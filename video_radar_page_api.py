"""视频榜页面数据与「值得跟/不值得/不相关」反馈的后端模块。

数据约定只按 ``coordination/video-radar-schema.md``（v1）读：

- ``workspace/video-radar/page.json``：C1 每轮原子写，schema 必须是 ``video-radar-page/v1``
- ``workspace/video-radar/feedback.json``：**本模块独占写**，C1 不读不写

``page_view`` 的 status：

- ``ok``：文件存在、JSON 合法、schema 对得上
- ``missing``：文件不存在（C1 还没跑）
- ``corrupt``：JSON 坏、schema 不是 ``video-radar-page/v1``、结构不合法

任何读取/清洗异常都收敛成 ``{"status": "corrupt", "error": ...}`` 并继续返回 200，绝不抛给路由层。

过期判定（按数据约定里的来源语义）：

- ``xiaohongshu`` / ``douyin_realtime``：距 ``last_success_at`` 超过 25 小时算 stale（每天 21:20 采集一次）
- ``douyin_daily``：超过 30 小时算 stale（日榜约晚 1–2 天）
- ``last_success_at`` 为空/不可解析一律算 stale（没有成功记录就等于没有新鲜数据）

``save_feedback`` 只接受 ``{"key": str, "verdict": "worth"|"not_worth"|"irrelevant"}``：

- 多余字段、类型不对、key 不在当前 page.json 任何列表里 → ``ValueError``（路由转 400）
- ``feedback.json`` 损坏 → ``FeedbackCorrupt``（路由转 409），原文件一字节不动
- 进程内锁 + 临时文件 ``os.replace`` 原子写；同 key 再点覆盖
"""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

SCHEMA = 'video-radar-page/v1'
LIST_KEYS = ('xiaohongshu', 'douyin_realtime', 'douyin_daily')
VERDICTS = ('worth', 'not_worth', 'irrelevant')
# 时效阈值按数据约定：实时/每小时来源 25 小时（每天 21:20 采集一次），日榜 30 小时
STALE_AFTER = {
    'xiaohongshu': timedelta(hours=25),
    'douyin_realtime': timedelta(hours=25),
    'douyin_daily': timedelta(hours=30),
}
FEEDBACK_MAX_BYTES = 2 * 1024 * 1024
# 防超大文件靠整文件上限（M2：不靠逐字段截断）
PAGE_MAX_BYTES = 16 * 1024 * 1024
FEEDBACK_MAX_DEPTH = 8
KEY_MAX = 200
_FEEDBACK_LOCK = threading.RLock()


class CorruptData(ValueError):
    """page.json 内容不可信：整包按 corrupt 处理。"""


class FeedbackCorrupt(ValueError):
    """feedback.json 损坏：写路径必须拒绝，不得当成空表覆盖。"""


def _now():
    return datetime.now(timezone.utc)


def _radar_dir(workspace):
    """workspace 可以是 workspace 目录，也可以是仓库根（两种都能解析）。"""
    base = Path(workspace)
    as_workspace = base / 'video-radar'
    as_repo = base / 'workspace/video-radar'
    for candidate in (as_workspace, as_repo):
        if candidate.is_dir():
            return candidate
    return as_repo if (base / 'workspace').is_dir() else as_workspace


def _read_json(path):
    with Path(path).open('r', encoding='utf-8') as stream:
        return json.load(stream)


def _reject_constant(name):
    """M1：JSON 里的 NaN / Infinity / -Infinity 是非有限数，不是合法数据。

    json 模块遇到这三个 token 会回调 parse_constant；这里直接抛错，
    page_view 会把整包收敛成 status='corrupt'，绝不把该字段悄悄改成 null 照常展示。
    """
    raise ValueError('page.json 含非有限数字面量 ' + name)


def _loads_strict(raw):
    """严格解析（第一步）：拒绝 NaN / Infinity / -Infinity 三个字面量 token。

    只做语法层；数值有限性由 _assert_all_finite 单独做（见下），两者的错误文案
    分开，这样「JSON 语法不合法」和「JSON 合法但数值不可表示」能区分开。
    """
    return json.loads(raw, parse_constant=_reject_constant)


def _finite_check_label(parts):
    """把路径片段拼成可读的 JSON 路径：today.extra.a[0].b.c[0]。"""
    out = []
    for part in parts:
        if isinstance(part, int):
            out.append('[%d]' % part)
        elif out:
            out.append('.' + str(part))
        else:
            out.append(str(part))
    return ''.join(out) or '<root>'


def _assert_all_finite(root):
    """解析后、任何清洗前的整包有限性校验（fix2/M1）。

    `1e400` / `-1e400` 是**合法 JSON 数字文本**，Python 直接解成 ±inf 且不触发
    parse_constant；`_count` / `_rate` 只在清洗用到该值时才拦得住，位置一多就漏
    （fix1 复验在 sources.note、sources 时间戳、三个 extra 深层、items[0].sources
    六处判成 ok）。所以这里遍历整棵数据——含未使用字段、任意深度的数组/对象——
    命中即抛 CorruptData 并带上出错路径，由 page_view 收敛成 status='corrupt'。

    用显式栈迭代而不是递归：深层嵌套不会触发 RecursionError（单测有 2000 层用例）。
    还要拒绝 float(int) 溢出的整数：Python int 有限，浏览器 JSON.parse 却会得到 Infinity。
    只检查浏览器有限性，不限制 MAX_SAFE_INTEGER；有限大数、0、null、数字字符串不受影响。
    """
    stack = [(root, ())]
    while stack:
        node, path = stack.pop()
        if isinstance(node, (int, float)):
            try:
                number = float(node)
            except OverflowError:
                raise CorruptData('在 %s 发现浏览器无法表示的非有限数（整数溢出）'
                                  % _finite_check_label(path)) from None
            if number != number or number in (float('inf'), float('-inf')):
                raise CorruptData('在 %s 发现非有限数字面量（1e400 / -1e400 / NaN 一类）'
                                  % _finite_check_label(path))
        elif isinstance(node, dict):
            for key, value in node.items():
                stack.append((value, path + (key,)))
        elif isinstance(node, list):
            for index, value in enumerate(node):
                stack.append((value, path + (index,)))


def _atomic_write(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.tmp-' + str(os.getpid()) + '-' + str(threading.get_ident()))
    with temporary.open('w', encoding='utf-8') as stream:
        stream.write(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def _stamp(value):
    """带时区的 ISO 8601 → aware datetime；无时区/非字符串一律 None（不猜时区）。"""
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip().replace(' ', 'T')
    if text.endswith(('Z', 'z')):
        text = text[:-1] + '+00:00'
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return None
    return moment if moment.tzinfo else None


def _text(value, limit=None):
    """外部文本：只接受字符串，**原样返回，不截断**（M2）。

    limit 只用于「格式有界」的字段（时间戳、日期这类不可能更长的格式串），
    自由文本（标题/作者/理由/来源名/来源注释/链接/封面）一律不传 limit。
    防超大文件靠整文件大小上限，不靠逐字段截断。
    """
    if not isinstance(value, str):
        return ''
    return value if limit is None else value[:limit]


def _count(value):
    """计数类字段：int 或 null；0 与 null 必须区分（null 显示「—」、0 显示 0）。

    M1：非有限数（NaN / ±Infinity，含 1e400 这种解析成 inf 的写法）不再静默收敛成 None，
    而是抛 CorruptData 让整包判 corrupt。合法 null、合法 int、合法数字字符串行为不变。
    """
    if value is None or isinstance(value, bool):
        return None                          # 布尔沿用原行为：算未知，不算坏数
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if value != value or value in (float('inf'), float('-inf')):
            raise CorruptData('计数字段是非有限数')
        return int(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            number = float(text)
        except ValueError:
            return None                      # 不是数字的字符串：保持原行为（不算坏数）
        if number != number or number in (float('inf'), float('-inf')):
            raise CorruptData('计数字段是非有限数')
        return int(number)
    return None


def _rate(value):
    """比率类字段：number 或 null。"""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
    elif isinstance(value, str):
        try:
            number = float(value.strip())
        except ValueError:
            return None
    else:
        return None
    if number != number or number in (float('inf'), float('-inf')):
        raise CorruptData('比率字段是非有限数')       # M1：不再静默 None
    return number


def _strings(value, limit=None, item_limit=None):
    """字符串数组：**每个元素原样返回，不截断**（M2）。

    limit/item_limit 只在需要「格式有界」时显式传；来源名（sources）不传。
    """
    if not isinstance(value, list):
        return []
    items = value if limit is None else value[:limit]
    out = []
    for item in items:
        if isinstance(item, str):
            out.append(item if item_limit is None else item[:item_limit])
    return out


def _clean_item(raw):
    """一条 Item 收敛成页面用的形状；结构不合法抛 CorruptData 让整包按 corrupt 处理。"""
    if not isinstance(raw, dict):
        raise CorruptData('列表条目不是对象')
    key = raw.get('key')
    if not isinstance(key, str) or not key.strip() or len(key) > KEY_MAX:
        raise CorruptData('条目缺少可用的 key')
    platform = raw.get('platform') if raw.get('platform') in ('xiaohongshu', 'douyin') else None
    identifier = raw.get('id') if isinstance(raw.get('id'), str) else ''
    return {
        'key': key.strip()[:KEY_MAX],
        'platform': platform,
        'id': identifier[:KEY_MAX],
        'sources': _strings(raw.get('sources')),
        'title': _text(raw.get('title')),
        'url': _text(raw.get('url')),
        'author': _text(raw.get('author')),
        'author_fans': _count(raw.get('author_fans')),
        'published_at': _text(raw.get('published_at'), 80),
        'first_seen_at': _text(raw.get('first_seen_at'), 80),
        'last_seen_at': _text(raw.get('last_seen_at'), 80),
        'likes': _count(raw.get('likes')),
        'collects': _count(raw.get('collects')),
        'comments': _count(raw.get('comments')),
        'shares': _count(raw.get('shares')),
        'plays': _count(raw.get('plays')),
        'likes_per_hour': _rate(raw.get('likes_per_hour')),
        'likes_delta_per_hour': _rate(raw.get('likes_delta_per_hour')),
        'score': _rate(raw.get('score')),
        'relevance': raw.get('relevance') if raw.get('relevance') in ('relevant', 'weak') else None,
        'relevance_reason': _text(raw.get('relevance_reason')),
        'flags': _strings(raw.get('flags')),
        'cover': _text(raw.get('cover')),
    }


def _feedback_snapshot(workspace):
    """读反馈表，返回 (记录, 状态)。状态：ok / missing / invalid；损坏时原文件不动。"""
    path = _radar_dir(workspace) / 'feedback.json'
    try:
        raw = path.read_text(encoding='utf-8')
    except FileNotFoundError:
        return {}, 'missing'
    except (OSError, UnicodeDecodeError):
        return {}, 'invalid'
    if len(raw.encode('utf-8')) > FEEDBACK_MAX_BYTES:
        return {}, 'invalid'
    try:
        data = json.loads(raw, object_pairs_hook=_feedback_object)
    except ValueError:
        return {}, 'invalid'
    if not isinstance(data, dict):
        return {}, 'invalid'
    cleaned = {}
    for key, record in data.items():
        if not isinstance(key, str) or not key.strip() or not isinstance(record, dict):
            continue
        verdict = record.get('verdict')
        cleaned[key.strip()[:KEY_MAX]] = {
            'verdict': verdict if verdict in VERDICTS else None,
            'updated_at': _text(record.get('updated_at'), 80) or None,
            'title': _text(record.get('title')),
            'url': _text(record.get('url')),
            'platform': record.get('platform') if record.get('platform') in ('xiaohongshu', 'douyin') else None,
        }
    return cleaned, 'ok'


def _feedback_object(pairs):
    """重复键直接判损坏：同一条反馈出现两次说明文件不可信。"""
    out = {}
    for key, value in pairs:
        if key in out:
            raise FeedbackCorrupt('反馈文件包含重复键')
        out[key] = value
    if len(out) > 5000:
        raise FeedbackCorrupt('反馈文件条目过多')
    return out


def _source_meta(raw, name, now):
    source = raw.get(name) if isinstance(raw.get(name), dict) else {}
    last_success = _text(source.get('last_success_at'), 80) or None
    stamp = _stamp(last_success)
    status = source.get('status') if source.get('status') in ('ok', 'partial', 'failed', 'never') else None
    if stamp is None:
        stale = True
        age_minutes = None
    else:
        age = now - stamp
        stale = age > STALE_AFTER[name]
        age_minutes = round(age.total_seconds() / 60.0, 1)
    return {
        'last_attempt_at': _text(source.get('last_attempt_at'), 80) or None,
        'last_success_at': last_success,
        'status': status,
        'note': _text(source.get('note')),
        'stale': bool(stale),
        'age_minutes': age_minutes,
        'stale_after_minutes': int(STALE_AFTER[name].total_seconds() // 60),
    }


def page_view(workspace, now=None):
    """返回视频榜页面数据；任何异常都收敛成 status=corrupt，绝不抛。"""
    moment = now or _now()
    feedback, feedback_status = _feedback_snapshot(workspace)
    base = {'status': 'missing', 'generated_at': None, 'keyword': None, 'today': {},
            'sources': {}, 'lists': {name: [] for name in LIST_KEYS},
            'feedback': feedback, 'feedback_status': feedback_status, 'stale': True}
    path = _radar_dir(workspace) / 'page.json'
    try:
        try:
            raw = path.read_text(encoding='utf-8')
        except FileNotFoundError:
            return base
        except (OSError, UnicodeDecodeError) as exc:
            return dict(base, status='corrupt', error='无法读取 page.json：' + type(exc).__name__)
        if len(raw.encode('utf-8')) > PAGE_MAX_BYTES:
            return dict(base, status='corrupt', error='page.json 超过整文件大小上限')
        try:
            data = _loads_strict(raw)
        except ValueError as exc:
            return dict(base, status='corrupt', error='page.json 不是合法 JSON：' + str(exc)[:120])
        try:
            _assert_all_finite(data)
        except CorruptData as exc:
            # 单独一条路径：JSON 语法合法，是数值不可表示（1e400 / -1e400 一类）。
            # 文案自带出错路径，便于直接定位到 page.json 里的字段。
            return dict(base, status='corrupt', error=str(exc)[:160])
        if not isinstance(data, dict) or data.get('schema') != SCHEMA:
            return dict(base, status='corrupt', error='page.json 的 schema 不是 ' + SCHEMA)
        raw_lists = data.get('lists')
        if not isinstance(raw_lists, dict):
            return dict(base, status='corrupt', error='page.json 缺少 lists')
        sources = data.get('sources') if isinstance(data.get('sources'), dict) else {}
        meta = {name: _source_meta(sources, name, moment) for name in LIST_KEYS}
        lists = {}
        for name in LIST_KEYS:
            raw_items = raw_lists.get(name)
            if raw_items is None:
                raw_items = []
            if not isinstance(raw_items, list):
                return dict(base, status='corrupt', error='lists.%s 不是数组' % name)
            items = []
            for entry in raw_items:
                item = _clean_item(entry)
                item['feedback'] = (feedback.get(item['key']) or {}).get('verdict')
                items.append(item)
            lists[name] = items
        today = data.get('today') if isinstance(data.get('today'), dict) else {}
        return {
            'status': 'ok',
            'generated_at': _text(data.get('generated_at'), 80) or None,
            'keyword': _text(data.get('keyword'), 80) or None,
            'today': {
                'date': _text(today.get('date'), 40) or None,
                'rounds': _count(today.get('rounds')),
                'xiaohongshu_ok': _count(today.get('xiaohongshu_ok')),
                'douyin_realtime_ok': _count(today.get('douyin_realtime_ok')),
                'douyin_daily_ok': _count(today.get('douyin_daily_ok')),
                'api_calls': _count(today.get('api_calls')),
            },
            'sources': meta,
            'lists': lists,
            'feedback': feedback,
            'feedback_status': feedback_status,
            'stale': any(meta[name]['stale'] for name in LIST_KEYS),
        }
    except (CorruptData, ValueError) as exc:
        return dict(base, status='corrupt', error=str(exc) or 'page.json 内容不合法')
    except Exception as exc:                       # 兜底：任何意外都不该让连接断掉
        return dict(base, status='corrupt', error='page.json 处理失败：' + type(exc).__name__)


def validate_feedback(payload):
    """严格校验 payload；不合法抛 ValueError。"""
    if not isinstance(payload, dict):
        raise ValueError('反馈内容必须是对象')
    if set(payload) != {'key', 'verdict'}:
        raise ValueError('只允许 key 与 verdict 两个字段')
    key = payload.get('key')
    if not isinstance(key, str) or isinstance(key, bool) or not key.strip() or len(key) > KEY_MAX:
        raise ValueError('key 必须是长度 1–%d 的字符串' % KEY_MAX)
    verdict = payload.get('verdict')
    if not (isinstance(verdict, str) and verdict in VERDICTS):
        raise ValueError('verdict 只能是 worth、not_worth 或 irrelevant')
    return key.strip(), verdict


def _index_items(view):
    """当前 page.json 里所有可用 key → 条目快照（只收 status=ok 的）。"""
    index = {}
    if view.get('status') != 'ok':
        return index
    for name in LIST_KEYS:
        for item in view['lists'].get(name) or []:
            index.setdefault(item['key'], item)
    return index


def save_feedback(workspace, payload, now=None, index=None):
    """写入一条反馈；同 key 再点覆盖。返回落盘后的该条记录。"""
    key, verdict = validate_feedback(payload)
    moment = now or _now()
    known = index if isinstance(index, dict) else _index_items(page_view(workspace, now=moment))
    if key not in known:
        # 反馈必须落在当前榜上：不能给已下榜或不存在的条目写意见
        raise ValueError('key 不在当前视频榜里，可能已下榜')
    path = _radar_dir(workspace) / 'feedback.json'
    with _FEEDBACK_LOCK:
        records, status = _feedback_snapshot(workspace)
        if status == 'invalid':
            # 损坏时拒绝写入：原文件一字节不动，也不得当成空表覆盖
            raise FeedbackCorrupt('反馈文件损坏，已停止写入')
        item = known.get(key) or {}
        records[key] = {
            'verdict': verdict,
            'updated_at': moment.isoformat(),
            'title': _text(item.get('title')),
            'url': _text(item.get('url')),
            'platform': item.get('platform'),
        }
        _atomic_write(path, records)
    return records[key]


__all__ = ['page_view', 'save_feedback', 'validate_feedback', 'load_feedback',
           'SCHEMA', 'LIST_KEYS', 'VERDICTS', 'STALE_AFTER', 'PAGE_MAX_BYTES',
           'CorruptData', 'FeedbackCorrupt']


def load_feedback(workspace):
    """只读接口：返回 (记录表, 状态)，不写盘。"""
    return _feedback_snapshot(workspace)
