import importlib.util
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

P=Path(__file__).resolve().parents[1]/'scripts/weknora_push_results.py'
spec=importlib.util.spec_from_file_location('push_results',P);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
SCHEMA='CREATE TABLE result_ledger(result_number TEXT PRIMARY KEY,knowledge_base_id TEXT NOT NULL,knowledge_id TEXT NOT NULL UNIQUE,content_sha256 TEXT NOT NULL,tag_ids_json TEXT NOT NULL,last_pushed_at TEXT NOT NULL,remote_state TEXT NOT NULL DEFAULT "active")'

def deal(**overrides):
 d={'id':'a'*32,'revision':1,'title':'测试商单','customer':'测试品牌','stage':'unknown','original_group':'已付','sources':[],'created_at':'2026-11-03','updated_at':'2026-11-03','original_text':'不得复制私密原话'};d.update(overrides);return d

def record(content='whole text'):
 return m.pack('SD-'+'a'*32,'title',content,'private',['类型-商单','来源-Agent会话','隐私-私密'],'ref','unknown -> private')

class Missing(RuntimeError):status=404
class FakeClient:
 def __init__(self):self.docs={};self.calls=[];self.fail_create=False
 def get_knowledge(self,ident):
  self.calls.append(('GET',ident))
  if ident not in self.docs:raise Missing('not found')
  return {'data':self.docs[ident]}
 def _request(self,method,path,payload=None):
  self.calls.append((method,path,payload))
  if method=='POST':
   ident='doc-'+str(len(self.docs)+1);kb=path.split('/')[2]
   self.docs[ident]={'id':ident,'knowledge_base_id':kb,'metadata':{'content':payload['content'],'status':payload['status']},'tags':[{'id':x} for x in payload['tag_ids']],'parse_status':'completed'}
   if self.fail_create:raise ConnectionResetError('uncertain create')
   return {'data':{'id':ident}}
  if method=='PUT':
   ident=path.rsplit('/',1)[1];self.docs[ident]['metadata']={'content':payload['content'],'status':payload['status']};return {'data':self.docs[ident]}
  raise AssertionError('Unexpected method')
 def replace_tags(self,ident,tags,kb):
  self.calls.append(('TAGS',ident,tags,kb));self.docs[ident]['tags']=[{'id':x} for x in tags]

class PrepareTests(unittest.TestCase):
 def test_unknown_source_and_bookkeeping_bucket_are_private(self):
  for bucket in ['已付','未付','测试群','']:
   result=m.make_deal(deal(original_group=bucket))
   self.assertEqual(result['target'],'private');self.assertEqual(result['tag_names'],['类型-商单','隐私-私密'])
 def test_explicit_group_is_public(self):
  result=m.make_deal(deal(sources=[{'chatroom_id':'123@chatroom'}]))
  self.assertEqual(result['target'],'public');self.assertEqual(result['tag_names'],['类型-商单','来源-微信群','隐私-群内'])
 def test_mixed_private_and_group_never_becomes_public(self):
  result=m.make_deal(deal(sources=[{'chat_type':'group'},{'chat_type':'private'}]))
  self.assertEqual(result['target'],'private')
 def test_private_pii_and_original_quote_are_omitted(self):
  result=m.make_deal(deal(contact='微信：private_contact',notes='电话13812345678 邮箱test@example.com wxid_example'))
  for value in ['13812345678','test@example.com','wxid_example','private_contact','不得复制私密原话']:
   self.assertNotIn(value,result['content'])
  self.assertNotIn('## 原话',result['content'])
 def test_pending_status_is_first_metadata_line_and_created_is_not_event_date(self):
  result=m.make_deal(deal());self.assertEqual(result['content'].splitlines()[1],'- 状态：待核对')
  self.assertIn('业务时间未记录',result['content'])
 def test_decision_identity_and_source_position(self):
  with tempfile.TemporaryDirectory() as tmp:
   p=Path(tmp)/'decision.md';p.write_text('建议先不做\n黄总定：本地部署\n')
   quote='黄总定：本地部署';r={'file':p.name,'line':2,'quote':quote,'result_number':'JD-decision-'+m.digest(quote)[:10],'title':'本地部署','status':'有效','aliases':[]}
   result=m.make_decision(r,tmp);self.assertEqual(result['tag_names'],['类型-决定','来源-Agent会话','隐私-内部'])
   self.assertEqual(result,m.make_decision(r,tmp))
   r['line']=1
   with self.assertRaises(ValueError):m.make_decision(r,tmp)
 def test_quotation_from_other_paragraph_is_rejected(self):
  with tempfile.TemporaryDirectory() as tmp:
   p=Path(tmp)/'d.md';p.write_text('黄总定：第一段\n黄总定：第二段\n')
   quote='黄总定：第二段';r={'file':'d.md','line':1,'quote':quote,'result_number':'JD-d-'+m.digest(quote)[:10],'title':'第二段','status':'有效'}
   with self.assertRaises(ValueError):m.make_decision(r,tmp)
 def test_manifest_duplicate_and_modified_hash_rejected(self):
  r=m.make_deal(deal())
  with self.assertRaises(ValueError):m.validate([r,r])
  r['content']+='changed'
  with self.assertRaises(ValueError):m.validate([r])

class DateTests(unittest.TestCase):
 def test_entry_date_beats_stale_sibling_heading_and_inner_old_policy_date(self):
  lines=['# 项目','# 班底（2026-10-15 更新）','## 在途','  - 11-01 17:2x 黄总：后续沿用10-15方案']
  self.assertEqual(m.decision_date(lines,4)[0],'2026-11-01')
 def test_undated_peer_heading_does_not_inherit_previous_dated_peer(self):
  self.assertEqual(m.decision_date(['# 项目','## 旧（2026-10-15）','## 新','黄总：部署'],4)[0],'未记录')
 def test_enclosing_heading_or_parent_entry(self):
  self.assertEqual(m.decision_date(['# 项目','## 决定（11-03）','黄总：部署'],3)[0],'2026-11-03')
  self.assertEqual(m.decision_date(['- 2026-11-01 本轮','  - 黄总：部署'],2)[0],'2026-11-01')
 def test_invalid_date_and_range_are_not_guessed(self):
  self.assertEqual(m.decision_date(['# 2026-10-12 ~ 10-13','黄总：部署'],2)[0],'未记录')
  self.assertEqual(m.decision_date(['黄总 11-31 定：部署'],1)[0],'未记录')
 def test_owner_excerpt_omits_coordinator_without_losing_entry_date(self):
  with tempfile.TemporaryDirectory() as tmp:
   quote='11-01 17:2x 黄总：部署。主控已执行。';p=Path(tmp)/'d.md';p.write_text(quote)
   r={'file':'d.md','line':1,'quote':quote,'owner_quotes':['黄总：部署。'],'result_number':'JD-d-'+m.digest(quote)[:10],'title':'部署','status':'有效'}
   out=m.make_decision(r,tmp)['content'];self.assertIn('- 时间：2026-11-01',out)
   self.assertNotIn('主控已执行',out);self.assertIn('> 黄总：部署。',out)
   r['owner_quotes']=['杜撰的原话']
   with self.assertRaises(ValueError):m.make_decision(r,tmp)
 def test_multiday_task_opening_is_not_a_date_scope(self):
  lines=['## 在途','- 2026-10-30 10:1x 推特项目启动','  - 黄总21:0x：复核改回Codex']
  self.assertEqual(m.decision_date(lines,3)[0],'未记录')
 def test_exact_4090_date_regression(self):
  # Real frozen data checked separately; this test guards the exact regression.
  self.assertEqual(m.decision_date(['## 班底（2026-10-15 更新）','## 在途','  - 11-01 17:2x **黄总：WeKnora 要本地部署在 4090**'],3)[0],'2026-11-01')


class LedgerTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.path=Path(self.temp.name)
  db=sqlite3.connect(self.path/'ledger.db');db.execute(SCHEMA);db.execute('INSERT INTO result_ledger VALUES (?,?,?,?,?,?,?)',('CW-foreign','public','foreign','h','[]','unchanged','active'));db.commit();db.close()
  self.db=m.ledger_connect(self.path/'ledger.db');self.addCleanup(self.db.close);self.client=FakeClient();self.journal={};self.jp=self.path/'journal.json'
 def push(self,r=None,tags=None):return m.push_one(self.client,self.db,r or record(),'private',tags or ['a','b','c'],self.journal,self.jp)
 def test_create_then_replay_is_idempotent_and_foreign_prefix_is_untouched(self):
  first=self.push();second=self.push();self.assertEqual(first['knowledge_id'],second['knowledge_id']);self.assertEqual(second['action'],'skip')
  self.assertEqual(sum(x[0]=='POST' for x in self.client.calls),1)
  self.assertEqual(tuple(self.db.execute('SELECT * FROM result_ledger WHERE result_number="CW-foreign"').fetchone()),('CW-foreign','public','foreign','h','[]','unchanged','active'))
 def test_update_sends_entire_content_publishes_and_replaces_tags(self):
  self.push();r=record('entire new body');out=self.push(r,['d','e','f']);self.assertEqual(out['action'],'update')
  call=next(x for x in self.client.calls if x[0]=='PUT');self.assertEqual(call[2]['content'],'entire new body');self.assertEqual(call[2]['status'],'publish')
  self.assertEqual(call[2]['process_config'],{'summary_enabled':False})
  self.assertEqual(self.client.docs[out['knowledge_id']]['tags'],[{'id':x} for x in ['d','e','f']])
 def test_private_create_only_disables_summary(self):
  self.push();call=next(x for x in self.client.calls if x[0]=='POST')
  self.assertEqual(call[2]['process_config'],{'summary_enabled':False})
 def test_public_create_and_update_do_not_override_processing(self):
  r=m.pack('JD-public-123','title','public body','public',['类型-决定','来源-Agent会话','隐私-内部'],'ref','internal')
  m.push_one(self.client,self.db,r,'public',['a','b','c'],self.journal,self.jp)
  r['content']='new public body';r['content_sha256']=m.digest(r['content'])
  m.push_one(self.client,self.db,r,'public',['a','b','c'],self.journal,self.jp)
  mutations=[c for c in self.client.calls if c[0] in ('POST','PUT')];self.assertEqual(len(mutations),2)
  for call in mutations:self.assertNotIn('process_config',call[2])
 def test_missing_ledger_is_not_created(self):
  missing=self.path/'missing.db'
  with self.assertRaises(ValueError):m.ledger_connect(missing)
  self.assertFalse(missing.exists())
 def test_remote_missing_stops_and_does_not_recreate(self):
  self.push();self.client.docs.clear()
  with self.assertRaises(Missing):self.push()
  self.assertEqual(sum(x[0]=='POST' for x in self.client.calls),1)
 def test_unknown_create_stops_replay_without_second_post(self):
  self.client.fail_create=True
  with self.assertRaises(ConnectionResetError):self.push()
  with self.assertRaises(ValueError):self.push()
  self.assertEqual(sum(x[0]=='POST' for x in self.client.calls),1)
 def test_recover_received_id_without_duplicate(self):
  result=self.push();self.db.execute('DELETE FROM result_ledger WHERE result_number=?',(result['result_number'],));self.db.commit()
  result=self.push();self.assertEqual(result['action'],'recover');self.assertEqual(sum(x[0]=='POST' for x in self.client.calls),1)
 def test_remote_hash_drift_stops(self):
  result=self.push();self.client.docs[result['knowledge_id']]['metadata']['content']='remote drift'
  with self.assertRaises(ValueError):self.push()
 def test_other_prefix_never_writes(self):
  r=record();r['result_number']='WC-unowned'
  with self.assertRaises(ValueError):self.push(r)
  self.assertEqual(self.client.calls,[])
 def test_schema_is_validated_without_migration(self):
  self.db.execute('ALTER TABLE result_ledger ADD COLUMN wrong TEXT');self.db.commit()
  with self.assertRaises(ValueError):m.ledger_connect(self.path/'ledger.db')

class RetagJDTests(unittest.TestCase):
 setUp=LedgerTests.setUp
 def ready(self):
  r=m.pack('JD-test-123','decision','unchanged body','public',['类型-决定','来源-Agent会话','隐私-内部'],'ref','internal')
  self.client.docs['jd']={'id':'jd','knowledge_base_id':'public','metadata':{'content':r['content'],'status':'publish'},'tags':[{'id':x} for x in ['a','b']],'parse_status':'finalizing'}
  with self.db:self.db.execute('INSERT INTO result_ledger VALUES (?,?,?,?,?,?,?)',(r['result_number'],'public','jd',r['content_sha256'],json.dumps(['a','b']),'original-time','active'))
  return r
 def migrate(self,r):return m.retag_jd_one(self.client,self.db,r,'public',['a','b','c'])
 def test_retag_changes_only_three_tags_and_one_ledger_column(self):
  r=self.ready();before=dict(self.db.execute('SELECT * FROM result_ledger WHERE result_number=?',(r['result_number'],)).fetchone());out=self.migrate(r)
  after=dict(self.db.execute('SELECT * FROM result_ledger WHERE result_number=?',(r['result_number'],)).fetchone())
  self.assertEqual(out['action'],'replace-tags');self.assertEqual([k for k in before if before[k]!=after[k]],['tag_ids_json'])
  self.assertEqual(self.client.docs['jd']['metadata'],{'content':'unchanged body','status':'publish'});self.assertEqual(self.client.docs['jd']['parse_status'],'finalizing')
  self.assertEqual([x[0] for x in self.client.calls],['GET','TAGS','GET'])
  self.assertEqual(self.db.execute('SELECT last_pushed_at FROM result_ledger WHERE result_number="CW-foreign"').fetchone()[0],'unchanged')
 def test_replay_has_no_tag_put_or_ledger_write(self):
  r=self.ready();self.migrate(r);self.client.calls.clear();before=self.db.total_changes;out=self.migrate(r)
  self.assertEqual(out['action'],'skip');self.assertEqual([x[0] for x in self.client.calls],['GET']);self.assertEqual(self.db.total_changes,before)
 def test_sd_and_foreign_prefix_are_rejected_without_requests(self):
  for prefix in ['SD-','CW-','WC-']:
   r=self.ready() if prefix=='SD-' else m.pack('JD-any','x','body','public',['类型-决定','来源-Agent会话','隐私-内部'],'ref','internal')
   r['result_number']=prefix+'not-authorized'
   with self.assertRaises(ValueError):self.migrate(r)
  self.assertEqual(self.client.calls,[])
 def test_remote_missing_stops_without_create(self):
  r=self.ready();self.client.docs.clear()
  with self.assertRaises(Missing):self.migrate(r)
  self.assertEqual([x[0] for x in self.client.calls],['GET'])
 def test_body_drift_stops_before_any_tag_put(self):
  r=self.ready();self.client.docs['jd']['metadata']['content']='changed remotely'
  with self.assertRaises(ValueError):self.migrate(r)
  self.assertNotIn('TAGS',[x[0] for x in self.client.calls])
 def test_ledger_or_library_drift_stops(self):
  r=self.ready();self.db.execute('UPDATE result_ledger SET content_sha256="drift" WHERE result_number=?',(r['result_number'],));self.db.commit()
  with self.assertRaises(ValueError):self.migrate(r)
  self.assertEqual(self.client.calls,[])
  self.db.execute('UPDATE result_ledger SET content_sha256=? WHERE result_number=?',(r['content_sha256'],r['result_number']));self.db.commit()
  self.client.docs['jd']['knowledge_base_id']='private'
  with self.assertRaises(ValueError):self.migrate(r)
  self.assertNotIn('TAGS',[x[0] for x in self.client.calls])
 def test_unpersisted_tag_replacement_does_not_advance_ledger(self):
  from unittest.mock import patch
  r=self.ready()
  with patch.object(self.client,'replace_tags',return_value={}):
   with self.assertRaises(ValueError):self.migrate(r)
  self.assertEqual(json.loads(self.db.execute('SELECT tag_ids_json FROM result_ledger WHERE result_number=?',(r['result_number'],)).fetchone()[0]),['a','b'])
 def test_uncertain_tag_put_can_recover_without_body_or_second_put(self):
  from unittest.mock import patch
  r=self.ready();original=self.client.replace_tags
  def uncertain(*args):original(*args);raise ConnectionResetError('uncertain response')
  with patch.object(self.client,'replace_tags',side_effect=uncertain):
   with self.assertRaises(ConnectionResetError):self.migrate(r)
  out=self.migrate(r);self.assertEqual(out['action'],'skip');self.assertTrue(out['ledger_tags_changed'])
  self.assertEqual(sum(c[0]=='TAGS' for c in self.client.calls),1)
 def test_jd_without_internal_tag_is_rejected(self):
  r=self.ready();r['tag_names'].remove('隐私-内部')
  with self.assertRaises(ValueError):m.validate([r])

if __name__=='__main__':unittest.main()
