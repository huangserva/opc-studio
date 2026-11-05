'use strict';
// 仅显示层：随机端口、独立 /tmp profile；API 夹具不接触正式服务或采集器。
const fs=require('fs'),path=require('path'),http=require('http');
const {chromium}=require('playwright');
const ROOT=path.resolve(__dirname,'../..'),PUBLIC=process.env.SOURCE_RADAR_PUBLIC_DIR||path.join(ROOT,'public');
const evidence=fs.mkdtempSync('/tmp/source-radar-display-e2e-');
const results=[],errors=[],requests=[];
const check=(name,ok,detail)=>{results.push({name,passed:!!ok,...(detail===undefined?{}:{detail})});};
const note='截断情况未知；轮次保留原状态，等待后续覆盖核实。'+ '长状态说明'.repeat(8)+'；末尾仍需完整可见';
const item={id:'fixture:1',kind:'post',title:'首排真实条目',full_text:'完整测试正文',sources:['G'],author:{name:'测试作者',handle:'fixture'},metrics:{views:1234},tier:'爆款',hot:true,topic:'Agent',status:'new',rich_sources:[]};
let sourceError=null,radarNote=note;
function snapshot(){return {items:[item],total:1,page:1,topic_options:[{key:'Agent',display_name:'Agent',count:1}],filtering:{by_source:{},items:[],total:0},registries:{platforms:{},channels:{}},sources:{G:{updated_at:'2026-11-05T16:00:00+08:00',error:null},A:{updated_at:'2026-11-02T16:00:40+08:00',status:'disabled',error:sourceError,note:'AIsa 推特雷达已停用，保留历史数据'},'grok:radar':{updated_at:'2026-11-03T14:26:33Z',status:'ok',error:null,note:radarNote},V:{updated_at:'2026-11-04T21:21:16+08:00',error:null},L:{updated_at:'2026-11-05T09:32:00Z',error:'RSS 测试异常'}},source_catchup:{status:'waiting',sources:[]},clue_morning:{status:'ok',date:'2026-11-05',clue_count:3,content:'晨报全文\n第二行内容'},today_todo:{status:'pending',items:[],reason:'测试中'}};}
async function geometry(page){return page.evaluate(()=>{
 const rect=selector=>{const r=document.querySelector(selector).getBoundingClientRect();return {top:r.top,bottom:r.bottom,left:r.left,right:r.right};};
 const row=rect('.today-source-row'),filter=rect('.filter-top'),expanded=rect('.source-expanded'),card=rect('#item-list .item-card');
 const badges=[...document.querySelectorAll('#sources>.status-block,.source-overview[open] .status-block')].map(n=>{
  const r=n.getBoundingClientRect(),range=document.createRange();range.selectNodeContents(n);
  return {text:n.textContent,width:n.clientWidth,scrollWidth:n.scrollWidth,after:getComputedStyle(n,'::after').content,textFits:[...range.getClientRects()].every(t=>t.left>=r.left-1&&t.right<=r.right+1&&t.top>=r.top-1&&t.bottom<=r.bottom+1)};
 });return {row,filter,expanded,card,badges,overflow:document.documentElement.scrollWidth>innerWidth};
 });}
(async()=>{
 const server=http.createServer((req,res)=>{
  requests.push({method:req.method,path:new URL(req.url,'http://local').pathname});
  if(req.method!=='GET'){res.writeHead(405);return res.end();}
  const url=new URL(req.url,'http://local');
  if(url.pathname==='/api/today'){res.setHeader('Content-Type','application/json');return res.end(JSON.stringify(snapshot()));}
  if(url.pathname.startsWith('/api/')){res.setHeader('Content-Type','application/json');return res.end('{}');}
  const file=path.resolve(PUBLIC,'.'+url.pathname);if(!file.startsWith(PUBLIC+path.sep)){res.writeHead(403);return res.end();}
  try{res.setHeader('Content-Type',file.endsWith('.js')?'text/javascript':file.endsWith('.css')?'text/css':file.endsWith('.svg')?'image/svg+xml':'text/html');res.end(fs.readFileSync(file));}catch{res.writeHead(404);res.end();}
 });
 await new Promise(r=>server.listen(0,'127.0.0.1',r));const base='http://127.0.0.1:'+server.address().port;
 let context;
 try{
  context=await chromium.launchPersistentContext(path.join(evidence,'profile'),{headless:true,channel:'chrome',args:['--disable-background-networking','--disable-component-update']});
  await context.route('**/*',route=>new URL(route.request().url()).origin===base?route.continue():route.abort());
  for(const width of [1440,390]){
   const page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));await page.setViewportSize({width,height:1000});
   await page.goto(base+'/today.html');await page.locator('#item-list .item-card').waitFor();
   if(width===390)await page.locator('#today-todo-close').click();
   const texts=await page.locator('#sources>.status-block').allTextContents();
   check(width+' 推特取消单列',texts.some(t=>t.includes('AIsa · 推特')&&t.includes('已取消'))&&!texts.some(t=>/^AIsa /.test(t)&&t.includes('已停用')),texts);
   check(width+' 非推特状态未知',texts.some(t=>t.includes('抖音/小红书')&&t.includes('启用状态未知')));
   const compact=await geometry(page);check(width+' 雷达长note完整可见',compact.badges.some(b=>b.text.endsWith('末尾仍需完整可见')&&b.textFits&&b.scrollWidth<=b.width+1));
   check(width+' note无矛盾正常后缀',compact.badges.filter(b=>b.text.includes('截断情况未知')||b.text.includes('已取消')||b.text.includes('启用状态未知')).every(b=>['none','""'].includes(b.after)));
   check(width+' 收起时不覆盖首排',compact.row.bottom<=compact.filter.top+1&&!compact.overflow,compact);
   await page.screenshot({path:path.join(evidence,'collapsed-'+width+'.png')});
   await page.locator('.source-summary').click();
   const expanded=await geometry(page);check(width+' 展开不覆盖筛选或条目',expanded.expanded.bottom<=expanded.row.bottom+1&&expanded.row.bottom<=expanded.filter.top+1&&expanded.filter.bottom<=expanded.card.top+1&&!expanded.overflow,expanded);
   check(width+' 展开所有回执无裁切',expanded.badges.every(b=>b.textFits&&b.scrollWidth<=b.width+1),expanded.badges);
   check(width+' 视频榜状态独立',await page.locator('.source-expanded').innerText().then(t=>t.includes('AIsa · 视频榜 21:21')&&!t.includes('AIsa · 视频榜 21:21 · 已取消')));
   await page.screenshot({path:path.join(evidence,'expanded-'+width+'.png')});
   await page.locator('.source-summary').focus();await page.keyboard.press('Space');check(width+' 键盘可收起',!await page.locator('.source-overview').evaluate(n=>n.open));
   await page.locator('#sources>button').filter({hasText:'RSS'}).click();check(width+' 异常按钮仍打开详情',await page.locator('#source-error').isVisible()&&await page.locator('#source-error').innerText().then(t=>t.includes('RSS 测试异常')));await page.locator('#source-error button').click();
   await page.locator('#clue-morning-summary').click();const morning=await page.evaluate(()=>({bottom:document.querySelector('#clue-morning-content').getBoundingClientRect().bottom,filterTop:document.querySelector('.filter-top').getBoundingClientRect().top}));check(width+' 晨报展开不覆盖首排',morning.bottom<=morning.filterTop+1,morning);await page.locator('#clue-morning-summary').click();
   sourceError='历史快照测试错误';radarNote='截断情况未知 <img src=x onerror=alert(1)> & 原样文本';
   await page.locator('#today-refresh').click();await page.locator('#sources>button').filter({hasText:'AIsa'}).waitFor();
   check(width+' 取消与历史快照错误并存',await page.locator('#sources>button').filter({hasText:'AIsa'}).innerText().then(t=>t.includes('已取消')&&t.includes('历史快照异常')));
   check(width+' note作为纯文本',await page.locator('#sources img,#sources script').count()===0&&await page.locator('#sources').innerText().then(t=>t.includes('<img src=x onerror=alert(1)>')));
   sourceError=null;radarNote=note;await page.close();
  }
  check('pageerror=0',errors.length===0,errors);check('业务写请求=0',requests.every(r=>r.method==='GET'));
 }finally{if(context)await context.close();await new Promise(r=>server.close(r));fs.writeFileSync(path.join(evidence,'results.json'),JSON.stringify({base,results,errors,requests},null,2));console.log(JSON.stringify({evidence,passed:results.filter(r=>r.passed).length,failed:results.filter(r=>!r.passed)},null,2));}
 if(results.some(r=>!r.passed))process.exitCode=1;
})().catch(e=>{console.error(e);process.exitCode=1});
