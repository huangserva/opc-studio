"""fixes-b4：长过渡引号、链接边界、礼貌前缀、启动失败中断。临时工作区，无外网。"""
from isolation_patches import target_start
import html
import unittest
from unittest.mock import patch

import intel_store
from assistant_policy import paste_links_allowed, paste_originals
from tests import test_fixes_b2 as fixtures
from tests import test_round4_intel_rate_limit as concurrency


class PasteBoundaryB4Tests(unittest.TestCase):
    setUp = fixtures.PasteFixTests.setUp
    tearDown = fixtures.PasteFixTests.tearDown
    turn = fixtures.PasteFixTests.turn
    prepare = fixtures.PasteFixTests.prepare
    edit = fixtures.PasteFixTests.edit

    def paste(self, text):
        return self.edit([{'op': 'insert_text', 'text_source': 'paste',
                           'html': '<p>' + html.escape(text).replace('\n', '<br>') + '</p>'}])

    def test_long_compose_quote_cannot_be_replaced_by_the_next_line(self):
        message = '把下面贴文贴上，再补一句「今天发布甲方案的完整长文。」\n短'
        self.assertEqual(paste_originals(message), ())
        long = '这是一段明显超过二十个字的过渡说明文字继续写'
        self.assertGreater(len(long), 20)
        self.assertEqual(paste_originals(f'把下面贴文贴上，再补一句「{long}」\n短'), ())
        self.assertEqual(paste_originals('把下面贴文贴上，再补一句「甲方案。后面还有正文」\n短'), ())
        self.assertEqual(paste_originals('把下面贴文贴上，再补一句“今天发布甲方案的完整长文。”\n短'), ())
        self.prepare(message)
        with self.assertRaisesRegex(ValueError, '请换行后再贴原文'):
            self.paste('短')
        self.assertEqual(self.store.meta(self.slug)['head'], 'v0002')

    def test_short_transition_and_quote_without_a_next_line_still_work(self):
        self.assertEqual(paste_originals('把下面贴文贴上，再补一句过渡「接下来」。\n今天发布甲方案。'),
                         ('今天发布甲方案。',))
        self.assertEqual(paste_originals('把下面贴文贴上，再补一句「今天发布甲方案的完整长文。」'),
                         ('今天发布甲方案的完整长文。',))
        self.assertEqual(paste_originals('把下面贴文贴上，再补一句过渡。原文：甲\n原文：乙'),
                         ('原文：甲\n原文：乙', '甲\n原文：乙'))

    def test_polite_prefix_is_allowed_and_other_misses_stay_closed(self):
        body = '今天发布甲方案。'
        for message in [f'帮我把下面贴文贴上\n{body}',
                        f'麻烦把下面贴文贴上\n{body}',
                        f'请帮我把下面贴文贴上\n{body}',
                        f'请把下面贴文贴上\n{body}',
                        f'麻烦你把下面贴文贴上\n{body}']:
            self.assertEqual(paste_originals(message), (body,))
        self.prepare(f'帮我把下面贴文贴上\n{body}')
        self.assertEqual(self.paste(body)['version'], 'v0003')
        for message in ['贴上这段：\n今天发布甲方案。',
                        '贴文：\n今天发布甲方案。',
                        '把下面贴文贴到第一段后面\n今天发布甲方案。',
                        '把这段贴上作品里\n今天发布甲方案。',
                        '把下面贴文贴上。甲方案\n乙方案']:
            self.assertEqual(paste_originals(message), ())

    def test_saved_paste_keeps_the_url_without_the_chinese_period(self):
        body = '见 https://example.com/a。'
        self.prepare('把下面贴文贴上\n' + body)
        saved = self.edit([{'op': 'insert_text', 'text_source': 'paste',
                           'html': '<p>见 <a href="https://example.com/a">https://example.com/a</a>。</p>'}])
        self.assertEqual(saved['version'], 'v0003')
        self.assertIn('href="https://example.com/a"', self.store.detail(self.slug)['blocks'][-1]['html'])

    def test_saved_paste_rejects_a_scheme_glued_to_letters(self):
        self.prepare('把下面贴文贴上\n见 nothttps://evil.example/x')
        with self.assertRaisesRegex(ValueError, '原文里没有的链接'):
            self.edit([{'op': 'insert_text', 'text_source': 'paste',
                        'html': '<p>见 <a href="https://evil.example/x">nothttps://evil.example/x</a></p>'}])
        self.assertEqual(self.store.meta(self.slug)['head'], 'v0002')

    def test_unparseable_href_is_refused_as_a_missing_link(self):
        self.prepare('把下面贴文贴上\n见 https://a.com:xx/说明')
        with self.assertRaisesRegex(ValueError, '原文里没有的链接'):
            self.edit([{'op': 'insert_text', 'text_source': 'paste',
                        'html': '<p>见 <a href="https://a.com:xx/">https://a.com:xx/</a>说明</p>'}])
        self.assertEqual(self.store.meta(self.slug)['head'], 'v0002')


class LinkBoundaryTests(unittest.TestCase):
    def allowed(self, href, original):
        return paste_links_allowed(f'<a href="{href}">{href}</a>', original)

    def test_chinese_punctuation_and_han_do_not_join_the_url(self):
        original = '见 https://example.com/a。以及 mailto:a@b.c'
        self.assertTrue(self.allowed('https://example.com/a', original))
        self.assertFalse(self.allowed('https://example.com/a。以及', original))
        self.assertFalse(self.allowed('https://example.com/a。', original))
        self.assertTrue(self.allowed('mailto:a@b.c', original))
        self.assertTrue(self.allowed('https://example.com/post?x=1&y=2#top',
                                     '见 https://example.com/post?x=1&y=2#top'))
        self.assertFalse(self.allowed('https://example.com/post?x=1&y=2',
                                      '见 https://example.com/post?x=1&y=2#top'))
        self.assertFalse(self.allowed('https://example.com/post?x=1&y=2#other',
                                      '见 https://example.com/post?x=1&y=2#top'))

    def test_scheme_and_host_ignore_case_but_path_does_not(self):
        original = '见 HTTPS://Example.COM/Path 与 MAILTO:a@B.C'
        self.assertTrue(self.allowed('https://example.com/Path', original))
        self.assertTrue(self.allowed('HTTPS://Example.COM/Path', original))
        self.assertFalse(self.allowed('https://example.com/path', original))
        self.assertTrue(self.allowed('mailto:a@b.c', original))
        self.assertFalse(self.allowed('mailto:A@b.c', original))

    def test_scheme_needs_a_non_alphanumeric_boundary(self):
        self.assertFalse(self.allowed('https://evil.example/x', '见 nothttps://evil.example/x'))
        self.assertFalse(self.allowed('https://evil.example/x', '见 NOTHTTPS://evil.example/x'))
        self.assertTrue(self.allowed('https://example.com/a', '见https://example.com/a'))
        self.assertTrue(self.allowed('https://example.com/a', 'https://example.com/a'))
        self.assertFalse(self.allowed('javascript:alert(1)', '见 https://example.com/a'))
        self.assertFalse(self.allowed('/intel.html', '见 https://example.com/a'))
        self.assertFalse(self.allowed('//evil.example/a', '见 https://example.com/a'))

    def test_unparseable_urls_fail_closed(self):
        for bad in ('https://a.com:xx/', 'https://a.com:99999/', 'https://[::1/'):
            with self.subTest(bad=bad):
                self.assertFalse(self.allowed(bad, f'见 {bad} 说明'))
                self.assertFalse(self.allowed('https://a.com/', f'见 {bad}'))
                self.assertTrue(self.allowed('https://example.com/ok', f'见 {bad} 另有 https://example.com/ok'))
        self.assertTrue(self.allowed('https://example.com:8443/a', '见 https://example.com:8443/a'))
        self.assertFalse(self.allowed('https://example.com/a', '见 https://example.com:8443/a'))


class WorkerStartInterruptTests(concurrency.Round4TestCase):
    def test_start_failure_interrupts_the_row_after_releasing_the_lock(self):
        self.intel.subscribe({'fakeid': concurrency.BIZ, 'name': '测试'})
        observed = {}
        original = intel_store.Intel._finish_job

        def spy(store, job_id, status, reason='', **counts):
            observed['locked'] = intel_store._PROCESS_WORKERS_LOCK._is_owned()
            observed['status'] = status
            observed['reason'] = reason
            return original(store, job_id, status, reason, **counts)

        failed = []

        def fail_start(worker):
            failed.append(worker)
            self.assertTrue(intel_store._PROCESS_WORKERS_LOCK._is_owned())
            raise RuntimeError('合成线程启动失败')

        with target_start(lambda worker: worker in self.intel.workers.values(), fail_start), \
                patch.object(intel_store.Intel, '_finish_job', spy):
            with self.assertRaisesRegex(ValueError, '合成线程启动失败'):
                self.intel.run({'fakeid': concurrency.BIZ, 'mode': 'history', 'sogou': False})
        self.assertEqual(observed['locked'], False)
        self.assertEqual(observed['status'], 'interrupted')
        self.assertIn('没有启动', observed['reason'])
        self.assertEqual(self.intel.workers, {})
        self.assertNotIn(failed[0], intel_store._PROCESS_WORKERS.values())
        row = self.intel.jobs(1)[0]
        self.assertEqual(row['status'], 'interrupted')
        self.assertFalse(row['active'])
        self.assertIsNone(self.intel.active_job())
