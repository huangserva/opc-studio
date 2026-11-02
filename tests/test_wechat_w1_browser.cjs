'use strict';
const fs=require('fs'),path=require('path'),http=require('http'),os=require('os'),assert=require('node:assert/strict');
const {chromium}=require(path.join(os.homedir(),'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright'));
const APP=path.resolve(__dirname,'..'),E=fs.mkdtempSync(path.join(os.tmpdir(),'wechat-w1-browser-')),results=[],requests=[];
const historical=Array.from({length:491},(_,i)=>({batch_id:i.toString(16).padStart(64,'0'),message:'Pi 分析未返回有效正文，本批未推进：模型执行失败',state:'needs_review',attempts:3,documents:1,next_retry_at:null}));
const defs=[
 ['本轮完成','succeeded','wechat_clues',{processed:3,skipped:0,clues:[],has_more:false,errors:[]}],
 ['C1预算续跑','partial','wechat_clues',{processed:352,skipped:89,clues:[],has_more:true,time_budget:true,deferred:9198,errors:historical}],
 ['C2采集失败','failed','wechat_clues',{processed:0,clues:[],has_more:true,errors:[{message:'微信采集或索引未成功；未开始模型分析'}],completion:{type:'failed',phase:'capture',reason:'索引失败，已有资料保留',processed:0,remaining_scope:'最近七天未处理消息'}}],
 ['C3历史队列','failed','wechat_clues',{processed:0,clues:[],has_more:true,deferred:9198,errors:historical}],
 ['D1商单轮转','partial','deals_review',{progress:{checked:35},evidence:{checked:6,deferred:29},conflicts:{deferred:0},has_more:true,errors:[]}],
 ['W2回执待确认','timeout','wechat_radar',{source_run_id:'f'.repeat(32),receipt_pending:true,source_receipt_final:false}],
 ['XSS反例','partial','wechat_radar',{processed:4,has_more:true,completion:{type:'continuation',phase:'analysis',reason:'安全',processed:4,remaining_scope:'<img src=x onerror="window.injected=true"><script>window.injected=true</script>'}}]
];
const runs=defs.map(([name,status,kind,data],i)=>({id:(i+1).toString(16).padStart(32,'0'),task_id:(i+11).toString(16).padStart(32,'0'),snapshot:{name,kind,revision:6,wechat_mode:'incremental',body:'合成回放'},status,output:JSON.stringify(data),started_at:'2026-11-02T06:00:00Z',finished_at:'2026-11-02T06:01:00Z',exit_code:null}));
const tasks=runs.map(r=>({id:r.task_id,name:r.snapshot.name,kind:r.snapshot.kind,enabled:false,revision:6,last_run:r,minutes:60,frequency:'interval',body:'合成回放'}));
const server=http.createServer((req,res)=>{
 requests.push({method:req.method,path:req.url});if(req.method!=='GET'){res.writeHead(403);return res.end('{}');}
 const u=new URL(req.url,'http://127.0.0.1');let d;
 if(u.pathname==='/api/schedules')d={tasks,models:[],default_cwd:'/tmp',scheduler_health:{running:true},external:{tasks:[]}};
 else if(u.pathname==='/api/schedules/runs')d={runs,total:runs.length,page:1,has_more:false};
 else if(u.pathname.startsWith('/api/schedules/run/'))d=runs.find(r=>r.id===u.pathname.split('/').pop());
 else if(u.pathname.startsWith('/api/'))d={};
 if(d!==undefined){res.writeHead(200,{'Content-Type':'application/json'});return res.end(JSON.stringify(d));}
 const p=path.join(APP,'public',u.pathname==='/'?'schedules.html':u.pathname);
 if(!p.startsWith(path.join(APP,'public')+path.sep)||!fs.existsSync(p)){res.writeHead(404);return res.end('');}
 res.writeHead(200,{'Content-Type':p.endsWith('.js')?'application/javascript':p.endsWith('.css')?'text/css':'text/html'});res.end(fs.readFileSync(p));
});
(async()=>{let browser;
 try{
 await new Promise(r=>server.listen(0,'127.0.0.1',r));const base='http://127.0.0.1:'+server.address().port;
 browser=await chromium.launch({headless:true,executablePath:'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'});const context=await browser.newContext({viewport:{width:1440,height:1100}});const page=await context.newPage();
 const errors=[];page.on('pageerror',e=>errors.push(e.message));await page.route('**/*',route=>new URL(route.request().url()).origin===base?route.continue():route.abort());
 await page.goto(base+'/schedules.html');await page.locator('#run-tab').click();await page.locator('.run-row').nth(6).waitFor();
 const check=(name,fn)=>{fn();results.push({name,passed:true});};const rows=page.locator('.run-row');
 const kinds=await rows.evaluateAll(x=>x.map(r=>r.dataset.completion));check('三类及待确认类型',()=>assert.deepEqual(kinds,['completed','continuation','failed','continuation','continuation','pending','continuation']));
 const text=await page.locator('#runs').innerText();check('C1处理与积压数',()=>assert.match(text,/已处理 352[\s\S]*待重试或核对 9198 条/));
 check('C2阶段',()=>assert.match(text,/真失败[\s\S]*阶段 capture/));check('商单上限与余量',()=>assert.match(text,/29 项 · 每轮最多 6 笔轮转/));
 check('W2无虚构零',()=>assert.match(text,/回执待确认 · 已处理 数量未确认/));
 
 assert.equal(await rows.nth(1).locator('.state-pill').getAttribute('data-status'),'partial');
 assert.equal(await rows.nth(3).locator('.state-pill').getAttribute('data-status'),'failed');results.push({name:'C3原failed保留',passed:true});
 assert.equal(await page.locator('#runs img').count(),0);assert.equal(await page.evaluate(()=>Boolean(window.injected)),false);results.push({name:'XSS纯文本',passed:true});
 const colors=await rows.nth(1).locator('.state-pill').evaluate(e=>({color:getComputedStyle(e).color,text:e.textContent}));assert.equal(colors.text,'续跑中');assert.equal(colors.color,'rgb(66, 110, 155)');const c3color=await rows.nth(3).locator('.state-pill').evaluate(e=>getComputedStyle(e).backgroundColor);assert.equal(c3color,'rgb(237, 245, 255)');results.push({name:'续跑非红色',passed:true,colors});
 await page.screenshot({path:path.join(E,'schedules-1440.png'),fullPage:true});await rows.nth(2).locator('[data-run]').click();await page.locator('#run-detail[open]').waitFor();
 assert.match(await page.locator('#run-events').innerText(),/真失败/);assert.match(await page.locator('#run-output').innerText(),/completion/);results.push({name:'详情保留原始回执',passed:true});
 await page.screenshot({path:path.join(E,'detail-1440.png')});await page.locator('#run-detail [data-close]').click();await page.setViewportSize({width:390,height:844});await page.screenshot({path:path.join(E,'schedules-390.png'),fullPage:true});
 assert.equal(errors.length,0);assert.equal(requests.filter(r=>r.method!=='GET').length,0);results.push({name:'隔离无写请求与页面错误',passed:true});
 fs.writeFileSync(path.join(E,'browser-results.json'),JSON.stringify({results,requests,errors},null,2));console.log(JSON.stringify({passed:results.length,results}));
 }catch(e){fs.writeFileSync(path.join(E,'browser-failure.json'),JSON.stringify({error:e.message,results},null,2));throw e;}
 finally{if(browser)await browser.close();server.close();}
})();
