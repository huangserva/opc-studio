"""Account-scoped, offline-first X feedback. Remote reads require an explicit sync."""
import hashlib
import json
import re
from datetime import datetime, timezone, timedelta
from urllib.parse import urlencode, urlsplit
from .x_official import private_write, remote

FIELDS = {'tweet.fields': 'author_id,created_at,conversation_id,referenced_tweets,public_metrics,attachments,note_tweet,article',
          'expansions': 'author_id,referenced_tweets.id,referenced_tweets.id.author_id,attachments.media_keys',
          'user.fields': 'name,username,profile_image_url',
          'media.fields': 'url,preview_image_url,type,alt_text'}


def identifier(value):
    value = str(value or '')
    if not re.fullmatch(r'\d{1,19}', value):
        raise ValueError('请输入有效的 X 帖子 ID')
    return value


def window(payload):
    values = {}
    for name in ('start_time', 'end_time'):
        raw = payload.get(name)
        if not isinstance(raw, str) or len(raw) > 40:
            raise ValueError('请选择有效的时间范围')
        try:
            date = datetime.fromisoformat(raw.replace('Z', '+00:00'))
        except ValueError:
            raise ValueError('时间格式无效') from None
        if date.tzinfo is None:
            raise ValueError('时间需要包含时区')
        values[name] = date.astimezone(timezone.utc)
    if values['start_time'] >= values['end_time'] or values['end_time'] - values['start_time'] > timedelta(days=366):
        raise ValueError('时间范围须在一年以内且开始早于结束')
    if values['start_time'] >= datetime.now(timezone.utc):
        raise ValueError('开始时间不能晚于现在')
    values['end_time'] = min(values['end_time'], datetime.now(timezone.utc) - timedelta(seconds=10))
    if values['start_time'] >= values['end_time']:
        raise ValueError('时间范围过于接近当前时间，请稍后重试')
    return {k: v.isoformat(timespec='seconds').replace('+00:00', 'Z') for k, v in values.items()}


class XFeedback:
    def __init__(self, service):
        self.service = service

    def path(self, account_id):
        return self.service.root / 'feedback' / (identifier(account_id) + '.json')

    def cached(self, account_id):
        path = self.path(account_id)
        return json.loads(path.read_text()) if path.exists() else {'posts': {}, 'comments': {}, 'pages': {}, 'synced_at': None}

    def local(self):
        s = self.service
        account = s.config().get('account')
        cache = self.cached(account['id']) if account else {'posts': {}, 'comments': {}, 'pages': {}, 'synced_at': None}
        posts = dict(cache['posts'])
        store = s.store
        if store:
            from content_store import studio_at
            for binding in store.feedback.bindings()['bindings']:
                if binding['platform'] != 'x':
                    continue
                match = re.search(r'/status/(\d{1,19})(?:$|[/?#])', binding['url'])
                if not match:
                    continue
                key = match[1]
                with store.lock:
                    manifest, _, package = store.info(binding['slug'], binding['version'])
                    target = studio_at(package)['platforms'].get('x', {})
                    text = target.get('caption') or (package / 'body-text.txt').read_text()
                    record = next(r for r in store.delivery_records(binding['slug']) if r['id'] == binding['delivery_id'])
                local = {'id': key, 'text': text, 'title': binding['title'], 'username': binding['account'].lstrip('@'),
                         'created_at': None, 'registered_at': binding['registration_at'], 'source': 'local',
                         'work_url': binding['work_url'], 'version': binding['version'],
                         'group_id': record.get('x_job_id') or key, 'kind': studio_at(package)['type'],
                         'images': [{'url': store.asset_url(binding['slug'], binding['version'], item['file'])}
                                    for item in manifest.get('body_media', [])[:4]], 'url': binding['url']}
                if key in posts:
                    local.update(posts[key])
                    local['work_url'], local['version'] = binding['work_url'], binding['version']
                posts[key] = local
        return {'account': account, 'connected': bool(s.config().get('access_token')), 'posts': list(posts.values()),
                'comments': list(cache['comments'].values()), 'pages': cache['pages'], 'synced_at': cache['synced_at']}

    def normalize(self, result):
        included = result.get('includes', {})
        users = {u['id']: u for u in included.get('users', [])}
        media = {m['media_key']: m for m in included.get('media', [])}
        raw = result.get('data', [])
        if isinstance(raw, dict):
            raw = [raw]
        items = {}
        for tweet in [*included.get('tweets', []), *raw]:
            key = identifier(tweet['id'])
            author = users.get(tweet.get('author_id'), {})
            refs = tweet.get('referenced_tweets', [])
            parent = next((r['id'] for r in refs if r['type'] == 'replied_to'), None)
            items[key] = {'id': key, 'text': tweet.get('note_tweet', {}).get('text') or tweet.get('text', ''),
                'author_id': tweet.get('author_id'), 'username': author.get('username'), 'name': author.get('name'),
                'kind': 'article' if tweet.get('article') else 'note' if tweet.get('note_tweet') else 'post',
                'avatar': author.get('profile_image_url'), 'created_at': tweet.get('created_at'),
                'conversation_id': tweet.get('conversation_id') or key, 'parent_id': parent,
                'metrics': tweet.get('public_metrics', {}), 'source': 'x',
                'images': [{'url': media[k].get('url') or media[k].get('preview_image_url'), 'alt': media[k].get('alt_text', '')}
                           for k in tweet.get('attachments', {}).get('media_keys', []) if k in media]}
        return items, [identifier(t['id']) for t in raw]

    def sync(self, payload):
        s = self.service
        account = s.config().get('account')
        if not account:
            raise ValueError('请先连接 X 账号')
        if str(payload.get('account_id')) != account['id']:
            raise ValueError('只能同步当前授权的 X 账号；其他账号可查看本机记录')
        mode = payload.get('mode')
        if mode not in ('timeline', 'mentions', 'discussion', 'context'):
            raise ValueError('同步类型无效')
        post_kind = payload.get('post_kind', 'original') if mode == 'timeline' else ''
        if post_kind not in ('', 'original', 'replies'):
            raise ValueError('帖子分类无效')
        dates = window(payload)
        post_id = identifier(payload.get('post_id')) if mode in ('discussion', 'context') else ''
        signature_parts = [mode, post_id, payload.get('start_time'), payload.get('end_time')]
        if mode == 'timeline':
            signature_parts.append(post_kind)
        signature = hashlib.sha256(json.dumps(signature_parts).encode()).hexdigest()
        cursor = payload.get('next_token')
        if cursor is not None and (not isinstance(cursor, str) or len(cursor) > 1000):
            raise ValueError('分页参数无效')
        token = s.token()
        params = {**FIELDS, **dates, 'max_results': 20}
        if mode == 'timeline':
            path = f"/2/users/{account['id']}/tweets"
            params['exclude'] = 'replies,retweets' if post_kind == 'original' else 'retweets'
            if cursor: params['pagination_token'] = cursor
        elif mode == 'mentions':
            path = f"/2/users/{account['id']}/mentions"
            if cursor: params['pagination_token'] = cursor
        elif mode == 'discussion':
            recent = datetime.fromisoformat(dates['start_time'].replace('Z', '+00:00')) >= datetime.now(timezone.utc) - timedelta(days=7)
            path = '/2/tweets/search/' + ('recent' if recent else 'all')
            with s.lock:
                selected = self.cached(account['id'])['posts'].get(post_id, {})
            params['query'] = 'conversation_id:' + identifier(selected.get('conversation_id') or post_id)
            if cursor: params['next_token'] = cursor
        else:
            path, params = '/2/tweets/' + post_id, FIELDS.copy()
        result = remote('GET', path + '?' + urlencode(params), token)
        items, primary = self.normalize(result)
        # One bounded lookup supplies missing original posts / reply parents. Never auto-page.
        wanted = set()
        if mode in ('discussion', 'mentions'):
            if post_id: wanted.add(post_id)
            for key in primary:
                wanted.update(v for v in (items[key].get('parent_id'), items[key].get('conversation_id')) if v)
            with s.lock:
                known = self.cached(account['id'])['posts']
            wanted = sorted(wanted - items.keys() - known.keys())[:20]
            if wanted:
                try:
                    extra = remote('GET', '/2/tweets?' + urlencode({**FIELDS, 'ids': ','.join(wanted)}), token)
                    expanded, _ = self.normalize(extra)
                    items.update(expanded)
                except ValueError:
                    result.setdefault('errors', []).append({'title': '部分原帖／被回复内容未能读取'})
        now = datetime.now(timezone.utc).isoformat()
        with s.lock:
            if s.config().get('account', {}).get('id') != account['id']:
                raise ValueError('读取期间授权账号已变化，请重新选择账号')
            cache = self.cached(account['id'])
            for key, item in items.items():
                previous = cache['posts'].get(key, {})
                for field in ('username', 'name', 'avatar'):
                    if not item.get(field) and previous.get(field):
                        item[field] = previous[field]
            cache['posts'].update(items)
            if mode in ('mentions', 'discussion'):
                cache['comments'].update({key: items[key] for key in primary if key != post_id})
            matches = [(key, page) for key, page in cache['pages'].items()
                       if page.get('mode') == mode and page.get('post_id', '') == post_id
                       and page.get('start_time') == payload['start_time'] and page.get('end_time') == payload['end_time']
                       and (mode != 'timeline' or page.get('post_kind') == post_kind)]
            old = max((page for _, page in matches), key=lambda page: page.get('synced_at') or '', default={})
            # Collapse duplicate legacy signatures into the canonical key on sync.
            for key, _ in matches:
                cache['pages'].pop(key)

            ids = list(dict.fromkeys((old.get('ids', []) if cursor else []) + primary))
            cache['pages'][signature] = {'ids': ids, 'next_token': result.get('meta', {}).get('next_token'),
                'mode': mode, 'post_id': post_id, 'post_kind': post_kind, 'start_time': payload['start_time'], 'end_time': payload['end_time'],
                'synced_at': now, 'partial': bool(result.get('errors'))}
            cache['synced_at'] = now
            private_write(self.path(account['id']), cache)
        return {'page': cache['pages'][signature], 'result_count': len(primary), 'partial': bool(result.get('errors'))}


def image_response(handler, query):
    """Serve only public X CDN images through same origin; never send OAuth tokens."""
    from urllib.request import build_opener, HTTPRedirectHandler, Request
    from urllib.error import URLError, HTTPError
    value = query.get('url', [''])[0]
    parsed = urlsplit(value)
    if (parsed.scheme != 'https' or parsed.hostname != 'pbs.twimg.com' or parsed.port not in (None,443)
            or parsed.username is not None or parsed.password is not None or parsed.fragment
            or not re.match(r'^/(media|profile_images|profile_banners)/', parsed.path)):
        raise ValueError('只允许读取 X 官方图片地址')
    class NoRedirect(HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None
    try:
        with build_opener(NoRedirect()).open(Request(value, headers={'Accept':'image/*','User-Agent':'Mozilla/5.0','Referer':'https://x.com/'}), timeout=15) as response:
            kind = response.headers.get_content_type()
            if kind not in ('image/jpeg','image/png','image/webp','image/gif'):
                raise ValueError('X 图片格式不可用')
            data = response.read(10*1024*1024+1)
            if len(data)>10*1024*1024:
                raise ValueError('X 图片超过10 MB')
    except (URLError, HTTPError, TimeoutError, OSError):
        raise ValueError('X 图片暂时无法读取') from None
    handler.send_response(200)
    handler.send_header('Content-Type',kind)
    handler.send_header('Content-Length',str(len(data)))
    handler.send_header('Cache-Control','private, max-age=86400')
    handler.send_header('X-Content-Type-Options','nosniff')
    handler.end_headers()
    handler.wfile.write(data)
