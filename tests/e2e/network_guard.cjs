// Pi 子进程也仅能连接本轮两个本地端口，不能连接生产或公网。
const net=require('node:net'),fs=require('node:fs');
const allowed=new Set((process.env.E2E_ALLOWED_PORTS||'').split(',').map(Number));
const original=net.Socket.prototype.connect;
net.Socket.prototype.connect=function(...args){
 let a=args[0];if(Array.isArray(a))a=a[0];
 const port=typeof a==='object'?Number(a.port):Number(a);
 const host=typeof a==='object'?(a.host||'localhost'):(typeof args[1]==='string'?args[1]:'localhost');
 if(!allowed.has(port)||!['127.0.0.1','::1','localhost'].includes(host)){
  fs.appendFileSync(process.env.E2E_NETWORK_LOG,JSON.stringify({blocked:'node:'+host+':'+port})+'\n');
  throw new Error('E2E 禁止非夹具网络连接');
 }
 return original.apply(this,args);
};
