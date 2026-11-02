"""K1 shadow store. Legacy tables and downstream databases are never written here.

Future protocols (not materialised in K1): daily_rollups/topic_read_models,
triage_actions/destinations/delivery_outbox, own_posts/own_post_observations/
output_links/weekly_reviews, people_candidates, skill_drafts and topic_notes.
All future writers must use revision checks, immutable audit rows and batch journals.
"""
from __future__ import annotations
from contextlib import contextmanager
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import base64
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import tempfile
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
import uuid
from platform_registry import derive_platform

SCHEMA_VERSION = 1
PARSER_VERSION = 'k1-v1'
ROOT = Path(__file__).resolve().parent
REAL_DB = ROOT / 'workspace/clues/clues.db'
SAFE_INTEGER = 2**53 - 1
METRICS = ('views', 'likes', 'bookmarks', 'retweets', 'replies', 'quotes', 'followers')
DIRECTION_KEYS = ('models', 'local-inference', 'visual-models', 'api-engines', 'agent-coding',
                  'skill-security', 'local-agent-ecosystem', 'content-automation', 'embodied-3d')

def now():
    return datetime.now(timezone.utc).isoformat()

def dumps(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)

def digest(value):
    return hashlib.sha256(dumps(value).encode()).hexdigest()

def identity(*parts):
    return uuid.uuid5(uuid.NAMESPACE_URL, dumps(parts)).hex

def utc(value):
    if value is None or value == '':
        return None
    try:
        if type(value) in (int, float):
            stamp = datetime.fromtimestamp(value / 1000 if value > 10**11 else value, timezone.utc)
        else:
            try:
                stamp = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
            except ValueError:
                stamp = parsedate_to_datetime(str(value))
        return stamp.astimezone(timezone.utc).isoformat() if stamp.tzinfo is not None else None
    except (ValueError, TypeError, OverflowError, OSError):
        return None

_SECRET = re.compile(r'(?i)(?:sk-(?:proj-)?[a-z0-9_-]{12,}|(?:api[_-]?key|access[_-]?token|authorization|password)\s*[:=]\s*[^\s,;]+)')

def safe_text(value):
    text = value if isinstance(value, str) else ''
    text = re.sub(r'https?://[^\s<>\[\]()]+', lambda m: m[0] if safe_url(m[0]) else '[受限链接]', text)
    return _SECRET.sub('[已隐藏凭据]', text)

def safe_url(value):
    if not isinstance(value, str):
        return None
    try:
        p = urlsplit(value)
        if p.scheme not in ('http', 'https') or not p.hostname or p.username or p.password:
            return None
        if re.search(r'(?i)(login|signin|oauth|authorize|download.*wechat)', p.path):
            return None
        query = parse_qsl(p.query, keep_blank_values=True)
        if any(re.search(r'(?i)(key|token|secret|password|signature|credential|auth|session)', k) for k, _ in query):
            return None
        if _SECRET.search(value):
            return None
        query = [(k, v) for k, v in query if not k.lower().startswith('utm_') and k.lower() not in ('ref', 's', 't', 'fbclid')]
        return urlunsplit((p.scheme.lower(), p.netloc.lower(), p.path.rstrip('/'), urlencode(query), ''))
    except ValueError:
        return None

def safe_payload(value):
    if isinstance(value, str):
        return safe_text(value)
    if isinstance(value, list):
        return [safe_payload(v) for v in value]
    if isinstance(value, dict):
        return {k: safe_payload(v) for k, v in value.items() if not re.search(r'(?i)(password|token|api.?key|authorization|cookie)', k)}
    return value

def canonical(row, platform=None):
    """One registry entry point, with platform-specific object identity validation."""
    raw = str(row.get('id') or row.get('tweet_id') or row.get('topic_id') or '')
    url = safe_url(row.get('url'))
    p = urlsplit(url or '')
    host = (p.hostname or '').removeprefix('www.')
    inferred = derive_platform(raw)
    hint = {'twitter': 'x', 'xiaohongshu': 'xhs', 'linux.do': 'linuxdo'}.get(platform, platform)
    if host in ('x.com', 'twitter.com', 'mobile.twitter.com'):
        hint = 'x'
    elif host.endswith('douyin.com'):
        hint = 'douyin'
    elif host in ('xiaohongshu.com', 'xhslink.com'):
        hint = 'xhs'
    elif host == 'linux.do':
        hint = 'linuxdo'
    elif host == 'github.com':
        hint = 'github'
    platform = hint or inferred
    key = raw.split(':', 1)[-1]
    if platform == 'x':
        match = re.search(r'^/[^/]+/status/(\d{1,25})(?:/|$)', p.path)
        external = key if re.fullmatch(r'\d{1,25}', key) else None
        if match and external and external != match[1]:
            raise ValueError('id_url_conflict')
        external = external or (match[1] if match else None)
        if not external:
            raise ValueError('missing_x_id')
        return 'x:' + external, derive_platform('x:' + external), 'post', 'https://x.com/i/status/' + external
    if platform in ('douyin', 'xhs', 'linuxdo'):
        pattern = {'douyin': r'/video/(\d+)', 'xhs': r'/(?:explore|discovery/item)/([a-fA-F0-9]+)', 'linuxdo': r'/t/(?:[^/]+/)?(\d+)(?:/|$)'}[platform]
        match = re.search(pattern, p.path)
        if platform == 'linuxdo':
            key = str(row.get('topic_id') or (match[1] if match else key))
        elif match:
            if key and re.fullmatch(r'\d+' if platform == 'douyin' else r'[a-fA-F0-9]{16,}', key) and key != match[1]:
                raise ValueError('id_url_conflict')
            key = match[1]
        if re.fullmatch(r'\d+' if platform != 'xhs' else r'[a-fA-F0-9]{16,}', key):
            canonical_key = platform + ':' + key
            return canonical_key, derive_platform(canonical_key), 'topic' if platform == 'linuxdo' else 'video', url
    if platform == 'github':
        bits = p.path.strip('/').split('/')
        if len(bits) >= 2:
            repo = '/'.join(bits[:2]).lower().removesuffix('.git')
            kind, number = 'repo', ''
            if len(bits) >= 4 and bits[2] in ('issues', 'pull') and bits[3].isdigit():
                kind, number = ('issue' if bits[2] == 'issues' else 'pr'), bits[3]
            elif len(bits) >= 5 and bits[2:4] == ['releases', 'tag']:
                kind, number = 'release', '/'.join(bits[4:])
            elif len(bits) > 2:
                # A path that isn't a repository/release/issue/PR is its own web object.
                return 'web:' + digest(url), derive_platform('web:' + digest(url)), 'page', url
            canonical_key = 'github:' + repo + ':' + kind + (':' + number if number else '')
            return canonical_key, derive_platform(canonical_key), kind, url
    if url:
        return 'web:' + digest(url), derive_platform('web:' + digest(url)), 'page', url
    raise ValueError('missing_identity')

def raw_metrics(row):
    metrics = row.get('public_metrics') or row.get('metrics') or row
    names = {'views': ('views', 'impression_count', 'plays'), 'likes': ('likes', 'like_count'),
             'bookmarks': ('bookmarks', 'bookmark_count', 'collects'), 'retweets': ('retweets', 'repost_count', 'retweet_count', 'shares'),
             'replies': ('replies', 'reply_count', 'comments'), 'quotes': ('quotes', 'quote_count'),
             'followers': ('followers', 'author_followers', 'author_fans')}
    raw, reasons = {}, {}
    for name, keys in names.items():
        metric_source = {**row, **metrics} if name == 'followers' else metrics
        value = next((metric_source[k] for k in keys if k in metric_source), None)
        if type(value) is int and value >= 0:
            raw[name] = value
        elif isinstance(value, str) and re.fullmatch(r'\d+', value):
            raw[name] = int(value)
        else:
            raw[name] = None
            reasons[name] = 'not_provided' if value is None else 'not_raw_nonnegative_integer'
    return raw, reasons

def wire_numbers(value):
    if type(value) is int and abs(value) > SAFE_INTEGER:
        return str(value)
    if isinstance(value, list):
        return [wire_numbers(x) for x in value]
    if isinstance(value, dict):
        return {k: wire_numbers(v) for k, v in value.items()}
    return value

def assert_write_path(path, allow_real=False):
    resolved = Path(path).resolve()
    if allow_real and resolved == REAL_DB.resolve():
        return resolved
    if any(resolved.is_relative_to(p.resolve()) for p in (Path('/tmp'), Path(tempfile.gettempdir()))) and not resolved.is_relative_to((ROOT / 'workspace').resolve()):
        return resolved
    raise ValueError('K1_ISOLATION: 写入必须使用独立 /tmp 副本；真实迁移须显式 --allow-real')

# Every mutable row has an owner batch. Changes to existing rows are compare-and-swap journalled.
COLUMNS = {
 'clue_objects': "canonical_key TEXT NOT NULL UNIQUE,platform TEXT NOT NULL,kind TEXT NOT NULL,title TEXT NOT NULL,summary TEXT NOT NULL,author_key TEXT,published_at TEXT,first_seen_at TEXT NOT NULL,last_seen_at TEXT NOT NULL,content_complete INTEGER NOT NULL,incomplete_reason TEXT,verification_state TEXT NOT NULL DEFAULT 'unverified',triage_state TEXT NOT NULL DEFAULT 'pending',revision INTEGER NOT NULL DEFAULT 1",
 'legacy_object_links': "thread_id TEXT NOT NULL,clue_id TEXT NOT NULL REFERENCES clue_objects(id),mapping_reason TEXT NOT NULL,mapping_state TEXT NOT NULL,decision_id TEXT,legacy_state_json TEXT NOT NULL,UNIQUE(thread_id,clue_id)",
 'clue_aliases': "alias TEXT NOT NULL UNIQUE,clue_id TEXT NOT NULL REFERENCES clue_objects(id),kind TEXT NOT NULL",
 'clue_relations': "from_id TEXT NOT NULL REFERENCES clue_objects(id),to_key TEXT NOT NULL,relation_type TEXT NOT NULL,evidence_json TEXT NOT NULL,UNIQUE(from_id,to_key,relation_type)",
 'clue_content_versions': "clue_id TEXT NOT NULL REFERENCES clue_objects(id),channel TEXT NOT NULL,content_hash TEXT NOT NULL,body TEXT NOT NULL,rich_json TEXT NOT NULL,content_complete INTEGER NOT NULL,incomplete_reason TEXT,origin_receipt_id TEXT NOT NULL REFERENCES source_receipts(id),privacy_scope TEXT NOT NULL CHECK(privacy_scope='public'),UNIQUE(clue_id,channel,content_hash)",
 'clue_evidence': "clue_id TEXT NOT NULL REFERENCES clue_objects(id),origin_receipt_id TEXT NOT NULL REFERENCES source_receipts(id),source_locator TEXT NOT NULL,privacy_scope TEXT NOT NULL,reference_json TEXT NOT NULL",
 'source_receipts': "clue_id TEXT REFERENCES clue_objects(id),channel TEXT NOT NULL,record_key TEXT NOT NULL,source_file TEXT NOT NULL,file_hash TEXT NOT NULL,source_version TEXT NOT NULL,parser_version TEXT NOT NULL,first_seen_at TEXT NOT NULL,last_seen_at TEXT NOT NULL,import_status TEXT NOT NULL,error TEXT,UNIQUE(channel,record_key,source_version)",
 'clue_channels': "clue_id TEXT NOT NULL REFERENCES clue_objects(id),channel TEXT NOT NULL,source TEXT NOT NULL,source_id TEXT NOT NULL,bookmarked_at TEXT,first_seen_at TEXT NOT NULL,last_seen_at TEXT NOT NULL,metadata_json TEXT NOT NULL,UNIQUE(clue_id,channel)",
 'clue_bookmark_folders': "clue_id TEXT NOT NULL REFERENCES clue_objects(id),folder TEXT NOT NULL,origin_receipt_id TEXT NOT NULL REFERENCES source_receipts(id),UNIQUE(clue_id,folder)",
 'metric_observations': "clue_id TEXT NOT NULL REFERENCES clue_objects(id),channel TEXT NOT NULL,observed_at TEXT,raw_metrics_json TEXT NOT NULL,missing_reasons_json TEXT NOT NULL,raw_hash TEXT NOT NULL,quality TEXT NOT NULL,origin_receipt_id TEXT NOT NULL REFERENCES source_receipts(id),UNIQUE(clue_id,channel,observed_at,raw_hash)",
 'content_events': "title TEXT NOT NULL,summary TEXT NOT NULL,occurred_at TEXT,revision INTEGER NOT NULL DEFAULT 1",
 'event_members': "event_id TEXT NOT NULL REFERENCES content_events(id),clue_id TEXT NOT NULL REFERENCES clue_objects(id),state TEXT NOT NULL,decision_id TEXT",
 'merge_proposals': "member_ids_json TEXT NOT NULL,confidence REAL NOT NULL CHECK(confidence>=0 AND confidence<=1),reason TEXT NOT NULL,rule_version TEXT NOT NULL,model_version TEXT,suggested_at TEXT NOT NULL,state TEXT NOT NULL DEFAULT 'suggested',revision INTEGER NOT NULL DEFAULT 1",
 'merge_decisions': "proposal_id TEXT REFERENCES merge_proposals(id),decision TEXT NOT NULL CHECK(decision IN ('confirmed','rejected','split')),actor TEXT NOT NULL,decided_at TEXT NOT NULL,before_json TEXT NOT NULL,after_json TEXT NOT NULL,revision INTEGER NOT NULL DEFAULT 1",
 'topics': "parent_id TEXT REFERENCES topics(id),kind TEXT NOT NULL CHECK(kind IN ('direction','subtopic','collection')),name TEXT NOT NULL,interest_key TEXT UNIQUE,sort INTEGER NOT NULL,revision INTEGER NOT NULL DEFAULT 1",
 'topic_closure': "ancestor_id TEXT NOT NULL REFERENCES topics(id),descendant_id TEXT NOT NULL REFERENCES topics(id),depth INTEGER NOT NULL CHECK(depth BETWEEN 0 AND 5),UNIQUE(ancestor_id,descendant_id)",
 'topic_assignments': "clue_id TEXT NOT NULL REFERENCES clue_objects(id),topic_id TEXT NOT NULL REFERENCES topics(id),state TEXT NOT NULL CHECK(state IN ('suggested','confirmed','rejected')),confidence REAL CHECK(confidence>=0 AND confidence<=1),reason TEXT NOT NULL,model_version TEXT,confirmed_by TEXT,UNIQUE(clue_id,topic_id)",
 'tags': "name TEXT NOT NULL UNIQUE,aliases_json TEXT NOT NULL",
 'clue_tags': "clue_id TEXT NOT NULL REFERENCES clue_objects(id),tag_id TEXT NOT NULL REFERENCES tags(id),state TEXT NOT NULL,UNIQUE(clue_id,tag_id)",
 'resource_links': "clue_id TEXT NOT NULL REFERENCES clue_objects(id),resource_type TEXT NOT NULL,url TEXT NOT NULL,version TEXT,license TEXT,evidence_id TEXT,UNIQUE(clue_id,url,resource_type)",
 'ingest_checkpoints': "source_file TEXT NOT NULL,source TEXT NOT NULL,source_version TEXT NOT NULL,committed_at TEXT NOT NULL,counts_json TEXT NOT NULL,UNIQUE(source_file,source)",
}
INDEXES = (
 'CREATE INDEX IF NOT EXISTS v2_objects_triage ON clue_objects(triage_state,last_seen_at DESC,id DESC)',
 'CREATE INDEX IF NOT EXISTS v2_objects_platform ON clue_objects(platform,published_at,id)',
 'CREATE INDEX IF NOT EXISTS v2_channels ON clue_channels(channel,clue_id)',
 'CREATE INDEX IF NOT EXISTS v2_channel_source ON clue_channels(source,clue_id)',
 'CREATE INDEX IF NOT EXISTS v2_event_members ON event_members(event_id,state,clue_id)',
 "CREATE UNIQUE INDEX IF NOT EXISTS v2_primary_event ON event_members(clue_id) WHERE state='primary'",
 'CREATE INDEX IF NOT EXISTS v2_assignments ON topic_assignments(topic_id,state,clue_id)',
 'CREATE INDEX IF NOT EXISTS v2_observations ON metric_observations(clue_id,observed_at)',
 'CREATE INDEX IF NOT EXISTS v2_receipt_object ON source_receipts(clue_id)',
 'CREATE INDEX IF NOT EXISTS v2_versions ON clue_content_versions(clue_id)',
 'CREATE INDEX IF NOT EXISTS v2_evidence ON clue_evidence(clue_id)',
 'CREATE INDEX IF NOT EXISTS v2_legacy ON legacy_object_links(clue_id)',
)

def connect(path, readonly=False):
    db = sqlite3.connect('file:' + str(Path(path).resolve()) + ('?mode=ro' if readonly else '?mode=rw'), uri=True, timeout=15)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    return db

@contextmanager
def transaction(path, allow_real=False):
    path = assert_write_path(path, allow_real)
    db = connect(path)
    try:
        db.execute('BEGIN IMMEDIATE')
        yield db
        db.commit()
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()

@contextmanager
def _completion_transaction(path, allow_real):
    db = connect(assert_write_path(path, allow_real))
    try:
        db.execute('PRAGMA busy_timeout=3000')
        db.execute('BEGIN IMMEDIATE')
        yield db
        db.commit()
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()


def complete_batch(path, batch_id, *, confirmed=False, allow_real=False, source_names=()):
    """Seal an explicitly successful run; derive counts from committed checkpoints.

    No business/audit rows are modified. A failed caller must never confirm.
    Repeating a seal with unchanged committed data is a byte-for-byte no-op.
    """
    if confirmed is not True:
        raise ValueError('批次尚未确认完成')
    with _completion_transaction(path, allow_real) as db:
        row = db.execute('SELECT * FROM migrations WHERE id=?', (batch_id,)).fetchone()
        if not row or row['status'].startswith('rolled_back') or row['status'] == 'failed':
            raise ValueError('批次不可封口')
        if db.execute("SELECT 1 FROM source_receipts WHERE batch_id=? AND record_key='__file__' AND import_status='quarantined' LIMIT 1", (batch_id,)).fetchone():
            raise ValueError('存在未完成来源文件')
        prior = json.loads(row['counts_json'])
        prior = prior.get('sources', prior)
        known = set(source_names) | {k for k,v in prior.items() if isinstance(v,dict) and 'files' in v}
        sources = {k:dict(files=0,read=0,new=0,reused=0,bad=0,no_time=0,skipped=0,bad_files=0) for k in known}
        manifest = {}
        for checkpoint in db.execute('SELECT * FROM ingest_checkpoints WHERE batch_id=? ORDER BY source,source_file', (batch_id,)):
            value = json.loads(checkpoint['counts_json'])
            if value.get('bad_files', 0):
                raise ValueError('存在未完成来源文件')
            target = sources.setdefault(checkpoint['source'], {'files': 0})
            target['files'] += 1
            for key, count in value.items():
                if type(count) is int:
                    target[key] = target.get(key, 0) + count
            manifest[checkpoint['source_file']] = checkpoint['source_version']
        counts = dict(sources=sources, tables={table: db.execute('SELECT COUNT(*) FROM '+table+' WHERE batch_id=?', (batch_id,)).fetchone()[0] for table in COLUMNS})
        values = dumps(counts), dumps(manifest)
        if (row['status'], row['counts_json'], row['hash_manifest_json']) != ('completed', *values):
            db.execute("UPDATE migrations SET status='completed',counts_json=?,hash_manifest_json=?,updated_at=? WHERE id=?", (*values, now(), batch_id))
        return counts

def schema(path, batch_id, topic_names, allow_real=False):
    if len(topic_names) != 9 or len(set(topic_names)) != 9:
        raise ValueError('K1 requires exactly nine existing interest directions')
    with transaction(path, allow_real) as db:
        db.execute('CREATE TABLE IF NOT EXISTS migrations(id TEXT PRIMARY KEY,schema_version INTEGER NOT NULL,status TEXT NOT NULL,cursor TEXT,counts_json TEXT NOT NULL,hash_manifest_json TEXT NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL)')
        db.execute('CREATE TABLE IF NOT EXISTS migration_changes(seq INTEGER PRIMARY KEY,batch_id TEXT NOT NULL,table_name TEXT NOT NULL,row_id TEXT NOT NULL,before_json TEXT,after_json TEXT NOT NULL)')
        db.execute('CREATE INDEX IF NOT EXISTS v2_changes_batch ON migration_changes(batch_id,seq)')
        db.execute('CREATE INDEX IF NOT EXISTS v2_changes_row ON migration_changes(batch_id,table_name,row_id)')
        for table, columns in COLUMNS.items():
            # Table constraints must follow all columns.
            parts = columns.split(',UNIQUE(')
            fields = parts[0] + ',batch_id TEXT NOT NULL,schema_version INTEGER NOT NULL,created_at TEXT NOT NULL'
            if len(parts) > 1:
                fields += ',UNIQUE(' + ',UNIQUE('.join(parts[1:])
            db.execute(f'CREATE TABLE IF NOT EXISTS {table}(id TEXT PRIMARY KEY,{fields})')
        for sql in INDEXES:
            db.execute(sql)
        prior = db.execute('SELECT status FROM migrations WHERE id=?', (batch_id,)).fetchone()
        if prior and prior['status'].startswith('rolled_back'):
            raise ValueError('已回滚批次请换新 batch_id')
        db.execute("INSERT OR IGNORE INTO migrations VALUES(?,?,'ready',NULL,'{}','{}',?,?)", (batch_id, SCHEMA_VERSION, now(), now()))
        for i, (key, name) in enumerate(zip(DIRECTION_KEYS, topic_names)):
            tid = 'direction:' + key
            write(db, 'topics', tid, dict(parent_id=None, kind='direction', name=safe_text(name), interest_key=key, sort=i, revision=1), batch_id)
            write(db, 'topic_closure', identity(tid, tid), dict(ancestor_id=tid, descendant_id=tid, depth=0), batch_id)
        write(db, 'topics', 'collection:tools', dict(parent_id=None, kind='collection', name='工具', interest_key=None, sort=99, revision=1), batch_id)
        write(db, 'topic_closure', identity('collection:tools'), dict(ancestor_id='collection:tools', descendant_id='collection:tools', depth=0), batch_id)

def write(db, table, row_id, values, batch_id, update=False):
    if table not in COLUMNS:
        raise ValueError('非影子表禁止写入')
    old = db.execute(f'SELECT * FROM {table} WHERE id=?', (row_id,)).fetchone()
    if old and not update:
        return False
    before = dict(old) if old else None
    after = {**(before or dict(id=row_id, batch_id=batch_id, schema_version=SCHEMA_VERSION, created_at=now())), **values}
    if before == after:
        return False
    keys = list(after)
    if before:
        db.execute(f'UPDATE {table} SET ' + ','.join(k + '=?' for k in keys if k != 'id') + ' WHERE id=?', [after[k] for k in keys if k != 'id'] + [row_id])
    else:
        db.execute(f'INSERT INTO {table}(' + ','.join(keys) + ') VALUES(' + ','.join('?' for _ in keys) + ')', [after[k] for k in keys])
    # Persist the complete row including SQL DEFAULTs, for safe compare-and-swap rollback.
    after = dict(db.execute(f'SELECT * FROM {table} WHERE id=?', (row_id,)).fetchone())
    db.execute('INSERT INTO migration_changes(batch_id,table_name,row_id,before_json,after_json) VALUES(?,?,?,?,?)', (batch_id, table, row_id, dumps(before) if before else None, dumps(after)))
    return not bool(before)

def rollback(path, batch_id, allow_real=False):
    removed = restored = 0
    retained = []
    deferred = []
    with transaction(path, allow_real) as db:
        if not db.execute('SELECT 1 FROM migrations WHERE id=?', (batch_id,)).fetchone():
            raise ValueError('批次不存在')
        for change in db.execute('SELECT * FROM migration_changes WHERE batch_id=? ORDER BY seq DESC', (batch_id,)).fetchall():
            table, row_id = change['table_name'], change['row_id']
            if table not in COLUMNS:
                raise ValueError('非法回滚表')
            row = db.execute(f'SELECT * FROM {table} WHERE id=?', (row_id,)).fetchone()
            if not row:
                continue
            if dumps(dict(row)) != change['after_json']:
                retained.append(dict(table=table, id=row_id, reason='changed_after_batch'))
                continue
            try:
                before = json.loads(change['before_json']) if change['before_json'] else None
                if before:
                    keys = [k for k in before if k != 'id']
                    db.execute(f'UPDATE {table} SET ' + ','.join(k + '=?' for k in keys) + ' WHERE id=?', [before[k] for k in keys] + [row_id]); restored += 1
                else:
                    db.execute(f'DELETE FROM {table} WHERE id=?', (row_id,)); removed += 1
            except sqlite3.IntegrityError:
                deferred.append((table, row_id, change['after_json']))
        while deferred:
            pending = []
            for table, row_id, after in deferred:
                current = db.execute(f'SELECT * FROM {table} WHERE id=?', (row_id,)).fetchone()
                if not current:
                    continue
                if dumps(dict(current)) != after:
                    retained.append(dict(table=table, id=row_id, reason='changed_after_batch'))
                    continue
                try:
                    db.execute(f'DELETE FROM {table} WHERE id=?', (row_id,)); removed += 1
                except sqlite3.IntegrityError:
                    pending.append((table,row_id,after))
            if len(pending) == len(deferred):
                retained.extend(dict(table=t,id=i,reason='referenced_by_later_data') for t,i,_ in pending)
                break
            deferred = pending
        db.execute('UPDATE migrations SET status=?,counts_json=?,updated_at=? WHERE id=?', ('rolled_back_with_retained' if retained else 'rolled_back', dumps(dict(removed=removed, restored=restored, retained=retained)), now(), batch_id))
    return dict(removed=removed, restored=restored, retained=retained)


def propose_merge(db, member_ids, confidence, reason, batch_id, rule_version='k1-explicit-v1', model_version=None):
    """Suggestion only. No content_event/member mutation; no low confidence inbox spam."""
    ids = sorted(set(member_ids))
    if len(ids) < 2 or not .9 <= confidence <= 1 or not safe_text(reason):
        raise ValueError('需要至少两个对象、明确理由及>=0.9置信度')
    for cid in ids:
        if not db.execute('SELECT 1 FROM clue_objects WHERE id=?', (cid,)).fetchone():
            raise ValueError('建议成员不存在')
    pid = identity('merge', ids)
    if db.execute("SELECT 1 FROM merge_decisions WHERE proposal_id=? AND decision IN ('rejected','split')", (pid,)).fetchone():
        return None
    write(db, 'merge_proposals', pid, dict(member_ids_json=dumps(ids), confidence=confidence, reason=safe_text(reason), rule_version=rule_version, model_version=model_version, suggested_at=now(), state='suggested', revision=1), batch_id)
    return pid


def inbox(path, limit=30, cursor=None, source=None, channel=None, status=None):
    if not 1 <= limit <= 100:
        raise ValueError('limit 必须为1–100')
    if status and status not in ('pending', 'discarded', 'archived', 'triaged'):
        raise ValueError('未知状态')
    if not Path(path).exists():
        return dict(items=[], next_cursor=None, schema_version=SCHEMA_VERSION, ready=False)
    db = connect(path, True)
    try:
        db.execute('BEGIN')
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name='clue_objects'").fetchone():
            return dict(items=[], next_cursor=None, schema_version=SCHEMA_VERSION, ready=False)
        where, args = [], []
        if source:
            where.append('EXISTS(SELECT 1 FROM clue_channels c WHERE c.clue_id=o.id AND c.source=?)');args.append(source)
        if channel:
            where.append('EXISTS(SELECT 1 FROM clue_channels c WHERE c.clue_id=o.id AND c.channel=?)');args.append(channel)
        if status:
            where.append('o.triage_state=?');args.append(status)
        if cursor:
            if len(cursor)>512:
                raise ValueError('cursor 过长')
            try:
                value = json.loads(base64.urlsafe_b64decode(cursor + '=' * (-len(cursor) % 4)))
                if not isinstance(value, list) or len(value) != 2 or not all(isinstance(x, str) for x in value):
                    raise ValueError()
                where.append('(o.last_seen_at,o.id)<(?,?)');args.extend(value)
            except Exception as exc:
                raise ValueError('cursor 无效') from exc
        sql = 'SELECT o.* FROM clue_objects o' + (' WHERE ' + ' AND '.join(where) if where else '') + ' ORDER BY o.last_seen_at DESC,o.id DESC LIMIT ?'
        rows = [dict(r) for r in db.execute(sql, args + [limit + 1])]
        next_cursor = base64.urlsafe_b64encode(dumps([rows[limit-1]['last_seen_at'], rows[limit-1]['id']]).encode()).decode().rstrip('=') if len(rows) > limit else None
        rows = rows[:limit]
        for row in rows:
            row.pop('batch_id', None)
            row['channels'] = [dict(r) for r in db.execute('SELECT channel,source FROM clue_channels WHERE clue_id=? ORDER BY channel', (row['id'],))]
        return wire_numbers(dict(items=rows, next_cursor=next_cursor, schema_version=SCHEMA_VERSION, ready=True))
    finally:
        db.close()


def detail(path, clue_id):
    if not Path(path).exists():
        return None
    db = connect(path, True)
    try:
        db.execute('BEGIN')
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name='clue_objects'").fetchone():
            return None
        row = db.execute('SELECT * FROM clue_objects WHERE id=? OR canonical_key=?', (clue_id, clue_id)).fetchone()
        if not row:
            return None
        result = dict(row)
        cid = row['id']
        for key, table, col in [('channels','clue_channels','clue_id'), ('aliases','clue_aliases','clue_id'), ('content_versions','clue_content_versions','clue_id'), ('observations','metric_observations','clue_id'), ('legacy_links','legacy_object_links','clue_id'), ('evidence','clue_evidence','clue_id'), ('relations','clue_relations','from_id'), ('topic_assignments','topic_assignments','clue_id'), ('folders','clue_bookmark_folders','clue_id'), ('resources','resource_links','clue_id')]:
            result[key] = []
            for r in db.execute(f'SELECT * FROM {table} WHERE {col}=? ORDER BY id', (cid,)):
                item = dict(r)
                for name in list(item):
                    if name.endswith('_json'):
                        item[name[:-5]] = json.loads(item.pop(name))
                result[key].append(item)
        result['merge_proposals'] = [dict(r) for r in db.execute('SELECT p.* FROM merge_proposals p,json_each(p.member_ids_json) m WHERE m.value=?', (cid,))]
        return wire_numbers(result)
    finally:
        db.close()


def verify_snapshot_rows(live_path, snapshot_path, tables=None, *, include_new_keys=(), snapshot_manifest=None):
    """Read-only protection of every row present in a consistent SQLite snapshot.

    Later inserts are reported separately. Changed/deleted snapshot rows fail.
    Callers may pass a second SQLite-backup copy as live_path to avoid long
    read transactions on the production database while hashing large stores.
    """
    def quote(name):
        return '"' + str(name).replace('"', '""') + '"'
    def hashed_rows(rows):
        h = hashlib.sha256(); count = 0
        for row in rows:
            for value in row:
                raw = b'B' + value if isinstance(value, bytes) else json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode()
                h.update(str(len(raw)).encode() + b':' + raw)
            count += 1
        return count, h.hexdigest()
    db = connect(live_path, True)
    try:
        db.execute('ATTACH DATABASE ? AS frozen', ('file:' + str(Path(snapshot_path).resolve()) + '?mode=ro',))
        db.execute('BEGIN')
        names = [r[0] for r in db.execute("SELECT name FROM frozen.sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        if tables is not None:
            wanted = set(tables)
            if not wanted <= set(names):
                raise ValueError('快照缺少保护表')
            names = [name for name in names if name in wanted]
        result = {}
        for name in names:
            q = quote(name)
            info = db.execute('PRAGMA frozen.table_info(' + q + ')').fetchall()
            keys = [r['name'] for r in sorted(info, key=lambda r: r['pk']) if r['pk']] or ['rowid']
            if keys != ['rowid'] and db.execute('SELECT 1 FROM frozen.' + q + ' WHERE ' + ' OR '.join(quote(k) + ' IS NULL' for k in keys) + ' LIMIT 1').fetchone():
                keys = ['rowid']  # SQLite rowid tables can contain nullable TEXT primary keys.
            # '=' is intentional: FTS5 does not use its rowid point index for IS.
            predicate = ' AND '.join('l.' + quote(k) + ' = b.' + quote(k) for k in keys)
            order = ','.join(quote(k) for k in keys)
            if snapshot_manifest is not None:
                reference = snapshot_manifest[name]
                expected_count, expected_sha = reference['count'], reference['sha256']
                if type(expected_count) is not int or expected_count < 0 or not isinstance(expected_sha, str) or not re.fullmatch(r'[0-9a-f]{64}', expected_sha):
                    raise ValueError('快照摘要格式无效')
            else:
                expected_count, expected_sha = hashed_rows(db.execute('SELECT * FROM frozen.' + q + ' ORDER BY ' + order))
            live_exists = db.execute("SELECT 1 FROM main.sqlite_master WHERE type='table' AND name=?", (name,)).fetchone()
            if not live_exists:
                result[name] = dict(equal=False, snapshot_count=expected_count, protected_count=0, snapshot_sha256=expected_sha, protected_sha256=None, new_count=0, new_keys=[])
                continue
            live_count, live_sha = hashed_rows(db.execute('SELECT l.* FROM main.' + q + ' l JOIN frozen.' + q + ' b ON ' + predicate + ' ORDER BY ' + ','.join('l.' + quote(k) for k in keys)))
            new_where = ' NOT EXISTS(SELECT 1 FROM frozen.' + q + ' b WHERE ' + predicate + ')'
            new_count = db.execute('SELECT COUNT(*) FROM main.' + q + ' l WHERE ' + new_where).fetchone()[0]
            new_keys = []
            if name in include_new_keys:
                fields = ','.join('l.' + quote(k) + ' AS ' + quote(k) for k in keys)
                new_keys = [dict(r) for r in db.execute('SELECT ' + fields + ' FROM main.' + q + ' l WHERE ' + new_where + ' ORDER BY ' + ','.join('l.' + quote(k) for k in keys))]
            result[name] = dict(equal=(live_count == expected_count and live_sha == expected_sha), snapshot_count=expected_count, protected_count=live_count,
                                snapshot_sha256=expected_sha, protected_sha256=live_sha, new_count=new_count, new_keys=new_keys)
        return dict(equal=all(item['equal'] for item in result.values()), tables=result)
    finally:
        db.close()
