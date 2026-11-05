"""Publication actions reuse the legacy API and canonical source identity."""
import json,sqlite3,hashlib,unittest
from pathlib import Path
from datetime import datetime,timedelta,timezone
from tests import test_publish_review as old
from tests import test_publication_reviews_api as api
from publish_review import PublishReview
class SourceIdentityTests(unittest.TestCase):
 setUp=old.ReviewTests.setUp
 tearDown=old.ReviewTests.tearDown
 deliveries=old.ReviewTests.deliveries
 def source(self,ref):
  (self.folder/'source-item.json').write_text(json.dumps(ref));return self.review.listing()['items'][0]['source_item']
 def test_legacy_source_has_canonical_logo_and_grok_channel(self):
  s=self.source({'item_id':'x:123','sources':['G']})
  self.assertEqual(s['platform_info']['key'],'x');self.assertEqual(s['channels'],['grok']);self.assertEqual(s['channel_details'][0]['display_name'],'Grok Bot');self.assertEqual(s['url'],'/library.html?item=x%3A123')
 def test_enriched_source_preserves_douyin_aisa_and_material_link(self):
  s=self.source({'item_id':'douyin:9','sources':['V'],'platform':'douyin','channels':['aisa'],'platform_info':{'key':'douyin'},'channel_details':[{'key':'aisa'}]})
  self.assertEqual(s['platform_info']['key'],'douyin');self.assertEqual(s['channel_details'][0]['display_name'],'AIsa');self.assertEqual(s['channel_details'][0]['note'],'视频榜');self.assertTrue(s['url'].endswith('douyin%3A9'))
 def test_items_identity_and_k1_are_read_only(self):
  p=self.root/'clues/clues.db';p.parent.mkdir();c=sqlite3.connect(p);c.executescript("CREATE TABLE clue_objects(id TEXT,canonical_key TEXT,platform TEXT);CREATE TABLE clue_channels(clue_id TEXT,channel TEXT);INSERT INTO clue_objects VALUES('1','x:123','x');INSERT INTO clue_channels VALUES('1','aisa');");c.commit();c.close()
  before=hashlib.sha256(p.read_bytes()).hexdigest();s=self.source({'item_id':'x:123','sources':[]})
  self.assertEqual(s['channels'],['aisa']);self.assertEqual(before,hashlib.sha256(p.read_bytes()).hexdigest())
 def test_unknown_source_still_safe_and_excluded_deliveries_stay_excluded(self):
  self.source({'item_id':'odd:123','sources':[]});s=self.review.listing()['items'][0]['source_item'];self.assertEqual(s['platform'],'web');self.assertEqual(s['channels'],['unknown'])
  self.deliveries([{**self.row,'kind':k} for k in ['exported','buffer_draft','buffer_submitted']]);self.assertEqual(self.review.listing()['total'],0)

class ActionApiTests(unittest.TestCase):
 setUp=api.PublicationReviewApiTests.setUp
 tearDown=api.PublicationReviewApiTests.tearDown
 request=api.PublicationReviewApiTests.request
 def test_same_note_saved_and_exported_twice_keeps_one_document_with_body_hash(self):
  slug=self.work['slug'];before=self.request('/api/knowledge')['total'];p='/api/feedback/review/'+slug
  self.request(p+'/notes',{'notes':'已验证参数，下一篇保留对照组。'})
  a=self.request(p+'/knowledge',{});self.request(p+'/notes',{'notes':'已验证参数，下一篇保留对照组。'});b=self.request(p+'/knowledge',{})
  self.assertEqual(a['document_id'],b['document_id']);self.assertTrue(b['duplicate']);self.assertEqual(self.request('/api/knowledge')['total'],before+1)
  doc=self.request('/api/knowledge/document/'+a['document_id']);self.assertIn('已验证参数',doc['body']);self.assertEqual(hashlib.sha256(doc['body'].encode()).hexdigest(),a['hash']);self.assertEqual(doc['provenance']['review_body_sha256'],a['hash']);self.assertIn('/feedback.html?',doc['provenance']['source_locator'])
 def test_empty_import_rejected_without_dropping_existing_values(self):
  slug=self.work['slug'];prior=self.request('/api/publish/review/'+slug)['items'][0]['metrics'];snap=self.snapshot;body={'slug':slug,'delivery_id':self.delivery['id'],'platform':'x','version':'v0001','collected_at':self.when.isoformat(),'range':snap['range'],'metrics':{},'provenance':{'type':'manual','url':'https://analytics.x.com/content','note':'仅测试'}}
  self.request('/api/feedback/import',body,expected_status=400);after=self.request('/api/publish/review/'+slug)['items'][0]['metrics'];self.assertEqual(after,prior);self.assertIsNone(after['replies']['value'])
 def test_wrong_origin_cannot_save_note_import_or_export(self):
  p='/api/feedback/review/'+self.work['slug'];self.request(p+'/notes',{'notes':'非法写入'},origin=False,expected_status=403);self.request(p+'/knowledge',{},origin=False,expected_status=403);self.request('/api/feedback/import',{},origin=False,expected_status=403)
if __name__=='__main__':unittest.main()
