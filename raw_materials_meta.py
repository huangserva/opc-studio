"""Read-only material metadata and a separately bounded acquisition-health probe.

The JSON sidecar contains counts/watermarks only, never originals or credentials.
Every SQLite connection is private to a refresh and is opened read-only.
"""
from contextlib import closing, contextmanager
import copy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sqlite3
import tempfile
import threading
import time
from wechat_source_client import RemoteCapture

REFRESH_SECONDS = 30
HEALTH_TIMEOUT = .075
HEALTH_TTL = 10
# The old COUNT(d.id) ignores NULL ids. Preserve that edge case as well as trash.
EXCLUDED_COUNT_ROWS = ('SELECT x.rowid FROM document_trash t JOIN documents x ON x.id=t.document_id '
                       'UNION ALL SELECT rowid FROM documents WHERE id IS NULL')
SOURCE_SQL = ('SELECT s.*,COUNT(d.rowid) count,MAX(d.modified) latest FROM sources s '
              'LEFT JOIN documents d ON d.source=s.id AND d.rowid NOT IN ('+EXCLUDED_COUNT_ROWS+') '
              'GROUP BY s.id ORDER BY s.rowid')
# MAX still includes a document with NULL id in the old query. Such rows are
# unusual but legal in a legacy SQLite TEXT PRIMARY KEY table.
LATEST_SQL = ('SELECT source,MAX(modified) latest FROM documents WHERE rowid NOT IN '
              '(SELECT x.rowid FROM document_trash t JOIN documents x ON x.id=t.document_id) GROUP BY source')
TOTAL_SQL = ('SELECT COUNT(*) FROM documents WHERE rowid NOT IN '
             '(SELECT x.rowid FROM document_trash t JOIN documents x ON x.id=t.document_id)')
SOURCE_FIELDS = frozenset(('id','name','kind','backend','enabled','capture_enabled','date_from','date_to',
                          'document_kind','status','detail','synced_at','count','latest'))
HEALTH_FIELDS = frozenset(('available','status','busy','phase','detail','started_at','finished_at','captured_at',
                          'messages_imported','new_messages','collector_messages','index_status','indexed_at',
                          'error_kind','max_msg_time','recovered','index_detail','last_success'))


def stamp():
    return datetime.now(timezone.utc).isoformat()


def public_text(value):
    value = re.sub(r'(?i)\b(?:sk|api)[-_][\w-]{12,}|(?:api[_-]?key|authorization|password|access[_-]?token)\s*[:=]\s*\S+', '[已隐藏]', str(value))
    value = re.sub(r'(?:https?|file)://[^\s，。；]+|(?<![\w:/])(?:[A-Za-z]:[\\/]|/|~/)[^\s，。；]+|\b(?:\d{1,3}\.){3}\d{1,3}(?::\d+)?\b', '[地址已隐藏]', value)
    return value[:2000]


def safe_health(value):
    if not isinstance(value, dict):
        return dict(available=False, busy=False, status='unknown', detail='采集健康未知')
    result = {}
    for key, item in value.items():
        if key not in HEALTH_FIELDS:
            continue
        if isinstance(item, str): result[key] = public_text(item)
        elif type(item) in (bool, int, float) or item is None: result[key] = item
        elif key == 'last_success' and isinstance(item, dict): result[key] = safe_health(item)
    return result


class HealthCache:
    def __init__(self, capture):
        self.capture = capture
        self.lock = threading.Lock()
        self.result = None
        self.as_of = None
        self.checked = 0
        self.inflight = None
        self.invalidated = False
        self.failed = False

    def invalidate(self):
        with self.lock: self.invalidated = True

    def snapshot(self, *, public=True):
        with self.lock:
            known = self.result is not None
            stale = self.failed or self.invalidated or self.inflight is not None or time.monotonic()-self.checked > HEALTH_TTL
            return dict(capture=(safe_health(self.result) if public else copy.deepcopy(self.result)) if known else safe_health(None),
                        as_of=self.as_of, state='last_known' if known and stale else 'known' if known else 'unknown',
                        stale=stale if known else True)

    def request(self, *, force=False, timeout=HEALTH_TIMEOUT):
        with self.lock:
            if force and self.inflight is not None:self.invalidated=True
            if self.inflight is None and (force or self.invalidated or self.checked==0 or time.monotonic()-self.checked > HEALTH_TTL):
                done = threading.Event()
                self.inflight = done
                self.invalidated = False
                def probe():
                    began = stamp()
                    try:
                        # RemoteCapture.status turns transport errors into an
                        # ordinary status dict. Keep the last successful probe
                        # instead of recording a timeout as fresh knowledge.
                        raw=(self.capture.client.request('capture')
                             if isinstance(self.capture,RemoteCapture) else self.capture.status())
                        if not isinstance(raw,dict) or not isinstance(raw.get('status'),str):raise ValueError('health_schema')
                        result=copy.deepcopy(raw)
                    except Exception:result=None
                    with self.lock:
                        self.failed=result is None
                        if result is not None:self.result=result;self.as_of=began
                        self.checked=time.monotonic();self.inflight=None
                    done.set()
                threading.Thread(target=probe, name='raw-materials-health', daemon=True).start()
            done = self.inflight
        if done is not None:
            done.wait(timeout)  # A hung upstream gets one bounded daemon, never a thread per GET.
        return self.snapshot()


class MetadataCache:
    def __init__(self, knowledge):
        self.knowledge = knowledge
        self.path = Path(knowledge.path)
        self.sidecar = self.path.parent.parent/'raw-materials-meta'/'counts.json'
        self.lock = threading.Lock()
        self.refresh_lock = threading.Lock()
        self.changed = threading.Event()
        self.stop = knowledge.stop_event
        self.health = HealthCache(knowledge.capture)
        self.value = None
        self.as_of = None
        self.dirty = True
        self.generation = 0
        self.last_validation = None
        self.last_error = None
        self.worker = None
        self._load()

    def _load(self):
        # A process/connection-cold GET can use the private last-known watermark.
        # Only source configuration and dossier headers are read here, never documents.
        try:
            if self.sidecar.is_symlink():return
            with self.sidecar.open() as stream:disk=json.loads(stream.read(65537))
            if disk.get('version')!=1 or type(disk.get('total')) is not int or disk['total']<0:return
            as_of=datetime.fromisoformat(disk['as_of'])
            if as_of.tzinfo is None or as_of>datetime.now(timezone.utc):return
            counts=disk['sources'];latest=disk['latest']
            if not isinstance(counts,dict) or not isinstance(latest,dict):return
            if any(not isinstance(k,str) or type(v) is not int or v<0 for k,v in counts.items()):return
            if any(v is not None and not isinstance(v,str) for v in latest.values()):return
            with closing(sqlite3.connect(self.path.resolve().as_uri()+'?mode=ro',uri=True,timeout=.1)) as db:
                db.row_factory=sqlite3.Row;db.execute('BEGIN')
                sources=[{**json.loads(r['config']),**{k:r[k] for k in ('status','detail','synced_at')},
                          'count':counts.get(r['id'],0),'latest':latest.get(r['id'])} for r in db.execute('SELECT * FROM sources ORDER BY rowid')]
                dossiers=[dict(r) for r in db.execute('SELECT id,title,modified FROM dossiers ORDER BY modified DESC')]
            self.value=dict(sources=sources,dossiers=dossiers,total=disk['total']);self.as_of=disk['as_of']
            self.changed.set()
        except (OSError, ValueError, KeyError, TypeError, sqlite3.Error):return

    def _read(self):
        with closing(sqlite3.connect(self.path.resolve().as_uri()+'?mode=ro', uri=True, timeout=.1)) as db:
            db.row_factory = sqlite3.Row
            db.execute('PRAGMA query_only=ON'); db.execute('BEGIN')
            rows = db.execute(SOURCE_SQL).fetchall()
            latest = dict(db.execute(LATEST_SQL))
            sources = [{**json.loads(r['config']), **{k:r[k] for k in ('status','detail','synced_at','count')},
                        'latest':latest.get(r['id'])} for r in rows]
            dossiers = [dict(r) for r in db.execute('SELECT id,title,modified FROM dossiers ORDER BY modified DESC')]
            total = db.execute(TOTAL_SQL).fetchone()[0]
        return dict(sources=sources, dossiers=dossiers, total=total)

    @staticmethod
    def counts(value):
        return dict(total=value['total'], sources={s['id']:s['count'] for s in value['sources']})

    def _persist(self, value, as_of):
        data = dict(version=1, as_of=as_of, latest={s['id']:s['latest'] for s in value['sources']}, **self.counts(value))
        self.sidecar.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix='.counts-', dir=self.sidecar.parent)
        try:
            with os.fdopen(fd, 'w') as stream:
                json.dump(data, stream, ensure_ascii=False);stream.flush();os.fsync(stream.fileno())
            os.replace(name, self.sidecar)
        finally:
            if os.path.exists(name):os.unlink(name)

    def refresh(self, *, blocking=True):
        # No snapshot lock is held during SQLite/file I/O. Readers retain the last watermark.
        if not self.refresh_lock.acquire(blocking=blocking):return None
        try:
            before = self.snapshot_value()
            with self.lock:generation = self.generation
            as_of = stamp();value = self._read()
            previous_counts = self.counts(before) if before is not None else None
            actual_counts = self.counts(value)
            # Also validate the persisted projection; a corrupt/tampered cache is observable.
            disk_counts = None
            try:
                disk = json.loads(self.sidecar.read_text());disk_counts = dict(total=disk['total'], sources=disk['sources'])
            except (OSError, ValueError, KeyError, TypeError): pass
            validation = dict(as_of=as_of, difference=previous_counts is not None and previous_counts != actual_counts,
                              persisted_difference=disk_counts is not None and disk_counts != actual_counts)
            persistence_error = None
            try:self._persist(value, as_of)
            except OSError:persistence_error='计数已更新；持久缓存暂未保存'
            with self.lock:
                self.value=value;self.as_of=as_of;self.dirty=self.generation != generation
                self.last_validation=validation;self.last_error=persistence_error
        finally:self.refresh_lock.release()
        return self.read()

    def snapshot_value(self):
        with self.lock:return copy.deepcopy(self.value)

    def invalidate(self):
        with self.lock:self.dirty=True;self.generation+=1
        self.health.invalidate();self.changed.set()

    def committed(self):
        self.invalidate()
        # Non-bulk writes refresh before their receipt; a busy cache never holds up a commit.
        # Bulk sync already calls fresh() once after all its durable source updates.
        if not self.knowledge.sync_lock.locked():
            try:self.refresh(blocking=False)
            except (OSError, ValueError, sqlite3.Error):
                with self.lock:self.last_error='元数据暂未更新，保留最近已知计数'

    def start(self):
        with self.lock:
            if self.worker is not None:return
            self.worker = threading.Thread(target=self._watch, name='raw-materials-meta', daemon=True)
            worker = self.worker
        worker.start()

    def _watch(self):
        deadline = time.monotonic()+REFRESH_SECONDS
        while not self.stop.is_set():
            if self.changed.is_set() or time.monotonic() >= deadline:
                self.changed.clear()
                try:
                    self.refresh()
                    with self.lock:dirty=self.dirty
                    if dirty:self.changed.set()
                except (OSError, ValueError, sqlite3.Error):
                    with self.lock:self.last_error='元数据暂未更新，保留最近已知计数'
                deadline=time.monotonic()+REFRESH_SECONDS
            self.stop.wait(.1)

    def read(self):
        with self.lock:
            value=copy.deepcopy(self.value);as_of=self.as_of;dirty=self.dirty
            validation=copy.deepcopy(self.last_validation);error=self.last_error
        if value is None:
            # First materialisation is local only; server startup primes this before listening.
            return self.refresh()
        health=self.health.snapshot(public=False)
        for source in value['sources']:
            if source.get('id')=='wechat' and source.get('backend')=='collector':source['capture']=health['capture']
        return {**value,'syncing':self.knowledge.sync_lock.locked(),'counts_as_of':as_of,'counts_stale':dirty,
                'meta_validation':validation,'meta_error':error,
                'health_as_of':health['as_of'],'health_state':health['state']}

    def fresh(self):
        try:self.refresh()
        except (OSError, ValueError, sqlite3.Error):
            if self.snapshot_value() is None:raise
            with self.lock:
                self.dirty=True;self.last_error='元数据暂未更新，保留最近已知计数'
        # Write callers keep their actual commit receipt; only the ancillary probe is bounded.
        self.health.request(force=True)
        return self.read()


def model(knowledge):
    with knowledge._raw_meta_guard:
        if knowledge._raw_meta is None:knowledge._raw_meta=MetadataCache(knowledge)
        return knowledge._raw_meta


def get_route(handler, parts):
    if parts.query:raise ValueError('原始资料状态参数无效')
    cache=model(handler.server.store.knowledge)
    if parts.path=='/api/knowledge/health':
        result=cache.health.request()
        return handler.reply(200,result)
    result=cache.read()
    result['sources']=[{k:public_text(v) if isinstance(v,str) else v for k,v in s.items() if k in SOURCE_FIELDS and (type(v) in (str,int,float,bool) or v is None)}
                       for s in result['sources']]
    result['dossiers']=[{**d,'title':public_text(d['title'])} for d in result['dossiers']]
    return handler.reply(200,result)

# Default-list ordering is exactly the existing CASE in Knowledge.search.
DEFAULT_KINDS = ('articles','projects','notes','mail')
DEFAULT_ORDER = "CASE d.kind WHEN 'articles' THEN 0 WHEN 'projects' THEN 1 WHEN 'notes' THEN 2 WHEN 'mail' THEN 3 ELSE 4 END,d.modified DESC,d.id"
_fallback_lock = threading.Lock()
_fallback_counts = {}


def default_list_fallback(reason, error_type=''):
    import logging
    with _fallback_lock:
        _fallback_counts[reason]=_fallback_counts.get(reason,0)+1
    # Controlled reason/type only: no original text, paths, arguments, or exception messages.
    logging.getLogger(__name__).warning('raw_materials_default_list_fallback reason=%s type=%s',reason,error_type)


def default_list_metrics():
    with _fallback_lock:return dict(_fallback_counts)


def clue_read_phase(query_string):
    from urllib.parse import parse_qs
    values = parse_qs(query_string, keep_blank_values=True).get('phase', ['all'])
    if len(values) != 1 or values[0] not in ('all', 'rows', 'facets'):
        raise ValueError('线索读取阶段无效')
    return values[0]


def clue_read_options(query_string):
    from urllib.parse import parse_qs
    phase = clue_read_phase(query_string)
    params = parse_qs(query_string, keep_blank_values=True)
    for name, maximum in (('offset', 2**63-1), ('limit', 100)):
        if name not in params: continue
        values = params[name]
        if len(values) != 1 or not re.fullmatch(r'[0-9]+', values[0]):
            raise ValueError('分页无效')
        try: value = int(values[0])
        except ValueError: raise ValueError('分页无效') from None
        minimum = 0 if name == 'offset' else 1
        if not minimum <= value <= maximum:
            raise ValueError('分页范围无效')
    # Default/valid legacy limit requests retain the original fixed page size.
    return {} if phase == 'all' else {'phase': phase}


def material_search_offset(query_string):
    from urllib.parse import parse_qs
    params = parse_qs(query_string, keep_blank_values=True)
    for name in ('offset', 'limit'):
        if name not in params: continue
        values = params[name]
        if len(values) != 1 or not re.fullmatch(r'[0-9]+', values[0]):
            raise ValueError('分页无效')
        try: value = int(values[0])
        except ValueError: raise ValueError('分页无效') from None
        if name == 'offset' and not 0 <= value <= 100000:
            raise ValueError('分页范围无效')
        if name == 'limit' and not 1 <= value <= 1000:
            raise ValueError('分页范围无效')
        # The legacy HTTP route still returns 30 rows; a valid limit was
        # historically ignored. Validate malformed input without changing it.
    return int(params.get('offset', ['0'])[0])


@contextmanager
def default_material_snapshot(connection, enabled=True):
    """Count and rows see one SQLite snapshot, including concurrent kind changes."""
    own_snapshot = enabled and not connection.in_transaction
    if own_snapshot: connection.execute('SAVEPOINT raw_materials_search')
    try:
        yield
    finally:
        if own_snapshot: connection.execute('RELEASE raw_materials_search')


def default_material_total(connection):
    # SQLite's unfiltered Count opcode uses the compact existing index. Only
    # trash entries require a join; NULL ids and orphan tombstones stay exact.
    return connection.execute('SELECT (SELECT COUNT(*) FROM documents) - '
                              '(SELECT COUNT(*) FROM document_trash t '
                              'JOIN documents d ON d.id=t.document_id)').fetchone()[0]


def default_material_rows(connection, offset, limit=30):
    from knowledge_store import NOT_TRASHED
    if type(offset) is not int or type(limit) is not int or offset<0 or not 1<=limit<=1000:
        raise ValueError('默认列表分页无效')
    if offset>=10000:
        # Keep the exact legacy statement for deep pages; no extra per-kind counts/sorts.
        default_list_fallback('deep_offset_original')
        return connection.execute('SELECT d.* FROM documents d WHERE '+NOT_TRASHED+
                                  ' ORDER BY '+DEFAULT_ORDER+' LIMIT ? OFFSET ?',(limit,offset)).fetchall()
    own_snapshot=not connection.in_transaction
    if own_snapshot:connection.execute('SAVEPOINT raw_materials_default')
    try:
        result=[];skip=offset
        for kind in (*DEFAULT_KINDS,None):
            predicate='d.kind=?' if kind else "(d.kind IS NULL OR d.kind NOT IN ('articles','projects','notes','mail'))"
            args=[kind] if kind else []
            if skip and kind is not None:
                count=connection.execute('SELECT COUNT(*) FROM documents d WHERE '+predicate+' AND '+NOT_TRASHED,args).fetchone()[0]
                if skip>=count:skip-=count;continue
            # Sort only row identifiers, never complete bodies/JSON payloads.
            # Fetch the selected page after its order is fixed.
            rows=connection.execute('SELECT d.* FROM documents d WHERE d.rowid IN ('
                                    'SELECT d.rowid FROM documents d WHERE '+predicate+' AND '+NOT_TRASHED+
                                    ' ORDER BY d.modified DESC,d.id LIMIT ? OFFSET ?) '
                                    'ORDER BY d.modified DESC,d.id',[*args,limit-len(result),skip]).fetchall()
            result.extend(rows);skip=0
            if len(result)==limit:break
        return result
    finally:
        if own_snapshot:connection.execute('RELEASE raw_materials_default')
