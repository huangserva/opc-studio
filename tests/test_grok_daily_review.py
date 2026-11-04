import copy
from datetime import datetime,timedelta,timezone
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from contextlib import closing,redirect_stdout
import grok_daily_review as D
import publish_review as P

NOW=datetime(2026,11,4,19,tzinfo=D.BEIJING)

def write(path,data,when=NOW):
 path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(data,ensure_ascii=False));os.utime(path,(when.timestamp(),when.timestamp()));return path

def fixture(root,now=NOW):
 reports=root/'reports';reports.mkdir()
 for kind in ('daily','alerts'):write(reports/kind/(now.date().isoformat()+'.json'),{'collected_at':now.isoformat(),'posts':[]},now)
 write(reports/'interest'/(now.strftime('%Y-%m-%d-%H%M')+'.json'),{'generated_at':now.isoformat(),'items':[]},now)
 write(reports/'posts'/(now.date().isoformat()+'.json'),{'snapshots':[{'captured_at':now.isoformat(),'posts':[{'id':'1','type':'主帖','text':'测试','metrics':{}}]}]},now)
 write(reports/'posts'/('review-'+now.date().isoformat()+'.json'),{'generated_at':now.isoformat(),'summary':['Grok原话']},now)
 write(reports/'weekly/2026-W44.json',{'week':'2026-W44','generated_at':now.isoformat(),'summary':['本周原话']},now)
 write(reports/'radar/state.json',{'updated_at':now.isoformat(),'per_handle':{}},now)
 roster_path=D.R.ROOT/'scripts/radar_targets.json';raw=roster_path.read_bytes();roster=json.loads(raw);write(reports/'radar/targets-copy.json',roster,now)
 for slot in D.expected_slots(now):
  data=dict(schema_version=D.R.SCHEMA,run_id=slot.isoformat(),status='ok',baseline=True,all_groups_complete=True,rule_version='v1',scheduled_slot=slot.isoformat(),round_started_at=slot.isoformat(),finished_at=(slot+timedelta(minutes=1)).isoformat(),target_count=len(roster['targets']),target_list_sha=hashlib.sha256(raw).hexdigest(),accounts=[dict(handle=r['handle'],status='empty',truncated=False) for r in roster['targets']],observations=[],events=[],usage={'search_calls':9,'credits_before':None})
  write(reports/'radar'/(slot.strftime('%Y-%m-%dT%H00')+'-test.json'),data,slot+timedelta(minutes=1))
 dbpath=root/'workspace/knowledge/knowledge.db';dbpath.parent.mkdir(parents=True)
 with closing(sqlite3.connect(dbpath)) as db,db:
  db.execute('CREATE TABLE documents(id TEXT,modified TEXT,tags TEXT)');db.execute('INSERT INTO documents VALUES (?,?,?)',('tagged',now.isoformat(),'["grok-bot"]'))
 return reports

class DailyReviewTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory(dir='/tmp',prefix='grok-review-test-');self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name).resolve();self.reports=fixture(self.root)
 def generate(self):return D.generate(self.root,NOW,self.reports)
 def checks(self,meta):return {c['key']:c for c in meta['checks']}
 def missing(self,folder):
  for path in (self.reports/folder).glob('*.json'):
   if folder!='radar' or D.R.ROUND_NAME.fullmatch(path.name):path.unlink()
 def test_normal_fixed_judgment_counts_schedule_and_output_pair(self):
  path,meta=self.generate();self.assertEqual(meta['judgment'],'今日无异常');self.assertEqual(meta['anomaly_count'],0);self.assertEqual(meta['to_grok'],'无');self.assertEqual(meta['markdown_sha256'],D.digest(path.read_bytes()));self.assertEqual(D.snapshot(self.root,NOW)['status'],'ok')
  self.assertIsNone(meta['usage'][0]['provided']['usage']['credits_before']);self.assertEqual(meta['usage'][0]['provided']['usage']['search_calls'],9)
  cfg=D.schedule_payload(self.root);self.assertEqual((cfg['kind'],cfg['at'],cfg['assistant_id'],cfg['tool_groups'],cfg['allow_commands']),('pi','09:30','isolated',[],True));self.assertIn('第一步必须',cfg['body']);self.assertIn('--prepare',cfg['body']);self.assertIn('--expected-sha256',cfg['body'])
 def test_missing_radar_twenty_four_hours_is_missing_not_normal(self):
  self.missing('radar');_,meta=self.generate();checks=self.checks(meta);self.assertEqual(checks['radar']['status'],'缺');self.assertEqual(checks['radar_slots']['status'],'缺');self.assertEqual(len(checks['radar_slots']['missing_slots']),9);self.assertNotEqual(meta['judgment'],'今日无异常')
 def test_missing_daily_is_missing(self):
  self.missing('daily');_,meta=self.generate();self.assertEqual(self.checks(meta)['daily']['status'],'缺');self.assertNotEqual(meta['judgment'],'今日无异常')
 def test_missing_posts_is_missing(self):
  self.missing('posts');_,meta=self.generate();self.assertEqual(self.checks(meta)['posts']['status'],'缺');self.assertNotEqual(meta['judgment'],'今日无异常')
 def test_weekly_is_weekly_not_daily(self):
  path=self.reports/'weekly/2026-W44.json';write(path,{'week':'2026-W44','generated_at':(NOW-timedelta(days=3)).isoformat(),'summary':['原话']},NOW-timedelta(days=3))
  _,meta=self.generate();c=self.checks(meta)['weekly'];self.assertEqual(c['status'],'正常');self.assertEqual(c['files_24h'],0)
 def test_bad_json_empty_wrong_shape_big_symlink_future_isolate(self):
  outside=self.root/'outside.json';outside.write_text('{"posts":[]}')
  cases=['bad','empty','shape','big','symlink','future']
  for name in cases:
   p=self.reports/'daily'/('2026-11-04-'+name+'.json')
   if name=='bad':p.write_text('{bad')
   elif name=='empty':p.write_text('')
   elif name=='shape':write(p,{'posts':'bad','generated_at':NOW.isoformat()})
   elif name=='big':
    with p.open('wb') as f:f.truncate(D.MAX_BYTES+1)
   elif name=='symlink':p.symlink_to(outside)
   else:write(p,{'posts':[],'generated_at':(NOW+timedelta(hours=1)).isoformat()})
   if name!='symlink':os.utime(p,(NOW.timestamp(),NOW.timestamp()))
  _,meta=self.generate();c=self.checks(meta);self.assertGreaterEqual(c['readers']['bad_files'],6);self.assertEqual(c['alerts']['status'],'正常');self.assertGreater(meta['anomaly_count'],0)
 def test_auxiliary_not_bad_not_real_report(self):
  write(self.reports/'alerts/knowledge_post_results_2026-11-04.json',{'sent':4});_,meta=self.generate();c=self.checks(meta)['readers'];self.assertEqual(c['bad_files'],0);self.assertGreaterEqual(c['auxiliary_files'],1)
 def test_future_mtime_is_rejected(self):
  p=self.reports/'daily/2026-11-04.json';os.utime(p,((NOW+timedelta(hours=1)).timestamp(),)*2);_,meta=self.generate();self.assertEqual(self.checks(meta)['daily']['status'],'缺')
 def test_readonly_reports_and_only_output_folder_writes(self):
  before={str(p):p.read_bytes() for p in self.reports.rglob('*.json')};original=os.open;opened=[];out=self.root/'workspace/grok-review'
  def guard(path,flags,*args,**kw):
   if isinstance(path,(str,Path)):
    target=Path(path).resolve()
    if target.is_relative_to(self.reports):self.assertFalse(flags&(os.O_WRONLY|os.O_RDWR|os.O_CREAT|os.O_TRUNC|os.O_APPEND));opened.append(str(target))
    if flags&(os.O_WRONLY|os.O_RDWR|os.O_CREAT|os.O_TRUNC|os.O_APPEND):self.assertTrue(target.is_relative_to(out),str(target))
   return original(path,flags,*args,**kw)
  original_io=io.open;original_replace=os.replace
  def io_guard(path,mode='r',*args,**kw):
   if isinstance(path,(str,Path)) and any(c in mode for c in 'wax+'):
    self.assertTrue(Path(path).resolve().is_relative_to(out))
   return original_io(path,mode,*args,**kw)
  def replace_guard(src,dst,*args,**kw):
   self.assertTrue(Path(dst).resolve().is_relative_to(out));return original_replace(src,dst,*args,**kw)
  with patch('os.open',side_effect=guard),patch('io.open',side_effect=io_guard),patch('os.replace',side_effect=replace_guard):self.generate()
  self.assertTrue(opened);self.assertEqual(before,{str(p):p.read_bytes() for p in self.reports.rglob('*.json')})
 def test_fixed_source_not_query_and_traversal_rejected(self):
  with self.assertRaises(ValueError):D.read_source(self.reports/'../outside.json',self.reports,NOW)
  from urllib.parse import urlsplit
  with patch.object(D,'snapshot',side_effect=AssertionError('query must not read')):
   with self.assertRaises(ValueError):P.get_route(None,urlsplit('/api/publish/review'),{'root':['../../etc']})
 def test_finalize_wrong_sha_first_rejected_and_counts_immutable(self):
  self.missing('radar');path,meta=self.generate();before=path.read_bytes()
  with self.assertRaises(ValueError):D.finalize(self.root,'问题','请补轮','0'*64,NOW)
  self.assertEqual(before,path.read_bytes())
  _,done=D.finalize(self.root,'{"files_24h":999,"anomaly_count":0}','请恢复规定轮次',meta['markdown_sha256'],NOW)
  self.assertEqual(done['checks'],meta['checks']);self.assertEqual(done['anomaly_count'],meta['anomaly_count']);self.assertEqual(done['checks_sha256'],meta['checks_sha256'])
 def test_repeat_finalize_rejected_including_current_sha(self):
  self.missing('radar');_,meta=self.generate();_,done=D.finalize(self.root,'补轮次','请补轮次',meta['markdown_sha256'],NOW)
  with self.assertRaises(ValueError):D.finalize(self.root,'再改','再改',done['markdown_sha256'],NOW)
 def test_missing_cannot_finalize_no_anomaly_and_max_five(self):
  self.missing('daily');_,meta=self.generate()
  for text in ('今日无异常','无','1\n2\n3\n4\n5\n6'):
   with self.assertRaises(ValueError):D.finalize(self.root,text,'话',meta['markdown_sha256'],NOW)
 def test_normal_cannot_replace_fixed_judgment(self):
  _,meta=self.generate()
  with self.assertRaises(ValueError):D.finalize(self.root,'凭空问题','凭空问题',meta['markdown_sha256'],NOW)
 def test_tampered_check_data_rejected(self):
  path,meta=self.generate();meta['checks'][0]['files_24h']=999;path.with_suffix('.json').write_text(json.dumps(meta))
  self.assertEqual(D.snapshot(self.root,NOW)['status'],'error')
 def test_output_symlink_rejected(self):
  target=self.root/'elsewhere';target.mkdir();(self.root/'workspace/grok-review').symlink_to(target)
  with self.assertRaises(ValueError):self.generate()
  self.assertEqual(D.snapshot(self.root,NOW)['status'],'error');self.assertEqual(list(target.iterdir()),[])
 def test_empty_and_previous_day_snapshot(self):
  self.assertEqual(D.snapshot(self.root,NOW)['status'],'missing');self.generate();result=D.snapshot(self.root,NOW+timedelta(days=1));self.assertFalse(result['is_today']);self.assertEqual(result['date'],'2026-11-04')
 def test_roster_duplicate_counted_once_but_error(self):
  p=self.reports/'radar/targets-copy.json';data=json.loads(p.read_text());n=len(data['targets']);data['targets'].append(data['targets'][0]);write(p,data)
  handles,ok=D.roster(self.reports,NOW);self.assertEqual(len(handles),n);self.assertFalse(ok)
 def test_slot_late_or_incomplete_not_covered(self):
  paths=D.R.round_paths(self.reports);p=paths[-1];data=json.loads(p.read_text());data['all_groups_complete']=False;write(p,data)
  _,meta=self.generate();self.assertEqual(self.checks(meta)['radar_slots']['status'],'缺')
 def test_knowledge_only_readonly_and_no_false_new_count(self):
  result=D.knowledge(self.root,NOW);self.assertEqual(result['modified_in_window'],1);self.assertIsNone(result['new_count'])
 def test_cli_source_override_not_supported(self):
  with patch('sys.argv',['grok_daily_review.py','--prepare','--reports','../../secret']),patch('sys.stderr',new=io.StringIO()):
   with self.assertRaises(SystemExit):D.main()
 def test_usage_not_provided_and_filename_time_exposed(self):
  for p in D.R.round_paths(self.reports):
   data=json.loads(p.read_text());data.pop('usage');write(p,data)
  _,meta=self.generate();self.assertEqual(meta['usage'],'未提供');self.assertEqual(self.checks(meta)['daily']['latest_file'],'daily/2026-11-04.json');self.assertTrue(self.checks(meta)['daily']['latest_file_at'])
 def test_old_payload_not_reparsed_only_file_metadata(self):
  old=self.reports/'daily/2026-10-01.json';old.write_text('{corrupt ancient report');os.utime(old,((NOW-timedelta(days=34)).timestamp(),)*2)
  _,meta=self.generate();self.assertEqual(self.checks(meta)['readers']['bad_files'],0)
 def test_weekly_markdown_uses_existing_reader_and_bad_utf8_is_counted(self):
  md=self.reports/'weekly/2026-W44.md';md.write_bytes(b'\xff');os.utime(md,(NOW.timestamp(),NOW.timestamp()))
  _,meta=self.generate();self.assertEqual(self.checks(meta)['readers']['status'],'异常');self.assertTrue(any(e['file'].endswith('.md') for e in self.checks(meta)['readers']['errors']))
 def test_standalone_usage_auxiliary_preserves_provided_numbers(self):
  write(self.reports/'radar/usage.json',{'updated_at':NOW.isoformat(),'credits_used':12,'search_calls':3})
  _,meta=self.generate();entry=next(u for u in meta['usage'] if u['file']=='radar/usage.json');self.assertEqual(entry['provided'],{'credits_used':12,'search_calls':3})

class ThresholdTests(unittest.TestCase):
 def tweet(self,**kw):
  row=dict(tweet_id='123',type='original',published_at=(NOW-timedelta(hours=1)).isoformat(),fetched_at=NOW.isoformat(),is_stale=False,text='AI Agent亲测',full_text='AI Agent亲测',author='gkxspace',author_handle='gkxspace',queried_handle='gkxspace',eligible_author=True,views=3500,likes=0,followers=20000,retweets=0,replies=0);row.update(kw);return row
 def evaluate(self,row,previous=None,pushed=None,events=None):
  initial=D.R._Replay().fresh_state();initial['initialized']=True
  if previous:initial['samples'][row['tweet_id']]=previous
  if pushed:initial['pushed'][row['tweet_id']]=pushed
  initial['events']=events or []
  data=dict(run_id='test',slot=NOW.isoformat(),started=NOW,complete=True,baseline=False,observations=[row],events=[])
  return D.replay_events([('test.json',data)],initial)
 def test_new_fast_equal_and_both_sides(self):
  for hours,views,expected in [(1,3500,1),(1,3499,0),(1,3501,1),(.25,2500,1),(.25,2499,0),(.25,2501,1),(.249,2500,0),(.251,2500,1)]:
   with self.subTest(hours=hours,views=views):self.assertEqual(self.evaluate(self.tweet(published_at=(NOW-timedelta(hours=hours)).isoformat(),views=views))['computed']['new_fast'],expected)
 def test_lowfan_each_threshold_boundary(self):
  base=self.tweet(views=50000,likes=200,followers=10000,published_at=(NOW-timedelta(hours=6)).isoformat())
  for field,value,expected in [('followers',10000,1),('followers',10001,0),('followers',9999,1),('views',50000,1),('views',49999,0),('views',50001,1),('likes',200,1),('likes',199,0),('likes',201,1),('published_at',(NOW-timedelta(hours=6.001)).isoformat(),0),('published_at',(NOW-timedelta(hours=5.999)).isoformat(),1)]:
   with self.subTest(field=field,value=value):
    row={**base,field:value};previous={**row,'fetched_at':(NOW-timedelta(hours=1)).isoformat()};self.assertEqual(self.evaluate(row,previous)['computed']['lowfan_burst'],expected)
 def test_hourly_delta_equal_and_both_sides(self):
  previous=self.tweet(views=10000,fetched_at=(NOW-timedelta(hours=2)).isoformat())
  for views,expected in [(19999,0),(20000,1),(20001,1),(9999,0)]:
   with self.subTest(views=views):self.assertEqual(self.evaluate(self.tweet(views=views),previous)['computed']['hourly_delta'],expected)
 def test_upgrade_equal_and_sent_required(self):
  for views,expected in [(99999,0),(100000,1),(100001,1)]:
   with self.subTest(views=views):self.assertEqual(self.evaluate(self.tweet(views=views),pushed={'upgraded':False})['computed']['upgrade_100k'],expected)
  row=self.tweet(views=100000);self.assertEqual(self.evaluate(row,pushed={'upgraded':True})['computed']['upgrade_100k'],0)
  pending=[dict(type='new_fast',tweet=row,status='pending')];self.assertEqual(self.evaluate(row,events=pending)['computed']['upgrade_100k'],0)
 def test_mismatch_numeric_evidence_and_limit(self):
  initial=D.R._Replay().fresh_state();initial['initialized']=True
  rows=[self.tweet(tweet_id=str(i+1)) for i in range(12)];data=dict(run_id='test',slot=NOW.isoformat(),started=NOW,complete=True,baseline=False,observations=rows,events=[])
  result=D.replay_events([('test.json',data)],initial);self.assertEqual(result['mismatch_count'],12);self.assertEqual(len(result['mismatches']),10);self.assertEqual(result['mismatches'][0]['views'],3500)

if __name__=='__main__':unittest.main()
