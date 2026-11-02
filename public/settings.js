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
  let officialRules={include:[],exclude:[]},officialRevision=null;
  function renderOfficial(){
    $('#official-accounts').replaceChildren();
    for(const mode of ['include','exclude'])for(const account of officialRules[mode]){const row=document.createElement('li'),text=document.createElement('span'),button=document.createElement('button');text.textContent='@'+account+' · '+(mode==='include'?'强制算进':'强制排除');button.type='button';button.textContent='删除';button.setAttribute('aria-label','删除规则：'+mode+' '+account);button.onclick=()=>{officialRules[mode]=officialRules[mode].filter(v=>v!==account);renderOfficial();$('#official-notice').textContent='尚未保存';};row.append(text,button);$('#official-accounts').append(row);}
    $('#official-save').disabled=officialRevision===null;
  }
  function officialResult(data){officialRules={include:data.include,exclude:data.exclude};officialRevision=data.revision;renderOfficial();$('#official-history').replaceChildren();for(const change of data.history.slice(-30).reverse()){const row=document.createElement('li');row.textContent=clock(change.at)+' · '+({add_include:'新增算进',remove_include:'删除算进',add_exclude:'新增排除',remove_exclude:'删除排除'}[change.action]||'变更')+' @'+change.handle;$('#official-history').append(row);}}
  async function loadOfficial(){try{officialResult(await api('/api/settings/official-accounts'));$('#official-notice').textContent='已读取官方号名单';}catch(e){officialRevision=null;$('#official-save').disabled=true;$('#official-notice').textContent=e.message;}}
  $('#official-add').onsubmit=e=>{e.preventDefault();const raw=$('#official-handle').value,value=raw.replace(/^@/,'').toLowerCase(),mode=$('#official-mode').value;if(!/^[a-z0-9_]{1,15}$/.test(value)){$('#official-notice').textContent='账号只能含1–15位英文字母、数字、下划线，不能有空白';return;}if(officialRules[mode].includes(value)){$('#official-notice').textContent='账号不能重复';return;}officialRules[mode].push(value);$('#official-handle').value='';renderOfficial();$('#official-notice').textContent='尚未保存';};
  $('#official-save').onclick=async()=>{const button=$('#official-save');button.disabled=true;try{officialResult(await api('/api/settings/official-accounts',{...officialRules,revision:officialRevision}));$('#official-notice').textContent='官方号名单已保存，今日页立即生效';}catch(e){$('#official-notice').textContent=e.message;}finally{button.disabled=officialRevision===null;}};
  let toolGroups=[],toolRevision=null;
  function renderToolGroups(){
    $('#wechat-tools-groups').replaceChildren();
    toolGroups.forEach((group,index)=>{const li=document.createElement('li'),text=document.createElement('span'),button=document.createElement('button');text.textContent=group;button.type='button';button.textContent='删除';button.setAttribute('aria-label','删除精选群：'+group);button.onclick=()=>{toolGroups.splice(index,1);renderToolGroups();$('#wechat-tools-notice').textContent='尚未保存';};li.append(text,button);$('#wechat-tools-groups').append(li);});
    $('#wechat-tools-save').disabled=toolRevision===null;
  }
  async function loadToolGroups(){try{const data=await api('/api/settings/wechat-tools');toolGroups=data.groups;toolRevision=data.revision;renderToolGroups();$('#wechat-tools-notice').textContent='已读取精选群';}catch(e){toolRevision=null;$('#wechat-tools-save').disabled=true;$('#wechat-tools-notice').textContent=e.message;}}
  $('#wechat-tools-add').onsubmit=e=>{e.preventDefault();const value=$('#wechat-tools-input').value;if(!value||value!==value.trim()||/[\r\n\u0000-\u001f\u200b-\u200f\ufeff]/.test(value)||value.length>120){$('#wechat-tools-notice').textContent='请填完整群名，不能含首尾空格或隐藏字符';return;}if(toolGroups.length>=30){$('#wechat-tools-notice').textContent='最多30个精选群';return;}if(!toolGroups.includes(value))toolGroups.push(value);$('#wechat-tools-input').value='';renderToolGroups();$('#wechat-tools-notice').textContent='尚未保存';};
  $('#wechat-tools-save').onclick=async()=>{const button=$('#wechat-tools-save');button.disabled=true;try{const data=await api('/api/settings/wechat-tools',{groups:toolGroups,revision:toolRevision});toolGroups=data.groups;toolRevision=data.revision;renderToolGroups();$('#wechat-tools-notice').textContent='精选群已保存';}catch(e){$('#wechat-tools-notice').textContent=e.message;}finally{button.disabled=toolRevision===null;}};
  async function refresh() {
    loadTopics();loadToolGroups();loadOfficial();
    $('#refresh').disabled = true; $('#notice').textContent = '';
    const results = await Promise.allSettled([
      api('/api/settings/overview'), api('/api/assistant/settings')
    ]);
    const [overview, models] = results.map(r=>r.status==='fulfilled'?r.value:null);
    if (overview) {
      $('#sources').innerHTML = overview.sources.map(s=>`<article class="card" data-source="${esc(s.id)}"><h3>${esc(s.name)}</h3><p title="${esc(s.reason)}">${s.enabled===null?'启用状态未知':s.enabled?'任务已启用':'任务已暂停'}</p><p title="${esc(s.reason)}">最近成功：${esc(clock(s.last_success))}</p><p title="${esc(s.reason)}">最近错误：${esc(s.last_error || '未知')}</p>${s.tasks.map(t=>`<div>${esc(t.name)} ${taskButton(t)}</div>`).join('')}<p><a href="${esc(s.href)}">查看实时状态 ↗</a></p></article>`).join('');
      $('#sources').setAttribute('aria-busy','false');
      $('#services').innerHTML = overview.services.map(s=>`<li><strong>${esc(s.label)}</strong> · 最近日志 ${esc(clock(s.last_log_at))}</li>`).join('');
    }
    if (models) {
      $('#active-model').textContent = '当前 profile：' + (models.display_name || models.model || '未选择');
      $('#models').innerHTML = models.profiles.map(p=>`<article class="card"><h3>${esc(p.display_name || p.model)}</h3><p>${esc(p.model)}</p><p>${p.configured?'可用':'配置不完整'} · 密钥${p.has_api_key?'已配置':'未配置'}${p.no_api_key?'（此接口无需密钥）':''}</p><button type="button" data-profile="${esc(p.profile_id)}" ${p.profile_id===models.active_profile_id || !p.configured?'disabled':''}>${p.profile_id===models.active_profile_id?'当前使用':'切换'}</button></article>`).join('') || '<p>暂无模型配置</p>';
    }
    const failures = results.map((r,i)=>r.status==='rejected'?['来源与服务','模型'][i]+'：'+r.reason.message:'').filter(Boolean);
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
