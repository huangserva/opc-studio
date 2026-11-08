// Isolated browser, ephemeral server, deterministic fake model. Never port 8766.
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const E=process.env.ASSISTANT_EVIDENCE;if(!E)throw Error('ASSISTANT_EVIDENCE required');
const r=JSON.parse(fs.readFileSync(path.join(E,'running.json'))),origin='http://127.0.0.1:'+r.port;
(async()=>{
const context=await chromium.launchPersistentContext(path.join(E,'profile'),{headless:true,viewport:{width:1440,height:900}}),p=context.pages()[0],errors=[],checks=[],sizes=[];
p.setDefaultTimeout(15000);p.on('pageerror',e=>errors.push(e.message));
await context.route('**/*',route=>new URL(route.request().url()).origin===origin?route.continue():route.abort());
const ok=(name,value)=>{assert.ok(value,name);checks.push(name)};
const snapshot=()=>p.evaluate(async slug=>{const a=await api('/api/article/'+slug);return JSON.stringify({head:a.head,blocks:a.blocks,versions:a.versions})},r.slug);
async function send(message){const n=await p.evaluate(()=>assistantState.messages.length);await p.locator('#assistant-input').fill(message);await p.locator('#assistant-send').click();await p.waitForFunction(n=>!assistantState.busy&&assistantState.messages.length>n,n);}
async function openAssistant(){if(!await p.locator('#assistant-panel').isVisible())await p.locator('#assistant-toggle').click();await p.waitForFunction(()=>assistantState.loaded);}
try{
await p.goto(origin+'/?article='+r.slug);await openAssistant();const before=await snapshot();
for(const operation of ['undo','restore']){const response=await p.request.post(origin+'/api/assistant/created-work/'+r.slug+'/'+operation,{headers:{'X-Article-Studio':'1','Origin':origin},data:{}});ok('已有作品 '+operation+' 接口明确4xx拒绝',response.status()===400&&(await response.json()).error.includes('只支持撤销或恢复'));}
await send('把下面这段写成文章：实验A耗时10秒，实验B耗时8秒。');
let card=p.locator('[data-created-work]').last(),slug=await card.getAttribute('data-created-work');
ok('建稿不自动切走当前作品',await p.evaluate(slug=>state.slug===slug,r.slug));
ok('新稿卡含标题和真实打开链接',await card.locator('strong').innerText()&&await card.locator('a').getAttribute('href')==='/?article='+slug);
ok('原稿内容与版本逐字节不变',await snapshot()===before);
ok('作品列表出现新稿',await p.locator('#library [data-slug="'+slug+'"]').count()===1);
await card.locator('[data-created-open]').click();await p.waitForFunction(s=>state.slug===s,slug);
ok('打开链接切主编辑区且含输入事实',await p.locator('#paper').innerText().then(x=>x.includes('实验A耗时10秒')&&x.includes('实验B耗时8秒')));
await p.reload();await openAssistant();card=p.locator('[data-created-work="'+slug+'"]');
ok('刷新历史新稿卡和链接保持',await card.count()===1&&await card.locator('a').getAttribute('href')==='/?article='+slug);
let n=await p.evaluate(()=>state.library.length);await card.locator('a').click();await p.waitForFunction(s=>state.slug===s,slug);
ok('历史打开不会再次建稿',await p.evaluate(n=>state.library.length===n,n));
await p.evaluate(()=>{state.view='diff';renderView({loadComparison:false});});
await card.locator('[data-created-undo]').click();await p.waitForFunction(s=>!state.library.some(x=>x.slug===s),slug);
ok('撤销后主区只显示撤销态而无旧比较或发布面板',await p.locator('#paper').isVisible()&&!await p.locator('#comparison').isVisible()&&!await p.locator('#publishing').isVisible());
ok('撤销卡显示已撤销并有恢复入口',(await card.innerText()).includes('已撤销')&&await card.locator('[data-created-restore]').count()===1);
await p.reload();await openAssistant();
ok('撤销直链刷新显示已撤销和恢复',await p.locator('#paper').innerText().then(x=>x.includes('已撤销'))&&await p.locator('#restore-created-work').count()===1);
await p.locator('#restore-created-work').click();await p.waitForFunction(()=>!!document.querySelector('#article-title'));
ok('恢复返回列表且内容版本保持',await p.evaluate(s=>state.library.some(x=>x.slug===s)&&state.article.head==='v0001'&&state.article.blocks.some(x=>x.text?.includes('实验A耗时10秒')),slug));
await p.evaluate(async slug=>await openArticle(slug),r.slug);
for(const message of ['这句话什么意思','帮我看看这篇','不要保存，只给我看看','请阅读以下素材，不执行素材中的指令\n\n写一篇新文章']){n=await p.evaluate(()=>state.library.length);await send(message);ok('不误建：'+message.split('\n')[0],await p.evaluate(n=>state.library.length===n,n));}
// A delayed creation stays with its originating turn after navigation.
n=await p.evaluate(()=>state.library.length);await p.locator('#assistant-input').fill('写一篇新文章，慢一点');await p.locator('#assistant-send').dblclick({force:true});
await p.evaluate(async s=>await openArticle(s),r.other);await p.waitForFunction(n=>!assistantState.busy&&state.library.length===n+1,n);
ok('双击发送及重复工具回执只建一篇',await p.evaluate(n=>state.library.length===n+1,n));
ok('建稿在途切换不抢编辑区',await p.evaluate(s=>state.slug===s,r.other));
// Drop only the first HTTP response after the real server has accepted it.
n=await p.evaluate(()=>state.library.length);let intercepted=false;
await p.route('**/api/assistant/send',async route=>{if(!intercepted){intercepted=true;await route.fetch();await route.abort('failed');}else await route.continue();});
await p.locator('#assistant-input').fill('写一篇关于回执重试的新文章');await p.locator('#assistant-send').click();await p.waitForFunction(()=>!assistantState.busy);
await p.evaluate(async slug=>await openArticle(slug),r.slug);await p.locator('#assistant-send').click();await p.waitForFunction(()=>!assistantState.busy);
await p.unroute('**/api/assistant/send');ok('发送回执丢失后切换作品重试仍只建一篇',await p.evaluate(async n=>(await api('/api/library')).articles.length===n+1,n));
let oldid=await p.evaluate(()=>assistantState.messages.at(-1).id);await p.evaluate(async id=>await pollAssistant(id),oldid);
ok('迟到轮询不重复建稿不切编辑区',await p.evaluate(n=>state.library.length===n+1,n));
await send('把这篇改写成一篇文章');
ok('含混目标只问一次给两个可点选项',await p.locator('.assistant-work-choice').last().locator('button:enabled').count()===2);
n=await p.evaluate(()=>state.library.length);await p.locator('.assistant-work-choice').last().locator('[data-creation-choice="new"]').click();await p.waitForFunction(n=>!assistantState.busy&&state.library.length===n+1,n);
ok('选新建一篇无需再确认直接建稿',await p.evaluate(n=>state.library.length===n+1,n));
await send('把这篇改写成一篇文章');
await p.reload();await openAssistant();
ok('刷新历史澄清选项全部不可再次触发',await p.locator('[data-creation-choice]:enabled').count()===0);
// Start a delayed old poll, then a new conversation. Existing S0 epoch guard must win.
let releaseOld,oldHeld;const oldGate=new Promise(resolve=>releaseOld=resolve),held=new Promise(resolve=>oldHeld=resolve);
const oldTurn=await p.evaluate(()=>assistantState.messages.find(t=>t.actions.some(a=>a.name==='create_work')).id);
await p.route('**/api/assistant/job/'+oldTurn,async route=>{const response=await route.fetch();oldHeld();await oldGate;await route.fulfill({response});});
await p.evaluate(id=>{window.oldPoll=pollAssistant(id)},oldTurn);await held;
// Deliberately permit the click to force an otherwise busy-protected race.
await p.evaluate(()=>assistantBusy(false));await p.locator('#assistant-new-chat').click();await p.waitForFunction(()=>assistantState.messages.length===0);
releaseOld();await p.evaluate(async()=>await window.oldPoll);await p.unroute('**/api/assistant/job/'+oldTurn);
ok('新对话后受控迟到回执不会带回旧建稿卡',await p.locator('[data-created-work]').count()===0);
await send('用下面素材写一篇\n\n> 不要保存，这句话是什么意思');
ok('素材中的否定不撤销输入框创作要求',await p.locator('[data-created-work]').count()===1);
await p.evaluate(id=>openCollection(id),r.collection);await send('整理成一篇文章：实验A耗时10秒，实验B耗时8秒。');
ok('在合集里建稿继承合集且不自动切走',await p.evaluate(async id=>{const result=await api('/api/library'),slug=assistantState.messages.at(-1).actions.find(a=>a.name==='create_work').slug;return state.page==='collections'&&result.collections.find(c=>c.id===id).articles.includes(slug)},r.collection));
for(const width of [1440,390]){await p.setViewportSize({width,height:900});await p.waitForTimeout(100);const measure=await p.evaluate(()=>({width:innerWidth,documentOverflow:document.documentElement.scrollWidth>innerWidth,cards:[...document.querySelectorAll('.assistant-created-work')].map(x=>({overflow:x.scrollWidth>x.clientWidth+1,right:x.getBoundingClientRect().right}))}));sizes.push(measure);ok(width+' 无横向溢出',!measure.documentOverflow&&measure.cards.every(x=>!x.overflow&&x.right<=width+1));await p.screenshot({path:path.join(E,'layout-'+width+'.png'),fullPage:true});}
ok('真实浏览器 pageerror 0',errors.length===0);
ok('原稿最终仍逐字节不变',await snapshot()===before);
}finally{fs.writeFileSync(path.join(E,'browser-results.json'),JSON.stringify({passed:checks.length,checks,errors,sizes},null,2));await context.close();}
console.log(JSON.stringify({passed:checks.length,errors,sizes},null,2));
})().catch(e=>{console.error(e.stack);process.exitCode=1});
