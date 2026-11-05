'use strict';
// Every provider request goes to a random loopback HTTP fake. No real OAuth.
const fs=require('fs'),path=require('path'),os=require('os'),assert=require('assert/strict');
const {spawn}=require('child_process');
const {chromium}=(()=>{try{return require('playwright')}catch(_){return require(path.join(os.homedir(),'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright'))}})();
const ROOT=path.resolve(__dirname,'../..'),PY=path.resolve(ROOT,'../article-importer/.venv/bin/python');
const OUT=process.env.GMAIL_EVIDENCE||fs.mkdtempSync('/tmp/gmail-browser-');fs.mkdirSync(OUT,{recursive:true});
const records=[],pageerrors=[],external=[],consoleErrors=[],network=[];
const fixture=String.raw`
import sys,json,threading,urllib.request,urllib.error,urllib.parse
from pathlib import Path
from http.server import ThreadingHTTPServer,BaseHTTPRequestHandler
from types import SimpleNamespace
from tests.test_gmail_auth import Provider,MemoryVault
from gmail_view import GmailService
from gmail_auth import GmailError
import server
root=Path(sys.argv[1]);now=[1793876400.];provider=Provider(lambda:now[0]);provider.messages={'aa01':provider.message('aa01',body='SYNTHETIC_MAIL_BODY')};vault=MemoryVault()
class Fake(BaseHTTPRequestHandler):
 def log_message(self,*args):pass
 def do_GET(self):
  q=urllib.parse.urlsplit(self.path);query={k:v[0] for k,v in urllib.parse.parse_qs(q.query).items()}
  if q.path=='/audit':
   self.send_response(200);self.end_headers();self.wfile.write(json.dumps(provider.calls).encode());return
  if q.path=='/authorize':
   code=provider.issue(query['code_challenge']);url=query['redirect_uri']+'?'+urllib.parse.urlencode({'state':query['state'],'code':code});self.send_response(302);self.send_header('Location',url);self.end_headers();return
  self.respond('GET',q.path,query=query)
 def do_POST(self):
  raw=self.rfile.read(int(self.headers['Content-Length']))
  if self.path=='/control':
   c=json.loads(raw);now[0]+=400
   if c.get('label') is not None:
    provider.messages['aa01']['labelIds']=c['label'];provider.events=[dict(history=[dict(id='99999999999999999',labelsRemoved=[dict(message=dict(id='aa01'))])],historyId='99999999999999999')]
   if c.get('fail'):provider.fail['/profile']='auth'
   if c.get('clear'):provider.fail.clear()
   self.send_response(200);self.end_headers();self.wfile.write(b'{}');return
  self.respond('POST',self.path,data={k:v[0] for k,v in urllib.parse.parse_qs(raw.decode()).items()})
 def respond(self,method,path,query=None,data=None):
  try:result=provider.request(method,'token' if path=='/token' else path,query=query,data=data);status=200
  except GmailError as e:result={'kind':e.code};status=409
  self.send_response(status);self.send_header('Content-Type','application/json');self.end_headers();self.wfile.write(json.dumps(result).encode())
fake=ThreadingHTTPServer(('127.0.0.1',0),Fake);fakebase='http://127.0.0.1:'+str(fake.server_port)
threading.Thread(target=fake.serve_forever,daemon=True).start()
class LocalHTTP:
 def request(self,method,path,*,data=None,token=None,query=None):
  url=fakebase+('/token' if path=='token' else path)
  if query:url+='?'+urllib.parse.urlencode(query)
  body=urllib.parse.urlencode(data).encode() if data is not None else None
  try:
   with urllib.request.urlopen(urllib.request.Request(url,body,method=method),timeout=5) as r:return json.load(r)
  except urllib.error.HTTPError as e:
   value=json.load(e);raise GmailError(value['kind']) from None
app=ThreadingHTTPServer(('127.0.0.1',0),server.Handler);app.store=server.Store(root);app.gmail_service=GmailService(app.store,lambda backend:vault,LocalHTTP(),lambda:now[0]);print(json.dumps({'base':'http://127.0.0.1:'+str(app.server_port),'fake':fakebase}),flush=True);app.serve_forever()
`;
async function launch(width){
 const tmp=fs.mkdtempSync('/tmp/gmail-ui-');let log='';const child=spawn(PY,['-u','-B','-c',fixture,path.join(tmp,'studio')],{cwd:ROOT,stdio:['ignore','pipe','pipe']});child.stderr.on('data',b=>log+=b);
 const urls=await new Promise((resolve,reject)=>{let out='';child.stdout.on('data',b=>{out+=b;const line=out.split('\n')[0];try{resolve(JSON.parse(line));}catch(_){}});child.once('exit',()=>reject(Error(log)));setTimeout(()=>reject(Error('fixture startup timeout '+log)),15000).unref();});
 let context;
 try{
  context=await chromium.launchPersistentContext(path.join(tmp,'profile'),{headless:true,channel:'chrome',viewport:{width,height:900},args:['--disable-background-networking','--disable-component-update']});
  await context.route('**/*',async route=>{const u=new URL(route.request().url());if(u.origin===urls.base||u.origin===urls.fake||(u.hostname==='127.0.0.1'&&u.pathname.startsWith('/callback/')))return route.continue();if(u.hostname==='accounts.google.com'&&u.pathname==='/o/oauth2/v2/auth')return route.fulfill({status:302,headers:{location:urls.fake+'/authorize'+u.search},body:''});external.push(u.origin);return route.abort();});
  const page=await context.newPage();page.on('pageerror',e=>pageerrors.push(e.message));page.on('console',m=>{if(m.type()==='error')consoleErrors.push({width,text:m.text(),expected:/status of (403|409)/.test(m.text())});});page.on('response',r=>{const u=new URL(r.url());network.push({width,method:r.request().method(),origin:u.origin,path:u.pathname,status:r.status()});});
  const check=(name,passed)=>{assert.ok(passed,name);records.push({width,name,passed:true});};
  await page.goto(urls.base+'/gmail');await page.waitForFunction(()=>document.querySelector('#progress').dataset.client!==undefined);
  check('未连接', (await page.locator('#progress').textContent()).includes('未连接'));
  const csrf=await page.evaluate(async()=>({cross:(await fetch('/api/gmail/unlock',{method:'POST',headers:{'Content-Type':'application/json','X-Article-Studio':'1'},body:'{}'})).status,private:(await fetch('/api/gmail/search?private=true')).status}));check('nonce及私有自提升拦截',csrf.cross===403&&csrf.private===403);
  await page.click('#unlock');await page.waitForFunction(()=>document.querySelector('#unlock').disabled);
  const client={installed:{client_id:'synthetic.apps.googleusercontent.com',client_secret:'SYNTHETIC_CLIENT_NOT_A_SECRET',auth_uri:'https://accounts.google.com/o/oauth2/auth',token_uri:'https://oauth2.googleapis.com/token',redirect_uris:['http://localhost']}};
  await page.setInputFiles('#client',{name:'synthetic-client.json',mimeType:'application/json',buffer:Buffer.from(JSON.stringify(client))});await page.click('#import');await page.waitForFunction(()=>document.querySelector('#progress').dataset.client==='true');
  await page.fill('#account','owner@example.test');await page.click('#connect');await page.locator('#authorize').waitFor({state:'visible'});
  async function authorize(){await page.locator('#authorize').waitFor({state:'visible'});const promise=page.waitForEvent('popup');await page.click('#authorize');const popup=await promise;await popup.waitForURL(/\/callback\//);await popup.waitForFunction(()=>document.body.textContent.includes('授权完成'));await popup.close();}
  await authorize();await page.click('#sync');await page.waitForFunction(()=>!document.querySelector('#sync').disabled&&document.querySelector('#progress').dataset.status==='ready');
  check('假授权与回填进度', (await page.locator('#progress').textContent()).includes('本机缓存：1 封'));
  await page.fill('#query','SYNTHETIC');await page.click('#search');await page.locator('#results button').first().waitFor();await page.locator('#results button').first().click();await page.locator('#reader').waitFor({state:'visible'});check('本人私有搜索和详情', (await page.locator('#body').textContent())==='SYNTHETIC_MAIL_BODY');
  await page.screenshot({path:path.join(OUT,'gmail-detail-'+width+'.png'),fullPage:true});
  const pub=await page.evaluate(async()=>{const x=await fetch('/api/knowledge/search?q=SYNTHETIC');return x.json();});check('公共检索无邮件标题片段计数',pub.total===0&&pub.results.length===0);
  await fetch(urls.fake+'/control',{method:'POST',body:JSON.stringify({label:['TRASH']})});await page.click('#sync');await page.waitForFunction(()=>!document.querySelector('#sync').disabled);await page.click('#search');await page.waitForFunction(()=>document.querySelector('#count').textContent==='0 封本机私有邮件');check('标签撤回立即隐藏',await page.locator('#results button').count()===0);
  await fetch(urls.fake+'/control',{method:'POST',body:JSON.stringify({fail:true})});await page.click('#sync');await page.waitForFunction(()=>document.querySelector('#notice').textContent.includes('重新连接')&&document.querySelector('#progress').dataset.status==='needs_auth');check('授权失败可见',(await page.locator('#progress').textContent()).includes('重新连接'));
  await fetch(urls.fake+'/control',{method:'POST',body:JSON.stringify({clear:true,label:['INBOX']})});await page.click('#connect');await authorize();await page.click('#sync');await page.waitForFunction(()=>!document.querySelector('#sync').disabled&&document.querySelector('#progress').dataset.status==='ready');
  await page.click('#pause');await page.waitForFunction(()=>document.querySelector('#progress').dataset.status==='paused');check('重连后可暂停',true);
  const audit=await (await fetch(urls.fake+'/audit')).json();check('无附件读取或邮件写入',audit.every(([method,p])=>method==='GET'||p==='token')&&audit.every(([,p])=>!p.includes('/attachments/')&&!p.includes('/send')));
  check('宽度无溢出',await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));await page.screenshot({path:path.join(OUT,'gmail-'+width+'.png'),fullPage:true});
 }finally{if(context)await context.close();child.kill('SIGTERM');}
}
(async()=>{try{await launch(1440);await launch(390);assert.deepEqual(pageerrors,[]);assert.deepEqual(external,[]);assert.deepEqual(consoleErrors.filter(x=>!x.expected),[]);fs.writeFileSync(path.join(OUT,'browser-results.json'),JSON.stringify({records,pageerrors,external,consoleErrors,network},null,2));console.log(JSON.stringify({passed:records.length,pageerrors:pageerrors.length,external:external.length,evidence:OUT}));}catch(e){fs.writeFileSync(path.join(OUT,'browser-failure.json'),JSON.stringify({error:String(e),records,pageerrors,external},null,2));console.error(e);process.exitCode=1;}})();
