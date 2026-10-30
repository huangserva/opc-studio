'use strict';
(() => {
  const $ = s => document.querySelector(s);
  const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const clock = v => !v ? '未知' : Number.isNaN(Date.parse(v)) ? String(v) : new Date(v).toLocaleString('zh-CN', {hour12:false,timeZone:'Asia/Shanghai'});
  const status = v => ({succeeded:'成功',failed:'失败',timeout:'超时',running:'运行中',interrupted:'已中断',cancelled:'已停止'}[v] || v || '未知');
  async function api(url, body) {
    const r = await fetch(url, body === undefined ? {} : {method:'POST',headers:{'Content-Type':'application/json','X-Article-Studio':'1'},body:JSON.stringify(body)});
    const data = await r.json(); if (!r.ok) throw Error(data.error || '读取失败'); return data;
  }
  const taskButton = t => `<button type="button" data-task="${esc(t.id)}" data-action="${t.enabled?'pause':'resume'}">${t.enabled?'暂停':'恢复'}</button>`;
  const plan = t => t.frequency==='interval' ? `每 ${t.minutes} 分钟` : `${t.at || '未知时刻'} · ${(t.weekdays || []).map(d=>['周一','周二','周三','周四','周五','周六','周日'][d]).join(' / ')}`;
  const externalRow = t => `<tr data-external-row="${esc(t.id)}"><td>${esc(t.name)}<small class="task-owner">外部 · 只读 · ${esc(t.owner)}</small></td><td>${esc(t.schedule)}</td><td><span class="task-status status-${['ok','waiting','overdue','missing','unconfirmed','running','exited','unknown'].includes(t.status)?t.status:'unknown'}">${esc(t.status_label)}</span></td><td>${esc(clock(t.last_output_at))}${t.schedule==='常驻服务'?`<small class="task-owner">PID：${esc(t.pid ?? '—')} · 退出码：${esc(t.exit_code ?? '未知')}</small>`:''}<small class="task-owner">${esc(t.detail)}</small></td><td><span class="task-owner" title="${esc(t.rule)}">外部 · 只读</span></td></tr>`;
  let interestTopics=[], interestRevision=null;
  function renderTopics() {
    $('#interest-topics').replaceChildren();
    interestTopics.forEach((topic,index)=>{const li=document.createElement('li'),span=document.createElement('span'),button=document.createElement('button');span.textContent=topic;button.type='button';button.textContent='删除';button.setAttribute('aria-label','删除方向：'+topic);button.onclick=()=>{interestTopics.splice(index,1);renderTopics();$('#interest-notice').textContent='尚未保存';};li.append(span,button);$('#interest-topics').append(li);});
    $('#interest-save').disabled=interestRevision===null;
  }
  async function loadTopics() {
    try {const data=await api('/api/settings/interest-topics');interestTopics=data.topics;interestRevision=data.revision;renderTopics();$('#interest-notice').textContent='已读取当前方向';}
    catch(e){interestRevision=null;$('#interest-save').disabled=true;$('#interest-notice').textContent=e.message;}
  }
  $('#interest-add-form').onsubmit=e=>{e.preventDefault();const value=$('#interest-topic-input').value.trim();if(!value||value.startsWith('#')||/[\r\n]/.test(value)||value.length>80){$('#interest-notice').textContent='方向须为 1–80 字，不能换行或以 # 开头';return;}if(interestTopics.length>=30){$('#interest-notice').textContent='最多 30 个方向';return;}if(!interestTopics.includes(value))interestTopics.push(value);$('#interest-topic-input').value='';renderTopics();$('#interest-notice').textContent='尚未保存';};
  $('#interest-save').onclick=async()=>{const button=$('#interest-save');button.disabled=true;try{const data=await api('/api/settings/interest-topics',{topics:interestTopics,revision:interestRevision});interestTopics=data.topics;interestRevision=data.revision;renderTopics();$('#interest-notice').textContent='兴趣清单已保存';}catch(e){$('#interest-notice').textContent=e.message;}finally{button.disabled=interestRevision===null;}};
  async function refresh() {
    loadTopics();
    $('#refresh').disabled = true; $('#notice').textContent = '';
    const results = await Promise.allSettled([
      api('/api/settings/overview'), api('/api/schedules'), api('/api/schedules/runs?page_size=10'), api('/api/assistant/settings')
    ]);
    const [overview, schedules, runs, models] = results.map(r=>r.status==='fulfilled'?r.value:null);
    if (overview) {
      $('#sources').innerHTML = overview.sources.map(s=>`<article class="card" data-source="${esc(s.id)}"><h3>${esc(s.name)}</h3><p title="${esc(s.reason)}">${s.enabled===null?'启用状态未知':s.enabled?'任务已启用':'任务已暂停'}</p><p title="${esc(s.reason)}">最近成功：${esc(clock(s.last_success))}</p><p title="${esc(s.reason)}">最近错误：${esc(s.last_error || '未知')}</p>${s.tasks.map(t=>`<div>${esc(t.name)} ${taskButton(t)}</div>`).join('')}<p><a href="${esc(s.href)}">查看实时状态 ↗</a></p></article>`).join('');
      $('#sources').setAttribute('aria-busy','false');
      $('#services').innerHTML = overview.services.map(s=>`<li><strong>${esc(s.label)}</strong> · 最近日志 ${esc(clock(s.last_log_at))}</li>`).join('');
    }
    if (schedules) {
      const external = schedules.external?.tasks || [];
      $('#task-count').textContent = `（内置 ${schedules.tasks.length} · 外部 ${external.length}）`;
      $('#external-checked').textContent = `外部检查时间：${clock(schedules.external?.checked_at)}` + (schedules.external?.last_error ? `；外部心跳刷新失败（${clock(schedules.external.last_error_at)}），保留上次快照` : '');
      $('#tasks').innerHTML = schedules.tasks.map(t=>`<tr data-task-row="${esc(t.id)}"><td>${esc(t.name)}<small class="task-owner">内置</small></td><td>${esc(plan(t))}</td><td>${t.enabled?'已启用':'已暂停'}${t.running?' · 运行中':''}</td><td>${t.last_run?esc(status(t.last_run.status)+' · '+clock(t.last_run.finished_at || t.last_run.started_at)):'暂无运行记录'}</td><td class="task-actions">${taskButton(t)} <button type="button" data-run-task="${esc(t.id)}" data-action="run" ${t.running?'disabled':''}>立即运行</button></td></tr>`).join('') + external.map(externalRow).join('') || '<tr><td colspan="5">暂无定时任务</td></tr>';
      if(overview && overview.schedule_task_count !== schedules.tasks.length) $('#notice').textContent = '任务状态刚发生变化，请刷新核对。';
    }
    if (runs) $('#runs').innerHTML = runs.runs.map(r=>`<p class="run">${esc(r.snapshot?.name || r.task_id)} · ${esc(status(r.status))} · ${esc(clock(r.finished_at || r.started_at))}</p>`).join('') || '<p>暂无运行记录</p>';
    if (models) {
      $('#active-model').textContent = '当前 profile：' + (models.display_name || models.model || '未选择');
      $('#models').innerHTML = models.profiles.map(p=>`<article class="card"><h3>${esc(p.display_name || p.model)}</h3><p>${esc(p.model)}</p><p>${p.configured?'可用':'配置不完整'} · 密钥${p.has_api_key?'已配置':'未配置'}${p.no_api_key?'（此接口无需密钥）':''}</p><button type="button" data-profile="${esc(p.profile_id)}" ${p.profile_id===models.active_profile_id || !p.configured?'disabled':''}>${p.profile_id===models.active_profile_id?'当前使用':'切换'}</button></article>`).join('') || '<p>暂无模型配置</p>';
    }
    const failures = results.map((r,i)=>r.status==='rejected'?['来源与服务','定时任务','运行记录','模型'][i]+'：'+r.reason.message:'').filter(Boolean);
    if(failures.length) $('#notice').textContent = failures.join('；');
    $('#refresh').disabled = false;
  }
  document.addEventListener('click', async event=>{
    const b = event.target.closest('[data-task],[data-run-task],[data-profile]'); if(!b) return; b.disabled = true;
    try {
      const taskId = b.dataset.task || b.dataset.runTask;
      if(taskId) await api('/api/schedules/'+encodeURIComponent(taskId)+'/'+b.dataset.action, {});
      else await api('/api/assistant/select-model', {profile_id:b.dataset.profile});
      await refresh(); $('#notice').textContent = taskId?'任务状态已更新':'当前模型已切换';
    } catch(e) { $('#notice').textContent=e.message; b.disabled=false; }
  });
  async function readLogs() {
    $('#log-info').textContent='正在读取…';
    try {
      const data = await api('/api/settings/logs?name='+encodeURIComponent($('#log-name').value)+'&lines='+encodeURIComponent($('#log-lines').value));
      $('#logs').textContent = data.lines.join('\n');
      $('#log-info').textContent = data.missing?'日志文件尚不存在':data.truncated?'仅显示文件末尾；超长行已截断':'已读取并脱敏';
    } catch(e) { $('#logs').textContent=''; $('#log-info').textContent=e.message; }
  }
  $('#logs-form').onsubmit = e=>{e.preventDefault();readLogs();};
  $('#refresh').onclick=refresh;refresh();readLogs();
})();
