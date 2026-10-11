from isolation_patches import module_thread
import base64,io,json,tempfile,unittest
from unittest.mock import patch
from PIL import Image
from generation.models import build
from generation.video_options import normalize_references
from generation.service import Generation
from server import Store

class VideoLabTests(unittest.TestCase):
 def test_first_last_inputs_and_lora_chain(self):
  _,_,s,g=build({'model':'h3','size':'768x448','prompt':'A lake','workflow':'fl2v','frames':56,'references':{'first':'a','last':'b'},'_references':{'first':{'imageB64':'first'},'last':{'imageB64':'last'}},'video_loras':[{'id':'motion','strength':0.6}]},'job')
  t=json.loads(g['5']['inputs']['timeline_data'])
  self.assertEqual(t['shots'][0]['startImage']['imageB64'],'first')
  self.assertEqual(t['shots'][0]['endImage']['imageB64'],'last')
  self.assertIn('fl2va',g['1']['inputs']['unet_name'])
  self.assertEqual(g['5']['inputs']['model'],['video_lora_0',0])
  self.assertEqual(g['video_lora_0']['inputs']['model'],['10',0])
  self.assertEqual(s['references'],normalize_references({'first':'a','last':'b'}))
  self.assertEqual(s['engine'],'director')
 def test_reference_and_first_frame_have_distinct_conditioning(self):
  _,_,s,g=build({'model':'h3','size':'768x448','prompt':'lake','workflow':'i2v','references':{'first':'id'},'_references':{'first':{'imageB64':'data'}}},'job')
  t=json.loads(g['5']['inputs']['timeline_data'])
  self.assertIn('genImage',t['global'])
  self.assertIn('genImage',t['segments'][0])
  _,_,s,g=build({'model':'h3','size':'768x448','prompt':'lake','workflow':'r2v','references':{'reference':'id'},'_references':{'reference':{'id':'id','kind':'image','remote_file':'article_studio_refs/id/reference.png','width':768,'height':448}}},'job')
  self.assertEqual(s['engine'],'native')
  self.assertEqual(g['native_reference']['inputs']['ref_images.ref_image_0'],['ref_image_0',0])
  self.assertNotIn('guide_first',g)
  self.assertNotIn('5',g)
 def test_reject_missing_frames_mismatched_weights_and_invalid_loras(self):
  for extra in [{'workflow':'i2v','size':'768x432'} , {'workflow':'fl2v'},{'workflow':'r2v','video_checkpoint':'fl2va'}, {'workflow':'fl2v','video_accelerator':'ref8'}, {'video_loras':[{'id':'motion','strength':float('nan')}]},{'video_loras':[{'id':'bad'}]}]:
   with self.assertRaises(ValueError):build({'model':'h3','size':'768x448','prompt':'lake',**extra},'job')
 def test_reference_upload_validation_persistence_and_dedup(self):
  with tempfile.TemporaryDirectory() as tmp:
   g=Generation(Store(tmp));buf=io.BytesIO();Image.new('RGB',(1600,900),'blue').save(buf,format='PNG')
   ref=g.upload_reference({'name':'lake.png','data':base64.b64encode(buf.getvalue()).decode()})
   self.assertEqual(ref['width'],1280)
   with module_thread('generation.service'):
    job=g.start({'model':'h3','size':'768x448','prompt':'lake','workflow':'i2v','references':{'first':ref['id']}})
   graph=json.loads((g.folder/job['id']/'workflow.json').read_text())
   self.assertIn('imageB64',json.loads(graph['5']['inputs']['timeline_data'])['global']['genImage'])
   self.assertNotIn('imageB64',json.dumps(job))
   conditioning=g.resolve_references({'images':[ref['id']]})['images'][0]
   self.assertEqual(conditioning['width']%32,0)
   self.assertEqual(conditioning['height']%32,0)
   for x in ['../bad','a'*32]:
    with self.assertRaises(ValueError):g.reference_path(x)
   with self.assertRaises(ValueError):g.upload_reference({'data':'not image'})
