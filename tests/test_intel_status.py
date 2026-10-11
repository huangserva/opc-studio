"""fixes-intel-status：失败原因常驻可见 + 定时也参加单飞 + 发现合集留痕。

依据 coordination/diag-intel-kazike-report.md 的 M1 / M2 / L1。
fixes-intel-status-2 M2：认领之后的整段（请求、落库、统计、收口）都在收口保护里；
收口本身失败时不吞错，靠残留规则兜底（见 ScheduledSaveFailureTests）。
全部走临时库与假客户端：**不访问微信、不访问搜狗、不读取也不打印任何凭证**。
假后台的 appmsg_list 调用次数就是单飞门槛的判据（被拒为 busy 时必须为 0）。
"""
import sqlite3
import threading
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone

from intel_public import SogouBlocked
from intel_store import STALE_JOB_SECONDS, Intel, Settings, humanize, label
from intel_wechat_mp import BackendQuota, RateLimited
from server import Store

BIZ = 'MzB5NTkwMDAwMDE='
OTHER = 'MzB5NTkwMDAwMDI='
NAME = '占位公众号'
ALBUM = '3501527933040967680'
ARTICLE_LINK = f'https://mp.weixin.qq.com/s?__biz={BIZ}&mid=1234567890&idx=1&chksm=deadbeef'


class MemorySettings:
    def __init__(self):
        self.data = {}

    def get(self, key, default=None):
        return self.data.get(key, default)

    def set(self, key, value):
        self.data[key] = str(value)


class FakePublic:
    """合集/搜狗两路的假实现。block_sogou=True 时按实测行为抛验证码。"""

    def __init__(self, block_sogou=False):
        self.block_sogou = block_sogou
        self.album_calls = self.sogou_calls = 0

    def album_page(self, identifier, album_id, begin_msgid=None, begin_itemidx=None):
        self.album_calls += 1
        return {'title': '占位合集', 'article_count': 0, 'nickname': NAME, 'items': [],
                'continue_flag': False}

    def sogou_search(self, *args, **kwargs):
        self.sogou_calls += 1
        if self.block_sogou:
            raise SogouBlocked('搜狗要求验证码，已停止本轮并进入冷却')
        return []

    def sogou_resolve(self, *args, **kwargs):
        return ''


class FakeBackend:
    """假后台客户端。calls 记录每一次 appmsg_list，用来证明「被拒时一个请求都没发」。"""

    def __init__(self, items=None, error=None, article_error=None):
        self.quota = BackendQuota(MemorySettings())
        self.calls = []
        self.items = items if items is not None else [
            {'title': '定时文章', 'link': 'https://mp.weixin.qq.com/s/BACKEND0001',
             'published_at': 1788319000, 'aid': 'b1'}]
        self.error = error
        self.article_error = article_error

    def credentials_ready(self):
        return ('stub', '9999999999')

    def search_biz(self, query):
        return []

    def appmsg_list(self, identifier, begin, count):
        self.calls.append((identifier, begin, count))
        if self.error is not None:
            raise self.error
        return list(self.items)

    def fetch_article(self, url):
        if self.article_error is not None:
            raise self.article_error
        return {'biz': BIZ, 'nickname': NAME, 'title': '占位文章', 'chars': 120,
                'published_at': 1788319000, 'url': url, 'raw': '<html>没有合集标记</html>',
                'text': '占位正文', 'images': []}


class StatusTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(self.tmp.name)
        self.public = FakePublic()
        self.backend = FakeBackend()
        self.intel = Intel(self.store, client_factory=lambda s: self.backend,
                           public_factory=lambda s: self.public)
        # 限流状态跟生产一样落在 intel.db 的 settings 表里，冷却才能跨实例看见。
        self.backend.quota = BackendQuota(Settings(self.intel))
        self.intel.subscribe({'fakeid': BIZ, 'name': NAME})

    def tearDown(self):
        for worker in list(self.intel.workers.values()):
            worker.join(5)
        self.tmp.cleanup()

    def fresh(self):
        """另开一个 Intel 实例读同一份库，等价于「用户刷新页面」。"""
        return Intel(self.store, client_factory=lambda s: FakeBackend(),
                     public_factory=lambda s: FakePublic())

    def go(self, **payload):
        payload.setdefault('fakeid', BIZ)
        payload.setdefault('mode', 'latest')
        return self.intel.wait_for(self.intel.run(payload)['id'], timeout=5)

    def last_result(self, intel=None):
        return (intel or self.intel).subscriptions()[0]['last_result']


class ManualResultTests(StatusTestCase):
    """M1：手动抓取的任何收口都要在刷新后看得见，三条路线分开列。"""

    def test_sogou_captcha_run_is_readable_after_a_refresh(self):
        self.public.block_sogou = True
        done = self.go(sogou=True)
        self.assertEqual(done['status'], 'partial')
        row = self.fresh().subscriptions()[0]
        result = row['last_result']
        self.assertEqual(result['status'], 'partial')
        self.assertEqual(result['status_label'], '部分完成')
        self.assertEqual([line['label'] for line in result['routes']], ['合集', '搜狗', '后台'])
        sogou = next(line for line in result['routes'] if line['path'] == 'sogou')
        self.assertIn('验证码', sogou['text'])
        self.assertIn('完整', result['detail'])
        self.assertTrue(result['at_label'])
        self.assertIn('搜狗冷却至', result['cooldown_label'])
        self.assertTrue(result['suggestion'])
        # 保留的 subscriptions.last_status 也必须跟着收口更新，不能只更新成功那一路。
        self.assertEqual(row['last_status'], 'partial')

    def test_backend_rate_limit_headline_is_plain_language_with_the_code_in_parentheses(self):
        self.backend.error = RateLimited('命中公众号后台限流（200013）', until=int(time.time()) + 3600)
        done = self.go(sogou=False, backend=True)
        self.assertIn('200013', done['detail'])          # 原文完整保留
        result = self.last_result()
        self.assertEqual(result['status'], 'partial')
        self.assertIn('错误码 200013', result['headline'])
        self.assertFalse(result['headline'].startswith('200013'))
        self.assertIn('补一条合集链接', result['suggestion'])

    def test_successful_manual_run_still_records_its_routes(self):
        self.intel.remember_albums(BIZ, [ALBUM])
        done = self.go(sogou=False, backend=False)
        self.assertEqual(done['status'], 'partial')      # 假合集没有文章，如实算部分完成
        result = self.last_result()
        self.assertEqual([line['path'] for line in result['routes']], ['album', 'sogou', 'backend'])
        self.assertEqual(next(l for l in result['routes'] if l['path'] == 'album')['text'],
                         '1 个合集，取到 0 篇')
        self.assertEqual(next(l for l in result['routes'] if l['path'] == 'backend')['text'],
                         '已关闭（可用页面开关打开）')

    def test_stale_running_job_is_reported_as_interrupted_on_the_subscription(self):
        stale = (datetime.now(timezone.utc) - timedelta(seconds=STALE_JOB_SECONDS + 60)).isoformat()
        with self.intel.db() as connection:
            connection.execute("INSERT INTO jobs(id,fakeid,name,mode,status,started_at) "
                               "VALUES('j1',?,?,'latest','running',?)", (BIZ, NAME, stale))
        self.assertIsNone(self.intel.active_job())
        row = self.intel.subscriptions()[0]
        self.assertEqual(row['last_status'], 'interrupted')
        self.assertEqual(row['last_result']['status'], 'interrupted')
        self.assertIn('中断', row['last_result']['headline'])

    def test_no_run_means_no_fake_result(self):
        result = self.last_result()
        self.assertIsNone(result)
        self.assertEqual(self.intel.subscriptions()[0]['last_status'], '')


class ScheduledResultTests(StatusTestCase):
    """M1：定时轮次的每个收口（含失败提前退出）都要覆盖订阅的最近结果。"""

    def test_scheduled_rate_limit_is_visible_after_a_refresh(self):
        self.backend.error = RateLimited('命中公众号后台限流（200013）', until=0)
        receipt = self.intel.daily_latest(run_id='r' * 32)
        self.assertEqual(receipt['state'], 'rate_limited')
        fresh = self.fresh()
        row = fresh.subscriptions()[0]
        result = row['last_result']
        self.assertEqual(result['status'], 'rate_limited')
        self.assertEqual(result['mode'], 'scheduled')
        self.assertEqual(result['mode_label'], '定时（每日抓最新）')
        self.assertEqual(result['source'], 'scheduled')
        self.assertIn('未重试', result['detail'])
        self.assertEqual(row['last_status'], 'rate_limited')
        # 只有一份状态：页面读的就是 jobs 表里那条定时任务，不是第二份副本。
        self.assertEqual([j['mode'] for j in fresh.jobs(20)], ['scheduled'])
        self.assertEqual([j['status'] for j in fresh.jobs(20)], ['rate_limited'])

    def test_scheduled_credential_loss_is_recorded(self):
        self.backend.error = ValueError('后台返回了登录页')
        receipt = self.intel.daily_latest(run_id='r' * 32)
        self.assertIn(receipt['state'], ('done', 'partial'))   # 单号失败不中断整轮
        result = self.last_result()
        self.assertEqual(result['status'], 'error')
        self.assertIn('登录页', result['detail'])
        self.assertEqual(self.intel.subscriptions()[0]['last_status'], 'error')

    def test_scheduled_success_records_all_three_routes(self):
        receipt = self.intel.daily_latest(run_id='r' * 32)
        self.assertEqual(receipt['state'], 'done')
        self.assertEqual(receipt['new_articles'], 1)
        result = self.last_result()
        self.assertEqual(result['status'], 'done')
        texts = {line['path']: line['text'] for line in result['routes']}
        self.assertIn('不走合集', texts['album'])
        self.assertIn('不走搜狗', texts['sogou'])
        self.assertEqual(texts['backend'], '取到 1 篇')
        self.assertEqual(self.intel.subscriptions()[0]['last_status'], 'done')
        # 文章挂到这次的定时任务行上，便于回看是哪一轮抓的。
        job_id = self.intel.jobs(5)[0]['id']
        self.assertEqual(self.intel.articles(BIZ)['articles'][0]['job_id'], job_id)

    def test_whole_round_cooldown_skip_is_recorded_and_sends_nothing(self):
        self.backend.quota.settings.set(BackendQuota.K_COOLDOWN, repr(int(time.time()) + 7200))
        receipt = self.intel.daily_latest(run_id='r' * 32)
        self.assertEqual(receipt['state'], 'cooldown')
        self.assertEqual(self.backend.calls, [])
        result = self.last_result()
        self.assertEqual(result['status'], 'cooldown')
        self.assertEqual(result['status_label'], '冷却中，本轮跳过')
        self.assertIn('整轮跳过', result['detail'])
        self.assertIn('未发送任何请求', result['detail'])
        self.assertIn('不用再点抓取', result['suggestion'])
        self.assertIn('后台接口冷却至', result['cooldown_label'])
        self.assertEqual(self.intel.subscriptions()[0]['last_status'], 'cooldown')

    def test_cancellation_still_records_nothing_and_sends_nothing(self):
        cancel = threading.Event()
        cancel.set()
        receipt = self.intel.daily_latest(run_id='r' * 32, cancel=cancel)
        self.assertEqual(receipt['state'], 'cancelled')
        self.assertEqual(self.backend.calls, [])
        self.assertIsNone(self.last_result())

    def test_unexpected_backend_error_still_closes_the_job_row(self):
        """定时现在占着单飞闸门：意外异常不能把任务行留在 running。"""
        self.backend.error = RuntimeError('假客户端炸了')
        receipt = self.intel.daily_latest(run_id='r' * 32)
        self.assertEqual(receipt['state'], 'error')
        self.assertIsNone(self.intel.active_job())
        self.assertFalse(self.intel.status()['busy'])
        result = self.last_result()
        self.assertEqual(result['status'], 'error')
        self.assertIn('未预期的错误', result['detail'])
        self.assertEqual(self.intel.subscriptions()[0]['last_status'], 'error')


class ScheduledSaveFailureTests(StatusTestCase):
    """M2：认领之后的整段（请求、落库、统计、收口）都在收口保护里。

    只包住 appmsg_list 不够：请求成功但落库/统计/收口报错时，任务行会停在 running，
    单飞闸门把后面每一轮都挡成 busy，直到 3600 秒超时。
    """

    def test_article_write_failure_still_closes_the_row_and_frees_the_lane(self):
        def boom(*args, **kwargs):
            raise RuntimeError('落库炸了')

        self.intel._upsert_article = boom
        receipt = self.intel.daily_latest(run_id='r' * 32)
        self.assertEqual(receipt['state'], 'error')
        self.assertIn('未预期的错误：RuntimeError', receipt['detail'])
        # 任务行不是 running，闸门也没被占着。
        self.assertEqual([job['status'] for job in self.intel.jobs(20)], ['error'])
        self.assertIsNone(self.intel.active_job())
        self.assertFalse(self.intel.status()['busy'])
        result = self.last_result()
        self.assertEqual(result['status'], 'error')
        self.assertEqual(result['routes'][-1]['path'], 'backend')
        self.assertEqual(result['routes'][-1]['text'], '未预期的错误：RuntimeError')
        self.assertEqual(self.intel.subscriptions()[0]['last_status'], 'error')
        # 下一轮定时能重新认领闸门，不会停在 busy。
        del self.intel._upsert_article
        again = self.intel.daily_latest(run_id='r2' * 32)
        self.assertNotEqual(again['state'], 'busy')
        self.assertEqual(again['state'], 'done')
        self.assertEqual(again['new_articles'], 1)

    def test_save_failure_stops_the_round_instead_of_trying_the_next_account(self):
        self.intel.subscribe({'fakeid': OTHER, 'name': '另一个号'})

        def boom(*args, **kwargs):
            raise RuntimeError('落库炸了')

        self.intel._upsert_article = boom
        receipt = self.intel.daily_latest(run_id='r' * 32)
        self.assertEqual(receipt['state'], 'error')
        # 意外异常就停本轮：不重试、也不拿下一个号继续碰运气。
        self.assertEqual(len(self.backend.calls), 1)
        self.assertEqual(receipt['accounts'], 1)

    def test_finish_writeback_failure_is_logged_and_the_stale_rule_recovers_it(self):
        """收口自己失败（库不可写）：不吞错，回执写明恢复路径，残留规则兜底。"""
        def boom(*args, **kwargs):
            raise sqlite3.OperationalError('数据库不可写')

        self.intel._finish_job = boom
        with self.assertLogs('intel_store', level='ERROR') as logs:
            receipt = self.intel.daily_latest(run_id='r' * 32)
        self.assertEqual(receipt['state'], 'error')
        self.assertIn('收口也失败', receipt['detail'])
        self.assertIn(str(STALE_JOB_SECONDS), receipt['detail'])
        self.assertTrue(any('收口失败' in line for line in logs.output), '收口失败必须记日志')
        # 不能假装已收口：那一行还占着闸门，但到了残留门槛就会被标为已中断。
        self.assertEqual([job['status'] for job in self.intel.jobs(20)], ['running'])
        self.assertIsNotNone(self.intel.active_job())
        stale = (datetime.now(timezone.utc) - timedelta(seconds=STALE_JOB_SECONDS + 60)).isoformat()
        with self.intel.db() as connection:
            connection.execute("UPDATE jobs SET started_at=? WHERE status='running'", (stale,))
        self.assertIsNone(self.intel.active_job())
        row = self.intel.subscriptions()[0]
        self.assertEqual(row['last_status'], 'interrupted')
        self.assertEqual(row['last_result']['status'], 'interrupted')

    def test_manual_run_save_stage_failure_still_closes_the_row(self):
        """手动 _execute 同样核对：落库阶段的意外异常不会把任务行留在 running。"""
        def boom(*args, **kwargs):
            raise RuntimeError('落库炸了')

        self.intel._upsert_article = boom
        done = self.go(sogou=False, backend=True)
        self.assertNotIn(done['status'], ('running', 'queued'))
        self.assertIn(done['status'], ('done', 'partial'))
        self.assertIn('未预期的错误：RuntimeError', done['detail'])
        self.assertIsNone(self.intel.active_job())
        self.assertFalse(self.intel.status()['busy'])
        self.assertEqual(self.last_result()['status'], done['status'])


class SingleFlightTests(StatusTestCase):
    """M2：定时入口走和手动同一个单飞闸门；遇忙 skipped/busy，不排队、不重试。"""

    def test_scheduled_round_is_refused_while_a_manual_run_holds_the_lane(self):
        entered, release = threading.Event(), threading.Event()

        def blocking_page(*args, **kwargs):
            self.public.album_calls += 1
            entered.set()
            release.wait(5)
            return {'title': '占位合集', 'article_count': 0, 'nickname': NAME, 'items': [],
                    'continue_flag': False}

        self.public.album_page = blocking_page
        self.intel.remember_albums(BIZ, [ALBUM])
        started = self.intel.run({'fakeid': BIZ, 'mode': 'latest', 'sogou': False})
        self.assertTrue(entered.wait(5), '手动任务没有进入合集请求')
        try:
            receipt = self.intel.daily_latest(run_id='r' * 32)
            self.assertEqual(receipt['state'], 'busy')
            # 门槛判据：被拒的这次定时一个后台请求都没发。
            self.assertEqual(self.backend.calls, [])
            self.assertIn('已有抓取任务在进行中', receipt['detail'])
            self.assertIn('没有排队也没有重试', receipt['detail'])
            self.assertEqual(receipt['accounts'], 0)
            self.assertEqual(receipt['active_job']['id'], started['id'])
        finally:
            release.set()
            for worker in list(self.intel.workers.values()):
                worker.join(5)
        # 不排队：没有多出第二条任务，也没有定时留下的行。
        self.assertEqual([job['mode'] for job in self.intel.jobs(20)], ['latest'])

    def test_manual_run_is_refused_while_a_scheduled_round_holds_the_lane(self):
        entered, release = threading.Event(), threading.Event()

        def blocking_list(*args, **kwargs):
            self.backend.calls.append(args)
            entered.set()
            release.wait(5)
            return []

        self.backend.appmsg_list = blocking_list
        worker = threading.Thread(target=self.intel.daily_latest, kwargs={'run_id': 'r' * 32}, daemon=True)
        worker.start()
        self.assertTrue(entered.wait(5), '定时任务没有进入后台请求')
        try:
            answer = self.intel.run({'fakeid': BIZ, 'mode': 'latest', 'sogou': False})
            self.assertTrue(answer.get('reused'))
            self.assertEqual(answer['mode'], 'scheduled')
            self.assertTrue(self.intel.status()['busy'])
        finally:
            release.set()
            worker.join(5)

    def test_scheduled_round_still_runs_when_nothing_is_active(self):
        self.intel.daily_latest(run_id='r' * 32)
        self.assertEqual(len(self.backend.calls), 1)


class AlbumSearchTests(StatusTestCase):
    """L1：发现合集的结果要持久留痕，且区分「这篇文章没合集」和「该号没合集」。"""

    def test_zero_albums_found_survives_a_refresh(self):
        found = self.intel.identify_from_link(ARTICLE_LINK)
        self.assertEqual(found['albums_found'], 0)
        self.assertIn('不等于这个号没有合集', found['album_search']['note'])
        trace = self.fresh().album_search(BIZ)
        self.assertTrue(trace['attempted'])
        self.assertEqual(trace['albums_found'], 0)
        self.assertEqual(trace['albums_known'], 0)
        self.assertEqual(trace['kind'], 'article')
        self.assertTrue(trace['at_label'])
        self.assertTrue(trace['has_chksm'])
        self.assertIn('那篇文章里没有合集', trace['note'])

    def test_the_link_itself_is_never_stored(self):
        self.intel.identify_from_link(ARTICLE_LINK)
        with self.intel.db() as connection:
            rows = [dict(row) for row in connection.execute('SELECT * FROM album_searches').fetchall()]
        self.assertEqual(len(rows), 1)
        stored = ''.join(str(value) for row in rows for value in row.values())
        self.assertNotIn('chksm=deadbeef', stored)
        self.assertNotIn('deadbeef', stored)
        self.assertEqual(rows[0]['fakeid'], BIZ)

    def test_note_changes_when_the_account_already_has_albums(self):
        self.intel.identify_from_link(ARTICLE_LINK)
        self.intel.remember_albums(BIZ, [ALBUM])
        note = self.intel.album_search(BIZ)['note']
        self.assertIn('那篇文章里没有合集', note)
        self.assertIn('这个号已记录 1 个合集', note)
        self.assertNotIn('不等于这个号没有合集', note)

    def test_failed_article_read_is_recorded_and_still_raises(self):
        self.backend.article_error = OSError('文章页打不开')
        with self.assertRaises(OSError):
            self.intel.identify_from_link(ARTICLE_LINK)
        trace = self.intel.album_search(BIZ)
        self.assertTrue(trace['attempted'])
        self.assertIn('文章页读取失败', trace['note'])
        self.assertIn('文章页打不开', trace['note'])

    def test_no_attempt_says_so_instead_of_blaming_the_account(self):
        trace = self.intel.album_search(BIZ)
        self.assertFalse(trace['attempted'])
        self.assertIn('还没有用文章链接反查过合集', trace['note'])


class CopyTests(unittest.TestCase):
    """状态文案要给人看：错误码只放在括号里。"""

    def test_rate_limit_code_is_wrapped_not_leaded(self):
        text = humanize('命中公众号后台限流（200013）')
        self.assertIn('错误码 200013', text)
        self.assertFalse(text.startswith('200013'))

    def test_unknown_text_is_passed_through_verbatim(self):
        self.assertEqual(humanize('搜狗搜索夹具失败'), '搜狗搜索夹具失败')
        self.assertEqual(humanize(''), '')

    def test_label_reads_stored_timestamps(self):
        self.assertRegex(label(datetime.now(timezone.utc).isoformat()), r'^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$')
        self.assertEqual(label(''), '')
        self.assertEqual(label(None), '')


if __name__ == '__main__':
    unittest.main()
