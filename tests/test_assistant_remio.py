import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from isolation_patches import clock_sequence, module_time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import assistant_remio
from assistant_remio import DEFAULT_REMIO, OUTPUT_LIMIT, RAW_OUTPUT_LIMIT, RemioError, RemioTools


class CompletedProcess:
    def __init__(self, returncode=0):
        self.returncode = returncode

    def poll(self):
        return self.returncode


class RunningProcess:
    returncode = None

    def poll(self):
        return self.returncode

    def terminate(self):
        self.returncode = -15

    def kill(self):
        self.returncode = -9

    def wait(self, timeout=None):
        return self.returncode


class RemioToolsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.tools = RemioTools(self.temporary.name, command=DEFAULT_REMIO)
        self.job = {'cancel': False, 'conversation_id': 'chat-123',
                    'model_config': {'api_key': 'model-secret-key'}}

    def process_response(self, response, returncode=0):
        raw = response if isinstance(response, bytes) else json.dumps(response).encode()

        def launch(argv, **kwargs):
            kwargs['stdout'].write(raw)
            kwargs['stdout'].flush()
            return CompletedProcess(returncode)

        return launch

    def test_default_cli_does_not_depend_on_launchd_path(self):
        with patch.dict('assistant_remio.os.environ', {}, clear=True):
            tools = RemioTools(self.temporary.name)
        self.assertEqual(tools.command, DEFAULT_REMIO)
        tools.command = 'remio'
        self.assertFalse(tools.available())

    def test_status_reports_actual_probe_instead_of_installed_as_connected(self):
        with patch.object(self.tools, 'available', return_value=True), \
                patch.object(self.tools, '_call', return_value={'time': 'now'}) as call:
            result = self.tools.status({}, self.job)
        self.assertTrue(result['installed'])
        self.assertTrue(result['connected'])
        self.assertTrue(result['available'])
        self.assertFalse(result['query_performed'])
        self.assertEqual(call.call_args.args[0], ['syscall', 'get_local_time', '{}'])
        with patch.object(self.tools, 'available', return_value=True), \
                patch.object(self.tools, '_call', side_effect=RemioError('remio 服务调用失败', 'exit_error')):
            self.assertFalse(self.tools.status()['connected'])
        with patch.object(self.tools, 'available', return_value=False):
            self.assertEqual(self.tools.status()['connected'], False)

    def test_capabilities_never_claim_service_connection_without_probe(self):
        with patch.object(self.tools, 'available', return_value=True):
            result = self.tools.capabilities()
        self.assertTrue(result['installed'])
        self.assertNotIn('connected', result)

    def test_runner_uses_fixed_argv_no_shell_stdin_or_stderr(self):
        with patch.object(self.tools, 'available', return_value=True), \
                patch('assistant_remio.subprocess.Popen', side_effect=self.process_response(
                    {'ok': True, 'data': {'results': []}})) as launch:
            self.tools._call(['search_notes', '--query', '$(private-token)'], self.job)
        self.assertEqual(launch.call_args.args[0], [DEFAULT_REMIO, 'search_notes', '--query', '$(private-token)'])
        self.assertIs(launch.call_args.kwargs['shell'], False)
        self.assertEqual(launch.call_args.kwargs['stdin'], subprocess.DEVNULL)
        self.assertEqual(launch.call_args.kwargs['stderr'], subprocess.DEVNULL)

    def test_exit_failure_and_json_failure_are_not_empty_search(self):
        cases = [(7, {'ok': True, 'data': {'results': []}}),
                 (0, {'ok': False, 'error': 'private-provider-token https://secret-endpoint/'}),
                 (0, b'private-provider-token broken JSON'),
                 (0, {'ok': True, 'data': []})]
        for returncode, response in cases:
            with self.subTest(returncode=returncode, response=response), \
                    patch.object(self.tools, 'available', return_value=True), \
                    patch('assistant_remio.subprocess.Popen', side_effect=self.process_response(response, returncode)):
                with self.assertRaises(RemioError) as error:
                    self.tools._call(['search_notes'], dict(self.job))
                self.assertNotIn('private-provider-token', str(error.exception))
                self.assertNotIn('secret-endpoint', str(error.exception))

    def test_launch_errors_do_not_expose_exception_or_credentials(self):
        with patch.object(self.tools, 'available', return_value=True), \
                patch('assistant_remio.subprocess.Popen', side_effect=OSError('token-secret /private/path')):
            with self.assertRaises(RemioError) as error:
                self.tools._call(['search_notes'], self.job)
        self.assertNotIn('token-secret', str(error.exception))
        self.assertNotIn('/private/path', str(error.exception))

    def test_raw_response_size_and_call_limits_are_bounded(self):
        with patch.object(self.tools, 'available', return_value=True), \
                patch('assistant_remio.subprocess.Popen', side_effect=self.process_response(b'x' * (RAW_OUTPUT_LIMIT + 1))):
            with self.assertRaisesRegex(RemioError, '过大'):
                self.tools._call(['read_note'], self.job)
        self.job['remio_calls'] = 12
        with patch.object(self.tools, 'available', return_value=True), patch('assistant_remio.subprocess.Popen') as launch:
            with self.assertRaisesRegex(RemioError, '上限'):
                self.tools._call(['search_notes'], self.job)
            launch.assert_not_called()

    def test_cancellation_and_timeout_terminate_running_cli(self):
        for cancelled in (True, False):
            process = RunningProcess()
            job = dict(self.job)

            def launch(argv, **kwargs):
                if cancelled:
                    job['cancel'] = True
                return process

            with self.subTest(cancelled=cancelled), patch.object(self.tools, 'available', return_value=True), \
                    patch('assistant_remio.subprocess.Popen', side_effect=launch), \
                    module_time('assistant_remio', monotonic=clock_sequence([100, 200])):
                with self.assertRaises(RemioError) as error:
                    self.tools._call(['search_notes'], job)
            self.assertEqual(error.exception.code, 'cancelled' if cancelled else 'timeout')
            self.assertEqual(process.returncode, -15)

    def test_search_has_no_note_type_filter_and_keeps_actual_note_links(self):
        data = {'total': 2, 'query_search_method': 'semantic', 'results': [
            {'noteId': 'mail-id', 'title': 'Mail', 'noteType': 'mail', 'preview': 'Related mail'},
            {'noteId': 'file-id', 'title': 'File', 'noteType': 'file', 'preview': 'Related file'}]}
        with patch.object(self.tools, '_call', return_value=data) as call:
            result = self.tools.search({'query': 'Harness', 'limit': 8}, self.job)
        self.assertEqual(call.call_args.args[0], ['search_notes', '--query', 'Harness', '--limit', '8'])
        self.assertEqual(result['returned'], 2)
        self.assertTrue(result['ok'])
        self.assertTrue(result['available'])
        self.assertTrue(result['query_performed'])
        self.assertEqual(result['results'][0]['url'], 'http://127.0.0.1:17624/notes/mail-id')
        self.assertEqual(result['sources'][1]['noteId'], 'file-id')

    def test_empty_search_is_success_but_failed_semantic_search_is_error(self):
        with patch.object(self.tools, '_call', return_value={'results': [], 'total': 0}):
            result = self.tools.search({'query': 'narrow query'}, self.job)
        self.assertTrue(result['empty'])
        self.assertEqual(result['returned'], 0)
        with patch.object(self.tools, '_call', return_value={'results': [], 'query_search_method': 'failed'}):
            with self.assertRaisesRegex(RemioError, '不能将此结果视为没有资料'):
                self.tools.search({'query': 'narrow query'}, self.job)

    def test_search_output_budget_and_known_key_redaction(self):
        data = {'results': [{'noteId': 'n' + str(index), 'title': 't' * 1000,
                             'preview': 'model-secret-key ' + '\\"' * 3000}
                            for index in range(8)]}
        with patch.object(self.tools, '_call', return_value=data):
            result = self.tools.search({'query': 'q' * 1200}, self.job)
        raw = json.dumps(result, ensure_ascii=False, separators=(',', ':'))
        self.assertLessEqual(len(raw), OUTPUT_LIMIT)
        self.assertNotIn('model-secret-key', raw)
        self.assertTrue(result['truncated'])

    def test_malformed_search_metadata_and_credential_ids_are_rejected(self):
        for data in ({'results': [{'noteId': 'note-1', 'preview': {'token': 'secret'}}]},
                     {'results': [{'noteId': 'model-secret-key', 'title': 'A note'}]}):
            with self.subTest(data=data), patch.object(self.tools, '_call', return_value=data):
                with self.assertRaises(ValueError) as error:
                    self.tools.search({'query': 'q'}, self.job)
                self.assertNotIn('model-secret-key', str(error.exception))
                self.assertNotIn("'token'", str(error.exception))

    def test_read_passes_line_pagination_to_real_cli_and_bounds_return(self):
        data = {'noteId': 'note-1', 'title': 'A note', 'content': 'line\n' * 80}
        with patch.object(self.tools, '_call', return_value=data) as call:
            result = self.tools.read({'note_id': 'note-1', 'offset': 21, 'limit': 80}, self.job)
        self.assertEqual(call.call_args.args[0], ['read_note', 'note-1', '--format', 'md', '--offset', '21', '--limit', '80'])
        self.assertTrue(result['truncated'])
        self.assertEqual(result['next_offset'], 101)
        self.assertEqual(result['lines_returned'], 80)

    def test_read_does_not_skip_a_truncated_long_line(self):
        with patch.object(self.tools, '_call', return_value={'noteId': 'note-1', 'content': 'x' * 20000}):
            result = self.tools.read({'note_id': 'note-1', 'max_chars': 4000}, self.job)
        self.assertEqual(len(result['content']), 4000)
        self.assertTrue(result['partial_line'])
        self.assertIsNone(result['next_offset'])
        self.assertTrue(result['truncated'])

    def test_read_preserves_next_complete_line_and_json_budget(self):
        with patch.object(self.tools, '_call', return_value={'noteId': 'note-1', 'content': 'first\n' + 'x' * 9000}):
            result = self.tools.read({'note_id': 'note-1', 'max_chars': 50}, self.job)
        self.assertEqual(result['content'], 'first\n')
        self.assertEqual(result['next_offset'], 2)
        with patch.object(self.tools, '_call', return_value={'noteId': 'note-1', 'content': '\\"' * 10000}):
            result = self.tools.read({'note_id': 'note-1', 'max_chars': 8000}, self.job)
        self.assertLessEqual(len(json.dumps(result, ensure_ascii=False, separators=(',', ':'))), OUTPUT_LIMIT)

    def test_invalid_parameters_fail_before_starting_cli(self):
        cases = [('search', {'query': ''}), ('search', {'query': 'q', 'limit': -1}),
                 ('search', {'query': 'q', 'limit': True}), ('search', {'query': 'q', 'type': 'note'}),
                 ('read', {'note_id': '--help'}), ('read', {'note_id': 'n', 'offset': 0}),
                 ('read', {'note_id': 'n', 'limit': 201}), ('read', {'note_id': 'n', 'max_chars': 8001}),
                 ('create', {'title': 't', 'content': 'body'}),
                 ('create', {'title': 't', 'content': 'body', 'request_id': '--help'}),
                 ('update', {'note_id': 'n', 'append_text': '', 'request_id': 'r'}),
                 ('update', {'note_id': 'n', 'content': 'body', 'request_id': 'r'}),
                 ('update', {'note_id': 'n', 'append_text': '---\nnoteId: n\n---\nbody', 'request_id': 'r'})]
        with patch.object(self.tools, '_call') as call:
            for method, args in cases:
                with self.subTest(method=method, args=args), self.assertRaises(ValueError):
                    getattr(self.tools, method)(args, self.job)
            call.assert_not_called()

    def test_model_key_is_not_sent_to_remio(self):
        with patch.object(self.tools, '_call') as call:
            with self.assertRaisesRegex(ValueError, '密钥'):
                self.tools.search({'query': 'find model-secret-key'}, self.job)
            with self.assertRaisesRegex(ValueError, '密钥'):
                self.tools.create({'title': 't', 'content': 'model-secret-key', 'request_id': 'r'}, self.job)
            call.assert_not_called()

    def test_writes_require_session_and_request_and_reject_duplicate_h1(self):
        args = {'title': 'Title', 'content': 'body', 'request_id': 'r'}
        with patch.object(self.tools, '_call') as call:
            with self.assertRaisesRegex(ValueError, '稳定会话'):
                self.tools.create(args, {'model_config': {}})
            with self.assertRaisesRegex(ValueError, 'H1'):
                self.tools.create({**args, 'content': '# Title\n\nbody'}, self.job)
            call.assert_not_called()
        self.assertFalse(self.tools.request_db.exists())

    def test_create_uses_documented_session_context_and_persistent_dedup(self):
        args = {'title': 'Title', 'content': 'very-secret-note-content', 'request_id': 'save-1'}
        with patch.object(self.tools, '_call', return_value={'noteId': 'new-note', 'title': 'Title'}) as call:
            first = self.tools.create(args, self.job)
            repeat = self.tools.create(args, self.job)
        self.assertFalse(first['deduplicated'])
        self.assertTrue(first['ok'])
        self.assertFalse(first['query_performed'])
        self.assertTrue(repeat['deduplicated'])
        self.assertEqual(call.call_count, 1)
        argv = call.call_args.args[0]
        self.assertEqual(argv[:2], ['syscall', 'create_note'])
        self.assertEqual(json.loads(argv[2]), {'title': 'Title', 'content': args['content']})
        self.assertEqual(argv[3:], ['--client', 'article-studio-pi', '--client-session-id', 'chat-123'])
        restarted = RemioTools(self.temporary.name)
        with patch.object(restarted, '_call') as restarted_call:
            self.assertEqual(restarted.create(args, self.job)['noteId'], 'new-note')
            restarted_call.assert_not_called()
        self.assertNotIn(b'very-secret-note-content', self.tools.request_db.read_bytes())
        self.assertEqual(self.tools.request_db.stat().st_mode & 0o777, 0o600)

    def test_same_request_id_cannot_write_different_content(self):
        args = {'title': 'Title', 'content': 'body', 'request_id': 'save-1'}
        with patch.object(self.tools, '_call', return_value={'noteId': 'new-note'}) as call:
            self.tools.create(args, self.job)
            with self.assertRaisesRegex(ValueError, '不同内容'):
                self.tools.create({**args, 'content': 'different'}, self.job)
        self.assertEqual(call.call_count, 1)

    def test_uncertain_mutation_is_not_retried_after_timeout_or_restart(self):
        args = {'title': 'Title', 'content': 'body', 'request_id': 'save-1'}
        with patch.object(self.tools, '_call', side_effect=RemioError('remio 响应超时', 'timeout')) as call:
            with self.assertRaisesRegex(RemioError, '超时'):
                self.tools.create(args, self.job)
            with self.assertRaisesRegex(RemioError, '阻止重复提交'):
                self.tools.create(args, self.job)
        self.assertEqual(call.call_count, 1)
        restarted = RemioTools(self.temporary.name)
        with patch.object(restarted, '_call') as call:
            with self.assertRaisesRegex(RemioError, '阻止重复提交'):
                restarted.create(args, self.job)
            call.assert_not_called()

    def test_update_payload_never_selects_arbitrary_operation(self):
        args = {'note_id': 'note-1', 'append_text': 'New finding', 'request_id': 'update-1'}
        with patch.object(self.tools, '_call', return_value={'noteId': 'note-1', 'title': 'A note'}) as call:
            result = self.tools.update(args, self.job)
        self.assertEqual(result['noteId'], 'note-1')
        self.assertEqual(call.call_args.args[0][:2], ['syscall', 'update_note'])
        self.assertEqual(json.loads(call.call_args.args[0][2]), {'noteId': 'note-1', 'append': 'New finding'})

    def test_wrong_mutation_response_leaves_request_blocked(self):
        args = {'note_id': 'note-1', 'append_text': 'body', 'request_id': 'update-1'}
        with patch.object(self.tools, '_call', return_value={'noteId': 'wrong-note'}) as call:
            with self.assertRaisesRegex(RemioError, '不匹配'):
                self.tools.update(args, self.job)
            with self.assertRaisesRegex(RemioError, '阻止重复提交'):
                self.tools.update(args, self.job)
        self.assertEqual(call.call_count, 1)

    def test_unavailable_dedup_storage_prevents_any_external_write(self):
        self.tools.workspace = Path(self.temporary.name) / 'occupied'
        self.tools.workspace.write_text('file')
        self.tools.request_db = self.tools.workspace / 'remio-requests.sqlite3'
        with patch.object(self.tools, '_call') as call:
            with self.assertRaisesRegex(RemioError, '已停止写入'):
                self.tools.create({'title': 't', 'content': 'body', 'request_id': 'r'}, self.job)
            call.assert_not_called()


class RemioDefaultPathTests(unittest.TestCase):
    """默认 CLI 路径必须可移植：PATH 上的 remio 优先，否则回退到家目录。"""

    def default_with_env(self, env):
        snapshot = dict(assistant_remio.__dict__)
        try:
            with patch.dict('assistant_remio.os.environ', env, clear=True):
                if 'HOME' in env:
                    # check_offline 会全局 patch.object(Path, 'home', ...)；重算时按 env 的 HOME 覆盖，
                    # 保证本类不依赖外部对 Path.home 的固定替身。HOME 缺省时不 patch。
                    with patch.object(Path, 'home', return_value=Path(env['HOME'])):
                        importlib.reload(assistant_remio)
                        return assistant_remio.DEFAULT_REMIO
                importlib.reload(assistant_remio)
                return assistant_remio.DEFAULT_REMIO
        finally:
            assistant_remio.__dict__.update(snapshot)

    def test_executable_on_path_is_preferred_over_home_fallback(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            installed = home / 'bin' / 'remio'
            installed.parent.mkdir(parents=True)
            installed.write_text('#!/bin/sh\nexit 0\n')
            installed.chmod(0o755)
            self.assertEqual(self.default_with_env({'PATH': str(installed.parent), 'HOME': str(home)}),
                             str(installed))

    def test_path_without_executable_falls_back_to_home_local_bin(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            self.assertEqual(self.default_with_env({'PATH': str(home / 'empty'), 'HOME': str(home)}),
                             str(home / '.local' / 'bin' / 'remio'))

    def test_constructor_priority_is_argument_then_environment_then_default(self):
        with tempfile.TemporaryDirectory() as temporary:
            with patch.dict('assistant_remio.os.environ', {'ARTICLE_STUDIO_REMIO': '/opt/remio/remio'}, clear=True):
                self.assertEqual(RemioTools(temporary).command, '/opt/remio/remio')
                self.assertEqual(RemioTools(temporary, command='/bin/echo').command, '/bin/echo')
            with patch.dict('assistant_remio.os.environ', {}, clear=True):
                self.assertEqual(RemioTools(temporary).command, DEFAULT_REMIO)


if __name__ == '__main__':
    unittest.main()
