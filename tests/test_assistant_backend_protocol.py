"""The Pi tool loop can query deployment and receive current H3 capabilities."""
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

from server import Store, assistant_module, content


class AssistantBackendProtocolTests(unittest.TestCase):
    def test_failed_gpu_and_successful_jump_diagnosis_remain_distinct_across_native_pi(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            config = root / 'model.json'
            config.write_text(json.dumps({'endpoint': 'https://example.invalid/chat/completions',
                                          'model': 'mock-chat', 'api_key': 'mock-key'}))
            assistant = assistant_module.Assistant(Store(root / 'workspace'), content, config)
            observed = {
                'checked_at': '2026-10-05T10:30:00+00:00', 'transport': 'ssh',
                'host_alias': 'newgpu', 'command': 'true', 'connected': False,
                'exit_code': 255, 'connection_failed': True, 'timed_out': False,
                'truncated': False, 'stdout': '', 'stderr': 'Connection refused',
                'summary': 'GPU SSH 连接失败；跳板已连通，转发端口未监听',
                'diagnostic': {
                    'jump_host_alias': 'ali-jump', 'forwarding_host': '127.0.0.1',
                    'forwarding_port': 2222, 'port_listening': False, 'listen_addresses': [],
                    'stage': 'forwarding_port_not_listening',
                    'summary': '跳板已连通；转发端口未监听',
                    'jump_probe': {'transport': 'ssh', 'host_alias': 'ali-jump',
                                   'command': 'true', 'exit_code': 0, 'stdout': '', 'stderr': '',
                                   'connection_failed': False, 'timed_out': False, 'truncated': False},
                    'listener_probe': {'transport': 'ssh', 'host_alias': 'ali-jump',
                                       'command': "ss -H -ltn 'sport = :2222'", 'exit_code': 0,
                                       'stdout': '', 'stderr': '', 'connection_failed': False,
                                       'timed_out': False, 'truncated': False},
                },
            }
            assistant.ssh.status = Mock(return_value=observed)
            assistant.ssh.execute = Mock()
            received = {}

            def fake_model(messages, **kwargs):
                # The actual Pi SDK and Python tool bridge produce these model messages.
                for message in messages:
                    if message.get('role') == 'tool':
                        received[message.get('tool_call_id')] = json.loads(message['content'])
                if 'ssh-diagnosis' not in received:
                    return {'role': 'assistant', 'content': None, 'tool_calls': [{
                        'id': 'ssh-diagnosis', 'type': 'function',
                        'function': {'name': 'ssh_connection_status', 'arguments': '{}'}}]}
                return {'role': 'assistant',
                        'content': 'newgpu 未连通；跳板已连通，转发端口未监听。'}

            assistant.client = fake_model
            with patch.object(assistant.ssh, '_run', side_effect=AssertionError('unexpected SSH network probe')):
                result = assistant.start({'message': '通过 SSH 检查 newgpu 连接失败的跳板和转发端口原因',
                                          'request_id': 'f' * 32})
                deadline = time.monotonic() + 10
                while assistant.active and time.monotonic() < deadline:
                    time.sleep(.02)
                if assistant.active:
                    assistant.stop(result['id'])
                job = assistant.public_job(result['id'])
            self.assertEqual(job['status'], 'done', job)
            self.assertEqual([action['name'] for action in job['actions']], ['ssh_connection_status'])
            assistant.ssh.status.assert_called_once_with()
            assistant.ssh.execute.assert_not_called()
            receipt = received['ssh-diagnosis']
            self.assertEqual(receipt, observed)
            self.assertFalse(receipt['connected'])
            self.assertEqual(receipt['exit_code'], 255)
            self.assertEqual(receipt['diagnostic']['jump_probe']['exit_code'], 0)
            self.assertFalse(receipt['diagnostic']['jump_probe']['connection_failed'])
            self.assertIs(receipt['diagnostic']['port_listening'], False)
            self.assertEqual(receipt['diagnostic']['stage'], 'forwarding_port_not_listening')
            self.assertIn('newgpu 未连通', job['answer'])

    def test_real_ssh_tool_names_and_command_output_cross_pi_protocol(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            config = root / 'model.json'
            config.write_text(json.dumps({'endpoint': 'https://example.invalid/chat/completions',
                                          'model': 'mock-chat', 'api_key': 'mock-key'}))
            assistant = assistant_module.Assistant(Store(root / 'workspace'), content, config)
            command = 'nvidia-smi --query-gpu=name,memory.total --format=csv,noheader'
            assistant.ssh.status = Mock(return_value={
                'transport': 'ssh', 'command': 'true', 'exit_code': 0,
                'connection_failed': False, 'timed_out': False, 'summary': 'SSH 连接测试退出码 0'})
            assistant.ssh.execute = Mock(return_value={
                'transport': 'ssh', 'command': command, 'exit_code': 0, 'stdout': 'fixture_GPU, 49140 MiB',
                'stderr': '', 'truncated': False, 'timed_out': False, 'summary': 'SSH 查询退出码 0'})
            calls = [('ssh_connection_status', {}), ('ssh_run_readonly', {'command': command})]
            received = {}

            def fake_model(messages, **kwargs):
                for message in messages:
                    if message.get('role') == 'tool':
                        received[message.get('tool_call_id')] = json.loads(message['content'])
                if calls:
                    name, arguments = calls.pop(0)
                    return {'role': 'assistant', 'content': None, 'tool_calls': [{
                        'id': name, 'type': 'function',
                        'function': {'name': name, 'arguments': json.dumps(arguments)}}]}
                return {'role': 'assistant', 'content': 'SSH 查询退出码 0，已收到远端输出。'}

            assistant.client = fake_model
            result = assistant.start({'message': '通过 SSH 查询 4090 的 GPU 型号和显存',
                                      'request_id': 'e' * 32})
            deadline = time.monotonic() + 10
            while assistant.active and time.monotonic() < deadline:
                time.sleep(.02)
            if assistant.active:
                assistant.stop(result['id'])
            job = assistant.public_job(result['id'])
            self.assertEqual(job['status'], 'done', job)
            self.assertEqual([action['name'] for action in job['actions']],
                             ['ssh_connection_status', 'ssh_run_readonly'])
            assistant.ssh.status.assert_called_once_with()
            assistant.ssh.execute.assert_called_once_with(command=command, timeout_seconds=20,
                                                         max_output_chars=12000)
            self.assertEqual(received['ssh_run_readonly']['stdout'], 'fixture_GPU, 49140 MiB')
            self.assertEqual(received['ssh_run_readonly']['exit_code'], 0)

    def test_read_only_deployment_tools_reach_model_without_any_generation(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            store = Store(root / 'workspace')
            config = root / 'model.json'
            config.write_text(json.dumps({'endpoint': 'https://example.invalid/chat/completions',
                                          'model': 'mock-chat', 'api_key': 'mock-key'}))
            assistant = assistant_module.Assistant(store, content, config)
            backend = Mock(spec=['backend_inventory', 'backend_health', 'start'])
            backend.backend_inventory.return_value = {
                'checked_at': '2026-10-05T00:00:00+00:00', 'read_only': True,
                'services': {'h3': {'status': 'partial', 'inventory': {'loras': {
                    'status': 'ok', 'files': ['example.safetensors']}}}}}
            backend.backend_health.return_value = {
                'checked_at': '2026-10-05T00:00:01+00:00', 'read_only': True,
                'services': {'h3': {'status': 'ok', 'queue': {'running': 0, 'pending': 1, 'busy': True}}},
                'inference_status': {'status': 'unavailable', 'state': {'loaded': None}}}
            assistant.generation = backend
            calls = [('get_backend_inventory', {'model': 'h3'}),
                     ('get_backend_health', {}), ('get_capabilities', {'kind': 'video'})]
            received = {}

            def fake_model(messages, **kwargs):
                # These messages cross the real Pi protocol; the transport is a local mock.
                for message in messages:
                    if message.get('role') == 'tool':
                        received[message.get('tool_call_id')] = json.loads(message['content'])
                if calls:
                    name, arguments = calls.pop(0)
                    return {'role': 'assistant', 'content': None, 'tool_calls': [{
                        'id': name, 'type': 'function',
                        'function': {'name': name, 'arguments': json.dumps(arguments)}}]}
                return {'role': 'assistant', 'content': '部署查询完成；加载状态未知。'}

            assistant.client = fake_model
            result = assistant.start({'message': '查询 H3 的实际 LoRA、4090 排队状态及工作台视频能力',
                                      'request_id': 'd' * 32})
            deadline = time.monotonic() + 10
            while assistant.active and time.monotonic() < deadline:
                time.sleep(.02)
            if assistant.active:
                assistant.stop(result['id'])
            job = assistant.public_job(result['id'])
            self.assertEqual(job['status'], 'done', job)
            self.assertEqual([action['name'] for action in job['actions']],
                             ['get_backend_inventory', 'get_backend_health', 'get_capabilities'])
            backend.backend_inventory.assert_called_once_with(model='h3')
            backend.backend_health.assert_called_once_with(model=None)
            backend.start.assert_not_called()
            self.assertEqual(received['get_backend_inventory']['services']['h3']['status'], 'partial')
            self.assertIsNone(received['get_backend_health']['inference_status']['state']['loaded'])
            h3 = received['get_capabilities']['models'][0]
            self.assertEqual({mode['id'] for mode in h3['workflows']},
                             {'t2v', 'i2v', 'fl2v', 'r2v', 'ir2v', 'flr2v'})


if __name__ == '__main__':
    unittest.main()
