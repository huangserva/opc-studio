"""The app must reject inputs that ComfyUI's static prompt validation ignores."""
import json
import os
from pathlib import Path
import unittest

from generation.models import build
from generation.video_options import normalize_references


def descriptor(identity, kind='image', **changes):
    extension = {'image': 'png', 'video': 'mp4', 'audio': 'wav'}[kind]
    result = {'id': identity, 'kind': kind, 'name': identity,
              'remote_file': f'article_studio_refs/{identity}/reference.{extension}'}
    if kind in ('image', 'video'):
        result.update(width=768, height=448)
    if kind == 'video':
        result.update(duration=56/24, frames=56, has_audio=False, source_duration=3)
    if kind == 'audio':
        result.update(duration=3)
    result.update(changes)
    return result


def native_payload(mode='flr2v'):
    references = {'images': ['subject'], 'videos': ['silent', 'sound'], 'audios': ['voice']}
    resolved = {'images': [descriptor('subject')],
                'videos': [descriptor('silent', 'video'), descriptor('sound', 'video', has_audio=True)],
                'audios': [descriptor('voice', 'audio')]}
    if mode in ('ir2v', 'flr2v'):
        references['first'] = 'first'
        resolved['first'] = descriptor('first', imageB64='must-never-enter-native-graph')
    if mode == 'flr2v':
        references['last'] = 'last'
        resolved['last'] = descriptor('last', imageB64='must-never-enter-native-graph')
    return {'model': 'h3', 'size': '768x448', 'frames': 124, 'seed': 42,
            'workflow': mode, 'prompt': 'Use <Picture 1> and <Video 2> with <Audio 2>.',
            'references': references, '_references': resolved}


class ReferenceNormalizationTests(unittest.TestCase):
    def test_legacy_empty_and_canonical_inputs_have_one_stable_shape(self):
        expected = {'first': 'a', 'images': ['b', 'c'], 'videos': ['v'], 'audios': []}
        actual = normalize_references({'first': ' a ', 'last': '', 'reference': 'b',
                                       'images': [None, '', ' ', 'c'], 'videos': ['v'], 'audios': []}, 'ir2v')
        self.assertEqual(actual, expected)
        self.assertEqual(normalize_references(actual, 'ir2v'), actual)
        self.assertEqual(normalize_references({}), {'images': [], 'videos': [], 'audios': []})

    def test_invalid_types_unknown_roles_and_cross_role_duplicates_are_rejected(self):
        cases = [None, [], {'unknown': 'a'}, {'first': []}, {'reference': 2}, {'images': 'a'},
                 {'videos': None}, {'audios': [False]}, {'images': ['a', 'a']},
                 {'first': 'a', 'images': ['a']}, {'first': 'a', 'last': 'a'},
                 {'images': ['a'], 'audios': ['a']}, {'reference': 'a', 'images': ['a']},
                 {'images': ['../bad']}, {'videos': ['x\\bad']}, {'audios': ['x y']}]
        for values in cases:
            with self.subTest(values=values), self.assertRaises(ValueError):
                normalize_references(values)

    def test_exact_limits_include_anchors_and_count_video_soundtrack_as_one_asset(self):
        refs = {'first': 'first', 'last': 'last', 'images': [f'i{i}' for i in range(7)],
                'videos': ['v0', 'v1', 'v2']}
        normalized = normalize_references(refs, 'flr2v')
        self.assertEqual(len(normalized['images']), 7)
        self.assertEqual(len(normalize_references({'audios': ['a0', 'a1', 'a2']}, 'r2v')['audios']), 3)
        for values in [{**refs, 'images': [f'i{i}' for i in range(8)]},
                       {**refs, 'audios': ['a']}, {'videos': [f'v{i}' for i in range(4)]},
                       {'audios': [f'a{i}' for i in range(4)]}]:
            with self.subTest(values=values), self.assertRaises(ValueError):
                normalize_references(values)

    def test_modes_require_their_own_roles_without_silently_dropping_material(self):
        valid = {'t2v': {}, 'i2v': {'first': 'a'}, 'fl2v': {'first': 'a', 'last': 'b'},
                 'r2v': {'images': ['c']}, 'ir2v': {'first': 'a', 'audios': ['c']},
                 'flr2v': {'first': 'a', 'last': 'b', 'videos': ['c']}}
        for mode, refs in valid.items():
            normalize_references(refs, mode)
        for mode, refs in [('t2v', {'first': 'a'}), ('i2v', {'first': 'a', 'images': ['b']}),
                           ('fl2v', {'first': 'a'}), ('r2v', {}), ('r2v', {'first': 'a', 'images': ['b']}),
                           ('ir2v', {'first': 'a'}), ('flr2v', {'first': 'a', 'images': ['b']})]:
            with self.subTest(mode=mode, refs=refs), self.assertRaises(ValueError):
                normalize_references(refs, mode)


class NativeH3GraphTests(unittest.TestCase):
    def test_all_material_is_connected_with_anchor_positions_and_exact_label_order(self):
        _, _, settings, graph = build(native_payload(), 'native-test')
        inputs = graph['native_reference']['inputs']
        self.assertEqual([inputs[f'ref_images.ref_image_{i}'] for i in range(3)],
                         [[f'ref_image_{i}', 0] for i in range(3)])
        self.assertEqual(inputs['ref_videos.ref_video_0'], ['ref_video_components_0', 0])
        self.assertEqual(inputs['ref_videos.ref_video_1'], ['ref_video_components_1', 0])
        self.assertNotIn('ref_video_audios.ref_video_audio_0', inputs)
        self.assertEqual(inputs['ref_video_audios.ref_video_audio_1'], ['ref_video_components_1', 1])
        self.assertEqual(inputs['ref_audios.ref_audio_0'], ['ref_audio_0', 0])
        self.assertEqual(graph['guide_first']['inputs']['frame_idx'], 0)
        self.assertEqual(graph['guide_last']['inputs']['frame_idx'], -1)
        self.assertEqual(graph['guide_last']['inputs']['positive'], ['guide_first', 0])
        self.assertEqual(graph['guide_last']['inputs']['latent'], ['native_reference', 1])
        self.assertEqual(graph['guider']['inputs']['conditioning'], ['guide_last', 0])
        self.assertEqual([row['labels'] for row in settings['reference_labels']],
                         [['<Picture 1>'], ['<Picture 2>'], ['<Picture 3>'],
                          ['<Video 1>'], ['<Audio 1>', '<Video 2>'], ['<Audio 2>']])
        self.assertEqual([row['id'] for row in settings['reference_labels']],
                         ['first', 'last', 'subject', 'silent', 'sound', 'voice'])
        self.assertEqual(settings['engine'], 'native')
        self.assertEqual(settings['reference_video_usage'][1]['duration'], 56/24)
        self.assertNotIn('imageB64', json.dumps(graph))
        self.assertFalse(any(row['class_type'] == 'MiniMaxH3Director' for row in graph.values()))
        self.assertEqual(graph['7']['inputs']['filename_prefix'], 'article_studio/native-test')

    def test_modes_use_the_same_native_reference_node_and_only_requested_guides(self):
        for mode, guides in [('r2v', []), ('ir2v', ['guide_first']), ('flr2v', ['guide_first', 'guide_last'])]:
            _, _, settings, graph = build(native_payload(mode), mode)
            self.assertEqual(settings['engine'], 'native')
            self.assertEqual([key for key in ('guide_first', 'guide_last') if key in graph], guides)
            expected = [guides[-1], 0] if guides else ['native_reference', 0]
            self.assertEqual(graph['guider']['inputs']['conditioning'], expected)

    def test_sampling_uses_shifted_lora_chain_and_decodes_both_av_streams(self):
        payload = native_payload('ir2v')
        payload['video_loras'] = [{'id': 'motion', 'strength': .6}]
        _, _, settings, graph = build(payload, 'sampling')
        self.assertEqual(graph['sigma_shift']['inputs']['model'], ['video_lora_0', 0])
        self.assertEqual(graph['video_lora_0']['inputs']['model'], ['10', 0])
        self.assertEqual(graph['guider']['inputs']['model'], ['sigma_shift', 0])
        self.assertEqual(graph['scheduler']['inputs']['model'], ['sigma_shift', 0])
        self.assertEqual(graph['scheduler']['inputs']['steps'], 8)
        self.assertEqual(graph['noise']['inputs']['noise_seed'], 42)
        self.assertEqual(graph['separate_av']['inputs']['av_latent'], ['native_sampler', 0])
        self.assertEqual(graph['video_decode']['inputs']['samples'], ['separate_av', 0])
        self.assertEqual(graph['audio_decode']['inputs']['samples'], ['separate_av', 1])
        self.assertEqual(graph['6']['inputs']['audio'], ['audio_decode', 0])
        self.assertEqual(graph['6']['inputs']['fps'], 24)
        self.assertEqual(graph['7']['inputs']['format.codec'], 'h264')
        payload['video_accelerator'] = 'none'
        payload['video_loras'] = []
        _, _, settings, graph = build(payload, 'base')
        self.assertNotIn('10', graph)
        self.assertEqual(graph['sigma_shift']['inputs']['model'], ['1', 0])
        self.assertEqual(graph['scheduler']['inputs']['steps'], 25)

    def test_single_audio_and_single_video_are_real_conditions(self):
        for kind, plural, port in [('audio', 'audios', 'ref_audios.ref_audio_0'),
                                   ('video', 'videos', 'ref_videos.ref_video_0')]:
            payload = native_payload('r2v')
            payload['references'] = {plural: ['only']}
            payload['_references'] = {plural: [descriptor('only', kind)]}
            _, _, settings, graph = build(payload, 'only')
            self.assertIn(port, graph['native_reference']['inputs'])
            self.assertEqual(len(settings['reference_labels']), 1)

    def test_paired_video_audio_advances_standalone_audio_tokens_without_extra_assets(self):
        payload = native_payload('r2v')
        payload['references'] = {'videos': ['v0', 'v1', 'v2'], 'audios': ['a0', 'a1', 'a2']}
        payload['_references'] = {
            'videos': [descriptor(f'v{i}', 'video', has_audio=True) for i in range(3)],
            'audios': [descriptor(f'a{i}', 'audio') for i in range(3)],
        }
        _, _, settings, graph = build(payload, 'paired')
        self.assertEqual(len(settings['reference_labels']), 6)
        self.assertEqual([row['labels'] for row in settings['reference_labels']],
                         [['<Audio 1>', '<Video 1>'], ['<Audio 2>', '<Video 2>'],
                          ['<Audio 3>', '<Video 3>'], ['<Audio 4>'], ['<Audio 5>'], ['<Audio 6>']])
        inputs = graph['native_reference']['inputs']
        for index in range(3):
            self.assertEqual(inputs[f'ref_video_audios.ref_video_audio_{index}'],
                             [f'ref_video_components_{index}', 1])

    def test_invalid_resolved_type_path_metadata_and_unused_material_are_rejected(self):
        changes = [{'kind': 'audio'}, {'id': 'other'}, {'remote_file': '../source.mp4'},
                   {'unknown': True}, {'width': 767}, {'height': True},
                   {'frames': 48, 'duration': 2}, {'frames': 141, 'duration': 141/24},
                   {'duration': 2}, {'duration': float('nan')}, {'duration': 10**1000}, {'has_audio': 1},
                   {'source_duration': 1}, {'source_duration': 16}]
        for change in changes:
            payload = native_payload()
            payload['_references']['videos'][0].update(change)
            with self.subTest(change=change), self.assertRaises(ValueError):
                build(payload, 'invalid')
        for mutate in [lambda p: p['_references']['images'].append(descriptor('extra')),
                       lambda p: p['_references'].update(unknown=[]),
                       lambda p: p['_references']['audios'][0].update(duration=1),
                       lambda p: p['_references']['audios'][0].update(duration=float('inf'))]:
            payload = native_payload()
            mutate(payload)
            with self.assertRaises(ValueError):
                build(payload, 'invalid')

    def test_per_type_duration_totals_and_source_to_effective_video_duration(self):
        payload = native_payload('r2v')
        for key in ('images', 'audios'):
            payload['references'][key] = []
            payload['_references'][key] = []
        payload['_references']['videos'][0].update(source_duration=8)
        payload['_references']['videos'][1].update(source_duration=8)
        _, _, settings, _ = build(payload, 'cropped')
        self.assertEqual(sum(row['duration'] for row in settings['reference_video_usage']), 112/24)
        payload['references']['videos'] = ['v0', 'v1', 'v2']
        payload['_references']['videos'] = [descriptor(f'v{i}', 'video', frames=124, duration=124/24, source_duration=6) for i in range(3)]
        with self.assertRaisesRegex(ValueError, '总时长'):
            build(payload, 'duration')
        payload['references'] = {'videos': ['short']}
        payload['_references'] = {'videos': [descriptor('short', 'video', frames=39, duration=39/24, source_duration=2)]}
        _, _, settings, _ = build(payload, 'short')
        self.assertEqual(settings['reference_video_usage'][0]['duration'], 1.625)
        payload['references'] = {'audios': ['a', 'b']}
        payload['_references'] = {'audios': [descriptor('a', 'audio', duration=8), descriptor('b', 'audio', duration=8)]}
        with self.assertRaisesRegex(ValueError, '总时长'):
            build(payload, 'duration')

    def test_reference_modes_reject_fl_weights_fl_accelerators_and_ignored_controls(self):
        for fields in [{'video_checkpoint': 'fl2va'}, {'video_accelerator': 'fl8'},
                       {'video_accelerator': 'fl4'}, {'cfg': 2}, {'steps': 9},
                       {'negative_prompt': 'blur'}, {'ref_image_size': 'max'},
                       {'video_loras': [{'id': 'motion', 'file': 'arbitrary'}]},
                       {'video_loras': [{'id': 'motion', 'strength': 10**1000}]}]:
            payload = native_payload()
            payload.update(fields)
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                build(payload, 'incompatible')

    def test_graph_input_names_and_link_types_match_the_deployed_schema(self):
        schema_path = os.environ.get('ARTICLE_STUDIO_H3_SCHEMA')
        if not schema_path or not Path(schema_path).is_file():
            self.skipTest('External contract requires ARTICLE_STUDIO_H3_SCHEMA; no deployed schema is bundled')
        schema = json.loads(Path(schema_path).read_text())
        schema = schema.get('nodes', schema)
        _, _, _, graph = build(native_payload(), 'schema')
        for key, row in graph.items():
            node_schema = schema[row['class_type']]
            allowed = {**node_schema['input'].get('required', {}), **node_schema['input'].get('optional', {})}
            for name, value in row['inputs'].items():
                if name in allowed:
                    expected = allowed[name][0]
                elif row['class_type'] == 'MiniMaxH3ReferenceToVideo':
                    group, suffix = name.split('.')
                    template = allowed[group][1]['template']
                    index = int(suffix.removeprefix(template['prefix']))
                    self.assertLess(index, template['max'])
                    expected = next(iter(template['input']['required'].values()))[0]
                elif row['class_type'] == 'SaveVideo' and name == 'format.codec':
                    continue
                else:
                    self.fail(f'{key}: undeclared input {name}')
                if isinstance(value, list):
                    source, output_index = value
                    self.assertIn(source, graph)
                    outputs = schema[graph[source]['class_type']]['output']
                    self.assertEqual(outputs[output_index], expected, f'{key}.{name}')


if __name__ == '__main__':
    unittest.main()
