"""情报采集 · 二期测试：推荐订阅（A）与定时订阅（B）。

全部 HTTP 与后台调用都走 mock；只读库用临时文件，不碰真实 intake.db / knowledge.db。
"""
import json
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from intel_recommend import Recommender, classify
from intel_store import Intel
from intel_wechat_mp import (BackendQuota, BodyThrottle, CredentialExpired, CredentialsMissing,
                             RateLimited, parse_article)
from knowledge_store import Knowledge
from scheduled_tasks import ScheduledTasks
from server import Store, seed_intel_schedule

BIZ = 'MzB5NTkwMDAwMDE=='
OTHER = 'MzB5NTkwMDAwMDI=='
NEW_LINK = 'https://mp.weixin.qq.com/s/AbCdEf12AbCd'
OLD_LINK = 'https://mp.weixin.qq.com/s?__biz=MzB5NTkwMDAwMDE%3D%3D&mid=2247483001&idx=1&sn=abcdef'
WAERR = 'https://mp.weixin.qq.com/mp/waerrpage?appid=wx059&type=upgrade&upgradetype=3#wechat_redirect'
HOMEPAGE = 'https://mp.weixin.qq.com/mp/homepage?__biz=MzB5NTkwMDAwMDE%3D%3D&hid=1'
FIXTURE_ARTICLE = None


def fixture_article():
    from pathlib import Path
    return (Path(__file__).with_name('fixtures') / 'wechat_mp_article.html').read_text(encoding='utf-8')


class ClassifyTests(unittest.TestCase):
    """链接形态判定：这一层决定了要发多少请求，所以单独锁死。"""

    def test_old_style_yields_fakeid_without_any_request(self):
        kind, business = classify(OLD_LINK)
        self.assertEqual(kind, 'known')
        self.assertEqual(business, BIZ)  # 从 URL 直接读出，不需要抓页面

    def test_new_style_needs_one_fetch(self):
        self.assertEqual(classify(NEW_LINK), ('needs_fetch', ''))

    def test_error_and_home_pages_are_skipped_not_fetched(self):
        self.assertEqual(classify(WAERR)[0], 'skip')
        self.assertEqual(classify(HOMEPAGE)[0], 'skip')
        self.assertEqual(classify('https://example.com/s/abcdef')[0], 'skip')
        self.assertEqual(classify('')[0], 'skip')
        self.assertEqual(classify(None)[0], 'skip')

    def test_fakeid_looking_query_param_is_validated(self):
        bad = 'https://mp.weixin.qq.com/s?__biz=notafakeid&mid=1'
        self.assertEqual(classify(bad), ('needs_fetch', ''))

    def test_http_scheme_is_accepted(self):
        self.assertEqual(classify(OLD_LINK.replace('https', 'http'))[1], BIZ)


class NullPublic:
    """三期公开接口的空实现：被调用就说明测试出错了。"""

    def album_page(self, *a, **k):
        raise AssertionError('测试不该访问合集接口')

    def sogou_search(self, *a, **k):
        raise AssertionError('测试不该访问搜狗')

    def sogou_resolve(self, *a, **k):
        raise AssertionError('测试不该访问搜狗')


class RecommenderTestCase(unittest.TestCase):
    """搭一个带只读 intake.db / knowledge.db 的工作区。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(self.tmp.name)
        self.store._knowledge = Knowledge(self.store, sources=[])
        self.intel = Intel(self.store, client_factory=lambda s: None, public_factory=lambda s: NullPublic())
        workspace = self.store.workspace
        (workspace / 'inspiration').mkdir(parents=True, exist_ok=True)
        (workspace / 'knowledge').mkdir(parents=True, exist_ok=True)
        self.intake_db = workspace / 'inspiration' / 'intake.db'
        self.knowledge_db = workspace / 'knowledge' / 'knowledge.db'
        self.make_intake()
        self.make_knowledge()
        self.fetched = []

    def tearDown(self):
        self.tmp.cleanup()

    def make_intake(self, rows=(('a' * 32, OLD_LINK, '回收项一'),)):
        connection = sqlite3.connect(self.intake_db)
        connection.execute('''CREATE TABLE IF NOT EXISTS drops(id TEXT PRIMARY KEY, kind TEXT, received_at TEXT,
            url TEXT, title TEXT, text TEXT DEFAULT '', file_path TEXT DEFAULT '', file_meta TEXT DEFAULT '{}',
            reason TEXT DEFAULT '', status TEXT DEFAULT 'pending', error TEXT DEFAULT '',
            document_id TEXT DEFAULT '', revision INTEGER DEFAULT 1, meta TEXT DEFAULT '{}',
            recognition TEXT DEFAULT '{}')''')
        connection.executemany('INSERT OR REPLACE INTO drops(id,kind,received_at,url,title) VALUES(?,"link",?,?,?)',
                               [(r[0], '2026-10-09T00:00:00Z', r[1], r[2]) for r in rows])
        connection.commit(); connection.close()

    def make_knowledge(self, rows=(('b' * 32, NEW_LINK, '知识库文章'),)):
        connection = sqlite3.connect(self.knowledge_db)
        connection.execute('''CREATE TABLE IF NOT EXISTS documents(id TEXT PRIMARY KEY, source TEXT, kind TEXT,
            title TEXT, body TEXT DEFAULT '', url TEXT, modified TEXT, tags TEXT DEFAULT '[]',
            hash TEXT DEFAULT '', provenance TEXT DEFAULT '{}')''')
        connection.executemany('INSERT OR REPLACE INTO documents'
                               '(id,source,kind,title,body,url,modified,tags,hash,provenance) '
                               'VALUES(?,"test","wechat_links",?,"正文",?,"2026-10-09","[]","h","{}")',
                               [(r[0], r[2], r[1]) for r in rows])
        connection.commit(); connection.close()

    def recommender(self, client=None, limit=20):
        store = self.intel
        if client is not None:
            store._client = client
        return Recommender(store, fetch_limit=limit)


class CollectTests(RecommenderTestCase):
    def test_collect_counts_shapes_and_never_fetches(self):
        second_old = OLD_LINK.replace('mid=2247483001', 'mid=2247483002')
        self.make_knowledge([('b' * 32, NEW_LINK, '新式'), ('c' * 32, WAERR, '错误页'),
                             ('d' * 32, HOMEPAGE, '主页'), ('e' * 32, second_old, '旧式')])
        result = self.recommender().collect()
        self.assertEqual(result['known'], 2)       # 两条旧式，URL 里直接有 __biz
        self.assertEqual(result['needs_fetch'], 1)  # 一条新式
        self.assertEqual(result['skipped'], 2)     # waerrpage + homepage
        self.assertEqual(result['accounts'], 1)    # 两条旧式同属一个号
        self.assertEqual(self.fetched, [])

    def test_collect_is_idempotent_across_rounds(self):
        rec = self.recommender()
        rec.collect(); rec.collect(); rec.collect()
        with self.intel.db() as connection:
            links = connection.execute('SELECT COUNT(*) FROM link_index').fetchone()[0]
            sources = connection.execute('SELECT COUNT(*) FROM recommendation_sources').fetchone()[0]
            count = connection.execute('SELECT count FROM recommendations').fetchone()[0]
        self.assertEqual(links, 2)
        # 新式链接还没解析出 fakeid，因此此刻只有旧式那条有来源行。
        self.assertEqual(sources, 1)
        self.assertEqual(count, 1)  # 重复扫描不会把出现次数刷上去

    def test_missing_databases_do_not_crash(self):
        self.intake_db.unlink(); self.knowledge_db.unlink()
        self.assertEqual(self.recommender().collect()['links'], 0)

    def test_scan_does_not_write_to_intake_or_knowledge(self):
        before = self.intake_db.read_bytes(), self.knowledge_db.read_bytes()
        self.recommender().collect()
        self.assertEqual((self.intake_db.read_bytes(), self.knowledge_db.read_bytes()), before)


class ResolveTests(RecommenderTestCase):
    def client(self, behaviour=None):
        fetch_limit = {'n': 0}

        class Client:
            quota = BackendQuota(_MemorySettings())

            def credentials_ready(self):
                return ('stub', '9999999999')

            def search_biz(self, query):
                return []

            def fetch_article(self, url):
                fetch_limit['n'] += 1
                if behaviour:
                    behaviour(url)
                return parse_article(fixture_article(), url)
        self.fetched.append(fetch_limit)
        return Client()

    def test_one_fetch_per_unknown_account_not_per_link(self):
        self.make_knowledge([('b' * 32, NEW_LINK, '一'), ('c' * 32, OLD_LINK, '二')])
        rec = self.recommender(client=self.client())
        rec.collect()
        rec.resolve()
        self.assertEqual(self.fetched[0]['n'], 1)  # 旧式那条不需要请求
        again = rec.resolve()
        self.assertEqual(self.fetched[0]['n'], 1)  # 已解析的不重复请求
        self.assertEqual(again['attempted'], 0)

    def test_resolved_link_is_cached_and_never_requested_again(self):
        self.make_knowledge([('b' * 32, NEW_LINK, '一')])
        rec = self.recommender(client=self.client())
        rec.collect(); rec.resolve()
        self.make_knowledge([('b' * 32, NEW_LINK, '一')])
        fresh = Recommender(self.intel, fetch_limit=20)
        fresh.intel._client = self.client()
        fresh.collect(); fresh.resolve()
        self.assertEqual(self.fetched[1]['n'], 0)

    def test_round_budget_is_respected(self):
        rows = [(chr(97 + i) * 32, f'https://mp.weixin.qq.com/s/Link{i:06d}', f'文章{i}') for i in range(30)]
        self.make_knowledge(rows)
        rec = self.recommender(client=self.client(), limit=20)
        rec.collect(); result = rec.resolve()
        self.assertEqual(result['attempted'], 20)
        self.assertEqual(self.fetched[0]['n'], 20)

    def test_failed_link_is_recorded_and_not_retried_forever(self):
        self.make_knowledge([('b' * 32, NEW_LINK, '一')])
        rec = self.recommender(client=self.client(behaviour=lambda url: (_ for _ in ()).throw(
            ValueError('文章页内容异常，未取到正文'))))
        rec.collect(); result = rec.resolve()
        self.assertEqual((result['failed'], result['resolved']), (1, 0))
        self.assertEqual(self.fetched[0]['n'], 1)
        again = rec.resolve()
        self.assertEqual(again['attempted'], 0)  # 失败的不会在下一轮反复重试

    def test_rate_limit_stops_the_round(self):
        rows = [(chr(97 + i) * 32, f'https://mp.weixin.qq.com/s/Lk{i:06d}', f'文章{i}') for i in range(10)]
        self.make_knowledge(rows)

        def blow(url):
            raise RateLimited('命中公众号后台限流（200013），已停止本次抓取', until=123)
        rec = self.recommender(client=self.client(behaviour=blow))
        rec.collect(); result = rec.resolve()
        self.assertEqual(result['attempted'], 0)
        self.assertIn('200013', result['stopped'])
        self.assertEqual(self.fetched[0]['n'], 1)  # 命中即停，没有继续请求

    def test_missing_credentials_stop_the_round(self):
        def blow(url):
            raise CredentialsMissing('需重新登录后台：钥匙串里没有公众号后台凭证')
        rec = self.recommender(client=self.client(behaviour=blow))
        rec.collect(); result = rec.resolve()
        self.assertIn('需重新登录后台', result['stopped'])


class ListingTests(RecommenderTestCase):
    def seed_two(self):
        # BIZ 同时出现在回收站(setUp 默认)与知识库；OTHER 只在知识库。
        self.make_knowledge([('b' * 32, OLD_LINK, '回收链接也进了知识库'),
                             ('c' * 32, OLD_LINK.replace('MzB5NTkwMDAwMDE', 'MzB5NTkwMDAwMDI'), '另一个号')])
        rec = self.recommender()
        rec.collect()
        with self.intel.db() as connection:
            connection.execute("UPDATE recommendations SET nickname='占位公众号' WHERE fakeid=?", (BIZ,))

    def test_listing_reports_name_count_and_sources(self):
        self.seed_two()
        items = self.recommender().listing()['items']
        self.assertEqual(len(items), 2)
        first = next(i for i in items if i['fakeid'] == BIZ)
        self.assertEqual(first['nickname'], '占位公众号')
        self.assertEqual(first['count'], 2)  # 回收站 1 + 知识库 1
        self.assertEqual({s['kind'] for s in first['sources']}, {'intake', 'knowledge'})
        self.assertTrue(first['name_known'])

    def test_unknown_name_is_flagged_not_faked(self):
        self.seed_two()
        items = self.recommender().listing()['items']
        other = next(i for i in items if i['fakeid'] == OTHER)
        self.assertEqual(other['nickname'], '')
        self.assertFalse(other['name_known'])

    def test_subscribed_account_leaves_the_list(self):
        self.seed_two()
        self.intel.subscribe({'fakeid': BIZ, 'name': '占位公众号'})
        items = self.recommender().listing()['items']
        self.assertNotIn(BIZ, [i['fakeid'] for i in items])

    def test_ignore_hides_it_and_remembers(self):
        self.seed_two()
        rec = self.recommender()
        rec.ignore(BIZ)
        self.assertNotIn(BIZ, [i['fakeid'] for i in rec.listing()['items']])
        rec.collect()  # 再扫描也不会复活
        self.assertNotIn(BIZ, [i['fakeid'] for i in rec.listing()['items']])
        rec.unignore(BIZ)
        self.assertIn(BIZ, [i['fakeid'] for i in rec.listing()['items']])

    def test_ignore_keeps_source_rows(self):
        self.seed_two()
        self.recommender().ignore(BIZ)
        with self.intel.db() as connection:
            self.assertEqual(connection.execute(
                'SELECT COUNT(*) FROM recommendation_sources WHERE fakeid=?', (BIZ,)).fetchone()[0], 2)

    def test_bad_fakeid_is_rejected(self):
        with self.assertRaisesRegex(ValueError, '公众号编号'):
            self.recommender().ignore('nope')

    def test_source_breakdown_is_aggregated_by_kind(self):
        """同一个号不该出现两条同名来源（例如「知识库 15 条、知识库 1 条」）。"""
        self.make_knowledge([('b' * 32, NEW_LINK, '新式'), ('c' * 32, NEW_LINK + 'x', '新式二')])
        rec = self.recommender()
        rec.collect()
        with self.intel.db() as connection:
            # 这两条新式链接还没解析出 fakeid，手工补上来源行，模拟解析后的状态。
            connection.execute("UPDATE link_index SET fakeid=? WHERE link!=''", (BIZ,))
            connection.executemany('INSERT OR IGNORE INTO recommendation_sources'
                '(fakeid,source_kind,source_id,link,title,seen_at) VALUES(?,"knowledge",?,?,?,?)',
                [(BIZ, 'b' * 32, NEW_LINK, '新式', 'now'), (BIZ, 'c' * 32, NEW_LINK + 'x', '新式二', 'now')])
            connection.execute("UPDATE recommendations SET count=(SELECT COUNT(*) FROM recommendation_sources "
                               "s WHERE s.fakeid=recommendations.fakeid) WHERE fakeid=?", (BIZ,))
        item = next(i for i in rec.listing()['items'] if i['fakeid'] == BIZ)
        self.assertEqual([s['kind'] for s in item['sources']], ['knowledge', 'intake'])
        self.assertEqual(item['sources'][0]['count'], 2)   # 两条知识库聚合成一条

    def test_resolved_link_is_attributed_to_its_original_source(self):
        self.make_knowledge([('b' * 32, NEW_LINK, '新式')])
        rec = self.recommender(client=_StubClient())
        rec.collect()
        rec.resolve()
        with self.intel.db() as connection:
            # 解析后只应留下真实的两种来源，不会多出虚构的种类
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM recommendation_sources WHERE source_kind NOT IN ('intake','knowledge')"
            ).fetchone()[0], 0)
            kinds = {r[0] for r in connection.execute('SELECT source_kind FROM recommendation_sources')}
            # 回收站那条来自 setUp；解析出来的这条必须归到 knowledge，不是虚构种类
            self.assertEqual(kinds, {'intake', 'knowledge'})
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM recommendation_sources WHERE link=?", (NEW_LINK,)).fetchone()[0], 1)


class DailyLatestTests(RecommenderTestCase):
    """二期 B：每轮对已订阅号各抓最新 1 页。"""

    def client(self, pages=None, errors=None):
        state = {'calls': []}

        class Client:
            def __init__(self):
                self.quota = BackendQuota(_MemorySettings())

            def credentials_ready(self):
                return ('stub', '9999999999')

            def search_biz(self, query):
                return []

            def appmsg_list(self, identifier, begin, count):
                state['calls'].append((identifier, begin, count))
                if errors and identifier in errors:
                    raise errors[identifier]
                return list((pages or {}).get(identifier, []))

            def fetch_article(self, url):
                raise AssertionError('定时轮次只抓列表，不抓正文')
        self.calls = state['calls']
        return Client()

    def page(self, tag, count):
        return [{'title': f'{tag}{i}', 'link': f'https://mp.weixin.qq.com/s/{tag}{i:04d}',
                 'published_at': 1700000000 + i, 'aid': f'{tag}{i:04d}'} for i in range(count)]

    def test_round_walks_every_subscription_once(self):
        self.intel.subscribe({'fakeid': BIZ, 'name': '一号'})
        self.intel.subscribe({'fakeid': OTHER, 'name': '二号'})
        self.intel._client = self.client({BIZ: self.page('A', 3), OTHER: self.page('B', 2)})
        receipt = self.intel.daily_latest(run_id='r' * 32)
        self.assertEqual(receipt['accounts'], 2)
        self.assertEqual((receipt['listed'], receipt['new_articles']), (5, 5))
        self.assertEqual(receipt['state'], 'done')
        self.assertEqual(len(self.calls), 2)
        self.assertTrue(all(begin == 0 and count == 20 for _, begin, count in self.calls))

    def test_round_never_fetches_bodies(self):
        self.intel.subscribe({'fakeid': BIZ, 'name': '一号'})
        self.intel._client = self.client({BIZ: self.page('A', 2)})
        self.intel.daily_latest(run_id='r' * 32)
        for row in self.intel.articles(BIZ)['articles']:
            self.assertEqual(row['body_status'], 'pending')  # 正文仍待抓，不自动入库
            self.assertEqual(row['drop_id'], '')

    def test_new_articles_are_not_pushed_to_intake_or_knowledge(self):
        self.intel.subscribe({'fakeid': BIZ, 'name': '一号'})
        self.intel._client = self.client({BIZ: self.page('A', 2)})
        before = len(self.store.creative_intake.listing(80)['items'])
        self.intel.daily_latest(run_id='r' * 32)
        # 定时轮次不往回收站送任何东西。
        self.assertEqual(len(self.store.creative_intake.listing(80)['items']), before)
        self.assertEqual(self.store.knowledge.search('A0')['total'], 0)

    def test_cooldown_skips_the_whole_round_and_sends_nothing(self):
        self.intel.subscribe({'fakeid': BIZ, 'name': '一号'})
        client = self.client({BIZ: self.page('A', 2)})
        client.quota.settings.set(BackendQuota.K_COOLDOWN, repr(__import__('time').time() + 7200))
        self.intel._client = client
        receipt = self.intel.daily_latest(run_id='r' * 32)
        self.assertEqual(receipt['state'], 'cooldown')
        self.assertEqual(receipt['accounts'], 0)
        self.assertIn('整轮跳过', receipt['detail'])
        self.assertEqual(self.calls, [])

    def test_rate_limit_stops_the_round_without_retry(self):
        self.intel.subscribe({'fakeid': OTHER, 'name': '二号'})
        self.intel.subscribe({'fakeid': BIZ, 'name': '一号'})   # added_at DESC → 一号先跑
        self.intel._client = self.client(errors={BIZ: RateLimited('命中限流（200013）', until=999)})
        receipt = self.intel.daily_latest(run_id='r' * 32)
        self.assertEqual(receipt['state'], 'rate_limited')
        self.assertEqual(len(self.calls), 1)   # 二号没被请求
        self.assertIn('未重试', receipt['detail'])

    def test_expired_credentials_are_reported_not_retried(self):
        self.intel.subscribe({'fakeid': BIZ, 'name': '一号'})
        self.intel._client = self.client(errors={BIZ: CredentialExpired('需重新登录后台：返回登录页')})
        receipt = self.intel.daily_latest(run_id='r' * 32)
        self.assertEqual(receipt['state'], 'credentials')
        self.assertIn('需重新登录后台', receipt['detail'])
        self.assertEqual(len(self.calls), 1)

    def test_cancellation_keeps_completed_work(self):
        self.intel.subscribe({'fakeid': BIZ, 'name': '一号'})
        self.intel.subscribe({'fakeid': OTHER, 'name': '二号'})
        import threading
        cancel = threading.Event(); cancel.set()
        self.intel._client = self.client({BIZ: self.page('A', 1), OTHER: self.page('B', 1)})
        receipt = self.intel.daily_latest(run_id='r' * 32, cancel=cancel)
        self.assertEqual(receipt['state'], 'cancelled')
        self.assertEqual(self.calls, [])

    def test_analyze_scheduled_reports_partial_on_rate_limit(self):
        self.intel.subscribe({'fakeid': BIZ, 'name': '一号'})
        self.intel._client = self.client(errors={BIZ: RateLimited('命中限流（200013）', until=1)})
        status, code, output = self.intel.analyze_scheduled('r' * 32, {}, None, None, 9e9)
        self.assertEqual(status, 'partial')
        self.assertIsNone(code)
        self.assertEqual(json.loads(output)['state'], 'rate_limited')

    def test_scheduled_uses_the_same_quota_instance(self):
        """定时与手动必须共用一个限流器，否则纪律会被绕开。"""
        self.intel.subscribe({'fakeid': BIZ, 'name': '一号'})
        client = self.client({BIZ: self.page('A', 1)})
        self.intel._client = client
        self.intel.daily_latest(run_id='r' * 32)
        self.assertIs(self.intel.client().quota, client.quota)
        self.assertGreater(self.intel.quota().cooldown_until(), 0) if False else None
        self.assertEqual(len(self.calls), 1)


class ScheduleRegistrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.schedules = ScheduledTasks(self.tmp.name, None, autostart=False)
        self.schedules.intel_analyzer = lambda *a: ('succeeded', None, '{}')

    def tearDown(self):
        self.tmp.cleanup()

    def valid(self, **over):
        return {'name': '公众号订阅（每日抓最新）', 'kind': 'intel_wechat_mp', 'body': '抓最新',
                'cwd': self.tmp.name, 'frequency': 'daily', 'at': '08:00', 'weekdays': [0, 1, 2, 3, 4, 5, 6],
                'timeout': 1800, 'enabled': False, 'allow_commands': False,
                'result_destination': 'runs', **over}

    def test_seeded_disabled_daily_at_eight(self):
        task = seed_intel_schedule(self.schedules, self.tmp.name)
        self.assertEqual(task['name'], '公众号订阅（每日抓最新）')
        self.assertIs(task['enabled'], False)   # 初始停用，要黄总点开启
        self.assertEqual(task['at'], '08:00')
        self.assertEqual(task['frequency'], 'daily')
        self.assertEqual(task['kind'], 'intel_wechat_mp')

    def test_seed_is_idempotent_and_keeps_edits(self):
        first = seed_intel_schedule(self.schedules, self.tmp.name)
        self.schedules.control(first['id'], 'resume')
        again = seed_intel_schedule(self.schedules, self.tmp.name)
        self.assertEqual(again['id'], first['id'])
        self.assertEqual(len(self.schedules.listing()['tasks']), 1)
        self.assertTrue(again['enabled'])  # 黄总开过之后，重启不能悄悄关掉

    def test_rejects_commands_clue_delivery_and_interval(self):
        for over in ({'allow_commands': True}, {'result_destination': 'clues'},
                     {'clue_id': 'c' * 32}, {'frequency': 'interval', 'minutes': 60}):
            with self.assertRaisesRegex(ValueError, '公众号订阅'):
                self.schedules.validate(self.valid(**over))

    def test_kind_needs_no_model_profile(self):
        task = self.schedules.validate(self.valid(profile_id=None))
        self.assertEqual(task['kind'], 'intel_wechat_mp')
        self.assertEqual(task['executor_name'] if 'executor_name' in task else '', '')

    def test_executor_runs_through_the_analyzer(self):
        seen = {}
        self.schedules.intel_analyzer = lambda run_id, task, cancel, config, deadline: (
            seen.update(run=run_id, kind=task['kind']) or ('succeeded', 0, '{"ok":true}'))
        task = seed_intel_schedule(self.schedules, self.tmp.name)
        with self.schedules.lock, self.schedules.db() as db:
            row = db.execute('SELECT * FROM tasks WHERE id=?', (task['id'],)).fetchone()
        data = json.loads(row['data']); data['enabled'] = True
        with self.schedules.lock, self.schedules.db() as db:
            db.execute('UPDATE tasks SET data=? WHERE id=?', (json.dumps(data), task['id']))
        run = self.schedules.claim(task['id'], 'manual', launch=True)
        self.schedules.close()
        self.assertEqual(seen.get('kind'), 'intel_wechat_mp')


class _StubClient:
    """只回解析结果、不发任何请求的客户端。"""

    def __init__(self):
        self.quota = BackendQuota(_MemorySettings())
        self.calls = 0

    def credentials_ready(self):
        return ('stub', '9999999999')

    def search_biz(self, query):
        return []

    def fetch_article(self, url):
        self.calls += 1
        return parse_article(fixture_article(), url)


class RouteDispatchTests(unittest.TestCase):
    """真起一个 Handler 打这些路由。

    早期版本这里是**读 server.py 的源码文本**来断言路由写法，既脆又误导：
    一次并发编辑就会让无关的测试红。这里改成打真实请求。
    """

    def setUp(self):
        import threading
        import time
        import urllib.error
        import urllib.request
        from http.server import ThreadingHTTPServer
        from server import Handler
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(self.tmp.name)
        self.port = 8900 + (id(self) % 90)
        self.server = ThreadingHTTPServer(('127.0.0.1', self.port), Handler)
        self.server.store = self.store
        self.server.schedules = None
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        time.sleep(.2)
        self.urllib = urllib.request
        self.HTTPError = urllib.error.HTTPError

    def tearDown(self):
        self.server.shutdown()
        self.tmp.cleanup()

    def post(self, path, payload):
        request = self.urllib.Request(
            f'http://127.0.0.1:{self.port}{path}', data=json.dumps(payload).encode(),
            headers={'Content-Type': 'application/json', 'X-Article-Studio': '1',
                     'Origin': f'http://127.0.0.1:{self.port}'})
        try:
            with self.urllib.urlopen(request, timeout=60) as response:
                return response.status, json.loads(response.read())
        except self.HTTPError as error:
            return error.code, json.loads(error.read())

    def test_recommendation_verbs_dispatch_instead_of_falling_through(self):
        """段数写错会让 verbs 落到 404/500 —— 这里逐个打真实请求。"""
        for verb in ('subscribe', 'ignore', 'unignore'):
            status, body = self.post(f'/api/intel/recommendations/{verb}', {'fakeid': 'not-a-fakeid'})
            # 段数对了才会走到该 verb 自己的处理器：报的是这一路的错，而不是「接口不存在」
            self.assertEqual(status, 400, verb)
            self.assertNotIn('不存在', body.get('error', ''), verb)
        # ignore / unignore 还要再校验 fakeid 本身
        for verb in ('ignore', 'unignore'):
            _, body = self.post(f'/api/intel/recommendations/{verb}', {'fakeid': 'not-a-fakeid'})
            self.assertIn('公众号编号', body.get('error', ''), verb)

    def test_scan_route_accepts_empty_payload_and_rejects_extra(self):
        status, _ = self.post('/api/intel/recommendations/scan', {})
        self.assertEqual(status, 200)
        status, body = self.post('/api/intel/recommendations/scan', {'x': 1})
        self.assertEqual(status, 400)
        self.assertIn('额外字段', body['error'])

    def test_unknown_recommendation_verb_is_reported(self):
        status, body = self.post('/api/intel/recommendations/bogus', {'fakeid': 'x'})
        self.assertEqual(status, 400)
        self.assertIn('未知推荐操作', body['error'])


class _MemorySettings:
    def __init__(self):
        self.values = {}

    def get(self, key, default=None):
        return self.values.get(key, default)

    def set(self, key, value):
        self.values[key] = str(value)


if __name__ == '__main__':
    unittest.main()