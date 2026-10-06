"""Synthetic regression coverage for failed WeChat batches and safe recovery."""
import json
import sqlite3
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from clue_pipeline import CluePipeline
from knowledge_store import Knowledge, identifier
from server import Store


class ClueRetryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(self.tmp.name)
        self.store._knowledge = Knowledge(self.store, sources=[])
        self.pipeline = CluePipeline(self.store)
        self.config = {'model': 'synthetic', 'max_output_tokens': 4096, 'timeout_seconds': 10}
        self.pipeline.prepare_cache = lambda *args: {'index_status': 'ready'}
        self.pipeline.call_pi = lambda *args: self.fail('A test must provide a synthetic model response')
        self.calls = []

    def add(self, key, body, chat='synthetic-chat', age=0):
        modified = (datetime.now(timezone.utc) - timedelta(seconds=age)).isoformat()
        with self.store.knowledge.db() as db:
            db.execute('CREATE TEMP TABLE seen(id TEXT PRIMARY KEY)')
            self.store.knowledge._write(db, 'wechat', key, 'wechat', '合成原文', body,
                                       modified=modified, provenance={'chatroom_id': chat})
        return self.store.knowledge.document(identifier('wechat', key))

    @staticmethod
    def material(prompt):
        return json.loads(prompt.split('<source_material>', 1)[1].split('</source_material>', 1)[0])

    @staticmethod
    def candidate(ref, title='合成技术线索'):
        return {'title': title, 'summary': '合成工具进展，尚未实测。', 'relevance': '技术调研线索',
                'categories': ['technology'], 'kind': 'information', 'priority': 'normal',
                'quotes': [{'ref': ref}]}

    def response(self, prompt, mixed=False):
        current = next(item for item in self.material(prompt) if not item.get('context'))
        valid = self.candidate(current['evidence'][0]['ref'])
        return json.dumps({'clues': [valid, self.candidate('unknown-reference', '不合格合成候选')]
                           if mixed else [valid]}, ensure_ascii=False)

    def run_analysis(self, run='a', *, incremental=False, manual=False, cancel=None, deadline=None):
        task = {'body': '关注技术线索', 'wechat_mode': 'incremental' if incremental else 'once',
                'execution_trigger': 'manual' if manual else 'scheduled'}
        result = self.pipeline.analyze(run * 32, task, cancel or threading.Event(), self.config,
                                       time.monotonic() + 60 if deadline is None else deadline)
        self.assertIn(result[0], {'succeeded', 'partial', 'failed', 'cancelled', 'timeout'})
        data = json.loads(result[2])
        self.assertIsInstance(data['processed'], int)
        self.assertIsInstance(data['clues'], list)
        return result[0], data

    def seen(self, doc):
        with self.pipeline.db() as db:
            return bool(db.execute('SELECT 1 FROM seen WHERE document_id=? AND hash=?',
                                   (doc['id'], doc['hash'])).fetchone())

    def model(self, answer):
        def fake(identity, prompt, *args):
            self.calls.append((identity, prompt))
            return answer(prompt) if callable(answer) else answer
        self.pipeline.call_pi = fake

    def test_new_reference_contract_rejects_legacy_quotes_but_parse_compatibility_remains(self):
        doc = self.add('quote', '@Allen\u2005合成技术工具新增功能。')
        refs = self.pipeline.evidence_refs([doc])
        candidate = self.candidate(next(iter(refs)))
        candidate['quotes'] = [{'id': doc['id'], 'text': doc['body']}]
        answer = json.dumps({'clues': [candidate]}, ensure_ascii=False)
        self.assertEqual(len(self.pipeline.parse(answer, [doc])), 1)
        with self.assertRaises(ValueError):
            self.pipeline.parse(answer, [doc], refs)
        self.model(answer)
        status, data = self.run_analysis()
        self.assertEqual(status, 'failed')
        self.assertEqual(len(self.calls), 2)
        self.assertFalse(self.seen(doc))
        self.assertEqual(data['clues'], [])
        self.assertEqual(self.store.clues.listing()['total'], 0)

    def test_mixed_candidates_preserve_valid_clue_without_checkpoint_or_duplicate(self):
        doc = self.add('mixed', '合成技术消息，包括值得调查的引用能力。')
        self.model(lambda prompt: self.response(prompt, mixed=True))
        status, data = self.run_analysis()
        self.assertEqual(status, 'partial')
        self.assertEqual(data['processed'], 0)
        self.assertFalse(self.seen(doc))
        self.assertEqual(len(data['clues']), 1)
        self.assertEqual(data['deferred'], 1)
        error = data['errors'][0]
        self.assertTrue({'batch_id', 'message', 'attempts', 'state', 'documents', 'next_retry_at'} <= set(error))
        self.assertEqual(error['attempts'], 1)
        self.assertEqual(error['documents'], 1)
        self.assertEqual(error['state'], 'waiting')
        self.assertEqual(self.store.clues.listing()['total'], 1)
        original = data['clues'][0]
        status, retried = self.run_analysis('b', manual=True)
        self.assertEqual(status, 'partial')
        self.assertEqual(retried['clues'], [original])
        self.assertEqual(self.store.clues.listing()['total'], 1)
        self.assertFalse(self.seen(doc))
        self.assertEqual(len(self.calls), 4)

    def test_failed_empty_progress_is_failed_with_bounded_repair_and_receipt(self):
        doc = self.add('invalid', '合成坏响应的原始技术消息。')
        self.model('not-json')
        status, data = self.run_analysis(incremental=True)
        self.assertEqual(status, 'failed')
        self.assertEqual(len(self.calls), 2)
        self.assertFalse(self.seen(doc))
        self.assertEqual(data['processed'], 0)
        self.assertEqual(data['clues'], [])
        self.assertEqual(len(data['errors']), 1)
        self.assertEqual(data['errors'][0]['attempts'], 1)
        self.assertEqual(data['deferred'], 1)

    def test_retry_budget_cooldown_and_manual_override_survive_restart(self):
        doc = self.add('retry', '合成需要多次重试的技术消息。')
        with patch('clue_pipeline.time.time', return_value=1000):
            batch_id = self.pipeline.defer_batch([doc], 'synthetic', 'a' * 32, '合成校验失败', [])
            first = self.pipeline.retry_queue('synthetic')[0]
            self.assertEqual(first['attempts'], 1)
            self.assertEqual(first['next_retry_at'], datetime.fromtimestamp(1060, timezone.utc).isoformat())
            self.assertEqual(self.pipeline.pending(ready_only=True, model='synthetic', run_id='b' * 32), [])
            self.assertEqual(len(self.pipeline.pending(ready_only=True, model='synthetic', run_id='b' * 32, manual=True)), 1)
            self.assertEqual(self.pipeline.pending(ready_only=True, model='synthetic', run_id='a' * 32, manual=True), [])
        restarted = CluePipeline(self.store)
        with patch('clue_pipeline.time.time', return_value=1061):
            self.assertEqual(len(restarted.pending(ready_only=True, model='synthetic', run_id='b' * 32)), 1)
            self.assertEqual(restarted.defer_batch([doc], 'synthetic', 'b' * 32, '仍未通过', []), batch_id)
            second = restarted.retry_queue('synthetic')[0]
            self.assertEqual(second['attempts'], 2)
            self.assertEqual(second['next_retry_at'], datetime.fromtimestamp(1361, timezone.utc).isoformat())
        with patch('clue_pipeline.time.time', return_value=1362):
            restarted.defer_batch([doc], 'synthetic', 'c' * 32, '仍未通过', [])
        with patch('clue_pipeline.time.time', return_value=99999):
            third = restarted.retry_queue('synthetic')[0]
            self.assertEqual(third['attempts'], 3)
            self.assertEqual(third['state'], 'needs_review')
            self.assertIsNone(third['next_retry_at'])
            self.assertEqual(restarted.pending(ready_only=True, model='synthetic', run_id='d' * 32), [])
            self.assertEqual(len(restarted.pending(ready_only=True, model='synthetic', run_id='d' * 32, manual=True)), 1)
            self.assertFalse(self.seen(doc))

    def test_pending_excludes_deferred_before_limit_and_does_not_hide_older_new_messages(self):
        older = self.add('older-fresh', '合成较旧但尚未分析的消息。', chat='fresh', age=100)
        blocked = [self.add('blocked-' + str(i), '合成冷却消息 ' + str(i)) for i in range(35)]
        self.pipeline.defer_batch(blocked, 'synthetic', 'a' * 32, '合成冷却', [])
        ready = self.pipeline.pending(1, ready_only=True, model='synthetic', run_id='b' * 32)
        self.assertEqual([item['id'] for item in ready], [older['id']])
        self.assertEqual(len(self.pipeline.pending()), 30)

    def test_all_cooled_messages_remain_failed_and_pending_without_another_model_call(self):
        docs = [self.add('cooled-' + str(i), '合成已冷却的消息 ' + str(i)) for i in range(3)]
        self.pipeline.defer_batch(docs, 'synthetic', 'a' * 32, '合成原文引用未通过', [])
        self.model(lambda prompt: self.fail('Automatic execution must honor the cooling window'))
        status, data = self.run_analysis('b', incremental=True)
        self.assertEqual(status, 'failed')
        self.assertEqual(data['deferred'], 3)
        self.assertEqual(data['errors'][0]['documents'], 3)
        self.assertTrue(data['has_more'])
        self.assertEqual(self.calls, [])
        self.assertTrue(all(not self.seen(doc) for doc in docs))

    def test_primary_valid_candidate_survives_malformed_repair_without_false_checkpoint(self):
        doc = self.add('repair-invalid', '合成主调用已经发现有出处的技术线索')
        self.model(lambda prompt: self.response(prompt, mixed=True) if len(self.calls) == 1 else 'not-json')
        status, data = self.run_analysis()
        self.assertEqual(status, 'partial')
        self.assertEqual(data['processed'], 0)
        self.assertEqual(len(data['clues']), 1)
        self.assertFalse(self.seen(doc))
        self.assertEqual(self.store.clues.listing()['total'], 1)

    def test_fresh_incremental_progress_reports_deferred_message_count_not_batch_count(self):
        docs = [self.add('waiting-' + str(i), '合成一个失败批次中的消息 ' + str(i), chat='blocked')
                for i in range(3)]
        self.pipeline.defer_batch(docs, 'synthetic', 'a' * 32, '合成待重试批次', [])
        fresh = self.add('ready-message', '合成不受失败批次影响的新消息', chat='fresh')
        self.model('{"clues":[]}')
        status, data = self.run_analysis('b', incremental=True)
        self.assertEqual(status, 'partial')
        self.assertEqual(data['processed'], 1)
        self.assertTrue(self.seen(fresh))
        self.assertEqual(data['deferred'], 3)
        self.assertEqual(len(data['errors']), 1)

    def test_all_needs_review_batches_require_manual_override_and_only_one_attempt_per_run(self):
        doc = self.add('needs-review', '合成达到自动重试上限的技术消息')
        for run in ('a', 'b', 'c'):
            self.pipeline.defer_batch([doc], 'synthetic', run * 32, '合成持续校验失败', [])
        self.model('not-json')
        status, data = self.run_analysis('d', incremental=True)
        self.assertEqual(status, 'failed')
        self.assertEqual(self.calls, [])
        self.assertEqual(data['errors'][0]['state'], 'needs_review')
        status, data = self.run_analysis('e', incremental=True, manual=True)
        self.assertEqual(status, 'failed')
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(data['errors'][0]['attempts'], 4)
        self.assertFalse(self.seen(doc))

    def test_existing_scope_and_seen_checkpoint_survive_retry_schema_migration(self):
        seen = self.add('legacy-seen', '合成旧版已经完成的消息')
        pending = self.add('legacy-pending', '合成旧版尚未完成的消息')
        self.pipeline.checkpoint([seen], 'old')
        with self.pipeline.db() as db:
            db.execute('INSERT INTO scopes VALUES(?,?,?,0)', ('a' * 32, 'synthetic',
                       json.dumps([{'id': doc['id'], 'hash': doc['hash']} for doc in (seen, pending)])))
            db.execute('DROP TABLE retry_documents')
            db.execute('DROP TABLE failed_batches')
        self.pipeline = CluePipeline(self.store)
        self.model('{"clues":[]}')
        status, data = self.run_analysis('b')
        self.assertEqual(status, 'succeeded')
        self.assertEqual(data['processed'], 1)
        self.assertTrue(self.seen(seen))
        self.assertTrue(self.seen(pending))
        self.assertEqual(len(self.calls), 1)
        with self.pipeline.db() as db:
            self.assertEqual(db.execute('SELECT completed FROM scopes WHERE run_id=?', ('a' * 32,)).fetchone()[0], 1)
        self.assertEqual(self.pipeline.path.stat().st_mode & 0o777, 0o600)

    def test_legacy_scope_for_another_model_does_not_block_fresh_current_model_analysis(self):
        old = self.add('old-model', '合成另一模型未完成的消息', chat='old')
        with self.pipeline.db() as db:
            db.execute('INSERT INTO scopes VALUES(?,?,?,0)', ('a' * 32, 'other-model',
                       json.dumps([{'id': old['id'], 'hash': old['hash']}])))
        fresh = self.add('current-model', '合成当前模型的新消息', chat='fresh')
        self.model('{"clues":[]}')
        status, data = self.run_analysis('b', incremental=True)
        self.assertEqual(status, 'succeeded')
        self.assertEqual(data['processed'], 2)
        self.assertTrue(self.seen(old))
        self.assertTrue(self.seen(fresh))
        with self.pipeline.db() as db:
            self.assertEqual(db.execute('SELECT model FROM scopes WHERE run_id=?', ('a' * 32,)).fetchone()[0], 'other-model')

    def test_deleted_legacy_source_releases_scope_without_marking_missing_version_seen(self):
        missing = self.add('deleted', '合成之后被删除的旧消息')
        with self.pipeline.db() as db:
            db.execute('INSERT INTO scopes VALUES(?,?,?,0)', ('a' * 32, 'synthetic',
                       json.dumps([{'id': missing['id'], 'hash': missing['hash']}])))
        with self.store.knowledge.db() as db:
            db.execute('DELETE FROM documents WHERE id=?', (missing['id'],))
        fresh = self.add('after-deleted', '合成仍然可用的新消息', chat='fresh')
        self.model('{"clues":[]}')
        status, data = self.run_analysis('b', incremental=True)
        self.assertEqual(status, 'succeeded')
        self.assertEqual(data['processed'], 1)
        self.assertTrue(self.seen(fresh))
        self.assertFalse(self.seen(missing))
        with self.pipeline.db() as db:
            self.assertEqual(db.execute('SELECT completed FROM scopes WHERE run_id=?', ('a' * 32,)).fetchone()[0], 1)

    def test_expired_legacy_scope_cannot_extend_the_seven_day_analysis_contract(self):
        expired = self.add('expired', '合成超过七天的旧消息', age=8 * 86400)
        with self.pipeline.db() as db:
            db.execute('INSERT INTO scopes VALUES(?,?,?,0)', ('a' * 32, 'synthetic',
                       json.dumps([{'id': expired['id'], 'hash': expired['hash']}])))
        fresh = self.add('after-expired', '合成七天内的新消息', chat='fresh')
        def response(prompt):
            self.assertNotIn(expired['id'], [item['id'] for item in self.material(prompt)])
            return '{"clues":[]}'
        self.model(response)
        status, data = self.run_analysis('b', incremental=True)
        self.assertEqual(status, 'succeeded')
        self.assertEqual(data['processed'], 1)
        self.assertTrue(self.seen(fresh))
        self.assertFalse(self.seen(expired))

    def test_report_failure_after_checkpoint_retains_durable_processed_count(self):
        doc = self.add('committed-before-report', '合成检查点成功后报告写入失败的消息')
        self.model('{"clues":[]}')
        original_report = self.pipeline.report
        thrown = []
        def report(run_id, phase, processed, skipped=0, **extra):
            if phase == 'analysis' and processed == 1 and not thrown:
                thrown.append(True)
                raise sqlite3.OperationalError('合成报告数据库暂时不可写')
            return original_report(run_id, phase, processed, skipped, **extra)
        self.pipeline.report = report
        status, data = self.run_analysis()
        self.assertEqual(thrown, [True])
        self.assertEqual(status, 'partial')
        self.assertEqual(data['processed'], 1)
        self.assertTrue(self.seen(doc))
        self.assertEqual(self.pipeline.status()['processed_versions'], 1)
        self.assertEqual(self.pipeline.status()['progress']['processed'], 1)
        self.assertTrue(data['errors'])
        with self.pipeline.db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM batches').fetchone()[0], 1)

    def test_report_failure_after_valid_sibling_save_retains_durable_clue_id(self):
        doc = self.add('saved-before-report', '合成合法候选已保存后报告写入失败的消息')
        self.model(lambda prompt: self.response(prompt, mixed=True))
        original_report = self.pipeline.report
        thrown = []
        def report(run_id, phase, processed, skipped=0, **extra):
            if phase == 'analysis' and extra.get('clue_ids') and not thrown:
                thrown.append(True)
                raise sqlite3.OperationalError('合成报告数据库暂时不可写')
            return original_report(run_id, phase, processed, skipped, **extra)
        self.pipeline.report = report
        status, data = self.run_analysis()
        self.assertEqual(thrown, [True])
        self.assertEqual(status, 'partial')
        self.assertEqual(data['processed'], 0)
        self.assertEqual(len(data['clues']), 1)
        self.assertFalse(self.seen(doc))
        self.assertEqual(self.store.clues.listing()['total'], 1)
        self.assertEqual(self.pipeline.status()['progress']['clue_ids'], data['clues'])
        self.assertTrue(data['errors'])
        with self.pipeline.db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM batches').fetchone()[0], 0)

    def test_historical_progress_without_durable_ledger_keeps_original_counts(self):
        historical = {'run_id': 'f' * 32, 'phase': 'analysis', 'processed': 163, 'skipped': 8,
                      'clue_ids': [], 'has_more': True}
        with self.pipeline.db() as db:
            db.execute('INSERT INTO progress VALUES(1,?)', (json.dumps(historical),))
            self.assertIsNone(db.execute('SELECT 1 FROM run_totals WHERE run_id=?',
                                         (historical['run_id'],)).fetchone())
        state = self.pipeline.status()
        self.assertEqual(state['progress']['processed'], 163)
        self.assertEqual(state['progress']['skipped'], 8)
        self.assertEqual(state['progress']['phase'], 'analysis')
        self.assertTrue(state['progress']['has_more'])

    def test_unknown_fields_remain_rejected_and_repair_names_exact_allowed_schema(self):
        doc = self.add('extra-fields', '合成有明确出处的技术更新消息')
        refs = self.pipeline.evidence_refs([doc])
        injected = self.candidate(next(iter(refs)))
        injected['actions'] = [{'tool': 'run_command', 'command': 'must-never-execute'}]
        injected['confidence'] = 0.9
        with self.assertRaises(ValueError) as caught:
            self.pipeline.parse(json.dumps({'clues': [injected]}), [doc], refs)
        self.assertIn('actions', str(caught.exception))
        self.assertIn('confidence', str(caught.exception))
        self.assertFalse(self.seen(doc))
        self.assertEqual(self.store.clues.listing()['total'], 0)
        def response(prompt):
            if len(self.calls) == 1:
                clean = json.loads(self.response(prompt))
                clean['clues'][0].update(actions=injected['actions'], confidence=0.9)
                return json.dumps(clean, ensure_ascii=False)
            repair = prompt.split('\n上次返回未通过格式校验：', 1)[1]
            self.assertIn('actions', repair)
            self.assertIn('confidence', repair)
            for field in ('title', 'summary', 'relevance', 'categories', 'kind', 'priority', 'quotes'):
                self.assertIn(field, repair)
            self.assertTrue(any(word in repair for word in ('允许', '只能', '仅允许')))
            self.assertTrue(any(word in repair for word in ('删除', '移除')))
            self.assertIn('ref', repair)
            self.assertEqual(self.store.events.list_events()['events'], [])
            return self.response(prompt)
        self.model(response)
        status, data = self.run_analysis()
        self.assertEqual(status, 'succeeded')
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(data['processed'], 1)
        self.assertEqual(len(data['clues']), 1)
        self.assertTrue(self.seen(doc))
        self.assertEqual(self.store.clues.listing()['total'], 1)
        self.assertEqual(self.store.events.list_events()['events'], [])
        clue = self.store.clues.detail(data['clues'][0])
        self.assertNotIn('actions', clue)
        self.assertNotIn('confidence', clue)

    def test_incremental_failure_does_not_block_other_groups_or_later_messages(self):
        bad = self.add('bad-group', '合成坏群消息', chat='bad')
        good = self.add('good-group', '合成好群技术消息', chat='good')
        def response(prompt):
            return 'not-json' if any(item['id'] == bad['id'] and not item.get('context')
                                     for item in self.material(prompt)) else '{"clues":[]}'
        self.model(response)
        status, data = self.run_analysis(incremental=True)
        self.assertEqual(status, 'partial')
        self.assertEqual(data['processed'], 1)
        self.assertTrue(self.seen(good))
        self.assertFalse(self.seen(bad))
        self.assertEqual(len(self.calls), 3)
        later = self.add('later-new', '合成失败后新增的技术消息', chat='later')
        self.model('{"clues":[]}')
        _, later_data = self.run_analysis('b', incremental=True)
        self.assertTrue(self.seen(later))
        self.assertEqual(later_data['processed'], 1)
        self.assertFalse(self.seen(bad))
        self.assertEqual(later_data['deferred'], 1)

    def test_changed_source_hash_is_a_fresh_version_and_old_failure_does_not_block_it(self):
        old = self.add('changed', '合成旧版本技术消息')
        with self.pipeline.db() as db:
            db.execute('INSERT INTO scopes VALUES(?,?,?,0)', ('a' * 32, 'synthetic',
                       json.dumps([{'id': old['id'], 'hash': old['hash']}])))
        self.pipeline.defer_batch([old], 'synthetic', 'a' * 32, '合成旧版本校验失败', [])
        new = self.add('changed', '合成已经修改的新版本技术消息')
        self.assertEqual(old['id'], new['id'])
        self.assertNotEqual(old['hash'], new['hash'])
        self.model('{"clues":[]}')
        status, data = self.run_analysis('b', incremental=True)
        self.assertEqual(status, 'succeeded')
        self.assertEqual(data['processed'], 1)
        self.assertTrue(self.seen(new))
        self.assertFalse(self.seen(old))
        self.assertEqual(self.pipeline.retry_queue('synthetic'), [])

    def test_successful_manual_retry_clears_reservations_and_keeps_saved_clue_id(self):
        doc = self.add('recover', '合成最终能够恢复的技术消息')
        self.model(lambda prompt: self.response(prompt, mixed=True))
        _, first = self.run_analysis()
        self.model(self.response)
        status, second = self.run_analysis('b', manual=True)
        self.assertEqual(status, 'succeeded')
        self.assertEqual(second['processed'], 1)
        self.assertEqual(second['clues'], first['clues'])
        self.assertEqual(self.store.clues.listing()['total'], 1)
        self.assertTrue(self.seen(doc))
        self.assertEqual(second['deferred'], 0)
        with self.pipeline.db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM retry_documents WHERE document_id=? AND hash=?',
                                       (doc['id'], doc['hash'])).fetchone()[0], 0)

    def test_cancellation_preserves_completed_batches_without_creating_retry_attempt(self):
        first = self.add('completed', '合成已完成的技术消息', chat='first')
        second = self.add('cancelled', '合成中途停止的技术消息', chat='second')
        cancel = threading.Event()
        def response(prompt):
            if len(self.calls) == 2:
                cancel.set()
                raise ValueError('合成模型已停止')
            return self.response(prompt)
        self.model(response)
        status, data = self.run_analysis(incremental=True, cancel=cancel)
        self.assertEqual(status, 'cancelled')
        self.assertEqual(data['processed'], 1)
        self.assertEqual(len(data['clues']), 1)
        self.assertEqual(sum(self.seen(doc) for doc in (first, second)), 1)
        with self.pipeline.db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM failed_batches').fetchone()[0], 0)

    def test_timeout_preserves_completed_clue_ids_and_does_not_increment_retry_budget(self):
        self.add('time-first', '合成按时完成的技术消息', chat='first')
        self.add('time-second', '合成超过预算的技术消息', chat='second')
        clock = [0]
        def response(prompt):
            if len(self.calls) == 2:
                clock[0] = 100
                raise ValueError('合成调用耗尽预算')
            return self.response(prompt)
        self.model(response)
        with patch('clue_pipeline.time.monotonic', side_effect=lambda: clock[0]):
            status, data = self.run_analysis(incremental=True, deadline=90)
        self.assertEqual(status, 'timeout')
        self.assertEqual(data['processed'], 1)
        self.assertEqual(len(data['clues']), 1)
        self.assertEqual(self.pipeline.status()['processed_versions'], 1)
        with self.pipeline.db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM failed_batches').fetchone()[0], 0)


if __name__ == '__main__':
    unittest.main()
