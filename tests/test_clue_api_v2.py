import json
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
import urllib.request
import urllib.error
from server import Handler,ThreadingHTTPServer
from clue_store_v2 import schema,digest,assert_write_path
from clue_ingest import import_document

class APIV2Tests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='clue-api-v2-',dir='/tmp');self.workspace=Path(self.tmp.name);(self.workspace/'clues').mkdir();self.path=self.workspace/'clues/clues.db';assert_write_path(self.path)
        self.server=ThreadingHTTPServer(('127.0.0.1',0),Handler);self.server.store=SimpleNamespace(workspace=self.workspace)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start();self.base='http://127.0.0.1:'+str(self.server.server_port)
    def tearDown(self):self.server.shutdown();self.server.server_close();self.thread.join();self.tmp.cleanup()
    def get(self,p):
        with urllib.request.urlopen(self.base+p) as r:return json.load(r)
    def seed(self):
        import sqlite3
        sqlite3.connect(self.path).close();schema(self.path,'api',['方向'+str(i) for i in range(9)])
        d=dict(items=[dict(id='123',full_text='AI 测试正文',content_complete=True,views=9007199254740994)])
        import_document(self.path,'bookmarks','fixture.json',d,digest(d),'api')
    def test_missing_shadow_honest_not_created(self):
        self.assertFalse(self.get('/api/clue-inbox')['ready']);self.assertFalse(self.path.exists())
        with self.assertRaises(urllib.error.HTTPError) as cm:self.get('/api/clue-objects/missing')
        self.assertEqual(cm.exception.code,404)
    def test_actual_http_read_chain(self):
        self.seed();listing=self.get('/api/clue-inbox?source=bookmarks&limit=1');self.assertTrue(listing['ready']);self.assertEqual(len(listing['items']),1)
        d=self.get('/api/clue-objects/'+listing['items'][0]['id']);self.assertEqual(d['canonical_key'],'x:123');self.assertEqual(d['observations'][0]['raw_metrics']['views'],'9007199254740994');self.assertEqual(d['content_versions'][0]['body'],'AI 测试正文')
        self.assertEqual(self.get('/api/clue-objects/x%3A123')['id'],d['id'])
    def test_reader_question_fixture_http_chain(self):
        self.seed()
        payload=dict(generated_at='2026-10-30T00:00:00Z',items=[dict(cluster_id='reader-1',question='如何配置Agent记忆',parent_post_url='https://x.com/a/status/123',frequency=7,replies=[dict(id='r1',url='https://x.com/a/status/124')])])
        fixture=self.workspace/'reader-question.json';fixture.write_text(json.dumps(payload,ensure_ascii=False))
        import_document(self.path,'questions',fixture,json.loads(fixture.read_text()),digest(payload),'api')
        listing=self.get('/api/clue-inbox?source=questions');self.assertEqual(len(listing['items']),1)
        value=self.get('/api/clue-objects/'+listing['items'][0]['id']);self.assertEqual(value['canonical_key'],'question:reader-1');self.assertEqual(value['content_versions'][0]['rich']['frequency'],7);self.assertEqual(len(value['observations']),1);self.assertEqual(value['channels'][0]['channel'],'reader:questions')

    def test_query_validation(self):
        self.seed()
        for p in ['/api/clue-inbox?limit=0','/api/clue-inbox?limit=1&limit=2','/api/clue-inbox?x=1','/api/clue-inbox?cursor=bad','/api/clue-inbox?status=madeup','/api/clue-objects/missing?x=1']:
            with self.subTest(p=p):
                with self.assertRaises(urllib.error.HTTPError) as cm:self.get(p)
                self.assertEqual(cm.exception.code,400)
    def test_post_not_added(self):
        self.seed()
        for p in ['/api/clue-inbox','/api/clue-objects/x:123']:
            with self.assertRaises(urllib.error.HTTPError) as cm:urllib.request.urlopen(urllib.request.Request(self.base+p,data=b'{}',headers={'Origin':self.base,'X-Article-Studio':'1','Content-Type':'application/json'}))
            self.assertIn(cm.exception.code,(400,404))

if __name__=='__main__':unittest.main()
