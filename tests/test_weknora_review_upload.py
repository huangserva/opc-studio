import json,sqlite3,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from contextlib import nullcontext
from tests.test_weknora_upload import fixture,Fake,TAGS
from scripts.weknora_push_longtext import Ledger as OldLedger, SafetyStop
import weknora_upload as U
import weknora_review_upload as R


def add_reviews(root):
    tags=json.loads((root/'coordination/weknora-tags.json').read_text());tags['tags'].update({'隐私-内部':'internal-tag','类型-知识':'knowledge-tag'});(root/'coordination/weknora-tags.json').write_text(json.dumps(tags))
    body='【验收合成】复盘结论：只记录事实，不推断写法的因果。'
    p={'slug':'test-review','type':'post','review_updated_at':'date','platforms':['x'],'source_locator':'/feedback.html?review=test-review','delivery_ids':['delivery-test'],'review_body_sha256':U.sha(body)}
    observation={'source_locator':'/feedback.html?observation='+'3'*32,'platform':'x','observation_id':'3'*32,'observation_hash':'4'*64,'raw_sha256':'5'*64,'platform_verified':False,'window':None}
    with sqlite3.connect(root/'workspace/knowledge/knowledge.db') as db:
        db.execute("ALTER TABLE documents ADD COLUMN provenance TEXT DEFAULT '{}'")
        db.executemany('INSERT INTO documents VALUES(?,?,?,?,?,?,?)',[(
            '1'*32,'manual','【验收合成】复盘笔记',body,'/?article=test-review','date',json.dumps(p)),(
            '2'*32,'publication_observations','【验收合成】发布观察','观察：统计窗口未知，未向平台校验。','https://x.com/test/status/1','date',json.dumps(observation)),(
            '6'*32,'manual','普通个人笔记','不得上传的普通笔记','','date','{}'),(
            '7'*32,'publication_feedback','原始指标快照','不得上传CSV数据','','date','{}')])
    return p


class ReviewFake(Fake):
    def list_tags(self,*a,**kw):
        part=super().list_tags(*a,**kw);part['data'] += [{'id':'internal-tag','name':'隐私-内部'},{'id':'knowledge-tag','name':'类型-知识'}];return part


class Tests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir='/tmp',prefix='m2-unit-');self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name);fixture(self.root);self.p=add_reviews(self.root);self.c=ReviewFake();self.s=U.UploadService(self.root,client_factory=lambda:self.c)
    def selected(self,source=None):return [{'number':x['number'],'content_sha256':x['content_sha256']} for x in self.s.catalog()['items'] if x['source'] in R.REVIEW_SOURCES and (source is None or x['source']==source)]
    def test_only_exported_reviews_and_observations_are_candidates(self):
        cat=self.s.catalog();self.assertEqual(len(cat['items']),4);self.assertEqual(len(self.selected()),2);self.assertFalse(any('个人笔记' in d['title'] or 'CSV数据' in d['title'] for d in cat['items']))
    def test_verbatim_body_origin_and_internal_privacy(self):
        for doc in self.s.documents():
            if doc.source not in R.REVIEW_SOURCES:continue
            self.assertEqual(doc.content.split('\n\n---\n\n',1)[1],doc.body);self.assertIn('- 隐私：内部',doc.content);self.assertIn('- 来源类型：'+doc.source,doc.content);self.assertIn('/feedback.html?',doc.content);self.assertEqual(doc.tag_ids,('internal-tag','knowledge-tag'))
    def test_upload_and_three_repeats_hash_idempotent(self):
        payload={'documents':self.selected()};r=self.s.upload(payload);self.assertEqual([x['action'] for x in r['results']],['created','created'])
        for _ in range(3):self.assertTrue(all(x['action']=='skipped' for x in self.s.upload(payload)['results']))
        self.assertEqual(sum(m=='POST' for m,p in self.c.calls),2);self.assertEqual(len(self.c.remote),2)
    def test_threeway_tags_body_and_ledger(self):
        result=self.s.upload({'documents':self.selected()})
        for x in result['results']:
            row=self.s.rows()[x['number']];remote=self.c.remote[x['knowledge_id']];self.assertEqual(set(json.loads(row['tag_ids_json'])),{'internal-tag','knowledge-tag'});self.assertEqual(set(json.loads(row['tag_ids_json'])),{t['id'] for t in remote['tags']});self.assertEqual(row['content_sha256'],U.sha(remote['metadata']['content']))
    def test_original_cli_does_not_acquire_review_prefixes(self):
        for d in self.selected():
            with self.assertRaises(SafetyStop):OldLedger.owned(d['number'])
            R.UploadLedger.owned(d['number'])
        for n in ['SD-x','RP-foreign','PO-'+'a'*32]:
            with self.assertRaises(SafetyStop):R.UploadLedger.owned(n)
    def test_review_body_origin_mismatch_not_uploadable(self):
        with sqlite3.connect(self.root/'workspace/knowledge/knowledge.db') as db:db.execute("UPDATE documents SET body='forged' WHERE source='manual' AND id=?",('1'*32,))
        bad=next(x for x in self.s.catalog()['items'] if x['source']=='manual');self.assertFalse(bad['uploadable']);self.assertIn('出处',bad['error']);self.assertEqual(len(self.c.remote),0)
    def test_missing_or_corrupt_provenance_fails_one_only(self):
        for raw in ['{broken','[]']:
            with sqlite3.connect(self.root/'workspace/knowledge/knowledge.db') as db:db.execute('UPDATE documents SET provenance=? WHERE id=?',(raw,'1'*32))
            cat=self.s.catalog();self.assertEqual(len(cat['items']),4);self.assertEqual(next(x for x in cat['items'] if x['source']=='manual')['state'],'failed');self.assertEqual(next(x for x in cat['items'] if x['source']=='publication_observations')['state'],'pending')
    def test_missing_review_tags_does_not_block_longtext(self):
        p=self.root/'coordination/weknora-tags.json';t=json.loads(p.read_text());del t['tags']['隐私-内部'];p.write_text(json.dumps(t));cards=self.s.catalog()['items'];self.assertTrue(all(x['state']=='failed' for x in cards if x['source'] in R.REVIEW_SOURCES));good=next(x for x in cards if x['source']=='articles');self.assertEqual(self.s.upload({'documents':[{k:good[k] for k in ('number','content_sha256')}]})['results'][0]['action'],'created')
    def test_wrong_remote_tags_no_partial_ledger(self):
        self.c.bad_tags=True;p={'documents':self.selected('manual')};r=self.s.upload(p);self.assertEqual(r['results'][0]['action'],'failed');self.assertNotIn(p['documents'][0]['number'],self.s.rows())
    def test_remote_accept_timeout_recovers_single_item(self):
        self.c.fail='after';p={'documents':self.selected('manual')};self.s.upload(p);self.c.fail=None;r=self.s.upload(p);self.assertEqual(r['results'][0]['action'],'recovered');self.assertEqual(len(self.c.remote),1)
    def test_m4_catalog_keeps_internal_review_number(self):
        from weknora_search import RemoteSearch
        self.s.upload({'documents':self.selected('manual')});tags=json.loads((self.root/'coordination/weknora-tags.json').read_text());tags['knowledge_bases']={'public':{'knowledge_base_id':U.KB,'tags':tags['tags']}};(self.root/'coordination/weknora-tags.json').write_text(json.dumps(tags));catalog=RemoteSearch(self.root/'workspace',tags_path=self.root/'coordination/weknora-tags.json').catalog();row=next(iter(catalog.values()));self.assertEqual(row['privacy'],'内部');self.assertTrue(row['result_number'].startswith('RP-'));self.assertNotIn('已脱敏',row['result_number'])

    def failure(self,kind):
        if kind=='read':
            original=R.read_row
            def read(db,rowid):
                row=original(db,rowid)
                if row['id']=='1'*32:raise sqlite3.OperationalError('synthetic-sensitive-detail')
                return row
            return patch.object(R,'read_row',read)
        body='a'*200001 if kind=='oversize' else '  \n\t';p=dict(self.p,review_body_sha256=U.sha(body))
        with sqlite3.connect(self.root/'workspace/knowledge/knowledge.db') as db:db.execute('UPDATE documents SET body=?,provenance=? WHERE id=?',(body,json.dumps(p),'1'*32))
        return nullcontext()
    def test_stage_exception_matrix(self):
        for stage in ['documents','catalog','upload']:
            for kind in ['oversize','read','empty']:
                with self.subTest(stage=stage,kind=kind):
                    self.setUp();good=self.selected('publication_observations')[0]
                    with self.failure(kind):
                        if stage=='documents':
                            docs=self.s.documents();bad=next(d for d in docs if d.source=='manual');self.assertIsInstance(bad,U.UnavailableDocument);self.assertIn('不可上传',bad.error)
                        elif stage=='catalog':
                            cards=self.s.catalog()['items'];bad=next(d for d in cards if d['source']=='manual');self.assertEqual(bad['state'],'failed');self.assertIn('不可上传',bad['error']);self.assertNotIn('synthetic-sensitive-detail',json.dumps(cards))
                        else:
                            bad={'number':R.number('manual','1'*32),'content_sha256':''};r=self.s.upload({'documents':[bad,good]});self.assertEqual([x['action'] for x in r['results']],['failed','created']);self.assertEqual(len(self.c.remote),1);self.assertNotIn(bad['number'],self.s.rows())

if __name__=='__main__':unittest.main()
