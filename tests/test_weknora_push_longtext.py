import sys,json,sqlite3,tempfile,unittest,subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import weknora_push_longtext as P
DDL='CREATE TABLE result_ledger (result_number TEXT PRIMARY KEY,knowledge_base_id TEXT NOT NULL,knowledge_id TEXT NOT NULL UNIQUE,content_sha256 TEXT NOT NULL,tag_ids_json TEXT NOT NULL,last_pushed_at TEXT NOT NULL,remote_state TEXT NOT NULL DEFAULT \'active\')'
class ApiError(Exception):
 def __init__(self,status=None):self.status=status
class Client:
 def __init__(self):self.config=SimpleNamespace(api_key='fake-key-value');self.remote={};self.calls=[];self.fail_create=False;self.fail_tags=False
 def create_manual(self,title,content,tags,kb):
  self.calls.append(('create',title,content,list(tags),kb))
  if self.fail_create:raise ApiError()
  kid='id-'+str(len(self.remote));self.remote[kid]={'id':kid,'knowledge_base_id':kb,'metadata':{'content':content,'status':'publish'},'tags':[{'id':t} for t in tags]};return {'data':self.remote[kid]}
 def get_knowledge(self,kid):
  self.calls.append(('get',kid))
  if kid not in self.remote:raise ApiError(404)
  return {'data':self.remote[kid]}
 def update_manual(self,kid,title,content):
  self.calls.append(('update',kid,content));self.remote[kid]['metadata']={'content':content,'status':'publish'}
 def replace_tags(self,kid,tags,kb):
  self.calls.append(('tags',kid,list(tags),kb))
  if self.fail_tags:raise ApiError(500)
  self.remote[kid]['tags']=[{'id':t} for t in tags]
def doc(ident='a:b',source='articles',body=' 原样首段\r\n\r\n第二段末尾\t '):
 return P.render(dict(id=ident,source=source,title='标题',body=body,url='https://example.org/a',modified='2026-11-03T12:00:00Z'),'public-tag')
class Tests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory(prefix='weknora-longtext-test-',dir='/tmp');self.root=Path(self.tmp.name);self.addCleanup(self.tmp.cleanup);self.path=self.root/'ledger.db'
  with sqlite3.connect(self.path) as db:db.execute(DDL);db.execute('INSERT INTO result_ledger VALUES(?,?,?,?,?,?,?)',('SD-other',P.KB,'other-id','hash','[]','time','active'))
  self.ledger=P.Ledger(self.path);self.client=Client()
 def test_verbatim_body_and_fixed_metadata(self):
  d=doc();self.assertEqual(d.content.partition(P.MARKER)[2],d.body);self.assertTrue(d.content.startswith('# 【长文·作品】CW-a-b'));self.assertIn('- 隐私：公开',d.content);self.assertEqual(d.tag_ids,('public-tag',));self.assertIn('documents.id=a:b',d.content)
 def test_external_is_public(self):self.assertIn('- 隐私：公开',doc(source='external-references').content);self.assertEqual(doc(source='external-references').number,'WC-a-b')
 def test_create_skip_no_repeated_write(self):
  d=doc();self.assertEqual(P.push_one(self.client,self.ledger,d),'created');calls=len(self.client.calls);self.assertEqual(P.push_one(self.client,self.ledger,d),'skipped');self.assertEqual(len(self.client.calls),calls)
 def test_update_full_text_with_existing_id(self):
  d=doc();P.push_one(self.client,self.ledger,d);new=doc(body='完全更换的正文\n末尾');self.assertEqual(P.push_one(self.client,self.ledger,new),'updated');kid=self.ledger.get(d.number)['knowledge_id'];self.assertEqual(self.client.remote[kid]['metadata'],{'content':new.content,'status':'publish'});self.assertEqual(sum(c[0]=='create' for c in self.client.calls),1)
 def test_changed_tags_replace_whole_set_separately(self):
  d=doc();P.push_one(self.client,self.ledger,d);new=P.Document(d.number,d.source_id,d.source,d.title,d.body,d.content,d.content_sha256,('new-public-tag',));P.push_one(self.client,self.ledger,new);self.assertEqual(self.client.calls[-1][0],'tags');self.assertEqual(self.client.calls[-1][2],['new-public-tag']);self.assertFalse(any(c[0]=='update' for c in self.client.calls))
 def test_tag_failure_keeps_id_and_can_resume(self):
  d=doc();P.push_one(self.client,self.ledger,d);new=P.Document(d.number,d.source_id,d.source,d.title,d.body+'x',d.content+'x',P.sha(d.content+'x'),('new',));self.client.fail_tags=True
  with self.assertRaises(ApiError):P.push_one(self.client,self.ledger,new)
  self.assertEqual(self.ledger.get(d.number)['remote_state'],'tags_pending');self.client.fail_tags=False;P.push_one(self.client,self.ledger,new);self.assertEqual(sum(c[0]=='create' for c in self.client.calls),1);self.assertEqual(sum(c[0]=='update' for c in self.client.calls),1)
 def test_remote_missing_stops_no_recreate(self):
  d=doc();P.push_one(self.client,self.ledger,d);self.client.remote.clear();result=P.push_all(self.client,self.ledger,[doc(body='changed'),doc('other')],interval=0);self.assertTrue(result['stopped']);self.assertEqual(sum(c[0]=='create' for c in self.client.calls),1)
 def test_uncertain_create_blocks_retry(self):
  d=doc();self.client.fail_create=True
  with self.assertRaises(ApiError):P.push_one(self.client,self.ledger,d)
  self.assertEqual(self.ledger.get(d.number)['remote_state'],'creating');self.client.fail_create=False
  with self.assertRaises(P.SafetyStop):P.push_one(self.client,self.ledger,d)
  self.assertEqual(sum(c[0]=='create' for c in self.client.calls),1)
 def test_failure_continues_other_documents(self):
  self.client.fail_create=True;result=P.push_all(self.client,self.ledger,[doc('one'),doc('two')],interval=0);self.assertEqual(len(result['failed']),2);self.assertFalse(result['stopped'])
 def test_foreign_prefix_cannot_be_written_and_other_rows_unchanged(self):
  d=doc();P.push_one(self.client,self.ledger,d)
  with self.assertRaises(P.SafetyStop):self.ledger.get('SD-other')
  with sqlite3.connect(self.path) as db:self.assertEqual(db.execute('SELECT knowledge_id,content_sha256 FROM result_ledger WHERE result_number=?',('SD-other',)).fetchone(),('other-id','hash'))
 def test_wrong_kb_rejected(self):
  d=doc();P.push_one(self.client,self.ledger,d)
  with sqlite3.connect(self.path) as db:db.execute('UPDATE result_ledger SET knowledge_base_id=? WHERE result_number=?',('wrong',d.number))
  with self.assertRaises(P.SafetyStop):P.push_one(self.client,self.ledger,doc(body='changed'))
 def test_source_only_read_and_empty_excluded_collision_rejected(self):
  source=self.root/'knowledge.db';tags=self.root/'tags.json';tags.write_text(json.dumps({'knowledge_base_id':P.KB,'tags':{'隐私-公开':'public-tag'}}))
  with sqlite3.connect(source) as db:
   db.execute('CREATE TABLE documents(id TEXT,source TEXT,title TEXT,body TEXT,url TEXT,modified TEXT)');db.executemany('INSERT INTO documents VALUES(?,?,?,?,?,?)',[('id','articles','标题','text','','date'),('empty','external-references','空文','','','date')])
  before=source.read_bytes();values,empty=P.read_documents(source,tags);self.assertEqual(len(values),1);self.assertEqual(len(empty),1);self.assertEqual(source.read_bytes(),before)
  with sqlite3.connect(source) as db:db.executemany('INSERT INTO documents VALUES(?,?,?,?,?,?)',[('a:b','articles','x','x','','date'),('a/b','articles','y','y','','date')])
  with self.assertRaises(P.SafetyStop):P.read_documents(source,tags)
 def test_key_guard_does_not_write(self):
  with self.assertRaises(P.SafetyStop):P.push_one(self.client,self.ledger,doc(body='fake-key-value'))
  self.assertEqual(self.client.calls,[])
 def test_empty_selection_fails_before_any_push(self):
  script=Path(P.__file__)
  result=subprocess.run([sys.executable,str(script),'--push','--numbers'],capture_output=True,text=True)
  self.assertEqual(result.returncode,2);self.assertIn('expected at least one argument',result.stderr)
 def test_schema_not_created_or_changed(self):
  missing=self.root/'missing.db'
  with self.assertRaises(sqlite3.OperationalError):P.Ledger(missing)
  self.assertFalse(missing.exists())
  with sqlite3.connect(self.path) as db:before=db.execute("SELECT sql FROM sqlite_master WHERE name='result_ledger'").fetchone()[0]
  P.push_one(self.client,self.ledger,doc())
  with sqlite3.connect(self.path) as db:self.assertEqual(db.execute("SELECT sql FROM sqlite_master WHERE name='result_ledger'").fetchone()[0],before)
if __name__=='__main__':unittest.main()
