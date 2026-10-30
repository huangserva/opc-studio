import hashlib
import json
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.request import Request,urlopen
from http.server import ThreadingHTTPServer
from library_search import build_catalog
from items_store import ItemsStore,canonical_id,from_document,library_search,readonly,upsert_from_reference

LONG='首行😀 <script>alert(1)</script> <img src=x onerror=alert(1)> &amp;\n\n'+'完整中文正文'*450+'\n末行　\t'


def knowledge_fixture(root):
    path=Path(root)/'knowledge/knowledge.db';path.parent.mkdir(parents=True,exist_ok=True)
    c=sqlite3.connect(path)
    c.executescript("""CREATE TABLE documents(id TEXT PRIMARY KEY,source TEXT,kind TEXT,title TEXT,body TEXT,url TEXT,modified TEXT,tags TEXT,hash TEXT,provenance TEXT);
    CREATE TABLE document_trash(document_id TEXT PRIMARY KEY,deleted_at TEXT);
    CREATE VIRTUAL TABLE document_fts USING fts5(title,body,content='documents',content_rowid='rowid',tokenize='trigram');
    CREATE TRIGGER ai AFTER INSERT ON documents BEGIN INSERT INTO document_fts(rowid,title,body) VALUES(new.rowid,new.title,new.body);END;""")
    c.execute('INSERT INTO documents VALUES(?,?,?,?,?,?,?,?,?,?)',('a'*32,'manual','article','AI agent <img src=x onerror=alert(1)>',LONG+' agent source','https://x.com/fixture/status/100','2026-10-29','[]','hash','{}'))
    c.commit();c.close();return path


class ItemsTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='items-unit-',dir='/tmp');self.root=Path(self.temp.name);self.store=ItemsStore(self.root)
    def tearDown(self):self.temp.cleanup()
    def item(self,**kw):return dict(id='x:100',url='https://x.com/fixture/status/100',sources=['A'],title='agent 标题',full_text=LONG,note='个人理由',tags=['agent'],**kw)
    def test_normalized_ids(self):
        for url,want in [('https://twitter.com/user/status/100?s=20','x:100'),('https://github.com/Owner/Repo.git/issues/1','github:owner/repo'),('https://www.douyin.com/video/10','douyin:10'),('https://www.xiaohongshu.com/explore/aa','xhs:aa')]:self.assertEqual(canonical_id({'url':url}),want)
        self.assertTrue(canonical_id({'url':'https://mp.weixin.qq.com/s?b=2&a=1'}).startswith('wechat:'))
        self.assertEqual(canonical_id({'legacy_id':'old'}),'kb:old')
        self.assertEqual(canonical_id({'kind':'card','prompt_sha':'abc'}),'card:abc')
        self.assertEqual(canonical_id({'url':'https://example.com/a?utm_x=y&b=2#a'}),canonical_id({'url':'https://EXAMPLE.com/a?b=2'}))
    def test_source_and_legacy_merge_preserves_user_fields(self):
        first=self.item(legacy_refs=[{'store':'knowledge','id':'old'}],status='used',feedback='accurate');self.store.upsert(first)
        second={**first,'sources':['G'],'legacy_refs':[{'store':'intake','id':'drop'}],'note':'覆盖理由','status':'new','feedback':'inaccurate'}
        saved=self.store.upsert(second)
        self.assertEqual(saved['sources'],['A','G']);self.assertEqual(len(saved['legacy_refs']),2)
        self.assertEqual((saved['note'],saved['status'],saved['feedback']),('个人理由','used','accurate'))
    def test_replay_hash_is_identical(self):
        a=self.store.upsert(self.item());b=self.store.upsert(self.item());self.assertEqual(a,b)
    def test_long_text_exact_and_summary(self):
        self.store.upsert(self.item());self.assertEqual(self.store.detail('x:100')['full_text'],LONG)
        result=self.store.listing();self.assertNotIn('full_text',result['items'][0]);self.assertNotIn(LONG,json.dumps(result))
    def test_search_title_body_note_author_tags(self):
        self.store.upsert(self.item(author={'handle':'fixture_handle'}))
        for text in ['agent','完整中文正文','个人理由','fixture_handle','完整','理由','fi','a']:
            self.assertEqual(self.store.listing({'q':[text]})['total'],1,text)
        self.assertEqual(self.store.listing({'q':['" OR *']})['total'],0)
    def test_filters_paging(self):
        for i in range(53):self.store.upsert({**self.item(),'id':'x:'+str(i),'url':'https://x.com/f/status/'+str(i),'kind':'post','source':'A'})
        self.assertEqual(len(self.store.listing({'page':['2']})['items']),3)
        for query in ({'source':['A']},{'kind':['post']},{'tag':['agent']},{'status':['kept']}):self.assertEqual(self.store.listing(query)['total'],53)
        self.assertEqual(self.store.listing({'source':['missing']})['total'],0)
    def test_trash_restore_exact_then_delete_fts(self):
        before=self.store.upsert(self.item())
        with self.assertRaisesRegex(ValueError,'回收站'):self.store.action('x:100','delete')
        self.store.action('x:100','trash');self.assertEqual(self.store.listing()['total'],0)
        self.assertEqual(self.store.listing({'trashed':['1']})['total'],1)
        self.assertEqual(self.store.action('x:100','restore'),before)
        self.store.action('x:100','trash');self.store.action('x:100','delete')
        self.assertEqual(self.store.listing({'q':['agent'],'trashed':['1']})['total'],0)
        with self.store.db() as c:self.assertEqual(c.execute("SELECT count(*) FROM items_fts WHERE items_fts MATCH 'agent'").fetchone()[0],0)
    def test_parent_reference_blocks_and_cards_cascade(self):
        self.store.upsert(self.item());self.store.upsert({'legacy_id':'child','parent_id':'x:100','full_text':'reference'})
        self.store.action('x:100','trash')
        with self.assertRaisesRegex(ValueError,'其他素材引用'):self.store.action('x:100','delete')
        self.store.action('kb:child','trash');self.store.action('kb:child','delete')
        self.store.upsert({'kind':'card','prompt_sha':'sha','parent_id':'x:100','full_text':'agent card'})
        self.store.action('x:100','restore');self.store.action('x:100','trash')
        self.assertTrue(self.store.detail('card:sha')['trashed_at'])
        self.store.action('x:100','delete')
        with self.assertRaises(ValueError):self.store.detail('card:sha')
    def test_note_edit_and_use(self):
        self.store.upsert(self.item());self.store.action('x:100','note',{'note':'更新理由 <img src=x>'})
        self.assertEqual(self.store.listing({'q':['更新理由']})['total'],1)
        self.assertEqual(self.store.action('x:100','use')['status'],'used')
    def test_concurrent_same_and_different_ids(self):
        def same(source):return self.store.upsert({**self.item(),'sources':[source]})
        with ThreadPoolExecutor(max_workers=2) as pool:list(pool.map(same,['G','A']))
        self.assertEqual(set(self.store.detail('x:100')['sources']),{'G','A'})
        def different(id):return self.store.upsert({'id':'x:'+id,'sources':[id]})
        with ThreadPoolExecutor(max_workers=2) as pool:list(pool.map(different,['101','102']))
        self.assertEqual(self.store.listing()['total'],3)
    def test_readonly_library_no_mutation_or_body_projection(self):
        path=knowledge_fixture(self.root);before=hashlib.sha256(path.read_bytes()).hexdigest()
        result=library_search(self.root,{'q':['agent']})
        self.assertEqual(result['total'],1);self.assertNotIn('body',result['items'][0]);self.assertIn('\x01',result['items'][0]['snippet'])
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),before)
        with readonly(path) as c:
            with self.assertRaises(sqlite3.OperationalError):c.execute('DELETE FROM documents')
    def test_short_query_and_trash_exclusion(self):
        path=knowledge_fixture(self.root)
        build_catalog(self.root)
        result=library_search(self.root,{'q':['AI']});self.assertTrue(result['short_query']);self.assertEqual(result['total'],1)
        c=sqlite3.connect(path);c.execute('INSERT INTO document_trash VALUES(?,?)',('a'*32,'now'));c.commit();c.close()
        self.assertEqual(library_search(self.root,{'q':['agent']})['total'],0)
    def test_short_scan_bounds_nonmatches_and_reports_partial_results(self):
        path=knowledge_fixture(self.root)
        with sqlite3.connect(path) as c:
            for i,title in enumerate(['旧鲸资料','普通资料','新鲸资料']):
                c.execute('INSERT INTO documents(id,source,title) VALUES(?,?,?)',(str(i),'manual',title))
        before=hashlib.sha256(path.read_bytes()).hexdigest()
        with patch('library_search.catalog_search',return_value=(None,'building')), patch('items_store.SHORT_SEARCH_ROWS',2):
            r=library_search(self.root,{'q':['鲸']})
            self.assertEqual([x['id'] for x in r['items']],['2'])
            self.assertTrue(r['partial']);self.assertEqual(r['scanned_count'],2)
            self.assertEqual((r['total_label'],r['has_more']),('≥1',False))
            empty=library_search(self.root,{'q':['龘'],'page':['2']})
            self.assertEqual(empty['items'],[]);self.assertTrue(empty['partial'])
            self.assertEqual((empty['scanned_count'],empty['total_label']),(2,'≥0'))
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),before)
        self.assertEqual(library_search(self.root,{'q':['旧鲸资料']})['items'][0]['id'],'0')
    def test_short_deadline_returns_honest_empty_instead_of_error(self):
        knowledge_fixture(self.root)
        with patch('library_search.catalog_search',return_value=(None,'unavailable')), patch('items_store.SHORT_SEARCH_SECONDS',0):
            r=library_search(self.root,{'q':['鲸']})
        self.assertTrue(r['partial']);self.assertEqual(r['items'],[])
        self.assertEqual(r['total_label'],'≥0')
    def test_short_paging_source_trash_and_literal_matching(self):
        path=knowledge_fixture(self.root)
        with sqlite3.connect(path) as c:
            for i in range(120):
                c.execute('INSERT INTO documents(id,source,title) VALUES(?,?,?)',(str(i),'manual','模型 100%_\\'+str(i)))
            c.execute('INSERT INTO document_trash VALUES(?,?)',('119','now'))
        build_catalog(self.root)
        first=library_search(self.root,{'q':['模型']})
        second=library_search(self.root,{'q':['模型'],'page':['2']})
        self.assertEqual([r['id'] for r in first['items']],[str(i) for i in range(118,68,-1)])
        self.assertEqual([r['id'] for r in second['items']],[str(i) for i in range(68,18,-1)])
        self.assertTrue(first['has_more']);self.assertEqual(first['total_label'],'≥50')
        self.assertEqual(second['total_label'],'≥100')
        for word in ['%','_','\\']:
            self.assertEqual(len(library_search(self.root,{'q':[word]})['items']),50)
        excluded=library_search(self.root,{'q':['模型'],'source':['github']})
        self.assertEqual(excluded['items'],[]);self.assertFalse(excluded['partial'])
    def test_short_does_not_hide_sql_errors(self):
        path=knowledge_fixture(self.root)
        with sqlite3.connect(path) as c:c.execute('DROP TABLE document_trash')
        with self.assertRaises(sqlite3.OperationalError):library_search(self.root,{'q':['AI']})
    def test_catalog_finds_old_matches_beyond_fallback_window(self):
        path=knowledge_fixture(self.root)
        with sqlite3.connect(path) as c:
            c.execute('INSERT INTO documents(id,source,title) VALUES(?,?,?)',('old','manual','微信模型'))
            c.executemany('INSERT INTO documents(id,source,title) VALUES(?,?,?)',[(str(i),'manual','普通资料') for i in range(8300)])
        build_catalog(self.root)
        with patch('items_store.SHORT_SEARCH_ROWS',2):
            for word in ['微信','模型']:
                r=library_search(self.root,{'q':[word]})
                self.assertEqual([x['id'] for x in r['items']],['old'])
                self.assertEqual((r['partial'],r['catalog_state'],r['total_label']),(False,'ready','≥1'))
        with sqlite3.connect(self.root/'items/research_catalog.db') as c:
            columns=[r[1] for r in c.execute('PRAGMA table_info(titles)')]
        self.assertEqual(columns,['rowid','source','title','modified','trashed'])

    def test_catalog_incremental_and_same_count_trash_replacement(self):
        path=knowledge_fixture(self.root);build_catalog(self.root)
        with sqlite3.connect(path) as c:
            c.executemany('INSERT INTO documents(id,source,title) VALUES(?,?,?)',[('one','manual','鲸鱼一'),('two','manual','鲸鱼二')])
            c.execute('INSERT INTO document_trash VALUES(?,?)',('one','now'))
        with patch('library_search.SYNC_INTERVAL',0):
            r=library_search(self.root,{'q':['鲸']})
            self.assertEqual([x['id'] for x in r['items']],['two'])
            with sqlite3.connect(path) as c:
                c.execute('DELETE FROM document_trash')
                c.execute('INSERT INTO document_trash VALUES(?,?)',('two','now'))
            r=library_search(self.root,{'q':['鲸']})
            self.assertEqual([x['id'] for x in r['items']],['one'])
            with sqlite3.connect(path) as c:c.execute('DELETE FROM document_trash')
            self.assertEqual(len(library_search(self.root,{'q':['鲸']})['items']),2)

    def test_catalog_missing_build_is_nonblocking_and_honest(self):
        knowledge_fixture(self.root)
        with patch('library_search.threading.Thread') as thread:
            r=library_search(self.root,{'q':['AI']})
            self.assertEqual(r['catalog_state'],'building')
            thread.return_value.start.assert_called_once()
        # Fake thread did not run; release its test-only state ownership.
        from library_search import _states,_paths
        state=_states.pop(str(_paths(self.root)[1]));state['lock'].release()

    def test_catalog_corruption_falls_back_then_rebuilds(self):
        knowledge_fixture(self.root)
        path=self.root/'items/research_catalog.db';path.parent.mkdir();path.write_bytes(b'broken sqlite')
        with patch('library_search.threading.Thread') as thread:
            with self.assertLogs('library_search',level='WARNING'):
                r=library_search(self.root,{'q':['AI']})
            self.assertEqual(r['catalog_state'],'building');self.assertEqual(r['items'][0]['id'],'a'*32)
            thread.return_value.start.assert_called_once()
        from library_search import _states,_paths
        state=_states.pop(str(_paths(self.root)[1]));state['lock'].release()
        build_catalog(self.root)
        self.assertEqual(library_search(self.root,{'q':['AI']})['catalog_state'],'ready')

    def test_catalog_sync_failure_keeps_available_results(self):
        knowledge_fixture(self.root);build_catalog(self.root)
        with patch('library_search._sync',side_effect=sqlite3.OperationalError('fixture sync failure')):
            with self.assertLogs('library_search',level='WARNING'):
                r=library_search(self.root,{'q':['AI']})
        self.assertEqual((r['catalog_state'],len(r['items'])),('ready',1))

    def test_catalog_incremental_respects_deadline_and_watermark(self):
        path=knowledge_fixture(self.root);build_catalog(self.root)
        with sqlite3.connect(path) as c:
            c.executemany('INSERT INTO documents(id,source,title) VALUES(?,?,?)',[(str(i),'manual','模型') for i in range(1100)])
        with patch('library_search.SYNC_INTERVAL',0),patch('library_search.SYNC_SECONDS',0):
            self.assertEqual(library_search(self.root,{'q':['模型']})['items'],[])
        with patch('library_search.SYNC_INTERVAL',0):
            self.assertEqual(len(library_search(self.root,{'q':['模型']})['items']),50)
        with sqlite3.connect(self.root/'items/research_catalog.db') as c:
            self.assertEqual(json.loads(c.execute('SELECT value FROM metadata').fetchone()[0])['watermark'],1101)

    def test_mirror_failure_keeps_original_document(self):
        doc={'id':'a'*32,'source':'manual','body':LONG,'title':'reference','url':'https://x.com/f/status/100'}
        self.store.upsert(self.item());self.store.path.chmod(0o444)
        with self.assertLogs('items_store',level='ERROR'):result=upsert_from_reference(self.root,doc)
        self.assertIs(result,doc);self.assertEqual(self.store.detail('x:100')['title'],'agent 标题')
    def test_bad_queries_and_payloads(self):
        for query in ({'page':['0']},{'q':['a','b']},{'trashed':['bad']},{'unknown':['x']}):
            with self.assertRaises(ValueError):self.store.listing(query)
        for data in (None,{}, {'title':[]},{'id':'x:a','sources':[{}]},{'id':'x:a','note':3}):
            with self.assertRaises(ValueError):self.store.upsert(data)


class TodayItemsIntegrationTests(unittest.TestCase):
    def test_today_keep_and_use_are_visible_in_library(self):
        from tests.test_today_view import TodayTests
        import today_view
        fixture=TodayTests();fixture.setUp()
        try:
            today_view.save_feedback(fixture.workspace,{'id':'x:1','action':'keep'})
            store=ItemsStore(fixture.workspace)
            self.assertEqual(store.detail('x:1')['full_text'],fixture.detail()['full_text'])
            self.assertIn('x:1',[i['id'] for i in store.listing()['items']])
            today_view.save_feedback(fixture.workspace,{'id':'x:1','action':'use'})
            self.assertEqual(store.detail('x:1')['status'],'used')
            self.assertTrue(today_view.feedback_path(fixture.workspace).exists())
        finally:fixture.tearDown()


class ItemsHTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from server import Handler
        from knowledge_store import Knowledge
        cls.temp=tempfile.TemporaryDirectory(prefix='items-http-',dir='/tmp');cls.root=Path(cls.temp.name)
        # Original reference API is deliberately writable only inside this fixture.
        cls.knowledge=Knowledge(SimpleNamespace(workspace=cls.root),sources=[])
        cls.server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        cls.server.store=SimpleNamespace(workspace=cls.root,knowledge=cls.knowledge)
        cls.thread=threading.Thread(target=cls.server.serve_forever,daemon=True);cls.thread.start()
        cls.base=f'http://127.0.0.1:{cls.server.server_port}'
    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown();cls.server.server_close();cls.thread.join();cls.temp.cleanup()
    def request(self,path,data=None,method='POST',headers=None):
        headers={'Origin':self.base,'X-Article-Studio':'1','Content-Type':'application/json'} if headers is None else headers
        request=Request(self.base+path,data=json.dumps(data or {}).encode() if method=='POST' else None,method=method,headers=headers)
        try:r=urlopen(request,timeout=5)
        except HTTPError as e:r=e
        return r.status,json.load(r)
    def test_all_mutations_require_origin_and_header(self):
        for method,path in [('POST','/api/items'),('POST','/api/items/x%3A100/trash'),('POST','/api/items/x%3A100/restore'),('POST','/api/items/x%3A100/note'),('POST','/api/items/x%3A100/use'),('DELETE','/api/items/x%3A100')]:
            for headers in ({'X-Article-Studio':'1'},{'Origin':'https://evil.invalid','X-Article-Studio':'1'},{'Origin':self.base}):
                self.assertEqual(self.request(path,{'id':'x:100'},method,headers)[0],403,(method,path,headers))
        self.assertFalse((self.root/'items/items.db').exists())
    def test_reference_api_readonly_mirror_still_succeeds(self):
        store=ItemsStore(self.root);store.upsert({'id':'x:fixture'});store.path.chmod(0o444)
        with self.assertLogs('items_store',level='ERROR'):
            status,doc=self.request('/api/knowledge/reference',{'title':'镜像失败不影响原接口','url':'https://example.com/readonly-mirror','reader_note':'保留原收藏'})
        self.assertEqual(status,201);self.assertEqual(doc['code'],'COLLECTED');self.assertEqual(self.knowledge.document(doc['id'])['title'],doc['title']);store.path.chmod(0o644)
    def test_concurrent_http_posts(self):
        def post(s):return self.request('/api/items',{'id':'x:parallel','sources':[s]})
        with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(post,['A','G']))
        self.assertEqual([r[0] for r in results],[201,201]);self.assertEqual(set(ItemsStore(self.root).detail('x:parallel')['sources']),{'A','G'})
        with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(lambda id:self.request('/api/items',{'id':id}),['x:101','x:102']))
        self.assertTrue(all(status==201 for status,_ in results))
    def test_http_trash_restore_delete(self):
        status,item=self.request('/api/items',{'id':'x:delete','title':'delete agent','full_text':LONG});self.assertEqual(status,201)
        path='/api/items/x%3Adelete'
        self.assertEqual(self.request(path,method='DELETE')[0],400)
        self.assertEqual(self.request(path+'/trash',{})[0],200)
        self.assertEqual(self.request(path+'/restore',{})[1]['full_text'],LONG)
        self.request(path+'/trash',{});self.assertEqual(self.request(path,method='DELETE')[0],200)

if __name__=='__main__':unittest.main()
