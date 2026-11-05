"""Sandbox only: production Handler, no schedulers/watchers/model startup."""
import argparse
import json
from pathlib import Path
import sys
from http.server import ThreadingHTTPServer
from types import SimpleNamespace
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from server import Handler
from weknora_upload import UploadService
from tests.test_weknora_upload import fixture, Fake
p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True);p.add_argument('--port',type=int,default=0);a=p.parse_args()
assert str(a.root.resolve()).startswith(('/tmp/','/private/tmp/'))
a.root.mkdir(parents=True,exist_ok=True);fixture(a.root);c=Fake()
s=ThreadingHTTPServer(('127.0.0.1',a.port),Handler);s.store=SimpleNamespace(workspace=a.root/'workspace');s.weknora_upload_service=UploadService(a.root,client_factory=lambda:c)
print(json.dumps({'port':s.server_port}),flush=True);s.serve_forever()
