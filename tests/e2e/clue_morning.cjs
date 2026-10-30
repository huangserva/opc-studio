'use strict';
// H3b：独立 headless profile；默认合成 API 响应，--real 才点击真实任务立即运行。
const fs=require('fs'),path=require('path'),os=require('os'),assert=require('assert/strict');
const {chromium}=(()=>{try{return require('playwright')}catch(_){return require(path.join(os.homedir(),'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright'))}})();
const REAL=process.argv.includes('--real'),VIEW=process.argv.includes('--view-only'),ROOT=path.resolve(__dirname,'../..'),base='http://127.0.0.1:8766';
const out=path.join(ROOT,'coordination/evidence/impl-hub-h3b'),tmp=fs.mkdtempSync('/tmp/h3b-browser-');
fs.mkdirSync(out,{recursive:true});const checks=[],errors=[];let context;
const check=(name,value)=>{assert.ok(value,name);checks.push({name,passed:true});console.log('通过 '+name)};
const pause=ms=>new Promise(r=>setTimeout(r,ms));
(async()=>{try{
 context=await chromium.launchPersistentContext(path.join(tmp,'profile'),{headless:true,channel:'chrome',viewport:{width:1440,height:1000},args:['--disable-background-networking','--disable-component-update']});
 await context.route('**/*',r=>new URL(r.request().url()).origin===base?r.continue():r.abort());
 const get=async url=>{const r=await context.request.get(base+url);assert.equal(r.status(),200);return r.json()};
 const page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));
 let task,run,report;
 if(REAL){
  task=(await get('/api/schedules')).tasks.find(t=>t.name==='微信线索晨报');
  check('真实任务为Pi且每天08:30启用',task&&task.kind==='pi'&&task.at==='08:30'&&task.enabled&&task.weekdays.length===7);
  if(!VIEW){
  await page.goto(base+'/schedules.html');
  const button=page.locator('[data-action=run][data-id="'+task.id+'"]');await button.waitFor();
  const response=page.waitForResponse(r=>r.url()===base+'/api/schedules/'+task.id+'/run'&&r.request().method()==='POST');
  await button.click();const responseBody=await response;
  if(responseBody.status()!==200){const body=await responseBody.json();throw Error('立即运行请求失败：HTTP '+responseBody.status()+'；'+body.error)}
  const claim=await responseBody.json();
  for(let i=0;i<310;i++){run=await get('/api/schedules/run/'+claim.id);if(run.status!=='running')break;await pause(1000)}
  check('工作台立即运行成功且记录可查',run.status==='succeeded');
  }
  const today=await get('/api/today');report=today.clue_morning;
  check('重启后新接口返回完整晨报',report?.status==='ok'&&report.content.startsWith('# 微信线索晨报'));
  const meta=JSON.parse(fs.readFileSync(path.join(ROOT,'workspace/clue-morning',report.date+'.json')));
  if(!VIEW)check('非空晨报判断来自Pi且记录生成时间',meta.clue_count===0?meta.judgment.source==='rules':meta.judgment.source==='pi'&&!!meta.judgment.generated_at);
  fs.writeFileSync(path.join(out,VIEW?'real-cli-preview.json':'real-run.json'),JSON.stringify({task_id:task.id,run_id:run?.id,status:run?.status,started_at:run?.started_at,finished_at:run?.finished_at,clue_count:meta.clue_count,category_counts:meta.category_counts,judgment:meta.judgment},null,2));
  if(!VIEW){
  await page.locator('[data-action=history][data-id="'+task.id+'"]').evaluate(b=>b.click());
  await page.waitForFunction(()=>{const text=document.querySelector('#runs').textContent;return text.includes('成功')&&text.includes('微信线索晨报')});
  check('页面运行记录可见成功',await page.locator('#runs').innerText().then(t=>t.includes('成功')&&t.includes('微信线索晨报')));
  // 仅截任务这一行，避免其他任务输出进入证据。
  await page.locator('#runs').evaluate(el=>{for(const child of el.children)if(!child.textContent.includes('微信线索晨报'))child.remove()});
  await page.locator('#runs').screenshot({path:path.join(out,'real-run.png')});
  }
 }else{
  report={status:'ok',date:'2026-10-30',clue_count:1,category_counts:{business:1,technology:0,topics:0,projects:0,other:0},content:'# 微信线索晨报 · 合成数据\n\n## 商单（1 条）\n\n<script>window.syntheticXss=1</script>\n发言人：合成发言人\n合作需求：合成合作需求\n\n当日判断：合成判断。'};
  await page.route('**/api/today?*',r=>r.fulfill({json:{items:[],top:[],total:0,page:1,topics:[],sources:{},filtering:{items:[],total:0,by_source:{}},clue_morning:report}}));
 }
 await page.goto(base+'/today.html');await page.waitForFunction(()=>document.querySelector('#item-list').getAttribute('aria-busy')==='false');
 check('晨报入口位于两个内容通道之前',await page.locator('#clue-morning-panel').evaluate(el=>!!(el.compareDocumentPosition(document.querySelector('.channel-tabs'))&Node.DOCUMENT_POSITION_FOLLOWING)));
 for(const width of [1440,390]){
  await page.setViewportSize({width,height:width===390?844:1000});
  await page.locator('#clue-morning-panel').evaluate(el=>el.open=false);
  await page.screenshot({path:path.join(out,(REAL?'real':'synthetic')+'-'+width+'-collapsed.png')});
  await page.locator('#clue-morning-summary').click();
  check(width+'点开可读完整晨报',await page.locator('#clue-morning-content').textContent()===report.content);
  check(width+'晨报不混入列表或阅读器',await page.locator('#item-list #clue-morning-content,#reader #clue-morning-content').count()===0);
  check(width+'页面无横向溢出',await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  if(REAL){
   // 截图隐藏真实线索标题、正文、发言人；完整正文已在上方逐字校验。
   await page.locator('#clue-morning-content').evaluate(el=>el.textContent=el.textContent.split('\n').map(line=>/^(# 微信|## |生成时间：|统计窗口：|线索共 |分类计数：|本类仅展示)/.test(line)?line:line?'（线索正文已隐藏）':'').join('\n'));
  }else check('合成标签按纯文本显示且不执行',await page.locator('#clue-morning-content script').count()===0&&await page.evaluate(()=>!window.syntheticXss));
  await page.screenshot({path:path.join(out,(REAL?'real':'synthetic')+'-'+width+'-expanded.png'),
    ...(REAL?{mask:[page.locator('#clue-morning-content')],maskColor:'#e7ede2'}:{})});
  if(REAL)await page.locator('#clue-morning-content').evaluate((el,text)=>el.textContent=text,report.content);
 }
 if(!REAL){
  await page.locator('#clue-morning-summary').click();
  await page.locator('[data-channel=interest]').click();await page.waitForFunction(()=>!document.querySelector('#item-list').inert);
  check('兴趣通道仍显示独立晨报入口',await page.locator('#clue-morning-summary').innerText().then(t=>t.includes('1 条')));
  report={status:'missing',message:'今日晨报尚未生成'};
  await page.locator('[data-channel=hot]').click();await page.waitForFunction(()=>!document.querySelector('#item-list').inert);
  check('无文件显示尚未生成且不冒充无新增',await page.locator('#clue-morning-summary').innerText().then(t=>t.includes('尚未生成')&&!t.includes('今日无新增')));
 }
 check('无页面脚本错误',errors.length===0);
 }finally{fs.writeFileSync(path.join(out,(REAL?'real':'synthetic')+'-e2e-results.json'),JSON.stringify({checks,errors},null,2));if(context)await context.close()}
})().catch(error=>{console.error(error.message);process.exitCode=1});
