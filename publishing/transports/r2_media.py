"""Cloudflare R2 (S3-compatible) uploads using only the Python standard library.

SigV4 signing, idempotent PUT, HEAD size check and an unauthenticated public reachability
check. Credentials are read from a private config file and never leave this module: they
are not returned, logged, embedded in a delivery record, or repeated in an error message.

Every request names itself with one fixed, honest User-Agent (`USER_AGENT`): the public
r2.dev endpoint answers the default `Python-urllib/x.y` agent with HTTP 403, so the check
below would fail on objects that are genuinely public. The agent is attached at transport
level only; it is deliberately not listed in the SigV4 SignedHeaders.
"""
import hashlib
import hmac
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote, urlsplit
from urllib.request import Request, urlopen

DEFAULT_CONFIG_PATH = '~/Library/Application Support/ArticleStudio/r2-media.json'
DEFAULT_PREFIX = 'article-studio/x'
REGION = 'auto'
SERVICE = 's3'
MAX_IMAGE_BYTES = 5 * 1024 * 1024
MAX_IMAGES_PER_POST = 4
# One fixed, self-naming client identity for every request this module makes.
USER_AGENT = 'ArticleStudio/1.0 (+r2-media)'
CONTENT_TYPES = {'jpg': 'image/jpeg', 'jpeg': 'image/jpeg', 'png': 'image/png',
                 'webp': 'image/webp', 'gif': 'image/gif'}
CONFIG_KEYS = ('account_id', 'bucket', 'access_key_id', 'secret_access_key', 'public_base_url')
NAME_SEGMENT = re.compile(r'[A-Za-z0-9._-]{1,120}')
LOOPBACK = ('127.0.0.1', '::1', 'localhost')

_SIGNATURE_NOISE = (
    re.compile(r'(?i)AWS4-HMAC-SHA256[^\r\n]*'),
    re.compile(r'(?i)\b(authorization|x-amz-credential|x-amz-signature|x-amz-date|'
               r'x-amz-content-sha256|x-amz-security-token|access[-_ ]?key[-_ ]?id|'
               r'secret[-_ ]?access[-_ ]?key|signature|credential|token|password)\b'
               r'(["\']?\s*[:=]\s*)[^\s,;}"\']+'),
    re.compile(r'\bAKIA[A-Z0-9]{16}\b'),
    re.compile(r'\b(?:[a-f0-9]{40,}|[A-Za-z0-9+/_]{32,}={0,2})\b'),
)


def redact(value, config=None):
    """Truncate and strip anything credential-shaped, including the configured key id."""
    text = str(value if value is not None else '')
    for pattern in _SIGNATURE_NOISE:
        text = pattern.sub(lambda m: (m.group(1) + m.group(2) + '‹已隐藏›')
                           if m.groups() and m.group(1) else '‹已隐藏›', text)
    if config is not None:
        for secret in (getattr(config, 'access_key_id', ''), getattr(config, 'secret_access_key', '')):
            if secret:
                text = text.replace(secret, '‹已隐藏›')
    if len(text) > 400:
        text = text[:400] + '…（已截断）'
    return ' '.join(text.split())


def config_path():
    return Path(os.environ.get('ARTICLE_STUDIO_R2_CONFIG')
                or os.path.expanduser(DEFAULT_CONFIG_PATH)).expanduser()


class R2Config:
    """Validated view of the operator-provided config. Never printed, never persisted."""

    def __init__(self, data, path=''):
        missing = [key for key in CONFIG_KEYS if not str(data.get(key) or '').strip()]
        if missing:
            raise ValueError('图床配置缺少字段：' + '、'.join(missing) + '（配置文件：' + str(path) + '）')
        self.path = str(path)
        self.account_id = str(data['account_id']).strip()
        self.bucket = str(data['bucket']).strip()
        self.access_key_id = str(data['access_key_id']).strip()
        self.secret_access_key = str(data['secret_access_key'])
        self.prefix = str(data.get('prefix') or DEFAULT_PREFIX).strip('/')
        if not NAME_SEGMENT.fullmatch(self.account_id) or not NAME_SEGMENT.fullmatch(self.bucket):
            raise ValueError('图床配置的 account_id 或 bucket 名称无效')
        for segment in self.prefix.split('/'):
            if segment and not NAME_SEGMENT.fullmatch(segment):
                raise ValueError('图床配置的 prefix 只能包含字母、数字、点、下划线和连字符')
        self.public_base_url = str(data['public_base_url']).strip().rstrip('/')
        public = urlsplit(self.public_base_url)
        if public.scheme != 'https' or not public.netloc or public.username or public.password \
                or public.path not in ('', '/'):
            raise ValueError('图床配置的 public_base_url 必须是不带路径与凭据的 HTTPS 地址')
        self.endpoint = str(data.get('endpoint') or
                            'https://' + self.account_id + '.r2.cloudflarestorage.com').strip().rstrip('/')
        endpoint = urlsplit(self.endpoint)
        allowed = endpoint.scheme == 'https' or (endpoint.scheme == 'http' and endpoint.hostname in LOOPBACK)
        if not allowed or not endpoint.netloc or endpoint.path not in ('', '/'):
            raise ValueError('图床 endpoint 必须是 HTTPS；只有本机测试才允许 http 回环地址')


def load_config(path=None):
    """Return the config, or None when the operator has not configured an image host yet."""
    target = Path(path).expanduser() if path else config_path()
    if not target.is_file():
        return None
    mode = target.stat().st_mode & 0o777
    if mode & 0o077:
        raise ValueError('图床配置文件权限过宽（应为 600），已停止使用：' + str(target))
    try:
        data = json.loads(target.read_text(encoding='utf-8'))
    except ValueError:
        raise ValueError('图床配置文件不是有效 JSON：' + str(target)) from None
    if not isinstance(data, dict):
        raise ValueError('图床配置文件格式无效：' + str(target))
    return R2Config(data, target)


def extension_of(filename):
    return str(filename).rsplit('.', 1)[-1].lower() if '.' in str(filename) else ''


def content_type_for(filename):
    return CONTENT_TYPES.get(extension_of(filename))


def object_key(prefix, slug, version, digest, filename):
    extension = extension_of(filename)
    if extension not in CONTENT_TYPES:
        raise ValueError('不支持的图片格式：' + str(filename))
    for part in (slug, version):
        if not NAME_SEGMENT.fullmatch(str(part)):
            raise ValueError('对象键中的作品标识无效')
    if not re.fullmatch(r'[a-f0-9]{64}', str(digest)):
        raise ValueError('对象键需要文件的 sha256')
    return '/'.join(segment for segment in (prefix, slug, version, digest + '.' + extension) if segment)


def _canonical_uri(path):
    segments = str(path or '/').split('/')
    return '/'.join(quote(segment, safe='!$&\'()*+,;=:@-._~') for segment in segments) or '/'


def _canonical_query(query):
    pairs = []
    for item in str(query or '').split('&'):
        if not item:
            continue
        name, _, value = item.partition('=')
        pairs.append((quote(name, safe=''), quote(value, safe='')))
    pairs.sort()
    return '&'.join(name + '=' + value for name, value in pairs)


def signature_headers(method, url, payload, config, when=None):
    """AWS SigV4 headers for one request. `when` is injectable so tests stay deterministic."""
    moment = when or datetime.now(timezone.utc)
    parts = urlsplit(url)
    body = payload if payload is not None else b''
    amz_date = moment.strftime('%Y%m%dT%H%M%SZ')
    date_stamp = moment.strftime('%Y%m%d')
    body_hash = hashlib.sha256(body).hexdigest()
    headers = {'host': parts.netloc, 'x-amz-content-sha256': body_hash, 'x-amz-date': amz_date}
    signed = ';'.join(sorted(headers))
    canonical_headers = ''.join(key + ':' + headers[key].strip() + '\n' for key in sorted(headers))
    canonical = '\n'.join([method.upper(), _canonical_uri(parts.path or '/'), _canonical_query(parts.query),
                           canonical_headers, signed, body_hash])
    scope = '/'.join([date_stamp, REGION, SERVICE, 'aws4_request'])
    string_to_sign = '\n'.join(['AWS4-HMAC-SHA256', amz_date, scope,
                                hashlib.sha256(canonical.encode('utf-8')).hexdigest()])

    def step(key, message):
        return hmac.new(key, message.encode('utf-8'), hashlib.sha256).digest()

    signing = step(step(step(step(('AWS4' + config.secret_access_key).encode('utf-8'), date_stamp),
                            REGION), SERVICE), 'aws4_request')
    signature = hmac.new(signing, string_to_sign.encode('utf-8'), hashlib.sha256).hexdigest()
    return {'Authorization': ('AWS4-HMAC-SHA256 Credential=' + config.access_key_id + '/' + scope +
                              ', SignedHeaders=' + signed + ', Signature=' + signature),
            'X-Amz-Date': amz_date,
            'X-Amz-Content-Sha256': body_hash}


def object_url(config, key):
    return config.endpoint + '/' + config.bucket + '/' + quote(key, safe='/')


def _open(url, method, headers, body=None, timeout=60):
    request = Request(url, data=body, method=method.upper())
    for name, value in headers.items():
        request.add_header(name, value)
    if not any(str(name).lower() == 'user-agent' for name in headers):
        # urllib would otherwise send its default agent, which the public CDN refuses.
        request.add_header('User-Agent', USER_AGENT)
    return urlopen(request, timeout=timeout)


def head_object(key, config, timeout=60):
    """Return {'status','size'} for an object, or None when the request itself fails."""
    try:
        response = _open(object_url(config, key), 'HEAD',
                         signature_headers('HEAD', object_url(config, key), b'', config), None, timeout)
    except Exception as exc:
        status = getattr(exc, 'code', None)
        if status in (301, 302, 303, 307, 308, 404):
            return {'status': int(status), 'size': None}
        return None
    try:
        length = response.headers.get('Content-Length')
        return {'status': int(response.status), 'size': int(length) if length not in (None, '') else None}
    finally:
        try:
            response.close()
        except Exception:
            pass


def put_object(key, body, content_type, config, timeout=60, when=None):
    url = object_url(config, key)
    headers = signature_headers('PUT', url, body, config, when)
    headers['Content-Type'] = content_type
    try:
        response = _open(url, 'PUT', headers, body, timeout)
    except Exception as exc:
        raise ValueError('图床上传失败：' + redact(getattr(exc, 'reason', None) or exc, config)) from None
    status = int(response.status)
    etag = response.headers.get('ETag')
    try:
        response.close()
    except Exception:
        pass
    if status not in (200, 201):
        raise ValueError('图床上传失败：HTTP ' + str(status))
    return {'status': status, 'etag': etag, 'bytes': len(body)}


def is_public_https(url):
    """A public link must be HTTPS, credential-free and not a private/loopback target."""
    parts = urlsplit(str(url or ''))
    if parts.scheme != 'https' or not parts.netloc or parts.username or parts.password or parts.port:
        return False
    host = (parts.hostname or '').lower()
    if not host or host in LOOPBACK or '.' not in host:
        return False
    if host.endswith(('.local', '.internal', '.localhost', '.test', '.invalid')):
        return False
    if host.startswith('10.') or host.startswith('192.168.') or host.startswith('172.16.'):
        return False
    return True


def public_reachable(url, timeout=20, config=None):
    """Fetch the public link with no credentials; require 200 and an image content type."""
    if not is_public_https(url):
        return {'ok': False, 'reason': '链接不是公开 HTTPS 地址（不能是内网、本机或带凭据的地址）'}
    try:
        response = _open(url, 'GET', {'Accept': 'image/*'}, None, timeout)
    except Exception as exc:
        status = getattr(exc, 'code', None)
        detail = redact(getattr(exc, 'reason', None) or exc, config)
        return {'ok': False, 'reason': ('链接返回 HTTP ' + str(status)) if status else ('链接无法访问：' + detail)}
    try:
        status = int(response.status)
        content_type = (response.headers.get_content_type() or '').lower()
    finally:
        try:
            response.close()
        except Exception:
            pass
    if status != 200:
        return {'ok': False, 'reason': '链接返回 HTTP ' + str(status)}
    if not content_type.startswith('image/'):
        return {'ok': False, 'reason': '链接返回的内容类型是 ' + (content_type or '未知') + '，不是图片'}
    return {'ok': True, 'content_type': content_type}


def public_url(config, key):
    return config.public_base_url + '/' + quote(key, safe='/')
