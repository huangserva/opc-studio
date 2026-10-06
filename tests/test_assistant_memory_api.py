"""Fixed memory health probes cannot turn HTTP input into a search or command."""
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


class MemoryApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.server.store = Store(self.temp.name)
        self.observed = {'execution_host': 'Mac', 'remio': {'connected': True},
                         'memos': {'available': False, 'summary': 'fixture unavailable'}}
        self.server.assistant = SimpleNamespace(memory_tools=SimpleNamespace(status=Mock(return_value=self.observed)))
        self.base = 'http://127.0.0.1:' + str(self.server.server_port)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)
        self.temp.cleanup()

    def get(self, path, host=None):
        with urlopen(Request(self.base + path, headers={'Host': host} if host else {}), timeout=3) as r:
            return json.load(r)

    def test_status_preserves_each_provider_state(self):
        self.assertEqual(self.get('/api/assistant/memory'), self.observed)
        self.server.assistant.memory_tools.status.assert_called_once_with()

    def test_queries_and_foreign_host_cannot_change_the_probe(self):
        for path, host, expected in [('/api/assistant/memory?query=private', None, 400),
                                     ('/api/assistant/memory?command=whoami', None, 400),
                                     ('/api/assistant/memory?url=http://other', None, 400),
                                     ('/api/assistant/memory', 'other.example', 403)]:
            with self.subTest(path=path), self.assertRaises(HTTPError) as failure:
                self.get(path, host)
            self.assertEqual(failure.exception.code, expected)
            failure.exception.close()
        self.server.assistant.memory_tools.status.assert_not_called()


if __name__ == '__main__':
    unittest.main()
