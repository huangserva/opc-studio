#!/usr/bin/env python3
"""推特雷达 A1 地基的离线测试（不联网、不碰正式目录）。

对应实施合同 impl-radar-foundation-a1-contract.md 的 15 条反例，逐条一个测试：
test_c01 … test_c15。另有名单／run 记录／原子切换的补充测试。
所有 AIsa 响应都是 mock；凭证用合成假 Key，测试同时验证产物里不出现该 Key。
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock
from urllib.parse import parse_qs, urlparse

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

import radar_bundle  # noqa: E402
import radar_collect  # noqa: E402
import radar_common as rc  # noqa: E402
import radar_store  # noqa: E402
from radar_client import fetch_account  # noqa: E402
import refresh_twitter_radar as entry  # noqa: E402

FAKE_KEY = "sk" + "-aisa-" + "SYNTHETIC-TEST-9f3a"  # 合成假 Key；不在文件里留下凭证字面量
BASE = datetime(2026, 10, 15, 4, 0, 0, tzinfo=timezone.utc)
EPOCH_MS = rc.EPOCH_MS


# ------------------------------------------------------------------ 构造工具

def sid_for(dt: datetime) -> str:
    ms = int(dt.timestamp() * 1000)
    return str(((ms - EPOCH_MS) << 22) + 7)


def raw_tweet(handle: str, tweet_id: str, *, text="AI 实测：Claude Code 多窗口工作流", views=1000,
              likes=10, retweets=None, replies=None, followers=5000, user_id="777",
              include_views=True, include_followers=True) -> dict:
    tw = {
        "id": tweet_id,
        "text": text,
        "createdAt": dt_str(dt_from_sid(tweet_id)),
        "url": f"https://x.com/{handle}/status/{tweet_id}",
        "likeCount": likes,
        "author": {"userName": handle, "id": user_id, "name": handle},
    }
    if include_views:
        tw["viewCount"] = views
    if retweets is not None:
        tw["retweetCount"] = retweets
    if replies is not None:
        tw["replyCount"] = replies
    if include_followers:
        tw["author"]["followers"] = followers
    return tw


def dt_from_sid(tweet_id: str) -> datetime:
    return rc.tweet_id_to_utc(tweet_id)


def dt_str(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%a %b %d %H:%M:%S +0000 %Y")


def payload(tweets, *, has_next=False, cursor=None, code=0, status="success"):
    body = {"code": code, "status": status, "msg": "ok", "data": {"tweets": tweets}}
    if has_next:
        body["has_next_page"] = True
        body["next_cursor"] = cursor or "CURSOR_2"
    return body


def make_opener(script: dict):
    """script: handle → 页面列表（dict=响应体，Exception=抛异常）。"""

    def opener(url: str, api_key: str, timeout: float):
        query = parse_qs(urlparse(url).query)
        handle = query["userName"][0]
        page = 2 if "cursor" in query else 1
        spec = script.get(handle, [payload([])])
        item = spec[page - 1] if page - 1 < len(spec) else payload([])
        if isinstance(item, BaseException):
            raise item
        return item, 200

    return opener


def targets_fixture(count: int = 51) -> list[dict]:
    return [{"handle": f"vip{i:03d}", "name": f"博主{i}"} for i in range(1, count + 1)]


def write_targets_file(path: Path, count: int = 51) -> Path:
    doc = {"schema_version": rc.TARGETS_SCHEMA, "expected_count": count, "targets": targets_fixture(count)}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def normalize_run(tweets_by_handle: dict, base: datetime, *, failed: dict | None = None,
                  followers_by_handle: dict | None = None) -> dict:
    """离线构造一份 run 记录（走真实的 normalize_tweet），用于 bundle 层测试。"""
    accounts, records = {}, []
    for handle, raws in tweets_by_handle.items():
        fetched = base - timedelta(minutes=1)
        recs = []
        for raw in raws:
            rec = rc.normalize_tweet(raw, handle, fetched)
            rec["is_stale"] = False
            rec["sampled_run_id"] = "prev-run"
            recs.append(rec)
        records.extend(recs)
        followers, source, f_at = rc.pick_live_followers(recs)
        accounts[rc.normalize_handle(handle)] = {
            "handle": handle, "status": "ok", "ok": True, "pages": 1, "calls": 1,
            "fetched_at": rc.iso_utc(fetched), "sampled_tweets": len(recs),
            "followers": followers, "followers_source": source, "followers_fetched_at": f_at,
        }
    for handle, status in (failed or {}).items():
        accounts[rc.normalize_handle(handle)] = {
            "handle": handle, "status": status, "ok": False, "pages": 0, "calls": 1,
            "fetched_at": None, "sampled_tweets": 0, "followers": None,
            "followers_source": "unknown", "followers_fetched_at": None,
        }
    for handle, value in (followers_by_handle or {}).items():
        accounts[rc.normalize_handle(handle)].update(value)
    records.sort(key=lambda r: str(r.get("published_at") or ""), reverse=True)
    return {
        "schema_version": rc.SCHEMA_VERSION, "run_id": "run-test", "started_at": rc.iso_utc(base),
        "finished_at": rc.iso_utc(base), "base_time": rc.iso_utc(base),
        "target_count": len(accounts), "attempted": len(accounts),
        "success": sum(1 for a in accounts.values() if a["ok"]),
        "failed": sum(1 for a in accounts.values() if not a["ok"]),
        "empty": 0, "coverage": 1.0, "complete": True, "api_calls": len(accounts),
        "pages_fetched": len(accounts), "deadline_hit": False,
        "accounts": accounts, "tweets": records, "kind_counts": {},
    }


def capture_stdout(fn, *args, **kwargs):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        result = fn(*args, **kwargs)
    return result, buf.getvalue()


class TempRepo:
    """临时仓库根：只建 public/ 与 workspace/，不碰正式目录。"""

    def __init__(self, targets_count: int = 51):
        self.dir = Path(tempfile.mkdtemp(prefix="radar-a1-test-"))
        (self.dir / "public").mkdir(parents=True, exist_ok=True)
        self.targets = write_targets_file(self.dir / "scripts" / "radar_targets.json", targets_count)

    @property
    def root(self) -> Path:
        return self.dir

    def workspace(self) -> Path:
        return radar_store.workspace_root(self.dir)

    def public_bundle(self) -> Path:
        return radar_store.public_bundle_path(self.dir)

    def run_entry(self, opener, *, build=None, extra=()):
        def _fetch(handle, api_key, **kw):
            return fetch_account(handle, api_key, opener=opener, **kw)

        def _collect(**kw):
            return radar_collect.collect(fetch=_fetch, **kw)

        with mock.patch.dict(os.environ, {"AISA_API_KEY": FAKE_KEY}), \
                mock.patch.object(entry, "collect", _collect), \
                mock.patch.object(entry, "build_bundle", build or radar_bundle.build_bundle):
            code, out = capture_stdout(entry.main, ["--repo-root", str(self.dir), "--targets", str(self.targets), *extra])
        return code, out

    def cleanup(self):
        import shutil
        shutil.rmtree(self.dir, ignore_errors=True)


# ------------------------------------------------------------------ 15 条反例

class C01BusinessError(unittest.TestCase):
    """反例 1：HTTP 200 + {"code":401,...} → 业务错误，不计成功。"""

    def test_business_error_is_not_success(self):
        opener = make_opener({"vip001": [{"code": 401, "message": "unauthorized"}]})
        res = fetch_account("vip001", FAKE_KEY, opener=opener, base_time=BASE)
        self.assertEqual(res["status"], rc.STATUS_BUSINESS)
        self.assertEqual(res["tweets"], [])
        self.assertEqual(res["calls"], 1)
        self.assertIn("401", res["error"])

    def test_run_counts_business_error_as_failed(self):
        script = {h: [payload([raw_tweet(h, sid_for(BASE - timedelta(hours=2)))])] for h in ("vip001", "vip002")}
        script["vip003"] = [{"code": 401, "message": "unauthorized"}]
        run = radar_collect.collect(api_key=FAKE_KEY, targets=targets_fixture(3), run_id="r1",
                                   base_time=BASE, fetch=lambda h, k, **kw: fetch_account(h, k, opener=make_opener(script), **kw))
        self.assertEqual(run["accounts"]["vip003"]["status"], "business_error")
        self.assertEqual(run["success"], 2)
        self.assertEqual(run["failed"], 1)


class C02TimeoutKeepsOldTimestamps(unittest.TestCase):
    """反例 2：1 号超时 → failed=1；该号旧帖保留旧 fetched_at 并标 is_stale。"""

    def test_timeout_marks_failed_and_keeps_old_fetched_at(self):
        repo = TempRepo()
        try:
            old_id = sid_for(BASE - timedelta(hours=3))
            ok_script = {h: [payload([raw_tweet(h, sid_for(BASE - timedelta(hours=2)), views=90_000, likes=400)])]
                         for h in (f"vip{i:03d}" for i in range(1, 52))}
            ok_script["vip001"] = [payload([raw_tweet("vip001", old_id, views=88_000, likes=350)])]
            code, out = repo.run_entry(make_opener(ok_script))
            self.assertEqual(code, 0, out)
            first = json.loads(repo.public_bundle().read_text(encoding="utf-8"))
            old_fetched = next(t for t in first["vip_tweets"] if t["id"] == old_id)["fetched_at"]
            self.assertIsNotNone(old_fetched)

            # 第二轮：vip001 超时，其余正常 → 覆盖率 50/51 = 98% ≥ 95%，仍发布
            slow_script = {h: [payload([raw_tweet(h, sid_for(BASE - timedelta(hours=1)), views=90_000, likes=400)])]
                           for h in (f"vip{i:03d}" for i in range(2, 52))}
            slow_script["vip001"] = [TimeoutError("timed out")]
            code, out = repo.run_entry(make_opener(slow_script))
            self.assertEqual(code, 0, out)
            run_paths = sorted((repo.workspace() / "snapshots").rglob("*.json"))
            self.assertEqual(len(run_paths), 2)
            runs = [json.loads(p.read_text(encoding="utf-8")) for p in run_paths]
            latest = [r for r in runs if r["accounts"]["vip001"]["status"] == "timeout"]
            self.assertEqual(len(latest), 1, "应恰好有一轮 vip001 超时")
            latest = latest[0]
            self.assertEqual(latest["failed"], 1)
            self.assertIsNone(latest["accounts"]["vip001"]["fetched_at"])

            bundle = json.loads(repo.public_bundle().read_text(encoding="utf-8"))
            carried = [t for t in bundle["vip_tweets"] if t["id"] == old_id]
            self.assertEqual(len(carried), 1)
            self.assertTrue(carried[0]["is_stale"])
            self.assertEqual(carried[0]["fetched_at"], old_fetched, "旧帖必须保留它自己的旧 fetched_at")
            self.assertNotEqual(carried[0]["fetched_at"], bundle["generated_at"])
        finally:
            repo.cleanup()


class C03CoverageGate(unittest.TestCase):
    """反例 3：4 号失败（覆盖率 <95%）→ 本轮标不完整，manifest 不切换。"""

    def test_incomplete_run_is_not_published(self):
        repo = TempRepo()
        try:
            good = {h: [payload([raw_tweet(h, sid_for(BASE - timedelta(hours=2)), views=90_000, likes=400)])]
                    for h in (f"vip{i:03d}" for i in range(1, 52))}
            code, out = repo.run_entry(make_opener(good))
            self.assertEqual(code, 0, out)
            manifest_before = json.loads((repo.workspace() / "manifest.json").read_text(encoding="utf-8"))
            bundle_before = repo.public_bundle().read_bytes()

            script = dict(good)
            for h in ("vip001", "vip002", "vip003", "vip004"):
                script[h] = [payload([], code=401, status="error")]
            code, out = repo.run_entry(make_opener(script))
            self.assertEqual(code, 1, out)
            self.assertIn("不完整", out)
            manifest_after = json.loads((repo.workspace() / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest_after["run_id"], manifest_before["run_id"], "manifest 不得切换到不完整轮次")
            self.assertEqual(repo.public_bundle().read_bytes(), bundle_before)
            self.assertEqual(len(manifest_after["run_id"]), len(manifest_before["run_id"]))

            # 快照仍留档，且明确标了不完整
            snaps = sorted((repo.workspace() / "snapshots").rglob("*.json"))
            self.assertEqual(len(snaps), 2)
            runs = [json.loads(p.read_text(encoding="utf-8")) for p in snaps]
            incomplete = [r for r in runs if not r["complete"]]
            self.assertEqual(len(incomplete), 1, "只应有一轮被标不完整")
            self.assertEqual(incomplete[0]["failed"], 4)
            self.assertLess(incomplete[0]["coverage"], 0.95)
        finally:
            repo.cleanup()


class C04AtomicPublish(unittest.TestCase):
    """反例 4：发布中途抛异常 → 旧 manifest/bundle 完好，无截断 JSON、无残留临时文件。"""

    def _twice(self, repo, script):
        first = repo.run_entry(make_opener(script))
        self.assertEqual(first[0], 0, first[1])
        return json.loads(repo.public_bundle().read_text(encoding="utf-8"))

    def test_unserializable_payload_leaves_old_artifacts(self):
        repo = TempRepo()
        try:
            script = {h: [payload([raw_tweet(h, sid_for(BASE - timedelta(hours=2)), views=90_000, likes=400)])]
                      for h in (f"vip{i:03d}" for i in range(1, 52))}
            before = self._twice(repo, script)
            manifest_before = json.loads((repo.workspace() / "manifest.json").read_text(encoding="utf-8"))

            def broken_build(run, previous, targets, now=None):
                bundle = radar_bundle.build_bundle(run, previous, targets, now=now)
                bundle["boom"] = object()
                return bundle

            code, out = repo.run_entry(make_opener(script), build=broken_build)
            self.assertEqual(code, 4, out)
            self.assertIn("发布失败", out)
            self.assertEqual(json.loads(repo.public_bundle().read_text(encoding="utf-8")), before)
            self.assertEqual(json.loads((repo.workspace() / "manifest.json").read_text(encoding="utf-8"))["run_id"],
                             manifest_before["run_id"])
            self._assert_no_temp(repo)
        finally:
            repo.cleanup()

    def test_replace_failure_leaves_no_temp_file(self):
        repo = TempRepo()
        try:
            script = {h: [payload([raw_tweet(h, sid_for(BASE - timedelta(hours=2)), views=90_000, likes=400)])]
                      for h in (f"vip{i:03d}" for i in range(1, 52))}
            before = self._twice(repo, script)
            manifest_before = json.loads((repo.workspace() / "manifest.json").read_text(encoding="utf-8"))
            real_replace = os.replace
            calls = {"n": 0}

            def flaky_replace(src, dst, *a, **kw):
                if str(dst).endswith(".json") and f"{os.sep}bundles{os.sep}" in str(dst):
                    calls["n"] += 1
                    if calls["n"] == 1:  # 发布第一步（写版本文件）「写到一半」失败
                        raise OSError("模拟写盘失败")
                return real_replace(src, dst, *a, **kw)

            with mock.patch("os.replace", side_effect=flaky_replace):
                code, out = repo.run_entry(make_opener(script))
            self.assertEqual(code, 4, out)
            self.assertGreaterEqual(calls["n"], 1)
            self.assertEqual(json.loads(repo.public_bundle().read_text(encoding="utf-8")), before)
            self.assertEqual(json.loads((repo.workspace() / "manifest.json").read_text(encoding="utf-8"))["run_id"],
                             manifest_before["run_id"])
            self._assert_no_temp(repo)
        finally:
            repo.cleanup()

    def _assert_no_temp(self, repo):
        leftovers = [p for p in repo.root.rglob("*.tmp")]
        self.assertEqual(leftovers, [], f"残留临时文件：{leftovers}")


class C05SingleInstance(unittest.TestCase):
    """反例 5：两个刷新进程并发 → 第二个拿不到锁直接退出，不串档。"""

    def test_second_process_exits_without_touching_data(self):
        repo = TempRepo()
        try:
            script = {h: [payload([raw_tweet(h, sid_for(BASE - timedelta(hours=2)), views=90_000, likes=400)])]
                      for h in (f"vip{i:03d}" for i in range(1, 52))}
            code, out = repo.run_entry(make_opener(script))
            self.assertEqual(code, 0, out)
            bundle_before = repo.public_bundle().read_bytes()

            holder = subprocess.Popen(
                [sys.executable, "-c",
                 "import sys; sys.path.insert(0, %r); import time, radar_store as s; "
                 "lk = s.SingleInstanceLock(s.lock_path(s.workspace_root(%r))).acquire(); time.sleep(8)"
                 % (str(REPO / "scripts"), str(repo.root))],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            try:
                # 等锁真正被持有
                deadline = datetime.now(timezone.utc) + timedelta(seconds=10)
                while datetime.now(timezone.utc) < deadline:
                    probe = radar_store.SingleInstanceLock(radar_store.lock_path(repo.workspace()))
                    try:
                        probe.acquire()
                    except radar_store.LockHeld:
                        break
                    finally:
                        probe.release()
                    import time
                    time.sleep(0.2)
                else:
                    self.fail("子进程未持有锁")
                code, out = repo.run_entry(make_opener(script))
                self.assertEqual(code, 3, out)
                self.assertIn("跳过本轮", out)
                self.assertEqual(repo.public_bundle().read_bytes(), bundle_before)
            finally:
                holder.terminate()
                holder.wait(timeout=10)
                holder.stdout.close()
                holder.stderr.close()
        finally:
            repo.cleanup()


class C06Followers(unittest.TestCase):
    """反例 6：帖内实时粉丝优先；未知不进低粉；servasyy_ai 无硬编码也正确。"""

    def test_live_author_followers_wins_and_unknown_excluded(self):
        live_id = sid_for(BASE - timedelta(hours=2))
        unknown_id = sid_for(BASE - timedelta(hours=2))
        run = normalize_run(
            {
                "richguy": [raw_tweet("richguy", live_id, views=60_000, likes=500, followers=40_000)],
                "mystery": [raw_tweet("mystery", unknown_id, views=60_000, likes=500, include_followers=False)],
            },
            BASE,
        )
        targets = [{"handle": "richguy", "name": "richguy"}, {"handle": "mystery", "name": "mystery"}]
        bundle = radar_bundle.build_bundle(run, None, targets, now=BASE)
        states = {b["handle"]: b for b in bundle["vip_bloggers"]}
        self.assertEqual(states["richguy"]["fc"], 40_000)
        self.assertEqual(states["richguy"]["followers_source"], "live_author")
        self.assertIsNotNone(states["richguy"]["followers_fetched_at"])
        self.assertIsNone(states["mystery"]["fc"], "拿不到粉丝必须是未知，不是 0")
        self.assertEqual(states["mystery"]["followers_source"], "unknown")
        lowfan_handles = {t["user"] for t in bundle["tweets"]}
        self.assertNotIn("mystery", lowfan_handles, "粉丝未知不得进低粉榜")
        self.assertNotIn("richguy", lowfan_handles)
        self.assertTrue(all(t["fc"] is not None and t["fc"] <= rc.LOW_FAN_THRESHOLD for t in bundle["tweets"]))

    def test_follow_cache_value_is_not_used(self):
        """关注缓存 1,000 粉、帖内 40,000 → 用 40,000，不进低粉。"""
        tid = sid_for(BASE - timedelta(hours=2))
        run = normalize_run({"poorlooking": [raw_tweet("poorlooking", tid, views=30_000, likes=200, followers=40_000)]}, BASE)
        run["accounts"]["poorlooking"]["follow_cache"] = 1_000  # 旧口径的缓存值不参与取值
        bundle = radar_bundle.build_bundle(run, None, [{"handle": "poorlooking", "name": "x"}], now=BASE)
        self.assertEqual({b["fc"] for b in bundle["vip_bloggers"]}, {40_000})
        self.assertEqual(bundle["tweets"], [])

    def test_servasyy_has_no_hardcoded_floor(self):
        tid = sid_for(BASE - timedelta(hours=2))
        run = normalize_run({"servasyy_ai": [raw_tweet("servasyy_ai", tid, views=150_000, likes=1_300, followers=41_234)]}, BASE)
        bundle = radar_bundle.build_bundle(run, None, [{"handle": "servasyy_ai", "name": "黄总"}], now=BASE)
        self.assertEqual([b["fc"] for b in bundle["vip_bloggers"]], [41_234])
        self.assertEqual(bundle["tweets"], [], "黄总约 4.1 万粉不进低粉榜")
        for name in ("radar_bundle.py", "radar_common.py", "radar_collect.py", "radar_client.py", "radar_store.py"):
            source = (REPO / "scripts" / name).read_text(encoding="utf-8")
            self.assertNotIn("servasyy", source, f"{name} 里不应出现账号硬编码")
            self.assertNotIn("41214", source, f"{name} 里不应残留粉丝下限")


class C07NoPrefixDedupe(unittest.TestCase):
    """反例 7：同作者两条不同 ID、前 40 字相同 → 两条都保留，指标不串。"""

    def test_same_prefix_different_ids_both_kept(self):
        same = "AI 视频生成流水线实测：一句话出片，成本砍掉七成，附完整参数与耗时对比表"
        a, b = sid_for(BASE - timedelta(hours=2)), sid_for(BASE - timedelta(hours=3))
        run = normalize_run({"dupes": [
            raw_tweet("dupes", a, text=same, views=90_000, likes=900, followers=8_000),
            raw_tweet("dupes", b, text=same + "（补充：换了模型）", views=12_000, likes=120, followers=8_000),
        ]}, BASE)
        bundle = radar_bundle.build_bundle(run, None, [{"handle": "dupes", "name": "dupes"}], now=BASE)
        ids = {t["id"] for t in bundle["vip_tweets"]}
        self.assertEqual(ids, {a, b})
        by_id = {t["id"]: t for t in bundle["vip_tweets"]}
        self.assertEqual(by_id[a]["views"], 90_000)
        self.assertEqual(by_id[b]["views"], 12_000)
        self.assertNotEqual(by_id[a]["likes"], by_id[b]["likes"])


class C08Retweet(unittest.TestCase):
    """反例 8：RT @original: ... → 标为转推，不作为查询账号原创进榜。"""

    def test_retweet_not_treated_as_original(self):
        rt_id = sid_for(BASE - timedelta(hours=2))
        raw = raw_tweet("queried", rt_id, text="RT @someone_else: AI 模型评测真香", views=500_000, likes=9_000)
        raw["retweeted_tweet"] = {"id": "1234567890123456789"}
        run = normalize_run({"queried": [raw]}, BASE)
        self.assertEqual(run["tweets"][0]["kind"], "retweet")
        self.assertEqual(run["tweets"][0]["referenced_tweet_id"], "1234567890123456789")
        bundle = radar_bundle.build_bundle(run, None, [{"handle": "queried", "name": "q"}], now=BASE)
        self.assertEqual(bundle["vip_tweets"], [])
        self.assertEqual(bundle["tweets"], [])
        self.assertEqual(bundle["meta"]["filtered_out"]["kind"], 1)

    def test_quote_is_kept(self):
        qid = sid_for(BASE - timedelta(hours=2))
        raw = raw_tweet("queried", qid, text="AI 出海订阅实测：Stripe 被封号后我怎么收款", views=60_000, likes=400)
        raw["quoted_tweet"] = {"id": "2222222222222222222"}
        run = normalize_run({"queried": [raw]}, BASE)
        self.assertEqual(run["tweets"][0]["kind"], "quote")
        bundle = radar_bundle.build_bundle(run, None, [{"handle": "queried", "name": "q"}], now=BASE)
        self.assertEqual([t["id"] for t in bundle["vip_tweets"]], [qid])
        self.assertEqual(bundle["vip_tweets"][0]["referenced_tweet_id"], "2222222222222222222")


class C09TimeWindow(unittest.TestCase):
    """反例 9：47.95h 仍在 48h 窗口；24.04h 不算今日；未来时间不算新帖。"""

    def test_window_boundaries(self):
        self.assertTrue(rc.in_window(BASE - timedelta(hours=47, minutes=57), BASE, 48))
        self.assertFalse(rc.in_window(BASE - timedelta(hours=48, minutes=3), BASE, 48))
        self.assertFalse(rc.in_window(None, BASE, 48))
        self.assertFalse(rc.in_window(BASE + timedelta(minutes=1), BASE, 48))

    def test_bundle_windows(self):
        t4795 = sid_for(BASE - timedelta(hours=47, minutes=57))
        t2404 = sid_for(BASE - timedelta(hours=24, minutes=2, seconds=24))
        future = sid_for(BASE + timedelta(minutes=30))
        run = normalize_run({"acct": [
            raw_tweet("acct", t4795, views=80_000, likes=500, followers=50_000),
            raw_tweet("acct", t2404, views=70_000, likes=400, followers=50_000),
            raw_tweet("acct", future, views=99_000, likes=900, followers=50_000),
        ]}, BASE)
        bundle = radar_bundle.build_bundle(run, None, [{"handle": "acct", "name": "a"}], now=BASE)
        ids = {t["id"] for t in bundle["vip_tweets"]}
        self.assertIn(t4795, ids, "47.95h 的帖必须在 48h 窗口内")
        self.assertIn(t2404, ids)
        self.assertNotIn(future, ids, "未来时间不算新帖")
        by_id = {t["id"]: t for t in bundle["vip_tweets"]}
        self.assertFalse(by_id[t2404]["is_today"], "24.04h 不算今日")
        self.assertTrue(by_id[t4795]["is_today"] is False)
        self.assertEqual(bundle["today_virals"], 0, "两条都超过 24h，今日爆款必须是 0")
        self.assertEqual(bundle["h48_virals"], 2)


class C10MissingMetrics(unittest.TestCase):
    """反例 10：views 缺失 → 记 null，不当 0 参与统计。"""

    def test_missing_views_is_none(self):
        tid = sid_for(BASE - timedelta(hours=2))
        raw = raw_tweet("acct", tid, views=None, likes=400, include_views=False, followers=9_000)
        run = normalize_run({"acct": [raw]}, BASE)
        self.assertIsNone(run["tweets"][0]["views"])
        self.assertIsNone(run["tweets"][0]["replies"])
        bundle = radar_bundle.build_bundle(run, None, [{"handle": "acct", "name": "a"}], now=BASE)
        self.assertEqual(len(bundle["vip_tweets"]), 1)
        item = bundle["vip_tweets"][0]
        self.assertIsNone(item["views"])
        self.assertIsNone(item["ratio"])
        self.assertIn("未知", item["viral_reason"])
        self.assertNotIn("浏览 0", item["viral_reason"])
        self.assertEqual(bundle["today_virals"], 0)
        self.assertEqual(bundle["h48_virals"], 0)
        self.assertEqual(item["tier_class"], "tier-unknown")
        self.assertTrue(rc.format_int(None) == "未知")


class C11Pagination(unittest.TestCase):
    """反例 11：第 20 条仍在 24h 内 → 翻第 2 页且最多 2 页；调用数正确计入。"""

    def _script(self, handle, *, fresh_20th: bool, has_next=True, pages=3):
        def page(idx):
            tweets = []
            for i in range(20):
                age = (timedelta(hours=1) + timedelta(minutes=idx * 30 + i)) if fresh_20th else timedelta(hours=30, minutes=i)
                tweets.append(raw_tweet(handle, sid_for(BASE - age), views=1000 + i))
            return payload(tweets, has_next=has_next, cursor=f"C{idx}")

        return {handle: [page(i + 1) for i in range(pages)]}

    def test_pages_two_max_and_call_count(self):
        handle = "vip001"
        opener = make_opener(self._script(handle, fresh_20th=True, pages=3))
        res = fetch_account(handle, FAKE_KEY, opener=opener, base_time=BASE)
        self.assertEqual(res["status"], "ok")
        self.assertEqual(res["calls"], 2)
        self.assertEqual(res["pages"], 2)
        self.assertTrue(res["paginated"])
        self.assertEqual(len(res["tweets"]), 40)

    def test_no_second_page_when_20th_is_old(self):
        handle = "vip001"
        opener = make_opener(self._script(handle, fresh_20th=False))
        res = fetch_account(handle, FAKE_KEY, opener=opener, base_time=BASE)
        self.assertEqual(res["calls"], 1)
        self.assertEqual(res["pages"], 1)
        self.assertFalse(res["paginated"])

    def test_run_counts_calls(self):
        script = self._script("vip001", fresh_20th=True, pages=3)
        script["vip002"] = [payload([raw_tweet("vip002", sid_for(BASE - timedelta(hours=1)))])]
        opener = make_opener(script)
        run = radar_collect.collect(api_key=FAKE_KEY, targets=targets_fixture(2), run_id="r2", base_time=BASE,
                                   fetch=lambda h, k, **kw: fetch_account(h, k, opener=opener, **kw))
        self.assertEqual(run["api_calls"], 3)
        self.assertEqual(run["pages_fetched"], 3)
        self.assertEqual(run["accounts"]["vip001"]["calls"], 2)
        self.assertEqual(run["accounts"]["vip002"]["calls"], 1)


class C12SnapshotRetention(unittest.TestCase):
    """反例 12：31 天前的快照被删，30 天内的保留，目录外／不符命名的文件不动。"""

    def test_prune_only_touches_matching_snapshots(self):
        repo = TempRepo()
        try:
            root = repo.workspace()
            snap = root / "snapshots"
            old_day = (BASE - timedelta(days=31)).strftime("%Y-%m-%d")
            edge_day = (BASE - timedelta(days=30)).strftime("%Y-%m-%d")
            today = BASE.strftime("%Y-%m-%d")
            made = {}
            for day, name in ((old_day, "20260914-010101-aaaaaaaa.json"),
                              (edge_day, "20261015-010101-bbbbbbbb.json"),
                              (today, "20261015-020202-cccccccc.json")):
                path = snap / day / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("{}", encoding="utf-8")
                made[(day, name)] = path
            odd = snap / "notes.txt"
            odd.write_text("keep me", encoding="utf-8")
            odd_json = snap / "20260914-010101-notavalidhex!.json"
            odd_json.write_text("{}", encoding="utf-8")
            other_dir = snap / "scratch" / "20260914-010101-aaaaaaaa.json"
            other_dir.parent.mkdir(parents=True, exist_ok=True)
            other_dir.write_text("{}", encoding="utf-8")
            outside = root / "bundles" / "20260914-010101-aaaaaaaa.json"
            outside.parent.mkdir(parents=True, exist_ok=True)
            outside.write_text("{}", encoding="utf-8")

            result = radar_store.prune_snapshots(root, BASE, keep_days=30)
            self.assertEqual(result["deleted"], [f"{old_day}/20260914-010101-aaaaaaaa.json"])
            self.assertFalse(made[(old_day, "20260914-010101-aaaaaaaa.json")].exists())
            self.assertTrue(made[(edge_day, "20261015-010101-bbbbbbbb.json")].exists())
            self.assertTrue(made[(today, "20261015-020202-cccccccc.json")].exists())
            self.assertEqual(odd.read_text(encoding="utf-8"), "keep me")
            self.assertTrue(odd_json.exists())
            self.assertTrue(other_dir.exists())
            self.assertTrue(outside.exists())
            self.assertEqual(result["kept"], 2)
        finally:
            repo.cleanup()


class C13RetiredScripts(unittest.TestCase):
    """反例 13：旧脚本运行 → 非 0 退出，不写任何文件。"""

    NAMES = ["batch_scan_150_followings.py", "deep_scan_batch_2.py", "sync_all_radar_live.py", "build_fresh_radar_bundle.py"]

    def test_retired_scripts_exit_nonzero_and_write_nothing(self):
        repo = TempRepo()
        legacy_tmp = Path("/tmp/fresh_oct14_tweets.json")
        before = legacy_tmp.stat().st_mtime_ns if legacy_tmp.exists() else None
        try:
            for name in self.NAMES:
                script = REPO / "scripts" / name
                self.assertTrue(script.exists(), f"缺少 {name}")
                proc = subprocess.run([sys.executable, str(script)], cwd=str(repo.root),
                                      capture_output=True, text=True, timeout=30)
                self.assertNotEqual(proc.returncode, 0, f"{name} 必须非 0 退出：{proc.stdout}")
                self.assertIn("已停用", proc.stdout)
                self.assertIn("refresh_twitter_radar.py", proc.stdout)
            created = sorted(str(p.relative_to(repo.root)) for p in repo.root.rglob("*") if p.is_file())
            self.assertEqual([c for c in created if c.startswith(("public/", "workspace/"))], [],
                             "停用脚本不得写任何数据文件")
            after = legacy_tmp.stat().st_mtime_ns if legacy_tmp.exists() else None
            self.assertEqual(after, before, "停用脚本不得写 /tmp/fresh_oct14_tweets.json")
        finally:
            repo.cleanup()


class C14NoFabricatedTopics(unittest.TestCase):
    """反例 14：全部空数据 → 不产出带具体曝光数字的话题摘要。"""

    def test_empty_round_has_no_numbered_summaries(self):
        repo = TempRepo()
        try:
            script = {h: [payload([])] for h in (f"vip{i:03d}" for i in range(1, 52))}
            code, out = repo.run_entry(make_opener(script))
            self.assertEqual(code, 0, out)
            self.assertIn("空", out)
            bundle = json.loads(repo.public_bundle().read_text(encoding="utf-8"))
            self.assertEqual(bundle["topic_clusters"], [])
            self.assertEqual(bundle["vip_tweets"], [])
            self.assertEqual(bundle["tweets"], [])
            self.assertEqual(bundle["today_virals"], 0, "零值必须如实输出 0")
            self.assertEqual(bundle["h48_virals"], 0)
            self.assertEqual(bundle["coverage"]["empty"], 51)
            text = json.dumps(bundle, ensure_ascii=False)
            self.assertNotIn("万曝光", text)
            for stale in ("26 万", "233", "13.9 万"):
                self.assertNotIn(stale, text)
        finally:
            repo.cleanup()

    def test_topic_summaries_list_supporting_ids(self):
        tid = sid_for(BASE - timedelta(hours=2))
        run = normalize_run({"acct": [raw_tweet("acct", tid, views=88_000, likes=700, followers=9_000)]}, BASE)
        bundle = radar_bundle.build_bundle(run, None, [{"handle": "acct", "name": "a"}], now=BASE)
        clusters = bundle["topic_clusters"]
        self.assertTrue(clusters)
        for cluster in clusters:
            self.assertEqual(cluster["supporting_tweet_ids"], [tid])
            self.assertEqual([e["id"] for e in cluster["embedded_tweets"]], [tid])
            self.assertIsNotNone(cluster["generated_at"])
            self.assertIn("88,000", cluster["burst_summary"])
        self.assertTrue(rc.topic_summaries([], BASE) == [])


class C15CredentialHygiene(unittest.TestCase):
    """反例 15：交付物、日志、快照、报告中不出现该 Key 前缀字面量。"""

    def test_no_key_in_artifacts_or_logs(self):
        repo = TempRepo()
        try:
            script = {h: [payload([raw_tweet(h, sid_for(BASE - timedelta(hours=2)), views=90_000, likes=400)])]
                      for h in (f"vip{i:03d}" for i in range(1, 52))}
            script["vip050"] = [ConnectionError(f"authorization failed for {FAKE_KEY}")]
            code, out = repo.run_entry(make_opener(script), extra=["--summary-json", str(repo.root / "summary.json")])
            self.assertIn(code, (0, 1), out)
            targets = [repo.public_bundle(), repo.workspace() / "manifest.json", repo.root / "summary.json", *sorted((repo.workspace() / "snapshots").rglob("*.json"))]
            self.assertTrue(targets)
            needle = "sk" + "-aisa"  # 不在本文件里留下该字面量
            for path in targets:
                self.assertNotIn(needle, path.read_text(encoding="utf-8"), f"{path} 泄漏凭证")
            self.assertNotIn(needle, out, "日志泄漏凭证")
            self.assertNotIn(FAKE_KEY, out)
            for path in sorted((REPO / "scripts").glob("radar_*.py")) + [REPO / "scripts" / "refresh_twitter_radar.py",
                        Path(__file__), REPO / "scripts" / "radar_targets.json"]:
                self.assertNotIn(needle, path.read_text(encoding="utf-8"), f"{path.name} 泄漏凭证")
        finally:
            repo.cleanup()


# ------------------------------------------------------------------ 补充：名单与 run 记录

class TargetsAndRunRecord(unittest.TestCase):
    def test_targets_file_is_the_single_source(self):
        targets = rc.load_targets(REPO / "scripts" / "radar_targets.json")
        self.assertEqual(len(targets), 51)
        handles = {rc.normalize_handle(t["handle"]) for t in targets}
        self.assertEqual(len(handles), 51)
        for required in ("gosailglobal", "hitw93", "jinchenma_ai", "lianyanshe", "lifesinger",
                         "ooeli_eth", "shitunote", "xiaomovps"):
            self.assertIn(required, handles, f"复核列出的漏扫账号必须在名单里：{required}")

    def test_targets_file_rejects_mismatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "t.json"
            path.write_text(json.dumps({"expected_count": 3, "targets": [{"handle": "a"}, {"handle": "a"}]}), encoding="utf-8")
            with self.assertRaises(ValueError):
                rc.load_targets(path)

    def test_coverage_denominator_is_the_target_list(self):
        """分母固定是名单数量；非名单作者的帖子不扩大名单、不进榜。"""
        outsider = sid_for(BASE - timedelta(hours=1))
        script = {"vip001": [payload([raw_tweet("outsider_guy", outsider, views=999_999, likes=9_999)])]}
        for handle in (f"vip{i:03d}" for i in range(2, 52)):
            script[handle] = [{"code": 401, "message": "unauthorized"}]
        opener = make_opener(script)
        run = radar_collect.collect(api_key=FAKE_KEY, targets=targets_fixture(51), run_id="r3", base_time=BASE,
                                   fetch=lambda h, k, **kw: fetch_account(h, k, opener=opener, **kw))
        self.assertEqual(run["target_count"], 51)
        self.assertEqual(run["success"], 1)
        self.assertEqual(run["failed"], 50)
        self.assertAlmostEqual(run["coverage"], round(1 / 51, 4))
        self.assertFalse(run["complete"])
        self.assertNotIn("outsider_guy", run["accounts"])

        bundle = radar_bundle.build_bundle(run, None, targets_fixture(51), now=BASE)
        self.assertEqual(len(bundle["vip_bloggers"]), 51)
        self.assertEqual(bundle["coverage"]["target"], 51, "页面按 coverage.target 读固定分母")
        self.assertEqual(bundle["coverage"]["success"], 1)
        self.assertEqual(bundle["coverage"]["failed"], 50)
        self.assertFalse(bundle["coverage"]["complete"])
        self.assertNotIn("outsider_guy", {b["handle"] for b in bundle["vip_bloggers"]})
        self.assertNotIn(outsider, {t["id"] for t in bundle["vip_tweets"]})

    def test_run_record_fields_and_snapshot_naming(self):
        repo = TempRepo()
        try:
            script = {h: [payload([raw_tweet(h, sid_for(BASE - timedelta(hours=2)), views=90_000, likes=400)])]
                      for h in (f"vip{i:03d}" for i in range(1, 52))}
            code, out = repo.run_entry(make_opener(script))
            self.assertEqual(code, 0, out)
            snaps = sorted((repo.workspace() / "snapshots").rglob("*.json"))
            self.assertEqual(len(snaps), 1)
            day_dir = snaps[-0].parent.name
            self.assertRegex(day_dir, r"^\d{4}-\d{2}-\d{2}$")
            self.assertRegex(snaps[0].name, r"^\d{8}-\d{6}-[0-9a-f]{8}\.json$")
            run = json.loads(snaps[0].read_text(encoding="utf-8"))
            for field in ("schema_version", "run_id", "started_at", "finished_at", "base_time", "target_count",
                          "attempted", "success", "failed", "api_calls", "pages_fetched", "coverage", "complete",
                          "deadline_seconds", "accounts", "tweets"):
                self.assertIn(field, run)
            self.assertEqual(run["target_count"], 51)
            self.assertEqual(run["api_calls"], 51)
            account = run["accounts"]["vip001"]
            for field in ("status", "pages", "calls", "fetched_at", "followers", "followers_source",
                          "followers_fetched_at"):
                self.assertIn(field, account)
            for tweet in run["tweets"]:
                for field in ("tweet_id", "author_handle", "author_user_id", "kind", "referenced_tweet_id",
                              "published_at", "views", "likes", "retweets", "replies", "fetched_at"):
                    self.assertIn(field, tweet)

            manifest = json.loads((repo.workspace() / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["run_id"], run["run_id"])
            self.assertEqual(manifest["bundle_sha256"], radar_store.sha256_bytes(repo.public_bundle().read_bytes()))
            bundle = json.loads(repo.public_bundle().read_text(encoding="utf-8"))
            for field in ("tweets", "vip_tweets", "vip_bloggers", "topic_clusters", "updated_at",
                          "today_virals", "h48_virals", "run_id", "coverage"):
                self.assertIn(field, bundle)
            self.assertEqual(len(bundle["vip_bloggers"]), 51)
            self.assertEqual(bundle["coverage"]["target"], 51)
            self.assertEqual(bundle["coverage"]["target_count"], 51)
            self.assertTrue(all(t["fetched_at"] for t in bundle["vip_tweets"]))
        finally:
            repo.cleanup()

    def test_deadline_stops_the_round(self):
        """整轮绝对截止时间：超时未跑的账号记 deadline，不无限等。"""
        import time as _time

        def slow_fetch(handle, api_key, **kw):
            if handle.endswith(("001", "002", "003", "004", "005")):
                _time.sleep(0.4)
            return {"handle": handle, "handle_normalized": rc.normalize_handle(handle), "status": "ok",
                    "tweets": [], "pages": 1, "calls": 1, "http_status": 200, "error": None,
                    "fetched_at": BASE, "page_errors": [], "paginated": False}

        run = radar_collect.collect(api_key=FAKE_KEY, targets=targets_fixture(51), run_id="r4", base_time=BASE,
                                   fetch=slow_fetch, deadline_seconds=0.05, workers=1)
        self.assertTrue(run["deadline_hit"])
        self.assertLess(run["success"], 51)
        self.assertEqual(run["failed"], 51 - run["success"])
        deadline_accounts = [a for a in run["accounts"].values()
                             if a["status"] in ("deadline_inflight", "not_started")]
        self.assertTrue(deadline_accounts)
        self.assertTrue(any(a["status"] == "deadline_inflight" for a in deadline_accounts))
        self.assertTrue(any(a["status"] == "not_started" for a in deadline_accounts))
        self.assertTrue(all(not a["ok"] for a in deadline_accounts))
        self.assertEqual(run["failed"], len(deadline_accounts))
        self.assertEqual(run["api_calls"], 1)
        self.assertEqual(run["attempted"], 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
