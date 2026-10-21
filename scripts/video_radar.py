#!/usr/bin/env python3
"""独立视频榜采集器。仅 AIsa 读取；不操作反馈、发布或工作台服务。"""
import argparse
import copy
import datetime as dt
import fcntl
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time
from urllib.parse import urlencode, urlsplit

from video_radar_relevance import classify

TZ = dt.timezone(dt.timedelta(hours=8))
ORIGIN = 'https://api.aisa.one'
BASE = '/apis/v1/tikhub/'
SOURCES = ('xiaohongshu', 'douyin_realtime', 'douyin_daily')
LIMITS = dict(zip(SOURCES, (50, 30, 50)))


class RadarError(Exception):
    """只携带固定、安全的错误码，禁止原始响应/异常文本外泄。"""


def now():
    return dt.datetime.now(TZ)


def stamp(value=None):
    return (value or now()).astimezone(TZ).isoformat(timespec='microseconds')


def published(value):
    try:
        if isinstance(value, bool) or value is None:
            return None
        if isinstance(value, (int, float)) or re.fullmatch(r'\d+(\.\d+)?', str(value)):
            number = float(value)
            if number > 10**11:
                number /= 1000
            return dt.datetime.fromtimestamp(number, TZ) if number > 0 else None
        parsed = dt.datetime.fromisoformat(str(value))
        return parsed.replace(tzinfo=TZ) if parsed.tzinfo is None else parsed.astimezone(TZ)
    except (ValueError, TypeError, OverflowError, OSError):
        return None


def integer(value, zero_unknown=False):
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        number = value
    elif isinstance(value, str) and re.fullmatch(r'\d+', value):
        number = int(value)
    else:
        return None
    return number if number >= 0 and (number != 0 or not zero_unknown) else None


def safe_url(url, platform):
    if not isinstance(url, str) or any(ord(c) <= 32 for c in url):
        return ''
    try:
        u = urlsplit(url)
        host, prefix = ('www.xiaohongshu.com', '/explore/') if platform == 'xiaohongshu' else ('www.douyin.com', '/video/')
        valid = u.scheme == 'https' and u.netloc == host and re.fullmatch(re.escape(prefix) + r'[a-zA-Z0-9]+', u.path)
        return url if valid else ''
    except ValueError:
        return ''


def clean_strings(value, key):
    if isinstance(value, str):
        for part in (key, key[:12], key[-8:]):
            if part:
                value = value.replace(part, '[已隐藏]')
        return value
    if isinstance(value, dict):
        return {clean_strings(str(k), key): clean_strings(v, key) for k, v in value.items()
                if str(k).lower() not in ('authorization', 'api_key', 'apikey', 'access_token', 'refresh_token')}
    if isinstance(value, list):
        return [clean_strings(v, key) for v in value]
    return value


def validate_gateway(url):
    try:
        u = urlsplit(url)
        if u.scheme != 'https' or u.netloc != 'api.aisa.one' or not u.path.startswith(BASE) or u.fragment:
            raise RadarError('gateway_refused')
    except ValueError:
        raise RadarError('gateway_refused') from None


def curl_json(url, method, params, key, timeout):
    validate_gateway(url)
    if any(c in key for c in '\r\n\x00'):
        raise RadarError('credential_invalid')
    # -q 禁用用户 curlrc；不加 -L。Key 只经 stdin，既不进 argv 也不落盘。
    args = ['curl', '-q', '--silent', '--proto', '=https', '--connect-timeout', str(min(10, timeout)),
            '--max-time', str(timeout), '--max-filesize', '16777216', '--config', '-',
            '--request', method, '--write-out', '\n%{http_code}', url]
    escaped = key.replace('\\', '\\\\').replace('"', '\\"')
    config = 'header = "Authorization: Bearer ' + escaped + '"\nheader = "Content-Type: application/json"\n'
    if params is not None:
        body = json.dumps(params, ensure_ascii=False).replace('\\', '\\\\').replace('"', '\\"')
        config += 'data = "' + body + '"\n'
    try:
        proc = subprocess.run(args, input=config.encode(), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                              timeout=timeout + .1, check=False)
    except (subprocess.TimeoutExpired, OSError):
        raise RadarError('request_timeout_or_transport') from None
    if proc.returncode:
        raise RadarError('transport_' + str(proc.returncode))
    try:
        raw, http = proc.stdout.rsplit(b'\n', 1)
        if http != b'200':
            raise RadarError('http_' + http.decode('ascii', errors='ignore')[:3])
        data = json.loads(raw)
        if not isinstance(data, dict) or data.get('code') != 200:
            raise RadarError('gateway_business_failed')
        return clean_strings(data, key)
    except (ValueError, TypeError):
        raise RadarError('invalid_json') from None


def read_json(path, default):
    try:
        return json.loads(path.read_text())
    except FileNotFoundError:
        return copy.deepcopy(default)


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix='.video-radar-', suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2, allow_nan=False)
            handle.write('\n')
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def cleanup(root, at, retention):
    # 不遍历符号链接；rmtree 的 fd-safe 实现也不会跟随内部符号链接。
    directories = [root]
    snapshots = root / 'snapshots'
    if snapshots.exists() and not snapshots.is_symlink():
        for child in snapshots.iterdir():
            if child.is_symlink() or not child.is_dir():
                continue
            try:
                day = dt.date.fromisoformat(child.name)
            except ValueError:
                continue
            if day <= at.date() - dt.timedelta(days=retention):
                shutil.rmtree(child)
            else:
                directories.append(child)
    for directory in directories:
        for temp in directory.glob('.video-radar-*.tmp'):
            if not temp.is_symlink() and temp.is_file():
                temp.unlink()


def acquire_lock(root):
    handle = open(root / '.lock', 'a+')
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close()
        return None
    # 不删除 lock inode；SIGKILL 后 flock 自动释放，无 PID 复用问题。
    handle.seek(0)
    handle.truncate()
    handle.write(str(os.getpid()))
    handle.flush()
    return handle


def envelope(data, kind):
    if not isinstance(data, dict) or data.get('code') != 200:
        raise RadarError('gateway_business_failed')
    body = data.get('data')
    if kind == 'multi':
        if not isinstance(body, list) or not body:
            raise RadarError('multi_array_missing')
        items, valid = [], False
        for block in body:
            if not isinstance(block, dict):
                continue
            if 'status_code' in block and block['status_code'] != 0:
                raise RadarError('multi_business_failed')
            if block.get('status_code') == 0 and isinstance(block.get('business_data'), list):
                valid = True
                for entry in block['business_data']:
                    if isinstance(entry, dict):
                        payload = entry.get('data')
                        item = payload.get('aweme_info') if isinstance(payload, dict) else None
                        if isinstance(item, dict) and item.get('aweme_id'):
                            items.append(item)
        if not valid:
            raise RadarError('multi_business_array_missing')
        if not items:
            raise RadarError('empty_result_unverified')
        return items
    if not isinstance(body, dict):
        raise RadarError('platform_body_missing')
    if kind in ('xhs_search', 'xhs_detail'):
        if body.get('code') != 0 or body.get('success') is not True:
            raise RadarError('xhs_business_failed')
        rows = body.get('data', {}).get('items') if kind == 'xhs_search' and isinstance(body.get('data'), dict) else body.get('data')
    elif kind == 'index':
        if 'code' in body and body['code'] != 0:
            raise RadarError('index_business_failed')
        rows = body.get('data')
    else:
        if body.get('code') != 0:
            raise RadarError('board_business_failed')
        rows = body.get('data', {}).get('objs') if isinstance(body.get('data'), dict) else None
    if not isinstance(rows, list):
        raise RadarError('required_array_missing')
    if not rows:
        raise RadarError('empty_result_unverified')
    return rows


# 黄总 2026-10-18 23:10 口径：每条候选保存简介前 200 字，供人工核对系统按简介做的相关性判断。
DESC_EXCERPT_LIMIT = 200


def _desc_excerpt(raw, kind):
    """平台简介/正文原文的前 200 个字符（按 Python 字符计）。

    小红书（xhs_*）取 ``desc``；抖音 multi 取 ``desc``；热点宝（dy_board_*）与指数库
    （dy_index）没有单独简介字段，一律返回 ``''``。平台没给就是 ``''``，
    **不拿标题顶替**（标题在相关性判断里已经单独参与，不该混进简介留档）。
    非字符串（数字/对象/数组）一律当没给，不凭空造文本。
    """
    if not isinstance(raw, dict) or (not kind.startswith('xhs') and kind != 'dy_multi'):
        return ''
    value = raw.get('desc')
    if not isinstance(value, str):
        return ''
    return value[:DESC_EXCERPT_LIMIT]


def normalize(raw, kind, observed, config, detail=False):
    try:
        return _normalize(raw, kind, observed, config, detail)
    except (TypeError, ValueError, AttributeError, IndexError, KeyError):
        return None


def _normalize(raw, kind, observed, config, detail=False):
    if not isinstance(raw, dict):
        return None
    if kind.startswith('xhs'):
        if raw.get('type') != 'video':
            return None
        platform, ident = 'xiaohongshu', str(raw.get('id') or '')
        title = raw.get('title') or raw.get('desc') or ''
        text = str(title) + '\n' + str(raw.get('desc') or '')
        when = published(raw.get('time') if detail else raw.get('timestamp'))
        metrics = [raw.get(k) for k in ('liked_count', 'collected_count', 'comments_count', 'shared_count')]
        author = raw.get('user') or {}
        nickname, fans, plays = author.get('nickname') or author.get('name') or '', None, None
        url = 'https://www.xiaohongshu.com/explore/' + ident
        if raw.get('xsec_token'):
            url += '?' + urlencode({'xsec_token': raw['xsec_token'], 'xsec_source': 'pc_search'})
        images = raw.get('images_list') or [{}]
        cover = images[0].get('url') or ''
    elif kind == 'dy_multi':
        if raw.get('aweme_type') != 0 or raw.get('media_type') != 4:
            return None
        platform, ident = 'douyin', str(raw.get('aweme_id') or '')
        title = raw.get('desc') or raw.get('item_title') or ''
        text, when = title, published(raw.get('create_time'))
        stats, author = raw.get('statistics') or {}, raw.get('author') or {}
        metrics = [stats.get(k) for k in ('digg_count', 'collect_count', 'comment_count', 'share_count')]
        nickname = author.get('nickname') or ''
        fans, plays = None, None
        covers = ((raw.get('video') or {}).get('cover') or {}).get('url_list') or ['']
        cover, url = covers[0], 'https://www.douyin.com/video/' + ident
    elif kind == 'dy_index':
        platform, ident = 'douyin', str(raw.get('itemId') or '')
        title = raw.get('title') or ''
        text, when = title, published(raw.get('createTime'))
        metrics = [raw.get('likes'), None, None, None]
        nickname, fans, plays = raw.get('nickname') or '', integer(raw.get('fans'), True), None
        cover, url = raw.get('thumbnail') or '', raw.get('url') or ''
    else:
        if raw.get('media_type') != 4:
            return None
        platform, ident = 'douyin', str(raw.get('item_id') or '')
        title = raw.get('item_title') or ''
        text, when = title, published(raw.get('publish_time'))
        metrics = [raw.get('like_cnt'), None, None, None]
        nickname, fans, plays = raw.get('nick_name') or '', integer(raw.get('fans_cnt')), integer(raw.get('play_cnt'))
        cover, url = raw.get('item_cover_url') or '', 'https://www.douyin.com/video/' + ident
    if not re.fullmatch(r'[a-zA-Z0-9]+', ident) or when is None or when > observed:
        return None
    if kind in ('xhs_latest', 'xhs_hot', 'dy_multi', 'dy_board_video_1h', 'dy_board_low_fan_1h') and (observed - when).total_seconds() > 86400:
        return None
    relevance, reason = classify(text, config)
    if relevance is None:
        return None
    metrics = [integer(v) for v in metrics]
    average = metrics[0] / max(.5, (observed - when).total_seconds()/3600) if metrics[0] is not None else None
    return dict(key=platform+':'+ident, platform=platform, id=ident, sources=[kind], title=str(title),
                desc_excerpt=_desc_excerpt(raw, kind),
                url=safe_url(url, platform), author=str(nickname), author_fans=fans, published_at=stamp(when),
                first_seen_at=stamp(observed), last_seen_at=stamp(observed), likes=metrics[0], collects=metrics[1],
                comments=metrics[2], shares=metrics[3], plays=plays, likes_per_hour=round(average, 6) if average is not None else None,
                likes_delta_per_hour=None, score=round(average or 0, 6), relevance=relevance, relevance_reason=reason,
                flags=['low_fan_hot'] if integer(raw.get('isLowFansButHot')) == 1 or kind in ('dy_board_low_fan', 'dy_board_low_fan_1h') else [],
                cover=cover if isinstance(cover, str) and cover.startswith('https://') else '')


def reclassify_cached(item, config):
    """仅更新缓存相关性，不伪造观察时刻、指标或增量。摘要未持久化时保留已有技术词证据。"""
    item = copy.deepcopy(item)
    prediction, reason = classify(item['title'], config)
    had_technical_evidence = item['relevance_reason'].startswith('工具或技术词：')
    if prediction is None:
        return None if reason == '否定AI制作且无技术语义' and not had_technical_evidence else item
    if had_technical_evidence and prediction != 'relevant':
        return item
    item.update(relevance=prediction, relevance_reason=reason)
    return item


def observe(item, previous, daily=False):
    if previous:
        item['first_seen_at'] = previous['first_seen_at']
        item['sources'] = sorted(set(item['sources'] + previous['sources']))
        item['flags'] = sorted(set(item['flags'] + previous['flags']))
        for field in ('author_fans', 'plays'):
            if item[field] is None and any(s.startswith('dy_board_') for s in previous['sources']):
                item[field] = previous[field]
        # 同一条视频被没有简介字段的来源（热点宝/指数库）再次观测到时，别把已采到的简介抹掉
        if not item.get('desc_excerpt') and previous.get('desc_excerpt'):
            item['desc_excerpt'] = previous['desc_excerpt']
        seconds = (published(item['last_seen_at']) - published(previous['last_seen_at'])).total_seconds()
        if not daily and seconds > 0 and item['likes'] is not None and previous['likes'] is not None:
            item['likes_delta_per_hour'] = round((item['likes'] - previous['likes']) * 3600 / seconds, 6)
    value = item['likes_delta_per_hour'] if not daily and item['likes_delta_per_hour'] is not None else item['likes_per_hour']
    item['score'] = value if value is not None else 0
    return item


class Client:
    def __init__(self, config, reserve, finish, deadline):
        self.config, self.reserve, self.finish, self.deadline = config, reserve, finish, deadline
        self.key = os.environ.get('AISA_API_KEY', '')
        if not self.key:
            raise RadarError('AISA_API_KEY_missing')

    def request(self, method, path, params, query=False):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise RadarError('round_deadline')
        url = ORIGIN + path
        if method == 'GET' or query:
            url += '?' + urlencode(params)
        validate_gateway(url)
        record = {'method': method, 'path': path, 'started_at': stamp(), 'status': 'in_progress'}
        self.reserve(record)  # 请求启动前落盘；SIGKILL/超时失败也计费计数。
        started = time.monotonic()
        try:
            response = curl_json(url, method, None if method == 'GET' or query else params,
                                 self.key, min(30, self.config['request_timeout_seconds'], remaining))
            record.update(status='http_ok', finished_at=stamp(), elapsed_s=round(time.monotonic()-started, 3))
            return response, published(record['finished_at'])
        except RadarError as error:
            record.update(status='failed', error=str(error), finished_at=stamp(), elapsed_s=round(time.monotonic()-started, 3))
            raise
        finally:
            self.finish()


def empty_page():
    return dict(schema='video-radar-page/v1', generated_at=stamp(), keyword='AI',
                sources={s: dict(last_attempt_at=None, last_success_at=None, status='never', note='') for s in SOURCES},
                today={}, lists={s: [] for s in SOURCES})


def run(root, mode='auto', client_factory=Client, config=None):
    config = config or json.loads(Path(__file__).with_name('video_radar_config.json').read_text())
    storage = Path(root) / 'workspace' / 'video-radar'
    for ancestor in (Path(root)/'workspace', storage):
        if ancestor.is_symlink():
            raise RadarError('storage_symlink_refused')
    storage.mkdir(parents=True, exist_ok=True)
    lock = acquire_lock(storage)
    if lock is None:
        print('video-radar：已有轮次持锁，本轮立即退出', flush=True)
        return None
    try:
        return run_locked(storage, mode, client_factory, config)
    finally:
        lock.close()


def run_locked(storage, mode, client_factory, config):
    started = now()
    deadline = time.monotonic() + min(600, config['round_timeout_seconds'])
    cleanup(storage, started, config['snapshot_retention_days'])
    page = read_json(storage/'page.json', empty_page())
    state = read_json(storage/'state.json', {'items': {}, 'daily': {}})
    stat_path = storage / ('daily-' + started.date().isoformat() + '.json')
    stats = read_json(stat_path, dict(date=started.date().isoformat(), rounds=0, api_calls=0,
                                    xiaohongshu_ok=0, douyin_realtime_ok=0, douyin_daily_ok=0, runs=[]))
    # daily 重试次数也存入调用账本，避免进程崩溃后重复绕过 3 次上限。
    daily_attempts = sum(bool(r.get('daily_attempt')) for r in stats['runs'])
    daily_done = any(r.get('sources', {}).get('douyin_daily', {}).get('status') == 'ok' for r in stats['runs'])
    daily_due = mode != 'hourly' and not daily_done and daily_attempts < 3
    for old_run in stats['runs']:
        if 'finished_at' not in old_run:
            old_run['status'] = 'interrupted'
            old_run['interrupted_detected_at'] = stamp(started)
            for call in old_run['calls']:
                if call['status'] == 'in_progress':
                    call['status'] = 'interrupted_unknown'
            for source in old_run.get('planned_sources', []):
                old_run['sources'].setdefault(source, {'status': 'interrupted', 'successes': 0, 'failures': 1})
    planned = ([] if mode == 'daily' else list(SOURCES[:2])) + (['douyin_daily'] if daily_due else [])
    record = dict(started_at=stamp(started), mode=mode, status='running', daily_attempt=daily_due, planned_sources=planned, calls=[], sources={})
    stats['runs'].append(record)
    stats['rounds'] += 1
    atomic_json(stat_path, stats)

    def reserve(call):
        record['calls'].append(call)
        stats['api_calls'] += 1
        atomic_json(stat_path, stats)

    def finish():
        atomic_json(stat_path, stats)

    client = client_factory(config, reserve, finish, deadline)
    # 缓存标题按现行口径刷新；摘要中已确认的技术词仍按旧证据保留。
    state['items'] = {k: value for k, item in state['items'].items()
                      if (value := reclassify_cached(item, config)) is not None}
    state['daily'] = {source: [value for item in part if (value := reclassify_cached(item, config)) is not None]
                      for source, part in state.get('daily', {}).items()}
    original = copy.deepcopy(state['items'])
    daily_baseline = {}
    for part in state.get('daily', {}).values():
        for item in part:
            existing = daily_baseline.get(item['key'])
            if existing is None or item['first_seen_at'] < existing['first_seen_at']:
                daily_baseline[item['key']] = copy.deepcopy(item)
    observed_items = {}
    for key, item in original.items():
        when = published(item.get('published_at'))
        if when and 0 <= (started-when).total_seconds() < 86400:
            observed_items[key] = item
    state['items'] = observed_items

    def add(item, destination, daily=False):
        if item is None:
            return
        key = item['key']
        baseline = original.get(key) if not daily else daily_baseline.get(key)
        item = observe(item, baseline, daily)
        history = [i for i in (original.get(key), daily_baseline.get(key), state['items'].get(key)) if i]
        if history:
            item['first_seen_at'] = min([item['first_seen_at']] + [i['first_seen_at'] for i in history])
        current = destination.get(key)
        if current:
            item['first_seen_at'] = min(item['first_seen_at'], current['first_seen_at'])
            for field in ('author_fans', 'plays', 'collects', 'comments', 'shares'):
                if item[field] is None:
                    item[field] = current[field]
                elif current[field] is None:
                    current[field] = item[field]
            # 简介两向补齐：两次观测总有一侧带 desc 才不丢；旧条目没这个键也不会报错
            for a, b in ((current, item), (item, current)):
                if not a.get('desc_excerpt') and b.get('desc_excerpt'):
                    a['desc_excerpt'] = b['desc_excerpt']
            item['sources'] = sorted(set(item['sources'] + current['sources']))
            item['flags'] = sorted(set(item['flags'] + current['flags']))
            if published(current['last_seen_at']) > published(item['last_seen_at']):
                current['sources'], current['flags'] = item['sources'], item['flags']
                current['first_seen_at'] = item['first_seen_at']
                item = current
        destination[key] = item

    def request(method, endpoint, params, kind, query=False):
        response, at = client.request(method, BASE+endpoint, params, query)
        try:
            rows = envelope(response, kind)
        except RadarError:
            if record['calls']:
                record['calls'][-1]['status'] = 'business_failed'
                finish()
            raise
        return rows, response['data'], at

    def tracked(platform):
        at = now()
        state['items'] = {k: i for k, i in state['items'].items() if published(i['published_at']) and 0 <= (at-published(i['published_at'])).total_seconds() < 86400}
        return {k: i for k, i in state['items'].items() if i['platform'] == platform}

    def complete(source, items, successes, failures, note=''):
        status = 'ok' if successes and not failures else 'partial' if successes else 'failed'
        info = page['sources'][source]
        attempts = record['calls'][before:]
        info.update(last_attempt_at=attempts[0]['started_at'] if attempts else stamp(), status=status, note=note)
        if status == 'ok':
            info['last_success_at'] = stamp()
            stats[source+'_ok'] += 1
        if successes:
            candidates = list(items.values())
            page['lists'][source] = sorted((i for i in candidates if i['likes'] is not None and i['likes'] >= config['min_likes']),
                                           key=lambda i: (-i['score'], i['key']))[:LIMITS[source]]
        record['sources'][source] = dict(status=status, successes=successes, failures=failures,
                                         api_calls=len(record['calls']) - before)

    if mode != 'daily':
        before = len(record['calls'])
        found, success, failed, errors = {}, 0, 0, []
        for sort, source in [('time_descending', 'xhs_latest'), ('popularity_descending', 'xhs_hot')]:
            params = dict(keyword=config['keyword'], page=1, sort_type=sort, note_type='视频笔记', time_filter='一天内')
            for page_number in range(1, 4):
                params['page'] = page_number
                try:
                    rows, body, at = request('GET', 'xiaohongshu/app_v2/search_notes', params, 'xhs_search')
                    success += 1
                    for row in rows:
                        raw = row.get('note') if isinstance(row, dict) else None
                        normalized = normalize(raw, source, at, config)
                        if normalized is None and isinstance(raw, dict) and raw.get('id'):
                            state['items'].pop('xiaohongshu:'+str(raw['id']), None)
                        add(normalized, found)
                    if page_number < 3:
                        if not body.get('search_id') or not body.get('search_session_id'):
                            raise RadarError('pagination_tokens_missing')
                        params.update(search_id=body['search_id'], search_session_id=body['search_session_id'])
                except RadarError as error:
                    failed += 1
                    errors.append(str(error))
                    break
        missing = [i for k, i in state['items'].items() if i['platform'] == 'xiaohongshu' and k not in found]
        for item in sorted(missing, key=lambda i: -i['score'])[:30]:
            try:
                rows, _, at = request('GET', 'xiaohongshu/app_v2/get_video_note_detail', {'note_id': item['id']}, 'xhs_detail')
                target = next((r for r in rows if isinstance(r, dict) and str(r.get('id')) == item['id']), None)
                if target is None:
                    raise RadarError('detail_target_missing')
                normalized = normalize(target, item['sources'][0], at, config, detail=True)
                if normalized is not None:
                    add(normalized, found)
                else:
                    state['items'].pop(item['key'], None)
                success += 1
            except RadarError as error:
                failed += 1
                errors.append(str(error))
        for key, item in found.items():
            state['items'][key] = item
        candidates = tracked('xiaohongshu')
        complete('xiaohongshu', candidates, success, failed, '搜索及复采；失败：'+','.join(sorted(set(errors))) if errors else '最新/最热各3页；复采最多30条')
        before = len(record['calls'])
        found, rejected, success, failed, parts, errors = {}, set(), 0, 0, {}, []
        realtime_specs = []
        for page_number in (1, 2):
            realtime_specs.append(('dy_board_video_1h', 'douyin/billboard/fetch_hot_total_video_list',
                dict(page=page_number, page_size=50, date_window=1, sub_type=1001, keyword=config['keyword'], tags=[]), 'board'))
        realtime_specs.append(('dy_board_low_fan_1h', 'douyin/billboard/fetch_hot_total_low_fan_list',
            dict(page=1, page_size=50, date_window=1, keyword=config['keyword'], tags=[]), 'board'))
        realtime_specs.append(('dy_multi', 'douyin/search/fetch_multi_search', dict(keyword=config['keyword'], cursor=0,
                sort_type='2', publish_time='1', filter_duration='0', content_type='1', search_id='', backtrace=''), 'multi'))
        for source, endpoint, params, kind in realtime_specs:
            part_name = source + (':p'+str(params['page']) if 'page' in params else '')
            try:
                rows, _, at = request('POST', endpoint, params, kind)
                for raw in rows:
                    if not isinstance(raw, dict):
                        continue
                    video = raw.get('aweme_type') == 0 and raw.get('media_type') == 4 if kind == 'multi' else raw.get('media_type') == 4
                    if not video:
                        rejected.add(str(raw.get('aweme_id') or raw.get('item_id') or 'unknown'))
                    normalized = normalize(raw, source, at, config)
                    if normalized is None:
                        state['items'].pop('douyin:'+str(raw.get('aweme_id') or raw.get('item_id') or ''), None)
                    add(normalized, found)
                success += 1
                parts[part_name] = 'ok'
            except RadarError as error:
                failed += 1
                errors.append(str(error))
                parts[part_name] = 'failed'
        state['items'].update(found)
        candidates = tracked('douyin')
        note = f'热点宝近1小时视频榜2页+低粉榜；multi首页补充；排除图集/非视频{len(rejected)}条'
        if errors:
            note += '；失败：'+','.join(sorted(set(errors)))
        complete('douyin_realtime', candidates, success, failed, note)
        record['sources']['douyin_realtime']['parts'] = parts
    if daily_due:
        before = len(record['calls'])
        success, failed, errors = 0, 0, []
        daily_parts = state.setdefault('daily', {})
        specs = [('dy_index', 'douyin/index/fetch_item_query', dict(query=config['keyword'], category_id=0, date_type=3, label_type=0, duration_type=0), 'index', True)]
        for suffix, name, window in [('video','video',24), ('low_fan','low_fan',24), ('high_like','high_like',24), ('high_play','high_play',24)]:
            params = dict(date_window=window, keyword=config['keyword'], page=1, page_size=10, tags=[])
            if suffix == 'video':
                params['sub_type'] = 1001
            specs.append(('dy_board_'+name, 'douyin/billboard/fetch_hot_total_'+suffix+'_list', params, 'board', False))
        for source, endpoint, params, kind, query in specs:
            try:
                rows, _, at = request('POST', endpoint, params, kind, query)
                part = {}
                for raw in rows:
                    add(normalize(raw, source, at, config), part, daily=True)
                daily_parts[source] = list(part.values())
                success += 1
            except RadarError as error:
                failed += 1
                errors.append(str(error))
        combined = {}
        for part in daily_parts.values():
            for item in part:
                add(copy.deepcopy(item), combined, daily=True)
        complete('douyin_daily', combined, success, failed, '数据约晚1–2天'+('；'+','.join(sorted(set(errors))) if errors else ''))
    page['keyword'], page['generated_at'] = config['keyword'], stamp()
    page['today'] = {k: stats[k] for k in ('date','rounds','xiaohongshu_ok','douyin_realtime_ok','douyin_daily_ok','api_calls')}
    record.update(finished_at=stamp(), api_calls=len(record['calls']), status='complete')
    atomic_json(stat_path, stats)
    atomic_json(storage/'state.json', state)
    snapshot_dir = storage/'snapshots'/started.date().isoformat()
    if (storage/'snapshots').is_symlink() or snapshot_dir.is_symlink():
        raise RadarError('snapshot_symlink_refused')
    atomic_json(snapshot_dir/(started.strftime('%H%M')+'.json'), page)
    atomic_json(storage/'page.json', page)
    summary = dict(started_at=record['started_at'], finished_at=record['finished_at'], api_calls=len(record['calls']),
                   elapsed_s=round((now()-started).total_seconds(),3), sources=record['sources'],
                   counts={s:len(page['lists'][s]) for s in SOURCES})
    print(json.dumps(summary, ensure_ascii=False), flush=True)
    return page


def main():
    parser = argparse.ArgumentParser(description='采集 AI 视频榜')
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--mode', choices=('auto','hourly','daily'), default='auto')
    args = parser.parse_args()
    try:
        run(args.root, args.mode)
    except RadarError as error:
        print('video-radar：'+str(error), flush=True)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
