"""Bounded read-only remote retrieval, with explicit privacy and citation snapshots.

No remote endpoint except hybrid_search is used. Local snapshots contain only
selected, sanitized passages, never complete remote responses or credentials.
"""
from contextlib import contextmanager
from dataclasses import replace
import base64
import hashlib
import json
from pathlib import Path
import re
import shutil
import sqlite3
import tempfile
import threading
from urllib.parse import urlencode, urlsplit

from weknora_client import WeKnoraClient, resource_url

PUBLIC_KB = '82b87a9e-2154-4ac4-8615-55bb26e2a9dd'
MAX_PASSAGE = 8000
MAX_RESPONSE = 512000
WAIT_SECONDS = .8
MAX_SNAPSHOTS = 256
_ID = r'[A-Za-z0-9_-]{1,128}'
REMOTE_ID = re.compile(r'wk:([A-Za-z0-9_-]{1,128}):([A-Za-z0-9_-]{1,128}):([a-f0-9]{16})')
_LOCK = threading.Lock()
_SERVICES = {}


# Recognize compact JWT/JWE by its decoded JOSE header, not generic words or
# arbitrary dotted identifiers. Segment bounds also bound decoding work.
_JOSE = re.compile(r'(?<![A-Za-z0-9_-])([A-Za-z0-9_-]{8,2048})(?:\.[A-Za-z0-9_-]{0,4096}){2}(?:\.[A-Za-z0-9_-]{0,4096}\.[A-Za-z0-9_-]{0,4096})?(?![A-Za-z0-9_-])')


def _redact_jose(match):
    header=match.group(1)
    try:
        decoded=json.loads(base64.urlsafe_b64decode(header+'='*((-len(header))%4)))
        if isinstance(decoded,dict) and (isinstance(decoded.get('alg'),str) or str(decoded.get('typ','')).upper()=='JWT' or 'enc' in decoded):
            return '[凭据已脱敏]'
    except (ValueError,UnicodeError,RecursionError):
        pass
    return match.group(0)


def sanitize(value, limit=8000, secrets=()):
    if not isinstance(value, str):
        return ''
    value = value[:max(limit * 2, 16000)]
    for secret in secrets:
        if secret:
            value = value.replace(secret, '[已脱敏]')
    value = _JOSE.sub(_redact_jose,value)
    value = re.sub(r'(?im)(?:authorization|cookie|set-cookie|api[_-]?key|(?:access[_-]?|refresh[_-]?)?token|password|passwd|密码|密钥)["\']?\s*[=:：]\s*[^\n]+', '[鉴权内容已脱敏]', value)
    value = re.sub(r'(?i)\bbearer\s+[A-Za-z0-9._+/-]{8,}', '[已脱敏]', value)
    value = re.sub(r'\b(?:sk-|sk_|ghp_|xoxb-)[A-Za-z0-9_+/-]{8,}', '[已脱敏]', value)
    value = re.sub(r'\b[a-fA-F0-9]{32,}\b|\b[A-Za-z0-9_+=/-]{48,}\b', '[长串已脱敏]', value)
    return value[:limit]


def safe_url(value, config=None):
    if not isinstance(value, str) or len(value) > 2048:
        return ''
    if config is not None:
        value = resource_url(config, value)
    try:
        p = urlsplit(value)
        # Query strings may carry credentials. Do not propagate them.
        if p.scheme in ('http', 'https') and p.hostname and p.username is None and p.password is None and not p.query and not p.fragment and not re.search(r'[\x00-\x20]', value):
            return value
    except ValueError:
        pass
    return ''


def validate_query(query):
    if not isinstance(query, str) or not query.strip() or len(query) > 500 or any(ord(c) < 32 for c in query):
        raise ValueError('检索问题需为 1–500 字，不能包含控制字符')
    return query.strip()


def link(identity):
    return '/library.html?' + urlencode({'remote': identity})


class _LimitedOpener:
    def __init__(self, opener):
        self.opener = opener

    @contextmanager
    def open(self, *args, **kwargs):
        with self.opener.open(*args, **kwargs) as response:
            class BoundedResponse:
                status = response.status
                def read(self):
                    raw = response.read(MAX_RESPONSE + 1)
                    if len(raw) > MAX_RESPONSE:
                        raise ValueError('远端响应超过大小限制')
                    return raw
            yield BoundedResponse()


class RemoteSearch:
    def __init__(self, workspace, client_factory=None, tags_path=None, wait=WAIT_SECONDS):
        self.workspace = Path(workspace)
        self.client_factory = client_factory or self._client
        self.tags_path = Path(tags_path) if tags_path else Path(__file__).with_name('coordination') / 'weknora-tags.json'
        self.wait = wait
        self.slots = threading.BoundedSemaphore(2)
        self.lock = threading.RLock()
        self.cache = self.workspace / 'weknora' / 'retrieval-snapshots'

    @staticmethod
    def _client():
        client = WeKnoraClient.from_env_file(timeout=.6)
        # The runtime never follows an environment setting into the retired KB.
        client.config = replace(client.config, kb_id=PUBLIC_KB)
        client._opener = _LimitedOpener(client._opener)
        return client

    def catalog(self):
        """Read a stable binary copy; never open the live upload ledger in SQLite."""
        groups = json.loads(self.tags_path.read_text(encoding='utf-8'))['knowledge_bases']
        tags = groups['public']
        if tags['knowledge_base_id'] != PUBLIC_KB:
            raise ValueError('标签库配置不匹配')
        # Ledger KB/tag aliases may lag a server-side move. They are metadata,
        # never a search target: every request and every returned row must
        # still belong to PUBLIC_KB. No migration or remote tagging occurs.
        privacy = {v: k.removeprefix('隐私-') for group in groups.values() if isinstance(group,dict)
                   for k,v in group.get('tags',{}).items() if k.startswith('隐私-')}
        path = self.workspace / 'weknora/ledger.db'
        files = [path] + [Path(str(path) + suffix) for suffix in ('-wal',) if Path(str(path) + suffix).exists()]
        if any(p.stat().st_size > 8_000_000 for p in files):
            raise ValueError('账本大小超过读取限制')
        before = [(p.stat().st_size, p.stat().st_mtime_ns) for p in files]
        with tempfile.TemporaryDirectory(prefix='weknora-ledger-', dir='/tmp') as folder:
            for p in files:
                shutil.copyfile(p, Path(folder) / p.name)
            current = [path] + [Path(str(path) + suffix) for suffix in ('-wal',) if Path(str(path) + suffix).exists()]
            if current != files or before != [(p.stat().st_size, p.stat().st_mtime_ns) for p in files]:
                raise ValueError('账本正在变化，请稍后重试')
            db = sqlite3.connect((Path(folder) / path.name).as_uri() + '?mode=ro', uri=True, timeout=.1)
            try:
                rows = db.execute('SELECT knowledge_id,result_number,tag_ids_json FROM result_ledger WHERE remote_state=?', ('active',)).fetchall()
            finally:
                db.close()
        result = {}
        for kid, number, raw in rows:
            ids = json.loads(raw)
            labels = {privacy[t] for t in ids if t in privacy} if isinstance(ids, list) else set()
            # Missing/conflicting privacy is never implicitly made public.
            if len(labels) == 1 and isinstance(kid, str) and re.fullmatch(_ID, kid) and isinstance(number, str):
                result[kid] = {'result_number': number if re.fullmatch(r'(?:CW|WC)-[a-f0-9]{32}',number) else sanitize(number, 180), 'privacy': labels.pop()}
        return result

    def _normalize(self, raw, catalog, client, include_private, limit):
        rows = raw.get('data') if isinstance(raw, dict) and raw.get('success') is not False else None
        if isinstance(raw,dict) and raw.get('success') is True and 'data' in raw and rows is None:
            rows=[]  # Go nil slices encode an otherwise successful empty hit set as null.
        if not isinstance(rows, list):
            raise ValueError('远端结果格式错误')
        # Keep the server's knowledge ranking, but choose the most informative
        # returned passage for each knowledge ID (a trailing 'references: none'
        # chunk must not hide its substantive companion passage).
        ranked, best = [], {}
        for item in rows[:30]:
            if not isinstance(item,dict) or not isinstance(item.get('knowledge_id'),str):
                continue
            key=item['knowledge_id']
            if key not in best:
                ranked.append(key);best[key]=item
            elif isinstance(item.get('content'),str) and len(item['content']) > len(best[key].get('content') or ''):
                best[key]=item
        result, seen = [], set()
        for row in (best[key] for key in ranked):
            if not isinstance(row, dict):
                continue
            kid, chunk = row.get('knowledge_id'), row.get('id')
            if (not isinstance(kid, str) or kid not in catalog or kid in seen or
                    not isinstance(chunk, str) or not re.fullmatch(_ID, chunk) or row.get('knowledge_base_id') != PUBLIC_KB):
                continue
            record = catalog[kid]
            if record['privacy'] == '私密' and not include_private:
                continue
            if record['privacy'] not in ('公开', '群内', '内部', '私密'):
                continue
            original = row.get('content')
            if not isinstance(original, str) or not original.strip():
                continue
            secret = getattr(getattr(client, 'config', None), 'api_key', '')
            body = sanitize(original, MAX_PASSAGE, (secret,))
            title = sanitize(row.get('knowledge_title'), 180, (secret,)) or record['result_number']
            digest = hashlib.sha256(body.encode()).hexdigest()
            identity = 'wk:' + kid + ':' + chunk + ':' + digest[:16]
            url = link(identity)
            local_id = record['result_number'][3:]
            local_id = local_id if record['result_number'].startswith(('CW-', 'WC-')) and re.fullmatch('[a-f0-9]{32}', local_id) else ''
            local_url = '/knowledge.html?' + urlencode({'document': local_id}) if local_id else ''
            metadata = row.get('metadata') if isinstance(row.get('metadata'), dict) else {}
            source_url = safe_url(metadata.get('source_url'), getattr(client, 'config', None))
            if source_url and sanitize(source_url,2048,(secret,)) != source_url:source_url=''
            provenance = {**record, 'remote_knowledge_id': kid, 'remote_chunk_id': chunk,
                          'local_document_id': local_id, 'local_document_url': local_url,
                          'retrieval_scope': '检索片段，非整篇原文', 'content_redacted': body != original[:MAX_PASSAGE]}
            notice = '检索片段，非整篇原文；' + ('片段超过 8000 字，已截断；' if len(original) > MAX_PASSAGE else '') + '仅可引用此处实际返回的内容'
            result.append(dict(id=identity, title=title, source='weknora', kind='article', body=body,
                               excerpt=body[:280], snippet=body[:280], hash=digest, url=url, source_url=source_url,
                               provenance=provenance, privacy=record['privacy'], result_number=record['result_number'],
                               remote_knowledge_id=kid, remote_chunk_id=chunk, content_complete=False,
                               incomplete_reason=notice, truncated=len(original)>MAX_PASSAGE))
            seen.add(kid)
            if len(result) >= limit:
                break
        return result

    def search(self, query, *, include_private=False, limit=8):
        query = validate_query(query)
        if type(include_private) is not bool or type(limit) is not int or not 1 <= limit <= 30:
            raise ValueError('远端检索参数无效')
        if not self.slots.acquire(blocking=False):
            return {'results': [], 'state': 'unavailable', 'notice': '远端不可用（繁忙），已保留本机结果'}
        done, box = threading.Event(), []
        def fetch():
            try:
                catalog = self.catalog()
                client = self.client_factory()
                raw = client.hybrid_search(query, kb_id=PUBLIC_KB, match_count=30)
                box.append(self._checked_docs(self._normalize(raw, catalog, client, include_private, limit),include_private))
            except Exception:
                box.append(None)  # Never stringify transport/config/remote errors.
            finally:
                self.slots.release()
                done.set()
        threading.Thread(target=fetch, daemon=True, name='weknora-read').start()
        if not done.wait(self.wait) or not box or box[0] is None:
            return {'results': [], 'state': 'unavailable', 'notice': '远端不可用，已保留本机结果'}
        docs=[]
        try:
            docs=self._checked_docs(box[0],include_private)
            for doc in docs:
                self._remember_checked(doc,include_private)
            # Publish only after a second current-ledger check, including any
            # privacy changes that happened while serializing snapshots.
            visible=self._checked_docs(docs,include_private)
            visible_ids={doc['id'] for doc in visible}
            for doc in docs:
                if doc['id'] not in visible_ids:self._forget(doc)
        except Exception:
            for doc in docs:self._forget(doc)
            return {'results': [], 'state': 'unavailable', 'notice': '远端引用快照不可用，已保留本机结果'}
        return {'results': visible, 'state': 'ok', 'notice': '远端为混合检索片段；私密仅在显式勾选后展示' if include_private else '远端为混合检索片段；默认不含私密'}

    def checked_result(self,result,*,include_private=False):
        """Revalidate again at each consuming API/tool's publication boundary."""
        docs=result['results']
        try:
            visible=self._checked_docs(docs,include_private)
            ids={doc['id'] for doc in visible}
            for doc in docs:
                if doc['id'] not in ids:self._forget(doc)
        except Exception:
            for doc in docs:self._forget(doc)
            return {'results':[],'state':'unavailable','notice':'远端来源暂不可核对，已保留本机结果'}
        return {**result,'results':visible,**({'notice':'远端来源已变化，已丢弃相关候选'} if len(visible)!=len(docs) else {})}

    def _checked_docs(self,docs,include_private):
        if not docs:return []  # No passage to authorize or persist.
        current=self.catalog()
        return [doc for doc in docs if current.get(doc['remote_knowledge_id']) == {'result_number':doc['result_number'],'privacy':doc['privacy']} and (include_private or doc['privacy']!='私密')]

    def _forget(self,doc):
        with self.lock:
            try:self._path(doc['id']).unlink(missing_ok=True)
            except OSError:pass

    def _remember_checked(self,doc,include_private):
        with self.lock:
            if not self._checked_docs([doc],include_private):
                raise ValueError('远端来源或隐私已变化')
            self._remember(doc,include_private=include_private)
            try:
                if not self._checked_docs([doc],include_private):
                    raise ValueError('远端来源或隐私已变化')
            except Exception:
                self._forget(doc)
                raise

    def _path(self, identity):
        if not isinstance(identity, str) or not REMOTE_ID.fullmatch(identity):
            raise ValueError('无效远端资料编号')
        return self.cache / (hashlib.sha256(identity.encode()).hexdigest() + '.json')

    def _remember(self, doc, *, include_private=False):
        with self.lock:
            if not self._checked_docs([doc],include_private):
                raise ValueError('远端来源或隐私已变化')
            self.cache.mkdir(parents=True, exist_ok=True, mode=0o700)
            path = self._path(doc['id'])
            # Serial, atomic replacement inside the trusted cache directory.
            tmp = path.with_suffix('.tmp')
            try:
                with tmp.open('w', encoding='utf-8') as stream:
                    tmp.chmod(0o600)
                    json.dump(doc, stream, ensure_ascii=False)
                if not self._checked_docs([doc],include_private):
                    raise ValueError('远端来源或隐私已变化')
                tmp.replace(path)
            finally:
                tmp.unlink(missing_ok=True)
            files = sorted(self.cache.glob('*.json'), key=lambda p:p.stat().st_mtime)
            for old in files[:-MAX_SNAPSHOTS]:
                old.unlink()

    def document(self, identity, *, include_private=False):
        path = self._path(identity)
        with self.lock:
            if not path.exists() or path.stat().st_size > 60000:
                raise ValueError('远端片段已失效，请重新检索；已存素材仍保留快照')
            doc = json.loads(path.read_text(encoding='utf-8'))
        # Recheck current ledger privacy on every read: a cached public passage
        # cannot bypass a later privacy change or deletion.
        current = self.catalog().get(doc.get('remote_knowledge_id'))
        if not current or current['privacy'] != doc.get('privacy'):
            raise ValueError('远端来源或隐私已变化，请重新检索')
        if doc.get('privacy') == '私密' and not include_private:
            raise ValueError('私密资料须在素材页显式勾选后读取')
        if (doc.get('id') != identity or not isinstance(doc.get('body'), str) or len(doc['body']) > MAX_PASSAGE or
                hashlib.sha256(doc['body'].encode()).hexdigest() != doc.get('hash') or not identity.endswith(doc['hash'][:16])):
            raise ValueError('远端引用快照损坏，请重新检索')
        return doc

    def material(self, identity, *, include_private=False):
        doc = self.document(identity, include_private=include_private)
        return dict(id=doc['id'], legacy_id=doc['id'], title=doc['title'], source='weknora', sources=['weknora'],
                    kind='article', full_text=doc['body'], url=doc['url'], content_complete=False,
                    incomplete_reason=doc['incomplete_reason'], tags=['隐私-'+doc['privacy']],
                    legacy_refs=[{'store':'weknora','id':doc['remote_knowledge_id'], 'chunk_id':doc['remote_chunk_id'],
                                  'hash':doc['hash'], **doc['provenance']}],
                    rich_sources=[{'text':doc['body'], **doc['provenance'], 'hash':doc['hash'], 'url':doc['url']}])


def service(root):
    root = Path(root).resolve()
    with _LOCK:
        if root not in _SERVICES:
            _SERVICES[root] = RemoteSearch(root)
        return _SERVICES[root]


def library_results(root, query, local_search):
    from items_store import parameters
    parameters(query,{'q','source','page','include_private'})
    source = query.get('source', [''])[0]
    private = query.get('include_private', ['0'])[0]
    if private not in ('0', '1'):
        raise ValueError('私密范围参数无效')
    local_query = {k:v for k,v in query.items() if k != 'include_private'}
    if source == 'weknora':
        from items_store import parameters, page_number
        q = parameters(local_query, {'q','source','page'})
        local = {'items': [], 'total':0, 'page':page_number(q), 'has_more':False}
    else:
        local = local_search(root, local_query)
    q = query.get('q', [''])[0]
    if not q.strip() or source not in ('', 'weknora'):
        return local
    if local.get('page',1)==1:
        retrieval=service(root)
        remote=retrieval.checked_result(retrieval.search(q,include_private=private=='1'),include_private=private=='1')
    else:
        remote={'results':[], 'state':'first_page_only','notice':'远端候选已在第 1 页展示'}
    docs = [{k:v for k,v in doc.items() if k not in ('body', 'excerpt')} for doc in remote['results']]
    local['items'] = docs + local['items']
    local['total'] += len(docs)
    if docs:
        lower_bound = str(local.get('total_label','')).startswith('≥') or local.get('has_more')
        local['total_label'] = ('≥' if lower_bound else '') + str(local['total'])
    local['sources'] = list(dict.fromkeys(local.get('sources',[]) + ['weknora']))
    local['remote'] = {k:v for k,v in remote.items() if k != 'results'}
    return local
