"""R09：任务期限必须贯穿到底层 HTTP，但不得污染共享客户端上的非任务请求。

零外网：所有出网都走注入的假 opener；临时库 + /tmp。

运行：
    PYTHONDONTWRITEBYTECODE=1 TMPDIR=/tmp PYTHONPATH=.:tests \
        ../article-importer/.venv/bin/python -m unittest tests.test_intel_deadline_http
"""
import tempfile
import threading
import time
import unittest

from intel_store import Intel
from intel_wechat_mp import BACKEND_TIMEOUT, BODY_TIMEOUT, BackendQuota, WechatMpClient
from server import Store

BIZ = 'MzB5NTkwMDAwMDE='
NAME = '卡兹克'
LINK_A = f'https://mp.weixin.qq.com/s?__biz={BIZ}&mid=2650000001&idx=1&sn=aaa&chksm=x'
LINK_B = f'https://mp.weixin.qq.com/s?__biz={BIZ}&mid=2650000002&idx=1&sn=bbb&chksm=x'
EMPTY_JSON = b'{"base_resp": {"ret": 0}, "app_msg_list": []}'


class _MemorySettings:
    def __init__(self):
        self.data = {}

    def get(self, key, default=None):
        return self.data.get(key, default)

    def set(self, key, value):
        self.data[key] = str(value)


class NoWaitThrottle:
    """正文节流器在测试里不真等：只关心每次请求实际带了多大超时。"""

    def wait(self):
        pass


class FakeCredentials:
    def load(self):
        return ('cookie-stub', '9999999999')


class RecordingOpener:
    """记录每次 HTTP 实际拿到的 timeout；可只阻塞其中一条线程，制造「读取进行中」窗口。"""

    def __init__(self, response=EMPTY_JSON):
        self.response = response
        self.by_url = {}
        self.entered = threading.Event()
        self.release = threading.Event()
        self.block_thread = None
        self.opener_is_original = {}
        self.client = None          # 由 setUp 注入，用来检查共享 opener 有没有被换掉
        self.original = None

    def __call__(self, url, headers, timeout):
        self.by_url[url] = timeout
        if self.client is not None:
            self.opener_is_original[url] = (self.client.opener is self.original)
        if self.block_thread is not None and threading.current_thread() is self.block_thread:
            self.entered.set()
            self.release.wait(5)
        return self.response


class DeadlineReaderTests(unittest.TestCase):
    """发现读取的期限只影响本次调用，绝不替换共享 client.opener。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(self.tmp.name)
        self.opener = RecordingOpener()
        self.client = WechatMpClient(FakeCredentials(), BackendQuota(_MemorySettings()),
                                     throttle=NoWaitThrottle(), opener=self.opener)
        self.opener.client = self.client
        self.opener.original = self.client.opener
        self.intel = Intel(self.store, client_factory=lambda s: self.client)

    def tearDown(self):
        for worker in list(self.intel.workers.values()):
            worker.join(5)
        self.tmp.cleanup()

    def _wrapped(self, deadline):
        return self.intel._deadline_reader(lambda link: self.client.fetch_article(link),
                                           self.client, None, deadline)

    def _read_in_thread(self, work):
        """在另一条线程里跑一次读取，并在它进入 HTTP 后返回（此时窗口仍打开）。"""
        box = {}

        def run():
            self.opener.block_thread = threading.current_thread()
            try:
                work()
            except Exception as exc:  # noqa: BLE001
                box['error'] = exc

        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        self.assertTrue(self.opener.entered.wait(5), '读取没进到 HTTP')
        return thread, box

    def test_non_task_read_does_not_inherit_the_task_deadline(self):
        # 任务读取（另一条线程）带着 30 秒期限正在 HTTP 里；同一共享客户端上的非任务读取
        # （identify_from_link / intel_recommend / collect_adapters 走的就是这条路）
        # 必须照旧用完整预算 60，不能被任务期限压小。
        wrapped = self._wrapped(time.monotonic() + 30)
        thread, _box = self._read_in_thread(lambda: wrapped(LINK_A))
        try:
            self.client.fetch_article(LINK_B)
        except Exception:  # noqa: BLE001
            pass
        self.opener.release.set()
        thread.join(5)
        self.assertIn(LINK_B, self.opener.by_url, '非任务读取没发出去')
        self.assertEqual(self.opener.by_url[LINK_B], BODY_TIMEOUT,
                         f'非任务读继承了任务限时：{self.opener.by_url[LINK_B]}')

    def test_shared_opener_is_never_replaced_during_a_deadline_read(self):
        wrapped = self._wrapped(time.monotonic() + 30)
        thread, _box = self._read_in_thread(lambda: wrapped(LINK_A))
        self.assertEqual(self.opener.opener_is_original.get(LINK_A), True,
                         '任务读取期间绝不能替换共享 client.opener')
        self.opener.release.set()
        thread.join(5)
        self.assertIs(self.client.opener, self.opener.original, '调用结束后共享客户端必须还是原样')

    def test_task_read_still_gets_a_capped_timeout(self):
        # 期限仍要贯穿到底层：单次 HTTP 不得超过剩余时间（既有行为不回退）。
        wrapped = self._wrapped(time.monotonic() + 1.0)
        try:
            wrapped(LINK_A)
        except Exception:  # noqa: BLE001
            pass
        self.assertLess(self.opener.by_url[LINK_A], BODY_TIMEOUT, '剩余期限必须压小单次 HTTP')
        self.assertLessEqual(self.opener.by_url[LINK_A], 1.0 + 0.05)

    def test_deadline_does_not_leak_after_the_call(self):
        wrapped = self._wrapped(time.monotonic() + 1.0)
        try:
            wrapped(LINK_A)
        except Exception:  # noqa: BLE001
            pass
        try:
            self.client.fetch_article(LINK_B)
        except Exception:  # noqa: BLE001
            pass
        self.assertEqual(self.opener.by_url[LINK_B], BODY_TIMEOUT, '调用结束后不得残留期限')


class BackendHttpTests(unittest.TestCase):
    """后台单次 HTTP：min(40, 剩余期限)；没有期限时照旧 40，不突破既有请求预算。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(self.tmp.name)
        self.opener = RecordingOpener()
        self.client = WechatMpClient(FakeCredentials(), BackendQuota(_MemorySettings()),
                                     throttle=NoWaitThrottle(), opener=self.opener)
        self.intel = Intel(self.store, client_factory=lambda s: self.client)
        self.record = {'fakeid': BIZ, 'name': NAME}

    def tearDown(self):
        for worker in list(self.intel.workers.values()):
            worker.join(5)
        self.tmp.cleanup()

    def _backend_timeout(self, deadline):
        items, info = self.intel._scheduled_backend(self.record, 'e' * 32, {}, True, None,
                                                   cancel=None, deadline=deadline, known={})
        self.assertTrue(info['requested'], '后台这一路要真的发出请求')
        self.assertEqual(items, [])
        self.assertEqual(len(self.opener.by_url), 1, '只该发一次后台请求')
        return next(iter(self.opener.by_url.values()))

    def test_backend_timeout_is_capped_to_remaining_deadline(self):
        seen = self._backend_timeout(time.monotonic() + 1.0)
        self.assertLess(seen, BACKEND_TIMEOUT, '后台单次 HTTP 必须压到剩余期限')
        self.assertLessEqual(seen, 1.0 + 0.05)

    def test_backend_timeout_stays_at_budget_without_deadline(self):
        self.assertEqual(self._backend_timeout(None), BACKEND_TIMEOUT,
                         '没任务期限时照旧用既有预算，不突破')

    def test_backend_timeout_never_exceeds_budget_with_a_long_deadline(self):
        self.assertEqual(self._backend_timeout(time.monotonic() + 3600), BACKEND_TIMEOUT,
                         '剩余期限再长也不得突破 40 秒预算')


class RequestTimeoutHelperTests(unittest.TestCase):
    """新增的请求级超时助手（基座上没有这两个函数 → 本组用例在基座上报错）。"""

    def setUp(self):
        from intel_wechat_mp import request_deadline, request_timeout  # noqa: F401
        self.request_deadline = request_deadline
        self.request_timeout = request_timeout

    def test_helper_respects_budget_explicit_timeout_and_deadline(self):
        self.assertEqual(self.request_timeout(40), 40)
        self.assertEqual(self.request_timeout(40, 10), 10)
        self.assertEqual(self.request_timeout(40, 99), 40, '显式 timeout 不得突破既有预算')
        with self.request_deadline(time.monotonic() + 5):
            self.assertLess(self.request_timeout(40), 40)
            self.assertLess(self.request_timeout(40, 99), 40)
        self.assertEqual(self.request_timeout(40), 40, '离开期限上下文后不得残留')

    def test_deadline_scope_is_thread_local(self):
        box = {}

        def worker():
            box['other_thread'] = self.request_timeout(40)

        with self.request_deadline(time.monotonic() + 1):
            thread = threading.Thread(target=worker)
            thread.start()
            thread.join(5)
            box['in_scope'] = self.request_timeout(40)
        box['after_scope'] = self.request_timeout(40)
        self.assertEqual(box['other_thread'], 40, '另一条线程不该看到本线程的任务期限（期限只随调用上下文走）')
        self.assertLess(box['in_scope'], 40, '本线程在期限内必须压小单次 HTTP')
        self.assertEqual(box['after_scope'], 40, '离开上下文后不得残留期限')


# ── M2：期限在等待里耗尽，就不发这次请求 ────────────────────────────────────

class SlowWait:
    """把「等待」变成可观察的动作：调用即把时钟推过期限（不真等，只耗掉剩余时间）。"""

    STEP = 0.05

    def wait(self):
        time.sleep(self.STEP)          # 期限取 +0.01s，这里睡 0.05s → 必然越过


class SlowCredentials(FakeCredentials):
    """取凭据同样会把剩余期限耗掉（钥匙串/子进程那一步在真实环境里也会耗时）。"""

    def load(self):
        time.sleep(SlowWait.STEP)
        return super().load()


class ExpiredBeforeSendTests(unittest.TestCase):
    """期限在节流等待／取凭据中耗尽：一个请求都不能发出去，必须按超时如实收口（M2）。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _client(self, credentials, throttle):
        opener = RecordingOpener()
        client = WechatMpClient(credentials, BackendQuota(_MemorySettings()),
                                throttle=throttle, opener=opener)
        return client, opener

    def test_body_read_expires_in_throttle_and_never_opens_a_connection(self):
        from intel_wechat_mp import TaskDeadlinePassed, request_deadline
        client, opener = self._client(FakeCredentials(), SlowWait())
        with self.assertRaises(TaskDeadlinePassed):
            with request_deadline(time.monotonic() + 0.01):
                client.fetch_article(LINK_A)
        self.assertEqual(opener.by_url, {}, '期限已到还发正文请求：' + str(opener.by_url))

    def test_backend_call_expires_while_fetching_credentials_and_never_opens(self):
        from intel_wechat_mp import TaskDeadlinePassed, request_deadline
        client, opener = self._client(SlowCredentials(), NoWaitThrottle())
        with self.assertRaises(TaskDeadlinePassed):
            with request_deadline(time.monotonic() + 0.01):
                client.appmsg_list(BIZ, 0, 20)
        self.assertEqual(opener.by_url, {}, '期限已到还发后台请求：' + str(opener.by_url))

    def test_deadline_passed_is_not_misreported_as_an_expired_credential(self):
        from intel_wechat_mp import CredentialExpired, TaskDeadlinePassed, request_deadline
        client, _opener = self._client(SlowCredentials(), NoWaitThrottle())
        with request_deadline(time.monotonic() + 0.01):
            with self.assertRaises(TaskDeadlinePassed) as caught:
                client.appmsg_list(BIZ, 0, 20)
        self.assertNotIsInstance(caught.exception, CredentialExpired,
                                 '期限到点不能被当成「需重新登录后台」')

    def test_scheduled_backend_reports_a_timeout_stop_and_sends_nothing(self):
        opener = RecordingOpener()
        client = WechatMpClient(SlowCredentials(), BackendQuota(_MemorySettings()),
                                throttle=NoWaitThrottle(), opener=opener)
        intel = Intel(self.store, client_factory=lambda s: client)
        record = {'fakeid': BIZ, 'name': NAME}
        routes = {}
        # 走真实的 _scheduled_backend 路径：期限在取凭据时耗尽 → 不发请求，如实记超时。
        items, info = intel._scheduled_backend(record, 'e' * 32, routes, True, None,
                                               cancel=None, deadline=time.monotonic() + 0.01,
                                               known={})
        self.assertEqual(items, [])
        self.assertFalse(info['requested'], '没发出去的请求不能记成已请求')
        self.assertEqual((info['state'], info['stop']), ('blocked', 'deadline'))
        self.assertIn('时限', routes['backend'], f'回执要写明是时限而不是成功：{routes}')
        self.assertEqual(opener.by_url, {}, '期限已到还发后台请求：' + str(opener.by_url))

    def test_scheduled_round_finishes_as_timeout_when_the_backend_never_went_out(self):
        from tests.test_intel_article_url_edges import FakeProvider, FakePublic
        opener = RecordingOpener()
        client = WechatMpClient(SlowCredentials(), BackendQuota(_MemorySettings()),
                                throttle=NoWaitThrottle(), opener=opener)
        intel = Intel(self.store, client_factory=lambda s: client,
                      public_factory=lambda s: FakePublic(),
                      discovery_factory=lambda s: {'provider': FakeProvider(), 'reader': None})
        intel.subscribe({'fakeid': BIZ, 'name': NAME})
        receipt = intel.daily_latest(run_id='r' * 32, deadline=time.monotonic() + 0.01)
        job = intel.jobs(5)[0]
        routes = {line['path']: line['text'] for line in job['routes']}
        self.assertEqual(receipt['state'], 'timeout', f'本轮应如实记超时：{receipt}')
        self.assertIn('时限', routes['backend'], f"回执不得说后台正常完成：{routes}")
        self.assertEqual(job['status'], 'done', '超时不等于整轮意外错误')
        backend = job['outcome']['list']['routes']['backend']
        self.assertEqual(backend['state'], 'blocked')
        self.assertEqual(backend['stop_kind'], 'deadline')
        self.assertFalse(backend['requested'], '没发出去的请求不能记成已请求')
        self.assertEqual(opener.by_url, {}, '期限已到还发后台请求：' + str(opener.by_url))

    def test_deadline_reader_reports_the_discovery_stop_vocabulary(self):
        from intel_wechat_mp import request_deadline
        client, opener = self._client(FakeCredentials(), SlowWait())
        wrapped = Intel(self.store)._deadline_reader(lambda link: client.fetch_article(link),
                                                      client, None, time.monotonic() + 0.01)
        with self.assertRaises(Exception) as caught:
            with request_deadline(time.monotonic() + 0.01):
                wrapped(LINK_A)
        self.assertIn('时限', str(caught.exception))
        self.assertEqual(opener.by_url, {}, '期限已到还发正文请求：' + str(opener.by_url))


if __name__ == '__main__':
    unittest.main()
