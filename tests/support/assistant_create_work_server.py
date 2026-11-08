from pathlib import Path
import sys,json,socket,time,hashlib,tempfile
import os
C=Path(__file__).resolve().parents[2]
E=Path(os.environ['ASSISTANT_EVIDENCE']).resolve();E.mkdir(parents=True,exist_ok=True)
sys.path.insert(0,str(C));sys.dont_write_bytecode=True
TMP=E/'temp';TMP.mkdir(exist_ok=True);tempfile.tempdir=str(TMP)
def blocked(*args,**kw):raise RuntimeError('outbound network disabled')
socket.socket.connect=blocked;socket.socket.connect_ex=blocked
import server as s
class Offline:
 version='local-deterministic-newwork-test'
 def available(self):return True
 def __init__(self,owner):self.owner=owner
 def run(self,job,system,tools):
  actions=[]
  if '慢' in job['prompt']:time.sleep(.7)
  def call(name,args):
   action={'name':name,'label':self.owner.tool_labels[name],'status':'running'};job['actions'].append(action)
   try:
    result=self.owner.execute(name,args,job);action.update(status='done',result=result.get('summary','完成'))
    for k in ('slug','version'):
     if k in result:action[k]=result[k]
    return result
   except ValueError as ex:action.update(status='error',result=str(ex));raise
  if 'create_work' in job['policy']['allowed_tools']:
   result=call('create_work',{'title':'离线新作品 '+job['id'][:6],'markdown':'## 实验结果\n\n实验A耗时10秒，实验B耗时8秒。\n\n原稿保持不变。','text_source':'generated'})
   again=call('create_work',{'title':'重复调用不另建','markdown':'重复内容','text_source':'generated'});assert again==result
   answer='已创建独立新作品，原稿保留。'
  else:answer='本轮未新建作品。'
  (E/'runtime-turns.jsonl').open('a').write(json.dumps({'id':job['id'],'prompt':job['prompt'],'policy':job['policy'],'workflow':bool(job.get('workflow')),'actions':job['actions']},ensure_ascii=False)+'\n')
  return {'status':'done','answer':answer}
s.assistant_module.PiRuntime=Offline
store=s.Store(E/'workspace')
from knowledge_store import Knowledge
store._knowledge=Knowledge(store,sources=[])
a=store.create({'title':'受保护旧稿'});slug=a['slug'];store.save(slug,{'base_version':'v0001','title':a['title'],'blocks':[{'kind':'text','html':'<p>这是不可覆盖的原稿。</p>'}]})
b=store.create({'title':'切换核对稿'});collection=store.save_collection({'name':'离线创作合集'})
if os.environ.get('ASSISTANT_R1_RECEIPTS')=='1':
 (store.article(slug)/'assistant-created.json').write_text(json.dumps({'turn_id':'a'*32,'slug':slug,'version':'v0001','archived':True}))
 (store.article(b['slug'])/'assistant-created.json').write_text('{broken')
config=E/'offline-model.json';config.write_text(json.dumps({'endpoint':'http://127.0.0.1:9/v1/chat/completions','no_api_key':True,'model':'offline-test-model','context_tokens':262144,'max_output_tokens':32768}))
agent=s.assistant_module.Assistant(store,s.content,config)
class Handler(s.Handler):
 def do_GET(self):
  if self.path.startswith('/api/generation'):return self.reply(200,{'models':[],'jobs':[],'configured':False})
  if self.path.startswith('/api/platforms'):return self.reply(200,{'platforms':[]})
  return super().do_GET()
 def log_request(self,code='-',size='-'):
  (E/'http.jsonl').open('a').write(json.dumps({'method':self.command,'path':self.path,'status':code})+'\n')
http=s.ThreadingHTTPServer(('127.0.0.1',0),Handler);http.store=store;http.assistant=agent
from publishing.local_jobs import LocalDeliveries
http.local_deliveries=LocalDeliveries(store)
# Every write stays below the evidence directory, including temp package writes.
def audit(event,args):
 if event=='open':
  path,mode,flags=args
  if isinstance(path,(str,bytes)) and (isinstance(mode,str) and any(c in mode for c in 'wax+') or isinstance(flags,int) and flags & (1|2|64|512|1024)):
   if not Path(path).resolve().is_relative_to(E.resolve()):raise RuntimeError('write outside isolation')
 if event=='subprocess.Popen':raise RuntimeError('subprocess disabled in offline server')
sys.addaudithook(audit)
(E/'running.json').write_text(json.dumps({'port':http.server_port,'slug':slug,'other':b['slug'],'collection':collection['id']}));print(http.server_port,flush=True);http.serve_forever()
