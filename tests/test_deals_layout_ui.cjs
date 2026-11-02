'use strict';
// Run: DEALS_EVIDENCE=/tmp/deals-fix/<UTC directory with before/> node tests/test_deals_layout_ui.cjs
// A private browser context and a random local static server; all API calls use synthetic fixtures.
const fs=require('node:fs'),path=require('node:path'),http=require('node:http'),assert=require('node:assert/strict');
const {chromium}=(()=>{try{return require('playwright')}catch{return require(path.join(require('node:os').homedir(),'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright'))}})();
const root=path.resolve(__dirname,'..'),out=process.env.DEALS_EVIDENCE||fs.mkdtempSync('/tmp/deals-layout-');
fs.mkdirSync(out,{recursive:true});
const before=path.join(out,'before'),results=[],observations=[];
const mark=(name,detail)=>{results.push({name,passed:true,detail});console.log('通过 '+name)};
const longTitle='超长商单标题'.repeat(20),longDetail='核对交付进展说明'.repeat(50),id='a'.repeat(32);
const deal={id,revision:1,title:'合成商单',customer:'合成客户',stage:'writing',quote:null,currency:null,quote_confirmed:false,needs_review:false,settled:false,archived:false,original_group:'未付',original_text:'',receipts:[],deliverables:[],publications:[],sources:[],audit:[],cash:{status:'unpaid',outstanding:null,totals:{CNY:'0.00'}}};
const deals=Array.from({length:20},(_,i)=>({...deal,id:i.toString(16).padStart(32,'0'),title:'合成商单 '+i}));
const options={stages:{writing:'待写',unknown:'进度待核对'},stage_groups:{'创作':['writing'],'其他':['unknown']},delivery_stages:{},works:[],bindings:[]};
const summary={signed:0,closed:0,published:0,received:{CNY:'0'},outstanding:{CNY:'0'},needs_review:0,unknown_dates:0,unknown_quote:20};
const review={run_id:'fixture',generated_at:'2026-11-01T00:00:00Z',findings:{overdue:Array.from({length:11},(_,i)=>({deal_id:i%2?id:'',title:longTitle,detail:longDetail})),needs_review:[{detail:longTitle}]},conflicts:Array.from({length:12},(_,i)=>({clue_title:longTitle,deal_id:i%2?id:'',deal_title:longTitle,reason:longDetail})),proposals:Array.from({length:12},(_,i)=>({id:'proposal'+i,title:longTitle,operation:'update',changes:{stage:'writing'},reason:longDetail})),reports:Array.from({length:12},(_,i)=>({week:'2026-W'+(i+1),generated_at:'2026-11-01T00:00:00Z'}))};
const tasks=Array.from({length:20},(_,i)=>({id:String(i),revision:1,name:'合成定时任务 '+i,body:'只读布局验证',kind:'deals_review',assistant_id:'isolated',tool_groups:[],profile_id:'fake',cwd:'/tmp',frequency:'interval',minutes:240,weekdays:[0],at:'09:00',timeout:900,enabled:false,allow_commands:false,next_run:null,result_destination:'runs',executor_name:'商单分析（Pi）'}));
const gh={connection:{},tasks:[],retry_documents:0,sources:Array.from({length:20},(_,i)=>({id:String(i),repository:'fixture/repo-'+i,watch:['issues'],count:1,enabled:false,capture:{}}))};
let server,browser;
async function pageFor(size,variant='after',empty=false){
 const ctx=await browser.newContext({viewport:size,hasTouch:size.width===390});
 const p=await ctx.newPage();
 await p.route('**/*',async r=>{
  const u=new URL(r.request().url());
  if(u.origin!==base)return r.abort();
  if(u.pathname.startsWith('/api/')){
   assert.equal(r.request().method(),'GET','布局测试不得写入');
   let data={};
   if(u.pathname==='/api/deals/options')data=options;
   else if(u.pathname==='/api/deals/review')data=empty?{run_id:'empty',findings:{}}:review;
   else if(u.pathname==='/api/deals/review/report')data={week:'2026-W1',markdown:'合成周报'};
   else if(u.pathname==='/api/deals')data={deals,summary,imports:[]};
   else if(u.pathname.startsWith('/api/deals/'))data=deal;
   else if(u.pathname==='/api/github')data=gh;
   else if(u.pathname==='/api/assistant/settings')data={profiles:[]};
   else if(u.pathname==='/api/schedules')data={tasks,models:[],external:{tasks:[],checked_at:'2026-11-01T00:00:00Z'},scheduler_health:{running:true},concurrency:{max:2,active:0,available:2}};
   else if(u.pathname==='/api/schedules/runs')data={runs:[],total:0,page:1,page_size:50,has_more:false};
   else if(u.pathname==='/api/clues')data={items:[]};
   return r.fulfill({json:data});
  }
  const name=path.basename(u.pathname),snapshot=variant==='before';
  let file=path.join(snapshot?before:path.join(root,'public'),decodeURIComponent(u.pathname));
  if(!fs.existsSync(file))return r.fulfill({status:404,body:''});
  let content=fs.readFileSync(file);
  if(variant==='no-scroll'&&name==='panel-layout.css')content=fs.readFileSync(path.join(before,name));
  if(variant==='old-rows'&&name==='deals.js'){
   const old=fs.readFileSync(path.join(before,name),'utf8');let current=content.toString();
   content=current.slice(0,current.indexOf('function renderReview()'))+old.slice(old.indexOf('function renderReview()'),old.indexOf('async function loadReview()'))+current.slice(current.indexOf('async function loadReview()'));
  }
  if(variant==='old-hover'&&name==='deals.css')content=content.toString()+'\n.dl-focus-tab:hover:not(.selected){border-color:#9daffe}';
  return r.fulfill({body:content,contentType:name.endsWith('.css')?'text/css':name.endsWith('.js')?'text/javascript':name.endsWith('.html')?'text/html':'application/octet-stream'});
 });
 return {p,ctx};
}
let base;
async function ready(p,name){await p.goto(base+'/'+name+'.html');await p.locator(name==='deals'?'.dl-deal-card':name==='github'?'.gh-source':'[data-overview-task]').first().waitFor();}
async function scroll(p,name){
 const canvas=p.locator('.schedule-canvas'),b=await canvas.boundingBox();
 const x=Math.min(b.x+b.width/2,await p.evaluate(()=>innerWidth-10)),y=Math.min(b.y+Math.min(b.height/2,200),await p.evaluate(()=>innerHeight-20));
 await p.mouse.move(x,y);
 await p.mouse.wheel(0,100000);await p.waitForTimeout(180);
 const metric=await p.evaluate(()=>{const e=document.querySelector('.schedule-canvas'),h=document.querySelector('.topbar').getBoundingClientRect();return {canvasTop:e.scrollTop,documentTop:document.scrollingElement.scrollTop,height:e.clientHeight,scrollHeight:e.scrollHeight,topbarTop:h.top,topbarBottom:h.bottom,viewport:innerHeight}});
 const last=p.locator(name==='deals'?'.dl-deal-card':name==='github'?'.gh-source':'[data-overview-task]').last();const box=await last.boundingBox();
 return {...metric,lastTop:box.y,lastBottom:box.y+box.height};
}
function checkScroll(m){assert(m.canvasTop>0,'滚动容器 scrollTop 未增加');assert(m.lastBottom<=m.viewport+1&&m.lastBottom>m.topbarBottom,'最后一张卡未进入可视区');assert(m.topbarTop>=0&&m.topbarBottom<m.viewport,'顶部栏离开可视区')}
async function geometry(p){
 const errors=await p.locator('.dl-analysis-grid li').evaluateAll(rows=>{
  const bad=[];for(const [i,row] of rows.entries()){
   const section=row.closest('section').getBoundingClientRect(),children=[...row.children].filter(e=>e.getBoundingClientRect().height>0);
   for(let j=0;j<children.length;j++){
    const a=children[j].getBoundingClientRect();if(a.left<section.left-1||a.right>section.right+1)bad.push('越出 section '+i);
    if(children[j].scrollWidth>children[j].clientWidth+1)bad.push('内容横向溢出 '+i);
    for(let k=j+1;k<children.length;k++){const b=children[k].getBoundingClientRect();if(Math.min(a.right,b.right)-Math.max(a.left,b.left)>1&&Math.min(a.bottom,b.bottom)-Math.max(a.top,b.top)>1)bad.push('元素相交 '+i);}
   }
   const title=row.querySelector('.dl-ar-title');if(title&&title.getBoundingClientRect().height>parseFloat(getComputedStyle(title).lineHeight)*2+1)bad.push('标题超过两行 '+i);
  }return bad;
 });assert.deepEqual(errors,[],'分析行几何检查失败');
}
async function scopeClick(p,k){await p.locator(`[data-scope="${k}"]`).click();await p.locator(`[data-scope="${k}"][aria-pressed="true"]`).waitFor();}
async function stageClick(p,k){await p.locator(`[data-stage="${k}"]`).click();await p.locator(`[data-stage="${k}"][aria-pressed="true"]`).waitFor();}
async function selected(p){
 const states=await p.locator('.dl-focus-tab,.dl-stage-card').evaluateAll(bs=>bs.map(b=>({selected:b.classList.contains('selected'),pressed:b.getAttribute('aria-pressed')})));
 assert.equal(states.filter(s=>s.selected).length,1,'筛选选中态必须唯一');assert(states.every(s=>s.pressed===String(s.selected)),'aria-pressed 与 selected 不一致');
}
async function hover(p){await scopeClick(p,'work');await p.locator('[data-scope="payment"]').hover();const colors=await p.locator('.dl-focus-tab').evaluateAll(bs=>bs.map(b=>getComputedStyle(b).borderTopColor));assert.notEqual(colors[0],colors[1],'悬停边框不得等于选中边框');await selected(p);}
(async()=>{
 server=http.createServer((q,s)=>{s.end()});await new Promise(r=>server.listen(0,'127.0.0.1',r));base='http://127.0.0.1:'+server.address().port;
 browser=await chromium.launch({headless:true,channel:'chrome'});
 const sizes=[{width:1440,height:900},{width:1024,height:768},{width:390,height:844}];
 for(const size of sizes){
  const label=size.width+'x'+size.height;
  for(const name of ['deals','github','schedules']){
   const metrics={};
   for(const variant of fs.existsSync(before)?['before','after']:['after']){
    const {p,ctx}=await pageFor(size,variant);try{await ready(p,name);await p.screenshot({path:path.join(out,`${name}-${label}-${variant}-top.png`)});metrics[variant]=await scroll(p,name);observations.push({name,label,variant,...metrics[variant]});await p.screenshot({path:path.join(out,`${name}-${label}-${variant}-bottom.png`)});
     if(variant==='after'&&name!=='schedules')checkScroll(metrics[variant]);
    }finally{await ctx.close()}
   }
   if(name==='schedules'&&metrics.before)assert.deepEqual(metrics.after,metrics.before,'定时任务滚动行为发生改变');
   mark(name+' 滚动 '+label,metrics);
  }
  const {p,ctx}=await pageFor(size);try{
   await ready(p,'deals');await geometry(p);
   assert.deepEqual(await p.locator('.dl-analysis-grid section ul').evaluateAll(us=>us.map(u=>u.children.length)),[5,5,5,5]);
   const cols=await p.locator('.dl-analysis-grid').evaluate(e=>getComputedStyle(e).gridTemplateColumns.split(' ').length),width=await p.locator('.dl-analysis').evaluate(e=>e.clientWidth);assert.equal(cols,width<720?1:2);
   for(const k of ['progress','conflicts','proposals','reports'])await p.locator(`[data-review-toggle="${k}"]`).click();
   assert.deepEqual(await p.locator('.dl-analysis-grid section ul').evaluateAll(us=>us.map(u=>u.children.length)),[12,12,12,12]);await geometry(p);
   for(const k of ['progress','conflicts','proposals','reports']){await p.locator(`#dl-review-${k} li`).first().scrollIntoViewIfNeeded();await p.screenshot({path:path.join(out,`analysis-${k}-${label}.png`)});}
   for(const k of ['progress','conflicts','proposals','reports'])await p.locator(`[data-review-toggle="${k}"]`).click();
   assert.deepEqual(await p.locator('.dl-analysis-grid section ul').evaluateAll(us=>us.map(u=>u.children.length)),[5,5,5,5]);
   assert.equal(await p.locator('[data-deal="undefined"],[data-deal=""]').count(),0);
   for(const k of ['work','payment','review','all']){await scopeClick(p,k);await selected(p);await p.locator(`[data-scope="${k==='work'?'all':'work'}"]`).hover();await selected(p)}
   await hover(p);await p.mouse.move(0,0);await selected(p);assert.equal(await p.locator('.dl-focus-tab:focus-visible').count(),0,'鼠标点击不应留下键盘焦点外框');await p.screenshot({path:path.join(out,`selection-${label}.png`)});
   await p.locator('.dl-stage-details>summary').click();await stageClick(p,'writing');await selected(p);
   await scopeClick(p,'all');await selected(p);
   await p.locator('#dl-analysis [data-deal]').first().click();await p.locator('#dl-detail[open]').waitFor();assert(await p.locator('#dl-detail').isVisible());await p.keyboard.press('Escape');
   await p.locator('[data-report]').first().click();await p.locator('#dl-report-dialog[open]').waitFor();await p.keyboard.press('Escape');
   mark('分析/展开收起/选中/弹窗 '+label);
  }finally{await ctx.close()}
  const empty=await pageFor(size,'after',true);try{await ready(empty.p,'deals');assert.equal(await empty.p.locator('.dl-analysis-grid section').count(),4);await geometry(empty.p);mark('空栏 '+label)}finally{await empty.ctx.close()}
 }
 if(fs.existsSync(before)){
  const {p,ctx}=await pageFor(sizes[0],'before');try{await ready(p,'deals');await p.locator('.dl-stage-details>summary').click();await stageClick(p,'writing');const count=await p.locator('.dl-focus-tab.selected,.dl-stage-card.selected').count();assert.equal(count,2);await p.screenshot({path:path.join(out,'before-stage-double-selected.png')});mark('改前阶段筛选双选中复现',{count})}finally{await ctx.close()}
 }
 if(fs.existsSync(before))for(const variant of ['no-scroll','old-rows','old-hover']){
  const {p,ctx}=await pageFor(sizes[2],variant);try{
   await ready(p,'deals');let caught;
   try{if(variant==='no-scroll')checkScroll(await scroll(p,'deals'));else if(variant==='old-rows')await geometry(p);else await hover(p)}catch(e){caught=e.message}
   assert(caught,'变异未被检出 '+variant);fs.writeFileSync(path.join(out,variant+'-failure.txt'),caught);mark('变异检出 '+variant,caught);console.log('预期失败 '+variant+': '+caught.slice(0,240));
  }finally{await ctx.close()}
 }
 fs.writeFileSync(path.join(out,'results.json'),JSON.stringify({results,observations},null,2));console.log(`共 ${results.length} 项通过，证据 ${out}`);
})().catch(e=>{console.error(e);process.exitCode=1}).finally(async()=>{await browser?.close();server?.close()});
