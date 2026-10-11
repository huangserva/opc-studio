"""返工边界回归：临时目录、阻塞替身，无真实模型或平台请求。"""
import json
import socket
import sqlite3
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from assistant_policy import paste_originals
from collect_adapters import ClueSourceAdapter, public_sources, public_tasks, public_gmail
from intel_store import Intel
from tests import test_assistant_edit_insert as assistant_fixture
from tests import test_intel_wechat_mp_3 as intel_fixture
from tests.test_intel_wechat_mp_4 import LINK
from tests import test_round4_intel_rate_limit as concurrency_fixture


class PasteFixTests(unittest.TestCase):
    setUp = assistant_fixture.EditInsertTests.setUp
    tearDown = assistant_fixture.EditInsertTests.tearDown
    turn = assistant_fixture.EditInsertTests.turn

    def prepare(self, message):
        self.turn(message)
        self.job['prompt'] = message
        self.agent.execute('read_work', {'slug': self.slug}, self.job)

    def edit(self, operations):
        return self.agent.execute('edit_work', {'slug': self.slug, 'base_version': 'v0002',
                                  'summary': '边界回归', 'operations': operations}, self.job)

    def test_instruction_word_cannot_replace_the_complete_paste(self):
        self.prepare('把下面贴文贴上，再补一句过渡。原文：今天发布甲方案。')
        with self.assertRaisesRegex(ValueError, '完整复制'):
            self.edit([{'op': 'insert_text', 'text_source': 'paste', 'html': '<p>贴文</p>'},
                       {'op': 'insert_text', 'text_source': 'compose', 'html': '<p>今天发布乙方案。新过渡。</p>'}])
        self.assertEqual(self.store.meta(self.slug)['head'], 'v0002')

    def test_rewrite_mixed_into_paste_is_rejected_atomically(self):
        self.prepare('把下面贴文贴上，再补一句过渡。原文：今天发布甲方案。')
        with self.assertRaisesRegex(ValueError, '完整复制'):
            self.edit([{'op': 'insert_text', 'text_source': 'paste', 'html': '<p>今天发布甲方案。新过渡。</p>'},
                       {'op': 'insert_text', 'text_source': 'compose', 'html': '<p>过渡。</p>'}])
        self.assertEqual(self.store.meta(self.slug)['head'], 'v0002')

    def test_compose_cannot_fulfil_the_paste_requirement(self):
        self.prepare('把下面贴文贴上，再补一句过渡。原文：今天发布甲方案。')
        with self.assertRaisesRegex(ValueError, '不能改写或编造'):
            self.edit([{'op': 'insert_text', 'text_source': 'compose', 'html': '<p>今天发布甲方案。</p>'}])

    def test_mixed_turn_requires_explicit_sources(self):
        self.prepare('把下面贴文贴上，再补一句过渡。原文：今天发布甲方案。')
        with self.assertRaisesRegex(ValueError, 'text_source'):
            self.edit([{'op': 'insert_text', 'html': '<p>今天发布甲方案。</p>'}])

    def test_complete_paste_and_separate_compose_save_one_version(self):
        self.prepare('把下面贴文贴上，再补一句过渡。原文：今天发布甲方案。')
        result = self.edit([{'op': 'insert_text', 'text_source': 'paste', 'html': '<p>今天发布甲方案。</p>'},
                            {'op': 'insert_text', 'text_source': 'compose', 'html': '<p>接下来继续观察。</p>'}])
        self.assertEqual(result['version'], 'v0003')
        texts = [block['text'] for block in self.store.detail(self.slug)['blocks']]
        self.assertIn('今天发布甲方案。', texts)
        self.assertIn('接下来继续观察。', texts)

    def test_unlabelled_ambiguous_paste_fails_closed(self):
        self.prepare('把这段贴文贴上，再补一句过渡。')
        with self.assertRaisesRegex(ValueError, '原文范围不清楚'):
            self.edit([{'op': 'insert_text', 'text_source': 'paste', 'html': '<p>贴文</p>'}])

    def test_explicit_paragraph_and_html_entities(self):
        self.prepare('把这段贴文贴上，再补一句过渡。指定原文段落：甲 & 乙\n第二行。')
        self.edit([{'op': 'insert_text', 'text_source': 'paste', 'html': '<p>甲 &amp; 乙</p><p>第二行。</p>'},
                   {'op': 'insert_text', 'text_source': 'compose', 'html': '<p>过渡。</p>'}])
        self.assertEqual(self.store.meta(self.slug)['head'], 'v0003')


class ProbeFixTests(intel_fixture.StoreTestCase):
    def setUp(self):
        super().setUp()
        guard = patch.object(socket.socket, 'connect', side_effect=AssertionError('禁止出网'))
        guard.start()
        self.addCleanup(guard.stop)

    def test_zero_probe_budget_reads_nothing(self):
        result = self.intel.discover_albums_from_links(intel_fixture.BIZ,
                    [LINK, LINK.replace('mid=123','mid=124'), LINK.replace('mid=123','mid=125')], max_reads=0)
        self.assertEqual(result, 0)
        self.assertEqual(self.article_client.bodies, [])

    def test_persistence_failure_retains_real_probe_count_in_receipt(self):
        link = LINK
        with patch.object(self.intel, 'local_album_links', return_value=[link]), \
             patch.object(self.intel, 'remember_albums', side_effect=sqlite3.OperationalError('fixture')):
            job = self.go(mode='history', bodies=3, sogou=False)
        self.assertEqual(self.article_client.bodies, [link])
        self.assertIn('探测读取 1 篇', job['detail'])
        self.assertIn('正文读取 0 篇', job['detail'])
        self.assertNotIn(job['id'], self.intel.workers)

    def test_client_initialization_failure_finishes_and_cleans_worker(self):
        with patch.object(self.intel, 'client', side_effect=RuntimeError('fixture')):
            job = self.go(mode='history', sogou=False)
        self.assertEqual(job['status'], 'partial')
        self.assertNotIn(job['id'], self.intel.workers)


class WorkerFixTests(concurrency_fixture.Round4TestCase):
    def start_aged_worker(self):
        self.intel.subscribe({'fakeid': concurrency_fixture.BIZ, 'name': '测试'})
        job = self.intel.run({'fakeid': concurrency_fixture.BIZ, 'mode': 'history', 'sogou': False})
        self.entered()
        with self.intel.db() as c:
            c.execute("UPDATE jobs SET started_at='2000-01-01T00:00:00+00:00' WHERE id=?", (job['id'],))
        return job

    def test_live_aged_worker_keeps_serial_slot(self):
        job = self.start_aged_worker()
        self.assertTrue(self.intel.status()['busy'])
        self.assertEqual(self.intel.job(job['id'])['status'], 'running')
        repeated = self.intel.run({'fakeid': concurrency_fixture.BIZ, 'mode': 'history', 'sogou': False})
        self.assertEqual(repeated['id'], job['id'])
        self.assertTrue(repeated['reused'])
        self.assertEqual(self.public.calls, 1)
        self.release()
        self.intel.wait_for(job['id'], 5)
        self.assertFalse(self.intel.status()['busy'])

    def test_second_intel_instance_does_not_interrupt_local_live_worker(self):
        job = self.start_aged_worker()
        other = Intel(self.store, public_factory=lambda s: concurrency_fixture.NullPublic())
        active = other.active_job()
        self.assertEqual(active['id'], job['id'])
        self.assertEqual(active['status'], 'running')

    def test_interrupted_state_is_not_overwritten_by_late_worker(self):
        job = self.start_aged_worker()
        with self.intel.db() as c:
            c.execute("UPDATE jobs SET status='interrupted',reason='fixture stop' WHERE id=?", (job['id'],))
        self.assertEqual(self.intel.active_job()['id'], job['id'])
        self.release()
        ended = self.intel.wait_for(job['id'], 5)
        self.assertEqual(ended['status'], 'interrupted')
        self.assertEqual(ended['reason'], 'fixture stop')
        self.assertEqual(ended['detail'], '')
        self.assertFalse(self.intel.status()['busy'])


class StatusFixTests(unittest.TestCase):
    def test_white_lists_reject_nested_values_and_wrong_scalar_types(self):
        material = {'body': 'synthetic-session-content'}
        result = [public_sources([{'id': material, 'name': material, 'count': material, 'account': material}]),
                  public_tasks([{'id': material, 'kind': material, 'enabled': material, 'running': material}]),
                  public_gmail({'account': material, 'query': material, 'count': True, 'captured_at': material,
                                'has_more': material})]
        self.assertNotIn('synthetic-session-content', json.dumps(result))
        self.assertEqual(result[0][0]['count'], 0)
        self.assertEqual(result[1][0]['enabled'], False)
        self.assertEqual(result[2]['count'], 0)

    def test_wechat_detail_uses_normalized_counts(self):
        client = SimpleNamespace(clue_status=lambda: {'processed_versions': 'synthetic-session-content',
                                 'pending_versions': {'body': 'synthetic-session-content'}, 'has_pending': True},
                                 status=lambda: {})
        adapter = ClueSourceAdapter('wechat_msg', SimpleNamespace(wechat_source=client))
        with patch.object(adapter, 'sources', return_value=[]), \
             patch('collect_adapters.clue_totals', return_value=[]), \
             patch('collect_adapters.schedule_tasks', return_value=[]):
            result = adapter.status()
        self.assertNotIn('synthetic-session-content', json.dumps(result))
        self.assertEqual(result['count'], 0)
        self.assertIn('已检查 0 个版本，待分析 未知 个', result['detail'])

    def test_public_fields_keep_frontend_shapes(self):
        self.assertEqual(public_sources([{'id': 's', 'name': '测试', 'count': 3, 'account': '测试账号'}]),
                         [{'id': 's', 'name': '测试', 'count': 3, 'account': '测试账号'}])
        self.assertEqual(public_tasks([{'id': 't', 'kind': 'demo', 'enabled': True, 'running': False}]),
                         [{'id': 't', 'kind': 'demo', 'enabled': True, 'running': False}])
        self.assertEqual(public_tasks(None), None)


class PasteBoundaryTests(unittest.TestCase):
    def test_complete_fence_and_quoted_paragraph(self):
        self.assertEqual(paste_originals('把这段贴文贴上\n```text\n甲\n乙\n```'), ('甲\n乙',))
        self.assertEqual(paste_originals('把这段贴文贴上：「甲乙」'), ('甲乙',))

    def test_transition_quote_does_not_replace_multiline_original(self):
        self.assertEqual(paste_originals('把下面贴文贴上，再补一句过渡「接下来」。\n今天发布甲方案。'),
                         ('今天发布甲方案。',))
        self.assertEqual(paste_originals('把下面贴文贴上，再补一句过渡「接下来」。'), ())

    def test_empty_marker_and_inline_body_are_not_original_spans(self):
        self.assertEqual(paste_originals('把下面贴文贴上，再补一句。原文：'), ())
        self.assertEqual(paste_originals('把下面贴文贴上。甲方案\n乙方案'), ())
