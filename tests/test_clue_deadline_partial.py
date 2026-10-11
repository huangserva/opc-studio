"""Synthetic coverage: a slow shared model yields before the run limit.

The run must persist finished batches, report partial, and leave the unfinished
batch pending for the next run instead of being cut off as a timeout.
"""
import json
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from isolation_patches import module_time

import test_clue_pipeline as fixtures
from clue_pipeline import CluePipeline
from wechat_source_loader import source_module

_clue_source = source_module('clue_pipeline')


class DeadlinePartialTests(unittest.TestCase):
    tearDown = fixtures.PipelineTests.tearDown
    add = fixtures.PipelineTests.add

    def setUp(self):
        fixtures.PipelineTests.setUp(self)
        self.pipeline.prepare_cache = lambda *args: {'index_status': 'ready'}
        for index in range(100):
            self.add('slow' + str(index), '合成待续技术消息 ' + str(index))

    def run_analysis(self, run, model, clock, limit=1800):
        self.pipeline.call_pi = model
        with module_time(_clue_source, monotonic=lambda: clock[0]):
            status, _, output = self.pipeline.analyze(run * 32,
                {'body': '合成范围', 'wechat_mode': 'incremental'}, threading.Event(), self.config, limit)
        return status, json.loads(output)

    def test_slow_model_saves_progress_and_reports_partial_before_the_limit(self):
        clock = [0.0]
        def model(*args):
            clock[0] += 220
            return '{"clues":[]}'
        status, data = self.run_analysis('a', model, clock)
        self.assertLess(clock[0], 1800, 'the run must return before the run limit')
        self.assertEqual(status, 'partial')
        self.assertEqual(data['processed'], 80)
        self.assertTrue(data['has_more'])
        self.assertTrue(data['time_budget'])
        self.assertEqual(data['deferred'], 0)
        self.assertIn('本轮时间用完', data['state'])
        with self.pipeline.db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM failed_batches').fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM retry_documents').fetchone()[0], 0)
        # The next run resumes from the saved checkpoint and finishes the scope.
        clock[0] = 0.0
        status, data = self.run_analysis('b', lambda *args: '{"clues":[]}', clock)
        self.assertEqual(status, 'succeeded')
        self.assertEqual(data['processed'], 21)  # the fixture's own sample message is pending too
        self.assertFalse(data['has_more'])
        self.assertEqual(self.pipeline.status()['processed_versions'], 101)

    def test_model_failure_at_the_time_limit_leaves_the_batch_pending(self):
        clock = [0.0]
        def model(*args):
            clock[0] += 220
            if clock[0] >= 1760:
                raise ValueError('Pi 分析未完成，本批未推进：等待模型期间停止')
            return '{"clues":[]}'
        status, data = self.run_analysis('a', model, clock)
        self.assertLess(clock[0], 1800)
        self.assertEqual(status, 'partial')
        self.assertEqual(data['processed'], 70)
        self.assertTrue(data['time_budget'])
        # A starved batch must not burn one of its three automatic attempts.
        self.assertEqual(data['deferred'], 0)
        self.assertEqual(data['errors'], [])
        with self.pipeline.db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM failed_batches').fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM retry_documents').fetchone()[0], 0)
        self.assertEqual(self.pipeline.status()['processed_versions'], 70)

    def test_busy_inference_slot_leaves_the_batch_pending_and_reports_partial(self):
        clock = [0.0]
        def model(*args):
            clock[0] += 240
            raise ValueError('推理服务仍繁忙，本批未发送，保留待处理')
        status, data = self.run_analysis('a', model, clock)
        self.assertLess(clock[0], 1800)
        self.assertEqual(status, 'partial')
        self.assertEqual(data['processed'], 0)
        self.assertTrue(data['has_more'])
        self.assertTrue(data['time_budget'])
        # Nothing was sent, so the batch keeps all three automatic attempts.
        self.assertEqual(data['deferred'], 0)
        self.assertEqual(data['errors'], [])
        with self.pipeline.db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM failed_batches').fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM retry_documents').fetchone()[0], 0)

    def test_busy_inference_slot_cannot_consume_the_whole_run(self):
        from source_pi_executor import execute_pi
        clock = [0.0]
        class FakeCancel:
            def is_set(self): return False
            def wait(self, seconds):
                clock[0] += seconds
                return False
        owner = SimpleNamespace(root=Path(self.tmp.name) / 'pi', store=SimpleNamespace(workspace=Path(self.tmp.name)))
        with patch('model_service_status.backend_slots', return_value='busy'), \
             module_time('source_pi_executor', monotonic=lambda: clock[0]):
            with self.assertRaises(ValueError) as failure:
                execute_pi(owner, 'a' * 32, '合成资料', FakeCancel(), {'model': 'synthetic', 'timeout_seconds': 100}, 1800)
        self.assertIn('繁忙', str(failure.exception))
        self.assertLess(clock[0], 300, 'one batch must not wait until the run limit')


if __name__ == '__main__':
    unittest.main()
