"""Single-library routing and ledger-only relocation; all writes use /tmp fixtures."""
import contextlib
import copy
import importlib.util
import io
import json
import os
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from test_weknora_push_results import FakeClient,SCHEMA,deal

P=Path(os.environ.get('PUSH_RESULTS_MODULE',Path(__file__).resolve().parents[1]/'scripts/weknora_push_results.py'))
spec=importlib.util.spec_from_file_location('push_results_merge',P);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)

class ReadClient:
 def __init__(self,docs,config):self.docs=docs;self.calls=[];self.config=config;self.writes=[]
 def get_knowledge(self,ident):self.calls.append(('GET',ident));return {'data':copy.deepcopy(self.docs[ident])}
 def _request(self,method,path,payload=None,**kwargs):
  self.calls.append((method,path))
  if method!='GET' or payload is not None:self.writes.append(method);raise AssertionError('Remote write forbidden')
  return {'data':{'id':m.PUBLIC_KB_ID}}
 def list_tags(self,kb,**kw):
  self.calls.append(('GET-tags',kb));return {'data':{'data':[{'name':n,'id':v} for n,v in self.tags.items()]}}
 def replace_tags(self,*args):self.writes.append('PUT-tags');raise AssertionError('Remote tag PUT forbidden')

class MergeTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory(prefix='results-merge-test-',dir='/tmp');self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name).resolve()
  (self.root/'coordination').mkdir();(self.root/'workspace/weknora').mkdir(parents=True)
  names=sorted(m.REQUIRED_TAG_NAMES)
  self.config={'schema_version':2,'knowledge_bases':{'public':{'knowledge_base_id':m.PUBLIC_KB_ID,'tags':{n:'public-'+str(i) for i,n in enumerate(names)}},'private':{'knowledge_base_id':m.LEGACY_PRIVATE_KB_ID,'tags':{n:'private-'+str(i) for i,n in enumerate(names)}}}}
  self.config_path=self.root/'coordination/weknora-tags.json';self.save_config()
  self.path=self.root/'workspace/weknora/ledger.db'
  with contextlib.closing(sqlite3.connect(self.path)) as db,db:
   db.execute(SCHEMA)
   for char in ('a','b'):
    db.execute('INSERT INTO result_ledger VALUES (?,?,?,?,?,?,?)',('SD-'+char*32,m.LEGACY_PRIVATE_KB_ID,'doc-'+char,m.digest('body-'+char),json.dumps([self.config['knowledge_bases']['private']['tags'][n] for n in ['类型-商单','隐私-私密']]),'original-time-'+char,'active'))
   db.execute('INSERT INTO result_ledger VALUES (?,?,?,?,?,?,?)',('CW-foreign',m.PUBLIC_KB_ID,'foreign','foreign-hash','[]','foreign-time','active'))
  self.client=ReadClient({f'doc-{c}':{'id':f'doc-{c}','knowledge_base_id':m.PUBLIC_KB_ID} for c in ('a','b')},SimpleNamespace(api_key='synthetic-test-key'))
  self.client.tags=self.config['knowledge_bases']['public']['tags']
  self.evidence=self.root/'evidence';self.args=SimpleNamespace(root=self.root,evidence=self.evidence,dry_run=False,numbers=None,manifest=self.root/'manifest.json',wait_index=False)
 def save_config(self):self.config_path.write_text(json.dumps(self.config,ensure_ascii=False))
 def rows(self,path=None):
  with contextlib.closing(sqlite3.connect((path or self.path).as_uri()+'?mode=ro',uri=True)) as db:return db.execute('SELECT * FROM result_ledger ORDER BY result_number').fetchall()
 def call(self,fn='relocate_private'):
  out=io.StringIO()
  with patch.object(m,'client_for',return_value=self.client),contextlib.redirect_stdout(out):getattr(m,fn)(self.args)
  return json.loads(out.getvalue())
 def test_relocate_success_backup_exact_original_and_only_two_columns_change(self):
  before=self.rows();result=self.call();after=self.rows();backup=Path(result['backup']);self.assertTrue(backup.is_file());self.assertEqual(self.rows(backup),before);self.assertEqual(backup.stat().st_mode&0o777,0o600)
  self.assertEqual(result['processed'],2);self.assertEqual(result['verified_rows'],2);self.assertEqual(result['remote_write_requests'],0)
  for old,new in zip(before,after):
   if old[0].startswith('SD-'):
    self.assertEqual([i for i in range(7) if old[i]!=new[i]],[1,4]);self.assertEqual(new[1],m.PUBLIC_KB_ID);self.assertEqual(json.loads(new[4]),[self.client.tags[n] for n in ['类型-商单','隐私-私密']])
   else:self.assertEqual(new,old)
  self.assertEqual([x[0] for x in self.client.calls],['GET','GET']);self.assertEqual(self.client.writes,[])
 def test_last_remote_still_private_stops_whole_batch_no_local_writes(self):
  self.client.docs['doc-b']['knowledge_base_id']=m.LEGACY_PRIVATE_KB_ID;before=self.path.read_bytes()
  with self.assertRaises(ValueError):self.call()
  self.assertEqual(self.path.read_bytes(),before);self.assertFalse(self.evidence.exists());self.assertEqual(self.client.writes,[])
 def test_wrong_remote_identity_stops(self):
  self.client.docs['doc-b']['id']='different-id';before=self.rows()
  with self.assertRaises(ValueError):self.call()
  self.assertEqual(self.rows(),before);self.assertFalse(self.evidence.exists())
 def test_missing_remote_stops_no_writes(self):
  del self.client.docs['doc-b'];before=self.rows()
  with self.assertRaises(KeyError):self.call()
  self.assertEqual(self.rows(),before);self.assertFalse(self.evidence.exists())
 def test_missing_public_tag_name_stops(self):
  del self.config['knowledge_bases']['public']['tags']['隐私-私密'];self.save_config();before=self.path.read_bytes()
  with self.assertRaises(ValueError):self.call()
  self.assertEqual(self.path.read_bytes(),before);self.assertFalse(self.evidence.exists())
 def test_unknown_legacy_tag_id_stops(self):
  with contextlib.closing(sqlite3.connect(self.path)) as db,db:db.execute('UPDATE result_ledger SET tag_ids_json=? WHERE knowledge_id="doc-b"',(json.dumps(['unknown-id']),))
  before=self.rows()
  with self.assertRaises(ValueError):self.call()
  self.assertEqual(self.rows(),before);self.assertFalse(self.evidence.exists())
 def test_duplicate_or_wrong_tag_container_stops(self):
  for tags in (None,{},[],['x','x'],[{'wrong':'value'}]):
   with self.subTest(tags=tags):
    with contextlib.closing(sqlite3.connect(self.path)) as db,db:db.execute('UPDATE result_ledger SET tag_ids_json=? WHERE knowledge_id="doc-b"',(json.dumps(tags),))
    before=self.rows()
    with self.assertRaises(ValueError):self.call()
    self.assertEqual(self.rows(),before)
 def test_replay_no_backup_no_write_and_no_private_config_dependency(self):
  self.call();before=self.path.read_bytes();backups=list(self.evidence.glob('*.db'));del self.config['knowledge_bases']['private'];self.save_config()
  with patch.object(m,'client_for',side_effect=AssertionError('No network/client on no-op')):
   out=io.StringIO()
   with contextlib.redirect_stdout(out):m.relocate_private(self.args)
  self.assertEqual(json.loads(out.getvalue())['processed'],0);self.assertEqual(self.path.read_bytes(),before);self.assertEqual(list(self.evidence.glob('*.db')),backups)
 def test_dry_run_get_only_readonly_sqlite_no_backup_or_file_writes(self):
  self.args.dry_run=True;before=self.path.read_bytes();original=sqlite3.connect;connections=[]
  def connect(path,*a,**kw):
   self.assertIn('?mode=ro',str(path));self.assertTrue(kw.get('uri'));connections.append(str(path));return original(path,*a,**kw)
  with patch.object(m.sqlite3,'connect',side_effect=connect):result=self.call()
  self.assertTrue(connections);self.assertEqual(result['processed'],2);self.assertEqual(self.path.read_bytes(),before);self.assertFalse(self.evidence.exists());self.assertEqual(self.client.writes,[])
 def test_single_transaction_rolls_back_if_second_readback_corrupted(self):
  with contextlib.closing(sqlite3.connect(self.path)) as db,db:
   db.execute('CREATE TRIGGER corrupt_second AFTER UPDATE ON result_ledger WHEN NEW.knowledge_id="doc-b" BEGIN UPDATE result_ledger SET content_sha256="corrupt" WHERE knowledge_id="doc-b"; END')
  before=self.rows()
  with self.assertRaises(ValueError):self.call()
  self.assertEqual(self.rows(),before);self.assertEqual(len(list(self.evidence.glob('*.db'))),1)
 def test_concurrent_ledger_change_during_preflight_stops(self):
  original=self.client.get_knowledge
  def get(ident):
   if ident=='doc-b':
    with contextlib.closing(sqlite3.connect(self.path)) as db,db:db.execute('UPDATE result_ledger SET last_pushed_at="concurrent" WHERE knowledge_id="doc-a"')
   return original(ident)
  with patch.object(self.client,'get_knowledge',side_effect=get):
   with self.assertRaises(ValueError):self.call()
  after=self.rows();self.assertTrue(all(row[1]==m.LEGACY_PRIVATE_KB_ID for row in after if row[0].startswith('SD-')));self.assertFalse(self.evidence.exists())
 def test_backup_failure_stops_before_updates(self):
  before=self.rows()
  with patch.object(m,'backup_ledger',side_effect=OSError('synthetic backup failure')):
   with self.assertRaises(OSError):self.call()
  self.assertEqual(self.rows(),before)
 def test_provision_read_only_no_private_requests_or_config_change(self):
  before=self.config_path.read_bytes();result=self.call('provision')
  self.assertTrue(result['readonly_verified']);self.assertEqual(result['public_tags'],22);self.assertEqual(before,self.config_path.read_bytes());self.assertEqual(self.client.writes,[])
  self.assertTrue(all(m.LEGACY_PRIVATE_KB_ID not in str(c) for c in self.client.calls));self.assertFalse(self.evidence.exists())
 def test_provision_missing_remote_tag_stops_without_creating(self):
  self.client.tags=dict(self.client.tags);del self.client.tags['隐私-私密']
  with self.assertRaises(ValueError):self.call('provision')
  self.assertEqual(self.client.writes,[])
 def test_provision_works_after_private_config_removed(self):
  del self.config['knowledge_bases']['private'];self.save_config();self.assertTrue(self.call('provision')['readonly_verified'])
 def test_push_private_real_entry_uses_public_and_private_tag_and_summary_false(self):
  self.evidence.mkdir();record=m.make_deal(deal());self.args.manifest.write_text(json.dumps([record]));client=FakeClient();client.config=SimpleNamespace(api_key='synthetic-test-key')
  with contextlib.closing(sqlite3.connect(self.path)) as db,db:db.execute('DELETE FROM result_ledger WHERE result_number=?',(record['result_number'],))
  with patch.object(m,'client_for',return_value=client),contextlib.redirect_stdout(io.StringIO()):m.push(self.args)
  post=next(c for c in client.calls if c[0]=='POST');self.assertIn('/'+m.PUBLIC_KB_ID+'/',post[1]);self.assertEqual(post[2]['tag_ids'],[self.client.tags[n] for n in record['tag_names']]);self.assertIn(self.client.tags['隐私-私密'],post[2]['tag_ids']);self.assertEqual(post[2]['process_config'],{'summary_enabled':False})
 def test_public_group_push_unchanged_no_process_override(self):
  self.evidence.mkdir();record=m.make_deal(deal(id='c'*32,sources=[{'chat_type':'group'}]));self.args.manifest.write_text(json.dumps([record]));client=FakeClient();client.config=SimpleNamespace(api_key='synthetic-test-key')
  with patch.object(m,'client_for',return_value=client),contextlib.redirect_stdout(io.StringIO()):m.push(self.args)
  post=next(c for c in client.calls if c[0]=='POST');self.assertIn('/'+m.PUBLIC_KB_ID+'/',post[1]);self.assertEqual(post[2]['tag_ids'],[self.client.tags[n] for n in ['类型-商单','来源-微信群','隐私-群内']]);self.assertNotIn('process_config',post[2])
 def test_push_dry_run_no_client_no_writable_sqlite_no_files(self):
  record=m.make_deal(deal());self.args.manifest.write_text(json.dumps([record]));self.args.dry_run=True;before=self.path.read_bytes();original=sqlite3.connect
  def connect(path,*a,**kw):self.assertIn('?mode=ro',str(path));return original(path,*a,**kw)
  with patch.object(m,'client_for',side_effect=AssertionError('Dry run must not call client')),patch.object(m.sqlite3,'connect',side_effect=connect),contextlib.redirect_stdout(io.StringIO()) as out:m.push(self.args)
  result=json.loads(out.getvalue());self.assertEqual(result['rows'][0]['knowledge_base_id'],m.PUBLIC_KB_ID);self.assertEqual(result['rows'][0]['action'],'relocate-private-first');self.assertFalse(self.evidence.exists());self.assertEqual(self.path.read_bytes(),before)
 def test_private_classification_is_preserved_but_private_config_not_used(self):
  r=m.make_deal(deal());self.assertEqual(r['target'],'private');del self.config['knowledge_bases']['private'];kb,tags=m.destination(r,self.config);self.assertEqual(kb,m.PUBLIC_KB_ID);self.assertIn(self.client.tags['隐私-私密'],tags)
 def test_relocate_cli_dry_run_requires_no_manifest(self):
  self.args.dry_run=True
  with patch('sys.argv',['weknora_push_results.py','relocate-private','--root',str(self.root),'--evidence',str(self.evidence),'--dry-run']),patch.object(m,'client_for',return_value=self.client),contextlib.redirect_stdout(io.StringIO()) as out:m.main()
  self.assertTrue(json.loads(out.getvalue())['dry_run']);self.assertFalse(self.evidence.exists())
 def test_wal_ledger_backup_and_relocation(self):
  with contextlib.closing(sqlite3.connect(self.path)) as db:self.assertEqual(db.execute('PRAGMA journal_mode=WAL').fetchone()[0],'wal')
  before=self.rows();result=self.call();self.assertEqual(self.rows(Path(result['backup'])),before);self.assertEqual(result['processed'],2)
 def test_verify_private_record_in_public_library_with_readonly_ledger(self):
  record=m.make_deal(deal());self.args.manifest.write_text(json.dumps([record]));self.evidence.mkdir()
  tags=[self.client.tags[n] for n in record['tag_names']]
  with contextlib.closing(sqlite3.connect(self.path)) as db,db:
   db.execute('UPDATE result_ledger SET knowledge_base_id=?,tag_ids_json=?,content_sha256=? WHERE knowledge_id="doc-a"',(m.PUBLIC_KB_ID,json.dumps(tags),record['content_sha256']))
  self.client.docs['doc-a']={'id':'doc-a','knowledge_base_id':m.PUBLIC_KB_ID,'metadata':{'content':record['content'],'status':'publish'},'tags':[{'id':t} for t in tags],'parse_status':'completed'}
  original=sqlite3.connect;before=self.path.read_bytes()
  def connect(path,*a,**kw):self.assertIn('?mode=ro',str(path));return original(path,*a,**kw)
  with patch.object(m.sqlite3,'connect',side_effect=connect):result=self.call('verify')
  self.assertEqual(result['verified'],1);self.assertEqual(self.client.writes,[]);self.assertEqual(self.path.read_bytes(),before)
 def test_relocate_cli_rejects_partial_selection(self):
  with patch('sys.argv',['weknora_push_results.py','relocate-private','--root',str(self.root),'--evidence',str(self.evidence),'--numbers','SD-any']),contextlib.redirect_stderr(io.StringIO()),patch.object(m,'client_for',side_effect=AssertionError('No requests on invalid CLI')):
   with self.assertRaises(SystemExit) as error:m.main()
  self.assertEqual(error.exception.code,2)

if __name__=='__main__':unittest.main()
