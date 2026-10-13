"""情报采集 · 存储：订阅号、抓取任务、文章（fakeid + 链接双去重）。

独立库 workspace/intel/intel.db，不碰 intake* / knowledge_store / publication_*。
正文入库一律标为「待确认」，默认不自动进知识库；只有用户点「送入回收站」
才写 provenance=intel:wechat_mp:<fakeid>:<aid> 调既有回收站接口。
"""
import hashlib
import json
import logging
import re
import sqlite3
import threading
import time
import uuid
from urllib.parse import urlsplit, parse_qs
from contextlib import contextmanager, closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

import intel_job_ownership as ownership
from intel_wechat_mp import (MAX_BODIES_PER_RUN, MAX_PAGES_PER_RUN, HISTORY_PAGE_SIZE,
                             LATEST_PAGE_SIZE, BackendQuota, CredentialExpired, CredentialsMissing,
                             RateLimited, TaskDeadlinePassed, article_url, fakeid, request_deadline)
from intel_public import (ALBUM_REQUEST_BUDGET, AlbumError, AlbumStopped, PublicClient,
                          SOGOU_COOLDOWN_SECONDS, SOGOU_REQUEST_BUDGET, SogouBlocked,
                          discover_album_ids)

SCHEMA = '''
CREATE TABLE IF NOT EXISTS subscriptions(
    fakeid TEXT PRIMARY KEY, name TEXT NOT NULL DEFAULT '', note TEXT NOT NULL DEFAULT '',
    origin TEXT NOT NULL DEFAULT '', added_at TEXT NOT NULL, last_run_at TEXT NOT NULL DEFAULT '',
    last_status TEXT NOT NULL DEFAULT '', UNIQUE(fakeid));
CREATE TABLE IF NOT EXISTS jobs(
    id TEXT PRIMARY KEY, fakeid TEXT NOT NULL, name TEXT NOT NULL DEFAULT '',
    mode TEXT NOT NULL, status TEXT NOT NULL, reason TEXT NOT NULL DEFAULT '',
    detail TEXT NOT NULL DEFAULT '', routes TEXT NOT NULL DEFAULT '', found INTEGER NOT NULL DEFAULT 0,
    added INTEGER NOT NULL DEFAULT 0, bodies INTEGER NOT NULL DEFAULT 0,
    pages INTEGER NOT NULL DEFAULT 0, started_at TEXT NOT NULL, finished_at TEXT NOT NULL DEFAULT '',
    outcome TEXT NOT NULL DEFAULT '',
    ownership_version INTEGER NOT NULL DEFAULT 0, owner_token TEXT NOT NULL DEFAULT '',
    owner_dev INTEGER NOT NULL DEFAULT 0, owner_ino INTEGER NOT NULL DEFAULT 0,
    discovery TEXT NOT NULL DEFAULT '',
    UNIQUE(id));
CREATE INDEX IF NOT EXISTS jobs_fakeid ON jobs(fakeid, started_at DESC);
CREATE TABLE IF NOT EXISTS articles(
    id TEXT PRIMARY KEY, fakeid TEXT NOT NULL, aid TEXT NOT NULL, link TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL DEFAULT '', title TEXT NOT NULL DEFAULT '', published_at INTEGER NOT NULL DEFAULT 0,
    body_status TEXT NOT NULL DEFAULT 'pending', body TEXT NOT NULL DEFAULT '',
    chars INTEGER NOT NULL DEFAULT 0, images TEXT NOT NULL DEFAULT '[]', error TEXT NOT NULL DEFAULT '',
    job_id TEXT NOT NULL DEFAULT '', drop_id TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT '', link_ok INTEGER NOT NULL DEFAULT 1,
    added_at TEXT NOT NULL, body_at TEXT NOT NULL DEFAULT '',
    UNIQUE(fakeid, aid));
CREATE INDEX IF NOT EXISTS articles_fakeid ON articles(fakeid, published_at DESC);
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS link_index(
    link TEXT PRIMARY KEY, fakeid TEXT NOT NULL DEFAULT '', nickname TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending', detail TEXT NOT NULL DEFAULT '',
    source_kind TEXT NOT NULL DEFAULT '', resolved_at TEXT NOT NULL DEFAULT '');
CREATE INDEX IF NOT EXISTS link_index_fakeid ON link_index(fakeid);
CREATE TABLE IF NOT EXISTS recommendations(
    fakeid TEXT PRIMARY KEY, nickname TEXT NOT NULL DEFAULT '', count INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'new', first_seen TEXT NOT NULL, last_seen TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS albums(
    fakeid TEXT NOT NULL, album_id TEXT NOT NULL, title TEXT NOT NULL DEFAULT '',
    article_count INTEGER NOT NULL DEFAULT 0, found_from TEXT NOT NULL DEFAULT '',
    found_at TEXT NOT NULL, UNIQUE(fakeid, album_id));
CREATE INDEX IF NOT EXISTS albums_fakeid ON albums(fakeid);
CREATE TABLE IF NOT EXISTS recommendation_sources(
    fakeid TEXT NOT NULL, source_kind TEXT NOT NULL, source_id TEXT NOT NULL, link TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT '', seen_at TEXT NOT NULL,
    PRIMARY KEY(source_kind, source_id, link));
CREATE INDEX IF NOT EXISTS recommendation_sources_fakeid ON recommendation_sources(fakeid);
CREATE TABLE IF NOT EXISTS album_searches(
    id TEXT PRIMARY KEY, fakeid TEXT NOT NULL DEFAULT '', kind TEXT NOT NULL DEFAULT '',
    albums_found INTEGER NOT NULL DEFAULT 0, has_chksm INTEGER NOT NULL DEFAULT 0,
    detail TEXT NOT NULL DEFAULT '', searched_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS album_searches_fakeid ON album_searches(fakeid, searched_at DESC);
CREATE TABLE IF NOT EXISTS discovery_state(
    fakeid TEXT PRIMARY KEY, last_attempt_at TEXT NOT NULL DEFAULT '',
    last_job_id TEXT NOT NULL DEFAULT '');
'''

# 推荐订阅（二期A）：每轮最多解析多少条未解析链接。
MAX_RESOLVE_PER_ROUND = 20

NAME_LIMIT = 100
NOTE_LIMIT = 200
# 超过这个时长还挂着 running/queued 的任务按「重启残留」处理。
# 定时任务的 timeout 上限是 1800 秒，给足余量，正常在跑的任务不会被误杀。
STALE_JOB_SECONDS = 3600
ABANDONED_JOB_SECONDS = 60
START_FAILURE_RETRY_SECONDS = 0.05
# 所有权协议版本：0 = 旧行（无 owner，沿用 3600/60 秒规则）；1 = 持续持有 flock。
# 残缺的 v1（缺 token/dev/ino）与未知版本都当 unknown，保持闸门，绝不降级 legacy 兜底。
OWNERSHIP_VERSION = 1
# 恢复专用有界预算：不无限等 DB，超时未恢复就如实保留，锁 FD 由 finally 释放。
RECOVERY_DB_TIMEOUT = 1.0
RECOVERY_BUSY_TIMEOUT_MS = 1000
RECOVER_REASON = '任务所有权已释放，恢复时标为已中断；已入库成果保留，未保存的内存结果未知'

# ── 阶段 B：自动发现与合集优先 ────────────────────────────────────────────
# 发现安全回执独立存 jobs.discovery，自己 version=1，不挤入 outcome 三段（也不受其
# 4000 字符硬截断影响）。保存用结果包（完整 link/body/identity）只交给内部事务，绝不整体序列化。
DISCOVERY_RECEIPT_VERSION = 1
# 已有合集按「上次尝试」节流 7 天（滚动窗口）；无合集每轮一次。
DISCOVERY_ALBUM_INTERVAL_SECONDS = 7 * 24 * 3600
# 范围三态：within（滚动窗口内）/ unknown（缺失或未来）/ outside（窗口外）。
DISCOVERY_RANGE_STATES = ('within', 'unknown', 'outside')
DISCOVERY_RANGE_WINDOW_DAYS = {'pw': 7, 'pm': 30}
DISCOVERY_MAX_JSON = 4000
# A 接口（intel_link_discovery）的硬预算：最多 8 候选 / 3 读取；逻辑查询 1 次。
DISCOVERY_BUDGET = {'max_searches': 1, 'max_candidates': 8, 'max_reads': 3}
# 发现回执的安全状态/停止码（与 A 模块对齐，避免字符串反推）。
# reserved = 已在 owner 事务里预留了一次尝试，但请求前/中中断，结果未知（不当作已请求或成功）。
DISCOVERY_STATES = ('ok', 'skipped', 'failed', 'cancelled', 'reserved')
DISCOVERY_UNCONFIGURED_NOTE = '未配置发现来源（provider），本轮跳过发现，只走合集/后台'
# 预留后崩溃/中断的专用回执文案（合同 B §6：已预留尝试，结果未知；不重放）。
DISCOVERY_RESERVED_NOTE = '已预留尝试，结果未知'
# 手动发现的停止边界：run 没有外部取消/截止，给一个有限预算，保证 reader 单次超时也受剩余时间约束。
MANUAL_DISCOVERY_DEADLINE_SECONDS = 600
# 安全回执标量白名单：文本限长、整数强制、嵌套对象只留已知键，保证整份序列化有界。
_DISCOVERY_TEXT_LIMITS = {'provider': 64, 'query': 200, 'freshness': 8, 'state': 16,
                          'stop_kind': 32, 'stop_note': 200}
_DISCOVERY_INT_KEYS = ('version', 'search_attempted', 'results_found', 'candidates_accepted',
                       'reads_attempted', 'verified', 'biz_mismatch', 'read_failed')
# 回执计数字段的可表达上限：真实计数远小于它；更大的「合法」整数（10**2000 也能被
# json 编码）一律封顶，保证整份序列化 ≤ DISCOVERY_MAX_JSON（B3 · L1）。
_DISCOVERY_INT_LIMIT = 10 ** 9
_DISCOVERY_BUDGET_KEYS = ('max_searches', 'max_candidates', 'max_reads')


class StaleOwnershipError(RuntimeError):
    """任务所有权已失效（已恢复/被别的 owner 接管）：业务写必须拒绝，不覆盖新结果。"""
_PROCESS_ABANDONED = set()
_LOG = logging.getLogger(__name__)
_PROCESS_WORKERS = {}
_PROCESS_WORKERS_LOCK = threading.RLock()
STARTUP_STALE_REASON = '工作台重启中断了这次抓取；点一次抓取即可重来'
LIVE_STALE_REASON = '抓取超过时限仍未结束，已中断；点一次抓取即可重来'
ALBUM_SEARCH_FAILED = '文章页读取失败'

# 三条路线与任务状态的中文文案。页面直接用它，不再自己拼。
PATH_LABELS = {'album': '合集', 'sogou': '搜狗', 'backend': '后台'}
PATH_ORDER = ('album', 'sogou', 'backend')
# 发现路线（B4 小修）：不走 PATH_ORDER（_routes_detail 等已单独拼「发现：」，
# 放进去会重复），只在页面路线渲染处补一行。routes['discovery'] 通常已是
# _discovery_route_text 的中文整句；若只存了状态码，也翻译成中文。
DISCOVERY_ROUTE_LABEL = '发现'
DISCOVERY_STATE_TEXTS = {'ok': '发现完成', 'skipped': '发现跳过', 'failed': '发现失败',
                         'cancelled': '发现被取消', 'reserved': '已预留尝试，结果未知'}
MODE_LABELS = {'latest': '抓最新', 'history': '抓历史', 'scheduled': '定时（每日抓最新）',
               'discovery': '自动发现'}
STATUS_LABELS = {'running': '进行中', 'queued': '排队中', 'done': '已完成', 'partial': '部分完成',
                 'rate_limited': '限流冷却', 'credentials': '需重新登录后台', 'interrupted': '已中断',
                 'error': '失败', 'cooldown': '冷却中，本轮跳过', 'busy': '已有任务在跑'}

# 主文案给人看，错误码只放在括号里对照。命中第一条即用。
_HUMAN_REASONS = (
    ('200013', '公众号后台接口被限流（错误码 200013），这一轮已停止、没有重试'),
    ('接口限流冷却中', '后台接口还在冷却期，这一轮没有发任何请求'),
    ('需重新登录', '公众号后台的登录已失效，要重新登录并更新凭证'),
    ('钥匙串里没有', '这台电脑上还没有存公众号后台的凭证，要先在后台登录一次'),
    ('验证码', '搜狗的跳转被验证码拦住，这一路只拿得到标题和时间'),
    ('线程没有启动', '抓取线程没能启动；再点一次抓取即可'),
    ('重启中断', '工作台重启打断了这次抓取；再点一次抓取即可'),
    ('超过时限', '这次抓取超过时限被中断；再点一次抓取即可'),
    ('未预期的错误', '抓取过程中出现未预期的错误，已保留入库的结果'),
)


def humanize(text):
    """把技术文案/错误码换成给人看的主文案；原文仍在 detail 里保留。"""
    text = ' '.join(str(text or '').split())
    if not text:
        return ''
    for marker, phrase in _HUMAN_REASONS:
        if marker in text:
            return phrase
    return text


# ── 分阶段 outcome（version 1）───────────────────────────────────────────
# 一次抓取分三段：list（取列表）/ save（落库）/ body（读正文）。每段单独记真实结果，
# 终态由这三段推导，不再用「有列表来源就 done」。旧行 outcome 为空 = 不可用，不猜。
OUTCOME_VERSION = 1
# 阶段状态取值：not_requested 未请求；not_attempted 未开始；complete 完成；
# partial 部分完成；failed 全部失败；unavailable 无法查询；blocked 受阻；unknown 未知。
LIST_STATES = ('not_requested', 'complete', 'partial', 'failed', 'unavailable', 'unknown')
SAVE_STATES = ('not_attempted', 'complete', 'partial', 'failed', 'unknown')
BODY_STATES = ('not_requested', 'not_started', 'complete', 'partial', 'failed', 'unknown')


def route_record(state, requested=True, attempted=0, count=0, stop_kind=''):
    """一条路线（合集/搜狗/后台）的真实结果，不靠中文 routes 文字反推。"""
    return {'requested': bool(requested), 'attempted': int(attempted),
            'state': state, 'count': int(count), 'stop_kind': str(stop_kind or '')}


def reduce_list(routes):
    """三条路线的真实结果 → 列表阶段状态。

    有任一路线确实查询成功即为 complete（缺合集不影响：另一路成功就算查到了）；
    有成功也有失败/受阻 → partial；全失败 → failed；只有受阻/缺入口 → partial。
    """
    active = [record for record in routes.values() if record.get('state') != 'not_requested']
    if not active:
        return 'not_requested'
    states = {record.get('state') for record in active}
    if 'complete' in states:
        return 'partial' if (states - {'complete', 'unavailable'}) else 'complete'
    if 'failed' in states:
        return 'failed'
    return 'partial'


def reduce_save(attempted, succeeded, failed, not_attempted):
    """落库阶段：attempted=succeeded+failed，selected=attempted+not_attempted。"""
    attempted, succeeded, failed, not_attempted = (int(attempted), int(succeeded),
                                                   int(failed), int(not_attempted))
    total = attempted + not_attempted
    if total <= 0:
        return 'not_attempted'
    if succeeded <= 0:
        return 'failed'
    if succeeded >= total and failed == 0:
        return 'complete'
    return 'partial'


def save_stage(state, attempted=0, succeeded=0, new=0, existing=0, failed=0, not_attempted=0):
    """落库阶段结构。不变量：attempted=succeeded+failed，selected=attempted+not_attempted。"""
    return {'state': state, 'attempted': int(attempted), 'succeeded': int(succeeded),
            'new': int(new), 'existing': int(existing), 'failed': int(failed),
            'not_attempted': int(not_attempted)}


def body_stage(state, requested_budget=0, effective_budget=0, attempted=0, succeeded=0,
               failed=0, stopped=False, skipped_budget=0, eligible_remaining=0, unreadable=0):
    """正文阶段结构。预算/范围都如实写，不把外网失败算进 skipped_budget。"""
    return {'state': state, 'requested_budget': int(requested_budget),
            'effective_budget': int(effective_budget), 'attempted': int(attempted),
            'succeeded': int(succeeded), 'failed': int(failed), 'stopped': bool(stopped),
            'skipped_budget': int(skipped_budget), 'eligible_remaining': int(eligible_remaining),
            'unreadable': int(unreadable)}


def reduce_body(requested_budget, attempted, succeeded):
    """正文阶段。requested_budget=0 表示本轮未请求正文（含定时）。"""
    requested_budget, attempted, succeeded = int(requested_budget), int(attempted), int(succeeded)
    if requested_budget <= 0:
        return 'not_requested'
    if attempted <= 0:
        return 'complete'
    if succeeded >= attempted:
        return 'complete'
    if succeeded <= 0:
        return 'failed'
    return 'partial'


def summarize_outcome(outcome):
    """把三段结果拼成一句给人看的结构化摘要；页面直接显示它。"""
    listed, saved, body = outcome.get('list', {}), outcome.get('save', {}), outcome.get('body', {})
    parts = []
    empty_success = (listed.get('state') == 'complete' and int(listed.get('found') or 0) == 0)
    if empty_success:
        parts.append('查询成功，本次没有新文章')
    elif listed.get('state') == 'complete':
        parts.append(f"列表 {int(listed.get('found') or 0)} 篇")
    elif listed.get('state') == 'partial':
        parts.append(f"列表 {int(listed.get('found') or 0)} 篇（部分路线未完成）")
    elif listed.get('state') == 'failed':
        parts.append('取列表失败')
    elif listed.get('state') == 'not_requested':
        parts.append('未取列表')
    else:
        parts.append('列表结果不可用')
    saved_state = saved.get('state')
    succeeded = int(saved.get('succeeded') or 0)
    if not empty_success:
        if saved_state == 'not_attempted':
            parts.append('未保存')
        elif saved_state == 'failed':
            parts.append(f"保存全部失败（尝试 {int(saved.get('attempted') or 0)} 篇）")
        elif saved_state == 'partial':
            parts.append(f"保存 {succeeded} 篇，失败 {int(saved.get('failed') or 0)} 篇，"
                         f"未处理 {int(saved.get('not_attempted') or 0)} 篇")
        elif succeeded == 0:
            parts.append('没有需要保存的文章')
        elif int(saved.get('new') or 0) == 0 and int(saved.get('existing') or 0):
            parts.append(f"复用 {int(saved.get('existing') or 0)} 篇，没有新增")
        else:
            parts.append(f"保存 {succeeded} 篇（新增 {int(saved.get('new') or 0)} 篇）")
    body_state = body.get('state')
    if body_state == 'not_requested':
        parts.append('正文未请求')
    elif body_state == 'not_started':
        # 本轮在正文之前就中止了：既不是「无需读取」也不是「完成」，如实说未开始。
        parts.append('正文未开始（本轮已中止）')
    elif body_state == 'failed':
        if body.get('stopped'):
            parts.append(f"正文被限流停止（尝试 {int(body.get('attempted') or 0)} 篇，"
                         f"成功 {int(body.get('succeeded') or 0)} 篇）")
        else:
            parts.append(f"正文全部失败（尝试 {int(body.get('attempted') or 0)} 篇）")
    elif body_state == 'partial':
        parts.append(f"正文成功 {int(body.get('succeeded') or 0)}/{int(body.get('attempted') or 0)} 篇")
    elif int(body.get('attempted') or 0):
        parts.append(f"正文 {int(body.get('succeeded') or 0)} 篇待确认")
    else:
        parts.append('正文无需读取')
    if int(body.get('eligible_remaining') or 0):
        parts.append(f"另有 {int(body.get('eligible_remaining') or 0)} 篇未读")
    return '；'.join(parts)


def build_outcome(list_state, list_found, list_selected, list_routes,
                  save_state, save, body_state, body, unique_new=None):
    outcome = {'version': OUTCOME_VERSION,
               'list': {'state': list_state, 'found': int(list_found),
                        'selected': int(list_selected), 'routes': list_routes},
               'save': dict(save), 'body': dict(body)}
    # R10 口径分开记：list.found 是**来源命中数**（各路各自计数，同一篇被多个来源命中会各算一次），
    # unique_new 是**唯一新增文章数**（本轮按入库身份键去重后真正新增的文章）。
    # 两者语义都不改既有字段，只是把「命中」与「新增」说清楚。
    if unique_new is not None:
        outcome['unique_new'] = max(0, int(unique_new))
    outcome['summary'] = summarize_outcome(outcome)
    return outcome


def decide_status(outcome, interrupted=False):
    """三段结果 → jobs.status。中断/专门状态由调用方直接给，不走这里。"""
    if interrupted:
        return 'interrupted'
    listed = outcome['list']['state']
    saved = outcome['save']['state']
    body = outcome['body']['state']
    if listed == 'failed' or saved == 'failed':
        return 'error'
    if listed == 'partial' or saved == 'partial' or body in ('partial', 'failed', 'not_started'):
        return 'partial'
    if listed == 'unknown' or saved == 'unknown':
        return 'partial'
    return 'done'


def recovered_outcome():
    """恢复（原所有权结束）时写的回执：内存里的阶段结果没落库，如实标 unknown，不编造计数。"""
    return {'version': OUTCOME_VERSION,
            'list': {'state': 'unknown', 'found': 0, 'selected': 0, 'routes': {}},
            'save': save_stage('unknown'),
            'body': body_stage('unknown'),
            'summary': '任务被中断；恢复时未保存内存中的阶段结果，已有入库成果保留'}


def label(value):
    """库里的 ISO 时间 → 本地可读；拿不到就如实留空。"""
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return ''
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone().strftime('%Y-%m-%d %H:%M')


def now():
    return datetime.now(timezone.utc).isoformat()


def stamp(value):
    """Unix 秒 → 本地可读时间；拿不到就如实留空，不编造。"""
    try:
        seconds = int(value)
    except (TypeError, ValueError):
        return ''
    if seconds <= 0:
        return ''
    return datetime.fromtimestamp(seconds, timezone.utc).astimezone().strftime('%Y-%m-%d %H:%M')


def clean(value, limit):
    value = '' if value is None else str(value)
    value = re.sub(r'\s+', ' ', value).strip()
    if len(value) > limit:
        raise ValueError(f'内容超过 {limit} 字上限')
    return value


def _int_or(value, default=0):
    """宽松整数解析：残缺/非法一律回退默认值，绝不抛异常。"""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _bounded_text(value, limit):
    """结构化限长：只按字符数裁单个字段，绝不截断整份 JSON 字符串。"""
    text = '' if value is None else str(value)
    return text[:limit]


def _parse_iso(value):
    """ISO 文本 → 带时区的 datetime；残缺/非法返回 None（不抛）。"""
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def discovery_aid(identity, identifier):
    """可信页面身份 → 稳定 aid。

    仅当页面 biz 与订阅 fakeid 严格相等、且 msgid/itemidx 都是合法数字时才用
    ``SHA256('wechat:'+biz+':'+msgid+':'+itemidx)[:32]``；缺身份返回 ''，
    由调用方回退到规范链接指纹（绝不按标题合并）。
    """
    if not isinstance(identity, dict):
        return ''
    biz = str(identity.get('biz') or '').strip()
    msgid = str(identity.get('msgid') or '').strip()
    itemidx = str(identity.get('itemidx') or '').strip()
    if biz and biz == identifier and re.fullmatch(r'\d{5,30}', msgid) \
            and re.fullmatch(r'\d{1,3}', itemidx):
        material = f'wechat:{biz}:{msgid}:{itemidx}'
        return hashlib.sha256(material.encode()).hexdigest()[:32]
    return ''


def _link_identity(link):
    """规范长链接 → 可信身份（__biz/mid/idx）；拿不到返回空 dict，不猜。"""
    try:
        parts = urlsplit(str(link or ''))
    except (TypeError, ValueError):
        return {}
    if not parts.query:
        return {}
    query = parse_qs(parts.query)

    def one(key):
        values = query.get(key) or []
        return str(values[0]).strip() if values else ''

    return {'biz': one('__biz'), 'msgid': one('mid'), 'itemidx': one('idx')}


def trusted_identity_aid(identifier, link='', msgid='', itemidx=''):
    """发现 / 合集 / 后台三路统一的可信身份 aid。

    优先用显式 msgid/itemidx（合集接口直接给）；没有就退回规范长链接的 mid/idx（后台列表给的是长链）。
    biz 必须是本号；缺身份 / biz 不符 / 非数字一律返回 ''，由调用方回退规范链接指纹（绝不按标题合并）。
    """
    msgid = str(msgid or '').strip()
    itemidx = str(itemidx or '').strip()
    if not (msgid and itemidx):
        identity = _link_identity(link)
        if identity.get('biz') != identifier:
            return ''
        msgid, itemidx = identity.get('msgid'), identity.get('itemidx')
    return discovery_aid({'biz': identifier, 'msgid': msgid, 'itemidx': itemidx}, identifier)


class DiscoveryStopped(Exception):
    """发现读取阶段遇到取消/截止：由 A 的读取循环收口成 cancelled/deadline，不继续出网。"""


def _cancel_requested(cancel):
    """取消信号：支持 threading.Event.is_set() 或 callable；缺失/异常一律当作未取消。"""
    if cancel is None:
        return False
    checker = getattr(cancel, 'is_set', None)
    if callable(checker):
        try:
            return bool(checker())
        except Exception:  # noqa: BLE001
            return False
    if callable(cancel):
        try:
            return bool(cancel())
        except Exception:  # noqa: BLE001
            return False
    return False


def _deadline_passed(deadline):
    """monotonic 截止值是否已到；None 表示无截止。"""
    return deadline is not None and time.monotonic() >= deadline


def discovery_window(published_at, freshness, reference):
    """reader published_at 与冻结任务 UTC 时间 → within/unknown/outside。

    pw 滚动 7 天、pm 滚动 30 天；缺失/非法/未来时间一律 unknown。
    provider 的 freshness 只是查询提示，范围判定只认页面真实 published_at。
    """
    seconds = _int_or(published_at, 0)
    if seconds <= 0 or reference is None:
        return 'unknown'
    try:
        published = datetime.fromtimestamp(seconds, timezone.utc)
    except (ValueError, OSError, OverflowError, TypeError):
        return 'unknown'
    if published > reference:
        return 'unknown'  # 未来时间：不当作窗口内，也不当作窗口外
    days = DISCOVERY_RANGE_WINDOW_DAYS.get(freshness)
    if not days:
        return 'unknown'
    if published >= reference - timedelta(days=days):
        return 'within'
    return 'outside'


def _bounded_int(value, limit=_DISCOVERY_INT_LIMIT):
    """整数限幅：计数类字段强制落在 [0, limit]，超大合法整数（如 10**2000）不再撑爆 JSON。

    真实计数远小于上限；限幅只影响异常输入，语义是「可表达范围内的封顶值」。
    """
    number = _int_or(value, 0)
    return max(0, min(number, limit))


# 时间戳的独立合理范围（B4 · M1）：0 < t ≤ 4102444800（2100-01-01）。
# 时间**不复用**计数上限 10**9——那是 2001-09-09，之后的正常现代时间都会被误伤；
# 越界（缺失/非整数/异常巨大）一律置 null，绝不截成常数。
_DISCOVERY_TS_LIMIT = 4102444800


def _bounded_timestamp(value):
    """时间戳独立限界：范围内原值保留，越界置 None（不是封顶值）。"""
    number = _int_or(value, 0)
    if number <= 0 or number > _DISCOVERY_TS_LIMIT:
        return None
    return number


def _bounded_budget(value):
    """budget 只保留已知键的整数，防止任意嵌套把整份回执撑爆。"""
    source = value if isinstance(value, dict) else {}
    return {key: _bounded_int(source.get(key)) for key in _DISCOVERY_BUDGET_KEYS}


def _bounded_range_counts(value):
    """range_counts 只保留三态整数（总计/状态不可丢）。"""
    source = value if isinstance(value, dict) else {}
    return {key: _bounded_int(source.get(key)) for key in DISCOVERY_RANGE_STATES}


def _trim_discovery(receipt, max_candidates=16, max_articles=16):
    """安全回执的结构化限长：**白名单 + 标量限长**，保留总计/状态；绝不截断整份 JSON。

    标量按类型处理：整数强制**并限幅**、文本限长、budget/range_counts 只留已知键，
    避免像 stop_note='x'*10000 或 10**2000 这种异常标量把整份回执撑到超过硬上限。
    """
    data = {}
    if not isinstance(receipt, dict):
        return data
    for key in _DISCOVERY_INT_KEYS:
        if key in receipt:
            data[key] = _bounded_int(receipt.get(key))
    for key, limit in _DISCOVERY_TEXT_LIMITS.items():
        if key in receipt:
            data[key] = _bounded_text(receipt.get(key), limit)
    if 'budget' in receipt:
        data['budget'] = _bounded_budget(receipt.get('budget'))
    if 'range_counts' in receipt:
        data['range_counts'] = _bounded_range_counts(receipt.get('range_counts'))
    albums = receipt.get('album_ids')
    if isinstance(albums, list):
        data['album_ids'] = [_bounded_text(a, 40) for a in albums[:20]]
    candidates = receipt.get('candidates')
    if isinstance(candidates, list):
        trimmed = [{'code': _bounded_text(c.get('code'), 40)} for c in candidates[:max_candidates]
                   if isinstance(c, dict)]
        if len(candidates) > max_candidates:
            trimmed.append({'code': 'trimmed'})
        data['candidates'] = trimmed
    articles = receipt.get('verified_articles')
    if isinstance(articles, list):
        trimmed = [{'link': _bounded_text(a.get('link'), 200),
                    'title': _bounded_text(a.get('title'), 120),
                    'name': _bounded_text(a.get('name'), 80),
                    'published_at': _bounded_timestamp(a.get('published_at')),
                    'source': _bounded_text(a.get('source'), 20),
                    'window': _bounded_text(a.get('window'), 20)}
                   for a in articles[:max_articles] if isinstance(a, dict)]
        if len(articles) > max_articles:
            trimmed.append({'link': '', 'title': '', 'window': 'trimmed'})
        data['verified_articles'] = trimmed
    return data


def _discovery_json(receipt):
    """安全回执 → 结构化裁剪后的 JSON 文本。

    序列化前先做标量白名单限长；若仍超硬上限，按「可选明细 → 可选标量」顺序继续裁剪，
    最后**校验序列化结果本身**，确保输出永远是合法 JSON 且 ≤ DISCOVERY_MAX_JSON，
    同时保留 version/state/verified/range_counts 等总计/状态。
    """
    data = _trim_discovery(receipt)
    raw = json.dumps(data, ensure_ascii=False)
    if len(raw) <= DISCOVERY_MAX_JSON:
        return raw
    # 1) 删可选明细列表
    for key in ('verified_articles', 'candidates', 'album_ids'):
        if key in data:
            data.pop(key, None)
            raw = json.dumps(data, ensure_ascii=False)
            if len(raw) <= DISCOVERY_MAX_JSON:
                return raw
    # 2) 收紧可选文本标量
    for key, limit in (('query', 80), ('stop_note', 80), ('provider', 32), ('stop_kind', 24)):
        if key in data:
            data[key] = _bounded_text(data.get(key), limit)
    raw = json.dumps(data, ensure_ascii=False)
    if len(raw) <= DISCOVERY_MAX_JSON:
        return raw
    # 3) 兜底：删可选标量（保留 version/state/verified/range_counts 等总计）
    for key in ('stop_note', 'query', 'provider', 'budget', 'stop_kind', 'freshness'):
        if key in data:
            data.pop(key, None)
            raw = json.dumps(data, ensure_ascii=False)
            if len(raw) <= DISCOVERY_MAX_JSON:
                return raw
    # 4) 最终兜底：只留总计/状态骨架。整数已限幅、文本已限长，骨架必然有界——
    #    输出永远合法、≤ DISCOVERY_MAX_JSON（B3 · L1：不能只限文本，整数也要有界）。
    skeleton = {'version': data.get('version'),
                'state': _bounded_text(data.get('state'), 16),
                'verified': data.get('verified', 0),
                'range_counts': data.get('range_counts') or _bounded_range_counts({})}
    return json.dumps(skeleton, ensure_ascii=False)


def article_fingerprint(link, seed=''):
    """没有后台 aid 时的文章身份。

    有链接就用链接指纹；**没有链接时**（搜狗只拿到标题的情况）退回「标题+时间」
    的指纹 —— 否则 article_url('') 会抛错，整轮抓取被一条无链接的记录带崩。
    """
    material = article_url(link) if link else f'no-link:{seed}'
    return hashlib.sha256(material.encode()).hexdigest()[:32]


def accepted_link(link):
    """入库前过 article_url。不合格的合集链接不当成 href，改走无链接占位。"""
    text = '' if link is None else str(link).strip()
    if not text:
        return ''
    try:
        return article_url(text)
    except ValueError:
        return ''


# 对外行用。只认入库时写下的 source，不从占位串反推：合集不合格链接也存成 sogou-no-link:。
_LINK_MISSING_REASON = {'sogou': 'sogou_title_only', 'album': 'invalid_link'}


def annotate_missing_link(data):
    """无链接的对外行按入库路径填原因。认不出路径就不填，页面用中性说法。"""
    if data.get('link_ok', 1):
        return data
    data['link'] = ''
    reason = _LINK_MISSING_REASON.get(data.get('source') or '')
    if reason:
        data['link_missing_reason'] = reason
    return data


def interrupt_stale_jobs(connection, reason, protected=(), abandoned=()):
    """把超时仍挂着的 queued/running 标为中断。调用方负责提交。

    只处理**严格 legacy** 行（``ownership.LEGACY_WHERE``，与公开投影的 ``classify()`` 同一判定）：
    v1 由所有权协议恢复，绝不按年龄回收；version 0 却混入 owner 字段的身份未知行同样保持闸门，
    不能因为 ownership_version 碰巧是 0 就被当成旧协议行清掉。
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(seconds=STALE_JOB_SECONDS)).isoformat()
    protected = tuple(protected)
    exclusion = (' AND id NOT IN (' + ','.join('?' for _ in protected) + ')') if protected else ''
    moment = now()
    connection.execute("UPDATE jobs SET status='interrupted',finished_at=?,reason=? "
                       "WHERE status IN ('queued','running') AND " + ownership.LEGACY_WHERE +
                       " AND started_at < ?" + exclusion,
                       (moment, reason, cutoff, *protected))
    # 无存活本机线程不能证明其他进程没有 worker；短宽限只给明确启动失败的 legacy 行。
    abandoned = tuple(set(abandoned) - set(protected))
    if abandoned:
        short_cutoff = (datetime.now(timezone.utc) - timedelta(seconds=ABANDONED_JOB_SECONDS)).isoformat()
        # 启动失败时 reason 已写明「线程没有启动」。短宽限只补空原因，不改写成超时文案。
        connection.execute("UPDATE jobs SET status='interrupted',finished_at=?,"
                           "reason=CASE WHEN reason!='' THEN reason ELSE ? END "
                           "WHERE status IN ('queued','running') AND " + ownership.LEGACY_WHERE +
                           " AND started_at < ? AND id IN ("
                           + ','.join('?' for _ in abandoned) + ')',
                           (moment, reason, short_cutoff, *abandoned))
    # 中断也是一种收口：订阅上的「最近状态」要跟着走，否则页面永远停在旧状态。
    connection.execute("UPDATE subscriptions SET last_run_at=?,last_status='interrupted' "
                       "WHERE fakeid IN (SELECT fakeid FROM jobs WHERE status='interrupted' AND finished_at=?)",
                       (moment, moment))


class Settings:
    """限流器用的键值表；冷却状态因此能跨进程重启存活。"""

    def __init__(self, store):
        self.store = store

    def get(self, key, default=None):
        with self.store.db() as connection:
            row = connection.execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()
        return row['value'] if row else default

    def set(self, key, value):
        with self.store.db() as connection:
            connection.execute('INSERT INTO settings(key,value) VALUES(?,?) '
                               'ON CONFLICT(key) DO UPDATE SET value=excluded.value', (key, str(value)))


class Intel:
    """情报采集 · 第一期（微信公众号）。所有抓取都经 intel_wechat_mp 的限流器。"""

    def __init__(self, store, client_factory=None, public_factory=None, discovery_factory=None):
        self.store = store
        self.workspace = Path(store.workspace) / 'intel'
        self.path = self.workspace / 'intel.db'
        self.lock = threading.RLock()
        self.workers = {}
        self.client_factory = client_factory
        self.public_factory = public_factory
        # 自动发现（A 接口）的来源。None = 用真实链路（remio→serper→自建 SearXNG）；
        # 测试一律注入假 provider/reader，保证零真实请求。
        self.discovery_factory = discovery_factory
        self._client = None
        self._public = None
        self._settings = None

    @contextmanager
    def db(self):
        self._ensure()
        connection = self._connect()
        try:
            with self.lock:
                connection.execute('BEGIN IMMEDIATE')
            yield connection
            connection.commit()
        finally:
            connection.close()

    def _connect(self, timeout=30, busy_timeout=30000):
        """开一个连接但不走 _ensure：恢复路径用它，避免 db→ensure→恢复→db 递归。"""
        connection = sqlite3.connect(self.path, timeout=timeout)
        connection.row_factory = sqlite3.Row
        connection.execute(f'PRAGMA busy_timeout={int(busy_timeout)}')
        return connection

    def _ensure(self):
        if getattr(self, '_ready', False):
            return
        with self.lock:
            if getattr(self, '_ready', False):
                return
            self.workspace.mkdir(parents=True, mode=0o700, exist_ok=True)
            self.workspace.chmod(0o700)
            connection = sqlite3.connect(self.path)
            try:
                connection.executescript(SCHEMA)
                self._migrate_columns(connection)
                # 进程重启后不可能还有活的抓取线程；把「放了很久还在跑」的行标为中断，
                # 否则它们会永远占着「已有任务」的位子，让这个号再也发不起抓取。
                # 只按时间判定，不看是哪个实例：否则同一 workspace 上第二个 Intel
                # 实例刚建好就会把第一个实例正在跑的任务误杀。
                # 只处理 legacy 行：v1 由所有权协议在迁移提交后恢复，绝不按年龄回收。
                interrupt_stale_jobs(connection, STARTUP_STALE_REASON, self._live_worker_ids(), self._abandoned_ids())
                connection.commit()
                self._release_settled_abandoned(connection)
            finally:
                connection.close()
            self.path.chmod(0o600)
            self._ready = True
        # 迁移提交后再恢复 v1：此时 _ready 已置真，恢复用 _connect 不会递归回 _ensure。
        self._recover_owned_jobs()

    # 迁移要补的列：表 → ((列名, 声明), …)。缺任意一列都算「还没迁移完」的旧库。
    MIGRATION_COLUMNS = (
        # 所有权四列逐列幂等迁移：不能用 outcome 存在推断 owner 完整（manual-status 独立迁移）。
        ('jobs', (('routes', "TEXT NOT NULL DEFAULT ''"),
                  # version:1 的分阶段结果 JSON。旧行保持空串 = 不可用，不回填推测值。
                  ('outcome', "TEXT NOT NULL DEFAULT ''"),
                  ('ownership_version', 'INTEGER NOT NULL DEFAULT 0'),
                  ('owner_token', "TEXT NOT NULL DEFAULT ''"),
                  ('owner_dev', 'INTEGER NOT NULL DEFAULT 0'),
                  ('owner_ino', 'INTEGER NOT NULL DEFAULT 0'),
                  ('discovery', "TEXT NOT NULL DEFAULT ''"))),
        ('link_index', (('source_kind', "TEXT NOT NULL DEFAULT ''"),)),
        ('articles', (('source', "TEXT NOT NULL DEFAULT ''"),
                      ('link_ok', 'INTEGER NOT NULL DEFAULT 1'))),
    )

    def _missing_columns(self, connection):
        """还没建的列：``{表: [(列名, 声明), …]}``；都已迁移就是空 dict。

        只读 PRAGMA，不持任何锁：调用方可能拿这个结果去抢写锁（迁移慢路径），也可能只在
        锁外用它判一次「有没有活要干」（快路径）。
        """
        missing = {}
        for table, wanted in self.MIGRATION_COLUMNS:
            present = {row[1] for row in connection.execute(f'PRAGMA table_info({table})')}
            gap = [(column, declaration) for column, declaration in wanted if column not in present]
            if gap:
                missing[table] = gap
        return missing

    def _migrate_columns(self, connection):
        """逐列 ALTER 迁移，跨实例/跨连接串行化。

        ``self.lock`` 只是本实例的 RLock：同一工作区上另一个 Intel 实例、另一个进程各有各的
        锁，彼此看不见。两边在锁外读到同一份旧列集合，就会去 ALTER 同名列，后动手的那个拿到
        「duplicate column name: outcome」，迁移被判失败、``_ready`` 留假、这一轮恢复整个跳过。

        所以真正动 schema 的这一段放进 SQLite 写事务：``BEGIN IMMEDIATE`` 拿的是**文件级**写锁，
        跨连接、跨进程都成立，不会因为不在同一个 Python 锁里就形同虚设。拿到锁之后**重读**列集合
        再决定 ALTER 哪几列——等待期间另一个实例很可能已经把列补齐了，锁内重读正是避免重复
        ALTER 的那一半。只读锁外结果的快路径不碰写锁，免得每次进程启动都抢一次。

        锁抢不到（被别的写事务占满 busy_timeout）或库读不了，异常照旧往上抛，由调用方告警并
        fail-closed；只有「duplicate column 且重读确认列已被补齐」才允许重进事务再来一轮，
        其余 ALTER 失败一律不吞。
        """
        if not self._missing_columns(connection):
            return                       # 快路径：已迁移完，别为常态启动抢写锁
        for attempt in (1, 2):
            connection.execute('BEGIN IMMEDIATE')
            try:
                for table, gap in self._missing_columns(connection).items():
                    for column, declaration in gap:
                        connection.execute(f'ALTER TABLE {table} ADD COLUMN {column} {declaration}')
                connection.commit()
                return
            except sqlite3.OperationalError as exc:
                connection.rollback()
                # 只有「别人刚把同一列补上」才重试，且必须重读列集合确认已经补齐；
                # 其余（锁超时/只读库/真损坏）照旧抛出，不当成迁移成功。
                if attempt == 2 or 'duplicate column name' not in str(exc) or self._missing_columns(connection):
                    raise
        raise sqlite3.OperationalError('schema 迁移未完成')   # 两轮都没落地（不可达，留给兜底）


    # ── 客户端 ────────────────────────────────────────────────────────────
    def client(self):
        if self._client is None:
            if self.client_factory is not None:
                self._client = self.client_factory(self)
            else:
                from intel_wechat_mp import (BackendQuota, BodyThrottle, KeychainCredentials,
                                             WechatMpClient)
                quota = BackendQuota(Settings(self))
                self._client = WechatMpClient(KeychainCredentials(), quota, BodyThrottle())
        return self._client

    def quota(self):
        return self.client().quota

    # ── 订阅号 ────────────────────────────────────────────────────────────
    def subscribe(self, payload):
        """录入订阅号。号名搜索的结果必须由用户选定 fakeid，不自动认同名号。"""
        if not isinstance(payload, dict) or set(payload) - {'fakeid', 'name', 'note', 'origin'}:
            raise ValueError('录入订阅号包含无效字段')
        identifier = fakeid(payload.get('fakeid'))
        name = clean(payload.get('name'), NAME_LIMIT)
        note = clean(payload.get('note'), NOTE_LIMIT)
        origin = clean(payload.get('origin'), NAME_LIMIT)
        if not name:
            raise ValueError('请填写公众号名称')
        with self.db() as connection:
            connection.execute('''INSERT INTO subscriptions(fakeid,name,note,origin,added_at)
                VALUES(?,?,?,?,?) ON CONFLICT(fakeid) DO UPDATE SET
                name=CASE WHEN excluded.name!='' THEN excluded.name ELSE subscriptions.name END,
                note=excluded.note, origin=CASE WHEN excluded.origin!='' THEN excluded.origin ELSE subscriptions.origin END''',
                (identifier, name, note, origin, now()))
        return self.subscription(identifier)

    def subscription(self, identifier):
        with self.db() as connection:
            row = connection.execute('SELECT * FROM subscriptions WHERE fakeid=?',
                                     (fakeid(identifier),)).fetchone()
        return self._subscription_row(row) if row else None

    @staticmethod
    def _subscription_row(row):
        data = dict(row)
        data['id'] = data['fakeid']
        return data

    def cooldown_snapshot(self):
        """只读 settings 表拿两个冷却截止时间；不构造客户端、不读凭证。"""
        out = {}
        for key, name in ((BackendQuota.K_COOLDOWN, 'backend'), (self.K_SOGOU_COOLDOWN, 'sogou')):
            try:
                value = float(self.settings().get(key, '0') or 0)
            except (TypeError, ValueError):
                value = 0.0
            out[name] = int(value) if value > time.time() else 0
        return out

    def _attach_results(self, records):
        """给每个订阅号挂上「最近一次结果」。

        只从 jobs 表回读，不再维护第二份状态；定时轮次也写任务行，所以手动与定时共用同一份。
        """
        if not records:
            return records
        identifiers = [record['fakeid'] for record in records]
        with self.db() as connection:
            rows = connection.execute('SELECT * FROM jobs WHERE fakeid IN ('
                                      + ','.join('?' for _ in identifiers) + ') ORDER BY started_at DESC',
                                      identifiers).fetchall()
        latest = {}
        for row in rows:
            latest.setdefault(row['fakeid'], row)
        cooldowns = self.cooldown_snapshot()
        for record in records:
            record['last_result'] = self._last_result(latest.get(record['fakeid']), cooldowns)
        return records

    def _last_result(self, row, cooldowns=None):
        """把一条 jobs 行整理成页面能直接渲染的「最近结果」。"""
        if row is None:
            return None
        job = self._public_job(row)
        cooldowns = cooldowns or {}
        routes = {line['path']: line['text'] for line in job['routes']}
        cooling = []
        if cooldowns.get('backend'):
            cooling.append(f"后台接口冷却至 {stamp(cooldowns['backend'])}")
        if cooldowns.get('sogou'):
            cooling.append(f"搜狗冷却至 {stamp(cooldowns['sogou'])}")
        result = dict(job)
        result.update(source='scheduled' if job['mode'] == 'scheduled' else 'manual',
                      at=job['finished_at'] or job['started_at'],
                      cooldown_until=max(list(cooldowns.values()) + [0]),
                      cooldown_label='；'.join(cooling),
                      route_map=routes,
                      suggestion=self._suggestion(job, routes, cooldowns))
        return result

    @staticmethod
    def _suggestion(job, routes, cooldowns):
        """下一步建议：只说现在真正能做的事，不鼓励反复点抓取。"""
        text = f"{job.get('reason', '')} {job.get('detail', '')}"
        outcome = job.get('outcome') or {}
        saved = outcome.get('save') or {}
        body = outcome.get('body') or {}
        album = routes.get('album') or ''
        tips = []
        if job['status'] == 'credentials':
            tips.append('先重新登录公众号后台、更新凭证，再试一次。')
        if job['status'] in ('rate_limited', 'cooldown') or '200013' in text or '限流' in text:
            until = cooldowns.get('backend')
            tips.append((f"后台接口被限流，等到 {stamp(until)} 之前不用再点抓取" if until
                         else '后台接口被限流，短时间内再点也只会再被拦') + '；这期间最有用的是补一条合集链接。')
        if '验证码' in text:
            tips.append('搜狗这一路被验证码拦住，只能等冷却过去；想稳定拿到文章还是补合集最直接。')
        if any(word in album for word in ('没有', '尚未发现', '还没发现')):
            tips.append('发一篇该号文章的完整链接到「补充该号合集」，或直接粘贴合集链接（带 album_id）。')
        if job['status'] == 'interrupted':
            state = job.get('ownership_state')
            if state == 'unknown':
                # 身份残缺/锁文件缺失或被替换：闸门保持，不能给「再点一次就好」的误导建议。
                tips.append('这条中断记录的所有权身份无法确认（身份残缺，或锁文件缺失/被替换）；'
                            '页面不会自动放行新任务，请先检查本机锁目录，别反复点抓取。')
            elif state in ('held', 'released'):
                # v1：原执行者释放所有权后立即恢复，不再等 3600 秒。
                tips.append('这次任务已被中断（原执行者已释放所有权）；再点一次抓取即可，已入库的文章不会重复。')
            else:
                tips.append(f'这次被中断了；再点一次抓取即可，已入库的文章不会重复。'
                            f'进程被杀留下的「进行中」要等开始时间起 {STALE_JOB_SECONDS} 秒才会自动标为已中断。')
        if saved.get('state') == 'failed':
            tips.append('取到了列表但一条都没能保存；先看完整回执里的保存错误，确认数据库可写后再试。')
        if not tips and job['status'] in ('done', 'partial') and not job.get('added'):
            if int(saved.get('succeeded') or 0) > 0 and int(saved.get('new') or 0) == 0:
                # 全是复用：不是失败，别让人误以为「没新增就是抓取失败」。
                tips.append(f"本轮复用了已入库的 {int(saved.get('succeeded') or 0)} 篇，没有新增；"
                            f"要补老文章就补一条合集链接。")
            else:
                tips.append('这一轮没有新文章。如果这个号有合集，补一条合集链接后成功率最高。')
        if not tips and body.get('state') in ('partial', 'failed'):
            tips.append('列表已保存；正文没全部取到，可稍后再点一次抓正文。')
        return ' '.join(tips[:3])

    def subscriptions(self):
        with self.db() as connection:
            rows = connection.execute('''SELECT s.*, (SELECT COUNT(*) FROM articles a WHERE a.fakeid=s.fakeid) AS article_count
                FROM subscriptions s ORDER BY s.added_at DESC''').fetchall()
        return self._attach_results([self._subscription_row(row) for row in rows])

    def remove_subscription(self, identifier):
        """只解除订阅关系；已抓到的文章保留，不删除任何数据。"""
        identifier = fakeid(identifier)
        with self.db() as connection:
            connection.execute('DELETE FROM subscriptions WHERE fakeid=?', (identifier,))
        return {'fakeid': identifier, 'removed': True}

    # ── 三期：免后台取文章 ────────────────────────────────────────────────
    K_SOGOU_COOLDOWN = 'mp.sogou.cooldown_until'

    def sogou_cooldown_until(self):
        try:
            value = float(self.settings().get(self.K_SOGOU_COOLDOWN, '0') or 0)
        except (TypeError, ValueError):
            return 0
        return int(value) if value > time.time() else 0

    def set_sogou_cooldown(self):
        until = int(time.time()) + SOGOU_COOLDOWN_SECONDS
        self.settings().set(self.K_SOGOU_COOLDOWN, repr(until))
        return until

    def settings(self):
        if self._settings is None:
            self._settings = Settings(self)
        return self._settings

    def public_client(self):
        """合集/搜狗客户端。测试通过 public_factory 注入假实现，绝不出网。"""
        if self._public is None:
            if self.public_factory is not None:
                self._public = self.public_factory(self)
            else:
                from intel_public import PublicClient
                self._public = PublicClient()
        return self._public

    def albums(self, identifier=None):
        with self.db() as connection:
            if identifier:
                rows = connection.execute('SELECT * FROM albums WHERE fakeid=? ORDER BY found_at DESC',
                                          (fakeid(identifier),)).fetchall()
            else:
                rows = connection.execute('SELECT * FROM albums ORDER BY found_at DESC LIMIT 200').fetchall()
        return [dict(row) for row in rows]

    def remember_albums(self, identifier, found, found_from='', guard=None):
        """记录「这个号有哪些合集」。同一合集重复发现不会重复插入。

        guard 是可选的任务所有权校验（callable(connection)），在同一事务内先跑，
        保证迟到 owner 的合集登记写不进。
        """
        kept = 0
        with self.db() as connection:
            if guard is not None:
                guard(connection)
            for album_id in found:
                if not str(album_id).isdigit():
                    continue
                connection.execute('''INSERT INTO albums(fakeid,album_id,found_from,found_at) VALUES(?,?,?,?)
                    ON CONFLICT(fakeid,album_id) DO NOTHING''',
                    (identifier, str(album_id), found_from[:120], now()))
                kept += 1
        return kept

    def note_album(self, identifier, album_id, title, article_count, guard=None):
        """记录合集元信息。只在拿到非零值时覆盖，避免用空值抹掉已知信息。"""
        with self.db() as connection:
            if guard is not None:
                guard(connection)
            connection.execute('''INSERT INTO albums(fakeid,album_id,title,article_count,found_at) VALUES(?,?,?,?,?)
                ON CONFLICT(fakeid,album_id) DO UPDATE SET
                  title=CASE WHEN excluded.title!='' THEN excluded.title ELSE albums.title END,
                  article_count=CASE WHEN excluded.article_count>0 THEN excluded.article_count
                                     ELSE albums.article_count END''',
                (identifier, str(album_id), str(title or '')[:200], int(article_count or 0), now()))

    @staticmethod
    def complete_article_link(url, identifier):
        """只接受该号带校验串的完整文章链接，不凭同名或残缺 URL 猜测。"""
        try:
            link = article_url(url)
            parts = urlsplit(link)
            query = parse_qs(parts.query)
            if (parts.path != '/s' or query.get('__biz') != [identifier]
                    or len(query.get('chksm', [])) != 1 or not query['chksm'][0].strip()):
                return ''
            return link
        except (ValueError, KeyError, TypeError):
            return ''

    def local_album_links(self, identifier):
        """仅查本地三个来源，按优先级去重，最多两条；不初始化或扫描知识库。"""
        found = []
        def take(rows):
            for row in rows:
                link = self.complete_article_link(row[0], identifier)
                if link and link not in found:
                    found.append(link)
                if len(found) == 2:
                    break
        with self.db() as connection:
            take(connection.execute('SELECT link FROM link_index ORDER BY resolved_at DESC'))
        path = Path(self.store.workspace) / 'knowledge' / 'knowledge.db'
        if len(found) < 2 and path.is_file():
            # ro 避免写入业务库；缺表/旧库不阻断另外两个来源。
            try:
                with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)) as connection:
                    take(connection.execute("SELECT url FROM documents WHERE url LIKE '%mp.weixin.qq.com/%' ORDER BY modified DESC"))
            except sqlite3.DatabaseError:
                pass
        if len(found) < 2:
            with self.db() as connection:
                take(connection.execute('SELECT link FROM recommendation_sources WHERE fakeid=? ORDER BY seen_at DESC', (identifier,)))
        return found

    def discover_albums_from_links(self, identifier, links, max_reads=2, on_read=None, guard=None):
        """尚无合集时读取本地完整文章来发现合集。

        最多读 max_reads 篇，并且不超过 2。返回实际读取次数，失败的读取也计入。
        已有合集时返回 0，不再发请求。
        """
        if self.albums(identifier):
            return 0
        cap = 2 if type(max_reads) is not int else max(0, min(max_reads, 2))
        if cap == 0:
            return 0
        usable = []
        for candidate in links:
            link = self.complete_article_link(candidate, identifier)
            if link and link not in usable:
                usable.append(link)
            if len(usable) >= cap:
                break
        reads = 0
        for link in usable:
            reads += 1
            if on_read is not None:
                on_read(reads)
            try:
                parsed = self.client().fetch_article(link)
            except (ValueError, OSError, RateLimited, CredentialsMissing, CredentialExpired,
                    TaskDeadlinePassed):
                continue
            if parsed.get('biz') != identifier:
                continue
            found = discover_album_ids(parsed.get('raw') or '')
            if found:
                self.remember_albums(identifier, found, found_from=link, guard=guard)
                return reads
        return reads

    def album_items(self, identifier, wanted=HISTORY_PAGE_SIZE * MAX_PAGES_PER_RUN, max_requests=ALBUM_REQUEST_BUDGET,
                    guard=None, cancel=None, deadline=None):
        """主路：合集接口翻页，直到够数或没有下一页。

        返回逐合集台账：attempted/successful/failed/unattempted_albums。**成功空页算成功**
        （确实查到了、只是没有文章）；只有「确实尝试过的合集全部失败」才算全部失败。
        cancel/deadline 在每页请求前后检查：一旦取消/到时限，立即停，不再开新请求（也不算全部失败）。
        """
        client = self.public_client()
        # 真实 PublicClient 自带节流等待，取消/截止必须传进去：等待后、出网前它自查一次；
        # 假实现（测试桩）没有节流等待，不传，避免改坏固定签名的桩。
        gate = {'cancel': cancel, 'deadline': deadline} if isinstance(client, PublicClient) else {}
        catalog = self.albums(identifier)
        items, used, stop, stop_kind = [], 0, '', ''
        attempted = successful = failed = 0
        if not catalog:
            return {'items': [], 'requests': 0, 'stopped': '这个号还没发现任何合集', 'stop_kind': '',
                    'albums': 0, 'attempted_albums': 0, 'successful_albums': 0,
                    'failed_albums': 0, 'unattempted_albums': 0}
        stopped_early = False
        for album in catalog:
            if used >= max_requests or len(items) >= wanted:
                break
            if _cancel_requested(cancel):
                stop, stop_kind, stopped_early = '已取消，本轮不再请求合集', 'cancel', True
                break
            if _deadline_passed(deadline):
                stop, stop_kind, stopped_early = '到达时限，本轮不再请求合集', 'deadline', True
                break
            begin = None
            attempted += 1
            album_ok = False
            for _ in range(max_requests - used):
                if _cancel_requested(cancel):
                    stop, stop_kind, stopped_early = '已取消，本轮不再请求合集', 'cancel', True
                    break
                if _deadline_passed(deadline):
                    stop, stop_kind, stopped_early = '到达时限，本轮不再请求合集', 'deadline', True
                    break
                used += 1  # 失败的页请求也占本轮预算。
                try:
                    page = client.album_page(identifier, album['album_id'], *(begin or (None, None)), **gate)
                except AlbumStopped as exc:
                    used -= 1  # 节流等待后就被拦下，这次请求实际没有发出，不占预算。
                    stop = ('已取消，本轮不再请求合集' if exc.kind == 'cancel'
                            else '到达时限，本轮不再请求合集')
                    stop_kind, stopped_early = exc.kind, True
                    break
                except AlbumError as exc:
                    stop, stop_kind = str(exc), 'error'
                    break
                album_ok = True
                # guard 位置传入：测试里 note_album 可能被 *args 假实现替换。
                self.note_album(identifier, album['album_id'], page['title'] or album['title'],
                                page['article_count'] or album['article_count'], guard)
                for item in page['items']:
                    if not item['link']:
                        continue
                    item['source'] = 'album'
                    item['album_id'] = album['album_id']
                    items.append(item)
                # 请求返回后统一重取停止状态：等待/传输期间发生的取消与截止也要如实进
                # 台账（含末页，B3 · M1）。已取回的条目保留，不算请求失败。
                if _cancel_requested(cancel):
                    stop, stop_kind, stopped_early = '已取消，本轮不再请求合集', 'cancel', True
                elif _deadline_passed(deadline):
                    stop, stop_kind, stopped_early = '到达时限，本轮不再请求合集', 'deadline', True
                if stopped_early:
                    break
                if not page['continue_flag'] or not page['items']:
                    break
                last = page['items'][-1]
                begin = (last['msgid'], last['itemidx'])
                if used >= max_requests or len(items) >= wanted:
                    break
            if album_ok:
                successful += 1
            elif not stopped_early:
                failed += 1
            if stopped_early:
                break
        unattempted = max(0, len(catalog) - attempted)
        if used >= max_requests and stop == '':
            stop, stop_kind = f'本轮合集请求已达上限 {max_requests} 次，剩余留给下一轮', 'budget'
        kept = items[:wanted]
        # 可信身份 aid：合集条目带 msgid/itemidx 时与发现/后台用同一 aid，同一篇不再重复入库。
        self._stamp_identity_aid(identifier, kept)
        return {'items': kept, 'requests': used, 'stopped': stop, 'stop_kind': stop_kind,
                'albums': len(catalog), 'attempted_albums': attempted, 'successful_albums': successful,
                'failed_albums': failed, 'unattempted_albums': unattempted}

    @staticmethod
    def _stamp_identity_aid(identifier, items):
        """给列表条目补三路统一的可信身份 aid；无可信身份就不覆盖（保留来源自带 aid/链接指纹）。"""
        for item in items:
            aid = trusted_identity_aid(identifier, item.get('link'), item.get('msgid'), item.get('itemidx'))
            if aid:
                item['aid'] = aid
        return items

    def sogou_items(self, identifier, name, max_requests=SOGOU_REQUEST_BUDGET, cooldown=0):
        """补充最新：搜狗。**实测跳转必定被反爬拦住**，所以通常只拿得到标题与时间。"""
        if cooldown > 0:
            return {'items': [], 'requests': 0, 'blocked': True, 'cooldown_skip': True,
                    'stopped': f'搜狗冷却中，本轮跳过（至 {stamp(cooldown)}）'}
        client = self.public_client()
        if not name:
            return {'items': [], 'requests': 0, 'blocked': False, 'error': True,
                    'stopped': '没有号名，搜狗要求按名称搜索'}
        used = 1  # 搜索尝试固定占一次，无论成功或失败。
        try:
            hits = client.sogou_search(name)
        except SogouBlocked as exc:
            return {'items': [], 'requests': used, 'blocked': True, 'stopped': str(exc)}
        except (ValueError, OSError) as exc:
            return {'items': [], 'requests': used, 'blocked': False, 'error': True, 'stopped': str(exc)}
        # 只保留账号名与订阅号完全一致的结果：实测结果里混有他号文章。
        mine = [hit for hit in hits if hit['account'] == name]
        items, blocked, note = [], False, ''
        for hit in mine:
            if used >= max_requests:
                note = f'本轮搜狗请求已达上限 {max_requests} 次'
                break
            used += 1  # 先计尝试，验证码和网络错误同样占额度。
            link = ''
            try:
                link = client.sogou_resolve(hit['sogou_link'])
            except SogouBlocked as exc:
                note, blocked = str(exc), True
                break
            except (ValueError, OSError):
                pass  # 网络层失败不算验证码，如实记「没拿到链接」
            items.append({'title': hit['title'][:300], 'link': link,
                          'published_at': int(hit['published_at'] or 0), 'source': 'sogou',
                          'aid': '', 'link_ok': bool(link), 'album_id': ''})
        return {'items': items, 'requests': used, 'blocked': blocked, 'stopped': note,
                'scanned': len(hits), 'matched': len(mine)}

    # ── 二期 B：定时订阅（每日抓最新） ────────────────────────────────────
    SCHEDULED_ROUTES = {'album': '定时轮次不走合集（只走后台接口那一路）',
                        'sogou': '定时轮次不走搜狗（只走后台接口那一路）'}

    @staticmethod
    def _routes_detail(routes, extra=''):
        """三条路线 + 发现的结果拼成给人看的 detail。"""
        lines = [f"{PATH_LABELS[path]}：{routes[path]}" for path in PATH_ORDER if routes.get(path)]
        if routes.get('discovery'):
            lines.append(f"发现：{routes['discovery']}")
        if extra:
            lines.append(extra)
        return '；'.join(lines)

    @staticmethod
    def _scheduled_outcome(backend_state, backend_count=0, backend_stop='', backend_attempted=1, save=None,
                           album=None, album_count=0, album_stop='', album_attempted=0,
                           backend_requested=True, discovery_count=0, unique_new=None):
        """定时轮次的 outcome：合集 + 后台两路（定时不走搜狗），正文永远 not_requested。

        requested/attempted 必须随实际路由同步：后台没请求时 requested=False、attempted=0，
        不再出现 state=not_requested 却 requested=true/attempted=1 的自相矛盾台账。
        discovery_count 把发现已提交成果并入列表总计。
        """
        if not backend_requested:
            backend_attempted = 0
        routes = {'album': route_record(album or 'not_requested', bool(album),
                                        album_attempted, album_count, album_stop),
                  'sogou': route_record('not_requested', False),
                  'backend': route_record(backend_state, backend_requested,
                                          backend_attempted, backend_count, backend_stop)}
        list_state = reduce_list(routes)
        save = save or save_stage('not_attempted')
        body = body_stage('not_requested')
        # selected 必须满足 selected = attempted + not_attempted，不能固定 0。
        selected = int(save['attempted']) + int(save['not_attempted'])
        found = int(album_count or 0) + int(backend_count or 0) + int(discovery_count or 0)
        return build_outcome(list_state, found, selected, routes, save['state'], save, body['state'], body,
                             unique_new=unique_new)

    # ── 阶段 B：发现 / 合集 / 必要后台 ──────────────────────────────────────
    def _scheduled_discovery(self, record, job_id, routes, known, owner, cancel, deadline):
        """到期发现：同一 owner 事务预留尝试 → 跑 A → 提交可信结果。返回落库台账或 None。"""
        identifier, name = record['fakeid'], record['name']
        has_album = bool(self.albums(identifier))
        due, due_note = self._discovery_due(identifier, has_album)
        if not due:
            routes['discovery'] = self._discovery_route_text(None, due, due_note)
            known['discovery'] = None
            known['discovery_receipt'] = None
            return None
        # 先在同一 owner 事务预留尝试：失败也占机会，中断不重放（可能未真正请求）。
        # 同一事务里冻结任务 started_at 作为范围三态的参考时间，整轮不随墙钟漂移。
        # 漏 owner 不能降级：无条件 _guard_owner，由 legacy/v1 严格规则判断（v1 漏 token 直接拒绝）。
        with self.db() as connection:
            self._guard_owner(connection, job_id, owner)
            reference = self._frozen_reference(connection, job_id)
            self._reserve_discovery_attempt(connection, identifier, job_id, owner=owner,
                                            has_album=has_album)
        receipt, articles, windows, _ = self._discover(identifier, name, has_album,
                                                       cancel=cancel, deadline=deadline,
                                                       reference=reference)
        committed = None
        if articles:
            committed = self._commit_discovery(identifier, name, job_id, owner, receipt, articles, windows)
        routes['discovery'] = self._discovery_route_text(receipt, due, due_note, committed)
        known['discovery'] = committed
        known['discovery_receipt'] = receipt
        return committed

    @staticmethod
    def _need_backend(album):
        """只有「无合集」或「确实尝试过的合集全部失败」才需要后台第一页。

        部分成功、预算停止、取消/截止、未尝试都不是全部失败（不得回退后台）。
        """
        if album.get('stop_kind') in ('cancel', 'deadline'):
            # 取消/截止不是「全部失败」：停止后不得再开后台请求。
            return False
        if not album['albums']:
            return True
        attempted = int(album.get('attempted_albums') or 0)
        failed = int(album.get('failed_albums') or 0)
        unattempted = int(album.get('unattempted_albums') or 0)
        return attempted > 0 and failed >= attempted and unattempted == 0

    @staticmethod
    def _discovery_stop_state(known, cancel, deadline):
        """发现阶段是否已取消/到时限：返回 'cancelled' / 'timeout' / ''。"""
        receipt = known.get('discovery_receipt') or {}
        if _cancel_requested(cancel) or receipt.get('state') == 'cancelled':
            return 'cancelled'
        if _deadline_passed(deadline) or (receipt.get('state') == 'failed'
                                           and receipt.get('stop_kind') == 'deadline'):
            return 'timeout'
        return ''

    def _stop_after_discovery(self, record, job_id, routes, known, owner, stop):
        """发现阶段被取消/到时限：不再开合集/后台请求，保留已提交成果并如实收口。"""
        word = '已取消' if stop == 'cancelled' else '到达时限'
        routes['album'] = f'{word}，本轮不再请求合集'
        routes['sogou'] = '定时轮次不走搜狗'
        routes['backend'] = f'{word}，本轮不再请求后台'
        discovery = known.get('discovery') or {}
        found = int(discovery.get('new') or 0) + int(discovery.get('existing') or 0)
        added = int(discovery.get('new') or 0)
        unique_new = self._unique_new_count(known)
        self._finish_job(job_id, 'done', '', routes=routes, detail=self._routes_detail(routes),
                         found=found, added=added,
                         outcome=self._scheduled_outcome('not_requested', 0, '', 0,
                                                         backend_requested=False,
                                                         discovery_count=found, unique_new=unique_new),
                         discovery=known.get('discovery_receipt'), owner=owner)
        return 'stop', {'state': stop, 'detail': f'{word}，本轮已停止，已保存的成果保留',
                        'listed': found, 'new_articles': added}

    def _scheduled_backend(self, record, job_id, routes, need_backend, owner, cancel=None, deadline=None,
                           known=None):
        """必要后台：无合集/全部失败才打第一页；冷却/凭证只挡这一路，不挡公开路径。

        返回 (items, info)：info 是**实际请求台账** {'requested','state','stop'}，
        调用方据此记 outcome，不再从「路由有必要」倒推「实际发了请求」（B3 · M2）。
        真正调用 appmsg_list 前先在 known 里登记 backend_attempted，异常收口才分得清
        「请求发出去了才失败」和「根本没走到后台」。
        """
        info = {'requested': False, 'state': 'not_requested', 'stop': ''}
        if not need_backend:
            routes['backend'] = '合集已覆盖，本轮不打后台'
            return [], info
        if _cancel_requested(cancel):
            routes['backend'] = '已取消，本轮不再请求后台'
            return [], info
        if _deadline_passed(deadline):
            routes['backend'] = '到达时限，本轮不再请求后台'
            return [], info
        until = self.client().quota.cooldown_until()
        if until:
            routes['backend'] = f'冷却中，本轮未发后台请求（至 {stamp(until)}）'
            info['state'], info['stop'] = 'blocked', 'cooldown'
            return [], info
        if known is not None:
            known['backend_attempted'] = True  # 实际要发请求了；异常也算尝试过
        # 任务期限贯穿到这一次后台 HTTP：min(BACKEND_TIMEOUT=40, 剩余期限)，不突破既有预算。
        # 不替换共享客户端的任何对象，只给本次调用设期限（R09）。
        with request_deadline(deadline):
            try:
                items = list(self.client().appmsg_list(record['fakeid'], 0, LATEST_PAGE_SIZE))
            except TaskDeadlinePassed as exc:
                # 期限在冷却等待／取凭据里耗尽：一个请求都没发出去，如实记超时，
                # 绝不能因为把超时压到 0.01 秒就报「后台正常完成」（M2）。
                routes['backend'] = str(exc)
                info['state'], info['stop'] = 'blocked', 'deadline'
                return [], info
        # 后台列表给的是长链：按 mid/idx 映射到与发现/合集一致的可信身份 aid。
        self._stamp_identity_aid(record['fakeid'], items)
        # 链接不合格的单条由客户端单独交上来（client.isolated_items）：只隔离这一条，
        # 正常条目照常入库，但要在回执里报出失败条数，不能让坏条目静默消失（M3）。
        isolated = list(getattr(self.client(), 'isolated_items', None) or ())
        if known is not None:
            known['backend_isolated'] = len(isolated)
        routes['backend'] = (f'取到 {len(items)} 篇'
                             + (f'，另 {len(isolated)} 条链接不合格已隔离' if isolated else ''))
        info['requested'], info['state'], info['stop'] = True, 'complete', 'ok'
        return items, info

    # ── 阶段 B：自动发现核心（消费 A 接口，只写内部事务）───────────────────
    def _discovery_tools(self):
        """返回 (provider, reader)。注入优先；否则用真实链路（remio→serper→自建）。

        reader 复用后台客户端读正文；provider 的 availability/密钥/网络全部由 A 模块负责，
        B 不读、不传、不记录任何密钥。测试一律注入假 provider，保证零真实请求。
        """
        if self.discovery_factory is not None:
            built = self.discovery_factory(self)
            if not built:
                return None, None
            return built.get('provider'), built.get('reader')
        from intel_link_discovery import ChainProvider
        client = self.client()
        return ChainProvider(), (lambda link: client.fetch_article(link))

    @staticmethod
    def _unconfigured_discovery_receipt(has_album):
        """未配置发现来源时的安全回执：state=skipped，绝不发请求。"""
        return {'version': DISCOVERY_RECEIPT_VERSION, 'provider': '', 'query': '',
                'freshness': 'pw' if has_album else 'pm', 'state': 'skipped',
                'stop_kind': 'provider_unavailable', 'search_attempted': 0, 'results_found': 0,
                'candidates_accepted': 0, 'reads_attempted': 0, 'verified': 0, 'biz_mismatch': 0,
                'read_failed': 0, 'budget': dict(DISCOVERY_BUDGET), 'album_ids': [],
                'verified_articles': [], 'candidates': [],
                'range_counts': {'within': 0, 'unknown': 0, 'outside': 0},
                'stop_note': DISCOVERY_UNCONFIGURED_NOTE}

    def _discovery_due(self, identifier, has_album):
        """是否该做一次发现：返回 (due, note)。

        无合集每轮一次（每轮只调用一次，天然一次）；已有合集按「上次尝试」节流 7 天。
        用 UTC 墙钟（跨重启存活）；上次尝试时间在未来（时钟回拨）保守跳过并给 clock_skew。
        """
        if not has_album:
            return True, ''
        with self.db() as connection:
            row = connection.execute('SELECT last_attempt_at FROM discovery_state WHERE fakeid=?',
                                     (identifier,)).fetchone()
        if row is None or not row['last_attempt_at']:
            return True, ''
        last = _parse_iso(row['last_attempt_at'])
        if last is None:
            return True, ''
        delta = (datetime.now(timezone.utc) - last).total_seconds()
        if delta < 0:
            return False, 'clock_skew'
        if delta >= DISCOVERY_ALBUM_INTERVAL_SECONDS:
            return True, ''
        return False, 'throttled'

    @staticmethod
    def _frozen_reference(connection, job_id):
        """读任务 started_at 作为冻结的 UTC 参考时间；缺失或坏格式退回 now()。"""
        row = connection.execute('SELECT started_at FROM jobs WHERE id=?', (job_id,)).fetchone()
        if row is not None:
            frozen = _parse_iso(row['started_at'])
            if frozen is not None:
                return frozen
        return datetime.now(timezone.utc)

    @staticmethod
    def _discovery_reservation(has_album):
        """预留尝试的安全回执：state=reserved，明说「已预留尝试，结果未知」，不当作已请求/成功。"""
        return {'version': DISCOVERY_RECEIPT_VERSION, 'provider': '', 'query': '',
                'freshness': 'pw' if has_album else 'pm', 'state': 'reserved',
                'stop_kind': 'reserved', 'search_attempted': 0, 'results_found': 0,
                'candidates_accepted': 0, 'reads_attempted': 0, 'verified': 0,
                'biz_mismatch': 0, 'read_failed': 0, 'budget': dict(DISCOVERY_BUDGET),
                'album_ids': [], 'verified_articles': [], 'candidates': [],
                'range_counts': {'within': 0, 'unknown': 0, 'outside': 0},
                'stop_note': DISCOVERY_RESERVED_NOTE}

    @staticmethod
    def _reserve_discovery_attempt(connection, identifier, job_id, owner=None, has_album=False):
        """在同一 owner 事务里预留一次发现机会：失败也占机会，中断不重放。

        同一事务里同时把安全 reservation 回执写进 jobs.discovery：预留后请求前崩溃，
        刷新页面能看到「已预留尝试，结果未知」，而不是空白或假成功；正常结束由 _finish_job 覆盖。
        """
        connection.execute('''INSERT INTO discovery_state(fakeid,last_attempt_at,last_job_id)
            VALUES(?,?,?) ON CONFLICT(fakeid) DO UPDATE SET
            last_attempt_at=excluded.last_attempt_at, last_job_id=excluded.last_job_id''',
            (identifier, now(), job_id))
        raw = _discovery_json(Intel._discovery_reservation(has_album))
        if owner:
            connection.execute('UPDATE jobs SET discovery=? WHERE id=? AND ownership_version=1 '
                               'AND owner_token=?', (raw, job_id, owner))
        else:
            # 与守卫同一谓词（严格 legacy），不留两套口径（Pi-L4）。
            connection.execute(f'UPDATE jobs SET discovery=? WHERE id=? AND {ownership.LEGACY_WHERE}',
                               (raw, job_id))

    @staticmethod
    def _deadline_reader(reader, client, cancel, deadline):
        """把 reader 包一层：读取前查取消/截止，并把底层 HTTP 超时压到剩余时间。

        真实链路里 reader 是 ``client.fetch_article``。期限只随**本次调用**走：
        ``request_deadline`` 把截止时刻放进当前调用上下文（contextvar，按线程/上下文隔离），
        底层 ``WechatMpClient`` 每次 HTTP 自己算 min(既有预算, 剩余期限)，保证「单 HTTP 耗时
        不超过剩余时间」。**绝不临时替换共享 client.opener**（R09）：那是共享对象，
        包裹窗口内共用同一 WechatMpClient 的非任务路径（identify_from_link、
        intel_recommend、collect_adapters）会继承被压小的超时。
        注入的假 reader（测试）不受影响，仍会做取消/截止检查；client 参数只为既有调用方保留。
        """
        def call(link):
            if _cancel_requested(cancel):
                raise DiscoveryStopped('发现读取前已请求取消')
            remaining = None if deadline is None else deadline - time.monotonic()
            if remaining is not None and remaining <= 0:
                raise DiscoveryStopped('发现读取前已到时限')
            with request_deadline(deadline):
                try:
                    return reader(link)
                except TaskDeadlinePassed:
                    # 读取途中期限到点（节流等待跨期）：按发现侧的停止词汇收口，
                    # 不当成一次读取失败，也不把已耗尽的期限当还能成功（M2）。
                    raise DiscoveryStopped('发现读取时已到时限') from None
        return call

    def _discover(self, identifier, name, has_album, cancel=None, deadline=None, reference=None):
        """跑一次 A 发现，返回 (receipt, articles, windows, range_counts)。

        receipt 是安全回执（不含正文包）；articles 是保存用结果包（完整 link/body/identity），
        只交给内部事务。范围三态由 reader 的 published_at 与**冻结的任务 UTC 时间**算出：
        reference 由调用方在认领时冻结（任务 started_at），整轮不随墙钟漂移；缺省才退回 now()。
        """
        from intel_link_discovery import discover_links
        provider, reader = self._discovery_tools()
        if provider is None or reader is None:
            receipt = self._unconfigured_discovery_receipt(has_album)
            return receipt, [], [], {'within': 0, 'unknown': 0, 'outside': 0}
        account = {'fakeid': identifier, 'name': name, 'has_album': has_album}
        # reader 每次读取前查取消/截止，并把底层 HTTP 超时压到剩余时间（单次读取不超过剩余）。
        reader = self._deadline_reader(reader, self.client(), cancel, deadline)
        receipt = discover_links(account, provider, reader, dict(DISCOVERY_BUDGET),
                                 cancel=cancel, deadline=deadline)
        if reference is None:
            reference = datetime.now(timezone.utc)
        freshness = receipt.get('freshness')
        articles = list(getattr(receipt, 'save_articles', []) or [])
        windows, range_counts = [], {'within': 0, 'unknown': 0, 'outside': 0}
        for article in articles:
            window = discovery_window(article.get('published_at'), freshness, reference)
            windows.append(window)
            range_counts[window] = range_counts.get(window, 0) + 1
        # 范围三态与逐篇窗口写进安全回执（数值与定义，不含正文包）。
        receipt['range_counts'] = dict(range_counts)
        for safe, window in zip(receipt.get('verified_articles') or [], windows):
            if isinstance(safe, dict):
                safe['window'] = window
        return receipt, articles, windows, range_counts

    def _commit_discovery(self, identifier, name, job_id, owner, receipt, articles, windows):
        """同一 owner 事务里落库发现成果：文章（可信 aid）、正文（非空才待确认）、合集。

        new/existing 以提交后台账计；remember 的合集不当作 new；已有成功正文不覆盖。
        """
        new = existing = bodies = 0
        dedupe = []
        new_keys = []
        album_ids = [str(a) for a in (receipt.get('album_ids') or []) if str(a).isdigit()]
        with self.db() as connection:
            # 漏 owner 不能降级：无条件 _guard_owner（v1 漏 token → 直接拒绝，不写业务）。
            self._guard_owner(connection, job_id, owner)
            for article, window in zip(articles, windows):
                identity = article.get('identity') or {}
                item = {
                    'link': article.get('link'),
                    'title': _bounded_text(article.get('title'), 240),
                    'published_at': _int_or(article.get('published_at'), 0),
                    'name': article.get('name') or name,
                    'source': 'discovery',
                    'aid': discovery_aid(identity, identifier),
                }
                article_id, created = self._upsert_article(identifier, item, job_id, connection)
                key = self._dedupe_key(item)
                dedupe.append(key)
                if created:
                    new += 1
                    new_keys.append(key)
                else:
                    existing += 1
                body = article.get('body')
                if isinstance(body, dict) and str(body.get('text') or '').strip():
                    if self._write_discovery_body(connection, article_id, article, body, name):
                        bodies += 1
            for album_id in album_ids:
                connection.execute('''INSERT INTO albums(fakeid,album_id,found_from,found_at)
                    VALUES(?,?,?,?) ON CONFLICT(fakeid,album_id) DO NOTHING''',
                    (identifier, album_id, 'discovery', now()))
        # dedupe：本轮发现已提交文章的身份键，供合集/后台汇总去重（Pi-L3）。
        # new_keys：本轮确实新建的那部分身份键，供「唯一新增文章数」计数（R10）。
        return {'new': new, 'existing': existing, 'bodies': bodies, 'albums': len(album_ids),
                'dedupe': dedupe, 'new_keys': new_keys}

    @staticmethod
    def _unique_new_count(known):
        """本轮唯一新增文章数（R10）：按入库身份键去重，跨来源命中同一篇只算一次。

        与「来源命中数」（outcome['list']['found']）分开：同一篇文章被发现/合集/后台
        各路命中时命中数会重复计数，唯一新增不会。没登记身份键的收口路径回退到
        已提交台账里的 added，不虚构。
        """
        keys = known.get('new_keys')
        if keys is not None:
            return len(set(str(key) for key in keys))
        return int(known.get('added') or 0)

    @staticmethod
    def _dedupe_key(item):
        """文章去重键，与 _upsert_article 的入库身份同口径：aid 优先，否则链接指纹。

        链接先过 accepted_link：不合格链接在入库时会清成占位，去重键也必须按
        「无链接」算 —— 否则 article_url 会对畸形链接（缺 mid 等）抛错，
        一条坏链接把整轮带崩（R01），且两种口径会算出不同键、同一篇可能重复入库。
        """
        aid = str(item.get('aid') or '').strip()[:64]
        if aid:
            return aid
        return article_fingerprint(accepted_link(item.get('link') or ''),
                                   f"{item.get('published_at', 0)}|{item.get('title', '')}")

    @staticmethod
    def _write_discovery_body(connection, article_id, article, body, name):
        """A 复用的正文：非空才写 awaiting_confirm；已有成功正文不覆盖。返回是否写入。"""
        row = connection.execute('SELECT body_status,body FROM articles WHERE id=?',
                                 (article_id,)).fetchone()
        if row is not None and row['body_status'] == 'awaiting_confirm' and str(row['body'] or '').strip():
            return False
        text = str(body.get('text') or '')
        images = body.get('images')
        images = [str(item) for item in images] if isinstance(images, list) else []
        connection.execute("UPDATE articles SET body=?,chars=?,images=?,name=?,"
                           "body_status='awaiting_confirm',body_at=?,error='' WHERE id=?",
                           (text, _int_or(body.get('chars'), len(text)),
                            json.dumps(images, ensure_ascii=False),
                            _bounded_text(article.get('name') or name, 200), now(), article_id))
        return True

    @staticmethod
    def _discovery_route_text(receipt, due, due_note, committed=None):
        """发现结果 → 一句给人看的话（route 文本；不含安全码细节）。"""
        if receipt is None:
            if not due:
                if due_note == 'clock_skew':
                    return '上次发现尝试时间在未来（本机时钟回拨），保守跳过本轮发现'
                return '已有合集，距上次发现尝试不足 7 天，本轮跳过发现'
            return '本轮未做发现'
        state = receipt.get('state')
        provider = receipt.get('provider') or '未配置'
        if state == 'skipped':
            return f'发现跳过（{provider} 全部不可用）'
        if state == 'failed':
            return f"发现失败（{receipt.get('stop_kind') or 'search_failed'}）"
        if state == 'cancelled':
            return '发现被取消'
        found = int(receipt.get('verified') or 0)
        extra = ''
        if committed:
            extra = (f"，新增 {committed['new']} 篇、复用 {committed['existing']} 篇、"
                     f"正文 {committed['bodies']} 篇、合集 {committed['albums']} 个")
        return f'发现 {provider} 验证 {found} 篇{extra}'

    def _discovery_capabilities(self):
        """只读：发现来源是否可用（不发起任何搜索）。"""
        provider, reader = self._discovery_tools()
        if provider is None or reader is None:
            return {'available': False, 'providers': []}
        try:
            caps = provider.capabilities()
        except Exception:  # noqa: BLE001
            caps = []
        if isinstance(caps, dict):
            caps = [caps]
        return {'available': any(c.get('available') for c in caps if isinstance(c, dict)),
                'providers': caps}

    def _claim_scheduled_job(self, record):
        """定时也走和手动 run 同一个单飞闸门，并返回持有 FD 的 handle。

        先持锁、再在同一个 BEGIN IMMEDIATE 事务里检查活跃任务与建任务；有任务在跑就返回 None
        （关闭本次候选 FD），这次定时算跳过，不排队、不重试。
        """
        self._recover_owned_jobs()
        token = ownership.new_token()
        job_id = uuid.uuid4().hex
        try:
            handle = ownership.acquire(ownership.normalize_root(self.workspace), token)
        except ownership.OwnershipBusy:
            # 清理期闸门被占用：另一任务在跑或正在收尾，本轮定时直接跳过。
            _LOG.info('情报定时任务：清理期闸门被占用（任务在跑或收尾中），本轮跳过')
            return None
        except ownership.OwnershipError as exc:
            # 锁目录/文件不满足安全前提：本轮不发请求、不建 v1 无锁行，如实记日志。
            _LOG.warning('情报定时任务无法取得所有权锁，本轮跳过：%s', exc)
            return None
        handle.job_id = job_id
        try:
            with self.db() as connection:
                busy = self.active_job(connection)
                if busy:
                    connection.rollback()
                    handle.close()
                    return None
                self._start_job(connection, record['fakeid'], record['name'], 'scheduled', job_id=job_id,
                                owner={'version': OWNERSHIP_VERSION, 'token': token,
                                       'dev': handle.dev, 'ino': handle.ino})
                connection.execute('UPDATE subscriptions SET last_run_at=?,last_status=? WHERE fakeid=?',
                                   (now(), 'running', record['fakeid']))
            # 交接前的最后一步也在同一个保护里：这里抛异常同样要关 handle（闸门不能漏）。
            self._release_settled_abandoned()
        except BaseException:
            handle.close()
            raise
        return handle

    def _record_scheduled_skip(self, record, status, reason, routes, outcome=None):
        """整轮没发请求也要留痕：写一条已结束的定时任务行，刷新页面后还能看到。"""
        with self.db() as connection:
            if self.active_job(connection):
                connection.rollback()
                return False
            moment = now()
            connection.execute('''INSERT INTO jobs(id,fakeid,name,mode,status,reason,detail,routes,outcome,started_at,finished_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?)''',
                               (uuid.uuid4().hex, record['fakeid'], record['name'], 'scheduled', status,
                                str(reason)[:300], self._routes_detail(routes, reason)[:2000],
                                json.dumps(routes, ensure_ascii=False)[:2000],
                                json.dumps(outcome, ensure_ascii=False)[:4000] if outcome else '',
                                moment, moment))
            connection.execute('UPDATE subscriptions SET last_run_at=?,last_status=? WHERE fakeid=?',
                               (moment, status, record['fakeid']))
        return True

    @staticmethod
    def _album_outcome(known):
        """合集台账 → outcome 的 album 路记录 (state, count, stop_kind, attempted)。"""
        album = known.get('album')
        if not album or not album.get('albums'):
            return None, 0, '', 0
        attempted = int(album.get('attempted_albums') or 0)
        count = len(album.get('items') or [])
        if album.get('stopped'):
            return 'partial', count, album.get('stop_kind') or 'error', attempted
        if int(album.get('successful_albums') or 0) > 0:
            return 'complete', count, '', attempted
        return 'failed', count, 'error', attempted

    @staticmethod
    def _exception_totals(known):
        """就地收口分支的统一台账（B3 · M2）。

        已提交的发现成果必计（found/added 与真实入库一致）；后台 requested/attempted
        只按实际发过的请求记——「路由有必要」不等于「实际发了请求」。
        """
        discovery = known.get('discovery') or {}
        discovery_new = int(discovery.get('new') or 0)
        discovery_found = discovery_new + int(discovery.get('existing') or 0)
        attempted = bool(known.get('backend_attempted'))
        return {'found': discovery_found, 'added': discovery_new,
                'unique_new': len(set(str(key) for key in discovery.get('new_keys') or ()) or ()),
                'requested': attempted, 'attempted': 1 if attempted else 0}

    def _scheduled_account(self, record, job_id, routes, known=None, owner=None, cancel=None, deadline=None):
        """认领之后的整段：公开路径（发现→合集）→ 必要后台 → 落库 → 统计 → 收口。

        顺序固定：公开 client.begin_round 每号一次 → 到期发现 → 提交可信结果 → 合集 →
        必要后台。限流/凭证/网络异常在这里就地收口；其他意外异常原样上抛，由 daily_latest
        统一收口，绝不把任务行留在 running。

        known 是进展台账：列表成功取到几篇、保存是否已提交、后台计数都写进去，
        这样后续任一步出意外时，收口仍能保留「列表已取到 N 篇」等已知成果。
        """
        identifier, name = record['fakeid'], record['name']
        try:
            # 公开路径每号一次：清预算、保留跨轮节流（新 client/新 provider 都不能绕产品预算）。
            begin_round = getattr(self.public_client(), 'begin_round', None)
            if begin_round is not None:
                begin_round()
            # ── 到期发现（先于合集：新发现的合集本轮立刻可用）──
            self._scheduled_discovery(record, job_id, routes, known, owner, cancel, deadline)
            # 发现阶段若已取消/到时限：停止后不得再开合集/后台请求，保留已提交成果。
            stop = self._discovery_stop_state(known, cancel, deadline)
            if stop:
                return self._stop_after_discovery(record, job_id, routes, known, owner, stop)
            # ── 合集（每页请求前后查取消/截止）──
            album = self.album_items(identifier, guard=self._owner_guard(job_id, owner),
                                     cancel=cancel, deadline=deadline)
            routes['sogou'] = '定时轮次不走搜狗'
            if not album['albums']:
                routes['album'] = '这个号还没发现任何合集'
            elif album['stopped']:
                routes['album'] = album['stopped']
            else:
                routes['album'] = f"{album['albums']} 个合集，取到 {len(album['items'])} 篇"
            known['album'] = album
            # 合集翻页中途被取消/到时限：不再打后台（_need_backend 已判否），本轮就此停止。
            if album.get('stop_kind') == 'cancel':
                stop = 'cancelled'
            elif album.get('stop_kind') == 'deadline':
                stop = 'timeout'
            # ── 必要后台：无合集，或确实尝试过的合集全部失败，才打后台第一页 ──
            need_backend = self._need_backend(album)
            backend_items, backend_info = self._scheduled_backend(record, job_id, routes, need_backend,
                                                                  owner, cancel=cancel, deadline=deadline,
                                                                  known=known)
            known['backend_requested'] = bool(backend_info['requested'])
            known['backend_count'] = len(backend_items)
            known['backend_state'] = backend_info['state']
            known['backend_stop'] = backend_info['stop']
            if backend_info['stop'] == 'deadline' and not stop:
                # 期限在后台这一路耗尽：没发出去的请求不算成功，本轮到此为止，已保存的成果保留。
                stop = 'timeout'
            items = list(album['items']) + list(backend_items)
        except RateLimited as exc:
            # 200013：立即停本轮，不重试；下一轮若仍未冷却会累计到 3 次转 6 小时冷却。
            routes['backend'] = f'{exc}（未重试）'
            totals = self._exception_totals(known)
            self._finish_job(job_id, 'rate_limited', str(exc), routes=routes,
                             detail=self._routes_detail(routes), found=totals['found'],
                             added=totals['added'],
                             outcome=self._scheduled_outcome('blocked', 0, 'rate_limited',
                                                             totals['attempted'],
                                                             album=self._album_outcome(known)[0],
                                                             backend_requested=totals['requested'],
                                                             discovery_count=totals['found'],
                                                             unique_new=totals['unique_new']),
                             discovery=known.get('discovery_receipt'), owner=owner)
            return 'stop', {'state': 'rate_limited', 'detail': f'{exc}；本轮已停止，未重试',
                            'cooldown_until': exc.until or self.client().quota.cooldown_until(),
                            'listed': totals['found'], 'new_articles': totals['added']}
        except CredentialsMissing as exc:
            routes['backend'] = str(exc)
            totals = self._exception_totals(known)
            self._finish_job(job_id, 'credentials', str(exc), routes=routes,
                             detail=self._routes_detail(routes), found=totals['found'],
                             added=totals['added'],
                             outcome=self._scheduled_outcome('blocked', 0, 'credentials',
                                                             totals['attempted'],
                                                             album=self._album_outcome(known)[0],
                                                             backend_requested=totals['requested'],
                                                             discovery_count=totals['found'],
                                                             unique_new=totals['unique_new']),
                             discovery=known.get('discovery_receipt'), owner=owner)
            return 'stop', {'state': 'credentials', 'detail': str(exc),
                            'listed': totals['found'], 'new_articles': totals['added']}
        except CredentialExpired as exc:
            routes['backend'] = str(exc)
            totals = self._exception_totals(known)
            self._finish_job(job_id, 'credentials', str(exc), routes=routes,
                             detail=self._routes_detail(routes), found=totals['found'],
                             added=totals['added'],
                             outcome=self._scheduled_outcome('blocked', 0, 'credentials',
                                                             totals['attempted'],
                                                             album=self._album_outcome(known)[0],
                                                             backend_requested=totals['requested'],
                                                             discovery_count=totals['found'],
                                                             unique_new=totals['unique_new']),
                             discovery=known.get('discovery_receipt'), owner=owner)
            return 'stop', {'state': 'credentials', 'detail': str(exc),
                            'listed': totals['found'], 'new_articles': totals['added']}
        except (ValueError, OSError) as exc:
            routes['backend'] = str(exc)
            totals = self._exception_totals(known)
            self._finish_job(job_id, 'error', f"{name or identifier} · {exc}",
                             routes=routes, detail=self._routes_detail(routes),
                             found=totals['found'], added=totals['added'],
                             outcome=self._scheduled_outcome('failed', 0, 'error',
                                                             totals['attempted'],
                                                             album=self._album_outcome(known)[0],
                                                             backend_requested=totals['requested'],
                                                             discovery_count=totals['found'],
                                                             unique_new=totals['unique_new']),
                             discovery=known.get('discovery_receipt'), owner=owner)
            return 'continue', {'detail': f"{name or identifier} · {exc}",
                                'listed': totals['found'], 'new_articles': totals['added']}
        # 列表确实查询成功：先记下这个事实，之后落库/收口再出意外也抹不掉。
        # Pi-L3：同一篇既被又被合集/后台返回时只计一次 —— 用与入库同口径的身份键去重，
        # 已提交的发现成果也不重复计入；新增数与入库行为不受影响。
        seen = {str(key) for key in (known.get('discovery') or {}).get('dedupe') or ()}
        distinct_items = []
        for item in items:
            key = self._dedupe_key(item)
            if key and key in seen:
                continue
            if key:
                seen.add(key)
            distinct_items.append(item)
        listed = len(distinct_items)
        known['listed'] = listed
        created_count = 0
        attempted_count = 0
        created_keys = []
        with self.db() as connection:
            # 同一事务先校验 owner：迟到 owner 的整批业务写直接拒绝，不先写业务后才发现失效。
            self._guard_owner(connection, job_id, owner)
            for item in distinct_items:
                item['name'] = name
                # 先记实际尝试：_upsert_article 自己抛异常时，这次调用也已经发生了。
                # 回滚收口时用这个台账，绝不把事务目标条数当成实际调用次数。
                attempted_count += 1
                known['attempted'] = attempted_count
                # 复用外层事务：_upsert_article 自己开事务会自锁。
                _, created = self._upsert_article(identifier, item, job_id, connection)
                created_count += int(created)
                if created:
                    created_keys.append(self._dedupe_key(item))
        # 事务提交成功后才把临时计数确认为成功；回滚不会保留「前两条保存成功」。
        # 列表查询成功（含 0 篇空列表）都算保存阶段达成目标，统一记 complete（prep §3 行3）。
        # 被隔离的坏单条计入 attempted+failed：不变量 attempted=succeeded+failed 仍成立，
        # 回执里能看到「保存失败 N 条」，而不是一条坏链接静默消失（M3）。
        isolated = int(known.get('backend_isolated') or 0)
        existing = listed - created_count
        save = save_stage('partial' if isolated else 'complete',
                          listed + isolated, listed, created_count, existing, isolated, 0)
        known['save'] = save
        known['added'] = created_count
        album_state, album_count, album_stop, album_attempted = self._album_outcome(known)
        backend_count = int(known.get('backend_count') or 0)
        discovery = known.get('discovery') or {}
        # 发现已提交成果并入 job/轮次总计：found/added 与详情里「新增 N 篇」一致，不再漏计。
        discovery_new = int(discovery.get('new') or 0)
        discovery_found = discovery_new + int(discovery.get('existing') or 0)
        found_total = listed + discovery_found
        added_total = created_count + discovery_new
        # 唯一新增文章数（R10）：本轮实际新建的文章身份键，跨来源命中同一篇只算一次。
        # 只在事务提交后登记，回滚不会留下假的新增键。
        known['new_keys'] = [str(key) for key in created_keys] + \
            [str(key) for key in discovery.get('new_keys') or ()]
        unique_new = self._unique_new_count(known)
        produced = bool(listed or discovery_new or int(discovery.get('albums') or 0))
        until = self.client().quota.cooldown_until()
        backend_requested = bool(known.get('backend_requested'))
        # 后台台账以 _scheduled_backend 的实际请求为准（含冷却零请求：blocked/0/cooldown）。
        backend_state = known.get('backend_state') or ('complete' if backend_requested else 'not_requested')
        backend_stop = known.get('backend_stop') or ('ok' if backend_requested else '')
        if stop:
            # 取消/截止：不再开新请求，已保存的成果保留，轮次回执如实写停止原因。
            word = '已取消' if stop == 'cancelled' else '到达时限'
            routes['backend'] = routes.get('backend') or f'{word}，本轮不再请求后台'
            self._finish_job(job_id, 'done', '', routes=routes, detail=self._routes_detail(routes),
                             found=found_total, added=added_total,
                             outcome=self._scheduled_outcome(backend_state, backend_count, backend_stop, 1,
                                                             save, album=album_state, album_count=album_count,
                                                             album_stop=album_stop,
                                                             album_attempted=album_attempted,
                                                             backend_requested=backend_requested,
                                                             discovery_count=discovery_found,
                                                             unique_new=unique_new),
                             discovery=known.get('discovery_receipt'), owner=owner)
            return 'stop', {'state': stop, 'detail': f'{word}，本轮已停止，已保存的成果保留',
                            'listed': found_total, 'new_articles': added_total}
        if until and not produced:
            # 后台冷却且公开路径没有任何产出：终态记 cooldown（只挡后台，公开路径已按纪律执行）。
            reason = (f'后台接口冷却中，本轮未发送任何后台请求；公开路径（发现/合集）已按纪律执行；'
                      f'冷却至 {stamp(until)}')
            self._finish_job(job_id, 'cooldown', reason, routes=routes,
                             detail=self._routes_detail(routes, reason),
                             outcome=self._scheduled_outcome('blocked', 0, 'cooldown',
                                                             album=album_state, album_count=album_count,
                                                             album_stop=album_stop,
                                                             album_attempted=album_attempted,
                                                             backend_requested=False,
                                                             discovery_count=discovery_found,
                                                             unique_new=unique_new),
                             discovery=known.get('discovery_receipt'), owner=owner)
            return 'continue', {'state': 'cooldown', 'detail': reason, 'cooldown_until': until}
        self._finish_job(job_id, 'done', '', routes=routes, detail=self._routes_detail(routes),
                         found=found_total, added=added_total,
                         outcome=self._scheduled_outcome(backend_state, backend_count, backend_stop, 1, save,
                                                         album=album_state, album_count=album_count,
                                                         album_stop=album_stop,
                                                         album_attempted=album_attempted,
                                                         backend_requested=backend_requested,
                                                         discovery_count=discovery_found,
                                                         unique_new=unique_new),
                         discovery=known.get('discovery_receipt'),
                         owner=owner)
        return 'continue', {'listed': found_total, 'new_articles': added_total}

    def _close_scheduled_round(self, job_id, record, routes, exc, receipt, known=None, owner=None):
        """认领之后任何一步出意外都要把任务行收口，不能留 running 把单飞闸门堵到超时。

        收口本身也失败（例如数据库不可写）时不吞错：记日志、在回执里写明恢复路径——
        v1 行在 owner FD 关闭后由下次检查按所有权 CAS 恢复，不再等 3600 秒；
        legacy 行仍按残留规则等到 started_at 起 STALE_JOB_SECONDS 秒被标为已中断。

        known 带已知成果：列表成功取到几篇、实际尝试保存几篇、保存是否已提交。这三样统一
        喂给三段回执、任务行基础计数与轮次回执，任何一步出意外都不丢也不虚构：
        保存阶段整批回滚时保留「列表已成功取到 N 篇」，保存按真实调用次数记 attempted
        （不是事务目标条数），未进入循环的记 not_attempted；收口自身在提交后失败时同样
        保留已提交成果与 job 的 found/added、外层轮次的 listed/new_articles。
        """
        known = known or {}
        routes['backend'] = f'未预期的错误：{type(exc).__name__}'
        reason = f'定时轮次出现未预期的错误：{type(exc).__name__}'
        listed = known.get('listed')
        committed = known.get('save')
        # 发现已提交成果同样并入异常收口：不能因为后面某一步抛错就丢掉已入库的发现成果与回执。
        discovery = known.get('discovery') or {}
        discovery_new = int(discovery.get('new') or 0)
        discovery_found = discovery_new + int(discovery.get('existing') or 0)
        found = added = 0
        if listed is None:
            # 列表都没取到：如实记失败，不编造数量（发现成果另行计入）。
            backend_state, backend_count, backend_stop = 'failed', 0, 'error'
            save = save_stage('not_attempted')
        else:
            backend_state, backend_count, backend_stop = 'complete', listed, 'ok'
            found = listed
            added = int(known.get('added') or 0)
            if committed is None:
                # 列表成功但保存事务整批回滚（未提交）：attempted 只算真实调用过的次数，
                # 未进入循环的条目记 not_attempted；保存全失败，不是「取列表失败」。
                attempted = min(int(known.get('attempted') or 0), listed)
                save = save_stage('failed', attempted, 0, 0, 0, attempted, listed - attempted)
            else:
                # 收口自身在保存提交之后失败：保留已提交成果，不重新假定取数失败。
                save = committed
                added = int(save.get('new') or 0)
        found += discovery_found
        added += discovery_new
        # 唯一新增文章数（R10）：发现成果已在自己的事务里提交，必定计入；
        # 列表那一批只在事务确实提交后才登记身份键，整批回滚时不会留下假新增。
        unique_new = len(set(str(key) for key in discovery.get('new_keys') or ()))
        if known.get('new_keys') is not None:
            unique_new = self._unique_new_count(known)
        try:
            self._finish_job(job_id, 'error', reason, routes=routes, detail=self._routes_detail(routes),
                             found=found, added=added,
                             outcome=self._scheduled_outcome(backend_state, backend_count, backend_stop, 1, save,
                                                             discovery_count=discovery_found,
                                                             unique_new=unique_new),
                             discovery=known.get('discovery_receipt'), owner=owner)
            closed = True
        except Exception as finish_error:  # noqa: BLE001
            closed = False
            _LOG.exception('情报定时任务 %s 收口失败：%s', job_id, finish_error)
        # 轮次回执也吃同一份 known 台账：异常提前退出时列表/新增成果照样计入本轮。
        receipt['listed'] += found
        receipt['new_articles'] += added
        receipt['state'] = 'error'
        if closed:
            recovery_note = ''
        elif owner:
            recovery_note = '；任务行收口也失败，但已释放所有权，下次检查时会按所有权恢复标为已中断'
        else:
            recovery_note = (f'；任务行收口也失败，会按残留规则等到开始时间起 '
                             f'{STALE_JOB_SECONDS} 秒后标为已中断')
        receipt['detail'] = (f"{record['name'] or record['fakeid']} · 未预期的错误：{type(exc).__name__}；本轮已停止"
                             + recovery_note)
        return receipt

    def daily_latest(self, run_id='', cancel=None, deadline=None):
        """一轮「已订阅号各抓最新 1 页」。严格走一期限流器，并与手动抓取共用单飞闸门。

        回执写明：处理号数、新文章数、限流/冷却/凭证失效状态。新文章只进 intel.db
        待确认，**不自动送回收站、也不进知识库**。每个号都留一条任务行，所以刷新页面
        后仍能看到这次为什么没抓到。
        """
        # 认领/跳过前先恢复已释放所有权的 v1（独立事务，不嵌进认领事务）。
        self._recover_owned_jobs()
        # 后台冷却不再整轮 return：冷却/凭证只挡后台那一路，公开路径（发现/合集）照常按纪律执行。
        receipt = {'accounts': 0, 'new_articles': 0, 'listed': 0, 'state': 'done', 'detail': ''}
        for record in self.subscriptions():
            if cancel is not None and cancel.is_set():
                receipt['state'] = 'cancelled'
                receipt['detail'] = '本轮已请求停止，已处理的号保留结果'
                break
            if deadline is not None and time.monotonic() >= deadline:
                receipt['state'] = 'timeout'
                receipt['detail'] = '本轮到达时限，剩下的号留到下一轮'
                break
            handle = self._claim_scheduled_job(record)
            if handle is None:
                # 已有任务在跑（手动或上一轮定时）：这次定时跳过，不排队、不重试，也不发请求。
                busy = self.active_job() or {}
                receipt['state'] = 'busy'
                receipt['detail'] = (f"已有抓取任务在进行中（「{busy.get('name') or busy.get('id') or '另一个任务'}」），"
                                     '本轮定时这次直接跳过，没有排队也没有重试，未发送任何请求')
                receipt['active_job'] = {'id': busy.get('id', ''), 'name': busy.get('name', ''),
                                         'status': busy.get('status', '')}
                break
            job_id, token = handle.job_id, handle.token
            routes = {}
            receipt['accounts'] += 1
            known = {}
            try:
                # 认领之后占着单飞闸门：请求、落库、统计、收口任何一步意外都不能把任务行留在 running。
                action, extra = self._scheduled_account(record, job_id, routes, known, owner=token,
                                                        cancel=cancel, deadline=deadline)
            except Exception as exc:  # noqa: BLE001
                self._close_scheduled_round(job_id, record, routes, exc, receipt, known, owner=token)
                break
            finally:
                # 每号 finally 覆盖正常/异常/收口失败：FD 只有一个关闭责任者。
                handle.close()
            receipt['listed'] += extra.get('listed', 0)
            receipt['new_articles'] += extra.get('new_articles', 0)
            for key in ('state', 'detail', 'cooldown_until'):
                if extra.get(key) is not None:
                    receipt[key] = extra[key]
            if action == 'stop':
                break
        if not receipt['detail']:
            receipt['detail'] = (f"处理 {receipt['accounts']} 个订阅号，列表 {receipt['listed']} 篇，"
                                 f"新增 {receipt['new_articles']} 篇待确认")
        return receipt

    def analyze_scheduled(self, run_id, task, cancel, config, deadline):
        """scheduled_tasks 的执行入口。返回 (status, exit_code, output_json)。"""
        receipt = self.daily_latest(run_id=run_id, cancel=cancel, deadline=deadline)
        status = {'done': 'succeeded', 'rate_limited': 'partial', 'cooldown': 'partial', 'busy': 'partial',
                  'credentials': 'partial', 'cancelled': 'cancelled', 'timeout': 'partial'}.get(receipt['state'], 'partial')
        return status, None, json.dumps(receipt, ensure_ascii=False)

    def search_accounts(self, query):
        return self.client().search_biz(query)

    def identify_from_link(self, url):
        """复用既有入口：文章反查并发现合集，合集链接无需请求即可补录。

        结果（时间、找到几个合集、这篇文章没合集）会留痕，刷新页面后仍能看到；
        只存结果，不存链接本身与 chksm 这类参数。
        """
        parts = urlsplit(str(url or '').strip())
        query = parse_qs(parts.query)
        has_chksm = len(query.get('chksm', [])) == 1 and bool(query['chksm'][0].strip())
        if 'album_id' in query:
            if (parts.scheme not in ('http', 'https') or parts.netloc != 'mp.weixin.qq.com'
                    or parts.path != '/mp/appmsgalbum' or len(query.get('__biz', [])) != 1
                    or len(query['album_id']) != 1 or not re.fullmatch(r'\d{8,30}', query['album_id'][0])):
                raise ValueError('请填写有效的该号合集链接（含 __biz 和 album_id）')
            identifier = fakeid(query['__biz'][0])
            count = self.remember_albums(identifier, query['album_id'], found_from=url)
            self.note_album_search(identifier, 'album', count, has_chksm)
            return {'fakeid': identifier, 'nickname': '', 'title': '合集入口', 'chars': 0,
                    'published_at': 0, 'url': url, 'albums_found': count,
                    'album_search': self.album_search(identifier)}
        declared = query.get('__biz')
        target = declared[0] if len(declared or []) == 1 else ''
        try:
            parsed = self.client().fetch_article(article_url(url))
        except (ValueError, OSError, RateLimited, CredentialExpired, CredentialsMissing) as exc:
            self.note_album_search(target, 'article', 0, has_chksm, f'{ALBUM_SEARCH_FAILED}：{exc}')
            raise
        if declared and declared != [parsed['biz']]:
            self.note_album_search(parsed['biz'], 'article', 0, has_chksm, '文章页的公众号与链接不一致，未记录')
            raise ValueError('文章页面公众号编号与链接不一致，未记录合集')
        found = discover_album_ids(parsed.get('raw') or '')
        count = self.remember_albums(parsed['biz'], found, found_from=url)
        self.note_album_search(parsed['biz'], 'article', count, has_chksm)
        return {'fakeid': parsed['biz'], 'nickname': parsed['nickname'],
                'title': parsed['title'], 'chars': parsed['chars'],
                'published_at': parsed['published_at'], 'url': parsed['url'], 'albums_found': count,
                'album_search': self.album_search(parsed['biz'])}

    def note_album_search(self, identifier, kind, found, has_chksm=False, detail=''):
        """留痕「上次试过发现合集，结果如何」。

        只存结果：链接本身（含 chksm）不存，凭证一律不落库。
        """
        try:
            key = fakeid(identifier) if identifier else ''
        except ValueError:
            key = ''
        with self.db() as connection:
            connection.execute('INSERT INTO album_searches(id,fakeid,kind,albums_found,has_chksm,detail,searched_at) '
                               'VALUES(?,?,?,?,?,?,?)',
                               (uuid.uuid4().hex, key, str(kind)[:20], int(found or 0),
                                1 if has_chksm else 0, str(detail)[:200], now()))
            # 每个号只留最近 20 条，够页面回看，也不会无限长。
            connection.execute('DELETE FROM album_searches WHERE fakeid=? AND id NOT IN '
                               '(SELECT id FROM album_searches WHERE fakeid=? ORDER BY searched_at DESC LIMIT 20)',
                               (key, key))
        return True

    def album_search(self, identifier):
        """刷新页面后还能看到的「发现合集」留痕，并区分本篇没合集 / 该号没合集。"""
        try:
            key = fakeid(identifier) if identifier else ''
        except ValueError:
            key = ''
        row, known = None, 0
        if key:
            with self.db() as connection:
                row = connection.execute('SELECT * FROM album_searches WHERE fakeid=? '
                                         'ORDER BY searched_at DESC LIMIT 1', (key,)).fetchone()
                known = connection.execute('SELECT COUNT(*) FROM albums WHERE fakeid=?', (key,)).fetchone()[0]
        if row is None:
            return {'attempted': False, 'albums_known': known, 'albums_found': 0, 'kind': '', 'at': '',
                    'at_label': '', 'has_chksm': False,
                    'note': '还没有用文章链接反查过合集。补一篇该号的完整文章链接，或直接粘贴合集链接。'}
        found = int(row['albums_found'] or 0)
        detail = str(row['detail'] or '')
        if detail.startswith(ALBUM_SEARCH_FAILED):
            note = f'上次想用它发现合集，但{detail}。'
        elif row['kind'] == 'album':
            note = f'上次粘贴的是合集链接：{"已记录 " + str(found) + " 个合集" if found else "这个合集之前已经记过了"}。'
        elif found:
            note = f'上次读的那篇文章里发现 {found} 个合集，抓取会直接用它们。'
        elif known:
            note = f'上次读的那篇文章里没有合集；这个号已记录 {known} 个合集，抓取会直接用它们。'
        else:
            note = '上次读的那篇文章里没有合集；这个号目前还没有任何合集记录（不等于这个号没有合集）。'
        if detail and not detail.startswith(ALBUM_SEARCH_FAILED):
            note = f'{note}（{detail}）'
        return {'attempted': True, 'albums_known': known, 'albums_found': found, 'kind': row['kind'],
                'at': row['searched_at'], 'at_label': label(row['searched_at']),
                'has_chksm': bool(row['has_chksm']), 'note': note}

    # ── 文章 ──────────────────────────────────────────────────────────────
    def articles(self, identifier=None, limit=100, offset=0):
        identifier = fakeid(identifier) if identifier else None
        limit, offset = max(1, min(int(limit), 300)), max(0, int(offset))
        query = ('SELECT a.*, (SELECT drop_id FROM articles d WHERE d.id=a.id AND d.drop_id!=\'\') AS drop_id '
                 'FROM articles a WHERE (? IS NULL OR a.fakeid=?) ORDER BY a.published_at DESC, a.added_at DESC LIMIT ? OFFSET ?')
        with self.db() as connection:
            rows = connection.execute(query, (identifier, identifier, limit, offset)).fetchall()
            total = connection.execute('SELECT COUNT(*) FROM articles WHERE (? IS NULL OR fakeid=?)',
                                       (identifier, identifier)).fetchone()[0]
        return {'articles': [self._article_row(row) for row in rows], 'total': total}

    @staticmethod
    def _article_row(row):
        data = dict(row)
        data.pop('body', None)  # 列表不返回正文全文
        data['images'] = json.loads(data.get('images') or '[]')
        data['published_label'] = stamp(data.get('published_at'))
        # 库里为了让 UNIQUE 成立存了占位串；对外一律还原成「没有链接」，
        # 并按 source 注明原因，页面才不会把它渲染成一个打不开的 href。
        return annotate_missing_link(data)

    def article(self, article_id):
        with self.db() as connection:
            row = connection.execute('SELECT * FROM articles WHERE id=?', (article_id,)).fetchone()
        if row is None:
            raise ValueError('文章不存在')
        data = dict(row)
        data['images'] = json.loads(data.get('images') or '[]')
        data['published_label'] = stamp(data.get('published_at'))
        return annotate_missing_link(data)

    def _upsert_article(self, identifier, item, job_id, connection=None):
        """fakeid + 链接双去重。已存在的行不会被后来的列表结果覆盖正文。

        传入 connection 时复用调用方的事务 —— 在已开启的事务里再开一个会自锁。
        不合格链接在算指纹之前清掉，这样会存成占位而不是把整条跳过。
        """
        item = dict(item)
        item['link'] = accepted_link(item.get('link'))
        aid = str(item.get('aid') or '').strip()[:64] or article_fingerprint(
            item.get('link') or '', f"{item.get('published_at', 0)}|{item.get('title', '')}")
        identifier_id = hashlib.sha256(f'{identifier}:{aid}'.encode()).hexdigest()[:32]
        if connection is not None:
            return self._write_article(connection, identifier, aid, identifier_id, item, job_id)
        with self.db() as own:
            return self._write_article(own, identifier, aid, identifier_id, item, job_id)

    def _save_article(self, identifier, item, job_id, owner):
        """手动 _execute 的落库入口：同一事务先校验 owner 身份，再写文章。

        owner 失效（任务已恢复/被别的 owner 接管）时不写业务数据 —— 不先写业务后才发现 finish 失效。
        保持逐条独立事务：单条失败回滚不影响已提交的前几条。
        """
        with self.db() as connection:
            self._guard_owner(connection, job_id, owner)
            return self._upsert_article(identifier, item, job_id, connection)

    def _guard_owner(self, connection, job_id, owner):
        """同一事务内校验任务仍 active 且 owner 身份匹配；失效抛 StaleOwnershipError。"""
        if owner:
            row = connection.execute(
                "SELECT status FROM jobs WHERE id=? AND ownership_version=1 AND owner_token=?",
                (job_id, owner)).fetchone()
        else:
            # 漏 token 不能降级 legacy：只认真正的严格 legacy 行（与公开投影同一判定）。
            row = connection.execute(
                "SELECT status FROM jobs WHERE id=? AND " + ownership.LEGACY_WHERE, (job_id,)).fetchone()
        if row is None or row['status'] not in self.ACTIVE:
            raise StaleOwnershipError('任务所有权已失效，不再写入')

    def _owner_guard(self, job_id, owner):
        """给 album/正文等内部开事务的 helper 用的守卫：在同一事务里校验 owner。"""
        def guard(connection):
            self._guard_owner(connection, job_id, owner)
        return guard

    @staticmethod
    def _write_article(connection, identifier, aid, identifier_id, item, job_id):
        real_link = item.get('link') or ''
        # articles.link 上有 UNIQUE 约束。搜狗只拿得到标题时没有链接，
        # 若真存空串，第二条无链接记录就会撞 UNIQUE 把整轮抓取带崩。
        # 所以无链接的行存一个**不可解析**的占位值，并用 link_ok=0 标明它不是链接。
        stored = real_link or f'sogou-no-link:{identifier}:{aid}'
        existing = connection.execute('SELECT id FROM articles WHERE link=? OR (fakeid=? AND aid=?)',
                                      (stored, identifier, aid)).fetchone()
        if existing:
            connection.execute('UPDATE articles SET title=?, name=CASE WHEN name!=\'\' THEN name ELSE ? END '
                               'WHERE id=?', (item['title'], item.get('name', ''), existing['id']))
            return existing['id'], False
        connection.execute('''INSERT INTO articles(id,fakeid,aid,link,name,title,published_at,job_id,added_at,source,link_ok)
            VALUES(?,?,?,?,?,?,?,?,?,?,?)''',
            (identifier_id, identifier, aid, stored, item.get('name', ''), item['title'],
             item.get('published_at', 0), job_id, now(), item.get('source', ''),
             1 if real_link else 0))
        return identifier_id, True

    def save_body(self, article_id, parsed, guard=None):
        """正文入库并标为「待确认」；绝不自动进知识库。guard 在同一事务内先校验 owner。"""
        with self.db() as connection:
            if guard is not None:
                guard(connection)
            row = connection.execute('SELECT * FROM articles WHERE id=?', (article_id,)).fetchone()
            if row is None:
                raise ValueError('文章不存在')
            name = parsed.get('nickname') or row['name'] or ''
            connection.execute('''UPDATE articles SET body=?,chars=?,images=?,name=?,body_status='awaiting_confirm',
                body_at=?,error='' WHERE id=?''',
                (parsed['text'], int(parsed['chars']), json.dumps(parsed['images'], ensure_ascii=False),
                 name, now(), article_id))
        return self.article(article_id)

    def fail_body(self, article_id, message, guard=None):
        with self.db() as connection:
            if guard is not None:
                guard(connection)
            connection.execute("UPDATE articles SET body_status='failed',error=? WHERE id=?",
                               (str(message)[:200], article_id))
        return self.article(article_id)

    def push_to_intake(self, article_id, intake):
        """送入回收站：调用既有创意回收接口。

        provenance 从一期的 `intel:wechat_mp:<fakeid>:<aid>` 换成通用采集模块的
        `collect:<平台>:<来源>:<编号>`（合同 collect-core 第 9 条）。
        旧前缀仍然**认得**（collect_core.parse_provenance），老数据不会变成脏数据。
        """
        from collect_core import legacy_provenance, provenance as build_provenance
        record = self.article(article_id)
        if record['body_status'] != 'awaiting_confirm' or not record['body']:
            raise ValueError('这篇文章还没有正文，先抓正文再送入回收站')
        marker = build_provenance('wechat_mp', record['fakeid'], record['aid'])
        reason = clean(f'情报采集 · 公众号「{record["name"]}」', NOTE_LIMIT)
        # 回收项编号沿用一期的算法：同一篇重复点不会产生第二条，
        # 换 provenance 格式也不会让已送过的文章变成第二条。
        drop_id = hashlib.sha256(
            legacy_provenance('wechat_mp', record['fakeid'], record['aid']).encode()).hexdigest()[:32]
        saved = intake.receive({'id': drop_id, 'kind': 'text', 'title': record['title'],
                                'text': record['body'], 'reason': reason})
        with self.db() as connection:
            connection.execute('UPDATE articles SET drop_id=? WHERE id=?', (saved['id'], article_id))
        return {'drop_id': saved['id'], 'provenance': marker, 'document_pending': False}

    def forget_intake_drop(self, drop_id):
        """回收项被删后清掉 articles.drop_id 痕迹。

        不然情报页会一直显示「已进回收站」，点开却找不到东西；清掉后同一篇可以重新送。
        """
        if not isinstance(drop_id, str) or not re.fullmatch(r'[a-f0-9]{32}', drop_id):
            raise ValueError('回收项编号格式无效')
        with self.db() as connection:
            return {'cleared': connection.execute("UPDATE articles SET drop_id='' WHERE drop_id=?",
                                                  (drop_id,)).rowcount}

    # ── 抓取任务 ──────────────────────────────────────────────────────────
    def jobs(self, limit=30):
        with self.db() as connection:
            rows = connection.execute('''SELECT j.*, s.name AS account_name FROM jobs j
                LEFT JOIN subscriptions s ON s.fakeid=j.fakeid ORDER BY j.started_at DESC LIMIT ?''',
                (max(1, min(int(limit), 100)),)).fetchall()
        return [self._public_job(row) for row in rows]

    def _public_job(self, row):
        keys = row.keys()
        raw_token = row['owner_token'] if 'owner_token' in keys else ''
        raw_dev = row['owner_dev'] if 'owner_dev' in keys else 0
        raw_ino = row['owner_ino'] if 'owner_ino' in keys else 0
        data = dict(row)
        # 所有权原信息（token/dev/ino）绝不外传：只给安全状态与版本，加一句人话。
        data.pop('owner_token', None)
        data.pop('owner_dev', None)
        data.pop('owner_ino', None)
        # 分类一律用原始列值：非法 version 不能先被降级成 0 再被当成 legacy。
        # 展示用的 ownership_version 只是安全整数，权威判定是 ownership_state。
        state = Intel._ownership_state(data.get('ownership_version'), raw_token, raw_dev, raw_ino,
                                       data.get('status'))
        data['ownership_version'] = ownership.safe_version(data.get('ownership_version'))
        if state == 'held':
            # 数据库 running 不等于已确认持锁：以实际锁文件身份核对为准（缺失/替换→unknown）。
            state = self._probe_ownership_state(raw_token, raw_dev, raw_ino)
        data['ownership_state'] = state
        data['ownership_note'] = Intel._ownership_note(state)
        data['status_label'] = STATUS_LABELS.get(data['status'], data['status'])
        data['mode_label'] = MODE_LABELS.get(data.get('mode'), data.get('mode') or '')
        # 主文案给人看，错误码只放在括号里；原文完整保留在 reason/detail。
        data['headline'] = humanize(data.get('reason') or data.get('detail'))
        data['at_label'] = label(data.get('finished_at') or data.get('started_at'))
        data['routes'] = Intel._route_lines(data.get('routes'))
        # 分阶段结果：空/坏 JSON/未知版本一律不可用，页面按 detail/reason 回退，不猜计数。
        data['outcome'] = Intel._parse_outcome(data.get('outcome'))
        data['outcome_available'] = data['outcome'] is not None
        # R10：「唯一新增文章数」单独投影，页面才能和「来源命中数」分开写。
        # 旧任务回执里没这个键：如实给 None，页面就不显示这组数字，不拿 added 列猜。
        data['unique_new_articles'] = Intel._unique_new_of(data['outcome'])
        # 发现安全回执：独立列，未知版本/坏 JSON 一律标不可用；UI 只做安全投影。
        raw_discovery = data.get('discovery')
        data['discovery'] = Intel._parse_discovery(raw_discovery)
        data['discovery_available'] = data['discovery'] is not None
        data['discovery_present'] = bool(raw_discovery)
        data['active'] = data['status'] in Intel.ACTIVE
        return data

    @staticmethod
    def _ownership_state(version, token, dev, ino, status):
        """安全的所有权状态：先做身份安全归类，不给原始 token/dev/ino。

        入参是数据库里的**原始列值**（任何 SQLite 类型都可能），归类交给
        ``ownership.classify``，与年龄清理用的 ``ownership.LEGACY_WHERE`` 同一判定：

        - 严格 legacy（version 0 且 token/dev/ino 三件套全空）→ legacy；
        - version 1 且身份合法：running/queued 暂标 held（再按实际锁核对），否则 released；
        - 其余（残缺/非法/未知版本/v0 混入 owner）→ unknown，页面保持闸门提示。
        """
        kind = ownership.classify(version, token, dev, ino)
        if kind == ownership.LEGACY:
            return 'legacy'
        if kind == ownership.V1:
            return 'held' if status in Intel.ACTIVE else 'released'
        return 'unknown'

    def _probe_ownership_state(self, token, dev, ino):
        """按实际锁文件核对 v1 active 行：held / released / unknown，绝不抛异常。"""
        try:
            state, handle = ownership.probe(
                ownership.normalize_root(self.workspace), token, dev, ino)
        except Exception:  # noqa: BLE001
            return 'unknown'
        if handle is not None:
            handle.close()
        if state == ownership.HELD:
            return 'held'
        if state == ownership.ACQUIRED:
            return 'released'
        return 'unknown'

    @staticmethod
    def _ownership_note(state):
        if state == 'unknown':
            return ('无法确认这个任务的所有权（身份残缺，或锁文件缺失/被替换），'
                    '页面不会自动放行新任务；请检查本机锁目录。')
        return ''

    @staticmethod
    def _unique_new_of(outcome):
        """outcome → 唯一新增文章数（R10）；回执里没有就返回 None，不拿别的字段猜。"""
        if not isinstance(outcome, dict):
            return None
        value = outcome.get('unique_new')
        if isinstance(value, bool) or value is None:
            return None
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return None
        return parsed if parsed >= 0 else None

    @staticmethod
    def _parse_outcome(raw):
        """outcome 文本 → dict；不可用（空/坏 JSON/未知版本/结构不符）返回 None。"""
        if not raw:
            return None
        try:
            parsed = json.loads(raw)
        except (TypeError, ValueError):
            return None
        if not isinstance(parsed, dict) or parsed.get('version') != OUTCOME_VERSION:
            return None
        for key, allowed in (('list', LIST_STATES), ('save', SAVE_STATES), ('body', BODY_STATES)):
            stage = parsed.get(key)
            if not isinstance(stage, dict) or stage.get('state') not in allowed:
                return None
        if not isinstance(parsed.get('summary'), str):
            return None
        return parsed

    @staticmethod
    def _parse_discovery(raw):
        """discovery 文本 → dict；不可用（空/坏 JSON/未知版本/结构不符）返回 None。"""
        if not raw:
            return None
        try:
            parsed = json.loads(raw)
        except (TypeError, ValueError):
            return None
        if not isinstance(parsed, dict) or parsed.get('version') != DISCOVERY_RECEIPT_VERSION:
            return None
        if parsed.get('state') not in DISCOVERY_STATES:
            return None
        return parsed

    @staticmethod
    def _route_lines(raw):
        """jobs.routes 里的 JSON → 页面可直接渲染的路线（发现 + 三路）。

        发现行放最前：定时/发现的既有测试与 detail 文案都按「三路在后」的顺序断言，
        且发现是这一轮最先走的路（发现 → 合集 → 必要后台）。
        """
        try:
            parsed = json.loads(raw or '{}')
        except (TypeError, ValueError):
            return []
        if not isinstance(parsed, dict):
            return []
        lines = []
        if parsed.get('discovery'):
            raw_text = str(parsed['discovery'])
            lines.append({'path': 'discovery', 'label': DISCOVERY_ROUTE_LABEL,
                          'text': DISCOVERY_STATE_TEXTS.get(raw_text, raw_text)})
        lines.extend({'path': path, 'label': PATH_LABELS[path], 'text': str(parsed[path])}
                     for path in PATH_ORDER if parsed.get(path))
        return lines

    def job(self, job_id):
        for row in self.jobs(200):
            if row['id'] == job_id:
                return row
        raise ValueError('抓取任务不存在')

    def _start_job(self, connection, identifier, name, mode, job_id=None, owner=None):
        """建 running 行。owner 为 None 时是 legacy（无所有权）；否则同步写 v1 身份。"""
        job_id = job_id or uuid.uuid4().hex
        owner = owner or {}
        connection.execute(
            'INSERT INTO jobs(id,fakeid,name,mode,status,started_at,ownership_version,owner_token,owner_dev,owner_ino) '
            'VALUES(?,?,?,?,?,?,?,?,?,?)',
            (job_id, identifier, name, mode, 'running', now(),
             int(owner.get('version', 0)), str(owner.get('token', '')),
             int(owner.get('dev', 0)), int(owner.get('ino', 0))))
        return job_id

    ACTIVE = ('queued', 'running')
    # 恢复路径要读的所有权列。这四列是 _ensure 的逐列 ALTER 加的：缺任意一列就是
    # 「还没迁移」的旧库，那种 schema 里不可能有 v1 行。
    OWNERSHIP_COLUMNS = ('ownership_version', 'owner_token', 'owner_dev', 'owner_ino')

    def _abandoned_ids(self):
        with _PROCESS_WORKERS_LOCK:
            return tuple(job_id for path, job_id in _PROCESS_ABANDONED
                         if path == str(self.path.resolve()))

    def _discard_abandoned(self, job_id):
        with _PROCESS_WORKERS_LOCK:
            _PROCESS_ABANDONED.discard((str(self.path.resolve()), job_id))

    def _release_settled_abandoned(self, connection=None):
        """已经离开 queued/running 的放弃行不再占着进程集合。仍在跑的留着，短宽限才认它们。

        调用方必须已经提交；run() 发现忙会回滚，不能在那次事务里改这个集合。
        """
        if connection is None:
            if not self._abandoned_ids():
                return
            with self.db() as own:
                self._release_settled_abandoned(own)
            return
        abandoned = self._abandoned_ids()
        if not abandoned:
            return
        rows = connection.execute(
            'SELECT id,status FROM jobs WHERE id IN (' + ','.join('?' for _ in abandoned) + ')',
            abandoned).fetchall()
        found = {row[0]: row[1] for row in rows}
        settled = [job_id for job_id in abandoned if found.get(job_id) not in self.ACTIVE]
        if not settled:
            return
        with _PROCESS_WORKERS_LOCK:
            path = str(self.path.resolve())
            for job_id in settled:
                _PROCESS_ABANDONED.discard((path, job_id))

    def _live_worker_ids(self):
        with _PROCESS_WORKERS_LOCK:
            live = {key for key, worker in self.workers.items() if worker.is_alive()}
            live.update(job_id for (path, job_id), worker in _PROCESS_WORKERS.items()
                        if path == str(self.path.resolve()) and worker.is_alive())
            live.difference_update(self._abandoned_ids())
        return tuple(live)

    def _recover_owned_jobs(self):
        """恢复 v1 任务：原所有权已结束（身份匹配的原锁无人持有）才 CAS 标中断。

        顺序：**先确保 schema 迁移完成** → 只读 v1 active 快照（不持 DB 写锁）→ 非阻塞打开原锁
        核身份（不 O_CREAT）→ 锁可取再有界 DB 事务重读并 CAS active+version+token+dev+ino。
        锁忙保持、缺失/替换/符号链接/无权 unknown 保持；不按年龄回收 v1、不重放旧外网请求。
        绝不抛异常影响调用方。

        为什么必须先迁移：status()/run()/定时这些入口都会在首次 _ensure 之前调用本函数，
        而旧库（第 13 轮之前的 intel.db）没有 owner_* 列，直接 SELECT 会报
        「no such column: owner_token」——8766 重启日志里那条「情报任务恢复：读取 v1 快照失败
        （no such column: owner_token），本轮不恢复」就是这么来的：既刷一条假告警，又把这一轮
        的恢复整个跳过。_ensure 在 _ready 置真之后自己会跑一次恢复，所以这里补完迁移就返回，
        既不重复扫一遍，也不会递归回 _ensure。
        """
        if not self.path.exists():
            # 库还没建（首次 _ensure 之前）：没有 v1 行可恢复，交给 _ensure 建库后再恢复。
            return
        if not getattr(self, '_ready', False):
            try:
                self._ensure()
            except Exception as exc:  # noqa: BLE001
                # 迁移本身失败（库损坏/被占）：如实告警并保持不恢复，fail-closed。
                _LOG.warning('情报任务恢复：schema 迁移未完成（%s），本轮不恢复', exc)
            return
        try:
            root = ownership.normalize_root(self.workspace)
            connection = self._connect(timeout=RECOVERY_DB_TIMEOUT, busy_timeout=RECOVERY_BUSY_TIMEOUT_MS)
            try:
                columns = {row[1] for row in connection.execute('PRAGMA table_info(jobs)')}
                if not set(self.OWNERSHIP_COLUMNS) <= columns:
                    # 兜底：连上的库仍缺所有权列（例：_ready 已置真后被人换上旧库）。
                    # 这种 schema 里不可能有 v1 行，属于「没有可恢复」而不是失败：不打告警，
                    # 等迁移那一路补齐列后下一轮再恢复。
                    return
                rows = connection.execute(
                    "SELECT id,fakeid,owner_token,owner_dev,owner_ino FROM jobs "
                    "WHERE ownership_version=1 AND status IN ('queued','running')").fetchall()
            finally:
                connection.close()
        except Exception as exc:  # noqa: BLE001
            _LOG.warning('情报任务恢复：读取 v1 快照失败（%s），本轮不恢复', exc)
            return
        for row in rows:
            try:
                state, handle = ownership.probe(
                    root, row['owner_token'], row['owner_dev'], row['owner_ino'])
            except Exception as exc:  # noqa: BLE001
                # 单条坏数据（残缺身份/文件系统异常）不能中断整个恢复扫描。
                _LOG.warning('情报任务恢复：%s 身份核对异常（%s），本轮跳过', row['id'], exc)
                continue
            if state != ownership.ACQUIRED:
                continue
            try:
                self._cas_recover(row)
            finally:
                handle.close()

    def _cas_recover(self, row):
        """有界事务里 CAS 标中断；DB busy/提交失败时不提前返回「已恢复」，锁 FD 由调用方 finally 关。

        返回 CAS 的 rowcount（1=本次收口，0=没抢到/已收口），供并发恢复测试判定唯一赢家。
        """
        changed = 0
        try:
            connection = self._connect(timeout=RECOVERY_DB_TIMEOUT, busy_timeout=RECOVERY_BUSY_TIMEOUT_MS)
            try:
                connection.execute('BEGIN IMMEDIATE')
                # 预留后崩溃：jobs.discovery 里留着 reservation 回执，恢复时给专门的「已预留尝试，结果未知」，
                # 不当作已请求或成功，也不自动重放。
                current = connection.execute('SELECT discovery FROM jobs WHERE id=?', (row['id'],)).fetchone()
                parsed = Intel._parse_discovery(current['discovery']) if current is not None else None
                reserved = bool(parsed and parsed.get('state') == 'reserved')
                if reserved:
                    changed = connection.execute(
                        "UPDATE jobs SET status='interrupted',finished_at=?,reason=?,detail=?,"
                        "outcome=CASE WHEN outcome='' THEN ? ELSE outcome END "
                        "WHERE id=? AND status IN ('queued','running') AND ownership_version=1 "
                        "AND owner_token=? AND owner_dev=? AND owner_ino=?",
                        (now(), f'{RECOVER_REASON}；{DISCOVERY_RESERVED_NOTE}',
                         f'{DISCOVERY_RESERVED_NOTE}（预留后中断，不自动重放）',
                         json.dumps(recovered_outcome(), ensure_ascii=False),
                         row['id'], row['owner_token'], row['owner_dev'], row['owner_ino'])).rowcount
                else:
                    changed = connection.execute(
                        "UPDATE jobs SET status='interrupted',finished_at=?,reason=?,"
                        "outcome=CASE WHEN outcome='' THEN ? ELSE outcome END "
                        "WHERE id=? AND status IN ('queued','running') AND ownership_version=1 "
                        "AND owner_token=? AND owner_dev=? AND owner_ino=?",
                        (now(), RECOVER_REASON, json.dumps(recovered_outcome(), ensure_ascii=False),
                         row['id'], row['owner_token'], row['owner_dev'], row['owner_ino'])).rowcount
                if changed:
                    connection.execute(
                        "UPDATE subscriptions SET last_run_at=?,last_status='interrupted' WHERE fakeid=?",
                        (now(), row['fakeid']))
                connection.commit()
            finally:
                connection.close()
        except sqlite3.Error as exc:
            _LOG.warning('情报任务恢复：%s 未能写回（%s），保持现状待下次检查', row['id'], exc)
            return 0
        return changed

    def active_job(self, connection=None):
        """当前是否已有抓取任务在跑。

        后台接口是**全局串行**的，所以哪怕是两个不同的号，也不该同时跑 ——
        第二个任务只会在限流器里干等 90 秒，既没有意义也更容易触发 200013。
        检查与建任务放在同一个 BEGIN IMMEDIATE 事务里，两个并发请求只有一个能建。
        """
        if connection is not None:
            # 本进程明确放弃的行用短宽限；其他行保留原有长宽限与存活线程保护。
            # 与调用方同一事务：status()/run() 提交后才生效，避免再开一个连接把自己锁死。
            # 恢复是独立写事务，不能在这里嵌套：调用方须在进入事务前先跑 _recover_owned_jobs。
            protected = self._live_worker_ids()
            abandoned = self._abandoned_ids()
            interrupt_stale_jobs(connection, LIVE_STALE_REASON, protected, abandoned)
            live_clause = (' OR id IN (' + ','.join('?' for _ in protected) + ')') if protected else ''
            # 短宽限排除只适用严格 legacy：v1 与身份未知行（含 v0 混入 owner）的闸门只看所有权，
            # 绝不因 abandoned 放行，也不能因为 version 碰巧是 0 就被排除。
            exclusion = (' AND (' + ownership.NOT_LEGACY_WHERE + ' OR id NOT IN ('
                         + ','.join('?' for _ in abandoned) + '))') if abandoned else ''
            row = connection.execute(
                f"SELECT * FROM jobs WHERE (status IN {self.ACTIVE}{live_clause}){exclusion} ORDER BY started_at LIMIT 1",
                (*protected, *abandoned)).fetchone()
            return dict(row) if row else None
        # 独立检查先恢复（含本进程已释放的 v1），再开事务选择。
        self._recover_owned_jobs()
        with self.db() as own:
            found = self.active_job(own)
        self._release_settled_abandoned()
        return found

    def _current_owned_job(self):
        """清理期闸门被占用时，回读当前在跑（或正在收尾）的任务，供 reused 返回。

        先恢复已释放所有权的 v1，再找活跃行；若活跃行为空（例如另一进程已写终态、
        仍在收尾），退回最近一条 v1 行，让调用方仍能把「同一个任务」还给页面。
        """
        self._recover_owned_jobs()
        with self.db() as connection:
            busy = self.active_job(connection)
            if busy:
                return self._public_job(busy)
            row = connection.execute(
                "SELECT * FROM jobs WHERE ownership_version=? ORDER BY started_at DESC LIMIT 1",
                (OWNERSHIP_VERSION,)).fetchone()
        return self._public_job(row) if row else None

    # 允许写进 jobs 的计数列白名单：不把任意 dict 塞进 counts 再 int()。
    COUNT_COLUMNS = ('found', 'added', 'bodies', 'pages')

    def _finish_job(self, job_id, status, reason='', routes=None, detail=None, outcome=None, discovery=None,
                    owner=None, **counts):
        """收口一条任务。owner 为 token 时按 v1 身份 CAS；owner 为 None 只认严格 legacy 行。

        漏传 token 绝不能降级 legacy：v1 行只被 ``ownership_version=1 AND owner_token=?`` 命中，
        迟到的旧 owner（CAS=0）既不改状态/计数，也不同步订阅时间。
        订阅回读与状态写回共用同一个前提：本次 CAS 确实命中（rowcount>0），且 owner 缺失时
        该行仍是严格 legacy（与公开投影同一判定）；否则订阅的 last_run_at/last_status 一律不动。
        discovery 是安全回执（结构化裁剪后序列化），与 outcome 分开存，不挤占其 4000 字符。
        """
        columns = ['status=?', 'reason=?', 'finished_at=?']
        values = [status, str(reason)[:300], now()]
        if routes is not None:
            columns.append('routes=?')
            values.append(json.dumps(routes, ensure_ascii=False)[:2000])
        if detail is not None:
            columns.append('detail=?')
            values.append(str(detail)[:2000])
        # 分阶段结果与 status 同一事务写，避免先收口再补详情时状态与结果不一致。
        if outcome is not None:
            columns.append('outcome=?')
            values.append(json.dumps(outcome, ensure_ascii=False)[:4000])
        # 发现安全回执独立列：结构化裁剪后序列化，绝不字符串硬截断破坏 JSON。
        if discovery is not None:
            columns.append('discovery=?')
            values.append(_discovery_json(discovery))
        for key, value in counts.items():
            if key not in self.COUNT_COLUMNS:
                continue
            columns.append(f'{key}=?')
            values.append(int(value))
        values.append(job_id)
        if owner:
            guard, guard_values = " AND ownership_version=1 AND owner_token=?", (owner,)
        else:
            guard, guard_values = " AND " + ownership.LEGACY_WHERE, ()
        with self.db() as connection:
            # status='interrupted' 的行不可被迟到的收口覆盖：status 与 outcome 一起受保护。
            cursor = connection.execute(
                f'UPDATE jobs SET {",".join(columns)} WHERE id=? AND status != \'interrupted\'{guard}',
                (*values, *guard_values))
            # 订阅上的最近状态只能跟着确实生效的收口走：成功/部分/失败/限流/中断都走这里。
            # CAS=0（迟到的旧 owner、错 token、漏 token 碰上 v1 行）不得同步旧统计或更新时间；
            # 保留 legacy 回读兼容时也要再确认那一行仍是严格 legacy。
            if cursor.rowcount > 0:
                read = ('SELECT fakeid,status FROM jobs WHERE id=?' if owner else
                        'SELECT fakeid,status FROM jobs WHERE id=? AND ' + ownership.LEGACY_WHERE)
                row = connection.execute(read, (job_id,)).fetchone()
                if row:
                    connection.execute('UPDATE subscriptions SET last_run_at=?,last_status=? WHERE fakeid=?',
                                       (now(), row['status'], row['fakeid']))
        if status not in self.ACTIVE:
            self._discard_abandoned(job_id)

    def _write_job_detail(self, job_id, detail, owner=None):
        guard, guard_values = (" AND ownership_version=1 AND owner_token=?", (owner,)) if owner else \
            (" AND " + ownership.LEGACY_WHERE, ())
        with self.db() as connection:
            connection.execute("UPDATE jobs SET detail=? WHERE id=? AND status != 'interrupted'" + guard,
                               (detail[:2000], job_id, *guard_values))

    def run(self, payload):
        """起一次抓取。合集优先 → 搜狗补最新 → 后台（默认关闭）。"""
        allowed = {'fakeid', 'mode', 'bodies', 'sogou', 'backend'}
        if not isinstance(payload, dict) or set(payload) - allowed:
            raise ValueError('抓取参数无效')
        identifier = fakeid(payload.get('fakeid'))
        mode = payload.get('mode')
        if mode not in ('latest', 'history', 'discovery'):
            raise ValueError('请选择抓最新或抓历史')
        bodies = payload.get('bodies', MAX_BODIES_PER_RUN)
        if type(bodies) is not int or not 0 <= bodies <= MAX_BODIES_PER_RUN:
            raise ValueError(f'每次最多抓 {MAX_BODIES_PER_RUN} 篇正文')
        record = self.subscription(identifier)
        if record is None:
            raise ValueError('请先录入这个订阅号')
        # 后台闸门只在后台那一路真正要发请求时才拦；公开接口有自己的冷却与预算。
        use_backend = payload.get('backend', False)
        use_sogou = payload.get('sogou', True)
        if type(use_backend) is not bool or type(use_sogou) is not bool:
            raise ValueError('抓取路线的开关必须是开或关')
        if mode == 'discovery':
            # 显式手动发现：固定不走搜狗/后台，正文也不在发现阶段抓（正文由 A 复用）。
            use_sogou = use_backend = False
            bodies = 0
        if use_backend:
            until = self.client().quota.cooldown_until()
            if until:
                pass  # 后台那一路在 _execute 里跳过；公开两路照常走
        # 先恢复 v1（独立写事务），再持锁认领：恢复不能嵌进下面的 BEGIN IMMEDIATE。
        self._recover_owned_jobs()
        token = ownership.new_token()
        job_id = uuid.uuid4().hex
        try:
            handle = ownership.acquire(ownership.normalize_root(self.workspace), token)
        except ownership.OwnershipBusy:
            # 清理期闸门被占用：另一任务在跑，或终态已写但旧 FD 仍在收尾。
            # 跨进程可见的闸门保证不会新建第二个任务；把现有任务当 reused 返回。
            existing = self._current_owned_job()
            if existing is not None:
                existing['reused'] = True
                return existing
            raise ValueError('已有抓取任务正在进行，请稍后再试')
        except ownership.OwnershipError as exc:
            # 锁目录/文件不满足安全前提：拒绝新任务，绝不降级无锁。
            raise ValueError(f'无法取得任务所有权锁：{exc}') from exc
        handle.job_id = job_id
        try:
            with self.db() as connection:
                # 同号（或全局）已有任务在跑就不新建，把现有的返回给页面。
                busy = self.active_job(connection)
                if busy:
                    connection.rollback()
                    existing = self._public_job(busy)
                    existing['reused'] = True
                    handle.close()
                    return existing
                self._start_job(connection, identifier, record['name'], mode, job_id=job_id,
                                owner={'version': OWNERSHIP_VERSION, 'token': token,
                                       'dev': handle.dev, 'ino': handle.ino})
                connection.execute('UPDATE subscriptions SET last_run_at=?,last_status=? WHERE fakeid=?',
                                   (now(), 'running', identifier))
        except Exception:
            handle.close()
            raise
        # 取锁之后到 worker 接手之前，任何一步意外（_release_settled_abandoned、Thread 构造、
        # 线程锁、启动失败收口）都必须关掉 handle；闸门 FD 不能漏到进程重启才恢复。
        handed_off = False
        try:
            self._release_settled_abandoned()
            worker = threading.Thread(target=self._execute,
                                      args=(job_id, identifier, record['name'], mode, bodies, use_sogou,
                                            use_backend, token, handle),
                                      name=f'intel-mp-{job_id[:8]}', daemon=True)
            start_error = None
            with _PROCESS_WORKERS_LOCK:
                self.workers[job_id] = worker
                key = (str(self.path.resolve()), job_id)
                _PROCESS_WORKERS[key] = worker
                try:
                    worker.start()
                except Exception as exc:
                    self.workers.pop(job_id, None)
                    _PROCESS_WORKERS.pop(key, None)
                    start_error = exc
            if start_error is not None:
                # 库写入放在放开线程锁之后，避免和 active_job 的「先库后锁」交叉。
                reason = '任务线程没有启动，已中断：' + str(start_error)
                failures = []
                for attempt in range(2):
                    try:
                        self._finish_job(job_id, 'interrupted', reason, owner=token)
                        break
                    except Exception as finish_error:
                        failures.append(str(finish_error))
                        _LOG.exception('情报任务 %s 启动失败后的写回失败（第 %s 次）', job_id, attempt + 1)
                        if attempt == 0:
                            time.sleep(START_FAILURE_RETRY_SECONDS)
                if failures:
                    # 独立尽力写入，不能让辅助记录失败覆盖原始启动异常；同样受 owner 条件保护。
                    # 与 legacy 一致，这里记录瞬时写回失败，即使行已被同一 owner 标为 interrupted。
                    try:
                        with self.db() as connection:
                            connection.execute(
                                "UPDATE jobs SET detail=?,reason=? WHERE id=? AND ownership_version=1 "
                                "AND owner_token=?",
                                (('启动失败写回异常：' + '；'.join(failures))[:2000], reason[:300],
                                 job_id, token))
                    except Exception:
                        _LOG.exception('情报任务 %s 启动失败原因也未能落库', job_id)
                # 启动失败：FD 只有 claim 者这一个关闭责任者（由下面 finally 关）；
                # 行若仍 running 由下次恢复 CAS 收口。
                raise ValueError(reason) from start_error
            handed_off = True  # worker 已接手，成为 FD 的唯一关闭责任者
        finally:
            if not handed_off:
                handle.close()
        return self.job(job_id)

    def wait_for(self, job_id, timeout=5):
        worker = self.workers.get(job_id)
        if worker is not None:
            worker.join(timeout)
        return self.job(job_id)

    def _execute_discovery(self, job_id, identifier, name, owner=None, handle=None):
        """手动显式发现（mode=discovery）：只做发现 + 提交可信结果，固定不走搜狗/后台。

        显式手动发现可绕 7 天频率，但仍在同一单飞闸门/硬预算/取消/截止之下。
        """
        finished = False
        # 手动发现没有外部取消/截止：给一个有限预算，保证 reader 单次超时也受剩余时间约束。
        deadline = time.monotonic() + MANUAL_DISCOVERY_DEADLINE_SECONDS
        try:
            begin_round = getattr(self.public_client(), 'begin_round', None)
            if begin_round is not None:
                begin_round()
            has_album = bool(self.albums(identifier))
            # 手动发现也在同一 owner 事务预留 attempt（绕过 7 天频率不等于不登记这次尝试）；
            # 预留同时写安全 reservation 回执，请求前崩溃也能在回执区看到「已预留尝试，结果未知」。
            with self.db() as connection:
                self._guard_owner(connection, job_id, owner)
                reference = self._frozen_reference(connection, job_id)
                self._reserve_discovery_attempt(connection, identifier, job_id, owner=owner,
                                                has_album=has_album)
            receipt, articles, windows, _ = self._discover(identifier, name, has_album,
                                                           cancel=None, deadline=deadline,
                                                           reference=reference)
            committed = None
            if articles:
                committed = self._commit_discovery(identifier, name, job_id, owner, receipt, articles, windows)
            state = receipt.get('state')
            status = 'partial' if state == 'skipped' else ('error' if state in ('failed', 'cancelled') else 'done')
            found = int(receipt.get('verified') or 0)
            added = int((committed or {}).get('new') or 0)
            detail = self._discovery_route_text(receipt, True, '', committed)
            outcome = self._scheduled_outcome('not_requested', 0, '', 0, save_stage('not_attempted'),
                                              backend_requested=False, unique_new=added)
            self._finish_job(job_id, status, detail[:300], routes={'discovery': detail}, detail=detail,
                             outcome=outcome, discovery=receipt, found=found, added=added, owner=owner)
            finished = True
        except Exception:  # noqa: BLE001
            if not finished:
                try:
                    self._finish_job(job_id, 'error', '发现任务结束写回失败', owner=owner)
                except Exception as finish_error:  # noqa: BLE001
                    _LOG.exception('情报发现任务 %s 收口失败：%s', job_id, finish_error)
        finally:
            with _PROCESS_WORKERS_LOCK:
                self.workers.pop(job_id, None)
                _PROCESS_WORKERS.pop((str(self.path.resolve()), job_id), None)
            if handle is not None:
                handle.close()

    def _execute(self, job_id, identifier, name, mode, bodies, use_sogou=True, use_backend=False,
                 owner=None, handle=None):
        """后台线程。三路取文章：合集 → 搜狗 → 后台（默认关闭）。

        公开接口与后台各有自己的限流器，互不影响。任何一路命中限制就只停那一路，
        绝不自动重试。回执写明每条文章来自哪一路。三段（列表/保存/正文）各记真实结果，
        终态由这三段推导，不再用「有列表来源就 done」。
        """
        if mode == 'discovery':
            return self._execute_discovery(job_id, identifier, name, owner, handle)
        wanted = HISTORY_PAGE_SIZE * MAX_PAGES_PER_RUN if mode == 'history' else LATEST_PAGE_SIZE
        added = done_bodies = probe_reads = body_reads = 0
        body_failed = body_eligible = body_unreadable = body_skipped = 0
        body_stopped = body_started = body_limit_hit = False
        save_attempted = save_succeeded = save_new = save_existing = save_failed = 0
        aborted = False
        route_attempted = False
        ordered = []
        body_cap = 0

        def record_probe(count):
            nonlocal probe_reads
            probe_reads = count
        paths, routes, notes, list_routes = {}, {}, [], {}
        merged = {}

        def absorb(items, source):
            for item in items:
                item.setdefault('source', source)
                item['name'] = name
                key = item.get('link') or f"{item.get('published_at', 0)}|{item.get('title', '')}"
                # 同一篇文章可能几条路都给：链接优先，没有链接的先用有链接的补上。
                if key in merged:
                    if not merged[key].get('link') and item.get('link'):
                        merged[key] = {**merged[key], **item, 'source': merged[key]['source']}
                    continue
                merged[key] = item
                paths.setdefault(item['source'], 0)
                paths[item['source']] += 1

        try:
            client = self.client()
            guard = self._owner_guard(job_id, owner)
            # run() 的 active_job 事务保证公开路径单飞；只清预算计数，保留节流时刻。
            begin_round = getattr(self.public_client(), 'begin_round', None)
            if begin_round is not None:
                begin_round()
            # ── 主路：合集 ──
            known = self.albums(identifier)
            links = []
            if not known:
                links = self.local_album_links(identifier)
                # 抓历史的文章页总预算是 MAX_BODIES_PER_RUN，探测读取也算进去。
                # 抓最新仍只受「最多两篇探测」和本次正文参数分别约束。
                probe_cap = min(2, MAX_BODIES_PER_RUN) if mode == 'history' else 2
                probe_reads = self.discover_albums_from_links(identifier, links, max_reads=probe_cap,
                                                              on_read=record_probe, guard=guard)
            route_attempted = True
            album = self.album_items(identifier, wanted=wanted, guard=guard)
            absorb(album['items'], 'album')
            # 回执必须写明每一路的情况，成功也要写，不能只写失败。
            # 每一路的结果单独存（routes），页面刷新后还能按路线分开看。
            if not album['albums']:
                routes['album'] = ('本地没有该号的完整文章链接，无法发现合集；可在回收站收下一篇该号文章后重试'
                                   if not known and not links else '已尝试本地完整文章链接，尚未发现合集；可手动补充该号文章或合集链接')
                list_routes['album'] = route_record('unavailable', True, album['requests'], 0, 'no_album')
            elif album['stopped']:
                routes['album'] = album['stopped']
                # 合集接口报错（如 ret=10004 合集不存在）是「受阻/未能完整查询」，不是硬失败；
                # 预算上限同理。真正的中止由上面的 aborted 分支判为列表失败。
                list_routes['album'] = route_record('partial', True, album['requests'],
                                                    len(album['items']), album.get('stop_kind') or 'error')
            else:
                routes['album'] = f"{album['albums']} 个合集，取到 {len(album['items'])} 篇"
                list_routes['album'] = route_record('complete', True, album['requests'], len(album['items']))

            # ── 补充：搜狗 ──
            if use_sogou:
                sogou_cooldown = self.sogou_cooldown_until()
                route_attempted = True
                sogou = self.sogou_items(identifier, name, cooldown=sogou_cooldown)
                absorb(sogou['items'], 'sogou')
                if sogou.get('scanned') is not None:
                    sogou_note = f"{sogou['scanned']} 条结果里 {sogou['matched']} 条确属本号"
                else:
                    sogou_note = ''
                if sogou['stopped']:
                    sogou_note = f"{sogou_note}；{sogou['stopped']}" if sogou_note else sogou['stopped']
                if sogou['blocked'] and not sogou_cooldown:
                    self.set_sogou_cooldown()
                    sogou_note += '（已进入 6 小时冷却）'
                routes['sogou'] = sogou_note or '没有取到结果'
                if sogou.get('error'):
                    list_routes['sogou'] = route_record('failed', True, sogou['requests'], len(sogou['items']), 'error')
                elif sogou.get('blocked'):
                    kind = 'cooldown' if sogou.get('cooldown_skip') else 'captcha'
                    list_routes['sogou'] = route_record('blocked', True, sogou['requests'], len(sogou['items']), kind)
                else:
                    list_routes['sogou'] = route_record('complete', True, sogou['requests'], len(sogou['items']))
            else:
                routes['sogou'] = '已关闭'
                list_routes['sogou'] = route_record('not_requested', False)

            # ── 第三路：后台（默认关闭）──
            if use_backend:
                until = self.client().quota.cooldown_until()
                if until:
                    routes['backend'] = f'冷却中，跳过（至 {stamp(until)}）'
                    list_routes['backend'] = route_record('blocked', True, 0, 0, 'cooldown')
                else:
                    try:
                        self.client().credentials_ready()
                        pages = 1 if mode == 'latest' else MAX_PAGES_PER_RUN
                        size = LATEST_PAGE_SIZE if mode == 'latest' else HISTORY_PAGE_SIZE
                        backend_items = []
                        isolated = []
                        route_attempted = True
                        for page in range(pages):
                            backend_items.extend(self.client().appmsg_list(identifier, page * size, size))
                            isolated.extend(getattr(self.client(), 'isolated_items', None) or ())
                        # 后台长链按 mid/idx 映射到与发现/合集一致的可信身份 aid。
                        self._stamp_identity_aid(identifier, backend_items)
                        # 坏单条只隔离这一条并计入保存失败；正常条目照常入库（M3）。
                        if isolated:
                            save_attempted += len(isolated)
                            save_failed += len(isolated)
                            notes.append(f'后台列表另有 {len(isolated)} 条链接不合格，已隔离未入库')
                        absorb(backend_items, 'backend')
                        routes['backend'] = (f'取到 {len(backend_items)} 篇'
                                             + (f'，另 {len(isolated)} 条链接不合格已隔离' if isolated else ''))
                        list_routes['backend'] = route_record('complete', True, pages, len(backend_items))
                    except RateLimited as exc:
                        routes['backend'] = f'{exc}（未重试）'
                        list_routes['backend'] = route_record('blocked', True, 1, 0, 'rate_limited')
                    except (CredentialsMissing, CredentialExpired) as exc:
                        routes['backend'] = str(exc)
                        list_routes['backend'] = route_record('blocked', True, 1, 0, 'credentials')
                    except TaskDeadlinePassed as exc:
                        # 期限在冷却/取凭据里耗尽，一个请求都没发出去：如实记超时（M2）。
                        routes['backend'] = str(exc)
                        list_routes['backend'] = route_record('blocked', True, 0, 0, 'deadline')
                    except (ValueError, OSError) as exc:
                        routes['backend'] = str(exc)
                        list_routes['backend'] = route_record('failed', True, 1, 0, 'error')
            else:
                routes['backend'] = '已关闭（可用页面开关打开）'
                list_routes['backend'] = route_record('not_requested', False)

            # ── 合并去重、按时间排序、入库 ──
            ordered = sorted(merged.values(), key=lambda i: i.get('published_at', 0), reverse=True)[:wanted]
            skipped = 0
            for item in ordered:
                save_attempted += 1
                try:
                    _, created = self._save_article(identifier, item, job_id, owner)
                    added += int(created)
                    save_succeeded += 1
                    if created:
                        save_new += 1
                    else:
                        save_existing += 1
                except (ValueError, sqlite3.Error):
                    # 一条坏链接不能把整轮抓取带崩：记下来，继续下一条。
                    skipped += 1
                    save_failed += 1
                except Exception:
                    # 未预期的保存异常：这一条同样记一次 failed（保证 attempted=succeeded+failed），
                    # 整轮中止，剩余条目记未处理，已提交的成果保留。
                    save_failed += 1
                    raise
            if skipped:
                notes.append(f'有 {skipped} 条链接无法识别，已跳过（其余照常入库）')

            # ── 正文：只抓真正有链接的 ──
            # 抓历史时，探测和正文共用文章页总预算。失败的读取也占名额。
            body_cap = bodies
            if mode == 'history':
                body_cap = min(bodies, max(0, MAX_BODIES_PER_RUN - probe_reads))
            body_rows = self.articles(identifier, limit=300)['articles']
            body_started = True
            for row in body_rows:
                if row['body_status'] != 'pending':
                    continue
                if not row['link']:
                    body_unreadable += 1
                    continue
                if body_limit_hit:
                    # 限流已停：剩下的可读未读只统计范围，不再发请求（外网失败不算预算跳过）。
                    body_eligible += 1
                    continue
                if body_reads >= body_cap:
                    # 正常预算用尽：剩下的可读未读既算范围也算预算内未选中。
                    body_eligible += 1
                    if body_cap > 0:
                        body_skipped += 1
                    continue
                body_reads += 1
                try:
                    self.save_body(row['id'], client.fetch_article(row['link']), guard=guard)
                    done_bodies += 1
                except RateLimited as exc:
                    # 停止的这次尝试照常占预算并计入失败；继续扫描只为统计剩余可读范围，不再重试。
                    notes.append(f'{exc}；已停止，不再重试')
                    body_stopped = True
                    body_limit_hit = True
                    body_failed += 1
                except (ValueError, OSError) as exc:
                    self.fail_body(row['id'], str(exc), guard=guard)
                    body_failed += 1
        except (RateLimited, CredentialsMissing, CredentialExpired, TaskDeadlinePassed, ValueError, OSError) as exc:
            notes.append(str(exc))
            aborted = True
        except Exception as exc:  # noqa: BLE001
            # 工作线程里的意外异常绝不能静默死掉：那会让任务永远停在 running。
            notes.append(f'抓取过程中出现未预期的错误：{type(exc).__name__}；已保留已入库的结果')
            aborted = True

        # ── 三段结果与终态 ──
        # 意外中止且没有任何一路查询成功：列表阶段判失败，终态 error（不是「有来源就完成」）。
        # 但如果连一个请求都没发出去（例如客户端都建不起来），那是「未能完整查询」→ partial，
        # 不能说成「请求全部失败」。
        if not aborted:
            list_state = reduce_list(list_routes)
        elif any(record['state'] == 'complete' for record in list_routes.values()):
            list_state = reduce_list(list_routes)
        elif route_attempted:
            list_state = 'failed'
        else:
            list_state = 'partial'
        save_not_attempted = max(0, len(ordered) - save_attempted)
        if list_state == 'complete' and not ordered:
            # 确实成功查询空列表：保存目标 0 已完成，统一记 complete（prep §3 行3）。
            save_state = 'complete'
        else:
            save_state = reduce_save(save_attempted, save_succeeded, save_failed, save_not_attempted)
        if not body_started:
            # 正文循环还没进就中止了：区分「用户没请求」(not_requested) 与「本轮异常中止」(not_started)。
            body_state = 'not_requested' if bodies <= 0 else 'not_started'
        else:
            body_state = reduce_body(bodies, body_reads, done_bodies)
        outcome = build_outcome(
            list_state, len(merged), len(ordered), list_routes, save_state,
            save_stage(save_state, save_attempted, save_succeeded, save_new, save_existing,
                       save_failed, save_not_attempted), body_state,
            body_stage(body_state, bodies, body_cap, body_reads, done_bodies, body_failed,
                       body_stopped, body_skipped, body_eligible, body_unreadable),
            # 手动这一路先按链接/标题合并再入库，added 已是唯一新增数（R10）。
            unique_new=added)

        # 三条路线的结果排在前面，其他补充说明跟在后面。
        notes = [f"{PATH_LABELS[path]}：{routes[path]}" for path in PATH_ORDER if routes.get(path)] + notes
        summary = '；'.join(f'{key} {value} 篇' for key, value in sorted(paths.items())) or '没有取到列表'
        detail = (f'入库 {added} 篇（{summary}），探测读取 {probe_reads} 篇，'
                  f'正文读取 {body_reads} 篇，正文 {done_bodies} 篇待确认')
        if notes:
            detail += '。' + '；'.join(notes)
        status = decide_status(outcome)
        finished = False
        try:
            # 主回执（status/detail/outcome/counts）在同一事务写，不再依赖事后的详情补写。
            # owner token 受保护：迟到/已失效的 owner 写不进任何字段。
            self._finish_job(job_id, status, detail[:300], routes=routes, detail=detail,
                             outcome=outcome, found=len(merged), added=added,
                             bodies=done_bodies, pages=0, owner=owner)
            finished = True
            # 终态已定：辅助补写失败不能把状态改成 error（主回执已原子落库）。
            try:
                self._write_job_detail(job_id, detail, owner=owner)
            except Exception:  # noqa: BLE001
                _LOG.exception('情报任务 %s 辅助详情补写失败（终态已定，不改状态）', job_id)
        except Exception:
            if not finished:
                try:
                    self._finish_job(job_id, 'error', '结束任务时写回失败', owner=owner)
                except Exception as finish_error:  # noqa: BLE001
                    # 收口本身也失败（例如库不可写）不能吞错：如实记录，
                    # 那一行仍持有 v1 身份，FD 在 finally 关闭后由下次恢复 CAS 收口。
                    _LOG.exception('情报任务 %s 收口失败：%s', job_id, finish_error)
        finally:
            with _PROCESS_WORKERS_LOCK:
                self.workers.pop(job_id, None)
                _PROCESS_WORKERS.pop((str(self.path.resolve()), job_id), None)
            # FD 只有一个关闭责任者：worker 是转交后的唯一持有者，收口后 finally 关闭。
            if handle is not None:
                handle.close()

    def status(self):
        """界面总览：凭证状态 + 冷却截止时间 + 计数。

        **只读**：不发送任何后台请求，也不改动 strike 计数。曾经这里调用
        clear_cooldown() 把 strike 清零，导致页面每刷新一次就把「连续 3 次
        触发冷却」这条纪律抹掉一次，冷却永远不会触发。
        """
        record = {'schema': 'intel-wechat-mp/v1', 'mode': 'wechat_mp', 'cooldown_until': 0,
                  'cooldown_label': '', 'credentials': 'unknown', 'detail': ''}
        # 刷新页面也是一次「状态检查」：先恢复已释放所有权的 v1，再读闸门。
        self._recover_owned_jobs()
        strikes = 0
        try:
            state = self.client().quota.state()
            strikes = state['strikes']
            record.update(cooldown_until=state['cooldown_until'])
            if state['cooling']:
                record['cooldown_label'] = stamp(state['cooldown_until'])
            self.client().credentials_ready()
            record['credentials'] = 'ready'
            record['detail'] = '后台凭证可用' if not state['cooling'] else '冷却中，恢复后可再抓取'
        except CredentialsMissing as exc:
            record['credentials'] = 'missing'
            record['detail'] = str(exc)
        except (CredentialExpired, ValueError, OSError) as exc:
            record['credentials'] = 'expired'
            record['detail'] = str(exc)
        # 计数在事务外读：限流状态也在同一张表里，嵌套写事务会自锁。
        record['rate_strikes'] = strikes
        cooldowns = self.cooldown_snapshot()
        # 搜狗冷却与后台冷却相互独立，页面要分开显示，不能只报一个 ready。
        record['sogou_cooldown_until'] = cooldowns['sogou']
        record['sogou_cooldown_label'] = stamp(cooldowns['sogou']) if cooldowns['sogou'] else ''
        with self.db() as connection:
            record['subscriptions'] = connection.execute('SELECT COUNT(*) FROM subscriptions').fetchone()[0]
            record['articles'] = connection.execute('SELECT COUNT(*) FROM articles').fetchone()[0]
            record['awaiting'] = connection.execute(
                "SELECT COUNT(*) FROM articles WHERE body_status='awaiting_confirm' AND drop_id=''").fetchone()[0]
            busy = self.active_job(connection)
        self._release_settled_abandoned()
        # 页面据此禁用抓取按钮，避免连点产生第二个并发任务。
        record['busy'] = bool(busy)
        record['active_job'] = {'id': busy['id'], 'name': busy['name'], 'status': busy['status']} if busy else None
        return record
