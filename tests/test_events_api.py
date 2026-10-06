import json
from pathlib import Path
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request,urlopen

from server import Store,Handler
from knowledge_store import Knowledge


class EventApiTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.store=Store(Path(self.temp.name)/'studio')
        self.store._knowledge=Knowledge(self.store,[])
        self.doc=self.store.knowledge.add_note({'title':'项目约定','body':'😀周五交付；邮件明确改至周一','url':'https://example.com/project'})
        self.server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        self.server.store=self.store
        self.base='http://127.0.0.1:'+str(self.server.server_port)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join(timeout=3)
        self.temp.cleanup()

    def request(self,path,payload=None,origin=True):
        headers={'Origin':self.base,'X-Article-Studio':'1','Content-Type':'application/json'} if origin else {'Content-Type':'application/json'}
        req=Request(self.base+path,data=json.dumps(payload).encode() if payload is not None else None,headers=headers)
        with urlopen(req,timeout=5) as r:
            return json.load(r)

    def create(self):
        doc=self.store.knowledge.document(self.doc['id'])
        return self.request('/api/events',{'title':'项目交付','priority':'important','needs_confirmation':False,
            'items':[{'id':doc['id'],'hash':doc['hash'],'start':1,'end':len(doc['body'])}],'origin':'manual','reason':'用户确认约定'})

    def test_source_selection_event_action_reschedule_cancel_preserves_evidence_and_history(self):
        event=self.create();eid=event['id'];evidence=event['evidence'][0]
        self.assertEqual(evidence['excerpt'],'周五交付；邮件明确改至周一')
        saved=self.request('/api/events/'+eid+'/actions',{'summary':'交付项目','owner':'我','owner_is_self':True,'due_at':'2026-10-09T09:00:00+08:00','status':'open','evidence_ids':[evidence['id']]})
        a=saved['actions'][0]
        shifted=self.request('/api/events/'+eid+'/actions',{'id':a['id'],'revision':a['revision'],'due_at':'2026-10-12T09:00:00+08:00','reason':'根据新约定改期','origin':'manual'})
        a=shifted['actions'][0]
        final=self.request('/api/events/'+eid+'/actions',{'id':a['id'],'revision':a['revision'],'status':'cancelled','reason':'用户确认不再需要交付','origin':'manual'})
        self.assertEqual(len(final['actions']),1)
        self.assertEqual(final['actions'][0]['status'],'cancelled')
        self.assertEqual(final['evidence'][0]['excerpt'],evidence['excerpt'])
        self.assertEqual(len(final['history']),4)
        self.assertEqual(self.request('/api/events?view=calendar&limit=8')['events'],[])
        self.assertEqual(self.request('/api/events?view=important&limit=8')['counts']['important'],1)

    def test_conflicts_are_http_409_and_wrong_origin_cannot_create(self):
        with self.assertRaises(HTTPError) as exc:
            self.request('/api/events',{'title':'forged'},origin=False)
        self.assertEqual(exc.exception.code,403)
        event=self.create()
        self.request('/api/events',{'id':event['id'],'revision':event['revision'],'title':'确认项目','origin':'manual','reason':'补充标题'})
        with self.assertRaises(HTTPError) as exc:
            self.request('/api/events',{'id':event['id'],'revision':event['revision'],'title':'旧页覆盖','origin':'manual','reason':'陈旧编辑'})
        self.assertEqual(exc.exception.code,409)
        self.assertEqual(self.request('/api/events/'+event['id'])['title'],'确认项目')

    def test_explicit_limit_and_invalid_filter_are_checked(self):
        self.assertEqual(self.request('/api/events?limit=30')['counts']['total'],0)
        for path in ['/api/events?limit=NaN','/api/events?limit=1000','/api/events?view=unknown']:
            with self.assertRaises(HTTPError) as exc:
                self.request(path)
            self.assertEqual(exc.exception.code,400)

if __name__=='__main__':
    unittest.main()
