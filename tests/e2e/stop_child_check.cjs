'use strict';
const assert=require('node:assert/strict'),path=require('node:path'),{spawn}=require('node:child_process');
const {stopChild,alive}=require('./stop_child.cjs');
const python=path.resolve(__dirname,'../../../article-importer/.venv/bin/python');
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
function pythonTree(){
 const child=spawn(python,['-c','import subprocess,sys,time\np=subprocess.Popen([sys.executable,"-c","import time; time.sleep(120)"])\nprint(p.pid, flush=True)\ntime.sleep(120)\n'],{detached:true,stdio:['ignore','pipe','ignore']});
 return new Promise((resolve,reject)=>{
  let buf='';
  const timer=setTimeout(()=>reject(new Error('孙进程没有报出 pid')),5000);
  child.stdout.on('data',chunk=>{buf+=chunk;if(buf.trim()){clearTimeout(timer);resolve({child,grandchild:Number(buf.trim())})}});
  child.on('exit',code=>{if(!buf.trim())reject(new Error('python 提前退出 '+code))});
 });
}
(async()=>{
 const sibling=spawn('sleep',['60'],{stdio:'ignore'});
 const first=await pythonTree();
 assert(alive(first.child.pid)&&alive(first.grandchild)&&alive(sibling.pid));
 const original=process.kill;
 process.kill=function(pid,sig){
  if(typeof pid==='number'&&pid<0){const error=new Error('kill EPERM');error.code='EPERM';error.errno=-1;error.syscall='kill';throw error}
  return original.call(process,pid,sig);
 };
 let forced;
 try{forced=await stopChild(first.child,sleep)}
 finally{process.kill=original}
 assert(forced.notes.some(note=>note.includes('EPERM')),JSON.stringify(forced));
 assert.equal(forced.left.length,0,JSON.stringify(forced));
 assert.equal(alive(first.child.pid),false);
 assert.equal(alive(first.grandchild),false);
 assert.equal(alive(sibling.pid),true,'同组的旁支进程被误杀');
 const second=await pythonTree();
 const natural=await stopChild(second.child,sleep);
 assert.equal(natural.left.length,0,JSON.stringify(natural));
 assert.equal(alive(second.child.pid),false);
 assert.equal(alive(second.grandchild),false);
 assert.equal(alive(sibling.pid),true);
 sibling.kill();
 console.log('stop_child_check ok',JSON.stringify({forced:forced.method,natural:natural.method,forcedNotes:forced.notes}));
})().catch(error=>{console.error(error);process.exitCode=1});
