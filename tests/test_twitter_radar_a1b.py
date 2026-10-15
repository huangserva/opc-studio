"""A1-b regression: real process death/deadlines, authoritative publication, safe retention."""
import contextlib,io,json,os,subprocess,sys,tempfile,time,unittest,threading
from pathlib import Path
from datetime import datetime,timedelta,timezone
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
import radar_common as common,radar_client as client,radar_collect as collector,radar_store as store,radar_bundle as builder,refresh_twitter_radar as entry
NOW=datetime(2026,10,15,6,tzinfo=timezone.utc)
def raw(handle='Audit',seq=1,**kw):
 tid=str(((int((NOW-timedelta(hours=1)).timestamp()*1000)-common.EPOCH_MS)<<22)+seq)
 return {'id':tid,'author':{'userName':handle,'followers':500},'text':'Agent 开源实测','viewCount':60000,'likeCount':600,**kw}
def payload(rows):return {'code':0,'data':{'tweets':rows}}
def targets():return [{'handle':'Audit'},{'handle':'Other'}]
class A1bTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory(prefix='a1b-case-',dir='/tmp');self.addCleanup(self.tmp.cleanup);self.repo=Path(self.tmp.name);self.root=store.workspace_root(self.repo)
 def collect(self,**kw):return collector.collect(api_key='offline',targets=targets(),run_id='20261015-060000-abcdef12',base_time=NOW,log=lambda *a:None,**kw)
 def test_h1a_false_code_and_shapes(self):
  samples=[{'success':False,'data':{'tweets':[]}}, {'success':False,'code':0,'data':{'tweets':[raw()]}}, {'code':'200','data':{'tweets':[]}}, {'code':200,'data':{'tweets':[]}}, {'code':0,'data':{}},{'code':0,'data':{'tweets':None}},{'code':0,'data':{'tweets':{}}}]
  for p in samples:
   r=client.fetch_account('Audit','offline',opener=lambda *a:(p,200));self.assertNotIn(r['status'],common.SUCCESS_STATUSES)
  self.assertEqual(client.fetch_account('Audit','offline',opener=lambda *a:(payload([]),200))['status'],'empty')
  print('H1-a business/shape: 7 rejected, explicit [] empty')
 def test_h1a_partial_preserves_page1_and_counts_coverage(self):
  def fetch(h,k,**kw):
   pages=[{**payload([raw(h,i) for i in range(20)]),'has_next_page':True,'next_cursor':'cursor'},{'code':401,'data':None}]
   return client.fetch_account(h,k,opener=lambda *a:(pages.pop(0),200),**kw)
  run=self.collect(fetch=fetch);self.assertEqual(run['success'],2);self.assertEqual(run['partial'],2);self.assertEqual(run['coverage'],1);self.assertEqual(run['api_calls'],4);self.assertTrue(run['complete']);self.assertTrue(all(a['status']=='partial' and a['pages_complete'] is False for a in run['accounts'].values()))
  self.assertEqual(run['accounts']['audit']['sampled_tweets'],20);print('H1-a partial: success=2 partial=2 calls=4 coverage=1.0 pages_complete=false')
 def seed(self,rid='20261015-060000-abcdef12'):
  snapshot={'run_id':rid,'tweets':[]};store.write_json_atomic(store.snapshot_path(self.root,rid,common.iso_utc(NOW)),snapshot)
  return store.publish_bundle(self.root,self.repo,rid,{'schema_version':common.SCHEMA_VERSION,'run_id':rid,'tweets':[]},started_at=common.iso_utc(NOW),coverage={})
 def test_h1b_public_is_authority_and_index_repair(self):
  self.seed();store.write_json_atomic(store.manifest_path(self.root),{'run_id':'wrong'})
  self.assertEqual(store.load_previous_snapshot(self.root,{'run_id':'wrong'})['run_id'],'20261015-060000-abcdef12')
  messages=[];store.recover_publication(self.root,self.repo,messages.append);doc=store.read_json(store.public_bundle_path(self.repo));index=store.read_manifest(self.root)
  self.assertTrue(messages);self.assertEqual(index['snapshot_sha256'],doc['manifest']['snapshot_sha256']);self.assertEqual(index['bundle_sha256'],store.sha256_bytes(store.public_bundle_path(self.repo).read_bytes()));print('H1-b wrong index ignored/repaired; public manifest and snapshot SHA verified')
 def test_h1b_snapshot_hash_and_escape_fail_closed(self):
  self.seed();doc=store.read_json(store.public_bundle_path(self.repo));path=self.root/doc['manifest']['snapshot_file'];path.write_text('{"run_id":"wrong"}');self.assertIsNone(store.load_previous_snapshot(self.root))
  doc['manifest']['snapshot_file']='../../outside.json';store.write_json_atomic(store.public_bundle_path(self.repo),doc);self.assertIsNone(store.load_previous_snapshot(self.root))
 def test_h1c_four_sigkills_next_round_recovers(self):
  for phase in ('snapshot','bundle','public','manifest'):
   with self.subTest(phase=phase):
    self.seed();new='20261015-070000-aabbccdd';gate=self.repo/'gate';gate.unlink(missing_ok=True)
    code=f'''import sys,time
from pathlib import Path
sys.path.insert(0,{str(ROOT/'scripts')!r});import radar_store as s
repo=Path({str(self.repo)!r});root=s.workspace_root(repo);rid={new!r};snap=s.snapshot_path(root,rid,'2026-10-15T07:00:00Z');dest={{'snapshot':snap,'bundle':s.bundle_path(root,rid),'public':s.public_bundle_path(repo),'manifest':s.manifest_path(root)}}[{phase!r}];original=s.os.replace
def replace(a,b):
 if Path(b)==dest:Path({str(gate)!r}).touch();time.sleep(60)
 return original(a,b)
s.os.replace=replace
with s.SingleInstanceLock(s.lock_path(root)):
 s.write_json_atomic(snap,{{'run_id':rid,'tweets':[]}})
 s.publish_bundle(root,repo,rid,{{'run_id':rid,'tweets':[]}},started_at='2026-10-15T07:00:00Z',coverage={{}})
'''
    p=subprocess.Popen([sys.executable,'-c',code],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    try:
     until=time.monotonic()+3
     while not gate.exists() and time.monotonic()<until and p.poll() is None:time.sleep(.01)
     self.assertTrue(gate.exists())
    finally:p.kill();p.communicate(timeout=2)
    self.assertEqual(p.returncode,-9);self.assertTrue(list(self.repo.rglob('*.tmp')))
    recovery=f"import sys;from pathlib import Path;sys.path.insert(0,{str(ROOT/'scripts')!r});import radar_store as s;r=Path({str(self.repo)!r});w=s.workspace_root(r)\nwith s.SingleInstanceLock(s.lock_path(w)):s.recover_publication(w,r)"
    r=subprocess.run([sys.executable,'-c',recovery],capture_output=True,text=True,timeout=3);self.assertEqual(r.returncode,0,r.stderr);self.assertEqual(list(self.repo.rglob('*.tmp')),[])
    authority=store.public_manifest(self.root,self.repo);self.assertEqual(store.read_manifest(self.root),authority);self.assertEqual(authority['run_id'],new if phase=='manifest' else '20261015-060000-abcdef12')
    print('H1-c SIGKILL '+phase+': exit=-9 next_round_tmp=0 index=public')
 def test_h1c_cleanup_ownership_and_symlink_boundaries(self):
  self.seed();public=self.repo/'public';owned=public/'.twitter-radar-bundle.json.abcdefgh.tmp';owned.touch();foreign=public/'unrelated.tmp';foreign.touch();outside=self.repo/'outside';outside.mkdir();protected=outside/'.20260901-000000-1234abcd.json.abcdefgh.tmp';protected.touch();link=store.snapshots_dir(self.root)/'2026-09-01';link.symlink_to(outside,target_is_directory=True)
  symlink=public/'.twitter-radar-bundle.json.zzzzzzzz.tmp';symlink.symlink_to(protected)
  store.cleanup_temporary_files(self.root,self.repo);self.assertFalse(owned.exists());self.assertTrue(foreign.exists());self.assertTrue(protected.exists());self.assertTrue(symlink.is_symlink());print('H1-c cleanup scope: owned removed; unrelated/symlink/outside preserved')
 def test_m9_daemon_hung_fetch_process_exits_and_records_failure(self):
  (self.repo/'scripts').mkdir();t=self.repo/'scripts/targets.json';t.write_text(json.dumps({'expected_count':2,'targets':targets()}));summary=self.repo/'summary.json'
  code=f'''import sys,time,os
sys.path.insert(0,{str(ROOT/'scripts')!r});import refresh_twitter_radar as e,radar_collect as c
os.environ['AISA_API_KEY']='offline'
def hang(*a,**kw):
 while True:time.sleep(1)
original=c.collect
e.collect=lambda **kw:original(fetch=hang,workers=1,**kw)
raise SystemExit(e.main(['--repo-root',{str(self.repo)!r},'--targets',{str(t)!r},'--deadline-seconds','0.1','--summary-json',{str(summary)!r}]))
'''
  before=time.monotonic();p=subprocess.run([sys.executable,'-c',code],capture_output=True,text=True,timeout=2.1);elapsed=time.monotonic()-before;self.assertEqual(p.returncode,1,p.stderr);self.assertLess(elapsed,2.1);s=json.loads(summary.read_text());self.assertEqual(s['api_calls'],1);self.assertEqual(s['failed'],2);self.assertEqual(set(s['failed_accounts'].values()),{'deadline_inflight','not_started'});self.assertEqual(len(list(store.snapshots_dir(self.root).rglob('*.json'))),1);print(f'M9 hung process: elapsed={elapsed:.3f}s exit=1 calls=1 failed=2 snapshot=1')
 def test_m9_request_timeout_remaining_and_start_accounting(self):
  position=[0.0];timeouts=[];calls=[]
  pages=[{**payload([raw(seq=i) for i in range(20)]),'has_next_page':True,'next_cursor':'next'},payload([])]
  def opener(url,key,timeout):timeouts.append(timeout);position[0]+=.09;return pages.pop(0),200
  client.fetch_account('Audit','offline',opener=opener,deadline=.1,timeout=20,base_time=NOW,clock=lambda:position[0],on_request=lambda:calls.append(1))
  self.assertEqual(len(calls),2);self.assertAlmostEqual(timeouts[0],.1);self.assertAlmostEqual(timeouts[1],.01);print('M9 request timeout=min(20, remaining): 0.10s -> 0.01s; calls=2 at launch')
  reported=collector.collect(api_key='offline',targets=[{'handle':'Audit'}],run_id='reported',fetch=lambda *a,**k:{'status':'deadline','calls':0,'tweets':[]},log=lambda *a:None)
  self.assertTrue(reported['deadline_hit']);self.assertEqual(reported['accounts']['audit']['status'],'not_started');self.assertFalse(reported['complete'])
  tick=[0.0]
  def finish_at_deadline(*a,**kw):tick[0]=1.0;return {'status':'empty','calls':1,'tweets':[]}
  simultaneous=collector.collect(api_key='offline',targets=[{'handle':'Audit'}],run_id='simultaneous',fetch=finish_at_deadline,deadline_seconds=.1,clock=lambda:tick[0],log=lambda *a:None)
  self.assertTrue(simultaneous['deadline_hit']);self.assertFalse(simultaneous['complete'])
  print('M9 boundary: reported deadline -> not_started; return at deadline still incomplete')
 def test_m9_inflight_second_page_calls_already_two(self):
  hold=threading.Event();self.addCleanup(hold.set)
  def fetch(h,k,**kw):
   count=[0]
   def opener(*a):
    count[0]+=1
    if count[0]==1:return {**payload([raw(seq=i) for i in range(20)]),'has_next_page':True,'next_cursor':'next'},200
    hold.wait(3);return payload([]),200
   return client.fetch_account(h,k,opener=opener,**kw)
  run=collector.collect(api_key='offline',targets=[{'handle':'Audit'}],run_id='r',fetch=fetch,base_time=NOW,deadline_seconds=.05,log=lambda*a:None)
  self.assertEqual(run['api_calls'],2);self.assertEqual(run['accounts']['audit']['status'],'deadline_inflight');before=json.dumps(run);hold.set();time.sleep(.03);self.assertEqual(json.dumps(run),before);print('M9 deadline page2: calls=2 status=deadline_inflight; late result cannot mutate run')
 def test_m10_beijing_retention_directory_symlink(self):
  outside=self.repo/'outside';outside.mkdir();f=outside/'20260901-000000-1234abcd.json';f.touch();snaps=store.snapshots_dir(self.root);snaps.mkdir(parents=True);(snaps/'2026-09-01').symlink_to(outside,target_is_directory=True)
  paths=[]
  for day in ('2026-09-15','2026-09-16'):
   p=snaps/day/'20260915-000000-1234abcd.json';p.parent.mkdir();p.touch();paths.append(p)
  self.assertEqual(store.snapshot_path(self.root,'20261015-160000-1234abcd','2026-10-15T16:00:00Z').parent.name,'2026-10-16');store.prune_snapshots(self.root,datetime(2026,10,15,16,tzinfo=timezone.utc));self.assertTrue(f.exists());self.assertFalse(paths[0].exists());self.assertTrue(paths[1].exists());print('M10 Beijing 10/16 cutoff=9/16: 9/15 deleted, 9/16 kept, outside preserved')
 def test_m2_author_ownership_non_retweet_unknown(self):
  rows=[raw('Audit',1),raw('Other',2,author={'userName':'Other','followers':41000}),raw('Audit',3,text='RT @x: Agent',author={'userName':'Audit','followers':90000})]
  def fetch(h,k,**kw):return {'status':'ok','tweets':rows,'fetched_at':NOW,'calls':1,'pages':1}
  run=collector.collect(api_key='offline',targets=[{'handle':'Audit'}],run_id='r',fetch=fetch,base_time=NOW,log=lambda *a:None);self.assertEqual(run['accounts']['audit']['followers'],500)
  records=[common.normalize_tweet(x,'Audit',NOW) for x in rows[1:]];self.assertIsNone(common.pick_live_followers(records)[0]);unknown=common.normalize_tweet(raw(author={'followers':123}), 'Audit', NOW);self.assertIsNone(common.pick_live_followers([unknown])[0]);print('M2 Audit=500; Other/RT/missing-author => unknown')
 def test_l2_finished_time_separate_generation_utc(self):
  run=self.collect(fetch=lambda h,k,**kw:{'status':'empty','tweets':[],'calls':1,'pages':1,'fetched_at':NOW});run['finished_at']='2026-10-15T05:50:45.505Z';b=builder.build_bundle(run,None,targets(),now=datetime(2026,10,15,6,47,28,tzinfo=timezone.utc));self.assertEqual(b['updated_at'],'2026-10-15 13:50:45');self.assertEqual(b['collected_at'],run['finished_at']);self.assertEqual(b['generated_at'],'2026-10-15T06:47:28.000Z');print('L2 updated_at=13:50:45 collected_at=05:50:45.505Z generated_at=06:47:28.000Z')
 def test_type_evidence_minimal_flags_ids_only(self):
  item=raw(isQuote=True,quoted_tweet={'id':'1234','text':'private body','token':'private token'},in_reply_to_status_id='4567');r=common.normalize_tweet(item,'Audit',NOW);e=r['type_evidence'];self.assertTrue(e['isQuote']);self.assertEqual(e['quoted_tweet_id'],'1234');self.assertEqual(e['in_reply_to_status_id'],'4567');self.assertNotIn('private',json.dumps(e));print('TYPE evidence: isQuote=true quoted_tweet_id=1234 reply_id=4567; no raw text/token')
 def test_credential_transport_rejects_redirect_and_other_origins(self):
  self.assertIsNone(client._NoRedirect().redirect_request(None,None,302,'',{},'https://third.example'))
  with self.assertRaises(ValueError):client.urllib_opener('https://third.example/path','offline',.1)
  print('TRANSPORT: redirect denied, foreign origin rejected before request')
 def test_maximum_two_pages_and_zero_budget_never_success(self):
  calls=[]
  def opener(*args):calls.append(1);return {**payload([raw(seq=i) for i in range(20)]),'has_next_page':True,'next_cursor':'next'},200
  client.fetch_account('Audit','offline',opener=opener,base_time=NOW,max_pages=99)
  self.assertEqual(len(calls),2)
  result=client.fetch_account('Audit','offline',opener=opener,base_time=NOW,timeout=0)
  self.assertNotIn(result['status'],common.SUCCESS_STATUSES);self.assertEqual(len(calls),2)
 def test_h1a_all_51_false_keeps_public_and_index_bytes(self):
  self.seed();before_pub=store.public_bundle_path(self.repo).read_bytes();before_index=store.manifest_path(self.root).read_bytes();original=collector.collect
  def fetch(h,k,**kw):return client.fetch_account(h,k,opener=lambda *a:({'success':False,'code':0,'data':{'tweets':[]}},200),**kw)
  def collect(**kw):return original(fetch=fetch,**kw)
  summary=self.repo/'summary.json'
  with patch.dict(os.environ,{'AISA_API_KEY':'offline'}),patch.object(entry,'collect',collect),contextlib.redirect_stdout(io.StringIO()):
   code=entry.main(['--repo-root',str(self.repo),'--targets',str(ROOT/'scripts/radar_targets.json'),'--summary-json',str(summary)])
  result=json.loads(summary.read_text());self.assertEqual(code,1);self.assertEqual(result['success'],0);self.assertEqual(result['failed'],51);self.assertFalse(result['published']);self.assertEqual(result['api_calls'],51);self.assertEqual(store.public_bundle_path(self.repo).read_bytes(),before_pub);self.assertEqual(store.manifest_path(self.root).read_bytes(),before_index)
  print('H1-a full pipeline: success=0 failed=51 api_calls=51 exit=1 public/index bytes unchanged')
if __name__=='__main__':unittest.main(verbosity=2)
