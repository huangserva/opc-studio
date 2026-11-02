"""W2 regression cases: no production writes, fake models only."""
import copy,json,sqlite3,tempfile,threading,time,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from wechat_source.results import ResultReader,SCHEMA
from wechat_source_client import WechatSourceClient,SourceUnavailable
from scheduled_tasks import ScheduledTasks,ModelBusy

class CacheTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.reader=ResultReader(self.tmp.name);self.addCleanup(self.reader.close_status_cache)
        self.saved={'schema':SCHEMA,'stages':{n:{'available':True,'processed_versions':v,'pending_versions':2,'has_pending':True,'cache_latest_time':'2026-11-02T00:00:00+00:00'} for n,v in [('clues',387),('radar',253)]}}
    def test_cold_does_not_block_or_invent_zero(self):
        entered=threading.Event();release=threading.Event()
        def heavy():entered.set();release.wait(2);return self.saved
        self.reader.status=heavy
        try:
            start=time.monotonic();cold=self.reader.cached_status()
            self.assertLess(time.monotonic()-start,.1)
            self.assertTrue(cold['coverage']['stale']);self.assertIsNone(cold['stages']['clues']['processed_versions'])
            self.assertTrue(entered.wait(1))
            for _ in range(10):self.assertIsNone(self.reader.cached_status()['stages']['radar']['processed_versions'])
        finally:release.set()
    def test_failed_compute_keeps_old_counts_and_asof(self):
        self.reader.status=lambda:self.saved
        self.reader.refresh_coverage();asof=self.reader._coverage['as_of']
        with patch.object(self.reader,'status',side_effect=OSError('secret')):self.reader.refresh_coverage()
        with patch.object(self.reader,'start_status_cache'):
            data=self.reader.cached_status()
        self.assertTrue(data['coverage']['stale']);self.assertEqual(data['coverage']['as_of'],asof)
        self.assertFalse(data['available']);self.assertEqual([data['stages'][k]['processed_versions'] for k in ('clues','radar')],[387,253])
        self.assertNotIn('secret',json.dumps(data))
    def test_expired_cache_is_marked_stale_and_keeps_values(self):
        self.reader.status=lambda:self.saved;self.reader.refresh_coverage()
        self.reader._coverage['as_of']='2020-01-01T00:00:00+00:00'
        with patch.object(self.reader,'start_status_cache'):data=self.reader.cached_status()
        self.assertTrue(data['coverage']['stale']);self.assertFalse(data['stages']['clues']['available'])
        self.assertEqual(data['stages']['clues']['processed_versions'],387)
    def test_stage_failure_preserves_only_failed_stage_old_value(self):
        self.reader.status=lambda:self.saved;self.reader.refresh_coverage()
        failed=copy.deepcopy(self.saved);failed['stages']['clues']={'available':False};failed['stages']['radar']['processed_versions']=254
        self.reader.status=lambda:failed;self.reader.refresh_coverage()
        with patch.object(self.reader,'start_status_cache'):data=self.reader.cached_status()
        self.assertEqual(data['stages']['clues']['processed_versions'],387)
        self.assertEqual(data['stages']['radar']['processed_versions'],254)
        self.assertTrue(data['coverage']['stale'])
    def test_cached_clue_status_never_calls_expensive_processor(self):
        self.reader.status=lambda:self.saved
        self.reader.clue_status_compute=lambda:{'daily_review':{'pending_versions':3}}
        self.reader.refresh_coverage()
        with patch.object(self.reader,'start_status_cache'),patch.object(self.reader,'clue_status_compute',side_effect=AssertionError('HTTP must not calculate')):
            data=self.reader.cached_clue_status()
        self.assertEqual(data['processed_versions'],387);self.assertEqual(data['daily_review']['pending_versions'],3)
        self.assertIsNotNone(data['coverage_as_of'])
    def test_persisted_cache_loads_and_remains_stale_until_refreshed(self):
        self.reader.status=lambda:self.saved;self.reader.refresh_coverage()
        other=ResultReader(self.tmp.name)
        with patch.object(other,'start_status_cache'):data=other.cached_status()
        self.assertEqual(data['stages']['radar']['processed_versions'],253)

class LightLedgerTests(unittest.TestCase):
    def test_exclusive_writer_returns_old_job_marked_stale_with_bounded_wait(self):
        from wechat_source.daemon import Jobs
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);scheduler=ScheduledTasks(root,autostart=False)
            reader=ResultReader(root)
            reader.status=lambda:{'stages':{n:{'available':True,'processed_versions':1} for n in ('clues','radar')}}
            processor=SimpleNamespace(lock=threading.Lock(),status=lambda:{})
            source=SimpleNamespace(reader=reader,clues=processor,radar=processor)
            runtime=SimpleNamespace(workspace=root,source=source,knowledge=SimpleNamespace(capture=SimpleNamespace(close=lambda:None)))
            jobs=Jobs(runtime,root/'no-models.json',autostart=False)
            with scheduler.db() as db:db.execute('INSERT INTO tasks VALUES(?,?,?,?)',('b'*32,1,json.dumps({'kind':'wechat_radar','profile_id':'fixture'}),None))
            try:
                with jobs.db() as db:db.execute('INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?,?,?)',('a'*32,'radar','b'*32,'{}','running','start','start',None,None,''))
                first=jobs.status();self.assertEqual(len(first['jobs']),1)
                with sqlite3.connect(jobs.path) as writer:
                    writer.execute('BEGIN EXCLUSIVE')
                    start=time.monotonic();busy=jobs.status();elapsed=time.monotonic()-start
                self.assertLess(elapsed,.8);self.assertTrue(busy['jobs_stale']);self.assertTrue(busy['tasks_stale'])
                self.assertEqual(busy['jobs'][0]['run_id'],'a'*32);self.assertEqual(busy['error_stage'],'source_light_status')
            finally:jobs.close();scheduler.close()

class ClientTests(unittest.TestCase):
    def setUp(self):self.client=WechatSourceClient(SimpleNamespace(workspace='/tmp'),base='http://127.0.0.1:1')
    def test_disconnected_status_is_safe_unknown_then_stale_old_value(self):
        with patch.object(self.client,'request',side_effect=SourceUnavailable('radar_to_source','timeout')):
            data=self.client.status()
        self.assertEqual(data['error_stage'],'radar_to_source');self.assertIsNone(data['stages']['clues']['processed_versions'])
        self.client._last_status={'stages':{'clues':{'processed_versions':387}},'coverage':{'as_of':'saved'}}
        with patch.object(self.client,'request',side_effect=SourceUnavailable()):data=self.client.status()
        self.assertEqual(data['stages']['clues']['processed_versions'],387);self.assertEqual(data['coverage']['as_of'],'saved');self.assertTrue(data['coverage']['stale'])
    def test_poll_disconnect_is_pending_and_no_second_post(self):
        rid='a'*32;tid='b'*32;calls=[]
        def request(path,payload=None):
            calls.append((path,payload))
            if path=='run':return {'run_id':rid,'task_id':tid,'kind':'radar','executor':'wechat-source-daemon','status':'running'}
            raise SourceUnavailable('radar_to_source','network')
        with patch.object(self.client,'request',side_effect=request):
            status,_,out=self.client.analyze('radar',rid,{'id':tid},threading.Event(),{},time.monotonic()+10)
        self.assertEqual(status,'partial');self.assertTrue(json.loads(out)['receipt_pending'])
        self.assertEqual(sum(payload is not None for _,payload in calls),1);self.assertEqual(calls[-1][0],'jobs/'+rid)
    def test_lost_post_response_still_waits_on_original_id(self):
        with patch.object(self.client,'request',side_effect=SourceUnavailable()) as request:
            status,_,out=self.client.analyze('clues','a'*32,{'id':'b'*32},threading.Event(),{},time.monotonic()+10)
        self.assertEqual(request.call_count,1);self.assertEqual(json.loads(out)['source_run_id'],'a'*32)
    def test_clue_status_transport_failure_preserves_count_and_is_not_400(self):
        self.client._last_clue_status={'processed_versions':387,'coverage_as_of':'saved'}
        with patch.object(self.client,'request',side_effect=SourceUnavailable('source_read')):data=self.client.clue_status()
        self.assertEqual(data['processed_versions'],387);self.assertTrue(data['coverage_stale'])
        self.assertEqual(data['coverage_as_of'],'saved');self.assertEqual(data['error_stage'],'source_read')
    def test_wrong_job_binding_is_not_accepted(self):
        with patch.object(self.client,'request',return_value={'run_id':'c'*32,'task_id':'b'*32,'kind':'radar','executor':'wechat-source-daemon'}):
            with self.assertRaises(ValueError):self.client.event_receipt('a'*32,'b'*32,kind='radar')

class ReconciliationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.scheduler=ScheduledTasks(self.tmp.name,autostart=False);self.addCleanup(self.scheduler.close)
    def insert(self,rid='a'*32,kind='wechat_radar',status='partial',source_id=None):
        with self.scheduler.db() as db:
            db.execute('INSERT INTO runs(id,task_id,snapshot,status,started_at,finished_at,output) VALUES(?,?,?,?,?,?,?)',
              (rid,'b'*32,json.dumps({'kind':kind}),status,'start','stop',json.dumps({'receipt_pending':True,'source_run_id':source_id or rid})))
        return rid
    def test_final_ordinary_receipt_updates_only_original_run(self):
        rid=self.insert();calls=[]
        def read(*args,**kwargs):
            calls.append((args,kwargs));return {'run_id':rid,'task_id':'b'*32,'kind':'radar','status':'partial','output':json.dumps({'processed':253,'format':{'dropped':2}}),'exit_code':0,'finished_at':'source-finish'}
        self.scheduler.source_receipt_reader=read;self.scheduler.reconcile_source_receipt()
        with self.scheduler.db() as db:
            row=db.execute('SELECT * FROM runs').fetchone();self.assertEqual(db.execute('SELECT COUNT(*) FROM runs').fetchone()[0],1)
        data=json.loads(row['output']);self.assertEqual(data['processed'],253);self.assertFalse(data['receipt_pending']);self.assertTrue(data['source_receipt_final']);self.assertEqual(row['finished_at'],'source-finish')
        self.assertEqual(calls,[((rid,'b'*32),{'kind':'radar'})])
    def test_timeout_keeps_user_outcome_but_adds_final_durable_count(self):
        rid=self.insert(status='timeout')
        self.scheduler.source_receipt_reader=lambda *a,**k: {'run_id':rid,'task_id':'b'*32,'kind':'radar','status':'partial','output':'{"processed":253}'}
        self.scheduler.reconcile_source_receipt()
        row=self.scheduler.get_run(rid);self.assertEqual(row['status'],'timeout');self.assertEqual(row['finished_at'],'stop');self.assertEqual(json.loads(row['output'])['processed'],253)
    def test_wrong_binding_does_not_update_anything(self):
        rid=self.insert();before=self.scheduler.get_run(rid)
        self.scheduler.source_receipt_reader=lambda *a,**k: {'run_id':'c'*32,'task_id':'b'*32,'kind':'radar','status':'succeeded','output':'{}'}
        self.scheduler.reconcile_source_receipt();self.assertEqual(self.scheduler.get_run(rid),before)
    def test_pending_ordinary_job_blocks_second_submission_before_model_or_post(self):
        self.insert()
        task={'kind':'wechat_radar','profile_id':'fixture','enabled':True,'tool_groups':[],'allow_commands':False}
        with self.scheduler.db() as db:db.execute('INSERT INTO tasks VALUES(?,?,?,?)',('b'*32,1,json.dumps(task),None))
        with patch.object(self.scheduler,'model_config') as model:
            with self.assertRaises(ModelBusy):self.scheduler.claim('b'*32,'manual',launch=False)
            model.assert_not_called()
        with self.scheduler.db() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM runs').fetchone()[0],1)
    def test_restart_retains_original_receipt_id_instead_of_replaying(self):
        rid=self.insert(kind='wechat_clues',status='running',source_id='c'*32)
        self.scheduler.close();self.scheduler=ScheduledTasks(self.tmp.name,autostart=False)
        row=self.scheduler.get_run(rid);data=json.loads(row['output'])
        self.assertEqual(row['status'],'interrupted');self.assertEqual(data['source_run_id'],'c'*32);self.assertTrue(data['receipt_pending'])

if __name__=='__main__':unittest.main()
