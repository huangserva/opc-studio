import hashlib
import json
import sqlite3
import tempfile
import unittest
from datetime import datetime,timedelta,timezone
from pathlib import Path
from types import SimpleNamespace

from publish_review import PublishReview,valid_number
from publication_metrics import PUBLISHED


class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='review-p3-unit-',dir='/tmp');self.root=Path(self.temp.name);self.library=self.root/'library';self.library.mkdir()
        self.store=SimpleNamespace(workspace=self.root,library=self.library)
        self.review=PublishReview(self.store)
        self.now=datetime.now(timezone.utc)
        self.row={'id':'a'*32,'kind':'published_manual','platform':'x','version':'v0002','url':'https://x.com/fixture/status/123','created_at':self.now.isoformat()}
        self.folder=self.library/'article-fixture';self.folder.mkdir()
        (self.folder/'article.json').write_text(json.dumps({'title':'真实发布作品'}))
        self.deliveries([self.row])
    def tearDown(self):self.temp.cleanup()
    def deliveries(self,rows): (self.folder/'deliveries.json').write_text(json.dumps(rows))
    def db(self):
        p=self.root/'publication-feedback/snapshots.db';p.parent.mkdir(exist_ok=True);c=sqlite3.connect(p)
        c.executescript('CREATE TABLE snapshots(data TEXT);CREATE TABLE observations(data TEXT);CREATE TABLE reviews(slug TEXT,data TEXT);');return c
    def point(self,metrics,age=1):return {'binding':{'slug':self.folder.name,'delivery_id':self.row['id']},'collected_at':(self.now-timedelta(hours=age)).isoformat(),'metrics':metrics,'range':{'kind':'cumulative','label':'用户导入'}}
    def test_all_published_kinds_only_and_reverse_order(self):
        rows=[{**self.row,'id':str(i),'kind':kind,'created_at':(self.now-timedelta(days=i)).isoformat()} for i,kind in enumerate(sorted(PUBLISHED))]
        self.deliveries(rows+[{**self.row,'kind':kind} for kind in ('exported','buffer_draft','buffer_submitted','buffer_unknown','buffer_failed')])
        result=self.review.listing();self.assertEqual(result['total'],4);self.assertEqual([r['delivery_id'] for r in result['items']],['0','1','2','3'])
    def test_missing_is_unknown_not_zero(self):
        row=self.review.listing()['items'][0]
        self.assertIsNone(row['metrics']['impressions']['value']);self.assertIn('未提供',row['metrics']['impressions']['reason'])
    def test_numeric_strings_nan_and_latest_per_metric(self):
        with self.db() as c:
            for p in [self.point({'Likes':5,'Views':1200},2),self.point({'Likes':'7','Impressions':float('nan'),'Replies':'not number','Bookmarks':0},1)]:c.execute('INSERT INTO snapshots VALUES(?)',(json.dumps(p),))
        metrics=self.review.listing()['items'][0]['metrics']
        self.assertEqual(metrics['likes']['value'],7);self.assertEqual(metrics['views']['value'],1200);self.assertEqual(metrics['bookmarks']['value'],0)
        self.assertIsNone(metrics['impressions']['value']);self.assertIsNone(metrics['replies']['value'])
        json.dumps(metrics,allow_nan=False)
    def test_negative_bool_inf_not_numbers(self):
        for value in (True,None,-1,float('inf'),float('nan'),'NaN','3万','bad'):self.assertIsNone(valid_number(value))
    def test_source_link_and_weekly_attribution(self):
        (self.folder/'source-item.json').write_text(json.dumps({'item_id':'x:123','sources':['G'],'url':self.row['url']}))
        p=self.root/'items/items.db';p.parent.mkdir();c=sqlite3.connect(p);c.execute('CREATE TABLE items(id TEXT,title TEXT,sources_json TEXT)');c.execute('INSERT INTO items VALUES(?,?,?)',('x:123','原素材全文标题','["G"]'));c.commit();c.close()
        result=self.review.listing();self.assertEqual((result['week_count'],result['week_from_items']),(1,1));source=result['items'][0]['source_item'];self.assertEqual(source['title'],'原素材全文标题');self.assertEqual(source['url'],'/library.html?item=x%3A123')
    def test_all_snapshots_observations_and_notes_readonly(self):
        with self.db() as c:
            for i in range(105):c.execute('INSERT INTO snapshots VALUES(?)',(json.dumps(self.point({'Likes':i},i+1)),))
            observation={'version_binding':{'slug':self.folder.name,'delivery_id':self.row['id']},'observed_at':self.now.isoformat(),'metrics':[{'scope':'post','label':'浏览','value':'1234'},{'scope':'account','label':'赞','value':900}]}
            c.execute('INSERT INTO observations VALUES(?)',(json.dumps(observation),));c.execute('INSERT INTO reviews VALUES(?,?)',(self.folder.name,json.dumps({'notes':'我手动记录的复盘结论'})))
        hashes={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in self.root.rglob('*') if p.is_file()}
        result=self.review.listing(self.folder.name);self.assertEqual(len(result['items'][0]['snapshots']),106);self.assertEqual(result['review']['notes'],'我手动记录的复盘结论');self.assertEqual(result['items'][0]['metrics']['views']['value'],1234)
        self.assertEqual(hashes,{str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in self.root.rglob('*') if p.is_file()})
    def test_cached_x_metrics_no_remote_or_credentials(self):
        path=self.root/'x-official/feedback/42.json';path.parent.mkdir(parents=True);path.write_text(json.dumps({'synced_at':self.now.isoformat(),'posts':{'123':{'created_at':(self.now-timedelta(days=8)).isoformat(),'metrics':{'like_count':11,'impression_count':2000}}}}))
        result=self.review.listing();row=result['items'][0];self.assertEqual(row['metrics']['likes']['value'],11);self.assertEqual(row['metrics']['impressions']['value'],2000);self.assertEqual(result['week_count'],0);self.assertEqual(row['time_basis'],'发布时间')
    def test_read_empty_does_not_create_database(self):
        self.review.listing();self.assertFalse((self.root/'publication-feedback').exists());self.assertFalse((self.root/'items').exists())
    def test_missing_slug_explicit_error(self):
        with self.assertRaisesRegex(ValueError,'没有已发布'):self.review.listing('no-such')


if __name__=='__main__':unittest.main()
