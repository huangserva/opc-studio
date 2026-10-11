/* Real media generation: persistent jobs, preview first, explicit versioned save. */
const mediaState={models:[],kind:'image',job:null,timer:null,context:null,submission:null,videoSettings:null};
const mediaLabels={connecting:'正在连接 4090…',queued:'任务已提交，等待模型…',running:'模型正在生成，请稍候…',downloading:'正在保存生成结果…',reconnecting:'连接暂时中断，正在恢复任务状态…',uncertain:'状态待核实：服务器任务可能仍在进行',cancelled:'已停止',done:'生成完成',error:'生成失败'};
function mediaVideoRecipe(job){
  if(job?.kind!=='video')return null;
  const source=job.settings||{},recipe={};
  for(const key of ['workflow','video_checkpoint','video_accelerator','references'])if(source[key]!==undefined)recipe[key]=JSON.parse(JSON.stringify(source[key]));
  if(Array.isArray(source.video_loras))recipe.video_loras=source.video_loras.map(row=>({id:row.id,strength:row.strength??1}));
  return recipe;
}
function mediaPreferencePayload(payload,kind){
  const preferences={kind};
  for(const key of ['model','size','frames','image_style','lora','lora_strength','lora_trigger'])if(payload[key]!==undefined)preferences[key]=payload[key];
  return preferences;
}
function renderImageStyleOptions(select,skill,current=''){
  const groups=new Map();
  for(const style of skill?.styles||[]){if(!groups.has(style.category))groups.set(style.category,[]);groups.get(style.category).push(style);}
  select.innerHTML='<option value="">按描述生成</option>'+[...groups].map(([name,styles])=>`<optgroup label="${escapeHTML(name)}">${styles.map(s=>`<option value="${escapeHTML(s.id)}">${escapeHTML(s.name)}</option>`).join('')}</optgroup>`).join('');
  select.value=[...select.options].some(o=>o.value===current)?current:'';
}
async function openMediaConnection(kind){
  mediaState.kind=kind;mediaState.videoSettings=null;clearTimeout(mediaState.timer);$('#media-compose').open=true;
  mediaState.context=state.page==='article'&&state.article?{slug:state.slug,version:state.article.version.id}:{};
  $('#media-connection-title').textContent=kind==='image'?'AI 生图':'AI 视频';
  $('#media-video-fields').hidden=kind!=='video';
  $('#media-prompt').placeholder=kind==='image'?'描述想生成的画面、风格、构图和画面文字…':'描述场景、人物动作、镜头运动与声音…';
  $('#media-connection-dialog').showModal();
  $('#media-connection-status').textContent='正在读取模型与生成记录…';
  $('#media-task-progress').hidden=true;
  try{
    const data=await api('/api/generation'); mediaState.models=data.models;
    $('#media-image-style-label').hidden=kind!=='image'||!data.image_skill?.ready;
    renderImageStyleOptions($('#media-image-style'),data.image_skill,data.preferences?.image?.image_style);
    $('#media-connection-model').innerHTML=data.models.filter(m=>m.kind===kind).map(m=>`<option value="${escapeHTML(m.id)}">${escapeHTML(m.name)}</option>`).join('');
    $('#media-connection-model').value=data.preferences?.[kind]?.model||$('#media-connection-model').value;
    updateMediaSizes(data.preferences?.[kind]);
    $('#media-seed').value='';$('#media-prompt').value='';
    renderMediaHistory(data.jobs.filter(j=>j.kind===kind));
    const active=data.jobs.find(j=>mediaActiveStatuses.includes(j.status)&&j.kind===kind);
    mediaState.job=null;$('#media-result').replaceChildren();delete $('#media-result').dataset.job;$('#media-job-info').textContent='';$('#media-save-actions').hidden=true;
    $('#media-start').disabled=false;$('#media-connection-status').textContent='选择模型，填写描述后开始生成。';
    if(active) watchMedia(active);
    if(!active)$('#media-start').textContent='开始生成 · 返回聊天';
  }catch(e){$('#media-connection-status').textContent=e.message;}
}
// Same rule as the lab and the chat composer: the reused recipe's workflow decides which
// sizes are legal, because reference and first/last-frame workflows reject non-32-aligned
// pixels on the server.
function mediaWorkflow(){return (mediaState.videoSettings||{}).workflow||'t2v';}
function updateMediaSizes(settings){
  const model=mediaState.models.find(m=>m.id===$('#media-connection-model').value);
  const workflow=mediaWorkflow();
  const sizeModel=workflow!=='t2v'?filteredSizeModel(model):model;
  renderMediaSize('media',sizeModel,settings?.size);
  const chosen=$('#media-size').value;
  $('#media-size-note').textContent=(settings?.size&&settings.size!=='custom'&&chosen!==settings.size&&!(sizeModel?.sizes||[]).includes(settings.size))
    ? mediaWorkflowSizeNotice(settings.size,workflow,chosen):'';
  renderMediaFrames('media',model,settings?.frames);
  $('#media-model-detail').textContent=model?`${model.name} · ${model.steps} 步 · 自有服务器`:'暂无可用模型';
  renderLoraFields('media',mediaState.models,model?.id,settings);
}
function renderMediaHistory(jobs){
  $('#media-history').innerHTML=jobs.length?'<h3>最近生成</h3>'+jobs.map(job=>`<button type="button" class="media-history-item" data-media-job="${job.id}"><span>${escapeHTML(job.prompt.slice(0,48))}</span><small>${escapeHTML(job.model_name)} · ${escapeHTML(mediaLabels[job.status]||job.status)} · ${new Date(job.created_at).toLocaleString('zh-CN',{hour12:false})}</small></button>`).join(''):'<p class="muted">还没有生成记录</p>';
  $('#media-history').querySelectorAll('[data-media-job]').forEach(button=>button.onclick=async()=>{
    try{watchMedia(await api('/api/generation/job/'+button.dataset.mediaJob),true);}catch(e){toast(e.message);}
  });
}
function fillMediaJobForm(job){
  mediaState.videoSettings=mediaVideoRecipe(job);
  $('#media-connection-model').value=job.model;updateMediaSizes(job.settings);
  $('#media-prompt').value=job.image_skill?.user_prompt||job.input_prompt||job.prompt;$('#media-seed').value=job.settings.seed??'';
  $('#media-image-style').value=job.image_skill?.style||'';
  mediaState.context=job.slug?{slug:job.slug,version:job.base_version}:{};
}
function watchMedia(job,refill=false){
  clearTimeout(mediaState.timer);
  if(refill||mediaState.job?.id!==job.id)fillMediaJobForm(job);
  mediaState.job=job;renderMediaJob(job);
  if(mediaJobPhase(job)!=='active')api('/api/generation').then(data=>renderMediaHistory(data.jobs.filter(j=>j.kind===mediaState.kind))).catch(()=>{});
  if(mediaJobPhase(job)==='active')mediaState.timer=setTimeout(async()=>{
    try{watchMedia(await api('/api/generation/job/'+job.id));}
    catch(e){
      $('#media-connection-status').textContent='暂时无法获取最新状态，正在重试；请勿重复提交';
      markMediaProgressUnavailable($('#media-task-progress'));
      mediaState.timer=setTimeout(()=>watchMedia(mediaState.job),3000);
    }
  },3000);
}
function renderMediaJob(job){
  $('#media-task-progress').hidden=false;
  renderMediaProgress($('#media-task-progress'),job);
  // 停止/重新核实 must hand control back to this panel: resume polling after a successful
  // reconcile, and re-enable 开始生成 immediately after a stop
  attachMediaProgressHost($('#media-task-progress'),{onMediaAction:fresh=>{
    if(fresh.id!==job.id)return;
    clearTimeout(mediaState.timer);mediaState.job=fresh;renderMediaJob(fresh);
    if(mediaJobPhase(fresh)==='active')mediaState.timer=setTimeout(()=>watchMedia(fresh),3000);
    else api('/api/generation').then(data=>renderMediaHistory(data.jobs.filter(j=>j.kind===mediaState.kind))).catch(()=>{});
  }});
  // a stopped job is terminal: the user must be able to start a new generation without
  // reloading the page; an uncertain job stays gated until 重新核实 resolves it
  $('#media-start').disabled=mediaJobPhase(job)!=='terminal';
  $('#media-start').textContent=job.status==='error'?'重新提交 · 返回聊天':job.status==='cancelled'?'重新生成 · 返回聊天':'开始生成 · 返回聊天';
  $('#media-connection-status').textContent=`${job.model_name} · ${mediaLabels[job.status]||job.status}${job.error?'：'+job.error:''}`;
  $('#media-job-info').textContent=`生成描述：${job.prompt}\n尺寸 ${job.settings.size} · 种子 ${job.settings.seed}${job.settings.seconds?' · '+job.settings.seconds+' 秒':''}`;
  if(job.settings.lora)$('#media-job-info').textContent+=`\nLoRA：${job.settings.lora_name} · 强度 ${job.settings.lora_strength}\n文件：${job.settings.lora_file}\n触发词：${job.settings.lora_trigger||'未填写'}`;
  $('#media-save-actions').hidden=job.status!=='done';
  if(job.status==='done')$('#media-compose').open=false;
  const result=$('#media-result');
  if(job.status==='done'&&result.dataset.job!==job.id){
    result.replaceChildren();const node=document.createElement(job.kind==='image'?'img':'video');
    node.src=job.asset_url;if(job.kind==='image')node.alt=job.prompt;else{node.controls=true;node.preload='metadata';}
    result.append(node);result.dataset.job=job.id;
  }else if(job.status!=='done'){result.replaceChildren();delete result.dataset.job;}
  $('#media-insert').hidden=job.kind!=='image'||!job.slug;
  $('#media-insert').textContent=job.saved?.insert?'打开已插入的作品':'插入生成时的作品';
  $('#media-save-new').textContent=job.saved?.new?'打开已保存的作品':'另存为新作品';
  $('#media-download').href=job.asset_url||'#';$('#media-download').download=job.kind==='image'?'AI图片.png':'AI视频.mp4';
}
$('#media-connection-model').onchange=()=>updateMediaSizes();
$('#media-size').onchange=()=>updateCustomSize('media');
$('#media-lora').onchange=()=>updateLoraEnabled('media');
$('#media-test').onclick=async()=>{
  const button=$('#media-test');button.disabled=true;$('#media-connection-status').textContent='正在测试模型服务…';
  try{const data=await api('/api/generation/health',{model:$('#media-connection-model').value});$('#media-connection-status').textContent=data.connected?(data.busy?'模型已连接，当前有任务':'模型已连接，可提交生成任务'):'模型未连接';}
  catch(e){$('#media-connection-status').textContent=e.message;}finally{button.disabled=false;}
};
$('#media-form').onsubmit=async event=>{
  event.preventDefault();$('#media-start').disabled=true;
  try{
    const payload={...(mediaState.kind==='video'?mediaState.videoSettings||{}:{}),model:$('#media-connection-model').value,prompt:$('#media-prompt').value,size:selectedMediaSize('media',mediaWorkflow()),frames:selectedMediaFrames('media',mediaState.models.find(m=>m.id===$('#media-connection-model').value)),seed:$('#media-seed').value,...(mediaState.kind==='image'?{image_style:$('#media-image-style').value}:{}),...selectedLoraParameters('media',$('#media-connection-model').value),...mediaState.context};
    const fingerprint=JSON.stringify(payload);
    if(mediaState.submission?.fingerprint!==fingerprint)mediaState.submission={fingerprint,id:crypto.randomUUID()};
    await api('/api/generation/preferences',mediaPreferencePayload(payload,mediaState.kind));
    const job=await api('/api/generation/start',{...payload,request_id:mediaState.submission.id});
    mediaState.submission=null;
    $('#media-connection-dialog').close();
    await openAssistant();
    jumpToLatestAssistant();
    toast('生成任务已进入对话，可在聊天中继续查看');
  }
  catch(e){$('#media-connection-status').textContent=e.message;$('#media-start').disabled=false;}
};
async function saveMedia(mode){
  if(state.dirty){toast('请先保存或取消当前编辑，再保存生成结果');return;}
  const job=mediaState.job;if(!job)return;
  const button=$(mode==='insert'?'#media-insert':'#media-save-new');button.disabled=true;
  try{const saved=await api('/api/generation/save/'+job.id,{mode});await loadLibrary();await openArticle(saved.slug,saved.version);$('#media-connection-dialog').close();toast('已保存，历史版本保留');}
  catch(e){$('#media-connection-status').textContent=e.message;}finally{button.disabled=false;}
}
$('#media-insert').onclick=()=>saveMedia('insert');$('#media-save-new').onclick=()=>saveMedia('new');
$('#media-connection-dialog').addEventListener('close',()=>clearTimeout(mediaState.timer));
async function openMediaJob(id){
  try{const job=await api('/api/generation/job/'+encodeURIComponent(id));await openMediaConnection(job.kind);watchMedia(job,true);}
  catch(e){toast(e.message);}
}
