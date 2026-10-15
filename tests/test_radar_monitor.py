"""B1 contract counterexamples: all network-independent, all mutable data in /tmp."""
import copy
from datetime import datetime, timedelta, timezone
import importlib.util
import json
import multiprocessing
import os
from pathlib import Path
import plistlib
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.radar_monitor import Monitor, atomic_write, collect_snapshot, persist_clue, read_snapshot

ZONE = ZoneInfo('Asia/Shanghai')


def when(hour, minute=0, day=15):
    return datetime(2026, 10, day, hour, minute, tzinfo=ZONE).astimezone(timezone.utc)


def tweet(tid='1', hour=7, *, age=1, views=4000, likes=10, followers=20000, **extra):
    return {'tweet_id': tid, 'author': 'test_user', 'user_id': '11',
            'followers': followers, 'published_at': (when(hour) - timedelta(hours=age)).isoformat(),
            'fetched_at': when(hour).isoformat(), 'type': 'original', 'is_stale': False,
            'text': 'Agent 开源模型测试', 'url': f'https://x.com/test_user/status/{tid}',
            'views': views, 'likes': likes, 'retweets': 0, 'replies': 0, **extra}


def snapshot(hour, tweets=(), coverage=1.0, calls=51, **extra):
    return {'run_id': f'run-{hour}', 'started_at': when(hour).isoformat(),
            'ended_at': when(hour).isoformat(), 'tweets': list(tweets),
            'coverage': coverage, 'api_calls': calls, 'failed_handles': [], **extra}


def holder(root, entered, release):
    def collector(*args):
        entered.set()
        if not release.wait(10):
            raise RuntimeError('父进程未放行')
        return snapshot(7)
    Monitor(root, collector=collector).run_once(when(7))


class MonitorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='b1-case-', dir='/tmp')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / 'scripts').mkdir()
        for filename in ('radar_monitor_config.json', 'radar_common.py'):
            shutil.copy2(ROOT / 'scripts' / filename, self.root / 'scripts' / filename)
        self.result = snapshot(6)
        self.collect_calls = 0
        self.deliveries = []
        def collect(*args):
            self.collect_calls += 1
            if isinstance(self.result, Exception):
                raise self.result
            return copy.deepcopy(self.result)
        def send(events):
            self.deliveries.extend(copy.deepcopy(events))
            return [{'event_id': event['event_id'], 'status': 'sent'} for event in events]
        self.sender = send
        self.monitor = Monitor(self.root, collector=collect, sender=send)
        self.monitor.run_once(when(6))

    def run_round(self, hour, tweets=(), *, coverage=1, calls=51, **extra):
        self.result = snapshot(hour, tweets, coverage, calls, **extra)
        return self.monitor.run_once(when(hour))

    def state(self):
        return json.loads((self.monitor.directory / 'state.json').read_text())

    def events(self, kind=None):
        result = self.state()['events']
        return [e for e in result if kind is None or e['type'] == kind]

    def test_01_six_am_only_baseline(self):
        self.run_round(6, [tweet(hour=6, views=60000, likes=250, followers=100)])
        self.assertEqual(self.events(), [])
        self.assertEqual(self.deliveries, [])
        self.assertFalse((self.root / 'workspace/clues/clues.db').exists())
        # A fresh installation with hot tweets at 06:00 also never emits anything.
        (self.monitor.directory / 'state.json').unlink()
        self.run_round(6, [tweet(hour=6, views=60000, likes=250, followers=100)])
        self.assertIn('1', self.state()['samples'])
        self.assertEqual(self.events(), [])

    def test_02_three_am_skips_collector(self):
        count = self.collect_calls
        self.assertEqual(self.monitor.run_once(when(3))['status'], 'outside_hours')
        self.assertEqual(self.collect_calls, count)
        self.assertIn('非运行时段', (self.monitor.directory / 'monitor.log').read_text())

    def test_03_failed_round_preserves_actual_two_hour_interval(self):
        (self.monitor.directory / 'state.json').unlink()
        self.run_round(6, [tweet(hour=6, age=5, views=1000)])
        self.result = RuntimeError('离线采集失败')
        self.assertEqual(self.monitor.run_once(when(7))['status'], 'failed')
        self.run_round(8, [tweet(hour=8, age=7, views=11000)])
        events = self.events('hourly_delta')
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]['tweet']['hourly_delta'], 5000)

    def test_04_stale_previous_is_not_delta_or_new(self):
        self.run_round(7, [tweet(hour=7, views=100, is_stale=True)])
        self.run_round(8, [tweet(hour=8, age=2, views=30000)])
        self.assertEqual(self.events(), [])
        self.assertEqual(self.state()['shadows'], [])

    def test_05_declining_views_never_trigger_even_lowfan(self):
        self.run_round(7, [tweet(hour=7, age=3, views=90000, is_stale=True)])
        self.run_round(8, [tweet(hour=8, age=4, views=60000, likes=250, followers=100)])
        self.assertEqual(self.events(), [])

    def test_06_three_rounds_one_initial_and_one_upgrade(self):
        for hour, views in [(7, 8000), (8, 15000), (9, 23000), (10, 100000), (11, 120000)]:
            self.run_round(hour, [tweet(hour=hour, age=hour-6, views=views)])
        self.assertEqual([e['type'] for e in self.events()], ['new_fast', 'upgrade_100k'])
        self.assertEqual(len(self.deliveries), 2)
        self.assertTrue(self.state()['pushed']['1']['upgraded'])

    def test_07_lowfan_inclusive_boundaries(self):
        for followers, age, expected in [(10000, 6, True), (10001, 6, False),
                                         (None, 6, False), (10000, 6.01, False)]:
            with self.subTest(followers=followers, age=age):
                tid = str(100 + len(self.state()['samples']))
                # Pre-seen stale sample prevents new_fast from hiding the lowfan boundary.
                current = self.state()
                current['samples'][tid] = tweet(tid, hour=6, views=50000, is_stale=True)
                self.monitor.commit(current)
                # Independent slots, since one collection per hour is intentional.
                hour = 7 + len(self.state()['runs']) - 1
                self.run_round(hour, [tweet(tid, hour=hour, age=age, views=50000, likes=200, followers=followers)])
                self.assertEqual(any(e['tweet']['tweet_id'] == tid for e in self.events('lowfan_burst')), expected)

    def test_08_new_age_absolute_views_and_rate_boundaries(self):
        cases = [(0, 4000, False), (.249, 4000, False), (.25, 2500, True),
                 (.5, 2400, False), (1, 3499, False), (1, 3500, True)]
        for index, (age, views, expected) in enumerate(cases):
            tid, hour = str(index+10), 7+index
            self.run_round(hour, [tweet(tid, hour=hour, age=age, views=views)])
            with self.subTest(age=age, views=views):
                self.assertEqual(any(e['tweet']['tweet_id'] == tid for e in self.events('new_fast')), expected)

    def test_09_retweet_reply_and_non_ai_filtered(self):
        for index, extra in enumerate([{'type': 'retweet'}, {'type': 'reply'},
                                       {'text': 'weekend walking and tea'}, {'text': 'RT @x: Agent 模型', 'type': 'retweet'}]):
            self.run_round(7+index, [tweet(str(index+1), hour=7+index, views=60000, likes=250, followers=100, **extra)])
        self.assertEqual(self.events(), [])

    def test_10_two_processes_lock_and_no_state_interleaving(self):
        ctx = multiprocessing.get_context('fork')
        entered, release = ctx.Event(), ctx.Event()
        process = ctx.Process(target=holder, args=(str(self.root), entered, release))
        process.start()
        try:
            self.assertTrue(entered.wait(5))
            before = (self.monitor.directory / 'state.json').read_bytes()
            self.assertEqual(self.monitor.run_once(when(7)), {'status': 'locked'})
            self.assertEqual(before, (self.monitor.directory / 'state.json').read_bytes())
        finally:
            release.set()
            process.join(10)
            if process.is_alive():
                process.kill()
                process.join()
        self.assertEqual(process.exitcode, 0)
        self.assertEqual(self.state()['days']['2026-10-15']['rounds'], 2)

    def test_11_corrupt_state_rebuilds_without_realert(self):
        self.run_round(7, [tweet(views=5000)])
        (self.monitor.directory / 'state.json').write_text('{broken')
        self.run_round(8, [tweet(hour=8, age=1, views=60000, likes=250, followers=100), tweet('2', hour=8, views=9000)])
        self.assertEqual(len(self.events()), 1)
        self.assertEqual(len(self.deliveries), 1)
        self.assertIn('2', self.state()['samples'])
        self.assertIn('1', self.state()['pushed'])
        self.run_round(9, [tweet(hour=9, age=2, views=70000, likes=250, followers=100)])
        self.assertEqual(len(self.deliveries), 1)

    def test_12_absent_sender_exception_retry_and_remote_idempotency(self):
        self.monitor.sender = None
        self.run_round(7, [tweet(views=5000)])
        initial_id = self.events()[0]['event_id']
        self.assertEqual(self.events()[0]['status'], 'pending')
        self.assertEqual(self.state()['pushed'], {})
        self.monitor.sender = lambda events: (_ for _ in ()).throw(RuntimeError('模拟发送失败'))
        self.run_round(8, [tweet(hour=8, views=10000)])
        self.assertEqual(self.events()[0]['event_id'], initial_id)
        self.assertEqual(self.events()[0]['status'], 'pending')
        self.assertEqual(self.state()['pushed'], {})
        remote_seen, attempts = set(), []
        def sender(events):
            for event in events:
                attempts.append(event['event_id'])
                remote_seen.add(event['event_id'])  # B2 durable dedup equivalent.
            return [{'event_id': e['event_id'], 'status': 'sent'} for e in events]
        self.monitor.sender = sender
        # Crash window: remote accepted, state commit fails before touching disk.
        real_commit = self.monitor.commit
        def crash_after_send(state):
            if any(e['status'] == 'sent' for e in state['events']):
                raise OSError('模拟状态落盘失败')
            return real_commit(state)
        with patch.object(self.monitor, 'commit', side_effect=crash_after_send):
            self.run_round(9)
        self.assertEqual(self.events()[0]['status'], 'pending')
        self.assertEqual(self.state()['pushed'], {})
        self.run_round(10)
        self.assertEqual(attempts, [initial_id, initial_id])
        self.assertEqual(len(remote_seen), 1)
        self.assertEqual(self.events()[0]['status'], 'sent')
        self.assertEqual(self.state()['pushed']['1']['type'], 'new_fast')
        self.run_round(11)
        self.assertEqual(len(attempts), 2)

    def test_13_real_clues_store_tweet_id_idempotency(self):
        self.monitor.sender = None
        self.run_round(7, [tweet(views=5000)])
        event = self.events()[0]
        first = persist_clue(self.root, event)
        other = copy.deepcopy(event)
        other.update(type='upgrade_100k', event_id='different-stage')
        second = persist_clue(self.root, other)
        self.assertEqual(first['id'], second['id'])
        self.assertTrue(second['duplicate'])
        from clues_store import Clues
        from types import SimpleNamespace
        clues = Clues(SimpleNamespace(workspace=self.root/'workspace'))
        listing = clues.listing(source='爆款监控')
        self.assertEqual(listing['total'], 1)
        detail = clues.detail(first['id'])
        self.assertEqual(detail['entries_total'], 1)
        evidence = detail['entries'][0]['evidence'][0]
        self.assertEqual(evidence['source'], '爆款监控')
        self.assertEqual(evidence['provenance']['tweet_id'], '1')
        self.assertIn('本小时增量', detail['summary'])

    def test_14_two_low_coverage_fault_dedup_and_recovery(self):
        self.run_round(7, coverage=.94)
        self.assertEqual(self.events(), [])
        self.run_round(8, coverage=.90)
        self.run_round(9, coverage=.93)
        self.assertEqual(len(self.events('monitor_fault')), 1)
        self.run_round(10, coverage=.95)
        self.run_round(11)
        self.assertEqual(len(self.events('monitor_recovery')), 1)
        self.assertFalse(self.state()['fault_active'])

    def test_15_shadow_candidates_have_no_outbox_or_clues(self):
        # 3000/h hits 2500 candidate but not production 3500.
        self.run_round(7, [tweet(age=1, views=3000)])
        # Lowfan 8h hits 12h candidate but neither lowfan 6h nor new_fast (preseen).
        self.run_round(8, [tweet('2', hour=8, age=7, views=49000, is_stale=True)])
        self.run_round(9, [tweet('2', hour=9, age=8, views=50000, likes=200, followers=100)])
        shadows = self.state()['shadows']
        self.assertEqual([r['candidates'] for r in shadows], [['new_rate_2500'], ['lowfan_hours_12']])
        self.assertEqual(self.events(), [])
        self.assertFalse((self.root / 'workspace/clues/clues.db').exists())

    def test_16_twenty_three_summary_once_calls_sum(self):
        self.run_round(22, calls=52)
        self.run_round(23, calls=53)
        self.monitor.run_once(when(23, 10))
        summaries = self.events('daily_summary')
        self.assertEqual(len(summaries), 1)
        self.assertEqual(summaries[0]['summary']['api_calls'], 51+52+53)
        self.assertEqual(summaries[0]['summary']['rounds'], 3)
        daily = json.loads((self.monitor.directory / 'daily-2026-10-15.json').read_text())
        self.assertEqual(daily['api_calls'], 156)
        self.assertEqual(daily['unknown_call_rounds'], 0)

    def test_17_no_credentials_in_state_outbox_logs_or_evidence(self):
        secret = 'sk-' + 'aisa-synthetic-b1-canary'
        with patch.dict(os.environ, {'AISA_API_KEY': secret}):
            self.run_round(7, [tweet(views=5000, text='Agent ' + secret)])
            self.result = RuntimeError(secret)
            self.monitor.run_once(when(8))
        for path in (self.root / 'workspace').rglob('*'):
            if path.is_file():
                self.assertNotIn(secret.encode(), path.read_bytes(), str(path))
                self.assertNotIn(('sk-'+'aisa').encode(), path.read_bytes(), str(path))

    def test_pending_clue_write_failure_retries_and_does_not_send(self):
        real = self.monitor.clue_writer
        self.monitor.clue_writer = lambda event: (_ for _ in ()).throw(RuntimeError('数据库失败'))
        self.run_round(7, [tweet(views=5000)])
        self.assertEqual(self.events()[0]['status'], 'pending')
        self.assertEqual(self.deliveries, [])
        self.monitor.clue_writer = real
        self.run_round(8)
        self.assertEqual(len(self.deliveries), 1)
        self.assertIn('1', self.state()['pushed'])

    def test_failed_author_blocks_delta_but_not_lowfan(self):
        self.run_round(7, [tweet(views=100)])  # preseen, no trigger
        self.run_round(8, failed_handles=['test_user'], coverage=.98)
        self.run_round(9, [tweet(hour=9, views=50000)])
        self.assertEqual(self.events(), [])
        self.run_round(10, [tweet(hour=10, views=50000, likes=200, followers=100)])
        self.assertEqual(len(self.events('lowfan_burst')), 1)

    def test_null_metrics_future_or_expired_never_trigger(self):
        cases = [{'views': None}, {'age': -1}, {'age': 24.01}]
        for index, values in enumerate(cases):
            self.run_round(7+index, [tweet(str(index+1), hour=7+index, **values)])
        self.assertEqual(self.events(), [])

    def test_initial_over_100k_does_not_generate_redundant_upgrade(self):
        self.run_round(7, [tweet(views=100000)])
        self.run_round(8, [tweet(hour=8, views=120000)])
        self.assertEqual(len(self.events()), 1)

    def test_projection_failure_committed_state_recovers_on_retry(self):
        original = self.monitor.project
        def fail_after_creation(state):
            if state['events']:
                raise OSError('模拟 JSONL 导出失败')
            original(state)
        with patch.object(self.monitor, 'project', side_effect=fail_after_creation):
            with self.assertRaises(OSError):
                self.run_round(7, [tweet(views=5000)])
        self.assertEqual(self.events()[0]['status'], 'pending')
        self.monitor.run_once(when(7, 5))
        self.assertEqual(len(self.deliveries), 1)
        self.assertEqual(self.events()[0]['status'], 'sent')
        self.assertEqual(self.state()['days']['2026-10-15']['rounds'], 2)

    def test_partial_send_only_confirmed_ids_are_pushed(self):
        self.monitor.sender = lambda events: [{'event_id': events[0]['event_id'], 'status': 'sent'},
                                             {'event_id': 'unrelated', 'status': 'sent'}]
        self.run_round(7, [tweet('1', views=5000), tweet('2', views=5000)])
        self.assertEqual(list(self.state()['pushed']), ['1'])
        self.assertEqual([e['status'] for e in self.events()], ['sent', 'pending'])

    def test_06_baseline_keeps_pending_without_sending(self):
        self.monitor.sender = None
        self.run_round(7, [tweet(views=5000)])
        self.monitor.sender = self.sender
        self.result = snapshot(6, [tweet(hour=6)], run_id='next-day')
        self.monitor.run_once(when(6, day=16))
        self.assertEqual(self.events()[0]['status'], 'pending')
        self.assertEqual(self.deliveries, [])

    def test_cold_start_failure_fault_and_first_recovery_deliver(self):
        (self.monitor.directory / 'state.json').unlink()
        self.result = RuntimeError('首轮采集失败')
        self.monitor.run_once(when(7))
        self.monitor.run_once(when(8))
        self.assertEqual(len(self.events('monitor_fault')), 1)
        self.assertEqual(self.events()[0]['status'], 'sent')
        self.run_round(9)
        self.assertEqual(len(self.events('monitor_recovery')), 1)
        self.assertEqual(self.events()[-1]['status'], 'sent')

    def test_pending_upgrade_counted_only_once(self):
        self.run_round(7, [tweet(views=5000)])
        self.monitor.sender = None
        for hour in (8, 9, 10):
            self.run_round(hour, [tweet(hour=hour, views=100000)])
        self.assertEqual(len(self.events('upgrade_100k')), 1)
        self.assertEqual(self.state()['days']['2026-10-15']['viral_counts']['upgrade_100k'], 1)

    def test_incomplete_run_calls_counted_and_no_tweets_published(self):
        self.run_round(7, [tweet(views=5000)], coverage=.9, calls=54, collector_failed=True)
        self.assertEqual(self.events(), [])
        day = self.state()['days']['2026-10-15']
        self.assertEqual(day['api_calls'], 105)
        self.assertEqual(day['unknown_call_rounds'], 0)
        self.assertEqual(self.state()['samples'], {})

    def test_valid_json_with_corrupt_nested_state_rebuilds_baseline(self):
        self.run_round(7, [tweet(views=5000)])
        original = self.state()
        for field, value in [('views', 'bad'), ('is_stale', 'false'), ('fetched_at', 'invalid')]:
            with self.subTest(field=field):
                state = copy.deepcopy(original)
                state['samples']['1'][field] = value
                (self.monitor.directory/'state.json').write_text(json.dumps(state))
                self.run_round(8, [tweet('2', hour=8, views=50000)])
                self.assertEqual(len(self.deliveries), 1)
                self.assertEqual(len(self.events()), 1)

    def test_changed_filter_interface_fails_closed_and_faults(self):
        (self.root/'scripts/radar_common.py').write_text('# 接口已变化，等待适配\n')
        self.run_round(7, [tweet(views=5000)])
        self.run_round(8, [tweet(hour=8, views=10000)])
        self.assertEqual(len(self.events('monitor_fault')), 1)
        self.assertEqual(self.state()['samples'], {})
        self.assertEqual(self.state()['days']['2026-10-15']['api_calls'], 153)


class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='b1-adapter-', dir='/tmp')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root/'scripts').mkdir()
        (self.root/'scripts/radar_targets.json').write_text(json.dumps(['test_user', 'second']))
        self.folder = self.root/'workspace/twitter-radar/snapshots/2026-10-15'
        self.folder.mkdir(parents=True)
        self.raw = {'schema_version': 1, 'run_id': 'one', 'started_at': when(7).isoformat(),
                    'ended_at': when(7, 1).isoformat(), 'attempted': 2, 'success': 2, 'failed': 0, 'api_calls': 3,
                    'accounts': [{'handle': 'test_user', 'status': 'success', 'followers': 10000},
                                 {'handle': 'second', 'status': 'empty_success', 'followers': None}],
                    'tweets': [{**tweet(), 'author': {'handle': 'test_user', 'user_id': '11'}, 'views': None}]}

    def read(self, **args):
        from test_radar_monitor_a1_fixture import commit_fixture
        commit_fixture(self.root, self.raw)
        return read_snapshot(self.root, **args)

    def test_contract_shape_null_metrics_and_live_followers(self):
        result = self.read()
        self.assertEqual(result['coverage'], 1)
        self.assertEqual(result['api_calls'], 3)
        self.assertIsNone(result['tweets'][0]['views'])
        self.assertEqual(result['tweets'][0]['followers'], 10000)

    def test_nested_accounts_and_metadata(self):
        raw = self.raw
        accounts = {x['handle']: x for x in raw['accounts']}
        accounts['test_user']['tweets'] = raw['tweets']
        self.raw = {'run': {k: v for k, v in raw.items() if k not in ('accounts', 'tweets')}, 'accounts': accounts}
        self.assertEqual(self.read()['tweets'][0]['tweet_id'], '1')

    def test_replayed_or_old_snapshot_is_rejected(self):
        with self.assertRaises(ValueError):
            self.read(excluded_run_ids={'one'})
        with self.assertRaises(ValueError):
            self.read(started_at=when(8))

    def test_failed_account_marks_old_and_fixed_denominator(self):
        self.raw['accounts'][0]['status'] = 'timeout'
        self.raw.update(success=1, failed=1)
        result = self.read()
        self.assertEqual(result['coverage'], .5)
        self.assertTrue(result['tweets'][0]['is_stale'])
        self.assertEqual(result['failed_handles'], ['test_user'])

    def test_inconsistent_counts_fail_closed(self):
        self.raw['success'] = 0
        with self.assertRaises(ValueError):
            self.read()

    def test_conflicting_duplicate_id_rejected(self):
        self.raw['tweets'].append({**self.raw['tweets'][0], 'views': 9999})
        with self.assertRaises(ValueError):
            self.read()

    def test_collector_deadline_covers_process_group(self):
        # Real subprocess timeout; child has no network code and cannot touch production.
        child = self.root/'scripts/refresh_twitter_radar.py'
        child.write_text('import subprocess,sys,time\nsubprocess.Popen([sys.executable,"-c","import time; time.sleep(30)"])\ntime.sleep(30)\n')
        import time
        started = time.monotonic()
        with self.assertRaises(ValueError):
            collect_snapshot(self.root, .15, (), when(7))
        self.assertLess(time.monotonic()-started, 3)

    def test_nonzero_collector_retains_partial_run_calls(self):
        self.read()
        (self.root/'scripts/refresh_twitter_radar.py').write_text(
            'import json,sys\nfrom pathlib import Path\n'
            + 'Path(sys.argv[2]).write_text(json.dumps(' + repr({'run_id': 'failed-current', 'started_at': when(7).isoformat(), 'api_calls': 3, 'coverage': 1, 'published': False}) + '))\nraise SystemExit(1)\n')
        result = collect_snapshot(self.root, 2, (), when(7))
        self.assertEqual(result['api_calls'], 3)
        self.assertTrue(result['collector_failed'])

    def test_launchd_template_schedule_and_wrapper_syntax(self):
        path = ROOT/'scripts/launchd/com.servasyy.radar-monitor.plist'
        data = plistlib.loads(path.read_bytes())
        self.assertEqual(data['StartCalendarInterval'], [{'Hour': h, 'Minute': 0} for h in range(6,24)])
        self.assertFalse(data['RunAtLoad'])
        self.assertEqual(data['EnvironmentVariables']['TZ'], 'Asia/Shanghai')
        result = subprocess.run(['/bin/zsh', '-n', str(ROOT/'scripts/launchd/run-radar-monitor.sh')], capture_output=True)
        self.assertEqual(result.returncode, 0)

    def test_atomic_write_failure_keeps_old_content_and_cleans_temp(self):
        path = self.root/'state.json'
        atomic_write(path, '{"old":true}')
        with patch('scripts.radar_monitor.os.replace', side_effect=OSError('模拟 rename 失败')):
            with self.assertRaises(OSError):
                atomic_write(path, '{"new":true}')
        self.assertEqual(json.loads(path.read_text()), {'old': True})
        self.assertEqual(list(self.root.glob('.state.json.*')), [])


if __name__ == '__main__':
    unittest.main()
