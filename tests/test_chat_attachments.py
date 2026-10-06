import base64
import io
import json
from pathlib import Path
import sys
import tempfile
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
        with patch.object(self.agent,'config',return_value={'model':'vision','context_tokens':262144}),patch('assistant_agent.threading.Thread'):
            public=self.agent.start({'message':'','attachments':[meta['id']]})
        self.agent.run(self.agent.jobs[public['id']])
        parts=captured[0][-1]['content'];self.assertEqual(parts[1]['type'],'image_url')
        image=parts[1]['image_url']['url'];self.assertTrue(image.startswith('data:image/png;base64,'))
        self.assertEqual(Image.open(io.BytesIO(base64.b64decode(image.split(',')[1]))).size,(40,30))
        serialized=self.agent.history_path.read_text();self.assertNotIn('data:image',serialized)
        self.assertEqual(self.agent.status()['messages'][0]['attachments'][0]['id'],meta['id'])
        # A follow-up retains recent image context even without re-uploading.
        with patch.object(self.agent,'config',return_value={'model':'vision','context_tokens':262144}),patch('assistant_agent.threading.Thread'):
            nxt=self.agent.start({'message':'它是什么颜色？'})
        self.agent.run(self.agent.jobs[nxt['id']])
        self.assertTrue(any(isinstance(m['content'],list) for m in captured[-1][:-1]))
    def test_large_base64_does_not_consume_text_budget(self):
        messages=[{'content':[{'type':'image_url','image_url':{'url':'data:image/png;base64,'+'a'*500000}}]}]
        self.assertLess(self.agent.message_budget(messages),10000)
    def test_attachments_are_never_silently_discarded_by_generation(self):
        meta=self.upload()
        with self.assertRaisesRegex(ValueError,'参考图'):
            self.agent.start({'message':'@生图 参考这个','attachments':[meta['id']]})
    def test_greeting_with_image_calls_model_and_does_not_grant_media_tools(self):
        meta=self.upload()
        with patch.object(self.agent,'config',return_value={'model':'vision'}),patch('assistant_agent.threading.Thread'):
            public=self.agent.start({'message':'你好','attachments':[meta['id']]})
        policy=self.agent.jobs[public['id']]['policy']
        self.assertIsNone(policy['reply']);self.assertEqual(policy['allowed_tools'],[])

if __name__=='__main__':unittest.main()
