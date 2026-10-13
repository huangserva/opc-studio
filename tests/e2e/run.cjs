'use strict';
const fs=require('node:fs'),path=require('node:path'),os=require('node:os'),http=require('node:http'),net=require('node:net'),crypto=require('node:crypto');
const {spawn,execFileSync}=require('node:child_process'),assert=require('node:assert/strict');
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
fs.mkdirSync(evidence,{recursive:true});
// L1 返修：父套件临时目录挪到自测分支之后再创建。自测不跑浏览器、不用工作区，
// 以前会在 $TMPDIR 留下空的 article-studio-e2e-* 目录（自测分支直接 process.exit，不走统一清理）。
let temp=null,workspace=null;
const results=[],modelCalls=[],httpCalls=[],browserErrors=[],networkLog=path.join(evidence,'blocked-network.jsonl');fs.writeFileSync(networkLog,'');
const BIZ='MzB5NTkwMDAwMDE==',LINK='https://mp.weixin.qq.com/s?__biz=MzB5NTkwMDAwMDE%3D%3D&mid=123&idx=1&sn=fixture&chksm=fixture';
let child,browser,modelServer,base,scheduledCardFailed=false,trashChild=null,trashRunId=null,cmChild=null,cmRunId=null;const suiteStartedAt=Date.now();
// M1 返修：第 29 例的 temp/workspace 由父套件预建并持有（连同本轮 runId、子套件 PID 与 spawn 时刻 lstart），
// 兜底回收的归属由这些父进程持有的资源证明，不再靠子套件清单字段之间自洽。
let trashTemp=null,trashTempReal=null,trashWorkspace=null,trashSuiteLstart=null;
const SUITE_TIMEOUT_MS=(()=>{const raw=Number(process.env.E2E_SUITE_TIMEOUT_MS);return Number.isFinite(raw)&&raw>0?raw:900000})();
const RESERVED_PORTS=new Set([8766,3000,18766]);
const watchdog=setTimeout(async()=>{
 const budget=Math.round(SUITE_TIMEOUT_MS/1000);
 console.error('E2E 总超时 '+budget+' 秒');
 results.push({name:'总超时',status:'failed',error:'总超时 '+budget+' 秒，已停止浏览器与临时进程'});
 const cleanup={browserClosed:false,serverStop:{method:'none',notes:[],left:[]},modelServerClosed:false,workspaceRemoved:false,modelServerTimedOut:false};
 try{if(browser)cleanup.browserClosed=await Promise.race([browser.close().then(()=>true).catch(()=>false),sleep(10000).then(()=>false)])}catch{}
 try{if(child)cleanup.serverStop=forceStopSync(child)||cleanup.serverStop}catch{}
 try{if(modelServer){const closed=await closeModelServer();cleanup.modelServerClosed=closed.closed;cleanup.modelServerTimedOut=closed.timedOut}}catch{}
 try{if(trashChild)cleanup.trashTree=await stopTrashTree(3000)}catch{}
 try{if(trashTemp){fs.rmSync(trashTemp,{recursive:true,force:true});cleanup.trashHeldTempRemoved=!fs.existsSync(trashTemp)}}catch{}
 try{fs.rmSync(temp,{recursive:true,force:true});cleanup.workspaceRemoved=!fs.existsSync(temp)}catch{}
 try{fs.writeFileSync(path.join(evidence,'results.json'),JSON.stringify({results,timedOut:true,evidence,cleanup},null,2));fs.writeFileSync(path.join(evidence,'result.txt'),results.map(x=>x.status+' '+x.name).join('\n'))}catch{}
 process.exit(1);
},SUITE_TIMEOUT_MS);
watchdog.unref();
async function freePort(){const s=net.createServer();await new Promise(r=>s.listen(0,'127.0.0.1',r));const p=s.address().port;await new Promise(r=>s.close(r));assert(!RESERVED_PORTS.has(p));return p}
async function listenLocal(server){for(let attempt=1;attempt<=8;attempt++){const port=await freePort();try{await new Promise((resolve,reject)=>{const fail=err=>{server.off('error',fail);reject(err)};server.once('error',fail);server.listen(port,'127.0.0.1',()=>{server.off('error',fail);resolve()})});return port}catch(err){if(err.code!=='EADDRINUSE'||attempt===8)throw err}}}
// 模型响应可能一直不结束（流式/长轮询），server.close() 会等所有活动连接自然断开而无限挂起。
// 清理不能依赖响应自然结束：先销毁套件自己持有的连接，再给监听关闭一个短上限；超时如实返回未关闭，不谎报已停止。
async function closeModelServer(){
 if(!modelServer)return{closed:true,forced:false,timedOut:false};
 if(!modelServer.listening)return{closed:true,forced:false,timedOut:false};
 let forced=false;
 try{if(typeof modelServer.closeAllConnections==='function'){modelServer.closeAllConnections();forced=true}}catch{}
 try{if(typeof modelServer.closeIdleConnections==='function')modelServer.closeIdleConnections()}catch{}
 const closed=await Promise.race([new Promise(r=>modelServer.close(err=>r(!err))),sleep(3000).then(()=>false)]);
 return{closed:closed&&!modelServer.listening,forced,timedOut:!closed};
}
async function api(url,body){assert(!RESERVED_PORTS.has(Number(new URL(base).port)));const r=await fetch(base+url,body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json','X-Article-Studio':'1','Origin':base},body:JSON.stringify(body)});const data=await r.json();assert(r.ok,JSON.stringify(data));return data}
async function until(work,timeout=45000){const end=Date.now()+timeout;let last;while(Date.now()<end){try{const value=await work();if(value)return value}catch(e){last=e}await sleep(150)}throw last||new Error('等待条件超时')}
async function page(){const p=await browser.newPage({viewport:{width:1440,height:1080},serviceWorkers:'block'});await p.route('**/*',r=>{if(new URL(r.request().url()).origin===base)return r.continue();fs.appendFileSync(networkLog,JSON.stringify({blockedBrowser:r.request().url()})+'\n');return r.abort()});p.on('response',r=>{if(new URL(r.url()).pathname.startsWith('/api/'))httpCalls.push({method:r.request().method(),path:new URL(r.url()).pathname,status:r.status()})});p.on('pageerror',e=>browserErrors.push(e.message));p.setDefaultTimeout(15000);return p}
async function test(name,work){if(!selected(name))return;const started=Date.now();const p=await page();try{const observation=await work(p);await p.screenshot({path:path.join(evidence,name+'.png'),fullPage:true});results.push({name,status:observation?.status||'passed',ms:Date.now()-started,...observation});console.log((observation?.status==='known_defect_reproduction'?'已知缺陷复现（仅记录） ':'通过 ')+name)}catch(e){await p.screenshot({path:path.join(evidence,name+'-failed.png'),fullPage:true}).catch(()=>{});results.push({name,status:'failed',ms:Date.now()-started,error:e.stack});console.log('失败 '+name+'：'+e.message)}finally{await p.close()}}
async function drop(p,url){await p.goto(base+'/intake.html');await p.locator('#in-input').fill(url);await p.locator('#in-reason').fill('E2E收藏理由');await p.locator('#in-submit').click();await until(async()=>{const rows=await api('/api/intake');return (rows.items||rows.drops||[]).find(x=>x.url===url)});return until(async()=>{const rows=await api('/api/intake');return (rows.items||rows.drops||[]).find(x=>x.url===url&&x.analysis?.status==='done')})}
// —— M1 清单兜底的归属核对（返修 c：归属由父进程持有，而不是靠清单自洽）——
// 上一版只证明「清单字段之间自洽」，Codex 复核证明这不等价于属于本轮：无关替身只要把真实 PID/lstart
// 填进清单就会被兜底杀掉，目录兜底甚至不校验 runId。本版把归属锚在父进程自己持有的资源上：
// 父套件在启动第 29 项前 mkdtemp 出本轮 temp/workspace，持有 runId、子套件 PID 与其 spawn 时刻的 lstart，
// 目录经 E2E_TRASH_TEMP 交给子套件。兜底先过 manifestOwnership 归属门（runId、temp/tempReal 必须等于
// 父持有目录，suitePid 必须等于本轮子套件 PID 且其 lstart 与父记录一致）；归属门不过 → 停止全部清单兜底
// （不杀、不删），只记录原因。归属门通过后再逐项核对服务身份：命令行按「脚本 + --workspace 本轮 workspace
// + --port 端口」逐段精确比对（不用 includes），可执行段必须是真实存在的 python 解释器，端口不得是生产
// 保留端口，启动时刻 lstart 与登记一致，kill 前再复核一次。目录删除只允许删父套件本轮持有的那个目录，
// 中间符号链接、realpath 与登记不一致等一律拒绝（上一轮已关闭的部分保留）。
function psField(pid,field){
 try{return execFileSync('/bin/ps',['-ww','-p',String(pid),'-o',field+'='],{encoding:'utf8'}).trim()}catch(e){return ''}
}
// 归属门：这份清单描述的是不是本轮资源。证据只取父进程自己持有的东西——本轮 runId、父 mkdtemp 出来的
// temp/tempReal、本轮子套件 PID 与父在 spawn 时记录的 lstart。清单内部自洽不构成归属；任一项不符
// 就停止全部清单兜底。子套件 PID 若还活着，再核一次它的 lstart 与父记录是否一致，防 PID 被复用。
function manifestOwnership(manifest,expected){
 const refuse=(why)=>({ok:false,reason:why});
 if(!expected||!expected.runId)return refuse('父套件未持有本轮 runId');
 if(!expected.temp||!expected.tempReal)return refuse('父套件未持有本轮 temp 目录');
 if(typeof manifest.runId!=='string'||!manifest.runId)return refuse('清单缺 runId');
 if(manifest.runId!==expected.runId)return refuse('清单 runId 与本轮不一致（'+manifest.runId+' ≠ '+expected.runId+'）');
 if(typeof manifest.temp!=='string'||manifest.temp!==expected.temp)return refuse('清单 temp 不是父套件本轮持有的目录（'+manifest.temp+' ≠ '+expected.temp+'）');
 if(typeof manifest.tempReal!=='string'||manifest.tempReal!==expected.tempReal)return refuse('清单 tempReal 与父套件登记的 realpath 不一致（'+manifest.tempReal+' ≠ '+expected.tempReal+'）');
 if(!Number.isInteger(manifest.suitePid)||manifest.suitePid!==expected.suitePid)return refuse('清单 suitePid 与本轮子套件 PID 不一致（'+manifest.suitePid+' ≠ '+expected.suitePid+'）');
 if(alive(manifest.suitePid)&&(!expected.suiteLstart||psField(manifest.suitePid,'lstart')!==expected.suiteLstart))return refuse('本轮子套件 PID 的 lstart 与父套件 spawn 时记录不一致（PID 可能已被复用）');
 return {ok:true};
}
// 服务命令行核对：清单登记的 serviceArgv（不含可执行文件）必须逐段等于
// [本轮 fixture 绝对路径, --workspace, 父持有的本轮 workspace, --port, 端口]，且 ps 命令行正好是
// 「一个可执行文件段 + 这些参数」。不用 includes：把 fixture 路径当普通参数塞进别的命令行、
// 参数顺序不同、多一个参数、workspace 不是本轮那个，都不匹配。
// 可执行段按「存在 + 是文件 + 可执行 + 名字是 python」核对：macOS 的 framework Python 会把 argv[0]
// 改写成 …/Python.app/Contents/MacOS/Python，与 venv 里的 python 路径逐字节不同（实测 inode 也不同），
// 无法逐字节比对；真正的归属由父持有的脚本路径、本轮 workspace、runId 与 suitePid 建立。
function serviceCommandMatches(pid,manifest,expected){
 const cmd=psField(pid,'command');
 if(!cmd)return{ok:false,why:'读不到命令行'};
 const port=manifest.servicePort;
 if(!Number.isInteger(port)||port<1||port>65535)return{ok:false,why:'清单 servicePort 不是合法端口'};
 if(RESERVED_PORTS.has(port))return{ok:false,why:'servicePort '+port+' 是生产保留端口'};
 const argv=manifest.serviceArgv;
 if(!Array.isArray(argv)||!argv.every(item=>typeof item==='string'&&item&&!/\s/.test(item)))return{ok:false,why:'清单 serviceArgv 不是无空白的字符串数组'};
 const want=[expected.fixturePath,'--workspace',expected.workspace,'--port',String(port)];
 if(argv.length!==want.length||argv.some((item,index)=>item!==want[index]))return{ok:false,why:'清单 serviceArgv 与父持有脚本/本轮 workspace 不逐段相符'};
 const tail=want.join(' ');
 if(!cmd.endsWith(' '+tail))return{ok:false,why:'实际命令行与登记参数不逐段相符'};
 const exe=cmd.slice(0,cmd.length-tail.length-1);
 if(/\s/.test(exe))return{ok:false,why:'可执行文件段含空白，无法可靠核对'};
 let st;try{st=fs.statSync(exe)}catch(e){return{ok:false,why:'可执行文件 '+exe+' 读不到（'+e.code+'）'}}
 if(!st.isFile()||!(st.mode&0o111))return{ok:false,why:exe+' 不是可执行文件'};
 if(!/^python/i.test(path.basename(exe)))return{ok:false,why:'可执行文件不是 python 解释器：'+path.basename(exe)};
 return{ok:true};
}
// expected 必须是父套件持有的预期值（fixture 脚本绝对路径 + 本轮 workspace）；没有预期值就拒杀，
// 不再退回「清单自己说自己一致」的旧口径。
function manifestServiceFallback(manifest,expectedRunId,record,expected){
 const missing=['runId','servicePid','serviceLstart','fixturePath','workspace'].filter(k=>manifest[k]===undefined||manifest[k]===null||manifest[k]==='');
 if(missing.length){record.serviceRefused='清单缺少身份字段：'+missing.join(',');return}
 if(manifest.runId!==expectedRunId){record.serviceRefused='清单 runId 与本轮不一致（'+manifest.runId+' ≠ '+expectedRunId+'），拒杀';return}
 const pid=manifest.servicePid;
 if(!Number.isInteger(pid)||pid<=0){record.serviceRefused='servicePid 非正整数，拒杀';return}
 if(!expected||!expected.fixturePath||!expected.workspace){record.serviceRefused='缺少父套件持有的预期脚本路径/本轮 workspace，拒杀';return}
 const identity=()=>{
  const cmd=serviceCommandMatches(pid,manifest,expected);
  if(!cmd.ok)return cmd;
  if(typeof manifest.serviceLstart!=='string'||!manifest.serviceLstart)return{ok:false,why:'清单缺 serviceLstart'};
  if(psField(pid,'lstart')!==manifest.serviceLstart)return{ok:false,why:'lstart 与登记不一致'};
  return{ok:true};
 };
 const first=identity();
 if(!first.ok){record.serviceRefused='身份核对不一致（'+first.why+'），拒杀';return}
 // kill 前一刻再核一次身份，缩小 ps 与 kill 之间的竞态窗口
 const second=identity();
 if(!second.ok){record.serviceRefused='kill 前一刻身份复核不一致（'+second.why+'），拒杀';return}
 try{process.kill(-pid,'SIGKILL');record.serviceKilled=true}catch(e){record.serviceGroupKillError=e.code||String(e)}
 try{process.kill(pid,'SIGKILL')}catch(e){}
}
// 目录兜底只接受父套件本轮持有的那个目录：ownership（{runId,temp,tempReal}）由 manifestOwnership 的
// 同一份父持有值给出。没有 ownership 的旧调用方式一律拒删——清单自洽不能证明目录属于本轮。
// 其余核对保留上一轮已关闭的部分：规范化绝对路径、必须在真实 tmpdir 下、前缀限定、逐段 lstat
// 拒绝任何中间符号链接、realpath 与登记一致。
function manifestTempFallback(manifest,record,tempPrefixes=['kd2-e2e-'],ownership=null){
 const refuse=(why)=>{record.tempRefused=why};
 if(!ownership||!ownership.runId||!ownership.temp||!ownership.tempReal)return refuse('缺少父套件持有的本轮目录归属（expectedRunId/expectedTemp），拒删');
 if(manifest.runId!==ownership.runId)return refuse('清单 runId 与本轮不一致（'+manifest.runId+' ≠ '+ownership.runId+'），拒删');
 if(typeof manifest.temp!=='string'||manifest.temp!==ownership.temp)return refuse('清单 temp 不是父套件本轮持有的目录（'+manifest.temp+' ≠ '+ownership.temp+'），拒删');
 if(typeof manifest.tempReal!=='string'||manifest.tempReal!==ownership.tempReal)return refuse('清单 tempReal 与父套件登记的 realpath 不一致，拒删');
 if(typeof manifest.temp!=='string'||!manifest.temp)return refuse('清单缺 temp');
 if(typeof manifest.tempReal!=='string'||!manifest.tempReal)return refuse('清单缺 tempReal');
 if(manifest.temp!==path.resolve(manifest.temp))return refuse('temp 不是规范化绝对路径，拒删');
 const tmp=os.tmpdir();
 if(!(manifest.temp===tmp||manifest.temp.startsWith(tmp+path.sep)))return refuse('temp 不在 os.tmpdir() 下，拒删');
 if(!tempPrefixes.some(prefix=>path.basename(manifest.temp).startsWith(prefix)))return refuse('temp basename 不以登记前缀（'+tempPrefixes.join('/')+'）开头，拒删');
 if(!fs.existsSync(manifest.temp))return refuse('temp 已不存在（子套件可能已自清）');
 const tail=path.relative(tmp,manifest.temp);
 if(!tail||tail==='..'||tail.startsWith('..'+path.sep))return refuse('temp 相对段异常，拒删');
 let base;try{base=fs.realpathSync(tmp)}catch(e){return refuse('realpath(tmpdir) 失败：'+e.code)}
 // 从真实 tmpdir 起逐段 lstat：任何中间段是符号链接都拒绝删除（macOS /var→/private/var 已由 realpath(tmpdir) 归一化）
 let cur=base;
 for(const part of tail.split(path.sep)){
  cur=path.join(cur,part);
  let st;try{st=fs.lstatSync(cur)}catch(e){return refuse('无法核对 '+cur+'：'+e.code)}
  if(st.isSymbolicLink())return refuse('中间存在符号链接：'+cur+'，拒删');
 }
 let real;try{real=fs.realpathSync(manifest.temp)}catch(e){return refuse('realpath(temp) 失败：'+e.code)}
 if(real!==base&&!real.startsWith(base+path.sep))return refuse('realpath 不在真实 tmpdir 下，拒删');
 if(!tempPrefixes.some(prefix=>path.basename(real).startsWith(prefix)))return refuse('realpath basename 不以登记前缀（'+tempPrefixes.join('/')+'）开头，拒删');
 let registered;try{registered=fs.realpathSync(manifest.tempReal)}catch(e){return refuse('清单 tempReal 无法 realpath，拒删')}
 if(real!==registered)return refuse('realpath 与清单登记不一致：'+real+' ≠ '+registered+'，拒删');
 try{fs.rmSync(real,{recursive:true,force:true})}catch(e){}
 record.workspaceRemoved=!fs.existsSync(real);
 record.workspacePath=real;
}
// 终止第 29 例子套件进程树：先对组发 SIGTERM（子套件自带处理器，会回收它的 detached
// 服务、浏览器与临时目录并落盘结果），留宽限后升级 SIGKILL；再按子套件登记的清单兜底回收，
// 但先过 manifestOwnership 归属门——runId/temp/tempReal/suitePid 必须由父套件本轮持有；
// 归属门不过就停止全部清单兜底（不杀、不删）并记录原因，归属门通过后再逐项核对服务身份与目录。
// 最后无条件回收父套件自己 mkdtemp 出来的那个目录：它由本轮创建，归属无需清单证明。
async function stopTrashTree(graceMs=5000){
 const record={groupTerm:false,groupKill:false,manifestFallback:null,heldTempPath:trashTemp,heldTempRemoved:false};
 if(!trashChild)return record;
 const suitePid=trashChild.pid;
 try{process.kill(-suitePid,'SIGTERM');record.groupTerm=true}catch{}
 try{trashChild.kill('SIGTERM')}catch{}
 await sleep(graceMs);
 if(alive(suitePid)){
  try{process.kill(-suitePid,'SIGKILL');record.groupKill=true}catch{}
  try{trashChild.kill('SIGKILL')}catch{}
 }
 try{
  const manifest=JSON.parse(fs.readFileSync(path.join(evidence,'knowledge_trash_manifest.json'),'utf8'));
  record.manifestFallback={servicePid:manifest.servicePid||null,serviceKilled:false,workspaceRemoved:false};
  const ownership=manifestOwnership(manifest,{runId:trashRunId,temp:trashTemp,tempReal:trashTempReal,suitePid,suiteLstart:trashSuiteLstart});
  if(!ownership.ok){
   record.manifestFallback.ownershipRefused=ownership.reason+'，停止全部清单兜底（不杀、不删）';
  }else{
   manifestServiceFallback(manifest,trashRunId,record.manifestFallback,{fixturePath:path.join(__dirname,'server_fixture.py'),workspace:trashWorkspace});
   manifestTempFallback(manifest,record.manifestFallback,['kd2-e2e-'],{runId:trashRunId,temp:trashTemp,tempReal:trashTempReal});
  }
 }catch(e){record.manifestFallback={error:'清单读取/解析失败：'+(e&&e.message?e.message:e)}}
 if(trashTemp){
  try{fs.rmSync(trashTemp,{recursive:true,force:true})}catch(e){record.heldTempError=e.code||String(e)}
  record.heldTempRemoved=!fs.existsSync(trashTemp);
 }
 return record;
}
// M1 自测（沿用 Codex 复核报告的替身思路，并把它本轮给出的两个反例补进来自测）：
// E2E_TRASH_SELFTEST=1 时不跑整套，只验证清单兜底的归属核对。替身进程与目录全部由自测自己
// 启动/创建，结束一律回收。调用的是生产测试文件里的同一批校验函数，不是另写的模拟实现。
// 负例1 伪造启动时刻 → 拒杀；正控1 身份齐全（精确 argv + 真实 lstart + 父持有 workspace）→ 回收；
// 负例2 中间符号链接目录 → 拒删、目标保留；负例3 登记 tempReal 与实际 realpath 不一致 → 拒删；
// 正控2 本轮持有目录 → 可删；负例4 sameNameForeign：与本轮无关的替身，清单字段完全自洽（真实 PID/
// lstart/参数）但脚本与 workspace 不是父持有的 → 不杀；负例5 wrongRunDirectory：runId 不符 →
// 停止全部清单兜底（服务不杀、目录保留）；负例6 runId 对但 temp 指向他人目录 → 拒删；
// 负例7 旧调用方式（不传 ownership）→ 拒删；负例8 命令行多一个参数（不逐段相符）→ 拒杀；
// 负例9 同一脚本但 workspace 不是本轮那个 → 拒杀。
async function trashSelfTest(){
 const failures=[];const checked=[];const dirs=[];const spawned=[];
 const report=(label,ok,detail)=>{checked.push(label);console.log((ok?'通过 ':'失败 ')+label+(ok?'':'：'+String(detail||'')));if(!ok)failures.push(label+'：'+String(detail||''))};
 const fixturePath=path.join(__dirname,'server_fixture.py');
 const python=path.resolve(ROOT,'../article-importer/.venv/bin/python');
 if(!fs.existsSync(python)){console.log('跳过自测：找不到副本使用的 python 解释器 '+python);return ['缺少 python：'+python]}
 // 本轮替身：真实 python 解释器 + 与产品副本工作台同形状的精确参数（脚本 --workspace 目录 --port 端口），
 // 用来验证「身份齐全且属于本轮的正控确实会被兜底回收」，避免只验负例。
 const heldTemp=fs.mkdtempSync(path.join(os.tmpdir(),'kd2-e2e-selftest-'));dirs.push(heldTemp);
 const heldWorkspace=path.join(heldTemp,'workspace');fs.mkdirSync(heldWorkspace);
 const heldReal=fs.realpathSync(heldTemp);
 const loopPy=path.join(heldTemp,'sleeper.py');fs.writeFileSync(loopPy,'import time\ntime.sleep(600)\n');
 const port=await freePort();
 const argv=[loopPy,'--workspace',heldWorkspace,'--port',String(port)];
 const service=spawn(python,argv,{detached:true,stdio:'ignore'});spawned.push(service);
 const suite=spawn(process.execPath,['-e','setTimeout(()=>{},600000)'],{detached:true,stdio:'ignore'});spawned.push(suite);
 await sleep(500);
 const suiteLstart=psField(suite.pid,'lstart');
 const ownership={runId:'st',temp:heldTemp,tempReal:heldReal};
 const expected={fixturePath:loopPy,workspace:heldWorkspace};
 const serviceManifest=(over={})=>({runId:'st',servicePid:service.pid,serviceLstart:psField(service.pid,'lstart'),fixturePath:loopPy,workspace:heldWorkspace,serviceArgv:argv,servicePort:port,temp:heldTemp,tempReal:heldReal,suitePid:suite.pid,...over});
 const spawnSleeper=(args)=>{const p=spawn(python,args,{detached:true,stdio:'ignore'});spawned.push(p);return p};
 // 负例1：命令行完全匹配本轮参数，但清单登记的启动时刻是伪造的 → 必须拒杀、进程存活
 const rec1={};
 manifestServiceFallback(serviceManifest({serviceLstart:'Mon Oct  1 00:00:00 2020'}),'st',rec1,expected);
 report('负例1 伪造启动时刻拒杀',!rec1.serviceKilled&&alive(service.pid)&&/拒杀/.test(rec1.serviceRefused||''),rec1.serviceRefused||JSON.stringify(rec1));
 // 负例8：同一解释器与脚本，但命令行多一个参数（不是逐段相符）→ 必须拒杀（证明不是 includes 式宽松匹配）
 const extraArgs=[loopPy,'--workspace',heldWorkspace,'--port',String(port),'--extra','x'];
 const extra=spawnSleeper(extraArgs);
 await sleep(300);
 const rec8={};
 manifestServiceFallback(serviceManifest({serviceArgv:extraArgs,servicePid:extra.pid,serviceLstart:psField(extra.pid,'lstart')}),'st',rec8,expected);
 report('负例8 参数不逐段相符拒杀',!rec8.serviceKilled&&alive(extra.pid)&&/拒杀/.test(rec8.serviceRefused||''),rec8.serviceRefused||JSON.stringify(rec8));
 // 负例9：同一脚本、同样参数形状，但 --workspace 不是父持有的本轮那个 → 必须拒杀
 const foreignWs=fs.mkdtempSync(path.join(os.tmpdir(),'kd2-e2e-selftest-foreign-ws-'));dirs.push(foreignWs);
 const foreignArgs=[loopPy,'--workspace',foreignWs,'--port',String(port)];
 const foreignService=spawnSleeper(foreignArgs);
 await sleep(300);
 const rec9={};
 manifestServiceFallback(serviceManifest({serviceArgv:foreignArgs,workspace:foreignWs,servicePid:foreignService.pid,serviceLstart:psField(foreignService.pid,'lstart')}),'st',rec9,expected);
 report('负例9 workspace 非本轮持有拒杀',!rec9.serviceKilled&&alive(foreignService.pid)&&/拒杀/.test(rec9.serviceRefused||''),rec9.serviceRefused||JSON.stringify(rec9));
 // 负例4 sameNameForeign：与本轮无关的替身（node 进程，命令行里塞着真实 fixture 路径与自建 workspace），
 // 清单字段完全自洽（真实 PID、真实 lstart、真实参数）→ 仍必须拒杀。这就是复核报告的同名替身。
 const foreignNode=spawn(process.execPath,['-e','setInterval(()=>{},1000)',fixturePath,foreignWs],{detached:true,stdio:'ignore'});spawned.push(foreignNode);
 await sleep(300);
 const rec4={};
 manifestServiceFallback({runId:'st',servicePid:foreignNode.pid,serviceLstart:psField(foreignNode.pid,'lstart'),fixturePath,workspace:foreignWs,serviceArgv:['-e','setInterval(()=>{},1000)',fixturePath,foreignWs],servicePort:port,temp:heldTemp,tempReal:heldReal,suitePid:suite.pid},'st',rec4,expected);
 report('负例4 sameNameForeign（自洽但非本轮）不杀',!rec4.serviceKilled&&alive(foreignNode.pid)&&/拒杀/.test(rec4.serviceRefused||''),rec4.serviceRefused||JSON.stringify(rec4));
 // 负例5 wrongRunDirectory：清单 runId 是别一轮的 → 归属门不过，停止全部清单兜底（服务不杀、目录保留）
 const foreignDir=fs.mkdtempSync(path.join(os.tmpdir(),'kd2-e2e-selftest-foreign-dir-'));dirs.push(foreignDir);
 const wrongManifest=serviceManifest({runId:'WRONG',temp:foreignDir,tempReal:fs.realpathSync(foreignDir)});
 const gateWrong=manifestOwnership(wrongManifest,{runId:'st',temp:heldTemp,tempReal:heldReal,suitePid:suite.pid,suiteLstart});
 const rec5={serviceKilled:false,workspaceRemoved:false};
 if(!gateWrong.ok)rec5.ownershipRefused=gateWrong.reason;else{manifestServiceFallback(wrongManifest,'st',rec5,expected);manifestTempFallback(wrongManifest,rec5,['kd2-e2e-'],ownership)}
 report('负例5 wrongRunDirectory 停止兜底',!gateWrong.ok&&!rec5.serviceKilled&&!rec5.workspaceRemoved&&alive(service.pid)&&fs.existsSync(foreignDir)&&/runId/.test(gateWrong.reason||''),JSON.stringify({gate:gateWrong,rec5}));
 // 正控1：身份齐全（精确 argv + 真实 lstart + 父持有 workspace + 本轮 runId/suitePid）→ 兜底应回收
 const rec2={};
 manifestServiceFallback(serviceManifest(),'st',rec2,expected);
 await sleep(400);
 report('正控1 本轮服务可回收',rec2.serviceKilled&&!alive(service.pid),rec2.serviceRefused||JSON.stringify(rec2));
 // 负例2：中间符号链接目录（链接段把 claimed 指到 tmpdir 外的真实目录）→ 必须拒删、目标保留
 const outside=fs.mkdtempSync(path.join(os.tmpdir(),'kd2-e2e-selftest-outside-'));dirs.push(outside);
 const canary=path.join(outside,'kd2-e2e-selftest-canary');fs.mkdirSync(canary);
 const linkHome=fs.mkdtempSync(path.join(os.tmpdir(),'kd2-e2e-selftest-link-'));dirs.push(linkHome);
 fs.symlinkSync(outside,path.join(linkHome,'parent-link'),'dir');
 const claimed=path.join(linkHome,'parent-link','kd2-e2e-selftest-canary');
 const rec3={};
 manifestTempFallback({runId:'st',temp:claimed,tempReal:fs.realpathSync(claimed)},rec3,['kd2-e2e-'],{runId:'st',temp:claimed,tempReal:fs.realpathSync(claimed)});
 report('负例2 符号链接目录拒删',!rec3.workspaceRemoved&&fs.existsSync(canary)&&/符号链接/.test(rec3.tempRefused||''),rec3.tempRefused||JSON.stringify(rec3));
 // 负例3：登记 tempReal 与实际 realpath 不一致（清单与 ownership 都写 dirB，实际目录是 dirA）→ 必须拒删
 const dirA=fs.mkdtempSync(path.join(os.tmpdir(),'kd2-e2e-selftest-real-'));dirs.push(dirA);
 const dirB=fs.mkdtempSync(path.join(os.tmpdir(),'kd2-e2e-selftest-registered-'));dirs.push(dirB);
 const recA={};
 manifestTempFallback({runId:'st',temp:dirA,tempReal:fs.realpathSync(dirB)},recA,['kd2-e2e-'],{runId:'st',temp:dirA,tempReal:fs.realpathSync(dirB)});
 report('负例3 登记 realpath 不一致拒删',!recA.workspaceRemoved&&fs.existsSync(dirA)&&/不一致/.test(recA.tempRefused||''),recA.tempRefused||JSON.stringify(recA));
 // 负例6：runId 对，但 temp 指向别的（他人的）kd2-e2e-* 目录 → 拒删、目录保留
 const rec6={};
 manifestTempFallback({runId:'st',temp:foreignDir,tempReal:fs.realpathSync(foreignDir)},rec6,['kd2-e2e-'],ownership);
 report('负例6 temp 非父持有拒删',!rec6.workspaceRemoved&&fs.existsSync(foreignDir)&&/不是父套件本轮持有/.test(rec6.tempRefused||''),rec6.tempRefused||JSON.stringify(rec6));
 // 负例7：旧调用方式（不传 ownership）→ 一律拒删，清单自洽不能证明归属
 const rec7={};
 manifestTempFallback({runId:'st',temp:foreignDir,tempReal:fs.realpathSync(foreignDir)},rec7);
 report('负例7 缺 ownership 拒删',!rec7.workspaceRemoved&&fs.existsSync(foreignDir)&&/缺少父套件持有/.test(rec7.tempRefused||''),rec7.tempRefused||JSON.stringify(rec7));
 // 正控2：temp 就是父套件本轮持有的目录、登记一致 → 可删
 const recB={};
 manifestTempFallback({runId:'st',temp:heldTemp,tempReal:heldReal},recB,['kd2-e2e-'],ownership);
 report('正控2 本轮持有目录可删',recB.workspaceRemoved&&!fs.existsSync(heldTemp),recB.tempRefused||JSON.stringify(recB));
 // 收尾：自己启动的进程与目录全部回收，并如实核对没有残留
 for(const p of spawned){try{process.kill(-p.pid,'SIGKILL')}catch(e){}try{p.kill('SIGKILL')}catch(e){}}
 await sleep(400);
 const left=spawned.filter(p=>alive(p.pid)).map(p=>p.pid);
 report('收尾 替身进程全部回收',!left.length,'仍存活：'+left.join(','));
 for(const dir of dirs){try{fs.rmSync(dir,{recursive:true,force:true})}catch(e){}}
 const leftDirs=dirs.filter(dir=>fs.existsSync(dir));
 report('收尾 自测目录全部回收',!leftDirs.length,'仍存在：'+leftDirs.join(','));
 console.log('清单兜底自测：'+(checked.length-failures.length)+'/'+checked.length+' 通过'+(failures.length?'，失败 '+failures.length+' 项':''));
 // 用例数哨兵：少了就是有用例被跳过（提前 return/抛错），不能算全过。
 if(checked.length<13)failures.push('自测用例数不足：只跑到 '+checked.length+' 项，应至少 13 项');
 return failures;
}
// 登记 knowledge_trash.cjs（自带服务与浏览器的独立联调套件）为整套第 29 例：
// 它按自身 IIFE 运行并 process.exit，因此以子进程方式接入，不 require（否则会终结整套）。
// 预算按整套剩余额度封顶，超时按进程组终止，避免残留 Chrome/服务并拖过总超时。
// M1 返修：启动子套件前，父套件先 mkdtemp 出本轮 temp/workspace/home 并持有其路径与 realpath，
// 经 E2E_TRASH_TEMP 交给子套件；同时记下本轮 runId、子套件 PID 与其 spawn 时刻的 lstart。
// 这样兜底回收能问「是不是本轮的」，答案来自父进程自己持有的资源，而不是子套件清单自不自洽。
async function runTrashSubsuite(){
 const name='29-知识库软删除隔离联调';
 if(!selected(name))return;
 const started=Date.now();
 const logFile=path.join(evidence,'knowledge_trash.log');
 const out=fs.openSync(logFile,'a');
 // 超时探针可用 E2E_TRASH_BUDGET_MS 单独压低本子套件预算（毫秒，不动的整套看门狗；显式设置时不受 20s 下限约束），默认按整套剩余额度封顶。
 const probeBudget=Number(process.env.E2E_TRASH_BUDGET_MS);
 const budget=Number.isFinite(probeBudget)&&probeBudget>0?probeBudget:Math.max(SUITE_TIMEOUT_MS-(Date.now()-suiteStartedAt)-5000,20000);
 // 本轮随机 runId 一并传给子套件：清单兜底回收前必须核对 runId 一致，防止旧轮清单错指。
 trashRunId=crypto.randomBytes(6).toString('hex');
 // 父套件只建本轮那个空 temp 并持有它（workspace/home 由子套件在里面建，所以这里不能预先填东西，
 // 否则子套件会认为该目录不是本轮新开的而回退自建）；trashWorkspace 只作为预期路径字符串参与核对。
 trashTemp=fs.mkdtempSync(path.join(os.tmpdir(),'kd2-e2e-'));
 trashTempReal=fs.realpathSync(trashTemp);
 trashWorkspace=path.join(trashTemp,'workspace');
 trashChild=spawn(process.execPath,[path.join(__dirname,'knowledge_trash.cjs')],{cwd:ROOT,env:{...process.env,E2E_TRASH_MANIFEST:path.join(evidence,'knowledge_trash_manifest.json'),E2E_TRASH_RUN_ID:trashRunId,E2E_TRASH_TEMP:trashTemp},detached:true,stdio:['ignore',out,out]});
 // 子套件自己的身份由父在 spawn 后立即记录：归属门会用它核对清单里的 suitePid，防 PID 被复用。
 trashSuiteLstart=psField(trashChild.pid,'lstart');
 fs.closeSync(out);
 const exit=await new Promise(resolve=>{
  const timer=setTimeout(()=>resolve({timedOut:true}),budget);
  trashChild.once('exit',(code,signal)=>{clearTimeout(timer);resolve({code,signal})});
  trashChild.once('error',err=>{clearTimeout(timer);resolve({error:String(err)})});
 });
 let trashCleanup=null;
 if(exit.timedOut||exit.signal){
  // 超时走组 SIGTERM→SIGKILL；被信号打死（如外部 kill）时组里可能还有残留，同样按树回收。
  trashCleanup=await stopTrashTree();
 }
 trashChild=null;
 if(exit.code===0){results.push({name,status:'passed',ms:Date.now()-started,log:logFile});console.log('通过 '+name);}
 else{const reason=exit.timedOut?('子套件超时 '+Math.round(budget/1000)+' 秒'):(exit.error||('退出码 '+exit.code));results.push({name,status:'failed',ms:Date.now()-started,error:reason+'，日志 '+logFile,...(trashCleanup?{cleanup:trashCleanup}:{})});console.log('失败 '+name+'：'+reason);}
}
// 终止第 30 例子套件进程树：与第 29 例同一规范——先对组发 SIGTERM（子套件自带处理器，
// 会回收它的 detached 服务、浏览器与临时目录并落盘结果），留宽限后升级 SIGKILL；最后按
// 子套件登记的清单兜底回收——只处理身份核对全部通过的本轮服务与临时目录（runId、启动时刻、
// fixture/workspace 绝对路径、realpath 一致，temp 前缀限定 studio-markdown-e2e-），
// 核对不过就拒杀拒删并记录，不按端口/名称误杀他人。
async function stopContentMarkdownTree(graceMs=5000){
 const record={groupTerm:false,groupKill:false,manifestFallback:null};
 if(!cmChild)return record;
 try{process.kill(-cmChild.pid,'SIGTERM');record.groupTerm=true}catch{}
 try{cmChild.kill('SIGTERM')}catch{}
 await sleep(graceMs);
 if(alive(cmChild.pid)){
  try{process.kill(-cmChild.pid,'SIGKILL');record.groupKill=true}catch{}
  try{cmChild.kill('SIGKILL')}catch{}
 }
 try{
  const manifest=JSON.parse(fs.readFileSync(path.join(evidence,'content_markdown_manifest.json'),'utf8'));
  record.manifestFallback={servicePid:manifest.servicePid||null,serviceKilled:false,workspaceRemoved:false};
  // 第 30 例的 temp 目前仍由子套件自己创建（父套件没预建持有，也没传 E2E_CM_TEMP），而新的
  // manifestServiceFallback/manifestTempFallback 都要求父持有的预期值，所以这里两条兜底都会
  // 如实拒杀拒删并记录原因（fail closed：宁可不回收，也不拿清单自洽当归属）。
  // #30 在 markdown 新底座上重做时，应照 29 的做法让父套件预建 temp 并传下去，届时兜底自动恢复。
  manifestServiceFallback(manifest,cmRunId,record.manifestFallback);
  manifestTempFallback(manifest,record.manifestFallback,['studio-markdown-e2e-']);
 }catch(e){record.manifestFallback={error:'清单读取/解析失败：'+(e&&e.message?e.message:e)}}
 return record;
}
// 登记 content_markdown.cjs（自带服务与浏览器的独立联调套件）为整套第 30 例，接入规范与
// 第 29 例相同：它按自身 IIFE 运行并 process.exit，因此以子进程方式接入，不 require（否则会
// 终结整套）。本轮随机 runId 经 E2E_CM_RUN_ID 传入、清单经 E2E_CM_MANIFEST 落盘；预算按
// 整套剩余额度封顶，超时按进程组终止，避免残留 Chrome/服务并拖过总超时；清单兜底回收前
// 必须核对 runId 与身份一致，只回收自己启动的服务与临时目录。
async function runContentMarkdownSubsuite(){
 const name='30-正文 Markdown 重排与助手改稿';
 if(!selected(name))return;
 const started=Date.now();
 const logFile=path.join(evidence,'content_markdown.log');
 const out=fs.openSync(logFile,'a');
 // 超时探针可用 E2E_CM_BUDGET_MS 单独压低本子套件预算（毫秒，不动的整套看门狗；显式设置时不受 20s 下限约束），默认按整套剩余额度封顶。
 const probeBudget=Number(process.env.E2E_CM_BUDGET_MS);
 const budget=Number.isFinite(probeBudget)&&probeBudget>0?probeBudget:Math.max(SUITE_TIMEOUT_MS-(Date.now()-suiteStartedAt)-5000,20000);
 cmRunId=crypto.randomBytes(6).toString('hex');
 cmChild=spawn(process.execPath,[path.join(__dirname,'content_markdown.cjs')],{cwd:ROOT,env:{...process.env,E2E_CM_MANIFEST:path.join(evidence,'content_markdown_manifest.json'),E2E_CM_RUN_ID:cmRunId},detached:true,stdio:['ignore',out,out]});
 fs.closeSync(out);
 const exit=await new Promise(resolve=>{
  const timer=setTimeout(()=>resolve({timedOut:true}),budget);
  cmChild.once('exit',(code,signal)=>{clearTimeout(timer);resolve({code,signal})});
  cmChild.once('error',err=>{clearTimeout(timer);resolve({error:String(err)})});
 });
 let cmCleanup=null;
 if(exit.timedOut||exit.signal){
  // 超时走组 SIGTERM→SIGKILL；被信号打死（如外部 kill）时组里可能还有残留，同样按树回收。
  cmCleanup=await stopContentMarkdownTree();
 }
 cmChild=null;
 if(exit.code===0){results.push({name,status:'passed',ms:Date.now()-started,log:logFile});console.log('通过 '+name);}
 else{const reason=exit.timedOut?('子套件超时 '+Math.round(budget/1000)+' 秒'):(exit.error||('退出码 '+exit.code));results.push({name,status:'failed',ms:Date.now()-started,error:reason+'，日志 '+logFile,...(cmCleanup?{cleanup:cmCleanup}:{})});console.log('失败 '+name+'：'+reason);}
}
(async()=>{
 if(process.env.E2E_TRASH_SELFTEST==='1'){
  const failures=await trashSelfTest();
  // L1 返修：自测不跑浏览器、不产生证据，本轮刚建的证据目录里只有一个空的 blocked-network.jsonl，
  // 退出前一并回收（父套件 temp 已改到自测分支之后创建，本来就不会为自测建）。
  try{fs.rmSync(evidence,{recursive:true,force:true})}catch(e){}
  process.exit(failures.length?1:0);
 }
 // L1 返修：父套件临时目录在自测分支之后才创建，自测不再留下空的 article-studio-e2e-* 目录。
 temp=fs.mkdtempSync(path.join(os.tmpdir(),'article-studio-e2e-'));
 workspace=path.join(temp,'workspace');fs.mkdirSync(workspace);fs.mkdirSync(path.join(temp,'home'));
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
 await runTrashSubsuite();
 await runContentMarkdownSubsuite();
})().catch(e=>{results.push({name:'启动器',status:'failed',error:e.stack});console.error(e.message)}).finally(async()=>{
 const browserProc=browser&&typeof browser.process==='function'?browser.process():null;
 const browserClosed=!browser||await Promise.race([browser.close().then(()=>true).catch(()=>false),sleep(10000).then(()=>false)]);
 if(browserProc&&browserProc.exitCode===null&&browserProc.signalCode===null){try{browserProc.kill('SIGKILL')}catch{}}
 const serverStop=child?await stopChild(child,sleep):{method:'none',notes:[],left:[]};
 if(serverStop.left.length)results.push({name:'子进程清理',status:'failed',error:JSON.stringify(serverStop)});
 const modelServerClose=await closeModelServer();
 const blockedNetwork=fs.readFileSync(networkLog,'utf8');
 if(browserErrors.length||blockedNetwork.trim())results.push({name:'隔离与浏览器错误检查',status:'failed',error:JSON.stringify({browserErrors,blockedNetwork})});
 const summary={results,modelCalls,httpCalls,browserErrors,workspace,base,evidence,browser:browser?browser.version():null,blockedNetwork:fs.readFileSync(networkLog,'utf8'),serverStop};fs.writeFileSync(path.join(evidence,'results.json'),JSON.stringify(summary,null,2));fs.writeFileSync(path.join(evidence,'result.txt'),results.map(x=>x.status+' '+x.name+(x.error?'\n'+x.error:'')).join('\n'));
 // 第 29 例的 temp 由父套件预建持有：无论子套件是正常退出、失败还是被看门狗打断，最后都回收它。
 if(trashTemp){try{fs.rmSync(trashTemp,{recursive:true,force:true})}catch(e){}summary.trashHeldTemp=trashTemp;summary.trashHeldTempRemoved=!fs.existsSync(trashTemp)}
 if(temp)fs.rmSync(temp,{recursive:true,force:true});summary.workspaceRemoved=!temp||!fs.existsSync(temp);summary.serverExited=!child||(!alive(child.pid)&&!serverStop.left.length);summary.modelServerClosed=modelServerClose.closed;summary.modelServerTimedOut=modelServerClose.timedOut;summary.cleanup={browserClosed,serverStop,modelServerClosed:summary.modelServerClosed,modelServerTimedOut:summary.modelServerTimedOut,workspaceRemoved:summary.workspaceRemoved};if(!summary.workspaceRemoved||!summary.serverExited||!summary.modelServerClosed)results.push({name:'清理检查',status:'failed',error:'临时工作区或进程未清理'});fs.writeFileSync(path.join(evidence,'results.json'),JSON.stringify(summary,null,2));console.log('证据：'+evidence);process.exitCode=results.some(x=>x.status==='failed')?1:0;
 clearTimeout(watchdog);
 process.exit(process.exitCode||0);
});
