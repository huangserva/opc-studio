"""Isolated real Handler/Store browser fixture, with writes and outbound I/O guarded."""
import sys,os,json,tempfile,socket
from pathlib import Path
E=Path(sys.argv[1]).resolve();ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT));sys.dont_write_bytecode=True
tempfile.tempdir=str(E/'temp');Path(tempfile.tempdir).mkdir(parents=True,exist_ok=True)
os.environ['GROK_BOT_REPORTS_DIR']=str(E/'reports');os.environ['ARTICLE_STUDIO_MODEL_CONFIG']=str(E/'no-model.json');os.environ['ARTICLE_STUDIO_BUFFER_CLI']='';os.environ['ARTICLE_STUDIO_DEALS_SEED']=''
(E/'reports/posts').mkdir(parents=True,exist_ok=True)
def inside(p):return Path(os.fsdecode(p)).resolve().is_relative_to(E)
def audit(event,args):
 if event=='open':
  p,mode,flags=args
  if isinstance(p,(str,bytes,os.PathLike)):
   writing=isinstance(mode,str) and any(c in mode for c in 'wax+') or flags & (os.O_WRONLY|os.O_RDWR|os.O_CREAT|os.O_TRUNC|os.O_APPEND)
   if writing and not inside(p):raise PermissionError('fixture forbids writes outside isolated directory')
 if event in ('socket.connect','socket.getaddrinfo'):
  address=args[1] if event=='socket.connect' else args[0];host=address[0] if isinstance(address,tuple) else address
  if host not in ('127.0.0.1','localhost','::1',None):raise PermissionError('fixture forbids external network')
 if event in ('subprocess.Popen','os.system','os.posix_spawn'):raise PermissionError('fixture forbids subprocess')
sys.addaudithook(audit)
from server import Store,Handler
from knowledge_store import Knowledge
from items_store import ItemsStore
from http.server import ThreadingHTTPServer
from types import SimpleNamespace
from datetime import datetime,timezone
import publish_review
publish_review.GROK_REPORTS_ROOT=E/'reports'
store=Store(E/'workspace');store._knowledge=Knowledge(store,sources=[]);items=ItemsStore(store.workspace)
now=datetime.now(timezone.utc).isoformat()
work=store.create({'title':'隔离发布复盘操作验收','type':'post'});other=store.create({'title':'带规范来源字段的发布','type':'post'})
for w,itemid,sources,extra in [(work,'x:77',['G'],{}),(other,'douyin:88',['V'],{'platform':'douyin','channels':['aisa'],'platform_info':{'key':'douyin'},'channel_details':[{'key':'aisa'}]})]:
 items.upsert({'id':itemid,'title':'隔离来源素材 '+itemid,'sources':sources,'kind':'post','url':'https://x.com/source/status/77' if itemid.startswith('x:') else 'https://www.douyin.com/video/88','status':'kept','fetched_at':now})
 (store.library/w['slug']/'source-item.json').write_text(json.dumps({'item_id':itemid,'sources':sources,**extra}))
# Explicit non-PUBLISHED fixtures, with no external export or Buffer operation.
p=store.library/work['slug']/'deliveries.json';p.write_text(json.dumps([{'id':'draft','kind':'buffer_draft','platform':'x','version':'v0001','url':'https://x.com/test/status/1'},{'id':'export','kind':'exported','platform':'x','version':'v0001'}]))
store.record_delivery(other['slug'],{'platform':'x','version':'v0001','url':'https://x.com/fixture/status/456'})
class QuietHandler(Handler):
 def log_message(self,*args):pass
 def do_GET(self):
  if self.path.startswith('/api/assistant'):return self.reply(200,{'active_job':None,'messages':[],'profiles':[],'configured':False})
  return super().do_GET()
http=ThreadingHTTPServer(('127.0.0.1',0),QuietHandler);http.store=store;http.assistant=SimpleNamespace(active=None)
(E/'port.json').write_text(json.dumps({'port':http.server_port,'slug':work['slug'],'other':other['slug']}));print(http.server_port,flush=True);http.serve_forever()
