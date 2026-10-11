"""启动失败与库写失败的回归；仅临时库和合成线程。"""
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import intel_store
from tests.test_round4_intel_rate_limit import Round4TestCase, BIZ


@contextmanager
def failing_intel_start(error):
    """只让名字以 intel-mp- 开头的抓取线程启动失败，其余线程仍调用原来的 start。"""
    original = threading.Thread.start

    def start(worker):
        if str(getattr(worker, 'name', '')).startswith('intel-mp-'):
            raise error
        return original(worker)

    with patch('intel_store.threading.Thread.start', start):
        yield original


class IntelStartFailureTests(Round4TestCase):
    def setUp(self):
        super().setUp()
        self.intel.subscribe({'fakeid': BIZ, 'name': '测试'})

    def tearDown(self):
        path = str(self.intel.path.resolve())
        try:
            super().tearDown()
        finally:
            with intel_store._PROCESS_WORKERS_LOCK:
                own = {key for key in intel_store._PROCESS_ABANDONED if key[0] == path}
                intel_store._PROCESS_ABANDONED.difference_update(own)
                self.assertFalse(any(key[0] == path for key in intel_store._PROCESS_ABANDONED))

    def test_finish_fails_twice_original_exception_and_no_reuse(self):
        original = RuntimeError('合成启动失败')
        with failing_intel_start(original), \
                patch.object(self.intel, '_finish_job', side_effect=sqlite3.OperationalError('合成库锁')) as finish, \
                self.assertLogs('intel_store', level='ERROR') as logs:
            with self.assertRaises(ValueError) as raised:
                self.intel.run({'fakeid': BIZ, 'mode': 'latest', 'sogou': False})
        self.assertIs(raised.exception.__cause__, original)
        self.assertIn('任务线程没有启动，已中断', str(raised.exception))
        self.assertIn('合成启动失败', str(raised.exception))
        self.assertEqual(finish.call_count, 2)
        self.assertIn('合成库锁', '\n'.join(logs.output))
        failed = self.intel.jobs(1)[0]
        self.assertIn('合成库锁', failed['detail'])
        self.assertIn('合成启动失败', failed['reason'])
        self.assertEqual(failed['status'], 'running')
        self.assertIsNone(self.intel.active_job())
        self.assertIn(failed['id'], self.intel._abandoned_ids())
        self.assertIsNone(self.intel.status()['active_job'])
        new = self.intel.run({'fakeid': BIZ, 'mode': 'latest', 'sogou': False})
        self.assertNotEqual(new['id'], failed['id'])
        self.assertFalse(new.get('reused', False))

    def test_finish_retry_recovers(self):
        finish = self.intel._finish_job
        calls = []
        def flaky(*args, **kwargs):
            calls.append(args)
            if len(calls) == 1:
                raise sqlite3.OperationalError('一次库锁')
            return finish(*args, **kwargs)
        with failing_intel_start(RuntimeError('启动失败')), \
                patch.object(self.intel, '_finish_job', side_effect=flaky), self.assertLogs('intel_store'):
            with self.assertRaises(ValueError) as raised:
                self.intel.run({'fakeid': BIZ, 'mode': 'latest', 'sogou': False})
        self.assertIn('任务线程没有启动，已中断', str(raised.exception))
        self.assertIn('启动失败', str(raised.exception))
        self.assertIsInstance(raised.exception.__cause__, RuntimeError)
        self.assertEqual(len(calls), 2)
        settled = self.intel.jobs(1)[0]
        self.assertEqual(settled['status'], 'interrupted')
        self.assertIn('一次库锁', settled['detail'])
        self.assertNotIn(settled['id'], self.intel._abandoned_ids())

    def test_detail_write_also_fails_without_masking_start_error(self):
        original = RuntimeError('原始启动异常')
        real_db = self.intel.db
        calls = []
        def db():
            calls.append(1)
            if len(calls) > 2:
                raise sqlite3.OperationalError('记录也失败')
            return real_db()
        with failing_intel_start(original), \
                patch.object(self.intel, '_finish_job', side_effect=sqlite3.OperationalError('写回失败')), \
                patch.object(self.intel, 'db', side_effect=db), self.assertLogs('intel_store') as logs:
            with self.assertRaises(ValueError) as raised:
                self.intel.run({'fakeid': BIZ, 'mode': 'latest', 'sogou': False})
        self.assertIs(raised.exception.__cause__, original)
        self.assertIn('任务线程没有启动，已中断', str(raised.exception))
        self.assertIn('记录也失败', '\n'.join(logs.output))
        self.assertIsNone(self.intel.active_job())
        self.assertIn(self.intel.jobs(1)[0]['id'], self.intel._abandoned_ids())

    def test_second_run_while_finish_is_blocked(self):
        entered, release = threading.Event(), threading.Event()
        original_finish = self.intel._finish_job
        def held(*args, **kwargs):
            entered.set()
            if not release.wait(5):
                raise AssertionError('测试写回未放行')
            return original_finish(*args, **kwargs)
        errors = []
        def first():
            try:
                self.intel.run({'fakeid': BIZ, 'mode': 'latest', 'sogou': False})
            except ValueError as exc:
                errors.append(exc)
        runner = threading.Thread(target=first)
        try:
            with failing_intel_start(RuntimeError('启动失败')) as original_start, \
                    patch.object(self.intel, '_finish_job', held):
                runner.start()
                self.assertTrue(entered.wait(3))
                failed = self.intel.jobs(1)[0]['id']
                self.assertIsNone(self.intel.active_job())
                self.assertIsNone(self.intel.status()['active_job'])
                # 恢复真实 start，第二次受理必须创建新行。
                with patch('intel_store.threading.Thread.start', original_start):
                    new = self.intel.run({'fakeid': BIZ, 'mode': 'latest', 'sogou': False})
                self.assertNotEqual(failed, new['id'])
                self.assertFalse(new.get('reused', False))
                release.set()
                runner.join(5)
                self.assertFalse(runner.is_alive())
                self.assertNotIn(failed, self.intel._abandoned_ids())
        finally:
            release.set()
            runner.join(5)
        self.assertEqual(len(errors), 1)
        self.assertIn('任务线程没有启动，已中断', str(errors[0]))
        self.assertIsInstance(errors[0].__cause__, RuntimeError)

    def test_short_grace_only_abandoned_not_other_process_or_live(self):
        old = (datetime.now(timezone.utc) - timedelta(seconds=90)).isoformat()
        with self.intel.db() as connection:
            for job in ('abandoned', 'foreign', 'protected'):
                connection.execute("INSERT INTO jobs(id,fakeid,mode,status,started_at) VALUES(?,?,'latest','running',?)", (job, BIZ, old))
            with patch.object(intel_store, 'ABANDONED_JOB_SECONDS', 60):
                intel_store.interrupt_stale_jobs(connection, '清理', protected=('protected',), abandoned=('abandoned', 'protected'))
            statuses = dict(connection.execute('SELECT id,status FROM jobs'))
            reasons = dict(connection.execute('SELECT id,reason FROM jobs'))
        self.assertEqual(statuses, {'abandoned': 'interrupted', 'foreign': 'running', 'protected': 'running'})
        self.assertEqual(reasons['abandoned'], '清理')
        self.assertEqual(reasons['foreign'], '')
        self.assertEqual(reasons['protected'], '')

    def test_abandoned_live_registry_excluded_and_path_scoped(self):
        class Alive:
            def is_alive(self):
                return True
        key = (str(self.intel.path.resolve()), 'abandoned')
        other = ('/other/workspace/intel.db', 'foreign')
        try:
            with intel_store._PROCESS_WORKERS_LOCK:
                intel_store._PROCESS_ABANDONED.update((key, other))
                intel_store._PROCESS_WORKERS[key] = Alive()
                self.intel.workers['abandoned'] = Alive()
            self.assertNotIn('abandoned', self.intel._live_worker_ids())
            self.assertNotIn('foreign', self.intel._abandoned_ids())
        finally:
            with intel_store._PROCESS_WORKERS_LOCK:
                intel_store._PROCESS_ABANDONED.difference_update((key, other))
                intel_store._PROCESS_WORKERS.pop(key, None)
                self.intel.workers.pop('abandoned', None)

    def test_status_cleans_abandoned_after_short_grace(self):
        with failing_intel_start(RuntimeError('启动失败')), \
                patch.object(self.intel, '_finish_job', side_effect=sqlite3.OperationalError('库锁')), \
                self.assertLogs('intel_store'):
            with self.assertRaises(ValueError) as raised:
                self.intel.run({'fakeid': BIZ, 'mode': 'latest', 'sogou': False})
        self.assertIn('任务线程没有启动，已中断', str(raised.exception))
        failed = self.intel.jobs(1)[0]['id']
        self.assertIsNone(self.intel.active_job())
        self.assertEqual(self.intel.job(failed)['status'], 'running')
        self.assertIn(failed, self.intel._abandoned_ids())
        with self.intel.db() as connection:
            connection.execute('UPDATE jobs SET started_at=? WHERE id=?',
                               ((datetime.now(timezone.utc) - timedelta(seconds=61)).isoformat(), failed))
        self.assertIsNone(self.intel.status()['active_job'])
        settled = self.intel.job(failed)
        self.assertEqual(settled['status'], 'interrupted')
        self.assertIn('任务线程没有启动，已中断', settled['reason'])
        self.assertIn('启动失败', settled['reason'])
        self.assertNotEqual(settled['reason'], intel_store.LIVE_STALE_REASON)
        self.assertNotIn(failed, self.intel._abandoned_ids())
