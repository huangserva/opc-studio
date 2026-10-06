(() => {
  'use strict';
  const $ = selector => document.querySelector(selector);
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const chars = value => Array.from(String(value ?? '')).length;
  const isId = value => typeof value === 'string' && /^[a-f0-9]{32}$/.test(value);
  const isHash = value => typeof value === 'string' && /^[a-f0-9]{64}$/.test(value);
  const views = {all:'全部事项',important:'重大事项',mine:'由我跟进',waiting:'等待回复',confirm:'待确认',calendar:'日程'};
  const statuses = {open:'待跟进',waiting:'等待回复',needs_confirmation:'待确认',done:'已完成',cancelled:'已取消'};
  const help = {all:'重大事项与需要跟进的记录，都在这里。',important:'只显示本人明确标记为重大的事项。',mine:'只列已接受、明确勾选“由我跟进”的行动，按约定时间排列（北京时间）；建议仍留在“待确认”。',waiting:'等待回复或反馈的事项。',confirm:'只显示标记为待确认的事项或行动。',calendar:'只显示已经核对、时间明确的待跟进或等待回复行动；候选与未确认日期保留在“待确认”。'};
  const state = {view:'all',events:[],counts:{},selected:null,listRequest:0,detailRequest:0,editor:null,document:null,documentRequest:0,knowledge:[],knowledgeNext:null,knowledgeRequest:0,knowledgeQuery:'',saving:false,proposal:null,suggestions:new Map()};
  const stamp = value => {
    if (!value || !Number.isFinite(Date.parse(value))) return '';
    return new Date(value).toLocaleString('zh-CN',{hour12:false,timeZoneName:'short'});
  };
  const hasDue = value => {
    if (typeof value!=='string') return false;
    const match=/^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(?::(\d{2})(?:\.\d{1,6})?)?(?:Z|([+-])(\d{2}):(\d{2}))$/.exec(value);
    if (!match) return false;
    const [,year,month,day,hour,minute,second,,offsetHour,offsetMinute]=match;
    const y=Number(year),m=Number(month),days=[31,y%4===0&&(y%100!==0||y%400===0)?29:28,31,30,31,30,31,31,30,31,30,31];
    return y>0&&m>=1&&m<=12&&Number(day)>=1&&Number(day)<=days[m-1]&&Number(hour)<24&&Number(minute)<60&&Number(second || 0)<60&&Number(offsetHour || 0)<24&&Number(offsetMinute || 0)<60&&Number.isFinite(Date.parse(value));
  };
  const evidenceLink = evidence => {
    const id = evidence.document_id || evidence.id;
    if (!isId(id)) return '';
    const params = new URLSearchParams({document:id});
    if (isHash(evidence.hash)) params.set('hash',evidence.hash);
    if (Number.isInteger(evidence.start) && Number.isInteger(evidence.end) && evidence.start >= 0 && evidence.end > evidence.start) {
      params.set('start',String(evidence.start)); params.set('end',String(evidence.end));
    }
    return '/knowledge.html?' + params;
  };
  function message(text, error=false, selector='#ev-status') {
    const node = $(selector); node.textContent=text; node.dataset.error=String(error);
  }
  async function api(path, body) {
    const response = await fetch(path, body === undefined ? {} : {method:'POST',headers:{'Content-Type':'application/json','X-Article-Studio':'1'},body:JSON.stringify(body)});
    let value;
    try { value = await response.json(); } catch (_) { throw new Error(`服务返回了无法读取的响应（HTTP ${response.status || '未知'}）。`); }
    if (!response.ok) {
      const detail = value && typeof value.error === 'string' ? value.error : `请求失败（HTTP ${response.status}）`;
      throw new Error(response.status === 409 ? '内容已变化，编辑内容已保留。' + detail : detail);
    }
    return value;
  }
  function provenance(doc) {
    const p = doc.provenance || {}, rows = [];
    if (p.local_path || p.localPath) rows.push(['原始文件',p.local_path || p.localPath]);
    if (p.project_name) rows.push(['所属项目',p.project_name]);
    if (p.slug) rows.push(['原始作品',p.slug]);
    if (p.version) rows.push(['引用版本',p.version]);
    if (p.chatroom_id) rows.push(['原始会话',p.chatroom_id]);
    if (p.local_id !== undefined) rows.push(['消息编号',String(p.local_id)]);
    if (p.noteId) rows.push(['remio 条目',p.noteId]);
    if (p.source_locator) rows.push(['原文定位',typeof p.source_locator === 'object' ? JSON.stringify(p.source_locator) : p.source_locator]);
    if (p.sourceUrl || doc.url) rows.push(['原始入口',p.sourceUrl || doc.url]);
    return rows.map(([label,value])=>`<dt>${esc(label)}</dt><dd>${esc(value)}</dd>`).join('');
  }
  function badges(event) {
    return `${event.priority === 'important' ? '<span class="ev-tag important">重大事项</span>' : '<span class="ev-tag">普通事项</span>'}${event.needs_confirmation ? '<span class="ev-tag confirm">待确认</span>' : ''}`;
  }
  function renderList() {
    $('#ev-view-title').textContent=views[state.view]; $('#ev-view-help').textContent=help[state.view];
    $('#ev-mobile-view').value=state.view;
    document.querySelectorAll('[data-view]').forEach(button=>{const selected=button.dataset.view===state.view;button.classList.toggle('selected',selected);button.setAttribute('aria-pressed',String(selected));});
    document.querySelectorAll('[data-count]').forEach(node=>{const count=state.counts[node.dataset.count];node.textContent=Number.isInteger(count)?count.toLocaleString():'—';});
    $('#ev-total').textContent=Number.isInteger(state.counts.all)?`${state.counts.all.toLocaleString()} 件事项 · 本机记录`:'本机事项';
    $('#ev-result-count').textContent=`${state.events.length.toLocaleString()} 件`;
    if (state.view === 'calendar') { renderCalendar(); return; }
    if (state.view === 'mine') { renderMyActions(); return; }
    $('#ev-list').innerHTML=state.events.length ? state.events.map(event=>{
      const actions=Array.isArray(event.actions)?event.actions:[];
      const unfinished=actions.filter(action=>['open','waiting'].includes(action.status));
      const pending=actions.filter(action=>action.status==='needs_confirmation');
      return `<button class="ev-card ${state.selected?.id===event.id?'selected':''}" data-event="${esc(event.id)}"><div class="ev-card-meta">${badges(event)}</div><h3>${esc(event.title)}</h3><p>${esc(event.summary || '尚未填写概要，可回到依据核对。')}</p><div class="ev-card-foot">${Array.isArray(event.evidence)?`<span>${event.evidence.length} 条依据</span>`:''}${Array.isArray(event.actions)?`<span>${unfinished.length} 项待跟进</span>`:''}${pending.length?`<span>${pending.length} 项待确认</span>`:''}${stamp(event.updated_at)?`<span>${esc(stamp(event.updated_at))}</span>`:''}</div></button>`;
    }).join('') : `<div class="ev-empty"><span class="ev-empty-icon">◷</span><p>${$('#ev-query').value.trim()?'没有匹配的事项。试试其它关键词。':state.view==='all'?'这里还没有事项。<br>从知识库选一个具体片段，建立第一件事。':'这个视图暂时没有事项。'}</p></div>`;
    bindList();
  }
  function renderMyActions() {
    const rows=state.events.flatMap(event=>(Array.isArray(event.actions)?event.actions:[]).filter(action=>action.owner_is_self===true&&['open','waiting'].includes(action.status)).map(action=>({event,action,time:hasDue(action.due_at)?Date.parse(action.due_at):null}))).sort((a,b)=>(a.time ?? Infinity)-(b.time ?? Infinity));
    const clock=Date.now(),dayFormat=new Intl.DateTimeFormat('en-CA',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit'}),today=dayFormat.format(new Date(clock));
    const groups=[['past','已过约定时间'],['today','今天'],['later','之后'],['unknown','时间待确认']];
    const group=row=>row.time===null?'unknown':row.time<clock?'past':dayFormat.format(new Date(row.time))===today?'today':'later';
    $('#ev-result-count').textContent=`${rows.length} 项行动`;
    $('#ev-list').innerHTML=rows.length?groups.map(([key,label])=>{
      const selected=rows.filter(row=>group(row)===key);
      if (!selected.length) return '';
      return `<section class="ev-followup-group" data-group="${key}"><h3 class="ev-calendar-heading">${label} · ${selected.length} 项</h3>${selected.map(({event,action,time})=>{
        const refs=(Array.isArray(action.evidence_ids)?action.evidence_ids:[]).map(id=>(event.evidence||[]).find(item=>item.id===id)).filter(Boolean);
        const sourceNote=refs.some(item=>item.state==='stale'||item.state==='missing')?' · 原依据需重新核对':'';
        return `<button class="ev-calendar-row ev-followup-row" data-event="${esc(event.id)}" data-focus-action="${esc(action.id)}">${time===null?'<time>时间待确认</time>':`<time datetime="${esc(action.due_at)}">${esc(new Date(time).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false}))}（北京时间）</time>`}<strong>${esc(action.summary)}</strong><small>${esc(event.title)} · ${esc(statuses[action.status])} · ${refs.length} 条依据${sourceNote}</small></button>`;
      }).join('')}</section>`;
    }).join(''):'<div class="ev-empty">暂无已接受、由我跟进的行动。<br>未确认的建议可在“待确认”中核对。</div>';
    bindList();
  }
  function renderCalendar() {
    const rows=state.events.flatMap(event=>(Array.isArray(event.actions)?event.actions:[]).filter(action=>['open','waiting'].includes(action.status)&&hasDue(action.due_at)).map(action=>({event,action}))).sort((a,b)=>Date.parse(a.action.due_at)-Date.parse(b.action.due_at));
    $('#ev-result-count').textContent=`${rows.length} 项约定`;
    $('#ev-list').innerHTML=rows.length ? rows.map(({event,action})=>`<button class="ev-calendar-row" data-event="${esc(event.id)}"><time datetime="${esc(action.due_at)}">${esc(stamp(action.due_at))}</time><strong>${esc(action.summary)}</strong><small>${esc(event.title)} · ${esc(action.owner || '负责人待确认')}${action.owner_is_self?' · 由我跟进':''} · ${esc(statuses[action.status] || '待确认')}</small></button>`).join('') : '<div class="ev-empty">暂无明确时间的行动。<br>未约定时间的事项不会被排进日程。</div>';
    bindList();
  }
  function bindList() { $('#ev-list').querySelectorAll('[data-event]').forEach(button=>button.onclick=()=>openEvent(button.dataset.event,button.dataset.focusAction).catch(error=>message(error.message,true))); }
  async function refreshList(refreshDetail=false) {
    const request=++state.listRequest;
    const params=new URLSearchParams({view:state.view,q:$('#ev-query').value.trim()});
    const data=await api('/api/events?'+params);
    if (request!==state.listRequest) return;
    if (!data || !Array.isArray(data.events)) throw new Error('事项列表响应缺少 events。');
    state.events=data.events;state.counts=data.counts||{};renderList();
    if (refreshDetail && state.selected && !state.editor) await openEvent(state.selected.id);
    else if (refreshDetail && state.editor) message('列表已刷新；正在编辑的内容已保留。');
  }
  async function openEvent(id,actionId='') {
    if (state.editor || state.saving) { message('正在编辑的内容已保留，请先保存或关闭编辑窗口。'); return; }
    if (!isId(id)) throw new Error('事项编号无效。');
    const request=++state.detailRequest;
    const event=await api('/api/events/'+encodeURIComponent(id));
    if (request!==state.detailRequest || state.editor) return;
    state.selected=event;renderList();renderDetail();message('');
    if (isId(actionId)) {
      const target=document.getElementById('ev-action-'+actionId);
      target?.focus({preventScroll:true});target?.scrollIntoView?.({block:'nearest'});
    }
  }
  function sourceTime(item) {
    const p=item.provenance || {};
    if (['wechat','wechat_links'].includes(item.kind) && typeof p.timestamp==='number' && Number.isFinite(p.timestamp) && p.timestamp>0 && p.timestamp<=253402300799) {
      return {value:p.timestamp*1000,label:'微信消息时间'};
    }
    if (item.kind==='mail' && typeof p.date_sent==='string' && /\s(?:[+-]\d{4}|UT|UTC|GMT)(?:\s+\([^)]*\))?$/i.test(p.date_sent) && Number.isFinite(Date.parse(p.date_sent))) {
      return {value:Date.parse(p.date_sent),label:'邮件头发信时间'};
    }
    if (hasDue(item.modified)) return {value:Date.parse(item.modified),label:item.kind==='mail'?'邮件来源记录时间':'资料来源记录时间'};
    return {value:null,label:'来源时间未记录'};
  }
  function renderSourceTimeline(evidence) {
    const rows=evidence.map((item,index)=>({item,index,time:sourceTime(item)})).sort((a,b)=>(a.time.value ?? Infinity)-(b.time.value ?? Infinity) || a.index-b.index);
    if (!rows.length) return '';
    const kinds={wechat:'微信',wechat_links:'微信分享链接',mail:'邮件',articles:'作品',projects:'项目',notes:'笔记'};
    return `<section class="ev-detail-section"><div class="ev-section-heading"><h3>线索时间线</h3><small>北京时间</small></div><p class="ev-view-help">按来源记录时间排列，保留原文引用。先后顺序不代表因果关系${state.selected?.needs_confirmation?'；这些线索的关联仍待确认':''}。</p><ol class="ev-source-timeline">${rows.map(({item,time})=>`<li><time>${time.value===null?'时间待核对':esc(new Date(time.value).toLocaleString('zh-CN',{hour12:false,timeZone:'Asia/Shanghai'}))}<small>${esc(time.label)}</small></time><span class="ev-tag">${esc(kinds[item.kind] || '资料')}</span> <a href="${esc(evidenceLink(item))}">${esc(item.title || '原文片段')}</a></li>`).join('')}</ol></section>`;
  }
  function renderDetail() {
    const event=state.selected;
    if (!event) return;
    const evidence=Array.isArray(event.evidence)?event.evidence:[],actions=Array.isArray(event.actions)?event.actions:[],history=Array.isArray(event.history)?event.history:[];
    $('#ev-detail').innerHTML=`<div class="ev-detail-head"><span class="eyebrow">EVENT CONTEXT</span><span>${badges(event)}</span></div><h2>${esc(event.title)}</h2><p class="ev-detail-summary">${esc(event.summary || '尚未填写概要。')}</p><div class="ev-detail-actions"><button id="ev-edit" class="button">编辑事项 / 补充依据</button><button id="ev-add-action" class="button primary" ${evidence.length?'':'disabled'}>＋ 添加行动</button></div><section class="ev-detail-section"><div class="ev-section-heading"><h3>行动与约定</h3><small>${actions.length} 项</small></div>${actions.length?actions.map(action=>renderAction(action,evidence)).join(''):'<p class="ev-view-help">还没有明确的下一步。可依据原文添加行动，负责人和时间允许留空。</p>'}${renderSuggestions(event)}</section>${renderSourceTimeline(evidence)}<section class="ev-detail-section"><div class="ev-section-heading"><h3>依据与原文</h3><small>${evidence.length} 条</small></div>${evidence.length?evidence.map((item,index)=>renderEvidence(item,index)).join(''):'<p class="ev-view-help">尚无依据，请补充具体资料片段。</p>'}</section><section class="ev-detail-section"><div class="ev-section-heading"><h3>事项变更记录</h3><small>保留每次变更</small></div>${history.length?`<ol class="ev-timeline">${history.map(renderHistory).join('')}</ol>`:'<p class="ev-view-help">暂无变更记录。</p>'}</section>`;
    $('#ev-edit').onclick=()=>openEventEditor(event);
    $('#ev-add-action').onclick=()=>openActionEditor();
    $('#ev-detail').querySelectorAll('[data-action]').forEach(button=>button.onclick=()=>openActionEditor(actions.find(action=>action.id===button.dataset.action)));
    $('#ev-detail').querySelectorAll('[data-candidate]').forEach(button=>button.onclick=()=>openActionEditor(null,state.suggestions.get(event.id)[Number(button.dataset.candidate)]));
  }
  function renderAction(action,evidence) {
    const refs=(action.evidence_ids||[]).map(id=>evidence.find(item=>item.id===id)).filter(Boolean);
    return `<article id="ev-action-${esc(action.id)}" class="ev-action" tabindex="-1"><div class="ev-action-top"><span class="ev-tag ${action.status==='done'?'done':action.status==='needs_confirmation'?'confirm':''}">${esc(statuses[action.status] || '待确认')}</span><button class="button subtle" data-action="${esc(action.id)}">编辑</button></div><p>${esc(action.summary)}</p><div class="ev-action-meta"><span>负责人：${esc(action.owner || '待确认')}${action.owner_is_self?' · 由我跟进':''}</span><span>${hasDue(action.due_at)?'约定：'+esc(stamp(action.due_at)):'时间待确认'}</span></div><div class="ev-action-evidence">${refs.map((item,index)=>`<a href="${esc(evidenceLink(item))}">依据 ${index+1} · ${esc(item.title)}</a>`).join('')}</div></article>`;
  }
  function renderEvidence(item,index) {
    const link=evidenceLink(item),range=Number.isInteger(item.start)&&Number.isInteger(item.end)?`原文片段 ${item.start+1}–${item.end}`:'原文范围待核对';
    const note=item.state==='stale'?'原资料已更新，以下保留收集时的快照。':item.state==='missing'?'原资料暂不可用，以下保留收集时的快照。':'';
    return `<article id="ev-evidence-${esc(item.id)}" class="ev-evidence"><h4>[${index+1}] ${esc(item.title)}</h4>${note?`<span class="ev-tag stale">${esc(note)}</span>`:''}<blockquote>${esc(item.excerpt)}</blockquote><small>${esc(range)}</small><dl class="ev-provenance">${provenance(item)}</dl>${link?`<a href="${esc(link)}">${item.state==='stale'?'核对当前原文':'回到原文'} ↗</a>`:''}</article>`;
  }
  function renderHistory(item) {
    const labels={created:'建立事项',event_created:'建立事项',updated:'更新事项',event_updated:'更新事项',action_created:'添加行动',action_updated:'更新行动'};
    const type=item.type || item.kind || item.operation;
    const title=item.summary || item.message || labels[type] || (item.entity_type==='action'?(item.before?'更新行动':'添加行动'):(item.before?'更新事项':'建立事项'));
    const time=stamp(item.created_at || item.at || item.timestamp || item.event_at);
    const before=item.before || {},after=item.after || {},fields={title:'名称',summary:'内容',owner:'负责人',owner_is_self:'由我跟进',due_at:'约定时间',status:'状态',priority:'重要程度',needs_confirmation:'待确认'};
    const value=(key,raw)=>key==='status'?(statuses[raw]||raw):key==='priority'?(raw==='important'?'重大事项':'普通事项'):typeof raw==='boolean'?(raw?'是':'否'):String(raw || '未明确');
    const changes=Object.keys(fields).filter(key=>key in after && before[key]!==after[key]).map(key=>`${fields[key]}：${item.before?value(key,before[key])+' → ':''}${value(key,after[key])}`);
    return `<li>${time?`<time>${esc(time)}</time>`:''}<strong>${esc(title)}</strong>${changes.length?`<p>${esc(changes.join('\n'))}</p>`:''}${item.reason?`<p>原因：${esc(item.reason)}</p>`:''}${item.origin?`<p>${item.origin==='manual'?'本人修改':item.origin==='evidence'?'依据资料更新':esc(item.origin)}</p>`:''}</li>`;
  }
  function renderSuggestions(event) {
    const candidates=state.suggestions.get(event.id)||[];
    return candidates.length?`<div class="ev-proposal-note">待核对的行动建议，尚未保存。请逐项确认负责人、时间与依据。</div>${candidates.map((candidate,index)=>`<div class="ev-proposal-action">${esc(candidate.summary)}<br><small>${esc(candidate.owner || '负责人待确认')} · ${hasDue(candidate.due_at)?esc(stamp(candidate.due_at)):'时间待确认'}</small> <button class="button subtle" data-candidate="${index}">核对并添加</button></div>`).join('')}` : '';
  }
  function openEventEditor(event=null,proposal=null) {
    if (state.saving || state.editor) return;
    ++state.detailRequest; ++state.documentRequest; ++state.knowledgeRequest;
    state.editor={kind:'event',id:event?.id || null,revision:event?.revision,baseEvidence:event?.evidence || [],picks:proposal?.picks || [],candidates:proposal?.action_candidates || [],proposal:Boolean(proposal)};
    $('#ev-editor-title').textContent=event?'编辑事项与依据':proposal?'核对建议并建立事项':'从资料建立事项';
    $('#ev-event-title').value=event?.title || proposal?.title || '';
    $('#ev-event-summary').value=event?.summary || proposal?.summary || '';
    $('#ev-event-priority').value=(event?.priority || proposal?.priority)==='important'?'important':'normal';
    $('#ev-event-confirm').checked=event ? Boolean(event.needs_confirmation) : proposal ? true : false;
    $('#ev-event-reason').value=''; $('#ev-event-reason').required=Boolean(event);
    $('#ev-event-save').textContent=event?'保存修改':'确认建立事项';
    $('#ev-existing-evidence').textContent=event?`保留现有 ${state.editor.baseEvidence.length} 条依据；下方可追加`:'';
    $('#ev-knowledge-query').value='';state.knowledge=[];state.knowledgeNext=null;state.knowledgeQuery='';$('#ev-knowledge-results').innerHTML='';$('#ev-knowledge-more').hidden=true;
    message('',false,'#ev-event-status');message('',false,'#ev-knowledge-status');
    $('#ev-proposed-actions').hidden=!state.editor.candidates.length;
    $('#ev-proposed-actions').innerHTML=state.editor.candidates.length?'<p class="ev-proposal-note">以下为待确认的行动建议，建立事项不会自动添加这些行动。</p>'+state.editor.candidates.map(candidate=>`<div class="ev-proposal-action">${esc(candidate.summary)}<br><small>${esc(candidate.owner || '负责人待确认')} · ${hasDue(candidate.due_at)?esc(stamp(candidate.due_at)):'时间待确认'}</small></div>`).join(''):'';
    renderPicks();$('#ev-event-dialog').showModal();
  }
  function validPick(pick) { return pick && isId(pick.id) && isHash(pick.hash) && Number.isInteger(pick.start) && Number.isInteger(pick.end) && pick.start>=0 && pick.end>pick.start; }
  function addPick(pick) {
    if (state.editor?.kind!=='event') return;
    if (!validPick(pick) || typeof pick.excerpt!=='string' || !pick.excerpt.trim() || chars(pick.excerpt)!==pick.end-pick.start) { message('这个片段缺少有效的版本或范围，请打开原文重新选择。',true,'#ev-knowledge-status'); return; }
    const picks=state.editor.picks;
    if (picks.some(item=>item.id===pick.id&&item.start===pick.start&&item.end===pick.end)) { message('这个片段已经选入。',false,'#ev-knowledge-status'); return; }
    const retained=state.editor.baseEvidence || [];
    if (picks.length+retained.length>=20 || [...picks,...retained].reduce((total,item)=>total+chars(item.excerpt),0)+chars(pick.excerpt)>12000) { message('现有与新增依据合计最多 20 个片段、12,000 字，请缩小引用范围。',true,'#ev-knowledge-status'); return; }
    picks.push(pick);renderPicks();message('已选择具体片段，原文版本与范围会一并保存。',false,'#ev-knowledge-status');
  }
  function renderPicks() {
    const picks=state.editor?.picks || [];
    $('#ev-picks').innerHTML=picks.map((pick,index)=>`<div class="ev-pick"><button type="button" data-remove-pick="${index}">移除</button><strong>${esc(pick.title)}</strong><p>${esc(pick.excerpt)}</p><small>原文片段 ${pick.start+1}–${pick.end} · ${chars(pick.excerpt)} 字</small></div>`).join('');
    $('#ev-pick-budget').textContent=picks.length?`${picks.length} 个片段 · ${picks.reduce((total,item)=>total+chars(item.excerpt),0).toLocaleString()} / 12,000 字`:state.editor?.id?'现有依据快照会保留；可检索新资料并追加。':'选择至少一个具体片段，保留原文定位。';
    $('#ev-picks').querySelectorAll('[data-remove-pick]').forEach(button=>button.onclick=()=>{state.editor.picks.splice(Number(button.dataset.removePick),1);renderPicks();});
  }
  async function searchKnowledge(more=false) {
    if (state.editor?.kind!=='event') return;
    const editor=state.editor,request=++state.knowledgeRequest;
    const query=more?state.knowledgeQuery:$('#ev-knowledge-query').value.trim();
    if (!query) { message('请输入原文中的关键词，再选择与事项相关的片段。',false,'#ev-knowledge-status'); return; }
    message('正在检索本机资料…',false,'#ev-knowledge-status');
    const params=new URLSearchParams({q:query,offset:String(more?state.knowledgeNext || 0:0)});
    const data=await api('/api/knowledge/search?'+params);
    if (request!==state.knowledgeRequest || editor!==state.editor) return;
    state.knowledge=more?[...state.knowledge,...data.results]:data.results;state.knowledgeNext=data.next_offset;state.knowledgeQuery=query;
    $('#ev-knowledge-more').hidden=state.knowledgeNext===null || state.knowledgeNext===undefined;
    $('#ev-knowledge-results').innerHTML=state.knowledge.length?state.knowledge.map((doc,index)=>`<article class="ev-knowledge-result"><h4>${esc(doc.title)}</h4><p>${esc(doc.excerpt || '这份资料没有可选择的文字片段。')}</p><div><button type="button" data-preview="${index}">查看原文 / 选取范围</button><button type="button" data-pick="${index}" ${doc.excerpt?'':'disabled'}>使用这个片段</button></div></article>`).join(''):'<div class="ev-empty">没有匹配的资料，请换一个原文关键词。</div>';
    $('#ev-knowledge-results').querySelectorAll('[data-preview]').forEach(button=>button.onclick=()=>previewDocument(state.knowledge[Number(button.dataset.preview)].id).catch(error=>message(error.message,true,'#ev-knowledge-status')));
    $('#ev-knowledge-results').querySelectorAll('[data-pick]').forEach(button=>button.onclick=()=>{const doc=state.knowledge[Number(button.dataset.pick)];addPick({...doc,start:doc.excerpt_start,end:doc.excerpt_start+chars(doc.excerpt)});});
    message(`${Number.isInteger(data.total)?data.total:state.knowledge.length} 份匹配资料；只会保存本人选中的片段。`,false,'#ev-knowledge-status');
  }
  async function previewDocument(id) {
    const editor=state.editor,request=++state.documentRequest;
    const doc=await api('/api/knowledge/document/'+encodeURIComponent(id));
    if (request!==state.documentRequest || editor!==state.editor) return;
    state.document=doc;$('#ev-document-title').textContent=doc.title;$('#ev-document-body').value=doc.body || '';
    $('#ev-document-origin').innerHTML=provenance(doc);$('#ev-document-link').href=evidenceLink({document_id:doc.id});
    message('选中与这件事有关的原文，保留具体范围。',false,'#ev-document-status');$('#ev-document-dialog').showModal();
  }
  function pickSelection() {
    const field=$('#ev-document-body'),doc=state.document;
    if (!doc || field.selectionStart===field.selectionEnd) { message('请先选中需要引用的文字。',true,'#ev-document-status'); return; }
    // A textarea collapses CRLF and CR to LF. Map its UTF-16 positions back
    // to the immutable original before computing Python code-point ranges.
    const body=String(doc.body || '');
    const rawOffset=displayOffset=>{
      let raw=0,display=0;
      while(display<displayOffset && raw<body.length){raw+=body[raw]==='\r'&&body[raw+1]==='\n'?2:1;display++;}
      return raw;
    };
    const from=rawOffset(field.selectionStart),to=rawOffset(field.selectionEnd);
    const splitsCharacter=offset=>offset>0&&offset<body.length&&/[\uD800-\uDBFF]/.test(body[offset-1])&&/[\uDC00-\uDFFF]/.test(body[offset]);
    if (body.replace(/\r\n?/g,'\n')!==field.value || splitsCharacter(from) || splitsCharacter(to)) { message('请完整选中文字，原文范围暂无法核对。',true,'#ev-document-status');return; }
    const excerpt=body.slice(from,to),start=chars(body.slice(0,from));
    addPick({...doc,excerpt,start,end:start+chars(excerpt)});
    message($('#ev-knowledge-status').textContent,$('#ev-knowledge-status').dataset.error==='true','#ev-document-status');
  }
  function openActionEditor(action=null,candidate=null) {
    if (state.saving || state.editor || !state.selected) return;
    const event=state.selected,seed=action || candidate || {};
    state.editor={kind:'action',eventId:event.id,id:action?.id || null,revision:action?.revision,candidate};++state.detailRequest;
    $('#ev-action-editor-title').textContent=action?'编辑行动与约定':candidate?'核对建议并添加行动':'添加行动';
    $('#ev-action-summary').value=seed.summary || '';$('#ev-action-owner').value=seed.owner || '';$('#ev-action-self').checked=seed.owner_is_self===true;
    $('#ev-action-due').value=seed.due_at || '';$('#ev-action-status').value=statuses[seed.status]?seed.status:'open';$('#ev-action-reason').value='';$('#ev-action-reason').required=Boolean(action);
    const chosen=action?.evidence_ids || [];
    $('#ev-action-evidence').innerHTML='<legend>选择行动依据（至少一项）</legend>'+(event.evidence || []).map(item=>`<label class="ev-checkbox"><input type="checkbox" name="evidence" value="${esc(item.id)}" ${chosen.includes(item.id)?'checked':''}><span>${esc(item.title)}${item.state==='stale'?' · 收集时的快照':''}<small>${esc(item.excerpt)}</small></span></label>`).join('');
    message(candidate?'这条建议尚未保存，请确认内容并选择依据。':'',false,'#ev-action-form-status');$('#ev-action-dialog').showModal();
  }
  function setSaving(form,saving) {
    state.saving=saving;form.querySelectorAll('input,textarea,select,button').forEach(node=>{
      if (saving) { node.dataset.evWasDisabled=String(node.disabled);node.disabled=true; }
      else { node.disabled=node.dataset.evWasDisabled==='true';delete node.dataset.evWasDisabled; }
    });
  }
  async function saveEvent(event) {
    event.preventDefault();if (state.saving || state.editor?.kind!=='event') return;
    const editor=state.editor,form=event.currentTarget,title=$('#ev-event-title').value.trim(),reason=$('#ev-event-reason').value.trim();
    if (!title) { message('请填写事项名称。',true,'#ev-event-status'); return; }
    if (!editor.id && !editor.picks.length) { message('请先选择至少一个具体的依据片段。',true,'#ev-event-status'); return; }
    if (editor.id && !reason) { message('请填写修改原因，保留这次变更的来由。',true,'#ev-event-status'); return; }
    const body={title,summary:$('#ev-event-summary').value.trim(),priority:$('#ev-event-priority').value,needs_confirmation:$('#ev-event-confirm').checked,reason:reason || '手动建立事项',origin:'manual'};
    if (editor.picks.length) body.items=editor.picks.map(({id,hash,start,end})=>({id,hash,start,end}));
    if (editor.id) { body.id=editor.id;body.revision=editor.revision; }
    setSaving(form,true);message('正在保存事项…',false,'#ev-event-status');
    try {
      const saved=await api('/api/events',body);state.selected=saved;
      if (editor.candidates.length) state.suggestions.set(saved.id,editor.candidates);
      if (editor.proposal) { try { sessionStorage.removeItem('studio-event-proposal'); } catch (_) {} state.proposal=null; }
      state.editor=null;$('#ev-event-dialog').close();renderDetail();await refreshList();message(editor.id?'事项修改已保存，依据快照继续保留。':'事项已建立，可继续确认下一步行动。');
    } catch (error) { message(error.message,true,state.editor===editor?'#ev-event-status':'#ev-status'); }
    finally { setSaving(form,false); }
  }
  async function saveAction(event) {
    event.preventDefault();if (state.saving || state.editor?.kind!=='action') return;
    const editor=state.editor,form=event.currentTarget,summary=$('#ev-action-summary').value.trim(),due=$('#ev-action-due').value.trim(),reason=$('#ev-action-reason').value.trim();
    const ids=Array.from($('#ev-action-evidence').querySelectorAll('input:checked'),node=>node.value);
    if (!summary) { message('请填写需要跟进的事情。',true,'#ev-action-form-status'); return; }
    if (!ids.length) { message('请选择至少一条行动依据。',true,'#ev-action-form-status'); return; }
    if (due && !hasDue(due)) { message('时间必须是带时区的 ISO 格式，例如 2026-10-05T14:00:00+08:00；未明确可留空。',true,'#ev-action-form-status'); return; }
    if (editor.id && !reason) { message('请填写修改原因。',true,'#ev-action-form-status'); return; }
    const body={summary,owner:$('#ev-action-owner').value.trim(),owner_is_self:$('#ev-action-self').checked,due_at:due,status:$('#ev-action-status').value,evidence_ids:ids,origin:'manual',reason:reason || '手动添加行动'};
    if (editor.id) { body.id=editor.id;body.revision=editor.revision; }
    setSaving(form,true);message('正在保存行动…',false,'#ev-action-form-status');
    try {
      state.selected=await api('/api/events/'+encodeURIComponent(editor.eventId)+'/actions',body);
      if (editor.candidate) state.suggestions.set(editor.eventId,(state.suggestions.get(editor.eventId)||[]).filter(item=>item!==editor.candidate));
      state.editor=null;$('#ev-action-dialog').close();renderDetail();await refreshList();message('行动已保存，依据和修改原因一并保留。');
    }
    catch (error) { message(error.message,true,state.editor===editor?'#ev-action-form-status':'#ev-status'); }
    finally { setSaving(form,false); }
  }
  async function importProposal() {
    let raw;try { raw=sessionStorage.getItem('studio-event-proposal'); } catch (_) { return; }
    if (!raw) return;
    let proposal;try { proposal=JSON.parse(raw); } catch (_) { throw new Error('建议内容无法读取，请回到助手重新生成预览。'); }
    if (!proposal || typeof proposal.title!=='string' || !proposal.title.trim() || chars(proposal.title)>200 || (proposal.summary!==undefined&&typeof proposal.summary!=='string') || chars(proposal.summary || '')>4000 || !['normal','important'].includes(proposal.priority) || typeof proposal.needs_confirmation!=='boolean' || !Array.isArray(proposal.items) || !proposal.items.length || proposal.items.length>20 || !proposal.items.every(validPick)) throw new Error('建议缺少有效的名称、版本或依据范围，请重新核对资料。');
    const docs=new Map(),picks=[];let total=0;
    for (const item of proposal.items) {
      if (!docs.has(item.id)) docs.set(item.id,await api('/api/knowledge/document/'+encodeURIComponent(item.id)));
      const doc=docs.get(item.id),body=Array.from(doc.body || '');
      if (doc.id!==item.id || doc.hash!==item.hash || item.end>body.length) throw new Error('建议引用的原资料已变化或范围无效，请回到助手重新核对。建议尚未保存。');
      const excerpt=body.slice(item.start,item.end).join('');total+=chars(excerpt);
      if (total>12000) throw new Error('建议引用超过 12,000 字，请缩小范围后重新核对。');
      picks.push({...doc,...item,excerpt});
    }
    const candidates=Array.isArray(proposal.action_candidates)?proposal.action_candidates.filter(item=>item&&typeof item.summary==='string'&&item.summary.trim()&&chars(item.summary)<=1000).slice(0,20).map(item=>({summary:item.summary,owner:typeof item.owner==='string'?Array.from(item.owner).slice(0,200).join(''):'',owner_is_self:item.owner_is_self===true,due_at:hasDue(item.due_at)?item.due_at:'',status:'needs_confirmation'})):[];
    state.proposal=proposal;
    if (state.editor) { message('建议已核验并保留；当前编辑内容不会被覆盖。可稍后重新打开本页核对建议。');return; }
    openEventEditor(null,{...proposal,picks,action_candidates:candidates});
  }
  document.querySelectorAll('[data-close]').forEach(button=>button.onclick=()=>{
    if (state.saving) return;
    const dialog=button.closest('dialog');dialog.close();
    if (dialog.id==='ev-event-dialog' || dialog.id==='ev-action-dialog') { state.editor=null;++state.knowledgeRequest;++state.documentRequest; }
  });
  for (const id of ['ev-event-dialog','ev-action-dialog']) $( '#'+id).addEventListener('cancel',event=>{if(state.saving){event.preventDefault();return;}state.editor=null;++state.knowledgeRequest;++state.documentRequest;});
  document.querySelectorAll('[data-view]').forEach(button=>button.onclick=()=>{state.view=button.dataset.view;refreshList().catch(error=>message(error.message,true));});
  $('#ev-mobile-view').onchange=event=>{state.view=event.target.value;refreshList().catch(error=>message(error.message,true));};
  $('#ev-search-form').onsubmit=event=>{event.preventDefault();refreshList().catch(error=>message(error.message,true));};
  $('#ev-refresh').onclick=()=>refreshList(true).catch(error=>message(error.message,true));
  $('#ev-new').onclick=()=>openEventEditor();
  $('#ev-event-form').onsubmit=saveEvent;$('#ev-action-form').onsubmit=saveAction;
  $('#ev-knowledge-search').onclick=()=>searchKnowledge().catch(error=>message(error.message,true,'#ev-knowledge-status'));
  $('#ev-knowledge-more').onclick=()=>searchKnowledge(true).catch(error=>message(error.message,true,'#ev-knowledge-status'));
  $('#ev-knowledge-query').onkeydown=event=>{if(event.key==='Enter'){event.preventDefault();searchKnowledge().catch(error=>message(error.message,true,'#ev-knowledge-status'));}};
  $('#ev-pick-selection').onclick=pickSelection;
  async function initialize() {
    const requested=new URLSearchParams(location.search).get('event');
    try { await refreshList(); }
    catch(error){message(error.message,true);$('#ev-list').innerHTML='<div class="ev-empty">事项暂时无法读取。<br>请查看上方错误信息后重试。</div>';}
    // A direct event link takes precedence over an older, unsaved proposal.
    // Neither path may replace an editor the user opened while loading.
    if (requested!==null) {
      if (!isId(requested)) { message('事项链接中的编号无效，请重新选择事项。',true);return; }
      await openEvent(requested);
    } else { await importProposal(); }
  }
  initialize().catch(error=>message(error.message,true));
})();
