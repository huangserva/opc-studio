"""B2-b3: structural feedback integrity and a versioned read/write snapshot."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('b2b3_api', Path(__file__).resolve().parents[1] / 'radar_page_api.py')
api = importlib.util.module_from_spec(spec)
spec.loader.exec_module(api)


class FeedbackIntegrityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='b2b3-api-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.path = self.root / 'twitter-radar/monitor/feedback.json'
        self.path.parent.mkdir(parents=True)

    def save(self, verdict='worth'):
        return api.save_feedback(self.root, {'tweet_id': '101', 'verdict': verdict})

    def test_missing_file_is_writable(self):
        self.assertEqual(api.load_feedback(self.root), ({}, 'missing'))
        self.assertFalse(self.path.exists())
        self.save()
        self.assertEqual(json.loads(self.path.read_text())['101']['verdict'], 'worth')

    def test_empty_object_is_writable(self):
        self.path.write_text('{}')
        self.assertEqual(api.load_feedback(self.root), ({}, 'ok'))
        self.save()
        self.assertEqual(json.loads(self.path.read_text())['101']['verdict'], 'worth')

    def test_invalid_scalar_verdict_is_unreviewed_not_corruption(self):
        for verdict in ['yes', 'WORTH', 1, True, None]:
            with self.subTest(verdict=verdict):
                self.path.write_text(json.dumps({'202': {'verdict': verdict}}))
                records, status = api.load_feedback(self.root)
                self.assertEqual(status, 'ok')
                self.assertIsNone(records['202']['verdict'])
                self.save()
                self.assertIn('202', json.loads(self.path.read_text()))

    def test_nested_known_fields_are_corrupt_without_mutation(self):
        for field in ['verdict', 'updated_at', 'hit', 'pushed']:
            for value in [[{'nested': []}], {'deep': {'x': []}}]:
                with self.subTest(field=field, value=value):
                    self.path.write_text(json.dumps({'202': {field: value}}))
                    before, inode = self.path.read_bytes(), self.path.stat().st_ino
                    self.assertEqual(api.load_feedback(self.root), ({}, 'invalid'))
                    with self.assertRaisesRegex(api.FeedbackCorrupt, '反馈文件损坏，已停止写入'):
                        self.save()
                    self.assertEqual(self.path.read_bytes(), before)
                    self.assertEqual(self.path.stat().st_ino, inode)
                    self.assertEqual(list(self.path.parent.glob('feedback.json.*')), [])

    def test_revision_tracks_read_write_delete_and_external_repair(self):
        def version(value):
            epoch, n = value.split(':')
            self.assertEqual(len(epoch), 32)
            return epoch, int(n)
        initial = api.monitor_view(self.root)['feedback_revision']
        first = self.save()['feedback_revision']
        after_read = api.monitor_view(self.root)['feedback_revision']
        removed = self.save(None)['feedback_revision']
        self.path.write_text('{bad')
        corrupt = api.monitor_view(self.root)['feedback_revision']
        self.path.write_text('{}')
        repaired = api.monitor_view(self.root)['feedback_revision']
        values = [version(v) for v in [initial, first, after_read, removed, corrupt, repaired]]
        self.assertEqual(len({e for e, _ in values}), 1)
        self.assertLess(values[0][1], values[1][1])
        self.assertEqual(values[1], values[2])
        self.assertLess(values[2][1], values[3][1])
        self.assertLess(values[3][1], values[4][1])
        self.assertLess(values[4][1], values[5][1])
        self.assertEqual(json.loads(self.path.read_text()), {})
        self.save()
        self.assertNotIn('feedback_revision', json.loads(self.path.read_text())['101'])

    def test_deep_json_is_invalid_without_disconnect(self):
        self.path.write_text('{"202":' + '[' * 5000 + '0' + ']' * 5000 + '}')
        before = self.path.read_bytes()
        self.assertEqual(api.load_feedback(self.root), ({}, 'invalid'))
        with self.assertRaises(api.FeedbackCorrupt):
            self.save()
        self.assertEqual(self.path.read_bytes(), before)


if __name__ == '__main__':
    unittest.main(verbosity=2)
