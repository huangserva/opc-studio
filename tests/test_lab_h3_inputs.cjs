const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const {JSDOM}=require('jsdom');

const root=path.join(__dirname,'..');
const html=fs.readFileSync(path.join(root,'public/lab.html'),'utf8');
const modeIds=['t2v','i2v','fl2v','r2v','ir2v','flr2v'];
const model={id:'h3',name:'H3',kind:'video',sizes:['768x432','768x448','448x768'],steps:8,
 workflows:modeIds.map(id=>({id,name:id,validation:{label:id==='ir2v'?'待实测':'已实测'}})),
 checkpoints:[{id:'ref',name:'Reference',modes:['t2v','i2v','r2v','ir2v']},{id:'hybrid',name:'Hybrid',modes:modeIds}],
 accelerators:[{id:'ref8',name:'Reference 8',steps:8,modes:['t2v','i2v','r2v','ir2v']},{id:'all',name:'All',steps:25,modes:modeIds}],
 video_loras:[{id:'motion',name:'Motion'}]};
const id=n=>n.toString(16).padStart(32,'0');
const metadata=(n,kind='image',extra={})=>({id:id(n),kind,url:'/generation-references/'+id(n),name:`fixture-${n}.${kind==='image'?'png':kind==='video'?'mp4':'wav'}`,
 ...(kind==='image'?{width:640,height:360}:{duration:4,frames:96,has_audio:false}),...extra});
const deferred=()=>{let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no;});return {promise,resolve,reject};};
const settle=()=>new Promise(resolve=>setImmediate(resolve));
async function until(check){for(let count=0;count<20&&!check();count++)await settle();assert.ok(check(),'asynchronous fixture did not reach expected state');}

function setup(t,{request,draft}={}){
 const dom=new JSDOM(html,{url:'http://127.0.0.1:8766/lab.html?kind=video',runScripts:'outside-only'});
 t.after(()=>dom.window.close());
 const w=dom.window,context=dom.getInternalVMContext(),requests=[],known=new Map();let nextId=100,nextTimer=0;
 w.setTimeout=()=>++nextTimer;w.clearTimeout=()=>{};
 w.renderLoraFields=()=>{};w.selectedLoraParameters=()=>({});
 w.renderMediaProgress=()=>{};w.markMediaProgressUnavailable=()=>{};
 // lab.html always loads media-progress.js before lab.js; these harnesses stub the widget
 w.attachMediaProgressHost=()=>{};
 w.HTMLElement.prototype.scrollIntoView=()=>{};
 w.HTMLMediaElement.prototype.pause=()=>{};
 w.HTMLDialogElement.prototype.showModal=function(){this.open=true;};
 w.HTMLDialogElement.prototype.close=function(){this.open=false;};
 w.fetch=async(url,options={})=>{
  const parsed=new URL(url,w.location.href),call={url,options,path:parsed.pathname,query:parsed.searchParams};
  if(typeof options.body==='string')call.payload=JSON.parse(options.body);
  requests.push(call);
  if(request){const response=await request(call);if(response)return {ok:response.ok!==false,json:async()=>response.data};}
  let data;
  if(call.path==='/api/generation/references')data=metadata(nextId++,'image',{name:call.payload.name});
  else if(call.path==='/api/generation/reference-upload')data=metadata(nextId++,parsed.searchParams.get('kind'),{name:parsed.searchParams.get('name')});
  else if(call.path.startsWith('/api/generation/reference/'))data=known.get(call.path.split('/').at(-1));
  else if(call.path==='/api/generation/lab')data={models:[model],jobs:[],busy:false,preferences:{video:{model:'h3'}},image_skill:{styles:[]}};
  else throw new Error('Unexpected test request '+url);
  if(!data)return {ok:false,json:async()=>({error:'素材不存在'})};
  if(data.id)known.set(data.id,data);
  return {ok:true,json:async()=>data};
 };
 if(draft)w.localStorage.setItem('studio-generation-lab-v1-video',draft);
 for(const name of ['lab-core.js','media-size.js','lab-video.js','lab-management.js','lab.js']){
  const source=fs.readFileSync(path.join(root,'public',name),'utf8').replace(/\nrefreshLab\(true\);\s*$/,'\n');
  vm.runInContext(source,context,{filename:name});
 }
 const run=code=>vm.runInContext(code,context),query=selector=>w.document.querySelector(selector);
 w.fixtureModel=model;run('lab.models=[fixtureModel];');query('#lab-model').innerHTML='<option value="h3">H3</option>';
 query('#lab-prompt').value='一只猫在湖边走动';
 const load=settings=>{w.fixtureSettings=settings;run('configureLab(fixtureSettings);');};
 load({workflow:'t2v'});
 return {w,run,query,requests,known,load,
  params:()=>JSON.parse(run('JSON.stringify(labParameters())')),
  validate:()=>run('validateVideoParameters(labParameters())'),
  file(name,kind='image',size=8){const file=new w.File(['fixture'],name,{type:kind==='image'?'image/png':kind==='video'?'video/mp4':'audio/wav'});Object.defineProperty(file,'size',{value:size});return file;},
  upload(slot,files){const input=query('[data-video-upload="'+slot+'"]');assert.ok(input,'expected input slot '+slot);Object.defineProperty(input,'files',{configurable:true,value:files});return input.onchange();},
  switchMode(mode){query('#lab-workflow').value=mode;query('#lab-workflow').dispatchEvent(new w.Event('change',{bubbles:true}));},
  storage:()=>w.localStorage.getItem('studio-generation-lab-v1-video')
 };
}

test('六种模式显示实测状态，组合模式筛选匹配权重、尺寸并要求锚图和参考素材',async t=>{
 const s=setup(t);assert.deepEqual([...s.query('#lab-workflow').options].map(option=>option.value),modeIds);
 assert.match([...s.query('#lab-workflow').options].find(option=>option.value==='ir2v').textContent,/待实测/);
 s.switchMode('flr2v');
 assert.deepEqual([...s.query('#lab-checkpoint').options].map(option=>option.value),['hybrid']);
 assert.deepEqual([...s.query('#media-size').options].map(option=>option.value),['768x448','448x768']);
 assert.throws(s.validate,/首帧或尾帧/);
 await s.upload('first',[s.file('first.png')]);await s.upload('last',[s.file('last.png')]);
 assert.throws(s.validate,/至少添加/);
 await s.upload('images',[s.file('cat.png')]);assert.doesNotThrow(s.validate);
 assert.deepEqual([...s.w.document.querySelectorAll('.video-reference-heading strong')].map(node=>node.textContent),['首帧图片 · <Picture 1>','尾帧图片 · <Picture 2>','<Picture 3>']);
});

test('多图上传乱序完成仍按选择顺序提交，删除中间图片同步更新编号',async t=>{
 const cat=deferred(),dog=deferred();
 const s=setup(t,{request:async call=>{
  if(call.path==='/api/generation/references'&&call.payload.name==='cat.png')return cat.promise;
  if(call.path==='/api/generation/references'&&call.payload.name==='dog.png')return dog.promise;
 }});s.switchMode('ir2v');await s.upload('first',[s.file('first.png')]);
 const pending=s.upload('images',[s.file('cat.png'),s.file('dog.png')]);
 await until(()=>s.requests.filter(call=>call.path==='/api/generation/references').length===3);
 assert.equal(s.query('#lab-generate').disabled,true);
 dog.resolve({data:metadata(22)});await settle();assert.equal(s.query('#lab-generate').disabled,true);
 cat.resolve({data:metadata(21)});await pending;
 assert.deepEqual(s.params().references.images,[id(21),id(22)]);
 const catTile=[...s.w.document.querySelectorAll('[data-video-item]')].find(tile=>tile.textContent.includes('fixture-21'));
 catTile.querySelector('[data-video-remove]').click();
 assert.deepEqual(s.params().references.images,[id(22)]);
 assert.equal([...s.w.document.querySelectorAll('.video-reference-heading strong')].at(-1).textContent,'<Picture 2>');
 assert.doesNotThrow(s.validate);
});

test('混合素材使用原始文件上传并显示对应预览，视频音轨先于独立音频编号',async t=>{
 const s=setup(t,{request:async call=>{
  if(call.path==='/api/generation/reference-upload')return {data:metadata(call.query.get('kind')==='video'?31:32,call.query.get('kind'),{has_audio:call.query.get('kind')==='video'})};
 }});s.switchMode('r2v');
 await s.upload('images',[s.file('cat.png')]);
 const video=s.file('参考 片&段.mp4','video'),audio=s.file('voice.wav','audio');
 await s.upload('videos',[video]);await s.upload('audios',[audio]);
 const call=s.requests.find(value=>value.path==='/api/generation/reference-upload');
 assert.equal(call.options.body,video);assert.equal(call.options.headers['X-Article-Studio'],'1');assert.equal(call.query.get('name'),video.name);
 assert.equal(s.query('#lab-video-refs video').controls,true);assert.equal(s.query('#lab-video-refs video').playsInline,true);
 assert.equal(s.query('#lab-video-refs audio').controls,true);
 const labels=[...s.w.document.querySelectorAll('.video-reference-heading strong')].map(node=>node.textContent);
 assert.deepEqual(labels,['<Picture 1>','<Video 1> · <Audio 1>','<Audio 2>']);
 assert.match(s.query('#lab-video-refs').textContent,/只取开头/);assert.match(s.query('#lab-video-refs').textContent,/实际使用的片段可能略短/);
 assert.doesNotMatch(s.query('#lab-video-refs').textContent,/17k\+5/);
 assert.doesNotThrow(s.validate);
});

test('完整草稿和历史沿用保存有序ID与metadata，切换任务后不残留旧素材',async t=>{
 const s=setup(t);s.switchMode('ir2v');
 await s.upload('first',[s.file('first.png')]);await s.upload('images',[s.file('cat.png'),s.file('dog.png')]);await s.upload('audios',[s.file('voice.wav','audio')]);
 s.run('rememberLab()');const before=s.params(),reopened=setup(t,{draft:s.storage()});
 reopened.run('configureLab(readLabDraft());');
 assert.deepEqual(reopened.params().references,before.references);
 assert.deepEqual(reopened.params().reference_media,before.reference_media);
 assert.equal(reopened.requests.length,0,'saved metadata restores without speculative requests');
 assert.deepEqual(reopened.params().draftreference_media,{references:before.references,media:before.reference_media});
 reopened.w.fixtureJob={model:'h3',prompt:'另一只猫',settings:{workflow:'r2v',references:{reference:id(55)},reference_media:{[id(55)]:metadata(55)},size:'768x448',seed:7,frames:56}};
 reopened.run('fillLab(fixtureJob);');
 assert.deepEqual(reopened.params().references,{images:[id(55)]});
 assert.deepEqual(Object.keys(reopened.params().reference_media),[id(55)]);
 assert.equal(reopened.query('#lab-prompt').value,'另一只猫');
 reopened.switchMode('i2v');assert.deepEqual(reopened.params().references,{});
 assert.equal(reopened.query('[data-video-upload="images"]'),null);
});

test('缺失历史metadata先显示待核对，异步读回后恢复预览；裁后短片核对原素材',async t=>{
 const read=deferred(),s=setup(t,{request:async call=>call.path.startsWith('/api/generation/reference/')?read.promise:undefined});
 s.load({workflow:'r2v',references:{videos:[id(60)]},reference_media:{[id(60)]:metadata(60,'video',{duration:41/24})},reference_labels:{}});
 assert.match(s.query('#lab-video-refs').textContent,/文件待核对/);assert.equal(s.query('#lab-generate').disabled,true);
 assert.equal(s.query('#lab-video-refs video'),null);
 read.resolve({data:metadata(60,'video',{duration:2,has_audio:true})});await until(()=>Boolean(s.query('#lab-video-refs video')));
 assert.equal(s.params().reference_media[id(60)].duration,2);assert.doesNotThrow(s.validate);
});

test('沿用新历史或移除上传中素材后，迟到的请求不会覆盖当前输入或草稿',async t=>{
 const upload=deferred(),read=deferred();
 const s=setup(t,{request:async call=>call.path==='/api/generation/reference-upload'?upload.promise:call.path.startsWith('/api/generation/reference/')?read.promise:undefined});
 s.switchMode('r2v');const pending=s.upload('videos',[s.file('old.mp4','video')]);
 await until(()=>s.requests.some(call=>call.path==='/api/generation/reference-upload'));
 s.load({workflow:'r2v',references:{images:[id(70)]},reference_media:{[id(70)]:metadata(70)}});s.run('rememberLab();');
 upload.resolve({data:metadata(71,'video')});await pending;
 assert.deepEqual(s.params().references,{images:[id(70)]});assert.equal(s.run('lab.uploading'),0);
 assert.deepEqual(JSON.parse(s.storage()).references,{images:[id(70)]});
 s.load({workflow:'r2v',references:{images:[id(72)]}});
 s.query('[data-video-remove]').click();read.resolve({data:metadata(72)});await settle();await settle();
 assert.deepEqual(s.params().references,{});assert.equal(s.query('[data-video-item]'),null);assert.equal(s.run('lab.uploading'),0);
});

test('图片限额包含锚图且全部素材最多12，超限文件不发送上传请求',async t=>{
 const s=setup(t);s.switchMode('flr2v');
 await s.upload('images',Array.from({length:8},(_,index)=>s.file(`cat-${index}.png`)));
 assert.equal(s.params().references.images.length,7);assert.match(s.query('#toast').textContent,/最多添加 7/);
 await s.upload('videos',Array.from({length:4},(_,index)=>s.file(`clip-${index}.mp4`,'video')));
 assert.equal(s.params().references.videos.length,3);
 const count=s.requests.length;await s.upload('audios',[s.file('voice.wav','audio')]);
 assert.equal(s.requests.length,count,'two required anchor slots remain reserved within the total cap');
 assert.match(s.query('#toast').textContent,/12/);
 await s.upload('first',[s.file('first.png')]);await s.upload('last',[s.file('last.png')]);
 assert.equal(Object.keys(s.params().reference_media).length,12);assert.doesNotThrow(s.validate);
});

test('类型和文件大小在上传前验证，失败显示在当前卡片并解除上传状态',async t=>{
 const s=setup(t,{request:async call=>call.path==='/api/generation/references'?{ok:false,data:{error:'无法读取图片'}}:undefined});s.switchMode('r2v');
 await s.upload('images',[s.file('wrong.wav','audio'),s.file('huge.png','image',10_000_001)]);
 await s.upload('videos',[s.file('huge.mp4','video',100_000_001)]);assert.equal(s.requests.length,0);
 await s.upload('images',[s.file('broken.png')]);
 assert.match(s.query('#lab-video-refs').textContent,/上传失败：无法读取图片/);
 assert.equal(s.run('lab.uploading'),0);assert.equal(s.query('#lab-generate').disabled,false);
 assert.deepEqual(s.params().references,{});
 s.query('[data-video-remove]').click();assert.equal(s.query('[data-video-item]'),null);
});

test('音视频单段2–15秒、各自总时长15秒以及未核对状态均阻止提交',async t=>{
 const s=setup(t);
 for(const kind of ['video','audio']){
  const key=kind==='video'?'videos':'audios';
  s.load({workflow:'r2v',references:{[key]:[id(80),id(81)]},reference_media:{[id(80)]:metadata(80,kind,{duration:8}),[id(81)]:metadata(81,kind,{duration:8})}});
  assert.throws(s.validate,/总时长最多 15/);assert.match(s.query('#lab-video-refs').textContent,/总时长超过 15/);
 }
 s.load({workflow:'ir2v'});await s.query('#lab-form').onsubmit({preventDefault(){}});
 assert.match(s.query('#lab-submit-status').textContent,/首帧或尾帧/);assert.equal(s.requests.some(call=>call.path==='/api/generation/lab/start'),false);
});

test('非法单段时长不追加可提交ID；历史重复素材在提交前拦截',async t=>{
 let duration=1.5;
 const s=setup(t,{request:async call=>call.path==='/api/generation/reference-upload'?{data:metadata(90,call.query.get('kind'),{duration})}:undefined});
 s.switchMode('r2v');await s.upload('audios',[s.file('short.wav','audio')]);
 assert.deepEqual(s.params().references,{});assert.match(s.query('#lab-video-refs').textContent,/2–15 秒/);assert.equal(s.run('lab.uploading'),0);
 s.query('[data-video-remove]').click();duration=15.1;await s.upload('videos',[s.file('long.mp4','video')]);
 assert.deepEqual(s.params().references,{});assert.match(s.query('#lab-video-refs').textContent,/2–15 秒/);
 s.load({workflow:'ir2v',references:{first:id(91),images:[id(91)]},reference_media:{[id(91)]:metadata(91)}});
 assert.throws(s.validate,/不可重复/);
});
