"""Synthetic coverage: an unusable continuation degrades instead of parking a batch.

No real messages, model, collector or database outside a temporary directory.
"""
import json
import threading
import time
import unittest

import test_clue_pipeline as fixtures
import wechat_clue_context as workflow
from clue_pipeline import CluePipeline


class ContinuationDegradeTests(unittest.TestCase):
    setUp = fixtures.PipelineTests.setUp
    tearDown = fixtures.PipelineTests.tearDown
    add = fixtures.PipelineTests.add

    def verified(self, body=None, chat='synthetic-chat'):
        body = body or ('合成产品：合作需求为制作一次演示。' * 100)
        doc = self.add('old', body, chat=chat)
        picks = [{'id': doc['id'], 'hash': doc['hash'], 'start': 0, 'end': 12}]
        clue = self.store.clues._save({'title': '人工整理的项目', 'summary': '已核对的原始需求', 'relevance': '合成测试',
            'categories': ['projects'], 'status': 'verified', 'tracking': True, 'kind': 'information',
            'priority': 'important'}, self.store.events._capture(picks))
        return doc, clue

    def answer(self, continuation):
        """Build the model reply from the refs this batch actually offered."""
        def model(identity, prompt, *args):
            items = json.loads(prompt.split('<source_material>', 1)[1].split('</source_material>', 1)[0])
            states = json.loads(prompt.split('<existing_clues>')[1].split('</existing_clues>')[0])
            old_item = next(d for d in items if d['id'] == self.old['id'])
            new_item = next(d for d in items if d['id'] == self.new['id'])
            proof_ref = next(e['ref'] for e in old_item['evidence'] if e['text'] == self.old['body'][:12])
            wide_ref = next(e['ref'] for e in old_item['evidence'] if e['text'] == self.old['body'][:1000])
            value = continuation(states, proof_ref, wide_ref, new_item['evidence'][0]['ref'])
            return json.dumps({'clues': [{'title': '模型提议续接', 'summary': '对方补充了新的演示验收要求。',
                'relevance': '合成变化', 'categories': ['other'], 'kind': 'information', 'priority': 'normal',
                'quotes': [{'ref': new_item['evidence'][0]['ref']}], 'continuation': value}]}, ensure_ascii=False)
        self.pipeline.call_pi = model

    def run_analysis(self, run, manual=False):
        self.pipeline.prepare_cache = lambda *args: {'index_status': 'ready'}
        result = self.pipeline.analyze(run * 32, {'body': '合成范围', 'wechat_mode': 'incremental',
            'execution_trigger': 'manual' if manual else 'scheduled'}, threading.Event(), self.config,
            time.monotonic() + 60)
        return result[0], json.loads(result[2])

    def seen(self, doc):
        with self.pipeline.db() as db:
            return bool(db.execute('SELECT 1 FROM seen WHERE document_id=? AND hash=?', (doc['id'], doc['hash'])).fetchone())

    def queues(self):
        with self.pipeline.db() as db:
            return (db.execute('SELECT COUNT(*) FROM failed_batches').fetchone()[0],
                db.execute('SELECT COUNT(*) FROM retry_documents').fetchone()[0])

    def test_missing_continuation_identity_degrades_on_the_first_attempt(self):
        self.old, clue = self.verified()
        self.new = self.add('new', '接上次产品演示，请补充截图验收。')
        self.pipeline.checkpoint([self.old, self.doc], 'a' * 32, 'synthetic')
        # Exactly the production receipt: the continuation object is malformed.
        self.answer(lambda states, proof_ref, wide_ref, new_ref: {'id': states[0]['id'], 'revision': states[0]['revision'],
            'refs': [proof_ref, new_ref], 'note': '模型多给的字段'})
        status, data = self.run_analysis('b')
        self.assertEqual(status, 'succeeded')
        self.assertEqual(data['processed'], 1)
        self.assertEqual(data['deferred'], 0)
        self.assertEqual(data['errors'], [])
        self.assertTrue(self.seen(self.new))
        self.assertEqual(len(data['degraded']), 1)
        self.assertEqual(data['degraded'][0]['documents'], 1)
        self.assertIn('续接线索必须提供已有编号', data['degraded'][0]['reason'])
        # No retry queue entry and no parked batch: the first pass degrades, so a
        # bad continuation never spends the batch's three attempts again.
        self.assertEqual(self.queues(), (0, 0))
        self.assertEqual(data['degraded'][0]['batch_id'], self.pipeline.batch_identity([self.new], 'synthetic'))
        # Never merged by keyword: the verified clue is untouched and the saved
        # result is a separate new clue.
        self.assertEqual(self.store.clues.detail(clue['id'])['revision'], 1)
        self.assertEqual(self.store.clues.listing()['total'], 2)
        self.assertNotIn(clue['id'], data['clues'])
        self.assertEqual(len(data['clues']), 1)
        detail = self.store.clues.detail(data['clues'][0])
        self.assertEqual(detail['status'], 'candidate')
        self.assertEqual(detail['entries'][0]['evidence'][0]['document_id'], self.new['id'])

    def test_stale_proof_continuation_degrades_instead_of_guessing_a_merge(self):
        self.old, clue = self.verified()
        self.new = self.add('new', '接上次产品演示，补充截图验收。')
        self.pipeline.checkpoint([self.old, self.doc], 'a' * 32, 'synthetic')
        # A valid id and revision, but the quoted evidence is not the clue's
        # still-valid proof: exactly the keyword-merge case that must not merge.
        self.answer(lambda states, proof_ref, wide_ref, new_ref: {'id': states[0]['id'], 'revision': states[0]['revision'],
            'refs': [wide_ref, new_ref]})
        status, data = self.run_analysis('b')
        self.assertEqual(status, 'succeeded')
        self.assertEqual(data['processed'], 1)
        self.assertEqual(data['deferred'], 0)
        self.assertIn('缺少仍然有效的既有原文依据', data['degraded'][0]['reason'])
        self.assertEqual(self.queues(), (0, 0))
        self.assertEqual(self.store.clues.detail(clue['id'])['revision'], 1)
        self.assertEqual(self.store.clues.listing()['total'], 2)

    def test_continuation_quoting_foreign_refs_degrades_on_the_first_attempt(self):
        self.old, clue = self.verified()
        self.new = self.add('new', '接上次产品演示，补充截图验收。')
        self.pipeline.checkpoint([self.old, self.doc], 'a' * 32, 'synthetic')
        # 'zz' was never offered in this batch, so the continuation is malformed.
        self.answer(lambda states, proof_ref, wide_ref, new_ref: {'id': states[0]['id'], 'revision': states[0]['revision'],
            'refs': [proof_ref, 'zz']})
        status, data = self.run_analysis('b')
        self.assertEqual(status, 'succeeded')
        self.assertEqual(data['processed'], 1)
        self.assertEqual(data['deferred'], 0)
        self.assertIn('续接线索引用了本批之外的证据编号', data['degraded'][0]['reason'])
        self.assertEqual(self.store.clues.detail(clue['id'])['revision'], 1)
        self.assertEqual(len(data['clues']), 1)

    def test_valid_continuation_still_appends_and_is_never_degraded(self):
        self.old, clue = self.verified()
        self.new = self.add('new', '接上次产品演示，补充截图验收要求。')
        self.pipeline.checkpoint([self.old, self.doc], 'a' * 32, 'synthetic')
        self.answer(lambda states, proof_ref, wide_ref, new_ref: {'id': states[0]['id'], 'revision': states[0]['revision'],
            'refs': [proof_ref, new_ref]})
        status, data = self.run_analysis('b')
        self.assertEqual(status, 'succeeded')
        self.assertEqual(data['processed'], 1)
        self.assertEqual(data['clues'], [clue['id']])
        self.assertNotIn('degraded', data)
        self.assertEqual(self.store.clues.detail(clue['id'])['revision'], 2)
        self.assertEqual(self.store.clues.listing()['total'], 1)

    def test_degraded_candidate_without_new_evidence_is_dropped_not_deferred(self):
        self.old, clue = self.verified()
        self.new = self.add('new', '接上次产品演示，补充截图验收。')
        self.pipeline.checkpoint([self.old, self.doc], 'a' * 32, 'synthetic')
        # The candidate quotes only context, so after the continuation link is
        # dropped there is nothing new to save; the batch still completes.
        def model(identity, prompt, *args):
            items = json.loads(prompt.split('<source_material>', 1)[1].split('</source_material>', 1)[0])
            old_item = next(d for d in items if d['id'] == self.old['id'])
            return json.dumps({'clues': [{'title': '只有上下文引用', 'summary': '仅引用已有依据的候选。', 'relevance': '合成',
                'categories': ['other'], 'kind': 'information', 'priority': 'normal',
                'quotes': [{'ref': old_item['evidence'][0]['ref']}],
                'continuation': {'id': 'f' * 32, 'revision': 1, 'refs': [old_item['evidence'][0]['ref']]}}]},
                ensure_ascii=False)
        self.pipeline.call_pi = model
        status, data = self.run_analysis('b')
        self.assertEqual(status, 'succeeded')
        self.assertEqual(data['processed'], 1)
        self.assertEqual(data['deferred'], 0)
        self.assertEqual(data['clues'], [])
        self.assertEqual(len(data['degraded']), 1)
        self.assertEqual(self.store.clues.listing()['total'], 1)


if __name__ == '__main__':
    unittest.main()
