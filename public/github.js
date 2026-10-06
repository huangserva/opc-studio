'use strict';
(() => {
  const $=s=>document.querySelector(s), esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const time=v=>v?new Date(typeof v==='number'?v*1000:v).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai'}):'尚未完成';
  let state=null,initialized=false,busy=false;
  async function api(path,data){const r=await fetch(path,data===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json','X-Article-Studio':'1'},body:JSON.stringify(data)});const d=await r.json();if(!r.ok)throw Error(d.error||'请求失败');return d;}
  function status(message,error=false){$('#gh-status').textContent=message;$('#gh-status').dataset.error=String(error);}
  async function action(form,fn){if(busy)return;busy=true;const button=form.querySelector('button');button.disabled=true;status('正在处理…');try{await fn();await load();}catch(e){status(e.message,true);}finally{busy=false;button.disabled=false;}}
  function render(){const c=state.connection||{};
    $('#gh-connection').textContent=c.owner?'账号 '+c.owner+' · '+(c.auth==='local_api'?'已使用本机 API 凭据':'公开仓库 API'):'尚未核对账号';
    $('#gh-quota').textContent=c.remaining!==undefined?'API 剩余 '+c.remaining+' / '+c.limit+' · 配额恢复 '+time(c.reset)+(c.retry_at>Date.now()/1000?' · 等待限流恢复 '+time(c.retry_at):''):'';
    const task=state.tasks[0];const labels={running:'运行中',succeeded:'成功',partial:'部分完成',failed:'失败',cancelled:'已停止',timeout:'超时'};$('#gh-run-state').innerHTML=task?((task.running?'正在采集与分析':task.last_run?'最近运行：'+(labels[task.last_run.status]||task.last_run.status):'任务已保存，尚未运行')+(task.last_run?' · <a href="/schedules.html?run='+encodeURIComponent(task.last_run.id)+'">查看本次运行记录 ↗</a>':'')):'';
    $('#gh-count').textContent=state.sources.length+' 个来源 · '+state.retry_documents+' 条分析待重试或核对';
    const types={issues:'Issue',pulls:'PR',comments:'讨论回复',releases:'Release',commits:'提交'};
    $('#gh-sources').innerHTML=state.sources.map(s=>'<article class="gh-source"><div><h3>'+esc(s.repository)+'</h3><p>'+esc(s.watch.map(k=>types[k]||k).join('、'))+' · '+s.count+' 份原文 · '+(s.enabled?'采集已启用':'已暂停')+'</p><p>'+esc(s.threads?.length?'指定讨论：'+s.threads.map(n=>'#'+n).join('、'):'范围：整个仓库')+'</p><p>最近成功采集：'+esc(time(s.capture.last_capture))+(s.capture.has_more?' · 仍有后续页，下轮续取':'')+'</p><p class="gh-error">'+esc(s.capture.error||'')+'</p></div><div class="gh-actions"><a class="button" href="/knowledge.html?source='+encodeURIComponent(s.id)+'">查看原文</a><button class="button" data-source="'+esc(s.id)+'">'+(s.enabled?'暂停来源':'启用来源')+'</button></div></article>').join('')||'<p class="muted">接入一个仓库后，在上方开始采集与分析。</p>';
    $('#gh-sources').querySelectorAll('[data-source]').forEach(b=>b.onclick=()=>action($('#gh-source'),async()=>{const s=state.sources.find(s=>s.id===b.dataset.source);await api('/api/knowledge/source',{id:s.id,enabled:!s.enabled});status('来源开关已更新；原文及引用保留。');}));
    if(!initialized){$('#gh-connect').elements.owner.value=c.owner||'';const t=state.tasks[0];if(t){const f=$('#gh-run').elements;f.profile_id.value=t.profile_id;f.minutes.value=t.minutes;f.enabled.checked=t.enabled;f.topics.value=t.body;}initialized=true;}
  }
  async function load(){state=await api('/api/github');render();}
  $('#gh-connect').onsubmit=e=>{e.preventDefault();action(e.currentTarget,async()=>{const d=await api('/api/github/connect',{owner:$('#gh-connect').elements.owner.value});$('#gh-repositories').innerHTML=d.repositories.map(r=>'<option value="'+esc(r.repository)+'"></option>').join('');status('已核对 '+d.account+' 的公开仓库。'+(d.has_more?'列表还有后续仓库，可直接填写地址。':''));});};
  $('#gh-source').elements.threads.oninput=()=>{if($('#gh-source').elements.threads.value.trim())for(const type of ['commits','releases'])$('#gh-source').querySelector('[name=watch][value='+type+']').checked=false;};
  $('#gh-source').onsubmit=e=>{e.preventDefault();action(e.currentTarget,async()=>{const raw=$('#gh-source').elements.threads.value.trim(),threads=raw?raw.split(/[\s,，、]+/).map(n=>Number(n.replace(/^#/,''))):[];if(threads.length>20||threads.some(n=>!Number.isInteger(n)||n<1)||new Set(threads).size!==threads.length)throw Error('指定讨论请填写最多 20 个不同的正整数编号');await api('/api/github/source',{repository:$('#gh-source').elements.repository.value,watch:[...$('#gh-source').querySelectorAll('[name=watch]:checked')].map(e=>e.value),threads});status('仓库已接入，开始采集后即可查看原文。');});};
  $('#gh-run').onsubmit=e=>{e.preventDefault();action(e.currentTarget,async()=>{if(!state.sources.some(s=>s.enabled))throw Error('请先启用一个仓库来源');const f=$('#gh-run').elements;await api('/api/github/run',{profile_id:f.profile_id.value,minutes:Number(f.minutes.value),enabled:f.enabled.checked,topics:f.topics.value});status('GitHub 任务已开始；请在运行记录查看分析结果。');});};
  $('#gh-refresh').onclick=()=>load().catch(e=>status(e.message,true));
  (async()=>{try{const d=await api('/api/assistant/settings');$('#gh-run').elements.profile_id.innerHTML=d.profiles.filter(p=>p.configured).map(p=>'<option value="'+esc(p.profile_id)+'">'+esc(p.display_name||p.model)+'</option>').join('');await load();}catch(e){status(e.message,true);}})();
  setInterval(()=>{if(!busy)load().catch(e=>status(e.message,true));},5000);
})();
