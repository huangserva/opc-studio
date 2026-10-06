"""Actual pipe/timeout behavior with a mock SSH child; no remote connections."""
import os
from pathlib import Path
import shlex
import subprocess
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from assistant_ssh import COMMANDS, SSHRemote, validate_command


class SSHValidationTests(unittest.TestCase):
    def test_practical_readonly_commands_have_exact_preserved_arguments(self):
        commands = [
            'nvidia-smi', 'nvidia-smi -L',
            'nvidia-smi --query-gpu=name,memory.total,memory.used --format=csv,noheader,nounits',
            'nvidia-smi --query-compute-apps=pid,process_name,used_memory --format=csv',
            'ps aux', 'ps -ef', 'ps -eo pid,comm,args --sort=-rss',
            'ls -lah /home/studio/models', 'pwd', 'uname -a', 'df -h',
            'du -sh /home/studio/models', 'free -h', 'uptime -p',
            "stat --format='%n %s' /home/studio/models/model.safetensors",
            "head -n 30 '/var/log/gpu service.log'", 'tail -n200 /var/log/h3.log',
            'cat /etc/os-release', 'readlink -f /home/studio/models/current',
            'which nvidia-smi python3', 'rg -n -i error /var/log/h3.log',
            "find /home/studio/models -maxdepth 2 -type f -name '*.safetensors' -print",
            "find /home/studio/models -type f -name '*刀斧手*' -print",
            "find /home/studio/models -iname '*社交媒体大片*' -print",
            'systemctl --no-pager status h3.service', 'systemctl is-active h3.service',
            'systemctl is-enabled h3.service', 'systemctl show h3.service -p MainPID,ActiveState',
            'systemctl list-units --type=service', 'systemctl list-unit-files',
            "journalctl -u h3.service -n 80 --no-pager --since '2026-10-05 08:00'",
        ]
        for command in commands:
            with self.subTest(command=command):
                argv = validate_command(command)
                self.assertEqual(argv, shlex.split(command))
                self.assertEqual(shlex.split(shlex.join(argv)), argv)

    def test_execution_shell_and_mutating_options_are_rejected(self):
        commands = [
            'ls; uptime', 'ls | cat', 'cat /etc/os-release > /tmp/out',
            'ls && pwd', 'ls $(pwd)', 'ls `pwd`', 'ls $HOME', 'ls\npwd',
            'sudo ls', 'python3 -c pass', 'sh -c ls', 'env ls', './script.sh',
            '/tmp/ls', 'ssh otherhost ls', 'curl http://example.com',
            'nvidia-smi --gpu-reset', 'nvidia-smi -pl 100', 'nvidia-smi -pm 1',
            'nvidia-smi --query-gpu=name --format=csv -f /tmp/out', 'nvidia-smi -l 1',
            'systemctl restart h3', 'systemctl stop h3', 'systemctl status --host=other',
            'systemctl show h3', 'systemctl show h3 -p Environment',
            'journalctl --rotate', 'journalctl --vacuum-time=1s', 'journalctl --flush',
            'journalctl --update-catalog', 'journalctl -f', 'tail -f /var/log/h3.log',
            'tail -n +1 /var/log/h3.log', 'head -c -1 /var/log/h3.log',
            'find /tmp -delete', 'find /tmp -exec touch /tmp/out +',
            'find -- /tmp -exec touch /tmp/out +', 'find /tmp -execdir true +',
            'find /tmp -fprint /tmp/out', 'find /tmp -fprintf /tmp/out xx',
            'rg --pre touch error /var/log/h3.log', 'rg --passthru error /var/log/h3.log',
            'rg -z error /var/log/h3.log', 'rg --search-zip error /var/log/h3.log',
            'rg --file=/tmp/patterns /var/log/h3.log', 'rg --hidden error /home/studio',
        ]
        for command in commands:
            with self.subTest(command=command), self.assertRaises(ValueError):
                validate_command(command)

    def test_explicit_credential_special_and_binary_paths_are_rejected(self):
        paths = ['/home/studio/.ssh/config', '/home/studio/.aws/config',
                 '/srv/.env', '/srv/.envrc', '/srv/.env.production', '/srv/secrets/models.json',
                 '/srv/token.json', '/srv/api_key.txt', '/srv/id_rsa',
                 '/srv/private.pem', '/etc/../etc/shadow', '/etc/ssh/ssh_host_ed25519_key', '/home/studio/.bash_history',
                 '/home/studio/.config/gcloud/configurations/default',
                 '/home/studio/.docker/config.json', '/dev/zero',
                 '/proc/123/fd/1', '/proc/self/environ', '/proc/123/mem']
        for path in paths:
            for command in ('ls', 'cat', 'head', 'tail', 'rg -n error'):
                with self.subTest(path=path, command=command), self.assertRaises(ValueError):
                    validate_command(command + ' ' + path)
        for command in ('cat', 'head', 'tail', 'rg error'):
            with self.subTest(command=command), self.assertRaises(ValueError):
                validate_command(command + ' /srv/models/model.gguf')
        self.assertEqual(validate_command('cat /srv/models/tokenizer.json')[0], 'cat')
        self.assertEqual(validate_command('ls /srv/models/model.gguf')[0], 'ls')

    def test_validations_never_start_ssh(self):
        remote = SSHRemote()
        with patch('assistant_ssh.subprocess.Popen') as spawn:
            for kwargs in ({'timeout_seconds': True}, {'timeout_seconds': 0},
                           {'timeout_seconds': 31}, {'timeout_seconds': 1.5},
                           {'max_output_chars': False}, {'max_output_chars': 16001},
                           {'max_output_chars': '20'}):
                with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                    remote.execute('ls', **kwargs)
            for command in (None, [], '', 'ls ' + 'a' * 4096, 'cat "unclosed'):
                with self.subTest(command=command), self.assertRaises(ValueError):
                    remote.execute(command)
            spawn.assert_not_called()

    def test_capabilities_are_static_and_host_is_not_a_command_parameter(self):
        with patch('assistant_ssh.subprocess.Popen') as spawn:
            self.assertEqual(SSHRemote.capabilities()['commands'], list(COMMANDS))
            self.assertTrue(SSHRemote.capabilities()['read_only'])
            spawn.assert_not_called()
        with patch.dict(os.environ, {'ARTICLE_STUDIO_GPU_HOST': '-oProxyCommand=anything'}):
            with self.assertRaises(ValueError):
                SSHRemote()


class SSHExecutionTests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(os.environ, {'ARTICLE_STUDIO_GPU_HOST': 'newgpu'})
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.remote = SSHRemote()
        self.real_popen = subprocess.Popen

    def run_child(self, script, command='ls /srv/models', **kwargs):
        calls = []

        def mock_ssh(argv, **options):
            calls.append((argv, options))
            # This child only writes synthetic data or sleeps; never call SSH.
            return self.real_popen([sys.executable, '-c', script], **options)

        with patch('assistant_ssh.subprocess.Popen', side_effect=mock_ssh):
            result = self.remote.execute(command, **kwargs)
        return result, calls

    def test_real_pipe_output_exit_and_remote_quoting_without_local_shell(self):
        command = "head -n 2 '/var/log/gpu service.log'"
        result, calls = self.run_child("import sys; print('synthetic output'); print('synthetic warning', file=sys.stderr)", command)
        self.assertEqual(result['stdout'], 'synthetic output\n')
        self.assertEqual(result['stderr'], 'synthetic warning\n')
        self.assertEqual(result['exit_code'], 0)
        self.assertFalse(result['connection_failed'])
        self.assertFalse(result['timed_out'])
        self.assertFalse(result['truncated'])
        self.assertEqual(result['command'], shlex.join(shlex.split(command)))
        argv, options = calls[0]
        self.assertEqual(argv[-2:], ['newgpu', result['command']])
        self.assertIn('BatchMode=yes', argv)
        self.assertIn('ConnectTimeout=8', argv)
        self.assertIn('ServerAliveInterval=5', argv)
        self.assertIn('StrictHostKeyChecking=yes', argv)
        self.assertFalse(options['shell'])
        self.assertEqual(options['stdin'], subprocess.DEVNULL)
        self.assertNotIn('env', options)
        self.assertEqual(result['transport'], 'ssh')
        self.assertTrue(result['checked_at'])
        self.assertIn('退出码 0', result['summary'])

    def test_large_dual_streams_are_bounded_while_draining_and_preserve_exit(self):
        script = "import os; os.write(1, b'x'*300000); os.write(2, b'y'*300000)"
        result, _ = self.run_child(script, max_output_chars=1234)
        self.assertLessEqual(len(result['stdout']) + len(result['stderr']), 1234)
        self.assertEqual(len(result['stdout']) + len(result['stderr']), 1234)
        self.assertTrue(result['truncated'])
        self.assertFalse(result['timed_out'])
        self.assertEqual(result['exit_code'], 0)
        self.assertIn('输出已截断', result['summary'])

    def test_unicode_char_limit_counts_decoded_characters(self):
        script = "import os; data=('用户'*20000).encode(); [os.write(1, data[i:i+103]) for i in range(0,len(data),103)]"
        result, _ = self.run_child(script, max_output_chars=13)
        self.assertEqual(result['stdout'], ('用户' * 7)[:13])
        self.assertNotIn('\ufffd', result['stdout'])
        self.assertTrue(result['truncated'])

    def test_timeout_cannot_claim_command_finished(self):
        result, _ = self.run_child("import os,time; os.write(1,b'partial'); time.sleep(10)", timeout_seconds=1)
        self.assertTrue(result['timed_out'])
        self.assertIsNone(result['exit_code'])
        self.assertFalse(result['connection_failed'])
        self.assertEqual(result['stdout'], 'partial')
        self.assertIn('无法确认远端完成', result['summary'])

    def test_nonzero_command_and_ssh_connection_failure_are_distinct(self):
        for code, connection_failed in ((2, False), (255, True)):
            with self.subTest(code=code):
                result, _ = self.run_child(f"import sys; print('synthetic error',file=sys.stderr); sys.exit({code})")
                self.assertEqual(result['exit_code'], code)
                self.assertEqual(result['connection_failed'], connection_failed)
                self.assertIn('连接失败，命令未执行确认' if connection_failed else '退出码 2', result['summary'])

    def test_missing_client_is_an_explicit_connection_failure(self):
        with patch('assistant_ssh.subprocess.Popen', side_effect=FileNotFoundError('synthetic path')):
            result = self.remote.execute('ls', max_output_chars=4)
        self.assertTrue(result['connection_failed'])
        self.assertIsNone(result['exit_code'])
        self.assertLessEqual(len(result['stderr']), 4)
        self.assertTrue(result['truncated'])
        self.assertNotIn('synthetic path', result['stderr'])
        self.assertIn('连接失败', result['summary'])

    def test_status_is_a_real_fixed_probe_and_does_not_open_general_true_command(self):
        calls = []

        def mock_ssh(argv, **options):
            calls.append(argv)
            return self.real_popen([sys.executable, '-c', 'pass'], **options)

        with patch('assistant_ssh.subprocess.Popen', side_effect=mock_ssh):
            result = self.remote.status()
        self.assertEqual(calls[0][-2:], ['newgpu', 'true'])
        self.assertEqual(result['command'], 'true')
        self.assertTrue(result['connected'])
        with self.assertRaises(ValueError):
            self.remote.execute('true')


if __name__ == '__main__':
    unittest.main()
