import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from server import Handler,Store,ThreadingHTTPServer
from scheduled_tasks import ScheduledTasks

class ScheduleHTTPTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.service=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        self.service.store=Store(self.tmp.name);self.service.schedules=ScheduledTasks(self.tmp.name,autostart=False)
        self.thread=threading.Thread(target=self.service.serve_forever,daemon=True);self.thread.start()
        self.base=f'http://127.0.0.1:{self.service.server_port}';self.opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    def tearDown(self):
        self.service.shutdown();self.service.schedules.close();self.service.server_close();self.thread.join();self.tmp.cleanup()
    def request(self,path,body=None,headers=None):
        h={'Origin':self.base,'X-Article-Studio':'1','Content-Type':'application/json'} if headers is None else headers
        req=urllib.request.Request(self.base+path,data=json.dumps(body).encode() if body is not None else None,headers=h)
        with self.opener.open(req) as r:return json.load(r)
    def test_save_list_run_and_history_use_existing_origin_protection(self):
        task=dict(name='HTTP 合成测试',kind='script',body='printf synthetic-http',cwd=self.tmp.name,frequency='interval',minutes=60,timeout=5,enabled=False,allow_commands=False)
        saved=self.request('/api/schedules/save',task);self.assertEqual(self.request('/api/schedules')['tasks'][0]['id'],saved['id'])
        self.assertEqual(self.request('/api/schedules/runs')['runs'],[])
        with self.assertRaises(urllib.error.HTTPError) as err:self.request('/api/schedules/'+saved['id']+'/run',{},headers={'Origin':'https://invalid.test','X-Article-Studio':'1'})
        self.assertEqual(err.exception.code,403);err.exception.close()
        run=self.request('/api/schedules/'+saved['id']+'/run',{});self.assertEqual(run['status'],'running')
        import time
        for _ in range(50):
            rows=self.request('/api/schedules/runs?task_id='+saved['id'])['runs']
            if rows and rows[0]['status']!='running':break
            time.sleep(.05)
        self.assertEqual(rows[0]['status'],'succeeded');self.assertEqual(rows[0]['output'],'synthetic-http')
    def test_static_ui_and_bad_payload(self):
        with self.opener.open(self.base+'/schedules.html') as r:self.assertIn('定时任务',r.read().decode())
        with self.assertRaises(urllib.error.HTTPError) as err:self.request('/api/schedules/save',[])
        self.assertEqual(err.exception.code,400);err.exception.close()

    def test_run_query_defaults_legacy_task_filter_and_full_filters(self):
        result={'runs':[], 'total':0, 'page':1, 'page_size':50, 'has_more':False}
        with patch.object(self.service.schedules,'runs',return_value=result) as query:
            self.assertEqual(self.request('/api/schedules/runs'),result)
            query.assert_called_once_with(task_id=None,q='',status=None,page=1,page_size=50)
            query.reset_mock()
            task_id='a'*32
            self.request('/api/schedules/runs?task_id='+task_id)
            query.assert_called_once_with(task_id=task_id,q='',status=None,page=1,page_size=50)
            query.reset_mock()
            parameters=urllib.parse.urlencode({'task_id':task_id,'q':'合成输出','status':'succeeded','page':2,'page_size':100})
            self.request('/api/schedules/runs?'+parameters)
            query.assert_called_once_with(task_id=task_id,q='合成输出',status='succeeded',page=2,page_size=100)
            query.reset_mock()
            self.request('/api/schedules/runs?task_id=&q=&status=')
            query.assert_called_once_with(task_id=None,q='',status=None,page=1,page_size=50)

    def test_run_query_rejects_ambiguous_invalid_and_out_of_range_parameters(self):
        invalid=('task_id=a&task_id=b','q=&q=second','status=failed&status=succeeded',
                 'page=1&page=2','page_size=50&page_size=100','unknown=',
                 'page=','page=wrong','page=1.5','page=0','page=-1',
                 'page_size=','page_size=wrong','page_size=1.5','page_size=0','page_size=101')
        with patch.object(self.service.schedules,'runs') as query:
            for parameters in invalid:
                with self.subTest(parameters=parameters),self.assertRaises(urllib.error.HTTPError) as err:
                    self.request('/api/schedules/runs?'+parameters)
                self.assertEqual(err.exception.code,400);err.exception.close()
            query.assert_not_called()

    def test_run_query_backend_validation_is_http_400(self):
        with patch.object(self.service.schedules,'runs',side_effect=ValueError('运行状态无效')) as query:
            with self.assertRaises(urllib.error.HTTPError) as err:
                self.request('/api/schedules/runs?status=unknown')
            self.assertEqual(err.exception.code,400)
            self.assertEqual(json.load(err.exception),{'error':'运行状态无效'});err.exception.close()
            query.assert_called_once_with(task_id=None,q='',status='unknown',page=1,page_size=50)

    def test_batch_route_forwards_payload_and_preserves_origin_protection(self):
        payload={'action':'pause','tasks':[{'id':'a'*32,'revision':1}]}
        result={'ok':True}
        with patch.object(self.service.schedules,'batch',create=True,return_value=result) as batch:
            with self.assertRaises(urllib.error.HTTPError) as err:
                self.request('/api/schedules/batch',payload,headers={'Origin':'https://invalid.test','X-Article-Studio':'1'})
            self.assertEqual(err.exception.code,403);err.exception.close();batch.assert_not_called()
            self.assertEqual(self.request('/api/schedules/batch',payload),result)
            batch.assert_called_once_with(payload)

    def test_batch_backend_validation_is_http_400(self):
        payload={'action':'run','tasks':[{'id':'a'*32,'revision':1}]}
        with patch.object(self.service.schedules,'batch',create=True,side_effect=ValueError('批量操作仅支持暂停或启用')) as batch:
            with self.assertRaises(urllib.error.HTTPError) as err:
                self.request('/api/schedules/batch',payload)
            self.assertEqual(err.exception.code,400)
            self.assertEqual(json.load(err.exception),{'error':'批量操作仅支持暂停或启用'});err.exception.close()
            batch.assert_called_once_with(payload)

    def test_batch_pause_and_resume_use_temporary_tasks(self):
        saved=[self.request('/api/schedules/save',dict(name='HTTP 合成批量 '+str(n),kind='script',body='true',cwd=self.tmp.name,frequency='interval',minutes=60,timeout=5,enabled=False,allow_commands=False)) for n in range(2)]
        self.request('/api/schedules/batch',{'action':'resume','tasks':saved})
        tasks=self.request('/api/schedules')['tasks']
        self.assertTrue(all(task['enabled'] and task['revision']==2 for task in tasks))
        self.assertTrue(all(task['next_run'] is not None for task in tasks))
        self.request('/api/schedules/batch',{'action':'pause','tasks':[{'id':task['id'],'revision':task['revision']} for task in tasks]})
        tasks=self.request('/api/schedules')['tasks']
        self.assertTrue(all(not task['enabled'] and task['revision']==3 for task in tasks))
        self.assertTrue(all(task['next_run'] is None for task in tasks))
        self.assertEqual(self.request('/api/schedules/runs')['runs'],[])

    def test_batch_rejects_unsafe_actions_without_changing_tasks(self):
        saved=self.request('/api/schedules/save',dict(name='HTTP 合成批量保护',kind='script',body='true',cwd=self.tmp.name,frequency='interval',minutes=60,timeout=5,enabled=False,allow_commands=False))
        for action in ('run','delete'):
            with self.subTest(action=action),self.assertRaises(urllib.error.HTTPError) as err:
                self.request('/api/schedules/batch',{'action':action,'tasks':[saved]})
            self.assertEqual(err.exception.code,400);err.exception.close()
        task=self.request('/api/schedules')['tasks'][0]
        self.assertEqual((task['id'],task['revision'],task['enabled']),(saved['id'],1,False))
        self.assertEqual(self.request('/api/schedules/runs')['runs'],[])

if __name__=='__main__':unittest.main()
