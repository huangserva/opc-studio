"""Synthetic regression coverage for failed WeChat batches and safe recovery."""
import json
import sqlite3
import tempfile
import threading
import time
import unittest
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from isolation_patches import RECOVERY_SCRIPT_REASON, WECHAT_SOURCE_REASON, wechat_source_available
# hygiene-4：下面的 import 会加载微信来源插件（clue_pipeline 等在模块级调
# wechat_source_loader.source_module）。插件由 ARTICLE_STUDIO_WECHAT_MODULE 或
# workspace/sources/wechat.json 指定，两者都是本机资源，不进公开仓库；
# 干净克隆里没有它，这里直接跳过整个模块（unittest 会把 SkipTest 记成 skip
# 而不是 error）。本机配置齐全时行为与从前逐字一致。
if not wechat_source_available():
    raise unittest.SkipTest(WECHAT_SOURCE_REASON)

from clue_pipeline import CluePipeline
from knowledge_store import Knowledge, identifier
from server import Store
from isolation_patches import module_time
from wechat_source_loader import source_module

_clue_source = source_module('clue_pipeline')


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

    def test_not_sent_does_not_defer_batch(self):
        doc = self.add('not-sent', '合成待分析资料')
        class ClassifiedError(ValueError):
            def __init__(self, kind):
                super().__init__('合成分类错误（不靠文案判断）'); self.failure_kind = kind
        for kind, phase in (('not_sent', None), ('cancelled', 'cancelled'), ('deadline', 'timeout')):
            with self.subTest(kind=kind):
                self.pipeline.call_pi = lambda *args: (_ for _ in ()).throw(ClassifiedError(kind))
                with module_time(_clue_source, monotonic=lambda: 0):
                    status, data = self.run_analysis(deadline=300)
                self.assertFalse(self.seen(doc))
                if phase: self.assertEqual(status, phase)
                else: self.assertTrue(data['time_budget'])
                with self.pipeline.db() as db:
                    for table in ('failed_batches', 'retry_documents', 'batches'):
                        self.assertEqual(db.execute('SELECT COUNT(*) FROM '+table).fetchone()[0], 0)
        self.pipeline.call_pi = lambda *args: (_ for _ in ()).throw(ClassifiedError('model_transient'))
        with module_time(_clue_source, monotonic=lambda: 0):
            self.run_analysis(deadline=300)
        with self.pipeline.db() as db:
            self.assertEqual(db.execute('SELECT attempts FROM failed_batches').fetchone()[0], 1)
        self.assertFalse(self.seen(doc))

    def retry_row(self, batch):
        with self.pipeline.db() as db:
            return dict(db.execute('SELECT * FROM failed_batches WHERE id=?', (batch,)).fetchone())

    def test_first_failure_waits_30_minutes(self):
        doc = self.add('r-first', '合成重试')
        with module_time(_clue_source, time=lambda: 1000):
            key = self.pipeline.defer_batch([doc], 'synthetic', 'a'*32, '合成失败', [])
            self.assertEqual(self.retry_row(key)['next_retry_at'], 2800)
            self.assertFalse(self.pipeline.pending(ready_only=True, model='synthetic', run_id='b'*32))

    def test_second_failure_waits_60_minutes(self):
        doc = self.add('r-second', '合成重试')
        with module_time(_clue_source, time=lambda: 1000):
            key = self.pipeline.defer_batch([doc], 'synthetic', 'a'*32, '失败', [])
        with module_time(_clue_source, time=lambda: 2801):
            self.assertEqual(self.pipeline.defer_batch([doc], 'synthetic', 'b'*32, '失败', []), key)
        self.assertEqual(self.retry_row(key)['next_retry_at'], 6401)
        with self.pipeline.db() as db:
            self.assertEqual(db.execute('SELECT first_failed_at FROM retry_policy WHERE batch_id=?', (key,)).fetchone()[0], 1000)

    def test_third_failure_parks_without_due(self):
        doc = self.add('r-third', '合成重试')
        for run, now in (('a', 1000), ('b', 2801), ('c', 6402)):
            with module_time(_clue_source, time=lambda: now):
                key = self.pipeline.defer_batch([doc], 'synthetic', run*32, '失败', [])
        row = self.retry_row(key)
        self.assertEqual((row['attempts'], row['state'], row['next_retry_at']), (3, 'needs_review', None))

    def test_window_expires_at_pending_not_on_next_failure(self):
        doc = self.add('r-window', '合成重试')
        for entry in ('pending', 'conversation', 'scoped'):
            with self.subTest(entry=entry):
                with self.pipeline.db() as db:
                    for table in ('failed_batches', 'retry_documents', 'retry_policy', 'scopes'):
                        db.execute('DELETE FROM '+table)
                with module_time(_clue_source, time=lambda: 1000):
                    key = self.pipeline.defer_batch([doc], 'synthetic', 'a'*32, '失败', [])
                with module_time(_clue_source, time=lambda: 87400):
                    if entry == 'pending': docs = self.pipeline.pending(ready_only=True, model='synthetic', run_id='b'*32)
                    elif entry == 'conversation': docs = self.pipeline.conversation_pending('synthetic', 'b'*32, False)
                    else: _, docs = self.pipeline.scoped_pending('b'*32, 'synthetic')
                self.assertEqual(docs, [])
                self.assertEqual((self.retry_row(key)['state'], self.retry_row(key)['next_retry_at']), ('needs_review', None))
                self.assertFalse(self.seen(doc))

    def test_new_run_does_not_reset_first_failed_at(self):
        self.test_second_failure_waits_60_minutes()

    # ── R13：retry_queue 先做轻量过期收口 ────────────────────────────────

    def traced_db(self):
        statements = []
        original = self.pipeline.db
        @contextmanager
        def db():
            with original() as connection:
                connection.set_trace_callback(statements.append)
                yield connection
        return patch.object(self.pipeline, 'db', db), statements

    def test_retry_queue_settles_the_expired_window_before_reporting(self):
        doc = self.add('r-queue-expire', '合成重试')
        with module_time(_clue_source, time=lambda: 1000):
            key = self.pipeline.defer_batch([doc], 'synthetic', 'a'*32, '失败', [])
        with module_time(_clue_source, time=lambda: 87400):
            entry = self.pipeline.retry_queue('synthetic')[0]
        self.assertEqual((entry['state'], entry['next_retry_at']), ('needs_review', None))
        self.assertEqual((self.retry_row(key)['state'], self.retry_row(key)['next_retry_at']), ('needs_review', None))
        self.assertFalse(self.seen(doc))

    def test_retry_queue_keeps_an_unexpired_batch_waiting_and_write_free(self):
        doc = self.add('r-queue-wait', '合成重试')
        with module_time(_clue_source, time=lambda: 1000):
            self.pipeline.defer_batch([doc], 'synthetic', 'a'*32, '失败', [])
        traced, statements = self.traced_db()
        with traced, module_time(_clue_source, time=lambda: 2000):
            entry = self.pipeline.retry_queue('synthetic')[0]
        self.assertEqual((entry['state'], entry['next_retry_at']), ('waiting', datetime.fromtimestamp(2800, timezone.utc).isoformat()))
        self.assertFalse(any('BEGIN IMMEDIATE' in s.upper() for s in statements), statements)

    # ── R13-b：在途读队列 + 明确未发送 → 下一轮仍可重试 ──────────────────

    def test_inflight_queue_read_then_not_sent_keeps_the_batch_retryable(self):
        doc = self.add('r-inflight', '合成在途资料')
        with module_time(_clue_source, time=lambda: 1000):
            key = self.pipeline.defer_batch([doc], 'synthetic', 'a'*32, '合成失败', [])
        with self.pipeline.db() as db:
            db.execute('INSERT INTO recovery_grants VALUES(?,?,?,?,NULL,NULL)', (key, 'inflight-epoch', 90000, 110000))
            db.execute('UPDATE failed_batches SET next_retry_at=90000 WHERE id=?', (key,))
            db.execute('UPDATE retry_documents SET next_retry_at=90000 WHERE batch_id=?', (key,))
        entered = threading.Event(); release = threading.Event()
        class NotSent(ValueError): failure_kind = 'not_sent'
        def model(identity, prompt, *args):
            receipt = self.pipeline.root/'analysis-pi'/identity/'execution-receipt.json'
            receipt.parent.mkdir(parents=True, exist_ok=True)
            receipt.write_text(json.dumps({'sent': False}))
            entered.set(); self.assertTrue(release.wait(5), '合成执行器未被放行')
            raise NotSent('合成未发送')
        self.pipeline.call_pi = model
        results = []; errors = []
        def run():
            try: results.append(self.run_analysis('b', incremental=True, deadline=300))
            except Exception as exc: errors.append(exc)
        with module_time(_clue_source, time=lambda: 100000, monotonic=lambda: 0):
            worker = threading.Thread(target=run); worker.start()
            self.assertTrue(entered.wait(5), '模型未进入调用')
            self.assertTrue(self.pipeline.lock.locked())
            during = self.pipeline.retry_queue('synthetic')
            release.set(); worker.join(10)
            self.assertFalse(worker.is_alive()); self.assertEqual(errors, [])
            self.assertEqual([entry['state'] for entry in during], ['waiting'])
            row = self.retry_row(key)
            self.assertEqual((row['state'], row['next_retry_at'], row['attempts']), ('waiting', 90000, 1))
            with self.pipeline.db() as db:
                self.assertEqual(tuple(db.execute('SELECT claimed_at,claimed_run_id FROM recovery_grants WHERE batch_id=?', (key,)).fetchone()), (None, None))
            self.assertEqual(len(self.pipeline.pending(ready_only=True, model='synthetic', run_id='c'*32)), 1)
            self.model(self.response)
            status, data = self.run_analysis('c', incremental=True, deadline=300)
        self.assertEqual((status, data['processed']), ('succeeded', 1))

    def test_same_run_does_not_consume_batch_twice(self):
        doc = self.add('r-same', '合成重试')
        with module_time(_clue_source, time=lambda: 1000):
            key = self.pipeline.defer_batch([doc], 'synthetic', 'a'*32, '失败', [])
        with module_time(_clue_source, time=lambda: 9000):
            self.assertEqual(self.pipeline.pending(ready_only=True, model='synthetic', run_id='a'*32), [])
            self.pipeline.defer_batch([doc], 'synthetic', 'a'*32, '重复失败', [])
        self.assertEqual(self.retry_row(key)['attempts'], 1)
        self.assertEqual(self.retry_row(key)['next_retry_at'], 2800)

    def test_policy_and_queue_roll_back_together(self):
        doc = self.add('r-rollback', '合成重试')
        with self.pipeline.db() as db:
            db.execute("CREATE TRIGGER fail_retry BEFORE INSERT ON retry_documents BEGIN SELECT RAISE(ABORT,'synthetic'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            self.pipeline.defer_batch([doc], 'synthetic', 'a'*32, '失败', [])
        with self.pipeline.db() as db:
            for table in ('retry_policy', 'failed_batches', 'retry_documents'):
                self.assertEqual(db.execute('SELECT COUNT(*) FROM '+table).fetchone()[0], 0)

    def test_changed_hash_is_a_new_window(self):
        old = self.add('r-hash', '合成旧版本')
        with module_time(_clue_source, time=lambda: 1000):
            old_key = self.pipeline.defer_batch([old], 'synthetic', 'a'*32, '失败', [])
        new = self.add('r-hash', '合成新版本')
        with module_time(_clue_source, time=lambda: 2000):
            new_key = self.pipeline.defer_batch([new], 'synthetic', 'b'*32, '失败', [])
        self.assertNotEqual(old_key, new_key)
        self.assertEqual(self.retry_row(new_key)['attempts'], 1)
        with self.pipeline.db() as db:
            self.assertEqual(db.execute('SELECT first_failed_at FROM retry_policy WHERE batch_id=?', (new_key,)).fetchone()[0], 2000)

    def test_not_sent_does_not_spend_attempt_or_window(self):
        doc = self.add('r-not-sent', '合成资料')
        class NotSent(ValueError): failure_kind = 'not_sent'
        self.pipeline.call_pi = lambda *args: (_ for _ in ()).throw(NotSent('本批未发送'))
        with module_time(_clue_source, time=lambda: 1000, monotonic=lambda: 0):
            self.run_analysis(deadline=300)
        with self.pipeline.db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM retry_policy').fetchone()[0], 0)
        self.assertFalse(self.seen(doc))

    def recovery_script(self):
        import importlib.util
        from pathlib import Path
        path = Path(__file__).resolve().parents[1]/'coordination/restore_clues_retry_20s.py'
        # hygiene-4：这个恢复脚本在 coordination/ 下，是私有协调证据，不进公开仓库。
        # 公开克隆里没有它就跳过依赖它的用例，其余照跑；本机有它时行为与从前一致。
        if not path.is_file():
            raise unittest.SkipTest(RECOVERY_SCRIPT_REASON)
        spec = importlib.util.spec_from_file_location('synthetic_recovery', path)
        module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        return module

    def prepare_grant(self, docs, now=1000):
        for n in range(3):
            with module_time(_clue_source, time=lambda: now):
                key = self.pipeline.defer_batch(docs, 'synthetic', str(n)*32, '失败', [])
        with self.pipeline.db() as db:
            result = self.recovery_script().prepare_recovery(db, [(key, len(docs), 0)], now=now,
                                                           epoch='synthetic-recovery', model='synthetic')
        self.assertEqual(result, 1)
        return key

    def test_recovery_migration_is_idempotent(self):
        doc = self.add('r-grant', '合成恢复')
        key = self.prepare_grant([doc])
        with self.pipeline.db() as db:
            self.assertEqual(self.recovery_script().prepare_recovery(db, [(key, 1, 0)], now=2000,
                              epoch='synthetic-recovery', model='synthetic'), 0)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM recovery_grants').fetchone()[0], 1)
        self.assertEqual(self.retry_row(key)['attempts'], 3)

    def test_recovery_token_is_claimed_once(self):
        docs = [self.add('r-grant-'+str(n), '合成恢复资料', chat=str(n)) for n in range(2)]
        keys = [self.prepare_grant([doc]) for doc in docs]
        class Transient(ValueError): failure_kind = 'model_transient'
        def failure(identity, prompt, *args):
            self.calls.append(identity); raise Transient('合成暂时模型故障')
        self.pipeline.call_pi = failure
        with module_time(_clue_source, time=lambda: 1001, monotonic=lambda: 0):
            self.run_analysis('x', incremental=True, deadline=300)
            self.assertEqual(len(self.calls), 2)
            self.run_analysis('y', incremental=True, deadline=300)
            self.assertEqual(len(self.calls), 2)
        for key in keys:
            self.assertEqual((self.retry_row(key)['attempts'], self.retry_row(key)['state']), (3, 'needs_review'))
        with self.pipeline.db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM recovery_grants WHERE claimed_at IS NOT NULL').fetchone()[0], 2)

    def test_recovery_claim_is_atomic_and_zero_send_can_release(self):
        doc=self.add('r-atomic','合成恢复资料')
        key=self.prepare_grant([doc])
        with module_time(_clue_source,time=lambda:1001):
            self.assertTrue(self.pipeline.claim_recovery([doc],'synthetic','x'*32))
            self.assertIsNone(self.pipeline.claim_recovery([doc],'synthetic','y'*32))
            folder=self.pipeline.root/'analysis-pi'/'synthetic-unsent'
            folder.mkdir(parents=True)
            (folder/'execution-receipt.json').write_text('{"sent":false}')
            self.pipeline.unsent_recovery([doc],'synthetic','x'*32,'synthetic-unsent')
            self.assertFalse(self.pipeline.pending(ready_only=True,model='synthetic',run_id='x'*32))
            self.assertTrue(self.pipeline.claim_recovery([doc],'synthetic','y'*32))
        self.assertEqual(self.retry_row(key)['attempts'],3)

    def test_recovery_incomplete_receipt_cannot_release_token(self):
        doc=self.add('r-receipt','合成缺字段回执')
        with module_time(_clue_source,time=lambda:1000):
            self.prepare_grant([doc])
            self.assertTrue(self.pipeline.claim_recovery([doc],'synthetic','x'*32))
            folder=self.pipeline.root/'analysis-pi'/'synthetic-incomplete'
            folder.mkdir(parents=True)
            (folder/'execution-receipt.json').write_text('{}')
            self.pipeline.unsent_recovery([doc],'synthetic','x'*32,'synthetic-incomplete')
            self.assertIsNone(self.pipeline.claim_recovery([doc],'synthetic','y'*32))
        self.assertEqual(self.retry_row(self.pipeline.batch_identity([doc],'synthetic'))['state'],'needs_review')

    def test_recovery_rejects_seen_and_rolls_back_all_targets(self):
        first=self.add('r-eligible','合成恢复一')
        second=self.add('r-ineligible','合成恢复二')
        keys=[]
        for doc in (first,second):
            for n in range(3):keys.append(self.pipeline.defer_batch([doc],'synthetic',str(n)*32,'失败',[]))
        with self.pipeline.db() as db:
            db.execute('INSERT INTO seen VALUES(?,?,?,?)',(second['id'],second['hash'],'synthetic','z'*32))
        with self.assertRaises(ValueError), self.pipeline.db() as db:
            self.recovery_script().prepare_recovery(db,[(keys[0],1,0),(keys[-1],1,0)],now=1000,model='synthetic')
        with self.pipeline.db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM recovery_grants').fetchone()[0],0)
        self.assertEqual(self.retry_row(keys[0])['state'],'needs_review')

    def test_partial_save_keeps_one_clue_id(self):
        doc = self.add('r-partial', '合成部分保存的技术资料')
        self.model(self.response)
        original = self.pipeline.report
        thrown=[]
        def report(run, phase, count, *args, **kwargs):
            if kwargs.get('clue_ids') and not thrown:
                thrown.append(True); raise sqlite3.OperationalError('synthetic report failure')
            return original(run, phase, count, *args, **kwargs)
        self.pipeline.report = report
        _, first = self.run_analysis()
        self.assertEqual(len(first['clues']), 1)
        self.pipeline.report = original
        with module_time(_clue_source, time=lambda: 1000):
            key = self.pipeline.defer_batch([doc], 'synthetic', 'a'*32, '合成保存后失败', first['clues'])
        with module_time(_clue_source, time=lambda: 2801):
            _, second = self.run_analysis('b', manual=True)
        self.assertEqual(self.store.clues.listing()['total'], 1)
        self.assertEqual(second['clues'], first['clues'])
        self.assertEqual(json.loads(self.retry_row(key)['clue_ids']), first['clues'])
        self.assertEqual(self.retry_row(key)['state'], 'resolved')

    def test_new_reference_contract_maps_unique_legacy_quotes_and_keeps_compatibility(self):
        doc = self.add('quote', '@Allen\u2005合成技术工具新增功能。')
        refs = self.pipeline.evidence_refs([doc])
        candidate = self.candidate(next(iter(refs)))
        candidate['quotes'] = [{'id': doc['id'], 'text': doc['body']}]
        answer = json.dumps({'clues': [candidate]}, ensure_ascii=False)
        self.assertEqual(len(self.pipeline.parse(answer, [doc])), 1)
        stats = {}
        self.assertEqual(len(self.pipeline.parse(answer, [doc], refs, stats=stats)), 1)
        self.assertEqual(stats['quote_mapped'], 1)
        self.model(answer)
        status, data = self.run_analysis()
        self.assertEqual(status, 'succeeded')
        self.assertEqual(len(self.calls), 1)
        self.assertTrue(self.seen(doc))
        self.assertEqual(len(data['clues']), 1)
        self.assertEqual(data['format']['quote_mapped'], 1)
        self.assertEqual(self.store.clues.listing()['total'], 1)

    def test_mixed_candidates_save_the_valid_clue_and_advance_the_batch(self):
        doc = self.add('mixed', '合成技术消息，包括值得调查的引用能力。')
        self.model(lambda prompt: self.response(prompt, mixed=True))
        status, data = self.run_analysis()
        # The unusable sibling is dropped on its own; the valid clue is saved and
        # the message is covered by it, so the batch advances in one pass.
        self.assertEqual(status, 'succeeded')
        self.assertEqual(data['processed'], 1)
        self.assertTrue(self.seen(doc))
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(len(data['clues']), 1)
        self.assertEqual(data['deferred'], 0)
        self.assertEqual(data['format']['dropped_clues'], 1)
        self.assertEqual(data['dropped'][0]['state'], 'covered')
        self.assertEqual(self.store.clues.listing()['total'], 1)
        original = data['clues'][0]
        status, retried = self.run_analysis('b', manual=True)
        self.assertEqual(retried['processed'], 0)
        self.assertEqual(retried['clues'], [])
        self.assertEqual(self.store.clues.listing()['total'], 1)

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
        with module_time(_clue_source, time=lambda: 1000):
            batch_id = self.pipeline.defer_batch([doc], 'synthetic', 'a' * 32, '合成校验失败', [])
            first = self.pipeline.retry_queue('synthetic')[0]
            self.assertEqual(first['attempts'], 1)
            self.assertEqual(first['next_retry_at'], datetime.fromtimestamp(2800, timezone.utc).isoformat())
            self.assertEqual(self.pipeline.pending(ready_only=True, model='synthetic', run_id='b' * 32), [])
            self.assertEqual(len(self.pipeline.pending(ready_only=True, model='synthetic', run_id='b' * 32, manual=True)), 1)
            self.assertEqual(self.pipeline.pending(ready_only=True, model='synthetic', run_id='a' * 32, manual=True), [])
        restarted = CluePipeline(self.store)
        with module_time(_clue_source, time=lambda: 2801):
            self.assertEqual(len(restarted.pending(ready_only=True, model='synthetic', run_id='b' * 32)), 1)
            self.assertEqual(restarted.defer_batch([doc], 'synthetic', 'b' * 32, '仍未通过', []), batch_id)
            second = restarted.retry_queue('synthetic')[0]
            self.assertEqual(second['attempts'], 2)
            self.assertEqual(second['next_retry_at'], datetime.fromtimestamp(6401, timezone.utc).isoformat())
        with module_time(_clue_source, time=lambda: 6402):
            restarted.defer_batch([doc], 'synthetic', 'c' * 32, '仍未通过', [])
        with module_time(_clue_source, time=lambda: 99999):
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

    def test_primary_valid_candidate_survives_a_dropped_sibling_and_advances(self):
        doc = self.add('repair-invalid', '合成主调用已经发现有出处的技术线索')
        self.model(lambda prompt: self.response(prompt, mixed=True) if len(self.calls) == 1 else 'not-json')
        status, data = self.run_analysis()
        self.assertEqual(status, 'succeeded')
        self.assertEqual(data['processed'], 1)
        self.assertEqual(len(data['clues']), 1)
        self.assertEqual(len(self.calls), 1)
        self.assertTrue(self.seen(doc))
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

    def test_unknown_fields_are_ignored_counted_and_never_stored(self):
        doc = self.add('extra-fields', '合成有明确出处的技术更新消息')
        refs = self.pipeline.evidence_refs([doc])
        injected = self.candidate(next(iter(refs)))
        injected['actions'] = [{'tool': 'run_command', 'command': 'must-never-execute'}]
        injected['confidence'] = 0.9
        stats = {}
        saved = CluePipeline.parse(json.dumps({'clues': [injected]}), [doc], refs, stats=stats)
        # Key damage outside the contract is ignored and counted; it can neither
        # reach the clue store nor void the candidate's real evidence.
        self.assertEqual(stats['ignored_fields'], 2)
        self.assertNotIn('actions', saved[0]); self.assertNotIn('confidence', saved[0])
        self.model(json.dumps({'clues': [injected]}, ensure_ascii=False))
        status, data = self.run_analysis()
        self.assertEqual(status, 'succeeded')
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(data['processed'], 1)
        self.assertEqual(data['format']['ignored_fields'], 2)
        self.assertTrue(self.seen(doc))
        self.assertEqual(self.store.clues.listing()['total'], 1)
        self.assertEqual(self.store.events.list_events()['events'], [])
        clue = self.store.clues.detail(data['clues'][0])
        self.assertNotIn('actions', clue)
        self.assertNotIn('confidence', clue)

    def test_field_names_lose_surrounding_whitespace_and_are_counted(self):
        doc = self.add('padded-keys', '合成字段名带空格的技术更新消息')
        refs = self.pipeline.evidence_refs([doc])
        clean = self.candidate(next(iter(refs)))
        damaged = {' title': clean['title'], 'summary ': clean['summary'], 'categories': clean['categories'],
                   'relevance': clean['relevance'], 'kind': clean['kind'], 'priority': clean['priority'],
                   'quotes': clean['quotes'], 'id': 'ignored-by-contract'}
        stats = {}
        saved = CluePipeline.parse(json.dumps({'clues': [damaged]}), [doc], refs, stats=stats)
        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0]['title'], clean['title'])
        self.assertEqual(stats['field_names'], 2)
        self.assertEqual(stats['ignored_fields'], 1)
        # The same answer that used to park the batch now advances it in one pass.
        self.model(json.dumps({'clues': [damaged]}, ensure_ascii=False))
        status, data = self.run_analysis()
        self.assertEqual(status, 'succeeded')
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(data['processed'], 1)
        self.assertEqual(data['deferred'], 0)
        self.assertTrue(self.seen(doc))
        self.assertEqual(self.store.clues.listing()['total'], 1)

    def test_repair_prompt_names_the_exact_allowed_schema(self):
        doc = self.add('repair-schema', '合成需要一次格式修复的技术更新消息')
        def response(prompt):
            if len(self.calls) == 1: return 'not-json'
            repair = prompt.split('\n上次返回未通过格式校验：', 1)[1]
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
        self.model('not-json')
        self.run_analysis()
        self.model(self.response)
        status, second = self.run_analysis('b', manual=True)
        self.assertEqual(status, 'succeeded')
        self.assertEqual(second['processed'], 1)
        self.assertEqual(len(second['clues']), 1)
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
        with module_time(_clue_source, monotonic=lambda: clock[0]):
            status, data = self.run_analysis(incremental=True, deadline=90)
        self.assertEqual(status, 'timeout')
        self.assertEqual(data['processed'], 1)
        self.assertEqual(len(data['clues']), 1)
        self.assertEqual(self.pipeline.status()['processed_versions'], 1)
        with self.pipeline.db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM failed_batches').fetchone()[0], 0)


if __name__ == '__main__':
    unittest.main()
