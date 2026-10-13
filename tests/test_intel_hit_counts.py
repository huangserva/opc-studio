"""R10：「来源命中数」与「唯一新增文章数」口径分开（合成用例，零外网）。

同一篇文章被多路来源命中时：来源命中数（outcome['list']['found']）按各路分别计数，
唯一新增文章数（outcome['unique_new'] → 对外 unique_new_articles）按入库身份去重只算一次。

运行：
    PYTHONDONTWRITEBYTECODE=1 TMPDIR=/tmp PYTHONPATH=.:tests \
        ../article-importer/.venv/bin/python -m unittest tests.test_intel_hit_counts
"""
import json
import tempfile
import unittest
from datetime import datetime, timezone

from intel_store import Intel
from intel_wechat_mp import BackendQuota
from server import Store

BIZ = 'MzB5NTkwMDAwMDE='
NAME = '卡兹克'
ALBUM = '3501527933040967680'
SENTINEL = 'CHKSM-SENTINEL-DO-NOT-STORE'
MID, IDX = '2650000001', '1'


def long_link(mid=MID, idx=IDX, chksm=SENTINEL):
    return f'https://mp.weixin.qq.com/s?__biz={BIZ}&mid={mid}&idx={idx}&sn=abc&chksm={chksm}'


def identity_raw(mid=MID, idx=IDX):
    return f'<html><script>var msgid = "{mid}"; var itemidx = "{idx}";</script></html>'


def album_raw(*albums):
    return ' '.join(f'<a href="https://mp.weixin.qq.com/mp/appmsgalbum?__biz={BIZ}&album_id={a}">'
                    f'album</a>' for a in albums) or '<html>no album</html>'


def page(title='标题', text='正文占位', raw='', published=1788319000):
    return {'biz': BIZ, 'nickname': NAME, 'title': title, 'published_at': published,
            'text': text, 'images': [], 'url': '', 'chars': len(text or ''), 'raw': raw}


class _MemorySettings:
    def __init__(self):
        self.data = {}

    def get(self, key, default=None):
        return self.data.get(key, default)

    def set(self, key, value):
        self.data[key] = str(value)


class FakeProvider:
    def __init__(self, results=None):
        self._results = list(results or [])

    def capabilities(self):
        return {'provider': 'fake', 'installed': True, 'available': True}

    def search(self, query, count, freshness, cancel=None, deadline=None):
        return {'provider': 'fake', 'results': list(self._results)}


class FakeReader:
    def __init__(self, default=None):
        self.default = default

    def __call__(self, url):
        if self.default is None:
            raise ValueError('没有为这个链接准备页面')
        return self.default


class FakePublic:
    def __init__(self, pages=None):
        self.pages = pages or {}
        self.album_calls = []

    def begin_round(self):
        pass

    def album_page(self, identifier, album_id, begin_msgid=None, begin_itemidx=None):
        self.album_calls.append(album_id)
        value = self.pages.get(album_id)
        if isinstance(value, Exception):
            raise value
        if value is not None:
            return value
        return {'title': '占位合集', 'article_count': 0, 'nickname': NAME, 'items': [],
                'continue_flag': False}

    def sogou_search(self, *a, **k):
        raise AssertionError('定时轮次不该访问搜狗')

    def sogou_resolve(self, *a, **k):
        raise AssertionError('定时轮次不该访问搜狗')


class FakeBackend:
    def __init__(self, items=None):
        self.quota = BackendQuota(_MemorySettings())
        self.items = items if items is not None else []
        self.calls = []

    def credentials_ready(self):
        return ('stub', '9999999999')

    def appmsg_list(self, identifier, begin, count):
        self.calls.append((identifier, begin, count))
        return list(self.items)

    def fetch_article(self, url):
        raise AssertionError('发现阶段正文由 A 复用，不该走后台抓正文')


class HitCountTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(self.tmp.name)
        self.provider = FakeProvider()
        self.reader = FakeReader()
        self.public = FakePublic()
        self.backend = FakeBackend()
        self.intel = Intel(self.store, client_factory=lambda s: self.backend,
                           public_factory=lambda s: self.public,
                           discovery_factory=lambda s: {'provider': self.provider, 'reader': self.reader})
        self.intel.subscribe({'fakeid': BIZ, 'name': NAME})

    def tearDown(self):
        for worker in list(self.intel.workers.values()):
            worker.join(5)
        self.tmp.cleanup()

    def job(self):
        return self.intel.jobs(5)[0]

    def _album_page(self, title='同一篇文章'):
        return {'title': '合集', 'article_count': 1, 'nickname': NAME, 'continue_flag': False,
                'items': [{'title': title, 'link': long_link(), 'published_at': 1788319000,
                           'msgid': MID, 'itemidx': IDX, 'source': 'album'}]}

    # ── 合成用例：同一篇文章跨来源命中 ─────────────────────────────────────
    def test_same_article_hit_by_discovery_and_album_is_new_once(self):
        # 发现命中这篇文章（并顺手发现合集），合集这一轮又把它端上来一次。
        self.provider._results = [{'url': long_link(), 'title': '同一篇文章'}]
        self.reader.default = page(title='同一篇文章',
                                   raw=identity_raw() + album_raw(ALBUM))
        self.public.pages[ALBUM] = self._album_page()
        receipt = self.intel.daily_latest(run_id='r' * 32)
        job = self.job()
        self.assertEqual(receipt['state'], 'done')
        self.assertEqual(self.intel.articles(BIZ)['total'], 1, '库里只有一篇文章')
        self.assertEqual(job['added'], 1, '入库只新增一篇')
        # 口径分开：来源命中 2（发现 + 合集各一次），唯一新增 1。
        self.assertEqual(job['outcome']['list']['found'], 2, '来源命中数按各路分别计数')
        self.assertEqual(job['unique_new_articles'], 1, '同一篇跨来源命中只算一次新增')
        self.assertLessEqual(job['unique_new_articles'], job['outcome']['list']['found'])
        # 既有字段语义不变：jobs.found / 轮次 listed 仍是原来的口径。
        self.assertEqual(job['found'], 1)
        self.assertEqual(receipt['listed'], 1)
        self.assertEqual(receipt['new_articles'], 1)

    def test_same_article_hit_by_discovery_and_backend_is_new_once(self):
        # 合集这一路失败→回退后台；发现与后台都命中同一篇：来源命中 2，唯一新增 1。
        from intel_public import AlbumError
        self.provider._results = [{'url': long_link(), 'title': '同一篇文章'}]
        self.reader.default = page(title='同一篇文章', raw=identity_raw() + album_raw(ALBUM))
        self.public.pages[ALBUM] = AlbumError('合成：合集这一路失败')
        self.backend.items = [{'title': '同一篇文章', 'link': long_link(),
                               'published_at': 1788319000, 'aid': ''}]
        self.intel.daily_latest(run_id='r' * 32)
        job = self.job()
        self.assertTrue(self.backend.calls, '合集全部失败时应回退后台')
        self.assertEqual(self.intel.articles(BIZ)['total'], 1)
        self.assertEqual(job['added'], 1)
        self.assertEqual(job['outcome']['list']['found'], 2, '发现与后台各命中一次')
        self.assertEqual(job['unique_new_articles'], 1, '两路命中同一篇也只算一次新增')
        self.assertLessEqual(job['unique_new_articles'], job['outcome']['list']['found'])

    def test_reused_article_next_round_adds_nothing(self):
        # 第一轮发现 + 合集命中；第二轮只剩合集命中：来源命中 1，唯一新增 0。
        self.provider._results = [{'url': long_link(), 'title': '同一篇文章'}]
        self.reader.default = page(title='同一篇文章', raw=identity_raw() + album_raw(ALBUM))
        self.public.pages[ALBUM] = self._album_page()
        self.intel.daily_latest(run_id='r' * 32)
        with self.intel.db() as connection:
            connection.execute('DELETE FROM discovery_state WHERE fakeid=?', (BIZ,))
        self.provider._results = []
        second = self.intel.daily_latest(run_id='s' * 32)
        job = self.job()
        self.assertEqual(second['state'], 'done')
        self.assertEqual(self.intel.articles(BIZ)['total'], 1, '第二轮不得重复入库')
        self.assertEqual(job['outcome']['list']['found'], 1, '第二轮仍有一次来源命中')
        self.assertEqual(job['unique_new_articles'], 0, '复用不是新增')
        self.assertEqual(job['added'], 0)

    def test_unique_new_field_is_honest_for_old_outcomes(self):
        # 旧任务回执里没有 unique_new：对外给 None，页面不显示这组数字，不拿 added 列猜。
        job_id = 'o' * 32
        old = {'version': 1, 'list': {'state': 'complete', 'found': 3, 'selected': 3, 'routes': {}},
               'save': {'state': 'complete', 'attempted': 3, 'succeeded': 3, 'new': 2, 'existing': 1,
                        'failed': 0, 'not_attempted': 0},
               'body': {'state': 'not_requested'}, 'summary': '旧回执'}
        with self.intel.db() as connection:
            connection.execute('''INSERT INTO jobs(id,fakeid,name,mode,status,started_at,found,added,outcome)
                VALUES(?,?,?,?,?,?,?,?,?)''',
                               (job_id, BIZ, NAME, 'scheduled', 'done',
                                datetime.now(timezone.utc).isoformat(), 3, 2, json.dumps(old)))
        row = self.intel.job(job_id)
        self.assertTrue(row['outcome_available'])
        self.assertIsNone(row['unique_new_articles'], '旧回执不得凭空造出唯一新增数')

    def test_unique_new_survives_an_unexpected_error_closeout(self):
        # 异常收口也要带上唯一新增数：发现成果已提交，不能因为后面出错就丢掉。
        self.provider._results = [{'url': long_link(), 'title': '同一篇文章'}]
        self.reader.default = page(title='同一篇文章', raw=identity_raw() + album_raw(ALBUM))
        self.public.pages[ALBUM] = self._album_page()

        def boom(*args, **kwargs):
            raise RuntimeError('合成：合集之后某一步炸了')

        original = self.intel.album_items
        self.intel.album_items = boom
        try:
            self.intel.daily_latest(run_id='t' * 32)
        finally:
            self.intel.album_items = original
        job = self.job()
        self.assertEqual(job['status'], 'error')
        self.assertEqual(job['unique_new_articles'], 1, '异常收口保留已提交的新增成果')
        self.assertEqual(self.intel.articles(BIZ)['total'], 1)


if __name__ == '__main__':
    unittest.main()
