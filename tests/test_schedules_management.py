import json
from pathlib import Path
import sqlite3
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scheduled_tasks import ScheduledTasks


class ScheduleManagementTests(unittest.TestCase):
    """All tasks and run rows are synthetic; no task is executed in these tests."""
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.scheduler = ScheduledTasks(self.temporary.name, autostart=False)
        self.addCleanup(self.scheduler.close)

    def task(self, **changes):
        payload = {'name': 'Synthetic task', 'kind': 'script', 'body': 'synthetic body only',
                   'cwd': self.temporary.name, 'frequency': 'interval', 'minutes': 60,
                   'at': '08:00', 'weekdays': list(range(7)), 'ends_at': None, 'timeout': 300,
                   'enabled': False, 'profile_id': None, 'allow_commands': False,
                   'result_destination': 'runs'}
        payload.update(changes)
        return self.scheduler.save(payload)

    def tasks(self):
        return {row['id']: row for row in self.scheduler.listing()['tasks']}

    def seed_runs(self, count=1, *, task_id=None, name='Synthetic run', body='Synthetic prompt',
                  output='Synthetic output', status='succeeded'):
        task_id = task_id or uuid.uuid4().hex
        ids = []
        with self.scheduler.db() as db:
            for number in range(count):
                run_id = uuid.uuid4().hex
                ids.append(run_id)
                snapshot = {'id': task_id, 'revision': 1, 'name': name, 'body': body,
                            'kind': 'script', 'cwd': self.temporary.name}
                db.execute('INSERT INTO runs(id,task_id,snapshot,trigger,status,started_at,finished_at,exit_code,output) '
                           'VALUES(?,?,?,?,?,?,?,?,?)', (run_id, task_id, json.dumps(snapshot), 'manual', status,
                            '2026-10-05T00:00:00+00:00', None if status == 'running' else '2026-10-05T00:01:00+00:00',
                            None if status == 'running' else 0, output))
        return ids

    def configure_models(self):
        config = {'model': 'synthetic-qwen', 'endpoint': 'http://127.0.0.1:1/v1/chat/completions',
                  'api_key': 'synthetic-model-secret'}
        self.scheduler.assistant = SimpleNamespace(
            model_document=lambda: {'profiles': [{'id': 'model-one', 'config': config}]},
            settings=lambda: {'profiles': [{'profile_id': 'model-one', 'display_name': 'Local Qwen',
                                            'model': 'synthetic-qwen', 'configured': True}],
                              'active_profile_id': 'model-one'})

    def test_listing_exposes_scheduler_capacity_health_and_script_executor(self):
        saved = self.task()
        self.seed_runs(task_id=saved['id'], status='running')
        self.scheduler.last_tick = 'synthetic-tick'
        self.scheduler.last_error = 'synthetic-error'
        listing = self.scheduler.listing()
        self.assertEqual(listing['concurrency'], {'max': 2, 'active': 1, 'available': 1})
        self.assertEqual(listing['scheduler_health'],
                         {'running': False, 'last_tick': 'synthetic-tick', 'last_error': 'synthetic-error'})
        task = listing['tasks'][0]
        self.assertTrue(task['running'])
        self.assertEqual(task['executor_name'], 'Bash 脚本')
        self.assertEqual(task['model_name'], '')
        for old_field in ('body', 'cwd', 'enabled', 'next_run', 'last_run', 'revision'):
            self.assertIn(old_field, task)
        self.scheduler.thread = SimpleNamespace(is_alive=lambda: True, join=lambda **kwargs: None)
        self.assertTrue(self.scheduler.listing()['scheduler_health']['running'])
        self.scheduler.stop_event.set()
        self.assertFalse(self.scheduler.listing()['scheduler_health']['running'])

    def test_listing_legacy_pi_defaults_and_profile_name_do_not_reveal_secrets(self):
        self.configure_models()
        saved = self.task(kind='pi', profile_id='model-one')
        with self.scheduler.db() as db:
            row = db.execute('SELECT data FROM tasks WHERE id=?', (saved['id'],)).fetchone()
            stored = json.loads(row['data'])
            stored.pop('assistant_id', None)
            stored.pop('tool_groups', None)
            db.execute('UPDATE tasks SET data=? WHERE id=?', (json.dumps(stored), saved['id']))
        task = self.tasks()[saved['id']]
        self.assertEqual(task['assistant_id'], 'isolated')
        self.assertEqual(task['tool_groups'], [])
        self.assertEqual(task['executor_name'], '独立 Pi')
        self.assertEqual(task['model_name'], 'Local Qwen')
        self.assertNotIn('synthetic-model-secret', json.dumps(self.scheduler.listing()))
        self.scheduler.assistant.settings = lambda: {'profiles': []}
        self.assertEqual(self.tasks()[saved['id']]['model_name'], '模型配置已移除')

    def test_listing_displays_workbench_and_specialized_executors(self):
        self.configure_models()
        workbench = self.task(kind='pi', profile_id='model-one')
        wechat = self.task(kind='wechat_clues', profile_id='model-one')
        # Seed projection data independently of the validator implemented elsewhere.
        with self.scheduler.db() as db:
            row = db.execute('SELECT data FROM tasks WHERE id=?', (workbench['id'],)).fetchone()
            data = json.loads(row['data'])
            data.update(assistant_id='workbench', tool_groups=['memory'])
            db.execute('UPDATE tasks SET data=? WHERE id=?', (json.dumps(data), workbench['id']))
        tasks = self.tasks()
        self.assertEqual(tasks[workbench['id']]['executor_name'], '工作台助手（Pi）')
        self.assertEqual(tasks[workbench['id']]['tool_groups'], ['memory'])
        self.assertEqual(tasks[wechat['id']]['executor_name'], '微信线索分析（Pi）')

    def test_run_pagination_counts_all_rows_and_retains_task_only_call(self):
        task_id = uuid.uuid4().hex
        ids = self.seed_runs(105, task_id=task_id)
        first = self.scheduler.runs(task_id)
        self.assertEqual((first['total'], first['page'], first['page_size']), (105, 1, 50))
        self.assertEqual(len(first['runs']), 50)
        self.assertTrue(first['has_more'])
        self.assertEqual(first['runs'][0]['id'], ids[-1])
        second = self.scheduler.runs(task_id, page=2)
        third = self.scheduler.runs(task_id, page=3)
        self.assertTrue(second['has_more'])
        self.assertEqual(len(third['runs']), 5)
        self.assertFalse(third['has_more'])
        self.assertFalse(self.scheduler.runs(task_id, page=99)['has_more'])
        self.assertEqual(self.scheduler.runs(task_id, page=99)['runs'], [])

    def test_run_search_matches_name_prompt_and_output_with_combined_filters(self):
        first = uuid.uuid4().hex
        second = uuid.uuid4().hex
        expected = []
        expected += self.seed_runs(task_id=first, name='Project 上海', status='failed')
        expected += self.seed_runs(task_id=first, body='上海 prompt', status='failed')
        expected += self.seed_runs(task_id=first, output='上海 result', status='failed')
        self.seed_runs(task_id=first, name='上海 successful', status='succeeded')
        self.seed_runs(task_id=second, name='上海 other task', status='failed')
        result = self.scheduler.runs(first, q='上海', status='failed', page=2, page_size=2)
        self.assertEqual(result['total'], 3)
        self.assertEqual([row['id'] for row in result['runs']], expected[:1])
        self.assertFalse(result['has_more'])
        self.assertEqual(self.scheduler.runs(q='project')['total'], 1)

    def test_run_search_is_literal_and_parameterized(self):
        literal = r'100%_done\final'
        match = self.seed_runs(output=literal)
        self.seed_runs(output='100xxdone-final')
        self.assertEqual([row['id'] for row in self.scheduler.runs(q=literal)['runs']], match)
        self.assertEqual(self.scheduler.runs(q="' OR 1=1 --")['total'], 0)

    def test_run_filters_reject_unknown_status_and_invalid_pagination(self):
        for changes in ({'status': 'unknown'}, {'status': []}, {'page': 0}, {'page': True},
                        {'page': 1.5}, {'page': 10 ** 100}, {'page_size': 0}, {'page_size': 101},
                        {'page_size': False}, {'q': 'x' * 501}, {'q': 'line\nline'}, {'task_id': '../private'}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.scheduler.runs(**changes)

    def test_batch_resume_pause_updates_all_revisions_without_launching(self):
        first = self.task()
        second = self.task()
        with patch.object(self.scheduler, 'claim') as claim:
            resumed = self.scheduler.batch({'action': 'resume', 'tasks': [first, second]})
            self.assertEqual(resumed['updated'], 2)
            self.assertEqual({task['revision'] for task in resumed['tasks']}, {2})
            self.assertTrue(all(task['enabled'] and task['next_run'] for task in resumed['tasks']))
            selected = [{'id': task['id'], 'revision': task['revision']} for task in resumed['tasks']]
            paused = self.scheduler.batch({'action': 'pause', 'tasks': selected})
            self.assertEqual({task['revision'] for task in paused['tasks']}, {3})
            self.assertTrue(all(not task['enabled'] and task['next_run'] is None for task in paused['tasks']))
            claim.assert_not_called()
        self.assertEqual(self.scheduler.runs()['total'], 0)

    def test_batch_version_missing_and_expiry_errors_leave_every_task_unchanged(self):
        first = self.task()
        second = self.task()
        expired = self.task(ends_at='2020-01-01T00:00:00+08:00')
        for bad in ({'id': second['id'], 'revision': second['revision'] + 1},
                    {'id': '0' * 32, 'revision': 1}, expired):
            before = self.tasks()
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.scheduler.batch({'action': 'resume', 'tasks': [first, bad]})
            after = self.tasks()
            self.assertEqual(before, after)

    def test_batch_database_failure_rolls_back_previously_updated_rows(self):
        first = self.task()
        second = self.task()
        with self.scheduler.db() as db:
            db.execute(f"CREATE TRIGGER synthetic_update_failure BEFORE UPDATE ON tasks "
                       f"WHEN NEW.id='{second['id']}' BEGIN SELECT RAISE(ABORT,'synthetic-failure'); END")
        before = self.tasks()
        with self.assertRaises(sqlite3.IntegrityError):
            self.scheduler.batch({'action': 'resume', 'tasks': [first, second]})
        self.assertEqual(before, self.tasks())

    def test_batch_rejects_non_toggle_actions_duplicates_and_unbounded_selection(self):
        saved = self.task()
        cases = [None, {}, {'action': 'run', 'tasks': [saved]}, {'action': 'delete', 'tasks': [saved]},
                 {'action': 'pause', 'tasks': []}, {'action': 'pause', 'tasks': [saved, saved]},
                 {'action': 'pause', 'tasks': [saved] * 101},
                 {'action': 'pause', 'tasks': [{'id': saved['id'], 'revision': True}]},
                 {'action': 'pause', 'tasks': [{'id': saved['id']}]},
                 {'action': 'pause', 'tasks': [saved], 'allow_commands': True}]
        before = self.tasks()
        with patch.object(self.scheduler, 'claim') as claim:
            for payload in cases:
                with self.subTest(payload=payload), self.assertRaises(ValueError):
                    self.scheduler.batch(payload)
            claim.assert_not_called()
        self.assertEqual(before, self.tasks())


if __name__ == '__main__':
    unittest.main()
