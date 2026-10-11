/* One chat composer for text and explicitly selected media tools. */
const composerMedia={kind:null,models:[],preferences:null,reference:null,sourcePrompt:null,pending:null,request:null,loading:false,videoSettings:null,imageSettings:null};
function mediaSendReady(){
  $('#assistant-send').textContent=composerMedia.kind?(composerMedia.kind==='image'?'生成图片 ↑':'生成视频 ↑'):writingWorkflow.selected?'调研写稿 ↑':'发送 ↑';
  if(!assistantState.busy)$('#assistant-status').textContent=composerMedia.kind?'仅生成预览，结果保存在聊天中':writingWorkflow.selected?'联网调研、写稿、审稿；完成后可保存草稿':'修改会保存为新版本';
  $('#assistant-send').disabled=assistantState.busy||composerMedia.loading||(!composerMedia.kind&&!assistantState.config?.configured);
}
function clearMediaTool(){
  composerMedia.kind=null;composerMedia.imageSettings=null;composerMedia.reference=null;composerMedia.videoSettings=null;
  $('#chat-media-settings').hidden=true;$('#chat-tool-menu').hidden=true;
  $('#chat-media-width').disabled=true;$('#chat-media-height').disabled=true;
  $('#assistant-input').placeholder='输入问题，或选择生图和视频…';
  mediaSendReady();
}
async function chooseMediaTool(kind,job=null){
  selectWritingWorkflow(false);
  composerMedia.videoSettings=null;composerMedia.loading=true;mediaSendReady();
  try{
    const data=await api('/api/generation');
    composerMedia.models=data.models;composerMedia.preferences=data.preferences;
    composerMedia.kind=kind;composerMedia.reference=job?.id||null;composerMedia.sourcePrompt=job?.image_skill?.user_prompt||job?.input_prompt||job?.prompt||null;
    composerMedia.imageSettings=kind==='image'&&job?{references:job.settings.references||{},denoise:job.settings.denoise??0.7}:null;
    composerMedia.videoSettings=kind==='video'?mediaVideoRecipe(job):null;
    const settings=job?{model:job.model,...job.settings}:data.preferences[kind];
    $('#chat-tool-menu').hidden=true;$('#chat-media-settings').hidden=false;
    $('#chat-tool-label').textContent=kind==='image'?'生图':'视频';
    $('#chat-media-model').innerHTML=data.models.filter(m=>m.kind===kind).map(m=>`<option value="${escapeHTML(m.id)}">${escapeHTML(m.name)}</option>`).join('');
    $('#chat-media-model').value=settings.model;updateComposerSizes(settings.size,settings);
    $('#chat-image-style-label').hidden=kind!=='image'||!data.image_skill?.ready;
    renderImageStyleOptions($('#chat-image-style'),data.image_skill,job?.image_skill?.style||settings.image_style||'');
    $('#chat-media-duration').hidden=kind!=='video';
    $('#chat-media-seed').value='';
    const input=$('#assistant-input');
    if(job)input.value=job.image_skill?.user_prompt||job.input_prompt||job.prompt;
    else input.value=input.value.replace(/^@(?:生图|视频)?\s*/, '');
    input.placeholder=kind==='image'?'可添加参考图片，描述想生成或修改的画面…':'描述场景、动作和声音…';
    $('#chat-media-reference').textContent=(job?'沿用这条任务的描述；发送后创建新任务':'')+((composerMedia.kind==='video'?composerMedia.sizeNotice:'')||'');
    input.focus();
  }catch(e){toast(e.message);}finally{composerMedia.loading=false;mediaSendReady();}
}
// The recipe a job carries decides which sizes are legal: reference and first/last-frame
// workflows reject non-32-aligned pixels, so their option list must drop those presets and
// the submit path must refuse them instead of shipping a payload the server rejects.
function composerWorkflow(){return (composerMedia.videoSettings||{}).workflow||'t2v';}
function composerModel(){return composerMedia.models.find(m=>m.id===$('#chat-media-model').value);}
function updateComposerSizes(size,settings){
  const model=composerModel(),workflow=composerWorkflow();
  const sizeModel=workflow!=='t2v'?filteredSizeModel(model):model;
  const chosen=$('#chat-media-size').value;
  composerMedia.sizeNotice='';
  renderMediaSize('chat-media',sizeModel,size);
  const chosenSize=$('#chat-media-size').value;
  if(size&&size!=='custom'&&chosenSize!==size&&!(sizeModel.sizes||[]).includes(size))
    composerMedia.sizeNotice=' · '+mediaWorkflowSizeNotice(size,workflow,chosenSize);
  renderMediaFrames('chat-media',model,(settings||{}).frames);
  renderLoraFields('chat',composerMedia.models,model?.id,settings);
}
function composerParameters(){const model=composerModel();return {...(composerMedia.kind==='image'?composerMedia.imageSettings||{}:{}),...(composerMedia.kind==='video'?composerMedia.videoSettings||{}:{}),model:$('#chat-media-model').value,size:selectedMediaSize('chat-media',composerWorkflow()),...(composerMedia.kind==='video'?{frames:selectedMediaFrames('chat-media',model)}:{image_style:$('#chat-image-style').value}),...selectedLoraParameters('chat',$('#chat-media-model').value),seed:$('#chat-media-seed').value};}
function saveComposerPreference(){
  let payload;
  try{payload=mediaPreferencePayload(composerParameters(),composerMedia.kind);}catch(e){$('#assistant-status').textContent=e.message;return;}
  composerMedia.pending=(composerMedia.pending||Promise.resolve()).catch(()=>{}).then(()=>api('/api/generation/preferences',payload));
  composerMedia.pending.catch(e=>{$('#assistant-status').textContent='模型偏好保存失败：'+e.message;});
}
// Switching model inside the composer keeps the size/duration the user already picked.
function currentComposerSize(){
  try{return selectedMediaSize('chat-media',composerWorkflow());}catch{return $('#chat-media-size').value;}
}
function currentComposerValues(){
  const model=composerModel(),values={model:$('#chat-media-model').value,seed:$('#chat-media-seed').value};
  try{values.size=selectedMediaSize('chat-media',composerWorkflow());}catch{}
  try{Object.assign(values,selectedLoraParameters('chat',$('#chat-media-model').value));}catch{}
  if(composerMedia.kind==='video'){try{values.frames=selectedMediaFrames('chat-media',model);}catch{}}
  else values.image_style=$('#chat-image-style').value;
  return values;
}
$('#chat-media-model').onchange=()=>{updateComposerSizes(currentComposerSize(),{...(composerMedia.preferences||{})[composerMedia.kind],...currentComposerValues()});saveComposerPreference();};
$('#chat-media-size').onchange=()=>{updateCustomSize('chat-media');saveComposerPreference();};$('#chat-media-frames').onchange=saveComposerPreference;
$('#chat-media-width').onchange=saveComposerPreference;$('#chat-media-height').onchange=saveComposerPreference;
$('#chat-image-style').onchange=saveComposerPreference;
$('#chat-lora').onchange=()=>{updateLoraEnabled('chat');saveComposerPreference();};
$('#chat-lora-strength').onchange=saveComposerPreference;
$('#chat-lora-trigger').onchange=saveComposerPreference;
$('#chat-tool-clear').onclick=clearMediaTool;
$('#assistant-create-image').onclick=()=>chooseMediaTool('image');
$('#assistant-create-video').onclick=()=>chooseMediaTool('video');
$('#chat-tool-toggle').onclick=()=>{$('#chat-tool-menu').hidden=!$('#chat-tool-menu').hidden;};
$('#chat-tool-menu').querySelectorAll('[data-tool-kind]').forEach(b=>b.onclick=()=>chooseMediaTool(b.dataset.toolKind));
$('#chat-media-history').onclick=()=>openMediaConnection(composerMedia.kind||'image');
$('#assistant-input').addEventListener('input',()=>{
  $('#chat-tool-menu').hidden=!/^@(?:生|生图|视|视频)?$/.test($('#assistant-input').value);
  // A complete typed mention is also an explicit selection, including paste.
  const match=$('#assistant-input').value.match(/^@(生图|视频)\s+/);
  if(match&&!composerMedia.loading&&!composerMedia.kind)chooseMediaTool(match[1]==='生图'?'image':'video');
});
$('#assistant-input').addEventListener('keydown',e=>{
  if(e.isComposing)return;
  if(e.key==='Escape')$('#chat-tool-menu').hidden=true;
  if(!$('#chat-tool-menu').hidden&&['ArrowDown','Enter'].includes(e.key)){
    e.preventDefault();e.stopImmediatePropagation();$('#chat-tool-menu button').focus();
  }
},true);
async function submitComposerMedia(message,extra={}){
  if(composerMedia.loading)throw new Error('正在读取模型配置，请稍候');
  if(composerMedia.pending)await composerMedia.pending;
  const payload={message,tool:composerMedia.kind,media:composerMedia.kind?composerParameters():undefined,context:assistantContext(),...(writingWorkflow.selected?{workflow:'research-write'}:{}),...(composerMedia.reference&&String(composerMedia.sourcePrompt||'').trim()&&String(message).trim()===String(composerMedia.sourcePrompt).trim()?{reference_job:composerMedia.reference}:{}),...extra};
  // Referencing a card pre-fills editable text; edits become a new explicit request.
  const key=JSON.stringify(payload);
  if(composerMedia.request?.key!==key)composerMedia.request={key,id:crypto.randomUUID()};
  const result=await api('/api/assistant/send',{...payload,request_id:composerMedia.request.id});
  composerMedia.request=null;
  return result;
}
async function repeatMediaJob(job,changeModel=false){
  await chooseMediaTool(job.kind,job);
  if(changeModel)$('#chat-media-model').focus();
}
