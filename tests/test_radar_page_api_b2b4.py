"""Feedback-file limits protect the original file, including concurrent growth."""
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('b2b4_api', Path(__file__).resolve().parents[1] / 'radar_page_api.py')
api = importlib.util.module_from_spec(spec)
spec.loader.exec_module(api)


class FeedbackLimitsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='b2b4-limits-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.path = self.root / 'twitter-radar/monitor/feedback.json'
        self.path.parent.mkdir(parents=True)

    def assert_corrupt(self, raw):
        self.path.write_bytes(raw)
        inode = self.path.stat().st_ino
        self.assertEqual(api.load_feedback(self.root), ({}, 'invalid'))
        with self.assertRaises(api.FeedbackCorrupt):
            api.save_feedback(self.root, {'tweet_id': '777', 'verdict': 'worth'})
        self.assertEqual(self.path.read_bytes(), raw)
        self.assertEqual(self.path.stat().st_ino, inode)
        self.assertEqual(list(self.path.parent.glob('feedback.json.*')), [])

    def test_size_exact_limit_and_one_over(self):
        self.path.write_bytes(b'{}' + b' ' * (api.FEEDBACK_MAX_BYTES - 2))
        self.assertEqual(api.load_feedback(self.root), ({}, 'ok'))
        self.assert_corrupt(b'{}' + b' ' * (api.FEEDBACK_MAX_BYTES - 1))

    def test_depth_exact_limit_and_one_over_and_1400(self):
        for depth in [api.FEEDBACK_MAX_DEPTH, api.FEEDBACK_MAX_DEPTH + 1, 1400]:
            raw = b'{"101":{"extension":' + b'[' * (depth - 2) + b'0' + b']' * (depth - 2) + b'}}'
            with self.subTest(depth=depth):
                if depth == api.FEEDBACK_MAX_DEPTH:
                    self.path.write_bytes(raw)
                    self.assertEqual(api.load_feedback(self.root)[1], 'ok')
                else:
                    self.assert_corrupt(raw)

    def test_duplicates_first_and_last_at_both_levels(self):
        for duplicate in [b'"101":{},"101":{}', b'"101":{"verdict":"worth","verdict":"not_worth"}']:
            for first in [True, False]:
                with self.subTest(duplicate=duplicate, first=first):
                    good = b'"202":{}'
                    self.assert_corrupt(b'{' + (duplicate + b',' + good if first else good + b',' + duplicate) + b'}')

    def test_quoted_delimiters_and_escaped_quote_do_not_count_as_depth(self):
        self.path.write_text(json.dumps({'101': {'extension': '[' * 100 + '\\"{}'}}))
        self.assertEqual(api.load_feedback(self.root)[1], 'ok')

    def test_bom_is_corruption(self):
        self.assert_corrupt(b'\xef\xbb\xbf{}')

    def test_known_oversize_file_is_never_read(self):
        self.path.write_bytes(b'{}')
        with self.path.open('ab') as stream:
            stream.truncate(api.FEEDBACK_MAX_BYTES + 1)
        original = Path.open
        reads = []
        class Guard:
            def __init__(self, stream): self.stream = stream
            def __enter__(self): return self
            def __exit__(self, *args): self.stream.close()
            def fileno(self): return self.stream.fileno()
            def read(self, size=-1):
                reads.append(size)
                raise AssertionError('oversize file must be rejected before read')
        def guarded(path, *args, **kwargs):
            stream = original(path, *args, **kwargs)
            return Guard(stream) if path == self.path and args == ('rb',) else stream
        with patch.object(Path, 'open', guarded):
            self.assertEqual(api.load_feedback(self.root), ({}, 'invalid'))
        self.assertEqual(reads, [])

    def test_growth_after_stat_is_bounded_and_rejected(self):
        self.path.write_bytes(b'{}')
        original = Path.open
        reads = []
        class Growing:
            def __init__(self, stream): self.stream = stream
            def __enter__(self): return self
            def __exit__(self, *args): self.stream.close()
            def fileno(self): return self.stream.fileno()
            def read(self, size=-1):
                reads.append(size)
                # Simulate an external writer growing the opened inode after fstat.
                with original(self_path, 'ab') as out:
                    out.write(b' ' * api.FEEDBACK_MAX_BYTES)
                return self.stream.read(size)
        self_path = self.path
        def growing(path, *args, **kwargs):
            stream = original(path, *args, **kwargs)
            return Growing(stream) if path == self.path and args == ('rb',) else stream
        with patch.object(Path, 'open', growing):
            self.assertEqual(api.load_feedback(self.root), ({}, 'invalid'))
        self.assertEqual(reads, [api.FEEDBACK_MAX_BYTES + 1])
        self.assertEqual(self.path.stat().st_size, api.FEEDBACK_MAX_BYTES + 2)


if __name__ == '__main__':
    unittest.main(verbosity=2)
