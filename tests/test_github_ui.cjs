const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs');
const {JSDOM}=require('jsdom');
const settle=async()=>{for(let i=0;i<5;i++)await new Promise(r=>setImmediate(r));};
async function fixture(t){
  const dom=new JSDOM(fs.readFileSync('public/github.html','utf8'),{url:'http://127.0.0.1:8766/github.html',runScripts:'outside-only'});
  t.after(()=>dom.window.close());const w=dom.window,d=w.document,calls=[];
  const state={connection:{owner:'owner'},tasks:[{profile_id:'model',minutes:180,enabled:true,body:'原关注方向'}],retry_documents:0,
    sources:[{id:'source',repository:'owner/repo',watch:['issues','pulls','comments'],threads:[869],enabled:true,count:1,capture:{}}]};
  w.setInterval=()=>0;
  w.fetch=async(url,options={})=>{calls.push({url,options,body:options.body&&JSON.parse(options.body)});return {ok:true,json:async()=>url==='/api/assistant/settings'?{profiles:[{profile_id:'model',configured:true,display_name:'模型'}]}:url==='/api/github'?state:{ok:true}};};
  w.eval(fs.readFileSync('public/github.js','utf8'));await settle();return {w,d,calls};
}
test('pinned discussions submit exact numbers and include replies without selecting releases or commits',async t=>{
  const {w,d,calls}=await fixture(t),f=d.querySelector('#gh-source');f.elements.repository.value='owner/repo';
  f.elements.threads.value='#869、980';f.elements.threads.dispatchEvent(new w.Event('input'));
  f.dispatchEvent(new w.Event('submit',{cancelable:true}));await settle();
  const post=calls.find(c=>c.url==='/api/github/source');assert.deepEqual(post.body.threads,[869,980]);
  assert.deepEqual(post.body.watch,['issues','pulls','comments']);assert.equal(calls.some(c=>c.url==='/api/github/run'),false);
});
test('invalid discussion input is explained and never sent to the backend',async t=>{
  const {w,d,calls}=await fixture(t),f=d.querySelector('#gh-source');f.elements.threads.value='869,869';
  f.dispatchEvent(new w.Event('submit',{cancelable:true}));await settle();
  assert.equal(calls.some(c=>c.url==='/api/github/source'),false);assert.match(d.querySelector('#gh-status').textContent,/不同的正整数/);
});
test('source scope and replies are visible while polling preserves current form edits',async t=>{
  const {w,d}=await fixture(t);assert.match(d.querySelector('#gh-sources').textContent,/讨论回复/);assert.match(d.querySelector('#gh-sources').textContent,/#869/);
  const f=d.querySelector('#gh-run');f.elements.topics.value='还没保存的新方向';d.querySelector('#gh-refresh').click();await settle();
  assert.equal(f.elements.topics.value,'还没保存的新方向');assert.equal(f.elements.profile_id.value,'model');
});
