import base64
import json
import sys
import unittest
import uuid
from pathlib import Path
from generation.models import build
from generation.image_references import normalize_image_references
from tests import test_media_http_transport as transport
from tests.isolation_patches import WORK_BACKEND_REASON, work_backend_available

class ImageReferenceTests(unittest.TestCase):
    # hygiene-4：这条要 import work/image-reference-fix/backend 下的 workflows.py，
    # /work/ 被 .gitignore 第 13 行排除，不进公开仓库。同文件另两条用例不碰它，照跑。
    @unittest.skipUnless(work_backend_available(), WORK_BACKEND_REASON)
    def test_real_http_upload_ids_reach_both_model_graphs(self):
        case = transport.TransportTests()
        case.setUp()
        case.api.next_id=lambda:uuid.uuid4().hex
        try:
            sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'work/image-reference-fix/backend'))
            import workflows
            for model in ('qi21','krea2'):
                identity=case.generation.upload_reference({'data':'data:image/png;base64,'+base64.b64encode(transport.PNG).decode()})['id']
                job=case.start_job(model=model,references={'images':[identity]},denoise=0.6)
                result=case.run_sync(job['id'])
                self.assertEqual(result['status'],'done')
                params=case.api.jobs[result['api_job_id']]['params']
                self.assertNotEqual(params['references']['images'],[identity])
                def resolve(ids, frames):
                    return {'images':[{'id':i,'kind':'image','remote_file':f'article_studio_refs/{i}/reference.png'} for i in ids.get('images',[])]}
                graph,settings=workflows.build(params['model'],{k:v for k,v in params.items() if k!='request_id'},'test',resolve)
                audit=json.loads((case.generation.folder/job['id']/'workflow.json').read_text())
                self.assertEqual(graph['image_reference_1']['class_type'],'LoadImage')
                if model=='qi21':
                    self.assertEqual(graph['4']['inputs']['images.image_1'],['image_reference_size',0])
                    self.assertEqual(graph['8']['inputs']['latent_image'],['4',2])
                    self.assertEqual(audit['8']['inputs']['latent_image'],['4',2])
                else:
                    self.assertEqual(graph['sampler']['inputs']['denoise'],0.6)
                    self.assertEqual(graph['sampler']['inputs']['latent_image'],['image_reference_encode',0])
                    self.assertEqual(audit['sampler']['inputs']['denoise'],0.6)
        finally:
            case.tearDown()
    def test_reject_invalid_roles_counts_and_strength(self):
        for refs in ({'videos':[]},{'images':['x']},{'images':['a'*32]*2}):
            with self.assertRaises(ValueError):normalize_image_references(refs,'krea2')
        for value in (True,0,float('nan'),1.1):
            with self.assertRaises(ValueError):build({'model':'krea2','prompt':'test','denoise':value},'test')
    def test_text_generation_keeps_empty_latent(self):
        self.assertEqual(build({'model':'qi21','prompt':'test'},'test')[3]['8']['inputs']['latent_image'],['6',0])
        self.assertEqual(build({'model':'krea2','prompt':'test'},'test')[3]['sampler']['inputs']['latent_image'],['latent',0])

if __name__=='__main__':unittest.main()
