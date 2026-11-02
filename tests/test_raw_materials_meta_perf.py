"""Isolated same-scale read-only API/browser server. Never accepts a production workspace."""
import argparse
from contextlib import closing
import hashlib
import http.client
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
from urllib.parse import urlsplit

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from server import Handler, Store, ThreadingHTTPServer
from knowledge_store import Knowledge
from raw_materials_meta import model


def make_server(workspace, health_seconds=0):
    workspace=Path(workspace).resolve()
    if not workspace.is_relative_to(Path('/tmp').resolve()) or workspace==ROOT/'workspace':
        raise ValueError('性能回放仅允许/tmp副本')
    store=Store(workspace)
    knowledge=Knowledge.__new__(Knowledge)
    knowledge.store=store;knowledge.root=workspace/'knowledge';knowledge.path=knowledge.root/'knowledge.db'
    knowledge.sync_lock=threading.Lock();knowledge.stop_event=threading.Event()
    knowledge._raw_meta=None;knowledge._raw_meta_guard=threading.Lock()
    calls=SimpleNamespace(count=0,entered=threading.Event(),release=threading.Event())
    def capture():
        calls.count+=1;calls.entered.set()
        if health_seconds:calls.release.wait(health_seconds)
        return dict(available=True,status='idle',busy=False)
    knowledge.capture=SimpleNamespace(status=capture)
    store._knowledge=knowledge
    store._wechat_source=SimpleNamespace(clue_status=lambda:dict(processed_versions=0,has_pending=False))
    store.clues
    meta=model(knowledge)
    if meta.value is None:meta.refresh()
    class ReadonlyHandler(Handler):
        def do_POST(self):
            return self.reply(403,dict(error='只读性能副本不执行写操作'))
        def do_GET(self):
            path=urlsplit(self.path).path
            if path=='/api/schedules':return self.reply(200,dict(tasks=[],models=[]))
            if path=='/api/collect/aisa/job':return self.reply(200,dict(status='idle'))
            return super().do_GET()
        def log_message(self,*args):pass
    server=ThreadingHTTPServer(('127.0.0.1',0),ReadonlyHandler);server.store=store
    server.daemon_threads=True
    return server,knowledge,calls


def request(port,path):
    began=time.perf_counter();connection=http.client.HTTPConnection('127.0.0.1',port,timeout=60)
    try:
        connection.request('GET',path);response=connection.getresponse();body=response.read()
        return dict(path=path,status=response.status,ms=(time.perf_counter()-began)*1000,bytes=len(body),sha256=hashlib.sha256(body).hexdigest())
    finally:connection.close()


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--workspace',required=True);parser.add_argument('--output',required=True);parser.add_argument('--health-seconds',type=float,default=0);parser.add_argument('--serve',action='store_true');args=parser.parse_args()
    output=Path(args.output).resolve()
    if not output.is_relative_to(Path('/tmp').resolve()):raise ValueError('证据仅写/tmp')
    output.mkdir(parents=True,exist_ok=True)
    server,knowledge,calls=make_server(args.workspace,args.health_seconds)
    try:
        (output/'server.json').write_text(json.dumps(dict(port=server.server_port,pid=__import__('os').getpid(),readonly=True)))
        if args.serve:server.serve_forever()
        else:
            threading.Thread(target=server.serve_forever,daemon=True).start()
            result=[]
            for path in ('/api/knowledge/meta','/api/knowledge','/api/knowledge/search','/api/clues','/api/clue-inbox?limit=20'):
                result.append(request(server.server_port,path))
            (output/'smoke.json').write_text(json.dumps(result,indent=2));print(result)
    finally:
        calls.release.set();knowledge.stop_event.set();server.server_close()


if __name__=='__main__':main()
