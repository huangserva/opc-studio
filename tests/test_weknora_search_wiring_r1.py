import base64
import copy
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import patch

from items_store import ItemsStore, post_route
from assistant_knowledge import KnowledgeTools
from weknora_search import RemoteSearch, sanitize, library_results
from tests.test_weknora_search_wiring import fixture, FakeClient, row, KID
from tests.test_items_store import knowledge_fixture

CANARY='R1_PRIVATE_CONTENT_MUST_NOT_ESCAPE'


def jwt(header=None, payload=None, signature=b'r1-synthetic-signature-variant'):
    enc=lambda value:base64.urlsafe_b64encode(value).decode().rstrip('=')
    return '.'.join((enc(header or b'{"alg":"HS256","typ":"JWT"}'),
                     enc(payload or b'{"sub":"review","iat":1793790000}'),enc(signature)))


class R1Tests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='wk-r1-',dir='/tmp');self.root=Path(self.temp.name)
        self.tags=fixture(self.root);self.client=FakeClient()
        self.s=RemoteSearch(self.root,lambda:self.client,tags_path=self.tags,wait=1)
        self.handler=SimpleNamespace(path='/api/items',server=SimpleNamespace(store=SimpleNamespace(workspace=self.root)),reply=lambda status,data:(status,data))
    def tearDown(self):self.temp.cleanup()
    def post(self,payload):
        with patch('weknora_search.service',return_value=self.s):return post_route(self.handler,payload)
    def mutate(self,change):
        if change=='corrupt':(self.root/'weknora/ledger.db').write_bytes(b'BROKEN');return
        db=sqlite3.connect(self.root/'weknora/ledger.db')
        if change=='private':db.execute('update result_ledger set tag_ids_json=? where knowledge_id=?',('["private"]',KID))
        if change=='delete':db.execute("update result_ledger set remote_state='deleted' where knowledge_id=?",(KID,))
        if change=='number':db.execute('update result_ledger set result_number=? where knowledge_id=?',('JD-new-number',KID))
        db.commit();db.close()
    def no_cache_canary(self):
        self.assertFalse(any(CANARY in p.read_text() for p in self.s.cache.glob('*') if p.is_file()))
    def inflight(self,change,assistant=False):
        entered=threading.Event();release=threading.Event()
        def remote(*a,**kw):
            entered.set();release.wait(3);return {'success':True,'data':[row(content=CANARY)]}
        self.client.hybrid_search=remote
        tools=KnowledgeTools(SimpleNamespace(workspace=self.root,knowledge=SimpleNamespace(search=lambda *a,**kw:{'results':[],'total':0})))
        with patch('weknora_search.service',return_value=self.s),ThreadPoolExecutor(1) as pool:
            pending=pool.submit(tools.execute,'search_knowledge',{'query':'R1 隐私','source':'weknora'},{}) if assistant else pool.submit(self.s.search,'R1 隐私')
            self.assertTrue(entered.wait(2));self.mutate(change);release.set();result=pending.result()
        self.assertEqual(result['results'],[]);self.assertNotIn(CANARY,json.dumps(result));self.no_cache_canary()
        return result
    def test_h1_extra_fields_cannot_bypass_known_or_unknown_snapshot(self):
        known=self.s.search('决定')['results'][0]['id']
        for identity in (known,'wk:never-queried:chunk:'+'0'*16):
            for payload in ({'remote_id':identity,'include_private':False,'full_text':CANARY},
                            {'full_text':CANARY,'include_private':False,'remote_id':identity,'legacy_id':identity,'source':'weknora'},
                            {'remote_id':identity,'include_private':False,'title':'伪造','legacy_refs':[]}):
                self.assertEqual(self.post(payload)[0],400)
        self.assertFalse(ItemsStore(self.root).path.exists())
    def test_h1_identity_source_and_nested_reference_variants(self):
        rid='wk:never-queried:chunk:'+'0'*16
        variants=[{'id':rid},{'id':'kb:'+rid},{'legacy_id':rid},{'legacy_id':'kb:'+rid},
                  {'id':'%77%6b%3anever-queried:chunk:'+'0'*16},
                  {'source':' WeKnora '},{'source':'ＷｅＫｎｏｒａ'},{'sources':['manual','WEKNORA']},{'source':{'name':'weknora'}},
                  {'legacy_refs':[{'store':'knowledge','id':'local','refs':[{'store':'weknora','id':'never'}]}]},
                  {'rich_sources':[{'provenance':{'source':'weknora'}}]},
                  {'author':{'name':'manual','refs':[{'store':'weknora'}]}},
                  {'remote_knowledge_id':'never'}, {'url':'/library.html?remote='+rid},
                  {'refs':{'nested':{'source':'weknora'}}}]
        for change in variants:
            with self.subTest(fields=list(change)):
                self.assertEqual(self.post({'id':'manual:forged','title':'伪造','full_text':CANARY,**change})[0],400)
        self.assertFalse(ItemsStore(self.root).path.exists())
    def test_h1_normal_manual_preserves_body_and_metadata(self):
        body='普通手动素材，讨论 WeKnora 和 token 预算 64 次。<script>纯文本</script>'
        payload={'legacy_id':'manual-old','source':'manual','sources':['manual'],'kind':'article','title':'普通资料','full_text':body,'url':'https://example.com/manual','note':'收藏理由','tags':['WeKnora研究'],'author':{'name':'手动作者','handle':'manual','followers':0},'metrics':{'views':0,'bookmarks':None},'content_complete':True}
        status,saved=self.post(payload);self.assertEqual(status,201);self.assertEqual(saved['full_text'],body)
        self.assertEqual(saved['note'],'收藏理由');self.assertEqual(saved['metrics']['views'],0)
    def test_h1_trusted_remote_and_local_imports_still_work(self):
        d=self.s.search('决定')['results'][0];status,saved=self.post({'remote_id':d['id'],'include_private':False})
        self.assertEqual(status,201);self.assertEqual(saved['full_text'],d['body']);self.assertEqual(saved['legacy_refs'][0]['id'],KID)
        knowledge_fixture(self.root);status,local=self.post({'document_id':'a'*32});self.assertEqual(status,201);self.assertTrue(local['full_text'])
    def test_h1_remote_type_or_extra_document_fields_rejected(self):
        for p in ({'remote_id':'wk:x:y:'+'0'*16,'include_private':'1'}, {'document_id':'a'*32,'full_text':CANARY}, {'id':'manual','author':{'name':{},'handle':'x'}}, {'id':'manual','metrics':{'source':'weknora'}}, {'id':'manual','metrics':{'views':10**400}}, {'id':'manual','author':{'followers':10**400}}):
            self.assertEqual(self.post(p)[0],400)
    def test_h2_inflight_private_default(self):self.inflight('private')
    def test_h2_inflight_delete_default(self):self.inflight('delete')
    def test_h2_inflight_corrupt_default(self):self.inflight('corrupt')
    def test_h2_inflight_number_changed(self):self.inflight('number')
    def test_h2_assistant_inflight_private(self):self.inflight('private',assistant=True)
    def test_h2_assistant_inflight_delete_variant(self):self.inflight('delete',assistant=True)
    def test_h2_assistant_after_search_return_before_tool_publish(self):
        self.client.rows=[row(content=CANARY)];original=self.s.search
        def change(*a,**kw):r=original(*a,**kw);self.mutate('private');return r
        tools=KnowledgeTools(SimpleNamespace(workspace=self.root,knowledge=SimpleNamespace(search=lambda *a,**kw:{'results':[],'total':0})))
        with patch.object(self.s,'search',change),patch('weknora_search.service',return_value=self.s):
            r=tools.execute('search_knowledge',{'query':'决定','source':'weknora'},{})
        self.assertEqual(r['results'],[]);self.assertNotIn(CANARY,json.dumps(r));self.no_cache_canary()
    def test_h2_library_after_search_return_before_route_publish(self):
        self.client.rows=[row(content=CANARY)];original=self.s.search
        def change(*a,**kw):r=original(*a,**kw);self.mutate('delete');return r
        with patch.object(self.s,'search',change),patch('weknora_search.service',return_value=self.s):
            r=library_results(self.root,{'q':['决定'],'source':['weknora']},lambda *a:None)
        self.assertEqual(r['items'],[]);self.assertNotIn(CANARY,json.dumps(r));self.no_cache_canary()
    def test_h2_before_cache_entry_checks_current_catalog(self):
        self.client.rows=[row(content=CANARY)];original=self.s._remember_checked
        def change(doc,private):self.mutate('private');return original(doc,private)
        with patch.object(self.s,'_remember_checked',change):r=self.s.search('决定')
        self.assertEqual(r['results'],[]);self.no_cache_canary();self.assertFalse(self.s.cache.exists())
    def test_h2_at_actual_write_boundary_no_serialization_if_private(self):
        self.client.rows=[row(content=CANARY)];original=self.s._remember
        def change(doc,**kw):self.mutate('private');return original(doc,**kw)
        with patch.object(self.s,'_remember',change),patch('weknora_search.json.dump',wraps=json.dump) as write:
            r=self.s.search('决定');self.assertFalse(write.called)
        self.assertEqual(r['results'],[]);self.no_cache_canary()
    def test_h2_during_serialization_discards_temporary_snapshot(self):
        self.client.rows=[row(content=CANARY)];original=json.dump
        def change(obj,stream,**kw):result=original(obj,stream,**kw);self.mutate('private');return result
        with patch('weknora_search.json.dump',change):r=self.s.search('决定')
        self.assertEqual(r['results'],[]);self.no_cache_canary();self.assertEqual(list(self.s.cache.glob('*.tmp')),[])
    def test_h2_before_publish_delete_cleans_cache(self):
        self.client.rows=[row(content=CANARY)];original=self.s._remember_checked
        def change(doc,private):result=original(doc,private);self.mutate('delete');return result
        with patch.object(self.s,'_remember_checked',change):r=self.s.search('决定')
        self.assertEqual(r['results'],[]);self.no_cache_canary()
    def test_h2_before_publish_corrupt_degrades_and_cleans_cache(self):
        self.client.rows=[row(content=CANARY)];original=self.s._remember_checked
        def change(doc,private):result=original(doc,private);self.mutate('corrupt');return result
        with patch.object(self.s,'_remember_checked',change):r=self.s.search('决定')
        self.assertEqual(r['state'],'unavailable');self.assertEqual(r['results'],[]);self.no_cache_canary()
    def test_m1_jwt_lengths_title_body_source_cache_and_material(self):
        tokens=[jwt(signature=b'synthetic-review-signature-bytes'),jwt(payload=b'{"sub":"r1"}',signature=b'other-r1-variant'),jwt(header=b'{"typ":"JWT"}',payload=b'{"sub":"third"}',signature=b'1234567890')]
        self.assertEqual([len(p) for p in tokens[0].split('.')],[36,44,43])
        for token in tokens:
            for prefix in ('','Bearer '):
                with self.subTest(lengths=[len(p) for p in token.split('.')],bearer=bool(prefix)):
                    self.client.rows=[dict(row(content='正文 '+prefix+token+'\ntoken 预算 64 次'),knowledge_title='标题 '+prefix+token,metadata={'source_url':'https://example.com/r/'+token})]
                    result=self.s.search('凭据');self.assertEqual(result['state'],'ok');doc=result['results'][0]
                    self.assertNotIn(token,json.dumps(doc));self.assertIn('token 预算 64 次',doc['body'])
                    self.assertNotIn(token,self.s._path(doc['id']).read_text());self.assertEqual(doc['source_url'],'')
                    status,saved=self.post({'remote_id':doc['id'],'include_private':False});self.assertEqual(status,201)
                    self.assertNotIn(token,json.dumps(saved));self.assertNotIn(token,ItemsStore(self.root).detail(saved['id'])['full_text'])
    def test_m1_jwe_five_segments_and_normal_technical_text(self):
        head=base64.urlsafe_b64encode(b'{"alg":"dir","enc":"A256GCM"}').decode().rstrip('=')
        token=head+'..aW5pdGlhbGl6YXRpb24.Y2lwaGVydGV4dA.dGFn'
        self.assertNotIn(token,sanitize('密文 '+token))
        for ordinary in ('token 预算 64 次','token 数量与缓存成本','release.v1.2026','documentation.example.com'):
            self.assertEqual(sanitize(ordinary),ordinary)

if __name__=='__main__':unittest.main()
