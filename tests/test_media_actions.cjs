/* The stop / re-verify actions are part of the phase 3 cancel + recovery flow: the copy
   tells the user to press 重新核实, so the button must exist, be honest about when it is
   usable, and call the existing job routes without creating a new generation. */
const {test}=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');
const path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../public/media-progress.js'),'utf8');

class Element{
  constructor(tag){this.tag=tag;this.children=[];this.dataset={};this.textContent='';}
  append(...items){this.children.push(...items);}
  replaceChildren(...items){this.children=items;}
  setAttribute(k,v){this[k]=v;}
  removeAttribute(k){delete this[k];}
  find(className){return this.children.filter(c=>c.children.length).flatMap(c=>[c,...c.children]).find(c=>c.className===className);}
}
function setup(job, api){
  const calls=[];
  const context=vm.createContext({document:{createElement:tag=>new Element(tag)},
    api:api||(async(route,body)=>{calls.push({route,body});return job;}),
    setInterval:()=>1});
  vm.runInContext(source,context);
  vm.runInContext(`view=document.createElement('div');renderMediaProgress(view, ${JSON.stringify(job)})`,context);
  return {calls,view:vm.runInContext('view',context)};
}
const buttons=view=>({stop:view.find('media-action-stop'),check:view.find('media-action-reconcile')});

test('待核实任务提供可用的重新核实与停止按钮，并按原任务编号调用现有路由',async()=>{
  const job={id:'a'.repeat(32),status:'uncertain',transport:'https',created_at:new Date().toISOString(),
             connection_warning:'提交回执丢失'};
  const {view,calls}=setup(job);
  const {stop,check}=buttons(view);
  assert.ok(stop,'stop action exists');assert.ok(check,'reconcile action exists');
  assert.equal(check.disabled,false);assert.equal(stop.disabled,false);
  assert.match(check.title,/不会重复提交/);
  await check.onclick();
  assert.equal(calls.length,1);
  assert.equal(calls[0].route,`/api/generation/job/${job.id}/reconcile`);
  assert.deepEqual(Object.keys(calls[0].body),[]);   // no new generation payload
  assert.equal(stop.textContent,'停止任务');
});

test('终态与旧 SSH 通路诚实禁用，不给出无法执行的按钮',()=>{
  const done=setup({id:'b'.repeat(32),status:'done',transport:'https',created_at:new Date().toISOString()});
  const doneButtons=buttons(done.view);
  assert.equal(doneButtons.stop.disabled,true);assert.equal(doneButtons.check.disabled,true);
  assert.match(doneButtons.stop.title,/已结束/);

  const legacy=setup({id:'c'.repeat(32),status:'uncertain',transport:'legacy-ssh',created_at:new Date().toISOString()});
  const legacyButtons=buttons(legacy.view);
  assert.equal(legacyButtons.stop.disabled,true);assert.equal(legacyButtons.check.disabled,true);
  assert.match(legacyButtons.stop.title,/服务器侧核实/);
  assert.deepEqual(legacy.calls,[]);
});

test('动作失败只反馈错误，不禁用后失联也不伪造状态',async()=>{
  const job={id:'d'.repeat(32),status:'uncertain',transport:'https',created_at:new Date().toISOString()};
  const {view}=setup(job,async()=>{throw new Error('服务器拒绝了对账请求');});
  const {check}=buttons(view);
  await check.onclick();
  const feedback=view.find('media-action-feedback');
  assert.match(feedback.textContent,/服务器拒绝了对账请求/);
  assert.equal(check.disabled,false);
});

/* ---- shared phase rule + the panel behaviour that depends on it ---- */
const progressSource=source;
const generationSource=fs.readFileSync(path.join(__dirname,'../public/generation.js'),'utf8');

function panelHarness(api){
  const elements=new Map(),timers=[];
  class Stub{
    constructor(tag){this.tag=tag;this.children=[];this.dataset={};this.textContent='';this.hidden=false;this.disabled=false;this.innerHTML='';}
    find(className){return this.children.filter(c=>c.children.length).flatMap(c=>[c,...c.children]).find(c=>c.className===className);}
    append(...items){this.children.push(...items);}
    replaceChildren(...items){this.children=items;}
    setAttribute(k,v){this[k]=v;}removeAttribute(k){delete this[k];}
    addEventListener(){}
    querySelector(){return null;}
  }
  const $=selector=>{if(!elements.has(selector))elements.set(selector,new Stub());return elements.get(selector);};
  const context=vm.createContext({$,
    document:{createElement:tag=>new Stub(tag),querySelector:()=>null,querySelectorAll:()=>[]},
    api:api||(async()=>({models:[],jobs:[],preferences:{},image_skill:null})),
    escapeHTML:value=>String(value),renderMediaSize:()=>{},renderLoraFields:()=>{},updateMediaSizes:()=>{},
    renderImageStyleOptions:()=>{},updateCustomSize:()=>{},updateLoraEnabled:()=>{},selectedMediaSize:()=>'1024x1024',
    selectedLoraParameters:()=>({}),markMediaProgressUnavailable:()=>{},openAssistant:async()=>{},
    jumpToLatestAssistant:()=>{},toast:()=>{},saveMedia:()=>{},openMediaJob:()=>{},loadLibrary:async()=>{},
    state:{page:'assistant',article:null,slug:null,dirty:false},crypto:{randomUUID:()=>'x'},
    setInterval:()=>1,setTimeout:(fn,ms)=>{timers.push({fn,ms});return timers.length;},clearTimeout:()=>{}});
  vm.runInContext(progressSource,context);
  vm.runInContext(generationSource,context);
  return {context,timers,$,render:job=>vm.runInContext('renderMediaJob',context)(job),
          phase:job=>vm.runInContext('mediaJobPhase',context)(job),
          watch:job=>vm.runInContext('watchMedia',context)(job)};
}

test('阶段规则：已停止是终态，待核实可核实但不轮询',()=>{
  const {phase}=panelHarness();
  assert.equal(phase({status:'cancelled'}),'terminal');
  assert.equal(phase({status:'done'}),'terminal');
  assert.equal(phase({status:'error'}),'terminal');
  assert.equal(phase({status:'uncertain'}),'verifying');
  assert.equal(phase({status:'running'}),'active');
  assert.equal(phase({status:'reconnecting'}),'active');
});

test('停止任务后卡片允许重新生成，待核实仍保持谨慎不自动重提',()=>{
  const base={id:'e'.repeat(32),kind:'image',model_name:'Qwen',prompt:'湖面',status:'cancelled',
              settings:{size:'1024x1024',seed:1},created_at:new Date().toISOString(),transport:'https'};
  const stopped=panelHarness();
  stopped.render(base);
  assert.equal(stopped.$('#media-start').disabled,false,'a stopped job must allow a new generation');
  assert.match(stopped.$('#media-start').textContent,/重新生成/);

  const verifying=panelHarness();
  verifying.render({...base,status:'uncertain'});
  assert.equal(verifying.$('#media-start').disabled,true,'uncertain must not invite a blind resubmit');
  assert.equal(verifying.timers.length,0,'uncertain is not polled as an active job');

  const running=panelHarness();
  running.watch({...base,status:'running'});
  assert.equal(running.timers.length,1,'an active job keeps polling');
  assert.equal(running.$('#media-start').disabled,true);
});

test('核实成功后所属卡片恢复轮询并渲染最终结果，且绝不新建生成任务',async()=>{
  const job={id:'f'.repeat(32),kind:'image',model_name:'Qwen',prompt:'湖面',status:'uncertain',
             settings:{size:'1024x1024',seed:1},created_at:new Date().toISOString(),transport:'https'};
  const done={...job,status:'done',asset_url:'/media/x.png'};
  const routes=[];
  const api=async(route,options)=>{
    routes.push({route,method:(options&&options.method)||'GET'});
    if(route.endsWith('/reconcile'))return {...job,status:'reconnecting'};
    if(route==='/api/generation')return {jobs:[]};
    return done;
  };
  const panel=panelHarness(api);
  panel.render(job);
  assert.equal(panel.timers.length,0,'待核实任务不应自动轮询');
  assert.equal(panel.$('#media-start').disabled,true,'待核实时不得盲目允许重新提交');
  const check=panel.$('#media-task-progress').find('media-action-reconcile');
  assert.ok(check,'reconcile action exists on the panel card');
  await check.onclick();
  assert.equal(routes[0].route,`/api/generation/job/${job.id}/reconcile`,'核实必须用原任务编号');
  assert.equal(panel.timers.length,1,'核实后所属卡片必须恢复轮询');
  assert.equal(panel.$('#media-start').disabled,true,'仍在进行时不得允许重新提交');
  await panel.timers[0].fn();                       // the resumed card keeps the same job under watch
  assert.equal(panel.timers.length,2,'进行中继续轮询');
  await panel.timers[1].fn();                       // that poll queries the same job and gets the result
  assert.equal(routes[1].route,`/api/generation/job/${job.id}`,'轮询必须查同一任务编号');
  assert.equal(panel.$('#media-save-actions').hidden,false,'完成结果必须渲染出来');
  assert.equal(panel.timers.length,2,'终态后不再继续轮询');
  assert.equal(panel.$('#media-start').disabled,false,'终态后恢复开始按钮');
  assert.ok(!routes.some(row=>row.route==='/api/generation'&&row.method==='POST'),
            '恢复流程不得新建生成任务');
});
