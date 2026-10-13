"""AIsa 全网情报雷达接入模块 · 离线 Mock 单元测试。

全程注入假 transport（默认 transport 也只走假 urlopen），不建任何真实网络连接，
不消耗任何付费额度，不读取也不打印真实 Key（测试里的 canary 均为自造）。
"""
import builtins
import gzip
import io
import json
import os
import socket
import socketserver
import sqlite3
import ssl
import subprocess
import tempfile
import threading
import time
import traceback
import unittest
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import urlsplit

import pytest

import aisa_source as mod
from aisa_source import (DOUYIN_HOT_PATH, TWITTER_TWEETS_PATH, XHS_INSPIRATION_PATH,
                         AIsaSource, urllib_transport)
from collect_adapters import AIsaRadarAdapter, build_adapters
from collect_core import PlannedAdapter
from isolation_patches import module_time


FIXTURES = Path(__file__).parent / 'fixtures' / 'aisa'
SECRET = 'test-placeholder-secret'
# 复核用的自造 canary（不是真实 Key）：任何对外表面出现它都算泄密。
CANARY = 'test-placeholder-canary'

# ── 零外网护栏 ─────────────────────────────────────────────────────────────
# 本文件所有用例都必须离线：除本机回环外，任何域名解析/连接一律抛 AssertionError。
# 与复核证据里的 conftest 护栏同构（此处内联，避免新增白名单外文件）。
_real_socket = socket.socket
_real_getaddrinfo = socket.getaddrinfo
_LOCAL_HOSTS = (None, '', '127.0.0.1', '::1', 'localhost')


class _NetworkGuard(_real_socket):
    def connect(self, address):
        host = address[0] if isinstance(address, tuple) and address else None
        port = address[1] if isinstance(address, tuple) and len(address) > 1 else None
        # 放行本机回环，但封死假代理端口 9（与复核护栏一致）。
        if host in _LOCAL_HOSTS and port != 9:
            return super().connect(address)
        raise AssertionError('ZERO_EXTERNAL_NETWORK')

    def connect_ex(self, address):
        self.connect(address)
        return 0


def _guarded_getaddrinfo(host, *args, **kwargs):
    if host in _LOCAL_HOSTS:
        return _real_getaddrinfo(host, *args, **kwargs)
    raise AssertionError('ZERO_EXTERNAL_NETWORK')


socket.socket = _NetworkGuard
socket.getaddrinfo = _guarded_getaddrinfo


def load(name):
    return (FIXTURES / name).read_text(encoding='utf-8')


class RecordingTransport:
    """按路径返回固定响应，并记录每次调用，便于断言请求内容。"""

    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def __call__(self, method, url, headers, body, timeout):
        self.calls.append({'method': method, 'path': urlsplit(url).path,
                           'headers': dict(headers), 'body': body, 'url': url})
        entry = self.responses.get(urlsplit(url).path)
        return entry if entry is not None else (404, '{"code":404}')


def raising_transport(exc):
    def transport(method, url, headers, body, timeout):
        raise exc
    return transport


class FakeStream:
    """假 HTTP 响应流：按 size 分块返回固定内容，读尽后返回空（与真实流一致）。"""

    status = 200

    def __init__(self, payload=b'{}'):
        self.payload = payload
        self.sizes = []
        self.offset = 0

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self, size=-1):
        self.sizes.append(size)
        step = size if isinstance(size, int) and size > 0 else len(self.payload)
        chunk = self.payload[self.offset:self.offset + step]
        self.offset += len(chunk)
        return chunk


class EndlessStream(FakeStream):
    """永远读不完的响应流：验证字节上限会主动断开。"""

    def read(self, size=-1):
        self.sizes.append(size)
        return b'x' * (size if isinstance(size, int) and size > 0 else 1024)


class SlowStream(FakeStream):
    """每读一块就把单调时钟推过总时限：验证传输总时限会主动断开。"""

    def __init__(self, clock):
        super().__init__(b'')
        self.clock = clock

    def read(self, size=-1):
        self.sizes.append(size)
        self.clock['now'] += mod.MAX_TOTAL_SECONDS
        return b'x' * (size if isinstance(size, int) and size > 0 else 1024)


class AIsaSourceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = SimpleNamespace(workspace=str(self.root))

    def tearDown(self):
        self.temp.cleanup()

    def source(self, responses=None, api_key='unit-test-key', transport=None):
        if transport is None:
            transport = RecordingTransport(responses or {})
        return AIsaSource(store=self.store, transport=transport, api_key=api_key)

    # ── 推特推文解析 ───────────────────────────────────────────────────────
    def test_twitter_tweets_are_parsed_with_all_fields(self):
        transport = RecordingTransport({TWITTER_TWEETS_PATH: (200, load('twitter_tweets.json'))})
        source = self.source(transport=transport)
        tweets = source.get_twitter_tweets('@tech_watch', count=20)
        self.assertEqual(len(tweets), 2)
        first = tweets[0]
        self.assertEqual(first['platform'], 'twitter')
        self.assertEqual(first['username'], 'tech_watch')
        self.assertEqual(first['tweet_id'], '1800000000000000001')
        self.assertIn('推理吞吐', first['text'])
        self.assertEqual(first['created_at'], '2026-10-11T08:30:00Z')
        self.assertEqual(first['likes'], 1280)
        self.assertEqual(first['retweets'], 340)
        self.assertEqual(first['media_urls'], ['https://pbs.twimg.com/media/aaa111.jpg'])
        call = transport.calls[0]
        self.assertEqual(call['method'], 'POST')
        self.assertEqual(call['path'], TWITTER_TWEETS_PATH)
        self.assertEqual(json.loads(call['body']), {'username': 'tech_watch', 'count': 20})
        self.assertEqual(call['headers']['Authorization'], 'Bearer unit-test-key')

    # ── 抖音热搜解析与排序 ─────────────────────────────────────────────────
    def test_douyin_hot_searches_are_parsed_and_sorted(self):
        transport = RecordingTransport({DOUYIN_HOT_PATH: (200, load('douyin_hot.json'))})
        source = self.source(transport=transport)
        hot = source.fetch_douyin_hot_searches(limit=20)
        self.assertEqual([row['word'] for row in hot],
                         ['端侧大模型', '多模态 Agent', 'AI 编程助手', '具身智能'])
        top = hot[0]
        self.assertEqual((top['hot_value'], top['rank'], top['label']), (1500000, 1, '爆'))
        self.assertEqual(transport.calls[0]['method'], 'GET')

    def test_douyin_limit_is_applied_after_sorting(self):
        transport = RecordingTransport({DOUYIN_HOT_PATH: (200, load('douyin_hot.json'))})
        source = self.source(transport=transport)
        hot = source.fetch_douyin_hot_searches(limit=2)
        self.assertEqual([row['word'] for row in hot], ['端侧大模型', '多模态 Agent'])

    # ── 小红书灵感流提取 ───────────────────────────────────────────────────
    def test_xiaohongshu_inspirations_are_extracted(self):
        transport = RecordingTransport({XHS_INSPIRATION_PATH: (200, load('xiaohongshu_inspiration.json'))})
        source = self.source(transport=transport)
        items = source.fetch_xiaohongshu_inspirations(limit=20)
        self.assertEqual(len(items), 2)
        first = items[0]
        self.assertEqual(first['title'], '多模态 Agent 工作流拆解')
        self.assertEqual(first['topic_id'], 'topic_8899')
        self.assertEqual(first['heat_score'], 9876)
        self.assertEqual(first['view_count'], 45210)
        self.assertEqual(transport.calls[0]['path'], XHS_INSPIRATION_PATH)

    # ── 降级：缺 Key / 401 / 429 / 超时 / 非 JSON ─────────────────────────
    def test_missing_key_degrades_without_network_or_crash(self):
        with patch.dict(os.environ, {}, clear=True):
            transport = RecordingTransport({TWITTER_TWEETS_PATH: (200, load('twitter_tweets.json'))})
            source = AIsaSource(store=self.store, transport=transport)
            self.assertEqual(source.get_twitter_tweets('tech_watch'), [])
            self.assertEqual(source.fetch_douyin_hot_searches(), [])
            self.assertEqual(transport.calls, [])
            self.assertIn('AISA_API_KEY', source.last_error)
            status = source.status()
            self.assertFalse(status['available'])
            self.assertEqual(status['detail'], '未配置 AISA_API_KEY 环境变量')

    def test_unauthorized_401_degrades(self):
        source = self.source({TWITTER_TWEETS_PATH: (401, load('error_401.json'))})
        self.assertEqual(source.get_twitter_tweets('tech_watch'), [])
        self.assertIn('鉴权失败', source.last_error)

    def test_rate_limited_429_degrades(self):
        source = self.source({DOUYIN_HOT_PATH: (429, load('error_429.json'))})
        self.assertEqual(source.fetch_douyin_hot_searches(), [])
        self.assertIn('限流', source.last_error)

    def test_timeout_degrades(self):
        source = self.source(transport=raising_transport(TimeoutError('timed out')))
        self.assertEqual(source.get_twitter_tweets('tech_watch'), [])
        self.assertIn('网络异常', source.last_error)
        self.assertIn('TimeoutError', source.last_error)

    def test_non_json_response_degrades(self):
        source = self.source({TWITTER_TWEETS_PATH: (200, load('notjson.txt'))})
        self.assertEqual(source.get_twitter_tweets('tech_watch'), [])
        self.assertIn('非 JSON', source.last_error)

    # ── 幂等去重 ───────────────────────────────────────────────────────────
    def test_repeat_fetch_is_idempotent_by_producer_key(self):
        transport = RecordingTransport({TWITTER_TWEETS_PATH: (200, load('twitter_tweets.json'))})
        source = self.source(transport=transport)
        source.get_twitter_tweets('tech_watch')
        source.get_twitter_tweets('tech_watch')
        self.assertEqual(source.count(), 2)
        self.assertTrue(source.last_synced())

    def test_clue_item_carries_stable_identity(self):
        source = self.source({TWITTER_TWEETS_PATH: (200, load('twitter_tweets.json'))})
        tweet = source.get_twitter_tweets('tech_watch')[0]
        first = source.to_clue_item(tweet)
        second = source.to_clue_item(tweet)
        self.assertEqual(first['producer_key'], 'aisa:twitter:1800000000000000001')
        self.assertEqual(first['producer_key'], second['producer_key'])
        self.assertEqual(first['provenance'], 'collect:aisa:twitter:1800000000000000001')

    # ── 线索候选字段 ───────────────────────────────────────────────────────
    def test_to_clue_item_shapes_each_platform(self):
        source = self.source()
        tweet = {'platform': 'twitter', 'username': 'tech_watch', 'tweet_id': '1800000000000000001',
                 'text': 'DeepSeek V4 推理吞吐实测', 'created_at': '2026-10-11T08:30:00Z',
                 'likes': 1280, 'retweets': 340, 'media_urls': []}
        clue = source.to_clue_item(tweet)
        self.assertEqual(clue['categories'], ['topics'])
        self.assertEqual(clue['heat'], 'rising')
        self.assertTrue(clue['title'].startswith('【推特技术首提】@tech_watch:'))

        hot = {'platform': 'douyin', 'word': '端侧大模型', 'hot_value': 1500000, 'rank': 1, 'label': '爆'}
        clue = source.to_clue_item(hot)
        self.assertEqual(clue['title'], '【抖音全网热搜】#端侧大模型 (热度 150万)')
        self.assertEqual(clue['producer_key'], 'aisa:douyin:端侧大模型')

        inspiration = {'platform': 'xiaohongshu', 'title': '多模态 Agent 工作流拆解',
                       'topic_id': 'topic_8899', 'heat_score': 9876, 'view_count': 45210}
        clue = source.to_clue_item(inspiration)
        self.assertEqual(clue['title'], '【小红书灵感】多模态 Agent 工作流拆解 (热度 9876)')
        self.assertEqual(clue['producer_key'], 'aisa:xiaohongshu:topic_8899')

        with self.assertRaises(ValueError):
            source.to_clue_item({'platform': 'unknown', 'id': 'x'})

    # ── Key 隔离铁律 ───────────────────────────────────────────────────────
    def test_api_key_never_leaks_into_output_error_or_database(self):
        leaky = json.dumps({'code': 401, 'message': f'unauthorized for {SECRET}'})
        transport = RecordingTransport({TWITTER_TWEETS_PATH: (401, leaky)})
        source = self.source(transport=transport, api_key=SECRET)
        items = source.get_twitter_tweets('tech_watch')
        self.assertEqual(items, [])
        self.assertNotIn(SECRET, source.last_error)
        self.assertNotIn(SECRET, json.dumps(source.status(), ensure_ascii=False))
        self.assertEqual(transport.calls[0]['headers']['Authorization'], f'Bearer {SECRET}')
        database = self.root / 'aisa' / 'aisa.db'
        self.assertNotIn(SECRET.encode(), database.read_bytes())

    def test_successful_payload_never_stores_api_key(self):
        source = self.source({TWITTER_TWEETS_PATH: (200, load('twitter_tweets.json'))}, api_key=SECRET)
        source.get_twitter_tweets('tech_watch')
        database = self.root / 'aisa' / 'aisa.db'
        self.assertNotIn(SECRET.encode(), database.read_bytes())

    # ── 缓存库权限 ─────────────────────────────────────────────────────────
    def test_cache_permissions_are_locked_down(self):
        self.source({TWITTER_TWEETS_PATH: (200, load('twitter_tweets.json'))})
        directory = self.root / 'aisa'
        database = directory / 'aisa.db'
        self.assertEqual(directory.stat().st_mode & 0o777, 0o700)
        self.assertEqual(database.stat().st_mode & 0o777, 0o600)

    # ── 状态 ───────────────────────────────────────────────────────────────
    def test_status_reports_readiness_and_count(self):
        source = self.source({TWITTER_TWEETS_PATH: (200, load('twitter_tweets.json'))})
        before = source.status()
        self.assertTrue(before['available'])
        self.assertEqual(before['detail'], 'AIsa 雷达网关就绪')
        self.assertEqual(before['count'], 0)
        source.get_twitter_tweets('tech_watch')
        self.assertEqual(source.status()['count'], 2)


class AIsaRedlineTests(unittest.TestCase):
    """凭证红线 + 资源上限 + 降级：复核 27 条对抗里失败的 6 条已原样纳入。"""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = SimpleNamespace(workspace=str(self.root))

    def tearDown(self):
        self.temp.cleanup()

    def source(self, payload=None, status=200, transport=None, api_key=CANARY, default_transport=False):
        if transport is None:
            transport = mod.urllib_transport if default_transport else (
                lambda *args: (status, json.dumps(payload)))
        return AIsaSource(store=self.store, transport=transport, api_key=api_key)

    def surfaces(self, source, items):
        """所有对外可见表面：返回值、状态、回执、repr/str、线索候选。"""
        parts = [json.dumps(items, ensure_ascii=False),
                 json.dumps(source.status(), ensure_ascii=False),
                 source.last_error, repr(source), str(source)]
        for item in items:
            parts.append(json.dumps(source.to_clue_item(item), ensure_ascii=False))
        return '\n'.join(parts)

    def database_bytes(self):
        directory = self.root / 'aisa'
        if not directory.exists():
            return b''
        return b''.join(f.read_bytes() for f in directory.glob('*') if f.is_file())

    # ── H1：成功响应同样必须脱敏（复核 3 条失败）─────────────────────────
    def test_success_canary_never_reaches_output_clue_or_database(self):
        payload = {'items': [{'tweet_id': CANARY, 'text': CANARY, 'created_at': CANARY,
                              'media_urls': [CANARY], 'word': CANARY, 'label': CANARY,
                              'title': CANARY, 'topic_id': CANARY}]}
        cases = (('twitter', lambda s: s.get_twitter_tweets(CANARY)),
                 ('douyin', lambda s: s.fetch_douyin_hot_searches()),
                 ('xiaohongshu', lambda s: s.fetch_xiaohongshu_inspirations()))
        for platform, call in cases:
            source = self.source(payload)
            items = call(source)
            self.assertTrue(items, f'{platform} 应解析出素材')
            artifacts = self.surfaces(source, items).encode() + self.database_bytes()
            self.assertNotIn(CANARY.encode(), artifacts,
                             f'{platform}: CANARY_PRESENT_IN_OUTPUT_CLUE_OR_DATABASE')

    def test_error_and_exception_canaries_stay_out(self):
        for code in (401, 429, 500):
            source = self.source({'message': CANARY}, status=code)
            self.assertEqual(source.get_twitter_tweets('u'), [])
            self.assertNotIn(CANARY, self.surfaces(source, []))
        source = self.source(transport=lambda *a: (_ for _ in ()).throw(TimeoutError(CANARY)))
        self.assertEqual(source.get_twitter_tweets('u'), [])
        self.assertNotIn(CANARY, self.surfaces(source, []))
        source = self.source(transport=lambda *a: (200, CANARY))
        self.assertEqual(source.get_twitter_tweets('u'), [])
        self.assertNotIn(CANARY, self.surfaces(source, []))

    # ── M2：解析与缓存异常统一降级（复核 2 条失败）─────────────────────
    def test_infinite_numeric_field_degrades(self):
        raw = '{"items":[{"tweet_id":"1","likes":1e309,"retweets":1e-309}]}'
        source = self.source(transport=lambda *a: (200, raw))
        items = source.get_twitter_tweets('u')
        self.assertIsInstance(items, list)
        self.assertEqual((items[0]['likes'], items[0]['retweets']), (0, 0))

    def test_database_write_failure_degrades_without_leak(self):
        source = self.source({'items': [{'tweet_id': '1', 'text': 'x'}]})
        with patch.object(source, '_remember', side_effect=sqlite3.OperationalError(CANARY)):
            try:
                items = source.get_twitter_tweets('u')
            except Exception:
                self.assertNotIn(CANARY, traceback.format_exc(), 'CANARY_PRESENT_IN_UNCAUGHT_TRACEBACK')
                raise
        self.assertEqual(len(items), 1)
        self.assertIn('OperationalError', source.last_error)
        self.assertNotIn(CANARY, self.surfaces(source, items))
        self.assertNotIn(CANARY.encode(), self.database_bytes())

    def test_database_read_failure_degrades_to_safe_status(self):
        source = self.source({'items': [{'tweet_id': '1', 'text': 'x'}]})
        source.get_twitter_tweets('u')
        with patch.object(mod.sqlite3, 'connect', side_effect=sqlite3.OperationalError(CANARY)):
            status = source.status()
            count = source.count()
            synced = source.last_synced()
        self.assertEqual((count, synced), (0, ''))
        self.assertEqual(status['cache_error'], 'OperationalError')
        self.assertNotIn(CANARY, json.dumps(status, ensure_ascii=False))
        self.assertNotIn(CANARY, source.last_error)

    def test_cache_init_failure_disables_cache_but_keeps_source_usable(self):
        with patch.object(mod.sqlite3, 'connect', side_effect=sqlite3.OperationalError(CANARY)):
            source = self.source({'items': [{'tweet_id': '1', 'text': 'x'}]})
        self.assertEqual(source.count(), 0)
        self.assertEqual(len(source.get_twitter_tweets('u')), 1)
        self.assertNotIn(CANARY, json.dumps(source.status(), ensure_ascii=False))

    def test_unwritable_workspace_disables_cache_without_crashing(self):
        locked = self.root / 'locked'
        locked.mkdir()
        locked.chmod(0o500)
        try:
            source = AIsaSource(store=SimpleNamespace(workspace=str(locked)),
                                transport=lambda *a: (200, '{"items":[{"tweet_id":"1"}]}'),
                                api_key=CANARY)
            self.assertEqual(source.count(), 0)
            self.assertEqual(len(source.get_twitter_tweets('u')), 1)
        finally:
            locked.chmod(0o700)

    def test_malformed_payloads_degrade(self):
        for payload in (None, [], {'data': 'bad'}, {'items': [None, 3, {}, 'x']},
                        {'items': [{'tweet_id': '1', 'likes': {}, 'retweets': [], 'media_urls': 123}]}):
            source = self.source(payload)
            self.assertIsInstance(source.get_twitter_tweets('u'), list)

    def test_deep_json_degrades(self):
        # 深层嵌套不得抛出未捕获异常（解析失败或解包失败都归为降级）。
        source = self.source(transport=lambda *a: (200, '[' * 20000 + ']' * 20000))
        self.assertEqual(source.get_twitter_tweets('u'), [])

    def test_deeply_nested_wrapper_object_degrades(self):
        payload = {'items': [{'tweet_id': '1'}]}
        for _ in range(40):
            payload = {'data': payload}
        source = self.source(payload)
        self.assertEqual(source.get_twitter_tweets('u'), [])

    # ── M1：默认 transport 的超时与读上限（直接测默认实现，不走注入）────
    def test_default_transport_always_passes_a_positive_timeout(self):
        for passed in (None, 0, -1, 'bad', 15):
            with patch.object(mod.request, 'urlopen', return_value=FakeStream()) as opening:
                urllib_transport('GET', 'https://api.aisa.one.invalid/x', {}, None, passed)
            timeout = opening.call_args.kwargs['timeout']
            self.assertIsNotNone(timeout, 'urlopen 的 timeout 不得为 None')
            self.assertTrue(0 < timeout <= mod.DEFAULT_TIMEOUT, f'timeout={timeout} 越界')

    def test_default_transport_response_read_is_bounded(self):
        stream = FakeStream()
        with patch.object(mod.request, 'urlopen', return_value=stream):
            status, text = urllib_transport('GET', 'https://api.aisa.one.invalid/x', {}, None, 15)
        self.assertEqual((status, text), (200, '{}'))
        self.assertTrue(stream.sizes, '默认 transport 必须带大小参数读取响应体')
        for size in stream.sizes:
            self.assertTrue(0 < size <= 8 * 1024 * 1024, 'UNBOUNDED_RESPONSE_READ')
        self.assertLessEqual(mod.MAX_RESPONSE_BYTES, 8 * 1024 * 1024)

    def test_default_transport_reads_full_body_under_the_cap(self):
        payload = json.dumps({'items': [{'word': 'w' * 5000}] * 200}).encode()
        stream = FakeStream(payload)
        with patch.object(mod.request, 'urlopen', return_value=stream):
            status, text = urllib_transport('GET', 'https://api.aisa.one.invalid/x', {}, None, 15)
        self.assertEqual(status, 200)
        self.assertEqual(len(text), len(payload))
        self.assertGreater(len(stream.sizes), 1, '未超上限的响应应分块读完整')

    def test_oversized_response_degrades_to_safe_receipt(self):
        source = self.source(default_transport=True)
        with patch.object(mod.request, 'urlopen', return_value=EndlessStream()):
            self.assertEqual(source.get_twitter_tweets('u'), [])
        self.assertIn('上限', source.last_error)
        self.assertNotIn(CANARY, self.surfaces(source, []))

    def test_slow_stream_hits_total_deadline(self):
        clock = {'now': 1000.0}
        source = self.source(default_transport=True)
        # 时钟补丁只替换 aisa_source 自己的 time 绑定，不动共享标准库 time。
        with module_time('aisa_source', monotonic=lambda: clock['now']), \
                patch.object(mod.request, 'urlopen', return_value=SlowStream(clock)):
            self.assertEqual(source.get_twitter_tweets('u'), [])
        self.assertIn('总时限', source.last_error)
        self.assertNotIn(CANARY, self.surfaces(source, []))

    # ── 其他复核项：count 边界 / 挂载不联网 / 权限与并发 ─────────────────
    def test_count_is_bounded_before_request(self):
        for count, expected in ((-3, 20), (0, 20), (999999999, 100), ('bad', 20), (None, 20)):
            calls = []

            def transport(*args):
                calls.append(args)
                return 200, json.dumps({'items': [{'tweet_id': str(i)} for i in range(150)]})

            source = self.source(transport=transport)
            result = source.get_twitter_tweets('u', count=count)
            self.assertEqual(len(result), expected)
            self.assertEqual(json.loads(calls[0][3])['count'], expected)

    def test_build_adapters_never_calls_network(self):
        for key in ('', CANARY):
            with patch.dict(os.environ, {'AISA_API_KEY': key}), \
                    patch.object(mod, 'urllib_transport', side_effect=AssertionError('NO_CALL')) as transport:
                adapters = build_adapters(SimpleNamespace(workspace=str(self.root), intel=object()))
                self.assertIn('wechat_mp', adapters)
                self.assertEqual(adapters['aisa'].status()['available'], bool(key))
                transport.assert_not_called()

    def test_import_failure_keeps_other_adapters(self):
        original = builtins.__import__

        def blocked(name, *args, **kwargs):
            if name == 'aisa_source':
                raise ImportError('simulated')
            return original(name, *args, **kwargs)

        with patch('builtins.__import__', side_effect=blocked):
            adapters = build_adapters(SimpleNamespace(workspace=str(self.root), intel=object()))
        # aisa 已在平台清单里：模块加载失败也不能从清单消失，只能退为占位适配器。
        self.assertIsInstance(adapters['aisa'], PlannedAdapter)
        self.assertFalse(adapters['aisa'].status()['available'])
        self.assertIn('wechat_mp', adapters)

    def test_permissions_and_concurrency(self):
        source = self.source({'items': [{'tweet_id': 'same', 'text': 'x'}]})
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: source.get_twitter_tweets('u'), range(32)))
        self.assertTrue(all(len(row) == 1 for row in results))
        self.assertEqual(source.count(), 1)
        directory = self.root / 'aisa'
        self.assertEqual(directory.stat().st_mode & 0o777, 0o700)
        for entry in directory.glob('*'):
            if entry.is_file():
                self.assertEqual(entry.stat().st_mode & 0o777, 0o600)


class AIsaRadarAdapterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = SimpleNamespace(intel=object(), workspace=str(self.root))

    def tearDown(self):
        self.temp.cleanup()

    def test_build_adapters_mounts_aisa_without_disturbing_others(self):
        adapters = build_adapters(self.store)
        self.assertIn('aisa', adapters)
        adapter = adapters['aisa']
        self.assertIsInstance(adapter, AIsaRadarAdapter)
        self.assertEqual(adapter.platform, 'aisa')
        self.assertEqual(adapter.label, '全网情报雷达 (AIsa)')
        self.assertEqual(adapter.state, 'connected')
        self.assertEqual(adapter.home, '/clues.html')
        for existing in ('wechat_mp', 'wechat_msg', 'mail', 'github'):
            self.assertIn(existing, adapters)

    def test_build_adapters_survives_aisa_failure(self):
        with patch('aisa_source.AIsaSource.__init__', side_effect=RuntimeError('模块不可用')):
            adapters = build_adapters(self.store)
        self.assertIsInstance(adapters['aisa'], PlannedAdapter)
        self.assertFalse(adapters['aisa'].status()['available'])
        self.assertIn('wechat_mp', adapters)

    def test_adapter_status_reflects_source_readiness(self):
        ready = AIsaRadarAdapter(AIsaSource(store=self.store, api_key='unit-test-key'))
        status = ready.status()
        self.assertTrue(status['available'])
        self.assertEqual(status['extra']['home'], '/clues.html')
        missing = AIsaRadarAdapter(AIsaSource(store=self.store, api_key=''))
        self.assertFalse(missing.status()['available'])
        self.assertEqual(ready.sources(), [])
        self.assertTrue(ready.latest('any')['delegated'])


# ══════════════════════════════════════════════════════════════════════════
# 复核 b 的 13 条独立新增边界：原样纳入（仅把复核文件里的 Stream/KEY 常量带过来）。
# ══════════════════════════════════════════════════════════════════════════
KEY = 'test-placeholder-review-key'


class Stream(io.BytesIO):
    status = 200

    def __init__(self, data):
        super().__init__(data)
        self.sizes = []

    def read(self, n=-1):
        self.sizes.append(n)
        return super().read(n)


@pytest.mark.parametrize('http_error', [False, True])
@pytest.mark.parametrize('size', [4 * 1024 * 1024, 4 * 1024 * 1024 + 1])
def test_exact_byte_cap(http_error, size):
    stream = Stream(b'x' * size)
    exc = HTTPError('https://example.invalid', 401, 'fake', {}, stream)
    with patch.object(mod.request, 'urlopen', side_effect=exc if http_error else None,
                      return_value=stream):
        if size > mod.MAX_RESPONSE_BYTES:
            with pytest.raises(mod.ResponseTooLarge):
                mod.urllib_transport('GET', 'https://example.invalid', {}, None, 15)
        else:
            code, raw = mod.urllib_transport('GET', 'https://example.invalid', {}, None, 15)
            assert len(raw) == size
    assert all(0 < n <= 256 * 1024 for n in stream.sizes)


@pytest.mark.parametrize('http_error', [False, True])
def test_last_short_chunk_respects_deadline(http_error):
    clock = {'now': 0}

    class SlowLast(Stream):
        def read(self, n=-1):
            clock['now'] = 31
            return b'{}'

    stream = SlowLast(b'{}')
    exc = HTTPError('https://example.invalid', 401, 'fake', {}, stream)
    with module_time('aisa_source', monotonic=lambda: clock['now']), \
            patch.object(mod.request, 'urlopen', side_effect=exc if http_error else None,
                         return_value=stream):
        with pytest.raises(mod.ResponseTooSlow):
            mod.urllib_transport('GET', 'https://example.invalid', {}, None, 15)


def test_total_deadline_not_expanded_by_timeout():
    clock = {'now': 0}

    class Late(Stream):
        def read(self, n=-1):
            clock['now'] += 31
            return b'x' * n

    with module_time('aisa_source', monotonic=lambda: clock['now']), \
            patch.object(mod.request, 'urlopen', return_value=Late(b'')):
        with pytest.raises(mod.ResponseTooSlow):
            mod.urllib_transport('GET', 'https://example.invalid', {}, None, 300)
        assert clock['now'] <= 60, 'DEADLINE_EXPANDED_ABOVE_30'


def test_huge_heat_conversion_safe(tmp_path):
    source = mod.AIsaSource(store=SimpleNamespace(workspace=tmp_path), api_key=KEY,
                            transport=lambda *a: (200, json.dumps({'items': [{'word': 'topic', 'hot_value': 10 ** 1000}]})))
    items = source.fetch_douyin_hot_searches()
    assert items
    assert isinstance(source.to_clue_item(items[0]), dict)


def test_unwritable_workspace_keeps_other_sources(tmp_path):
    folder = tmp_path / 'locked'
    folder.mkdir()
    folder.chmod(0o500)
    try:
        with patch.dict(os.environ, {'AISA_API_KEY': KEY}):
            adapters = build_adapters(SimpleNamespace(workspace=folder, intel=object()))
            assert 'wechat_mp' in adapters
            assert adapters['aisa'].source._db_path is None
            assert adapters['aisa'].status()['available']
    finally:
        folder.chmod(0o700)


def test_sqlite_read_and_write_failures_keep_fetched_data(tmp_path):
    source = mod.AIsaSource(store=SimpleNamespace(workspace=tmp_path), api_key=KEY,
                            transport=lambda *a: (200, '{"items":[{"tweet_id":"1"}]}'))
    with patch.object(mod.sqlite3, 'connect', side_effect=sqlite3.OperationalError(KEY)):
        items = source.get_twitter_tweets('u')
        status = source.status()
        assert len(items) == 1
        assert status['count'] == 0
        assert KEY not in json.dumps([items, status, source.last_error, repr(source), str(source)])


def test_proxy_environment_cannot_escape_socket_guard(tmp_path):
    with patch.dict(os.environ, {'HTTP_PROXY': 'http://127.0.0.1:9',
                                 'HTTPS_PROXY': 'http://127.0.0.1:9',
                                 'ALL_PROXY': 'http://127.0.0.1:9', 'NO_PROXY': '',
                                 'http_proxy': 'http://127.0.0.1:9',
                                 'https_proxy': 'http://127.0.0.1:9'}):
        source = mod.AIsaSource(api_key=KEY, base_url='https://review.invalid')
        assert source.get_twitter_tweets('u') == []
        assert 'AssertionError' in source.last_error


@pytest.mark.parametrize('encoded', [False, True])
def test_canary_direct_conversion(tmp_path, encoded):
    import base64
    source = mod.AIsaSource(api_key=KEY)
    value = base64.b64encode(KEY.encode()).decode() if encoded else KEY
    clue = source.to_clue_item({'platform': 'twitter', 'tweet_id': '1', 'text': value})
    if encoded:
        assert value in clue['summary']
    else:
        assert KEY not in json.dumps(clue)


@pytest.mark.parametrize('status', [200, 401])
def test_real_trickle_obeys_claimed_completion_bound(status, tmp_path):
    """真实本机滴流：每秒 50 字节、间隔小于 socket 超时，成功与 HTTPError 两条都须在
    总预算附近中断（read1 单次可用数据 + 每读前收紧 socket 超时）。只连 127.0.0.1 随机端口。"""

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            self.send_response(status)
            self.send_header('Content-Length', '20')
            self.end_headers()
            for i in range(20):
                try:
                    self.wfile.write(b'x')
                    self.wfile.flush()
                except OSError:
                    break
                if i < 19:
                    time.sleep(.02)

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with patch.object(mod, 'MAX_TOTAL_SECONDS', .1), patch.object(mod, 'DEFAULT_TIMEOUT', .06):
            start = time.monotonic()
            with pytest.raises(mod.ResponseTooSlow):
                mod.urllib_transport('GET', f'http://127.0.0.1:{server.server_port}/', {}, None, .06)
            elapsed = time.monotonic() - start
            (tmp_path / 'elapsed.json').write_text(json.dumps({'status': status, 'total': .1, 'socket': .06, 'elapsed': elapsed}))
            assert elapsed < .25, f'CLAIMED_BOUND=.16s; OBSERVED={elapsed:.3f}s'
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


# ══════════════════════════════════════════════════════════════════════════
# 复核 d 的 9 条协议变体：原样纳入（整次请求墙钟 deadline 的结构性验收）。
# 只连 127.0.0.1 随机端口，亚秒级预算。
# ══════════════════════════════════════════════════════════════════════════
class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


@pytest.mark.parametrize('mode', ['chunked_body', 'chunked_size_line', 'eof', 'headers', 'http_error', 'gzip'])
def test_protocol_deadline_variants(mode, tmp_path):
    class Handler(socketserver.BaseRequestHandler):
        def handle(self):
            self.request.recv(8192)
            try:
                if mode == 'headers':
                    for b in b'HTTP/1.1 200 OK\r\nX-Slow: abcdefghijklmnop\r\nContent-Length: 2\r\n\r\n{}':
                        self.request.sendall(bytes([b]))
                        time.sleep(.02)
                    return
                if mode == 'gzip':
                    body = gzip.compress(b'{"items":[]}')
                    self.request.sendall(f'HTTP/1.1 200 OK\r\nContent-Encoding: gzip\r\nContent-Length: {len(body)}\r\n\r\n'.encode() + body)
                    return
                if mode.startswith('chunked'):
                    self.request.sendall(b'HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n')
                    if mode == 'chunked_size_line':
                        for b in b'00000000000000000014\r\n':
                            self.request.sendall(bytes([b]))
                            time.sleep(.02)
                    else:
                        self.request.sendall(b'14\r\n')
                elif mode == 'eof':
                    self.request.sendall(b'HTTP/1.0 200 OK\r\n\r\n')
                else:
                    self.request.sendall(b'HTTP/1.1 401 Unauthorized\r\nContent-Length: 20\r\n\r\n')
                for i in range(20):
                    self.request.sendall(b'x')
                    if i < 19:
                        time.sleep(.02)
                if mode.startswith('chunked'):
                    self.request.sendall(b'\r\n0\r\n\r\n')
            except OSError:
                pass

    server = Server(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': .01}, daemon=True)
    thread.start()
    sockets = []
    original = mod._underlying_socket

    def record(stream):
        sock = original(stream)
        sockets.append(type(sock).__name__ if sock else None)
        return sock

    try:
        with patch.object(mod, 'MAX_TOTAL_SECONDS', .1), patch.object(mod, 'DEFAULT_TIMEOUT', .06), \
                patch.object(mod, '_underlying_socket', side_effect=record):
            start = time.monotonic()
            outcome = 'returned'
            try:
                code, body = mod.urllib_transport('GET', f'http://127.0.0.1:{server.server_address[1]}/', {}, None, .06)
            except Exception as exc:
                outcome = type(exc).__name__
            elapsed = time.monotonic() - start
            (tmp_path / 'result.json').write_text(json.dumps({'mode': mode, 'elapsed': elapsed, 'outcome': outcome, 'socket_types': sockets}))
            if mode != 'gzip':
                assert outcome == 'ResponseTooSlow'
                assert elapsed < .25, f'MODE={mode}; ELAPSED={elapsed:.3f}; TOTAL=.1'
            else:
                assert outcome == 'returned'
                assert len(sockets) == 1 and sockets[0] is not None
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_https_real_socket_path(tmp_path):
    cert = tmp_path / 'cert.pem'
    key = tmp_path / 'key.pem'
    subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-keyout', str(key), '-out', str(cert), '-subj', '/CN=localhost', '-days', '1'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)

    class Handler(socketserver.BaseRequestHandler):
        def handle(self):
            self.request.recv(8192)
            try:
                self.request.sendall(b'HTTP/1.1 200 OK\r\nContent-Length: 20\r\n\r\n')
                for i in range(20):
                    self.request.sendall(b'x')
                    time.sleep(.02)
            except OSError:
                pass

    server = Server(('127.0.0.1', 0), Handler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert, key)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': .01}, daemon=True)
    thread.start()
    sockets = []
    original = mod._underlying_socket

    def record(stream):
        sock = original(stream)
        sockets.append(type(sock).__name__ if sock else None)
        return sock

    opening = mod.request.urlopen

    def local_open(req, **kwargs):
        return opening(req, context=ssl._create_unverified_context(), **kwargs)

    try:
        with patch.object(mod, 'MAX_TOTAL_SECONDS', .1), patch.object(mod, 'DEFAULT_TIMEOUT', .06), \
                patch.object(mod, '_underlying_socket', side_effect=record), \
                patch.object(mod.request, 'urlopen', side_effect=local_open):
            start = time.monotonic()
            with pytest.raises(mod.ResponseTooSlow):
                mod.urllib_transport('GET', f'https://127.0.0.1:{server.server_address[1]}/', {}, None, .06)
            elapsed = time.monotonic() - start
            (tmp_path / 'result.json').write_text(json.dumps({'mode': 'https', 'elapsed': elapsed, 'socket_types': sockets}))
            assert sockets == ['SSLSocket']
            assert elapsed < .25
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


@pytest.mark.parametrize('mode', ['keep_alive', 'http_proxy'])
def test_plain_socket_variants(mode, tmp_path):
    requests = []

    class Handler(socketserver.BaseRequestHandler):
        def handle(self):
            data = self.request.recv(8192)
            requests.append(data.split(b'\r\n')[0].decode())
            self.request.sendall(b'HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: keep-alive\r\n\r\n{}')
            time.sleep(.2)

    server = Server(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': .01}, daemon=True)
    thread.start()
    sockets = []
    original = mod._underlying_socket
    opening = mod.request.urlopen

    def record(stream):
        sock = original(stream)
        sockets.append(type(sock).__name__ if sock else None)
        return sock

    try:
        with patch.object(mod, '_underlying_socket', side_effect=record):
            if mode == 'http_proxy':
                opener = mod.request.build_opener(mod.request.ProxyHandler({'http': f'http://127.0.0.1:{server.server_address[1]}'}))
                with patch.object(mod.request, 'urlopen', side_effect=opener.open), \
                        patch.object(mod.request, 'proxy_bypass', return_value=False):
                    assert mod.urllib_transport('GET', 'http://127.0.0.1:1/probe', {}, None, 15) == (200, '{}')
                assert requests[0].startswith('GET http://127.0.0.1:1/probe ')
            else:
                assert mod.urllib_transport('GET', f'http://127.0.0.1:{server.server_address[1]}/', {}, None, 15) == (200, '{}')
        assert len(sockets) == 1 and sockets[0] is not None
        (tmp_path / 'result.json').write_text(json.dumps({'mode': mode, 'socket_types': sockets, 'request': requests[0]}))
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_read_capped_alone_bounds_chunked_trickle(tmp_path):
    """直接测读取层（不经看门狗）：read1 单次可用数据必须在预算附近收敛。

    这是 read1 相对 read(n) 的可观测差异：read(n) 会阻塞到凑满整块或 EOF。
    """

    class Handler(socketserver.BaseRequestHandler):
        def handle(self):
            self.request.recv(8192)
            try:
                self.request.sendall(b'HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n')
                self.request.sendall(b'14\r\n')
                for i in range(20):
                    self.request.sendall(b'x')
                    if i < 19:
                        time.sleep(.02)
                self.request.sendall(b'\r\n0\r\n\r\n')
            except OSError:
                pass

    server = Server(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': .01}, daemon=True)
    thread.start()
    try:
        with patch.object(mod, 'MAX_TOTAL_SECONDS', .1), patch.object(mod, 'DEFAULT_TIMEOUT', .06):
            resp = mod.request.urlopen(f'http://127.0.0.1:{server.server_address[1]}/', timeout=.06)
            try:
                deadline = time.monotonic() + .1
                start = time.monotonic()
                with pytest.raises(mod.ResponseTooSlow):
                    mod._read_capped(resp, deadline)
                elapsed = time.monotonic() - start
                assert elapsed < .25, f'READ_LAYER_BOUND_VIOLATED: {elapsed:.3f}s'
            finally:
                try:
                    resp.close()
                except Exception:  # noqa: BLE001
                    pass
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


# ══════════════════════════════════════════════════════════════════════════
# 复核 f 的收口：连接阶段登记 + 资源回收上限 + 期限竞态最终核对。
# 只连 127.0.0.1 随机端口，亚秒级预算。
# ══════════════════════════════════════════════════════════════════════════
def _aisa_workers():
    return [t for t in threading.enumerate() if t.name in ('aisa-transport', 'aisa-abort')]


def _fd_count():
    try:
        return len(os.listdir('/dev/fd'))
    except OSError:
        return -1


def _settle_workers(seconds=2.0):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if not _aisa_workers():
            return True
        time.sleep(.01)
    return not _aisa_workers()


def _slow_header_server(release):
    """慢响应头服务器：每 0.005s 向同一响应头行补一字节，直到 release 置位。"""

    class Handler(socketserver.BaseRequestHandler):
        def handle(self):
            self.request.recv(8192)
            try:
                self.request.sendall(b'HTTP/1.1 200 OK\r\nX-Slow: ')
                while not release.is_set():
                    self.request.sendall(b'x')
                    time.sleep(.005)
                self.request.sendall(b'\r\nContent-Length: 2\r\n\r\n{}')
            except OSError:
                pass

    server = Server(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': .01}, daemon=True)
    thread.start()
    return server, thread


def test_connection_phase_socket_is_registered_for_abort(tmp_path):
    """慢响应头阶段（urlopen 尚未返回）holder 里必须已有底层 socket，_abort 才有对象可关。"""
    release = threading.Event()
    server, thread = _slow_header_server(release)
    holders = []
    real_abort = mod._abort

    def spy(holder):
        holders.append(dict(holder))
        return real_abort(holder)

    try:
        with patch.object(mod, 'MAX_TOTAL_SECONDS', .03), patch.object(mod, 'DEFAULT_TIMEOUT', .02), \
                patch.object(mod, '_abort', side_effect=spy):
            with pytest.raises(mod.ResponseTooSlow):
                mod.urllib_transport('GET', f'http://127.0.0.1:{server.server_address[1]}/', {}, None, .02)
        (tmp_path / 'header_holder.json').write_text(json.dumps(
            {'registered': [h.get('sock') is not None for h in holders]}))
        assert holders, 'ABORT_NOT_CALLED_ON_TIMEOUT'
        assert holders[0].get('sock') is not None, 'HEADER_PHASE_SOCKET_NOT_REGISTERED'
    finally:
        release.set()
        server.shutdown()
        server.server_close()
        thread.join()
        _settle_workers()


def test_repeated_slow_headers_reclaim_threads_and_fds(tmp_path):
    """连续 16 次慢响应头超时后，worker 线程与 FD 在有界等待内回到基线（不累积）。"""
    release = threading.Event()
    server, thread = _slow_header_server(release)
    try:
        _settle_workers()
        baseline_threads = len(_aisa_workers())
        baseline_fds = _fd_count()
        with patch.object(mod, 'MAX_TOTAL_SECONDS', .03), patch.object(mod, 'DEFAULT_TIMEOUT', .02):
            for _ in range(16):
                with pytest.raises(mod.ResponseTooSlow):
                    mod.urllib_transport('GET', f'http://127.0.0.1:{server.server_address[1]}/', {}, None, .02)
        reclaimed = _settle_workers(2.0)
        record = {'baseline_threads': baseline_threads, 'baseline_fds': baseline_fds,
                  'reclaimed': reclaimed, 'threads': len(_aisa_workers()), 'fds': _fd_count()}
        (tmp_path / 'reclaim.json').write_text(json.dumps(record))
        assert reclaimed, 'TIMEOUT_WORKERS_DID_NOT_EXIT_WITHIN_BOUND'
        assert len(_aisa_workers()) <= baseline_threads + 2, 'TIMEOUT_WORKERS_ACCUMULATE_WITHOUT_LIMIT'
        assert _fd_count() <= baseline_fds + 4, 'FD_LEAK_AFTER_REPEATED_TIMEOUTS'
    finally:
        release.set()
        server.shutdown()
        server.server_close()
        thread.join()
        _settle_workers()


def test_parallel_admission_is_atomic_and_bounded(tmp_path):
    """16 个调用方 barrier 同时到达：准入必须在建线程前原子预占名额。

    恰好 MAX_LINGERING_REQUESTS 个获准、其余 ResponseBusy；释放后计数归零、正常请求恢复。
    只读计数再放锁的「检查后行动」会让 16 个请求全部获准，此用例必须能抓住。
    """
    release = threading.Event()
    barrier = threading.Barrier(16)
    entered = []
    lock = threading.Lock()

    def stuck(*args, **kwargs):
        with lock:
            entered.append(threading.get_ident())
        release.wait(5)
        return 200, '{}'

    def invoke(_):
        barrier.wait()
        try:
            mod.urllib_transport('GET', 'http://127.0.0.1:1/', {}, None, .03)
            return 'ok'
        except mod.ResponseTooSlow:
            return 'timeout'
        except mod.ResponseBusy:
            return 'busy'

    try:
        _settle_workers()
        base = len(_aisa_workers())
        with patch.object(mod, 'MAX_TOTAL_SECONDS', .1), \
                patch.object(mod, '_perform_request', side_effect=stuck):
            with ThreadPoolExecutor(max_workers=16) as pool:
                outcomes = list(pool.map(invoke, range(16)))
            admitted = len(entered)
            busy = outcomes.count('busy')
            (tmp_path / 'admission.json').write_text(json.dumps(
                {'outcomes': outcomes, 'admitted': admitted, 'busy': busy,
                 'live_workers': len(_aisa_workers()) - base,
                 'in_flight': mod._lingering_count(), 'cap': mod.MAX_LINGERING_REQUESTS}))
            # 已获准的整批仍占着名额，第 17 个必须被拒。
            with pytest.raises(mod.ResponseBusy):
                mod.urllib_transport('GET', 'http://127.0.0.1:1/', {}, None, .03)
            assert admitted == mod.MAX_LINGERING_REQUESTS, 'CONCURRENT_REQUESTS_BYPASS_LIMIT'
            assert busy == 16 - mod.MAX_LINGERING_REQUESTS, 'BUSY_COUNT_MISMATCH'
    finally:
        release.set()
        _settle_workers(3.0)
    assert mod._lingering_count() == 0, 'ADMISSION_SLOT_LEAKED'
    with patch.object(mod, '_perform_request', return_value=(200, '{}')):
        assert mod.urllib_transport('GET', 'http://127.0.0.1:1/', {}, None, .03) == (200, '{}')


@pytest.mark.parametrize('mode', ['exact', 'stale_clock'])
def test_deadline_completed_result_is_discarded(mode):
    """恰好在 deadline 完成（exact）与「拿读取阶段旧观测当完成时刻」（stale_clock）都须丢弃。"""
    clock = {'now': 0}

    def perform(*args):
        if mode == 'stale_clock':
            args[-1]['clock'] = .01  # 读取阶段的旧观测，不得当作实际完成时刻
        clock['now'] = .1 if mode == 'exact' else .2
        return 200, '{}'

    with module_time('aisa_source', monotonic=lambda: clock['now']), \
            patch.object(mod, 'MAX_TOTAL_SECONDS', .1), \
            patch.object(mod, '_perform_request', side_effect=perform):
        with pytest.raises(mod.ResponseTooSlow):
            mod.urllib_transport('GET', 'http://127.0.0.1:1/', {}, None, .05)

# ══════════════════════════════════════════════════════════════════════════
# 复核 h：线程构造失败的名额泄漏修复验收。
# ══════════════════════════════════════════════════════════════════════════
def test_thread_constructor_failure_does_not_leak_slots():
    """连续 8 次 Thread 构造抛 RuntimeError，计数始终归零，撤掉故障后正常请求成功。

    变异对照：把 Thread 构造移回 try 之外，此测试必须变红（名额泄漏导致 ResponseBusy）。
    """
    _settle_workers()
    assert mod._lingering_count() == 0, 'PRE: SLOTS_NOT_ZERO'

    real_thread = threading.Thread
    failures = {'remaining': 8}

    def failing_thread(*args, **kwargs):
        if kwargs.get('name') == 'aisa-transport' and failures['remaining'] > 0:
            failures['remaining'] -= 1
            raise RuntimeError('simulated Thread() construction failure')
        return real_thread(*args, **kwargs)

    with patch.object(mod.threading, 'Thread', side_effect=failing_thread):
        for i in range(8):
            with pytest.raises(RuntimeError, match='construction failure'):
                mod.urllib_transport('GET', 'http://127.0.0.1:1/', {}, None, .03)
            assert mod._lingering_count() == 0, f'SLOT_LEAKED_AFTER_CONSTRUCTOR_FAILURE_{i}'

    # 故障撤掉后正常请求必须成功（不被 ResponseBusy 拒绝）。
    with patch.object(mod, '_perform_request', return_value=(200, '{}')):
        code, body = mod.urllib_transport('GET', 'http://127.0.0.1:1/', {}, None, .03)
    assert (code, body) == (200, '{}'), 'NORMAL_REQUEST_REJECTED_AFTER_RECOVERY'
    _settle_workers()
    assert mod._lingering_count() == 0, 'POST: SLOTS_NOT_ZERO'


def test_thread_start_failure_does_not_leak_slots():
    """thread.start() 抛异常时名额也归还。"""
    _settle_workers()
    assert mod._lingering_count() == 0

    real_thread = threading.Thread

    class UnstartableThread(real_thread):
        def start(self):
            raise RuntimeError('simulated start() failure')

    with patch.object(mod.threading, 'Thread', UnstartableThread):
        for i in range(4):
            with pytest.raises(RuntimeError, match='start\\(\\) failure'):
                mod.urllib_transport('GET', 'http://127.0.0.1:1/', {}, None, .03)
            assert mod._lingering_count() == 0, f'SLOT_LEAKED_AFTER_START_FAILURE_{i}'

    with patch.object(mod, '_perform_request', return_value=(200, '{}')):
        assert mod.urllib_transport('GET', 'http://127.0.0.1:1/', {}, None, .03) == (200, '{}')
    _settle_workers()


def test_worker_exception_does_not_leak_slots():
    """worker 内部异常（非 BaseException）也由 finally 归还名额。"""
    _settle_workers()
    assert mod._lingering_count() == 0

    def exploding(*args, **kwargs):
        raise ValueError('simulated worker explosion')

    for _ in range(4):
        with pytest.raises(ValueError, match='worker explosion'):
            with patch.object(mod, '_perform_request', side_effect=exploding):
                mod.urllib_transport('GET', 'http://127.0.0.1:1/', {}, None, .03)
        _settle_workers()
        assert mod._lingering_count() == 0, 'SLOT_LEAKED_AFTER_WORKER_EXCEPTION'


def test_timeout_then_late_completion_keeps_count_nonnegative():
    """主调用方超时后 worker 迟到完成：名额恰好归还一次，计数不出现负数。"""
    _settle_workers()
    assert mod._lingering_count() == 0

    finish = threading.Event()

    def slow_perform(*args, **kwargs):
        finish.wait(5)
        return 200, '{}'

    clock = {'now': 0}
    with module_time('aisa_source', monotonic=lambda: clock['now']), \
            patch.object(mod, 'MAX_TOTAL_SECONDS', .05), \
            patch.object(mod, '_perform_request', side_effect=slow_perform):
        clock['now'] = 0
        with pytest.raises(mod.ResponseTooSlow):
            mod.urllib_transport('GET', 'http://127.0.0.1:1/', {}, None, .03)
        # worker 还在跑，名额应该仍被占用（count >= 1）或刚被归还（count == 0）
        count_during = mod._lingering_count()
        assert count_during >= 0, f'NEGATIVE_COUNT_DURING_LATE_WORK: {count_during}'

    # 放 worker 完成
    finish.set()
    _settle_workers(3.0)
    final = mod._lingering_count()
    assert final == 0, f'SLOT_LEAKED_AFTER_LATE_COMPLETION: {final}'
    assert final >= 0, f'NEGATIVE_COUNT_AFTER_LATE_COMPLETION: {final}'


def test_mixed_failure_modes_all_recover():
    """混合：构造失败 + start 失败 + worker 异常 + 正常，计数始终归零。"""
    _settle_workers()
    assert mod._lingering_count() == 0

    real_thread = threading.Thread
    scenario = {'mode': 'constructor'}

    def controlled_thread(*args, **kwargs):
        if kwargs.get('name') == 'aisa-transport' and scenario['mode'] == 'constructor':
            raise RuntimeError('constructor fail')
        return real_thread(*args, **kwargs)

    # 1) 构造失败
    with patch.object(mod.threading, 'Thread', side_effect=controlled_thread):
        with pytest.raises(RuntimeError):
            mod.urllib_transport('GET', 'http://127.0.0.1:1/', {}, None, .03)
    assert mod._lingering_count() == 0

    # 2) worker 异常
    with patch.object(mod, '_perform_request', side_effect=ValueError('boom')):
        with pytest.raises(ValueError):
            mod.urllib_transport('GET', 'http://127.0.0.1:1/', {}, None, .03)
    _settle_workers()
    assert mod._lingering_count() == 0

    # 3) 正常请求
    with patch.object(mod, '_perform_request', return_value=(200, '{}')):
        assert mod.urllib_transport('GET', 'http://127.0.0.1:1/', {}, None, .03) == (200, '{}')
    _settle_workers()
    assert mod._lingering_count() == 0


if __name__ == '__main__':
    unittest.main()
