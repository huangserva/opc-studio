"""返修1：全部transport为内存模拟；不读Key，不联网，不注册任务。"""
from pathlib import Path
from datetime import timedelta
import json,unittest
from unittest.mock import patch
import test_interest_video_search as Old
from scripts import interest_video_search as M
ROOT=Path(__file__).resolve().parents[1]
class FixTests(unittest.TestCase):
 setUp=Old.SearchTests.setUp
 save=Old.SearchTests.save
 transport=Old.SearchTests.transport
 run_round=Old.SearchTests.run_round
 budget=Old.SearchTests.budget
 def production(self):
  self.cfg=json.loads((ROOT/'scripts/interest_video_search_config.json').read_text());self.save()
 def one_query(self):
  for d in self.cfg['directions']:d['keywords']=[d['keywords'][0]]
  self.cfg['platforms']['xhs']=False;self.save()
 def check_bad_budget(self,value,code,raw=False):
  target=M.storage(self.root)/'budget'/f'{Old.NOW.date()}.json';target.parent.mkdir(parents=True)
  original=value if raw else json.dumps(value);target.write_text(original)
  with patch.object(M.V,'curl_json') as send:
   with self.assertRaises(M.BudgetError):self.run_round(transport=send)
   send.assert_not_called()
  self.assertEqual(target.read_text(),original)
  state=M.read(M.storage(self.root)/'status.json',{})
  self.assertEqual(state['status'],'failed');self.assertEqual(state['error'],code)
  self.assertEqual(M.status(self.root,self.reports)[M.CHANNEL]['error'],code)
 def test_missing_day_no_requests(self):self.check_bad_budget({'calls':[]},'budget_invalid_structure')
 def test_wrong_day_no_requests(self):self.check_bad_budget({'day':'1900-01-01','calls':[]},'budget_invalid_day')
 def test_bad_json_no_requests(self):self.check_bad_budget('{','budget_invalid_json',True)
 def test_negative_count_no_requests(self):self.check_bad_budget({'day':str(Old.NOW.date()),'calls':-1},'budget_invalid_calls')
 def test_over_eighty_no_requests(self):self.check_bad_budget({'day':str(Old.NOW.date()),'calls':[{'status':'reserved'}]*81},'budget_over_limit')
 def test_wrong_calls_type_no_requests(self):self.check_bad_budget({'day':str(Old.NOW.date()),'calls':{}},'budget_invalid_calls')
 def test_wrong_entry_no_requests(self):self.check_bad_budget({'day':str(Old.NOW.date()),'calls':[-1]},'budget_invalid_entry')
 def test_wrong_top_level_no_requests(self):self.check_bad_budget([],'budget_invalid_structure')
 def test_negative_extra_counter_no_requests(self):self.check_bad_budget({'day':str(Old.NOW.date()),'calls':[],'count':-1},'budget_invalid_structure')
 def test_new_day_creates_separate_ledger_without_touching_yesterday(self):
  self.budget(80);target=M.storage(self.root)/'budget'/f'{Old.NOW.date()}.json';before=target.read_bytes()
  later=Old.NOW+timedelta(days=1)
  r=M.run(self.root,reports=self.reports,key=Old.KEY,clock=lambda:later,transport=self.transport)
  self.assertEqual(len(r['calls']),18);self.assertEqual(target.read_bytes(),before)
  new=M.read(target.with_name(str(later.date())+'.json'),{});self.assertEqual(new['day'],str(later.date()));self.assertEqual(len(new['calls']),18)
 def test_review_eleven_cards_seven_admit_three_reject_one_pending(self):
  cards=json.loads((ROOT/'tests/fixtures/interest_video_search/review_cards.json').read_text());counts={'admit':0,'reject':0,'pending':0}
  for card in cards:
   with self.subTest(id=card['id']):
    counts[card['verdict']]+=1;expected=card['verdict']=='admit';self.assertEqual(M.actionable(card['text']),expected)
    normalized,reason=M.normalize(Old.raw(card['platform'],card['text']),card['platform'],Old.NOW,self.cfg)
    self.assertEqual(normalized is not None,expected)
    if not expected:self.assertEqual(reason,'not_actionable')
  self.assertEqual(counts,{'admit':7,'reject':3,'pending':1})
 def test_hashtags_cannot_supply_action_gate(self):
  for body in ['本地部署一起织布呀 #ComfyUI #工作流','新手模型旋转换装 #ComfyUI #教程 #下载','11月工作流更新通知 #ComfyUI #教程','只展示一个生成效果 #教程 #github.com/example','#ComfyUI #工作流 #教程']:
   with self.subTest(body=body):self.assertFalse(M.actionable(body))
 def test_action_resources_and_steps_without_title_blacklist(self):
  for body in ['ComfyUI 安装卡67%操作教程','新版本更新，附ComfyUI工作流下载资源','模型打光任务，仓库 https://github.com/example/workflow','ComfyUI 设置第一步加载模型，第二步调节采样器']:
   with self.subTest(body=body):self.assertTrue(M.actionable(body))
 def test_old_rejected_cards_pruned_from_retained_snapshot(self):
  self.run_round();p=M.storage(self.root)/'page.json';page=M.read(p,{})
  page['items'][0]['full_text']='本地部署一起织布呀 #ComfyUI #工作流';bad=page['items'][0]['id'];M.write(p,page)
  r=self.run_round(lambda url,*_:Old.response('xhs' if 'xiaohongshu' in url else 'douyin',[]))
  self.assertEqual(r['qualified'],0);self.assertNotIn(bad,{p['id'] for p in M.read(p,{})['items']})
 def test_400_to_default_200_each_query_two_calls(self):
  self.one_query();calls=[]
  def send(url,method,params,*args):
   calls.append(dict(params))
   if params['sort_type']=='2':raise M.V.RadarError('http_400')
   return self.transport(url,method,params,*args)
  r=self.run_round(send);self.assertEqual(len(calls),18);self.assertEqual(r['status'],'ok');self.assertEqual(r['fallback_count'],9)
  for q in r['queries']:self.assertEqual(q['fallback']['status'],'ok');self.assertNotIn('error',q)
  for a,b in zip(calls[::2],calls[1::2]):
   self.assertEqual(a['keyword'],b['keyword']);self.assertEqual(b['sort_type'],'0');self.assertEqual(b['publish_time'],'0');self.assertEqual(b['content_type'],'0');self.assertEqual(b['cursor'],0);self.assertNotIn('backtrace',b)
  self.assertIn('默认筛选',M.status(self.root,self.reports)[M.CHANNEL]['note'])
 def test_400_to_400_stops_after_one_fallback(self):
  self.one_query();calls=[]
  def send(url,method,params,*args):calls.append(dict(params));raise M.V.RadarError('http_400')
  r=self.run_round(send);self.assertEqual(len(calls),18);self.assertEqual(r['status'],'failed');self.assertTrue(all(q['fallback']['status']=='failed' for q in r['queries']))
 def test_failed_default_fallback_is_not_retried_on_503(self):
  self.one_query()
  def send(url,method,params,*args):raise M.V.RadarError('http_503' if params['sort_type']=='0' else 'http_400')
  r=self.run_round(send);self.assertEqual(len(r['calls']),18);self.assertTrue(all(q['error']=='http_503' for q in r['queries']))
 def test_xhs_400_has_no_default_filter_retry(self):
  self.cfg['platforms']['douyin']=False;self.save()
  def send(*_):raise M.V.RadarError('http_400')
  r=self.run_round(send);self.assertEqual(len(r['calls']),9);self.assertEqual(r['fallback_count'],0)
 def test_400_fallback_obeys_remaining_budget(self):
  self.budget(79)
  def send(*_):raise M.V.RadarError('http_400')
  r=self.run_round(send);self.assertEqual(len(r['calls']),1);self.assertEqual(r['status'],'budget_exhausted');self.assertTrue(r['skipped_queries'])
 def test_default_filters_do_not_bypass_local_recency_or_quality(self):
  self.one_query()
  def send(url,method,params,*args):
   if params['sort_type']=='2':raise M.V.RadarError('http_400')
   row=Old.raw('douyin',Old.BODIES[4]);row['create_time']=int((Old.NOW-timedelta(days=30)).timestamp());return Old.response('douyin',[row])
  r=self.run_round(send);self.assertEqual(len(r['calls']),18);self.assertEqual(r['qualified'],0);self.assertTrue(all(q['rejected']=={'not_new_or_time_unknown':1} for q in r['queries']))
 def test_production_sixteen_words_exact_names_and_two_priority_directions(self):
  self.production();cfg=M.config(self.root,self.reports);self.assertEqual(sum(len(d['keywords']) for d in cfg['directions']),16)
  from interest_channel import topics
  self.assertEqual([d['name'] for d in cfg['directions']],topics(self.reports)['topics'])
  self.assertEqual([d['keywords'] for d in M.ordered_directions(cfg)[:2]],[['大模型本地部署'],['ComfyUI 工作流']])
  self.assertIn('智能体编程教程',cfg['directions'][4]['keywords']);self.assertRegex(cfg['directions'][4]['keywords'][1],r'^[\u4e00-\u9fff]+$')
 def test_normal_two_rounds_sixty_four_calls(self):
  self.production();a=self.run_round();b=self.run_round();self.assertEqual(a['planned_queries'],32);self.assertEqual(len(a['calls']),32);self.assertEqual(len(b['calls']),32);self.assertEqual(a['status'],'ok');self.assertEqual(b['status'],'ok')
  day=M.read(M.storage(self.root)/'budget'/f'{Old.NOW.date()}.json',{});self.assertEqual(len(day['calls']),64)
 def test_sixty_four_plus_fallbacks_stops_at_eighty_with_priority_log(self):
  self.production()
  def send(url,method,params,*args):
   if params is not None and params['sort_type']=='2':raise M.V.RadarError('http_400')
   return self.transport(url,method,params,*args)
  a=self.run_round(send);b=self.run_round(send);self.assertEqual(len(a['calls']),48);self.assertEqual(len(b['calls']),32);self.assertEqual(b['status'],'budget_exhausted')
  self.assertEqual([q['keyword'] for q in a['queries'][:4]],['大模型本地部署','大模型本地部署','ComfyUI 工作流','ComfyUI 工作流']);self.assertEqual([q['keyword'] for q in b['queries'][:4]],['大模型本地部署','大模型本地部署','ComfyUI 工作流','ComfyUI 工作流'])
  self.assertTrue(b['skipped_queries']);self.assertTrue(all(q['reason']=='budget_exhausted' for q in b['skipped_queries']));self.assertEqual(len(self.run_round(send)['calls']),0)
if __name__=='__main__':unittest.main()
