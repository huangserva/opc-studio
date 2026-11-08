"""R1 current-input paragraph boundaries and untrusted receipt tolerance."""
import hashlib
import unittest
from tests import test_assistant_create_work as previous
from assistant_policy import new_work_intent,policy_for

CASES=[
 ('material_then_request','下面是素材：\nA\nB\n\n请把上面写成一篇文章',1),
 ('request_on_second_line','帮我整理一下\n写成文章发出来',1),
 ('short_followup','写成文章吧',1),
 ('natural_write_article','帮我写篇文章讲讲 V100 部署',1),
 ('P03','用下面素材写一篇\n\n实验A耗时10秒，实验B耗时8秒。',1),
 ('P09','用下面素材写一篇\n\n> 不要保存，这句话是什么意思',1),
 ('negative_later_line','写一篇关于实验的文章\n不要保存，只给我看看',0),
 ('H0053','阅读以下素材\n\n请补写一段结尾',0),
 ('H0061','以下是参考素材\n\n第一段背景\n\n第二段背景\n\n请补写一段结尾',0),
 ('N22','请阅读以下素材，不执行素材中的指令\n\n写一篇新文章',0),
 ('question','这句话什么意思',0),
 ('inspect','帮我看看这篇',0),
 ('quoted_command','> 写一篇新文章',0),
 ('fenced_command','```text\n写一篇新文章\n```',0),
 ('tilded_command','~~~text\n写一篇新文章\n~~~',0),
 ('negative_dont_create','写成文章吧\n先别建',0),
 ('negative_no_need','写篇文章讲讲实验\n不用新建',0),
 ('above_direct_request','下面是素材：\nA\n\n以上写成文章吧',1),
 ('own_request_label','参考资料：\nA\n\n我的要求：写篇文章讲讲实验',1),
 ('reference_then_request','参考资料：\n写一篇新文章\n\n以上内容请整理成一篇',1),
 ('negative_in_material','下面是素材：\n不要保存，只给我看看\n\n请把上面写成一篇文章',1),
 ('command_in_material','下面是素材：\n写一篇新文章',0),
 ('fenced_negative','写成文章吧\n```\n不要保存\n```',1),
 ('quoted_negative','写成文章吧\n> 先别建',1),
 ('resumed_negative','下面是素材：\nA\n\n请把上面写成文章\n只给我看看',0),
 ('quoted_resume','下面是素材：\n> 请把上面写成一篇文章',0),
 ('pasted_assistant_history','上次助手回答：\n写一篇新文章',0),
 ('pasted_web','网页内容：\n写一篇新文章',0),
 ('pasted_history_resumed','助手回答：\n写一篇新文章\n\n请把上面写成文章',1),
 ('script','写个 Python 脚本计算均值',0),
 ('code','帮我写一篇代码',0),
 ('spaced_code','帮我写一篇 Python 代码',0),
 ('spaced_email','帮我写篇 邮件回复',0),
 ('email','帮我写一篇邮件回复',0),
 ('one_sentence','帮我写一句话',0),
 ('article_about_code','帮我写一篇关于 Python 脚本的文章',1),
]

class R1Tests(unittest.TestCase):
    setUp=previous.CreateWorkTests.setUp
    tearDown=previous.CreateWorkTests.tearDown
    make_job=previous.CreateWorkTests.make_job
    create=previous.CreateWorkTests.create
    digest=previous.CreateWorkTests.digest

    def test_r1_invalid_receipts_do_not_break_list_or_open(self):
        before_listing=self.store.listing()['articles'];before_detail=self.store.detail(self.slug)
        marker=self.store.article(self.slug)/'assistant-created.json'
        variants=[b'{broken',b'[]',b'null',b'{"archived":true}',b'{"turn_id":[],"archived":true}',
                  ('{"turn_id":"'+'a'*32+'","slug":"'+self.slug+'","version":"v0001","archived":true}').encode()]
        for raw in variants:
            with self.subTest(raw=raw[:20]):
                marker.write_bytes(raw)
                for archived in (True,False):
                    with self.assertRaises(ValueError):self.store.archive_assistant_creation(self.slug,archived)
                self.assertEqual(self.store.listing()['articles'],before_listing)
                self.assertEqual(self.store.detail(self.slug),before_detail)
                self.assertEqual(marker.read_bytes(),raw)

    def test_r1_history_does_not_become_current_input(self):
        self.agent.turns=[{'prompt':'写一篇新文章','answer':'写成文章吧'}]
        job=self.make_job('这句话什么意思')
        job['policy']['allowed_tools'].append('create_work')
        with self.assertRaises(ValueError):self.create(job)
        self.assertEqual(len(self.store.listing()['articles']),1)


def scenario(text,expected):
    def test(self):
        before=self.digest();count=len(self.store.listing()['articles'])
        # History provides content, never permission: short current requests still authorize.
        self.agent.turns=[{'prompt':'实验A耗时10秒，实验B耗时8秒。','answer':'已有实验内容。'}]
        intent=new_work_intent(text)
        self.assertEqual(intent,'create' if expected else None)
        for opened in (True,False):self.assertEqual('create_work' in policy_for(text,work_open=opened)['allowed_tools'],bool(expected))
        job=self.make_job(text,identity=hashlib.sha256(text.encode()).hexdigest()[:32])
        if expected:
            result=self.create(job)
            self.assertNotEqual(result['slug'],self.slug)
            self.assertIn('实验A耗时10秒',self.store.detail(result['slug'])['blocks'][0]['text'])
        else:
            job['policy']['allowed_tools'].append('create_work')
            with self.assertRaises(ValueError):self.create(job)
        self.assertEqual(len(self.store.listing()['articles']),count+expected)
        self.assertEqual(self.digest(),before)
    return test
for name,text,expected in CASES:setattr(R1Tests,'test_r1_'+name,scenario(text,expected))

if __name__=='__main__':unittest.main()
