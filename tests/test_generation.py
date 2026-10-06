import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from generation.models import build
from generation.service import Generation
from server import Store
from PIL import Image

class GenerationTests(unittest.TestCase):
    def test_qwen_official_2k_sizes_reach_generation_graph_without_downscaling(self):
        for width, height in [(2048,2048),(2400,1792),(1792,2400),(2528,1696),
                              (1696,2528),(2752,1536),(1536,2752)]:
            with self.subTest(width=width,height=height):
                size=f'{width}x{height}'
                _,_,settings,graph=build({'model':'qi21','prompt':'山水','size':size,'seed':42},'2k-test')
                self.assertEqual((graph['6']['inputs']['width'],graph['6']['inputs']['height']),(width,height))
                self.assertEqual(settings['size'],size)
                self.assertEqual(graph['10']['inputs']['filename_prefix'],'article_studio/2k-test')
                # This encoder setting only resizes reference images, not generated latents.
                self.assertEqual(graph['4']['inputs']['resolution'],1024)
                if width>2048 or height>2048:
                    with self.assertRaises(ValueError):
                        build({'model':'krea2','prompt':'山水','size':size},'unsupported-krea')

    def test_verified_image_graph_has_no_audio(self):
        model, prompt, settings, graph = build({'model':'qi21','prompt':'新画面','seed':42}, 'test')
        self.assertEqual(graph['4']['inputs']['prompt'], prompt)
        self.assertEqual(graph['8']['inputs']['seed'], 42)
        self.assertEqual(graph['1']['inputs']['unet_name'], model['checkpoint'])
        self.assertNotIn('audio', json.dumps(graph))

    def test_krea2_uses_own_weights_encoder_and_sampler(self):
        model,prompt,settings,graph=build({'model':'krea2','prompt':'orange cat','seed':12,'size':'768x512'},'krea-job')
        self.assertEqual(model['kind'],'image')
        self.assertEqual(graph['unet']['inputs']['unet_name'],'krea2_turbo_fp8.safetensors')
        self.assertEqual(graph['clip']['inputs']['type'],'krea2')
        self.assertEqual(graph['positive']['inputs']['text'],prompt)
        self.assertEqual(graph['sampler']['inputs']['steps'],8)
        self.assertEqual(graph['sampler']['inputs']['seed'],12)
        self.assertEqual(graph['save']['inputs']['filename_prefix'],'article_studio/krea-job')
        self.assertEqual(graph['latent']['inputs']['width'],768)
        self.assertFalse(any('ImageNode' in n['class_type'] for n in graph.values()))

    def test_video_is_t2v_not_old_reference_job(self):
        _,_,settings,graph = build({'model':'h3','prompt':'小猫走路','frames':56}, 'test')
        self.assertTrue(graph['5']['inputs']['task_type'].startswith('t2v'))
        self.assertEqual(json.loads(graph['5']['inputs']['timeline_data']), {})
        self.assertEqual(settings['frames'],56)

    def test_reject_unknown_model_and_size(self):
        for payload in ({'model':'chat','prompt':'hi'},{'model':'qi21','prompt':'hi','size':'99999x99999'}, {'model':'h3','prompt':'hi','frames':99999}):
            with self.assertRaises(ValueError): build(payload,'test')

    def test_save_is_explicit_versioned_idempotent_and_stale_guarded(self):
        with tempfile.TemporaryDirectory() as tmp:
            store=Store(tmp)
            original=store.create({'title':'原作品','type':'article'})
            generation=Generation(store)
            with patch('generation.service.threading.Thread'):
                job=generation.start({'model':'qi21','prompt':'图像','slug':original['slug'],'version':original['head']})
            job_id=job['id'];path=generation.folder/job_id/'result.png'
            Image.new('RGB',(64,64),'orange').save(path)
            generation.update(job_id,status='done',asset_file='result.png')
            self.assertEqual(store.detail(original['slug'])['head'], original['head'])
            result=generation.save(job_id,{'mode':'insert'})
            self.assertNotEqual(result['version'],original['head'])
            self.assertEqual(generation.save(job_id,{'mode':'insert'}),result)
            self.assertEqual(len(store.detail(original['slug'])['versions']),2)
            self.assertEqual(len(store.detail(original['slug'],original['head'])['blocks']),0)
            with patch.object(Generation, '_spawn', lambda _s, *a: None):
                stale=generation.start({'model':'qi21','prompt':'旧版本','slug':original['slug'],'version':original['head']})
            target=generation.folder/stale['id']/'result.png';Image.new('RGB',(64,64)).save(target)
            generation.update(stale['id'],status='done',asset_file='result.png')
            with self.assertRaisesRegex(ValueError,'新版本'):generation.save(stale['id'],{'mode':'insert'})
            fresh=generation.save(job_id,{'mode':'new'})
            self.assertNotEqual(fresh['slug'],original['slug'])
            self.assertEqual(store.detail(fresh['slug'])['studio']['type'],'gallery')

    def test_failure_without_output_is_not_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            g=Generation(Store(tmp))
            with patch.object(Generation, '_spawn', lambda _s, *a: None):
                job=g.start({'model':'qi21','prompt':'图片'})
            class NoResult:
                def result_meta(self, api_job_id): return {'content_type': None}
                def download_result(self, *a, **k): raise AssertionError('结果未就绪却尝试下载')
            with self.assertRaisesRegex(ValueError,'不支持的媒体类型'):g.finish(job['id'],{'id':'api0001'},NoResult())
            self.assertNotEqual(g.public(job['id'])['status'],'done')

if __name__=='__main__':unittest.main()
