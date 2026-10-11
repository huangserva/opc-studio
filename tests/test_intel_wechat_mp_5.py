"""公开采集尝试预算与跨轮节流；只用临时库、假客户端与假时钟。"""
import json
import socket
import unittest
from unittest.mock import patch

from intel_public import (ALBUM_REQUEST_BUDGET, SOGOU_REQUEST_BUDGET,
                          PUBLIC_MIN_INTERVAL, AlbumError, PublicClient,
                          PublicThrottle, SogouBlocked)
from intel_store import Intel
from tests.test_intel_wechat_mp_3 import StoreTestCase, BIZ, NAME


def hits(count=20):
    return [{'account': NAME, 'title': f'夹具{i}', 'published_at': i + 1,
             'sogou_link': f'https://weixin.sogou.com/link?url={i}'}
            for i in range(count)]


class CountingPublic:
    def __init__(self, search_error=None, resolve_error=OSError, album_mode='empty',
                 successful_resolves=0):
        self.search_error = search_error
        self.resolve_error = resolve_error
        self.album_mode = album_mode
        self.successful_resolves = successful_resolves
        self.search_calls = self.resolve_calls = self.page_calls = 0

    def sogou_search(self, name):
        self.search_calls += 1
        if self.search_error:
            raise self.search_error('搜索夹具失败')
        return hits()

    def sogou_resolve(self, link):
        self.resolve_calls += 1
        if self.resolve_calls <= self.successful_resolves:
            return f'https://mp.weixin.qq.com/s/FIXTURE{self.resolve_calls:04d}'
        raise self.resolve_error('跳转夹具失败')

    def album_page(self, identifier, album_id, begin_msgid=None, begin_itemidx=None):
        self.page_calls += 1
        if self.album_mode == 'error' or (self.album_mode == 'mixed' and begin_msgid):
            raise AlbumError('合集接口返回 ret=10004')
        items = [] if self.album_mode == 'empty' else [
            {'title': '合集夹具', 'link': f'https://mp.weixin.qq.com/s/ALBUM{album_id}',
             'published_at': 1, 'msgid': '1', 'itemidx': '1'}]
        return {'title': '合集夹具', 'article_count': 9, 'nickname': NAME,
                'items': items, 'continue_flag': bool(items)}


class AttemptBudgetTests(unittest.TestCase):
    def intel(self, public):
        intel = Intel.__new__(Intel)
        intel.public_client = lambda: public
        intel.albums = lambda _: [{'album_id': str(3501527933040967680 + i)}
                                 for i in range(25)]
        intel.note_album = lambda *args: None
        return intel

    def test_sogou_resolve_failures_count_against_budget(self):
        for error in (OSError, ValueError):
            with self.subTest(error=error.__name__):
                public = CountingPublic(resolve_error=error)
                result = self.intel(public).sogou_items(BIZ, NAME)
                self.assertEqual(public.search_calls + public.resolve_calls, SOGOU_REQUEST_BUDGET)
                self.assertEqual(result['requests'], SOGOU_REQUEST_BUDGET)
                self.assertEqual(len(result['items']), 2)
                self.assertTrue(all(not row['link_ok'] for row in result['items']))
                self.assertIn('上限', result['stopped'])
                self.assertFalse(result['blocked'])

    def test_sogou_search_failure_counts_one_request(self):
        for error in (OSError, ValueError, SogouBlocked):
            with self.subTest(error=error.__name__):
                public = CountingPublic(search_error=error)
                result = self.intel(public).sogou_items(BIZ, NAME)
                self.assertEqual(result['requests'], 1)
                self.assertEqual((public.search_calls, public.resolve_calls), (1, 0))
                self.assertEqual(result['blocked'], error is SogouBlocked)

    def test_sogou_captcha_on_resolve_counts_the_attempt_and_blocks(self):
        public = CountingPublic(resolve_error=SogouBlocked)
        result = self.intel(public).sogou_items(BIZ, NAME)
        self.assertTrue(result['blocked'])
        self.assertEqual(result['requests'], public.search_calls + public.resolve_calls)
        self.assertEqual(result['requests'], 2)

    def test_album_errors_across_many_albums_count_against_budget(self):
        public = CountingPublic(album_mode='error')
        result = self.intel(public).album_items(BIZ)
        self.assertEqual(public.page_calls, ALBUM_REQUEST_BUDGET)
        self.assertEqual(result['requests'], public.page_calls)
        self.assertEqual(result['stopped'], '合集接口返回 ret=10004')

    def test_album_mixed_success_then_error_never_exceeds_budget(self):
        public = CountingPublic(album_mode='mixed')
        result = self.intel(public).album_items(BIZ, max_requests=3)
        self.assertEqual(public.page_calls, 3)
        self.assertEqual(result['requests'], public.page_calls)
        self.assertEqual(len(result['items']), 2)


class RunBudgetTests(StoreTestCase):
    def setUp(self):
        super().setUp()
        guard = patch.object(socket.socket, 'connect', side_effect=AssertionError('禁止出网'))
        guard.start()
        self.addCleanup(guard.stop)

    def test_run_sogou_budget_holds_when_resolve_always_fails(self):
        self.public = CountingPublic()
        done = self.go(sogou=True, bodies=0)
        self.assertEqual(self.public.search_calls + self.public.resolve_calls, SOGOU_REQUEST_BUDGET)
        self.assertIn('上限', done['detail'])
        self.assertIn(done['status'], ('done', 'partial'))

    def test_run_album_budget_holds_when_every_album_errors(self):
        self.public = CountingPublic(album_mode='error')
        self.intel.remember_albums(BIZ, [str(3501527933040967680 + i) for i in range(25)])
        done = self.go(sogou=False, bodies=0)
        self.assertEqual(self.public.page_calls, ALBUM_REQUEST_BUDGET)
        self.assertIn(done['status'], ('done', 'partial'))
        self.assertIn('ret=10004', done['detail'])

    def test_run_still_collects_when_only_some_resolves_fail(self):
        self.public = CountingPublic(successful_resolves=1)
        done = self.go(sogou=True, bodies=0)
        self.assertEqual((self.public.search_calls, self.public.resolve_calls), (1, 2))
        self.assertIn(done['status'], ('done', 'partial'))
        rows = self.intel.articles(BIZ)['articles']
        self.assertEqual(len(rows), 2)
        self.assertEqual(sum(bool(row['link']) for row in rows), 1)

    def test_three_full_rounds_reuse_client_and_preserve_request_spacing(self):
        class Clock:
            at = 0.0
            def now(self):
                return self.at
            def sleep(self, duration):
                self.at += duration

        clock = Clock()
        throttle = PublicThrottle(now=clock.now, sleep=clock.sleep, jitter=lambda *args: 0)
        dispatches = []
        album_calls = []

        def opener(url, headers, timeout):
            dispatches.append(clock.now())
            album_calls.append(url)
            number = len(album_calls)
            return json.dumps({'base_resp': {'ret': 0}, 'getalbum_resp': {
                'base_info': {'title': '合集夹具', 'article_count': 100},
                'continue_flag': 1, 'article_list': [{'title': '轮次夹具',
                    'url': f'https://mp.weixin.qq.com/s/ROUND{number:04d}',
                    'create_time': number, 'msgid': str(number), 'itemidx': '1'}]}})

        class RoundClient(PublicClient):
            def sogou_search(self, name):
                self.throttle.wait()
                dispatches.append(clock.now())
                return hits()
            def sogou_resolve(self, link):
                self.throttle.wait()
                dispatches.append(clock.now())
                return ''

        self.public = RoundClient(throttle=throttle, opener=opener)
        self.seed_album()
        rounds = []
        for _ in range(3):
            before = len(album_calls)
            done = self.go(sogou=True, bodies=0)
            rounds.append((len(album_calls) - before, throttle.calls))
            self.assertIn(done['status'], ('done', 'partial'))
            self.assertIs(self.intel.public_client(), self.public)
        self.assertEqual(rounds, [(10, 13), (10, 13), (10, 13)])
        self.assertEqual(len(dispatches), 39)
        self.assertTrue(all(b - a >= PUBLIC_MIN_INTERVAL for a, b in zip(dispatches, dispatches[1:])))


if __name__ == '__main__':
    unittest.main()
