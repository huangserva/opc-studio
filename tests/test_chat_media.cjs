const {test}=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');
const path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../public/chat-media.js'),'utf8');
const progressSource=fs.readFileSync(path.join(__dirname,'../public/media-progress.js'),'utf8');
class Element{
  constructor(tag){this.tag=tag;this.children=[];this.dataset={};this.isConnected=true;this.scrollHeight=500;this.scrollTop=100;this.clientHeight=400;}
  append(...items){this.children.push(...items);}
  replaceChildren(...items){this.children=items;}
  setAttribute(k,v){this[k]=v;}
  removeAttribute(k){delete this[k];}
  addEventListener(type,fn){(this.events??={})[type]=fn;}
  querySelector(selector){return this.children.find(c=>selector==='[data-media-progress]'?c.className==='media-progress':c.className==='chat-media-status');}
  set innerHTML(value){this.children=[Object.assign(new Element('p'),{className:'chat-media-status'})];}
}
function setup(jobs){
  const host=new Element('div');host.dataset.chatMedia='a'.repeat(32);
  const box=new Element('div'),timers=new Map();let sequence=0,calls=0;
  const context=vm.createContext({document:{createElement:tag=>new Element(tag),querySelector:()=>box,querySelectorAll:()=>[host]},
    assistantState:{open:true,active:null},api:async()=>{calls++;return jobs.shift()||jobs.at(-1);},openMediaJob:()=>{},
    captureAssistantScroll:()=>({top:box.scrollTop}),restoreAssistantScroll:()=>{if(box.followLatest)box.scrollTop=box.scrollHeight-box.clientHeight;},
    setInterval:()=>0,setTimeout:fn=>{timers.set(++sequence,fn);return sequence;},clearTimeout:id=>timers.delete(id)});
  vm.runInContext(progressSource,context);
  vm.runInContext(source,context);
  return {host,timers,context,calls:()=>calls,run:code=>vm.runInContext(code,context)};
}
const settle=()=>new Promise(resolve=>setImmediate(resolve));
test('完成旧图片直接显示，重绘不重复获取或重建媒体',async()=>{
  const s=setup([{status:'done',kind:'image',model_name:'Qwen',asset_url:'/generation-assets/'+'a'.repeat(32),prompt:'小猫'}]);
  s.run('syncChatMedia()');await settle();
  const card=s.host.children[0],img=card.children.find(n=>n.tag==='img');
  assert.ok(img);assert.equal(img.alt,'小猫');assert.equal(s.timers.size,0);
  s.run('syncChatMedia()');await settle();assert.equal(s.calls(),1);assert.equal(s.host.children[0],card);
});
test('聊天回合已结束时继续轮询，视频完成后显示播放器并停止轮询',async()=>{
  const s=setup([{status:'running',kind:'video',model_name:'H3'},{status:'done',kind:'video',model_name:'H3',asset_url:'/generation-assets/'+'a'.repeat(32)}]);
  s.run('syncChatMedia()');await settle();assert.equal(s.timers.size,1);
  const next=[...s.timers.values()][0];s.timers.clear();next();await settle();
  const video=s.host.children[0].children.find(n=>n.tag==='video');assert.ok(video);assert.equal(video.controls,true);assert.equal(s.timers.size,0);
});
test('失败不显示虚假图片，关闭侧栏停止后续轮询',async()=>{
  const s=setup([{status:'error',kind:'image',model_name:'Krea2',error:'GPU busy'}]);s.run('syncChatMedia()');await settle();
  assert.equal(s.host.children[0].children.some(n=>n.tag==='img'),false);assert.equal(s.timers.size,0);
  const running=setup([{status:'running',kind:'image'}]);running.run('syncChatMedia()');await settle();
  running.run('assistantState.open=false;stopChatMedia()');assert.equal(running.timers.size,0);
});
test('预览延迟加载时重新判断跟随状态，不拉走正在看历史的用户',async()=>{
  const s=setup([{status:'done',kind:'image',asset_url:'/result',prompt:'图片'}]);
  const box=s.run('document.querySelector("#assistant-messages")');box.followLatest=true;
  s.run('syncChatMedia()');await settle();
  const img=s.host.children[0].children.find(n=>n.tag==='img');
  box.followLatest=false;box.scrollTop=20;box.scrollHeight=900;
  img.events.load();assert.equal(box.scrollTop,20);
  box.followLatest=true;img.events.load();assert.equal(box.scrollTop,500);
});
test('总状态不变时也更新真实步数，并保留历史阅读位置',async()=>{
  const base={status:'running',kind:'image',prompt:'很长的描述',created_at:new Date().toISOString(),backend_checked_at:new Date().toISOString()};
  const s=setup([{...base,progress:{phase:'sampling',label:'正在生成画面',current:2,total:20,percent:10}},
    {...base,progress:{phase:'sampling',label:'正在生成画面',current:3,total:20,percent:15}}]);
  const box=s.run('document.querySelector("#assistant-messages")');box.followLatest=false;box.scrollTop=20;
  s.run('syncChatMedia()');await settle();
  let progress=s.host.children[0].children.find(c=>c.className==='media-progress');assert.match(progress.children[0].children[0].textContent,/2\/20/);
  const next=[...s.timers.values()][0];s.timers.clear();next();await settle();
  progress=s.host.children[0].children.find(c=>c.className==='media-progress');assert.match(progress.children[0].children[0].textContent,/3\/20/);assert.equal(box.scrollTop,20);
  assert.equal(s.host.children[0].children.find(c=>c.tag==='details').open,false);
});
test('没有真实步数不编造百分比，状态过期明确提示',()=>{
  const s=setup([]);const now=Date.now();
  const value=s.run(`mediaProgressState(${JSON.stringify({status:'running',created_at:new Date(now-70000).toISOString(),backend_checked_at:new Date(now-30000).toISOString(),progress:{phase:'encoding',label:'正在理解画面描述'}})},${now})`);
  assert.equal(value.percent,null);assert.equal(value.warning,true);assert.match(value.connection,/未收到服务器确认/);assert.equal(value.elapsed,'已用 1 分 10 秒');
  const done=s.run(`mediaProgressState(${JSON.stringify({status:'done',created_at:new Date(now-60000).toISOString(),finished_at:new Date(now-10000).toISOString(),updated_at:new Date(now).toISOString()})},${now+90000})`);
  assert.equal(done.elapsed,'已用 50 秒');assert.equal(done.active,false);
});
