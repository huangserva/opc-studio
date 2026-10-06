import json
import unittest
import test_clue_pipeline as fixtures
from clue_pipeline import CluePipeline


class BusinessTests(unittest.TestCase):
    setUp=fixtures.PipelineTests.setUp
    tearDown=fixtures.PipelineTests.tearDown
    add=fixtures.PipelineTests.add
    answer=fixtures.PipelineTests.answer
    def message(self, body, sender='UU远程商单', chat='wxid_fixture'):
        d=self.add('business-'+str(len(body)),body,chat=chat)
        d['title']=f'UU远程商单 · {sender} · 2026-10-05 19:35:52'
        d['provenance'].update(sender=sender,is_self=sender=='__self__',conversation_type='group' if chat.endswith('@chatroom') else 'private')
        return d

    def classify(self,d,scope='owner_invitation',request=None,party='UU远程商单',**kwargs):
        refs=self.pipeline.evidence_refs([d]);key=next(iter(refs))
        assessment={'scope':scope,'counterparty':party,'request':request or d['body'],'refs':[key]}
        return self.pipeline.parse(self.answer(d,categories=['business'],quotes=[{'ref':key}],business_assessment=assessment,**kwargs),[d],refs)[0]

    def test_invitation_is_opportunity_without_assuming_closed_deal(self):
        d=self.message('就算拿不到激励金也可以合作一条推文 给老师按推文的奖金结算这样')
        c=self.classify(d)
        self.assertEqual(c['categories'],['business']);self.assertEqual(c['status'],'candidate')
        self.assertTrue(c['business_assessment']['eligible'])

    def test_third_party_order_is_not_owner_order_and_private_not_group(self):
        d=self.message('我接了 我上次不是跟你说了吗[旺柴]',sender='铁锤人')
        c=self.classify(d,scope='owner_order',party='铁锤人',kind='discussion')
        self.assertEqual(c['categories'],['other']);self.assertEqual(c['kind'],'information')
        self.assertFalse(c['business_assessment']['eligible'])

    def test_generic_own_announcement_has_no_specific_requirement(self):
        d=self.message('商单来了',sender='__self__')
        self.assertEqual(self.classify(d,scope='owner_order')['categories'],['other'])

    def test_concrete_own_order_and_public_call_remain_eligible(self):
        d=self.message('UU远程商单的推文稿今天已交付，正在等待确认',sender='__self__')
        self.assertEqual(self.classify(d,scope='owner_order')['categories'],['business'])
        d=self.message('UU远程商单现招募科技博主合作评测推文，欢迎联系报名',chat='fixture@chatroom')
        self.assertEqual(self.classify(d,scope='open_call')['categories'],['business'])

    def test_unsupported_demand_party_or_unknown_identity_cannot_enter_business(self):
        d=self.message('可以合作一条推文，按推文奖金结算')
        self.assertEqual(self.classify(d,request='虚构的合作需求')['categories'],['other'])
        self.assertEqual(self.classify(d,party='不存在的品牌')['categories'],['other'])
        d['provenance'].update(sender='',is_self=None);d['title']='身份未知'
        self.assertEqual(self.classify(d,party='未知')['categories'],['other'])

    def test_assessment_must_reference_the_selected_original(self):
        d=self.message('可以合作一条推文，按推文奖金结算');refs=self.pipeline.evidence_refs([d])
        answer=self.answer(d,categories=['business'],quotes=[{'ref':'e0'}],business_assessment={'scope':'owner_invitation','counterparty':'UU远程商单','request':d['body'],'refs':['invented']})
        self.assertEqual(self.pipeline.parse(answer,[d],refs)[0]['categories'],['other'])

    def test_legacy_sender_marker_and_private_chat_are_recovered(self):
        d={'title':'铁锤人 · __self__ · 2026-10-05 23:36:23','provenance':{'chatroom_id':'wxid_fixture'}}
        self.assertEqual(CluePipeline.message_identity(d),{'sender':'__self__','is_self':True,'conversation_type':'private'})

    def test_group_invitation_requires_owner_participation_in_same_conversation(self):
        request=self.message('Hi 老师们，非常喜欢你们的内容，期待合作 X Thread',sender='黑米男神.',chat='deal@chatroom')
        reply=self.message('hi',sender='__self__',chat='deal@chatroom')
        refs=self.pipeline.evidence_refs([request,reply])
        assessment={'scope':'owner_invitation','counterparty':'黑米男神.','request':request['body'],'refs':['e0'],'recipient_refs':['e1']}
        answer=self.answer(request,categories=['business'],quotes=[{'ref':'e0'},{'ref':'e1'}],business_assessment=assessment)
        self.assertEqual(self.pipeline.parse(answer,[request,reply],refs)[0]['categories'],['business'])
        selected_once=self.answer(request,categories=['business'],quotes=[{'ref':'e0'}],business_assessment=assessment)
        parsed=self.pipeline.parse(selected_once,[request,reply],refs)[0]
        self.assertEqual(parsed['categories'],['business']);self.assertEqual(len(parsed['picks']),2)
        reply['provenance']['chatroom_id']='another@chatroom'
        self.assertEqual(self.pipeline.parse(answer,[request,reply],refs)[0]['categories'],['other'])
        assessment['recipient_refs']=[]
        answer=self.answer(request,categories=['business'],quotes=[{'ref':'e0'}],business_assessment=assessment)
        self.assertEqual(self.pipeline.parse(answer,[request],refs)[0]['categories'],['other'])

    def test_prefixed_group_xml_keeps_invitation_proof_and_excludes_media_auth(self):
        d=self.message('sender:\n<msg><appmsg><title>具体合作需求</title><url>https://example.test/?token=secret</url><appattach><aeskey>secret-key</aeskey></appattach></appmsg></msg>',chat='deal@chatroom')
        body=self.pipeline.model_body(d)
        self.assertEqual(body,'具体合作需求')
        self.assertNotIn('secret',json.dumps(self.pipeline.evidence_refs([d])))
        invite=self.message('deal@chatroom:\n<sysmsg><template><![CDATA["$username$"邀请你和"$names$"加入了群聊]]></template><nickname>Jason Zhu</nickname></sysmsg>',sender='deal@chatroom',chat='deal@chatroom')
        refs=self.pipeline.evidence_refs([invite]);self.assertTrue(any('邀请你' in r['text'] for r in refs.values()))
        for r in refs.values():self.assertEqual(invite['body'][r['start']:r['end']],r['text'])

    def test_old_cooperation_message_is_not_buried_by_recent_chatter(self):
        from datetime import datetime,timedelta,timezone
        proposal=self.add('older-proposal','期待合作 X Thread',chat='deal@chatroom')
        with self.store.knowledge.db() as db:
            db.execute('UPDATE documents SET modified=? WHERE id=?',((datetime.now(timezone.utc)-timedelta(hours=12)).isoformat(),proposal['id']))
        for i in range(35):self.add('noise'+str(i),'普通新消息 '+str(i),chat='large@chatroom')
        pending=self.pipeline.pending(30,ready_only=True,model='synthetic',run_id='first')
        self.assertEqual(pending[0]['id'],proposal['id'])
        self.pipeline.checkpoint(pending,'first','synthetic')
        later=self.pipeline.pending(30,ready_only=True,model='synthetic',run_id='second')
        self.assertEqual(len(later),7)
        self.assertEqual(len({d['id'] for d in pending+later}),37)

    def test_new_cooperation_traffic_cannot_starve_existing_cooperation(self):
        from datetime import datetime,timedelta,timezone
        older=self.add('existing-demand','具体评测合作邀约',chat='small@chatroom')
        with self.store.knowledge.db() as db:
            db.execute('UPDATE documents SET modified=? WHERE id=?',((datetime.now(timezone.utc)-timedelta(hours=12)).isoformat(),older['id']))
        for i in range(35):self.add('new-demand'+str(i),'新合作讨论 '+str(i),chat='large@chatroom')
        self.assertEqual(self.pipeline.pending(30)[0]['id'],older['id'])

    def test_context_recovers_owner_reply_after_demand_and_before_noise(self):
        from datetime import datetime,timedelta,timezone
        demand=self.message('请合作制作一个产品评测视频',sender='brand',chat='small@chatroom')
        reply=self.message('我先了解一下要求',sender='__self__',chat='small@chatroom')
        stamp=datetime.now(timezone.utc)-timedelta(hours=2)
        with self.store.knowledge.db() as db:
            db.execute('UPDATE documents SET modified=? WHERE id=?',(stamp.isoformat(),demand['id']))
            db.execute('UPDATE documents SET modified=?,provenance=? WHERE id=?',((stamp+timedelta(minutes=30)).isoformat(),json.dumps(reply['provenance']),reply['id']))
        for i in range(20):
            noise=self.add('adjacent'+str(i),'围观讨论 '+str(i),chat='small@chatroom')
            with self.store.knowledge.db() as db:db.execute('UPDATE documents SET modified=? WHERE id=?',((stamp+timedelta(seconds=i+1)).isoformat(),noise['id']))
        other=self.add('other-chat','无关会话',chat='other@chatroom')
        context=self.pipeline.conversation_context([self.store.knowledge.document(demand['id'])])
        self.assertIn(reply['id'],[d['id'] for d in context])
        self.assertNotIn(other['id'],[d['id'] for d in context])
        self.assertNotIn(demand['id'],[d['id'] for d in context])
        self.assertEqual(self.pipeline.status()['processed_versions'],0)


if __name__=='__main__':unittest.main()
