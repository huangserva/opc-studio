"""smallfix-intel-b · R08 边界回归：额度文件日期形状 + 自建传输 3xx。

零外网：transport 全是假对象；只写 /tmp 下的合成额度文件，不碰正式库、不读 .env/Key、不重启服务。
运行：
    ../article-importer/.venv/bin/python -m unittest tests.test_intel_discovery_edges
"""
import io
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from intel_link_discovery import (  # noqa: E402
    CODE_USAGE_UNAVAILABLE, ProviderUnavailable, RedirectBlocked, SearchFailed,
    SelfHostedProvider, SerperProvider, _SerperUsage, _valid_usage_date, discover_links,
)

WALL = time.mktime(time.strptime('2026-10-13 12:00', '%Y-%m-%d %H:%M'))
TODAY = '2026-10-13'
FAKEID = 'MzA5NzU4NDU2'
SENTINEL = 'REDIRECT_LOCATION_SENTINEL'


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


def self_search_body(link='https://mp.weixin.qq.com/s/AbCdEf123456'):
    return json.dumps({'results': [{'url': link, 'title': 't', 'content': 'c'}]}).encode('utf-8')


def _account():
    return {'fakeid': FAKEID, 'name': '卡兹克', 'has_album': False}


def _no_reader(link):
    raise ValueError('本用例不读正文')


class UsageDateShapeTests(unittest.TestCase):
    """R08(a)：额度文件日期必须是 ASCII `YYYY-MM-DD`（fullmatch），异常形状安全拒绝。"""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix='intel-b-date-')
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.usage_path = str(Path(self.dir) / 'usage.json')

    def _usage(self, cap=1):
        return _SerperUsage(self.usage_path, clock=lambda: WALL, daily_cap=cap)

    def _write(self, record):
        Path(self.usage_path).write_text(json.dumps(record), encoding='utf-8')

    def _read(self):
        return json.loads(Path(self.usage_path).read_text(encoding='utf-8'))

    def test_date_shape_is_strict_ascii(self):
        # 合法：ASCII 零填充真实日历日照常通过（不得把合法历史文件一并拒掉）。
        for good in ('2026-10-13', '2026-10-12', '2024-02-29', '2000-01-01'):
            self.assertTrue(_valid_usage_date(good), f'{good!r} 是合法日期')
        # 非法：末尾换行、Unicode 数字、Unicode/多空格、非零填充、非真实日历日。
        for bad in ('2026-10-13\n', '2026-10-13\r\n', '2026-10-13\n\n',
                    '\uff12\uff10\uff12\uff16-\uff11\uff10-\uff11\uff13',   # 全角 2026-10-13
                    '2026-\u0661\u0660-13',                                  # 阿拉伯-印度月
                    '2026-10-13 ', ' 2026-10-13', '2026-1-13', '2026-10-13x',
                    '2026-02-30', '2026-13-01', '2026-00-10', '', None, 20261013):
            self.assertFalse(_valid_usage_date(bad), f'{bad!r} 不是合法额度日期')

    def test_corrupt_date_shape_safe_reject_and_no_reset(self):
        # 变异点：把 fullmatch 退回 match+`$`/`\d` → 末尾换行与 Unicode 数字被当合法记录
        # 归零放行，本用例转红（allowed=True）。
        for corrupt in ('2026-10-12\n', '\uff12\uff10\uff12\uff16-\uff11\uff10-\uff11\uff12',
                        '2026-10-12 '):
            with self.subTest(date=corrupt):
                self._write({'date': corrupt, 'count': 7})
                reservation = self._usage(cap=10).reserve()
                self.assertFalse(reservation.allowed, f'{corrupt!r} 不得发放配额')
                self.assertEqual(reservation.code, CODE_USAGE_UNAVAILABLE)
                self.assertEqual(self._read()['count'], 7, '损坏记录不得被静默归零')

    def test_legal_history_date_still_resets_and_reserves(self):
        # 正常路径不回退：合法历史日期照常归零并放行一次搜索。
        self._write({'date': '2026-10-12', 'count': 7})
        reservation = self._usage(cap=10).reserve()
        self.assertTrue(reservation.allowed)
        self.assertEqual(self._read(), {'date': TODAY, 'count': 1})

    def test_today_record_still_accumulates(self):
        self._write({'date': TODAY, 'count': 7})
        self.assertTrue(self._usage(cap=10).reserve().allowed)
        self.assertEqual(self._read(), {'date': TODAY, 'count': 8})

    def test_provider_refuses_corrupt_date_without_physical_request(self):
        key_file = Path(self.dir) / 'serper.key'
        key_file.write_text('test-placeholder-serper-key', encoding='utf-8')
        os.chmod(key_file, 0o600)
        self._write({'date': '2026-10-12\n', 'count': 0})
        opener = FakeHttpOpener(outcome=(200, b'{"organic": []}'))
        provider = SerperProvider(key_file=str(key_file), opener=opener,
                                  usage=self._usage(cap=1))
        with self.assertRaises(ProviderUnavailable) as ctx:
            provider.search('q', 8, 'pm')
        self.assertEqual(ctx.exception.code, CODE_USAGE_UNAVAILABLE)
        self.assertEqual(opener.calls, [], '损坏额度文件不得发出物理请求')


class SelfHostedRedirectTests(unittest.TestCase):
    """R08(b)：自定义 opener 返回的状态元组 3xx 必须与默认传输一致地拒绝。"""

    def test_tuple_3xx_is_redirect_blocked(self):
        for status in (301, 302, 303, 307, 308):
            with self.subTest(status=status):
                opener = FakeHttpOpener(outcome=(status, self_search_body()))
                with self.assertRaises(RedirectBlocked) as ctx:
                    SelfHostedProvider(opener=opener).search('q', 8, 'pm')
                self.assertNotIn('mp.weixin.qq.com', str(ctx.exception),
                                 '不得回显被重定向响应里的链接')
                self.assertEqual(len(opener.calls), 1)

    def test_tuple_3xx_body_location_never_leaks(self):
        body = json.dumps({'results': [
            {'url': f'https://external.invalid/s?chksm={SENTINEL}', 'title': 't', 'content': 'c'}]}
        ).encode('utf-8')
        opener = FakeHttpOpener(outcome=(302, body))
        with self.assertRaises(RedirectBlocked) as ctx:
            SelfHostedProvider(opener=opener).search('q', 8, 'pm')
        self.assertNotIn(SENTINEL, repr(ctx.exception))

    def test_default_transport_3xx_still_blocked(self):
        # 对照组：默认传输的 HTTPError 3xx 语义不变。
        error = urllib.error.HTTPError('http://127.0.0.1:8888/search', 302, 'Found', {}, io.BytesIO())
        self.addCleanup(error.close)

        def opener(url, timeout):
            raise error

        with self.assertRaises(RedirectBlocked):
            SelfHostedProvider(opener=opener).search('q', 8, 'pm')

    def test_tuple_4xx_and_5xx_semantics_unchanged(self):
        with self.assertRaises(ProviderUnavailable):
            SelfHostedProvider(opener=FakeHttpOpener(outcome=(429, b'{}'))).search('q', 8, 'pm')
        with self.assertRaises(SearchFailed) as ctx:
            SelfHostedProvider(opener=FakeHttpOpener(outcome=(500, b'{}'))).search('q', 8, 'pm')
        self.assertIn('HTTP 500', str(ctx.exception))

    def test_tuple_200_normal_path_unaffected(self):
        opener = FakeHttpOpener(outcome=(200, self_search_body()))
        outcome = SelfHostedProvider(opener=opener).search('q', 8, 'pm')
        self.assertEqual(outcome['provider'], 'selfhosted')
        self.assertEqual([r['url'] for r in outcome['results']],
                         ['https://mp.weixin.qq.com/s/AbCdEf123456'])

    def test_discover_links_marks_redirect_blocked(self):
        # 端到端：tuple 3xx 不得被当成可用结果继续走读文/校验。
        opener = FakeHttpOpener(outcome=(302, self_search_body(
            'https://mp.weixin.qq.com/s?__biz=%s&mid=2650000001&idx=1&sn=abc&chksm=deadbeef'
            % FAKEID)))
        receipt = discover_links(_account(), SelfHostedProvider(opener=opener), _no_reader, {})
        self.assertEqual(receipt['state'], 'failed')
        self.assertEqual(receipt['stop_kind'], 'redirect_blocked')
        self.assertEqual(receipt['search_attempted'], 1)
        self.assertEqual(receipt['verified_articles'], [])


if __name__ == '__main__':
    unittest.main()
