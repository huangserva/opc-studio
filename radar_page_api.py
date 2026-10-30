"""雷达页「每小时新增」与「值得跟/不值得」反馈的后端模块。

数据源（B1 产出）：``workspace/twitter-radar/monitor/page-hourly.json``
反馈落盘：``workspace/twitter-radar/monitor/feedback.json``

``monitor_view`` 的 status：
- ``ok``：文件存在、schema 为 1、generated_at 在 150 分钟内（2 小时间隔 + 最长 10 分钟采集 + 余量）
- ``missing``：监控未运行（文件不存在）
- ``stale``：generated_at 超过 150 分钟
- ``invalid``：文件损坏或 schema 不是 1

``save_feedback`` 只接受 ``{"tweet_id": 纯数字字符串 ≤25 位, "verdict": "worth"|"not_worth"|null}``，
多余字段、类型不对一律抛 ``ValueError``（路由层转 400）。进程内锁 + 临时文件 ``os.replace`` 原子写。
"""

from __future__ import annotations

import json
import os
import re
import threading
import hashlib
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

SCHEMA = 1
STALE_AFTER = timedelta(minutes=150)
FUTURE_TOLERANCE = timedelta(minutes=5)
SAFE_INT_MAX = 2 ** 53 - 1
# L1：只用 ASCII 数字 + fullmatch，全角/其他语言数字/尾换行一律拒绝，不做自动修剪
TWEET_ID_RE = re.compile(r'[0-9]{1,25}')
VERDICTS = ('worth', 'not_worth')
FEEDBACK_MAX_BYTES = 2 * 1024 * 1024
# Count the outer table as level 1 and each record as level 2.
FEEDBACK_MAX_DEPTH = 8
_FEEDBACK = 'worth'
_FEEDBACK_NOT = 'not_worth'
_FEEDBACK_LOCK = threading.RLock()
# A version is scoped to this server process and a workspace. No metadata is
# written into feedback.json, which remains the B1-readable tweet-id table.
_FEEDBACK_EPOCH = uuid.uuid4().hex
_FEEDBACK_REVISIONS = {}


def _feedback_revision(path, signature):
    key = str(path.resolve())
    previous, version = _FEEDBACK_REVISIONS.get(key, (None, 0))
    if previous != signature:
        version += 1
        _FEEDBACK_REVISIONS[key] = (signature, version)
    return _FEEDBACK_EPOCH + ':' + str(version)

HIT_TYPES = ('lowfan_burst', 'new_fast', 'hourly_delta', 'upgrade_100k')


def _monitor_dir(root):
    """root 可以是仓库根，也可以直接是 workspace（两种都要能解析）。

    存在的目录优先；都不存在时按目录形状判断：有 workspace 子目录 → 仓库根布局，否则 → workspace 布局。
    """
    base = Path(root)
    as_workspace = base / 'twitter-radar/monitor'
    as_repo = base / 'workspace/twitter-radar/monitor'
    for candidate in (as_workspace, as_repo):
        if candidate.is_dir():
            return candidate
    return as_repo if (base / 'workspace').is_dir() else as_workspace


def _utcnow():
    return datetime.now(timezone.utc)


def _stamp(value):
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip().replace(' ', 'T')
    if not re.search(r'(Z|z|[+-]\d{2}:?\d{2})$', text):
        return None                      # 无时区的挂钟串不可信，不用来判过期
    try:
        moment = datetime.fromisoformat(text.replace('Z', '+00:00'))
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


class InvalidData(ValueError):
    """监控文件里的数值/结构不可信：整包判 invalid，绝不部分采用。"""


class FeedbackCorrupt(ValueError):
    """反馈文件损坏：写路径必须拒绝，不得当成空表覆盖。"""


def _number(value, field='value'):
    """M5：任何非有限数（inf/nan/超范围/字符串 'Infinity'）都抛错，由整包收敛为 invalid。"""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise InvalidData(field + ' 类型非法')
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            parsed = float(text)
        except ValueError:
            raise InvalidData(field + ' 不是数字')
    else:
        parsed = float(value)
    if parsed != parsed or parsed in (float('inf'), float('-inf')):
        raise InvalidData(field + ' 不是有限数')
    if abs(parsed) > SAFE_INT_MAX:
        raise InvalidData(field + ' 超出安全整数范围')
    return parsed


def _read_json(path):
    with Path(path).open('r', encoding='utf-8') as stream:
        return json.load(stream)


def _atomic_write(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.tmp-' + str(os.getpid()) + '-' + str(threading.get_ident()))
    with temporary.open('w', encoding='utf-8') as stream:
        stream.write(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def _clean_tweet_row(tweet_id, row):
    """逐字段收敛：命中/标记类脏值丢成 None；数值非法则抛 InvalidData 让整包 invalid。"""
    if not isinstance(row, dict):
        raise InvalidData('帖子条目不是对象')
    hourly = _number(row.get('hourly_delta'), 'hourly_delta')
    views_delta = _number(row.get('views_delta'), 'views_delta')
    interval = _number(row.get('interval_hours'), 'interval_hours')
    views = _number(row.get('views'), 'views')
    hit = row.get('hit') if row.get('hit') in HIT_TYPES else None
    pushed = row.get('pushed') if isinstance(row.get('pushed'), bool) else None
    fetched = row.get('fetched_at') if _stamp(row.get('fetched_at')) else None
    return {
        'hourly_delta': hourly if hourly is not None and hourly >= 0 else None,
        'views_delta': int(views_delta) if views_delta is not None else None,
        'interval_hours': interval,
        'views': int(views) if views is not None else None,
        'fetched_at': fetched,
        'is_stale': row.get('is_stale') is True,
        'hit': hit,
        'pushed': pushed,
    }


def _feedback_object(pairs):
    """Reject duplicate keys at every object level before any row is cleaned."""
    result = {}
    for key, value in pairs:
        if key in result:
            raise FeedbackCorrupt('反馈文件包含重复键')
        result[key] = value
    return result


def _check_feedback_depth(raw):
    """Bound JSON nesting before the recursive decoder; ignore quoted braces."""
    depth = 0
    quoted = escaped = False
    for byte in raw:
        if quoted:
            if escaped:
                escaped = False
            elif byte == 92:  # backslash
                escaped = True
            elif byte == 34:
                quoted = False
        elif byte == 34:
            quoted = True
        elif byte in (91, 123):
            depth += 1
            if depth > FEEDBACK_MAX_DEPTH:
                raise FeedbackCorrupt('反馈文件嵌套过深')
        elif byte in (93, 125):
            depth -= 1


def _feedback_snapshot(root):
    """Read records and their revision under the same write lock.

    A missing file is writable; a malformed table is not. A dictionary record
    with an unknown scalar verdict is retained as an unreviewed record. Nested
    containers in known scalar fields are structural corruption, not a verdict.
    """
    path = _monitor_dir(root) / 'feedback.json'
    with _FEEDBACK_LOCK:
        try:
            # fstat the opened file before reading. The bounded read also covers
            # an external writer growing/replacing it after the size check.
            with path.open('rb') as stream:
                info = os.fstat(stream.fileno())
                if info.st_size > FEEDBACK_MAX_BYTES:
                    signature = ('oversize', info.st_ino, info.st_size, info.st_mtime_ns)
                    return {}, 'invalid', _feedback_revision(path, signature)
                raw = stream.read(FEEDBACK_MAX_BYTES + 1)
        except FileNotFoundError:
            return {}, 'missing', _feedback_revision(path, 'missing')
        except OSError:
            return {}, 'invalid', _feedback_revision(path, 'unreadable')
        revision = _feedback_revision(path, hashlib.sha256(raw).hexdigest())
        try:
            if len(raw) > FEEDBACK_MAX_BYTES:
                raise FeedbackCorrupt('反馈文件过大')
            _check_feedback_depth(raw)
            data = json.loads(raw.decode('utf-8'), object_pairs_hook=_feedback_object)
        except (ValueError, UnicodeError, RecursionError):
            return {}, 'invalid', revision
        if not isinstance(data, dict):
            return {}, 'invalid', revision
        cleaned = {}
        for tweet_id, record in data.items():
            if not isinstance(record, dict):
                return {}, 'invalid', revision
            if any(isinstance(record.get(field), (dict, list))
                   for field in ('verdict', 'updated_at', 'hit', 'pushed')):
                return {}, 'invalid', revision
            if not isinstance(tweet_id, str) or not TWEET_ID_RE.fullmatch(tweet_id):
                continue
            verdict = record.get('verdict')
            cleaned[tweet_id] = {
                'verdict': verdict if verdict in VERDICTS else None,
                'updated_at': record.get('updated_at') if _stamp(record.get('updated_at')) else None,
                'hit': record.get('hit') if record.get('hit') in HIT_TYPES else None,
                'pushed': record.get('pushed') if isinstance(record.get('pushed'), bool) else None,
            }
        return cleaned, 'ok', revision


def load_feedback(root):
    """Compatibility API: return (records, ok/missing/invalid), without writing."""
    records, status, _revision = _feedback_snapshot(root)
    return records, status


def monitor_view(root, now=None):
    """返回 {status, generated_at, run_id, tweets, feedback, feedback_status}。

    任何文件/清洗异常都收敛成 status='invalid' 继续返回 JSON，绝不让连接断掉。
    """
    moment = now or _utcnow()
    feedback, feedback_status, feedback_revision = _feedback_snapshot(root)
    base = {'status': 'missing', 'generated_at': None, 'run_id': None, 'tweets': {},
            'feedback': feedback, 'feedback_status': feedback_status, 'feedback_revision': feedback_revision}
    path = _monitor_dir(root) / 'page-hourly.json'
    try:
        try:
            raw = path.read_text(encoding='utf-8')
        except FileNotFoundError:
            return base
        except (OSError, UnicodeDecodeError):
            return dict(base, status='invalid')
        try:
            data = json.loads(raw)
        except ValueError:
            return dict(base, status='invalid')
        # schema 必须是整数 1（布尔 True 不算）
        if not isinstance(data, dict) or type(data.get('schema')) is not int or data.get('schema') != SCHEMA \
                or not isinstance(data.get('tweets'), dict):
            return dict(base, status='invalid')
        generated = _stamp(data.get('generated_at'))
        if generated is None or generated - moment > FUTURE_TOLERANCE:
            return dict(base, status='invalid')          # 无时区或明显未来的时间都不可信
        tweets = {}
        for tweet_id, row in data['tweets'].items():
            if not isinstance(tweet_id, str) or not TWEET_ID_RE.fullmatch(tweet_id):
                continue
            tweets[tweet_id] = _clean_tweet_row(tweet_id, row)
    except InvalidData:
        return dict(base, status='invalid')
    except Exception:                                   # 兜底：任何意外都不该断连接
        return dict(base, status='invalid')
    status = 'stale' if moment - generated > STALE_AFTER else 'ok'
    return {'status': status, 'generated_at': data.get('generated_at'), 'run_id': data.get('run_id'),
            'tweets': tweets, 'feedback': feedback, 'feedback_status': feedback_status, 'feedback_revision': feedback_revision}


def validate_feedback(payload):
    """严格校验 payload；不合法抛 ValueError。tweet_id 只收完整匹配的 ASCII 数字串。"""
    if not isinstance(payload, dict):
        raise ValueError('反馈内容必须是对象')
    if set(payload) != {'tweet_id', 'verdict'}:
        raise ValueError('只允许 tweet_id 与 verdict 两个字段')
    tweet_id = payload.get('tweet_id')
    if not isinstance(tweet_id, str) or isinstance(tweet_id, bool) or not TWEET_ID_RE.fullmatch(tweet_id):
        raise ValueError('tweet_id 必须是 1–25 位纯数字字符串')
    verdict = payload.get('verdict')
    if not (verdict is None or (isinstance(verdict, str) and verdict in VERDICTS)):
        raise ValueError('verdict 只能是 worth、not_worth 或 null')
    return tweet_id, verdict


def save_feedback(root, payload, now=None, snapshot=None):
    """写入一条反馈；verdict 为 null 表示撤销。返回落盘后的该条记录。"""
    tweet_id, verdict = validate_feedback(payload)
    moment = now or _utcnow()
    directory = _monitor_dir(root)
    path = directory / 'feedback.json'
    with _FEEDBACK_LOCK:
        records, status, _revision = _feedback_snapshot(root)
        if status == 'invalid':
            # M1：反馈文件损坏时拒绝写入，原文件一字节不动，也不当成空表覆盖
            raise FeedbackCorrupt('反馈文件损坏，已停止写入')
        if verdict is None:
            records.pop(tweet_id, None)
        else:
            source = snapshot if isinstance(snapshot, dict) else {}
            hit = source.get('hit') if source.get('hit') in HIT_TYPES else None
            pushed = source.get('pushed') if isinstance(source.get('pushed'), bool) else None
            records[tweet_id] = {'verdict': verdict, 'updated_at': moment.isoformat(),
                                 'hit': hit, 'pushed': pushed}
        _atomic_write(path, records)
        _records, _status, revision = _feedback_snapshot(root)
        record = records.get(tweet_id, {'tweet_id': tweet_id, 'verdict': None})
        return dict(record, feedback_revision=revision)


__all__ = ['monitor_view', 'save_feedback', 'validate_feedback', 'load_feedback',
           'HIT_TYPES', 'VERDICTS', 'SCHEMA', 'STALE_AFTER', 'InvalidData', 'FeedbackCorrupt']
