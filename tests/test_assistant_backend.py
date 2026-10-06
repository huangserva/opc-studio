"""Assistant backend queries and truthful media entry points, without remote calls."""
import copy
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from assistant_agent import Assistant, LABELS, SYSTEM, TOOLS
from assistant_policy import READ_TOOLS, policy_for
from generation.models import MODELS
from generation.video_options import REFERENCE_LIMITS


class AssistantBackendTests(unittest.TestCase):
    def setUp(self):
        # Bypass the constructor so the tests cannot start Pi or a model service.
        self.agent = Assistant.__new__(Assistant)
        self.agent.generation = Mock(spec=['backend_inventory', 'backend_health', 'start'])
        self.agent.web = SimpleNamespace(capabilities=lambda: {'enabled': True})
        self.agent.memory_tools = SimpleNamespace(capabilities=lambda: {})
        self.agent.image_skill = SimpleNamespace(status=lambda: {'installed': True})
        self.agent.media_preferences = Mock(return_value={
            'image': {'model': 'qi21', 'size': '1024x1024'},
            'video': {'model': 'h3', 'size': '768x432', 'frames': 56},
        })
        self.job = {'cancel': False, 'context': {'unsaved': True},
                    'policy': policy_for('H3 服务现在有哪些 LoRA、节点，显存和排队情况如何？')}

    def test_tools_are_read_only_with_bounded_optional_model(self):
        schemas = {tool['function']['name']: tool['function'] for tool in TOOLS}
        for name in ('get_backend_inventory', 'get_backend_health'):
            with self.subTest(name=name):
                self.assertIn(name, READ_TOOLS)
                self.assertIn(name, LABELS)
                self.assertIn(name, self.job['policy']['allowed_tools'])
                parameters = schemas[name]['parameters']
                self.assertEqual(set(parameters['properties']), {'model'})
                self.assertEqual(parameters['properties']['model']['enum'], ['qi21', 'krea2', 'h3'])
                self.assertEqual(parameters['required'], [])
                self.assertFalse(parameters['additionalProperties'])
        self.assertNotIn('generate_media', self.job['policy']['allowed_tools'])

    def test_inventory_all_delegates_and_preserves_partial_failure(self):
        result = {'checked_at': '2026-10-05T08:00:00+00:00', 'source': 'fixed_comfy_services',
                  'scope': 'fixed services', 'limitations': ['files do not prove loaded models'],
                  'services': {'h3': {'ok': True, 'loras': ['example.safetensors']},
                               'qi21': {'ok': False, 'error': 'connection timeout'}}}
        self.agent.generation.backend_inventory.return_value = result
        self.assertEqual(self.agent.execute('get_backend_inventory', {}, self.job), result)
        self.agent.generation.backend_inventory.assert_called_once_with(model=None)
        self.agent.generation.backend_health.assert_not_called()
        self.agent.generation.start.assert_not_called()

    def test_health_selected_model_is_a_read_even_with_unsaved_work(self):
        result = {'checked_at': '2026-10-05T08:00:01+00:00', 'source': 'fixed_comfy_services',
                  'services': {'h3': {'ok': True, 'queue': {'running': 0, 'pending': 1},
                                      'loaded_models': {'known': False}}}}
        self.agent.generation.backend_health.return_value = result
        self.assertEqual(self.agent.execute('get_backend_health', {'model': 'h3'}, self.job), result)
        self.agent.generation.backend_health.assert_called_once_with(model='h3')
        self.agent.generation.start.assert_not_called()

    def test_each_configured_model_filter_reaches_backend_unchanged(self):
        for name, method in (('get_backend_inventory', self.agent.generation.backend_inventory),
                             ('get_backend_health', self.agent.generation.backend_health)):
            for model in ('qi21', 'krea2', 'h3'):
                with self.subTest(tool=name, model=model):
                    self.agent.execute(name, {'model': model}, self.job)
                    method.assert_called_with(model=model)

    def test_rejects_unknown_models_and_input_paths_before_any_query(self):
        invalid = [{'model': 'all'}, {'model': 'H3'}, {'model': 'other'}, {'model': None},
                   {'model': True}, {'model': ['h3']}, {'model': '../private'},
                   {'path': '/private/model'}, {'command': 'nvidia-smi'},
                   {'model': 'h3', 'endpoint': 'http://other-service'}, []]
        for name in ('get_backend_inventory', 'get_backend_health'):
            for args in invalid:
                with self.subTest(tool=name, args=args), self.assertRaises(ValueError):
                    self.agent.execute(name, args, self.job)
        self.agent.generation.backend_inventory.assert_not_called()
        self.agent.generation.backend_health.assert_not_called()

    def test_cancel_and_policy_gate_prevent_queries(self):
        for job in ({**self.job, 'cancel': True},
                    {**self.job, 'policy': {'allowed_tools': []}},
                    {**self.job, 'policy': policy_for('你好')}):
            with self.subTest(job=job), self.assertRaises(ValueError):
                self.agent.execute('get_backend_health', {}, job)
        self.agent.generation.backend_health.assert_not_called()

    def test_missing_connection_reports_the_actual_gap(self):
        self.agent.generation = None
        result = self.agent.execute('get_backend_inventory', {'model': 'h3'}, self.job)
        self.assertFalse(result['query_performed'])
        self.assertEqual(set(result['services']), {'h3'})
        self.assertFalse(result['services']['h3']['ok'])
        self.assertIn('后端尚未连接', result['services']['h3']['error'])
        self.assertNotIn('无权限', result['summary'])
        self.assertTrue(result['checked_at'])
        all_services = self.agent.execute('get_backend_health', {}, self.job)
        self.assertEqual(set(all_services['services']), {'qi21', 'krea2', 'h3'})

    def test_backend_errors_remain_real_errors(self):
        self.agent.generation.backend_health.side_effect = ValueError('H3 服务连接超时')
        with self.assertRaisesRegex(ValueError, 'H3 服务连接超时'):
            self.agent.execute('get_backend_health', {'model': 'h3'}, self.job)
        self.agent.generation.start.assert_not_called()

    def test_capabilities_expose_h3_options_and_exact_validation_scope(self):
        capabilities = self.agent.capabilities('video')
        self.assertEqual(len(capabilities['models']), 1)
        h3 = capabilities['models'][0]
        self.assertEqual({wf['id'] for wf in h3['workflows']},
                         {'t2v', 'i2v', 'fl2v', 'r2v', 'ir2v', 'flr2v'})
        for key in ('workflows', 'checkpoints', 'accelerators', 'video_loras'):
            self.assertEqual(h3[key], MODELS['h3'][key])
        self.assertTrue(h3['reference_images'])
        self.assertEqual(h3['reference_media']['limits'], REFERENCE_LIMITS)
        self.assertTrue(h3['reference_media']['images_include_first_last'])
        self.assertTrue(h3['reference_media']['total_includes_first_last'])
        self.assertEqual(h3['reference_media']['entry_points'], ['generation_lab'])
        self.assertEqual(h3['reference_media']['recipe_reuse_entry_points'], ['manual_composer'])
        self.assertEqual(h3['reference_media']['sizes'], ['768x448', '448x768'])
        self.assertEqual(h3['reference_media']['source_total_duration_seconds'], {'videos': 15, 'audios': 15})
        tests = h3['validation_scope']['live_gpu_tests']
        self.assertEqual({row['workflow'] for row in tests}, {'r2v', 'ir2v', 'flr2v'})
        for row in tests:
            self.assertEqual(row['checkpoint'], 'singularity')
            self.assertEqual(row['accelerator'], 'ref8')
            self.assertEqual(row['size'], '768x448')
        # Capability callers cannot mutate the shared configuration or limits.
        h3['workflows'].clear()
        h3['reference_media']['limits']['images'] = 100
        self.assertEqual(len(MODELS['h3']['workflows']), 6)
        self.assertEqual(REFERENCE_LIMITS['images'], 9)

    def test_capabilities_keep_existing_shapes_and_separate_entry_points(self):
        capabilities = self.agent.capabilities('all', config={'model': 'mock-chat', 'context_tokens': 10000,
                                                             'max_output_tokens': 1000})
        self.assertEqual(capabilities['manual_features'], ['生图', '视频'])
        self.assertIsInstance(capabilities['more_settings'], list)
        self.assertEqual(capabilities['preferences']['video']['model'], 'h3')
        self.assertEqual(capabilities['chat']['model'], 'mock-chat')
        by_model = {row['id']: row for row in capabilities['models']}
        self.assertFalse(by_model['qi21']['reference_images'])
        self.assertFalse(by_model['krea2']['reference_images'])
        entries = capabilities['entry_points']
        lab = entries['generation_lab']
        self.assertTrue(lab['reference_upload'])
        self.assertTrue(lab['reference_selection'])
        self.assertEqual(set(lab['h3_workflows']), {wf['id'] for wf in MODELS['h3']['workflows']})
        manual = entries['manual_composer']
        self.assertEqual(manual['h3_workflows'], ['t2v'])
        self.assertFalse(manual['reference_upload'])
        self.assertFalse(manual['reference_selection'])
        self.assertEqual(manual['h3_reference_media'], [])
        self.assertEqual(manual['h3_options'], [])
        self.assertTrue(manual['source_job_recipe_reuse'])
        self.assertEqual(set(manual['source_job_h3_workflows']), {wf['id'] for wf in MODELS['h3']['workflows']})
        self.assertEqual(manual['source_job_reference_media'], ['image', 'video', 'audio'])
        self.assertIn('references', manual['source_job_recipe_fields'])
        llm = entries['generate_media']
        self.assertEqual(llm['h3_workflows'], ['t2v'])
        self.assertFalse(llm['reference_upload'])
        self.assertFalse(llm['reference_ids'])
        self.assertFalse(llm['chat_attachments_as_references'])
        actual = next(tool['function']['parameters'] for tool in TOOLS
                      if tool['function']['name'] == 'generate_media')
        self.assertEqual(llm['parameters'], actual['properties'])
        self.assertEqual(llm['required'], actual['required'])
        self.assertNotIn('references', llm['parameters'])
        self.assertNotIn('workflow', llm['parameters'])
        self.assertTrue(capabilities['limits']['media_reference_images'])
        self.assertEqual(capabilities['limits']['media_reference_models'], ['h3'])
        self.assertEqual(capabilities['limits']['media_reference_entry_points'], ['generation_lab'])
        self.assertEqual(capabilities['limits']['media_reference_recipe_reuse_entry_points'], ['manual_composer'])
        self.assertFalse(capabilities['limits']['llm_generate_media_reference_images'])
        self.assertIn('get_backend_inventory', capabilities['readonly_tools'])
        self.assertIn('get_backend_health', capabilities['readonly_tools'])

    def test_llm_tool_cannot_smuggle_manual_reference_or_private_inputs(self):
        job = {'id': 'mock-turn', 'cancel': False, 'context': {},
               'policy': policy_for('用 H3 生成海边的视频')}
        base = {'model': 'h3', 'prompt': '海边日落'}
        for field, value in (('references', {'first': 'a' * 32}), ('workflow', 'ir2v'),
                             ('video_checkpoint', 'hybrid'), ('video_accelerator', 'ref4'),
                             ('video_loras', [{'id': 'motion'}]), ('_references', {}),
                             ('path', '/private/input.mp4'), ('endpoint', 'http://other-service')):
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.agent.execute('generate_media', {**base, field: value}, job)
        self.agent.generation.start.assert_not_called()

    def test_existing_text_to_video_tool_still_submits_once(self):
        self.agent.generation.start.return_value = {'id': 'mock-video', 'status': 'connecting',
                                                   'model_name': 'MiniMax H3 · 4090'}
        job = {'id': 'mock-turn', 'cancel': False, 'context': {}, 'conversation_id': 'mock-conversation',
               'policy': policy_for('用 H3 生成海边的视频')}
        args = {'model': 'h3', 'prompt': '海边日落', 'size': '768x448', 'frames': 124}
        result = self.agent.execute('generate_media', copy.deepcopy(args), job)
        self.assertEqual(result['media_job'], 'mock-video')
        self.agent.generation.start.assert_called_once_with(
            {**args, 'slug': None, 'version': None, 'request_id': 'mock-turn'},
            conversation_id='mock-conversation')
        with self.assertRaises(ValueError):
            self.agent.execute('generate_media', args, job)
        self.agent.generation.start.assert_called_once()

    def test_prompt_requires_real_queries_and_reports_scope(self):
        self.assertIn('先实时调用 get_backend_inventory', SYSTEM)
        self.assertIn('先实时调用 get_backend_health', SYSTEM)
        self.assertIn('未执行查询不能说无权限', SYSTEM)
        self.assertIn('文件可选不等于模型已加载', SYSTEM)
        self.assertIn('不能把手动界面的能力说成 LLM 已能调用', SYSTEM)
        self.assertIn('不能把历史任务参数复用说成手动面板可上传新参考或自由选择六模式', SYSTEM)
        self.assertNotIn('单独生成页和手动生成面板中的 H3 支持六种工作流及上传', SYSTEM)
        self.assertNotIn('当前媒体生成仅支持文生图/文生视频', SYSTEM)


if __name__ == '__main__':
    unittest.main()
