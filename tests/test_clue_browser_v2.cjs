'use strict';
// GET-only acceptance against the approved local server; persistent profile is newly generated under /tmp.
const fs=require('node:fs'),os=require('node:os'),path=require('node:path'),crypto=require('node:crypto');
const {chromium}=(()=>{try{return require('playwright')}catch(_){return require(path.join(os.homedir(),'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright'))}})();
const out=process.argv[2],phase=process.argv[3]||'before',base=process.argv[4]||'http://127.0.0.1:8766';
if(!out||!path.resolve(out).startsWith('/tmp/k1/'))throw Error('验收产物必须在本单 /tmp/k1 目录');
const sha=b=>crypto.createHash('sha256').update(b).digest('hex');
(async()=>{const tmp=fs.mkdtempSync('/tmp/k1/browser-'),context=await chromium.launchPersistentContext(path.join(tmp,'profile'),{headless:true,channel:'chrome',viewport:{width:1440,height:1000},args:['--disable-background-networking','--disable-component-update']});
const results=[];try{
if(process.argv.includes('--single-load'))await context.addInitScript(()=>{globalThis.setInterval=()=>0});
await context.route('**/*',route=>{const r=route.request(),u=new URL(r.url());return r.method()==='GET'&&u.origin===base?route.continue():route.abort();});
for(const pageName of (process.argv[5]?process.argv[5].split(','):['today','clues','library','knowledge'])){
 const page=await context.newPage(),responses=[],errors=[];page.on('pageerror',e=>errors.push(e.message));page.on('response',async r=>{if(new URL(r.url()).pathname.startsWith('/api/')){try{const b=await r.body();responses.push({path:new URL(r.url()).pathname+new URL(r.url()).search,status:r.status(),sha256:sha(b),bytes:b.length,error:r.status()>=400?(()=>{try{return JSON.parse(b).error||JSON.parse(b).message||null}catch(_){return 'non-json error'}})():undefined});fs.writeFileSync(path.join(out,phase+'-'+pageName+'-'+sha(r.url()).slice(0,12)+'.json'),b)}catch(_){}}});
 const expected={today:'/api/today',clues:'/api/clues',library:'/api/items',knowledge:'/api/knowledge/search'}[pageName];
 const main=page.waitForResponse(r=>new URL(r.url()).pathname===expected&&r.status()===200,{timeout:process.argv.includes('--single-load')?180000:90000});
 await page.goto(base+'/'+pageName+'.html',{waitUntil:'domcontentloaded',timeout:60000});await main;
 if(pageName==='clues')await page.locator('#cl-list .cl-card').first().waitFor({state:'visible',timeout:10000});
 if(pageName==='knowledge')await page.locator('#kb-results .kb-result').first().waitFor({state:'visible',timeout:10000});
 if(pageName==='today')await page.waitForFunction(()=>document.querySelector('#item-list')?.getAttribute('aria-busy')==='false');
 await page.waitForTimeout(1000);
 const knownRadar=r=>process.argv.includes('--allow-known-radar-unavailable')&&r.status===400&&r.path==='/api/clues/wechat/status'&&r.error==='微信雷达接口不可用；未运行工作台内的备用微信分析，已保存结果与检查点保留';
 if(errors.length||responses.some(r=>r.status>=400&&!knownRadar(r))){fs.writeFileSync(path.join(out,phase+'-'+pageName+'-errors.json'),JSON.stringify({errors,responses},null,2));throw Error(pageName+' 页面或接口错误');}
 const listHtml=pageName==='clues'?await page.locator('#cl-list').innerHTML():null;
 if(listHtml)fs.writeFileSync(path.join(out,phase+'-clues-list.html'),listHtml);
 const screenshot=await page.screenshot({path:path.join(out,phase+'-'+pageName+'.png'),fullPage:false});
 results.push({page:pageName,title:await page.title(),http_errors:responses.filter(x=>x.status>=400),known_source_observations:responses.filter(knownRadar),responses,script_errors:errors,screenshot_sha256:sha(screenshot),dom_sha256:sha(Buffer.from(await page.content()))});await page.close();
}
if(phase.startsWith('after')){
 const r=await context.request.get(base+'/api/clue-inbox');if(r.status()!==200)throw Error('新接口失败');const v=await r.json();if(!v.ready||v.items.length<5)throw Error('新接口未见真实线索');results.push({api:'/api/clue-inbox',count:v.items.length,ready:v.ready});
}
fs.writeFileSync(path.join(out,phase+'-browser.json'),JSON.stringify(results,null,2));console.log(JSON.stringify(results.map(x=>({page:x.page||x.api,error_count:(x.script_errors||[]).length,http_errors:x.http_errors?.length,count:x.count}))));
}catch(error){fs.writeFileSync(path.join(out,phase+'-failure.json'),JSON.stringify({error:error.message,completed_pages:results},null,2));throw error;}finally{await context.close()}})().catch(e=>{console.error(e);process.exitCode=1});
