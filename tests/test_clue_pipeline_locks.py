"""Offline retry transaction and same-run receipt regression tests."""
import json
import sqlite3
import tempfile
import threading
import unittest
from contextlib import contextmanager
from unittest.mock import patch
from isolation_patches import WECHAT_SOURCE_REASON, wechat_source_available
# hygiene-4：下面的 import 会加载微信来源插件（clue_pipeline 等在模块级调
# wechat_source_loader.source_module）。插件由 ARTICLE_STUDIO_WECHAT_MODULE 或
# workspace/sources/wechat.json 指定，两者都是本机资源，不进公开仓库；
# 干净克隆里没有它，这里直接跳过整个模块（unittest 会把 SkipTest 记成 skip
# 而不是 error）。本机配置齐全时行为与从前逐字一致。
if not wechat_source_available():
    raise unittest.SkipTest(WECHAT_SOURCE_REASON)

from clue_pipeline import CluePipeline
from server import Store
from isolation_patches import module_time
from wechat_source_loader import source_module

SOURCE = source_module('clue_pipeline')


class CluePipelineLocksTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir='/tmp')
        self.addCleanup(self.tmp.cleanup)
        self.pipeline = CluePipeline(Store(self.tmp.name))
        self.batch = [{'id': 'synthetic-document', 'hash': 'synthetic-hash'}]

    def defer(self):
        with module_time(SOURCE, time=lambda: 1000):
            return self.pipeline.defer_batch(self.batch, 'fake-model', 'first-run', 'synthetic', ['old'])

    def grant(self, identity, due=1000, expires=100000):
        with self.pipeline.db() as db:
            db.execute('INSERT INTO recovery_grants VALUES(?,?,?,?,NULL,NULL)',
                       (identity, 'epoch', due, expires))

    @contextmanager
    def traced(self):
        statements = []
        original = self.pipeline.db
        @contextmanager
        def db():
            with original() as connection:
                connection.set_trace_callback(statements.append)
                yield connection
        with patch.object(self.pipeline, 'db', db):
            yield statements

    def expire(self, now):
        with module_time(SOURCE, time=lambda: now):
            with self.pipeline.db() as db:
                self.pipeline._expire_retries(db)

    def test_empty_and_unexpired_expiry_do_not_begin_write(self):
        for populated in (False, True):
            if populated: self.defer()
            with self.traced() as sql:
                self.expire(1100)
            self.assertFalse(any('BEGIN IMMEDIATE' in s.upper() for s in sql), sql)

    def test_no_grant_and_ineligible_grants_do_not_begin_write(self):
        identity = self.defer()
        for state in ('none', 'future', 'claimed', 'expired'):
            with self.pipeline.db() as db:
                db.execute('DELETE FROM recovery_grants')
            if state != 'none':
                self.grant(identity, due=2000 if state == 'future' else 1000,
                           expires=1100 if state == 'expired' else 100000)
                if state == 'claimed':
                    with self.pipeline.db() as db:
                        db.execute('UPDATE recovery_grants SET claimed_at=1000')
            with self.traced() as sql, module_time(SOURCE, time=lambda: 1200):
                result = self.pipeline.claim_recovery(self.batch, 'fake-model', 'next-run')
            self.assertIs(result, False if state == 'none' else None)
            self.assertFalse(any('BEGIN IMMEDIATE' in s.upper() for s in sql), sql)

    def test_expiry_behavior_and_second_call_no_write(self):
        identity = self.defer()
        self.expire(87400)
        with self.pipeline.db() as db:
            for table in ('failed_batches', 'retry_documents'):
                row = db.execute('SELECT state,next_retry_at,attempts FROM '+table).fetchone()
                self.assertEqual(tuple(row), ('needs_review', None, 1))
        with self.traced() as sql: self.expire(87400)
        self.assertFalse(any('BEGIN IMMEDIATE' in s.upper() for s in sql), sql)

    def test_same_run_merges_new_ids_without_changing_other_fields(self):
        identity = self.defer()
        with self.pipeline.db() as db:
            before = dict(db.execute('SELECT * FROM failed_batches').fetchone())
        with module_time(SOURCE, time=lambda: 99999):
            self.pipeline.defer_batch(self.batch, 'fake-model', 'first-run', 'new error', ['old', 'new', 'new'])
        with self.pipeline.db() as db:
            after = dict(db.execute('SELECT * FROM failed_batches').fetchone())
        self.assertEqual(json.loads(after.pop('clue_ids')), ['old', 'new'])
        before.pop('clue_ids')
        self.assertEqual(after, before)

    def test_expiry_rechecks_after_writer_changes_state(self):
        identity = self.defer()
        original = self.pipeline.db
        fired = []
        @contextmanager
        def db():
            with original() as connection:
                def trace(sql):
                    if sql.upper() == 'BEGIN IMMEDIATE' and not fired:
                        fired.append(True)
                        with original() as other:
                            other.execute("UPDATE failed_batches SET state='resolved' WHERE id=?", (identity,))
                            other.execute("UPDATE retry_documents SET state='resolved' WHERE batch_id=?", (identity,))
                connection.set_trace_callback(trace)
                yield connection
        with patch.object(self.pipeline, 'db', db): self.expire(87400)
        with original() as connection:
            self.assertEqual(connection.execute('SELECT state FROM failed_batches').fetchone()[0], 'resolved')

    def test_claim_rechecks_grant_after_competing_writer(self):
        identity = self.defer(); self.grant(identity)
        original = self.pipeline.db
        fired = []
        @contextmanager
        def db():
            with original() as connection:
                def trace(sql):
                    if sql.upper() == 'BEGIN IMMEDIATE' and not fired:
                        fired.append(True)
                        with original() as other:
                            other.execute("UPDATE recovery_grants SET claimed_at=1100,claimed_run_id='competitor'")
                connection.set_trace_callback(trace)
                yield connection
        with patch.object(self.pipeline, 'db', db), module_time(SOURCE, time=lambda: 1200):
            self.assertIsNone(self.pipeline.claim_recovery(self.batch, 'fake-model', 'loser'))
        with original() as connection:
            self.assertEqual(connection.execute('SELECT claimed_run_id FROM recovery_grants').fetchone()[0], 'competitor')

    def test_two_connections_expire_once(self):
        self.defer()
        barrier = threading.Barrier(2)
        changes = []; errors = []
        def worker():
            try:
                with self.pipeline.db() as db:
                    db.set_trace_callback(lambda sql: barrier.wait(timeout=5) if sql.upper() == 'BEGIN IMMEDIATE' else None)
                    self.pipeline._expire_retries(db)
                    changes.append(db.total_changes)
            except Exception as exc: errors.append(exc)
        # Real local threads; no process-wide Thread/time monkeypatch.
        with module_time(SOURCE, time=lambda: 87400):
            threads = [threading.Thread(target=worker) for _ in range(2)]
            for thread in threads: thread.start()
            for thread in threads: thread.join(timeout=10)
        self.assertFalse(any(t.is_alive() for t in threads))
        self.assertEqual(errors, [])
        self.assertEqual(sorted(changes), [0, 2])

    # ── R13：锁后重读时钟 / 坏回执兜底 / recovery_grants 复合主键非空 ──────────

    def advancing_clock(self, start=1000):
        clock = {'now': start}
        return clock, (lambda: clock['now'])

    def lock_waits_until(self, clock, when):
        """在 BEGIN IMMEDIATE 真正拿到写锁前推进时钟，模拟等锁期间时间流逝。"""
        original = self.pipeline.db
        @contextmanager
        def db():
            with original() as connection:
                def trace(sql):
                    if sql.upper() == 'BEGIN IMMEDIATE':
                        clock['now'] = when
                connection.set_trace_callback(trace)
                yield connection
        return patch.object(self.pipeline, 'db', db)

    def test_claim_rereads_the_clock_after_the_write_lock(self):
        identity = self.defer(); self.grant(identity, due=1000, expires=1200)
        clock, now = self.advancing_clock()
        with self.lock_waits_until(clock, 5000), module_time(SOURCE, time=now):
            self.assertIsNone(self.pipeline.claim_recovery(self.batch, 'fake-model', 'late-run'))
        with self.pipeline.db() as db:
            self.assertIsNone(db.execute('SELECT claimed_at FROM recovery_grants').fetchone()[0])

    def test_claimed_at_stamps_the_post_lock_clock(self):
        identity = self.defer(); self.grant(identity)
        clock, now = self.advancing_clock()
        with self.lock_waits_until(clock, 5000), module_time(SOURCE, time=now):
            self.assertIs(self.pipeline.claim_recovery(self.batch, 'fake-model', 'late-run'), True)
        with self.pipeline.db() as db:
            self.assertEqual(tuple(db.execute('SELECT claimed_at,claimed_run_id FROM recovery_grants').fetchone()), (5000, 'late-run'))

    def corrupt(self, clue_ids):
        identity = self.defer()
        with self.pipeline.db() as db:
            db.execute('UPDATE failed_batches SET clue_ids=? WHERE id=?', (clue_ids, identity))
        return identity

    def test_corrupt_same_run_receipt_reports_the_batch_not_a_json_error(self):
        identity = self.corrupt('NOT-JSON')
        with module_time(SOURCE, time=lambda: 99999):
            with self.assertRaises(ValueError) as caught:
                self.pipeline.defer_batch(self.batch, 'fake-model', 'first-run', 'new error', ['new'])
        message = str(caught.exception)
        self.assertIn('clue_ids', message); self.assertIn(identity, message)
        self.assertNotIn('Expecting value', message)
        with self.pipeline.db() as db:
            self.assertEqual(db.execute('SELECT clue_ids FROM failed_batches WHERE id=?', (identity,)).fetchone()[0], 'NOT-JSON')

    def test_corrupt_cross_run_receipt_reports_the_batch_not_a_json_error(self):
        identity = self.corrupt('{"clue_ids": broken')
        with module_time(SOURCE, time=lambda: 99999):
            with self.assertRaises(ValueError) as caught:
                self.pipeline.defer_batch(self.batch, 'fake-model', 'second-run', 'another failure', ['new'])
        self.assertIn('clue_ids', str(caught.exception)); self.assertIn(identity, str(caught.exception))
        with self.pipeline.db() as db:
            self.assertEqual(db.execute('SELECT clue_ids FROM failed_batches WHERE id=?', (identity,)).fetchone()[0], '{"clue_ids": broken')

    def test_non_list_receipt_is_a_clear_error_not_a_type_error(self):
        identity = self.corrupt('null')
        with module_time(SOURCE, time=lambda: 99999):
            with self.assertRaises(ValueError) as caught:
                self.pipeline.defer_batch(self.batch, 'fake-model', 'second-run', 'another failure', ['new'])
        self.assertIn('clue_ids', str(caught.exception))

    def test_valid_receipt_still_merges_after_the_guard(self):
        identity = self.defer()
        with module_time(SOURCE, time=lambda: 99999):
            self.pipeline.defer_batch(self.batch, 'fake-model', 'second-run', 'again', ['new'])
        with self.pipeline.db() as db:
            self.assertEqual(json.loads(db.execute('SELECT clue_ids FROM failed_batches WHERE id=?', (identity,)).fetchone()[0]), ['old', 'new'])

    def test_recovery_grants_composite_key_rejects_null_columns(self):
        with self.pipeline.db() as db:
            for column in ('batch_id', 'epoch'):
                values = [None if name == column else 'synthetic' for name in ('batch_id', 'epoch')]
                with self.assertRaises(sqlite3.IntegrityError):
                    db.execute('INSERT INTO recovery_grants VALUES(?,?,?,?,NULL,NULL)', (*values, 1000, 2000))
            self.assertEqual(db.execute('SELECT COUNT(*) FROM recovery_grants').fetchone()[0], 0)

    def test_fresh_table_needs_no_hardening(self):
        self.assertEqual(self.pipeline.harden_recovery_grants(), {'changed': False, 'nullable_keys': [], 'rows': 0})

    def age_table(self, rows=()):
        with self.pipeline.db() as db:
            db.execute('DROP TABLE recovery_grants')
            db.execute('CREATE TABLE recovery_grants(batch_id TEXT,epoch TEXT,due_at REAL NOT NULL,'
                       'expires_at REAL NOT NULL,claimed_at REAL,claimed_run_id TEXT,PRIMARY KEY(batch_id,epoch))')
            db.executemany('INSERT INTO recovery_grants VALUES(?,?,?,?,?,?)', list(rows))

    def test_legacy_table_hardening_is_checked_then_applied_idempotently(self):
        self.age_table([('batch', 'epoch', 1000, 2000, None, None), ('other', 'epoch', 1000, 2000, 1500, 'run')])
        self.assertEqual(self.pipeline.harden_recovery_grants(),
                         {'changed': False, 'nullable_keys': ['batch_id', 'epoch'], 'rows': 2, 'would_rebuild': True})
        with self.pipeline.db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM recovery_grants').fetchone()[0], 2)
        self.assertEqual(self.pipeline.harden_recovery_grants(apply=True),
                         {'changed': True, 'nullable_keys': ['batch_id', 'epoch'], 'rows': 2})
        with self.pipeline.db() as db:
            self.assertEqual([tuple(r) for r in db.execute('SELECT * FROM recovery_grants ORDER BY batch_id')],
                             [('batch', 'epoch', 1000, 2000, None, None), ('other', 'epoch', 1000, 2000, 1500, 'run')])
            self.assertEqual([r[1] for r in db.execute('PRAGMA table_info(recovery_grants)') if r[5] and not r[3]], [])
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute('INSERT INTO recovery_grants VALUES(?,?,?,?,NULL,NULL)', (None, None, 1000, 2000))
        self.assertEqual(self.pipeline.harden_recovery_grants(apply=True), {'changed': False, 'nullable_keys': [], 'rows': 2})

    def test_legacy_hardening_refuses_rows_needing_manual_review(self):
        # 旧形状下 NULL 主键能重复插入，这正是需要人工核对的脏行。
        self.age_table([(None, 'epoch', 1000, 2000, None, None), (None, 'epoch', 3000, 4000, None, None),
                        ('batch', 'epoch', 1000, 2000, None, None)])
        report = self.pipeline.harden_recovery_grants(apply=True)
        self.assertFalse(report['changed']); self.assertIn('NULL', report['blocked']); self.assertIn('重复', report['blocked'])
        with self.pipeline.db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM recovery_grants').fetchone()[0], 3)
            self.assertEqual([r[1] for r in db.execute('PRAGMA table_info(recovery_grants)') if r[5] and not r[3]], ['batch_id', 'epoch'])

    # ── R13-b：过期收口保护在途授权；未发送后状态与授权一致 ────────────────

    def claimed_grant(self, identity, due=1000, expires=100000, run='owner-run'):
        """插一条已被认领、且仍在有效期内的恢复授权（在途状态）。"""
        self.grant(identity, due=due, expires=expires)
        with self.pipeline.db() as db:
            db.execute('UPDATE recovery_grants SET claimed_at=?,claimed_run_id=?', (due, run))

    def test_expiry_protects_an_inflight_claimed_grant(self):
        # 首次失败已超 24h，但 grant 被本次运行认领且未过期：读队列不得收口。
        identity = self.defer(); self.claimed_grant(identity)
        with self.traced() as sql:
            self.expire(87400)
        self.assertFalse(any('BEGIN IMMEDIATE' in s.upper() for s in sql), sql)
        with self.pipeline.db() as db:
            for table in ('failed_batches', 'retry_documents'):
                self.assertEqual(tuple(db.execute('SELECT state,next_retry_at FROM '+table).fetchone()), ('waiting', 2800))

    def test_expiry_still_closes_a_claimed_but_expired_grant(self):
        # 认领过但窗口已过：仍按旧失败窗正常收口，保护不能放宽到过期授权。
        identity = self.defer(); self.claimed_grant(identity, due=1000, expires=2000)
        self.expire(87400)
        with self.pipeline.db() as db:
            for table in ('failed_batches', 'retry_documents'):
                self.assertEqual(tuple(db.execute('SELECT state,next_retry_at FROM '+table).fetchone()), ('needs_review', None))

    def unsent_receipt(self, identity, sent):
        folder = self.pipeline.root/'analysis-pi'/identity
        folder.mkdir(parents=True, exist_ok=True)
        (folder/'execution-receipt.json').write_text(json.dumps({'sent': sent}))

    def test_unsent_recovery_restores_state_matching_the_authorization(self):
        # 模拟读队列已把在途批次收成 needs_review：明确未发送后要还原成
        # waiting，授权放回未认领，next_retry_at 与授权窗口起点一致。
        identity = self.defer(); self.claimed_grant(identity, due=90000, expires=110000)
        with self.pipeline.db() as db:
            db.execute("UPDATE failed_batches SET state='needs_review',next_retry_at=NULL WHERE id=?", (identity,))
            db.execute("UPDATE retry_documents SET state='needs_review',next_retry_at=NULL WHERE batch_id=?", (identity,))
        self.unsent_receipt('synthetic-unsent', False)
        with module_time(SOURCE, time=lambda: 100000):
            self.pipeline.unsent_recovery(self.batch, 'fake-model', 'owner-run', 'synthetic-unsent')
        with self.pipeline.db() as db:
            self.assertEqual(tuple(db.execute('SELECT state,next_retry_at FROM failed_batches').fetchone()), ('waiting', 90000))
            self.assertEqual(tuple(db.execute('SELECT state,next_retry_at FROM retry_documents').fetchone()), ('waiting', 90000))
            self.assertIsNone(db.execute('SELECT claimed_at FROM recovery_grants').fetchone()[0])

    def test_sent_recovery_stays_parked_and_keeps_the_claim(self):
        # 回执明确已发送：批次保持 needs_review，授权仍被认领，不回退成普通重试。
        identity = self.defer(); self.claimed_grant(identity, due=90000, expires=110000)
        self.unsent_receipt('synthetic-sent', True)
        with module_time(SOURCE, time=lambda: 100000):
            self.pipeline.unsent_recovery(self.batch, 'fake-model', 'owner-run', 'synthetic-sent')
        with self.pipeline.db() as db:
            self.assertEqual(tuple(db.execute('SELECT state,next_retry_at FROM failed_batches').fetchone()), ('needs_review', None))
            self.assertEqual(db.execute('SELECT claimed_run_id FROM recovery_grants').fetchone()[0], 'owner-run')

    def test_two_connections_claim_once(self):
        identity = self.defer(); self.grant(identity)
        barrier = threading.Barrier(2)
        original = self.pipeline.db
        @contextmanager
        def db():
            with original() as connection:
                connection.set_trace_callback(lambda sql: barrier.wait(timeout=5) if sql.upper() == 'BEGIN IMMEDIATE' else None)
                yield connection
        results = []; errors = []
        def worker(run):
            try: results.append(self.pipeline.claim_recovery(self.batch, 'fake-model', run))
            except Exception as exc: errors.append(exc)
        with patch.object(self.pipeline, 'db', db), module_time(SOURCE, time=lambda: 1200):
            threads = [threading.Thread(target=worker, args=(str(i),)) for i in range(2)]
            for thread in threads: thread.start()
            for thread in threads: thread.join(timeout=10)
        self.assertFalse(any(t.is_alive() for t in threads))
        self.assertEqual(errors, [])
        self.assertCountEqual(results, [True, None])
