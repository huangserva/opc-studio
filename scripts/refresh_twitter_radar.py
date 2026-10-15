#!/usr/bin/env python3
"""推特雷达唯一入口：一轮可信采集 → 原始快照留档 → 达标才原子发布派生 bundle。

用法（Key 只从环境读，不放命令行）：
    AISA_API_KEY=... python3 scripts/refresh_twitter_radar.py

退出码：0 已发布；1 本轮不完整（未发布）；2 配置错误；3 已有刷新进程在跑；4 发布失败（提交版本以公开 bundle 为准）。
"""
from __future__ import annotations

import argparse
import json
import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

from radar_bundle import build_bundle                      # noqa: E402
from radar_collect import (                               # noqa: E402
    DEFAULT_DEADLINE_SECONDS, collect, make_run_id,
)
from radar_common import now_utc                          # noqa: E402
from radar_store import (                                 # noqa: E402
    DEFAULT_KEEP_DAYS, LockHeld, SingleInstanceLock, load_previous_snapshot, lock_path,
    prune_snapshots, publish_bundle, read_manifest, snapshot_path, workspace_root, write_json_atomic,
    recover_publication, public_manifest,
)
from radar_client import DEFAULT_TIMEOUT, MAX_PAGES       # noqa: E402

TARGETS_FILE = os.path.join(SCRIPT_DIR, "radar_targets.json")
DEFAULT_REPO_ROOT = os.path.dirname(SCRIPT_DIR)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="刷新推特雷达（唯一入口）")
    ap.add_argument("--repo-root", default=DEFAULT_REPO_ROOT)
    ap.add_argument("--targets", default=TARGETS_FILE, help="唯一账号名单（scripts/radar_targets.json）")
    ap.add_argument("--deadline-seconds", type=float, default=DEFAULT_DEADLINE_SECONDS, help="整轮绝对截止时间")
    ap.add_argument("--request-timeout", type=float, default=DEFAULT_TIMEOUT, help="单次请求超时")
    ap.add_argument("--max-pages", type=int, default=MAX_PAGES, help="每号最多页数（含首页）")
    ap.add_argument("--keep-days", type=int, default=DEFAULT_KEEP_DAYS, help="快照保留天数")
    ap.add_argument("--no-publish", action="store_true", help="只采集与留档，不发布 bundle")
    ap.add_argument("--summary-json", default=None, help="把本轮摘要写到指定路径（验收用）")
    args = ap.parse_args(argv)

    api_key = os.environ.get("AISA_API_KEY", "").strip()
    if not api_key:
        print("缺少 AISA_API_KEY（只从环境变量读取，不放命令行）")
        return 2
    try:
        targets = load_targets_file(args.targets)
    except Exception as exc:
        print(f"名单读取失败：{type(exc).__name__}: {exc}")
        return 2

    root = workspace_root(args.repo_root)
    try:
        lock = SingleInstanceLock(lock_path(root)).acquire()
    except LockHeld as exc:
        print(f"跳过本轮：{exc}")
        return 3

    try:
        recover_publication(root, args.repo_root)
        base = now_utc()
        run_id = make_run_id(base)
        print(f"[{run_id}] 开始采集 {len(targets)} 个特关账号（名单：{os.path.relpath(args.targets, args.repo_root)}）")
        run = collect(api_key=api_key, targets=targets, run_id=run_id, base_time=base,
                      deadline_seconds=args.deadline_seconds, request_timeout=args.request_timeout,
                      max_pages=args.max_pages)

        snap = snapshot_path(root, run_id, run["started_at"])
        write_json_atomic(snap, run)
        print(f"原始快照已完整写盘：{snap}")

        prune = prune_snapshots(root, base, args.keep_days)
        if prune["deleted"]:
            print(f"快照清理：删除 {len(prune['deleted'])} 个超过 {args.keep_days} 天的文件，保留 {prune['kept']} 个")

        if not run["complete"] and run.get("deadline_hit"):
            print("本轮达到整轮截止，失败快照已留档，不发布")
            _write_summary(args.summary_json, run, None)
            return 1

        if args.no_publish:
            print("--no-publish：本轮只留档，不发布 bundle")
            return 0

        if not run["complete"]:
            previous_manifest = public_manifest(root, args.repo_root) or {}
            print(f"本轮不完整：覆盖率 {run['coverage'] * 100:.1f}% < {run['coverage_threshold'] * 100:.0f}%，"
                  f"不发布为新快照；页面继续读上一轮 run_id={previous_manifest.get('run_id', '无')}")
            _write_summary(args.summary_json, run, None)
            return 1

        previous = load_previous_snapshot(root, repo_root=args.repo_root)
        try:
            bundle = build_bundle(run, previous, targets, now=now_utc())
            manifest = publish_bundle(root, args.repo_root, run_id, bundle,
                                     started_at=run["started_at"], coverage=bundle["coverage"])
        except Exception as exc:
            print(f"发布失败：{type(exc).__name__}: {exc}；以公开 bundle 内嵌 manifest 确认已提交版本")
            _write_summary(args.summary_json, run, None)
            return 4
        print(f"已发布 bundle：run_id={run_id} sha256={manifest['bundle_sha256'][:16]}… "
              f"→ {os.path.join('public', 'twitter-radar-bundle.json')}")
        print(f"  话题簇={len(bundle['topic_clusters'])} vip帖={len(bundle['vip_tweets'])} "
              f"低粉帖={len(bundle['tweets'])} 今日爆款={bundle['today_virals']} 48h爆款={bundle['h48_virals']} "
              f"旧值帖={bundle['meta']['stale_tweets']}")
        _write_summary(args.summary_json, run, manifest)
        return 0
    finally:
        lock.release()


def load_targets_file(path: str):
    from radar_common import load_targets
    return load_targets(path)


def _write_summary(path, run, manifest):
    if not path:
        return
    summary = {
        "run_id": run["run_id"],
        "started_at": run["started_at"],
        "finished_at": run["finished_at"],
        "target_count": run["target_count"],
        "attempted": run["attempted"],
        "success": run["success"],
        "failed": run["failed"],
        "empty": run["empty"],
        "partial": run.get("partial", 0),
        "coverage": run["coverage"],
        "complete": run["complete"],
        "api_calls": run["api_calls"],
        "pages_fetched": run["pages_fetched"],
        "deadline_hit": run["deadline_hit"],
        "sampled_tweets": len(run["tweets"]),
        "kind_counts": run["kind_counts"],
        "failed_accounts": {h: a["status"] for h, a in run["accounts"].items() if not a["ok"]},
        "paginated_accounts": [h for h, a in run["accounts"].items() if a["paginated"]],
        "published": bool(manifest),
        "manifest": manifest,
    }
    write_json_atomic(path, summary)


if __name__ == "__main__":
    raise SystemExit(main())
