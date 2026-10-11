import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from contextlib import contextmanager
from isolation_patches import WECHAT_SOURCE_REASON, wechat_source_available
# hygiene-4：下面的 import 会加载微信来源插件（clue_pipeline 等在模块级调
# wechat_source_loader.source_module）。插件由 ARTICLE_STUDIO_WECHAT_MODULE 或
# workspace/sources/wechat.json 指定，两者都是本机资源，不进公开仓库；
# 干净克隆里没有它，这里直接跳过整个模块（unittest 会把 SkipTest 记成 skip
# 而不是 error）。本机配置齐全时行为与从前逐字一致。
if not wechat_source_available():
    raise unittest.SkipTest(WECHAT_SOURCE_REASON)

from wechat_radar_pipeline import RadarPipeline
from clue_pipeline import CluePipeline

class Knowledge:
    def __init__(self,path):
        self.path=path
        with self.db() as db:db.execute('CREATE TABLE documents(id TEXT PRIMARY KEY,hash TEXT,source TEXT,kind TEXT,title TEXT,body TEXT,modified TEXT,provenance TEXT,url TEXT)')
    @contextmanager
    def db(self):
        db=sqlite3.connect(self.path);db.row_factory=sqlite3.Row
        try:
            with db:yield db
        finally:db.close()

class RadarTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.store=SimpleNamespace(workspace=Path(self.temp.name),knowledge=Knowledge(Path(self.temp.name)/'kb.db'))
        self.cp=CluePipeline.__new__(CluePipeline);self.cp.store=self.store;self.cp.conversation_context=lambda docs:[]
        self.store.clue_pipeline=self.cp;self.pipeline=RadarPipeline(self.store);self.pipeline.since='2026-10-06T00:00:00+08:00'
        self.doc=self.add('a','你好，模型工作流需要原文引用。 https://example.org/news',True)
    def add(self,identity,body,own=False,modified='2026-10-06T02:00:00+00:00'):
        provenance={'chatroom_id':'g@chatroom','local_id':1,'sender':'__self__' if own else '他人','is_self':own,'conversation_type':'group'}
        doc={'id':identity,'hash':identity*64,'source':'wechat','kind':'wechat','title':'群 · 作者 · 日期','body':body,'modified':modified,'provenance':provenance,'url':''}
        with self.store.knowledge.db() as db:db.execute('INSERT INTO documents VALUES(?,?,?,?,?,?,?,?,?)',(*[doc[k] for k in ['id','hash','source','kind','title','body','modified']],json.dumps(provenance),''))
        return doc
    def result(self,refs):
        return json.dumps({'topics':[{'title':'模型引用工作流','summary':'讨论消息引用方法','refs':refs}], 'knowledge':[{'title':'原文引用方法','summary':'保留引用位置以便核对','tags':['引用'],'refs':refs}],'profile':[{'title':'引用偏好','summary':'本片段显示重视原文引用','dimension':'tech_preferences','refs':refs}]},ensure_ascii=False)
    def test_evidence_unicode_profile_and_atomic_checkpoint(self):
        material,refs,catalog=self.pipeline.prepare([self.doc])
        findings=self.pipeline.validate(self.result(['e0']),refs,catalog,{'a'})
        self.assertEqual(findings[0]['evidence'][0]['text'],self.doc['body'])
        self.assertIn('document=a',findings[0]['evidence'][0]['url'])
        self.pipeline.commit([self.doc],findings,'run','qwen');self.pipeline.commit([self.doc],findings,'run2','qwen')
        state=self.pipeline.publish();self.assertEqual(state['processed_total'],1);self.assertEqual(state['findings'],3)
        with self.pipeline.db() as db:
            topic=db.execute('SELECT * FROM trending_topics').fetchone();self.assertEqual(topic['total_mentions'],1);self.assertEqual(topic['groups_count'],1)
            self.assertEqual(db.execute('SELECT share_count FROM trending_urls').fetchone()[0],1)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM todos').fetchone()[0],0)
        profile=json.loads((self.pipeline.root/'profile/servasyy_profile.json').read_text());self.assertEqual(profile['dimensions']['tech_preferences']['conclusions'][0]['evidence'][0]['hash'],self.doc['hash'])
    def test_invalid_reference_or_non_owner_does_not_advance(self):
        _,refs,catalog=self.pipeline.prepare([self.doc])
        # An invented ref poisons that entry only; it never invents evidence and
        # the checkpoint still requires an explicit commit.
        self.assertEqual(self.pipeline.validate(self.result(['missing']),refs,catalog,{'a'}),[])
        self.assertEqual(self.pipeline.dropped_items,{'手抄引用未精确命中本批 evidence':3})
        other=self.add('b','技术意见',False);_,refs,catalog=self.pipeline.prepare([other])
        result=self.pipeline.validate(self.result(['e0']),refs,catalog,{'b'})
        self.assertEqual([f['kind'] for f in result],['topics','knowledge'])
        self.assertEqual(self.pipeline.rejected_profiles,1)
        with self.pipeline.db() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM analyzed').fetchone()[0],0)
        self.pipeline.commit([other],result,'valid-siblings','qwen')
        with self.pipeline.db() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM findings WHERE kind='profile'").fetchone()[0],0)
            self.assertEqual(db.execute('SELECT non_owner_profiles FROM validation_warnings').fetchone()[0],1)
    def test_excess_real_refs_truncate_to_fifteen_and_the_batch_advances(self):
        doc=self.add('long','技术原文'*5000,True)
        _,refs,catalog=self.pipeline.prepare([doc])
        picks=list(refs)[:20]
        findings=self.pipeline.validate(self.result(picks),refs,catalog,{'long'})
        self.assertEqual(len(findings),3)
        for finding in findings:self.assertLessEqual(len(finding['evidence']),15)
        self.assertEqual(self.pipeline.truncated_findings,3)
        self.assertEqual(self.pipeline.truncated_refs,15)
        self.assertTrue(all(e['document_id']=='long' for finding in findings for e in finding['evidence']))
        calls=[]
        def call(*args):
            calls.append(args[1])
            return self.result(picks)
        self.cp.call_pi=call
        task={'radar_since':self.pipeline.since,'body':'test'}
        with patch('wechat_radar_pipeline.backend_slots',return_value='ready'):
            status,_,out=self.pipeline.analyze('over-quota',task,threading.Event(),{'model':'qwen','context_tokens':229368,'max_output_tokens':4000},time.monotonic()+30)
        data=json.loads(out)
        self.assertEqual(status,'succeeded',out)
        self.assertEqual(len(calls),1)  # an over-quota result no longer costs a repair round
        self.assertIn('1 至 15',calls[0])
        self.assertEqual((data['truncated_findings'],data['truncated_refs']),(3,15))
        with self.pipeline.db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM findings').fetchone()[0],3)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM analyzed').fetchone()[0],2)  # setUp's sample shares the batch
            self.assertIn('引用超量已截断 3 条结果共 15 个引用',db.execute('SELECT message FROM scan_log ORDER BY rowid DESC LIMIT 1').fetchone()[0])
    def test_over_quota_refs_keep_this_batchs_own_messages_first(self):
        ctx=self.add('ctx','背景资料'*4000,False)
        self.cp.conversation_context=lambda docs:[ctx]
        material,refs,catalog=self.pipeline.prepare([self.doc])
        new_ref=next(r for r,s in refs.items() if s['id']=='a')
        others=[r for r,s in refs.items() if s['id']=='ctx']
        picks=others[:19]+[new_ref]  # the batch's own message is quoted last
        findings=self.pipeline.validate(self.result(picks),refs,catalog,{'a'})
        self.assertEqual([f['kind'] for f in findings],['topics','knowledge'])  # mixed-owner profile is still rejected
        for finding in findings:
            self.assertLessEqual(len(finding['evidence']),15)
            self.assertTrue(any(e['document_id']=='a' for e in finding['evidence']))
        self.assertEqual((self.pipeline.truncated_findings,self.pipeline.truncated_refs),(2,4))
    def test_one_bad_entry_is_dropped_and_only_unusable_output_rejects_the_batch(self):
        _,refs,catalog=self.pipeline.prepare([self.doc])
        # A poisoned ref list drops that entry; the batch is not voided.
        self.assertEqual(self.pipeline.validate(self.result(['e0','missing']),refs,catalog,{'a'}),[])
        self.assertEqual(self.pipeline.dropped_items,{'手抄引用未精确命中本批 evidence':3})
        # Only output that is not a radar object at all keeps the whole-batch rule.
        with self.assertRaisesRegex(ValueError,'结构无效'):self.pipeline.validate('"只是一段文字"',refs,catalog,{'a'})
        with self.assertRaises(ValueError):self.pipeline.validate('invalid json',refs,catalog,{'a'})
    def test_missing_top_level_lane_is_an_empty_lane_and_extra_keys_are_counted(self):
        _,refs,catalog=self.pipeline.prepare([self.doc])
        value=json.loads(self.result(['e0']));del value['profile']
        value['todos']=[{'title':'模型多给的待办'}];value['summary ']='ignored'
        findings=self.pipeline.validate(json.dumps(value,ensure_ascii=False),refs,catalog,{'a'})
        self.assertEqual([f['kind'] for f in findings],['topics','knowledge'])
        self.assertEqual(self.pipeline.format_stats['missing_top_keys'],1)
        self.assertEqual(self.pipeline.format_stats['ignored_top_keys'],2)
        self.assertEqual(self.pipeline.dropped_items,{})
        self.pipeline.commit([self.doc],findings,'run','qwen')
        with self.pipeline.db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM findings').fetchone()[0],2)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM todos').fetchone()[0],0)
    def test_entry_field_names_are_repaired_and_one_bad_entry_keeps_its_siblings(self):
        _,refs,catalog=self.pipeline.prepare([self.doc])
        value=json.loads(self.result(['e0']))
        value['topics'][0][' title']=value['topics'][0].pop('title');value['topics'][0]['confidence']=0.9
        value['knowledge'][0]['tags']='引用'
        value['profile'][0]['dimension']='not-a-dimension'
        findings=self.pipeline.validate(json.dumps(value,ensure_ascii=False),refs,catalog,{'a'})
        self.assertEqual([f['kind'] for f in findings],['topics'])
        self.assertEqual(self.pipeline.dropped_items,{'知识标签无效':1,'画像维度无效':1})
        self.assertEqual(self.pipeline.format_stats['field_names'],1)
        self.assertEqual(self.pipeline.format_stats['ignored_fields'],1)
        self.assertEqual(self.pipeline.rejected_profiles,0)
    def test_entry_missing_a_required_field_is_dropped_not_repaired(self):
        _,refs,catalog=self.pipeline.prepare([self.doc])
        value=json.loads(self.result(['e0']));del value['knowledge'][0]['summary']
        findings=self.pipeline.validate(json.dumps(value,ensure_ascii=False),refs,catalog,{'a'})
        self.assertEqual([f['kind'] for f in findings],['topics','profile'])
        self.assertEqual(self.pipeline.dropped_items,{'雷达条目缺少必需字段：summary':1})
        self.assertEqual(self.pipeline.format_stats['ignored_fields'],0)
    def test_source_change_rolls_back_entire_batch(self):
        _,refs,catalog=self.pipeline.prepare([self.doc]);findings=self.pipeline.validate(self.result(['e0']),refs,catalog,{'a'})
        with self.store.knowledge.db() as db:db.execute("UPDATE documents SET hash='changed' WHERE id='a'")
        with self.assertRaises(ValueError):self.pipeline.commit([self.doc],findings,'run','qwen')
        with self.pipeline.db() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM findings').fetchone()[0],0)
    def test_no_implicit_earlier_history_or_context_checkpoint(self):
        old=self.add('b','昨天的资料',False,'2026-10-05T03:00:00+00:00')
        self.cp.conversation_context=lambda docs:[old]
        material,refs,catalog=self.pipeline.prepare([self.doc]);self.assertNotIn('b',catalog)
        self.pipeline.commit([self.doc],[],'run','qwen')
        self.assertEqual(self.pipeline.pending({'radar_since':self.pipeline.since},'qwen'),[])
    def test_model_failure_is_transient_and_the_same_input_is_retried(self):
        def broken(*args):raise ValueError('Pi 分析未完成，本批未推进：模型无响应')
        self.cp.call_pi=broken
        task={'radar_since':self.pipeline.since,'body':'test'};config={'model':'qwen','context_tokens':229368,'max_output_tokens':32768}
        status,code,output=self.pipeline.analyze('run',task,threading.Event(),config,time.monotonic()+30)
        data=json.loads(output)
        self.assertEqual(status,'failed');self.assertEqual(data['error_kind'],'model_unresponsive')
        self.assertTrue(self.pipeline.pending(task,'qwen'))  # a model failure is never quarantined
        with self.pipeline.db() as db:
            self.assertEqual(db.execute('SELECT error_kind FROM failures').fetchone()[0],'model_unresponsive')
            self.assertEqual(db.execute('SELECT error_kind FROM scan_log').fetchone()[0],'model_unresponsive')
    def test_second_invalid_answer_is_quarantined_instead_of_replaying_every_hour(self):
        calls=[]
        self.cp.call_pi=lambda *args:(calls.append(args[1]),'invalid json')[1]
        task={'radar_since':self.pipeline.since,'body':'test'};config={'model':'qwen','context_tokens':229368,'max_output_tokens':32768}
        status,code,output=self.pipeline.analyze('quarantine-run',task,threading.Event(),config,time.monotonic()+30)
        data=json.loads(output)
        self.assertEqual((status,data['quarantined'],data['error_kind']),('partial',1,'quarantined'))
        self.assertEqual(len(calls),2)  # one call plus one bounded repair, then stop
        self.assertEqual(self.pipeline.pending(task,'qwen'),[])  # the same batch is no longer replayed
        view=self.pipeline.quarantine_list();self.assertEqual(view['schema'],'radar-quarantine/v1')
        row=view['items'][0]
        self.assertEqual((row['document_id'],row['error_kind'],row['run_id']),('a','validation','quarantine-run'))
        self.assertEqual(len(row['hash']),64);self.assertTrue(row['at']);self.assertIn('document=a',row['url'])
        self.assertEqual(view['counts'],{'validation':1})
        # A later run spends no model call on the quarantined batch.
        self.cp.call_pi=lambda *args:self.fail('quarantined batch must not be re-sent')
        status,_,out=self.pipeline.analyze('next-run',task,threading.Event(),config,time.monotonic()+30)
        self.assertEqual((status,json.loads(out)['processed']),('succeeded',0))
        # Manual retry only re-queues; the source text and existing results stay.
        self.assertEqual(self.pipeline.retry_quarantined(['a']),1)
        self.assertEqual([d['id'] for d in self.pipeline.pending(task,'qwen')],['a'])
        self.assertEqual(self.pipeline.quarantine_list()['items'],[])
    def test_quarantine_view_and_retry_validate_their_arguments(self):
        for limit in (0,201,True,'5'):
            with self.subTest(limit=limit),self.assertRaises(ValueError):self.pipeline.quarantine_list(limit)
        for ids in ('a',[''],['x'*201],[]):
            with self.subTest(ids=ids),self.assertRaises(ValueError):self.pipeline.retry_quarantined(ids)
        self.assertEqual(self.pipeline.retry_quarantined(),0)

    def test_busy_service_waits_without_sending_source_or_advancing_checkpoint(self):
        self.cp.call_pi=lambda *args:self.fail('busy backend must not receive source text')
        cancel=threading.Event();cancel.wait=lambda seconds:cancel.set()
        task={'radar_since':self.pipeline.since,'body':'test'}
        with patch('wechat_radar_pipeline.backend_slots',return_value='busy'):
            state,code,out=self.pipeline.analyze('run',task,cancel,{'model':'qwen'},time.monotonic()+30)
        self.assertEqual(state,'cancelled');self.assertEqual(json.loads(out)['processed'],0)
        self.assertTrue(self.pipeline.pending(task,'qwen'))
    def test_xml_auth_not_sent_and_no_fabricated_url_counts(self):
        doc=self.add('b','<msg><appmsg><title>公开标题</title><url>https://example.org/a</url><authkey>secret-value</authkey></appmsg></msg>')
        material,refs,catalog=self.pipeline.prepare([doc]);self.assertNotIn('secret-value',json.dumps(material));self.assertNotIn('https://example.org/a',json.dumps(material))
        self.pipeline.publish_links()
        with self.pipeline.db() as db:self.assertEqual(db.execute("SELECT share_count FROM trending_urls WHERE url='https://example.org/a'").fetchone()[0],1)

    def test_local_link_projection_preserves_encoded_xml_and_rejects_secret_urls(self):
        doc=self.add('b','<msg><appmsg><title>原始分享标题</title><url>https://example.org/a?x=1&amp;y=2</url><shareurl>https://example.org/private?token=secret</shareurl></appmsg></msg>')
        self.pipeline.publish_links()
        with self.pipeline.db() as db:
            rows=db.execute('SELECT * FROM radar_links').fetchall()
            row=next(r for r in rows if r['document_id']=='b')
            self.assertEqual(row['url'],'https://example.org/a?x=1&y=2')
            self.assertEqual(row['title'],'原始分享标题')
            self.assertEqual(doc['body'][row['start']:row['end']],row['text'])
            self.assertEqual(row['hash'],doc['hash'])
            self.assertFalse(any('token=' in r['url'] for r in rows))

    def test_digest_counts_same_message_once_for_multiple_findings(self):
        _,refs,catalog=self.pipeline.prepare([self.doc]);findings=self.pipeline.validate(self.result(['e0']),refs,catalog,{'a'})
        second={**findings[1],'title':'同一消息的另一知识点'};findings.append(second)
        self.pipeline.commit([self.doc],findings,'run','qwen');self.pipeline.publish()
        with self.pipeline.db() as db:
            digest=json.loads(db.execute('SELECT content FROM digests').fetchone()[0])
            self.assertEqual(len(digest['groups'][0]['items']),2)
            self.assertEqual(digest['groups'][0]['useful_messages'],1)

    # ── radar-batch-hardening：时间预算、refs 上限、逐条丢弃、观测、空批次 ──────────

    def test_run_limit_reserves_room_for_the_call_and_its_repair(self):
        seen=[]
        def answer(*args):
            identity,prompt,cancel,settings,call_deadline=args
            seen.append({'timeout':settings['timeout_seconds'],'left':call_deadline-time.monotonic()})
            return self.result(['e0'])
        self.cp.call_pi=answer
        task={'radar_since':self.pipeline.since,'body':'test'}
        with patch('wechat_radar_pipeline.backend_slots',return_value='ready'):
            status,_,out=self.pipeline.analyze('budget',task,threading.Event(),
                {'model':'qwen','timeout_seconds':100,'context_tokens':229368,'max_output_tokens':4000},time.monotonic()+1800)
        self.assertEqual(status,'succeeded',out)
        self.assertEqual(len(seen),1)
        # The call may take its full timeout and a repair another one; a call is never
        # started when the run cannot still fit both.
        self.assertGreaterEqual(seen[0]['timeout'],180)
        self.assertGreaterEqual(seen[0]['left'],2*seen[0]['timeout'])

    def test_a_short_run_limit_shrinks_the_call_instead_of_starting_an_uncuttable_one(self):
        seen=[]
        self.cp.call_pi=lambda *args:(seen.append(args[3]['timeout_seconds']),self.result(['e0']))[1]
        task={'radar_since':self.pipeline.since,'body':'test'}
        with patch('wechat_radar_pipeline.backend_slots',return_value='ready'):
            status,_,out=self.pipeline.analyze('short',task,threading.Event(),{'model':'qwen'},time.monotonic()+30)
        self.assertEqual(status,'succeeded',out)
        self.assertLess(seen[0],180)  # shrunk so a started call can still finish in this run

    def test_batch_refs_are_capped_at_sixty_and_the_cut_is_counted(self):
        # The serialized-material budget usually narrows a batch first; this ceiling is
        # the second guard, and a cut is counted instead of being hidden from the run.
        wide=self.add('wide','技术原文'*21000,True)
        material,refs,catalog=self.pipeline.prepare([wide],60)
        self.assertLessEqual(len(refs),60)
        self.assertGreater(self.pipeline.refs_dropped,0)
        self.assertTrue(all(row['evidence'] and len(row['evidence'])<=60 for row in material))
        # Without the ceiling the same document would hand the model far more windows.
        _,uncapped,_=self.pipeline.prepare([wide])
        self.assertGreater(len(uncapped),60)

    def test_oversized_or_non_list_lane_is_dropped_without_voiding_the_batch(self):
        _,refs,catalog=self.pipeline.prepare([self.doc])
        value=json.loads(self.result(['e0']))
        value['knowledge']=[{'title':'第%d条'%n,'summary':'可复用知识','tags':['引用'],'refs':['e0']} for n in range(45)]
        value['profile']='不是列表'
        findings=self.pipeline.validate(json.dumps(value,ensure_ascii=False),refs,catalog,{'a'})
        kinds=[f['kind'] for f in findings]
        self.assertEqual(kinds.count('knowledge'),40)  # 45 arrive, 40 are kept
        self.assertIn('topics',kinds)
        self.assertEqual(self.pipeline.dropped_items.get('雷达单栏超过 40 条已截断'),5)
        self.assertEqual(self.pipeline.dropped_items.get('雷达profile 输出不是列表'),1)

    def test_prose_wrapped_json_is_recovered_before_a_repair_is_needed(self):
        _,refs,catalog=self.pipeline.prepare([self.doc])
        findings=self.pipeline.validate('我先分析一下：\n'+self.result(['e0'])+'\n以上。',refs,catalog,{'a'})
        self.assertEqual([f['kind'] for f in findings],['topics','knowledge','profile'])

    def test_batch_without_any_evidence_window_never_reaches_the_model(self):
        with self.store.knowledge.db() as db:db.execute("DELETE FROM documents WHERE id='a'")
        self.add('blank','',True)
        self.cp.call_pi=lambda *args:self.fail('an empty batch must not be sent to the model')
        task={'radar_since':self.pipeline.since,'body':'test'}
        with patch('wechat_radar_pipeline.backend_slots',return_value='ready'):
            status,_,out=self.pipeline.analyze('blank',task,threading.Event(),{'model':'qwen','context_tokens':229368,'max_output_tokens':4000},time.monotonic()+300)
        data=json.loads(out);self.assertEqual(status,'succeeded',out)
        self.assertGreaterEqual(data['processed'],1)
        with self.pipeline.db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM batch_metrics').fetchone()[0],0)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM analyzed').fetchone()[0],1)

    def test_each_batch_records_prompt_size_refs_and_duration(self):
        self.cp.call_pi=lambda *args:self.result(['e0'])
        task={'radar_since':self.pipeline.since,'body':'test'}
        with patch('wechat_radar_pipeline.backend_slots',return_value='ready'):
            status,_,out=self.pipeline.analyze('metrics',task,threading.Event(),{'model':'qwen','context_tokens':229368,'max_output_tokens':4000},time.monotonic()+300)
        data=json.loads(out);self.assertEqual(status,'succeeded',out)
        with self.pipeline.db() as db:rows=[dict(r) for r in db.execute('SELECT * FROM batch_metrics')]
        self.assertEqual(len(rows),1);row=rows[0]
        self.assertEqual((row['run_id'],row['batch'],row['error_kind']),('metrics',1,''))
        self.assertGreater(row['docs'],0);self.assertGreater(row['refs'],0)
        self.assertGreater(row['prompt_chars'],100);self.assertGreaterEqual(row['duration_ms'],0)
        self.assertEqual(data['batches'],1)

if __name__=='__main__':unittest.main()
