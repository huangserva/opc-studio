'use strict';
// 合同授权的 8766，只使用独立 headless profile；不改真实反馈或作品。
const fs=require('fs'),path=require('path'),os=require('os'),assert=require('assert/strict');
const {chromium}=(()=>{try{return require('playwright')}catch(_){return require(path.join(os.homedir(),'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright'))}})();
const base='http://127.0.0.1:8766',out=path.resolve(__dirname,'../../coordination/evidence/impl-hub-h3a');
if(!process.argv.includes('--real'))throw Error('此脚本只在合同授权时加 --real 执行');
const tmp=fs.mkdtempSync('/tmp/today-h3a-'),checks=[],errors=[];
let context;
const check=(name,ok)=>{assert.ok(ok,name);checks.push({name,passed:true});console.log('通过 '+name);};
(async()=>{try{
 context=await chromium.launchPersistentContext(path.join(tmp,'profile'),{headless:true,channel:'chrome',viewport:{width:1440,height:1000},args:['--disable-background-networking','--disable-component-update']});
 await context.route('**/*',r=>new URL(r.request().url()).origin===base?r.continue():r.abort());
 const get=async url=>{const r=await context.request.get(base+url);assert.equal(r.status(),200);return r.json();};
 let listing;
 for(let i=0;i<60;i++){listing=await get('/api/today');if(!listing.sources.L.refreshing)break;await new Promise(r=>setTimeout(r,1000));}
 check('原始51人采样统计可用',listing.filtering.aisa_all.available&&listing.filtering.aisa_all.total>0);
 const patterns=[/亲哥欠钱\s*10w/,/被.*骗.*钱.*追回/,/不限次数的\s*seedance2\.0/i];
 for(const pattern of patterns){const row=listing.filtering.items.find(p=>pattern.test(p.title));check('指定帖子进入已过滤 '+pattern,!!row&&row.hidden&&row.reason.startsWith('分类黑名单：'));}
 const rows=[...listing.items];for(let p=2;rows.length<listing.total;p++)rows.push(...(await get('/api/today?page='+p)).items);
 check('三条指定帖子均不在今日主列表',rows.every(r=>!patterns.some(p=>p.test(r.title))));
 check('各来源数量前后相符',Object.values(listing.filtering.by_source).every(c=>c.total===c.visible+c.filtered));
 const page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));
 await page.goto(base+'/today.html');await page.waitForFunction(()=>document.querySelector('#item-list').getAttribute('aria-busy')==='false');
 await page.locator('#filtered-summary').click();
 for(const pattern of patterns)check('过滤明细可见 '+pattern,await page.locator('#filtered-items').getByText(pattern).count()>0);
 check('明细原帖链接安全可点',await page.locator('#filtered-items a[href]').evaluateAll(as=>as.length>0&&as.every(a=>/^https?:/.test(a.href)&&a.rel==='noopener noreferrer')));
 check('AIsa全量过滤数比例可见',(await page.locator('#filtered-counts').innerText()).includes('AIsa 51 人全量推文：'));
 await page.screenshot({path:path.join(out,'real-1440-filtered.png'),fullPage:true});
 for(let i=0;i<patterns.length;i++){await page.locator('#filtered-items a').filter({hasText:patterns[i]}).scrollIntoViewIfNeeded();await page.screenshot({path:path.join(out,'real-1440-filtered-'+(i+1)+'.png'),fullPage:true});}
 await page.setViewportSize({width:390,height:844});await page.screenshot({path:path.join(out,'real-390-filtered.png'),fullPage:true});
 for(let i=0;i<patterns.length;i++){await page.locator('#filtered-items a').filter({hasText:patterns[i]}).scrollIntoViewIfNeeded();await page.screenshot({path:path.join(out,'real-390-filtered-'+(i+1)+'.png'),fullPage:true});}
 check('390页面无横向溢出',await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
 await page.locator('#filtered-summary').click();await page.screenshot({path:path.join(out,'real-390-list.png'),fullPage:true});
 await page.setViewportSize({width:1440,height:1000});await page.screenshot({path:path.join(out,'real-1440-list.png'),fullPage:true});
 if(listing.total>50&&rows.at(-1).content_insufficient){const loaded=page.waitForResponse(r=>new URL(r.url()).pathname==='/api/today'&&new URL(r.url()).searchParams.get('page')==='2');await page.locator('.pagination button').last().click();await loaded;await page.waitForFunction(()=>!document.querySelector('#item-list').inert);check('内容不足实际保留后排并显示卡片标记',await page.locator('.item-card[data-item-id="'+rows.at(-1).id+'"] .interest-warning').getByText('内容不足',{exact:true}).count()>0);await page.screenshot({path:path.join(out,'real-insufficient-last-page.png'),fullPage:true});}
 const linux=await get('/api/today?source=L&range=7d');
 check('linux.do统一来源保留AI标签及周榜标注',linux.items.some(p=>p.rss_boards.includes('AI 标签'))&&linux.items.some(p=>p.rss_boards.includes('周榜')));
 const tasks=(await get('/api/schedules')).tasks,task=tasks.find(t=>t.name==='linuxdo 热帖报告');
 check('内置脚本任务启用且每6小时',!!task&&task.kind==='script'&&task.enabled&&task.minutes===360);
 await page.goto(base+'/schedules.html');await page.locator('[data-action=run][data-id="'+task.id+'"]').waitFor();
 const response=page.waitForResponse(r=>r.url()===base+'/api/schedules/'+task.id+'/run'&&r.request().method()==='POST');
 await page.locator('[data-action=run][data-id="'+task.id+'"]').click();assert.equal((await response).status(),200);
 let runs;
 for(let i=0;i<90;i++){runs=await get('/api/schedules/runs?task_id='+task.id);if(runs.runs[0]&&runs.runs[0].status!=='running')break;await new Promise(r=>setTimeout(r,1000));}
 const run=runs.runs[0];check('真实立即运行成功且记录可查',run&&run.status==='succeeded');
 fs.writeFileSync(path.join(out,'schedule-run.json'),JSON.stringify({task_id:task.id,run_id:run.id,status:run.status,started_at:run.started_at,finished_at:run.finished_at,output:run.output},null,2));
 await page.locator('[data-action=history][data-id="'+task.id+'"]').evaluate(b=>b.click());await page.locator('#runs').waitFor();
 await page.screenshot({path:path.join(out,'real-schedule-run.png'),fullPage:true});
 check('无页面脚本错误',errors.length===0);
 fs.writeFileSync(path.join(out,'real-today.json'),JSON.stringify(listing,null,2));
 }finally{fs.writeFileSync(path.join(out,'real-e2e-results.json'),JSON.stringify({checks,errors},null,2));if(context)await context.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
