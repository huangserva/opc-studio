'use strict';
// 所有请求仅GET；冷=新独立profile5次，热=同profile刷新5次，不清服务端持久索引。
const guard=require('./real_write_guard.cjs');guard.install();
const fs=require('fs'),path=require('path'),assert=require('assert/strict');
const [base,out,range='today']=process.argv.slice(2),settled=process.argv.includes('--settled');
const baselineArg=process.argv.indexOf('--baseline'),baseline=baselineArg<0?null:JSON.parse(fs.readFileSync(process.argv[baselineArg+1],'utf8'));
if(baseline){assert.equal(baseline.range,range,'旧版基线必须是同一时间范围');for(const k of ['coldP95','hotP95'])assert(Number.isFinite(baseline[k])&&baseline[k]>0,'旧版基线必须有冷热各5次有效P95');assert.equal(baseline.cold.length,5);assert.equal(baseline.hot.length,5);}
if(!/^http:\/\/127\.0\.0\.1:\d+$/.test(base)||!['today','7d'].includes(range))throw Error('只允许本机只读验证');
if(new URL(base).port==='8766')assert(baseline,'真实8766必须传同页旧版冷热5次基线');
guard.allowRead(base);fs.mkdirSync(out,{recursive:true});const {chromium}=guard.playwright();const cold=[],hot=[],errors=[];let context;
async function open(profile){return chromium.launchPersistentContext(profile,{headless:true,channel:'chrome',viewport:{width:1440,height:1000},args:['--disable-background-networking','--disable-component-update']})}
async function measure(page,phase,index){
 const response=page.waitForResponse(r=>{const u=new URL(r.url());return u.pathname==='/api/today'&&u.searchParams.get('channel')==='interest'&&u.searchParams.get('range')===range});
 const started=performance.now();await page.goto(base+'/today.html?channel=interest&range='+range);
 await page.locator('#item-list[aria-busy=false]:not([inert]) .item-card').first().waitFor();
 await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>resolve())));
 const ms=performance.now()-started,body=await response.then(r=>r.json());assert.equal(body.interest_cache.partial,false,'首屏必须有完整可用快照，首次准备不能计作通过');assert.equal(body.interest_cache.projection?.source,'readmodel');assert(body.total>0&&body.items.length>0,'非空首卡必须来自真实完整快照');
 if(!body.interest_cache.ready){assert.equal(body.interest_cache.refreshing,true,'旧完整快照必须明确提示刷新');assert.match(await page.locator('.interest-index-status').innerText(),/暂显示上次完整结果/);}
 assert.equal(await page.locator('.error').count(),0);assert.equal(await page.locator('#item-list').getAttribute('aria-busy'),'false');
 const value={phase,index,ms:Math.round(ms*100)/100,total:body.total,ready:body.interest_cache.ready,partial:body.interest_cache.partial,refreshing:body.interest_cache.refreshing,snapshot_available:!body.interest_cache.partial,built_at:body.interest_cache.built_at};console.log(JSON.stringify(value));return value;
}
(async()=>{try{
 for(let i=0;i<5;i++){
  context=await open(fs.mkdtempSync(path.join(out,'cold-profile-')));await context.route('**/*',r=>new URL(r.request().url()).origin===base&&r.request().method()==='GET'?r.continue():r.abort());const page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));cold.push(await measure(page,'cold',i));if(i===0)await page.screenshot({path:path.join(out,'cold-first.png')});if(settled)await page.waitForLoadState('networkidle');await context.close();context=null;
 }
 context=await open(fs.mkdtempSync(path.join(out,'hot-profile-')));await context.route('**/*',r=>new URL(r.request().url()).origin===base&&r.request().method()==='GET'?r.continue():r.abort());const page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));
 await measure(page,'warmup',0);if(settled)await page.waitForLoadState('networkidle');for(let i=0;i<5;i++){hot.push(await measure(page,'hot',i));if(settled)await page.waitForLoadState('networkidle');}
 const p95=rows=>Math.max(...rows.map(r=>r.ms));const result={base,range,cold,hot,coldP95:p95(cold),hotP95:p95(hot),definition:'冷=独立全新浏览器profile；热=同profile刷新；服务端持久索引保留；完整旧快照可读且刷新提示可见也算可用，首次partial不通过；P95=n5最近秩最大值',betweenRuns:settled?'计时结束后等networkidle再开下一轮，预取照常':'连续轮次，后台预取可能重叠',errors,baseline:baseline?{coldP95:baseline.coldP95,hotP95:baseline.hotP95}:null,withinOneSecond:p95(cold)<=1000&&p95(hot)<=1000,limits:{absolute_ms:3000,baseline_multiplier:2,one_second_informational:true,rollback_logic:"and_per_phase"}};fs.writeFileSync(path.join(out,'results.json'),JSON.stringify(result,null,2));assert.equal(errors.length,0);if(baseline)assert(!((result.coldP95>3000&&result.coldP95>2*baseline.coldP95)||(result.hotP95>3000&&result.hotP95>2*baseline.hotP95)),'同页P95同时超过3秒且超过旧版2倍');else assert(result.coldP95<=3000&&result.hotP95<=3000,'未提供旧版基线，只能判绝对3秒');console.log('冷/热P95通过新裁定 '+result.coldP95+' / '+result.hotP95);
 }catch(e){fs.writeFileSync(path.join(out,'failure.json'),JSON.stringify({cold,hot,errors,error:e.stack},null,2));console.error(e);process.exitCode=1}finally{await context?.close()}})();
