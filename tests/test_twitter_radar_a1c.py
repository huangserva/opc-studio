"""A1-c independent network fault, deadline, receipt and process-death regressions."""
import errno, functools, json, os, signal, socket, ssl, subprocess, sys, tempfile, threading, time, unittest
import urllib.error
from collections import Counter
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch
from test_twitter_radar_a1b import ROOT, NOW, raw, payload
import radar_client as client, radar_collect as collector, radar_store as store, radar_common as common
RID='20261015-060000-abcdef12'
KEY='offline-credential-0123456789abcdef'
class A1cTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory(prefix='radar-a1c-case-',dir='/tmp');self.addCleanup(self.tmp.cleanup);self.repo=Path(self.tmp.name);self.root=store.workspace_root(self.repo)
 def run_collect(self,op,handles=('Audit',),**kw):
  fetch=functools.partial(client.fetch_account,opener=op,sleep=lambda s:None,jitter=lambda:0)
  return collector.collect(api_key=KEY,targets=[{'handle':h} for h in handles],run_id=RID,base_time=NOW,fetch=fetch,receipt_dir=self.repo/'receipt',log=lambda *a:None,**kw)
 def receipt(self):return json.loads((self.repo/'receipt/receipt.json').read_text())
 def sequence(self,items):
  self.calls=[]
  def op(url,key,timeout):
   self.calls.append((url,timeout));item=items.pop(0)
   if isinstance(item,Exception):raise item
   return item,200
  return op
 def test_timeout_recovers_coverage_and_rows(self):
  run=self.run_collect(self.sequence([TimeoutError('read timeout'),payload([])]));r=self.receipt()
  self.assertTrue(run['complete']);self.assertEqual((run['coverage'],run['api_calls'],r['retried'],r['recovered'],len(r['requests'])),(1,2,1,1,2));self.assertEqual([x['result'] for x in r['requests']],['timeout','ok'])
  print('A1c timeout->ok: coverage=1 calls=rows=2 retried=1 recovered=1')
 def test_timeout_twice_stops(self):
  run=self.run_collect(self.sequence([TimeoutError(),TimeoutError(),payload([])]));r=self.receipt()
  self.assertEqual((run['api_calls'],r['retried'],r['recovered'],len(self.calls)),(2,1,0,2));self.assertFalse(run['complete'])
  print('A1c timeout->timeout: calls=2 recovered=0')
 def test_transient_allowlist(self):
  errors=[TimeoutError(),ConnectionResetError(errno.ECONNRESET,'reset'),ConnectionRefusedError(errno.ECONNREFUSED,'refused'),urllib.error.URLError(socket.gaierror(socket.EAI_AGAIN,'temporary'))]
  errors += [urllib.error.HTTPError('https://api.aisa.one',code,'x',None,None) for code in (502,503,504)]
  for err in errors:
   with self.subTest(error=type(err).__name__,code=getattr(err,'code',None)):
    run=self.run_collect(self.sequence([err,payload([])]));self.assertEqual(run['api_calls'],2);self.assertEqual(run['success'],1)
  print('A1c transient allowlist: 7/7 recover')
 def test_permanent_failures_no_retry(self):
  errors=[urllib.error.HTTPError('https://api.aisa.one',c,'x',None,None) for c in (400,401,403,404,429,500,501)]
  errors += [urllib.error.URLError(socket.gaierror(socket.EAI_NONAME,'permanent')),ssl.SSLError('certificate'),json.JSONDecodeError('bad','x',0),{'success':False,'data':{'tweets':[]}}, {'data':{}}, []]
  for err in errors:
   with self.subTest(error=type(err).__name__,code=getattr(err,'code',None)):
    run=self.run_collect(self.sequence([err,payload([])]));self.assertEqual((run['api_calls'],len(self.calls),self.receipt()['retried']),(1,1,0));self.assertEqual(run['success'],0)
  print('A1c permanent/shape failures: 13/13 calls=1 retried=0')
 def first_page(self):return {**payload([raw(seq=i+1) for i in range(20)]),'has_next_page':True,'next_cursor':'next'}
 def test_page2_retry_does_not_refetch_page1(self):
  run=self.run_collect(self.sequence([self.first_page(),TimeoutError(),payload([raw(seq=99)])]));r=self.receipt()
  self.assertEqual((run['api_calls'],run['pages_fetched'],len(run['tweets'])),(3,2,21));self.assertEqual([x['page'] for x in r['requests']],[1,2,2]);self.assertNotIn('cursor=',self.calls[0][0]);self.assertTrue(all('cursor=next' in x[0] for x in self.calls[1:]));print('A1c page2 only retry: pages=[1,2,2] calls=3')
 def test_shared_one_retry_per_account_across_pages(self):
  run=self.run_collect(self.sequence([TimeoutError(),self.first_page(),TimeoutError(),payload([])]));r=self.receipt()
  self.assertEqual((run['api_calls'],run['partial'],r['retried'],len(self.calls)),(3,1,1,3));self.assertEqual([x['page'] for x in r['requests']],[1,1,2]);print('A1c shared retry quota: pages=[1,1,2] calls=3 partial=1')
 def test_budget_insufficient_no_retry(self):
  run=self.run_collect(self.sequence([TimeoutError(),payload([])]),deadline_seconds=.1,request_timeout=20)
  self.assertEqual((run['api_calls'],len(self.receipt()['requests'])),(1,1));print('A1c remaining .1s < 20s: calls=rows=1 retry=0')
 def test_budget_rechecked_after_backoff(self):
  now=[0.];calls=[];delays=[]
  def op(*a):calls.append(a);raise TimeoutError()
  def sleep(s):delays.append(s);now[0]=100
  r=client.fetch_account('Audit',KEY,opener=op,clock=lambda:now[0],deadline=30,sleep=sleep,jitter=lambda:1)
  self.assertEqual(r['calls'],1);self.assertEqual(delays,[.2]);print('A1c backoff recheck: jitter=.2s calls=1')
 def test_proxy_discovery_consumes_retry_budget(self):
  now=[0.];n=[0]
  def proxy():
   n[0]+=1
   if n[0]==2:now[0]=15
   return False
  with patch.object(client,'proxy_used',proxy):
   r=client.fetch_account('Audit',KEY,opener=self.sequence([TimeoutError(),payload([])]),clock=lambda:now[0],deadline=30,sleep=lambda s:None,jitter=lambda:0)
  self.assertEqual((r['calls'],len(self.calls)),(1,1));self.assertEqual(r['status'],'timeout');print('A1c proxy discovery budget: retry abandoned calls=1')
 def test_concurrent_retry_accounting(self):
  counts=Counter();lock=threading.Lock();gate=threading.Barrier(8)
  def op(url,*a):
   from urllib.parse import parse_qs,urlsplit
   h=parse_qs(urlsplit(url).query)['userName'][0]
   with lock:counts[h]+=1;n=counts[h]
   if n==1:gate.wait(timeout=2);raise TimeoutError()
   return payload([]),200
  run=self.run_collect(op,handles=tuple('U'+str(i) for i in range(8)));r=self.receipt()
  self.assertEqual((run['api_calls'],len(r['requests']),r['retried'],r['recovered']),(16,16,8,8));self.assertTrue(run['complete']);print('A1c concurrent8: api_calls=rows=16 retried=recovered=8')
 def test_deadline_late_retry_frozen(self):
  entered=threading.Event();release=threading.Event();n=[0]
  def op(*a):
   n[0]+=1
   if n[0]==1:raise TimeoutError()
   entered.set();release.wait(2);return payload([]),200
  run=self.run_collect(op,deadline_seconds=.08,request_timeout=.01);self.assertTrue(entered.is_set());self.assertEqual((run['api_calls'],len(self.receipt()['requests'])),(2,2));self.assertEqual(run['accounts']['audit']['status'],'deadline_inflight')
  before=json.dumps(run,sort_keys=True);disk=(self.repo/'receipt/receipt.json').read_bytes();release.set();time.sleep(.08)
  self.assertEqual(before,json.dumps(run,sort_keys=True));self.assertEqual(disk,(self.repo/'receipt/receipt.json').read_bytes());self.assertTrue(self.receipt()['requests'][-1]['deadline_inflight']);print('A1c late retry: calls=2 run+receipt frozen')
 def test_before_request_deadline_no_phantom(self):
  release=threading.Event()
  def proxy():release.wait(.3);return False
  with patch.object(client,'proxy_used',proxy):
   run=self.run_collect(lambda *a:(payload([]),200),deadline_seconds=.03)
   self.assertEqual((run['api_calls'],len(self.receipt()['requests'])),(0,0));self.assertEqual(run['accounts']['audit']['status'],'not_started');release.set();time.sleep(.02)
  print('A1c pre-request deadline: calls=rows=0 not_started')
 def test_credentials_never_in_receipt_or_errors(self):
  proxy='https://alice:private-password@proxy.invalid:1234';msg=f'Authorization: Bearer {KEY}; {proxy} {KEY[:12]} {KEY[-8:]}'
  with patch.dict(os.environ,{'https_proxy':proxy,'no_proxy':''}):
   run=self.run_collect(self.sequence([ConnectionResetError(msg),payload([])]))
  text=json.dumps(run)+json.dumps(self.receipt());self.assertTrue(all(x['proxy_used'] is True for x in self.receipt()['requests']))
  for secret in (KEY,KEY[:12],KEY[-8:],'private-password','alice:',proxy,'Bearer '):self.assertNotIn(secret,text)
  self.assertNotIn('Authorization',json.dumps(self.receipt()));print('A1c credentials scan: 0 hits proxy_used=true')
 def test_receipt_separate_and_context_path(self):
  with store.SingleInstanceLock(store.lock_path(self.root)):
   collector.collect(api_key=KEY,targets=[{'handle':'Audit'}],run_id=RID,base_time=NOW,fetch=functools.partial(client.fetch_account,opener=lambda *a:(payload([]),200)),log=lambda *a:None)
  p=self.root/'runs/2026-10-15'/RID/'receipt.json';self.assertEqual(json.loads(p.read_text())['api_calls'],1);self.assertIsNone(store._ACTIVE_RECEIPT_ROOT.get());print('A1c context: runs/2026-10-15/run_id/receipt.json')
 def test_retention_and_symlink_ownership(self):
  old=self.root/'runs/2026-09-14'/RID;old.mkdir(parents=True);(old/'receipt.json').write_text('{}');(old/'.receipt.json.12345678.tmp').write_text('x');(old/'foreign.txt').write_text('keep')
  kept=self.root/'runs/2026-09-15'/RID;kept.mkdir(parents=True);(kept/'receipt.json').write_text('{}')
  outside=self.repo/'outside';outside.mkdir();(outside/'receipt.json').write_text('protected');(self.root/'runs/2026-09-13').symlink_to(outside,target_is_directory=True)
  store.prune_request_receipts(self.root,now=NOW);self.assertFalse((old/'receipt.json').exists());self.assertFalse(list(old.glob('*.tmp')));self.assertTrue((old/'foreign.txt').exists());self.assertTrue((kept/'receipt.json').exists());self.assertEqual((outside/'receipt.json').read_text(),'protected');print('A1c retention: 30-day boundary kept, old owned removed, foreign+symlink protected')
 def test_receipts_follow_snapshot_custom_retention(self):
  old=self.root/'runs/2026-09-01'/RID;old.mkdir(parents=True);receipt=old/'receipt.json';receipt.write_text('{}')
  with store.SingleInstanceLock(store.lock_path(self.root)):self.assertTrue(receipt.exists())
  store.prune_snapshots(self.root,NOW,keep_days=60);self.assertTrue(receipt.exists())
  store.prune_snapshots(self.root,NOW,keep_days=7);self.assertFalse(receipt.exists());print('A1c custom retention: startup does not age-delete; keep_days=60 kept, 7 removed')
 def test_slow_old_writer_cannot_overwrite_final(self):
  original=store.RequestReceipt._persist;gate=threading.Event();release=threading.Event()
  def slow(obj,doc,rev):
   if not doc['closed']:gate.set();release.wait(.4)
   return original(obj,doc,rev)
  with patch.object(store.RequestReceipt,'_persist',slow):
   def op(*a):
    self.assertTrue(gate.wait(.2));return payload([]),200
   start=time.monotonic();run=self.run_collect(op);self.assertLess(time.monotonic()-start,.3);before=(self.repo/'receipt/receipt.json').read_bytes();release.set();time.sleep(.05);self.assertEqual(before,(self.repo/'receipt/receipt.json').read_bytes())
  print('A1c slow asynchronous writer: final receipt authoritative')
 def test_sigkill_retry_next_round_recovers(self):
  store.write_json_atomic(store.snapshot_path(self.root,RID,common.iso_utc(NOW)),{'run_id':RID,'tweets':[]});store.publish_bundle(self.root,self.repo,RID,{'schema_version':common.SCHEMA_VERSION,'tweets':[]},started_at=common.iso_utc(NOW),coverage={})
  before=store.public_bundle_path(self.repo).read_bytes();store.write_json_atomic(store.manifest_path(self.root),{'run_id':'stale'})
  code='''import sys,time,functools,os
from pathlib import Path
sys.path.insert(0,sys.argv[1])
import radar_store as s,radar_client as c,radar_collect as co
from datetime import datetime,timezone
repo=Path(sys.argv[2]);root=s.workspace_root(repo);n=0
def op(*a):
 global n
 n+=1
 if n==1:raise TimeoutError()
 run_dir=root/'runs/2026-10-15/20261015-060001-aabbccdd';run_dir.mkdir(parents=True,exist_ok=True)
 (run_dir/'.receipt.json.12345678.tmp').write_text('killed temporary')
 (repo/'gate').touch()
 while True:time.sleep(1)
with s.SingleInstanceLock(s.lock_path(root)):
 co.collect(api_key='offline',targets=[{'handle':'Audit'}],run_id='20261015-060001-aabbccdd',base_time=datetime(2026,10,15,6,tzinfo=timezone.utc),fetch=functools.partial(c.fetch_account,opener=op),log=lambda *a:None)
'''
  p=subprocess.Popen([sys.executable,'-c',code,str(ROOT/'scripts'),str(self.repo)],stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
  try:
   end=time.monotonic()+3
   while not (self.repo/'gate').exists() and time.monotonic()<end:time.sleep(.01)
   self.assertTrue((self.repo/'gate').exists());p.kill();p.wait(3);self.assertEqual(p.returncode,-signal.SIGKILL)
  finally:
   if p.poll() is None:p.kill();p.wait()
   p.stderr.close()
  self.assertEqual(before,store.public_bundle_path(self.repo).read_bytes())
  recovery='''import sys,os,functools
sys.path.insert(0,sys.argv[1])
import refresh_twitter_radar as e,radar_collect as co,radar_client as c
os.environ['AISA_API_KEY']='offline';real=co.collect
e.collect=lambda **kw:real(fetch=functools.partial(c.fetch_account,opener=lambda *a:({'code':0,'data':{'tweets':[]}},200)),**kw)
raise SystemExit(e.main(['--repo-root',sys.argv[2],'--targets',sys.argv[3],'--no-publish']))
'''
  r=subprocess.run([sys.executable,'-c',recovery,str(ROOT/'scripts'),str(self.repo),str(ROOT/'scripts/radar_targets.json')],capture_output=True,timeout=5)
  self.assertEqual(r.returncode,0,r.stdout);self.assertFalse(list(self.repo.rglob('*.tmp')));self.assertEqual(store.read_manifest(self.root),store.public_manifest(self.root,self.repo));self.assertEqual(store.load_previous_snapshot(self.root,repo_root=self.repo)['run_id'],RID);self.assertEqual(before,store.public_bundle_path(self.repo).read_bytes());print('A1c retry SIGKILL=-9: next full round exit=0 tmp=0 manifest repaired public unchanged')
if __name__=='__main__':unittest.main()
