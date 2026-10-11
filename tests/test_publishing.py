import base64
import copy
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from PIL import Image

spec=importlib.util.spec_from_file_location('studio',Path(__file__).resolve().parents[1]/'server.py')
studio=importlib.util.module_from_spec(spec);spec.loader.exec_module(studio)
from publishing.registry import capabilities
from publishing.transports.buffer_cli import validate

class PublishingTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.store=studio.Store(self.temp.name)
    def tearDown(self):self.temp.cleanup()
    def post(self):
        a=self.store.create({'title':'串文','type':'post'})
        out=io.BytesIO();Image.new('RGB',(12,12),'blue').save(out,'PNG')
        self.store.save(a['slug'],{'base_version':'v0001','title':'串文','blocks':[
            {'kind':'text','html':'<p>第一条</p>'},
            {'kind':'image','data':base64.b64encode(out.getvalue()).decode()},
            {'kind':'text','html':'<p>第二条</p>'}]})
        return a['slug']
    def plan(self,slug,platform='x',version='v0002',options=None):
        return self.store.publication_plan(slug,{'platform':platform,'version':version,'options':options or {}})

    def test_thread_order_media_and_local_path_block(self):
        slug=self.post();p=self.plan(slug)
        self.assertEqual(p['transport'],'buffer_cli');self.assertIsNone(p['buffer_input'])
        name=p['missing_media'][0]
        for url in ('http://localhost/a.png','https://127.0.0.1/a.png','file:///a.png'):
            self.assertEqual(self.plan(slug,options={'channel_id':'test-channel','media_urls':{name:url}})['status'],'blocked')
        p=self.plan(slug,options={'channel_id':'test-channel','media_urls':{name:'https://cdn.example.org/a.png'}})
        body=p['buffer_input'];self.assertTrue(body['saveToDraft']);self.assertFalse(p['published'])
        self.assertEqual(body['text'],'第一条')
        self.assertEqual([x['text'] for x in body['metadata']['twitter']['thread']],['第一条','第二条'])
        self.assertEqual(len(body['metadata']['twitter']['thread'][0]['assets']),1)
        self.assertEqual(body['metadata']['twitter']['thread'][1]['assets'],[])

    def test_exact_version_and_routes(self):
        slug=self.post()
        self.store.save(slug,{'base_version':'v0002','title':'新串文','blocks':[{'kind':'text','html':'<p>新正文</p>'}]})
        self.assertEqual(self.plan(slug)['posts'][0]['text'],'第一条')
        a=self.store.create({'title':'长文','type':'article'})
        p=self.plan(a['slug'],version='v0001',options={'account':'servasyy'})
        self.assertEqual(p['transport'],'browser_article_draft')
        self.assertNotIn('buffer_input',p)
        for platform in ('xiaohongshu','douyin','channels'):
            p=self.plan(slug,platform=platform)
            self.assertEqual(p['status'],'manual_only');self.assertEqual(p['stage'],'not_connected')
        with self.assertRaises(ValueError):self.plan(slug,platform='tiktok')
        self.assertEqual(len(capabilities()),20)

    def test_thread_caption_override_is_not_silent(self):
        slug=self.post();a=self.store.detail(slug)
        raw=copy.deepcopy(a['studio']);raw['platforms']['x']={'caption':'单条替代文案'}
        self.store.save(slug,{'base_version':'v0002','title':'串文','studio':raw})
        p=self.plan(slug,version='v0003')
        self.assertTrue(any('串文' in s for s in p['blocking']))

    def test_transport_only_dry_runs_and_rejects_live_payload(self):
        with patch('publishing.transports.buffer_cli.subprocess.run') as run:
            run.return_value.returncode=0
            result=validate({'saveToDraft':True,'text':'a; $(echo bad)'})
            args,kw=run.call_args
            self.assertIn('--dry-run',args[0]);self.assertNotIn('shell',kw)
            self.assertFalse(result['remote_request_sent'])
            with self.assertRaises(ValueError):validate({'saveToDraft':False})
            self.assertEqual(run.call_count,1)

if __name__=='__main__':unittest.main()
