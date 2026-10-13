"""R01：畸形微信文章 URL 必须统一抛 ValueError，坏单条不得拖垮整批。

零外网、零凭证：后台/合集/发现全部注入假实现，临时库 + /tmp。

运行：
    PYTHONDONTWRITEBYTECODE=1 TMPDIR=/tmp PYTHONPATH=.:tests \
        ../article-importer/.venv/bin/python -m unittest tests.test_intel_article_url_edges
"""
import json
import tempfile
import unittest

from intel_store import Intel
from intel_wechat_mp import article_url
from server import Store

BIZ = 'MzB5NTkwMDAwMDE='
NAME = '卡兹克'


def long_link(mid='2650000001', biz=BIZ, idx=1, chksm='CHKSM-SENTINEL'):
    return f'https://mp.weixin.qq.com/s?__biz={biz}&mid={mid}&idx={idx}&sn=abc&chksm={chksm}'


class MalformedUrlTests(unittest.TestCase):
    """缺 mid / 空 mid / 缺 __biz / 空 __biz / 非数字 mid：一律可识别的 ValueError。"""

    BAD = (
        ('缺 mid', f'https://mp.weixin.qq.com/s?__biz={BIZ}&idx=1&sn=abc&chksm=x'),
        ('空 mid', f'https://mp.weixin.qq.com/s?__biz={BIZ}&mid=&idx=1&sn=abc&chksm=x'),
        ('mid 非数字', f'https://mp.weixin.qq.com/s?__biz={BIZ}&mid=abc&idx=1&sn=abc'),
        ('缺 __biz', 'https://mp.weixin.qq.com/s?mid=2650000001&idx=1&sn=abc'),
        ('空 __biz', f'https://mp.weixin.qq.com/s?__biz=&mid=2650000001&idx=1&sn=abc'),
        ('只有 __biz', f'https://mp.weixin.qq.com/s?__biz={BIZ}'),
        ('空 query', 'https://mp.weixin.qq.com/s'),
        ('不是文章路径', 'https://mp.weixin.qq.com/mp/appmsgalbum?__biz=MzB5NTkwMDAwMDE=&album_id=1'),
    )

    def test_malformed_article_urls_raise_value_error(self):
        for label, url in self.BAD:
            with self.subTest(label):
                try:
                    article_url(url)
                except ValueError:
                    pass
                except Exception as exc:  # noqa: BLE001
                    self.fail(f'{label}：应抛 ValueError，实际抛 {type(exc).__name__}: {exc}')
            with self.subTest(f'{label} 必须是 ValueError 类型'):
                self.assertRaises(ValueError, article_url, url)

    def test_valid_urls_still_pass_through_unchanged(self):
        # 正常长链：query 一个字都不动（chksm 必须原样保留）。
        good = long_link()
        self.assertEqual(article_url(good), good)
        # 正常短码链接照旧接受。
        self.assertEqual(article_url('https://mp.weixin.qq.com/s/AbC0123xyz'),
                         'https://mp.weixin.qq.com/s/AbC0123xyz')
        # 合集接口给的 http 长链照旧升到 https。
        http = f'http://mp.weixin.qq.com/s?__biz={BIZ}&mid=2650000001&idx=1&sn=abc&chksm=x'
        self.assertEqual(article_url(http), 'https' + http[4:])
        # 片段（#rd）丢掉，query 保留。
        self.assertEqual(article_url(good + '#rd'), good)


class _MemorySettings:
    def __init__(self):
        self.data = {}

    def get(self, key, default=None):
        return self.data.get(key, default)

    def set(self, key, value):
        self.data[key] = str(value)


class FakeProvider:
    def __init__(self):
        self._results = []

    def capabilities(self):
        return {'provider': 'fake', 'installed': True, 'available': True}

    def search(self, query, count, freshness, cancel=None, deadline=None):
        return {'provider': 'fake', 'results': list(self._results)}


class FakePublic:
    def __init__(self):
        self.pages = {}

    def begin_round(self):
        pass

    def album_page(self, identifier, album_id, begin_msgid=None, begin_itemidx=None):
        raise AssertionError('本用例没有登记合集，不该请求合集')

    def sogou_search(self, *a, **k):
        raise AssertionError('定时轮次不该访问搜狗')

    def sogou_resolve(self, *a, **k):
        raise AssertionError('定时轮次不该访问搜狗')


class FakeBackend:
    from intel_wechat_mp import BackendQuota as _Quota

    def __init__(self, items=None):
        self.quota = self._Quota(_MemorySettings())
        self.items = items or []
        self.calls = []

    def credentials_ready(self):
        return ('stub', '9999999999')

    def appmsg_list(self, identifier, begin, count):
        self.calls.append((identifier, begin, count))
        return list(self.items)

    def fetch_article(self, url):
        raise AssertionError('本用例不抓正文')


class BatchIsolationTests(unittest.TestCase):
    """一条坏链接：隔离并留下失败回执，同批正常单条照常入库，整轮不得 error。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(self.tmp.name)
        self.provider = FakeProvider()
        self.public = FakePublic()
        self.backend = FakeBackend()
        self.intel = Intel(self.store, client_factory=lambda s: self.backend,
                           public_factory=lambda s: self.public,
                           discovery_factory=lambda s: {'provider': self.provider, 'reader': None})
        self.intel.subscribe({'fakeid': BIZ, 'name': NAME})

    def tearDown(self):
        for worker in list(self.intel.workers.values()):
            worker.join(5)
        self.tmp.cleanup()

    def test_one_malformed_link_does_not_fail_the_batch(self):
        bad = {'title': '坏链接文章', 'link': f'https://mp.weixin.qq.com/s?__biz={BIZ}&idx=1&sn=abc&chksm=x',
               'published_at': 1788319000, 'aid': ''}
        good = {'title': '正常文章', 'link': long_link(), 'published_at': 1788319100, 'aid': ''}
        self.backend.items = [bad, good]
        receipt = self.intel.daily_latest(run_id='r' * 32)
        job = self.intel.jobs(5)[0]
        self.assertNotEqual(job['status'], 'error', f"一条坏链接把整轮带崩：{job['detail']}")
        self.assertEqual(receipt['state'], 'done', '整轮要正常收口')
        self.assertTrue(self.backend.calls, '后台这一路确实跑过')
        saved = self.intel.articles(BIZ)['articles']
        titles = {row['title'] for row in saved}
        self.assertIn('正常文章', titles, '正常单条必须照常入库')
        good = next(row for row in saved if row['title'] == '正常文章')
        self.assertTrue(good['link'].startswith('https://mp.weixin.qq.com/s?'), '正常链接原样入库')
        self.assertEqual(len(saved), 2, '坏单条按既有口径存成无链接占位，不静默丢')
        self.assertNotIn('未预期的错误', job['detail'], '回执不得再把坏链接报成整轮意外错误')
        broken = next(row for row in saved if row['title'] == '坏链接文章')
        self.assertEqual(broken['link'], '', '不合格链接不得作为 href 入库')
        self.assertEqual(broken['link_ok'], 0, '坏链接要留下可辨认的失败标记（无链接占位）')
        self.assertEqual(job['added'], 2)

    def test_malformed_link_never_reaches_the_browser_fallback(self):
        # identify_from_link 这类入口：坏链接只报可识别错误，不冒 KeyError。
        from intel_store import Intel as _I  # noqa: F401
        self.assertRaises(ValueError, article_url,
                          f'https://mp.weixin.qq.com/s?__biz={BIZ}&idx=1')
        # complete_article_link 对同一链接照旧返回空串（不抛）。
        self.assertEqual(self.intel.complete_article_link(
            f'https://mp.weixin.qq.com/s?__biz={BIZ}&idx=1&chksm=x', BIZ), '')


if __name__ == '__main__':
    unittest.main()


# ── M1：判据不能改旧行的身份（重采不新增） ──────────────────────────────────

class LegacyIdentityTests(unittest.TestCase):
    """旧库已经按旧判据落库的行，重采时必须还是同一个身份键，不得另建一条（M1）。

    合成旧库用的是产品真实的 `_upsert_article`（落库身份就是它算的），因此这组用例
    逐条复现复核报告里「基座 7 条 → 补丁 9 条」那两个输入。
    """

    # (标签, 原始链接, 旧判据是否算合格链接, 旧库当年实际用来落库的值)
    # 后两例就是 M1 的两个回归输入：旧判据对它们分别存成「无链接占位」与「带链接」。
    ROWS = (
        ('normal', long_link(), True, long_link()),
        ('http', long_link(mid='2650000002').replace('https://', 'http://'), True, long_link(mid='2650000002')),
        ('short', 'https://mp.weixin.qq.com/s/AbC0123xyz', True, 'https://mp.weixin.qq.com/s/AbC0123xyz'),
        ('outerspaces', '  ' + long_link(mid='2650000003') + '  ', True, long_link(mid='2650000003')),
        ('invalid-mid', long_link(mid='no-number'), False, ''),
        ('padded-mid', long_link(mid='%202650000004%20'), False, ''),
        ('space-biz', f'https://mp.weixin.qq.com/s?__biz=%20&mid=2650000005&idx=1&sn=abc', True,
         f'https://mp.weixin.qq.com/s?__biz=%20&mid=2650000005&idx=1&sn=abc'),
    )

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(self.tmp.name)
        self.intel = Intel(self.store, client_factory=lambda s: FakeBackend(),
                           public_factory=lambda s: FakePublic(),
                           discovery_factory=lambda s: {'provider': FakeProvider(), 'reader': None})
        self.intel.subscribe({'fakeid': BIZ, 'name': NAME})

    def tearDown(self):
        for worker in list(self.intel.workers.values()):
            worker.join(5)
        self.tmp.cleanup()

    def seed(self):
        """按**旧判据**把旧库建出来：每条用当年实际落库的值，带旧正文便于证明不是空壳行。"""
        ids = {}
        for n, (label, _raw, _ok, legacy) in enumerate(self.ROWS):
            item = {'title': label, 'link': legacy, 'published_at': 1788319000 + n, 'aid': ''}
            ident, created = self.intel._upsert_article(BIZ, item, '')
            self.assertTrue(created, '旧库首建必须是新建：' + label)
            self.intel.save_body(ident, {'nickname': NAME, 'text': '旧正文-' + label,
                                         'chars': len('旧正文-' + label), 'images': []})
            ids[label] = ident
        return ids

    def test_acceptance_set_matches_the_legacy_rule_exactly(self):
        # 判据一旦动（旧补丁给 __biz/mid 加 strip），这两行的合格与否就变了。
        from intel_store import accepted_link
        for label, link, expected_ok, _legacy in self.ROWS:
            with self.subTest(label=label):
                self.assertEqual(bool(accepted_link(link)), expected_ok,
                                 f'{label} 的合格判据与旧库不一致，会改掉旧行的身份键')
                if not expected_ok:
                    with self.assertRaises(ValueError):
                        article_url(link)
                else:
                    self.assertEqual(article_url(link), accepted_link(link))

    def test_recollect_of_a_legacy_library_adds_no_duplicate_rows(self):
        # 旧库是「旧判据落的行」，这一轮重采送进去的是**原始链接**：同一篇必须命中旧行。
        ids = self.seed()
        before = self.intel.articles(BIZ)
        for n, (label, raw, _ok, _legacy) in enumerate(self.ROWS):
            item = {'title': label, 'link': raw, 'published_at': 1788319000 + n, 'aid': ''}
            _ident, created = self.intel._upsert_article(BIZ, item, '')
            self.assertFalse(created, f'旧文章重采又新建了一条：{label}')
        after = self.intel.articles(BIZ)
        self.assertEqual(after['total'], before['total'], '重采后总条数不能变')
        self.assertEqual(len(ids), len(self.ROWS))
        self.assertEqual({row['id'] for row in after['articles']}, set(ids.values()),
                         '旧行的 id 必须原样复用')
        with self.intel.db() as connection:               # 旧正文不能被重采覆盖
            bodies = {row['id']: str(row['body'] or '') for row in
                      connection.execute('SELECT id,body FROM articles WHERE fakeid=?', (BIZ,))}
        for label, ident in ids.items():
            self.assertEqual(bodies.get(ident), '旧正文-' + label, '旧正文被覆盖：' + label)

    def test_encoding_boundary_stays_where_the_legacy_library_left_it(self):
        # L1：畸形 percent 编码的既有边界（继承，不在本轮收紧）。收紧会让旧行身份键变化，
        # 那正是 M1 要避免的重复入库；这里把当前边界钉住，防止以后无声改动。
        from intel_store import accepted_link
        accepted = (f'https://mp.weixin.qq.com/s?__biz=%GG&mid=2650000001',
                    f'https://mp.weixin.qq.com/s?__biz=%FF&mid=2650000001',
                    'https://mp.weixin.qq.com/s?__biz=%&mid=2650000001')
        rejected = f'https://mp.weixin.qq.com/s?__biz={BIZ}&mid=%GG'
        for url in accepted:
            with self.subTest(url=url):
                self.assertTrue(accepted_link(url), '与旧库一致的边界：__biz 畸形仍按原样入库')
        self.assertEqual(accepted_link(rejected), '', 'mid 畸形一律不合格')
        with self.assertRaises(ValueError):
            article_url(rejected)


# ── M3：真实客户端的坏单条要有失败回执 ──────────────────────────────────────

class RealClientIsolationTests(unittest.TestCase):
    """真 WechatMpClient + 假 transport：坏单条隔离、正常单条入库、回执写明失败条数（M3）。

    刻意不走 FakeBackend：只有真实客户端的解析过滤才知道哪条链接不合格，
    绕过它就无法证明「坏条目没有静默消失」。
    """

    BAD = f'https://mp.weixin.qq.com/s?__biz={BIZ}&idx=1&sn=bad'

    def setUp(self):
        from intel_wechat_mp import BackendQuota, WechatMpClient
        from tests.test_intel_deadline_http import FakeCredentials, NoWaitThrottle, _MemorySettings
        self.quota_class = BackendQuota
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(self.tmp.name)
        self.raw = {'base_resp': {'ret': 0}, 'app_msg_list': [
            {'title': '坏链接文章', 'link': self.BAD, 'create_time': 1788319000},
            {'title': '正常文章', 'link': long_link(), 'create_time': 1788319100}]}
        self.calls = []

        def opener(url, headers, timeout):
            self.calls.append({'url': url, 'timeout': timeout})
            return json.dumps(self.raw).encode()

        self.client = WechatMpClient(FakeCredentials(), BackendQuota(_MemorySettings()),
                                     throttle=NoWaitThrottle(), opener=opener)
        self.intel = Intel(self.store, client_factory=lambda s: self.client,
                           public_factory=lambda s: FakePublic(),
                           discovery_factory=lambda s: {'provider': FakeProvider(), 'reader': None})
        self.intel.subscribe({'fakeid': BIZ, 'name': NAME})

    def tearDown(self):
        for worker in list(self.intel.workers.values()):
            worker.join(5)
        self.tmp.cleanup()

    def test_client_reports_the_bad_item_instead_of_dropping_it_silently(self):
        items = self.client.appmsg_list(BIZ, 0, 20)
        self.assertEqual(len(items), 1, '返回的列表仍只有可用条目（既有调用方不变）')
        self.assertTrue(items[0]['link'])
        isolated = self.client.isolated_items
        self.assertEqual(len(isolated), 1, '坏单条不能被静默丢掉，要单独交上来')
        self.assertIn('mp.weixin.qq.com', str(isolated[0]['reason']))
        self.assertEqual(isolated[0]['title'], '坏链接文章')
        self.raw['app_msg_list'] = [{'title': '正常文章', 'link': long_link(), 'create_time': 1788319100}]
        self.client.quota.settings.set(self.quota_class.K_LAST, '0')    # 清掉 90 秒节流标记，再读一次
        self.client.appmsg_list(BIZ, 0, 20)
        self.assertEqual(self.client.isolated_items, [], '每次读取先清空，不留上一次的残留')

    def test_scheduled_round_records_the_isolated_item_as_a_failure(self):
        receipt = self.intel.daily_latest(run_id='r' * 32)
        job = self.intel.jobs(5)[0]
        self.assertNotEqual(job['status'], 'error', f"一条坏链接把整轮带崩：{job['detail']}")
        self.assertEqual(receipt['state'], 'done')
        routes = {line['path']: line['text'] for line in job['routes']}
        self.assertIn('链接不合格已隔离', routes['backend'], f"回执要写明隔离条数：{routes}")
        outcome = job['outcome']
        self.assertEqual(outcome['save']['failed'], 1, '坏单条必须计入保存失败')
        self.assertEqual(outcome['save']['succeeded'], 1, '正常单条照常入库')
        self.assertEqual(outcome['save']['attempted'], outcome['save']['succeeded'] + outcome['save']['failed'],
                         'attempted=succeeded+failed 的不变量不能破')
        self.assertEqual(outcome['save']['state'], 'partial')
        saved = self.intel.articles(BIZ)['articles']
        self.assertEqual([row['title'] for row in saved], ['正常文章'], '坏条目不占位、不入库')
        self.assertEqual(job['added'], 1)

    def test_round_without_bad_items_keeps_the_complete_save_stage(self):
        self.raw['app_msg_list'] = [{'title': '正常文章', 'link': long_link(), 'create_time': 1788319100}]
        self.intel.daily_latest(run_id='r' * 32)
        job = self.intel.jobs(5)[0]
        save = job['outcome']['save']
        self.assertEqual((save['state'], save['failed']), ('complete', 0))
        self.assertNotIn('链接不合格已隔离',
                         {line['path']: line['text'] for line in job['routes']}['backend'])
