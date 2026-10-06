import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from generation.models import build
from generation.loras import KREA_LORAS
from generation.service import Generation
from server import Store, content, assistant_module

class LoraTests(unittest.TestCase):
    def test_adapter_is_in_sampler_path_and_trigger_reaches_encoder(self):
        for item in KREA_LORAS:
            identity,filename=item['id'],item['file']
            _,prompt,settings,graph=build({'model':'krea2','prompt':'mountain lake','lora':identity,'lora_strength':0.65,'lora_trigger':'test style'},'test')
            self.assertEqual(graph['sampler']['inputs']['model'],['selected_lora',0])
            self.assertEqual(graph['selected_lora']['inputs'],{'model':['unet',0],'lora_name':filename,'strength_model':0.65})
            self.assertEqual(graph['positive']['inputs']['text'],prompt)
            self.assertEqual(prompt,'test style\n\nmountain lake')
            self.assertEqual(settings['lora_file'],filename)
        _,_,_,graph=build({'model':'krea2','prompt':'mountain lake'},'base')
        self.assertNotIn('selected_lora',graph)
        self.assertEqual(graph['sampler']['inputs']['model'],['unet',0])

    def test_invalid_adapter_inputs_rejected(self):
        base={'model':'krea2','prompt':'lake','lora':'xhs'}
        for change in [{'lora':'../other'},{'model':'qi21'},{'lora_strength':float('nan')},{'lora_strength':float('inf')},{'lora_strength':-0.1},{'lora_strength':2.1},{'lora_strength':True},{'lora_trigger':['x']},{'lora_trigger':'a'*201}]:
            with self.subTest(change=change),self.assertRaises(ValueError):build({**base,**change},'bad')

    def test_preferences_retry_and_request_id_preserve_real_settings(self):
        with tempfile.TemporaryDirectory() as folder:
            store=Store(folder)
            agent=assistant_module.Assistant(store,content,Path(folder)/'missing.json')
            agent.generation=Generation(store)
            agent.save_media_preferences({'kind':'image','model':'krea2','size':'768x512','lora':'xhs','lora_strength':0.65,'lora_trigger':'old trigger'})
            restored=assistant_module.Assistant(store,content,Path(folder)/'missing.json')
            restored.generation=Generation(store)
            self.assertEqual(restored.media_preferences()['image']['lora'],'xhs')
            self.assertEqual(restored.capabilities('image')['models'][1]['loras'][0]['id'],'xhs')
            # Krea2 is not served by the 4090 media API: submitting it must fail loudly
            with patch.object(Generation,'_spawn',lambda _s,*a:None):
                with self.assertRaisesRegex(ValueError,'未接入 4090 媒体 API'):
                    restored.start({'message':'mountain lake','tool':'image','request_id':'a'*32})
            self.assertEqual(restored.generation.listing()['jobs'],[])
            # the stored Krea2 recipe is still resolved and validated with its real settings
            _,lora_prompt,lora_settings,_=build({'model':'krea2','prompt':'mountain lake','size':'768x512',
                                                 'lora':'xhs','lora_strength':0.65,'lora_trigger':'old trigger'},'pref')
            self.assertEqual(lora_settings['lora'],'xhs')
            self.assertEqual(lora_settings['lora_strength'],0.65)
            self.assertIn('old trigger',lora_prompt)
            restored.save_media_preferences({'kind':'image','model':'qi21','size':'768x512'})
            with patch.object(Generation,'_spawn',lambda _s,*a:None):
                first=restored.start({'message':'mountain lake','tool':'image','request_id':'a'*32})
            job=restored.generation.public(first['media_job'])
            self.assertEqual(job['settings']['size'],'768x512')
            self.assertEqual(job['input_prompt'],'mountain lake')
            restored.generation.update(job['id'],status='error')
            restored.save_media_preferences({'kind':'image','model':'qi21','size':'1024x1024'})
            with patch.object(Generation,'_spawn',lambda _s,*a:None):
                repeated=restored.start({'message':'再生成一次','request_id':'b'*32,'media':{'size':'1344x768'}})
            retry=restored.generation.public(repeated['media_job'])
            self.assertEqual(retry['settings']['size'],'1344x768')
            self.assertEqual(retry['input_prompt'],'mountain lake')  # inherits the referenced recipe
            self.assertNotEqual(retry['id'],job['id'])
            restored.generation.update(retry['id'],status='error')
            with patch.object(Generation,'_spawn',lambda _s,*a:None):
                switched=restored.start({'message':'换成 Qwen','tool':'image','reference_job':job['id'],'media':{'model':'qi21','size':'2048x2048'},'request_id':'c'*32})
            self.assertNotIn('lora',restored.generation.public(switched['media_job'])['settings'])
            restored.generation.update(switched['media_job'],status='error')
            with patch.object(Generation,'_spawn',lambda _s,*a:None):
                raw={'model':'krea2','prompt':'lake','lora':'xhs','request_id':'d'*32}
                with self.assertRaisesRegex(ValueError,'未接入 4090 媒体 API'):restored.generation.start(raw)
                with self.assertRaises(ValueError):restored.generation.start({**raw,'lora':'social','request_id':'e'*32})

if __name__=='__main__':unittest.main()
