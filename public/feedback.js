(() => {
  const $=s=>document.querySelector(s),esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const state={bindings:[],snapshots:[],observations:[],selected:null,listRequest:0,detailRequest:0,saving:false,file:null,fileRequest:0};
  const chars=s=>Array.from(s||'').length,key=b=>b.slug+':'+b.delivery_id;
  const stamp=s=>s?new Date(s).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false}):'未提供';
  const names={x:'X',wechat:'微信公众号',douyin:'抖音',xiaohongshu:'小红书',channels:'视频号'};
  function message(value,error=false,selector='#fb-status'){$(selector).textContent=value;$(selector).dataset.error=String(error);}
  async function api(url,body){const r=await fetch(url,body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json','X-Article-Studio':'1'},body:JSON.stringify(body)});const data=await r.json();if(!r.ok)throw new Error(data.error||'操作未完成');return data;}
  const label=b=>`${b.title} · ${names[b.platform]||b.platform} · ${b.version}${b.account?' · '+b.account:''}`;
  function href(value){try{if(typeof value!=='string'||/[\s\x00-\x1f\\]/.test(value))return '';if(value.startsWith('/?article=')||value.startsWith('/knowledge.html?'))return value;const u=new URL(value);return u.protocol==='https:'&&!u.username&&!u.password?u.href:'';}catch(_){return '';}}
  function link(value,title){const url=href(value);return url?`<a class="button" href="${esc(url)}" target="_blank" rel="noopener noreferrer">${esc(title)}</a>`:'';}
  function postKey(value){try{const url=new URL(href(value)),match=url.pathname.match(/^\/([A-Za-z0-9_]{1,15})\/status\/([0-9]{1,25})$/);return ['x.com','www.x.com','twitter.com','www.twitter.com'].includes(url.hostname)&&match?match[1].toLowerCase()+':'+match[2]:null;}catch(_){return null;}}
  function matchingBindings(o){const post=postKey(o.post_url);return post?state.bindings.filter(b=>b.platform==='x'&&postKey(b.url)===post):[];}
  function observationBinding(o){
    if(o.version_binding)return `<p class="fb-notice">已关联 ${esc(o.version_binding.version)} · ${esc(o.version_binding.title)}</p>${link(o.version_binding.work_url,'核对固定作品版本 ↗')}`;
    const candidates=matchingBindings(o);
    if(!candidates.length)return '<p class="fb-notice">工作台尚无同一原帖的发布登记。核对作品和发布版本后，在作品页登记原帖链接，再回来关联。</p>';
    return `<div class="fb-source-actions"><label>同一原帖的发布版本 <select data-observation-binding="${esc(o.id)}">${candidates.map(b=>`<option value="${esc(key(b))}">${esc(label(b))}</option>`).join('')}</select></label><button class="button" data-observation-bind="${esc(o.id)}">关联所选固定版本</button></div>`;
  }
  async function refresh(){
    const data=await api('/api/feedback');state.bindings=data.bindings||[];
    const selected=$('#fb-filter').value;
    $('#fb-filter').innerHTML='<option value="">全部发布项</option>'+state.bindings.map(b=>`<option value="${esc(key(b))}">${esc(label(b))}</option>`).join('');
    if(state.bindings.some(b=>key(b)===selected))$('#fb-filter').value=selected;
    $('#fb-new').disabled=!state.bindings.length;$('#fb-empty-bindings').hidden=Boolean(state.bindings.length);
    if(data.unavailable)message(`${data.unavailable} 个发布项的原版本或链接无法核对，暂不能绑定统计。`);
    await refreshList();
    const observations=await api('/api/feedback/observations');state.observations=observations.observations||[];renderObservations();
  }
  function renderObservations(){
    $('#fb-observations').hidden=!state.observations.length;
    $('#fb-observation-list').innerHTML=state.observations.map(o=>`<article class="fb-observation" id="observation-${esc(o.id)}"><h3>${esc(o.title)}</h3><p>${esc(names[o.platform]||o.platform)} · @${esc(o.account)} · 采集于 ${esc(stamp(o.observed_at))}</p><p class="fb-notice">${esc(o.notice)}</p>${observationBinding(o)}<div class="fb-source-actions">${link(o.post_url,'查看原帖 ↗')}${link(o.source_url,'统计来源页面 ↗')}<button class="button" data-observation-raw="${esc(o.id)}">核对采集原文</button><button class="button" data-observation-knowledge="${esc(o.id)}">${o.knowledge_id?'核对回库资料':'将原始资料回库'}</button></div><table class="fb-metrics"><thead><tr><th>平台原始指标</th><th>原始显示值</th></tr></thead><tbody>${o.metrics.map(m=>`<tr><td>${esc(m.scope)} / ${esc(m.label)}</td><td>${esc(m.value)}</td></tr>`).join('')}</tbody></table><div class="fb-observation-raw" hidden><p class="fb-notice">原文 SHA-256：${esc(o.raw_sha256)}</p><textarea readonly rows="8" aria-label="采集的原始页面文本"></textarea></div><a class="button fb-observation-knowledge-link" hidden>打开知识库资料 ↗</a></article>`).join('');
    $('#fb-observation-list').querySelectorAll('[data-observation-raw]').forEach(b=>b.onclick=()=>openObservation(b.dataset.observationRaw).catch(e=>message(e.message,true)));
    $('#fb-observation-list').querySelectorAll('[data-observation-knowledge]').forEach(b=>b.onclick=async()=>{b.disabled=true;try{const result=await api('/api/feedback/observation/'+b.dataset.observationKnowledge+'/knowledge',{});const url=href(result.url);if(!url)throw Error('回库资料链接无法核对');const a=b.closest('article').querySelector('.fb-observation-knowledge-link');a.href=url;a.hidden=false;b.textContent='核对回库资料';message(result.summary);}catch(e){message(e.message,true);}finally{b.disabled=false;}});
    $('#fb-observation-list').querySelectorAll('[data-observation-bind]').forEach(button=>button.onclick=async()=>{
      const select=button.closest('article').querySelector('[data-observation-binding]'),o=state.observations.find(o=>o.id===button.dataset.observationBind),b=o&&matchingBindings(o).find(b=>key(b)===select.value);
      if(!b){message('请刷新并核对同一原帖的发布登记。',true);return;}
      button.disabled=true;select.disabled=true;
      try{const linked=await api('/api/feedback/observation/'+o.id+'/bind',{slug:b.slug,delivery_id:b.delivery_id,version:b.version});await refresh();await openObservation(linked.id);message('已生成关联固定发布版本的资料，原始采集资料保留；统计窗口仍待核对。');}
      catch(e){message(e.message,true);}finally{button.disabled=false;select.disabled=false;}
    });
  }
  async function openObservation(id){
    if(!/^[a-f0-9]{32}$/.test(id))throw Error('平台采集资料编号无效');
    const o=await api('/api/feedback/observation/'+id);
    if(!state.observations.some(o=>o.id===id)){state.observations.unshift(o);renderObservations();}
    const card=document.getElementById('observation-'+id);card.querySelector('textarea').value=o.raw;card.querySelector('.fb-observation-raw').hidden=false;card.scrollIntoView?.({block:'start'});
  }
  async function refreshList(){
    const request=++state.listRequest,binding=state.bindings.find(b=>key(b)===$('#fb-filter').value),params=new URLSearchParams();
    if(binding){params.set('slug',binding.slug);params.set('delivery_id',binding.delivery_id);}
    const data=await api('/api/feedback/snapshots?'+params);if(request!==state.listRequest)return;
    state.snapshots=data.snapshots||[];$('#fb-total').textContent=`${data.total||0} 份原始统计快照`;
    renderList();
  }
  function renderList(){
    $('#fb-list').innerHTML=state.snapshots.length?state.snapshots.map(s=>`<button class="fb-snapshot" data-snapshot="${esc(s.id)}" aria-pressed="${s.id===state.selected?.id}"><strong>${esc(s.binding.title)}</strong><small>${esc(names[s.binding.platform]||s.binding.platform)} · ${esc(s.binding.version)} · ${esc(stamp(s.collected_at))}</small><small>${esc(s.range.label)}</small><small>${s.provenance.type==='csv'?'原始 CSV':'手动录入'} · ${s.knowledge_id?'已回库':'待回库'}</small></button>`).join(''):'<div class="fb-empty">尚未导入统计快照。<br>从后台录入指标或选择原始 CSV，保留统计口径与时间范围。</div>';
    $('#fb-list').querySelectorAll('[data-snapshot]').forEach(b=>b.onclick=()=>openSnapshot(b.dataset.snapshot).catch(e=>message(e.message,true)));
  }
  async function openSnapshot(id){
    if(!/^[a-f0-9]{32}$/.test(id))throw new Error('统计快照编号无效');const request=++state.detailRequest;
    const snapshot=await api('/api/feedback/snapshot/'+id);if(request!==state.detailRequest)return;
    state.selected=snapshot;renderDetail();renderList();
  }
  function renderDetail(){
    const s=state.selected;if(!s)return;const b=s.binding,p=s.provenance,r=s.range;
    const rows=[['发布项',b.delivery_id],['固定作品版本',b.version],['发布登记时间',stamp(b.registration_at)],['采集时间',stamp(s.collected_at)],['统计范围',`${r.kind==='cumulative'?'累计':'区间'} · ${r.start?stamp(r.start):'起点未提供'} 至 ${stamp(r.end)}`],['统计口径',r.label],['快照指纹',s.hash]];
    if(p.type==='csv')rows.push(['原始文件',p.filename],['原文件 SHA-256',p.sha256],['选定数据行',`${p.row}（原文件第 ${p.line_start}–${p.line_end} 行）`],['指标来源列',p.metric_columns.join('、')]);else rows.push(['来源说明',p.note||'未提供额外说明']);
    $('#fb-detail').innerHTML=`<h2>${esc(b.title)}</h2><p class="fb-notice">${esc(s.notice||'用户导入，未向平台校验；不同统计口径和时间窗口不能直接比较。')}</p><div class="fb-source-actions">${link(b.url,'查看发布链接 ↗')}${link(b.work_url,'核对固定作品版本 ↗')}${p.url?link(p.url,'统计来源页面 ↗'):''}</div><dl class="fb-meta">${rows.map(([k,v])=>`<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join('')}</dl><h3>原始指标</h3><table class="fb-metrics"><thead><tr><th>平台原始名称</th><th>原始数值或文字</th></tr></thead><tbody>${Object.entries(s.metrics).map(([k,v])=>`<tr><td>${esc(k)}</td><td>${esc(v)}</td></tr>`).join('')}</tbody></table>${p.type==='csv'?'<details><summary>核对原始 CSV</summary><textarea id="fb-raw-csv" readonly rows="8" aria-label="导入的原始 CSV 内容"></textarea><button id="fb-download-csv" class="button" type="button">下载原始 CSV</button></details>':''}<div class="fb-source-actions"><button id="fb-knowledge" class="button primary">${s.knowledge_id?'核对回库资料':'将这份原始统计回库'}</button><a id="fb-knowledge-link" class="button" hidden>打开知识库资料 ↗</a></div><p class="fb-notice">回库保留发布项、固定版本、指标口径与来源；尚未生成模型复盘。复盘结论应与事实分开，不从一次表现推定原因。</p>`;
    if(p.type==='csv'){$('#fb-raw-csv').value=p.text;$('#fb-download-csv').onclick=()=>{const blob=new Blob([p.text],{type:'text/csv;charset=utf-8'}),url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download=p.filename;a.click();URL.revokeObjectURL(url);};}
    $('#fb-knowledge').onclick=async()=>{const button=$('#fb-knowledge');button.disabled=true;try{const result=await api('/api/feedback/snapshot/'+s.id+'/knowledge',{});if(state.selected?.id!==s.id)return;const url=href(result.url);if(!url)throw new Error('回库资料链接无法核对');$('#fb-knowledge-link').href=url;$('#fb-knowledge-link').hidden=false;message(result.summary||'原始统计已回库。');await refreshList();}catch(e){message(e.message,true);}finally{button.disabled=false;}};
  }
  function addMetric(name='',value=''){
    if($('#fb-metric-rows').children.length>=40){message('原始指标最多 40 项。',true,'#fb-form-status');return;}
    const row=document.createElement('div');row.className='fb-metric-row';row.innerHTML='<input class="fb-metric-name" maxlength="80" aria-label="原始指标名称" placeholder="后台的指标名称"><input class="fb-metric-value" maxlength="200" aria-label="原始指标数值或文字" placeholder="后台的原始数值或文字"><button type="button" aria-label="移除这项指标">移除</button>';
    row.querySelector('.fb-metric-name').value=name;row.querySelector('.fb-metric-value').value=value;row.querySelector('button').onclick=()=>row.remove();$('#fb-metric-rows').append(row);
  }
  function bindingInfo(){const b=state.bindings.find(b=>key(b)===$('#fb-binding').value);$('#fb-binding-info').textContent=b?`${names[b.platform]} · 固定版本 ${b.version} · ${b.url}`:'';}
  function sourceKind(){const csv=$('#fb-source-kind').value==='csv';$('#fb-csv').hidden=!csv;$('#fb-manual').hidden=csv;$('#fb-manual-url').required=!csv;}
  function rangeKind(){$('#fb-range-start').required=$('#fb-range-kind').value==='interval';}
  function openEditor(){if(state.saving||!state.bindings.length)return;$('#fb-form').reset();state.file=null;++state.fileRequest;$('#fb-csv-preview').textContent='选择文件后可核对原始表头。';$('#fb-binding').innerHTML=state.bindings.map(b=>`<option value="${esc(key(b))}">${esc(label(b))}</option>`).join('');if($('#fb-filter').value)$('#fb-binding').value=$('#fb-filter').value;$('#fb-collected').value=new Date().toISOString();$('#fb-metric-rows').innerHTML='';addMetric();sourceKind();rangeKind();bindingInfo();message('',false,'#fb-form-status');$('#fb-dialog').showModal();}
  $('#fb-csv-file').onchange=async()=>{const request=++state.fileRequest,file=$('#fb-csv-file').files?.[0];state.file=null;if(!file)return;try{if(file.size>1024000||!file.name.toLowerCase().endsWith('.csv'))throw new Error('请选择最多 256,000 字的原始 CSV 文件。');const text=await file.text();if(request!==state.fileRequest)return;if(chars(text)>256000)throw new Error('原始 CSV 最多 256,000 字。');state.file={filename:file.name,text};$('#fb-csv-preview').textContent=text.slice(0,2000);message('已读取本机文件；尚未导入。',false,'#fb-form-status');}catch(e){if(request===state.fileRequest)message(e.message,true,'#fb-form-status');}};
  $('#fb-form').onsubmit=async event=>{
    event.preventDefault();if(state.saving)return;
    try{
      const b=state.bindings.find(b=>key(b)===$('#fb-binding').value);if(!b)throw new Error('请先绑定已登记的发布项。');
      const payload={slug:b.slug,delivery_id:b.delivery_id,platform:b.platform,version:b.version,collected_at:$('#fb-collected').value.trim(),range:{kind:$('#fb-range-kind').value,start:$('#fb-range-start').value.trim(),end:$('#fb-range-end').value.trim(),label:$('#fb-range-label').value.trim()}};
      if($('#fb-source-kind').value==='csv'){if(!state.file)throw new Error('请先选择原始 CSV 文件。');payload.provenance={type:'csv',...state.file,row:Number($('#fb-csv-row').value),url_column:$('#fb-csv-url-column').value.trim(),metric_columns:$('#fb-csv-columns').value.split('\n').map(x=>x.trim()).filter(Boolean),url:$('#fb-csv-url').value.trim()};}
      else{payload.metrics=Object.create(null);for(const row of $('#fb-metric-rows').children){const name=row.querySelector('.fb-metric-name').value.trim(),value=row.querySelector('.fb-metric-value').value;if(!name&&!value.trim())continue;if(!name||!value.trim())throw new Error('每个指标需要原始名称和数值，缺失值请移除该行。');if(Object.hasOwn(payload.metrics,name))throw new Error('原始指标名称不能重复。');payload.metrics[name]=value;}if(!Object.keys(payload.metrics).length)throw new Error('请至少填写一个后台原始指标。');payload.provenance={type:'manual',url:$('#fb-manual-url').value.trim(),note:$('#fb-manual-note').value.trim()};}
      state.saving=true;const nodes=[...new Set([...$('#fb-form').querySelectorAll('input,select,textarea,button'),...$('#fb-dialog').querySelectorAll('[data-close]')])];nodes.forEach(n=>{n.dataset.wasDisabled=String(n.disabled);n.disabled=true;});
      try{const saved=await api('/api/feedback/import',payload);$('#fb-dialog').close();await refreshList();await openSnapshot(saved.id);message('原始统计快照已导入；可核对来源后回库。');}finally{state.saving=false;nodes.forEach(n=>{n.disabled=n.dataset.wasDisabled==='true';delete n.dataset.wasDisabled;});}
    }catch(e){message(e.message,true,$('#fb-dialog').open?'#fb-form-status':'#fb-status');}
  };
  $('#fb-new').onclick=openEditor;$('#fb-add-metric').onclick=()=>addMetric();$('#fb-binding').onchange=bindingInfo;$('#fb-source-kind').onchange=sourceKind;$('#fb-range-kind').onchange=rangeKind;
  $('#fb-filter').onchange=()=>refreshList().catch(e=>message(e.message,true));$('#fb-refresh').onclick=()=>refresh().catch(e=>message(e.message,true));
  $('#fb-dialog').addEventListener('cancel',event=>{if(state.saving)event.preventDefault();});document.querySelectorAll('[data-close]').forEach(button=>button.onclick=()=>{if(!state.saving)$('#fb-dialog').close();});
  refresh().then(()=>{const params=new URLSearchParams(location.search),id=params.get('snapshot'),observation=params.get('observation'),binding=params.get('binding');if(observation)return openObservation(observation);if(id)return openSnapshot(id);if(binding&&state.bindings.some(b=>key(b)===binding)){$('#fb-filter').value=binding;refreshList().catch(e=>message(e.message,true));if(params.get('new')==='1')openEditor();}}).catch(e=>message(e.message,true));
})();
