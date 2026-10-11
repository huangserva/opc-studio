/* Ordered local references; only current upload IDs enter a request. */
const videoModes={
 t2v:{name:'文生视频',anchors:[],mixed:false},
 i2v:{name:'图生视频 · 首帧',anchors:['first'],mixed:false},
 fl2v:{name:'首尾帧生视频',anchors:['first','last'],mixed:false},
 r2v:{name:'多参考素材生视频',anchors:[],mixed:true},
 ir2v:{name:'首帧 + 多参考素材',anchors:['first'],mixed:true},
 flr2v:{name:'首尾帧 + 多参考素材',anchors:['first','last'],mixed:true}
};
const videoInputs={first:null,last:null,images:[],videos:[],audios:[]};
let videoInputEpoch=0,videoInputSequence=0;
function normalizedVideoReferences(value={}){
 const refs={};
 for(const key of ['first','last'])if(typeof value[key]==='string'&&value[key])refs[key]=value[key];
 for(const key of ['images','videos','audios'])if(Array.isArray(value[key])&&value[key].length)refs[key]=value[key].filter(id=>typeof id==='string'&&id);
 if(!refs.images?.length&&typeof value.reference==='string'&&value.reference)refs.images=[value.reference];
 return refs;
}
function videoMode(){return videoModes[$('#lab-workflow').value]||videoModes.t2v;}
function currentVideoItems(){
 const mode=videoMode();
 return [...mode.anchors.map(key=>videoInputs[key]).filter(Boolean),...(mode.mixed?['images','videos','audios'].flatMap(key=>videoInputs[key]):[])];
}
function videoMetadata(meta,id,kind){
 if(!meta||typeof meta!=='object'||!id)return null;
 if(meta.kind&&meta.kind!==kind)throw new Error('素材类型与选择的输入不一致');
 const value={id,kind,url:'/generation-references/'+encodeURIComponent(id),name:String(meta.name||'已保存'+({image:'图片',video:'视频',audio:'音频'}[kind]))};
 if(typeof meta.mime==='string')value.mime=meta.mime;
 for(const key of ['width','height','duration','frames','source_duration','source_frames'])if(Number.isFinite(Number(meta[key]))&&Number(meta[key])>0)value[key]=Number(meta[key]);
 if(typeof meta.has_audio==='boolean')value.has_audio=meta.has_audio;
 return value;
}
function videoMetadataMap(value){
 if(Array.isArray(value))return Object.fromEntries(value.filter(row=>row?.id).map(row=>[row.id,row]));
 return value&&typeof value==='object'?value:{};
}
function videoParameters(){
 const mode=videoMode(),references={},media={};
 for(const key of mode.anchors)if(videoInputs[key]?.id&&!videoInputs[key].error)references[key]=videoInputs[key].id;
 if(mode.mixed)for(const key of ['images','videos','audios']){
  const ids=videoInputs[key].filter(item=>item.id&&!item.error).map(item=>item.id);if(ids.length)references[key]=ids;
 }
 for(const item of currentVideoItems())if(item.id&&item.meta&&!item.error)media[item.id]=videoMetadata(item.meta,item.id,item.kind);
 return {workflow:$('#lab-workflow').value,video_checkpoint:$('#lab-checkpoint').value,video_accelerator:$('#lab-accelerator').value,
  video_sampling:$('#lab-video-sampling').value,...($('#lab-video-sampling').value==='dual'?{video_refine_steps:Number($('#lab-video-refine-steps').value),video_refine_denoise:Number($('#lab-video-refine-denoise').value),video_upscale:Number($('#lab-video-upscale').value)}:{}),
  references,reference_media:media,draftreference_media:{references,media},
  video_loras:[...document.querySelectorAll('[data-video-lora]:checked')].map(e=>({id:e.dataset.videoLora,strength:Number(document.querySelector('[data-video-strength="'+e.dataset.videoLora+'"]').value)}))};
}
function validateVideoParameters(payload){
 if(!['single','dual'].includes(payload.video_sampling||'single'))throw new Error('请选择有效采样方式');
 if(payload.video_sampling==='dual'){
  if(payload.size.split('x').some(n=>Number(n)%32))throw new Error('双采输出宽高须为 32 的倍数');
  for(const [key,min,max,integer] of [['video_refine_steps',1,25,true],['video_refine_denoise',0.05,0.75,false],['video_upscale',1,2,false]]){const value=payload[key];if(!Number.isFinite(value)||value<min||value>max||(integer&&!Number.isInteger(value)))throw new Error('双采参数超出范围：'+key);}
 }
 const mode=videoModes[payload.workflow];if(!mode)throw new Error('请选择有效的视频输入模式');
 const refs=normalizedVideoReferences(payload.references),media=videoMetadataMap(payload.reference_media);
 if(mode.anchors.some(key=>!refs[key]))throw new Error('请先上传当前模式需要的首帧或尾帧图片');
 const images=refs.images||[],videos=refs.videos||[],audios=refs.audios||[];
 if(mode.mixed&&!images.length&&!videos.length&&!audios.length)throw new Error('请至少添加一张图片、一段视频或一段音频作为参考');
 if(!mode.mixed&&(images.length||videos.length||audios.length))throw new Error('当前模式不使用多参考素材，请切换输入模式');
 const anchors=mode.anchors.filter(key=>refs[key]).length;
 const ids=[...mode.anchors.map(key=>refs[key]).filter(Boolean),...images,...videos,...audios];
 if(new Set(ids).size!==ids.length)throw new Error('同一参考素材不可重复使用，包含首帧和尾帧');
 if(anchors+images.length>9)throw new Error('图片总数最多 9 张，包含首帧和尾帧');
 if(videos.length>3||audios.length>3)throw new Error('参考视频、参考音频各最多 3 个');
 if(anchors+images.length+videos.length+audios.length>12)throw new Error('全部参考素材最多 12 个，包含首帧和尾帧');
 if(currentVideoItems().some(item=>item.pending||item.verifying))throw new Error('参考素材仍在上传或核对，请稍候');
 if(currentVideoItems().some(item=>item.error))throw new Error('有参考素材上传或核对失败，请移除后重新添加');
 for(const [ids,label] of [[videos,'视频'],[audios,'音频']]){
  let total=0;
  for(const id of ids){const duration=Number(media[id]?.source_duration||media[id]?.duration);if(!Number.isFinite(duration)||duration<=0)throw new Error(`参考${label}信息待核对，请稍候或重新上传`);if(duration<2||duration>15)throw new Error(`每段参考${label}须为 2–15 秒`);total+=duration;}
  if(total>15+1e-6)throw new Error(`参考${label}总时长最多 15 秒，请移除或缩短部分${label}`);
 }
 if((payload.video_loras||[]).length>3)throw new Error('附加 LoRA 最多 3 个');
}
function invalidateVideoInputs(){
 videoInputEpoch++;
 for(const key of ['first','last','images','videos','audios']){
  for(const item of Array.isArray(videoInputs[key])?videoInputs[key]:[videoInputs[key]])if(item){item.removed=true;item.controller?.abort();}
  videoInputs[key]=['first','last'].includes(key)?null:[];
 }
}
function videoItemCurrent(item){return !item.removed&&item.epoch===videoInputEpoch&&currentVideoItems().includes(item);}
function syncVideoInputState(){
 const items=currentVideoItems();lab.uploading=items.filter(item=>item.pending||item.verifying).length;updateGenerateState();
 if(lab.uploading)$('#lab-generate').textContent=items.some(item=>item.pending)?'正在上传参考素材…':'正在核对参考素材…';
}
async function readVideoInputMetadata(item){
 try{
  const data=await api('/api/generation/reference/'+encodeURIComponent(item.id));
  if(!videoItemCurrent(item))return;
  item.meta=videoMetadata(data,item.id,item.kind);checkVideoItemDuration(item.meta);
 }catch(e){if(videoItemCurrent(item))item.error='文件核对失败：'+e.message;}
 finally{if(videoItemCurrent(item)){item.verifying=false;renderVideoRefs();rememberLab();syncVideoInputState();}}
}
function hydrateVideoInputs(settings){
 invalidateVideoInputs();
 const refs=normalizedVideoReferences(settings.references||settings.draftreference_media?.references||{});
 const metadata=videoMetadataMap(settings.reference_media||settings.draftreference_media?.media),mode=videoMode();
 const make=(id,kind)=>{
  const item={token:String(++videoInputSequence),epoch:videoInputEpoch,id,kind,pending:false,verifying:false,removed:false,error:null,meta:null};
  try{
   item.meta=videoMetadata(metadata[id],id,kind);
   // Older history may describe the cropped clip rather than the uploaded source.
   if(item.meta&&kind!=='image'&&!item.meta.source_duration&&(settings.reference_labels||item.meta.duration<2))item.meta=null;
   if(item.meta)checkVideoItemDuration(item.meta);
  }catch(e){item.error=e.message;}
  item.verifying=!item.meta&&!item.error;return item;
 };
 for(const key of mode.anchors)if(refs[key])videoInputs[key]=make(refs[key],'image');
 if(mode.mixed)for(const [key,kind] of [['images','image'],['videos','video'],['audios','audio']])videoInputs[key]=(refs[key]||[]).map(id=>make(id,kind));
 renderVideoRefs();syncVideoInputState();
 for(const item of currentVideoItems())if(item.verifying)readVideoInputMetadata(item);
}
function videoValidationLabel(validation){
 if(validation==null)return '';
 const status=typeof validation==='object'?validation.label||validation.status||validation.message:validation;
 // Local checks and generic "passed" flags do not establish GPU generation.
 if(/^(已实测|GPU实测通过|GPU实测已通过|gpu_passed|gpu_verified|live_gpu_passed)$/i.test(String(status)))return '已实测';
 if(/^(实测未通过|GPU实测未通过|gpu_failed|live_gpu_failed)$/i.test(String(status)))return '实测未通过';
 if(/^(正在实测|GPU实测中|gpu_running|live_gpu_running)$/i.test(String(status)))return '正在实测';
 return '待实测';
}
function configureVideo(settings={}){
 $('#lab-video-controls').hidden=lab.kind!=='video';if(lab.kind!=='video')return;
 const model=lab.models.find(m=>m.id===$('#lab-model').value);if(!model)return;
 const workflows=Object.entries(videoModes).map(([id,value])=>({id,name:value.name,...model.workflows?.find(row=>row.id===id)}));
 $('#lab-workflow').innerHTML=workflows.map(row=>{const status=videoValidationLabel(row.validation)||(['ir2v','flr2v'].includes(row.id)?'待实测':'');return `<option value="${row.id}">${escapeHTML(row.name)}${status?' · '+escapeHTML(status):''}</option>`;}).join('');
 $('#lab-workflow').value=videoModes[settings.workflow]?settings.workflow:'t2v';const mode=$('#lab-workflow').value;
 for(const [id,key,field] of [['lab-checkpoint','checkpoints','video_checkpoint'],['lab-accelerator','accelerators','video_accelerator']]){
  const items=(model[key]||[]).filter(row=>row.modes.includes(mode));$('#'+id).innerHTML=items.map(row=>`<option value="${escapeHTML(row.id)}">${escapeHTML(row.name)}</option>`).join('');
  if(items.some(row=>row.id===settings[field]))$('#'+id).value=settings[field];
 }
 $('#lab-video-loras').innerHTML=(model.video_loras||[]).map(lo=>{const selected=(settings.video_loras||[]).find(row=>row.id===lo.id);return `<div class="video-lora-row"><label><input type="checkbox" data-video-lora="${escapeHTML(lo.id)}" ${selected?'checked':''}>${escapeHTML(lo.name)}</label><input type="number" aria-label="${escapeHTML(lo.name)}强度" data-video-strength="${escapeHTML(lo.id)}" min="0" max="2" step="0.05" value="${selected?.strength??1}"></div>`;}).join('');
 $('#lab-video-sampling').value=settings.video_sampling||'single';
 for(const [id,key,value] of [['lab-video-refine-steps','video_refine_steps',4],['lab-video-refine-denoise','video_refine_denoise',0.35],['lab-video-upscale','video_upscale',2]])$('#'+id).value=settings[key]??value;
 updateVideoSamplingControls();
 hydrateVideoInputs(settings);videoNote();
}
function videoNote(){
 const model=lab.models.find(m=>m.id===$('#lab-model').value),accel=model?.accelerators?.find(row=>row.id===$('#lab-accelerator').value),workflow=model?.workflows?.find(row=>row.id===$('#lab-workflow').value);
 const status=videoValidationLabel(workflow?.validation)||(['ir2v','flr2v'].includes($('#lab-workflow').value)?'待实测':'');
 const detail=$('#lab-workflow').value==='t2v'?'按描述生成视频。':'上传的参考素材会用于生成。';
 $('#lab-video-note').textContent=`${accel?.steps||8} 步 · 24 帧/秒。${status?status+'。':''}${detail}${workflow?.validation?.note||''}`;
}
function checkVideoItemDuration(meta){
 if(!meta)return;
 const duration=meta.source_duration||meta.duration;
 if(['video','audio'].includes(meta.kind)&&(!Number.isFinite(duration)||duration<2||duration>15))throw new Error(`每段参考${meta.kind==='video'?'视频':'音频'}须为 2–15 秒`);
}
function videoInputTile(item,label){
 const metadata=item.meta,name=metadata?.name||item.name||'已保存文件';
 const status=item.error?item.error:item.pending?'正在上传…':item.verifying?'文件待核对…':metadata?.duration?`${metadata.duration.toFixed(2)} 秒`:'';
 const preview=metadata&&!item.error&&!item.pending&&!item.verifying?(item.kind==='image'?`<img src="${escapeHTML(metadata.url)}" alt="${escapeHTML(label)}预览" loading="lazy">`:`<${item.kind} src="${escapeHTML(metadata.url)}" controls preload="metadata" ${item.kind==='video'?'playsinline':''} aria-label="${escapeHTML(label)}预览"></${item.kind}>`):'';
 return `<div class="video-reference-tile ${item.error?'video-reference-error':''}" data-video-item="${item.token}"><div class="video-reference-heading"><strong>${escapeHTML(label)}</strong><button class="button subtle" type="button" data-video-remove="${item.token}" aria-label="移除 ${escapeHTML(label)}">移除</button></div>${preview}<span class="video-reference-name" title="${escapeHTML(name)}">${escapeHTML(name)}</span><small ${item.error?'role="alert"':''}>${escapeHTML(status)}</small></div>`;
}
function renderVideoRefs(){
 const mode=videoMode(),sections=[];
 for(const [index,key] of mode.anchors.entries()){
  const label=(key==='first'?'首帧图片':'尾帧图片')+` · <Picture ${index+1}>`;
  sections.push(`<section class="video-reference"><label>${escapeHTML(label)}<input type="file" accept="image/png,image/jpeg,image/webp" data-video-upload="${key}" aria-label="${escapeHTML(label)}"></label><button class="button subtle" type="button" data-video-gallery="${key}">从图库选择</button>${videoInputs[key]?videoInputTile(videoInputs[key],label):''}</section>`);
 }
 if(mode.mixed){
  const audioUnknown=videoInputs.videos.some(item=>!item.meta&&!item.error),embeddedAudio=videoInputs.videos.filter(item=>item.meta?.has_audio&&!item.error).length;
  let videoAudioIndex=0;
  for(const [key,title,accept,hint] of [
   ['images','参考图片','image/png,image/jpeg,image/webp',`最多 ${9-mode.anchors.length} 张参考图；图片总数含首尾帧最多 9 张。`],
   ['videos','参考视频','video/mp4,video/webm,video/quicktime,.mp4,.webm,.mov','最多 3 段，每段 2–15 秒，总时长最多 15 秒。'],
   ['audios','参考音频','audio/*,.mp3,.wav,.m4a,.aac,.flac,.ogg','最多 3 段，每段 2–15 秒，总时长最多 15 秒。']]){
   const tiles=videoInputs[key].map((item,index)=>{
    let label=key==='images'?`<Picture ${mode.anchors.length+index+1}>`:key==='videos'?`<Video ${index+1}>`:audioUnknown?'音频编号待核对':`<Audio ${embeddedAudio+index+1}>`;
    if(key==='videos'&&item.meta?.has_audio&&!item.error)label+=` · <Audio ${++videoAudioIndex}>`;
    return videoInputTile(item,label);
   }).join('');
   sections.push(`<section class="video-reference"><label>${title}<input type="file" accept="${accept}" multiple data-video-upload="${key}" aria-label="添加${title}"></label><small>${hint}</small>${key==='images'?'<button class="button subtle" type="button" data-video-gallery="images">从图库选择</button>':''}<div class="video-reference-grid">${tiles}</div></section>`);
  }
  const total=currentVideoItems().filter(item=>!item.error).length;
  sections.push(`<small class="video-reference-guide">已选 ${total}/12 个素材。描述中可用 &lt;Picture 1&gt;、&lt;Video 1&gt;、&lt;Audio 1&gt; 指代。视频自带音轨先编号，独立音频随后编号；移除素材会更新编号。</small><small class="video-reference-guide">参考视频只取开头，按本次生成时长截取，实际使用的片段可能略短。</small>`);
  for(const [key,label] of [['videos','视频'],['audios','音频']])if(videoInputs[key].filter(item=>!item.error).reduce((sum,item)=>sum+(item.meta?.source_duration||item.meta?.duration||0),0)>15+1e-6)sections.push(`<small class="video-reference-error" role="alert">参考${label}总时长超过 15 秒，请移除或缩短部分${label}。</small>`);
 }
 $('#lab-video-refs').innerHTML=sections.join('');
 $('#lab-video-refs').querySelectorAll('[data-video-remove]').forEach(button=>button.onclick=()=>{
  const item=currentVideoItems().find(row=>row.token===button.dataset.videoRemove);if(!item)return;
  item.removed=true;item.controller?.abort();
  for(const key of ['first','last'])if(videoInputs[key]===item)videoInputs[key]=null;
  for(const key of ['images','videos','audios'])videoInputs[key]=videoInputs[key].filter(row=>row!==item);
  renderVideoRefs();syncVideoInputState();rememberLab();
 });
 $('#lab-video-refs').querySelectorAll('[data-video-upload]').forEach(input=>input.onchange=async()=>{const files=[...input.files];input.value='';await addVideoInputFiles(input.dataset.videoUpload,files);});
 $('#lab-video-refs').querySelectorAll('[data-video-gallery]').forEach(button=>button.onclick=()=>openVideoGallery(button.dataset.videoGallery));
}
/* Finished images from the generation history become references server side; the page
   only names the record, so nothing is downloaded and re-uploaded as a copy. */
const videoGallery={dialog:null,jobs:[],slot:'first',query:'',loading:false,busy:false};
function galleryJobPrompt(job){return job.image_skill?.user_prompt||job.input_prompt||job.prompt||'';}
function videoGalleryMatches(){
 const needle=videoGallery.query.trim().toLowerCase();
 return videoGallery.jobs.filter(job=>[galleryJobPrompt(job),job.model_name,job.settings?.lora_name||''].join(' ').toLowerCase().includes(needle));
}
function renderVideoGallery(){
 if(!videoGallery.dialog)return;
 const jobs=videoGalleryMatches();
 videoGallery.dialog.querySelector('#lab-gallery-count').textContent=videoGallery.loading?'正在读取生成历史…':`已完成图片 ${jobs.length} 张；选一张作为${videoGallery.slot==='images'?'参考图':'首帧'}`;
 const list=videoGallery.dialog.querySelector('#lab-gallery-list');
 list.innerHTML=jobs.length?jobs.map(job=>`<button class="lab-gallery-item" type="button" data-gallery-pick="${escapeHTML(job.id)}" aria-label="使用这张图片作为参考"><img src="${escapeHTML(job.asset_url)}" alt="生成结果" loading="lazy"><span title="${escapeHTML(galleryJobPrompt(job))}">${escapeHTML(galleryJobPrompt(job)||'无描述')}</span><small>${escapeHTML(job.model_name)} · ${escapeHTML(job.settings?.size||'')} · ${escapeHTML(labDate(job.created_at))} · ${job.origin==='lab'?'独立页':'AI 助手'}</small></button>`).join(''):'<p class="lab-muted">没有匹配的已完成图片。先到图片生成页产出一张，或换个关键词。</p>';
 list.querySelectorAll('[data-gallery-pick]').forEach(button=>button.onclick=()=>pickVideoGalleryImage(button.dataset.galleryPick));
}
async function loadVideoGallery(){
 videoGallery.loading=true;renderVideoGallery();
 try{
  const data=await api('/api/generation/lab?kind=image&scope=all&trash=0');
  videoGallery.jobs=data.jobs.filter(job=>job.status==='done'&&!job.deleted_at&&job.asset_url);
 }catch(e){videoGallery.jobs=[];videoGallery.loadError=e.message;}
 finally{videoGallery.loading=false;renderVideoGallery();}
 if(videoGallery.loadError)videoGallery.dialog.querySelector('#lab-gallery-count').textContent='生成历史读取失败：'+videoGallery.loadError;
}
function openVideoGallery(slot){
 if(!videoGallery.dialog){
  const dialog=document.createElement('dialog');dialog.id='lab-gallery-dialog';
  dialog.innerHTML='<div class="lab-card-heading"><h2>从图片生成历史选择</h2><button class="button subtle" type="button" data-gallery-close>关闭</button></div><label>搜索<input type="search" id="lab-gallery-search" placeholder="按提示词或模型搜索" aria-label="搜索图片生成历史"></label><small id="lab-gallery-count"></small><div class="lab-gallery-grid" id="lab-gallery-list"></div>';
  document.body.append(dialog);
  dialog.querySelector('[data-gallery-close]').onclick=()=>dialog.close();
  dialog.querySelector('#lab-gallery-search').oninput=event=>{videoGallery.query=event.target.value;renderVideoGallery();};
  videoGallery.dialog=dialog;
 }
 videoGallery.slot=slot;videoGallery.query='';videoGallery.loadError=null;
 const search=videoGallery.dialog.querySelector('#lab-gallery-search');search.value='';
 videoGallery.dialog.showModal();loadVideoGallery();
}
async function pickVideoGalleryImage(jobId){
 if(videoGallery.busy)return;videoGallery.busy=true;
 try{
  await attachVideoGalleryImage(videoGallery.slot,jobId);
  videoGallery.dialog.close();toast('已选用生成历史里的图片，可继续调整描述后生成');
 }catch(e){toast('无法使用该图片：'+e.message);loadVideoGallery();}
 finally{videoGallery.busy=false;}
}
async function attachVideoGalleryImage(slot,jobId){
 const mode=videoMode(),anchor=mode.anchors.includes(slot),mixed=slot==='images'&&mode.mixed;
 if(!anchor&&!mixed)throw new Error('当前输入模式不使用该图片位置');
 if(anchor){
  if(videoInputs[slot]){videoInputs[slot].removed=true;videoInputs[slot].controller?.abort();videoInputs[slot]=null;}
 }else{
  const active=currentVideoItems().filter(item=>!item.error),missingAnchors=mode.anchors.filter(key=>!videoInputs[key]).length;
  if(active.length+1+missingAnchors>12)throw new Error('全部参考素材最多 12 个，包含首帧和尾帧');
  if(active.filter(item=>item.kind==='image').length>=9)throw new Error('图片总数最多 9 张，包含首帧和尾帧');
  if(videoInputs.images.filter(item=>!item.error).length>=9-mode.anchors.length)throw new Error(`当前模式最多添加 ${9-mode.anchors.length} 张参考图，为首尾帧保留位置`);
 }
 const info=await api('/api/generation/reference-from-job',{job_id:jobId});
 const meta=videoMetadata(info,info.id,'image');
 const item={token:String(++videoInputSequence),epoch:videoInputEpoch,id:meta.id,kind:'image',name:meta.name,meta,pending:false,verifying:false,removed:false,error:null};
 if(anchor)videoInputs[slot]=item;else videoInputs[slot].push(item);
 renderVideoRefs();syncVideoInputState();rememberLab();
 return item;
}
async function setVideoFrameFromJob(jobId){
 if(!videoMode().anchors.includes('first')){
  const select=$('#lab-workflow');
  if([...select.options].some(option=>option.value==='i2v')){select.value='i2v';configureLab(currentLabValues());}
 }
 return attachVideoGalleryImage('first',jobId);
}
function videoFrameLocation(job){return '/lab.html?kind=video&frame_from='+encodeURIComponent(job.id);}
function startVideoFromImage(job){location.href=videoFrameLocation(job);}
function videoFileKind(slot){return slot==='videos'?'video':slot==='audios'?'audio':'image';}
function videoFileAllowed(file,kind){
 if(kind==='image')return ['image/png','image/jpeg','image/webp'].includes(file.type)||(!file.type&&/\.(png|jpe?g|webp)$/i.test(file.name));
 return kind==='video'?/^video\//.test(file.type)||/\.(mp4|webm|mov|m4v)$/i.test(file.name):/^audio\//.test(file.type)||/\.(mp3|wav|m4a|aac|flac|ogg|opus)$/i.test(file.name);
}
async function uploadVideoInput(item,file){
 try{
  let metadata;
  if(item.kind==='image'){
   const data=await new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(reader.result);reader.onerror=()=>reject(new Error('读取图片失败'));reader.readAsDataURL(file);});
   if(!videoItemCurrent(item))return;
   metadata=await api('/api/generation/references',{name:file.name,data});
  }else{
   item.controller=new AbortController();
   const response=await fetch('/api/generation/reference-upload?kind='+item.kind+'&name='+encodeURIComponent(file.name),{method:'POST',headers:{'X-Article-Studio':'1','Content-Type':file.type||'application/octet-stream'},body:file,signal:item.controller.signal});
   metadata=await response.json();if(!response.ok)throw new Error(metadata.error||'上传失败');
  }
  if(!videoItemCurrent(item))return;
  if(!metadata?.id)throw new Error('上传未返回可用素材');
  const safe=videoMetadata(metadata,metadata.id,item.kind);checkVideoItemDuration(safe);item.id=safe.id;item.meta=safe;
 }catch(e){if(videoItemCurrent(item)){item.error='上传失败：'+e.message;toast(item.error);}}
 finally{if(videoItemCurrent(item)){item.pending=false;renderVideoRefs();syncVideoInputState();rememberLab();}}
}
async function addVideoInputFiles(slot,files){
 const mode=videoMode(),kind=videoFileKind(slot),accepted=[];
 if(!mode.anchors.includes(slot)&&!(mode.mixed&&['images','videos','audios'].includes(slot)))return;
 for(const file of files){
  if(!videoFileAllowed(file,kind)){toast('请选择对应类型的图片、视频或音频');continue;}
  if(file.size>(kind==='image'?10_000_000:100_000_000)){toast(kind==='image'?'图片最大 10 MB':'视频和音频每个最大 100 MB');continue;}
  if(mode.anchors.includes(slot)){
   if(accepted.length)break;
   if(videoInputs[slot]){videoInputs[slot].removed=true;videoInputs[slot].controller?.abort();videoInputs[slot]=null;}
  }
  const active=currentVideoItems().filter(item=>!item.error);
  const missingAnchors=mode.anchors.filter(key=>!videoInputs[key]).length;
  if(active.length+1+(mode.anchors.includes(slot)?0:missingAnchors)>12){toast('全部参考素材最多 12 个，包含首帧和尾帧');break;}
  if(kind==='image'&&active.filter(item=>item.kind==='image').length>=9){toast('图片总数最多 9 张，包含首帧和尾帧');break;}
  if(slot==='images'&&videoInputs.images.filter(item=>!item.error).length>=9-mode.anchors.length){toast(`当前模式最多添加 ${9-mode.anchors.length} 张参考图，为首尾帧保留位置`);break;}
  if(['videos','audios'].includes(slot)&&videoInputs[slot].filter(item=>!item.error).length>=3){toast(kind==='video'?'参考视频最多 3 段':'参考音频最多 3 段');break;}
  const item={token:String(++videoInputSequence),epoch:videoInputEpoch,kind,name:file.name,id:null,meta:null,pending:true,verifying:false,removed:false,error:null};
  if(mode.anchors.includes(slot))videoInputs[slot]=item;else videoInputs[slot].push(item);accepted.push({item,file});
 }
 renderVideoRefs();syncVideoInputState();
 await Promise.all(accepted.map(({item,file})=>uploadVideoInput(item,file)));
}
$('#lab-workflow').onchange=()=>{const settings=videoParameters();configureLab(settings);rememberLab();};
$('#lab-accelerator').onchange=()=>{videoNote();rememberLab();};

function updateVideoSamplingControls(){const single=$('#lab-video-sampling').value!=='dual';$('#lab-video-refine').hidden=single;$('#lab-video-refine').querySelectorAll('input').forEach(input=>input.disabled=single);}
$('#lab-video-sampling').onchange=()=>{updateVideoSamplingControls();rememberLab();};
