"""状态接口耗时与挂起隔离；假适配器、临时 HTTP 服务。"""
import threading
import time
from types import SimpleNamespace
from unittest.mock import patch

import collect_core
from collect_core import Adapter, Registry
from tests.test_collect_core import RouteTests

class Slow(Adapter):
    platform = 'wechat_msg'
    def __init__(self):
        self.release = threading.Event()
        self.calls = 0
    def status(self):
        self.calls += 1
        self.release.wait(5)
        return {'available': True, 'count': 17, 'detail': '已核实状态', 'extra': {'busy': False}}

class StatusPerfTests(RouteTests):
    def test_http_cold_and_warm_hung_adapter_bounded_singleflight(self):
        slow = Slow()
        self.store._collect = Registry(SimpleNamespace(), {'wechat_msg': slow})
        try:
            for _ in range(3):
                begin = time.monotonic()
                status, body = self.get('/api/collect/status')
                elapsed = time.monotonic() - begin
                print('collect_status_hung_seconds', round(elapsed, 4))
                self.assertLess(elapsed, 1)
                self.assertEqual(status, 200)
                row = next(r for r in body['platforms'] if r['id'] == 'wechat_msg')
                self.assertTrue(row['timed_out'])
                self.assertTrue(row['stale'])
                self.assertIsNone(row['checked_at'])
                self.assertIsNone(row['available'])
            self.assertEqual(slow.calls, 1)
        finally:
            slow.release.set()
        self.wait_complete(self.store._collect)
        status, body = self.get('/api/collect/status')
        row = next(r for r in body['platforms'] if r['id'] == 'wechat_msg')
        self.assertEqual(row['count'], 17)
        self.assertTrue(row['checked_at'])
        self.assertFalse(row['stale'])
        self.assertFalse(row['timed_out'])

    def wait_complete(self, registry):
        until = time.monotonic() + 2
        while registry._status_workers and time.monotonic() < until:
            time.sleep(.005)
        self.assertFalse(registry._status_workers)

    def test_stale_refresh_preserves_last_values_and_marks_age(self):
        slow = Slow()
        slow.release.set()
        registry = Registry(SimpleNamespace(), {'wechat_msg': slow})
        first = registry.status()['platforms'][1]
        self.assertEqual(first['count'], 17)
        slow.release.clear()
        try:
            with patch.object(collect_core, 'STATUS_CACHE_SECONDS', 0):
                begin = time.monotonic()
                row = registry.status()['platforms'][1]
                self.assertLess(time.monotonic() - begin, 1)
                self.assertEqual(row['count'], 17)
                self.assertEqual(row['checked_at'], first['checked_at'])
                self.assertTrue(row['stale'])
                self.assertTrue(row['timed_out'])
        finally:
            slow.release.set()
            self.wait_complete(registry)

    def test_hung_quota_and_multiple_adapters_share_total_budget(self):
        a, b = Slow(), Slow()
        b.platform = 'mail'
        registry = Registry(SimpleNamespace(), {'wechat_msg': a, 'mail': b})
        quota_release = threading.Event()
        def quota():
            quota_release.wait(5)
            return {}
        try:
            with patch.object(registry.quota, 'states', side_effect=quota):
                begin = time.monotonic()
                result = registry.status()
                self.assertLess(time.monotonic() - begin, 1)
                self.assertIsNone(result['quota'])
                self.assertTrue(result['quota_status']['timed_out'])
                for row in result['platforms']:
                    if row['id'] in ('wechat_msg', 'mail'):
                        self.assertTrue(row['timed_out'])
        finally:
            a.release.set(); b.release.set(); quota_release.set()
            self.wait_complete(registry)

    def test_listing_never_reads_four_second_quota(self):
        registry = Registry(SimpleNamespace())
        self.store._collect = registry
        def hung():
            time.sleep(4)
            return {}
        with patch.object(registry.quota, 'states', side_effect=hung) as read:
            start = time.monotonic()
            code, result = self.get('/api/collect/platforms')
            self.assertLess(time.monotonic() - start, .3)
            self.assertEqual(code, 200)
            self.assertNotIn('quota', result)
            read.assert_not_called()

    def test_failed_refresh_keeps_verified_values_and_retries_after_fifteen_seconds(self):
        slow = Slow(); slow.release.set()
        registry = Registry(SimpleNamespace(), {'wechat_msg': slow})
        first = registry.status()['platforms'][1]
        key = ('wechat_msg', id(slow))
        with registry._status_lock:
            stamp, checked, value = registry._status_cache[key]
            registry._status_cache[key] = (stamp - 31, checked, value)
        with patch.object(slow, 'status', side_effect=OSError('private details')) as read:
            row = registry.status()['platforms'][1]
            self.assertEqual(row['available'], first['available'])
            self.assertEqual(row['count'], 17)
            self.assertEqual(row['extra'], first['extra'])
            self.assertEqual(row['checked_at'], first['checked_at'])
            self.assertEqual(row['detail'], '读取状态失败：OSError（显示上次结果）')
            self.assertTrue(row['stale']); self.assertTrue(row['failed'])
            registry.status()
            self.assertEqual(read.call_count, 1)
        with registry._status_lock:
            stamp, detail = registry._status_errors[key]
            registry._status_errors[key] = (stamp - collect_core.STATUS_ERROR_RETRY_SECONDS, detail)
        row = registry.status()['platforms'][1]
        self.assertFalse(row['failed']); self.assertFalse(row['stale'])
        self.assertEqual(row['detail'], '已核实状态')

    def test_quota_failure_has_explicit_detail_and_normal_available_key_is_valid(self):
        registry = Registry(SimpleNamespace())
        with patch.object(registry.quota, 'states', side_effect=RuntimeError('secret')):
            result = registry.status()
        self.assertIsNone(result['quota'])
        self.assertEqual(result['quota_status']['detail'], '读取状态失败：RuntimeError')
        self.assertTrue(result['quota_status']['failed'])
        self.assertTrue(result['quota_status']['stale'])
        with registry._status_lock:
            stamp, detail = registry._status_errors['quota']
            registry._status_errors['quota'] = (stamp - collect_core.STATUS_ERROR_RETRY_SECONDS, detail)
        with patch.object(registry.quota, 'states', return_value={'available': {'remaining': 7}}):
            result = registry.status()
        self.assertEqual(result['quota'], {'available': {'remaining': 7}})
        self.assertFalse(result['quota_status']['failed'])
        self.assertNotIn('detail', result['quota_status'])

    def test_cold_adapter_failure_remains_unavailable_and_stale(self):
        slow = Slow()
        registry = Registry(SimpleNamespace(), {'wechat_msg': slow})
        with patch.object(slow, 'status', side_effect=ValueError('private details')):
            row = registry.status()['platforms'][1]
        self.assertFalse(row['available']); self.assertTrue(row['stale'])
        self.assertTrue(row['failed'])
        self.assertEqual(row['detail'], '读取状态失败：ValueError')
        self.assertIsNone(row['checked_at'])
