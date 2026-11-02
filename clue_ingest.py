"""Incremental local-file ingestion into K1 shadow tables. No scheduler/network calls."""
from __future__ import annotations
import argparse
from contextlib import contextmanager
import fcntl
import importlib
import json
import os
from pathlib import Path
import sqlite3
import stat
import tempfile
from clue_store_v2 import (ROOT, PARSER_VERSION, SCHEMA_VERSION, schema, connect, transaction, write,
    dumps, digest, identity, now, safe_text, safe_url, safe_payload, propose_merge,
    assert_write_path, complete_batch)

SOURCES = ('daily', 'alerts', 'interest', 'bookmarks', 'linuxdo', 'video', 'legacy', 'questions', 'wechat_tools')


@contextmanager
def ingest_lock(path, allow_real=False):
    """Cooperate across K1 and tool imports without waiting or pausing producers."""
    path = assert_write_path(path, allow_real)
    fd = os.open(path.parent / '.clue-ingest.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    acquired = False
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            acquired = True
        except BlockingIOError:
            pass
        yield acquired
    finally:
        if acquired:
            fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


@contextmanager
def short_transaction(path, allow_real=False):
    path = assert_write_path(path, allow_real)
    db = connect(path)
    try:
        db.execute('PRAGMA busy_timeout=3000')
        db.execute('PRAGMA cache_spill=OFF')
        db.execute('BEGIN IMMEDIATE')
        yield db
        db.commit()
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()

# Import transactions all use the same bounded writer protocol.
transaction = short_transaction

def register_batch(path, batch_id, allow_real=False):
    with short_transaction(path, allow_real) as db:
        if db.execute("SELECT count(*) FROM topics WHERE kind='direction'").fetchone()[0] != 9:
            raise ValueError('K1底座尚未就绪')
        old = db.execute('SELECT status FROM migrations WHERE id=?', (batch_id,)).fetchone()
        if old and old['status'].startswith('rolled_back'):
            raise ValueError('已撤销批次不可重用')
        db.execute("INSERT OR IGNORE INTO migrations VALUES(?,?,'ready',NULL,'{}','{}',?,?)", (batch_id,SCHEMA_VERSION,now(),now()))


def current_topics(reports):
    """Read actual nine-direction document; no import-time source IO or fallback to five."""
    text = (Path(reports) / 'interest-topics.md').read_text()
    lines = text.splitlines()
    start = lines.index('## 当前方向') + 1
    end = next((i for i in range(start, len(lines)) if lines[i].startswith('#')), len(lines))
    names = [line[2:].strip() for line in lines[start:end] if line.startswith('- ')]
    if len(names) != 9:
        raise ValueError('当前方向必须是9项；变更须另审')
    return names


def read_document(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > 64*1024*1024:
            raise ValueError('unsupported_source_file')
        raw = stream.read(64*1024*1024+1)
        data = json.loads(raw, parse_constant=lambda _: (_ for _ in ()).throw(ValueError('nonfinite_json')))
        return data, __import__('hashlib').sha256(raw).hexdigest()


def source_files(source, reports, workspace):
    if source == 'wechat_tools':
        return []  # Dedicated RO-knowledge producer calls import_document with sanitised fields.
    if source in ('linuxdo', 'video'):
        file = Path(workspace) / ('linuxdo/top-daily.json' if source == 'linuxdo' else 'video-radar/page.json')
        return [file] if file.exists() else []
    folder = Path(reports) / source
    if folder.is_symlink():
        raise ValueError('symlink_source_directory')
    return sorted(folder.glob('*.json'))


def _persist(db, item, receipt_id, channel, source, record_key, batch_id):
    stamp = now()
    key = item['canonical_key']
    cid = identity('clue', key)
    existing = db.execute('SELECT * FROM clue_objects WHERE canonical_key=?', (key,)).fetchone()
    if existing:
        cid = existing['id']
    first = item.get('first_seen_at') or stamp
    last = max(first, item.get('observed_at') or first)
    values = dict(canonical_key=key, platform=item['platform'], kind=item['kind'], title=item['title'], summary=item['summary'], author_key=item.get('author_key'),
                  published_at=item.get('published_at'), first_seen_at=first, last_seen_at=last, content_complete=int(item['content_complete']), incomplete_reason=item.get('incomplete_reason'),
                  verification_state='unverified', triage_state='pending', revision=1)
    created = write(db, 'clue_objects', cid, values, batch_id)
    if existing:
        # Never overwrite human title/summary/state/revision or publish-time inference.
        write(db, 'clue_objects', cid, dict(last_seen_at=max(existing['last_seen_at'], last), content_complete=int(bool(existing['content_complete'] or item['content_complete'])),
                                          incomplete_reason=None if existing['content_complete'] or item['content_complete'] else existing['incomplete_reason']), batch_id, update=True)
    db.execute('UPDATE source_receipts SET clue_id=? WHERE id=?', (cid, receipt_id))
    # UPDATE above is part of this receipt's single insert journal entry.
    after = dict(db.execute('SELECT * FROM source_receipts WHERE id=?', (receipt_id,)).fetchone())
    db.execute('UPDATE migration_changes SET after_json=? WHERE batch_id=? AND table_name=? AND row_id=?', (dumps(after),batch_id,'source_receipts',receipt_id))
    for alias in item.get('aliases', []):
        if not alias:
            continue
        existing_alias = db.execute('SELECT clue_id FROM clue_aliases WHERE alias=?', (alias,)).fetchone()
        if existing_alias and existing_alias['clue_id'] != cid:
            raise ValueError('alias_conflict')
        write(db, 'clue_aliases', identity('alias', alias), dict(alias=alias, clue_id=cid, kind='canonical' if alias == key else 'source_alias'), batch_id)
    metadata = safe_payload(item.get('channel_metadata') or {})
    chid = identity(cid, channel)
    old = db.execute('SELECT * FROM clue_channels WHERE id=?', (chid,)).fetchone()
    channel_values = dict(clue_id=cid, channel=channel, source=source, source_id=record_key, bookmarked_at=item.get('bookmarked_at'), first_seen_at=first, last_seen_at=last, metadata_json=dumps(metadata))
    if old:
        channel_values['first_seen_at'] = old['first_seen_at']
        channel_values['last_seen_at'] = max(old['last_seen_at'], last)
        channel_values['bookmarked_at'] = old['bookmarked_at'] or item.get('bookmarked_at')
    write(db, 'clue_channels', chid, channel_values, batch_id, update=bool(old))
    for folder in item.get('folders', []):
        write(db, 'clue_bookmark_folders', identity(cid, folder), dict(clue_id=cid, folder=folder, origin_receipt_id=receipt_id), batch_id)
    if item.get('suggested_topic'):
        topic = db.execute('SELECT id FROM topics WHERE name=?', (item['suggested_topic'],)).fetchone()
        if topic:
            write(db, 'topic_assignments', identity(cid, topic['id']), dict(clue_id=cid, topic_id=topic['id'], state='suggested', confidence=None, reason='source_suggested_topic', model_version='source-report', confirmed_by=None), batch_id)
        else:
            metadata['unmatched_suggested_topic'] = item['suggested_topic']
            write(db, 'clue_channels', chid, dict(metadata_json=dumps(metadata)), batch_id, update=True)
    raw = dict(metrics=item['metrics'], missing_reasons=item['missing_reasons'])
    raw_hash = digest(raw)
    write(db, 'metric_observations', identity(cid, channel, item.get('observed_at'), raw_hash),
          dict(clue_id=cid, channel=channel, observed_at=item.get('observed_at'), raw_metrics_json=dumps(item['metrics']), missing_reasons_json=dumps(item['missing_reasons']), raw_hash=raw_hash,
               quality='raw' if not item['missing_reasons'] else 'partial_or_missing', origin_receipt_id=receipt_id), batch_id)
    if item['privacy_scope'] == 'public' and (item.get('body') or item.get('rich')):
        content_hash = digest([item.get('body', ''), item.get('rich', {}), item['content_complete']])
        write(db, 'clue_content_versions', identity(cid, channel, content_hash), dict(clue_id=cid, channel=channel, content_hash=content_hash, body=item.get('body', ''), rich_json=dumps(item.get('rich', {})), content_complete=int(item['content_complete']), incomplete_reason=item.get('incomplete_reason'), origin_receipt_id=receipt_id, privacy_scope='public'), batch_id)
    for index, e in enumerate(item.get('evidence') or [dict(reference=dict(url=item.get('canonical_url')), privacy_scope=item['privacy_scope'])]):
        write(db, 'clue_evidence', identity(receipt_id, index), dict(clue_id=cid, origin_receipt_id=receipt_id, source_locator=('legacy-entry:' + e['reference']['legacy_entry_id']) if 'legacy_entry_id' in e['reference'] else 'receipt:' + receipt_id,
              privacy_scope=e['privacy_scope'], reference_json=dumps(e['reference'])), batch_id)
    for relation in item.get('relations', []):
        write(db, 'clue_relations', identity(cid, relation['to_key'], relation['relation_type']), dict(from_id=cid, to_key=relation['to_key'], relation_type=relation['relation_type'], evidence_json=dumps(dict(receipt_id=receipt_id))), batch_id)
    # Legacy canonical candidates are auditable relations, not aliases stealing another object's identity.
    for candidate in item.get('canonical_alias_candidates', []):
        write(db, 'clue_relations', identity(cid, candidate, 'legacy_canonical_candidate'), dict(from_id=cid, to_key=candidate, relation_type='legacy_canonical_candidate', evidence_json=dumps(dict(receipt_id=receipt_id))), batch_id)
    for resource in item.get('resources', []):
        if not isinstance(resource, dict):
            continue
        url = safe_url(resource.get('url'))
        if url:
            kind = safe_text(resource.get('type')) or 'link'
            write(db, 'resource_links', identity(cid, url, kind), dict(clue_id=cid, resource_type=kind, url=url, version=resource.get('version'), license=None, evidence_id=resource.get('evidence_id') or receipt_id), batch_id)
    if 'legacy_state' in item:
        state = item['legacy_state']
        lid = identity(state['thread_id'], cid)
        write(db, 'legacy_object_links', lid, dict(thread_id=state['thread_id'], clue_id=cid, mapping_reason='conservative_one_thread_one_object', mapping_state='compatible', decision_id=None, legacy_state_json=dumps(state)), batch_id, update=bool(db.execute('SELECT 1 FROM legacy_object_links WHERE id=?',(lid,)).fetchone()))
    return cid, created


def import_document(path, source, source_file, data, file_hash, batch_id, allow_real=False, fault=None):
    module = importlib.import_module('clue_sources.' + ('grok' if source in ('daily','alerts','interest') else source))
    records = module.records(data, source)
    meta = data if isinstance(data, dict) else {}
    stats = dict(read=len(records), new=0, reused=0, bad=0, no_time=0, skipped=0)
    with transaction(path, allow_real) as db:
        migration = db.execute('SELECT status FROM migrations WHERE id=?', (batch_id,)).fetchone()
        if not migration or migration['status'].startswith('rolled_back'):
            raise ValueError('批次未迁移或已回滚')
        for channel, record_key, row in records:
            version = digest(row) if source in ('legacy', 'wechat_tools') else file_hash
            rid = identity(channel, record_key, version)
            if db.execute('SELECT 1 FROM source_receipts WHERE id=?', (rid,)).fetchone():
                stats['skipped'] += 1
                continue
            stamp = now()
            receipt = dict(clue_id=None, channel=channel, record_key=record_key, source_file=str(source_file), file_hash=file_hash, source_version=version, parser_version=getattr(module, 'PARSER_VERSION', PARSER_VERSION),
                           first_seen_at=stamp, last_seen_at=stamp, import_status='imported', error=None)
            db.execute('SAVEPOINT record')
            try:
                item = module.parse(row, meta, source)
                write(db, 'source_receipts', rid, receipt, batch_id)
                cid, created = _persist(db, item, rid, channel, source, record_key, batch_id)
                if source == 'wechat_tools':
                    module.after_persist(db, item, cid, rid, batch_id)
                stats['new' if created else 'reused'] += 1
                stats['no_time'] += int(not item.get('published_at'))
                db.execute('RELEASE record')
            except (ValueError, TypeError, KeyError, sqlite3.IntegrityError) as exc:
                db.execute('ROLLBACK TO record');db.execute('RELEASE record')
                receipt.update(import_status='quarantined', error=type(exc).__name__ + ':' + safe_text(str(exc))[:120])
                # Only controlled error codes, never serialize arbitrary bad source values.
                if isinstance(exc, (sqlite3.IntegrityError, KeyError, TypeError)):
                    receipt['error'] = type(exc).__name__
                write(db, 'source_receipts', rid, receipt, batch_id)
                stats['bad'] += 1
        if fault:
            fault(db)  # tests simulate process failure before the file transaction commits
        write(db, 'ingest_checkpoints', identity(source, str(source_file)), dict(source_file=str(source_file), source=source, source_version=file_hash, committed_at=now(), counts_json=dumps(stats)), batch_id, update=True)
        db.execute("UPDATE migrations SET status='importing',cursor=?,updated_at=? WHERE id=?", (str(source_file),now(),batch_id))
    return stats


def ingest(path, reports, workspace, batch_id, sources=SOURCES, allow_real=False):
    with ingest_lock(path, allow_real) as acquired:
        if not acquired:
            return dict(skipped=True, reason='clue_ingest_busy')
        try:
            with transaction(path, allow_real) as db:
                row = db.execute('SELECT status FROM migrations WHERE id=?', (batch_id,)).fetchone()
                if not row or row['status'].startswith('rolled_back'):
                    raise ValueError('批次不可导入')
                db.execute("UPDATE migrations SET status='importing',updated_at=? WHERE id=?", (now(), batch_id))
            return _ingest_unlocked(path, reports, workspace, batch_id, sources, allow_real)
        except BaseException:
            with transaction(path, allow_real) as db:
                db.execute("UPDATE migrations SET status='failed',updated_at=? WHERE id=? AND status NOT LIKE 'rolled_back%'", (now(), batch_id))
            raise


def _ingest_unlocked(path, reports, workspace, batch_id, sources, allow_real):
    result = {}
    for source in sources:
        if source not in SOURCES:
            raise ValueError('未知来源')
        files = source_files(source, reports, workspace) if source != 'legacy' else [Path(path)]
        counts = dict(files=len(files), read=0, new=0, reused=0, bad=0, no_time=0, skipped=0, bad_files=0)
        for file in files:
            file_hash = None
            try:
                if source == 'legacy':
                    from clue_sources.legacy import snapshot
                    data = snapshot(path); file_hash = digest(data)
                else:
                    data, file_hash = read_document(file)
                    if source == 'linuxdo':
                        status_path = Path(workspace)/'linuxdo/status.json'
                        if status_path.exists():
                            status, _ = read_document(status_path)
                            data['_source_status'] = {k: status.get(k) for k in ('attempted_at','refreshing','feed_version')}
                stats = import_document(path, source, file, data, file_hash, batch_id, allow_real)
                for k, value in stats.items():
                    counts[k] += value
            except (ValueError, UnicodeError, OSError, json.JSONDecodeError) as exc:
                counts['bad_files'] += 1
                # File-level corruption is visible too; no successful checkpoint advances.
                version = file_hash or digest([str(file), type(exc).__name__])
                channel = source + ':file-error'
                with transaction(path,allow_real) as db:
                    stamp=now()
                    write(db,'source_receipts',identity(channel,'__file__',version),dict(clue_id=None,channel=channel,record_key='__file__',source_file=str(file),file_hash=version,source_version=version,parser_version=PARSER_VERSION,first_seen_at=stamp,last_seen_at=stamp,import_status='quarantined',error=type(exc).__name__),batch_id)
        result[source] = counts
    if not any(s['bad_files'] for s in result.values()):
        complete_batch(path, batch_id, confirmed=True, allow_real=allow_real, source_names=result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', required=True)
    parser.add_argument('--reports', default=str(Path.home()/'grok-bot-reports'))
    parser.add_argument('--workspace', default=str(ROOT/'workspace'))
    parser.add_argument('--batch-id', required=True)
    parser.add_argument('--source', choices=(*SOURCES, 'all'), default='all')
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--allow-real', action='store_true')
    args = parser.parse_args()
    if args.dry_run:
        with tempfile.TemporaryDirectory(prefix='k1-dry-run-') as tmp:
            dest = Path(tmp)/'clues.db'
            source = connect(args.db, True); copy = sqlite3.connect(dest)
            source.backup(copy);source.close();copy.close()
            schema(dest,args.batch_id,current_topics(args.reports))
            result = ingest(dest,args.reports,args.workspace,args.batch_id,SOURCES if args.source=='all' else [args.source])
            print(dumps(dict(dry_run=True, results=result)))
    else:
        print(dumps(ingest(args.db,args.reports,args.workspace,args.batch_id,SOURCES if args.source=='all' else [args.source],args.allow_real)))

if __name__ == '__main__':
    main()
