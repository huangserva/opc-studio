from datetime import datetime,timedelta,timezone
from pathlib import Path
import copy,json,os,shutil,tempfile,threading,unittest
from unittest.mock import patch
from scripts import interest_video_search as M
import interest_sources as S
import interest_match as IM
import grok_bot_radar as G
NOW=datetime(2026,11,4,10,tzinfo=M.TZ)
KEY='synthetic-interest-video-credential-only'
ROOT=Path(__file__).resolve().parents[1]
BODIES=['开源权重 GGUF 下载教程','llama.cpp 本地部署详细命令教程','ComfyUI 工作流节点参数教程','sglang 模型网关部署配置教程','Claude Code MCP 安装教程','SkillSpector Skill 安全扫描安装教程','OpenCode 插件安装教程','剪映 批量出片脚本教程','3D生成 教程 工作流']
def raw(platform,body,index=0):
 if platform=='douyin':return dict(aweme_id=str(7700000000000000000+index),aweme_type=0,media_type=4,desc=body,create_time=int((NOW-timedelta(hours=1)).timestamp()),statistics=dict(digg_count=123,collect_count=23,comment_count=4,share_count=5),author=dict(nickname='隔离作者'))
 return dict(id=f'{index+1:024x}',type='video',title=body,desc=body,timestamp=int((NOW-timedelta(hours=1)).timestamp()),liked_count='321',collected_count='42',comments_count='2',shared_count='3',user=dict(nickname='隔离作者'))
def response(platform,rows):
 if platform=='xhs':return {'code':200,'data':{'code':0,'success':True,'data':{'items':[{'note':r} for r in rows]}}}
 return {'code':200,'data':[{'advertisement':{'text':'不应解析此段'}},{'status_code':0,'business_data':[{'data':{'aweme_info':r}} for r in rows]}]}
# Historical filtered-first fixture exercises the old fallback branch unchanged.
# New production-default tests do not install this fixture.
PRODUCTION_REQUEST_SPEC=M.request_spec
def legacy_request_spec(platform,word,default_filters=False):
 method,path,params=PRODUCTION_REQUEST_SPEC(platform,word,default_filters)
 if platform=='douyin':
  if not default_filters:params.update(sort_type='2',publish_time='1',content_type='1',backtrace='')
  else:
   params.update(sort_type='0',publish_time='0',content_type='0');params.pop('backtrace',None)
 return method,path,params
class SearchTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory(prefix='interest-video-test-',dir='/tmp');self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name);(self.root/'workspace').mkdir();(self.root/'scripts').mkdir();self.reports=self.root/'reports';self.reports.mkdir()
  self.cfg=json.loads((ROOT/'tests/fixtures/interest_video_search/legacy_config.json').read_text());(self.reports/'interest-topics.md').write_text('## 当前方向\n'+''.join('- '+r['name']+'\n' for r in self.cfg['directions']));self.path=self.root/'scripts/interest_video_search_config.json';self.save()
  p=patch.dict(os.environ,{'GROK_BOT_REPORTS_DIR':str(self.reports)});p.start();self.addCleanup(p.stop)
  p=patch.object(M,'request_spec',side_effect=legacy_request_spec);p.start();self.addCleanup(p.stop)
 def save(self):self.path.write_text(json.dumps(self.cfg,ensure_ascii=False))
 def transport(self,url,method,params,key,timeout):
  from urllib.parse import parse_qs,urlsplit
  platform='xhs' if 'xiaohongshu' in url else 'douyin';word=parse_qs(urlsplit(url).query)['keyword'][0] if platform=='xhs' else params['keyword'];i=next(i for i,r in enumerate(self.cfg['directions']) if word in r['keywords']);return response(platform,[raw(platform,BODIES[i],i)])
 def run_round(self,transport=None,**kwargs):return M.run(self.root,reports=self.reports,key=KEY,clock=lambda:NOW,transport=transport or self.transport,**kwargs)
 def budget(self,count):M.write(M.storage(self.root)/'budget'/f'{NOW.date()}.json',{'day':NOW.date().isoformat(),'calls':[{'status':'reserved'} for _ in range(count)]})
 def test_full_round_eighteen_queries_all_directions_and_platforms(self):
  result=self.run_round();self.assertEqual(result['status'],'ok');self.assertEqual(len(result['calls']),18);self.assertEqual(result['qualified'],18);self.assertEqual({q['direction'] for q in result['queries']},{d['name'] for d in self.cfg['directions']});self.assertEqual({p['platform'] for p in result['items']},{'douyin','xhs'})
 def test_budget_exactly_eighty_and_fresh_process_cannot_reset_it(self):
  self.budget(79);r=self.run_round();self.assertEqual(len(r['calls']),1);self.assertEqual(r['status'],'budget_exhausted');self.assertEqual(len(M.read(M.storage(self.root)/'budget'/f'{NOW.date()}.json',{})['calls']),80)
  self.assertEqual(len(self.run_round()['calls']),0)
 def test_simultaneous_rounds_do_not_exceed_budget(self):
  self.budget(79);entered=threading.Event();release=threading.Event();results=[]
  def slow(*args):entered.set();self.assertTrue(release.wait(3));return self.transport(*args)
  t=threading.Thread(target=lambda:results.append(self.run_round(slow)));t.start()
  try:
   self.assertTrue(entered.wait(3));second=self.run_round();self.assertEqual(second['status'],'busy');self.assertEqual(second['calls'],[])
  finally:release.set();t.join(5)
  self.assertFalse(t.is_alive());self.assertEqual(len(results[0]['calls']),1);self.assertEqual(len(M.read(M.storage(self.root)/'budget'/f'{NOW.date()}.json',{})['calls']),80)
 def test_retry_reserved_before_send_and_counts_toward_cap(self):
  self.budget(79)
  def failing(*_):raise M.V.RadarError('http_503')
  r=self.run_round(failing);self.assertEqual(len(r['calls']),1);self.assertEqual(r['calls'][0]['error'],'http_503');self.assertEqual(r['status'],'budget_exhausted')
 def test_each_retry_counts(self):
  def failing(*_):raise M.V.RadarError('http_503')
  r=self.run_round(failing);self.assertEqual(len(r['calls']),36);self.assertEqual(r['status'],'failed')
 def test_402_400_timeout_are_errors_not_successful_zero(self):
  for code in ['http_402','http_400','request_timeout_or_transport']:
   with self.subTest(code=code):
    def failing(*_):raise M.V.RadarError(code)
    r=self.run_round(failing);self.assertIn(r['status'],['failed','budget_exhausted']);self.assertEqual(r['calls'][0]['error'],code);self.assertIsNotNone(M.status(self.root,self.reports)[M.CHANNEL]['error'])
 def test_verified_empty_and_rejected_content_are_successful_zero_receipts(self):
  r=self.run_round(lambda url,*args:response('xhs' if 'xiaohongshu' in url else 'douyin',[]));self.assertEqual(r['status'],'ok');self.assertEqual(r['qualified'],0);self.assertEqual(r['outcome'],'no_qualified_content');self.assertTrue(list((M.storage(self.root)/'rounds').glob('*/*.json')));self.assertIsNone(M.status(self.root,self.reports)[M.CHANNEL]['error'])
 def test_never_run_distinct_from_success_empty(self):self.assertEqual(M.status(self.root,self.reports)[M.CHANNEL]['status'],'waiting')
 def test_missing_config_is_error_and_receipt(self):
  self.path.unlink()
  with self.assertRaises(ValueError):self.run_round()
  self.assertEqual(M.read(M.storage(self.root)/'status.json',{})['status'],'config_error');self.assertIn('配置',M.status(self.root,self.reports)[M.CHANNEL]['error'])
 def test_mismatched_direction_is_error(self):
  self.cfg['directions'][0]['name']+='错误';self.save()
  with self.assertRaises(ValueError):self.run_round()
  self.assertEqual(M.read(M.storage(self.root)/'status.json',{})['status'],'config_error')
 def test_zero_keywords_is_error(self):
  self.cfg['directions'][0]['keywords']=[];self.save()
  with self.assertRaises(ValueError):self.run_round()
 def test_invalid_budget_over_eighty_rejected(self):
  self.cfg['daily_call_limit']=81;self.save()
  with self.assertRaises(ValueError):self.run_round()
 def test_closed_platform_does_not_request_it(self):
  self.cfg['platforms']['xhs']=False;self.save();r=self.run_round();self.assertEqual(len(r['calls']),9);self.assertEqual({c['platform'] for c in r['calls']},{'douyin'})
 def test_config_without_topics_does_not_use_default_directions(self):
  (self.reports/'interest-topics.md').unlink()
  with self.assertRaises(ValueError):self.run_round()
 def test_schema_and_wrong_outer_body_fail(self):
  for value in [{},{'code':200,'data':{}},{'code':200,'data':[{'status_code':1,'business_data':[]}]}]:
   with self.assertRaises(M.V.RadarError):M.response_rows(value,'douyin')
 def test_explicit_nested_paths_ignore_advertisements(self):
  data=response('douyin',[raw('douyin',BODIES[0])]);data['data'].insert(0,{'status_code':0,'business_data':[{'text':'广告首段','aweme_info':raw('douyin','错误字段位置')}]});rows=M.response_rows(data,'douyin');self.assertEqual(len(rows),1);self.assertEqual(rows[0]['desc'],BODIES[0])
 def test_not_video_not_new_news_opinion_promotion_rejected(self):
  for body in ['Claude Code 发布重大新闻','我认为 Claude Code 未来很好','Claude Code 教程，进群领取福利','Claude Code 月入十万教程']:
   item,reason=M.normalize(raw('douyin',body),'douyin',NOW,self.cfg);self.assertIsNone(item)
  old=raw('douyin',BODIES[4]);old['create_time']=int((NOW-timedelta(days=8)).timestamp());self.assertIsNone(M.normalize(old,'douyin',NOW,self.cfg)[0]);old=raw('douyin',BODIES[4]);old['media_type']=2;self.assertIsNone(M.normalize(old,'douyin',NOW,self.cfg)[0])
 def test_multi_missing_views_never_fabricated_zero(self):
  item,_=M.normalize(raw('douyin',BODIES[4]),'douyin',NOW,self.cfg);self.assertIsNone(item['metrics']['views']);self.assertEqual(item['metrics']['likes'],123)
 def test_two_directions_one_video_one_card_preserves_topics(self):
  def both(url,*args):return response('xhs' if 'xiaohongshu' in url else 'douyin',[raw('xhs' if 'xiaohongshu' in url else 'douyin','Claude Code MCP 安装教程 与 ComfyUI 工作流节点')])
  r=self.run_round(both);self.assertEqual(r['qualified'],2);self.assertTrue(all(len(p['interest_topics'])==2 for p in r['items']))
 def test_repeated_capture_preserves_first_arrival(self):
  first=self.run_round();second=self.run_round();self.assertEqual({p['id']:p['first_seen_at'] for p in first['items']},{p['id']:p['first_seen_at'] for p in second['items']})
 def test_secret_echo_never_reaches_status_or_snapshot(self):
  def echoed(url,*args):
   platform='xhs' if 'xiaohongshu' in url else 'douyin';return response(platform,[raw(platform,BODIES[4]+KEY)])
  self.run_round(echoed)
  for p in M.storage(self.root).rglob('*.json'):self.assertNotIn(KEY,p.read_text())
 def test_exception_with_secret_is_only_fixed_error_code(self):
  def reflected(*_):raise RuntimeError(KEY)
  r=self.run_round(reflected);self.assertEqual(r['calls'][0]['error'],'collection_failed');self.assertNotIn(KEY,json.dumps(r))
 def test_adapter_platform_channel_metrics_and_multi_topics(self):
  r=self.run_round();path=M.storage(self.root)/'page.json';parsed=S.parse_file(path,'interest_video');self.assertEqual(len(parsed),18);self.assertTrue(all(p['channels']==['aisa'] for p in parsed));self.assertEqual({p['platform'] for p in parsed},{'douyin','xhs'});self.assertEqual(datetime.fromisoformat(parsed[0]['first_seen_at']),NOW)
 def test_wide_and_direction_same_video_deduplicate(self):
  self.run_round();p=S.parse_file(M.storage(self.root)/'page.json','interest_video')[4];wide=copy.deepcopy(p);wide.update(input_sources=['抖音视频榜'],sources=['V']);joined=IM.deduplicate([p,wide]);self.assertEqual(len(joined),1);self.assertEqual(set(joined[0]['input_sources']),{'抖音兴趣搜索','抖音视频榜'})
 def test_pagination_counts_unique_videos(self):
  import today_view as T
  rows=[]
  for i in range(61):
   item,_=M.normalize(raw('douyin',BODIES[4],i),'douyin',NOW,self.cfg);item['interest_topics']=[self.cfg['directions'][4]['name']];rows.append(item)
  M.write(M.storage(self.root)/'page.json',{'items':rows})
  data=S.build(self.root,NOW,self.reports);cache=dict(ready=True,partial=False,refreshing=False,error=None,built_at=data['built_at'],build_ms=data['build_ms'])
  with patch.object(S,'candidates',return_value=(data,cache)):
   a=T.page_view(self.root,{'channel':['interest'],'page':['1']},NOW);b=T.page_view(self.root,{'channel':['interest'],'page':['2']},NOW)
  self.assertEqual(a['total'],61);self.assertEqual(b['total'],61);self.assertEqual(len(a['items']),50);self.assertEqual(len(b['items']),11);self.assertEqual(len({p['id'] for p in a['items']+b['items']}),61)
 def test_bare_deploy_or_explanation_not_actionable(self):
  for body in ['节省成本的本地ai服务器 #本地部署','大模型权重到底是什么，可以下载到自己电脑上运行','OpenCode / VoltAgent / Manus 谁能活下来，GitHub 框架观点','5060Ti 从安装到卸载，没有尝试的意义 #本地部署']:
   self.assertIsNone(M.normalize(raw('douyin',body),'douyin',NOW,self.cfg)[0])
 def test_corrupt_budget_fails_closed_without_transport(self):
  p=M.storage(self.root)/'budget'/f'{NOW.date()}.json';p.parent.mkdir(parents=True);p.write_text('{')
  with patch.object(M.V,'curl_json') as call:
   with self.assertRaises(ValueError):self.run_round(transport=call)
   call.assert_not_called()
 def test_corrupt_status_does_not_break_page(self):
  p=M.storage(self.root)/'status.json';p.parent.mkdir(parents=True);p.write_text('{')
  self.assertEqual(M.status(self.root,self.reports)[M.CHANNEL]['status'],'failed')
 def test_projection_filters_counts_and_first_arrival_across_midnight(self):
  import interest_projection as P,today_view as T
  r=self.run_round();bundle=self.root/'bundle.json';bundle.write_text('{"tweets":[],"vip_tweets":[]}')
  with patch.object(G,'BUNDLE_PATH',bundle):
   P.publish(self.root,NOW)
   listing=T.page_view(self.root,{'channel':['interest'],'range':['today']},NOW)
   self.assertEqual(listing['total'],18)
   for platform in ('douyin','xhs'):
    result=T.page_view(self.root,{'channel':['interest'],'range':['today'],'platform':[platform]},NOW)
    self.assertEqual(result['total'],9);self.assertTrue(all(p['platform']==platform for p in result['items']))
   result=T.page_view(self.root,{'channel':['interest'],'range':['today'],'topic':['visual-models']},NOW)
   self.assertEqual(result['total'],2)
   later=NOW+timedelta(days=1)
   M.run(self.root,reports=self.reports,key=KEY,clock=lambda:later,transport=self.transport)
   P.publish(self.root,later)
   result=T.page_view(self.root,{'channel':['interest'],'range':['today']},later)
   self.assertEqual(result['total'],0)
   self.assertEqual(T.page_view(self.root,{'channel':['interest'],'range':['7d']},later)['total'],18)
 def test_registers_two_daily_script_tasks_idempotently_without_collector_calls(self):
  # All DB writes stay in this temporary workspace; no live scheduler starts.
  first=M.register(self.root,self.reports,enabled=False);second=M.register(self.root,self.reports,enabled=False);self.assertEqual(len(first),2);self.assertEqual({p['at'] for p in first},{'10:20','22:20'});self.assertEqual([p['id'] for p in first],[p['id'] for p in second]);self.assertTrue(all(not p['enabled'] and p['kind']=='script' for p in first))
if __name__=='__main__':unittest.main()
