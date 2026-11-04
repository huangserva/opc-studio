"""真实原始响应6例、动作边界与无旧参数夹具的生产默认首发。"""
from pathlib import Path
from datetime import datetime
from unittest.mock import patch
import unittest,json,tempfile,os
from scripts import interest_video_search as M
from interest_match import classify,compile_topics
from topic_registry import topic_key
ROOT=Path(__file__).resolve().parents[1]
# Freeze the fix2 (0/0/0) request fixture for its three parameter-sensitive tests.
# Fix3Tests checks current production requests without this historical fixture.
FIX2_PRODUCTION_REQUEST_SPEC=M.request_spec
def fix2_legacy_request_spec(platform,word,default_filters=False):
 method,path,params=FIX2_PRODUCTION_REQUEST_SPEC(platform,word,default_filters)
 if platform=='douyin':
  params.update(sort_type='0',publish_time='0',content_type='0');params.pop('backtrace',None)
 return method,path,params
class Fix2Tests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory(prefix='interest-video-fix2-',dir='/tmp');self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name);(self.root/'scripts').mkdir();(self.root/'workspace').mkdir();self.reports=self.root/'reports';self.reports.mkdir();self.cfg=json.loads((ROOT/'scripts/interest_video_search_config.json').read_text());(self.root/'scripts/interest_video_search_config.json').write_text(json.dumps(self.cfg,ensure_ascii=False));(self.reports/'interest-topics.md').write_text('## 当前方向\n'+''.join('- '+d['name']+'\n' for d in self.cfg['directions']));self.at=datetime.fromisoformat('2026-11-04T12:43:42.283885+08:00');self.dirs=compile_topics([d['name'] for d in self.cfg['directions']]);self.enterContext(patch.dict(os.environ,{'GROK_BOT_REPORTS_DIR':str(self.reports)}))
  if self._testMethodName in {'test_production_douyin_primary_defaults','test_production_two_rounds_32_plus_32_without_legacy_fixture','test_400_on_default_first_retries_default_once'}:
   self.enterContext(patch.object(M,'request_spec',side_effect=fix2_legacy_request_spec))
 def admit(self,raw,platform,direction):
  p,reason=M.normalize(raw,platform,self.at,self.cfg)
  if p and not any(m['direction']==topic_key(direction) for m in classify(dict(p,source_topics=[],input_sources=['兴趣视频搜索']),self.dirs)):p=None
  return bool(p)
 def test_six_real_rows_normalize_and_classify(self):
  fixture=json.loads((ROOT/'tests/fixtures/interest_video_search/real_round_counterexamples.json').read_text())
  for row in fixture['rows']:
   with self.subTest(id=row['id']):self.assertEqual(self.admit(row['raw'],row['platform'],row['direction']),row['expected'])
 def test_original_eleven_snapshot_fields_normalize_and_classify(self):
  rows=json.loads((ROOT/'tests/fixtures/interest_video_search/legacy_snapshot_pipeline.json').read_text())
  for row in rows:
   with self.subTest(id=row['id']):self.assertEqual(self.admit(row['raw'],row['platform'],row['direction']),row['expected'])
 def test_resources_must_not_bridge_sentences_or_clauses(self):
  for text in ['分享AI生图、视频生成、语音合成等应用方向。写代码、做素材','分享模型应用。代码可以改变生活，模型都能本地部署','分享生活经验，代码工具很多。','讨论AI工作流，分享未来观点，脚本也很重要。','附一条介绍.写脚本的能力很有意思']:
   with self.subTest(text=text):self.assertFalse(M.resource_evidence(text));self.assertFalse(M.actionable(text))
 def test_explicit_resources_still_admitted(self):
  for text in ['附ComfyUI节点与工作流','附ComfyUI2.1工作流','来自 Github aicg3d 大佬开源插件，含人体姿态素材','这里提供ComfyUI打光工作流下载地址','模型脚本的仓库地址 https://github.com/example/tool']:
   with self.subTest(text=text):self.assertTrue(M.resource_evidence(text))
 def test_action_semantics_specific_operations(self):
  for text in ['教你用剪映PC批量成片','H3导演台分段生成，跟着操作走一遍','Claude Code实用技巧合集，含上下文设置','4步搭建电商视频生产线','打开OpenCode模型列表，找Free模型，拿实际任务试用','打开模型列表→找指定模型→试用']:
   with self.subTest(text=text):self.assertTrue(M.action_semantics(text));self.assertTrue(M.actionable(text))
 def test_generic_words_not_new_action_evidence(self):
  for text in ['分享视频生产线未来的行业观点','AI会写代码，现在什么都可以做','一条视频分享个人生活与观点','模型、代码与视频的发展前景','分享模型写脚本的发展趋势','介绍模型提供自动生成脚本的能力','分享AI写脚本、生成代码的能力，介绍本地部署方向']:
   with self.subTest(text=text):self.assertFalse(M.action_semantics(text));self.assertFalse(M.actionable(text))
 def test_hashtag_demonstrations_still_rejected(self):
  for text in ['本地部署一起织布呀 #ComfyUI #工作流','新手MiniMax H3旋转换装 #教程 #工作流','工作流更新通知 #ComfyUI #实用技巧','给你看看生成效果 #教你用剪映 #视频生产线']:
   with self.subTest(text=text):
    self.assertFalse(M.actionable(text))
    original=json.loads((ROOT/'tests/fixtures/interest_video_search/real_round_counterexamples.json').read_text())['rows'][1]['raw']
    raw=dict(original,desc=text)
    self.assertFalse(self.admit(raw,'douyin',self.cfg['directions'][2]['name']))
 def test_production_douyin_primary_defaults(self):
  for d in self.cfg['directions']:
   for word in d['keywords']:
    for fallback in [False,True]:
     method,path,p=M.request_spec('douyin',word,fallback);self.assertEqual(method,'POST');self.assertEqual(p['keyword'],word);self.assertEqual(p['sort_type'],'0');self.assertEqual(p['publish_time'],'0');self.assertEqual(p['content_type'],'0');self.assertNotIn('backtrace',p);self.assertEqual(p['cursor'],0)
 def send_empty(self,url,method,params,key,timeout):
  self.sent.append(None if params is None else dict(params))
  return {'code':200,'data':{'code':0,'success':True,'data':{'items':[]}}} if params is None else {'code':200,'data':[{'status_code':0,'business_data':[]}]}
 def collect(self,send):return M.run(self.root,reports=self.reports,key='synthetic-offline-only',clock=lambda:self.at,transport=send)
 def test_production_two_rounds_32_plus_32_without_legacy_fixture(self):
  self.sent=[];a=self.collect(self.send_empty);b=self.collect(self.send_empty);self.assertEqual(len(a['calls']),32);self.assertEqual(len(b['calls']),32);self.assertEqual(len(self.sent),64);self.assertTrue(all(p is None or p['sort_type']=='0' for p in self.sent));self.assertEqual(a['fallback_count'],0)
 def test_400_on_default_first_retries_default_once(self):
  self.sent=[];seen=set()
  def send(url,method,params,key,timeout):
   if params is not None and params['keyword'] not in seen:
    seen.add(params['keyword']);self.assertEqual(params['sort_type'],'0');self.sent.append(dict(params));raise M.V.RadarError('http_400')
   return self.send_empty(url,method,params,key,timeout)
  a=self.collect(send);self.assertEqual(len(a['calls']),48);self.assertEqual(a['fallback_count'],16);self.assertEqual(a['status'],'ok');self.assertTrue(all(p is None or p['sort_type']=='0' for p in self.sent))
 def test_400_twice_stops_each_query_after_two(self):
  self.sent=[]
  def send(url,method,params,key,timeout):
   if params is not None:self.sent.append(dict(params));raise M.V.RadarError('http_400')
   return self.send_empty(url,method,params,key,timeout)
  a=self.collect(send);self.assertEqual(len(a['calls']),48);self.assertEqual(len(self.sent),48);self.assertEqual(a['status'],'partial');self.assertTrue(all(q['fallback']['status']=='failed' for q in a['queries'] if q['platform']=='douyin'))
 def test_direction_aliases_are_explicit_content_automation(self):
  for text in ['4步搭建电商视频生产线，先整理素材，再匹配镜头','教你用剪映PC批量成片']:
   p={'title':text,'full_text':text,'kind':'video','source_topics':[]};matches=classify(p,self.dirs);self.assertIn('content-automation',{m['direction'] for m in matches})
 def test_unity_mcp_query_can_match_game_agent_direction(self):
  p={'title':'Unity MCP 游戏开发教程','full_text':'教你用 Unity MCP 配置游戏开发工作流','kind':'video','source_topics':[]}
  self.assertIn('embodied-3d',{m['direction'] for m in classify(p,self.dirs)})
 def test_two_word_replacements_keep_sixteen_words_and_names(self):
  self.assertEqual(sum(len(d['keywords']) for d in self.cfg['directions']),16);self.assertEqual(self.cfg['directions'][5]['keywords'][0],'Agent Skill 安全扫描');self.assertEqual(self.cfg['directions'][8]['keywords'][1],'Unity MCP 游戏开发')
if __name__=='__main__':unittest.main()
