"""发布复盘聚合：只读文件与 SQLite，不初始化旧存储、不发远端请求。"""
from __future__ import annotations
import json
import math
import re
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote, unquote

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
