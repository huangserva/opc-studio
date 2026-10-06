"""Fake 4090 media API: the workbench media path must be pure HTTP.

Covers submit/poll/download with sha256 verification, idempotency and lost submit
responses, cancel races (barrier-synchronised), restart adoption, legacy SSH jobs,
reference uploads with declared metadata, and honest unknown states on transport
failures. The fake API always runs on real server threads: only the workbench's own
background seams (_spawn / _spawn_observer) are controlled, never the shared
threading module.
"""
import base64
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from server import Store
from generation import service
from generation.media_http import MediaApi, MediaApiError, MediaApiUnknown, resolve_config
from generation.service import Generation

# a real, PIL-decodable PNG: the workbench validates result bytes the same way it
# does in production, so a hand-written header stub would be rejected for the wrong reason
_buffer = io.BytesIO()
Image.new('RGB', (32, 32), (9, 30, 60)).save(_buffer, format='PNG')
PNG = _buffer.getvalue()


_VIDEO = None


def tiny_mp4():
    """A real, probeable 29-frame MP4: the workbench validates video bytes with FFmpeg."""
    global _VIDEO
    if _VIDEO is None:
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / 'sample.mp4'
            subprocess.run(['ffmpeg', '-y', '-loglevel', 'error', '-f', 'lavfi',
                            '-i', 'color=c=black:s=128x128:r=24', '-frames:v', '29',
                            '-pix_fmt', 'yuv420p', str(target)], check=True)
            _VIDEO = target.read_bytes()
    return _VIDEO


class FakeApi:
    def __init__(self):
        self.jobs = {}
        self.by_key = {}
        self.fingerprints = {}
        self.files = {}
        self.calls = []
        self.counter = 0
        self.script = ['running', 'succeeded']
        self.output = PNG
        self.result_sha = None
        self.result_bytes = None
        self.drop_submit_response = False
        self.transport_failures = 0
        self.cancel_mode = 'accepted'
        # independent of cancel behaviour: a refused cancel must not freeze the job
        self.freeze_progress = False
        self.cancelled = set()
        self.uploaded = []
        self.events = [('running', {'prompt_id': 'p'}), ('progress', {'step': 1}),
                       ('succeeded', {'bytes': 10})]
        self.drop_events_after = None      # cut the SSE stream after N served events
        self.event_requests = []           # 'since' cursor of every events request
        self.lock = threading.Lock()

    def next_id(self):
        self.counter += 1
        return 'api%04d' % self.counter

    def fingerprint(self, payload):
        return hashlib.sha256(json.dumps(
            {'model': payload.get('model'),
             'params': {k: v for k, v in payload.items() if k != 'request_id'}},
            sort_keys=True).encode()).hexdigest()

    def public(self, job):
        return {'id': job['id'], 'kind': job['kind'], 'model': job['params'].get('model'),
                'status': job['status'], 'request_id': job['request_id'], 'params': job['params'],
                'error': job.get('error'), 'uncertain_reason': job.get('uncertain_reason'),
                'output_sha256': job.get('output_sha256'), 'output_bytes': job.get('output_bytes'),
                'events_url': '/jobs/%s/events' % job['id'],
                'result_url': '/jobs/%s/result' % job['id'] if job.get('output_sha256') else None}


def build_handler(api):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'

        def log_message(self, *args):
            pass

        def send_json(self, status, payload):
            body = json.dumps(payload, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def read_body(self):
            length = int(self.headers.get('Content-Length') or 0)
            chunks, received = b'', 0
            while received < length:
                chunk = self.rfile.read(min(65536, length - received))
                if not chunk:
                    break
                chunks += chunk
                received += len(chunk)
            return chunks

        def route(self, method):
            path = self.path.split('?')[0]
            query = self.path.split('?')[1] if '?' in self.path else ''
            with api.lock:
                api.calls.append((method, path, dict(self.headers)))
            if path == '/health':
                return self.send_json(200, {'status': 'ok'})
            if path in ('/backends/health', '/backends/inventory'):
                if api.transport_failures:
                    api.transport_failures -= 1
                    return self.send_json(503, {'error': 'probe unavailable'})
                return self.send_json(200, {'checked_at': '2026-10-06T07:00:00+00:00', 'services': {
                    'qi21': {'model': 'qi21', 'status': 'ok', 'connected': True,
                             'system': {'comfyui_version': '0.3.60'}, 'devices': [],
                             'queue': {'running': 0, 'pending': 0, 'busy': False}, 'errors': {},
                             'checked_at': '2026-10-06T07:00:00+00:00'},
                    'krea2': {'model': 'krea2', 'status': 'unavailable', 'connected': False,
                              'system': {}, 'devices': [], 'queue': None,
                              'errors': {'/queue': 'connection-refused'}}}})
            if path == '/files' and method == 'POST':
                body = self.read_body()
                claimed = (self.headers.get('X-Sha256') or '').lower()
                if claimed != hashlib.sha256(body).hexdigest():
                    return self.send_json(400, {'error': 'sha256 校验不一致'})
                file_id = api.next_id()
                api.uploaded.append({'kind': self.headers.get('X-File-Kind'),
                                     'name': self.headers.get('X-File-Name'),
                                     'meta': json.loads(self.headers.get('X-File-Meta') or '{}'),
                                     'bytes': len(body), 'file_id': file_id,
                                     # the deployed API sizes the upload from Content-Length;
                                     # a chunked body would be hashed as empty
                                     'transfer_encoding': self.headers.get('Transfer-Encoding'),
                                     'content_length': self.headers.get('Content-Length')})
                api.files[file_id] = body
                declared = json.loads(self.headers.get('X-File-Meta') or '{}')
                return self.send_json(201, {'file_id': file_id, 'bytes': len(body),
                                            'kind': self.headers.get('X-File-Kind'),
                                            'measured': declared, 'meta': declared})
            if path == '/jobs' and method == 'POST':
                payload = json.loads(self.read_body())
                key = payload.get('request_id')
                mark = api.fingerprint(payload)
                with api.lock:
                    known = api.by_key.get(key)
                    if known and api.fingerprints[known] != mark:
                        return self.send_json(409, {'error': '同一 request_id 已用于不同参数'})
                    if known:
                        return self.send_json(200, api.public(api.jobs[known]))
                    job_id = api.next_id()
                    api.jobs[job_id] = {'id': job_id, 'status': 'queued', 'params': payload,
                    'output_type': 'video/mp4' if 'frames' in payload else 'image/png',
                                        'request_id': key, 'kind': 'image', 'step': 0,
                                        'output_sha256': None, 'output_bytes': None}
                    api.by_key[key] = job_id
                    api.fingerprints[job_id] = mark
                if api.drop_submit_response:
                    # the server accepted the job but the client never sees the reply
                    self.close_connection = True
                    try:
                        self.connection.close()
                    except Exception:
                        pass
                    return
                return self.send_json(201, api.public(api.jobs[job_id]))
            if path == '/jobs' and method == 'GET':
                return self.send_json(200, {'jobs': [api.public(job) for job in api.jobs.values()]})
            match = re.fullmatch(r'/jobs/([a-z0-9]+)', path)
            if match and method == 'GET':
                job = api.jobs.get(match.group(1))
                if not job:
                    return self.send_json(404, {'error': '未知任务'})
                if api.transport_failures:
                    api.transport_failures -= 1
                    return self.send_json(502, {'error': 'gateway down'})
                if (job['status'] not in ('succeeded', 'failed', 'cancelled', 'uncertain')
                        and job['id'] not in api.cancelled and not api.freeze_progress):
                    if job['step'] < len(api.script):
                        job['status'] = api.script[job['step']]
                        job['step'] += 1
                if job['status'] == 'succeeded':
                    job['output_sha256'] = api.result_sha or hashlib.sha256(api.output).hexdigest()
                    job['output_bytes'] = len(api.output)
                return self.send_json(200, api.public(job))
            match = re.fullmatch(r'/jobs/([a-z0-9]+)/cancel', path)
            if match and method == 'POST':
                job = api.jobs.get(match.group(1))
                if not job:
                    return self.send_json(404, {'error': '未知任务'})
                if api.cancel_mode == 'refused':
                    return self.send_json(409, {'error': '任务已在执行，无法取消'})
                if api.cancel_mode == 'late':
                    # the render was already finishing: the API acknowledges the stop but
                    # the job completes anyway
                    return self.send_json(200, api.public(job))
                if api.cancel_mode == 'unknown':
                    self.close_connection = True
                    try:
                        self.connection.close()
                    except Exception:
                        pass
                    return
                api.cancelled.add(job['id'])
                job['status'] = 'cancelled'
                return self.send_json(200, api.public(job))
            match = re.fullmatch(r'/jobs/([a-z0-9]+)/events', path)
            if match and method == 'GET':
                since = 0
                for part in query.split('&'):
                    if part.startswith('since='):
                        since = int(part[6:] or 0)
                with api.lock:
                    api.event_requests.append(since)
                self.send_response(200)
                self.send_header('Content-Type', 'text/event-stream')
                self.send_header('Connection', 'close')
                self.close_connection = True
                self.end_headers()
                served = 0
                for index, event in enumerate(api.events, start=1):
                    if index <= since:
                        continue          # the client asked to resume after this cursor
                    if api.drop_events_after is not None and served >= api.drop_events_after:
                        break             # drop the stream mid-flight
                    payload = json.dumps({'seq': index, 'ts': 'now', 'data': event[1]})
                    self.wfile.write(f"id: {index}\nevent: {event[0]}\ndata: {payload}\n\n".encode())
                    served += 1
                self.wfile.flush()
                return
            match = re.fullmatch(r'/jobs/([a-z0-9]+)/result', path)
            if match:
                job = api.jobs.get(match.group(1))
                if not job or not job.get('output_sha256'):
                    return self.send_json(409, {'error': '结果尚未就绪'})
                body = api.output if api.result_bytes is None else api.output[:api.result_bytes]
                self.send_response(200)
                self.send_header('Content-Type', job.get('output_type') or 'image/png')
                # deliberately lowercase: header lookup must be case-insensitive
                self.send_header('x-content-sha256', job['output_sha256'])
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                if method != 'HEAD':
                    self.wfile.write(body)
                return
            return self.send_json(404, {'error': 'no such route'})

        def do_GET(self):
            self.route('GET')

        def do_HEAD(self):
            self.route('HEAD')

        def do_POST(self):
            self.route('POST')

    return Handler


class TransportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.api = FakeApi()
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), build_handler(self.api))
        self.serve_thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.serve_thread.start()
        self.base = 'http://127.0.0.1:' + str(self.server.server_port)
        key = Path(self.temp.name) / 'media-api.key'
        key.write_text('test-token')
        os.chmod(key, 0o600)
        self.patcher = patch.dict(os.environ, {'MEDIA_API_BASE': self.base,
                                               'MEDIA_API_KEY_FILE': str(key),
                                               # hard isolation: never fall back to the
                                               # operator's real production configuration
                                               'MEDIA_API_CONFIG': str(
                                                   Path(self.temp.name) / 'no-such-config.json')})
        self.patcher.start()
        self.store = Store(self.temp.name)
        # only the workbench's own background seam is suppressed; the fake API keeps
        # real server threads, so HTTP behaviour stays real
        self.planned = []

        def no_spawn(_self, target, *args):
            self.planned.append((target, args))

        self.spawn_patch = patch.object(Generation, '_spawn', no_spawn)
        self.spawn_patch.start()
        self.generation = Generation(self.store)

    def tearDown(self):
        self.spawn_patch.stop()
        self.generation.close()
        self.server.shutdown()
        self.server.server_close()
        self.serve_thread.join(timeout=5)
        self.patcher.stop()
        self.temp.cleanup()

    def quiet_observer(self):
        return patch.object(Generation, '_spawn_observer', lambda *_: None)

    def payload(self, **extra):
        base = {'model': 'qi21', 'prompt': '湖面晨雾', 'size': '1024x1024', 'seed': 7}
        base.update(extra)
        return base

    def start_job(self, **extra):
        return self.generation.start(self.payload(**extra), origin='lab')

    def run_sync(self, job_id, generation=None):
        generation = generation or self.generation
        with self.quiet_observer(), patch('generation.service.time.sleep'):
            generation.run(job_id, None)
        return generation.public(job_id)

    def posts(self):
        return [c for c in self.api.calls if c[0] == 'POST' and c[1] == '/jobs']

    # ---------- configuration ----------
    def test_missing_configuration_is_explicit_and_never_falls_back_to_ssh(self):
        with patch.dict(os.environ, {'MEDIA_API_BASE': '', 'MEDIA_API_KEY_FILE': '',
                                     # never read the operator's real production config
                                     'MEDIA_API_CONFIG': str(
                                         Path(self.temp.name) / 'no-such-config.json')},
                         clear=False):
            with self.assertRaisesRegex(MediaApiError, 'MEDIA_API_BASE'):
                resolve_config()
        source = (Path(__file__).resolve().parents[1] / 'generation' / 'service.py').read_text()
        self.assertNotIn("'ssh'", source)
        self.assertNotIn('subprocess.Popen', source)
        self.assertNotIn('bridge.py', source)

    def test_plaintext_base_is_refused_for_non_loopback(self):
        with patch.dict(os.environ, {'MEDIA_API_BASE': 'http://example.com/media/v1'}):
            with self.assertRaisesRegex(MediaApiError, 'https'):
                resolve_config()

    def test_wide_key_file_permissions_are_refused(self):
        key = Path(self.temp.name) / 'loose.key'
        key.write_text('token')
        os.chmod(key, 0o644)
        with patch.dict(os.environ, {'MEDIA_API_KEY_FILE': str(key)}):
            with self.assertRaisesRegex(MediaApiError, '600'):
                resolve_config()

    # ---------- happy path ----------
    def test_submit_poll_download_verifies_sha_and_records_api_job(self):
        job = self.start_job()
        result = self.run_sync(job['id'])
        self.assertEqual(result['status'], 'done')
        self.assertEqual(result['asset_file'], 'result.png')
        self.assertEqual(result['sha256'], hashlib.sha256(self.api.output).hexdigest())
        self.assertEqual(result['api_output_sha256'], hashlib.sha256(self.api.output).hexdigest())
        self.assertTrue((self.generation.folder / job['id'] / 'result.png').is_file())
        self.assertEqual(result['transport'], 'https')
        self.assertEqual(len(self.posts()), 1)
        sent = self.api.jobs[result['api_job_id']]['params']
        self.assertEqual(sent['model'], 'qwen-image-2.1')
        self.assertEqual(sent['seed'], 7)
        self.assertNotIn('slug', sent)          # workbench-only fields never travel
        self.assertNotIn('image_skill', sent)
        self.assertNotIn('_references', sent)

    def test_reference_uploads_declare_meta_and_use_file_ids(self):
        # the product only wires references for the video model, so the reference
        # contract is exercised on that path (image + references is rejected upstream)
        reference = self.generation.upload_reference(
            {'data': 'data:image/png;base64,' + base64.b64encode(PNG).decode(), 'name': '参考图'})
        job = self.start_job(model='h3', frames=56, size='768x448', workflow='r2v',
                             references={'images': [reference['id']]})
        self.assertEqual(job['kind'], 'video')
        plan = json.loads((self.generation.folder / job['id'] / 'reference-uploads.json').read_text())
        self.assertEqual({item['kind'] for item in plan}, {'image'})
        self.assertEqual({frozenset(item) for item in plan},
                         {frozenset({'id', 'kind', 'name', '_local_path', 'meta'})})
        with self.quiet_observer():
            api_job_id = self.generation.submit_http(job['id'], MediaApi())
        self.assertEqual(len(self.api.uploaded), 1)
        self.assertEqual(self.api.uploaded[0]['meta'].get('width'), 32)
        self.assertEqual(self.api.uploaded[0]['kind'], 'image')
        self.assertIsNone(self.api.uploaded[0]['transfer_encoding'])
        self.assertEqual(self.api.uploaded[0]['content_length'], str(self.api.uploaded[0]['bytes']))
        files = json.loads((self.generation.folder / job['id'] / 'reference-files.json').read_text())
        self.assertEqual(list(files.values()), [self.api.uploaded[0]['file_id']])
        sent = self.api.jobs[api_job_id]['params']
        self.assertEqual(sent['model'], 'minimax-h3')
        self.assertEqual(sent['workflow'], 'r2v')
        self.assertEqual(sent['frames'], 56)
        self.assertEqual(sent['references'], {'images': [self.api.uploaded[0]['file_id']]})
        # a restart reuses the recorded file ids instead of uploading the asset twice
        fresh = Generation(self.store)
        with self.quiet_observer():
            replayed = fresh.submit_http(job['id'], MediaApi())
        self.assertEqual(replayed, api_job_id)
        self.assertEqual(len(self.api.uploaded), 1)
        self.assertEqual(len(self.posts()), 2)   # idempotent replay, never a second job

    # ---------- idempotency ----------
    def test_same_key_replays_and_different_params_are_rejected(self):
        api = MediaApi()
        first = api.submit({'model': 'qwen-image-2.1', 'prompt': 'a', 'size': '1024x1024', 'seed': 1},
                           'stable-key-0001')
        again = api.submit({'model': 'qwen-image-2.1', 'prompt': 'a', 'size': '1024x1024', 'seed': 1},
                           'stable-key-0001')
        self.assertEqual(first['id'], again['id'])
        with self.assertRaisesRegex(MediaApiError, '不同参数'):
            api.submit({'model': 'qwen-image-2.1', 'prompt': 'b', 'size': '1024x1024', 'seed': 1},
                       'stable-key-0001')
        self.assertEqual(len(self.api.jobs), 1)

    def test_lost_submit_response_is_adopted_without_second_submit(self):
        self.api.drop_submit_response = True
        job = self.start_job()
        with self.quiet_observer(), patch('generation.service.time.sleep'):
            self.generation.run(job['id'], None)
        result = self.generation.public(job['id'])
        self.assertEqual(result['status'], 'uncertain')
        self.assertIsNone(result.get('api_job_id'))
        self.assertEqual(len(self.posts()), 1)
        self.api.drop_submit_response = False
        self.api.script = ['succeeded']
        adopted = self.generation.reconcile(job['id'])
        self.assertTrue(adopted['api_job_id'])
        final = self.run_sync(job['id'])
        self.assertEqual(final['status'], 'done')
        self.assertEqual(len(self.posts()), 1)

    def test_lost_submit_and_no_server_job_stays_uncertain_without_resubmit(self):
        self.api.drop_submit_response = True
        job = self.start_job()
        with self.quiet_observer(), patch('generation.service.time.sleep'):
            self.generation.run(job['id'], None)
        self.api.by_key.clear()          # the server cannot be shown to have the job
        self.api.jobs.clear()
        self.generation.update(job['id'], status='uncertain')
        with self.assertRaisesRegex(ValueError, '仍未能定位'):
            self.generation.reconcile(job['id'])
        self.assertEqual(len(self.posts()), 1)
        self.assertEqual(self.generation.public(job['id'])['status'], 'uncertain')

    def test_transport_failure_while_polling_ends_uncertain_not_failed(self):
        job = self.start_job()
        self.api.transport_failures = 99
        with self.quiet_observer(), patch('generation.service.time.sleep'):
            self.generation.run(job['id'], None)
        result = self.generation.public(job['id'])
        self.assertEqual(result['status'], 'uncertain')
        self.assertIn('请勿重复提交', result['connection_warning'])
        self.assertIsNone(result.get('error'))

    def test_wait_budget_exhaustion_is_uncertain_not_error(self):
        job = self.start_job()
        clock = {'t': 0.0}

        class Clock:
            @staticmethod
            def monotonic():
                clock['t'] += 3601
                return clock['t']

            @staticmethod
            def sleep(_):
                pass

        with patch('generation.service.time', Clock()), self.quiet_observer():
            self.generation.run(job['id'], None)
        result = self.generation.public(job['id'])
        self.assertEqual(result['status'], 'uncertain')
        self.assertIn('60 分钟', result['connection_warning'])

    # ---------- results ----------
    def test_corrupt_result_is_unknown_and_never_done(self):
        job = self.start_job()
        self.api.result_sha = hashlib.sha256(b'something-else').hexdigest()
        result = self.run_sync(job['id'])
        self.assertEqual(result['status'], 'uncertain')
        self.assertFalse((self.generation.folder / job['id'] / 'result.png').exists())

    def test_truncated_result_is_unknown_and_leaves_no_partial_asset(self):
        job = self.start_job()
        self.api.result_bytes = 20
        result = self.run_sync(job['id'])
        self.assertEqual(result['status'], 'uncertain')
        self.assertEqual(list((self.generation.folder / job['id']).glob('result.*')), [])

    # ---------- cancel ----------
    def test_cancel_before_submit_prevents_the_api_job(self):
        job = self.start_job()
        stopped = self.generation.cancel(job['id'])
        self.assertEqual(stopped['status'], 'cancelled')
        self.assertIn('服务器未收到生成请求', stopped['connection_warning'])
        self.run_sync(job['id'])
        self.assertEqual(self.generation.public(job['id'])['status'], 'cancelled')
        self.assertEqual(self.posts(), [])

    def test_cancel_after_submit_uses_the_api_and_records_state(self):
        job = self.start_job()
        with self.quiet_observer():
            self.generation.submit_http(job['id'], MediaApi())
        stopped = self.generation.cancel(job['id'])
        self.assertEqual(stopped['status'], 'cancelled')
        self.assertTrue([c for c in self.api.calls if c[1].endswith('/cancel')])

    def test_cancel_during_upload_stops_before_submit(self):
        reference = self.generation.upload_reference(
            {'data': 'data:image/png;base64,' + base64.b64encode(PNG).decode()})['id']
        job = self.start_job(model='h3', frames=56, size='768x448', workflow='r2v',
                             references={'images': [reference]})
        in_upload, gate = threading.Event(), threading.Event()
        original = MediaApi.upload

        def hook(self_api, path, kind, name, meta=None, timeout=180):
            in_upload.set()
            gate.wait(5)
            return original(self_api, path, kind, name, meta, timeout)

        worker = threading.Thread(target=self.generation.run, args=(job['id'], None))
        with patch.object(MediaApi, 'upload', hook), self.quiet_observer():
            worker.start()
            self.assertTrue(in_upload.wait(5), 'upload never started')
            stopped = self.generation.cancel(job['id'])
            gate.set()
            worker.join(10)
        self.assertFalse(worker.is_alive())
        # nothing had been POSTed yet (only file staging happened), so the honest answer
        # is a local stop that states the server never received a generation request
        self.assertEqual(stopped['status'], 'cancelled')
        self.assertIn('服务器未收到生成请求', stopped['connection_warning'])
        self.assertTrue(self.generation.public(job['id'])['cancel_requested'])
        final = self.generation.public(job['id'])
        self.assertEqual(final['status'], 'cancelled')
        self.assertEqual(self.posts(), [])
        # the staged upload is not turned into a generation job by a later pass
        with self.quiet_observer(), patch('generation.service.time.sleep'):
            self.generation.run(job['id'], None)
        self.assertEqual(self.generation.public(job['id'])['status'], 'cancelled')
        self.assertEqual(self.posts(), [])

    def test_cancel_does_not_fake_local_cancel_once_submit_crossed_the_gate(self):
        job = self.start_job()
        self.generation.update(job['id'], submit_state='submitting')
        stopped = self.generation.cancel(job['id'])
        self.assertNotEqual(stopped['status'], 'cancelled')
        self.assertIn('已登记停止意图', stopped['connection_warning'])
        self.assertTrue(self.generation.public(job['id'])['cancel_requested'])

    def test_cancel_then_restart_replays_intent_on_the_adopted_job(self):
        self.api.drop_submit_response = True
        job = self.start_job()
        with self.quiet_observer(), patch('generation.service.time.sleep'):
            self.generation.run(job['id'], None)
        self.generation.cancel(job['id'])
        self.api.drop_submit_response = False
        # restart: a fresh instance adopts the same API job and must re-apply the stop
        fresh = Generation(self.store)
        with self.quiet_observer(), patch('generation.service.time.sleep'):
            fresh.run(job['id'], None)
        final = fresh.public(job['id'])
        self.assertEqual(final['status'], 'cancelled')
        self.assertEqual(len(self.posts()), 1)
        self.assertIn(final['api_job_id'], self.api.cancelled)

    def test_refused_cancel_is_reported_and_never_claimed(self):
        job = self.start_job()
        with self.quiet_observer():
            self.generation.submit_http(job['id'], MediaApi())
        self.api.cancel_mode = 'refused'
        with self.assertRaisesRegex(MediaApiError, '无法取消'):
            self.generation.cancel(job['id'])
        self.assertTrue(self.generation.public(job['id'])['cancel_requested'])

    def test_rejected_pending_cancel_on_replay_does_not_stop_generation(self):
        job = self.start_job()
        with self.quiet_observer():
            self.generation.submit_http(job['id'], MediaApi())
        self.generation.update(job['id'], status='uncertain', cancel_requested=True,
                               api_status='running')
        self.api.cancel_mode = 'refused'
        self.api.script = ['running', 'succeeded']
        result = self.run_sync(job['id'])
        self.assertEqual(result['status'], 'done')
        self.assertIn('停止请求被服务器拒绝', result['connection_warning'])

    # ---------- restart and legacy ----------
    def test_restart_resumes_existing_api_job_without_resubmitting(self):
        job = self.start_job()
        with self.quiet_observer():
            self.generation.submit_http(job['id'], MediaApi())
        self.generation.update(job['id'], status='running')
        fresh = Generation(self.store)
        with self.quiet_observer(), patch('generation.service.time.sleep'):
            fresh.run(job['id'], None)
        self.assertEqual(fresh.public(job['id'])['status'], 'done')
        self.assertEqual(len(self.posts()), 1)

    def test_legacy_ssh_job_becomes_uncertain_and_is_not_adopted(self):
        job_id = 'legacy' + '0' * 24
        folder = self.generation.folder / job_id
        folder.mkdir(parents=True)
        (folder / 'job.json').write_text(json.dumps({
            'id': job_id, 'model': 'qi21', 'kind': 'image', 'status': 'running',
            'prompt_id': 'comfy-prompt-1', 'created_at': service.now(), 'settings': {'seed': 1}}))
        fresh = Generation(self.store)
        self.assertEqual(fresh.public(job_id)['status'], 'uncertain')
        self.assertEqual(fresh.public(job_id)['transport'], 'legacy-ssh')
        self.assertEqual(self.posts(), [])
        with self.assertRaisesRegex(ValueError, '旧 SSH'):
            fresh.cancel(job_id)
        with self.assertRaisesRegex(ValueError, '旧 SSH'):
            fresh.reconcile(job_id)

    def test_legacy_job_without_prompt_id_is_uncertain_not_error(self):
        job_id = 'legacy2' + '0' * 23
        folder = self.generation.folder / job_id
        folder.mkdir(parents=True)
        (folder / 'job.json').write_text(json.dumps({
            'id': job_id, 'model': 'qi21', 'kind': 'image', 'status': 'connecting',
            'created_at': service.now(), 'settings': {'seed': 1}}))
        fresh = Generation(self.store)
        self.assertEqual(fresh.public(job_id)['status'], 'uncertain')
        self.assertEqual(fresh.public(job_id)['transport'], 'legacy-ssh')

    def test_uncertain_https_job_is_reconciled_on_restart(self):
        job = self.start_job()
        with self.quiet_observer():
            self.generation.submit_http(job['id'], MediaApi())
        self.generation.update(job['id'], status='uncertain', submit_state='unknown')
        fresh = Generation(self.store)
        with self.quiet_observer(), patch('generation.service.time.sleep'):
            fresh.run(job['id'], None)
        self.assertEqual(fresh.public(job['id'])['status'], 'done')
        self.assertEqual(len(self.posts()), 1)

    # ---------- read-only health semantics ----------
    def test_backend_health_reports_unknown_on_transport_failure(self):
        self.api.transport_failures = 1
        document = self.generation.backend_health(None)
        for item in document['services'].values():
            self.assertEqual(item['status'], 'unknown')
            self.assertIsNone(item['connected'])
        self.assertEqual(document['inference_status']['status'], 'unknown')

    def test_backend_health_uses_server_probe_and_checked_at(self):
        document = self.generation.backend_health(None)
        self.assertEqual(document['services']['qi21']['status'], 'ok')
        self.assertEqual(document['services']['krea2']['status'], 'unavailable')
        self.assertEqual(document['transport'], 'https')
        self.assertTrue(document['checked_at'])

    def test_health_reports_unknown_when_api_unreachable(self):
        with patch.dict(os.environ, {'MEDIA_API_BASE': 'http://127.0.0.1:1'}):
            generation = Generation(self.store)
            result = generation.health('qi21')
        self.assertEqual(result['status'], 'unknown')
        self.assertIsNone(result['connected'])

    def _api_reference_table(self, job):
        """Mirror the deployed scheduler._descriptor output for one submitted job.

        The 4090 API resolves each public file id into {id, kind, remote_file, width, height,
        duration, source_duration, frames, has_audio, name[, imageB64]} and rejects anything
        else, including the workbench's internal keys. Feeding that exact shape makes the
        contract test exercise the server's real reference rules (32-multiple sizes, 24 fps
        17k+5 video frames, audio duration) instead of treating the payload as opaque.
        """
        extension = {'image': 'png', 'video': 'mp4', 'audio': 'wav'}
        sent = self.api.jobs[job['api_job_id']]['params']
        wire = sent.get('references') or {}
        local = self.generation.resolve_references(job['api_params'].get('references') or {},
                                                   job['api_params'].get('frames'))
        table = {}

        def register(item, file_id, kind):
            self.assertTrue(file_id, '工作台未记录服务器素材编号')
            row = {'id': file_id, 'kind': kind,
                   'remote_file': f'article_studio_refs/{file_id}/reference.{extension[kind]}'}
            for key in ('width', 'height', 'duration', 'source_duration', 'frames', 'has_audio', 'name'):
                if item.get(key) is not None:
                    row[key] = item[key]
            if kind == 'image':
                row['imageB64'] = base64.b64encode(Path(item['_local_path']).read_bytes()).decode()
            table[file_id] = row

        for key in ('first', 'last'):
            if key in wire:
                register(local[key], wire[key], 'image')
        for key, kind in (('images', 'image'), ('videos', 'video'), ('audios', 'audio')):
            for item, file_id in zip(local[key], wire.get(key, [])):
                register(item, file_id, kind)
        return table, wire

    @staticmethod
    def _api_reference_resolver(table):
        def resolve(ids, frames):
            raw = {'images': [], 'videos': [], 'audios': []}
            for key in ('first', 'last'):
                if key in ids:
                    raw[key] = table[ids[key]]
            for key in ('images', 'videos', 'audios'):
                raw[key] = [table[identity] for identity in ids[key]]
            return raw
        return resolve

    def test_h3_semantic_params_are_accepted_by_the_deployed_api_contract(self):
        """Cross-boundary contract: the workbench must send what the 4090 API accepts.

        The fake API only stores the payload, so it cannot catch option-level rejections
        (the default H3 job used to send video_checkpoint=None → '无效H3 权重', and LoRA rows
        carrying name/file/modes). Every case here runs the real server builder over the
        payload that was actually put on the wire.
        """
        source = os.environ.get('MEDIA_API_SOURCE', '')
        if not os.path.isdir(source):
            self.skipTest('MEDIA_API_SOURCE 未指向 media-api-source 检出')
        import importlib
        _sys_path_added = False
        import sys as _sys
        _sys.path.insert(0, source)
        _sys_path_added = True
        try:
            workflows = importlib.import_module('workflows')
            upload = lambda: self.generation.upload_reference(
                {'data': 'data:image/png;base64,' + base64.b64encode(PNG).decode()})['id']
            first, last, image = upload(), upload(), upload()
            cases = {
                't2v 默认': {'model': 'h3', 'size': '768x432', 'frames': 56},
                'fl2v 自动默认': {'model': 'h3', 'workflow': 'fl2v', 'size': '768x448', 'frames': 124,
                                 'references': {'first': first, 'last': last}},
                'r2v 显式配方': {'model': 'h3', 'workflow': 'r2v', 'size': '768x448', 'frames': 56,
                                'video_checkpoint': 'singularity', 'video_accelerator': 'ref8',
                                'video_loras': [{'id': 'realism', 'strength': 0.8}],
                                'references': {'images': [image]}},
            }
            self.api.output = tiny_mp4()
            for label, extra in cases.items():
                with self.subTest(case=label):
                    job = self.start_job(**extra)
                    stored = self.generation.public(job['id'])
                    self.assertNotIn(None, stored['api_params'].values(), stored['api_params'])
                    self.assertLessEqual(set(stored['api_params']), workflows.ALLOWED_PARAMS['video'])
                    for row in stored['api_params'].get('video_loras', []):
                        self.assertEqual(set(row), {'id', 'strength'}, row)
                    result = self.run_sync(job['id'])
                    self.assertEqual(result['status'], 'done', result.get('error'))
                    sent = self.api.jobs[result['api_job_id']]['params']
                    self.assertNotIn(None, sent.values(), sent)
                    # POST /jobs strips request_id before validation (app.py) — mirror that
                    self.assertRegex(str(sent.get('request_id')), r'^[A-Za-z0-9_-]{8,64}$')
                    semantic = {k: v for k, v in sent.items() if k not in ('model', 'request_id')}
                    self.assertLessEqual(set(semantic), workflows.ALLOWED_PARAMS['video'])
                    # the exact option validation that used to reject the default job
                    workflows.option(workflows.H3_CHECKPOINTS,
                                     sent.get('video_checkpoint', 'singularity'), 'H3 权重')
                    workflows.option(workflows.H3_ACCELERATORS,
                                     sent.get('video_accelerator', 'ref8'), '加速 LoRA')
                    wire = sent.get('references') or {}
                    uploaded = set((result.get('reference_files') or {}).values())
                    on_wire = [value for role in wire.values() for value in
                               (role if isinstance(role, list) else [role])]
                    self.assertEqual(sorted(on_wire), sorted(uploaded),
                                     '提交的素材编号必须是 API 返回的 file_id')
                    table, _wire = self._api_reference_table(result)
                    graph, settings = workflows.build(
                        sent['model'], semantic,
                        result['api_job_id'], self._api_reference_resolver(table))
                    self.assertTrue(graph)
                    self.assertEqual(settings['workflow'], sent.get('workflow', 't2v'))
                    wired = json.dumps(graph, ensure_ascii=False)
                    for file_id, row in table.items():
                        # fl2v anchors travel as embedded content, r2v/i2v as LoadImage nodes
                        marker = f'article_studio_refs/{file_id}/reference'
                        content = (row.get('imageB64') or '')[:64]
                        self.assertTrue(marker in wired or (content and content in wired),
                                        '服务器图未加载工作台声明的参考素材: ' + file_id)
                    if sent.get('video_loras'):
                        self.assertTrue(any(key.startswith('video_lora_') for key in graph), sorted(graph))
        finally:
            if _sys_path_added:
                _sys.path.remove(source)

    def test_sse_cursor_resumes_after_a_dropped_stream(self):
        job = self.start_job()
        self.api.drop_events_after = 1        # first reconnect serves one event, then drops
        stop = threading.Event()
        worker = threading.Thread(target=self.generation.observe,
                                  args=(job['id'], 'api0001', stop), daemon=True)
        worker.start()
        deadline = time.time() + 10
        while time.time() < deadline and (self.generation.public(job['id']).get('event_cursor') or 0) < 1:
            time.sleep(0.05)
        self.api.drop_events_after = None     # the stream is healthy again
        deadline = time.time() + 20
        while time.time() < deadline and len(self.api.event_requests) < 2:
            time.sleep(0.1)
        stop.set()
        worker.join(20)
        self.assertFalse(worker.is_alive())
        final = self.generation.public(job['id'])
        self.assertEqual(final.get('event_cursor'), 3)
        # the reconnect resumed after the last consumed event instead of replaying all
        self.assertEqual(self.api.event_requests, [0, 1])
        # progress events never decide completion; polling stays authoritative
        self.assertNotEqual(final['status'], 'done')

    def test_natural_completion_beats_a_registered_stop_and_says_so(self):
        job = self.start_job()
        with self.quiet_observer():
            self.generation.submit_http(job['id'], MediaApi())
        self.generation.update(job['id'], cancel_requested=True, cancel_requested_at=service.now(),
                               status='running')
        self.api.cancel_mode = 'late'
        self.api.script = ['running', 'succeeded']
        result = self.run_sync(job['id'])
        self.assertEqual(result['status'], 'done')
        self.assertIn('在此之前已完成生成', result['connection_warning'])

    def test_progress_events_map_to_phases_without_invented_percent(self):
        job = self.start_job()
        stop = threading.Event()
        worker = threading.Thread(target=self.generation.observe,
                                  args=(job['id'], 'api0001', stop), daemon=True)
        worker.start()
        progress, deadline = {}, time.time() + 15
        while time.time() < deadline:
            progress = self.generation.public(job['id']).get('progress') or {}
            if progress.get('phase'):
                break
            time.sleep(0.05)
        stop.set()
        worker.join(10)
        self.assertFalse(worker.is_alive(), 'observer did not exit after stop was set')
        self.assertIn(progress.get('phase'), ('running', 'saving'))
        self.assertNotIn('percent', progress)


if __name__ == '__main__':
    unittest.main()
