'use strict';
const test = require('node:test'), assert = require('node:assert/strict'), fs = require('node:fs');
const {JSDOM} = require('jsdom');
const tick = () => new Promise(resolve => setImmediate(resolve));
const listing = {sections:[{name:'平台',platforms:[{id:'mail',label:'邮件',state:'connected',home:'/knowledge.html'}]}]};
async function fixture(row, options={}) {
  const dom = new JSDOM(fs.readFileSync('public/intel.html','utf8'), {url:'http://127.0.0.1:9999/intel.html',runScripts:'outside-only',pretendToBeVisual:true});
  const w=dom.window, timers=new Map(), calls=[];let seq=0;
  w.setInterval=()=>0;w.clearInterval=()=>{};
  w.setTimeout=(fn,ms)=>{const id=++seq;timers.set(id,{fn,ms});return id};w.clearTimeout=id=>timers.delete(id);
  w.fetch=async url=>{
    calls.push(url); let data={};
    if(url==='/api/collect/platforms')data=options.listing||listing;
    else if(url==='/api/collect/status')data=options.status?await options.status():{platforms:[{id:'mail',...row}],quota:null,quota_status:{timed_out:true}};
    else if(url==='/api/intel/subscriptions')data={subscriptions:[]};
    else if(url.startsWith('/api/intel/articles'))data={articles:[],total:0,albums:[]};
    else if(url==='/api/intel/status')data={subscriptions:0,articles:0,awaiting:0,credentials:'unknown',busy:false};
    else if(url==='/api/intel/recommendations')data={items:[]};
    else if(url==='/api/schedules')data={tasks:[]};
    return {ok:true,json:async()=>data};
  };
  w.eval(fs.readFileSync('public/intel.js','utf8'));
  await tick();await tick();
  async function fire(){assert.equal(timers.size,1);const [id,t]=[...timers][0];timers.delete(id);assert.equal(t.ms,2000);await t.fn();await tick();}
  return {w,d:w.document,timers,calls,fire,close:()=>w.close()};
}
for(const [name,row,label,planned] of [
  ['规划',{available:true},'规划中',true],
  ['未就绪',{available:false,refreshing:true},'未就绪'],
  ['正在核对',{available:null,refreshing:true},'核对中'],
  ['未返回可用性但核对中',{refreshing:true},'核对中'],
  ['待核对',{available:null,refreshing:false},'待核对'],
  ['旧结果',{available:true,stale:true,checked_at:'2026-10-09T20:00:00Z'},'已接通（旧结果）'],
  ['新结果',{available:true,stale:false},'已接通'],
])test('角标和详情一致：'+name,async()=>{
  const custom=structuredClone(listing);if(planned)custom.sections[0].platforms[0].state='planned';
  const p=await fixture(row,{listing:custom});try{
    const card=p.d.querySelector('[data-platform="mail"]');
    assert.equal(card.querySelector('.itl-platform-state').textContent,label);
    card.click();assert.equal(p.d.querySelector('#itl-delegated .itl-muted').textContent,label);
    if(row.stale){const local=new p.w.Date(row.checked_at).toLocaleString();assert.ok(card.querySelector('small').textContent.includes(local));assert.ok(p.d.querySelector('#itl-delegated').textContent.includes(local));}
  }finally{p.close()}
});
test('只重拉状态且最多三次，每次重绘只有一个定时器',async()=>{
  const p=await fixture({available:null,refreshing:true});try{
    for(let i=0;i<3;i++){assert.equal(p.timers.size,1);await p.fire();}
    assert.equal(p.timers.size,0);
    assert.equal(p.calls.filter(x=>x==='/api/collect/status').length,4);
    assert.equal(p.calls.filter(x=>x==='/api/collect/platforms').length,1);
  }finally{p.close()}
});
test('刷新完成更新详情并停止重拉，quota=null 不影响展示',async()=>{
  let count=0;const p=await fixture({}, {status:async()=>({quota:null,platforms:[{id:'mail',available:++count===1?null:true,refreshing:count===1,detail:'状态'+count}]})});
  try{p.d.querySelector('[data-platform="mail"]').click();await p.fire();assert.equal(p.timers.size,0);assert.equal(p.d.querySelector('#itl-delegated .itl-muted').textContent,'已接通');assert.match(p.d.querySelector('#itl-delegated').textContent,/状态2/);}finally{p.close()}
});
test('切走页面清理定时器，不再请求',async()=>{
  const p=await fixture({available:null,refreshing:true});try{p.w.dispatchEvent(new p.w.Event('pagehide'));assert.equal(p.timers.size,0);assert.equal(p.calls.filter(x=>x==='/api/collect/status').length,1);}finally{p.close()}
});
test('切走时在途刷新不能重绘或再排定时器',async()=>{
  let resolve,count=0;
  const p=await fixture({}, {status:()=>++count===1?{platforms:[{id:'mail',available:null,refreshing:true}]}:new Promise(r=>resolve=r)});
  try{const [id,t]=[...p.timers][0];p.timers.delete(id);const pending=t.fn();await tick();p.w.dispatchEvent(new p.w.Event('pagehide'));resolve({platforms:[{id:'mail',available:true,refreshing:true}]});await pending;assert.equal(p.timers.size,0);assert.equal(p.d.querySelector('.itl-platform-state').textContent,'核对中');}finally{p.close()}
});
