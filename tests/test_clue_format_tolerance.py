"""Synthetic coverage for clue and radar format tolerance and the one-time requeue.

No real messages, model, collector or database outside a temporary directory.
"""
import json
import unittest

import test_clue_retry as fixtures
from clue_pipeline import CluePipeline


class FormatToleranceTests(unittest.TestCase):
    setUp = fixtures.ClueRetryTests.setUp
    add = fixtures.ClueRetryTests.add
    response = fixtures.ClueRetryTests.response
    run_analysis = fixtures.ClueRetryTests.run_analysis
    seen = fixtures.ClueRetryTests.seen
    model = fixtures.ClueRetryTests.model
    candidate = staticmethod(fixtures.ClueRetryTests.candidate)
    material = staticmethod(fixtures.ClueRetryTests.material)

    def test_format_failure_still_parks_after_existing_repair(self):
        self.add('retry-format', '合成格式始终不可用的技术资料')
        policy = CluePipeline.FORMAT_POLICY
        self.model('not-json')
        for run in ('a','b','c'):
            self.run_analysis(run, manual=True)
        self.assertEqual(len(self.calls), 6)
        rows, docs = self.queue_rows()
        self.assertEqual((rows[0]['attempts'], rows[0]['state']), (3,'needs_review'))
        self.assertEqual(CluePipeline.FORMAT_POLICY, policy)
        with self.pipeline.db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM policy_requeue').fetchone()[0], 0)

    def queue_rows(self):
        with self.pipeline.db() as db:
            return ([dict(r) for r in db.execute('SELECT id,state,attempts,error FROM failed_batches')],
                    [dict(r) for r in db.execute('SELECT document_id,state,attempts FROM retry_documents')])

    def park(self, doc, error, runs=('a', 'b', 'c')):
        """Reproduce a batch the old rules left in needs_review."""
        for run in runs:
            self.pipeline.defer_batch([doc], 'synthetic', run * 32, error, [])
        state, rows = self.queue_rows()
        self.assertEqual([(row['state'], row['attempts']) for row in state], [('needs_review', 3)])
        self.assertEqual([(row['state'], row['attempts']) for row in rows], [('needs_review', 3)])
        return state[0]['id']

    def test_first_automatic_run_requeues_a_recoverable_parked_batch_once(self):
        doc = self.add('padded-field', '合成字段名带空格的技术消息')
        batch_id = self.park(doc, '模型线索包含不支持的字段： relevance；仅允许 title, summary')
        damaged = dict(self.candidate('e0'))
        damaged.pop('title')
        damaged[' title'] = '合成技术线索'
        damaged['id'] = '模型多给的编号'
        self.model(json.dumps({'clues': [damaged]}, ensure_ascii=False))
        status, data = self.run_analysis('d', incremental=True)
        # The parked batch is re-queued by product code and the same run finishes it.
        self.assertEqual(status, 'succeeded')
        self.assertEqual(data['requeued_batches'], {'policy': CluePipeline.FORMAT_POLICY, 'batches': 1, 'documents': 1})
        self.assertEqual(data['processed'], 1)
        self.assertEqual(data['format']['field_names'], 1)
        self.assertEqual(data['format']['ignored_fields'], 1)
        self.assertEqual(len(data['clues']), 1)
        self.assertTrue(self.seen(doc))
        state, rows = self.queue_rows()
        self.assertEqual([(row['id'], row['state']) for row in state], [(batch_id, 'resolved')])
        self.assertEqual(rows, [])
        # The policy applies once: a later automatic run never requeues again.
        self.model('{"clues":[]}')
        _, later = self.run_analysis('e', incremental=True)
        self.assertNotIn('requeued_batches', later)
        with self.pipeline.db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM policy_requeue').fetchone()[0], 1)

    def test_requeue_leaves_failures_the_new_rules_cannot_handle_parked(self):
        doc = self.add('unparseable', '合成模型始终返回无效 JSON 的技术消息')
        self.park(doc, '模型没有返回有效线索 JSON；本批未推进，下一次重试')
        self.model(lambda prompt: self.fail('An unparseable batch must not be retried by the requeue'))
        status, data = self.run_analysis('d', incremental=True)
        self.assertEqual(status, 'failed')
        self.assertEqual(self.calls, [])
        self.assertNotIn('requeued_batches', data)
        state, rows = self.queue_rows()
        self.assertEqual([row['state'] for row in state], ['needs_review'])
        self.assertEqual([row['state'] for row in rows], ['needs_review'])

    def test_manual_runs_never_apply_the_requeue_policy(self):
        doc = self.add('manual-park', '合成需要人工核对的技术消息')
        self.park(doc, '模型线索包含不支持的字段： relevance')
        self.model('{"clues":[]}')
        status, data = self.run_analysis('d', incremental=True, manual=True)
        self.assertNotIn('requeued_batches', data)
        with self.pipeline.db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM policy_requeue').fetchone()[0], 0)

    def test_counters_separate_repaired_fields_from_dropped_clues(self):
        first = self.add('repair-me', '合成字段名需要修复的技术消息')
        second = self.add('drop-me', '合成分类无效的技术消息', chat='other-chat')
        def answer(prompt):
            items = [item for item in self.material(prompt) if not item.get('context')]
            clues = []
            for item in items:
                clue = dict(self.candidate(item['evidence'][0]['ref']))
                clue[' title'] = clue.pop('title')
                clue['id'] = '多余的编号'
                if item['id'] == second['id']:
                    clue['categories'] = ['invalid-category']
                clues.append(clue)
            return json.dumps({'clues': clues}, ensure_ascii=False)
        self.model(answer)
        status, data = self.run_analysis(incremental=True)
        self.assertEqual(status, 'partial')
        self.assertEqual(data['processed'], 1)
        self.assertEqual(data['deferred'], 1)
        self.assertEqual(data['format']['field_names'], 2)
        self.assertEqual(data['format']['ignored_fields'], 2)
        self.assertEqual(data['format']['dropped_clues'], 1)
        self.assertEqual(data['format']['requeued_documents'], 1)
        self.assertEqual(len(data['clues']), 1)
        self.assertTrue(self.seen(first))
        self.assertFalse(self.seen(second))
        self.assertEqual(data['dropped'][0]['reason'][:6], '模型分类无效')
        self.assertEqual(data['dropped'][0]['documents'], 1)
        self.assertEqual(self.store.clues.listing()['total'], 1)


if __name__ == '__main__':
    unittest.main()
