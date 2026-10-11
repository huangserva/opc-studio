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
