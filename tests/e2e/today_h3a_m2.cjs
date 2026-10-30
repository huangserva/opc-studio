'use strict';
// M2 真实 8766 只读验收：隔离 Chromium profile；不写反馈或任务。
const fs=require('fs'),path=require('path'),os=require('os'),assert=require('assert/strict');
const {chromium}=(()=>{try{return require('playwright')}catch(_){return require(path.join(os.homedir(),'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright'))}})();
if(!process.argv.includes('--real'))throw Error('仅合同授权时使用 --real');
const out=path.resolve(__dirname,'../../coordination/evidence/impl-hub-h3a/m2'),base='http://127.0.0.1:8766';
const rows=JSON.parse(fs.readFileSync(path.resolve(__dirname,'../fixtures/today_h3a_m1.json'))),checks=[],errors=[];
const check=(name,ok)=>{assert.ok(ok,name);checks.push({name,passed:true});console.log('通过 '+name);};
let context;
(async()=>{try{
 const tmp=fs.mkdtempSync('/tmp/h3a-m1-real-');
 context=await chromium.launchPersistentContext(path.join(tmp,'profile'),{headless:true,channel:'chrome',viewport:{width:1440,height:1000},args:['--disable-background-networking','--disable-component-update']});
 await context.route('**/*',r=>new URL(r.request().url()).origin===base&&r.request().method()==='GET'?r.continue():r.abort());
 const get=async url=>{const r=await context.request.get(base+url);assert.equal(r.status(),200);return r.json();};
 let ready=false;for(let i=0;i<50;i++){try{await get('/api/today');ready=true;break;}catch(error){if(i===49)throw error;await new Promise(r=>setTimeout(r,200));}}assert.ok(ready,'重启后的服务就绪');
 const durations=[];let listing;
 for(let i=0;i<5;i++){const t=performance.now();listing=await get('/api/today');durations.push(performance.now()-t);}
 // 耗时只保存原始值；撤回M1的每次小于1秒承诺。
 const offline=JSON.parse(fs.readFileSync(path.join(out,'today-after.json')));
 check('真实来源计数与同口径回放一致',JSON.stringify(listing.filtering.by_source)===JSON.stringify(offline.filtering.by_source));
 check('真实51人原始统计与回放一致',listing.filtering.aisa_all.total===offline.filtering.aisa_all.total&&listing.filtering.aisa_all.filtered===offline.filtering.aisa_all.filtered);
 const countsText=await (await context.request.get(base+'/api/today?range=7d')).json();
 const six=JSON.parse(fs.readFileSync(path.resolve(__dirname,'../fixtures/today_h3a_m2.json')));
 const hiddenWeek=new Set(countsText.filtering.aisa_all_items.filter(r=>r.hidden).map(r=>r.id));
 for(const row of six)check('七天真实原始统计不再隐藏六条误杀 '+row.id,!hiddenWeek.has(row.id));
 const hidden=new Set(listing.filtering.aisa_all_items.filter(r=>r.hidden).map(r=>r.id));
 for(const row of rows.filter(r=>r.sources.includes('A')))check((row.expected?'正例恢复相关性保留 ':'反例仍被隐藏 ')+row.id,hidden.has('x:'+row.id)!==row.expected);
 const patterns=[/亲哥欠钱\s*10w/,/被.*骗.*钱.*追回/,/不限次数的\s*seedance2\.0/i];
 for(const pattern of patterns)check('三条硬性样例继续隐藏 '+pattern,listing.filtering.items.some(r=>pattern.test(r.title)&&r.hidden&&r.reason.startsWith('分类黑名单：')));
 const page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));
 await page.goto(base+'/today.html');await page.waitForFunction(()=>document.querySelector('#item-list').getAttribute('aria-busy')==='false');await page.locator('#filtered-summary').click();
 const candidates=page.locator('[data-filter-group=candidates]'),raw=page.locator('[data-filter-group=aisa-raw]');
 const sourceText=await page.locator('#filtered-counts').innerText();
 for(const [name,c] of Object.entries(listing.filtering.by_source))check('页面来源条数 '+name,sourceText.includes(name+'：'+c.total+' → '+c.visible+' 条，已过滤 '+c.filtered+' 条'));
 check('页面51人采样比例来自新计数',sourceText.includes('AIsa 51 人全量推文：'+listing.filtering.aisa_all.total+' → '+listing.filtering.aisa_all.visible+' 条，已过滤 '+listing.filtering.aisa_all.filtered+' 条（'+(100*listing.filtering.aisa_all.ratio).toFixed(1)+'%）'));
 check('主列表候选和51人统计明确分组',await candidates.count()===1&&await raw.count()===1);
 check('原始统计明确标注不属于主列表候选',(await raw.innerText()).includes('原始采样统计，不属于主列表候选'));
 check('原始采样项未混入候选分组',await candidates.locator('[data-audit-id^="x:"]').evaluateAll(nodes=>nodes.every(n=>!n.textContent.includes('AIsa 51 人全量推文'))));
 const financialId='x:2115900089075995087';
 check('无关金融原帖在原始统计与候选分组均按各自API输入准确呈现',await raw.locator('[data-audit-id="'+financialId+'"]').count()===Number(listing.filtering.aisa_all_items.some(r=>r.id===financialId))&&await candidates.locator('[data-audit-id="'+financialId+'"]').count()===Number(listing.filtering.items.some(r=>r.id===financialId)));
 check('无关金融原帖两个统计口径均判隐藏',listing.filtering.aisa_all_items.some(r=>r.id===financialId&&r.hidden)&&listing.filtering.items.some(r=>r.id===financialId&&r.hidden));
 check('原始组条目均有可核验来源与原因',await raw.locator('li[data-audit-id]').evaluateAll(ns=>ns.length>0&&ns.every(n=>n.textContent.includes(' · ')&&n.querySelector('a[href]'))));
 const linux=await get('/api/today?source=L&range=7d');check('linux.do套餐用量正例回到主列表',linux.items.some(r=>r.title.toLowerCase().includes('tibo')));
 for(const width of [1440,390]){
  await page.setViewportSize({width,height:width===1440?1000:844});
  await candidates.locator('h3').scrollIntoViewIfNeeded();await page.screenshot({path:path.join(out,'real-'+width+'-candidates.png'),fullPage:true});
  for(let i=0;i<patterns.length;i++){await candidates.locator('a').filter({hasText:patterns[i]}).scrollIntoViewIfNeeded();await page.screenshot({path:path.join(out,'real-'+width+'-blacklist-'+(i+1)+'.png'),fullPage:true});}
  await raw.locator('h3').scrollIntoViewIfNeeded();await page.screenshot({path:path.join(out,'real-'+width+'-aisa-raw.png'),fullPage:true});
  check(width+'页面无横向溢出',await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
 }
 check('无页面脚本错误',errors.length===0);
 const {clue_morning,...safeListing}=listing;
 fs.writeFileSync(path.join(out,'real-today.json'),JSON.stringify(safeListing,null,2));
 fs.writeFileSync(path.join(out,'real-timings.json'),JSON.stringify(durations));
 }finally{fs.writeFileSync(path.join(out,'real-e2e-results.json'),JSON.stringify({checks,errors},null,2));if(context)await context.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
