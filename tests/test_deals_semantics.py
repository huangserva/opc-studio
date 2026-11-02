"""Regression cases from independent lifecycle review; no live DB or model calls."""
import unittest
from deals_evidence import lifecycle_candidates

REAL_MESSAGES = [{'title': '豆包', 'text': 'gongkaichun:\n@所有人\u2005大家好，本次豆包工作宣发，计划周三到周五之间宣发，大家这几天可以创作了，写好初稿放在飞书文档的初稿列（尽量使用飞书链接，开好权限）有问题随时沟通\n\n最后，谢谢大家参与本次宣发，甲方不事儿，大家安心创作就行', 'at': '2026-09-07T01:51:54+00:00', 'provenance': {'conversation_type': 'group', 'is_self': False, 'sender': '木马人'}, 'terms': ['豆包']}, {'title': '字节洗白', 'text': 'gongkaichun:\n写好了提交初稿到文档上', 'at': '2026-10-13T07:53:17+00:00', 'provenance': {'conversation_type': 'group', 'is_self': False, 'sender': '木马人'}, 'terms': ['字节']}, {'title': 'Apimart', 'text': '25984983962522553@openim:\n📢 APIMart 支付渠道及充值规则调整通知\n出于运营合规要求，自北京时间 2026 年 10 月 21 日 12:00（UTC+8）起，APIMart 的微信支付与支付宝支付将统一通过 Antom 渠道提供。\n使用上述两种方式充值时，单笔最低支付金额为 5 美元，到账金额按实际支付金额的 80% 计算。例如，支付 5 美元，平台余额到账 4 美元，即 40 Credits。\n其他充值渠道不受本次调整影响，可继续正常使用。 请在支付前确认支付金额及预计到账额度。如有疑问，请联系客服。感谢您的理解与支持。', 'at': '2026-10-20T16:19:22+00:00', 'provenance': {'conversation_type': 'group', 'is_self': False, 'sender': '刘寅'}, 'terms': ['Apimart']}]

class SemanticsTests(unittest.TestCase):
    def candidates(self,body,group=False):
        e=dict(id='a'*32,title='独立品牌商务群 · 客户',text=body,at='2026-10-13T07:53:17+00:00',provenance=dict(conversation_type='group' if group else 'private',sender='客户'),terms=['独立品牌'],state='candidate')
        return lifecycle_candidates(dict(stage='unknown',evidence=[e],publications=[],receipts=[],date_events=[]))
    def test_g_minimal_instruction_is_not_a_completion_receipt(self):
        items=self.candidates('独立品牌合作执行说明：写好初稿放在飞书文档的初稿列')
        self.assertFalse(any(x['field']=='stage' and x['value']=='draft' for x in items),items)
    def test_g_minimal_payment_rules_are_not_personal_cooperation(self):
        self.assertEqual(self.candidates('独立品牌支付渠道充值规则调整通知，到账余额为充值额80%，单笔充值5美元',True),[])
    def test_platform_refund_and_billing_notices_are_not_contact_evidence(self):
        for body in ['独立品牌内容安全审核计费规则及费用退还通知，全平台模型请求将按新规则扣费，历史费用统一退还','独立品牌积分充值公告，最低支付5美元，平台余额今天到账','独立品牌支付渠道调整通知，所有用户的费用按新计费规则计算']:
            with self.subTest(body=body):self.assertEqual(self.candidates(body,True),[])
    def test_three_instruction_and_question_variants_are_not_completion(self):
        for body in ['明天写好初稿发我','初稿写好了吗？','请大家写完后提交']:
            with self.subTest(body=body):self.assertFalse(any(x['field']=='stage' and x['value']=='draft' for x in self.candidates('独立品牌合作：'+body)))
    def test_real_messages_from_review_do_not_repeat_the_two_misclassifications(self):
        self.assertEqual(len(REAL_MESSAGES),3)
        for e in REAL_MESSAGES:
            with self.subTest(title=e['title']):
                e=dict(e,id='b'*32,state='candidate');items=lifecycle_candidates(dict(stage='unknown',evidence=[e],publications=[],receipts=[],date_events=[]))
                if e['title']=='Apimart':self.assertEqual(items,[])
                else:self.assertFalse(any(x['field']=='stage' and x['value']=='draft' for x in items),items)
    def test_explicit_completion_receipts_still_propose_draft(self):
        for body in ['老师，我已经写好初稿了','老师，初稿已完成，已提交给您','老师，我写完了，这就是初稿']:
            with self.subTest(body=body):self.assertTrue(any(x['field']=='stage' and x['value']=='draft' for x in self.candidates(body)))
    def test_personal_fees_and_cooperation_brief_survive_announcement_filter(self):
        self.assertTrue(any(x['field']=='paid_on' for x in self.candidates('老师，合作稿费今天到账了')))
        self.assertTrue(any(x['field']=='stage' and x['value']=='preparing' for x in self.candidates('独立品牌达人合作 Brief 已收，开始整理资料')))


class ExplicitReceiptTests(unittest.TestCase):
    def candidates(self,body,group=False):
        e=dict(id='c'*32,title='独立品牌 · 本人',text=body,at='2026-10-31T18:05:00+00:00',provenance=dict(conversation_type='group' if group else 'private',sender='__self__',is_self=True),terms=['独立品牌'],state='candidate')
        return lifecycle_candidates(dict(stage='unknown',evidence=[e],publications=[],receipts=[],date_events=[]))
    def test_g_explicit_delivery_receipt(self):
        items=self.candidates('老师，初稿已发你');p=next((x for x in items if x['field']=='stage' and x['value']=='draft'),None);self.assertIsNotNone(p,items);self.assertEqual(p['entered_on'],'2026-11-01')
    def test_g_explicit_fee_received(self):
        items=self.candidates('老师，稿费已到');p=next((x for x in items if x['field']=='paid_on'),None);self.assertIsNotNone(p,items);self.assertEqual(p['value'],'2026-11-01');self.assertIn('推算',p['reason'])
    def test_three_more_explicit_draft_delivery_variants(self):
        for body in ['老师，初稿已交','老师，初稿已提交','老师，稿子已发过去了','老师，我已提交初稿']:
            with self.subTest(body=body):self.assertTrue(any(x['field']=='stage' and x['value']=='draft' for x in self.candidates(body)))
    def test_three_more_explicit_payment_variants(self):
        for body in ['老师，已打款','老师，已转','老师，款收到']:
            with self.subTest(body=body):self.assertTrue(any(x['field']=='paid_on' and x['value']=='2026-11-01' for x in self.candidates(body)))
    def test_questions_future_negation_and_platform_balance_are_not_receipts(self):
        for body in ['老师，初稿发你了吗？','老师，初稿明天发你','老师，初稿尚未提交','老师，初稿提交后请发我','老师，稿费明天到','老师，稿费到了吗？','老师，稿费还没到','老师，今天稿费没到账','老师，客户平台余额已到','老师，客户平台余额今天已到账','老师，充值积分已转','老师，已打款后再交稿']:
            with self.subTest(body=body):self.assertFalse(any(x['field']=='paid_on' or x['field']=='stage' and x['value']=='draft' for x in self.candidates(body)))


class ColloquialReceiptTests(unittest.TestCase):
    candidates=ExplicitReceiptTests.candidates
    def test_g_twelve_colloquial_and_mixed_question_cases(self):
        cases=[('稿子发过去了','stage'),('尾款到了','paid_on'),('稿费到了吗？',None),('下周打款',None),('初稿已发你看看要不要改？','stage'),('稿子发过去了吗？',None),('尾款到了再交稿',None),('昨天尾款没到，今天也未到',None),('请把初稿提交给客户',None),('尾款已经到账，谢谢老师','paid_on'),('稿件已经发给您','stage'),('初稿已发你，请查收','stage')]
        for body,field in cases:
            with self.subTest(body=body):
                items=self.candidates(body);matches=[x for x in items if x['field']=='paid_on' or x['field']=='stage' and x['value']=='draft']
                if field:
                    self.assertTrue(any(x['field']==field for x in matches),items)
                    for x in matches:self.assertEqual(x['entered_on'] if x['field']=='stage' else x['value'],'2026-11-01');self.assertIn('推算',x['reason'])
                else:self.assertEqual(matches,[])
    def test_six_fresh_category_variants(self):
        cases=[('稿件交给您了看看需不需要调整？','stage'),('我把文案发过去啦','stage'),('首款收到了','paid_on'),('稿费到啦，辛苦','paid_on'),('麻烦把稿件发过去',None),('首款明天到账，今天先给初稿',None)]
        for body,field in cases:
            with self.subTest(body=body):
                items=self.candidates(body);matches=[x for x in items if x['field']=='paid_on' or x['field']=='stage' and x['value']=='draft']
                if field:self.assertTrue(any(x['field']==field for x in matches),items)
                else:self.assertEqual(matches,[])

    def test_website_deployment_status_is_not_a_content_publication_receipt(self):
        items=self.candidates('稿子页面交接清单：8. 已发布状态和网站地址（不用重新发布）')
        self.assertFalse(any(x['field']=='published_on' for x in items),items)


class PublicationAttributionTests(unittest.TestCase):
    candidates=ExplicitReceiptTests.candidates
    def test_g_publication_object_table(self):
        for body,expected in [('老师，我的部署教程文章已发布',True),('老师，部署教程文章已发布，链接已发群里',True),('老师，今天网站已发布',False),('老师，文章已发布',True),('老师，产品测评文章已经发布',True),('老师，视频已经发布',True),('老师，推文已经发布',True),('老师，稿子已经发布',True),('老师，今天产品已发布',False),('老师，新版本已经发布',False),('老师，页面已上线',False)]:
            with self.subTest(body=body):
                items=self.candidates(body);pub=[x for x in items if x['field']=='published_on'];stage=[x for x in items if x['field']=='stage' and x['value']=='published']
                self.assertEqual(bool(pub),expected,items);self.assertEqual(bool(stage),expected,items)
                if expected:self.assertEqual(pub[0]['value'],'2026-11-01')
    def test_explicit_foreign_order_is_excluded_before_splitting_clauses(self):
        for body in ['客户说另一个博主的稿子发过去了，与我这单无关','这条消息与我这单无关，稿件已经提交','另一个博主的稿子已经发布','别人的稿子发过去了','尾款已经到账，是另一个博主的商单，与我这单无关']:
            with self.subTest(body=body):self.assertEqual(self.candidates(body),[])
        self.assertTrue(any(x['field']=='stage' and x['value']=='draft' for x in self.candidates('他说稿子发过去了')))
    def test_l1_boundaries_are_disclosed_without_expanding_scope(self):
        for body in ['文章已经发了','钱收到啦','文章已发布','平台余额已到']:
            with self.subTest(body=body):self.assertEqual(self.candidates(body),[])
        for body in ['老师，这单的钱收到啦','稿子过去了吗','等尾款到了再说']:
            with self.subTest(body=body):self.assertFalse(any(x['field'] in ('published_on','paid_on') or x['field']=='stage' and x['value']=='draft' for x in self.candidates(body)))
