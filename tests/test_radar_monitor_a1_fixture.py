"""Translate original B1 test inputs to A1-b committed fixture format.

Only fixture construction changes; original assertions remain unchanged.
"""
import copy
import hashlib
import json
from pathlib import Path


def commit_fixture(root, source):
    root = Path(root)
    raw = copy.deepcopy(source)
    meta = raw.get('run', raw)
    meta['schema_version'] = 'radar-1'
    meta['finished_at'] = meta.pop('ended_at', meta.get('finished_at'))
    accounts = raw['accounts']
    if isinstance(accounts, list):
        accounts = {a['handle']: a for a in accounts}
        raw['accounts'] = accounts
    for index, a in enumerate(accounts.values()):
        a['status'] = {'success':'ok', 'empty_success':'empty'}.get(a['status'], a['status'])
        a['ok'] = a['status'] in ('ok','empty','partial')
        a['calls'] = 1 + (meta['api_calls'] - len(accounts) if index == 0 else 0)
    meta['target_count'] = len(accounts)
    meta['partial'] = sum(a['status'] == 'partial' for a in accounts.values())
    meta['empty'] = sum(a['status'] == 'empty' for a in accounts.values())
    meta['coverage'] = round(meta['success'] / len(accounts), 4)
    rows = raw.get('tweets')
    if rows is None:
        rows = [t for a in accounts.values() for t in a.get('tweets', [])]
    for t in rows:
        author = t.pop('author', {})
        if isinstance(author, str): author = {'handle':author}
        t['author_handle'] = author.get('handle')
        t['author_user_id'] = author.get('user_id')
        t['queried_handle'] = t['author_handle']
        t['kind'] = t.pop('type', None)
    folder = root/'workspace/twitter-radar/snapshots/2026-10-15'
    folder.mkdir(parents=True, exist_ok=True)
    path = folder/(meta['run_id']+'.json')
    payload = json.dumps(raw).encode()
    path.write_bytes(payload)
    manifest = {'schema_version':'radar-1', 'run_id':meta['run_id'],
                'started_at':meta['started_at'], 'snapshot_file':str(path.relative_to(root/'workspace/twitter-radar')),
                'snapshot_sha256':hashlib.sha256(payload).hexdigest()}
    (root/'public').mkdir(exist_ok=True)
    (root/'public/twitter-radar-bundle.json').write_text(json.dumps({'run_id':meta['run_id'],'manifest':manifest}))
    return raw
