"""Direct X API integration. Credentials stay outside the served workspace.

No remote writes occur until a reviewed operation is explicitly submitted.
Uncertain writes are never retried automatically.
"""
import base64
import hashlib
import json
import os
import re
import secrets
import tempfile
import threading
import time
import uuid
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

CONFIG = Path.home() / 'Library/Application Support/ArticleStudio/x-official.json'
SCOPES = 'tweet.read tweet.write users.read media.write offline.access'


class XError(ValueError):
    def __init__(self, message, uncertain=False):
        super().__init__(message)
        self.uncertain = uncertain


def private_write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(value, stream, ensure_ascii=False)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def remote(method, path, token='', payload=None, form=None, basic=None):
    headers = {'Accept': 'application/json'}
    if token:
        headers['Authorization'] = 'Bearer ' + token
    if basic:
        headers['Authorization'] = 'Basic ' + base64.b64encode(basic.encode()).decode()
    body = None
    if payload is not None:
        body = json.dumps(payload).encode()
        headers['Content-Type'] = 'application/json'
    if form is not None:
        body = urlencode(form).encode()
        headers['Content-Type'] = 'application/x-www-form-urlencoded'
    try:
        with urlopen(Request('https://api.x.com' + path, body, headers, method=method), timeout=45) as response:
            raw = response.read()
            return json.loads(raw) if raw else {}
    except HTTPError as exc:
        # API error text can contain echoed credentials or content. Expose status only.
        labels = {400: '参数或内容不符合 X 要求', 401: '授权已失效，请重新连接',
                  403: '账户权限或回复条件不满足', 402: 'API 余额或额度不足',
                  429: '请求过快或额度已达上限'}
        raise XError('X API：' + labels.get(exc.code, '服务响应异常') + f'（HTTP {exc.code}）',
                     uncertain=method != 'GET' and exc.code >= 500) from None
    except (URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        cause = exc.reason if isinstance(exc, URLError) else exc
        reason = '请求超时' if isinstance(cause, TimeoutError) else '返回数据无法解析' if isinstance(exc, json.JSONDecodeError) else '网络或 TLS 连接中断'
        raise XError('X API ' + reason + '；写入结果可能未确认，请核对 X 后再操作',
                     uncertain=method != 'GET') from None


class XOfficial:
    def __init__(self, workspace, config_path=None, store=None):
        self.store = store
        self.root = Path(workspace) / 'x-official'
        self.root.mkdir(parents=True, exist_ok=True)
        self.config_path = Path(config_path or CONFIG)
        self.lock = threading.RLock()
        self.pending = {}
        self.jobs = {}
        self.reviews = {}
        for path in self.root.glob('job-*.json'):
            job = json.loads(path.read_text())
            if job['status'] in ('queued', 'running'):
                job.update(status='unknown', message='服务曾重启，结果待核实；不会自动重发')
                private_write(path, job)
            self.jobs[job['id']] = job

    def config(self):
        return json.loads(self.config_path.read_text()) if self.config_path.exists() else {}

    def status(self):
        c = self.config()
        return {'configured': bool(c.get('client_id')), 'connected': bool(c.get('access_token')),
                'account': c.get('account'), 'has_client_secret': bool(c.get('client_secret')),
                'client_id': c.get('client_id', ''), 'scopes': c.get('scope', ''),
                'jobs': sorted(self.jobs.values(), key=lambda j: j['created_at'], reverse=True)[:50]}

    def configure(self, payload):
        with self.lock:
            if any(j['status'] in ('queued', 'running') for j in self.jobs.values()):
                raise ValueError('发布期间不能更换授权应用')
            client = str(payload.get('client_id', '')).strip()
            secret = str(payload.get('client_secret', '')).strip()
            if not client or len(client) > 500 or len(secret) > 1000:
                raise ValueError('请填写 OAuth 2.0 Client ID')
            c = self.config()
            if c.get('client_id') == client and not secret and not payload.get('public_client'):
                secret = c.get('client_secret', '')
            private_write(self.config_path, {'client_id': client, 'client_secret': secret})
            self.pending.clear()
            self.reviews.clear()
            return self.status()

    def authorize(self, callback):
        with self.lock:
            c = self.config()
            if not c.get('client_id'):
                raise ValueError('请先配置 OAuth 2.0 应用')
            state, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(64)
            self.pending = {state: (verifier, callback, time.time())}
            challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')
            return {'url': 'https://x.com/i/oauth2/authorize?' + urlencode({
                'response_type': 'code', 'client_id': c['client_id'], 'redirect_uri': callback,
                'scope': SCOPES, 'state': state, 'code_challenge': challenge,
                'code_challenge_method': 'S256'})}

    def token_exchange(self, c, form):
        basic = c['client_id'] + ':' + c['client_secret'] if c.get('client_secret') else None
        if not basic:
            form['client_id'] = c['client_id']
        result = remote('POST', '/2/oauth2/token', form=form, basic=basic)
        if not result.get('access_token'):
            raise ValueError('X 未返回用户授权，请重新连接')
        c.update({key: result[key] for key in ('access_token', 'refresh_token', 'scope') if key in result})
        c['expires_at'] = time.time() + result.get('expires_in', 7200)
        return c

    def callback(self, query):
        with self.lock:
            if any(j['status'] in ('queued', 'running') for j in self.jobs.values()):
                raise ValueError('发布期间不能更换授权账号，请稍后重新连接')
            entry = self.pending.pop(query.get('state', [''])[0], None)
            if not entry or time.time() - entry[2] > 600:
                raise ValueError('授权请求过期或校验失败，请重新连接')
            if query.get('error') or not query.get('code'):
                raise ValueError('X 授权未完成，请重新连接')
            c = self.token_exchange(self.config(), {'grant_type': 'authorization_code',
                'code': query['code'][0], 'code_verifier': entry[0], 'redirect_uri': entry[1]})
            account = remote('GET', '/2/users/me', c['access_token']).get('data', {})
            if not account.get('id') or not account.get('username'):
                raise ValueError('无法确认授权账号')
            c['account'] = account
            private_write(self.config_path, c)
            self.reviews.clear()
            return self.status()

    def token(self):
        with self.lock:
            c = self.config()
            if not c.get('access_token') or not c.get('account'):
                raise ValueError('请先连接 X 账号')
            if time.time() >= c.get('expires_at', 0) - 60:
                if not c.get('refresh_token'):
                    raise ValueError('授权已过期，请重新连接')
                c = self.token_exchange(c, {'grant_type': 'refresh_token', 'refresh_token': c['refresh_token']})
                private_write(self.config_path, c)
            return c['access_token']

    def upload_local(self, stream, length, name):
        # Files stay local until a reviewed submission. Existing workspace files are never overwritten.
        suffix = Path(name).suffix.lower()
        allowed = {'.jpg', '.jpeg', '.png', '.webp', '.gif', '.mp4', '.mov'}
        maximum = 1024**3 if suffix in ('.mp4', '.mov') else 20 * 1024**2
        if suffix not in allowed or not 0 < length <= maximum:
            raise ValueError('图片最多 20 MB，视频最多 1 GB；请选择 JPG/PNG/WEBP/GIF/MP4/MOV')
        key = uuid.uuid4().hex + suffix
        folder = self.root / 'media'
        folder.mkdir(exist_ok=True)
        path = folder / key
        try:
            with path.open('xb') as output:
                remaining = length
                while remaining:
                    chunk = stream.read(min(1024**2, remaining))
                    if not chunk:
                        raise ValueError('文件上传未完成')
                    output.write(chunk)
                    remaining -= len(chunk)
            if suffix not in ('.mp4', '.mov'):
                from PIL import Image, ImageOps
                with Image.open(path) as source:
                    expected = {'.jpg': 'JPEG', '.jpeg': 'JPEG', '.png': 'PNG', '.webp': 'WEBP', '.gif': 'GIF'}
                    if source.format != expected[suffix]:
                        raise ValueError('图片格式与扩展名不符，请重新导出图片')
                    source.verify()
                if suffix == '.gif':
                    if length > 15 * 1024**2:
                        raise ValueError('X 动图最大 15 MB')
                elif length > 5 * 1024**2:
                    with Image.open(path) as source:
                        image = ImageOps.exif_transpose(source).convert('RGB')
                        for quality in (90, 80, 70, 60):
                            image.thumbnail((4096, 4096))
                            converted = folder / (uuid.uuid4().hex + '.jpg')
                            image.save(converted, quality=quality)
                            if converted.stat().st_size <= 5 * 1024**2:
                                path.unlink()
                                path, key = converted, converted.name
                                break
                            converted.unlink()
                        else:
                            raise ValueError('图片压缩后仍超过 X 的 5 MB 上限')
            return {'id': key, 'name': Path(name).name, 'size': path.stat().st_size,
                    'compressed': key.endswith('.jpg') and suffix not in ('.jpg', '.jpeg') or path.stat().st_size != length}
        except Exception as exc:
            path.unlink(missing_ok=True)
            if isinstance(exc, (ValueError, OSError)):
                raise
            raise ValueError('图片文件损坏或格式不受支持，请重新导出') from None

    def media_path(self, key):
        if not isinstance(key, str) or not re.fullmatch(r'[a-f0-9]{32}\.(jpg|jpeg|png|webp|gif|mp4|mov)', key):
            raise ValueError('附件标识无效')
        path = self.root / 'media' / key
        if not path.is_file():
            raise ValueError('附件不存在，请重新上传')
        return path

    def review(self, payload):
        self.token()
        mode = payload.get('mode')
        if mode not in ('post', 'thread', 'reply', 'article_draft', 'article_publish'):
            raise ValueError('发布类型无效')
        parts = payload.get('parts', [])
        if not isinstance(parts, list) or len(parts) > 25 or any(not isinstance(p, str) or len(p) > 25000 for p in parts):
            raise ValueError('正文无效；串帖最多 25 条')
        parts = [p.strip() for p in parts]
        if mode not in ('article_publish',) and (not parts or any(not p for p in parts)):
            raise ValueError('请填写完整正文')
        if mode != 'thread' and len(parts) > 1:
            raise ValueError('此类型只接受一篇正文')
        identifier = str(payload.get('reply_to') if mode == 'reply' else payload.get('article_id', ''))
        if mode in ('reply', 'article_publish') and not re.fullmatch(r'\d{1,19}', identifier):
            raise ValueError('请输入有效的 X 帖子／文章 ID')
        title = str(payload.get('title', '')).strip()
        if mode == 'article_draft' and not 0 < len(title) <= 200:
            raise ValueError('请填写文章标题（最多 200 字）')
        media = payload.get('media', [])
        if (not isinstance(media, list) or len(media) > 4 or
                any(not isinstance(m, str) for m in media) or len(set(media)) != len(media)):
            raise ValueError('最多选择 4 个不同附件')
        paths = [self.media_path(m) for m in media]
        if any(p.suffix in ('.gif', '.mp4', '.mov') for p in paths) and len(paths) != 1:
            raise ValueError('动图／视频不能与其他附件混发')
        if mode == 'article_publish' and media:
            raise ValueError('发布草稿不接受额外附件')
        if mode == 'article_draft' and (len(media) > 1 or any(p.suffix in ('.gif', '.mp4', '.mov') for p in paths)):
            raise ValueError('当前文章 API 入口支持纯文本正文和一张封面；图文排版请继续使用现有文章流程')
        account = self.config()['account']
        source = payload.get('source')
        if source:
            if not self.store or not isinstance(source, dict):
                raise ValueError('作品来源无效')
            from content_store import studio_at
            with self.store.lock:
                _, _, package = self.store.info(source.get('slug'), source.get('version'))
                target = studio_at(package)['platforms'].get('x', {})
            target_account = str(target.get('account', '')).lstrip('@')
            if target_account and target_account.lower() != account['username'].lower():
                raise ValueError('作品目标账号与授权账号不一致')
            source = {'slug': source['slug'], 'version': source['version']}
        item = {'mode': mode, 'parts': parts, 'title': title, 'identifier': identifier,
                'media': media, 'account': account, 'source': source, 'created_at': time.time()}
        item['fingerprint'] = hashlib.sha256(json.dumps({
            'mode': mode, 'parts': parts, 'title': title, 'identifier': identifier,
            'media_hashes': [self.file_hash(p) for p in paths],
            'account_id': account['id']}, sort_keys=True).encode()).hexdigest()
        key = uuid.uuid4().hex
        with self.lock:
            self.reviews = {k: v for k, v in self.reviews.items() if time.time() - v['created_at'] < 900}
            self.reviews[key] = item
        return {'review_id': key, **item,
                'note': '文章仅保存纯文本段落和封面。' if mode == 'article_draft' else
                        '附件只用于第一条帖子。X 会校验字数、账户权限与回复条件。'}

    @staticmethod
    def file_hash(path):
        digest = hashlib.sha256()
        with path.open('rb') as stream:
            while True:
                chunk = stream.read(1024**2)
                if not chunk:
                    break
                digest.update(chunk)
        return digest.hexdigest()

    def save_job(self, job):
        private_write(self.root / ('job-' + job['id'] + '.json'), job)

    def submit(self, payload):
        with self.lock:
            key = payload.get('review_id')
            for job in self.jobs.values():
                if job['review_id'] == key:
                    return job
            review = self.reviews.get(key)
            if not review or time.time() - review['created_at'] > 900:
                raise ValueError('确认内容已过期，请重新预览')
            if payload.get('confirm_account') != review['account']['username']:
                raise ValueError('目标账号确认不一致')
            if self.config().get('account', {}).get('id') != review['account']['id']:
                raise ValueError('授权账号已变化，请重新预览')
            for previous in self.jobs.values():
                if previous.get('fingerprint') == review['fingerprint'] and previous['status'] != 'failed':
                    raise ValueError('相同内容已有提交记录，请核对任务与 X 链接；不会重复提交')
            if any(j['status'] in ('queued', 'running') for j in self.jobs.values()):
                raise ValueError('已有发布正在执行，请等待结果')
            job = {'id': uuid.uuid4().hex, 'review_id': key, 'status': 'queued',
                   'fingerprint': review['fingerprint'],
                   'mode': review['mode'], 'account': review['account']['username'], 'source': review.get('source'),
                   'title': review['title'] or (review['parts'][0][:120] if review['parts'] else '发布文章草稿'),
                   'part_count': len(review['parts']), 'target_id': review['identifier'],
                   'created_at': time.time(), 'message': '任务已记录，准备提交', 'posts': []}
            self.jobs[job['id']] = job
            self.save_job(job)
            threading.Thread(target=self.run, args=(job, review), daemon=True).start()
            return dict(job)

    def confirm_not_sent(self, payload):
        """User reconciliation only: never performs an X request or submits a retry."""
        with self.lock:
            job = self.jobs.get(payload.get('job_id'))
            account = self.config().get('account', {})
            if not job or job.get('mode') != 'reply' or job.get('status') != 'unknown' or job.get('posts'):
                raise ValueError('只有结果未知且未返回链接的回复任务可核实')
            if payload.get('confirm_not_sent') is not True or payload.get('confirm_account') != job.get('account') or account.get('username') != job.get('account'):
                raise ValueError('请核实原评论下未发送，并确认当前账号')
            job.update(status='failed', previous_status='unknown', user_confirmed_not_sent_at=time.time(),
                       message='用户已在 X 核实未发送；允许重新核对后提交', retry_note='此操作未发送回复')
            self.save_job(job)
            return dict(job)

    def upload_x(self, path, token, job):
        import mimetypes
        mime = mimetypes.guess_type(path.name)[0] or 'application/octet-stream'
        category = 'tweet_video' if path.suffix in ('.mp4', '.mov') else 'tweet_gif' if path.suffix == '.gif' else 'tweet_image'
        result = remote('POST', '/2/media/upload/initialize', token, {
            'total_bytes': path.stat().st_size, 'media_type': mime, 'media_category': category})
        media_id = str(result.get('data', {}).get('id', ''))
        if not re.fullmatch(r'\d{1,19}', media_id):
            raise XError('X 未返回有效媒体 ID')
        with path.open('rb') as source:
            index = 0
            while True:
                chunk = source.read(4 * 1024**2)
                if not chunk:
                    break
                remote('POST', f'/2/media/upload/{media_id}/append', token,
                       {'segment_index': index, 'media': base64.b64encode(chunk).decode()})
                index += 1
                job['message'] = f'上传附件中：已上传 {min(index * 4 * 1024**2, path.stat().st_size) / 1024**2:.1f} MB'
                self.save_job(job)
        result = remote('POST', f'/2/media/upload/{media_id}/finalize', token)
        deadline = time.time() + 900
        while True:
            processing = result.get('data', {}).get('processing_info', {})
            if not processing or processing.get('state') == 'succeeded':
                return media_id
            if processing.get('state') == 'failed':
                raise XError('X 媒体处理失败，请检查视频格式')
            if time.time() > deadline:
                raise XError('X 媒体处理超时；尚未提交帖子')
            job['message'] = 'X 正在处理附件，请等待'
            self.save_job(job)
            time.sleep(min(15, max(1, processing.get('check_after_secs', 3))))
            result = remote('GET', '/2/media/upload?' + urlencode({'media_id': media_id}), token)

    def run(self, job, review):
        writing_post = False
        try:
            job.update(status='running', message='准备上传并提交到 X')
            self.save_job(job)
            token = self.token()
            media = [self.upload_x(self.media_path(key), token, job) for key in review['media']]
            mode = review['mode']
            if mode == 'article_draft':
                body = {'title': review['title'], 'content_state': {'blocks': [
                    {'key': uuid.uuid4().hex[:5], 'text': paragraph, 'type': 'unstyled',
                     'inline_style_ranges': [], 'entity_ranges': []}
                    for paragraph in review['parts'][0].split('\n\n')], 'entities': []}}
                if media:
                    body['cover_media'] = {'media_category': 'tweet_image', 'media_id': media[0]}
                writing_post = True
                result = remote('POST', '/2/articles/draft', token, body)
                article_id = str(result.get('data', {}).get('id', ''))
                if not re.fullmatch(r'\d{1,19}', article_id):
                    raise XError('文章草稿结果未确认，请在 X 核对', True)
                job.update(status='draft', article_id=article_id, message='文章草稿已创建，尚未公开发布')
            elif mode == 'article_publish':
                writing_post = True
                result = remote('POST', f"/2/articles/{review['identifier']}/publish", token)
                self.record_post(job, result.get('data', {}).get('post_id'))
                job.update(status='published', message='文章已发布')
            else:
                parent = review['identifier'] if mode == 'reply' else None
                for index, text in enumerate(review['parts']):
                    body = {'text': text}
                    if parent:
                        body['reply'] = {'in_reply_to_tweet_id': parent}
                    if index == 0 and media:
                        body['media'] = {'media_ids': media}
                    job['message'] = f"正在提交第 {index + 1} / {len(review['parts'])} 条"
                    self.save_job(job)
                    writing_post = True
                    result = remote('POST', '/2/tweets', token, body)
                    parent = self.record_post(job, result.get('data', {}).get('id'))
                    writing_post = False
                job.update(status='published', message='已发布到 X')
        except Exception as exc:
            uncertain = writing_post and (not isinstance(exc, XError) or exc.uncertain)
            job.update(status='unknown' if uncertain else 'partial' if job['posts'] else 'failed',
                       message=str(exc) if isinstance(exc, ValueError) else '任务异常，请核对已有发布记录',
                       retry_note='不会自动重发；请先核对已有链接及 X 页面')
        finally:
            self.save_job(job)
            self.sync_deliveries(job)

    def sync_deliveries(self, job):
        source = job.get('source')
        if not self.store or not source or not job.get('posts'):
            return
        from datetime import datetime, timezone
        from publishing.core.common import write_json
        try:
            with self.store.lock:
                self.store.info(source['slug'], source['version'])
                records = self.store.delivery_records(source['slug'])
                for post in job['posts']:
                    identifier = uuid.uuid5(uuid.NAMESPACE_URL, 'x-official:' + job['id'] + ':' + post['id']).hex
                    if any(r.get('id') == identifier for r in records):
                        continue
                    records.append({'id': identifier, 'kind': 'published_x_api', 'platform': 'x',
                        'version': source['version'], 'url': 'https://x.com/' + job['account'] + '/status/' + post['id'],
                        'account': job['account'], 'created_at': datetime.now(timezone.utc).isoformat(),
                        'x_job_id': job['id'], 'message': 'X 官方接口已确认此条发布；任务状态：' + job['status']})
                write_json(self.store.article(source['slug']) / 'deliveries.json', records)
        except Exception:
            job['binding_error'] = 'X 发布已返回链接，但作品记录同步失败，请保留链接并手动登记；不要重复发布'
            self.save_job(job)

    def record_post(self, job, identifier):
        identifier = str(identifier or '')
        if not re.fullmatch(r'\d{1,19}', identifier):
            raise XError('X 提交结果未确认，请先核对账号主页', True)
        job['posts'].append({'id': identifier, 'url': 'https://x.com/i/status/' + identifier})
        self.save_job(job)
        return identifier

    def comments(self, payload):
        token = self.token()
        account = self.config()['account']
        mode = payload.get('mode', 'mentions')
        params = {'max_results': 20, 'tweet.fields': 'author_id,created_at,conversation_id,referenced_tweets'}
        if mode == 'mentions':
            path = f"/2/users/{account['id']}/mentions"
            if payload.get('next_token'):
                params['pagination_token'] = str(payload['next_token'])[:1000]
        elif mode in ('recent', 'archive'):
            identifier = str(payload.get('post_id', ''))
            if not re.fullmatch(r'\d{1,19}', identifier):
                raise ValueError('请输入原帖的 X 帖子 ID')
            params['query'] = 'conversation_id:' + identifier
            path = '/2/tweets/search/' + ('all' if mode == 'archive' else 'recent')
            if payload.get('next_token'):
                params['next_token'] = str(payload['next_token'])[:1000]
        else:
            raise ValueError('读取方式无效')
        return remote('GET', path + '?' + urlencode(params), token)


_services = {}
_services_lock = threading.Lock()


def service(server):
    with _services_lock:
        key = id(server)
        if key not in _services:
            _services[key] = XOfficial(server.store.workspace, store=server.store)
        return _services[key]


def get_route(handler, parts, query):
    s = service(handler.server)
    if parts.path == '/api/x-official/image':
        from .x_feedback import image_response
        return image_response(handler, query)
    if parts.path == '/api/x-official/feedback':
        from .x_feedback import XFeedback
        return handler.reply(200, XFeedback(s).local())
    if parts.path == '/api/x-official/status':
        return handler.reply(200, s.status())
    if parts.path == '/api/x-official/callback':
        try:
            s.callback(query)
            message = 'X 授权成功。返回工作台后即可核对账号并发布。'
        except ValueError as exc:
            message = str(exc)
        import html
        return handler.reply(200, ('<!doctype html><meta charset="utf-8"><h1>' + html.escape(message)
            + '</h1><a href="/feedback.html">返回发布反馈</a>').encode(), 'text/html; charset=utf-8')
    if parts.path == '/api/x-official/source':
        store = handler.server.store
        slug, version = query.get('slug', [''])[0], query.get('version', [''])[0]
        from content_store import studio_at
        with store.lock:
            manifest, _, package = store.info(slug, version)
            studio = studio_at(package)
            target = studio['platforms'].get('x', {})
            text = (package / 'body-text.txt').read_text()
            kind = studio['type']
            if kind != 'article':
                if target.get('caption'):
                    text = target['caption']
                elif kind == 'post':
                    pieces = [b.get('display_text', b.get('text', '')) for b in manifest['blocks'] if b['kind'] == 'text']
                    if len(pieces) > 1:
                        text, kind = '\n\n---\n\n'.join(pieces), 'thread'
            items = []
            if kind == 'video':
                video = target.get('video') or studio.get('video')
                if video:
                    items = [video]
            elif kind == 'article':
                if manifest.get('cover'):
                    items = [manifest['cover']]
            elif kind != 'thread':
                items = manifest.get('body_media', [])
            attachments = [{'name': i.get('name') or Path(i['file']).name,
                            'url': store.asset_url(slug, version, i['file'])} for i in items if isinstance(i, dict) and i.get('file')]
            return handler.reply(200, {'title': target.get('title') or manifest['title'],
                'text': text,
                'account': target.get('account', ''), 'kind': kind, 'attachments': attachments,
                'note': '载入已保存的文字和可用素材；串帖配图需重新选择且只用于第一条，文章图文排版继续使用原有流程。'})
    raise ValueError('未知 X 接口')


def post_route(handler, path, payload):
    if not isinstance(payload, dict):
        raise ValueError('请求内容无效')
    s = service(handler.server)
    action = path.rsplit('/', 1)[-1]
    if action == 'configure':
        result = s.configure(payload)
    elif action == 'authorize':
        result = s.authorize(f'http://127.0.0.1:{handler.server.server_port}/api/x-official/callback')
    elif action == 'review':
        result = s.review(payload)
    elif action == 'confirm-not-sent':
        result = s.confirm_not_sent(payload)
    elif action == 'submit':
        result = s.submit(payload)
    elif action == 'feedback-sync':
        from .x_feedback import XFeedback
        result = XFeedback(s).sync(payload)
    elif action == 'comments':
        result = s.comments(payload)
    else:
        raise ValueError('未知 X 接口')
    return handler.reply(202 if action == 'submit' else 200, result)
