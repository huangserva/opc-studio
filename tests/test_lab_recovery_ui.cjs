/* fixes-ui-audit-2：生成页恢复（M1）与历史管理一致（L1）。

   M1：首次 /api/generation/lab 失败后，自动重试或点「刷新状态」必须把模型下拉和初始参数
   补回来；初始化之后的刷新不能覆盖用户已选模型和已输入描述。
   L1：已停止（cancelled）的记录在历史行、全选和结果区用同一份「终态可管理」判据；
   uncertain 维持现状不开放管理，生成中的任务也不能删。

   只用 jsdom 与假 fetch：不访问真实服务，不提交生成，不读取历史正文之外的内容。
   Run: node --test tests/test_lab_recovery_ui.cjs
*/
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const {JSDOM}=require('jsdom');

const root=path.join(__dirname,'..');
const html=fs.readFileSync(path.join(root,'public/lab.html'),'utf8');
// lab.html 的真实加载顺序（media-progress.js / media-lora.js 的控件在夹具里打桩）。
const scripts=['lab-core.js','media-size.js','lab-video.js','lab-management.js','lab-image.js','lab.js']
 .map(name=>({name,source:fs.readFileSync(path.join(root,'public',name),'utf8').replace(/\nrefreshLab\(true\);\s*$/,'\n')}));

const id=n=>n.toString(16).padStart(32,'0');
const qi21={id:'qi21',name:'Qwen Image 2.1',kind:'image',sizes:['1024x1024'],steps:20,cfg:1};
const krea2={id:'krea2',name:'Krea2',kind:'image',sizes:['1024x1024'],steps:8,cfg:1};
const job=(n,status='done',extra={})=>({id:id(n),kind:'image',status,origin:'lab',model:'qi21',model_name:'Qwen Image 2.1',
 created_at:`2026-10-11T01:0${n}:00+00:00`,prompt:`黄昏湖边的柴犬 ${n}`,asset_url:'/generation-assets/'+id(n),
 settings:{size:'1024x1024',seed:n,steps:20,cfg:1},...extra});
const settle=()=>new Promise(resolve=>setImmediate(resolve));
async function until(check){for(let count=0;count<30&&!check();count++)await settle();assert.ok(check(),'asynchronous fixture did not reach expected state');}

function setup(t,{failures=0,models=[qi21],jobs=[]}={}){
 const dom=new JSDOM(html,{url:'http://127.0.0.1:8766/lab.html?kind=image',runScripts:'outside-only'});
 t.after(()=>dom.window.close());
 const w=dom.window,context=dom.getInternalVMContext(),calls=[];let remaining=failures,nextTimer=0;
 w.setTimeout=()=>++nextTimer;w.clearTimeout=()=>{};                       // 不真的跑 4 秒轮询
 w.renderLoraFields=()=>{};w.selectedLoraParameters=()=>({});w.updateLoraEnabled=()=>{};
 w.renderMediaProgress=()=>{};w.markMediaProgressUnavailable=()=>{};w.attachMediaProgressHost=()=>{};
 w.HTMLElement.prototype.scrollIntoView=()=>{};w.HTMLMediaElement.prototype.pause=()=>{};
 w.HTMLDialogElement.prototype.showModal=function(){this.open=true;};
 w.HTMLDialogElement.prototype.close=function(){this.open=false;};
 w.fetch=async(url,options={})=>{
  const parsed=new URL(url,w.location.href),call={url,options,path:parsed.pathname,query:parsed.searchParams};
  if(typeof options.body==='string')call.payload=JSON.parse(options.body);
  calls.push(call);
  if(call.path!=='/api/generation/lab')throw new Error('Unexpected test request '+url);
  if(remaining-->0)throw new TypeError('Failed to fetch');                 // 首次状态读取失败
  return {ok:true,json:async()=>({models,jobs,busy:false,preferences:{image:{model:'qi21'}},image_skill:{styles:[]}})};
 };
 for(const script of scripts)vm.runInContext(script.source,context,{filename:script.name});
 w.fixtureJobs=jobs;
 const run=source=>vm.runInContext(source,context),query=selector=>w.document.querySelector(selector);
 run('lab.jobs=fixtureJobs;');
 return {w,run,query,calls,
  load:()=>run('refreshLab();'),
  seedDraft:draft=>w.localStorage.setItem('studio-generation-lab-v1-image',JSON.stringify(draft)),
  rows:()=>new Map([...w.document.querySelectorAll('.lab-history-item')]
   .map(row=>[row.querySelector('[data-select]').dataset.select,row])),
  row:n=>{const rows=new Map([...w.document.querySelectorAll('.lab-history-item')]
   .map(row=>[row.querySelector('[data-select]').dataset.select,row]));return rows.get(id(n));},
  selected:()=>run('JSON.stringify([...lab.selection].sort())'),
  status:()=>query('#lab-submit-status').textContent};
}

// ── M1：首次读取失败后的恢复 ─────────────────────────────────────────────
test('首次状态读取失败保留错误与重试入口，下一次成功补回模型下拉',async t=>{
 const s=setup(t,{failures:1,jobs:[job(1)]});
 await s.load();
 assert.equal(s.query('#lab-model').options.length,0,'首次失败时不能假装模型已就绪');
 assert.equal(s.run('lab.initialized'),false,'没成功读到就不能算已初始化');
 assert.match(s.status(),/状态读取失败/);
 assert.match(s.status(),/自动重试|刷新状态/,'要留下可读的重试入口');
 await s.load();                                     // 自动重试，或用户点「刷新状态」
 assert.equal(s.query('#lab-model').options.length,1,'重试后模型下拉必须补回来');
 assert.equal(s.query('#lab-model').value,'qi21');
 assert.equal(s.run('lab.models.length'),1);
 assert.equal(s.run('lab.initialized'),true);
 assert.equal(s.query('#lab-steps').value,'20','初始采样参数也要一起补回来');
 assert.doesNotMatch(s.status(),/状态读取失败/,'成功后不能留着旧错误');
 assert.equal(s.query('#lab-history-list').querySelectorAll('.lab-history-item').length,1);
});

test('失败期间用户已经写的描述不会被草稿覆盖，模型仍补回来',async t=>{
 const s=setup(t,{failures:1,jobs:[job(1)]});
 s.seedDraft({model:'qi21',prompt:'草稿里的旧描述',seed:7});
 await s.load();
 s.query('#lab-prompt').value='用户自己写的描述';
 await s.load();
 assert.equal(s.query('#lab-prompt').value,'用户自己写的描述','草稿只补空字段');
 assert.equal(s.query('#lab-model').value,'qi21');
 assert.equal(s.query('#lab-seed').value,'7','空字段仍然按草稿恢复');
});

test('初始化之后的刷新保留用户已选模型、描述与参数',async t=>{
 const s=setup(t,{models:[qi21,krea2],jobs:[job(1)]});
 await s.load();
 const select=s.query('#lab-model');
 select.value='krea2';select.dispatchEvent(new s.w.Event('change',{bubbles:true}));
 s.query('#lab-prompt').value='我改过的描述';
 s.query('#lab-seed').value='123';
 const steps=s.query('#lab-steps').value;
 await s.load();
 assert.equal(s.query('#lab-model').value,'krea2','刷新不能把用户选的模型换回去');
 assert.equal(s.query('#lab-prompt').value,'我改过的描述');
 assert.equal(s.query('#lab-seed').value,'123');
 assert.equal(s.query('#lab-steps').value,steps);
 assert.equal(s.query('#lab-model').options.length,2);
});

test('刷新状态按钮走同一条恢复路径，不需要整页重载',async t=>{
 const s=setup(t,{failures:1,jobs:[job(1)]});
 await s.load();
 assert.equal(s.query('#lab-model').options.length,0);
 s.query('#lab-refresh').click();
 await until(()=>s.query('#lab-model').options.length===1);
 assert.equal(s.run('lab.initialized'),true);
 assert.doesNotMatch(s.status(),/状态读取失败/);
});

// ── L1：已停止记录的管理入口与全选一致 ──────────────────────────────────
const FIVE=[job(1,'done'),job(2,'error'),job(3,'cancelled'),job(4,'running'),job(5,'uncertain')];

test('终态可管理判据只放行 done/error/cancelled，uncertain 不扩',async t=>{
 const s=setup(t,{jobs:FIVE});
 for(const status of ['done','error','cancelled'])assert.equal(s.run(`labManageable({status:'${status}'})`),true,status+' 应可管理');
 for(const status of ['running','queued','connecting','downloading','reconnecting','uncertain'])
  assert.equal(s.run(`labManageable({status:'${status}'})`),false,status+' 不应可管理');
});

test('已停止记录在历史行里可选可删，生成中与待核实仍然禁用',async t=>{
 const s=setup(t,{jobs:FIVE});
 s.run('renderLabHistory();');
 for(const n of [1,2,3]){
  const row=s.row(n),manage=row.querySelector('[data-manage]'),box=row.querySelector('[data-select]');
  assert.equal(box.disabled,false,`记录 ${n} 的 checkbox 要可用`);
  assert.equal(manage.disabled,false,`记录 ${n} 的删除按钮要可用`);
  assert.equal(manage.textContent,'删除');
  assert.equal(manage.getAttribute('title'),null);
  box.click();                                   // 真实点击：禁用控件不会触发 change
  assert.equal(s.run(`lab.selection.has('${id(n)}')`),true,`记录 ${n} 要能被选中`);
 }
 for(const n of [4,5]){
  const row=s.row(n),manage=row.querySelector('[data-manage]'),box=row.querySelector('[data-select]');
  assert.equal(box.disabled,true,`记录 ${n} 的 checkbox 要保持禁用`);
  assert.equal(manage.disabled,true,`记录 ${n} 的删除按钮要保持禁用`);
  assert.match(manage.getAttribute('title'),/生成完成后可删除/);
  box.click();
  assert.equal(s.run(`lab.selection.has('${id(n)}')`),false,`记录 ${n} 不能被选进批量操作`);
 }
});

test('历史行、全选和结果区共用同一份判据，已停止记录三处口径一致',async t=>{
 const s=setup(t,{jobs:FIVE});
 s.run('renderLabHistory();');
 const all=s.query('#lab-select-all');
 all.checked=true;all.onchange();
 assert.equal(s.selected(),JSON.stringify([id(1),id(2),id(3)].sort()),'全选要把已停止记录一起选上');
 assert.equal(s.query('#lab-batch-manage').disabled,false);
 assert.deepEqual([...s.w.document.querySelectorAll('[data-select]:checked')].map(e=>e.dataset.select).sort(),
  [id(1),id(2),id(3)].sort());
 // 只选一条已停止记录时，全选显示为半选而不是全选。
 s.run(`lab.selection=new Set(['${id(3)}']);lab.historySignature=null;renderLabHistory();`);
 assert.equal(s.query('#lab-select-all').checked,false);
 assert.equal(s.query('#lab-select-all').indeterminate,true);
 // 结果区（预览区）的删除/恢复按钮与历史行同一判据。
 for(const n of [1,2,3]){
  s.run(`renderLabJob(lab.jobs.find(j=>j.id==='${id(n)}'));`);
  assert.equal(s.query('#lab-delete-result').disabled,false,`结果区要能删除记录 ${n}`);
 }
 for(const n of [4,5]){
  s.run(`renderLabJob(lab.jobs.find(j=>j.id==='${id(n)}'));`);
  assert.equal(s.query('#lab-delete-result').disabled,true,`结果区不能删除记录 ${n}`);
 }
});

test('点已停止记录的删除会打开确认弹窗，取消不发出任何请求',async t=>{
 const s=setup(t,{jobs:FIVE});
 s.run('renderLabHistory();');
 s.row(3).querySelector('[data-manage]').click();
 assert.equal(s.query('#lab-delete-dialog').open,true,'已停止记录要能进入删除确认');
 assert.match(s.query('#lab-delete-message').textContent,/将删除 1 条生成记录/);
 s.query('#lab-delete-cancel').click();
 assert.equal(s.query('#lab-delete-dialog').open,false);
 assert.equal(s.calls.filter(call=>call.path==='/api/generation/manage').length,0,'取消不能发请求');
});

test('回收站里的已停止记录提供可用的「恢复」',async t=>{
 const s=setup(t,{jobs:[job(3,'cancelled',{deleted_at:'2026-10-11T02:00:00+00:00'})]});
 s.run('lab.trash=true;lab.jobs=fixtureJobs;lab.historySignature=null;renderLabHistory();');
 const manage=s.row(3).querySelector('[data-manage]');
 assert.equal(manage.textContent,'恢复');
 assert.equal(manage.disabled,false);
 assert.equal(s.row(3).querySelector('[data-select]').disabled,false);
 s.run('lab.trash=false;');
});
