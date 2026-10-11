"""Synthetic execution only; no backend requests or live tasks."""
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from isolation_patches import module_time
from wechat_source_loader import source_module
import test_wechat_radar_pipeline as radar_fixtures

executor_module=source_module('pi_executor')

class ExecutorStability(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.owner=SimpleNamespace(root=Path(self.tmp.name),store=SimpleNamespace(workspace=Path(self.tmp.name)))
        self.executor=executor_module.PiExecutor(__file__)
        self.config={'model':'synthetic','timeout_seconds':100}
    def execute(self,identity='batch',remaining=900):
        return self.executor.execute(self.owner,identity,'synthetic',threading.Event(),self.config,time.monotonic()+remaining)
    def test_empty_output_retries_once_and_records(self):
        with patch.object(executor_module,'backend_slots',return_value='ready'),patch.object(self.executor,'run',side_effect=[executor_module.EmptyModelOutput('empty'),'{}']) as run:
            self.assertEqual(self.execute(),'{}');self.assertEqual(run.call_count,2)
        receipt=json.loads((self.owner.root/'analysis-pi/batch/execution-receipt.json').read_text())
        self.assertEqual(receipt['empty_output_retries'],1)
    def test_second_empty_is_failure(self):
        with patch.object(executor_module,'backend_slots',return_value='ready'),patch.object(self.executor,'run',side_effect=executor_module.EmptyModelOutput('empty')) as run:
            with self.assertRaises(executor_module.EmptyModelOutput):self.execute()
            self.assertEqual(run.call_count,2)
    def test_failure_no_retry_and_repair_no_extra_empty_retry(self):
        for error,identity in [(ValueError('aborted'),'batch'),(executor_module.EmptyModelOutput('empty'),'batch-repair')]:
            with patch.object(executor_module,'backend_slots',return_value='ready'),patch.object(self.executor,'run',side_effect=error) as run:
                with self.assertRaises(ValueError):self.execute(identity)
                self.assertEqual(run.call_count,1)
    def test_insufficient_budget_no_retry(self):
        with patch.object(executor_module,'backend_slots',return_value='ready'),patch.object(self.executor,'run',side_effect=executor_module.EmptyModelOutput('empty')) as run:
            with self.assertRaises(ValueError):self.execute(remaining=30)
            self.assertEqual(run.call_count,1)
    def test_busy_wait_bounded_and_never_sends(self):
        clock=[0.]
        class Cancel:
            def is_set(self):return False
            def wait(self,seconds):clock[0]+=seconds
        with module_time(executor_module,monotonic=lambda:clock[0]),patch.object(executor_module,'backend_slots',return_value='busy'),patch.object(self.executor,'run') as run:
            with self.assertRaisesRegex(ValueError,'本批未发送'):
                self.executor.execute(self.owner,'batch','synthetic',Cancel(),self.config,1800)
            self.assertLessEqual(clock[0],230);run.assert_not_called()

    def test_runtime_done_blank_and_abort_are_distinguished(self):
        import io
        for event,error in [({'type':'done','status':'done','answer':'   '},'空输出'),({'type':'done','status':'done'},'空输出'),({'type':'done','status':'done','answer':'','stop_reason':'aborted'},'模型无响应')]:
            process=SimpleNamespace(stdin=io.StringIO(),stdout=io.StringIO(json.dumps(event)+'\n'),poll=lambda:0)
            with patch.object(executor_module.subprocess,'Popen',return_value=process):
                with self.assertRaisesRegex(ValueError,error):
                    self.executor.run(self.owner,'batch', 'synthetic',threading.Event(),self.config,time.monotonic()+900,None)

class RadarStability(unittest.TestCase):
    setUp=radar_fixtures.RadarTest.setUp
    add=radar_fixtures.RadarTest.add
    result=radar_fixtures.RadarTest.result
    def task(self):return {'radar_since':self.pipeline.since,'body':'synthetic'}
    def test_busy_receipt_is_partial_without_checkpoint(self):
        self.cp.call_pi=lambda *args: (_ for _ in ()).throw(ValueError('推理服务仍繁忙，本批未发送，保留待处理'))
        with patch('wechat_radar_pipeline.backend_slots',return_value='ready'):
            status,_,out=self.pipeline.analyze('busy',self.task(),threading.Event(),{'model':'synthetic'},time.monotonic()+900)
        data=json.loads(out);self.assertEqual(status,'partial');self.assertEqual(data['processed'],0)
        self.assertEqual(data['error_kind'],'model_busy');self.assertIn('format',data);self.assertIn('dropped_items',data)
        with self.pipeline.db() as db:self.assertEqual(db.execute('select count(*) from analyzed').fetchone()[0],0)
    def test_failure_retains_format_and_dropped_counts(self):
        def model(*args):return '{"topics":[{"title":"bad","refs":["e0"]}],"knowledge":[],"profile":[]}'
        self.cp.call_pi=model
        original=self.pipeline.validate
        def validate(*args):
            original(*args)
            raise ValueError('synthetic whole-batch failure')
        self.pipeline.validate=validate
        with patch('wechat_radar_pipeline.backend_slots',return_value='ready'):
            status,_,out=self.pipeline.analyze('failure',self.task(),threading.Event(),{'model':'synthetic'},time.monotonic()+900)
        data=json.loads(out)
        # radar-batch-hardening: a second invalid answer is quarantined and the run
        # moves on, so this is a partial run holding the batch out of replay rather
        # than a failed one; the format and per-entry drop counts must survive either way.
        self.assertEqual(status,'partial');self.assertEqual(data['error_kind'],'quarantined')
        self.assertEqual(data['quarantined'],1)
        self.assertEqual(data['dropped_items']['雷达条目缺少必需字段：summary'],2)
        self.assertIn('format',data)
    def test_reserve_keeps_next_batch_pending_before_deadline(self):
        self.add('b','另一条技术资料',True)
        clock=[0.]
        original=self.pipeline.pending
        self.pipeline.pending=lambda *args:original(*args)[:1]
        def model(*args):clock[0]+=350;return '{"topics":[],"knowledge":[],"profile":[]}'
        self.cp.call_pi=model
        with module_time('wechat_radar_pipeline',monotonic=lambda:clock[0]),patch('wechat_radar_pipeline.backend_slots',return_value='ready'):
            status,_,out=self.pipeline.analyze('reserve',self.task(),threading.Event(),{'model':'synthetic'},600)
        data=json.loads(out)
        self.assertEqual(status,'partial');self.assertEqual(data['processed'],1);self.assertTrue(data['has_more'])
        self.assertLess(clock[0],600)

class SharedExecutorStability(unittest.TestCase):
    def test_shared_empty_result_retries_and_records(self):
        import source_pi_executor as shared
        with tempfile.TemporaryDirectory() as folder:
            owner=SimpleNamespace(root=Path(folder),store=SimpleNamespace(workspace=Path(folder)))
            with patch.object(shared,'_execute_pi',side_effect=[shared.EmptyModelOutput('empty'),'{}']) as run:
                self.assertEqual(shared.execute_pi(owner,'batch','synthetic',threading.Event(),{'model':'synthetic','timeout_seconds':100},time.monotonic()+900),'{}')
                self.assertEqual(run.call_count,2)
            self.assertEqual(json.loads((owner.root/'analysis-pi/batch/execution-receipt.json').read_text())['empty_output_retries'],1)
    def test_shared_done_blank_is_not_success(self):
        import source_pi_executor as shared
        with tempfile.TemporaryDirectory() as folder:
            owner=SimpleNamespace(root=Path(folder),store=SimpleNamespace(workspace=Path(folder)))
            with patch('model_service_status.backend_slots',return_value='ready'),patch('pi_runtime.PiRuntime.run',return_value={'status':'done','answer':'  '}):
                with self.assertRaises(shared.EmptyModelOutput):shared._execute_pi(owner,'batch','synthetic',threading.Event(),{'model':'synthetic'},time.monotonic()+900)

class DealsCursorStability(unittest.TestCase):
    def test_invalid_conflict_keeps_failed_and_later_invitation_pending(self):
        from deals_review import DealsReview
        with tempfile.TemporaryDirectory() as folder:
            store=SimpleNamespace(workspace=Path(folder),deals=SimpleNamespace(listing=lambda **kw:{'deals':[]}))
            review=DealsReview(store)
            clues=[{'id':'first','title':'first','created_at':'2026-10-08T00:00:00+00:00'}, {'id':'bad','title':'bad','created_at':'2026-10-08T01:00:00+00:00'}, {'id':'later','title':'later','created_at':'2026-10-08T02:00:00+00:00'}]
            review.new_clues=lambda since,limit:([c for c in clues if c['created_at']>since],sum(c['created_at']>since for c in clues))
            review.clue_evidence=lambda *args:[]
            review.conflict_prompt=lambda *args:'synthetic'
            review.set_state('last_clue_scan_at','2026-10-07T00:00:00+00:00')
            with patch.object(review,'call_model',side_effect=['{"conflicts":[]}','{"conflicts":[{"deal":"invented","verdict":"possible"}]}']) as model:
                result=review.conflict_phase('run',{},threading.Event(),{},time.monotonic()+900)
                self.assertEqual(model.call_count,2)
            self.assertEqual(result['clues'],1);self.assertEqual(result['deferred'],2);self.assertTrue(result['has_more'])
            self.assertEqual(review.state('last_clue_scan_at'),clues[0]['created_at'])
            with patch.object(review,'call_model',return_value='{"conflicts":[]}') as model:
                result=review.conflict_phase('next',{},threading.Event(),{},time.monotonic()+900)
                self.assertEqual(model.call_count,2);self.assertEqual(result['clues'],2);self.assertFalse(result['has_more'])

class SourceDeadlineStability(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.daemon=source_module('daemon')
        self.jobs=self.daemon.Jobs.__new__(self.daemon.Jobs)
        self.jobs.runtime=SimpleNamespace(workspace=Path(self.tmp.name),source=SimpleNamespace())
        self.jobs.model=lambda task:{'model':'synthetic'}
    def row(self,expires=1000):
        return {'id':'a'*32,'kind':'radar','created_at':'1970-01-01T00:00:00+00:00','task':json.dumps({'timeout':900,'execution_deadline_at':expires})}
    def test_queue_expiry_never_reads_model_or_sends(self):
        with module_time(self.daemon,time=lambda:1001),patch.object(self.jobs,'model') as model:
            status,_,out=self.jobs.run_job(self.row(),threading.Event())
        self.assertEqual(status,'timeout');self.assertEqual(json.loads(out)['error_kind'],'queue_expired');model.assert_not_called()
    def test_queue_wait_is_deducted_and_expiry_persisted_from_run_start(self):
        import sqlite3
        root=self.jobs.runtime.workspace/'schedules';root.mkdir()
        with sqlite3.connect(root/'tasks.db') as db:
            db.execute('create table runs(id text,task_id text,snapshot text,started_at text)')
            db.execute('insert into runs values(?,?,?,?)',('a'*32,'b'*32,json.dumps({'kind':'wechat_radar','timeout':900}),'1970-01-01T00:01:40+00:00'))
        tid,task=self.jobs.task('radar','a'*32,'b'*32)
        self.assertEqual(task['execution_deadline_at'],1000)
        from unittest.mock import Mock
        fn=Mock(return_value=('partial',None,'{}'));self.jobs.runtime.source.analyze_radar=fn
        row=self.row();row['task']=json.dumps(task)
        with module_time(self.daemon,time=lambda:700,monotonic=lambda:50):
            self.jobs.run_job(row,threading.Event())
        self.assertEqual(fn.call_args.args[-1],350)  # only 300 seconds remain
    def test_legacy_queue_uses_creation_budget_and_cancel_never_sends(self):
        row=self.row();row['task']=json.dumps({'timeout':900})
        with module_time(self.daemon,time=lambda:901),patch.object(self.jobs,'model') as model:
            self.assertEqual(self.jobs.run_job(row,threading.Event())[0],'timeout');model.assert_not_called()
        cancel=threading.Event();cancel.set()
        with patch.object(self.jobs,'model') as model:
            self.assertEqual(self.jobs.run_job(self.row(),cancel)[0],'cancelled');model.assert_not_called()
