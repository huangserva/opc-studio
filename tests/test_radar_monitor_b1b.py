"""B1-b offline contract probes; real SIGKILL, immutable feedback, B2 API."""
import copy
from datetime import timedelta
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import signal
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

import test_radar_monitor as b1
from scripts import radar_monitor as module

ROOT = Path(__file__).resolve().parents[1]


class B1bTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='b1b-case-', dir='/tmp')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root/'scripts').mkdir()
        shutil.copy2(ROOT/'scripts/radar_monitor_config.json', self.root/'scripts/radar_monitor_config.json')
        self.result = b1.snapshot(6)
        self.sent = []
        self.calls = 0
        def collector(*args):
            self.calls += 1
            if isinstance(self.result, Exception):
                raise self.result
            return copy.deepcopy(self.result)
        def sender(events):
            self.sent.extend(copy.deepcopy(events))
            return [{'event_id': e['event_id'], 'status': 'sent'} for e in events]
        self.monitor = module.Monitor(self.root, collector=collector, sender=sender,
                                      clue_writer=lambda e: {'id': 'clue-'+e['tweet']['tweet_id']},
                                      ai_filter=lambda t: 'Agent' in t['text'])
        self.monitor.run_once(b1.when(6))
        self.pagefile = self.monitor.directory/'page-hourly.json'
        self.feedbackfile = self.monitor.directory/'feedback.json'

    def run_round(self, hour, tweets=(), **extra):
        self.result = b1.snapshot(hour, tweets, **extra)
        return self.monitor.run_once(b1.when(hour))

    def page(self):
        return json.loads(self.pagefile.read_text())

    def state(self):
        return json.loads((self.monitor.directory/'state.json').read_text())

    def feedback(self):
        return self.state()['days']['2026-10-15']['feedback']

    def test_01_baseline_has_null_deltas_no_events(self):
        self.result = b1.snapshot(6, [b1.tweet(hour=6, views=60000, likes=250, followers=100)])
        (self.monitor.directory/'state.json').unlink()
        self.assertEqual(self.monitor.run_once(b1.when(6))['status'], 'baseline')
        page = self.page()
        self.assertIs(type(page['schema']), int)
        self.assertEqual(page['run_id'], 'run-6')
        self.assertTrue(page['generated_at'].endswith('Z'))
        self.assertEqual(self.state()['events'], [])
        self.assertEqual(self.sent, [])
        row = page['tweets']['1']
        for key in ('hourly_delta', 'views_delta', 'interval_hours', 'hit'):
            self.assertIsNone(row[key])
        self.assertTrue(row['fetched_at'].endswith('Z'))
        self.assertFalse(row['pushed'])

    def test_02_normal_delta_hit_and_sent_match(self):
        (self.monitor.directory/'state.json').unlink()
        self.run_round(6, [b1.tweet(hour=6, views=100)])
        self.run_round(7, [b1.tweet(hour=7, views=6100)])
        row = self.page()['tweets']['1']
        self.assertEqual((row['hourly_delta'], row['views_delta'], row['interval_hours']), (6000, 6000, 1))
        self.assertEqual(row['hit'], self.state()['events'][-1]['type'])
        self.assertEqual(row['hit'], 'hourly_delta')
        self.assertEqual(row['pushed'], self.state()['events'][-1]['status'] == 'sent')
        self.assertTrue(row['pushed'])
        self.run_round(8, [b1.tweet(hour=8, views=100000)])
        self.assertEqual(self.page()['tweets']['1']['hit'], 'upgrade_100k')
        self.assertTrue(self.page()['tweets']['1']['pushed'])

    def test_03_low_coverage_and_failure_preserve_bytes_and_inode(self):
        self.run_round(7, [b1.tweet(views=5000)])
        before, inode = self.pagefile.read_bytes(), self.pagefile.stat().st_ino
        for hour, extra in ((8, {'coverage': .94}), (9, {'collector_failed': True})):
            self.run_round(hour, [b1.tweet(hour=hour, views=100000)], **extra)
            self.assertEqual(self.pagefile.read_bytes(), before)
            self.assertEqual(self.pagefile.stat().st_ino, inode)
        self.result = RuntimeError('offline collector fails')
        self.monitor.run_once(b1.when(10))
        self.assertEqual(self.pagefile.read_bytes(), before)
        self.assertEqual(self.pagefile.stat().st_ino, inode)

    def test_failed_later_slot_does_not_repair_interrupted_successful_export(self):
        before, inode = self.pagefile.read_bytes(), self.pagefile.stat().st_ino
        original = self.monitor.project
        def interrupt(state):
            if state.get('page_hourly', {}).get('run_id') == 'run-7':
                raise OSError('success committed, page not exported')
            return original(state)
        with patch.object(self.monitor, 'project', side_effect=interrupt):
            with self.assertRaises(OSError):
                self.run_round(7, [b1.tweet(views=5000)])
        self.assertEqual(self.state()['page_hourly']['run_id'], 'run-7')
        self.run_round(8, coverage=.94)
        self.assertEqual(self.pagefile.read_bytes(), before)
        self.assertEqual(self.pagefile.stat().st_ino, inode)

    def test_04_three_am_does_not_write_or_read_feedback(self):
        before, inode, calls = self.pagefile.read_bytes(), self.pagefile.stat().st_ino, self.calls
        with patch.object(self.monitor, 'read_feedback', side_effect=AssertionError('night read')):
            self.assertEqual(self.monitor.run_once(b1.when(3))['status'], 'outside_hours')
        self.assertEqual(self.calls, calls)
        self.assertEqual(self.pagefile.read_bytes(), before)
        self.assertEqual(self.pagefile.stat().st_ino, inode)
        self.pagefile.unlink()
        self.monitor.run_once(b1.when(3))
        self.assertFalse(self.pagefile.exists())

    def test_05_real_sigkill_during_atomic_projection_preserves_old_json_and_repairs(self):
        old = self.pagefile.read_bytes()
        marker = self.root/'at-rename'
        script = r'''
import sys,time
from pathlib import Path
sys.path.insert(0,sys.argv[1]); sys.path.insert(0,str(Path(sys.argv[1])/'tests'))
from scripts import radar_monitor as m
import test_radar_monitor as b
root=Path(sys.argv[2]); marker=Path(sys.argv[3]); real=m.os.replace
def replace(source,target):
 if Path(target).name=='page-hourly.json':
  marker.write_text('ready')
  time.sleep(60)
 return real(source,target)
m.os.replace=replace
m.Monitor(root,collector=lambda *a:b.snapshot(7,[b.tweet(views=5000)]),ai_filter=lambda t:True,
 clue_writer=lambda e:{'id':'offline'},sender=lambda e:[]).run_once(b.when(7))
'''
        child = subprocess.Popen([sys.executable, '-c', script, str(ROOT), str(self.root), str(marker)],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        try:
            deadline = time.monotonic()+5
            while not marker.exists() and child.poll() is None and time.monotonic() < deadline:
                time.sleep(.01)
            self.assertTrue(marker.exists(), child.poll())
            child.kill()
            self.assertEqual(child.wait(timeout=5), -signal.SIGKILL)
            self.assertEqual(self.pagefile.read_bytes(), old)
            self.assertEqual(json.loads(old), self.page())
            calls = self.calls
            self.assertEqual(self.monitor.run_once(b1.when(7, 5))['status'], 'already_run')
            self.assertEqual(self.calls, calls)
            self.assertEqual(self.page()['run_id'], 'run-7')
            self.assertTrue(self.page()['tweets']['1']['pushed'])
        finally:
            if child.poll() is None:
                child.kill(); child.wait(timeout=5)
            child.stderr.close()

    def test_06_null_views_missing_history_zero_interval_and_falling_views(self):
        self.run_round(7, [b1.tweet('1', views=None), b1.tweet('2', views=10), b1.tweet('3', views=10)])
        self.run_round(8, [b1.tweet('1', hour=8, views=100),
                           b1.tweet('2', hour=8, views=100, fetched_at=b1.when(7).isoformat()),
                           b1.tweet('3', hour=8, views=9), b1.tweet('4', hour=8, views=None)])
        page = self.page()
        for row in page['tweets'].values():
            for key in ('hourly_delta', 'views_delta', 'interval_hours'):
                self.assertIsNone(row[key])
        self.assertIsNone(page['tweets']['4']['views'])
        json.dumps(page, allow_nan=False)
        self.assertNotIn('Infinity', self.pagefile.read_text())
        self.assertNotIn('NaN', self.pagefile.read_text())

    def test_projection_nonfinite_string_boolean_metrics_are_null(self):
        self.run_round(7, [b1.tweet(str(i), views=bad) for i, bad in enumerate(
            (float('inf'), float('nan'), '5000', True), start=1)])
        for row in self.page()['tweets'].values():
            self.assertIsNone(row['views'])
            self.assertIsNone(row['hourly_delta'])
        json.dumps(self.page(), allow_nan=False)
        self.assertEqual(self.state()['events'], [])

    def test_07_non_ascii_ids_excluded_and_logged(self):
        self.run_round(7, [b1.tweet(tid, views=5000) for tid in ('１２３', '١٢٣', '123\n', 'a12', '12')])
        self.assertEqual(set(self.page()['tweets']), {'12'})
        self.assertIn('非 ASCII', (self.monitor.directory/'monitor.log').read_text())
        self.assertEqual([e['tweet']['tweet_id'] for e in self.state()['events']], ['12'])

    def test_08_unavailable_malformed_and_unpushed_feedback_are_read_only(self):
        self.run_round(7, [b1.tweet('1', views=5000), b1.tweet('2', views=5000)])
        self.assertFalse(self.feedbackfile.exists())
        self.assertEqual(self.feedback()['status'], 'unavailable')
        for content in ('{broken', '[]', '{"1":{"verdict":"WORTH"},"2":{"verdict":[]},"999":{"verdict":"worth","pushed":true}}',
                        '{"1":{"verdict":"worth","pushed":false},"999":{"verdict":"not_worth","pushed":true}}'):
            with self.subTest(content=content):
                self.feedbackfile.write_text(content)
                before = hashlib.sha256(self.feedbackfile.read_bytes()).hexdigest()
                self.monitor.run_once(b1.when(7, 10))
                stats = self.feedback()
                self.assertEqual(stats['today']['pushed'], 2)
                if content.startswith('{"1":{"verdict":"worth"'):
                    self.assertEqual(stats['today']['worth'], 1)
                    self.assertEqual(stats['today']['not_worth'], 0)
                else:
                    self.assertEqual(stats['today']['worth'], 0)
                    self.assertIsNone(stats['today']['useful_rate'])
                self.assertEqual(hashlib.sha256(self.feedbackfile.read_bytes()).hexdigest(), before)

    def test_09_three_sent_two_worth_one_unreviewed_and_no_feedback_null(self):
        self.feedbackfile.write_text(json.dumps({'1': {'verdict':'worth'}, '2':{'verdict':'worth'}}))
        self.run_round(22, [b1.tweet(str(i), hour=22, views=5000) for i in range(1,4)])
        expected = {'pushed':3, 'worth':2, 'not_worth':0, 'unreviewed':1, 'useful_rate':1.0}
        self.assertEqual(self.feedback()['today'], expected)
        summary = next(e for e in self.sent if e['type']=='daily_summary')
        self.assertEqual(summary['summary']['feedback']['today'], expected)
        self.assertEqual(self.sent[-1]['type'], 'daily_summary')
        self.feedbackfile.write_text('{}')
        self.monitor.run_once(b1.when(22, 10))
        self.assertEqual(self.feedback()['today']['unreviewed'], 3)
        self.assertIsNone(self.feedback()['today']['useful_rate'])
        # The previously sent executive summary is an immutable delivery snapshot.
        event = next(e for e in self.state()['events'] if e['type']=='daily_summary')
        self.assertEqual(event['summary']['feedback']['today'], expected)

    def test_10_read_only_b2_monitor_view_matches_projection(self):
        api_path = ROOT / 'radar_page_api.py'
        self.assertTrue(api_path.is_file(), 'B2 contract artifact is required')
        before = hashlib.sha256(api_path.read_bytes()).hexdigest()
        spec = importlib.util.spec_from_file_location('b1b_b2_api', api_path)
        api = importlib.util.module_from_spec(spec); spec.loader.exec_module(api)
        self.run_round(7, [b1.tweet(hour=7, views=100)])
        self.run_round(8, [b1.tweet(hour=8, views=6100)])
        page = self.page()
        response = api.monitor_view(self.root, now=b1.when(8))
        self.assertEqual(response['status'], 'ok')
        self.assertEqual(response['run_id'], page['run_id'])
        self.assertEqual(response['generated_at'], page['generated_at'])
        self.assertEqual(response['tweets'], page['tweets'])
        self.assertEqual(hashlib.sha256(api_path.read_bytes()).hexdigest(), before)

    def test_scope_filter_includes_null_and_stale_excludes_non_ai_expired_replies(self):
        rows = [b1.tweet('1', views=None), b1.tweet('2', is_stale=True),
                b1.tweet('3', type='quote'), b1.tweet('4', type='retweet'),
                b1.tweet('5', type='reply'), b1.tweet('6', text='sports'),
                b1.tweet('7', age=24.01), b1.tweet('8', age=-1)]
        self.run_round(7, rows)
        self.assertEqual(set(self.page()['tweets']), {'1','2','3'})
        self.assertTrue(self.page()['tweets']['2']['is_stale'])
        self.assertIsNone(self.page()['tweets']['2']['hourly_delta'])

    def test_rebuild_uses_recovered_events_and_never_invents_hits(self):
        self.run_round(7, [b1.tweet(views=5000)])
        (self.monitor.directory/'state.json').write_text('{broken')
        self.run_round(8, [b1.tweet(hour=8, views=9000), b1.tweet('2', hour=8, views=9000)])
        self.assertEqual(self.page()['tweets']['1']['hit'], 'new_fast')
        self.assertTrue(self.page()['tweets']['1']['pushed'])
        self.assertIsNone(self.page()['tweets']['2']['hit'])
        self.assertFalse(self.page()['tweets']['2']['pushed'])
        for row in self.page()['tweets'].values():
            self.assertIsNone(row['hourly_delta'])
        (self.monitor.directory/'state.json').write_text('{broken')
        (self.monitor.directory/'outbox.jsonl').write_text('{broken')
        self.run_round(9, [b1.tweet(hour=9, views=10000)])
        self.assertIsNone(self.page()['tweets']['1']['hit'])
        self.assertFalse(self.page()['tweets']['1']['pushed'])

    def test_pending_initial_and_upgrade_status_matches_confirmed_delivery(self):
        self.monitor.sender = lambda events: []
        self.run_round(7, [b1.tweet(views=5000)])
        row = self.page()['tweets']['1']
        self.assertEqual(row['hit'], 'new_fast')
        self.assertFalse(row['pushed'])
        self.assertEqual(self.feedback()['today']['pushed'], 0)
        self.monitor.sender = lambda events: [{'event_id':e['event_id'],'status':'sent'} for e in events]
        self.monitor.run_once(b1.when(7,5))
        self.assertTrue(self.page()['tweets']['1']['pushed'])
        self.monitor.sender = lambda events: []
        self.run_round(8, [b1.tweet(hour=8, views=100000)])
        self.assertEqual(self.page()['tweets']['1']['hit'], 'upgrade_100k')
        self.assertTrue(self.page()['tweets']['1']['pushed'])
        self.assertEqual(self.feedback()['today']['pushed'], 1)

    def test_feedback_seven_beijing_days_boundaries_upgrade_dedup_pending_ignored(self):
        state = self.state()
        for tid, moment, kind, status in (
            ('1', b1.when(7), 'new_fast', 'sent'),
            ('2', b1.when(0,day=9), 'new_fast', 'sent'),
            ('3', b1.when(23,day=8), 'new_fast', 'sent'),
            ('4', b1.when(7), 'new_fast', 'pending'),
            ('2', b1.when(7), 'upgrade_100k', 'sent'),
            ('5', b1.when(8), 'new_fast', 'sent')):
            event = {'event_id':f'{tid}-{kind}', 'type':kind, 'status':status,
                     'created_at':moment.isoformat(), 'sent_at':moment.isoformat(),
                     'tweet':b1.tweet(tid,views=5000), 'clue_id':'offline'}
            state['events'].append(event)
        self.monitor.commit(state)
        self.monitor.sender = lambda events: []
        self.feedbackfile.write_text(json.dumps({'1':{'verdict':'worth'},'2':{'verdict':'not_worth'},
                                               '3':{'verdict':'worth'},'4':{'verdict':'worth'},'5':{'verdict':'worth'}}))
        self.run_round(7)
        stats = self.feedback()
        self.assertEqual(stats['today']['pushed'], 1)
        self.assertEqual(stats['recent_7_days'], {'pushed':2,'worth':1,'not_worth':1,'unreviewed':0,'useful_rate':.5})

    def test_feedback_read_once_per_active_round_even_with_multiple_commits(self):
        with patch.object(self.monitor, 'read_feedback', wraps=self.monitor.read_feedback) as read:
            self.run_round(22, [b1.tweet(views=5000, hour=22)])
        self.assertEqual(read.call_count, 1)

    def test_feedback_successful_failed_and_baseline_rounds_all_update_stats(self):
        self.feedbackfile.write_text('{}')
        self.run_round(7, [b1.tweet(views=5000)])
        self.feedbackfile.write_text('{"1":{"verdict":"worth"}}')
        self.run_round(8, coverage=.94)
        self.assertEqual(self.feedback()['today']['useful_rate'], 1.0)
        self.result = b1.snapshot(6)
        self.monitor.run_once(b1.when(6,day=16))
        day = self.state()['days']['2026-10-16']['feedback']
        self.assertEqual(day['today']['pushed'],0)
        self.assertEqual(day['recent_7_days']['worth'],1)


if __name__ == '__main__':
    unittest.main()
