"""Exercise the source-owned stdio executor with synthetic local worker events."""
import json
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from isolation_patches import wechat_source_available, WECHAT_SOURCE_REASON
if not wechat_source_available():raise unittest.SkipTest(WECHAT_SOURCE_REASON)
from wechat_source_loader import source_module
executor=source_module('pi_executor')


class SourceTimeoutTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.worker=self.root/'worker.cjs'
        self.worker.write_text('''const readline=require('readline');
readline.createInterface({input:process.stdin}).on('line',line=>{
 const input=JSON.parse(line);if(input.type!=='run')return;
 console.log(JSON.stringify({type:'ready',tools:[]}));
 const mode=input.config.synthetic_mode,retry=input.job_id.includes('-timeout-retry')||input.job_id.includes('-empty-retry');
 const success=mode==='timeout-then-success'&&retry;
 if(success)console.log(JSON.stringify({type:'done',status:'done',stop_reason:'stop',answer:'synthetic answer'}));
 else if(mode==='empty')console.log(JSON.stringify({type:'done',status:'done',stop_reason:'stop',answer:''}));
 else console.log(JSON.stringify({type:'done',status:'error',stop_reason:'error',diagnostic:mode==='other-error'?'HTTP 503':'Request timed out. synthetic-private-value'}));
});''')
        self.owner=SimpleNamespace(root=self.root,store=SimpleNamespace(workspace=self.root));self.engine=executor.PiExecutor(self.worker)
        self.config={'model':'synthetic','api_key':'synthetic-private-key','timeout_seconds':60,'synthetic_mode':'timeout'}
        self.cancel=threading.Event()

    def call(self,identity='timeout',budget=300):
        with patch.object(executor,'backend_slots',return_value='ready'):
            return self.engine.execute(self.owner,identity,'synthetic prompt',self.cancel,self.config,time.monotonic()+budget)

    def receipt(self,identity='timeout'):
        return json.loads((self.root/'analysis-pi'/identity/'execution-receipt.json').read_text())

    def test_request_timeout_is_classified_and_only_retried_once_with_backoff(self):
        with patch.object(self.cancel,'wait',return_value=False) as wait:
            with self.assertRaises(ValueError) as caught:self.call()
            wait.assert_called_once_with(1.0)
        self.assertEqual(getattr(caught.exception,'failure_kind',None),'model_timeout')
        receipt=self.receipt()
        self.assertEqual([a['status'] for a in receipt['attempts']],['model_timeout','model_timeout'])
        self.assertEqual((receipt['model_timeout_retries'],receipt['empty_output_retries']),(1,0))
        self.assertEqual(receipt['attempts'][0]['backoff_seconds'],1.0)
        saved=json.dumps(receipt)+str(caught.exception)
        for private in ['synthetic-private-value','synthetic-private-key','synthetic prompt']:self.assertNotIn(private,saved)

    def test_timeout_retry_success_returns_answer_and_keeps_attempt_receipt(self):
        self.config['synthetic_mode']='timeout-then-success'
        with patch.object(self.cancel,'wait',return_value=False):self.assertEqual(self.call(),'synthetic answer')
        receipt=self.receipt()
        self.assertEqual([a['status'] for a in receipt['attempts']],['model_timeout','returned'])
        self.assertEqual(receipt['model_timeout_retries'],1)

    def test_timeout_without_budget_or_cancellation_never_starts_another_call(self):
        with self.assertRaises(ValueError) as caught:self.call(budget=20)
        self.assertEqual(getattr(caught.exception,'failure_kind',None),'model_timeout')
        self.assertEqual(len(self.receipt()['attempts']),1)
        def cancelled(_):self.cancel.set();return True
        with patch.object(self.cancel,'wait',side_effect=cancelled):
            with self.assertRaises(ValueError):self.call(identity='cancelled')
        self.assertEqual(len(self.receipt('cancelled')['attempts']),1)

    def test_other_failures_keep_existing_classification_and_no_new_retry(self):
        self.config['synthetic_mode']='other-error'
        with self.assertRaisesRegex(ValueError,'模型执行失败') as caught:self.call()
        self.assertIsNone(getattr(caught.exception,'failure_kind',None))
        self.assertEqual(len(self.receipt()['attempts']),1)
        self.assertEqual(self.receipt()['model_timeout_retries'],0)

    def test_empty_output_retry_count_remains_separate(self):
        self.config['synthetic_mode']='empty'
        with self.assertRaises(ValueError):self.call()
        self.assertEqual((self.receipt()['empty_output_retries'],self.receipt()['model_timeout_retries']),(1,0))


if __name__=='__main__':unittest.main()
