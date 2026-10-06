"""Fixed read-only deployment queries, with no SSH or model execution."""
import copy
import http.client
import io
import json
from pathlib import Path
from types import SimpleNamespace
import sys
import tempfile
import unittest
import urllib.error
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from generation import bridge
from generation.inventory import enrich_inventory
from generation.service import Generation
from generation.media_http import MediaApiUnknown


H3_FILES = {
    'UNETLoader': ('unet_name', ['Minimax-h3_Singularity_ref2va_Pruned_v1.3_int8.safetensors',
                               'minimax_h3_ref2va_pruned_int8_convrot.safetensors']),
    'CLIPLoader': ('clip_name', ['qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors']),
    'VAELoader': ('vae_name', ['minimax_h3_video_vae_fp16.safetensors', 'minimax_h3_audio_vae_fp32.safetensors']),
    'LoraLoaderModelOnly': ('lora_name', ['H3_Motion_Booster.safetensors',
        'minimax_h3_ref2v_turbo_8step_v1.0_768p_comfyui_bf16.safetensors', 'unconfigured.safetensors']),
}
IMAGE_FILES = {
    8192: {'UNETLoader': ('unet_name', ['qwen_image_2.1_bf16.safetensors']),
           'CLIPLoader': ('clip_name', ['qwen3vl_8b_bf16.safetensors']),
           'VAELoader': ('vae_name', ['qwen_image_2.1_vae_bf16.safetensors']),
           'LoraLoaderModelOnly': ('lora_name', [])},
    8188: {'UNETLoader': ('unet_name', ['krea2_turbo_fp8.safetensors']),
           'CLIPLoader': ('clip_name', ['qwen3vl_4b_fp8_scaled.safetensors']),
           'VAELoader': ('vae_name', ['qwen_image_vae.safetensors']),
           'LoraLoaderModelOnly': ('lora_name', ['krea2_lora_ccd.safetensors'])},
}


def deployed_response(port, route):
    if port == 18088:
        return {'loaded': True, 'in_flight': 1, 'model': 'Qwen3.8',
                'config': {'api_key': 'private-status-key', 'model_path': '/private/weights'},
                'argv': ['private-command'], 'error': 'private-trace'}
    if route == '/system_stats':
        return {'system': {'comfyui_version': '0.34.0', 'ram_total': 64000, 'ram_free': 32000,
                           'argv': ['private-command'], 'cwd': '/private/workspace'},
                'devices': [{'name': 'NVIDIA GeForce RTX 4090', 'type': 'cuda', 'index': 0,
                             'vram_total': 24000, 'vram_free': 12000, 'private': 'private-device-field'}]}
    if route == '/queue':
        return {'queue_running': [[0, 'private-prompt-id', {'prompt': 'private-user-prompt'}, {}]],
                'queue_pending': [[1, 'private-pending-id', {'prompt': 'private-queued-prompt'}, {}]]}
    name = route.split('/')[-1]
    files = H3_FILES if port == 8191 else IMAGE_FILES[port]
    required = {}
    if name in files:
        field, values = files[name]
        required[field] = [copy.deepcopy(values), {}]
    if name == 'CLIPLoader':
        required['type'] = [[{8191: 'minimax', 8192: 'qwen_image', 8188: 'krea2'}[port]], {}]
    return {name: {'input': {'required': required}, 'output': ['MODEL'], 'python_module': '/private/node_path'}}


class BackendBridgeTests(unittest.TestCase):
    def query(self, action='backend_inventory', model=None, response=deployed_response):
        with patch.object(bridge, 'readonly_request', side_effect=response) as calls, \
             patch.object(bridge, 'request', side_effect=AssertionError('legacy HTTP writer forbidden')), \
             patch.object(bridge, 'ensure_idle', side_effect=AssertionError('submission path forbidden')):
            result = bridge.main({'action': action, 'model': model})
        return result, calls

    def test_all_inventory_uses_only_fixed_read_routes_and_redacts_queue_and_system(self):
        result, calls = self.query()
        self.assertEqual(set(result['services']), {'qi21', 'krea2', 'h3'})
        self.assertTrue(result['read_only'])
        self.assertEqual(result['source'], 'live_comfyui_over_existing_ssh')
        self.assertIn('不保证', ' '.join(result['limitations']))
        for call in calls.call_args_list:
            port, route = call.args
            if port == 18088:
                self.assertEqual(route, '/status')
            else:
                self.assertIn(port, (8188, 8191, 8192))
                self.assertIn(route, bridge.READ_ONLY_ROUTES)
            self.assertNotIn(route, ('/free', '/prompt', '/load', '/unload'))
        for service in result['services'].values():
            self.assertEqual(service['status'], 'ok')
            self.assertEqual(service['queue'], {'running': 1, 'pending': 1, 'busy': True})
            self.assertEqual(service['devices'][0]['name'], 'NVIDIA GeForce RTX 4090')
        serialized = json.dumps(result)
        self.assertNotIn('private-', serialized)
        self.assertNotIn('/private/', serialized)
        self.assertNotIn('python_module', serialized)
        self.assertEqual(result['inference_status']['state'], {'loaded': True, 'in_flight': 1, 'model': 'Qwen3.8'})

    def test_health_does_not_query_inventory_or_inference_when_model_selected(self):
        result, calls = self.query('backend_health', 'qi21')
        self.assertEqual(set(result['services']), {'qi21'})
        self.assertEqual({call.args for call in calls.call_args_list}, {(8192, '/queue'), (8192, '/system_stats')})
        self.assertNotIn('inventory', result['services']['qi21'])
        self.assertNotIn('inference_status', result)

    def test_model_and_extra_arguments_cannot_expand_readonly_scope(self):
        with patch.object(bridge, 'readonly_request') as read:
            for model in ('all', '8191', '../../models', 'qi21; touch x', [], 8191, True):
                with self.subTest(model=model), self.assertRaises(ValueError):
                    bridge.main({'action': 'backend_inventory', 'model': model})
            for key in ('command', 'path', 'route', 'url', 'port', 'node', 'headers', 'workflow'):
                with self.subTest(key=key), self.assertRaises(ValueError):
                    bridge.main({'action': 'backend_health', 'model': 'h3', key: 'arbitrary'})
            read.assert_not_called()
        for port, route in ((8191, '/prompt'), (8191, '/free'), (8191, '/object_info/ArbitraryNode'),
                            (8188, '/status'), (18088, '/queue'), (18088, '/status?command=x'),
                            (8080, '/system_stats')):
            with self.subTest(port=port, route=route), self.assertRaises(ValueError):
                bridge.readonly_request(port, route)

    def test_actual_http_transport_gets_fixed_loopback_without_proxy_or_redirect(self):
        response = io.BytesIO(json.dumps({'system': {}, 'devices': []}).encode())
        opener = SimpleNamespace(open=lambda req, timeout: response)
        with patch.object(bridge.urllib.request, 'build_opener', return_value=opener) as build:
            with patch.object(opener, 'open', wraps=opener.open) as opened:
                bridge.readonly_request(8192, '/system_stats')
        req = opened.call_args.args[0]
        self.assertEqual(req.full_url, 'http://127.0.0.1:8192/system_stats')
        self.assertEqual(req.get_method(), 'GET')
        self.assertIsNone(req.data)
        self.assertEqual(opened.call_args.kwargs['timeout'], 3)
        handlers = build.call_args.args
        self.assertEqual(handlers[0].proxies, {})
        redirect = next(handler for handler in handlers if isinstance(handler, bridge.NoReadOnlyRedirect))
        self.assertIsNone(redirect.redirect_request(req, None, 302, '', {}, 'https://arbitrary.example'))

    def test_unreachable_service_and_strata_do_not_hide_working_services(self):
        def response(port, route):
            if port in (8192, 18088):
                raise OSError('private-path-and-token')
            return deployed_response(port, route)
        result, _ = self.query(response=response)
        self.assertEqual(result['services']['qi21']['status'], 'unavailable')
        self.assertIsNone(result['services']['qi21']['queue'])
        self.assertEqual(result['services']['h3']['status'], 'ok')
        self.assertEqual(result['services']['krea2']['status'], 'ok')
        self.assertEqual(result['inference_status']['status'], 'unavailable')
        self.assertIsNone(result['inference_status']['state']['loaded'])
        self.assertNotIn('private-', json.dumps(result))

    def test_malformed_endpoint_and_loader_failures_remain_unknown(self):
        def response(port, route):
            if route == '/queue':
                return {'queue_running': 'private-bad-queue', 'queue_pending': []}
            if route == '/object_info/UNETLoader':
                return {'UNETLoader': {'input': {'required': {'unet_name': ['STRING', {}]}}}}
            return deployed_response(port, route)
        result, _ = self.query(model='h3', response=response)
        live = result['services']['h3']
        self.assertEqual(live['status'], 'partial')
        self.assertIsNone(live['queue'])
        self.assertEqual(live['inventory']['diffusion_models']['status'], 'unavailable')
        mapped = enrich_inventory(result)['services']['h3']['configured']
        self.assertTrue(all(row['available'] is None for row in mapped['checkpoints']))
        self.assertNotIn('private-', json.dumps(result))

    def test_truncated_http_response_is_isolated_to_its_endpoint(self):
        def response(port, route):
            if port == 8192 and route == '/system_stats':
                raise http.client.IncompleteRead(b'private-partial-response', 500)
            return deployed_response(port, route)
        result, _ = self.query(response=response)
        self.assertEqual(result['services']['qi21']['status'], 'partial')
        self.assertEqual(result['services']['qi21']['system'], {})
        self.assertEqual(result['services']['qi21']['queue']['running'], 1)
        self.assertEqual(result['services']['h3']['status'], 'ok')
        self.assertEqual(result['services']['krea2']['status'], 'ok')
        self.assertNotIn('private-', json.dumps(result))

    def test_missing_node_and_failed_node_have_distinct_availability(self):
        def response(port, route):
            if route.endswith('/MiniMaxH3Director'):
                return {}
            if route.endswith('/MiniMaxH3AddGuide'):
                raise urllib.error.HTTPError('ignored', 503, 'private-error', {}, io.BytesIO(b'private-body'))
            return deployed_response(port, route)
        result, _ = self.query(model='h3', response=response)
        live = result['services']['h3']
        self.assertIs(live['nodes']['MiniMaxH3Director']['available'], False)
        self.assertIsNone(live['nodes']['MiniMaxH3AddGuide']['available'])
        self.assertEqual(live['errors']['/object_info/MiniMaxH3AddGuide'], '服务 HTTP 503')
        workflows = {row['id']: row for row in enrich_inventory(result)['services']['h3']['configured']['workflows']}
        self.assertIs(workflows['t2v']['required_nodes_present'], False)
        self.assertIsNone(workflows['ir2v']['required_nodes_present'])
        self.assertIs(workflows['r2v']['required_nodes_present'], True)
        self.assertNotIn('private-', json.dumps(result))

    def test_loader_enums_do_not_return_absolute_or_traversal_paths(self):
        schema = {'input': {'required': {'unet_name': [['nested\\valid.safetensors', 'valid.safetensors',
            '/private/secret', '../secret', 'nested/../secret', 'C:\\secret', 'invalid\nname', None], {}]}}}
        self.assertEqual(bridge.enum_files(schema, 'unet_name'), ['nested/valid.safetensors', 'valid.safetensors'])

    def test_strata_unknown_shape_does_not_claim_loaded_or_idle(self):
        def response(port, route):
            if port == 18088:
                return {'backend': {'loaded': True}, 'model': '/private/model', 'in_flight': False,
                        'config': {'secret': 'private-secret'}}
            return deployed_response(port, route)
        result, _ = self.query('backend_health', response=response)
        state = result['inference_status']['state']
        self.assertEqual(state, {'loaded': None, 'in_flight': None})
        self.assertNotIn('private-', json.dumps(result))


class FakeBackends:
    """Stands in for MediaApi: serves the same raw probe document the SSH bridge used to,
    so the enrichment assertions below keep their original strength over the HTTP seam."""

    def __init__(self, main, fail=None):
        self.main, self.fail, self.calls = main, fail, []

    def backends(self, kind, model=None):
        self.calls.append((kind, model))
        if self.fail:
            raise self.fail
        return self.main({'action': 'backend_' + kind, 'model': model})


class BackendServiceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='backend-inventory-test-')
        self.addCleanup(temporary.cleanup)
        self.service = Generation(SimpleNamespace(workspace=Path(temporary.name)))

    def use(self, client):
        return patch.object(Generation, 'media', lambda _self: client)

    def test_service_inventory_maps_actual_files_and_keeps_static_choices_separate(self):
        with patch.object(bridge, 'readonly_request', side_effect=deployed_response), \
             self.use(client := FakeBackends(bridge.main)):
            result = self.service.backend_inventory()
        self.assertEqual(client.calls, [('inventory', None)])
        h3 = result['services']['h3']['configured']
        checkpoints = {row['id']: row for row in h3['checkpoints']}
        self.assertIs(checkpoints['singularity']['available'], True)
        self.assertIs(checkpoints['ref2va']['available'], True)
        self.assertIs(checkpoints['fl2va']['available'], False)
        accelerators = {row['id']: row for row in h3['accelerators']}
        self.assertIs(accelerators['ref8']['available'], True)
        self.assertIs(accelerators['ref4']['available'], False)
        self.assertIs(accelerators['none']['available'], True)
        self.assertEqual({row['file'] for row in h3['vaes']},
                         {'minimax_h3_video_vae_fp16.safetensors', 'minimax_h3_audio_vae_fp32.safetensors'})
        self.assertTrue(h3['text_encoders'][0]['type_available'])
        self.assertIn('unconfigured.safetensors', result['services']['h3']['inventory']['loras']['files'])
        self.assertNotIn('unconfigured.safetensors', [row['file'] for row in h3['loras']])
        qwen = result['services']['qi21']['configured']
        krea = result['services']['krea2']['configured']
        self.assertTrue(qwen['checkpoints'][0]['available'])
        self.assertEqual(qwen['text_encoders'][0]['type'], 'qwen_image')
        self.assertTrue(krea['text_encoders'][0]['type_available'])
        self.assertEqual(krea['text_encoders'][0]['file'], 'qwen3vl_4b_fp8_scaled.safetensors')
        krea_loras = {row['id']: row for row in krea['loras']}
        self.assertIs(krea_loras['ccd']['available'], True)
        self.assertIs(krea_loras['xhs']['available'], False)

    def test_encoder_file_presence_does_not_imply_encoder_type_support(self):
        def response(port, route):
            data = deployed_response(port, route)
            if route.endswith('/CLIPLoader'):
                data['CLIPLoader']['input']['required']['type'] = [['wrong_type'], {}]
            return data
        with patch.object(bridge, 'readonly_request', side_effect=response), \
             self.use(FakeBackends(bridge.main)):
            result = self.service.backend_inventory('qi21')
        encoder = result['services']['qi21']['configured']['text_encoders'][0]
        self.assertTrue(encoder['available'])
        self.assertFalse(encoder['type_available'])

    def test_conditional_reference_loaders_are_reported_without_overclaiming_ready(self):
        def response(port, route):
            return {} if route.endswith('/LoadVideo') else deployed_response(port, route)
        with patch.object(bridge, 'readonly_request', side_effect=response), \
             self.use(FakeBackends(bridge.main)):
            result = self.service.backend_inventory('h3')
        workflow = next(row for row in result['services']['h3']['configured']['workflows'] if row['id'] == 'r2v')
        self.assertTrue(workflow['required_nodes_present'])
        self.assertFalse(workflow['conditional_input_nodes']['videos']['present'])
        self.assertIn('不代表可成功推理', result['services']['h3']['configured']['workflow_check_scope'])

    def test_director_modes_also_report_conditional_acceleration_loader(self):
        def response(port, route):
            return {} if route.endswith('/LoraLoaderModelOnly') else deployed_response(port, route)
        with patch.object(bridge, 'readonly_request', side_effect=response), \
             self.use(FakeBackends(bridge.main)):
            result = self.service.backend_inventory('h3')
        for row in result['services']['h3']['configured']['workflows']:
            self.assertFalse(row['conditional_input_nodes']['accelerator_or_lora']['present'])
            self.assertTrue(row['required_nodes_present'])

    def test_service_rejects_unknown_model_and_mutating_action_before_any_http_call(self):
        client = FakeBackends(bridge.main)
        with self.use(client):
            for model in ('all', 'http://remote', [], False):
                with self.subTest(model=model), self.assertRaises(ValueError):
                    self.service.backend_inventory(model)
            for action in ('submit', 'free', 'load', 'unload', 'observe'):
                with self.subTest(action=action), self.assertRaises(ValueError):
                    self.service.backend_query(action, 'qi21')
            self.assertEqual(client.calls, [])

    def test_transport_failure_returns_unknown_not_static_deployment_or_trace(self):
        client = FakeBackends(bridge.main, fail=MediaApiUnknown('private-token-in-HTTPS-trace'))
        with self.use(client):
            result = self.service.backend_inventory()
        for item in result['services'].values():
            self.assertEqual(item['status'], 'unknown')
            self.assertIsNone(item['connected'])
            self.assertIsNone(item['queue'])
            self.assertTrue(all(row['available'] is None for row in item['configured']['checkpoints']))
        self.assertIsNone(result['inference_status']['state']['loaded'])
        self.assertNotIn('private-', json.dumps(result))

    def test_health_public_interface_never_adds_static_inventory(self):
        with patch.object(bridge, 'readonly_request', side_effect=deployed_response), \
             self.use(client := FakeBackends(bridge.main)):
            result = self.service.backend_health('h3')
        self.assertEqual(client.calls, [('health', 'h3')])
        self.assertNotIn('configured', result['services']['h3'])
        self.assertNotIn('inventory', result['services']['h3'])


if __name__ == '__main__':
    unittest.main()
