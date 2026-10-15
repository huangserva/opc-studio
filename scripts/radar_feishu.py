#!/usr/bin/env python3
"""B2-a 爆款监控飞书卡片发送器。

B1 的 ``Monitor.flush`` 通过 ``load_module(root/'scripts/radar_feishu.py')`` 载入本文件，
调用 ``send_events(events)``。接口约定见 coordination 的 B2-send-events-interface.md：

- 成功的事件返回 ``{"event_id": ..., "status": "sent"}``；失败的省略（不冒充成功）。
- ``event_id`` 是幂等键：已 sent 的直接返回 sent 且**不发任何请求**。
- 本函数**不抛异常**：内部全部兜住并写日志，调用方拿到的永远是列表。
- 正文一律用 ``plain_text`` 元素承载，链接只由校验后的 handle + 纯数字 tweet_id 重建，
  Webhook 与密钥既不打印也不写日志。

配置（环境变量）：``FEISHU_RADAR_WEBHOOK``、``FEISHU_RADAR_SECRET``、
``RADAR_PUBLIC_BASE``、``FEISHU_RADAR_LEDGER``（账本路径，测试用临时目录）。
"""

from __future__ import annotations

import argparse
import base64
import copy
from contextvars import ContextVar
import fcntl
import hashlib
import hmac
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import quote, urlsplit
from urllib.request import Request, HTTPRedirectHandler, build_opener

BEIJING = timezone(timedelta(hours=8))
ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PUBLIC_BASE = 'http://127.0.0.1:8766'
MAX_EVENTS_PER_CARD = 10
MAX_BODY_BYTES = 18 * 1024
MAX_RESPONSE_BYTES = 64 * 1024
_CREDENTIALS = ContextVar("feishu_credentials", default=())
REQUEST_TIMEOUT = 10.0     # 单次请求连接+读取上限
CALL_DEADLINE = 10.0       # 一次 send_events 内所有请求的墙钟预算
RATE_LIMIT_CODES = {9499, 11232}
RATE_LIMIT_WORDS = ('频控', 'rate limit', 'too many request', '请求过于频繁')

TWEET_EVENTS = ('lowfan_burst', 'new_fast', 'hourly_delta', 'upgrade_100k')
TYPE_LABEL = {
    'lowfan_burst': '低粉大爆款',
    'new_fast': '新帖起飞',
    'hourly_delta': '小时增量',
    'upgrade_100k': '破 10 万升级',
}
HANDLE_RE = re.compile(r'^[A-Za-z0-9_]{1,15}$')
DIGITS_RE = re.compile(r'[0-9]{1,25}')
CLUE_SAFE_RE = re.compile(r'^[A-Za-z0-9_.:-]{1,64}$')

_NO_WEBHOOK_WARNED = False


# ---------------------------------------------------------------- 基础工具
def _now():
    return datetime.now(timezone.utc)


def _iso(value=None):
    return (value or _now()).astimezone(timezone.utc).isoformat()


def _beijing_stamp(value):
    """把 UTC ISO / 时间戳转成北京时间展示串；不可解析返回 None。"""
    if isinstance(value, (int, float)):
        moment = datetime.fromtimestamp(float(value), tz=timezone.utc)
    elif isinstance(value, str) and value.strip():
        text = value.strip().replace(' ', 'T')
        if re.search(r'(Z|z|[+-]\d{2}:?\d{2})$', text):
            moment = datetime.fromisoformat(text.replace('Z', '+00:00'))
        else:
            moment = None  # 无时区的挂钟串不可信，不当作发布时间
    else:
        moment = None
    if moment is None:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(BEIJING).strftime('%Y-%m-%d %H:%M')


def _number(value):
    """返回可展示的数字文本：null/缺失一律「—」，绝不用 0 顶替。"""
    if isinstance(value, bool) or value is None:
        return '—'
    if isinstance(value, (int, float)):
        if value != value or value in (float('inf'), float('-inf')):
            return '—'
        return f'{int(value):,}'
    if isinstance(value, str) and value.strip():
        try:
            return f'{int(float(value.strip())):,}'
        except ValueError:
            return '—'
    return '—'


def _percent(value):
    """覆盖率按百分比展示；null/非法一律「—」，不显示 0。"""
    if isinstance(value, bool) or value is None:
        return '—'
    try:
        number = float(value)
    except (TypeError, ValueError):
        return '—'
    if number != number or number in (float('inf'), float('-inf')):
        return '—'
    return '{:.1f}%'.format(number * 100)


def _clean_text(value, limit):
    """正文清洗：去掉孤立代理与控制字符、压平换行、超长按码点截断，不产生乱码。"""
    if value is None:
        return ''
    text = _redact(value if isinstance(value, str) else str(value))
    text = text.replace('\r\n', '\n').replace('\r', '\n')
    kept = []
    for char in text:
        point = ord(char)
        if char in ('\n', '\t'):
            kept.append(' ')
            continue
        if point < 32 or 0xD800 <= point <= 0xDFFF or 0xFFF0 <= point <= 0xFFFF:
            continue
        kept.append(char)
    text = ''.join(kept)
    text = re.sub(r'\s{2,}', ' ', text).strip()
    if limit and len(text) > limit:
        text = text[:limit - 1] + '…'
    return text


def _redact(value, webhook='', secret=''):
    """所有可观察出口共用；先替换完整地址，再替换 token 和密钥，截断在脱敏之后。"""
    credentials = list(_CREDENTIALS.get()) + [
        os.environ.get('FEISHU_RADAR_WEBHOOK', ''),
        os.environ.get('FEISHU_RADAR_SECRET', ''), webhook, secret]
    tokens = []
    for item in credentials:
        if item:
            tokens.extend((str(item), str(item).strip()))
            try:
                parts = urlsplit(str(item))
                if parts.scheme and parts.netloc and parts.path.rstrip('/'):
                    tokens.append(parts.path.rstrip('/').rsplit('/', 1)[-1])
            except ValueError:
                pass
    if isinstance(value, dict):
        return {_redact(k, webhook, secret): _redact(v, webhook, secret) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_redact(v, webhook, secret) for v in value]
    if not isinstance(value, str):
        return value
    for token in sorted({token for token in tokens if token}, key=len, reverse=True):
        value = value.replace(token, '[已隐藏]')
    # 即使正文嵌入的是另一条 webhook，也不把可用机器人地址外发。
    return re.sub(r'https?://[^\s<>"\']*/open-apis/bot/v2/hook/[^\s<>"\']+',
                  '[已隐藏]', value)


def _log(message, webhook='', secret=''):
    line = json.dumps({'time': _iso(), 'message': _redact(str(message), webhook, secret)},
                      ensure_ascii=False)
    try:
        path = ROOT / 'workspace/twitter-radar/monitor/feishu.log'
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('a', encoding='utf-8') as stream:
            stream.write(line + '\n')
    except OSError:
        pass
    return line


def _config():
    webhook = (os.environ.get('FEISHU_RADAR_WEBHOOK') or '').strip()
    secret = (os.environ.get('FEISHU_RADAR_SECRET') or '').strip()
    base = os.environ.get('RADAR_PUBLIC_BASE') or DEFAULT_PUBLIC_BASE
    ledger = (os.environ.get('FEISHU_RADAR_LEDGER') or '').strip()
    return webhook, secret, base, ledger


def _public_base():
    _, _, base, _ = _config()
    try:
        # urlsplit drops empty query/fragment delimiters; reject the raw syntax.
        if ('?' in base or '#' in base
                or any(ord(c) < 33 or 127 <= ord(c) <= 159 for c in base)):
            return DEFAULT_PUBLIC_BASE
        parts = urlsplit(base)
        if (parts.scheme not in ('http', 'https') or not parts.hostname
                or parts.username is not None or parts.password is not None
                or parts.query or parts.fragment):
            return DEFAULT_PUBLIC_BASE
        parts.port  # 验证端口，非法值按无效配置处理。
        if _redact(base) != base:
            return DEFAULT_PUBLIC_BASE
    except ValueError:
        return DEFAULT_PUBLIC_BASE
    return base.rstrip('/')


# ---------------------------------------------------------------- 幂等账本
def _ledger_path():
    _, _, _, override = _config()
    if override:
        return Path(override)
    return ROOT / 'workspace/twitter-radar/monitor/feishu-ledger.json'


def _empty_ledger():
    return {'version': 1, 'records': {}}


def _load_ledger(path):
    """账本损坏时不崩溃：备份坏文件并重建空账本。"""
    damaged = False
    data = _empty_ledger()
    try:
        raw = path.read_text(encoding='utf-8')
        parsed = json.loads(raw)
        if (isinstance(parsed, dict) and parsed.get('version') == 1
                and isinstance(parsed.get('records'), dict)):
            data = parsed
        else:
            raise ValueError('账本结构损坏')
    except FileNotFoundError:
        pass
    except (OSError, ValueError, UnicodeDecodeError):
        damaged = True
        backup = path.with_name(path.name + '.corrupt-' + _now().strftime('%Y%m%dT%H%M%SZ'))
        try:
            backup.write_text(_redact(path.read_text(encoding='utf-8', errors='replace')), encoding='utf-8')
            path.unlink()
        except OSError:
            pass
        _log('发送账本损坏，已备份为 ' + backup.name + ' 并重建空账本')
        data = _empty_ledger()
    return data, damaged


def _write_ledger(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.tmp-' + str(os.getpid()))
    payload = json.dumps(_redact(data), ensure_ascii=False, indent=2, sort_keys=True)
    with temporary.open('w', encoding='utf-8') as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


class _LedgerLock:
    """跨进程排他锁：两个进程同批事件时只有一个真正发请求。"""

    def __init__(self, path, deadline):
        self.deadline = deadline
        self.path = path.with_name(path.name + '.lock')
        self.handle = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = self.path.open('a+')
        while True:
            try:
                fcntl.flock(self.handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                remaining = self.deadline - time.monotonic()
                if remaining <= 0:
                    self.handle.close()
                    raise TimeoutError('等待发送锁超时')
                time.sleep(min(.02, remaining))
        return self

    def __exit__(self, *exc):
        try:
            fcntl.flock(self.handle, fcntl.LOCK_UN)
        finally:
            self.handle.close()
        return False


# ---------------------------------------------------------------- 链接重建
def _tweet_url(tweet):
    """只用合法 handle + 纯数字 tweet_id 重建原帖链接；不合法就没有按钮。"""
    if not isinstance(tweet, dict):
        return None
    handle = tweet.get('author')
    if isinstance(handle, dict):
        handle = handle.get('handle')
    tweet_id = tweet.get('tweet_id')
    if not HANDLE_RE.fullmatch(str(handle or '')) or not DIGITS_RE.fullmatch(str(tweet_id or '')):
        return None
    return 'https://x.com/{}/status/{}'.format(handle, tweet_id)


def _clue_url(clue_id):
    """线索中心链接：只接受保守字符集，其余一律省略按钮，不做可注入拼接。"""
    if clue_id is None:
        return None
    text = str(clue_id)
    if not CLUE_SAFE_RE.fullmatch(text):
        return None
    return '{}/clues.html?clue={}'.format(_public_base(), quote(text, safe=''))


def _radar_url(tweet):
    """反馈深链只依赖 ASCII 帖子编号；非法 base 沿用默认站内地址。"""
    tweet_id = tweet.get('tweet_id') if isinstance(tweet, dict) else None
    if not DIGITS_RE.fullmatch(str(tweet_id) if tweet_id is not None else ''):
        return None
    base = _public_base()
    return '{}/twitter-radar.html#tweet={}'.format(base, tweet_id) if base else None


# ---------------------------------------------------------------- 卡片构造
def _text_element(content):
    # plain_text：飞书不会把正文里的 [链接](url)、<at>、<font>、** ** 当成语法渲染
    return {'tag': 'div', 'text': {'tag': 'plain_text', 'content': _redact(content)}}


def _divider():
    return {'tag': 'hr'}


def _button(label, url):
    return {'tag': 'button', 'text': {'tag': 'plain_text', 'content': _redact(label)},
            'url': _redact(url), 'type': 'default', 'value': {'url': _redact(url)}}


def _viral_line(event, resend):
    tweet = event.get('tweet') if isinstance(event.get('tweet'), dict) else {}
    label = TYPE_LABEL.get(event.get('type'), '')
    author = tweet.get('author')
    if isinstance(author, dict):
        author = author.get('handle')
    handle_text = _clean_text(author, 30) if author else '作者未知'
    followers = _number(tweet.get('followers')) if tweet.get('followers') is not None else '粉丝未知'
    published = _beijing_stamp(tweet.get('published_at'))
    age = '发布 —'
    if published:
        try:
            moment = datetime.strptime(published, '%Y-%m-%d %H:%M').replace(tzinfo=BEIJING)
            hours = max(0.0, (_now().astimezone(BEIJING) - moment).total_seconds() / 3600)
            age = '发布约 %.1f 小时前' % hours if hours < 24 else '发布于 ' + published
        except ValueError:
            age = '发布 —'
    hourly = _number(tweet.get('hourly_delta')) if tweet.get('hourly_delta') is not None else '—'
    body = _clean_text(tweet.get('text'), 120)
    head = '【{}】@{}（{}）· {}'.format(label or '未知类型', handle_text, followers, age)
    metrics = '浏览 {} · 赞 {} · 转 {} · 评 {} · 每小时新增 {}'.format(
        _number(tweet.get('views')), _number(tweet.get('likes')),
        _number(tweet.get('retweets')), _number(tweet.get('replies')), hourly)
    parts = [head, metrics]
    if body:
        parts.append(body)
    if resend:
        parts.append('（重发：上次结果未知）')
    return '\n'.join(parts)


def build_viral_card(events, resend_ids=()):
    elements = []
    for event in events:
        elements.append(_text_element(_viral_line(event, event.get('event_id') in resend_ids)))
        actions = []
        tweet = event.get('tweet') if isinstance(event.get('tweet'), dict) else {}
        url = _tweet_url(tweet)
        if url:
            actions.append(_button('打开原帖', url))
        clue = _clue_url(event.get('clue_id'))
        if clue:
            actions.append(_button('线索中心', clue))
        radar = _radar_url(tweet)
        if radar:
            actions.append(_button('去雷达页标记', radar))
        if actions:
            elements.append({'tag': 'action', 'actions': actions})
        elements.append(_divider())
    if elements:
        elements.pop()
    return {
        'msg_type': 'interactive',
        'card': {
            'config': {'wide_screen_mode': True},
            'header': {'template': 'blue',
                       'title': {'tag': 'plain_text', 'content': '推特爆款 · {} 条'.format(len(events))}},
            'elements': elements or [_text_element('暂无爆款内容')],
        },
    }


def build_fault_card(event):
    failures = _number(event.get('failures'))
    coverage = _percent(event.get('coverage'))
    return {
        'msg_type': 'interactive',
        'card': {
            'config': {'wide_screen_mode': True},
            'header': {'template': 'red',
                       'title': {'tag': 'plain_text', 'content': '监控故障 · 采集不达标'}},
            'elements': [_text_element('连续失败轮数：{}\n本轮覆盖率：{}'.format(failures, coverage)),
                         _text_element('请检查采集链路与 AIsa 调用状态。')],
        },
    }


def build_recovery_card(event):
    return {
        'msg_type': 'interactive',
        'card': {
            'config': {'wide_screen_mode': True},
            'header': {'template': 'green',
                       'title': {'tag': 'plain_text', 'content': '监控恢复 · 采集已达标'}},
            'elements': [_text_element('本轮覆盖率：{}'.format(_percent(event.get('coverage')))),
                         _text_element('飞书卡片通道与采集链路均已恢复。')],
        },
    }


def _coverage_text(values):
    if not isinstance(values, list) or not values:
        return '—'
    shown = []
    for value in values[-6:]:
        shown.append(_percent(value))
    return ' / '.join(shown)


def _feedback_text(feedback):
    """沿用 B1 快照口径，不从已推送/未反馈数推算有用率。"""
    if (not isinstance(feedback, dict) or feedback.get('status') != 'ok'
            or feedback.get('message') == '反馈不可用'):
        return '反馈数据不可用'
    lines = []
    for key, label in (('today', '今日'), ('recent_7_days', '近 7 天')):
        stats = feedback.get(key)
        if not isinstance(stats, dict):
            return '反馈数据不可用'
        rate = stats.get('useful_rate')
        rate_text = '暂无反馈' if rate is None else '有用率 ' + _percent(rate)
        lines.append('{}：值得跟 {} / 不值得 {} / 未反馈 {} / {}'.format(
            label, _number(stats.get('worth')), _number(stats.get('not_worth')),
            _number(stats.get('unreviewed')), rate_text))
    return '\n'.join(lines)


def build_daily_card(event):
    summary = event.get('summary') if isinstance(event.get('summary'), dict) else {}
    rounds = _number(summary.get('rounds'))
    calls = _number(summary.get('api_calls'))
    unknown = summary.get('unknown_call_rounds')
    unknown_text = _number(unknown) if isinstance(unknown, int) else '—'
    if isinstance(unknown, int) and unknown > 0:
        call_line = '已知调用 {} 次（另有 {} 轮未统计）'.format(calls, unknown_text)
    else:
        call_line = '调用 {} 次'.format(calls)
    counts = summary.get('viral_counts') if isinstance(summary.get('viral_counts'), dict) else {}
    if counts:
        count_line = '各类爆款：' + '，'.join(
            '{} {} 条'.format(TYPE_LABEL.get(key, key), _number(counts[key]))
            for key in TWEET_EVENTS if key in counts)
    else:
        count_line = '各类爆款：—'
    shadows = summary.get('shadow_hits') if isinstance(summary.get('shadow_hits'), dict) else {}
    shadow_total = 0
    for value in shadows.values():
        parsed = _number(value)
        shadow_total += int(parsed.replace(',', '')) if parsed != '—' else 0
    return {
        'msg_type': 'interactive',
        'card': {
            'config': {'wide_screen_mode': True},
            'header': {'template': 'turquoise',
                       'title': {'tag': 'plain_text',
                                 'content': '爆款监控日报 · {}'.format(_clean_text(event.get('date'), 20) or '—')}},
            'elements': [
                _text_element('有效轮数：{}\n{}'.format(rounds, call_line)),
                _text_element('最近覆盖率：{}'.format(_coverage_text(summary.get('coverage')))),
                _text_element(count_line),
                _text_element('影子候选命中：{} 次'.format(shadow_total)),
                _text_element(_feedback_text(summary.get('feedback'))),
            ],
        },
    }


def _body_bytes(payload):
    return len(json.dumps(payload, ensure_ascii=False).encode('utf-8'))


def _valid_events(events, deadline=None):
    """本批 first-wins；坏事件单独跳过，保留可空指标与安全纯文本作者。"""
    usable, seen = [], set()
    for event in events:
        if deadline is not None and time.monotonic() >= deadline:
            _log('本次调用时间预算用尽，剩余事件未处理')
            break
        try:
            if not isinstance(event, dict):
                raise ValueError('事件不是对象')
            event_id = event.get('event_id')
            if (not isinstance(event_id, str) or not event_id or len(event_id) > 512
                    or _redact(event_id) != event_id
                    or any(ord(c) < 32 or 0xD800 <= ord(c) <= 0xDFFF for c in event_id)):
                raise ValueError('事件缺少可用的 event_id')
            if event_id in seen:
                _log('本批重复 event_id，保留第一条，忽略其余内容')
                continue
            seen.add(event_id)
            kind = event.get('type')
            if kind not in TWEET_EVENTS + ('monitor_fault', 'monitor_recovery', 'daily_summary'):
                raise ValueError('未知事件类型：' + _clean_text(kind, 40))
            if kind in TWEET_EVENTS:
                tweet = event.get('tweet')
                if not isinstance(tweet, dict) or not tweet.get('tweet_id'):
                    raise ValueError('爆款事件缺少 tweet 或 tweet_id')
                # 按 B1 必需语义字段验证；指标 null 和空正文仍合法。
                if not all(k in tweet for k in ('author', 'published_at', 'text')):
                    raise ValueError('爆款事件缺少作者、发布时间或正文')
                if _beijing_stamp(tweet.get('published_at')) is None:
                    raise ValueError('爆款事件发布时间无效')
            elif kind == 'daily_summary':
                if not isinstance(event.get('summary'), dict) or not isinstance(event.get('date'), str):
                    raise ValueError('日报缺少日期或 summary')
            # 在逐事件隔离区预构卡，非法数字或嵌套数据不能拖住同批。
            builder = (lambda e: build_viral_card([e])) if kind in TWEET_EVENTS else {
                'monitor_fault': build_fault_card, 'monitor_recovery': build_recovery_card,
                'daily_summary': build_daily_card}[kind]
            _body_bytes(builder(event))
            usable.append(event)
        except Exception as error:
            _log('事件已跳过：' + _clean_text(str(error), 100))
    return usable


def _wire_body(payload, secret):
    body = _redact(copy.deepcopy(payload))
    if secret:
        timestamp = str(int(time.time()))
        body.update(timestamp=timestamp, sign=_sign(secret, timestamp))
    return json.dumps(body, ensure_ascii=False).encode('utf-8')


def _fit_single(payload, secret):
    if len(_wire_body(payload, secret)) <= MAX_BODY_BYTES:
        return payload
    payload = copy.deepcopy(payload)
    # 异常长的页面 base 会使按钮占满卡片，先省略按钮；正文原本已限 120 字。
    payload['card']['elements'] = [el for el in payload['card']['elements'] if el.get('tag') != 'action']
    if len(_wire_body(payload, secret)) <= MAX_BODY_BYTES:
        return payload
    _log('单事件卡片超过 18KB，已跳过，未发送')
    return None


def plan_cards(events, resend_ids=(), secret='', deadline=None):
    """预览默认无签名；发送时传 secret，以最终序列化请求体分卡。"""
    viral, others = [], []
    for event in _valid_events(events, deadline):
        (viral if event['type'] in TWEET_EVENTS else others).append(event)
    cards, chunk = [], []
    for event in viral:
        if deadline is not None and time.monotonic() >= deadline:
            break
        candidate = chunk + [event]
        payload = build_viral_card(candidate, resend_ids)
        if chunk and (len(candidate) > MAX_EVENTS_PER_CARD
                      or len(_wire_body(payload, secret)) > MAX_BODY_BYTES):
            cards.append((build_viral_card(chunk, resend_ids), chunk))
            chunk = []
        if not chunk:
            single = _fit_single(build_viral_card([event], resend_ids), secret)
            if single is None:
                continue
            if single != build_viral_card([event], resend_ids):
                cards.append((single, [event]))
                continue
        chunk.append(event)
    if chunk:
        cards.append((build_viral_card(chunk, resend_ids), chunk))
    for event in others:
        if deadline is not None and time.monotonic() >= deadline:
            break
        builder = {'monitor_fault': build_fault_card, 'monitor_recovery': build_recovery_card,
                   'daily_summary': build_daily_card}[event['type']]
        payload = _fit_single(builder(event), secret)
        if payload is not None:
            cards.append((payload, [event]))
    return cards


# ---------------------------------------------------------------- 发送
def _sign(secret, timestamp):
    key = '{}\n{}'.format(timestamp, secret).encode('utf-8')
    return base64.b64encode(hmac.new(key, b'', hashlib.sha256).digest()).decode('ascii')


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _post_io(payload, webhook, secret, deadline):
    """子进程内分块读取；父进程的硬截止同时覆盖 DNS、TLS 和响应头慢流。"""
    data = _wire_body(payload, secret)
    if len(data) > MAX_BODY_BYTES:
        return False, False, '请求体超过 18KB，未发送'
    request = Request(webhook, data=data, method='POST',
                      headers={'Content-Type': 'application/json; charset=utf-8'})
    try:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False, False, '结果未知：本次调用截止'
        with build_opener(_NoRedirect()).open(request, timeout=remaining) as response:
            code = response.getcode()
            raw = bytearray()
            while not response.isclosed():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False, False, '结果未知：响应读取截止'
                response.fp.raw._sock.settimeout(remaining)
                block = response.read1(min(4096, MAX_RESPONSE_BYTES + 1 - len(raw)))
                if not block:
                    break
                raw.extend(block)
                if len(raw) > MAX_RESPONSE_BYTES:
                    return False, False, '结果未知：响应体超过 64KB'
    except HTTPError as error:
        code = error.code
        error.close()
        return False, False, 'HTTP {}'.format(code)
    except Exception as error:
        return False, False, '结果未知：请求失败 ' + error.__class__.__name__
    if code != 200:
        return False, False, 'HTTP {}'.format(code)
    try:
        parsed = json.loads(raw.decode('utf-8'))
    except (ValueError, UnicodeDecodeError):
        return False, False, '结果未知：响应不是 JSON'
    if not isinstance(parsed, dict):
        return False, False, '结果未知：响应不是 JSON 对象'
    business = parsed.get('code', parsed.get('StatusCode'))
    message = _clean_text(parsed.get('msg') or parsed.get('StatusMessage'), 60)
    if type(business) is not int:
        return False, False, '结果未知：响应缺少合法业务码'
    if business != 0:
        limited = business in RATE_LIMIT_CODES or any(word in message.lower() for word in RATE_LIMIT_WORDS)
        return False, limited, _redact('code={} msg={}'.format(business, message or '无'))
    return True, False, ''


def _post(payload, webhook, secret, deadline=None):
    deadline = deadline if deadline is not None else time.monotonic() + REQUEST_TIMEOUT
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        return False, False, '结果未知：本次调用截止'
    process = None
    try:
        # 使用独立短命运输进程，超时终止并回收；凭据仅在私有 stdin 管道，不进 argv/文件。
        process = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), '--_post-worker'],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        wire = json.dumps([payload, webhook, secret, deadline], ensure_ascii=True).encode('utf-8')
        out, _ = process.communicate(wire, timeout=max(.001, deadline - time.monotonic()))
        if process.returncode != 0:
            return False, False, '结果未知：运输进程失败'
        result = json.loads(out.decode('utf-8'))
        return bool(result[0]), bool(result[1]), _redact(str(result[2]), webhook, secret)
    except subprocess.TimeoutExpired:
        return False, False, '结果未知：本次调用截止'
    except Exception as error:
        return False, False, '结果未知：请求异常 ' + error.__class__.__name__
    finally:
        if process is not None and process.poll() is None:
            process.kill()
            process.communicate()


# ---------------------------------------------------------------- 主入口
def send_events(events, webhook=None, secret=None):
    """B1 同步入口；普通异常不外抛，显式模拟崩溃仍可用 SIGKILL/KeyboardInterrupt。"""
    context = _CREDENTIALS.set((webhook or '', secret or ''))
    try:
        return _send_events(events, webhook, secret)
    except Exception as error:
        _log('发送流程异常：' + error.__class__.__name__)
        return []
    finally:
        _CREDENTIALS.reset(context)


def _send_events(events, webhook=None, secret=None):
    global _NO_WEBHOOK_WARNED
    deadline = time.monotonic() + CALL_DEADLINE
    configured_webhook, configured_secret, _, _ = _config()
    webhook = (configured_webhook if webhook is None else webhook).strip()
    secret = (configured_secret if secret is None else secret).strip()
    if not isinstance(events, list):
        _log('传入事件不是列表，按空列表处理')
        return []
    usable = _valid_events(events, deadline)
    if not usable:
        return []
    if not webhook:
        if not _NO_WEBHOOK_WARNED:
            _log('未配置 FEISHU_RADAR_WEBHOOK，本次不发送')
            _NO_WEBHOOK_WARNED = True
        return []
    path, delivered = _ledger_path(), []
    try:
        with _LedgerLock(path, deadline):
            ledger, _ = _load_ledger(path)
            records = ledger['records']
            fresh, resend_ids = [], set()
            for event in usable:
                event_id = event['event_id']
                record = records.get(event_id, {})
                if not isinstance(record, dict):
                    record = {}
                if record.get('status') == 'sent':
                    delivered.append({'event_id': event_id, 'status': 'sent'})
                    continue
                if record.get('status') == 'unknown_exhausted':
                    continue
                if record.get('status') == 'sending':
                    if record.get('unknown_resends', int(bool(record.get('resend')))) >= 1:
                        record.update(status='unknown_exhausted', reason='未知结果补发预算已耗尽')
                        _log('未知结果补发预算已耗尽，不再自动发送')
                        continue
                    resend_ids.add(event_id)
                fresh.append(event)
            if not fresh:
                _write_ledger(path, ledger)
                return delivered
            plans = plan_cards(fresh, resend_ids, secret, deadline)
            # 下方每卡预写及最终提交都会保存 exhausted 迁移。
            for order, (payload, card_events) in enumerate(plans, start=1):
                if time.monotonic() >= deadline:
                    _log('本次调用时间预算用尽，剩余卡片未发送')
                    break
                for event in card_events:
                    event_id = event['event_id']
                    previous = records.get(event_id, {})
                    count = previous.get('unknown_resends', int(bool(previous.get('resend'))))
                    started_at = _iso()
                    # 旧版未保存首次时间；已有旧记录不能用最后一次 started_at 冒充首次。
                    first_attempt = previous.get('first_attempt_at') if previous else started_at
                    records[event_id] = {'status': 'sending', 'started_at': started_at, 'card': order,
                                         'type': event['type'], 'first_attempt_at': first_attempt,
                                         'resend': event_id in resend_ids,
                                         'unknown_resends': count + int(event_id in resend_ids)}
                _write_ledger(path, ledger)  # 补发预算必须在外发之前持久化。
                ok, limited, reason = _post(payload, webhook, secret, deadline)
                for event in card_events:
                    record = records[event['event_id']]
                    if ok:
                        record.update(status='sent', sent_at=_iso())
                    elif reason.startswith('结果未知：'):
                        record.update(status='unknown_exhausted' if record['unknown_resends'] >= 1
                                      else 'sending', reason=_clean_text(reason, 120))
                    else:
                        record.update(status='error', tried_at=_iso(), reason=_clean_text(reason, 120))
                if not ok:
                    _log('卡片发送失败：' + reason)
                _write_ledger(path, ledger)
                if ok:
                    delivered.extend({'event_id': event['event_id'], 'status': 'sent'} for event in card_events)
                if limited:
                    _log('命中飞书限频，本次剩余卡片不再发送')
                    break
            _write_ledger(path, ledger)
            return delivered
    except Exception as error:
        _log('发送流程异常：' + error.__class__.__name__)
        return delivered


# ---------------------------------------------------------------- CLI
def _load_events_file(path):
    try:
        data = json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError, UnicodeDecodeError) as error:
        raise SystemExit('事件文件读取失败：' + error.__class__.__name__)
    if isinstance(data, dict):
        data = data.get('events', [])
    return data if isinstance(data, list) else []


def _unknown_events():
    """只读原子账本快照；人工核对入口不修复文件，也不发送请求。"""
    try:
        ledger = json.loads(_ledger_path().read_text(encoding='utf-8'))
        if (not isinstance(ledger, dict) or ledger.get('version') != 1
                or not isinstance(ledger.get('records'), dict)):
            raise ValueError('账本结构损坏')
    except FileNotFoundError:
        return []
    except (OSError, ValueError, UnicodeDecodeError) as error:
        raise SystemExit('发送账本读取失败：' + error.__class__.__name__)
    events = []
    for event_id, record in ledger['records'].items():
        if not isinstance(record, dict) or record.get('status') != 'unknown_exhausted':
            continue
        first_attempt = record.get('first_attempt_at')
        events.append({'event_id': event_id, 'type': record.get('type') or '未知',
                       'first_attempt_at': first_attempt})
    return _redact(events)


class _SafeParser(argparse.ArgumentParser):
    def error(self, message):
        self.print_usage(sys.stderr)
        self.exit(2, _redact('参数错误：' + message) + '\n')


def main(argv=None):
    parser = _SafeParser(description='爆款监控飞书卡片发送器')
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument('--unknown', action='store_true', help='只读列出未知结果补发耗尽事件，不联网')
    modes.add_argument('--dry-run', metavar='EVENTS_JSON', help='只打印卡片 JSON，不联网')
    modes.add_argument('--test', action='store_true', help='发一张飞书连通测试卡')
    args = parser.parse_args(argv)
    if args.unknown:
        print(json.dumps(_unknown_events(), ensure_ascii=False, indent=2))
        return 0
    if args.dry_run:
        events = _load_events_file(args.dry_run)
        planned = plan_cards(events)
        print(json.dumps(_redact([{'cards': [payload], 'event_ids': [e['event_id'] for e in group]}
                          for payload, group in planned]), ensure_ascii=False, indent=2))
        return 0
    if args.test:
        webhook, secret, _, _ = _config()
        if not webhook:
            print(_redact('未配置 FEISHU_RADAR_WEBHOOK，未发送。'))
            return 2
        card = build_viral_card([{
            'event_id': 'radar:test:connectivity', 'type': 'new_fast',
            'tweet': {'tweet_id': '1', 'author': 'feishu_bot', 'followers': None,
                      'views': None, 'likes': None, 'retweets': None, 'replies': None,
                      'hourly_delta': None, 'text': '飞书连通测试卡：看到即表示通道正常。'}}])
        ok, _limited, reason = _post(card, webhook, secret)
        print(_redact('飞书连通测试：' + ('成功' if ok else '失败（' + reason + '）')))
        return 0 if ok else 1
    parser.print_help()
    return 0


if __name__ == '__main__':
    if sys.argv[1:] == ['--_post-worker']:
        try:
            payload, webhook, secret, deadline = json.loads(sys.stdin.buffer.read())
            _CREDENTIALS.set((webhook, secret))
            print(json.dumps(_redact(_post_io(payload, webhook, secret, deadline)), ensure_ascii=True))
        except Exception:
            print(json.dumps(_redact([False, False, '结果未知：运输进程异常'])))
    else:
        try:
            sys.exit(main())
        except Exception as error:
            print(_redact('命令执行失败：' + error.__class__.__name__), file=sys.stderr)
            sys.exit(2)
