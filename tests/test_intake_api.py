"""创意回收站 API：投喂、解析、列表、详情与文件回读。"""
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request

import intake_store
from tests.intake_dependency_patches import guard_intake_dependencies, patch_intake_dependency
from knowledge_store import Knowledge
from server import Handler, Store, ThreadingHTTPServer


class FakeWeb:
    def __init__(self, markdown='合成正文，讲检索与复用。'):
        self.markdown = markdown
        self.calls = []

    def __call__(self):
        return self

    def fetch(self, args, job):
        self.calls.append(dict(args))
        return {'url': args['url'], 'title': '合成页面标题', 'markdown': self.markdown, 'start': 0,
                'total_chars': len(self.markdown), 'truncated': False, 'next_start': None}


class IntakeAPITests(unittest.TestCase):
    def setUp(self):
        guard_intake_dependencies(self)
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(self.tmp.name)
        self.store._knowledge = Knowledge(self.store, sources=[])
        from unittest.mock import patch
        self.media_patch=patch('intake_media_recognition.MediaRecognizer.recognize_video',return_value={'status':'no_audio','scope':'视频无音轨'})
        self.media_patch.start()
        self.addCleanup(self.media_patch.stop)
        self.fake = patch_intake_dependency(self, "WebTools", FakeWeb())
        self.srv = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.srv.store = self.store
        self.thread = threading.Thread(target=self.srv.serve_forever, daemon=True)
        self.thread.start()
        self.base = f'http://127.0.0.1:{self.srv.server_port}'
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        self.thread.join()
        intake=self.store._creative_intake
        if intake:
            for worker in list(intake.recognition_workers.values()):worker.join(timeout=5)
            self.assertFalse(intake.recognition_workers, '测试结束前须等识别线程退出')
        cards = self.store._intake_cards
        if cards:
            for worker in list(cards.analysis_workers.values()):
                worker.join(timeout=5)
            self.assertFalse(cards.analysis_workers, '测试结束前须等自动分析线程退出')
        self.tmp.cleanup()

    def api(self, path, body=None, headers=None, raw=None, method=None):
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        head = {'Origin': self.base, 'X-Article-Studio': '1'}
        if raw is None:
            head['Content-Type'] = 'application/json'
        head.update(headers or {})
        req = urllib.request.Request(self.base + path, data=data, headers=head,
                                     method=method or ('POST' if data is not None else 'GET'))
        with self.opener.open(req) as response:
            return json.load(response)

    def fail(self, path, body=None, raw=None, headers=None):
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.api(path, body, headers, raw)
        error = caught.exception
        message = json.loads(error.read().decode()).get('error', '')
        error.close()
        return error.code, message

    def png(self, size=(12, 8)):
        from PIL import Image
        buffer = io.BytesIO()
        Image.new('RGB', size, 'blue').save(buffer, format='PNG')
        return buffer.getvalue()

    def test_link_drop_resolve_and_listing(self):
        saved = self.api('/api/intake/drop', {'kind': 'link', 'url': 'https://example.com/post', 'reason': '可参考'})
        self.assertEqual(saved['status'], 'pending')
        self.assertEqual(len(self.fake.calls), 0)
        done = self.api('/api/intake/' + saved['id'] + '/resolve', {})
        self.assertEqual(done['status'], 'resolved')
        self.assertEqual(done['state'], '已存入知识库，已抓取正文，共 12 字')
        self.assertEqual(done['knowledge_url'], '/knowledge.html?document=' + done['document_id'])
        listed = self.api('/api/intake')['items']
        self.assertEqual([item['id'] for item in listed], [saved['id']])
        self.assertEqual(self.api('/api/intake/' + saved['id'])['content_scope'], '已抓取正文，共 12 字')
        document = self.store.knowledge.document(done['document_id'])
        self.assertEqual(document['source'], 'external-references')
        self.assertEqual(document['provenance']['intake_channel'], '创意回收工厂')
        hits = self.store.knowledge.search('合成正文')['results']
        self.assertIn(done['document_id'], [hit['id'] for hit in hits])

    def test_text_drop_and_reason_stay_separate_through_the_api(self):
        saved = self.api('/api/intake/drop', {'kind': 'text', 'text': '这条想法要逐字保存', 'reason': '以后要用'})
        done = self.api('/api/intake/' + saved['id'] + '/resolve', {})
        document = self.store.knowledge.document(done['document_id'])
        self.assertEqual(document['body'], '这条想法要逐字保存')
        self.assertNotIn('以后要用', document['body'])
        self.assertEqual(self.api('/api/intake/' + saved['id'])['reason'], '以后要用')

    def test_text_whitespace_survives_the_http_path(self):
        original = '  开头有两个空格\n结尾有换行和空格  \n'
        saved = self.api('/api/intake/drop', {'kind': 'text', 'text': original, 'reason': ' \t理由也原样\n第二行\t '})
        done = self.api('/api/intake/' + saved['id'] + '/resolve', {})
        self.assertEqual(self.store.knowledge.document(done['document_id'])['body'], original)
        detail = self.api('/api/intake/' + saved['id'])
        self.assertEqual(detail['text_chars'], len(original))
        self.assertEqual(detail['reason'], ' \t理由也原样\n第二行\t ')
        self.assertEqual(detail['content_scope'], f'用户原文逐字保存，共 {len(original)} 字')

    def test_file_upload_then_resolve_then_read_the_stored_file(self):
        blob = self.png()
        query = urllib.parse.urlencode({'kind': 'image', 'name': 'shot.png', 'reason': '构图'})
        saved = self.api('/api/intake/drop?' + query, raw=blob)
        self.assertEqual(saved['kind'], 'image')
        done = self.api('/api/intake/' + saved['id'] + '/resolve', {})
        self.assertEqual(done['state'], '已存入知识库；图片内容未识别')
        self.assertIn('图片内容未识别', self.store.knowledge.document(done['document_id'])['body'])
        with self.opener.open(self.base + '/api/intake/' + saved['id'] + '/file') as response:
            self.assertEqual(response.read(), blob)

    def test_bad_input_is_refused_with_a_real_reason(self):
        for payload in ({'kind': 'link', 'url': '/Users/example/Downloads/a.png'},
                        {'kind': 'link', 'url': 'http://127.0.0.1:8766/x'},
                        {'kind': 'link', 'url': 'http://192.168.4.9/doc'},
                        {'kind': 'link', 'url': 'http://user:pw@example.com/x'},
                        {'kind': 'text', 'text': ''},
                        {'kind': 'image', 'url': 'https://example.com/x'},
                        {'kind': 'link', 'url': 'https://example.com/x', 'file_path': '/etc/passwd'}):
            code, message = self.fail('/api/intake/drop', payload)
            self.assertEqual(code, 400)
            self.assertTrue(message)
        self.assertIn('本机文件路径', self.fail('/api/intake/drop', {'kind': 'link', 'url': '/tmp/a.png'})[1])
        self.assertIn('本机或局域网', self.fail('/api/intake/drop', {'kind': 'link', 'url': 'http://10.0.0.5/x'})[1])
        self.assertIn('凭据', self.fail('/api/intake/drop', {'kind': 'link', 'url': 'http://u:p@example.com/x'})[1])
        self.assertEqual(self.api('/api/intake')['items'], [])

    def test_upload_route_rejects_unknown_kind_and_oversized_body(self):
        self.assertEqual(self.fail('/api/intake/drop?kind=spreadsheet&name=a.xlsx', raw=b'x')[0], 400)
        query = urllib.parse.urlencode({'kind': 'image', 'name': 'a.png'})
        self.assertEqual(self.fail('/api/intake/drop?' + query + '&path=/etc/passwd', raw=self.png())[0], 400)
        self.assertEqual(self.fail('/api/intake/drop?kind=image&name=../x.png', raw=self.png())[0], 400)

    def test_a_selected_file_and_a_link_arrive_as_one_item(self):
        # 合同②：选文件不再单独成一条；点「收下」时链接 + 理由 + 文件同一条入库。
        blob = self.png((24, 16))
        staged = self.api('/api/intake/stage?kind=image&name=' + urllib.parse.quote('shot 01.png'), raw=blob)
        self.assertEqual(staged['kind'], 'image')
        self.assertEqual(staged['file']['name'], 'shot 01.png')
        saved = self.api('/api/intake/drop', {'id': 'c' * 32, 'kind': 'link', 'url': 'https://example.com/post',
                                              'reason': '构图可参考', 'file_token': staged['token']})
        self.assertEqual(saved['kind'], 'link')
        self.assertEqual(saved['media_kind'], 'image')
        done = self.api('/api/intake/' + saved['id'] + '/resolve', {})
        self.assertEqual(done['url'], 'https://example.com/post')
        self.assertIn('另存本机图片：shot 01.png', done['content_scope'])
        self.assertIn('图片内容未识别', done['state'])
        self.assertEqual(len(self.api('/api/intake')['items']), 1)
        with self.opener.open(self.base + '/api/intake/' + saved['id'] + '/file') as response:
            self.assertEqual(response.read(), blob)

    def test_a_file_alone_can_be_submitted_over_the_same_route(self):
        blob = self.png()
        staged = self.api('/api/intake/stage?kind=image&name=only.png', raw=blob)
        saved = self.api('/api/intake/drop', {'reason': '只要这张图', 'file_token': staged['token']})
        self.assertEqual(saved['kind'], 'image')
        self.assertEqual(saved['reason'], '只要这张图')
        done = self.api('/api/intake/' + saved['id'] + '/resolve', {})
        self.assertIn('本机图片：only.png', self.store.knowledge.document(done['document_id'])['body'])

    def test_the_stored_image_name_and_content_type_follow_the_real_format(self):
        # 回归：组合收下时图片后缀要跟着字节识别出的真实格式，不跟着回收项 kind 或用户后缀。
        blob = self.png((24, 16))
        staged = self.api('/api/intake/stage?kind=image&name=' + urllib.parse.quote('fake.jpg'), raw=blob)
        saved = self.api('/api/intake/drop', {'id': 'd' * 32, 'kind': 'link', 'url': 'https://example.com/post',
                                             'reason': '后缀骗人的 PNG', 'file_token': staged['token']})
        self.assertTrue(saved['file_path'].endswith('.png'), saved['file_path'])
        with self.opener.open(self.base + '/api/intake/' + saved['id'] + '/file') as response:
            self.assertEqual(response.headers.get_content_type(), 'image/png')
            self.assertEqual(response.read(), blob)
        bare = self.api('/api/intake/stage?kind=image&name=shot', raw=blob)
        second = self.api('/api/intake/drop', {'id': 'e' * 32, 'kind': 'text', 'text': '一段想法',
                                              'file_token': bare['token']})
        self.assertTrue(second['file_path'].endswith('.png'), second['file_path'])
        with self.opener.open(self.base + '/api/intake/' + second['id'] + '/file') as response:
            self.assertEqual(response.headers.get_content_type(), 'image/png')

    def test_the_stage_route_refuses_bad_uploads_and_forged_tokens(self):
        self.assertEqual(self.fail('/api/intake/stage?kind=spreadsheet&name=a.xlsx', raw=b'x')[0], 400)
        self.assertEqual(self.fail('/api/intake/stage?kind=image&name=../x.png', raw=self.png())[0], 400)
        self.assertEqual(self.fail('/api/intake/stage?kind=image&name=a.png&path=/etc/passwd', raw=self.png())[0], 400)
        self.assertEqual(self.fail('/api/intake/stage?kind=image&name=a.png', raw=b'')[0], 400)
        code, message = self.fail('/api/intake/drop', {'kind': 'text', 'text': '一条', 'file_token': 'f' * 32})
        self.assertEqual(code, 400)
        self.assertIn('已不在', message)
        self.assertEqual(self.fail('/api/intake/drop', {'kind': 'text', 'text': '一条', 'file_token': '../x'})[0], 400)
        self.assertEqual(self.api('/api/intake')['items'], [])

    def test_routes_validate_their_parameters_and_origin(self):
        self.assertEqual(self.fail('/api/intake?offset=1')[0], 400)
        self.assertEqual(self.fail('/api/intake?limit=0')[0], 400)
        self.assertEqual(self.fail('/api/intake/' + 'a' * 32 + '/resolve', {'extra': 1})[0], 400)
        self.assertEqual(self.fail('/api/intake/' + 'a' * 32 + '/resolve', {})[0], 400)
        self.assertEqual(self.fail('/api/intake/not-an-id')[0], 400)
        saved = self.api('/api/intake/drop', {'kind': 'text', 'text': '一条'})
        self.assertEqual(self.fail('/api/intake/drop', {'kind': 'text', 'text': 'x'},
                                   headers={'Origin': 'https://elsewhere.test'})[0], 403)
        self.assertEqual(self.fail('/api/intake/drop', {'kind': 'text', 'text': 'x'},
                                   headers={'X-Article-Studio': '0'})[0], 403)
        self.assertEqual(len(self.api('/api/intake')['items']), 1)
        self.assertEqual(saved['status'], 'pending')

    def test_page_is_served_with_the_shared_navigation(self):
        with self.opener.open(self.base + '/intake.html') as response:
            page = response.read().decode()
        self.assertIn('◈ 创意回收', page)
        self.assertIn('panel-layout.js', page)
        self.assertIn('我为什么收它', page)
        for phrase in ('智能分析', '自动学习', '一键变作品'):
            self.assertNotIn(phrase, page)
        for name in ('index.html', 'knowledge.html', 'clues.html', 'events.html', 'feedback.html',
                     'schedules.html', 'deals.html', 'lab.html'):
            with self.opener.open(self.base + '/' + name) as response:
                self.assertIn('/intake.html', response.read().decode(), name)


if __name__ == '__main__':
    unittest.main()
