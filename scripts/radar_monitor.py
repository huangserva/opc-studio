#!/usr/bin/env python3
"""Hourly radar monitor. state.json is the transaction; JSONL/daily are projections.

No gateway calls except the single A1 refresh entry point. No network sender is
built in: B2 must implement send_events(events), idempotent by event_id, returning
[{"event_id": ..., "status": "sent"}] only after confirmed delivery. A remote
send and a local rename cannot be one transaction; receiver-side idempotency is
required for the success-before-local-commit crash window.
"""
from __future__ import annotations

import argparse
import copy
from datetime import datetime, timedelta, timezone
import fcntl
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import tempfile
from types import SimpleNamespace
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
SAFE_INT_MAX = 2 ** 53 - 1
TWEET_ID_RE = re.compile(r'[0-9]{1,25}')
PAGE_NUMBERS = ('hourly_delta', 'views_delta', 'interval_hours', 'views')
TWEET_EVENTS = {'lowfan_burst', 'new_fast', 'hourly_delta', 'upgrade_100k'}


def stamp(value):
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise ValueError('需要带时区的时间')
    return value.astimezone(timezone.utc)


def iso(value):
    return stamp(value).isoformat()


def number(value):
    # Compare integers before any float conversion: math.isfinite(10**400)
    # itself raises OverflowError. bool and numeric strings are not metrics.
    if type(value) is int:
        return value if 0 <= value <= SAFE_INT_MAX else None
    if type(value) is float and math.isfinite(value) and 0 <= value <= SAFE_INT_MAX:
        return value
    return None


def validate_page_hourly(page):
    # Optional for old B1 state, strict when present. Never re-export unvalidated
    # nested state; a failure uses the existing damaged-state rebuild path.
    if (not isinstance(page, dict)
            or set(page) != {'schema', 'run_id', 'generated_at', 'tweets'}
            or type(page.get('schema')) is not int or page['schema'] != 1
            or not isinstance(page.get('run_id'), str) or not page['run_id']
            or not isinstance(page.get('tweets'), dict)):
        raise ValueError('页面投影状态结构损坏')
    def utc_time(value):
        if not isinstance(value, str) or not value.endswith('Z'):
            raise ValueError('页面投影时间损坏')
        stamp(value)
    utc_time(page.get('generated_at'))
    fields = {*PAGE_NUMBERS, 'fetched_at', 'is_stale', 'hit', 'pushed'}
    for tid, row in page['tweets'].items():
        if (not isinstance(tid, str) or TWEET_ID_RE.fullmatch(tid) is None
                or not isinstance(row, dict) or set(row) != fields):
            raise ValueError('页面投影帖子结构损坏')
        for field in PAGE_NUMBERS:
            if row[field] is not None and number(row[field]) is None:
                raise ValueError('页面投影数值损坏')
        if (type(row['is_stale']) is not bool or type(row['pushed']) is not bool
                or (row['hit'] is not None and
                    (not isinstance(row['hit'], str) or row['hit'] not in TWEET_EVENTS))):
            raise ValueError('页面投影标记损坏')
        utc_time(row['fetched_at'])


def scrub(value):
    if isinstance(value, dict):
        return {str(k): scrub(v) for k, v in value.items()}
    if isinstance(value, list):
        return [scrub(v) for v in value]
    if isinstance(value, str):
        key = os.environ.get('AISA_API_KEY', '')
        if key:
            value = value.replace(key, '[已隐藏]')
        return re.sub(r'sk-[A-Za-z0-9_-]+', '[已隐藏]', value)
    return value


class PathBoundaryError(ValueError):
    """A project-owned path contains a link or escapes the trusted root."""


def checked_path(root, path):
    # Only the explicitly supplied project root is trusted. Its ancestors may
    # contain links (macOS /tmp -> /private/tmp); every component below it may not.
    root = Path(root).absolute()
    path = Path(path).absolute()
    try:
        relative = path.relative_to(root)
        if '..' in relative.parts:
            raise ValueError('parent traversal')
        anchor = root.resolve()
        current = anchor
        for part in relative.parts:
            current = current / part
            if current.is_symlink():
                raise ValueError('symbolic link')
        current.resolve().relative_to(anchor)
    except (ValueError, OSError, RuntimeError):
        raise PathBoundaryError('项目路径边界无效') from None
    return current


def atomic_write(path, data, *, root=None):
    path = checked_path(root, path) if root is not None else Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(prefix='.' + path.name + '.', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if root is not None:
            checked_path(root, path)
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def dump(value):
    return json.dumps(scrub(value), ensure_ascii=False, sort_keys=True, allow_nan=False)


def load_module(path):
    name = '_radar_' + hashlib.sha256(str(path).encode()).hexdigest()[:16]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def read_snapshot(root, excluded_run_ids=(), started_at=None):
    """Read only the snapshot committed by A1's public embedded manifest."""
    root = Path(root)
    bundle = json.loads(checked_path(root, root / 'public/twitter-radar-bundle.json').read_text())
    manifest = bundle.get('manifest')
    if (not isinstance(manifest, dict) or not manifest.get('run_id')
            or bundle.get('run_id') != manifest['run_id']
            or manifest.get('schema_version') != 'radar-1'):
        raise ValueError('公开 manifest 缺失或版本不一致')
    relative = manifest.get('snapshot_file')
    digest = manifest.get('snapshot_sha256')
    if (not isinstance(relative, str) or Path(relative).is_absolute()
            or not isinstance(digest, str) or re.fullmatch('[0-9a-f]{64}', digest) is None):
        raise ValueError('快照引用错误')
    store = root / 'workspace/twitter-radar'
    if Path(relative).parts[:1] != ('snapshots',):
        raise PathBoundaryError('快照路径越界')
    path = checked_path(root, store / relative)
    if path.is_symlink() or not path.is_file():
        raise ValueError('快照文件不可用')
    payload = path.read_bytes()
    if hashlib.sha256(payload).hexdigest() != digest:
        raise ValueError('快照 SHA 不一致')
    raw = json.loads(payload)
    meta = raw.get('run', raw)
    if (raw.get('schema_version', meta.get('schema_version')) != 'radar-1'
            or meta.get('run_id') != manifest['run_id']
            or meta['run_id'] in excluded_run_ids):
        raise ValueError('快照版本或轮次不一致')
    begin, end = stamp(meta['started_at']), stamp(meta['finished_at'])
    if (end < begin or stamp(manifest['started_at']) != begin
            or (started_at is not None and begin < stamp(started_at))):
        raise ValueError('不是本轮新采样')
    targets_raw = json.loads(checked_path(root, root / 'scripts/radar_targets.json').read_text())
    targets = targets_raw.get('targets', targets_raw.get('handles')) if isinstance(targets_raw, dict) else targets_raw
    if not isinstance(targets, list) or not targets:
        raise ValueError('名单格式错误')
    handles = [x.get('handle') if isinstance(x, dict) else x for x in targets]
    if not all(isinstance(x, str) and re.fullmatch(r'@?[A-Za-z0-9_]{1,15}', x) for x in handles):
        raise ValueError('名单账号错误')
    targets = {x.lstrip('@').lower() for x in handles}
    if len(targets) != len(handles):
        raise ValueError('重复名单账号')
    accounts_raw = raw['accounts']
    if isinstance(accounts_raw, dict):
        for handle, row in accounts_raw.items():
            if (row.get('handle', '').lstrip('@').lower() != handle.lstrip('@').lower()):
                raise ValueError('账号映射键与账号身份不一致')
        accounts_raw = [dict(row, handle=handle) for handle, row in accounts_raw.items()]
    accounts = {}
    success_statuses = {'ok', 'empty', 'partial'}
    for row in accounts_raw:
        handle = row['handle'].lstrip('@').lower()
        if handle not in targets or handle in accounts:
            raise ValueError('重复或非名单账号')
        ok = row.get('status') in success_statuses
        if type(row.get('ok')) is not bool or row['ok'] != ok:
            raise ValueError('账号状态与 ok 不一致')
        if type(row.get('calls')) is not int or row['calls'] < 0:
            raise ValueError('账号调用数错误')
        if ok and row['calls'] == 0:
            raise ValueError('成功账号没有采集调用')
        if row.get('status') == 'not_started' and row['calls'] != 0:
            raise ValueError('未开始账号存在调用')
        accounts[handle] = row
    success = sum(x['ok'] for x in accounts.values())
    expected = {'target_count': len(targets), 'attempted': sum(x['calls'] > 0 for x in accounts.values()),
                'success': success, 'failed': len(targets) - success,
                'api_calls': sum(x['calls'] for x in accounts.values()),
                'partial': sum(x['status'] == 'partial' for x in accounts.values()),
                'empty': sum(x['status'] == 'empty' for x in accounts.values())}
    if (set(accounts) != targets or any(type(meta.get(k)) is not int or meta[k] != v
                                       for k, v in expected.items())
            or type(meta.get('coverage')) not in (int, float)
            or meta['coverage'] != round(success / len(targets), 4)):
        raise ValueError('账号状态与轮次计数不一致')
    tweets = raw.get('tweets')
    if tweets is None:
        tweets = [tweet for account in accounts.values() for tweet in account.get('tweets', [])]
    normalized = {}
    for row in tweets:
        # A1 marks its own queried-handle fallback when no author was observed.
        # Such a value is not author evidence for monitor alerts.
        if row.get('author_handle_observed') is False:
            continue
        handle = (row.get('author_handle') or '').lstrip('@').lower()
        queried = (row.get('queried_handle') or '').lstrip('@').lower()
        tid = row.get('tweet_id')
        if (not isinstance(tid, str) or TWEET_ID_RE.fullmatch(tid) is None
                or not re.fullmatch(r'[A-Za-z0-9_]{1,15}', handle) or queried not in targets):
            raise ValueError('帖子编号或作者归属格式错误')
        # A timeline may contain somebody else's post. Never relabel that author
        # as the queried target or use the target's followers for that post.
        if handle not in targets or handle != queried:
            continue
        account = accounts[queried]
        tweet = {
            'tweet_id': tid, 'author': handle, 'user_id': row.get('author_user_id'),
            'queried_handle': queried, 'referenced_tweet_id': row.get('referenced_tweet_id'),
            'type_evidence': row.get('type_evidence'),
            'followers': number(account.get('followers')),
            'published_at': iso(row['published_at']) if row.get('published_at') else None,
            'fetched_at': iso(row['fetched_at']), 'type': row.get('kind'),
            'is_stale': row.get('is_stale') is True or not account['ok'],
            'text': str(row.get('text') or '')[:4000],
            'url': f'https://x.com/{handle}/status/{tid}',
            **{key: number(row.get(key)) for key in ('views', 'likes', 'retweets', 'replies')},
        }
        if tid in normalized and normalized[tid] != tweet:
            raise ValueError('同一帖子存在冲突采样')
        normalized[tid] = tweet
    return {'run_id': meta['run_id'], 'started_at': iso(begin), 'ended_at': iso(end),
            'coverage': success / len(targets), 'api_calls': meta['api_calls'],
            'tweets': list(normalized.values()),
            'failed_handles': sorted(h for h, a in accounts.items() if not a['ok'])}


def collect_snapshot(root, timeout, excluded_run_ids, started_at):
    """One refresh entry, an absolute process-group deadline, no stale fallback.

    A1's per-invocation summary is accounting only, never a sampling source.
    Failed publication can report its actual calls without changing the baseline.
    """
    root = Path(root)
    before = None
    checked_path(root, root / 'workspace/twitter-radar/snapshots')
    public = checked_path(root, root / 'public/twitter-radar-bundle.json')
    refresh = checked_path(root, root / 'scripts/refresh_twitter_radar.py')
    try:
        before = public.read_bytes()
    except OSError:
        pass
    with tempfile.TemporaryDirectory(prefix='radar-monitor-collect-') as folder:
        summary_path = Path(folder) / 'summary.json'
        process = subprocess.Popen([sys.executable, str(refresh),
                                    '--summary-json', str(summary_path)],
                                   cwd=root, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                   start_new_session=True)
        try:
            code = process.wait(timeout=timeout)
        except BaseException as exc:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()
            if isinstance(exc, subprocess.TimeoutExpired):
                raise ValueError('采集超时，本轮失败') from None
            raise
        summary = json.loads(checked_path(folder, summary_path).read_text()) if summary_path.exists() else None
        published = code == 0 and (summary is None or summary.get('published') is True)
        if not published:
            if (isinstance(summary, dict) and isinstance(summary.get('run_id'), str)
                    and summary['run_id'] not in excluded_run_ids
                    and stamp(summary['started_at']) >= stamp(started_at)
                    and type(summary.get('api_calls')) is int and summary['api_calls'] >= 0
                    and number(summary.get('coverage')) is not None and summary['coverage'] <= 1):
                return {'run_id': summary['run_id'], 'api_calls': summary['api_calls'],
                        'coverage': summary['coverage'], 'tweets': [], 'failed_handles': [],
                        'collector_failed': True}
            raise ValueError('A1 本轮未发布')
        if public.read_bytes() == before:
            raise ValueError('A1 未提交新公开 bundle')
        snapshot = read_snapshot(root, excluded_run_ids, started_at)
        if summary is not None and summary.get('run_id') != snapshot['run_id']:
            raise ValueError('A1 发布轮次与调用轮次不一致')
        snapshot['collector_failed'] = False
        return snapshot


def radar_filter(root):
    module = load_module(checked_path(root, Path(root) / 'scripts/radar_common.py'))
    if not callable(getattr(module, 'content_filter', None)):
        raise ValueError('雷达过滤接口不可用')
    def accepts(tweet):
        # The kind/type has already been resolved by A1; do not classify it again.
        return (tweet.get('type') in ('original', 'quote')
                and module.content_filter(tweet['text'], is_target_vip=True)[0])
    return accepts


def persist_clue(root, event):
    for name in ('clues.db', 'clues.db-wal', 'clues.db-shm', 'clues.db-journal'):
        checked_path(root, Path(root) / 'workspace/clues' / name)
    """Reuse Clues._save, the transaction used by Clues.ingest_aisa.

    A distinct stable producer key keeps one immutable monitor evidence per tweet,
    including upgrades/retries, without overwriting AIsa/manual threads.
    """
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    from clues_store import Clues, digest
    tweet = event['tweet']
    summary = (f"爆款监控 · @{tweet['author']} · {event['type']}\n"
               f"粉丝 {tweet['followers']}；发布时间 {tweet['published_at']}；"
               f"浏览 {tweet['views']}；赞 {tweet['likes']}；"
               f"本小时增量 {tweet.get('hourly_delta')}；{tweet['url']}\n{tweet['text']}")[:4000]
    snapshot = {'kind': 'aisa', 'source': '爆款监控',
                'title': f"爆款监控 · @{tweet['author']}", 'excerpt': tweet['text'][:4000],
                'hash': digest(tweet), 'start': 0, 'end': len(tweet['text'][:4000]),
                'captured_at': event['created_at'], 'origin_url': tweet['url'],
                'provenance': {**tweet, 'platform': 'twitter', 'original_id': tweet['tweet_id'],
                               'rule': event['type'], 'event_id': event['event_id']}}
    payload = {'title': f"【爆款监控】@{tweet['author']}：{tweet['text']}"[:200],
               'summary': summary, 'categories': ['topics'], 'priority': 'important',
               'reason': '爆款监控自动发现'}
    return Clues(SimpleNamespace(workspace=Path(root) / 'workspace'))._save(
        scrub(payload), [scrub(snapshot)], 'radar-monitor:twitter:' + tweet['tweet_id'])


class Monitor:
    def __init__(self, root=ROOT, *, collector=None, sender=None, clue_writer=None, ai_filter=None):
        self.root = Path(root).resolve()
        self.directory = self.root / 'workspace/twitter-radar/monitor'
        self.config = json.loads(checked_path(self.root, self.root / 'scripts/radar_monitor_config.json').read_text())
        self.zone = ZoneInfo(self.config['timezone'])
        self.collector = collector or collect_snapshot
        self.sender = sender
        self.clue_writer = clue_writer or (lambda event: persist_clue(self.root, event))
        self.ai_filter = ai_filter
        self.page_export_allowed = False
        self._last_safe_state = None

    def check_storage(self):
        checked_path(self.root, self.directory)
        for name in ('state.json', 'page-hourly.json', 'feedback.json', 'monitor.log',
                     'monitor.lock', 'outbox.jsonl', 'shadow.jsonl'):
            checked_path(self.root, self.directory / name)
        if self.directory.exists():
            for path in self.directory.glob('daily-*.json'):
                checked_path(self.root, path)
        for name in ('clues.db', 'clues.db-wal', 'clues.db-shm', 'clues.db-journal'):
            checked_path(self.root, self.root / 'workspace/clues' / name)

    def write(self, path, data):
        atomic_write(path, data, root=self.root)

    def fresh_state(self):
        return {'version': 1, 'initialized': False, 'samples': {}, 'pushed': {},
                'events': [], 'runs': {}, 'days': {}, 'shadows': [],
                'consecutive_failures': 0, 'fault_active': False, 'fault_id': None}

    def load(self):
        try:
            self.check_storage()
        except PathBoundaryError:
            # Never inspect redirected storage. A live instance may expose its
            # last trusted in-memory state for diagnostics, but cannot persist it.
            return copy.deepcopy(self._last_safe_state or self.fresh_state()), True
        path = checked_path(self.root, self.directory / 'state.json')
        try:
            state = json.loads(path.read_text())
            if (state.get('version') != 1 or type(state.get('initialized')) is not bool
                    or not all(isinstance(state.get(k), dict) for k in ('samples', 'pushed', 'runs', 'days'))
                    or not all(isinstance(state.get(k), list) for k in ('events', 'shadows'))
                    or type(state.get('consecutive_failures')) is not int
                    or type(state.get('fault_active')) is not bool):
                raise ValueError('状态结构损坏')
            if state['fault_active'] and not isinstance(state.get('fault_id'), str):
                raise ValueError('故障状态损坏')
            for tid, row in state['samples'].items():
                if (not isinstance(row, dict) or row.get('tweet_id') != tid
                        or not isinstance(row.get('author'), str)
                        or type(row.get('is_stale')) is not bool
                        or 'views' not in row
                        or (row['views'] is not None and number(row['views']) is None)):
                    raise ValueError('采样状态损坏')
                stamp(row['fetched_at'])
            for row in state['pushed'].values():
                if (not isinstance(row, dict) or row.get('type') not in TWEET_EVENTS
                        or type(row.get('upgraded')) is not bool or number(row.get('views')) is None):
                    raise ValueError('推送状态损坏')
                stamp(row['time'])
            for event in state['events']:
                if (not isinstance(event, dict) or not event.get('event_id')
                        or not isinstance(event.get('type'), str)
                        or event.get('status') not in ('pending', 'sent')):
                    raise ValueError('事件状态损坏')
                stamp(event['created_at'])
                if event['type'] in TWEET_EVENTS:
                    row = event['tweet']
                    if not isinstance(row, dict) or not row.get('tweet_id') or number(row.get('views')) is None:
                        raise ValueError('帖子事件损坏')
            for row in state['days'].values():
                if (not isinstance(row, dict) or type(row.get('rounds')) is not int
                        or type(row.get('api_calls')) is not int
                        or type(row.get('unknown_call_rounds')) is not int
                        or not isinstance(row.get('coverage'), list)
                        or not isinstance(row.get('viral_counts'), dict)
                        or not isinstance(row.get('shadow_hits'), dict)):
                    raise ValueError('统计状态损坏')
            if not all(isinstance(row, dict) for row in state['runs'].values()):
                raise ValueError('轮次状态损坏')
            if 'page_hourly' in state:
                validate_page_hourly(state['page_hourly'])
            self._last_safe_state = copy.deepcopy(state)
            return state, False
        except FileNotFoundError:
            return self.fresh_state(), False
        except (OSError, ValueError, TypeError, KeyError, AttributeError):
            # Preserve the evidence before rebuilding, without copying untrusted data to logs.
            state = self.fresh_state()
            try:
                rows = [json.loads(line) for line in checked_path(self.root, self.directory / 'outbox.jsonl').read_text().splitlines() if line]
                state['events'] = [e for e in rows if isinstance(e, dict) and e.get('event_id')
                                   and e.get('status') in ('pending', 'sent')
                                   and isinstance(e.get('type'), str) and e.get('created_at')
                                   and (e['type'] not in TWEET_EVENTS or
                                        isinstance(e.get('tweet'), dict) and e['tweet'].get('tweet_id')
                                        and number(e['tweet'].get('views')) is not None)]
                self.reconcile(state)
            except (OSError, ValueError, TypeError, AttributeError, KeyError):
                pass
            return state, True

    def reconcile(self, state):
        for event in state['events']:
            if event['status'] != 'sent' or event['type'] not in TWEET_EVENTS:
                continue
            tid = event['tweet']['tweet_id']
            if event['type'] == 'upgrade_100k':
                state['pushed'].setdefault(tid, {})['upgraded'] = True
            else:
                state['pushed'].setdefault(tid, {}).update(
                    type=event['type'], time=event.get('sent_at', event['created_at']),
                    views=event['tweet']['views'],
                    upgraded=event['tweet']['views'] >= self.config['upgrade_views'])

    def commit(self, state):
        self.check_storage()
        for day in state['days']:
            checked_path(self.root, self.directory / f'daily-{day}.json')
        # Commit event creation AND sampling/stats in one rename BEFORE external effects.
        self.write(self.directory / 'state.json', dump(state))
        self._last_safe_state = copy.deepcopy(state)
        self.project(state)

    def project(self, state):
        self.check_storage()
        for day in state['days']:
            checked_path(self.root, self.directory / f'daily-{day}.json')
        self.write(self.directory / 'outbox.jsonl', ''.join(dump(e) + '\n' for e in state['events']))
        self.write(self.directory / 'shadow.jsonl', ''.join(dump(e) + '\n' for e in state['shadows']))
        for day, data in state['days'].items():
            self.write(self.directory / f'daily-{day}.json', dump(data))
        page = state.get('page_hourly')
        if page is not None and self.page_export_allowed:
            target = checked_path(self.root, self.directory / 'page-hourly.json')
            payload = dump(page)
            # Unsuccessful rounds must leave the last successful export untouched.
            # A committed export interrupted by a crash is repairable without collection.
            try:
                unchanged = target.read_text(encoding='utf-8') == payload
            except OSError:
                unchanged = False
            if not unchanged:
                self.write(target, payload)

    def read_feedback(self, now):
        path = checked_path(self.root, self.directory / 'feedback.json')
        # One read per active invocation, never repair or rewrite a feedback file.
        try:
            records = json.loads(path.read_text(encoding='utf-8'))
            if not isinstance(records, dict):
                raise ValueError('反馈结构错误')
        except (OSError, ValueError, UnicodeError):
            self.log('反馈不可用', now)
            return None
        return records

    def feedback_summary(self, state, now):
        records = self.feedback_records
        today = now.astimezone(self.zone).date()
        # Only a confirmed first delivery enters the cohort; upgrades do not
        # introduce another post or move its original sent date.
        sent = {}
        for event in state['events']:
            if (event['type'] not in TWEET_EVENTS or event['type'] == 'upgrade_100k'
                    or event['status'] != 'sent'):
                continue
            try:
                moment = stamp(event['sent_at'])
            except (KeyError, TypeError, ValueError):
                continue
            tid = event['tweet']['tweet_id']
            if tid not in sent or moment < sent[tid]:
                sent[tid] = moment
        def cohort(start):
            tids = [tid for tid, moment in sent.items()
                    if start <= moment.astimezone(self.zone).date() <= today and moment <= now]
            counts = {'pushed': len(tids), 'worth': 0, 'not_worth': 0, 'unreviewed': 0}
            for tid in tids:
                row = records.get(tid) if records is not None else None
                verdict = row.get('verdict') if isinstance(row, dict) else None
                counts[verdict if verdict in ('worth', 'not_worth') else 'unreviewed'] += 1
            reviewed = counts['worth'] + counts['not_worth']
            counts['useful_rate'] = counts['worth'] / reviewed if reviewed else None
            return counts
        return {'status': 'ok' if records is not None else 'unavailable',
                'message': None if records is not None else '反馈不可用',
                'today': cohort(today), 'recent_7_days': cohort(today - timedelta(days=6))}

    def refresh_feedback(self, state, now):
        daykey = now.astimezone(self.zone).date().isoformat()
        if daykey not in state['days']:
            return
        state['days'][daykey]['feedback'] = self.feedback_summary(state, now)
        for event in state['events']:
            if event['type'] == 'daily_summary' and event['status'] == 'pending' and event['date'] == daykey:
                event['summary']['feedback'] = copy.deepcopy(state['days'][daykey]['feedback'])

    def refresh_page_status(self, state, now):
        page = state.get('page_hourly')
        slot = now.astimezone(self.zone).strftime('%Y-%m-%dT%H')
        run = state['runs'].get(slot, {})
        if not page or not run.get('success') or page['run_id'] != run.get('run_id'):
            return
        latest = {}
        initial_sent = set()
        for event in state['events']:
            if event['type'] in TWEET_EVENTS:
                tid = event['tweet']['tweet_id']
                # State events are append-ordered; later upgrade replaces initial hit.
                latest[tid] = event['type']
                if event['type'] != 'upgrade_100k' and event['status'] == 'sent':
                    initial_sent.add(tid)
        for tid, row in page['tweets'].items():
            row.update(hit=latest.get(tid), pushed=tid in initial_sent)


    def log(self, message, now):
        # No exception repr, collector output, environment or raw request data.
        path = checked_path(self.root, self.directory / 'monitor.log')
        with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600),
                       'a', encoding='utf-8') as stream:
            stream.write(dump({'time': iso(now), 'message': message}) + '\n')

    def event(self, state, kind, key, now, **payload):
        event_id = 'radar:tweet:' + key if kind in TWEET_EVENTS else 'radar:' + kind + ':' + key
        if any(e['event_id'] == event_id for e in state['events']):
            return False
        state['events'].append({'event_id': event_id, 'type': kind, 'created_at': iso(now),
                                'status': 'pending', **scrub(payload)})
        return True

    def flush(self, state, now):
        # Clue failure must not lose the event; next run retries before sending.
        for event in state['events']:
            if event['type'] in TWEET_EVENTS and not event.get('clue_id'):
                try:
                    saved = self.clue_writer(copy.deepcopy(event))
                    event['clue_id'] = saved['id']
                    self.commit(state)
                except Exception:
                    self.log('线索写入失败，保留待重试', now)
        try:
            sender = self.sender
            if sender is None:
                path = checked_path(self.root, self.root / 'scripts/radar_feishu.py')
                if not path.exists():
                    return
                sender = load_module(path).send_events
            # Confirm this round's tweet deliveries before building the summary
            # sent cohort, including posts first delivered at 23:00.
            for summary_phase in (False, True):
                self.refresh_feedback(state, now)
                self.refresh_page_status(state, now)
                self.commit(state)
                pending = [e for e in state['events'] if e['status'] == 'pending'
                           and (e['type'] == 'daily_summary') == summary_phase
                           and (e['type'] not in TWEET_EVENTS or e.get('clue_id'))]
                if not pending:
                    continue
                results = sender(copy.deepcopy(pending))
                if not isinstance(results, list):
                    raise ValueError('发送回执必须为列表')
                sent = {r['event_id'] for r in results if isinstance(r, dict) and r.get('status') == 'sent'}
                for event in pending:
                    if event['event_id'] in sent:
                        event.update(status='sent', sent_at=iso(now))
                self.reconcile(state)
                self.refresh_feedback(state, now)
                self.refresh_page_status(state, now)
                self.commit(state)
        except Exception:
            self.log('发送或回执保存失败，使用原事件编号重试', now)

    def eligible(self, tweet, now, accepts):
        if tweet['is_stale'] or tweet['type'] not in ('original', 'quote') or tweet['published_at'] is None:
            return None
        age = (now - stamp(tweet['published_at'])).total_seconds() / 3600
        if age < 0 or age > self.config['max_age_hours'] or tweet['views'] is None or not accepts(tweet):
            return None
        return age

    def evaluate(self, state, snapshot, now, baseline, day, accepts):
        page = {'schema': 1, 'run_id': snapshot['run_id'],
                'generated_at': iso(now).replace('+00:00', 'Z'), 'tweets': {}}
        rejected_ids = {}
        for tweet in snapshot['tweets']:
            tweet = {**tweet, **{key: number(tweet.get(key))
                                for key in ('views', 'likes', 'retweets', 'replies', 'followers')}}
            tid = tweet['tweet_id']
            if not isinstance(tid, str) or TWEET_ID_RE.fullmatch(tid) is None:
                reason = ('类型非字符串' if not isinstance(tid, str) else
                          '长度不在1至25位' if not 1 <= len(tid) <= 25 else
                          '含非 ASCII 数字字符')
                rejected_ids[reason] = rejected_ids.get(reason, 0) + 1
                continue
            previous = state['samples'].get(tid)
            age = self.eligible(tweet, now, accepts)
            hourly = None
            views_delta = None
            elapsed = None
            if (previous and not previous['is_stale'] and not tweet['is_stale']
                    and previous['views'] is not None and tweet['views'] is not None):
                elapsed = (stamp(tweet['fetched_at']) - stamp(previous['fetched_at'])).total_seconds() / 3600
                if elapsed > 0 and tweet['views'] >= previous['views']:
                    views_delta = tweet['views'] - previous['views']
                    hourly = number(views_delta / elapsed)
            # Page scope includes unknown views and stale samples; these may
            # still be displayed but never acquire invented increments.
            published = tweet.get('published_at')
            scope_age = (now - stamp(published)).total_seconds() / 3600 if published else None
            if (tweet['type'] in ('original', 'quote') and scope_age is not None
                    and 0 <= scope_age <= self.config['max_age_hours'] and accepts(tweet)):
                valid_delta = (not baseline and tweet['author'] not in snapshot.get('failed_handles', [])
                               and views_delta is not None and elapsed is not None and elapsed > 0)
                # Each numeric field degrades independently: an excessive rate
                # must not discard an otherwise valid increment and interval.
                page['tweets'][tid] = {
                    'hourly_delta': number(hourly) if valid_delta else None,
                    'views_delta': number(views_delta) if valid_delta else None,
                    'interval_hours': number(elapsed) if valid_delta else None,
                    'views': number(tweet['views']),
                    'fetched_at': iso(tweet['fetched_at']).replace('+00:00', 'Z'),
                    'is_stale': tweet['is_stale'] is True or tweet['author'] in snapshot.get('failed_handles', []),
                    'hit': None, 'pushed': False}
            # Unknown/stale values still establish that the ID has already been seen.
            state['samples'][tid] = {**tweet}
            falling = (previous is not None and number(previous.get('views')) is not None
                       and tweet['views'] is not None and tweet['views'] < previous['views'])
            if age is None or baseline or falling:
                continue
            lf = self.config['lowfan']
            nf = self.config['new_fast']
            lowfan = (tweet['followers'] is not None and tweet['followers'] <= lf['followers_max']
                      and tweet['views'] >= lf['views_min'] and tweet['likes'] is not None
                      and tweet['likes'] >= lf['likes_min'])
            new = previous is None
            shadows = []
            if new and age >= nf['age_min_hours'] and tweet['views'] >= nf['views_min']:
                shadows += [f'new_rate_{rate}' for rate in self.config['shadow']['new_rates']
                            if tweet['views'] / age >= rate]
            if lowfan:
                shadows += [f'lowfan_hours_{hours}' for hours in self.config['shadow']['lowfan_hours'] if age <= hours]
            if shadows:
                state['shadows'].append({'run_id': snapshot['run_id'], 'tweet_id': tid,
                                         'created_at': iso(now), 'candidates': shadows})
                for candidate in shadows:
                    day['shadow_hits'][candidate] = day['shadow_hits'].get(candidate, 0) + 1
            kind = None
            if tid in state['pushed']:
                if not state['pushed'][tid].get('upgraded') and tweet['views'] >= self.config['upgrade_views']:
                    kind = 'upgrade_100k'
            elif not any(e.get('tweet', {}).get('tweet_id') == tid and e['type'] in TWEET_EVENTS for e in state['events']):
                if lowfan and age <= lf['age_max_hours']:
                    kind = 'lowfan_burst'
                elif new and age >= nf['age_min_hours'] and tweet['views'] >= nf['views_min'] and tweet['views'] / age >= nf['views_per_hour']:
                    kind = 'new_fast'
                elif hourly is not None and hourly >= self.config['hourly_delta']['views_per_hour']:
                    kind = 'hourly_delta'
            if kind:
                key = tid + (':upgrade' if kind == 'upgrade_100k' else ':initial')
                # Initial rules use a common phase even if a later retry meets another rule.
                if self.event(state, kind, key, now, tweet={**tweet, 'hourly_delta': hourly, 'views_delta': views_delta,
                                                            'interval_hours': elapsed}, run_id=snapshot['run_id']):
                    day['viral_counts'][kind] = day['viral_counts'].get(kind, 0) + 1
        if rejected_ids:
            self.log('忽略非法帖子编号：拒绝数=' + str(sum(rejected_ids.values()))
                     + '；原因=' + dump(rejected_ids), now)
        state['page_hourly'] = page
        self.refresh_page_status(state, now)
        for tid, sample in state['samples'].items():
            if sample['author'] in snapshot.get('failed_handles', []):
                sample['is_stale'] = True

    def run_once(self, now=None):
        try:
            self.check_storage()
            return self._run_once(now)
        except PathBoundaryError:
            self.page_export_allowed = False
            return {'status': 'failed', 'run_id': None, 'coverage': 0, 'events': 0}

    def _run_once(self, now=None):
        now = stamp(now or datetime.now(timezone.utc))
        local = now.astimezone(self.zone)
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        path = checked_path(self.root, self.directory / 'monitor.lock')
        with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600), 'a') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return {'status': 'locked'}
            if not self.config['start_hour'] <= local.hour <= self.config['end_hour']:
                self.log('非运行时段，跳过', now)
                return {'status': 'outside_hours'}
            self.feedback_records = self.read_feedback(now)
            state, damaged = self.load()
            self.reconcile(state)
            slot = local.strftime('%Y-%m-%dT%H')
            # Only a successful slot may repair a page export. A later failed
            # collection must preserve even the pre-crash page bytes.
            self.page_export_allowed = state['runs'].get(slot, {}).get('success') is True
            self.project(state)
            daykey = local.date().isoformat()
            day = state['days'].setdefault(daykey, {'rounds': 0, 'api_calls': 0,
                    'coverage': [], 'viral_counts': {}, 'shadow_hits': {}, 'unknown_call_rounds': 0})
            if slot in state['runs']:
                self.refresh_feedback(state, now)
                self.commit(state)
                if local.hour != self.config['baseline_hour'] and not damaged:
                    self.flush(state, now)
                return {'status': 'already_run'}
            snapshot = None
            try:
                snapshot = self.collector(self.root, self.config['collector_timeout_seconds'],
                                          {r['run_id'] for r in state['runs'].values() if r.get('run_id')}, now)
                # Injected collectors use the same normalized schema as read_snapshot.
                coverage = snapshot['coverage']
                if number(coverage) is None or coverage > 1:
                    raise ValueError('覆盖率错误')
                if type(snapshot['api_calls']) is not int or snapshot['api_calls'] < 0:
                    raise ValueError('调用数错误')
                good = coverage >= self.config['coverage_min'] and not snapshot.get('collector_failed', False)
            except Exception:
                good = False
                snapshot = None
                self.log('采集失败或快照不可用', now)
            accepts = None
            if good:
                try:
                    accepts = self.ai_filter or radar_filter(self.root)
                except Exception:
                    good = False
                    self.log('雷达过滤接口不可用，保留采样基准', now)
            day['rounds'] += 1
            day['api_calls'] += snapshot['api_calls'] if snapshot else 0
            day['unknown_call_rounds'] += int(snapshot is None)
            day['coverage'].append(snapshot['coverage'] if snapshot else 0)
            state['runs'][slot] = {'run_id': snapshot['run_id'] if snapshot else None, 'success': good}
            silent_round = damaged or local.hour == self.config['baseline_hour']
            baseline = silent_round or not state['initialized']
            if damaged:
                self.log('状态损坏，本轮只重建基准', now)
            if good:
                self.page_export_allowed = True
                state['consecutive_failures'] = 0
                if state['fault_active'] and not silent_round:
                    self.event(state, 'monitor_recovery', state['fault_id'], now, coverage=snapshot['coverage'])
                    state['fault_active'] = False
                self.evaluate(state, snapshot, now, baseline, day, accepts)
                state['initialized'] = True
            else:
                state['consecutive_failures'] += 1
                if state['consecutive_failures'] >= self.config['fault_after_rounds'] and not state['fault_active'] and not silent_round:
                    state['fault_id'] = slot
                    self.event(state, 'monitor_fault', slot, now, failures=state['consecutive_failures'],
                               coverage=snapshot['coverage'] if snapshot else 0)
                    state['fault_active'] = True
            self.refresh_feedback(state, now)
            if local.hour == self.config['end_hour'] and not silent_round:
                self.event(state, 'daily_summary', daykey, now, date=daykey, summary=copy.deepcopy(day))
            self.commit(state)
            # 06:00 and corrupt-state recovery promise zero external effects/events.
            if not silent_round:
                self.flush(state, now)
            return {'status': 'baseline' if good and baseline else 'success' if good else 'failed',
                    'run_id': snapshot['run_id'] if snapshot else None,
                    'coverage': snapshot['coverage'] if snapshot else 0,
                    'events': len(state['events'])}


def run_once(now=None):
    return Monitor().run_once(now)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='爆款监控，每次执行一轮')
    parser.add_argument('--root', type=Path, default=ROOT)
    args = parser.parse_args()
    try:
        print(dump(Monitor(args.root).run_once()))
    except Exception:
        print('监控执行失败，请查看本地监控日志', file=sys.stderr)
        sys.exit(1)
