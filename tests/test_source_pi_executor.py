"""20r: synthetic runtime, module-local clocks, no provider or source data."""
import json
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from isolation_patches import module_time, module_thread, WECHAT_SOURCE_REASON, wechat_source_available
import source_pi_executor as executor

ZERO = {'input': 0, 'output': 0, 'totalTokens': 0}
DONE = {'status': 'done', 'answer': 'synthetic answer', 'stop_reason': 'stop', 'usage': ZERO}

class ExecutorTests(unittest.TestCase):
    # hygiene-4：本类的每个用例都要把微信来源插件装起来才能跑（source_module 或
    # patch model_service_status，而后者在模块级就会调 loader）。插件不在公开仓库里，
    # 缺它时整类跳过。本机配置齐全时逐条照跑，行为与从前一致。
    @unittest.skipUnless(wechat_source_available(), WECHAT_SOURCE_REASON)
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.owner = SimpleNamespace(root=Path(self.tmp.name), store=SimpleNamespace(workspace=Path(self.tmp.name)))
        self.cancel = threading.Event()
        self.config = {'timeout_seconds': 100, 'model': 'synthetic', 'api_key': 'synthetic-secret'}
        self.clock = [0.0]
        self.runtime = MagicMock()
        self.runtime.return_value.run.return_value = DONE
        for guard in (patch('pi_runtime.PiRuntime', self.runtime), patch('model_service_status.backend_slots', return_value='free')):
            guard.start(); self.addCleanup(guard.stop)

    def call(self, deadline=105, identity='test'):
        with module_time(executor, monotonic=lambda: self.clock[0]), module_thread(executor, constructor=MagicMock()):
            return executor.execute_pi(self.owner, identity, 'synthetic prompt body', self.cancel, self.config, deadline)

    def receipt(self, identity='test'):
        return json.loads((self.owner.root/'analysis-pi'/identity/'execution-receipt.json').read_text())

    def assert_kind(self, kind, deadline=105):
        with self.assertRaises(ValueError) as caught:
            self.call(deadline)
        self.assertEqual(getattr(caught.exception, 'failure_kind', None), kind)
        self.assertNotIsInstance(caught.exception, executor.EmptyModelOutput)
        return caught.exception

    def test_budget_blocks_before_runtime_at_104_9_seconds(self):
        self.assert_kind('not_sent', 104.9)
        self.runtime.assert_not_called()
        self.assertFalse(self.receipt()['sent'])
        self.assertEqual(self.receipt()['provider_requests'], 0)

    def test_budget_allows_at_105_and_105_1_seconds(self):
        for remaining in (105, 105.1):
            with self.subTest(remaining=remaining):
                self.runtime.reset_mock()
                self.assertEqual(self.call(remaining), DONE['answer'])
                self.runtime.assert_called_once()
                self.assertEqual(self.receipt()['provider_requests'], 1)

    def test_lease_wait_and_slot_wait_recheck_budget(self):
        def lease(*args):
            self.clock[0] = max(1, self.clock[0])
            raise BlockingIOError()
        for gate in ('lease-busy', 'lease-acquired', 'slot-busy', 'slot-free'):
            with self.subTest(gate=gate):
                self.clock[0] = 0; self.runtime.reset_mock()
                def slot(config):
                    self.clock[0] = max(1, self.clock[0])
                    return 'busy' if gate == 'slot-busy' else 'free'
                def acquired(*args):
                    self.clock[0] = max(1, self.clock[0])
                guard = (patch('fcntl.flock', side_effect=lease if gate == 'lease-busy' else acquired)
                         if gate.startswith('lease') else patch('model_service_status.backend_slots', side_effect=slot))
                def waited(*args):
                    self.clock[0] += 105
                with guard, patch.object(self.cancel, 'wait', side_effect=waited):
                    self.assert_kind('not_sent', 105)
                self.runtime.assert_not_called()
                self.assertEqual(self.receipt()['provider_requests'], 0)

    def test_cancel_and_deadline_send_nothing_extra(self):
        self.cancel.set(); self.assert_kind('cancelled')
        self.runtime.assert_not_called()
        self.cancel.clear(); self.assert_kind('deadline', 0)
        self.runtime.assert_not_called()
        for kind in ('cancelled', 'deadline'):
            def ended(*args):
                if kind == 'cancelled': self.cancel.set()
                else: self.clock[0] = 105
                return DONE
            self.clock[0] = 0; self.cancel.clear(); self.runtime.reset_mock()
            self.runtime.return_value.run.side_effect = ended
            self.assert_kind(kind)
            self.runtime.assert_called_once()
            self.assertEqual(self.receipt()['provider_requests'], 1)

    def test_aborted_is_one_request_and_not_empty_retry(self):
        self.runtime.return_value.run.return_value = {**DONE, 'status': 'error', 'stop_reason': 'aborted'}
        self.assert_kind('model_transient')
        self.runtime.assert_called_once()
        receipt = self.receipt()
        self.assertTrue(receipt['sent']); self.assertEqual(receipt['provider_requests'], 1)
        self.assertEqual(receipt['usage'], ZERO)

    def test_done_empty_answer_retries_once_then_stops(self):
        self.runtime.return_value.run.return_value = {**DONE, 'answer': '  '}
        for identity, calls in (('test', 2), ('test-repair', 1)):
            self.runtime.reset_mock()
            with self.assertRaises(executor.EmptyModelOutput): self.call(300, identity)
            self.assertEqual(self.runtime.call_count, calls)
            self.assertEqual(self.receipt(identity)['provider_requests'], calls)
            self.assertEqual(self.receipt(identity)['failure_kind'], 'model_transient')

    def test_receipt_has_stop_reason_usage_and_sent_flag(self):
        self.runtime.return_value.run.return_value = {**DONE, 'usage': {'input': 3, 'output': 4, 'totalTokens': 7, 'unsafe': 'synthetic-secret'}}
        self.call()
        receipt = self.receipt()
        self.assertEqual(receipt['stop_reason'], 'stop')
        self.assertEqual(receipt['usage'], {'input': 3, 'output': 4, 'totalTokens': 7})
        self.assertTrue(receipt['sent']); self.assertEqual(receipt['provider_requests'], 1)
        text = json.dumps(receipt)
        self.assertNotIn('synthetic-secret', text); self.assertNotIn('synthetic prompt body', text)
