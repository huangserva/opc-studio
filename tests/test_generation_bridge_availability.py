import sqlite3
import errno
import sys
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from generation import bridge


class AvailabilityTests(unittest.TestCase):
    def test_stopped_services_do_not_block_but_live_h3_queue_does(self):
        connection = MagicMock()
        connection.__enter__.return_value.execute.return_value.fetchall.return_value = []

        def probe(port, route, payload=None):
            if port in (8188, 8191):
                raise urllib.error.URLError(ConnectionRefusedError(errno.ECONNREFUSED, 'refused'))
            return {'queue_running': [], 'queue_pending': []}

        real_connect = sqlite3.connect
        def connect(database, *args, **kwargs):
            if str(database) == 'file:/home/admin01/.h3storyboard/h3storyboard.db?mode=ro':
                return connection
            return real_connect(database, *args, **kwargs)

        with patch.object(bridge.sqlite3, 'connect', side_effect=connect), patch.object(bridge, 'request', side_effect=probe):
            with sqlite3.connect(':memory:') as unrelated:
                self.assertEqual(unrelated.execute('SELECT 1').fetchone()[0], 1)
            self.assertEqual(bridge.ensure_idle(), [8190, 8192])
        with patch.object(bridge.sqlite3, 'connect', side_effect=connect), patch.object(bridge, 'request', return_value={'queue_running': [1]}):
            with self.assertRaisesRegex(ValueError, '已有生成任务'):
                bridge.ensure_idle()

    def test_unknown_service_state_still_blocks(self):
        for reason in (TimeoutError('timeout'), OSError(errno.ECONNRESET, 'reset')):
            with self.subTest(reason=reason), patch.object(bridge, 'request', side_effect=urllib.error.URLError(reason)):
                with self.assertRaises(urllib.error.URLError):
                    bridge.optional_service_request(8190, '/queue')

    def test_target_submission_is_required_and_only_live_peers_are_freed(self):
        with patch.object(bridge, 'ensure_idle', return_value=[8190, 8192]), patch.object(bridge, 'request', return_value={'prompt_id': 'test'}) as request:
            result = bridge.main({'action': 'submit', 'model': 'qi21', 'workflow': {}, 'client_id': 'test'})
            self.assertEqual(result['prompt_id'], 'test')
            self.assertEqual([call.args[:2] for call in request.call_args_list], [(8190, '/free'), (8192, '/prompt')])
        with patch.object(bridge, 'ensure_idle', return_value=[]), patch.object(bridge, 'request', side_effect=urllib.error.URLError(ConnectionRefusedError(errno.ECONNREFUSED, 'refused'))):
            with self.assertRaises(urllib.error.URLError):
                bridge.main({'action': 'submit', 'model': 'qi21', 'workflow': {}, 'client_id': 'test'})


if __name__ == '__main__':
    unittest.main()
