"""Upstream HTTP error bodies must never become user-visible text (diag-media-502 建议 A/C/D).

The 2026-10-06 incident showed the whole nginx 502 page inside a job card: the media
transport used to fall back to `raw.decode()[:200]` whenever the error body was not JSON.
These tests pin the replacement contract — a short Chinese line carrying the status code
and an actionable hint, the media API's own structured `error` field passed through, and
nothing else from the upstream body (no HTML, no server words, no paths, no stack).
They also pin 建议 B: the anti-resubmit sentence at the end of `connection_warning`
survives any truncation. Only the workbench's own background seam is controlled; no real
media service is contacted and no job is ever submitted.
"""
import email.message
import io
import json
import logging
import os
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from server import Store
from generation import media_http
from generation.media_http import MediaApi, MediaApiError, MediaApiUnknown
from generation.service import Generation

# the exact body the relay's nginx produced on 2026-10-06 15:39:44
NGINX_502 = (b'<html>\n<head><title>502 Bad Gateway</title></head>\n<body>\n'
             b'<center><h1>502 Bad Gateway</h1></center>\n<hr><center>nginx</center>\n'
             b'</body>\n</html>\n')
LEAK_MARKERS = ('<html', '<!doctype', 'nginx', 'Bad Gateway', '<center>', '<body>', '<hr>',
                'Traceback', 'File "/usr/bin/python3', 'Internal Server Error')


class FailingOpener:
    """Stand-in transport answering exactly like the relay: one HTTPError carrying a body."""

    def __init__(self, code, body, content_type='text/html'):
        self.code, self.body, self.content_type = code, body, content_type
        self.requests = []

    def open(self, request, timeout=None):
        self.requests.append(request.full_url)
        headers = email.message.Message()
        headers['Content-Type'] = self.content_type
        headers['Content-Length'] = str(len(self.body))
        raise urllib.error.HTTPError(request.full_url, self.code, 'upstream status', headers,
                                     io.BytesIO(self.body))


def json_body(payload):
    return json.dumps(payload, ensure_ascii=False).encode()


class TransportErrorTextTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        # the expected upstream failures log a bounded internal line; keep the suite quiet
        # (assertLogs attaches its own handler, so it still sees them)
        self._log_level = media_http._LOG.level
        media_http._LOG.setLevel(logging.CRITICAL)
        key = Path(self.temp.name) / 'media-api.key'
        key.write_text('test-token')
        os.chmod(key, 0o600)
        self.key_file = str(key)
        self.source = Path(self.temp.name) / '参考图.png'
        self.source.write_bytes(b'\x89PNG\r\n\x1a\n' + b'x' * 64)

    def tearDown(self):
        media_http._LOG.setLevel(self._log_level)
        self.temp.cleanup()

    def api(self, code, body, content_type='text/html'):
        opener = FailingOpener(code, body, content_type)
        return MediaApi(base='http://127.0.0.1:9', key_file=self.key_file, timeout=5,
                        opener=opener), opener

    def probe(self, code, body, content_type='text/html'):
        """Call the read path and return the raised exception's user-visible text."""
        api, opener = self.api(code, body, content_type)
        with self.assertRaises(MediaApiError) as caught:
            api.status()
        return str(caught.exception), caught.exception, opener

    # ---------- 建议 A: the main submit/poll path ----------
    def test_html_502_body_never_reaches_the_user(self):
        message, exc, opener = self.probe(502, NGINX_502)
        self.assertIsInstance(exc, MediaApiUnknown)
        self.assertEqual(exc.state, 'unknown')
        self.assertIn('HTTP 502', message)
        self.assertIn('不会自动重复提交', message)
        self.assertIn('重新核实', message)          # 建议 D: tell the user what to do next
        self.assertIn('维护或重启', message)
        for marker in LEAK_MARKERS:
            self.assertNotIn(marker, message, message)
        self.assertLess(len(message), 200, message)
        self.assertEqual(opener.requests, ['http://127.0.0.1:9/status'])

    def test_empty_body_502_stays_a_readable_line(self):
        message, exc, _ = self.probe(502, b'')
        self.assertIsInstance(exc, MediaApiUnknown)
        self.assertIn('HTTP 502', message)
        self.assertIn('不会自动重复提交', message)
        self.assertNotIn('None', message)
        self.assertFalse(message.rstrip().endswith('：'), message)
        self.assertNotIn('：\n', message)

    def test_oversized_html_body_is_not_leaked_and_stays_bounded(self):
        # a proxy page padded past the client's read cap, with a marker inside the read
        # window and another one far beyond it: neither may surface
        body = NGINX_502 + b'INSIDE-THE-READ-WINDOW ' + b'p' * 3000 + b'BEYOND-THE-READ-CAP' + b'q' * 200_000
        message, exc, _ = self.probe(503, body)
        self.assertIsInstance(exc, MediaApiUnknown)
        self.assertIn('HTTP 503', message)
        for marker in LEAK_MARKERS + ('INSIDE-THE-READ-WINDOW', 'BEYOND-THE-READ-CAP', 'pppp'):
            self.assertNotIn(marker, message, message)
        self.assertLess(len(message), 200, message)

    def test_truncated_json_body_fails_closed(self):
        # the body was cut mid-document: guessing is worse than saying nothing
        message, exc, _ = self.probe(504, ('{"error": "参数超出范围').encode())
        self.assertIsInstance(exc, MediaApiUnknown)
        self.assertIn('HTTP 504', message)
        self.assertNotIn('参数超出范围', message)

    def test_structured_error_field_from_the_media_api_is_passed_through(self):
        body = json_body({'error': '提示词包含未允许的内容'})
        message, exc, _ = self.probe(502, body, 'application/json')
        self.assertIsInstance(exc, MediaApiUnknown)
        self.assertIn('提示词包含未允许的内容', message)
        self.assertIn('HTTP 502', message)
        # a long business message is still bounded
        long_body = json_body({'error': '很长的问题说明' * 200})
        message, _, _ = self.probe(502, long_body, 'application/json')
        self.assertLess(len(message), 400, message)

    def test_structured_error_over_cap_is_dropped_even_within_read_window(self):
        # A valid-looking but >200-char error, well within the 4000-byte read window, must
        # still be dropped by the length cap. Removing the cap would let this text reach the
        # user, so this test turns red if the limit is lifted (L1). Synthetic text only.
        error = '图像尺寸或比例超出允许范围请缩小后重试或更换模型' * 12   # ~ 270 chars, ~810 bytes
        self.assertGreater(len(error), 200)
        message, exc, _ = self.probe(502, json_body({'error': error}), 'application/json')
        self.assertIsInstance(exc, MediaApiUnknown)
        self.assertNotIn('图像尺寸或比例超出允许范围请缩小后重试或更换模型', message, message)
        self.assertLess(len(message), 200, message)

    def test_markup_or_server_words_inside_a_json_error_field_are_refused(self):
        for payload in ({'error': '<html><body>nginx crashed</body></html>'},
                        {'error': 'Traceback (most recent call last): File "/usr/bin/python3"'},
                        {'error': '502 Bad Gateway from upstream'},
                        {'error': {'message': '上游 nginx 返回 Bad Gateway'}},
                        {'error': ['nginx']}, {'errors': 'nginx'}, {'error': '   '}):
            with self.subTest(payload=payload):
                message, _, _ = self.probe(502, json_body(payload), 'application/json')
                for marker in ('nginx', '<html', 'Traceback', 'Bad Gateway'):
                    self.assertNotIn(marker, message, message)

    def test_structured_error_carrying_paths_proxies_or_stack_is_refused(self):
        # The three synthetic inputs from review-media-error-text-report (M1). They are
        # accepted JSON, but each carries an internal artefact (filesystem path, proxy
        # IP:port, stack frame) that must never reach the user. These are synthetic test
        # data only — not real environment information.
        cases = (
            'FileNotFoundError: /srv/media-api/private/config.json',
            'upstream connect error: envoy at 10.0.0.5:8200',
            'uvicorn RuntimeError: stack at /opt/media/main.py:42',
        )
        for error in cases:
            with self.subTest(error=error):
                message, exc, _ = self.probe(502, json_body({'error': error}), 'application/json')
                self.assertIsInstance(exc, MediaApiUnknown)
                self.assertIn('HTTP 502', message)
                self.assertNotIn(error, message, message)
                for marker in ('/srv/media-api', '/opt/media', '10.0.0.5', 'FileNotFoundError',
                               'envoy', 'uvicorn', 'config.json', 'main.py'):
                    self.assertNotIn(marker, message, message)

    def test_status_code_classification_and_uncertain_semantics_are_preserved(self):
        for code in (408, 429, 500, 502, 503, 504):
            with self.subTest(code=code):
                message, exc, _ = self.probe(code, NGINX_502)
                self.assertIsInstance(exc, MediaApiUnknown)
                self.assertEqual(exc.state, 'unknown')
                self.assertIn(f'HTTP {code}', message)
                self.assertIn('不会自动重复提交', message)
        for code in (400, 401, 404, 409):
            with self.subTest(code=code):
                message, exc, _ = self.probe(code, NGINX_502)
                self.assertNotIsInstance(exc, MediaApiUnknown)
                self.assertIn(f'HTTP {code}', message)
                for marker in LEAK_MARKERS:
                    self.assertNotIn(marker, message, message)

    def test_internal_log_keeps_status_and_size_but_never_the_body(self):
        api, _ = self.api(502, NGINX_502)
        with self.assertLogs('generation.media_http', level=logging.WARNING) as logs:
            with self.assertRaises(MediaApiUnknown):
                api.status()
        joined = '\n'.join(logs.output)
        self.assertIn('502', joined)
        self.assertIn(str(len(NGINX_502)), joined)
        for marker in LEAK_MARKERS:
            self.assertNotIn(marker, joined, joined)

    # ---------- 建议 C: the reference upload path gets the same treatment ----------
    def test_upload_html_502_never_reaches_the_user(self):
        api, _ = self.api(502, NGINX_502)
        with self.assertRaises(MediaApiUnknown) as caught:
            api.upload(self.source, 'image', '参考图.png')
        message = str(caught.exception)
        self.assertIn('HTTP 502', message)
        self.assertIn('素材上传失败', message)
        self.assertIn('生成任务尚未提交', message)
        for marker in LEAK_MARKERS:
            self.assertNotIn(marker, message, message)
        self.assertLess(len(message), 200, message)

    def test_upload_oversized_and_empty_bodies_are_bounded(self):
        for body in (b'', NGINX_502 * 4000):
            with self.subTest(size=len(body)):
                api, _ = self.api(504, body)
                with self.assertRaises(MediaApiUnknown) as caught:
                    api.upload(self.source, 'image', '参考图.png')
                message = str(caught.exception)
                self.assertIn('HTTP 504', message)
                for marker in LEAK_MARKERS:
                    self.assertNotIn(marker, message, message)

    def test_upload_rejection_passes_its_own_message_and_refuses_html(self):
        api, _ = self.api(400, json_body({'error': 'sha256 校验不一致'}), 'application/json')
        with self.assertRaises(MediaApiError) as caught:
            api.upload(self.source, 'image', '参考图.png')
        self.assertNotIsInstance(caught.exception, MediaApiUnknown)
        self.assertIn('sha256 校验不一致', str(caught.exception))

        api, _ = self.api(400, NGINX_502)
        with self.assertRaises(MediaApiError) as caught:
            api.upload(self.source, 'image', '参考图.png')
        message = str(caught.exception)
        self.assertIn('HTTP 400', message)
        for marker in LEAK_MARKERS:
            self.assertNotIn(marker, message, message)

    def test_upload_internal_log_omits_the_body(self):
        api, _ = self.api(500, NGINX_502)
        with self.assertLogs('generation.media_http', level=logging.WARNING) as logs:
            with self.assertRaises(MediaApiUnknown):
                api.upload(self.source, 'image', '参考图.png')
        joined = '\n'.join(logs.output)
        self.assertIn('/files', joined)
        self.assertIn('500', joined)
        for marker in LEAK_MARKERS:
            self.assertNotIn(marker, joined, joined)

    # ---------- 建议 B: the anti-resubmit tail survives any truncation ----------
    def uncertain_warning_for(self, detail):
        """Run one job whose transport fails with `detail`; return its connection_warning."""
        with tempfile.TemporaryDirectory() as folder:
            key = Path(folder) / 'media-api.key'
            key.write_text('test-token')
            os.chmod(key, 0o600)
            with patch.dict(os.environ, {'MEDIA_API_BASE': 'http://127.0.0.1:9',
                                         'MEDIA_API_KEY_FILE': str(key),
                                         'MEDIA_API_CONFIG': str(Path(folder) / 'none.json')}):
                store = Store(folder)
                generation = Generation(store)
                # Isolate the background seam BEFORE start() spawns the run thread, so the
                # exploding submit is in place on the very first run (no start()-then-patch race).
                def exploding_submit(_self, _job_id, _api):
                    raise MediaApiUnknown(detail, state='unknown')

                with patch.object(Generation, '_spawn', lambda *_: None), \
                        patch.object(Generation, 'submit_http', exploding_submit):
                    job = generation.start({'model': 'qi21', 'prompt': '湖面晨雾',
                                            'size': '1024x1024', 'seed': 7}, origin='lab')
                    generation.run(job['id'], None)
                result = generation.public(job['id'])
                generation.close()
        self.assertEqual(result['status'], 'uncertain')
        return result['connection_warning']

    def test_uncertain_warning_keeps_the_anti_resubmit_tail_whatever_the_detail_is(self):
        tail = '；服务器任务可能仍在运行，请勿重复提交'
        long_detail = ('媒体服务暂时不可用（HTTP 502），媒体服务可能正在维护或重启，请稍后再试；'
                       '已提交的任务不会自动重复提交，可点“重新核实”按原任务编号查询 ') + ('详情' * 4000)
        warning = self.uncertain_warning_for(long_detail)
        self.assertTrue(warning.endswith(tail), warning[-80:])
        self.assertLessEqual(len(warning), 600 + len(tail) + 1)
        self.assertIn('HTTP 502', warning)
        # the cut lands on a token boundary, so the visible part stays a readable sentence
        self.assertIn('…；服务器任务可能仍在运行，请勿重复提交', warning)

    def test_truncated_uncertain_warning_never_leaves_half_a_tag_or_the_body(self):
        # a transport detail long enough that the 600-char budget cuts inside a tag:
        # the tail must stay intact and no dangling markup may reach the card
        tail = '；服务器任务可能仍在运行，请勿重复提交'
        prefix = '媒体服务暂时不可用（HTTP 502），请稍后再试 '
        detail = (prefix + 'x' * (590 - len(prefix))
                  + '<html><body>nginx Bad Gateway</body></html>' + 'y' * 500)
        warning = self.uncertain_warning_for(detail)
        self.assertTrue(warning.endswith(tail), warning[-80:])
        self.assertLessEqual(len(warning), 600 + len(tail) + 1)
        self.assertNotIn('<', warning, warning[-120:])
        for marker in ('<html', 'nginx', 'Bad Gateway', '</body>'):
            self.assertNotIn(marker, warning)

    def test_uncertain_warning_without_space_before_tag_never_leaves_half_a_tag(self):
        # M2: when no whitespace precedes the tag, the word-boundary cut would otherwise
        # leave a dangling "<div". The anti-resubmit tail must stay intact and no "<" may
        # reach the card.
        tail = '；服务器任务可能仍在运行，请勿重复提交'
        detail = ('a' * 590 + '<div class="' + 'x' * 800)
        warning = self.uncertain_warning_for(detail)
        self.assertTrue(warning.endswith(tail), warning[-80:])
        self.assertLessEqual(len(warning), 600 + len(tail) + 1)
        self.assertNotIn('<', warning, warning)
        self.assertNotIn('div', warning)


class FakeMediaApi:
    """Stand-in media client: answers one job poll with a fixed server payload.

    Only the workbench's own client seam is replaced; no socket is opened and no job is ever
    submitted to a real media service.
    """

    def __init__(self, remote):
        self.remote = remote

    def job(self, api_job_id):
        return dict(self.remote)

    def events(self, *args, **kwargs):
        return None

    def find_by_request(self, key):
        return None


class UpstreamTextSyntaxTests(unittest.TestCase):
    """review-media-error-text-2 M1/L1: stack shapes refused, ordinary copy kept.

    The filter must recognise a stack/traceback by its *syntax*: the previous substring `' at '`
    was defeated by a frame that starts the string (whitespace collapse removes the newlines)
    and broke on `at least`. A `/` must count as a path only in path shape, so a fraction such
    as `1/2 张` stays readable. Synthetic strings only; no media service is contacted.
    """
    STACK_SHAPES = (
        'at com.example.PrivateEngine.run(PrivateEngine.java:42)',   # java frame at a line start
        'at loadModel (engine.js:1:2)',                              # js frame
        'File "engine.py", line 42, in run',                        # python frame
        "File 'engine.py', line 42",                                # python frame, single quotes
        'Traceback (most recent call last): RuntimeError: engine crashed',
        'Caused by: java.lang.IllegalStateException: engine offline',
        'com.example.PrivateEngineException: engine offline',       # package-qualified exception
        'at main.go:11',                                            # native/go frame
        'envoy: connection reset by peer',                          # proxy wording
    )
    SAFE_COPY = (
        '最多允许 1/2 张参考图',           # a fraction, not a path
        'model.safetensors 不存在，请选择已安装模型',  # CJK-dominant business copy
        '模型要求客户端版本 v1.2.3.4',          # a dotted version, not an IP
        '额度将在 2026-10-12 恢复，请稍后再试',   # a date, not a path
        '输出比例 16/9 或 3/4 不支持，请改用 1:1',  # ratios
        '提示词包含未允许的内容',
        '任务于 2026-10-06 之后超时，请稍后重新核实',
        '请修改以下选项：\n    图片尺寸不超过 2048\n    参考图最多两张',  # indented Chinese hint
    )
    PATH_SHAPES = ('/srv/private/config.json', 'FileNotFoundError: /srv/private/config.json',
                   'C:\\x\\y', 'D:/media/private/engine.py')

    def test_stack_syntax_is_refused_by_shape_not_by_the_substring_at(self):
        for text in self.STACK_SHAPES:
            with self.subTest(text=text):
                self.assertEqual(media_http.safe_upstream_text(text), '')

    def test_ordinary_copy_with_a_slash_or_at_is_kept(self):
        for text in self.SAFE_COPY:
            with self.subTest(text=text):
                self.assertEqual(media_http.safe_upstream_text(text), ' '.join(text.split()))

    def test_path_shapes_are_still_refused(self):
        for text in self.PATH_SHAPES:
            with self.subTest(text=text):
                self.assertEqual(media_http.safe_upstream_text(text), '')

    def test_out_of_memory_is_rewritten_to_our_own_chinese_line(self):
        # review-media-error-text-d L1: only an explicitly GPU/CUDA out-of-memory statement is
        # named in our own words.
        for text in ('RuntimeError: CUDA out of memory',
                     'torch.cuda.OutOfMemoryError: CUDA out of memory. Tried to allocate 20GiB',
                     'CUDA error: out of memory', '显存不足，请降低尺寸'):
            with self.subTest(text=text):
                self.assertEqual(media_http.safe_upstream_text(text),
                                 '显卡显存不足，请稍后重试或降低尺寸')

    def test_bare_vram_or_negated_oom_is_never_diagnosed_as_the_gpu(self):
        # review-media-error-text-d L1 + e L2: `VRAM` alone is a resource name, `out of memory`
        # does not say whose memory ran out, a negation (also the `OOM` abbreviation) and
        # preventive advice must not become a diagnosis. None of these may turn into
        # 「显卡显存不足」; Chinese copy is passed through, English copy is only ever replaced by
        # our own wording or dropped (review-media-error-text-e rule 2).
        for text in ('This model requires 16 GB VRAM; choose a compatible model',
                     '当前模型不支持 low VRAM 模式，请关闭该选项',
                     'This is not an out of memory error; invalid image format',
                     'CPU allocator out of memory while reading input',
                     'OOM while loading the checkpoint',
                     # an explicit CUDA phrase inside a negation is still not an OOM diagnosis
                     'This is not a CUDA out of memory error; the image format is invalid',
                     'This is not a CUDA OOM error; invalid image format',
                     'To avoid CUDA out of memory, please choose a smaller image',
                     '为避免显存不足，请降低输出尺寸',
                     '这不是显存不足，是图片格式错误'):
            with self.subTest(text=text):
                result = media_http.safe_upstream_text(text)
                self.assertNotEqual(result, '显卡显存不足，请稍后重试或降低尺寸')
                if media_http._cjk_dominant(text):
                    self.assertEqual(result, ' '.join(text.split()))
                else:
                    self.assertNotEqual(result, ' '.join(text.split()))
                    self.assertNotIn('memory', result)


class CodeShapeTests(unittest.TestCase):
    """review-media-error-text-d M1: anything that looks like code is refused, whatever runtime.

    The previous rounds added one frame rule per language and kept losing the next runtime (the
    review's Go `goroutine 1 [running]:` pair passed straight through all four exits). This gate
    asks only about shape: a source file with a line number, a hex offset, a goroutine header,
    `panic:`, a Go runtime state, a dotted qualified call, a package-qualified Exception/Error,
    or two lines indented by a tab / 4+ spaces. Synthetic strings only; no media service is
    contacted.
    """
    CODE_SHAPES = (
        # the two Go inputs review-media-error-text-3 reported as leaking
        'panic: runtime error: index out of range [1] with length 1\n\ngoroutine 1 [running]:',
        'goroutine 1 [running]:\nmain.privateGenerate()\n\tengine.go:42 +0x123',
        'goroutine 27 [chan receive]:\nnetwork.run()',
        'runtime error: invalid memory address\n[select]: main.wait()',
        # source file + line number, per language, without an `at ` prefix
        'engine.go:42 +0x123', 'main.py:11', 'PrivateEngine.java:42', 'engine.js:42:7',
        'main.rs:88', 'Handler.kt:9', 'App.swift:31', 'main.cpp:7',
        # hex offsets and pointers
        'fault at +0x7f3c', '0x7ffd5c3e',
        # dotted qualified calls and package-qualified exceptions
        'main.privateGenerate()', 'com.example.Engine.run()',
        'io.netty.channel.stack.Error: offline',
        # traceback bodies in other shapes: two indented lines decide it, no keyword needed
        'Generation failed\n\texecute()\n\tload()',
        'Generation failed\n    step one\n    step two',
    )
    READABLE_COPY = (
        '最多允许 1/2 张参考图',
        '提示词包含未允许的内容',
        '参考图尺寸超过上限，请缩小到 2048x2048 以内',
        '模型 qwen-image-2.1 未加载，请稍后重试',      # a dotted model id, not a qualified call
        'size 1024x1024 (portrait) 不支持',
        '任务于 2026-10-06 之后超时，请稍后重新核实',
        '第一段缩进正常\n    这一行缩进了，只有一行',   # one indented line is not a traceback
    )

    def test_code_shapes_are_refused(self):
        for text in self.CODE_SHAPES:
            with self.subTest(text=text):
                self.assertEqual(media_http.safe_upstream_text(text), '')

    def test_readable_copy_survives_the_code_shape_gate(self):
        # the gate must not cost the user the clue that fixes their own input
        for text in self.READABLE_COPY:
            with self.subTest(text=text):
                self.assertEqual(media_http.safe_upstream_text(text),
                                 ' '.join(text.split()))


class StackLeakAcrossAllExitsTests(unittest.TestCase):
    """One stack line, refused on all four exits that can carry upstream text.

    review-media-error-text-2 M1 probed `at com.example.PrivateEngine.run(...)` through HTTP,
    SSE poll_error, JSON failed.error and JSON uncertain_reason. All four are pinned here.
    """
    STACK = 'at com.example.PrivateEngine.run(PrivateEngine.java:42)'
    MARKERS = ('PrivateEngine', 'com.example', 'PrivateEngine.java', '.java:42', 'at com')

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self._log_level = media_http._LOG.level
        media_http._LOG.setLevel(logging.CRITICAL)
        key = Path(self.temp.name) / 'media-api.key'
        key.write_text('test-token')
        os.chmod(key, 0o600)
        self.env = {'MEDIA_API_BASE': 'http://127.0.0.1:9', 'MEDIA_API_KEY_FILE': str(key),
                    'MEDIA_API_CONFIG': str(Path(self.temp.name) / 'none.json')}

    def tearDown(self):
        media_http._LOG.setLevel(self._log_level)
        self.temp.cleanup()

    def assertNoStack(self, text):
        for marker in self.MARKERS:
            self.assertNotIn(marker, text, text)

    def new_generation(self):
        store = Store(self.temp.name)
        generation = Generation(store)
        generation._media = FakeMediaApi({})     # media() returns the stub: no network
        return generation

    def start_job(self, generation):
        job = generation.start({'model': 'qi21', 'prompt': '湖面晨雾', 'size': '1024x1024',
                                'seed': 7}, origin='lab')
        return job['id']

    def test_http_exit_refuses_the_java_stack_line(self):
        opener = FailingOpener(502, json_body({'error': self.STACK}), 'application/json')
        api = MediaApi(base='http://127.0.0.1:9', key_file=self.env['MEDIA_API_KEY_FILE'],
                       timeout=5, opener=opener)
        with self.assertRaises(MediaApiError) as caught:
            api.status()
        message = str(caught.exception)
        self.assertIn('HTTP 502', message)
        self.assertNoStack(message)

    def test_sse_poll_error_exit_refuses_the_java_stack_line(self):
        with patch.dict(os.environ, self.env), patch.object(Generation, '_spawn', lambda *_: None):
            generation = self.new_generation()
            job_id = self.start_job(generation)
            generation.apply_event(job_id, {'type': 'poll_error', 'data': {'error': self.STACK}})
            result = generation.public(job_id)
            generation.close()
        warning = result['connection_warning']
        self.assertIn('服务器进度查询暂时异常：', warning)
        self.assertIn('详情未返回可显示说明', warning)   # fail closed, no stack attached
        self.assertNoStack(warning)

    def polled_job(self, remote):
        """One job whose server poll answers *remote*; returns the stored public job."""
        with patch.dict(os.environ, self.env), \
                patch.object(Generation, '_spawn', lambda *_: None), \
                patch.object(Generation, '_spawn_observer', lambda *_: None):
            generation = self.new_generation()
            generation._media.remote = remote
            job_id = self.start_job(generation)
            generation.update(job_id, api_job_id='api-1', submit_state='confirmed',
                              status='queued')
            generation.run(job_id, None)
            result = generation.public(job_id)
            generation.close()
        return result

    def test_json_failed_error_exit_refuses_the_java_stack_line(self):
        result = self.polled_job({'status': 'failed', 'error': self.STACK})
        self.assertEqual(result['status'], 'error')
        self.assertNoStack(str(result['error']))
        self.assertIn('生成失败', str(result['error']))

    def test_json_uncertain_reason_exit_refuses_the_java_stack_line(self):
        result = self.polled_job({'status': 'uncertain', 'uncertain_reason': self.STACK})
        self.assertEqual(result['status'], 'uncertain')
        warning = result['connection_warning']
        self.assertIn('服务器任务状态无法确定：', warning)
        self.assertIn('服务器未返回可显示的状态说明', warning)
        self.assertNoStack(warning)

    def test_business_copy_survives_the_http_exit_unchanged(self):
        # review-media-error-text-e: Chinese business copy still reaches the user; an English
        # condition is replaced by our own Chinese wording instead of the upstream sentence.
        for error, expected in (('最多允许 1/2 张参考图', '最多允许 1/2 张参考图'),
                                ('Please provide at least one image', '请至少提供一张参考图')):
            with self.subTest(error=error):
                opener = FailingOpener(400, json_body({'error': error}), 'application/json')
                api = MediaApi(base='http://127.0.0.1:9', key_file=self.env['MEDIA_API_KEY_FILE'],
                               timeout=5, opener=opener)
                with self.assertRaises(MediaApiError) as caught:
                    api.status()
                message = str(caught.exception)
                self.assertIn(expected, message, message)
                self.assertIn('HTTP 400', message)
        self.assertNotIn('at least one image',
                         self.http_business('Please provide at least one image'))

    def http_business(self, error):
        opener = FailingOpener(400, json_body({'error': error}), 'application/json')
        api = MediaApi(base='http://127.0.0.1:9', key_file=self.env['MEDIA_API_KEY_FILE'],
                       timeout=5, opener=opener)
        with self.assertRaises(MediaApiError) as caught:
            api.status()
        return str(caught.exception)


class FourExitHarness(unittest.TestCase):
    """Shared plumbing for the four exits that can carry an upstream error string.

    HTTP (MediaApi.status), SSE poll_error, JSON failed.error and JSON uncertain_reason all run
    through `safe_upstream_text`; a filter gap is one defect but reaches the user four times, so
    every artefact is pinned on all four. Temporary Store, stub media client, no socket, no job
    ever submitted to a real media service.
    """

    def fresh_store(self):
        """One isolated Store per case: an unfinished job in a shared Store would block `start`."""
        holder = tempfile.TemporaryDirectory(dir=self.temp.name)
        self._cases.append(holder)
        return Store(holder.name)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self._cases = []
        self._log_level = media_http._LOG.level
        media_http._LOG.setLevel(logging.CRITICAL)
        key = Path(self.temp.name) / 'media-api.key'
        key.write_text('test-token')
        os.chmod(key, 0o600)
        self.env = {'MEDIA_API_BASE': 'http://127.0.0.1:9', 'MEDIA_API_KEY_FILE': str(key),
                    'MEDIA_API_CONFIG': str(Path(self.temp.name) / 'none.json')}

    def tearDown(self):
        media_http._LOG.setLevel(self._log_level)
        for holder in self._cases:
            holder.cleanup()
        self.temp.cleanup()

    def http_exit(self, text, code=502):
        opener = FailingOpener(code, json_body({'error': text}), 'application/json')
        api = MediaApi(base='http://127.0.0.1:9', key_file=self.env['MEDIA_API_KEY_FILE'],
                       timeout=5, opener=opener)
        try:
            api.status()
        except MediaApiError as caught:
            return str(caught)
        self.fail('the failing media answer did not raise')

    def sse_exit(self, text):
        with patch.dict(os.environ, self.env), patch.object(Generation, '_spawn', lambda *_: None):
            generation = Generation(self.fresh_store())
            generation._media = FakeMediaApi({})
            job_id = generation.start({'model': 'qi21', 'prompt': '湖面晨雾', 'size': '1024x1024',
                                       'seed': 7}, origin='lab')['id']
            generation.apply_event(job_id, {'type': 'poll_error', 'data': {'error': text}})
            result = generation.public(job_id)
            generation.close()
        return result['connection_warning']

    def polled_job(self, remote):
        with patch.dict(os.environ, self.env), \
                patch.object(Generation, '_spawn', lambda *_: None), \
                patch.object(Generation, '_spawn_observer', lambda *_: None):
            generation = Generation(self.fresh_store())
            generation._media = FakeMediaApi(remote)
            job_id = generation.start({'model': 'qi21', 'prompt': '湖面晨雾', 'size': '1024x1024',
                                       'seed': 7}, origin='lab')['id']
            generation.update(job_id, api_job_id='api-1', submit_state='confirmed',
                              status='queued')
            generation.run(job_id, None)
            result = generation.public(job_id)
            generation.close()
        return result

    def failed_exit(self, text):
        result = self.polled_job({'status': 'failed', 'error': text})
        self.assertEqual(result['status'], 'error')
        return str(result['error'])

    def uncertain_exit(self, text):
        result = self.polled_job({'status': 'uncertain', 'uncertain_reason': text})
        self.assertEqual(result['status'], 'uncertain')
        return result['connection_warning']

    def exits(self, text):
        return (self.http_exit(text), self.sse_exit(text),
                self.failed_exit(text), self.uncertain_exit(text))


class UpstreamArtefactAcrossAllExitsTests(FourExitHarness):
    """review-media-error-text-d: the review-3 Go leaks and its 20 probes, pinned on four exits.

    REFUSED: an upstream artefact (stack, code shape, path, proxy wording) never reaches the
    user — the workbench's own Chinese line is shown instead.
    REWRITTEN: an explicit GPU/CUDA out-of-memory answer becomes only our fixed Chinese line.
    KEPT: readable business copy (a fraction, an ordinary sentence, a VRAM *requirement*, a
    negated OOM, a CPU OOM) still reaches the user unchanged and is never re-diagnosed.
    """
    REFUSED = (
        # the two Go inputs review-media-error-text-3 found leaking through all four exits
        ('panic: runtime error: index out of range [1] with length 1\n\ngoroutine 1 [running]:',
         ('goroutine 1', 'panic:', 'index out of range')),
        ('goroutine 1 [running]:\nmain.privateGenerate()\n\tengine.go:42 +0x123',
         ('goroutine 1', 'privateGenerate', 'engine.go', '+0x123')),
        ('at com.example.PrivateEngine.run(PrivateEngine.java:42)',
         ('PrivateEngine', 'com.example', '.java:42', 'at com')),
        ('envoy: connection reset by peer', ('envoy', 'connection reset')),
        ('/srv/private/config.json', ('srv', 'config.json')),
        ('C:\\private\\config.json', ('private', 'config.json')),
        ('/srv/media-api', ('srv', 'media-api')),
        ('envoy at 10.0.0.5:8200 upstream connect error', ('envoy', '10.0.0.5', 'upstream')),
        ('uvicorn 0.29\n  File "engine.py", line 42, in run', ('uvicorn', 'engine.py', 'line 42')),
        ('Generation failed\n  File "engine.py", line 42, in run\n    execute()',
         ('engine.py', 'line 42', 'execute()')),
        ('    at Object.<anonymous> (engine.js:42:7)', ('anonymous', 'engine.js', '42:7')),
        ('    at loadModel (C:\\private\\engine.js:42:7)', ('loadModel', 'engine.js', 'private')),
        ('main.rs:88 +0x4f', ('main.rs', '+0x4f')),
        ('kotlin.LazyKt.whenMappings(Lazy.kt:12)', ('LazyKt', 'Lazy.kt')),
        ('0x7ffd5c3e while reading the request', ('0x7ffd5c3e',)),
        ('com.example.PrivateEngineException: engine offline',
         ('PrivateEngineException', 'com.example')),
        # review-media-error-text-4 M1: the three formats the per-language rules kept missing.
        # They are refused by the allow-list (English-dominant), not by a new language rule.
        ('PHP Fatal error: Uncaught Error: Call to undefined function privateGenerate() '
         'in engine.php on line 42',
         ('privateGenerate', 'engine.php', 'on line 42', 'Fatal error')),
        ('Fatal error: invalid state: file Engine.swift, line 42',
         ('Engine.swift', 'line 42', 'invalid state')),
        ("RuntimeError('privateGenerate() failed in private_engine.py')",
         ('privateGenerate', 'private_engine.py')),
        # the rest of the review-4 language probes, pinned as well
        ("thread 'main' panicked at 'index out of bounds': src/engine.rs:42:7",
         ('panicked', 'engine.rs', '42:7')),
        ('System.InvalidOperationException: bad state\n   at PrivateEngine.Generate() '
         'in Engine.cs:line 42', ('PrivateEngine', 'Engine.cs', 'line 42')),
        ("engine.rb:42:in `generate': undefined method (RuntimeError)",
         ('engine.rb', 'generate')),
        ('(media 1.0.0) lib/engine.ex:42: Media.Engine.generate/1',
         ('lib/engine.ex', 'Media.Engine')),
        ('engine.lua:42: attempt to call a nil value', ('engine.lua', 'nil value')),
        ('privateGenerate()+0x28 [0x7ffdabcd]', ('privateGenerate', '0x7ffdabcd')),
    )
    REWRITTEN = (
        'RuntimeError: CUDA out of memory',
        'torch.cuda.OutOfMemoryError: CUDA out of memory',
        'CUDA error: out of memory',
        '显存不足，请降低尺寸',
    )
    KEPT = (
        '最多允许 1/2 张参考图',
        '提示词包含未允许的内容',
        'model.safetensors 不存在，请选择已安装模型',
        '当前模型不支持 low VRAM 模式，请关闭该选项',
        '模型要求客户端版本 v1.2.3.4',
        '请修改以下选项：\n    图片尺寸不超过 2048\n    参考图最多两张',
        '这不是显存不足，是图片格式错误',
    )
    # review-media-error-text-e rule 3: a known English condition becomes our own Chinese line,
    # and the upstream wording never appears.
    ENGLISH_MAPPED = (
        ('Please provide at least one image', '请至少提供一张参考图', 'at least one image'),
        ('no image provided', '请至少提供一张参考图', 'no image provided'),
        ('prompt is required', '请填写提示词', 'prompt is required'),
        ('empty prompt', '请填写提示词', 'empty prompt'),
        ('Unsupported image format', '图片格式不支持', 'image format'),
        ('model qi21 not found', '所选模型不可用', 'not found'),
        ('unknown model', '所选模型不可用', 'unknown model'),
        ('too many images', '参考图数量超出上限', 'too many images'),
        ('rate limit exceeded', '请求过于频繁，请稍后再试', 'rate limit'),
        ('Too many requests', '请求过于频繁，请稍后再试', 'too many requests'),
        ('request timed out', '媒体服务处理超时', 'timed out'),
    )
    # rule 2: any other English-dominant text is dropped — only the workbench's own copy shows.
    ENGLISH_DROPPED = (
        'This model requires 16 GB VRAM; choose a compatible model',
        'CPU allocator out of memory while reading input',
        'To avoid CUDA out of memory, please choose a smaller image',
        'Quota resets on 2026-10-12',
        'Please check the reference images and try again later',
    )
    GPU_COPY = '显卡显存不足，请稍后重试或降低尺寸'

    def test_http_exit_refuses_every_upstream_artefact(self):
        for text, markers in self.REFUSED:
            with self.subTest(text=text):
                message = self.http_exit(text)
                self.assertIn('HTTP 502', message)
                for marker in markers:
                    self.assertNotIn(marker, message, message)

    def test_sse_exit_refuses_every_upstream_artefact(self):
        for text, markers in self.REFUSED:
            with self.subTest(text=text):
                warning = self.sse_exit(text)
                for marker in markers:
                    self.assertNotIn(marker, warning, warning)

    def test_json_failed_exit_refuses_every_upstream_artefact(self):
        for text, markers in self.REFUSED:
            with self.subTest(text=text):
                error = self.failed_exit(text)
                for marker in markers:
                    self.assertNotIn(marker, error, error)

    def test_json_uncertain_exit_refuses_every_upstream_artefact(self):
        for text, markers in self.REFUSED:
            with self.subTest(text=text):
                warning = self.uncertain_exit(text)
                for marker in markers:
                    self.assertNotIn(marker, warning, warning)

    def test_gpu_out_of_memory_becomes_only_our_own_line_on_all_exits(self):
        for text in self.REWRITTEN:
            with self.subTest(text=text):
                for outcome in self.exits(text):
                    self.assertIn(self.GPU_COPY, outcome, outcome)
                    self.assertNotIn('CUDA', outcome, outcome)
                    self.assertNotIn('OutOfMemoryError', outcome, outcome)

    def test_readable_copy_and_ambiguous_memory_wording_survive_all_exits(self):
        # L1 guard: the allow-list must not swallow Chinese business copy, and must not turn a
        # VRAM requirement, a negated OOM or a CPU OOM into a graphics-card diagnosis.
        for text in self.KEPT:
            with self.subTest(text=text):
                expected = ' '.join(text.split())
                http = self.http_exit(text, code=400)
                self.assertIn(expected, http, http)
                for outcome in (self.sse_exit(text), self.failed_exit(text),
                                self.uncertain_exit(text)):
                    self.assertIn(expected, outcome, outcome)
                    self.assertNotIn(self.GPU_COPY, outcome, outcome)

    def test_known_english_conditions_become_only_our_own_chinese_on_all_exits(self):
        # rule 3: the mapping table replaces the wording; the English original never appears.
        for text, copy, marker in self.ENGLISH_MAPPED:
            with self.subTest(text=text):
                self.assertEqual(media_http.safe_upstream_text(text), copy)
                for outcome in self.exits(text):
                    self.assertIn(copy, outcome, outcome)
                    self.assertNotIn(marker, outcome, outcome)

    def test_unknown_english_copy_is_never_attached_on_any_exit(self):
        # rule 2: English-dominant text is dropped on all four exits; only fixed Chinese shows.
        for text in self.ENGLISH_DROPPED:
            with self.subTest(text=text):
                self.assertEqual(media_http.safe_upstream_text(text), '')
                for outcome in self.exits(text):
                    self.assertNotIn(text, outcome, outcome)
                    self.assertNotIn(' '.join(text.split()), outcome, outcome)
                    self.assertNotIn(self.GPU_COPY, outcome, outcome)


class CjkAllowListTests(unittest.TestCase):
    """review-media-error-text-e: the upstream text is attached only as an allow-list.

    Rule (a) CJK-dominant, (b) free of every code/path/proxy shape, (c) <= 200 chars. Anything
    else -> '' (or a fixed Chinese line from the mapping table). Synthetic strings only.
    """
    CHINESE_BUSINESS = (
        'model.safetensors 不存在，请选择已安装模型',
        '最多允许 1/2 张参考图',
        '模型要求客户端版本 v1.2.3.4',
        '额度将在 2026-10-12 恢复，请稍后再试',
        '参考图比例 0.75 到 1.25 之间',
        'size 1024x1024 (portrait) 不支持',
        '请修改以下选项：\n    图片尺寸不超过 2048\n    参考图最多两张',
    )
    ENGLISH_DROPPED = (
        'PHP Fatal error: Uncaught Error: Call to undefined function privateGenerate() '
        'in engine.php on line 42',
        'Fatal error: invalid state: file Engine.swift, line 42',
        "RuntimeError('privateGenerate() failed in private_engine.py')",
        'Please check the reference images and try again later',
        'Quota resets on 2026-10-12',
        'This model requires 16 GB VRAM; choose a compatible model',
    )

    def test_chinese_business_copy_is_attached(self):
        for text in self.CHINESE_BUSINESS:
            with self.subTest(text=text):
                self.assertEqual(media_http.safe_upstream_text(text), ' '.join(text.split()))

    def test_english_dominant_text_is_never_attached(self):
        for text in self.ENGLISH_DROPPED:
            with self.subTest(text=text):
                self.assertEqual(media_http.safe_upstream_text(text), '')

    def test_ratio_is_measured_in_reading_units_not_raw_characters(self):
        # one long model name must not outvote a whole Chinese sentence
        self.assertGreaterEqual(media_http._cjk_ratio('model.safetensors 不存在，请选择已安装模型'),
                                media_http._CJK_MIN_RATIO)
        self.assertEqual(media_http._cjk_ratio('PHP Fatal error: Call to privateGenerate() '
                                               'in engine.php on line 42'), 0.0)
        self.assertEqual(media_http._cjk_ratio(''), 0.0)

    def test_chinese_copy_still_needs_to_pass_the_shape_rules(self):
        # (a) alone is not enough: a Chinese sentence carrying a frame is still refused
        for text in ('生成失败 engine.go:42 +0x123', '服务异常 /srv/private/config.json',
                     '任务失败 main.privateGenerate()', '异常：envoy 上游连接被重置'):
            with self.subTest(text=text):
                self.assertTrue(media_http._cjk_dominant(text))
                self.assertEqual(media_http.safe_upstream_text(text), '')

    def test_length_cap_still_applies(self):
        self.assertEqual(media_http.safe_upstream_text('参考图' * 70), '')
        self.assertEqual(len(media_http.safe_upstream_text('参考图' * 60)), 180)

    def test_indented_chinese_hint_is_not_a_traceback(self):
        # review-media-error-text-e L1: two indented *Chinese* lines are a layout, not a body
        text = '请修改以下选项：\n\t图片尺寸不超过 2048\n\t参考图最多两张'
        self.assertEqual(media_http._indented_code_lines(text), 0)
        self.assertEqual(media_http.safe_upstream_text(text), ' '.join(text.split()))
        english = 'Generation failed:\n\tchoose a smaller size\n\tuse fewer images'
        self.assertEqual(media_http._indented_code_lines(english), 2)
        self.assertEqual(media_http.safe_upstream_text(english), '')


class KnownEnglishTableTests(unittest.TestCase):
    """review-media-error-text-e rule 3: every table entry -> our Chinese, original absent."""
    CASES = (
        ('at least one image', '请至少提供一张参考图'),
        ('No image provided', '请至少提供一张参考图'),
        ('image is required', '请至少提供一张参考图'),
        ('prompt is required', '请填写提示词'),
        ('Empty prompt given', '请填写提示词'),
        ('missing prompt', '请填写提示词'),
        ('unsupported image format', '图片格式不支持'),
        ('unsupported format', '图片格式不支持'),
        ('unsupported file type', '图片格式不支持'),
        ('model qi21 not found', '所选模型不可用'),
        ('unknown model requested', '所选模型不可用'),
        ('too many images in request', '参考图数量超出上限'),
        ('too many files uploaded', '参考图数量超出上限'),
        ('rate limit hit', '请求过于频繁，请稍后再试'),
        ('Too Many Requests', '请求过于频繁，请稍后再试'),
        ('quota exceeded', '请求过于频繁，请稍后再试'),
        ('request timed out', '媒体服务处理超时'),
        ('upstream timeout', '媒体服务处理超时'),
    )

    def test_each_entry_becomes_our_own_chinese(self):
        for text, copy in self.CASES:
            with self.subTest(text=text):
                self.assertEqual(media_http.safe_upstream_text(text), copy)

    def test_the_english_original_never_survives(self):
        for text, copy in self.CASES:
            with self.subTest(text=text):
                self.assertNotIn(text.lower(), media_http.safe_upstream_text(text).lower())

    def test_unknown_english_has_no_entry_and_is_dropped(self):
        for text in ('something went wrong on our side', 'please retry later today',
                     'the worker exited unexpectedly'):
            with self.subTest(text=text):
                self.assertEqual(media_http.safe_upstream_text(text), '')

    def test_gpu_out_of_memory_is_still_named_before_the_table(self):
        # CUDA OOM keeps the review-d narrowing: explicit only, never a negation or advice
        for text in ('CUDA out of memory', 'torch.cuda.OutOfMemoryError: CUDA out of memory',
                     'GPU out of memory', 'VRAM out of memory', '显存不足，请降低尺寸'):
            with self.subTest(text=text):
                self.assertEqual(media_http.safe_upstream_text(text),
                                 '显卡显存不足，请稍后重试或降低尺寸')
        for text in ('This is not a CUDA OOM error; invalid image format',
                     'To avoid CUDA out of memory, choose a smaller image',
                     '为避免显存不足，请降低输出尺寸', 'not a CUDA out of memory error'):
            with self.subTest(text=text):
                self.assertNotEqual(media_http.safe_upstream_text(text),
                                    '显卡显存不足，请稍后重试或降低尺寸')

    def test_known_english_wins_over_the_chinese_allow_list(self):
        # review-media-error-text-f M3: the table is consulted *before* the "CJK-dominant means
        # attach verbatim" branch, so a Chinese prefix cannot keep the English visible.
        for text, copy in (
                ('请检查输入并重新提交：prompt is required', '请填写提示词'),
                ('您选择的图片暂时无法读取：UNSUPPORTED IMAGE FORMAT', '图片格式不支持'),
                ('请检查输入并重新提交：PrOmPt Is ReQuIrEd', '请填写提示词'),
                ('请检查输入并重新提交：Ｐｒｏｍｐｔ　Ｉｓ　Ｒｅｑｕｉｒｅｄ', '请填写提示词'),
                ('请求过于频繁：Too Many Requests，请稍后再试', '请求过于频繁，请稍后再试'),
                ('所选模型不可用：model qi21 NOT FOUND', '所选模型不可用'),
                ('处理超时 upstream timeout，请稍后重试', '媒体服务处理超时'),
                ('参考图不足：At Least One Image 未收到', '请至少提供一张参考图')):
            with self.subTest(text=text):
                self.assertTrue(media_http._cjk_dominant(text))
                self.assertEqual(media_http.safe_upstream_text(text), copy)
                self.assertNotIn(' '.join(text.split()).lower(),
                                 media_http.safe_upstream_text(text).lower())


class CjkUnitCountingTests(unittest.TestCase):
    """review-media-error-text-f M1: punctuation and fullwidth Latin are not Chinese units.

    A CJK reading unit is a real letter — a CJK ideograph or a kana/bopomofo phonetic sign.
    Chinese punctuation, curly quotes and the whole U+FF00–FFEF fullwidth block (which also
    carries fullwidth Latin) never count, and punctuation only separates: it is neither a
    Chinese unit nor a unit of its own. Fullwidth forms are width-normalised before both the
    count and the shape checks, so `ＲｕｎｔｉｍｅＥｒｒｏｒ（ｐｒｉｖａｔｅ）` is read as
    `RuntimeError(private)`. Synthetic strings only, no transport involved.
    """
    NOT_USER_COPY = (
        '。。。。。。RuntimeError("new_private.py")',      # measured 85.7% before the fix
        '“””“RuntimeError("new_private.py")',             # measured 80% before the fix
        'ＲｕｎｔｉｍｅＥｒｒｏｒ（ｐｒｉｖａｔｅ）',                 # measured 100% before the fix
        '。。。，，！！！？？？',                              # punctuation alone proves nothing
        '错误，one, two, three, four, five, six, seven, nine',  # commas must not glue English
    )
    FULLWIDTH_ARTEFACTS = (
        'ｒａｉｓｅ ＲｕｎｔｉｍｅＥｒｒｏｒ（）',
        '错误：Ｔｒａｃｅｂａｃｋ（ｍｏｓｔ ｒｅｃｅｎｔ ｃａｌｌ ｌａｓｔ）',
        '服务返回 错误：文件 ｃｏｎｆｉｇ．ｊｓｏｎ：４２ 读取失败',
        '连接１０．０．０．９：８２００失败，请稍后重试',
    )
    READABLE = (
        'ＦｕｌｌＷｉｄｔｈ 混合提示，请稍后再试',
        'model.safetensors 不存在，请选择已安装模型',
        '额度将于 2026-10-12 重置',
        '参考图比例 1.5:2 不支持，请改为 16:9',
    )

    def test_punctuation_or_fullwidth_cannot_reach_the_ratio(self):
        for text in self.NOT_USER_COPY:
            with self.subTest(text=text):
                self.assertLess(media_http._cjk_ratio(' '.join(text.split())),
                                media_http._CJK_MIN_RATIO)
                self.assertEqual(media_http.safe_upstream_text(text), '')

    def test_fullwidth_forms_still_meet_the_shape_rules(self):
        for text in self.FULLWIDTH_ARTEFACTS:
            with self.subTest(text=text):
                self.assertEqual(media_http.safe_upstream_text(text), '')

    def test_readable_copy_with_fullwidth_or_punctuation_survives(self):
        for text in self.READABLE:
            with self.subTest(text=text):
                self.assertEqual(media_http.safe_upstream_text(text), ' '.join(text.split()))

    def test_punctuation_is_never_a_unit_of_its_own(self):
        # the approved reading-unit口径 stays: one long English name is one unit, and commas
        # separate English words instead of gluing them into one
        self.assertGreaterEqual(media_http._cjk_ratio('model.safetensors 不存在，请选择已安装模型'),
                                media_http._CJK_MIN_RATIO)
        self.assertEqual(media_http._cjk_ratio('。。。。。。'), 0.0)
        self.assertLess(media_http._cjk_ratio('错误，one, two, three, four, five'), 0.5)
        self.assertTrue(media_http._cjk_dominant('ＦｕｌｌＷｉｄｔｈ 混合提示，请稍后再试'))
        self.assertFalse(media_http._cjk_dominant('ＲｕｎｔｉｍｅＥｒｒｏｒ（ｐｒｉｖａｔｅ）'))


class ChineseWrappedArtefactTests(unittest.TestCase):
    """review-media-error-text-f M2: Chinese prose around code/IP is still an artefact.

    Every input below is CJK-dominant under the reading-unit rule, so the shape layer alone has
    to refuse it: a Chinese explanation never excuses the code it explains.
    """
    REFUSED = (
        '系统错误，请联系管理员：raise RuntimeError("new_private.py")',
        '任务执行失败，请联系管理员：PHP Fatal error in new_engine.php on line 73',
        '连接10.0.0.9:8200失败，请稍后重试',
        '后端 10.0.0.9:8200 未响应，请稍后重试',
        '生成失败：Traceback (most recent call last): 详见日志',
        '任务失败：main.privateGenerate() 无法继续',
        '服务异常：config.json:42 读取失败',
        '系统错误：Fatal error in new_engine.php on line 73，请联系管理员',
        '服务异常：nginx at 10.0.0.5:8200，请稍后重试',
    )
    READABLE = (
        '模型要求客户端版本 v1.2.3.4',
        '请升级桌面客户端至 v4.3.2.1 后重试',
        '参考图比例 1.5:2 不支持，请改为 16:9',
        '最多允许 1/2 张参考图',
        '图片尺寸不符合要求，请调整为 1024x1024',
        '请核对以下参数：\n    宽度不得超过 3072\n    请减少参考图片数量',
    )

    def test_chinese_wrapped_code_or_address_is_refused(self):
        for text in self.REFUSED:
            with self.subTest(text=text):
                self.assertTrue(media_http._cjk_dominant(text))
                self.assertEqual(media_http.safe_upstream_text(text), '')

    def test_versions_ratios_and_layout_survive_the_new_shapes(self):
        for text in self.READABLE:
            with self.subTest(text=text):
                self.assertEqual(media_http.safe_upstream_text(text), ' '.join(text.split()))

    def test_ip_boundary_is_ascii_not_unicode_word(self):
        # `\w` matched the Chinese 接 and hid the address; v1.2.3.4 must stay a version
        self.assertIsNotNone(media_http._IP_PORT.search('连接10.0.0.9:8200失败'))
        self.assertIsNone(media_http._IP_PORT.search('模型要求客户端版本 v1.2.3.4'))
        self.assertIsNone(media_http._IP_PORT.search('请升级桌面客户端至 v4.3.2.1 后重试'))


class ChineseWrappedArtefactAcrossAllExitsTests(FourExitHarness):
    """The eight review-media-error-text-f leaks, pinned on all four exits.

    REFUSED: the collapsed upstream string must appear in none of the four exits.
    MAPPED: only our fixed Chinese line may appear, the original must not.
    """
    REFUSED = (
        '。。。。。。RuntimeError("new_private.py")',
        '“””“RuntimeError("new_private.py")',
        'ＲｕｎｔｉｍｅＥｒｒｏｒ（ｐｒｉｖａｔｅ）',
        '系统错误，请联系管理员：raise RuntimeError("new_private.py")',
        '任务执行失败，请联系管理员：PHP Fatal error in new_engine.php on line 73',
        '连接10.0.0.9:8200失败，请稍后重试',
    )
    MAPPED = (
        ('请检查输入并重新提交：prompt is required', '请填写提示词'),
        ('您选择的图片暂时无法读取：UNSUPPORTED IMAGE FORMAT', '图片格式不支持'),
    )

    def test_http_exit_refuses_the_chinese_wrapped_artefacts(self):
        for text in self.REFUSED:
            with self.subTest(text=text):
                self.assertNotIn(' '.join(text.split()), self.http_exit(text))
        for text, copy in self.MAPPED:
            with self.subTest(text=text):
                result = self.http_exit(text)
                self.assertIn(copy, result)
                self.assertNotIn(' '.join(text.split()).lower(), result.lower())

    def test_sse_exit_refuses_the_chinese_wrapped_artefacts(self):
        for text in self.REFUSED:
            with self.subTest(text=text):
                self.assertNotIn(' '.join(text.split()), self.sse_exit(text))
        for text, copy in self.MAPPED:
            with self.subTest(text=text):
                result = self.sse_exit(text)
                self.assertIn(copy, result)
                self.assertNotIn(' '.join(text.split()).lower(), result.lower())

    def test_json_failed_exit_refuses_the_chinese_wrapped_artefacts(self):
        for text in self.REFUSED:
            with self.subTest(text=text):
                self.assertNotIn(' '.join(text.split()), str(self.failed_exit(text)))
        for text, copy in self.MAPPED:
            with self.subTest(text=text):
                result = str(self.failed_exit(text))
                self.assertIn(copy, result)
                self.assertNotIn(' '.join(text.split()).lower(), result.lower())

    def test_json_uncertain_exit_refuses_the_chinese_wrapped_artefacts(self):
        for text in self.REFUSED:
            with self.subTest(text=text):
                self.assertNotIn(' '.join(text.split()), str(self.uncertain_exit(text)))
        for text, copy in self.MAPPED:
            with self.subTest(text=text):
                result = str(self.uncertain_exit(text))
                self.assertIn(copy, result)
                self.assertNotIn(' '.join(text.split()).lower(), result.lower())

    def test_business_copy_still_survives_all_four_exits(self):
        for text in ('模型要求客户端版本 v1.2.3.4', '额度将于 2026-10-12 重置',
                     '参考图比例 1.5:2 不支持，请改为 16:9'):
            with self.subTest(text=text):
                for exit_text in self.exits(text):
                    self.assertIn(' '.join(text.split()), exit_text)


class ChineseAdjacentArtefactTests(unittest.TestCase):
    """review-media-error-text-h M1: a code artefact glued to Chinese is still an artefact.

    Python's `\\b`/`\\w` are Unicode-aware and a Chinese ideograph is a word character, so every
    shape rule used to lose its boundary the moment Chinese touched the ASCII word
    (`执行失败raise …`). All boundaries are now spelled in ASCII, and a bare exception name plus
    a colon is covered too — with a Chinese colon as well.
    """
    REFUSED = (
        '执行失败raise HiddenFailure("payload")请联系管理员重试',
        '运行失败NullPointerException: null请联系管理员后重试',
        '执行失败panic: invalid memory address请联系管理员后重试',
        '错误位置main.worker(42)请联系管理员后重试',
        '请稍后重试，错误位置handler.yaml:87，请联系管理员',
        '运行失败：NullPointerException: null，请联系管理员后重试',
        '运行失败：ValueError: invalid literal，请联系管理员后重试',
    )
    READABLE = (
        '请升级客户端到 v8.7.6.5 后重试图片生成',
        'ultrahyperdimensionalexpansiveweights.safetensors 不存在，请选择已经下载的模型',
        '请调整以下参数：\n    图片高度设为 1536\n    参考图最多三张',
        '业务提示：prompts were required，请重新检查本次输入',
        '模型 qwen-image-2.1 未加载，请稍后重试',
    )

    def test_artefact_glued_to_chinese_is_refused(self):
        for text in self.REFUSED:
            with self.subTest(text=text):
                self.assertTrue(media_http._cjk_dominant(text))
                self.assertEqual(media_http.safe_upstream_text(text), '')

    def test_ascii_boundary_is_used_by_every_shape_rule(self):
        # the Unicode `\b` is gone from the shape layer: an ASCII neighbour never opens a match,
        # a Chinese neighbour always does
        for pattern in media_http._STACK_PATTERNS + media_http._CODE_SHAPE_PATTERNS:
            with self.subTest(pattern=pattern.pattern):
                self.assertNotIn(r'\b', pattern.pattern)
                self.assertNotIn(r'\w', pattern.pattern)
        self.assertIsNotNone(media_http._IP_PORT.search('连接10.0.0.9:8200失败'))

    def test_readable_business_copy_survives_the_ascii_boundaries(self):
        for text in self.READABLE:
            with self.subTest(text=text):
                self.assertEqual(media_http.safe_upstream_text(text), ' '.join(text.split()))


class KnownEnglishSemanticsTests(unittest.TestCase):
    """review-media-error-text-h M2: the table needs the whole condition, ASCII-anchored.

    Positive: the complete phrase, also when Chinese sits right next to it -> our fixed Chinese,
    the English original gone. Negative: a phrase that only *contains* the shape of a condition
    (a processing state, a stated limit, a named timeout, a bare noun) must NOT be rewritten.
    """
    MAPPED = (
        ('请检查输入：images are required，再重新提交', '请至少提供一张参考图'),
        ('请补全输入at least one image然后再提交', '请至少提供一张参考图'),
        ('请补全输入prompt is required然后再重新提交', '请填写提示词'),
        ('请检查：Prompt Is Required', '请填写提示词'),
        ('格式错误，请检查：image format is invalid', '图片格式不支持'),
        ('所选模型不可用：model qi21 NOT FOUND', '所选模型不可用'),
        ('参考图太多：too many images，请减少', '参考图数量超出上限'),
        ('请求过于频繁：rate limit exceeded，请稍后再试', '请求过于频繁，请稍后再试'),
        ('任务超时 request timed out，请稍后重试', '媒体服务处理超时'),
        ('上游超时 upstream timeout，请稍后重试', '媒体服务处理超时'),
    )
    NOT_MAPPED = (
        ('服务提示：images are processing，请耐心等待完成', '请至少提供一张参考图'),
        ('参考图正在上传 images are uploading，请稍候', '请至少提供一张参考图'),
        ('业务提示：prompts were required，请重新检查本次输入', '请填写提示词'),
        ('提示词 prompt 是必填项，请补充', '请填写提示词'),
        ('支持格式 image format 包括 png 与 jpg', '图片格式不支持'),
        ('模型 model 未找到说明，请稍后重试', '所选模型不可用'),
        ('参考图上限 maximum number of images is 8，请在此范围内选择', '参考图数量超出上限'),
        ('每分钟请求上限 rate limit 为 60 次，请稍后核实', '请求过于频繁，请稍后再试'),
        ('处理超时 timeout 设置为 120 秒，请稍后核实', '媒体服务处理超时'),
    )

    def test_complete_condition_becomes_our_own_chinese(self):
        for text, copy in self.MAPPED:
            with self.subTest(text=text):
                self.assertEqual(media_http.safe_upstream_text(text), copy)
                self.assertNotIn(' '.join(text.split()).lower(),
                                 media_http.safe_upstream_text(text).lower())

    def test_partial_condition_is_never_rewritten(self):
        for text, wrong in self.NOT_MAPPED:
            with self.subTest(text=text):
                result = media_http.safe_upstream_text(text)
                self.assertNotEqual(result, wrong)
                self.assertEqual(result, ' '.join(text.split()))


class ChineseAdjacentArtefactAcrossAllExitsTests(FourExitHarness):
    """The seven review-media-error-text-h leaks, pinned on all four exits.

    REFUSED: the collapsed upstream string appears in none of the four exits.
    KEPT: readable business copy reaches every exit unchanged.
    """
    REFUSED = ChineseAdjacentArtefactTests.REFUSED
    KEPT = ('请升级客户端到 v8.7.6.5 后重试图片生成',
            '服务提示：images are processing，请耐心等待完成')

    def test_http_exit_refuses_the_chinese_adjacent_artefacts(self):
        for text in self.REFUSED:
            with self.subTest(text=text):
                self.assertNotIn(' '.join(text.split()), self.http_exit(text))

    def test_sse_exit_refuses_the_chinese_adjacent_artefacts(self):
        for text in self.REFUSED:
            with self.subTest(text=text):
                self.assertNotIn(' '.join(text.split()), self.sse_exit(text))

    def test_json_failed_exit_refuses_the_chinese_adjacent_artefacts(self):
        for text in self.REFUSED:
            with self.subTest(text=text):
                self.assertNotIn(' '.join(text.split()), str(self.failed_exit(text)))

    def test_json_uncertain_exit_refuses_the_chinese_adjacent_artefacts(self):
        for text in self.REFUSED:
            with self.subTest(text=text):
                self.assertNotIn(' '.join(text.split()), str(self.uncertain_exit(text)))

    def test_readable_copy_still_reaches_all_four_exits(self):
        for text in self.KEPT:
            with self.subTest(text=text):
                for exit_text in self.exits(text):
                    self.assertIn(' '.join(text.split()), exit_text)


class BareRuntimeShapeTests(unittest.TestCase):
    r"""review-media-error-text-i M1: bare runtime headlines are refused even without a frame.

    `Exception in thread main`, `Segmentation fault`, `core dumped`, a bare `at ` + package name
    and `errno 42` carry no parentheses, colon or line number, so no earlier shape rule saw
    them. They are refused inside the same ASCII-boundary system (no Unicode `\b`/`\w`), and an
    ordinary Chinese business line that merely contains `failed:` stays readable.
    """
    REFUSED = (
        '任务异常Exception in thread main请联系管理员后重试',
        '任务异常Segmentation fault请联系管理员后重试',
        '任务异常core dumped请联系管理员后重试',
        '执行异常at java.net.Socket请联系管理员后重试',
        '执行异常errno 42请联系管理员后重试',
        '任务异常kernel panic请联系管理员后重试',
        '进程崩溃segfault at 00007ffd 请联系管理员',
        '执行异常errno=28请联系管理员后重试',
        '异常堆栈at org.apache.cli.Run请联系管理员',
    )
    READABLE = (
        '上次生成failed:请修改描述后重新提交',
        '任务失败原因：描述过长，请缩短后重新提交',
        '可在高级设置调整timeout参数后重新生成',
        '进程占用过高，请关闭部分程序后重试',
    )

    def test_bare_runtime_shape_is_refused(self):
        for text in self.REFUSED:
            with self.subTest(text=text):
                self.assertTrue(media_http._cjk_dominant(text))
                self.assertEqual(media_http.safe_upstream_text(text), '')

    def test_shape_layer_still_has_no_unicode_boundaries(self):
        for pattern in media_http._STACK_PATTERNS + media_http._CODE_SHAPE_PATTERNS:
            with self.subTest(pattern=pattern.pattern):
                self.assertNotIn(r'\b', pattern.pattern)
                self.assertNotIn(r'\w', pattern.pattern)

    def test_failed_business_copy_stays_readable(self):
        for text in self.READABLE:
            with self.subTest(text=text):
                self.assertEqual(media_http.safe_upstream_text(text), ' '.join(text.split()))


class TableContextTests(unittest.TestCase):
    """review-media-error-text-i M2: the table only fires on a stated condition.

    STATED: the phrase is presented as what happened this time -> our fixed Chinese, the English
    original gone. NOT_STATED: the phrase is named, denied, assigned as a configuration value,
    described as a preventive setting, or reported as already satisfied -> the string keeps its
    own wording and is never turned into a failure we invented.
    """
    STATED = (
        ('请补充no images supplied然后再继续生成', '请至少提供一张参考图'),
        ('请补充prompt cannot be empty然后再继续生成', '请填写提示词'),
        ('请修改media format not supported然后再继续生成', '图片格式不支持'),
        ('请重选model demo not available然后再继续生成', '所选模型不可用'),
        ('请减少image count exceeds然后再继续生成', '参考图数量超出上限'),
        ('稍后重试rate limit hit然后再继续生成', '请求过于频繁，请稍后再试'),
        ('稍后重试timeout after 35然后再继续生成', '媒体服务处理超时'),
        ('请检查输入并重新提交：prompt is required', '请填写提示词'),
        ('您选择的图片暂时无法读取：UNSUPPORTED IMAGE FORMAT', '图片格式不支持'),
    )
    NOT_STATED = (
        ('已确认prompt is required仅为配置项名称并非本次失败原因', '请填写提示词'),
        ('请将request timeout设置为六十秒后再次重试', '媒体服务处理超时'),
        ('请开启rate limiting以避免请求过载后再次重试', '请求过于频繁，请稍后再试'),
        ('支持at least one image且当前已上传三张请稍候', '请至少提供一张参考图'),
        ('本服务maximum number of images is 8请合理安排上传数量', '参考图数量超出上限'),
        ('所选model已安装但参考文件not found请重新选择图片', '所选模型不可用'),
        ('这些images are ready请点击继续开始生成', '请至少提供一张参考图'),
        ('可在高级设置查看rate limit参数后重新生成', '请求过于频繁，请稍后再试'),
        ('提示词字段名称prompt is required已改名，不影响本次生成', '请填写提示词'),
        ('默认值为timeout 120秒，请按需调整后再试', '媒体服务处理超时'),
        ('参考图已提供三张，无需重复上传', '请至少提供一张参考图'),
        ('本次未开启rate limited选项，请确认后重试', '请求过于频繁，请稍后再试'),
    )

    def test_stated_condition_becomes_our_own_chinese(self):
        for text, copy in self.STATED:
            with self.subTest(text=text):
                self.assertEqual(media_http.safe_upstream_text(text), copy)
                self.assertNotIn(' '.join(text.split()).lower(),
                                 media_http.safe_upstream_text(text).lower())

    def test_named_denied_configured_or_satisfied_phrase_is_kept(self):
        for text, wrong in self.NOT_STATED:
            with self.subTest(text=text):
                result = media_http.safe_upstream_text(text)
                self.assertNotEqual(result, wrong)
                self.assertEqual(result, ' '.join(text.split()))

    def test_clause_guard_is_reachable_for_the_mutation_check(self):
        # the clause guard is one named classifier, so a mutation can neutralise it and go red
        self.assertTrue(callable(media_http._condition_state))
        for text, state in (('仅为配置项名称prompt is required', 'configure'),
                            ('本次实际错误为prompt is required', 'assert'),
                            ('本次未发生prompt is required', 'negate'),
                            ('帮助写“prompt is required”即可', 'quote'),
                            ('当前已提供三张图prompt is required', 'satisfied')):
            with self.subTest(text=text):
                at = text.index('prompt')
                self.assertEqual(media_http._condition_state(text, at, at + 18), state)
        self.assertFalse(hasattr(media_http, '_CONDITION_NOT_STATED'))


class BareRuntimeAcrossAllExitsTests(FourExitHarness):
    """The five review-media-error-text-i M1 leaks, pinned on all four exits.

    REFUSED: the upstream string appears in none of the four exits.
    KEPT: an ordinary Chinese business line containing `failed:` reaches every exit unchanged.
    """
    REFUSED = BareRuntimeShapeTests.REFUSED[:5]
    KEPT = ('上次生成failed:请修改描述后重新提交',
            '已确认prompt is required仅为配置项名称并非本次失败原因')

    def test_http_exit_refuses_the_bare_runtime_shapes(self):
        for text in self.REFUSED:
            with self.subTest(text=text):
                self.assertNotIn(' '.join(text.split()), self.http_exit(text))

    def test_sse_exit_refuses_the_bare_runtime_shapes(self):
        for text in self.REFUSED:
            with self.subTest(text=text):
                self.assertNotIn(' '.join(text.split()), self.sse_exit(text))

    def test_json_failed_exit_refuses_the_bare_runtime_shapes(self):
        for text in self.REFUSED:
            with self.subTest(text=text):
                self.assertNotIn(' '.join(text.split()), str(self.failed_exit(text)))

    def test_json_uncertain_exit_refuses_the_bare_runtime_shapes(self):
        for text in self.REFUSED:
            with self.subTest(text=text):
                self.assertNotIn(' '.join(text.split()), str(self.uncertain_exit(text)))

    def test_business_copy_still_reaches_all_four_exits(self):
        for text in self.KEPT:
            with self.subTest(text=text):
                for exit_text in self.exits(text):
                    self.assertIn(' '.join(text.split()), exit_text)


class RuntimeFatalShapeTests(unittest.TestCase):
    r"""review-media-error-text-j M1: the crash / process-exit vocabulary, enumerated once.

    The i round added five headline rules and still missed Aborted, Bus error, Floating point
    exception, signal 11, died with signal 9, Killed 7231, status 139 and a single-dot
    `at worker.run`. `_RUNTIME_FATAL_SHAPES` now carries the standard enumeration (POSIX signal
    names, signal numbers, the 128+N exit range, the C-runtime fatal headlines, errno, thread
    dumps), all ASCII-anchored, and the width-normalised form is checked too, so a fullwidth
    signal name cannot hide. Ordinary business copy — a `failed:` note, a version number, a model
    file name — stays readable.
    """
    REFUSED = (
        '本次失败Aborted请管理员检查',
        '本次失败Bus error请管理员检查',
        '本次失败Floating point exception请管理员检查',
        '本次失败signal 11请管理员检查',
        '工作进程died with signal 9请管理员检查',
        '工作进程Killed 7231请管理员检查',
        '工作进程status 139请管理员检查',
        '调用位置at worker.run请管理员检查',
        '进程收到ＳＩＧＳＥＧＶ信号请联系管理员',
        '进程异常ｓｉｇｎａｌ １１请联系管理员',
        '工作进程Killed -9请联系管理员',
        '进程退出status 137请联系管理员',
        '进程异常SIGABRT core dumped请联系管理员',
        '调用位置at java.net.Socket请联系管理员',
        '调用位置at org.demo.Runner请联系管理员',
        '本次失败panic: nil pointer请管理员检查',
    )
    READABLE = (
        '上次生成failed:请修改描述后重新提交',
        '说明示例at v2.3.4表示版本位置请核对',
        '请升级客户端到 v8.7.6.5 后重试图片生成',
        '模型文件model.safetensors未加载，请选择已安装模型',
        '模型 sdxl-turbo-v1.0-anime-style-mix 未加载，请稍后重试',
        '任务状态 status 已完成，请查看生成结果',
        '进程内存偏高，请关闭部分程序后重试',
    )

    def test_fatal_shapes_are_refused(self):
        for text in self.REFUSED:
            with self.subTest(text=text):
                self.assertTrue(media_http._cjk_dominant(text))
                self.assertEqual(media_http.safe_upstream_text(text), '')

    def test_shapes_are_one_table_and_stay_ascii_anchored(self):
        self.assertGreaterEqual(len(media_http._RUNTIME_FATAL_SHAPES), 12)
        for name, source, _flags in media_http._RUNTIME_FATAL_SHAPES:
            with self.subTest(shape=name):
                self.assertIn(media_http._LB, source)
                self.assertNotIn(r'\b', source)
                self.assertNotIn(r'\w', source)
        for pattern in media_http._STACK_PATTERNS + media_http._CODE_SHAPE_PATTERNS:
            with self.subTest(pattern=pattern.pattern[:48]):
                self.assertNotIn(r'\b', pattern.pattern)
                self.assertNotIn(r'\w', pattern.pattern)

    def test_business_copy_stays_readable(self):
        for text in self.READABLE:
            with self.subTest(text=text):
                self.assertEqual(media_http.safe_upstream_text(text), ' '.join(text.split()))


class ClauseContextTests(unittest.TestCase):
    """review-media-error-text-j M2: the guard is per clause and bound to the matched phrase.

    M2A — a denied or quoted phrase must keep its own wording (the i round invented a failure).
    M2B — a real failure stated in another clause must still be translated (the i round's
    whole-string switch silenced the whole table). The two halves come in pairs.
    """
    DENIED_OR_QUOTED = (
        ('提示词帮助引用了“prompt is required”这段文字请阅读说明', '请填写提示词'),
        ('本次未发生unsupported image format请检查其他原因', '图片格式不支持'),
        ('本次没有出现model nova not found请检查其他原因', '所选模型不可用'),
        ('本次并未出现too many images请检查其他原因', '参考图数量超出上限'),
        ('本次并未发生request timeout请检查其他原因', '媒体服务处理超时'),
    )
    STATED_ELSEWHERE = (
        ('这不是网络问题；实际错误为prompt is required，请补全输入', '请填写提示词'),
        ('已上传三张参考图；本次实际错误为model nova not found，请重新选择模型', '所选模型不可用'),
        ('图片支持正常；本次实际错误为rate limit hit，请稍后重试', '请求过于频繁，请稍后再试'),
        ('本次实际错误为unsupported image format，请检查参数再试', '图片格式不支持'),
        ('已确认本次错误为request timeout，请稍后再试', '媒体服务处理超时'),
    )

    def test_denied_or_quoted_phrase_keeps_its_own_wording(self):
        for text, invented in self.DENIED_OR_QUOTED:
            with self.subTest(text=text):
                result = media_http.safe_upstream_text(text)
                self.assertNotEqual(result, invented)
                self.assertEqual(result, ' '.join(text.split()))

    def test_failure_stated_in_another_clause_is_still_translated(self):
        for text, copy in self.STATED_ELSEWHERE:
            with self.subTest(text=text):
                self.assertEqual(media_http.safe_upstream_text(text), copy)
                self.assertNotIn(' '.join(text.split()), media_http.safe_upstream_text(text))


class TableStateMatrixTests(unittest.TestCase):
    """Every table entry × every clause state: 7 × 5 = 35 paired assertions.

    Only `assert` is rewritten into our own Chinese; negate / quote / configure / satisfied keep
    the original wording, and the classifier itself is pinned so a regression in one state cannot
    hide behind a passing outcome.
    """
    ENTRIES = (
        ('no images supplied', '请至少提供一张参考图'),
        ('prompt is required', '请填写提示词'),
        ('unsupported image format', '图片格式不支持'),
        ('model nova not found', '所选模型不可用'),
        ('too many images', '参考图数量超出上限'),
        ('rate limit hit', '请求过于频繁，请稍后再试'),
        ('request timeout', '媒体服务处理超时'),
    )
    TEMPLATES = (
        ('assert', '本次实际错误为{p}，请稍后重试', 'copy'),
        ('negate', '本次未发生{p}，请检查其他原因', 'keep'),
        ('quote', '帮助里引用了“{p}”这段文字，请阅读说明', 'keep'),
        ('configure', '请将{p}设置为默认值后再试', 'keep'),
        ('satisfied', '当前已有{p}条件已满足，请继续生成', 'keep'),
    )

    def test_matrix_outcomes(self):
        self.assertEqual(len(self.ENTRIES) * len(self.TEMPLATES), 35)
        for phrase, copy in self.ENTRIES:
            for state, template, kind in self.TEMPLATES:
                text = template.format(p=phrase)
                with self.subTest(state=state, phrase=phrase):
                    result = media_http.safe_upstream_text(text)
                    if kind == 'copy':
                        self.assertEqual(result, copy)
                    else:
                        self.assertEqual(result, ' '.join(text.split()))
                        self.assertNotEqual(result, copy)

    def test_matrix_states(self):
        for phrase, _copy in self.ENTRIES:
            for state, template, _kind in self.TEMPLATES:
                text = template.format(p=phrase)
                with self.subTest(state=state, phrase=phrase):
                    at = text.index(phrase)
                    self.assertEqual(
                        media_http._condition_state(text, at, at + len(phrase)), state)


class RuntimeFatalAcrossAllExitsTests(FourExitHarness):
    """The eight review-media-error-text-j M1 leaks, pinned on all four exits.

    REFUSED: the upstream string appears in none of the four exits.
    KEPT: a `failed:` note and a version hint reach every exit unchanged.
    """
    REFUSED = RuntimeFatalShapeTests.REFUSED[:8]
    KEPT = ('上次生成failed:请修改描述后重新提交',
            '说明示例at v2.3.4表示版本位置请核对')

    def test_http_exit_refuses_the_fatal_shapes(self):
        for text in self.REFUSED:
            with self.subTest(text=text):
                self.assertNotIn(' '.join(text.split()), self.http_exit(text))

    def test_sse_exit_refuses_the_fatal_shapes(self):
        for text in self.REFUSED:
            with self.subTest(text=text):
                self.assertNotIn(' '.join(text.split()), self.sse_exit(text))

    def test_json_failed_exit_refuses_the_fatal_shapes(self):
        for text in self.REFUSED:
            with self.subTest(text=text):
                self.assertNotIn(' '.join(text.split()), str(self.failed_exit(text)))

    def test_json_uncertain_exit_refuses_the_fatal_shapes(self):
        for text in self.REFUSED:
            with self.subTest(text=text):
                self.assertNotIn(' '.join(text.split()), str(self.uncertain_exit(text)))

    def test_business_copy_still_reaches_all_four_exits(self):
        for text in self.KEPT:
            with self.subTest(text=text):
                for exit_text in self.exits(text):
                    self.assertIn(' '.join(text.split()), exit_text)


class RuntimeFatalKTests(unittest.TestCase):
    """review-media-error-text-k M1: the four named crash headlines, with case / sign variants.

    `Unhandled Exception`, `access violation`, `OOM-killed` (any case) and `Killed ±N`. The
    ordinary word `killed` and a bare `OOM` mention in Chinese business copy stay readable.
    """
    REFUSED = (
        '本次异常Unhandled Exception请管理员检查',
        '本次异常access violation请管理员检查',
        '工作进程OOM-killed请管理员检查',
        '工作进程Killed +9请管理员检查',
        '本次异常unhandled exception请管理员检查',
        '本次异常Access Violation请管理员检查',
        '工作进程oom-killed请管理员检查',
        '工作进程OOM killed请管理员检查',
        '工作进程Killed -9请管理员检查',
        '工作进程Killed 9请管理员检查',
        '工作进程killed +9请管理员检查',
    )
    READABLE = (
        '任务被 killed 标记为已取消，请核对后重新提交',
        '内存 OOM 风险偏高，请降低尺寸后重试生成任务',
        '上次生成failed:请修改描述后重新提交',
    )

    def test_k_shapes_are_refused(self):
        for text in self.REFUSED:
            with self.subTest(text=text):
                self.assertTrue(media_http._cjk_dominant(text))
                self.assertEqual(media_http.safe_upstream_text(text), '')

    def test_killed_and_oom_as_ordinary_words_survive(self):
        for text in self.READABLE:
            with self.subTest(text=text):
                self.assertEqual(media_http.safe_upstream_text(text), ' '.join(text.split()))


class ScopeRuleTests(unittest.TestCase):
    """review-media-error-text-k M2: negation / contrast / quotation get scope, not keywords.

    The seven failing inputs, paired with the neighbours that already worked and must keep working
    (quoted citation followed by a real assertion, configure followed by an assertion across a
    comma or in one clause, satisfied followed by an assertion, the accepted quoted instruction).
    """
    CASES = (
        # double negation asserts
        ('本次并非没有出现request timeout，请稍后重试', '媒体服务处理超时'),
        ('本次不可能没有发生rate limit hit请稍后重试', '请求过于频繁，请稍后再试'),
        # 不是X而是Y — the negation targets X, Y is the failure
        ('本次不是prompt is required而是rate limit hit请稍后重试', '请求过于频繁，请稍后再试'),
        # a negation reaches its object across the colon that introduces it
        ('本次没有出现：request timeout，请检查其他原因', 'keep'),
        # a quoted label with no assert verb is not a failure
        ('“prompt is required”只是标签文字，不是当前故障', 'keep'),
        # a newline is a clause boundary: the previous line cannot cover the next one
        ('提示词已填写\n当前仍报prompt is required请检查提交内容', '请填写提示词'),
        # …and the previous line's satisfied marker cannot cover it either, with no failure
        # pointer in front of the condition (review-media-error-text-l mutation anchor)
        ('提示词已填写\n本次仍显示prompt is required请检查提交内容', '请填写提示词'),
        ('参考图已上传\n但too many images仍然出现，请减少参考图', '参考图数量超出上限'),
        ('文档引用了“错误，request timeout”供阅读参考', 'keep'),
        # neighbours that already worked and must not regress
        ('帮助引用了“prompt is required”供阅读；当前实际错误为prompt is required请补全',
         '请填写提示词'),
        ('请先修改request timeout设置，当前仍报request timeout', '媒体服务处理超时'),
        ('请先修改request timeout设置但当前仍报request timeout请稍后重试', '媒体服务处理超时'),
        ('提示词已填写；当前仍报prompt is required请检查提交内容', '请填写提示词'),
        ('请补全输入『prompt is required』然后再提交', '请填写提示词'),
        ('本次未发生unsupported image format请检查其他原因', 'keep'),
    )

    def test_scope_rules(self):
        for text, want in self.CASES:
            with self.subTest(text=text):
                collapsed = ' '.join(text.split())
                expected = collapsed if want == 'keep' else want
                result = media_http.safe_upstream_text(text)
                self.assertEqual(result, expected)
                if want != 'keep':
                    self.assertNotIn(collapsed, result)


class NegationScopeMatrixTests(unittest.TestCase):
    """Negation word × {single, double, not-X-but-Y, negation+colon, negation+newline}.

    Every row pins both the filter outcome and the `_condition_state` value of each match, so a
    regression in the scope engine cannot hide behind a passing outcome.
    """
    ROWS = (
        # 单否定 -> negate
        ('本次未发生prompt is required，请检查其他原因', 'keep', 'negate'),
        ('本次没有出现prompt is required，请检查其他原因', 'keep', 'negate'),
        ('本次不是prompt is required，请检查其他原因', 'keep', 'negate'),
        ('本次并非prompt is required，请检查其他原因', 'keep', 'negate'),
        ('本次未出现prompt is required，请检查其他原因', 'keep', 'negate'),
        # 双重否定 -> assert
        ('本次并非没有出现prompt is required，请稍后重试', '请填写提示词', 'assert'),
        ('本次不可能没有发生prompt is required，请稍后重试', '请填写提示词', 'assert'),
        ('本次不是没有发生prompt is required，请稍后重试', '请填写提示词', 'assert'),
        ('本次并非未出现prompt is required，请稍后重试', '请填写提示词', 'assert'),
        # 不是X而是Y -> X negate, Y assert
        ('本次不是prompt is required而是rate limit hit请稍后重试', '请求过于频繁，请稍后再试',
         ('negate', 'assert')),
        ('本次不是缺少参考图，而是prompt is required，请补全输入', '请填写提示词', 'assert'),
        # 否定 + 冒号：否定的宾语就是后面的条件
        ('本次没有出现：prompt is required，请检查其他原因', 'keep', 'negate'),
        ('本次未发生：prompt is required，请检查其他原因', 'keep', 'negate'),
        # 否定 + 换行：否定只作用于本行
        ('本次未发生prompt is required\n请检查其他原因', 'keep', 'negate'),
        ('提示词已填写\n当前仍报prompt is required请检查提交内容', '请填写提示词', 'assert'),
        ('本次未发生request timeout\n当前仍报prompt is required请补全输入', '请填写提示词',
         ('negate', 'assert')),
        # coordinate clauses count one negation per condition
        ('本次未出现too many images，也未发生request timeout，请检查其他原因', 'keep',
         ('negate', 'negate')),
    )

    def test_negation_matrix(self):
        self.assertGreaterEqual(len(self.ROWS), 10)
        for text, want, states in self.ROWS:
            with self.subTest(text=text):
                collapsed = ' '.join(text.split())
                expected = collapsed if want == 'keep' else want
                self.assertEqual(media_http.safe_upstream_text(text), expected)
                form = '\n'.join(' '.join(line.split()) for line in text.splitlines())
                found = []
                for pattern, _copy in media_http._KNOWN_EN_MESSAGES:
                    for match in pattern.finditer(form):
                        found.append((match.start(),
                                      media_http._condition_state(form, match.start(),
                                                                  match.end())))
                found.sort()
                want_states = (states,) if isinstance(states, str) else states
                self.assertEqual([state for _pos, state in found], list(want_states))


class QuoteScopeMatrixTests(unittest.TestCase):
    """Quotation × {citation verb, for-reading, mere label, assert verb, bare quotes}.

    Ruling: a quote mark never turns an assertion into a citation — `请补全输入『X』再提交` is
    still the failure. Only a quoted phrase with no assert verb in its clause is a citation.
    """
    ROWS = (
        ('帮助引用了“prompt is required”供阅读参考', 'keep', 'quote'),
        ('文档引用了“prompt is required”供阅读参考', 'keep', 'quote'),
        # review-media-error-text-l M2: `只是标签` is now a postposed negation phrase, so this
        # row is denied rather than merely cited — the outcome (kept verbatim) is unchanged
        ('“prompt is required”只是标签文字，不是当前故障', 'keep', 'negate'),
        ('请补全输入『prompt is required』然后再提交', '请填写提示词', 'assert'),
        ('界面显示“prompt is required”一行提示', 'keep', 'quote'),
        ('文档引用了“错误，prompt is required”供阅读参考', 'keep', 'quote'),
        ('帮助引用了“prompt is required”供阅读；当前实际错误为prompt is required请补全',
         '请填写提示词', ('quote', 'assert')),
    )

    def test_quote_matrix(self):
        self.assertGreaterEqual(len(self.ROWS), 5)
        for text, want, states in self.ROWS:
            with self.subTest(text=text):
                collapsed = ' '.join(text.split())
                expected = collapsed if want == 'keep' else want
                self.assertEqual(media_http.safe_upstream_text(text), expected)
                found = []
                for pattern, _copy in media_http._KNOWN_EN_MESSAGES:
                    for match in pattern.finditer(text):
                        found.append((match.start(),
                                      media_http._condition_state(text, match.start(),
                                                                  match.end())))
                found.sort()
                want_states = (states,) if isinstance(states, str) else states
                self.assertEqual([state for _pos, state in found], list(want_states))


class FatalVariantMatrixTests(unittest.TestCase):
    """Crash-shape variants: sign, case and hyphen (>=6), plus the two ordinary-word positives."""
    REFUSED = RuntimeFatalKTests.REFUSED
    READABLE = RuntimeFatalKTests.READABLE

    def test_variants_are_refused(self):
        self.assertGreaterEqual(len(self.REFUSED), 6)
        for text in self.REFUSED:
            with self.subTest(text=text):
                self.assertEqual(media_http.safe_upstream_text(text), '')

    def test_ordinary_word_positives(self):
        for text in self.READABLE:
            with self.subTest(text=text):
                self.assertEqual(media_http.safe_upstream_text(text), ' '.join(text.split()))


class RuntimeFatalKAcrossAllExitsTests(FourExitHarness):
    """The four review-media-error-text-k M1 leaks, pinned on all four exits."""
    REFUSED = RuntimeFatalKTests.REFUSED[:4]
    KEPT = ('任务被 killed 标记为已取消，请核对后重新提交',
            '内存 OOM 风险偏高，请降低尺寸后重试生成任务')

    def test_http_exit_refuses_the_k_shapes(self):
        for text in self.REFUSED:
            with self.subTest(text=text):
                self.assertNotIn(' '.join(text.split()), self.http_exit(text))

    def test_sse_exit_refuses_the_k_shapes(self):
        for text in self.REFUSED:
            with self.subTest(text=text):
                self.assertNotIn(' '.join(text.split()), self.sse_exit(text))

    def test_json_failed_exit_refuses_the_k_shapes(self):
        for text in self.REFUSED:
            with self.subTest(text=text):
                self.assertNotIn(' '.join(text.split()), str(self.failed_exit(text)))

    def test_json_uncertain_exit_refuses_the_k_shapes(self):
        for text in self.REFUSED:
            with self.subTest(text=text):
                self.assertNotIn(' '.join(text.split()), str(self.uncertain_exit(text)))

    def test_ordinary_word_copy_reaches_all_four_exits(self):
        for text in self.KEPT:
            with self.subTest(text=text):
                for exit_text in self.exits(text):
                    self.assertIn(' '.join(text.split()), exit_text)


class RuntimeFatalWordFormTests(unittest.TestCase):
    """review-media-error-text-l M1: one shape name, many spellings and many dashes.

    The shape is stated once and the regex carries the variation — blank / hyphen / underscore
    between the words, optional plural -s on the head noun — and every Unicode dash folds to an
    ASCII `-` for the checks (NFKC does not fold U+2212 MINUS SIGN).
    """
    REFUSED = (
        '后台返回unhandled exceptions请联系管理员处理',
        '后台返回unhandled-exception请联系管理员处理',
        '后台返回unhandled_Exception请联系管理员处理',
        '后台发生OOM-Killers请联系管理员处理',
        '后台发生OOMKiller请联系管理员处理',
        '后台返回access violations请运维检查进程',
        '后台返回access_violation请运维检查进程',
        '后台返回Bus errors请运维检查进程',
        '后台返回kernel panics请运维检查进程',
        '后台返回died-from-signal 9请运维检查进程',
        '线程崩溃Exception in threads请运维检查进程',
        '进程返回Killed −9请管理员检查',
        '进程返回Killed ‐9请管理员检查',
        '进程返回Killed ‑9请管理员检查',
        '进程返回Killed –9请管理员检查',
        '进程返回Killed —9请管理员检查',
        '进程返回Killed －9请管理员检查',
        '后台返回Killed+9请联系管理员处理',
    )
    READABLE = (
        '界面上的 killed 表示任务已取消，请核对后重新提交',
        '帮助页介绍 OOM 风险，请先降低生成尺寸',
        '上次生成failed:请修改描述后重新提交',
        '任务状态 status 200 已完成，请查看生成结果',
    )

    def test_word_forms_are_refused(self):
        self.assertGreaterEqual(len(self.REFUSED), 6)
        for text in self.REFUSED:
            with self.subTest(text=text):
                self.assertTrue(media_http._cjk_dominant(text))
                self.assertEqual(media_http.safe_upstream_text(text), '')

    def test_dashes_fold_for_the_checks_only(self):
        # the fold lives in _width_normalised; the string a user could be shown is untouched
        self.assertEqual(media_http._width_normalised('Killed −9'), 'Killed -9')
        self.assertEqual(media_http._width_normalised('Killed —9'), 'Killed -9')

    def test_ordinary_word_positives(self):
        for text in self.READABLE:
            with self.subTest(text=text):
                self.assertEqual(media_http.safe_upstream_text(text), ' '.join(text.split()))


class ScopeAttributionTests(unittest.TestCase):
    """review-media-error-text-l M2: quote, negation and contrast each keep their own scope.

    The six failing inputs, paired with the neighbours that already worked and must keep working:
    a quoted phrase an assert verb introduces outside the quotes is still the failure, a quoted
    help example is not, and an assert verb in front of the condition stops a trailing 不是… from
    denying it.
    """
    CASES = (
        # a negation outranks the quote mark and the trailing 报错
        ('本次没有发生“request timeout”报错，请检查其他原因', 'keep'),
        # an assert verb inside the quotation belongs to the cited text
        ('帮助示例“请修改prompt is required”仅供阅读参考', 'keep'),
        ('帮助示例“请修改prompt is required”仅供阅读；当前仍报prompt is required请补全',
         '请填写提示词'),
        # a postposed predicate is matched as a phrase, not with a one-character leash
        ('request timeout与此无关，请检查图片格式后重新提交', 'keep'),
        ('本次request timeout无关，请检查其他原因', 'keep'),
        ('prompt is required与本次生成没有关系，请检查提示词以外的参数', 'keep'),
        ('unsupported image format不相干，请检查其他原因', 'keep'),
        ('too many images只是文档里的标签，请忽略其余说明', 'keep'),
        # …but an assert verb in front of the condition is not outvoted by a trailing 不是
        ('本次错误为prompt is required，不是网络问题，请补全输入', '请填写提示词'),
        # a single negation reaches its object across a long attributive run and a colon
        ('本次未发生此前日志里反复提及并且需要特别检查的那一种错误：request timeout，请核对其他原因',
         'keep'),
        # 与其说X不如说Y — Y is what is claimed
        ('与其说是prompt is required不如说是rate limit hit，请稍后重试', '请求过于频繁，请稍后再试'),
        ('与其说是模型不可用，不如说是prompt is required，请补全输入', '请填写提示词'),
        # documentation names the phrase; the pointer names the real failure
        ('与prompt is required有关的是说明文字，真正错误是request timeout，请稍后重试',
         '媒体服务处理超时'),
        ('本次仍报prompt is required，真正错误是rate limit hit，请稍后重试',
         '请求过于频繁，请稍后再试'),
        # neighbours that already worked
        ('当前仍报“request timeout”，请稍后重试生成任务', '媒体服务处理超时'),
        ('文档中的“request timeout”仅供阅读参考', 'keep'),
        ('本次失败与prompt is required无关，请检查图片内容', 'keep'),
        ('网络没有异常且模型没有问题，本次错误为prompt is required，请补全', '请填写提示词'),
        ('本次未出现prompt is required\n当前仍报request timeout，请稍后重试', '媒体服务处理超时'),
    )

    def test_scope_attribution(self):
        for text, want in self.CASES:
            with self.subTest(text=text):
                collapsed = ' '.join(text.split())
                expected = collapsed if want == 'keep' else want
                result = media_http.safe_upstream_text(text)
                self.assertEqual(result, expected)
                if want != 'keep':
                    self.assertNotIn(collapsed, result)


class ScopeAttributionMatrixTests(unittest.TestCase):
    """Self-audit matrices for the l rules: word form × separator × plural, postposed predicate
    phrases, and the pointer that picks the target when several entries match.

    Every row pins the filter outcome *and* the `_condition_state` of each table match, so a
    regression cannot hide behind a passing outcome.
    """
    POSTPOSED = (
        ('request timeout与此无关，请检查图片格式后重新提交', 'keep', 'negate'),
        ('本次request timeout无关，请检查其他原因', 'keep', 'negate'),
        ('prompt is required与本次生成没有关系，请检查提示词以外的参数', 'keep', 'negate'),
        ('unsupported image format不相干，请检查其他原因', 'keep', 'negate'),
        ('too many images只是文档里的标签，请忽略其余说明', 'keep', 'negate'),
        ('本次错误为prompt is required，不是网络问题，请补全输入', '请填写提示词', 'assert'),
        ('当前仍报“request timeout”，请稍后重试生成任务', '媒体服务处理超时', 'assert'),
    )
    POINTER = (
        ('与prompt is required有关的是说明文字，真正错误是request timeout，请稍后重试',
         '媒体服务处理超时', ('configure', 'assert')),
        ('本次仍报prompt is required，真正错误是rate limit hit，请稍后重试',
         '请求过于频繁，请稍后再试', ('assert', 'assert')),
        ('与其说是prompt is required不如说是rate limit hit，请稍后重试',
         '请求过于频繁，请稍后再试', ('negate', 'assert')),
        ('帮助示例“请修改prompt is required”仅供阅读参考', 'keep', 'quote'),
        ('本次没有发生“request timeout”报错，请检查其他原因', 'keep', 'negate'),
    )

    def _states(self, text):
        form = '\n'.join(' '.join(line.split()) for line in text.splitlines())
        found = []
        for pattern, _copy in media_http._KNOWN_EN_MESSAGES:
            for match in pattern.finditer(form):
                found.append((match.start(),
                              media_http._condition_state(form, match.start(), match.end())))
        found.sort()
        return [state for _pos, state in found]

    def _check(self, rows):
        for text, want, states in rows:
            with self.subTest(text=text):
                collapsed = ' '.join(text.split())
                expected = collapsed if want == 'keep' else want
                self.assertEqual(media_http.safe_upstream_text(text), expected)
                want_states = (states,) if isinstance(states, str) else states
                self.assertEqual(self._states(text), list(want_states))

    def test_postposed_predicate_matrix(self):
        self.assertGreaterEqual(len(self.POSTPOSED), 5)
        self._check(self.POSTPOSED)

    def test_pointer_and_alternative_matrix(self):
        self.assertGreaterEqual(len(self.POINTER), 5)
        self._check(self.POINTER)

    def test_word_form_matrix(self):
        rows = [(text, '') for text in RuntimeFatalWordFormTests.REFUSED]
        rows += [(text, ' '.join(text.split()))
                 for text in RuntimeFatalWordFormTests.READABLE]
        self.assertGreaterEqual(len(rows), 10)
        for text, expected in rows:
            with self.subTest(text=text):
                self.assertEqual(media_http.safe_upstream_text(text), expected)


class DescriptionAndPointerMTests(unittest.TestCase):
    """review-media-error-text-m: the 仅是 family, the description context, and a pointer that
    reaches its condition across the colon that introduces it.

    Paired throughout: an exclusion or a document context keeps the sentence verbatim, a stated
    failure is still translated.
    """
    CASES = (
        # M-1: postposed exclusion family (one mechanism, not one keyword per spelling)
        ('too many images仅是标签，请忽略该说明', 'keep'),
        ('too many images仅仅是界面上的标签，请忽略', 'keep'),
        ('rate limit hit不过是文档里的举例，请忽略', 'keep'),
        ('unsupported image format无非是错误示例里的写法，请核对实际原因', 'keep'),
        ('prompt is required只是说明文字，请忽略该提示', 'keep'),
        ('当前仍报too many images，请减少参考图数量', '参考图数量超出上限'),
        # M-2: a document word in front is not an assertion of the failure
        ('报错说明里的request timeout不代表本次故障，请核对原因', 'keep'),
        ('错误示例中的unsupported image format只是举例，请核对实际原因', 'keep'),
        ('故障说明里的rate limit hit供参考，请核对本次原因', 'keep'),
        ('当前报错request timeout请稍后', '媒体服务处理超时'),
        ('本次错误为prompt is required，不是网络问题，请补全输入', '请填写提示词'),
        # M-3: the colon belongs to the pointer, it does not cut it off
        ('帮助页说明rate limit hit；此前报过prompt is required；当前真正错误是：request timeout',
         '媒体服务处理超时'),
        ('当前真正错误是：prompt is required，请补全输入', '请填写提示词'),
        # without a pointer the documented entry stays non-assertive and table order stands
        ('帮助页说明rate limit hit；此前报过prompt is required，请补全输入', '请填写提示词'),
    )
    EXCLUSIVE = (
        ('too many images仅是标签，请忽略该说明', 'negate'),
        ('too many images仅仅是界面上的标签，请忽略', 'negate'),
        ('rate limit hit不过是文档里的举例，请忽略', 'negate'),
        ('unsupported image format无非是错误示例里的写法，请核对实际原因', 'negate'),
        ('prompt is required只是说明文字，请忽略该提示', 'negate'),
        ('too many images仅是说明，请忽略该说明', 'negate'),
        ('rate limit hit仅是示例，请核对本次原因', 'negate'),
    )
    DESCRIPTION = (
        ('报错说明里的request timeout不代表本次故障，请核对原因', 'negate'),
        ('报错说明里的request timeout，请核对原因', 'configure'),
        ('错误示例中的unsupported image format只是举例，请核对实际原因', 'negate'),
        ('故障说明里的rate limit hit供参考，请核对本次原因', 'configure'),
        ('说明文字rate limit hit供参考', 'configure'),
    )
    POINTER = (
        ('帮助页说明rate limit hit；此前报过prompt is required；当前真正错误是：request timeout',
         ('configure', 'assert', 'assert')),
        ('当前真正错误是：prompt is required，请补全输入', 'assert'),
        ('帮助页说明rate limit hit；此前报过prompt is required，请补全输入',
         ('configure', 'assert')),
    )

    def test_cases(self):
        for text, want in self.CASES:
            with self.subTest(text=text):
                collapsed = ' '.join(text.split())
                expected = collapsed if want == 'keep' else want
                self.assertEqual(media_http.safe_upstream_text(text), expected)

    def _states(self, text):
        found = []
        for pattern, _copy in media_http._KNOWN_EN_MESSAGES:
            for match in pattern.finditer(text):
                found.append((match.start(),
                              media_http._condition_state(text, match.start(), match.end())))
        found.sort()
        return [state for _pos, state in found]

    def _check_states(self, rows):
        for text, states in rows:
            with self.subTest(text=text):
                want = (states,) if isinstance(states, str) else states
                self.assertEqual(self._states(text), list(want))

    def test_exclusive_variants(self):
        self.assertGreaterEqual(len(self.EXCLUSIVE), 6)
        self._check_states(self.EXCLUSIVE)

    def test_description_context(self):
        self.assertGreaterEqual(len(self.DESCRIPTION), 4)
        self._check_states(self.DESCRIPTION)

    def test_pointer_across_colon(self):
        self.assertGreaterEqual(len(self.POINTER), 3)
        self._check_states(self.POINTER)


if __name__ == '__main__':
    unittest.main()
