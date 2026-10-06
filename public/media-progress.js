/* The clock is local; the stage and sampling counts come from the generation service. */
const mediaProgressViews=new WeakMap();
// the card that owns a progress view decides what happens after 停止/重新核实: without this
// hook a reconcile would repaint once and leave a waiting card permanently unpolled
const mediaProgressHosts=new WeakMap();
function attachMediaProgressHost(view,host){mediaProgressHosts.set(view,host);}
const mediaProgressLabels={connecting:'正在连接生成服务',queued:'正在排队',running:'服务器正在执行',downloading:'正在保存结果',reconnecting:'正在恢复连接',uncertain:'状态待核实',cancelled:'已停止',done:'生成完成',error:'生成失败'};
// one shared rule for every host: cancelled is terminal (the user may start again),
// uncertain is not active but must stay verifiable instead of being polled forever
const mediaTerminalStatuses=['done','error','cancelled'];
const mediaActiveStatuses=['connecting','queued','running','downloading','reconnecting'];
function mediaJobPhase(job){
  return mediaTerminalStatuses.includes(job.status)?'terminal'
    :mediaActiveStatuses.includes(job.status)?'active':'verifying';
}
function mediaDuration(seconds){
  seconds=Math.max(0,Math.floor(seconds));
  return seconds<60?`${seconds} 秒`:`${Math.floor(seconds/60)} 分 ${seconds%60} 秒`;
}
function mediaProgressState(job,now=Date.now()){
  const active=!['done','error','cancelled','uncertain'].includes(job.status),p=job.progress||{};
  const start=Date.parse(job.created_at),end=active?now:Date.parse(job.finished_at||job.updated_at);
  const elapsed=Number.isFinite(start)&&Number.isFinite(end)?`已用 ${mediaDuration((end-start)/1000)}`:'';
  let label=mediaProgressLabels[job.status]||'正在确认任务状态';
  if(job.status==='running'&&p.label&&job.progress_connected!==false)label=p.label;
  const sampling=job.status==='running'&&job.progress_connected!==false&&p.phase==='sampling'&&Number.isFinite(p.percent)&&Number.isFinite(p.current)&&p.total>0;
  if(sampling)label+=` · ${p.current}/${p.total} 步（${p.percent}%）`;
  const checked=Date.parse(job.backend_checked_at),age=Number.isFinite(checked)?Math.max(0,now-checked):null;
  let connection='';
  if(active){
    if(job.status==='reconnecting')connection='暂时无法确认服务器状态，正在重连；请勿重复提交';
    else if(age!==null&&age>20000)connection=`已 ${mediaDuration(age/1000)} 未收到服务器确认，正在核实；请勿重复提交`;
    else if(age!==null)connection=job.status==='running'?'服务器正在执行 · 状态刚刚确认':'服务器已连接 · 状态刚刚确认';
    else connection=job.status==='connecting'?'正在建立连接，尚未确认提交':'正在确认服务器状态';
    if(job.status==='queued'&&job.queue_position)connection+=` · 排队第 ${job.queue_position} 位`;
    if(job.status==='running'&&age!==null&&age<=20000&&job.progress_connected===false)connection+=' · 正在恢复进度连接';
  }
  let note='';
  if(job.status==='running'){
    note=sampling?'这是采样进度，之后还需解码和保存。':'当前阶段尚未返回步数，完成时间暂不能准确预计。';
    if(job.progress_connected===false)note='服务器任务仍在执行，采样步数暂时无法确认。';
    const phaseStart=Date.parse(p.started_at);
    if(job.progress_connected!==false&&Number.isFinite(phaseStart)&&now-phaseStart>120000)note=`本阶段已用 ${mediaDuration((now-phaseStart)/1000)}，尚未进入下一阶段。`;
  }else if(job.status==='downloading')note='画面已生成，正在把原文件保存到工作台。';
  if(job.status==='uncertain'){
    connection=job.connection_warning||'服务器任务状态无法确定；请勿重复提交，可点“重新核实”按原任务编号查询。';
    note='工作台不会自动重复提交，也不会把该任务当作已完成。';
  }else if(job.status==='cancelled')connection=job.connection_warning||'任务已停止。';
  return {active,label,elapsed,connection,note,percent:sampling?p.percent:null,warning:job.status==='reconnecting'||job.status==='uncertain'||(active&&age!==null&&age>20000)};
}
function paintMediaProgress(view){
  const data=mediaProgressViews.get(view);if(!data)return;
  const state=mediaProgressState(data.job);
  data.title.textContent=state.label;data.clock.textContent=state.elapsed;data.connection.textContent=data.fetchFailed?'暂时无法获取最新状态，正在重试；请勿重复提交':state.connection;
  data.note.textContent=state.note;data.note.hidden=!state.note;
  view.dataset.warning=String(state.warning||!!data.fetchFailed);
  data.bar.hidden=!state.active;
  if(state.percent===null)data.bar.removeAttribute('value');else data.bar.value=state.percent;
}
function mediaJobActions(job){
  const legacy=job.transport==='legacy-ssh';
  const stoppable=['connecting','queued','running','downloading','reconnecting','uncertain'].includes(job.status);
  return {cancel:{enabled:stoppable&&!legacy,reason:legacy?'旧 SSH 通路任务无法通过媒体服务停止，请在服务器侧核实':(stoppable?'':'任务已结束，无需停止')},
          reconcile:{enabled:job.status==='uncertain'&&!legacy,
                     reason:legacy?'旧 SSH 通路任务需按服务器侧记录核实':(job.status==='uncertain'?'按原任务编号重新核实，不会重复提交':'仅状态待核实的任务需要重新核实')}};
}
function renderMediaActions(view,job){
  const actions=mediaJobActions(job),bar=document.createElement('div');bar.className='media-progress-actions';
  const stop=document.createElement('button');stop.type='button';stop.className='media-action-stop';stop.textContent='停止任务';
  const check=document.createElement('button');check.type='button';check.className='media-action-reconcile';check.textContent='重新核实';
  const feedback=document.createElement('span');feedback.className='media-action-feedback';
  stop.disabled=!actions.cancel.enabled;stop.title=actions.cancel.reason;
  check.disabled=!actions.reconcile.enabled;check.title=actions.reconcile.reason;
  const call=async(route,button)=>{
    const current=mediaProgressViews.get(view);if(!current||button.disabled)return;
    button.disabled=true;feedback.textContent='正在请求服务器…';
    try{
      const fresh=await api('/api/generation/job/'+current.job.id+'/'+route,{});
      feedback.textContent='';renderMediaProgress(view,fresh);
      const host=mediaProgressHosts.get(view);
      if(host&&host.onMediaAction)host.onMediaAction(fresh,route);
    }catch(error){feedback.textContent=error.message;button.disabled=false;}
  };
  stop.onclick=()=>call('cancel',stop);check.onclick=()=>call('reconcile',check);
  bar.append(stop,check,feedback);view.append(bar);
}
function renderMediaProgress(view,job){
  view.className='media-progress';view.dataset.mediaProgress=job.id||'';
  const heading=document.createElement('div');heading.className='media-progress-heading';
  const title=document.createElement('strong');title.className='media-progress-title';
  const clock=document.createElement('span');clock.className='media-progress-clock';heading.append(title,clock);
  const bar=document.createElement('progress');bar.max=100;bar.setAttribute('aria-label','采样进度');
  const connection=document.createElement('p');connection.className='media-progress-connection';
  const note=document.createElement('p');note.className='media-progress-note';
  view.replaceChildren(heading,bar,connection,note);mediaProgressViews.set(view,{job,title,clock,bar,connection,note});
  renderMediaActions(view,job);paintMediaProgress(view);
}
function markMediaProgressUnavailable(view){const data=mediaProgressViews.get(view);if(data){data.fetchFailed=true;paintMediaProgress(view);}}
setInterval(()=>document.querySelectorAll('[data-media-progress]').forEach(paintMediaProgress),1000);
