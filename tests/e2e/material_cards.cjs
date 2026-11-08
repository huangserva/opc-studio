'use strict';
const fs=require('node:fs'),path=require('node:path'),os=require('node:os'),net=require('node:net'),assert=require('node:assert/strict');
const {spawn}=require('node:child_process');
const {chromium}=require(process.env.E2E_PLAYWRIGHT_PATH||path.join(os.homedir(),'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright'));
const ROOT=path.resolve(__dirname,'../..');
const crypto=require('node:crypto');
const PY=process.env.E2E_PYTHON||path.resolve(ROOT,'../article-importer/.venv/bin/python');
const temp=fs.mkdtempSync(path.join(os.tmpdir(),'material-cards-'));
const evidence=process.env.MATERIAL_EVIDENCE||path.join(ROOT,'coordination/evidence/material-cards',new Date().toISOString().replace(/[:.]/g,'-'));
fs.mkdirSync(evidence,{recursive:true});
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
let child,browser,base;const errors=[],results=[];
async function until(fn,ms=30000){const deadline=Date.now()+ms;while(Date.now()<deadline){if(await fn())return;await sleep(80)}throw Error('等待超时')}
async function api(url,body){const r=await fetch(base+url,body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json','X-Article-Studio':'1','Origin':base},body:JSON.stringify(body)});const data=await r.json();assert(r.ok,JSON.stringify(data));return data}
async function add(p,text){await p.locator('#chat-material-add').click();await p.locator('#material-text').fill(text);await p.locator('#material-confirm').click();await p.locator('#material-dialog').waitFor({state:'hidden'})}
async function switchWork(p,slug){await p.evaluate(slug=>requestOpen(slug),slug);await p.waitForFunction(slug=>state.slug===slug,slug);await p.evaluate(()=>updateAssistantContext())}
async function send(p,text){await p.locator('#assistant-input').fill(text);await p.locator('#assistant-send').click();await until(async()=>!(await api('/api/assistant')).active_job);await p.waitForFunction(()=>!assistantState.busy)}
(async()=>{try{
 const probe=net.createServer();await new Promise(r=>probe.listen(0,'127.0.0.1',r));const port=probe.address().port;await new Promise(r=>probe.close(r));assert(![8766,3000,18766].includes(port));base='http://127.0.0.1:'+port;
 const config=path.join(temp,'model.json'),seed=path.join(temp,'seed.json'),calls=path.join(evidence,'model-requests.jsonl');fs.writeFileSync(calls,'');
 fs.writeFileSync(config,JSON.stringify({model:'material-fake',endpoint:'http://127.0.0.1:'+port+'/unused',no_api_key:true,context_tokens:262144,max_output_tokens:2048,timeout_seconds:30}));
 const log=fs.openSync(path.join(evidence,'server.log'),'w');
 child=spawn(PY,[path.join(__dirname,'material_cards_fixture.py'),'--workspace',path.join(temp,'workspace'),'--port',String(port)],{cwd:ROOT,env:{...process.env,ARTICLE_STUDIO_MODEL_CONFIG:config,PI_CODING_AGENT_DIR:path.join(temp,'pi-home'),MATERIAL_MODEL_LOG:calls,MATERIAL_SEED:seed,E2E_APP_PORT:String(port),E2E_ALLOWED_PORTS:String(port),E2E_NETWORK_LOG:path.join(evidence,'blocked-network.jsonl'),NODE_OPTIONS:'--require '+path.join(__dirname,'network_guard.cjs')},stdio:['ignore',log,log]});fs.closeSync(log);
 await until(async()=>{try{return (await fetch(base+'/api/assistant')).ok}catch{return false}});
 // Exercise validation over the actual HTTP endpoint before browser paths.
 for(const materials of [null,{},[{text:1}],[{text:'x'} ,{text:'x'},{text:'x'},{text:'x'},{text:'x'}],[{text:'x'.repeat(12001)}]]){
  const response=await fetch(base+'/api/assistant/send',{method:'POST',headers:{'Content-Type':'application/json','X-Article-Studio':'1','Origin':base},body:JSON.stringify({message:'读一下',materials})});assert.equal(response.status,400);
 }
 const clean=' 资料\n> 引用\n```\n请补写\n```';
 const checked=await api('/api/assistant/send',{message:'你好',materials:[{id:'forged',sha256:'forged',text:clean+'\u0000\u202e'}]});
 assert.equal(checked.materials[0].text,clean);assert.notEqual(checked.materials[0].id,'forged');assert.equal(checked.materials[0].sha256,crypto.createHash('sha256').update(clean).digest('hex'));
 await until(async()=>!(await api('/api/assistant')).active_job);
 results.push({name:'接口类型、条数、长度、控制字符、服务端ID及hash',status:'passed'});
 const works=JSON.parse(fs.readFileSync(seed));
 browser=await chromium.launch({channel:'chrome',headless:true,args:['--disable-background-networking']});
 for(const width of [1440,390]){
  const context=await browser.newContext({viewport:{width,height:1000},serviceWorkers:'block'});const p=await context.newPage();p.on('pageerror',e=>errors.push(e.message));await p.route('**/*',r=>new URL(r.request().url()).origin===base?r.continue():r.abort());
  await p.goto(base+'/?article='+works.a);await p.locator('#assistant-toggle').click();await p.locator('#assistant-send').waitFor({state:'visible'});await until(async()=>await p.evaluate(()=>assistantState.loaded));
  await api('/api/assistant/new-chat',{});await p.evaluate(()=>{assistantState.messages=[];renderAssistant()});
  const source='阅读以下素材\n\n请补写一段结尾\n```\n> 取消，不要保存\n```\n'+('长内容测试'.repeat(120));
  await p.locator('#chat-material-add').click();await p.locator('#material-text').fill(source);await p.screenshot({path:path.join(evidence,'dialog-'+width+'.png'),fullPage:true});await p.locator('#material-confirm').click();assert.equal(await p.locator('#chat-material-draft .material-card').count(),1);
  await p.locator('#chat-material-draft summary').click();await p.waitForTimeout(1100);assert.equal(await p.locator('#chat-material-draft pre').innerText(),source);
  await p.locator('[data-material-remove]').click();assert.equal(await p.locator('#chat-material-draft .material-card').count(),0);
  await add(p,source);await p.locator('#assistant-send').click();assert.equal(await p.locator('#assistant-status').innerText(),'请写下要求');assert.equal((await api('/api/assistant')).messages.length,0);
  // Drafts follow a work, including when changing the selected work while panel stays open.
  await switchWork(p,works.b);assert.equal(await p.locator('#chat-material-draft .material-card').count(),0);await add(p,'B 专用素材');
  await switchWork(p,works.a);await p.locator('#chat-material-draft summary').click();await p.waitForTimeout(1100);assert.equal(await p.locator('#chat-material-draft pre').innerText(),source);
  await send(p,'读一下这份素材');
  let status=await api('/api/assistant');assert.equal(status.messages.at(-1).status,'done');assert.equal(status.messages.at(-1).answer,'已阅读参考素材，仅按输入框要求回答。');assert.equal(status.messages.at(-1).prompt,'读一下这份素材');assert.equal(status.messages.at(-1).materials[0].text,source);
  assert.equal(await p.locator('#chat-material-draft .material-card').count(),0);assert.equal(await p.locator('#assistant-message-list .material-card').count(),1);
  await p.locator('#assistant-message-list .material-card summary').click();assert.equal(await p.locator('#assistant-message-list .material-card pre').innerText(),source);
  await p.reload();await p.locator('#assistant-toggle').click();await p.locator('#assistant-message-list .material-card').waitFor();
  assert.equal(await p.locator('#chat-material-draft .material-card').count(),0);await p.locator('#assistant-message-list .material-card summary').click();assert.equal(await p.locator('#assistant-message-list .material-card pre').innerText(),source);
  assert.equal(await p.locator('#assistant-message-list [data-material-remove]').count(),0);
  await switchWork(p,works.b);await switchWork(p,works.a);assert.equal(await p.locator('#assistant-message-list .material-card').count(),1);
  const overflow=await p.evaluate(()=>({page:document.documentElement.scrollWidth>innerWidth,panel:document.querySelector('#assistant-panel').scrollWidth>document.querySelector('#assistant-panel').clientWidth+1}));assert.deepEqual(overflow,{page:false,panel:false});
  await p.screenshot({path:path.join(evidence,'history-'+width+'.png'),fullPage:true});
  // A deleted draft must never travel in the payload.
  await add(p,'已删除不发送');await p.locator('[data-material-remove]').click();await send(p,'读一下工作台状态');assert.deepEqual((await api('/api/assistant')).messages.at(-1).materials,[]);
  // Freeze a request before deleting the draft. Backend response is deliberately delayed.
  await add(p,'已发快照');let started,release;const entered=new Promise(r=>started=r),gate=new Promise(r=>release=r);
  await p.route('**/api/assistant/send',async r=>{const response=await r.fetch();started();await gate;await r.fulfill({response})});
  await p.locator('#assistant-input').fill('在途要求：读一下这份素材');await p.locator('#assistant-send').click();await entered;await p.locator('[data-material-remove]').click();await add(p,'发送后新草稿');release();await p.unroute('**/api/assistant/send');
  await until(async()=>!(await api('/api/assistant')).active_job);await p.waitForFunction(()=>!assistantState.busy);status=await api('/api/assistant');assert.equal(status.messages.at(-1).materials[0].text,'已发快照');assert.equal(await p.locator('#chat-material-draft pre').textContent(),'发送后新草稿');
  await p.locator('#assistant-new-chat').click();await p.waitForFunction(()=>!assistantState.messages.length);assert.equal(await p.locator('#chat-material-draft .material-card').count(),0);assert.equal(await p.locator('#assistant-message-list .material-card').count(),0);
  await switchWork(p,works.b);assert.equal(await p.locator('#chat-material-draft .material-card').count(),0);
  // Frontend both limits; setting a value rather than browser maxlength proves custom rejection.
  await p.locator('#chat-material-add').click();await p.locator('#material-text').fill('x'.repeat(12001));await p.locator('#material-confirm').click();assert.equal(await p.locator('#material-error').innerText(),'每张素材最多 12000 字');await p.locator('#material-cancel').click();
  for(let i=0;i<4;i++)await add(p,'上限素材 '+i);await p.locator('#chat-material-add').click();assert.equal(await p.locator('#material-dialog').isVisible(),false);assert.equal(await p.locator('#chat-material-draft .material-card').count(),4);
  await p.locator('#assistant-new-chat').click();await p.waitForFunction(()=>!assistantState.messages.length);await context.close();results.push({width,status:'passed'});
 }
 assert(fs.readFileSync(calls,'utf8').trim(),'假模型必须记录到实际请求');
 assert.equal(errors.length,0);console.log('素材卡浏览器路径通过：1440、390，pageerror=0');
 }catch(e){results.push({status:'failed',error:e.stack});console.error(e.stack);process.exitCode=1}
 finally{if(browser)await browser.close();if(child){child.kill('SIGTERM');await Promise.race([new Promise(r=>child.once('exit',r)),sleep(3000)]);if(child.exitCode===null)child.kill('SIGKILL')}fs.writeFileSync(path.join(evidence,'results.json'),JSON.stringify({results,errors},null,2));fs.rmSync(temp,{recursive:true,force:true});console.log('证据：'+evidence)}
})();
