/* Workbench polling must preserve usable buttons and immutable task bindings. */
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs');
const {JSDOM}=require('jsdom');
const source=fs.readFileSync('public/local-publishing.js','utf8');
const tick=()=>new Promise(resolve=>setImmediate(resolve));
function fixture(){
  const dom=new JSDOM('<main><button id="export-package">导出</button></main>',{url:'http://127.0.0.1:8766/?article=fixture',runScripts:'outside-only'});
  const w=dom.window,intervals=[],calls=[],messages=[];
  let deferAccount=null,resolveRun,deferStart=false,resolveStart;
  const pendingAccounts=[];
  const capacity=new Map();
  const job={id:'a'.repeat(32),account:'demo_account',status:'blocked',message:'等待续传',version:'v0001',updated_at:'2026-10-04',checkpoint:{}};
  let jobs=[job];
  w.$=selector=>w.document.querySelector(selector);
  w.state={slug:'fixture',article:{version:{id:'v0001'}},dirty:false};
  w.escapeHTML=text=>String(text).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  w.getPlatformDraft=()=>({account:'demo_account'});
  w.versionName=value=>value;w.date=value=>value;w.toast=message=>messages.push(message);
  w.setInterval=(fn,delay)=>{intervals.push({fn,delay});return intervals.length;};w.clearInterval=()=>{};
  w.api=async(path,payload)=>{
    calls.push({path,payload});
    if(path.includes('/preflight')){
      const account=new URL(path,w.location.href).searchParams.get('account');
      if(account===deferAccount)return new Promise(resolve=>pendingAccounts.push({account,resolve}));
      return {account,source_images:4,needs_split:false,...capacity.get(account)};
    }
    if(path.includes('/jobs?'))return jobs;
    if(path.includes('/bundle/'))return {job:jobs.find(j=>j.id===path.split('/').at(-1)),images:[]};
    if(path.includes('/start/')){
      if(deferStart)return new Promise(resolve=>resolveStart=resolve);
      let target=jobs.find(j=>j.account===payload.account);
      if(!target){target={...job,id:'b'.repeat(32),account:payload.account,status:'queued',checkpoint:{}};jobs.push(target);}
      return target;
    }
    return {};
  };
  w.eval(source);
  w.localBridge=async(command,payload)=>{
    calls.push({command,payload});
    if(command==='ping')return {connected:true,version:'0.1.33'};
    return new Promise(resolve=>resolveRun=resolve);
  };
  return {w,calls,messages,job,setCapacity:(account,value)=>capacity.set(account,value),setJobs:value=>jobs=value,close:()=>dom.window.close(),mount:()=>w.mountLocalPublishing(),
    poll:()=>intervals.find(x=>x.delay===2000).fn(),defer:account=>deferAccount=account,
    deferStart:()=>deferStart=true,finishStart:()=>resolveStart(job),
    settleAccount:value=>{for(const pending of pendingAccounts)if(pending.account===value.account)pending.resolve(value);},finishRun:()=>resolveRun({ok:true})};
}
test('unchanged polling preserves focused resume button and its handlers',async()=>{
  const f=fixture();try{
    await f.mount();const button=f.w.document.querySelector('[data-resume]');button.focus();
    await f.poll();await tick();
    assert.equal(f.w.document.querySelector('[data-resume]'),button);
    assert.equal(f.w.document.activeElement,button);
    assert.equal(typeof button.onclick,'function');
  }finally{f.close();}
});
test('running task blocks duplicate resume and restores controls after completion',async()=>{
  const f=fixture();try{
    await f.mount();const button=f.w.document.querySelector('[data-resume]');
    button.click();await tick();await tick();
    assert.equal(button.disabled,true);
    assert.equal(f.w.document.querySelector('#local-x-start').disabled,true);
    button.click();await tick();
    assert.equal(f.calls.filter(x=>x.command==='run').length,1);
    f.finishRun();await tick();await tick();
    assert.equal(button.disabled,false);
  }finally{f.close();}
});
test('slow old-account preflight cannot replace the current-account result',async()=>{
  const f=fixture();try{
    await f.mount();const account=f.w.document.querySelector('#local-x-account');f.defer('old');
    account.value='old';account.dispatchEvent(new f.w.Event('change'));await tick();
    account.value='new';account.dispatchEvent(new f.w.Event('change'));await tick();
    f.settleAccount({account:'old',source_images:99,needs_split:true,limit:{body_images:2}});await tick();
    assert.equal(f.w.document.querySelector('#local-x-start').disabled,false);
    assert.equal(f.w.document.querySelector('#local-x-adapt'),null);
    assert.match(f.w.document.querySelector('#local-x-preflight').textContent,/正文 4 张/);
  }finally{f.close();}
});
test('changing target hides old-account actions and starts an independent task for the selected account',async()=>{
  const f=fixture();try{
    await f.mount();const account=f.w.document.querySelector('#local-x-account');
    account.value='demo_account_ai';account.dispatchEvent(new f.w.Event('input'));await tick();await tick();
    assert.equal(f.w.document.querySelector('[data-resume]'),null);
    assert.equal(f.w.document.querySelector('[data-confirm]'),null);
    assert.match(f.w.document.querySelector('.local-other-accounts').textContent,/@demo_account/);
    assert.match(f.w.document.querySelector('#local-x-start').textContent,/@demo_account_ai/);
    f.w.document.querySelector('#local-x-start').click();await tick();await tick();
    assert.equal(f.calls.find(x=>x.path?.includes('/start/')).payload.account,'demo_account_ai');
    assert.equal(f.calls.find(x=>x.command==='run').payload.job.account,'demo_account_ai');
    assert.equal(f.job.account,'demo_account');
    assert.equal(account.disabled,true);
    f.finishRun();await tick();await tick();
    assert.equal(account.disabled,false);
    assert.equal(f.w.document.querySelectorAll('[data-resume]').length,1);
    assert.equal(f.w.document.querySelector('[data-resume]').dataset.resume,'b'.repeat(32));
  }finally{f.close();}
});
test('stale resume handler cannot execute another account while replacement UI is loading',async()=>{
  const f=fixture();try{
    await f.mount();const stale=f.w.document.querySelector('[data-resume]').onclick;
    const account=f.w.document.querySelector('#local-x-account');f.defer('demo_account_ai');
    account.value='demo_account_ai';account.dispatchEvent(new f.w.Event('input'));
    stale();await tick();await tick();
    assert.equal(f.calls.filter(x=>x.command==='run').length,0);
    f.settleAccount({account:'demo_account_ai',source_images:4,needs_split:false});await tick();await tick();
    assert.match(f.messages.at(-1),/目标账号已改变/);
  }finally{f.close();}
});
test('target preference survives remount and normalizes a typed handle',async()=>{
  const f=fixture();try{
    await f.mount();const account=f.w.document.querySelector('#local-x-account');
    account.value=' @DeMo_AcCoUnT_AI ';account.dispatchEvent(new f.w.Event('input'));await tick();
    assert.equal(f.w.localStorage.getItem('article-studio:x-target:fixture'),'demo_account_ai');
    assert.match(f.w.document.querySelector('#local-x-start').textContent,/@demo_account_ai/);
    f.w.document.querySelector('.local-publishing').remove();await f.mount();
    assert.equal(f.w.document.querySelector('#local-x-account').value,'demo_account_ai');
    assert.equal(f.w.document.querySelector('[data-resume]'),null);
  }finally{f.close();}
});
test('polling cannot unlock target selection or duplicate start during task creation',async()=>{
  const f=fixture();try{
    await f.mount();f.deferStart();const start=f.w.document.querySelector('#local-x-start');
    start.click();await tick();await f.poll();await tick();
    assert.equal(start.disabled,true);
    assert.equal(f.w.document.querySelector('#local-x-account').disabled,true);
    start.click();f.w.document.querySelector('[data-resume]').onclick();await tick();
    assert.equal(f.calls.filter(x=>x.path?.includes('/start/')).length,1);
    assert.equal(f.calls.filter(x=>x.command==='run').length,0);
    f.finishStart();await tick();await tick();f.finishRun();await tick();await tick();
    assert.equal(f.w.document.querySelector('#local-x-account').disabled,false);
  }finally{f.close();}
});
test('switching target rejects a stale publish confirmation and hides every old-account action',async()=>{
  const f=fixture();try{
    f.job.status='review_ready';f.job.draft_url='https://x.com/compose/articles/edit/123';
    f.setJobs([f.job,{...f.job,id:'c'.repeat(32),status:'blocked',error_code:'DRAFT_UNAVAILABLE'}]);
    await f.mount();const stale=f.w.document.querySelector('[data-confirm]').onclick;
    const account=f.w.document.querySelector('#local-x-account');
    account.value='demo_account_ai';account.dispatchEvent(new f.w.Event('input'));await tick();
    await stale();await tick();
    assert.equal(f.w.document.querySelector('dialog'),null);
    assert.equal(f.w.document.querySelector('[data-confirm]'),null);
    assert.equal(f.w.document.querySelector('[data-new-draft]'),null);
    assert.equal(f.w.document.querySelector('[data-resume]'),null);
    assert.equal(f.calls.filter(x=>x.payload?.action==='confirm').length,0);
    assert.match(f.messages.at(-1),/目标账号已改变/);
  }finally{f.close();}
});
test('known over-limit source warns before upload and offers no split or retry path',async()=>{
  const f=fixture();try{
    f.setCapacity('demo_account',{source_images:29,over_limit:true,needs_split:true,limit:{body_images:25}});
    await f.mount();
    const alert=f.w.document.querySelector('#local-x-preflight [role=alert]');
    assert.match(alert.textContent,/正文 29 张/);assert.match(alert.textContent,/上限 25 张/);assert.match(alert.textContent,/超出 4 张/);
    assert.match(alert.textContent,/分批上传不能增加单篇文章容量/);
    assert.equal(f.w.document.querySelector('#local-x-start').disabled,true);
    assert.equal(f.w.document.querySelector('[data-resume]').disabled,true);
    assert.equal(f.w.document.querySelector('#local-x-adapt'),null);
    await f.w.document.querySelector('#local-x-start').onclick();
    await f.w.document.querySelector('[data-resume]').onclick();await tick();
    assert.equal(f.calls.filter(x=>x.path?.includes('/start/')).length,0);
    assert.equal(f.calls.filter(x=>x.command==='run').length,0);
  }finally{f.close();}
});
test('a limit learned after rendering blocks a stale resume before any X command',async()=>{
  const f=fixture();try{
    await f.mount();const stale=f.w.document.querySelector('[data-resume]').onclick;
    f.setCapacity('demo_account',{source_images:29,over_limit:true,needs_split:true,limit:{body_images:25}});
    await stale();await tick();
    assert.equal(f.calls.filter(x=>x.command==='run').length,0);
    assert.match(f.messages.at(-1),/图片数量超限/);
  }finally{f.close();}
});
test('unknown account labels its limit uncertainty and warns using scoped reference evidence',async()=>{
  const f=fixture();try{
    f.setCapacity('demo_account',{source_images:29,reference_limit:{account:'another',body_images:25}});
    await f.mount();const preflight=f.w.document.querySelector('#local-x-preflight');
    assert.match(preflight.textContent,/此账号图片上限尚未验证/);
    assert.match(preflight.querySelector('[role=alert]').textContent,/@another 实测的 25 张/);
    assert.match(preflight.querySelector('[role=alert]').textContent,/@demo_account 的上限尚未验证/);
    assert.equal(f.w.document.querySelector('#local-x-start').disabled,false);
  }finally{f.close();}
});
