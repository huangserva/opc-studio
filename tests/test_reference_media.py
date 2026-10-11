"""Real local media fixtures; no user media, SSH, or GPU work is performed."""
from isolation_patches import module_thread
import base64
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import wave

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image
from generation.references import MAX_UPLOAD, executable
from generation.service import Generation
from server import Store


class ReferenceMediaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            cls.ffmpeg = executable('ffmpeg')
            cls.ffprobe = executable('ffprobe')
        except ValueError as error:
            raise unittest.SkipTest(str(error))
        cls.fixtures = tempfile.TemporaryDirectory(prefix='studio-synthetic-reference-fixtures-')
        cls.fixture_folder = Path(cls.fixtures.name)
        cls.video_with_audio = cls.make_video('video-with-audio.mp4', 4.1, True)
        cls.video_without_audio = cls.make_video('video-without-audio.mp4', 4.1, False)
        cls.video_with_short_audio = cls.make_video('video-with-short-audio.mp4', 4, True, audio_duration=2.2)
        cls.video_eight_seconds = cls.make_video('video-eight-seconds.mp4', 8, True)
        cls.video_too_short = cls.make_video('video-too-short.mp4', 1.8, False)
        cls.video_too_long = cls.make_video('video-too-long.mp4', 15.2, False)
        cls.audio = cls.make_audio('audio.wav', 4.1)
        cls.audio_eight_seconds = cls.make_audio('audio-eight-seconds.wav', 8)
        cls.audio_boundary = cls.make_audio('audio-boundary.wav', 7.5)
        cls.audio_too_short = cls.make_audio('audio-too-short.wav', 1.9)
        cls.audio_too_long = cls.make_audio('audio-too-long.wav', 15.1)

    @classmethod
    def tearDownClass(cls):
        cls.fixtures.cleanup()

    @classmethod
    def make_video(cls, name, duration, with_audio, audio_duration=None):
        path = cls.fixture_folder / name
        command = [cls.ffmpeg, '-hide_banner', '-loglevel', 'error', '-nostdin',
                   '-f', 'lavfi', '-i', 'color=c=blue:s=98x66:r=15']
        if with_audio:
            audio_filter = 'sine=frequency=440:sample_rate=44100'
            if audio_duration is not None:
                audio_filter += ':duration=' + str(audio_duration)
            command += ['-f', 'lavfi', '-i', audio_filter]
        command += ['-t', str(duration), '-c:v', 'libx264', '-pix_fmt', 'yuv420p']
        if with_audio:
            command += ['-c:a', 'aac', '-ac', '1', '-ar', '44100']
        command.append(str(path))
        subprocess.run(command, check=True, stdout=subprocess.DEVNULL,
                       stderr=subprocess.PIPE, timeout=30)
        return path

    @classmethod
    def make_audio(cls, name, duration):
        path = cls.fixture_folder / name
        with wave.open(str(path), 'wb') as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(44100)
            output.writeframes(b'\x00\x00' * round(44100 * duration))
        return path

    def setUp(self):
        self.workspace = tempfile.TemporaryDirectory(prefix='studio-reference-media-test-')
        self.generation = Generation(Store(self.workspace.name))
        self.addCleanup(self.workspace.cleanup)
        self.addCleanup(self.generation.close)

    def inspect_media(self, path):
        result = subprocess.run([self.ffprobe, '-v', 'error', '-count_frames',
            '-show_entries', 'format=duration:stream=codec_type,width,height,r_frame_rate,sample_rate,channels,duration,nb_read_frames',
            '-of', 'json', str(path)], check=True, capture_output=True, timeout=30)
        return json.loads(result.stdout)

    def upload(self, kind, source):
        with source.open('rb') as stream:
            return self.generation.upload_media_reference(kind, source.name, stream, source.stat().st_size)

    def image(self, color='orange', size=(85, 49)):
        stream = io.BytesIO()
        Image.new('RGB', size, color).save(stream, format='PNG')
        raw = base64.b64encode(stream.getvalue()).decode()
        return self.generation.upload_reference({'name': 'synthetic.png', 'data': 'data:image/png;base64,' + raw})

    def assert_clean_rejection(self, call, message=None):
        before = set(self.generation.references.folder.iterdir())
        context = self.assertRaisesRegex(ValueError, message) if message else self.assertRaises(ValueError)
        with context:
            call()
        self.assertEqual(set(self.generation.references.folder.iterdir()), before)
        self.assertFalse(any(path.name.startswith('.upload-') for path in before))

    def test_video_normalization_uses_actual_24fps_padded_canvas_and_stereo_audio(self):
        original = self.inspect_media(self.video_with_audio)
        original_video = next(stream for stream in original['streams'] if stream['codec_type'] == 'video')
        self.assertEqual(original_video['r_frame_rate'], '15/1')
        self.assertNotEqual(original_video['width'] % 32, 0)
        reference = self.upload('video', self.video_with_audio)
        normalized = self.inspect_media(self.generation.reference_path(reference['id']))
        video = next(stream for stream in normalized['streams'] if stream['codec_type'] == 'video')
        audio = next(stream for stream in normalized['streams'] if stream['codec_type'] == 'audio')
        self.assertEqual(video['r_frame_rate'], '24/1')
        self.assertEqual(video['width'] % 32, 0)
        self.assertEqual(video['height'] % 32, 0)
        self.assertEqual((video['width'], video['height']), (reference['width'], reference['height']))
        self.assertEqual(int(video['nb_read_frames']), reference['frames'])
        self.assertEqual((int(audio['sample_rate']), audio['channels']), (32000, 2))
        self.assertTrue(reference['has_audio'])
        self.assertAlmostEqual(reference['source_duration'], float(original['format']['duration']), places=5)

    def test_silent_video_stays_silent_and_audio_upload_becomes_32khz_stereo_wav(self):
        video_reference = self.upload('video', self.video_without_audio)
        video = self.inspect_media(self.generation.reference_path(video_reference['id']))
        self.assertFalse(video_reference['has_audio'])
        self.assertFalse(any(stream['codec_type'] == 'audio' for stream in video['streams']))
        audio_reference = self.upload('audio', self.audio)
        path = self.generation.reference_path(audio_reference['id'])
        self.assertEqual(path.suffix, '.wav')
        audio = self.inspect_media(path)
        self.assertEqual(len(audio['streams']), 1)
        stream = audio['streams'][0]
        self.assertEqual((stream['codec_type'], int(stream['sample_rate']), stream['channels']), ('audio', 32000, 2))
        with wave.open(str(path), 'rb') as decoded:
            self.assertEqual((decoded.getframerate(), decoded.getnchannels()), (32000, 2))

    def test_normalization_keeps_video_tail_when_source_soundtrack_ends_early(self):
        original = self.inspect_media(self.video_with_short_audio)
        original_video = next(stream for stream in original['streams'] if stream['codec_type'] == 'video')
        original_audio = next(stream for stream in original['streams'] if stream['codec_type'] == 'audio')
        self.assertLess(float(original_audio['duration']), float(original_video['duration']))
        reference = self.upload('video', self.video_with_short_audio)
        normalized = self.inspect_media(self.generation.reference_path(reference['id']))
        video = next(stream for stream in normalized['streams'] if stream['codec_type'] == 'video')
        # Output-length cropping belongs to prepare(), not upload normalization.
        self.assertGreaterEqual(int(video['nb_read_frames']), round(float(original_video['duration']) * 24) - 1)

    def test_video_preparation_obeys_output_frame_grid_and_crops_audio_with_it(self):
        reference = self.upload('video', self.video_with_audio)
        for frames in (56, 124):
            with self.subTest(output_frames=frames):
                resolved = self.generation.resolve_references({'videos': [reference['id']]}, frames=frames)['videos'][0]
                expected = (min(reference['frames'], frames) - 5) // 17 * 17 + 5
                self.assertEqual(resolved['frames'], expected)
                self.assertEqual(resolved['frames'] % 17, 5)
                self.assertLessEqual(resolved['frames'], frames)
                self.assertAlmostEqual(resolved['duration'], expected / 24)
                media = self.inspect_media(resolved['_local_path'])
                video = next(stream for stream in media['streams'] if stream['codec_type'] == 'video')
                audio = next(stream for stream in media['streams'] if stream['codec_type'] == 'audio')
                self.assertEqual(int(video['nb_read_frames']), expected)
                self.assertEqual(video['r_frame_rate'], '24/1')
                # One AAC packet is 32ms; permit only that packet and one video frame.
                target_duration = expected / 24
                tolerance = 1 / 24 + 1024 / 32000
                self.assertLessEqual(abs(float(video['duration']) - target_duration), tolerance)
                self.assertLessEqual(abs(float(audio['duration']) - target_duration), tolerance)
                self.assertLessEqual(abs(float(audio['duration']) - float(video['duration'])), tolerance)
                self.assertEqual(resolved['source_duration'], reference['source_duration'])

    def test_source_duration_bounds_are_rejected_before_trimming_and_leave_no_assets(self):
        for kind, source in (('video', self.video_too_short), ('video', self.video_too_long),
                             ('audio', self.audio_too_short), ('audio', self.audio_too_long)):
            with self.subTest(kind=kind, source=source.name):
                self.assert_clean_rejection(lambda: self.upload(kind, source), '2–15')

    def test_each_reference_type_uses_source_duration_for_15_second_total_limit(self):
        for kind, source, role in (('video', self.video_eight_seconds, 'videos'),
                                   ('audio', self.audio_eight_seconds, 'audios')):
            with self.subTest(kind=kind):
                references = [self.upload(kind, source), self.upload(kind, source)]
                # The videos would each be cut to 56 frames, but original 16s must still fail.
                with self.assertRaisesRegex(ValueError, '总时长各不得超过 15'):
                    self.generation.resolve_references({role: [reference['id'] for reference in references]}, frames=56)
        self.assertFalse((self.generation.references.folder / 'prepared').exists())

    def test_exact_audio_15_second_total_is_allowed(self):
        references = [self.upload('audio', self.audio_boundary), self.upload('audio', self.audio_boundary)]
        resolved = self.generation.resolve_references({'audios': [reference['id'] for reference in references]}, frames=124)
        self.assertEqual(len(resolved['audios']), 2)
        self.assertAlmostEqual(sum(item['duration'] for item in resolved['audios']), 15, places=5)

    def test_oversized_or_invalid_declared_lengths_are_rejected_without_reading_stream(self):
        for length in (0, -1, MAX_UPLOAD + 1, '100'):
            with self.subTest(length=length):
                stream = io.BytesIO(b'unread')
                self.assert_clean_rejection(lambda: self.generation.upload_media_reference('video', 'oversize.mp4', stream, length), '100 MB')
                self.assertEqual(stream.tell(), 0)

    def test_incomplete_and_corrupt_streams_are_removed(self):
        raw = self.audio.read_bytes()
        truncated_video = self.video_with_audio.read_bytes()[:self.video_with_audio.stat().st_size // 2]
        for kind, data, length, message in (
            ('audio', raw[:-100], len(raw), '没有传输完整'),
            ('video', truncated_video, len(truncated_video), '无法读取素材'),
            ('video', b'not a media container', 21, '无法读取素材'),
            ('audio', b'bad audio', 9, '无法读取素材'),
            ('audio', raw, MAX_UPLOAD, '没有传输完整'),
        ):
            with self.subTest(kind=kind, message=message):
                self.assert_clean_rejection(lambda: self.generation.upload_media_reference(kind, 'synthetic', io.BytesIO(data), length), message)

    def test_audio_upload_without_audio_track_is_rejected_and_cleaned(self):
        self.assert_clean_rejection(lambda: self.upload('audio', self.video_without_audio), '没有音轨')

    def test_failed_transcode_cleans_partially_written_temporary_output(self):
        def fail_encoding(arguments):
            Path(arguments[-1]).write_bytes(b'incomplete transcode')
            raise ValueError('素材处理失败')
        with patch('generation.references.encode', side_effect=fail_encoding):
            self.assert_clean_rejection(lambda: self.upload('video', self.video_with_audio), '素材处理失败')

    def test_legacy_png_without_manifest_resolves_and_keeps_director_compatibility(self):
        identity = 'a' * 32
        old_image = self.generation.references.folder / (identity + '.png')
        Image.new('RGB', (85, 49), 'purple').save(old_image)
        self.assertFalse(old_image.with_suffix('.json').exists())
        info = self.generation.reference_info(identity)
        self.assertEqual((info['id'], info['kind'], info['width'], info['height']), (identity, 'image', 85, 49))
        legacy = self.generation.resolve_references({'reference': identity}, frames=56)
        self.assertEqual(legacy['reference']['id'], identity)
        self.assertEqual(legacy['images'][0]['id'], identity)
        with module_thread('generation.service') as thread:
            job = self.generation.start({'model': 'h3', 'workflow': 'i2v', 'size': '768x448',
                                         'frames': 56, 'prompt': 'Synthetic legacy lake', 'references': {'first': identity}})
            thread.return_value.start.assert_called_once()
        graph = json.loads((self.generation.folder / job['id'] / 'workflow.json').read_text())
        timeline = json.loads(graph['5']['inputs']['timeline_data'])
        self.assertTrue(timeline['global']['genImage']['imageB64'])
        self.assertEqual(job['settings']['engine'], 'director')
        self.assertNotIn('imageB64', json.dumps(job))
        self.assertNotIn('_local_path', json.dumps(job))

    def test_native_start_preserves_all_uploaded_descriptors_without_inline_or_local_data(self):
        first, last, image = self.image('orange'), self.image('blue'), self.image('green')
        silent_video = self.upload('video', self.video_without_audio)
        voiced_video = self.upload('video', self.video_with_audio)
        audio = self.upload('audio', self.audio)
        uploaded = [first, last, image, silent_video, voiced_video, audio]
        references = {'first': first['id'], 'last': last['id'], 'images': [image['id']],
                      'videos': [silent_video['id'], voiced_video['id']], 'audios': [audio['id']]}
        with patch.object(Generation, '_spawn') as spawn, \
             patch.object(Generation, 'media', side_effect=AssertionError('start 阶段不得访问媒体服务')) as remote:
            job = self.generation.start({'model': 'h3', 'workflow': 'flr2v', 'size': '768x448',
                                         'frames': 56, 'seed': 123, 'prompt': 'Synthetic combined reference audit', 'references': references})
            spawn.assert_called_once()
            remote.assert_not_called()
        folder = self.generation.folder / job['id']
        graph = json.loads((folder / 'workflow.json').read_text())
        persisted = json.loads((folder / 'job.json').read_text())
        settings = job['settings']
        self.assertEqual(settings['engine'], 'native')
        self.assertEqual(settings['references'], references)
        ids = {item['id'] for item in uploaded}
        self.assertEqual(set(settings['reference_media']), ids)
        self.assertEqual({item['id'] for item in settings['reference_labels']}, ids)
        expected_remote = {f"article_studio_refs/{item['id']}/reference" + {'image': '.png', 'video': '.mp4', 'audio': '.wav'}[item['kind']] for item in uploaded}
        loader_files = {item['inputs'][field] for item in graph.values()
                        for kind, field in (('LoadImage', 'image'), ('LoadVideo', 'file'), ('LoadAudio', 'audio'))
                        if item['class_type'] == kind}
        self.assertEqual(loader_files, expected_remote)
        native = graph['native_reference']['inputs']
        self.assertEqual(sum(key.startswith('ref_images.') for key in native), 3)
        self.assertEqual(sum(key.startswith('ref_videos.') for key in native), 2)
        self.assertNotIn('ref_video_audios.ref_video_audio_0', native)
        self.assertEqual(native['ref_video_audios.ref_video_audio_1'], ['ref_video_components_1', 1])
        self.assertEqual(native['ref_audios.ref_audio_0'], ['ref_audio_0', 0])
        self.assertEqual((graph['guide_first']['inputs']['frame_idx'], graph['guide_last']['inputs']['frame_idx']), (0, -1))
        self.assertEqual(graph['guide_last']['inputs']['positive'], ['guide_first', 0])
        for label, value in (('job', job), ('persisted_job', persisted), ('settings', settings), ('workflow', graph)):
            with self.subTest(public_artifact=label):
                encoded = json.dumps(value)
                self.assertNotIn('imageB64', encoded)
                self.assertNotIn('_local_path', encoded)
                self.assertNotIn('data:image/', encoded)
        pending_uploads = json.loads((folder / 'reference-uploads.json').read_text())
        self.assertEqual({item['id'] for item in pending_uploads}, ids)
        self.assertEqual({(item['id'], item['kind']) for item in pending_uploads},
                         {(item['id'], item['kind']) for item in uploaded})
        for item in pending_uploads:
            self.assertEqual(set(item), {'id', 'kind', 'name', '_local_path', 'meta'})
            self.assertTrue(Path(item['_local_path']).is_file())
        # the declared metadata is what the media API verifies against the uploaded bytes
        declared = {item['id']: item['meta'] for item in pending_uploads}
        for asset in uploaded:
            meta = declared[asset['id']]
            with self.subTest(asset=asset['kind']):
                self.assertLessEqual(set(meta), {'width', 'height', 'fps', 'frames', 'has_audio',
                                                 'source_duration', 'source_frames', 'duration'})
                if asset['kind'] == 'image':
                    # the declared size must match the normalised bytes that get uploaded,
                    # not the original upload the user handed us
                    plan = next(row for row in pending_uploads if row['id'] == asset['id'])
                    with Image.open(plan['_local_path']) as saved:
                        self.assertEqual((meta.get('width'), meta.get('height')), (saved.width, saved.height))
                if asset['kind'] == 'video':
                    # duration is measured by the server: container duration can drift
                    self.assertNotIn('duration', meta)
        # start only prepares the plan; nothing has been uploaded to the media API yet
        self.assertFalse((folder / 'reference-files.json').exists())


if __name__ == '__main__':
    unittest.main()
