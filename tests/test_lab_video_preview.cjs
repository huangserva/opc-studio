const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const {JSDOM}=require('jsdom');

const root=path.join(__dirname,'..');
const html=fs.readFileSync(path.join(root,'public/lab.html'),'utf8');
// the real lab page loads media-size.js and lab-image.js before lab.js; the harness must mirror it
const scripts=['lab-core.js','media-size.js','lab-management.js','lab-image.js','lab.js'].map(name=>({
  name,source:fs.readFileSync(path.join(root,'public',name),'utf8').replace(/\nrefreshLab\(true\);\s*$/,'\n')
}));
const model={id:'h3',name:'H3',kind:'video',sizes:['768x448'],steps:8,cfg:1};

function job(overrides={}){
  const id=overrides.id||'a'.repeat(32);
  return {
    id,kind:'video',status:'done',model:'h3',model_name:'H3',origin:'lab',
    prompt:'一只猫走过湖边',created_at:'2026-10-04T01:00:00Z',updated_at:'2026-10-04T01:01:00Z',
    asset_url:'/generation-assets/'+id,thumbnail_url:'/generation-thumbnails/'+id,
    settings:{size:'768x448',seed:42,frames:56,seconds:2.3,workflow:'t2v',workflow_name:'文生视频'},
    ...overrides
  };
}

function setup(t,jobs=[job()],{rejectPlay=false}={}){
  const dom=new JSDOM(html,{url:'http://127.0.0.1:8766/lab.html?kind=video',runScripts:'outside-only'});
  t.after(()=>dom.window.close());
  const w=dom.window,context=dom.getInternalVMContext();
  const playCalls=[],pauseCalls=[],scrollCalls=[],fetchCalls=[],timers=new Map(),mediaState=new WeakMap();
  let clicking=false,nextTimer=0,responseJobs=jobs;
  const state=media=>{
    if(!mediaState.has(media))mediaState.set(media,{paused:true});
    return mediaState.get(media);
  };
  Object.defineProperty(w.HTMLMediaElement.prototype,'paused',{configurable:true,get(){return state(this).paused;}});
  w.HTMLMediaElement.prototype.play=function(){
    playCalls.push({media:this,duringClick:clicking});
    if(rejectPlay)return Promise.reject(new Error('Playback blocked'));
    state(this).paused=false;
    return Promise.resolve();
  };
  w.HTMLMediaElement.prototype.pause=function(){pauseCalls.push(this);state(this).paused=true;};
  w.HTMLElement.prototype.scrollIntoView=function(options){scrollCalls.push({element:this,options});};
  w.HTMLDialogElement.prototype.showModal=function(){this.open=true;};
  w.HTMLDialogElement.prototype.close=function(){this.open=false;};
  w.setTimeout=(fn,delay)=>{timers.set(++nextTimer,{fn,delay});return nextTimer;};
  w.clearTimeout=id=>timers.delete(id);
  w.fetch=async(url,options)=>{
    fetchCalls.push({url,options});
    assert.match(url,/^\/api\/generation\/lab\?/,'isolated test must only poll the lab listing');
    return {ok:true,json:async()=>({models:[model],jobs:responseJobs,busy:responseJobs.some(j=>j.status==='running'),preferences:{video:{}},image_skill:{styles:[]}})};
  };
  w.renderMediaProgress=(host,value)=>{host.dataset.status=value.status;};
  // lab.html always loads media-progress.js before lab.js; these harnesses stub the widget
  w.attachMediaProgressHost=()=>{};
  w.markMediaProgressUnavailable=()=>{};
  w.configureVideo=()=>{};
  w.renderMediaSize=(_prefix,value,size)=>{
    w.document.querySelector('#media-size').innerHTML=`<option value="${size||value.sizes[0]}">${size||value.sizes[0]}</option>`;
  };
  w.renderLoraFields=()=>{};
  // media-size.js supplies the size and duration widgets on the real page; this harness
  // pins the size list, so the duration widget is stubbed to the same fixed value.
  w.renderMediaFrames=()=>{};
  w.selectedMediaFrames=()=>56;
  w.selectedMediaSize=()=>w.document.querySelector('#media-size').value;
  w.selectedLoraParameters=()=>({});
  w.videoParameters=()=>({workflow:'t2v',references:{},video_loras:[]});
  w.updateCustomSize=()=>{};
  w.updateLoraEnabled=()=>{};
  w.document.querySelector('#lab-model').innerHTML='<option value="h3">H3</option>';
  w.document.querySelector('#lab-workflow').innerHTML='<option value="t2v">文生视频</option>';
  for(const script of scripts)vm.runInContext(script.source,context,{filename:script.name});
  w.fixtureJobs=jobs;
  w.fixtureModel=model;
  const run=source=>vm.runInContext(source,context);
  run('lab.jobs=fixtureJobs;lab.models=[fixtureModel];renderLabHistory();');
  const query=selector=>w.document.querySelector(selector);
  return {
    w,run,query,playCalls,pauseCalls,scrollCalls,fetchCalls,timers,
    poll:async values=>{responseJobs=values;await run('refreshLab()');},
    click(element){assert.ok(element,'expected clickable element');clicking=true;try{element.click();}finally{clicking=false;}}
  };
}

test('视频历史卡片显示真实缩略图，点击立即在带控制器的主预览中播放',t=>{
  const value=job(),s=setup(t,[value]);
  const button=s.query('[data-preview="'+value.id+'"]'),thumbnail=button.querySelector('img[data-video-thumbnail]');
  assert.ok(thumbnail,'completed video must show its extracted frame');
  assert.equal(thumbnail.getAttribute('src'),value.thumbnail_url);
  assert.ok(button.textContent.trim(),'thumbnail has a visible play affordance');
  assert.equal(button.querySelector('video'),null,'history thumbnail is separate from the playback controls');
  assert.equal(s.playCalls.length,0,'loading history never starts playback');
  s.click(button);
  const video=s.query('#lab-preview video');
  assert.ok(video);
  assert.equal(video.getAttribute('src'),value.asset_url);
  assert.equal(video.controls,true);
  assert.equal(video.playsInline,true);
  assert.equal(video.getAttribute('poster'),value.thumbnail_url);
  assert.equal(video.paused,false);
  assert.equal(s.playCalls.length,1);
  assert.equal(s.playCalls[0].duringClick,true,'play request retains the click user activation');
  assert.equal(s.playCalls[0].media,video);
  assert.equal(s.scrollCalls.at(-1).element,s.query('.lab-result'));
  assert.equal(s.run('lab.selected.id'),value.id);
});

test('轮询更新其他任务和重绘历史不会重建播放器、重复播放或重置播放位置',async t=>{
  const done=job(),running=job({id:'b'.repeat(32),status:'running',asset_url:undefined,thumbnail_url:undefined});
  const s=setup(t,[done,running]);
  s.click(s.query('[data-preview="'+done.id+'"]'));
  const video=s.query('#lab-preview video');
  video.currentTime=1.25;
  await s.poll([{...done,updated_at:'2026-10-04T01:02:00Z'},
    {...running,progress:{phase:'sampling',current:3,total:8},updated_at:'2026-10-04T01:02:00Z'}]);
  assert.equal(s.fetchCalls.length,1);
  assert.equal(s.query('#lab-preview video'),video);
  assert.equal(video.currentTime,1.25);
  assert.equal(video.paused,false);
  assert.equal(s.playCalls.length,1);
  assert.equal(s.pauseCalls.length,0);
  s.run('renderLabJob({...lab.selected,updated_at:"2026-10-04T01:03:00Z"});');
  assert.equal(s.query('#lab-preview video'),video);
  assert.equal(video.currentTime,1.25);
  assert.equal(s.playCalls.length,1);
});

test('下载、选择、对比、沿用和删除记录不触发视频播放',t=>{
  const value=job(),s=setup(t,[value]),card=s.query('.lab-history-item');
  const download=card.querySelector('a[download]');
  download.addEventListener('click',event=>event.preventDefault());
  assert.equal(download.getAttribute('href'),value.asset_url);
  s.click(download);
  const select=card.querySelector('[data-select]');
  s.click(select);
  assert.equal(select.checked,true);
  assert.equal(s.run('lab.selection.has(fixtureJobs[0].id)'),true);
  const compare=card.querySelector('[data-compare]');
  s.click(compare);
  assert.equal(s.run('lab.compare.has(fixtureJobs[0].id)'),true);
  assert.ok(s.query('#lab-compare video'),'comparison creates a controllable video without autoplay');
  s.click(card.querySelector('[data-refill]'));
  assert.equal(s.query('#lab-prompt').value,value.prompt);
  assert.equal(s.query('#lab-seed').value,'42');
  s.click(card.querySelector('[data-manage]'));
  assert.equal(s.query('#lab-delete-dialog').open,true);
  assert.equal(s.playCalls.length,0);
  assert.equal(s.scrollCalls.length,0,'management operations do not jump to the preview');
});

test('坏缩略图被隐藏，但历史按钮仍能打开和播放原视频',t=>{
  const value=job(),s=setup(t,[value]);
  const button=s.query('[data-preview]'),thumbnail=button.querySelector('img[data-video-thumbnail]');
  assert.ok(thumbnail);
  thumbnail.dispatchEvent(new s.w.Event('error'));
  assert.ok(!thumbnail.isConnected||thumbnail.hidden||thumbnail.style.display==='none','failed thumbnail is hidden');
  assert.equal(button.disabled,false);
  s.click(button);
  assert.equal(s.query('#lab-preview video').getAttribute('src'),value.asset_url);
  assert.equal(s.playCalls.length,1);
});

test('浏览器拒绝播放时提示用户，并保留手动播放控制',async t=>{
  const s=setup(t,[job()],{rejectPlay:true});
  s.click(s.query('[data-preview]'));
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(s.playCalls.length,1);
  assert.equal(s.query('#lab-preview video').controls,true);
  assert.equal(s.query('#lab-preview video').paused,true);
  assert.ok(s.query('#toast').textContent.trim(),'play rejection provides a useful fallback message');
});

test('未完成和失败记录只显示状态，不创建缩略图或播放器',t=>{
  const running=job({status:'running',asset_url:undefined,thumbnail_url:undefined});
  const failed=job({id:'b'.repeat(32),status:'error',asset_url:undefined,thumbnail_url:undefined,error:'生成失败'});
  const s=setup(t,[running,failed]);
  assert.equal(s.query('#lab-history-list img[data-video-thumbnail]'),null);
  for(const value of [running,failed]){
    s.click(s.query('[data-preview="'+value.id+'"]'));
    assert.equal(s.query('#lab-preview video'),null);
  }
  assert.equal(s.playCalls.length,0);
});
