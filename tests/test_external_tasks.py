"""H1：可注入时钟、/tmp 文件、固定 launchctl 夹具；不触碰真实报告/调度库。"""
from datetime import datetime
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit

from external_tasks import ExternalTasks, TASKS, SERVICES, occurrences, service_snapshot
from scheduled_tasks import ScheduledTasks
from schedules_assistant_api import get_route


def timestamp(s):
    return datetime.fromisoformat(s + '+08:00').timestamp()


class ExternalHeartbeatTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='h1-unit-', dir='/tmp')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.now = timestamp('2026-10-30T10:30:00')
        self.services = {SERVICES[0]: dict(pid=321, exit_code=0), SERVICES[1]: dict(pid=None, exit_code=7)}
        self.reader = lambda: self.services
        self.cache = ExternalTasks(self.root / 'workspace', self.root / 'reports', clock=lambda: self.now, service_reader=self.reader)

    def put(self, relative, at, content=b''):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        value = timestamp(at)
        os.utime(path, (value, value))
        return path

    def row(self, identity='grok-interest'):
        self.cache.refresh(force=True)
        return next(t for t in self.cache.snapshot()['tasks'] if t['id'] == identity)

    def test_waiting_overdue_missing_then_late_empty_output_recovers(self):
        self.put('reports/interest/2026-10-29-2230.json','2026-10-29T22:40:00')
        self.assertEqual(self.row()['status'], 'waiting')
        self.now = timestamp('2026-10-30T11:30:00')
        self.assertEqual(self.row()['status'], 'waiting')
        self.now += 1
        self.assertEqual(self.row()['status'], 'overdue')
        self.now = timestamp('2026-10-30T22:30:00')
        self.assertEqual(self.row()['status'], 'missing')
        self.put('reports/interest/2026-10-30-2231.json','2026-10-30T22:31:00')
        self.now += 60
        self.assertEqual(self.row()['status'], 'ok')  # 0 字节空产出也算心跳。

    def test_late_output_in_same_cycle_recovers(self):
        self.put('reports/interest/2026-10-30-1200.json','2026-10-30T12:00:00',b'{"posts":[]}')
        self.now = timestamp('2026-10-30T12:01:00')
        self.assertEqual(self.row()['status'], 'ok')

    def test_midnight_uses_previous_evening_slot(self):
        self.now = timestamp('2026-10-31T00:30:00')
        self.put('reports/interest/2026-10-30-2240.json','2026-10-30T22:40:00')
        row = self.row()
        self.assertEqual(row['status'], 'ok')
        self.assertEqual(row['expected_at'], '2026-10-30T14:30:00+00:00')
        self.assertEqual(row['next_expected_at'], '2026-10-31T02:30:00+00:00')

    def test_late_after_midnight_with_previous_date_is_accepted(self):
        self.now = timestamp('2026-10-31T00:30:00')
        self.put('reports/interest/2026-10-30-2359.json','2026-10-31T00:10:00')
        self.assertEqual(self.row()['status'], 'ok')

    def test_weekly_cross_week_missing_and_new_arbitrary_file(self):
        self.put('reports/weekly/week-43.json','2026-10-25T20:30:00')
        self.now = timestamp('2026-11-01T20:00:00')
        self.assertEqual(self.row('grok-weekly')['status'], 'waiting')
        self.now = timestamp('2026-11-01T22:00:01')
        self.assertEqual(self.row('grok-weekly')['status'], 'overdue')
        self.now = timestamp('2026-11-08T20:00:00')
        self.assertEqual(self.row('grok-weekly')['status'], 'missing')
        self.put('reports/weekly/any-new-report','2026-11-08T20:01:00')
        self.now += 60
        self.assertEqual(self.row('grok-weekly')['status'], 'ok')

    def test_daily_exact_date_filename_only(self):
        self.now = timestamp('2026-10-30T09:30:00')
        for name in ('fixture.json','2026-10-30-extra.json','bad-2026-10-30.json','2026-02-30.json','2026-10-29.json'):
            self.put('reports/daily/'+name,'2026-10-30T09:10:00')
        self.assertEqual(self.row('grok-daily')['status'], 'missing')
        self.put('reports/daily/2026-10-30.json','2026-10-30T09:20:00')
        self.assertEqual(self.row('grok-daily')['status'], 'ok')

    def test_alert_pattern_date_and_mtime_not_first_number(self):
        self.now = timestamp('2026-10-30T16:30:00')
        for name in ('123-2026-10-30-1630.json','2026-10-30.json','2026-10-30-.json','2026-13-30-x.json','2026-10-30-x.json.tmp'):
            self.put('reports/alerts/'+name,'2026-10-30T16:05:00')
        self.assertEqual(self.row('grok-alerts')['status'], 'missing')
        self.put('reports/alerts/2026-10-30-999-1630.json','2026-10-30T16:20:00')
        self.assertEqual(self.row('grok-alerts')['status'], 'ok')

    def test_missing_folder_does_not_abort_other_rows(self):
        row = self.row()
        self.assertEqual(row['status'], 'missing')
        self.assertIn('不存在',row['detail'])
        self.assertIn('从未产出',row['detail'])
        self.assertEqual(len(self.cache.snapshot()['tasks']),10)

    def test_permission_failure_is_redacted_and_not_success(self):
        with patch('external_tasks.os.scandir', side_effect=PermissionError('synthetic-private-sentinel')):
            row = self.row()
        self.assertEqual(row['status'],'missing')
        self.assertIn('不可读',row['detail'])
        self.assertNotIn('synthetic-private-sentinel',json.dumps(self.cache.snapshot()))

    def test_symlink_directory_and_file_ignored(self):
        file = self.put('outside/2026-10-30-1031.json','2026-10-30T10:31:00')
        self.now += 120
        (self.root/'reports').mkdir()
        (self.root/'reports/interest').symlink_to(file.parent, target_is_directory=True)
        self.assertNotEqual(self.row()['status'],'ok')
        (self.root/'reports/interest').unlink();(self.root/'reports/interest').mkdir()
        (self.root/'reports/interest'/file.name).symlink_to(file)
        self.assertNotEqual(self.row()['status'],'ok')

    def test_future_mtime_and_output_before_plan_do_not_count(self):
        self.put('reports/interest/2026-10-30-future.json','2026-10-30T11:00:00')
        self.put('reports/interest/2026-10-30-early.json','2026-10-30T10:00:00')
        self.assertNotEqual(self.row()['status'],'ok')

    def test_digest_unknown_never_guesses_path(self):
        row = self.row('grok-digest')
        self.assertEqual((row['status'],row['rule'],row['grace_minutes']),('unconfirmed','待确认',None))

    def test_radar_nested_snapshot_with_30_minute_grace(self):
        self.now = timestamp('2026-10-30T10:00:00')
        self.put('workspace/twitter-radar/snapshots/2026-10-30/prior.json','2026-10-30T08:01:00')
        self.assertEqual(self.row('twitter-radar')['status'],'waiting')
        self.now = timestamp('2026-10-30T10:30:01')
        self.assertEqual(self.row('twitter-radar')['status'],'overdue')
        self.put('workspace/twitter-radar/snapshots/2026-10-30/new.json','2026-10-30T10:30:01')
        self.assertEqual(self.row('twitter-radar')['status'],'ok')

    def test_watchdog_completed_line_and_restart_are_stateless(self):
        self.now = timestamp('2026-10-30T10:16:00')
        self.put('workspace/logs/studio-watchdog.log','2026-10-30T10:16:00',b'first\n')
        first=self.row('studio-watchdog')
        self.assertEqual(first['status'],'ok')
        self.now=timestamp('2026-10-30T10:31:00')
        row=self.row('studio-watchdog')
        fresh=ExternalTasks(self.root/'workspace',self.root/'reports',clock=lambda:self.now,service_reader=self.reader)
        fresh.refresh(force=True)
        restarted=next(t for t in fresh.snapshot()['tasks'] if t['id']=='studio-watchdog')
        self.assertEqual(row,restarted)
        self.assertEqual(row['last_output_at'],first['last_output_at'])
        self.assertEqual(row['status'],'waiting')

    def test_watchdog_empty_partial_and_rotation(self):
        self.now=timestamp('2026-10-30T10:16:00')
        path=self.put('workspace/logs/studio-watchdog.log','2026-10-30T10:16:00')
        self.assertNotEqual(self.row('studio-watchdog')['status'],'ok')
        path.write_bytes(b'partial');os.utime(path,(self.now,self.now))
        self.assertNotEqual(self.row('studio-watchdog')['status'],'ok')
        path.unlink();self.put('workspace/logs/studio-watchdog.log','2026-10-30T10:16:00',b'new\n')
        self.assertEqual(self.row('studio-watchdog')['status'],'ok')

    def test_services_running_exited_and_unavailable(self):
        self.assertEqual(self.row(SERVICES[0])['status'],'running')
        row=self.row(SERVICES[1]);self.assertEqual((row['status'],row['exit_code']),('exited',7))
        self.services=None
        self.assertEqual(self.row(SERVICES[0])['status'],'unknown')

    def test_cache_throttle_copy_and_cached_api_never_scan(self):
        self.row()
        checked=self.cache.snapshot()['checked_at']
        with patch.object(self.cache,'_latest',side_effect=AssertionError('HTTP 不得扫描')), patch('external_tasks.subprocess.run',side_effect=AssertionError('HTTP 不得调用 launchctl')):
            self.now+=299;self.cache.refresh()
            self.assertEqual(self.cache.snapshot()['checked_at'],checked)
            scheduler=ScheduledTasks(self.root/'workspace',autostart=False)
            self.addCleanup(scheduler.close);scheduler.external_tasks=self.cache
            handler=SimpleNamespace(server=SimpleNamespace(store=None,schedules=scheduler),reply=lambda code,body:body)
            start=time.monotonic();body=get_route(handler,urlsplit('/api/schedules'))
            self.assertLess(time.monotonic()-start,1)
            self.assertEqual(len(body['external']['tasks']),10)
            body['external']['tasks'].clear()
            self.assertEqual(len(self.cache.snapshot()['tasks']),10)
        with patch.object(self.cache,'_latest',wraps=self.cache._latest) as scan:
            self.now+=1;self.cache.refresh();self.assertEqual(scan.call_count,7)

    def test_scheduler_loop_drives_cache_without_creating_tasks_or_runs(self):
        scheduler=ScheduledTasks(self.root/'workspace',autostart=False)
        self.addCleanup(scheduler.close);scheduler.external_tasks=self.cache
        with patch.object(scheduler.stop_event,'wait',side_effect=lambda _:scheduler.stop_event.set()):
            scheduler.loop()
        self.assertIsNotNone(self.cache.snapshot()['checked_at'])
        self.assertEqual(scheduler.listing()['tasks'],[])
        self.assertEqual(scheduler.runs()['runs'],[])

    def test_list_services_fixed_command_no_plist_or_full_output(self):
        output='PID Status Label\n123 0 '+SERVICES[0]+'\n- 9 '+SERVICES[1]+'\n999 0 synthetic-private-sentinel\n'
        with patch('external_tasks.subprocess.run',return_value=SimpleNamespace(stdout=output)) as run:
            rows=service_snapshot()
        self.assertEqual(run.call_args.args,(['/bin/launchctl','list'],))
        self.assertEqual(set(rows),set(SERVICES))
        self.assertEqual(rows[SERVICES[1]],dict(pid=None,exit_code=9))
        with patch('external_tasks.subprocess.run',side_effect=subprocess.TimeoutExpired('list',1)):
            self.assertIsNone(service_snapshot())

    def test_default_reports_location_reuses_grok_resolver(self):
        self.put('reports/interest/2026-10-30-1031.json','2026-10-30T10:31:00')
        self.now += 120
        fresh=ExternalTasks(self.root/'workspace',clock=lambda:self.now,service_reader=self.reader)
        with patch('grok_bot_radar.reports_dir',return_value=self.root/'reports') as location:
            fresh.refresh(force=True)
        self.assertEqual(location.call_count,4)
        row=next(t for t in fresh.snapshot()['tasks'] if t['id']=='grok-interest')
        self.assertEqual(row['status'],'ok')

    def test_registry_schedule_boundaries(self):
        task=next(t for t in TASKS if t['id']=='grok-weekly')
        previous,due,future=occurrences(task,timestamp('2026-11-02T00:30:00'))
        self.assertEqual(due,timestamp('2026-11-01T20:00:00'))
        self.assertEqual(future,timestamp('2026-11-08T20:00:00'))
        self.assertEqual(previous,timestamp('2026-10-25T20:00:00'))


if __name__=='__main__':
    unittest.main()
