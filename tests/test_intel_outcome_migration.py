"""fixes-intel-manual-status：jobs.outcome 列的迁移与安全解析。

只读/只写临时库（TemporaryDirectory），不碰正式 intel.db，不联网、不读凭证。
旧库（没有 outcome 列）迁移必须幂等；旧行/坏 JSON/未知版本一律「不可用」，不猜计数。
"""
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from intel_store import (Intel, OUTCOME_VERSION, build_outcome, decide_status,
                         body_stage, reduce_body, reduce_save, route_record, save_stage)
from server import Store

BIZ = 'MzB5NTkwMDAwMDE='
NAME = '占位公众号'

OLD_JOBS_SCHEMA = '''CREATE TABLE jobs(
    id TEXT PRIMARY KEY, fakeid TEXT NOT NULL, name TEXT NOT NULL DEFAULT '',
    mode TEXT NOT NULL, status TEXT NOT NULL, reason TEXT NOT NULL DEFAULT '',
    detail TEXT NOT NULL DEFAULT '', routes TEXT NOT NULL DEFAULT '', found INTEGER NOT NULL DEFAULT 0,
    added INTEGER NOT NULL DEFAULT 0, bodies INTEGER NOT NULL DEFAULT 0,
    pages INTEGER NOT NULL DEFAULT 0, started_at TEXT NOT NULL, finished_at TEXT NOT NULL DEFAULT '',
    UNIQUE(id))'''


class OutcomeMigrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(self.tmp.name)
        self.path = Path(self.tmp.name) / 'intel' / 'intel.db'
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        self.tmp.cleanup()

    def columns(self):
        with sqlite3.connect(self.path) as connection:
            return {row[1] for row in connection.execute('PRAGMA table_info(jobs)')}

    def test_old_schema_is_migrated_idempotently_and_old_rows_stay_unavailable(self):
        # 造一个没有 outcome 列的旧库，并放一条旧 done 行。
        with sqlite3.connect(self.path) as connection:
            connection.execute(OLD_JOBS_SCHEMA)
            connection.execute("INSERT INTO jobs(id,fakeid,name,mode,status,started_at) "
                               "VALUES('old',?,?,'latest','done','2020-01-01T00:00:00+00:00')",
                               (BIZ, NAME))
        intel = Intel(self.store)
        intel._ensure()
        first = self.columns()
        self.assertIn('outcome', first)
        # 第二次初始化不重复加列，列集合不变。
        Intel(self.store)._ensure()
        self.assertEqual(self.columns(), first)
        # 旧行保持原状态，outcome 为空 = 不可用，不猜计数。
        row = intel.job('old')
        self.assertEqual(row['status'], 'done')
        self.assertIsNone(row['outcome'])
        self.assertFalse(row['outcome_available'])

    def test_version1_outcome_round_trips_through_the_public_job(self):
        intel = Intel(self.store)
        intel._ensure()
        outcome = build_outcome('complete', 3, 3, {'album': route_record('complete', True, 1, 3)},
                                'complete', save_stage('complete', 3, 3, 2, 1, 0, 0),
                                'not_requested', body_stage('not_requested'))
        with intel.db() as connection:
            connection.execute("INSERT INTO jobs(id,fakeid,name,mode,status,started_at,outcome) "
                               "VALUES('v1',?,?,'latest','done',?,?)",
                               (BIZ, NAME, '2020-01-01T00:00:00+00:00',
                                json.dumps(outcome, ensure_ascii=False)))
        row = intel.job('v1')
        self.assertTrue(row['outcome_available'])
        self.assertEqual(row['outcome']['version'], OUTCOME_VERSION)
        self.assertEqual(row['outcome']['save']['succeeded'], 3)
        self.assertEqual(row['outcome']['summary'], outcome['summary'])

    def test_body_not_started_state_round_trips(self):
        """M2 新增的正文 not_started 阶段枚举要能往返解析，不当作坏结构。"""
        intel = Intel(self.store)
        intel._ensure()
        outcome = build_outcome('complete', 3, 3, {'album': route_record('complete', True, 1, 3)},
                                'partial', save_stage('partial', 2, 1, 1, 0, 1, 1),
                                'not_started', body_stage('not_started', 3, 3))
        with intel.db() as connection:
            connection.execute("INSERT INTO jobs(id,fakeid,name,mode,status,started_at,outcome) "
                               "VALUES('ns',?,?,'latest','partial',?,?)",
                               (BIZ, NAME, '2020-01-01T00:00:00+00:00',
                                json.dumps(outcome, ensure_ascii=False)))
        row = intel.job('ns')
        self.assertTrue(row['outcome_available'])
        self.assertEqual(row['outcome']['body']['state'], 'not_started')
        self.assertIn('正文未开始', row['outcome']['summary'])

    def test_bad_json_and_unknown_version_are_unavailable(self):
        intel = Intel(self.store)
        intel._ensure()
        rows = {'bad': '{不是 JSON', 'v9': json.dumps({'version': 99, 'summary': 'x'}),
                'empty': ''}
        with intel.db() as connection:
            for job_id, raw in rows.items():
                connection.execute("INSERT INTO jobs(id,fakeid,name,mode,status,started_at,outcome) "
                                   "VALUES(?,?,?,'latest','done',?,?)",
                                   (job_id, BIZ, NAME, '2020-01-01T00:00:00+00:00', raw))
        for job_id in rows:
            row = intel.job(job_id)
            self.assertIsNone(row['outcome'], job_id)
            self.assertFalse(row['outcome_available'], job_id)

    def test_missing_stage_or_state_is_unavailable(self):
        """结构不符（缺段/未知状态）也算不可用，不把半截 JSON 当成计数。"""
        intel = Intel(self.store)
        intel._ensure()
        broken = {'missing': json.dumps({'version': 1, 'list': {'state': 'complete'},
                                         'save': {'state': 'complete'}, 'summary': 'x'}),
                  'badstate': json.dumps({'version': 1, 'list': {'state': '???'},
                                          'save': {'state': 'complete'},
                                          'body': {'state': 'not_requested'}, 'summary': 'x'})}
        with intel.db() as connection:
            for job_id, raw in broken.items():
                connection.execute("INSERT INTO jobs(id,fakeid,name,mode,status,started_at,outcome) "
                                   "VALUES(?,?,?,'latest','done',?,?)",
                                   (job_id, BIZ, NAME, '2020-01-01T00:00:00+00:00', raw))
        for job_id in broken:
            self.assertFalse(intel.job(job_id)['outcome_available'], job_id)

    def test_owner_columns_migrate_alongside_outcome_and_old_row_stays_legacy(self):
        """lease-b L：迁移同时幂等补 owner 四列；旧行不因缺 outcome 被推断成 v1。"""
        with sqlite3.connect(self.path) as connection:
            connection.execute(OLD_JOBS_SCHEMA)
            connection.execute("INSERT INTO jobs(id,fakeid,name,mode,status,started_at) "
                               "VALUES('old',?,?,'latest','done','2020-01-01T00:00:00+00:00')",
                               (BIZ, NAME))
        intel = Intel(self.store)
        intel._ensure()
        with sqlite3.connect(self.path) as connection:
            columns = {row[1] for row in connection.execute('PRAGMA table_info(jobs)')}
            raw = connection.execute("SELECT ownership_version,owner_token,owner_dev,owner_ino "
                                     "FROM jobs WHERE id='old'").fetchone()
        self.assertTrue({'ownership_version', 'owner_token', 'owner_dev', 'owner_ino'} <= columns)
        self.assertEqual(tuple(raw), (0, '', 0, 0))
        row = intel.job('old')
        self.assertIsNone(row['outcome'])
        self.assertEqual(row['ownership_state'], 'legacy')
        self.assertNotIn('owner_token', row)

    def test_reducers_keep_the_counting_invariants(self):
        # attempted = succeeded + failed；selected = attempted + not_attempted
        self.assertEqual(reduce_save(3, 2, 1, 0), 'partial')
        self.assertEqual(reduce_save(3, 3, 0, 0), 'complete')
        self.assertEqual(reduce_save(3, 0, 3, 0), 'failed')
        self.assertEqual(reduce_save(0, 0, 0, 0), 'not_attempted')
        self.assertEqual(reduce_body(0, 0, 0), 'not_requested')
        self.assertEqual(reduce_body(3, 0, 0), 'complete')
        self.assertEqual(reduce_body(3, 2, 1), 'partial')
        self.assertEqual(reduce_body(3, 2, 0), 'failed')
        outcome = build_outcome('complete', 3, 3, {}, 'partial',
                                save_stage('partial', 3, 2, 1, 1, 1, 0),
                                'not_requested', body_stage('not_requested'))
        saved = outcome['save']
        self.assertEqual(saved['attempted'], saved['succeeded'] + saved['failed'])
        self.assertEqual(decide_status(outcome), 'partial')
        # 列表/保存任一 failed → error
        self.assertEqual(decide_status(build_outcome('complete', 1, 1, {}, 'failed',
                                                     save_stage('failed', 1, 0, 0, 0, 1, 0),
                                                     'not_requested', body_stage('not_requested'))), 'error')
        self.assertEqual(decide_status(build_outcome('failed', 0, 0, {}, 'not_attempted',
                                                     save_stage('not_attempted'),
                                                     'not_requested', body_stage('not_requested'))), 'error')
        # 中断不可被覆盖
        self.assertEqual(decide_status(outcome, interrupted=True), 'interrupted')


if __name__ == '__main__':
    unittest.main()
