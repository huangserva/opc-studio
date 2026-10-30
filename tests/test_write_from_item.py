import json
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from server import Store
from items_store import ItemsStore
from write_from_item import ItemWriter, parse_answer, PLACEHOLDER, blocks_for

LONG = '原文开头😀 <script>window.injected=1</script> <img src=x onerror=alert(1)>\n\n' + '完整原文与参数' * 360 + '\n原文结束'


def answer(item=None):
    body = '我的问题是怎么把收藏变成能继续写的作品。\n\n先把流程跑通\n\n' + PLACEHOLDER + '\n\n' + '我准备先核对输入，再记录失败的条件和输出。工具是否有用，要看真实任务里能不能保留来源和中间结果。每一步留下能回看的证据，遇到不清楚的参数就先标出来，等亲手试过再补结论。\n\n' * 4
    return json.dumps({'title':'先跑通收藏到写作的流程','parts':[body]},ensure_ascii=False)


class WriteTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='write-p3-unit-',dir='/tmp')
        self.store=Store(Path(self.tmp.name)/'workspace')
        self.items=ItemsStore(self.store.workspace)
        self.item=self.items.upsert({'id':'x:123','kind':'article','title':'来源标题','full_text':LONG,'url':'https://x.com/fixture/status/123','sources':['G','A'],'metrics':{'views':2400,'likes':0,'bookmarks':None,'views_per_hour':100},'why_viral':'真实问题','my_angle':'我的切入角度','outline':'问题\n流程\n工具','analysis':{'pattern':'可复用参数'}})
        self.gate=threading.Event()
        def model(item):
            self.gate.wait(3)
            return answer(item)
        self.server=SimpleNamespace(store=self.store,assistant=SimpleNamespace(active=None))
        self.writer=ItemWriter(self.server,model=model,timeout=2)
    def tearDown(self):
        self.gate.set()
        for _ in range(100):
            if not self.writer.live:break
            time.sleep(.01)
        self.tmp.cleanup()
    def done(self, writer=None):
        writer=writer or self.writer
        for _ in range(300):
            state=writer.status('x:123')
            if state['status']!='pending':return state
            time.sleep(.01)
        self.fail('起草未结算')
    def test_base_full_text_metrics_sources_and_version(self):
        started=time.monotonic();state=self.writer.start('x:123',{})
        self.assertLess(time.monotonic()-started,3)
        self.assertEqual(state['base_version'],'v0002')
        detail=self.store.detail(state['slug'])
        full=next(b for b in detail['blocks'] if b.get('text','').startswith('原文全文'))
        self.assertIn(LONG,full['text'])
        self.assertNotIn('<script>',full['html']);self.assertNotIn('<img ',full['html'])
        all_text='\n'.join(b.get('text','') for b in detail['blocks'])
        for value in ('浏览：2400','赞：0','收藏：未知','每小时浏览：100','可复用参数','我的切入角度','问题\n流程\n工具'):self.assertIn(value,all_text)
        ref=json.loads((self.store.article(state['slug'])/'source-item.json').read_text())
        self.assertEqual(ref['item_id'],'x:123');self.assertEqual(ref['sources'],['G','A'])
        self.assertEqual(self.items.detail('x:123')['used_work_id'],state['slug'])
        self.gate.set();done=self.done();self.assertEqual(done['status'],'done');self.assertEqual(done['version'],'v0003')
        detail=self.store.detail(state['slug']);body='\n'.join(b.get('text','') for b in detail['blocks'])
        self.assertIn(PLACEHOLDER,body);self.assertNotEqual(body,LONG)
        self.assertEqual(detail['versions'][0]['summary'],'AI 初稿')
    def test_concurrent_triple_reuses_and_shift_creates(self):
        with ThreadPoolExecutor(max_workers=3) as pool:states=list(pool.map(lambda _:self.writer.start('x:123',{}),range(3)))
        self.assertEqual(len({s['slug'] for s in states}),1)
        self.assertEqual(len(list(self.store.library.iterdir())),1)
        fresh=self.writer.start('x:123',{'force':True})
        self.assertNotEqual(fresh['slug'],states[0]['slug']);self.assertEqual(len(list(self.store.library.iterdir())),2)
    def test_conflict_keeps_human_and_saves_ai(self):
        base=self.writer.start('x:123',{})
        self.store.save(base['slug'],{'base_version':base['base_version'],'title':'手写标题','blocks':[{'kind':'text','html':'<p>黄总亲手修改，不得覆盖</p>'}]})
        self.gate.set();state=self.done();self.assertEqual(state['status'],'conflict')
        self.assertIn('没有覆盖',state['notice'])
        self.assertIn(PLACEHOLDER,(self.store.article(base['slug'])/'ai-draft.md').read_text())
        self.assertIn('黄总亲手修改',self.store.detail(base['slug'])['blocks'][0]['text'])
    def test_model_error_preserves_base_and_does_not_leak(self):
        def fail(item):raise RuntimeError('secret-api-key-never-show')
        self.writer.model=fail;state=self.writer.start('x:123',{});done=self.done()
        self.assertEqual(done['status'],'failed');self.assertNotIn('secret',json.dumps(done))
        self.assertEqual(self.store.meta(state['slug'])['head'],state['base_version'])
    def test_timeout_preserves_base_no_late_write(self):
        self.writer.timeout=.04
        state=self.writer.start('x:123',{});self.assertEqual(self.done()['status'],'failed')
        self.gate.set();time.sleep(.06)
        self.assertEqual(self.store.meta(state['slug'])['head'],state['base_version'])
    def test_pending_does_not_claim_assistant_active(self):
        self.writer.start('x:123',{})
        self.assertIsNone(self.server.assistant.active)
        # 同时有聊天任务也不阻止独立起草。
        self.server.assistant.active='chat-in-progress'
        self.gate.set();self.assertEqual(self.done()['status'],'done')
        self.assertEqual(self.server.assistant.active,'chat-in-progress')
    def test_restart_marks_orphan_failed(self):
        state=self.writer.start('x:123',{})
        restarted=ItemWriter(self.server,model=answer)
        self.assertEqual(restarted.status('x:123')['status'],'failed')
        self.assertEqual(self.store.meta(state['slug'])['head'],state['base_version'])
    def test_deleted_or_trashed_item_never_builds(self):
        with self.assertRaises(ValueError):self.writer.start('missing',{})
        self.items.action('x:123','trash')
        with self.assertRaisesRegex(ValueError,'恢复'):self.writer.start('x:123',{})
        self.assertEqual(len(list(self.store.library.iterdir())),0)
    def test_invalid_payload(self):
        for data in (None,[],{'force':'true'},{'from_today':1},{'title_prefix':'override'},{'bad':1}):
            with self.assertRaises(ValueError):self.writer.start('x:123',data)
    def test_model_once_active_profile_timeout_and_no_assistant_start(self):
        config={'timeout_seconds':500,'max_output_tokens':32768,'model':'active-model'}
        self.server.assistant.config=lambda:dict(config)
        with patch.object(self.server.assistant,'request_model',return_value={'content':answer()},create=True) as call:
            writer=ItemWriter(self.server)
            state=writer.start('x:123',{});done=self.done(writer)
        self.assertEqual(done['status'],'done');self.assertEqual(call.call_count,1)
        args=call.call_args.args
        self.assertIn('真实问题 → 具体操作与核对步骤',args[0][0]['content']);self.assertIn('同一句式在全文最多出现 2 次',args[0][0]['content']);self.assertIn('全文文末一处',args[0][0]['content']);self.assertIn('只保留一处',args[0][0]['content']);self.assertNotIn('第一人称句子只用',args[0][0]['content']);self.assertIn('来源标题',args[0][1]['content']);self.assertEqual(call.call_args.kwargs['model_config']['timeout_seconds'],180);self.assertEqual(call.call_args.kwargs['allowed_tools'],())
    def test_style_rejects_empty_copy_banned_missing_placeholder(self):
        for token in ('🔥','🚀','1️⃣','炸裂','最狠','说句大实话','兄弟们直接冲'):
            result=json.loads(answer());result['parts'][0]+=token
            with self.assertRaises(ValueError):parse_answer(json.dumps(result),self.item)
        for bad in ('', '{}', json.dumps({'title':'a','parts':['']}), answer().replace(PLACEHOLDER,'placeholder')):
            with self.assertRaises(ValueError):parse_answer(bad,self.item)
    def test_rejects_fabricated_experience_even_with_placeholder(self):
        for claim in ('我早期也这样。','这个顺序我验证过。','我在4090上跑过这个流程。','我花了三天做完。','我的流程是先跑模型。'):
            result=json.loads(answer());result['parts'][0]+=claim
            with self.assertRaisesRegex(ValueError,'经历'):parse_answer(json.dumps(result,ensure_ascii=False),self.item)
        parse_answer(answer(),self.item)
        valid=json.loads(answer());valid['parts'][0]+='然后按我的账号情况确认：订阅档位、邮箱一致性、API 组织权限、额度消耗顺序、Codex 是否可用。'
        parse_answer(json.dumps(valid,ensure_ascii=False),self.item)

    def test_thread_has_3_to_8_real_parts(self):
        item={**self.item,'kind':'post'}
        good={'title':'亲手跑流程','parts':['真实问题 '+PLACEHOLDER,'我准备跑通流程，再补实测记录','工具、参数和链接待核对']}
        self.assertEqual(len(parse_answer(json.dumps(good),item)[1]),3)
        good['parts']=good['parts'][:2]
        with self.assertRaises(ValueError):parse_answer(json.dumps(good),item)
    def test_outline_fallback_and_unsafe_url(self):
        blocks=blocks_for({'url':'javascript:alert(1)','full_text':'参考资料'})
        self.assertNotIn('<a ',blocks[0]['html']);self.assertIn('我亲手跑流程',blocks[-1]['html'])


if __name__=='__main__':unittest.main()
