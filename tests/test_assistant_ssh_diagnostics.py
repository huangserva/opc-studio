"""Fixed, read-only ProxyJump diagnosis; every local and remote action is mocked."""
import copy
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import call, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from assistant_ssh import SSHRemote


class SSHDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        environment = patch.dict(os.environ, {'ARTICLE_STUDIO_GPU_HOST': 'newgpu'})
        environment.start()
        self.addCleanup(environment.stop)
        self.remote = SSHRemote()
        self.route = {'jump_host_alias': 'ali-jump', 'forwarding_host': '127.0.0.1',
                      'forwarding_port': 2222}

    @staticmethod
    def probe(**updates):
        return {'checked_at': '2026-10-05T10:30:00+00:00', 'transport': 'ssh',
                'host_alias': 'newgpu', 'command': 'true', 'exit_code': 0,
                'stdout': '', 'stderr': '', 'timed_out': False, 'truncated': False,
                'connection_failed': False, 'summary': 'synthetic probe', **updates}

    def failed_gpu(self, **updates):
        return self.probe(exit_code=255, connection_failed=True,
                          stderr='channel 0: open failed: connect failed: Connection refused',
                          **updates)

    def diagnose(self, listener, gpu=None, jump=None):
        gpu = gpu if gpu is not None else self.failed_gpu()
        jump = jump if jump is not None else self.probe(host_alias='ali-jump')
        responses = [copy.deepcopy(item) for item in (gpu, jump, listener)]
        with patch.object(self.remote, '_resolved_route', return_value=copy.deepcopy(self.route)), \
                patch.object(self.remote, '_run', side_effect=responses) as run:
            result = self.remote.status()
        self.assertFalse(result['connected'])
        for key in ('exit_code', 'stdout', 'stderr', 'timed_out', 'connection_failed'):
            self.assertEqual(result[key], gpu[key], key)
        self.assertEqual(self.remote.host_alias, 'newgpu')
        self.assertEqual(run.call_args_list, [
            call(['true'], 10, 2000),
            call(['true'], 8, 2000, host_alias='ali-jump'),
            call(['ss', '-H', '-ltn', 'sport = :2222'], 8, 2000, host_alias='ali-jump'),
        ])
        diagnostic = result['diagnostic']
        for key, value in self.route.items():
            self.assertEqual(diagnostic[key], value)
        self.assertEqual(diagnostic['jump_probe'], jump)
        self.assertEqual(diagnostic['listener_probe'], listener)
        self.assertTrue(diagnostic['summary'])
        return diagnostic

    def test_successful_gpu_probe_never_runs_route_or_jump_diagnostics(self):
        with patch.object(self.remote, '_run', return_value=self.probe()) as run, \
                patch.object(self.remote, '_resolved_route') as resolve:
            result = self.remote.status()
        self.assertTrue(result['connected'])
        run.assert_called_once_with(['true'], 10, 2000)
        resolve.assert_not_called()
        self.assertNotIn('diagnostic', result)

    def test_unresolvable_route_preserves_gpu_failure_and_stops(self):
        gpu = self.failed_gpu()
        with patch.object(self.remote, '_run', return_value=copy.deepcopy(gpu)) as run, \
                patch.object(self.remote, '_resolved_route', side_effect=ValueError('unsupported route')):
            result = self.remote.status()
        self.assertFalse(result['connected'])
        self.assertEqual(result['stderr'], gpu['stderr'])
        self.assertEqual(result['exit_code'], 255)
        self.assertEqual(result['diagnostic']['stage'], 'route_unknown')
        self.assertTrue(result['diagnostic']['summary'])
        run.assert_called_once_with(['true'], 10, 2000)

    def test_failed_jump_does_not_probe_listeners_or_claim_gpu_connection(self):
        jump_failures = [self.probe(host_alias='ali-jump', exit_code=255,
                                    connection_failed=True, stderr='Permission denied'),
                         self.probe(host_alias='ali-jump', exit_code=None, timed_out=True)]
        for jump in jump_failures:
            with self.subTest(jump=jump), \
                    patch.object(self.remote, '_resolved_route', return_value=copy.deepcopy(self.route)), \
                    patch.object(self.remote, '_run', side_effect=[self.failed_gpu(), jump]) as run:
                result = self.remote.status()
            self.assertFalse(result['connected'])
            self.assertEqual(result['exit_code'], 255)
            self.assertEqual(result['diagnostic']['stage'], 'jump_unreachable')
            self.assertEqual(result['diagnostic']['jump_probe'], jump)
            self.assertIsNone(result['diagnostic']['port_listening'])
            self.assertEqual(run.call_count, 2)

    def test_empty_successful_ss_output_is_evidence_of_no_port_listener(self):
        diagnostic = self.diagnose(self.probe(host_alias='ali-jump', stdout=''))
        self.assertEqual(diagnostic['stage'], 'forwarding_port_not_listening')
        self.assertIs(diagnostic['port_listening'], False)
        self.assertEqual(diagnostic['listen_addresses'], [])

    def test_valid_listen_rows_keep_target_failure_and_report_observed_addresses(self):
        outputs = ['LISTEN 0 128 127.0.0.1:2222 0.0.0.0:*\n',
                   'LISTEN 0 128 [::1]:2222 [::]:*\n',
                   'LISTEN 0 128 0.0.0.0:2222 0.0.0.0:*\n',
                   'LISTEN 0 128 127.0.0.1:2222 0.0.0.0:*\n'
                   'LISTEN 0 128 [::1]:2222 [::]:*\n']
        for stdout in outputs:
            with self.subTest(stdout=stdout):
                diagnostic = self.diagnose(self.probe(host_alias='ali-jump', stdout=stdout))
            self.assertEqual(diagnostic['stage'], 'forwarding_port_listening_target_failed')
            self.assertIs(diagnostic['port_listening'], True)
            self.assertIsInstance(diagnostic['listen_addresses'], list)
            self.assertTrue(diagnostic['listen_addresses'])

    def test_gpu_timeout_also_runs_diagnostics_even_without_connection_failed_flag(self):
        gpu = self.probe(exit_code=None, timed_out=True, connection_failed=False)
        diagnostic = self.diagnose(self.probe(host_alias='ali-jump'), gpu=gpu)
        self.assertEqual(diagnostic['stage'], 'forwarding_port_not_listening')

    def test_listener_failure_timeout_or_truncation_never_becomes_missing_listener(self):
        scenarios = [self.probe(host_alias='ali-jump', exit_code=127, stderr='ss: command not found'),
                     self.probe(host_alias='ali-jump', exit_code=1, stderr='invalid filter'),
                     self.probe(host_alias='ali-jump', exit_code=255, connection_failed=True),
                     self.probe(host_alias='ali-jump', exit_code=None, timed_out=True),
                     self.probe(host_alias='ali-jump', truncated=True,
                                stdout='LISTEN 0 128 127.0.0.1:2222 0.0.0.0:*\n')]
        for listener in scenarios:
            with self.subTest(listener=listener):
                diagnostic = self.diagnose(listener)
            self.assertEqual(diagnostic['stage'], 'listener_unknown')
            self.assertIsNone(diagnostic['port_listening'])

    def test_remote_startup_banner_cannot_be_mistaken_for_listener(self):
        for stdout in ('Welcome to the GPU gateway\n', 'LISTEN fake output\n',
                       'LISTEN nonsense nonsense garbage:2222 bogus\n',
                       'LISTEN 0 128 garbage:2222 *:*\n',
                       'ESTAB 0 0 127.0.0.1:2222 127.0.0.1:12345\n'):
            with self.subTest(stdout=stdout):
                diagnostic = self.diagnose(self.probe(host_alias='ali-jump', stdout=stdout))
            self.assertEqual(diagnostic['stage'], 'listener_unknown')
            self.assertIsNone(diagnostic['port_listening'])
            self.assertEqual(diagnostic['listen_addresses'], [])

    def test_public_status_and_execute_do_not_accept_a_host(self):
        with patch.object(self.remote, '_run') as run:
            with self.assertRaises(TypeError):
                self.remote.status(host_alias='other')
            with self.assertRaises(TypeError):
                self.remote.execute('ls', host_alias='other')
            run.assert_not_called()

    def test_internal_jump_probe_does_not_mutate_default_gpu_host(self):
        observed = {key: self.probe()[key] for key in ('exit_code', 'stdout', 'stderr',
                                                     'timed_out', 'truncated', 'connection_failed')}
        with patch.object(self.remote, '_run_process', side_effect=lambda *args: copy.deepcopy(observed)) as process:
            jump = self.remote._run(['true'], 8, 2000, host_alias='ali-jump')
            gpu = self.remote._run(['true'], 10, 2000)
        self.assertEqual(self.remote.host_alias, 'newgpu')
        self.assertEqual(jump['host_alias'], 'ali-jump')
        self.assertEqual(gpu['host_alias'], 'newgpu')
        for actual, alias in zip(process.call_args_list, ('ali-jump', 'newgpu')):
            self.assertEqual(actual.args[0][-2:], [alias, 'true'])
            self.assertIn('BatchMode=yes', actual.args[0])
            self.assertIn('StrictHostKeyChecking=yes', actual.args[0])
            self.assertIn('ClearAllForwardings=yes', actual.args[0])


class SSHRouteResolutionTests(unittest.TestCase):
    def setUp(self):
        environment = patch.dict(os.environ, {'ARTICLE_STUDIO_GPU_HOST': 'newgpu'})
        environment.start()
        self.addCleanup(environment.stop)
        self.remote = SSHRemote()

    @staticmethod
    def config(host='127.0.0.1', port='2222', jump='ali-jump'):
        return f'host newgpu\nhostname {host}\nport {port}\nproxyjump {jump}\n'

    def resolve(self, stdout=None, **updates):
        observed = {'exit_code': 0, 'stdout': self.config() if stdout is None else stdout,
                    'stderr': '', 'timed_out': False, 'truncated': False,
                    'connection_failed': False, **updates}
        with patch.object(self.remote, '_run_process', return_value=observed) as process:
            result = self.remote._resolved_route()
        process.assert_called_once_with(['ssh', '-G', '--', 'newgpu'], 3, 16000)
        return result

    def test_resolves_only_safe_route_fields_without_exposing_full_ssh_config(self):
        raw = self.config() + 'user private-user\nidentityfile /private/secret/key\n'
        result = self.resolve(raw)
        self.assertEqual(result, {'jump_host_alias': 'ali-jump', 'forwarding_host': '127.0.0.1',
                                  'forwarding_port': 2222})
        self.assertNotIn('secret', str(result))
        self.assertNotIn('private-user', str(result))

    def test_ipv4_ipv6_and_localhost_targets_are_supported(self):
        for host in ('127.0.0.1', '::1', 'localhost'):
            with self.subTest(host=host):
                result = self.resolve(self.config(host=host))
            self.assertEqual(result['forwarding_host'], host)
            self.assertEqual(result['forwarding_port'], 2222)

    def test_remote_target_is_not_assumed_to_be_a_jump_port_forward(self):
        for host in ('gpu.example.com', '10.0.0.8', '0.0.0.0', '::', 'localhost.example.com'):
            with self.subTest(host=host), self.assertRaises(ValueError):
                self.resolve(self.config(host=host))

    def test_proxyjump_must_be_one_bare_configured_alias(self):
        for jump in ('none', 'newgpu', 'ali-jump,other', 'root@ali-jump', 'ali-jump:22',
                     '-oProxyCommand=anything', 'ali-jump;touch', '[::1]', ''):
            with self.subTest(jump=jump), self.assertRaises(ValueError):
                self.resolve(self.config(jump=jump))

    def test_forwarding_port_is_a_valid_bounded_integer(self):
        for port in ('0', '65536', '-1', '22x', '22;true', '', '1.5'):
            with self.subTest(port=port), self.assertRaises(ValueError):
                self.resolve(self.config(port=port))
        for port in ('1', '65535'):
            with self.subTest(port=port):
                self.assertEqual(self.resolve(self.config(port=port))['forwarding_port'], int(port))

    def test_missing_route_fields_are_not_inferred(self):
        for stdout in ('', 'hostname 127.0.0.1\nport 2222\n',
                       'port 2222\nproxyjump ali-jump\n',
                       'hostname 127.0.0.1\nproxyjump ali-jump\n'):
            with self.subTest(stdout=stdout), self.assertRaises(ValueError):
                self.resolve(stdout)

    def test_duplicate_route_fields_cannot_redirect_diagnostics(self):
        for extra in ('hostname 10.0.0.8\n', 'port 3333\n', 'proxyjump other\n'):
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                self.resolve(self.config() + extra)

    def test_config_failure_timeout_and_truncation_stop_resolution(self):
        for updates in ({'exit_code': 255}, {'exit_code': None, 'timed_out': True},
                        {'exit_code': None, 'connection_failed': True}, {'truncated': True}):
            with self.subTest(updates=updates), self.assertRaises(ValueError):
                self.resolve(**updates)


if __name__ == '__main__':
    unittest.main()
