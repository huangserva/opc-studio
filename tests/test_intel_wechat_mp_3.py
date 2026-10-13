"""情报采集 · 三期测试：免后台的两条取文章路径。

全部 HTTP 走脱敏夹具 + mock opener，不发真实请求。
关键事实（都是实测，不是假设）：
- 文章链接的 chksm 必须保留，否则返回「未知错误/失效的验证页」。
- 合集接口成功时 ret 在 base_resp；不存在的合集 ret=10004。
- 搜狗 /link 跳转必定跳 antispider，所以通常只能拿到标题与时间。
"""
import json
import sqlite3
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from intel_public import (ALBUM_REQUEST_BUDGET, PUBLIC_MIN_INTERVAL, SOGOU_REQUEST_BUDGET,
                          AlbumError, PublicClient, PublicThrottle, SogouBlocked,
                          discover_album_ids)
from intel_store import LIVE_STALE_REASON, STALE_JOB_SECONDS, Intel
from intel_wechat_mp import MAX_BODIES_PER_RUN, RateLimited, article_url, parse_article
from knowledge_store import Knowledge
from server import Store

FIXTURES = Path(__file__).with_name('fixtures')
BIZ = 'MzB5NTkwMDAwMDE=='
OTHER = 'MzB5NTkwMDAwMDI=='
ALBUM = '3501527933040967680'
NAME = '占位公众号'


def load(name):
    return (FIXTURES / name).read_text(encoding='utf-8')


class FakeClock:
    def __init__(self):
        self.value = 1000.0
        self.slept = []

    def now(self):
        return self.value

    def sleep(self, seconds):
        self.slept.append(seconds)
        self.value += seconds


class ThrottleTests(unittest.TestCase):
    def test_public_requests_are_at_least_five_seconds_apart(self):
        clock = FakeClock()
        throttle = PublicThrottle(clock.now, clock.sleep, jitter=lambda a, b: 0.0)
        throttle.wait()
        self.assertEqual(clock.slept, [])
        throttle.wait()
        self.assertGreaterEqual(clock.slept[0], PUBLIC_MIN_INTERVAL)

    def test_jitter_is_added_on_top(self):
        clock = FakeClock()
        throttle = PublicThrottle(clock.now, clock.sleep, jitter=lambda a, b: 3.0)
        throttle.wait(); throttle.wait()
        self.assertGreaterEqual(clock.slept[0], PUBLIC_MIN_INTERVAL + 3.0)

    def test_counting_never_goes_backwards(self):
        throttle = PublicThrottle(FakeClock().now, lambda s: None, jitter=lambda a, b: 0.0)
        for expected in (1, 2, 3):
            throttle.wait()
            self.assertEqual(throttle.calls, expected)


class AlbumDiscoveryTests(unittest.TestCase):
    def test_album_id_is_found_in_the_article_page(self):
        found = discover_album_ids(load('mp_article_with_album.html'))
        self.assertIn(ALBUM, found)

    def test_page_without_album_yields_nothing(self):
        self.assertEqual(discover_album_ids(load('wechat_mp_article.html')), [])
        self.assertEqual(discover_album_ids(''), [])
        self.assertEqual(discover_album_ids(None), [])


class AlbumClientTests(unittest.TestCase):
    def build(self, responses):
        clock = FakeClock()
        self.urls, self.headers = [], []

        def opener(url, headers, timeout):
            self.urls.append(url)
            self.headers.append(headers)
            for marker, payload in responses.items():
                if marker in url:
                    return payload if isinstance(payload, bytes) else payload.encode()
            raise AssertionError(f'未预期的请求：{url}')

        throttle = PublicThrottle(clock.now, clock.sleep, jitter=lambda a, b: 0.0)
        return PublicClient(throttle, opener, album_budget=ALBUM_REQUEST_BUDGET), clock

    def test_album_page_is_parsed_with_chksm_preserved(self):
        client, _ = self.build({'appmsgalbum': load('mp_album_page1.json')})
        page = client.album_page(BIZ, ALBUM)
        self.assertEqual(page['title'], '占位合集')
        self.assertEqual(page['article_count'], 3)
        self.assertTrue(page['continue_flag'])
        self.assertEqual(len(page['items']), 2, '没有链接的条目应被丢掉')
        self.assertIn('chksm=', page['items'][0]['link'])
        self.assertNotIn('#rd', page['items'][0]['link'], '前端片段要去掉')
        self.assertEqual(page['items'][0]['msgid'], '2647685772')

    def test_album_request_carries_biz_and_album_id(self):
        client, _ = self.build({'appmsgalbum': load('mp_album_page1.json')})
        client.album_page(BIZ, ALBUM)
        self.assertIn('__biz=' + BIZ.replace('==', '%3D%3D'), self.urls[0])
        self.assertIn('album_id=' + ALBUM, self.urls[0])

    def test_paging_uses_begin_msgid_and_itemidx(self):
        client, _ = self.build({'appmsgalbum': load('mp_album_page2.json')})
        client.album_page(BIZ, ALBUM, '2647685772', '1')
        self.assertIn('begin_msgid=2647685772', self.urls[0])
        self.assertIn('begin_itemidx=1', self.urls[0])

    def test_album_failure_is_reported_not_retried(self):
        client, _ = self.build({'appmsgalbum': load('mp_album_missing.json')})
        with self.assertRaisesRegex(AlbumError, 'ret=10004'):
            client.album_page(BIZ, ALBUM)
        self.assertEqual(len(self.urls), 1, '不该重试')

    def test_non_json_is_reported_as_album_error(self):
        client, _ = self.build({'appmsgalbum': load('mp_album_notjson.txt')})
        with self.assertRaisesRegex(AlbumError, '不是 JSON'):
            client.album_page(BIZ, ALBUM)

    def test_budget_stops_before_sending(self):
        clock = FakeClock()
        throttle = PublicThrottle(clock.now, clock.sleep, jitter=lambda a, b: 0.0)
        client = PublicClient(throttle, lambda u, h, t: b'{}', album_budget=0, sogou_budget=0)
        with self.assertRaisesRegex(AlbumError, '预算'):
            client.album_page(BIZ, ALBUM)

    def test_no_cookie_is_sent_to_the_public_album_api(self):
        client, _ = self.build({'appmsgalbum': load('mp_album_page1.json')})
        client.album_page(BIZ, ALBUM)
        self.assertNotIn('Cookie', self.headers[0])


class SogouClientTests(unittest.TestCase):
    def build(self, responses):
        clock = FakeClock()

        def opener(url, headers, timeout):
            for marker, payload in responses.items():
                if marker in url:
                    return payload if isinstance(payload, bytes) else payload.encode()
            raise AssertionError(f'未预期的请求：{url}')
        return PublicClient(PublicThrottle(clock.now, clock.sleep, jitter=lambda a, b: 0.0), opener)

    def test_search_parses_account_title_and_time(self):
        client = self.build({'weixin': load('mp_sogou.html')})
        hits = client.sogou_search(NAME)
        self.assertEqual(len(hits), 3)
        self.assertEqual(hits[0]['account'], NAME)
        self.assertEqual(hits[0]['title'], '占位文章一')
        self.assertEqual(hits[0]['published_at'], 1788319876)
        self.assertTrue(hits[0]['sogou_link'].startswith('https://weixin.sogou.com/link?url='))

    def test_results_from_other_accounts_are_present_so_caller_can_filter(self):
        """搜索页会混进他号文章；过滤是调用方的责任，这里如实返回原始结果。"""
        client = self.build({'weixin': load('mp_sogou.html')})
        self.assertEqual({hit['account'] for hit in client.sogou_search(NAME)},
                         {NAME, '马骋观察'})

    def test_antispider_page_raises_immediately(self):
        client = self.build({'weixin': load('mp_sogou_antispider.html')})
        with self.assertRaisesRegex(SogouBlocked, '验证码'):
            client.sogou_search(NAME)

    def test_resolve_extracts_the_real_link_when_it_works(self):
        client = self.build({'/link?': load('mp_sogou_redirect.html')})
        link = client.sogou_resolve('https://weixin.sogou.com/link?url=X')
        self.assertIn('mp.weixin.qq.com/s?__biz=', link)
        self.assertIn('chksm=deadbeef', link)

    def test_resolve_raises_when_antispider_intercepts(self):
        client = self.build({'/link?': load('mp_sogou_antispider.html')})
        with self.assertRaisesRegex(SogouBlocked, '验证码'):
            client.sogou_resolve('https://weixin.sogou.com/link?url=X')


class _FakeArticleClient:
    def __init__(self):
        self.quota = type('Q', (), {'cooldown_until': staticmethod(lambda: 0),
                                    'strike_count': staticmethod(lambda: 0)})()
        self.pages = 0
        self.bodies = []
        self.backend_allowed = False

    def credentials_ready(self):
        return ('stub', '9999999999')

    def search_biz(self, query):
        return []

    def fetch_article(self, url):
        self.bodies.append(url)
        parsed = parse_article(load('mp_article_with_album.html'), url)
        parsed['raw'] = load('mp_article_with_album.html')   # 供发现合集
        return parsed

    def appmsg_list(self, *a, **k):
        self.pages += 1
        if self.backend_allowed:
            return [{'title': '后台文章', 'link': 'https://mp.weixin.qq.com/s/BACKEND0001',
                     'published_at': 1788319000, 'aid': 'b1'}]
        raise AssertionError('三期默认不该走后台')


class _FakePublic:
    """基线：合集空、搜狗空。只在具体测试里显式开启某一路，避免用例之间互相污染。"""

    def __init__(self, hits=None, block_sogou=False):
        self.hits = hits if hits is not None else []
        self.block_sogou = block_sogou
        self.album_calls = self.sogou_calls = 0

    def album_page(self, identifier, album_id, begin_msgid=None, begin_itemidx=None):
        self.album_calls += 1
        return {'title': '占位合集', 'article_count': 3, 'nickname': NAME, 'items': [],
                'continue_flag': False}

    def sogou_search(self, *a, **k):
        self.sogou_calls += 1
        if self.block_sogou:
            from intel_public import SogouBlocked
            raise SogouBlocked('搜狗要求验证码，已停止本轮并进入冷却')
        return self.hits

    def sogou_resolve(self, *a, **k):
        return ''


class StoreTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(self.tmp.name)
        self.store._knowledge = Knowledge(self.store, sources=[])
        self.public = _FakePublic()
        self.article_client = _FakeArticleClient()
        self.intel = Intel(self.store, client_factory=lambda s: self.article_client,
                           public_factory=lambda s: self.public)
        self.intel.subscribe({'fakeid': BIZ, 'name': NAME})

    def tearDown(self):
        self.release()
        for worker in list(self.intel.workers.values()):
            worker.join(5)
        self.tmp.cleanup()

    def release(self):
        self.release_called = True

    def seed_album(self):
        self.intel.remember_albums(BIZ, [ALBUM])

    def go(self, **payload):
        payload.setdefault('fakeid', BIZ)
        payload.setdefault('mode', 'history')
        return self.intel.wait_for(self.intel.run(payload)['id'], timeout=5)


class AlbumPathTests(StoreTestCase):
    def test_album_items_are_stored_with_their_source(self):
        self.seed_album()
        self.public.album_page = lambda *a, **k: {
            'title': '占位合集', 'article_count': 2, 'nickname': NAME, 'continue_flag': False,
            'items': [{'title': '合集一', 'link': 'https://mp.weixin.qq.com/s/AAAAAAAAAAAA?chksm=x',
                       'published_at': 1788319876, 'msgid': '1', 'itemidx': '1', 'source': 'album'}]}
        done = self.go()
        row = next(r for r in self.intel.articles(BIZ)['articles'] if r['title'] == '合集一')
        self.assertEqual(row['source'], 'album')
        self.assertIn('chksm=x', row['link'], 'chksm 必须保留')
        self.assertIn('合集', done['detail'])
        self.assertIn('album 1 篇', done['detail'])

    def test_album_title_and_count_are_recorded(self):
        self.seed_album()
        self.go()
        albums = self.intel.albums(BIZ)
        self.assertEqual(albums[0]['album_id'], ALBUM)
        self.assertEqual(albums[0]['title'], '占位合集')

    def test_remembering_the_same_album_twice_does_not_duplicate(self):
        self.intel.remember_albums(BIZ, [ALBUM])
        self.intel.remember_albums(BIZ, [ALBUM, ALBUM])
        self.assertEqual(len(self.intel.albums(BIZ)), 1)

    def test_account_without_album_says_so_in_the_receipt(self):
        done = self.go()
        self.assertIn('合集', done['detail'])
        self.assertIn('本地没有该号的完整文章链接，无法发现合集', done['detail'])
        self.assertEqual(self.public.album_calls, 0, '没有合集就不该发请求')

    def test_album_is_discovered_from_a_known_article_page(self):
        link = f'https://mp.weixin.qq.com/s?__biz={BIZ}&mid=123&chksm=fixture'
        with self.intel.db() as c:
            c.execute("INSERT INTO link_index(link,fakeid,status,resolved_at) VALUES(?,?,?,?)", (link,BIZ,'resolved','2026'))
        self.go()
        self.assertEqual([a['album_id'] for a in self.intel.albums(BIZ)], [ALBUM])
        self.assertTrue(self.article_client.bodies)

    def test_history_is_capped_at_thirty(self):
        """合集有 99 篇，但历史仍只取 ≤30。"""
        self.seed_album()
        counter = {'n': 0}

        def page(*a, **k):
            counter['n'] += 1
            items = [{'title': f'第{counter["n"]}页{i}', 'published_at': 1788319876 - counter['n'] * 100 - i,
                      'link': f'https://mp.weixin.qq.com/s/L{counter["n"]:03d}{i:03d}', 'msgid': str(counter['n']),
                      'itemidx': str(i), 'source': 'album'} for i in range(10)]
            return {'title': '占位合集', 'article_count': 99, 'nickname': NAME,
                    'items': items, 'continue_flag': True}
        self.public.album_page = page
        self.go()
        self.assertEqual(self.intel.articles(BIZ, limit=300)['total'], 30)

    def test_request_budget_stops_paging_and_says_so(self):
        """一直有下一页时，请求预算必须封顶并在回执写明。"""
        self.seed_album()
        page_number = {'n': 0}

        def page(*a, **k):
            page_number['n'] += 1
            items = [{'title': f'第{page_number["n"]}页{i}', 'published_at': 1788319876 - page_number['n'] * 100 - i,
                      'link': f'https://mp.weixin.qq.com/s/PG{page_number["n"]:03d}X{i:03d}',
                      'msgid': str(page_number['n']), 'itemidx': str(i), 'source': 'album'} for i in range(2)]
            return {'title': '占位合集', 'article_count': 999, 'nickname': NAME,
                    'items': items, 'continue_flag': True}
        self.public.album_page = page
        result = self.intel.album_items(BIZ, wanted=999, max_requests=3)
        self.assertEqual(page_number['n'], 3)
        self.assertIn('上限', result['stopped'])
        self.assertEqual(len(result['items']), 6)

    def test_album_budget_is_never_exceeded_by_a_run(self):
        self.seed_album()
        self.public.album_page = lambda *a, **k: {
            'title': '占位合集', 'article_count': 999, 'nickname': NAME, 'continue_flag': True, 'items': []}
        self.go(mode='history')
        self.assertLessEqual(self.public.album_calls, ALBUM_REQUEST_BUDGET)


class SogouPathTests(StoreTestCase):
    HITS = [{'account': NAME, 'title': '搜狗标题', 'published_at': 1788319876, 'sogou_link': 'x'},
            {'account': '别人的号', 'title': '他号文章', 'published_at': 1, 'sogou_link': 'y'}]

    def with_hits(self, hits=None, block=False):
        self.public.hits = self.HITS if hits is None else hits
        self.public.block_sogou = block
        return self.public

    def test_only_matching_accounts_are_kept(self):
        self.with_hits()
        done = self.go(sogou=True)
        titles = [row['title'] for row in self.intel.articles(BIZ)['articles']]
        self.assertNotIn('他号文章', titles)
        self.assertIn('2 条结果里 1 条确属本号', done['detail'])

    def test_sogou_title_without_a_link_is_marked_not_invented(self):
        self.with_hits()
        self.go(sogou=True)
        row = next(r for r in self.intel.articles(BIZ)['articles'] if r['title'] == '搜狗标题')
        self.assertEqual(row['link'], '')
        self.assertEqual(row['link_ok'], 0)
        self.assertEqual(row['source'], 'sogou')
        self.assertEqual(row['link_missing_reason'], 'sogou_title_only')
        detail = self.intel.article(row['id'])
        self.assertEqual(detail['link'], '')
        self.assertEqual(detail['link_missing_reason'], 'sogou_title_only')

    def test_captcha_stops_the_round_and_opens_a_six_hour_cooldown(self):
        self.with_hits(block=True)
        done = self.go(sogou=True)
        self.assertIn('搜狗', done['detail'])
        self.assertIn('冷却', done['detail'])
        self.assertGreater(self.intel.sogou_cooldown_until(), time.time() + 5 * 3600)

    def test_cooling_sogou_is_skipped_next_round_without_requesting(self):
        self.with_hits(block=True)
        self.go(sogou=True)
        before = self.public.sogou_calls
        done = self.go(sogou=True)
        self.assertEqual(self.public.sogou_calls, before, '冷却期间不该再请求搜狗')
        self.assertIn('冷却', done['detail'])

    def test_turning_sogou_off_is_respected(self):
        self.with_hits()
        done = self.go(sogou=False)
        self.assertEqual(self.public.sogou_calls, 0)
        self.assertIn('搜狗：已关闭', done['detail'])

    def test_sogou_budget_is_never_exceeded(self):
        self.with_hits([{'account': NAME, 'title': f'T{i}', 'published_at': 1788319876 - i,
                          'sogou_link': 'x'} for i in range(10)])
        done = self.go(sogou=True)
        self.assertLessEqual(self.public.sogou_calls, SOGOU_REQUEST_BUDGET)
        self.assertIn('上限', done['detail'])


class MergeTests(StoreTestCase):
    def test_same_article_from_two_paths_is_stored_once(self):
        self.seed_album()
        link = 'https://mp.weixin.qq.com/s/SHAREDARTICLE?chksm=x'
        self.public.album_page = lambda *a, **k: {
            'title': '占位合集', 'article_count': 1, 'nickname': NAME, 'continue_flag': False,
            'items': [{'title': '同一篇', 'link': link, 'published_at': 1788319876,
                       'msgid': '1', 'itemidx': '1', 'source': 'album'}]}
        self.public.sogou_result = {'items': [{'title': '同一篇', 'link': link, 'published_at': 1788319876,
                                                'source': 'sogou', 'aid': '', 'link_ok': True, 'album_id': ''}],
                                    'requests': 1, 'blocked': False, 'stopped': '', 'scanned': 1, 'matched': 1}
        self.go()
        self.assertEqual(self.intel.articles(BIZ)['total'], 1)

    def test_merged_list_is_sorted_by_time_and_capped(self):
        self.seed_album()
        items = [{'title': f'文章{i}', 'published_at': 1700000000 + i,
                  'link': f'https://mp.weixin.qq.com/s/MERGE{i:04d}', 'msgid': str(i), 'itemidx': '1',
                  'source': 'album'} for i in range(40)]
        self.public.album_page = lambda *a, **k: {
            'title': '占位合集', 'article_count': 40, 'nickname': NAME, 'items': items, 'continue_flag': False}
        self.go(mode='latest')
        rows = self.intel.articles(BIZ, limit=300)['articles']
        self.assertLessEqual(len(rows), 20)
        self.assertEqual(rows[0]['published_at'], max(r['published_at'] for r in rows))

    def test_bodies_are_only_fetched_for_articles_that_have_a_link(self):
        self.seed_album()
        self.public.album_page = lambda *a, **k: {
            'title': '占位合集', 'article_count': 1, 'nickname': NAME, 'continue_flag': False,
            'items': [{'title': '有链接', 'link': 'https://mp.weixin.qq.com/s/AAAAAAAAAAAA',
                       'published_at': 1, 'msgid': '1', 'itemidx': '1', 'source': 'album'}]}
        self.public.hits = [{'account': NAME, 'title': '只有标题', 'published_at': 2, 'sogou_link': 'x'}]
        self.go(bodies=3, sogou=True)
        self.assertEqual(self.article_client.bodies, ['https://mp.weixin.qq.com/s/AAAAAAAAAAAA'])
        rows = {r['title']: r for r in self.intel.articles(BIZ)['articles']}
        self.assertEqual(rows['有链接']['body_status'], 'awaiting_confirm')
        self.assertEqual(rows['只有标题']['body_status'], 'pending')


class LinklessArticleTests(StoreTestCase):
    """搜狗只拿得到标题时的入库行为。这里曾经有一个会崩掉整轮的 bug。"""

    def test_many_linkless_articles_do_not_violate_the_unique_link_constraint(self):
        """articles.link 上有 UNIQUE；两条无链接记录若都存空串就会撞约束崩掉整轮。"""
        total = 0
        for offset in range(3):          # 多轮累积，逼出「多条无链接」这个场景
            self.public.hits = [{'account': NAME, 'title': f'只有标题{offset}-{i}',
                                 'published_at': 1788319876 - offset * 10 - i, 'sogou_link': 'x'}
                                for i in range(4)]
            done = self.go(sogou=True)
            self.assertEqual(done['status'], 'done')
            total = self.intel.articles(BIZ)['total']
        self.assertGreaterEqual(total, 6, '多轮下来应累积多条无链接文章')
        rows = [r for r in self.intel.articles(BIZ)['articles'] if r['link'] == '']
        self.assertGreaterEqual(len(rows), 6)
        for row in rows:
            self.assertEqual(row['link_ok'], 0)
            self.assertEqual(row['source'], 'sogou')

    def test_old_linkless_row_without_a_path_has_no_specific_reason(self):
        """source 为空的旧行分不清是搜狗还是不合格链接，对外不猜原因。"""
        article_id = 'c' * 32
        with self.intel.db() as connection:
            connection.execute(
                '''INSERT INTO articles(id,fakeid,aid,link,title,published_at,added_at,source,link_ok)
                   VALUES(?,?,?,?,?,?,?,?,?)''',
                (article_id, BIZ, 'old', f'sogou-no-link:{BIZ}:old', '旧数据', 1,
                 '2020-01-01T00:00:00+00:00', '', 0))
        row = next(item for item in self.intel.articles(BIZ)['articles'] if item['id'] == article_id)
        self.assertEqual(row['link'], '')
        self.assertEqual(row['link_ok'], 0)
        self.assertNotIn('link_missing_reason', row)
        detail = self.intel.article(article_id)
        self.assertEqual(detail['link'], '')
        self.assertNotIn('link_missing_reason', detail)

    def test_linkless_placeholder_is_not_accepted_as_a_real_link(self):
        self.public.hits = [{'account': NAME, 'title': '只有标题', 'published_at': 1, 'sogou_link': 'x'}]
        self.go(sogou=True)
        with self.intel.db() as connection:
            stored = connection.execute(
                "SELECT link FROM articles WHERE link LIKE 'sogou-no-link:%'").fetchall()
        self.assertEqual(len(stored), 1)
        with self.assertRaisesRegex(ValueError, '文章链接'):
            article_url(stored[0]['link'])

    def test_the_same_title_is_not_stored_twice(self):
        self.public.hits = [{'account': NAME, 'title': '同一篇标题', 'published_at': 1788319876, 'sogou_link': 'x'}]
        self.go(sogou=True)
        self.assertEqual(self.intel.articles(BIZ)['total'], 1)
        self.go(sogou=True)
        self.assertEqual(self.intel.articles(BIZ)['total'], 1)


class OutcomeStageTests(StoreTestCase):
    """prep §2/§3：保存与正文两段的真实计数，以及异常中止的未处理数。"""

    def _two_album_items(self):
        self.public.album_page = lambda *a, **k: {
            'title': '占位合集', 'article_count': 2, 'nickname': NAME, 'continue_flag': False,
            'items': [{'title': '正文甲', 'link': 'https://mp.weixin.qq.com/s/BODY000001',
                       'published_at': 3, 'msgid': '1', 'itemidx': '1', 'source': 'album'},
                      {'title': '正文乙', 'link': 'https://mp.weixin.qq.com/s/BODY000002',
                       'published_at': 2, 'msgid': '2', 'itemidx': '1', 'source': 'album'}]}

    def test_body_partial_failure_reports_partial(self):
        self.seed_album()
        self._two_album_items()
        calls = {'n': 0}
        original = self.article_client.fetch_article

        def flaky(url):
            calls['n'] += 1
            if calls['n'] == 1:
                raise ValueError('正文页异常')
            return original(url)

        self.article_client.fetch_article = flaky
        done = self.go(bodies=3)
        self.assertEqual(done['status'], 'partial')
        body = done['outcome']['body']
        self.assertEqual(body['state'], 'partial')
        self.assertEqual(body['attempted'], 2)
        self.assertEqual(body['succeeded'], 1)
        self.assertEqual(body['failed'], 1)

    def test_history_probe_and_body_share_budget(self):
        """抓历史时探测读取与正文共用文章页总预算：effective=MAX_BODIES_PER_RUN-探测。"""
        link = f'https://mp.weixin.qq.com/s?__biz={BIZ}&mid=123&chksm=fixture'
        with self.intel.db() as c:
            c.execute("INSERT INTO link_index(link,fakeid,status,resolved_at) VALUES(?,?,?,?)",
                      (link, BIZ, 'resolved', '2026'))
        done = self.go(bodies=3)
        self.assertIn('探测读取 1 篇', done['detail'])
        self.assertEqual(done['outcome']['body']['effective_budget'], MAX_BODIES_PER_RUN - 1)
        self.assertEqual(done['outcome']['body']['requested_budget'], 3)

    def test_zero_body_budget_is_not_requested(self):
        self.seed_album()
        done = self.go(bodies=0)
        self.assertEqual(done['outcome']['body']['state'], 'not_requested')
        self.assertEqual(done['outcome']['body']['requested_budget'], 0)
        self.assertEqual(self.article_client.bodies, [])

    def test_missing_link_placeholder_is_saved_not_failed(self):
        """无链接的搜狗条目存成占位，是保存成功，不算保存失败。"""
        self.public.hits = [{'account': NAME, 'title': '只有标题', 'published_at': 1, 'sogou_link': 'x'}]
        done = self.go(sogou=True)
        save = done['outcome']['save']
        self.assertEqual(save['failed'], 0)
        self.assertEqual(save['succeeded'], 1)
        self.assertEqual(save['new'], 1)
        row = next(r for r in self.intel.articles(BIZ)['articles'] if r['title'] == '只有标题')
        self.assertEqual(row['link_ok'], 0)
        self.assertEqual(done['status'], 'done')

    def test_unexpected_abort_counts_not_attempted(self):
        """首条保存抛未预期异常：记录 attempted/not_attempted，已提交成果保留，终态 error。"""
        self.seed_album()
        self.public.album_page = lambda *a, **k: {
            'title': '占位合集', 'article_count': 3, 'nickname': NAME, 'continue_flag': False,
            'items': [{'title': f'中止{i}', 'link': f'https://mp.weixin.qq.com/s/ABORT0000{i}',
                       'published_at': 3 - i, 'msgid': str(i), 'itemidx': '1', 'source': 'album'}
                      for i in range(1, 4)]}

        def boom(*a, **k):
            raise RuntimeError('落库炸了')

        self.intel._upsert_article = boom
        done = self.go()
        self.assertEqual(done['status'], 'error')
        save = done['outcome']['save']
        self.assertEqual(save['succeeded'], 0)
        self.assertEqual(save['attempted'], 1)
        self.assertEqual(save['not_attempted'], 2)
        self.assertEqual(done['outcome']['list']['state'], 'complete')

    def _three_album_items(self):
        self.public.album_page = lambda *a, **k: {
            'title': '占位合集', 'article_count': 3, 'nickname': NAME, 'continue_flag': False,
            'items': [{'title': f'条目{i}', 'link': f'https://mp.weixin.qq.com/s/SAVE0000{i}',
                       'published_at': 3 - i, 'msgid': str(i), 'itemidx': '1', 'source': 'album'}
                      for i in range(1, 4)]}

    def test_second_save_runtime_error_counts_failure_and_body_not_started(self):
        """M2 探针3：第二条保存抛 RuntimeError → 记一次 failed（attempted=succeeded+failed），
        正文未开始，不能冒充「无需读取」或「完成」。"""
        self.seed_album()
        self._three_album_items()
        original = self.intel._upsert_article
        calls = {'n': 0}

        def flaky(identifier, item, job_id, connection=None):
            calls['n'] += 1
            if calls['n'] == 2:
                raise RuntimeError('第二条炸了')
            return original(identifier, item, job_id, connection)

        self.intel._upsert_article = flaky
        done = self.go(bodies=3)
        self.assertEqual(done['status'], 'partial')
        save = done['outcome']['save']
        self.assertEqual(save['attempted'], 2)
        self.assertEqual(save['succeeded'], 1)
        self.assertEqual(save['failed'], 1, '未预期的保存异常也要记一次 failed')
        self.assertEqual(save['not_attempted'], 1)
        self.assertEqual(save['attempted'], save['succeeded'] + save['failed'])
        self.assertEqual(done['outcome']['list']['selected'],
                         save['attempted'] + save['not_attempted'])
        body = done['outcome']['body']
        self.assertEqual(body['state'], 'not_started')
        self.assertNotIn('正文无需读取', done['outcome']['summary'])
        self.assertNotIn('待确认', done['outcome']['summary'])
        self.assertIn('正文未开始', done['outcome']['summary'])
        self.assertEqual(self.intel.articles(BIZ)['total'], 1, '已提交的第一条要保留')

    def test_body_rate_limit_reports_remaining_range(self):
        """M3 探针4：正文首条限流停止后，仍要统计剩余可读未读（不能因 break 记 0），
        停止的这次尝试规范地计入失败；外网失败不算预算跳过。"""
        self.seed_album()
        self._three_album_items()
        self.article_client.fetch_article = lambda url: (_ for _ in ()).throw(
            RateLimited('正文页限流（200013）', until=0))
        done = self.go(bodies=3)
        self.assertEqual(done['status'], 'partial')
        body = done['outcome']['body']
        self.assertEqual(body['state'], 'failed')
        self.assertEqual(body['attempted'], 1)
        self.assertEqual(body['succeeded'], 0)
        self.assertEqual(body['failed'], 1)
        self.assertTrue(body['stopped'])
        self.assertEqual(body['eligible_remaining'], 2, '限流后仍要统计剩余可读未读')
        self.assertEqual(body['skipped_budget'], 0, '外网失败不算预算跳过')
        self.assertIn('另有 2 篇未读', done['outcome']['summary'])
        self.assertNotIn('正文无需读取', done['outcome']['summary'])

    def test_body_budget_skip_is_recorded_in_skipped_budget(self):
        """M3 探针5：正常预算 bodies=1、3 条可读 pending → 选 1 篇，另 2 篇记预算内未选中。"""
        self.seed_album()
        self._three_album_items()
        done = self.go(bodies=1)
        self.assertEqual(done['status'], 'done')
        body = done['outcome']['body']
        self.assertEqual(body['state'], 'complete')
        self.assertEqual(body['attempted'], 1)
        self.assertEqual(body['succeeded'], 1)
        self.assertEqual(body['skipped_budget'], 2, '预算内未选中的条目要记 skipped_budget')
        self.assertEqual(body['eligible_remaining'], 2)
        self.assertIn('另有 2 篇未读', done['outcome']['summary'])


class UnexpectedErrorTests(StoreTestCase):
    def test_worker_never_leaves_a_job_stuck_in_running(self):
        """工作线程里的意外异常绝不能让任务永远停在 running。

        合集请求抛未预期异常、整轮中止、没有任何一路查询成功：按 prep §3 行1，
        列表阶段 failed → 终态 error（不再只允许 done/partial），且不挂 active。
        """

        def boom(*a, **k):
            raise RuntimeError('合成故障')
        self.public.album_page = boom
        self.seed_album()
        done = self.go()
        self.assertEqual(done['status'], 'error')
        self.assertEqual(done['outcome']['list']['state'], 'failed')
        self.assertIn('未预期', done['detail'])
        self.assertNotIn(done['status'], ('running', 'queued'))
        self.assertIsNone(self.intel.active_job())

    def test_a_failed_finish_marks_the_job_error_and_releases_the_worker(self):
        original = self.intel._finish_job

        def flaky(job_id, status, reason='', **counts):
            if status != 'error':
                raise sqlite3.OperationalError('合成写回失败')
            return original(job_id, status, reason, **counts)

        self.intel._finish_job = flaky
        done = self.go()
        self.assertEqual(done['status'], 'error')
        self.assertIn('写回失败', done['reason'])
        self.assertNotIn(done['id'], self.intel.workers)

    def test_a_detail_write_failure_keeps_the_finished_status(self):
        """主回执（status/detail/outcome/counts）已同事务原子落库；
        事后的辅助详情补写失败不能把终态改成 error（prep §6 要求改的旧断言）。"""
        def boom(job_id, detail, **kwargs):
            raise sqlite3.OperationalError('合成详情失败')

        self.intel._write_job_detail = boom
        done = self.go()
        self.assertIn(done['status'], ('done', 'partial'))
        self.assertNotEqual(done['status'], 'error')
        self.assertTrue(done['detail'], '主回执应已原子写入，不依赖辅助补写')
        self.assertIsNotNone(done['outcome'])
        self.assertNotIn(done['id'], self.intel.workers)

    def test_a_live_process_interrupts_only_expired_running_jobs(self):
        old_id, fresh_id = 'a' * 32, 'b' * 32
        old = (datetime.now(timezone.utc) - timedelta(seconds=STALE_JOB_SECONDS + 30)).isoformat()
        fresh = datetime.now(timezone.utc).isoformat()
        with self.intel.db() as connection:
            connection.execute(
                'INSERT INTO jobs(id,fakeid,name,mode,status,started_at) VALUES(?,?,?,?,?,?)',
                (old_id, BIZ, NAME, 'latest', 'running', old))
            connection.execute(
                'INSERT INTO jobs(id,fakeid,name,mode,status,started_at) VALUES(?,?,?,?,?,?)',
                (fresh_id, BIZ, NAME, 'latest', 'running', fresh))
        self.assertTrue(self.intel._ready)
        found = self.intel.active_job()
        self.assertEqual(found['id'], fresh_id)
        self.assertEqual(found['status'], 'running')
        expired = self.intel.job(old_id)
        self.assertEqual(expired['status'], 'interrupted')
        self.assertEqual(expired['reason'], LIVE_STALE_REASON)
        other = Intel(self.store, client_factory=lambda store: self.article_client,
                      public_factory=lambda store: self.public)
        again = other.active_job()
        self.assertEqual(again['id'], fresh_id)
        self.assertEqual(other.job(fresh_id)['status'], 'running')


class RealWorldShapeTests(StoreTestCase):
    """按实测的真实响应形状写的测试 —— 这些坑都是冒烟时真踩到的。"""

    def test_album_http_links_are_normalized_to_https(self):
        """合集接口实测返回 http:// 链接。早期只认 https，导致整轮 0 入库。"""
        http_link = ('http://mp.weixin.qq.com/s?__biz=MzAwMDAwMDAwMDE%3D%3D&mid=2647685772'
                     '&idx=1&sn=abc&chksm=deadbeef')
        self.assertTrue(article_url(http_link).startswith('https://'))
        self.seed_album()
        self.public.album_page = lambda *a, **k: {
            'title': '占位合集', 'article_count': 1, 'nickname': NAME, 'continue_flag': False,
            'items': [{'title': '升到 https', 'link': http_link, 'published_at': 3,
                       'msgid': '1', 'itemidx': '1', 'source': 'album'}]}
        self.go()
        row = next(item for item in self.intel.articles(BIZ)['articles'] if item['title'] == '升到 https')
        self.assertTrue(row['link'].startswith('https://mp.weixin.qq.com/s?'))
        self.assertIn('chksm=deadbeef', row['link'])
        self.assertEqual(row['link_ok'], 1)

    def test_a_single_bad_link_does_not_sink_the_whole_round(self):
        self.seed_album()
        self.public.album_page = lambda *a, **k: {
            'title': '占位合集', 'article_count': 3, 'nickname': NAME, 'continue_flag': False,
            'items': [{'title': '好链接', 'link': 'https://mp.weixin.qq.com/s/GOODLINK000',
                       'published_at': 3, 'msgid': '1', 'itemidx': '1', 'source': 'album'},
                      {'title': '坏链接', 'link': 'not-a-wechat-link', 'published_at': 2,
                       'msgid': '2', 'itemidx': '1', 'source': 'album'},
                      {'title': '脚本链接', 'link': 'javascript:alert(1)', 'published_at': 1,
                       'msgid': '3', 'itemidx': '1', 'source': 'album'}]}
        done = self.go()
        rows = {item['title']: item for item in self.intel.articles(BIZ)['articles']}
        self.assertEqual(rows['好链接']['link'], 'https://mp.weixin.qq.com/s/GOODLINK000')
        self.assertEqual(rows['好链接']['link_ok'], 1)
        self.assertNotIn('link_missing_reason', rows['好链接'])
        for title in ('坏链接', '脚本链接'):
            self.assertEqual(rows[title]['link'], '', title)
            self.assertEqual(rows[title]['link_ok'], 0, title)
            self.assertEqual(rows[title]['link_missing_reason'], 'invalid_link', title)
            self.assertEqual(rows[title]['source'], 'album', title)
        self.assertEqual(done['status'], 'done')
        self.assertNotIn('已跳过', done['detail'])
        with self.intel.db() as connection:
            stored = connection.execute("SELECT link FROM articles WHERE title='脚本链接'").fetchone()
        self.assertTrue(stored['link'].startswith('sogou-no-link:'))

    def test_album_article_count_is_not_overwritten_by_zero(self):
        self.seed_album()
        with self.intel.db() as connection:
            connection.execute("UPDATE albums SET article_count=77 WHERE fakeid=? AND album_id=?",
                               (BIZ, ALBUM))
        self.public.album_page = lambda *a, **k: {
            'title': '', 'article_count': 0, 'nickname': NAME, 'items': [], 'continue_flag': False}
        self.go()
        self.assertEqual(self.intel.albums(BIZ)[0]['article_count'], 77)


class BackendToggleTests(StoreTestCase):
    def test_backend_is_off_by_default(self):
        done = self.go()
        self.assertEqual(self.article_client.pages, 0)
        self.assertIn('后台：已关闭', done['detail'])

    def test_backend_can_be_turned_on(self):
        self.article_client.backend_allowed = True
        done = self.go(backend=True, mode='latest')
        self.assertEqual(self.article_client.pages, 1)
        self.assertIn('后台', done['detail'])
        self.assertEqual(self.intel.articles(BIZ)['articles'][0]['source'], 'backend')

    def test_switches_must_be_boolean(self):
        with self.assertRaisesRegex(ValueError, '开关'):
            self.intel.run({'fakeid': BIZ, 'mode': 'latest', 'backend': 'yes'})

    def test_receipt_names_every_path(self):
        self.seed_album()
        self.article_client.backend_allowed = True
        done = self.go(backend=True, sogou=True)
        for path in ('合集', '搜狗', '后台'):
            self.assertIn(path, done['detail'])


class LinkPreservationTests(unittest.TestCase):
    def test_chksm_is_never_stripped(self):
        link = ('https://mp.weixin.qq.com/s?__biz=MzAwMDAwMDAwMDE%3D%3D&mid=2647685772&idx=1'
                '&sn=a073c30899a7f420147673def8209609&chksm=f007a6dbc7702fcd5a79bfc8f5efe9c78')
        self.assertEqual(article_url(link), link)

    def test_album_style_link_is_accepted_by_the_fetcher(self):
        link = ('https://mp.weixin.qq.com/s?__biz=MzAwMDAwMDAwMDE%3D%3D&mid=2647685772&idx=1'
                '&sn=abc&chksm=deadbeef')
        parsed = parse_article(load('mp_article_with_album.html'), link)
        self.assertEqual(parsed['biz'], BIZ)


if __name__ == '__main__':
    unittest.main()