from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from datetime import datetime,timezone
import platform_registry as R
from items_store import ItemsStore
from scripts.migrate_item_identity import migrate,legacy_hash
import test_today_view as fixtures
NOW=fixtures.NOW
import today_view as T
from interest_channel import _post

class RegistryTests(unittest.TestCase):
 def test_prefixes_and_unknown(self):
  for value,expected in [('x:1','x'),('X:1','x'),('douyin:2','douyin'),('xhs:3','xhs'),('wechat:4','wechat'),('github:a/b','github'),('linuxdo:1','linuxdo'),('x','web'),('x:','web'),('','web'),(None,'web'),('xxx:2','web')]:
   with self.subTest(value=value):self.assertEqual(R.derive_platform(value),expected)
 def test_alias_union_and_video_note(self):
  value=R.identity(dict(id='douyin:1',sources=['V','A','V']))
  self.assertEqual(value['channels'],['aisa']);self.assertEqual(value['channel_details'][0]['note'],'视频榜')
  self.assertEqual(R.identity(dict(id='x:1',sources=[]))['channels'],['unknown'])
  for channels in ([],None,'bad',['V','V','A']):
   value=R.identity(dict(id='?',sources=[],channels=channels,platform=[]))
   self.assertEqual(value['platform'],'web');self.assertEqual(len(value['channels']),len(set(value['channels'])))
 def test_bounded_filters(self):
  self.assertEqual(R.parse_filters({'platform':['X,douyin','x'],'source':['g']}),(['x','douyin'],'grok'))
  for query in ({'platform':['<script>']},{'platform':['x'*201]},{'collection_channel':['javascript:alert(1)']},{'source':['BAD']}):
   with self.subTest(query=query),self.assertRaises(ValueError):R.parse_filters(query)
 def test_k1_authoritative(self):
  with tempfile.TemporaryDirectory(dir='/tmp') as name:
   root=Path(name);path=root/'clues/clues.db';path.parent.mkdir()
   with closing(sqlite3.connect(path)) as db, db:
    db.executescript('CREATE TABLE clue_objects(id,canonical_key,platform); CREATE TABLE clue_channels(clue_id,channel);')
    db.execute("INSERT INTO clue_objects VALUES('c','tool:1','github')");db.execute("INSERT INTO clue_channels VALUES('c','wechat:group')")
   row=dict(id='tool:1',sources=['W']);R.annotate_items(root,[row]);self.assertEqual((row['platform'],row['channels']),('github',['wechat']))
   with closing(sqlite3.connect(path)) as db, db:self.assertEqual(db.execute('SELECT count(*) FROM clue_objects').fetchone()[0],1)
 def test_k1_aliases_and_readonly_locked_fallback(self):
  import hashlib,time
  with tempfile.TemporaryDirectory(dir='/tmp') as name:
   root=Path(name);path=root/'clues/clues.db';path.parent.mkdir()
   with closing(sqlite3.connect(path)) as db,db:
    db.executescript('CREATE TABLE clue_objects(id,canonical_key,platform); CREATE TABLE clue_channels(clue_id,channel); CREATE TABLE clue_aliases(alias,clue_id);')
    db.execute("INSERT INTO clue_objects VALUES('c','linuxdo:123','linuxdo')");db.execute("INSERT INTO clue_channels VALUES('c','linuxdo:rss')");db.execute("INSERT INTO clue_aliases VALUES('url:old','c')")
   before=hashlib.sha256(path.read_bytes()).hexdigest();row=dict(id='url:old',sources=['L'],url='https://linux.do/t/topic/123')
   R.annotate_items(root,[row]);self.assertEqual((row['platform'],row['channels']),('linuxdo',['rss']));self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),before)
   with closing(sqlite3.connect(path)) as blocker:
    blocker.execute('BEGIN EXCLUSIVE');began=time.monotonic()
    with self.assertLogs('platform_registry',level='WARNING') as logs:R.annotate_items(root,[dict(id='x:1',sources=['G'])])
    self.assertLess(time.monotonic()-began,.5);self.assertIn('OperationalError',str(logs.output));blocker.rollback()
   path.write_bytes(b'not sqlite')
   with self.assertLogs('platform_registry',level='WARNING'):value=R.annotate_items(root,[dict(id='x:2',sources=['G'])])[0]
   self.assertEqual(value['platform'],'x')
 def test_legacy_rss_uses_k1_canonical_without_store(self):
  with tempfile.TemporaryDirectory(dir='/tmp') as name:
   row=R.annotate_items(name,[dict(id='url:new',url='https://linux.do/t/topic/123',sources=['L'])])[0]
   self.assertEqual((row['id'],row['platform'],row['channels']),('url:new','linuxdo',['rss']))
 def test_interest_shapes_and_string_compat(self):
  row=dict(id='1',url='https://x.com/a/status/1',author_handle='a',topic='Agent',what_new='API',full_text='完整的模型部署教程',published_at=NOW.isoformat(),relevance=3,content_complete=True,usable=[])
  for value in ('原有纯字符串',['数组一',{'text':'数组二'}],None,[],0):
   result,_=_post({**row,'why':value},NOW);self.assertEqual(result['why'],value)

class PageTests(unittest.TestCase):
 def setUp(self):
  self.f=fixtures.TodayTests();self.f.setUp();self.addCleanup(self.f.tearDown)
 def test_platform_and_channel_counts_and_old_links(self):
  self.f.write_bundle([dict(id='3',text='AI Agent 完整原文',url='https://x.com/a/status/3',published_at=(NOW-__import__('datetime').timedelta(hours=1)).isoformat(),fetched_at=NOW.isoformat())])
  all_items=T.page_view(self.f.workspace,{'range':['48h']},NOW)['items']
  for platforms,channel in [(['douyin'],''),(['x'],''),(['x','douyin'],''),([],'grok'),(['x'],'aisa'),(['douyin'],'aisa')]:
   query=dict(range=['48h'])
   if platforms:query['platform']=[','.join(platforms)]
   if channel:query['collection_channel']=[channel]
   result=T.page_view(self.f.workspace,query,NOW)
   expected=[i for i in all_items if (not platforms or i['platform'] in platforms) and (not channel or channel in i['channels'])]
   self.assertEqual(result['total'],len(expected));self.assertEqual({i['id'] for i in result['items']},{i['id'] for i in expected})
  for old,new in [('G','grok'),('A','aisa'),('V','aisa'),('L','rss')]:
   a=T.page_view(self.f.workspace,dict(range=['48h'],source=[old]),NOW);b=T.page_view(self.f.workspace,dict(range=['48h'],collection_channel=[new]),NOW);self.assertEqual({i['id'] for i in a['items']},{i['id'] for i in all_items if old in i['sources']})
 def test_arrays_survive_post_and_summary(self):
  row=self.f.row();row.update(why=['原因'],pattern=[{'text':'写法'}],outline=['提纲'])
  self.f.write([row]);detail=self.f.detail();self.assertEqual(detail['why'],['原因']);self.assertEqual(detail['outline'],['提纲'])
 def test_raw_51_sample_filtering_not_in_candidate_total(self):
  fake=dict(id='x:full-raw',title='原始采样',sources=['AIsa 推特'],url='',reason='不相关',hidden=True)
  with patch.object(T,'aisa_full_stats',return_value=(dict(total=1,visible=0,filtered=1,ratio=1,insufficient=0),[fake])):
   result=T.page_view(self.f.workspace,{'range':['48h']},NOW)
  main={x['id'] for x in result['filtering']['items'] if x['hidden'] and x['sources']!=['AIsa 51 人全量推文']}
  self.assertEqual(result['filtering']['total'],len(main));self.assertEqual(result['filtering']['aisa_all_filtered'],1)
 def test_latest_report_extra_uses_correct_matching_row(self):
  from datetime import timedelta
  older=self.f.row('1');older.update(outline=['旧条目'])
  newer=self.f.row('1');newer.update(outline=['正确第二条'])
  decoy=self.f.row('999');decoy.update(outline=['不能取第一条'])
  self.f.write([older],file='daily/old.json',when=NOW-timedelta(hours=1));self.f.write([decoy,newer],when=NOW)
  self.assertEqual(self.f.detail()['outline'],['正确第二条'])
 def test_invalid_query_no_crash(self):
  for query in ({'platform':['x','x']},{'platform':['X,douyin']},{'source':['g']},{'collection_channel':['GROK']}):T.page_view(self.f.workspace,{**query,'range':['48h']},NOW)
  for query in ({'platform':['x']*11},{'platform':['x'*201]},{'collection_channel':['bad']},{'q':['x'*2001]}):
   with self.assertRaises(ValueError):T.page_view(self.f.workspace,query,NOW)

class PaginationTests(unittest.TestCase):
 def test_default_pages_match_pre_r4_by_every_field(self):
  import importlib.util
  import test_interest_channel as fixtures
  import wechat_tools_view as W
  fixture=fixtures.InterestTests();fixture.setUp();self.addCleanup(fixture.doCleanups)
  rows=[fixtures.row(str(1000+i)) for i in range(125)];fixture.write(dict(posts=rows))
  path=Path(__file__).parent/'fixtures/r4_interest_page_before.py'
  spec=importlib.util.spec_from_file_location('frozen_r4_interest',path);old=importlib.util.module_from_spec(spec);spec.loader.exec_module(old)
  all_rows=W.interest_page(fixture.root,dict(range=['7d']),fixtures.NOW,unpaged=True)
  self.assertEqual(len(all_rows['items']),125)
  for page in (1,2,3):
   query=dict(range=['7d'],page=[str(page)])
   legacy=old.interest_page(fixture.root,query,fixtures.NOW);current=W.interest_page(fixture.root,query,fixtures.NOW)
   self.assertEqual(current,legacy)
   self.assertEqual(current['items'],all_rows['items'][(page-1)*50:page*50])


class TopicTests(unittest.TestCase):
 def test_aliases_counts_chinese_and_unclassified_last(self):
  from topic_registry import topic_key,topic_options
  values=['Agent 编程工具（Claude Code、Codex、MCP）','Agent编程工具','claude_code','AI视频与图像','AIGC视频图像','待归类','ai_models','money_indie','overseas_infra','github_open']
  rows=[dict(topic=value) for value in values];options=topic_options(rows)
  self.assertEqual(next(x['count'] for x in options if x['key']=='agent-coding'),3)
  self.assertEqual(next(x['count'] for x in options if x['key']=='visual-models'),2)
  self.assertEqual(options[-1]['key'],'unclassified');self.assertTrue(all(not __import__('re').fullmatch('[a-z_]+',x['display_name']) for x in options))
  for value in values[:3]:self.assertEqual(topic_key(value),'agent-coding')
 def test_alias_query_matches_all_rows(self):
  from topic_registry import matches_topic
  rows=[dict(topic=v) for v in ['Agent编程工具','claude_code','Agent 编程工具（Claude Code、Codex、MCP）','新模型发布']]
  for query in ['agent-coding','claude_code','Agent 编程工具']:
   self.assertEqual(sum(matches_topic(row,query) for row in rows),3)


class MigrationTests(unittest.TestCase):
 def test_additive_migration_and_idempotence(self):
  with tempfile.TemporaryDirectory(prefix='unit-',dir='/tmp/r4-rest') as name:
   root=Path(name);store=ItemsStore(root)
   saved=store.upsert(dict(id='x:1',url='https://x.com/a/status/1',sources=['G','A'],title='原文',outline=['一','二']))
   with closing(sqlite3.connect(store.path)) as db, db:
    # Simulate old schema by removing only this migration's new columns.
    for col in ('platform','channels_json','identity_backup_json'):db.execute('ALTER TABLE items DROP COLUMN '+col)
    before=legacy_hash(db)
   a=migrate(store.path,root/'first');b=migrate(store.path,root/'repeat')
   self.assertEqual((a['before'],a['after'],a['changed'],b['changed']),(1,1,1,0));self.assertEqual(before,a['legacy_after_sha256'])
   self.assertEqual(store.detail('x:1')['outline'],['一','二']);self.assertEqual(store.detail('x:1')['channels'],['grok','aisa'])
   with closing(sqlite3.connect(a['backup'])) as db:self.assertNotIn('platform',[r[1] for r in db.execute('PRAGMA table_info(items)')])
 def test_new_items_and_existing_api(self):
  with tempfile.TemporaryDirectory(dir='/tmp') as name:
   store=ItemsStore(name);store.upsert(dict(id='x:1',url='https://x.com/a/status/1',sources=['G'],title='纯字符串',why_viral=['原因'],outline=['第一','第二']))
   value=store.detail('x:1');self.assertEqual(value['why'],['原因']);self.assertEqual(value['outline'],['第一','第二']);self.assertEqual(store.listing({'source':['G']})['total'],1)

if __name__=='__main__':unittest.main()
