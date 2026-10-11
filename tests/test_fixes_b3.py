"""原文边界/链接与启动失败登记：临时工作区、假线程，无真实模型或外网。"""
from isolation_patches import target_start
import html
import unittest
from unittest.mock import patch

import intel_store
from assistant_policy import paste_originals
from tests import test_fixes_b2 as fixtures
from tests import test_round4_intel_rate_limit as concurrency


class PasteBoundaryFixTests(unittest.TestCase):
    setUp = fixtures.PasteFixTests.setUp
    tearDown = fixtures.PasteFixTests.tearDown
    turn = fixtures.PasteFixTests.turn
    prepare = fixtures.PasteFixTests.prepare
    edit = fixtures.PasteFixTests.edit

    def paste(self, text):
        return self.edit([{'op': 'insert_text', 'text_source': 'paste',
                           'html': '<p>' + html.escape(text).replace('\n', '<br>') + '</p>'}])

    def test_body_marker_cannot_authorize_only_the_last_part(self):
        body = '研究表明。\n原文：据某某报道，后文仍是原文。'
        self.prepare('把下面贴文贴上\n' + body)
        with self.assertRaisesRegex(ValueError, '完整复制'):
            self.paste('据某某报道，后文仍是原文。')
        with self.assertRaisesRegex(ValueError, '完整复制'):
            self.paste('原文：据某某报道，后文仍是原文。')
        self.assertEqual(self.store.meta(self.slug)['head'], 'v0002')
        saved = self.paste(body)
        self.assertEqual(saved['version'], 'v0003')
        self.assertIn('研究表明。', self.store.detail(self.slug)['blocks'][-1]['text'])

    def test_first_body_label_keeps_all_later_labels(self):
        body = '研究表明。\n原文：后半段。'
        self.prepare('把下面贴文贴上\n原文：' + body)
        with self.assertRaisesRegex(ValueError, '完整复制'):
            self.paste('后半段。')
        self.assertEqual(self.paste(body)['version'], 'v0003')

    def test_inline_label_after_prose_is_not_a_boundary(self):
        message = '把下面贴文贴上：研究表明。原文：后半段。'
        self.prepare(message)
        self.assertEqual(paste_originals(message), ())
        with self.assertRaisesRegex(ValueError, '换行后再贴原文'):
            self.paste('后半段。')
        self.assertEqual(self.store.meta(self.slug)['head'], 'v0002')

    def test_partial_inline_quotes_fail_closed_in_both_forms(self):
        for message in ['把这段贴文贴上：「标题」今天发布甲方案的正文很长。',
                        '把这段贴文贴上：“标题”今天发布甲方案。']:
            with self.subTest(message=message):
                self.prepare(message)
                self.assertEqual(paste_originals(message), ())
                with self.assertRaisesRegex(ValueError, '换行后再贴原文'):
                    self.paste('标题')
                self.assertEqual(self.store.meta(self.slug)['head'], 'v0002')
        self.assertEqual(paste_originals('把「标题」贴上，今天发布甲方案。'), ())

    def test_complete_quote_and_instruction_label_still_work(self):
        for message in ['把这段贴文贴上：「甲乙」', '把这段贴文贴上：“甲乙”',
                        '把「甲乙」贴上', '将“甲乙”插入']:
            self.assertEqual(paste_originals(message), ('甲乙',))
        self.assertEqual(paste_originals('把下面贴文贴上，再补一句过渡。原文：甲\n原文：乙'),
                         ('原文：甲\n原文：乙', '甲\n原文：乙'))

    def test_inline_prose_clarification_is_explicit(self):
        self.prepare('把下面贴文贴上。甲方案\n乙方案')
        with self.assertRaisesRegex(ValueError, '请换行后再贴原文'):
            self.paste('乙方案')
        self.assertEqual(self.store.meta(self.slug)['head'], 'v0002')

    def test_inline_quoted_body_cannot_be_discarded_for_the_next_line(self):
        self.prepare('把下面贴文贴上「正文甲」\n正文乙')
        self.assertEqual(paste_originals(self.job['prompt']), ())
        with self.assertRaisesRegex(ValueError, '请换行后再贴原文'):
            self.paste('正文乙')
        self.assertEqual(self.store.meta(self.slug)['head'], 'v0002')

    def test_new_anchor_destinations_are_rejected_atomically(self):
        self.prepare('把下面贴文贴上，再补一句过渡。原文：今天发布甲方案。')
        for markup in ['<a href="https://evil.example">今天发布甲方案。</a>',
                       '<A HREF="mailto:other@example.com">今天发布甲方案。</A>',
                       '<a href="https&#58;//evil.example">今天发布甲方案。</a>',
                       '<a href="https://example.com" href="https://evil.example">今天发布甲方案。</a>']:
            with self.subTest(markup=markup), self.assertRaisesRegex(ValueError, '原文里没有的链接'):
                self.edit([{'op': 'insert_text', 'text_source': 'compose', 'html': '<p>新的过渡。</p>'},
                           {'op': 'insert_text', 'text_source': 'paste', 'html': markup}])
            self.assertEqual(self.store.meta(self.slug)['head'], 'v0002')

    def test_original_url_may_be_preserved_but_not_extended(self):
        body = '今天发布甲方案。 https://example.com/post'
        self.prepare('把下面贴文贴上\n' + body)
        with self.assertRaisesRegex(ValueError, '原文里没有的链接'):
            self.edit([{'op': 'insert_text', 'text_source': 'paste',
                        'html': '<p>今天发布甲方案。 <a href="https://example.com/post/extra">https://example.com/post</a></p>'}])
        saved = self.edit([{'op': 'insert_text', 'text_source': 'paste',
                           'html': '<p>今天发布甲方案。 <a href="https://example.com/post">https://example.com/post</a></p>'}])
        self.assertEqual(saved['version'], 'v0003')
        self.assertIn('href="https://example.com/post"', self.store.detail(self.slug)['blocks'][-1]['html'])


class WorkerStartFixTests(concurrency.Round4TestCase):
    def test_start_failure_cleans_both_registries_under_the_lock(self):
        self.intel.subscribe({'fakeid': concurrency.BIZ, 'name': '测试'})
        failed = []

        def fail_start(worker):
            failed.append(worker)
            self.assertTrue(intel_store._PROCESS_WORKERS_LOCK._is_owned())
            self.assertIn(worker, self.intel.workers.values())
            self.assertIn(worker, intel_store._PROCESS_WORKERS.values())
            raise RuntimeError('合成线程启动失败')

        with target_start(lambda worker: worker in self.intel.workers.values(), fail_start):
            with self.assertRaisesRegex(ValueError, '合成线程启动失败'):
                self.intel.run({'fakeid': concurrency.BIZ, 'mode': 'history', 'sogou': False})
        self.assertEqual(len(failed), 1)
        self.assertNotIn(failed[0], self.intel.workers.values())
        self.assertNotIn(failed[0], intel_store._PROCESS_WORKERS.values())


if __name__ == '__main__':
    unittest.main()
