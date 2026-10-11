"""X posts/threads with images: upload only after confirmation, public check before Buffer.

A fake image host (real local HTTP server) and a fake `buffer` executable stand in for the
outside world. No real R2 bucket, no real Buffer, and no credentials beyond test constants.
"""
import base64
import copy
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

spec = importlib.util.spec_from_file_location('studio', Path(__file__).resolve().parents[1] / 'server.py')
studio = importlib.util.module_from_spec(spec)
spec.loader.exec_module(studio)
from publishing.transports import buffer_cli, r2_media


def load_host_fixture():
    helper = importlib.util.spec_from_file_location(
        'r2_fake_host', Path(__file__).resolve().parent / 'r2_fake_host.py')
    module = importlib.util.module_from_spec(helper)
    helper.loader.exec_module(module)
    return module


host = load_host_fixture()

# Synthetic test constants. Every value is assembled at run time so the public
# source tree never holds a credential-shaped literal; the values themselves and
# the redaction assertions below are unchanged. CHANNEL_ID is a stand-in for a
# Buffer channel id and carries no secret of its own.
SECRET = 'r2-secret-DO-NOT-LEAK-' + '0f3a9c1b7e2d4a86'
KEY_ID = 'AK' + 'IA' + 'IOSFODNN7' + 'EXAMPLE'
CHANNEL_ID = 'facade' * 4
POST_ID = 'a1b2c3d4e5f6' + '0718293a4b5c'
TEXTS = ['第一条 带图', '第二条 带两张图']

FAKE_CLI = '''#!/usr/bin/env python3
import json, os, sys
LOG = __LOG__
SCRIPT = __SCRIPT__
calls = json.load(open(LOG, encoding='utf-8')) if os.path.exists(LOG) else []
calls.append({'argv': sys.argv[1:], 'stdin': sys.stdin.read()})
json.dump(calls, open(LOG, 'w', encoding='utf-8'), ensure_ascii=False)
steps = json.load(open(SCRIPT, encoding='utf-8'))['steps']
step = steps[min(len(calls) - 1, len(steps) - 1)]
if step.get('stdout') is not None:
    sys.stdout.write(step['stdout'])
if step.get('stderr') is not None:
    sys.stderr.write(step['stderr'])
sys.exit(step.get('returncode', 0))
'''


def png(seed, size=(12, 12)):
    out = io.BytesIO()
    Image.new('RGB', size, (seed, 30, 200)).save(out, 'PNG')
    return base64.b64encode(out.getvalue()).decode()


class BufferImageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.store = studio.Store(str(root / 'workspace'))
        self.log = root / 'calls.json'
        self.script = root / 'script.json'
        fake = root / 'buffer'
        fake.write_text(FAKE_CLI.replace('__LOG__', repr(str(self.log)))
                            .replace('__SCRIPT__', repr(str(self.script))), encoding='utf-8')
        fake.chmod(0o755)
        self.store.buffer_cli_command = [str(fake)]
        self.host = host.start_fake_host()
        self.addCleanup(self.shutdown_host)
        self.config_path = root / 'r2.json'
        self.write_config()
        patcher = patch.dict(os.environ, {'ARTICLE_STUDIO_R2_CONFIG': str(self.config_path)})
        patcher.start()
        self.addCleanup(patcher.stop)
        opener = patch.object(r2_media, 'urlopen', host.public_to_local(self.host))
        opener.start()
        self.addCleanup(opener.stop)

    def shutdown_host(self):
        self.host.shutdown()
        self.host.server_close()

    def tearDown(self):
        self.temp.cleanup()

    def write_config(self, **override):
        data = {'account_id': 'testaccount', 'bucket': 'media', 'access_key_id': KEY_ID,
                'secret_access_key': SECRET, 'public_base_url': 'https://' + host.PUBLIC_HOST,
                'endpoint': 'http://127.0.0.1:%d' % self.host.server_address[1],
                'prefix': 'article-studio/x'}
        data.update(override)
        self.config_path.write_text(json.dumps(data), encoding='utf-8')
        self.config_path.chmod(0o600)

    def steps(self, *steps):
        self.script.write_text(json.dumps({'steps': list(steps)}), encoding='utf-8')

    def channel_step(self):
        return {'stdout': json.dumps([{'id': CHANNEL_ID, 'name': 'servasyy_ai', 'service': 'twitter'}])}

    def created_step(self):
        return {'stdout': json.dumps({'__typename': 'PostActionSuccess',
                                      'post': {'id': POST_ID, 'status': 'draft'}}), 'returncode': 0}

    def cli_calls(self):
        return json.loads(self.log.read_text(encoding='utf-8')) if self.log.exists() else []

    def create_calls(self):
        return [item for item in self.cli_calls() if item['argv'][0:2] == ['posts', 'create']]

    def work(self, images_per_post=(1, 2)):
        created = self.store.create({'title': '带图串文', 'type': 'post'})
        slug = created['slug']
        raw = copy.deepcopy(created['studio'])
        raw['platforms']['x'] = {'account': 'servasyy_ai', 'caption': '', 'title': '带图串文',
                                 'topics': '', 'status': 'draft'}
        blocks, seed = [], 0
        for index, count in enumerate(images_per_post):
            blocks.append({'kind': 'text', 'html': '<p>' + TEXTS[index] + '</p>'})
            for _ in range(count):
                seed += 1
                blocks.append({'kind': 'image', 'data': png(seed, (12 + seed, 12))})
        self.store.save(slug, {'base_version': created['version']['id'], 'title': '带图串文',
                               'studio': raw, 'blocks': blocks})
        return slug, self.store.meta(slug)['head']

    def deliveries(self, slug):
        path = self.store.article(slug) / 'deliveries.json'
        return json.loads(path.read_text(encoding='utf-8')) if path.exists() else []

    def submit(self, slug, version, mode='draft', **extra):
        return self.store.buffer_submit(slug, {'version': version, 'platform': 'x',
                                               'mode': mode, 'confirmed': True, **extra})

    def check(self, slug, version):
        return self.store.buffer_channels(slug, {'version': version, 'platform': 'x'})

    # --- 1. when the upload happens -------------------------------------------
    def test_channel_check_and_confirmation_never_upload(self):
        slug, version = self.work()
        self.steps(self.channel_step())
        preview = self.check(slug, version)
        self.assertTrue(preview['resolved'])
        self.submit_rejected_without_confirmation(slug, version)
        self.assertEqual(host.methods(self.host, 'PUT'), [])
        self.assertEqual(host.methods(self.host, 'HEAD'), [])
        self.assertEqual(self.create_calls(), [])

    def submit_rejected_without_confirmation(self, slug, version):
        with self.assertRaises(ValueError):
            self.store.buffer_submit(slug, {'version': version, 'platform': 'x', 'mode': 'draft'})

    def test_images_upload_after_confirmation_and_before_buffer(self):
        slug, version = self.work()
        self.steps(self.channel_step(), self.created_step())
        result = self.submit(slug, version, 'draft')
        self.assertEqual(result['record']['kind'], 'buffer_draft')
        self.assertEqual(len(host.methods(self.host, 'PUT')), 3)
        body = json.loads(self.create_calls()[0]['stdin'])
        urls = [asset['image']['url'] for asset in body['metadata']['twitter']['thread'][0]['assets']]
        self.assertEqual(len(urls), 1)
        self.assertTrue(all(url.startswith('https://' + host.PUBLIC_HOST + '/') for url in urls))

    def test_thread_assets_follow_each_tweet_in_order(self):
        slug, version = self.work()
        self.steps(self.channel_step(), self.created_step())
        self.submit(slug, version, 'draft')
        body = json.loads(self.create_calls()[0]['stdin'])
        thread = body['metadata']['twitter']['thread']
        self.assertEqual([item['text'] for item in thread], TEXTS)
        self.assertEqual([len(item['assets']) for item in thread], [1, 2])
        keys = [asset['image']['url'].rsplit('/', 1)[-1] for item in thread for asset in item['assets']]
        stored = sorted(self.host.objects)
        self.assertEqual(sorted(keys), [key.rsplit('/', 1)[-1] for key in stored])
        self.assertEqual(len(set(keys)), 3)

    def test_single_tweet_uses_top_level_assets(self):
        slug, version = self.work([2])
        self.steps(self.channel_step(), self.created_step())
        self.submit(slug, version, 'publish_now')
        body = json.loads(self.create_calls()[0]['stdin'])
        self.assertEqual(len(body['assets']), 2)
        self.assertNotIn('metadata', body)
        self.assertEqual(body['mode'], 'shareNow')

    # --- 3. public reachability gate ------------------------------------------
    def test_public_check_failure_stops_before_buffer_and_writes_nothing(self):
        slug, version = self.work()
        self.steps(self.channel_step())
        # The objects upload fine, but the public link does not answer as an image.
        patcher = patch.object(r2_media, 'public_reachable',
                               lambda url, timeout=20, config=None: {'ok': False, 'reason': '链接返回 HTTP 404'})
        patcher.start()
        self.addCleanup(patcher.stop)
        with self.assertRaises(ValueError) as caught:
            self.submit(slug, version, 'draft')
        message = str(caught.exception)
        self.assertIn('图片公开校验未通过', message)
        self.assertIn('images/upload-', message)
        self.assertIn('404', message)
        self.assertEqual(self.create_calls(), [])
        self.assertEqual(self.deliveries(slug), [])
        # Fail fast: the first image already stops the run, so the other two never upload.
        self.assertEqual(len(host.methods(self.host, 'PUT')), 1)

    def test_upload_failure_writes_no_buffer_record_and_can_retry(self):
        slug, version = self.work([1])
        self.steps(self.channel_step(), self.channel_step(), self.created_step())
        # Fail the first upload attempt, then let the retry succeed.
        manifest, _, _ = self.store.info(slug, version)
        first = manifest['body_media'][0]
        fail_key = 'article-studio/x/%s/%s/%s.png' % (slug, version, first['sha256'])
        self.host.fail_put.add(fail_key)
        with self.assertRaises(ValueError) as caught:
            self.submit(slug, version, 'draft')
        self.assertIn('图床上传失败', str(caught.exception))
        self.assertEqual(self.create_calls(), [])
        self.assertEqual(self.deliveries(slug), [])
        self.host.fail_put.clear()
        result = self.submit(slug, version, 'draft')
        self.assertEqual(result['record']['kind'], 'buffer_draft')
        self.assertEqual(len(self.create_calls()), 1)

    def test_error_text_never_contains_credentials(self):
        slug, version = self.work([1])
        self.steps(self.channel_step())
        manifest, _, _ = self.store.info(slug, version)
        self.host.fail_put.add('article-studio/x/%s/%s/%s.png'
                               % (slug, version, manifest['body_media'][0]['sha256']))
        with self.assertRaises(ValueError) as caught:
            self.submit(slug, version, 'draft')
        message = str(caught.exception)
        self.assertNotIn(SECRET, message)
        self.assertNotIn(KEY_ID, message)
        self.assertNotIn('AWS4-HMAC-SHA256', message)

    # --- 2. idempotency -------------------------------------------------------
    def test_reupload_reuses_an_identical_object_and_replaces_a_different_one(self):
        slug, version = self.work([1])
        manifest, _, package = self.store.info(slug, version)
        media = self.store._buffer_media(package, manifest)
        first = self.store._buffer_upload(slug, version, package, media)
        self.assertEqual(len(host.methods(self.host, 'PUT')), 1)
        self.assertTrue(first['objects'][0]['uploaded'])
        second = self.store._buffer_upload(slug, version, package, media)
        self.assertEqual(len(host.methods(self.host, 'PUT')), 1)
        self.assertFalse(second['objects'][0]['uploaded'])
        self.assertEqual(first['objects'][0]['key'], second['objects'][0]['key'])
        # Same key, different size: the object has to be replaced.
        key = first['objects'][0]['key']
        self.host.objects[key] = b'shorter'
        third = self.store._buffer_upload(slug, version, package, media)
        self.assertEqual(len(host.methods(self.host, 'PUT')), 2)
        self.assertTrue(third['objects'][0]['uploaded'])
        self.assertEqual(len(host.methods(self.host, 'HEAD')), 3)

    def test_object_key_is_content_addressed(self):
        slug, version = self.work([1])
        manifest, _, package = self.store.info(slug, version)
        item = manifest['body_media'][0]
        digest = hashlib.sha256((package / item['file']).read_bytes()).hexdigest()
        uploaded = self.store._buffer_upload(slug, version, package, self.store._buffer_media(package, manifest))
        self.assertEqual(uploaded['objects'][0]['key'],
                         'article-studio/x/%s/%s/%s.png' % (slug, version, digest))
        self.assertEqual(uploaded['urls'][item['file']],
                         'https://' + host.PUBLIC_HOST + '/' + uploaded['objects'][0]['key'])

    def test_upload_rechecks_the_real_file_size(self):
        package = Path(self.temp.name) / 'pkg'
        (package / 'images').mkdir(parents=True)
        (package / 'images' / 'big.png').write_bytes(b'x' * (5 * 1024 * 1024 + 1))
        manifest = {'blocks': [{'kind': 'text', 'html': '<p>t</p>'}, {'kind': 'image', 'id': 'media-001'}],
                    'body_media': [{'id': 'media-001', 'file': 'images/big.png', 'bytes': 10}]}
        media = self.store._buffer_media(package, manifest)
        self.assertEqual(media['problems'], [])          # the manifest understates the size
        with self.assertRaises(ValueError) as caught:
            self.store._buffer_upload('slug-x', 'v0001', package, media)
        self.assertIn('5 MB', str(caught.exception))
        self.assertEqual(host.methods(self.host, 'PUT'), [])

    # --- 4. pre-flight checks -------------------------------------------------
    def checker(self, posts, unlink=()):
        package = Path(self.temp.name) / 'pkg'
        (package / 'images').mkdir(parents=True, exist_ok=True)
        blocks, media = [], []
        for index, images in enumerate(posts):
            blocks.append({'kind': 'text', 'html': '<p>t%d</p>' % index})
            for name, size in images:
                (package / 'images' / name).write_bytes(b'x' * size)
                media.append({'id': 'media-%03d' % len(media), 'file': 'images/' + name,
                              'bytes': size, 'width': 10, 'height': 10})
                blocks.append({'kind': 'image', 'id': media[-1]['id']})
        for name in unlink:
            (package / 'images' / name).unlink()
        return self.store._buffer_media(package, {'blocks': blocks, 'body_media': media})

    def test_more_than_four_images_per_tweet_blocks(self):
        media = self.checker([[('a.png', 10), ('b.png', 10), ('c.png', 10), ('d.png', 10), ('e.png', 10)]])
        self.assertTrue(any('最多 4 张' in problem for problem in media['problems']))
        self.assertEqual(media['total'], 5)

    def test_four_images_in_one_tweet_is_allowed(self):
        media = self.checker([[('a.png', 10), ('b.png', 10), ('c.png', 10), ('d.png', 10)]])
        self.assertEqual(media['problems'], [])

    def test_gif_cannot_mix_with_other_images_but_alone_is_fine(self):
        mixed = self.checker([[('a.gif', 10), ('b.png', 10)]])
        self.assertTrue(any('GIF' in problem for problem in mixed['problems']))
        alone = self.checker([[('a.gif', 10)], [('b.gif', 10)]])
        self.assertEqual(alone['problems'], [])

    def test_format_and_size_limits_name_the_file(self):
        media = self.checker([[('a.tiff', 10), ('b.png', 6 * 1024 * 1024)]])
        joined = '；'.join(media['problems'])
        self.assertIn('a.tiff', joined)
        self.assertIn('不支持的图片格式', joined)
        self.assertIn('b.png', joined)
        self.assertIn('6.0 MB', joined)

    def test_missing_local_file_is_reported(self):
        media = self.checker([[('a.png', 10)]], unlink=('a.png',))
        self.assertTrue(any('本机素材文件不存在' in problem for problem in media['problems']))

    def test_image_problems_block_before_any_upload_or_cli_call(self):
        slug, version = self.work([5])
        self.steps(self.channel_step())
        preview = self.check(slug, version)
        self.assertFalse(preview['resolved'])
        self.assertTrue(any('最多 4 张' in note for note in preview['blocking']))
        with self.assertRaises(ValueError) as caught:
            self.submit(slug, version, 'draft')
        self.assertIn('最多 4 张', str(caught.exception))
        self.assertEqual(host.methods(self.host, 'PUT'), [])
        self.assertEqual(self.create_calls(), [])

    def test_missing_image_host_blocks_images_but_not_text(self):
        slug, version = self.work([1])
        os.environ['ARTICLE_STUDIO_R2_CONFIG'] = str(Path(self.temp.name) / 'absent-r2.json')
        self.steps(self.channel_step(), self.channel_step(), self.created_step())
        preview = self.check(slug, version)
        self.assertFalse(preview['resolved'])
        self.assertTrue(any('尚未配置图床' in note for note in preview['blocking']))
        with self.assertRaises(ValueError) as caught:
            self.submit(slug, version, 'draft')
        self.assertIn('尚未配置图床', str(caught.exception))
        self.assertEqual(host.methods(self.host, 'PUT'), [])
        self.assertEqual(self.create_calls(), [])
        text_slug, text_version = self.work([0, 0])
        self.assertEqual(self.submit(text_slug, text_version, 'draft')['record']['kind'], 'buffer_draft')
        self.assertEqual(host.methods(self.host, 'PUT'), [])

    def test_unreadable_image_host_config_is_reported_without_leaking_secrets(self):
        self.config_path.write_text('{ not json', encoding='utf-8')
        slug, version = self.work([1])
        self.steps(self.channel_step())
        with self.assertRaises(ValueError) as caught:
            self.submit(slug, version, 'draft')
        self.assertIn('图床（R2）配置不可用', str(caught.exception))
        self.assertNotIn(SECRET, str(caught.exception))

    # --- request body cannot inject links -------------------------------------
    def test_request_body_cannot_supply_media_urls(self):
        slug, version = self.work([1])
        self.steps(self.channel_step(), self.created_step())
        for interface in ('submit', 'channels', 'status'):
            with self.subTest(interface=interface):
                with self.assertRaises(ValueError) as caught:
                    getattr(self.store, 'buffer_channels' if interface == 'channels'
                            else 'buffer_submit' if interface == 'submit' else 'buffer_refresh')(
                        slug, {'version': version, 'platform': 'x', 'mode': 'draft',
                               'confirmed': True, 'media_urls': {'images/x.png': 'https://evil.example/x.png'}})
                self.assertIn('media_urls', str(caught.exception))
        self.assertEqual(self.cli_calls(), [])
        self.assertEqual(host.methods(self.host, 'PUT'), [])

    # --- 6. delivery record ---------------------------------------------------
    def test_delivery_record_keeps_keys_and_links_but_no_credentials(self):
        slug, version = self.work()
        self.steps(self.channel_step(), self.created_step())
        self.submit(slug, version, 'draft')
        record = self.deliveries(slug)[0]
        self.assertEqual(len(record['media']), 3)
        for item in record['media']:
            self.assertTrue(item['key'].startswith('article-studio/x/%s/%s/' % (slug, version)))
            self.assertTrue(item['url'].startswith('https://' + host.PUBLIC_HOST + '/'))
            self.assertGreater(item['bytes'], 0)
        dumped = json.dumps(record, ensure_ascii=False)
        self.assertNotIn(SECRET, dumped)
        self.assertNotIn(KEY_ID, dumped)
        self.assertNotIn('Authorization', dumped)

    def test_channels_preview_lists_images_per_tweet_with_local_thumbnails(self):
        slug, version = self.work()
        self.steps(self.channel_step())
        preview = self.check(slug, version)
        self.assertEqual(preview['image_total'], 3)
        self.assertEqual([post['image_count'] for post in preview['posts']], [1, 2])
        for post in preview['posts']:
            for image in post['images']:
                self.assertTrue(image['url'].startswith('/assets/' + slug + '/' + version + '/images/'))
                self.assertGreater(image['bytes'], 0)
        self.assertEqual(host.methods(self.host, 'PUT'), [])

    def test_status_refresh_does_not_upload(self):
        slug, version = self.work([1])
        self.steps(self.channel_step(), self.created_step(),
                   {'stdout': json.dumps({'id': POST_ID, 'status': 'draft'}), 'returncode': 0})
        self.submit(slug, version, 'draft')
        puts = len(host.methods(self.host, 'PUT'))
        result = self.store.buffer_refresh(slug, {'version': version, 'platform': 'x'})
        self.assertEqual(result['record']['buffer_status'], 'draft')
        self.assertEqual(len(host.methods(self.host, 'PUT')), puts)

    # --- 7. image-host status is visible without secrets ----------------------
    def test_media_status_reports_readiness_without_secrets(self):
        status = self.store.buffer_media_status()
        self.assertTrue(status['configured'])
        self.assertEqual(status['host'], host.PUBLIC_HOST)
        self.assertIsNone(status['problem'])
        os.environ['ARTICLE_STUDIO_R2_CONFIG'] = str(Path(self.temp.name) / 'absent.json')
        status = self.store.buffer_media_status()
        self.assertFalse(status['configured'])
        self.assertIn('尚未配置图床', status['problem'])
        self.assertNotIn(SECRET, json.dumps(status, ensure_ascii=False))
        self.assertNotIn(KEY_ID, json.dumps(status, ensure_ascii=False))

    # --- 9. text-only requests stay byte identical ----------------------------
    def test_text_only_request_is_unchanged(self):
        slug, version = self.work([0, 0])
        self.steps(self.channel_step(), self.created_step())
        self.submit(slug, version, 'draft')
        body = json.loads(self.create_calls()[0]['stdin'])
        self.assertEqual(sorted(body), sorted(['channelId', 'schedulingType', 'mode', 'saveToDraft',
                                               'text', 'metadata']))
        self.assertNotIn('assets', body)
        self.assertEqual(host.methods(self.host, 'PUT'), [])
        self.assertEqual(self.deliveries(slug)[0]['media'], [])


if __name__ == '__main__':
    unittest.main()
