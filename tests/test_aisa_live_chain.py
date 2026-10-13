"""AIsa manual API→normal Clues persistence, using gateway-shaped offline responses."""
import copy
import json
import socket
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from types import SimpleNamespace
from pathlib import Path
from urllib.parse import urlsplit,parse_qs
from unittest.mock import patch
from aisa_source import AIsaSource,urllib_transport
from collect_adapters import AIsaRadarAdapter
from server import Store

SECRET='synthetic-aisa-live-canary'
TWITTER={'code':0,'status':'success','data':{'tweets':[{'id':'1234567890123456789','text':'合成推特新技术','url':'https://x.com/test_user/status/1234567890123456789','viewCount':100,'likeCount':3,'retweetCount':2,'replyCount':1,'createdAt':'Mon Oct 12 18:30:00 +0000 2026'}]},'has_next_page':True,'next_cursor':'opaque'}
DOUYIN={'code':200,'data':{'status_code':0,'data':{'active_time':'','share_info':{'share_url':'https://www.douyin.com/hot'},'word_list':[{'sentence_id':'zero','word':'合成置顶','hot_value':0},{'sentence_id':'normal','word':'合成热词','hot_value':100000,'position':1}]}}}
XHS={'code':200,'data':{'success':True,'data':{'items':[{'id':12345,'hot_id':'888','title':'合成小红书灵感','score':456700,'score_text':'45.7万人在看','deeplink':'xhsdiscover://post_new_note?source=creator_center'}],'cursor':'1','end_flag':False}}}
class FakeTransport:
 def __init__(self):self.calls=[];self.gate=None;self.entered=threading.Event();self.fail=None;self.responses={'twitter':TWITTER,'douyin':DOUYIN,'xiaohongshu':XHS}
 def __call__(self,method,url,headers,body,timeout):
  self.calls.append((method,url,body));self.entered.set()
  if self.gate:self.gate.wait(3)
  if self.fail:return self.fail
  platform='twitter' if '/twitter/' in url else 'douyin' if '/douyin/' in url else 'xiaohongshu'
  return 200,json.dumps(self.responses[platform],ensure_ascii=False)
class AIsaFixture(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory(dir='/tmp');self.addCleanup(self.tmp.cleanup)
  self.store=Store(self.tmp.name);self.transport=FakeTransport();self.source=AIsaSource(store=self.store,transport=self.transport,api_key=SECRET)
  self.adapter=AIsaRadarAdapter(self.source)
 def config(self,platform='twitter',identity='a'*32,**kw):return {'request_id':identity,'platform':platform,'handles':['test_user'] if platform=='twitter' else [],'limit':5,**kw}
 def done(self,result):
  until=time.monotonic()+5
  while time.monotonic()<until:
   job=self.adapter.job(result['job_id'])
   if job['status'] not in ('queued','running'):return job
   time.sleep(.01)
  self.fail('manual job did not finish')

class LiveChainTests(AIsaFixture):
 def test_twitter_real_route_and_camelcase(self):
  item=self.source.get_twitter_tweets('test_user',5)[0]
  method,url,body=self.transport.calls[0]
  self.assertEqual(method,'GET');self.assertEqual(urlsplit(url).path,'/apis/v1/twitter/user/last_tweets')
  self.assertEqual(parse_qs(urlsplit(url).query),{'userName':['test_user']});self.assertIsNone(body)
  self.assertEqual((item['views'],item['likes'],item['retweets'],item['replies']),(100,3,2,1))
  self.assertEqual(item['created_at'],'2026-10-12T18:30:00+00:00')
  self.assertEqual(item['url'],TWITTER['data']['tweets'][0]['url'])
 def test_douyin_real_route_ids_and_special_rank(self):
  items=self.source.fetch_douyin_hot_searches()
  self.assertEqual(urlsplit(self.transport.calls[0][1]).path,'/apis/v1/tikhub/douyin/app/v3/fetch_hot_search_list')
  self.assertEqual(parse_qs(urlsplit(self.transport.calls[0][1]).query),{'board_type':['0']})
  top=next(x for x in items if x['sentence_id']=='zero');self.assertIsNone(top['rank'])
  self.assertEqual(self.source._item_id(top,'douyin'),'zero')
 def test_score_deeplink_and_no_invented_note_url(self):
  item=self.source.fetch_xiaohongshu_inspirations()[0]
  self.assertEqual(item['heat_score'],456700);self.assertEqual(item['score_text'],'45.7万人在看')
  self.assertEqual(item['url'],'');self.assertEqual(item['link_label'],'仅 App 创作入口，无公开原文链接')
  self.assertEqual(item['deeplink'],XHS['data']['data']['items'][0]['deeplink'])
 def test_all_platforms_persist_filter_and_time(self):
  total=0
  for index,platform in enumerate(['twitter','douyin','xiaohongshu']):
   job=self.done(self.adapter.start(self.config(platform,str(index)*32)))
   self.assertEqual(job['status'],'done');total+=job['added']
  listing=self.store.clues.listing(source='aisa');self.assertEqual(listing['total'],total)
  item=next(x for x in listing['items'] if '推特' in x['title'])
  self.assertEqual(item['source_time'],'2026-10-12T18:30:00+00:00')
  evidence=self.store.clues.detail(item['id'])['entries'][0]['evidence'][0]
  self.assertEqual(evidence['source'],'aisa');self.assertEqual(evidence['provenance']['platform'],'twitter')
  self.assertEqual(evidence['origin_url'],TWITTER['data']['tweets'][0]['url'])
  timeline=self.store.clues.listing(source='aisa',view='year')['timeline']
  self.assertEqual(next(x for x in timeline if x['thread_id']==item['id'])['month'],'10')
 def test_duplicate_updates_metrics_preserves_manual_classification(self):
  first=self.done(self.adapter.start(self.config('xiaohongshu')))
  clue=self.store.clues.listing(source='aisa')['items'][0]
  self.store.clues.save({'id':clue['id'],'revision':clue['revision'],'categories':['technology'],'status':'verified','heat':'peak','reason':'人工核对'})
  self.transport.responses=copy.deepcopy(self.transport.responses)
  self.transport.responses['xiaohongshu']['data']['data']['items'][0].update(score=987600,score_text='98.8万人在看')
  second=self.done(self.adapter.start(self.config('xiaohongshu','b'*32)))
  self.assertEqual((second['added'],second['duplicates'],second['updated']),(0,1,1))
  current=self.store.clues.listing(source='aisa')['items'][0]
  self.assertEqual(self.store.clues.listing(source='aisa')['total'],1)
  self.assertIn('987600',current['title']);self.assertEqual(current['categories'],['technology']);self.assertEqual(current['status'],'verified');self.assertEqual(current['heat'],'peak');self.assertEqual(current['tags_source'],'suggested')
  self.assertEqual(first['added'],1)
 def test_same_request_replay_and_global_singleflight(self):
  self.transport.gate=threading.Event()
  one=self.adapter.start(self.config());self.assertTrue(self.transport.entered.wait(1))
  two=self.adapter.start(self.config('douyin','b'*32))
  second_instance=AIsaRadarAdapter(AIsaSource(store=Store(self.tmp.name),transport=self.transport,api_key=SECRET))
  three=second_instance.start(self.config('xiaohongshu','c'*32))
  self.assertEqual({one['job_id'],two['job_id'],three['job_id']},{'a'*32});self.assertTrue(two['reused']);self.assertTrue(three['reused'])
  self.transport.gate.set();self.done(one)
  replay=self.adapter.start(self.config());self.assertTrue(replay['reused']);self.assertEqual(len(self.transport.calls),1)
  with self.assertRaises(ValueError):self.adapter.start(self.config('douyin'))
 def test_failure_and_no_key_leak(self):
  self.transport.fail=(401,json.dumps({'message':SECRET}))
  result=self.done(self.adapter.start(self.config()))
  self.assertEqual(result['status'],'error');self.assertEqual(result['added'],0);self.assertIn('鉴权',result['errors'][0])
  for p in Path(self.tmp.name).rglob('*'):
   if p.is_file():self.assertNotIn(SECRET.encode(),p.read_bytes(),str(p))
  self.assertNotIn(SECRET,json.dumps(result))
 def test_malformed_missing_list_and_business_failure(self):
  for obj in [{'code':200,'data':{'category_list':[]}},{'code':200,'data':{'success':False,'msg':SECRET}}]:
   self.transport.fail=(200,json.dumps(obj))
   self.assertEqual(self.source.fetch_xiaohongshu_inspirations(),[]);self.assertTrue(self.source.last_error)
   self.assertNotIn(SECRET,self.source.last_error)
 def test_limits_and_unknown_fields_rejected_before_network(self):
  for kw in [{'limit':True},{'limit':0},{'limit':101},{'platform':'bad'},{'handles':['bad/link']},{'handles':['abc']*6},{'secret':SECRET},{'request_id':'bad'}]:
   with self.subTest(kw=list(kw)):
    with self.assertRaises(ValueError):self.adapter.start(self.config(**kw))
  self.assertEqual(self.transport.calls,[])
 def test_thread_start_failure_releases_claim_and_no_repeat(self):
  with patch('collect_adapters.threading.Thread',side_effect=RuntimeError(SECRET)):
   with self.assertRaises(ValueError):self.adapter.start(self.config())
  self.assertEqual(self.adapter.job('a'*32)['status'],'error')
  replay=self.adapter.start(self.config());self.assertTrue(replay['reused']);self.assertEqual(self.transport.calls,[])
  self.assertEqual(self.done(self.adapter.start(self.config(identity='b'*32)))['status'],'done')
 def test_socket_cap_30_without_expanding_deadline(self):
  import aisa_source as mod
  self.assertEqual(mod._effective_timeout(300),30.0)
  self.assertEqual(mod.MAX_TOTAL_SECONDS,30.0)
  self.assertEqual(mod._effective_timeout(.5),.5)

class HttpChainTests(unittest.TestCase):
 def setUp(self):
  from http.server import ThreadingHTTPServer
  from server import Handler
  from collect_core import Registry
  self.tmp=tempfile.TemporaryDirectory(dir='/tmp');self.addCleanup(self.tmp.cleanup)
  self.store=Store(self.tmp.name);self.transport=FakeTransport()
  self.adapter=AIsaRadarAdapter(AIsaSource(store=self.store,transport=self.transport,api_key=SECRET))
  self.store._collect=Registry(self.store,{'aisa':self.adapter})
  class QuietHandler(Handler):
   def log_message(self,*a):pass
  self.http=ThreadingHTTPServer(('127.0.0.1',0),QuietHandler);self.http.store=self.store
  self.thread=threading.Thread(target=self.http.serve_forever,daemon=True);self.thread.start()
  self.addCleanup(self.close)
 def close(self):self.http.shutdown();self.http.server_close();self.thread.join(2)
 def request(self,path,body=None,headers=None):
  import http.client
  c=http.client.HTTPConnection('127.0.0.1',self.http.server_port,timeout=5)
  base={'Content-Type':'application/json','Origin':f'http://127.0.0.1:{self.http.server_port}','X-Article-Studio':'1'}
  if headers:base.update(headers)
  c.request('GET' if body is None else 'POST',path,None if body is None else json.dumps(body),base)
  r=c.getresponse();out=(r.status,json.loads(r.read()));c.close();return out
 def test_post_source_gate_before_any_charge(self):
  for h in [{'Origin':'https://example.invalid'},{'X-Article-Studio':''},{'Host':'example.invalid'}]:
   status,_=self.request('/api/collect/aisa/run',LiveChainTests.config(self),h)
   self.assertEqual(status,403)
  self.assertEqual(self.transport.calls,[])
 def test_post_poll_filter_detail_end_to_end(self):
  status,job=self.request('/api/collect/aisa/run',LiveChainTests.config(self))
  self.assertEqual(status,202)
  until=time.monotonic()+3
  while time.monotonic()<until:
   status,result=self.request('/api/collect/aisa/job?id='+job['job_id'])
   if result['status']=='done':break
   time.sleep(.01)
  self.assertEqual(status,200);self.assertEqual(result['added'],1)
  status,listing=self.request('/api/clues?source=aisa');self.assertEqual(listing['total'],1)
  status,detail=self.request('/api/clues/'+listing['items'][0]['id'])
  self.assertEqual(status,200);self.assertEqual(detail['entries'][0]['clock'],'2026-10-12T18:30:00+00:00')
  self.assertNotIn(SECRET,json.dumps(detail))
 def test_invalid_payload_rejected_safely(self):
  status,result=self.request('/api/collect/aisa/run',{'platform':'twitter','handles':[SECRET],'limit':5,'request_id':'a'*32})
  self.assertEqual(status,400);self.assertNotIn(SECRET,json.dumps(result));self.assertEqual(self.transport.calls,[])

class RecoveryAndSafetyTests(AIsaFixture):
 # 仅新增真正的恢复/保护场景，避免继承重复执行主测试。
 def test_orphan_receipt_not_auto_charged_and_new_intent_can_run(self):
  first=self.done(self.adapter.start(self.config()))
  first.update(status='running')
  self.adapter._write_job(first)
  recovered=self.adapter.job(first['job_id'])
  self.assertEqual(recovered['status'],'interrupted')
  self.assertTrue(self.adapter.start(self.config())['reused'])
  self.assertEqual(len(self.transport.calls),1)
  self.assertEqual(self.done(self.adapter.start(self.config(identity='b'*32)))['status'],'done')
 def test_unchanged_duplicate_does_not_add_revision(self):
  self.done(self.adapter.start(self.config('xiaohongshu')))
  before=self.store.clues.listing(source='aisa')['items'][0]
  job=self.done(self.adapter.start(self.config('xiaohongshu','b'*32)))
  after=self.store.clues.listing(source='aisa')['items'][0]
  self.assertEqual((job['duplicates'],job['updated']),(1,0));self.assertEqual(after['revision'],before['revision'])
 def test_rate_limit_stops_following_handles(self):
  self.transport.fail=(429,'{}')
  result=self.done(self.adapter.start(self.config(handles=['one','two'])))
  self.assertEqual(result['status'],'error');self.assertEqual(result['completed'],1)
  self.assertEqual(len(self.transport.calls),1)
 def test_source_urls_reject_credentials_and_other_domains(self):
  self.transport.responses=copy.deepcopy(self.transport.responses)
  for url in ['javascript:alert(1)','https://evil.example/post','https://user:pass@x.com/x/status/1']:
   self.transport.responses['twitter']['data']['tweets'][0]['url']=url
   self.assertEqual(self.source.get_twitter_tweets('test_user')[0]['url'],'')
 def test_snapshot_scrub_covers_metrics_links_and_excerpt(self):
  self.transport.responses=copy.deepcopy(self.transport.responses)
  self.transport.responses['xiaohongshu']['data']['data']['items'][0].update(title=SECRET,score_text=SECRET,deeplink=SECRET)
  result=self.done(self.adapter.start(self.config('xiaohongshu')))
  self.assertEqual(result['added'],1)
  for p in Path(self.tmp.name).rglob('*'):
   if p.is_file():self.assertNotIn(SECRET.encode(),p.read_bytes())

 def test_manual_title_summary_and_tags_survive_metric_refresh(self):
  self.done(self.adapter.start(self.config('xiaohongshu')))
  before=self.store.clues.listing(source='aisa')['items'][0]
  self.store.clues.save({'id':before['id'],'revision':before['revision'],'title':'人工标题','summary':'人工摘要','categories':['technology'],'tags':['memory'],'reason':'人工整理'})
  self.transport.responses=copy.deepcopy(self.transport.responses)
  self.transport.responses['xiaohongshu']['data']['data']['items'][0]['score']=777
  job=self.done(self.adapter.start(self.config('xiaohongshu','b'*32)))
  current=self.store.clues.detail(before['id'])
  self.assertEqual(job['updated'],1);self.assertEqual(current['title'],'人工标题');self.assertEqual(current['summary'],'人工摘要')
  self.assertEqual(current['tags'],['memory']);self.assertEqual(current['entries'][0]['evidence'][0]['provenance']['metrics']['heat_score'],777)
 def test_nonempty_unparseable_rows_are_failure(self):
  self.transport.fail=(200,json.dumps({'data':{'items':[{}]}}))
  result=self.done(self.adapter.start(self.config('xiaohongshu')))
  self.assertEqual(result['status'],'error');self.assertEqual(result['added'],0);self.assertIn('格式',result['errors'][0])

 def test_second_process_reuses_running_work_without_transport(self):
  import subprocess,os
  self.transport.gate=threading.Event()
  job=self.adapter.start(self.config());self.assertTrue(self.transport.entered.wait(1))
  script="""import sys,json
from server import Store
from aisa_source import AIsaSource
from collect_adapters import AIsaRadarAdapter
def no_network(*a):raise AssertionError('child must not call transport')
source=AIsaSource(store=Store(sys.argv[1]),transport=no_network,api_key='child-test')
result=AIsaRadarAdapter(source).start({'request_id':'d'*32,'platform':'douyin','handles':[],'limit':5})
assert result['reused'] and result['job_id']=='a'*32
print('PROCESS_REUSE_PASS')
"""
  env=os.environ.copy();env['AISA_API_KEY']=''
  result=subprocess.run([os.sys.executable,'-c',script,self.tmp.name],env=env,capture_output=True,text=True,timeout=5)
  self.transport.gate.set();self.done(job)
  self.assertEqual(result.returncode,0,result.stderr);self.assertIn('PROCESS_REUSE_PASS',result.stdout)
  self.assertEqual(len(self.transport.calls),1)

 def test_partial_save_reports_fetched_and_success_separately(self):
  actual=self.store.clues.ingest_aisa
  def save(source,item):
   if item['sentence_id']=='zero':raise RuntimeError(SECRET)
   return actual(source,item)
  with patch.object(self.store.clues,'ingest_aisa',side_effect=save):
   result=self.done(self.adapter.start(self.config('douyin')))
  self.assertEqual((result['fetched'],result['added'],result['failed']),(2,1,1));self.assertEqual(result['status'],'partial')
  self.assertNotIn(SECRET,json.dumps(result))
 def test_all_save_failures_are_error_not_partial(self):
  with patch.object(self.store.clues,'ingest_aisa',side_effect=RuntimeError(SECRET)):
   result=self.done(self.adapter.start(self.config('douyin')))
  self.assertEqual((result['fetched'],result['added'],result['failed']),(2,0,2));self.assertEqual(result['status'],'error')

class ConnectionRecorder:
 """任何 TCP 连接都记一笔（含 TLS 握手）：用来证明跳转目标根本没被触达。"""
 def __init__(self):
  self.sock=socket.socket(socket.AF_INET6);self.sock.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
  self.sock.setsockopt(socket.IPPROTO_IPV6,socket.IPV6_V6ONLY,0)
  self.sock.bind(('::',0));self.sock.listen(5);self.port=self.sock.getsockname()[1];self.hits=[]
  self.thread=threading.Thread(target=self._run,daemon=True);self.thread.start()
 def _run(self):
  while True:
   try:conn,peer=self.sock.accept()
   except OSError:return
   self.hits.append(peer)
   try:conn.recv(64)
   except OSError:pass
   conn.close()
 def close(self):
  try:self.sock.close()
  except OSError:pass
  self.thread.join(2)

class RedirectTransportTests(unittest.TestCase):
 # H1：产品传输层不得跟随任何 3xx，也不得把 Authorization 带到第二个地址。
 def setUp(self):
  self.target=ConnectionRecorder();self.received=[];self.code=302;self.location=''
  outer=self
  class Endpoint(BaseHTTPRequestHandler):
   def do_GET(self):
    outer.received.append((self.path,dict(self.headers)))
    self.send_response(outer.code);self.send_header('Location',outer.location);self.send_header('Content-Length','0');self.end_headers()
   def log_message(self,*a):pass
  self.http=ThreadingHTTPServer(('127.0.0.1',0),Endpoint);self.http.daemon_threads=True
  self.thread=threading.Thread(target=self.http.serve_forever,daemon=True);self.thread.start()
  self.base=f'http://127.0.0.1:{self.http.server_port}'
  self.addCleanup(self.stop)
 def stop(self):self.http.shutdown();self.http.server_close();self.thread.join(2);self.target.close()
 def wait_idle(self):
  until=time.monotonic()+.4
  while time.monotonic()<until and not self.target.hits:time.sleep(.02)
 def test_no_3xx_is_followed_across_host_port_or_protocol(self):
  for code,location in [(301,f'http://localhost:{self.target.port}/stolen'),(302,f'http://127.0.0.1:{self.target.port}/stolen'),(307,f'https://127.0.0.1:{self.target.port}/stolen'),(308,f'http://127.0.0.1:{self.target.port}/stolen')]:
   with self.subTest(code=code,location=location):
    self.code=code;self.location=location
    status,text=urllib_transport('GET',self.base+'/apis/v1/twitter/user/last_tweets',{'Authorization':'Bearer '+SECRET,'Accept':'application/json'},None,5)
    self.assertEqual(status,code)
    self.wait_idle();self.assertEqual(self.target.hits,[])
  self.assertEqual(len(self.received),4)
  self.assertTrue(all(h.get('Authorization')=='Bearer '+SECRET for _,h in self.received))
 def test_source_reports_redirect_as_error_without_leaking_key(self):
  self.code=302;self.location=f'http://localhost:{self.target.port}/stolen'
  source=AIsaSource(transport=urllib_transport,api_key=SECRET,base_url=self.base)
  self.assertEqual(source.get_twitter_tweets('test_user'),[])
  self.assertIn('跳转',source.last_error);self.assertNotIn(SECRET,source.last_error)
  self.wait_idle();self.assertEqual(self.target.hits,[])

CANARY='synthetic-canary-credential-qz7k9m2x4p8v'
FRAGMENTS=(CANARY,CANARY[:16],CANARY[-16:],CANARY[:6],CANARY[10:22])

class ReworkBTests(AIsaFixture):
 # M1：人工标题/摘要要在第二次及以后的指标刷新里持续存活。
 def test_manual_title_survives_second_and_third_refresh(self):
  self.done(self.adapter.start(self.config('xiaohongshu')))
  clue=self.store.clues.listing(source='aisa')['items'][0]
  self.store.clues.save({'id':clue['id'],'revision':clue['revision'],'title':'人工标题','summary':'人工摘要','categories':['technology'],'reason':'人工整理'})
  for index,score in enumerate((111,222,333)):
   self.transport.responses=copy.deepcopy(self.transport.responses);self.transport.responses['xiaohongshu']['data']['data']['items'][0].update(score=score,score_text=f'{score}热度文案')
   job=self.done(self.adapter.start(self.config('xiaohongshu',str(index+1)*32)))
   with self.subTest(refresh=index+1):
    self.assertEqual(job['updated'],1)
    current=self.store.clues.detail(clue['id'])
    self.assertEqual(current['title'],'人工标题');self.assertEqual(current['summary'],'人工摘要')
    self.assertEqual(current['entries'][0]['evidence'][0]['provenance']['metrics']['heat_score'],score)
 def test_auto_title_still_follows_metrics_over_two_refreshes(self):
  self.done(self.adapter.start(self.config('xiaohongshu')))
  for index,score in enumerate((444,555)):
   self.transport.responses=copy.deepcopy(self.transport.responses);self.transport.responses['xiaohongshu']['data']['data']['items'][0].update(score=score)
   self.done(self.adapter.start(self.config('xiaohongshu',str(index+1)*32)))
   with self.subTest(refresh=index+1):
    current=self.store.clues.listing(source='aisa')['items'][0]
    self.assertIn(str(score),current['title']);self.assertEqual(self.store.clues.listing(source='aisa')['total'],1)
 # M2：抖音非空但全部不可解析要判格式错误，合法空数组仍显示 0 条。
 def test_douyin_unparseable_rows_are_error_not_empty_success(self):
  self.transport.fail=(200,json.dumps({'code':200,'data':{'status_code':0,'data':{'word_list':[{},{'no_word':1}]}}}))
  self.assertEqual(self.source.fetch_douyin_hot_searches(),[])
  self.assertIn('抖音',self.source.last_error);self.assertIn('格式',self.source.last_error)
  job=self.done(self.adapter.start(self.config('douyin')))
  self.assertEqual(job['status'],'error');self.assertEqual((job['fetched'],job['failed']),(0,1));self.assertIn('格式',job['errors'][0])
 def test_douyin_legitimate_empty_list_still_reports_zero_rows(self):
  self.transport.fail=(200,json.dumps({'code':200,'data':{'status_code':0,'data':{'word_list':[]}}}))
  self.assertEqual(self.source.fetch_douyin_hot_searches(),[])
  self.assertEqual(self.source.last_error,'')
  job=self.done(self.adapter.start(self.config('douyin')))
  self.assertEqual(job['status'],'done');self.assertEqual((job['fetched'],job['failed'],job['errors']),(0,0,[]))
 # L1：推特的格式错误不能被标成抖音。
 def test_twitter_format_error_names_twitter_not_douyin(self):
  self.transport.fail=(200,json.dumps({'code':0,'status':'success','data':{'tweets':[{}]}}))
  self.assertEqual(self.source.get_twitter_tweets('test_user'),[])
  self.assertIn('推特',self.source.last_error);self.assertNotIn('抖音',self.source.last_error)

class CredentialFragmentTests(AIsaFixture):
 # M3：完整 Key 之外的已知片段（前/后 16、前 6、中间切片）也不得进入返回、快照、回执或库文件。
 def setUp(self):
  super().setUp()
  self.source=AIsaSource(store=self.store,transport=self.transport,api_key=CANARY)
  self.adapter=AIsaRadarAdapter(self.source)
 def test_fragments_never_reach_outputs_snapshots_receipts_or_db(self):
  self.transport.responses=copy.deepcopy(self.transport.responses)
  echo=f'上游回显 {CANARY} / {CANARY[:16]} / {CANARY[-16:]} / {CANARY[:6]} / {CANARY[10:22]}'
  self.transport.responses['xiaohongshu']['data']['data']['items'][0].update(title='合成小红书灵感'+echo,score_text=echo,deeplink='xhsdiscover://post?x='+CANARY)
  items=self.source.fetch_xiaohongshu_inspirations()
  self.assertEqual(len(items),1)
  job=self.done(self.adapter.start(self.config('xiaohongshu')))
  self.assertEqual(job['added'],1)
  listing=self.store.clues.listing(source='aisa');detail=self.store.clues.detail(listing['items'][0]['id'])
  blob=json.dumps({'items':items,'listing':listing,'detail':detail,'job':job,'status':self.source.status()})
  for fragment in FRAGMENTS:
   with self.subTest(fragment_length=len(fragment)):
    self.assertFalse(fragment in blob,f'凭据片段泄漏到出口：长度 {len(fragment)}')
  self.assertIn('合成小红书灵感',detail['title'])
  self.transport.fail=(401,json.dumps({'message':echo,'detail':CANARY[:16]}))
  self.assertEqual(self.source.fetch_xiaohongshu_inspirations(),[])
  self.assertTrue(self.source.last_error)
  for fragment in FRAGMENTS:self.assertFalse(fragment in self.source.last_error,f'凭据片段泄漏到错误回执：长度 {len(fragment)}')
  for p in Path(self.tmp.name).rglob('*'):
   if p.is_file():
    data=p.read_bytes()
    for fragment in FRAGMENTS:self.assertFalse(fragment.encode() in data,f'凭据片段泄漏到库文件：长度 {len(fragment)}')

class LegacyRowGuardTests(AIsaFixture):
    # 复核 M1·c：历史里没有 auto_fields 的旧线索，第一次升级刷新也不能盖掉人工内容。
    def strip_auto_fields(self, thread_id):
        """把线索历史改回旧版写入的形状：旧版的 evidence 条目里没有 auto_fields 这个键。"""
        with self.store.clues.db() as db:
            for row in db.execute('SELECT id,data FROM entries WHERE thread_id=?', (thread_id,)).fetchall():
                entry = json.loads(row['data'])
                entry.pop('auto_fields', None)
                db.execute('UPDATE entries SET data=? WHERE id=?', (json.dumps(entry, ensure_ascii=False), row['id']))

    def refresh(self, identity, score):
        self.transport.responses = copy.deepcopy(self.transport.responses)
        self.transport.responses['xiaohongshu']['data']['data']['items'][0].update(score=score, score_text=f'{score}人在看')
        return self.done(self.adapter.start(self.config('xiaohongshu', identity)))

    def test_legacy_row_keeps_human_title_and_summary_over_three_refreshes(self):
        self.assertEqual(self.refresh('a' * 32, 100)['added'], 1)
        clue = self.store.clues.listing(source='aisa')['items'][0]
        self.store.clues.save({'id': clue['id'], 'revision': clue['revision'], 'title': '历史人工标题',
                               'summary': '历史人工摘要', 'reason': '人工整理'})
        self.assertEqual(self.refresh('b' * 32, 200)['updated'], 1)  # 旧版又刷过一次：最新 after 里已是人工摘要
        self.strip_auto_fields(clue['id'])
        self.assertFalse(any('auto_fields' in e for e in self.store.clues.detail(clue['id'])['entries']))
        for index, score in enumerate((300, 400, 500)):
            job = self.refresh(str(index + 3) * 32, score)
            current = self.store.clues.detail(clue['id'])
            with self.subTest(refresh=index + 1, score=score):
                self.assertEqual(job['updated'], 1)
                self.assertEqual(current['title'], '历史人工标题')
                self.assertEqual(current['summary'], '历史人工摘要')
                self.assertEqual(current['entries'][0]['evidence'][0]['provenance']['metrics']['heat_score'], score)
        self.assertEqual(self.store.clues.listing(source='aisa')['total'], 1)

    def test_legacy_row_never_edited_still_follows_auto_copy(self):
        self.assertEqual(self.refresh('a' * 32, 100)['added'], 1)
        clue = self.store.clues.listing(source='aisa')['items'][0]
        self.assertEqual(self.refresh('b' * 32, 200)['updated'], 1)
        self.strip_auto_fields(clue['id'])
        for index, score in enumerate((600, 700)):
            self.refresh(str(index + 3) * 32, score)
            current = self.store.clues.listing(source='aisa')['items'][0]
            with self.subTest(refresh=index + 1, score=score):
                self.assertIn(str(score), current['title'])
                self.assertIn(str(score), current['summary'])
        self.assertEqual(self.store.clues.listing(source='aisa')['total'], 1)

    def test_legacy_row_with_manual_classification_only_still_follows_auto_copy(self):
        self.assertEqual(self.refresh('a' * 32, 100)['added'], 1)
        clue = self.store.clues.listing(source='aisa')['items'][0]
        self.store.clues.save({'id': clue['id'], 'revision': clue['revision'], 'categories': ['technology'],
                               'status': 'verified', 'heat': 'peak', 'reason': '人工核对'})
        self.strip_auto_fields(clue['id'])
        for index, score in enumerate((800, 900)):
            self.refresh(str(index + 3) * 32, score)
            current = self.store.clues.listing(source='aisa')['items'][0]
            with self.subTest(refresh=index + 1, score=score):
                self.assertIn(str(score), current['title'])
                self.assertEqual(current['categories'], ['technology'])
                self.assertEqual(current['status'], 'verified')
                self.assertEqual(current['heat'], 'peak')

    def test_legacy_row_with_manual_summary_only_keeps_summary_and_follows_title(self):
        self.assertEqual(self.refresh('a' * 32, 100)['added'], 1)
        clue = self.store.clues.listing(source='aisa')['items'][0]
        self.store.clues.save({'id': clue['id'], 'revision': clue['revision'], 'summary': '人工摘要',
                               'reason': '人工整理'})
        self.strip_auto_fields(clue['id'])
        for index, score in enumerate((1000, 1100)):
            self.refresh(str(index + 3) * 32, score)
            current = self.store.clues.detail(clue['id'])
            with self.subTest(refresh=index + 1, score=score):
                self.assertEqual(current['summary'], '人工摘要')
                self.assertIn(str(score), current['title'])


class OverLongCredentialTests(AIsaFixture):
    # 复核 M2：凭据长度超过 SCRUB_MAX_KEY_CHARS 就在第一个请求前被拒，零外连、状态显示配置错误。
    def refused(self, length):
        key = ''.join(chr(ord('a') + (i * 7 + i // 3) % 26) for i in range(length))  # 合成凭据，不是真实 Key
        self.assertEqual(len(key), length)
        source = AIsaSource(store=self.store, transport=self.transport, api_key=key)
        return key, source, AIsaRadarAdapter(source)

    def test_over_long_credential_is_refused_before_any_request(self):
        import aisa_source as mod
        for length in (257, 334):
            key, source, adapter = self.refused(length)
            with self.subTest(length=length):
                # 先验行为：不发任何请求，并给出中文配置错误（在 b 版上这两条就是红的）。
                self.assertEqual(source.fetch_xiaohongshu_inspirations(), [])
                self.assertEqual(self.transport.calls, [])
                self.assertIn('长度异常', source.last_error)
                self.assertIn(str(mod.SCRUB_MAX_KEY_CHARS), source.last_error)
                self.assertNotIn(key, source.last_error)
                self.assertIn('长度异常', source.config_error)
                self.assertNotIn('Authorization', source._headers())
                status = source.status()
                self.assertFalse(status['available'])
                self.assertIn('长度异常', status['detail'])
                with self.assertRaises(ValueError) as caught:
                    adapter.start(self.config('xiaohongshu'))
                self.assertIn('长度异常', str(caught.exception))
                self.assertEqual(self.transport.calls, [])
                self.assertEqual(source.count(), 0)
                for path in Path(self.tmp.name).rglob('*'):
                    if path.is_file():
                        self.assertNotIn(key.encode(), path.read_bytes(), str(path))

    def test_credential_at_the_limit_still_works_and_scrubs_middle_fragments(self):
        import aisa_source as mod
        key = ''.join(chr(ord('a') + (i * 7 + i // 3) % 26) for i in range(mod.SCRUB_MAX_KEY_CHARS))
        source = AIsaSource(store=self.store, transport=self.transport, api_key=key)
        adapter = AIsaRadarAdapter(source)
        self.assertEqual(source.config_error, '')
        self.transport.responses = copy.deepcopy(self.transport.responses)
        self.transport.responses['xiaohongshu']['data']['data']['items'][0].update(
            title='合成小红书灵感 回显 ' + key[120:126] + ' ' + key[100:160], score=456700)
        job = self.done(adapter.start(self.config('xiaohongshu')))
        self.assertEqual(job['added'], 1)
        detail = self.store.clues.detail(self.store.clues.listing(source='aisa')['items'][0]['id'])
        blob = json.dumps({'detail': detail, 'items': source.fetch_xiaohongshu_inspirations(),
                           'status': source.status(), 'job': job}, ensure_ascii=False)
        self.assertEqual(len(self.transport.calls), 2)
        for start in range(len(key) - mod.SCRUB_FRAGMENT_CHARS + 1):
            fragment = key[start:start + mod.SCRUB_FRAGMENT_CHARS]
            with self.subTest(fragment_start=start):
                self.assertNotIn(fragment, blob)
        self.assertIn('合成小红书灵感', blob)
        self.assertNotIn(key, blob)

if __name__=='__main__':unittest.main()
