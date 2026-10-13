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


class _UnavailableDiscoveryProvider:
    """测试用发现来源：明确不可用，绝不发任何请求。"""

    name = 'unavailable'

    def capabilities(self):
        return {'provider': self.name, 'installed': False, 'available': False}

    def search(self, query, count, freshness, cancel=None, deadline=None):
        from intel_link_discovery import ProviderUnavailable
        raise ProviderUnavailable('发现来源不可用（测试）', provider=self.name)


def _no_reader(link):
    raise ValueError('测试不读正文')


def fake_discovery(provider=None, reader=None):
    """discovery_factory：默认注入「不可用」来源，保证零真实请求。"""
    return lambda store: {'provider': provider or _UnavailableDiscoveryProvider(),
                          'reader': reader or _no_reader}


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
                           public_factory=lambda s: self.public,
                           discovery_factory=fake_discovery())
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
                     public_factory=lambda s: FakePublic(),
                     discovery_factory=fake_discovery())

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
        # 合集确实查询成功但取到 0 篇：这是「确实成功查询空列表」，终态 done（prep §3 行3）。
        # 回执必须说明「查询成功，本次没有新文章」，不能和有新文章的完成混为一谈。
        self.assertEqual(done['status'], 'done')
        self.assertEqual(done['outcome']['list']['state'], 'complete')
        self.assertIn('查询成功，本次没有新文章', done['outcome']['summary'])
        result = self.last_result()
        self.assertEqual([line['path'] for line in result['routes']], ['album', 'sogou', 'backend'])
        self.assertEqual(next(l for l in result['routes'] if l['path'] == 'album')['text'],
                         '1 个合集，取到 0 篇')
        self.assertEqual(next(l for l in result['routes'] if l['path'] == 'backend')['text'],
                         '已关闭（可用页面开关打开）')

    def test_empty_success_differs_from_unavailable_list(self):
        """空列表成功（done）与列表不可用（partial）必须能分开，不混为一谈。"""
        # 有合集、查询成功、0 篇 → done，且回执写明「查询成功，本次没有新文章」。
        self.intel.remember_albums(BIZ, [ALBUM])
        empty = self.go(sogou=False, backend=False)
        self.assertEqual(empty['status'], 'done')
        self.assertIn('查询成功，本次没有新文章', empty['outcome']['summary'])
        # 无合集、搜狗也关掉 → 列表不可用，partial，回执写明原因，不说「没有文章」。
        other = self.intel.subscribe({'fakeid': OTHER, 'name': '另一个号'})
        self.assertIsNotNone(other)
        done = self.intel.wait_for(
            self.intel.run({'fakeid': OTHER, 'mode': 'latest', 'sogou': False})['id'], timeout=5)
        self.assertEqual(done['status'], 'partial')
        self.assertEqual(done['outcome']['list']['state'], 'partial')
        self.assertNotIn('查询成功，本次没有新文章', done['outcome']['summary'])

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


class OutcomeStatusTests(StatusTestCase):
    """prep §2/§3：三段（列表/保存/正文）真实计数与 11 种终态规则。"""

    def test_manual_all_save_failures_are_error(self):
        """列表成功、每一条保存都失败 → save.failed → error，不再谎称完成。"""
        def boom(*args, **kwargs):
            raise sqlite3.Error('落库失败')

        self.intel._upsert_article = boom
        done = self.go(sogou=False, backend=True)
        self.assertEqual(done['status'], 'error')
        self.assertEqual(done['outcome']['list']['state'], 'complete')
        self.assertEqual(done['outcome']['save']['state'], 'failed')
        self.assertEqual(done['outcome']['save']['succeeded'], 0)
        self.assertEqual(done['outcome']['save']['failed'], done['outcome']['save']['attempted'])

    def test_partial_save_counts_committed_rows(self):
        """部分保存失败：attempted=succeeded+failed，已提交的成果保留，终态 partial。"""
        original = self.intel._upsert_article
        self.backend.items = [
            {'title': '甲', 'link': 'https://mp.weixin.qq.com/s/PART00001', 'published_at': 1788319003, 'aid': 'p1'},
            {'title': '乙', 'link': 'https://mp.weixin.qq.com/s/PART00002', 'published_at': 1788319002, 'aid': 'p2'},
            {'title': '丙', 'link': 'https://mp.weixin.qq.com/s/PART00003', 'published_at': 1788319001, 'aid': 'p3'}]

        def flaky(identifier, item, job_id, connection=None):
            if item.get('aid') == 'p2':
                raise sqlite3.Error('这一条失败')
            return original(identifier, item, job_id, connection)

        self.intel._upsert_article = flaky
        done = self.go(sogou=False, backend=True)
        self.assertEqual(done['status'], 'partial')
        save = done['outcome']['save']
        self.assertEqual(save['attempted'], 3)
        self.assertEqual(save['succeeded'], 2)
        self.assertEqual(save['failed'], 1)
        self.assertEqual(save['new'], 2)
        self.assertEqual(save['attempted'], save['succeeded'] + save['failed'])
        self.assertEqual(done['added'], 2)
        self.assertEqual(self.intel.articles(BIZ)['total'], 2, '已提交的成果要保留')

    def test_existing_articles_count_as_success(self):
        """全是复用也算保存成功：done + existing=N，不误报「没有新文章=抓取失败」。"""
        self.intel.remember_albums(BIZ, [ALBUM])
        self.public.album_page = lambda *a, **k: {
            'title': '占位合集', 'article_count': 1, 'nickname': NAME, 'continue_flag': False,
            'items': [{'title': '复用文章', 'link': 'https://mp.weixin.qq.com/s/REUSE00001',
                       'published_at': 1788319000, 'msgid': '1', 'itemidx': '1', 'source': 'album'}]}
        first = self.go(sogou=False)
        self.assertEqual(first['status'], 'done')
        self.assertEqual(first['outcome']['save']['new'], 1)
        second = self.go(sogou=False)
        self.assertEqual(second['status'], 'done')
        self.assertEqual(second['added'], 0)
        save = second['outcome']['save']
        self.assertEqual(save['succeeded'], 1)
        self.assertEqual(save['new'], 0)
        self.assertEqual(save['existing'], 1)
        self.assertIn('复用', second['outcome']['summary'])
        # 复用不是失败：建议里不能出现「检查抓取失败」这类误导。
        self.assertIn('复用', self.last_result()['suggestion'])
        self.assertNotIn('抓取失败', self.last_result()['suggestion'])

    def test_empty_success_save_stage_is_complete(self):
        """L2：手动确实成功查询空列表时，save 阶段统一记 complete，与 prep §3 行3 一致。"""
        self.intel.remember_albums(BIZ, [ALBUM])
        done = self.go(sogou=False, backend=False)
        self.assertEqual(done['status'], 'done')
        save = done['outcome']['save']
        self.assertEqual(save['state'], 'complete')
        self.assertEqual(save['attempted'], 0)
        self.assertEqual(save['not_attempted'], 0)
        self.assertEqual(done['outcome']['list']['selected'], 0)

    def test_outcome_survives_reload_and_subscription_matches(self):
        """刷新后同一份回执：jobs 与订阅的 last_result 读的是同一份 outcome。"""
        done = self.go(sogou=False, backend=True)
        fresh = self.fresh()
        job = fresh.jobs(5)[0]
        self.assertTrue(job['outcome_available'])
        self.assertEqual(job['outcome']['version'], 1)
        row = fresh.subscriptions()[0]
        self.assertEqual(row['last_result']['outcome']['summary'], job['outcome']['summary'])
        self.assertEqual(row['last_status'], done['status'])

    def test_interrupted_job_rejects_late_outcome_write(self):
        """已标中断的行不可被迟到的 outcome 写回覆盖成 done。"""
        job_id = 'k' * 32
        started = datetime.now(timezone.utc).isoformat()
        with self.intel.db() as connection:
            connection.execute('INSERT INTO jobs(id,fakeid,name,mode,status,started_at) '
                               'VALUES(?,?,?,\'latest\',\'running\',?)', (job_id, BIZ, NAME, started))
        self.intel._finish_job(job_id, 'interrupted', '残留回收')
        self.intel._finish_job(job_id, 'done', '', outcome=self.intel._scheduled_outcome('complete', 1))
        row = self.intel.job(job_id)
        self.assertEqual(row['status'], 'interrupted')
        self.assertIsNone(row['outcome'], '迟到写回不能补上 outcome')


class OutcomeScheduledTests(StatusTestCase):
    """定时轮次同样写三段 outcome：正文 not_requested，回滚不保留半截成功。"""

    def test_scheduled_body_not_requested(self):
        receipt = self.intel.daily_latest(run_id='r' * 32)
        self.assertEqual(receipt['state'], 'done')
        job = self.intel.jobs(5)[0]
        self.assertEqual(job['outcome']['body']['state'], 'not_requested')
        self.assertEqual(job['outcome']['list']['state'], 'complete')

    def test_scheduled_round_aggregates_account_results(self):
        self.intel.subscribe({'fakeid': OTHER, 'name': '另一个号'})
        counter = {'n': 0}

        def per_account(identifier, begin, count):
            counter['n'] += 1
            return [{'title': f'文章{counter["n"]}',
                     'link': f'https://mp.weixin.qq.com/s/AGG{counter["n"]:05d}',
                     'published_at': 1788319000, 'aid': f'a{counter["n"]}'}]

        self.backend.appmsg_list = per_account
        receipt = self.intel.daily_latest(run_id='r' * 32)
        self.assertEqual(receipt['accounts'], 2)
        self.assertEqual(receipt['new_articles'], 2)
        self.assertEqual(receipt['state'], 'done')
        for job in self.intel.jobs(5):
            self.assertEqual(job['outcome']['save']['succeeded'], 1)

    def test_scheduled_transaction_rollback_counts_zero_saved(self):
        """共享事务回滚：前一条已写入、后一条失败 → 整批回滚，不能保留「前几条保存成功」的假计数。"""
        self.backend.items = [
            {'title': '甲', 'link': 'https://mp.weixin.qq.com/s/RB00000001', 'published_at': 3, 'aid': 'x1'},
            {'title': '乙', 'link': 'https://mp.weixin.qq.com/s/RB00000002', 'published_at': 2, 'aid': 'x2'}]
        original = self.intel._upsert_article
        calls = {'n': 0}

        def flaky(identifier, item, job_id, connection=None):
            calls['n'] += 1
            if calls['n'] == 2:
                raise RuntimeError('第二条回滚')
            return original(identifier, item, job_id, connection)

        self.intel._upsert_article = flaky
        receipt = self.intel.daily_latest(run_id='r' * 32)
        self.assertEqual(receipt['state'], 'error')
        self.assertEqual(self.intel.articles(BIZ)['total'], 0, '回滚后一条都不该留下')
        job = self.intel.jobs(5)[0]
        self.assertEqual(job['outcome']['save']['succeeded'], 0)

    def test_rate_limit_stops_without_retry(self):
        self.backend.error = RateLimited('命中公众号后台限流（200013）', until=0)
        receipt = self.intel.daily_latest(run_id='r' * 32)
        self.assertEqual(receipt['state'], 'rate_limited')
        self.assertEqual(len(self.backend.calls), 1, '限流后不再重试')
        job = self.intel.jobs(5)[0]
        self.assertEqual(job['status'], 'rate_limited')
        self.assertEqual(job['outcome']['body']['state'], 'not_requested')
        self.assertEqual(job['outcome']['list']['state'], 'partial')

    def test_scheduled_success_counts_selected_and_attempted(self):
        """M1 探针1：定时成功取到 3 篇，list.selected 不能固定 0。

        不变量：selected = attempted + not_attempted；attempted = succeeded + failed。
        """
        self.backend.items = [
            {'title': '甲', 'link': 'https://mp.weixin.qq.com/s/SEL0000001', 'published_at': 3, 'aid': 's1'},
            {'title': '乙', 'link': 'https://mp.weixin.qq.com/s/SEL0000002', 'published_at': 2, 'aid': 's2'},
            {'title': '丙', 'link': 'https://mp.weixin.qq.com/s/SEL0000003', 'published_at': 1, 'aid': 's3'}]
        receipt = self.intel.daily_latest(run_id='r' * 32)
        self.assertEqual(receipt['state'], 'done')
        self.assertEqual(receipt['new_articles'], 3)
        job = self.intel.jobs(5)[0]
        listed, save = job['outcome']['list'], job['outcome']['save']
        self.assertEqual(listed['state'], 'complete')
        self.assertEqual(listed['found'], 3)
        self.assertEqual(listed['selected'], 3)
        self.assertEqual(save['attempted'], 3)
        self.assertEqual(save['succeeded'], 3)
        self.assertEqual(save['not_attempted'], 0)
        self.assertEqual(listed['selected'], save['attempted'] + save['not_attempted'])
        self.assertEqual(save['attempted'], save['succeeded'] + save['failed'])

    def test_scheduled_rollback_records_real_attempts_and_all_ledgers(self):
        """M1 探针2：保存阶段整批回滚时，attempted 必须是真实调用次数；三段回执、
        job 基础计数、轮次回执都吃同一份 known 台账（都不许丢或虚构）。

        3 篇列表、第 2 条 _upsert_article 抛异常 → 真实调用 2 次、整批回滚；回执
        save attempted=2/failed=2/not_attempted=1；job.found 与轮次 listed 都等于真实列表 3，
        summary 不得写「尝试 3 篇」。
        """
        self.backend.items = [
            {'title': '甲', 'link': 'https://mp.weixin.qq.com/s/RBK0000001', 'published_at': 3, 'aid': 'r1'},
            {'title': '乙', 'link': 'https://mp.weixin.qq.com/s/RBK0000002', 'published_at': 2, 'aid': 'r2'},
            {'title': '丙', 'link': 'https://mp.weixin.qq.com/s/RBK0000003', 'published_at': 1, 'aid': 'r3'}]
        original = self.intel._upsert_article
        calls = {'n': 0}

        def flaky(identifier, item, job_id, connection=None):
            calls['n'] += 1
            if calls['n'] == 2:
                raise RuntimeError('第二条回滚')
            return original(identifier, item, job_id, connection)

        self.intel._upsert_article = flaky
        receipt = self.intel.daily_latest(run_id='r' * 32)
        # 实际调用次数：第三条根本没进入循环。
        self.assertEqual(calls['n'], 2, '真实调用只有 2 次，第三条未尝试')
        # 数据库事实：整批回滚，一条都不该留下。
        self.assertEqual(receipt['state'], 'error')
        self.assertEqual(self.intel.articles(BIZ)['total'], 0, '整批回滚，一条都不该留下')
        job = self.intel.jobs(5)[0]
        listed, save = job['outcome']['list'], job['outcome']['save']
        # 三段回执：列表成功 3 篇保留，保存按真实尝试记，正文未请求。
        self.assertEqual(listed['state'], 'complete')
        self.assertEqual(listed['found'], 3)
        self.assertEqual(listed['selected'], 3)
        self.assertEqual(save['state'], 'failed')
        self.assertEqual(save['attempted'], 2)
        self.assertEqual(save['succeeded'], 0)
        self.assertEqual(save['failed'], 2)
        self.assertEqual(save['not_attempted'], 1)
        self.assertEqual(job['outcome']['body']['state'], 'not_requested')
        self.assertEqual(save['attempted'], save['succeeded'] + save['failed'])
        self.assertEqual(listed['selected'], save['attempted'] + save['not_attempted'])
        # job 基础计数：列表事实保留，新增为 0（回滚）。
        self.assertEqual(job['found'], 3)
        self.assertEqual(job['added'], 0)
        # 轮次回执：列表事实保留，新增为 0。
        self.assertEqual(receipt['listed'], 3)
        self.assertEqual(receipt['new_articles'], 0)
        self.assertIn('列表 3 篇', job['outcome']['summary'])
        self.assertIn('保存全部失败（尝试 2 篇）', job['outcome']['summary'])
        self.assertNotIn('尝试 3 篇', job['outcome']['summary'])
        self.assertNotIn('取列表失败', job['outcome']['summary'])

    def test_scheduled_finish_failure_keeps_committed_ledgers(self):
        """M1 探针2b：保存已提交后 _finish_job 首次抛异常，收口仍要保留已知成果。

        3 篇确实已入库；job.found/added 与外层 receipt.listed/new_articles 必须是 3/3，
        不能因为收口自身失败就报 0；嵌套 outcome.save 仍是 complete/new=3。
        """
        self.backend.items = [
            {'title': '甲', 'link': 'https://mp.weixin.qq.com/s/FIN0000001', 'published_at': 3, 'aid': 'f1'},
            {'title': '乙', 'link': 'https://mp.weixin.qq.com/s/FIN0000002', 'published_at': 2, 'aid': 'f2'},
            {'title': '丙', 'link': 'https://mp.weixin.qq.com/s/FIN0000003', 'published_at': 1, 'aid': 'f3'}]
        upserts = {'n': 0}
        original_upsert = self.intel._upsert_article

        def counted(identifier, item, job_id, connection=None):
            upserts['n'] += 1
            return original_upsert(identifier, item, job_id, connection)

        self.intel._upsert_article = counted
        original_finish = self.intel._finish_job
        finish_calls = {'n': 0}

        def flaky_finish(*args, **kwargs):
            finish_calls['n'] += 1
            if finish_calls['n'] == 1:
                raise RuntimeError('收口首次写回失败')
            return original_finish(*args, **kwargs)

        self.intel._finish_job = flaky_finish
        receipt = self.intel.daily_latest(run_id='r' * 32)
        # 实际调用次数：三篇都真实落库。
        self.assertEqual(upserts['n'], 3, '三篇都真实调用过保存')
        # 数据库事实：保存已提交，三篇确实在库里。
        self.assertEqual(receipt['state'], 'error')
        self.assertEqual(self.intel.articles(BIZ)['total'], 3, '保存已提交，三篇都在库里')
        job = self.intel.jobs(5)[0]
        listed, save = job['outcome']['list'], job['outcome']['save']
        # 三段回执：保留已提交成果，正文未请求。
        self.assertEqual(listed['state'], 'complete')
        self.assertEqual(listed['found'], 3)
        self.assertEqual(listed['selected'], 3)
        self.assertEqual(save['state'], 'complete')
        self.assertEqual(save['attempted'], 3)
        self.assertEqual(save['succeeded'], 3)
        self.assertEqual(save['new'], 3)
        self.assertEqual(job['outcome']['body']['state'], 'not_requested')
        # job 基础计数：保留已知成果，不能报 0。
        self.assertEqual(job['found'], 3)
        self.assertEqual(job['added'], 3)
        # 轮次回执：同样保留已知成果。
        self.assertEqual(receipt['listed'], 3)
        self.assertEqual(receipt['new_articles'], 3)
        self.assertIn('列表 3 篇', job['outcome']['summary'])
        self.assertIn('保存 3 篇', job['outcome']['summary'])

    def test_scheduled_empty_success_save_stage_is_complete(self):
        """L2：确实成功查询空列表时，save 阶段统一记 complete（prep §3 行3）。"""
        self.backend.items = []
        receipt = self.intel.daily_latest(run_id='r' * 32)
        self.assertEqual(receipt['state'], 'done')
        job = self.intel.jobs(5)[0]
        self.assertEqual(job['outcome']['list']['state'], 'complete')
        self.assertEqual(job['outcome']['list']['found'], 0)
        self.assertEqual(job['outcome']['save']['state'], 'complete')
        self.assertEqual(job['outcome']['list']['selected'], 0)
        self.assertIn('查询成功，本次没有新文章', job['outcome']['summary'])


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
        # 阶段 B 新语义：定时先走发现→合集，无合集时才回退后台；搜狗仍不走。
        self.assertIn('还没发现任何合集', texts['album'])
        self.assertIn('不走搜狗', texts['sogou'])
        self.assertEqual(texts['backend'], '取到 1 篇')
        self.assertEqual(self.intel.subscriptions()[0]['last_status'], 'done')
        # 文章挂到这次的定时任务行上，便于回看是哪一轮抓的。
        job_id = self.intel.jobs(5)[0]['id']
        self.assertEqual(self.intel.articles(BIZ)['articles'][0]['job_id'], job_id)

    def test_whole_round_cooldown_skip_is_recorded_and_sends_nothing(self):
        # 阶段 B 新语义：后台冷却只挡后台那一路，公开路径（发现/合集）仍按纪律执行；
        # 旧语义是「冷却整轮跳过、未发送任何请求」，新语义是「后台被挡、账号照常处理」。
        self.backend.quota.settings.set(BackendQuota.K_COOLDOWN, repr(int(time.time()) + 7200))
        receipt = self.intel.daily_latest(run_id='r' * 32)
        self.assertEqual(receipt['state'], 'cooldown')
        self.assertEqual(self.backend.calls, [])
        result = self.last_result()
        self.assertEqual(result['status'], 'cooldown')
        self.assertEqual(result['status_label'], '冷却中，本轮跳过')
        self.assertIn('后台接口冷却中', result['detail'])
        self.assertIn('未发送任何后台请求', result['detail'])
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

    def test_finish_writeback_failure_is_logged_and_the_owner_recovery_recovers_it(self):
        """收口自己失败（库不可写）：不吞错，回执写明恢复路径；v1 释放所有权后下次检查恢复。

        旧语义：legacy 行按残留规则等 started_at 起 3600 秒。新语义：v1 在 owner FD 关闭后，
        下次状态检查即按所有权 CAS 恢复，不必等 3600 秒（legacy 的 3600 秒由
        test_stale_running_job_is_reported_as_interrupted_on_the_subscription 覆盖）。
        """
        def boom(*args, **kwargs):
            raise sqlite3.OperationalError('数据库不可写')

        self.intel._finish_job = boom
        with self.assertLogs('intel_store', level='ERROR') as logs:
            receipt = self.intel.daily_latest(run_id='r' * 32)
        self.assertEqual(receipt['state'], 'error')
        self.assertIn('收口也失败', receipt['detail'])
        self.assertIn('所有权', receipt['detail'])
        self.assertTrue(any('收口失败' in line for line in logs.output), '收口失败必须记日志')
        # 不能假装已收口：那一行还挂着 running。
        self.assertEqual([job['status'] for job in self.intel.jobs(20)], ['running'])
        # v1 已释放所有权：下次状态检查立即按所有权恢复为 interrupted，不等 3600 秒。
        self.assertIsNone(self.intel.active_job())
        self.assertEqual(self.intel.jobs(20)[0]['status'], 'interrupted')
        row = self.intel.subscriptions()[0]
        self.assertEqual(row['last_status'], 'interrupted')
        self.assertEqual(row['last_result']['status'], 'interrupted')

    def test_manual_run_save_stage_failure_still_closes_the_row(self):
        """手动 _execute 同样核对：落库阶段的意外异常不会把任务行留在 running。

        合成的是「保存全部失败」（第一条就抛 RuntimeError）：按 prep §3 行4，
        列表成功但一条都没保存 → save.failed → 终态 error，不再只允许 done/partial。
        """
        def boom(*args, **kwargs):
            raise RuntimeError('落库炸了')

        self.intel._upsert_article = boom
        done = self.go(sogou=False, backend=True)
        self.assertNotIn(done['status'], ('running', 'queued'))
        self.assertEqual(done['status'], 'error')
        self.assertEqual(done['outcome']['save']['state'], 'failed')
        self.assertEqual(done['outcome']['save']['succeeded'], 0)
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
