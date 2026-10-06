"""Assistant routing for real SSH queries, with every remote action mocked."""
import copy
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from assistant_agent import Assistant, LABELS, SYSTEM, TOOLS
from assistant_policy import READ_TOOLS, policy_for


class AssistantSSHTests(unittest.TestCase):
    def setUp(self):
        self.agent = Assistant.__new__(Assistant)
        self.agent.ssh = Mock(spec=['status', 'execute', 'capabilities'])
        self.declared = {'transport': 'ssh', 'host_alias': 'newgpu', 'read_only': True,
                         'commands': ['nvidia-smi', 'ps'], 'restrictions': ['no shell operators'],
                         'limits': {'timeout_seconds': 30, 'max_output_chars': 16000}}
        self.agent.ssh.capabilities.return_value = copy.deepcopy(self.declared)
        self.agent.web = SimpleNamespace(capabilities=lambda: {})
        self.agent.memory_tools = SimpleNamespace(capabilities=lambda: {})
        self.agent.image_skill = SimpleNamespace(status=lambda: {})
        self.agent.media_preferences = Mock(return_value={'image': {'model': 'qi21'}, 'video': {'model': 'h3'}})
        self.job = {'cancel': False, 'context': {'unsaved': True},
                    'policy': policy_for('请通过 SSH 查 newgpu 的进程、显存和项目日志')}

    def result(self, **updates):
        return {'checked_at': '2026-10-05T10:30:00+00:00', 'transport': 'ssh', 'host_alias': 'newgpu',
                'command': 'nvidia-smi', 'exit_code': 0, 'stdout': 'mock GPU output', 'stderr': '',
                'timed_out': False, 'truncated': False, 'connection_failed': False,
                'summary': 'mock SSH query completed', **updates}

    def test_query_tools_are_registered_without_remote_mutation_grants(self):
        schemas = {tool['function']['name']: tool['function'] for tool in TOOLS}
        for name in ('ssh_connection_status', 'ssh_run_readonly'):
            self.assertIn(name, READ_TOOLS)
            self.assertIn(name, LABELS)
            self.assertIn(name, self.job['policy']['allowed_tools'])
            self.assertFalse(schemas[name]['parameters']['additionalProperties'])
        status = schemas['ssh_connection_status']['parameters']
        self.assertEqual(status['properties'], {})
        self.assertEqual(status['required'], [])
        command = schemas['ssh_run_readonly']['parameters']
        self.assertEqual(command['required'], ['command'])
        self.assertEqual(set(command['properties']), {'command', 'timeout_seconds', 'max_output_chars'})
        self.assertEqual(command['properties']['command']['maxLength'], 4096)
        self.assertEqual(command['properties']['timeout_seconds']['maximum'], 30)
        self.assertEqual(command['properties']['max_output_chars']['maximum'], 16000)
        self.assertNotIn('generate_media', self.job['policy']['allowed_tools'])
        self.assertFalse(any(name.startswith('ssh_') and name not in READ_TOOLS for name in schemas))

    def test_connection_status_calls_the_real_backend_entry_without_generation(self):
        observed = self.result(command='true', connected=True)
        self.agent.ssh.status.return_value = observed
        self.assertEqual(self.agent.execute('ssh_connection_status', {}, self.job), observed)
        self.agent.ssh.status.assert_called_once_with()
        self.agent.ssh.execute.assert_not_called()

    def test_command_reaches_backend_with_exact_text_and_bounded_defaults(self):
        command = 'nvidia-smi --query-gpu=memory.used --format=csv'
        observed = self.result(command=command)
        self.agent.ssh.execute.return_value = observed
        self.assertEqual(self.agent.execute('ssh_run_readonly', {'command': command}, self.job), observed)
        self.agent.ssh.execute.assert_called_once_with(command=command, timeout_seconds=20, max_output_chars=12000)
        self.agent.ssh.status.assert_not_called()

    def test_explicit_limits_are_forwarded_without_casting(self):
        self.agent.ssh.execute.return_value = self.result(command='ps -eo pid,comm')
        self.agent.execute('ssh_run_readonly', {'command': 'ps -eo pid,comm', 'timeout_seconds': 30,
                                              'max_output_chars': 16000}, self.job)
        self.agent.ssh.execute.assert_called_once_with(command='ps -eo pid,comm', timeout_seconds=30,
                                                       max_output_chars=16000)

    def test_unknown_host_credentials_or_access_mode_never_reach_backend(self):
        for field, value in (('host', 'other'), ('port', 22), ('user', 'root'), ('password', 'mock-secret'),
                             ('private_key', '/private/key'), ('read_only', False), ('shell', True),
                             ('operation', 'restart')):
            args = {'command': 'nvidia-smi', field: value}
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.agent.execute('ssh_run_readonly', args, self.job)
        for args in ({'host': 'newgpu'}, {'command': 'true'}, None, []):
            with self.subTest(status_args=args), self.assertRaises(ValueError):
                self.agent.execute('ssh_connection_status', args, self.job)
        self.agent.ssh.execute.assert_not_called()
        self.agent.ssh.status.assert_not_called()

    def test_invalid_commands_and_numeric_types_are_rejected_before_execution(self):
        invalid = [{}, {'command': ''}, {'command': ' \t'}, {'command': None}, {'command': ['nvidia-smi']},
                   {'command': 'a' * 4097}, None, []]
        for key, values in (('timeout_seconds', (0, 31, True, 20.0, '20', None)),
                            ('max_output_chars', (0, 16001, True, 12000.0, '12000', None))):
            invalid.extend({'command': 'nvidia-smi', key: value} for value in values)
        for args in invalid:
            with self.subTest(args=args), self.assertRaises(ValueError):
                self.agent.execute('ssh_run_readonly', args, self.job)
        self.agent.ssh.execute.assert_not_called()

    def test_backend_validation_error_is_not_converted_into_fake_ssh_output(self):
        self.agent.ssh.execute.side_effect = ValueError('SSH 仅支持后端列出的只读命令，不允许管道')
        with self.assertRaisesRegex(ValueError, '不允许管道'):
            self.agent.execute('ssh_run_readonly', {'command': 'ps -ef | head'}, self.job)
        self.agent.ssh.execute.assert_called_once_with(command='ps -ef | head', timeout_seconds=20,
                                                       max_output_chars=12000)

    def test_connection_and_remote_command_failures_are_returned_as_facts(self):
        scenarios = [self.result(command='true', exit_code=255, stdout='', stderr='Connection refused',
                                 connection_failed=True, connected=False),
                     self.result(exit_code=None, stdout='partial', stderr='', timed_out=True, truncated=True),
                     self.result(exit_code=1, stdout='', stderr='GPU query failed', connection_failed=False)]
        for observed in scenarios:
            with self.subTest(observed=observed):
                self.agent.ssh.execute.return_value = observed
                returned = self.agent.execute('ssh_run_readonly', {'command': 'nvidia-smi'}, self.job)
                self.assertEqual(returned, observed)
                self.assertEqual(returned['exit_code'], observed['exit_code'])
                self.assertEqual(returned['stderr'], observed['stderr'])
                self.assertEqual(returned['connection_failed'], observed['connection_failed'])
        self.agent.ssh.status.return_value = scenarios[0]
        self.assertFalse(self.agent.execute('ssh_connection_status', {}, self.job)['connected'])

    def test_current_message_policy_and_cancel_still_gate_ssh(self):
        jobs = [{**self.job, 'cancel': True}, {**self.job, 'policy': {'allowed_tools': []}},
                {**self.job, 'policy': policy_for('你好')}]
        for job in jobs:
            for name, args in (('ssh_connection_status', {}), ('ssh_run_readonly', {'command': 'nvidia-smi'})):
                with self.subTest(job=job, name=name), self.assertRaises(ValueError):
                    self.agent.execute(name, args, job)
        self.agent.ssh.execute.assert_not_called()
        self.agent.ssh.status.assert_not_called()

    def test_static_capabilities_report_backend_scope_without_testing_connection(self):
        result = self.agent.capabilities('video')
        ssh = result['ssh']
        self.assertEqual(ssh['transport'], 'ssh')
        self.assertEqual(ssh['host_alias'], 'newgpu')
        self.assertEqual(ssh['commands'], self.declared['commands'])
        self.assertEqual(ssh['restrictions'], self.declared['restrictions'])
        self.assertFalse(ssh['connection_verified'])
        self.assertEqual(ssh['tools'], ['ssh_connection_status', 'ssh_run_readonly'])
        self.assertIn('未测试 SSH 连接', ssh['note'])
        self.agent.ssh.capabilities.assert_called_once_with()
        self.agent.ssh.status.assert_not_called()
        self.agent.ssh.execute.assert_not_called()
        ssh['commands'].clear()
        self.assertEqual(self.agent.ssh.capabilities.return_value['commands'], ['nvidia-smi', 'ps'])

    def test_system_separates_ssh_from_fixed_service_inventory_and_remote_operations(self):
        self.assertIn('不是通用 SSH 命令工具', SYSTEM)
        self.assertIn('先调用 ssh_connection_status', SYSTEM)
        self.assertIn('调用 ssh_run_readonly', SYSTEM)
        self.assertIn('不能据此', self.agent.capabilities('video')['ssh']['note'])
        self.assertIn('当前 SSH 工具不支持启停、卸载、修改、安装或部署', SYSTEM)
        self.assertNotIn('不执行系统命令、不访问凭据', SYSTEM)


if __name__ == '__main__':
    unittest.main()
