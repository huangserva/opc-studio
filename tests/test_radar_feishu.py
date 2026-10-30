"""B2-a 飞书发送器离线测试：16 条合同反例逐条覆盖。

本机随机端口起假飞书服务，不连真飞书、不调用 AIsa。假 Webhook/密钥用 TESTTOKEN123 / TESTSECRET456。
"""
import base64
import hashlib
import hmac
import importlib.util
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
FEISHU_PATH = ROOT / 'scripts/radar_feishu.py'
TEST_WEBHOOK_TOKEN = 'TESTTOKEN123'
TEST_SECRET = 'TESTSECRET456'


def load_module(path=FEISHU_PATH, name='radar_feishu_under_test'):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def isolate_feishu(test, root):
    """每条用例使用独立脚本副本，CLI/HTTP worker 的 __file__ 也落在临时根。"""
    path = root / 'scripts/radar_feishu.py'
    path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(FEISHU_PATH, path)
    values = {
        'FEISHU_RADAR_LEDGER': str(root / 'feishu-ledger.json'),
        'FEISHU_RADAR_WEBHOOK': '', 'FEISHU_RADAR_SECRET': '',
        'RADAR_PUBLIC_BASE': 'http://127.0.0.1:8766',
        'NO_PROXY': '*', 'no_proxy': '*',
    }
    # 可选证据出口同样不能继承外部目录；保留原有条件与内容断言。
    for key in ('FEISHU_TEST_ARTIFACT_DIR', 'FEISHU_B2A3_ARTIFACT_DIR'):
        if os.environ.get(key):
            values[key] = str(root / 'artifacts' / key)
    env = patch.dict(os.environ, values)
    env.start()
    test.addCleanup(env.stop)
    return path


class FakeFeishu:
    """假飞书服务：可编排状态码/业务码/延迟，并记录全部请求体。"""

    def __init__(self, behaviour=None):
        self.requests = []
        self.behaviour = behaviour or (lambda index: (200, {'code': 0, 'msg': 'success'}))
        feishu = self
        outer = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.1'

            def log_message(self, *args):
                pass

            def do_POST(self):
                length = int(self.headers.get('Content-Length', '0'))
                raw = self.rfile.read(length) if length else b'{}'
                try:
                    parsed = json.loads(raw.decode('utf-8'))
                except ValueError:
                    parsed = None
                outer.requests.append({'path': self.path, 'raw': raw, 'json': parsed})
                status, payload = feishu.behaviour(len(outer.requests) - 1)
                if status == 'sleep':
                    time.sleep(payload)
                    status, payload = 200, {'code': 0}
                if status == 'refuse':
                    self.close_connection = True
                    return
                if status == 'nonjson':
                    body = b'<html>not json</html>'
                    self.send_response(200)
                    self.send_header('Content-Type', 'text/html')
                    self.send_header('Content-Length', str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                body = json.dumps(payload).encode('utf-8')
                self.send_response(status)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def webhook(self):
        return 'http://127.0.0.1:{}/open-apis/bot/v2/hook/{}'.format(self.port, TEST_WEBHOOK_TOKEN)

    def close(self):
        self.server.shutdown()
        self.server.server_close()


def viral_event(index, **over):
    event = {
        'event_id': 'radar:tweet:{}:initial'.format(2000000 + index),
        'type': ['lowfan_burst', 'new_fast', 'hourly_delta', 'upgrade_100k'][index % 4],
        'created_at': '2026-10-15T02:00:00+00:00',
        'status': 'pending',
        'run_id': 'run-x',
        'clue_id': 'clue-{}'.format(1000 + index),
        'tweet': {
            'tweet_id': '2110000000000000{:03d}'.format(index),
            'author': 'author_{}'.format(index),
            'followers': 800 + index,
            'published_at': '2026-10-15T01:00:00+00:00',
            'text': '正文 {}：讲清楚怎么跑通 Agent 流水线'.format(index),
            'views': 100000 + index, 'likes': 900, 'retweets': 40, 'replies': 12,
            'hourly_delta': 1500 + index,
        },
    }
    event.update(over)
    return event


def with_env(**over):
    saved = {key: os.environ.get(key) for key in over}
    os.environ.update({key: str(value) for key, value in over.items()})
    return saved


def restore(saved):
    for key, value in saved.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


class FeishuSenderTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.mkdtemp(prefix='b2a-', dir='/tmp')
        self.addCleanup(shutil.rmtree, self.folder, ignore_errors=True)
        self.feishu_path = isolate_feishu(self, Path(self.folder))
        self.ledger = str(Path(self.folder) / 'feishu-ledger.json')
        self.saved = with_env(FEISHU_RADAR_LEDGER=self.ledger, FEISHU_RADAR_SECRET=TEST_SECRET,
                              RADAR_PUBLIC_BASE='http://127.0.0.1:8766')
        os.environ.pop('FEISHU_RADAR_WEBHOOK', None)
        self.feishu = None

    def tearDown(self):
        if self.feishu:
            self.feishu.close()
        restore(self.saved)
        shutil.rmtree(self.folder, ignore_errors=True)

    def start(self, behaviour=None):
        self.feishu = FakeFeishu(behaviour)
        os.environ['FEISHU_RADAR_WEBHOOK'] = self.feishu.webhook
        return load_module(self.feishu_path)

    def ledger_records(self):
        return json.loads(Path(self.ledger).read_text(encoding='utf-8'))['records']

    # ---- 反例 1：同一 event_id 两次调用
    def test_01_duplicate_event_id_sends_once(self):
        feishu = self.start()
        events = [viral_event(0)]
        first = feishu.send_events([dict(events[0])])
        second = feishu.send_events([dict(events[0])])
        self.assertEqual(first, [{'event_id': events[0]['event_id'], 'status': 'sent'}])
        self.assertEqual(second, [{'event_id': events[0]['event_id'], 'status': 'sent'}])
        self.assertEqual(len(self.feishu.requests), 1, '重复事件必须只发一次请求')

    # ---- 反例 2：发送成功但写账本前崩溃 → 下轮重发恰好一次并带标记
    def test_02_crash_before_ledger_write_resends_once(self):
        feishu = self.start()
        event = viral_event(1)
        original = feishu._write_ledger
        state = {'calls': 0}

        def crashing(path, data):
            state['calls'] += 1
            if state['calls'] == 2:            # 第一次「发送中」写完后崩溃
                raise KeyboardInterrupt('模拟写账本前崩溃')
            return original(path, data)

        feishu._write_ledger = crashing
        with self.assertRaises(KeyboardInterrupt):
            feishu.send_events([dict(event)])
        self.assertEqual(len(self.feishu.requests), 1)
        feishu._write_ledger = original
        records = self.ledger_records()
        self.assertEqual(records[event['event_id']]['status'], 'sending')
        again = feishu.send_events([dict(event)])
        self.assertEqual(len(self.feishu.requests), 2, '必须恰好重发一次')
        self.assertEqual(again[0]['status'], 'sent')
        sent_card = json.dumps(self.feishu.requests[1]['json'], ensure_ascii=False)
        self.assertIn('重发：上次结果未知', sent_card)
        third = feishu.send_events([dict(event)])
        self.assertEqual(len(self.feishu.requests), 2, '第三次不得再发')
        self.assertEqual(third[0]['status'], 'sent')

    # ---- 反例 3：业务码
    def test_03_business_codes(self):
        def bad_sign(index):
            return 200, {'code': 19021, 'msg': 'sign match fail or timestamp is not within one hour'}
        feishu = self.start(bad_sign)
        result = feishu.send_events([viral_event(2), viral_event(3)])
        self.assertEqual(result, [], 'code=19021 不得返回 sent')
        self.assertEqual(len(self.feishu.requests), 1, '两条合一张卡，只发一次')
        self.assertEqual(self.ledger_records()[viral_event(2)['event_id']]['status'], 'error')

        self.feishu.close()
        self.feishu = None
        limited = FakeFeishu(lambda index: (200, {'code': 9499, 'msg': 'rate limited'}))
        os.environ['FEISHU_RADAR_WEBHOOK'] = limited.webhook
        self.feishu = limited
        feishu = load_module(self.feishu_path)
        result = feishu.send_events([viral_event(i) for i in range(25)])
        self.assertEqual(result, [])
        self.assertEqual(len(limited.requests), 1, '限频后本次剩余卡片必须 0 请求')

    # ---- 反例 4：HTTP 500 / 非 JSON / 拒连 / 睡 30 秒
    def test_04_transport_failures_return_in_time(self):
        for label, behaviour in [
            ('http500', lambda index: (500, {'code': 0})),
            ('nonjson', lambda index: ('nonjson', None)),
            ('refused', lambda index: ('refuse', None)),
        ]:
            feishu = self.start(behaviour)
            started = time.monotonic()
            result = feishu.send_events([viral_event(4)])
            elapsed = time.monotonic() - started
            self.assertEqual(result, [], label + ' 不得返回 sent')
            self.assertLess(elapsed, 11, label + ' 返回过慢：' + str(elapsed))
            self.feishu.close()
            self.feishu = None

        feishu = self.start(lambda index: ('sleep', 30))
        started = time.monotonic()
        result = feishu.send_events([viral_event(5)])
        elapsed = time.monotonic() - started
        self.assertEqual(result, [], '超时不得返回 sent')
        self.assertLessEqual(elapsed, 11, '超时返回应 ≤11 秒，实际 ' + str(elapsed))

    # ---- 反例 5：未配置 Webhook
    def test_05_missing_webhook_sends_nothing(self):
        log_path = feishu_log_path(Path(self.folder))
        if log_path.exists():
            log_path.unlink()
        feishu = load_module(self.feishu_path)
        feishu._NO_WEBHOOK_WARNED = False
        first = feishu.send_events([viral_event(6)])
        second = feishu.send_events([viral_event(7)])
        self.assertEqual(first, [])
        self.assertEqual(second, [])
        lines = [line for line in log_path.read_text(encoding='utf-8').splitlines() if line.strip()]
        self.assertEqual(len(lines), 1, '同一进程只应记一条日志，实际 ' + str(len(lines)))
        self.assertIn('FEISHU_RADAR_WEBHOOK', json.loads(lines[0])['message'])

    # ---- 反例 6：签名
    def test_06_signature(self):
        feishu = self.start()
        before = int(time.time())
        feishu.send_events([viral_event(8)])
        body = self.feishu.requests[0]['json']
        self.assertIn('sign', body)
        self.assertIn('timestamp', body)
        expected = base64.b64encode(hmac.new(
            '{}\n{}'.format(body['timestamp'], TEST_SECRET).encode('utf-8'), b'', hashlib.sha256).digest()).decode()
        self.assertEqual(body['sign'], expected)
        self.assertGreaterEqual(int(body['timestamp']), before)

        restore({'FEISHU_RADAR_SECRET': None})
        feishu.send_events([viral_event(9)])
        body = self.feishu.requests[-1]['json']
        self.assertNotIn('sign', body)
        self.assertNotIn('timestamp', body)

    # ---- 反例 7：分卡与部分失败
    def test_07_cards_split_and_partial_failure(self):
        def behaviour(index):
            if index == 1:
                return 500, {'code': 0}
            return 200, {'code': 0, 'msg': 'success'}
        feishu = self.start(behaviour)
        events = [viral_event(i) for i in range(25)]
        result = feishu.send_events([dict(e) for e in events])
        sent = {row['event_id'] for row in result}
        self.assertEqual(len(self.feishu.requests), 3, '25 条应拆成 3 张卡')
        for row in self.feishu.requests:
            body = row['json']
            card = body['card']
            count = int(card['header']['title']['content'].split('·')[-1].replace('条', '').strip())
            self.assertLessEqual(count, 10)
            self.assertLessEqual(len(row['raw']), 18 * 1024, '请求体必须 ≤18KB')
        # 第 2 张卡（第 11–20 条）失败 → 只有第 1、3 张的事件是 sent
        self.assertEqual(sent, {event['event_id'] for event in events[:10] + events[20:]})
        records = self.ledger_records()
        self.assertEqual(records[events[10]['event_id']]['status'], 'error')
        self.assertEqual(records[events[0]['event_id']]['status'], 'sent')
        self.assertEqual(records[events[24]['event_id']]['status'], 'sent')

    # ---- 反例 8：正文注入与编码
    def test_08_text_injection_and_encoding(self):
        feishu = self.start()
        nasty = {
            'link': '[点我](https://evil.example)',
            'at': '<at id=all></at>',
            'font': "<font color='red'>x</font>",
            'bold': '**粗**',
            'newline': '第一行\n第二行\r\n第三行',
            'emoji': '🔥👨‍👩‍👧‍👦🇨🇳生僻字龘𠮷',
            'long': '长' * 5000,
            'empty': '',
            'null': None,
        }
        events = [viral_event(20 + offset, tweet={**viral_event(20 + offset)['tweet'], 'text': text})
                  for offset, text in enumerate(nasty.values())]
        result = feishu.send_events([dict(e) for e in events])
        self.assertEqual(len(result), len(events))
        for row in self.feishu.requests:
            card = row['json']['card']
            for element in card['elements']:
                if element.get('tag') == 'div':
                    self.assertEqual(element['text']['tag'], 'plain_text', '正文必须用 plain_text')
                if element.get('tag') == 'action':
                    for action in element['actions']:
                        self.assertEqual(action['text']['tag'], 'plain_text')
            raw = row['raw']
            raw.decode('utf-8')                      # 必须是合法 UTF-8
            for element in card['elements']:
                if element.get('tag') == 'action':
                    for action in element['actions']:
                        self.assertNotIn('evil.example', action['url'],
                                         '正文里的链接不得变成可点按钮')
        contents = [element['text']['content'] for row in self.feishu.requests
                    for element in row['json']['card']['elements'] if element.get('tag') == 'div']
        joined = '\n'.join(contents)
        self.assertIn('[点我](https://evil.example)', joined, '应原样保留字面文本')
        self.assertIn('<at id=all></at>', joined)
        self.assertIn('<font color=\'red\'>x</font>', joined)
        self.assertIn('**粗**', joined)
        self.assertIn('🔥', joined)
        self.assertTrue(any('…' in text for text in contents), '超长正文应被截断')
        self.assertFalse(any('�' in text for text in contents), '不得出现替换字符')
        self.assertTrue(any('第一行 第二行 第三行' in text for text in contents), '换行应被压平而不是报错')

    # ---- 反例 9：链接防篡改
    def test_09_link_tampering(self):
        feishu = self.start()
        bad = [
            viral_event(30, tweet={**viral_event(30)['tweet'], 'url': 'javascript:alert(1)'}),
            viral_event(31, tweet={**viral_event(31)['tweet'], 'author': 'evil.example/x'}),
            viral_event(32, tweet={**viral_event(32)['tweet'], 'author': 'a"onmouseover="1'}),
            viral_event(33, tweet={**viral_event(33)['tweet'], 'tweet_id': 'abc<script>'}),
            viral_event(34, tweet={**viral_event(34)['tweet'], 'url': 'https://evil.example/x'}),
        ]
        for offset, clue in enumerate(['a"b', 'x<y', 'a&b', 'a/b', '../../etc/passwd']):
            bad.append(viral_event(35 + offset, clue_id=clue))
        feishu.send_events([dict(e) for e in bad])
        urls = []
        for row in self.feishu.requests:
            for element in row['json']['card']['elements']:
                if element.get('tag') == 'action':
                    urls.extend(action['url'] for action in element['actions'])
        self.assertTrue(all(u.startswith('https://x.com/') or u.startswith('http://127.0.0.1:8766/clues.html')
                            or u.startswith('http://127.0.0.1:8766/twitter-radar.html#tweet=')
                            for u in urls), '不得出现外域或注入 URL: ' + json.dumps(urls, ensure_ascii=False))
        expected_urls = []
        for index, event in enumerate(bad):
            if index not in (1, 2, 3):
                expected_urls.append('https://x.com/{}/status/{}'.format(
                    event['tweet']['author'], event['tweet']['tweet_id']))
            if index < 5:
                expected_urls.append('http://127.0.0.1:8766/clues.html?clue=' + event['clue_id'])
            if index != 3:
                expected_urls.append('http://127.0.0.1:8766/twitter-radar.html#tweet=' + event['tweet']['tweet_id'])
        self.assertEqual(urls, expected_urls)
        allowed = set()
        for event in bad:
            url = feishu._tweet_url(event.get('tweet'))
            if url:
                allowed.add(url)
        for url in urls:
            if url.startswith('https://x.com/'):
                self.assertIn(url, allowed, '原帖链接必须由合法 handle + 数字 id 重建')
        self.assertLessEqual(len([u for u in urls if u.startswith('https://x.com/')]), len(bad))

    # ---- 反例 10：null 指标
    def test_10_null_metrics(self):
        feishu = self.start()
        tweet = {**viral_event(40)['tweet'],
                 'followers': None, 'views': None, 'likes': None,
                 'retweets': None, 'replies': None, 'hourly_delta': None}
        feishu.send_events([viral_event(40, tweet=tweet)])
        content = [element['text']['content'] for element in self.feishu.requests[0]['json']['card']['elements']
                   if element.get('tag') == 'div'][0]
        self.assertIn('粉丝未知', content)
        self.assertIn('浏览 —', content)
        self.assertIn('赞 —', content)
        self.assertIn('每小时新增 —', content)
        self.assertNotIn('粉丝 0', content)
        self.assertNotIn('浏览 0', content)

    # ---- 反例 11：日报 unknown_call_rounds
    def test_11_daily_summary_unknown_calls(self):
        summary = {'rounds': 24, 'api_calls': 300, 'unknown_call_rounds': 2, 'coverage': [1, 0.9],
                   'viral_counts': {'lowfan_burst': 3, 'new_fast': 1}, 'shadow_hits': {'lowfan_hours_6': 2}}
        event = {'event_id': 'radar:daily_summary:2026-10-15', 'type': 'daily_summary',
                 'date': '2026-10-15', 'summary': summary}
        module = load_module(self.feishu_path)
        card = module.build_daily_card(event)
        content = '\n'.join(e['text']['content'] for e in card['card']['elements'])
        self.assertIn('已知调用 300 次（另有 2 轮未统计）', content)
        self.assertNotIn('共调用', content)
        self.assertNotIn('共 300', content)

    # ---- 反例 12：fault / recovery / 未知 type
    def test_12_fault_recovery_and_unknown_type(self):
        feishu = self.start()
        events = [{'event_id': 'radar:monitor_fault:2026-10-15T07', 'type': 'monitor_fault',
                   'failures': 3, 'coverage': 0.4},
                  {'event_id': 'radar:monitor_recovery:2026-10-15T07', 'type': 'monitor_recovery',
                   'coverage': 1},
                  {'event_id': 'radar:mystery:1', 'type': 'brand_new_kind', 'payload': 1}]
        result = feishu.send_events([dict(e) for e in events])
        sent = {row['event_id'] for row in result}
        self.assertEqual(sent, {events[0]['event_id'], events[1]['event_id']})
        templates = [row['json']['card']['header']['template'] for row in self.feishu.requests]
        self.assertIn('red', templates)
        self.assertIn('green', templates)
        contents = [element['text']['content'] for row in self.feishu.requests
                    for element in row['json']['card']['elements'] if element.get('tag') == 'div']
        self.assertTrue(any('连续失败轮数：3' in text for text in contents))
        self.assertTrue(any('本轮覆盖率：40.0%' in text for text in contents), '覆盖率应按百分比展示')
        self.assertTrue(any('本轮覆盖率：100.0%' in text for text in contents))
        self.assertNotIn('radar:mystery:1', self.ledger_records())

    # ---- 反例 13：凭据不外泄
    def test_13_no_credential_leak(self):
        log_path = feishu_log_path(Path(self.folder))
        for name in ('feishu.log', 'feishu-ledger.json', 'feishu-ledger.json.corrupt'):
            target = log_path.parent / name
            if target.exists():
                target.unlink()
        feishu = self.start(lambda index: (500, {'code': 19021, 'msg': 'sign match fail'}))
        feishu.send_events([viral_event(i) for i in range(12)])
        feishu.send_events([{'event_id': 'radar:monitor_fault:x', 'type': 'monitor_fault', 'failures': 1}])
        Path(self.ledger).write_text('{ broken')
        feishu.send_events([viral_event(60)])
        cards = [json.dumps(row['json'], ensure_ascii=False) for row in self.feishu.requests]
        blob = '\n'.join([log_path.read_text(encoding='utf-8') if log_path.exists() else '',
                           Path(self.ledger).read_text(encoding='utf-8')] + cards)
        self.assertNotIn(TEST_WEBHOOK_TOKEN, blob, '日志/账本/卡片不得含 Webhook token')
        self.assertNotIn(TEST_SECRET, blob, '日志/账本/卡片不得含密钥')
        self.assertTrue(self.feishu.requests, '本用例必须真的发出过请求，否则断言无意义')
        for card in cards:
            self.assertNotIn('/open-apis/bot/v2/hook/', card, '卡片体内不得出现 webhook 路径')

    # ---- 反例 14：两进程并发同批事件
    def test_14_concurrent_processes_send_once(self):
        self.start()
        script = (
            'import importlib.util,json,os,sys\n'
            'spec=importlib.util.spec_from_file_location("f", {path!r})\n'
            'm=importlib.util.module_from_spec(spec); spec.loader.exec_module(m)\n'
            'events=json.loads({events!r})\n'
            'sys.stdout.write(json.dumps(m.send_events(events)))\n'
        ).format(path=str(self.feishu_path), events=json.dumps([dict(viral_event(i)) for i in range(4)]))
        env = dict(os.environ)
        env['PYTHONPATH'] = str(ROOT)
        procs = [subprocess.Popen([sys.executable, '-c', script], env=env, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE) for _ in range(2)]
        results = []
        for proc in procs:
            out, err = proc.communicate(timeout=60)
            self.assertEqual(proc.returncode, 0, err.decode()[-500:])
            results.append(json.loads(out.decode() or '[]'))
        sent_ids = [row['event_id'] for result in results for row in result]
        expected = {'radar:tweet:{}:initial'.format(2000000 + i) for i in range(4)}
        self.assertEqual(set(sent_ids), expected, '两个进程回执合起来必须正好覆盖 4 个事件')
        # 第二个进程拿到账本后必须全部判为已发送，一个请求都不再发
        self.assertEqual(len(self.feishu.requests), 1,
                         '4 个事件合一张卡，只应有 1 次请求，实际 ' + str(len(self.feishu.requests)))
        remote = []
        for row in self.feishu.requests:
            remote.extend(element['text']['content'] for element in row['json']['card']['elements']
                          if element.get('tag') == 'div')
        for index in range(4):
            self.assertEqual(sum('@author_{}'.format(index) in text for text in remote), 1,
                             '每个 event_id 只能出现在远端一次')

    # ---- 反例 15：账本损坏
    def test_15_corrupt_ledger_is_backed_up(self):
        feishu = self.start()
        Path(self.ledger).write_text('{ this is not json', encoding='utf-8')
        result = feishu.send_events([viral_event(70)])
        self.assertEqual(result[0]['status'], 'sent')
        backups = list(Path(self.ledger).parent.glob('feishu-ledger.json.corrupt-*'))
        self.assertEqual(len(backups), 1, '坏账本必须被备份')
        self.assertEqual(backups[0].read_text(encoding='utf-8'), '{ this is not json')

    # ---- 反例 16：与 B1 联用
    def test_16_integration_with_b1_monitor(self):
        b1 = ROOT
        self.assertTrue((b1 / 'scripts/radar_monitor.py').exists(), '缺少 B1 交付文件')
        feishu = self.start()
        root = Path(self.folder) / 'repo'
        (root / 'scripts').mkdir(parents=True)
        shutil.copy2(b1 / 'scripts/radar_monitor.py', root / 'scripts/radar_monitor.py')
        shutil.copy2(b1 / 'scripts/radar_monitor_config.json', root / 'scripts/radar_monitor_config.json')
        (root / 'scripts/radar_targets.json').write_text(json.dumps(['radar_case']), encoding='utf-8')
        shutil.copy2(FEISHU_PATH, root / 'scripts/radar_feishu.py')
        (root / 'scripts/refresh_twitter_radar.py').write_text(
            'import json, sys\nfrom pathlib import Path\n'
            f'sys.path.insert(0, {str(ROOT / "tests")!r})\n'
            'from test_radar_monitor_a1_fixture import commit_fixture\n'
            'r = Path(__file__).resolve().parents[1]\n'
            'v = json.loads((r / "collector-input.json").read_text())\n'
            'commit_fixture(r, v)\n')
        sys.path.insert(0, str(b1))
        try:
            from scripts.radar_monitor import Monitor
        finally:
            sys.path.pop(0)
        def when(hour, minute=0):
            from datetime import datetime, timedelta, timezone
            from zoneinfo import ZoneInfo
            zone = ZoneInfo('Asia/Shanghai')
            return datetime(2026, 10, 15, hour, minute, tzinfo=zone).astimezone(timezone.utc)

        def tweet(views):
            return {'tweet_id': '2110555000000000001', 'author': {'handle': 'radar_case', 'id': '7'},
                    'published_at': when(6).isoformat(), 'fetched_at': when(hour).isoformat(),
                    'type': 'original', 'text': 'B1 联调正文', 'url': 'https://x.com/radar_case/status/2110555000000000001',
                    'views': views, 'likes': 400, 'retweets': 10, 'replies': 2,
                    'hourly_delta': None, 'views_delta': None, 'interval_hours': None,
                    'is_stale': False}

        monitor = Monitor(root, clue_writer=lambda event: {'id': 'clue-b1-case'},
                          ai_filter=lambda item: True)
        env_backup = os.environ.get('FEISHU_RADAR_LEDGER')
        os.environ['FEISHU_RADAR_LEDGER'] = str(root / 'workspace/twitter-radar/monitor/feishu-ledger.json')
        try:
            for hour, views in ((6, 5000), (7, 60000), (8, 70000)):
                raw = {'schema_version': 1, 'run_id': 'fixture-{}'.format(hour),
                       'started_at': when(hour).isoformat(), 'ended_at': when(hour, 1).isoformat(),
                       'attempted': 1, 'success': 1, 'failed': 0, 'api_calls': 1,
                       'accounts': [{'handle': 'radar_case', 'status': 'success', 'followers': 20000}],
                       'tweets': [] if hour == 6 else [{**tweet(views)}]}
                (root / 'collector-input.json').write_text(json.dumps(raw), encoding='utf-8')
                monitor.run_once(when(hour))
            state = json.loads((monitor.directory / 'state.json').read_text(encoding='utf-8'))
            tweet_events = [e for e in state['events'] if e['type'] != 'daily_summary']
            self.assertTrue(tweet_events, 'B1 至少应产生一条爆款事件')
            self.assertTrue(all(e['status'] == 'sent' for e in tweet_events),
                            'B1 事件应被标记为 sent: ' + json.dumps(tweet_events, ensure_ascii=False)[:400])
            sent_after_flush = len(self.feishu.requests)
            self.assertGreaterEqual(sent_after_flush, 1)
            monitor.run_once(when(9))
            self.assertEqual(len(self.feishu.requests), sent_after_flush,
                             '再次运行不得重复发送已 sent 的事件')
            ledger = json.loads(Path(os.environ['FEISHU_RADAR_LEDGER']).read_text(encoding='utf-8'))
            self.assertTrue(all(r['status'] == 'sent' for r in ledger['records'].values()))
        finally:
            if env_backup is None:
                os.environ.pop('FEISHU_RADAR_LEDGER', None)
            else:
                os.environ['FEISHU_RADAR_LEDGER'] = env_backup


def feishu_log_path(root):
    return root / 'workspace/twitter-radar/monitor/feishu.log'


# B2-a2 新增反例；原 FeishuSenderTests 的 16 条断言保持不变。
import ast
import copy
import fcntl
from unittest.mock import patch


class BoundaryServer:
    def __init__(self, mode, data=None):
        self.requests = []
        outer = self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_GET(self):
                outer.requests.append({'method': 'GET'})
                self.send_response(200); self.end_headers()
                self.wfile.write(b'{"code":0}')
            def do_POST(self):
                raw = self.rfile.read(int(self.headers['Content-Length']))
                outer.requests.append({'method': 'POST', 'raw': raw, 'json': json.loads(raw)})
                try:
                    if mode == 'headers':
                        for byte in b'HTTP/1.1 200 OK\r\nContent-Length: 10\r\n\r\n':
                            self.wfile.write(bytes([byte])); self.wfile.flush(); time.sleep(.04)
                        return
                    if mode == 'delay':
                        time.sleep(data)
                    body = b'{"code":0}'
                    if mode == 'large':
                        body = b'{"code":0,"padding":"' + b'x' * 70000 + b'"}'
                    self.send_response(data if mode == 'redirect' else 200)
                    if mode == 'redirect':
                        self.send_header('Location', '/redirect-target')
                    self.send_header('Content-Length', str(len(body)))
                    self.end_headers()
                    if mode == 'drip':
                        for byte in body:
                            self.wfile.write(bytes([byte])); self.wfile.flush(); time.sleep(.04)
                    else:
                        self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError):
                    pass
        class Http(ThreadingHTTPServer):
            daemon_threads = True
            block_on_close = False
        self.http = Http(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.http.serve_forever, daemon=True)
        self.thread.start()
    @property
    def webhook(self):
        return f'http://127.0.0.1:{self.http.server_port}/hook/{TEST_WEBHOOK_TOKEN}'
    def close(self):
        self.http.shutdown(); self.http.server_close()


class RepairBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='b2a2-boundary-', dir='/tmp')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.feishu_path = isolate_feishu(self, self.root)
        self.m = load_module(self.feishu_path)
        self.m.ROOT = self.root
        self.env = patch.dict(os.environ, {
            'FEISHU_RADAR_WEBHOOK': '', 'FEISHU_RADAR_SECRET': TEST_SECRET,
            'FEISHU_RADAR_LEDGER': str(self.root / 'ledger.json'),
            'RADAR_PUBLIC_BASE': 'https://workbench.example', 'NO_PROXY': '127.0.0.1,localhost'})
        self.env.start(); self.addCleanup(self.env.stop)

    def server(self, mode='ok', data=None):
        if mode == 'error':
            server = FakeFeishu(lambda index: (200, {'code': 19021, 'msg': data or 'failure'}))
        else:
            server = BoundaryServer(mode, data)
        self.addCleanup(server.close)
        os.environ['FEISHU_RADAR_WEBHOOK'] = server.webhook
        return server

    def record(self, e):
        return json.loads((self.root / 'ledger.json').read_text())['records'][e['event_id']]

    def no_leak(self, value):
        blob = json.dumps(value, ensure_ascii=False) if not isinstance(value, str) else value
        self.assertNotIn(TEST_WEBHOOK_TOKEN, blob)
        self.assertNotIn(TEST_SECRET, blob)
        self.assertNotIn(os.environ['FEISHU_RADAR_WEBHOOK'], blob)
        return blob

    def child(self, *args):
        return subprocess.run([sys.executable, str(self.feishu_path), *args], capture_output=True,
                              text=True, timeout=4, env=dict(os.environ))

    def test_h1_drip_and_headers_deadline_no_straggler(self):
        for mode in ('drip', 'headers'):
            with self.subTest(mode=mode):
                server = self.server(mode)
                self.m.CALL_DEADLINE = .25
                e = viral_event(len(server.requests))
                (self.root / 'ledger.json').unlink(missing_ok=True)
                started = time.monotonic()
                self.assertEqual(self.m.send_events([e]), [])
                self.assertLess(time.monotonic() - started, .8)
                self.assertEqual(len(server.requests), 1)
                self.assertEqual(self.record(e)['status'], 'sending')

    def test_h1_cumulative_budget_and_unsent_remainder(self):
        from isolation_patches import TimeBinding
        # 本项只测跨卡片共用预算；真实网络截止另由 drip/headers 用例覆盖。
        # 0.28秒还包含子进程冷启动，不能用其墙钟耗时推断必成功第一张卡。
        self.server()
        self.m.CALL_DEADLINE = .28
        es = [viral_event(i) for i in range(21)]
        clock = [1000.0]
        attempts = []
        def post(payload, webhook, secret, deadline):
            attempts.append((payload, deadline))
            remaining = deadline - clock[0]
            clock[0] += min(.16, remaining)
            return (True, False, '') if remaining >= .16 else (False, False, '结果未知：累计预算耗尽')
        started = time.monotonic()
        with patch.object(self.m, 'time', TimeBinding(time, {'monotonic': lambda: clock[0]})), \
                patch.object(self.m, '_post', side_effect=post):
            result = self.m.send_events(es)
        self.assertLess(time.monotonic() - started, .8)
        self.assertEqual(len(result), 10)
        self.assertEqual(len(attempts), 2)
        self.assertEqual([deadline for _, deadline in attempts], [1000.28, 1000.28])
        self.assertAlmostEqual(clock[0], 1000.28)
        records = json.loads((self.root / 'ledger.json').read_text())['records']
        self.assertNotIn(es[20]['event_id'], records)
        self.assertTrue(all(records[e['event_id']]['status'] == 'sent' for e in es[:10]))
        self.assertTrue(all(records[e['event_id']]['status'] == 'sending' for e in es[10:20]))

    def test_h1_response_limit_rejects_success_json(self):
        self.server('large'); e = viral_event(1)
        self.assertEqual(self.m.send_events([e]), [])
        self.assertIn('64KB', self.record(e)['reason'])
        self.assertEqual(self.record(e)['status'], 'sending')

    def test_h1_lock_wait_has_same_deadline(self):
        server = self.server(); self.m.CALL_DEADLINE = .15
        with (self.root / 'ledger.json.lock').open('a+') as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            started = time.monotonic()
            self.assertEqual(self.m.send_events([viral_event(1)]), [])
            self.assertLess(time.monotonic() - started, .6)
        self.assertEqual(server.requests, [])

    def test_m1_conflicting_duplicate_first_wins_and_one_receipt(self):
        server = self.server(); first = viral_event(1); second = copy.deepcopy(first)
        first['tweet']['text'] = 'FIRST-CONTENT'; second['tweet']['text'] = 'SECOND-CONTENT'
        incoming = [first] + [second] * 12; before = copy.deepcopy(incoming)
        self.assertEqual(len(self.m.send_events(incoming)), 1)
        self.assertEqual(len(server.requests), 1)
        blob = server.requests[0]['raw'].decode()
        self.assertEqual(blob.count('FIRST-CONTENT'), 1)
        self.assertNotIn('SECOND-CONTENT', blob)
        self.assertEqual(len(self.m.send_events(incoming)), 1)
        self.assertEqual(len(server.requests), 1)
        self.assertEqual(before, incoming)

    def kill_commit(self, event):
        marker = self.root / 'paused'; marker.unlink(missing_ok=True)
        code = f'''import importlib.util,json,time
from pathlib import Path
s=importlib.util.spec_from_file_location('f',{str(self.feishu_path)!r});m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
original=m._write_ledger
def hook(path,data):
 if any(r.get('status')=='sent' for r in data['records'].values()):
  Path({str(marker)!r}).touch();time.sleep(30)
 return original(path,data)
m._write_ledger=hook;m.send_events(json.loads({json.dumps([event])!r}))
'''
        proc = subprocess.Popen([sys.executable, '-c', code], env=dict(os.environ),
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            end = time.monotonic() + 4
            while not marker.exists() and time.monotonic() < end and proc.poll() is None:
                time.sleep(.01)
            self.assertTrue(marker.exists(), '未到达远端成功、本地提交前窗口')
        finally:
            proc.kill(); proc.communicate(timeout=2)
        self.assertEqual(proc.returncode, -9)

    def test_m2_real_double_sigkill_exhausted_and_no_false_sent(self):
        server = self.server(); e = viral_event(1)
        self.kill_commit(e)
        self.assertEqual(self.record(e)['unknown_resends'], 0)
        self.kill_commit(e)
        self.assertEqual(self.record(e)['unknown_resends'], 1)
        for _ in range(3):
            self.assertEqual(self.m.send_events([e]), [])
        self.assertEqual(len(server.requests), 2)
        self.assertEqual(self.record(e)['status'], 'unknown_exhausted')
        self.assertIn('重发：上次结果未知', server.requests[1]['raw'].decode())

    def test_m2_definite_business_failure_keeps_normal_retries(self):
        server = self.server('error'); e = viral_event(2)
        for _ in range(4):
            self.assertEqual(self.m.send_events([e]), [])
            self.assertEqual(self.record(e)['status'], 'error')
        self.assertEqual(len(server.requests), 4)
        self.assertEqual(self.record(e)['unknown_resends'], 0)

    def test_m2_unknown_transport_outcomes_are_also_bounded(self):
        server = self.server('large'); e = viral_event(3)
        for _ in range(4):
            self.assertEqual(self.m.send_events([e]), [])
        self.assertEqual(len(server.requests), 2)
        self.assertEqual(self.record(e)['status'], 'unknown_exhausted')

    def test_m3_all_redirect_codes_never_get_or_sent(self):
        for code in (301, 302, 303, 307, 308):
            with self.subTest(code=code):
                server = self.server('redirect', code)
                self.assertEqual(self.m.send_events([viral_event(code)]), [])
                self.assertEqual([r['method'] for r in server.requests], ['POST'])

    def test_m4_huge_single_omits_buttons_and_signed_final_limit(self):
        server = self.server(); os.environ['RADAR_PUBLIC_BASE'] = 'https://example.org/' + 'x' * 19000
        e = viral_event(1)
        self.assertEqual(len(self.m.send_events([e])), 1)
        body = server.requests[0]['json']
        self.assertFalse(any(el['tag'] == 'action' for el in body['card']['elements']))
        self.assertIn('sign', body)
        self.assertLessEqual(len(server.requests[0]['raw']), 18 * 1024)

    def test_m4_cannot_fit_single_is_skipped(self):
        server = self.server()
        with patch.object(self.m, 'build_viral_card', return_value={
            'msg_type': 'interactive', 'card': {'elements': [self.m._text_element('长' * 20000)]}}):
            self.assertEqual(self.m.send_events([viral_event(1)]), [])
        self.assertEqual(server.requests, [])

    def test_m4_signed_unicode_cards_never_cross_limit(self):
        server = self.server(); os.environ['RADAR_PUBLIC_BASE'] = 'https://example.org/' + 'x' * 558
        es = [viral_event(i) for i in range(25)]
        for e in es:
            e['tweet']['text'] = '🙂长' * 4000
        self.assertEqual(len(self.m.send_events(es)), 25)
        self.assertTrue(all(len(r['raw']) <= 18 * 1024 for r in server.requests))
        self.assertTrue(all('sign' in r['json'] for r in server.requests))

    def test_m5_public_base_and_handle_full_validation(self):
        server = self.server()
        for index, base in enumerate(('https://user:pass@example.org', 'https://example.org/\npath',
                                      'https://example.org/\x01path', 'https://example.org/\x85path',
                                      'ftp://example.org', 'https://example.org:bad')):
            os.environ['RADAR_PUBLIC_BASE'] = base
            e = viral_event(index); e['tweet']['author'] = 'writer\n'
            self.assertEqual(len(self.m.send_events([e])), 1)
            urls = [a['url'] for el in server.requests[-1]['json']['card']['elements']
                    if el['tag'] == 'action' for a in el['actions']]
            self.assertEqual(urls, ['http://127.0.0.1:8766/clues.html?clue=' + e['clue_id'],
                                    'http://127.0.0.1:8766/twitter-radar.html#tweet=' + e['tweet']['tweet_id']])

    def test_m6_output_inventory_and_all_exports_redacted(self):
        server = self.server('error', TEST_SECRET + ' ' + TEST_WEBHOOK_TOKEN)
        injected = server.webhook + ' ' + TEST_WEBHOOK_TOKEN + ' ' + TEST_SECRET
        # 静态出口清单：新增 print/write 需要先明确登记并补注入断言。
        tree = ast.parse(FEISHU_PATH.read_text())
        found = set()
        def walk(node, owner='<module>'):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                owner = node.name
            if isinstance(node, ast.Call):
                name = node.func.id if isinstance(node.func, ast.Name) else (
                    node.func.attr if isinstance(node.func, ast.Attribute) else '')
                if name in ('print', 'write', 'write_text'):
                    found.add((owner, name))
            for child in ast.iter_child_nodes(node):
                walk(child, owner)
        walk(tree)
        self.assertEqual(found, {('_log', 'write'), ('_load_ledger', 'write_text'),
                                 ('_write_ledger', 'write'), ('main', 'print'),
                                 ('<module>', 'print')})
        exports = {}
        exports['log-return'] = self.m._log(injected)
        ledger = self.root / 'export-ledger.json'
        self.m._write_ledger(ledger, {'version': 1, 'records': {'x': {'reason': injected}}})
        exports['ledger'] = ledger.read_text()
        ledger.write_text('{broken ' + injected)
        self.m._load_ledger(ledger)
        exports['corrupt-backup'] = next(self.root.glob('export-ledger.json.corrupt-*')).read_text()
        e = viral_event(90); e['tweet']['text'] = injected
        e['tweet']['author'] = injected; e['clue_id'] = injected
        exports['viral-card'] = self.m.build_viral_card([e])
        exports['fault-card'] = self.m.build_fault_card({'failures': injected, 'coverage': injected})
        exports['recovery-card'] = self.m.build_recovery_card({'coverage': injected})
        exports['daily-card'] = self.m.build_daily_card({'date': injected, 'summary': {}})
        exports['text'] = self.m._text_element(injected)
        exports['button'] = self.m._button(injected, injected)
        self.m.send_events([e])
        exports['error-record'] = (self.root / 'ledger.json').read_text()
        self.m.send_events([{'event_id': 'unknown', 'type': injected}])
        exports['log-file'] = (self.root / 'workspace/twitter-radar/monitor/feishu.log').read_text()
        events = self.root / 'input.json'; events.write_text(json.dumps([e]))
        for label, args in [('dry-run', ['--dry-run', str(events)]), ('test', ['--test']),
                            ('invalid-arg', ['--' + TEST_SECRET]),
                            ('missing-input', ['--dry-run', str(self.root / TEST_SECRET)])]:
            child = self.child(*args)
            exports[label + '-stdout'] = child.stdout
            exports[label + '-stderr'] = child.stderr
        for index, request in enumerate(server.requests):
            exports[f'card-{index}'] = request['raw'].decode()
        # 输出持久证据只保留已经过零泄露断言的出口，不复制输入/请求 URL。
        for label, output in exports.items():
            with self.subTest(export=label):
                self.no_leak(output)
        destination = os.environ.get('FEISHU_TEST_ARTIFACT_DIR')
        if destination:
            path = Path(destination); path.mkdir(parents=True, exist_ok=True)
            (path / 'redaction-exports.json').write_text(json.dumps(exports, ensure_ascii=False, indent=2))
            (path / 'redaction-inventory.json').write_text(json.dumps(sorted(found), ensure_ascii=False))

    def test_m6_explicit_credentials_context_and_truncation(self):
        server = self.server(); os.environ['FEISHU_RADAR_WEBHOOK'] = ''; os.environ['FEISHU_RADAR_SECRET'] = ''
        e = viral_event(1); e['tweet']['text'] = server.webhook + ' ' + TEST_SECRET
        self.assertEqual(len(self.m.send_events([e], webhook=server.webhook, secret=TEST_SECRET)), 1)
        self.assertNotIn(TEST_SECRET, server.requests[0]['raw'].decode())
        self.assertNotIn(TEST_WEBHOOK_TOKEN, server.requests[0]['raw'].decode())
        with patch.dict(os.environ, {'FEISHU_RADAR_SECRET': TEST_SECRET}):
            self.assertNotIn(TEST_SECRET[:7], self.m._clean_text('x' * 117 + TEST_SECRET, 120))

    def test_m7_missing_tweet_id_and_bad_dates_isolate_null_metrics(self):
        server = self.server(); good = viral_event(1)
        good['tweet'].update(followers=None, views=None, likes=None, hourly_delta=None)
        no_id = viral_event(2); no_id['tweet'].pop('tweet_id')
        bad_date = viral_event(3); bad_date['tweet']['published_at'] = '2026-99-99T88:00:00Z'
        no_author = viral_event(4); no_author['tweet'].pop('author')
        no_tweet = {'event_id': 'missing', 'type': 'new_fast'}
        self.assertEqual(self.m.send_events([no_tweet, no_id, good, bad_date, no_author]),
                         [{'event_id': good['event_id'], 'status': 'sent'}])
        self.assertEqual(len(server.requests), 1)
        body = server.requests[0]['raw'].decode()
        self.assertIn('粉丝未知', body); self.assertIn('浏览 —', body)
        self.assertIn('每小时新增 —', body)


if __name__ == '__main__':
    unittest.main(verbosity=2)
