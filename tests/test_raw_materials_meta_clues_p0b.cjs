'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),{JSDOM}=require('jsdom');
const tick=()=>new Promise(resolve=>setImmediate(resolve));
const code=fs.readFileSync(process.env.P0B_JS_FILE||'public/clues.js','utf8');
const windows=[];test.afterEach(()=>{while(windows.length)windows.pop().close()});
const response=value=>({ok:true,json:async()=>value});
const card=(id='a',category='other')=>({id:id.repeat(32),title:'合成真实卡片'+id,summary:'合成正文摘要',categories:[category],status:'candidate',priority:'normal',kind:'information',sources:['aisa'],updated_at:'2026-10-31T00:00:00Z'});
async function fixture({holdRows=false}={}){
 const dom=new JSDOM(fs.readFileSync('public/clues.html','utf8'),{url:'http://127.0.0.1:8766/clues.html',runScripts:'outside-only'}),w=dom.window;windows.push(w);const pending=[],rows=[];
 w.HTMLDialogElement.prototype.showModal=function(){this.open=true};w.HTMLDialogElement.prototype.close=function(){this.open=false};
 w.fetch=async raw=>{
  const url=new URL(raw,'http://test');
  if(url.pathname==='/api/clues'){
   const p=url.searchParams,category=p.get('category')||'other',item=card(category==='business'?'b':'a',category);
   const value={items:[item],timeline:[],total:1,next_offset:null,counts:{other:0,business:0},tag_counts:{},heat_counts:{},years:[],sources:[],facets_pending:true};
   if(p.get('phase')!=='rows')return new Promise(resolve=>pending.push({p,resolve}));
   if(holdRows&&rows.length===0)return new Promise((resolve,reject)=>rows.push({resolve,reject,value}));
   rows.push({value});return response(value);
  }
  if(url.pathname==='/api/clues/wechat/status')return response({processed_versions:0,has_pending:false});
  if(url.pathname==='/api/schedules')return response({tasks:[],models:[]});
  if(url.pathname==='/api/knowledge/health')return response({state:'unknown',capture:{status:'unknown'}});
  if(url.pathname==='/api/knowledge/meta')return response({sources:[]});
  return response({});
 };
 w.eval(code);await tick();await tick();
 const answer=async(index,counts)=>{pending[index].resolve(response({counts,tag_counts:{},heat_counts:{},years:['2026'],sources:['aisa']}));await tick();await tick()};
 return{w,d:w.document,pending,rows,answer};
}
test('actual cards render before slow aggregate; late counts preserve card node identity',async()=>{
 const f=await fixture();const node=f.d.querySelector('[data-clue]');assert(node,'统计未返回时必须已有可读卡片');assert.match(node.textContent,/合成真实卡片/);assert.match(f.d.querySelector('#cl-categories').textContent,/统计中/);assert.equal(f.pending.length,1);
 await f.answer(0,{other:7});assert.equal(f.d.querySelector('[data-clue]'),node);assert.equal(f.d.querySelector('[data-category=other] b').textContent,'7');
});
test('stale aggregates cannot overwrite the current filter after a newer response',async()=>{
 const f=await fixture();f.d.querySelector('[data-category=business]').click();await tick();await tick();assert.equal(f.pending.length,2);
 await f.answer(1,{business:2,other:0});const node=f.d.querySelector('[data-clue]');assert.equal(node.dataset.clue,'b'.repeat(32));
 await f.answer(0,{business:999,other:999});assert.equal(f.d.querySelector('[data-category=business] b').textContent,'2');assert.equal(f.d.querySelector('[data-clue]'),node);
});
test('late old rows cannot overwrite a newer user choice',async()=>{
 const f=await fixture({holdRows:true});f.d.querySelector('#cl-refresh').click();await tick();await tick();const node=f.d.querySelector('[data-clue]');assert(node);
 f.rows[0].resolve(response({...f.rows[0].value,items:[card('z')]}));await tick();await tick();assert.equal(f.d.querySelector('[data-clue]'),node);assert.notEqual(node.dataset.clue,'z'.repeat(32));
});

test('stale row failure cannot replace the status after a newer successful selection',async()=>{
 const f=await fixture({holdRows:true});f.d.querySelector('#cl-refresh').click();await tick();await tick();const node=f.d.querySelector('[data-clue]');assert(node);
 f.rows[0].reject(new Error('SYNTHETIC_OLD_FAILURE'));await tick();await tick();assert.equal(f.d.querySelector('[data-clue]'),node);assert.equal(f.d.querySelector('#cl-status').textContent,'');
});
