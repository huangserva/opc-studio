"""M3 R1 exception matrix server: sandbox source/ledger and fake remote only."""
import argparse
import json
from pathlib import Path
import sys
from http.server import ThreadingHTTPServer
from types import SimpleNamespace
import sqlite3
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from server import Handler
from weknora_upload import UploadService
from tests.test_weknora_upload import fixture,Fake
p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True);p.add_argument('--kind',choices=['oversize','read','empty'],required=True);a=p.parse_args()
assert str(a.root.resolve()).startswith(('/tmp/','/private/tmp/'))
a.root.mkdir(parents=True);fixture(a.root);c=Fake();service=UploadService(a.root,client_factory=lambda:c)
good=service.catalog()['items'][1]
if a.kind=='read':
    original=service.read_document
    def read(db,rowid):
        row=original(db,rowid)
        if row['id']=='one':raise sqlite3.OperationalError('synthetic-sensitive-read-detail')
        return row
    service.read_document=read
else:
    with sqlite3.connect(a.root/'workspace/knowledge/knowledge.db') as db:db.execute('UPDATE documents SET body=? WHERE id=?',('a'*200001 if a.kind=='oversize' else '  \n\t','one'))
s=ThreadingHTTPServer(('127.0.0.1',0),Handler);s.store=SimpleNamespace(workspace=a.root/'workspace');s.weknora_upload_service=service
print(json.dumps({'port':s.server_port,'good':good}),flush=True);s.serve_forever()
