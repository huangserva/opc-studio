"""创意回收工厂第一期：投喂、解析、入库（intake_store）。"""
import io
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from PIL import Image

import intake_store
from tests.intake_dependency_patches import guard_intake_dependencies, patch_intake_dependency
from intake_store import Intake
from knowledge_store import Knowledge
from server import Store


class FakeWeb:
    """Stands in for assistant_web.WebTools; records the paging calls it receives."""

    def __init__(self, pages=None, error=None, title='合成页面'):
        self.pages, self.error, self.title, self.calls = pages or [], error, title, []

    def __call__(self):
        return self

    def fetch(self, args, job):
        self.calls.append(dict(args))
        if self.error:
            raise ValueError(self.error)
        index = len(self.calls) - 1
        page = self.pages[index] if index < len(self.pages) else ''
        total = sum(len(p) for p in self.pages)
        start = args.get('start', 0)
        end = min(start + args.get('max_chars', 16000), total)
        body = ''.join(self.pages)
        return {'url': args['url'], 'title': self.title, 'markdown': body[start:end], 'start': start,
                'total_chars': total, 'truncated': end < total, 'next_start': end if end < total else None}


def png_bytes(size=(24, 16), colour='red'):
    buffer = io.BytesIO()
    Image.new('RGB', size, colour).save(buffer, format='PNG')
    return buffer.getvalue()


class IntakeTests(unittest.TestCase):
    def setUp(self):
        guard_intake_dependencies(self)
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(self.tmp.name)
        self.store._knowledge = Knowledge(self.store, sources=[])
        self.intake = Intake(self.store.knowledge, self.tmp.name)
        from unittest.mock import patch
        self.media_patch=patch('intake_media_recognition.MediaRecognizer.recognize_video',return_value={'status':'no_audio','scope':'视频无音轨'})
        self.media_patch.start()
        self.addCleanup(self.media_patch.stop)
        self.fakes = []

    def tearDown(self):
        intake=self.intake
        if intake:
            for worker in list(intake.recognition_workers.values()):worker.join(timeout=5)
        self.tmp.cleanup()

    def fake_fetch(self, pages=None, error=None, title='合成页面'):
        fake = FakeWeb(pages, error, title)
        self.fakes.append(fake)
        patch_intake_dependency(self, "WebTools", fake)
        return fake

    def doc(self, item):
        return self.store.knowledge.document(item['document_id'])

    def resolve(self, payload):
        return self.intake.resolve(self.intake.receive(payload)['id'])

    # ── 链接 ──────────────────────────────────────────────────────────────────

    def test_link_drop_fetches_and_files_reference_with_honest_scope(self):
        self.fake_fetch(pages=['第一段。' * 10, '第二段。' * 10])
        saved = self.intake.receive({'kind': 'link', 'url': 'https://example.com/post', 'reason': '开头方式可以借'})
        self.assertEqual(saved['status'], 'pending')
        done = self.intake.resolve(saved['id'])
        self.assertEqual(done['status'], 'resolved')
        self.assertEqual(done['outcome'], 'stored')
        self.assertEqual(done['state'], '已存入知识库，已抓取正文，共 80 字')
        self.assertEqual(done['content_scope'], '已抓取正文，共 80 字')
        record = self.doc(done)
        self.assertEqual(record['source'], 'external-references')
        self.assertEqual(record['kind'], 'external')
        self.assertEqual(record['url'], 'https://example.com/post')
        self.assertEqual(record['title'], '合成页面')
        self.assertEqual(record['body'], ('第一段。' * 10) + ('第二段。' * 10))
        provenance = record['provenance']
        self.assertEqual(provenance['intake_channel'], '创意回收工厂')
        self.assertEqual(provenance['drop_id'], saved['id'])
        self.assertIs(provenance['fetched'], True)
        self.assertTrue(provenance['fetched_at'])
        self.assertEqual(provenance['content_scope'], '已抓取正文，共 80 字')
        self.assertEqual(provenance['discovered_via'], '复制链接')
        self.assertNotIn('开头方式可以借', record['body'])
        self.assertNotIn('开头方式可以借', record['title'] + record['url'] + record['provenance']['discovery_detail'])

    def test_link_paging_records_every_page_window(self):
        self.fake_fetch(pages=['a' * 16000, 'b' * 16000, 'c' * 8000])
        saved = self.intake.receive({'kind': 'link', 'url': 'https://example.com/long'})
        done = self.intake.resolve(saved['id'])
        self.assertEqual(done['meta']['pages'], [{'start': 0, 'end': 16000}, {'start': 16000, 'end': 32000},
                                                 {'start': 32000, 'end': 40000}])
        self.assertEqual(done['content_scope'], '已抓取正文，共 40000 字')
        self.assertEqual(len(self.doc(done)['body']), 40000)

    def test_link_beyond_page_ceiling_says_what_was_not_read(self):
        self.fake_fetch(pages=['x' * 16000] * (intake_store.MAX_PAGES + 2))
        saved = self.intake.receive({'kind': 'link', 'url': 'https://example.com/verylong'})
        done = self.intake.resolve(saved['id'])
        read = intake_store.MAX_PAGES * 16000
        self.assertEqual(done['content_scope'], f'已抓取正文前 {read} 字；原文共 {read + 32000} 字，剩余未读取')
        self.assertEqual(done['status'], 'resolved')

    def test_fetch_failure_keeps_no_partial_body_and_reports_real_reason(self):
        self.fake_fetch(error='联网服务响应超时，请稍后重试')
        saved = self.intake.receive({'kind': 'link', 'url': 'https://example.com/slow'})
        done = self.intake.resolve(saved['id'])
        self.assertEqual(done['status'], 'failed')
        self.assertEqual(done['outcome'], 'failed')
        self.assertEqual(done['error'], '联网服务响应超时，请稍后重试')
        self.assertEqual(self.intake._raw(saved['id'])['text'], '')
        self.assertEqual(done['state'], '抓取失败：联网服务响应超时，请稍后重试；已存入知识库（仅链接）')
        self.assertEqual(done['content_scope'], '仅收藏链接；原文正文未读取（抓取失败：联网服务响应超时，请稍后重试）')
        record = self.doc(done)
        self.assertEqual(record['body'], '')
        self.assertIs(record['provenance']['fetched'], False)
        self.assertEqual(record['title'], 'https://example.com/slow')
        self.assertEqual(done['meta']['title_pending'], '标题待补')

    def test_user_title_survives_a_failed_fetch(self):
        self.fake_fetch(error='网页域名暂时无法解析，请核对地址或稍后重试')
        saved = self.intake.receive({'kind': 'link', 'url': 'https://example.com/gone', 'title': '我给的标题'})
        done = self.intake.resolve(saved['id'])
        self.assertEqual(self.doc(done)['title'], '我给的标题')
        self.assertIn('网页域名暂时无法解析', done['state'])

    def test_duplicate_url_is_reported_without_overwriting_the_existing_note(self):
        kept = self.store.knowledge.add_reference({'title': '已有收藏', 'url': 'https://example.com/kept',
                                                   'reader_note': '我当时写的评论'})
        self.fake_fetch(pages=['新抓到的正文'])
        saved = self.intake.receive({'kind': 'link', 'url': 'https://example.com/kept'})
        done = self.intake.resolve(saved['id'])
        self.assertEqual(done['outcome'], 'duplicate')
        self.assertEqual(done['state'], '已存在，未重复导入')
        self.assertEqual(done['document_id'], kept['id'])
        record = self.doc(done)
        self.assertEqual(record['title'], '已有收藏')
        self.assertEqual(record['body'], '')
        self.assertEqual(record['provenance']['reader_note'], '我当时写的评论')

    def test_resolve_does_not_refetch_an_already_resolved_drop(self):
        fake = self.fake_fetch(pages=['正文'])
        saved = self.intake.receive({'kind': 'link', 'url': 'https://example.com/once'})
        self.intake.resolve(saved['id'])
        again = self.intake.resolve(saved['id'])
        self.assertEqual(len(fake.calls), 1)
        self.assertEqual(again['revision'], 2)

    # ── 文字 ──────────────────────────────────────────────────────────────────

    def test_text_drop_is_stored_verbatim_and_reason_stays_out_of_the_body(self):
        original = '第一行\n  缩进也要保留  \n第二行：结论待核对。'
        saved = self.intake.receive({'kind': 'text', 'text': original, 'reason': '以后写稿要用'})
        done = self.intake.resolve(saved['id'])
        self.assertEqual(done['status'], 'resolved')
        self.assertEqual(done['state'], '已存入知识库，原文逐字保存')
        record = self.doc(done)
        self.assertEqual(record['body'], original)
        self.assertEqual(record['source'], 'manual')
        self.assertEqual(record['kind'], 'notes')
        self.assertEqual(record['url'], '')
        self.assertTrue(record['title'].startswith('创意回收 · 文字 · '))
        self.assertNotIn('以后写稿要用', record['body'])
        self.assertEqual(record['provenance']['intake_reason'], '以后写稿要用')
        self.assertEqual(record['provenance']['intake_channel'], '创意回收工厂')

    def test_text_keeps_every_space_and_newline_in_both_stores(self):
        original = '  第一行\n\t中间带制表符\n最后一行  \n\n'
        saved = self.intake.receive({'kind': 'text', 'text': original, 'reason': '  以后写稿要用  '})
        self.assertEqual(self.intake._raw(saved['id'])['text'], original)
        self.assertEqual(self.intake._raw(saved['id'])['reason'], '  以后写稿要用  ')
        done = self.intake.resolve(saved['id'])
        self.assertEqual(self.doc(done)['body'], original)
        self.assertEqual(done['content_scope'], f'用户原文逐字保存，共 {len(original)} 字')
        self.assertEqual(done['text_chars'], len(original))
        self.assertEqual(self.doc(done)['provenance']['intake_reason'], '  以后写稿要用  ')

    def test_whitespace_only_text_is_refused_and_blank_reason_becomes_empty(self):
        for value in ('   ', '\n\t ', ' \u3000 '):
            with self.assertRaises(ValueError) as bad:
                self.intake.receive({'kind': 'text', 'text': value})
            self.assertIn('请填写文字内容', str(bad.exception))
        saved = self.intake.receive({'kind': 'text', 'text': '一条想法', 'reason': '   \n '})
        self.assertEqual(self.intake._raw(saved['id'])['reason'], '')
        self.assertEqual(self.intake.resolve(saved['id'])['reason'], '')
        self.assertEqual(self.intake._raw(saved['id'])['text'], '一条想法')

    def test_file_upload_keeps_the_reason_verbatim(self):
        patch_intake_dependency(self, "ffprobe", lambda: None)
        saved = self.upload('image', 'shot.png', png_bytes(), '  构图可参考\n')
        self.assertEqual(self.intake._raw(saved['id'])['reason'], '  构图可参考\n')
        self.assertEqual(self.doc(self.intake.resolve(saved['id']))['provenance']['intake_reason'], '  构图可参考\n')

    # ── 图片 / 视频 ───────────────────────────────────────────────────────────

    def upload(self, kind, name, data, reason=''):
        return self.intake.receive_file(kind, name, reason, io.BytesIO(data), len(data))

    def test_image_drop_says_the_content_was_not_read(self):
        patch_intake_dependency(self, "ffprobe", lambda: None)
        saved = self.upload('image', 'shot 01.png', png_bytes(), '构图可参考')
        self.assertEqual(saved['kind'], 'image')
        done = self.intake.resolve(saved['id'])
        self.assertEqual(done['state'], '已存入知识库；图片内容未识别')
        record = self.doc(done)
        self.assertTrue(record['body'].startswith('本机图片：shot 01.png，24×16，PNG，'))
        self.assertTrue(record['body'].endswith('。图片内容未识别。'))
        self.assertIn('图片内容未识别', record['body'])
        self.assertEqual(record['url'], '')
        for leak in (self.tmp.name, '/Users/', 'workspace', 'media/'):
            self.assertNotIn(leak, record['body'])
            self.assertNotIn(leak, record['url'])
        self.assertEqual(record['provenance']['content_scope'], '仅保存本机图片与尺寸格式；图片内容未识别')
        self.assertNotIn('构图可参考', record['body'])
        self.assertEqual(done['file']['width'], 24)
        self.assertTrue((self.intake.root / done['file_path']).is_file())

    def test_image_is_stored_under_our_own_name_with_the_detected_format(self):
        patch_intake_dependency(self, "ffprobe", lambda: None)
        saved = self.upload('image', 'sneaky.php', png_bytes((10, 10)))
        self.assertEqual(saved['file']['format'], 'PNG')
        self.assertEqual(saved['file']['name'], 'sneaky.php')
        self.assertTrue(saved['file_path'].startswith('media/'))
        self.assertNotIn('..', saved['file_path'])
        self.assertTrue(saved['file_path'].endswith('.png'))

    def test_video_drop_says_the_body_and_audio_were_not_read(self):
        patch_intake_dependency(self, "ffprobe", lambda: None)
        saved = self.upload('video', 'clip.mp4', b'\x00' * 2048, '节奏可参考')
        done = self.intake.resolve(saved['id'])
        self.assertEqual(done['state'], '已存入知识库；视频正文与语音未读取')
        record = self.doc(done)
        self.assertEqual(record['body'], '本机视频：clip.mp4，MP4，本机 ffprobe 不可用，时长未读取，2.0 KB。视频正文与语音未读取。')
        self.assertNotIn('节奏可参考', record['body'])
        self.assertNotIn(self.tmp.name, json.dumps(record, ensure_ascii=False))

    def test_video_duration_comes_from_the_local_probe(self):
        def probe():
            return '/usr/bin/ffprobe'

        def run(command, **kwargs):
            class Out:
                stdout = json.dumps({'format': {'duration': '90.5', 'format_name': 'mov,mp4,m4a'},
                                     'streams': [{'codec_type': 'video'}, {'codec_type': 'audio'}]})
            return Out()

        with patch.object(intake_store, 'ffprobe', probe), \
                patch.object(intake_store.subprocess, 'run', run):
            saved = self.upload('video', 'clip.mov', b'\x00' * 512)
        self.assertEqual(saved['file']['duration_seconds'], 90.5)
        self.assertTrue(saved['file']['has_audio'])
        done = self.intake.resolve(saved['id'])
        self.assertIn('1 分 30 秒', self.doc(done)['body'])
        self.assertIn('视频正文与语音未读取', self.doc(done)['body'])

    def test_file_size_and_pixel_limits(self):
        with self.assertRaises(ValueError) as bad:
            self.intake.receive_file('image', 'big.png', '', io.BytesIO(b''), 10 * 1024 * 1024 + 1)
        self.assertIn('10 MB', str(bad.exception))
        with self.assertRaises(ValueError) as bad:
            self.intake.receive_file('video', 'big.mp4', '', io.BytesIO(b''), 1024 ** 3 + 1)
        self.assertIn('1 GB', str(bad.exception))
        keep = intake_store.MAX_PIXELS
        intake_store.MAX_PIXELS = 100
        try:
            with self.assertRaises(ValueError) as bad:
                self.upload('image', 'huge.png', png_bytes((20, 20)))
            self.assertIn('像素', str(bad.exception))
        finally:
            intake_store.MAX_PIXELS = keep
        with self.assertRaises(ValueError):
            self.upload('image', 'clip.txt', b'not an image')
        with self.assertRaises(ValueError):
            self.upload('video', 'notes.txt', b'\x00' * 10)

    # ── 存储与幂等 ────────────────────────────────────────────────────────────

    def test_directories_are_seventy_and_the_database_is_sixty(self):
        self.intake.receive({'kind': 'text', 'text': '一条想法'})
        self.assertEqual(self.intake.root.stat().st_mode & 0o777, 0o700)
        self.assertEqual(self.intake.media_root.stat().st_mode & 0o777, 0o700)
        self.assertEqual(self.intake.path.stat().st_mode & 0o777, 0o600)

    def test_same_id_never_creates_a_second_row(self):
        first = self.intake.receive({'id': 'a' * 32, 'kind': 'text', 'text': '同一想法'})
        again = self.intake.receive({'id': 'a' * 32, 'kind': 'text', 'text': '同一想法'})
        self.assertEqual(first['id'], again['id'])
        self.assertEqual(len(self.intake.listing()['items']), 1)
        self.assertEqual(again['revision'], 1)

    def test_local_paths_and_credentials_are_refused(self):
        for value in ('/Users/example/Downloads/a.png', 'file:///tmp/a.png', '~/notes.txt',
                      'http://127.0.0.1:8766/api/intake', 'http://192.168.1.5/doc', 'http://localhost/x',
                      'http://user:secret@example.com/x', 'https://example.com:8443/x', 'ftp://example.com/x'):
            with self.assertRaises(ValueError) as bad:
                self.intake.receive({'kind': 'link', 'url': value})
            self.assertTrue(str(bad.exception))
        self.assertEqual(self.intake.listing()['items'], [])

    def test_upload_never_accepts_a_client_supplied_path(self):
        with self.assertRaises(ValueError):
            self.intake.receive_file('image', '/tmp/x.png', '', io.BytesIO(png_bytes()), len(png_bytes()))
        with self.assertRaises(ValueError):
            self.intake.receive_file('spreadsheet', 'sheet.xlsx', '', io.BytesIO(b'x'), 1)

    def test_the_four_states_never_blend(self):
        self.fake_fetch(pages=['正文'])
        fetched = self.resolve({'kind': 'link', 'url': 'https://example.com/a'})
        patch_intake_dependency(self, "ffprobe", lambda: None)
        image = self.intake.resolve(self.upload('image', 'shot.png', png_bytes())['id'])
        video = self.intake.resolve(self.upload('video', 'clip.mp4', b'\x00' * 512)['id'])
        text = self.resolve({'kind': 'text', 'text': '一段想法'})
        self.fake_fetch(error='网页没有返回可读正文，请换文档页或提供文字')
        failed = self.resolve({'kind': 'link', 'url': 'https://example.com/b'})
        states = {fetched['state'], image['state'], video['state'], text['state'], failed['state']}
        self.assertEqual(len(states), 5)
        self.assertTrue(fetched['state'].startswith('已存入知识库，已抓取正文'))
        self.assertTrue(failed['state'].startswith('抓取失败：'))
        self.assertTrue(image['state'].endswith('图片内容未识别'))
        self.assertTrue(video['state'].endswith('视频正文与语音未读取'))
        self.assertNotIn('智能分析', json.dumps([fetched, image, video, text, failed], ensure_ascii=False))

    def test_listing_and_detail_report_the_knowledge_link(self):
        self.fake_fetch(pages=['正文'])
        done = self.resolve({'kind': 'link', 'url': 'https://example.com/c'})
        listed = self.intake.listing()['items'][0]
        self.assertEqual(listed['id'], done['id'])
        self.assertEqual(listed['knowledge_url'], '/knowledge.html?document=' + done['document_id'])
        self.assertEqual(self.intake.detail(done['id'])['outcome'], 'stored')
        with self.assertRaises(ValueError):
            self.intake.detail('f' * 32)

    def test_file_lookup_only_accepts_a_drop_id(self):
        patch_intake_dependency(self, "ffprobe", lambda: None)
        saved = self.upload('image', 'shot.png', png_bytes())
        self.assertTrue(self.intake.file_path(saved['id']).is_file())
        with self.assertRaises(ValueError):
            self.intake.file_path('a' * 32)
        with self.assertRaises(ValueError):
            self.intake.file_path(self.resolve({'kind': 'text', 'text': '没有文件'})['id'])


class ComboIntakeTests(unittest.TestCase):
    """链接/文字 + 图片/视频 一起收下：一次「收下」只产生同一条回收项。

    文件先暂存，点「收下」时和输入框内容 + 收藏理由一起入库；只有文件、只有链接的旧用法照旧可用。
    """

    def setUp(self):
        guard_intake_dependencies(self)
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(self.tmp.name)
        self.store._knowledge = Knowledge(self.store, sources=[])
        self.intake = Intake(self.store.knowledge, self.tmp.name)
        patch_intake_dependency(self, "ffprobe", lambda: None)
        from unittest.mock import patch
        self.media_patch = patch('intake_media_recognition.MediaRecognizer.recognize_image',
                                 return_value={'status': 'unavailable', 'error': '未接上图片识别模型'})
        self.media_patch.start()
        self.addCleanup(self.media_patch.stop)

    def tearDown(self):
        for worker in list(self.intake.recognition_workers.values()):
            worker.join(timeout=5)
        self.tmp.cleanup()

    def fake_fetch(self, pages=None, error=None, title='合成页面'):
        fake = FakeWeb(pages, error, title)
        patch_intake_dependency(self, "WebTools", fake)
        return fake

    def stage(self, kind='image', name='shot 01.png', data=None):
        data = png_bytes() if data is None else data
        return self.intake.stage_file(kind, name, io.BytesIO(data), len(data))

    def doc(self, item):
        return self.store.knowledge.document(item['document_id'])

    # ── 链接 + 图片 ───────────────────────────────────────────────────────────

    def test_a_link_and_a_selected_file_become_one_drop(self):
        self.fake_fetch(pages=['第一段正文。'])
        staged = self.stage()
        saved = self.intake.receive({'id': 'a' * 32, 'kind': 'link', 'url': 'https://example.com/post',
                                     'reason': '这个开头方式可以借', 'file_token': staged['token']})
        self.assertEqual(saved['kind'], 'link')
        self.assertEqual(saved['media_kind'], 'image')
        self.assertTrue(saved['file_path'])
        self.assertEqual(self.intake.listing()['items'][0]['id'], saved['id'])
        self.assertEqual(len(self.intake.listing()['items']), 1)
        done = self.intake.resolve(saved['id'])
        self.assertEqual(done['url'], 'https://example.com/post')
        self.assertEqual(self.doc(done)['body'], '第一段正文。')          # 链接正文逐字存，附件不塞进正文
        self.assertIn('另存本机图片：shot 01.png', done['content_scope'])
        self.assertIn('图片内容未识别', done['state'])
        self.assertEqual(self.intake.file_path(saved['id']).read_bytes(), png_bytes())
        self.assertEqual(self.doc(done)['provenance']['intake_channel'], '创意回收工厂')

    def test_text_and_a_selected_file_keep_the_text_verbatim(self):
        staged = self.stage()
        original = '  开头两个空格\n结尾有换行  \n'
        saved = self.intake.receive({'kind': 'text', 'text': original, 'reason': '以后要用',
                                     'file_token': staged['token']})
        self.assertEqual(saved['kind'], 'text')
        self.assertEqual(saved['media_kind'], 'image')
        done = self.intake.resolve(saved['id'])
        self.assertEqual(self.doc(done)['body'], original)
        self.assertNotIn('以后要用', self.doc(done)['body'])
        self.assertIn('另存本机图片', done['content_scope'])
        self.assertEqual(len(self.intake.listing()['items']), 1)

    def test_a_file_alone_still_works_through_the_stage_route(self):
        staged = self.stage()
        saved = self.intake.receive({'file_token': staged['token']})
        self.assertEqual(saved['kind'], 'image')
        self.assertEqual(saved['url'], '')
        done = self.intake.resolve(saved['id'])
        self.assertIn('本机图片：shot 01.png，24×16，PNG', self.doc(done)['body'])
        self.assertIn('图片内容未识别', self.doc(done)['body'])

    def test_the_old_file_only_and_link_only_routes_stay_available(self):
        self.fake_fetch(pages=['旧用法正文。'])
        link = self.intake.resolve(self.intake.receive({'kind': 'link', 'url': 'https://example.com/old'})['id'])
        uploaded = self.intake.receive_file('image', 'old.png', '旧用法',
                                            io.BytesIO(png_bytes()), len(png_bytes()))
        upload = self.intake.resolve(uploaded['id'])
        self.assertEqual(link['media_kind'], '')
        self.assertEqual(upload['media_kind'], 'image')
        self.assertEqual(len(self.intake.listing()['items']), 2)

    def test_a_combined_drop_deletes_its_file_with_the_drop(self):
        self.fake_fetch(pages=['正文。'])
        staged = self.stage()
        saved = self.intake.receive({'kind': 'link', 'url': 'https://example.com/x', 'file_token': staged['token']})
        self.intake.resolve(saved['id'])
        self.assertTrue(self.intake.file_path(saved['id']).is_file())
        result = self.intake.delete(saved['id'])
        self.assertTrue(result['file_removed'])
        self.assertFalse(self.intake.exists(saved['id']))

    # ── 暂存区 ────────────────────────────────────────────────────────────────

    def test_stage_validates_kind_name_size_and_leaves_nothing_behind_on_failure(self):
        with self.assertRaises(ValueError):
            self.intake.stage_file('spreadsheet', 'sheet.xlsx', io.BytesIO(b'x'), 1)
        with self.assertRaises(ValueError):
            self.intake.stage_file('image', '../x.png', io.BytesIO(png_bytes()), len(png_bytes()))
        with self.assertRaises(ValueError) as bad:
            self.intake.stage_file('image', 'big.png', io.BytesIO(b''), 10 * 1024 * 1024 + 1)
        self.assertIn('10 MB', str(bad.exception))
        with self.assertRaises(ValueError):
            self.intake.stage_file('video', 'clip.txt', io.BytesIO(b'\x00' * 10), 10)
        self.assertEqual([path.name for path in self.intake.media_root.glob('.stage-*')], [])

    def test_a_missing_or_forged_token_is_refused_without_creating_a_drop(self):
        with self.assertRaises(ValueError) as bad:
            self.intake.receive({'kind': 'text', 'text': '一条想法', 'file_token': 'f' * 32})
        self.assertIn('已不在', str(bad.exception))
        with self.assertRaises(ValueError):
            self.intake.receive({'kind': 'text', 'text': '一条想法', 'file_token': '../etc/passwd'})
        self.assertEqual(self.intake.listing()['items'], [])

    def test_a_bad_link_with_a_staged_file_leaves_nothing_behind(self):
        staged = self.stage()
        with self.assertRaises(ValueError):
            self.intake.receive({'kind': 'link', 'url': 'http://127.0.0.1:8766/x', 'file_token': staged['token']})
        self.assertEqual(self.intake.listing()['items'], [])
        self.assertEqual([path.name for path in self.intake.media_root.glob('.stage-*')], [])

    def test_a_staged_file_is_used_once_and_a_second_submit_says_so(self):
        staged = self.stage()
        self.intake.receive({'kind': 'text', 'text': '同一条', 'file_token': staged['token']})
        with self.assertRaises(ValueError):
            self.intake.receive({'kind': 'text', 'text': '再交一次', 'file_token': staged['token']})
        self.assertEqual(len(self.intake.listing()['items']), 1)

    def test_stale_unsubmitted_stage_files_are_swept(self):
        staged = self.stage()
        path = self.intake.media_root / ('.stage-' + staged['token'])
        import os
        import time as clock
        os.utime(path, (0, 0))
        removed = self.intake._sweep_stages(now_value=clock.time() + intake_store.STAGE_TTL_SECONDS + 10)
        self.assertGreaterEqual(removed, 1)
        self.assertFalse(path.exists())
        self.assertFalse((self.intake.media_root / ('.stage-' + staged['token'] + '.json')).exists())

    # ── 附件后缀跟着字节识别出的真实格式 ────────────────────────────────

    def test_a_link_with_a_suffixless_png_still_gets_the_real_image_suffix(self):
        self.fake_fetch(pages=['第一段正文。'])
        staged = self.stage(name='shot', data=png_bytes())          # 原始文件名没后缀
        saved = self.intake.receive({'kind': 'link', 'url': 'https://example.com/post',
                                     'file_token': staged['token']})
        self.assertTrue(saved['file_path'].endswith('.png'), saved['file_path'])
        self.assertEqual(self.intake.file_path(saved['id']).read_bytes(), png_bytes())

    def test_a_png_named_fake_jpg_is_stored_as_png(self):
        staged = self.stage(name='fake.jpg', data=png_bytes())
        saved = self.intake.receive({'kind': 'text', 'text': '一段想法', 'file_token': staged['token']})
        self.assertTrue(saved['file_path'].endswith('.png'), saved['file_path'])
        self.assertNotIn('.jpg', saved['file_path'])
        self.assertEqual(saved['file']['format'], 'PNG')

    # ── 只剩一半或坏了的暂存在领取时就清干净 ────────────────────────

    def test_a_half_written_or_corrupt_stage_is_cleaned_when_it_is_claimed(self):
        cases = []
        staged = self.stage()
        (self.intake.media_root / ('.stage-' + staged['token'] + '.json')).write_text('不是 JSON', encoding='utf-8')
        cases.append(('元数据坏了', staged['token']))
        staged = self.stage()
        (self.intake.media_root / ('.stage-' + staged['token'] + '.json')).unlink()
        cases.append(('缺元数据', staged['token']))
        staged = self.stage()
        (self.intake.media_root / ('.stage-' + staged['token'])).unlink()
        cases.append(('缺本体', staged['token']))
        staged = self.stage()
        (self.intake.media_root / ('.stage-' + staged['token'] + '.json')).write_text(
            json.dumps({'name': 'shot 01.png', 'format': 'PNG'}), encoding='utf-8')
        cases.append(('字段不全', staged['token']))
        for label, token in cases:
            with self.assertRaises(ValueError):
                self.intake.receive({'kind': 'image', 'file_token': token})
            self.assertEqual(self.intake.listing()['items'], [], label)
            self.assertEqual([path.name for path in self.intake.media_root.glob('.stage-' + token + '*')], [], label)

    def test_a_stage_never_becomes_a_drop_by_itself(self):
        self.stage()
        self.assertEqual(self.intake.listing(200)['items'], [])

    # ── 同一个编号重复提交：原附件字节绝不覆盖、绝不删除 ──────────────────────

    def test_resubmitting_the_same_id_keeps_the_original_attachment_bytes(self):
        first = self.stage(name='first.png', data=png_bytes(colour='red'))
        saved = self.intake.receive({'id': 'a' * 32, 'kind': 'image', 'file_token': first['token'],
                                     'reason': '第一条'})
        original = self.intake.file_path(saved['id']).read_bytes()
        self.assertEqual(saved['file']['name'], 'first.png')
        second = self.stage(name='second.png', data=png_bytes(colour='blue'))
        again = self.intake.receive({'id': 'a' * 32, 'kind': 'image', 'file_token': second['token'],
                                     'reason': '重复提交'})
        # 幂等：返回原行，不新增第二条，也不把新文件换到原附件路径上。
        self.assertEqual(again['id'], saved['id'])
        self.assertEqual(again['file_path'], saved['file_path'])
        self.assertEqual(again['file']['name'], 'first.png')
        self.assertEqual(self.intake.file_path(saved['id']).read_bytes(), original)
        self.assertEqual(len(self.intake.listing()['items']), 1)
        self.assertEqual([path.name for path in self.intake.media_root.glob('.stage-*')], [])
        self.assertEqual(sorted(path.name for path in self.intake.media_root.glob('*')),
                         [Path(saved['file_path']).name])

    def test_two_concurrent_submits_of_one_id_keep_one_row_and_one_attachment(self):
        stages = [self.stage(name='one.png', data=png_bytes(colour='red')),
                  self.stage(name='two.png', data=png_bytes(colour='blue'))]
        rows, errors = [], []
        gate = threading.Barrier(2)

        def submit(staged):
            try:
                gate.wait(5)
                rows.append(self.intake.receive({'id': 'b' * 32, 'kind': 'image', 'file_token': staged['token']}))
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=submit, args=(staged,)) for staged in stages]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(10)
        self.assertEqual(errors, [])
        self.assertEqual(len(rows), 2)
        self.assertEqual({row['id'] for row in rows}, {'b' * 32})
        self.assertEqual({row['file_path'] for row in rows}, {rows[0]['file_path']})
        items = self.intake.listing()['items']
        self.assertEqual(len(items), 1)
        on_disk = self.intake.file_path('b' * 32).read_bytes()
        self.assertIn(on_disk, (png_bytes(colour='red'), png_bytes(colour='blue')))
        winner = 'one.png' if on_disk == png_bytes(colour='red') else 'two.png'
        self.assertEqual(items[0]['file']['name'], winner)
        self.assertEqual([path.name for path in self.intake.media_root.glob('.stage-*')], [])
        self.assertEqual(sorted(path.name for path in self.intake.media_root.glob('*')),
                         [Path(rows[0]['file_path']).name])

    def test_an_orphan_attachment_name_is_never_overwritten(self):
        self.intake._ensure()
        orphan = self.intake.media_root / ('e' * 32 + '.png')
        orphan.write_bytes(png_bytes(colour='green'))
        staged = self.stage()
        saved = self.intake.receive({'id': 'e' * 32, 'kind': 'image', 'file_token': staged['token']})
        self.assertNotEqual(saved['file_path'], 'media/' + 'e' * 32 + '.png')
        self.assertEqual(orphan.read_bytes(), png_bytes(colour='green'))
        self.assertEqual(self.intake.file_path(saved['id']).read_bytes(), png_bytes())

    # ── 领取 token 前先把请求校验完；失败不留暂存字节 ──────────────────────

    def test_every_rejected_submit_after_a_token_is_taken_leaves_no_stage_bytes(self):
        cases = [
            {'kind': 'spreadsheet'},                                        # 非法 kind
            {'kind': 'image', 'url': 'https://example.com/x'},              # 图片带链接
            {'kind': 'image', 'text': '一段想法'},                          # 图片带文字
            {'kind': 'image', 'reason': '长' * 2001},                       # 超长收藏理由
            {'kind': 'image', 'title': 7},                                  # 非字符串标题
            {'kind': 'image', 'text': 'x' * 200001},                        # 超长正文
        ]
        for case in cases:
            staged = self.stage()
            payload = dict(case, file_token=staged['token'])
            with self.assertRaises(ValueError):
                self.intake.receive(payload)
            self.assertEqual(self.intake.listing()['items'], [])
            self.assertEqual([path.name for path in self.intake.media_root.glob('.stage-*')], [],
                             '失败分支没清掉暂存：' + json.dumps(sorted(case)))

    def test_a_rename_failure_leaves_no_row_and_no_stage_bytes(self):
        staged = self.stage()
        with patch.object(intake_store.Intake, '_media_target',
                          side_effect=lambda drop_id, suffix: self.intake.media_root / 'gone' / (drop_id + suffix)):
            with self.assertRaises(OSError):
                self.intake.receive({'id': 'c' * 32, 'kind': 'image', 'file_token': staged['token']})
        self.assertEqual(self.intake.listing()['items'], [])
        self.assertEqual([path.name for path in self.intake.media_root.glob('.stage-*')], [])

    def test_a_failed_database_commit_cleans_the_moved_attachment(self):
        staged = self.stage()
        with patch.object(intake_store.Intake, '_public_row', side_effect=RuntimeError('提交失败')):
            with self.assertRaises(RuntimeError):
                self.intake.receive({'id': 'd' * 32, 'kind': 'image', 'file_token': staged['token']})
        self.assertEqual(self.intake.listing()['items'], [])
        self.assertEqual(list(self.intake.media_root.glob('*')), [])

    def test_a_stage_that_cannot_write_its_metadata_keeps_no_body(self):
        self.intake._ensure()
        with patch.object(intake_store.Intake, '_stage_meta',
                          return_value=self.intake.media_root / 'gone' / 'meta.json'):
            with self.assertRaises(OSError):
                self.intake.stage_file('image', 'shot.png', io.BytesIO(png_bytes()), len(png_bytes()))
        self.assertEqual([path.name for path in self.intake.media_root.glob('.stage-*')], [])

    # ── 六小时 TTL 在领取时真检查 ──────────────────────────────────────

    def test_an_expired_stage_is_refused_even_without_a_new_upload(self):
        staged = self.stage()
        body = self.intake.media_root / ('.stage-' + staged['token'])
        stale = time.time() - intake_store.STAGE_TTL_SECONDS - 60
        os.utime(body, (stale, stale))
        os.utime(self.intake.media_root / ('.stage-' + staged['token'] + '.json'), (stale, stale))
        with self.assertRaisesRegex(ValueError, '超过 6 小时'):
            self.intake.receive({'kind': 'image', 'file_token': staged['token']})
        self.assertEqual(self.intake.listing()['items'], [])
        self.assertEqual([path.name for path in self.intake.media_root.glob('.stage-*')], [])

    def test_a_fresh_stage_still_submits_after_the_ttl_clock_is_read(self):
        staged = self.stage()
        saved = self.intake.receive({'kind': 'image', 'file_token': staged['token']})
        self.assertTrue(saved['file_path'])
        self.assertEqual([path.name for path in self.intake.media_root.glob('.stage-*')], [])


class KnowledgeProvenanceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.knowledge = Knowledge(Store(self.tmp.name), sources=[])

    def tearDown(self):
        self.tmp.cleanup()

    def test_add_note_without_provenance_is_unchanged(self):
        before = self.knowledge.add_note({'title': '标题', 'body': '正文'})
        self.assertEqual(before['provenance'], {})
        self.assertEqual(before['source'], 'manual')
        self.assertEqual(before['kind'], 'notes')
        self.assertEqual(before['url'], '')

    def test_add_note_default_still_trims_and_verbatim_keeps_the_text(self):
        trimmed = self.knowledge.add_note({'title': '标题', 'body': '  正文  \n'})
        self.assertEqual(trimmed['body'], '正文')
        kept = self.knowledge.add_note({'title': '标题', 'body': '  正文  \n'}, verbatim=True)
        self.assertEqual(kept['body'], '  正文  \n')
        with self.assertRaises(ValueError):
            self.knowledge.add_note({'title': '标题', 'body': '   \n  '}, verbatim=True)
        with self.assertRaises(ValueError):
            self.knowledge.add_note({'title': '标题', 'body': '文' * 100001}, verbatim=True)

    def test_add_note_provenance_does_not_touch_the_fingerprint(self):
        from knowledge_store import identifier
        doc_id = identifier('manual', 'same-key')
        stamp = '2026-01-01T00:00:00+00:00'
        with self.knowledge.db() as db:
            db.execute('INSERT OR IGNORE INTO sources(id,config,status) VALUES(?,?,?)',
                       ('manual', json.dumps({'id': 'manual', 'name': '手动收集', 'kind': 'manual'}), 'ready'))
            db.execute('CREATE TEMP TABLE seen(id TEXT PRIMARY KEY)')
            self.knowledge._write(db, 'manual', 'same-key', 'notes', '同一份', '同一正文', '', stamp, [], {})
        plain = self.knowledge.document(doc_id)
        with self.knowledge.db() as db:
            db.execute('CREATE TEMP TABLE seen(id TEXT PRIMARY KEY)')
            self.knowledge._write(db, 'manual', 'same-key', 'notes', '同一份', '同一正文', '', stamp, [],
                                  {'drop_id': 'b' * 32})
        marked = self.knowledge.document(doc_id)
        self.assertEqual(plain['hash'], marked['hash'])
        self.assertEqual(marked['provenance'], {'drop_id': 'b' * 32})

    def test_add_reference_merges_extra_provenance_without_changing_validation(self):
        plain = self.knowledge.add_reference({'title': '收藏', 'url': 'https://example.com/keep'})
        self.assertEqual(plain['provenance']['intake_channel'], '工作台收藏登记')
        marked = self.knowledge.add_reference({'title': '回收', 'url': 'https://example.com/new'},
                                              provenance={'intake_channel': '创意回收工厂', 'drop_id': 'c' * 32,
                                                          'fetched': False, 'fetched_at': '', 'content_scope': '仅链接'})
        self.assertEqual(marked['provenance']['intake_channel'], '创意回收工厂')
        self.assertEqual(marked['provenance']['drop_id'], 'c' * 32)
        self.assertEqual(marked['provenance']['canonical_url'], 'https://example.com/new')
        self.assertEqual(marked['provenance']['ownership'], 'third_party')
        with self.assertRaises(ValueError):
            self.knowledge.add_reference({'title': '越界', 'url': 'https://example.com/x'},
                                         provenance={'canonical_url': 'https://evil.test/'})
        with self.assertRaises(ValueError):
            self.knowledge.add_note({'title': '越界', 'body': '正文'}, provenance={'author': '假的'})
        with self.assertRaises(ValueError):
            self.knowledge.add_note({'title': '格式', 'body': '正文'}, provenance='不是字典')


if __name__ == '__main__':
    unittest.main()
