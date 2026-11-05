"""M3 upload entry: isolation, retries, whole-chain ledger/tag agreement."""
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch
import weknora_upload as U
from tests.test_weknora_push_longtext import DDL

TAGS={'隐私-公开':'public-tag','类型-长文·作品':'work-tag','类型-长文·外部参考':'ref-tag'}


def fixture(root):
    (root/'workspace/knowledge').mkdir(parents=True)
    (root/'workspace/weknora').mkdir(parents=True)
    (root/'coordination').mkdir()
    (root/'coordination/weknora-tags.json').write_text(json.dumps({'knowledge_base_id':U.KB,'tags':TAGS}))
    with sqlite3.connect(root/'workspace/knowledge/knowledge.db') as c:
        c.execute('CREATE TABLE documents(id,source,title,body,url,modified)')
        c.executemany('INSERT INTO documents VALUES(?,?,?,?,?,?)', [('one','articles','作品','原样正文\n末行','','date'),('two','external-references','外部参考','外部正文','','date')])
    with sqlite3.connect(root/'workspace/weknora/ledger.db') as c:
        c.execute(DDL)
        c.execute('INSERT INTO result_ledger VALUES(?,?,?,?,?,?,?)',('SD-other',U.KB,'other','otherhash','[]','date','active'))


class Fake:
    def __init__(self):
        self.config=SimpleNamespace(kb_id=U.KB,api_key='synthetic-upload-key')
        self.remote={};self.calls=[];self.fail=None;self.enter=None;self.release=None;self.bad_tags=False
    def _request(self,method,path,**kw):
        self.calls.append((method,path));return {'data':{'id':U.KB,'indexing_strategy':{'wiki_enabled':True}}}
    def list_tags(self,*args,**kw):
        self.calls.append(('GET','tags'));return {'data':[{'id':v,'name':k} for k,v in TAGS.items()]}
    def list_knowledge(self,*args,**kw):
        self.calls.append(('GET','list'));return {'data':list(self.remote.values())}
    def create_manual(self,title,content,tags,kb):
        self.calls.append(('POST','manual'))
        if self.enter:self.enter.set();self.release.wait(5)
        if self.fail=='reject':raise U.WeKnoraError('synthetic',400)
        if self.fail=='before':raise U.WeKnoraError('synthetic')
        kid='id-'+str(len(self.remote)+1)
        row={'id':kid,'title':title,'knowledge_base_id':kb,'metadata':{'content':content,'status':'publish'},'tags':[{'id':t} for t in ([] if self.bad_tags else tags)]}
        self.remote[kid]=row
        if self.fail=='after':raise U.WeKnoraError('synthetic')
        return {'data':row}
    def get_knowledge(self,kid):
        self.calls.append(('GET',kid));return {'data':self.remote[kid]}


class Tests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir='/tmp',prefix='m3-unit-');self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);fixture(self.root);self.client=Fake();self.s=U.UploadService(self.root,client_factory=lambda:self.client)
    def payload(self,*indices):
        rows=self.s.catalog()['items'];return {'documents':[{k:rows[i][k] for k in ('number','content_sha256')} for i in (indices or (0,))]}
    def creates(self):return sum(m=='POST' for m,p in self.client.calls)
    def test_catalog_pure_local_source_unchanged(self):
        p=self.root/'workspace/knowledge/knowledge.db';b=p.read_bytes();self.assertEqual(len(self.s.catalog()['items']),2);self.assertEqual(self.client.calls,[]);self.assertEqual(p.read_bytes(),b)
    def test_three_clicks_content_hash_idempotency(self):
        p=self.payload();self.assertEqual(self.s.upload(p)['results'][0]['action'],'created')
        calls=len(self.client.calls)
        for _ in range(3):self.assertEqual(self.s.upload(p)['results'][0]['action'],'skipped')
        self.assertEqual(self.creates(),1);self.assertEqual(len(self.client.calls),calls)
    def test_concurrent_process_service_lock(self):
        self.client.enter=threading.Event();self.client.release=threading.Event();p=self.payload();out=[]
        t=threading.Thread(target=lambda:out.append(self.s.upload(p)));t.start();self.assertTrue(self.client.enter.wait(3))
        try:
            with self.assertRaises(U.UploadBusy):U.UploadService(self.root,client_factory=lambda:self.client).upload(p)
        finally:self.client.release.set();t.join()
        self.assertEqual(self.creates(),1);self.assertEqual(len(out),1)
    def test_timeout_after_remote_accept_reconciles_no_duplicate(self):
        p=self.payload();self.client.fail='after';r=self.s.upload(p);self.assertEqual(r['results'][0]['action'],'failed');self.assertNotIn(p['documents'][0]['number'],self.s.rows())
        self.client.fail=None;self.assertEqual(self.s.upload(p)['results'][0]['action'],'recovered');self.assertEqual(self.creates(),1)
    def test_uncertain_missing_failclosed_without_second_post(self):
        p=self.payload();self.client.fail='before';self.s.upload(p);self.client.fail=None;self.assertEqual(self.s.upload(p)['results'][0]['action'],'failed');self.assertEqual(self.creates(),1);self.assertNotIn(p['documents'][0]['number'],self.s.rows())
    def test_rejected_can_retry_no_partial_ledger(self):
        p=self.payload();self.client.fail='reject';self.assertTrue(self.s.upload(p)['stopped']);self.assertNotIn(p['documents'][0]['number'],self.s.rows());self.client.fail=None;self.assertEqual(self.s.upload(p)['results'][0]['action'],'created');self.assertEqual(len(self.client.remote),1)
    def test_remote_accepted_ledger_commit_failure_recovers(self):
        p=self.payload()
        with patch.object(U.Ledger,'put',side_effect=sqlite3.OperationalError('disk full')):self.assertEqual(self.s.upload(p)['results'][0]['action'],'failed')
        self.assertNotIn(p['documents'][0]['number'],self.s.rows());self.assertEqual(self.s.upload(p)['results'][0]['action'],'recovered');self.assertEqual(self.creates(),1)
    def test_local_failure_one_item_other_continues(self):
        p=self.payload(0,1);original=U.Ledger.put
        def put(ledger,doc,*a,**kw):
            if doc.source=='articles':raise sqlite3.OperationalError('full')
            return original(ledger,doc,*a,**kw)
        with patch.object(U.Ledger,'put',put):r=self.s.upload(p)
        self.assertEqual([x['action'] for x in r['results']],['failed','created']);self.assertFalse(r['stopped']);self.assertEqual(len(self.client.remote),2)
    def test_http_error_stops_next_document(self):
        self.client.fail='reject';r=self.s.upload(self.payload(0,1));self.assertEqual([x['action'] for x in r['results']],['failed','not_attempted']);self.assertEqual(self.creates(),1)
    def test_remote_tag_ledger_threeway_consistent(self):
        r=self.s.upload(self.payload(0,1))
        for x in r['results']:
            row=self.s.rows()[x['number']];remote=self.client.remote[x['knowledge_id']];self.assertEqual(set(json.loads(row['tag_ids_json'])),{t['id'] for t in remote['tags']});self.assertEqual(row['content_sha256'],U.sha(remote['metadata']['content']));self.assertEqual(row['knowledge_base_id'],U.KB)
        self.assertEqual(self.s.rows()['SD-other']['content_sha256'],'otherhash')
    def test_wrong_remote_tags_no_ledger_then_retry_after_fixed(self):
        p=self.payload();self.client.bad_tags=True;self.assertEqual(self.s.upload(p)['results'][0]['action'],'failed');self.assertNotIn(p['documents'][0]['number'],self.s.rows());self.client.remote['id-1']['tags']=[{'id':'public-tag'},{'id':'work-tag'}];self.assertEqual(self.s.upload(p)['results'][0]['action'],'recovered');self.assertEqual(self.creates(),1)
    def test_existing_remote_without_ledger_reused_by_hash(self):
        doc=self.s.documents()[0];self.client.create_manual(doc.title,doc.content,doc.tag_ids,U.KB);self.assertEqual(self.s.upload(self.payload())['results'][0]['action'],'recovered');self.assertEqual(self.creates(),1)
    def test_same_title_wrong_hash_refused_no_post(self):
        doc=self.s.documents()[0];self.client.create_manual(doc.title,'different',doc.tag_ids,U.KB);self.assertEqual(self.s.upload(self.payload())['results'][0]['action'],'failed');self.assertEqual(self.creates(),1)
    def test_stale_hash_and_unknown_selection_rejected(self):
        p=self.payload();p['documents'][0]['content_sha256']='0'*64
        with self.assertRaises(U.UploadStop):self.s.upload(p)
        p['documents'][0]['number']='SD-not-owned'
        with self.assertRaises(U.UploadStop):self.s.upload(p)
        self.assertEqual(self.creates(),0)
    def test_injected_payload_and_library_rejected(self):
        p=self.payload()
        for mutation in [dict(p,kb_id='private'),dict(p,content='forged'),{'documents':[dict(p['documents'][0],tag_ids=['private'])]}]:
            with self.assertRaises(U.UploadStop):self.s.upload(mutation)
        self.assertEqual(self.client.calls,[])
    def test_duplicate_and_limit_rejected(self):
        row=self.payload()['documents'][0]
        for docs in [[],[row,row],[dict(row,number=str(i)) for i in range(51)]]:
            with self.assertRaises(U.UploadStop):self.s.upload({'documents':docs})
    def test_changed_content_no_silent_overwrite(self):
        self.s.upload(self.payload());c=sqlite3.connect(self.root/'workspace/knowledge/knowledge.db');c.execute("UPDATE documents SET body='changed' WHERE id='one'");c.commit();c.close();self.assertEqual(self.s.catalog()['items'][0]['state'],'changed');self.assertEqual(self.s.upload(self.payload())['results'][0]['action'],'failed');self.assertEqual(self.creates(),1)
    def test_wrong_library_no_post(self):
        self.client.config.kb_id='private'
        with self.assertRaises(U.UploadStop):self.s.upload(self.payload())
        self.assertEqual(self.creates(),0)
    def test_credential_in_source_rejected_before_write(self):
        with sqlite3.connect(self.root/'workspace/knowledge/knowledge.db') as c:c.execute('UPDATE documents SET body=? WHERE id=?',(self.client.config.api_key,'one'))
        with self.assertRaises(U.UploadStop):self.s.upload(self.payload())
        self.assertEqual(self.creates(),0)
    def test_receipt_no_body_and_no_key(self):
        self.s.upload(self.payload());raw=''.join(p.read_text() for p in self.s.state.glob('*.json'));self.assertNotIn('原样正文',raw);self.assertNotIn(self.client.config.api_key,raw)


class AuditTests(unittest.TestCase):
    def test_only_manual_post_allowed_audit_no_source_or_credential(self):
        from weknora_client import WeKnoraConfig
        import io
        class Response(io.BytesIO):
            status=200
        with tempfile.TemporaryDirectory(dir='/tmp') as tmp:
            c=U.AuditClient(WeKnoraConfig('http://127.0.0.1:1/api/v1','synthetic-secret','10000',U.KB),Path(tmp)/'audit.jsonl')
            class Opener:
                def open(self,*a,**kw):return Response(b'{"data":{"id":"id-safe"}}')
            c._opener=Opener();c.create_manual('source-title','source-body',['a'],U.KB)
            row=json.loads(c.audit_path.read_text());self.assertEqual(row['method'],'POST');self.assertEqual(row['status'],200);self.assertEqual(row['knowledge_id'],'id-safe')
            raw=c.audit_path.read_text()
            for text in ['source-title','source-body','synthetic-secret']:self.assertNotIn(text,raw)
            for method,path in [('PUT','/knowledge/tags'),('DELETE','/knowledge/id-safe'),('POST','/knowledge/move')]:
                with self.assertRaises(U.UploadStop):c._request(method,path,{})
            self.assertEqual(len(c.audit_path.read_text().splitlines()),1)




class IsolationTests(unittest.TestCase):
    setUp = Tests.setUp
    payload = Tests.payload
    creates = Tests.creates
    """M3 R1: stage × failure matrix; no external service, no relaxed limits."""
    def failure(self, kind):
        if kind == 'read':
            # Fetch boundary for exactly one known row; raw errors must not leak.
            original = self.s.read_document
            def read(db, rowid):
                row = original(db, rowid)
                if row['id'] == 'one':
                    raise sqlite3.OperationalError('synthetic-sensitive-read-detail')
                return row
            return patch.object(self.s, 'read_document', read)
        value = 'a' * 200001 if kind == 'oversize' else '   \n\t'
        with sqlite3.connect(self.root/'workspace/knowledge/knowledge.db') as db:
            db.execute('UPDATE documents SET body=? WHERE id=?', (value, 'one'))
        from contextlib import nullcontext
        return nullcontext()

    def matrix(self, stage, kind):
        self.setUp()  # independent fixture per cell; inherited regression stays intact
        good = self.s.catalog()['items'][1]
        with self.failure(kind):
            if stage == 'documents':
                rows = self.s.documents()
                self.assertEqual(len(rows), 2)
                bad = next(d for d in rows if d.number == 'CW-one')
                self.assertTrue(bad.error)
                self.assertTrue(any(d.number == good['number'] and not getattr(d, 'error', None) for d in rows))
            elif stage == 'catalog':
                data = self.s.catalog()
                self.assertEqual(len(data['items']), 2)
                bad = next(d for d in data['items'] if d['number'] == 'CW-one')
                self.assertEqual(bad['state'], 'failed')
                self.assertFalse(bad['uploadable'])
                self.assertIn('不可上传', bad['error'])
                self.assertEqual(next(d for d in data['items'] if d['number'] == good['number'])['state'], 'pending')
                self.assertNotIn('synthetic-sensitive-read-detail', json.dumps(data))
            else:
                selected = {'documents': [{'number': 'CW-one', 'content_sha256': ''},
                                         {k: good[k] for k in ('number', 'content_sha256')}]}
                result = self.s.upload(selected)
                self.assertEqual([d['action'] for d in result['results']], ['failed', 'created'])
                self.assertFalse(result['stopped'])
                self.assertEqual(len(self.client.remote), 1)
                self.assertEqual(self.creates(), 1)
                self.assertNotIn('CW-one', self.s.rows())
                self.assertIn(good['number'], self.s.rows())
                self.assertIn('不可上传', result['results'][0]['error'])

    def test_r1_documents_matrix(self):
        for kind in ('oversize', 'read', 'empty'):
            with self.subTest(kind=kind): self.matrix('documents', kind)

    def test_r1_catalog_matrix(self):
        for kind in ('oversize', 'read', 'empty'):
            with self.subTest(kind=kind): self.matrix('catalog', kind)

    def test_r1_upload_matrix(self):
        for kind in ('oversize', 'read', 'empty'):
            with self.subTest(kind=kind): self.matrix('upload', kind)

    def test_r1_only_healthy_selection_ignores_unselected_bad(self):
        for kind in ('oversize', 'read', 'empty'):
            with self.subTest(kind=kind):
                self.setUp();good = self.s.catalog()['items'][1]
                with self.failure(kind):
                    result = self.s.upload({'documents': [{k: good[k] for k in ('number', 'content_sha256')}]})
                    self.assertEqual(result['results'][0]['action'], 'created')
                    self.assertEqual(self.creates(), 1)

    def test_r1_source_changes_to_bad_after_catalog_are_isolated(self):
        for kind in ('oversize', 'read', 'empty'):
            with self.subTest(kind=kind):
                self.setUp();selected = self.payload(0, 1)
                with self.failure(kind):
                    result = self.s.upload(selected)
                    self.assertEqual([r['action'] for r in result['results']], ['failed', 'created'])

    def test_r1_all_bad_no_client_or_ledger_write(self):
        with sqlite3.connect(self.root/'workspace/knowledge/knowledge.db') as db:
            db.execute("UPDATE documents SET body='' ")
        result = self.s.upload({'documents': [{'number':'CW-one', 'content_sha256':''}]})
        self.assertEqual(result['results'][0]['action'], 'failed')
        self.assertEqual(self.client.calls, [])
        self.assertEqual(set(self.s.rows()), {'SD-other'})

    def test_r1_renderer_limit_remains_exact_and_body_never_truncated(self):
        # Derive the header size with the original renderer; 200000 valid, 200001 invalid.
        from scripts.weknora_push_longtext import render, SafetyStop
        row = dict(id='limit',source='articles',title='limit',body='a'*200000,url='',modified='date')
        header = len(render(dict(row,body='a'*160),'public-tag','work-tag').content) - 160
        row['body'] = 'a' * (200000-header)
        exact = render(row,'public-tag','work-tag')
        self.assertEqual(len(exact.content), 200000)
        with sqlite3.connect(self.root/'workspace/knowledge/knowledge.db') as db:
            db.execute('INSERT INTO documents VALUES(?,?,?,?,?,?)',('limit','articles','limit',row['body'],'','date'))
        doc = next(d for d in self.s.documents() if d.number=='CW-limit')
        self.assertEqual(doc.content, exact.content)
        self.assertEqual(doc.body, row['body'])
        with sqlite3.connect(self.root/'workspace/knowledge/knowledge.db') as db:
            db.execute("UPDATE documents SET body=body || 'a' WHERE id='limit'")
        card = next(d for d in self.s.catalog()['items'] if d['number']=='CW-limit')
        self.assertFalse(card['uploadable'])
        self.assertIn('200000',card['error'])
        with self.assertRaises(SafetyStop):render(dict(row,body=row['body']+'a'),'public-tag','work-tag')


if __name__=='__main__':unittest.main()
