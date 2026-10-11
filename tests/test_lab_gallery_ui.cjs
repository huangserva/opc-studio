/* The video page picks a first frame from the image generation history (jsdom).

   The page must never download and re-upload the picture: it names the generation
   record, the server answers with a reference asset, and that reference behaves like
   an uploaded one in the parameters, the draft and the preview. It also pins the
   history card entry point and the readable failure when the picture was deleted.

   Run: node --test tests/test_lab_gallery_ui.cjs
*/
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const {JSDOM,VirtualConsole}=require('jsdom');

const root=path.join(__dirname,'..');
const html=fs.readFileSync(path.join(root,'public/lab.html'),'utf8');
const id=n=>n.toString(16).padStart(32,'0');
const imageJob=(n,{status='done',origin='lab',prompt=`黄昏湖边的柴犬 ${n}`,deleted=false,trash=false}={})=>({
 id:id(n),kind:'image',status,origin,model:'qi21',model_name:'Qwen Image 2.1',created_at:'2026-10-08T02:0'+n+':00+00:00',
 prompt,asset_url:'/generation-assets/'+id(n),settings:{size:'1024x1024',seed:n,steps:20},deleted_at:trash?deleted:null});
const videoJob=n=>({id:id(n),kind:'video',status:'done',origin:'lab',model:'h3',model_name:'H3',
 created_at:'2026-10-08T03:0'+n+':00+00:00',prompt:'湖边走动',asset_url:'/generation-assets/'+id(n),settings:{size:'768x448',seed:n}});
const model={id:'h3',name:'H3',kind:'video',sizes:['768x448','1024x576'],steps:8,
 workflows:[{id:'t2v',name:'文生视频'},{id:'i2v',name:'图生视频 · 首帧'},{id:'fl2v',name:'首尾帧生视频'},{id:'ir2v',name:'首帧 + 多参考素材'}],
 checkpoints:[{id:'ref',name:'Reference',modes:['t2v','i2v','fl2v','ir2v']}],
 accelerators:[{id:'ref8',name:'Reference 8',steps:8,modes:['t2v','i2v','fl2v','ir2v']}],video_loras:[]};
const imageModel={id:'qi21',name:'Qwen Image 2.1',kind:'image',sizes:['1024x1024'],steps:20};
const settle=()=>new Promise(resolve=>setImmediate(resolve));
async function until(check){for(let count=0;count<30&&!check();count++)await settle();assert.ok(check(),'asynchronous fixture did not reach expected state');}

function setup(t,{kind='video',frameFrom='',jobs=[],imageJobs=[],referenceError=null,referenceId=id(900),uniqueReferences=false}={}){
 const query=new URLSearchParams({kind});if(frameFrom)query.set('frame_from',frameFrom);
 const virtualConsole=new VirtualConsole();               // navigation attempts are expected here
 const dom=new JSDOM(html,{url:`http://127.0.0.1:8766/lab.html?${query}`,runScripts:'outside-only',virtualConsole});
 t.after(()=>dom.window.close());
 const w=dom.window,context=dom.getInternalVMContext(),calls=[],known=new Map();let nextId=100,nextTimer=0,referenceCount=0;
 w.setTimeout=()=>++nextTimer;w.clearTimeout=()=>{};
 w.renderLoraFields=()=>{};w.selectedLoraParameters=()=>({});w.updateLoraEnabled=()=>{};
 w.renderMediaProgress=()=>{};w.markMediaProgressUnavailable=()=>{};w.attachMediaProgressHost=()=>{};
 w.HTMLElement.prototype.scrollIntoView=()=>{};w.HTMLMediaElement.prototype.pause=()=>{};
 w.HTMLDialogElement.prototype.showModal=function(){this.open=true;};
 w.HTMLDialogElement.prototype.close=function(){this.open=false;};
 w.fetch=async(url,options={})=>{
  const parsed=new URL(url,w.location.href),call={url,options,path:parsed.pathname,query:parsed.searchParams};
  if(typeof options.body==='string')call.payload=JSON.parse(options.body);
  calls.push(call);
  let data;
  if(call.path==='/api/generation/lab'){
   const trash=call.query.get('trash')==='1';
   data={models:[kind==='video'?model:imageModel],jobs:(call.query.get('kind')==='image'?(w.fixtureImageJobs||imageJobs):jobs).filter(job=>Boolean(job.deleted_at)===trash),
    busy:false,preferences:{video:{model:'h3'},image:{model:'qi21'}},image_skill:{styles:[]}};
  }else if(call.path==='/api/generation/reference-from-job'){
   if(referenceError)return {ok:false,json:async()=>({error:referenceError})};
   const chosen=uniqueReferences?id(900+ ++referenceCount):referenceId;
   data={id:chosen,kind:'image',url:'/generation-references/'+chosen,name:'黄昏湖边的柴犬',width:1024,height:1024,
    mime:'image/png',source:call.payload.job_id,source_job:call.payload.job_id};
  }else if(call.path.startsWith('/api/generation/reference/'))data=known.get(call.path.split('/').at(-1));
  else if(call.path==='/api/generation/references')data={id:id(nextId++),kind:'image',url:'/generation-references/'+id(nextId),name:'uploaded.png',width:640,height:360};
  else throw new Error('Unexpected test request '+url);
  if(data?.id)known.set(data.id,data);
  return {ok:true,json:async()=>data};
 };
 for(const name of ['lab-core.js','media-size.js','lab-video.js','lab-management.js','lab-image.js','lab.js']){
  const source=fs.readFileSync(path.join(root,'public',name),'utf8').replace(/\nrefreshLab\(true\);\s*$/,'\n');
  vm.runInContext(source,context,{filename:name});
 }
 const run=code=>vm.runInContext(code,context),query2=selector=>w.document.querySelector(selector);
 w.fixtureModel=kind==='video'?model:imageModel;run(`lab.models=[fixtureModel];lab.jobs=${JSON.stringify(kind==='video'?jobs:jobs.concat(imageJobs))};`);
 query2('#lab-model').innerHTML=`<option value="${kind==='video'?'h3':'qi21'}">fixture</option>`;
 query2('#lab-prompt').value='一只猫在湖边走动';
 if(kind==='video'){w.fixtureSettings={workflow:'t2v'};run('configureLab(fixtureSettings);');}
 else{w.fixtureSettings={};run('configureLab(fixtureSettings);');}
 return {w,run,query:query2,calls,known,
  params:()=>JSON.parse(run('JSON.stringify(labParameters())')),
  storage:()=>w.localStorage.getItem('studio-generation-lab-v1-'+kind),
  switchMode:mode=>{query2('#lab-workflow').value=mode;query2('#lab-workflow').dispatchEvent(new w.Event('change',{bubbles:true}));},
  openGallery:slot=>{const button=query2('[data-video-gallery="'+slot+'"]');assert.ok(button,'expected a gallery button for '+slot);button.onclick();},
  pick:jobId=>{const card=w.document.querySelector('#lab-gallery-dialog [data-gallery-pick="'+jobId+'"]');assert.ok(card,'expected a gallery card for '+jobId);return card.onclick();},
  cards:()=>[...w.document.querySelectorAll('#lab-gallery-dialog [data-gallery-pick]')],
  toast:()=>query2('#toast').textContent,
  load:()=>run('refreshLab(true);')};
}

test('首帧旁提供“从图库选择”，弹窗只列已完成图片并显示缩略图、提示词、模型、时间与来源',async t=>{
 const s=setup(t,{imageJobs:[imageJob(1),imageJob(2,{origin:'assistant',prompt:'Krea2 海报草图'}),
  imageJob(3,{status:'running'}),imageJob(4,{trash:true,deleted:'2026-10-08T04:00:00+00:00'})]});
 s.switchMode('i2v');
 s.openGallery('first');
 assert.equal(s.query('#lab-gallery-dialog').open,true);
 await until(()=>s.cards().length===2);
 const [first,second]=s.cards();
 assert.match(first.querySelector('img').getAttribute('src'),/^\/generation-assets\//);
 assert.match(first.textContent,/黄昏湖边的柴犬/);
 assert.match(first.textContent,/Qwen Image 2.1 · 1024x1024/);
 assert.match(first.textContent,/独立页/);
 assert.match(second.textContent,/AI 助手/);
 assert.match(s.query('#lab-gallery-count').textContent,/已完成图片 2 张/);
 assert.ok(!s.calls.some(call=>call.path==='/api/generation/references'),'picking from the library must not upload a copy');
});

test('弹窗搜索只在已加载的已完成图片里过滤',async t=>{
 const s=setup(t,{imageJobs:[imageJob(1,{prompt:'黄昏湖边的柴犬'}),imageJob(2,{prompt:'雪山公路海报'})]});
 s.switchMode('i2v');
 s.openGallery('first');
 await until(()=>s.cards().length===2);
 const search=s.query('#lab-gallery-search');search.value='海报';search.oninput({target:search});
 assert.deepEqual(s.cards().map(card=>card.dataset.galleryPick),[id(2)]);
 search.value='不存在的关键词';search.oninput({target:search});
 assert.equal(s.cards().length,0);
 assert.match(s.query('#lab-gallery-list').textContent,/没有匹配的已完成图片/);
});

test('点选图库图片只提交生成记录编号，并立即成为首帧、进入预览与提交参数',async t=>{
 const s=setup(t,{imageJobs:[imageJob(1)]});
 s.switchMode('i2v');
 s.openGallery('first');
 await until(()=>s.cards().length===1);
 await s.pick(id(1));
 const request=s.calls.find(call=>call.path==='/api/generation/reference-from-job');
 assert.deepEqual(request.payload,{job_id:id(1)});
 assert.equal(s.query('#lab-gallery-dialog').open,false);
 const tile=s.query('.video-reference-tile img');
 assert.equal(tile.getAttribute('src'),'/generation-references/'+id(900));
 assert.equal(tile.getAttribute('alt'),'首帧图片 · <Picture 1>预览');
 assert.deepEqual(s.params().references,{first:id(900)});
 assert.equal(s.params().reference_media[id(900)].kind,'image');
 assert.equal(s.params().reference_media[id(900)].width,1024);
 assert.match(s.storage(),/"first":"[0-f]{32}"/);
});

test('图库首帧写进草稿，重新载入后仍作为首帧引用而不是重新上传',async t=>{
 const s=setup(t,{imageJobs:[imageJob(1)]});
 s.run('lab.models=[fixtureModel];');
 s.query('#lab-model').innerHTML='<option value="h3">H3</option>';
 s.run('configureLab({workflow:"i2v"});');
 s.openGallery('first');
 await until(()=>s.cards().length===1);
 await s.pick(id(1));
 const draft=s.storage();
 s.run('hydrateVideoInputs(JSON.parse(localStorage.getItem("studio-generation-lab-v1-video")));');
 assert.deepEqual(s.params().references,{first:id(900)});
 assert.equal(s.query('.video-reference-tile img').getAttribute('src'),'/generation-references/'+id(900));
 assert.ok(draft.includes(id(900)));
});

test('“做成视频”跳转参数预填首帧、清掉地址参数，并且重复载入不会再次登记',async t=>{
 const s=setup(t,{frameFrom:id(7),imageJobs:[imageJob(1)]});
 await s.load();
 await until(()=>s.calls.some(call=>call.path==='/api/generation/reference-from-job'));
 assert.deepEqual(s.calls.filter(call=>call.path==='/api/generation/reference-from-job').map(call=>call.payload),[{job_id:id(7)}]);
 assert.deepEqual(s.params().references,{first:id(900)});
 assert.equal(new URL(s.w.location.href).searchParams.get('frame_from'),null);
 assert.equal(new URL(s.w.location.href).searchParams.get('kind'),'video');
 // A second load has no parameter left to consume, so nothing is registered again.
 await s.load();await settle();
 assert.equal(s.calls.filter(call=>call.path==='/api/generation/reference-from-job').length,1);
});

test('文生视频模式收到图库首帧时先切到需要首帧的模式，再预填',async t=>{
 const s=setup(t,{frameFrom:id(7)});
 assert.equal(s.query('#lab-workflow').value,'t2v');
 await s.load();
 await until(()=>s.calls.some(call=>call.path==='/api/generation/reference-from-job'));
 assert.equal(s.query('#lab-workflow').value,'i2v');
 assert.deepEqual(s.params().references,{first:id(900)});
});

test('图片完成记录提供“做成视频”跳转，视频记录与回收站不提供',async t=>{
 const s=setup(t,{kind:'image',jobs:[imageJob(1),videoJob(2)]});
 s.run('renderLabHistory();');
 const buttons=[...s.w.document.querySelectorAll('[data-video-from]')];
 assert.deepEqual(buttons.map(button=>button.dataset.videoFrom),[id(1)]);
 assert.equal(s.run('videoFrameLocation({id:'+JSON.stringify(id(1))+'})'),'/lab.html?kind=video&frame_from='+id(1));
 s.run('lab.trash=true;renderLabHistory();');
 assert.deepEqual([...s.w.document.querySelectorAll('[data-video-from]')],[]);
 s.run('lab.trash=false;lab.historySignature=null;renderLabHistory();');
 const [card]=s.w.document.querySelectorAll('[data-video-from]');
 assert.equal(typeof card.onclick,'function');
 card.onclick();                                  // navigation is attempted; jsdom does not browse
 assert.equal(s.run('typeof startVideoFromImage'),'function');
});

test('图库首帧可以移除并改回本机上传，草稿同步清掉引用',async t=>{
 const s=setup(t,{imageJobs:[imageJob(1)]});
 s.switchMode('i2v');
 s.openGallery('first');
 await until(()=>s.cards().length===1);
 await s.pick(id(1));
 assert.deepEqual(s.params().references,{first:id(900)});
 const remove=[...s.w.document.querySelectorAll('.video-reference-tile button')].find(button=>button.textContent==='移除');
 remove.onclick();
 assert.deepEqual(s.params().references,{});
 assert.equal(s.query('.video-reference-tile'),null);
 assert.match(s.storage(),/"references":\{\}/);
 // The local picker stays on the same slot, so the existing upload path is untouched.
 assert.ok(s.query('[data-video-upload="first"]'));
 assert.ok(s.query('[data-video-gallery="first"]'));
});

test('图库图片已被删除时给出可读错误，当前首帧保持不变',async t=>{
 const s=setup(t,{imageJobs:[imageJob(1)],referenceError:'这张图片已在回收站，请先恢复后再使用'});
 s.run('configureLab({workflow:"i2v"});');
 s.openGallery('first');
 await until(()=>s.cards().length===1);
 await s.pick(id(1));
 assert.match(s.toast(),/回收站/);
 assert.equal(s.params().references.first,undefined);
 assert.equal(s.query('.video-reference-tile'),null);
});

test('多参考素材模式的参考图片也可从图库选择，并守住含首尾帧的图片上限',async t=>{
 const s=setup(t,{uniqueReferences:true,imageJobs:[imageJob(1),imageJob(2),imageJob(3),imageJob(4),imageJob(5),imageJob(6),imageJob(7),imageJob(8),imageJob(9),imageJob(10)]});
 s.switchMode('ir2v');
 s.openGallery('first');
 await until(()=>s.cards().length===10);
 await s.pick(id(1));
 assert.deepEqual(s.params().references,{first:id(901)});
 s.openGallery('images');
 await until(()=>s.cards().length===10);
 for(const job of [2,3,4,5,6,7,8,9]){
  await until(()=>s.cards().some(card=>card.dataset.galleryPick===id(job)));
  await s.pick(id(job));
 }
 // ir2v keeps room for the anchor: nine images in total, so the tenth image is
 // refused before any extra copy is registered on the server.
 await until(()=>s.params().references.images.length===8);
 await s.pick(id(10));
 assert.match(s.toast(),/最多添加 8 张参考图|图片总数最多 9 张/);
 assert.equal(s.params().references.images.length,8);
 assert.equal(s.calls.filter(call=>call.path==='/api/generation/reference-from-job').length,9);
 assert.equal(s.params().reference_media[id(909)].kind,'image');
});
