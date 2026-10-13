"""阶段 B：自动发现接线（发现→合集→必要后台）的合成集成测试。

零外网、零凭证：发现来源与正文读者全部注入假实现；合集/后台走假客户端；
临时库 + /tmp。覆盖合同 §B必测矩阵与安全回执/频率/身份/范围三态等关键行为。

运行：
    PYTHONDONTWRITEBYTECODE=1 TMPDIR=/tmp PYTHONPATH=.:tests \
        ../article-importer/.venv/bin/python -m unittest tests.test_intel_discovery_integration
"""
import json
import sqlite3
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from intel_link_discovery import ProviderUnavailable, SearchFailed, STATE_OK
from intel_public import AlbumError, AlbumStopped
from intel_store import (DISCOVERY_ALBUM_INTERVAL_SECONDS, DISCOVERY_MAX_JSON, Intel,
                         StaleOwnershipError, discovery_aid, discovery_window)
from intel_wechat_mp import (BackendQuota, CredentialExpired, CredentialsMissing, RateLimited,
                             article_url)
from server import Store

BIZ = 'MzB5NTkwMDAwMDE='
OTHER = 'MzB5NTkwMDAwMDI='
NAME = '卡兹克'
ALBUM = '3501527933040967680'
ALBUM2 = '3501527933040967681'
SENTINEL = 'CHKSM-SENTINEL-DO-NOT-STORE'
BODY_SENTINEL = '正文哨兵不得进回执'


def long_link(mid=2650000001, biz=BIZ, idx=1, chksm=SENTINEL):
    return f'https://mp.weixin.qq.com/s?__biz={biz}&mid={mid}&idx={idx}&sn=abc&chksm={chksm}'


def album_raw(*albums):
    return ' '.join(f'<a href="https://mp.weixin.qq.com/mp/appmsgalbum?__biz={BIZ}&album_id={a}">'
                    f'album</a>' for a in albums) or '<html>no album</html>'


def page(biz=BIZ, title='标题', text=BODY_SENTINEL, nickname=NAME, published=None, raw='', images=None):
    published = 1788319000 if published is None else published
    return {'biz': biz, 'nickname': nickname, 'title': title, 'published_at': published,
            'text': text, 'images': images or [], 'url': '', 'chars': len(text or ''), 'raw': raw}


class FakeProvider:
    """SearchProvider 协议的最小假实现：零网络。"""

    def __init__(self, name='remio', available=True, results=None, error=None, on_search=None):
        self.name = name
        self._available = available
        self._results = list(results or [])
        self._error = error
        self.on_search = on_search
        self.search_calls = 0
        self.attempts = 0
        self.calls = []

    def capabilities(self):
        return {'provider': self.name, 'installed': self._available, 'available': self._available,
                'freshness': ['pd', 'pw', 'pm', 'py']}

    def search(self, query, count, freshness, cancel=None, deadline=None):
        self.search_calls += 1
        self.calls.append((query, count, freshness))
        self.attempts = 1
        if self.on_search is not None:
            self.on_search()
        if self._error is not None:
            raise self._error
        return {'provider': self.name, 'results': list(self._results)}


class FakeReader:
    """reader(link) → 页面解析 dict；按规范化链接索引，缺省用 default。"""

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


class FakePublic:
    """合集假实现；记录 begin_round 与每页返回，可配置失败。"""

    def __init__(self, pages=None):
        self.begin_rounds = 0
        self.album_calls = []
        self.pages = pages or {}
        self.on_album = None

    def begin_round(self):
        self.begin_rounds += 1

    def album_page(self, identifier, album_id, begin_msgid=None, begin_itemidx=None):
        self.album_calls.append(album_id)
        if self.on_album is not None:
            self.on_album()
        value = self.pages.get(album_id)
        if isinstance(value, Exception):
            raise value
        if value is not None:
            return value
        return {'title': '占位合集', 'article_count': 0, 'nickname': NAME, 'items': [],
                'continue_flag': False}

    def sogou_search(self, *a, **k):
        raise AssertionError('定时轮次不该访问搜狗')

    def sogou_resolve(self, *a, **k):
        raise AssertionError('定时轮次不该访问搜狗')


class FakeBackend:
    """后台假实现；记录 appmsg_list 与 credentials_ready 调用次数。"""

    def __init__(self, items=None, error=None):
        self.quota = BackendQuota(_MemorySettings())
        self.items = items if items is not None else []
        self.error = error
        self.calls = []
        self.credential_calls = 0

    def credentials_ready(self):
        self.credential_calls += 1
        return ('stub', '9999999999')

    def search_biz(self, query):
        return []

    def appmsg_list(self, identifier, begin, count):
        self.calls.append((identifier, begin, count))
        if self.error is not None:
            raise self.error
        return list(self.items)

    def fetch_article(self, url):
        raise AssertionError('发现阶段正文由 A 复用，不该走后台抓正文')


class _MemorySettings:
    def __init__(self):
        self.data = {}

    def get(self, key, default=None):
        return self.data.get(key, default)

    def set(self, key, value):
        self.data[key] = str(value)


class DiscoveryTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(self.tmp.name)
        self.provider = FakeProvider(results=[])
        self.reader = FakeReader()
        self.public = FakePublic()
        self.backend = FakeBackend()
        self.intel = Intel(self.store, client_factory=lambda s: self.backend,
                           public_factory=lambda s: self.public,
                           discovery_factory=lambda s: {'provider': self.provider, 'reader': self.reader})
        self.intel.subscribe({'fakeid': BIZ, 'name': NAME})

    def tearDown(self):
        for worker in list(self.intel.workers.values()):
            worker.join(5)
        self.tmp.cleanup()

    def round(self):
        return self.intel.daily_latest(run_id='r' * 32)

    def job(self):
        return self.intel.jobs(5)[0]

    def latest_job(self, intel=None):
        return (intel or self.intel).jobs(5)[0]

    def discovery_state(self, fakeid=BIZ):
        with self.intel.db() as connection:
            return connection.execute('SELECT * FROM discovery_state WHERE fakeid=?', (fakeid,)).fetchone()


# ── 安全回执 / 范围三态 / 身份 ──────────────────────────────────────────────
class ReceiptTests(DiscoveryTestCase):
    def test_receipt_is_safe_and_never_carries_chksm_or_body(self):
        link = long_link()
        self.provider._results = [{'url': link, 'title': '标题'}]
        self.reader.default = page(raw=album_raw(ALBUM))
        receipt = self.round()
        self.assertEqual(receipt['state'], 'done')
        row = self.job()
        self.assertTrue(row['discovery_available'])
        raw = json.dumps(row['discovery'], ensure_ascii=False)
        self.assertNotIn(SENTINEL, raw)          # chksm 不进安全回执
        self.assertNotIn(BODY_SENTINEL, raw)     # 正文不进安全回执
        self.assertEqual(row['discovery']['version'], 1)
        self.assertEqual(row['discovery']['verified'], 1)
        # 无合集 → 新鲜度 pm（30 天）；样本发布于约 39 天前 → outside。
        self.assertEqual(row['discovery']['range_counts']['outside'], 1)
        # 正文确实入库（保存包走了内部事务）
        articles = self.intel.articles(BIZ)['articles']
        self.assertEqual(articles[0]['body_status'], 'awaiting_confirm')
        self.assertIn(BODY_SENTINEL, self.intel.article(articles[0]['id'])['body'])

    def test_unknown_version_is_reported_unavailable(self):
        job_id = 'a' * 32
        with self.intel.db() as connection:
            connection.execute(
                "INSERT INTO jobs(id,fakeid,name,mode,status,started_at,discovery) "
                "VALUES(?,?,?,?,?,?,?)",
                (job_id, BIZ, NAME, 'scheduled', 'done', datetime.now(timezone.utc).isoformat(),
                 json.dumps({'version': 99, 'state': 'ok'})))
        row = self.intel.job(job_id)
        self.assertIsNone(row['discovery'])
        self.assertFalse(row['discovery_available'])
        self.assertTrue(row['discovery_present'])

    def test_oversized_receipt_stays_valid_json_and_keeps_totals(self):
        receipt = {'version': 1, 'provider': 'remio', 'state': 'ok', 'verified': 500,
                   'range_counts': {'within': 500, 'unknown': 0, 'outside': 0},
                   'candidates': [{'code': 'verified'}] * 5000,
                   'verified_articles': [{'link': 'https://mp.weixin.qq.com/s/' + 'x' * 200,
                                          'title': 't' * 300} for _ in range(5000)]}
        raw = self.intel._finish_job  # 占位，避免误用
        from intel_store import _discovery_json
        text = _discovery_json(receipt)
        self.assertLessEqual(len(text), DISCOVERY_MAX_JSON)
        parsed = json.loads(text)  # 必须是合法 JSON，不能字符串硬截断
        self.assertEqual(parsed['verified'], 500)
        self.assertEqual(parsed['state'], 'ok')

    def test_range_three_states(self):
        reference = datetime(2026, 1, 31, tzinfo=timezone.utc)
        within = int((reference - timedelta(days=3)).timestamp())
        outside = int((reference - timedelta(days=40)).timestamp())
        future = int((reference + timedelta(days=1)).timestamp())
        self.assertEqual(discovery_window(within, 'pw', reference), 'within')
        self.assertEqual(discovery_window(outside, 'pw', reference), 'outside')
        self.assertEqual(discovery_window(within, 'pm', reference), 'within')
        self.assertEqual(discovery_window(outside, 'pm', reference), 'outside')
        self.assertEqual(discovery_window(future, 'pw', reference), 'unknown')
        self.assertEqual(discovery_window(0, 'pw', reference), 'unknown')

    def test_range_uses_the_frozen_reference_not_the_wall_clock(self):
        # 文章发布于 2026-09-02；把参考时间冻结在 2026-09-05（3 天前）→ within。
        # 若误用 now()（约 2026-10-11，39 天前）会算成 outside，本用例据此证伪。
        self.provider._results = [{'url': long_link(), 'title': '标题'}]
        self.reader.default = page(published=1788319000, raw=album_raw(ALBUM))
        frozen = datetime(2026, 9, 5, tzinfo=timezone.utc)
        _receipt, _articles, _windows, counts = self.intel._discover(BIZ, NAME, False, reference=frozen)
        self.assertEqual(counts['within'], 1)
        self.assertEqual(counts['outside'], 0)

    def test_trusted_identity_aid_and_missing_identity_fallback(self):
        self.assertEqual(
            discovery_aid({'biz': BIZ, 'msgid': '2650000001', 'itemidx': '1'}, BIZ),
            discovery_aid({'biz': BIZ, 'msgid': '2650000001', 'itemidx': '1'}, BIZ))
        self.assertTrue(discovery_aid({'biz': BIZ, 'msgid': '2650000001', 'itemidx': '1'}, BIZ))
        # 缺身份 / biz 不符 / 非数字：一律回退（返回空，由调用方用链接指纹）
        self.assertEqual(discovery_aid({'biz': BIZ, 'msgid': '', 'itemidx': ''}, BIZ), '')
        self.assertEqual(discovery_aid({'biz': OTHER, 'msgid': '2650000001', 'itemidx': '1'}, BIZ), '')
        self.assertEqual(discovery_aid({'biz': BIZ, 'msgid': 'abc', 'itemidx': '1'}, BIZ), '')


class IdentityMergeTests(DiscoveryTestCase):
    def _raw_with_identity(self, mid, idx):
        return f'<html><script>var msgid = "{mid}"; var itemidx = "{idx}";</script></html>'

    def test_same_identity_short_and_long_links_merge(self):
        mid, idx = 2650000001, 1
        self.provider._results = [{'url': long_link(mid=mid, idx=idx, chksm='aaa'), 'title': '甲'}]
        self.reader.default = page(raw=self._raw_with_identity(mid, idx) + album_raw(ALBUM))
        self.round()
        first = self.intel.articles(BIZ)['total']
        self.assertIn('新增 1 篇、复用 0 篇', self.job()['detail'])
        # 清掉频率留痕，让下一轮重新发现同一可信身份（不同 chksm 的长链）。
        with self.intel.db() as connection:
            connection.execute('DELETE FROM discovery_state WHERE fakeid=?', (BIZ,))
        self.provider._results = [{'url': long_link(mid=mid, idx=idx, chksm='bbb'), 'title': '甲改'}]
        self.round()
        self.assertEqual(self.intel.articles(BIZ)['total'], first)
        # new/existing 以提交后台账计：第二次是复用，不是新增。
        self.assertIn('新增 0 篇、复用 1 篇', self.job()['detail'])

    def test_same_title_different_identity_is_kept(self):
        self.provider._results = [{'url': long_link(mid=1, idx=1), 'title': '同标题'}]
        self.reader.default = page(title='同标题', raw=self._raw_with_identity(1, 1))
        self.round()
        self.provider._results = [{'url': long_link(mid=2, idx=1), 'title': '同标题'}]
        self.reader.default = page(title='同标题', raw=self._raw_with_identity(2, 1))
        self.round()
        self.assertEqual(self.intel.articles(BIZ)['total'], 2)


# ── 到期顺序 / 合集优先 / 必要后台 ─────────────────────────────────────────
class ScheduledOrderTests(DiscoveryTestCase):
    def test_due_discovery_uses_new_album_same_round(self):
        link = long_link()
        self.provider._results = [{'url': link, 'title': '新文章'}]
        self.reader.default = page(raw=album_raw(ALBUM))
        self.public.pages[ALBUM] = {'title': '新合集', 'article_count': 1, 'nickname': NAME,
                                    'continue_flag': False,
                                    'items': [{'title': '合集文章', 'link': 'https://mp.weixin.qq.com/s/ALB0000001',
                                               'published_at': 1788319000, 'msgid': '9', 'itemidx': '1'}]}
        receipt = self.round()
        self.assertEqual(receipt['state'], 'done')
        self.assertIn(ALBUM, self.public.album_calls)     # 新发现的合集本轮立刻使用
        self.assertEqual(self.public.begin_rounds, 1)     # 每号一次
        # 无后台调用：合集已覆盖
        self.assertEqual(self.backend.calls, [])

    def test_not_due_skips_search_within_7_days(self):
        self.intel.remember_albums(BIZ, [ALBUM])
        # 先做一次（记录 attempt），再立即做第二次：第二次不该搜索。
        self.round()
        calls_after_first = self.provider.search_calls
        receipt = self.round()
        self.assertEqual(self.provider.search_calls, calls_after_first)
        job = self.latest_job()
        texts = {line['path']: line['text'] for line in job['routes']}
        self.assertIn('跳过发现', job['detail'])

    def test_no_album_falls_back_to_backend(self):
        self.backend.items = [{'title': '后台文章', 'link': 'https://mp.weixin.qq.com/s/BK00000001',
                               'published_at': 1788319000, 'aid': 'b1'}]
        receipt = self.round()
        self.assertEqual(receipt['new_articles'], 1)
        self.assertEqual(len(self.backend.calls), 1)

    def test_album_success_does_not_hit_backend(self):
        self.intel.remember_albums(BIZ, [ALBUM])
        self.public.pages[ALBUM] = {'title': '合集', 'article_count': 1, 'nickname': NAME,
                                    'continue_flag': False,
                                    'items': [{'title': '甲', 'link': 'https://mp.weixin.qq.com/s/AL00000001',
                                               'published_at': 1788319000, 'msgid': '1', 'itemidx': '1'}]}
        self.round()
        self.assertEqual(self.backend.calls, [])
        self.assertEqual(self.backend.credential_calls, 0)   # 公开路径不读凭证

    def test_album_empty_success_does_not_hit_backend(self):
        self.intel.remember_albums(BIZ, [ALBUM])
        self.round()   # 合集成功但 0 篇
        self.assertEqual(self.backend.calls, [])

    def test_album_partial_failure_does_not_hit_backend(self):
        self.intel.remember_albums(BIZ, [ALBUM, ALBUM2])
        self.public.pages[ALBUM] = {'title': '合集', 'article_count': 1, 'nickname': NAME,
                                    'continue_flag': False,
                                    'items': [{'title': '甲', 'link': 'https://mp.weixin.qq.com/s/AL00000001',
                                               'published_at': 1788319000, 'msgid': '1', 'itemidx': '1'}]}
        self.public.pages[ALBUM2] = AlbumError('合集不存在')
        self.round()
        self.assertEqual(self.backend.calls, [])

    def test_album_all_failed_falls_back_to_backend(self):
        self.intel.remember_albums(BIZ, [ALBUM])
        self.public.pages[ALBUM] = AlbumError('合集不存在')
        self.backend.items = [{'title': '后台文章', 'link': 'https://mp.weixin.qq.com/s/BK00000001',
                               'published_at': 1788319000, 'aid': 'b1'}]
        self.round()
        self.assertEqual(len(self.backend.calls), 1)

    def test_backend_cooldown_still_runs_public_path(self):
        self.intel.remember_albums(BIZ, [ALBUM])
        self.public.pages[ALBUM] = {'title': '合集', 'article_count': 1, 'nickname': NAME,
                                    'continue_flag': False,
                                    'items': [{'title': '甲', 'link': 'https://mp.weixin.qq.com/s/AL00000001',
                                               'published_at': 1788319000, 'msgid': '1', 'itemidx': '1'}]}
        self.backend.quota.settings.set(BackendQuota.K_COOLDOWN, repr(int(time.time()) + 7200))
        receipt = self.round()
        self.assertEqual(self.backend.calls, [])
        self.assertNotEqual(receipt['state'], 'cooldown')   # 公开路径有产出，不是冷却跳过
        self.assertEqual(receipt['new_articles'], 1)

    def test_200013_stops_the_round_before_the_next_account(self):
        self.intel.subscribe({'fakeid': OTHER, 'name': '二号'})
        self.backend.error = RateLimited('命中公众号后台限流（200013）', until=0)
        receipt = self.round()
        self.assertEqual(receipt['state'], 'rate_limited')
        self.assertEqual(len(self.backend.calls), 1)   # 第二个号没被请求

    def test_discovery_failure_still_uses_existing_album(self):
        self.intel.remember_albums(BIZ, [ALBUM])
        self.provider._error = SearchFailed('坏格式')
        self.public.pages[ALBUM] = {'title': '合集', 'article_count': 1, 'nickname': NAME,
                                    'continue_flag': False,
                                    'items': [{'title': '甲', 'link': 'https://mp.weixin.qq.com/s/AL00000001',
                                               'published_at': 1788319000, 'msgid': '1', 'itemidx': '1'}]}
        receipt = self.round()
        self.assertEqual(receipt['new_articles'], 1)
        self.assertEqual(len(self.public.album_calls), 1)


# ── 频率 / 预留 / 时钟回拨 / 手动绕频 ───────────────────────────────────────
class FrequencyTests(DiscoveryTestCase):
    def test_all_providers_unavailable_is_skipped(self):
        self.provider._error = ProviderUnavailable('全部不可用', provider='chain')
        receipt = self.round()
        self.assertIn(receipt['state'], ('done', 'partial'))
        job = self.job()
        self.assertEqual(job['discovery']['state'], 'skipped')

    def test_failure_consumes_the_attempt(self):
        self.intel.remember_albums(BIZ, [ALBUM])
        self.provider._error = SearchFailed('坏格式')
        self.round()
        row = self.discovery_state()
        self.assertTrue(row['last_attempt_at'])   # 失败也占机会
        calls = self.provider.search_calls
        self.round()
        self.assertEqual(self.provider.search_calls, calls)   # 未到期不再搜

    def test_clock_skew_future_attempt_is_conservatively_skipped(self):
        self.intel.remember_albums(BIZ, [ALBUM])
        future = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
        with self.intel.db() as connection:
            connection.execute("INSERT INTO discovery_state(fakeid,last_attempt_at,last_job_id) VALUES(?,?,?)",
                               (BIZ, future, 'x'))
        self.round()
        self.assertEqual(self.provider.search_calls, 0)
        self.assertIn('时钟回拨', self.job()['detail'])

    def test_frequency_survives_restart(self):
        self.intel.remember_albums(BIZ, [ALBUM])
        self.round()
        calls = self.provider.search_calls
        fresh = Intel(self.store, client_factory=lambda s: FakeBackend(),
                      public_factory=lambda s: FakePublic(),
                      discovery_factory=lambda s: {'provider': self.provider, 'reader': self.reader})
        fresh.daily_latest(run_id='r2' * 32)
        self.assertEqual(self.provider.search_calls, calls)   # 跨实例仍按 attempt 节流

    def test_manual_discovery_bypasses_frequency(self):
        self.intel.remember_albums(BIZ, [ALBUM])
        self.round()
        calls = self.provider.search_calls
        # 手动显式发现：绕过 7 天频率，仍受同一单飞闸门约束。
        self.intel.wait_for(self.intel.run({'fakeid': BIZ, 'mode': 'discovery'})['id'], timeout=5)
        self.assertGreater(self.provider.search_calls, calls)


# ── 手动发现 ────────────────────────────────────────────────────────────────
class ManualDiscoveryTests(DiscoveryTestCase):
    def test_manual_discovery_runs_only_discovery(self):
        link = long_link()
        self.provider._results = [{'url': link, 'title': '手动发现'}]
        self.reader.default = page(raw=album_raw(ALBUM))
        job = self.intel.wait_for(self.intel.run({'fakeid': BIZ, 'mode': 'discovery'})['id'], timeout=5)
        self.assertEqual(job['status'], 'done')
        self.assertEqual(job['mode'], 'discovery')
        self.assertEqual(job['mode_label'], '自动发现')
        self.assertTrue(job['discovery_available'])
        self.assertEqual(self.backend.calls, [])   # 固定不走后台
        self.assertEqual(self.intel.articles(BIZ)['total'], 1)

    def test_manual_discovery_forces_off_sogou_and_backend(self):
        self.intel.wait_for(self.intel.run({'fakeid': BIZ, 'mode': 'discovery',
                                            'sogou': True, 'backend': True})['id'], timeout=5)
        self.assertEqual(self.backend.calls, [])

    def test_unknown_mode_is_rejected(self):
        with self.assertRaises(ValueError):
            self.intel.run({'fakeid': BIZ, 'mode': 'discover'})

    def test_discovery_budget_is_8_candidates_3_reads(self):
        links = [long_link(mid=1000 + i) for i in range(20)]
        self.provider._results = [{'url': u, 'title': str(i)} for i, u in enumerate(links)]
        self.reader.default = page(raw=album_raw(ALBUM))
        self.round()
        job = self.job()
        # A 模块硬上限：候选 8、读取 3；搜索 1 次。
        self.assertEqual(job['discovery']['budget']['max_candidates'], 8)
        self.assertEqual(job['discovery']['budget']['max_reads'], 3)
        self.assertEqual(job['discovery']['search_attempted'], 1)
        self.assertLessEqual(job['discovery']['reads_attempted'], 3)


# ── 正文复用 / 空正文 ──────────────────────────────────────────────────────
class BodyReuseTests(DiscoveryTestCase):
    def test_empty_body_is_not_awaiting_confirm(self):
        self.provider._results = [{'url': long_link(), 'title': '空正文'}]
        self.reader.default = page(text='', raw=album_raw(ALBUM))
        self.round()
        row = self.intel.articles(BIZ)['articles'][0]
        self.assertEqual(row['body_status'], 'pending')

    def test_existing_successful_body_is_not_overwritten(self):
        self.provider._results = [{'url': long_link(), 'title': '甲'}]
        self.reader.default = page(text='第一版正文', raw=album_raw(ALBUM))
        self.round()
        article = self.intel.articles(BIZ)['articles'][0]
        self.reader.default = page(text='第二版正文', raw=album_raw(ALBUM))
        self.round()   # 同身份复用同一篇
        stored = self.intel.article(article['id'])
        self.assertEqual(stored['body'], '第一版正文')


# ── 迟到 owner 零业务写 / 提交后收口失败保留成果 ────────────────────────────
class OwnerGuardTests(DiscoveryTestCase):
    def test_late_owner_cannot_write_discovery_business(self):
        job_id = 'b' * 32
        with self.intel.db() as connection:
            connection.execute(
                "INSERT INTO jobs(id,fakeid,name,mode,status,started_at,ownership_version,"
                "owner_token,owner_dev,owner_ino) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (job_id, BIZ, NAME, 'scheduled', 'running', datetime.now(timezone.utc).isoformat(),
                 1, 'g' * 32, 0, 0))
        article = {'link': long_link(), 'title': '迟到发现', 'published_at': 1788319000, 'name': NAME,
                   'identity': {'biz': BIZ, 'msgid': '2650000001', 'itemidx': '1'},
                   'body': {'text': BODY_SENTINEL}}
        with self.assertRaises(StaleOwnershipError):
            self.intel._commit_discovery(BIZ, NAME, job_id, 'f' * 32, {'album_ids': [ALBUM]},
                                         [article], ['outside'])
        self.assertEqual(self.intel.articles(BIZ)['total'], 0, '迟到 owner 不得写发现文章')
        self.assertEqual(self.intel.albums(BIZ), [], '迟到 owner 不得登记合集')


class FinishFailureTests(DiscoveryTestCase):
    def test_discovery_commit_survives_a_finish_failure(self):
        self.provider._results = [{'url': long_link(), 'title': '提交后收口失败'}]
        self.reader.default = page(raw=album_raw(ALBUM))

        def boom(*a, **k):
            raise sqlite3.OperationalError('数据库不可写')

        with patch.object(self.intel, '_finish_job', boom):
            with self.assertLogs('intel_store', level='ERROR'):
                receipt = self.round()
        self.assertEqual(receipt['state'], 'error')
        # 发现提交是独立事务：收口失败不抹掉已提交的可信文章与合集。
        self.assertEqual(self.intel.articles(BIZ)['total'], 1)
        self.assertEqual([a['album_id'] for a in self.intel.albums(BIZ)], [ALBUM])


# ── B2 · M1：取消/截止贯通到发现之后的合集与后台 + reader 超时受剩余时间约束 ──
class CancelDeadlineTests(DiscoveryTestCase):
    def test_cancel_during_discovery_stops_album_and_backend(self):
        cancel = threading.Event()
        self.intel.remember_albums(BIZ, [ALBUM])
        self.public.pages[ALBUM] = {'title': '合集', 'article_count': 1, 'nickname': NAME,
                                    'continue_flag': False,
                                    'items': [{'title': '甲', 'link': 'https://mp.weixin.qq.com/s/AL00000001',
                                               'published_at': 1788319000, 'msgid': '1', 'itemidx': '1'}]}
        # provider 搜索期间置取消：发现返回 cancelled 后，不得再发合集/后台请求。
        self.provider.on_search = cancel.set
        receipt = self.intel.daily_latest(run_id='r' * 32, cancel=cancel)
        self.assertEqual(receipt['state'], 'cancelled')
        self.assertEqual(self.public.album_calls, [], '取消后不得再请求合集')
        self.assertEqual(self.backend.calls, [], '取消后不得再请求后台')

    def test_cancel_during_album_paging_stops_further_pages(self):
        cancel = threading.Event()
        self.intel.remember_albums(BIZ, [ALBUM])
        # 第一页返回 continue_flag=True，并在请求时置取消：不得继续翻页。
        self.public.pages[ALBUM] = {'title': '合集', 'article_count': 5, 'nickname': NAME,
                                    'continue_flag': True,
                                    'items': [{'title': '甲', 'link': 'https://mp.weixin.qq.com/s/AL00000001',
                                               'published_at': 1788319000, 'msgid': '1', 'itemidx': '1'}]}
        self.public.on_album = cancel.set
        receipt = self.intel.daily_latest(run_id='r' * 32, cancel=cancel)
        self.assertEqual(len(self.public.album_calls), 1, '取消后不得再翻合集页')
        self.assertEqual(receipt['state'], 'cancelled')

    def test_deadline_during_discovery_stops_before_album_and_backend(self):
        # 无合集 → 本会打后台；但发现阶段就到时限，合集/后台都必须被挡住。
        record = self.intel.subscription(BIZ)
        handle = self.intel._claim_scheduled_job(record)
        job_id, token = handle.job_id, handle.token
        self.backend.items = [{'title': '后台文章', 'link': 'https://mp.weixin.qq.com/s/BK00000001',
                               'published_at': 1788319000, 'aid': 'b1'}]
        routes, known = {}, {}
        try:
            action, extra = self.intel._scheduled_account(record, job_id, routes, known, owner=token,
                                                          cancel=None, deadline=time.monotonic() - 1)
        finally:
            handle.close()
        self.assertEqual(self.public.album_calls, [], '到时限后不得请求合集')
        self.assertEqual(self.backend.calls, [], '到时限后不得请求后台')
        self.assertEqual(action, 'stop')
        self.assertEqual(extra.get('state'), 'timeout')

    def test_reader_http_timeout_is_capped_to_remaining(self):
        from intel_wechat_mp import WechatMpClient
        seen = {}

        def opener(url, headers, timeout):
            seen['timeout'] = timeout
            raise ValueError('测试里主动中断，只看超时参数')

        client = WechatMpClient(None, self.backend.quota, opener=opener)
        reader = self.intel._deadline_reader(lambda link: client.fetch_article(link), client,
                                             None, time.monotonic() + 1.0)
        with self.assertRaises(ValueError):
            reader(f'https://mp.weixin.qq.com/s?__biz={BIZ}&mid=2650000001&idx=1')
        self.assertLess(seen['timeout'], 60, '底层 60 秒固定超时必须被压到剩余时间')
        self.assertLessEqual(seen['timeout'], 1.0 + 0.05)

    def test_reader_restores_the_original_opener(self):
        from intel_wechat_mp import WechatMpClient
        original = lambda url, headers, timeout: b'x'
        client = WechatMpClient(None, self.backend.quota, opener=original)
        reader = self.intel._deadline_reader(lambda link: client.opener('u', {}, 60), client,
                                             None, time.monotonic() + 5)
        reader('ignored')
        self.assertIs(client.opener, original, '调用结束后必须还原 opener，不长期改动共享客户端')

    def test_cancel_on_last_empty_page_is_recorded_as_cancelled(self):
        cancel = threading.Event()
        self.intel.remember_albums(BIZ, [ALBUM])
        # 成功空末页，请求期间置取消：没有下一页来补检查，也必须如实记取消（Codex 探针转测试）。
        self.public.pages[ALBUM] = {'title': '合集', 'article_count': 0, 'nickname': NAME,
                                    'continue_flag': False, 'items': []}
        self.public.on_album = cancel.set
        receipt = self.intel.daily_latest(run_id='r' * 32, cancel=cancel)
        self.assertEqual(len(self.public.album_calls), 1)
        self.assertEqual(receipt['state'], 'cancelled', '末页取消要如实记为取消，不能记 done')
        job = self.job()
        self.assertEqual(job['status'], 'done', '成果保留：任务本身正常收口')
        backend = job['outcome']['list']['routes']['backend']
        self.assertFalse(backend['requested'], '取消后不得再打后台')
        self.assertEqual(backend['attempted'], 0)


# ── B3 · M1：真实 PublicClient + PublicThrottle（只替换 sleep/opener）复现 Codex 探针 ──
class ThrottleGateTests(unittest.TestCase):
    def _client(self, clock, opener, on_sleep=None):
        from intel_public import PublicClient, PublicThrottle

        def now():
            return clock['t']

        def sleep(gap):
            clock['t'] += gap
            if on_sleep is not None:
                on_sleep(gap)

        throttle = PublicThrottle(now=now, sleep=sleep, jitter=lambda low, high: 0)
        throttle.last = 99.0  # 上一轮请求刚发过：这次必须先等节流间隔
        return PublicClient(throttle=throttle, opener=opener)

    def test_cancel_during_real_throttle_blocks_the_request(self):
        clock = {'t': 100.0}
        cancel = threading.Event()
        seen = {'calls': 0}

        def opener(url, headers, timeout):
            seen['calls'] += 1
            return b'{}'

        client = self._client(clock, opener, on_sleep=lambda gap: cancel.set())
        with self.assertRaises(AlbumStopped) as caught:
            client.album_page(BIZ, ALBUM, cancel=cancel)
        self.assertEqual(caught.exception.kind, 'cancel')
        self.assertEqual(seen['calls'], 0, '节流等待期间发生的取消：出网前必须拦住')

    def test_album_http_timeout_is_capped_to_remaining(self):
        clock = {'t': 100.0}
        seen = {}

        def opener(url, headers, timeout):
            seen['timeout'] = timeout
            raise ValueError('测试里主动中断，只看超时参数')

        client = self._client(clock, opener)
        deadline = time.monotonic() + 1.0
        with self.assertRaises(ValueError):
            client.album_page(BIZ, ALBUM, deadline=deadline)
        self.assertLess(seen['timeout'], 40, '合集请求的 40 秒固定超时必须压到剩余时间以内')
        self.assertLessEqual(seen['timeout'], 1.05)

    def test_already_expired_deadline_never_reaches_the_opener(self):
        clock = {'t': 100.0}
        seen = {'calls': 0}

        def opener(url, headers, timeout):
            seen['calls'] += 1
            return b'{}'

        client = self._client(clock, opener)
        with self.assertRaises(AlbumStopped) as caught:
            client.album_page(BIZ, ALBUM, deadline=time.monotonic() - 1)
        self.assertEqual(caught.exception.kind, 'deadline')
        self.assertEqual(seen['calls'], 0, '已到时限的合集请求不得出网')

    def test_sub_minimal_remaining_deadline_stops_instead_of_opener(self):
        # B4 · L1：剩余 5ms 低于最小可用超时——直接按截止停止（opener 0 次），
        # 不再硬给 opener 一个超出剩余的超时（B3 残余：曾实得 0.01s > 剩余 0.005s）。
        clock = {'t': 100.0}
        seen = {'calls': 0}

        def opener(url, headers, timeout):
            seen['calls'] += 1
            return b'{}'

        client = self._client(clock, opener)
        with self.assertRaises(AlbumStopped) as caught:
            client.album_page(BIZ, ALBUM, deadline=time.monotonic() + 0.005)
        self.assertEqual(caught.exception.kind, 'deadline')
        self.assertEqual(seen['calls'], 0, '剩余不足最小可用超时：这次请求不发出')

    def test_one_second_remaining_timeout_never_exceeds_the_remaining(self):
        # B4 · L1：剩余 1s → 传给 opener 的超时严格 ≤ 剩余（不是 ≤ 剩余+10ms）。
        clock = {'t': 100.0}
        seen = {}

        def opener(url, headers, timeout):
            seen['timeout'] = timeout
            raise ValueError('测试里主动中断，只看超时参数')

        client = self._client(clock, opener)
        with self.assertRaises(ValueError):
            client.album_page(BIZ, ALBUM, deadline=time.monotonic() + 1.0)
        self.assertGreater(seen['timeout'], 0, '有可用余量时仍要给 opener 正超时')
        self.assertLessEqual(seen['timeout'], 1.0, '超时必须 ≤ 剩余时间')


# ── B3 · Pi-L5：三处纵深防御分支补专门行为测试 ──────────────────────────────
class DefenseInDepthTests(DiscoveryTestCase):
    def test_need_backend_rejects_cancel_and_deadline_stop_kinds(self):
        # 变异绿例 1：删掉 _need_backend 对 stop_kind 的特例 → 本用例直接红。
        for kind in ('cancel', 'deadline'):
            album = {'albums': 0, 'stop_kind': kind, 'attempted_albums': 0,
                     'failed_albums': 0, 'unattempted_albums': 0}
            self.assertFalse(Intel._need_backend(album), f'stop_kind={kind} 不得回退后台')
        album_ok = {'albums': 0, 'stop_kind': '', 'attempted_albums': 0,
                    'failed_albums': 0, 'unattempted_albums': 0}
        self.assertTrue(Intel._need_backend(album_ok), '无合集仍需要后台')

    def test_scheduled_backend_checks_cancel_even_when_needed(self):
        # 变异绿例 2：删掉 _scheduled_backend 的取消检查 → 本用例直接红。
        record = self.intel.subscription(BIZ)
        routes, known = {}, {}
        cancel = threading.Event()
        cancel.set()
        items, info = self.intel._scheduled_backend(record, 'e' * 32, routes, True, None,
                                                    cancel=cancel, known=known)
        self.assertEqual(items, [], '取消后不得发后台请求')
        self.assertFalse(info['requested'])
        self.assertEqual(info['state'], 'not_requested')

    def test_deadline_reader_checks_cancel_before_each_read(self):
        # 变异绿例 3：删掉 _deadline_reader 读取前的取消检查 → 本用例直接红。
        reader = FakeReader(default=page())
        cancel = threading.Event()
        cancel.set()
        wrapped = self.intel._deadline_reader(reader, None, cancel, None)
        from intel_store import DiscoveryStopped
        with self.assertRaises(DiscoveryStopped):
            wrapped('https://mp.weixin.qq.com/s/anything')
        self.assertEqual(reader.calls, [], '已取消的读取不得触达 reader')


# ── B2 · M2：发现已提交成果计入 job/轮次总计；异常收口保留发现回执 ──────────────
class DiscoveryTotalsTests(DiscoveryTestCase):
    def test_discovery_commit_counts_into_job_and_round_totals(self):
        self.provider._results = [{'url': long_link(), 'title': '甲'}]
        self.reader.default = page(raw=album_raw(ALBUM))
        self.intel.remember_albums(BIZ, [ALBUM])
        self.public.pages[ALBUM] = {'title': '合集', 'article_count': 0, 'nickname': NAME,
                                    'continue_flag': False, 'items': []}
        receipt = self.round()
        self.assertEqual(receipt['state'], 'done')
        job = self.job()
        self.assertEqual(job['found'], 1, '发现提交的 1 篇要计入 job.found')
        self.assertEqual(job['added'], 1, '发现提交的 1 篇要计入 job.added')
        self.assertEqual(receipt['listed'], 1, '发现成果要计入轮次 listed')
        self.assertEqual(receipt['new_articles'], 1, '发现成果要计入轮次 new_articles')
        self.assertEqual(job['outcome']['list']['found'], 1)

    def test_backend_outcome_is_not_self_contradictory(self):
        self.intel.remember_albums(BIZ, [ALBUM])
        self.public.pages[ALBUM] = {'title': '合集', 'article_count': 0, 'nickname': NAME,
                                    'continue_flag': False, 'items': []}
        self.round()
        backend = self.job()['outcome']['list']['routes']['backend']
        self.assertEqual(backend['state'], 'not_requested')
        self.assertFalse(backend['requested'], '未请求后台时 requested 必须为 False')
        self.assertEqual(backend['attempted'], 0, '未请求后台时 attempted 必须为 0')

    def test_discovery_counts_survive_a_finish_failure(self):
        self.provider._results = [{'url': long_link(), 'title': '甲'}]
        self.reader.default = page(raw=album_raw(ALBUM))
        self.intel.remember_albums(BIZ, [ALBUM])
        self.public.pages[ALBUM] = {'title': '合集', 'article_count': 0, 'nickname': NAME,
                                    'continue_flag': False, 'items': []}
        real = self.intel._finish_job
        calls = {'n': 0}

        def flaky(*a, **k):
            calls['n'] += 1
            if calls['n'] == 1:
                raise sqlite3.OperationalError('数据库不可写')
            return real(*a, **k)

        with patch.object(self.intel, '_finish_job', flaky):
            receipt = self.round()
        self.assertGreaterEqual(calls['n'], 2, '第一次收口失败后必须由异常收口再收一次')
        job = self.job()
        self.assertEqual(job['status'], 'error')
        self.assertTrue(job['discovery_present'], '异常收口不得丢掉发现回执')
        self.assertEqual(job['discovery']['state'], 'ok')
        self.assertEqual(job['added'], 1, '异常收口仍要保留发现提交的 1 篇')
        self.assertEqual(receipt['new_articles'], 1)

    def test_discovery_counts_survive_backend_exceptions(self):
        # Codex 探针转测试：发现已提交 1 篇、无合集、必要后台抛四类异常，
        # 就地收口都必须计入发现成果（job + 轮次），不能 0/0 与真实入库矛盾。
        cases = [('rate_limited', RateLimited('200013')),
                 ('credentials', CredentialsMissing('钥匙串里没有凭证')),
                 ('credentials', CredentialExpired('凭证已失效')),
                 ('error', OSError('网络炸了'))]
        for status, exc in cases:
            with self.subTest(exc=type(exc).__name__):
                with tempfile.TemporaryDirectory() as tmp:
                    backend = FakeBackend(error=exc)
                    intel = Intel(Store(tmp),
                                  client_factory=lambda s: backend,
                                  public_factory=lambda s: FakePublic(),
                                  discovery_factory=lambda s: {
                                      'provider': FakeProvider(results=[{'url': long_link(), 'title': '甲'}]),
                                      'reader': FakeReader(default=page())})
                    intel.subscribe({'fakeid': BIZ, 'name': NAME})
                    receipt = intel.daily_latest(run_id='r' * 32)
                    job = intel.jobs(5)[0]
                    self.assertEqual(intel.articles(BIZ)['total'], 1, '发现已提交的正文就在库里')
                    self.assertEqual(job['status'], status)
                    self.assertEqual(job['found'], 1, f'{type(exc).__name__} 收口也要计入发现成果')
                    self.assertEqual(job['added'], 1)
                    self.assertEqual(receipt['new_articles'], 1, '轮次 new_articles 也要计入')
                    self.assertEqual(receipt['listed'], 1)
                    routes = job['outcome']['list']['routes']
                    self.assertEqual(routes['backend']['requested'], True, '后台请求确实发出去了才失败')
                    self.assertEqual(routes['backend']['attempted'], 1)

    def test_backend_cooldown_ledger_matches_actual_requests(self):
        # Codex cooldown 探针转测试：发现成功有产出、无合集、后台冷却零请求 ——
        # outcome 不得记 requested=true/attempted=1/state=complete。
        self.provider._results = [{'url': long_link(), 'title': '甲'}]
        self.reader.default = page()
        self.backend.quota.cooldown_until = lambda: 9999999999  # 冷却中：零请求
        receipt = self.round()
        job = self.job()
        backend = job['outcome']['list']['routes']['backend']
        self.assertEqual(self.backend.calls, [], '冷却期后台实际零请求')
        self.assertFalse(backend['requested'], '冷却期零请求：requested 必须为 False')
        self.assertEqual(backend['attempted'], 0, '冷却期零请求：attempted 必须为 0')
        self.assertNotEqual(backend['state'], 'complete', '冷却期零请求不能记 complete')
        self.assertEqual(backend['stop_kind'], 'cooldown')
        self.assertEqual(job['found'], 1, '发现成果仍要计入 job 总计')
        self.assertEqual(receipt['new_articles'], 1, '发现成果仍要计入轮次总计')


    def test_same_article_from_discovery_and_album_counts_once(self):
        # Pi-L3：同一篇既被又被合集返回（同 biz+msgid+itemidx）→ found/listed 只计 1。
        mid, idx = 2650000003, 1
        self.provider._results = [{'url': long_link(mid=mid, idx=idx, chksm='aaa'), 'title': '甲'}]
        self.reader.default = page(raw=f'<html><script>var msgid = "{mid}"; '
                                       f'var itemidx = "{idx}";</script></html>' + album_raw(ALBUM))
        self.intel.remember_albums(BIZ, [ALBUM])
        self.public.pages[ALBUM] = {'title': '合集', 'article_count': 1, 'nickname': NAME,
                                    'continue_flag': False,
                                    'items': [{'title': '甲', 'link': 'https://mp.weixin.qq.com/s/SHORT00003',
                                               'published_at': 1788319000, 'msgid': str(mid), 'itemidx': str(idx)}]}
        receipt = self.round()
        job = self.job()
        self.assertEqual(self.intel.articles(BIZ)['total'], 1, '入库去重不变：仍只有 1 篇')
        self.assertEqual(job['found'], 1, '同一篇被发现又被合集返回：found 只计 1')
        self.assertEqual(job['added'], 1)
        self.assertEqual(receipt['listed'], 1, '轮次 listed 只计 1')
        self.assertEqual(receipt['new_articles'], 1)


# ── B2 · M3：可信身份 aid 三条路线统一 ─────────────────────────────────────
class CrossRouteIdentityTests(DiscoveryTestCase):
    def _raw_with_identity(self, mid, idx):
        return f'<html><script>var msgid = "{mid}"; var itemidx = "{idx}";</script></html>'

    def test_discovery_and_album_same_identity_merge(self):
        mid, idx = 2650000001, 1
        self.provider._results = [{'url': long_link(mid=mid, idx=idx, chksm='aaa'), 'title': '甲'}]
        self.reader.default = page(raw=self._raw_with_identity(mid, idx) + album_raw(ALBUM))
        # 新发现的合集返回**不同 URL**（短链）但同一 biz+msgid+itemidx。
        self.public.pages[ALBUM] = {'title': '合集', 'article_count': 1, 'nickname': NAME,
                                    'continue_flag': False,
                                    'items': [{'title': '甲', 'link': 'https://mp.weixin.qq.com/s/SHORT00001',
                                               'published_at': 1788319000, 'msgid': str(mid), 'itemidx': str(idx)}]}
        self.round()
        self.assertEqual(self.intel.articles(BIZ)['total'], 1, '发现与合集同身份必须合并为一篇')

    def test_discovery_and_backend_same_identity_merge(self):
        mid, idx = 2650000002, 1
        self.provider._results = [{'url': long_link(mid=mid, idx=idx, chksm='aaa'), 'title': '甲'}]
        # 无合集 → 走后台；reader 页面带可信身份（msgid/itemidx）。
        self.reader.default = page(raw=self._raw_with_identity(mid, idx))
        self.backend.items = [{'title': '甲', 'link': long_link(mid=mid, idx=idx, chksm='bbb'),
                               'published_at': 1788319000, 'aid': 'backend-own-aid'}]
        self.round()
        self.assertEqual(self.intel.articles(BIZ)['total'], 1, '发现与后台同身份必须合并为一篇')


# ── B2 · M4：手动发现预留 attempt + 预留中断专门回执 ───────────────────────
class ReservationTests(DiscoveryTestCase):
    def test_manual_discovery_reserves_the_attempt(self):
        self.intel.remember_albums(BIZ, [ALBUM])
        self.intel.wait_for(self.intel.run({'fakeid': BIZ, 'mode': 'discovery'})['id'], timeout=5)
        row = self.discovery_state()
        self.assertIsNotNone(row, '手动发现也要登记这次尝试')
        self.assertTrue(row['last_attempt_at'])
        calls = self.provider.search_calls
        # 紧接的定时轮次按最近 attempt 节流，不再搜索。
        self.round()
        self.assertEqual(self.provider.search_calls, calls)

    def test_reserved_receipt_after_crash_before_request(self):
        record = self.intel.subscription(BIZ)
        handle = self.intel._claim_scheduled_job(record)
        job_id, token = handle.job_id, handle.token
        # 模拟「认领 → owner 事务预留 → 释放 handle（请求前崩溃）」。
        with self.intel.db() as connection:
            self.intel._guard_owner(connection, job_id, token)
            self.intel._reserve_discovery_attempt(connection, BIZ, job_id, owner=token, has_album=False)
        handle.close()
        self.intel._recover_owned_jobs()
        job = self.intel.job(job_id)
        self.assertEqual(job['status'], 'interrupted')
        self.assertTrue(job['discovery_present'], '预留回执不得丢失')
        self.assertEqual(job['discovery']['state'], 'reserved')
        self.assertIn('已预留尝试，结果未知', job['headline'] + job['detail'])


# ── B2 · M5：发现业务 helper 漏 owner 即拒绝（不降级） ─────────────────────
class DiscoveryOwnerRequiredTests(DiscoveryTestCase):
    def _running_v1(self, job_id):
        with self.intel.db() as connection:
            connection.execute(
                "INSERT INTO jobs(id,fakeid,name,mode,status,started_at,ownership_version,"
                "owner_token,owner_dev,owner_ino) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (job_id, BIZ, NAME, 'scheduled', 'running', datetime.now(timezone.utc).isoformat(),
                 1, 't' * 32, 0, 0))

    def test_commit_discovery_missing_owner_is_rejected(self):
        job_id = 'c' * 32
        self._running_v1(job_id)
        article = {'link': long_link(), 'title': '漏 token', 'published_at': 1788319000, 'name': NAME,
                   'identity': {'biz': BIZ, 'msgid': '2650000001', 'itemidx': '1'},
                   'body': {'text': BODY_SENTINEL}}
        with self.assertRaises(StaleOwnershipError):
            self.intel._commit_discovery(BIZ, NAME, job_id, None, {'album_ids': [ALBUM]},
                                         [article], ['outside'])
        self.assertEqual(self.intel.articles(BIZ)['total'], 0, '漏 owner 不得写发现业务')
        self.assertEqual(self.intel.albums(BIZ), [], '漏 owner 不得登记合集')

    def test_scheduled_discovery_missing_owner_is_rejected(self):
        job_id = 'd' * 32
        self._running_v1(job_id)
        routes, known = {}, {}
        with self.assertRaises(StaleOwnershipError):
            self.intel._scheduled_discovery({'fakeid': BIZ, 'name': NAME}, job_id, routes, known,
                                            None, None, None)
        self.assertIsNone(self.discovery_state(), '漏 owner 不得预留发现机会')

    def test_reservation_uses_strict_legacy_predicate(self):
        # Pi-L4：预留写 jobs.discovery 的 legacy 谓词必须与守卫同一口径 ——
        # v0 却混入 owner 字段的行不是 legacy，预留不得写入。
        job_id = 'f' * 32
        with self.intel.db() as connection:
            connection.execute(
                "INSERT INTO jobs(id,fakeid,name,mode,status,started_at,ownership_version,"
                "owner_token,owner_dev,owner_ino) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (job_id, BIZ, NAME, 'scheduled', 'done', datetime.now(timezone.utc).isoformat(),
                 0, 't' * 32, 1, 2))
            self.intel._reserve_discovery_attempt(connection, BIZ, job_id, owner=None, has_album=False)
        self.assertFalse(self.intel.job(job_id)['discovery_present'],
                         'v0 混入 owner 的行不是严格 legacy，预留回执不得写入')


# ── B2 · L1：4000 字符整份序列化硬上限 ─────────────────────────────────────
class HardLimitTests(DiscoveryTestCase):
    def test_oversized_scalars_are_hard_bounded(self):
        from intel_store import _discovery_json
        receipt = {'version': 1, 'state': 'x' * 10000, 'stop_note': 'y' * 10000,
                   'verified': 7, 'range_counts': {'within': 7, 'unknown': 0, 'outside': 0}}
        text = _discovery_json(receipt)
        self.assertLessEqual(len(text), DISCOVERY_MAX_JSON, '整份序列化必须 ≤ 4000 字符')
        parsed = json.loads(text)     # 必须是合法 JSON，不能字符串硬截断
        self.assertEqual(parsed['verified'], 7)
        self.assertEqual(parsed['range_counts']['within'], 7)

    def test_large_integers_cannot_break_the_hard_limit(self):
        # Codex 探针转测试：合法 JSON 里的大整数（10**2000）也必须被限幅（B3 · L1）。
        from intel_store import _DISCOVERY_INT_LIMIT, _discovery_json
        big = 10 ** 2000
        receipt = {'version': 1, 'state': 'ok', 'verified': big, 'results_found': big,
                   'range_counts': {'within': big, 'unknown': 0, 'outside': 0}}
        text = _discovery_json(receipt)
        self.assertLessEqual(len(text), DISCOVERY_MAX_JSON, '整份序列化必须 ≤ 4000 字符')
        parsed = json.loads(text)
        self.assertEqual(parsed['verified'], _DISCOVERY_INT_LIMIT, '超限整数封顶到可表达上限')
        self.assertEqual(parsed['results_found'], _DISCOVERY_INT_LIMIT)
        self.assertEqual(parsed['range_counts']['within'], _DISCOVERY_INT_LIMIT)


# ── B4 · M1：published_at 用独立时间戳范围，不复用计数上限 ──────────────────
class TimestampBoundTests(DiscoveryTestCase):
    def test_normal_modern_timestamp_is_preserved(self):
        # B3 回归：2026 年的正常时间（1788319000 > 10**9）原值保留，不再被截成 10**9。
        from intel_store import _discovery_json
        receipt = {'version': 1, 'state': 'ok', 'verified': 1,
                   'verified_articles': [{'link': long_link(), 'title': '标题',
                                          'published_at': 1788319000, 'window': 'within'}]}
        parsed = json.loads(_discovery_json(receipt))
        self.assertEqual(parsed['verified_articles'][0]['published_at'], 1788319000,
                         '正常现代时间必须原值保留')

    def test_huge_timestamp_becomes_null_and_receipt_stays_bounded(self):
        # 异常巨大的「合法」整数时间戳：置 null（不是截成常数），整份仍 ≤4000 合法 JSON。
        from intel_store import _discovery_json
        receipt = {'version': 1, 'state': 'ok', 'verified': 1,
                   'verified_articles': [{'link': long_link(), 'title': '标题',
                                          'published_at': 10 ** 2000, 'window': 'unknown'}]}
        text = _discovery_json(receipt)
        self.assertLessEqual(len(text), DISCOVERY_MAX_JSON, '整份序列化必须 ≤ 4000 字符')
        parsed = json.loads(text)
        self.assertIsNone(parsed['verified_articles'][0]['published_at'],
                          '越界时间置 null，不得截成常数')


# ── B4 小修：任务进度「路线」也要列出发现这一路 ─────────────────────────────
class RouteLinesTests(unittest.TestCase):
    def test_route_lines_include_the_discovery_route(self):
        raw = json.dumps({'album': '取到 2 篇', 'sogou': '定时轮次不走搜狗',
                          'backend': '取到 1 篇', 'discovery': '发现 remio 验证 3 篇，新增 2 篇、复用 1 篇'})
        lines = Intel._route_lines(raw)
        # 发现行放最前（本轮最先走发现），既有三路顺序与断言不动。
        self.assertEqual([line['path'] for line in lines],
                         ['discovery', 'album', 'sogou', 'backend'])
        self.assertEqual(lines[0]['label'], '发现')
        self.assertEqual(lines[0]['text'], '发现 remio 验证 3 篇，新增 2 篇、复用 1 篇')

    def test_route_lines_translate_bare_discovery_state_codes(self):
        for state, text in (('ok', '发现完成'), ('skipped', '发现跳过'), ('failed', '发现失败'),
                            ('cancelled', '发现被取消'), ('reserved', '已预留尝试，结果未知')):
            lines = Intel._route_lines(json.dumps({'discovery': state}))
            self.assertEqual(len(lines), 1)
            self.assertEqual(lines[0]['path'], 'discovery')
            self.assertEqual(lines[0]['label'], '发现')
            self.assertEqual(lines[0]['text'], text, f'状态 {state} 要显示中文')

    def test_route_lines_without_discovery_stay_three(self):
        lines = Intel._route_lines(json.dumps({'album': 'a', 'sogou': 'b', 'backend': 'c'}))
        self.assertEqual([line['path'] for line in lines], ['album', 'sogou', 'backend'])
