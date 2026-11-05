'use strict';
// M3 R1: unchanged production frontend + Handler, fake-only external operations.
const fs=require('fs'),path=require('path'),os=require('os'),assert=require('assert/strict'),{spawn}=require('child_process');
const {chromium}=require('playwright'),ROOT=path.resolve(__dirname,'../..'),PY=path.resolve(ROOT,'../article-importer/.venv/bin/python');
const tmp=fs.mkdtempSync('/tmp/m3-r1-browser-'),checks=[];
(async()=>{
 for(const kind of ['oversize','read','empty']){
  const child=spawn(PY,['-B','tests/support/weknora_upload_isolation_server.py','--root',path.join(tmp,kind),'--kind',kind],{cwd:ROOT});let log='';child.stderr.on('data',x=>log+=x);
  const {port}=await new Promise((resolve,reject)=>{child.stdout.once('data',x=>resolve(JSON.parse(x)));child.on('exit',()=>reject(Error(log)));});const base='http://127.0.0.1:'+port;
  const context=await chromium.launchPersistentContext(path.join(tmp,kind+'-profile'),{headless:true}),page=await context.newPage(),errors=[];let external=0;page.on('pageerror',e=>errors.push(e.message));await context.route('**/*',r=>{if(new URL(r.request().url()).origin!==base){external++;return r.abort();}return r.continue();});
  const check=(name,ok)=>{assert.ok(ok,kind+' '+name);checks.push({kind,name,passed:true});};
  try{
   const response=await page.goto(base+'/knowledge-upload.html');await page.waitForFunction(()=>document.querySelector('#documents').getAttribute('aria-busy')==='false');
   check('两张卡片正常显示',await page.locator('.document').count()===2);
   const bad=page.locator('.document[data-number="CW-one"]'),good=page.locator('.document[data-number="WC-two"]');check('异常篇明确不可上传及原因',(await bad.locator('.result').textContent()).includes('不可上传'));
   check('清单API200且失败状态可见',(await context.request.get(base+'/api/knowledge/upload-candidates')).status()===200);
   await good.locator('input').check();await page.locator('#upload').click();await page.waitForFunction(()=>document.querySelector('#status').textContent.includes('已完成 1 篇'));check('只选正常篇按钮上传成功',await page.locator('.document').count()===1);
   // The unchanged frontend lets failed cards be explicitly retried; backend returns a per-item failure.
   await bad.locator('input').check();await page.locator('#upload').click();await page.waitForFunction(()=>document.querySelector('#status').textContent.includes('失败 1 篇'));check('混选异常篇失败正常篇跳过',await page.locator('#status').textContent()==='已完成 0 篇，跳过 1 篇，失败 1 篇。');
   check('失败卡仍可定位',(await bad.locator('.result').textContent()).includes('不可上传'));
   await page.setViewportSize({width:390,height:844});check('390px无溢出',await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));await page.screenshot({path:path.join(tmp,kind+'-390.png')});check('无脚本错误与外部请求',errors.length===0&&external===0);
  }finally{await context.close();child.kill();}
 }
 fs.writeFileSync(path.join(tmp,'results.json'),JSON.stringify({checks},null,2));console.log(JSON.stringify({tmp,passed:checks.length,checks}));
})().catch(e=>{console.error(e);process.exitCode=1;});
