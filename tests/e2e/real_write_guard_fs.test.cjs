'use strict';
// Every destructive probe runs solely in a fresh /tmp imitation of the formal roots.
const fs=require('node:fs'),path=require('node:path'),os=require('node:os'),assert=require('node:assert/strict'),{spawnSync}=require('node:child_process');
if(process.argv.includes('--stdio')){
 const target=process.argv[process.argv.indexOf('--stdio')+1],guard=require('./real_write_guard.cjs');guard.install();let denied=false;
 try{fs.writeSync(1,'changed');}catch(e){denied=String(e).includes('E2E_GUARD');}
 fs.writeFileSync(target,JSON.stringify({passed:denied}));return;
}
if(!process.argv.includes('--child')){
 const out=fs.mkdtempSync('/tmp/e2e-fs-guard-'),source=fs.readFileSync(path.join(__dirname,'real_write_guard.cjs'),'utf8');
 for(const mode of ['guarded','disabled']){
  const root=path.join(out,mode);fs.mkdirSync(path.join(root,'tests/e2e'),{recursive:true});fs.mkdirSync(path.join(root,'workspace'));fs.mkdirSync(path.join(root,'home/grok-bot-reports'),{recursive:true});
  fs.mkdirSync(path.join(root,'node_modules'));fs.symlinkSync(path.dirname(require.resolve(path.join(os.homedir(),'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright/package.json'))),path.join(root,'node_modules/playwright'));
  fs.writeFileSync(path.join(root,'tests/e2e/real_write_guard.cjs'),mode==='disabled'?source.replace('const ENABLED=true;','const ENABLED=false;'):source);
  fs.copyFileSync(__filename,path.join(root,'tests/e2e/probe.cjs'));
  const r=spawnSync(process.execPath,[path.join(root,'tests/e2e/probe.cjs'),'--child',mode],{encoding:'utf8',env:{...process.env,HOME:path.join(root,'home')},timeout:90000});fs.writeFileSync(path.join(out,mode+'.log'),r.stdout+r.stderr);
  const result=JSON.parse(fs.readFileSync(path.join(root,'result.json')));assert.equal(r.status,0,r.stderr);if(mode==='guarded'){assert.equal(result.failed,0);assert.equal(result.reads,4);}else{assert.equal(result.F,result.cases.length);assert(result.F>=90);}
  const stdio=path.join(root,'workspace/stdio'),stdioResult=path.join(root,'stdio-result.json');fs.writeFileSync(stdio,'keep-original');const fd=fs.openSync(stdio,'r+');
  const stdioChild=spawnSync(process.execPath,[path.join(root,'tests/e2e/probe.cjs'),'--stdio',stdioResult],{env:{...process.env,HOME:path.join(root,'home')},stdio:['ignore',fd,'pipe'],timeout:30000});fs.closeSync(fd);assert.equal(stdioChild.status,0,String(stdioChild.stderr));const stdioPassed=JSON.parse(fs.readFileSync(stdioResult)).passed&&fs.readFileSync(stdio,'utf8')==='keep-original';assert.equal(stdioPassed,mode==='guarded');
  console.log(mode+' inherited protected stdout '+(stdioPassed?'PASS':'F'));
  console.log(mode+' '+JSON.stringify({passed:result.passed,F:result.F,failed:result.failed,reads:result.reads,total:result.cases.length}));
 }
 console.log('证据 '+out);return;
}
const mode=process.argv.at(-1),root=path.resolve(__dirname,'../..'),cases=[],todo=[],C=fs.constants;
function add(name,fn){const dir=path.join(root,'workspace',String(todo.length));fs.mkdirSync(dir);const target=path.join(dir,'file'),other=path.join(dir,'other'),outside=path.join(root,'outside-'+todo.length);fs.writeFileSync(target,'keep-original');fs.writeFileSync(other,'other-original');fs.mkdirSync(outside);fs.writeFileSync(path.join(outside,'child'),'outside');todo.push({name,dir,target,other,outside,fn});}
const call=(name,...args)=>new Promise((resolve,reject)=>fs[name](...args,(err,value)=>err?reject(err):resolve(value)));
const methods={writeFile:(t,o,x)=>[t,'changed'],appendFile:t=>[t,'changed'],truncate:t=>[t,0],chmod:t=>[t,0o600],chown:t=>[t,process.getuid(),process.getgid()],utimes:t=>[t,1,1],lutimes:t=>[t,1,1],lchown:t=>[t,process.getuid(),process.getgid()],rename:(t,o,x)=>[t,path.join(x,'renamed')],copyFile:(t,o,x)=>[path.join(x,'child'),t],cp:(t,o,x)=>[path.join(x,'child'),t],link:(t,o,x)=>[t,path.join(x,'hardlink')],symlink:(t,o)=>['missing-source',o+'-link'],mkdir:(t,o)=>[o+'-dir'],mkdtemp:(t,o)=>[o+'-temp-'],unlink:t=>[t],rm:t=>[t],rmdir:(t,o,x,d)=>[path.join(d,'empty')]};
for(const [name,args] of Object.entries(methods))for(const route of ['sync','callback','promise']){
 const key=name+(route==='sync'?'Sync':'');if(typeof(route==='promise'?fs.promises[name]:fs[key])!=='function')continue;
 add(name+' '+route,async({target:t,other:o,outside:x,dir:d})=>{const a=args(t,o,x,d);if(route==='sync')return fs[key](...a);if(route==='callback')return call(name,...a);return fs.promises[name](...a);});
}
// Prepare empty directories before installing the guard.
for(const task of todo)fs.mkdirSync(path.join(task.dir,'empty'));
for(const route of ['sync','callback','promise'])for(const [label,flag] of [['O_TRUNC',C.O_RDONLY|C.O_TRUNC],['O_APPEND',C.O_WRONLY|C.O_APPEND],['O_CREAT',C.O_RDONLY|C.O_CREAT],['O_EXCL',C.O_RDONLY|C.O_CREAT|C.O_EXCL],...['r+','rs+','w','wx','w+','wx+','a','ax','a+','ax+','as','as+'].map(x=>[x,x])]){
 add('open '+route+' '+label,async({target,other})=>{const file=label.includes('CREAT')||label.includes('EXCL')||['wx','wx+','ax','ax+'].includes(label)?other+'-new':target;
  if(route==='promise'){const h=await fs.promises.open(file,flag);try{if(label==='O_APPEND')await h.write('changed');}finally{await h.close();}}
  else {const fd=route==='sync'?fs.openSync(file,flag):await call('open',file,flag);try{if(label==='O_APPEND')fs.writeSync(fd,'changed');}finally{fs.closeSync(fd);}}
 });
}
for(const key of ['write','writev','writeFile','appendFile','truncate','chmod','chown','utimes','sync','datasync','createWriteStream'])add('FileHandle.'+key,async({target})=>{const h=await fs.promises.open(target,'r');try{const args={write:['changed'],writev:[[Buffer.from('changed')]],writeFile:['changed'],appendFile:['changed'],truncate:[0],chmod:[0o600],chown:[process.getuid(),process.getgid()],utimes:[1,1],sync:[],datasync:[],createWriteStream:[]}[key];if(key==='createWriteStream'){const s=h[key](...args);s.on('error',()=>{});s.end('changed');await new Promise(r=>s.once('close',r));}else await h[key](...args);}finally{await h.close();}});
for(const key of ['write','writev','ftruncate','fchmod','fchown','futimes','fsync','fdatasync'])for(const route of ['sync','callback'])add(key+' '+route,async({target,fd})=>{const args={write:[fd,'changed'],writev:[fd,[Buffer.from('changed')]],ftruncate:[fd,0],fchmod:[fd,0o600],fchown:[fd,process.getuid(),process.getgid()],futimes:[fd,1,1],fsync:[fd],fdatasync:[fd]}[key];return route==='sync'?fs[key+'Sync'](...args):call(key,...args);});
for(const key of ['createWriteStream','WriteStream','createReadStream','ReadStream'])add(key,async({target})=>{const s=new fs[key](target,{flags:'w'});s.on('error',()=>{});if(key.includes('Write'))s.end('changed');else s.resume();await new Promise(r=>s.once('close',r));});
add('symlink destination escape',async({alias})=>fs.writeFileSync(alias,'changed'));
add('Buffer path',async({target})=>fs.writeFileSync(Buffer.from(target),'changed'));
add('URL path',async({target})=>fs.promises.writeFile(new URL('file://'+target),'changed'));
add('unknown pre-install fd',async({fd})=>fs.writeSync(fd,'changed'));
add('existing hardlink write escape',async({hardlink})=>fs.writeFileSync(hardlink,'changed'));
add('existing hardlink O_TRUNC escape',async({hardlink})=>{const fd=fs.openSync(hardlink,C.O_RDONLY|C.O_TRUNC);fs.closeSync(fd);});
add('existing hardlink FileHandle escape',async({hardlink})=>{const h=await fs.promises.open(hardlink,'r');try{await h.truncate(0);}finally{await h.close();}});
for(const task of todo)if(task.name.startsWith('existing hardlink')){task.hardlink=path.join(root,'hardlink-'+todo.indexOf(task));fs.linkSync(task.target,task.hardlink);}
for(const t of todo){t.fd=fs.openSync(t.target,'r+');t.alias=path.join(root,'alias-'+t.name.replace(/[^a-z0-9]/gi,'_'));fs.symlinkSync(t.target,t.alias);}
function snapshot(d){const result={};function scan(p){const s=fs.lstatSync(p);result[path.relative(d,p)]=[s.mode,s.uid,s.gid,s.mtimeMs,s.nlink,s.isFile()?fs.readFileSync(p).toString('hex'):s.isSymbolicLink()?fs.readlinkSync(p):null];if(s.isDirectory())for(const x of fs.readdirSync(p))scan(path.join(p,x));}scan(d);return result;}
const guard=require('./real_write_guard.cjs');guard.install();
(async()=>{let reads=0;for(const flags of [C.O_RDONLY,'r']){const fd=fs.openSync(todo[0].target,flags);assert.equal(fs.readFileSync(fd,'utf8'),'keep-original');fs.closeSync(fd);reads++;const h=await fs.promises.open(todo[0].target,flags);assert.equal(await h.readFile('utf8'),'keep-original');await h.close();reads++;}
 for(const task of todo){const before=snapshot(task.dir);let denied=false,error='';try{await task.fn(task);}catch(e){error=String(e);denied=error.includes('E2E_GUARD');}const unchanged=JSON.stringify(snapshot(task.dir))===JSON.stringify(before);const passed=denied&&unchanged;cases.push({name:task.name,passed,denied,unchanged,error:denied?'E2E_GUARD':error});console.log((passed?'PASS ':'F ')+task.name);fs.closeSync(task.fd);}
 const passed=cases.filter(x=>x.passed).length;fs.writeFileSync(path.join(root,'result.json'),JSON.stringify({cases,reads,passed,failed:mode==='guarded'?cases.length-passed:0,F:cases.length-passed},null,2));if(mode==='guarded'&&passed!==cases.length)process.exitCode=1;
})().catch(e=>{console.error(e);process.exitCode=1});
