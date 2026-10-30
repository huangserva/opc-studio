"""R1/R2/R3 random-port HTTP probe with local deterministic backend.

Usage: python tests/server_domain_route_probe.py ROOT OUTPUT [BASELINE_JSON]
The fixture records service calls and avoids remote collection/model/publishing.
"""
import ast,sys,json,tempfile,threading,http.client,pathlib,re,types,hashlib
root=pathlib.Path(sys.argv[1]).resolve();sys.path.insert(0,str(root));import server
OUT=pathlib.Path(sys.argv[2]);OUT.parent.mkdir(parents=True,exist_ok=True)
GET=['/chat-attachments/'+('a'*32)+'%5C.png','/api/deals','/api/deals/proposals/status?ids=fixture','/api/deals/options','/api/deals/review','/api/deals/review/report?week=2026-10-26','/api/deals/fixture','/api/intake','/api/intake/analysis-status','/api/intake/fixture/file','/api/intake/fixture','/api/cards','/api/cards/fixture/prompt','/api/cards/fixture','/api/collect/platforms','/api/collect/aisa/job?id=fixture','/api/collect/status','/api/intel/recommendations','/api/intel/status','/api/intel/subscriptions','/api/intel/jobs','/api/intel/articles','/api/intel/accounts?query=fixture','/api/intel/articles/fixture','/api/intel/jobs/fixture','/api/feedback','/api/feedback/observations','/api/feedback/reviews','/api/feedback/overviews','/api/feedback/review/fixture','/api/feedback/observation/fixture','/api/feedback/snapshots','/api/feedback/snapshot/fixture','/api/schedules','/api/schedules/run/fixture','/api/schedules/fixture/outsourced_prompt','/api/schedules/runs','/api/assistant/settings','/api/assistant','/api/assistant/capabilities','/api/assistant/ssh','/api/assistant/memory','/api/assistant/job/fixture','/assistant-images/'+('a'*32)+'.png','/chat-attachments/'+('a'*32)+'.png']
POST=[('/api/collect/aisa/run',{}),('/api/deals',{}),('/api/deals/from-clue',{}),('/api/deals/apply-proposal',{}),('/api/deals/fixture/scan-wechat',{'query':'fixture'}),('/api/deals/fixture/evidence',{}),('/api/deals/import-initial',{})]+[('/api/deals/fixture/'+x,{}) for x in ('receipt','deliverable','publication','publication-edit','void')]+[('/api/deals/unknown',{})]+[('/api/intake/fixture/'+x,{}) for x in ('resolve','analyze','delete')]+[('/api/intake/'+x,{'ids':['fixture']}) for x in ('delete-preview','delete-many')]+[('/api/cards/fixture/'+x,{}) for x in ('confirm','reject','restore','note','delete')]+[('/api/cards/cleanup',{}),('/api/intel/recommendations/scan',{})]+[('/api/intel/recommendations/'+x,{'fakeid':'fixture'}) for x in ('subscribe','ignore','unignore','unknown')]+[('/api/intel/subscriptions',{}),('/api/intel/identify',{'url':'fixture'}),('/api/intel/run',{}),('/api/intel/unsubscribe',{'fakeid':'fixture'}),('/api/intel/articles/fixture/intake',{}),('/api/intel/unknown',{}),('/api/feedback/import',{}),('/api/feedback/review/fixture/notes',{}),('/api/feedback/review/fixture/knowledge',{}),('/api/feedback/overview',{}),('/api/feedback/observe',{}),('/api/feedback/observation/fixture/knowledge',{}),('/api/feedback/observation/fixture/bind',{}),('/api/feedback/snapshot/fixture/knowledge',{}),('/api/assistant/attachments',{}),('/api/schedules/save',{}),('/api/schedules/batch',{})]+[('/api/schedules/fixture/'+x,{}) for x in ('publish','cancel','run','pause','resume')]+[('/api/assistant/'+x,{'id':'fixture'}) for x in ('settings','select-model','test-settings','send','stop','new-chat')]+[('/api/assistant/workflows/fixture/save',{})]
INVALID=['/api/deals/proposals/status','/api/deals/review/report','/api/intake?limit=1&limit=2','/api/cards?bad=1','/api/intel/accounts','/api/schedules/runs?page=0','/api/assistant/capabilities?bad=1','/api/assistant/ssh?bad=1','/api/assistant/memory?bad=1','/api/deals/fixture/no-route','/api/assistant/no-route','/api/intel/no-route','/api/cards/fixture/no-route']
# Around every route, exercise one extra segment, one fewer segment, and the
# other HTTP method. De-duplicate by method/path, keeping the original fixtures.
negative_cases={}
for method,path,payload in [('GET',p,None) for p in GET+INVALID]+[('POST',p,v) for p,v in POST]:
 clean=path.split('?')[0]
 for near in (clean+'/extra',clean.rsplit('/',1)[0] or '/'):
  negative_cases[(method,near)]={} if method=='POST' else None
 negative_cases[('POST' if method=='GET' else 'GET',clean)]={} if method=='GET' else None
# Each dispatch predicate must have exactly one matching module branch, in order.
# The pre-split snapshot has no domain modules, so only the after tree is checked.
if (root/'deals_api.py').exists():
 handler=next(n for n in ast.parse((root/'server.py').read_text()).body if isinstance(n,ast.ClassDef) and n.name=='Handler')
 checked=0
 for method,function in (('do_GET','get_route'),('_post_routes','post_route')):
  method_node=next(n for n in handler.body if isinstance(n,ast.FunctionDef) and n.name==method)
  guards={}
  for n in ast.walk(method_node):
   if not isinstance(n,ast.If):continue
   imports=[x for x in n.body if isinstance(x,ast.ImportFrom) and x.module in ('intel_collect_api','deals_api','intake_cards_api','feedback_api','schedules_assistant_api') and any(a.name==function for a in x.names)]
   if not imports:continue
   conditions=n.test.values if isinstance(n.test,ast.BoolOp) and isinstance(n.test.op,ast.Or) else [n.test]
   guards.setdefault(imports[0].module,[]).extend(ast.dump(x) for x in conditions)
  for module,conditions in guards.items():
   function_node=next(n for n in ast.parse((root/(module+'.py')).read_text()).body if isinstance(n,ast.FunctionDef) and n.name==function)
   assert conditions==[ast.dump(n.test) for n in function_node.body if isinstance(n,ast.If)], ('dispatch/module deadzone',module,function)
   checked+=len(conditions)
 assert checked==77, ('incomplete dispatch inventory',checked)
 print('ordered dispatch/branch predicates identical:',checked)
class Backend:
 def __init__(self,name='store'):self.name=name
 def __getattr__(self,k):
  if k=='analysis_workers':return []
  return Backend(self.name+'.'+k)
 def __call__(self,*a,**kw):
  def safe(x):
   if isinstance(x,Backend):return x.name
   if hasattr(x,'read'):return {'stream':True}
   return x
  entry={'call':self.name,'args':[safe(x) for x in a],'kwargs':{k:safe(v) for k,v in kw.items()}}
  calls.append(entry)
  if self.name.endswith('.adapter'):return Backend(self.name+'('+str(a[0])+')')
  if self.name.endswith(('file_path','attachment_path')):return file
  if self.name.endswith('.analysis_model'):return True
  if self.name.endswith('.resolve'):return {'status':'resolved','fixture':'local'}
  if self.name.endswith('intel_recommend.listing'):return {'items':[{'fakeid':'fixture','nickname':'固定公众号'}]}
  if self.name.endswith(('.articles','.listing')):return {'items':[{'id':'fixture','title':'固定内容'}],'total':1,'fixture':True}
  return {'fixture':True,'value':self.name,'items':[{'id':'fixture','title':'固定内容'}]}
with tempfile.TemporaryDirectory(prefix='p4-route-workspace-') as temp:
 workspace=pathlib.Path(temp);file=workspace/'assistant/images'/('a'*32+'.png');file.parent.mkdir(parents=True);file.write_bytes(b'fixture-image')
 calls=[];store=Backend();store.workspace=workspace
 httpd=server.ThreadingHTTPServer(('127.0.0.1',0),server.Handler);httpd.store=store;httpd.assistant=Backend('assistant');httpd.schedules=Backend('schedules')
 thread=threading.Thread(target=httpd.serve_forever,daemon=True);thread.start();port=httpd.server_port
 def request(method,path,payload=None,missing=None,length=None,raw=None):
  calls.clear();body=raw if raw is not None else json.dumps(payload).encode() if method=='POST' else None
  headers={}
  if method=='POST':headers={'Origin':f'http://127.0.0.1:{port}','X-Article-Studio':'1','Content-Type':'application/json'}
  if raw is not None:headers['Content-Type']='application/octet-stream'
  if missing:headers.pop(missing,None)
  if length is not None:headers['Content-Length']=str(length)
  conn=http.client.HTTPConnection('127.0.0.1',port,timeout=10);conn.request(method,path,body,headers);r=conn.getresponse();data=r.read();status=r.status
  assert data, ('empty HTTP response body',method,path,status)
  try:body=json.loads(data)
  except ValueError:body={'bytes_sha256':hashlib.sha256(data).hexdigest()}
  rec={'method':method,'path':path,'status':status,'body':body,'calls':list(calls),'headers':{k:v for k,v in r.getheaders() if k.lower() in ('content-type','cache-control','x-content-type-options','accept-ranges')}};conn.close();return rec
 rows=[request('GET',p) for p in GET+INVALID]+[request('POST',p,v) for p,v in POST]
 negative=[request(method,path,payload) for (method,path),payload in negative_cases.items()]
 security=[{**request('POST',p,v,missing=missing),'missing':missing} for p,v in POST for missing in ('Origin','X-Article-Studio')]
 assert all(x['status']==403 and not x['calls'] for x in security)
 # Upload endpoints must bypass generic 32MB cap. Backend consumes no real bytes;
 # advertise 33MB, send zero body, proving dispatch occurs before body parsing.
 uploads=[request('POST',p,raw=b'',length=33_000_000) for p in ('/api/intake/drop?kind=image&name=x.png','/api/intake/stage?kind=image&name=x.png')]
 for x in uploads:assert x['status']==201 and x['calls'],x
 too_large=request('POST','/api/deals',raw=b'',length=33_000_000);assert too_large['status']==413 and not too_large['calls']
 OUT.write_text(json.dumps({'routes':rows,'negative':negative,'security':security,'uploads':uploads,'too_large':too_large},indent=2,ensure_ascii=False))
 httpd.shutdown();httpd.server_close();thread.join()
print('negative',len(negative));print('routes',len(rows),'security',len(security),'uploads',len(uploads),'too_large',too_large['status'])

if len(sys.argv) > 3:
 baseline=json.loads(pathlib.Path(sys.argv[3]).read_text())
 current=json.loads(OUT.read_text())
 if baseline != current:
  differences=[(kind,i) for kind in ('routes','negative','security','uploads') for i,(old,new) in enumerate(zip(baseline[kind],current[kind])) if old != new]
  print('route comparison FAILED:',differences)
  raise SystemExit(1)
 print('route comparison identical, including body/calls/headers')
