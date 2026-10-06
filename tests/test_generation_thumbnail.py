import copy
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PIL import Image
from generation.service import Generation
from server import Store


class GenerationThumbnailTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ffmpeg = shutil.which('ffmpeg')
        if not ffmpeg and Path('/opt/homebrew/bin/ffmpeg').is_file():
            ffmpeg = '/opt/homebrew/bin/ffmpeg'
        if not ffmpeg:
            raise unittest.SkipTest('Local FFmpeg is needed for real video thumbnail fixtures')
        cls.fixtures = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.fixtures.cleanup)
        cls.clips = {}
        for name, size in [('landscape', '960x540'), ('portrait', '540x960')]:
            destination = Path(cls.fixtures.name) / (name + '.mp4')
            subprocess.run(
                [ffmpeg, '-hide_banner', '-loglevel', 'error', '-nostdin',
                 '-f', 'lavfi', '-i', f'color=c=0xE87220:s={size}:r=4',
                 '-t', '0.5', '-c:v', 'mpeg4', '-q:v', '5', '-pix_fmt', 'yuv420p',
                 '-threads', '1', '-y', str(destination)],
                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, check=True, timeout=20,
            )
            cls.clips[name] = destination

    def setUp(self):
        self.workspace = tempfile.TemporaryDirectory()
        self.addCleanup(self.workspace.cleanup)
        self.generation = Generation(Store(self.workspace.name))
        self.addCleanup(self.generation.close)

    def add_job(self, *, clip=None, kind='video', status='done', raw=None, asset=True):
        identity = uuid.uuid4().hex
        folder = self.generation.folder / identity
        folder.mkdir()
        record = {
            'id': identity, 'kind': kind, 'status': status,
            'model': 'h3' if kind == 'video' else 'qi21', 'model_name': 'Fixture model',
            'prompt': 'A small local fixture', 'origin': 'lab',
            'created_at': '2026-10-04T01:00:00+00:00',
            'updated_at': '2026-10-04T01:01:00+00:00',
            'finished_at': '2026-10-04T01:01:00+00:00' if status == 'done' else None,
            'settings': {'size': '960x540', 'seed': 42}, 'error': None,
        }
        if asset:
            source = folder / ('result.mp4' if kind == 'video' else 'result.png')
            if kind == 'image':
                Image.new('RGB', (40, 30), 'orange').save(source)
            elif raw is not None:
                source.write_bytes(raw)
            else:
                shutil.copyfile(clip or self.clips['landscape'], source)
            record.update(asset_file=source.name, bytes=source.stat().st_size,
                          sha256=hashlib.sha256(source.read_bytes()).hexdigest())
        self.generation.jobs[identity] = record
        (folder / 'job.json').write_text(json.dumps(record), encoding='utf-8')
        return identity

    def snapshot(self, identity):
        folder = self.generation.folder / identity
        return (copy.deepcopy(self.generation.jobs[identity]),
                (folder / 'job.json').read_bytes(),
                (folder / 'result.mp4').read_bytes())

    def test_ready_video_exposes_thumbnail_but_public_and_listing_never_extract(self):
        ready = self.add_job()
        image = self.add_job(kind='image')
        pending = self.add_job(status='running')
        missing_asset = self.add_job(asset=False)
        with patch('generation.service.subprocess.run') as extraction:
            public = self.generation.public(ready)
            self.assertEqual(public['thumbnail_url'], '/generation-thumbnails/' + ready)
            self.assertEqual(public['asset_url'], '/generation-assets/' + ready)
            for identity in [image, pending, missing_asset]:
                self.assertNotIn('thumbnail_url', self.generation.public(identity))
            by_id = {value['id']: value for value in self.generation.listing()['jobs']}
            self.assertEqual(by_id[ready]['thumbnail_url'], public['thumbnail_url'])
            extraction.assert_not_called()
        self.assertFalse((self.generation.folder / ready / 'thumbnail.jpg').exists())

    def test_real_ffmpeg_frame_is_valid_bounded_jpeg_with_source_aspect_and_color(self):
        for name, expected in [('landscape', (640, 360)), ('portrait', (360, 640))]:
            with self.subTest(orientation=name):
                identity = self.add_job(clip=self.clips[name])
                before = self.snapshot(identity)
                poster = self.generation.thumbnail(identity)
                with Image.open(poster) as thumbnail:
                    self.assertEqual(thumbnail.format, 'JPEG')
                    self.assertEqual(thumbnail.size, expected)
                    self.assertLessEqual(max(thumbnail.size), 640)
                    thumbnail.load()
                    pixel = thumbnail.convert('RGB').getpixel((thumbnail.width // 2, thumbnail.height // 2))
                    # The thumbnail contains the actual orange video frame.
                    for actual, wanted in zip(pixel, (232, 114, 32)):
                        self.assertLessEqual(abs(actual - wanted), 12)
                with Image.open(poster) as thumbnail:
                    thumbnail.verify()
                self.assertEqual(self.snapshot(identity), before)
                self.assertEqual(list(poster.parent.glob('.thumbnail-*')), [])

    def test_cached_frame_is_reused_without_extraction_including_after_restart(self):
        identity = self.add_job()
        real_run = subprocess.run
        with patch('generation.service.subprocess.run', wraps=real_run) as extraction:
            poster = self.generation.thumbnail(identity)
            contents, modified = poster.read_bytes(), poster.stat().st_mtime_ns
            self.assertEqual(self.generation.thumbnail(identity), poster)
            reopened = Generation(Store(self.workspace.name))
            self.addCleanup(reopened.close)
            self.assertEqual(reopened.thumbnail(identity), poster)
            extraction.assert_called_once()
        self.assertEqual(poster.read_bytes(), contents)
        self.assertEqual(poster.stat().st_mtime_ns, modified)

    def test_simultaneous_requests_for_same_video_extract_only_once(self):
        identity = self.add_job()
        before = self.snapshot(identity)
        barrier = threading.Barrier(4)

        def request():
            barrier.wait(timeout=5)
            return self.generation.thumbnail(identity)

        real_run = subprocess.run
        with patch('generation.service.subprocess.run', wraps=real_run) as extraction:
            with ThreadPoolExecutor(max_workers=4) as executor:
                futures = [executor.submit(request) for _ in range(4)]
                posters = [future.result(timeout=30) for future in futures]
            self.assertEqual(len(set(posters)), 1)
            extraction.assert_called_once()
        with Image.open(posters[0]) as thumbnail:
            thumbnail.verify()
        self.assertEqual(self.snapshot(identity), before)
        self.assertEqual(list(posters[0].parent.glob('.thumbnail-*')), [])

    def test_invalid_video_failure_cleans_files_and_preserves_completed_original(self):
        # Recognizable MP4 header with corrupt contents: FFmpeg must actually fail.
        identity = self.add_job(raw=b'\x00\x00\x00\x18ftypmp42broken-video-fixture')
        before = self.snapshot(identity)
        folder = self.generation.folder / identity
        original_names = {path.name for path in folder.iterdir()}
        real_run = subprocess.run
        with patch('generation.service.subprocess.run', wraps=real_run) as extraction:
            with self.assertRaisesRegex(ValueError, '缩略图.*仍可播放原视频'):
                self.generation.thumbnail(identity)
            extraction.assert_called_once()
        self.assertEqual({path.name for path in folder.iterdir()}, original_names)
        self.assertEqual(self.snapshot(identity), before)
        self.assertEqual(self.generation.public(identity)['status'], 'done')
        self.assertEqual(self.generation.asset(identity).read_bytes(), before[2])

    def test_interrupted_extraction_removes_partial_output_and_keeps_job_done(self):
        identity = self.add_job()
        before = self.snapshot(identity)

        def interrupted(command, **_kwargs):
            # An external encoder can leave a partially written image on timeout.
            Path(command[-1]).write_bytes(b'partial-jpeg')
            raise subprocess.TimeoutExpired(command, 20)

        with patch('generation.service.subprocess.run', side_effect=interrupted):
            with self.assertRaisesRegex(ValueError, '缩略图.*仍可播放原视频'):
                self.generation.thumbnail(identity)
        folder = self.generation.folder / identity
        self.assertEqual(list(folder.glob('.thumbnail-*')), [])
        self.assertFalse((folder / 'thumbnail.jpg').exists())
        self.assertEqual(self.snapshot(identity), before)
        self.assertEqual(self.generation.public(identity)['status'], 'done')


if __name__ == '__main__':
    unittest.main()
