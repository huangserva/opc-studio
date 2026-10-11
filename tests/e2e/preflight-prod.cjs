'use strict';
// 只发固定 loopback GET，不跟随重定向；不保存业务正文、联系人或凭据。
const http=require('node:http'),fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto');
const {execFileSync}=require('node:child_process');
const ROOT=path.resolve(__dirname,'../..');
const endpoints=[
 [8766,'/api/intake?limit=1'],[8766,'/api/intake/analysis-status'],[8766,'/api/cards?limit=1'],
 [8766,'/api/collect/platforms'],[8766,'/api/collect/status'],[8766,'/api/intel/status'],
 [8766,'/api/sources/wechat/radar-quarantine?limit=1'],[3000,'/api/source/radar-quarantine?limit=1'],
 [8766,'/intake.js'],[8766,'/cards.js'],[8766,'/intel.js'],[3000,'/']
];
function shape(value,depth=0){
 if(value===null)return 'null';if(Array.isArray(value))return {type:'array',count:value.length,item:depth<3&&value.length?shape(value[0],depth+1):undefined};
 if(typeof value==='object'){if(depth>=3)return 'object';return Object.fromEntries(Object.keys(value).filter(k=>/^[a-zA-Z_][a-zA-Z0-9_-]{0,64}$/.test(k)).sort().map(k=>[k,shape(value[k],depth+1)]));}
 return typeof value;
}
function identifiers(value){const out={};for(const key of ['schema','version','build_id','buildId']){const v=value?.[key];if(typeof v==='string'&&/^[a-zA-Z0-9_./:+-]{1,100}$/.test(v))out[key]=v;}return out;}
function get(port,pathname){
 // 12000ms 是 socket 无活动超时（不是严格总耗时）；不放宽它。
 // collect/status 性能目标另由计时回归测试约束为总耗时 <1000ms。
 if(![8766,3000].includes(port)||!endpoints.some(x=>x[0]===port&&x[1]===pathname))throw Error('预检地址不在固定白名单');
 return new Promise(resolve=>{const start=Date.now();const req=http.request({hostname:'127.0.0.1',port,path:pathname,method:'GET',agent:false},res=>{
  let bytes=0;const chunks=[];res.on('data',c=>{bytes+=c.length;if(bytes>4*1024*1024){req.destroy(new Error('响应超过预检大小上限'));return;}chunks.push(c)});
  res.on('end',()=>{const body=Buffer.concat(chunks),record={port,path:pathname,method:'GET',status:res.statusCode,reachable:res.statusCode>=200&&res.statusCode<300,ms:Date.now()-start,bytes,contentType:res.headers['content-type'],sha256:crypto.createHash('sha256').update(body).digest('hex')};
   if((res.headers['content-type']||'').includes('json')){try{const v=JSON.parse(body);record.shape=shape(v);record.identifiers=identifiers(v)}catch{record.jsonError='无效 JSON'}}
   else if(port===3000){const m=body.toString().match(/"buildId"\s*:\s*"([A-Za-z0-9_-]{1,100})"/);record.buildId=m?.[1]||null;record.buildIdNote=m?'响应提供 Next buildId':'响应未提供可核实的 buildId';}
   if(pathname.endsWith('.js')){const disk=path.join(ROOT,'public',path.basename(pathname));if(fs.existsSync(disk)){record.diskSha256=crypto.createHash('sha256').update(fs.readFileSync(disk)).digest('hex');record.matchesDisk=record.sha256===record.diskSha256;}}
   resolve(record);
  });res.on('error',()=>resolve({port,path:pathname,method:'GET',reachable:false,error:'响应中断'}));
 });req.setTimeout(12000,()=>req.destroy(new Error('预检超时')));req.on('error',e=>resolve({port,path:pathname,method:'GET',reachable:false,error:e.code||'GET_FAILED',ms:Date.now()-start}));req.end();});
}
function processInfo(port){try{const pids=[...new Set(execFileSync('lsof',['-nP','-iTCP:'+port,'-sTCP:LISTEN','-t'],{encoding:'utf8',timeout:5000}).trim().split(/\s+/).filter(x=>/^\d+$/.test(x)))];return {port,listeners:pids.map(pid=>({pid:Number(pid),started:execFileSync('ps',['-p',pid,'-o','lstart='],{encoding:'utf8',timeout:5000}).trim(),elapsed:execFileSync('ps',['-p',pid,'-o','etime='],{encoding:'utf8',timeout:5000}).trim()}))};}catch{return {port,listeners:[],note:'未查到监听者或系统进程查询不可用'};}}
(async()=>{const startedAt=new Date().toISOString();const probes=[];for(const [port,p] of endpoints)probes.push(await get(port,p));const report={startedAt,endedAt:new Date().toISOString(),policy:{method:'GET',hosts:['127.0.0.1'],ports:[8766,3000],followRedirects:false,businessPayloadStored:false},processes:[processInfo(8766),processInfo(3000)],probes,note:'静态文件与磁盘一致不能证明 Python 内存已加载新后端；无版本接口时不虚构版本号。GET 的上游转发行为由产品实现控制，本脚本自身不访问其他地址。'};const dir=path.join(ROOT,'coordination/evidence/round6-prep');fs.mkdirSync(dir,{recursive:true});const target=path.join(dir,'preflight-'+startedAt.replace(/[:.]/g,'-')+'.json');fs.writeFileSync(target,JSON.stringify(report,null,2));console.log(JSON.stringify({report:target,processes:report.processes,probes:probes.map(x=>({port:x.port,path:x.path,status:x.status,reachable:x.reachable,matchesDisk:x.matchesDisk,buildId:x.buildId}))},null,2));process.exitCode=probes.some(x=>!x.reachable||x.jsonError)?1:0;})().catch(()=>{console.error('预检执行失败');process.exitCode=1});
