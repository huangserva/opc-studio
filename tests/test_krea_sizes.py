import sys,unittest,tempfile
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from generation.models import MODELS, KREA_PRESETS, build, validate_size
from generation.service import Generation
from server import Store,content,assistant_module
class KreaSizesTests(unittest.TestCase):
 def test_all_presets_have_exact_ratios_and_reach_workflow(self):
  for group in KREA_PRESETS.values():
   for size,ratio in group.items():
    w,h=map(int,size.split('x'));a,b=map(int,ratio.split(':'))
    self.assertEqual(w*b,h*a)
    _,_,settings,graph=build({'model':'krea2','prompt':'lake','size':size},'size-test')
    self.assertEqual((graph['latent']['inputs']['width'],graph['latent']['inputs']['height']),(w,h))
    self.assertEqual(settings['size'],size)
 def test_bounded_custom_size_and_other_models(self):
  model=MODELS['krea2']
  for size in ['512x512','1600x1200','2048x2048']:
   self.assertEqual(validate_size(model,size),size)
  for size in ['custom','513x1024','496x1024','2064x1024','65536x65536','1024x1024x2',None,[],1]:
   with self.subTest(size=size),self.assertRaises(ValueError):validate_size(model,size)
  for key in ['qi21','h3']:
   with self.assertRaises(ValueError):validate_size(MODELS[key],'1600x1200')
 def test_custom_preference_restart_retry_and_model_switch(self):
  with tempfile.TemporaryDirectory() as root:
   store=Store(root);path=Path(root)/'config.json'
   agent=assistant_module.Assistant(store,content,path);agent.generation=Generation(store)
   agent.save_media_preferences({'kind':'image','model':'krea2','size':'1600x1200','lora':'ccd','lora_strength':0.65})
   agent=assistant_module.Assistant(store,content,path);agent.generation=Generation(store)
   self.assertEqual(agent.media_preferences()['image']['size'],'1600x1200')
   self.assertEqual(agent.capabilities('image')['models'][1]['custom_size']['step'],16)
   # Krea2 is not wired to the 4090 media API: the workbench must refuse explicitly and
   # must never fall back to the old SSH path or silently switch models.
   with patch.object(Generation,'_spawn',lambda _s,*a:None):
    with self.assertRaisesRegex(ValueError,'未接入 4090 媒体 API'):
     agent.start({'message':'湖泊','tool':'image','request_id':'a'*32})
   self.assertEqual(agent.generation.listing()['jobs'],[])
   # the preference/retry path itself is verified on a model the media API serves
   agent.save_media_preferences({'kind':'image','model':'qi21','size':'1344x768'})
   with patch.object(Generation,'_spawn',lambda _s,*a:None):
    first=agent.start({'message':'湖泊','tool':'image','request_id':'a'*32})
   job=agent.generation.public(first['media_job']);self.assertEqual(job['settings']['size'],'1344x768')
   agent.generation.update(job['id'],status='error')
   with patch.object(Generation,'_spawn',lambda _s,*a:None):
    repeat=agent.start({'message':'再生成一次','request_id':'b'*32})
   self.assertEqual(agent.generation.public(repeat['media_job'])['settings']['size'],'1344x768')
   agent.generation.update(repeat['media_job'],status='error')
   agent.save_media_preferences({'kind':'image','model':'krea2','size':'1600x1200'})
   with patch.object(Generation,'_spawn',lambda _s,*a:None):
    with self.assertRaisesRegex(ValueError,'未接入 4090 媒体 API'):
     agent.start({'message':'换成 Krea2 Turbo','tool':'image','request_id':'c'*32})
   self.assertEqual(len(agent.generation.listing()['jobs']),2)
if __name__=='__main__':unittest.main()
