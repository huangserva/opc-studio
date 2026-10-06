"""Synthetic scheduler rows and mock analyzers only; never launch a real task."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scheduled_tasks import RUN_STATUSES, ScheduledTasks


class Analyzer:
    def __init__(self, progress, *, error=None, result=None, on_call=None):
        self.progress = progress
        self.error = error or ValueError('合成引用定位失败')
        self.result = result
        self.on_call = on_call
        self.run_id = None
        self.status_calls = 0

    def analyze(self, run_id, task, cancel, config, deadline):
        self.run_id = run_id
        if self.on_call: self.on_call(cancel, deadline)
        if self.result is not None: return self.result
        raise self.error

    def status(self):
        self.status_calls += 1
        progress = copy.deepcopy(self.progress)
        if isinstance(progress, dict): progress.setdefault('run_id', self.run_id)
        return {'progress': progress}


class SchedulePartialTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.scheduler = ScheduledTasks(self.temporary.name, autostart=False)
        self.addCleanup(self.scheduler.close)
        self.scheduler.model_config = Mock(return_value={'model': 'synthetic'})
        self.scheduler.publisher = Mock(return_value={'id': 'c' * 32})

    def start(self, *, kind='wechat_clues', destination='runs', trigger='manual'):
        payload = {'name': '合成微信分析', 'kind': kind, 'body': '仅合成测试资料',
                   'cwd': self.temporary.name, 'frequency': 'interval', 'minutes': 60,
                   'at': '08:00', 'weekdays': list(range(7)), 'ends_at': None, 'timeout': 5,
                   'enabled': False, 'profile_id': 'synthetic', 'allow_commands': False,
                   'result_destination': destination, 'wechat_mode': 'incremental'}
        task_id = self.scheduler.save(payload)['id']
        run_id = self.scheduler.claim(task_id, trigger, launch=False)['id']
        return run_id, self.scheduler.get_run(run_id)['snapshot'], self.scheduler.active[run_id]

    def execute(self, analyzer, **options):
        self.scheduler.wechat_analyzer = analyzer.analyze
        run_id, task, cancel = self.start(**options)
        self.scheduler.execute(run_id, task, cancel, {'model': 'synthetic'})
        row = self.scheduler.get_run(run_id)
        self.assertIsNotNone(row['finished_at'])
        self.assertNotIn(run_id, self.scheduler.active)
        return row, json.loads(row['output'])

    def test_unexpected_exception_preserves_owned_cumulative_progress(self):
        analyzer = Analyzer({'processed': 141, 'skipped': 8, 'clue_ids': ['a' * 32, 'b' * 32, 'a' * 32],
                             'has_more': True, 'deferred': 10, 'private_dump': 'must not be serialized',
                             'capture': {'private_dump': 'also excluded'}, 'errors': [{
                                 'batch_id': 'd' * 64, 'message': '合成批次等待重试', 'documents': 10,
                                 'attempts': 2, 'next_retry_at': '2026-10-06T01:00:00Z',
                                 'private_dump': 'excluded'}]})
        row, result = self.execute(analyzer)
        self.assertEqual(row['status'], 'partial')
        self.assertEqual(result['processed'], 141)
        self.assertEqual(result['skipped'], 8)
        self.assertEqual(result['clues'], ['a' * 32, 'b' * 32])
        self.assertEqual(result['deferred'], 10)
        self.assertTrue(result['has_more'])
        self.assertIn('持续增量', result['scope'])
        self.assertEqual(result['errors'][0], {'batch_id': 'd' * 64, 'message': '合成批次等待重试',
                                             'documents': 10, 'attempts': 2,
                                             'next_retry_at': '2026-10-06T01:00:00Z'})
        self.assertEqual(result['errors'][-1]['message'], '合成引用定位失败')
        self.assertNotIn('private_dump', row['output'])
        self.assertNotIn('capture', result)
        self.scheduler.publisher.assert_not_called()

    def test_each_durable_progress_kind_qualifies_as_partial(self):
        for change in ({'processed': 1}, {'skipped': 1}, {'clue_ids': ['a' * 32]}):
            with self.subTest(change=change):
                row, _ = self.execute(Analyzer({'processed': 0, 'skipped': 0, 'clue_ids': [], **change}))
                self.assertEqual(row['status'], 'partial')

    def test_valid_zero_progress_remains_failed_with_structured_error(self):
        row, result = self.execute(Analyzer({'processed': 0, 'skipped': 0, 'clue_ids': []},
                                           error=RuntimeError('private implementation detail')))
        self.assertEqual(row['status'], 'failed')
        self.assertEqual((result['processed'], result['skipped'], result['clues']), (0, 0, []))
        self.assertNotIn('private implementation detail', row['output'])
        self.assertEqual(len(result['errors']), 1)

    def test_invalid_or_other_run_progress_is_ignored(self):
        base = {'processed': 2, 'skipped': 1, 'clue_ids': ['a' * 32], 'has_more': True}
        cases = [None, [], {**base, 'run_id': 'f' * 32}, {**base, 'processed': True},
                 {**base, 'processed': -1}, {**base, 'skipped': 1.5}, {**base, 'skipped': '1'},
                 {**base, 'clue_ids': ['not-a-clue-id']}, {**base, 'clue_ids': ['A' * 32]},
                 {**base, 'clue_ids': 'a' * 32}, {**base, 'has_more': 1},
                 {'processed': 2, 'clue_ids': ['a' * 32]}]
        for progress in cases:
            with self.subTest(progress=progress):
                row, result = self.execute(Analyzer(progress))
                self.assertEqual(row['status'], 'failed')
                self.assertEqual((result['processed'], result['skipped'], result['clues']), (0, 0, []))

    def test_unbound_or_broken_status_owner_does_not_supply_progress(self):
        def analyze(*args): raise ValueError('合成异常')
        analyze.status = Mock(return_value={'progress': {'processed': 10, 'skipped': 1}})
        self.scheduler.wechat_analyzer = analyze
        run_id, task, cancel = self.start()
        self.scheduler.execute(run_id, task, cancel, {})
        self.assertEqual(self.scheduler.get_run(run_id)['status'], 'failed')
        analyze.status.assert_not_called()
        analyzer = Analyzer({'processed': 10, 'skipped': 1})
        analyzer.status = Mock(side_effect=OSError('synthetic unavailable status'))
        row, result = self.execute(analyzer)
        self.assertEqual(row['status'], 'failed')
        self.assertEqual(result['processed'], 0)
        self.assertNotIn('unavailable status', row['output'])

    def test_invalid_optional_error_fields_are_not_serialized(self):
        analyzer = Analyzer({'processed': 1, 'skipped': 0, 'deferred': True, 'errors': [
            {'message': '合成错误', 'documents': True, 'attempts': -1, 'batch_id': 'not-a-batch-id',
             'next_retry_at': 'not-a-date'},
            {'message': '合成无时区', 'next_retry_at': '2026-10-06T12:00:00'},
            {'message': 12}, 'invalid']})
        _, result = self.execute(analyzer)
        self.assertEqual(result['errors'][:-1], [{'message': '合成错误'}, {'message': '合成无时区'}])
        self.assertNotIn('deferred', result)

    def test_fallback_preserves_valid_review_states_and_bounded_cache_warnings(self):
        for fresh in (True, False):
            with self.subTest(fresh=fresh):
                analyzer = Analyzer({'processed': 1, 'skipped': 0, 'errors': [
                    {'message': '待重试', 'state': 'waiting'},
                    {'message': '待核对', 'state': 'needs_review'},
                    {'message': '无状态', 'state': None},
                    {'message': '错误状态', 'state': 'running'}], 'capture': {
                        'fresh': fresh, 'warning': '旧缓存警告' * 500, 'status': 's' * 2500,
                        'index_status': 'i' * 2500, 'private_dump': 'private capture contents',
                        'source': {'private_token': 'excluded'}}})
                row, result = self.execute(analyzer)
                self.assertEqual([error.get('state') for error in result['errors'][:3]],
                                 ['waiting', 'needs_review', None])
                self.assertIn('state', result['errors'][2])
                self.assertNotIn('state', result['errors'][3])
                self.assertEqual(result['capture'], {'fresh': fresh, 'warning': ('旧缓存警告' * 500)[:2000],
                                                     'status': 's' * 2000, 'index_status': 'i' * 2000})
                self.assertNotIn('private_dump', row['output'])
                self.assertNotIn('private_token', row['output'])

    def test_invalid_capture_fields_and_error_states_are_ignored(self):
        _, result = self.execute(Analyzer({'processed': 1, 'skipped': 0,
                                          'capture': {'fresh': 1, 'warning': {}, 'status': 2, 'index_status': None},
                                          'errors': [{'message': '无效状态', 'state': False}]}))
        self.assertNotIn('capture', result)
        self.assertNotIn('state', result['errors'][0])

    def test_partial_return_never_records_a_success_exit_code(self):
        output = json.dumps({'scope': '合成部分完成', 'processed': 1, 'skipped': 0,
                             'clues': [], 'has_more': True, 'errors': []})
        row, _ = self.execute(Analyzer({}, result=('partial', 0, output)))
        self.assertEqual(row['status'], 'partial')
        self.assertIsNone(row['exit_code'])
        self.assertEqual(row['output'], output)
        self.scheduler.publisher.assert_not_called()

    def test_cancel_or_deadline_overrides_partial_exception_and_keeps_progress(self):
        for stopping in (True, False):
            with self.subTest(stopping=stopping):
                clock = [10.0]
                def advance(cancel, deadline):
                    if stopping: cancel.set()
                    else: clock[0] = deadline + 1
                analyzer = Analyzer({'processed': 9, 'skipped': 2, 'clue_ids': ['a' * 32]}, on_call=advance)
                with patch('scheduled_tasks.time.monotonic', lambda: clock[0]):
                    row, result = self.execute(analyzer)
                self.assertEqual(row['status'], 'cancelled' if stopping else 'timeout')
                self.assertEqual((result['processed'], result['skipped'], result['clues']), (9, 2, ['a' * 32]))

    def test_success_or_partial_return_checks_cancel_deadline_and_preserves_output(self):
        output = json.dumps({'scope': '合成范围', 'processed': 9, 'skipped': 2, 'clues': ['a' * 32],
                             'has_more': True, 'errors': []})
        for status in ('succeeded', 'partial'):
            for stopping in (True, False):
                with self.subTest(status=status, stopping=stopping):
                    clock = [10.0]
                    def advance(cancel, deadline):
                        if stopping: cancel.set()
                        else: clock[0] = deadline + 1
                    analyzer = Analyzer({}, result=(status, 0, output), on_call=advance)
                    with patch('scheduled_tasks.time.monotonic', lambda: clock[0]):
                        row, _ = self.execute(analyzer)
                    self.assertEqual(row['status'], 'cancelled' if stopping else 'timeout')
                    self.assertIsNone(row['exit_code'])
                    self.assertEqual(row['output'], output)
                    self.assertEqual(analyzer.status_calls, 0)

    def test_partial_is_terminal_searchable_and_not_auto_delivered(self):
        self.assertIn('partial', RUN_STATUSES)
        row, _ = self.execute(Analyzer({'processed': 8, 'skipped': 0, 'clue_ids': []}))
        filtered = self.scheduler.runs(status='partial', q='引用', page=1, page_size=1)
        self.assertEqual(filtered['total'], 1)
        self.assertFalse(filtered['has_more'])
        self.assertEqual(filtered['runs'][0]['id'], row['id'])
        self.assertEqual(self.scheduler.listing()['tasks'][0]['last_run']['status'], 'partial')
        self.assertEqual(self.scheduler.listing()['concurrency']['active'], 0)
        self.scheduler.close()
        reopened = ScheduledTasks(self.temporary.name, autostart=False)
        self.addCleanup(reopened.close)
        self.assertEqual(reopened.get_run(row['id'])['status'], 'partial')
        self.assertEqual(reopened.get_run(row['id'])['output'], row['output'])
        self.scheduler.publisher.assert_not_called()

    def test_generic_partial_result_never_uses_success_auto_delivery(self):
        run_id, task, cancel = self.start(kind='pi', destination='clues')
        self.scheduler.pi = Mock(return_value=('partial', None, 'synthetic partial result'))
        self.scheduler.execute(run_id, task, cancel, {})
        self.assertEqual(self.scheduler.get_run(run_id)['status'], 'partial')
        self.scheduler.loop = Mock()
        self.scheduler.run_loop()
        self.scheduler.publisher.assert_not_called()

    def test_claim_snapshots_trigger_without_changing_saved_task_or_permissions(self):
        for trigger in ('manual', 'scheduled'):
            with self.subTest(trigger=trigger):
                run_id, snapshot, _ = self.start(trigger=trigger)
                with self.scheduler.db() as db:
                    saved = db.execute('SELECT * FROM tasks WHERE id=?', (snapshot['id'],)).fetchone()
                    saved_data = json.loads(saved['data'])
                self.assertEqual(snapshot['execution_trigger'], trigger)
                self.assertEqual(snapshot['assistant_id'], 'isolated')
                self.assertEqual(snapshot['tool_groups'], [])
                self.assertNotIn('execution_trigger', saved_data)
                self.assertFalse(saved_data['enabled'])
                self.assertEqual(saved['revision'], 1)
                # No executor is launched by start(); retire this synthetic claim
                # before testing the next trigger under the same model profile.
                with self.scheduler.db() as db:
                    db.execute("UPDATE runs SET status='cancelled' WHERE id=?", (run_id,))
                self.scheduler.active.pop(run_id)

    def test_return_deadline_override_is_limited_to_wechat(self):
        for kind in ('pi', 'script'):
            with self.subTest(kind=kind):
                clock = [10.0]
                run_id, task, cancel = self.start(kind=kind)
                def synthetic_result(*args):
                    clock[0] = 20.0
                    cancel.set()
                    return 'succeeded', 0, 'synthetic existing behavior'
                with patch('scheduled_tasks.time.monotonic', lambda: clock[0]), \
                     patch.object(self.scheduler, 'pi', synthetic_result), \
                     patch.object(self.scheduler, 'shell', synthetic_result):
                    self.scheduler.execute(run_id, task, cancel, {})
                self.assertEqual(self.scheduler.get_run(run_id)['status'], 'succeeded')


if __name__ == '__main__': unittest.main()
