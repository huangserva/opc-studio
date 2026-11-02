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
from topic_registry import topic_key, topic_options, matches_topic
from platform_registry import identity, annotate_items, registries, parse_filters, matches

_LOCK = threading.RLock()
ACTIONS = {'accurate', 'inaccurate', 'keep', 'use', 'clear'}
METRICS = ('views', 'likes', 'bookmarks', 'retweets', 'replies', 'views_per_hour', 'hours_since_created')
EXTRA = ('why', 'pattern', 'fit_score', 'doable', 'my_angle', 'outline', 'doable_resource', 'incomplete_reason', 'author_type', 'author_verification_type')
HOT_VIEWS = 100000
SUMMARY = ('platform', 'channels', 'platform_info', 'channel_details', 'fetched_at', 'hot', 'id', 'kind', 'sources', 'author', 'published_at', 'title', 'metrics', 'tier', 'topic', 'fit_score', 'doable', 'content_complete', 'status', 'feedback', 'content_insufficient', 'relevance_note', 'rss_boards', 'reply_change', 'official')
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
                    files[name] = data.get('posts', []) + data.get('entries', []) + data.get('items', [])
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
    analysis = str(item.get('why') or '') + '\n' + str(item.get('pattern') or '')
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
    item.setdefault('fetched_at', None)
    if 'platform_info' not in item:
        item.update(identity(item))
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
        item.setdefault('official', None)
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
                author=author, author_type=extra.get('author_type', g.get('author_type')), author_verification_type=extra.get('author_verification_type', g.get('author_verification_type')), published_at=row.get('created_at'), full_text=grok.text(row.get('full_text')) or grok.text(row.get('text')),
                title=grok.text(g.get('title_zh')), url=row.get('url'), metrics={k: grok.number(row.get(k)) for k in METRICS},
                tier=_tier(g or a or row), topic=grok.text(row.get('topic')) or grok.text(row.get('track')),
                fit_score=grok.number(extra.get('fit_score', row.get('fit_score'))), doable=grok.number(extra.get('doable', row.get('doable'))),
                content_complete=row.get('content_complete', True), incomplete_reason=grok.text(extra.get('incomplete_reason')),
                why=extra.get('why', g.get('why') or a.get('viral_reason') or ''), pattern=extra.get('pattern', g.get('pattern') or ''),
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
    from official_accounts import annotate
    annotate(list(items.values()), root)
    for item in items.values():
        prepare_content(item)
        item['hot'] = item['official']['hot'] if item['official']['is_official'] else (grok.number(item['metrics'].get('views')) or 0) >= HOT_VIEWS
        _heat(item)
        record = feedback.get(item['id'], {})
        item.update(status=record.get('status', 'new'), feedback=record.get('feedback'))
    annotate_items(root, list(items.values()))
    return list(items.values()), states


def interest_detail_metrics(root, items):
    """列表范围影响入选来源；数字口径与详情的7天合并记录一致，只读数字。"""
    import sqlite3
    from interest_projection import connect
    wanted={item['id'] for item in items}
    if not wanted:return items
    try:
        with connect(root) as db:
            # 不读取正文/detail，也不逐条调用详情造成N+1次原文解析。
            rows=db.execute("SELECT id,json_extract(summary,'$.metrics') FROM cards WHERE period='7d'")
            merged={ident:json.loads(raw) for ident,raw in rows if ident in wanted and raw}
    except (OSError,sqlite3.Error,ValueError,TypeError):return items
    output=[]
    for item in items:
        metrics=merged.get(item['id'])
        if isinstance(metrics,dict):
            metrics={key:value for key,value in metrics.items() if isinstance(value,(int,float)) and not isinstance(value,bool) and math.isfinite(value)}
            output.append(dict(item,metrics=metrics))
        else:output.append(item)
    return output


def heat_thresholds():
    # 来源等级是采集侧标签，不为起势臆造浏览量阈值。
    return dict(hot_views=HOT_VIEWS, tiers={'爆款':'来源标记为爆款', '起势':'来源标记为起势', '其他':'未标记爆款、起势或低粉爆款'}, hot_rule='浏览 ≥ HOT_VIEWS；官方号还须超过自身粉丝数')


def matches_tier(item, tier):
    if not tier or tier == 'top':return True
    if tier == '火爆':return bool(item.get('hot'))
    if tier == '其他':return item.get('tier') not in TIERS
    return item.get('tier') == tier


def sort_items(items, mode):
    if not mode:return
    if mode == 'heat':
        for item in items:_heat(item)
        key=lambda p:(-(grok.number(p['metrics'].get('heat')) or 0), -_ts(p.get('published_at')), p['id'])
    elif mode == 'time':key=lambda p:(-_ts(p.get('published_at')),p['id'])
    else:key=lambda p:(-(grok.number(p.get('relevance')) or 0), -(grok.number(p.get('fit_score')) or 0), -(grok.number(p.get('doable')) or 0), -_ts(p.get('published_at')),p['id'])
    items.sort(key=key)


def today_todo_view(root, now=None):
    """Read today's daily report verbatim in report order; never rank or borrow yesterday."""
    now = now or datetime.now(timezone.utc)
    date = now.astimezone(grok.BEIJING).date().isoformat()
    result = dict(date=date, report='daily/' + date + '.json', status='pending', items=[],
                  reason='今天的日报还没出，09:00 后更新')
    try:
        projection = grok.load_today_todo(date, with_notes=True)
        todo = projection['items']
        if todo is None:
            return result
        if not isinstance(todo, list):
            raise ValueError('今天该做的清单结构不正确')
        # Use the same daily file only to complete content/metrics; the reader owns todo order and presence.
        data, _ = grok._read_json(grok.reports_dir() / result['report'])
        if not isinstance(data, dict) or data.get('date', date) != date:
            raise ValueError('当天日报日期或结构不正确')
        rows = {}
        for key in ('items', 'posts', 'entries'):
            for row in data.get(key, []) if isinstance(data.get(key), list) else []:
                if isinstance(row, dict):
                    rows[str(row.get('id', row.get('tweet_id', ''))).removeprefix('x:')] = row
        def project(entry):
            entry = {'id': entry} if isinstance(entry, (str, int)) and not isinstance(entry, bool) else entry
            if not isinstance(entry, dict):
                raise ValueError('今天该做的条目结构不正确')
            report_id = str(entry.get('id', entry.get('tweet_id', ''))).removeprefix('x:')
            if not grok.ID_RE.fullmatch(report_id):
                raise ValueError('今天该做的条目缺少有效帖子 id')
            raw = {**rows.get(report_id, {}), **entry}
            author = raw.get('author')
            if isinstance(author, dict):
                raw.update(author=author.get('name') or author.get('handle'), handle=raw.get('handle') or author.get('handle'), followers=raw.get('followers', author.get('followers')))
            title = raw.get('title_zh') or raw.get('title') or raw.get('what_new') or raw.get('full_text') or raw.get('text') or raw.get('why') or '日报条目'
            if not (grok.text(raw.get('full_text')) or grok.text(raw.get('text'))):
                raw.update(content_complete=False, incomplete_reason='日报只提供建议，未提供完整原文')
            record = {**raw, 'id': report_id, 'sources': ['Grok'],
                      'grok': {**raw, 'title_zh': grok.text(title)},
                      'collected_at': raw.get('collected_at') or data.get('collected_at_iso') or data.get('collected_at')}
            metrics = raw.get('metrics')
            if isinstance(metrics, dict):
                record.update({k: metrics[k] for k in METRICS if k in metrics and k not in record})
            item = _post(record, {report_id: {k: raw[k] for k in EXTRA if k in raw}})
            item.update(report_id=report_id, hot=(grok.number(item['metrics'].get('views')) or 0) >= HOT_VIEWS)
            return item
        items = [project(entry) for entry in todo]
        try:
            feedback = _load_feedback(root)
        except ValueError:
            feedback = {}
        # The original curated list is unchanged. Extras are a separately labelled pool.
        extras = []
        selected = {item['id'] for item in items}
        batch_error = None
        try:
            if items:
                for report_id, row in rows.items():
                    if not grok.ID_RE.fullmatch(report_id) or 'x:' + report_id in selected:
                        continue
                    try:
                        candidate = project(row)
                    except (ValueError, TypeError):
                        continue
                    if candidate['tier'] in TIERS:
                        extras.append(candidate)
                from official_accounts import annotate
                annotate(extras, root)
                extras = [item for item in extras if not item['official']['is_official']]
                # Same score/date key as the existing top recommendations; stable ties keep report order.
                extras.sort(key=lambda item: (-((item['fit_score'] or 0) + (item['doable'] or 0)), -_ts(item['published_at'])))
        except (OSError, ValueError, TypeError):
            extras = []
            batch_error = '后续候选暂不可读，Grok 精选仍保留'
        for item in items + extras:
            record = feedback.get(item['id'])
            if record:
                item.update(status=record.get('status', 'new'), feedback=record.get('feedback'))
        annotate_items(root, items)
        annotate_items(root, extras)
        notes = projection.get('notes')
        reason = notes if isinstance(notes, str) and notes.strip() else '今天的日报没有安排值得做的条目。'
        result.update(status='ready' if items else 'empty', items=items, batch_candidates=extras, batch_size=3, batch_error=batch_error, reason='' if items else reason)
    except FileNotFoundError:
        pass
    except (OSError, ValueError, TypeError, UnicodeError, RecursionError):
        result.update(status='unavailable', reason='当天日报暂不可读，不能据此判断今天没有值得做的。')
    return result


def page_view(root, query=None, now=None):
    query = query or {}
    # A bounded receipt-only read: never scan reports, initialize state or trigger work.
    if 'source_catchup' in query:
        if query != {'source_catchup': ['1']}:
            raise ValueError('漏报状态查询参数无效')
        from source_catchup import status_snapshot
        return {'source_catchup': status_snapshot(_workspace(root))}
    if set(query) - {'range', 'tier', 'source', 'topic', 'q', 'page', 'author', 'channel', 'platform', 'collection_channel', 'view', 'audit', 'sort'} or any(not isinstance(v, list) or not v or len(v) > (10 if k == 'platform' else 1) or any(not isinstance(x,str) or len(x)>2000 for x in v) for k,v in query.items()):
        raise ValueError('今日查询参数无效')
    if query.get('view', [''])[0] not in ('','summary') or query.get('audit',[''])[0] not in ('','0') or ('view' in query or 'audit' in query) and query.get('channel') != ['interest']:
        raise ValueError('摘要参数无效')
    mode=query.get('sort',[''])[0]
    if mode not in ('','relevance','heat','time'):raise ValueError('排序参数无效')
    tier=query.get('tier',[''])[0]
    if tier not in ('','top','火爆','其他',*TIERS):raise ValueError('热度参数无效')
    filters = parse_filters(query)
    if query.get('channel', ['hot'])[0] not in ('hot', 'interest', 'official'):
        raise ValueError('通道无效')
    if query.get('channel') == ['interest']:
        from interest_channel import cross_source_page as interest_view
        import interest_match as interest_matching
        from official_accounts import annotate, interest_hints
        # 先汇齐兴趣与微信工具，再用 K1 平台/采集渠道筛选，最后分页。
        clean = {k:v for k,v in query.items() if k not in ('platform','collection_channel','source','topic','tier','sort')}
        result = interest_view(root, {**clean, 'page':['1']}, now)
        # 同一完整快照的匹配基数；与 filtering（兴趣规则排除）和用户筛选分开。
        # 旧读模型的 total 尚未经过 SQL q/author、平台、方向、热度筛选。
        range_total = result.pop('range_total', result['total'])
        candidates = interest_detail_metrics(root,result['items'])
        if mode=='heat':candidates=[dict(item,metrics=dict(item.get('metrics') or {})) for item in candidates]
        candidates = [item for item in candidates if matches(item, filters, query.get('source',[''])[0])]
        options = interest_matching.options(candidates,interest_matching.compile_topics(result.pop('configured_topics')))
        requested_topic=query.get('topic',[''])[0]
        selected_topic=requested_topic if requested_topic.startswith('custom:') else topic_key(requested_topic) if requested_topic else ''
        candidates = [item for item in candidates if not selected_topic or any(m['direction']==selected_topic for m in item['interest_matches'])]
        candidates = [item for item in candidates if matches_tier(item,tier)]
        registry = registries()
        active_filters = []
        if filters[0]:active_filters.append('平台：'+'、'.join(registry['platforms'][key]['display_name'] for key in filters[0]))
        if filters[1]:active_filters.append('渠道：'+registry['channels'][filters[1]]['display_name'])
        if query.get('source',[''])[0]:
            from today_filter_config import SOURCE_NAMES
            legacy_source=query['source'][0].upper()
            active_filters.append('来源：'+SOURCE_NAMES.get(legacy_source,legacy_source))
        if requested_topic:active_filters.append('方向：'+next((row['display_name'] for row in options if row['key']==selected_topic),requested_topic))
        if tier and tier!='top':active_filters.append({'爆款':'只看爆款','火爆':'火爆徽标'}.get(tier,'来源热度：'+tier))
        if query.get('q',[''])[0]:active_filters.append('搜索：'+query['q'][0])
        if query.get('author',[''])[0]:active_filters.append('作者：'+query['author'][0])
        result['filter_context']=dict(range_total=range_total,active_filters=active_filters,source_tier=tier,
                                    range=query.get('range',['today'])[0])
        sort_items(candidates,mode)
        if mode:result['sort_policy']={'relevance':'相关度倒序 · 画像与可做分辅助 · 发布时间倒序','heat':'既有排序热度倒序 · 发布时间倒序','time':'发布时间倒序'}[mode]
        from collections import Counter
        result['daily_counts']=dict(Counter(grok.stamp(p['fetched_at']).astimezone(grok.BEIJING).date().isoformat() for p in candidates))
        requested = int(query.get('page',['1'])[0])
        if requested < 1:raise ValueError('页码至少为 1')
        result.update(items=candidates[(requested-1)*50:requested*50],total=len(candidates),page=requested,registries=registries(),topic_options=options,topics=[row['display_name'] for row in options],selected_topic=selected_topic)
        try:live_feedback=_load_feedback(root)
        except ValueError:live_feedback={}
        for item in result['items']:
            record=live_feedback.get('interest:'+item['id'])
            if record:item.update(status=record.get('status','new'),feedback=record.get('feedback'))
        from wechat_tools_view import overlay_feedback
        overlay_feedback(root,result['items'])
        annotate_items(root,result['items'])
        hints=result.pop('_official_hints',None)
        if hints is None:hints=interest_hints() if result['items'] else {}
        annotate(result['items'], root, hints=hints)
        comparisons=[p for item in result['items'] for p in item.get('_discovery_candidates',[])]
        if comparisons:annotate(comparisons,root)
        for item in result['items']:
            own_comparisons=item.pop('_discovery_candidates',None)
            if own_comparisons is not None:
                item['discovery_overlap']=any(not p['official']['is_official'] or p['official']['hot'] for p in own_comparisons)
        interest_fields=(*SUMMARY,'channel','what_new','usable','relevance','incomplete_reasons','canonical_key','clue_id','identity_state','input_sources','interest_matches','discovery_overlap','interest_rule_version','interest_rank',*interest_matching.TOOL_FIELDS)
        result['items']=[{key:item.get(key) for key in interest_fields} for item in result['items']]
        return {**result, 'heat_thresholds':heat_thresholds(), 'selected_sort':mode, 'clue_morning': morning_snapshot(root, now), 'today_todo': today_todo_view(root, now)}
    get = lambda k, default='': query.get(k, [default])[0]
    period = get('range', 'today')
    if period not in ('today', '48h', '7d'):
        raise ValueError('时间范围只能是 today、48h、7d')
    page = int(get('page', '1'))
    if page < 1:
        raise ValueError('页码至少为 1')
    if get('tier') not in ('', 'top', '火爆', '其他', *TIERS):
        raise ValueError('热度或来源参数无效')
    linuxdo.refresh_background(root)
    now = now or datetime.now(timezone.utc)
    author = get('author')
    items, states = snapshot(root, now, author_mode=bool(author))
    from official_accounts import annotate
    annotate(items, root)
    official_channel = get('channel') == 'official'
    if official_channel:
        from interest_channel import snapshot as interest_snapshot
        from official_accounts import annotate, interest_hints
        extra_items, _ = interest_snapshot(root, now)
        annotate(extra_items, root, hints=interest_hints())
        for item in extra_items:prepare_content(item)
        annotate_items(root, extra_items)
        known_ids = {p['id'] for p in items}
        items.extend(p for p in extra_items if p['id'] not in known_ids)
    cutoff = now - timedelta(days=30) if author else now.astimezone(grok.BEIJING).replace(hour=0, minute=0, second=0, microsecond=0) if period == 'today' else now - timedelta(hours=48 if period == '48h' else 168)
    filtered = []
    for item in items:
        published = grok.stamp(item['published_at'])
        if not published or not cutoff <= published <= now:
            continue
        if author and author.casefold() not in (item['author']['handle'].casefold(), item['author']['name'].casefold()):
            continue
        filtered.append(item)
    if official_channel:
        filtered = [p for p in filtered if p['official']['is_official']]
        filtering = dict(by_source={}, items=[], total=0,
                         policy='本栏收录全部官方号帖子，按发布时间倒序；身份不符不计入本栏已过滤条数。')
    else:
        filtered, filtering = apply_gate(filtered)
        from today_filter_config import SOURCE_NAMES
        diverted = [p for p in filtered if p['official']['is_official'] and not p['official']['hot']]
        for item in diverted:
            filtering['items'].append(dict(id=item['id'], title=item['title'], url=item.get('url',''),
                sources=[SOURCE_NAMES.get(k,k) for k in item['sources']], hidden=True,
                reason='已分流至官方号栏：'+item['official']['eligibility']))
            for source in set(SOURCE_NAMES.get(k,k) for k in item['sources']):
                count=filtering['by_source'][source];count['filtered']+=1;count['visible']-=1
                count['ratio']=count['filtered']/count['total'] if count['total'] else 0
        filtered = [p for p in filtered if not p['official']['is_official'] or p['official']['hot']]
        filtering['official_diverted'] = len(diverted)
        filtering['policy'] = '官方号默认分流至官方号栏，计入已过滤；浏览≥10万且超过自身粉丝数才保留在发现和今天该做的3条。兴趣栏保留官方号。'
        filtering['aisa_all'], all_audit = aisa_full_stats(root, cutoff, now)
        seen = {p['id'] for p in filtering['items']}
        filtering['aisa_all_items'] = all_audit
        filtering['total'] = len({p['id'] for p in filtering['items'] if p['hidden']})
        filtering['aisa_all_filtered'] = len({p['id'] for p in all_audit if p['hidden']})
        filtering['items'].extend(dict(p, sources=['AIsa 51 人全量推文']) for p in all_audit if p['id'] not in seen)
    items = filtered
    filtered = []
    for item in items:
        if not matches(item, filters, query.get('source',[''])[0]):
            continue
        if not matches_tier(item,get('tier')):
            continue
        if get('q').casefold() not in (item['title'] + item['full_text'] + str(item['author'])).casefold():
            continue
        filtered.append(item)
    options = topic_options(filtered)
    filtered = [item for item in filtered if matches_topic(item,get('topic'))]
    if not official_channel:
        filtered.sort(key=lambda p: (p['content_insufficient'], TIERS.get(p['tier'], 3), -p['metrics']['heat'], -_ts(p['published_at']), p['id']))
    if official_channel:
        filtered.sort(key=lambda p: (-_ts(p['published_at']), p['id']))
    sort_items(filtered,mode)
    top = sorted([p for p in filtered if not official_channel and not p['content_insufficient'] and p['tier'] in TIERS and (p['fit_score'] or 0) >= 2 and (p['doable'] or 0) >= 2], key=lambda p: (-(p['fit_score'] + p['doable']), -_ts(p['published_at'])))[:3]
    waiting = not any(p['fit_score'] is not None and p['doable'] is not None for p in filtered if 'G' in p['sources'])
    if get('tier') == 'top':
        filtered = list(top)
        sort_items(filtered,mode)
    summary = lambda p: {**{k: p[k] for k in SUMMARY}, **({k:p[k] for k in ('channel','what_new','usable','relevance','incomplete_reasons')} if p.get('channel')=='interest' else {})}
    return dict(items=[summary(p) for p in filtered[(page - 1) * 50:page * 50]], total=len(filtered), page=page, page_size=50,
                top=[summary(p) for p in top], top_message='' if top else '等 Grok Bot 按画像打分后出现' if waiting else '今天没有值得做的',
                registries=registries(), sources=states, filtering=filtering, topics=[row['display_name'] for row in options], topic_options=options, selected_topic=topic_key(get('topic')) if get('topic') else '',
                heat_thresholds=heat_thresholds(),selected_sort=mode,clue_morning=morning_snapshot(root, now),today_todo=today_todo_view(root, now))


def item_view(root, query):
    if set(query) == {'id', 'today_todo'} and query.get('today_todo') == ['1'] and len(query['id']) == 1:
        todo = today_todo_view(root)
        item = next((p for p in todo['items'] + todo.get('batch_candidates', []) if p['id'] == query['id'][0]), None)
        if item is None:
            raise ValueError('当日日报没有这条待办')
        return item
    if query.get('channel') == ['official'] and set(query) == {'id', 'channel'} and len(query['id']) == 1:
        item = next((p for p in snapshot(root, author_mode=True)[0] if p['id'] == query['id'][0]), None)
        if item is None:
            from interest_channel import snapshot as interest_snapshot
            from official_accounts import annotate, interest_hints
            item = next((p for p in interest_snapshot(root)[0] if p['id'] == query['id'][0]), None)
            if item is not None:annotate([item], root, hints=interest_hints())
        if item is None:raise ValueError('官方号条目不存在或报告已过期')
        return annotate_items(root, [item])[0]
    if query.get('channel') == ['interest'] and set(query) == {'id', 'channel'} and len(query['id']) == 1:
        from interest_projection import detail
        item = detail(root,query['id'][0])
        if item is None:
            from interest_channel import cross_source_page
            item = next((p for p in cross_source_page(root,{'range':['7d']})['items'] if p['id'] == query['id'][0]), None)
        if item is None:
            raise ValueError('兴趣条目不存在或报告已过期')
        from official_accounts import annotate, interest_hints
        annotate([item], root, hints=interest_hints())
        comparisons=item.pop('_discovery_candidates',None)
        if comparisons is not None:
            annotate(comparisons,root)
            item['discovery_overlap']=any(not p['official']['is_official'] or p['official']['hot'] for p in comparisons)
        try:record=_load_feedback(root).get('interest:'+item['id'])
        except ValueError:record=None
        if record:item.update(status=record.get('status','new'),feedback=record.get('feedback'))
        from wechat_tools_view import overlay_feedback
        overlay_feedback(root,[item])
        return annotate_items(root, [item])[0]
    if set(query) != {'id'} or len(query['id']) != 1:
        raise ValueError('详情必须提供一个 id')
    item = next((p for p in snapshot(root, author_mode=True)[0] if p['id'] == query['id'][0]), None)
    if item is None:
        todo = today_todo_view(root)
        item = next((p for p in todo['items'] + todo.get('batch_candidates', []) if p['id'] == query['id'][0]), None)
    if item is None:
        raise ValueError('条目不存在或已下榜')
    return annotate_items(root, [item])[0]


def save_feedback(root, payload):
    if isinstance(payload, dict) and isinstance(payload.get('id'), str) and payload['id'].startswith('tool:'):
        from wechat_tools_view import feedback
        return feedback(root, payload)
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
    except __import__('wechat_tools_config').Conflict as exc:
        return handler.reply(409, {'error': str(exc)})
    except ValueError as exc:
        return handler.reply(400, {'error': str(exc)})
    except OSError:
        return handler.reply(500, {'error': '反馈未保存，请稍后重试'})
