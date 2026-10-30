"""H1-L1：真实循环、临时数据库与报告目录；故障不污染调度健康。"""
from datetime import datetime
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from types import SimpleNamespace
from urllib.parse import urlsplit

from external_tasks import ExternalTasks
from scheduled_tasks import ScheduledTasks
from schedules_assistant_api import get_route


class ExternalRefreshIsolationTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='h1-l1-', dir='/tmp')
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.scheduler = ScheduledTasks(self.root / 'workspace', autostart=False)
        self.addCleanup(self.scheduler.close)
        self.cache = ExternalTasks(self.root / 'workspace', self.root / 'reports', service_reader=lambda: {})
        self.scheduler.external_tasks = self.cache
        self.cache.refresh()

    def run_cycles(self, count):
        states = []
        handler = SimpleNamespace(server=SimpleNamespace(store=None, schedules=self.scheduler), reply=lambda code, body: body)
        def wait(_):
            states.append((self.scheduler.health(), get_route(handler, urlsplit('/api/schedules'))))
            if len(states) == count:
                self.scheduler.stop_event.set()
        with patch.object(self.scheduler.stop_event, 'wait', side_effect=wait), patch.object(self.scheduler, 'thread', threading.current_thread()):
            self.scheduler.loop()
        return states

    def test_repeated_refresh_failure_preserves_snapshot_and_health_then_recovers(self):
        before = self.cache.snapshot()
        original = self.cache.refresh
        calls = []
        def refresh():
            calls.append(True)
            if len(calls) <= 2:
                raise RuntimeError('synthetic-private-sentinel')
            original()
        with patch.object(self.cache, 'refresh', side_effect=refresh), patch.object(self.scheduler, 'tick', wraps=self.scheduler.tick) as tick:
            states = self.run_cycles(3)
        self.assertEqual(tick.call_count, 3)
        for health, listing in states[:2]:
            self.assertTrue(health['ok'])
            self.assertIsNone(health['last_error'])
            self.assertIsNone(listing['last_error'])
            self.assertIsNone(listing['scheduler_health']['last_error'])
            external = listing['external']
            self.assertEqual(external['tasks'], before['tasks'])
            self.assertEqual(external['checked_at'], before['checked_at'])
            self.assertEqual(external['last_error'], '外部心跳刷新失败')
            self.assertIsNotNone(datetime.fromisoformat(external['last_error_at']).utcoffset())
            self.assertNotIn('synthetic-private-sentinel', str(listing))
        health, listing = states[-1]
        self.assertTrue(health['ok'])
        self.assertIsNone(listing['external']['last_error'])
        self.assertIsNone(listing['external']['last_error_at'])
        self.assertEqual(listing['tasks'], [])
        self.assertEqual(self.scheduler.runs()['runs'], [])

    def test_partial_scan_exception_preserves_entire_previous_snapshot(self):
        before = self.cache.snapshot()
        # 先改了局部 rows，再在读取服务时失败：不得发布半份结果。
        with patch.object(self.cache, '_latest', return_value=(self.cache.clock(), None)), patch.object(self.cache, 'service_reader', side_effect=RuntimeError('synthetic-private-sentinel')):
            with patch.object(self.cache, 'refresh', wraps=lambda: ExternalTasks.refresh(self.cache, force=True)):
                states = self.run_cycles(2)
        for health, listing in states:
            self.assertTrue(health['ok'])
            self.assertEqual(listing['external']['tasks'], before['tasks'])
            self.assertEqual(listing['external']['checked_at'], before['checked_at'])
            self.assertEqual(listing['external']['last_error'], '外部心跳刷新失败')
        self.cache.refresh()
        self.assertIsNone(self.cache.snapshot()['last_error'])

    def test_tick_exception_keeps_existing_scheduler_error_behavior(self):
        with patch.object(self.scheduler, 'tick', side_effect=RuntimeError('synthetic-private-sentinel')), patch.object(self.cache, 'refresh') as refresh:
            states = self.run_cycles(2)
        self.assertEqual(refresh.call_count, 0)
        for health, listing in states:
            self.assertFalse(health['ok'])
            self.assertEqual(listing['last_error'], '调度检查失败，请检查工作台日志与任务数据库；任务没有被标记成功')
            self.assertEqual(health['last_error'], '调度检查失败，请检查工作台日志')
            self.assertIsNone(listing['external'].get('last_error'))
            self.assertIsNone(listing['last_tick'])


if __name__ == '__main__':
    unittest.main()
