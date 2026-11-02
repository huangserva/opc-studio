const fs=require('node:fs'),assert=require('node:assert/strict');
const {chromium}=require('/Users/huangzongning/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
(async()=>{
 const root=process.env.WECHAT_W2_ROOT;assert.ok(root,'请设置隔离候选 WECHAT_W2_ROOT，禁止使用正式工作区');
 const out=root+'/r1',baseline=process.env.W2_BASELINE==='1';
 const base=JSON.parse(fs.readFileSync(out+'/ports.json')).workbench;
 const context=await chromium.launchPersistentContext(fs.mkdtempSync(out+'/pw-'),{channel:'chrome',headless:true,viewport:{width:1400,height:1000}});
 const page=await context.newPage(),errors=[],cases=[],failures=[];
 page.on('pageerror',e=>errors.push(e.message));
 if(baseline)await page.route('**/clues.js',route=>route.fulfill({contentType:'text/javascript',body:fs.readFileSync(root+'/baseline/workbench/public/clues.js','utf8')}));
 await page.route('**/api/**',async route=>{
  const p=new URL(route.request().url()).pathname;
  if(p==='/api/clues/wechat/status')return route.continue();
  let data={};
  if(p==='/api/schedules')data={tasks:[{kind:'wechat_clues',enabled:false,running:false,last_run:{id:'c'.repeat(32),status:'succeeded'}}]};
  else if(p==='/api/knowledge/health')data={state:'current',capture:{status:'ok'}};
  else if(p.startsWith('/api/knowledge'))data={sources:[]};
  else if(p==='/api/clues')data={items:[],timeline:[],total:0,next:null,next_offset:null,counts:{},tag_counts:{},heat_counts:{},years:[],sources:[]};
  return route.fulfill({contentType:'application/json',body:JSON.stringify(data)});
 });
 function check(label,fn){try{fn();}catch(error){failures.push({label,message:error.message});}}
 try{
  let normal=null;
  for(const scenario of ['normal','stat_failure','cold','transport_error']){
   fs.writeFileSync(out+'/scenario',scenario);
   // Exact current document's GET; no request from a prefetch or alternative endpoint is accepted.
   const responsePromise=page.waitForResponse(r=>r.url()===base+'/api/clues/wechat/status'&&r.request().method()==='GET'&&r.request().resourceType()==='fetch');
   await page.goto(base+'/clues.html?case='+scenario);
   const response=await responsePromise;await response.finished();const api=await response.json();
   await page.waitForFunction(()=>!document.querySelector('#cl-analysis-status').textContent.includes('正在读取微信分析状态'));
   const ui=await page.evaluate(()=>({status:document.querySelector('#cl-analysis-status').textContent,detail:document.querySelector('#cl-analysis-detail').textContent,warning:document.querySelector('#cl-coverage-detail').textContent,summary:document.querySelector('#cl-coverage-summary').textContent,warningVisible:!document.querySelector('#cl-coverage-warning').hidden}));
   await page.locator('#cl-analysis-info').evaluate(el=>el.open=true);
   if(ui.warningVisible)await page.locator('#cl-coverage-warning').evaluate(el=>el.open=true);
   const prefix=baseline?'baseline':'candidate';await page.screenshot({path:out+'/'+prefix+'-'+scenario+'.png',fullPage:true});
   check(scenario+' HTTP200',()=>assert.equal(response.status(),200));
   const all=ui.status+' '+ui.detail+' '+ui.warning+' '+ui.summary;
   if(scenario==='normal'){
    normal=api;check('normal API fresh',()=>assert.equal(api.coverage_stale,false));check('normal count',()=>assert.ok(all.includes(String(api.processed_versions))));
    check('normal as_of HH:MM',()=>assert.match(ui.detail,/截至 \d{2}:\d{2}/));
   }else if(scenario==='stat_failure'){
    check('failed statistics keep previous count',()=>assert.equal(api.processed_versions,normal.processed_versions));
    check('failed statistics keep previous as_of',()=>assert.equal(api.coverage_as_of,normal.coverage_as_of));
    check('failed API stale',()=>assert.equal(api.coverage_stale,true));
    check('failed UI stale',()=>assert.match(all,/数据可能过时/));
    check('failed UI as_of HH:MM',()=>assert.match(all,/截至 \d{2}:\d{2}/));
    check('failed UI error',()=>assert.match(all,/状态异常|统计失败|暂不可用/));
    check('failed warning visible',()=>assert.equal(ui.warningVisible,true));
   }else if(scenario==='cold'){
    check('cold API null',()=>assert.equal(api.processed_versions,null));
    check('cold UI pending',()=>assert.match(ui.status,/统计中/));
    check('cold UI never zero',()=>assert.doesNotMatch(ui.status,/已检查\s*0\s*条/));
   }else{
    check('transport source stage',()=>assert.equal(api.error_stage,'source_read'));
    check('transport UI error',()=>assert.match(all,/状态异常|暂不可用/));
    check('transport UI stale',()=>assert.match(all,/数据可能过时/));
   }
   cases.push({scenario,url:response.url(),method:response.request().method(),resourceType:response.request().resourceType(),http:response.status(),api,ui});
  }
  check('no browser page errors',()=>assert.deepEqual(errors,[]));
  const result={baseline,cases,failures,errors,passed:failures.length===0};
  fs.writeFileSync(out+'/'+(baseline?'baseline':'candidate')+'-browser.json',JSON.stringify(result,null,2));
  console.log(JSON.stringify({baseline,failures,passed:result.passed}));
  assert.equal(failures.length,0,'浏览器行为断言失败');
 }finally{fs.writeFileSync(out+'/scenario','normal');await context.close();}
})().catch(e=>{console.error(e);process.exit(1)});
