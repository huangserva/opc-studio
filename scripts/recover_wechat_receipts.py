"""Recover missing final receipts from the local source ledger, never rerun jobs.

Default is a read-only preview. --apply retains originals in a private backup
and changes only placeholder output; original status/timing remain untouched.
"""
import argparse
import json
from pathlib import Path
import sqlite3
import uuid


def recover(workspace, apply=False):
    workspace=Path(workspace).resolve()
    source=sqlite3.connect((workspace/'wechat-source/jobs.db').as_uri()+'?mode=ro',uri=True)
    target=sqlite3.connect((workspace/'schedules/tasks.db').as_uri()+'?mode='+('rw' if apply else 'ro'),uri=True)
    changes=[]
    try:
        for rid,tid,status,raw,snapshot in target.execute("SELECT id,task_id,status,output,snapshot FROM runs WHERE status IN ('timeout','cancelled')"):
            try:value=json.loads(raw);task=json.loads(snapshot)
            except (ValueError,TypeError):continue
            if not isinstance(value,dict) or set(value)-{'source_run_id','state'} or value.get('source_run_id')!=rid:continue
            row=source.execute('SELECT task_id,kind,status,output FROM jobs WHERE id=?',(rid,)).fetchone()
            if not row or row[0]!=tid or row[1]!=({'wechat_clues':'clues','wechat_radar':'radar'}.get(task.get('kind'))) or row[2] in ('queued','running'):continue
            try:receipt=json.loads(row[3])
            except (ValueError,TypeError):continue
            if not isinstance(receipt,dict) or type(receipt.get('processed')) is not int or receipt['processed']<0:continue
            changes.append({'id':rid,'status':status,'original_output':raw,'source_output':row[3]})
        if apply and changes:
            backup=workspace/'schedules'/('receipt-recovery-'+uuid.uuid4().hex+'.json')
            with backup.open('x') as f:
                backup.chmod(0o600);json.dump(changes,f,ensure_ascii=False,indent=2)
            with target:
                for change in changes:
                    target.execute('UPDATE runs SET output=? WHERE id=? AND status=? AND output=?',
                        (change['source_output'],change['id'],change['status'],change['original_output']))
        return [{'id':c['id'],'status':c['status'],'processed':json.loads(c['source_output'])['processed']} for c in changes]
    finally:
        source.close();target.close()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace',required=True);parser.add_argument('--apply',action='store_true')
    args=parser.parse_args()
    print(json.dumps(recover(args.workspace,args.apply),ensure_ascii=False))
