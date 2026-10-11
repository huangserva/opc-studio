"""round4 · B：情报采集限流纪律被绕过的回归测试。

两个被验收抓出来的真 bug：
1. status() 在未冷却时调用 clear_cooldown() 把 rate_strikes 清零 → 页面每刷新
   一次就抹掉一次计数，「连续 3 次触发 6 小时冷却」永远不会触发。
2. 同一号可以并发多个抓取任务（连点产生 3 个），后台接口是全局串行的，
   多余任务只会在限流器里干等并更容易触发 200013。

注意：Intel.run() 返回的是**受理瞬间**的任务快照，完成状态一律重新读
Intel.job() 再断言 —— 直接断言 run() 的返回值会因线程快慢而 flaky。

全部 mock，不发真实后台请求。
"""
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from intel_store import LIVE_STALE_REASON, STARTUP_STALE_REASON, Intel, Settings
from intel_wechat_mp import (COOLDOWN_SECONDS, BackendQuota, BodyThrottle, RateLimited)
from knowledge_store import Knowledge
from server import Store

BIZ = 'MzB5NTkwMDAwMDE=='
OTHER = 'MzB5NTkwMDAwMDI=='


class BlockingPublic:
    """在三期**默认主路（合集）**上阻塞，用来稳定复现并发。

    刻意不用后台那一路：后台在三期默认关闭，用它测并发会名不副实。
    """

    def __init__(self, hold):
        self.entered = threading.Event()
        self.release = threading.Event()
        self.hold = hold
        self.calls = 0

    def album_page(self, identifier, album_id, begin_msgid=None, begin_itemidx=None):
        self.calls += 1
        self.entered.set()
        self.release.wait(self.hold)
        return {'title': '占位合集', 'article_count': 0, 'nickname': '', 'items': [], 'continue_flag': False}

    def sogou_search(self, *a, **k):
        return []

    def sogou_resolve(self, *a, **k):
        return ''


class NullPublic:
    """三期公开接口的空实现：被调用就说明测试出错了。"""

    def album_page(self, *a, **k):
        raise AssertionError('测试不该访问合集接口')

    def sogou_search(self, *a, **k):
        raise AssertionError('测试不该访问搜狗')

    def sogou_resolve(self, *a, **k):
        raise AssertionError('测试不该访问搜狗')


class _SlowClient:
    """可控的假客户端：请求会阻塞，直到测试放行，用来稳定复现并发。"""

    def __init__(self, intel, hold=None):
        self.quota = BackendQuota(Settings(intel))
        self.credentials = ('stub', '9999999999')
        self.hold = hold
        self.entered = threading.Event()
        self.release = threading.Event()
        self.list_calls = 0

    def credentials_ready(self):
        return self.credentials

    def search_biz(self, query):
        return []

    def appmsg_list(self, identifier, begin, count):
        self.list_calls += 1
        self.entered.set()
        if self.hold is not None:
            self.release.wait(self.hold)
        return []

    def fetch_article(self, url):
        raise AssertionError('这些测试只关心限流与并发，不该抓正文')


class Round4TestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(self.tmp.name)
        self.store._knowledge = Knowledge(self.store, sources=[])
        self.public = BlockingPublic(hold=5)
        self.intel = Intel(self.store, public_factory=lambda s: self.public)
        self.client = _SlowClient(self.intel, hold=5)
        self.intel._client = self.client
        # 有合集才能走主路；专辑号是公开的占位数字。
        self.intel.remember_albums('MzB5NTkwMDAwMDE==', ['1234567890123456'])
        self.intel.remember_albums('MzB5NTkwMDAwMDI==', ['1234567890123457'])

    def entered(self):
        """等任务真正进到取列表那一步。"""
        if not (self.public.entered.wait(3) or self.client.entered.wait(0.2)):
            self.fail('任务没有进入取列表阶段')

    def release(self):
        self.public.release.set()
        self.client.release.set()

    def tearDown(self):
        self.release()
        for worker in list(self.intel.workers.values()):
            worker.join(5)
        self.tmp.cleanup()


class StrikeSurvivesStatusTests(Round4TestCase):
    """bug 1：只读 status 不得改动 strike 计数。"""

    def test_status_does_not_clear_strikes(self):
        for expected in (1, 2):
            self.client.quota.record(200013)
            self.assertEqual(self.client.quota.strike_count(), expected)
            record = self.intel.status()
            self.assertEqual(record['rate_strikes'], expected, 'status() 把 strike 抹掉了')
            self.assertEqual(self.client.quota.strike_count(), expected)

    def test_repeated_status_polling_never_weakens_the_discipline(self):
        """页面会反复刷 status —— 刷多少次都不该影响 3 次冷却。"""
        self.client.quota.record(200013)
        for _ in range(50):
            self.intel.status()
        self.assertEqual(self.client.quota.strike_count(), 1)
        self.client.quota.record(200013)
        for _ in range(50):
            self.intel.status()
        self.assertEqual(self.client.quota.strike_count(), 2)
        self.client.quota.record(200013)
        self.assertTrue(self.client.quota.state()['cooling'])

    def test_three_strikes_open_cooldown_through_status(self):
        for _ in range(3):
            self.client.quota.record(200013)
        record = self.intel.status()
        self.assertTrue(record['cooldown_until'] > 0)
        self.assertTrue(record['cooldown_label'])
        self.assertIn('冷却', record['detail'])

    def test_cooldown_deadline_is_about_six_hours(self):
        for _ in range(3):
            self.client.quota.record(200013)
        remaining = self.client.quota.cooldown_until() - time.time()
        self.assertGreater(remaining, COOLDOWN_SECONDS - 60)
        self.assertLessEqual(remaining, COOLDOWN_SECONDS)

    def test_strike_only_clears_on_a_successful_response(self):
        self.client.quota.record(200013)
        self.client.quota.record(200013)
        self.assertEqual(self.client.quota.strike_count(), 2)
        self.client.quota.record(0)          # 一次成功响应
        self.assertEqual(self.client.quota.strike_count(), 0)

    def test_expired_cooldown_still_reads_as_not_cooling(self):
        """只读路径不再顺手清冷却值，但过期的冷却必须自然失效。"""
        self.intel.subscribe({'fakeid': BIZ, 'name': '甲'})
        self.client.quota.settings.set(BackendQuota.K_COOLDOWN, repr(time.time() - 10))
        self.client.quota.settings.set(BackendQuota.K_STRIKE, '2')
        record = self.intel.status()
        self.assertEqual(record['cooldown_until'], 0)
        self.assertFalse(record['cooldown_label'])
        self.assertEqual(record['rate_strikes'], 2, '过期的冷却不该顺手抹掉 strike')
        # 但过期的冷却确实不再拦人
        job = self.intel.run({'fakeid': BIZ, 'mode': 'latest'})
        self.assertFalse(job.get('reused'))
        self.release()
        self.intel.wait_for(job['id'], 5)

    def test_clear_cooldown_never_touches_strikes(self):
        self.client.quota.record(200013)
        self.client.quota.record(200013)
        self.client.quota.clear_cooldown()     # 不在冷却中：什么都不该发生
        self.assertEqual(self.client.quota.strike_count(), 2)


class ConcurrentRunTests(Round4TestCase):
    """bug 2：同一号（或全局）已有任务时不新建。"""

    def subscribe(self, identifier=BIZ, name='甲'):
        self.intel.subscribe({'fakeid': identifier, 'name': name})
        return identifier

    def test_repeated_clicks_create_exactly_one_job(self):
        self.subscribe()
        first = self.intel.run({'fakeid': BIZ, 'mode': 'latest'})
        self.entered()
        rest = [self.intel.run({'fakeid': BIZ, 'mode': 'latest'}) for _ in range(2)]
        self.assertEqual({j['id'] for j in rest}, {first['id']}, '连点产生了多个任务')
        self.assertEqual(self.public.calls, 1, '主路（合集）只应被请求一次')
        for job in rest:
            self.assertTrue(job['reused'])
            self.assertEqual(job['active'], True)

    def test_second_account_is_also_refused_while_backend_is_busy(self):
        """公开接口只有一个全局闸门，换个号也不该并行。"""
        self.subscribe(BIZ, '甲'); self.subscribe(OTHER, '乙')
        first = self.intel.run({'fakeid': BIZ, 'mode': 'latest'})
        self.entered()
        other = self.intel.run({'fakeid': OTHER, 'mode': 'latest'})
        self.assertEqual(other['id'], first['id'])
        self.assertTrue(other['reused'])
        self.assertEqual(self.public.calls, 1, '主路只被请求了一次')

    def test_true_concurrent_clicks_still_produce_one_job(self):
        """并发 POST 的竞态：检查与建任务必须在同一个写事务里。"""
        self.subscribe()
        results, barrier = [], threading.Barrier(6)

        def click():
            barrier.wait(5)
            try:
                results.append(self.intel.run({'fakeid': BIZ, 'mode': 'latest'}))
            except ValueError as exc:
                results.append({'error': str(exc)})

        threads = [threading.Thread(target=click) for _ in range(5)]
        for t in threads:
            t.start()
        barrier.wait(5)
        for t in threads:
            t.join(10)
        ids = {r['id'] for r in results if 'id' in r}
        self.assertEqual(len(ids), 1, f'并发点击产生了 {len(ids)} 个任务')
        self.assertEqual(len([r for r in results if r.get('reused')]), 4)

    def test_status_reports_busy_so_the_page_can_disable_buttons(self):
        self.subscribe()
        self.assertFalse(self.intel.status()['busy'])
        self.intel.run({'fakeid': BIZ, 'mode': 'latest'})
        self.entered()
        record = self.intel.status()
        self.assertTrue(record['busy'])
        self.assertTrue(record['active_job']['id'])
        self.assertIn(record['active_job']['status'], ('running', 'queued'))
        self.release()
        self.intel.wait_for(record['active_job']['id'], 5)
        self.assertFalse(self.intel.status()['busy'])

    def test_a_new_job_is_allowed_once_the_previous_one_finishes(self):
        self.subscribe()
        first = self.intel.run({'fakeid': BIZ, 'mode': 'latest'})
        self.entered()
        self.release()
        self.intel.wait_for(first['id'], 5)
        second = self.intel.run({'fakeid': BIZ, 'mode': 'latest'})
        self.assertNotEqual(second['id'], first['id'])
        self.assertFalse(second.get('reused'))

    def test_a_rate_limited_job_frees_the_slot(self):
        self.subscribe()

        def blow(identifier, begin, count):
            raise RateLimited('命中限流（200013）', until=int(time.time()) + 60)
        self.client.appmsg_list = blow
        job = self.intel.run({'fakeid': BIZ, 'mode': 'latest', 'sogou': False, 'backend': True})
        self.release()   # 让主路立刻放行，任务尽快走到后台那一路
        self.intel.wait_for(job['id'], 5)
        # run() 返回的是受理时的快照，完成状态要重新读，别拿快照当结果断言。
        done = self.intel.job(job['id'])
        self.assertEqual(done['status'], 'partial')   # 没取到东西就不谎称完成
        self.assertIn('200013', done['detail'])
        self.assertIn('未重试', done['detail'])
        self.assertFalse(self.intel.status()['busy'])

    def test_stale_running_job_left_by_a_restart_does_not_block_forever(self):
        """超时的 running 不能永久占位。进程还活着时，status() 也会清扫。"""
        self.subscribe()
        stale_id = 'stale0000000000000000000000000000'
        with self.intel.db() as connection:
            connection.execute("INSERT INTO jobs(id,fakeid,name,mode,status,started_at) "
                               "VALUES(?,?,'甲','latest','running',?)",
                               (stale_id, BIZ, '2026-10-09T00:00:00Z'))
        self.assertFalse(self.intel.status()['busy'])
        self.assertEqual(self.intel.job(stale_id)['status'], 'interrupted')
        self.assertEqual(self.intel.job(stale_id)['reason'], LIVE_STALE_REASON)
        fresh = self.intel.run({'fakeid': BIZ, 'mode': 'latest'})
        self.assertFalse(fresh.get('reused'))
        self.release()
        self.intel.wait_for(fresh['id'], 5)

    def test_startup_sweep_marks_a_leftover_job_as_restarted(self):
        """第一次打开库仍用重启原因；活进程清扫不会改写这句话。"""
        self.subscribe()
        stale_id = 'c' * 32
        with self.intel.db() as connection:
            connection.execute("INSERT INTO jobs(id,fakeid,name,mode,status,started_at) "
                               "VALUES(?,?,'甲','latest','running',?)",
                               (stale_id, BIZ, '2026-10-09T00:00:00Z'))
        reopened = Intel(self.store, public_factory=lambda s: NullPublic())
        reopened._client = self.client
        rows = {job['id']: job for job in reopened.jobs(50)}
        self.assertEqual(rows[stale_id]['status'], 'interrupted')
        self.assertEqual(rows[stale_id]['reason'], STARTUP_STALE_REASON)
        self.assertFalse(reopened.status()['busy'])

    def test_a_second_instance_on_the_same_workspace_does_not_kill_live_jobs(self):
        """曾用「首次建表就中断所有 running」，结果同一 workspace 上第二个
        Intel 实例会把第一个实例正在跑的任务误杀。改成只按时间判定。"""
        self.subscribe()
        self.intel.run({'fakeid': BIZ, 'mode': 'latest'})
        self.entered()
        twin = Intel(self.store, public_factory=lambda s: NullPublic())          # 同 workspace 的第二个实例
        twin._client = self.client
        self.assertTrue(twin.status()['busy'], '第二个实例误杀了第一个实例的任务')
        running = [j for j in self.intel.jobs() if j['active']]
        self.assertEqual(len(running), 1)
        self.assertEqual(running[0]['status'], 'running')

    def test_a_recent_running_job_is_not_marked_interrupted(self):
        self.subscribe()
        self.intel.run({'fakeid': BIZ, 'mode': 'latest'})
        self.entered()
        Intel(self.store, public_factory=lambda s: NullPublic()).jobs()           # 触发一次建表检查
        self.assertEqual(self.intel.jobs()[0]['status'], 'running')


class StatusReadOnlyTests(Round4TestCase):
    """status() 不得产生任何副作用。"""

    def test_status_sends_no_backend_request(self):
        with patch.object(self.client, 'appmsg_list', side_effect=AssertionError('status 不该请求后台')):
            for _ in range(5):
                self.intel.status()
        self.assertEqual(self.client.list_calls, 0)

    def test_status_does_not_move_the_last_request_clock(self):
        self.client.quota.record(0)
        before = self.client.quota._float(BackendQuota.K_LAST)
        for _ in range(10):
            self.intel.status()
        self.assertEqual(self.client.quota._float(BackendQuota.K_LAST), before)


if __name__ == '__main__':
    unittest.main()