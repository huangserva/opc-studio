"""User-confirmed local events with immutable source evidence and an audit trail.

This module never extracts tasks, merges people, calls models, or writes sources.
"""
from contextlib import contextmanager, closing
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import uuid


class EventConflict(ValueError):
    """A caller tried to overwrite a newer event or action revision."""


STATUSES = {'open', 'waiting', 'done', 'cancelled', 'needs_confirmation'}
VIEWS = {'all', 'important', 'mine', 'waiting', 'confirm', 'calendar'}
ALIASES = {'todos': 'mine', 'needs_confirmation': 'confirm'}


def now():
    return datetime.now(timezone.utc).isoformat()


def identity(value, label='编号'):
    if not isinstance(value, str) or not re.fullmatch(r'[a-f0-9]{32}', value):
        raise ValueError(f'{label}无效')
    return value


def text(value, label, maximum, required=False):
    if not isinstance(value, str) or '\x00' in value or len(value) > maximum:
        raise ValueError(f'{label}须为文字，最多 {maximum} 字')
    value = value.strip()
    if required and not value:
        raise ValueError(f'请填写{label}')
    return value


def flag(value, label):
    if not isinstance(value, bool):
        raise ValueError(f'{label}须为布尔值')
    return value


def due_time(value):
    value = text(value, '事项时间', 50)
    if not value:
        return ''
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d{1,6})?)?(?:Z|[+-]\d{2}:\d{2})', value):
        raise ValueError('事项时间须为含时区的 ISO 日期时间；时间未知请留空')
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        raise ValueError('事项日期时间无效') from None
    if parsed.utcoffset() is None:
        raise ValueError('事项时间须包含时区')
    return parsed.isoformat()


def payload_object(payload, allowed):
    if not isinstance(payload, dict) or set(payload) - allowed:
        raise ValueError('事项参数格式无效或包含未知字段')


class Events:
    def __init__(self, store):
        self.store = store
        self.root = Path(store.workspace) / 'events'
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self.root, 0o700)
        self.path = self.root / 'events.db'
        with closing(sqlite3.connect(self.path, timeout=30)) as c:
            c.execute('PRAGMA journal_mode=WAL')
            c.executescript('''
                CREATE TABLE IF NOT EXISTS events(
                    id TEXT PRIMARY KEY, title TEXT NOT NULL, summary TEXT NOT NULL,
                    priority TEXT NOT NULL, revision INTEGER NOT NULL,
                    needs_confirmation INTEGER NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS evidence(
                    id TEXT PRIMARY KEY, event_id TEXT NOT NULL REFERENCES events(id),
                    document_id TEXT NOT NULL, hash TEXT NOT NULL,
                    start INTEGER NOT NULL, end INTEGER NOT NULL, snapshot TEXT NOT NULL,
                    UNIQUE(event_id,document_id,hash,start,end)
                );
                CREATE INDEX IF NOT EXISTS evidence_event ON evidence(event_id);
                CREATE TABLE IF NOT EXISTS actions(
                    id TEXT PRIMARY KEY, event_id TEXT NOT NULL REFERENCES events(id),
                    summary TEXT NOT NULL, owner TEXT NOT NULL, owner_is_self INTEGER NOT NULL,
                    due_at TEXT NOT NULL, status TEXT NOT NULL, revision INTEGER NOT NULL,
                    evidence_ids TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS actions_event ON actions(event_id);
                CREATE TABLE IF NOT EXISTS history(
                    id INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT NOT NULL REFERENCES events(id),
                    entity_type TEXT NOT NULL, entity_id TEXT NOT NULL, origin TEXT NOT NULL,
                    reason TEXT NOT NULL, evidence_ids TEXT NOT NULL,
                    before_json TEXT NOT NULL, after_json TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS history_event ON history(event_id,id);
            ''')
        self._secure()

    def _secure(self):
        for suffix in ('', '-wal', '-shm'):
            path = Path(str(self.path) + suffix)
            try:
                os.chmod(path, 0o600)
            except FileNotFoundError:
                # SQLite removes WAL/SHM when the last concurrent reader closes.
                pass

    @contextmanager
    def db(self, write=False):
        c = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        c.row_factory = sqlite3.Row
        c.create_function('casefold', 1, lambda value: str(value or '').casefold(), deterministic=True)
        c.execute('PRAGMA foreign_keys=ON')
        if not write:
            c.execute('PRAGMA query_only=ON')
        try:
            c.execute('BEGIN IMMEDIATE' if write else 'BEGIN')
            yield c
            c.commit()
        except Exception:
            c.rollback()
            raise
        finally:
            c.close()
            self._secure()

    @staticmethod
    def _revision(payload, current):
        value = payload.get('revision')
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ValueError('修改须携带已读取的版本号')
        if value != current:
            raise EventConflict('事项已有新版本，请刷新后核对修改')

    @staticmethod
    def _new_revision(payload):
        if 'revision' in payload and (type(payload['revision']) is not int or payload['revision'] != 0):
            raise ValueError('新记录不能携带已有版本号')

    @staticmethod
    def _origin(payload, changed=False, new_evidence=False):
        origin = payload.get('origin', 'manual')
        if origin not in ('manual', 'evidence'):
            raise ValueError('变更来源须为 manual 或 evidence')
        reason = text(payload.get('reason', ''), '变更原因', 2000)
        if changed:
            if origin == 'manual' and not reason:
                raise ValueError('用户修改须填写变更原因')
            if origin == 'evidence' and not new_evidence:
                raise ValueError('根据来源更新须提供有效的新证据')
        return origin, reason

    def _capture(self, picks):
        if not isinstance(picks, list) or not 1 <= len(picks) <= 20:
            raise ValueError('请选择 1–20 个原文证据片段')
        snapshots = []
        size = 0
        for pick in picks:
            payload_object(pick, {'id', 'hash', 'start', 'end'})
            doc_id = identity(pick.get('id'), '来源编号')
            fingerprint = pick.get('hash')
            if not isinstance(fingerprint, str) or not re.fullmatch('[a-f0-9]{64}', fingerprint):
                raise ValueError('请选择带版本指纹的原文片段')
            doc = self.store.knowledge.document(doc_id)
            if fingerprint != doc['hash']:
                raise EventConflict('这份来源已更新，请重新读取并选择原文片段')
            start, end = pick.get('start'), pick.get('end')
            if type(start) is not int or type(end) is not int or not 0 <= start < end <= len(doc['body']):
                raise ValueError('证据原文范围无效')
            excerpt = doc['body'][start:end]
            if not excerpt.strip():
                raise ValueError('不能使用空白片段作为证据')
            size += len(excerpt)
            if size > 12000:
                raise ValueError('证据片段合计最多 12,000 字')
            snapshots.append({
                'document_id': doc_id, 'hash': fingerprint, 'start': start, 'end': end,
                'title': doc['title'], 'excerpt': excerpt, 'url': doc.get('url', ''),
                'source': doc['source'], 'kind': doc['kind'], 'modified': doc.get('modified', ''),
                'provenance': doc.get('provenance') or {}, 'captured_at': now(),
            })
        return snapshots

    @staticmethod
    def _row(c, event_id):
        row = c.execute('SELECT * FROM events WHERE id=?', (event_id,)).fetchone()
        if row is None:
            raise ValueError('事件不存在')
        result = dict(row)
        result['needs_confirmation'] = bool(result['needs_confirmation'])
        return result

    @staticmethod
    def _actions(c, event_id):
        actions = []
        for row in c.execute('SELECT * FROM actions WHERE event_id=? ORDER BY created_at,id', (event_id,)):
            item = dict(row)
            item['owner_is_self'] = bool(item['owner_is_self'])
            item['evidence_ids'] = json.loads(item['evidence_ids'])
            actions.append(item)
        return actions

    @staticmethod
    def _evidence(c, event_id):
        return [{**json.loads(r['snapshot']), 'id': r['id']} for r in c.execute(
            'SELECT id,snapshot FROM evidence WHERE event_id=? ORDER BY rowid', (event_id,))]

    def _detail(self, c, event_id, history=True):
        item = self._row(c, event_id)
        item['evidence'] = self._evidence(c, event_id)
        item['actions'] = self._actions(c, event_id)
        if history:
            entries = []
            for row in c.execute('SELECT * FROM history WHERE event_id=? ORDER BY id', (event_id,)):
                entry = dict(row)
                entry['before'] = json.loads(entry.pop('before_json'))
                entry['after'] = json.loads(entry.pop('after_json'))
                entry['evidence_ids'] = json.loads(entry['evidence_ids'])
                entries.append(entry)
            item['history'] = entries
        return item

    def _states(self, details):
        # Only fetch hashes of selected indexed IDs, never source bodies or a full scan.
        ids = sorted({e['document_id'] for d in details for e in d['evidence']})
        if not ids:
            return details
        hashes = {}
        with self.store.knowledge.db() as c:
            for offset in range(0, len(ids), 100):
                batch = ids[offset:offset + 100]
                hashes.update((r['id'], r['hash']) for r in c.execute(
                    'SELECT id,hash FROM documents WHERE id IN (' + ','.join('?' for _ in batch) + ')', batch))
        for detail in details:
            for e in detail['evidence']:
                current = hashes.get(e['document_id'])
                e['state'] = 'missing' if current is None else 'fresh' if current == e['hash'] else 'stale'
        return details

    def event(self, event_id):
        event_id = identity(event_id, '事件编号')
        with self.db() as c:
            detail = self._detail(c, event_id)
        return self._states([detail])[0]

    def list_events(self, query='', view='all', status='', limit=100):
        """Counts cover matched events before the view/limit; list omits history.

        'fresh' compares the saved version with the local knowledge index. Source
        connection freshness remains available in Knowledge.status().
        """
        query = text(query, '检索词', 200).casefold()
        if not isinstance(view, str) or not isinstance(status, str):
            raise ValueError('事件筛选无效')
        view = ALIASES.get(view, view)
        if view not in VIEWS or status and status not in STATUSES:
            raise ValueError('未知事件筛选')
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError('每次最多读取 100 个事件')
        conditions, args = [], []
        if query:
            pattern = '%' + query.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%'
            conditions.append("(casefold(e.title) LIKE ? ESCAPE '\\' OR casefold(e.summary) LIKE ? ESCAPE '\\' OR EXISTS(SELECT 1 FROM actions a WHERE a.event_id=e.id AND (casefold(a.summary) LIKE ? ESCAPE '\\' OR casefold(a.owner) LIKE ? ESCAPE '\\')))")
            args.extend([pattern] * 4)
        if status:
            conditions.append('(EXISTS(SELECT 1 FROM actions a WHERE a.event_id=e.id AND a.status=?) OR (?=\'needs_confirmation\' AND e.needs_confirmation=1))')
            args.extend([status, status])
        active = "a.status IN ('open','waiting')"
        views = {
            'all': '1', 'important': "e.priority='important'",
            'mine': f'EXISTS(SELECT 1 FROM actions a WHERE a.event_id=e.id AND a.owner_is_self=1 AND {active})',
            'waiting': "EXISTS(SELECT 1 FROM actions a WHERE a.event_id=e.id AND a.status='waiting')",
            'confirm': "(e.needs_confirmation=1 OR EXISTS(SELECT 1 FROM actions a WHERE a.event_id=e.id AND a.status='needs_confirmation'))",
            'calendar': "EXISTS(SELECT 1 FROM actions a WHERE a.event_id=e.id AND a.due_at!='' AND a.status IN ('open','waiting'))",
        }
        common = ' AND '.join(conditions) or '1'
        with self.db() as c:
            counts = {v: c.execute('SELECT COUNT(*) FROM events e WHERE ' + common + ' AND ' + clause, args).fetchone()[0] for v, clause in views.items()}
            counts['mine_actions'] = c.execute('SELECT COUNT(*) FROM actions a JOIN events e ON e.id=a.event_id WHERE ' + common + ' AND a.owner_is_self=1 AND ' + active, args).fetchone()[0]
            rows = c.execute('SELECT e.id FROM events e WHERE ' + common + ' AND ' + views[view] + ' ORDER BY e.updated_at DESC,e.id LIMIT ?', [*args, limit]).fetchall()
            selected = [self._detail(c, r['id'], history=False) for r in rows]
        counts.update(total=counts['all'], todos=counts['mine'], needs_confirmation=counts['confirm'])
        return {'events': self._states(selected), 'counts': counts}

    @staticmethod
    def _log(c, event_id, entity_type, entity_id, before, after, origin, reason, evidence_ids):
        c.execute('''INSERT INTO history(event_id,entity_type,entity_id,origin,reason,evidence_ids,before_json,after_json,created_at)
            VALUES(?,?,?,?,?,?,?,?,?)''', (event_id, entity_type, entity_id, origin, reason,
            json.dumps(evidence_ids), json.dumps(before, ensure_ascii=False), json.dumps(after, ensure_ascii=False), now()))

    def save_event(self, payload, *, creation_id=None):
        payload_object(payload, {'id', 'revision', 'title', 'summary', 'priority', 'needs_confirmation', 'picks', 'items', 'reason', 'origin'})
        if creation_id is not None and 'id' in payload:raise ValueError('内部创建不能携带已有事项编号')
        event_id = identity(creation_id, '事件编号') if creation_id is not None else identity(payload['id'], '事件编号') if 'id' in payload else uuid.uuid4().hex
        if 'picks' in payload and 'items' in payload and payload['picks'] != payload['items']:
            raise ValueError('items 与 picks 不能提供不同的证据')
        supplied = 'picks' in payload or 'items' in payload
        snapshots = self._capture(payload.get('picks', payload.get('items'))) if supplied else []
        with self.db(write=True) as c:
            existing = c.execute('SELECT 1 FROM events WHERE id=?', (event_id,)).fetchone()
            if 'id' in payload and not existing:
                raise ValueError('事件不存在；新增时请勿传入事件编号')
            if creation_id is not None and existing:
                return self._states([self._detail(c, event_id)])[0]
            before = self._row(c, event_id) if existing else None
            if before:
                self._revision(payload, before['revision'])
            else:
                self._new_revision(payload)
                if not snapshots:
                    raise ValueError('创建事件必须选择原文证据；提及不会自动成为事项')
            title = text(payload.get('title', before['title'] if before else ''), '事件标题', 200, True)
            summary = text(payload.get('summary', before['summary'] if before else ''), '事件摘要', 4000)
            priority = payload.get('priority', before['priority'] if before else 'normal')
            if not isinstance(priority, str) or priority not in ('normal', 'important'):
                raise ValueError('事件重要程度无效')
            confirmation = flag(payload.get('needs_confirmation', before['needs_confirmation'] if before else True), '待确认状态')
            old_evidence = self._evidence(c, event_id) if before else []
            existing_ids = {e['id'] for e in old_evidence}
            pending = []
            for snapshot in snapshots:
                key = json.dumps([event_id, snapshot['document_id'], snapshot['hash'], snapshot['start'], snapshot['end']])
                eid = hashlib.sha256(key.encode()).hexdigest()[:32]
                if eid not in existing_ids:
                    pending.append((eid, snapshot))
                    existing_ids.add(eid)
            if len(old_evidence) + len(pending) > 20 or sum(len(e['excerpt']) for e in old_evidence) + sum(len(e['excerpt']) for _, e in pending) > 12000:
                raise ValueError('一个事件最多保留 20 份证据、合计 12,000 字')
            origin, reason = self._origin(payload, changed=bool(before), new_evidence=bool(pending))
            fields = {'title': title, 'summary': summary, 'priority': priority, 'needs_confirmation': confirmation}
            changed = not before or bool(pending) or any(before[k] != v for k, v in fields.items())
            if changed:
                stamp = now()
                if before:
                    c.execute('UPDATE events SET title=?,summary=?,priority=?,needs_confirmation=?,revision=revision+1,updated_at=? WHERE id=?',
                        (title, summary, priority, int(confirmation), stamp, event_id))
                else:
                    c.execute('INSERT INTO events VALUES(?,?,?,?,?,?,?,?)',
                        (event_id, title, summary, priority, 1, int(confirmation), stamp, stamp))
                for eid, snapshot in pending:
                    c.execute('INSERT INTO evidence VALUES(?,?,?,?,?,?,?)', (eid, event_id, snapshot['document_id'], snapshot['hash'], snapshot['start'], snapshot['end'], json.dumps(snapshot, ensure_ascii=False)))
                after = self._row(c, event_id)
                audit_before = {**before, 'evidence_ids': [e['id'] for e in old_evidence]} if before else None
                audit_after = {**after, 'evidence_ids': [e['id'] for e in old_evidence] + [eid for eid, _ in pending]}
                self._log(c, event_id, 'event', event_id, audit_before, audit_after, origin, reason or ('建立事件（待确认）' if confirmation else '建立事件'), [eid for eid, _ in pending])
        return self.event(event_id)

    def save_action(self, event_id, payload):
        event_id = identity(event_id, '事件编号')
        payload_object(payload, {'id', 'revision', 'summary', 'owner', 'owner_is_self', 'due_at', 'status', 'evidence_ids', 'reason', 'origin'})
        action_id = identity(payload['id'], '事项编号') if 'id' in payload else uuid.uuid4().hex
        with self.db(write=True) as c:
            self._row(c, event_id)
            actions = {a['id']: a for a in self._actions(c, event_id)}
            before = actions.get(action_id)
            if 'id' in payload and before is None:
                raise ValueError('事项不属于本事件或不存在')
            if before:
                self._revision(payload, before['revision'])
            else:
                self._new_revision(payload)
                if c.execute('SELECT COUNT(*) FROM actions WHERE event_id=?', (event_id,)).fetchone()[0] >= 100:
                    raise ValueError('一个事件最多保留 100 个事项')
            summary = text(payload.get('summary', before['summary'] if before else ''), '事项内容', 1000, True)
            owner = text(payload.get('owner', before['owner'] if before else ''), '负责人', 200)
            owner_is_self = flag(payload.get('owner_is_self', before['owner_is_self'] if before else False), '由我跟进')
            due_at = due_time(payload.get('due_at', before['due_at'] if before else ''))
            status = payload.get('status', before['status'] if before else 'needs_confirmation')
            if not isinstance(status, str) or status not in STATUSES:
                raise ValueError('事项状态无效')
            evidence_ids = payload.get('evidence_ids', before['evidence_ids'] if before else None)
            if not isinstance(evidence_ids, list) or not 1 <= len(evidence_ids) <= 20:
                raise ValueError('事项必须关联本事件的原文证据')
            evidence_ids = list(dict.fromkeys(identity(e, '证据编号') for e in evidence_ids))
            valid_ids = {e['id'] for e in self._evidence(c, event_id)}
            if not set(evidence_ids) <= valid_ids:
                raise ValueError('事项证据不属于本事件')
            fields = {'summary': summary, 'owner': owner, 'owner_is_self': owner_is_self,
                'due_at': due_at, 'status': status, 'evidence_ids': evidence_ids}
            changed = before is None or any(before[k] != v for k, v in fields.items())
            new_evidence = bool(set(evidence_ids) - set(before['evidence_ids'])) if before else True
            origin, reason = self._origin(payload, changed=bool(before) and changed, new_evidence=new_evidence)
            if origin == 'evidence':
                selected = [e for e in self._evidence(c, event_id) if e['id'] in evidence_ids]
                if any(e['state'] != 'fresh' for e in self._states([{'evidence': selected}])[0]['evidence']):
                    raise EventConflict('引用的来源已变化或缺失，请核对新的原文证据')
            if changed:
                stamp = now()
                if before:
                    c.execute('UPDATE actions SET summary=?,owner=?,owner_is_self=?,due_at=?,status=?,evidence_ids=?,revision=revision+1,updated_at=? WHERE id=? AND event_id=?',
                        (summary, owner, int(owner_is_self), due_at, status, json.dumps(evidence_ids), stamp, action_id, event_id))
                else:
                    c.execute('INSERT INTO actions VALUES(?,?,?,?,?,?,?,?,?,?,?)', (action_id, event_id, summary, owner, int(owner_is_self), due_at, status, 1, json.dumps(evidence_ids), stamp, stamp))
                c.execute('UPDATE events SET revision=revision+1,updated_at=? WHERE id=?', (stamp, event_id))
                after = next(a for a in self._actions(c, event_id) if a['id'] == action_id)
                self._log(c, event_id, 'action', action_id, before, after, origin, reason or ('建立行动（待确认）' if status == 'needs_confirmation' else '建立行动'), evidence_ids)
        return self.event(event_id)
