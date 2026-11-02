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

 const initial=await browser.newContext();const list=await api(initial,'/api/deals');const newDeal=list.deals.find(d=>d.title==='大模型评测'),old=list.deals.find(d=>d.title==='AI for Math');
 check('仅新建一笔4000元未知进度商单',list.deals.length===35&&list.deals.filter(d=>d.title==='大模型评测').length===1&&newDeal.quote==='4000.00'&&newDeal.currency==='CNY'&&newDeal.stage==='unknown'&&newDeal.lifecycle.days===null&&newDeal.business_dates.length===0);
 const review=await api(initial,'/api/deals/review'),qs=review.proposals.filter(q=>q.source==='联系人线索上下文核对 · 黄总 2026-11-01');
 check('旧单4条新单3条均待确认',qs.filter(q=>q.deal_id===old.id).length===4&&qs.filter(q=>q.deal_id===newDeal.id).length===3);
 check('旧单日期进度与资金均未自动写入',old.stage==='unknown'&&old.business_dates.length===0&&old.receipts.length===0&&old.publications.length===0);
 await initial.close();
 for(const size of [{width:1440,height:900},{width:1024,height:768},{width:390,height:844}]){
  const name=size.width+'x'+size.height,ctx=await browser.newContext({viewport:size}),p=await ctx.newPage(),errors=[];p.on('pageerror',e=>{errors.push(e.message);console.error('页面JS错误',e.message)});p.on('console',m=>{if(m.type()==='error')console.error(m.text())});
  await p.route('**/api/**',r=>new URL(r.request().url()).pathname.startsWith('/api/deals')?r.continue():r.fulfill({json:{profiles:[],items:[],messages:[]}}));
  await p.goto(base+'/deals.html');try{await p.locator('.dl-board').waitFor()}catch(e){await p.screenshot({path:path.join(testRoot,'contact-failure-'+name+'.png')});console.error((await p.locator('body').innerText()).slice(-2500));throw e}await p.locator('.dl-unknown-board summary').click();const card=p.locator(`[data-card-deal="${newDeal.id}"]`);await card.scrollIntoViewIfNeeded();
  check('新单原文金额缺日期 '+name,(await card.innerText()).includes('4000rmb')&&(await card.innerText()).includes('缺日期')&&(await card.innerText()).includes('未付'));
  await p.screenshot({path:path.join(testRoot,'contact-board-'+name+'.png')});await p.locator('#dl-candidates').click();const toggle=p.locator('[data-review-toggle="proposals"]');if(await toggle.getAttribute('aria-expanded')==='false')await toggle.click();
  for(const q of qs)await p.locator(`[data-lifecycle-proposal="${q.id}"]`).waitFor();check('七条联系人建议可见 '+name,true);
  const chosen=qs.find(q=>q.deal_id===newDeal.id&&q.changes.stage);await p.locator(`[data-lifecycle-proposal="${chosen.id}"]`).click();await p.locator('#dl-candidate-dialog[open]').waitFor();
  check('阶段建议保留证据与人工确认 '+name,(await p.locator('#dl-candidate-evidence').innerText()).includes(chosen.evidence.excerpt));
  check('不确定阶段及进入日期均不预选 '+name,await p.locator('#dl-candidate-form [name=value]').inputValue()===''&&await p.locator('#dl-candidate-form [name=entered_on]').inputValue()===''&&!await p.locator('#dl-candidate-form').evaluate(f=>f.checkValidity()));
  await p.locator('#dl-candidate-form [type=submit]').click();check('未手选阶段不能确认 '+name,await p.locator('#dl-candidate-dialog').evaluate(e=>e.open));
  await p.screenshot({path:path.join(testRoot,'contact-candidate-'+name+'.png')});await p.keyboard.press('Escape');check('页面无JS错误 '+name,errors.length===0);await ctx.close();
 }
 const ctx=await browser.newContext({viewport:{width:1440,height:900}}),p=await ctx.newPage();await p.route('**/api/**',r=>new URL(r.request().url()).pathname.startsWith('/api/deals')?r.continue():r.fulfill({json:{profiles:[],items:[],messages:[]}}));await p.goto(base+'/deals.html');await p.locator('.dl-board').waitFor();await p.locator('#dl-candidates').click();const toggle=p.locator('[data-review-toggle="proposals"]');if(await toggle.getAttribute('aria-expanded')==='false')await toggle.click();
 for(const q of qs.filter(q=>q.deal_id===old.id&&q.changes.published_on)){
  await p.locator(`[data-lifecycle-proposal="${q.id}"]`).click();check('不确定日期不预填 '+q.changes.published_on,await p.locator('#dl-candidate-form [name=value]').inputValue()===''&&!await p.locator('#dl-candidate-form').evaluate(f=>f.checkValidity()));await p.locator('#dl-candidate-form [name=value]').fill(q.changes.published_on);await p.locator('#dl-candidate-form [type=submit]').click();await p.locator('#dl-candidate-dialog').waitFor({state:'hidden'});
 }
 const saved=await api(ctx,'/api/deals/'+old.id);check('两次实际发布日期逐条确认成功',JSON.stringify(saved.date_events.filter(x=>x.kind==='published_on').map(x=>x.date).sort())===JSON.stringify(['2026-10-01','2026-10-08'])&&saved.audit.filter(x=>x.action.includes('确认微信')).length===2);
 check('确认日期没有自动变进度或造发布到账记录',saved.stage==='unknown'&&saved.publications.length===0&&saved.receipts.length===0);await p.screenshot({path:path.join(testRoot,'two-publication-dates-confirmed.png')});await ctx.close();
 fs.writeFileSync(path.join(testRoot,'results.json'),JSON.stringify({checks,workspace},null,2));console.log('共 '+checks.length+' 项通过，证据 '+testRoot);
})().catch(e=>{console.error(e,stderr.slice(-1500));process.exitCode=1}).finally(async()=>{await browser?.close();child?.kill('SIGTERM')});
