"""Isolated product server with a recording fake model; never touches live data."""
import json
import os
from pathlib import Path
import socket
import sys
import time
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
allowed=int(os.environ['E2E_APP_PORT'])
connect=socket.socket.connect
connect_ex=socket.socket.connect_ex

def checked(method):
    def local(self,address):
        if not isinstance(address,tuple) or address[0] not in ('127.0.0.1','localhost','::1') or address[1]!=allowed:
            raise OSError('测试禁止访问非隔离服务')
        return method(self,address)
    return local
socket.socket.connect=checked(connect)
socket.socket.connect_ex=checked(connect_ex)
import assistant_policy
import knowledge_store
init=knowledge_store.Knowledge.__init__
knowledge_store.Knowledge.__init__=lambda self,store,sources=None:init(self,store,sources=[])
import server
original=server.assistant_module.Assistant.__init__

def fixture(self,*args,**kwargs):
    original(self,*args,**kwargs)
    def fake(messages,**kw):
        with Path(os.environ['MATERIAL_MODEL_LOG']).open('a') as f:
            f.write(json.dumps({'messages':messages,'allowed_tools':kw.get('allowed_tools')},ensure_ascii=False)+'\n')
        if '在途要求' in json.dumps(messages,ensure_ascii=False):time.sleep(1)
        return {'role':'assistant','content':'已阅读参考素材，仅按输入框要求回答。'}
    self.client=fake
server.assistant_module.Assistant.__init__=fixture
workspace=Path(sys.argv[sys.argv.index('--workspace')+1])
store=server.Store(workspace)
a=store.create({'title':'素材测试 A'});b=store.create({'title':'素材测试 B'})
Path(os.environ['MATERIAL_SEED']).write_text(json.dumps({'a':a['slug'],'b':b['slug']}))
server.main()
