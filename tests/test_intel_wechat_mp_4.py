"""四期：临时业务库 + 文章夹具，所有 socket 连接封死。"""
import sqlite3
import socket
from unittest.mock import patch
from intel_wechat_mp import MAX_BODIES_PER_RUN
from tests.test_intel_wechat_mp_3 import StoreTestCase, BIZ, OTHER, ALBUM, NAME

LINK = f'https://mp.weixin.qq.com/s?__biz={BIZ}&mid=123&idx=1&sn=fixture&chksm=fixture'

class ColdStartTests(StoreTestCase):
    def setUp(self):
        super().setUp()
        self.socket_guard = patch.object(socket.socket, 'connect', side_effect=AssertionError('禁止出网'))
        self.socket_guard.start()
        self.addCleanup(self.socket_guard.stop)

    def index(self, link):
        with self.intel.db() as c:
            c.execute('INSERT INTO link_index(link,fakeid,nickname,status,detail,source_kind,resolved_at) VALUES(?,?,?,\'resolved\',\'\',\'fixture\',\'2026\')', (link,BIZ,'夹具'))

    def knowledge(self, link):
        path = self.store.workspace / 'knowledge' / 'knowledge.db'
        path.parent.mkdir(exist_ok=True)
        with sqlite3.connect(path) as c:
            c.execute('CREATE TABLE IF NOT EXISTS documents(url TEXT, modified TEXT)')
            c.execute("INSERT INTO documents(id,source,kind,title,body,url,modified,tags,hash) VALUES(?, 'fixture', 'web', '', '', ?, '2026', '[]', '')", (link,link))

    def test_knowledge_cold_start_reaches_album(self):
        self.knowledge(LINK)
        job = self.go(bodies=0, sogou=False)
        self.assertEqual(self.article_client.bodies, [LINK])
        self.assertEqual(self.public.album_calls, 1)
        self.assertEqual(self.intel.albums(BIZ)[0]['album_id'], ALBUM)

    def test_priority_dedup_and_two_link_budget(self):
        self.index(LINK)
        self.knowledge(LINK)
        second = LINK.replace('mid=123','mid=124')
        with self.intel.db() as c:
            c.execute('INSERT INTO recommendation_sources VALUES(?,?,?,?,?,?)', (BIZ,'fixture','r',second,'','2026'))
        self.assertEqual(self.intel.local_album_links(BIZ), [LINK,second])
        third = LINK.replace('mid=123','mid=125')
        def empty(url):
            self.article_client.bodies.append(url)
            return {'biz': BIZ, 'raw': ''}
        self.article_client.fetch_article = empty
        self.intel.discover_albums_from_links(BIZ, [LINK,second,third])
        self.assertEqual(self.article_client.bodies,[LINK,second])

    def test_missing_checksum_wrong_account_and_no_link_receipt(self):
        for link in [LINK.split('&chksm')[0],LINK.replace(BIZ,OTHER),'https://example.com/']:
            self.index(link)
        job = self.go(bodies=0,sogou=False)
        self.assertEqual(self.article_client.bodies, [])
        self.assertIn('本地没有该号的完整文章链接，无法发现合集；可在回收站收下一篇该号文章后重试',job['detail'])

    def test_discovery_records_second_successful_source(self):
        second = LINK.replace('mid=123','mid=124')
        original = self.article_client.fetch_article
        def fetch(url):
            if url == LINK: raise ValueError('夹具失败')
            return original(url)
        self.article_client.fetch_article = fetch
        self.intel.discover_albums_from_links(BIZ,[LINK,second])
        self.assertEqual(self.intel.albums(BIZ)[0]['found_from'],second[:120])

    def test_known_album_skips_article_discovery(self):
        self.index(LINK); self.seed_album()
        self.go(bodies=0,sogou=False)
        self.assertEqual(self.article_client.bodies,[])

    def test_cooldown_no_request_and_no_extension(self):
        self.intel.set_sogou_cooldown()
        before = self.intel.sogou_cooldown_until()
        job = self.go(bodies=0)
        self.assertEqual(self.public.sogou_calls,0)
        self.assertEqual(self.intel.sogou_cooldown_until(),before)
        self.assertIn('搜狗冷却中，本轮跳过',job['detail'])

    def test_manual_album_does_not_fetch_and_article_discovers(self):
        result = self.intel.identify_from_link(f'https://mp.weixin.qq.com/mp/appmsgalbum?__biz={BIZ}&album_id={ALBUM}')
        self.assertEqual(result['albums_found'],1)
        self.assertEqual(self.article_client.bodies,[])
        result = self.intel.identify_from_link(LINK)
        self.assertEqual(result['fakeid'],BIZ)
        self.assertEqual(len(self.intel.albums(BIZ)),1)

    def test_malformed_album_is_rejected(self):
        for url in [f'https://example.com/mp/appmsgalbum?__biz={BIZ}&album_id={ALBUM}',f'https://mp.weixin.qq.com/mp/appmsgalbum?__biz={BIZ}&album_id=bad']:
            with self.assertRaises(ValueError): self.intel.identify_from_link(url)
        self.assertEqual(self.article_client.bodies,[])

    def test_legacy_article_columns_are_migrated_without_data_loss(self):
        with self.intel.db() as c:
            c.execute('ALTER TABLE articles DROP COLUMN source')
            c.execute('ALTER TABLE articles DROP COLUMN link_ok')
        self.intel._ready = False
        with self.intel.db() as c:
            columns = {r[1] for r in c.execute('PRAGMA table_info(articles)')}
        self.assertTrue({'source','link_ok'} <= columns)


class HistoryReadBudgetTests(ColdStartTests):
    """抓历史的文章页总预算含冷启动探测。失败的读取也占名额。"""

    def plant_pair(self):
        second = LINK.replace('mid=123', 'mid=124')
        with self.intel.db() as c:
            c.execute("INSERT INTO link_index(link,fakeid,nickname,status,detail,source_kind,resolved_at) VALUES(?,?,?,'resolved','','fixture','2026-02')", (LINK, BIZ, '夹具'))
            c.execute("INSERT INTO link_index(link,fakeid,nickname,status,detail,source_kind,resolved_at) VALUES(?,?,?,'resolved','','fixture','2026-01')", (second, BIZ, '夹具'))
        return second

    def three_bodies(self):
        items = [{'title': f'正文{i}', 'link': f'https://mp.weixin.qq.com/s/BODYAAA0000{i}',
                  'published_at': 40 - i, 'msgid': str(i), 'itemidx': '1', 'source': 'album'}
                 for i in range(1, 4)]
        self.public.album_page = lambda *a, **k: {
            'title': '占位合集', 'article_count': 3, 'nickname': NAME, 'items': items, 'continue_flag': False}
        return [item['link'] for item in items]

    def miss_then_hit(self, first):
        """第一篇探测不带合集，后面的文章页走原来的夹具。"""
        original = self.article_client.fetch_article

        def fetch(url):
            if url == first:
                self.article_client.bodies.append(url)
                return {'biz': BIZ, 'raw': '', 'text': '探测', 'chars': 2, 'images': [], 'nickname': NAME}
            return original(url)

        self.article_client.fetch_article = fetch

    def test_history_cold_start_counts_probe_pages_inside_the_budget(self):
        second = self.plant_pair()
        bodies = self.three_bodies()
        self.miss_then_hit(LINK)
        job = self.go(mode='history', bodies=MAX_BODIES_PER_RUN, sogou=False)
        self.assertEqual(self.article_client.bodies, [LINK, second, bodies[0]])
        self.assertLessEqual(len(self.article_client.bodies), MAX_BODIES_PER_RUN)
        self.assertIn('探测读取 2 篇', job['detail'])
        self.assertIn('正文读取 1 篇', job['detail'])
        self.assertIn('正文 1 篇待确认', job['detail'])
        self.assertEqual(job['bodies'], 1)

    def test_known_album_history_keeps_the_full_body_budget(self):
        self.seed_album()
        self.index(LINK)
        bodies = self.three_bodies()
        job = self.go(mode='history', bodies=MAX_BODIES_PER_RUN, sogou=False)
        self.assertEqual(self.article_client.bodies, bodies)
        self.assertIn('探测读取 0 篇', job['detail'])
        self.assertIn('正文读取 3 篇', job['detail'])
        self.assertEqual(job['bodies'], 3)

    def test_history_with_zero_bodies_still_probes_and_records_zero(self):
        self.index(LINK)
        self.three_bodies()
        job = self.go(mode='history', bodies=0, sogou=False)
        self.assertEqual(self.article_client.bodies, [LINK])
        self.assertIn('探测读取 1 篇', job['detail'])
        self.assertIn('正文读取 0 篇', job['detail'])
        self.assertEqual(job['bodies'], 0)

    def test_failed_probe_still_uses_a_history_read(self):
        second = self.plant_pair()
        bodies = self.three_bodies()
        original = self.article_client.fetch_article

        def fetch(url):
            if url == LINK:
                self.article_client.bodies.append(url)
                raise ValueError('夹具失败')
            return original(url)

        self.article_client.fetch_article = fetch
        job = self.go(mode='history', bodies=MAX_BODIES_PER_RUN, sogou=False)
        self.assertEqual(self.article_client.bodies, [LINK, second, bodies[0]])
        self.assertIn('探测读取 2 篇', job['detail'])
        self.assertIn('正文读取 1 篇', job['detail'])

    def test_failed_body_read_still_uses_a_history_slot(self):
        self.seed_album()
        bodies = self.three_bodies()
        original = self.article_client.fetch_article

        def fetch(url):
            if url == bodies[0]:
                self.article_client.bodies.append(url)
                raise ValueError('正文夹具失败')
            return original(url)

        self.article_client.fetch_article = fetch
        job = self.go(mode='history', bodies=MAX_BODIES_PER_RUN, sogou=False)
        self.assertEqual(self.article_client.bodies, bodies)
        self.assertIn('探测读取 0 篇', job['detail'])
        self.assertIn('正文读取 3 篇', job['detail'])
        self.assertIn('正文 2 篇待确认', job['detail'])
        self.assertEqual(job['bodies'], 2)

    def test_latest_cold_start_does_not_share_the_history_budget(self):
        second = self.plant_pair()
        bodies = self.three_bodies()
        self.miss_then_hit(LINK)
        job = self.go(mode='latest', bodies=MAX_BODIES_PER_RUN, sogou=False)
        self.assertEqual(self.article_client.bodies, [LINK, second, *bodies])
        self.assertIn('探测读取 2 篇', job['detail'])
        self.assertIn('正文读取 3 篇', job['detail'])
        self.assertEqual(job['bodies'], 3)
