"""今日页：只读已有雷达数据；摘要/详情分离，反馈复用各来源写入器。"""
from __future__ import annotations

import fcntl
import json
import math
import os
import re
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

import grok_bot_radar as grok
import video_radar_page_api as video
import linuxdo_source as linuxdo
from clue_morning import snapshot as morning_snapshot

_LOCK = threading.RLock()
ACTIONS = {'accurate', 'inaccurate', 'keep', 'use', 'clear'}
METRICS = ('views', 'likes', 'bookmarks', 'retweets', 'replies', 'views_per_hour', 'hours_since_created')
EXTRA = ('fit_score', 'doable', 'my_angle', 'outline', 'doable_resource', 'incomplete_reason')
HOT_VIEWS = 100000
SUMMARY = ('hot', 'id', 'kind', 'sources', 'author', 'published_at', 'title', 'metrics', 'tier', 'topic', 'fit_score', 'doable', 'content_complete', 'status', 'feedback', 'content_insufficient', 'relevance_note', 'rss_boards', 'reply_change')
TIERS = {'爆款': 0, '起势': 1, '低粉爆款': 2}


def _workspace(root):
    base = Path(root)
    return base / 'workspace' if (base / 'workspace').is_dir() else base


def feedback_path(root):
    return _workspace(root) / 'today' / 'feedback.json'


def _load_feedback(root):
    try:
        data, _ = grok._read_json(feedback_path(root))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError, UnicodeError) as exc:
        raise ValueError('今日反馈文件损坏，已停止写入') from exc
    if not isinstance(data, dict) or any(not isinstance(v, dict) or v.get('feedback') not in (None, 'accurate', 'inaccurate') or v.get('status') not in ('new', 'kept', 'used') for v in data.values()):
        raise ValueError('今日反馈文件损坏，已停止写入')
    return data


@contextmanager
def _feedback_lock(root):
    path = feedback_path(root).parent / '.feedback.lock'
    path.parent.mkdir(parents=True, exist_ok=True)
    with _LOCK, path.open('a') as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def _ts(value):
    moment = grok.stamp(value)
    return moment.timestamp() if moment else 0


def _json_safe(value):
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_json_safe(v) for v in value]
    return value


def _invalid_numbers(row):
    return any(k in row and row[k] is not None and grok.number(row[k]) is None for k in (*METRICS, 'followers', 'fc', 'fit_score', 'doable'))


def _report_extras(posts):
    """Grok 的旧 schema 会丢评分，按聚合层标注的原报告路径补回，不采集。"""
    files, extras, bad = {}, {}, False
    for post in posts:
        candidates = []
        for source in post.get('sources', []):
            name = source.get('file', '') if isinstance(source, dict) else ''
            if not name or Path(name).is_absolute() or '..' in Path(name).parts:
                continue
            if name not in files:
                try:
                    data, _ = grok._read_json(grok.reports_dir() / name)
                    files[name] = data.get('posts', []) + data.get('entries', [])
                except (OSError, ValueError, TypeError):
                    files[name] = []
                    bad = True
            for row in files[name]:
                if isinstance(row, dict) and str(row.get('id', row.get('tweet_id', ''))) == post['id']:
                    candidates.append((_ts(source.get('collected_at')), row))
        if candidates:
            raw = max(candidates, key=lambda pair: pair[0])[1]
            bad = bad or _invalid_numbers(raw)
            extras[post['id']] = {k: raw[k] for k in EXTRA if k in raw}
    return extras, bad


def _tier(row):
    value = row.get('tier') or row.get('label') or ''
    names = {'viral': '爆款', 'rising': '起势', 'lowfan_viral': '低粉爆款'}
    if value in TIERS:
        return value
    if value in names:
        return names[value]
    if 'low_fan_hot' in (row.get('flags') or []):
        return '低粉爆款'
    if row.get('tier_class') in ('tier-100k', 'tier-1m'):
        return '爆款'
    if row.get('tier_class') == 'tier-10k':
        return '起势'
    return '其他'


def _heat(item):
    """同档位热度：每小时浏览 × 高收藏权重；缺速率时从同次采样计算。"""
    metrics = item['metrics']
    rate = grok.number(metrics.get('views_per_hour'))
    views = grok.number(metrics.get('views'))
    if rate is None and views is not None:
        hours = grok.number(metrics.get('hours_since_created'))
        if not hours:
            sampled, published = _ts(item.get('fetched_at')), _ts(item.get('published_at'))
            hours = (sampled - published) / 3600 if sampled > published > 0 else None
        rate = views / hours if hours else views
        metrics['views_per_hour'] = rate
    base = rate if rate is not None else 0
    bookmarks = metrics.get('bookmarks')
    likes = metrics.get('likes')
    high_save = bookmarks is not None and (bookmarks >= 100 or bookmarks > 0 and likes is not None and (likes == 0 or bookmarks >= likes))
    weight = min(3, 1 + bookmarks / 100) if high_save else 1
    metrics['heat'] = base * weight
    return metrics['heat']


def _incomplete(item):
    reasons = []
    if item.get('content_complete') is False:
        reasons.append(item.get('incomplete_reason') or '来源标记内容不完整')
    text = item.get('full_text', '')
    supplements = item.get('rich_sources', [])
    if len(text) < 80 and (item['metrics'].get('bookmarks') or 0) >= 100:
        reasons.append('短正文但高收藏，可能只有摘要')
    if re.search(r'https?://t\.co/\S+\s*$', text):
        reasons.append('正文以 t.co 链接结尾，可能有后续内容')
    analysis = item.get('why', '') + '\n' + item.get('pattern', '')
    for pattern, field, reason in [(r'长文|article|文章', 'article', '提到长文但未提供文章正文'), (r'引用|quote', 'quoted', '提到引用但未提供被引用帖'), (r'串推|thread', 'thread', '提到串推但未提供后续推文')]:
        if re.search(pattern, analysis, re.I) and not any(s.get(field) for s in supplements):
            reasons.append(reason)
    item['incomplete_reasons'] = reasons
    item['content_complete'] = not reasons
    return prepare_content(item)


def prepare_content(item):
    """只展开已携带的引用/卡片，绝不补抓外网。"""
    text = item.get('full_text') or ''
    meaningful = re.sub(r'https?://\S+|\s|[^\w\u4e00-\u9fff]', '', text)
    expanded = []
    def collect(value, depth=0):
        if depth > 6:
            return
        if isinstance(value, str):
            expanded.append(value)
        elif isinstance(value, list):
            for v in value[:100]:
                collect(v, depth + 1)
        elif isinstance(value, dict):
            for k in ('title', 'text', 'full_text', 'body', 'content', 'description', 'article', 'thread', 'quoted', 'card'):
                if k in value:
                    collect(value[k], depth + 1)
    for source in item.get('rich_sources') or []:
        if isinstance(source, dict):
            for k in ('quoted', 'card', 'article', 'thread'):
                collect(source.get(k))
    item['expanded_text'] = '\n'.join(expanded)
    extra = re.sub(r'https?://\S+|\s|[^\w\u4e00-\u9fff]', '', item['expanded_text'])
    item['content_insufficient'] = len(meaningful) < 16 and len(extra) < 16
    if item['content_insufficient']:
        reasons = item.setdefault('incomplete_reasons', [])
        if '内容不足：已有数据无法展开引用或链接卡片' not in reasons:
            reasons.append('内容不足：已有数据无法展开引用或链接卡片')
    item.setdefault('relevance_note', '')
    item.setdefault('rss_boards', [])
    item.setdefault('reply_change', '')
    return item


def apply_gate(items, channel='hot'):
    from interest_channel import topics
    from scripts.radar_common import today_relevance_gate
    from today_filter_config import SOURCE_NAMES
    try:
        interests = topics()['topics']
    except (OSError, ValueError, UnicodeError):
        interests = []
    visible, rejected, counts = [], [], {}
    for item in items:
        prepare_content(item)
        verdict = today_relevance_gate(item, interests, channel)
        item['relevance_note'] = verdict['reason'] if verdict['marked_only'] else ''
        for source in set(SOURCE_NAMES.get(s, s) for s in item.get('sources', [])):
            c = counts.setdefault(source, {'total': 0, 'filtered': 0, 'insufficient': 0})
            c['total'] += 1
            c['filtered'] += int(not verdict['visible'])
            c['insufficient'] += int(item['content_insufficient'])
        if verdict['visible']:
            visible.append(item)
        if not verdict['visible'] or item['content_insufficient']:
            rejected.append({'id': item['id'], 'title': item['title'], 'sources': [SOURCE_NAMES.get(s, s) for s in item['sources']],
                             'url': item.get('url', ''), 'reason': verdict['reason'] if not verdict['visible'] else '内容不足（保留并后排）',
                             'hidden': not verdict['visible']})
    for value in counts.values():
        value['visible'] = value['total'] - value['filtered']
        value['ratio'] = value['filtered'] / value['total'] if value['total'] else 0
    return visible, {'by_source': counts, 'items': rejected, 'total': sum(p['hidden'] for p in rejected)}


def aisa_full_stats(root, cutoff, now):
    """按发布时间窗口回放 51 人原始采样，跨轮 tweet_id 去重；不把派生 bundle 冒充全量。"""
    folder = _workspace(root) / 'twitter-radar' / 'snapshots'
    rows, files = {}, 0
    for day in (now.astimezone(grok.BEIJING).date() - timedelta(days=i) for i in range((now - cutoff).days + 2)):
        for path in sorted((folder / day.isoformat()).glob('*.json')):
            try:
                if path.is_symlink() or path.stat().st_size > 32 * 1024 * 1024:
                    continue
                raw = json.loads(path.read_text(encoding='utf-8'))
                if raw.get('target_count') != 51 or not isinstance(raw.get('tweets'), list):
                    continue
                files += 1
                for row in raw['tweets']:
                    identifier = str(row.get('tweet_id') or '')
                    published = grok.stamp(row.get('published_at'))
                    if identifier and published and cutoff <= published <= now:
                        prior = rows.get(identifier)
                        if not prior or _ts(row.get('fetched_at')) >= _ts(prior.get('fetched_at')):
                            rows[identifier] = row
            except (OSError, ValueError, TypeError):
                continue
    candidates = [dict(id='x:' + tid, sources=['A'], title=grok.text(r.get('text'))[:120], full_text=grok.text(r.get('text')),
                       topic='', url=r.get('url', ''), rich_sources=[]) for tid, r in rows.items()]
    visible, audit = apply_gate(candidates)
    c = audit['by_source'].get('AIsa 推特', {'total': 0, 'visible': 0, 'filtered': 0, 'ratio': 0, 'insufficient': 0})
    return dict(c, snapshot_count=files, available=bool(files), basis='原始51人采样，按发布时间窗口、tweet_id去重'), audit['items']


def accumulate_inaccurate(root, item, previous, action, channel='hot'):
    """累计进入不准状态的次数；重复请求幂等，撤销不抹去历史。调用方持反馈锁。"""
    if action != 'inaccurate' or previous.get('feedback') == 'inaccurate':
        return
    from today_filter_config import SOURCE_NAMES
    path = feedback_path(root).with_name('inaccurate-counts.json')
    try:
        data, _ = grok._read_json(path)
        if not isinstance(data, dict) or not isinstance(data.get('counts'), dict):
            raise ValueError('不准累计文件损坏')
    except FileNotFoundError:
        data = {'schema': 'today-inaccurate/v1', 'counts': {}}
    category = item.get('category') or item.get('topic') or '未分类'
    for source in set(SOURCE_NAMES.get(s, s) for s in item['sources']):
        categories = data['counts'].setdefault(source, {})
        categories[category] = categories.get(category, 0) + 1
    data['updated_at'] = datetime.now(timezone.utc).isoformat()
    grok._atomic_write(path, (json.dumps(data, ensure_ascii=False, indent=2) + '\n').encode())


def _post(row, extras):
    g, a = row.get('grok') or {}, row.get('aisa') or {}
    extra = extras.get(row['id'], {})
    author = {'handle': grok.text(row.get('handle')), 'name': grok.text(row.get('author')), 'followers': grok.number(row.get('followers'))}
    item = dict(id='x:' + row['id'], kind='post', sources=[{'Grok': 'G', 'AIsa': 'A'}.get(s, s) for s in row.get('sources', [])],
                author=author, published_at=row.get('created_at'), full_text=grok.text(row.get('full_text')) or grok.text(row.get('text')),
                title=grok.text(g.get('title_zh')), url=row.get('url'), metrics={k: grok.number(row.get(k)) for k in METRICS},
                tier=_tier(g or a or row), topic=grok.text(row.get('topic')) or grok.text(row.get('track')),
                fit_score=grok.number(extra.get('fit_score', row.get('fit_score'))), doable=grok.number(extra.get('doable', row.get('doable'))),
                content_complete=row.get('content_complete', True), incomplete_reason=grok.text(extra.get('incomplete_reason')),
                why=grok.text(g.get('why')) or grok.text(a.get('viral_reason')), pattern=grok.text(g.get('pattern')),
                my_angle=extra.get('my_angle', a.get('plan', '')), outline=extra.get('outline', ''), doable_resource=extra.get('doable_resource', ''),
                rich_sources=[_json_safe(s) for s in (a, g) if s], fetched_at=row.get('collected_at'), status='new', feedback=None)
    item['title'] = (item['title'] or item['full_text'])[:120]
    return _incomplete(item)


def _video(row):
    platform = 'douyin' if row.get('platform') == 'douyin' else 'xhs'
    item = dict(id=platform + ':' + row['id'], kind='video', sources=['V'], video_key=row['key'],
                author={'name': row['author'], 'handle': '', 'followers': row['author_fans']}, published_at=row['published_at'],
                title=row['title'][:120], full_text=row['title'], url=row['url'],
                metrics=dict(views=row['plays'], likes=row['likes'], bookmarks=row['collects'], replies=row['comments'], retweets=row['shares'],
                             likes_per_hour=row['likes_per_hour'], delta=row['likes_delta_per_hour'], score=row['score']),
                tier=_tier(row), topic=row.get('topic') or '视频', fit_score=None, doable=None, content_complete=False,
                incomplete_reason='视频榜只提供标题和封面，完整内容请播放原帖', why=row['relevance_reason'], pattern='', my_angle='', outline='', doable_resource='',
                rich_sources=[{'url': row['url'], 'kind': 'video', 'media': [{'type': 'video', 'poster': row['cover']}]}],
                fetched_at=row['last_seen_at'], status='new', feedback=None)
    return _incomplete(item)


def snapshot(root, now=None, author_mode=False):
    now = now or datetime.now(timezone.utc)
    states, items = {}, {}
    try:
        radar = grok.radar_view(root, hours=168, now=now)
        posts = grok.load_reports()['posts'] if author_mode else radar['posts']
        extra, bad = _report_extras(posts)
        merged = grok.merge_posts(posts, grok._bundle()) if author_mode else radar['merged_posts']
        latest = (radar.get('latest_report') or {}).get('collected_at')
        states['G'] = dict(updated_at=latest, error=('报告缺失或目录不存在' if not radar['report_count'] else '部分报告损坏或数字异常' if radar['bad_files'] or bad else None))
        try:
            bundle, _ = grok._read_json(grok.BUNDLE_PATH)
            if not isinstance(bundle, dict) or not any(isinstance(bundle.get(k), list) for k in ('tweets', 'vip_tweets')):
                raise ValueError('快照结构异常')
            bad_a = any(_invalid_numbers(p) for k in ('tweets', 'vip_tweets') for p in bundle.get(k, []) if isinstance(p, dict))
            states['A'] = dict(updated_at=bundle.get('updated_at') or bundle.get('collected_at'), error='快照数字异常' if bad_a else None)
        except (OSError, ValueError, TypeError):
            states['A'] = dict(updated_at=None, error='快照缺失或损坏')
        for row in merged:
            item = _post(row, extra)
            items[item['id']] = item
    except Exception as exc:
        states.setdefault('G', dict(updated_at=None, error='读取异常：' + type(exc).__name__))
        states.setdefault('A', dict(updated_at=None, error='读取异常：' + type(exc).__name__))
    view = video.page_view(root, now=now)
    errors = [k + '：' + (s.get('note') or s.get('status') or '数据过期') for k, s in view.get('sources', {}).items() if s.get('status') != 'ok' or s.get('stale')]
    if view['status'] == 'ok':
        try:
            raw = video._read_json(video._radar_dir(root) / 'page.json')
            fields = ('plays', 'likes', 'collects', 'comments', 'shares', 'author_fans', 'likes_per_hour', 'likes_delta_per_hour', 'score')
            if any(any(k in row and row[k] is not None and grok.number(row[k]) is None for k in fields) for rows in raw.get('lists', {}).values() for row in rows):
                errors.append('快照数字异常')
        except (OSError, ValueError, TypeError):
            errors.append('快照读取异常')
    states['V'] = dict(updated_at=view.get('generated_at'), error=view.get('error') or ('视频榜缺失' if view['status'] != 'ok' else '；'.join(errors) or None))
    for rows in view.get('lists', {}).values():
        for row in rows:
            item = _video(row)
            prior = items.get(item['id'])
            if not prior or _ts(item['fetched_at']) > _ts(prior['fetched_at']):
                items[item['id']] = item
    linux_items, states['L'] = linuxdo.snapshot(root)
    for item in linux_items:
        items[item['id']] = item
    try:
        feedback = _load_feedback(root)
    except ValueError as exc:
        feedback = {}
        states['feedback'] = dict(updated_at=None, error=str(exc))
    for item in items.values():
        prepare_content(item)
        item['hot'] = (grok.number(item['metrics'].get('views')) or 0) >= HOT_VIEWS
        _heat(item)
        record = feedback.get(item['id'], {})
        item.update(status=record.get('status', 'new'), feedback=record.get('feedback'))
    return list(items.values()), states


def page_view(root, query=None, now=None):
    query = query or {}
    if set(query) - {'range', 'tier', 'source', 'topic', 'q', 'page', 'author', 'channel'} or any(not isinstance(v, list) or len(v) != 1 for v in query.values()):
        raise ValueError('今日查询参数无效')
    if query.get('channel', ['hot'])[0] not in ('hot', 'interest'):
        raise ValueError('通道无效')
    if query.get('channel') == ['interest']:
        from interest_channel import page_view as interest_view
        return {**interest_view(root, query, now), 'clue_morning': morning_snapshot(root, now)}
    get = lambda k, default='': query.get(k, [default])[0]
    period = get('range', 'today')
    if period not in ('today', '48h', '7d'):
        raise ValueError('时间范围只能是 today、48h、7d')
    page = int(get('page', '1'))
    if page < 1:
        raise ValueError('页码至少为 1')
    if get('tier') not in ('', 'top', '火爆', '其他', *TIERS) or get('source') not in ('', 'G', 'A', 'V', 'L'):
        raise ValueError('热度或来源参数无效')
    linuxdo.refresh_background(root)
    now = now or datetime.now(timezone.utc)
    author = get('author')
    items, states = snapshot(root, now, author_mode=bool(author))
    cutoff = now - timedelta(days=30) if author else now.astimezone(grok.BEIJING).replace(hour=0, minute=0, second=0, microsecond=0) if period == 'today' else now - timedelta(hours=48 if period == '48h' else 168)
    filtered = []
    for item in items:
        published = grok.stamp(item['published_at'])
        if not published or not cutoff <= published <= now:
            continue
        if author and author.casefold() not in (item['author']['handle'].casefold(), item['author']['name'].casefold()):
            continue
        filtered.append(item)
    filtered, filtering = apply_gate(filtered)
    filtering['aisa_all'], all_audit = aisa_full_stats(root, cutoff, now)
    seen = {p['id'] for p in filtering['items']}
    filtering['aisa_all_items'] = all_audit
    filtering['items'].extend(dict(p, sources=['AIsa 51 人全量推文']) for p in all_audit if p['id'] not in seen)
    filtering['total'] = len({p['id'] for p in filtering['items'] if p['hidden']})
    items = filtered
    filtered = []
    for item in items:
        if get('source') and get('source') not in item['sources']:
            continue
        if get('tier') == '火爆' and not item['hot']:
            continue
        if get('tier') and get('tier') not in ('top', '火爆') and get('tier') != item['tier']:
            continue
        if get('topic') and get('topic') != item['topic']:
            continue
        if get('q').casefold() not in (item['title'] + item['full_text'] + str(item['author'])).casefold():
            continue
        filtered.append(item)
    filtered.sort(key=lambda p: (p['content_insufficient'], TIERS.get(p['tier'], 3), -p['metrics']['heat'], -_ts(p['published_at']), p['id']))
    top = sorted([p for p in filtered if not p['content_insufficient'] and p['tier'] in TIERS and (p['fit_score'] or 0) >= 2 and (p['doable'] or 0) >= 2], key=lambda p: (-(p['fit_score'] + p['doable']), -_ts(p['published_at'])))[:3]
    waiting = not any(p['fit_score'] is not None and p['doable'] is not None for p in filtered if 'G' in p['sources'])
    if get('tier') == 'top':
        filtered = top
    summary = lambda p: {k: p[k] for k in SUMMARY}
    return dict(items=[summary(p) for p in filtered[(page - 1) * 50:page * 50]], total=len(filtered), page=page, page_size=50,
                top=[summary(p) for p in top], top_message='' if top else '等 Grok Bot 按画像打分后出现' if waiting else '今天没有值得做的',
                sources=states, filtering=filtering, topics=sorted({p['topic'] for p in items if p['topic']}),
                clue_morning=morning_snapshot(root, now))


def item_view(root, query):
    if query.get('channel') == ['interest'] and set(query) == {'id', 'channel'} and len(query['id']) == 1:
        from interest_channel import snapshot as interest_snapshot
        item = next((p for p in interest_snapshot(root)[0] if p['id'] == query['id'][0]), None)
        if item is None:
            raise ValueError('兴趣条目不存在或报告已过期')
        return item
    if set(query) != {'id'} or len(query['id']) != 1:
        raise ValueError('详情必须提供一个 id')
    item = next((p for p in snapshot(root, author_mode=True)[0] if p['id'] == query['id'][0]), None)
    if item is None:
        raise ValueError('条目不存在或已下榜')
    return item


def save_feedback(root, payload):
    if isinstance(payload, dict) and payload.get('channel') == 'interest':
        from interest_channel import save_feedback as interest_feedback
        return interest_feedback(root, payload)
    if not isinstance(payload, dict) or set(payload) != {'id', 'action'} or not isinstance(payload['id'], str) or not isinstance(payload['action'], str) or payload['action'] not in ACTIONS:
        raise ValueError('只允许 id 和 action，动作必须为 accurate、inaccurate、keep、use、clear')
    item = item_view(root, {'id': [payload['id']]})
    action, identifier = payload['action'], item['id']
    with _feedback_lock(root):
        records = _load_feedback(root)
        record = dict(records.get(identifier, {'status': 'new', 'feedback': None}))
        previous = dict(record)
        if action == 'clear':
            records.pop(identifier, None)
            record = {'status': 'new', 'feedback': None}
        else:
            if action in ('accurate', 'inaccurate'):
                record['feedback'] = action
            else:
                record['status'] = 'kept' if action == 'keep' else 'used'
            record['updated_at'] = datetime.now(timezone.utc).isoformat()
            records[identifier] = record
        if 'G' in item['sources']:
            verdict = None if action == 'clear' else 'used' if record['status'] == 'used' else 'not_worth' if record['feedback'] == 'inaccurate' else 'worth'
            grok.save_feedback(root, {'tweet_id': identifier.removeprefix('x:'), 'verdict': verdict})
        if 'V' in item['sources']:
            if action == 'clear':
                # 视频旧 API 无撤销动作；复用其锁/损坏校验/原子写入器，移除同一 key。
                with video._FEEDBACK_LOCK:
                    entries, status = video.load_feedback(root)
                    if status == 'invalid':
                        raise ValueError('视频反馈文件损坏，已停止写入')
                    entries.pop(item['video_key'], None)
                    video._atomic_write(video._radar_dir(root) / 'feedback.json', entries)
            else:
                video.save_feedback(root, {'key': item['video_key'], 'verdict': 'not_worth' if record['feedback'] == 'inaccurate' else 'worth'})
        if action in ('keep', 'use'):
            from items_store import ItemsStore
            ItemsStore(root).upsert({**item, **record, 'why_viral': item.get('why', '')})
            if action == 'use':
                ItemsStore(root).action(identifier, 'use')
        grok._atomic_write(feedback_path(root), (json.dumps(records, ensure_ascii=False, indent=2, allow_nan=False) + '\n').encode())
        accumulate_inaccurate(root, item, previous, action)
    return dict(id=identifier, **record)


def feedback_route(handler, payload):
    """让 server.py 保持三行装配，异常映射也归本模块。"""
    try:
        return handler.reply(200, save_feedback(handler.server.store.workspace, payload))
    except (grok.FeedbackCorrupt, video.FeedbackCorrupt) as exc:
        return handler.reply(409, {'error': str(exc)})
    except ValueError as exc:
        return handler.reply(400, {'error': str(exc)})
    except OSError:
        return handler.reply(500, {'error': '反馈未保存，请稍后重试'})
