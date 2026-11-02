#!/usr/bin/env python3
"""K1 additive shadow migration; backup via SQLite API, never copy a live DB file."""
import argparse
from pathlib import Path
import sqlite3
import sys
import tempfile
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from clue_store_v2 import schema, rollback, connect, assert_write_path, dumps
from clue_ingest import current_topics


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', required=True)
    parser.add_argument('--batch-id', required=True)
    parser.add_argument('--reports', default=str(Path.home()/'grok-bot-reports'))
    parser.add_argument('--rollback', action='store_true')
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--allow-real', action='store_true')
    args = parser.parse_args()
    def run(path, real=False):
        if args.rollback:
            return rollback(path,args.batch_id,real)
        assert_write_path(path,real)
        if not Path(path).exists():
            sqlite3.connect(path).close()
        schema(path,args.batch_id,current_topics(args.reports),real)
        return dict(batch_id=args.batch_id,schema_version=1,status='ready')
    if args.dry_run:
        with tempfile.TemporaryDirectory(prefix='k1-migration-') as tmp:
            dest=Path(tmp)/'clues.db'
            src=connect(args.db,True);db=sqlite3.connect(dest);src.backup(db);src.close();db.close()
            print(dumps(dict(dry_run=True,result=run(dest))))
    else:
        print(dumps(run(args.db,args.allow_real)))

if __name__=='__main__':
    main()
