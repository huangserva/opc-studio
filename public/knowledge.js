(() => {
  const $ = s => document.querySelector(s);
  const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const names = {articles:'文案与作品',external:'外部文章 · 他人作品',projects:'项目资料',wechat:'微信记录',wechat_links:'微信分享链接',mail:'邮件',github:'GitHub 动态',notes:'笔记与资料'};
  const statuses = {pending:'待同步',syncing:'同步中',ready:'已接入',empty:'暂无资料',partial:'部分已同步',error:'同步异常',paused:'已暂停'};
  const state = {sources:[],items:[],picks:[],dossier:null,document:null,query:'',kind:'',tag:'',source:'',next:null,request:0,busy:false,syncing:false};
  const chars = s => Array.from(s).length;
  const normalized = s => s.replace(/\r\n?/g,'\n');
  function rawOffset(raw, displayOffset) {
    const points=Array.from(raw);let shown=0,at=0;
    while(at<points.length&&shown<displayOffset){if(points[at]==='\r'&&points[at+1]==='\n')at++;at++;shown++;}
    return at;
  }
  const stamp = s => s ? new Date(s).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false}) : '尚未同步';
  const key = p => `${p.id}:${p.start}:${p.end}`;
  const picksSignature = () => JSON.stringify(state.picks.map(p=>({id:p.id,hash:p.hash,start:p.start,end:p.end})));
  const sourceName = id => state.sources.find(s=>s.id===id)?.name || id;
  function captureInfo(s) {
    if(s.kind!=='wechat'||s.backend!=='collector')return '';
    const c=s.capture||{},last=c.last_success||{};
    const labels={idle:'尚未采集',running:'采集中',done:'采集完成',error:'采集异常',commit_unknown:'提交结果待核对',needs_review:'采集任务待核对',unavailable:'采集未配置'};
    const phase={starting:'准备任务',checking_access:'检查本机文件访问',preparing:'复制并校验来源',decrypting:'解密隔离快照',staging:'核对新增消息',committing:'更新采集库',captured:'更新索引'};
    const detail=c.busy?(phase[c.phase]||'采集中'):(c.detail||labels[c.status]||'尚未采集');
    const cadence=s.enabled===false?'新消息采集已暂停':s.capture_enabled?'每小时采集已开启':'手动采集';
    const clock=last.captured_at?'最近成功采集：'+stamp(last.captured_at):'最近成功采集：尚无完成回执';
    const index=last.index_status&&last.index_status!=='ready'?'；采集库已更新，索引仍待核对':'';
    return `${cadence} · ${detail}\n${clock}${index}`;
  }
  function scope(s) {
    if(s.document_kind==='external')return ['来源：人工收藏的他人文章','保留原作者、原始链接、收集渠道与收藏理由','仅链接收藏不代表已导入全文；收藏不会发布或改写原文'];
    if(s.kind==='gmail_cache')return ['来源：Gmail 连接器 → 本机邮件缓存','账号：'+s.account,'最近采集查询：'+s.query,'采集时间：'+stamp(s.captured_at),s.has_more?'本次查询仍有后续页；当前只导入部分邮件':'本次查询已取完；不代表整个邮箱已导入','更新来源只重建缓存索引；新邮件仍需重新采集，附件未读取'];
    if(s.kind==='projects')return ['目录：'+(s.paths||[]).join('\n'),'范围：Markdown / TXT / RST，跳过缓存、生成文件与敏感文件'];
    if(s.kind==='wechat')return [s.backend==='collector'?'来源：wechat-assistant 采集快照':'来源：微信雷达已有数据库快照','本机数据库：'+(s.path||'未配置'),'会话范围：'+((s.chatroom_ids||[]).length?(s.chatroom_ids||[]).join('、'):'采集库中的全部会话'),'日期范围：'+(s.date_from||'最早')+' 至 '+(s.date_to||'最新'),'新消息依赖上游采集同步；采集正文最多 2,000 字，媒体未转录'];
    if(s.kind==='wechat_links')return ['来源：微信雷达已有分享链接快照','本机数据库：'+(s.path||'未配置'),'范围：仅匹配本机微信已索引消息的会话、编号和时间，跟随其范围；搜索补充与手动资源不作为分享记录导入','外部网页正文未读取；分享不代表本人认可或需要行动'];
    if(s.kind==='articles')return ['来源：工作台作品库','范围：所有作品的当前版本；引用固定到收集时的版本'];
    if(s.kind==='mail')return ['来源：本机 remio 的已同步邮件','范围：remio 已连接账号的邮件列表；账号与邮箱范围在 remio 管理'];
    if(s.kind==='notes')return ['来源：本机 remio','范围：手记；文件、网页、录音等其它 remio 来源尚未接入工作台'];
    return ['来源：手动收集的笔记；有外部依据时请保留原始链接'];
  }
  function provenance(d) {
    const p=d.provenance||{},rows=[['接入来源',sourceName(d.source)],[d.kind==='external'?'收藏时间':d.kind==='wechat_links'?'分享消息时间':'原文更新时间',stamp(d.modified)]];
    if(p.ownership==='third_party')rows.push(['作品归属','他人作品'],['原作者',p.author||'姓名待补'],['原作者账号',p.author_handle?'@'+p.author_handle:'账号待补'],['作者依据',p.author_basis],['原文平台',p.platform],['原文发布日期',p.published_on||'待核对'],['在哪里看到',p.discovered_via],['收录入口',p.intake_channel],['收集路径',p.discovery_detail],['我的收藏理由 / 评论',p.reader_note||'尚未填写']);
    if(p.local_path||p.localPath)rows.push(['原始文件',p.local_path||p.localPath]);
    if(p.repository)rows.push(['GitHub 仓库',p.repository],['资料类型',p.item_kind],['条目编号',p.item_key],['作者',p.author],['原文更新时间',stamp(p.updated_at)],['采集时间',stamp(p.captured_at)]);
    if(Array.isArray(p.comments))rows.push(['讨论回复',p.comments.length+' 条 · '+(p.comments_complete?'当前分页已完整':'尚有后续页')]);
    if(p.project_name)rows.push(['所属项目',p.project_name]);
    if(p.slug)rows.push(['原始作品',p.slug],['引用版本',p.version]);
    if(p.chatroom_id)rows.push(['原始会话',p.chatroom_id],['消息编号',String(p.local_id)],['快照数据库',p.database_path]);
    if(p.raw_local_id)rows.push(['上游消息编号',p.raw_local_id]);
    if(p.content_scope)rows.push(['正文覆盖',typeof p.content_scope==='object'?(p.content_scope.detail||JSON.stringify(p.content_scope)):p.content_scope]);
    if(p.message_id)rows.push(['邮箱账号',p.account],['Gmail 消息编号',p.message_id],['原始邮件编号',p.rfc_message_id],['邮件线程',p.thread_id],['发件人',p.from],['收件人',p.to],['原始发送时间',p.date_sent||p.date_header],['采集时间',stamp(p.captured_at)]);
    if(p.noteId)rows.push(['remio 条目',p.noteId],['原始类型',[p.noteType,p.noteSource].filter(Boolean).join(' / ')]);
    if(p.sourceUrl)rows.push([d.kind==='wechat_links'?'分享链接（规范化）':'原始链接',p.sourceUrl]);
    else if(d.url)rows.push(['原资料入口',d.url]);
    if(p.collections?.length)rows.push(['原集合',p.collections.join('、')]);
    if(!Object.keys(p).length&&!d.url)rows.push(['定位状态','历史索引尚未补齐原文定位，可同步来源后核对']);
    const ref=p.original_message_reference;
    if(p.source_record_hash)rows.push(['分享记录指纹',p.source_record_hash]);
    const original=ref&&typeof ref.id==='string'&&typeof ref.hash==='string'&&/^[a-f0-9]{32}$/.test(ref.id)&&/^[a-f0-9]{64}$/.test(ref.hash)&&Number.isInteger(ref.start)&&Number.isInteger(ref.end)&&ref.start>=0&&ref.end>ref.start?'/knowledge.html?'+new URLSearchParams({document:ref.id,hash:ref.hash,start:String(ref.start),end:String(ref.end)}):'';
    return rows.filter(([,v])=>v!==undefined&&v!==null&&v!=='').map(([k,v])=>`<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join('')+(original?`<dt>对应消息索引</dt><dd><a href="${esc(original)}">核对微信消息与版本 ↗</a></dd>`:'');
  }
  async function api(path, data) {
    const r = await fetch(path, data === undefined ? {} : {method:'POST',headers:{'Content-Type':'application/json','X-Article-Studio':'1'},body:JSON.stringify(data)});
    const value = await r.json();
    if (!r.ok) throw new Error(value.error || '操作失败，请重试');
    return value;
  }
  function message(text) {$('#kb-status').textContent=text;}
  function sourceConfig(sources,syncing) {
    if(state.sourceEditing)return;
    $('#kb-source-config').innerHTML=sources.map(s=>{
      const capture=s.kind==='wechat'&&s.backend==='collector',busy=syncing||s.capture?.busy;
      return `<section class="kb-source-config-card"><strong>${esc(s.name)}</strong><span>${statuses[s.status]||esc(s.status)} · 索引 ${s.count.toLocaleString()} 份</span><pre>${esc(scope(s).join('\n'))}</pre><p>${esc(s.detail||'尚未同步')}<br>最近更新索引：${esc(stamp(s.synced_at))}</p>${capture?`<pre>${esc(captureInfo(s))}</pre>`:''}${s.kind==='manual'?'':`<form data-source-options="${esc(s.id)}"><label><input type="checkbox" name="enabled" ${s.enabled===false?'':'checked'}> 自动更新本机索引</label>${capture?`<label><input type="checkbox" name="capture_enabled" ${s.capture_enabled?'checked':''} ${s.capture?.available?'':'disabled'}> 每小时采集本机微信新消息</label><small>工作台运行时执行；采集异常会保留已有资料。</small>`:''}${s.kind==='wechat'?`<div class="kb-source-dates"><label>开始日期<input type="date" name="date_from" value="${esc(s.date_from||'')}"></label><label>结束日期<input type="date" name="date_to" value="${esc(s.date_to||'')}"></label></div><label>限定会话编号（每行一个，留空为全部）<textarea name="chatroom_ids" rows="3">${esc((s.chatroom_ids||[]).join('\n'))}</textarea></label><small>保存范围后点击更新；旧事件与资料夹引用仍保留原快照。</small>`:''}<button type="submit" class="button" ${busy?'disabled':''}>保存同步范围</button><p class="kb-form-status" role="status"></p></form>${capture?`<button class="button primary" data-capture-source="${esc(s.id)}" ${busy||s.enabled===false||!s.capture?.available?'disabled':''}>${s.capture?.busy?'正在采集…':'采集新消息并更新索引'}</button> `:''}<button class="button" data-sync-source="${esc(s.id)}" ${busy||s.enabled===false?'disabled':''}>${capture||s.kind==='gmail_cache'?'更新缓存索引':'更新这个来源'}</button>`}</section>`;
    }).join('');
    $('#kb-source-config').querySelectorAll('form').forEach(f=>{
      f.oninput=()=>{state.sourceEditing=true;};
      f.onsubmit=async e=>{e.preventDefault();const button=f.querySelector('[type=submit]');button.disabled=true;const data={id:f.dataset.sourceOptions,enabled:f.elements.enabled.checked};if(f.elements.date_from){data.date_from=f.elements.date_from.value;data.date_to=f.elements.date_to.value;data.chatroom_ids=f.elements.chatroom_ids.value.split('\n').map(s=>s.trim()).filter(Boolean);}
        if(f.elements.capture_enabled)data.capture_enabled=f.elements.capture_enabled.checked;
        try{await api('/api/knowledge/source',data);state.sourceEditing=false;await status();message('来源范围已保存；同步后索引按新范围更新。');}catch(e){f.querySelector('.kb-form-status').textContent=e.message;}finally{button.disabled=false;}
      };
    });
    $('#kb-source-config').querySelectorAll('[data-sync-source]').forEach(b=>b.onclick=()=>operation(b,async()=>{await api('/api/knowledge/sync',{source:b.dataset.syncSource});await status();message('正在更新所选来源。');}));
    $('#kb-source-config').querySelectorAll('[data-capture-source]').forEach(b=>b.onclick=()=>operation(b,async()=>{await api('/api/knowledge/wechat/capture',{});await status();message('正在采集本机新消息，完成后更新索引。');}));
  }
  async function status() {
    const d=await api('/api/knowledge');
    state.sources=d.sources;
    state.syncing=d.syncing;
    $('#kb-total').textContent=`${d.total.toLocaleString()} 份资料 · 本机索引`;
    $('#kb-sync').disabled=d.syncing;
    $('#kb-sync').textContent=d.syncing?'正在同步…':'同步资料';
    $('#kb-source-nav').innerHTML=`<button data-source="" data-kind=""><span>▥ 所有资料</span><small>${d.total.toLocaleString()}</small></button>`+d.sources.map(s=>`<button data-source="${esc(s.id)}" data-kind="${esc(s.document_kind||(s.kind==='manual'?'notes':s.kind==='gmail_cache'?'mail':s.kind))}"><span>${esc(s.name)}</span><small>${s.count.toLocaleString()}</small></button>`).join('');
    $('#kb-source-status').innerHTML=d.sources.map(s=>`<div class="kb-source-card" data-status="${esc(s.status)}"><strong>${esc(s.name)}</strong><span>${statuses[s.status]||esc(s.status)} · ${s.count.toLocaleString()} 份</span><p>${esc(s.detail||'点击同步资料，建立可检索索引')}</p>${s.capture?`<pre>${esc(captureInfo(s))}</pre>`:''}<p>更新索引：${esc(stamp(s.synced_at))}</p></div>`).join('');
    sourceConfig(d.sources,d.syncing);
    $('#kb-dossiers').innerHTML=d.dossiers.length?d.dossiers.map(x=>`<button data-dossier="${esc(x.id)}"><span>▦ ${esc(x.title)}</span></button>`).join(''):'<p class="empty">围绕一个主题挑选资料，<br>保存后可在这里继续创作。</p>';
    $('#kb-source-nav').querySelectorAll('button').forEach(b=>b.onclick=()=>{state.source=b.dataset.source;state.kind=b.dataset.kind;state.tag='';$('#kb-kind').value=state.kind;search().catch(e=>message(e.message));});
    $('#kb-dossiers').querySelectorAll('button').forEach(b=>b.onclick=()=>openDossier(b.dataset.dossier).catch(e=>message(e.message)));
    return d;
  }
  async function search(more=false) {
    const request=++state.request;
    const params=new URLSearchParams({q:$('#kb-query').value.trim(),kind:state.kind,source:state.source,tag:state.tag,offset:more?state.next||0:0});
    const result=await api('/api/knowledge/search?'+params);
    if(request!==state.request)return;
    state.query=params.get('q');state.items=more?[...state.items,...result.results]:result.results;state.next=result.next_offset;
    $('#kb-result-count').textContent=`${result.total.toLocaleString()} 份相关资料${state.tag?' · '+state.tag:''}`;
    $('#kb-more').hidden=state.next===null;
    renderResults();message('');
  }
  function renderResults() {
    $('#kb-results').innerHTML=state.items.length?state.items.map(d=>{
      const picked=state.picks.some(p=>p.id===d.id);
      const p=d.provenance||{},external=p.ownership==='third_party';
      const credit=external?`<p class="kb-reference-credit">原作者：${esc(p.author||'姓名待补')}${p.author_handle?' · @'+esc(p.author_handle):''} · ${esc(p.platform||'平台待补')}</p><p class="kb-origin">收集：${esc(p.discovered_via||'渠道待补')} · ${esc(p.content_scope||'覆盖范围待核对')}</p>${p.reader_note?`<p class="kb-reader-note"><b>我的评论：</b>${esc(p.reader_note)}</p>`:''}`:'';
      return `<article class="kb-result" data-document="${esc(d.id)}"><div class="kb-result-meta"><b>${esc(names[d.kind]||d.kind)}</b><span>${external?'收藏于 ':''}${esc(stamp(d.modified))}</span></div><h3>${esc(d.title)}</h3><p class="kb-origin">来源：${esc(sourceName(d.source))}${d.provenance?.version?' · '+esc(d.provenance.version):''}</p>${credit}<pre>${esc(d.excerpt||(external?'仅收藏原帖链接，未保存原文正文。':'这份作品没有文字正文，可打开原资料查看。'))}</pre><div class="kb-result-actions"><button data-preview="${esc(d.id)}">查看原文与出处</button>${d.tags.slice(0,2).map(t=>`<button data-tag="${esc(t)}">#${esc(t)}</button>`).join('')}<button data-pick="${esc(d.id)}" class="${picked?'picked':''}" ${d.excerpt?'':'disabled'}>${picked?'✓ 已收集':'＋ 收集这个片段'}</button></div></article>`;
    }).join(''):'<div class="kb-empty">暂无匹配资料。<br>试试原文关键词、切换来源，或同步最新资料。</div>';
    $('#kb-results').querySelectorAll('[data-preview]').forEach(b=>b.onclick=()=>preview(b.dataset.preview).catch(e=>message(e.message)));
    $('#kb-results').querySelectorAll('[data-pick]').forEach(b=>b.onclick=()=>{
      const d=state.items.find(x=>x.id===b.dataset.pick);
      if(state.picks.some(p=>p.id===d.id)){state.picks=state.picks.filter(p=>p.id!==d.id);renderPicks();return;}
      addPick({...d,start:d.excerpt_start,end:d.excerpt_start+chars(d.excerpt)});
    });
    $('#kb-results').querySelectorAll('[data-tag]').forEach(b=>b.onclick=()=>{state.tag=state.tag===b.dataset.tag?'':b.dataset.tag;search().catch(e=>message(e.message));});
  }
  function addPick(p) {
    if(state.picks.some(x=>key(x)===key(p)))return;
    if(state.picks.length>=20 || chars(p.excerpt)+state.picks.reduce((n,x)=>n+chars(x.excerpt),0)>12000){message('最多选择 20 个片段、合计 12,000 字。请在原文里缩小选取范围。');return;}
    state.picks.push(p);renderPicks();message('已收集片段，原文与出处会一起保留。');
  }
  function renderPicks() {
    $('#kb-picks').innerHTML=state.picks.length?state.picks.map((p,i)=>`<div class="kb-pick"><strong>[${i+1}] ${esc(p.title)}</strong><small>来源：${esc(sourceName(p.source))}${p.provenance?.version?' · '+esc(p.provenance.version):''} · 原文片段 ${p.start+1}–${p.end}</small><p>${esc(p.excerpt)}</p><button data-remove="${i}">移除 · ${chars(p.excerpt).toLocaleString()} 字</button></div>`).join(''):'<p>在左侧检索，再收集需要的片段。</p>';
    $('#kb-budget').textContent=`${state.picks.length} 个片段 · ${state.picks.reduce((n,x)=>n+chars(x.excerpt),0).toLocaleString()} / 12,000 字`;
    $('#kb-picks').querySelectorAll('button').forEach(b=>b.onclick=()=>{state.picks.splice(Number(b.dataset.remove),1);renderPicks();});
    renderResults();
  }
  async function preview(id, citation=null) {
    const d=await api('/api/knowledge/document/'+encodeURIComponent(id));state.document=d;
    $('#kb-document-title').textContent=d.title;
    $('#kb-document-meta').textContent=`${names[d.kind]||d.kind} · ${stamp(d.modified)}`;
    $('#kb-document-provenance').innerHTML=provenance(d);
    $('#kb-document-body').value=d.body;
    $('#kb-document-body').hidden=d.kind==='external'&&!d.body;
    $('#kb-pick-selection').disabled=!d.body;
    $('#kb-clue-link').href='/clues.html?document='+encodeURIComponent(d.id);
    $('#kb-clue-link').hidden=d.kind==='external'&&!d.body;
    $('#kb-document-link').hidden=!d.url;$('#kb-document-link').href=d.url||'#';
    $('#kb-document-dialog .kb-form-status').textContent='在原文中选中需要的片段，再收集。';
    if(d.kind==='external'&&!d.body)$('#kb-document-dialog .kb-form-status').textContent='这是链接收藏，原文正文未保存。可打开原帖；您的评论显示在上方，不作为原文引用。';
    $('#kb-document-dialog').showModal();
    if(citation){
      const body=Array.from(d.body),start=Number(citation.start),end=Number(citation.end);
      if(citation.hash&&citation.hash!==d.hash){
        $('#kb-document-dialog .kb-form-status').textContent='原资料已更新，旧引用片段请在事件证据中核对。';
      }else if(Number.isInteger(start)&&Number.isInteger(end)&&start>=0&&end>start&&end<=body.length){
        const text=$('#kb-document-body');text.focus();text.setSelectionRange(normalized(body.slice(0,start).join('')).length,normalized(body.slice(0,end).join('')).length);
        $('#kb-document-dialog .kb-form-status').textContent=`已定位引用片段 ${start+1}–${end}；出处见上方。`;
      }else{
        $('#kb-document-dialog .kb-form-status').textContent='已打开原资料；引用范围无法定位，请核对原文。';
      }
    }
  }
  async function openDossier(id) {
    const d=await api('/api/knowledge/dossier/'+encodeURIComponent(id));state.dossier=d.id;state.picks=d.items;
    state.savedPicks=picksSignature();
    $('#kb-dossier-title').value=d.title;$('#kb-purpose').value=d.purpose||'';renderPicks();message('已打开资料夹，引用保留收集时的原文版本。');
  }
  async function saveDossier() {
    const payload={id:state.dossier,title:$('#kb-dossier-title').value,purpose:$('#kb-purpose').value};
    if(!state.dossier||state.savedPicks!==picksSignature())payload.items=state.picks.map(p=>({id:p.id,start:p.start,end:p.end,hash:p.hash}));
    const d=await api('/api/knowledge/dossier',payload);
    state.dossier=d.id;state.picks=d.items;state.savedPicks=picksSignature();renderPicks();await status();return d;
  }
  async function operation(button, action) {
    if(state.busy)return;state.busy=true;button.disabled=true;
    try{await action();}catch(e){message(e.message);}finally{state.busy=false;button.disabled=false;}
  }
  $('#kb-search-form').onsubmit=e=>{e.preventDefault();state.tag='';search().catch(e=>message(e.message));};
  $('#kb-kind').onchange=e=>{state.kind=e.target.value;state.source='';state.tag='';search().catch(e=>message(e.message));};
  $('#kb-more').onclick=()=>search(true).catch(e=>message(e.message));
  $('#kb-sync').onclick=()=>operation($('#kb-sync'),async()=>{await api('/api/knowledge/sync',{});await status();message('正在同步本机已有资料，完成后刷新检索结果。');});
  $('#kb-save-dossier').onclick=()=>operation($('#kb-save-dossier'),async()=>{await saveDossier();message('资料夹已保存，可以随时打开继续使用。');});
  $('#kb-create-draft').onclick=()=>operation($('#kb-create-draft'),async()=>{const d=await saveDossier();const result=await api('/api/knowledge/draft',{id:d.id,purpose:$('#kb-purpose').value});location.href=result.url;});
  $('#kb-new-dossier').onclick=()=>{state.dossier=null;state.picks=[];$('#kb-dossier-title').value='';$('#kb-purpose').value='';renderPicks();message('新资料夹，之前保存的资料夹保留。');};
  $('#kb-pick-selection').onclick=()=>{
    const t=$('#kb-document-body'),d=state.document;
    if(t.selectionStart===t.selectionEnd){$('#kb-document-dialog .kb-form-status').textContent='请先选中需要收集的文字。';return;}
    const start=rawOffset(d.body,chars(t.value.slice(0,t.selectionStart))),end=rawOffset(d.body,chars(t.value.slice(0,t.selectionEnd)));
    const excerpt=Array.from(d.body).slice(start,end).join('');
    addPick({...d,excerpt,start,end});
    $('#kb-document-dialog .kb-form-status').textContent=$('#kb-status').textContent;
  };
  $('#kb-add-note').onclick=()=>$('#kb-note-dialog').showModal();
  $('#kb-add-reference').onclick=()=>$('#kb-reference-dialog').showModal();
  $('#kb-reference-form').onsubmit=async e=>{
    e.preventDefault();const f=e.currentTarget,b=f.querySelector('button');if(b.disabled)return;b.disabled=true;
    const statusText=f.querySelector('.kb-form-status');statusText.textContent='正在收藏…';
    try{const d=await api('/api/knowledge/reference',Object.fromEntries(new FormData(f)));await status();state.source='external-references';state.kind='external';state.tag='';$('#kb-kind').value='external';$('#kb-query').value='';await search();f.closest('dialog').close();f.reset();await preview(d.id);message(d.duplicate?'该原帖已经收藏，保留已有评论与引用。':'已收藏他人作品，原作者与收集路径已保留。');}
    catch(err){statusText.textContent=err.message;}finally{b.disabled=false;}
  };
  $('#kb-add-source').onclick=()=>$('#kb-source-dialog').showModal();
  $('#kb-manage-sources').onclick=()=>$('#kb-source-dialog').showModal();
  document.querySelectorAll('[data-close]').forEach(b=>b.onclick=()=>{if(b.closest('dialog').id==='kb-source-dialog'){state.sourceEditing=false;sourceConfig(state.sources,state.syncing);}b.closest('dialog').close();});
  for(const [selector,endpoint] of [['#kb-note-form','note'],['#kb-source-form','project']]){
    $(selector).onsubmit=async e=>{
      e.preventDefault();const f=e.currentTarget,button=f.querySelector('button');button.disabled=true;f.querySelector('.kb-form-status').textContent='正在保存…';
      try{await api('/api/knowledge/'+endpoint,Object.fromEntries(new FormData(f)));f.reset();f.closest('dialog').close();await status();await search();message(endpoint==='note'?'已收集到知识库。':'项目来源已添加，正在建立索引。');}
      catch(err){f.querySelector('.kb-form-status').textContent=err.message;}finally{button.disabled=false;}
    };
  }
  let polling=false;
  setInterval(async()=>{if(polling)return;polling=true;try{const before=state.syncing||state.sources.some(s=>s.capture?.busy);await status();const after=state.syncing||state.sources.some(s=>s.capture?.busy);if(before&&!after){await search();message('更新结束，检索结果已刷新；采集与索引状态见上方。');}}catch(e){message(e.message);}finally{polling=false;}},2500);
  renderPicks();
  status().then(async d=>{await search();const p=new URLSearchParams(location.search),id=p.get('document');const selected=d.sources.find(s=>s.id===p.get('source'));if(selected){state.source=selected.id;state.kind=selected.document_kind||(selected.kind==='manual'?'notes':selected.kind==='gmail_cache'?'mail':selected.kind);$('#kb-kind').value=state.kind;await search();}if(id){if(!/^[a-f0-9]{32}$/.test(id))throw new Error('引用资料编号无效');const citation=p.has('start')||p.has('end')||p.has('hash')?{start:p.get('start'),end:p.get('end'),hash:p.get('hash')}:null;await preview(id,citation);}if(d.sources.some(s=>s.status==='pending'))await api('/api/knowledge/sync',{});else await api('/api/knowledge/sync',{source:'articles'});}).catch(e=>message(e.message));
})();
