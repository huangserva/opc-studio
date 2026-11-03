"""兴趣首次抵达账本：源文件只读；独立保留，不随可重建索引删除。"""
from contextlib import closing
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import re
import sqlite3
import threading

LOCK = threading.RLock()
LOG = logging.getLogger(__name__)


def path(root):
    from interest_sources import workspace
    return workspace(root) / 'today/interest-first-seen-v1.sqlite3'


def moment(value):
    from grok_bot_radar import stamp
    return stamp(value)


def arrival(item):
    return item.get('first_seen_at') or item.get('fetched_at')


def _offer(records, item, now, basis):
    key = item.get('canonical_key') or item.get('id')
    first = moment(arrival(item))
    last = moment(item.get('last_seen_at') or item.get('fetched_at'))
    if not key or not first or first > now:
        return
    prior = records.get(key)
    row = (first, max(first, last) if last and last <= now else first,
           item.get('first_seen_basis') or basis)
    if prior:
        row = (min(prior[0], row[0]), max(prior[1], row[1]),
               prior[2] if prior[0] <= row[0] else row[2])
    records[key] = row


def _history(root, reports, now):
    """仅首次升级：旧规范缓存、保留的不可变历史报告提供最早观察证据。"""
    import interest_sources as S
    records = {}
    identities = {}

    def add(rows):
        if not isinstance(rows,list):return
        for row in rows:
            if not isinstance(row,dict):continue
            key=row.get('canonical_key') or row.get('id')
            if key:identities.setdefault(key,dict(id=row.get('id') or key,canonical_key=key,kind=row.get('kind') or 'post',url=row.get('url') or '',platform=row.get('platform') or 'web',sources=row.get('sources') or [],source_topics=row.get('source_topics') or []))
            _offer(records, row, now, 'earliest_retained_observation')

    cache = S.filecache_path(root)
    if cache.is_file() and not cache.is_symlink():
        try:
            with S.readonly(cache) as db:
                for (raw,) in db.execute('SELECT data FROM files'):
                    add(json.loads(raw))
        except (OSError, ValueError, sqlite3.Error):
            LOG.warning('interest_first_seen_history_cache_unavailable')
    # 全部保留日期，而非七日滚动窗口；修复不能把已淘汰的老观察当今天。
    for kind in ('daily', 'alerts', 'weekly', 'bookmarks', 'questions'):
        folder = Path(reports) / kind
        if folder.is_symlink():
            continue
        for source in sorted(folder.glob('*.json')):
            if not re.match(r'^\d{4}-\d{2}-\d{2}', source.name):
                continue
            try:
                add(S.parse_file(source, 'report:' + kind))
            except (OSError, ValueError, TypeError):
                LOG.warning('interest_first_seen_history_report_unavailable')
    # 原兴趣通道也按每个保留报告的观察时间恢复，而非快照最后一个版本。
    import interest_channel as I
    folder = Path(reports) / 'interest'
    if not folder.is_symlink():
        for source in sorted(folder.glob('*.json')):
            try:
                when = datetime.strptime(source.stem, '%Y-%m-%d-%H%M').replace(tzinfo=I.grok.BEIJING)
                raw = S.read_json(source)
                rows = raw if isinstance(raw, list) else next((raw[k] for k in ('posts', 'entries', 'items') if isinstance(raw, dict) and isinstance(raw.get(k), list)), [])
                posts = [I._post(row, when)[0] for row in rows if isinstance(row, dict)]
                add([dict(p, canonical_key=p['id']) for p in posts if p])
            except (OSError, ValueError, TypeError):
                LOG.warning('interest_first_seen_history_interest_unavailable')
    # 不可变的来源建档时间也可恢复；modified/updated_at 不冒充首次时间。
    for row in S.database_items(root,now):
        if moment(row.get('first_seen_at')):add([row])
    keys=list(identities)
    aliases=list(identities.values())
    S.attach_k1(root,aliases)
    normalized={}
    for original_key,row in zip(keys,aliases):
        original=records.get(original_key)
        if original:
            known=moment(row.get('first_seen_at'))
            first=min(original[0],known) if known else original[0]
            basis=row.get('first_seen_basis') if known and known<original[0] else original[2]
            _offer(normalized,dict(row,first_seen_at=first.isoformat(),last_seen_at=original[1].isoformat(),first_seen_basis=basis),now,basis)
    records=normalized
    # 仅剩被覆盖的派生索引、没有任何历史观察证据时，明确从修复时点起算。
    try:
        saved = S.read_json(S.cache_path(root))
        for row in saved.get('items', []) if isinstance(saved,dict) else []:
            if not isinstance(row,dict):continue
            key = row.get('canonical_key') or row.get('id')
            if key not in records:
                _offer(records, dict(row, first_seen_at=now.isoformat(),
                                    first_seen_basis='since_repair_no_history'), now,
                       'since_repair_no_history')
    except (OSError, ValueError, TypeError):
        pass
    return records


def apply(root, items, now=None, reports=None):
    """首次时间只可被更早证据纠正；重复同步仅推进最后观察时间。"""
    import interest_sources as S
    import grok_bot_radar as G
    now = now or datetime.now(timezone.utc)
    target = path(root)
    if target.is_symlink() or target.parent.is_symlink():
        raise ValueError('first_seen_link')
    target.parent.mkdir(parents=True, exist_ok=True)
    with LOCK, closing(sqlite3.connect(target, timeout=30)) as db, db:
        db.execute('CREATE TABLE IF NOT EXISTS first_seen(canonical_key TEXT PRIMARY KEY, first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL, basis TEXT NOT NULL)')
        db.execute('CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT NOT NULL)')
        db.execute('BEGIN IMMEDIATE')
        if not db.execute("SELECT 1 FROM meta WHERE key='initialized_at'").fetchone():
            records = _history(root, reports or G.reports_dir(), now)
            db.executemany('INSERT OR IGNORE INTO first_seen VALUES(?,?,?,?)',
                           [(key, first.isoformat(), last.isoformat(), basis) for key, (first, last, basis) in records.items()])
            db.execute("INSERT INTO meta VALUES('initialized_at',?)", (now.isoformat(),))
        records = {}
        for row in items:
            _offer(records, row, now, 'observed_since_repair')
            # K1 后续合并身份时，保留旧规范ID账本中更早的观察。
            alias=row.get('id')
            key=row.get('canonical_key') or alias
            if alias and alias!=key:
                prior=db.execute('SELECT first_seen_at,last_seen_at,basis FROM first_seen WHERE canonical_key=?',(alias,)).fetchone()
                if prior:_offer(records,dict(canonical_key=key,first_seen_at=prior[0],last_seen_at=prior[1],first_seen_basis=prior[2]),now,prior[2])
        for key, (first, last, basis) in records.items():
            old = db.execute('SELECT first_seen_at,last_seen_at,basis FROM first_seen WHERE canonical_key=?', (key,)).fetchone()
            if old:
                old_first, old_last = moment(old[0]), moment(old[1])
                if old_first <= first or old[2]=='since_repair_no_history' and basis=='observed_since_repair':
                    first, basis = old_first, old[2]
                last = max(old_last, last)
            value = (first.isoformat(), last.isoformat(), basis)
            if old != value:
                db.execute('INSERT OR REPLACE INTO first_seen VALUES(?,?,?,?)', (key, *value))
            records[key] = value
        for row in items:
            value = records.get(row.get('canonical_key') or row.get('id'))
            if value:
                row.update(first_seen_at=value[0], last_seen_at=value[1], first_seen_basis=value[2])
    os.chmod(target, 0o600)
    return items
