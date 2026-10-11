"""通用采集模块。夹具在临时目录里，网络套接字封死，不读钥匙串，不打真实平台。"""
import json
import socket
import tempfile
import threading
import unittest
from contextlib import contextmanager
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib import error, request

from collect_adapters import ClueSourceAdapter, build_adapters, clue_totals
from collect_core import (Adapter, PlannedAdapter, PlatformQuota, QuotaRegistry, Receipt,
                          Registry, TaskQueue, fingerprint, parse_provenance, provenance)
from gmail_source import GmailCache
from intel_wechat_mp import CredentialsMissing
from knowledge_store import identifier
from server import Handler, Store, seed_intel_schedule


FAKEID = 'MzB5NTkwMDAwMDE=='
AID = '2247480001'


class FakeSettings:
    def __init__(self):
        self.values = {}

    def get(self, key, default=None):
        return self.values.get(key, default)

    def set(self, key, value):
        self.values[key] = str(value)


@contextmanager
def seal_network(allowed_ports=()):
    """测试期间禁止对外连接。只放行本机测试服务自己的端口。"""
    real = socket.socket
    blocked = []

    class Guard(real):
        def connect(self, address):
            host, port = (address[0], address[1]) if isinstance(address, tuple) and len(address) > 1 else (address, None)
            if host in ('127.0.0.1', '::1') and port in allowed_ports:
                return super().connect(address)
            blocked.append(address)
            raise OSError(f'测试封死了网络：{address}')

        def connect_ex(self, address):
            self.connect(address)
            return 0

    socket.socket = Guard
    try:
        yield blocked
    finally:
        socket.socket = real


class ProvenanceTests(unittest.TestCase):
    def test_legacy_fakeid_keeps_padding_equals(self):
        parsed = parse_provenance(f'intel:wechat_mp:{FAKEID}:{AID}')
        self.assertEqual(parsed, ('wechat_mp', FAKEID, AID, True))
        self.assertIn('==', parsed[1])

    def test_new_mark_splits_platform_source_and_id(self):
        mark = provenance('wechat_mp', FAKEID, AID)
        self.assertEqual(mark, f'collect:wechat_mp:{FAKEID}:{AID}')
        self.assertEqual(parse_provenance(mark), ('wechat_mp', FAKEID, AID, False))

    def test_blank_and_foreign_text_are_not_guessed(self):
        self.assertIsNone(parse_provenance(None))
        self.assertIsNone(parse_provenance('intel:wechat_mp:'))
        self.assertIsNone(parse_provenance('不是来源标记'))

    def test_colon_is_rejected_so_the_mark_stays_four_fields(self):
        with self.assertRaisesRegex(ValueError, '冒号'):
            provenance('wechat_mp', 'a:b', AID)

    def test_fingerprint_is_stable_and_distinguishes_titles(self):
        self.assertEqual(fingerprint('公众号', FAKEID, '标题'), fingerprint('公众号', FAKEID, '标题'))
        self.assertNotEqual(fingerprint('公众号', FAKEID, '标题'), fingerprint('公众号', FAKEID, '另一篇'))


class QuotaAndQueueTests(unittest.TestCase):
    def test_platforms_do_not_share_a_cooldown(self):
        settings = FakeSettings()
        mail = PlatformQuota('mail', settings, cooldown_seconds=60)
        github = PlatformQuota('github', settings, cooldown_seconds=60)
        mail.record(True)
        mail.record(True)
        until = mail.record(True)
        self.assertGreater(until, 0)
        self.assertEqual(github.cooldown_until(), 0)
        self.assertEqual(github.strikes(), 0)

    def test_reading_state_does_not_clear_strikes(self):
        quota = PlatformQuota('wechat_mp', FakeSettings())
        quota.record(True)
        quota.state()
        quota.state()
        self.assertEqual(quota.strikes(), 1)

    def test_same_source_reuses_the_running_task(self):
        queue = TaskQueue()
        created = []

        def create():
            created.append(1)
            return f'job-{len(created)}'

        first, reused = queue.submit('github', 'demo/repo', create)
        second, again = queue.submit('github', 'demo/repo', create)
        other, _ = queue.submit('github', 'other/repo', create)
        self.assertEqual(first, second)
        self.assertTrue(again)
        self.assertFalse(reused)
        self.assertNotEqual(other, first)
        self.assertEqual(len(created), 2)
        queue.finish('github', 'demo/repo', first)
        third, reused_after = queue.submit('github', 'demo/repo', create)
        self.assertFalse(reused_after)
        self.assertNotEqual(third, first)

    def test_receipt_names_platform_source_and_path(self):
        receipt = Receipt('wechat_mp', FAKEID, '合集')
        receipt.added = 2
        receipt.note('album', 2)
        receipt.say('搜狗只拿到标题')
        data = receipt.as_dict()
        self.assertEqual(data['platform'], 'wechat_mp')
        self.assertEqual(data['source'], FAKEID)
        self.assertEqual(data['paths']['album'], 2)
        self.assertIn('合集', data['detail'])
        self.assertIn('搜狗只拿到标题', data['detail'])


class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(self.tmp.name)
        self.seal = seal_network()
        self.blocked = self.seal.__enter__()
        self.keys = patch('intel_wechat_mp.KeychainCredentials.load',
                          side_effect=CredentialsMissing('测试不读钥匙串'))
        self.keys.start()
        self.registry = self.store.collect

    def tearDown(self):
        schedules = getattr(self.store, 'schedules', None)
        if schedules is not None:
            schedules.close()
        self.keys.stop()
        self.seal.__exit__(None, None, None)
        self.tmp.cleanup()

    def test_listing_splits_connected_and_planned(self):
        listing = self.registry.listing()
        names = [section['name'] for section in listing['sections']]
        self.assertEqual(names, ['已接通', '规划中'])
        connected = [item['label'] for item in listing['sections'][0]['platforms']]
        planned = [item['label'] for item in listing['sections'][1]['platforms']]
        self.assertEqual(connected, ['公众号', '微信消息', '邮件', 'GitHub'])
        self.assertEqual(planned, ['视频号', '小红书', '抖音', '推特', '其他'])
        homes = {item['id']: item['home'] for item in listing['sections'][0]['platforms']}
        self.assertEqual(homes['github'], '/github.html')
        self.assertEqual(homes['wechat_msg'], '/clues.html')
        self.assertEqual(listing['sections'][1]['platforms'][0]['home'], '')

    def test_planned_adapter_refuses_to_fetch(self):
        planned = self.registry.adapter('xiaohongshu')
        self.assertIsInstance(planned, PlannedAdapter)
        self.assertEqual(planned.sources(), [])
        self.assertFalse(planned.status()['available'])
        with self.assertRaisesRegex(ValueError, '尚未接通'):
            planned.latest('anything')

    def test_clue_actions_delegate_and_do_not_fetch(self):
        calls = []
        self.store.github.request = lambda path: calls.append(path)
        github = self.registry.adapter('github')
        for action, result in (('抓最新', github.latest('demo/repo')),
                               ('抓历史', github.history('demo/repo')),
                               ('抓正文', github.body('demo/repo', {'id': '1'}))):
            self.assertTrue(result['delegated'])
            self.assertEqual(result['action'], action)
            self.assertEqual(result['home'], '/github.html')
        self.assertEqual(calls, [])
        before = len(getattr(self.store, 'schedules', None).listing()['tasks']) if getattr(self.store, 'schedules', None) else 0
        github.latest('demo/repo')
        after = len(self.store.schedules.listing()['tasks']) if getattr(self.store, 'schedules', None) else 0
        self.assertEqual(before, after)

    def test_wechat_status_is_the_clue_center_status(self):
        raw = {'processed_versions': 4, 'pending_versions': 1, 'running': False,
               'has_pending': True, 'coverage': {'cached': 9}, 'checkpoint': 'c1'}
        live = {'busy': False, 'last_capture': {'finished_at': '2026-10-09T00:00:00Z'}}
        with patch('wechat_source_client.WechatSourceClient.clue_status', return_value=raw), \
                patch('wechat_source_client.WechatSourceClient.status', return_value=live):
            got = self.registry.adapter('wechat_msg').status()
            extra = got['extra']
            self.assertEqual(extra['processed_versions'], 4)
            self.assertEqual(extra['pending_versions'], 1)
            self.assertTrue(extra['has_pending'])
            self.assertFalse(extra['busy'])
            self.assertNotIn('clue_status', extra)
            self.assertNotIn('live', extra)
            self.assertNotIn('capture', extra)
            self.assertIn('待分析 1', got['detail'])
            self.assertEqual(got['count'], 4)

    def test_github_status_matches_the_source_itself(self):
        config = {'id': 'gh-demo', 'name': 'GitHub · demo/repo', 'kind': 'github',
                  'repository': 'demo/repo', 'enabled': True}
        with self.store.knowledge.db() as connection:
            connection.execute(
                "INSERT INTO sources(id,config,status,detail) VALUES(?,?,?,?)",
                ('gh-demo', json.dumps(config), 'ready', '已同步'))
        self.store.github.set_settings({'owner': 'demo', 'auth': 'public_api'})
        raw = self.store.github.status()
        got = self.registry.adapter('github').status()
        self.assertEqual(got['extra']['owner'], raw['connection'].get('owner'))
        self.assertEqual(got['extra']['auth'], raw['connection'].get('auth'))
        self.assertEqual(got['extra']['busy'], bool(raw['busy']))
        self.assertEqual(got['extra']['retry_documents'], raw['retry_documents'])
        self.assertNotIn('connection', got['extra'])
        self.assertNotIn('scope', got['extra'])
        self.assertEqual(got['extra']['sources'][0]['repository'], 'demo/repo')
        self.assertEqual(got['count'], 1)
        self.assertIn('demo', got['detail'])

    def test_mail_status_matches_gmail_cache_and_knowledge_rows(self):
        account = 'demo.user@gmail.com'
        payload = self._gmail(account)
        self.store.knowledge.ingest_gmail(payload)
        cache = GmailCache(self.store.workspace).status(account)
        got = self.registry.adapter('mail').status()
        kept = {key: cache[key] for key in ('account', 'count', 'query', 'has_more', 'captured_at')}
        self.assertEqual(got['extra']['gmail'], [kept])
        self.assertNotIn('scope', json.dumps(got['extra']['gmail']))
        kinds = {item['kind'] for item in got['extra']['sources']}
        self.assertIn('gmail_cache', kinds)
        self.assertIn('mail', kinds)
        self.assertEqual(got['extra']['syncing'], self.store.knowledge.sync_lock.locked())
        listed = {item['id']: item['total'] for item in got['extra']['clues']}
        for source_id, total in listed.items():
            self.assertEqual(total, self.store.clues.listing(source=source_id, limit=1)['total'])

    def test_clue_total_matches_the_clue_listing(self):
        with self.store.knowledge.db() as connection:
            connection.execute('CREATE TEMP TABLE seen(id TEXT PRIMARY KEY)')
            self.store.knowledge._write(connection, 'wechat', 'msg-1', 'wechat', '合成项目',
                                        '甲项目预计延期', 'http://example.test/message',
                                        '2026-10-05T02:00:00+00:00', [], {'timestamp': 1760000000})
        doc_id = identifier('wechat', 'msg-1')
        document = self.store.knowledge.document(doc_id)
        self.store.clues.save({'title': '合成项目进展', 'summary': '延期候选', 'relevance': '正在关注',
                               'picks': [{'id': doc_id, 'hash': document['hash'], 'start': 0,
                                          'end': len(document['body'])}]})
        totals = {item['id']: item['total'] for item in clue_totals(self.store, ['wechat', 'mail'])}
        self.assertEqual(totals['wechat'], self.store.clues.listing(source='wechat', limit=1)['total'])
        self.assertEqual(totals['wechat'], 1)
        self.assertEqual(totals['mail'], 0)

    def test_status_extra_keeps_a_field_whitelist(self):
        secret = 'planted-session-token-should-not-leak'
        raw = {'processed_versions': 2, 'pending_versions': 0, 'running': False,
               'has_pending': False, 'session': secret, 'cookie': secret}
        live = {'busy': False, 'token': secret, 'cookie': secret}
        with patch('wechat_source_client.WechatSourceClient.clue_status', return_value=raw), \
                patch('wechat_source_client.WechatSourceClient.status', return_value=live):
            extra = self.registry.adapter('wechat_msg').status()['extra']
        blob = json.dumps(extra, ensure_ascii=False)
        self.assertNotIn(secret, blob)
        self.assertEqual(set(extra), {'busy', 'processed_versions', 'pending_versions', 'has_pending',
                                      'sources', 'clues', 'tasks', 'home'})
        self.store.github.set_settings({'owner': 'demo', 'auth': 'public_api', 'token': secret})
        config = {'id': 'gh-secret', 'name': 'GitHub · demo/repo', 'kind': 'github',
                  'repository': 'demo/repo', 'enabled': True, 'token': secret}
        with self.store.knowledge.db() as connection:
            connection.execute(
                "INSERT INTO sources(id,config,status,detail) VALUES(?,?,?,?)",
                ('gh-secret', json.dumps(config), 'ready', '已同步'))
        github = self.registry.adapter('github').status()['extra']
        self.assertNotIn(secret, json.dumps(github, ensure_ascii=False))
        self.assertNotIn('connection', github)
        self.assertNotIn('scope', github)
        self.assertEqual(github['owner'], 'demo')
        self.assertEqual(github['auth'], 'public_api')
        self.assertTrue(github['sources'])
        for item in github['sources']:
            self.assertFalse(set(item) - {'id', 'name', 'kind', 'status', 'count', 'repository', 'account'})
            self.assertNotIn('token', item)

    def test_schedule_row_matches_the_schedule_list(self):
        from scheduled_tasks import ScheduledTasks
        schedules = ScheduledTasks(self.tmp.name, None, autostart=False)
        self.store.schedules = schedules
        seed_intel_schedule(schedules, self.tmp.name)
        listed = [task for task in schedules.listing()['tasks'] if task['kind'] == 'intel_wechat_mp']
        got = self.registry.adapter('wechat_mp').status()['extra']['tasks']

        def brief(tasks):
            return [(task['id'], task['kind'], task['enabled'], task['running']) for task in tasks]

        self.assertEqual(brief(got), brief(listed))
        self.assertTrue(listed)
        self.assertFalse(listed[0]['enabled'])

    def test_one_platform_failing_does_not_blank_the_page(self):
        class Boom(Adapter):
            platform = 'mail'
            label = '邮件'

            def status(self):
                raise RuntimeError('boom')

        self.registry.register(Boom())
        rows = {row['id']: row for row in self.registry.status()['platforms']}
        self.assertEqual(len(rows), 9)
        self.assertIn('RuntimeError', rows['mail']['detail'])
        self.assertFalse(rows['mail']['available'])
        self.assertIn(rows['github']['detail'], ('尚未连接 GitHub',) )
        self.assertEqual(rows['xiaohongshu']['state'], 'planned')
        self.assertFalse(rows['xiaohongshu']['available'])

    def test_no_external_host_was_contacted(self):
        self.registry.status()
        hosts = set()
        for address in self.blocked:
            if isinstance(address, tuple) and address:
                hosts.add(address[0])
        self.assertFalse(hosts & {'api.github.com', 'mp.weixin.qq.com', 'www.sogou.com', 'imap.gmail.com'})

    def _gmail(self, account):
        body = {'size': 6, 'content': '演示正文'}
        part = {'mime_type': 'text/plain', 'headers': [
            {'name': 'Subject', 'value': '演示'},
            {'name': 'From', 'value': 'Demo <sender@example.test>'},
            {'name': 'To', 'value': account},
            {'name': 'Message-ID', 'value': '<demo-aa11@example.test>'},
            {'name': 'Date', 'value': 'Sat, 3 Oct 2026 16:00:00 +0800'},
        ], 'body': body}
        message = {'id': 'aa11', 'thread_id': 'bb22', 'label_ids': ['INBOX'], 'snippet': '预览',
                   'history_id': '1234', 'internal_date': '1791014400000', 'payload': part,
                   'size_estimate': 500}
        return {'account': account, 'query': 'after:2026/10/01', 'messages': [message],
                'has_more': False, 'captured_at': '2026-10-04T12:00:00+08:00'}


class RouteTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(self.tmp.name)
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.server.store = self.store
        self.server.schedules = None
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.keys = patch('intel_wechat_mp.KeychainCredentials.load',
                          side_effect=CredentialsMissing('测试不读钥匙串'))
        self.keys.start()
        self.seal = seal_network(allowed_ports=(self.port,))
        self.blocked = self.seal.__enter__()

    def tearDown(self):
        self.seal.__exit__(None, None, None)
        self.keys.stop()
        self.server.shutdown()
        self.server.server_close()
        self.tmp.cleanup()

    def get(self, path):
        with request.urlopen(f'http://127.0.0.1:{self.port}{path}', timeout=30) as response:
            return response.status, json.loads(response.read().decode())

    def test_collect_routes_list_platforms_without_leaving_the_machine(self):
        status, listing = self.get('/api/collect/platforms')
        self.assertEqual(status, 200)
        self.assertEqual([section['name'] for section in listing['sections']], ['已接通', '规划中'])
        status, body = self.get('/api/collect/status')
        self.assertEqual(status, 200)
        self.assertEqual(len(body['platforms']), 9)
        ids = [row['id'] for row in body['platforms']]
        self.assertEqual(ids[0], 'wechat_mp')
        self.assertIn('xiaohongshu', ids)
        hosts = {address[0] for address in self.blocked if isinstance(address, tuple) and address}
        self.assertFalse(hosts & {'api.github.com', 'mp.weixin.qq.com', 'www.sogou.com'})

    def test_existing_intel_route_still_answers(self):
        try:
            status, body = self.get('/api/intel/subscriptions')
        except error.HTTPError as exc:
            self.fail(exc.read().decode())
        self.assertEqual(status, 200)
        self.assertEqual(body['subscriptions'], [])


class BuildTests(unittest.TestCase):
    def test_build_registers_every_platform(self):
        class Intel:
            store = None

            def subscriptions(self):
                return []

            def status(self):
                return {'credentials': 'missing', 'subscriptions': 0, 'detail': '无', 'articles': 0,
                        'awaiting': 0, 'cooldown_until': 0, 'rate_strikes': 0, 'busy': False}

        class Stub:
            intel = Intel()
            knowledge = None
            github = None
            wechat_source = None
            workspace = Path('.')

        adapters = build_adapters(Stub())
        self.assertIsInstance(adapters['wechat_mp'], object)
        self.assertIsInstance(adapters['mail'], ClueSourceAdapter)
        self.assertIsInstance(adapters['channels'], PlannedAdapter)
        self.assertEqual(provenance('mail', 'demo.user@gmail.com', 'aa11'),
                         'collect:mail:demo.user@gmail.com:aa11')


if __name__ == '__main__':
    unittest.main()
