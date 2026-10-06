const lab={trash:false,selection:new Set(),kind:new URLSearchParams(location.search).get('kind')==='video'?'video':'image',models:[],jobs:[],selected:null,compare:new Set(),busy:false,submitting:false,request:null,timer:null,historySignature:null,previewKey:null};
const labStatuses={connecting:'连接中',queued:'排队中',running:'正在生成',downloading:'保存中',reconnecting:'重连中',uncertain:'待核实',cancelled:'已停止',done:'已完成',error:'失败'};
const labStorage='studio-generation-lab-v1-'+lab.kind;
function readLabDraft(){try{return JSON.parse(localStorage.getItem(labStorage)||'null');}catch{return null;}}
function labParameters(){
 const model=$('#lab-model').value;
 return {model,prompt:$('#lab-prompt').value.trim(),size:selectedMediaSize('media'),seed:$('#lab-seed').value,...selectedLoraParameters('media',model),...(lab.kind==='image'?{image_style:$('#lab-style').value,steps:Number($('#lab-steps').value),cfg:Number($('#lab-cfg').value)}:{frames:Number($('#lab-frames').value),...videoParameters()})};
}
function rememberLab(){try{localStorage.setItem(labStorage,JSON.stringify(labParameters()));}catch{}}
function configureLab(settings={}){
 configureVideo(settings);
 const model=lab.models.find(m=>m.id===$('#lab-model').value);
 renderMediaSize('media',lab.kind==='video'&&$('#lab-workflow').value!=='t2v'?{...model,sizes:model.sizes.filter(s=>s.split('x').every(n=>Number(n)%32===0))}:model,settings.size);renderLoraFields('media',lab.models,model?.id,settings);
 $('#lab-steps').value=settings.steps??model?.steps;$('#lab-cfg').value=settings.cfg??model?.cfg??1;
 $('#lab-model-note').textContent=lab.kind==='video'?'24 FPS · 自有生成服务':`默认 ${model?.steps} 步 · CFG ${model?.cfg}`;
 $('#lab-duration-label').hidden=lab.kind!=='video';$('#lab-style-label').hidden=lab.kind!=='image';$('#lab-sampling').hidden=lab.kind!=='image';
 for(const id of ['lab-steps','lab-cfg'])$('#'+id).disabled=lab.kind!=='image';
 $('#lab-sampling-note').textContent=model?.id==='krea2'?'Turbo 默认 8 步；当前采样器以 CFG 1 关闭引导。':'保留默认值作为基准，每次只改一个参数，便于对比。';
}
function fillLab(job){
 const base=job.image_skill?.user_prompt||job.input_prompt||job.prompt;
 $('#lab-model').value=job.model;configureLab(job.settings);$('#lab-prompt').value=base;$('#lab-seed').value=job.settings.seed;
 $('#lab-style').value=job.image_skill?.style||'';$('#lab-frames').value=job.settings.frames||56;rememberLab();
 $('#lab-prompt').focus();toast('已沿用描述、种子和生成参数；可调整后生成');
}
function jobSummary(job){const s=job.settings;return `${s.size} · 种子 ${s.seed}${s.seconds?' · '+s.seconds+' 秒'+(s.workflow_name?' · '+s.workflow_name:''):' · '+s.steps+' 步 · CFG '+(s.cfg??'默认')}${s.lora?' · '+s.lora_name+' × '+s.lora_strength:''}${(s.video_loras||[]).map(x=>' · '+x.name+' × '+x.strength).join('')}`;}
function jobExport(job){return {id:job.id,status:job.status,model:job.model,model_name:job.model_name,created_at:job.created_at,prompt:job.image_skill?.user_prompt||job.input_prompt||job.prompt,effective_prompt:job.prompt,image_style:job.image_skill?.style||'',...job.settings,error:job.error||null};}
function renderLabJob(job){
 lab.selected=job;$('.lab-result').dataset.status=job.status;$('#lab-result-model').textContent=job.model_name;
 $('#lab-progress').hidden=false;renderMediaProgress($('#lab-progress'),job);
 attachMediaProgressHost($('#lab-progress'),{onMediaAction:fresh=>{if(fresh.id===job.id)refreshLab();}});
 const key=[job.id,job.status,job.asset_url].join(':');
 if(lab.previewKey!==key){
  lab.previewKey=key;const view=$('#lab-preview');view.querySelector('video')?.pause();view.replaceChildren();
  if(job.status==='done'){
   const media=document.createElement(job.kind==='image'?'img':'video');media.src=job.asset_url;
   if(job.kind==='image')media.alt='生成结果';else{media.controls=true;media.playsInline=true;media.preload='metadata';if(job.thumbnail_url)media.poster=job.thumbnail_url;}view.append(media);
  }else{
   const box=document.createElement('div');box.className='lab-empty';const h=document.createElement('h3'),p=document.createElement('p');
   h.textContent=job.status==='error'?'这次生成未完成':'画面正在准备中';p.textContent=job.error||'进度会持续更新，可离开页面，稍后回来查看。';box.append(h,p);view.append(box);
  }
 }
 $('#lab-result-actions').hidden=false;$('#lab-save').hidden=job.status!=='done';$('#lab-download').hidden=job.status!=='done';$('#lab-job-details').hidden=false;
 $('#lab-job-json').textContent=JSON.stringify(jobExport(job),null,2);
 $('#lab-download').href=job.asset_url||'#';$('#lab-download').download='生成结果-'+job.id.slice(0,8)+(job.kind==='image'?'.png':'.mp4');
 $('#lab-delete-result').disabled=!['done','error','cancelled'].includes(job.status);$('#lab-delete-result').textContent=job.deleted_at?'恢复记录':'删除记录';
 $('#lab-save').textContent=job.saved?.new?'打开已保存作品':'保存为作品';
}
function renderLabHistory(){
 const query=$('#lab-history-search').value.trim().toLowerCase();
 const jobs=lab.jobs.filter(j=>[j.prompt,j.model_name,j.settings.lora_name||''].join(' ').toLowerCase().includes(query));
 $('#lab-history-count').textContent=`最近 ${lab.jobs.length} 条 · 选中 ${lab.compare.size} 项对比`;
 const signature=JSON.stringify([jobs,Array.from(lab.compare),Array.from(lab.selection),lab.trash]);if(signature===lab.historySignature)return;lab.historySignature=signature;
 const list=$('#lab-history-list');
 list.innerHTML=jobs.length?jobs.map(j=>`<article class="lab-history-item"><button class="lab-history-preview" type="button" data-preview="${escapeHTML(j.id)}" aria-label="${j.kind==='video'&&j.status==='done'?'播放视频':'查看任务'} ${escapeHTML(j.id.slice(0,8))}">${j.status==='done'?(j.kind==='image'?`<img src="${escapeHTML(j.asset_url)}" alt="生成结果" loading="lazy">`:`${j.thumbnail_url?`<img src="${escapeHTML(j.thumbnail_url)}" alt="视频缩略图" loading="lazy" data-video-thumbnail>`:''}<span class="lab-video-play" aria-hidden="true"><span>▶</span></span><span class="lab-video-duration" aria-hidden="true">${escapeHTML(j.settings.seconds?'约 '+j.settings.seconds+' 秒':'播放视频')}</span>`):escapeHTML(labStatuses[j.status]||j.status)}</button><div class="lab-history-copy"><p>${escapeHTML(j.image_skill?.user_prompt||j.input_prompt||j.prompt)}</p><small>${escapeHTML(j.model_name)} · ${escapeHTML(labStatuses[j.status]||j.status)}</small><small>${escapeHTML(jobSummary(j))}</small><small>${escapeHTML(labDate(j.created_at))} · ${j.origin==='lab'?'独立页':'AI 助手'}</small>${j.status==='done'&&!lab.trash?`<label><input type="checkbox" data-compare="${escapeHTML(j.id)}" ${lab.compare.has(j.id)?'checked':''}>加入对比</label>`:''}${labHistoryActions(j)}</div></article>`).join(''):'<p class="lab-muted">这里还没有记录。生成第一张画面，或切换“所有来源”查看之前的任务。</p>';
 bindLabManagement(list);
 list.querySelectorAll('[data-video-thumbnail]').forEach(img=>img.onerror=()=>{img.hidden=true;});
 list.querySelectorAll('[data-preview]').forEach(b=>b.onclick=()=>{
  const j=lab.jobs.find(j=>j.id===b.dataset.preview);if(!j)return;
  renderLabJob(j);$('.lab-result').scrollIntoView({behavior:'smooth',block:'start'});
  if(j.kind==='video'&&j.status==='done'){
   const video=$('#lab-preview video');
   document.querySelectorAll('video').forEach(other=>{if(other!==video)other.pause();});
   video.play().catch(()=>toast('请点击播放器的播放按钮继续播放'));
  }
 });
 list.querySelectorAll('[data-compare]').forEach(box=>box.onchange=()=>{
  if(box.checked&&lab.compare.size>=4){box.checked=false;toast('最多同时比较 4 项');return;}
  if(box.checked)lab.compare.add(box.dataset.compare);else lab.compare.delete(box.dataset.compare);
  renderLabCompare();$('#lab-history-count').textContent=`最近 ${lab.jobs.length} 条 · 选中 ${lab.compare.size} 项对比`;
 });
}
function renderLabCompare(){
 const jobs=[...lab.compare].map(id=>lab.jobs.find(j=>j.id===id)).filter(Boolean);
 $('#lab-compare').innerHTML=jobs.length?jobs.map(j=>`<figure>${j.kind==='image'?`<img src="${escapeHTML(j.asset_url)}" alt="对比结果">`:`<video src="${escapeHTML(j.asset_url)}" controls preload="metadata"></video>`}<figcaption>${escapeHTML(j.model_name)}<br>${escapeHTML(jobSummary(j))}</figcaption></figure>`).join(''):'<p class="lab-muted">选择完成的结果进行对比。</p>';
}
function updateGenerateState(){
 $('#lab-generate').disabled=lab.submitting||lab.busy||lab.uploading;
 $('#lab-generate').textContent=lab.uploading?'正在上传参考图…':lab.submitting?'正在提交…':lab.busy?'已有任务进行中':'生成'+(lab.kind==='image'?'图片':'视频')+' ↑';
}
async function refreshLab(initial=false){
 clearTimeout(lab.timer);
 try{
  const data=await api('/api/generation/lab?kind='+lab.kind+'&scope='+$('#lab-scope').value+'&trash='+(lab.trash?'1':'0'));
  lab.models=data.models;lab.jobs=data.jobs;lab.busy=data.busy;
  if(initial){
   const draft=readLabDraft()||data.preferences[lab.kind]||{};
   $('#lab-model').innerHTML=data.models.filter(m=>m.kind===lab.kind).map(m=>`<option value="${escapeHTML(m.id)}">${escapeHTML(m.name)}</option>`).join('');
   if(data.models.some(m=>m.id===draft.model&&m.kind===lab.kind))$('#lab-model').value=draft.model;
   const groups=new Map();for(const s of data.image_skill?.styles||[]){if(!groups.has(s.category))groups.set(s.category,[]);groups.get(s.category).push(s);}
   $('#lab-style').innerHTML='<option value="">按描述生成</option>'+[...groups].map(([name,styles])=>`<optgroup label="${escapeHTML(name)}">${styles.map(s=>`<option value="${escapeHTML(s.id)}">${escapeHTML(s.name)}</option>`).join('')}</optgroup>`).join('');
   configureLab(draft);$('#lab-prompt').value=draft.prompt||'';$('#lab-seed').value=draft.seed??'';$('#lab-style').value=draft.image_style||'';$('#lab-frames').value=draft.frames||56;
   try{lab.request=JSON.parse(localStorage.getItem(labStorage+'-submission')||'null');}catch{}
  }
  if(lab.selected){const updated=data.jobs.find(j=>j.id===lab.selected.id);if(updated)renderLabJob(updated);else clearLabSelectionPreview();}
  else if(data.jobs.length)renderLabJob(data.jobs[0]);
  lab.selection=new Set([...lab.selection].filter(id=>lab.jobs.some(j=>j.id===id)));
  renderLabHistory();updateGenerateState();
  $('#lab-submit-status').textContent=lab.busy?'共享生成服务正在执行任务，完成后可提交。':'';
 }catch(e){$('#lab-submit-status').textContent='状态读取失败：'+e.message;if(lab.selected)markMediaProgressUnavailable($('#lab-progress'));}
 finally{lab.timer=setTimeout(()=>refreshLab(),4000);}
}
$('#lab-form').onsubmit=async event=>{
 event.preventDefault();if(lab.submitting||lab.busy||lab.uploading)return;lab.submitting=true;updateGenerateState();
 try{
  const payload=labParameters();if(!payload.prompt)throw new Error('请填写画面描述');if(lab.kind==='video')validateVideoParameters(payload);rememberLab();
  const fingerprint=JSON.stringify(payload);
  if(lab.request?.fingerprint!==fingerprint)lab.request={fingerprint,id:crypto.randomUUID()};
  try{localStorage.setItem(labStorage+'-submission',JSON.stringify(lab.request));}catch{}
  const job=await api('/api/generation/lab/start',{...payload,request_id:lab.request.id});
  lab.request=null;localStorage.removeItem(labStorage+'-submission');renderLabJob(job);await refreshLab();
 }catch(e){$('#lab-submit-status').textContent=e.message;}
 finally{lab.submitting=false;updateGenerateState();}
};
$('#lab-model').onchange=()=>{configureLab();rememberLab();};$('#media-size').onchange=()=>{updateCustomSize('media');rememberLab();};$('#media-lora').onchange=()=>{updateLoraEnabled('media');rememberLab();};
$('#lab-form').addEventListener('input',rememberLab);$('#lab-form').addEventListener('change',rememberLab);
$('#lab-defaults').onclick=()=>{configureLab();$('#media-lora').value='';updateLoraEnabled('media');rememberLab();toast('已恢复模型默认尺寸与采样参数');};
$('#lab-refresh').onclick=()=>refreshLab();$('#lab-scope').onchange=()=>{lab.compare.clear();renderLabCompare();refreshLab();};$('#lab-history-search').oninput=renderLabHistory;
$('#lab-clear-compare').onclick=()=>{lab.compare.clear();lab.historySignature=null;renderLabHistory();renderLabCompare();};
$('#lab-refill').onclick=()=>{if(lab.selected)fillLab(lab.selected);};
$('#lab-save').onclick=async()=>{
 const j=lab.selected;if(!j||j.status!=='done')return;const button=$('#lab-save');button.disabled=true;
 try{if(j.saved?.new){location.href='/?article='+encodeURIComponent(j.saved.new.slug);return;}const saved=await api('/api/generation/save/'+j.id,{mode:'new'});j.saved={...(j.saved||{}),new:saved};toast('已保存为作品');if(lab.selected?.id===j.id)button.textContent='打开已保存作品';}
 catch(e){toast(e.message);}finally{button.disabled=false;}
};
$('#lab-export').onclick=()=>{if(!lab.selected)return;const blob=new Blob([JSON.stringify(jobExport(lab.selected),null,2)],{type:'application/json'});const a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download='生成参数-'+lab.selected.id.slice(0,8)+'.json';a.click();setTimeout(()=>URL.revokeObjectURL(a.href),1000);};
const labName=lab.kind==='image'?'图片生成':'视频生成';$('#lab-title').textContent=labName;document.title=labName+' · 内容工作台';
$('#lab-subtitle').textContent=lab.kind==='image'?'调试提示词，比较参数，保存满意的结果。':'调试场景、动作和镜头描述，预览并比较生成视频。';
$('#lab-prompt').placeholder=lab.kind==='image'?'描述主体、场景、动作、构图和风格…':'描述场景、人物动作、镜头运动与声音…';
document.querySelectorAll('[data-lab-kind]').forEach(a=>a.classList.toggle('active',a.dataset.labKind===lab.kind));
refreshLab(true);
