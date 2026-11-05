'use strict';
const fs=require('fs'),path=require('path'),os=require('os'),assert=require('assert/strict'),{spawn}=require('child_process');
const {chromium}=require('playwright');const ROOT=path.resolve(__dirname,'../..'),PY=path.resolve(ROOT,'../article-importer/.venv/bin/python'),tmp=fs.mkdtempSync('/tmp/m2-review-e2e-');
const child=spawn(PY,['-B','tests/support/weknora_review_upload_server.py','--root',path.join(tmp,'data')],{cwd:ROOT});let log='';child.stderr.on('data',x=>log+=x);const checks=[];
(async()=>{
 const info=await new Promise((resolve,reject)=>{child.stdout.once('data',x=>resolve(JSON.parse(x)));child.on('exit',()=>reject(Error(log)));});const base='http://127.0.0.1:'+info.port,context=await chromium.launchPersistentContext(path.join(tmp,'profile'),{headless:true});const page=await context.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));let external=0;await context.route('**/*',r=>{if(new URL(r.request().url()).origin!==base){external++;return r.abort();}return r.continue();});
 const check=(n,v)=>{assert.ok(v,n);checks.push({name:n,passed:true});};
 try{
  for(const slug of info.works){
   await page.goto(base+'/feedback.html?review='+slug);await page.locator('#fb-review-notes').waitFor();await page.locator('#fb-review-notes').fill('【验收合成】蓝杉复盘回流：样本不足，只保留核验事实，后续再观察。');
   await page.locator('#fb-review-save').click();await page.waitForResponse(r=>r.url().includes('/api/feedback/review/'+slug)&&r.request().method()==='GET');
   await page.locator('#fb-review-knowledge').click();await page.waitForResponse(r=>r.url().includes('/api/feedback/review/'+slug)&&r.request().method()==='GET');
  }
  check('两篇复盘实际保存及本机回库',true);
  await page.locator('a[href="/knowledge-upload.html?source=reviews"]').click();await page.waitForFunction(()=>document.querySelector('#documents').getAttribute('aria-busy')==='false');check('复盘筛选入口两卡可选',await page.locator('.document').count()===2&&await page.locator('#upload-source').inputValue()==='reviews');
  check('隐私内部文案明确',(await page.locator('.document').first().textContent()).includes('隐私-内部'));
  await page.locator('#select-visible').click();await page.locator('#upload').click();await page.waitForFunction(()=>document.querySelector('#status').textContent.includes('已完成 2 篇'));check('真实按钮两篇上传成功及列表变化',await page.locator('.document').count()===0);
  for(let i=0;i<3;i++){await page.locator('#upload').click();await page.waitForFunction(()=>document.querySelector('#status').textContent.includes('跳过 2 篇'));}check('三次重复点击幂等',true);
  await page.locator('#pending-only').uncheck();check('两篇持久状态可查看',await page.locator('.document').count()===2);
  await page.locator('#upload-source').selectOption('articles');check('切换来源清空选择',await page.locator('#upload').isDisabled());await page.locator('#upload-source').selectOption('reviews');
  await page.setViewportSize({width:390,height:844});check('390px无溢出',await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));await page.screenshot({path:path.join(tmp,'review-upload-390.png')});check('脚本错误与外部请求0',errors.length===0&&external===0);
 }finally{await context.close();child.kill();fs.writeFileSync(path.join(tmp,'results.json'),JSON.stringify({checks,errors,external},null,2));}
 console.log(JSON.stringify({tmp,passed:checks.length,checks}));
})().catch(e=>{child.kill();console.error(e);process.exitCode=1;});
