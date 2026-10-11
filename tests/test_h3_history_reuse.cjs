const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const {JSDOM}=require('jsdom');

const root=path.join(__dirname,'..');
const html=fs.readFileSync(path.join(root,'public/index.html'),'utf8');
const recipeFields=['workflow','video_checkpoint','video_accelerator','video_loras','references'];
function sourceJob(){return {
 id:'a'.repeat(32),kind:'video',status:'done',model:'h3',model_name:'H3',prompt:'一只猫沿湖边走动',
 created_at:'2026-10-04T01:00:00Z',asset_url:'/generation-assets/'+'a'.repeat(32),
 settings:{workflow:'ir2v',video_checkpoint:'singularity',video_accelerator:'ref8',
  video_loras:[{id:'motion',strength:0.65,name:'Motion',file:'motion.safetensors'}],
  references:{first:'b'.repeat(32),images:['c'.repeat(32)],videos:['d'.repeat(32)],audios:[]},
  size:'768x448',frames:124,seed:99,steps:8,cfg:1,negative_prompt:'never reuse this',ref_image_size:512}
};}

function setup(t){
 const dom=new JSDOM(html,{url:'http://127.0.0.1:8766/',runScripts:'outside-only'});t.after(()=>dom.window.close());
 const w=dom.window,context=dom.getInternalVMContext(),calls=[],source=sourceJob();let nextTimer=0;
 const models=[{id:'h3',name:'H3',kind:'video',steps:8,sizes:['768x432','768x448','448x768']},
  {id:'qi21',name:'Qwen',kind:'image',steps:20,sizes:['1024x1024']}];
 const preferences={video:{model:'h3',size:'768x432',frames:56},image:{model:'qi21',size:'1024x1024'}};
 w.state={page:'library',dirty:false};w.assistantState={busy:false,config:{configured:true}};
 w.writingWorkflow={selected:false};w.selectWritingWorkflow=selected=>{w.writingWorkflow.selected=selected;};
 w.assistantContext=()=>({view:'library'});w.openAssistant=async()=>{};w.jumpToLatestAssistant=()=>{};
 w.renderLoraFields=()=>{};w.selectedLoraParameters=()=>({});w.updateLoraEnabled=()=>{};
 w.renderMediaProgress=()=>{};w.markMediaProgressUnavailable=()=>{};
 // lab.html always loads media-progress.js before lab.js; these harnesses stub the widget
 w.attachMediaProgressHost=()=>{};
 w.setTimeout=()=>++nextTimer;w.clearTimeout=()=>{};
 w.HTMLDialogElement.prototype.showModal=function(){this.open=true;};w.HTMLDialogElement.prototype.close=function(){this.open=false;};
 w.fetch=async(url,options={})=>{
  const payload=options.body?JSON.parse(options.body):undefined;calls.push({url,payload});let data;
  if(url==='/api/generation')data={models,preferences,jobs:[source],image_skill:{styles:[]}};
  else if(url==='/api/generation/job/'+source.id)data=source;
  else if(url==='/api/generation/preferences')data=preferences;
  else if(url==='/api/generation/start')data={id:'e'.repeat(32)};
  else if(url==='/api/assistant/send')data={media_job:'e'.repeat(32)};
  else throw new Error('Unexpected test request '+url);
  return {ok:true,json:async()=>data};
 };
 // generation.js depends on the shared progress module, exactly as index.html/lab.html load it
 for(const name of ['lab-core.js','media-size.js','media-progress.js','generation.js','media-composer.js'])vm.runInContext(fs.readFileSync(path.join(root,'public',name),'utf8'),context,{filename:name});
 w.fixtureJob=source;
 const run=code=>vm.runInContext(code,context),query=selector=>w.document.querySelector(selector);
 return {w,run,query,calls,source,
  params:()=>JSON.parse(run('JSON.stringify(composerParameters())')),
  request:url=>calls.filter(call=>call.url===url).at(-1)?.payload,
  submitPopup:()=>query('#media-form').onsubmit({preventDefault(){}})
 };
}

function assertRecipe(payload,source){
 assert.equal(payload.workflow,source.settings.workflow);
 assert.equal(payload.video_checkpoint,source.settings.video_checkpoint);
 assert.equal(payload.video_accelerator,source.settings.video_accelerator);
 assert.deepEqual(payload.video_loras,[{id:'motion',strength:0.65}]);
 assert.deepEqual(payload.references,source.settings.references);
 for(const field of ['steps','cfg','negative_prompt','ref_image_size'])assert.equal(Object.hasOwn(payload,field),false,field+' must remain controlled by H3 acceleration');
}

test('从聊天卡片重试后修改描述，发送仍保留完整H3配方且用户尺寸、时长、种子覆盖历史',async t=>{
 const s=setup(t),original=JSON.stringify(s.source);
 await s.run('repeatMediaJob(fixtureJob)');
 s.query('#assistant-input').value='同一只猫快速跑过湖边';s.query('#chat-media-size').value='448x768';
 s.query('#chat-media-frames').value='56';s.query('#chat-media-seed').value='123';
 await s.run('submitComposerMedia(document.querySelector("#assistant-input").value)');
 const request=s.request('/api/assistant/send');
 assert.equal(request.message,'同一只猫快速跑过湖边');assert.equal(Object.hasOwn(request,'reference_job'),false,'edited prompt creates an explicit request');
 assertRecipe(request.media,s.source);assert.equal(request.media.size,'448x768');assert.equal(request.media.frames,56);assert.equal(request.media.seed,'123');
 assert.equal(JSON.stringify(s.source),original,'editing the form does not mutate the recorded job');
});

test('从聊天卡片重试后只在描述末尾多出换行或空格，仍沿用原任务',async t=>{
 const s=setup(t);
 await s.run('repeatMediaJob(fixtureJob)');
 const prefilled=s.query('#assistant-input').value;
 for(const tail of ['\n',' ']){
  await s.run('submitComposerMedia('+JSON.stringify(prefilled+tail)+')');
  const request=s.request('/api/assistant/send');
  assert.equal(request.reference_job,s.source.id,'trailing whitespace alone is not an edit: '+JSON.stringify(tail));
 }
});

test('历史任务没有描述时，空白消息不沿用原任务',async t=>{
 const s=setup(t);
 await s.run('repeatMediaJob(fixtureJob)');
 await s.run('composerMedia.sourcePrompt=null');
 await s.run('submitComposerMedia("   ")');
 assert.equal(Object.hasOwn(s.request('/api/assistant/send'),'reference_job'),false);
});

test('聊天普通视频、切图片和取消工具均清空历史H3配方，偏好请求不保存素材',async t=>{
 const s=setup(t);await s.run('chooseMediaTool("video",fixtureJob)');
 s.query('#chat-media-frames').value='56';s.query('#chat-media-frames').dispatchEvent(new s.w.Event('change'));
 await s.run('composerMedia.pending');
 const prefs=s.request('/api/generation/preferences');
 assert.equal(prefs.kind,'video');assert.equal(prefs.frames,56);
 for(const field of recipeFields)assert.equal(Object.hasOwn(prefs,field),false);
 await s.run('chooseMediaTool("video")');
 for(const field of recipeFields)assert.equal(Object.hasOwn(s.params(),field),false);
 await s.run('chooseMediaTool("video",fixtureJob)');await s.run('chooseMediaTool("image")');
 assert.equal(s.params().model,'qi21');for(const field of recipeFields)assert.equal(Object.hasOwn(s.params(),field),false);
 await s.run('chooseMediaTool("video",fixtureJob)');s.run('clearMediaTool()');assert.equal(s.run('composerMedia.videoSettings'),null);
});

test('生成历史弹窗完整恢复H3配方，改描述后提交保留素材并使用当前控件值',async t=>{
 const s=setup(t);await s.run('openMediaJob(fixtureJob.id)');
 assert.equal(s.query('#media-prompt').value,s.source.prompt);assert.equal(s.query('#media-frames').value,'124');assert.equal(s.query('#media-seed').value,'99');
 s.query('#media-prompt').value='猫停下来望向湖面';s.query('#media-size').value='448x768';s.query('#media-frames').value='56';s.query('#media-seed').value='456';
 // A later polling repaint must preserve the user's editable values.
 s.run('watchMedia({...fixtureJob,updated_at:"2026-10-04T01:01:00Z"})');
 assert.equal(s.query('#media-prompt').value,'猫停下来望向湖面');await s.submitPopup();
 const payload=s.request('/api/generation/start');assertRecipe(payload,s.source);
 assert.equal(payload.prompt,'猫停下来望向湖面');assert.equal(payload.size,'448x768');assert.equal(payload.frames,56);assert.equal(payload.seed,'456');
 const prefs=s.request('/api/generation/preferences');for(const field of recipeFields)assert.equal(Object.hasOwn(prefs,field),false);
});

test('重新打开普通视频或图片弹窗不沿用旧H3素材、种子、时长和描述',async t=>{
 const s=setup(t);await s.run('openMediaJob(fixtureJob.id)');await s.run('openMediaConnection("video")');
 assert.equal(s.run('mediaState.videoSettings'),null);assert.equal(s.query('#media-prompt').value,'');
 assert.equal(s.query('#media-seed').value,'');assert.equal(s.query('#media-frames').value,'56');assert.equal(s.query('#media-size').value,'768x432');
 s.query('#media-prompt').value='新的湖面日落';await s.submitPopup();
 const video=s.request('/api/generation/start');for(const field of recipeFields)assert.equal(Object.hasOwn(video,field),false);
 await s.run('openMediaJob(fixtureJob.id)');await s.run('openMediaConnection("image")');
 s.query('#media-prompt').value='一张湖面照片';await s.submitPopup();
 const image=s.request('/api/generation/start');assert.equal(image.model,'qi21');for(const field of recipeFields)assert.equal(Object.hasOwn(image,field),false);
});
