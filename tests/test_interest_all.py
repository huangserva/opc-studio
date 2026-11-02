"""跨来源只读投影的实质反例；所有写入仅位于独立 /tmp 夹具。"""
from contextlib import closing
from datetime import datetime,timedelta,timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import grok_bot_radar as G
import interest_channel as I
import interest_match as M
import interest_sources as S
import today_view as T
from clue_store_v2 import COLUMNS

NOW=datetime.now(timezone.utc)
TOPICS=list(I.DEFAULT_TOPICS)+['Agent Skill 安全扫描（SkillSpector 一类）','OpenCode / 本地 Agent 桌面插件生态','AI 内容生产自动化（选题雷达 / 剪映 / 批量出片）','3D / 游戏 / 硬件 Agent 落地']

def write(path,value):
 path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(value,ensure_ascii=False))

def fixture(base):
 base=Path(base);root=base/'workspace';root.mkdir(exist_ok=True);reports=base/'reports';reports.mkdir(exist_ok=True)
 (reports/'interest').mkdir(exist_ok=True)
 (reports/'interest-topics.md').write_text('# 兴趣\n\n## 当前方向\n'+''.join('- '+t+'\n' for t in TOPICS)+'\n## 说明\n只读夹具\n')
 stamp=(NOW-timedelta(minutes=1)).isoformat();day=NOW.astimezone(G.BEIJING).date().isoformat()
 row=dict(id='303',url='https://x.com/fixture/status/303',handle='fixture',author='作者',created_at=stamp,full_text='Claude Code 与 MCP 编程工具的实际使用教程，保留完整文本。',text='Claude Code 完整正文',topic='claude_code',outline=['正确的提纲条目'],content_complete=True)
 write(reports/'daily'/(day+'.json'),dict(collected_at=stamp,posts=[dict(row,id='999',url='https://x.com/fixture/status/999',outline=['第一条诱饵']),row]))
 write(reports/'alerts'/(day+'-1200.json'),dict(collected_at=stamp,entries=[dict(row,id='404',url='https://x.com/fixture/status/404',title_zh='ComfyUI 工作流教程',full_text='ComfyUI 工作流教程和 LoRA 训练参数，包含完整可执行步骤。')]))
 write(reports/'bookmarks'/(day+'.json'),dict(exported_at=stamp,items=[dict(row,author_handle='fixture',suggested_topic=TOPICS[4])]))
 write(root/'twitter-radar/snapshots'/day/'run.json',dict(finished_at=stamp,target_count=51,tweets=[dict(tweet_id='303',url=row['url'],text='这个原文版本没有任何方向专名；同一帖子由其他来源补充了主题。',author_handle='fixture',published_at=stamp,fetched_at=stamp,is_stale=False),dict(tweet_id='555',url='https://x.com/fixture/status/555',text='只有 Agent 和 模型 泛词，没有有效方向专名。',fetched_at=stamp)]))
 write(root/'video-radar/page.json',dict(schema='video-radar-page/v1',generated_at=stamp,sources={},lists=dict(douyin_daily=[dict(id='77000000000000001',key='douyin:77000000000000001',platform='douyin',title='ComfyUI 模型工作流教程',url='https://www.douyin.com/video/77000000000000001',last_seen_at=stamp,published_at=stamp,author='视频作者')],xiaohongshu=[dict(id='6adc1402000000002403fa66',platform='xiaohongshu',title='LoRA 训练教程',desc_excerpt='LoRA 模型训练步骤与参数，完整教程说明',url='https://www.xiaohongshu.com/explore/6adc1402000000002403fa66',last_seen_at=stamp,published_at=stamp)])))
 write(root/'linuxdo/top-daily.json',dict(updated_at=stamp,items=[dict(id='url:legacy',kind='topic',topic_id=123,title='Codex CLI 完整教程',full_text='Codex 的 CLI 参数教程，附完整设置说明。',url='https://linux.do/t/tool/123',fetched_at=stamp,published_at=stamp,sources=['L'],author=dict(name='作者',handle=''),metrics={},tier='讨论',fit_score=None,doable=None,why='',pattern='',my_angle='',outline='',doable_resource='',rich_sources=[],content_complete=True,status='new',feedback=None)]))
 write(base/'bundle.json',dict(tweets=[],vip_tweets=[]))
 dbpath=root/'clues/clues.db';dbpath.parent.mkdir()
 with closing(sqlite3.connect(dbpath)) as db,db:
  for name,columns in COLUMNS.items():db.execute('CREATE TABLE '+name+'(id TEXT PRIMARY KEY,batch_id TEXT,'+columns+')')
  db.executescript('CREATE TABLE threads(id,data,created_at,updated_at);CREATE TABLE entries(id,thread_id,data);')
  db.execute('INSERT INTO threads VALUES(?,?,?,?)',('t1',json.dumps(dict(title='MCP Agent 工具的新技术线索',summary='已提炼的 MCP 工具使用摘要，不含聊天原文。')),stamp,stamp))
  db.execute('INSERT INTO entries VALUES(?,?,?)',('e1','t1',json.dumps(dict(evidence=[dict(kind='wechat',document_id='private-document',excerpt='PRIVATE_CHAT_SENTINEL_NOT_FOR_INTEREST',url='')]))))
  db.execute("INSERT INTO clue_objects(id,canonical_key,platform,kind,title,summary,first_seen_at,last_seen_at,content_complete) VALUES('c1','legacy:t1','web','legacy_thread','私密引用','',?,?,0)",(stamp,stamp))
  db.execute("INSERT INTO legacy_object_links(id,thread_id,clue_id,mapping_reason,mapping_state,legacy_state_json) VALUES('l1','t1','c1','legacy','mapped','{}')")
  db.execute("INSERT INTO clue_channels(id,clue_id,channel,source,source_id,first_seen_at,last_seen_at,metadata_json) VALUES('ch1','c1','wechat:clues','legacy','t1',?,?,'{}')",(stamp,stamp))
  db.execute("INSERT INTO clue_objects(id,canonical_key,platform,kind,title,summary,first_seen_at,last_seen_at,content_complete) VALUES('c2','x:303','x','post','公开帖子','',?,?,1)",(stamp,stamp))
  db.execute("INSERT INTO clue_aliases(id,alias,clue_id,kind) VALUES('a1','https://x.com/fixture/status/303','c2','source')")
  db.execute("INSERT INTO clue_channels(id,clue_id,channel,source,source_id,first_seen_at,last_seen_at,metadata_json) VALUES('ch2','c2','grok:daily','daily','303',?,?,'{}')",(stamp,stamp))
 path=root/'knowledge/knowledge.db';path.parent.mkdir()
 with closing(sqlite3.connect(path)) as db,db:
  db.executescript('CREATE TABLE documents(id,source,kind,title,body,url,modified,tags,provenance);CREATE TABLE document_trash(document_id);CREATE TABLE sources(id,config);')
  db.execute("INSERT INTO sources VALUES('gh','{\"enabled\":true}')")
  for id,source,body in [('ext','external-references','Codex 的外部收藏原文，比其他版本更长，具体使用步骤与完整教程都在这里。'),('trash','external-references','Codex 已删除内容'),('project','projects','Codex 项目源码 PRIVATE_PROJECT_SENTINEL')]:
   db.execute('INSERT INTO documents VALUES(?,?,?,?,?,?,?,?,?)',(id,source,'external','外部收藏 '+id,body,row['url'] if id=='ext' else 'https://example.test/'+id,stamp,'[]','{}'))
  db.execute("INSERT INTO document_trash VALUES('trash')")
 path=root/'github/github.db';path.parent.mkdir()
 with closing(sqlite3.connect(path)) as db,db:
  db.execute('CREATE TABLE items(source,key,data)');db.execute('INSERT INTO items VALUES(?,?,?)',('gh','issue1',json.dumps(dict(url='https://github.com/acme/codex/issues/1',title='Codex MCP 兼容性修复',body='Codex MCP 兼容性修复的详细过程。',modified=stamp))))
 path=root/'intel/intel.db';path.parent.mkdir()
 with closing(sqlite3.connect(path)) as db,db:
  db.execute('CREATE TABLE articles(id,title,body,link,name,published_at,added_at)');db.execute('INSERT INTO articles VALUES(?,?,?,?,?,?,?)',('mp1','公众号 MCP 接入教程','MCP 接入的完整教程，已经采集到本地正文。','https://mp.weixin.qq.com/s/local-fixture','公众号作者',int(NOW.timestamp()),stamp))
 path=root/'aisa/aisa.db';path.parent.mkdir()
 with closing(sqlite3.connect(path)) as db,db:
  db.execute('CREATE TABLE items(platform,item_id,captured_at,payload)');db.execute('INSERT INTO items VALUES(?,?,?,?)',('twitter','606',stamp,json.dumps(dict(tweet_id='606',url='https://x.com/fixture/status/606',text='MCP 的手动读取候选，包含完整教程。'))))
 return root,reports

class CrossSourceTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory(prefix='interest-all-unit-',dir='/tmp');self.addCleanup(self.tmp.cleanup)
  self.root,self.reports=fixture(self.tmp.name)
  for p in [patch.dict(os.environ,{'GROK_BOT_REPORTS_DIR':str(self.reports)}),patch.object(G,'BUNDLE_PATH',Path(self.tmp.name)/'bundle.json')]:p.start();self.addCleanup(p.stop)
  self.key=(str(self.root.resolve()),str(self.reports.resolve()));self.addCleanup(self.join)
 def join(self):
  state=S.STATES.get(self.key,{})
  if state.get('worker'):state['worker'].join(5)
 def ready(self):
  S.candidates(self.root,NOW);self.join()
  return S.candidates(self.root,NOW)
 def listing(self,**query):
  self.ready();return T.page_view(self.root,dict(channel=['interest'],range=['7d'],**{k:[v] for k,v in query.items()}),NOW)
 def test_rss_subscription_ads_need_technical_content_in_title(self):
  directions=M.compile_topics(TOPICS)
  for title in ['API充值1:1，进群送5刀','0.68/0.75的ccmax与codex套餐','分组价格下调，评论抽额度']:
   self.assertEqual(M.classify(dict(title=title,full_text='Claude Code Codex',input_sources=['linux.do']),directions),[])
  self.assertTrue(M.classify(dict(title='Codex API网关部署教程，兼谈充值问题',full_text='Codex部署步骤',input_sources=['linux.do']),directions))
 def test_rss_public_archive_survives_snapshot_rotation(self):
  stamp=(NOW-timedelta(hours=10)).isoformat()
  with closing(sqlite3.connect(self.root/'clues/clues.db')) as db,db:
   db.execute("INSERT INTO clue_objects(id,canonical_key,platform,kind,title,summary,first_seen_at,last_seen_at,content_complete) VALUES('rss-old','linuxdo:456','linuxdo','topic','Codex 旧RSS教程','',?,?,1)",(stamp,stamp))
   db.execute("INSERT INTO clue_channels(id,clue_id,channel,source,source_id,first_seen_at,last_seen_at,metadata_json) VALUES('rss-ch','rss-old','linuxdo:rss','linuxdo','456',?,?,'{}')",(stamp,stamp))
   db.execute("INSERT INTO clue_aliases(id,alias,clue_id,kind) VALUES('rss-alias','https://linux.do/t/archive/456','rss-old','source')")
   db.execute("INSERT INTO clue_content_versions(id,clue_id,channel,content_hash,body,rich_json,content_complete,origin_receipt_id,privacy_scope) VALUES('rss-version','rss-old','linuxdo:rss','hash','Codex MCP 归档教程全文，不依赖已轮换的两个RSS快照','{}',1,'receipt','public')")
  result=self.listing();old=next(p for p in result['items'] if p['id']=='linuxdo:456');self.assertEqual(old['clue_id'],'rss-old');self.assertEqual(old['channels'],['rss'])
  detail=T.item_view(self.root,dict(channel=['interest'],id=['linuxdo:456']));self.assertIn('归档教程全文',detail['full_text'])
 def test_questions_keep_cluster_identity_and_xboard_requires_event_identity(self):
  day=NOW.astimezone(G.BEIJING).date().isoformat();stamp=NOW.isoformat()
  write(self.reports/'questions'/(day+'.json'),dict(generated_at=stamp,items=[dict(cluster_id='q'+str(i),parent_post_url='https://x.com/fixture/status/303',question='MCP 如何使用 '+str(i),frequency=1,replies=[]) for i in (1,2)]))
  write(self.reports/'weekly'/(day+'.json'),dict(generated_at=stamp,posts=[dict(id='808',url='https://x.com/fixture/status/808',full_text='Codex MCP 使用教程',topic=TOPICS[4])]))
  path=self.root/'xradar/xradar.db';path.parent.mkdir()
  with closing(sqlite3.connect(path)) as db,db:
   db.execute('CREATE TABLE items(day,board,key,data,clue_id)')
   for key,clue in [('published','t1'),('unpublished',None)]:db.execute('INSERT INTO items VALUES(?,?,?,?,?)',(day,'topics',key,json.dumps(dict(title='MCP 教程 '+key,url='https://xbangdan.com/words/',summary='MCP 插件用法')),clue))
  data,_=self.ready();questions=[p for p in data['items'] if p['kind']=='question_cluster'];self.assertEqual({p['canonical_key'] for p in questions},{'question:q1','question:q2'});self.assertTrue(all(p['clue_id'] is None for p in questions))
  self.assertTrue(all(p['channels']==['manual'] for p in T.annotate_items(self.root,questions)))
  board=[p for p in data['items'] if 'X 榜单' in p['input_sources']];self.assertEqual(len(board),1);self.assertEqual(board[0]['canonical_key'],'legacy:t1');self.assertEqual(board[0]['channels'],['wechat','rss']);self.assertIn('X榜单主题缺少已发表线索身份',data['errors'])
  self.assertTrue(any('Grok 周报' in p['input_sources'] for p in data['items']))
 def test_one_bad_source_database_keeps_other_database_sources(self):
  path=self.root/'intel/intel.db';path.write_bytes(b'not a database')
  data,_=self.ready();sources={s for p in data['items'] for s in p['input_sources']}
  self.assertTrue({'GitHub API','AIsa 手动取数','微信线索摘要'}<=sources);self.assertIn('database:intel',data['errors'])
 def test_aisa_summary_needs_concrete_technical_evidence(self):
  directions=M.compile_topics(TOPICS)
  row=dict(title='Codex 额度今天重置',full_text='今天 Codex 重置，大家去用吧',input_sources=['AIsa 线索摘要'])
  self.assertEqual(M.classify(row,directions),[])
  self.assertTrue(M.classify(dict(row,full_text='Claude Code 通过 Webhook 发送任务，附接入教程'),directions))
  self.assertTrue(M.classify(dict(row,source_topics=[TOPICS[4]]),directions))
 def test_all_actual_read_adapters_and_source_boundaries(self):
  result=self.listing();sources={s for p in result['items'] for s in p['input_sources']}
  self.assertTrue({'Grok 日报','Grok 快报','X 书签','AIsa X','抖音视频榜','小红书灵感榜','linux.do','微信线索摘要','外部文章收藏','GitHub API','公众号文章','AIsa 手动取数'}<=sources)
  serialized=json.dumps(result,ensure_ascii=False);self.assertNotIn('PRIVATE_CHAT_SENTINEL',serialized);self.assertNotIn('PRIVATE_PROJECT_SENTINEL',serialized);self.assertNotIn('已删除内容',serialized)
  self.assertNotIn('x:555',{p['id'] for p in result['items']})
 def test_canonical_dedup_sources_union_and_correct_second_row(self):
  result=self.listing();same=[p for p in result['items'] if p['canonical_key']=='x:303'];self.assertEqual(len(same),1)
  self.assertTrue({'Grok 日报','AIsa X','X 书签','外部文章收藏'}<=set(same[0]['input_sources']))
  self.assertNotIn('x:303',{p['id'] for p in result['filtering']['items']})
  detail=T.item_view(self.root,dict(channel=['interest'],id=[same[0]['id']]))
  self.assertEqual(detail['outline'],['正确的提纲条目']);self.assertEqual(detail['clue_id'],'c2');self.assertEqual(detail['identity_state'],'k1')
 def test_platform_channels_directions_counts_and_filters(self):
  all_rows=self.listing()['items']
  for query in [dict(platform='douyin'),dict(platform='X,douyin'),dict(collection_channel='grok'),dict(platform='x',collection_channel='aisa'),dict(topic='claude_code')]:
   result=self.listing(**query)
   expected=[p for p in all_rows if (not query.get('platform') or p['platform'] in query['platform'].lower().split(',')) and (not query.get('collection_channel') or query['collection_channel'] in p['channels']) and (not query.get('topic') or any(m['direction']=='agent-coding' for m in p['interest_matches']))]
   self.assertEqual(sum(result['daily_counts'].values()),result['total'])
   self.assertEqual(result['total'],len(expected));self.assertEqual({p['id'] for p in result['items']},{p['id'] for p in expected})
 def test_readonly_all_sources_unchanged_and_authorizer(self):
  paths=[p for p in Path(self.tmp.name).rglob('*') if p.is_file()];before={p:hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
  self.listing();self.join()
  self.assertEqual(before,{p:hashlib.sha256(p.read_bytes()).hexdigest() for p in paths})
  with S.readonly(self.root/'clues/clues.db') as db:
   with self.assertRaises(sqlite3.DatabaseError):db.execute("DELETE FROM clue_objects")
   self.assertEqual(db.total_changes,0)
 def test_tool_summary_inherits_existing_read_model_fields(self):
  tool=S.item(dict(id='tool:inherited',url='https://example.test/tool',topic='claude_code',full_text='MCP 工具的完整说明和具体用法。',content_complete=True),'微信群工具','W',NOW.isoformat(),kind='tool')
  tool.update(in_tools=True,share_count=3,verification=dict(state='checked',http_status=200),direction_state='pending_confirmation',classification='tool',tool_version='v1',first_shared_at=NOW.isoformat())
  with patch('wechat_tools_view.cards',return_value=[tool]):
   result=self.listing();row=next(p for p in result['items'] if p['id']=='tool:inherited')
  self.assertTrue(row['in_tools']);self.assertEqual(row['share_count'],3);self.assertEqual(row['verification']['state'],'checked')
 def test_old_article_newly_hydrated_body_uses_arrival_time(self):
  with closing(sqlite3.connect(self.root/'intel/intel.db')) as db,db:
   db.execute('ALTER TABLE articles ADD COLUMN body_at');db.execute('UPDATE articles SET added_at=?,body_at=?',((NOW-timedelta(days=9)).isoformat(),(NOW-timedelta(minutes=1)).isoformat()))
  self.assertTrue(any('公众号文章' in p['input_sources'] for p in self.listing()['items']))
 def test_non_grok_feedback_does_not_write_grok_mirrors(self):
  self.listing();I.save_feedback(self.root,dict(id='linuxdo:123',channel='interest',action='accurate'))
  self.assertFalse((self.reports/'feedback/studio-feedback.json').exists());self.assertFalse(G.feedback_path(self.root).exists())
 def test_corrupt_feedback_and_busy_tool_store_do_not_block_browsing(self):
  self.ready();path=T.feedback_path(self.root);path.parent.mkdir(exist_ok=True);path.write_text('{bad')
  with patch('wechat_tools_view.cards',side_effect=sqlite3.OperationalError('locked')):
   result=T.page_view(self.root,dict(channel=['interest'],range=['7d']),NOW)
  self.assertGreater(result['total'],0);self.assertTrue(result['sources']['W']['error']);self.assertTrue(result['sources']['反馈']['error']);self.assertEqual(path.read_text(),'{bad')
 def test_tools_read_lock_wait_is_bounded(self):
  from wechat_tools_view import cards
  with closing(sqlite3.connect(self.root/'clues/clues.db')) as db:
   db.execute('BEGIN EXCLUSIVE');started=time.perf_counter()
   with self.assertRaises(sqlite3.DatabaseError):cards(self.root)
   self.assertLess(time.perf_counter()-started,.5);db.rollback()
 def test_missing_bad_json_version_and_schema_cache(self):
  for content in (None,'{bad',json.dumps(dict(schema=999,items=[])),json.dumps(dict(schema=S.VERSION,ready=True,items=[{}],built_at=NOW.isoformat(),build_ms=1))):
   self.join();S.STATES.pop(self.key,None);p=S.cache_path(self.root)
   if content is None:
    if p.exists():p.unlink()
   else:p.parent.mkdir(exist_ok=True);p.write_text(content)
   with self.assertLogs('interest_sources',level='WARNING') as log:data,status=S.candidates(self.root,NOW)
   self.assertFalse(status['ready']);self.assertTrue(status['partial']);self.assertTrue(status['refreshing']);self.assertIn('missing_or_bad',str(log.output));self.join()
   self.assertTrue(S.candidates(self.root,NOW)[1]['ready'])
 def test_source_change_invalidates_background_and_updates(self):
  data,_=self.ready();old=len(data['items']);p=self.root/'twitter-radar/snapshots'/NOW.astimezone(G.BEIJING).date().isoformat()/'run.json';v=json.loads(p.read_text());v['tweets'].append(dict(tweet_id='707',url='https://x.com/f/status/707',text='Codex 新版真实教程',fetched_at=(NOW-timedelta(seconds=1)).isoformat()));write(p,v)
  S.STATES[self.key]['checked']=0;_,status=S.candidates(self.root,NOW);self.assertTrue(status['refreshing']);self.join()
  data,status=S.candidates(self.root,NOW);self.assertTrue(status['ready']);self.assertGreater(len(data['items']),old)
 def test_signature_is_independent_of_path_alias(self):
  self.assertEqual(S.signature(self.root,NOW),S.signature(self.root/'..'/'workspace',NOW))
 def test_rebuild_never_blocks_existing_result(self):
  self.ready();gate=threading.Event();started=threading.Event();original=S.build
  def slow(*args,**kwargs):
   if not kwargs.get('light'):started.set();gate.wait(3)
   return original(*args,**kwargs)
  with patch.object(S,'build',side_effect=slow):
   S.STATES[self.key]['data']['signature']='old'
   try:
    S.candidates(self.root,NOW);self.assertTrue(started.wait(1));t=time.perf_counter();_,state=S.candidates(self.root,NOW);self.assertLess(time.perf_counter()-t,.1);self.assertTrue(state['refreshing'])
   finally:gate.set();self.join()
 def test_setting_change_applies_without_restart(self):
  self.listing();(self.reports/'interest-topics.md').write_text('## 当前方向\n- 稀有自定义方向（ZXQtool）\n## 说明\n')
  result=T.page_view(self.root,dict(channel=['interest'],range=['7d']),NOW);self.assertEqual(result['total'],0)
 def test_cross_source_today_at_all_shanghai_boundaries(self):
  times=['2026-11-01T00:00:00+08:00','2026-11-01T00:30:00+08:00','2026-11-01T07:59:00+08:00','2026-11-01T08:00:00+08:00','2026-11-01T08:01:00+08:00','2026-11-01T23:59:00+08:00','2026-10-31T23:59:00+00:00','2026-11-01T00:00:00+00:00']
  for when in times:
   now=datetime.fromisoformat(when);midnight=now.astimezone(G.BEIJING).replace(hour=0,minute=0,second=0,microsecond=0);rows=[]
   for n,moment in enumerate([midnight,midnight-timedelta(seconds=1),now+timedelta(seconds=1)]):
    value=S.item(dict(url='https://github.com/a/b/issues/'+str(n+1),topic='claude_code',full_text='MCP 工具的完整说明。'),'GitHub API','github:api',moment.isoformat(),platform='github');value['interest_matches']=M.classify(value,M.compile_topics(TOPICS));rows.append(value)
   data=dict(items=rows,errors=[],match_profile=M.profile_key(TOPICS));state=dict(ready=True,partial=False,refreshing=False,error=None,built_at=now.isoformat(),build_ms=0)
   with self.subTest(when=when),patch.object(S,'candidates',return_value=(data,state)):
    result=T.page_view(self.root,dict(channel=['interest'],range=['today']),now);self.assertEqual([p['id'] for p in result['items']],['github:a/b:issue:1'])
 def test_pagination_after_cross_source_dedup(self):
  data,_=self.ready();example=data['items'][0];rows=[]
  for n in range(125):
   v=dict(example,id='x:'+str(1000+n),canonical_key='x:'+str(1000+n),input_sources=['AIsa X'],title='Codex 分页内容',interest_matches=M.classify(dict(title='Codex',full_text=''),M.compile_topics(TOPICS)));rows.extend([v,dict(v,input_sources=['Grok 日报'])])
  data['items']=rows;all_ids=[]
  for page in ('1','2','3'):
   result=T.page_view(self.root,dict(channel=['interest'],range=['7d'],page=[page]),NOW);self.assertEqual(result['total'],125);all_ids.extend(p['id'] for p in result['items'])
  self.assertEqual(len(all_ids),125);self.assertEqual(len(set(all_ids)),125)

class MatchingBoundaries(unittest.TestCase):
 def test_fullwidth_duplicate_direction_registers_once(self):
  compiled=M.compile_topics(['Agent 编程工具','Ａｇｅｎｔ 编程工具','claude_code']);self.assertEqual(len(compiled),1)
 def test_merge_time_and_content_state_and_original_tool_identity(self):
  a=dict(id='github:a/b:repo',canonical_key='tool:1',kind='repo',fetched_at='2026-10-31T22:00:00+08:00',full_text='',content_complete=False,content_insufficient=True,interest_matches=[],rich_sources=[dict(article='补充一')])
  b=dict(id='tool:1',canonical_key='tool:1',kind='tool',interest_original=True,fetched_at='2026-10-31T15:00:00+00:00',full_text='完整的新正文以及工具的使用说明。',content_complete=True,content_insufficient=False,interest_matches=[],share_count=3,in_tools=True,what_new='中文新内容说明',rich_sources=[dict(quoted='补充二')])
  merged=M.deduplicate([a,b])[0];self.assertEqual(merged['fetched_at'],b['fetched_at']);self.assertFalse(merged['content_insufficient']);self.assertEqual(merged['id'],'tool:1');self.assertEqual(merged['share_count'],3);self.assertEqual(len(merged['rich_sources']),2);self.assertEqual(merged['what_new'],'中文新内容说明')
 def test_analysis_zero_preserved_and_bad_usable_is_safe(self):
  value=S.item(dict(id='1',url='https://x.com/f/status/1',why=0,outline=[0],usable={'url':'javascript:alert(1)'}),'AIsa X','A',NOW.isoformat(),platform='x')
  self.assertEqual(value['why'],0);self.assertEqual(value['outline'],[0]);self.assertEqual(value['usable'],[]);self.assertTrue(value['incomplete_reasons'])
 def test_metrics_are_newest_independent_from_longest_body(self):
  old=dict(id='x:1',canonical_key='x:1',full_text='旧的完整长正文'*20,content_complete=True,interest_original=True,fetched_at='2026-10-31T12:00:00+00:00',metrics=dict(views=99999),author=dict(handle='FactoryAI',followers=999999),interest_matches=[])
  fresh=dict(old,full_text='较短原文',content_complete=False,interest_original=False,fetched_at='2026-10-31T13:00:00+00:00',metrics=dict(views=150000),author=dict(handle='FactoryAI',followers=100000))
  merged=M.deduplicate([fresh,old])[0];self.assertEqual(merged['full_text'],old['full_text']);self.assertEqual(merged['metrics']['views'],150000);self.assertEqual(merged['author']['followers'],100000)
 def test_nfkc_english_case_parentheses_and_alias_metadata(self):
  directions=M.compile_topics(TOPICS)
  for row in [dict(title='Ｃｌａｕｄｅ　Ｃｏｄｅ 教程'),dict(full_text='cOdEx 使用'),dict(topic='Agent 编程工具（Claude Code、Codex、MCP）'),dict(topic='claude_code')]:
   with self.subTest(row=row):self.assertIn('agent-coding',{m['direction'] for m in M.classify(row,directions)})
 def test_generic_substring_entertainment_promo_false(self):
  directions=M.compile_topics(TOPICS)
  for row in [dict(title='Agent 和模型只是泛词'),dict(title='mycodexfoobar'),dict(kind='video',title='AI视频里的跳舞 #剪映'),dict(title='注册就送5刀 Claude Code 限时福利')]:
   with self.subTest(row=row):self.assertEqual(M.classify(row,directions),[])
 def test_weak_technical_context_and_multi_directions(self):
  rows=M.classify(dict(kind='video',title='剪映 ComfyUI 教程，用 Codex 批量出片'),M.compile_topics(TOPICS))
  self.assertTrue({'content-automation','agent-coding','visual-models'}<={m['direction'] for m in rows})
 def test_life_post_incidental_tool_word_and_two_character_specialist(self):
  directions=M.compile_topics(TOPICS)
  self.assertFalse(M.classify(dict(input_sources=['linux.do'],title='女朋友天天给我发钱',full_text='以为是 OpenCode 自动扣费'),directions))
  self.assertTrue(M.classify(dict(input_sources=['linux.do'],title='用 Codex 给女朋友做 App 教程',full_text='有真实技术流程'),directions))
  self.assertTrue(M.classify(dict(kind='video',title='剪映特效教程与步骤'),directions))
  self.assertFalse(M.classify(dict(title='H3 路线自驾游'),directions))
  self.assertFalse(M.classify(dict(title='Autodesk Maya 建模入门'),directions))
  self.assertIn('api-engines',{m['direction'] for m in M.classify(dict(title='Maya DeepSeek GGUF 推理引擎实测'),directions)})
 def test_inactive_unknown_and_custom_direction(self):
  self.assertEqual(M.classify(dict(title='Codex'),M.compile_topics(['本地部署与推理'])),[])
  self.assertEqual(M.classify(dict(topic='未知方向'),M.compile_topics(TOPICS)),[])
  matches=M.classify(dict(title='ＺＸＱｔｏｏｌ 可用'),M.compile_topics(['自定义（ZXQtool）']));self.assertTrue(matches[0]['direction'].startswith('custom:'))
 def test_alias_and_dedup_mutation_contract(self):
  # 变异运行应使此真实断言失败，不能只验证替身被调用。
  self.assertTrue(M.classify(dict(topic='claude_code'),M.compile_topics(TOPICS)))
  a=dict(canonical_key='x:1',id='x:1',input_sources=['Grok 日报'],sources=['G'],interest_matches=[],fetched_at=NOW.isoformat())
  self.assertEqual(len(M.deduplicate([a,dict(a,input_sources=['AIsa X'],sources=['A'])])),1)

if __name__=='__main__':unittest.main()
