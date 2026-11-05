"""Explicit local longtext uploads. No background registration or implicit sync.

The existing renderer owns source/privacy/type rules. The shared CLI lock serializes
writers; a separate durable receipt journal keeps incomplete work OUT of the ledger.
Ambiguous POSTs are reconciled by content hash, never blindly repeated.
"""
from __future__ import annotations
from contextlib import contextmanager, closing
from collections import Counter
from datetime import datetime, timezone
from dataclasses import dataclass
import fcntl
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import re
import time

from scripts.weknora_push_longtext import KB, SOURCES, Ledger, render, SafetyStop, metadata, sha
from weknora_client import WeKnoraClient, WeKnoraError


def now():
    return datetime.now(timezone.utc).isoformat()


class UploadStop(ValueError):
    pass


class UploadBusy(UploadStop):
    pass


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = path.with_suffix('.tmp')
    with tmp.open('w', encoding='utf-8') as out:
        os.chmod(tmp, 0o600)
        json.dump(value, out, ensure_ascii=False)
        out.flush()
        os.fsync(out.fileno())
    os.replace(tmp, path)
    fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


class AuditClient(WeKnoraClient):
    """Only typed upload operations; never store headers, source bodies or raw errors."""
    def __init__(self, config, audit_path, **kw):
        super().__init__(config, **kw)
        self.audit_path = audit_path

    def _request(self, method, path, payload=None, query=None):
        # Also protects a future accidental caller; search/settings writes aren't allowed.
        allowed = method == 'GET' or (method == 'POST' and path == f'/knowledge-bases/{KB}/knowledge/manual')
        if not allowed:
            raise UploadStop('上传入口不允许此远端写操作')
        row = {'at': now(), 'method': method, 'path': path, 'query': query}
        if payload is not None:
            row['payload'] = {k: v for k, v in payload.items() if k not in ('content', 'title')}
            row['content_sha256'] = sha(payload.get('content', ''))
            row['content_characters'] = len(payload.get('content', ''))
        try:
            result = super()._request(method, path, payload, query)
            row['knowledge_id'] = (result.get('data') or {}).get('id') if isinstance(result.get('data'), dict) else None
            return result
        finally:
            row['status'] = self.last_status
            self.audit_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            with self.audit_path.open('a', encoding='utf-8') as out:
                os.chmod(self.audit_path, 0o600)
                out.write(json.dumps(row, ensure_ascii=False) + '\n')
                out.flush()
                os.fsync(out.fileno())


@dataclass(frozen=True)
class UnavailableDocument:
    """Identity survives a per-row failure; never eligible for remote upload."""
    number: str
    source_id: str
    source: str
    title: str
    error: str
    error_code: str
    body_characters: int = 0
    content_sha256: str = ''


class UploadService:
    def __init__(self, root, *, client_factory=None):
        self.root = Path(root).resolve()
        self.workspace = self.root / 'workspace'
        self.state = self.workspace / 'weknora' / 'upload-receipts'
        self.ledger_path = self.workspace / 'weknora' / 'ledger.db'
        self.client_factory = client_factory or (lambda: AuditClient.from_env_file(
            self.root / '.env.local', audit_path=self.state / 'http-audit.jsonl', timeout=90))

    @staticmethod
    def read_document(db, rowid):
        # Keep identity enumeration independent from content fetch/decode failures.
        row = db.execute('SELECT id,source,title,body,url,modified FROM documents WHERE rowid=?', (rowid,)).fetchone()
        if row is None:
            raise OSError('source row missing')
        return row

    def documents(self):
        # Match the CLI tag/number/render rules; only per-row error handling differs.
        tags = json.loads((self.root / 'coordination' / 'weknora-tags.json').read_text())
        if tags.get('knowledge_base_id') != KB:
            raise SafetyStop('tag_knowledge_base_mismatch')
        public = tags['tags']['隐私-公开']
        if not isinstance(public, str) or not public:
            raise SafetyStop('privacy_tag_missing')
        types = {}
        for source, (_, kind, _) in SOURCES.items():
            tag = tags['tags'].get('类型-' + kind)
            if not isinstance(tag, str) or not tag or tag == public:
                raise SafetyStop('type_tag_missing_or_invalid')
            types[source] = tag
        if len(set(types.values())) != len(SOURCES):
            raise SafetyStop('type_tags_not_distinct')
        docs = []
        with closing(sqlite3.connect((self.workspace / 'knowledge' / 'knowledge.db').as_uri() + '?mode=ro', uri=True)) as db:
            db.row_factory = sqlite3.Row
            db.execute('PRAGMA query_only=ON')
            db.execute('BEGIN')
            entries = db.execute("SELECT rowid,id,source FROM documents WHERE source IN ('articles','external-references') ORDER BY source,id").fetchall()
            # Number collisions must not silently select one document by dictionary overwrite.
            numbers = [SOURCES[e['source']][0] + '-' + (re.sub(r'[^A-Za-z0-9_-]', '-', e['id'])
                       if isinstance(e['id'], str) and e['id'] else 'unreadable-row-' + str(e['rowid'])) for e in entries]
            duplicate = {n for n, count in Counter(numbers).items() if count > 1}
            for entry, number in zip(entries, numbers):
                title = '【' + SOURCES[entry['source']][1] + '】' + number
                size = 0
                try:
                    if not isinstance(entry['id'], str) or not entry['id']:
                        raise TypeError('invalid source identity')
                    row = self.read_document(db, entry['rowid'])
                    if isinstance(row['title'], str) and row['title'].strip():
                        title += ' ' + ' '.join(row['title'].splitlines()).strip()
                    if not isinstance(row['body'], str):
                        raise TypeError('invalid body')
                    size = len(row['body'])
                    if number in duplicate:
                        raise SafetyStop('result_number_collision')
                    doc = render(row, public, types[entry['source']])
                    if not doc.body.strip():
                        docs.append(UnavailableDocument(number, entry['id'], entry['source'], title[:250],
                                    '不可上传：正文为空，请先补充正文', 'empty_body', size))
                    else:
                        docs.append(doc)
                except Exception as exc:
                    # Curated messages only: a read/renderer exception can contain source data or credentials.
                    code = str(exc) if isinstance(exc, SafetyStop) else 'source_read_or_render_failed'
                    reasons = {'longtext_exceeds_manual_limit': '渲染后正文超过 200000 字符上限，请拆分后重试',
                               'result_number_collision': '文档编号冲突，请先核对来源'}
                    reason = reasons.get(code, '读取或渲染失败，请检查该篇本机资料后重试')
                    docs.append(UnavailableDocument(number, entry['id'], entry['source'], title[:250],
                                '不可上传：' + reason, code if code in reasons else 'source_read_or_render_failed', size))
        return docs

    def rows(self):
        with closing(sqlite3.connect(self.ledger_path.as_uri() + '?mode=ro', uri=True)) as db:
            db.row_factory = sqlite3.Row
            db.execute('PRAGMA query_only=ON')
            return {r['result_number']: dict(r) for r in db.execute('SELECT * FROM result_ledger')}

    @staticmethod
    def same(old, doc):
        return (old and old['knowledge_base_id'] == KB and old['remote_state'] == 'active'
                and old['content_sha256'] == doc.content_sha256
                and set(json.loads(old['tag_ids_json'])) == set(doc.tag_ids))

    def catalog(self):
        rows = self.rows()
        items = []
        for d in self.documents():
            if isinstance(d, UnavailableDocument):
                # Existing frontend already renders failed + error cards; no frontend rewrite needed.
                items.append({'number': d.number, 'source_id': d.source_id, 'source': d.source,
                              'title': d.title, 'body_characters': d.body_characters,
                              'content_sha256': '', 'state': 'failed', 'action': 'failed',
                              'uploadable': False, 'error': d.error, 'error_code': d.error_code,
                              'knowledge_id': None})
                continue
            old = rows.get(d.number)
            state = 'uploaded' if self.same(old, d) else ('changed' if old else 'pending')
            items.append({'number': d.number, 'source_id': d.source_id, 'source': d.source,
                          'title': d.title, 'body_characters': len(d.body),
                          'content_sha256': d.content_sha256, 'state': state,
                          'knowledge_id': old['knowledge_id'] if old else None})
        return {'items': items, 'target': '大后方', 'max_batch': 50}

    @contextmanager
    def lock(self):
        # Same path as weknora_push_longtext CLI: no CLI/UI writer race.
        path = Path('/tmp') / ('weknora-longtext-' + sha(str(self.root))[:16] + '.lock')
        with path.open('a') as out:
            try:
                fcntl.flock(out, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise UploadBusy('已有上传正在进行，请等待完成后重试') from None
            try:
                yield
            finally:
                fcntl.flock(out, fcntl.LOCK_UN)

    def receipt_path(self, number):
        return self.state / (sha(number) + '.json')

    def read_receipt(self, doc):
        path = self.receipt_path(doc.number)
        if not path.exists():
            return None
        value = json.loads(path.read_text())
        if value['number'] != doc.number or value['content_sha256'] != doc.content_sha256:
            raise UploadStop('上次上传的内容已变化，请先核实恢复记录')
        return value

    def save_receipt(self, doc, **changes):
        value = {'number': doc.number, 'content_sha256': doc.content_sha256, 'at': now(), **changes}
        atomic_json(self.receipt_path(doc.number), value)
        return value

    @staticmethod
    def all_remote(client):
        items = []
        for page in range(1, 10001):
            rows = client.list_knowledge(KB, page=page, page_size=100)['data']
            if not isinstance(rows, list):
                raise UploadStop('远端清单返回异常')
            items.extend(rows)
            if len(rows) < 100:
                return items
        raise UploadStop('远端分页过多，已停止')

    @staticmethod
    def verify(client, kid, doc):
        row = client.get_knowledge(kid)['data']
        meta = metadata(row)
        if (row.get('knowledge_base_id') != KB or sha(meta.get('content', '')) != doc.content_sha256
                or meta.get('status') != 'publish'
                or {t['id'] for t in row.get('tags', [])} != set(doc.tag_ids)):
            raise UploadStop('远端正文或标签核验不一致，未登记台账')
        return row

    def preflight(self, client, docs):
        if client.config.kb_id != KB:
            raise UploadStop('只允许上传大后方')
        # Wiki is intentionally allowed; uploads must not change any KB setting.
        kb = client._request('GET', '/knowledge-bases/' + KB)['data']
        if kb.get('id') != KB:
            raise UploadStop('远端知识库不匹配')
        tags = {}
        for page in range(1, 101):
            part = client.list_tags(KB, page=page, page_size=50)['data']
            if isinstance(part, dict):
                part = part.get('items', part.get('data', []))
            tags.update({t['id']: t['name'] for t in part})
            if len(part) < 50:
                break
        else:
            raise UploadStop('远端标签分页异常')
        for doc in docs:
            if tuple(tags.get(t) for t in doc.tag_ids) != ('隐私-公开', '类型-' + SOURCES[doc.source][1]):
                raise UploadStop('内部标签与远端标签不一致')
            if client.config.api_key in doc.content or client.config.api_key in doc.title:
                raise UploadStop('正文含凭据，已停止上传')

    def one(self, client, ledger, doc, remote):
        old = ledger.get(doc.number)
        if self.same(old, doc):
            return {'number': doc.number, 'action': 'skipped', 'knowledge_id': old['knowledge_id']}
        if old:
            # M3 is an upload entry, never silently overwrite edited remote material.
            raise UploadStop('已有台账但内容或状态不同，请先核对，未覆盖远端')
        receipt = self.read_receipt(doc)
        made_new = False
        kid = receipt.get('knowledge_id') if receipt else None
        if not kid:
            candidates = [r for r in remote if r.get('title') == doc.title or doc.number in r.get('title', '')]
            if len(candidates) > 1:
                raise UploadStop('远端编号重复，已停止以免重复上传')
            if candidates:
                kid = candidates[0]['id']
                self.verify(client, kid, doc)
                self.save_receipt(doc, state='accepted', knowledge_id=kid)
            elif receipt and receipt.get('state') in ('sending', 'uncertain'):
                raise UploadStop('上次响应未确认且远端尚未找到；请稍后重试核实，禁止重复发送')
        if not kid:
            self.save_receipt(doc, state='sending')  # fsync BEFORE the only POST
            try:
                result = client.create_manual(doc.title, doc.content, list(doc.tag_ids), KB)['data']
            except WeKnoraError as exc:
                # Determinate rejection permits an explicit retry; ambiguous delivery does not.
                state = 'rejected' if exc.status in (400, 401, 403, 404, 413, 422) else 'uncertain'
                self.save_receipt(doc, state=state)
                raise
            kid = result.get('id')
            if not isinstance(kid, str) or not kid:
                raise UploadStop('上传响应缺少条目编号，请重试核实，禁止重复发送')
            made_new = True
            self.save_receipt(doc, state='accepted', knowledge_id=kid)
            remote.append({'id': kid, 'title': doc.title})
        self.verify(client, kid, doc)
        ledger.put(doc, kid, only_new=True)
        self.save_receipt(doc, state='complete', knowledge_id=kid)
        return {'number': doc.number, 'action': 'created' if made_new else 'recovered', 'knowledge_id': kid}

    def upload(self, payload):
        if not isinstance(payload, dict) or set(payload) != {'documents'}:
            raise UploadStop('只接受选中的 documents，不接受正文、标签或目标库')
        selected = payload['documents']
        if not isinstance(selected, list) or not 1 <= len(selected) <= 50:
            raise UploadStop('一次请选择 1–50 篇')
        if any(not isinstance(x, dict) or set(x) != {'number', 'content_sha256'}
               or not isinstance(x['number'], str) or not isinstance(x['content_sha256'], str) for x in selected):
            raise UploadStop('文档选择格式无效')
        if len({x['number'] for x in selected}) != len(selected):
            raise UploadStop('同一批不能重复选择文档')
        with self.lock():
            available = {d.number: d for d in self.documents()}
            docs = []
            failures = {}
            for x in selected:
                doc = available.get(x['number'])
                if isinstance(doc, UnavailableDocument):
                    # Re-read can fail after the user selected a previously healthy card.
                    failures[x['number']] = {'number': x['number'], 'action': 'failed',
                                              'error': doc.error, 'error_code': doc.error_code}
                    continue
                if not doc or doc.content_sha256 != x['content_sha256']:
                    raise UploadStop('文档已变化或不可上传，请刷新后重新选择')
                docs.append(doc)
            def ordered(results):
                by_number = {r['number']: r for r in results}
                by_number.update(failures)
                return [by_number[x['number']] for x in selected]
            if not docs:
                return {'results': ordered([]), 'stopped': False}
            ledger = Ledger(self.ledger_path)
            # All unchanged: absolutely no remote call, including triple clicks.
            if all(self.same(ledger.get(d.number), d) for d in docs):
                return {'results': ordered([{'number': d.number, 'action': 'skipped', 'knowledge_id': ledger.get(d.number)['knowledge_id']} for d in docs]), 'stopped': False}
            client = self.client_factory()
            self.preflight(client, docs)
            remote = self.all_remote(client)
            results = []
            stopped = False
            for index, doc in enumerate(docs):
                try:
                    results.append(self.one(client, ledger, doc, remote))
                except Exception as exc:
                    # Never expose exception text from transport/source data.
                    status = getattr(exc, 'status', None)
                    message = str(exc) if isinstance(exc, UploadStop) else '上传未完成，可重试核实；已成功的条目不会重复上传'
                    results.append({'number': doc.number, 'action': 'failed', 'status': status, 'error': message})
                    # HTTP errors are an operational stop, not a license to burn through the batch.
                    if status is not None:
                        stopped = True
                        results.extend({'number': d.number, 'action': 'not_attempted'} for d in docs[index + 1:])
                        break
                if index < len(docs) - 1:
                    time.sleep(0.2)
            return {'results': ordered(results), 'stopped': stopped}


def service(handler):
    injected = getattr(handler.server, 'weknora_upload_service', None)
    return injected or UploadService(handler.server.store.workspace.parent)


def get_route(handler):
    try:
        return handler.reply(200, service(handler).catalog())
    except Exception:
        return handler.reply(503, {'error': '上传清单暂不可用，请检查本机资料与台账'})


def post_route(handler, payload):
    try:
        return handler.reply(200, service(handler).upload(payload))
    except UploadBusy as exc:
        return handler.reply(409, {'error': str(exc)})
    except UploadStop as exc:
        return handler.reply(400, {'error': str(exc)})
    except Exception:
        return handler.reply(503, {'error': '上传暂不可用，请稍后重试核实'})
