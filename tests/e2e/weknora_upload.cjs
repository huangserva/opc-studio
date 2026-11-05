'use strict';
// Explicit sandbox; no remote requests, independent profile and random port.
const fs=require('fs'),os=require('os'),path=require('path'),assert=require('assert/strict');
const {spawn,execFileSync}=require('child_process');
const {chromium}=(()=>{try{return require('playwright')}catch{return require(path.join(os.homedir(),'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright'))}})();
const ROOT=path.resolve(__dirname,'../..'),PY=path.resolve(ROOT,'../article-importer/.venv/bin/python');
const tmp=fs.mkdtempSync('/tmp/m3-upload-e2e-');const child=spawn(PY,['-B','tests/support/weknora_upload_server.py','--root',path.join(tmp,'data')],{cwd:ROOT});
let log='';child.stderr.on('data',x=>log+=x);const checks=[];const check=(n,v)=>{assert.ok(v,n);checks.push({name:n,passed:true});};
(async()=>{
 const port=await new Promise((resolve,reject)=>{let data='';child.stdout.on('data',x=>{data+=x;try{resolve(JSON.parse(data.split('\n')[0]).port)}catch{}});child.on('exit',()=>reject(Error(log)));setTimeout(()=>reject(Error('start timeout '+log)),20000).unref();});
 const base='http://127.0.0.1:'+port;
 const context=await chromium.launchPersistentContext(path.join(tmp,'profile'),{headless:true});
 const page=await context.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));let external=0;await context.route('**/*',r=>{if(new URL(r.request().url()).origin!==base){external++;return r.abort();}return r.continue();});
 try{
  await page.goto(base+'/knowledge-upload.html');await page.waitForFunction(()=>document.querySelectorAll('.document').length===2);
  check('默认未上传清单2篇',await page.locator('.document').count()===2);
  check('默认按钮禁用',await page.locator('#upload').isDisabled());
  await page.locator('.document input').first().check();check('选择单篇更新按钮',await page.locator('#upload').textContent()==='上传所选（1）');
  await page.locator('#upload').click();await page.waitForFunction(()=>document.querySelector('#status').textContent.includes('已完成 1 篇'));
  check('按钮上传回执和列表状态变化',await page.locator('.document').count()===1);
  const db=path.join(tmp,'data/workspace/weknora/ledger.db');
  const rows=()=>JSON.parse(execFileSync(PY,['-B','-c',`import sqlite3,json;c=sqlite3.connect(${JSON.stringify(db)});print(json.dumps(c.execute('SELECT result_number,knowledge_id FROM result_ledger ORDER BY result_number').fetchall()))`],{encoding:'utf8'}));
  check('真实台账新增1行',rows().length===2);
  await page.locator('#pending-only').uncheck();check('已上传列表可查看',await page.locator('.result').first().textContent()==='上传成功');
  for(let i=0;i<3;i++){await page.locator('#upload').click();await page.waitForFunction(()=>document.querySelector('#status').textContent.includes('跳过 1 篇'));}
  check('连点三次保持唯一知识ID',rows().length===2);
  await page.locator('#clear-selection').click();await page.locator('#select-visible').click();await page.locator('#upload').click();await page.waitForFunction(()=>document.querySelector('#status').textContent.includes('已完成 1 篇，跳过 1 篇'));
  check('混合已传未传逐篇回执',rows().length===3);
  const bad=await context.request.post(base+'/api/knowledge/upload',{data:{documents:[]}});check('缺少Origin和授权头403',bad.status()===403);
  const bad2=await context.request.post(base+'/api/knowledge/upload',{headers:{Origin:base,'X-Article-Studio':'1'},data:{documents:[],kb_id:'private'}});check('服务端拒绝伪造目标库400',bad2.status()===400);
  await page.setViewportSize({width:390,height:844});check('390px无横向溢出',await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  await page.screenshot({path:path.join(tmp,'upload-390.png'),fullPage:true});
  check('pageerror为0',errors.length===0);check('外部请求为0',external===0);
 }finally{await context.close();child.kill();fs.writeFileSync(path.join(tmp,'results.json'),JSON.stringify({checks,errors,external},null,2));console.log(JSON.stringify({tmp,passed:checks.length,checks}));}
})().catch(e=>{child.kill();console.error(e);process.exitCode=1;});
