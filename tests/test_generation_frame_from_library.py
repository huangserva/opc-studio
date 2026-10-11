"""A finished image result becomes a video reference without leaving the server.

Covers the contract points: the browser names only the generation record, the server
validates that record (kind, status, trash), the resolved file must stay inside that
record's own folder, and the registered reference behaves like an uploaded one.
"""
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from generation.service import Generation
from server import Handler, Store, ThreadingHTTPServer


class FrameFromLibraryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.generation = Generation(Store(self.temp.name))

    def job(self, model='qi21', prompt='一只猫在湖边走动', size=(640, 360), status='done', trash=False):
        with patch.object(Generation, '_spawn'):
            started = self.generation.start({'model': model, 'prompt': prompt})
        folder = self.generation.folder / started['id']
        if model == 'qi21':
            Image.new('RGB', size, (12, 34, 56)).save(folder / 'result.png')
            self.generation.update(started['id'], status=status, asset_file='result.png', sha256='source-'+started['id'])
        else:
            (folder / 'result.mp4').write_bytes(b'placeholder video bytes')
            self.generation.update(started['id'], status=status, asset_file='result.mp4')
        if trash:
            self.generation.manage([started['id']], 'delete')
        return self.generation.public(started['id'])

    def test_picking_the_same_record_twice_reuses_the_copy_already_on_disk(self):
        job = self.job()
        first = self.generation.reference_from_job(job['id'])
        second = self.generation.reference_from_job(job['id'])
        self.assertEqual(first['id'], second['id'])
        self.assertEqual(len(list((self.generation.folder / 'references').glob('*.png'))), 1)
        # A regenerated record carries new bytes, so it gets its own copy instead of
        # the stale one that was registered before.
        Image.new('RGB', (512, 512), (7, 7, 7)).save(self.generation.folder / job['id'] / 'result.png')
        self.generation.update(job['id'], sha256='regenerated')
        third = self.generation.reference_from_job(job['id'])
        self.assertNotEqual(third['id'], first['id'])
        self.assertEqual(third['source_sha256'], 'regenerated')

    def test_finished_image_result_registers_a_reference_without_the_browser(self):
        job = self.job(prompt='黄昏湖边的柴犬')
        reference = self.generation.reference_from_job(job['id'])
        self.assertEqual(reference['kind'], 'image')
        self.assertEqual(reference['width'], 640)
        self.assertEqual(reference['height'], 360)
        self.assertEqual(reference['url'], '/generation-references/' + reference['id'])
        self.assertEqual(reference['source_job'], job['id'])
        # The reference is a real local asset the video pipeline can prepare, and the
        # original result file is untouched.
        prepared = self.generation.references.prepare(reference['id'], 'image', inline=True)
        self.assertTrue(prepared['imageB64'])
        self.assertTrue((self.generation.folder / job['id'] / 'result.png').is_file())
        stored = self.generation.references.path(reference['id'])
        self.assertEqual(stored.parent, self.generation.folder / 'references')

    def test_reference_survives_restart_and_stays_readable(self):
        job = self.job()
        reference = self.generation.reference_from_job(job['id'])
        reloaded = Generation(Store(self.temp.name))
        self.assertEqual(reloaded.reference_info(reference['id'])['source_job'], job['id'])
        self.assertTrue(reloaded.reference_path(reference['id']).is_file())

    def test_trashed_running_missing_and_video_records_are_refused_with_readable_errors(self):
        deleted = self.job(trash=True)
        with self.assertRaises(ValueError) as caught:
            self.generation.reference_from_job(deleted['id'])
        self.assertIn('回收站', str(caught.exception))
        video = self.job(model='h3', prompt='湖边走动')
        with self.assertRaises(ValueError) as caught:
            self.generation.reference_from_job(video['id'])
        self.assertIn('图片', str(caught.exception))
        running = self.job(status='running')
        with self.assertRaises(ValueError) as caught:
            self.generation.reference_from_job(running['id'])
        self.assertIn('已完成', str(caught.exception))
        with self.assertRaises(ValueError):
            self.generation.reference_from_job('f' * 32)

    def test_forged_or_malformed_ids_never_escape_the_generation_folder(self):
        for candidate in ('../../etc/passwd', '', None, 5, 'x' * 32, '../' + 'a' * 31):
            with self.subTest(candidate=candidate), self.assertRaises(ValueError):
                self.generation.reference_from_job(candidate)

    def test_record_pointing_outside_its_own_folder_is_refused(self):
        job = self.job()
        Image.new('RGB', (64, 64), (1, 2, 3)).save(self.generation.folder / 'elsewhere.png')
        self.generation.update(job['id'], asset_file='../elsewhere.png')
        with self.assertRaises(ValueError):
            self.generation.reference_from_job(job['id'])
        self.assertEqual(list((self.generation.folder / 'references').glob('*.json')), [])
        # A record whose file was removed says so instead of registering nothing.
        self.generation.update(job['id'], asset_file='result.png')
        (self.generation.folder / job['id'] / 'result.png').unlink()
        with self.assertRaises(ValueError) as caught:
            self.generation.reference_from_job(job['id'])
        self.assertIn('目录内', str(caught.exception))

    def test_unreadable_result_is_refused_rather_than_half_registered(self):
        job = self.job()
        (self.generation.folder / job['id'] / 'result.png').write_bytes(b'not an image')
        with self.assertRaises(ValueError) as caught:
            self.generation.reference_from_job(job['id'])
        self.assertIn('无法读取', str(caught.exception))
        self.assertEqual(list((self.generation.folder / 'references').glob('*.json')), [])


class FrameFromLibraryHttpTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(self.temp.name)
        self.store.generation = Generation(self.store)
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.server.store = self.store
        self.server.generation = self.store.generation
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.shutdown)
        self.base = f'http://127.0.0.1:{self.server.server_port}'
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def shutdown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def post(self, path, body):
        request = urllib.request.Request(self.base + path, data=json.dumps(body).encode(),
            headers={'Origin': self.base, 'X-Article-Studio': '1', 'Content-Type': 'application/json'})
        with self.opener.open(request) as response:
            return response.status, json.load(response)

    def test_route_registers_the_reference_and_reports_a_deleted_picture_readably(self):
        with patch.object(Generation, '_spawn'):
            started = self.server.generation.start({'model': 'qi21', 'prompt': '湖边柴犬'})
        Image.new('RGB', (320, 240), (9, 9, 9)).save(self.server.generation.folder / started['id'] / 'result.png')
        self.server.generation.update(started['id'], status='done', asset_file='result.png')
        status, reference = self.post('/api/generation/reference-from-job', {'job_id': started['id']})
        self.assertEqual(status, 201)
        self.assertEqual(reference['kind'], 'image')
        self.assertEqual(reference['source_job'], started['id'])
        self.server.generation.manage([started['id']], 'delete')
        try:
            self.post('/api/generation/reference-from-job', {'job_id': started['id']})
            self.fail('A trashed picture must not register a reference')
        except urllib.error.HTTPError as error:
            body = json.load(error)
            error.close()
            self.assertEqual(error.code, 400)
            self.assertIn('回收站', body['error'])
        try:
            self.post('/api/generation/reference-from-job', {'job_id': '../../etc'})
            self.fail('A forged record id must be refused')
        except urllib.error.HTTPError as error:
            error.close()
            self.assertEqual(error.code, 400)


if __name__ == '__main__':
    unittest.main()
