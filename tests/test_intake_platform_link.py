"""回收站接三家平台链接：落库 meta、如实回执、换 token 不重复入库。

这一份是 impl-platform-links 的集成用例：链接识别本身在 tests/test_collect_links.py，
这里只核对回收站与知识库**接到识别之后**的行为。编号一律用公开格式的合成示例。
"""
import contextlib
import tempfile
import unittest
from unittest.mock import patch

import collect_links
import external_reference
import intake_store
from tests.intake_dependency_patches import guard_intake_dependencies, patch_intake_dependency
from external_reference import metadata as reference_metadata
from intake_store import Intake
from knowledge_store import Knowledge, identifier
from server import Store

XHS_NOTE = '64f0a1b20000000012034567'
XHS_URL = 'https://www.xiaohongshu.com/explore/%s?xsec_token=AAA111&xsec_source=pc_share' % XHS_NOTE
XHS_URL_OTHER_TOKEN = 'https://www.xiaohongshu.com/explore/%s?xsec_token=BBB222' % XHS_NOTE
XHS_DISCOVERY = 'https://www.xiaohongshu.com/discovery/item/%s?source=webshare' % XHS_NOTE
DOUYIN_URL = 'https://www.douyin.com/video/7412345678901234567?utm_source=share&tt_from=copy_link'
DOUYIN_SLASH = 'https://www.douyin.com/video/7412345678901234567/'
DOUYIN_USER = 'https://www.douyin.com/user/MS4wLjABAAAA' + 'a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6'
DOUYIN_USER_SLASH = DOUYIN_USER + '/'
DOUYIN_SHORT = 'https://v.douyin.com/iRb7Xqk0/'
XHS_SHORT = 'http://xhslink.com/a/AbCdEf123'
SPH_URL = 'https://weixin.qq.com/sph/Aa1Bb2Cc3Dd'
REMIO_DOWN = '联网服务未完成查询，请核对网页地址及 remio 连接后重试'
# 识别接入之前（旧口径）外部收藏的去重键就是 host+path+query，原样带 query。
XHS_LEGACY_CANONICAL = XHS_URL
XHS_LEGACY_ID = identifier('external-references', XHS_LEGACY_CANONICAL)


@contextlib.contextmanager
def identification_off():
    """把平台识别关掉，只在这段里重现旧版登记三家长链接时的口径。"""
    with patch.object(collect_links, 'identify', lambda url: None), \
            patch.object(external_reference, 'identify_platform', lambda url: None):
        yield


class FakeWeb:
    """Stands in for assistant_web.WebTools; the only fetch path these tests use."""

    def __init__(self, pages=None, error=None, title='合成页面'):
        self.pages, self.error, self.title, self.calls = pages or [], error, title, []

    def __call__(self):
        return self

    def fetch(self, args, job):
        self.calls.append(dict(args))
        if self.error:
            raise ValueError(self.error)
        index = len(self.calls) - 1
        page = self.pages[index] if index < len(self.pages) else ''
        total = sum(len(p) for p in self.pages)
        start = args.get('start', 0)
        end = min(start + args.get('max_chars', 16000), total)
        body = ''.join(self.pages)
        return {'url': args['url'], 'title': self.title, 'markdown': body[start:end], 'start': start,
                'total_chars': total, 'truncated': end < total, 'next_start': end if end < total else None}


class PlatformLinkTests(unittest.TestCase):
    def setUp(self):
        guard_intake_dependencies(self)
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(self.tmp.name)
        self.store._knowledge = Knowledge(self.store, sources=[])
        self.intake = Intake(self.store.knowledge, self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def fake_fetch(self, pages=None, error=None, title='合成页面'):
        fake = FakeWeb(pages, error, title)
        patch_intake_dependency(self, 'WebTools', fake)
        return fake

    def doc(self, item):
        return self.store.knowledge.document(item['document_id'])

    def drop(self, url):
        saved = self.intake.receive({'kind': 'link', 'url': url, 'reason': '合成收藏理由'})
        return self.intake.resolve(saved['id'])

    def test_xiaohongshu_link_records_platform_and_note_id(self):
        self.fake_fetch(error=REMIO_DOWN)
        done = self.drop(XHS_URL)
        self.assertEqual(done['meta']['source_platform'], 'xiaohongshu')
        self.assertEqual(done['meta']['source_item_id'], XHS_NOTE)
        self.assertEqual(done['meta']['source_label'], '小红书')
        # 列表与筛选直接读顶层字段，不用翻 meta。
        self.assertEqual(done['source_platform'], 'xiaohongshu')
        self.assertEqual(done['source_label'], '小红书')
        self.assertEqual(self.intake.listing()['items'][0]['source_platform'], 'xiaohongshu')

    def test_xiaohongshu_failure_receipt_is_human_and_says_the_body_was_not_read(self):
        self.fake_fetch(error=REMIO_DOWN)
        done = self.drop(XHS_URL)
        self.assertEqual(done['status'], 'failed')
        self.assertEqual(done['outcome'], 'failed')
        self.assertIn('小红书正文需要登录浏览器，本机不代跑', done['error'])
        self.assertIn('联网服务未就绪（remio 未运行）', done['error'])
        self.assertIn('未读到作品正文', done['content_scope'])
        self.assertIn('小红书正文需要登录浏览器，本机不代跑', done['content_scope'])
        self.assertNotIn('已抓取正文', done['content_scope'])
        self.assertIn('未读到作品正文', done['state'])
        record = self.doc(done)
        self.assertEqual(record['body'], '')
        self.assertIs(record['provenance']['fetched'], False)
        self.assertIn('未读到作品正文', record['provenance']['content_scope'])

    def test_knowledge_dedupe_key_drops_the_token_but_the_openable_link_keeps_it(self):
        self.fake_fetch(error=REMIO_DOWN)
        done = self.drop(XHS_URL)
        provenance = self.doc(done)['provenance']
        self.assertEqual(provenance['platform'], '小红书')
        self.assertEqual(provenance['canonical_url'], 'https://www.xiaohongshu.com/explore/' + XHS_NOTE)
        self.assertEqual(provenance['source_item_id'], XHS_NOTE)
        self.assertEqual(provenance['sourceUrl'], XHS_URL)
        self.assertIn('xsec_token=AAA111', provenance['share_url'])

    def test_the_same_note_with_another_token_files_one_document(self):
        self.fake_fetch(error=REMIO_DOWN)
        first = self.drop(XHS_URL)
        again = self.drop(XHS_URL_OTHER_TOKEN)
        self.assertNotEqual(first['id'], again['id'])
        self.assertEqual(first['document_id'], again['document_id'])
        self.assertTrue(again['meta']['duplicate'])
        self.assertEqual(again['outcome'], 'duplicate')
        self.assertEqual(self.store.knowledge.search(kind='external')['total'], 1)

    def test_the_same_note_from_another_link_form_files_one_document(self):
        self.fake_fetch(error=REMIO_DOWN)
        first = self.drop(XHS_URL)
        again = self.drop(XHS_DISCOVERY)
        self.assertEqual(first['document_id'], again['document_id'])
        self.assertEqual(self.store.knowledge.search(kind='external')['total'], 1)

    # ── M1：旧库主键衔接 ────────────────────────────────────────────────────

    def test_a_link_already_filed_under_the_old_key_reuses_that_document(self):
        """旧库里已按 host+path+query 登记过的链接：复用旧 id，判为已存在。"""
        self.fake_fetch(error=REMIO_DOWN)
        with identification_off():
            legacy = self.drop(XHS_URL)
        self.assertEqual(legacy['document_id'], XHS_LEGACY_ID)
        self.assertEqual(self.doc(legacy)['provenance']['canonical_url'], XHS_LEGACY_CANONICAL)
        self.assertNotIn('source_platform', legacy['meta'])
        self.assertEqual(self.store.knowledge.search(kind='external')['total'], 1)
        again = self.drop(XHS_URL)
        self.assertEqual(again['document_id'], XHS_LEGACY_ID)
        self.assertTrue(again['meta']['duplicate'])
        self.assertEqual(again['outcome'], 'duplicate')
        self.assertEqual(self.store.knowledge.search(kind='external')['total'], 1)
        # 回收项上仍然记到了平台与作品编号，只是正文归到旧那篇。
        self.assertEqual(again['meta']['source_item_id'], XHS_NOTE)
        self.assertEqual(again['knowledge_url'], '/knowledge.html?document=' + XHS_LEGACY_ID)

    def test_an_old_record_under_another_link_form_is_not_matched(self):
        """边界：旧 canonical 带 path，旧库那条 /explore/ 记录对不上新的 /discovery/item/ 形态。"""
        self.fake_fetch(error=REMIO_DOWN)
        with identification_off():
            legacy = self.drop(XHS_URL)
        again = self.drop(XHS_DISCOVERY)
        self.assertNotEqual(again['document_id'], legacy['document_id'])
        self.assertEqual(self.store.knowledge.search(kind='external')['total'], 2)

    def test_without_an_old_record_the_new_key_is_used(self):
        self.fake_fetch(error=REMIO_DOWN)
        done = self.drop(XHS_URL)
        self.assertNotEqual(done['document_id'], XHS_LEGACY_ID)
        self.assertEqual(self.doc(done)['provenance']['canonical_url'],
                         'https://www.xiaohongshu.com/explore/' + XHS_NOTE)

    def test_an_old_record_with_another_token_is_not_matched(self):
        """边界：旧 canonical 带 query，换 token 的旧记录对不上，仍会多出一篇。"""
        self.fake_fetch(error=REMIO_DOWN)
        with identification_off():
            legacy = self.drop(XHS_URL)
        again = self.drop(XHS_URL_OTHER_TOKEN)
        self.assertNotEqual(again['document_id'], legacy['document_id'])
        self.assertNotIn('duplicate', again['meta'])
        self.assertEqual(self.store.knowledge.search(kind='external')['total'], 2)

    def test_short_links_keep_their_own_old_key_and_are_not_matched(self):
        """短链没有去重地址，不参与旧键查找；同一短链新旧口径本来就是同一个键。"""
        self.fake_fetch(error=REMIO_DOWN)
        with identification_off():
            legacy = self.drop(XHS_SHORT)
        again = self.drop(XHS_SHORT)
        self.assertEqual(again['document_id'], legacy['document_id'])
        self.assertEqual(self.store.knowledge.search(kind='external')['total'], 1)

    # ── M2：抖音末尾斜杠 ────────────────────────────────────────────────────

    def test_douyin_trailing_slash_files_one_document(self):
        """带与不带末尾斜杠要落在同一个 document_id，不能只比逻辑键。"""
        self.fake_fetch(error=REMIO_DOWN)
        first = self.drop(DOUYIN_URL)
        again = self.drop(DOUYIN_SLASH)
        self.assertEqual(first['document_id'], again['document_id'])
        self.assertTrue(again['meta']['duplicate'])
        self.assertEqual(again['outcome'], 'duplicate')
        self.assertEqual(self.store.knowledge.search(kind='external')['total'], 1)
        self.assertEqual(self.doc(again)['provenance']['canonical_url'],
                         'https://www.douyin.com/video/7412345678901234567')

    def test_douyin_account_link_with_trailing_slash_files_one_document(self):
        self.fake_fetch(error=REMIO_DOWN)
        first = self.drop(DOUYIN_USER)
        again = self.drop(DOUYIN_USER_SLASH)
        self.assertEqual(first['document_id'], again['document_id'])
        self.assertEqual(self.store.knowledge.search(kind='external')['total'], 1)
        self.assertEqual(self.doc(again)['provenance']['canonical_url'],
                         'https://www.douyin.com/user/' + DOUYIN_USER.split('/user/')[1])

    def test_douyin_link_says_it_is_a_js_rendered_page(self):
        self.fake_fetch(error=REMIO_DOWN)
        done = self.drop(DOUYIN_URL)
        self.assertEqual(done['meta']['source_platform'], 'douyin')
        self.assertEqual(done['meta']['source_item_id'], '7412345678901234567')
        self.assertIn('抖音是 JS 渲染页，公开抓取拿不到正文', done['error'])
        self.assertIn('未读到作品正文', done['content_scope'])
        provenance = self.doc(done)['provenance']
        self.assertEqual(provenance['platform'], '抖音')
        self.assertEqual(provenance['canonical_url'], 'https://www.douyin.com/video/7412345678901234567')

    def test_channels_link_says_there_is_no_public_entry(self):
        self.fake_fetch(error=REMIO_DOWN)
        done = self.drop(SPH_URL)
        self.assertEqual(done['meta']['source_platform'], 'channels')
        self.assertEqual(done['meta']['source_item_id'], 'Aa1Bb2Cc3Dd')
        self.assertIn('视频号没有公开的抓取入口', done['error'])
        self.assertIn('未读到作品正文', done['content_scope'])
        self.assertEqual(self.doc(done)['provenance']['canonical_url'], 'https://weixin.qq.com/sph/Aa1Bb2Cc3Dd')

    def test_short_link_says_the_work_id_is_still_unknown(self):
        self.fake_fetch(error=REMIO_DOWN)
        done = self.drop(XHS_SHORT)
        self.assertEqual(done['meta']['source_platform'], 'xiaohongshu')
        self.assertEqual(done['meta']['source_item_id'], '')
        self.assertIs(done['meta']['source_short_link'], True)
        self.assertIn('短链', done['error'])
        self.assertIn('未读到作品正文', done['content_scope'])

    def test_the_raw_backend_error_stays_beside_the_human_receipt(self):
        self.fake_fetch(error=REMIO_DOWN)
        done = self.drop(XHS_URL)
        self.assertEqual(done['meta']['fetch_error_raw'], REMIO_DOWN)
        self.assertIn('小红书正文需要登录浏览器，本机不代跑', done['meta']['fetch_error'])

    def test_an_older_row_gets_the_platform_mark_when_it_is_parsed(self):
        """改前已落库的行 meta 里没有平台字段：重新解析时补上，不靠旧数据重贴。"""
        self.fake_fetch(error=REMIO_DOWN)
        saved = self.intake.receive({'kind': 'link', 'url': XHS_URL})
        with self.intake.db() as connection:
            connection.execute('''UPDATE drops SET meta='{"source":"intake_page"}',status='pending',error='' WHERE id=?''',
                               (saved['id'],))
        self.assertEqual(self.intake.detail(saved['id'])['meta'].get('source_platform'), None)
        done = self.intake.resolve(saved['id'])
        self.assertEqual(done['meta']['source_platform'], 'xiaohongshu')
        self.assertEqual(done['meta']['source_item_id'], XHS_NOTE)
        self.assertEqual(done['source_label'], '小红书')

    def test_login_wall_text_is_never_filed_as_the_work_body(self):
        wall = '扫码登录后查看更多内容，请先登录。' + '占位文字' * 200
        self.fake_fetch(pages=[wall])
        done = self.drop(XHS_URL)
        self.assertEqual(done['status'], 'failed')
        self.assertIs(done['meta']['fetched'], False)
        self.assertIs(done['meta']['login_wall'], True)
        self.assertEqual(done['text_chars'], 0)
        self.assertEqual(self.intake._raw(done['id'])['text'], '')
        self.assertIn('未读到作品正文', done['content_scope'])
        self.assertIn('登录墙', done['content_scope'])
        self.assertNotIn('占位文字', done['content_scope'])
        record = self.doc(done)
        self.assertEqual(record['body'], '')
        self.assertIn('未读到作品正文', record['provenance']['content_scope'])

    def test_empty_shell_text_is_never_filed_as_the_work_body(self):
        self.fake_fetch(pages=['页面加载中'])
        done = self.drop(DOUYIN_URL)
        self.assertEqual(done['status'], 'failed')
        self.assertIs(done['meta']['login_wall'], True)
        self.assertIn('空壳', done['content_scope'])
        self.assertEqual(self.doc(done)['body'], '')

    # ── M4：登录壳标题与 fetched 标记 ──────────────────────────────────────

    def test_login_wall_title_never_becomes_the_document_title(self):
        """壳子标题不能当文档标题，也不能进检索索引。"""
        shell = '请先登录查看完整内容' + '壳子文字占位' * 60
        self.fake_fetch(pages=[shell], title='请先登录查看完整内容')
        done = self.drop(XHS_URL)
        record = self.doc(done)
        self.assertNotIn('登录', record['title'])
        self.assertNotIn('壳子', record['title'])
        self.assertIs(record['provenance']['fetched'], False)
        self.assertEqual(record['body'], '')
        for term in ('壳子文字占位', '请先登录查看完整内容'):
            self.assertEqual(self.store.knowledge.search(query=term, kind='external')['total'], 0, term)
            self.assertEqual(self.store.knowledge.search(query=term, kind='external', mode='fts')['total'], 0, term)

    def test_a_manual_title_survives_the_login_wall(self):
        """有壳子时人工标题优先，口径与普通抓取失败一致。"""
        shell = '扫码登录后查看更多内容' + '壳子文字占位' * 60
        self.fake_fetch(pages=[shell], title='扫码登录后查看更多内容')
        saved = self.intake.receive({'kind': 'link', 'url': XHS_URL, 'title': '黄总自己写的标题'})
        done = self.intake.resolve(saved['id'])
        self.assertEqual(self.doc(done)['title'], '黄总自己写的标题')
        self.assertNotIn('title_pending', done['meta'])

    def test_shell_fetch_title_falls_back_like_an_ordinary_failure(self):
        """没人工标题时退回链接本身并标「标题待补」，不拿壳子标题。"""
        self.fake_fetch(pages=['页面加载中，请先登录'], title='页面加载中')
        done = self.drop(DOUYIN_URL)
        record = self.doc(done)
        self.assertEqual(record['title'], DOUYIN_URL)
        self.assertEqual(done['meta']['title_pending'], '标题待补')
        self.assertIs(record['provenance']['fetched'], False)
        self.assertNotIn('页面加载中', record['title'])

    def test_a_real_body_keeps_the_fetched_flag_true(self):
        """fetched 只跟「是否接受了正文」走：真读到正文照旧 true。"""
        body = '这条笔记正文足够长。' + '逐字保存。' * 40
        self.fake_fetch(pages=[body], title='一条真笔记')
        done = self.drop(XHS_URL)
        self.assertIs(self.doc(done)['provenance']['fetched'], True)
        self.assertEqual(self.doc(done)['title'], '一条真笔记')

    def test_a_real_body_behind_a_platform_link_is_still_kept(self):
        body = '这条笔记的正文写得很实在，' + '逐字保存的内容足够长，不会被当成空壳。' * 30
        self.fake_fetch(pages=[body])
        done = self.drop(XHS_URL)
        self.assertEqual(done['status'], 'resolved')
        self.assertIs(done['meta']['fetched'], True)
        self.assertNotIn('login_wall', done['meta'])
        self.assertIn('已抓取正文', done['content_scope'])
        self.assertEqual(self.doc(done)['body'], body)
        self.assertEqual(done['meta']['source_platform'], 'xiaohongshu')

    def test_an_ordinary_blog_link_behaves_exactly_as_before(self):
        self.fake_fetch(pages=['第一段。' * 10, '第二段。' * 10])
        done = self.drop('https://blog.example.com/posts/64f0a1b20000000012034567')
        self.assertEqual(done['status'], 'resolved')
        self.assertEqual(done['content_scope'], '已抓取正文，共 80 字')
        self.assertEqual(done['state'], '已存入知识库，已抓取正文，共 80 字')
        self.assertNotIn('source_platform', done['meta'])
        self.assertEqual(done['source_platform'], '')
        provenance = self.doc(done)['provenance']
        self.assertEqual(provenance['platform'], 'blog.example.com')
        self.assertEqual(provenance['canonical_url'],
                         'https://blog.example.com/posts/64f0a1b20000000012034567')

    def test_an_ordinary_failing_link_keeps_the_old_wording(self):
        self.fake_fetch(error='联网服务响应超时，请稍后重试')
        done = self.drop('https://blog.example.com/slow')
        self.assertEqual(done['error'], '联网服务响应超时，请稍后重试')
        self.assertEqual(done['content_scope'], '仅收藏链接；原文正文未读取（抓取失败：联网服务响应超时，请稍后重试）')
        self.assertEqual(done['state'], '抓取失败：联网服务响应超时，请稍后重试；已存入知识库（仅链接）')

    def test_x_link_handling_is_unchanged(self):
        self.fake_fetch(error=REMIO_DOWN)
        done = self.drop('https://x.com/some_author/status/1450000000000000000')
        provenance = self.doc(done)['provenance']
        self.assertEqual(provenance['platform'], 'X')
        self.assertEqual(provenance['canonical_url'], 'https://x.com/some_author/status/1450000000000000000')
        self.assertEqual(provenance['author_handle'], 'some_author')
        self.assertNotIn('source_platform', done['meta'])


class ReferenceMetadataTests(unittest.TestCase):
    def metadata(self, url, **extra):
        values, provenance = reference_metadata({'title': '合成收藏', 'url': url, **extra}, '2026-10-10T00:00:00+00:00')
        return values, provenance

    def test_platform_links_get_a_label_and_a_token_free_dedupe_key(self):
        _, provenance = self.metadata(XHS_URL)
        self.assertEqual(provenance['platform'], '小红书')
        self.assertEqual(provenance['canonical_url'], 'https://www.xiaohongshu.com/explore/' + XHS_NOTE)
        self.assertEqual(provenance['share_url'], 'https://www.xiaohongshu.com/explore/' + XHS_NOTE + '?xsec_token=AAA111&xsec_source=pc_share')
        self.assertEqual(provenance['source_item_id'], XHS_NOTE)
        self.assertEqual(provenance['sourceUrl'], XHS_URL)

    def test_short_links_stay_separate_documents_and_say_so(self):
        _, first = self.metadata(XHS_SHORT)
        _, second = self.metadata('http://xhslink.com/a/Zz99Yy88')
        self.assertEqual(first['platform'], '小红书')
        self.assertTrue(first['short_link'])
        self.assertNotEqual(first['canonical_url'], second['canonical_url'])
        self.assertEqual(first['source_item_id'], '')

    def test_non_platform_links_keep_the_plain_canonical(self):
        _, provenance = self.metadata('https://blog.example.com/posts/one?utm_source=share#top')
        self.assertEqual(provenance['platform'], 'blog.example.com')
        self.assertEqual(provenance['canonical_url'], 'https://blog.example.com/posts/one?utm_source=share')
        self.assertNotIn('share_url', provenance)
        self.assertNotIn('source_item_id', provenance)

    def test_wechat_article_links_are_not_read_as_channels(self):
        _, provenance = self.metadata('https://mp.weixin.qq.com/s/AbCdEfGhIjKlMnOpQrStUv')
        self.assertEqual(provenance['platform'], 'mp.weixin.qq.com')
        self.assertNotIn('source_item_id', provenance)

    def test_legacy_canonical_is_the_old_host_path_query_key(self):
        """旧口径：识别接入之前，去重键就是 host+path+query（query 原样保留）。"""
        self.assertEqual(external_reference.legacy_canonical(XHS_URL), XHS_LEGACY_CANONICAL)
        self.assertEqual(external_reference.legacy_document_id(XHS_URL), XHS_LEGACY_ID)
        # 换 token 就是另一个旧键；fragment 不算。
        self.assertNotEqual(external_reference.legacy_canonical(XHS_URL_OTHER_TOKEN), XHS_LEGACY_CANONICAL)
        self.assertEqual(external_reference.legacy_canonical('https://blog.example.com/a?x=1#top'),
                         'https://blog.example.com/a?x=1')
        self.assertEqual(external_reference.legacy_canonical('https://blog.example.com'), 'https://blog.example.com/')
        for bad in ('', 'javascript:alert(1)', 'ftp://xiaohongshu.com/explore/x', 'not a url'):
            self.assertEqual(external_reference.legacy_canonical(bad), '')
            self.assertEqual(external_reference.legacy_document_id(bad), '')


if __name__ == '__main__':
    unittest.main()
