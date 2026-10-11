import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from generation.models import build
from generation.progress import event_progress
from generation.service import Generation
from server import Store

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / 'coordination/evidence/video-dual-sampling/single-baseline.json'
# hygiene-4：这份快照在 coordination/evidence/ 下，是私有协调证据，不进公开仓库。
# 原来拼路径无害（真正读在用例里），这里显式标注可用性并只跳过依赖它的用例。
DUAL_SAMPLING_EVIDENCE = BASE.is_file() and BASE.with_name('single-matrix-baseline.json').is_file()

class DualSamplingTests(unittest.TestCase):
    @unittest.skipUnless(DUAL_SAMPLING_EVIDENCE,
                         '需要本机证据快照 coordination/evidence/video-dual-sampling/*.json；'
                         'coordination/ 是私有协调证据，不进公开仓库（load-manifest-v2 §3.2）。')
    def test_single_graphs_match_prechange_snapshot_all_six_modes(self):
        rows = json.loads(BASE.read_text()) + json.loads(BASE.with_name('single-matrix-baseline.json').read_text())
        for row in rows:
            for extra in ({}, {'video_sampling': 'single'}):
                with self.subTest(mode=row['payload']['workflow'], extra=extra):
                    self.assertEqual(build({**row['payload'], **extra}, 'regression')[3], row['graph'])

    @unittest.skipUnless(DUAL_SAMPLING_EVIDENCE,
                         '需要本机证据快照 coordination/evidence/video-dual-sampling/*.json；'
                         'coordination/ 是私有协调证据，不进公开仓库（load-manifest-v2 §3.2）。')
    def test_dual_graph_all_six_modes_and_reference_conditioning(self):
        for row in json.loads(BASE.read_text()):
            _, _, s, graph = build({**row['payload'], 'video_sampling': 'dual'}, 'dual')
            self.assertEqual(s['size'], '768x448')
            self.assertEqual(s['video_first_size'], '384x224')
            self.assertEqual(s['engine'], 'native')
            self.assertEqual(sum(n['class_type'] == 'SamplerCustomAdvanced' for n in graph.values()), 2)
            self.assertEqual(graph['refine_av']['inputs']['audio_latent'], ['first_separate_av', 1])
            self.assertEqual(graph['separate_av']['inputs']['av_latent'], ['refine_sampler', 0])
            self.assertEqual(graph['video_upscale']['inputs']['mode.width'], 768)
            self.assertEqual(graph['refine_reference']['inputs']['height'], 448)
            if row['payload']['workflow'] in ('ir2v', 'flr2v'):
                self.assertEqual(graph['refine_guide_first']['inputs']['latent'], ['refine_av', 0])

    def test_validation_and_parameter_boundaries(self):
        payload = {'model': 'h3', 'prompt': 'lake', 'size': '256x256', 'frames': 39, 'seed': 42, 'video_sampling': 'dual'}
        for extra in ({'video_sampling': 'bad'}, {'video_refine_steps': True}, {'video_refine_steps': 1.5},
                      {'video_refine_steps': 0}, {'video_refine_steps': 26}, {'video_refine_denoise': float('nan')},
                      {'video_refine_denoise': 0}, {'video_refine_denoise': .76}, {'video_upscale': float('inf')},
                      {'video_upscale': False}, {'video_upscale': 2.1}, {'video_upscale': '2'}, {'size': '768x432'}):
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                build({**payload, **extra}, 'invalid')
        for steps, denoise, scale in ((1, .05, 1), (25, .75, 2)):
            _, _, _, graph = build({**payload, 'video_refine_steps': steps, 'video_refine_denoise': denoise, 'video_upscale': scale}, 'edge')
            self.assertEqual(graph['refine_scheduler']['inputs']['denoise'], denoise)

    def test_progress_is_continuous_through_both_samplers_and_bridge(self):
        graph = build({'model': 'h3', 'prompt': 'lake', 'video_sampling': 'dual'}, 'progress')[3]
        previous = None
        percents = []
        for node, value, total in [('native_sampler', 0, 8), ('native_sampler', 4, 8), ('native_sampler', 8, 8),
                                   ('video_upscale', None, None), ('refine_reference', None, None),
                                   ('refine_sampler', None, None), ('refine_sampler', 0, 4),
                                   ('refine_sampler', 1, 4), ('refine_sampler', 4, 4)]:
            data = {'node': node, 'prompt_id': 'p'}
            if value is not None:
                data.update(value=value, max=total)
            previous = event_progress({'type': 'progress' if value is not None else 'executing', 'data': data}, 'p', graph, 'video', previous)
            percents.append(previous['percent'])
        self.assertEqual(percents, sorted(percents))
        self.assertEqual(percents, [0, 33, 67, 67, 67, 67, 67, 75, 100])
        same = event_progress({'type': 'executing', 'data': {'node': 'refine_sampler'}}, 'p', graph, 'video', previous)
        self.assertEqual(same['percent'], 100)
        snapshot = event_progress({'type': 'progress_state', 'data': {'nodes': {'refine_sampler': {'state': 'running', 'value': 1, 'max': 1}}}}, 'p', graph, 'video', previous)
        self.assertEqual(snapshot['percent'], 100)

    def test_real_service_semantic_params_and_request_identity(self):
        for mode in ('single', 'dual'):
            with tempfile.TemporaryDirectory() as directory:
                generation = Generation(Store(directory))
                payload = {'model': 'h3', 'prompt': 'lake', 'size': '256x256', 'frames': 39, 'seed': 42, 'request_id': 'a' * 32}
                with patch.object(Generation, '_spawn'):
                    result = generation.start({**payload, 'video_sampling': mode})
                params = generation.jobs[result['id']]['api_params']
                if mode == 'single':
                    self.assertEqual(params, {'model': 'minimax-h3', 'prompt': 'lake', 'size': '256x256', 'seed': 42,
                                            'workflow': 't2v', 'frames': 39, 'video_checkpoint': 'singularity',
                                            'video_accelerator': 'ref8', 'video_loras': [], 'references': {}})
                else:
                    self.assertEqual(params['video_sampling'], 'dual')
                    self.assertEqual(params['video_refine_steps'], 4)
                    self.assertEqual(params['video_refine_denoise'], .35)
                    with self.assertRaisesRegex(ValueError, '不同'):
                        generation.start({**payload, 'video_sampling': 'dual', 'video_refine_steps': 5})

if __name__ == '__main__':
    unittest.main()
