#!/usr/bin/env python3
"""一轮采集（H1／M7／翻页）：只读 scripts/radar_targets.json 这份名单，产出 run 记录。

- 覆盖率分母固定为名单里的账号数，不按爆款动态追加。
- 每号记录状态、页数、调用次数；整轮有绝对截止时间，区分 deadline_inflight 与 not_started，发起时计数。
- 原始采样（含转推、回复、非 AI 内容）完整保留在快照里，过滤只发生在派生 bundle。
"""
from __future__ import annotations

import secrets
import time
import inspect
import queue
import threading
from datetime import datetime, timezone

from radar_client import DEFAULT_TIMEOUT, MAX_PAGES, fetch_account
from radar_common import (
    SCHEMA_VERSION, STATUS_DEADLINE, STATUS_PARTIAL, STATUS_INFLIGHT, STATUS_NOT_STARTED,
    SUCCESS_STATUSES, iso_utc, normalize_handle,
    pick_live_followers,
)

COVERAGE_THRESHOLD = 0.95
DEFAULT_DEADLINE_SECONDS = 600.0
DEFAULT_WORKERS = 8


def make_run_id(now: datetime) -> str:
    return f"{now.astimezone(timezone.utc).strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(4)}"


def collect(*, api_key: str, targets: list[dict], run_id: str, base_time: datetime | None = None,
            fetch=fetch_account, deadline_seconds: float = DEFAULT_DEADLINE_SECONDS,
            request_timeout: float = DEFAULT_TIMEOUT, max_pages: int = MAX_PAGES,
            workers: int = DEFAULT_WORKERS, clock=time.monotonic, log=print, receipt_dir=None) -> dict:
    base_time = base_time or datetime.now(timezone.utc)
    started_at = base_time
    deadline = clock() + float(deadline_seconds)
    from radar_store import RequestReceipt
    receipt = RequestReceipt(run_id, iso_utc(started_at), directory=receipt_dir, clock=clock)
    targets = [t for t in targets if t.get("handle")]
    target_count = len(targets)

    run = {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "started_at": iso_utc(started_at),
        "finished_at": None,
        "base_time": iso_utc(base_time),
        "target_count": target_count,
        "attempted": 0,
        "success": 0,
        "failed": 0,
        "empty": 0,
        "partial": 0,
        "coverage": 0.0,
        "coverage_threshold": COVERAGE_THRESHOLD,
        "complete": False,
        "api_calls": 0,
        "pages_fetched": 0,
        "deadline_seconds": float(deadline_seconds),
        "deadline_hit": False,
        "accounts": {},
        "tweets": [],
        "kind_counts": {},
    }

    # Daemon workers never hold interpreter exit hostage. Freeze results at the
    # deadline so a late HTTP return cannot mutate the already persisted run.
    results: dict[str, dict] = {}
    condition = threading.Condition()
    pending = queue.Queue()
    progress = {normalize_handle(t["handle"]): {"started": False, "calls": 0} for t in targets}
    for target in targets:
        pending.put(target)
    closed = False
    try:
        signature = inspect.signature(fetch)
        accepts_progress = "on_request" in signature.parameters or any(
            p.kind == p.VAR_KEYWORD for p in signature.parameters.values())
        accepts_attempt = "on_attempt" in signature.parameters or any(
            p.kind == p.VAR_KEYWORD for p in signature.parameters.values())
        accepts_retry_timeout = "retry_timeout" in signature.parameters or any(
            p.kind == p.VAR_KEYWORD for p in signature.parameters.values())
    except (ValueError, TypeError):
        accepts_progress = accepts_attempt = accepts_retry_timeout = False

    reports_requests = (fetch is fetch_account or getattr(fetch, "func", None) is fetch_account
                        or getattr(fetch, "reports_requests", False))

    def worker():
        while True:
            with condition:
                if closed or clock() >= deadline:
                    return
                try:
                    target = pending.get_nowait()
                except queue.Empty:
                    return
                handle = target["handle"]
                key = normalize_handle(handle)
                progress[key]["started"] = True

            def request_started():
                with condition:
                    if closed or clock() >= deadline:
                        raise TimeoutError("整轮截止，未发起新请求")
                    progress[key]["calls"] += 1
                    index = progress[key]["calls"]
                    return receipt.start(handle, index)

            try:
                kwargs = dict(timeout=min(float(request_timeout), max(0.0, deadline - clock())),
                              max_pages=max_pages, deadline=deadline, base_time=base_time, clock=clock)
                if accepts_progress:
                    kwargs["on_request"] = request_started
                if accepts_attempt:
                    kwargs["on_attempt"] = receipt.event
                if accepts_retry_timeout:
                    kwargs["retry_timeout"] = float(request_timeout)
                res = fetch(handle, api_key, **kwargs)
            except Exception as exc:
                from radar_client import sanitize_error
                res = {"handle": handle, "status": "collect_error", "tweets": [],
                       "pages": 0, "calls": progress[key]["calls"], "fetched_at": None,
                       "error": sanitize_error(type(exc).__name__ + ": " + str(exc), api_key)}
            with condition:
                if closed:
                    return
                res["calls"] = max(progress[key]["calls"], int(res.get("calls") or 0))
                if res.get("status") == STATUS_DEADLINE:
                    res["status"] = STATUS_INFLIGHT if res["calls"] else STATUS_NOT_STARTED
                    res["pages_complete"] = False
                results[key] = res
                condition.notify_all()

    for _ in range(max(1, min(int(workers), target_count or 1))):
        threading.Thread(target=worker, daemon=True, name="radar-fetch").start()
    with condition:
        while len(results) < target_count:
            remaining = deadline - clock()
            if remaining <= 0:
                break
            condition.wait(timeout=remaining)
        closed = True
        run["deadline_hit"] = clock() >= deadline or any(
            r.get("status") in (STATUS_INFLIGHT, STATUS_NOT_STARTED) for r in results.values())
        for target in targets:
            handle = target["handle"]
            key = normalize_handle(handle)
            if key in results:
                continue
            item = progress[key]
            # Injected opaque fetch functions represent one request boundary;
            # real fetch_account reports every page through request_started.
            calls = item["calls"] if reports_requests else item["calls"] or int(item["started"])
            results[key] = {
                "handle": handle, "handle_normalized": key,
                "status": STATUS_INFLIGHT if calls else STATUS_NOT_STARTED,
                "tweets": [], "pages": 0, "calls": calls,
                "http_status": None, "error": "整轮截止：请求未完成" if calls else "整轮截止：尚未发起",
                "fetched_at": None, "pages_complete": False,
                "page_errors": [], "paginated": False,
            }
            run["deadline_hit"] = True

    from radar_common import normalize_tweet  # 局部导入避免循环依赖

    all_records = []
    for target in targets:
        key = normalize_handle(target["handle"])
        res = results.get(key) or {
            "handle": target["handle"], "handle_normalized": key, "status": STATUS_DEADLINE,
            "tweets": [], "pages": 0, "calls": 0, "http_status": None, "error": "未执行",
            "fetched_at": None, "page_errors": [], "paginated": False,
        }
        fetched_at = res.get("fetched_at")
        if isinstance(fetched_at, datetime):
            fetched_at_iso = iso_utc(fetched_at)
        else:
            fetched_at_iso = fetched_at
        records = []
        for raw in res.get("tweets") or []:
            rec = normalize_tweet(raw, target["handle"], fetched_at if isinstance(fetched_at, datetime) else None)
            if rec is None:
                continue
            rec["is_stale"] = False
            records.append(rec)
        records.sort(key=lambda r: str(r.get("published_at") or ""), reverse=True)
        followers, followers_source, followers_fetched_at = pick_live_followers(records)

        status = res.get("status") or "unknown"
        ok = status in SUCCESS_STATUSES or status == STATUS_PARTIAL
        run["attempted"] += int(int(res.get("calls") or 0) > 0)
        run["success"] += 1 if ok else 0
        run["failed"] += 0 if ok else 1
        run["empty"] += 1 if status == "empty" else 0
        run["partial"] += int(status == STATUS_PARTIAL)
        run["api_calls"] += int(res.get("calls") or 0)
        run["pages_fetched"] += int(res.get("pages") or 0)

        run["accounts"][key] = {
            "handle": target["handle"],
            "name": target.get("name") or target["handle"],
            "status": status,
            "ok": ok,
            "pages": int(res.get("pages") or 0),
            "calls": int(res.get("calls") or 0),
            "http_status": res.get("http_status"),
            "error": res.get("error"),
            "page_errors": res.get("page_errors") or [],
            "paginated": bool(res.get("paginated")),
            "pages_complete": bool(res.get("pages_complete", status in SUCCESS_STATUSES)),
            "fetched_at": fetched_at_iso,
            "sampled_tweets": len(records),
            "followers": followers,
            "followers_source": followers_source,
            "followers_fetched_at": followers_fetched_at,
        }
        all_records.extend(records)

    seen: set[str] = set()
    unique = []
    for rec in all_records:
        tid = rec["tweet_id"]
        if tid in seen:
            continue
        seen.add(tid)
        unique.append(rec)
    unique.sort(key=lambda r: str(r.get("published_at") or ""), reverse=True)

    run["tweets"] = unique
    counts = {}
    for rec in unique:
        counts[rec["kind"]] = counts.get(rec["kind"], 0) + 1
    run["kind_counts"] = counts
    run["coverage"] = round(run["success"] / target_count, 4) if target_count else 0.0
    run["complete"] = bool(target_count) and run["coverage"] >= COVERAGE_THRESHOLD and not run["deadline_hit"]
    run["finished_at"] = iso_utc(datetime.now(timezone.utc))
    receipt.close(run)
    log(f"[run {run['run_id']}] 成功 {run['success']}/{target_count}（其中空成功 {run['empty']}，覆盖率 {run['coverage'] * 100:.1f}%），"
        f"失败 {run['failed']}，调用 {run['api_calls']} 次，翻页 {sum(1 for a in run['accounts'].values() if a['paginated'])} 个账号，"
        f"采样帖子 {len(unique)} 条，完整={run['complete']}")
    return run
