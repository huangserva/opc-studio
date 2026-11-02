const fs=require('fs'),path=require('path'),os=require('os'),assert=require('assert/strict');
const {chromium}=require(path.join(os.homedir(),'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright'));
const out=process.argv[2],base='http://127.0.0.1:'+fs.readFileSync(path.join(out,'port.txt'),'utf8').trim(),checks=[],errors=[],consoleErrors=[],shots=[],responses=[],networkFailures=[];let browser;
function check(name,value){assert(value,name);checks.push(name);console.log('通过 '+name)}
async function ready(p){await p.waitForFunction(()=>document.querySelector('#item-list').getAttribute('aria-busy')==='false'&&!document.querySelector('#item-list').inert);assert.equal(await p.locator('#item-list>.error').count(),0)}
(async()=>{browser=await chromium.launch({headless:true,channel:'chrome'});const ctx=await browser.newContext({viewport:{width:1280,height:1000}});await ctx.route('**/*',r=>{const req=r.request();if(!['GET','HEAD'].includes(req.method()))throw Error('禁止写请求 '+req.url());if(new URL(req.url()).origin===base)return r.continue();if(req.resourceType()==='image')return r.fulfill({contentType:'image/svg+xml',body:'<svg xmlns="http://www.w3.org/2000/svg"/>'});return r.abort()});
const p=await ctx.newPage();p.on('requestfailed',r=>networkFailures.push({url:r.url(),error:r.failure()?.errorText,type:r.resourceType()}));p.on('pageerror',e=>errors.push(e.message));p.on('console',m=>{if(m.type()==='error')consoleErrors.push(m.text())});p.on('response',async r=>{if(r.url().includes('/api/today?')&&!r.url().includes('source_catchup')){try{responses.push({url:r.url(),data:await r.json()})}catch{}}});
for(const c of ['hot','interest','official'])for(const range of ['today','48h','7d']){
 await p.goto(base+'/today.html?channel='+c+'&range='+range);await ready(p);check(c+'/'+range+' 无下拉框',await p.locator('select').count()===0);
 const cards=p.locator('#item-list .item-card');if(await cards.count()){
  check(c+'/'+range+' 数据行不印空值',!(await cards.first().innerText()).match(/—|未知/));
  const withViews=await cards.locator('[data-metric=views]').count();check(c+'/'+range+' 浏览量按已有字段显示',withViews>0||!responses.at(-1)?.data.items.some(i=>i.metrics?.views!=null));
  await cards.first().locator('.item-summary').click();await p.locator('#reader .item-detail').waitFor();
  check(c+'/'+range+' 标题在作者与数据带前',await p.locator('#reader .detail-title').count()===1&&await p.locator('#reader .item-detail').evaluate(n=>n.firstElementChild.classList.contains('detail-title')));
  check(c+'/'+range+' 顶部数据带且仅一次',await p.locator('#reader .detail-metrics').count()<=1&&await p.locator('#reader .item-detail').evaluate(n=>{const strip=n.querySelector('.detail-metrics'),body=n.querySelector('.full-text');return !strip||!!(strip.compareDocumentPosition(body)&Node.DOCUMENT_POSITION_FOLLOWING)}));
  if(await p.locator('.reader-back').isVisible())await p.locator('.reader-back').click();
 }
 await p.screenshot({path:path.join(out,c+'-'+range+'-1280.png')});shots.push(c+'-'+range+'-1280.png');
}
await p.goto(base+'/today.html?channel=interest&range=7d');await ready(p);const initial=(await ctx.request.get(base+'/api/today?channel=interest&range=7d&view=summary&audit=0')).json();const before=(await initial).total;
await p.locator('#hot-only').check();await ready(p);check('兴趣爆款开关写URL并改变结果',new URL(p.url()).searchParams.get('tier')==='爆款'&&responses.at(-1).data.total<before);
await p.locator('#hot-only').uncheck();await ready(p);
for(const sort of ['heat','time','relevance']){
 if(sort==='relevance')await p.locator('#more-filters').evaluate(n=>n.open=true);
 await p.locator('[data-sort="'+sort+'"]').click();await ready(p);check(sort+' 写入URL',new URL(p.url()).searchParams.get('sort')===sort);
 const data=(await ctx.request.get(base+'/api/today?'+new URL(p.url()).searchParams+'&view=summary&audit=0')).json();const rows=(await data).items;
 const value=i=>sort==='heat'?(i.metrics?.heat||0):sort==='time'?(Date.parse(i.published_at)||0):(i.relevance||0);check(sort+' 顺序正确',rows.every((r,i)=>!i||value(rows[i-1])>=value(r)));
 await p.reload();await ready(p);check(sort+' 刷新保持',await p.locator('#sort').inputValue()===sort);
}
await p.locator('[data-sort=""]').click();await ready(p);
const topic=p.locator('#topic-buttons [data-topic]:not([data-topic=""])').first();if(await topic.count()){
 const key=await topic.getAttribute('data-topic');await topic.click();await ready(p);check('方向点选URL生效',new URL(p.url()).searchParams.get('topic')===key);await p.locator('[data-topic="'+key+'"]').first().click();await ready(p);check('方向再次点击取消',!new URL(p.url()).searchParams.get('topic'));
}
await p.locator('#filter-collapse').click();check('筛选收起只留摘要',!await p.locator('#filters').isVisible()&&await p.locator('#filter-summary').isVisible());await p.reload();await ready(p);check('收起状态刷新保持',!await p.locator('#filters').isVisible());await p.locator('#filter-summary').click();check('点摘要展开',await p.locator('#filters').isVisible());await p.reload();await ready(p);check('展开状态刷新保持',await p.locator('#filters').isVisible());
await p.locator('#q').fill('Agent');await p.waitForURL(u=>u.searchParams.get('q')==='Agent');await ready(p);check('停输400ms自动搜索',true);await p.locator('#q').fill('Codex');await p.locator('#q').press('Enter');await p.waitForURL(u=>u.searchParams.get('q')==='Codex');await ready(p);check('回车搜索',true);
await p.goto(base+'/today.html?range=7d');await ready(p);
await p.locator('#more-filters').evaluate(n=>n.open=true);
const collector=p.locator('[data-collection-channel]:not([data-collection-channel=""])').first(),collectorKey=await collector.getAttribute('data-collection-channel');await collector.click();await ready(p);check('采集渠道标签保持原过滤能力',new URL(p.url()).searchParams.get('collection_channel')===collectorKey);await p.locator('[data-collection-channel="'+collectorKey+'"]').click();await ready(p);
await p.locator('[data-tier="其他"]').click();await ready(p);check('普通来源标签可用',new URL(p.url()).searchParams.get('tier')==='其他');await p.locator('[data-tier="其他"]').click();await ready(p);
await p.locator('[data-source="G"]').click();await ready(p);check('旧来源兼容过滤仍可用',new URL(p.url()).searchParams.get('source')==='G');await p.locator('[data-source="G"]').click();await ready(p);await p.locator('#more-filters').evaluate(n=>n.open=false);
for(const width of [390,768,1280]){await p.setViewportSize({width,height:1000});await ready(p);const state=await p.evaluate(()=>{const list=document.querySelector('#item-list');return {body:document.documentElement.scrollWidth<=innerWidth+1,listScroll:list.scrollHeight>list.clientHeight,height:list.clientHeight}});check(width+' 不横向溢出且列表仍可滚',state.body&&state.listScroll&&state.height>100);await p.locator('#item-list').evaluate(n=>n.scrollTop=n.scrollHeight);check(width+' 加行后仍能滚到底',await p.locator('#item-list').evaluate(n=>n.scrollTop>0));await p.locator('#item-list').evaluate(n=>n.scrollTop=0);await p.screenshot({path:path.join(out,'responsive-'+width+'.png')});shots.push('responsive-'+width+'.png');}
const formatted=await p.evaluate(async()=>{const m=await import('/components/item-card.js');const i={id:'fixture',title:'指标边界',kind:'post',author:{name:'作者',followers:25000},metrics:{views:13000,likes:0,bookmarks:233,retweets:31,replies:13,quotes:2,views_per_hour:2100},published_at:new Date().toISOString(),tier:'爆款',topic:'测试',fit_score:3,doable:2};const card=m.renderPostCard(i,{actions:false}),detail=m.renderPostDetail(i);return {valid:[0,999,1000,10000,13000,25000].map(m.formatMetric),absent:[null,undefined,'',false,NaN,Infinity,-1].map(m.formatMetric),card:card.innerText||card.textContent,detail:detail.textContent,strips:detail.querySelectorAll('.detail-metrics').length};});
check('统一formatter缩写及0值',JSON.stringify(formatted.valid)===JSON.stringify(['0','999','1.0千','1.0万','1.3万','2.5万']));check('缺值非法值整段省略',formatted.absent.every(v=>v===''));check('浏览赞收藏转发回复增速同一行',/浏览 1.3万.*赞 0.*收藏 233.*转发 31.*回复 13.*每小时 2.1千/.test(formatted.card));check('详情保留引用且指标不重复',formatted.detail.includes('引用 2')&&formatted.strips===1);
check('已过滤位于列表底部',await p.locator('#item-list').evaluate(n=>n.lastElementChild.id==='filtered-panel'));
check('控制台0错误',errors.length===0&&consoleErrors.length===0);fs.writeFileSync(path.join(out,'browser-results.json'),JSON.stringify({checks,errors,consoleErrors,networkFailures,shots},null,2));console.log('共 '+checks.length+' 项通过');await ctx.close();
})().catch(e=>{console.error(e);fs.writeFileSync(path.join(out,'browser-failure.json'),JSON.stringify({error:e.stack,checks,errors,consoleErrors,networkFailures},null,2));process.exitCode=1}).finally(async()=>browser?.close());
