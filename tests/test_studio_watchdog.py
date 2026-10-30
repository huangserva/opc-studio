"""H4 counterexamples use random loopback ports and temporary files only."""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import socket
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
import urllib.request
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scheduled_tasks import ScheduledTasks
from scripts import studio_watchdog as wd


@contextmanager
def fake_service(payload=None, status=200, delay=0, raw=None, drip=False):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.server.paths.append(self.path)
            if drip:
                try:
                    for byte in b'HTTP/1.1 200 OK\r\n':
                        self.wfile.write(bytes([byte])); self.wfile.flush(); time.sleep(.4)
                except OSError:
                    pass
                return
            time.sleep(delay)
            body = raw if raw is not None else json.dumps(payload).encode()
            try:
                self.send_response(status)
                self.send_header('Content-Length', str(len(body)))
                if status == 302:
                    self.send_header('Location', f'http://127.0.0.1:{self.server.server_port}/private')
                self.end_headers(); self.wfile.write(body)
            except OSError:
                pass
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    server.paths = []
    thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=.01), daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown(); server.server_close(); thread.join()


def healthy(**changes):
    return dict(ok=True, last_tick=datetime.now(timezone.utc).isoformat(), last_error=None,
                now=datetime.now(timezone.utc).isoformat(), tick_age_seconds=2, running_count=0, **changes)


class HealthHTTPTests(unittest.TestCase):
    def setUp(self):
        from server import Handler, Store, ThreadingHTTPServer
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.scheduler = ScheduledTasks(self.tmp.name, autostart=False)
        self.addCleanup(self.scheduler.close)
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.server.store = Store(self.tmp.name)
        self.server.schedules = self.scheduler
        self.addCleanup(self.server.server_close)
        self.thread = threading.Thread(target=lambda: self.server.serve_forever(poll_interval=.01), daemon=True)
        self.thread.start()
        self.addCleanup(self.thread.join)
        self.addCleanup(self.server.shutdown)
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def read_health(self):
        with self.opener.open(f'http://127.0.0.1:{self.server.server_port}/api/schedules/health', timeout=5) as response:
            self.assertEqual(response.status, 200)
            return json.load(response)

    def test_get_without_security_header_is_small_read_only_and_timezone_aware(self):
        self.scheduler.start()
        deadline = time.monotonic()+3
        while self.scheduler.last_tick is None and time.monotonic() < deadline:
            time.sleep(.01)
        before = (self.scheduler.db_path.stat().st_mtime_ns, hashlib.sha256(self.scheduler.db_path.read_bytes()).hexdigest())
        payload = self.read_health()
        self.assertEqual(set(payload), {'ok', 'last_tick', 'last_error', 'now', 'tick_age_seconds', 'running_count'})
        self.assertTrue(payload['ok'])
        self.assertIsNotNone(datetime.fromisoformat(payload['last_tick']).utcoffset())
        self.assertIsNotNone(datetime.fromisoformat(payload['now']).utcoffset())
        self.assertEqual(payload['running_count'], 0)
        self.assertEqual(before, (self.scheduler.db_path.stat().st_mtime_ns, hashlib.sha256(self.scheduler.db_path.read_bytes()).hexdigest()))

    def test_error_text_and_invalid_tick_never_leak_user_data(self):
        self.scheduler.last_error = 'SYNTHETIC_PRIVATE_SENTINEL /Users/example/private prompt'
        for tick in (None, 'bad SYNTHETIC_PRIVATE_SENTINEL', '2026-01-01T00:00:00'):
            self.scheduler.last_tick = tick
            payload = self.read_health()
            self.assertFalse(payload['ok'])
            self.assertIsNone(payload['last_tick'])
            self.assertIsNone(payload['tick_age_seconds'])
            self.assertNotIn('SYNTHETIC_PRIVATE_SENTINEL', json.dumps(payload))

    def test_stale_error_stopped_and_database_count_are_reported(self):
        self.scheduler.last_tick = (datetime.now(timezone.utc)-timedelta(seconds=400)).isoformat()
        self.assertGreater(self.read_health()['tick_age_seconds'], 399)
        self.assertFalse(self.read_health()['ok'])
        self.scheduler.last_tick = datetime.now(timezone.utc).isoformat()
        with self.scheduler.db() as db:
            db.execute("INSERT INTO runs(id,status) VALUES('fixture','running')")
        self.assertEqual(self.read_health()['running_count'], 1)
        self.scheduler.last_error = 'fixture'
        self.assertTrue(self.read_health()['last_error'])
        self.scheduler.last_error = None
        self.assertFalse(self.read_health()['ok'])  # no live loop despite a fresh timestamp

    def test_missing_database_is_not_recreated(self):
        self.scheduler.db_path.unlink()
        payload = self.read_health()
        self.assertFalse(payload['ok'])
        self.assertIsNone(payload['running_count'])
        self.assertFalse(self.scheduler.db_path.exists())


class WatchdogTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.logs = Path(self.tmp.name)/'logs'
        self.notifications = []

    def notify(self, message):
        self.notifications.append(message)
        return True

    def check(self, port, now=10000, **kwargs):
        return wd.check_once(self.logs, port, notifier=self.notify, clock=lambda: now, **kwargs)

    def test_closed_port_logs_and_notifies_within_one_run(self):
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
        started = time.monotonic()
        result = self.check(port)
        self.assertLess(time.monotonic()-started, 5)
        self.assertEqual(result['errors'], ['port_unavailable'])
        self.assertEqual(result['conclusion'], '异常')
        self.assertEqual(len(self.notifications), 1)
        self.assertEqual(json.loads((self.logs/'studio-watchdog.log').read_text()), result)

    def test_200_stale_tick_and_nonempty_error_are_independent_failures(self):
        for changes, expected in (({'tick_age_seconds': 400}, 'tick_stale'),
                                  ({'last_error': 'SYNTHETIC_PRIVATE_SENTINEL /Users/example'}, 'scheduler_error')):
            payload = healthy(); payload.update(changes)
            with self.subTest(expected=expected), fake_service(payload) as server:
                result = self.check(server.server_port)
            self.assertIn(expected, result['errors'])
            self.assertEqual(result['conclusion'], '异常')
        self.assertNotIn('SYNTHETIC_PRIVATE_SENTINEL', (self.logs/'studio-watchdog.log').read_text())
        self.assertNotIn('SYNTHETIC_PRIVATE_SENTINEL', (self.logs/'studio-watchdog.state.json').read_text())
        self.assertNotIn('SYNTHETIC_PRIVATE_SENTINEL', ''.join(self.notifications))

    def test_six_second_response_times_out_at_five_seconds(self):
        with fake_service(healthy(), delay=6) as server:
            started = time.monotonic()
            result = self.check(server.server_port)
            elapsed = time.monotonic()-started
        self.assertIn('http_timeout', result['errors'])
        self.assertGreater(elapsed, 4.8)
        self.assertLess(elapsed, 5.5)
        self.assertEqual(result['notification'], 'sent')

    def test_slow_drip_headers_obey_total_deadline(self):
        with fake_service(healthy(), drip=True) as server:
            started = time.monotonic()
            result = self.check(server.server_port, timeout=.3)
            self.assertLess(time.monotonic()-started, .8)
        self.assertEqual(result['errors'], ['http_timeout'])

    def test_non_json_http_status_and_redirect_do_not_crash_or_follow(self):
        for status, raw, expected in ((200, b'<html>not json</html>', 'invalid_json'),
                                      (503, b'private', 'http_status'),
                                      (201, b'{}', 'http_status'), (302, b'{}', 'http_status'),
                                      (200, b'x'*16385, 'invalid_json')):
            with self.subTest(status=status, expected=expected), fake_service(status=status, raw=raw) as server:
                result = self.check(server.server_port)
                self.assertEqual(server.paths, ['/api/schedules/health'])
            self.assertIn(expected, result['errors'])

    def test_malformed_fields_and_valid_boundary(self):
        for age in (None, '2', True, -1, float('nan'), float('inf')):
            payload = healthy(); payload['tick_age_seconds'] = age
            with self.subTest(age=age), fake_service(payload) as server:
                self.assertIn('invalid_health', self.check(server.server_port)['errors'])
        payload = healthy(); payload['tick_age_seconds'] = 300
        with fake_service(payload) as server:
            self.assertEqual(self.check(server.server_port)['conclusion'], 'ok')
        for payload in ([], {}, {**healthy(), 'ok': False}, {**healthy(), 'last_error': []}):
            with self.subTest(payload=payload), fake_service(payload) as server:
                self.assertEqual(self.check(server.server_port)['conclusion'], '异常')

    def assert_invalid_health(self, payload):
        with fake_service(payload) as server:
            result = self.check(server.server_port)
        self.assertIn('invalid_health', result['errors'])
        self.assertEqual(result['conclusion'], '异常')
        self.assertEqual(json.loads((self.logs/'studio-watchdog.log').read_text().splitlines()[-1]), result)

    def test_review_m1_nine_missing_or_mistyped_field_counterexamples(self):
        for field in ('last_tick', 'now', 'running_count'):
            payload = healthy(); del payload[field]
            with self.subTest(field=field, case='missing'):
                self.assert_invalid_health(payload)
            for value in ([], False):
                payload = healthy(); payload[field] = value
                with self.subTest(field=field, value=value):
                    self.assert_invalid_health(payload)

    def test_all_six_fields_are_required(self):
        for field in healthy():
            payload = healthy(); del payload[field]
            with self.subTest(field=field):
                self.assert_invalid_health(payload)

    def test_timestamps_require_valid_timezone_aware_iso_strings(self):
        for field in ('last_tick', 'now'):
            for value in (None, '', '2026-10-30T12:00:00', '2026-10-30',
                          '2026-02-30T12:00:00+00:00', 'SYNTHETIC_PRIVATE_SENTINEL'):
                payload = healthy(); payload[field] = value
                with self.subTest(field=field, value=value):
                    self.assert_invalid_health(payload)
        self.assertNotIn('SYNTHETIC_PRIVATE_SENTINEL', (self.logs/'studio-watchdog.log').read_text())
        self.assertNotIn('SYNTHETIC_PRIVATE_SENTINEL', (self.logs/'studio-watchdog.state.json').read_text())
        self.assertNotIn('SYNTHETIC_PRIVATE_SENTINEL', ''.join(self.notifications))

    def test_running_count_requires_nonnegative_integer_excluding_bool(self):
        for value in (None, -1, 1.5, '0', True):
            payload = healthy(); payload['running_count'] = value
            with self.subTest(value=value):
                self.assert_invalid_health(payload)

    def test_ok_and_error_types_and_non_object_health_are_invalid(self):
        for field, values in (('ok', (None, 0, 1, 'true', [])),
                              ('last_error', (False, 0, [], {}))):
            for value in values:
                payload = healthy(); payload[field] = value
                with self.subTest(field=field, value=value):
                    self.assert_invalid_health(payload)
        for payload in ([], None, 0, 'healthy'):
            with self.subTest(payload=payload):
                self.assert_invalid_health(payload)

    def test_complete_health_accepts_iso_offsets_and_numeric_boundaries(self):
        for age, count, error in ((0, 0, None), (0.5, 1, ''), (300, 2, None)):
            payload = healthy()
            payload.update(last_tick='2026-10-30T12:00:00+08:00', now='2026-10-30T04:00:00Z',
                           tick_age_seconds=age, running_count=count, last_error=error)
            with self.subTest(age=age, count=count), fake_service(payload) as server:
                result = self.check(server.server_port)
            self.assertEqual(result['errors'], [])
            self.assertEqual(result['conclusion'], 'ok')

    def test_hourly_dedup_different_errors_and_one_recovery(self):
        payload = healthy(); payload['tick_age_seconds'] = 400
        with fake_service(payload) as server:
            self.assertEqual(self.check(server.server_port)['notification'], 'sent')
            self.assertEqual(self.check(server.server_port, 13599)['notification'], 'suppressed')
            self.assertEqual(self.check(server.server_port, 13600)['notification'], 'sent')
        payload = healthy(); payload['last_error'] = 'fixture'
        with fake_service(payload) as server:
            self.assertEqual(self.check(server.server_port, 13601)['notification'], 'sent')
        with fake_service(healthy()) as server:
            self.assertEqual(self.check(server.server_port, 13602)['notification'], 'recovery_sent')
            self.assertEqual(self.check(server.server_port, 13603)['notification'], 'none')
        self.assertEqual(len(self.notifications), 4)

    def test_dedup_survives_recovery_and_restart(self):
        payload = healthy(); payload['tick_age_seconds'] = 400
        with fake_service(payload) as bad, fake_service(healthy()) as good:
            self.check(bad.server_port)
            self.check(good.server_port, 10001)
            self.assertEqual(self.check(bad.server_port, 10002)['notification'], 'suppressed')
            self.assertEqual(self.check(good.server_port, 10003)['notification'], 'recovery_sent')

    def test_notification_failure_retries_and_atomic_state_handles_corruption(self):
        self.logs.mkdir()
        (self.logs/'studio-watchdog.state.json').write_text('broken')
        payload = healthy(); payload['last_error'] = 'fixture'
        with fake_service(payload) as bad, fake_service(healthy()) as good:
            first = wd.check_once(self.logs, bad.server_port, notifier=lambda _: False)
            self.assertEqual(first['notification'], 'failed')
            self.assertEqual(self.check(bad.server_port)['notification'], 'sent')
            wd.check_once(self.logs, good.server_port, notifier=lambda _: False)
            self.assertEqual(self.check(good.server_port)['notification'], 'recovery_sent')

    def test_rotation_preserves_one_backup_and_only_own_files_change(self):
        protected = [Path(self.tmp.name)/p for p in ('tasks.db', 'agent.plist', 'grok-bot-reports/report.json')]
        for path in protected:
            path.parent.mkdir(exist_ok=True, parents=True); path.write_text('protected-fixture')
        before = {str(p): (p.stat().st_mtime_ns, p.read_bytes()) for p in protected}
        self.logs.mkdir()
        (self.logs/'studio-watchdog.log').write_text('x'*wd.MAX_LOG_BYTES)
        (self.logs/'studio-watchdog.log.1').write_text('old backup')
        with fake_service(healthy()) as server:
            self.check(server.server_port)
        self.assertEqual((self.logs/'studio-watchdog.log.1').stat().st_size, wd.MAX_LOG_BYTES)
        self.assertLess((self.logs/'studio-watchdog.log').stat().st_size, wd.MAX_LOG_BYTES)
        self.assertEqual(before, {str(p): (p.stat().st_mtime_ns, p.read_bytes()) for p in protected})
        self.assertEqual({p.name for p in self.logs.iterdir()}, {'studio-watchdog.log', 'studio-watchdog.log.1', 'studio-watchdog.state.json', 'studio-watchdog.lock'})

    def test_short_interval_detects_failure_within_five_minutes(self):
        payload = healthy()
        with fake_service(payload) as server:
            self.check(server.server_port)
            fault_at = time.monotonic()
            payload['tick_age_seconds'] = 400
            time.sleep(.05)  # test schedule interval; production interval is 900s
            result = self.check(server.server_port, 10001)
        self.assertLess(time.monotonic()-fault_at, 300)
        self.assertEqual(result['conclusion'], '异常')

    def test_proxy_environment_cannot_redirect_local_probe(self):
        with fake_service(healthy()) as server, patch.dict('os.environ', {'HTTP_PROXY': 'http://127.0.0.1:1', 'NO_PROXY': ''}):
            self.assertEqual(self.check(server.server_port)['conclusion'], 'ok')

    def test_root_fields_take_precedence_over_nested_decoys(self):
        for root_age, nested_age, expected in ((2, 400, 'ok'), (400, 2, '异常')):
            payload = {'history': {'tick_age_seconds': nested_age, 'last_error': 'SYNTHETIC_PRIVATE_SENTINEL'},
                       **healthy()}
            payload['tick_age_seconds'] = root_age
            with self.subTest(root_age=root_age), fake_service(payload) as server:
                result = self.check(server.server_port)
            self.assertEqual(result['conclusion'], expected)
            self.assertEqual(result['tick_age_seconds'], root_age)
        self.assertNotIn('SYNTHETIC_PRIVATE_SENTINEL', (self.logs/'studio-watchdog.log').read_text())

    def test_unexpected_parser_failure_fails_closed_without_exception_text(self):
        with patch.object(wd, '_probe', side_effect=RuntimeError('SYNTHETIC_PRIVATE_SENTINEL')):
            result = self.check(1)
        self.assertEqual(result['errors'], ['http_unavailable'])
        self.assertNotIn('SYNTHETIC_PRIVATE_SENTINEL', (self.logs/'studio-watchdog.log').read_text())

    def test_concurrent_invocation_does_not_duplicate_notifications(self):
        self.logs.mkdir()
        with (self.logs/'studio-watchdog.lock').open('a') as lock:
            wd.fcntl.flock(lock, wd.fcntl.LOCK_EX | wd.fcntl.LOCK_NB)
            self.assertIsNone(self.check(1))
        self.assertEqual(self.notifications, [])
        self.assertFalse((self.logs/'studio-watchdog.log').exists())

    def test_notification_uses_fixed_script_and_no_shell(self):
        with patch.object(wd.subprocess, 'run', return_value=SimpleNamespace(returncode=0)) as run:
            self.assertTrue(wd.notify('fixture " ; malicious'))
        argv = run.call_args.args[0]
        self.assertEqual(argv[0], '/usr/bin/osascript')
        self.assertEqual(argv[-1], 'fixture " ; malicious')
        self.assertNotIn('malicious', argv[2])
        self.assertNotIn('shell', run.call_args.kwargs)


if __name__ == '__main__':
    unittest.main()
