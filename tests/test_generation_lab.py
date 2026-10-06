import sys,tempfile,unittest,json
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from server import Store,content,assistant_module
from generation.service import Generation
from generation.models import build
class GenerationLabTests(unittest.TestCase):
 def test_lab_submission_no_chat_side_effects_idempotence_and_sampling(self):
  with tempfile.TemporaryDirectory() as root:
   store=Store(root);agent=assistant_module.Assistant(store,content,Path(root)/'missing.json');agent.generation=Generation(store)
   cid=agent.conversation_id;prefs=agent.media_preferences()
   payload={'model':'krea2','prompt':'a lake','size':'1600x1200','steps':10,'cfg':1.2,'lora':'ccd','lora_strength':0.65,'seed':42,'request_id':'a'*32,'origin':'composer'}
   # Krea2 is not served by the 4090 media API: the lab must refuse it twice under the same
   # request id and must not leave a half-created job behind
   with patch.object(Generation,'_spawn') as worker:
    for attempt in range(2):
     with self.subTest(attempt=attempt), self.assertRaisesRegex(ValueError,'未接入 4090 媒体 API'):
      agent.start_lab_media(payload)
   self.assertEqual(worker.call_count,0)
   self.assertEqual(agent.generation.listing(origin='lab',kind='image')['jobs'],[])
   self.assertEqual(agent.turns,[]);self.assertEqual(agent.conversation_id,cid)
   self.assertEqual(agent.media_preferences(),prefs)
   # the Krea2 sampling and LoRA wiring the lab would have shipped stays validated
   _,_,krea_settings,krea_graph=build({k:payload[k] for k in ('model','prompt','size','steps','cfg','lora','lora_strength','seed')},'krea-audit')
   self.assertEqual(krea_graph['sampler']['inputs']['steps'],10)
   self.assertEqual(krea_graph['sampler']['inputs']['cfg'],1.2)
   self.assertEqual(krea_graph['sampler']['inputs']['model'],['selected_lora',0])
   self.assertEqual(krea_settings['cfg'],1.2)
   # the lab job mechanics themselves are verified on a model the media API serves
   served={'model':'qi21','prompt':'a lake','size':'1024x1024','steps':10,'cfg':1.2,'seed':42,'request_id':'b'*32,'origin':'composer'}
   with patch.object(Generation,'_spawn') as worker:
    job=agent.start_lab_media(served);repeat=agent.start_lab_media(served)
   self.assertEqual(worker.call_count,1);self.assertEqual(job['id'],repeat['id'])
   self.assertEqual(job['origin'],'lab');self.assertIsNone(job['conversation_id']);self.assertEqual(agent.turns,[])
   self.assertEqual(agent.conversation_id,cid);self.assertEqual(agent.media_preferences(),prefs)
   graph=json.loads((agent.generation.folder/job['id']/'workflow.json').read_text())
   self.assertEqual(graph['8']['inputs']['steps'],10);self.assertEqual(graph['8']['inputs']['cfg'],1.2)
   self.assertEqual(job['settings']['cfg'],1.2)
   self.assertEqual(len(agent.generation.listing(origin='lab',kind='image')['jobs']),1)
   self.assertEqual(agent.generation.listing(origin='lab',kind='video')['jobs'],[])
   with self.assertRaises(ValueError):agent.start_lab_media({**served,'cfg':1.3,'request_id':'c'*32})
   agent.recover_media_turns();self.assertEqual(agent.turns,[])
 def test_qwen_sampling_parameters_and_invalid_numbers(self):
  _,_,settings,graph=build({'model':'qi21','prompt':'lake','steps':25,'cfg':3.1},'test')
  self.assertEqual(graph['8']['inputs']['steps'],25);self.assertEqual(graph['8']['inputs']['cfg'],3.1)
  for changes in [{'steps':0},{'steps':61},{'steps':1.5},{'steps':True},{'cfg':float('nan')},{'cfg':float('inf')},{'cfg':21},{'cfg':True}]:
   with self.subTest(changes=changes),self.assertRaises(ValueError):build({'model':'krea2','prompt':'lake',**changes},'bad')
if __name__=='__main__':unittest.main()
