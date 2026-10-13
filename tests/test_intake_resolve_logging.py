"""重新解析专用脱敏日志：只测这一条路由记了什么、没记什么。

日志写进程 stderr，测试用哨兵数据（合成 URL、token、报错原文、正文片段）逐一断言它们
一个都不出现。全程 TemporaryDirectory + 合成抓取器，不联网、不碰正式数据库。
"""
import http.client
import json
import sys
import tempfile
import threading
import time
import unittest
import unittest.mock
import urllib.error
import urllib.parse
import urllib.request

from tests.intake_dependency_patches import guard_intake_dependencies, patch_intake_dependency
from knowledge_store import Knowledge
from server import Handler, Store, ThreadingHTTPServer

# 哨兵：只要它们出现在日志里，就说明泄漏了。
SENTINEL_URL = 'https://leak-canary.example.com/post?token=SENTINELQUERYTOKEN'
SENTINEL_BODY = 'SENTINELBODY正文片段'
SENTINEL_ERROR = 'SENTINELERROR抓取失败原因'
WHITELIST = {'method', 'path', 'status', 'outcome', 'error_kind', 'drop_ref', 'elapsed_ms'}


class FakeWeb:
    def __init__(self, markdown=SENTINEL_BODY):
        self.markdown = markdown
        self.calls = []

    def __call__(self):
        return self

    def fetch(self, args, job):
        self.calls.append(dict(args))
        if self.fail_with:
            raise ValueError(self.fail_with)
        return {'url': args['url'], 'title': '合成页面标题', 'markdown': self.markdown, 'start': 0,
                'total_chars': len(self.markdown), 'truncated': False, 'next_start': None}

    fail_with = ''


class _Collector:
    """线程安全的 stderr 收集器：日志是服务端线程写的，不能靠客户端返回就算写完。"""

    def __init__(self):
        self.lock = threading.Lock()
        self.lines = []

    def write(self, value):
        with self.lock:
            self.lines.append(value)
        return len(value)

    def flush(self):
        pass

    def snapshot(self):
        with self.lock:
            return [line for chunk in self.lines for line in chunk.splitlines() if line.strip()]

    def reset(self):
        with self.lock:
            self.lines = []


class ResolveLoggingTests(unittest.TestCase):
    def setUp(self):
        guard_intake_dependencies(self)
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(self.tmp.name)
        self.store._knowledge = Knowledge(self.store, sources=[])
        self.media_patch = unittest.mock.patch('intake_media_recognition.MediaRecognizer.recognize_video',
                                               return_value={'status': 'no_audio', 'scope': '视频无音轨'})
        self.media_patch.start()
        self.addCleanup(self.media_patch.stop)
        self.web = patch_intake_dependency(self, 'WebTools', FakeWeb())
        self.srv = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.srv.store = self.store
        self.thread = threading.Thread(target=self.srv.serve_forever, daemon=True)
        self.thread.start()
        self.base = 'http://127.0.0.1:%d' % self.srv.server_port
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        self.collector = _Collector()
        stderr_patch = unittest.mock.patch.object(sys, 'stderr', self.collector)
        stderr_patch.start()
        self.addCleanup(stderr_patch.stop)

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        self.thread.join()
        intake = self.store._creative_intake
        if intake:
            for worker in list(intake.recognition_workers.values()):
                worker.join(timeout=5)
        cards = self.store._intake_cards
        if cards:
            for worker in list(cards.analysis_workers.values()):
                worker.join(timeout=5)
        self.tmp.cleanup()

    # ── 小工具 ────────────────────────────────────────────────────────────────

    def call(self, path, body=None, headers=None, raw=None, opener=None):
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        head = {'Origin': self.base, 'X-Article-Studio': '1'}
        if raw is None:
            head['Content-Type'] = 'application/json'
        head.update(headers or {})
        request = urllib.request.Request(self.base + path, data=data, headers=head,
                                         method='POST' if data is not None else 'GET')
        try:
            with (opener or self.opener).open(request) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as error:
            code, payload = error.code, error.read()
            error.close()
            return code, payload
        except (http.client.RemoteDisconnected, ConnectionResetError, BrokenPipeError):
            # 未预期异常的既有行为：HTTP 服务器直接断开，没有 JSON 回执（prep §1）。
            # 断开时内核可能先回 RST，报 ConnectionResetError，两种都当「没回执」。
            return None, b''

    def post(self, path, body=None, headers=None, raw=None):
        return self.call(path, body, headers, raw)

    def resolve_path(self, drop_id, query=''):
        return '/api/intake/' + drop_id + '/resolve' + query

    def drop(self, url=SENTINEL_URL, text=SENTINEL_BODY):
        if text is not None:
            payload = {'kind': 'text', 'text': text}
        else:
            payload = {'kind': 'link', 'url': url}
        status, raw = self.post('/api/intake/drop', payload)
        self.assertEqual(status, 201)
        return json.loads(raw)['id']

    def begin(self):
        """清空收集器并返回它：每个用例从「零行日志」开始。"""
        self.collector.reset()
        return self.collector

    def wait_logs(self, count, timeout=5):
        """等到服务端线程真的写够 count 行再断言（有上限，不靠 sleep 赌时序）。"""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if len(self.collector.snapshot()) >= count:
                break
            time.sleep(0.01)
        return self.collector.snapshot()

    def log_records(self, raw_lines):
        """只把本模块自己写的那行 JSON 认出来。

        其余行是 HTTP 服务器既有的 traceback —— 未预期异常本来就会打，本轮明确不脱敏它
        （prep §3），所以这里分开收，不拿它冒充 resolve 日志。
        """
        records, other = [], []
        for line in raw_lines:
            try:
                record = json.loads(line)
            except ValueError:
                other.append(line)
                continue
            records.append(record) if isinstance(record, dict) and set(record) == WHITELIST else other.append(line)
        return records, other

    def lines_of(self, raw_lines):
        records, other = self.log_records(raw_lines)
        self.assertEqual(other, [], '正常请求的 stderr 只应有 resolve 日志这一行')
        for record in records:
            self.assertEqual(set(record), WHITELIST, '日志只允许白名单字段')
        return records

    def assert_no_sentinel(self, *records):
        text = json.dumps(records, ensure_ascii=False)
        for sentinel in (SENTINEL_URL, SENTINEL_BODY, SENTINEL_ERROR, 'SENTINELQUERYTOKEN'):
            self.assertNotIn(sentinel, text)

    # ── 白名单与脱敏 ──────────────────────────────────────────────────────────

    def test_resolve_log_has_only_whitelisted_fields(self):
        self.begin()
        drop_id = self.drop()
        self.post(self.resolve_path(drop_id), {})
        lines = self.wait_logs(1)
        self.assertEqual(len(lines), 1, '一次请求只记一行')
        record = self.lines_of(lines)[0]
        self.assertEqual(record['method'], 'POST')
        self.assertEqual(record['path'], '/api/intake/<redacted>/resolve')
        self.assertEqual(record['status'], 200)
        self.assertEqual(record['outcome'], 'stored')
        self.assertEqual(record['error_kind'], 'none')
        self.assertNotIn(drop_id, lines[0], '实际回收项 id 不进日志')
        self.assertRegex(record['drop_ref'], r'^d[0-9a-f]{12}$')
        self.assertIsInstance(record['elapsed_ms'], int)
        self.assertGreaterEqual(record['elapsed_ms'], 0)

    def test_resolve_log_redacts_id_query_body_and_error(self):
        self.begin()
        self.web.fail_with = SENTINEL_ERROR
        drop_id = self.drop(url=SENTINEL_URL, text=None)
        self.post(self.resolve_path(drop_id, '?token=SENTINELQUERYTOKEN&x=' + drop_id), {})
        lines = self.wait_logs(1)
        record = self.lines_of(lines)[0]
        self.assertEqual((record['status'], record['outcome'], record['error_kind']), (200, 'failed', 'fetch_failed'))
        self.assert_no_sentinel(record)
        self.assertNotIn(drop_id, lines[0])
        self.assertEqual(record['path'], '/api/intake/<redacted>/resolve', 'path 里也不能出现真实 id 或 query')

    def test_resolve_log_reports_200_business_failure(self):
        self.begin()
        self.web.fail_with = SENTINEL_ERROR
        drop_id = self.drop(url=SENTINEL_URL, text=None)
        status, raw = self.post(self.resolve_path(drop_id), {})
        self.assertEqual(status, 200, '业务失败仍是 200')
        self.assertEqual(json.loads(raw)['outcome'], 'failed')
        record = self.lines_of(self.wait_logs(1))[0]
        self.assertEqual((record['status'], record['outcome'], record['error_kind']),
                         (200, 'failed', 'fetch_failed'), '200 的业务失败照样单独成行')

    def test_resolve_log_covers_403_400_413_and_unexpected_exception(self):
        self.begin()
        drop_id = self.drop()
        self.post(self.resolve_path(drop_id), {}, headers={'Origin': 'https://example.com'})
        self.post(self.resolve_path(drop_id), {'extra': 1})
        self.post(self.resolve_path(drop_id), raw=b'')
        with unittest.mock.patch.object(Handler, '_post_routes', side_effect=RuntimeError(SENTINEL_ERROR)):
            replied = self.post(self.resolve_path(drop_id), {})
        self.assertIsNone(replied[0], '未预期异常按既有行为断开连接，不返回 JSON')
        self.assertEqual(replied[1], b'')
        cases = self.wait_logs(4)
        records, other = self.log_records(cases)
        self.assertEqual(len(records), 4, '四种情况各记一行')
        forbidden_line, bad_line, large_line, crash_line = records
        self.assertEqual((forbidden_line['status'], forbidden_line['error_kind'], forbidden_line['outcome']),
                         (403, 'forbidden', 'unknown'))
        self.assertEqual((bad_line['status'], bad_line['error_kind']), (400, 'bad_request'))
        self.assertEqual((large_line['status'], large_line['error_kind']), (413, 'too_large'))
        # 未预期异常：还没发出任何响应就炸了，status 如实为 null，不谎称已回 200。
        self.assertIsNone(crash_line['status'])
        self.assertEqual(crash_line['error_kind'], 'unexpected')
        self.assertEqual(crash_line['outcome'], 'unknown')
        self.assert_no_sentinel(*records)
        self.assertNotIn(drop_id, '\n'.join(json.dumps(r, ensure_ascii=False) for r in records))
        # 断连前 HTTP 服务器自己打的 traceback 是既有行为，本轮没做脱敏，也不算 resolve 日志。
        self.assertTrue(any(SENTINEL_ERROR in line for line in other), '未预期异常的 traceback 仍在，属于既有行为')

    def test_other_routes_stay_silent(self):
        self.begin()
        drop_id = self.drop()
        for path, body in (('/api/intake/drop', {'kind': 'text', 'text': SENTINEL_BODY}),
                           ('/api/intake/' + drop_id + '/analyze', {}),
                           ('/api/intake/' + drop_id + '/delete', {}),
                           ('/api/collection', {'id': 'x'})):
            self.post(path, body)
        self.call('/api/intake/' + drop_id)
        # 别的路由跑完后一行 resolve 日志都没有（留一秒余量，不是靠瞬时快照下结论）。
        self.assertEqual(self.wait_logs(1, timeout=1), [], '非 resolve 路由不记日志')

    def test_keepalive_does_not_reuse_previous_resolve_context(self):
        # 这里的两次请求落在同一个 urllib opener 上；处理器是 HTTP/1.0、每请求一个实例，
        # 所以这条断言守的是「入口必须每次重置上下文」这条纪律：换成 keep-alive 复用同一个
        # 处理器实例时，上一次的 status/outcome 也不能串到下一次。
        self.begin()
        first = self.drop()
        second = self.drop()
        self.post(self.resolve_path(first), {})
        self.post(self.resolve_path(second), {})
        records = self.lines_of(self.wait_logs(2))
        self.assertEqual(len(records), 2, '一次请求一行')
        self.assertEqual([r['drop_ref'] for r in records],
                         [Handler._drop_ref(first), Handler._drop_ref(second)])
        self.assertNotEqual(records[0]['drop_ref'], records[1]['drop_ref'])
        self.assertEqual({k: v for k, v in records[0].items() if k not in ('drop_ref', 'elapsed_ms')},
                         {k: v for k, v in records[1].items() if k not in ('drop_ref', 'elapsed_ms')},
                         '两个请求除 drop_ref 与耗时外字段一致，说明没有交叉污染')
        # 失败那次之后的请求不能继承上一次的 status/outcome。
        self.begin()
        self.web.fail_with = SENTINEL_ERROR
        third = self.drop(url='https://example.com/three', text=None)
        fourth = self.drop(text='普通文字')
        self.post(self.resolve_path(third), {})
        self.post(self.resolve_path(fourth), {})
        failed, ok = self.lines_of(self.wait_logs(2))
        self.assertEqual((failed['drop_ref'], failed['status'], failed['outcome'], failed['error_kind']),
                         (Handler._drop_ref(third), 200, 'failed', 'fetch_failed'))
        self.assertEqual((ok['drop_ref'], ok['status'], ok['outcome'], ok['error_kind']),
                         (Handler._drop_ref(fourth), 200, 'stored', 'none'))

    def test_log_failure_does_not_change_response(self):
        self.begin()
        drop_id = self.drop()

        class BrokenStderr:
            def write(self, _value):
                raise OSError('日志设备不可写')

            def flush(self):
                raise OSError('日志设备不可写')

        # 直接验守卫：日志写不出去时 _log_resolve 吞掉 OSError，不把异常抛回业务路径。
        context = {'started': time.monotonic(), 'status': 200, 'outcome': 'stored',
                   'error_kind': 'none', 'drop_ref': 'd000000000000'}
        with unittest.mock.patch.object(sys, 'stderr', BrokenStderr()):
            Handler._log_resolve(Handler.__new__(Handler), dict(context))
            status, raw = self.post(self.resolve_path(drop_id), {})
        self.assertEqual(status, 200, '日志写不出去也不能改写业务回执')
        self.assertEqual(json.loads(raw)['outcome'], 'stored')
        self.assertEqual(self.call('/api/intake/' + drop_id)[0], 200)
        self.assertEqual(self.collector.snapshot(), [], '坏掉的 stderr 没把内容漏到别处')


if __name__ == '__main__':
    unittest.main()