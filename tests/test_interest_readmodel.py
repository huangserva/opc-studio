"""生产模式失效/并发反例；只写独立/tmp，所有patch由上下文回收。"""
from contextlib import ExitStack,closing
from datetime import datetime,timedelta,timezone
import hashlib,json,os,sqlite3,tempfile,threading,time,unittest
from pathlib import Path
from unittest.mock import patch
import grok_bot_radar as G
import interest_channel as I
import interest_sources as S
import interest_projection as P
import today_view as T
import official_accounts as O
import clue_morning as C
from tests.test_interest_all import fixture,NOW

class ReadModelTests(unittest.TestCase):
 def setUp(self):
  tmp=tempfile.TemporaryDirectory(prefix='interest-readmodel-',dir='/tmp');self.addCleanup(tmp.cleanup)
  self.root,self.reports=fixture(tmp.name);self.base=Path(tmp.name)
  stack=self.enterContext(ExitStack());stack.enter_context(patch.dict(os.environ,{'GROK_BOT_REPORTS_DIR':str(self.reports)}));stack.enter_context(patch.object(G,'BUNDLE_PATH',self.base/'bundle.json'))
  self.addCleanup(self.join)
 def join(self):
  for states in (S.STATES,P.STATES):
   for st in list(states.values()):
    if st.get('worker'):st['worker'].join(5)
 def tearDown(self):
  # 每种条件都证明请求线程不会整池计算；同步computed变异必须逐项报F。
  with patch.object(P,'schedule'),patch.object(I,'_cross_source_page',side_effect=AssertionError('request must not compute entire pool')) as compute:
   P.page(self.root,{'range':['7d'],'audit':['0']});self.assertEqual(compute.call_count,0)
 def build(self):return P.publish(self.root,NOW)
 def test_default_summary_and_reference_fields_equal(self):
  self.build()
  baseline=T.page_view(self.root,{'channel':['interest'],'range':['7d']},NOW)
  with patch.object(P,'schedule'):
   full=T.page_view(self.root,{'channel':['interest'],'range':['7d']})
   lean=T.page_view(self.root,{'channel':['interest'],'range':['7d'],'view':['summary'],'audit':['0']})
  self.assertEqual(full['items'],baseline['items']);self.assertEqual(full['filtering'],baseline['filtering'])
  for key in full:
   if key not in ('filtering','interest_cache'):self.assertEqual(full[key],lean[key],key)
  self.assertEqual(lean['filtering']['items'],[])
  self.assertEqual({k:v for k,v in full['filtering'].items() if k!='items'},{k:v for k,v in lean['filtering'].items() if k!='items'})
  self.assertEqual(full['items'][0]['id'],P.detail(self.root,full['items'][0]['id'])['id'])
 def test_only_page_is_k1_annotated_and_no_request_full_compute(self):
  target=next((self.root/'twitter-radar/snapshots').glob('*/*.json'));data=json.loads(target.read_text())
  for n in range(100):data['tweets'].append(dict(tweet_id=str(10000+n),url=f'https://x.com/fixture/status/{10000+n}',text='MCP 编程工具的完整教程',fetched_at=(NOW-timedelta(seconds=10)).isoformat()))
  target.write_text(json.dumps(data))
  self.build()
  with patch.object(P,'schedule'),patch.object(I,'_cross_source_page',side_effect=AssertionError('inline compute')),patch.object(T,'annotate_items',wraps=T.annotate_items) as annotate:
   result=T.page_view(self.root,{'channel':['interest'],'range':['7d'],'view':['summary'],'audit':['0']})
  self.assertGreater(result['total'],50);self.assertEqual(annotate.call_count,1);self.assertEqual(len(annotate.call_args.args[1]),len(result['items']));self.assertEqual(len(result['items']),50)
 def test_profile_mtime_and_unrelated_db_writes_keep_fingerprint(self):
  self.build();before=P.fingerprint(self.root,NOW)
  p=self.root/'official-accounts/profiles.json';p.parent.mkdir(exist_ok=True);p.write_text('{}');os.utime(p,None)
  with closing(sqlite3.connect(self.root/'knowledge/knowledge.db')) as db,db:db.execute('CREATE TABLE unrelated_jobs(id TEXT)');db.execute("INSERT INTO unrelated_jobs VALUES('job')")
  self.assertEqual(before,P.fingerprint(self.root,NOW))
  entered=threading.Event();release=threading.Event()
  def checking(*args):entered.set();release.wait(2);return before
  with patch.object(P,'fingerprint',side_effect=checking),patch.object(P,'publish',side_effect=AssertionError('unrelated write full rebuild')) as publish:
   try:
    result=P.page(self.root,{'range':['7d'],'audit':['0']});self.assertTrue(entered.wait(1));self.assertTrue(result['interest_cache']['ready']);self.assertTrue(result['interest_cache']['checking']);self.assertFalse(result['interest_cache']['refreshing'])
   finally:release.set();self.join()
   self.assertEqual(publish.call_count,0)
  # 真正的输入正文变更必须改变签名，不能忽略全部DB变更来变绿。
  with closing(sqlite3.connect(self.root/'knowledge/knowledge.db')) as db,db:db.execute("UPDATE documents SET body='新的 MCP 教程' WHERE id='ext'")
  self.assertNotEqual(before,P.fingerprint(self.root,NOW))
 def test_expired_refreshing_concurrent_reads_single_flight(self):
  self.build();entered=threading.Event();release=threading.Event();self.addCleanup(release.set)
  class Clock(datetime):
   @classmethod
   def now(cls,tz=None):return NOW+timedelta(minutes=6)
  def slow(*args,**kwargs):entered.set();release.wait(3)
  with patch.object(P,'datetime',Clock),patch.object(P,'publish',side_effect=slow) as build,patch.object(I,'_cross_source_page',side_effect=AssertionError('inline compute')):
   first=P.page(self.root,{'range':['7d'],'audit':['0']});self.assertTrue(entered.wait(2));owner=P.state(self.root)['worker']
   results=[];threads=[threading.Thread(target=lambda:results.append(P.page(self.root,{'range':['7d'],'audit':['0']}))) for _ in range(4)]
   for t in threads:t.start()
   for t in threads:t.join(2);self.assertFalse(t.is_alive())
   self.assertIs(P.state(self.root)['worker'],owner);self.assertEqual(build.call_count,1);self.assertTrue(all(r['items']==first['items'] and r['interest_cache']['refreshing'] for r in results));self.assertFalse(first['interest_cache']['partial'])
   release.set();self.join()
 def test_persistent_file_cache_survives_restart_and_more_than16_files(self):
  paths=[]
  for n in range(23):
   p=self.reports/'daily'/f'fixture-{n}.json';p.write_text('{"posts":[]}');paths.append(p)
  with patch.object(S,'parse_file',return_value=[]) as parse:
   for p in paths:S.cached_file(self.root,p,'report:daily')
   self.assertEqual(parse.call_count,23)
   for p in paths:S.cached_file(self.root,p,'report:daily')
   self.assertEqual(parse.call_count,23)
   with S.FILE_LOCK:S.FILE_CACHE.clear()
   for p in paths:S.cached_file(self.root,p,'report:daily')
   self.assertEqual(parse.call_count,23)
   paths[0].write_text('{"posts":[],"changed":true}');S.cached_file(self.root,paths[0],'report:daily');self.assertEqual(parse.call_count,24)
   S.prune_filecache(self.root,{str(paths[0])})
   with S.readonly(S.filecache_path(self.root)) as db:self.assertEqual(db.execute('SELECT count(*) FROM files').fetchone()[0],1)
   with S.FILE_LOCK:S.FILE_CACHE.clear()
   (self.root/'today/interest-filecache-v2.sqlite3').write_bytes(b'corrupt optional cache')
   self.assertEqual(S.cached_file(self.root,paths[0],'report:daily'),[]);self.assertEqual(parse.call_count,25)
 def test_formal_root_automatic_network_profile_update_does_not_rebuild(self):
  self.build();signature=P.fingerprint(self.root,NOW);calls=[];entered=threading.Event();release=threading.Event()
  def fetch(account):calls.append(account);entered.set();release.wait(2);return dict(verification_type='organization',followers=123)
  with patch.object(O,'ROOT',self.base.resolve()),patch.object(O,'RATE_SECONDS',0):
   profiles=O.Profiles(self.root,fetcher=fetch)
   self.assertTrue(profiles.network) # 覆盖此前/tmp根自动关闭network的盲点。
   profiles.lookup('fixture');worker=profiles.worker
   try:
    self.assertTrue(entered.wait(1))
    with patch.dict(O._profiles,{str(O.workspace(self.root)):profiles}),patch.object(P,'schedule'),patch.object(I,'_cross_source_page',side_effect=AssertionError('pending network induced full compute')):
     pending=T.page_view(self.root,{'channel':['interest'],'range':['7d'],'audit':['0']})
    self.assertFalse(next(p for p in pending['items'] if p['id']=='x:303')['official']['is_official'])
   finally:
    release.set()
    if worker:worker.join(2)
   self.assertEqual(calls,['fixture']);self.assertTrue(profiles.path.exists())
   self.assertEqual(P.fingerprint(self.root,NOW),signature)
   with patch.dict(O._profiles,{str(O.workspace(self.root)):profiles}),patch.object(P,'schedule'),patch.object(I,'_cross_source_page',side_effect=AssertionError('profile induced full compute')):
    result=T.page_view(self.root,{'channel':['interest'],'range':['7d'],'audit':['0']})
   self.assertTrue(next(p for p in result['items'] if p['id']=='x:303')['official']['is_official'])
 def test_missing_model_returns_partial_and_starts_only_background(self):
  entered=threading.Event();release=threading.Event();self.addCleanup(release.set)
  def slow(*args):entered.set();release.wait(3)
  with patch.object(P,'refresh',side_effect=slow),patch.object(I,'_cross_source_page',side_effect=AssertionError('request full compute')):
   result=P.page(self.root,{'range':['7d']});self.assertTrue(entered.wait(2));self.assertTrue(result['interest_cache']['partial']);self.assertEqual(result['items'],[]);release.set();self.join()
 def test_midnight_returns_previous_then_background_new_day(self):
  clock=[NOW.astimezone(G.BEIJING).replace(hour=23,minute=59,second=59,microsecond=0).astimezone(timezone.utc)]
  P.publish(self.root,clock[0])
  class Clock(datetime):
   @classmethod
   def now(cls,tz=None):return clock[0]
  entered=threading.Event();release=threading.Event();self.addCleanup(release.set);original=P.publish
  def delayed(*args,**kwargs):entered.set();release.wait(3);return original(*args,**kwargs)
  with patch.object(P,'datetime',Clock),patch.object(C,'datetime',Clock):
   with patch.object(P,'schedule'):before=P.page(self.root,{'range':['today']})
   self.assertGreater(len(before['items']),0);clock[0]+=timedelta(seconds=1)
   with patch.object(P,'publish',side_effect=delayed):
    stale=P.page(self.root,{'range':['today']});morning=T.page_view(self.root,{'channel':['interest'],'range':['today'],'audit':['0']})['clue_morning'];self.assertEqual(morning['date'],clock[0].astimezone(G.BEIJING).date().isoformat());self.assertTrue(entered.wait(2));self.assertEqual(stale['items'],before['items']);self.assertTrue(stale['interest_cache']['refreshing']);release.set();self.join()
   with patch.object(P,'schedule'):after=P.page(self.root,{'range':['today']})
   self.assertEqual(after['items'],[]);self.assertTrue(after['interest_cache']['ready'])
 def test_bad_schema_partial_no_inline_recompute(self):
  self.build();target=P.path(self.root)
  with closing(sqlite3.connect(target)) as db,db:
   for period,data in db.execute('SELECT period,data FROM meta').fetchall():
    value=json.loads(data);value['schema']=999;db.execute('UPDATE meta SET data=? WHERE period=?',(json.dumps(value),period))
  with patch.object(P,'schedule') as schedule,patch.object(I,'_cross_source_page',side_effect=AssertionError('bad schema sync rebuild')):
   result=P.page(self.root,{'range':['7d']});self.assertTrue(result['interest_cache']['partial']);schedule.assert_called_once()
 def test_feedback_is_immediate_without_whole_pool_recompute(self):
  self.build();target=self.root/'today/feedback.json';target.write_text(json.dumps({'interest:x:303':dict(status='kept',feedback='accurate')}))
  with patch.object(P,'schedule'),patch.object(I,'_cross_source_page',side_effect=AssertionError('feedback full compute')):
   result=T.page_view(self.root,{'channel':['interest'],'range':['7d'],'audit':['0']});detail=T.item_view(self.root,{'channel':['interest'],'id':['x:303']})
  card=next(p for p in result['items'] if p['id']=='x:303')
  self.assertEqual((card['status'],card['feedback']),('kept','accurate'));self.assertEqual((detail['status'],detail['feedback']),('kept','accurate'))
 def test_copy_isolation_and_source_change_background_refresh(self):
  self.build()
  with patch.object(P,'schedule'):
   first=P.page(self.root,{'range':['7d']});count=len(first['items']);first['items'].clear();first['filtering']['items'].clear()
   second=P.page(self.root,{'range':['7d']});self.assertEqual(len(second['items']),count);self.assertGreater(len(second['filtering']['items']),0)
  target=next((self.root/'twitter-radar/snapshots').glob('*/*.json'));data=json.loads(target.read_text())
  next(p for p in data['tweets'] if p['tweet_id']=='555')['text']='MCP 编程工具的具体接入教程'
  target.write_text(json.dumps(data));st=P.state(self.root);P.refresh(self.root,st,NOW)
  with patch.object(P,'schedule'):after=P.page(self.root,{'range':['7d']})
  self.assertEqual(len(after['items']),count+1);self.assertIn('x:555',{p['id'] for p in after['items']})
  before_hash=hashlib.sha256(P.path(self.root).read_bytes()).hexdigest()
  key=(str(S.workspace(self.root).resolve()),str(self.reports.resolve()));mismatched={**S.STATES[key]['data'],'signature':'older-generation'}
  _,valid_status=S.candidates(self.root,NOW)
  with patch.object(S,'candidates',return_value=(mismatched,valid_status)):
   with self.assertRaisesRegex(ValueError,'source_changed_during_index'):P.publish(self.root,NOW)
  self.assertEqual(hashlib.sha256(P.path(self.root).read_bytes()).hexdigest(),before_hash)
 def test_linuxdo_refresh_keeps_complete_previous_snapshot_until_publish(self):
  class Clock(datetime):
   @classmethod
   def now(cls,tz=None):return NOW
  with patch.object(P,'datetime',Clock):
   self.build()
   with patch.object(P,'schedule'):
    before={period:P.page(self.root,{'range':[period],'audit':['0']}) for period in ('today','7d')}
   source=self.root/'linuxdo/top-daily.json';raw=json.loads(source.read_text());row=dict(raw['items'][0]);row.update(id='linuxdo:77889900',topic_id=77889900,title='Codex MCP 完整教程',full_text='Codex MCP 完整接入教程',url='https://linux.do/t/refresh/77889900');raw['items'].append(row);source.write_text(json.dumps(raw))
   entered=threading.Event();release=threading.Event();self.addCleanup(release.set);original=P.publish
   def delayed(*args,**kwargs):entered.set();release.wait(4);return original(*args,**kwargs)
   with patch.object(P,'publish',side_effect=delayed):
    try:
     P.page(self.root,{'range':['today'],'audit':['0']});self.assertTrue(entered.wait(2))
     for period in ('today','7d'):
      old=P.page(self.root,{'range':[period],'audit':['0']});self.assertEqual(old['items'],before[period]['items']);self.assertEqual(old['total'],before[period]['total']);self.assertGreater(old['total'],0)
      self.assertFalse(old['interest_cache']['ready']);self.assertFalse(old['interest_cache']['partial']);self.assertTrue(old['interest_cache']['refreshing'])
    finally:release.set();self.join()
   with patch.object(P,'schedule'):
    for period in ('today','7d'):
     after=P.page(self.root,{'range':[period],'audit':['0']});self.assertEqual(after['total'],before[period]['total']+1);self.assertTrue(after['interest_cache']['ready']);self.assertIn('linuxdo:77889900',{p['id'] for p in after['items']})

 def test_corrupt_sqlite_returns_partial_without_inline_recompute(self):
  self.build();P.path(self.root).write_bytes(b'{bad old JSON is not sqlite')
  with patch.object(P,'schedule') as schedule,patch.object(I,'_cross_source_page',side_effect=AssertionError('corrupt model sync compute')):
   result=P.page(self.root,{'range':['7d']});self.assertTrue(result['interest_cache']['partial']);self.assertTrue(result['interest_cache']['refreshing']);schedule.assert_called_once()
 def test_enabled_directions_change_refreshes_result(self):
  self.build();before=P.fingerprint(self.root,NOW)
  (self.reports/'interest-topics.md').write_text('# 兴趣\n\n## 当前方向\n- 完全不同方向（RareZZZ）\n\n## 说明\n只读夹具\n')
  self.assertNotEqual(before,P.fingerprint(self.root,NOW));P.refresh(self.root,P.state(self.root),NOW)
  with patch.object(P,'schedule'):after=P.page(self.root,{'range':['7d']})
  self.assertEqual(after['items'],[]);self.assertEqual(after['configured_topics'],['完全不同方向（RareZZZ）']);self.assertTrue(after['interest_cache']['ready'])

if __name__=='__main__':unittest.main()
