"""R4 additive item provenance migration; old columns and all content stay unchanged.

Run explicitly before installing code that selects the new columns. No read request migrates.
"""
from contextlib import closing
import argparse
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
from datetime import datetime, timezone
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from platform_registry import identity, annotate_items


def legacy_hash(db):
    digest=hashlib.sha256()
    for row in db.execute('SELECT id,source,sources_json FROM items ORDER BY id'):
        digest.update(json.dumps(tuple(row),ensure_ascii=False).encode())
    return digest.hexdigest()


def old_columns_hash(db):
    columns=[r[1] for r in db.execute('PRAGMA table_info(items)') if r[1] not in ('platform','channels_json','identity_backup_json')]
    digest=hashlib.sha256()
    for row in db.execute('SELECT '+','.join(columns)+' FROM items ORDER BY id'):
        digest.update(json.dumps(tuple(row),ensure_ascii=False,default=repr).encode())
    return digest.hexdigest()


def migrate(path, evidence):
    path=Path(path).resolve(); evidence=Path(evidence).resolve()
    if not evidence.is_relative_to(Path('/tmp/r4-rest').resolve()):
        raise ValueError('备份目录必须位于 /tmp/r4-rest/')
    evidence.mkdir(parents=True,exist_ok=True)
    db=sqlite3.connect(path);db.row_factory=sqlite3.Row
    try:
        db.execute('BEGIN IMMEDIATE')
        columns={r[1] for r in db.execute('PRAGMA table_info(items)')}
        before=db.execute('SELECT count(*) FROM items').fetchone()[0];before_hash=legacy_hash(db);content_before=old_columns_hash(db)
        # Backup uses a separate reader so BEGIN IMMEDIATE cannot deadlock backup().
        backup=evidence/('items-before-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'.db')
        with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)) as source, closing(sqlite3.connect(backup)) as dest: source.backup(dest)
        for name,definition in [('platform',"TEXT NOT NULL DEFAULT 'web'"),('channels_json',"TEXT NOT NULL DEFAULT '[]'"),('identity_backup_json',"TEXT NOT NULL DEFAULT '{}' ")]:
            if name not in columns: db.execute('ALTER TABLE items ADD COLUMN '+name+' '+definition)
        changed=0
        for row in db.execute('SELECT id,url,source,sources_json,platform,channels_json,identity_backup_json FROM items').fetchall():
            # A complete prior migration is a no-op, even if upstream clue channels change later.
            if row['identity_backup_json']!='{}':continue
            try: sources=json.loads(row['sources_json'])
            except ValueError:sources=[]
            item=dict(id=row['id'],url=row['url'],source=row['source'],sources=sources if isinstance(sources,list) else [])
            annotate_items(path.parent.parent,[item])
            archive=json.dumps(dict(source=row['source'],sources_json=row['sources_json']),ensure_ascii=False,sort_keys=True)
            db.execute('UPDATE items SET platform=?,channels_json=?,identity_backup_json=? WHERE id=?',(item['platform'],json.dumps(item['channels']),archive,row['id']));changed+=1
        after=db.execute('SELECT count(*) FROM items').fetchone()[0];after_hash=legacy_hash(db);content_after=old_columns_hash(db)
        assert before==after and before_hash==after_hash and content_before==content_after
        db.commit()
        result=dict(database=str(path),backup=str(backup),before=before,after=after,changed=changed,legacy_before_sha256=before_hash,legacy_after_sha256=after_hash,old_columns_before_sha256=content_before,old_columns_after_sha256=content_after,backup_sha256=hashlib.sha256(backup.read_bytes()).hexdigest(),
                    rollback='停止服务后，将 SQLite backup 通过 sqlite3 backup API 恢复到原路径，再恢复代码基线。生产恢复会覆盖迁移后的新写入，执行前须核对。')
        (evidence/'migration.json').write_text(json.dumps(result,ensure_ascii=False,indent=2));return result
    except Exception:db.rollback();raise
    finally:db.close()


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--db',required=True);parser.add_argument('--evidence',required=True);args=parser.parse_args()
    print(json.dumps(migrate(args.db,args.evidence),ensure_ascii=False))
