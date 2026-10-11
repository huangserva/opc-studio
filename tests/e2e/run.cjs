'use strict';
const fs=require('node:fs'),path=require('node:path'),os=require('node:os'),http=require('node:http'),net=require('node:net'),crypto=require('node:crypto');
const {spawn}=require('node:child_process'),assert=require('node:assert/strict');
const {stopChild,forceStopSync,alive}=require('./stop_child.cjs');
const {assistantReply,isAssistant}=require('./assistant_model.cjs');
const forceSaveModel=require('./force_and_save_model.cjs');
let visionGate=null;
function holdVision(){let finish;const gate={started:false,released:false,wait:new Promise(r=>{finish=r}),release(){if(!this.released){this.released=true;finish()}}};visionGate=gate;return gate;}
const ROOT=path.resolve(__dirname,'../..');process.chdir(ROOT);
const only=new Set((process.env.E2E_ONLY||'').split(',').map(item=>item.trim()).filter(Boolean));
function selected(name){return !only.size||[...only].some(id=>name===id||name.startsWith(id+'-'))}
function playwright(){
 for(const mod of [process.env.E2E_PLAYWRIGHT_PATH,'playwright',path.join(os.homedir(),'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright')].filter(Boolean)){
  try{return require(mod)}catch(e){if(e.code!=='MODULE_NOT_FOUND')throw e}
 }
 throw new Error('缺少 Playwright。先 npm install --prefix tests/e2e，再运行；套件运行期间不会安装或下载依赖。');
}
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
const stamp=new Date().toISOString().replace(/[:.]/g,'-')+'-'+process.pid+'-'+crypto.randomBytes(3).toString('hex'),evidence=path.join(ROOT,'coordination/evidence/e2e',stamp);
fs.mkdirSync(evidence,{recursive:true});const temp=fs.mkdtempSync(path.join(os.tmpdir(),'article-studio-e2e-'));
const workspace=path.join(temp,'workspace');fs.mkdirSync(workspace);fs.mkdirSync(path.join(temp,'home'));
const results=[],modelCalls=[],httpCalls=[],browserErrors=[],networkLog=path.join(evidence,'blocked-network.jsonl');fs.writeFileSync(networkLog,'');
const BIZ='MzB5NTkwMDAwMDE==',LINK='https://mp.weixin.qq.com/s?__biz=MzB5NTkwMDAwMDE%3D%3D&mid=123&idx=1&sn=fixture&chksm=fixture';
let child,browser,modelServer,base,scheduledCardFailed=false;
const SUITE_TIMEOUT_MS=300000;
const watchdog=setTimeout(()=>{
 console.error('E2E 总超时 '+SUITE_TIMEOUT_MS/1000+' 秒');
 results.push({name:'总超时',status:'failed',error:'总超时 '+SUITE_TIMEOUT_MS/1000+' 秒，已停止浏览器与临时进程'});
 try{if(browser)browser.close()}catch{}
 try{if(child)forceStopSync(child)}catch{}
 try{if(modelServer)modelServer.close()}catch{}
 try{fs.rmSync(temp,{recursive:true,force:true})}catch{}
 try{fs.writeFileSync(path.join(evidence,'results.json'),JSON.stringify({results,timedOut:true,evidence},null,2));fs.writeFileSync(path.join(evidence,'result.txt'),results.map(x=>x.status+' '+x.name).join('\n'))}catch{}
 process.exit(1);
},SUITE_TIMEOUT_MS);
watchdog.unref();
async function freePort(){const s=net.createServer();await new Promise(r=>s.listen(0,'127.0.0.1',r));const p=s.address().port;await new Promise(r=>s.close(r));assert(![8766,3000].includes(p));return p}
async function listenLocal(server){for(let attempt=1;attempt<=8;attempt++){const port=await freePort();try{await new Promise((resolve,reject)=>{const fail=err=>{server.off('error',fail);reject(err)};server.once('error',fail);server.listen(port,'127.0.0.1',()=>{server.off('error',fail);resolve()})});return port}catch(err){if(err.code!=='EADDRINUSE'||attempt===8)throw err}}}
async function api(url,body){assert(new URL(base).port!=='8766');const r=await fetch(base+url,body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json','X-Article-Studio':'1','Origin':base},body:JSON.stringify(body)});const data=await r.json();assert(r.ok,JSON.stringify(data));return data}
async function until(work,timeout=45000){const end=Date.now()+timeout;let last;while(Date.now()<end){try{const value=await work();if(value)return value}catch(e){last=e}await sleep(150)}throw last||new Error('等待条件超时')}
async function page(){const p=await browser.newPage({viewport:{width:1440,height:1080},serviceWorkers:'block'});await p.route('**/*',r=>{if(new URL(r.request().url()).origin===base)return r.continue();fs.appendFileSync(networkLog,JSON.stringify({blockedBrowser:r.request().url()})+'\n');return r.abort()});p.on('response',r=>{if(new URL(r.url()).pathname.startsWith('/api/'))httpCalls.push({method:r.request().method(),path:new URL(r.url()).pathname,status:r.status()})});p.on('pageerror',e=>browserErrors.push(e.message));p.setDefaultTimeout(15000);return p}
async function test(name,work){if(!selected(name))return;const started=Date.now();const p=await page();try{const observation=await work(p);await p.screenshot({path:path.join(evidence,name+'.png'),fullPage:true});results.push({name,status:observation?.status||'passed',ms:Date.now()-started,...observation});console.log((observation?.status==='known_defect_reproduction'?'已知缺陷复现（仅记录） ':'通过 ')+name)}catch(e){await p.screenshot({path:path.join(evidence,name+'-failed.png'),fullPage:true}).catch(()=>{});results.push({name,status:'failed',ms:Date.now()-started,error:e.stack});console.log('失败 '+name+'：'+e.message)}finally{await p.close()}}
async function drop(p,url){await p.goto(base+'/intake.html');await p.locator('#in-input').fill(url);await p.locator('#in-reason').fill('E2E收藏理由');await p.locator('#in-submit').click();await until(async()=>{const rows=await api('/api/intake');return (rows.items||rows.drops||[]).find(x=>x.url===url)});return until(async()=>{const rows=await api('/api/intake');return (rows.items||rows.drops||[]).find(x=>x.url===url&&x.analysis?.status==='done')})}
(async()=>{
 const {chromium}=playwright();
 const piModules=path.join(ROOT,'pi-runtime/node_modules');
 if(!fs.existsSync(piModules)){const official=process.env.ARTICLE_STUDIO_PI_MODULES;if(!official||!fs.existsSync(official))throw new Error('副本缺少 pi-runtime/node_modules，无法启动助手（可设 ARTICLE_STUDIO_PI_MODULES 指向正式仓库的 pi-runtime/node_modules）');fs.symlinkSync(official,piModules,'dir')}
 modelServer=http.createServer(async(req,res)=>{let raw='';for await(const chunk of req)raw+=chunk;try{
 const body=JSON.parse(raw),text=JSON.stringify(body.messages),image=text.includes('image_url');
 const system=(body.messages||[]).filter(m=>m.role==='system').map(m=>JSON.stringify(m.content)).join('');
 const assistant=!image&&!system.includes('即时分析')&&isAssistant(body);
 const kind=image?'vision':system.includes('即时分析')?'analysis':assistant?'assistant':'cards';
 if(kind==='cards'&&text.includes('e2e-scheduled')&&!scheduledCardFailed){scheduledCardFailed=true;modelCalls.push({kind,fixtureFailure:503});res.writeHead(503,{'Content-Type':'application/json'});return res.end(JSON.stringify({error:'E2E 首次卡片提炼失败，留给定时兜底'}));}
 modelCalls.push({kind,stream:!!body.stream});
 if(kind==='vision'&&visionGate&&!visionGate.released){visionGate.started=true;await visionGate.wait;}
 const reply=kind==='assistant'?(forceSaveModel.reply(body)||assistantReply(body)):null;
 const answer=reply?reply.content:image?'图片是蓝色方块，背景为白色。这是 E2E 图片识别夹具。':JSON.stringify(kind==='analysis'?{summary:'E2E灯塔取景：黄昏使用倒影和背影。',points:['水面倒影复制天空','人物只留背影'],why:'构图方法具体，可以整理成拍摄笔记。',angles:[{title:'灯塔取景笔记',how:'整理倒影与背影的组合'}]}:{cards:[{prompt:'蓝色海湾的灯塔，黄昏暖光照亮水面，前景木栏，中景背影，远处渡船灯线，低对比电影画面，测试构图 '+modelCalls.filter(x=>x.kind==='cards').length,model:'qi21',scene:'E2E配图',refs:['e0']}]});
 const toolList=reply?.tools||(reply?.tool?[reply.tool]:null);
 const message=toolList?{role:'assistant',content:null,tool_calls:toolList.map((call,index)=>({id:'call-'+modelCalls.length+'-'+index,type:'function',function:{name:call.name,arguments:JSON.stringify(call.arguments)}}))}:{role:'assistant',content:answer};
 await sleep(kind==='analysis'?1200:kind==='assistant'?0:100);
 const chunk=(delta,finish)=>'data: '+JSON.stringify({id:'e2e',object:'chat.completion.chunk',created:1,model:'e2e-mock',choices:[{index:0,delta,finish_reason:finish}],usage:{prompt_tokens:10,completion_tokens:10,total_tokens:20}})+'\n\n';
 if(body.stream){res.writeHead(200,{'Content-Type':'text/event-stream'});if(message.tool_calls){for(const [index,call] of message.tool_calls.entries())res.write(chunk({tool_calls:[{index,id:call.id,type:'function',function:{name:call.function.name,arguments:call.function.arguments}}]},null));res.write(chunk({},'tool_calls'))}else{res.write(chunk({role:'assistant',content:answer},null));res.write(chunk({},'stop'))}res.end('data: [DONE]\n\n')}
 else{res.setHeader('Content-Type','application/json');res.end(JSON.stringify({model:'e2e-mock',choices:[{message,finish_reason:message.tool_calls?'tool_calls':'stop'}],usage:{prompt_tokens:10,completion_tokens:10,total_tokens:20}}))}
 }catch(e){res.writeHead(500);res.end(JSON.stringify({error:String(e)}))}});
 const modelPort=await listenLocal(modelServer);
 let appPort,startError;
 for(let attempt=1;attempt<=5;attempt++){
  appPort=await freePort();if(appPort===modelPort)continue;
  base='http://127.0.0.1:'+appPort;
  const config=path.join(temp,'model.json');fs.writeFileSync(config,JSON.stringify({model:'e2e-mock',endpoint:'http://127.0.0.1:'+modelPort+'/v1/chat/completions',no_api_key:true,context_tokens:262144,max_output_tokens:2048,temperature:0,timeout_seconds:30}));
  const log=fs.openSync(path.join(evidence,'server.log'),'a');
  child=spawn(path.resolve(ROOT,'../article-importer/.venv/bin/python'),[path.join(__dirname,'server_fixture.py'),'--workspace',workspace,'--port',String(appPort)],{cwd:ROOT,env:{...process.env,E2E_STATUS_SUMMARY:'1',PI_CODING_AGENT_DIR:path.join(temp,'home'),ARTICLE_STUDIO_MODEL_CONFIG:config,E2E_APP_PORT:String(appPort),E2E_MODEL_PORT:String(modelPort),E2E_ALLOWED_PORTS:appPort+','+modelPort,E2E_NETWORK_LOG:networkLog,NODE_OPTIONS:'--require '+path.join(__dirname,'network_guard.cjs')},detached:true,stdio:['ignore',log,log]});fs.closeSync(log);
  try{await until(async()=>{if(child.exitCode!==null)throw new Error('测试工作台启动失败，见 server.log');try{const r=await fetch(base+'/api/intake/analysis-status');return r.ok}catch{return false}},30000);startError=null;break}
  catch(err){startError=err;const died=child.exitCode!==null;await stopChild(child,sleep);child=null;if(!died||attempt===5)throw err}
 }
 if(!child)throw startError||new Error('测试工作台启动失败');
 browser=await chromium.launch({channel:process.env.E2E_BROWSER||'chrome',headless:!process.env.E2E_HEADED,args:['--disable-background-networking','--disable-component-update']});
 await test('01-收下分析与重复链接',async p=>{const url='https://example.com/e2e-main';const first=await drop(p,url);await p.locator('#in-refresh').click();await p.locator('[data-id="'+first.id+'"] .in-analysis-block').first().waitFor();assert.equal(await p.locator('[data-id="'+first.id+'"]').locator('.in-analysis-block').count(),4);const before=modelCalls.length;await p.locator('#in-input').fill(url);await p.locator('#in-submit').click();await until(async()=>{const rows=await api('/api/intake');return (rows.items||[]).filter(x=>x.url===url).length>=2});await p.locator('#in-refresh').click();await p.getByText('沿用已有分析',{exact:false}).first().waitFor();assert.equal(modelCalls.length,before);assert(first.document_id);
 // 延迟浏览器接收重新分析回执，后端真实启动分析，另一按钮走真实 busy 提示。
 await p.route('**/api/intake/*/analyze',async r=>{const reply=await r.fetch();await sleep(800);await r.fulfill({response:reply})});
 const re=p.locator('[data-analyze]').first();await re.click();await p.locator('[data-delete]').first().click();await p.waitForFunction(()=>document.querySelector('#in-status').textContent.includes('上一条还在处理'));await sleep(1500);
 });
 await test('02-删除与重新收下',async p=>{const url='https://example.com/e2e-delete';const item=await drop(p,url);await p.locator('#in-refresh').click();assert((await api('/api/knowledge/search?q='+encodeURIComponent('E2E灯塔'))).results.some(x=>x.id===item.document_id));await until(async()=>{const cards=await api('/api/cards');return (cards.items||cards.cards||[]).some(c=>c.drop_id===item.id)});let dialog='';p.once('dialog',async d=>{dialog=d.message();await d.accept()});await p.locator('[data-id="'+item.id+'"] [data-delete]').click();await until(async()=>!(await api('/api/intake')).items.some(x=>x.id===item.id));assert.match(dialog,/知识库.*正文/);assert.match(dialog,/来源已删除/);const search=await api('/api/knowledge/search?q='+encodeURIComponent('E2E灯塔'));assert(!(search.items||search.results||[]).some(x=>x.id===item.document_id));await p.goto(base+'/knowledge.html');await p.locator('#kb-query').fill('E2E灯塔');const searched=p.waitForResponse(r=>new URL(r.url()).pathname==='/api/knowledge/search');await p.locator('#kb-search-form button').click();await searched;await sleep(100);assert.equal(await p.locator('[data-document="'+item.document_id+'"]').count(),0);await p.screenshot({path:path.join(evidence,'02-知识库检索删除后.png'),fullPage:true});await p.goto(base+'/cards.html');await p.getByText('来源已删除',{exact:false}).first().waitFor();await p.screenshot({path:path.join(evidence,'02-卡片来源已删除.png'),fullPage:true});const next=await drop(p,url);assert.notEqual(next.id,item.id);assert(next.document_id);});
 if(!fs.existsSync(path.join(ROOT,'coordination/intake-combo-vision-report.md'))){if(selected('03-链接图片同条与视觉模型')){results.push({name:'03-链接图片同条与视觉模型',status:'skipped',reason:'Pi intake-combo-vision 尚未提交交付报告；按合同暂不启用'});console.log('跳过 03-链接图片同条与视觉模型：Pi 尚未交付')}} else await test('03-链接图片同条与视觉模型',async p=>{await p.goto(base+'/intake.html');await p.locator('#in-input').fill('https://example.com/e2e-combo');await p.locator('#in-file').setInputFiles({name:'e2e.png',mimeType:'image/png',buffer:Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=','base64')});const before=(await api('/api/intake')).items.length;await p.locator('#in-submit').click();const item=await until(async()=>{const rows=(await api('/api/intake')).items;return rows.find(x=>x.url==='https://example.com/e2e-combo')});await until(async()=>{const row=await api('/api/intake/'+item.id);return row.recognition?.status==='succeeded'&&row.analysis?.status==='done'});assert.equal((await api('/api/intake')).items.length,before+1);const detail=await api('/api/intake/'+item.id);assert(detail.file_path);assert(modelCalls.some(x=>x.kind==='vision'));await p.locator('#in-refresh').click();});
 await api('/api/intel/subscriptions',{fakeid:BIZ,name:'占位公众号'});
 await test('04-合集抓历史状态与真实列表',async p=>{await p.goto(base+'/intel.html');await p.locator('#itl-accounts button').first().click();await p.locator('#itl-album-link').fill(LINK);await p.locator('#itl-discover-album').click();await p.waitForFunction(()=>document.querySelector('#itl-status').textContent.includes('已发现'));const accepted=p.waitForResponse(r=>new URL(r.url()).pathname==='/api/intel/run'&&r.request().method()==='POST');await p.locator('#itl-run-history').click();assert.equal((await accepted).status(),202);await p.waitForFunction(()=>document.querySelector('#itl-run-history').disabled);assert(await p.locator('#itl-run-latest').isDisabled());await p.reload();await p.locator('#itl-accounts button').first().click();await p.waitForFunction(()=>document.querySelector('#itl-run-latest').disabled);assert(await p.locator('#itl-run-latest').isDisabled());await p.screenshot({path:path.join(evidence,'04-刷新进行中.png'),fullPage:true});await p.waitForFunction(()=>!document.querySelector('#itl-run-history').disabled,{},{timeout:30000});await p.locator('.itl-src').first().waitFor();assert(await p.locator('.itl-src[data-source="album"]').count());const sogou=p.locator('.itl-article').filter({has:p.locator('[data-source="sogou"]')});assert.equal(await sogou.count(),1);assert.equal(await sogou.locator('a[href]').count(),0);});
 await test('05-平台分栏与规划能力',async p=>{await p.goto(base+'/intel.html');await p.locator('.itl-platform-group').first().waitFor();const groups=p.locator('.itl-platform-group');assert.equal(await groups.count(),2);const connected=groups.filter({hasText:'已接通'}),planned=groups.filter({hasText:'规划中'});assert.equal(await connected.locator('[data-platform]').count(),4);assert.equal(await planned.locator('[data-platform]').count(),5);assert.equal(await planned.getByRole('button',{name:/抓最新|抓历史/}).count(),0);});
 await test('06-创意提炼真实手动运行',async p=>{const pending=await drop(p,'https://example.com/e2e-scheduled');assert(pending.analysis.cards_error);await p.goto(base+'/schedules.html');await p.locator('#new-task').click();await p.locator('[name="name"]').fill('E2E创意提炼');await p.locator('[name="kind"]').selectOption('intake_cards');await p.locator('[name="body"]').fill('从夹具回收资料提炼候选配图卡，不调用工具。');await p.locator('#task-form button[type="submit"]').click();await p.getByText('E2E创意提炼',{exact:true}).waitFor();const task=(await api('/api/schedules')).tasks.find(x=>x.name==='E2E创意提炼');assert(task);const beforeModel=modelCalls.length;await p.locator('[data-action="run"][data-id="'+task.id+'"]').click();const run=await until(async()=>{const rows=await api('/api/schedules/runs?task_id='+task.id);return rows.runs?.find(x=>x.status!=='running')},45000);assert.equal(run.status,'succeeded');assert(modelCalls.length>beforeModel);assert(JSON.parse(run.output).checked>=1);await p.locator('#run-tab').click();await p.getByText('创意提炼 · 回收项',{exact:false}).first().waitFor();});
 await test('08-本地视觉模型分支',async p=>{await p.goto(base+'/intake.html');await p.locator('#in-file').setInputFiles({name:'e2e-vision.png',mimeType:'image/png',buffer:Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=','base64')});await p.locator('#in-submit').click();const item=await until(async()=>{const rows=(await api('/api/intake')).items;return rows.find(x=>x.file?.name==='e2e-vision.png')});await until(async()=>{const row=await api('/api/intake/'+item.id);return row.recognition?.status==='succeeded'&&row.analysis?.status==='done'});assert(modelCalls.some(x=>x.kind==='vision'));await p.locator('#in-refresh').click();await p.getByText('AI 描述',{exact:false}).first().waitFor();});
 await test('07-版本不一致重启提示',async p=>{await p.route('**/api/intake/analysis-status',r=>r.fulfill({status:404,json:{error:'操作不存在'}}));await p.goto(base+'/intake.html');await p.locator('#in-service').waitFor({state:'visible'});assert.match(await p.locator('#in-service').innerText(),/重启/);});
 await require('./cards_delete.cjs')({test,api,drop,until,base,httpCalls,fixture:JSON.parse(fs.readFileSync(path.join(workspace,'cards-fixture.json'),'utf8')),fs,path,evidence});
 await require('./knowledge_first.cjs')({test,api,until,base,fs,path,evidence,ROOT});
 await require('./combo_edge.cjs')({test,api,until,base,fs,path,evidence,ROOT,sleep});
 await require('./image_suffix.cjs')({test,api,until,base,fs,path,evidence,ROOT});
 await require('./batch_delete.cjs')({test,api,until,base,fs,path,evidence,ROOT,workspace});
 await require('./paste_boundary.cjs')({test,api,until,base,fs,path,evidence});
 await require('./force_and_save.cjs')({test,api,until,base,fs,path,evidence,sleep,modelCalls,holdVision});
 await require('./status_summary.cjs')({test,api,until,base,fs,path,evidence,sleep,workspace});
})().catch(e=>{results.push({name:'启动器',status:'failed',error:e.stack});console.error(e.message)}).finally(async()=>{
 const browserProc=browser&&typeof browser.process==='function'?browser.process():null;
 if(browser)await Promise.race([browser.close().catch(()=>{}),sleep(10000)]);
 if(browserProc&&browserProc.exitCode===null&&browserProc.signalCode===null){try{browserProc.kill('SIGKILL')}catch{}}
 const serverStop=child?await stopChild(child,sleep):{method:'none',notes:[],left:[]};
 if(serverStop.left.length)results.push({name:'子进程清理',status:'failed',error:JSON.stringify(serverStop)});
 if(modelServer)await new Promise(r=>modelServer.close(r));
 const blockedNetwork=fs.readFileSync(networkLog,'utf8');
 if(browserErrors.length||blockedNetwork.trim())results.push({name:'隔离与浏览器错误检查',status:'failed',error:JSON.stringify({browserErrors,blockedNetwork})});
 const summary={results,modelCalls,httpCalls,browserErrors,workspace,base,evidence,browser:browser?browser.version():null,blockedNetwork:fs.readFileSync(networkLog,'utf8'),serverStop};fs.writeFileSync(path.join(evidence,'results.json'),JSON.stringify(summary,null,2));fs.writeFileSync(path.join(evidence,'result.txt'),results.map(x=>x.status+' '+x.name+(x.error?'\n'+x.error:'')).join('\n'));
 fs.rmSync(temp,{recursive:true,force:true});summary.workspaceRemoved=!fs.existsSync(temp);summary.serverExited=!child||(!alive(child.pid)&&!serverStop.left.length);summary.modelServerClosed=!modelServer||!modelServer.listening;if(!summary.workspaceRemoved||!summary.serverExited||!summary.modelServerClosed)results.push({name:'清理检查',status:'failed',error:'临时工作区或进程未清理'});fs.writeFileSync(path.join(evidence,'results.json'),JSON.stringify(summary,null,2));console.log('证据：'+evidence);process.exitCode=results.some(x=>x.status==='failed')?1:0;
 clearTimeout(watchdog);
 process.exit(process.exitCode||0);
});
