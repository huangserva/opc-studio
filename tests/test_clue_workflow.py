"""Synthetic conversations only; no external model, collector or real messages."""
import json
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
import test_clue_pipeline as fixtures
import wechat_clue_context as workflow
import wechat_daily_review as daily
from clue_pipeline import CluePipeline
from scheduled_tasks import ScheduledTasks


class WorkflowTests(unittest.TestCase):
    setUp=fixtures.PipelineTests.setUp
    tearDown=fixtures.PipelineTests.tearDown
    add=fixtures.PipelineTests.add

    def previous(self,body='合成产品：合作需求为制作一次演示。',chat='synthetic-chat'):
        doc=self.add('old',body,chat=chat)
        picks=[{'id':doc['id'],'hash':doc['hash'],'start':0,'end':min(12,len(body))}]
        clue=self.store.clues._save({'title':'人工整理的项目','summary':'已核对的原始需求','relevance':'合成测试',
             'categories':['projects'],'status':'verified','tracking':True,'kind':'information','priority':'important'},self.store.events._capture(picks))
        return doc,clue

    def candidate(self,old,new,clue):
        existing,anchors=workflow.existing_clues(self.pipeline,[new])
        docs=[old,new];refs=self.pipeline.evidence_refs(docs);workflow.bind_history_refs(self.pipeline,existing,docs,refs)
        old_ref=next(k for k,v in refs.items() if v['id']==old['id'] and v['end']==12)
        new_ref=next(k for k,v in refs.items() if v['id']==new['id'])
        value={'title':'模型提议改名','summary':'对方补充了新的演示验收要求。','relevance':'合成变化',
           'categories':['other'],'kind':'information','priority':'normal','quotes':[{'ref':new_ref}],
           'continuation':{'id':clue['id'],'revision':clue['revision'],'refs':[old_ref,new_ref]}}
        return value,existing,docs,refs

    def test_continuation_appends_evidence_preserves_verified_state_and_deduplicates(self):
        old,clue=self.previous();new=self.add('new','接上次产品演示，请补充截图验收。')
        value,existing,docs,refs=self.candidate(old,new,clue)
        parsed=self.pipeline.parse(json.dumps({'clues':[value]},ensure_ascii=False),docs,refs,existing,{new['id']})[0]
        snaps=self.store.events._capture(parsed['picks'])
        saved=workflow.persist_candidate(self.pipeline,parsed,snaps,'synthetic-producer',existing)
        detail=self.store.clues.detail(saved['id'])
        self.assertEqual(detail['revision'],2);self.assertEqual(detail['title'],'人工整理的项目')
        self.assertEqual(detail['categories'],['projects']);self.assertEqual(detail['status'],'verified');self.assertTrue(detail['tracking'])
        self.assertEqual(detail['entries'][0]['note'],value['summary'])
        self.assertEqual(len(detail['entries'][0]['evidence']),1)
        repeated=workflow.persist_candidate(self.pipeline,parsed,snaps,'synthetic-producer',existing)
        self.assertEqual(repeated['id'],saved['id']);self.assertEqual(self.store.clues.detail(saved['id'])['revision'],2)
        self.assertEqual(self.store.events.list_events()['events'],[])

    def test_continuation_rejects_unknown_revision_without_old_proof_or_cross_chat(self):
        old,clue=self.previous();new=self.add('new','接上次产品演示，补充截图验收。')
        value,existing,docs,refs=self.candidate(old,new,clue)
        for change in ({'id':'f'*32},{'revision':3},{'revision':True}):
            broken=json.loads(json.dumps(value));broken['continuation'].update(change)
            with self.assertRaises(ValueError):self.pipeline.parse(json.dumps({'clues':[broken]}),docs,refs,existing,{new['id']})
        broken=json.loads(json.dumps(value));broken['continuation']['refs']=[broken['quotes'][0]['ref']]
        with self.assertRaises(ValueError):self.pipeline.parse(json.dumps({'clues':[broken]}),docs,refs,existing,{new['id']})
        with self.assertRaises(ValueError):workflow.validate_continuation(value['continuation'],[
          {'id':old['id'],'hash':old['hash'],'start':0,'end':12},
          {'id':new['id'],'hash':new['hash'],'start':0,'end':5}],{old['id']:old,new['id']:{**new,'provenance':{'chatroom_id':'other'}}},existing,{new['id']})

    def test_manual_concurrent_edit_conflicts_instead_of_overwrite(self):
        old,clue=self.previous();new=self.add('new','产品演示验收补充。')
        value,existing,docs,refs=self.candidate(old,new,clue)
        parsed=self.pipeline.parse(json.dumps({'clues':[value]}),docs,refs,existing,{new['id']})[0]
        self.store.clues._save({'id':clue['id'],'revision':1,'title':'人工的新标题','reason':'合成人工修改'},[])
        with self.assertRaises(ValueError):workflow.persist_candidate(self.pipeline,parsed,self.store.events._capture(parsed['picks']),'synthetic',existing)
        self.assertEqual(self.store.clues.detail(clue['id'])['title'],'人工的新标题')

    def test_only_matching_conversation_state_enters_model(self):
        old,clue=self.previous(chat='one');new=self.add('new','另一会话合成消息',chat='two')
        self.assertEqual(workflow.existing_clues(self.pipeline,[new]),([],[]))
        mail=self.add('mail','无关私密邮件原文',source='gmail',chat='mail')
        d=self.store.clues.detail(clue['id']);self.store.clues._save({'id':d['id'],'revision':d['revision'],'summary':'无关邮件信息'},
          self.store.events._capture([{'id':mail['id'],'hash':mail['hash'],'start':0,'end':len(mail['body'])}]))
        state,anchors=workflow.existing_clues(self.pipeline,[old])
        self.assertEqual(state[0]['summary'],'');self.assertEqual(state[0]['title'],'')
        self.assertNotIn('无关私密邮件原文',json.dumps(state,ensure_ascii=False))
        self.assertEqual([d['id'] for d in anchors],[old['id']])

    def test_unanswered_private_cooperation_offer_is_eligible(self):
        doc=self.add('unanswered','合成品牌：请帮我们制作一篇产品评测推文。',chat='wxid_partner')
        doc['provenance'].update(sender='品牌联系人',is_self=False,conversation_type='private')
        refs=self.pipeline.evidence_refs([doc]);key=next(iter(refs))
        value={'title':'合成合作邀约','summary':'待回复的评测邀约。','relevance':'明确需求','categories':['business'],
          'kind':'information','priority':'important','quotes':[{'ref':key}],
          'business_assessment':{'scope':'owner_invitation','counterparty':'合成品牌','request':'请帮我们制作一篇产品评测推文','refs':[key]}}
        candidate=self.pipeline.parse(json.dumps({'clues':[value]}),[doc],refs)[0]
        self.assertEqual(candidate['categories'],['business']);self.assertTrue(candidate['business_assessment']['eligible'])

    def yesterday(self,key,body):
        doc=self.add(key,body)
        stamp=(datetime.now(timezone.utc)-timedelta(days=1)).isoformat()
        with self.store.knowledge.db() as db:db.execute('UPDATE documents SET modified=? WHERE id=?',(stamp,doc['id']))
        return self.store.knowledge.document(doc['id'])

    def run_review(self,answer='{"clues":[]}'):
        self.pipeline.prepare_cache=lambda *args:{'index_status':'ready'}
        self.pipeline.call_pi=lambda *args:answer
        return self.pipeline.analyze('b'*32,{'body':'合成资料范围','wechat_mode':'incremental','wechat_review':True},threading.Event(),self.config,time.monotonic()+120)

    def test_review_only_prior_analyzed_versions_and_durable_resume(self):
        old=self.yesterday('old-review','昨天的合成产品记录');unseen=self.yesterday('unseen-review','昨天未检查合成记录')
        self.pipeline.checkpoint([old,self.doc],'a'*32,'synthetic')
        day,docs=daily.pending(self.pipeline,'synthetic');self.assertTrue(day);self.assertEqual([d['id'] for d in docs],[old['id']])
        result=self.run_review();data=json.loads(result[2])
        self.assertEqual(result[0],'succeeded');self.assertEqual(data['reviewed'],1);self.assertEqual(data['processed'],1)
        self.assertEqual(data['daily_review']['pending_versions'],1) # newly ordinary-analyzed unseen version
        reboot=CluePipeline(self.store);self.assertEqual(daily.status(reboot,'synthetic')['reviewed_versions'],1)
        self.assertEqual([d['id'] for d in daily.pending(reboot,'synthetic')[1]],[unseen['id']])

    def test_failed_review_is_not_coverage_and_does_not_block_incremental(self):
        old=self.yesterday('review-error','昨天的合成产品记录');self.pipeline.checkpoint([old],'a'*32,'synthetic')
        self.pipeline.prepare_cache=lambda *args:{'index_status':'ready'};calls=[]
        def model(identity,prompt,*args):
            calls.append(prompt)
            if '\nanalysis_mode=daily_review\ncontext=' in prompt:raise ValueError('合成复查失败')
            return '{"clues":[]}'
        self.pipeline.call_pi=model
        result=self.pipeline.analyze('b'*32,{'body':'合成范围','wechat_mode':'incremental','wechat_review':True},threading.Event(),self.config,time.monotonic()+120)
        data=json.loads(result[2]);self.assertEqual(result[0],'partial');self.assertEqual(data['processed'],1);self.assertEqual(data['reviewed'],0)
        self.assertEqual(data['daily_review']['pending_versions'],1);self.assertTrue(any(e.get('analysis_mode')=='daily_review' for e in data['errors']))
        self.assertTrue(any('\nanalysis_mode=incremental\ncontext=' in p for p in calls))
        self.assertEqual(len(daily.retries(self.pipeline,'synthetic')),1)

    def test_review_receipt_idempotent_and_model_failure_preserves_checkpoint(self):
        old=self.yesterday('repeated-review','昨天复查合成记录');self.pipeline.checkpoint([old],'a'*32,'synthetic')
        self.pipeline.checkpoint([old],'b'*32,'synthetic',review_day='synthetic-day')
        self.pipeline.checkpoint([old],'b'*32,'synthetic',review_day='synthetic-day')
        self.assertEqual(self.pipeline.committed_progress('b'*32)['reviewed'],1)
        self.assertEqual(self.pipeline.committed_progress('b'*32)['processed'],0)
        self.assertEqual(daily.pending(self.pipeline,'synthetic')[1],[])

    def test_conversation_page_keeps_same_chat_chronological_and_includes_unanswered_offer(self):
        first=self.add('before','合成需求沟通开头',chat='wxid_partner')
        offer=self.add('offer','合成品牌合作评测邀约',chat='wxid_partner')
        other=self.add('other-chat','另一会话普通消息',chat='wxid_other')
        _,docs=self.pipeline.scoped_pending('c'*32,'synthetic',conversation=True)
        self.assertEqual([d['id'] for d in docs],[first['id'],offer['id']])
        self.assertNotIn(other['id'],[d['id'] for d in docs])

    def test_real_pipeline_passes_matching_state_to_pi_and_appends_its_response(self):
        old,clue=self.previous();new=self.add('new','同一产品演示补充截图验收。')
        self.pipeline.checkpoint([old,self.doc],'a'*32,'synthetic')
        self.pipeline.prepare_cache=lambda *args:{'index_status':'ready'}
        calls=[]
        def model(identity,prompt,*args):
            calls.append(prompt)
            material=json.loads(prompt.split('<source_material>')[1].split('</source_material>')[0])
            states=json.loads(prompt.split('<existing_clues>')[1].split('</existing_clues>')[0])
            self.assertEqual(states[0]['id'],clue['id']);self.assertEqual(states[0]['status'],'verified')
            old_item=next(d for d in material if d['id']==old['id']);new_item=next(d for d in material if d['id']==new['id'])
            old_ref=next(e['ref'] for e in old_item['evidence'] if e['text']==old['body'][:12]);new_ref=new_item['evidence'][0]['ref']
            return json.dumps({'clues':[{'title':'模型续谈','summary':'新增验收条件','relevance':'具体变化','categories':['other'],
                'kind':'information','priority':'normal','quotes':[{'ref':new_ref}],
                'continuation':{'id':states[0]['id'],'revision':states[0]['revision'],'refs':[old_ref,new_ref]}}]})
        self.pipeline.call_pi=model
        result=self.pipeline.analyze('b'*32,{'body':'合成范围','wechat_mode':'incremental'},threading.Event(),
             {**self.config,'context_tokens':229368},time.monotonic()+120)
        data=json.loads(result[2]);self.assertEqual(result[0],'succeeded');self.assertEqual(data['processed'],1)
        self.assertEqual(data['clues'],[clue['id']]);self.assertEqual(len(calls),1)
        self.assertEqual(self.store.clues.detail(clue['id'])['revision'],2)
        self.assertEqual(self.store.clues.listing()['items'][0]['latest_note'],'新增验收条件')

    def test_generic_prompt_has_no_known_deal_hints(self):
        prompt=workflow.instructions()
        for name in ('UU远程','黑米','Jason','10月5日','10.5'):self.assertNotIn(name,prompt)
        self.assertIn('existing_clues',prompt);self.assertIn('daily_review',prompt)

    def test_review_flag_validated_and_survives_task_save(self):
        scheduler=ScheduledTasks(self.tmp.name,autostart=False);self.addCleanup(scheduler.close)
        scheduler.model_config=lambda *args:self.config
        payload={'name':'合成微信任务','body':'仅合成测试','kind':'wechat_clues','cwd':self.tmp.name,
          'frequency':'interval','minutes':60,'at':'08:00','weekdays':list(range(7)),'ends_at':None,
          'timeout':120,'enabled':False,'profile_id':'synthetic','allow_commands':False,'wechat_mode':'incremental','wechat_review':True}
        saved=scheduler.save(payload);task=scheduler.listing()['tasks'][0];self.assertTrue(task['wechat_review'])
        scheduler.save({**task,'name':'更新合成名称'});self.assertTrue(scheduler.listing()['tasks'][0]['wechat_review'])
        with self.assertRaises(ValueError):scheduler.validate({**payload,'wechat_review':'true'})
        with self.assertRaises(ValueError):scheduler.validate({**payload,'wechat_mode':'once'})
