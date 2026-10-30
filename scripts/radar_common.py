#!/usr/bin/env python3
"""推特雷达（Twitter Radar）采集／发布的公共模块（A1 地基）。

职责：时间口径、字段规范化、账号名单、粉丝数取值、去重与话题摘要的纯函数。
约定：
- 所有时间一律 timezone-aware UTC；内部比较一律用未舍入时间戳，舍入只用于展示。
- 缺失的指标记为 None，绝不默认 0。
- 帖子一律按原始字符串 tweet_id 一对一，不按正文前缀去重。
本模块不联网、不写文件。
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone

SCHEMA_VERSION = "radar-1"
TARGETS_SCHEMA = "radar-targets-1"

# Twitter Snowflake 纪元（毫秒）
EPOCH_MS = 1288834974657

SNAPSHOT_NAME_RE = re.compile(r"^\d{8}-\d{6}-[0-9a-f]{8}\.json$")
SNAPSHOT_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
HANDLE_RE = re.compile(r"^[A-Za-z0-9_]{1,15}$")

# 采样状态（H1）：区分 HTTP 错误／业务错误／空成功／超时／解析失败／未执行
STATUS_OK = "ok"
STATUS_EMPTY = "empty"
STATUS_HTTP = "http_error"
STATUS_BUSINESS = "business_error"
STATUS_TIMEOUT = "timeout"
STATUS_PARSE = "parse_error"
STATUS_DEADLINE = "deadline"
STATUS_PARTIAL = "partial"
STATUS_INFLIGHT = "deadline_inflight"
STATUS_NOT_STARTED = "not_started"
# Fully sampled states; collect separately counts partial page success toward coverage.
SUCCESS_STATUSES = (STATUS_OK, STATUS_EMPTY)

TRACK_LABELS = {
    "claude_code": "💻 Claude Code & 代码革命",
    "ai_models": "🤖 AI 模型与新工具评测",
    "github_open": "⭐ GitHub 爆款开源",
    "money_indie": "💰 AI 商业落地与赚钱实操",
    "overseas_infra": "💳 AI 出海基建与支付订阅",
}

LEGACY_TRACK_MAP = {
    "models_tools": "ai_models",
    "biz_money": "money_indie",
    "global_ship": "overseas_infra",
}

AI_KEYWORDS = [
    "ai", "gpt", "llm", "claude", "agent", "prompt", "cursor", "codex", "mcp",
    "skill", "deepseek", "qwen", "gemini", "openai", "anthropic", "grok",
    "大模型", "模型", "写代码", "编程", "生图", "视频生成", "评测", "推理",
    "蒸馏", "训练", "算力", "显卡", "本地部署", "开源", "github", "repo",
    "独立开发", "出海", "变现", "赚钱", "订阅", "支付", "收款", "工作流",
    "自动化", "部署", "token", "api", "智能体", "多模态", "lora", "4090",
]

NOISE_KEYWORDS = [
    "币", "合约", "打狗", "meme", "韭菜", "量化", "约炮", "轻生", "杀害",
    "大冰", "上厕所", "健身房", "公务员", "破产了", "杯架哥", "西游奇谈",
]

LOW_FAN_THRESHOLD = 10_000


# ---------------------------------------------------------------- 时间（L1）

def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def iso_utc(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return dt.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def parse_utc(value) -> datetime | None:
    """解析 ISO／RFC2822 风格时间字符串为 aware UTC；解析不了返回 None（不猜）。"""
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        dt = value
        return dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc).astimezone(timezone.utc)
    text = str(value).strip()
    if not text:
        return None
    candidates = [text.replace("Z", "+00:00")]
    for fmt in ("%a %b %d %H:%M:%S %z %Y", "%a %b %d %H:%M:%S %Y", "%Y-%m-%d %H:%M:%S%z",
                "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            dt = datetime.strptime(text, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except ValueError:
            continue
    for cand in candidates:
        try:
            dt = datetime.fromisoformat(cand)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except ValueError:
            continue
    return None


def tweet_id_to_utc(tweet_id) -> datetime | None:
    """Snowflake → aware UTC。非数字 ID 返回 None（未知就是未知）。"""
    text = str(tweet_id or "").strip()
    if not text.isdigit():
        return None
    try:
        ms = (int(text) >> 22) + EPOCH_MS
    except (ValueError, OverflowError):
        return None
    if ms <= EPOCH_MS:
        return None
    try:
        return datetime.fromtimestamp(ms / 1000.0, timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None


def published_at_of(raw: dict) -> tuple[datetime | None, str | None]:
    """取帖子的真实发布时间（未舍入）。优先 Snowflake，其次接口时间字符串。"""
    pub = tweet_id_to_utc(raw.get("id"))
    if pub is not None:
        return pub, "snowflake"
    pub = parse_utc(raw.get("createdAt") or raw.get("created_at") or raw.get("createdTime"))
    if pub is not None:
        return pub, "created_at"
    return None, None


def age_hours(pub: datetime | None, base: datetime) -> float | None:
    """未舍入年龄（小时）。未知或未来时间返回 None／负值，由调用方判窗。"""
    if pub is None:
        return None
    return (base - pub).total_seconds() / 3600.0


def in_window(pub: datetime | None, base: datetime, hours: float) -> bool:
    """判窗用未舍入时间基准：窗口就是 hours 小时；未知时间与未来时间不算新帖。"""
    if pub is None or base is None:
        return False
    if pub > base:
        return False
    return (base - pub).total_seconds() <= hours * 3600.0


def display_age_hours(pub: datetime | None, base: datetime) -> float:
    """仅用于展示／排序的舍入年龄；未知给一个很大的哨兵值，不当成最新。"""
    ah = age_hours(pub, base)
    if ah is None or ah < 0:
        return 9_999.0
    return round(ah, 1)


# ---------------------------------------------------------------- 账号与名单

def normalize_handle(handle) -> str:
    return str(handle or "").strip().lstrip("@").lower()


def valid_handle(handle) -> bool:
    return bool(HANDLE_RE.match(normalize_handle(handle)))


def load_targets(path: str) -> list[dict]:
    """读取唯一名单文件 scripts/radar_targets.json。校验失败直接抛错，不静默降级。"""
    with open(path, "r", encoding="utf-8") as f:
        doc = json.load(f)
    targets = doc.get("targets")
    if not isinstance(targets, list) or not targets:
        raise ValueError(f"{path}: targets 列表为空")
    expected = doc.get("expected_count")
    if isinstance(expected, int) and expected != len(targets):
        raise ValueError(f"{path}: 名单 {len(targets)} 个与 expected_count={expected} 不一致")
    seen: set[str] = set()
    out = []
    for item in targets:
        handle = str(item.get("handle", "")).strip()
        key = normalize_handle(handle)
        if not valid_handle(key):
            raise ValueError(f"{path}: 非法 handle {handle!r}")
        if key in seen:
            raise ValueError(f"{path}: 重复 handle {handle!r}")
        seen.add(key)
        out.append({
            "handle": handle,
            "name": item.get("name") or handle,
            "track": item.get("track") or "ai_models",
        })
    return out


# ---------------------------------------------------------------- 字段规范化

def metric(raw: dict, *keys) -> int | None:
    """按候选字段名取整数指标；缺失或不可解析一律 None，绝不默认 0。"""
    for key in keys:
        if key not in raw:
            continue
        value = raw.get(key)
        if value is None or value == "":
            continue
        if isinstance(value, bool):
            continue
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            return int(value)
        text = str(value).replace(",", "").strip()
        if text.isdigit():
            return int(text)
    return None


def classify_kind(raw: dict, text: str = "") -> tuple[str, str | None]:
    """返回 (kind, referenced_tweet_id)：original／retweet／quote／reply。"""
    text = text or str(raw.get("text") or "")
    quoted = raw.get("quoted_tweet") or raw.get("quotedTweet")
    retweeted = raw.get("retweeted_tweet") or raw.get("retweetedStatus")
    reply_id = raw.get("in_reply_to_status_id") or raw.get("inReplyToStatusId") or raw.get("replyToStatusId")
    if retweeted or raw.get("isRetweet") or text.startswith("RT @"):
        ref = None
        if isinstance(retweeted, dict):
            ref = str(retweeted.get("id") or "") or None
        return "retweet", ref
    if quoted or raw.get("isQuote"):
        ref = str(quoted.get("id")) if isinstance(quoted, dict) and quoted.get("id") else None
        return "quote", ref
    if reply_id:
        return "reply", str(reply_id)
    return "original", None


def type_evidence(raw: dict, text: str) -> dict:
    """Only type discriminators: booleans and bounded numeric IDs, never raw objects/text."""
    result = {"rt_prefix": text.startswith("RT @")}
    for key in ("isQuote", "isRetweet"):
        if key in raw:
            result[key] = bool(raw[key])
    for key in ("quoted_tweet", "quotedTweet", "retweeted_tweet", "retweetedStatus"):
        value = raw.get(key)
        if isinstance(value, dict):
            tid = str(value.get("id") or "")
            result[key + "_id"] = tid if tid.isascii() and tid.isdigit() and len(tid) <= 30 else None
    for key in ("in_reply_to_status_id", "inReplyToStatusId", "replyToStatusId"):
        if key in raw:
            tid = str(raw[key] or "")
            result[key] = tid if tid.isascii() and tid.isdigit() and len(tid) <= 30 else None
    return result


def author_of(raw: dict) -> dict:
    author = raw.get("author") or {}
    if not isinstance(author, dict):
        author = {}
    handle = author.get("userName") or author.get("screen_name") or author.get("username") or ""
    user_id = author.get("id") or author.get("id_str") or author.get("userId") or author.get("rest_id")
    return {
        "handle": str(handle).strip().lstrip("@"),
        "user_id": str(user_id) if user_id else None,
        "name": str(author.get("name") or "").strip(),
        "followers": metric(author, "followers", "followers_count", "followersCount"),
    }


def normalize_tweet(raw: dict, queried_handle: str, fetched_at: datetime | None) -> dict | None:
    """把接口返回的一条帖子规范化为快照记录（契约第 2 条的逐帖字段）。"""
    if not isinstance(raw, dict):
        return None
    tweet_id = str(raw.get("id") or "").strip()
    if not tweet_id.isdigit():
        return None
    text = str(raw.get("text") or "").strip()
    author = author_of(raw)
    pub, pub_source = published_at_of(raw)
    kind, ref = classify_kind(raw, text)
    handle = author["handle"] or queried_handle
    return {
        "tweet_id": tweet_id,
        "author_handle": handle,
        "author_handle_observed": bool(author["handle"]),
        "author_handle_normalized": normalize_handle(handle),
        "author_user_id": author["user_id"],
        "author_name": author["name"] or handle,
        "queried_handle": normalize_handle(queried_handle),
        "kind": kind,
        "type_evidence": type_evidence(raw, text),
        "referenced_tweet_id": ref,
        "published_at": iso_utc(pub),
        "published_at_source": pub_source,
        "views": metric(raw, "viewCount", "views", "view_count"),
        "likes": metric(raw, "likeCount", "likes", "like_count"),
        "retweets": metric(raw, "retweetCount", "retweets", "retweet_count"),
        "replies": metric(raw, "replyCount", "replies", "reply_count"),
        "author_followers": author["followers"],
        "text": text,
        "url": str(raw.get("url") or raw.get("twitterUrl") or f"https://x.com/{handle}/status/{tweet_id}"),
        "raw_created_at": raw.get("createdAt") or raw.get("created_at") or "",
        "fetched_at": iso_utc(fetched_at),
    }


def dedupe_by_tweet_id(records: list[dict]) -> dict[str, dict]:
    """严格按原始 tweet_id 一对一去重；正文相似不删帖、不合并不同 ID 的指标。"""
    out: dict[str, dict] = {}
    for rec in records:
        tid = str(rec.get("tweet_id") or "")
        if not tid:
            continue
        out[tid] = rec
    return out


def pick_live_followers(records: list[dict]) -> tuple[int | None, str | None, str | None]:
    """账号级粉丝：只取作者明确等于查询账号且非转推的实时值，按采样时间取最新。

    返回 (followers, source, followers_fetched_at)。拿不到 → (None, 'unknown', None)。
    """
    with_value = [r for r in records if r.get("author_followers") is not None
                  and r.get("author_handle_observed") is not False
                  and r.get("kind") != "retweet"
                  and r.get("author_handle_normalized") == r.get("queried_handle")]
    if not with_value:
        return None, "unknown", None
    with_value.sort(key=lambda r: (str(r.get("fetched_at") or ""), str(r.get("published_at") or "")), reverse=True)
    best = with_value[0]
    return best["author_followers"], "live_author", best.get("fetched_at")


def low_fan_eligible(followers) -> bool:
    """未知粉丝不参与低粉榜；不使用任何单账号硬编码下限。"""
    if followers is None:
        return False
    return followers <= LOW_FAN_THRESHOLD


# ---------------------------------------------------------------- 内容过滤（显示层）

def has_chinese(text: str) -> bool:
    return any("\u4e00" <= c <= "\u9fff" for c in text or "")


def is_ai_related(text: str) -> bool:
    low = (text or "").lower()
    return any(k in low for k in AI_KEYWORDS)


def _today_normalize(text):
    import unicodedata
    value = unicodedata.normalize('NFKC', text).casefold()
    # 只纠正 Claude 这个已知产品名里的常见形近字，不全局把数字/I 换成 l。
    return re.sub(r'(?<![a-z0-9_])c[il1]aude(?=$|[^a-z0-9_]|v?\d)', 'claude', value)


def _today_term_matches(text, term):
    from today_filter_config import TODAY_AI_VERSIONED_TERMS, TODAY_AI_PLURAL_TERMS
    term = _today_normalize(term)
    if not term:
        return False
    # 中文可与英文产品名紧邻；\b 会错误拒绝「Claude设置」「Muse现在」。
    left = r'(?<![a-z0-9_])' if term[:1].isascii() and term[:1].isalnum() else ''
    right = r'(?![a-z0-9_])' if term[-1:].isascii() and term[-1:].isalnum() else ''
    suffix = r'(?:[\s-]*v?\d+(?:\.\d+)*)?' if term in TODAY_AI_VERSIONED_TERMS else 's?' if term in TODAY_AI_PLURAL_TERMS else ''
    return bool(re.search(left + re.escape(term) + suffix + right, text))


def _today_content_text(item, *, preserve_case=False):
    """只遍历已有正文、RT/引用原文、卡片；不使用作者身份、URL 或分析结论。"""
    fields = ('title', 'full_text', 'text', 'expanded_text', 'body', 'content', 'description')
    containers = ('rich_sources', 'quoted', 'quoted_status', 'quoted_tweet', 'retweeted_status',
                  'retweeted_tweet', 'retweet', 'retweeted', 'retweeted_status_result', 'quoted_status_result',
                  'result', 'legacy', 'card', 'link_card', 'url_card', 'link_preview', 'preview', 'article', 'thread')
    pieces, seen = [], set()
    def collect(value, depth=0):
        if depth > 8:
            return
        if isinstance(value, str):
            pieces.append(value)
        elif isinstance(value, (list, dict)):
            if id(value) in seen:
                return
            seen.add(id(value))
            if isinstance(value, list):
                for child in value[:100]:
                    collect(child, depth + 1)
            else:
                for key in (*fields, *containers):
                    if key in value:
                        collect(value[key], depth + 1)
    collect(item)
    topic = item.get('topic') or ''
    if topic not in TRACK_LABELS and topic not in LEGACY_TRACK_MAP:
        pieces.append(topic)
    # 先规范全角URL再剥离；保留原大小写供短词的词内大写规则使用。
    import unicodedata
    raw = unicodedata.normalize('NFKC', '\n'.join(pieces))
    raw = re.sub(r'https?://[^\s<>]+', ' ', raw, flags=re.I)
    return raw if preserve_case else _today_normalize(raw)


def _today_legacy_ai_related(text, original):
    """旧安全词保持子串；短词限独立词、混排驼峰整段或已证实复合词。"""
    from today_filter_config import TODAY_AI_SUBSTRING_TERMS, TODAY_AI_BOUNDARY_TERMS, TODAY_AI_COMPOUNDS
    if any(term in text for term in TODAY_AI_SUBSTRING_TERMS):
        return True
    words = re.findall(r'[a-z0-9]+', text)
    segments = set()
    for word in re.findall(r'[A-Za-z0-9]+', original):
        if not (re.search(r'[a-z]', word) and re.search(r'[A-Z]', word)):
            continue
        # 先识别完整缩写复数，再走普通驼峰切段：OpenAPIs/ourMCPsHub 不拆成 AP/Is。
        # 小写 s 仅在词尾、下一个大写段或数字之前作复数；MAILs/RAPIDs 仍只得 MAIL/RAPID。
        for segment in re.findall(r'[A-Z]+s(?=$|[A-Z0-9])|[A-Z]+(?=[A-Z][a-z]|[0-9]|$)|[A-Z]?[a-z]+|[0-9]+', word):
            segments.add(segment[:-1] if segment.endswith('s') and segment[:-1].isupper() else segment)
    for term in TODAY_AI_BOUNDARY_TERMS:
        if (re.search(r'(?<![a-z0-9])' + re.escape(term) + r'(?![a-z0-9])', text)
                or term.upper() in segments
                or any(term in word and word in TODAY_AI_COMPOUNDS for word in words)):
            return True
    return False


def _today_product_related(text):
    """具名产品增量；歧义词仅同句AI语境，URL/作者身份不参与。"""
    from today_filter_config import (TODAY_AI_PRODUCTS, TODAY_AI_CONTEXT_PRODUCTS,
                                     TODAY_AI_PRODUCT_CONTEXT, TODAY_AI_PRODUCT_SPECIFIC_CONTEXT)
    if any(_today_term_matches(text, term) for term in TODAY_AI_PRODUCTS):
        return True
    # 英文句号排除小数点；换行与中英文句末标点均阻断语境跨句继承。
    for sentence in re.split(r'[。！？!?；;\n]|(?<!\d)\.|\.(?!\d)', text):
        for term in TODAY_AI_CONTEXT_PRODUCTS:
            specifics = TODAY_AI_PRODUCT_SPECIFIC_CONTEXT.get(term, ())
            specific = any(re.search(pattern, sentence) for pattern in specifics)
            # Kimi官方handle和K3.1版号自带词边界，独立匹配具体产品复合名。
            named = _today_term_matches(sentence, term)
            if ((named and (specific or any(re.search(pattern, sentence) for pattern in TODAY_AI_PRODUCT_CONTEXT)))
                    or (term in ('kimi', 'opus') and specific)):
                return True
    return False


def today_relevance_gate(item, interest_topics=(), channel='hot'):
    """全部来源共用的显示闸门；兴趣中的纯 Grok 只标记，不隐藏。

    只检查正文/标题和已有引用内容，不信任来源预填的赛道或相关性结论。
    返回值不修改输入，不联网，黑名单也适用于标签源中混入的福利帖。
    """
    from today_filter_config import LINUXDO_CATEGORY_BLACKLIST, TODAY_AI_TERMS, TODAY_AI_CONTEXT_PATTERNS
    sources = item.get('sources') or []
    text = _today_content_text(item)
    category = item.get('category') or item.get('topic') or ''
    reason = ''
    if any(s in ('L', 'linux.do', 'linuxdo') for s in sources) and category in LINUXDO_CATEGORY_BLACKLIST:
        reason = '分类黑名单：' + category
    elif not (_today_legacy_ai_related(text, _today_content_text(item, preserve_case=True))
              or any(_today_term_matches(text, t) for t in TODAY_AI_TERMS)
              or _today_product_related(text)
              or any(re.search(pattern, text, re.S) for pattern in TODAY_AI_CONTEXT_PATTERNS)):
        # 仅从当前方向段提取具体词，不把“新/工具/本地”等泛词当通行证。
        terms = set()
        for topic in interest_topics:
            terms.update(re.findall(r'[A-Za-z0-9][A-Za-z0-9.+-]{1,}|[\u4e00-\u9fff]{2,}', topic))
        generic = {'新模型发布', '本地部署与推理', '新', '模型网关', '本地', '工具', '教程', '参数', '开源权重', '量化版'}
        matched = any(t not in generic and _today_term_matches(text, t) for t in terms)
        if not matched:
            reason = '非 AI / 未匹配兴趣方向'
    exempt = channel == 'interest' and bool(sources) and all(s in ('G', 'Grok') for s in sources)
    return {'visible': exempt or not reason, 'reason': reason, 'marked_only': bool(exempt and reason)}


def classify_track(text: str, default_track: str = "ai_models") -> str:
    t = (text or "").lower()
    if any(k in t for k in ["虚拟卡", "stripe", "订阅", "支付", "封号", "绑卡", "美区", "出海基建", "收款", "税", "llc", "mercury", "x pass"]):
        return "overseas_infra"
    if any(k in t for k in ["github", "开源", "repo", "star", "仓库", "arxiv", "scion", "代码管理"]):
        return "github_open"
    if any(k in t for k in ["claude", "cursor", "codex", "agent", "mcp", "windsurf", "trae", "vibe", "写代码", "编程", "opus", "sonnet", "skill", "cli"]):
        return "claude_code"
    if any(k in t for k in ["变现", "赚钱", "商业", "营收", "arr", "mrr", "独立开发", "出海", "获客", "增长", "矩阵", "起号", "副业", "转化", "付费", "月入", "美金", "流量"]):
        return "money_indie"
    if any(k in t for k in ["模型", "gpt", "gemini", "deepseek", "qwen", "千问", "豆包", "kling", "可灵", "seedance", "sora", "生图", "视频生成", "评测", "推理", "蒸馏", "训练", "算力", "显卡", "本地部署"]):
        return "ai_models"
    return LEGACY_TRACK_MAP.get(default_track, "ai_models")


def content_filter(text: str, is_target_vip: bool = False) -> tuple[bool, str | None]:
    """雷达 AI 赛道过滤（只用于派生 bundle，不改快照）。"""
    low = (text or "").lower()
    if any(b in low for b in NOISE_KEYWORDS):
        return False, "noise"
    if is_ai_related(low):
        return True, None
    if has_chinese(text) and is_target_vip:
        return True, None
    return False, "not_ai"


def format_int(value) -> str:
    """展示用：None 明确写『未知』，不写 0。"""
    return "未知" if value is None else f"{int(value):,}"


def topic_summaries(views: list[dict], base: datetime, max_clusters: int = 10) -> list[dict]:
    """话题摘要只能由当前证据生成（M8）：列出支持帖 ID 与生成时间；无证据不产出带数字的摘要。

    输入是 bundle 里的帖子视图（id/user/name/views/likes/retweets/published_at/text/...）。
    """
    if not views:
        return []
    groups: dict[str, list[dict]] = {}
    for item in views:
        groups.setdefault(classify_track(item.get("text", "")), []).append(item)

    def sort_key(v: dict):
        return (v.get("views") if v.get("views") is not None else -1,
                v.get("likes") if v.get("likes") is not None else -1)

    clusters = []
    generated_at = iso_utc(base)
    for track, items in groups.items():
        items = sorted(items, key=sort_key, reverse=True)[:5]
        if not items:
            continue
        known_views = [v["views"] for v in items if v.get("views") is not None]
        total = sum(known_views)
        if known_views and len(known_views) == len(items):
            heat = f"{total / 10000:.1f}万曝光" if total >= 10000 else f"{total:,}曝光"
        else:
            heat = f"{len(items)} 条样本（部分浏览量未知）"
        newest = min((v.get("published_at") or "9999" for v in items))
        newest_dt = parse_utc(newest)
        if newest_dt is not None and in_window(newest_dt, base, 24):
            age_label = "🔥 24 小时内有新帖 · 实时追踪"
        else:
            age_label = "近 48 小时样本"
        lines = []
        for v in items:
            lines.append(
                f"@{v['user']}（{v['id']}）发布于 {v.get('published_at') or '未知'}，"
                f"浏览 {format_int(v.get('views'))} · 赞 {format_int(v.get('likes'))} · 转推 {format_int(v.get('retweets'))}"
            )
        top = items[0]
        clusters.append({
            "id": f"cluster_{track}",
            "track": track,
            "tag": TRACK_LABELS.get(track, track),
            "title": f"{TRACK_LABELS.get(track, track)} · 本轮 {len(items)} 条相关帖（按当前采样生成）",
            "age_label": age_label,
            "heat": heat,
            "burst_summary": "\n".join(lines),
            "core_conflict": f"本轮 {len(items)} 条样本里 @{top['user']} 的帖子指标最高，其余同赛道帖子见支持列表。",
            "counter_check": "结论只基于本轮采样，下一轮采样会重算；需要交叉核验请打开支持帖原帖。",
            "article_plan": f"《从 @{top['user']} 的这条帖子出发，核实现象再决定要不要跟》",
            "embedded_tweets": [embedded_view(v) for v in items],
            "supporting_tweet_ids": [v["id"] for v in items],
            "evidence_count": len(items),
            "generated_at": generated_at,
        })
    clusters.sort(key=lambda c: c["evidence_count"], reverse=True)
    return clusters[:max_clusters]


def embedded_view(view: dict) -> dict:
    return {
        "id": view["id"],
        "user": view["user"],
        "name": view.get("name") or view["user"],
        "fc": view.get("fc"),
        "views": view.get("views"),
        "likes": view.get("likes"),
        "retweets": view.get("retweets"),
        "diff_hours": view.get("age_hours"),
        "age_hours": view.get("age_hours"),
        "is_24h": view.get("is_today"),
        "url": view.get("url"),
        "created_at": view.get("created_at") or view.get("published_at") or "",
        "text": view.get("text") or "",
        "fetched_at": view.get("fetched_at"),
        "is_stale": bool(view.get("is_stale")),
    }
