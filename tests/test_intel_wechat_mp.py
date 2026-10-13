"""情报采集 · 公众号第一期测试。全部 HTTP 走夹具 + mock，不发真实请求。"""
import io
import json
from email.message import Message
from pathlib import Path
import subprocess
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch

from intel_public import _open as public_open
from intel_store import Intel, Settings, article_fingerprint
from intel_wechat_mp import (BackendQuota, BodyThrottle, COOLDOWN_SECONDS, KeychainCredentials,
                             MAX_BODIES_PER_RUN, MAX_PINNED_REDIRECTS, CredentialExpired,
                             PinnedRedirect, RateLimited, WechatMpClient, _open, article_url,
                             assert_pinned_url, fakeid, parse_article)
from knowledge_store import Knowledge
from server import Store

FIXTURES = Path(__file__).with_name('fixtures')
SAMPLE_BIZ = 'MzB5NTkwMDAwMDE='
OTHER_BIZ = 'MzB5NTkwMDAwMDI='


def load(name):
    return (FIXTURES / name).read_text(encoding='utf-8')


class FakeSettings:
    """内存版 settings；限流状态机测试用它，冷却因此能跨实例观察。"""

    def __init__(self):
        self.values = {}

    def get(self, key, default=None):
        return self.values.get(key, default)

    def set(self, key, value):
        self.values[key] = str(value)


class FakeClock:
    def __init__(self, start=1_000_000.0):
        self.value = start
        self.slept = []

    def now(self):
        return self.value

    def sleep(self, seconds):
        self.slept.append(seconds)
        self.value += seconds


class ParserTests(unittest.TestCase):
    """解析器只吃本地夹具；夹具已脱敏，不含任何真实账号信息。"""

    def setUp(self):
        self.document = load('wechat_mp_article.html')

    def test_parses_every_contract_field(self):
        parsed = parse_article(self.document, 'https://mp.weixin.qq.com/s/SAMPLE0000')
        self.assertEqual(parsed['biz'], SAMPLE_BIZ)
        self.assertEqual(parsed['nickname'], '占位公众号')
        self.assertEqual(parsed['title'], '占位文章标题')
        self.assertEqual(parsed['published_at'], 1700000000)
        self.assertIn('占位段落一', parsed['text'])
        self.assertIn('占位段落二', parsed['text'])
        self.assertIn('占位段落三', parsed['text'])
        self.assertEqual(len(parsed['images']), 2)
        self.assertTrue(all(image.startswith('https://mmbiz') for image in parsed['images']))

    def test_first_non_empty_biz_wins(self):
        self.assertIn('var biz = "";', self.document)
        self.assertEqual(parse_article(self.document)['biz'], SAMPLE_BIZ)

    def test_body_is_balanced_and_excludes_following_script(self):
        parsed = parse_article(self.document)
        self.assertNotIn('tail', parsed['text'])
        self.assertNotIn('<', parsed['text'])

    def test_missing_biz_is_rejected_not_guessed(self):
        with self.assertRaisesRegex(ValueError, '没有公众号编号'):
            parse_article('<html><body><div id="js_content">正文</div></body></html>')
        with self.assertRaisesRegex(ValueError, '内容为空'):
            parse_article('   ')

    def test_url_and_fakeid_validators(self):
        self.assertEqual(article_url(' https://mp.weixin.qq.com/s/AbCdEf12 '),
                         'https://mp.weixin.qq.com/s/AbCdEf12')
        for bad in ('https://example.com/s/AbCdEf12', 'http://mp.weixin.qq.com/s/x',
                    'https://mp.weixin.qq.com/s/short', 'not a url'):
            with self.assertRaisesRegex(ValueError, '文章链接'):
                article_url(bad)
        self.assertEqual(fakeid(' ' + SAMPLE_BIZ + ' '), SAMPLE_BIZ)
        for bad in ('', 'not-a-fakeid', 'X' + SAMPLE_BIZ, None):
            with self.assertRaisesRegex(ValueError, '公众号编号'):
                fakeid(bad)


class RateLimitTests(unittest.TestCase):
    """限流状态机：200013 计数 → 3 次进冷却 → 冷却中不发请求 → 恢复。"""

    def setUp(self):
        self.settings = FakeSettings()
        self.clock = FakeClock()
        self.quota = BackendQuota(self.settings, self.clock.now, self.clock.sleep,
                                  jitter=lambda low, high: 5.0)

    def test_interval_and_jitter_are_enforced(self):
        self.settings.set(BackendQuota.K_LAST, repr(self.clock.value - 10))
        self.quota.before()
        # 至少补足 90 秒最小间隔，再叠加抖动。
        self.assertGreaterEqual(self.clock.slept[0], 90 - 10)
        self.assertEqual(len(self.clock.slept), 1)

    def test_first_request_has_no_wait(self):
        self.quota.before()
        self.assertEqual(self.clock.slept, [])

    def test_three_strikes_open_six_hour_cooldown(self):
        self.assertEqual(self.quota.record(200013), 0)
        self.assertEqual(self.quota.strike_count(), 1)
        self.assertEqual(self.quota.record(200013), 0)
        self.assertEqual(self.quota.strike_count(), 2)
        until = self.quota.record(200013)
        self.assertEqual(until, int(self.clock.value) + COOLDOWN_SECONDS)
        self.assertTrue(self.quota.state()['cooling'])
        self.assertEqual(self.quota.strike_count(), 0)

    def test_cooldown_refuses_to_send_and_reports_deadline(self):
        self.quota.record(200013); self.quota.record(200013); self.quota.record(200013)
        before = len(self.clock.slept)
        with self.assertRaises(RateLimited) as caught:
            self.quota.before()
        self.assertEqual(caught.exception.until, self.quota.cooldown_until())
        self.assertEqual(len(self.clock.slept), before)  # 冷却中绝不 sleep 试探

    def test_cooldown_expires_and_resets(self):
        for _ in range(3):
            self.quota.record(200013)
        self.clock.value += COOLDOWN_SECONDS + 1
        self.assertEqual(self.quota.cooldown_until(), 0)
        self.quota.before()
        self.assertEqual(self.quota.record(0), 0)
        self.assertEqual(self.quota.strike_count(), 0)

    def test_success_resets_strike_counter(self):
        self.quota.record(200013)
        self.assertEqual(self.quota.strike_count(), 1)
        self.quota.record(0)
        self.assertEqual(self.quota.strike_count(), 0)

    def test_cooldown_survives_a_new_process(self):
        for _ in range(3):
            self.quota.record(200013)
        revived = BackendQuota(self.settings, self.clock.now, self.clock.sleep,
                               jitter=lambda low, high: 0.0)
        self.assertTrue(revived.state()['cooling'])

    def test_body_throttle_is_at_least_five_seconds(self):
        clock = FakeClock()
        throttle = BodyThrottle(clock.now, clock.sleep)
        throttle.wait()
        self.assertEqual(clock.slept, [])
        throttle.wait()
        self.assertGreaterEqual(clock.slept[0], 5.0)


class StubCredentials:
    def __init__(self, value=('cookie=stub', '9999999999')):
        self.value = value
        self.calls = 0

    def load(self):
        self.calls += 1
        if isinstance(self.value, Exception):
            raise self.value
        return self.value


class ClientTests(unittest.TestCase):
    """客户端全部走 mock opener，测试期间不产生任何真实网络请求。"""

    def build(self, responses, quota=None, credentials=None):
        clock = FakeClock()
        self.urls = []

        def opener(url, headers, timeout):
            self.urls.append(url)
            key = url.split('?')[0]
            for pattern, response in responses.items():
                if pattern in key:
                    return response(url, headers) if callable(response) else response
            raise AssertionError(f'未预期的请求：{url}')

        quota = quota or BackendQuota(FakeSettings(), clock.now, clock.sleep, jitter=lambda low, high: 0.0)
        client = WechatMpClient(credentials or StubCredentials(), quota, BodyThrottle(clock.now, clock.sleep), opener)
        return client, clock

    def test_search_biz_dedupes_and_never_auto_picks(self):
        client, _ = self.build({'/cgi-bin/searchbiz': load('wechat_mp_searchbiz.json')})
        found = client.search_biz('占位号')
        self.assertEqual([item['fakeid'] for item in found], [SAMPLE_BIZ, OTHER_BIZ])
        self.assertEqual(found[0]['nickname'], '占位号')
        self.assertEqual(found[0]['signature'], '占位')
        # 同名两个不同 fakeid 都保留，交由用户选。
        self.assertNotEqual(found[0]['fakeid'], found[1]['fakeid'])

    def test_appmsg_list_drops_non_wechat_links_and_keeps_chksm(self):
        """**chksm 必须保留**：实测没有 chksm 的长链会返回「未知错误/失效的验证页」。
        一期曾把 query 剥干净，三期按合同更正改回保留。"""
        client, _ = self.build({'/cgi-bin/appmsg': load('wechat_mp_appmsg.json')})
        items = client.appmsg_list(SAMPLE_BIZ, 0, 20)
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]['link'],
                         'https://mp.weixin.qq.com/s/AAAAAAAAAAAA?chksm=abc&scene=27')
        self.assertEqual(items[0]['aid'], '2247483001')
        self.assertEqual(items[1]['title'], '占位文章二 & 符号')

    def test_article_links_with_chksm_are_accepted(self):
        long_link = ('https://mp.weixin.qq.com/s?__biz=MzB5NTkwMDAwMDE%3D%3D&mid=2647685772'
                     '&idx=1&sn=a073c30899a7f420147673def8209609'
                     '&chksm=f007a6dbc7702fcd5a79bfc8f5efe9c78')
        self.assertEqual(article_url(long_link), long_link)
        # 片段（#rd）可以丢，query 一个字都不能少
        self.assertEqual(article_url(long_link + '#rd'), long_link)
        for bad in ('https://example.com/s/abcdef', 'https://mp.weixin.qq.com/mp/waerrpage?a=1'):
            with self.assertRaisesRegex(ValueError, '文章链接'):
                article_url(bad)

    def test_publish_page_string_is_parsed_twice(self):
        payload = json.dumps({'publish_page': json.dumps({'publish_list': [
            {'title': '备用接口文章', 'link': 'https://mp.weixin.qq.com/s/CCCCCCCCCCCC',
             'update_time': 1700000001, 'appmsgid': 2247483009}]}, ensure_ascii=False)})
        payload = '{"base_resp":{"ret":0},' + payload[1:]
        client, _ = self.build({'/cgi-bin/appmsg': payload})
        items = client.appmsg_list(SAMPLE_BIZ, 0, 20)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]['aid'], '2247483009')

    def test_rate_limit_stops_without_retry(self):
        client, _ = self.build({'/cgi-bin/appmsg': load('wechat_mp_ratelimit.json')})
        quota = client.quota
        with self.assertRaises(RateLimited):
            client.appmsg_list(SAMPLE_BIZ, 0, 20)
        self.assertEqual(len(self.urls), 1)  # 只发了一次，没有重试
        # 第一次命中只记一次累计，不立刻冷却；累计到 3 次才进冷却。
        self.assertEqual(quota.strike_count(), 1)
        self.assertEqual(quota.cooldown_until(), 0)

    def test_login_page_is_reported_as_relogin_not_retry(self):
        client, _ = self.build({'/cgi-bin/appmsg': load('wechat_mp_loginpage.html')})
        with self.assertRaisesRegex(CredentialExpired, '需重新登录后台'):
            client.appmsg_list(SAMPLE_BIZ, 0, 20)
        self.assertEqual(len(self.urls), 1)

    def test_nonzero_ret_other_than_rate_limit_is_relogin(self):
        client, _ = self.build({'/cgi-bin/appmsg': '{"base_resp":{"ret":200002}}'})
        with self.assertRaisesRegex(CredentialExpired, 'ret=200002'):
            client.appmsg_list(SAMPLE_BIZ, 0, 20)

    def test_non_json_is_relogin_not_a_crash(self):
        client, _ = self.build({'/cgi-bin/appmsg': '<html>网关错误</html>'})
        with self.assertRaisesRegex(CredentialExpired, '不是 JSON'):
            client.appmsg_list(SAMPLE_BIZ, 0, 20)

    def test_article_fetch_sends_no_cookie_and_desktop_headers(self):
        seen = {}

        def respond(url, headers):
            seen.update(headers)
            return load('wechat_mp_article.html').encode()

        client, _ = self.build({'/s/SAMPLE0000': respond})
        parsed = client.fetch_article('https://mp.weixin.qq.com/s/SAMPLE0000')
        self.assertEqual(parsed['biz'], SAMPLE_BIZ)
        self.assertNotIn('Cookie', seen)
        self.assertIn('Chrome', seen['User-Agent'])
        self.assertEqual(seen['Accept-Language'], 'zh-CN,zh;q=0.9')

    def test_article_failure_is_reported_without_fallback(self):
        def refuse(url, headers, timeout):
            raise OSError('connection reset')
        clock = FakeClock()
        client = WechatMpClient(StubCredentials(),
                                BackendQuota(FakeSettings(), clock.now, clock.sleep, jitter=lambda a, b: 0.0),
                                BodyThrottle(clock.now, clock.sleep), refuse)
        with self.assertRaisesRegex(ValueError, '普通 HTTP 请求没有拿到页面'):
            client.fetch_article('https://mp.weixin.qq.com/s/SAMPLE0000')

    def test_tiny_page_is_refused(self):
        blocked = b'<html><body>' + b'x' * 5000 + b'</body></html>'  # 够长，但没有 js_content
        client, _ = self.build({'/s/SAMPLE0000': blocked})
        with self.assertRaisesRegex(ValueError, '内容异常'):
            client.fetch_article('https://mp.weixin.qq.com/s/SAMPLE0000')


class KeychainTests(unittest.TestCase):
    """凭证只从钥匙串读；缺字段、格式错、命令失败都不泄漏内容。"""

    def runner(self, mapping, ok=True):
        def run(command, **kwargs):
            account = command[-2]
            if account not in mapping:
                return subprocess.CompletedProcess(command, 1, '', 'SecKeychainSearchCopyNext: The specified item could not be found')
            return subprocess.CompletedProcess(command, 0, mapping[account] + '\n', '')
        return run

    def test_reads_cookie_and_token(self):
        keychain = KeychainCredentials(runner=self.runner({'cookie': 'a=1; b=2', 'token': '9999999999'}))
        self.assertEqual(keychain.load(), ('a=1; b=2', '9999999999'))

    def test_missing_entry_asks_for_relogin(self):
        keychain = KeychainCredentials(runner=self.runner({}))
        with self.assertRaisesRegex(Exception, '需重新登录后台'):
            keychain.load()

    def test_empty_value_asks_for_relogin(self):
        keychain = KeychainCredentials(runner=self.runner({'cookie': '', 'token': '9999999999'}))
        with self.assertRaisesRegex(Exception, '凭证为空'):
            keychain.load()

    def test_bad_token_shape_asks_for_relogin(self):
        keychain = KeychainCredentials(runner=self.runner({'cookie': 'a=1', 'token': 'abc;drop'}))
        with self.assertRaisesRegex(Exception, 'token 格式无效'):
            keychain.load()

    def test_command_failure_asks_for_relogin(self):
        def boom(command, **kwargs):
            raise OSError('security not found')
        with self.assertRaisesRegex(Exception, '需重新登录后台'):
            KeychainCredentials(runner=boom).load()

    def test_reads_the_configured_service_only(self):
        seen = {}

        def run(command, **kwargs):
            seen['service'] = command[command.index('-s') + 1]
            seen['account'] = command[command.index('-a') + 1]
            return subprocess.CompletedProcess(command, 0, '9999999999', '')

        KeychainCredentials(runner=run).load()
        self.assertEqual(seen['service'], 'article-studio.wechat-mp')
        self.assertEqual(seen['account'], 'token')


class QuietPublic:
    """三期公开接口的空结果实现：走新流程但不产生任何请求。"""

    def __init__(self):
        self.album_calls = self.sogou_calls = 0

    def album_page(self, *a, **k):
        self.album_calls += 1
        return {'title': '', 'article_count': 0, 'nickname': '', 'items': [],
                'continue_flag': False}

    def sogou_search(self, *a, **k):
        self.sogou_calls += 1
        return []

    def sogou_resolve(self, *a, **k):
        return ''


class NullPublic:
    """三期公开接口的空实现：任何方法被调用都说明测试出错了。"""

    def discover_album_ids(self, document):  # pragma: no cover - 不该走到
        raise AssertionError('测试不该访问公开接口')

    def album_page(self, *args, **kwargs):  # pragma: no cover
        raise AssertionError('测试不该访问合集接口')

    def sogou_search(self, *args, **kwargs):  # pragma: no cover
        raise AssertionError('测试不该访问搜狗')

    def sogou_resolve(self, *args, **kwargs):  # pragma: no cover
        raise AssertionError('测试不该访问搜狗')


class StoreTests(unittest.TestCase):
    """存储层：fakeid/链接双去重、正文待确认、送入回收站带 provenance。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(self.tmp.name)
        self.store._knowledge = Knowledge(self.store, sources=[])
        self.intel = Intel(self.store, client_factory=lambda s: None, public_factory=lambda s: NullPublic())

    def tearDown(self):
        self.tmp.cleanup()

    def item(self, index, aid=True):
        return {'title': f'占位文章{index}', 'link': f'https://mp.weixin.qq.com/s/SAMPLE{index:05d}',
                'published_at': 1700000000 + index, 'aid': f'224748{index:04d}' if aid else ''}

    def test_subscribe_requires_fakeid_and_name(self):
        saved = self.intel.subscribe({'fakeid': SAMPLE_BIZ, 'name': '占位公众号'})
        self.assertEqual(saved['fakeid'], SAMPLE_BIZ)
        with self.assertRaisesRegex(ValueError, '公众号编号'):
            self.intel.subscribe({'fakeid': 'nope', 'name': 'x'})
        with self.assertRaisesRegex(ValueError, '请填写公众号名称'):
            self.intel.subscribe({'fakeid': OTHER_BIZ, 'name': '  '})

    def test_subscribe_is_idempotent_per_fakeid(self):
        self.intel.subscribe({'fakeid': SAMPLE_BIZ, 'name': '占位公众号'})
        self.intel.subscribe({'fakeid': SAMPLE_BIZ, 'name': '占位公众号二'})
        self.assertEqual(len(self.intel.subscriptions()), 1)
        self.assertEqual(self.intel.subscriptions()[0]['name'], '占位公众号二')

    def test_upsert_dedupes_by_link_then_by_fakeid_aid(self):
        identifier = self.intel._upsert_article(SAMPLE_BIZ, self.item(1), 'job1')[0]
        again, created = self.intel._upsert_article(SAMPLE_BIZ, self.item(1), 'job1')
        self.assertEqual(again, identifier)
        self.assertFalse(created)
        # 同一个 aid 换一个链接，仍然认作同一篇。
        duplicate = self.item(1)
        duplicate['link'] = 'https://mp.weixin.qq.com/s/OTHERLINK01'
        same, created = self.intel._upsert_article(SAMPLE_BIZ, duplicate, 'job1')
        self.assertEqual(same, identifier)
        self.assertFalse(created)
        self.assertEqual(self.intel.articles(SAMPLE_BIZ)['total'], 1)

    def test_missing_aid_falls_back_to_link_fingerprint(self):
        self.intel._upsert_article(SAMPLE_BIZ, self.item(7, aid=False), 'job1')
        self.intel._upsert_article(SAMPLE_BIZ, self.item(7, aid=False), 'job1')
        self.assertEqual(self.intel.articles(SAMPLE_BIZ)['total'], 1)
        self.assertEqual(article_fingerprint(self.item(7)['link']), article_fingerprint(self.item(7)['link']))

    def test_body_is_awaiting_confirm_and_not_auto_stored(self):
        self.intel.subscribe({'fakeid': SAMPLE_BIZ, 'name': '占位公众号'})
        identifier = self.intel._upsert_article(SAMPLE_BIZ, self.item(1), 'job1')[0]
        self.intel.save_body(identifier, parse_article(load('wechat_mp_article.html')))
        record = self.intel.article(identifier)
        self.assertEqual(record['body_status'], 'awaiting_confirm')
        self.assertGreater(record['chars'], 0)
        # 默认不进知识库。
        self.assertEqual(self.store.knowledge.search('占位段落一')['total'], 0)

    def test_failed_body_is_recorded_without_half_text(self):
        identifier = self.intel._upsert_article(SAMPLE_BIZ, self.item(2), 'job1')[0]
        record = self.intel.fail_body(identifier, '普通 HTTP 请求没有拿到页面')
        self.assertEqual(record['body_status'], 'failed')
        self.assertEqual(record['body'], '')
        self.assertIn('普通 HTTP', record['error'])

    def test_push_to_intake_writes_contract_provenance(self):
        self.intel.subscribe({'fakeid': SAMPLE_BIZ, 'name': '占位公众号'})
        identifier = self.intel._upsert_article(SAMPLE_BIZ, self.item(1), 'job1')[0]
        self.intel.save_body(identifier, parse_article(load('wechat_mp_article.html')))
        done = self.intel.push_to_intake(identifier, self.store.creative_intake)
        # provenance 已按 collect-core 合同换成 collect:<平台>:<来源>:<编号>
        self.assertEqual(done['provenance'], f'collect:wechat_mp:{SAMPLE_BIZ}:2247480001')
        # 一期写下的旧前缀仍然认得，老数据不会被当成脏数据
        from collect_core import parse_provenance
        self.assertEqual(parse_provenance('intel:wechat_mp:MzB5NTkwMDAwMDE==:2247480001'),
                         ('wechat_mp', 'MzB5NTkwMDAwMDE==', '2247480001', True))
        self.assertEqual(parse_provenance(done['provenance']),
                         ('wechat_mp', SAMPLE_BIZ, '2247480001', False))
        self.assertEqual(self.intel.article(identifier)['drop_id'], done['drop_id'])
        # 回收项里确实躺着原文，且没有自动入知识库。
        dropped = self.store.creative_intake.detail(done['drop_id'])
        self.assertEqual(dropped['title'], '占位文章1')  # 列表里的标题，不是正文页标题
        self.assertGreater(dropped['text_chars'], 0)
        self.assertIn('占位段落一', self.store.creative_intake._raw(done['drop_id'])['text'])
        self.assertEqual(self.store.knowledge.search('占位段落一')['total'], 0)

    def test_push_twice_does_not_duplicate_the_drop(self):
        self.intel.subscribe({'fakeid': SAMPLE_BIZ, 'name': '占位公众号'})
        identifier = self.intel._upsert_article(SAMPLE_BIZ, self.item(1), 'job1')[0]
        self.intel.save_body(identifier, parse_article(load('wechat_mp_article.html')))
        first = self.intel.push_to_intake(identifier, self.store.creative_intake)
        second = self.intel.push_to_intake(identifier, self.store.creative_intake)
        self.assertEqual(first['drop_id'], second['drop_id'])
        self.assertEqual(len(self.store.creative_intake.listing(80)['items']), 1)

    def test_push_requires_body(self):
        identifier = self.intel._upsert_article(SAMPLE_BIZ, self.item(3), 'job1')[0]
        with self.assertRaisesRegex(ValueError, '还没有正文'):
            self.intel.push_to_intake(identifier, self.store.creative_intake)

    def test_remove_subscription_keeps_articles(self):
        self.intel.subscribe({'fakeid': SAMPLE_BIZ, 'name': '占位公众号'})
        self.intel._upsert_article(SAMPLE_BIZ, self.item(1), 'job1')
        self.intel.remove_subscription(SAMPLE_BIZ)
        self.assertEqual(self.intel.subscriptions(), [])
        self.assertEqual(self.intel.articles(SAMPLE_BIZ)['total'], 1)

    def test_listing_never_returns_full_body(self):
        identifier = self.intel._upsert_article(SAMPLE_BIZ, self.item(1), 'job1')[0]
        self.intel.save_body(identifier, parse_article(load('wechat_mp_article.html')))
        row = self.intel.articles(SAMPLE_BIZ)['articles'][0]
        self.assertNotIn('body', row)
        self.assertGreater(row['chars'], 0)

    def test_reject_unknown_fields(self):
        with self.assertRaisesRegex(ValueError, '无效字段'):
            self.intel.subscribe({'fakeid': SAMPLE_BIZ, 'name': 'x', 'evil': 1})


class RunTests(unittest.TestCase):
    """抓取任务：异步 + 回执；冷却中拒发，不排队不重试。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(self.tmp.name)
        self.store._knowledge = Knowledge(self.store, sources=[])
        self.settings = Settings(None)
        self.intel = None

    def tearDown(self):
        self.tmp.cleanup()

    def build(self, listings, articles=0, cooldown=0, credentials_missing=False):
        clock = FakeClock()
        values = {}
        if cooldown:
            values[BackendQuota.K_COOLDOWN] = repr(clock.now() + cooldown)
            values[BackendQuota.K_STRIKE] = '0'
        self.settings = FakeSettings()
        self.settings.values = values
        quota = BackendQuota(self.settings, clock.now, clock.sleep, jitter=lambda low, high: 0.0)
        self.clock = clock
        state = {'lists': list(listings), 'bodies': 0}

        class Client:
            quota = None

            def credentials_ready(self):
                return ('cookie=stub', '9999999999')

            def search_biz(self, query):
                return []

            def appmsg_list(self, identifier, begin, count):
                if state['lists']:
                    return state['lists'].pop(0)
                return []

            def fetch_article(self, url):
                state['bodies'] += 1
                return parse_article(load('wechat_mp_article.html'), url)

        Client.quota = quota
        self.state = state
        self.public = QuietPublic()
        self.intel = Intel(self.store, client_factory=lambda s: Client(), public_factory=lambda s: self.public)
        return self.intel

    def page(self, count, start=0):
        return [self.item(start + i) for i in range(count)]

    def item(self, index):
        return {'title': f'占位文章{index}', 'link': f'https://mp.weixin.qq.com/s/RUN{index:05d}',
                'published_at': 1700000000 + index, 'aid': f'22475{index:05d}'}

    def test_latest_run_receives_one_page_and_three_bodies(self):
        intel = self.build([self.page(20)], articles=3)
        intel.subscribe({'fakeid': SAMPLE_BIZ, 'name': '占位公众号'})
        job = intel.run({'fakeid': SAMPLE_BIZ, 'mode': 'latest', 'sogou': False, 'backend': True})
        done = intel.wait_for(job['id'], timeout=5)
        self.assertEqual(done['status'], 'done')
        self.assertEqual(done['found'], 20)
        self.assertEqual(done['added'], 20)
        self.assertEqual(done['bodies'], MAX_BODIES_PER_RUN)
        self.assertEqual(self.state['bodies'], MAX_BODIES_PER_RUN)

    def test_history_run_caps_at_thirty_articles(self):
        """历史仍 ≤30 篇：后台那两页取 30 篇，第三页不再要。"""
        intel = self.build([self.page(15, 0), self.page(15, 15), self.page(15, 30)])
        intel.subscribe({'fakeid': SAMPLE_BIZ, 'name': '占位公众号'})
        done = intel.wait_for(intel.run({'fakeid': SAMPLE_BIZ, 'mode': 'history', 'sogou': False, 'backend': True})['id'], timeout=5)
        self.assertEqual(done['status'], 'done')
        self.assertEqual(done['found'], 30)
        self.assertEqual(done['added'], 30)
        self.assertEqual(len(self.state['lists']), 1, '第三页不该被取')

    def test_duplicate_links_across_rounds_are_merged_once(self):
        """同一批链接再抓一次：只入库一次，第二轮 added=0。"""
        intel = self.build([self.page(3, 0)])
        # 连续两轮都拿到同一页（模拟合集与后台同时返回同一篇）
        self.state['lists'] = [self.page(3, 0), self.page(3, 0)]
        intel.subscribe({'fakeid': SAMPLE_BIZ, 'name': '占位公众号'})
        done = intel.wait_for(intel.run({'fakeid': SAMPLE_BIZ, 'mode': 'latest', 'sogou': False, 'backend': True})['id'], timeout=5)
        self.assertEqual(done['found'], 3, '两轮同页应合并成 3 篇')
        self.assertEqual(done['added'], 3)
        second = intel.wait_for(intel.run({'fakeid': SAMPLE_BIZ, 'mode': 'latest', 'sogou': False, 'backend': True})['id'], timeout=5)
        self.assertEqual(second['added'], 0, '重复抓取不应新增')
        self.assertEqual(intel.articles(SAMPLE_BIZ)['total'], 3)

    def test_backend_cooldown_skips_only_the_backend_path(self):
        """后台冷却时：后台一个请求都不发，但这一轮照常跑完并如实写明原因。"""
        intel = self.build([self.page(5)], cooldown=3600)
        intel.subscribe({'fakeid': SAMPLE_BIZ, 'name': '占位公众号'})
        done = intel.wait_for(intel.run({'fakeid': SAMPLE_BIZ, 'mode': 'latest', 'backend': True})['id'], timeout=5)
        self.assertEqual(self.state['lists'], [self.page(5)])  # 后台一个请求都没发
        self.assertIn('冷却', done['detail'])
        self.assertIn('后台', done['detail'])

    def test_public_paths_are_not_blocked_by_the_backend_cooldown(self):
        """三期起后台只是可选第三路：它在冷却时**不该**连累合集与搜狗。"""
        intel = self.build([self.page(5)], cooldown=3600)
        intel.subscribe({'fakeid': SAMPLE_BIZ, 'name': '占位公众号'})
        done = intel.wait_for(intel.run({'fakeid': SAMPLE_BIZ, 'mode': 'latest'})['id'], timeout=5)
        self.assertEqual(self.state['lists'], [self.page(5)])  # 后台默认关闭，一个请求都没发
        self.assertIn('已关闭', done['detail'])
        self.assertEqual(self.state['bodies'], 0)

    def test_rate_limit_midway_stops_and_reports_cooldown(self):
        clock = FakeClock()

        class Limited:
            quota = None
            calls = 0
            clock = None

            def credentials_ready(self):
                return ('cookie=stub', '9999999999')

            def search_biz(self, query):
                return []

            def appmsg_list(self, identifier, begin, count):
                Limited.calls += 1
                raise RateLimited('命中公众号后台限流（200013），已停止本次抓取', until=int(Limited.clock.now()) + 60)

            def fetch_article(self, url):
                raise AssertionError('限流后不应继续抓正文')

        Limited.clock = clock
        Limited.quota = BackendQuota(FakeSettings(), clock.now, clock.sleep, jitter=lambda a, b: 0.0)
        intel = Intel(self.store, client_factory=lambda s: Limited(), public_factory=lambda s: NullPublic())
        intel.subscribe({'fakeid': SAMPLE_BIZ, 'name': '占位公众号'})
        done = intel.wait_for(intel.run({'fakeid': SAMPLE_BIZ, 'mode': 'latest', 'sogou': False, 'backend': True})['id'], timeout=5)
        self.assertEqual(Limited.calls, 1)
        # 命中后不重试，且回执如实写明，不伪装成成功
        self.assertIn('200013', done['detail'])
        self.assertIn('未重试', done['detail'])

    def test_missing_credentials_are_reported_not_retried(self):
        intel = self.build([self.page(5)])
        intel.subscribe({'fakeid': SAMPLE_BIZ, 'name': '占位公众号'})

        def boom(identifier, begin, count):
            from intel_wechat_mp import CredentialsMissing
            raise CredentialsMissing('需重新登录后台：钥匙串里没有公众号后台凭证')

        intel.client().appmsg_list = boom
        done = intel.wait_for(intel.run({'fakeid': SAMPLE_BIZ, 'mode': 'latest', 'sogou': False, 'backend': True})['id'], timeout=5)
        self.assertIn('需重新登录后台', done['detail'])

    def test_run_rejects_bad_mode_and_body_budget(self):
        intel = self.build([self.page(5)])
        intel.subscribe({'fakeid': SAMPLE_BIZ, 'name': '占位公众号'})
        with self.assertRaisesRegex(ValueError, '抓最新或抓历史'):
            intel.run({'fakeid': SAMPLE_BIZ, 'mode': 'everything'})
        with self.assertRaisesRegex(ValueError, '每次最多抓'):
            intel.run({'fakeid': SAMPLE_BIZ, 'mode': 'latest', 'bodies': MAX_BODIES_PER_RUN + 1})
        with self.assertRaisesRegex(ValueError, '先录入'):
            intel.run({'fakeid': OTHER_BIZ, 'mode': 'latest'})

    def test_body_failure_marks_article_and_keeps_going(self):
        intel = self.build([self.page(2)])
        intel.subscribe({'fakeid': SAMPLE_BIZ, 'name': '占位公众号'})
        calls = {'n': 0}

        def flaky(url):
            calls['n'] += 1
            if calls['n'] == 1:
                raise ValueError('文章页内容异常，未取到正文')
            return parse_article(load('wechat_mp_article.html'), url)

        intel.client().fetch_article = flaky
        done = intel.wait_for(intel.run({'fakeid': SAMPLE_BIZ, 'mode': 'latest', 'sogou': False, 'backend': True})['id'], timeout=5)
        self.assertEqual(done['status'], 'partial')   # 正文 2 篇尝试 1 成 1 败 → partial（prep §3 行7）
        self.assertEqual(done['bodies'], 1)
        states = sorted(a['body_status'] for a in intel.articles(SAMPLE_BIZ)['articles'])
        self.assertEqual(states, ['awaiting_confirm', 'failed'])

    def test_status_reports_counts_and_cooldown_deadline(self):
        intel = self.build([self.page(3)], cooldown=7200)
        intel.subscribe({'fakeid': SAMPLE_BIZ, 'name': '占位公众号'})
        intel._upsert_article(SAMPLE_BIZ, self.item(1), 'job')
        record = intel.status()
        self.assertEqual(record['schema'], 'intel-wechat-mp/v1')
        self.assertEqual(record['subscriptions'], 1)
        self.assertEqual(record['articles'], 1)
        self.assertTrue(record['cooldown_until'] > 0)
        self.assertTrue(record['cooldown_label'])

    def test_status_does_not_deadlock_on_its_own_settings_table(self):
        # 限流状态与计数同库；若 status() 在计数事务里再开写事务就会自锁。
        intel = self.build([self.page(2)])
        intel.subscribe({'fakeid': SAMPLE_BIZ, 'name': '占位公众号'})
        intel._upsert_article(SAMPLE_BIZ, self.item(1), 'job')
        for _ in range(5):
            self.assertEqual(intel.status()['articles'], 1)

    def test_repeated_status_under_concurrency_stays_writable(self):
        intel = self.build([self.page(2)])
        intel.subscribe({'fakeid': SAMPLE_BIZ, 'name': '占位公众号'})
        errors = []

        def hammer():
            try:
                for _ in range(6):
                    intel.status()
            except Exception as exc:  # noqa: BLE001 - 记录即可，断言在下面
                errors.append(exc)

        threads = [threading.Thread(target=hammer) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(20)
        self.assertEqual(errors, [])
        self.assertEqual(len(intel.subscriptions()), 1)

    def test_status_reports_missing_credentials_without_raising(self):
        intel = Intel(self.store)

        class Broken:
            quota = BackendQuota(FakeSettings())

            def credentials_ready(self):
                from intel_wechat_mp import CredentialsMissing
                raise CredentialsMissing('需重新登录后台：钥匙串里没有公众号后台凭证')

        intel._client = Broken()
        record = intel.status()
        self.assertEqual(record['credentials'], 'missing')
        self.assertIn('需重新登录后台', record['detail'])


class RedirectPinTests(unittest.TestCase):
    """跳转白名单只吃合成 Location，不打开套接字。"""

    def test_initial_url_rejects_ftp_off_host_and_http_article(self):
        for bad in ('ftp://mp.weixin.qq.com/s/ABCDEF',
                    'ftp://weixin.sogou.com/link?url=1',
                    'https://evil.example/s/ABCDEF',
                    'http://mp.weixin.qq.com/s/ABCDEFGH',
                    'https://user:pw@mp.weixin.qq.com/s/ABCDEF',
                    'https://mp.weixin.qq.com:443/s/ABCDEF',
                    'file:///tmp/x'):
            with self.assertRaises(urllib.error.URLError):
                assert_pinned_url(bad)
        assert_pinned_url('https://mp.weixin.qq.com/s/ABCDEF')
        assert_pinned_url('https://weixin.sogou.com/link?url=1')
        # 搜狗主机按现状仍允许 http；文章主机不允许。
        assert_pinned_url('http://weixin.sogou.com/link?url=1')

    def test_open_refuses_before_building_an_opener(self):
        with patch('intel_wechat_mp.urllib.request.build_opener', side_effect=AssertionError('不该建连接')):
            for opener in (_open, public_open):
                for bad in ('ftp://mp.weixin.qq.com/s/ABCDEF', 'https://evil.example/a'):
                    with self.assertRaises(urllib.error.URLError):
                        opener(bad, {}, 1)

    def test_redirect_request_rejects_ftp_and_off_host(self):
        handler = PinnedRedirect()
        req = urllib.request.Request('https://mp.weixin.qq.com/mp/appmsgalbum?action=getalbum')
        fp = io.BytesIO(b'')
        for bad in ('ftp://mp.weixin.qq.com/s/ABCDEF',
                    'https://evil.example/path',
                    'http://mp.weixin.qq.com/s/ABCDEFGH',
                    'https://mp.weixin.qq.com.evil.example/s/ABCDEF'):
            with self.assertRaises(urllib.error.HTTPError) as caught:
                handler.redirect_request(req, fp, 302, 'Found', {}, bad)
            caught.exception.close()
            self.assertNotIn(bad, str(caught.exception))

    def test_redirect_allows_https_mp_and_sogou_http(self):
        handler = PinnedRedirect()
        req = urllib.request.Request('https://weixin.sogou.com/link?url=1')
        article = handler.redirect_request(
            req, io.BytesIO(b''), 302, 'Found', {},
            'https://mp.weixin.qq.com/s?__biz=abc&mid=1')
        self.assertEqual(article.full_url, 'https://mp.weixin.qq.com/s?__biz=abc&mid=1')
        sogou = handler.redirect_request(
            req, io.BytesIO(b''), 302, 'Found', {}, 'http://weixin.sogou.com/link?url=2')
        self.assertTrue(sogou.full_url.startswith('http://weixin.sogou.com/'))

    def test_cross_host_redirect_drops_cookie(self):
        handler = PinnedRedirect()
        req = urllib.request.Request('https://mp.weixin.qq.com/cgi-bin/appmsg',
                                     headers={'Cookie': 'token=secret-cookie'})
        crossed = handler.redirect_request(
            req, io.BytesIO(b''), 302, 'Found', {}, 'https://weixin.sogou.com/link?url=1')
        self.assertFalse(any(key.lower() == 'cookie' for key in crossed.headers))
        stayed = handler.redirect_request(
            req, io.BytesIO(b''), 302, 'Found', {}, 'https://mp.weixin.qq.com/s/ABCDEFGH')
        self.assertTrue(any(key.lower() == 'cookie' for key in stayed.headers))

    def test_redirect_count_is_capped_without_reading_the_body(self):
        class Boom(io.BytesIO):
            def read(self, *args, **kwargs):
                raise AssertionError('超限时不该再读响应')

        handler = PinnedRedirect()
        self.assertEqual(handler.max_redirections, MAX_PINNED_REDIRECTS)
        self.assertLessEqual(MAX_PINNED_REDIRECTS, 4)
        req = urllib.request.Request('https://mp.weixin.qq.com/s/ABCDEFGH')
        req.redirect_dict = {f'https://mp.weixin.qq.com/s/HOP{i}AAAAAA': 1
                             for i in range(MAX_PINNED_REDIRECTS)}
        headers = Message()
        headers['Location'] = 'https://mp.weixin.qq.com/s/NEWHOPAAAA'
        with self.assertRaises(urllib.error.HTTPError) as caught:
            handler.http_error_302(req, Boom(), 302, 'Found', headers)
        caught.exception.close()


if __name__ == '__main__':
    unittest.main()