'use strict';
// Real routes, isolated /tmp DB copy, random port, fresh headless profile.
const fs=require('fs'),path=require('path'),os=require('os'),net=require('net'),assert=require('assert/strict');
const {spawn}=require('child_process');
const {chromium}=(()=>{try{return require('playwright')}catch{return require(path.join(os.homedir(),'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright'))}})();
const root=path.resolve(__dirname,'..'),out=process.env.DEALS_LIFECYCLE_EVIDENCE;
assert(out,'请指定 DEALS_LIFECYCLE_EVIDENCE（含 workspace/deals/deals.db 的隔离目录）');
const testRoot=fs.mkdtempSync(path.join(out,'browser-')),workspace=path.join(testRoot,'workspace');fs.mkdirSync(path.join(workspace,'deals'),{recursive:true});fs.copyFileSync(path.join(out,'workspace/deals/deals.db'),path.join(workspace,'deals/deals.db'));
let child,browser,base,stderr='';const checks=[];function check(name,ok){assert(ok,name);checks.push(name);console.log('通过 '+name)}
const key=()=>require('crypto').randomUUID().replaceAll('-','');
async function api(ctx,url,data){const r=await ctx.request[data?'post':'get'](base+url,data?{data,headers:{'X-Article-Studio':'1','Origin':base}}:{});assert(r.ok(),url+': '+await r.text());return r.json()}
(async()=>{
 const port=await new Promise(r=>{const s=net.createServer().listen(0,'127.0.0.1',()=>{const p=s.address().port;s.close(()=>r(p))})});base='http://127.0.0.1:'+port;
 const code=`import sys;sys.path.insert(0,${JSON.stringify(root)});from server import Store,Handler;from http.server import ThreadingHTTPServer;s=ThreadingHTTPServer(('127.0.0.1',${port}),Handler);s.store=Store(${JSON.stringify(workspace)});s.serve_forever()`;
 child=spawn(path.resolve(root,'../article-importer/.venv/bin/python'),['-u','-c',code],{cwd:root,stdio:['ignore','ignore','pipe']});child.stderr.on('data',d=>stderr+=d);
 for(let i=0;i<100;i++){try{if((await fetch(base+'/api/deals/options')).ok)break}catch{}await new Promise(r=>setTimeout(r,100));if(child.exitCode!==null)throw Error(stderr)}
 browser=await chromium.launch({headless:true,channel:'chrome'});
 for(const size of [{width:1440,height:900},{width:1024,height:768},{width:390,height:844}]){
  const name=size.width+'x'+size.height,ctx=await browser.newContext({viewport:size}),p=await ctx.newPage();
  await p.route('**/api/**',r=>new URL(r.request().url()).pathname.startsWith('/api/deals')?r.continue():r.fulfill({json:{profiles:[],items:[],messages:[]}}));
  const errors=[];p.on('pageerror',e=>errors.push(e.message));
  try{
   const d=await api(ctx,'/api/deals',{title:'浏览器验收 '+name,customer:'隔离测试',stage:'writing',stage_entered_on:'',signed_on:'2026-09-01',deadline:'2026-11-08',request_key:key()});
   const month={1440:'05',1024:'06',390:'07'}[size.width];const receipt=await api(ctx,'/api/deals/'+d.id+'/receipt',{revision:d.revision,request_key:key(),amount:'125.50',currency:'CNY',paid_on:'2026-'+month+'-10'});await api(ctx,'/api/deals/'+d.id+'/publication',{revision:receipt.revision,request_key:key(),platform:'x',account:'fixture',url:'https://x.com/fixture/status/'+size.width,published_on:'2026-'+month+'-11'});
   await p.goto(base+'/deals.html');await p.locator('.dl-board').waitFor();
   check('默认看板列顺序 '+name,JSON.stringify(await p.locator('[data-board-stage]').evaluateAll(es=>es.map(e=>e.dataset.boardStage)))===JSON.stringify(['contact','negotiating','preparing','writing','draft','published','closed']));
   const card=p.locator(`[data-card-deal="${d.id}"]`);await card.scrollIntoViewIfNeeded();check('无日期显示缺日期 '+name,(await card.innerText()).includes('缺日期')&&!(await card.innerText()).match(/阶段 \d+ 天/));
   await p.screenshot({path:path.join(testRoot,'board-'+name+'.png')});
   const listing=await api(ctx,'/api/deals');check('手记11笔独立归位 '+name,listing.deals.filter(d=>d.original_group==='已付'&&d.stage==='published'&&d.cash.status==='reported_paid'&&d.receipts.length===0).length===11);
   await card.locator('[data-deal]').first().click();await p.locator('#dl-detail[open]').waitFor();check('生命周期7节点 '+name,await p.locator('.dl-lifecycle>li').count()===7);await p.screenshot({path:path.join(testRoot,'detail-'+name+'.png')});
   await p.locator('[data-stage-date="writing"]').click();await p.locator('#dl-stage-date-form [name=entered_on]').fill('2026-10-20');await p.locator('#dl-stage-date-form [type=submit]').click();await p.locator('#dl-stage-date-dialog').waitFor({state:'hidden'});await p.waitForFunction(()=>document.querySelector('[data-stage-date="writing"]').textContent==='2026-10-20');
   const updated=await api(ctx,'/api/deals/'+d.id);check('补日期保持进度且写audit '+name,updated.stage==='writing'&&updated.stage_history.at(-1).entered_on==='2026-10-20'&&updated.audit.some(a=>a.action.includes('补充阶段日期')));await p.screenshot({path:path.join(testRoot,'date-saved-'+name+'.png')});await p.keyboard.press('Escape');
   await p.locator('#dl-view').selectOption('timeline');await p.locator('#dl-timeline-list').waitFor();check('时间线按月显示发布与实际到账 '+name,(await p.locator('#dl-timeline-list').innerText()).includes('2026-'+month+' · 发布 1 次 · 实际到账 ¥125.50 CNY'));await p.screenshot({path:path.join(testRoot,'timeline-'+name+'.png')});
   await p.locator('#dl-candidates').click();const candidate=p.locator('[data-lifecycle-proposal]').first();await candidate.waitFor();const pid=await candidate.getAttribute('data-lifecycle-proposal');
   const before=await api(ctx,'/api/deals/review'),proposal=before.proposals.find(x=>x.id===pid),field=Object.keys(proposal.changes)[0];await candidate.click();await p.locator('#dl-candidate-dialog[open]').waitFor();
   check('建议原文时间会话齐全 '+name,(await p.locator('#dl-candidate-evidence').innerText()).includes('来源会话：')&&(await p.locator('#dl-candidate-evidence').innerText()).includes(proposal.evidence.excerpt));await p.screenshot({path:path.join(testRoot,'candidate-'+name+'.png')});
   if(field==='stage'){await p.locator('#dl-candidate-form [name=value]').selectOption('preparing');await p.locator('#dl-candidate-form [name=entered_on]').fill('2026-10-19')}else await p.locator('#dl-candidate-form [name=value]').fill('2026-10-19');
   await p.locator('#dl-candidate-form [type=submit]').click();await p.locator('#dl-candidate-dialog').waitFor({state:'hidden'});
   const saved=await api(ctx,'/api/deals/'+proposal.deal_id);check('修改后确认写入audit '+name,saved.audit.some(a=>a.action.includes('确认微信'))&&(field==='stage'?saved.stage==='preparing':['published_on','paid_on'].includes(field)?saved.date_events.some(x=>x.kind===field&&x.date==='2026-10-19'):saved[field]==='2026-10-19'));
   await p.locator('#dl-candidates').click();await p.locator('[data-lifecycle-proposal]').first().click();await p.locator('#dl-candidate-reject').click();await p.locator('#dl-candidate-dialog').waitFor({state:'hidden'});check('驳回成功 '+name,true);
   check('页面无JS错误 '+name,errors.length===0);
  }catch(e){await p.screenshot({path:path.join(testRoot,'failure-'+name+'.png')});console.error('页面错误回执',await p.locator('#dl-candidate-error').innerText());throw e}finally{await ctx.close()}
 }
 fs.writeFileSync(path.join(testRoot,'results.json'),JSON.stringify({checks,workspace},null,2));console.log('共 '+checks.length+' 项通过，证据 '+testRoot);
})().catch(e=>{console.error(e,stderr.slice(-1500));process.exitCode=1}).finally(async()=>{await browser?.close();child?.kill('SIGTERM')});
