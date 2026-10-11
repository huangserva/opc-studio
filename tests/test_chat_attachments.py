from isolation_patches import module_thread
import base64
import io
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from PIL import Image
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from server import Store,content,assistant_module

class ChatImageTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.agent=assistant_module.Assistant(Store(self.root/'workspace'),content,self.root/'missing.json')
    def tearDown(self):self.tmp.cleanup()
    def upload(self):
        output=io.BytesIO();Image.new('RGB',(40,30),'blue').save(output,format='PNG')
        return self.agent.upload_chat_image({'name':'剪贴板.png','data':base64.b64encode(output.getvalue()).decode()})
    def test_upload_validation_and_local_asset(self):
        meta=self.upload();self.assertEqual(meta['width'],40)
        self.assertEqual(self.agent.resolve_attachments([meta['id']]),[meta])
        with self.assertRaises(ValueError):self.agent.upload_chat_image({'data':base64.b64encode(b'not an image').decode()})
        with self.assertRaises(ValueError):self.agent.resolve_attachments(['../escape'])
        with self.assertRaises(ValueError):self.agent.resolve_attachments([meta['id']]*5)
        with self.assertRaises(ValueError):self.agent.resolve_attachments([{}])
    def test_real_image_parts_sent_history_stores_only_references(self):
        meta=self.upload();captured=[]
        def client(messages,**kwargs):
            captured.append(messages)
            return {'role':'assistant','content':'蓝色图片'}
        self.agent.client=client
        with patch.object(self.agent,'config',return_value={'model':'vision','context_tokens':262144}),module_thread(assistant_module):
            public=self.agent.start({'message':'','attachments':[meta['id']]})
        self.agent.run(self.agent.jobs[public['id']])
        parts=captured[0][-1]['content'];self.assertEqual(parts[1]['type'],'image_url')
        image=parts[1]['image_url']['url'];self.assertTrue(image.startswith('data:image/png;base64,'))
        self.assertEqual(Image.open(io.BytesIO(base64.b64decode(image.split(',')[1]))).size,(40,30))
        serialized=self.agent.history_path.read_text();self.assertNotIn('data:image',serialized)
        self.assertEqual(self.agent.status()['messages'][0]['attachments'][0]['id'],meta['id'])
        # A follow-up retains recent image context even without re-uploading.
        with patch.object(self.agent,'config',return_value={'model':'vision','context_tokens':262144}),module_thread(assistant_module):
            nxt=self.agent.start({'message':'它是什么颜色？'})
        self.agent.run(self.agent.jobs[nxt['id']])
        self.assertTrue(any(isinstance(m['content'],list) for m in captured[-1][:-1]))
    def test_large_base64_does_not_consume_text_budget(self):
        messages=[{'content':[{'type':'image_url','image_url':{'url':'data:image/png;base64,'+'a'*500000}}]}]
        self.assertLess(self.agent.message_budget(messages),10000)
    def test_attachments_are_never_silently_discarded_by_generation(self):
        # 2026-10-09 修正依据：@生图 现在把聊天附件转成生成参考图
        # （assistant_media.direct_media 里 settings['references']['images'] = generation.upload_reference(...)），
        # 不再一律报「参考图」；生产环境由 server.py:1042 注入 assistant.generation = Generation(store)，
        # 旧夹具缺这个协作者所以 AttributeError。测试目的不变：附件绝不能被静默丢掉——
        # 要么真的传到生成任务，要么明确报错。断言只增不减。
        meta=self.upload()
        class GenerationStub:
            def __init__(self):
                self.lock=threading.RLock();self.jobs={};self.references=[];self.started=[]
            def upload_reference(self,payload):
                self.references.append(payload);return {'id':'r'*32}
            def start(self,payload,origin=None,conversation_id=None):
                self.started.append({'payload':payload,'origin':origin,'conversation_id':conversation_id})
                return {'id':'j'*32,'kind':'image','model_name':'Qwen Image 2.1','created_at':'2026-10-09T00:00:00+00:00',
                        'prompt':payload.get('prompt'),'chat_request':payload.get('chat_request')}
        generation=GenerationStub();self.agent.generation=generation
        result=self.agent.start({'message':'@生图 参考这个','attachments':[meta['id']]})
        self.assertEqual(result,{'media_job':'j'*32})
        self.assertEqual(len(generation.references),1)
        reference=generation.references[0]
        self.assertEqual(reference['name'],meta['name'])
        self.assertEqual(Image.open(io.BytesIO(base64.b64decode(reference['data'].split(',')[1]))).size,(40,30))
        started=generation.started[0]
        self.assertEqual(started['payload']['references'],{'images':['r'*32]})
        self.assertEqual(started['origin'],'composer')
        self.assertEqual(started['conversation_id'],self.agent.conversation_id)
        # 聊天记录里也看得到这张图，不是只发不记。
        self.assertEqual(self.agent.turns[-1]['attachments'][0]['id'],meta['id'])
        # 超出当前模型的参考图上限必须报错，不能静默丢图。
        second=self.upload()
        with self.assertRaisesRegex(ValueError,'参考图'):
            self.agent.start({'message':'@生图 两张','attachments':[meta['id'],second['id']],'media':{'model':'krea2'}})
        self.assertEqual(len(generation.started),1)
    def test_greeting_with_image_calls_model_and_does_not_grant_media_tools(self):
        meta=self.upload()
        with patch.object(self.agent,'config',return_value={'model':'vision'}),module_thread(assistant_module):
            public=self.agent.start({'message':'你好','attachments':[meta['id']]})
        policy=self.agent.jobs[public['id']]['policy']
        self.assertIsNone(policy['reply']);self.assertEqual(policy['allowed_tools'],[])

if __name__=='__main__':unittest.main()
