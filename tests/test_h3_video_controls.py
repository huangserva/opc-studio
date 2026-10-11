"""H3 video parameter entry points: workbench catalog, validation and API parity.

The workbench and the 4090 media API are separate deployments; the user must see one
consistent set of resolutions, custom bounds and durations in both. This test pins the
workbench side and compares it against the API source that is deployed to 4090, so a
one-sided edit fails here instead of producing a UI option the server rejects.

Run: python3 -m unittest tests.test_h3_video_controls
The 4090 media API source is not part of this repository: export MEDIA_API_SOURCE to the
local media-api-source directory to enable the parity checks; without it they skip.
"""
import importlib.util
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from generation import models as wb_models          # noqa: E402
from generation import video_options as wb_video    # noqa: E402

API_SOURCE = os.environ.get('MEDIA_API_SOURCE', '')


def load_api_workflows():
    """Load the media API's workflows.py (the file deployed to 4090) without importing the app."""
    if not API_SOURCE:
        return None
    path = os.path.join(API_SOURCE, 'workflows.py')
    if not os.path.isfile(path):
        return None
    sys.path.insert(0, API_SOURCE)
    spec = importlib.util.spec_from_file_location('api_workflows', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class CatalogTest(unittest.TestCase):
    def setUp(self):
        self.h3 = wb_models.MODELS['h3']

    def test_sizes_are_grouped_by_aspect_ratio(self):
        groups = self.h3['size_groups']
        self.assertTrue(groups)
        self.assertEqual([g['name'] for g in groups], list(wb_video.H3_SIZE_GROUPS))
        flat = [size for group in groups for size in group['sizes']]
        self.assertEqual(flat, self.h3['sizes'])
        self.assertEqual(flat, sorted(flat, key=flat.index))

    def test_measured_options_are_marked_as_measured(self):
        self.assertEqual(self.h3['sizes_measured'], ['768x448', '448x768'])
        self.assertEqual(self.h3['frames_measured'], [56, 124])
        self.assertIn('已实测', self.h3['size_groups'][0]['name'])

    def test_custom_bounds_are_bounded_and_sourced(self):
        limits = self.h3['custom_size']
        self.assertEqual(limits, {'min': 256, 'max': 1024, 'step': 32, 'max_area': 1024 * 576})
        # the node schema allows 8192/16384 px and 100000 frames; that is not a usable option
        self.assertLessEqual(limits['max'], 1024)
        self.assertLessEqual(self.h3['frames_max'], 345)
        # the user-facing note keeps the real range and states the per-combination limit;
        # node-schema provenance (8191 / 8192 / 16384) lives in the evidence docs instead
        self.assertIn('256–1024', self.h3['constraint_note'])
        self.assertIn('未经逐项实测', self.h3['constraint_note'])
        self.assertIn('不代表任意搭配', self.h3['constraint_note'])

    def test_duration_ladder(self):
        self.assertEqual(self.h3['frames'], list(range(39, 346, 17)))
        self.assertEqual(self.h3['fps'], 24)
        self.assertIn(56, self.h3['frames'])
        self.assertIn(175, self.h3['frames'])

    def test_every_preset_is_inside_the_custom_bounds(self):
        for size in self.h3['sizes']:
            width, height = map(int, size.split('x'))
            self.assertTrue(all(256 <= n <= 1024 for n in (width, height)), size)

    def test_option_matrix_is_unchanged(self):
        self.assertEqual([c['id'] for c in self.h3['checkpoints']], ['singularity', 'ref2va', 'fl2va', 'hybrid'])
        self.assertEqual([a['id'] for a in self.h3['accelerators']], ['ref8', 'fl8', 'ref4', 'fl4', 'none'])
        self.assertEqual([w['id'] for w in self.h3['workflows']], ['t2v', 'i2v', 'fl2v', 'r2v', 'ir2v', 'flr2v'])
        self.assertEqual([lo['id'] for lo in self.h3['video_loras']], ['motion', 'realism', 'hm', 'naughty'])

    def test_labels_state_the_exact_reduced_ratio(self):
        self.assertEqual(self.h3['size_labels']['768x448'], '12:7 · 768x448')
        self.assertEqual(self.h3['size_labels']['1024x576'], '16:9 · 1024x576')
        for size, label in self.h3['size_labels'].items():
            self.assertEqual(label.split(' · ')[0], wb_video.exact_ratio(size), size)

    def test_capability_matrix_is_published(self):
        matrix = self.h3['capability_matrix']
        self.assertEqual([row['workflow'] for row in matrix],
                         ['t2v', 'i2v', 'fl2v', 'r2v', 'ir2v', 'flr2v'])
        self.assertEqual([row['engine'] for row in matrix],
                         ['director', 'director', 'director', 'native', 'native', 'native'])
        self.assertEqual([row['sizes_require_32_alignment'] for row in matrix],
                         [False, True, True, True, True, True])
        self.assertEqual(next(row for row in matrix if row['workflow'] == 'fl2v')['checkpoints'],
                         ['fl2va', 'hybrid'])
        for row in matrix:
            for combo in row['measured_combinations']:
                self.assertTrue(combo['evidence'], row['workflow'])


class SizeValidationTest(unittest.TestCase):
    def setUp(self):
        self.h3 = wb_models.MODELS['h3']

    def test_presets_accepted(self):
        for size in self.h3['sizes']:
            self.assertEqual(wb_models.validate_size(self.h3, size), size)

    def test_custom_sizes_accepted(self):
        for size in ('1024x576', '800x480', '256x256', '1024x544'):
            self.assertEqual(wb_models.validate_size(self.h3, size), size)

    def test_out_of_range_sizes_rejected_not_clamped(self):
        for size in ('767x448', '1056x576', '224x224', '1024x608', '1920x1080', '768x448x1', 'abc'):
            with self.assertRaises(ValueError) as caught:
                wb_models.validate_size(self.h3, size)
            self.assertIn('589824', str(caught.exception))

    def test_krea2_custom_bounds_keep_their_own_message(self):
        with self.assertRaises(ValueError) as caught:
            wb_models.validate_size(wb_models.MODELS['krea2'], '2064x2064')
        self.assertIn('512–2048', str(caught.exception))


class DurationValidationTest(unittest.TestCase):
    def test_ladder_accepted(self):
        for frames in (39, 56, 124, 175, 345):
            self.assertEqual(wb_video.validate_frames(wb_models.MODELS['h3'], frames), frames)

    def test_bad_durations_rejected(self):
        for frames in (38, 40, 346, 1000, '175', 175.0, True, None):
            with self.assertRaises(ValueError):
                wb_video.validate_frames(wb_models.MODELS['h3'], frames)

    def test_build_accepts_a_longer_duration_and_records_seconds(self):
        model, prompt, settings, graph = wb_models.build(
            {'model': 'h3', 'prompt': 'a neutral test scene', 'size': '1024x576', 'frames': 175,
             'seed': 1234}, 'c' * 32)
        self.assertEqual(settings['frames'], 175)
        self.assertEqual(settings['fps'], 24)
        self.assertEqual(settings['seconds'], 7.29)
        self.assertEqual(graph['5']['inputs']['total_frames'], 175)
        self.assertEqual((graph['5']['inputs']['width'], graph['5']['inputs']['height']), (1024, 576))

    def test_build_rejects_an_off_ladder_duration(self):
        with self.assertRaises(ValueError):
            wb_models.build({'model': 'h3', 'prompt': 'a neutral test scene', 'size': '768x448',
                             'frames': 40, 'seed': 1}, 'd' * 32)


class ApiParityTest(unittest.TestCase):
    def setUp(self):
        if not API_SOURCE:
            self.skipTest('需设置 MEDIA_API_SOURCE 指向本机媒体 API 源码')
        self.api = load_api_workflows()
        if self.api is None:
            self.skipTest(f'media API source not present at {API_SOURCE}')

    def test_catalogs_agree(self):
        h3 = wb_models.MODELS['h3']
        api_h3 = self.api.MODELS['h3']
        for field in ('sizes', 'size_labels', 'size_groups', 'custom_size', 'sizes_measured',
                      'frames', 'frames_min', 'frames_max', 'frames_step', 'frames_measured',
                      'fps', 'constraint_note', 'size_hint', 'capability_matrix'):
            self.assertEqual(h3[field], api_h3[field], f'{field} differs between workbench and API')

    def test_measured_evidence_lists_agree(self):
        self.assertEqual(wb_video.H3_MEASURED_COMBOS, self.api.H3_MEASURED_COMBOS)
        self.assertEqual(wb_video.H3_ENGINES, self.api.H3_ENGINES)

    def test_validation_rules_agree(self):
        cases = ['768x448', '448x768', '1024x576', '800x480', '1024x608', '767x448', '1056x576',
                 '224x224', '1920x1080', '512x512', '768x768', '576x1024']
        for size in cases:
            def outcome(validate):
                try:
                    return validate(wb_models.MODELS['h3'] if validate is wb_models.validate_size
                                    else self.api.MODELS['h3'], size)
                except ValueError as exc:
                    return 'rejected: ' + str(exc)
            workbench, api = outcome(wb_models.validate_size), outcome(self.api.validate_size)
            self.assertEqual(workbench.startswith('rejected'), api.startswith('rejected'), size)
            self.assertEqual(workbench, api, size)

    def test_duration_rules_agree(self):
        for frames in (39, 56, 124, 175, 345, 38, 40, 346, '175', True, 175.0):
            def outcome(validate, model):
                try:
                    return validate(model, frames)
                except ValueError as exc:
                    return 'rejected: ' + str(exc)
            self.assertEqual(outcome(wb_video.validate_frames, wb_models.MODELS['h3']),
                             outcome(self.api.validate_frames, self.api.MODELS['h3']), str(frames))


if __name__ == '__main__':
    unittest.main()
