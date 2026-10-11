'use strict';
const {spawnSync}=require('node:child_process');
// macOS kill(2)：对进程组发信号时，组里只要有一个进程发不了，就返回 EPERM，并且整组一个信号都不送。
// detached 的子进程另起会话后，组里若混进发不了信号的成员（或沙箱拒绝负 pid），负 pid 会失败。
// 这时退回 child.kill()，再按父进程号只给这棵子树的正 pid 发信号，避免误杀并行的另一个套件。
function pgidOf(pid){
 const out=spawnSync('ps',['-o','pgid=','-p',String(pid)],{encoding:'utf8'});
 const value=Number(String(out.stdout||'').trim());
 return Number.isInteger(value)?value:0;
}
function descendantPids(root){
 const out=spawnSync('ps',['-ax','-o','pid=,ppid='],{encoding:'utf8'});
 const children=new Map();
 for(const line of String(out.stdout||'').split('\n')){
  const [pid,ppid]=line.trim().split(/\s+/).map(Number);
  if(!pid||!ppid)continue;
  if(!children.has(ppid))children.set(ppid,[]);
  children.get(ppid).push(pid);
 }
 const found=[],stack=[root],seen=new Set();
 while(stack.length){
  const pid=stack.pop();
  if(!pid||seen.has(pid))continue;
  seen.add(pid);found.push(pid);
  for(const kid of children.get(pid)||[])stack.push(kid);
 }
 return found;
}
function alive(pid){
 try{process.kill(pid,0);return true}
 catch(e){return e.code==='EPERM'}
}
function signalPid(pid,sig,notes){
 try{process.kill(pid,sig);return 'ok'}
 catch(e){if(e.code!=='ESRCH'&&notes)notes.push(pid+' '+sig+' '+e.code);return e.code||'error'}
}
function signalGroup(pgid,sig,ownPgid,notes){
 if(!pgid||pgid<2||pgid===ownPgid)return 'skip';
 try{process.kill(-pgid,sig);return 'group'}
 catch(e){
  if(e.code==='ESRCH')return 'esrch';
  if(notes)notes.push('group '+pgid+' '+sig+' '+e.code);
  return e.code==='EPERM'?'eperm':'error';
 }
}
function killTree(child,sig,notes,tracked){
 if(!child||!child.pid)return;
 for(const pid of descendantPids(child.pid))tracked.add(pid);
 try{child.kill(sig)}
 catch(e){if(e.code!=='ESRCH')notes.push('child.kill '+sig+' '+(e.code||e.message))}
 for(const pid of tracked){
  if(pid===process.pid)continue;
  signalPid(pid,sig,notes);
 }
}
function forceStopSync(child){
 const notes=[],tracked=new Set();
 if(!child||!child.pid)return {method:'none',notes,left:[]};
 for(const pid of descendantPids(child.pid))tracked.add(pid);
 const method=signalGroup(pgidOf(child.pid),'SIGKILL',pgidOf(process.pid),notes);
 if(method==='eperm'||method==='error'||method==='skip')killTree(child,'SIGKILL',notes,tracked);
 const left=[...tracked].filter(pid=>pid!==process.pid&&alive(pid));
 return {method,notes,left};
}
async function stopChild(child,sleep){
 const notes=[];
 if(!child||!child.pid)return {method:'none',notes,left:[]};
 const root=child.pid,own=pgidOf(process.pid),tracked=new Set();
 const leftovers=()=>{for(const pid of descendantPids(root))tracked.add(pid);return [...tracked].filter(pid=>pid!==process.pid&&alive(pid))};
 if(child.exitCode!==null||child.signalCode!==null){
  killTree(child,'SIGKILL',notes,tracked);
  await sleep(200);
  return {method:'already-exited',notes,left:leftovers()};
 }
 for(const pid of descendantPids(root))tracked.add(pid);
 let method=signalGroup(pgidOf(root),'SIGTERM',own,notes);
 if(method==='eperm'||method==='error'||method==='skip'){killTree(child,'SIGTERM',notes,tracked);method=method==='skip'?'tree':'child.kill'}
 const deadline=Date.now()+5000;
 while(Date.now()<deadline){
  if(!alive(root)&&!leftovers().length)return {method,notes,left:[]};
  await sleep(100);
 }
 const again=signalGroup(pgidOf(root),'SIGKILL',own,notes);
 if(again!=='group'&&again!=='esrch')killTree(child,'SIGKILL',notes,tracked);
 else for(const pid of leftovers())signalPid(pid,'SIGKILL',notes);
 const deadline2=Date.now()+2000;
 while(Date.now()<deadline2){
  if(!alive(root)&&!leftovers().length)return {method,notes,left:[]};
  await sleep(100);
 }
 return {method,notes,left:leftovers()};
}
module.exports={stopChild,forceStopSync,descendantPids,alive,pgidOf};
