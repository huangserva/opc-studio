"""Temporary manual AIsa service. Fake mode has no egress; live mode only gateway DNS/IPs."""
import argparse,json,os,socket,sys,tempfile,threading,signal
from pathlib import Path
from http.server import ThreadingHTTPServer
from urllib.parse import urlsplit,parse_qs
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from server import Store,Handler
from collect_core import Registry
from collect_adapters import AIsaRadarAdapter
from aisa_source import AIsaSource,urllib_transport
from test_aisa_live_chain import FakeTransport,SECRET
args=argparse.ArgumentParser();args.add_argument('--mode',choices=['fake','live','view'],default='fake');args.add_argument('--workspace');options=args.parse_args()
records=[]
if options.mode!='live':
 transport=FakeTransport()
 def deny(*a,**k):raise OSError('AIsa夹具禁止外连')
 socket.socket.connect=deny;socket.socket.connect_ex=deny;socket.getaddrinfo=deny
else:
 original_dns=socket.getaddrinfo;original_connect=socket.socket.connect;original_connect_ex=socket.socket.connect_ex
 permitted=set()
 def dns(host,*a,**k):
  if host!='api.aisa.one':raise OSError('只允许AIsa网关')
  rows=original_dns(host,*a,**k);permitted.update(x[4][0] for x in rows);return rows
 def connect(sock,address):
  if not isinstance(address,tuple) or address[0] not in permitted:raise OSError('只允许AIsa网关')
  return original_connect(sock,address)
 def connect_ex(sock,address):
  if not isinstance(address,tuple) or address[0] not in permitted:raise OSError('只允许AIsa网关')
  return original_connect_ex(sock,address)
 socket.getaddrinfo=dns;socket.socket.connect=connect;socket.socket.connect_ex=connect_ex
 def transport(method,url,headers,body,timeout):
  import time
  started=time.monotonic();record={'method':method,'path':urlsplit(url).path,'query':parse_qs(urlsplit(url).query)};records.append(record)
  try:
   status,text=urllib_transport(method,url,headers,body,timeout);record.update(http=status,seconds=round(time.monotonic()-started,3));return status,text
  except Exception as e:
   record.update(error=type(e).__name__,seconds=round(time.monotonic()-started,3));raise
workspace=options.workspace if options.mode=='view' else tempfile.mkdtemp(prefix='aisa-chain-workspace-',dir='/tmp')
if not workspace or not (Path(workspace).resolve().is_relative_to(Path('/tmp').resolve())):raise ValueError('仅支持/tmp独立工作区')
store=Store(workspace)
source=AIsaSource(store=store,transport=transport,api_key=SECRET if options.mode=='fake' else '' if options.mode=='view' else None)
store._collect=Registry(store,{'aisa':AIsaRadarAdapter(source)})
class Fixture(Handler):
 def log_message(self,*a):pass
 def do_GET(self):
  parts=urlsplit(self.path)
  if parts.path=='/__fixture/metrics' and options.mode=='fake':
   score=int((parse_qs(parts.query).get('score') or ['987600'])[0]);transport.responses['xiaohongshu']['data']['data']['items'][0].update(score=score,score_text=f'{score}人在看');return self.reply(200,{'ok':True})
  if parts.path=='/__fixture/failure' and options.mode=='fake':
   transport.fail=(401,'{}');return self.reply(200,{'ok':True})
  if parts.path=='/__fixture/malformed' and options.mode=='fake':
   # 非空但全部不可解析：不能当成「今天 0 条」的成功空结果。
   transport.fail=(200,json.dumps({'code':200,'data':{'status_code':0,'data':{'word_list':[{},{'no_word':1}]}}}));return self.reply(200,{'ok':True})
  if parts.path=='/__fixture/empty' and options.mode=='fake':
   # 合法空数组：仍按 0 条成功展示。
   transport.fail=(200,json.dumps({'code':200,'data':{'status_code':0,'data':{'word_list':[]}}}));return self.reply(200,{'ok':True})
  if parts.path=='/__fixture/stats':
   result={'mode':options.mode,'calls':records if options.mode=='live' else [{'method':m,'path':urlsplit(u).path} for m,u,b in transport.calls],'listing':store.clues.listing(source='aisa')}
   if options.mode=='live':
    key=os.environ.get('AISA_API_KEY','');hits=[];count=0
    for p in Path(workspace).rglob('*'):
     if p.is_file():
      count+=1;data=p.read_bytes()
      if any(part and part.encode() in data for part in (key,key[:16],key[-16:],key[:6],key[10:22])):hits.append(p.name)
    result['leak_scan']={'files':count,'exact_or_fragment_matches':len(hits),'matched_names':hits}
   return self.reply(200,source._scrub(result))
  super().do_GET()
http=ThreadingHTTPServer(('127.0.0.1',0),Fixture);http.store=store
print(json.dumps({'base':'http://127.0.0.1:'+str(http.server_port),'workspace':workspace,'mode':options.mode}),flush=True)
def stop(*a):threading.Thread(target=http.shutdown,daemon=True).start()
signal.signal(signal.SIGTERM,stop)
try:http.serve_forever()
finally:http.server_close()
