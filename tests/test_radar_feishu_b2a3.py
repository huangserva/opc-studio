"""B2-a3 离线增量验收：雷达深链、反馈口径、只读未知账本与脱敏。"""
import copy
import io
import json
import os
import socket
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent))
from test_radar_feishu import (load_module, viral_event, FakeFeishu, TEST_SECRET,
                               TEST_WEBHOOK_TOKEN, FEISHU_PATH, isolate_feishu)
import subprocess


def buttons(card):
    return [a for el in card['card']['elements'] if el.get('tag') == 'action'
            for a in el['actions']]


class B2a3Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='b2a3-', dir='/tmp')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.feishu_path = isolate_feishu(self, self.root)
        self.m = load_module(self.feishu_path)
        self.m.ROOT = self.root
        env = patch.dict(os.environ, {
            'FEISHU_RADAR_WEBHOOK': '', 'FEISHU_RADAR_SECRET': TEST_SECRET,
            'FEISHU_RADAR_LEDGER': str(self.root / 'ledger.json'),
            'RADAR_PUBLIC_BASE': 'https://workbench.example/studio/', 'NO_PROXY': '*'})
        env.start(); self.addCleanup(env.stop)

    def server(self, behaviour=None):
        server = FakeFeishu(behaviour)
        self.addCleanup(server.close)
        os.environ['FEISHU_RADAR_WEBHOOK'] = server.webhook
        return server

    def child(self, *args):
        # 子进程也用实际 CLI；网络函数一旦被调用立即失败，不能悄悄联网。
        code = '''import runpy,socket,sys
def deny(*a,**k):raise AssertionError('只读CLI禁止联网')
socket.getaddrinfo=deny
socket.socket.connect=deny
sys.argv=sys.argv[1:]
runpy.run_path(sys.argv[0],run_name='__main__')
'''
        return subprocess.run([sys.executable, '-c', code, str(self.feishu_path), *args],
                              env=dict(os.environ), capture_output=True, text=True, timeout=4)

    def daily(self, feedback='missing'):
        summary = {'rounds': 17, 'api_calls': 51, 'unknown_call_rounds': 2}
        if feedback != 'missing':
            summary['feedback'] = feedback
        event = {'event_id': 'radar:daily_summary:2026-10-15', 'type': 'daily_summary',
                 'date': '2026-10-15', 'summary': summary}
        return event, '\n'.join(e['text']['content'] for e in self.m.build_daily_card(event)['card']['elements'])

    def feedback(self, rate=2/3):
        return {'status': 'ok', 'message': None,
                'today': {'pushed': 4, 'worth': 2, 'not_worth': 1, 'unreviewed': 1,
                          'useful_rate': rate},
                'recent_7_days': {'pushed': 9, 'worth': 3, 'not_worth': 3, 'unreviewed': 3,
                                  'useful_rate': rate}}

    def test_radar_valid_ascii_ids_and_url(self):
        for ident in ('0', '1', '9' * 25, 123):
            with self.subTest(ident=ident):
                e = viral_event(1); e['tweet']['tweet_id'] = ident
                actions = buttons(self.m.build_viral_card([e]))
                self.assertEqual([a['text']['content'] for a in actions],
                                 ['打开原帖', '线索中心', '去雷达页标记'])
                self.assertEqual(actions[-1]['url'],
                                 'https://workbench.example/studio/twitter-radar.html#tweet=' + str(ident))
                self.assertEqual(actions[-1]['value']['url'], actions[-1]['url'])

    def test_radar_invalid_ids_never_generate_button(self):
        for ident in ('', '9' * 26, '１２３', '١٢٣', '12\n', ' 12', '12 ',
                      '1/2', '1#2', '1?x=2', '1.2', None, True, '１２3', '1\x00'):
            with self.subTest(ident=repr(ident)):
                e = viral_event(1); e['tweet']['tweet_id'] = ident
                actions = buttons(self.m.build_viral_card([e]))
                self.assertNotIn('去雷达页标记', [a['text']['content'] for a in actions])
                self.assertNotIn('打开原帖', [a['text']['content'] for a in actions])

    def test_radar_does_not_depend_on_author_or_clue(self):
        e = viral_event(1); e['tweet']['author'] = 'bad/author'; e['clue_id'] = '../bad'
        actions = buttons(self.m.build_viral_card([e]))
        self.assertEqual([a['text']['content'] for a in actions], ['去雷达页标记'])
        self.assertTrue(actions[0]['url'].endswith('#tweet=' + e['tweet']['tweet_id']))

    def test_invalid_public_bases_use_same_fallback_for_both_internal_buttons(self):
        for base in ('https://user:pass@example.org', 'https://user@example.org',
                     'https://:pass@example.org', 'https://example.org/\npath',
                     'https://example.org/\x01path', 'https://example.org/\x85path',
                     'ftp://example.org', 'javascript:alert(1)', 'https://example.org:bad',
                     'https://example.org:99999', 'https://example.org/?q=1',
                     'https://example.org/#fragment'):
            with self.subTest(base=repr(base)):
                os.environ['RADAR_PUBLIC_BASE'] = base
                e = viral_event(1); card = self.m.build_viral_card([e]); actions = buttons(card)
                self.assertEqual([a['url'] for a in actions], [
                    self.m._tweet_url(e['tweet']),
                    'http://127.0.0.1:8766/clues.html?clue=' + e['clue_id'],
                    'http://127.0.0.1:8766/twitter-radar.html#tweet=' + e['tweet']['tweet_id']])
                self.assertNotIn(base, json.dumps(card, ensure_ascii=False))
                self.assertNotIn('user:pass@', json.dumps(card))

    def test_ten_viral_signed_wire_under_18kb_with_all_buttons(self):
        server = self.server()
        es = [viral_event(i) for i in range(10)]
        for e in es:
            e['tweet']['text'] = '🙂' * 120
            e['tweet']['tweet_id'] = '9' * 22 + e['tweet']['tweet_id'][-3:]
            e['clue_id'] = 'x' * 60 + e['clue_id'][-3:]
        os.environ['RADAR_PUBLIC_BASE'] = 'https://workbench.example/' + '长' * 450
        incoming = copy.deepcopy(es)
        self.assertEqual(len(self.m.send_events(es)), 10)
        self.assertGreater(len(server.requests), 1)
        for request in server.requests:
            self.assertLessEqual(len(request['raw']), 18 * 1024)
            payload = request['json']
            self.assertEqual(payload['sign'], self.m._sign(TEST_SECRET, payload['timestamp']))
            labels = [a['text']['content'] for a in buttons(payload)]
            self.assertEqual(labels.count('去雷达页标记'), labels.count('打开原帖'))
            self.assertEqual(labels.count('去雷达页标记'), labels.count('线索中心'))
        self.assertEqual(incoming, es)
        destination = os.environ.get('FEISHU_B2A3_ARTIFACT_DIR')
        if destination:
            dest = Path(destination); dest.mkdir(parents=True, exist_ok=True)
            (dest/'new-size-observations.json').write_text(json.dumps({
                'events': 10, 'cards': len(server.requests),
                'signed_bytes': [len(r['raw']) for r in server.requests],
                'radar_buttons': sum(a['text']['content'] == '去雷达页标记'
                                     for r in server.requests for a in buttons(r['json']))}, indent=2))

    def test_daily_feedback_missing(self):
        event, text = self.daily()
        self.assertIn('反馈数据不可用', text); self.assertNotIn('有用率 0', text)
        self.assertNotIn('暂无反馈', text)
        self.assertEqual(len(self.m.plan_cards([event])), 1)

    def test_daily_feedback_null(self):
        event, text = self.daily(None)
        self.assertIn('反馈数据不可用', text); self.assertNotIn('有用率 0', text)
        self.assertEqual(len(self.m.plan_cards([event])), 1)

    def test_daily_feedback_unavailable(self):
        for feedback in ({'status': 'unavailable', 'message': '反馈不可用'},
                         {**self.feedback(), 'message': '反馈不可用'}, '反馈不可用', [],
                         {'status': 'ok', 'today': None}):
            with self.subTest(feedback=feedback):
                _, text = self.daily(feedback)
                self.assertIn('反馈数据不可用', text)
                self.assertNotIn('有用率 0', text); self.assertNotIn('暂无反馈', text)

    def test_daily_useful_rate_null_is_no_feedback(self):
        feedback = self.feedback(None)
        for key in ('today', 'recent_7_days'):
            feedback[key].update(worth=0, not_worth=0, unreviewed=feedback[key]['pushed'])
        _, text = self.daily(feedback)
        self.assertIn('今日：值得跟 0 / 不值得 0 / 未反馈 4 / 暂无反馈', text)
        self.assertIn('近 7 天：值得跟 0 / 不值得 0 / 未反馈 9 / 暂无反馈', text)
        self.assertNotIn('0.0%', text)

    def test_daily_normal_and_real_zero_rates(self):
        feedback = self.feedback()
        feedback['recent_7_days']['useful_rate'] = .5
        _, text = self.daily(feedback)
        self.assertIn('今日：值得跟 2 / 不值得 1 / 未反馈 1 / 有用率 66.7%', text)
        self.assertIn('近 7 天：值得跟 3 / 不值得 3 / 未反馈 3 / 有用率 50.0%', text)
        feedback['today'].update(worth=0, not_worth=3, useful_rate=0)
        _, text = self.daily(feedback)
        self.assertIn('有用率 0.0%', text)
        self.assertIn('已知调用 51 次（另有 2 轮未统计）', text)

    def test_unknown_persists_type_first_attempt_and_cli_is_read_only_offline(self):
        server = self.server(lambda i: ('nonjson', None))
        e = viral_event(1)
        self.assertEqual(self.m.send_events([e]), [])
        first = json.loads((self.root/'ledger.json').read_text())['records'][e['event_id']]['started_at']
        self.assertEqual(self.m.send_events([e]), [])
        self.assertEqual(self.m.send_events([e]), [])
        self.assertEqual(len(server.requests), 2)
        path = self.root/'ledger.json'; before = path.read_bytes(); inode = path.stat().st_ino
        files_before = sorted(str(p.relative_to(self.root)) for p in self.root.rglob('*'))
        result = self.child('--unknown')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), [{'event_id': e['event_id'], 'type': e['type'],
                                                     'first_attempt_at': first}])
        self.assertEqual(result.stderr, '')
        self.assertEqual(path.read_bytes(), before); self.assertEqual(path.stat().st_ino, inode)
        self.assertEqual(sorted(str(p.relative_to(self.root)) for p in self.root.rglob('*')), files_before)
        self.assertEqual(len(server.requests), 2)

    def test_first_attempt_survives_definite_failures_and_unknown_resend(self):
        server = self.server(lambda i: (500, {'code': 0}) if i < 2 else ('nonjson', None))
        e = viral_event(1)
        self.assertEqual(self.m.send_events([e]), [])
        path = self.root/'ledger.json'
        first = json.loads(path.read_text())['records'][e['event_id']]['first_attempt_at']
        for _ in range(4):
            self.assertEqual(self.m.send_events([e]), [])
        self.assertEqual(len(server.requests), 4)
        result = self.child('--unknown')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), [{'event_id': e['event_id'], 'type': e['type'],
                                                     'first_attempt_at': first}])

    def test_retry_legacy_record_never_invents_first_attempt_time(self):
        self.server(lambda i: ('nonjson', None))
        e = viral_event(1); path = self.root/'ledger.json'
        path.write_text(json.dumps({'version': 1, 'records': {e['event_id']: {
            'status': 'sending', 'started_at': '2026-10-14T01:00:00Z',
            'unknown_resends': 0, 'resend': False}}}))
        self.assertEqual(self.m.send_events([e]), [])
        result = self.child('--unknown')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), [{'event_id': e['event_id'], 'type': e['type'],
                                                     'first_attempt_at': None}])

    def test_unknown_empty_missing_bad_and_legacy_ledgers(self):
        path = self.root/'ledger.json'
        self.assertEqual(json.loads(self.child('--unknown').stdout), [])
        self.assertFalse(path.exists())
        legacy = {'version': 1, 'records': {
            'legacy': {'status': 'unknown_exhausted', 'started_at': '2026-10-15T01:00:00Z',
                       'unknown_resends': 1, 'resend': True},
            'sent': {'status': 'sent'}, 'pending': {'status': 'sending'},
            'error': {'status': 'error'}, 'bad': None}}
        path.write_text(json.dumps(legacy))
        self.assertEqual(json.loads(self.child('--unknown').stdout),
                         [{'event_id': 'legacy', 'type': '未知', 'first_attempt_at': None}])
        for data in ('{broken', '[]', '{"version":1,"records":[]}'):
            path.write_text(data); before = path.read_bytes()
            result = self.child('--unknown')
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('发送账本读取失败', result.stderr)
            self.assertEqual(path.read_bytes(), before)
            self.assertFalse(list(self.root.glob('*.corrupt-*')))

    def test_unknown_and_test_cannot_accidentally_send_together(self):
        server = self.server()
        result = self.child('--unknown', '--test')
        self.assertEqual(result.returncode, 2)
        self.assertEqual(server.requests, [])

    def test_new_exports_inventory_all_credentials_redacted(self):
        webhook = 'https://webhook.invalid/open-apis/bot/v2/hook/' + TEST_WEBHOOK_TOKEN
        os.environ['FEISHU_RADAR_WEBHOOK'] = webhook
        injected = webhook + ' ' + TEST_WEBHOOK_TOKEN + ' ' + TEST_SECRET
        e = viral_event(1)
        # 测试新按钮的URL/文案同样走现有公共脱敏出口。
        exports = {'radar-button': self.m._button('去雷达页标记 ' + injected, injected)}
        os.environ['RADAR_PUBLIC_BASE'] = 'https://workbench.example/' + TEST_SECRET
        exports['radar-card'] = self.m.build_viral_card([e])
        feedback = self.feedback(); feedback['today']['worth'] = injected
        exports['feedback-card'] = self.m.build_daily_card(self.daily(feedback)[0])
        records = {'version': 1, 'records': {injected: {'status': 'unknown_exhausted',
                   'type': injected, 'first_attempt_at': injected}}}
        (self.root/'ledger.json').write_text(json.dumps(records))
        result = self.child('--unknown')
        self.assertEqual(result.returncode, 0, result.stderr)
        exports['unknown-stdout'] = result.stdout; exports['unknown-stderr'] = result.stderr
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(self.m.main(['--unknown']), 0)
        exports['unknown-main'] = output.getvalue()
        inventory = ['radar-button', 'radar-card', 'feedback-card', 'unknown-stdout',
                     'unknown-stderr', 'unknown-main']
        self.assertEqual(sorted(exports), sorted(inventory))
        for key, value in exports.items():
            with self.subTest(export=key):
                blob = json.dumps(value, ensure_ascii=False)
                for credential in (webhook, TEST_WEBHOOK_TOKEN, TEST_SECRET):
                    self.assertNotIn(credential, blob)
        destination = os.environ.get('FEISHU_B2A3_ARTIFACT_DIR')
        if destination:
            dest = Path(destination); dest.mkdir(parents=True, exist_ok=True)
            (dest/'new-redaction-exports.json').write_text(json.dumps(exports, ensure_ascii=False, indent=2))
            (dest/'new-redaction-inventory.json').write_text(json.dumps(inventory, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    unittest.main(verbosity=2)
