import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from isolation_patches import module_time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from assistant_memory import MemoryTools
from assistant_memos import MemosTools
from assistant_ssh import SSHRemote
from scheduled_pi import GROUPS, ScheduledPiTools, allowed_tools, schemas, validate_scope
from scheduled_tasks import ScheduledTasks


class ScheduledPiToolsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.config = {'model': 'synthetic', 'endpoint': 'http://127.0.0.1:1/v1/chat/completions',
                       'api_key': 'synthetic-key', 'timeout_seconds': 5}
        self.assistant = SimpleNamespace(
            model_document=lambda: {'profiles': [{'id': 'fake', 'config': self.config}]},
            settings=lambda: {'profiles': []}, ssh=SSHRemote(),
            knowledge_tools=SimpleNamespace(execute=Mock(return_value={'summary': 'synthetic knowledge'})),
            execute=Mock(return_value={'summary': 'synthetic work'}))
        memory = MemoryTools(self.tmp.name)
        memory.remio = SimpleNamespace(timeout=30, status=lambda **kw: {'ok': True, 'connected': True},
                                       search=lambda args, job: {'ok': True, 'notes': [], 'summary': 'synthetic remio'})
        memory.memos = SimpleNamespace(timeout=12, sensitive_values=(), status=lambda: {'available': True},
                                       search=lambda query, limit: {'items': [], 'semantic': False})
        self.assistant.memory_tools = memory
        self.assistant.web = SimpleNamespace(timeout=45, search=Mock(return_value={'summary': 'synthetic web'}))
        self.scheduler = ScheduledTasks(self.tmp.name, self.assistant, autostart=False)
        self.task = dict(name='合成工具任务', kind='pi', body='处理合成资料', cwd=self.tmp.name,
                         frequency='interval', minutes=60, at='08:00', weekdays=list(range(7)), ends_at=None,
                         timeout=15, enabled=False, profile_id='fake', allow_commands=False,
                         assistant_id='workbench', tool_groups=['memory', 'ssh', 'workspace', 'web'])
        self.cancel = threading.Event()

    def tearDown(self):
        self.scheduler.close()
        self.tmp.cleanup()

    def bridge(self, task=None):
        return ScheduledPiTools(self.scheduler, task or self.task, self.cancel, time.monotonic() + 10)

    def job(self, task=None):
        return {'id': 'synthetic', 'conversation_id': 'synthetic', 'model_config': self.config,
                'context': {'schedule': {'name': '合成工具任务'}}, 'cancel': False,
                'policy': {'allowed_tools': allowed_tools(task or self.task)}, 'read_versions': {}, 'read_blocks': {}}

    def test_saved_permissions_and_legacy_defaults(self):
        result = self.scheduler.save(self.task)
        saved = self.scheduler.listing()['tasks'][0]
        self.assertEqual(saved['tool_groups'], sorted(self.task['tool_groups']))
        saved['name'] = '改名称'
        self.scheduler.save(saved)
        self.assertEqual(self.scheduler.listing()['tasks'][0]['tool_groups'], saved['tool_groups'])
        legacy = {key: value for key, value in self.task.items() if key not in ('assistant_id', 'tool_groups')}
        self.scheduler.save(legacy)
        self.assertEqual(self.scheduler.listing()['tasks'][0]['assistant_id'], 'isolated')
        self.assertEqual(self.scheduler.listing()['tasks'][0]['tool_groups'], [])
        run = self.scheduler.claim(result['id'], 'manual', launch=False)['id']
        snapshot = self.scheduler.get_run(run)['snapshot']
        self.assertEqual(snapshot['authorized_tools'], allowed_tools(saved))
        self.assertNotIn('synthetic-key', json.dumps(snapshot))

    def test_invalid_or_implicit_scope_is_rejected(self):
        invalid = [dict(assistant_id='isolated', tool_groups=['memory']),
                   dict(tool_groups=['memory', 'memory']), dict(tool_groups=['write']),
                   dict(tool_groups='memory'), dict(tool_groups=[False]),
                   dict(kind='script', tool_groups=['ssh']), dict(assistant_id='unknown')]
        for change in invalid:
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.scheduler.save({**self.task, **change})
        self.assertEqual(validate_scope({}, 'pi'), {'assistant_id': 'isolated', 'tool_groups': []})

    def test_schema_and_backend_both_deny_unselected_tools(self):
        restricted = {**self.task, 'tool_groups': ['memory']}
        self.assertEqual({tool['function']['name'] for tool in schemas(restricted)}, GROUPS['memory'])
        bridge = self.bridge(restricted)
        job = self.job(restricted)
        for name in ('memos_write', 'remio_create_note', 'update_text', 'generate_media', 'run_command', 'ssh_run_readonly'):
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, '未授权'):
                bridge.execute(name, {}, job)
        job['policy']['allowed_tools'] = []
        with self.assertRaisesRegex(ValueError, '未授权'):
            bridge.execute('memos_status', {}, job)
        self.assistant.execute.assert_not_called()

    def test_dedicated_dispatch_and_per_run_provider_limits(self):
        bridge, job = self.bridge(), self.job()
        self.assertTrue(bridge.execute('remio_status', {}, job)['connected'])
        self.assertTrue(bridge.execute('memos_status', {}, job)['available'])
        self.assertFalse(bridge.execute('memos_search', {'query': 'synthetic'}, job)['semantic'])
        bridge.execute('read_knowledge', {'id': 'synthetic'}, job)
        self.assistant.knowledge_tools.execute.assert_called_once()
        self.assistant.execute.assert_not_called()
        bridge.execute('web_search', {'query': 'synthetic public'}, job)
        self.assistant.web.search.assert_called_once()
        self.assertEqual(self.assistant.web.timeout, 45)
        self.assertEqual(self.assistant.memory_tools.memos.timeout, 12)
        self.assertNotIn('preferences', bridge.execute('get_workspace_context', {}, job))

    def test_ssh_only_uses_validated_command_and_can_cancel(self):
        bridge, job = self.bridge(), self.job()
        process_result = dict(exit_code=0, stdout='synthetic', stderr='', timed_out=False,
                              connection_failed=False, truncated=False)
        with patch.object(SSHRemote, '_run_process', return_value=process_result) as process:
            result = bridge.execute('ssh_run_readonly', {'command': 'nvidia-smi'}, job)
            self.assertEqual(result['stdout'], 'synthetic')
            self.assertEqual(process.call_args.args[0][-2:], ['newgpu', 'nvidia-smi'])
            self.assertIn('cancel_check', process.call_args.kwargs)
            with self.assertRaises(ValueError):
                bridge.execute('ssh_run_readonly', {'command': 'touch forbidden'}, job)
            self.assertEqual(process.call_count, 1)
        self.cancel.set()
        with self.assertRaisesRegex(ValueError, '已停止'):
            bridge.execute('ssh_connection_status', {}, job)

    def test_native_pi_protocol_calls_scoped_tools_without_parent_chat(self):
        from pi_runtime import PiRuntime
        original = PiRuntime.__init__
        observed = []
        def inject(runtime, owner):
            original(runtime, owner)
            responses = [
                {'role': 'assistant', 'content': None, 'tool_calls': [
                    {'id': 'remio-probe', 'type': 'function', 'function': {'name': 'remio_status', 'arguments': '{}'}},
                    {'id': 'memos-probe', 'type': 'function', 'function': {'name': 'memos_status', 'arguments': '{}'}}]},
                {'role': 'assistant', 'content': '合成工具完成 synthetic-key'}]
            def model(messages, **kwargs):
                observed.append(copy.deepcopy(messages))
                return responses.pop(0)
            owner.client = model
        restricted = {**self.task, 'tool_groups': ['memory']}
        saved = self.scheduler.save(restricted)
        with patch('pi_runtime.PiRuntime.__init__', inject):
            run = self.scheduler.control(saved['id'], 'run')['id']
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                result = self.scheduler.get_run(run)
                if result['status'] != 'running': break
                time.sleep(.05)
        self.assertEqual(result['status'], 'succeeded', result['output'])
        self.assertIn('合成工具完成', result['output'])
        self.assertIn('remio_status', result['output'])
        self.assertIn('memos_status', result['output'])
        self.assertNotIn('synthetic-key', result['output'])
        self.assertEqual([message['role'] for message in observed[0]], ['system', 'user'])
        self.assertNotIn('read_chat_image', str(observed[0]))

    def test_stop_and_timeout_terminate_ssh_read_process(self):
        original = subprocess.Popen
        processes = []
        def capture(*args, **kwargs):
            process = original(*args, **kwargs)
            processes.append(process)
            return process
        started = time.monotonic()
        with patch('assistant_ssh.subprocess.Popen', capture), self.assertRaisesRegex(ValueError, '已停止'):
            SSHRemote._run_process(['/bin/sleep', '30'], 30, 100,
                                   cancel_check=lambda: time.monotonic() - started > .1)
        self.assertLess(time.monotonic() - started, 2)
        self.assertIsNotNone(processes[0].poll())

    def test_initialization_stop_and_timeout_keep_correct_run_status(self):
        for mode in ('cancelled', 'timeout'):
            with self.subTest(mode=mode):
                saved = self.scheduler.save(self.task)
                run = self.scheduler.claim(saved['id'], 'manual', launch=False)['id']
                cancel = self.scheduler.active[run]
                clock = [100.0]
                def fail(*args):
                    if mode == 'cancelled': cancel.set()
                    else: clock[0] = 200.0
                    raise ValueError('合成初始化失败')
                with patch.object(self.scheduler, 'pi', fail), module_time('scheduled_tasks', monotonic=lambda: clock[0]):
                    self.scheduler.execute(run, self.task, cancel, self.config)
                self.assertEqual(self.scheduler.get_run(run)['status'], mode)
        self.cancel.set()
        with patch('pi_runtime.PiRuntime.__init__') as initializer, self.assertRaisesRegex(ValueError, '已停止'):
            self.scheduler.pi('a'*32, self.task, self.cancel, self.config, time.monotonic()+10)
        initializer.assert_not_called()

    def test_scheduled_preparation_obeys_deadline_and_kills_process(self):
        from pi_runtime import PiRuntime
        original = subprocess.Popen
        processes = []
        owner = SimpleNamespace(_schedule_deadline=time.monotonic()+.15, _schedule_cancel=threading.Event())
        def fake_prepare(*args, **kwargs):
            process = original(['/bin/sleep', '30'], **kwargs)
            processes.append(process)
            return process
        started = time.monotonic()
        with patch('pi_runtime.subprocess.Popen', fake_prepare):
            runtime = PiRuntime(owner)
        self.assertFalse(runtime.prepared)
        self.assertLess(time.monotonic()-started, 2)
        self.assertIsNotNone(processes[0].poll())

    def test_stop_terminates_readonly_memos_process(self):
        memos = object.__new__(MemosTools)
        memos._ready = lambda: True
        memos.db_path = Path(self.tmp.name) / 'synthetic.db'
        memos.plugin_dir = Path(self.tmp.name)
        memos.workspace = Path(self.tmp.name)
        memos.node = 'synthetic-node'
        memos.owners, memos.sensitive_values, memos.timeout = (), (), 12
        started = time.monotonic()
        memos.cancel_check = lambda: time.monotonic() - started > .1
        original = subprocess.Popen
        processes = []
        def fake_process(*args, **kwargs):
            process = original(['/bin/sleep', '30'], **kwargs)
            processes.append(process)
            return process
        with patch('assistant_memos.subprocess.Popen', fake_process), self.assertRaisesRegex(ValueError, '已停止'):
            memos._call('status')
        self.assertLess(time.monotonic() - started, 2)
        self.assertIsNotNone(processes[0].poll())


if __name__ == '__main__':
    unittest.main()
