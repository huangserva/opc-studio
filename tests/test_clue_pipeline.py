import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from datetime import datetime,timezone
from server import Store
from knowledge_store import Knowledge
from isolation_patches import WECHAT_SOURCE_REASON, wechat_source_available
# hygiene-4：下面的 import 会加载微信来源插件（clue_pipeline 等在模块级调
# wechat_source_loader.source_module）。插件由 ARTICLE_STUDIO_WECHAT_MODULE 或
# workspace/sources/wechat.json 指定，两者都是本机资源，不进公开仓库；
# 干净克隆里没有它，这里直接跳过整个模块（unittest 会把 SkipTest 记成 skip
# 而不是 error）。本机配置齐全时行为与从前逐字一致。
if not wechat_source_available():
    raise unittest.SkipTest(WECHAT_SOURCE_REASON)

from clue_pipeline import CluePipeline
from scheduled_tasks import ScheduledTasks
from isolation_patches import module_time
from wechat_source_loader import source_module

_clue_source = source_module('clue_pipeline')

class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.store=Store(self.tmp.name)
        self.store._knowledge=Knowledge(self.store,sources=[]);self.pipeline=CluePipeline(self.store)
        self.doc=self.add('m1','😀合成技术：开源检索工具新增引用功能。')
        self.config={'model':'synthetic','max_output_tokens':32768,'timeout_seconds':100};self.sequence=0
    def tearDown(self):self.tmp.cleanup()
    def add(self,key,body,source='wechat',chat='synthetic-chat'):
        with self.store.knowledge.db() as db:
            db.execute('CREATE TEMP TABLE seen(id TEXT PRIMARY KEY)')
            self.store.knowledge._write(db,source,key,'wechat','合成原文',body,modified=datetime.now(timezone.utc).isoformat(),provenance={'chatroom_id':chat,'timestamp':datetime.now(timezone.utc).timestamp()})
        return self.store.knowledge.document(self.store.knowledge.search(body)['results'][0]['id'])
    def answer(self,doc=None,**kw):
        d=doc or self.doc
        c={'title':'合成工具更新','summary':'群友谈到检索引用功能，尚未实测。','relevance':'可作为技术调研线索','categories':['technology'],'kind':'information','priority':'normal','quotes':[{'ref':'e0'}]};c.update(kw)
        return json.dumps({'clues':[c]},ensure_ascii=False)
    def run_analysis(self,answer=None,cancel=None):
        self.pipeline.call_pi=lambda *args:answer if answer is not None else self.answer()
        identity=('abcdef'[self.sequence%6])*32;self.sequence+=1
        return self.pipeline.analyze(identity,{'body':'关注五类线索','execution_trigger':'manual'},cancel or threading.Event(),self.config,time.monotonic()+30)
    def test_isolated_cache_to_model_to_frozen_evidence_and_checkpoint(self):
        result=self.run_analysis();self.assertEqual(result[0],'succeeded')
        d=self.store.clues.detail(json.loads(result[2])['clues'][0]);self.assertEqual(d['categories'],['technology']);self.assertEqual(d['status'],'candidate')
        quote=d['entries'][0]['evidence'][0];self.assertEqual(quote['excerpt'],self.doc['body']);self.assertEqual(quote['start'],0);self.assertEqual(quote['end'],len(self.doc['body']))
        self.assertEqual(quote['analysis']['run_id'],'a'*32);self.assertEqual(self.pipeline.status()['processed_versions'],1)
        self.assertEqual(self.store.events.list_events()['events'],[])
        self.assertEqual(json.loads(self.run_analysis()[2])['processed'],0)
    def test_no_clues_still_records_actual_processed_range(self):
        result=self.run_analysis('{"clues":[]}');self.assertEqual(json.loads(result[2])['clues'],[])
        self.assertEqual(self.pipeline.status()['processed_versions'],1);self.assertEqual(self.store.clues.listing()['total'],0)
    def test_invalid_json_or_invented_quote_cannot_advance(self):
        # Unparseable output is the only format case that still defers the batch.
        result=self.run_analysis('not json');self.assertEqual(result[0],'failed');self.assertTrue(json.loads(result[2])['errors'])
        self.assertEqual(self.pipeline.status()['processed_versions'],0);self.assertEqual(self.store.clues.listing()['total'],0)
        # A hand-copied or invented quote drops only that clue: nothing is saved,
        # the message stays unprocessed and returns to the retry queue.
        result=self.run_analysis(self.answer(quotes=[{'id':self.doc['id'],'text':'模型杜撰的原文'}]));data=json.loads(result[2])
        self.assertEqual(len(data['dropped']),1);self.assertEqual(data['deferred'],1)
        self.assertEqual(self.pipeline.status()['processed_versions'],0);self.assertEqual(self.store.clues.listing()['total'],0)
    def test_a_message_is_marked_handled_only_after_three_cumulative_drops(self):
        # Every pass drops the same unusable clue and returns the message to the
        # existing retry queue; only the third drop stops retrying it.
        answer=self.answer(quotes=[{'id':'f'*32,'text':'外部资料'}])
        for attempts in (1,2):
            data=json.loads(self.run_analysis(answer)[2])
            self.assertEqual(data['dropped'][0]['attempts'],attempts)
            self.assertEqual(data['dropped'][0]['state'],'waiting')
            self.assertEqual(data['deferred'],1)
            self.assertEqual(self.pipeline.status()['processed_versions'],0)
            self.assertEqual(self.store.clues.listing()['total'],0)
        data=json.loads(self.run_analysis(answer)[2])
        self.assertEqual(data['dropped'][0]['attempts'],3)
        self.assertEqual(data['dropped'][0]['state'],'handled')
        self.assertEqual(self.pipeline.status()['processed_versions'],1)
        self.assertEqual(self.store.clues.listing()['total'],0)
        with self.pipeline.db() as db:
            # The drop record stays even though the message stops being retried.
            self.assertEqual(db.execute('SELECT state FROM failed_batches').fetchone()['state'],'resolved')
            self.assertEqual(db.execute('SELECT COUNT(*) FROM retry_documents').fetchone()[0],0)
    def test_invalid_category_drops_only_that_clue(self):
        result=self.run_analysis(self.answer(categories=['invalid']));data=json.loads(result[2])
        self.assertEqual(self.store.clues.listing()['total'],0)
        self.assertEqual(len(data['dropped']),1);self.assertEqual(data['deferred'],1)
        self.assertIn('模型分类无效',data['dropped'][0]['reason'])
        self.assertEqual(data['format']['dropped_clues'],1)
        self.assertEqual(self.pipeline.status()['processed_versions'],0)
    def test_injected_action_field_is_ignored_counted_and_never_stored(self):
        result=self.run_analysis(self.answer(actions=['自动发送邮件'],confidence=0.9));data=json.loads(result[2])
        self.assertEqual(data['format']['ignored_fields'],2)
        self.assertEqual(len(data['clues']),1)
        clue=self.store.clues.detail(data['clues'][0])
        self.assertNotIn('actions',clue);self.assertNotIn('confidence',clue)
        self.assertEqual(clue['status'],'candidate')
        self.assertEqual(self.store.events.list_events()['events'],[])
    def test_chat_discussion_cannot_merge_different_chats(self):
        other=self.add('m2','另一群的合成消息',chat='different')
        with self.assertRaises(ValueError):self.pipeline.parse(self.answer(kind='discussion',quotes=[{'id':self.doc['id'],'text':self.doc['body']},{'id':other['id'],'text':other['body']}]),[self.doc,other])
    def test_current_hash_change_conflicts_without_false_checkpoint(self):
        def model(*args):
            self.add('m1','合成原文已更新。');return self.answer()
        self.pipeline.call_pi=model
        result=self.pipeline.analyze('a'*32,{'body':'范围'},threading.Event(),self.config,time.monotonic()+30)
        self.assertEqual(result[0],'failed');self.assertTrue(json.loads(result[2])['errors'])
        self.assertEqual(self.pipeline.status()['processed_versions'],0)
    def test_model_failure_and_cancellation_leave_source_and_checkpoint(self):
        self.pipeline.call_pi=lambda *args:(_ for _ in ()).throw(ValueError('模拟模型中断'))
        result=self.pipeline.analyze('a'*32,{'body':'范围'},threading.Event(),self.config,time.monotonic()+30)
        self.assertEqual(result[0],'failed');self.assertTrue(json.loads(result[2])['errors'])
        self.assertEqual(self.pipeline.status()['processed_versions'],0)
        stop=threading.Event();stop.set();self.assertEqual(self.run_analysis(cancel=stop)[0],'cancelled')
    def test_batch_cap_only_wechat_and_private_receipts(self):
        for i in range(31):self.add('m'+str(i+2),'合成消息 '+str(i))
        self.add('mail','合成邮件不是本次范围',source='gmail')
        result=self.run_analysis('{"clues":[]}');self.assertEqual(json.loads(result[2])['processed'],30);self.assertTrue(json.loads(result[2])['has_more'])
        self.assertEqual(self.pipeline.path.stat().st_mode&0o777,0o600)
    def test_retry_policy_does_not_change_success_checkpoint(self):
        self.run_analysis()
        with self.pipeline.db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM retry_policy').fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM seen').fetchone()[0], 1)
        self.assertEqual(self.store.clues.listing()['total'], 1)

    def test_repeat_batch_commit_recovers_without_duplicate_clue(self):
        self.run_analysis()
        with self.pipeline.db() as db:db.execute('DELETE FROM seen')
        def forbidden(*args):raise AssertionError('不应重复向模型发送已完成批次')
        self.pipeline.call_pi=forbidden
        result=self.pipeline.analyze('b'*32,{'body':'范围'},threading.Event(),self.config,time.monotonic()+30)
        self.assertEqual(result[0],'succeeded');self.assertEqual(self.store.clues.listing()['total'],1)
    def test_failed_batch_deferred_without_blocking_new_messages(self):
        for i in range(11):self.add('batch'+str(i),'合成消息 '+str(i))
        calls=[]
        def model(identity,*args):
            calls.append(identity)
            if identity.endswith('-0'):return '{"clues":[]}'
            raise ValueError('模拟接口失败')
        self.pipeline.call_pi=model
        result=self.pipeline.analyze('a'*32,{'body':'范围'},threading.Event(),self.config,time.monotonic()+30)
        self.assertEqual(result[0],'partial');self.assertEqual(json.loads(result[2])['deferred'],2)
        self.assertEqual(self.pipeline.status()['processed_versions'],10)
        newer=self.add('new-after-failure','失败后新增消息，不属于原批次')
        scope,remaining=self.pipeline.scoped_pending('b'*32,'synthetic')
        self.assertEqual(scope,'b'*32);self.assertEqual([d['id'] for d in remaining],[newer['id']])
        self.pipeline.call_pi=lambda *args:'{"clues":[]}'
        result=self.pipeline.analyze('b'*32,{'body':'范围'},threading.Event(),self.config,time.monotonic()+30)
        self.assertEqual(json.loads(result[2])['processed'],1);self.assertEqual(result[0],'partial')
        result=self.pipeline.analyze('c'*32,{'body':'范围','execution_trigger':'manual'},threading.Event(),self.config,time.monotonic()+30)
        self.assertEqual(json.loads(result[2])['processed'],2);self.assertEqual(result[0],'succeeded')
        self.assertFalse(json.loads(result[2])['has_more'])
    def test_invalid_output_retry_uses_same_batch_and_preserves_validation(self):
        calls=[]
        def model(identity,prompt,*args):
            calls.append((identity,prompt))
            return 'not json' if len(calls)==1 else self.answer()
        self.pipeline.call_pi=model
        result=self.pipeline.analyze('a'*32,{'body':'范围'},threading.Event(),self.config,time.monotonic()+30)
        self.assertEqual(result[0],'succeeded');self.assertEqual(len(calls),2)
        self.assertIn('-repair',calls[1][0]);self.assertTrue(calls[1][1].startswith(calls[0][1]))
    def test_one_bad_clue_is_dropped_without_a_second_model_call(self):
        calls=[]
        def model(identity,prompt,*args):
            calls.append((identity,prompt));return self.answer(quotes=[])
        self.pipeline.call_pi=model
        result=self.pipeline.analyze('a'*32,{'body':'范围'},threading.Event(),self.config,time.monotonic()+30)
        data=json.loads(result[2])
        self.assertEqual(len(calls),1)
        self.assertEqual(data['format']['dropped_clues'],1);self.assertEqual(data['deferred'],1)
        self.assertEqual(self.store.clues.listing()['total'],0)
        self.assertEqual(self.pipeline.status()['processed_versions'],0)
    def test_incremental_captures_before_pi_and_continues_beyond_thirty(self):
        for i in range(44):self.add('inc'+str(i),'增量文本 '+str(i))
        calls=[]
        def capture(*args):calls.append('capture');return {'index_status':'ready'}
        def model(*args):calls.append('model');return '{"clues":[]}'
        self.pipeline.prepare_cache=capture;self.pipeline.call_pi=model
        result=self.pipeline.analyze('a'*32,{'body':'范围','wechat_mode':'incremental'},threading.Event(),self.config,time.monotonic()+30)
        data=json.loads(result[2]);self.assertEqual(data['processed'],45);self.assertFalse(data['has_more'])
        self.assertEqual(calls[0],'capture');self.assertGreater(calls.count('model'),3)
        self.assertEqual(self.pipeline.status()['processed_versions'],45)
    def test_failed_capture_does_not_send_old_cache_or_checkpoint(self):
        self.pipeline.prepare_cache=lambda *args:(_ for _ in ()).throw(ValueError('模拟索引失败'))
        self.pipeline.call_pi=lambda *args:self.fail('采集失败不能分析旧缓存')
        result=self.pipeline.analyze('a'*32,{'body':'范围','wechat_mode':'incremental'},threading.Event(),self.config,time.monotonic()+30)
        self.assertEqual(result[0],'failed');self.assertIn('索引失败',json.loads(result[2])['errors'][0]['message'])
        self.assertEqual(self.pipeline.status()['processed_versions'],0)
    def test_capture_failure_can_consume_only_previously_verified_cache(self):
        from types import SimpleNamespace
        stamp=datetime.now(timezone.utc).isoformat()
        with self.store.knowledge.db() as db:
            db.execute("INSERT INTO sources(id,config,status) VALUES('wechat','{}','ready')")
        state={'available':True,'status':'error','busy':False,'detail':'模拟新采集失败','last_success':{'finished_at':stamp,'index_status':'ready'}}
        self.store.knowledge.capture=SimpleNamespace(status=lambda:state,_source=lambda:{'enabled':True},retry_after=time.monotonic()+100)
        result=self.pipeline.prepare_cache(threading.Event(),time.monotonic()+30)
        self.assertFalse(result['fresh']);self.assertEqual(result['warning'],'模拟新采集失败')
        self.assertEqual(result['last_success']['finished_at'],stamp)
        with self.store.knowledge.db() as db:db.execute("UPDATE sources SET status='error' WHERE id='wechat'")
        with self.assertRaises(ValueError):self.pipeline.prepare_cache(threading.Event(),time.monotonic()+30)

    def test_time_budget_yields_and_next_run_resumes_without_fixed_quota(self):
        for i in range(44):self.add('time'+str(i),'待续文本 '+str(i))
        self.pipeline.prepare_cache=lambda *args:{'index_status':'ready'}
        clock=[0]
        def model(*args):clock[0]+=20;return '{"clues":[]}'
        self.pipeline.call_pi=model
        with module_time(_clue_source,monotonic=lambda:clock[0]):
            result=self.pipeline.analyze('a'*32,{'body':'范围','wechat_mode':'incremental'},threading.Event(),self.config,45)
        data=json.loads(result[2]);self.assertEqual(result[0],'partial');self.assertTrue(data['time_budget']);self.assertEqual(data['processed'],20);self.assertTrue(data['has_more'])
        self.pipeline.call_pi=lambda *args:'{"clues":[]}'
        result=self.pipeline.analyze('b'*32,{'body':'范围','wechat_mode':'incremental'},threading.Event(),self.config,time.monotonic()+30)
        self.assertEqual(json.loads(result[2])['processed'],25);self.assertEqual(self.pipeline.status()['processed_versions'],45)
    def test_media_authentication_material_is_not_sent_to_model(self):
        self.add('media','<msg><img aeskey="synthetic-secret" cdnmidimgurl="synthetic-url"/></msg>')
        self.add('article','<msg><appmsg><title>合成文章标题</title><url>https://example.test/?token=synthetic-token</url></appmsg></msg>')
        prompts=[]
        self.pipeline.call_pi=lambda identity,prompt,*args:(prompts.append(prompt) or '{"clues":[]}')
        result=self.pipeline.analyze('a'*32,{'body':'范围'},threading.Event(),self.config,time.monotonic()+30)
        self.assertEqual(json.loads(result[2])['skipped'],1);self.assertIn('合成文章标题',''.join(prompts))
        for secret in ('synthetic-secret','synthetic-token','synthetic-url'):self.assertNotIn(secret,''.join(prompts))
    def test_incremental_batches_keep_conversation_and_previous_reply_together(self):
        previous=self.add('previous','不需要赏金，但是推文尚未确认',chat='business')
        self.pipeline.checkpoint([previous],'old')
        self.add('new-business','可以合作推文，价格待议',chat='business')
        self.add('unrelated','不相干的新技术消息',chat='other')
        self.pipeline.prepare_cache=lambda *args:{'index_status':'ready'}
        material=[]
        def model(identity,prompt,*args):
            items=json.loads(prompt.split('<source_material>')[1].split('</source_material>')[0]);material.append(items)
            self.assertEqual(len({d['chatroom_id'] for d in items}),1)
            return '{"clues":[]}'
        self.pipeline.call_pi=model
        self.pipeline.analyze('a'*32,{'body':'范围','wechat_mode':'incremental'},threading.Event(),self.config,time.monotonic()+30)
        business=next(items for items in material if items[0]['chatroom_id']=='business')
        self.assertTrue(any(d['id']==previous['id'] and d['context'] for d in business))

    def test_context_only_findings_do_not_duplicate_or_block_new_messages(self):
        self.pipeline.checkpoint([self.doc],'old')
        self.add('later','普通回复收到，没有新进展')
        self.pipeline.prepare_cache=lambda *args:{'index_status':'ready'}
        self.pipeline.call_pi=lambda *args:self.answer()
        result=self.pipeline.analyze('a'*32,{'body':'范围','wechat_mode':'incremental'},threading.Event(),self.config,time.monotonic()+30)
        self.assertEqual(result[0],'succeeded');self.assertEqual(json.loads(result[2])['processed'],1)
        self.assertEqual(self.store.clues.listing()['total'],0)

    def test_reference_selection_preserves_invisible_spaces_and_rejects_unknown_refs(self):
        doc=self.add('spacing','@Allen\u2005@nana\u2005各位老师，我也交稿了\n收到 收到')
        refs=self.pipeline.evidence_refs([doc]);key=next(iter(refs))
        candidate=self.pipeline.parse(self.answer(doc,quotes=[{'ref':key}]),[doc],refs)[0]
        pick=candidate['picks'][0];self.assertEqual(doc['body'][pick['start']:pick['end']],doc['body'])
        with self.assertRaises(ValueError):self.pipeline.parse(self.answer(doc,quotes=[{'ref':'nonexistent'}]),[doc],refs)

    def test_model_reference_selection_is_frozen_and_hash_checked_before_saving(self):
        refs=self.pipeline.evidence_refs([self.doc]);key=next(iter(refs))
        result=self.run_analysis(self.answer(quotes=[{'ref':key}]))
        clue=self.store.clues.detail(json.loads(result[2])['clues'][0])
        self.assertEqual(clue['entries'][0]['evidence'][0]['excerpt'],self.doc['body'])

    def test_specialized_task_has_no_command_or_generic_output_permission(self):
        from types import SimpleNamespace
        s=ScheduledTasks(self.tmp.name,autostart=False);s.assistant=SimpleNamespace(model_document=lambda:{'profiles':[{'id':'synthetic','config':{'model':'synthetic','endpoint':'http://127.0.0.1:1/v1/chat/completions','no_api_key':True}}]})
        task=dict(name='合成微信分析',kind='wechat_clues',body='五类',cwd=self.tmp.name,frequency='interval',minutes=60,timeout=5,enabled=False,allow_commands=False,profile_id='synthetic')
        try:
            self.assertEqual(s.validate(task)['wechat_mode'],'once')
            self.assertEqual(s.validate({**task,'wechat_mode':'incremental'})['wechat_mode'],'incremental')
            with self.assertRaises(ValueError):s.validate({**task,'wechat_mode':'unbounded'})
            for extra in [{'allow_commands':True},{'result_destination':'clues'},{'clue_id':'a'*32}]:
                with self.assertRaises(ValueError):s.validate({**task,**extra})
        finally:s.close()

if __name__=='__main__':unittest.main()
