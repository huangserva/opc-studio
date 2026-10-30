"""素材库短词标题目录。知识库连接始终 mode=ro；可删除此旁路库重建。"""
from __future__ import annotations

import json
from contextlib import closing
import logging
import os
from pathlib import Path
import sqlite3
import threading
import time

LOG = logging.getLogger(__name__)
SYNC_INTERVAL = 60
SYNC_SECONDS = .15
VERSION = 1
_guard = threading.Lock()
_states = {}


def _paths(root):
    root = Path(root).resolve()
    if (root / 'workspace').is_dir():
        root /= 'workspace'
    return root / 'knowledge/knowledge.db', root / 'items/research_catalog.db'


def _state(path):
    with _guard:
        return _states.setdefault(str(path), {'lock': threading.Lock(), 'building': False, 'retry_at': 0})


def _identity(path):
    stat = path.stat()
    return [str(path), stat.st_dev, stat.st_ino]


def _open(path):
    c = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=.02)
    c.row_factory = sqlite3.Row
    return c


def _metadata(c, knowledge):
    value = json.loads(c.execute('SELECT value FROM metadata WHERE key=1').fetchone()[0])
    if value['version'] != VERSION or value['identity'] != _identity(knowledge):
        raise ValueError('标题目录来源已改变')
    return value


def _save(c, meta):
    c.execute('INSERT OR REPLACE INTO metadata VALUES(1,?)', (json.dumps(meta),))


def _trash(c):
    # Hash actual membership, including same-count replacements and restore/retrash.
    # Resolving the primary-key ids in knowledge avoids persisting another id copy.
    rows = c.execute('SELECT d.rowid FROM document_trash t JOIN documents d ON d.id=t.document_id ORDER BY d.rowid').fetchall()
    return [r[0] for r in rows]


def _replace_trash(c, rowids):
    c.execute('UPDATE titles SET trashed=0 WHERE trashed=1')
    c.executemany('UPDATE titles SET trashed=1 WHERE rowid=?', [(r,) for r in rowids])


def build_catalog(root):
    """Build in a sibling file, then publish atomically; never expose half a catalog."""
    from items_store import readonly
    knowledge, path = _paths(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + f'.building-{os.getpid()}-{threading.get_ident()}')
    start = time.monotonic()
    try:
        with closing(readonly(knowledge)) as source, closing(sqlite3.connect(temp)) as dest, dest:
            source.execute('BEGIN')  # metadata, rowids and trash belong to one snapshot
            dest.executescript('''CREATE TABLE titles(rowid INTEGER PRIMARY KEY,source TEXT,title TEXT,modified TEXT,trashed INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE metadata(key INTEGER PRIMARY KEY,value TEXT NOT NULL);
                CREATE INDEX titles_trashed ON titles(trashed) WHERE trashed=1;''')
            watermark = 0
            cursor = source.execute('SELECT rowid,source,title,modified FROM documents ORDER BY rowid')
            while rows := cursor.fetchmany(1024):
                dest.executemany('INSERT INTO titles(rowid,source,title,modified) VALUES(?,?,?,?)', rows)
                watermark = rows[-1][0]
            trash = _trash(source)
            _replace_trash(dest, trash)
            meta = dict(version=VERSION, identity=_identity(knowledge), watermark=watermark,
                        trash=trash, last_sync=time.time(), build_seconds=time.monotonic()-start)
            _save(dest, meta)
        os.replace(temp, path)
        LOG.info('素材标题目录构建完成 %.3fs / %s bytes', time.monotonic()-start, path.stat().st_size)
        return meta
    finally:
        temp.unlink(missing_ok=True)


def start_catalog(root, rebuild=False):
    """Nonblocking startup/recovery, also used by requests while a catalog is absent."""
    knowledge, path = _paths(root)
    if not knowledge.exists():
        return 'unavailable'
    state = _state(path)
    if not state['lock'].acquire(blocking=False):
        return 'building' if state['building'] else 'ready' if path.exists() else 'unavailable'
    try:
        if not rebuild and path.exists():
            try:
                with closing(_open(path)) as c:
                    _metadata(c, knowledge)
                return 'ready'
            except (sqlite3.DatabaseError, ValueError, TypeError, KeyError):
                LOG.warning('素材标题目录不可用，将后台重建', exc_info=True)
        if time.monotonic() < state['retry_at']:
            return 'unavailable'
        if rebuild or path.exists():
            path.unlink(missing_ok=True)
        state['building'] = True
    except OSError:
        LOG.warning('素材标题目录无法创建，使用限时扫描', exc_info=True)
        return 'unavailable'
    finally:
        if not state['building']:
            state['lock'].release()
    # Ownership passes to a daemon; no query waits on a full corpus build.
    def run():
        try:
            build_catalog(root)
        except Exception:
            state['retry_at'] = time.monotonic() + 5
            LOG.exception('素材标题目录后台构建失败，使用限时扫描')
        finally:
            state['building'] = False
            state['lock'].release()
    threading.Thread(target=run, name='research-catalog', daemon=True).start()
    return 'building'


def _sync(root, source):
    """Only rows above the committed watermark; bound catch-up work per request."""
    knowledge, path = _paths(root)
    state = _state(path)
    if not state['lock'].acquire(blocking=False):
        return
    deadline = time.monotonic() + SYNC_SECONDS
    try:
        with closing(sqlite3.connect(path, timeout=.02)) as c, c:
            meta = _metadata(c, knowledge)
            if time.time() - meta['last_sync'] < SYNC_INTERVAL:
                return
            source.set_progress_handler(lambda: int(time.monotonic() >= deadline), 256)
            try:
                # Fetch small complete batches; only advance the watermark after INSERT.
                while time.monotonic() < deadline:
                    rows = source.execute('SELECT rowid,source,title,modified FROM documents WHERE rowid>? ORDER BY rowid LIMIT 512', (meta['watermark'],)).fetchall()
                    c.executemany('INSERT OR REPLACE INTO titles(rowid,source,title,modified) VALUES(?,?,?,?)', rows)
                    if rows:
                        meta['watermark'] = rows[-1][0]
                    if len(rows) < 512:
                        trash = _trash(source)
                        if trash != meta['trash']:
                            _replace_trash(c, trash)
                            meta['trash'] = trash
                        meta['last_sync'] = time.time()
                        break
            except sqlite3.OperationalError as exc:
                if getattr(exc, 'sqlite_errorcode', None) != sqlite3.SQLITE_INTERRUPT:
                    raise
            finally:
                source.set_progress_handler(None, 0)
            _save(c, meta)
    finally:
        state['lock'].release()


def catalog_search(root, source, text, filter_source, page):
    """Return (result, state); None explicitly asks the caller to use its fallback."""
    knowledge, path = _paths(root)
    status = start_catalog(root)
    if status != 'ready':
        return None, status
    try:
        # A source read failure must not discard an otherwise usable title catalog.
        try:
            _sync(root, source)
        except (sqlite3.DatabaseError, OSError):
            LOG.warning('素材标题目录增量同步失败，保留现有目录', exc_info=True)
        pattern = '%' + text.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%'
        with closing(_open(path)) as c:
            _metadata(c, knowledge)
            rows = c.execute("SELECT rowid FROM titles WHERE trashed=0 AND title LIKE ? ESCAPE '\\' AND (?='' OR source=?) ORDER BY rowid DESC LIMIT 51 OFFSET ?",
                             (pattern, filter_source, filter_source, (page-1)*50)).fetchall()
        # Hydrate only matched metadata. No body projection and no whole-table COUNT.
        ids = [r[0] for r in rows]
        items = []
        if ids:
            items = [dict(r) for r in source.execute(f'''SELECT d.id,d.source,d.kind,d.title,d.url,d.modified AS published_at,substr(d.title,1,240) AS snippet
                FROM documents d WHERE d.rowid IN ({','.join('?' for _ in ids)})
                AND NOT EXISTS(SELECT 1 FROM document_trash t WHERE t.document_id=d.id) ORDER BY d.rowid DESC''', ids)]
        more = len(items) > 50
        total = (page-1)*50 + min(len(items), 50) if items else 0
        return dict(items=items[:50], total=total, total_label='≥'+str(total), has_more=more,
                    page=page, page_size=50, short_query=True, partial=False, catalog_state='ready'), 'ready'
    except (sqlite3.DatabaseError, OSError, ValueError, TypeError, KeyError):
        LOG.warning('素材标题目录查询失败，将后台重建', exc_info=True)
        return None, start_catalog(root, rebuild=True)
