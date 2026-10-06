import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from server import Store, content, assistant_module
from generation.service import Generation

class MediaConversationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.store=Store(self.root/'workspace');self.config=self.root/'missing-config.json'
        self.agent=assistant_module.Assistant(self.store,content,self.config)
        self.agent.generation=Generation(self.store)
        self.payload={'model':'qi21','prompt':'红军长征会师的历史场景','request_id':'a'*32}
    def tearDown(self):self.tmp.cleanup()
    def test_popup_submission_is_persistent_and_idempotent(self):
        with patch('generation.service.threading.Thread') as thread:
            first=self.agent.start_media(self.payload);second=self.agent.start_media(self.payload)
        self.assertEqual(first['id'],second['id']);self.assertEqual(thread.call_count,1)
        self.assertEqual(len(self.agent.turns),1)
        self.assertEqual(self.agent.turns[0]['prompt'],self.payload['prompt'])
        self.agent.generation.update(first['id'],status='error',error='4090 busy')
        restarted=assistant_module.Assistant(self.store,content,self.config);restarted.generation=Generation(self.store)
        messages=restarted.status()['messages'];self.assertEqual(len(messages),1)
        self.assertEqual(messages[0]['actions'][0]['media_job'],first['id'])
        self.assertEqual(restarted.generation.public(first['id'])['error'],'4090 busy')
        with self.assertRaises(ValueError):restarted.start_media({**self.payload,'prompt':'different'})
    def test_missing_receipt_is_recovered_without_resubmission(self):
        with patch('generation.service.threading.Thread'):
            job=self.agent.generation.start(self.payload,origin='composer',conversation_id=self.agent.conversation_id)
        self.assertEqual(self.agent.turns,[])
        self.agent.status();self.agent.status()
        self.assertEqual(len(self.agent.turns),1)
        self.assertEqual(self.agent.turns[0]['actions'][0]['media_job'],job['id'])
        self.assertEqual(len(self.agent.generation.jobs),1)
    def test_new_chat_does_not_resurrect_old_media_requests(self):
        with patch('generation.service.threading.Thread'):
            job=self.agent.start_media(self.payload)
        previous=self.agent.conversation_id;self.agent.new_chat()
        self.assertNotEqual(previous,self.agent.conversation_id)
        self.assertEqual(self.agent.status()['messages'],[])
        self.assertEqual(len(self.agent.generation.jobs),1)
        archives=list(self.agent.folder.glob('history-*.json'))
        self.assertEqual(json.loads(archives[0].read_text())[0]['actions'][0]['media_job'],job['id'])

    def send_media(self, **changes):
        payload={'message':'@生图 一只猫','request_id':'b'*32,**changes}
        with patch.object(Generation,'_spawn') as worker, patch.object(self.agent,'client',side_effect=AssertionError('must bypass LLM')):
            result=self.agent.start(payload)
        return self.agent.generation.public(result['media_job']),worker

    def test_explicit_tool_without_chat_config_and_preferences_survive_restart(self):
        self.agent.save_media_preferences({'kind':'image','model':'qi21','size':'1344x768'})
        self.agent=assistant_module.Assistant(self.store,content,self.config);self.agent.generation=Generation(self.store)
        # a Krea2 preference must be refused by the media layer, never served over SSH
        self.agent.save_media_preferences({'kind':'image','model':'krea2','size':'1344x768'})
        with patch.object(Generation,'_spawn',lambda _s,*a:None), \
             patch.object(self.agent,'client',side_effect=AssertionError('must bypass LLM')):
            with self.assertRaisesRegex(ValueError,'未接入 4090 媒体 API'):
                self.agent.start({'message':'@生图 一只猫','request_id':'a'*32})
        self.assertEqual(self.agent.generation.listing()['jobs'],[])
        self.agent.save_media_preferences({'kind':'image','model':'qi21','size':'1344x768'})
        self.agent=assistant_module.Assistant(self.store,content,self.config);self.agent.generation=Generation(self.store)
        job,worker=self.send_media()
        self.assertEqual(job['model'],'qi21');self.assertEqual(job['settings']['size'],'1344x768')
        self.assertEqual(job['prompt'],'一只猫');self.assertEqual(worker.call_count,1)
        duplicate,_=self.send_media();self.assertEqual(job['id'],duplicate['id'])
        with self.assertRaises(ValueError):self.send_media(message='@生图 不同描述')

    def test_qwen_2k_preference_survives_restart_and_is_used_by_chat_generation(self):
        self.agent.save_media_preferences({'kind':'image','model':'qi21','size':'2752x1536'})
        self.agent=assistant_module.Assistant(self.store,content,self.config)
        self.agent.generation=Generation(self.store)
        self.assertEqual(self.agent.capabilities('image')['preferences']['image']['size'],'2752x1536')
        job,_=self.send_media()
        self.assertEqual(job['settings']['size'],'2752x1536')
        graph=json.loads((self.agent.generation.folder/job['id']/'workflow.json').read_text())
        self.assertEqual((graph['6']['inputs']['width'],graph['6']['inputs']['height']),(2752,1536))

    def test_selected_skill_style_reaches_graph_and_survives_job_restart(self):
        from image_skill import ImageSkill
        root=self.root/'skill';(root/'modules/gpt-prompts/references').mkdir(parents=True)
        (root/'SKILL.md').write_text('# image skill')
        (root/'modules/gpt-prompts/references/styles.py').write_text("STYLES={'art':{'styles':{'ink':{'style_instruction':'水墨留白'}}}}")
        self.agent.image_skill=ImageSkill(root)
        self.agent.save_media_preferences({'kind':'image','model':'qi21','size':'2752x1536','image_style':'gpt:ink'})
        job,_=self.send_media(message='两只橙猫',tool='image',media={'image_style':'gpt:ink'})
        self.assertEqual(job['image_skill']['user_prompt'],'两只橙猫')
        self.assertIn('水墨留白',job['prompt'])
        graph=json.loads((self.agent.generation.folder/job['id']/'workflow.json').read_text())
        self.assertEqual(graph['4']['inputs']['prompt'],job['prompt'])
        self.assertEqual(graph['6']['inputs']['width'],2752)
        self.agent.generation.update(job['id'],status='error')
        repeated,_=self.send_media(message='再生成一次',request_id='c'*32)
        self.assertEqual(repeated['prompt'],job['prompt'])
        self.agent.generation.update(repeated['id'],status='error')
        restored=Generation(self.store)
        self.assertEqual(restored.public(job['id'])['image_skill']['style'],'gpt:ink')

    def test_retry_inherits_prompt_and_model_but_creates_new_job(self):
        original,_=self.send_media()
        self.agent.generation.update(original['id'],status='error')
        retried,_=self.send_media(message='再次生成一次',request_id='c'*32)
        self.assertNotEqual(retried['id'],original['id'])
        self.assertEqual(retried['prompt'],original['prompt'])
        self.assertEqual(retried['reference_job'],original['id'])
        duplicate,_=self.send_media(message='再次生成一次',request_id='c'*32)
        self.assertEqual(retried['id'],duplicate['id'])
        self.agent.generation.update(retried['id'],status='error')
        self.agent.save_media_preferences({'kind':'image','model':'krea2','size':'1344x768'})
        with patch.object(Generation,'_spawn',lambda _s,*a:None), \
             patch.object(self.agent,'client',side_effect=AssertionError('must bypass LLM')):
            with self.assertRaisesRegex(ValueError,'未接入 4090 媒体 API'):
                self.agent.start({'message':'换成 Krea2','request_id':'d'*32})

    def test_greeting_and_stale_reference_never_regenerate(self):
        original,_=self.send_media()
        self.agent.generation.update(original['id'],status='error')
        self.assertIsNone(self.agent.direct_media({'message':'你好'}))
        self.assertIsNone(self.agent.direct_media({'message':'对的'}))
        self.agent.turns.append({'id':'chat','actions':[],'prompt':'别的事情'})
        with self.assertRaises(ValueError):self.send_media(message='再生成一次',request_id='c'*32)
        selected,_=self.send_media(message='再生成一次',reference_job=original['id'],request_id='c'*32)
        self.assertEqual(selected['reference_job'],original['id'])
        self.agent.new_chat()
        with self.assertRaises(ValueError):self.send_media(message='再生成一次',reference_job=original['id'],request_id='d'*32)

    def test_video_and_invalid_tool_model_combinations(self):
        job,_=self.send_media(message='海边日落',tool='video',media={'model':'h3','size':'432x768','frames':124},unsaved=True)
        self.assertEqual(job['kind'],'video');self.assertEqual(job['settings']['frames'],124)
        with self.assertRaises(ValueError):self.send_media(message='猫',tool='image',media={'model':'h3'})
        with self.assertRaises(ValueError):self.send_media(message='@生图')
        with self.assertRaises(ValueError):self.agent.save_media_preferences({'kind':'image','model':'h3'})

if __name__=='__main__':unittest.main()
