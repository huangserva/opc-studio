"""只读 Grok 雷达轮次；复用旧雷达数学，不采集、不投递、不回写报告。"""
from __future__ import annotations

import copy
import hashlib
import json
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path

import grok_bot_radar as G
from scripts.radar_monitor import Monitor, TWEET_EVENTS, number, stamp
from scripts.radar_common import content_filter, classify_track, HANDLE_RE, in_window, low_fan_eligible
from scripts.radar_bundle import VIP_VIEWS, VIP_LIKES, LOWFAN_VIEWS, LOWFAN_LIKES, LOWFAN_LIMIT, WINDOW_48H, _meets

SCHEMA = 'grok-radar-draft-2'
CHANNEL = 'grok:radar'
# 只接纳有日期和时段前缀的轮次，state/targets/manifest/shard/临时文件不是报告。
ROUND_NAME = re.compile(r'^(?:[0-2][0-9]-)?\d{4}-\d{2}-\d{2}[-T_]\d{2}(?:[-:]?\d{2})?(?:[-:]?\d{2})?(?:Z|[+]\d{2}:?\d{2})?-.+\.json$')
AUX_NAME = re.compile(r'(?:^|[-_])(?:state|targets-copy|manifest|shard|ledger|usage|knowledge_results)(?:[-_.]|$)', re.I)
METRICS = ('views', 'likes', 'retweets', 'replies', 'followers')
ROOT = Path(__file__).resolve().parent


def round_paths(reports=None):
    folder = Path(reports or G.reports_dir()) / 'radar'
    if folder.is_symlink():
        raise ValueError('雷达目录不能为符号链接')
    return sorted(p for p in folder.glob('*.json') if ROUND_NAME.fullmatch(p.name) and not AUX_NAME.search(p.name))


def _handle(value):
    if not isinstance(value, str) or not HANDLE_RE.fullmatch(value.lstrip('@')):
        raise ValueError('账号格式异常')
    return value.lstrip('@').casefold()


def read_round(path):
    from interest_sources import read_json
    data = read_json(path)
    if (not isinstance(data, dict) or data.get('schema_version') != SCHEMA
            or not isinstance(data.get('run_id'), str) or not data['run_id'].strip()
            or data.get('status') not in ('ok', 'good', 'completed', 'success', 'incomplete', 'partial', 'blocked', 'failed')
            or type(data.get('baseline')) is not bool
            or type(data.get('all_groups_complete')) is not bool
            or not isinstance(data.get('rule_version'), str) or not data['rule_version']
            or not all(isinstance(data.get(k), list) for k in ('accounts', 'observations', 'events'))):
        raise ValueError('轮次 schema 不符')
    started, finished = (stamp(data.get(k)) for k in ('round_started_at', 'finished_at'))
    raw_slot = data.get('scheduled_slot')
    slot = (started.astimezone(G.BEIJING).replace(hour=int(raw_slot), minute=0, second=0, microsecond=0)
            if isinstance(raw_slot,str) and re.fullmatch(r'(?:[01][0-9]|2[0-3])',raw_slot) else stamp(raw_slot))
    if finished < started:
        raise ValueError('完成时间早于开始时间')
    roster_path = ROOT / 'scripts/radar_targets.json'
    roster_raw = roster_path.read_bytes()
    roster = json.loads(roster_raw)
    handles = {_handle(r['handle']) for r in roster['targets']}
    if (not handles or len(handles) != len(roster['targets']) or roster['expected_count'] != len(handles)
            or data.get('target_count') != len(handles)
            or data.get('target_list_sha') != hashlib.sha256(roster_raw).hexdigest()):
        raise ValueError('名单版本或人数不符')
    accounts = {}; truncation_unknown = []
    for account in data['accounts']:
        if not isinstance(account, dict):
            raise ValueError('账号回执异常')
        handle = _handle(account.get('handle'))
        if handle not in handles or handle in accounts or account.get('status') not in ('ok', 'empty', 'error', 'incomplete', 'not_run', 'not_due') or not (type(account.get('truncated')) is bool or type(account.get('truncated')) is int and account['truncated'] >= 0):
            raise ValueError('账号回执异常')
        accounts[handle] = account
        if type(account['truncated']) is not bool:truncation_unknown.append(handle)
    complete = (data['status'] in ('ok', 'good', 'completed', 'success') and data['all_groups_complete']
                and set(accounts) == handles
                and all(a['status'] in ('ok', 'empty') and a['truncated'] is not True for a in accounts.values()))
    if complete and truncation_unknown:complete = None
    # 未批准轮转，不凭 not_due 缩小分母。
    if data.get('schedule_mode', 'full') != 'full':
        complete = False
    rows, seen = [], set()
    required = ('tweet_id', 'author_handle', 'author_id', 'queried_handle', 'type', 'type_evidence', 'published_at', 'fetched_at', 'is_stale', 'full_text', 'article', 'quoted', 'url', *METRICS, 'missing_reasons', 'evidence_ref')
    for raw in data['observations']:
        if not isinstance(raw, dict) or any(k not in raw for k in required):
            raise ValueError('观察字段缺失')
        tid = raw['tweet_id']
        if not isinstance(tid, str) or G.ID_RE.fullmatch(tid) is None or tid in seen:
            raise ValueError('观察编号异常或重复')
        seen.add(tid)
        author, queried = _handle(raw['author_handle']), _handle(raw['queried_handle'])
        if (raw['type'] not in ('original', 'quote', 'reply', 'retweet', 'unknown')
                or type(raw['is_stale']) is not bool or not isinstance(raw['full_text'], str)
                or not isinstance(raw['missing_reasons'], list)):
            raise ValueError('观察结构异常')
        fetched = stamp(raw['fetched_at'])
        if fetched > finished + timedelta(seconds=1):
            raise ValueError('观察时间晚于完成时间')
        published = stamp(raw['published_at']) if raw['published_at'] is not None else None
        if any(raw.get(k) is not None and number(raw[k]) is None for k in (*METRICS,'bookmarks','quote_count')):
            raise ValueError('指标必须为精确安全数字或 null')
        if not G.safe_url(raw['url']) or G.URL_RE.fullmatch(raw['url']) is None or G.URL_RE.fullmatch(raw['url'])[1] != tid:
            raise ValueError('观察 URL 与编号不符')
        row = copy.deepcopy(raw)
        row.update(author_handle=author, queried_handle=queried, fetched_at=fetched.isoformat(), published_at=published.isoformat() if published else None,
                   author=author, text=raw['full_text'], content_complete=bool(raw['full_text']),
                   eligible_author=author in handles and author == queried,
                   is_stale=raw['is_stale'] or accounts.get(queried, {}).get('status') not in ('ok', 'empty'))
        rows.append(row)
    event_fields = ('event_id','tweet_id','event_type','rule_version','current_observation_ref','previous_observation_ref','age_hours','views_delta','interval_hours','hourly_delta','initial_event_id','ingested_at','delivery_status')
    for event in data['events']:
        if (not isinstance(event, dict) or any(k not in event for k in event_fields) or not isinstance(event.get('event_id'), str)
                or event.get('rule_version') != data['rule_version']
                or any(event[k] is not None and number(event[k]) is None for k in ('age_hours','views_delta','interval_hours','hourly_delta'))
                or event.get('event_type') not in TWEET_EVENTS or event.get('tweet_id') not in seen):
            raise ValueError('事件结构异常')
    return dict(data, observations=rows, complete=complete, truncation_unknown=truncation_unknown, started=started, finished=finished, slot=slot.astimezone(timezone.utc).isoformat())


class _Replay(Monitor):
    """只用 evaluate 的纯数学路径；禁用日志、页面/线索/投递副作用。"""
    def __init__(self):
        self.config = json.loads((ROOT / 'scripts/radar_monitor_config.json').read_text())

    def refresh_page_status(self, state, now):
        pass

    def log(self, message, now):
        pass


def load(root, now=None, reports=None):
    from interest_sources import workspace, read_json
    now = now or datetime.now(timezone.utc)
    errors, rounds = [], []
    try:
        paths = round_paths(reports)
    except (OSError, ValueError):
        paths = []; errors.append('雷达目录不可读')
    for path in paths:
        try:
            data = read_round(path)
            if data['started'] > now or data['finished'] > now:
                raise ValueError('轮次时间在未来')
            rounds.append((path, data))
        except (OSError, ValueError, TypeError, KeyError, OverflowError, RecursionError):
            errors.append(path.name + '：轮次损坏或字段异常')
    # 同 slot 优先完整成功、成功但截断未知、失败；同档取较新执行。
    # 重复 slot 只回放一次，不因重复文件重判事件。
    selected = {}
    for path, data in sorted(rounds, key=lambda pair: (
            0 if pair[1]['complete'] is True else 1 if pair[1]['complete'] is None else 2,
            -pair[1]['started'].timestamp(), pair[0].name)):
        selected.setdefault(data['slot'], (path, data))
    replay = _Replay(); state = replay.fresh_state(); items = {}; initialized = False
    # 只从本地旧雷达账本读取送达事实，不信任 Grok delivery_status='sent'。
    old = workspace(root) / 'twitter-radar/monitor/state.json'
    if old.exists():
        try:
            legacy = read_json(old)
            for tid, sample in legacy.get('samples', {}).items():
                if G.ID_RE.fullmatch(tid) and isinstance(sample, dict) and (sample.get('views') is None or number(sample.get('views')) is not None) and stamp(sample['fetched_at']) <= now:
                    state['samples'][tid] = sample
            for event in legacy.get('events', []):
                if event.get('type') in TWEET_EVENTS and event.get('status') in ('pending', 'sent'):
                    state['events'].append(event)
                    if event['status'] == 'sent' and event['type'] != 'upgrade_100k':
                        row = legacy.get('pushed', {}).get(event['tweet']['tweet_id'])
                        if isinstance(row, dict) and type(row.get('upgraded')) is bool:
                            state['pushed'][event['tweet']['tweet_id']] = row
            initialized = legacy.get('initialized') is True
        except (OSError, ValueError, TypeError, KeyError, AttributeError):
            errors.append('AIsa 历史事件账本异常'); state = replay.fresh_state()
    for path, data in sorted(selected.values(), key=lambda pair: (pair[1]['started'], pair[0].name)):
        if data['complete'] is False:
            errors.append(path.name + '：轮次不完整')
        rows = data['observations']
        before = len(state['events'])
        baseline = data['baseline'] or data['complete'] is not True or not initialized or data['started'].astimezone(G.BEIJING).hour == 6
        replay.evaluate(state, {'run_id': data['run_id'], 'tweets': rows}, data['started'], baseline,
                        {'shadow_hits': {}, 'viral_counts': {}},
                        lambda row: row['eligible_author'] and content_filter(row['text'], is_target_vip=True)[0])
        computed = {(e['tweet']['tweet_id'], e['type']) for e in state['events'][before:]}
        for event in data['events']:
            # 已经核验过的幂等事件可重报，但不能凭标签产生新事件。
            known = computed | {(e['tweet']['tweet_id'], e['type']) for e in state['events'] if e.get('tweet')}
            if (event['tweet_id'], event['event_type']) not in known:
                errors.append(path.name + '：事件未通过本地规则核验')
        if data['complete']:
            initialized = True
        for row in rows:
            tid = row['tweet_id']; prior = items.get(tid)
            first = min(row['fetched_at'], prior['first_seen_at'], key=stamp) if prior else row['fetched_at']
            if prior and stamp(prior['fetched_at']) > stamp(row['fetched_at']):
                prior['first_seen_at'] = first; continue
            events = [e for e in state['events'] if e.get('tweet', {}).get('tweet_id') == tid]
            # 失败轮保留观察但不覆盖成功采样指标。
            if prior and row['is_stale'] and not prior.get('is_stale'):
                prior['first_seen_at'] = first; continue
            row = dict(row, first_seen_at=first, first_seen_basis='earliest_radar_observation', events=[e['type'] for e in events],
                       hourly_delta=state['page_hourly']['tweets'].get(tid, {}).get('hourly_delta'),
                       topic=classify_track(row['full_text']) or '', source_file=path.name, round_started_at=data['started'].isoformat())
            items[tid] = row
    # 后续轮未生成新事件也保留同帖已核验的标记。
    for tid, row in items.items():
        row['events'] = sorted({e['type'] for e in state['events'] if e.get('tweet', {}).get('tweet_id') == tid})
    latest = max((data['finished'] for _, data in rounds), default=None)
    unknown_handles = sorted({h for _,data in selected.values() for h in data['truncation_unknown']})
    state_view = dict(updated_at=latest.isoformat() if latest else None, status='error' if errors else 'ok' if rounds else 'waiting',
                      error='；'.join(errors[:20]) if errors else None, round_count=len(selected), bad_files=len(errors),
                      duplicate_slots=len(rounds)-len(selected), note='等待 Grok 产出雷达轮次文件' if not paths else '截断情况未知' if unknown_handles else '',
                      truncation_unknown=unknown_handles, complete_coverage=None if unknown_handles else all(data['complete'] for _,data in selected.values()) if rounds else None)
    return list(items.values()), state_view


def discovery_rows(rows):
    """旧 bundle 门槛与排序原样复用；已核验四类事件可额外入栏。"""
    eligible = [row for row in rows if row['type'] in ('original','quote') and row['eligible_author']
                and row['published_at'] and in_window(stamp(row['published_at']),stamp(row['round_started_at']),WINDOW_48H)
                and content_filter(row['full_text'],is_target_vip=True)[0]]
    # 与 radar_bundle.build_bundle 的两次稳定排序相同，包括 0 的既有排序语义。
    eligible.sort(key=lambda r:str(r['published_at']),reverse=True)
    eligible.sort(key=lambda r:(not r['is_stale'],r['views'] or -1,r['likes'] or -1),reverse=True)
    lowfan = [r for r in eligible if low_fan_eligible(r['followers'])
              and (_meets(r['views'],LOWFAN_VIEWS) or _meets(r['likes'],LOWFAN_LIKES))]
    lowfan.sort(key=lambda r:(not r['is_stale'],r['views'] or -1),reverse=True)
    lowfan_ids = {r['tweet_id'] for r in lowfan[:LOWFAN_LIMIT]}
    return [r for r in eligible if _meets(r['views'],VIP_VIEWS) or _meets(r['likes'],VIP_LIKES)
            or r['tweet_id'] in lowfan_ids or r.get('events')]


def merge_item(prior, current):
    """同 ID 只展示一条；整次采样选新版本，未知指标不从旧轮伪补。"""
    if prior is None:
        return current
    latest = stamp(current['fetched_at']) >= stamp(prior['fetched_at']) if prior.get('fetched_at') else True
    result = dict(prior)
    if latest:
        result['metrics'] = current['metrics']
        result['fetched_at'] = current['fetched_at']
        result['author'] = current['author']
    for key in ('full_text', 'title'):
        result[key] = max((prior.get(key) or '', current.get(key) or ''), key=len)
    result['rich_sources'] = prior.get('rich_sources', []) + current.get('rich_sources', [])
    result['sources'] = list(dict.fromkeys(prior['sources'] + current['sources']))
    result['radar_events'] = current['radar_events']
    if current['tier'] != '其他' and (result.get('tier') == '其他' or current['tier'] == '爆款'):
        result['tier'] = current['tier']
    if not result.get('topic'):
        result['topic'] = current['topic']
    times = [v for v in (current.get('first_seen_at'), prior.get('first_seen_at') or prior.get('fetched_at')) if v]
    result['first_seen_at'] = min(times, key=stamp)
    result['first_seen_basis'] = 'earliest_retained_observation'
    result['content_complete'] = prior.get('content_complete') or current.get('content_complete')
    return result
