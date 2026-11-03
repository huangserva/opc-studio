"""Small synchronous WeKnora v0.9 client; no startup hooks or implicit writes.

Use WeKnoraClient.from_env_file('/path/to/article-studio/.env.local').
Platform keys require both authentication headers. Writes are never retried.
"""
from dataclasses import dataclass, field
import json
from pathlib import Path
import re
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit, urlunsplit
from urllib.request import (Request, build_opener, HTTPRedirectHandler,
                            ProxyHandler)


class WeKnoraError(RuntimeError):
    """Credential-safe error. Server error bodies are deliberately excluded."""
    def __init__(self, message, status=None):
        self.status = status
        super().__init__(message)


def _raise_clean(message, status):
    """Raise without retaining even an ambient caller's active exception."""
    try:
        raise WeKnoraError(message, status) from None
    except WeKnoraError as clean:
        clean.__cause__ = None
        clean.__context__ = None
        raise


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # Never forward the platform credential to another endpoint.


@dataclass(frozen=True)
class WeKnoraConfig:
    base_url: str
    api_key: str = field(repr=False)
    tenant_id: str
    kb_id: str

    def __post_init__(self):
        p = urlsplit(self.base_url)
        # Allow a deployment prefix, without ambiguous or traversing segments.
        api_path = p.path.rstrip('/')
        segments = api_path.split('/')[1:]
        if (p.scheme not in ('http', 'https') or not p.hostname or
                p.username is not None or p.password is not None or
                p.query or p.fragment or
                '?' in self.base_url or '#' in self.base_url or
                any(ord(c) < 33 or ord(c) > 126 for c in self.base_url) or
                not re.fullmatch(r'(?:/[A-Za-z0-9._~-]+)*/api/v1', api_path) or
                any(segment in ('.', '..') for segment in segments)):
            raise ValueError('Invalid WEKNORA_BASE_URL; expected origin[/prefix]/api/v1')
        if p.scheme == 'http' and p.hostname not in ('127.0.0.1', 'localhost', '::1'):
            raise ValueError('HTTP is allowed only over loopback')
        # Validate without encoding: UnicodeEncodeError keeps the secret in
        # its .object, so even repr(exception) could otherwise disclose it.
        if (not isinstance(self.api_key, str) or not self.api_key or
                any(not 33 <= ord(c) <= 126 for c in self.api_key)):
            raise ValueError('Invalid WEKNORA_API_KEY')
        if not str(self.tenant_id).isdigit() or int(self.tenant_id) <= 0:
            raise ValueError('Invalid WEKNORA_TENANT_ID')
        _id(self.kb_id)

    @classmethod
    def from_env_file(cls, path):
        # Only read these four variables. Do not load secrets into os.environ,
        # expand shell expressions, or interpolate other environment values.
        names = ('WEKNORA_BASE_URL', 'WEKNORA_API_KEY',
                 'WEKNORA_TENANT_ID', 'WEKNORA_KB_ID')
        values = {}
        for raw in Path(path).read_text(encoding='utf-8').splitlines():
            line = raw.strip()
            if line.startswith('export '):
                line = line[7:].lstrip()
            key, sep, value = line.partition('=')
            key = key.strip()
            if not sep or key not in names:
                continue
            value = value.strip()
            if value[:1] in ('"', "'"):
                quote = value[0]
                end = value.find(quote, 1)
                if end < 0 or value[end+1:].strip().split('#', 1)[0].strip():
                    raise ValueError('Invalid quoted configuration value: ' + key)
                value = value[1:end]
            else:
                value = re.split(r'\s+#', value, maxsplit=1)[0].rstrip()
            if key in values:
                raise ValueError('Duplicate configuration variable: ' + key)
            values[key] = value
        missing = [key for key in names if not values.get(key)]
        if missing:
            raise ValueError('Missing configuration: ' + ', '.join(missing))
        return cls(*(values[key] for key in names))


def resource_url(config, value):
    """Expand a root-relative WeKnora /r/ URL using the configured prefix.

    Other values, including complete URLs, are returned unchanged. This is
    path adaptation only, not validation for rendering arbitrary links.
    """
    if not isinstance(value, str) or not value.startswith('/r/'):
        return value
    base = urlsplit(config.base_url)
    prefix = base.path.rstrip('/')[:-len('/api/v1')]
    origin = urlunsplit((base.scheme, base.netloc, '', '', ''))
    return origin + prefix + value


def _id(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', value):
        raise ValueError('Invalid WeKnora identifier')
    return value


def _tags(values):
    if not isinstance(values, (list, tuple)):
        raise ValueError('tag_ids must be a list')
    return list(dict.fromkeys(_id(v) for v in values))


def _manual(title, content):
    if not isinstance(title, str) or not title.strip():
        raise ValueError('title is required')
    if not isinstance(content, str) or not content.strip() or len(content) > 200000:
        raise ValueError('Complete Markdown content is required (max 200000 characters)')
    return {'title': title, 'content': content, 'status': 'publish',
            'process_config': {'summary_enabled': False}}


class WeKnoraClient:
    def __init__(self, config, *, timeout=30):
        self.config = config
        self.timeout = timeout
        self.last_status = None
        self._opener = build_opener(ProxyHandler({}), _NoRedirect())

    @classmethod
    def from_env_file(cls, path=None, **kwargs):
        path = path or Path(__file__).resolve().with_name('.env.local')
        return cls(WeKnoraConfig.from_env_file(path), **kwargs)

    def __repr__(self):
        return f'WeKnoraClient(tenant_id={self.config.tenant_id!r})'

    def _request(self, method, path, payload=None, query=None):
        # Paths are built by typed methods below, never accepted from users.
        self.last_status = None
        failure = None
        url = body = request = raw = result = response = None
        try:
            url = self.config.base_url.rstrip('/') + path
            if query:
                url += '?' + urlencode(query)
            body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode('utf-8')
            request = Request(url, data=body, method=method, headers={
                'X-API-Key': self.config.api_key,
                'X-Tenant-ID': str(self.config.tenant_id),
                'Content-Type': 'application/json', 'Accept': 'application/json'})
            with self._opener.open(request, timeout=self.timeout) as response:
                self.last_status = response.status
                raw = response.read()
        except HTTPError as exc:
            self.last_status = exc.code
            exc.close()
            failure = (f'WeKnora HTTP {exc.code}', exc.code)
        except UnicodeError:
            failure = ('WeKnora request encoding failed', None)
        except (URLError, TimeoutError, OSError):
            failure = ('WeKnora connection failed', None)
        if failure is None:
            try:
                result = json.loads(raw) if raw else {'success': True}
            except (ValueError, UnicodeError):
                failure = ('WeKnora returned invalid JSON', self.last_status)
            if failure is None and (not isinstance(result, dict) or result.get('success') is False):
                failure = ('WeKnora request rejected', self.last_status)
        if failure is not None:
            # Exit every raw-exception handler first; do not retain request
            # headers, raw bodies or the client/config in this transport frame.
            del self, payload, query, url, body, request, raw, result, response
            _raise_clean(*failure)
        return result

    def _kb(self, kb_id):
        return _id(self.config.kb_id if kb_id is None else kb_id)

    def list_tags(self, kb_id=None, *, page=1, page_size=50):
        self._page(page, page_size, 50)
        return self._request('GET', f'/knowledge-bases/{self._kb(kb_id)}/tags',
                             query={'page': page, 'page_size': page_size})

    def create_tag(self, name, kb_id=None):
        if not isinstance(name, str) or not name.strip():
            raise ValueError('Tag name is required')
        return self._request('POST', f'/knowledge-bases/{self._kb(kb_id)}/tags', {'name': name})

    def create_manual(self, title, content, tag_ids=(), kb_id=None):
        payload = _manual(title, content)
        payload.update(tag_ids=_tags(tag_ids), channel='api')
        return self._request('POST', f'/knowledge-bases/{self._kb(kb_id)}/knowledge/manual', payload)

    def update_manual(self, knowledge_id, title, content):
        # Explicit publish is mandatory; updating tags is a separate operation.
        return self._request('PUT', f'/knowledge/manual/{_id(knowledge_id)}', _manual(title, content))

    def replace_tags(self, knowledge_id, tag_ids, kb_id=None):
        return self._request('PUT', '/knowledge/tags', {'kb_id': self._kb(kb_id),
                             'knowledge_ids': [_id(knowledge_id)], 'tag_ids': _tags(tag_ids)})

    def get_knowledge(self, knowledge_id):
        return self._request('GET', f'/knowledge/{_id(knowledge_id)}')

    @staticmethod
    def _page(page, page_size, limit):
        if (type(page) is not int or type(page_size) is not int or
                not 1 <= page <= 10000 or not 1 <= page_size <= limit):
            raise ValueError('Invalid pagination')

    def list_knowledge(self, kb_id=None, *, page=1, page_size=100):
        self._page(page, page_size, 100)
        return self._request('GET', f'/knowledge-bases/{self._kb(kb_id)}/knowledge',
                             query={'page': page, 'page_size': page_size})

    def hybrid_search(self, query, kb_id=None, *, match_count=10, knowledge_ids=(), tag_ids=()):
        if not isinstance(query, str) or not query.strip() or len(query) > 10000:
            raise ValueError('Invalid search query')
        if type(match_count) is not int or not 1 <= match_count <= 30:
            raise ValueError('Invalid match_count')
        if len(knowledge_ids) > 20:
            raise ValueError('At most 20 knowledge_ids')
        return self._request('POST', f'/knowledge-bases/{self._kb(kb_id)}/hybrid-search', {
            'query_text': query, 'match_count': match_count,
            'disable_keywords_match': False, 'disable_vector_match': False,
            'knowledge_ids': [_id(v) for v in knowledge_ids], 'tag_ids': _tags(tag_ids)})

    def delete_knowledge(self, knowledge_id):
        """Explicit deletion; never called as an update/not-found fallback."""
        return self._request('DELETE', f'/knowledge/{_id(knowledge_id)}')
