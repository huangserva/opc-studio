/* Keep media jobs live after the chat turn ends; reuse cards across chat redraws. */
const chatMediaRecords=new Map();
const chatMediaLabels={connecting:'正在连接生成服务',queued:'已提交，等待生成',running:'正在生成',downloading:'正在保存结果',reconnecting:'正在恢复连接',uncertain:'状态待核实',cancelled:'已停止',done:'生成完成',error:'生成失败'};
function chatMediaActive(record){return assistantState.open&&record.node.isConnected;}
function stopChatMedia(){for(const record of chatMediaRecords.values()){clearTimeout(record.timer);record.timer=null;}}
function syncChatMedia(){
  stopChatMedia();
  document.querySelectorAll('#assistant-messages [data-chat-media]').forEach(host=>{
    const id=host.dataset.chatMedia;if(!/^[a-f0-9]{32}$/.test(id))return;
    let record=chatMediaRecords.get(id);
    if(!record){
      const node=document.createElement('section');node.className='chat-media-card';
      node.setAttribute('aria-label','生成结果');node.innerHTML='<p class="chat-media-status" role="status">正在读取生成结果…</p>';
      record={id,node,timer:null,loading:false,terminal:false,signature:null};chatMediaRecords.set(id,record);
    }
    host.replaceChildren(record.node);
    if(!record.terminal&&!record.loading)refreshChatMedia(record);
  });
}
async function refreshChatMedia(record){
  if(!chatMediaActive(record)||record.loading)return;
  record.loading=true;
  try{
    const job=await api('/api/generation/job/'+record.id);
    const signature=JSON.stringify([job.status,job.asset_url,job.model_name,job.error,job.progress,job.backend_checked_at,job.queue_position]);
    if(record.signature!==signature){paintChatMedia(record,job);record.signature=signature;}
    record.terminal=mediaTerminalStatuses.includes(job.status);
    record.verifying=job.status==='uncertain';
  }catch(e){
    record.node.querySelector('.chat-media-status').textContent='暂时无法读取生成状态，正在重试…';
    const progress=record.node.querySelector('[data-media-progress]');if(progress)markMediaProgressUnavailable(progress);
    record.signature=null;record.verifying=false;
  }finally{
    record.loading=false;
    // active jobs poll; a stopped job is terminal; an uncertain one waits for 重新核实
    if(!record.terminal&&!record.verifying&&chatMediaActive(record))record.timer=setTimeout(()=>refreshChatMedia(record),3000);
  }
}
function paintChatMedia(record,job){
  const position=captureAssistantScroll();
  const card=record.node;card.replaceChildren();card.dataset.status=job.status;
  const status=document.createElement('p');status.className='chat-media-status';status.setAttribute('role','status');
  status.textContent=(job.model_name||'媒体模型')+' · '+(chatMediaLabels[job.status]||'正在读取生成状态');card.append(status);
  if(job.settings?.lora){
    const adapter=document.createElement('small');adapter.className='chat-media-lora';
    adapter.textContent=`LoRA：${job.settings.lora_name||job.settings.lora} · 强度 ${job.settings.lora_strength}`;card.append(adapter);
  }
  const progress=document.createElement('div');renderMediaProgress(progress,job);card.append(progress);
  // after 停止/重新核实 the card itself must re-decide polling and repaint the result
  attachMediaProgressHost(progress,{onMediaAction:fresh=>{
    if(fresh.id!==record.id)return;
    clearTimeout(record.timer);record.terminal=mediaTerminalStatuses.includes(fresh.status);
    record.verifying=fresh.status==='uncertain';record.signature=null;
    paintChatMedia(record,fresh);
    if(mediaJobPhase(fresh)==='active'&&chatMediaActive(record))record.timer=setTimeout(()=>refreshChatMedia(record),3000);
  }});
  if(job.prompt){
    const description=document.createElement('details');description.className='chat-media-description';description.open=!!record.descriptionOpen;
    const summary=document.createElement('summary');summary.textContent='查看生成描述';
    const text=document.createElement('p');text.textContent=job.image_skill?.user_prompt||job.input_prompt||job.prompt;
    description.append(summary,text);description.addEventListener('toggle',()=>{record.descriptionOpen=description.open;});card.append(description);
  }
  if(job.status==='error'){
    const error=document.createElement('p');error.className='chat-media-error';error.textContent=job.error||'本次生成未完成';card.append(error);
  }
  const controls=document.createElement('div');controls.className='chat-media-controls';
  const details=document.createElement('button');details.type='button';details.className='assistant-version-link';
  details.textContent=job.status==='done'?'查看大图 / 保存到作品':'查看任务详情';
  if(job.kind==='video'&&job.status==='done')details.textContent='查看视频 / 保存到作品';
  details.onclick=()=>openMediaJob(record.id);
  if(job.status==='done'&&job.asset_url){
    const url='/generation-assets/'+record.id;
    const media=document.createElement(job.kind==='video'?'video':'img');
    media.className='chat-media-preview';media.src=url;
    if(job.kind==='video'){media.controls=true;media.preload='metadata';media.playsInline=true;media.setAttribute('aria-label','生成的视频');}
    else{
      media.alt=job.prompt||'生成的图片';media.tabIndex=0;media.setAttribute('role','button');media.title='点击查看大图';
      media.onclick=()=>openMediaJob(record.id);
      media.onkeydown=e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();openMediaJob(record.id);}};
    }
    media.addEventListener('error',()=>{status.textContent='生成已完成，但预览加载失败，可下载原文件或打开详情。';});
    media.addEventListener(job.kind==='video'?'loadedmetadata':'load',()=>{
      if(chatMediaActive(record))restoreAssistantScroll();
    },{once:true});
    card.append(media);
    const download=document.createElement('a');download.className='assistant-version-link';download.href=url;
    download.download=job.asset_file||('result'+(job.kind==='video'?'.mp4':'.png'));download.textContent='下载原文件';controls.append(download);
  }
  if(mediaTerminalStatuses.includes(job.status)){
    const retry=document.createElement('button');retry.type='button';retry.className='assistant-version-link';retry.textContent=job.kind==='video'?'再生成一段':'再生成一张';retry.onclick=()=>repeatMediaJob(job);controls.append(retry);
    if(job.kind==='image'){
      const change=document.createElement('button');change.type='button';change.className='assistant-version-link';change.textContent='换模型';change.onclick=()=>repeatMediaJob(job,true);controls.append(change);
    }
  }
  controls.append(details);card.append(controls);
  if(chatMediaActive(record))restoreAssistantScroll(position);
}
