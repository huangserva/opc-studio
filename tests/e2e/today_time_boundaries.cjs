"use strict";
// Exact production page logic in a random-port fixture; read-only browser control.
const fs=require('fs'),path=require('path'),os=require('os'),assert=require('assert/strict');
const {spawn}=require('child_process');const {chromium}=require(path.join(os.homedir(),'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright'));
const repo=path.resolve(__dirname,'../..'),temp=fs.mkdtempSync('/tmp/r4-rest/time-'),out=process.argv[2]||temp;
fs.mkdirSync(out,{recursive:true});let server,context;const checks=[],negative=[];
const code=`from pathlib import Path
import sys,json,os
from datetime import datetime,timezone,timedelta
sys.path.insert(0,${JSON.stringify(repo)})
import server,grok_bot_radar as grok,linuxdo_source,today_view as today
from types import SimpleNamespace
root=Path(${JSON.stringify(temp)});workspace=root/'workspace';workspace.mkdir();reports=root/'reports';(reports/'daily').mkdir(parents=True)
os.environ['GROK_BOT_REPORTS_DIR']=str(reports);grok.BUNDLE_PATH=root/'bundle.json';grok.BUNDLE_PATH.write_text('{"tweets":[],"vip_tweets":[]}');linuxdo_source.refresh_background=lambda *a,**k:None
clock=datetime.now(timezone.utc)
class H(server.Handler):
 def log_message(self,*a):pass
 def do_GET(self):
  global clock
  from urllib.parse import urlsplit,parse_qs
  u=urlsplit(self.path);q=parse_qs(u.query)
  if u.path=='/__clock':
   clock=datetime.fromisoformat(q['now'][0].replace('Z','+00:00'));midnight=clock.astimezone(grok.BEIJING).replace(hour=0,minute=0,second=0,microsecond=0);rows=[]
   for id,h,bookmarks in [('201',8,412),('202',4,111),('501',0,1),('502',0,1)]:
    stamp=midnight-timedelta(milliseconds=1) if id=='501' else midnight if id=='502' else clock-timedelta(hours=h)
    rows.append(dict(id=id,handle='fixture',author='作者',text='AI Agent 模型部署教程与开源API，可复现的详细实践。'*10,url='https://x.com/fixture/status/'+id,created_at=stamp.isoformat(),views=10000,likes=50,bookmarks=bookmarks,views_per_hour=1000,label='爆款',topic='Agent'))
   (reports/'daily/a.json').write_text(json.dumps(dict(posts=rows,collected_at=clock.isoformat())));return self.reply(200,dict(now=clock.isoformat()))
  if u.path=='/api/today':
   try:return self.reply(200,today.page_view(workspace,q,clock))
   except ValueError as e:return self.reply(400,dict(error=str(e)))
  return super().do_GET()
 def do_POST(self):return self.reply(403,{})
http=server.ThreadingHTTPServer(('127.0.0.1',0),H);http.store=SimpleNamespace(workspace=workspace);print(http.server_port,flush=True);http.serve_forever()
`;
(async()=>{try{
server=spawn(path.resolve(repo,'../article-importer/.venv/bin/python'),['-u','-c',code],{cwd:repo,stdio:['ignore','pipe','pipe']});let log='';server.stderr.on('data',b=>log+=b);const port=await new Promise((resolve,reject)=>{server.stdout.once('data',b=>resolve(Number(String(b).trim())));server.once('exit',()=>reject(Error(log)))});const base='http://127.0.0.1:'+port;
context=await chromium.launchPersistentContext(path.join(temp,'profile'),{headless:true,channel:'chrome'});await context.route('**/*',r=>new URL(r.request().url()).origin===base&&r.request().method()==='GET'?r.continue():r.abort());const page=await context.newPage();
for(const now of ['2026-10-30T16:00:00Z','2026-10-30T16:30:00Z','2026-10-30T23:59:00Z','2026-10-31T00:00:00Z','2026-10-31T00:01:00Z','2026-10-31T15:59:00Z','2026-10-30T23:59:59Z','2026-10-31T00:00:01Z']){
assert((await context.request.get(base+'/__clock?now='+encodeURIComponent(now))).ok());
await page.goto(base+'/today.html?range=48h');await page.locator('#item-list[aria-busy="false"]').waitFor();const ids=await page.locator('#item-list .item-card').evaluateAll(ns=>ns.map(n=>n.dataset.itemId));assert(ids.indexOf('x:201')>=0&&ids.indexOf('x:201')<ids.indexOf('x:202'));checks.push({now,range:'48h',ranking:'passed'});
await page.goto(base+'/today.html');await page.locator('#item-list[aria-busy="false"]').waitFor();const todayIds=await page.locator('#item-list .item-card').evaluateAll(ns=>ns.map(n=>n.dataset.itemId));assert(todayIds.includes('x:502')&&!todayIds.includes('x:501'));checks.push({now,range:'today',beijingMidnight:'passed'});
if(now==='2026-10-30T16:30:00Z'||now==='2026-10-30T23:59:00Z'){const oldFixturePass=todayIds.indexOf('x:201')>=0&&todayIds.indexOf('x:201')<todayIds.indexOf('x:202');assert.equal(oldFixturePass,false);negative.push({now,oldFixturePass});}
}
fs.writeFileSync(path.join(out,'results.json'),JSON.stringify({checks,negative},null,2));console.log(checks.length+' checks passed; '+negative.length+' old fixtures failed as expected');
}finally{if(context)await context.close();if(server)server.kill('SIGTERM')}})().catch(e=>{console.error(e);process.exitCode=1});
