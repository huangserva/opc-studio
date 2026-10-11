"""知识库页面「收藏他人作品」入口的旧键衔接（POST /api/knowledge/reference）。

回收站入口在 fixes-platform-links-1 已经接上旧键衔接；这一份锁另一个入口：识别接入之前
按 host+path+query 登记过的那篇三家长链接，从这个 API 再收一次不能多出一篇，而且回执要与
既有「已存在」口径完全一致（前端只读 `duplicate` 与 `id`，没有新文案）。

走真实 HTTP 路由 + 临时库；不联网、不抓取、不写正式库。编号与 token 一律是合成短串。
"""
import contextlib
import json
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from unittest.mock import patch

import collect_links
import external_reference
from external_reference import existing_legacy_document, legacy_document_id
from intake_store import Intake
from knowledge_store import Knowledge, identifier
from server import Handler, Store
from tests.intake_dependency_patches import guard_intake_dependencies, patch_intake_dependency

XHS_NOTE = '64f0a1b20000000012034567'
XHS_URL = 'https://www.xiaohongshu.com/explore/%s?xsec_token=AAA111&xsec_source=pc_share' % XHS_NOTE
XHS_URL_OTHER_TOKEN = 'https://www.xiaohongshu.com/explore/%s?xsec_token=BBB222' % XHS_NOTE
XHS_LEGACY_CANONICAL = XHS_URL
XHS_LEGACY_ID = identifier('external-references', XHS_LEGACY_CANONICAL)
XHS_NEW_ID = identifier('external-references', 'https://www.xiaohongshu.com/explore/' + XHS_NOTE)
XHS_SHORT = 'http://xhslink.com/a/AbCdEf123'
# 视频号带末尾斜杠与跟踪参数：旧口径保留 path 与 query，去重地址只留作品编号，两个键不同。
SPH_URL = 'https://weixin.qq.com/sph/Aa1Bb2Cc3Dd/?utm_source=copy_link'
SPH_LEGACY_ID = identifier('external-references', SPH_URL)
SPH_NEW_ID = identifier('external-references', 'https://weixin.qq.com/sph/Aa1Bb2Cc3Dd')
SPH_PLAIN = 'https://weixin.qq.com/sph/Aa1Bb2Cc3Dd'
DOUYIN_URL = 'https://www.douyin.com/video/7412345678901234567?utm_source=share&tt_from=copy_link'
DOUYIN_LEGACY_ID = identifier('external-references', DOUYIN_URL)
DOUYIN_NEW_ID = identifier('external-references', 'https://www.douyin.com/video/7412345678901234567')
BLOG_URL = 'https://blog.example.com/posts/how-to-open-an-article?utm_source=share#top'
REMIO_DOWN = '联网服务未完成查询，请核对网页地址及 remio 连接后重试'


@contextlib.contextmanager
def identification_off():
    """把平台识别关掉，只在这段里重现旧版登记三家长链接时的口径。"""
    with patch.object(collect_links, 'identify', lambda url: None), \
            patch.object(external_reference, 'identify_platform', lambda url: None):
        yield


class FakeWeb:
    """Stands in for assistant_web.WebTools; the tests never touch the network."""

    def __init__(self, pages=(), error=None, title='合成页面'):
        self.pages, self.error, self.title, self.calls = list(pages), error, title, []

    def __call__(self):
        return self

    def fetch(self, args, job):
        self.calls.append(dict(args))
        if self.error:
            raise ValueError(self.error)
        body = ''.join(self.pages)
        start = args.get('start', 0)
        end = min(start + args.get('max_chars', 16000), len(body))
        return {'url': args['url'], 'title': self.title, 'markdown': body[start:end], 'start': start,
                'total_chars': len(body), 'truncated': end < len(body),
                'next_start': end if end < len(body) else None}


class SharedLookupTests(unittest.TestCase):
    """公共函数本身：输入 url 与 knowledge，命中旧记录才返回旧编号。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(self.tmp.name)
        self.knowledge = Knowledge(self.store, sources=[])
        self.store._knowledge = self.knowledge

    def file_under_old_key(self, url, title='合成外部文章'):
        with identification_off():
            return self.knowledge.add_reference({'title': title, 'url': url})

    def test_it_returns_the_old_document_id_only_when_that_document_exists(self):
        self.assertEqual(existing_legacy_document(XHS_URL, self.knowledge), '')
        legacy = self.file_under_old_key(XHS_URL)
        self.assertEqual(legacy['id'], XHS_LEGACY_ID)
        self.assertEqual(existing_legacy_document(XHS_URL, self.knowledge), XHS_LEGACY_ID)

    def test_it_never_writes_and_never_migrates(self):
        self.file_under_old_key(XHS_URL)
        before = self.knowledge.document(XHS_LEGACY_ID)
        for _ in range(3):
            existing_legacy_document(XHS_URL, self.knowledge)
        after = self.knowledge.document(XHS_LEGACY_ID)
        self.assertEqual(before, after)
        self.assertEqual(self.knowledge.search(kind='external')['total'], 1)

    def test_links_whose_key_never_changed_are_left_to_the_normal_dedupe(self):
        """非三家、短链、无效地址都不做旧键查找：新旧口径本来就是同一个键。"""
        for url in (BLOG_URL, XHS_SHORT, '', 'javascript:alert(1)', 'ftp://xiaohongshu.com/x',
                    'not a url', None, 123, 'https://user:pass@www.xiaohongshu.com/explore/x'):
            with self.subTest(url=url):
                self.assertEqual(existing_legacy_document(url, self.knowledge), '')
        # 短链在旧口径下登记过，也不靠这条衔接：add_reference 自己的去重已经覆盖。
        legacy = self.file_under_old_key(XHS_SHORT)
        self.assertEqual(existing_legacy_document(XHS_SHORT, self.knowledge), '')
        self.assertEqual(legacy_document_id(XHS_SHORT), legacy['id'])


class ReferenceApiTests(unittest.TestCase):
    """POST /api/knowledge/reference：知识库页面的收藏入口。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(self.tmp.name)
        self.store._knowledge = Knowledge(self.store, sources=[])
        self.httpd = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.httpd.store = self.store
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.shutdown)
        self.base = 'http://127.0.0.1:%d' % self.httpd.server_port

    def shutdown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join()

    def post(self, payload):
        request = Request(self.base + '/api/knowledge/reference', data=json.dumps(payload).encode(),
                          headers={'Content-Type': 'application/json', 'X-Article-Studio': '1',
                                   'Origin': self.base})
        with urlopen(request, timeout=5) as response:
            return response.status, json.load(response)

    def reject(self, payload):
        """非法请求的回执：(状态码, 错误正文)，用来对比「有没有旧文档」两种情况口径是否一致。"""
        try:
            return self.post(payload)
        except HTTPError as exc:
            body = json.loads(exc.read())
            exc.close()
            return exc.code, body

    def external_total(self):
        return self.store.knowledge.search(kind='external')['total']

    def file_under_old_key(self, url, title='合成外部文章'):
        with identification_off():
            return self.store.knowledge.add_reference({'title': title, 'url': url})

    def test_a_link_already_filed_under_the_old_key_is_not_filed_twice(self):
        legacy = self.file_under_old_key(XHS_URL)
        self.assertEqual(legacy['id'], XHS_LEGACY_ID)
        self.assertEqual(self.external_total(), 1)
        status, hit = self.post({'title': '同一链接再收一次', 'url': XHS_URL, 'reader_note': '再收一次'})
        self.assertEqual(status, 201)
        self.assertEqual(hit['id'], XHS_LEGACY_ID)
        self.assertTrue(hit['duplicate'])
        self.assertEqual(self.external_total(), 1)

    def test_the_legacy_hit_answers_exactly_like_an_ordinary_duplicate(self):
        """前端只认 `duplicate` 与 `id`；命中旧记录时的返回必须和既有「已存在」一模一样。"""
        self.file_under_old_key(XHS_URL)
        status, hit = self.post({'title': '同一链接再收一次', 'url': XHS_URL})
        self.post({'title': '普通博客', 'url': BLOG_URL})
        ordinary_status, ordinary = self.post({'title': '普通博客再收一次', 'url': BLOG_URL})
        self.assertEqual((status, ordinary_status), (201, 201))
        self.assertEqual(set(hit), set(ordinary))
        self.assertTrue(hit['duplicate'])
        self.assertEqual(hit['id'], XHS_LEGACY_ID)
        self.assertEqual(hit['provenance']['canonical_url'], XHS_LEGACY_CANONICAL)

    def test_the_legacy_hit_leaves_the_old_document_as_it_was(self):
        legacy = self.file_under_old_key(XHS_URL, title='旧那篇的标题')
        before = self.store.knowledge.document(XHS_LEGACY_ID)
        self.post({'title': '新标题不能覆盖', 'url': XHS_URL, 'body': '新片段不能覆盖',
                   'reader_note': '新评论不能覆盖'})
        after = self.store.knowledge.document(XHS_LEGACY_ID)
        self.assertEqual(before, after)
        self.assertEqual(after['title'], '旧那篇的标题')
        self.assertEqual(after['provenance']['reader_note'], '')
        self.assertEqual(self.external_total(), 1)

    def test_a_channels_link_under_the_old_key_is_not_filed_twice(self):
        """视频号带斜杠与跟踪参数时换了去重地址：旧口径登记过就复用旧那篇。"""
        self.assertNotEqual(SPH_LEGACY_ID, SPH_NEW_ID)
        legacy = self.file_under_old_key(SPH_URL)
        self.assertEqual(legacy['id'], SPH_LEGACY_ID)
        status, hit = self.post({'title': '同一个视频号作品', 'url': SPH_URL})
        self.assertEqual(status, 201)
        self.assertEqual(hit['id'], SPH_LEGACY_ID)
        self.assertTrue(hit['duplicate'])
        self.assertEqual(self.external_total(), 1)

    def test_a_douyin_link_with_tracking_parameters_under_the_old_key_is_not_filed_twice(self):
        legacy = self.file_under_old_key(DOUYIN_URL)
        self.assertEqual(legacy['id'], DOUYIN_LEGACY_ID)
        status, hit = self.post({'title': '同一个抖音视频', 'url': DOUYIN_URL})
        self.assertEqual(status, 201)
        self.assertEqual(hit['id'], DOUYIN_LEGACY_ID)
        self.assertTrue(hit['duplicate'])
        self.assertEqual(self.external_total(), 1)

    def test_a_link_whose_two_keys_already_match_stays_one_document(self):
        """不带 query 的视频号链接新旧口径本来就是同一个键，衔接不改变结果。"""
        self.assertEqual(identifier('external-references', SPH_PLAIN), SPH_NEW_ID)
        _, first = self.post({'title': '视频号作品', 'url': SPH_PLAIN})
        self.assertEqual(first['id'], SPH_NEW_ID)
        self.assertNotIn('duplicate', first)
        _, again = self.post({'title': '同一个视频号作品', 'url': SPH_PLAIN})
        self.assertEqual(again['id'], SPH_NEW_ID)
        self.assertTrue(again['duplicate'])
        self.assertEqual(self.external_total(), 1)

    def test_without_an_old_record_the_new_dedupe_key_is_used(self):
        status, made = self.post({'title': '合成外部文章', 'url': XHS_URL})
        self.assertEqual(status, 201)
        self.assertEqual(made['id'], XHS_NEW_ID)
        self.assertNotIn('duplicate', made)
        self.assertEqual(made['provenance']['canonical_url'],
                         'https://www.xiaohongshu.com/explore/' + XHS_NOTE)
        self.assertEqual(self.external_total(), 1)

    def test_the_same_link_again_still_reports_a_duplicate_under_the_new_key(self):
        _, first = self.post({'title': '合成外部文章', 'url': XHS_URL})
        _, again = self.post({'title': '再收一次', 'url': XHS_URL})
        self.assertEqual(again['id'], first['id'])
        self.assertTrue(again['duplicate'])
        self.assertEqual(self.external_total(), 1)

    def test_an_old_record_with_another_token_is_still_a_second_document(self):
        """边界（与回收站入口同一口径）：旧 canonical 带 query，换 token 就是另一个旧键。"""
        legacy = self.file_under_old_key(XHS_URL)
        _, made = self.post({'title': '换 token', 'url': XHS_URL_OTHER_TOKEN})
        self.assertNotEqual(made['id'], legacy['id'])
        self.assertNotIn('duplicate', made)
        self.assertEqual(self.external_total(), 2)

    def test_a_short_link_under_the_old_key_stays_one_document(self):
        legacy = self.file_under_old_key(XHS_SHORT)
        status, hit = self.post({'title': '同一条短链', 'url': XHS_SHORT})
        self.assertEqual(status, 201)
        self.assertEqual(hit['id'], legacy['id'])
        self.assertTrue(hit['duplicate'])
        self.assertEqual(self.external_total(), 1)

    def test_a_plain_blog_link_is_untouched_by_the_bridge(self):
        _, first = self.post({'title': '普通博客', 'url': BLOG_URL})
        self.assertEqual(first['provenance']['canonical_url'],
                         'https://blog.example.com/posts/how-to-open-an-article?utm_source=share')
        self.assertNotIn('source_item_id', first['provenance'])
        _, again = self.post({'title': '普通博客再收一次', 'url': BLOG_URL})
        self.assertEqual(again['id'], first['id'])
        self.assertTrue(again['duplicate'])
        self.assertEqual(self.external_total(), 1)

    def test_invalid_payloads_are_still_rejected_the_same_way(self):
        """加了旧键查找之后，非法入参的口径不能变（更不能变成 500）。"""
        for payload in ({'title': '未知', 'url': 'javascript:bad()'},
                        {'title': '合成', 'url': 'https://user:pass@example.test/a'},
                        {'title': '合成', 'url': 'https://example.test/a\n'},
                        {'title': '合成', 'url': ['wrong']}, {'title': '合成', 'url': {'a': 1}},
                        ['不是对象'], '不是对象', {}):
            with self.subTest(payload=payload), self.assertRaises(HTTPError) as caught:
                self.post(payload)
            self.assertEqual(caught.exception.code, 400)
            caught.exception.close()
        self.assertEqual(self.external_total(), 0)

    def test_illegal_fields_are_rejected_before_the_legacy_lookup(self):
        """M1：库里已有旧键文档时，非法字段不能因为命中旧记录被当成收藏成功。

        三种非法请求（缺标题、标题非字符串、多了未知字段）在旧键查找之前就要被拒，
        回执与「库里没有旧文档」时逐字一致；旧那篇一字不改，也不多写新文档。
        """
        illegal = ({'url': XHS_URL},                       # 只有有效链接，缺标题
                   {'title': 123, 'url': XHS_URL},          # 标题不是字符串
                   {'title': '合成标题', 'url': XHS_URL, 'surprise': True})   # 多了未知字段
        baseline = [self.reject(payload) for payload in illegal]   # 没有旧文档时的口径
        for code, body in baseline:
            self.assertEqual(code, 400)
            self.assertIn('error', body)
        self.assertEqual(self.external_total(), 0)
        legacy = self.file_under_old_key(XHS_URL)
        self.assertEqual(legacy['id'], XHS_LEGACY_ID)
        self.assertEqual(self.external_total(), 1)
        untouched = self.store.knowledge.document(XHS_LEGACY_ID)
        for payload, expected in zip(illegal, baseline):
            with self.subTest(payload=payload):
                self.assertEqual(self.reject(payload), expected)
        self.assertEqual(self.external_total(), 1)
        self.assertEqual(self.store.knowledge.document(XHS_LEGACY_ID), untouched)

    def test_the_invalid_payload_matrix_is_still_400_with_an_old_record_present(self):
        """旧文档存在时，原来那八种非法入参口径也不变（命中路径排在它们之后）。"""
        self.file_under_old_key(XHS_URL)
        untouched = self.store.knowledge.document(XHS_LEGACY_ID)
        for payload in ({'title': '未知', 'url': 'javascript:bad()'},
                        {'title': '合成', 'url': 'https://user:pass@example.test/a'},
                        {'title': '合成', 'url': 'https://example.test/a\n'},
                        {'title': '合成', 'url': ['wrong']}, {'title': '合成', 'url': {'a': 1}},
                        ['不是对象'], '不是对象', {}):
            with self.subTest(payload=payload), self.assertRaises(HTTPError) as caught:
                self.post(payload)
            self.assertEqual(caught.exception.code, 400)
            caught.exception.close()
        self.assertEqual(self.external_total(), 1)
        self.assertEqual(self.store.knowledge.document(XHS_LEGACY_ID), untouched)


class RecycleBinGateTests(unittest.TestCase):
    """回收站入口没有同类绕过：整条请求在 receive 那一刻就校验完。

    旧键查找排在 `resolve` 里，只能碰到已经存下的链接；非法链接/非法标题在 `receive`
    就 400，根本到不了旧键查找，所以不存在「非法请求被当成收藏成功」的路径。
    这里用真实 HTTP 把这条闸门钉住（库里已有旧口径那篇时也一样）。
    """

    def setUp(self):
        guard_intake_dependencies(self)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(self.tmp.name)
        self.store._knowledge = Knowledge(self.store, sources=[])
        self.intake = Intake(self.store.knowledge, self.tmp.name)
        fake = FakeWeb(error=REMIO_DOWN)
        patch_intake_dependency(self, 'WebTools', fake)
        self.httpd = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.httpd.store = self.store
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.shutdown)
        self.base = 'http://127.0.0.1:%d' % self.httpd.server_port

    def shutdown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join()

    def post(self, path, payload):
        request = Request(self.base + path, data=json.dumps(payload).encode(),
                          headers={'Content-Type': 'application/json', 'X-Article-Studio': '1',
                                   'Origin': self.base})
        try:
            with urlopen(request, timeout=5) as response:
                return response.status, json.load(response)
        except HTTPError as exc:
            body = json.loads(exc.read())
            exc.close()
            return exc.code, body

    def test_illegal_drops_are_refused_before_any_legacy_lookup(self):
        with identification_off():
            status, made = self.post('/api/knowledge/reference', {'title': '知识库页面先收藏', 'url': XHS_URL})
        self.assertEqual((status, made['id']), (201, XHS_LEGACY_ID))
        total = self.store.knowledge.search(kind='external')['total']
        for payload in ({'kind': 'link', 'url': 'https://user:pass@www.xiaohongshu.com/explore/x'},
                        {'kind': 'link', 'url': 'javascript:alert(1)'},
                        {'kind': 'link', 'url': XHS_URL, 'title': 123},
                        {'kind': 'link', 'url': XHS_URL, 'text': '链接项不接受正文'},
                        {'kind': 'link', 'url': XHS_URL, 'surprise': True}):
            with self.subTest(payload=payload):
                status, body = self.post('/api/intake/drop', payload)
                self.assertEqual(status, 400)
                self.assertIn('error', body)
        self.assertEqual(self.store.knowledge.search(kind='external')['total'], total)
        self.assertEqual(self.intake.listing()['items'], [])


class EntriesAgreeTests(unittest.TestCase):
    """两个入口共用一套查找：回收站与知识库页面收藏指向同一篇。"""

    def setUp(self):
        guard_intake_dependencies(self)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(self.tmp.name)
        self.store._knowledge = Knowledge(self.store, sources=[])
        self.intake = Intake(self.store.knowledge, self.tmp.name)
        fake = FakeWeb(error=REMIO_DOWN)
        patch_intake_dependency(self, 'WebTools', fake)
        self.httpd = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.httpd.store = self.store
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.shutdown)
        self.base = 'http://127.0.0.1:%d' % self.httpd.server_port

    def shutdown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join()

    def post(self, payload):
        request = Request(self.base + '/api/knowledge/reference', data=json.dumps(payload).encode(),
                          headers={'Content-Type': 'application/json', 'X-Article-Studio': '1',
                                   'Origin': self.base})
        with urlopen(request, timeout=5) as response:
            return json.load(response)

    def test_the_api_entry_reuses_what_the_recycle_bin_filed_under_the_old_key(self):
        with identification_off():
            saved = self.intake.receive({'kind': 'link', 'url': XHS_URL, 'reason': '合成收藏理由'})
            dropped = self.intake.resolve(saved['id'])
        self.assertEqual(dropped['document_id'], XHS_LEGACY_ID)
        self.assertEqual(self.store.knowledge.search(kind='external')['total'], 1)
        hit = self.post({'title': '知识库页面再收一次', 'url': XHS_URL})
        self.assertEqual(hit['id'], XHS_LEGACY_ID)
        self.assertTrue(hit['duplicate'])
        self.assertEqual(self.store.knowledge.search(kind='external')['total'], 1)

    def test_the_recycle_bin_reuses_what_the_api_filed_under_the_old_key(self):
        with identification_off():
            made = self.post({'title': '知识库页面先收藏', 'url': XHS_URL})
        self.assertEqual(made['id'], XHS_LEGACY_ID)
        done = self.intake.resolve(self.intake.receive(
            {'kind': 'link', 'url': XHS_URL, 'reason': '合成收藏理由'})['id'])
        self.assertEqual(done['document_id'], XHS_LEGACY_ID)
        self.assertTrue(done['meta']['duplicate'])
        self.assertEqual(self.store.knowledge.search(kind='external')['total'], 1)


if __name__ == '__main__':
    unittest.main()
