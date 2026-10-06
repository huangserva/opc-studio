"""The live workbench exposes fixed SSH status and truthful tool capabilities."""
import json
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from types import SimpleNamespace
from unittest.mock import Mock
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from server import Handler, Store


class AssistantSshApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.server.store = Store(self.temp.name)
        self.connection = {'transport': 'ssh', 'command': 'true', 'exit_code': 255,
                           'connection_failed': True, 'stdout': '',
                           'summary': 'SSH 连接失败，无法确认远端命令执行'}
        self.server.assistant = SimpleNamespace(
            capabilities=Mock(return_value={'ssh': {'tools': ['ssh_connection_status', 'ssh_run_readonly']}}),
            ssh=SimpleNamespace(status=Mock(return_value=self.connection), execute=Mock()))
        self.base = 'http://127.0.0.1:' + str(self.server.server_port)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)
        self.temp.cleanup()

    def get(self, path, host=None):
        request = Request(self.base + path, headers={'Host': host} if host else {})
        with urlopen(request, timeout=3) as response:
            return json.load(response)

    def test_status_runs_fixed_probe_and_keeps_real_connection_failure(self):
        self.assertEqual(self.get('/api/assistant/ssh'), self.connection)
        self.server.assistant.ssh.status.assert_called_once_with()
        self.server.assistant.ssh.execute.assert_not_called()

    def test_static_capabilities_never_probe_ssh(self):
        result = self.get('/api/assistant/capabilities?kind=video')
        self.assertIn('ssh_run_readonly', result['ssh']['tools'])
        self.server.assistant.capabilities.assert_called_once_with('video')
        self.server.assistant.ssh.status.assert_not_called()

    def test_query_parameters_cannot_change_connection_or_execute_a_command(self):
        for path in ('/api/assistant/ssh?host=ali-jump', '/api/assistant/ssh?command=whoami',
                     '/api/assistant/ssh?port=2222',
                     '/api/assistant/capabilities?host=other',
                     '/api/assistant/capabilities?kind=all&kind=video'):
            with self.subTest(path=path), self.assertRaises(HTTPError) as failure:
                self.get(path)
            self.assertEqual(failure.exception.code, 400)
            failure.exception.close()
        self.server.assistant.ssh.status.assert_not_called()
        self.server.assistant.ssh.execute.assert_not_called()

    def test_foreign_host_is_rejected_before_ssh(self):
        with self.assertRaises(HTTPError) as failure:
            self.get('/api/assistant/ssh', host='other.example')
        self.assertEqual(failure.exception.code, 403)
        failure.exception.close()
        self.server.assistant.ssh.status.assert_not_called()


if __name__ == '__main__':
    unittest.main()
