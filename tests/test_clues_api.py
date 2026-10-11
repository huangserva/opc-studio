import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
import urllib.parse
from server import Handler,Store,ThreadingHTTPServer
from knowledge_store import Knowledge
from scheduled_tasks import ScheduledTasks

from isolation_patches import WECHAT_SOURCE_REASON, wechat_source_available
class ClueAPITests(unittest.TestCase):
    # hygiene-4：本类的每个用例都要把微信来源插件装起来才能跑（source_module 或
    # patch model_service_status，而后者在模块级就会调 loader）。插件不在公开仓库里，
    # 缺它时整类跳过。本机配置齐全时逐条照跑，行为与从前一致。
    @unittest.skipUnless(wechat_source_available(), WECHAT_SOURCE_REASON)
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.store=Store(self.tmp.name);self.store._knowledge=Knowledge(self.store,sources=[])
        with self.store.knowledge.db() as db:
            db.execute('CREATE TEMP TABLE seen(id TEXT PRIMARY KEY)');self.store.knowledge._write(db,'synthetic','m1','wechat','合成项目原文','合成项目需要确认交付时间，负责人尚未确认。',provenance={'timestamp':1791165600})
        self.doc=self.store.knowledge.search('')['results'][0];self.doc=self.store.knowledge.document(self.doc['id'])
        # In-process processors are fixture-only; production delegates to Radar.
        from wechat_source_loader import source_module
        self.store._wechat_source=source_module('service').WechatSource(self.store,self.store.clue_pipeline,source_module('wechat_radar_pipeline').RadarPipeline(self.store))
        self.srv=ThreadingHTTPServer(('127.0.0.1',0),Handler);self.srv.store=self.store;self.srv.schedules=ScheduledTasks(self.tmp.name,autostart=False);self.store.schedules=self.srv.schedules;self.srv.schedules.publisher=self.store.clues.from_run
        self.thread=threading.Thread(target=self.srv.serve_forever,daemon=True);self.thread.start();self.base=f'http://127.0.0.1:{self.srv.server_port}';self.opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    def tearDown(self):
        self.srv.shutdown();self.srv.schedules.close();self.srv.server_close();self.thread.join();self.tmp.cleanup()
    def api(self,path,body=None,headers=None):
        req=urllib.request.Request(self.base+path,data=json.dumps(body).encode() if body is not None else None,headers={'Origin':self.base,'X-Article-Studio':'1','Content-Type':'application/json'} if headers is None else headers)
        with self.opener.open(req) as r:return json.load(r)
    def new(self):return self.api('/api/clues',{'title':'合成项目','categories':['projects'],'picks':[{'id':self.doc['id'],'hash':self.doc['hash'],'start':0,'end':len(self.doc['body'])}]})
    def test_local_origin_and_conflict_are_enforced(self):
        with self.assertRaises(urllib.error.HTTPError) as e:self.api('/api/clues',{'title':'bad'},headers={'Origin':'https://invalid.test'})
        self.assertEqual(e.exception.code,403);e.exception.close()
        saved=self.new()
        with self.assertRaises(urllib.error.HTTPError) as e:self.api('/api/clues',{'id':saved['id'],'revision':0,'reason':'overwrite'})
        self.assertEqual(e.exception.code,409);e.exception.close()
        self.assertEqual(self.api('/api/clues?'+urllib.parse.urlencode({'q':'合成'}))['total'],1)
    def test_real_source_to_clue_to_event_to_explicit_action(self):
        saved=self.new();d=self.api('/api/clues/'+saved['id']);self.assertEqual(d['categories'],['projects']);self.assertEqual(len(d['entries']),1)
        link=self.api('/api/clues/'+saved['id']+'/promote',{'revision':d['revision']});event=self.api('/api/events/'+link['event_id'])
        self.assertTrue(event['needs_confirmation']);self.assertEqual(event['actions'],[])
        action=self.api('/api/events/'+event['id']+'/actions',{'summary':'合成测试：本人核对交付时间','owner':'合成测试本人','owner_is_self':True,'status':'needs_confirmation','due_at':'','evidence_ids':[event['evidence'][0]['id']]})
        action=action['actions'][0]
        self.assertEqual(self.api('/api/events?view=mine')['counts']['mine_actions'],0)
        self.api('/api/events/'+event['id']+'/actions',{'id':action['id'],'revision':action['revision'],'status':'open','reason':'仅在合成环境显式确认'})
        self.assertEqual(self.api('/api/events?view=mine')['counts']['mine_actions'],1)
        self.assertEqual(self.api('/api/events?view=calendar')['events'],[])
    def test_successful_run_delivery_link_and_raw_run_survive_restart(self):
        task=self.api('/api/schedules/save',dict(name='合成结果入口',kind='script',body='printf synthetic-inbox',cwd=self.tmp.name,frequency='interval',minutes=60,timeout=5,enabled=False,allow_commands=False,result_destination='clues'))
        run=self.api('/api/schedules/'+task['id']+'/run',{})
        for _ in range(100):
            raw=self.api('/api/schedules/run/'+run['id'])
            if raw.get('clue_id'):break
            time.sleep(.03)
        self.assertEqual(raw['status'],'succeeded');self.assertEqual(raw['output'],'synthetic-inbox');self.assertTrue(raw['clue_id'])
        first=self.api('/api/schedules/'+run['id']+'/publish',{});self.assertTrue(first['duplicate']);self.assertEqual(self.api('/api/clues')['total'],1)
        self.srv.schedules.close();self.srv.schedules=ScheduledTasks(self.tmp.name,autostart=False);self.store.schedules=self.srv.schedules;self.srv.schedules.publisher=self.store.clues.from_run
        self.assertEqual(self.api('/api/schedules/run/'+run['id'])['clue_id'],raw['clue_id'])
        with self.opener.open(self.base+'/clues.html') as r:self.assertIn('年度记事',r.read().decode())

    def test_wechat_analysis_api_runs_scoped_pipeline_without_external_transport(self):
        from types import SimpleNamespace
        from datetime import datetime,timezone
        with self.store.knowledge.db() as db:db.execute('UPDATE documents SET source=?,modified=? WHERE id=?',('wechat',datetime.now(timezone.utc).isoformat(),self.doc['id']))
        config={'model':'synthetic','endpoint':'http://127.0.0.1:1/v1/chat/completions','no_api_key':True}
        self.srv.schedules.assistant=SimpleNamespace(model_document=lambda:{'profiles':[{'id':'synthetic','config':config}]},settings=lambda:{'profiles':[]})
        self.srv.schedules.wechat_analyzer=self.store.clue_pipeline.analyze
        self.store.clue_pipeline.call_pi=lambda *args:json.dumps({'clues':[dict(title='合成交付进展',summary='交付时间待核对',relevance='合成项目资料',categories=['projects'],kind='information',priority='normal',quotes=[{'ref':'e0'}])]})
        self.assertEqual(self.api('/api/clues/wechat/status')['processed_versions'],0)
        run=self.api('/api/clues/wechat/run',{'profile_id':'synthetic','enabled':False})
        for _ in range(100):
            raw=self.api('/api/schedules/run/'+run['run_id'])
            if raw['status']!='running':break
            time.sleep(.03)
        self.assertEqual(raw['status'],'succeeded',raw['output']);self.assertEqual(self.api('/api/clues')['total'],1)
        self.assertEqual(self.api('/api/clues/wechat/status')['processed_versions'],1)
        self.assertEqual(self.api('/api/events')['counts']['total'],0)

    def test_wechat_api_drops_one_bad_clue_and_never_duplicates_the_valid_one(self):
        from datetime import datetime,timezone
        from types import SimpleNamespace
        from unittest.mock import Mock
        with self.store.knowledge.db() as db:
            db.execute('UPDATE documents SET source=?,modified=? WHERE id=?',
                       ('wechat',datetime.now(timezone.utc).isoformat(),self.doc['id']))
        config={'model':'synthetic-partial','endpoint':'http://127.0.0.1:1/v1/chat/completions','no_api_key':True}
        self.srv.schedules.assistant=SimpleNamespace(
            model_document=lambda:{'profiles':[{'id':'synthetic-partial','config':config}]},
            settings=lambda:{'profiles':[]})
        pipeline=self.store.clue_pipeline
        self.srv.schedules.wechat_analyzer=pipeline.analyze
        publisher=Mock(wraps=self.store.clues.from_run)
        self.srv.schedules.publisher=publisher
        valid=dict(title='合成可追溯进展',summary='交付时间尚待确认',relevance='必须核对合成原文',
                   categories=['projects'],kind='information',priority='normal',quotes=[{'ref':'e0'}])
        invalid={**valid,'title':'合成错误引用','quotes':[{'ref':'e-not-in-this-batch'}]}
        mixed=json.dumps({'clues':[valid,invalid]},ensure_ascii=False)
        primary_and_repair=Mock(side_effect=[mixed,mixed])
        pipeline.call_pi=primary_and_repair

        def finished(identity):
            deadline=time.monotonic()+5
            while time.monotonic()<deadline:
                receipt=self.api('/api/schedules/run/'+identity)
                if receipt['status']!='running':return receipt
                time.sleep(.01)
            self.fail('合成 HTTP 任务未在期限内完成')

        started=self.api('/api/clues/wechat/run',{'profile_id':'synthetic-partial','enabled':False})
        partial=finished(started['run_id'])
        # One unusable clue is dropped on its own: the valid sibling is saved and
        # the batch advances, so the run is not left waiting for a retry.
        self.assertEqual(partial['status'],'succeeded',partial['output'])
        self.assertEqual(partial['exit_code'],0)
        self.assertIsNone(partial.get('clue_id'))
        self.assertIsNone(partial.get('publication_error'))
        self.assertFalse(partial['snapshot']['enabled'])
        self.assertEqual(partial['snapshot']['result_destination'],'runs')
        self.assertEqual(partial['snapshot']['execution_trigger'],'manual')
        output=json.loads(partial['output'])
        self.assertEqual(output['processed'],1)
        self.assertEqual(output['deferred'],0)
        self.assertEqual(output['errors'],[])
        self.assertFalse(output['has_more'])
        self.assertEqual(len(output['clues']),1)
        self.assertEqual(output['format']['dropped_clues'],1)
        self.assertEqual(output['dropped'][0]['reason'],'手抄引用未精确命中本批 evidence')
        self.assertEqual(primary_and_repair.call_count,1)
        self.assertEqual(self.api('/api/clues/wechat/status')['processed_versions'],1)
        with pipeline.db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM seen').fetchone()[0],1)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM retry_documents').fetchone()[0],0)
        saved=self.api('/api/clues/'+output['clues'][0])
        self.assertEqual(saved['status'],'candidate')
        self.assertFalse(saved['event_id'])
        self.assertEqual(saved['entries_total'],1)
        evidence=saved['entries'][0]['evidence'][0]
        source=self.api('/api/knowledge/document/'+evidence['document_id'])
        self.assertEqual(source['id'],self.doc['id'])
        self.assertEqual(evidence['hash'],source['hash'])
        self.assertEqual(evidence['excerpt'],source['body'][evidence['start']:evidence['end']])
        self.assertEqual(evidence['excerpt'],self.doc['body'])
        self.assertIn('/knowledge.html?document='+source['id'],evidence['origin_url'])
        records=self.api('/api/schedules/runs?'+urllib.parse.urlencode(
            {'status':'succeeded','task_id':started['task_id'],'page':1,'page_size':10}))
        self.assertEqual(records['total'],1)
        self.assertEqual([item['id'] for item in records['runs']],[started['run_id']])
        self.assertEqual(records['runs'][0]['status'],'succeeded')
        self.assertEqual(self.api('/api/events')['counts']['total'],0)
        self.assertEqual(self.api('/api/events?view=mine')['counts']['mine_actions'],0)
        publisher.assert_not_called()

        # A later pass over the same message must not duplicate the saved clue.
        recovered_pi=Mock(return_value=json.dumps({'clues':[valid]},ensure_ascii=False))
        pipeline.call_pi=recovered_pi
        retried=self.api('/api/schedules/'+started['task_id']+'/run',{})
        recovered=finished(retried['id'])
        self.assertEqual(recovered['status'],'succeeded',recovered['output'])
        self.assertEqual(recovered['exit_code'],0)
        self.assertIsNone(recovered.get('clue_id'))
        recovery=json.loads(recovered['output'])
        self.assertEqual(recovery['processed'],0)
        self.assertEqual(recovery['clues'],[])
        self.assertEqual(recovery['deferred'],0)
        self.assertEqual(recovery['errors'],[])
        self.assertFalse(recovery['has_more'])
        recovered_pi.assert_not_called()
        self.assertEqual(self.api('/api/clues')['total'],1)
        self.assertEqual(self.api('/api/clues/'+saved['id'])['entries_total'],1)
        self.assertEqual(self.api('/api/clues/wechat/status')['processed_versions'],1)
        self.assertEqual(pipeline.retry_queue(config['model']),[])
        with pipeline.db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM seen').fetchone()[0],1)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM retry_documents').fetchone()[0],0)
        self.assertEqual(self.api('/api/events')['counts']['total'],0)
        with self.store.clues.db() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM entries WHERE producer_key LIKE 'task:%'").fetchone()[0],0)
        publisher.assert_not_called()

if __name__=='__main__':unittest.main()
