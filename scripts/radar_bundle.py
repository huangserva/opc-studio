#!/usr/bin/env python3
"""从快照生成派生 bundle（M2／M6／M8／L1／M7）。

- 输入是本轮 run 记录＋（可选）上一轮快照；输出 public/twitter-radar-bundle.json 的内容。
- 帖子一律按原始 tweet_id 一对一；失败账号的旧帖保留它自己的 fetched_at 并显式 is_stale=true。
- 判窗用未舍入 published_at 与本轮统一时间基准；未知／未来时间不算新帖。
- 粉丝数取本轮该号帖子里的实时 author.followers；未知不参与低粉榜；没有任何账号硬编码。
- 话题摘要只由当前证据生成；没有证据就不输出带数字的摘要。
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta

from radar_common import (
    SCHEMA_VERSION, TRACK_LABELS, classify_track, content_filter, display_age_hours,
    format_int, in_window, iso_utc, low_fan_eligible, normalize_handle, parse_utc,
    topic_summaries,
)

VIP_VIEWS = 15_000
VIP_LIKES = 100
LOWFAN_VIEWS = 5_000
LOWFAN_LIKES = 30
VIRAL_VIEWS = 50_000
VIRAL_LIKES = 300
WINDOW_48H = 48.0
WINDOW_24H = 24.0
LOWFAN_LIMIT = 120


def _meets(value, threshold) -> bool:
    return value is not None and value >= threshold


def _target_index(targets: list[dict]) -> dict[str, dict]:
    return {normalize_handle(t["handle"]): t for t in targets if t.get("handle")}


def account_states(run: dict, previous: dict | None, targets: list[dict]) -> dict[str, dict]:
    """账号级状态：本轮成功的用本轮实时值；失败账号保留上一轮的值并显式标旧。"""
    states: dict[str, dict] = {}
    prev_accounts = (previous or {}).get("accounts") or {}
    prev_run_id = (previous or {}).get("run_id")
    cur_accounts = run.get("accounts") or {}
    for key, target in _target_index(targets).items():
        cur = cur_accounts.get(key) or {}
        if cur.get("ok"):
            states[key] = {
                "handle": target["handle"],
                "name": target.get("name") or target["handle"],
                "status": cur.get("status"),
                "followers": cur.get("followers"),
                "followers_source": cur.get("followers_source") or "unknown",
                "followers_fetched_at": cur.get("followers_fetched_at"),
                "is_stale": False,
                "sampled_run_id": run.get("run_id"),
                "fetched_at": cur.get("fetched_at"),
            }
            continue
        prev = prev_accounts.get(key) or {}
        states[key] = {
            "handle": target["handle"],
            "name": target.get("name") or target["handle"],
            "status": cur.get("status") or "not_sampled",
            "followers": prev.get("followers"),
            "followers_source": "previous_run" if prev.get("followers") is not None else "unknown",
            "followers_fetched_at": prev.get("followers_fetched_at"),
            "is_stale": True,
            "sampled_run_id": prev_run_id,
            "fetched_at": prev.get("fetched_at"),
        }
    return states


def merge_tweets(run: dict, previous: dict | None, states: dict[str, dict]) -> list[dict]:
    """本轮采样优先；失败账号的旧帖按原 fetched_at 保留并标 is_stale。严格按 tweet_id 去重。"""
    merged: dict[str, dict] = {}
    target_keys = set(states)
    for rec in run.get("tweets") or []:
        key = rec.get("queried_handle") or rec.get("author_handle_normalized")
        if key not in target_keys:
            continue
        item = dict(rec)
        item["is_stale"] = False
        item["sampled_run_id"] = run.get("run_id")
        merged[item["tweet_id"]] = item
    if previous:
        for rec in previous.get("tweets") or []:
            key = rec.get("queried_handle") or rec.get("author_handle_normalized")
            tid = str(rec.get("tweet_id") or "")
            if not tid or tid in merged or key not in target_keys:
                continue
            state = states.get(key) or {}
            if not state.get("is_stale"):
                continue  # 本轮成功采到的账号，旧帖不再回灌
            item = dict(rec)
            item["is_stale"] = True
            item["sampled_run_id"] = state.get("sampled_run_id")
            merged[tid] = item
    return sorted(merged.values(), key=lambda r: str(r.get("published_at") or ""), reverse=True)


def bundle_view(rec: dict, state: dict, base: datetime) -> dict:
    views = rec.get("views")
    likes = rec.get("likes")
    followers = state.get("followers")
    ratio = round(views / followers, 2) if (views is not None and followers) else None
    pub = parse_utc(rec.get("published_at"))
    age_display = display_age_hours(pub, base)
    if views is None:
        tier_badge, tier_class = "🔎 浏览量未知", "tier-unknown"
    elif views >= 200_000:
        tier_badge, tier_class = "👑 200K+ 现象级特爆", "tier-200k"
    elif views >= 100_000:
        tier_badge, tier_class = "★★ 100K+ 行业大爆", "tier-100k"
    elif views >= 50_000:
        tier_badge, tier_class = "★ 50K+ 标准爆款", "tier-50k"
    else:
        tier_badge, tier_class = "🚀 10K+ 高潜黑马", "tier-10k"
    return {
        "id": rec["tweet_id"],
        "tweet_id": rec["tweet_id"],
        "user": rec.get("author_handle") or state.get("handle"),
        "user_id": rec.get("author_user_id"),
        "name": rec.get("author_name") or state.get("name"),
        "fc": followers,
        "followers_source": state.get("followers_source"),
        "followers_fetched_at": state.get("followers_fetched_at"),
        "views": views,
        "likes": likes,
        "retweets": rec.get("retweets"),
        "replies": rec.get("replies"),
        "ratio": ratio,
        "kind": rec.get("kind"),
        "referenced_tweet_id": rec.get("referenced_tweet_id"),
        "age_hours": age_display,
        "is_today": in_window(pub, base, WINDOW_24H),
        "published_at": rec.get("published_at"),
        "tier_badge": tier_badge,
        "tier_class": tier_class,
        "track": classify_track(rec.get("text", "")),
        "track_label": TRACK_LABELS.get(classify_track(rec.get("text", "")), ""),
        "text": rec.get("text") or "",
        "url": rec.get("url"),
        "created_at": rec.get("published_at") or rec.get("raw_created_at") or "",
        "fetched_at": rec.get("fetched_at"),
        "is_stale": bool(rec.get("is_stale")),
        "sampled_run_id": rec.get("sampled_run_id"),
        "viral_reason": (f"粉丝 {format_int(followers)}，发布 {age_display} 小时，"
                         f"浏览 {format_int(views)} · {format_int(likes)} 赞"),
        "plan": f"《核实 @{rec.get('author_handle')} 这条帖子的实操，再决定要不要跟》",
        "recheck": "打开原帖与评论区交叉核验后再立项。",
    }


def build_bundle(run: dict, previous: dict | None, targets: list[dict],
                 now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    base = parse_utc(run.get("base_time")) or now
    states = account_states(run, previous, targets)
    merged = merge_tweets(run, previous, states)

    evidence: list[dict] = []
    filtered_out = {"kind": 0, "window": 0, "content": 0, "unknown_time": 0}
    for rec in merged:
        state_key = rec.get("queried_handle") or rec.get("author_handle_normalized")
        author_key = rec.get("author_handle_normalized")
        # 时间线里其他作者的帖子不能当查询账号的原创（M3）
        if rec.get("kind") not in ("original", "quote") or (author_key and author_key != state_key):
            filtered_out["kind"] += 1
            continue
        pub = parse_utc(rec.get("published_at"))
        if pub is None:
            filtered_out["unknown_time"] += 1
            continue
        if not in_window(pub, base, WINDOW_48H):
            filtered_out["window"] += 1
            continue
        state = states.get(rec.get("queried_handle") or rec.get("author_handle_normalized")) or {}
        ok, _reason = content_filter(rec.get("text", ""), is_target_vip=True)
        if not ok:
            filtered_out["content"] += 1
            continue
        evidence.append(bundle_view(rec, state, base))

    evidence.sort(key=lambda v: (0 if v["is_stale"] else 1, v["views"] or -1, v["likes"] or -1), reverse=True)

    vip_tweets = [v for v in evidence if _meets(v["views"], VIP_VIEWS) or _meets(v["likes"], VIP_LIKES)]
    vip_tweets.sort(key=lambda v: str(v["id"]), reverse=True)

    lowfan = [v for v in evidence
              if low_fan_eligible(v["fc"]) and (_meets(v["views"], LOWFAN_VIEWS) or _meets(v["likes"], LOWFAN_LIKES))]
    lowfan.sort(key=lambda v: (0 if v["is_stale"] else 1, v["views"] or -1), reverse=True)

    today_virals = sum(1 for v in evidence
                       if _meets(v["views"], VIRAL_VIEWS) and _meets(v["likes"], VIRAL_LIKES)
                       and in_window(parse_utc(v["published_at"]), base, WINDOW_24H))
    h48_virals = sum(1 for v in evidence
                     if _meets(v["views"], VIRAL_VIEWS) and _meets(v["likes"], VIRAL_LIKES))

    vip_bloggers = []
    for key, target in _target_index(targets).items():
        state = states[key]
        vip_bloggers.append({
            "handle": target["handle"],
            "name": target.get("name") or target["handle"],
            "fc": state.get("followers"),
            "followers_source": state.get("followers_source"),
            "followers_fetched_at": state.get("followers_fetched_at"),
            "status": state.get("status"),
            "is_stale": bool(state.get("is_stale")),
            "fetched_at": state.get("fetched_at"),
        })

    clusters = topic_summaries(evidence, base)

    coverage = {
        # 页面（A2）按 coverage.target 读固定分母；target_count 保留作为兼容别名。
        "target": run.get("target_count"),
        "run_id": run.get("run_id"),
        "target_count": run.get("target_count"),
        "attempted": run.get("attempted"),
        "success": run.get("success"),
        "failed": run.get("failed"),
        "empty": run.get("empty"),
        "partial": run.get("partial", 0),
        "coverage": run.get("coverage"),
        "complete": run.get("complete"),
        "api_calls": run.get("api_calls"),
        "pages_fetched": run.get("pages_fetched"),
        "deadline_hit": run.get("deadline_hit"),
    }

    bundle = {
        "schema_version": SCHEMA_VERSION,
        "run_id": run.get("run_id"),
        "generated_at": iso_utc(now),
        "collected_at": iso_utc(parse_utc(run.get("finished_at")) or base),
        "base_time": iso_utc(base),
        "updated_at": (parse_utc(run.get("finished_at")) or base).astimezone(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S"),
        "coverage": coverage,
        "topic_clusters": clusters,
        "vip_tweets": vip_tweets,
        "vip_bloggers": vip_bloggers,
        "tweets": lowfan[:LOWFAN_LIMIT],
        "today_virals": today_virals,
        "h48_virals": h48_virals,
        "meta": {
            "sampled_tweets": len(merged),
            "evidence_tweets": len(evidence),
            "filtered_out": filtered_out,
            "stale_tweets": sum(1 for v in evidence if v["is_stale"]),
            "window_hours": WINDOW_48H,
            "previous_run_id": (previous or {}).get("run_id"),
        },
    }
    return bundle
