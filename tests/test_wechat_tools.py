"""All messages are synthetic; never connect a real writer or inference service."""
import copy
from contextlib import closing
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
from urllib.parse import urlsplit,parse_qs
import clue_store_v2 as S
import clue_ingest as I
import wechat_tools as W
import wechat_tools_config as C
import wechat_tools_view as V
from clue_ingest_api import get_route

TOPICS=['新模型发布','本地部署与推理','AI 视频与图像模型','新 API / 推理引擎 / 模型网关','Agent 编程工具','Agent Skill 安全扫描','OpenCode / 本地 Agent 桌面插件生态','AI 内容生产自动化','3D / 游戏 / 硬件 Agent 落地']
MEMBER='SYNTHETIC_MEMBER';PRIVATE='wxid_SYNTHETIC_PRIVATE'

def message(identifier='m1',body=None,group=C.DEFAULT_GROUP,day='2026-10-28',clock='08:00:00',sender=MEMBER,private=False):
    return dict(id=identifier,title=group+' · '+sender+' · '+day,body=body or '📮 小耳 Today · 10月28日\n① AgentAlpha\nAI Agent 编程工具\n用法：打开页面输入代码\n链接：https://tools.example/alpha',modified=day+'T'+clock+'+00:00',hash='h'+identifier,provenance=json.dumps(dict(chatroom_id='synthetic@chatroom' if not private else PRIVATE,conversation_type='group' if not private else 'private',sender=sender)))

def checked(url):return dict(state='checked',http_status=200,title='Synthetic Tool',reason=None)

class ToolsTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='r3-tools-',dir='/tmp');self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name);(self.root/'clues').mkdir();self.db=self.root/'clues/clues.db';sqlite3.connect(self.db).close();S.schema(self.db,'seed',TOPICS)
        (self.root/'wechat-tools').mkdir();(self.root/'wechat-tools/interest-topics.json').write_text(json.dumps(TOPICS))
    def prepare(self,rows,**kw):return W.prepare(self.root,{'2026-10-28'},rows=rows,title_check=checked,**kw)
    def import_rows(self,rows=None,batch='tools',**kw):return W.run(self.root,{'2026-10-28'},batch,rows=rows or [message()],title_check=checked,**kw)
    def count(self,table):
        with closing(S.connect(self.db,True)) as db:return db.execute('SELECT count(*) FROM '+table).fetchone()[0]
    def all_rows(self,table):
        with closing(S.connect(self.db,True)) as db:return [dict(r) for r in db.execute('SELECT * FROM '+table+' ORDER BY id')]
    def test_split_three_messages_and_forward_date(self):
        old=message('old',body='📮 小耳 Today · 10月27日\n① WrongDay\nAI tool\n用法：打开\n链接：https://tools.example/wrong')
        two=message('m2',body='② AgentBeta\nAI MCP工具\n用法：运行CLI\n链接：https://tools.example/beta',clock='08:00:12')
        three=message('m3',body='③ AgentGamma\nAI 工具\n用法：输入代码\n链接：https://tools.example/gamma',clock='08:00:24')
        data,stats=self.prepare([old,message(),two,three]);self.assertEqual([r['name'] for r in data['items']],['AgentAlpha','AgentBeta','AgentGamma']);self.assertEqual(stats['bad'],0)
    def test_continuation_wrong_sender_or_delayed_is_not_attached(self):
        two=message('m2',body='② Bad\nAI tool\n用法：打开\n链接：https://tools.example/bad',sender='OTHER_SYNTHETIC',clock='08:00:12')
        late=message('m3',body=two['body'],clock='08:20:00');data,_=self.prepare([message(),two,late]);self.assertEqual(len(data['items']),1)
    def test_two_full_issues_in_one_message_uses_correct_last_anchor(self):
        row=message(body='📮 小耳 Today · 10月27日\n① Wrong\nAI tool\n链接：https://tools.example/wrong\n📮 小耳 Today · 10月28日\n① Right\nAI 编程\n用法：运行\n链接：https://tools.example/right')
        data,_=self.prepare([row]);self.assertEqual(data['items'][0]['name'],'Right')
    def test_multiple_requested_issues_in_one_message_are_both_parsed(self):
        row=message(body='📮 小耳 Today · 10月28日\n① First\nAI 工具\n用法：运行\n链接：https://tools.example/first\n📮 小耳 Today · 10月29日\n① Second\nAI 工具\n用法：打开\n链接：https://tools.example/second')
        data,_=W.prepare(self.root,{'2026-10-28','2026-10-29'},rows=[row],title_check=checked);self.assertEqual([r['name'] for r in data['items']],['First','Second']);I.register_batch(self.db,'two-issues');result=I.import_document(self.db,'wechat_tools','knowledge:wechat',data,S.digest(data['items']),'two-issues');self.assertEqual(result['new'],2);self.assertEqual(self.count('source_receipts'),2)
    def test_malformed_numbers_links_and_ad_do_not_crash(self):
        row=message(body='📮 小耳 Today · 10月28日\n① Valid\nAI工具\n用法：运行\n链接：https://tools.example/a\n广告：加入聊天 https://advert.example/extra\n② MissingLink\n说明\n③ Good\nAI工具\n用法：上传\n链接：https://tools.example/c')
        data,stats=self.prepare([row]);self.assertEqual([r['name'] for r in data['items']],['Valid','Good']);self.assertEqual(stats['bad'],1);self.assertNotIn('广告',S.dumps(data))
    def test_missing_number_is_ignored(self):
        data,_=self.prepare([message(body='📮 小耳 Today · 10月28日\nTool without number\n链接：https://tools.example/a')]);self.assertEqual(data['items'],[])
    def test_private_messages_never_call_model(self):
        model=unittest.mock.Mock(side_effect=AssertionError('private reached model'));data,stats=self.prepare([message(private=True)],infer=model);self.assertEqual(data['items'],[]);model.assert_not_called();self.assertEqual(stats['private_skipped'],1)
        (self.root/'knowledge').mkdir();db=sqlite3.connect(self.root/'knowledge/knowledge.db');db.execute('create table documents(id text,title text,body text,modified text,provenance text,hash text,source text)')
        for row in (message('group'),message('private',private=True)):
            db.execute('insert into documents values(?,?,?,?,?,?,?)',tuple(row[k] for k in ('id','title','body','modified','provenance','hash'))+('wechat',))
        db.commit();db.close();self.assertEqual([r['id'] for r in W.messages(self.root,{'2026-10-28'})],['group'])
    def test_unknown_conversation_fails_closed(self):
        r=message();r['provenance']='{}';data,_=self.prepare([r],infer=lambda *a:self.fail('unknown reached model'));self.assertEqual(data['items'],[])
    def test_exact_whitelist_all_lookalikes(self):
        for name in [' '+C.DEFAULT_GROUP,C.DEFAULT_GROUP+' ', '\u200b'+C.DEFAULT_GROUP,C.DEFAULT_GROUP+'伪装',C.DEFAULT_GROUP.replace('Omia','omia'),C.DEFAULT_GROUP[:-1]]:
            with self.subTest(name=repr(name)):
                self.assertFalse(C.curated(name,[C.DEFAULT_GROUP]));data,_=self.prepare([message(group=name)],infer=lambda *a:dict(is_tool=False));self.assertTrue(all(not r.get('curated',False) for r in data['items']))
    def test_non_tool_social_file_internal_urls(self):
        for url in ['https://www.douyin.com/video/123','https://weibo.com/a/2','https://tools.example/a.pdf','https://mp.weixin.qq.com/s/test','https://weixin.qq.com/download/a','https://127.0.0.1/a','https://a.qq.com/files/a','https://localhost/a','https://tools.example/a?token=secret','https://tools.example:bad/a','https://tools.example/a?owner='+PRIVATE]:
            with self.subTest(url=url):self.assertIsNone(W.tool_url(url))
    def test_url_equivalence_and_explicit_version(self):
        urls=['http://tools.example/a/','https://tools.example/a?utm_source=test','https://tools.example/a#x'];self.assertEqual(len({W.tool_url(x) for x in urls}),1)
        rows=[message('m'+str(i),body=f'📮 小耳 Today · 10月28日\n① Tool v1.0\nAI工具\n用法：运行\n链接：{u}') for i,u in enumerate(urls)]
        self.import_rows(rows);self.assertEqual(self.count('clue_objects'),1);self.assertEqual(self.count('clue_channels'),3);self.assertEqual(V.cards(self.root)[0]['share_count'],3)
        self.assertEqual(self.all_rows('resource_links')[0]['version'],'v1.0')
    def test_distinct_versions_remain_distinct(self):
        rows=[message('v'+str(i),body=f'📮 小耳 Today · 10月28日\n① Tool v{i}.0\nAI工具\n用法：运行\n链接：https://tools.example/a') for i in (1,2)];self.import_rows(rows);self.assertEqual(self.count('clue_objects'),2)
    def test_same_domain_different_paths_only_candidates(self):
        rows=[message('path'+str(i),body=f'📮 小耳 Today · 10月28日\n① AgentTool\nAI工具\n用法：运行\n链接：https://github.com/{p}') for i,p in enumerate(('a/x','b/y'))];self.import_rows(rows);self.assertEqual(self.count('clue_objects'),2);self.assertEqual(self.count('content_events'),0);self.assertTrue(any(r['relation_type']=='possible_duplicate_resource' for r in self.all_rows('clue_relations')))
    def test_complete_chain_collection_suggestions_evidence_resource(self):
        self.import_rows();card=V.cards(self.root)[0];d=S.detail(self.db,card['clue_id']);self.assertTrue(card['in_tools']);self.assertEqual(card['direction_state'],'pending_confirmation');self.assertTrue(all(t['state']=='suggested' for t in d['topic_assignments'] if t['topic_id'].startswith('direction:')))
        self.assertEqual(d['evidence'][0]['reference'],dict(namespace='knowledge:wechat',message_id='m1'));self.assertEqual(d['resources'][0]['evidence_id'],d['evidence'][0]['id']);self.assertEqual(self.count('clue_content_versions'),0)
        self.assertEqual(V.collection_inbox(self.db,collection='tools')['items'][0]['id'],card['clue_id']);self.assertEqual(self.count('source_receipts'),1)
    def test_unreachable_link_keeps_trusted_card_unverified(self):
        W.run(self.root,{'2026-10-28'},'dead',rows=[message()],title_check=lambda u:dict(state='unverified',http_status=503,title=None,reason='http_error'));card=V.cards(self.root)[0];self.assertTrue(card['in_tools']);self.assertEqual(card['verification']['state'],'unverified')
    def test_other_group_low_relevance_and_gate_hidden(self):
        model=lambda *a:dict(is_tool=True,name='Pretty Colour',purpose='普通配色工具',usage='打开网页',classification='设计',relevance=1,version=None)
        self.import_rows([message(group='合成其他群')],infer=model);self.assertEqual(V.cards(self.root),[]);self.assertFalse(V.cards(self.root,include_filtered=True)[0]['in_tools'])
        self.assertEqual(V.collection_inbox(self.db,collection='tools')['items'],[])
    def test_other_group_high_score_still_needs_gate(self):
        model=lambda *a:dict(is_tool=True,name='Weather',purpose='查看天气',usage='打开网站',classification='天气',relevance=3)
        self.import_rows([message(group='合成天气群')],infer=model);self.assertEqual(V.cards(self.root),[])
    def test_other_group_ai_candidate_never_auto_collection(self):
        model=lambda *a:dict(is_tool=True,name='AgentCode',purpose='AI Agent 编程助手',usage='输入代码',classification='编程',relevance=3)
        self.import_rows([message(group='合成开发群')],infer=model);card=V.cards(self.root)[0];self.assertFalse(card['in_tools']);self.assertEqual(V.collection_inbox(self.db,collection='tools')['items'],[])
    def test_adapter_rechecks_gate_and_cannot_trust_forged_visible_flag(self):
        model=lambda *a:dict(is_tool=True,name='Weather',purpose='普通天气',usage='打开网页',classification='天气',relevance=0)
        data,_=self.prepare([message(group='合成其他群')],infer=model);data['items'][0]['filtered']=False;I.register_batch(self.db,'forged');I.import_document(self.db,'wechat_tools','knowledge:wechat',data,S.digest(data['items']),'forged');self.assertEqual(V.cards(self.root),[])
    def test_model_declines_non_tools(self):
        self.import_rows([message(group='合成其他群')],infer=lambda *a:dict(is_tool=False));self.assertEqual(self.count('clue_objects'),0);self.assertEqual(self.all_rows('source_receipts')[0]['import_status'],'evaluated')
    def test_model_fails_no_fabricated_card(self):
        with patch.object(W,'infer_tool',side_effect=ValueError('model_schema')):
            data,stats=self.prepare([message(group='合成其他群')],infer=lambda *a:(_ for _ in ()).throw(ValueError('bad')))
        self.assertEqual(data['items'],[]);self.assertEqual(stats['model_deferred'],1)
    def test_model_only_sanitized_output_no_sender_or_wxid(self):
        r=message(group='合成开发群');model=lambda *a:dict(is_tool=True,name='AgentCode '+MEMBER,purpose='AI 工具 '+PRIVATE,usage='运行 '+MEMBER,classification='编程',relevance=3,raw_message=r['body'])
        self.import_rows([r],infer=model);encoded=S.dumps(self.all_rows('clue_objects')+self.all_rows('clue_channels')+self.all_rows('clue_evidence')+self.all_rows('source_receipts'))
        for token in (MEMBER,PRIVATE,'raw_message',r['body']):self.assertNotIn(token,encoded)
    def test_replay_no_duplicates_or_overwrite_old_k1_rows(self):
        self.import_rows();old=self.all_rows('clue_objects');channels=self.all_rows('clue_channels');self.import_rows(batch='second');self.assertEqual(self.all_rows('clue_objects'),old);self.assertEqual(self.all_rows('clue_channels'),channels);self.assertEqual(self.count('source_receipts'),1)
    def test_import_never_initializes_items_or_tasks_or_private_config(self):
        self.import_rows();self.assertFalse((self.root/'items').exists());self.assertFalse((self.root/'schedules').exists());self.assertFalse((self.root/'private').exists())
    def test_explicit_keep_only_and_evidence_immutable(self):
        self.import_rows();item=V.cards(self.root)[0];before=self.all_rows('clue_evidence');V.feedback(self.root,dict(id=item['id'],channel='interest',action='accurate'));self.assertFalse((self.root/'items/items.db').exists())
        V.feedback(self.root,dict(id=item['id'],channel='interest',action='keep'));from items_store import ItemsStore
        saved=ItemsStore(self.root).listing({})['items'];self.assertEqual(len(saved),1);self.assertEqual(saved[0]['kind'],'tool');self.assertEqual(self.all_rows('clue_evidence'),before)
        dest=[r for r in self.all_rows('clue_relations') if r['relation_type']=='additional_destination'];self.assertEqual(len(dest),1);self.assertTrue(dest[0]['to_key'].startswith('items:'));self.assertEqual(V.cards(self.root)[0]['status'],'kept')
        V.feedback(self.root,dict(id=item['id'],channel='interest',action='keep'));self.assertEqual(len(ItemsStore(self.root).listing({})['items']),1)
    def test_other_group_first_keep_is_one_primary_destination_and_idempotent(self):
        model=lambda *a:dict(is_tool=True,name='AgentCode',purpose='AI Agent 编程助手',usage='输入代码',classification='编程',relevance=3)
        self.import_rows([message(group='合成开发群')],infer=model);item=V.cards(self.root)[0];payload=dict(id=item['id'],channel='interest',action='keep');V.feedback(self.root,payload);V.feedback(self.root,payload)
        relations=[r for r in self.all_rows('clue_relations') if r['relation_type'] in ('primary_destination','additional_destination')];self.assertEqual(len(relations),1);self.assertEqual(relations[0]['relation_type'],'primary_destination');self.assertTrue(relations[0]['to_key'].startswith('items:'));self.assertEqual(V.cards(self.root)[0]['status'],'kept')
    def test_write_helper_rejects_non_explicit_action(self):
        self.import_rows();item=V.cards(self.root)[0]
        with self.assertRaises(ValueError):V.save_item_explicit(self.root,item,'accurate')
        self.assertFalse((self.root/'items/items.db').exists())
    def test_nonblocking_shared_k1_lock(self):
        with I.ingest_lock(self.db) as acquired:
            self.assertTrue(acquired);t=time.monotonic();r=self.import_rows();self.assertTrue(r['skipped']);self.assertLess(time.monotonic()-t,.5)
            self.assertTrue(I.ingest(self.db,self.root,self.root,'seed',[])['skipped'])
        self.assertEqual(self.count('clue_objects'),0)
    def test_initial_seal_complete_legacy_and_idempotent(self):
        self.import_rows();before=self.all_rows('migrations');counts=S.complete_batch(self.db,'tools',confirmed=True);self.assertEqual(counts['tables']['clue_objects'],1);self.assertIn('wechat_tools',counts['sources']);self.assertEqual(before,self.all_rows('migrations'))
    def test_mid_import_failure_cannot_be_completed(self):
        with patch.object(I,'import_document',side_effect=RuntimeError('synthetic_failure')):
            with self.assertRaises(RuntimeError):self.import_rows()
        row=next(r for r in self.all_rows('migrations') if r['id']=='tools');self.assertNotEqual(row['status'],'completed')
        with self.assertRaises(ValueError):S.complete_batch(self.db,'tools',confirmed=True)
        with self.assertRaises(ValueError):S.complete_batch(self.db,'seed')
    def test_whitelist_atomic_conflict_and_permissions(self):
        initial=C.read(self.root);saved=C.save(self.root,dict(groups=[C.DEFAULT_GROUP,'合成新群'],revision=initial['revision']));p=C.path_for(self.root);os.chmod(p,0o640)
        newer=C.save(self.root,dict(groups=[C.DEFAULT_GROUP],revision=saved['revision']));self.assertEqual(p.stat().st_mode&0o777,0o640)
        with self.assertRaises(C.Conflict):C.save(self.root,dict(groups=[],revision=saved['revision']))
        self.assertEqual(C.read(self.root),newer);self.assertFalse(list(p.parent.glob('.whitelist-*')))
    def test_whitelist_invalid_hidden_space_duplicate(self):
        for groups in [[' '+C.DEFAULT_GROUP],[C.DEFAULT_GROUP+'\u200b'],[PRIVATE],[C.DEFAULT_GROUP,C.DEFAULT_GROUP]]:
            with self.subTest(groups=groups):
                with self.assertRaises(ValueError):C.save(self.root,dict(groups=groups,revision='missing'))
    def test_sync_trigger_does_not_run_before_ready_or_repeat_same_version(self):
        (self.root/'knowledge').mkdir();db=sqlite3.connect(self.root/'knowledge/knowledge.db');db.execute('create table sources(id text,status text,synced_at text)');db.execute("insert into sources values('wechat','syncing',?)",(S.now(),));db.commit();db.close()
        self.assertEqual(self.import_rows(after_sync=True)['reason'],'wechat_sync_not_ready');self.assertEqual(self.count('clue_objects'),0)
        db=sqlite3.connect(self.root/'knowledge/knowledge.db');db.execute("update sources set status='ready'");db.commit();db.close()
        first=self.import_rows(after_sync=True);self.assertEqual(first['imported']['new'],1);self.assertIsNone(V.cards(self.root)[0]['relevance'])
        self.assertEqual(self.import_rows(batch='repeat',after_sync=True)['reason'],'wechat_sync_unchanged')
    def test_file_failure_cannot_be_manually_sealed(self):
        reports=self.root/'reports';(reports/'daily').mkdir(parents=True);(reports/'daily/bad.json').write_text('{invalid')
        result=I.ingest(self.db,reports,self.root,'seed',sources=['daily']);self.assertEqual(result['daily']['bad_files'],1)
        with self.assertRaises(ValueError):S.complete_batch(self.db,'seed',confirmed=True)
    def test_collection_invalid_blank_injection_never_full_scan(self):
        self.import_rows()
        class H:
            server=type('Server',(),{'store':type('Store',(),{'workspace':self.root})()})()
            def reply(self,code,value):return code,value
        for query in ('collection=','collection=TOOLS','collection=tools%20','collection=x%27%20OR%201=1--','collection=tools&collection='):
            with self.subTest(query=query):
                with self.assertRaises(ValueError):get_route(H(),urlsplit('/api/clue-inbox?'+query),parse_qs(query))
        code,value=get_route(H(),urlsplit('/api/clue-inbox?collection=tools'),{'collection':['tools']});self.assertEqual(code,200);self.assertEqual(len(value['items']),1)
    def test_collection_cursor_and_other_filters(self):
        rows=[message(str(i),body=f'📮 小耳 Today · 10月28日\n① Tool{i}\nAI 工具\n用法：打开\n链接：https://tools.example/a{i}') for i in range(3)];self.import_rows(rows)
        first=V.collection_inbox(self.db,collection='tools',limit=2);second=V.collection_inbox(self.db,collection='tools',limit=2,cursor=first['next_cursor']);self.assertEqual(len(first['items'])+len(second['items']),3);self.assertFalse(set(r['id'] for r in first['items'])&set(r['id'] for r in second['items']));self.assertEqual(V.collection_inbox(self.db,collection='tools',source='other')['items'],[])



class Fix1BoundaryTests(unittest.TestCase):
    """Synthetic, ordered messages; no real source or writer is used."""
    days = {'2026-10-28','2026-10-29'}

    def row(self, identifier, number=None, *, header=False, clock='2026-10-28T08:00:00Z', sender=MEMBER, group=C.DEFAULT_GROUP):
        body = (("📮 小耳 Today · 10月" + ('29' if '29' in clock[:10] else '28') + "日\n") if header else '')
        body += (str(number)+'. Agent'+str(number)+'\n用途：AI Agent 编程工具\n用法：打开页面\n链接：https://tools.example/'+str(number)) if number else '普通插话'
        row=message(identifier,body=body,group=group,sender=sender)
        row['modified']=clock
        provenance=json.loads(row['provenance']);provenance['chatroom_id']='synthetic-'+S.digest(group)[:8]+'@chatroom';row['provenance']=json.dumps(provenance)
        return row

    def parse(self, rows):return W.extract_curated(rows,self.days,[C.DEFAULT_GROUP,'合成第二精选群'])

    def test_other_sender_interrupts(self):
        cards,audit=self.parse([self.row('one',1,header=True),self.row('talk',sender='OTHER_SYNTHETIC',clock='2026-10-28T08:00:01Z'),self.row('two',2,clock='2026-10-28T08:00:02Z')])
        self.assertEqual([c['message_id'] for c in cards],['one']);self.assertEqual(audit['bad'],1);self.assertEqual(audit['bad_reasons'],{'continuation_not_adjacent':1})

    def test_same_sender_ordinary_message_interrupts(self):
        cards,audit=self.parse([self.row('one',1,header=True),self.row('talk',clock='2026-10-28T08:00:01Z'),self.row('two',2,clock='2026-10-28T08:00:02Z')])
        self.assertEqual(len(cards),1);self.assertEqual(audit['bad'],1)

    def test_system_and_withdrawal_interrupt(self):
        for signal in ('tags','withdrawal_type'):
            with self.subTest(signal=signal):
                system=self.row('system',2,clock='2026-10-28T08:00:01Z')
                if signal=='tags':system['tags']=json.dumps(['系统'])
                else:
                    provenance=json.loads(system['provenance']);provenance['msg_type']=10002;system['provenance']=json.dumps(provenance)
                cards,audit=self.parse([self.row('one',1,header=True),system,self.row('two',2,clock='2026-10-28T08:00:02Z')])
                self.assertEqual([c['message_id'] for c in cards],['one']);self.assertEqual(audit['bad'],1)

    def test_shanghai_midnight_breaks_continuation(self):
        cards,audit=self.parse([self.row('one',1,header=True,clock='2026-10-28T15:59:59Z'),self.row('two',2,clock='2026-10-28T16:00:01Z')])
        self.assertEqual(len(cards),1);self.assertEqual(audit['bad_reasons'],{'continuation_different_day':1})

    def test_utc_midnight_keeps_same_shanghai_day(self):
        first=self.row('one',1,header=True,clock='2026-10-28T23:59:59Z');first['body']=first['body'].replace('10月28','10月29')
        cards,audit=self.parse([first,self.row('two',2,clock='2026-10-29T00:00:01Z')]);self.assertEqual(len(cards),2);self.assertEqual(audit['bad'],0)

    def test_301_seconds_rejected_and_300_allowed(self):
        for delay,count in (('08:05:01',1),('08:05:00',2)):
            with self.subTest(delay=delay):
                cards,audit=self.parse([self.row('one',1,header=True),self.row('two',2,clock='2026-10-28T'+delay+'Z')]);self.assertEqual(len(cards),count);self.assertEqual(audit['bad'],2-count)

    def test_interleaved_groups_keep_independent_state(self):
        rows=[self.row(group+str(number),number,header=number==1,group=group,clock='2026-10-28T08:00:0'+str(number)+'Z') for number in (1,2,3) for group in (C.DEFAULT_GROUP,'合成第二精选群')]
        cards,audit=self.parse(rows);self.assertEqual(len(cards),6);self.assertEqual(audit['bad'],0)

    def test_other_group_system_does_not_interrupt(self):
        system=self.row('system',group='合成第二精选群');system['tags']='["系统"]'
        cards,audit=self.parse([self.row('one',1,header=True),system,self.row('two',2)]);self.assertEqual(len(cards),2);self.assertEqual(audit['bad'],0)
        first=self.row('one',1,header=True);untrusted=self.row('renamed',group='合成普通群');provenance=json.loads(untrusted['provenance']);provenance['chatroom_id']=json.loads(first['provenance'])['chatroom_id'];untrusted['provenance']=json.dumps(provenance)
        cards,audit=self.parse([first,untrusted,self.row('two',2)]);self.assertEqual([c['message_id'] for c in cards],['one']);self.assertEqual(audit['bad'],1)

    def test_equal_timestamp_preserves_input_message_order(self):
        cards,audit=self.parse([self.row('z-first',1,header=True),self.row('y-talk'),self.row('a-last',2)]);self.assertEqual([c['message_id'] for c in cards],['z-first']);self.assertEqual(audit['bad'],1)

    def test_missing_number_counted_and_reasoned(self):
        row=self.row('missing',1,header=True);row['body']=row['body'].replace('1. Agent1','Agent1');cards,audit=self.parse([row]);self.assertEqual(cards,[]);self.assertEqual(audit['bad'],1);self.assertEqual(audit['bad_reasons'],{'missing_number':1})

    def test_rejected_continuation_cannot_restore_old_state(self):
        cards,audit=self.parse([self.row('one',1,header=True),self.row('wrong',3),self.row('two',2)]);self.assertEqual(len(cards),1);self.assertEqual(audit['bad'],2)

    def test_source_sequence_and_clock_override_index_modification(self):
        with tempfile.TemporaryDirectory(prefix='r3-fix1-order-',dir='/tmp') as name:
            root=Path(name);(root/'knowledge').mkdir();db=sqlite3.connect(root/'knowledge/knowledge.db');db.execute('create table documents(id text,title text,body text,modified text,provenance text,hash text,source text,tags text)')
            rows=[self.row('z-first',1,header=True),self.row('y-talk'),self.row('a-last',2)]
            for number,row in enumerate(rows,1):
                provenance=json.loads(row['provenance']);provenance.update(timestamp=1793174400,raw_local_id=str(number),local_id=number);row['provenance']=json.dumps(provenance);row['modified']='2026-11-05T00:00:00Z'
            for row in reversed(rows):db.execute('insert into documents values(?,?,?,?,?,?,?,?)',tuple(row[k] for k in ('id','title','body','modified','provenance','hash'))+('wechat','[]'))
            db.commit();db.close();loaded=W.messages(root,self.days);self.assertEqual([r['id'] for r in loaded],['z-first','y-talk','a-last']);cards,audit=self.parse(loaded);self.assertEqual([c['message_id'] for c in cards],['z-first']);self.assertEqual(audit['bad'],1);self.assertTrue(cards[0]['shared_at'].startswith('2026-10-28'))

    def test_untitled_first_number_is_not_a_continuation(self):
        cards,audit=self.parse([self.row('one',1,header=True),self.row('ordinary',1),self.row('two',2)])
        self.assertEqual([c['message_id'] for c in cards],['one']);self.assertEqual(audit['bad'],1)


class Fix2ModelLeaseTests(unittest.TestCase):
    """Use the source-owned radar executor, synthetic HTTP, and /tmp locks only."""
    def setUp(self):
        from wechat_source_loader import source_module
        try:
            self.executor = source_module('pi_executor')
            self.status = source_module('model_service_status')
        except ValueError as exc:
            raise unittest.SkipTest('本机微信来源插件未配置') from exc
        self.tmp = tempfile.TemporaryDirectory(prefix='r3-tools-lock-', dir='/tmp')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.settings = dict(endpoint='http://127.0.0.1:1/v1/chat/completions', model='synthetic', timeout_seconds=2)
        self.lease = self.executor.lease_path(self.root, self.settings)

    def assert_available(self):
        import fcntl
        with self.lease.open('r+') as handle:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def assert_locked(self):
        import fcntl
        with self.lease.open('r+') as handle:
            with self.assertRaises(BlockingIOError):
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def infer(self):
        return W.infer_tool('synthetic tool', ['https://tools.example/a'], TOPICS, self.settings, workspace=self.root)

    def response(self, raw=None):
        import io
        value = {'choices':[{'message':{'content':json.dumps({'is_tool':False})}}]}
        return io.BytesIO(json.dumps(value).encode() if raw is None else raw)

    def engine(self):
        import sys
        from types import SimpleNamespace
        engine = self.executor.PiExecutor(__file__)
        engine.node = sys.executable
        owner = SimpleNamespace(root=self.root/'radar', store=SimpleNamespace(workspace=self.root))
        return engine, owner

    def start_radar(self, run):
        engine, owner = self.engine()
        errors=[]; cancel=threading.Event()
        def target():
            try: engine.execute(owner, 'synthetic-radar', 'synthetic prompt', cancel, self.settings, time.monotonic()+5)
            except BaseException as exc: errors.append(exc)
        run_patch=patch.object(engine, 'run', side_effect=run);run_patch.start();self.addCleanup(run_patch.stop)
        status_patch=patch.object(self.executor, 'backend_slots', return_value='ready');status_patch.start();self.addCleanup(status_patch.stop)
        thread=threading.Thread(target=target);thread.start()
        self.addCleanup(lambda: (cancel.set(), thread.join(6)))
        return thread, errors, cancel

    def test_same_path_function_and_exact_radar_lease_for_endpoint_model(self):
        with patch.object(self.executor, 'lease_path', wraps=self.executor.lease_path) as path:
            with W.model_lease(self.root, self.settings):
                path.assert_called_once_with(self.root, self.settings)
                self.assert_locked()
        self.assert_available()
        for change in ({'model':'synthetic-other'}, {'endpoint':'http://127.0.0.1:2/v1/chat/completions'}):
            settings={**self.settings, **change}
            self.assertNotEqual(self.lease, self.executor.lease_path(self.root, settings))

    def test_radar_held_skips_whole_model_round_without_slot_or_http_calls(self):
        acquired=threading.Event();release=threading.Event();self.addCleanup(release.set)
        def hold(*args):
            acquired.set();release.wait(4);return 'synthetic answer'
        thread,errors,_=self.start_radar(hold)
        try:
            self.assertTrue(acquired.wait(2))
            rows=[message('other'+str(i),group='合成开发群') for i in range(7)]
            with patch.object(W,'model_config',return_value=self.settings), patch.object(self.status,'backend_slots') as slots, patch.object(W.urllib.request,'build_opener') as http:
                start=time.monotonic();data,stats=W.prepare(self.root,{'2026-10-28'},rows=rows,title_check=checked)
                self.assertLess(time.monotonic()-start,.5)
                self.assertEqual(data['items'],[])
                self.assertEqual(stats['model_deferred'],7)
                self.assertEqual(stats['model_deferred_reasons'],{'model_lock_busy':7})
                slots.assert_not_called();http.assert_not_called()
        finally:release.set();thread.join(5)
        self.assertFalse(thread.is_alive());self.assertEqual(errors,[])
        self.assert_available()

    def test_tool_held_radar_keeps_existing_queue_then_runs_after_release(self):
        ran=threading.Event();radar=[]
        def http_call(*args,**kwargs):
            self.assert_locked()
            thread,errors,cancel=self.start_radar(lambda *args: (ran.set() or 'synthetic answer'))
            radar.extend((thread,errors,cancel))
            self.assertFalse(ran.wait(.1))
            return self.response()
        with patch.object(self.status,'backend_slots',return_value='ready'), patch.object(W.urllib.request,'build_opener') as opener:
            opener.return_value.open.side_effect=http_call
            self.assertEqual(self.infer(),{'is_tool':False})
        thread,errors,_=radar;thread.join(3)
        self.assertFalse(thread.is_alive());self.assertTrue(ran.is_set());self.assertEqual(errors,[])
        self.assert_available()

    def test_lock_created_with_private_permissions_before_slot_and_http(self):
        self.lease.unlink(missing_ok=True)
        self.lease.parent.rmdir()
        def slots(settings):self.assert_locked();return 'ready'
        def http(*args,**kwargs):self.assert_locked();return self.response()
        with patch.object(self.status,'backend_slots',side_effect=slots), patch.object(W.urllib.request,'build_opener') as opener:
            opener.return_value.open.side_effect=http
            self.infer()
        self.assertEqual(self.lease.stat().st_mode & 0o777,0o600)
        self.assertEqual(self.lease.parent.stat().st_mode & 0o777,0o700)
        self.assert_available()

    def test_exception_paths_release_lease(self):
        cases=[('slot',ValueError),('http',OSError),('json',ValueError),('schema',ValueError)]
        for case,error in cases:
            with self.subTest(case=case), patch.object(self.status,'backend_slots',return_value='busy' if case=='slot' else 'ready'), patch.object(W.urllib.request,'build_opener') as opener:
                if case=='http':opener.return_value.open.side_effect=OSError('synthetic failure')
                else:opener.return_value.open.return_value=self.response(b'not json' if case=='json' else json.dumps({'choices':[{'message':{'content':'{}'}}]}).encode() if case=='schema' else None)
                with self.assertRaises(error):self.infer()
            self.assert_available()

    def test_process_crash_releases_actual_tool_lease(self):
        import subprocess,sys
        script='import pathlib,time,wechat_tools as W;\nwith W.model_lease(pathlib.Path('+repr(str(self.root))+'),'+repr(self.settings)+'):\n print("READY",flush=True)\n time.sleep(30)\n'
        child_env=dict(os.environ, ARTICLE_STUDIO_WECHAT_MODULE=str(Path(self.executor.__file__).parent.parent))
        process=subprocess.Popen([sys.executable,'-u','-c',script],cwd=Path(W.__file__).parent,env=child_env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        self.addCleanup(lambda: process.poll() is None and process.kill())
        try:
            self.assertEqual(process.stdout.readline().strip(),'READY')
            self.assert_locked();process.kill();process.communicate(timeout=3)
            self.assert_available()
        finally:
            if process.poll() is None:process.kill();process.communicate(timeout=3)

    def test_curated_only_never_loads_profile_lock_or_model(self):
        with patch.object(W,'model_lease',side_effect=AssertionError('curated lock')), patch.object(W,'model_config',side_effect=AssertionError('curated profile')), patch.object(W,'infer_tool',side_effect=AssertionError('curated model')):
            data,stats=W.prepare(self.root,{'2026-10-28'},rows=[message(),message('other',group='合成其他群')],title_check=checked,curated_only=True)
        self.assertEqual(len(data['items']),1);self.assertEqual(stats['model_deferred'],0)
        self.assertFalse(self.lease.exists())

    def test_nonbusy_round_retains_five_call_cap(self):
        rows=[message('other'+str(i),group='合成其他群') for i in range(7)]
        with patch.object(W,'model_config',return_value=self.settings), patch.object(self.status,'backend_slots',return_value='ready'), patch.object(W.urllib.request,'build_opener') as opener:
            opener.return_value.open.side_effect=lambda *a,**kw:self.response()
            data,stats=W.prepare(self.root,{'2026-10-28'},rows=rows,title_check=checked)
        self.assertEqual(opener.return_value.open.call_count,5);self.assertEqual(stats['non_tools'],5);self.assertEqual(stats['model_deferred'],2)
        self.assertEqual(len(data['_evaluations']),5);self.assert_available()

    def test_lock_file_symlink_cannot_redirect_write(self):
        target=self.root/'untouched';target.write_text('synthetic original');self.lease.symlink_to(target)
        with self.assertRaises(OSError):
            with W.model_lease(self.root,self.settings):self.fail('symlink followed')
        self.assertEqual(target.read_text(),'synthetic original')

if __name__=='__main__':unittest.main()
