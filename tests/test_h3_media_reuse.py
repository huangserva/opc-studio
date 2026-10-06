"""H3 conversation recipe reuse with real saved inputs and no GPU/network work."""
import base64
import copy
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image
from generation.service import Generation
from server import Store, assistant_module, content


class H3MediaReuseTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='h3-media-reuse-test-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.store = Store(self.root / 'workspace')
        self.agent = assistant_module.Assistant(self.store, content, self.root / 'missing-config.json')
        self.agent.generation = Generation(self.store)
        self.addCleanup(self.agent.generation.close)
        first = self.upload_image('orange')
        reference_one = self.upload_image('blue')
        reference_two = self.upload_image('green')
        self.references = {'first': first, 'images': [reference_one, reference_two], 'videos': [], 'audios': []}
        self.loras = [{'id': 'motion', 'strength': 0.65}, {'id': 'realism', 'strength': 0.4}]
        self.recipe = {'model': 'h3', 'size': '768x448', 'frames': 124, 'workflow': 'ir2v',
                       'video_checkpoint': 'ref2va', 'video_accelerator': 'ref4',
                       'video_loras': copy.deepcopy(self.loras), 'references': copy.deepcopy(self.references)}

    def upload_image(self, color):
        stream = io.BytesIO()
        Image.new('RGB', (85, 49), color).save(stream, format='PNG')
        reference = self.agent.generation.upload_reference({
            'name': 'synthetic-' + color + '.png', 'data': base64.b64encode(stream.getvalue()).decode()})
        return reference['id']

    def send(self, payload):
        # A worker must never run, and neither the LLM nor the HTTPS media transport
        # may be touched while the workbench is only preparing the job.
        with patch.object(Generation, '_spawn') as worker, \
             patch.object(Generation, 'media', side_effect=AssertionError('no media API permitted')) as remote, \
             patch('generation.media_http.MediaApi.upload', side_effect=AssertionError('no uploads permitted')) as upload, \
             patch.object(self.agent, 'client', side_effect=AssertionError('must bypass LLM')) as client, \
             patch.object(self.agent.generation, 'start', wraps=self.agent.generation.start) as start:
            result = self.agent.start(copy.deepcopy(payload))
            remote.assert_not_called()
            upload.assert_not_called()
            client.assert_not_called()
        return self.agent.generation.public(result['media_job']), worker, start

    def original(self):
        payload = {'message': '黄昏下人物走过湖边', 'tool': 'video',
                   'media': copy.deepcopy(self.recipe), 'request_id': 'a' * 32}
        job, worker, _ = self.send(payload)
        self.assertEqual(worker.call_count, 1)
        self.assertEqual(job['settings']['engine'], 'native')
        self.agent.generation.update(job['id'], status='error', error='Synthetic test state; no GPU request sent')
        return self.agent.generation.public(job['id'])

    def assert_recipe_reused(self, original, reused, submitted):
        self.assertEqual(reused['model'], 'h3')
        self.assertEqual(reused['reference_job'], original['id'])
        for field in ('size', 'frames', 'workflow', 'video_checkpoint', 'video_accelerator', 'references'):
            with self.subTest(recipe_field=field):
                self.assertEqual(reused['settings'][field], original['settings'][field])
                self.assertEqual(submitted[field], original['settings'][field])
        # Saved job metadata contains human names and weight filenames. Replay arguments
        # must retain only the public LoRA identity/strength accepted by apply_video().
        self.assertTrue(all('file' in row and 'name' in row for row in original['settings']['video_loras']))
        self.assertEqual(submitted['video_loras'], self.loras)
        self.assertTrue(all(set(row) == {'id', 'strength'} for row in submitted['video_loras']))
        self.assertEqual([{'id': row['id'], 'strength': row['strength']} for row in reused['settings']['video_loras']], self.loras)
        self.assertEqual(reused['settings']['engine'], 'native')
        graph = json.loads((self.agent.generation.folder / reused['id'] / 'workflow.json').read_text())
        self.assertNotIn('5', graph)
        self.assertEqual(graph['guide_first']['inputs']['frame_idx'], 0)
        self.assertNotIn('guide_last', graph)
        self.assertEqual(graph['guider']['inputs']['conditioning'], ['guide_first', 0])
        inputs = graph['native_reference']['inputs']
        self.assertEqual([inputs['ref_images.ref_image_' + str(index)] for index in range(3)],
                         [['ref_image_0', 0], ['ref_image_1', 0], ['ref_image_2', 0]])
        expected_ids = [self.references['first'], *self.references['images']]
        self.assertEqual([graph['ref_image_' + str(index)]['inputs']['image'] for index in range(3)],
                         [f'article_studio_refs/{identity}/reference.png' for identity in expected_ids])
        self.assertEqual(graph['scheduler']['inputs']['steps'], 4)
        self.assertEqual(graph['sigma_shift']['inputs']['model'], ['video_lora_1', 0])
        self.assertEqual(graph['video_lora_1']['inputs']['model'], ['video_lora_0', 0])
        self.assertEqual(graph['video_lora_0']['inputs']['model'], ['10', 0])
        return graph

    def test_native_h3_history_retry_preserves_complete_recipe_and_sanitizes_saved_loras(self):
        original = self.original()
        retried, worker, start = self.send({'message': '再生成一次', 'request_id': 'b' * 32})
        self.assertNotEqual(retried['id'], original['id'])
        self.assertEqual(worker.call_count, 1)
        self.assertEqual(start.call_count, 1)
        submitted = start.call_args.args[0]
        graph = self.assert_recipe_reused(original, retried, submitted)
        self.assertEqual(retried['prompt'], original['prompt'])
        self.assertEqual(graph['native_reference']['inputs']['prompt'], original['prompt'])
        self.assertEqual(len(self.agent.turns), 2)

    def test_duplicate_h3_retry_reuses_job_without_new_thread_or_resolving_new_recipe(self):
        original = self.original()
        payload = {'message': '重试', 'reference_job': original['id'], 'request_id': 'c' * 32}
        first, first_worker, first_start = self.send(payload)
        graph = self.assert_recipe_reused(original, first, first_start.call_args.args[0])
        self.assertEqual(graph['native_reference']['inputs']['prompt'], original['prompt'])
        second, second_worker, second_start = self.send(payload)
        self.assertEqual(first['id'], second['id'])
        self.assertEqual(first['settings']['seed'], second['settings']['seed'])
        self.assertEqual(first_worker.call_count, 1)
        self.assertEqual(second_worker.call_count, 0)
        self.assertEqual(second_start.call_count, 0)
        self.assertEqual(len(self.agent.generation.jobs), 2)
        self.assertEqual(len(self.agent.turns), 2)
        with self.assertRaisesRegex(ValueError, '请求编号已使用'):
            self.send({**payload, 'message': '修改过的描述'})
        self.assertEqual(len(self.agent.generation.jobs), 2)

    def test_explicit_history_reference_reuses_recipe_with_edited_prompt_after_other_chat(self):
        original = self.original()
        self.agent.turns.append({'id': 'ordinary-chat', 'prompt': '聊聊别的事情', 'actions': []})
        edited = '清晨下人物停在湖边，镜头缓慢拉近'
        payload = {'message': edited, 'tool': 'video', 'reference_job': original['id'], 'request_id': 'd' * 32}
        reused, worker, start = self.send(payload)
        self.assertEqual(worker.call_count, 1)
        graph = self.assert_recipe_reused(original, reused, start.call_args.args[0])
        self.assertEqual(reused['prompt'], edited)
        self.assertEqual(reused['input_prompt'], edited)
        self.assertEqual(graph['native_reference']['inputs']['prompt'], edited)
        self.assertEqual(self.agent.generation.public(original['id'])['prompt'], original['prompt'])


if __name__ == '__main__':
    unittest.main()
