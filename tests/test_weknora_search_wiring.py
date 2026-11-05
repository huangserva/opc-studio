import copy
import hashlib
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor

from weknora_search import RemoteSearch, PUBLIC_KB, MAX_PASSAGE, _LimitedOpener, MAX_RESPONSE, sanitize, safe_url, library_results
from items_store import ItemsStore, library_search, canonical_id
from assistant_knowledge import KnowledgeTools
from tests.test_items_store import knowledge_fixture

KID='jd-remote-only'
SD='sd-private-only'
LOCAL='cw-local'
SECRET='sk-'+'synthetic_private_value_'*4


def row(kid=KID, content='JD-独有决定：本轮采用单写入者，先核对证据。', **changes):
    return dict(id='chunk-one', knowledge_id=kid, knowledge_base_id=PUBLIC_KB,
                knowledge_title='远端独有成果', content=content, metadata={}, **changes)


def fixture(root):
    root=Path(root);(root/'weknora').mkdir(parents=True,exist_ok=True)
    tags=root/'tags.json'
    tags.write_text(json.dumps({'knowledge_bases':{'public':{'knowledge_base_id':PUBLIC_KB,
        'tags':{'隐私-公开':'pub','隐私-私密':'private','隐私-内部':'internal'}}}}))
    db=sqlite3.connect(root/'weknora/ledger.db')
    db.execute('CREATE TABLE result_ledger(knowledge_id TEXT,result_number TEXT,tag_ids_json TEXT,knowledge_base_id TEXT,remote_state TEXT)')
    db.executemany('INSERT INTO result_ledger VALUES(?,?,?,?,?)',[(KID,'JD-独有决定','["internal"]',PUBLIC_KB,'active'),
        (SD,'SD-私密商单','["private"]',PUBLIC_KB,'active'),(LOCAL,'CW-'+'a'*32,'["pub"]',PUBLIC_KB,'active')])
    db.commit();db.close();return tags


class FakeClient:
    def __init__(self, rows=None, delay=0, failure=None):
        self.rows=rows if rows is not None else [row()]
        self.calls=[];self.delay=delay;self.failure=failure
        self.config=SimpleNamespace(api_key=SECRET,base_url='https://example.com/weknora/api/v1')
    def hybrid_search(self, query, **kw):
        self.calls.append((query,kw));time.sleep(self.delay)
        if self.failure:raise self.failure
        return {'data':self.rows(query) if callable(self.rows) else copy.deepcopy(self.rows),'success':True}
    def __getattr__(self,name):
        if any(x in name for x in ('create','update','delete','replace','reparse','rebuild')):
            raise AssertionError('写方法不得调用')
        raise AttributeError(name)


class WiringTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='wk-search-test-',dir='/tmp');self.root=Path(self.tmp.name)
        self.tags=fixture(self.root);self.client=FakeClient();self.service=RemoteSearch(self.root,lambda:self.client,tags_path=self.tags,wait=.8)
    def tearDown(self):self.tmp.cleanup()
    def first(self,**kw):return self.service.search('独有决定',**kw)['results'][0]
    def test_only_hybrid_fixed_public_kb(self):
        self.first();self.assertEqual(len(self.client.calls),1)
        self.assertEqual(self.client.calls[0][1],{'kb_id':PUBLIC_KB,'match_count':30})
    def test_remote_only_id_and_immutable_passage(self):
        d=self.first();self.assertEqual(d['remote_knowledge_id'],KID)
        self.assertEqual(d['provenance']['result_number'],'JD-独有决定')
        self.assertFalse(d['content_complete']);self.assertIn('非整篇',d['incomplete_reason'])
        self.assertEqual(self.service.document(d['id'])['body'],d['body'])
    def test_companion_substantive_passage_not_hidden_by_tail_chunk(self):
        self.client.rows=[row(content='出处：无'),dict(row(content='明确决定：采用单写入者并保留证据，禁止覆盖原文。'),id='substantive')]
        d=self.first();self.assertEqual(d['remote_chunk_id'],'substantive');self.assertIn('单写入者',d['body'])
    def test_private_hidden_by_default_and_explicit_read(self):
        self.client.rows=[row(SD)]
        self.assertEqual(self.service.search('商单')['results'],[])
        d=self.first(include_private=True)
        with self.assertRaisesRegex(ValueError,'私密'):self.service.document(d['id'])
        self.assertEqual(self.service.document(d['id'],include_private=True)['privacy'],'私密')
    def test_ledger_alias_does_not_choose_remote_kb(self):
        tags=json.loads(self.tags.read_text());tags['knowledge_bases']['retired']={'tags':{'隐私-私密':'old-private'}}
        self.tags.write_text(json.dumps(tags))
        db=sqlite3.connect(self.root/'weknora/ledger.db');db.execute('update result_ledger set knowledge_base_id=?,tag_ids_json=? where knowledge_id=?',('retired','["old-private"]',SD));db.commit();db.close()
        self.client.rows=[row(SD)]
        self.assertEqual(self.service.search('商单')['results'],[])
        d=self.first(include_private=True)
        self.assertEqual(d['privacy'],'私密')
        self.assertTrue(all(kw['kb_id']==PUBLIC_KB for _,kw in self.client.calls))
    def test_unknown_or_conflicting_privacy_fail_closed(self):
        db=sqlite3.connect(self.root/'weknora/ledger.db');db.execute('update result_ledger set tag_ids_json=? where knowledge_id=?',('["pub","private"]',KID));db.commit();db.close()
        self.assertEqual(self.service.search('决定',include_private=True)['results'],[])
    def test_cached_read_rechecks_privacy(self):
        d=self.first();db=sqlite3.connect(self.root/'weknora/ledger.db');db.execute('update result_ledger set tag_ids_json=? where knowledge_id=?',('["private"]',KID));db.commit();db.close()
        with self.assertRaisesRegex(ValueError,'隐私'):self.service.document(d['id'])
    def test_no_private_kb_even_when_result_claims_other(self):
        self.client.rows=[dict(row(),knowledge_base_id='cedb34b6-3cdd-461f-a3d5-e28b4739736c')]
        self.assertEqual(self.service.search('决定',include_private=True)['results'],[])
    def test_no_unknown_ledger_id(self):
        self.client.rows=[row('not-in-ledger')];self.assertEqual(self.service.search('决定')['results'],[])
    def test_bad_rows_are_ignored(self):
        self.client.rows=[None,{},dict(row(),content=None),dict(row(),id='../secret'),dict(row(),knowledge_id=[])]
        # A malformed ID must not turn the valid local result into an exception.
        self.assertEqual(self.service.search('决定')['results'],[])
        for invalid in (None,0,{},[]):
            with self.subTest(content=type(invalid).__name__):
                self.client.rows=[dict(row(),content=invalid)]
                self.assertEqual(self.service.search('决定')['results'],[])
    def test_successful_null_hit_set_is_empty_not_outage(self):
        self.client.hybrid_search=lambda *a,**k:{'success':True,'data':None}
        result=self.service.search('无命中');self.assertEqual(result['state'],'ok');self.assertEqual(result['results'],[])
    def test_malformed_envelope_is_unavailable(self):
        self.client.hybrid_search=lambda *a,**k:{'data':{'huge':'ignored'}}
        self.assertEqual(self.service.search('决定')['state'],'unavailable')
    def test_secret_redacted_in_title_content_and_large_metadata_not_saved(self):
        self.client.rows=[dict(row(content='token: '+SECRET+'\n正文 '+SECRET+'\nCookie: session=synthetic'),knowledge_title=SECRET, metadata={'arbitrary':'z'*1000000})]
        d=self.first();serialized=json.dumps(d,ensure_ascii=False)
        self.assertNotIn(SECRET,serialized);self.assertNotIn('session=synthetic',serialized)
        self.assertLess(len(next(self.service.cache.glob('*.json')).read_bytes()),60000)
    def test_technical_token_word_is_not_treated_as_secret(self):
        self.assertEqual(sanitize('token 数量与缓存成本'),'token 数量与缓存成本')
        self.assertNotIn('value123456',sanitize('Authorization: Bearer value123456'))
    def test_large_field_explicit_truncation(self):
        self.client.rows=[row(content='正文'*9000)];d=self.first()
        self.assertEqual(len(d['body']),MAX_PASSAGE);self.assertTrue(d['truncated']);self.assertIn('已截断',d['incomplete_reason'])
    def test_url_filter_and_prefix(self):
        c=self.client.config
        self.assertEqual(safe_url('/r/x',c),'https://example.com/weknora/r/x')
        for u in ('javascript:alert(1)','https://x/?token=x','https://u:p@x/a','https://x/a\n'):self.assertEqual(safe_url(u,c),'')
    def test_cw_local_reference_kept(self):
        self.client.rows=[row(LOCAL)];d=self.first()
        self.assertEqual(d['provenance']['local_document_id'],'a'*32)
        self.assertIn('document='+'a'*32,d['provenance']['local_document_url'])
    def test_wrong_id_or_hash_tamper_rejected(self):
        d=self.first()
        for identity in ('../ledger.db','wk:wrong:chunk-one:'+'0'*16):
            with self.assertRaises(ValueError):self.service.document(identity)
        p=self.service._path(d['id']);data=json.loads(p.read_text());data['body']='被篡改';p.write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError,'损坏'):self.service.document(d['id'])
    def test_snapshot_persists_after_service_recreation(self):
        d=self.first();other=RemoteSearch(self.root,lambda:self.client,tags_path=self.tags)
        self.assertEqual(other.document(d['id'])['hash'],d['hash'])
        self.assertEqual(self.service._path(d['id']).stat().st_mode&0o777,0o600)
    def test_timeout_bounded_and_no_late_disk_write(self):
        self.client.delay=.15;self.service.wait=.02;t=time.monotonic()
        r=self.service.search('决定');self.assertLess(time.monotonic()-t,.1)
        self.assertEqual(r['state'],'unavailable');time.sleep(.18)
        self.assertFalse(self.service.cache.exists())
    def test_two_inflight_cap_and_third_does_not_queue(self):
        self.client.delay=.15;self.service.wait=.01
        self.service.search('一');self.service.search('二');t=time.monotonic();r=self.service.search('三')
        self.assertLess(time.monotonic()-t,.02);self.assertIn('繁忙',r['notice']);time.sleep(.2)
        self.assertEqual(len(self.client.calls),2)
    def test_concurrent_queries_never_cross_results(self):
        self.client.rows=lambda q:[row(content=q)];self.client.delay=.01
        with ThreadPoolExecutor(2) as pool:results=list(pool.map(self.service.search,['问题甲','问题乙']))
        self.assertEqual([r['results'][0]['body'] for r in results],['问题甲','问题乙'])
        self.assertNotEqual(results[0]['results'][0]['id'],results[1]['results'][0]['id'])
    def test_errors_are_sanitized_no_exception_details(self):
        self.client.failure=RuntimeError(SECRET)
        r=self.service.search('决定');self.assertEqual(r['state'],'unavailable');self.assertNotIn(SECRET,repr(r))
    def test_query_boundaries(self):
        for q in ('','x'*501,'x\n',None,{},True):
            with self.assertRaises(ValueError):self.service.search(q)
        with self.assertRaises(ValueError):self.service.search('决定',include_private='1')
    def test_corrupt_ledger_fails_before_network(self):
        (self.root/'weknora/ledger.db').write_bytes(b'invalid')
        self.assertEqual(self.service.search('决定')['state'],'unavailable');self.assertEqual(self.client.calls,[])
    def test_ledger_source_never_opened_by_sqlite(self):
        original=sqlite3.connect;opened=[]
        def track(path,*a,**kw):opened.append(str(path));return original(path,*a,**kw)
        with patch('weknora_search.sqlite3.connect',track):self.first()
        self.assertTrue(opened);self.assertTrue(all(str(self.root/'weknora/ledger.db') not in p for p in opened))
    def test_library_keeps_local_on_outage(self):
        knowledge_fixture(self.root);self.client.failure=TimeoutError()
        with patch('weknora_search.service',return_value=self.service):
            r=library_results(self.root,{'q':['agent']},library_search)
        self.assertEqual(r['items'][0]['id'],'a'*32);self.assertEqual(r['remote']['state'],'unavailable');self.assertIn('远端不可用',r['remote']['notice'])
    def test_outage_preserves_local_lower_bound_and_pagination(self):
        self.client.failure=TimeoutError()
        local={'items':[{'id':'local'}],'total':1,'total_label':'≥1','page':1,'has_more':True,'partial':True}
        with patch('weknora_search.service',return_value=self.service):
            data=library_results(self.root,{'q':['鲸']},lambda *a:copy.deepcopy(local))
        for key in ('total','total_label','has_more','partial'):self.assertEqual(data[key],local[key])
    def test_library_merges_and_source_filter_does_not_widen(self):
        knowledge_fixture(self.root)
        with patch('weknora_search.service',return_value=self.service):
            r=library_results(self.root,{'q':['agent']},library_search)
            self.assertEqual({d['source'] for d in r['items']},{'manual','weknora'})
            self.client.calls=[];r=library_results(self.root,{'q':['agent'],'source':['manual']},library_search)
            self.assertEqual(self.client.calls,[]);self.assertTrue(all(d['source']=='manual' for d in r['items']))
    def test_library_parameters_cannot_choose_kb_or_path(self):
        for q in ({'q':['x'],'kb_id':['private']},{'q':['x'],'path':['../']},{'q':['x'],'include_private':['yes']},{'q':['x','y']}):
            with self.assertRaises(ValueError):library_results(self.root,q,lambda *a:{'items':[],'total':0})
    def test_material_is_stable_and_preserves_reference(self):
        d=self.first();m=self.service.material(d['id']);s=ItemsStore(self.root);saved=s.upsert(m)
        self.assertEqual(saved['id'],'kb:'+d['id']);self.assertEqual(saved['full_text'],d['body'])
        self.assertEqual(saved['legacy_refs'][0]['id'],KID);self.assertEqual(saved['legacy_refs'][0]['privacy'],'内部')
        self.assertEqual(s.upsert(m)['id'],saved['id'])
    def test_assistant_read_records_exact_passage_hash(self):
        d=self.first();store=SimpleNamespace(workspace=self.root);tools=KnowledgeTools(store);job={}
        with patch('weknora_search.service',return_value=self.service):
            result=tools.execute('read_knowledge',{'id':d['id'],'hash':d['hash'],'start':1,'max_chars':5},job)
            self.assertEqual(result['text'],d['body'][1:6]);self.assertEqual(job['citations'][0]['provenance']['remote_knowledge_id'],KID)
            self.assertEqual(job['_knowledge_reads'][0]['hash'],d['hash'])
            with self.assertRaises(ValueError):tools.execute('read_knowledge',{'id':d['id'],'hash':'0'*64},job)
    def test_assistant_never_reads_private_or_widens_frozen_context(self):
        self.client.rows=[row(SD)];d=self.first(include_private=True);tools=KnowledgeTools(SimpleNamespace(workspace=self.root))
        with patch('weknora_search.service',return_value=self.service):
            with self.assertRaisesRegex(ValueError,'私密'):tools.execute('read_knowledge',{'id':d['id']},{})
            with self.assertRaisesRegex(ValueError,'固定'):tools.execute('read_knowledge',{'id':d['id']},{'_knowledge_context':{}})
    def test_assistant_search_remote_only_is_not_local_article(self):
        knowledge=SimpleNamespace(search=lambda *a,**k:{'results':[],'total':0})
        tools=KnowledgeTools(SimpleNamespace(workspace=self.root,knowledge=knowledge))
        with patch('weknora_search.service',return_value=self.service):
            r=tools.execute('search_knowledge',{'query':'独有决定','source':'weknora'}, {})
        self.assertEqual(r['results'][0]['remote_knowledge_id'],KID);self.assertIn('/library.html?',r['results'][0]['url'])
    def test_remote_draft_only_and_no_model_send(self):
        from server import Store
        from write_from_item import ItemWriter
        self.client.rows=[row(SD)];d=self.first(include_private=True);m=self.service.material(d['id'],include_private=True)
        store=Store(self.root);saved=ItemsStore(self.root).upsert(m);calls=[]
        writer=ItemWriter(SimpleNamespace(store=store),model=lambda item:calls.append(item))
        result=writer.start(saved['id'],{})
        self.assertEqual(result['status'],'source_only');self.assertEqual(calls,[])
        source=json.loads((store.article(result['slug'])/'source-item.json').read_text())
        self.assertEqual(source['legacy_refs'][0]['id'],SD)
        blocks=json.dumps(store.detail(result['slug']),ensure_ascii=False)
        self.assertIn('私密',blocks);self.assertIn(SD,blocks);self.assertIn('非全文',blocks)
    def test_limited_response_stops_oversize(self):
        class Response(io.BytesIO):
            status=200
            def __enter__(self):return self
            def __exit__(self,*a):self.close()
        opener=SimpleNamespace(open=lambda *a,**k:Response(b'x'*(MAX_RESPONSE+2)))
        with _LimitedOpener(opener).open('x') as response:
            with self.assertRaises(ValueError):response.read()

if __name__=='__main__':unittest.main()
