const assistantState={open:false,loaded:false,active:null,messages:[],poll:null,busy:false,config:null,contextTimer:null};
function assistantContext(){
  const ui={page:state.page,view:state.view,more_settings_open:!!$('#chat-media-settings details')?.open};
  if(typeof composerMedia!=='undefined'&&composerMedia.kind){
    Object.assign(ui,{media_kind:composerMedia.kind,media_model:$('#chat-media-model').value,
      media_size:$('#chat-media-size').value==='custom'?$('#chat-media-width').value+'x'+$('#chat-media-height').value:$('#chat-media-size').value,...(composerMedia.kind==='video'?{media_frames:Number($('#chat-media-frames').value)}:{})});
  }
  if(state.page==='article'&&state.article)return {slug:state.slug,version:state.article.version.id,title:state.article.title,ui};
  return {ui};
}
function updateAssistantContext(){
  const context=assistantContext();
  $('#assistant-context-label').textContent=context?.slug?'当前作品 · '+context.title:'整个工作台 · 查询与整理合集';
  $('#assistant-context-label').title=context?.title||'整个工作台';
}
async function openAssistant(){
  assistantState.open=true;$('#assistant-panel').hidden=false;document.body.classList.add('assistant-open');document.body.classList.remove('history-open');
  $('#assistant-toggle').setAttribute('aria-expanded','true');updateAssistantContext();
  window.StudioPanels?.assistantChanged(true);
  clearInterval(assistantState.contextTimer);assistantState.contextTimer=setInterval(updateAssistantContext,1000);
  try{
    const config=await api('/api/assistant');assistantState.config=config;assistantState.messages=config.messages;assistantState.loaded=true;
    showCurrentModel(config);
    mediaSendReady();
    assistantState.active=config.active_job;
    renderAssistant();syncChatMedia();if(config.active_job)pollAssistant(config.active_job);
  }catch(e){$('#assistant-model').textContent='连接失败';$('#assistant-status').textContent=e.message;}
  if(assistantState.open&&!$('#assistant-panel').hidden&&!$('#assistant-panel').inert)$('#assistant-input').focus();
}
function closeAssistant(){assistantState.open=false;stopChatMedia();$('#assistant-panel').hidden=true;document.body.classList.remove('assistant-open');$('#assistant-toggle').setAttribute('aria-expanded','false');clearInterval(assistantState.contextTimer);window.StudioPanels?.assistantChanged(false);$('#assistant-toggle').focus();}
function chatText(text){return chatLinkedText(text);}
function assistantActions(actions){
  if(!actions?.length)return '';
  let proposalIndex=0;
  return `<div class="assistant-actions">${actions.map(a=>{
    if(a.media_job)return `<div class="assistant-action" data-chat-media="${escapeHTML(a.media_job)}"></div>`;
    const proposal=a.status==='done'&&a.event_proposal?`<button type="button" class="assistant-version-link" data-event-proposal="${proposalIndex++}">核对并保存事件 →</button>`:'';
    return `<div class="assistant-action ${a.status}"><span>${a.status==='done'?'✓':a.status==='error'?'!':'◌'} ${escapeHTML(a.label)}</span>${a.result?`<small>${escapeHTML(a.result)}</small>`:''}${chatSourceLinks(a.sources)}${proposal}${a.slug&&a.version?`<button type="button" class="assistant-version-link" data-assistant-slug="${escapeHTML(a.slug)}" data-assistant-version="${escapeHTML(a.version)}">查看 ${escapeHTML(a.version)} →</button>`:''}${a.image_url?`<a href="${escapeHTML(a.image_url)}" target="_blank" rel="noopener"><img src="${escapeHTML(a.image_url)}" alt="助手生成的文字卡片"></a>`:''}</div>`;
  }).join('')}</div>`;
}
function renderAssistant(active=null){
  const list=[...assistantState.messages];if(active&&!list.some(t=>t.id===active.id))list.push(active);
  const signature=JSON.stringify(list);
  if(assistantState.renderedSignature===signature){restoreAssistantScroll();return;}
  const position=captureAssistantScroll(),box=$('#assistant-message-list');
  const expanded=new Set([...box.querySelectorAll('[data-workflow-details][open]')].map(node=>node.dataset.workflowDetails));
  const sourcesExpanded=new Set([...box.querySelectorAll('[data-read-sources][open]')].map(node=>node.closest('[data-chat-turn]').dataset.chatTurn));
  assistantState.renderedSignature=signature;
  stopChatMedia();
  if(!list.length){assistantScroll.followLatest=true;box.innerHTML='<div class="assistant-welcome"><span>✦</span><h3>把想法告诉我</h3><p>我可以帮您改稿、找作品、生成图片与视频、整理合集。修改会保存新版本。</p></div>';restoreAssistantScroll();return;}
  box.innerHTML=list.map((t,i)=>`<div class="chat-turn" data-chat-turn="${escapeHTML(t.id)}"><div class="chat-user"><div><div>${chatText(t.prompt)}</div>${attachmentMarkup(t.attachments)}<button type="button" class="chat-copy" data-copy-turn="${i}" data-copy-role="user" aria-label="复制我的消息">⧉ 复制</button></div></div><div class="chat-assistant">${t.model?`<small class="chat-model">${escapeHTML(t.model)}</small>`:''}${workflowCard(t.workflow)}${assistantActions(t.workflow?t.actions.filter(a=>a.name!=='submit_workflow_stage'):t.actions)}${t.answer?`<div class="assistant-answer">${chatText(t.answer)}</div><button type="button" class="chat-copy" data-copy-turn="${i}" data-copy-role="assistant" aria-label="复制助手回复">⧉ 复制</button>`:`<div class="assistant-thinking">${t.status==='stopping'?'正在停止…':escapeHTML(t.phase||'正在处理…')}</div>`}${chatReadSources(t.citations)}</div></div>`).join('');
  box.querySelectorAll('[data-copy-turn]').forEach(button=>button.onclick=()=>copyChatMessage(button.dataset.copyRole==='user'?list[Number(button.dataset.copyTurn)].prompt:list[Number(button.dataset.copyTurn)].answer,button));
  box.querySelectorAll('[data-assistant-slug]').forEach(b=>b.onclick=()=>requestOpen(b.dataset.assistantSlug,b.dataset.assistantVersion));
  box.querySelectorAll('[data-event-proposal]').forEach(b=>b.onclick=()=>{
    const turn=list.find(t=>t.id===b.closest('[data-chat-turn]')?.dataset.chatTurn),a=(turn?.actions||[]).filter(a=>a.status==='done'&&a.event_proposal)[Number(b.dataset.eventProposal)];
    if(!a)return;
    try{const data=JSON.stringify(a.event_proposal);if(data.length>16000)throw new Error('事件建议过长，请缩小证据范围');sessionStorage.setItem('studio-event-proposal',data);location.href='/events.html';}catch(e){toast(e.message||'无法打开事件建议');}
  });
  box.querySelectorAll('[data-workflow-details]').forEach(node=>node.open=expanded.has(node.dataset.workflowDetails));
  box.querySelectorAll('[data-read-sources]').forEach(node=>node.open=sourcesExpanded.has(node.closest('[data-chat-turn]').dataset.chatTurn));
  bindWorkflowCards(box);
  syncChatMedia();
  restoreAssistantScroll(position);
}
function assistantBusy(busy){
  assistantState.busy=busy;mediaSendReady();$('#assistant-stop').hidden=!busy;
  $('#assistant-research-write').disabled=busy;
  $('#assistant-status').textContent=busy?'正在处理，可随时停止':'修改会保存为新版本';$('#assistant-new-chat').disabled=busy;
}
async function pollAssistant(id){
  clearTimeout(assistantState.poll);assistantBusy(true);
  try{
    const job=await api('/api/assistant/job/'+id);renderAssistant(job);
    if(['running','stopping'].includes(job.status)){assistantState.poll=setTimeout(()=>pollAssistant(id),1200);return;}
    assistantState.active=null;assistantBusy(false);
    const fresh=await api('/api/assistant');assistantState.config=fresh;assistantState.messages=fresh.messages;showCurrentModel(fresh);renderAssistant();
    if(job.changed?.length){
      await loadLibrary();
      if(state.page==='article'&&job.changed.includes(state.slug)&&!state.dirty){await openArticle(state.slug);}
      updateAssistantContext();
    }
  }catch(e){assistantBusy(false);$('#assistant-status').textContent=e.message;}
}
async function sendAssistant(event){
  event.preventDefault();const input=$('#assistant-input'),message=input.value.trim();if((!message&&!chatAttachments.items.length)||assistantState.busy||composerMedia.loading)return;
  assistantBusy(true);
  try{
    const attachments=await readyChatAttachments();
    const result=await submitComposerMedia(message,{unsaved:state.dirty,attachments});
    input.value='';clearChatAttachments();clearMediaTool();selectWritingWorkflow(false);
    assistantScroll.followLatest=true;
    if(result.media_job){
      assistantBusy(false);await openAssistant();
      jumpToLatestAssistant();
    }else{assistantState.active=result.id;renderAssistant(result);pollAssistant(result.id);}
  }catch(e){assistantBusy(false);$('#assistant-status').textContent=e.message;}
}

$('#assistant-toggle').onclick=()=>assistantState.open?closeAssistant():openAssistant();
$('#assistant-close').onclick=closeAssistant;$('#assistant-form').onsubmit=sendAssistant;
$('#assistant-input').onkeydown=e=>{if(e.key==='Enter'&&!e.shiftKey&&!e.isComposing){e.preventDefault();$('#assistant-form').requestSubmit();}};
$('#assistant-stop').onclick=async()=>{if(assistantState.active){try{await api('/api/assistant/stop',{id:assistantState.active});$('#assistant-status').textContent='正在停止，已保存修改会保留';}catch(e){toast(e.message);}}};
$('#assistant-new-chat').onclick=async()=>{try{await api('/api/assistant/new-chat',{});assistantState.messages=[];clearChatAttachments();clearMediaTool();renderAssistant();}catch(e){toast(e.message);}};
document.querySelectorAll('[data-assistant-prompt]').forEach(b=>b.onclick=()=>{$('#assistant-input').value=b.dataset.assistantPrompt;$('#assistant-input').focus();});
$('#history-toggle').addEventListener('click',()=>{if(assistantState.open)closeAssistant();});
let assistantWidth=440;
try{assistantWidth=Number(localStorage.getItem('studio-assistant-width'))||440;}catch{}
function setAssistantWidth(width,preservePreference=false){const maximum=window.StudioPanels?.assistantWidthLimit()??Math.max(320,window.innerWidth-(window.innerWidth>1100?614:40));assistantWidth=Math.max(320,Math.min(width,preservePreference?4096:maximum));document.documentElement.style.setProperty('--assistant-width',assistantWidth+'px');$('#assistant-resize').setAttribute('aria-valuenow',Math.round(Math.min(assistantWidth,maximum)));$('#assistant-resize').setAttribute('aria-valuemin','320');$('#assistant-resize').setAttribute('aria-valuemax',Math.round(maximum));}
setAssistantWidth(assistantWidth,true);
const resizeHandle=$('#assistant-resize');
resizeHandle.onpointerdown=e=>{e.preventDefault();resizeHandle.setPointerCapture(e.pointerId);document.body.classList.add('resizing-assistant');};
resizeHandle.onpointermove=e=>{if(resizeHandle.hasPointerCapture(e.pointerId))setAssistantWidth(window.innerWidth-e.clientX);};
resizeHandle.onpointerup=e=>{if(resizeHandle.hasPointerCapture(e.pointerId))resizeHandle.releasePointerCapture(e.pointerId);document.body.classList.remove('resizing-assistant');try{localStorage.setItem('studio-assistant-width',String(assistantWidth));}catch{}};
resizeHandle.onkeydown=e=>{if(['ArrowLeft','ArrowRight'].includes(e.key)){e.preventDefault();setAssistantWidth(assistantWidth+(e.key==='ArrowLeft'?24:-24));try{localStorage.setItem('studio-assistant-width',String(assistantWidth));}catch{}}};
window.addEventListener('resize',()=>{if(!window.StudioPanels)setAssistantWidth(assistantWidth);});

function modelTokens(n){return n>=1024&&n%1024===0?(n/1024)+'K':String(n);}
let editingModelId=null;
function showCurrentModel(config){
  const label=config.configured?config.model+' · 输入 '+modelTokens(config.context_tokens)+' / 输出 '+modelTokens(config.max_output_tokens):'未配置模型 · 请添加模型';
  $('#assistant-model').textContent=label;$('#assistant-model').title=label;
  $('#model-picker-label').textContent=config.configured?'聊天 · '+(config.display_name||config.model):'选择聊天模型';
  $('#model-picker-toggle').title=label;
  $('#model-picker-toggle').setAttribute('aria-label','当前模型：'+(config.configured?(config.display_name||config.model):'未配置')+'，点击选择模型');
  renderModelOptions(config);
}
function renderModelOptions(config){
  const profiles=config.profiles||[];
  $('#model-picker-options').innerHTML=profiles.length?profiles.map(p=>`<div class="model-option ${p.profile_id===config.active_profile_id?'selected':''}"><button type="button" class="model-option-select" data-model-select="${escapeHTML(p.profile_id)}" aria-pressed="${p.profile_id===config.active_profile_id}"><span class="model-option-check">${p.profile_id===config.active_profile_id?'✓':'✦'}</span><span><strong>${escapeHTML(p.display_name||p.model)}</strong>${p.display_name!==p.model?`<small>${escapeHTML(p.model)}</small>`:''}<small>输入 ${modelTokens(p.context_tokens)} · 输出 ${modelTokens(p.max_output_tokens)}</small></span></button><button type="button" class="model-option-edit" data-model-edit="${escapeHTML(p.profile_id)}" aria-label="编辑 ${escapeHTML(p.display_name||p.model)}" title="编辑配置">⚙</button></div>`).join(''):'<p class="model-picker-empty">添加模型后，就能在这里切换。</p>';
  $('#model-picker-options').querySelectorAll('[data-model-select]').forEach(b=>b.onclick=()=>selectModel(b.dataset.modelSelect));
  $('#model-picker-options').querySelectorAll('[data-model-edit]').forEach(b=>b.onclick=()=>openModelSettings(b.dataset.modelEdit));
}
function closeModelPicker(focus=false){$('#model-picker-menu').hidden=true;$('#model-picker-toggle').setAttribute('aria-expanded','false');if(focus)$('#model-picker-toggle').focus();}
async function toggleModelPicker(){
  if(!$('#model-picker-menu').hidden){closeModelPicker();return;}
  try{
    const c=await api('/api/assistant/settings');assistantState.config={...assistantState.config,...c};showCurrentModel(c);
    $('#model-picker-menu').hidden=false;$('#model-picker-toggle').setAttribute('aria-expanded','true');
    ($('#model-picker-options button[aria-pressed="true"]')||$('#model-picker-add')).focus();
  }catch(e){toast(e.message);}
}
async function selectModel(id){
  if(assistantState.busy){toast('请等当前回复完成或停止后再切换模型');return;}
  try{
    const c=await api('/api/assistant/select-model',{profile_id:id});
    assistantState.config={...assistantState.config,...c};showCurrentModel(c);
    $('#assistant-send').disabled=!c.configured;closeModelPicker(true);toast('当前模型：'+(c.display_name||c.model));
  }catch(e){toast(e.message);}
}
async function openModelSettings(identity=undefined){
  try{
    const settings=await api('/api/assistant/settings');
    editingModelId=identity===undefined?settings.active_profile_id:identity;
    const c=editingModelId?settings.profiles.find(p=>p.profile_id===editingModelId):{model:'',display_name:'',endpoint:'',no_api_key:false,context_tokens:262144,max_output_tokens:32768,temperature:0.25,timeout_seconds:100};
    if(!c)throw new Error('该模型配置不存在，请刷新列表');
    closeModelPicker();$('#model-dialog-title').textContent=editingModelId?'编辑模型':'添加模型';
    $('#model-save').textContent=editingModelId?'保存并使用':'添加并使用';
    $('#model-display-name').value=c.display_name||'';
    $('#model-name').value=c.model;$('#model-endpoint').value=c.endpoint;$('#model-api-key').value='';
    $('#model-no-key').checked=c.no_api_key;$('#model-api-key').disabled=c.no_api_key;
    $('#model-key-status').textContent=c.has_api_key?'已保存密钥。同一接口留空保留原密钥。':'尚未保存密钥。';
    $('#model-context').value=c.context_tokens;$('#model-output').value=c.max_output_tokens;
    $('#model-temperature').value=c.temperature;$('#model-timeout').value=c.timeout_seconds;
    $('#model-settings-status').textContent='';$('#model-dialog').showModal();
  }catch(e){toast(e.message);}
}
function modelSettingsPayload(){return {
  profile_id:editingModelId,display_name:$('#model-display-name').value.trim(),
  model:$('#model-name').value.trim(),endpoint:$('#model-endpoint').value.trim(),api_key:$('#model-api-key').value,
  no_api_key:$('#model-no-key').checked,context_tokens:Number($('#model-context').value),
  max_output_tokens:Number($('#model-output').value),temperature:Number($('#model-temperature').value),timeout_seconds:Number($('#model-timeout').value)
};}
let modelSettingsBusy=false;
async function applyModelSettings(test){
  if(modelSettingsBusy||!$('#model-settings-form').reportValidity())return;
  modelSettingsBusy=true;$('#model-save').disabled=true;$('#model-test').disabled=true;
  $('#model-settings-status').textContent=test?'正在测试当前表单配置（最多等待 30 秒）…':'正在保存…';
  try{
    const result=await api(test?'/api/assistant/test-settings':'/api/assistant/settings',modelSettingsPayload());
    if(test){$('#model-settings-status').textContent=result.message+' 返回模型：'+result.model;}
    else{
      assistantState.config={...assistantState.config,...result};showCurrentModel(result);
      $('#assistant-send').disabled=assistantState.busy||!result.configured;
      $('#model-api-key').value='';$('#model-dialog').close();toast('已切换为 '+result.model+'，下一条消息使用新配置');
    }
  }catch(e){$('#model-settings-status').textContent=e.message;}
  finally{modelSettingsBusy=false;$('#model-save').disabled=false;$('#model-test').disabled=false;}
}
$('#assistant-settings').onclick=()=>openModelSettings();
$('#model-picker-toggle').onclick=toggleModelPicker;
$('#model-picker-add').onclick=()=>openModelSettings(null);
document.addEventListener('click',e=>{if(!e.target.closest('.model-picker'))closeModelPicker();});
document.addEventListener('keydown',e=>{if(e.key==='Escape'&&!$('#model-picker-menu').hidden){e.preventDefault();closeModelPicker(true);}});
$('#model-picker-menu').addEventListener('keydown',e=>{
  if(['ArrowDown','ArrowUp','Home','End'].includes(e.key)){
    e.preventDefault();const buttons=[...$('#model-picker-menu').querySelectorAll('button')],index=buttons.indexOf(document.activeElement);
    const next=e.key==='Home'?0:e.key==='End'?buttons.length-1:(index+(e.key==='ArrowDown'?1:-1)+buttons.length)%buttons.length;buttons[next].focus();
  }
});
$('#model-settings-form').onsubmit=e=>{e.preventDefault();applyModelSettings(false);};
$('#model-test').onclick=()=>applyModelSettings(true);
$('#model-no-key').onchange=()=>{$('#model-api-key').disabled=$('#model-no-key').checked;};
$('#model-dialog').addEventListener('close',()=>{$('#model-api-key').value='';});

async function copyChatMessage(text,button){
  try{
    await navigator.clipboard.writeText(text||'');
    const previous=button.textContent;button.textContent='✓ 已复制';setTimeout(()=>{if(button.isConnected)button.textContent=previous;},1800);
  }catch{
    // Preserve the original text and line breaks when clipboard access is unavailable.
    $('#copy-message-text').value=text||'';$('#copy-message-dialog').showModal();
    $('#copy-message-text').focus();$('#copy-message-text').select();
  }
}

$('#assistant-create-image').onclick=()=>openMediaConnection('image');
$('#assistant-create-video').onclick=()=>openMediaConnection('video');
