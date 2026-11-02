'use strict';
// Real headless Chrome, its own /tmp profile; only an isolated read-only server is allowed.
const fs=require('node:fs'),path=require('node:path'),os=require('node:os'),assert=require('node:assert/strict'),crypto=require('node:crypto');
const {chromium}=require(path.join(os.homedir(),'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright'));
const [base,outArg,mode='performance',countArg='20',mutantArg]=process.argv.slice(2),count=Number(countArg);
if(!/^http:\/\/127\.0\.0\.1:\d+$/.test(base)||[8766,3000,18766].includes(Number(new URL(base).port)))throw Error('仅限随机端口副本');
fs.mkdirSync(outArg,{recursive:true});const out=fs.realpathSync(outArg),anchor=fs.realpathSync('/tmp/p0');if(!out.startsWith(anchor+path.sep))throw Error('证据仅限/tmp/p0');
const sleep=ms=>new Promise(r=>setTimeout(r,ms)),sha=value=>crypto.createHash('sha256').update(value).digest('hex');
const definitions=[{id:'knowledge',other:'clues',selector:'#kb-results .kb-result',meta:'/api/knowledge',api:'/api/knowledge/search'},{id:'clues',other:'knowledge',selector:'#cl-list [data-clue]',meta:'/api/knowledge/meta',api:'/api/clues'}];
const results=[],errors=[];let active;const mutant=mutantArg?fs.realpathSync(mutantArg):null;if(mutant&&!mutant.startsWith(anchor+path.sep))throw Error('变异仅限/tmp/p0');
async function create(){const profile=fs.mkdtempSync(path.join(out,'profile-'));const context=await chromium.launchPersistentContext(profile,{headless:true,channel:'chrome',viewport:{width:1440,height:1000},args:['--disable-background-networking']});const gate={target:null};await context.route('**/*',async route=>{const request=route.request(),url=new URL(request.url());if(url.origin!==base||request.method()!=='GET')return route.abort();if(mutant&&['/knowledge.js','/clues.js'].includes(url.pathname)){const file=path.join(mutant,url.pathname.slice(1));if(fs.existsSync(file))return route.fulfill({status:200,contentType:'application/javascript',body:fs.readFileSync(file,'utf8')});}if(gate.target&&url.pathname===gate.target&&mode==='delayed_metadata')await sleep(2000);try{return await route.continue()}catch{}});const page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));return{profile,context,page,gate};}
async function close(item){await item.context.close();fs.rmSync(item.profile,{recursive:true,force:true});}
(async()=>{try{
 for(const definition of definitions){
  for(const cache of (mode==='performance'?['cold','hot']:['cold'])){
   if(cache==='hot')active=await create();
   for(let index=0;index<count;index++){
    const item=cache==='cold'?await create():active;const {page,context,gate}=item;
    try{
     gate.target=null;await page.goto(base+'/'+definition.other+'.html',{waitUntil:'domcontentloaded'});await page.locator(definitions.find(d=>d.id===definition.other).selector).nth(4).waitFor({state:'visible',timeout:15000});
     const cdp=await context.newCDPSession(page);await cdp.send('Network.setCacheDisabled',{cacheDisabled:cache==='cold'});if(cache==='cold')await cdp.send('Network.clearBrowserCache');await cdp.detach();
     const observations=[];const onRequest=request=>{const url=new URL(request.url());if(url.pathname===definition.api)observations.push({path:url.pathname,requested_ms:Date.now()-started})};
     const started=Date.now();page.on('request',onRequest);gate.target=definition.meta;
     const navigation=page.waitForURL(base+'/'+definition.id+'.html');await page.locator('#workspace-nav a[href="/'+definition.id+'.html"]').click();await navigation;
     await page.locator(definition.selector).nth(4).waitFor({state:'visible',timeout:15000});
     const ms=Date.now()-started;page.off('request',onRequest);
     const cards=await page.locator(definition.selector).evaluateAll(nodes=>nodes.map(n=>({id:n.dataset.document||n.dataset.clue,titleLength:n.querySelector('h3')?.textContent.trim().length||0,textLength:n.textContent.trim().length})));
     assert(cards.length>=5&&cards.every(c=>c.id&&c.titleLength>0&&c.textLength>c.titleLength),'没有真实可用列表');assert(observations.length>=1,'未走真实列表API');
     const record={page:definition.id,cache,index,ms,rows:cards.length,ids_sha256:sha(cards.map(c=>c.id).join('\n')),api:observations};results.push(record);
     if(mode==='delayed_metadata'){assert(ms<1000,'串行等待元数据，列表超过1秒');const first=await page.locator(definition.selector).first().elementHandle();await sleep(2200);assert(await first.evaluate(el=>el.isConnected),'迟到元数据重建了列表');}
     if(mode==='slow_health'){await page.waitForTimeout(150);const status=await page.locator(definition.id==='knowledge'?'#kb-source-status':'#cl-coverage-warning').innerText();assert(/未知|最近已知/.test(status),'健康挂起未显示未知或最近已知');}
    }finally{if(cache==='cold')await close(item)}
   }
   if(active){await close(active);active=null}
  }
 }
 const groups={};for(const definition of definitions)for(const cache of ['cold','hot']){const times=results.filter(r=>r.page===definition.id&&r.cache===cache).map(r=>r.ms).sort((a,b)=>a-b);if(times.length)groups[definition.id+'_'+cache]={count:times.length,p50_ms:times[Math.floor(times.length*.5)],p95_ms:times[Math.ceil(times.length*.95)-1]};}
 if(mode==='performance')for(const group of Object.values(groups))assert(group.p95_ms<=1000,'页面首屏P95超过1秒');
 assert.equal(errors.length,0,'页面脚本错误');fs.writeFileSync(path.join(out,'browser-results.json'),JSON.stringify({passed:true,mode,groups,results,script_errors:errors,click_path:'loaded legacy page → real sidebar link → JS fetch → real Handler/SQLite → at least 5 usable rows',cold_definition:'fresh independent Chrome profile, browser cache cleared/disabled',hot_definition:'reused independent profile/browser cache',write_requests_allowed:false},null,2));console.log(JSON.stringify(groups));
}finally{if(active)await close(active)}})().catch(error=>{fs.writeFileSync(path.join(out,'browser-failure.json'),JSON.stringify({passed:false,error:error.message,assertion:error instanceof assert.AssertionError,results},null,2));console.error(error.message);process.exitCode=1});
