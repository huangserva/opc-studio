"""Read-only deployment routes do not accept remote addresses or arbitrary actions."""
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


class BackendQueryApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.server.store = Store(self.temp.name)
        self.result = {'read_only': True, 'checked_at': '2026-10-05T00:00:00+00:00',
                       'services': {'h3': {'status': 'unavailable', 'connected': False}}}
        self.server.generation = SimpleNamespace(
            backend_health=Mock(return_value=self.result),
            backend_inventory=Mock(return_value=self.result), start=Mock())
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

    def test_health_all_inventory_single_and_failure_response_are_read_only(self):
        self.assertEqual(self.get('/api/generation/backend-health'), self.result)
        self.server.generation.backend_health.assert_called_once_with(None)
        self.assertEqual(self.get('/api/generation/backend-inventory?model=h3'), self.result)
        self.server.generation.backend_inventory.assert_called_once_with('h3')
        self.server.generation.start.assert_not_called()

    def test_remote_address_action_and_invalid_model_parameters_are_rejected(self):
        for query in ('model=../../etc/passwd', 'model=all', 'model=', 'model=h3&model=qi21',
                      'host=example.com', 'port=18088', 'url=http://example.com',
                      'model=h3&action=submit', 'model=h3&path=', 'command=whoami'):
            with self.subTest(query=query), self.assertRaises(HTTPError) as failure:
                self.get('/api/generation/backend-inventory?' + query)
            self.assertEqual(failure.exception.code, 400)
            failure.exception.close()
        self.server.generation.backend_inventory.assert_not_called()
        self.server.generation.backend_health.assert_not_called()
        self.server.generation.start.assert_not_called()

    def test_nonlocal_host_is_rejected_before_any_query(self):
        with self.assertRaises(HTTPError) as failure:
            self.get('/api/generation/backend-health', host='example.com')
        self.assertEqual(failure.exception.code, 403)
        failure.exception.close()
        self.server.generation.backend_health.assert_not_called()


if __name__ == '__main__':
    unittest.main()
