import json
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import urllib.request
import urllib.error
from server import Store, Handler, ThreadingHTTPServer
from knowledge_store import Knowledge
from scheduled_tasks import ScheduledTasks

class GitHubAPITests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.store=Store(self.tmp.name)
        self.store._knowledge=Knowledge(self.store,sources=[])
        self.srv=ThreadingHTTPServer(('127.0.0.1',0),Handler);self.srv.store=self.store
        self.srv.schedules=ScheduledTasks(self.tmp.name,autostart=False)
        self.srv.schedules.assistant=SimpleNamespace(settings=lambda:{'profiles':[]})
        self.called=[]
        def analyze(*args):
            self.called.append(args[0]);return 'succeeded',0,json.dumps({'processed':0,'clues':[]})
        self.srv.schedules.github_analyzer=analyze
        self.thread=threading.Thread(target=self.srv.serve_forever,daemon=True);self.thread.start()
        self.base=f'http://127.0.0.1:{self.srv.server_port}'
        self.opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    def tearDown(self):
        self.srv.shutdown();self.srv.schedules.close();self.srv.server_close();self.thread.join();self.tmp.cleanup()
    def post(self,path,data,headers=None):
        req=urllib.request.Request(self.base+path,data=json.dumps(data).encode(),headers=headers or {'Origin':self.base,'X-Article-Studio':'1','Content-Type':'application/json'})
        with self.opener.open(req) as r:return json.load(r)
    def test_run_route_claims_and_executes_instead_of_only_saving(self):
        with patch.object(self.srv.schedules,'model_config',return_value={'model':'synthetic'}):
            run=self.post('/api/github/run',{'profile_id':'synthetic','enabled':False,'minutes':180})
            for _ in range(100):
                row=self.srv.schedules.get_run(run['id'])
                if row['status']!='running':break
                time.sleep(.01)
        self.assertEqual(row['status'],'succeeded');self.assertEqual(self.called,[run['id']])
        snapshot=row['snapshot'];self.assertEqual(snapshot['kind'],'github_clues');self.assertFalse(snapshot['allow_commands']);self.assertEqual(snapshot['result_destination'],'runs')
        with patch.object(self.srv.schedules,'model_config',return_value={'model':'synthetic'}):
            self.post('/api/github/run',{'profile_id':'synthetic','enabled':False,'minutes':240})
        self.assertEqual(len(self.srv.schedules.listing()['tasks']),1)
    def test_github_routes_reject_other_origin(self):
        with self.assertRaises(urllib.error.HTTPError) as e:self.post('/api/github/source',{},headers={'Origin':'https://invalid.example'})
        self.assertEqual(e.exception.code,403);e.exception.close()
    def test_specialized_executor_never_gets_shell_or_extra_tools(self):
        payload={'name':'GitHub','kind':'github_clues','body':'read','cwd':self.tmp.name,'frequency':'interval','minutes':180,'at':'08:00','weekdays':[0], 'ends_at':None,'timeout':300,'enabled':False,'allow_commands':True,'profile_id':'synthetic'}
        with self.assertRaises(ValueError):self.srv.schedules.save(payload)
        payload.update(allow_commands=False,assistant_id='workbench',tool_groups=['ssh'])
        with self.assertRaises(ValueError):self.srv.schedules.save(payload)

if __name__=='__main__':unittest.main()
