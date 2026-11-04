"""发布复盘聚合：只读文件与 SQLite，不初始化旧存储、不发远端请求。"""
from __future__ import annotations
import json
import math
import re
import threading
from collections import OrderedDict
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit

from items_store import readonly
from publication_metrics import PUBLISHED, slug_value

METRICS = {
 'views': ('浏览', ('views','view_count','Views','浏览','浏览量','阅读','播放量','观看量','视频播放量')),
 'impressions': ('曝光', ('impression_count','impressions','Impressions','曝光','曝光量')),
 'likes': ('赞', ('like_count','likes','Likes','点赞','赞')),
 'retweets': ('转发', ('retweet_count','retweets','reposts','Reposts','转发','分享')),
 'replies': ('回复', ('reply_count','replies','Replies','回复','评论')),
 'bookmarks': ('收藏', ('bookmark_count','bookmarks','Bookmarks','收藏')),
}


def read_json(path, default):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError, UnicodeError):
        return default


def stamp(value):
    try:
        parsed = datetime.fromisoformat(str(value).replace('Z','+00:00'))
        return parsed.timestamp() if parsed.tzinfo else 0
    except (ValueError, TypeError, OverflowError):
        return 0


def valid_number(value):
    if isinstance(value,bool):
        return None
    if isinstance(value,str):
        value = value.strip().replace(',','')
        if not re.fullmatch(r'\d+(?:\.\d+)?', value):
            return None
    try:
        number = float(value)
        return int(number) if math.isfinite(number) and 0 <= number <= 10**18 and number.is_integer() else number if math.isfinite(number) and 0 <= number <= 10**18 else None
    except (ValueError,TypeError,OverflowError):
        return None


# Grok 报告只读入口；根目录不来自 URL、表单或报告内的路径。
GROK_REPORTS_ROOT = Path.home() / 'grok-bot-reports'
BEIJING = timezone(timedelta(hours=8))
POST_TYPES = ('主帖', '长文', '引用', '回复')
X_METRICS = {'views':'浏览', 'likes':'赞', 'bookmarks':'收藏', 'retweets':'转发', 'replies':'回复', 'quotes':'引用'}
TYPE_ALIASES = {'post':'主帖', 'original':'主帖', 'video':'主帖', 'long':'长文', 'article':'长文', 'quote':'引用', 'reply':'回复', 'retweet':'转帖'}
MAX_REPORT_BYTES = 16 * 1024 * 1024
_REPORT_CACHE = OrderedDict()
_REPORT_LOCK = threading.RLock()


def grok_time(value):
    """只接收有时区的 ISO 或 Grok 已使用的显式 Asia/Shanghai 格式。"""
    if not isinstance(value, str):
        return ''
    if value.endswith(' Asia/Shanghai'):
        try:
            return datetime.fromisoformat(value[:-14]).replace(tzinfo=BEIJING).astimezone(timezone.utc).isoformat()
        except ValueError:
            return ''
    try:
        return datetime.fromtimestamp(stamp(value), timezone.utc).isoformat() if stamp(value) else ''
    except (ValueError, OverflowError, OSError):
        return ''


def _reject_constant(value):
    raise ValueError('非标准 JSON 数字')


def x_post_id(url):
    if not isinstance(url, str):
        return None
    try:
        p = urlsplit(url)
        if p.scheme not in ('https', 'http') or p.hostname not in ('x.com', 'www.x.com', 'twitter.com', 'www.twitter.com') or p.username or p.password:
            return None
        match = re.fullmatch(r'/[A-Za-z0-9_]{1,15}/status/(\d{1,25})/?', p.path)
        return match[1] if match else None
    except ValueError:
        return None


def http_url(value):
    if not isinstance(value, str) or re.search(r'[\s\x00-\x1f]', value):
        return None
    try:
        p = urlsplit(value)
        return value if p.scheme in ('https', 'http') and p.hostname and not p.username and not p.password else None
    except ValueError:
        return None


def _newer_observation(left, right):
    # 时间无法解析时只按已知的文件日期/轮次选展示值，不伪造曲线时间。
    return left >= right if left[0] and right[0] else left[1:] >= right[1:]


def radar_reference(value):
    if not isinstance(value, dict) or not http_url(value.get('url')):
        return None
    report = value.get('report', '')
    if not isinstance(report, str) or report and (not re.fullmatch(r'[A-Za-z0-9_./-]+\.json', report) or report.startswith('/') or '..' in report.split('/')):
        return None
    return {'url':value['url'], 'title':value.get('title') if isinstance(value.get('title'), str) else '打开原条目', 'report':report, 'basis':'Grok 明确标记'}


def weekly_section(markdown):
    """返回目标标题到下一同级/上级标题前的原文；代码围栏中的标题不是节边界。"""
    start = level = None
    fence = None
    offset = 0
    for line in markdown.splitlines(keepends=True):
        marker = re.match(r'^ {0,3}(`{3,}|~{3,})', line)
        if marker:
            token = marker[1]
            if fence is None:
                fence = token
            elif token[0] == fence[0] and len(token) >= len(fence):
                fence = None
        elif fence is None:
            heading = re.match(r'^ {0,3}(#{1,6})[ \t]+(.+?)\s*#*\s*$', line)
            if heading:
                depth, title = len(heading[1]), heading[2]
                if start is not None and depth <= level:
                    return markdown[start:offset]
                if start is None and re.fullmatch(r'(?:[0-9一二三四五六七八九十]+[.、．)）]?\s*)?本周产出复盘', title):
                    start, level = offset, depth
        offset += len(line)
    return markdown[start:] if start is not None else ''


def readable_summary(value, depth=0):
    # 只呈现原字符串，不泄露结构键名、账号/帖子 ID 或纯数字。
    if depth > 15:
        return []
    if isinstance(value, str):
        return [value] if re.search(r'[\u3400-\u9fff]|[A-Za-z]+[ \t]+[A-Za-z]+', value) else []
    if isinstance(value, (list, dict)):
        return [text for child in (value.values() if isinstance(value, dict) else value) for text in readable_summary(child, depth + 1)]
    return []


class GrokReports:
    def __init__(self, root=None, now=None):
        self.root = Path(root if root is not None else GROK_REPORTS_ROOT).resolve()
        self.today = (now or datetime.now(BEIJING)).astimezone(BEIJING).date()

    def _read(self, relative):
        # 所有实际打开的路径均由固定目录和严格文件名产生；from_radar.report 仅展示。
        if not re.fullmatch(r'(?:posts/(?:review-)?\d{4}-\d{2}-\d{2}\.json|weekly/\d{4}-W\d{2}\.(?:json|md))', relative):
            return None, '文件名无效'
        path = self.root / relative
        try:
            if path.is_symlink() or path.parent.is_symlink() or not path.resolve().is_relative_to(self.root):
                return None, '拒绝符号链接'
            info = path.stat()
            signature = (info.st_mtime_ns, info.st_size, info.st_ino)
            key = str(path)
            with _REPORT_LOCK:
                cached = _REPORT_CACHE.get(key)
                if cached and cached[0] == signature:
                    _REPORT_CACHE.move_to_end(key)
                    return cached[1], cached[2]
            if info.st_size > MAX_REPORT_BYTES:
                data, error = None, '文件过大，未读取'
            else:
                with path.open('rb') as stream:
                    raw = stream.read(MAX_REPORT_BYTES + 1)
                if len(raw) > MAX_REPORT_BYTES:
                    data, error = None, '文件过大，未读取'
                else:
                    data = raw.decode('utf-8') if relative.endswith('.md') else json.loads(raw, parse_constant=_reject_constant)
                    error = '' if isinstance(data, str if relative.endswith('.md') else dict) else '顶层格式无效'
                    if error:
                        data = None
            with _REPORT_LOCK:
                _REPORT_CACHE[key] = (signature, data, error)
                _REPORT_CACHE.move_to_end(key)
                while len(_REPORT_CACHE) > 96 or sum(v[0][1] for v in _REPORT_CACHE.values()) > 40 * 1024 * 1024:
                    _REPORT_CACHE.popitem(last=False)
            return data, error
        except FileNotFoundError:
            return None, 'Grok 当天没抓'
        except (OSError, ValueError, UnicodeError, RecursionError):
            error = '报告损坏或无法读取'
            if 'signature' in locals():
                with _REPORT_LOCK:
                    _REPORT_CACHE[str(path)] = (signature, None, error)
                    while len(_REPORT_CACHE) > 96 or sum(v[0][1] for v in _REPORT_CACHE.values()) > 40 * 1024 * 1024:
                        _REPORT_CACHE.popitem(last=False)
            return None, error

    def _latest_review(self, daily=False):
        folder = 'posts' if daily else 'weekly'
        pattern = r'review-(\d{4}-\d{2}-\d{2})\.json' if daily else r'(\d{4}-W\d{2})\.json'
        paths = sorted((self.root / folder).glob('review-*.json' if daily else '*.json'), reverse=True)
        warnings = []
        for path in paths:
            match = re.fullmatch(pattern, path.name)
            if not match or match[1] > (self.today.isoformat() if daily else self.today.strftime('%G-W%V')):
                continue
            data, error = self._read(folder + '/' + path.name)
            if error:
                warnings.append(path.name + '：' + error)
                continue
            if daily:
                summary = data.get('summary')
                summary = [v for v in summary if isinstance(v, str)] if isinstance(summary, list) else []
                sections = {}
                for name in ('best', 'worst'):
                    values = data.get(name)
                    sections[name] = [{'id':str(v['id']), 'why':v['why']} for v in values if isinstance(v, dict) and isinstance(v.get('id'), (str, int)) and not isinstance(v.get('id'), bool) and re.fullmatch(r'\d{1,25}', str(v['id'])) and isinstance(v.get('why'), str)] if isinstance(values, list) else []
                notes = data.get('notes')
                return {'date':match[1], 'generated_at':grok_time(data.get('generated_at')), 'summary':summary, **sections, 'notes':[v for v in notes if isinstance(v, str)] if isinstance(notes, list) else [], 'is_today':match[1] == self.today.isoformat(), 'warnings':warnings}
            review = data.get('output_review')
            markdown, md_error = self._read('weekly/' + match[1] + '.md')
            section = weekly_section(markdown) if isinstance(markdown, str) else ''
            if md_error and md_error != 'Grok 当天没抓':
                warnings.append(match[1] + '.md：' + md_error)
            return {'week':match[1], 'generated_at':grok_time(data.get('generated_at')),
                    'markdown':section, 'summary_text':readable_summary(data.get('summary')),
                    'output_review':review if isinstance(review, (dict, list, str)) else None, 'warnings':warnings}
        return {'warnings':warnings}

    def listing(self, local_sources=None):
        local_sources = local_sources or {}
        posts, days, warnings = {}, [], []
        for offset in reversed(range(30)):
            day = (self.today - timedelta(days=offset)).isoformat()
            data, error = self._read('posts/' + day + '.json')
            status = {'date':day, 'status':'missing' if error == 'Grok 当天没抓' else 'error' if error else 'ok', 'message':error}
            if error:
                days.append(status)
                if status['status'] == 'error':
                    warnings.append(day + '：' + error)
                continue
            snapshots = data.get('snapshots')
            if not isinstance(snapshots, list) or not snapshots:
                days.append({**status, 'status':'empty', 'message':'Grok 当天没抓（快照为空）'})
                continue
            observations = 0
            for si, snap in enumerate(snapshots):
                if not isinstance(snap, dict) or not isinstance(snap.get('posts'), list):
                    continue
                for pi, post in enumerate(snap['posts']):
                    if not isinstance(post, dict):
                        continue
                    identifier = str(post.get('id', '')) if not isinstance(post.get('id'), bool) else ''
                    if not re.fullmatch(r'\d{1,25}', identifier):
                        identifier = x_post_id(post.get('url'))
                    if not identifier:
                        continue
                    observations += 1
                    capture = grok_time(post.get('captured_at')) or grok_time(snap.get('captured_at'))
                    raw_metrics = post.get('metrics') if isinstance(post.get('metrics'), dict) else {}
                    kind = post.get('type') if isinstance(post.get('type'), str) else '未知类型'
                    kind = TYPE_ALIASES.get(kind, kind if kind in POST_TYPES else '未知类型')
                    point = {'type':kind, 'captured_at':capture, 'metrics':{k:valid_number(raw_metrics.get(k)) for k in X_METRICS}}
                    record = posts.setdefault(identifier, {'points':{}, 'latest_order':None, 'from_radar':None, 'types':{}})
                    key = capture or f'unknown:{day}:{si}'
                    order = (stamp(capture), day, si, pi)
                    record['points'][key] = point
                    record['types'][(kind, capture)] = {'type':kind, 'captured_at':capture}
                    ref = radar_reference(post.get('from_radar'))
                    if ref and (not record['from_radar'] or _newer_observation(order, record['from_radar'][0])):
                        record['from_radar'] = (order, ref)
                    if record['latest_order'] is None or _newer_observation(order, record['latest_order']):
                        url = post.get('url') if x_post_id(post.get('url')) == identifier else 'https://x.com/servasyy_ai/status/' + identifier
                        record.update(latest_order=order, row={'id':identifier, 'url':url, 'type':kind, 'text':post.get('text') if isinstance(post.get('text'), str) else '', 'created_at':grok_time(post.get('created_at')), 'captured_at':capture, 'metrics':point['metrics']})
            if not observations:
                status.update(status='empty', message='Grok 当天没抓（没有有效帖子快照）')
            days.append(status)
        rows = []
        for identifier, record in posts.items():
            row = record['row']
            row['series'] = sorted(record['points'].values(), key=lambda p:stamp(p['captured_at']))
            row['type_history'] = sorted((p for p in record['types'].values() if p['type'] != row['type']), key=lambda p:stamp(p['captured_at']))
            row['from_radar'] = record['from_radar'][1] if record['from_radar'] else local_sources.get(identifier)
            rows.append(row)
        rows.sort(key=lambda p:(stamp(p['created_at']), p['id']), reverse=True)
        cutoff = self.today - timedelta(days=6)
        recent = [r for r in rows if r['created_at'] and cutoff <= datetime.fromtimestamp(stamp(r['created_at']), BEIJING).date() <= self.today]
        captured_days = sum(day['status'] == 'ok' for day in days[-7:])
        counts = {kind:sum(r['type'] == kind for r in recent) if captured_days or recent else None for kind in POST_TYPES}
        known_views = [r['metrics']['views'] for r in recent if r['metrics']['views'] is not None]
        return {'posts':[r for r in rows if r['type'] in POST_TYPES[:3]], 'replies':[r for r in rows if r['type'] == '回复'], 'other_posts':[r for r in rows if r['type'] not in POST_TYPES], 'total':len(rows), 'type_counts':{kind:sum(r['type'] == kind for r in rows) for kind in POST_TYPES},
                'week':{'counts':counts, 'captured_days':captured_days, 'missing_days':7-captured_days, 'other_count':sum(r['type'] not in POST_TYPES for r in recent), 'total':len(recent), 'views':sum(known_views) if known_views else None, 'views_known_posts':len(known_views), 'views_unknown_posts':len(recent)-len(known_views), 'notice':'近 7 天含今天，按北京时间发帖日计数；每帖取最新一次快照，浏览合计含回复及其他类型，仅累加已知值，缺抓与未知不按 0 计，发帖时间未知不计入近7天。'},
                'days':days, 'warnings':warnings, 'weekly':self._latest_review(), 'daily':self._latest_review(True), 'notice':'只读 Grok 最近 30 天抓取文件；旧 video 归主帖，retweet 收在其他类型。无时区时间显示未知。'}


class PublishReview:
    def __init__(self, store):
        self.store = store
        self.workspace = Path(store.workspace)

    def _tables(self):
        data = {'snapshots':[], 'observations':[], 'reviews':[]}
        path = self.workspace / 'publication-feedback/snapshots.db'
        if not path.exists():
            return data
        with closing(readonly(path)) as c:
            tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            for table in data:
                if table in tables:
                    data[table] = [read for r in c.execute('SELECT data FROM '+table) if isinstance((read:=read_json_string(r[0])),dict)]
        return data

    def _source(self, folder):
        ref = read_json(folder / 'source-item.json', None)
        if not isinstance(ref,dict) or not isinstance(ref.get('item_id'),str):
            return None
        title = ref['item_id']
        sources = ref.get('sources') if isinstance(ref.get('sources'),list) else []
        path = self.workspace / 'items/items.db'
        if path.exists():
            with closing(readonly(path)) as c:
                row = c.execute('SELECT title,sources_json FROM items WHERE id=?',(ref['item_id'],)).fetchone()
                if row:
                    title = row['title'] or title
                    sources = read_json_string(row['sources_json']) or sources
        return {'item_id':ref['item_id'], 'title':title, 'sources':sources,
                'url':'/library.html?item='+quote(ref['item_id'],safe='')}

    def _cache(self):
        posts = {}
        for path in (self.workspace / 'x-official/feedback').glob('*.json'):
            cache = read_json(path,{})
            for key,post in (cache.get('posts') or {}).items():
                if not isinstance(post,dict):
                    continue
                candidate = {**post,'collected_at':cache.get('synced_at') or ''}
                if key not in posts or stamp(candidate['collected_at']) >= stamp(posts[key]['collected_at']):
                    posts[key] = candidate
        return posts

    @staticmethod
    def _points(row, data, cache):
        points = []
        for snap in data['snapshots']:
            binding = snap.get('binding') or {}
            if binding.get('slug') == row['slug'] and binding.get('delivery_id') == row['delivery_id']:
                points.append({'collected_at':snap.get('collected_at',''),'metrics':snap.get('metrics') or {},'source':'导入快照','range':snap.get('range',{})})
        for observation in data['observations']:
            binding = observation.get('version_binding') or {}
            if binding.get('slug') == row['slug'] and binding.get('delivery_id') == row['delivery_id']:
                metrics = {m['label']:m.get('value') for m in observation.get('metrics',[]) if m.get('scope')=='post' and 'label' in m}
                points.append({'collected_at':observation.get('observed_at',''),'metrics':metrics,'source':'页面采集（未向平台校验）','range':{}})
        if cache:
            points.append({'collected_at':cache.get('collected_at',''),'metrics':cache.get('metrics') or {},'source':'X 手动同步缓存','range':{}})
        return sorted(points,key=lambda p:stamp(p['collected_at']))

    @staticmethod
    def _latest(points):
        result = {}
        for key,(label,aliases) in METRICS.items():
            chosen = None
            for point in reversed(points):
                matching = next((alias for alias in aliases if alias in point['metrics']),None)
                if matching:
                    value = valid_number(point['metrics'][matching])
                    chosen = {'label':label,'value':value,'reason':'指标原值无法解析为非负有限数字' if value is None else point['source'], 'collected_at':point['collected_at']}
                    break
            result[key] = chosen or {'label':label,'value':None,'reason':'现有快照与 X 缓存未提供此指标；未知不等于 0','collected_at':''}
        return result

    def _grok_sources(self, rows):
        feedback = read_json(self.workspace / 'today/feedback.json', {})
        feedback = feedback if isinstance(feedback, dict) else {}
        sources = {}
        for row in rows:
            source = row.get('source_item')
            if not source or not isinstance(feedback.get(source['item_id']), dict) or feedback[source['item_id']].get('status') != 'used':
                continue
            identifier = x_post_id(row.get('url')) if row.get('platform') == 'x' else None
            if identifier:
                sources[identifier] = {'url':source['url'], 'title':source['title'], 'item_id':source['item_id'], 'basis':'工作台拿去写与发布记录'}
        return sources

    def listing(self, detail_slug=None):
        data = self._tables()
        cache = self._cache()
        rows = []
        warnings = []
        if not self.store.library.exists():
            folders = []
        else:
            folders = sorted(self.store.library.iterdir())
        for folder in folders:
            if not folder.is_dir() or folder.is_symlink() or not (folder/'article.json').exists():
                continue
            if detail_slug and folder.name != detail_slug:
                continue
            meta = read_json(folder/'article.json',{})
            source = self._source(folder)
            records = read_json(folder/'deliveries.json',[])
            if not isinstance(records,list):
                warnings.append(folder.name+' 发布记录格式无效')
                continue
            for record in records:
                if not isinstance(record,dict) or record.get('kind') not in PUBLISHED:
                    continue
                post_id = re.search(r'/status/(\d+)', record.get('url') or '') if record.get('platform') == 'x' else None
                post = cache.get(post_id[1],{}) if post_id else {}
                published = record.get('published_at') or post.get('created_at') or ''
                registered = record.get('created_at') or ''
                row = {'slug':folder.name, 'delivery_id':record.get('id',''), 'kind':record['kind'],'title':meta.get('title') or folder.name,
                       'platform':record.get('platform','未知'),'url':record.get('url') or '', 'version':record.get('version',''),
                       'published_at':published,'registration_at':registered,'time_basis':'发布时间' if stamp(published) else '登记时间（发布时间未知）',
                       'sort_at':published if stamp(published) else registered, 'source_item':source}
                points = self._points(row,data,post)
                row['metrics'] = self._latest(points)
                if detail_slug:
                    row['snapshots'] = [{**p,'metrics':{str(k): valid_number(v) if valid_number(v) is not None else '未知（原值无效）' for k,v in p['metrics'].items()}} for p in points]
                rows.append(row)
        rows.sort(key=lambda r:(stamp(r['sort_at']),r['slug'],r['delivery_id']),reverse=True)
        cutoff = (datetime.now(timezone.utc)-timedelta(days=7)).timestamp()
        recent = [r for r in rows if cutoff <= stamp(r['sort_at']) <= datetime.now(timezone.utc).timestamp()]
        result = {'items':rows,'total':len(rows),'week_count':len(recent),'week_from_items':sum(bool(r['source_item']) for r in recent),'warnings':warnings,
                  'notice':'发布时间未知时按登记时间排序和计入近 7 天；指标只读本机快照，缺失显示未知。'}
        if detail_slug:
            if not rows:
                raise ValueError('该作品没有已发布记录')
            result['review'] = next((r for r in data['reviews'] if r.get('slug')==detail_slug),{})
            # 既有 reviews 的 slug 在 SQL 列，不在 data 内。
            path = self.workspace / 'publication-feedback/snapshots.db'
            if path.exists():
                with closing(readonly(path)) as c:
                    if c.execute("SELECT 1 FROM sqlite_master WHERE name='reviews'").fetchone():
                        note = c.execute('SELECT data FROM reviews WHERE slug=?',(detail_slug,)).fetchone()
                        result['review'] = read_json_string(note[0]) if note else {}
        if not detail_slug:
            result['grok'] = GrokReports().listing(self._grok_sources(rows))
            from grok_daily_review import snapshot as studio_review
            result['studio_grok_review'] = studio_review(self.workspace)
        return result


def read_json_string(value):
    try:
        return json.loads(value)
    except (ValueError,TypeError):
        return None


def get_route(handler, parts, query):
    if query:
        raise ValueError('发布复盘不接受查询参数')
    prefix = '/api/publish/review'
    suffix = unquote(parts.path[len(prefix):]).strip('/')
    if suffix:
        slug_value(suffix)
    return handler.reply(200, PublishReview(handler.server.store).listing(suffix or None))
