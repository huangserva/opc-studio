"""intel_link_discovery 的合成单测：零网络、零真实 remio/微信/凭证。

运行：
    TMPDIR=/tmp PYTHONPATH=.:tests ../article-importer/.venv/bin/python -m unittest tests.test_intel_link_discovery
"""
import email.message
import json
import multiprocessing
import os
import sys
import tempfile
import threading
import time
import traceback
import unittest
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from intel_link_discovery import (  # noqa: E402
    ApiProvider, Cancelled, ChainProvider, DeadlineExceeded, ProviderUnavailable,
    RedirectBlocked, RemioProvider, SearchFailed, SearchTimeout, SelfHostedProvider,
    SerperProvider, _default_self_search_open, build_query, discover_links,
)
from intel_link_discovery import _SerperUsage  # noqa: E402
from intel_link_discovery import _default_serper_open  # noqa: E402

FAKEID = 'MzA5NzU4NDU2'
ACCOUNT_NAME = '卡兹克'


def account(has_album=False, fakeid=FAKEID, name=ACCOUNT_NAME):
    return {'fakeid': fakeid, 'name': name, 'has_album': has_album}


def long_link(mid=2650000001, biz=FAKEID, idx=1, sn='abc', chksm='deadbeef'):
    return f'https://mp.weixin.qq.com/s?__biz={biz}&mid={mid}&idx={idx}&sn={sn}&chksm={chksm}'


def short_link(code='AbCdEf123456'):
    return f'https://mp.weixin.qq.com/s/{code}'


def page(biz=FAKEID, title='标题', text='正文内容', nickname=ACCOUNT_NAME,
         published=1700000000, raw='', images=None, chars=None):
    return {'biz': biz, 'nickname': nickname, 'title': title, 'published_at': published,
            'text': text, 'images': images or [], 'url': '', 'chars': len(text) if chars is None else chars,
            'raw': raw}


class FakeProvider:
    """SearchProvider 协议的最小假实现。"""

    def __init__(self, name='remio', available=True, results=None, error=None, on_search=None):
        self.name = name
        self._available = available
        self._results = list(results or [])
        self._error = error
        self._on_search = on_search
        self.search_calls = 0
        self.attempts = 0
        self.calls = []

    def capabilities(self):
        return {'provider': self.name, 'installed': self._available, 'available': self._available,
                'freshness': ['pd', 'pw', 'pm', 'py']}

    def search(self, query, count, freshness, cancel=None, deadline=None):
        self.search_calls += 1
        self.calls.append((query, count, freshness))
        if self._on_search is not None:
            self._on_search()
        self.attempts = 1
        if self._error is not None:
            raise self._error
        return {'provider': self.name, 'results': list(self._results)}


class FakeReader:
    def __init__(self, pages=None, default=None):
        self.pages = pages or {}
        self.default = default
        self.calls = []

    def __call__(self, url):
        self.calls.append(url)
        if url in self.pages:
            value = self.pages[url]
            if isinstance(value, Exception):
                raise value
            return value
        if self.default is not None:
            return self.default
        raise ValueError('没有为这个链接准备页面')


class FakeWebTools:
    """模拟 assistant_web.WebTools：只看 job['cancel'] 真值，并按 self.timeout 计时。"""

    def __init__(self, available=True, timeout=45, results=None):
        self._available = available
        self.timeout = timeout
        self._results = list(results or [])
        self.seen_job = None
        self.seen_timeout = None

    def available(self):
        return self._available

    def search(self, args, job):
        self.seen_job = job
        self.seen_timeout = self.timeout
        if job.get('cancel'):
            raise ValueError('任务已停止')
        return {'results': list(self._results)}


class FakeHttpOpener:
    """假 HTTP 传输：记录 (url, timeout)，返回预置 (status, body) 或抛预置异常。"""

    def __init__(self, outcome=None):
        self._outcome = outcome
        self.calls = []

    def __call__(self, url, timeout):
        self.calls.append((url, timeout))
        if isinstance(self._outcome, Exception):
            raise self._outcome
        return self._outcome


class _FakeRedirectResponse:
    """最小响应对象：够 urllib 的重定向处理读 code/msg/info。"""

    def __init__(self, code, msg, headers):
        self.code = code
        self.msg = msg
        self._headers = headers

    def info(self):
        return self._headers

    def read(self, *args):
        return b''

    def close(self):
        pass


class _FakeRedirectHandler(urllib.request.HTTPHandler):
    """假 HTTP 传输：对每个请求返回 302 到外部主机，并记录真实发出的请求。

    继承 HTTPHandler，build_opener 因此不会再装默认网络处理器（零网络）；配合真实
    urllib 重定向机制，可验证默认传输确实**没有**发出第二跳。
    """

    def __init__(self, location='http://external.invalid/search'):
        super().__init__()
        self.location = location
        self.requests = []

    def http_open(self, req):
        self.requests.append(req.full_url)
        headers = email.message.Message()
        headers['Location'] = self.location
        return _FakeRedirectResponse(302, 'Found', headers)


class DiscoveryTests(unittest.TestCase):
    def test_query_month_for_no_album_week_for_known(self):
        provider = FakeProvider(results=[])
        reader = FakeReader()
        no_album = discover_links(account(has_album=False), provider, reader, {})
        known = discover_links(account(has_album=True), provider, reader, {})
        self.assertEqual(no_album['freshness'], 'pm')
        self.assertEqual(known['freshness'], 'pw')
        self.assertIn(ACCOUNT_NAME, no_album['query'])
        self.assertIn('site:mp.weixin.qq.com', no_album['query'])
        self.assertEqual(provider.calls[0][2], 'pm')
        self.assertEqual(provider.calls[1][2], 'pw')
        self.assertEqual(build_query(ACCOUNT_NAME), no_album['query'])

    def test_one_search_max_three_reads_including_failures(self):
        links = [long_link(mid=2650000000 + i) for i in range(1, 9)]
        provider = FakeProvider(results=[{'url': u} for u in links])
        pages = {links[0]: ValueError('boom'), links[1]: ValueError('boom')}
        for u in links[2:]:
            pages[u] = page()
        reader = FakeReader(pages=pages)
        receipt = discover_links(account(), provider, reader, {})
        self.assertEqual(provider.search_calls, 1)
        self.assertEqual(receipt['candidates_accepted'], 8)
        self.assertEqual(receipt['reads_attempted'], 3)
        self.assertEqual(receipt['read_failed'], 2)
        self.assertEqual(receipt['verified'], 1)
        self.assertEqual(receipt['stop_kind'], 'read_budget')

    def test_reject_non_article_spoof_host_credentials(self):
        results = [
            {'url': 'https://mp.weixin.qq.com.evil.com/s/AbCdEf123456'},
            {'url': 'https://user:pass@mp.weixin.qq.com/s/AbCdEf123456'},
            {'url': 'https://mp.weixin.qq.com:8443/s/AbCdEf123456'},
            {'url': f'https://mp.weixin.qq.com/mp/appmsgalbum?__biz={FAKEID}&album_id=123'},
            {'url': f'https://mp.weixin.qq.com/cgi-bin/appmsg?__biz={FAKEID}'},
            {'url': 'https://weixin.sogou.com/link?url=abc'},
            {'url': 'https://evil.com/s/AbCdEf123456'},
        ]
        provider = FakeProvider(results=results)
        reader = FakeReader()
        receipt = discover_links(account(), provider, reader, {})
        self.assertEqual(receipt['candidates_accepted'], 0)
        self.assertEqual(receipt['reads_attempted'], 0)
        self.assertEqual(reader.calls, [])
        self.assertEqual([c['code'] for c in receipt['candidates']], ['rejected_link'] * len(results))

    def test_dedupe_canonical_links(self):
        results = [
            {'url': 'http://mp.weixin.qq.com/s/AbCdEf123456'},
            {'url': 'https://mp.weixin.qq.com/s/AbCdEf123456#rd'},
            {'url': 'https://mp.weixin.qq.com/s/AbCdEf123456'},
        ]
        provider = FakeProvider(results=results)
        receipt = discover_links(account(), provider, FakeReader(default=page()), {})
        self.assertEqual(receipt['candidates_accepted'], 1)
        self.assertEqual(receipt['reads_attempted'], 1)
        self.assertEqual(receipt['verified'], 1)
        codes = [c['code'] for c in receipt['candidates']]
        self.assertEqual(codes.count('duplicate'), 2)

    def test_page_biz_mismatch_no_album_no_article(self):
        link = long_link(mid=2650000001)
        provider = FakeProvider(results=[{'url': link}])
        raw = '<html><script>var album_id = "12345678901234567890";</script></html>'
        reader = FakeReader(pages={link: page(biz='MzOtherBiz999', raw=raw)})
        receipt = discover_links(account(), provider, reader, {})
        self.assertEqual(receipt['verified'], 0)
        self.assertEqual(receipt['biz_mismatch'], 1)
        self.assertEqual(receipt['album_ids'], [])
        self.assertEqual(receipt['verified_articles'], [])

    def test_declared_biz_conflict(self):
        link = long_link(mid=2650000001, biz='MzOtherBiz999')
        provider = FakeProvider(results=[{'url': link}])
        reader = FakeReader(default=page())
        receipt = discover_links(account(), provider, reader, {})
        self.assertEqual(receipt['candidates_accepted'], 0)
        self.assertEqual(receipt['reads_attempted'], 0)
        self.assertEqual(reader.calls, [])
        self.assertEqual(receipt['verified'], 0)
        self.assertIn('declared_biz_conflict', [c['code'] for c in receipt['candidates']])

    def test_remio_unavailable_falls_back_once(self):
        remio = FakeProvider(name='remio', available=True,
                             error=ProviderUnavailable('remio 尚未配置搜索源'))
        api = FakeProvider(name='api', available=True, results=[{'url': long_link(mid=2650000001)}])
        provider = ChainProvider([remio, api])
        receipt = discover_links(account(), provider, FakeReader(default=page()), {})
        self.assertEqual(receipt['state'], 'ok')
        self.assertEqual(receipt['provider'], 'api')
        self.assertEqual(remio.search_calls, 1)
        self.assertEqual(api.search_calls, 1)
        self.assertEqual(receipt['search_attempted'], 2)

    def test_both_unavailable_skipped_no_retry(self):
        remio = FakeProvider(name='remio', available=False)
        api = FakeProvider(name='api', available=False)
        provider = ChainProvider([remio, api])
        receipt = discover_links(account(), provider, FakeReader(), {})
        self.assertEqual(receipt['state'], 'skipped')
        self.assertEqual(receipt['stop_kind'], 'provider_unavailable')
        self.assertEqual(remio.search_calls, 0)
        self.assertEqual(api.search_calls, 0)
        self.assertEqual(receipt['search_attempted'], 0)

    def test_remio_empty_success_no_fallback(self):
        remio = FakeProvider(name='remio', available=True, results=[])
        api = FakeProvider(name='api', available=True, results=[{'url': long_link(mid=2650000001)}])
        provider = ChainProvider([remio, api])
        receipt = discover_links(account(), provider, FakeReader(default=page()), {})
        self.assertEqual(receipt['provider'], 'remio')
        self.assertEqual(receipt['results_found'], 0)
        self.assertEqual(receipt['state'], 'ok')
        self.assertEqual(api.search_calls, 0)

    def test_cancel_never_falls_back(self):
        flag = {'on': False}
        remio = FakeProvider(name='remio', available=True,
                             error=ProviderUnavailable('remio 桌面未运行'),
                             on_search=lambda: flag.update(on=True))
        api = FakeProvider(name='api', available=True, results=[{'url': long_link(mid=2650000001)}])
        provider = ChainProvider([remio, api])
        receipt = discover_links(account(), provider, FakeReader(default=page()),
                                 {}, cancel=lambda: flag['on'])
        self.assertEqual(receipt['state'], 'cancelled')
        self.assertEqual(receipt['stop_kind'], 'cancel')
        self.assertEqual(remio.search_calls, 1)
        self.assertEqual(api.search_calls, 0)

    def test_search_timeout_distinct_failure(self):
        remio = FakeProvider(name='remio', available=True, error=SearchTimeout('联网服务响应超时'))
        api = FakeProvider(name='api', available=True, results=[{'url': long_link(mid=2650000001)}])
        provider = ChainProvider([remio, api])
        receipt = discover_links(account(), provider, FakeReader(default=page()), {})
        self.assertEqual(receipt['state'], 'failed')
        self.assertEqual(receipt['stop_kind'], 'search_timeout')
        self.assertEqual(api.search_calls, 0)
        self.assertEqual(receipt['search_attempted'], 1)

    def test_cancel_deadline_stop_before_next_read(self):
        links = [long_link(mid=2650000000 + i) for i in range(1, 4)]
        provider = FakeProvider(results=[{'url': u} for u in links])

        flag = {'on': False}

        def cancelling_reader(url):
            flag['on'] = True
            return page()

        cancelled = discover_links(account(), provider, cancelling_reader, {},
                                   cancel=lambda: flag['on'])
        self.assertEqual(cancelled['reads_attempted'], 1)
        self.assertEqual(cancelled['stop_kind'], 'cancel')
        self.assertEqual(cancelled['state'], 'cancelled')

        state = {'t': 0.0}

        def deadline_reader(url):
            state['t'] = 100.0
            return page()

        expired = discover_links(account(), provider, deadline_reader, {},
                                 deadline=50.0, clock=lambda: state['t'])
        self.assertEqual(expired['reads_attempted'], 1)
        self.assertEqual(expired['stop_kind'], 'deadline')
        self.assertEqual(expired['state'], 'failed')

    def test_html_album_ids_and_article_payload(self):
        link = long_link(mid=2650000007)
        raw = ('<html><script>var album_id = "12345678901234567890";'
               'var msgid = "2650000007"; var itemidx = "2";</script>'
               '<div id="js_content">正文</div></html>')
        parsed = page(biz=FAKEID, title='标题', text='正文', nickname=ACCOUNT_NAME,
                      published=1700000000, raw=raw,
                      images=['https://mmbiz.qpic.cn/a.jpg'])
        provider = FakeProvider(results=[{'url': link}])
        receipt = discover_links(account(), provider, FakeReader(pages={link: parsed}), {})
        self.assertEqual(receipt['verified'], 1)
        self.assertEqual(receipt['album_ids'], ['12345678901234567890'])
        entry = receipt['verified_articles'][0]
        # M3：回执里的链接已脱敏（去掉 chksm），完整链接改由 save_articles 提供。
        self.assertEqual(entry['link'], link.replace('&chksm=deadbeef', ''))
        self.assertNotIn('chksm', entry['link'])
        self.assertEqual(receipt.save_articles[0]['link'], link)
        self.assertEqual(entry['title'], '标题')
        self.assertEqual(entry['published_at'], 1700000000)
        self.assertEqual(entry['name'], ACCOUNT_NAME)
        self.assertEqual(entry['source'], 'discovery')
        self.assertEqual(entry['identity']['fakeid'], FAKEID)
        self.assertEqual(entry['identity']['msgid'], '2650000007')
        self.assertEqual(entry['identity']['itemidx'], '2')
        # M2：安全回执不再携带正文包，完整正文/图片只在保存包。
        self.assertNotIn('body', entry)
        saved = receipt.save_articles[0]
        self.assertEqual(saved['body']['text'], '正文')
        self.assertEqual(saved['body']['chars'], 2)
        self.assertEqual(saved['body']['images'], ['https://mmbiz.qpic.cn/a.jpg'])

    def test_empty_body_not_confirmed(self):
        link = long_link(mid=2650000001)
        provider = FakeProvider(results=[{'url': link}])
        parsed = page(biz=FAKEID, text='', raw='<html>album_id="12345678901234567890"</html>')
        receipt = discover_links(account(), provider, FakeReader(pages={link: parsed}), {})
        entry = receipt['verified_articles'][0]
        # M2：回执不含正文包；空正文在保存包里如实留 None。
        self.assertNotIn('body', entry)
        self.assertIsNone(receipt.save_articles[0]['body'])
        self.assertNotIn('awaiting_confirm', str(entry))
        self.assertNotIn('body_status', entry)

    def test_provider_result_schema_and_output_bounds(self):
        results = [{'url': long_link(mid=2650000000 + i), 'title': 't', 'description': 'd'}
                   for i in range(1, 13)]
        provider = FakeProvider(results=results)
        receipt = discover_links(account(), provider, FakeReader(default=page()), {})
        expected = {'version', 'provider', 'query', 'freshness', 'state', 'stop_kind',
                    'search_attempted', 'results_found', 'candidates_accepted', 'reads_attempted',
                    'verified', 'biz_mismatch', 'read_failed', 'budget', 'album_ids',
                    'verified_articles', 'candidates'}
        self.assertTrue(expected.issubset(set(receipt)))
        self.assertEqual(receipt['version'], 1)
        self.assertEqual(receipt['results_found'], 12)
        self.assertEqual(receipt['candidates_accepted'], 8)
        self.assertEqual(receipt['reads_attempted'], 3)
        self.assertEqual(receipt['budget']['max_candidates'], 8)
        self.assertEqual(receipt['budget']['max_reads'], 3)
        self.assertLessEqual(len(receipt['verified_articles']), 3)
        self.assertNotIn('raw', receipt)
        self.assertNotIn('chksm', receipt)
        # 回执里的逐候选码只有安全码，不含被拒链接
        self.assertTrue(all(set(c) == {'code'} for c in receipt['candidates']))

    def test_remio_provider_maps_webtools_states(self):
        class FakeTools:
            def __init__(self, available=True, outcome=None):
                self._available = available
                self._outcome = outcome
                self.calls = []

            def available(self):
                return self._available

            def search(self, args, job):
                self.calls.append((args, job))
                if isinstance(self._outcome, Exception):
                    raise self._outcome
                return self._outcome

        provider = RemioProvider(FakeTools(outcome={'results': [{'url': short_link()}]}))
        outcome = provider.search('q site:mp.weixin.qq.com', 8, 'pm')
        self.assertEqual(outcome['provider'], 'remio')
        self.assertEqual(outcome['results'][0]['url'], short_link())

        not_installed = RemioProvider(FakeTools(available=False))
        with self.assertRaises(ProviderUnavailable):
            not_installed.search('q', 8, 'pm')

        with self.assertRaises(ProviderUnavailable):
            RemioProvider(FakeTools(outcome=ValueError('remio 搜索额度已用完，请检查搜索服务配置'))
                          ).search('q', 8, 'pm')
        with self.assertRaises(SearchTimeout):
            RemioProvider(FakeTools(outcome=ValueError('联网服务响应超时，请稍后重试'))).search('q', 8, 'pm')
        with self.assertRaises(SearchFailed):
            RemioProvider(FakeTools(outcome=ValueError('联网搜索返回格式异常，请稍后重试'))).search('q', 8, 'pm')

    def test_api_provider_unconfigured_is_unavailable(self):
        provider = ApiProvider()
        self.assertFalse(provider.capabilities()['available'])
        with self.assertRaises(ProviderUnavailable):
            provider.search('q', 8, 'pm')

    # ── M1 预算硬上限 ───────────────────────────────────────────────────────
    def test_budget_hard_upper_bound_clamps_oversized(self):
        links = [long_link(mid=2650000000 + i) for i in range(1, 13)]
        provider = FakeProvider(results=[{'url': u} for u in links])
        receipt = discover_links(account(), provider, FakeReader(default=page()),
                                 {'max_searches': 99, 'max_candidates': 12, 'max_reads': 12})
        self.assertEqual(receipt['budget'],
                         {'max_searches': 1, 'max_candidates': 8, 'max_reads': 3})
        self.assertEqual(receipt['candidates_accepted'], 8)
        self.assertEqual(receipt['reads_attempted'], 3)
        self.assertEqual(provider.search_calls, 1)

    def test_budget_invalid_values_normalized_to_defaults(self):
        for value in (0, -5, 'abc', None, True, 3.5, [], {}):
            provider = FakeProvider(results=[])
            receipt = discover_links(account(), provider, FakeReader(), {'max_candidates': value})
            self.assertEqual(receipt['budget']['max_candidates'], 8, value)
        provider = FakeProvider(results=[])
        receipt = discover_links(account(), provider, FakeReader(),
                                 {'max_searches': 4, 'max_candidates': '5', 'max_reads': 2.0})
        self.assertEqual(receipt['budget'], {'max_searches': 1, 'max_candidates': 5, 'max_reads': 2})

    # ── M2 取消/截止贯通 ────────────────────────────────────────────────────
    def test_chain_no_fallback_after_deadline(self):
        state = {'t': 0.0}
        remio = FakeProvider(name='remio', available=True,
                             error=ProviderUnavailable('remio 桌面未运行'),
                             on_search=lambda: state.update(t=100.0))
        selfhosted = FakeProvider(name='selfhosted', available=True, results=[])
        provider = ChainProvider([remio, selfhosted])
        receipt = discover_links(account(), provider, FakeReader(), {},
                                 deadline=50.0, clock=lambda: state['t'])
        self.assertEqual(selfhosted.search_calls, 0)
        self.assertEqual(receipt['state'], 'failed')
        self.assertEqual(receipt['stop_kind'], 'deadline')

    def test_deadline_after_empty_results_not_ok(self):
        state = {'t': 0.0}
        provider = FakeProvider(results=[], on_search=lambda: state.update(t=100.0))
        receipt = discover_links(account(), provider, FakeReader(), {},
                                 deadline=50.0, clock=lambda: state['t'])
        self.assertEqual(receipt['results_found'], 0)
        self.assertEqual(receipt['state'], 'failed')
        self.assertEqual(receipt['stop_kind'], 'deadline')

    def test_deadline_after_last_read_closes_out(self):
        state = {'t': 0.0}
        link = long_link(mid=2650000001)
        provider = FakeProvider(results=[{'url': link}])

        def slow_reader(url):
            state['t'] = 100.0
            return page()

        receipt = discover_links(account(), provider, slow_reader, {},
                                 deadline=50.0, clock=lambda: state['t'])
        self.assertEqual(receipt['reads_attempted'], 1)
        self.assertEqual(receipt['verified'], 1)
        self.assertEqual(receipt['state'], 'failed')
        self.assertEqual(receipt['stop_kind'], 'deadline')

    def test_remio_provider_bridges_cancel_and_clamps_timeout(self):
        tools = FakeWebTools(results=[])
        provider = RemioProvider(tools)
        outcome = provider.search('q', 8, 'pm', deadline=time.monotonic() + 2.0)
        self.assertEqual(outcome['provider'], 'remio')
        self.assertFalse(bool(tools.seen_job['cancel']))          # 未取消：桥接对象为假
        self.assertLessEqual(tools.seen_timeout, 2.0)             # 超时压到剩余时间内
        self.assertEqual(tools.timeout, 45)                       # 用完还原
        with self.assertRaises(Cancelled):
            provider.search('q', 8, 'pm', cancel=lambda: True)

    # ── M3 回执脱敏 ─────────────────────────────────────────────────────────
    def test_receipt_strips_checksum_but_save_package_keeps_full_link(self):
        full = long_link(mid=2650000001, chksm='SENTINEL_CHECKSUM')
        provider = FakeProvider(results=[{'url': full}])
        receipt = discover_links(account(), provider, FakeReader(default=page()), {})
        self.assertNotIn('SENTINEL_CHECKSUM', json.dumps(receipt))
        self.assertNotIn('chksm', receipt['verified_articles'][0]['link'])
        self.assertEqual(receipt.save_articles[0]['link'], full)
        self.assertIn('SENTINEL_CHECKSUM', receipt.save_articles[0]['link'])

    def test_short_link_receipt_link_unchanged(self):
        link = short_link()
        provider = FakeProvider(results=[{'url': link}])
        receipt = discover_links(account(), provider, FakeReader(default=page()), {})
        self.assertEqual(receipt['verified_articles'][0]['link'], link)
        self.assertEqual(receipt.save_articles[0]['link'], link)

    # ── 协议入口：坏 results 直接失败 ───────────────────────────────────────
    def test_entry_rejects_non_list_results(self):
        class BadResults:
            name = 'bad'
            attempts = 1

            def capabilities(self):
                return {'provider': 'bad', 'available': True}

            def search(self, query, count, freshness, cancel=None, deadline=None):
                return {'provider': 'bad', 'results': 'not-a-list'}

        receipt = discover_links(account(), BadResults(), FakeReader(), {})
        self.assertEqual(receipt['state'], 'failed')
        self.assertEqual(receipt['stop_kind'], 'search_failed')
        self.assertEqual(receipt['search_attempted'], 1)

    # ── 自建 SearXNG 提供者 ─────────────────────────────────────────────────
    def test_self_hosted_normal_return_and_time_mapping(self):
        body = json.dumps({
            'results': [
                {'url': short_link('AbCdEf123456'), 'title': '标题一', 'content': '摘要一'},
                {'url': long_link(mid=2650000002), 'title': '标题二', 'content': '摘要二'},
            ],
            'unresponsive_engines': [],
        }).encode('utf-8')
        opener = FakeHttpOpener(outcome=(200, body))
        provider = SelfHostedProvider(opener=opener)
        week = provider.search('q', 8, 'pw')
        self.assertEqual(week['provider'], 'selfhosted')
        self.assertEqual([r['url'] for r in week['results']],
                         [short_link('AbCdEf123456'), long_link(mid=2650000002)])
        self.assertEqual(week['results'][0]['title'], '标题一')
        self.assertEqual(week['results'][0]['description'], '摘要一')
        self.assertTrue(opener.calls[0][0].startswith('http://127.0.0.1:8888/search?'))
        self.assertIn('format=json', opener.calls[0][0])
        self.assertIn('time_range=week', opener.calls[0][0])
        provider.search('q', 8, 'pm')
        self.assertIn('time_range=month', opener.calls[1][0])

    def test_self_hosted_rejects_non_local_address(self):
        for base in ('http://evil.com:8888', 'https://127.0.0.1:8888',
                     'http://192.168.1.5:8888', 'http://example.com', 'ftp://127.0.0.1'):
            with self.assertRaises(ValueError, msg=base):
                SelfHostedProvider(base_url=base)

    def test_self_hosted_connection_refused_is_unavailable(self):
        provider = SelfHostedProvider(opener=FakeHttpOpener(outcome=ConnectionRefusedError('no')))
        with self.assertRaises(ProviderUnavailable):
            provider.search('q', 8, 'pm')
        chain = ChainProvider([FakeProvider(name='remio', available=True,
                                            error=ProviderUnavailable('remio 桌面未运行')),
                               provider])
        receipt = discover_links(account(), chain, FakeReader(), {})
        self.assertEqual(receipt['state'], 'skipped')
        self.assertEqual(receipt['stop_kind'], 'provider_unavailable')

    def test_self_hosted_all_engines_blocked_is_skipped(self):
        body = json.dumps({'results': [],
                           'unresponsive_engines': [['baidu', 'CAPTCHA']]}).encode('utf-8')
        provider = SelfHostedProvider(opener=FakeHttpOpener(outcome=(200, body)))
        with self.assertRaises(ProviderUnavailable):
            provider.search('q', 8, 'pm')
        chain = ChainProvider([FakeProvider(name='remio', available=True,
                                            error=ProviderUnavailable('remio 桌面未运行')),
                               provider])
        receipt = discover_links(account(), chain, FakeReader(), {})
        self.assertEqual(receipt['state'], 'skipped')

    def test_self_hosted_bad_json_is_failed(self):
        provider = SelfHostedProvider(opener=FakeHttpOpener(outcome=(200, b'not json')))
        with self.assertRaises(SearchFailed):
            provider.search('q', 8, 'pm')
        receipt = discover_links(account(), provider, FakeReader(), {})
        self.assertEqual(receipt['state'], 'failed')
        self.assertEqual(receipt['stop_kind'], 'search_failed')

    def test_self_hosted_timeout_is_failed(self):
        provider = SelfHostedProvider(opener=FakeHttpOpener(outcome=TimeoutError('slow')))
        with self.assertRaises(SearchTimeout):
            provider.search('q', 8, 'pm')

    def test_self_hosted_deadline_raises(self):
        provider = SelfHostedProvider(opener=FakeHttpOpener(outcome=(200, b'{}')),
                                      clock=lambda: 100.0)
        with self.assertRaises(DeadlineExceeded):
            provider.search('q', 8, 'pm', deadline=50.0)

    def test_default_chain_is_remio_serper_selfhosted(self):
        provider = ChainProvider()
        self.assertEqual([getattr(p, 'name', '') for p in provider.providers],
                         ['remio', 'serper', 'selfhosted'])

    # ── M1 默认传输禁止重定向（真实 urllib 机制 + 假 HTTPHandler，零网络）────
    def test_default_transport_blocks_redirect_never_sends_second_hop(self):
        handler = _FakeRedirectHandler(location='http://external.invalid/search')
        provider = SelfHostedProvider(
            opener=lambda url, timeout: _default_self_search_open(
                url, timeout, handlers=(handler,)))
        with self.assertRaises(RedirectBlocked):
            provider.search('q', 8, 'pm')
        # 只发了第一跳（本机）；外部主机从未被请求。
        self.assertEqual(len(handler.requests), 1)
        self.assertTrue(handler.requests[0].startswith('http://127.0.0.1:8888/search?'))
        self.assertNotIn('external.invalid', handler.requests)

    def test_redirect_blocked_is_safe_failure_code(self):
        handler = _FakeRedirectHandler(location='https://external.invalid/s?chksm=REDIRECT_SENTINEL')
        provider = SelfHostedProvider(
            opener=lambda url, timeout: _default_self_search_open(
                url, timeout, handlers=(handler,)))
        receipt = discover_links(account(), provider, FakeReader(), {})
        self.assertEqual(receipt['state'], 'failed')
        self.assertEqual(receipt['stop_kind'], 'redirect_blocked')
        self.assertEqual(len(handler.requests), 1)
        # 回执不回显被重定向的地址/校验串。
        self.assertNotIn('external.invalid', json.dumps(receipt))
        self.assertNotIn('REDIRECT_SENTINEL', json.dumps(receipt))

    # ── M2 回执不含正文包；嵌套图片校验串不外泄；保存包完整 ───────────────────
    def test_receipt_drops_body_and_nested_sensitive_urls(self):
        link = long_link(mid=2650000001, chksm='LINK_SENTINEL')
        images = [
            'https://mmbiz.qpic.cn/a.jpg?chksm=IMAGE_SENTINEL',
            'https://mmbiz.qpic.cn/b.jpg?key=KEY_SENTINEL',
            'https://mmbiz.qpic.cn/c.jpg?signature=SIGNATURE_SENTINEL',
            'https://mmbiz.qpic.cn/d.jpg?sig=SIG_SENTINEL',
        ]
        provider = FakeProvider(results=[{'url': link}])
        receipt = discover_links(account(), provider, FakeReader(default=page(images=images)), {})
        blob = json.dumps(receipt)
        for sentinel in ('LINK_SENTINEL', 'IMAGE_SENTINEL', 'KEY_SENTINEL',
                         'SIGNATURE_SENTINEL', 'SIG_SENTINEL'):
            self.assertNotIn(sentinel, blob, sentinel)
        entry = receipt['verified_articles'][0]
        self.assertNotIn('body', entry)
        self.assertNotIn('chksm', entry['link'])
        # 保存包保持完整、未被就地改：完整链接与嵌套图片原样。
        saved = receipt.save_articles[0]
        self.assertEqual(saved['link'], link)
        self.assertIn('LINK_SENTINEL', saved['link'])
        self.assertEqual(saved['body']['images'], images)
        self.assertIn('IMAGE_SENTINEL', saved['body']['images'][0])

    # ── M3 取消优先于不可用/截止（搜索异常统一收口）─────────────────────────
    def test_cancel_beats_provider_unavailable_single_provider(self):
        flag = {'on': False}
        provider = FakeProvider(name='selfhosted', available=True,
                                error=ProviderUnavailable('自建搜索服务未运行'),
                                on_search=lambda: flag.update(on=True))
        reader = FakeReader(default=page())
        receipt = discover_links(account(), provider, reader, {}, cancel=lambda: flag['on'])
        self.assertEqual(receipt['state'], 'cancelled')
        self.assertEqual(receipt['stop_kind'], 'cancel')
        self.assertEqual(reader.calls, [])          # 取消后无后续读取

    def test_cancel_beats_last_hop_unavailable_in_chain(self):
        flag = {'on': False}
        remio = FakeProvider(name='remio', available=True,
                             error=ProviderUnavailable('remio 桌面未运行'))
        selfhosted = FakeProvider(name='selfhosted', available=True,
                                  error=ProviderUnavailable('自建搜索服务未运行'),
                                  on_search=lambda: flag.update(on=True))
        provider = ChainProvider([remio, selfhosted])
        reader = FakeReader(default=page())
        receipt = discover_links(account(), provider, reader, {}, cancel=lambda: flag['on'])
        self.assertEqual(remio.search_calls, 1)
        self.assertEqual(selfhosted.search_calls, 1)  # 最后一跳确实跑了
        self.assertEqual(receipt['state'], 'cancelled')
        self.assertEqual(receipt['stop_kind'], 'cancel')
        self.assertEqual(reader.calls, [])

    def test_cancel_beats_deadline_on_search_failure(self):
        flag = {'on': False}
        state = {'t': 0.0}

        def on_search():
            flag['on'] = True
            state['t'] = 100.0

        provider = FakeProvider(name='selfhosted', available=True,
                                error=ProviderUnavailable('自建搜索服务未运行'),
                                on_search=on_search)
        receipt = discover_links(account(), provider, FakeReader(), {},
                                 cancel=lambda: flag['on'], deadline=50.0,
                                 clock=lambda: state['t'])
        self.assertEqual(receipt['state'], 'cancelled')
        self.assertEqual(receipt['stop_kind'], 'cancel')

    def test_deadline_still_reported_when_not_cancelled(self):
        state = {'t': 0.0}
        provider = FakeProvider(name='selfhosted', available=True,
                                error=ProviderUnavailable('自建搜索服务未运行'),
                                on_search=lambda: state.update(t=100.0))
        receipt = discover_links(account(), provider, FakeReader(), {},
                                 deadline=50.0, clock=lambda: state['t'])
        self.assertEqual(receipt['state'], 'failed')
        self.assertEqual(receipt['stop_kind'], 'deadline')

    # ── L1 userinfo / 主机样例 ──────────────────────────────────────────────
    def test_self_hosted_rejects_userinfo_and_lookalike_hosts(self):
        for base in ('http://@127.0.0.1:8888',              # 空 userinfo
                     'http://user@127.0.0.1:8888',          # 非空 userinfo
                     'http://user:pass@127.0.0.1:8888',
                     'http://127.0.0.1.nip.io:8888',        # nip.io 域名
                     'http://[::ffff:127.0.0.1]:8888',      # IPv4 映射 IPv6
                     'http://[::1]:8888',                   # IPv6 回环（非白名单）
                     'http://192.168.1.5:8888'):
            with self.assertRaises(ValueError, msg=base):
                SelfHostedProvider(base_url=base)

    def test_self_hosted_accepts_local_hosts(self):
        self.assertEqual(SelfHostedProvider(base_url='http://127.0.0.1:8888').base_url,
                         'http://127.0.0.1:8888')
        self.assertEqual(SelfHostedProvider(base_url='http://localhost:8888').base_url,
                         'http://localhost:8888')
        self.assertEqual(SelfHostedProvider(base_url='http://localhost').base_url,
                         'http://localhost:8888')


class _FakeRedirectHttpsHandler(urllib.request.HTTPSHandler):
    """假 HTTPS 传输：对每个请求返回 302 到外部主机，并记录真实发出的请求。

    Serper 端点是 https，故用 HTTPSHandler 子类（http_open 不适用 https 请求）；
    继承 HTTPSHandler 让 build_opener 不再装默认 HTTPS 处理器（零网络），配合真实
    urllib 重定向机制验证默认传输确实**没有**发出第二跳。
    """

    def __init__(self, location='https://external.invalid/search'):
        super().__init__()
        self.location = location
        self.requests = []

    def https_open(self, req):
        self.requests.append(req.full_url)
        headers = email.message.Message()
        headers['Location'] = self.location
        return _FakeRedirectResponse(302, 'Found', headers)


class FakeSerperOpener:
    """假 POST 传输：记录 (url, timeout, body(解析后), headers)，返回预置结果或抛异常。"""

    def __init__(self, outcome=None):
        self._outcome = outcome
        self.calls = []

    def __call__(self, url, timeout, body, headers):
        try:
            parsed = json.loads(bytes(body).decode('utf-8'))
        except Exception:
            parsed = None
        self.calls.append({'url': url, 'timeout': timeout, 'body': parsed,
                           'headers': dict(headers)})
        if isinstance(self._outcome, Exception):
            raise self._outcome
        return self._outcome


def serper_body(organic):
    return json.dumps({'organic': organic}).encode('utf-8')


def write_key_file(directory, key='test-placeholder-serper-key', mode=0o600, name='serper.key'):
    path = Path(directory) / name
    path.write_text(key)
    os.chmod(path, mode)
    return str(path)


def serper_race_worker(key_file, usage_path, sent_path, barrier, delay, results):
    """子进程：barrier 同步后并发预留每日额度；每次物理请求往 sent_path 追加一行。

    在 `_read_record` 内 sleep，放大「读→写」窗口：有锁时第二个进程读到已自增的
    计数而被拦；无锁时两个进程都会读到 0 并各自发一次请求。
    """
    from intel_link_discovery import ProviderUnavailable, SerperProvider, _SerperUsage

    class SlowReadUsage(_SerperUsage):
        def _read_record(self, today):
            record, ok = super()._read_record(today)
            time.sleep(delay)
            return record, ok

    def opener(url, timeout, body, headers):
        with open(sent_path, 'a') as handle:
            handle.write('1\n')
        return (200, serper_body([]))

    usage = SlowReadUsage(usage_path, daily_cap=1)
    provider = SerperProvider(key_file=key_file, opener=opener, usage=usage)
    barrier.wait()
    try:
        provider.search('q', 8, 'pm')
        results.put('ok')
    except ProviderUnavailable as exc:
        results.put(exc.code)


class SerperProviderTests(unittest.TestCase):
    """付费 Serper 提供者：零网络、密钥用哨兵、每日上限写临时目录。"""

    SENTINEL = 'test-placeholder-serper-sentinel'

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = self._tmp.name
        self.key_file = write_key_file(self.dir, self.SENTINEL)
        self.usage_path = str(Path(self.dir) / 'usage.json')

    def provider(self, opener, **kwargs):
        kwargs.setdefault('usage_path', self.usage_path)
        kwargs.setdefault('daily_cap', 50)
        return SerperProvider(key_file=self.key_file, opener=opener, **kwargs)

    # ── 密钥文件：未配置/空/权限过宽 ─────────────────────────────────────────
    def test_missing_key_file_is_unavailable(self):
        provider = SerperProvider(key_file=str(Path(self.dir) / 'nope.key'),
                                  opener=FakeSerperOpener())
        caps = provider.capabilities()
        self.assertFalse(caps['available'])
        self.assertEqual(caps['reason'], 'key_file_missing')
        with self.assertRaises(ProviderUnavailable) as ctx:
            provider.search('q', 8, 'pm')
        self.assertEqual(ctx.exception.code, 'key_file_missing')

    def test_empty_key_file_is_unavailable(self):
        path = write_key_file(self.dir, key='')
        provider = SerperProvider(key_file=path, opener=FakeSerperOpener())
        with self.assertRaises(ProviderUnavailable) as ctx:
            provider.search('q', 8, 'pm')
        self.assertEqual(ctx.exception.code, 'key_file_empty')

    def test_permissive_key_file_rejected(self):
        # 变异点①：去掉权限检查后本用例转红（会读到密钥并继续）。
        path = write_key_file(self.dir, key=self.SENTINEL, mode=0o644)
        provider = SerperProvider(key_file=path, opener=FakeSerperOpener())
        caps = provider.capabilities()
        self.assertFalse(caps['available'])
        self.assertEqual(caps['reason'], 'key_file_permissions')
        with self.assertRaises(ProviderUnavailable) as ctx:
            provider.search('q', 8, 'pm')
        self.assertEqual(ctx.exception.code, 'key_file_permissions')
        self.assertNotIn(self.SENTINEL, repr(provider))

    def test_env_var_overrides_key_path(self):
        old = os.environ.get('SERPER_API_KEY_FILE')
        os.environ['SERPER_API_KEY_FILE'] = self.key_file
        try:
            provider = SerperProvider(opener=FakeSerperOpener(outcome=(200, serper_body([]))))
            self.assertTrue(provider.capabilities()['available'])
        finally:
            if old is None:
                os.environ.pop('SERPER_API_KEY_FILE', None)
            else:
                os.environ['SERPER_API_KEY_FILE'] = old

    def test_default_key_path_under_home_config(self):
        import intel_link_discovery as module
        old_env = os.environ.pop('SERPER_API_KEY_FILE', None)
        old_home = os.environ.get('HOME')
        os.environ['HOME'] = self.dir
        try:
            key, code = module._load_serper_key()
            self.assertIsNone(key)
            self.assertEqual(code, 'key_file_missing')
            cfg = Path(self.dir) / '.config' / 'article-studio'
            cfg.mkdir(parents=True)
            target = cfg / 'serper.key'
            target.write_text(self.SENTINEL)
            os.chmod(target, 0o600)
            self.assertEqual(module._load_serper_key(), (self.SENTINEL, ''))
        finally:
            if old_home is not None:
                os.environ['HOME'] = old_home
            if old_env is not None:
                os.environ['SERPER_API_KEY_FILE'] = old_env

    # ── 请求体/头/freshness/过滤 ─────────────────────────────────────────────
    def test_normal_request_body_headers_and_mp_filter(self):
        organic = [
            {'link': short_link('AbCdEf123456'), 'title': '标题一', 'snippet': '摘要一',
             'date': '2026-01-02'},
            {'link': 'https://evil.com/s/AbCdEf123456', 'title': '外部', 'snippet': 'x'},
            {'link': 'https://mp.weixin.qq.com.evil.com/s/AbCdEf123456', 'title': '伪域名',
             'snippet': 'y'},
            {'link': long_link(mid=2650000002), 'title': '标题二', 'snippet': '摘要二'},
        ]
        opener = FakeSerperOpener(outcome=(200, serper_body(organic)))
        provider = self.provider(opener)
        outcome = provider.search('数字生命卡兹克 site:mp.weixin.qq.com', 8, 'pm')
        self.assertEqual(outcome['provider'], 'serper')
        self.assertEqual([r['url'] for r in outcome['results']],
                         [short_link('AbCdEf123456'), long_link(mid=2650000002)])
        self.assertEqual(outcome['results'][0]['title'], '标题一')
        self.assertEqual(outcome['results'][0]['description'], '摘要一')
        self.assertEqual(outcome['results'][0]['date'], '2026-01-02')
        self.assertEqual(len(opener.calls), 1)  # 单次物理请求、无重试
        call = opener.calls[0]
        self.assertEqual(call['url'], 'https://google.serper.dev/search')
        self.assertEqual(call['headers']['X-API-KEY'], self.SENTINEL)
        self.assertEqual(call['body']['q'], '数字生命卡兹克 site:mp.weixin.qq.com')
        self.assertEqual(call['body']['gl'], 'cn')
        self.assertEqual(call['body']['hl'], 'zh-cn')
        self.assertEqual(call['body']['num'], 8)
        self.assertEqual(call['body']['tbs'], 'qdr:m')

    def test_num_clamped_to_ten(self):
        opener = FakeSerperOpener(outcome=(200, serper_body([])))
        self.provider(opener).search('q', 99, 'pw')
        self.assertEqual(opener.calls[0]['body']['num'], 10)

    def test_freshness_mapping(self):
        opener = FakeSerperOpener(outcome=(200, serper_body([])))
        provider = self.provider(opener)
        provider.search('q', 8, 'pw')
        provider.search('q', 8, 'pm')
        provider.search('q', 8, 'pd')
        self.assertEqual(opener.calls[0]['body']['tbs'], 'qdr:w')
        self.assertEqual(opener.calls[1]['body']['tbs'], 'qdr:m')
        self.assertNotIn('tbs', opener.calls[2]['body'])

    # ── HTTP 分类 ───────────────────────────────────────────────────────────
    def test_401_403_are_key_invalid(self):
        for code in (401, 403):
            opener = FakeSerperOpener(outcome=(code, b'{}'))
            with self.assertRaises(ProviderUnavailable, msg=code) as ctx:
                self.provider(opener).search('q', 8, 'pm')
            self.assertEqual(ctx.exception.code, 'key_invalid')

    def test_429_is_unavailable_quota_exhausted(self):
        # 变异点③：把 429 当失败后本用例转红。
        opener = FakeSerperOpener(outcome=(429, b'{}'))
        with self.assertRaises(ProviderUnavailable) as ctx:
            self.provider(opener).search('q', 8, 'pm')
        self.assertEqual(ctx.exception.code, 'quota_exhausted')

    def test_5xx_and_bad_json_are_failed(self):
        for outcome in ((500, b'{}'), (503, b'{}'), (200, b'not json'), (200, b'[]')):
            with self.assertRaises(SearchFailed, msg=repr(outcome)):
                self.provider(FakeSerperOpener(outcome=outcome)).search('q', 8, 'pm')

    def test_429_falls_back_but_5xx_does_not(self):
        remio = FakeProvider(name='remio', available=True,
                             error=ProviderUnavailable('remio 桌面未运行'))
        searx = FakeProvider(name='selfhosted', available=True, results=[])
        receipt = discover_links(
            account(), ChainProvider([remio, self.provider(FakeSerperOpener(outcome=(429, b'{}'))), searx]),
            FakeReader(), {})
        self.assertEqual(receipt['state'], 'ok')
        self.assertEqual(receipt['provider'], 'selfhosted')

        remio2 = FakeProvider(name='remio', available=True,
                              error=ProviderUnavailable('remio 桌面未运行'))
        searx2 = FakeProvider(name='selfhosted', available=True, results=[])
        receipt2 = discover_links(
            account(), ChainProvider([remio2, self.provider(FakeSerperOpener(outcome=(500, b'{}'))), searx2]),
            FakeReader(), {})
        self.assertEqual(receipt2['state'], 'failed')
        self.assertEqual(receipt2['stop_kind'], 'search_failed')
        self.assertEqual(searx2.search_calls, 0)

    # ── 超时/截止/取消 ───────────────────────────────────────────────────────
    def test_timeout_bounded_by_deadline_and_expiry(self):
        opener = FakeSerperOpener(outcome=(200, serper_body([])))
        state = {'t': 0.0}
        provider = self.provider(opener, clock=lambda: state['t'])
        provider.search('q', 8, 'pm', deadline=5.0)
        self.assertLessEqual(opener.calls[0]['timeout'], 5.0)
        state['t'] = 100.0
        with self.assertRaises(DeadlineExceeded):
            provider.search('q', 8, 'pm', deadline=50.0)
        self.assertEqual(len(opener.calls), 1)  # 过期后不再发物理请求

    def test_timeout_exception_is_search_timeout(self):
        with self.assertRaises(SearchTimeout):
            self.provider(FakeSerperOpener(outcome=TimeoutError('slow'))).search('q', 8, 'pm')

    def test_cancel_before_serper_raises_cancelled(self):
        with self.assertRaises(Cancelled):
            self.provider(FakeSerperOpener(outcome=(200, serper_body([])))).search(
                'q', 8, 'pm', cancel=lambda: True)

    def test_cancel_raised_inside_serper_marked_cancelled(self):
        flag = {'on': False}

        class CancelOpener(FakeSerperOpener):
            def __call__(self, url, timeout, body, headers):
                flag['on'] = True
                return super().__call__(url, timeout, body, headers)

        opener = CancelOpener(outcome=(200, serper_body([{'link': short_link()}])))
        receipt = discover_links(account(), self.provider(opener), FakeReader(default=page()), {},
                                 cancel=lambda: flag['on'])
        self.assertEqual(receipt['state'], 'cancelled')
        self.assertEqual(receipt['stop_kind'], 'cancel')

    # ── 每日上限：跨实例持久化 + 日期滚动 ───────────────────────────────────
    def test_daily_cap_persists_and_rolls(self):
        # 变异点④：去掉每日上限后本用例转红（第三次会成功）。
        wall = {'t': time.mktime(time.strptime('2026-10-11 12:00', '%Y-%m-%d %H:%M'))}
        opener = FakeSerperOpener(outcome=(200, serper_body([])))
        usage = _SerperUsage(self.usage_path, clock=lambda: wall['t'], daily_cap=2)
        provider = SerperProvider(key_file=self.key_file, opener=opener, usage=usage)
        provider.search('q', 8, 'pm')
        provider.search('q', 8, 'pm')
        with self.assertRaises(ProviderUnavailable) as ctx:
            provider.search('q', 8, 'pm')
        self.assertEqual(ctx.exception.code, 'daily_cap')
        self.assertEqual(len(opener.calls), 2)

        # 跨实例持久化：新实例读同一计数文件，仍被拦。
        usage2 = _SerperUsage(self.usage_path, clock=lambda: wall['t'], daily_cap=2)
        provider2 = SerperProvider(key_file=self.key_file, opener=opener, usage=usage2)
        with self.assertRaises(ProviderUnavailable) as ctx2:
            provider2.search('q', 8, 'pm')
        self.assertEqual(ctx2.exception.code, 'daily_cap')

        # 日期滚动：换一天后计数归零，可继续搜索。
        wall['t'] += 24 * 3600
        usage3 = _SerperUsage(self.usage_path, clock=lambda: wall['t'], daily_cap=2)
        provider3 = SerperProvider(key_file=self.key_file, opener=opener, usage=usage3)
        provider3.search('q', 8, 'pm')
        self.assertEqual(len(opener.calls), 3)

    def test_usage_file_has_no_key(self):
        opener = FakeSerperOpener(outcome=(200, serper_body([])))
        self.provider(opener).search('q', 8, 'pm')
        self.assertNotIn(self.SENTINEL, Path(self.usage_path).read_text())

    # ── 网络边界：主机白名单 + 禁重定向 ─────────────────────────────────────
    def test_endpoint_host_whitelist(self):
        # 变异点②：去掉主机白名单后本用例转红（非白名单地址不再抛 ValueError）。
        for base in ('https://evil.com/search', 'http://google.serper.dev/search',
                     'https://google.serper.dev.evil.com/search',
                     'https://google.serper.dev:8443/search',
                     'https://user@google.serper.dev/search'):
            with self.assertRaises(ValueError, msg=base):
                SerperProvider(key_file=self.key_file, base_url=base)
        self.assertEqual(
            SerperProvider(key_file=self.key_file,
                           base_url='https://google.serper.dev/search').endpoint,
            'https://google.serper.dev/search')

    def test_redirect_blocked_never_sends_second_hop(self):
        handler = _FakeRedirectHttpsHandler(location='https://external.invalid/search')
        provider = self.provider(
            opener=lambda url, timeout, body, headers: _default_serper_open(
                url, timeout, body, headers, handlers=(handler,)))
        with self.assertRaises(RedirectBlocked):
            provider.search('q', 8, 'pm')
        self.assertEqual(len(handler.requests), 1)
        self.assertTrue(handler.requests[0].startswith('https://google.serper.dev/search'))
        self.assertNotIn('external.invalid', handler.requests)

    # ── 默认链路 + 密钥哨兵不外泄 ───────────────────────────────────────────
    def test_default_chain_remio_serper_selfhosted(self):
        self.assertEqual([getattr(p, 'name', '') for p in ChainProvider().providers],
                         ['remio', 'serper', 'selfhosted'])

    def test_unconfigured_serper_falls_back_in_chain(self):
        remio = FakeProvider(name='remio', available=True,
                             error=ProviderUnavailable('remio 桌面未运行'))
        serper = SerperProvider(key_file=str(Path(self.dir) / 'nope.key'),
                                opener=FakeSerperOpener())
        searx = FakeProvider(name='selfhosted', available=True, results=[])
        receipt = discover_links(account(), ChainProvider([remio, serper, searx]), FakeReader(), {})
        self.assertEqual(receipt['provider'], 'selfhosted')
        self.assertEqual(receipt['state'], 'ok')

    def test_key_sentinel_never_in_outputs(self):
        organic = [{'link': short_link('AbCdEf123456'), 'title': 't', 'snippet': 's'}]
        opener = FakeSerperOpener(outcome=(200, serper_body(organic)))
        provider = self.provider(opener)
        receipt = discover_links(account(), provider, FakeReader(default=page()), {})
        self.assertNotIn(self.SENTINEL, json.dumps(receipt))
        self.assertNotIn(self.SENTINEL, repr(provider))
        self.assertNotIn(self.SENTINEL, json.dumps(provider.capabilities()))
        self.assertEqual(opener.calls[0]['headers']['X-API-KEY'], self.SENTINEL)  # 确实带上了
        bad = self.provider(FakeSerperOpener(outcome=(500, b'{}')))
        with self.assertRaises(SearchFailed) as ctx:
            bad.search('q', 8, 'pm')
        self.assertNotIn(self.SENTINEL, str(ctx.exception))

    # ── M1 并发安全：两进程 barrier + 上限 1 只放行 1 次请求 ──────────────────
    def test_daily_cap_two_processes_only_one_request(self):
        # 变异点：去锁 → 两进程都读到 0、各发一次请求，本用例转红。
        ctx = multiprocessing.get_context('fork')
        barrier = ctx.Barrier(2)
        results = ctx.Queue()
        sent_path = str(Path(self.dir) / 'sent.log')
        procs = [ctx.Process(target=serper_race_worker,
                             args=(self.key_file, self.usage_path, sent_path,
                                   barrier, 0.3, results))
                 for _ in range(2)]
        for proc in procs:
            proc.start()
        for proc in procs:
            proc.join(timeout=60)
        for proc in procs:
            self.assertEqual(proc.exitcode, 0)
        outcomes = sorted(results.get(timeout=5) for _ in range(2))
        self.assertEqual(outcomes, ['daily_cap', 'ok'])
        sent = Path(sent_path).read_text().strip().splitlines()
        self.assertEqual(len(sent), 1)  # 上限 1，只允许 1 次物理请求
        self.assertEqual(json.loads(Path(self.usage_path).read_text())['count'], 1)

    def test_usage_parent_path_is_file_safe_reject(self):
        parent_file = Path(self.dir) / 'not-a-dir'
        parent_file.write_text('x')
        opener = FakeSerperOpener(outcome=(200, serper_body([])))
        provider = SerperProvider(key_file=self.key_file, opener=opener,
                                  usage_path=str(parent_file / 'usage.json'), daily_cap=1)
        for _ in range(3):
            with self.assertRaises(ProviderUnavailable) as ctx:
                provider.search('q', 8, 'pm')
            self.assertEqual(ctx.exception.code, 'usage_unavailable')
        self.assertEqual(opener.calls, [])

    def test_usage_corrupt_json_safe_reject(self):
        Path(self.usage_path).write_text('{not-json')
        opener = FakeSerperOpener(outcome=(200, serper_body([])))
        provider = SerperProvider(key_file=self.key_file, opener=opener,
                                  usage_path=self.usage_path, daily_cap=1)
        with self.assertRaises(ProviderUnavailable) as ctx:
            provider.search('q', 8, 'pm')
        self.assertEqual(ctx.exception.code, 'usage_unavailable')
        self.assertEqual(opener.calls, [])

    def test_usage_write_failure_safe_reject(self):
        # 变异点：写失败放行 → 本用例转红（会发请求）。
        class _FailingWriteUsage(_SerperUsage):
            def _write_record(self, record):
                return False

        opener = FakeSerperOpener(outcome=(200, serper_body([])))
        usage = _FailingWriteUsage(self.usage_path, daily_cap=1)
        provider = SerperProvider(key_file=self.key_file, opener=opener, usage=usage)
        for _ in range(2):
            with self.assertRaises(ProviderUnavailable) as ctx:
                provider.search('q', 8, 'pm')
            self.assertEqual(ctx.exception.code, 'usage_unavailable')
        self.assertEqual(opener.calls, [])

    # ── M1 用量记录先完整校验再判跨天（损坏记录安全拒绝，不清零放行）──────────
    def test_usage_presets_safe_reject_no_request(self):
        # 报告表 5 种预置记录 + 形状合法但非真实日历日：全部安全拒绝，0 请求。
        # 变异点①：去掉日期校验 → "not-a-date"/"" 被当跨天归零放行，本用例转红。
        # 变异点②：去掉 count 校验 → -1/"bad" 被当合法跨天放行，本用例转红。
        presets = [
            {'date': 'not-a-date', 'count': 999},
            {'date': '', 'count': 999},
            {'date': '2000-01-01', 'count': -1},
            {'date': '2000-01-01', 'count': 'bad'},
            {'date': '2099-01-01', 'count': 999},       # 未来日期
            {'date': '2026-02-30', 'count': 3},          # 形状合法但非真实日历日
        ]
        for preset in presets:
            with self.subTest(preset=preset):
                Path(self.usage_path).write_text(json.dumps(preset))
                opener = FakeSerperOpener(outcome=(200, serper_body([])))
                provider = SerperProvider(key_file=self.key_file, opener=opener,
                                          usage_path=self.usage_path, daily_cap=1)
                with self.assertRaises(ProviderUnavailable) as ctx:
                    provider.search('q', 8, 'pm')
                self.assertEqual(ctx.exception.code, 'usage_unavailable')
                self.assertEqual(opener.calls, [])       # 损坏记录不得发放配额

    def test_usage_legal_past_date_resets(self):
        # 合法历史日期（含已超上限的计数）可归零并正常搜索。
        wall = {'t': time.mktime(time.strptime('2026-10-11 12:00', '%Y-%m-%d %H:%M'))}
        Path(self.usage_path).write_text(json.dumps({'date': '2026-10-10', 'count': 999}))
        opener = FakeSerperOpener(outcome=(200, serper_body([])))
        usage = _SerperUsage(self.usage_path, clock=lambda: wall['t'], daily_cap=1)
        provider = SerperProvider(key_file=self.key_file, opener=opener, usage=usage)
        provider.search('q', 8, 'pm')
        self.assertEqual(len(opener.calls), 1)
        saved = json.loads(Path(self.usage_path).read_text())
        self.assertEqual(saved, {'date': '2026-10-11', 'count': 1})

    def test_usage_today_accumulates(self):
        # 当天记录累加而非归零：count=1、上限 3 → 允许两次后达上限被拦。
        wall = {'t': time.mktime(time.strptime('2026-10-11 12:00', '%Y-%m-%d %H:%M'))}
        Path(self.usage_path).write_text(json.dumps({'date': '2026-10-11', 'count': 1}))
        opener = FakeSerperOpener(outcome=(200, serper_body([])))
        usage = _SerperUsage(self.usage_path, clock=lambda: wall['t'], daily_cap=3)
        provider = SerperProvider(key_file=self.key_file, opener=opener, usage=usage)
        provider.search('q', 8, 'pm')
        provider.search('q', 8, 'pm')
        with self.assertRaises(ProviderUnavailable) as ctx:
            provider.search('q', 8, 'pm')
        self.assertEqual(ctx.exception.code, 'daily_cap')
        self.assertEqual(len(opener.calls), 2)

    # ── M3 预留后、物理请求前复查取消/截止（慢额度 I/O）────────────────────
    def test_cancel_during_quota_reservation_no_request_keeps_reservation(self):
        # 变异点：去预留后复查 → 取消被忽略，本用例转红（会发请求）。
        flag = {'on': False}

        class _SlowUsage(_SerperUsage):
            def _write_record(self, record):
                ok = super()._write_record(record)
                flag['on'] = True   # 预留落盘过程中发生取消
                return ok

        opener = FakeSerperOpener(outcome=(200, serper_body([])))
        usage = _SlowUsage(self.usage_path, daily_cap=5)
        provider = SerperProvider(key_file=self.key_file, opener=opener, usage=usage)
        with self.assertRaises(Cancelled):
            provider.search('q', 8, 'pm', cancel=lambda: flag['on'])
        self.assertEqual(opener.calls, [])  # 取消后不发物理请求
        self.assertEqual(json.loads(Path(self.usage_path).read_text())['count'], 1)  # 预留保留

    def test_deadline_during_quota_reservation_no_request_keeps_reservation(self):
        state = {'t': 0.0}

        class _SlowUsage(_SerperUsage):
            def _write_record(self, record):
                ok = super()._write_record(record)
                state['t'] = 100.0  # 预留落盘过程中越过截止
                return ok

        opener = FakeSerperOpener(outcome=(200, serper_body([])))
        usage = _SlowUsage(self.usage_path, daily_cap=5)
        provider = SerperProvider(key_file=self.key_file, opener=opener, usage=usage,
                                  clock=lambda: state['t'])
        with self.assertRaises(DeadlineExceeded):
            provider.search('q', 8, 'pm', deadline=50.0)
        self.assertEqual(opener.calls, [])
        self.assertEqual(json.loads(Path(self.usage_path).read_text())['count'], 1)

    # ── M2 密钥严格校验 + 异常链清理（哨兵不入任何输出）────────────────────
    def test_key_with_embedded_newline_rejected(self):
        path = write_key_file(self.dir, key=f'{self.SENTINEL}\nINJECTED_HEADER')
        provider = SerperProvider(key_file=path, opener=FakeSerperOpener())
        caps = provider.capabilities()
        self.assertFalse(caps['available'])
        self.assertEqual(caps['reason'], 'key_file_invalid')
        with self.assertRaises(ProviderUnavailable) as ctx:
            provider.search('q', 8, 'pm')
        self.assertEqual(ctx.exception.code, 'key_file_invalid')
        self.assertNotIn(self.SENTINEL, repr(provider))
        self.assertNotIn('INJECTED_HEADER', repr(provider))

    def test_key_invalid_utf8_rejected_without_raise(self):
        path = Path(self.dir) / 'bad.key'
        path.write_bytes(b'\xff\xfe\x00\x01')
        os.chmod(path, 0o600)
        provider = SerperProvider(key_file=str(path), opener=FakeSerperOpener())
        caps = provider.capabilities()
        self.assertFalse(caps['available'])
        self.assertEqual(caps['reason'], 'key_file_invalid')
        with self.assertRaises(ProviderUnavailable) as ctx:
            provider.search('q', 8, 'pm')
        self.assertEqual(ctx.exception.code, 'key_file_invalid')

    def test_key_trailing_newline_accepted_and_stripped(self):
        path = write_key_file(self.dir, key=f'{self.SENTINEL}\n')
        opener = FakeSerperOpener(outcome=(200, serper_body([])))
        provider = SerperProvider(key_file=path, opener=opener, usage_path=self.usage_path)
        provider.search('q', 8, 'pm')
        self.assertEqual(opener.calls[0]['headers']['X-API-KEY'], self.SENTINEL)

    def test_key_directory_rejected(self):
        provider = SerperProvider(key_file=self.dir, opener=FakeSerperOpener())
        caps = provider.capabilities()
        self.assertFalse(caps['available'])
        self.assertEqual(caps['reason'], 'key_file_permissions')

    def test_key_fifo_does_not_block_constructor(self):
        # L2：密钥路径被误指为无写端 FIFO 时，O_NONBLOCK 打开立即返回、同 fd
        # fstat 判非普通文件拒绝，构造不得阻塞。变异点：去掉 O_NONBLOCK →
        # 无写端 FIFO 上 os.open 阻塞，本用例在超时窗口内转红。
        fifo = Path(self.dir) / 'serper.fifo'
        os.mkfifo(fifo, 0o600)
        opener = FakeSerperOpener(outcome=(200, serper_body([])))
        result = {}

        def build():
            try:
                result['provider'] = SerperProvider(key_file=str(fifo), opener=opener)
            except Exception as exc:      # 不得抛
                result['exc'] = exc

        worker = threading.Thread(target=build, daemon=True)
        worker.start()
        worker.join(timeout=5.0)
        self.assertFalse(worker.is_alive(), 'FIFO 密钥路径不得阻塞构造')
        self.assertNotIn('exc', result)
        provider = result['provider']
        caps = provider.capabilities()
        self.assertFalse(caps['available'])
        self.assertEqual(caps['reason'], 'key_file_permissions')
        with self.assertRaises(ProviderUnavailable) as ctx:
            provider.search('q', 8, 'pm')
        self.assertEqual(ctx.exception.code, 'key_file_permissions')
        self.assertEqual(opener.calls, [])

    def test_key_symlink_target_validated_on_opened_fd(self):
        # L1：按**实际打开的 fd 目标**校验，而非路径 stat；软链到合法目标可用，
        # 目标换成宽权限则拒绝，stat/read 之间无可利用窗口。
        target = Path(self.dir) / 'real.key'
        target.write_text(self.SENTINEL)
        os.chmod(target, 0o600)
        link = Path(self.dir) / 'link.key'
        link.symlink_to(target)
        ok = SerperProvider(key_file=str(link), opener=FakeSerperOpener())
        self.assertTrue(ok.capabilities()['available'])
        os.chmod(target, 0o644)
        bad = SerperProvider(key_file=str(link), opener=FakeSerperOpener())
        self.assertFalse(bad.capabilities()['available'])
        self.assertEqual(bad.capabilities()['reason'], 'key_file_permissions')

    def test_sentinel_absent_from_traceback_and_all_outputs(self):
        # 变异点：去 from None → traceback 带上原始异常文本，本用例转红。
        opener = FakeSerperOpener(outcome=OSError(f'transport blew up {self.SENTINEL}'))
        provider = self.provider(opener)
        try:
            provider.search('q', 8, 'pm')
            self.fail('expected ProviderUnavailable')
        except ProviderUnavailable as exc:
            message = str(exc)
            formatted = traceback.format_exc()
            representation = repr(exc)
        self.assertNotIn(self.SENTINEL, message)
        self.assertNotIn(self.SENTINEL, formatted)
        self.assertNotIn(self.SENTINEL, representation)
        self.assertNotIn(self.SENTINEL, repr(provider))
        self.assertNotIn(self.SENTINEL, json.dumps(provider.capabilities()))

    def test_receipt_has_no_sentinel_on_transport_error(self):
        opener = FakeSerperOpener(outcome=OSError(f'boom {self.SENTINEL}'))
        provider = self.provider(opener)
        receipt = discover_links(account(), provider, FakeReader(), {})
        self.assertEqual(receipt['state'], 'skipped')
        self.assertEqual(receipt['stop_kind'], 'provider_unavailable')
        self.assertNotIn(self.SENTINEL, json.dumps(receipt))


if __name__ == '__main__':
    unittest.main()
