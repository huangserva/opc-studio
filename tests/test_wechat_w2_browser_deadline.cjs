const fs=require('node:fs'),assert=require('node:assert/strict');
const {chromium}=require('/Users/huangzongning/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
(async()=>{
 const root=process.env.WECHAT_W2_R2_DIR||'/tmp/wechat-w2-r2',base='http://127.0.0.1:'+fs.readFileSync(root+'/ui-port','utf8').trim(),old=process.env.W2_OLD_UI==='1';
 const context=await chromium.launchPersistentContext(fs.mkdtempSync(root+'/pw-'),{channel:'chrome',headless:true,viewport:{width:1440,height:1000}}),page=await context.newPage(),results=[],errors=[],requests=[];
 page.on('pageerror',e=>errors.push(e.message));page.on('request',r=>{if(r.url()===base+'/api/clues/wechat/status')requests.push({url:r.url(),method:r.method(),type:r.resourceType(),frame:r.frame()===page.mainFrame()});});
 await page.route('**/*',route=>{const req=route.request(),url=new URL(req.url());if(url.origin!==base||!['GET','HEAD'].includes(req.method()))return route.abort();if(old&&url.pathname==='/clues.js')return route.fulfill({contentType:'text/javascript',body:fs.readFileSync(root+'/before/workbench/public/clues.js','utf8')});return route.continue();});
 try{
  for(const scenario of old?['body']:['normal','stat_failure','cold','transport_error','body','headers','schedules','health']){
   fs.writeFileSync(root+'/ui-scenario',scenario);const timeout=['body','headers','schedules','health'].includes(scenario);const start=performance.now();
   await page.goto(base+'/clues.html?case='+scenario);
   if(timeout)await page.waitForFunction(()=>document.querySelector('#cl-analysis-status').textContent.includes('状态暂不可用'),{},{timeout:7800});
   else await page.waitForFunction(()=>!document.querySelector('#cl-analysis-status').textContent.includes('正在读取微信分析状态'));
   const seconds=(performance.now()-start)/1000;
   const ui=await page.evaluate(()=>({status:document.querySelector('#cl-analysis-status').textContent,detail:document.querySelector('#cl-analysis-detail').textContent,warning:document.querySelector('#cl-coverage-detail').textContent,warningVisible:!document.querySelector('#cl-coverage-warning').hidden}));
   if(timeout){assert.ok(seconds<8);assert.ok(seconds>5);assert.match(ui.status,/超时/);assert.equal(ui.warningVisible,true);}
   else if(scenario==='normal'){assert.match(ui.status,/已检查 387/);assert.match(ui.detail,/截至 \d{2}:\d{2}/);assert.doesNotMatch(ui.status,/状态异常/);}
   else if(scenario==='stat_failure'){assert.match(ui.status,/已检查 387/);assert.match(ui.status,/数据可能过时/);assert.match(ui.detail,/截至 \d{2}:\d{2}/);}
   else if(scenario==='cold'){assert.match(ui.status,/统计中/);assert.doesNotMatch(ui.status,/已检查\s*0/);}
   else assert.match(ui.status,/状态异常/);
   await page.locator('#cl-analysis-info').evaluate(el=>el.open=true);await page.locator('#cl-coverage-warning').evaluate(el=>el.open=true);
   if(scenario==='body')await page.setViewportSize({width:390,height:900});
   await page.screenshot({path:root+'/ui-'+scenario+'.png',fullPage:true});
   if(scenario==='body')await page.setViewportSize({width:1440,height:1000});
   results.push({scenario,seconds,ui});console.log(JSON.stringify({scenario,seconds,status:ui.status}));
  }
  assert.deepEqual(errors,[]);assert.ok(requests.every(r=>r.method==='GET'&&r.type==='fetch'&&r.frame));
  fs.writeFileSync(root+'/'+(old?'old-ui':'ui-results')+'.json',JSON.stringify({old,results,errors,requests,passed:true},null,2));
 }catch(error){fs.writeFileSync(root+'/'+(old?'old-ui':'ui-results')+'.json',JSON.stringify({old,results,errors,requests,passed:false,error:error.message},null,2));throw error;}
 finally{fs.writeFileSync(root+'/ui-scenario','normal');await context.close();}
})().catch(error=>{console.error(error);process.exit(1)});
