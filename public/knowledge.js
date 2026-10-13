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
  // 删除/恢复回执单独记一份：2.5 秒轮询会走 status()→search()，search() 末尾要清状态行，
  // 不能把刚给出的回执抹掉；回执留到用户下一次主动操作（任何 message(...) 都会清）。
  let receipt='';
  function message(text) {receipt='';$('#kb-status').textContent=text||'';}
  function showReceipt(lines){receipt=Array.isArray(lines)?lines.join('\n'):String(lines||'');$('#kb-status').textContent=receipt;}
  function keepReceipt(){if(receipt)$('#kb-status').textContent=receipt;else message('');}
  function clearReceipt(){receipt='';}
  // 后台提示分两种，都不许盖掉回执：
  // notice —— 成功类（同步/采集结束）。回执优先：直接让位给回执，来源状态区本来就有更新时间，
  //           这句只是重复信息，丢掉不丢事实；receipt 本身一个字都不动。
  // problem —— 失败类（后台出错）。回执仍在第一行，错误附在下面，既不覆盖回执也不把错误吞掉；
  //           receipt 变量不动，所以之后的检索重绘仍会还原成纯回执。
  function notice(text){if(receipt)return;message(text);}
  function problem(text){if(receipt){$('#kb-status').textContent=receipt+'\n'+String(text||'');return;}message(text);}
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
    $('#kb-source-nav').querySelectorAll('button').forEach(b=>b.onclick=()=>{clearReceipt();state.source=b.dataset.source;state.kind=b.dataset.kind;state.tag='';$('#kb-kind').value=state.kind;search().catch(e=>message(e.message));});
    $('#kb-dossiers').querySelectorAll('button').forEach(b=>b.onclick=()=>openDossier(b.dataset.dossier).catch(e=>message(e.message)));
    return d;
  }
  async function search(more=false) {
    const request=++state.request;
    const params=new URLSearchParams({q:$('#kb-query').value.trim(),kind:state.kind,source:state.source,tag:state.tag,offset:more?state.next||0:0});
    const result=await api('/api/knowledge/search?'+params);
    if(request!==state.request)return;
    if(!more)clearTrashPicks();   // 换来源/重新检索会把看不见的条目翻页掉，选择跟着清掉，避免隐式选中误删
    state.query=params.get('q');state.items=more?[...state.items,...result.results]:result.results;state.next=result.next_offset;
    $('#kb-result-count').textContent=`${result.total.toLocaleString()} 份相关资料${state.tag?' · '+state.tag:''}`;
    $('#kb-more').hidden=state.next===null;
    renderResults();keepReceipt();
  }
  function renderResults() {
    $('#kb-results').innerHTML=state.items.length?state.items.map(d=>{
      const picked=state.picks.some(p=>p.id===d.id);
      const marked=trash.picks.some(p=>p.id===d.id);
      const p=d.provenance||{},external=p.ownership==='third_party';
      const credit=external?`<p class="kb-reference-credit">原作者：${esc(p.author||'姓名待补')}${p.author_handle?' · @'+esc(p.author_handle):''} · ${esc(p.platform||'平台待补')}</p><p class="kb-origin">收集：${esc(p.discovered_via||'渠道待补')} · ${esc(p.content_scope||'覆盖范围待核对')}</p>${p.reader_note?`<p class="kb-reader-note"><b>我的评论：</b>${esc(p.reader_note)}</p>`:''}`:'';
      return `<article class="kb-result" data-document="${esc(d.id)}"><div class="kb-result-meta"><b>${esc(names[d.kind]||d.kind)}</b><span>${external?'收藏于 ':''}${esc(stamp(d.modified))}</span></div><h3>${esc(d.title)}</h3><p class="kb-origin">来源：${esc(sourceName(d.source))}${d.provenance?.version?' · '+esc(d.provenance.version):''}</p>${credit}<pre>${esc(d.excerpt||(external?'仅收藏原帖链接，未保存原文正文。':'这份作品没有文字正文，可打开原资料查看。'))}</pre><div class="kb-result-actions"><label class="kb-trash-check"><input type="checkbox" data-trash-pick="${esc(d.id)}" ${marked?'checked':''} aria-label="选择这篇，准备移到已删除"><span>选择</span></label><button data-preview="${esc(d.id)}">查看原文与出处</button>${d.tags.slice(0,2).map(t=>`<button data-tag="${esc(t)}">#${esc(t)}</button>`).join('')}<button data-trash-move="${esc(d.id)}">移到已删除</button><button data-pick="${esc(d.id)}" class="${picked?'picked':''}" ${d.excerpt?'':'disabled'}>${picked?'✓ 已收集':'＋ 收集这个片段'}</button></div></article>`;
    }).join(''):'<div class="kb-empty">暂无匹配资料。<br>试试原文关键词、切换来源，或同步最新资料。</div>';
    $('#kb-results').querySelectorAll('[data-preview]').forEach(b=>b.onclick=()=>preview(b.dataset.preview).catch(e=>message(e.message)));
    $('#kb-results').querySelectorAll('[data-pick]').forEach(b=>b.onclick=()=>{
      const d=state.items.find(x=>x.id===b.dataset.pick);
      if(state.picks.some(p=>p.id===d.id)){state.picks=state.picks.filter(p=>p.id!==d.id);renderPicks();return;}
      addPick({...d,start:d.excerpt_start,end:d.excerpt_start+chars(d.excerpt)});
    });
    $('#kb-results').querySelectorAll('[data-tag]').forEach(b=>b.onclick=()=>{state.tag=state.tag===b.dataset.tag?'':b.dataset.tag;search().catch(e=>message(e.message));});
    $('#kb-results').querySelectorAll('[data-trash-pick]').forEach(b=>b.onchange=()=>toggleTrashPick(b.dataset.trashPick,b.checked));
    $('#kb-results').querySelectorAll('[data-trash-move]').forEach(b=>b.onclick=()=>askTrash([b.dataset.trashMove]).catch(e=>message(e.message)));
  }
  function addPick(p) {
    if(state.picks.some(x=>key(x)===key(p)))return;
    if(state.picks.length>=20 || chars(p.excerpt)+state.picks.reduce((n,x)=>n+chars(x.excerpt),0)>12000){message('最多选择 20 个片段、合计 12,000 字。请在原文里缩小选取范围。');return;}
    state.picks.push(p);writeGuard.blocked=false;renderPicks();message('已收集片段，原文与出处会一起保留。');
  }
  function renderPicks() {
    $('#kb-picks').innerHTML=state.picks.length?state.picks.map((p,i)=>`<div class="kb-pick"><strong>[${i+1}] ${esc(p.title)}</strong><small>来源：${esc(sourceName(p.source))}${p.provenance?.version?' · '+esc(p.provenance.version):''} · 原文片段 ${p.start+1}–${p.end}</small><p>${esc(p.excerpt)}</p><button data-remove="${i}">移除 · ${chars(p.excerpt).toLocaleString()} 字</button></div>`).join(''):'<p>在左侧检索，再收集需要的片段。</p>';
    $('#kb-budget').textContent=`${state.picks.length} 个片段 · ${state.picks.reduce((n,x)=>n+chars(x.excerpt),0).toLocaleString()} / 12,000 字`;
    $('#kb-picks').querySelectorAll('button').forEach(b=>b.onclick=()=>{state.picks.splice(Number(b.dataset.remove),1);renderPicks();});
    renderResults();
  }
  function clearDocumentView(){
    // 加载失败或换一篇时不能把上一篇的标题、出处、正文留在页面上冒充当前结果。
    state.document=null;
    $('#kb-document-title').textContent='';$('#kb-document-meta').textContent='';
    $('#kb-document-provenance').textContent='';$('#kb-document-body').value='';$('#kb-document-body').hidden=false;
    $('#kb-document-trash-banner').hidden=true;$('#kb-document-trash').hidden=false;
    $('#kb-pick-selection').disabled=true;$('#kb-clue-link').hidden=true;$('#kb-document-link').hidden=true;
  }
  async function preview(id, citation=null) {
    clearDocumentView();
    const d=await api('/api/knowledge/document/'+encodeURIComponent(id));state.document=d;writeGuard.blocked=false;
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
    // 已移到已删除的旧入口（卡库引用、回收项链接）仍能打开原文：只告知隐藏事实，不自动恢复、不禁选片段。
    const trashed=d.trashed===true;
    $('#kb-document-trash-banner').hidden=!trashed;
    $('#kb-document-trash').hidden=trashed;
    if(trashed){
      $('#kb-document-trash-text').textContent='这篇已移出常规检索，原文仍可核对';
      $('#kb-document-restore').disabled=false;
      $('#kb-document-dialog .kb-form-status').textContent='这篇已移出常规检索；原文仍可核对、可选中片段引用，不会自动恢复。要回到检索请点上方「恢复到常规检索」并确认。';
    }
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
    state.savedPicks=picksSignature();writeGuard.blocked=false;
    $('#kb-dossier-title').value=d.title;$('#kb-purpose').value=d.purpose||'';renderPicks();message('已打开资料夹，引用保留收集时的原文版本。');
  }
  async function saveDossier() {
    const payload={id:state.dossier,title:$('#kb-dossier-title').value,purpose:$('#kb-purpose').value};
    if(!state.dossier||state.savedPicks!==picksSignature())payload.items=state.picks.map(p=>({id:p.id,start:p.start,end:p.end,hash:p.hash}));
    const d=await api('/api/knowledge/dossier',payload);
    state.dossier=d.id;state.picks=d.items;state.savedPicks=picksSignature();renderPicks();await status();return d;
  }
  // ── 从创意回收带过来的写作入口（方案 A）───────────────────────────────────
  // query 只带标识：document / intake / angle / analysis_at。正文、摘要、角度文字、收藏理由都不从
  // URL 读，也不写 localStorage；带入的标题/目标只是可编辑建议，永远不会变成引用片段。
  const HEX32=/^[a-f0-9]{32}$/;
  const writeGuard={blocked:false,drop:null};
  function queryContext(){
    const p=new URLSearchParams(location.search);
    return {p,document:p.get('document'),source:p.get('source'),intake:p.get('intake'),angle:p.get('angle'),analysisAt:p.get('analysis_at')};
  }
  const analysisVersion=a=>String((a&&(a.at||a.fingerprint))||'');
  let guideModel=null;
  function renderGuide(){
    const box=$('#kb-intake-guide');
    if(!guideModel){box.hidden=true;return;}
    box.hidden=false;
    $('#kb-intake-title').textContent=guideModel.title;
    const note=$('#kb-intake-note');note.textContent=guideModel.note;note.hidden=!guideModel.note;
    const meta=$('#kb-intake-meta');meta.textContent='';
    for(const[label,value]of guideModel.rows){
      if(value===undefined||value===null||value==='')continue;
      const dt=document.createElement('dt'),dd=document.createElement('dd');
      dt.textContent=label;dd.textContent=String(value);meta.append(dt,dd);
    }
    const actions=$('#kb-intake-actions');actions.textContent='';
    for(const link of guideModel.links){
      const a=document.createElement('a');a.className='button';a.href=link.href;a.textContent=link.label;
      if(link.external){a.target='_blank';a.rel='noopener noreferrer';}
      actions.append(a);
    }
  }
  function setGuide(patch){
    guideModel=Object.assign({title:'从创意回收带过来的写作入口',note:'',rows:[],links:[]},guideModel||{},patch||{});
    renderGuide();
  }
  async function intakeHandoff(q){
    setGuide({});
    if(!HEX32.test(q.intake||'')){setGuide({note:'回收项编号无效，没有带入写作建议；这篇原文仍可以自己核对后选片段。'});return;}
    let drop=null;
    try{drop=await api('/api/intake/'+encodeURIComponent(q.intake));}
    catch(e){setGuide({note:'这条回收项读不到（'+e.message+'），没有带入写作建议。'});return;}
    if(!drop||typeof drop!=='object'||Array.isArray(drop)||drop.id!==q.intake){setGuide({note:'这条回收项读不到或编号不一致，没有带入写作建议。'});return;}
    writeGuard.drop=drop;
    const rows=[];
    if(typeof drop.reason==='string'&&drop.reason)rows.push(['我的收藏理由',drop.reason]);
    // 归属要可核实：query 的 document 与回执的 document_id 都必须是有效编号并且严格相等。
    // 缺字段、空串、null、类型不对（数字/对象/数组）都算「没核实到归属」，不能当一致，一律不带入写作建议。
    const dropDocument=typeof drop.document_id==='string'?drop.document_id:'';
    if(!HEX32.test(q.document||'')||!HEX32.test(dropDocument)||dropDocument!==q.document){
      const note='这条回收项对应的资料不是当前打开的这篇（或没有可核实的资料编号），没有带入写作建议。';
      setGuide({rows,note});
      message(note);
      return;
    }
    let angle=null,note='';
    if(q.angle!==null&&q.angle!==''){
      if(!/^\d{1,4}$/.test(q.angle))note='角度编号无效，没有带入写作建议。';
      else{
        const index=Number(q.angle),a=drop.analysis||{},angles=Array.isArray(a.angles)?a.angles:[];
        const sent=q.analysisAt===null?'':String(q.analysisAt).slice(0,200);
        if(a.status!=='done'||index>=angles.length||sent!==analysisVersion(a))note='分析已更新，请重新选角度';
        else angle=angles[index]&&typeof angles[index]==='object'?angles[index]:null;
      }
    }
    if(angle){
      const title=String(angle.title||'').trim().slice(0,200),purpose=String(angle.how||'').trim().slice(0,2000);
      if(title&&!$('#kb-dossier-title').value.trim())$('#kb-dossier-title').value=title;
      if(purpose&&!$('#kb-purpose').value.trim())$('#kb-purpose').value=purpose;
      $('#kb-purpose-ai-note').hidden=!(title||purpose);
      rows.unshift(['AI 建议的写作角度',title||'未命名角度']);
    }else if(!note)note='这条没带角度建议；写作目标请自己填。';
    setGuide({rows,note:(note?note+' ':'')+'核对资料 → 在原文里选中要引用的片段 → 自己点「带引用建立写作底稿」，不会自动提交。'});
  }
  function markDocumentUnreadable(reason){
    // 读不到原文：禁建底稿、不伪造原文，只给回收重试与知识库入口。
    writeGuard.blocked=true;
    clearDocumentView();
    const rows=[['读不到的原因',reason||'这篇资料读不到']];
    if(guideModel&&guideModel.rows)rows.push(...guideModel.rows);
    setGuide({rows,note:'这篇资料读不到，不能凭空的引用建底稿。请回到创意回收重试，或在知识库里重新找这篇。',
      links:[{label:'回到创意回收重试',href:'/intake.html'},{label:'在知识库重新检索',href:'/knowledge.html'}]});
  }
  async function draftRequest(payload){
    // 底稿这一步单独走：超时与读不出的 JSON 都是「结果未确认」，不能当成功，也不自动重发。
    const limit=60000,unconfirmed='创建结果未确认，请先到我的作品查看；未自动重发。';
    const controller=typeof AbortController==='function'?new AbortController():null;
    let expired=false;
    const timer=setTimeout(()=>{expired=true;if(controller)controller.abort();},limit);
    try{
      let r;
      try{r=await fetch('/api/knowledge/draft',{method:'POST',headers:{'Content-Type':'application/json','X-Article-Studio':'1'},body:JSON.stringify(payload),...(controller?{signal:controller.signal}:{})});}
      catch(e){if(expired||(e&&e.name==='AbortError'))throw new Error(unconfirmed);throw e;}
      let value=null,unreadable=false;
      try{value=await r.json();}catch(e){if(expired||(e&&e.name==='AbortError'))throw new Error(unconfirmed);unreadable=true;}
      if(expired)throw new Error(unconfirmed);
      if(!r.ok)throw new Error('底稿创建失败：'+((value&&value.error)||'原因读不出')+'；资料夹已保存，不会自动重发。');
      if(unreadable||!value||typeof value!=='object'||Array.isArray(value)||typeof value.url!=='string'||!value.url)throw new Error(unconfirmed);
      return value;
    }finally{clearTimeout(timer);}
  }
  function showDraftNote(text,dossierId,dossierTitle){
    // 资料夹已落库：保留它的编号与打开入口，只说底稿这一步没确认。
    const box=$('#kb-draft-note');box.textContent='';
    const span=document.createElement('span');span.textContent=text.replace('{title}',dossierTitle||'未命名');box.append(span);
    if(dossierId){
      box.dataset.dossier=dossierId;
      const button=document.createElement('button');button.type='button';button.className='button';button.textContent='打开这个资料夹';
      button.onclick=()=>openDossier(dossierId).catch(e=>message(e.message));
      box.append(button);
    }
    box.hidden=false;
  }
  async function createDraft(){
    if(writeGuard.blocked){message('这篇资料读不到，不能建底稿。请回到创意回收重试，或在知识库里重新找这篇。');return;}
    if(!state.picks.length){message('先在原文里选中要引用的片段（至少 1 个）再建底稿；不会自动挑片段。');return;}
    let dossier;
    try{dossier=await saveDossier();}
    catch(e){message('资料夹没保存成功：'+e.message+'；未创建底稿。');return;}
    let result;
    try{result=await draftRequest({id:dossier.id,purpose:$('#kb-purpose').value});}
    catch(e){showDraftNote('资料夹已保存（{title}），底稿这一步没确认，不会自动重发。',dossier.id,dossier.title);message(e.message);return;}
    $('#kb-draft-note').hidden=true;
    location.href=result.url;
  }
  // ── 移到已删除 / 已删除列表 / 恢复（可恢复软删除）─────────────────────────
  // 接口与文案依据 coordination/prep-knowledge-delete-2-report.md §2–§3；存储层语义见 knowledge_store.py。
  // 后端接口未上线（404）时只提示「删除功能尚未启用」；超时或 JSON 读不出只说「未确认」，绝不自动重发。
  const TRASH_LIMIT=50,TRASH_PAGE=30;
  const TRASH_CONFIRM_NOTE='移出常规检索和来源计数，原文件与 remio 笔记不动。回收项、提示词卡、作品与已保存证据保留，历史引用仍可打开原文。可在“已删除”恢复；这不是彻底抹除，文本仍留在本机。';
  const TRASH_UNCONFIRMED='操作结果未确认，请刷新已删除列表核对；未自动重发。';
  const TRASH_READ_UNCONFIRMED='读取超时，未确认结果，可稍后重试；未自动重发。';
  const TRASH_NOT_ENABLED='删除功能尚未启用：这台工作台还没有这些接口，资料没有任何改动。';
  const TRASH_CONFLICT='预览后资料已更新，未移出；请重新预览。';
  const TRASH_STATUS_LABELS={ready:'可移到已删除',trashed:'已在已删除中',missing:'知识库里没有这条',source_gone:'来源已不在索引'};
  const TRASH_GROUP_LABELS={deleted:'已移出',already_trashed:'已在已删除',missing:'知识库没有',conflicts:'版本已变化',errors:'未写成功'};
  const TRASH_ERROR_CODES={INVALID_REQUEST:'请求内容有问题，这次没有任何改动。',UNTRUSTED_ORIGIN:'这个页面不是本机工作台打开的，操作已被拒绝。',DOCUMENT_NOT_FOUND:'这篇资料已不在知识库。',REQUEST_TOO_LARGE:'一次最多处理 50 条资料，请减少选择后再试。',STORE_BUSY:'知识库正在更新，请稍后刷新核对。',STORE_ERROR:'这次操作结果未确认，请刷新已删除列表核对。',VERSION_CONFLICT:TRASH_CONFLICT};
  const REF_LABELS={recycle_drops:'回收项',prompt_cards:'提示词卡',dossier_snapshots:'资料夹快照'};
  const trash={picks:[],items:[],total:null,next:null,request:0,busy:false,preview:null,restoreIds:[]};
  const setTrashStatus=text=>{$('#kb-trash-status').textContent=text||'';};
  function safeServerMessage(value){
    // 只透出后端给的可读校验文案；带路径、SQL 或原始异常的一律不显示。
    const text=typeof value==='string'?value.trim():'';
    if(!text||text.length>200||/[/\\]|sqlite|SELECT|Traceback|Error:|line \d/i.test(text))return '';
    return text;
  }
  function trashErrorText(status,payload){
    const code=payload&&typeof payload==='object'?String(payload.code||''):'';
    const detail=safeServerMessage(payload&&typeof payload==='object'?payload.error:'');
    if(status===400&&detail)return detail;   // 后端给的安全校验文案优先
    if(code&&TRASH_ERROR_CODES[code])return TRASH_ERROR_CODES[code];
    if(status===403)return TRASH_ERROR_CODES.UNTRUSTED_ORIGIN;
    if(status===413)return TRASH_ERROR_CODES.REQUEST_TOO_LARGE;
    if(status===503)return TRASH_ERROR_CODES.STORE_BUSY;
    if(status===500)return TRASH_ERROR_CODES.STORE_ERROR;
    if(status===400)return detail||TRASH_ERROR_CODES.INVALID_REQUEST;
    return detail||'这次操作没有完成，资料没有任何改动。';
  }
  async function trashRequest(path,payload){
    // 忙态与超时都覆盖到 response.json 读完：读不出就是「结果未确认」，不推断成功也不自动再 POST。
    // GET（已删除列表/分页）同样带取消信号：不带 signal 时 60 秒 abort 不生效，fetch 一直挂着，
    // 忙态永不释放，页面卡在「正在读取已删除列表…」，删除与恢复入口都被 busy 挡住。
    const controller=typeof AbortController==='function'?new AbortController():null;
    const reading=payload===undefined;   // 只读请求：超时只说明没读到，不涉及写结果
    const unconfirmed=reading?TRASH_READ_UNCONFIRMED:TRASH_UNCONFIRMED;
    let expired=false;
    const timer=setTimeout(()=>{expired=true;if(controller)controller.abort();},60000);
    try{
      const init=reading?{}:{method:'POST',headers:{'Content-Type':'application/json','X-Article-Studio':'1'},body:JSON.stringify(payload)};
      if(controller)init.signal=controller.signal;
      let r;
      try{r=await fetch(path,init);}
      catch(e){if(expired||(e&&e.name==='AbortError'))throw new Error(unconfirmed);throw e;}
      let value=null,unreadable=false;
      try{value=await r.json();}catch(e){if(expired||(e&&e.name==='AbortError'))throw new Error(unconfirmed);unreadable=true;}
      if(expired)throw new Error(unconfirmed);
      if(!r.ok){
        const code=value&&typeof value==='object'?String(value.code||''):'';
        if(r.status===404&&code!=='DOCUMENT_NOT_FOUND')throw new Error(TRASH_NOT_ENABLED);
        if(unreadable)throw new Error(r.status>=500?TRASH_ERROR_CODES.STORE_ERROR:TRASH_NOT_ENABLED);
        throw new Error(trashErrorText(r.status,value));
      }
      if(unreadable||!value||typeof value!=='object'||Array.isArray(value))throw new Error(TRASH_UNCONFIRMED);
      return value;
    }finally{clearTimeout(timer);}
  }
  function renderTrashBar(){
    $('#kb-delete-bar').hidden=!trash.picks.length;
    $('#kb-delete-count').textContent=`已选 ${trash.picks.length} 条`;
    $('#kb-delete-selected').disabled=trash.busy||!trash.picks.length;
    $('#kb-delete-clear').disabled=trash.busy;
    $('#kb-trash-count').textContent=Number.isInteger(trash.total)?` ${trash.total}`:'';
  }
  function clearTrashPicks(){trash.picks=[];renderTrashBar();}
  function toggleTrashPick(id,on){
    const at=trash.picks.findIndex(p=>p.id===id);
    if(on){
      if(at>=0)return;
      if(trash.picks.length>=TRASH_LIMIT){
        message(`一次最多选择 ${TRASH_LIMIT} 条资料。请先处理已选的 ${trash.picks.length} 条。`);
        renderResults();   // 超限的这条要回到未勾选，不能看着像已选
        return;
      }
      const d=state.items.find(x=>x.id===id);
      trash.picks.push({id,title:d?d.title:'',source:d?d.source:''});
    }else if(at>=0)trash.picks.splice(at,1);
    renderTrashBar();
  }
  function referenceText(references){
    // 数得到的报数字，数不到的如实说「未统计」；不合成「只有 N 处引用」。
    const counted=references&&typeof references.counted==='object'&&references.counted!==null?references.counted:{};
    const parts=[];
    for(const[field,label]of Object.entries(REF_LABELS)){
      if(!Object.hasOwn(counted,field))continue;
      const value=counted[field];
      parts.push(`${label}：${Number.isInteger(value)?value.toLocaleString():'未统计'}`);
    }
    for(const field of Object.keys(counted))if(!REF_LABELS[field])parts.push(`${field}：${Number.isInteger(counted[field])?counted[field].toLocaleString():'未统计'}`);
    const unknown=references&&Array.isArray(references.unknown)?references.unknown:[];
    for(const label of unknown)if(!parts.some(part=>part.startsWith(`${label}：`)))parts.push(`${label}：未统计`);
    return parts.length?parts.join(' · '):'引用情况：未统计';
  }
  function trashPreviewItems(){return trash.preview&&Array.isArray(trash.preview.items)?trash.preview.items:[];}
  function renderTrashPreview(items){
    const box=$('#kb-trash-confirm-items');box.textContent='';
    for(const item of items){
      const row=document.createElement('div');row.className='kb-trash-item';
      const title=document.createElement('strong');title.textContent=item.title||'标题读不出';
      const meta=document.createElement('small');
      meta.textContent=`来源：${sourceName(item.source)||'来源未记'} · ${item.status_label||TRASH_STATUS_LABELS[item.status]||'状态未记'} · 当前版本：${typeof item.hash==='string'&&item.hash?item.hash.slice(0,12)+'…':'读不出'}`;
      const refs=document.createElement('p');refs.textContent=`引用：${referenceText(item.references)}`;
      row.append(title,meta,refs);box.append(row);
    }
  }
  async function askTrash(ids){
    if(trash.busy)return;
    const wanted=[...new Set((Array.isArray(ids)?ids:[ids]).filter(id=>HEX32.test(String(id??''))))];
    if(!wanted.length){message('这条资料编号无效，没有提交删除。');return;}
    if(wanted.length>TRASH_LIMIT){message(`一次最多处理 ${TRASH_LIMIT} 条资料，请减少选择后再试。`);return;}
    trash.busy=true;renderTrashBar();
    let preview;
    try{preview=await trashRequest('/api/knowledge/trash/preview',{ids:wanted});}
    catch(e){trash.busy=false;renderTrashBar();message(e.message);return;}
    trash.busy=false;renderTrashBar();
    const items=Array.isArray(preview.items)?preview.items:[];
    if(!items.length){message('预览没有返回条目，这次没有任何改动。');return;}
    trash.preview={items,requested:wanted};
    renderTrashPreview(items);
    $('#kb-trash-confirm-title').textContent=`把这 ${items.length} 条资料移到已删除？`;
    $('#kb-trash-confirm-note').textContent=TRASH_CONFIRM_NOTE;
    $('#kb-trash-confirm-status').textContent='';
    $('#kb-trash-confirm-ok').disabled=false;
    $('#kb-trash-confirm-dialog').showModal();
  }
  function trashGroup(result,key){
    // 存储层回执始终带五个完整数组；缺数组就按「未统计」显示，不用 summary 数字兜底
    // （数字兜底给不出可迭代结构，后面的 conflicts/errors 遍历会坏，也不假装知道明细）。
    return Array.isArray(result&&result[key])?result[key]:null;
  }
  async function commitTrash(){
    if(trash.busy)return;
    const items=[],unhashed=[];
    for(const item of trashPreviewItems()){
      if(!item||typeof item.id!=='string'||!HEX32.test(item.id))continue;
      if(item.status&&item.status!=='ready')continue;
      if(typeof item.hash!=='string'||!/^[a-f0-9]{64}$/.test(item.hash)){unhashed.push(item.id);continue;}
      items.push({id:item.id,expected_hash:item.hash});
    }
    if(!items.length){
      $('#kb-trash-confirm-status').textContent=unhashed.length?`有 ${unhashed.length} 条缺少可核对的版本信息，未提交；请重新预览。`:'这些条目已在已删除中或不在知识库，这次没有可移出的条目。';
      return;
    }
    trash.busy=true;$('#kb-trash-confirm-ok').disabled=true;renderTrashBar();
    let result,error=null;
    try{result=await trashRequest('/api/knowledge/trash',{items,confirm:true});}
    catch(e){error=e;}
    trash.busy=false;$('#kb-trash-confirm-ok').disabled=false;renderTrashBar();
    if(error){$('#kb-trash-confirm-status').textContent=error.message;message(error.message);return;}
    const groups=['deleted','already_trashed','missing','conflicts','errors'];
    const counts=Object.fromEntries(groups.map(key=>[key,trashGroup(result,key)]));
    const number=key=>counts[key]===null?'未统计':counts[key].length;
    const rows=result&&typeof result==='object'?result:{};
    const lines=[`已删除结果：${groups.map(key=>`${TRASH_GROUP_LABELS[key]} ${number(key)} 条`).join(' · ')}`];
    if(counts.conflicts&&counts.conflicts.length)lines.push(`${counts.conflicts.length} 条：${TRASH_CONFLICT}不会自动重发。`);
    if(counts.errors&&counts.errors.length)lines.push(`${counts.errors.length} 条没写成功，选择已保留，可重新预览后再试。`);
    if(counts.missing&&counts.missing.length)lines.push(`${counts.missing.length} 条知识库里已没有，可能已被其他入口删掉。`);
    if(unhashed.length)lines.push(`${unhashed.length} 条缺少可核对的版本信息，未提交。`);
    if(counts.deleted&&counts.deleted.length)lines.push('可在「已删除」里查看或恢复；这不是彻底抹除。');
    const keep=new Set();
    for(const key of ['conflicts','errors'])for(const item of (counts[key]||[]))if(item&&typeof item.id==='string')keep.add(item.id);
    const kept=trash.picks.filter(p=>keep.has(p.id));
    trash.preview=null;
    $('#kb-trash-confirm-dialog').close();
    try{await status();await search();}catch(e){/* 刷新失败不抹掉已确认的删除结果 */}
    trash.picks=kept;   // 重新检索会清选择，这里把冲突/未写成功的条目放回去
    renderTrashBar();renderResults();
    showReceipt(lines);
    if($('#kb-trash-dialog').open)await reloadTrash().catch(()=>{});
  }
  function trashRowText(row){
    const parts=[`删除时间：${row.deleted_at?stamp(row.deleted_at):'删除时间未记录'}`,`来源：${sourceName(row.source)||'来源未记'}`];
    parts.push(row.body_available===false?'正文已不在索引':'正文仍在');
    if(row.content_changed===true)parts.push('正文自删除后已变化');
    return parts.join(' · ');
  }
  function renderTrashList(){
    const box=$('#kb-trash-list');box.textContent='';
    if(!trash.items.length){
      const empty=document.createElement('p');empty.className='empty';empty.textContent='已删除里还没有资料。在结果列表里勾选，再点「移到已删除」。';box.append(empty);
      return;
    }
    for(const row of trash.items){
      const item=document.createElement('div');item.className='kb-trash-row';
      const title=document.createElement('strong');title.textContent=row.title||'标题读不出';
      const meta=document.createElement('small');meta.textContent=trashRowText(row);
      const status=document.createElement('p');status.textContent=row.status_label||TRASH_STATUS_LABELS[row.status]||'状态未记';
      const actions=document.createElement('div');actions.className='kb-trash-actions';
      const check=document.createElement('label');check.className='kb-trash-check';
      const input=document.createElement('input');input.type='checkbox';input.dataset.restorePick=String(row.id??'');
      check.append(input,document.createTextNode(' 选择'));
      const restore=document.createElement('button');restore.type='button';restore.dataset.restore=String(row.id??'');restore.textContent='恢复';
      actions.append(check,restore);
      if(row.body_available!==false){
        const view=document.createElement('button');view.type='button';view.dataset.trashView=String(row.id??'');view.textContent='查看原文';
        actions.append(view);
      }
      item.append(title,meta,status,actions);box.append(item);
    }
    $('#kb-trash-list').querySelectorAll('[data-restore]').forEach(b=>b.onclick=()=>askRestore([b.dataset.restore]).catch(e=>setTrashStatus(e.message)));
    $('#kb-trash-list').querySelectorAll('[data-trash-view]').forEach(b=>b.onclick=()=>preview(b.dataset.trashView).catch(e=>setTrashStatus('这篇原文读不到：'+e.message)));
  }
  async function loadTrash(more=false){
    const request=++trash.request;
    const offset=more?trash.next||0:0;
    const d=await trashRequest(`/api/knowledge/trash?offset=${offset}&limit=${TRASH_PAGE}`);
    if(request!==trash.request)return d;
    trash.items=more?[...trash.items,...(Array.isArray(d.results)?d.results:[])]:Array.isArray(d.results)?d.results:[];
    trash.total=Number.isInteger(d.total)?d.total:trash.items.length;
    trash.next=Number.isInteger(d.next_offset)?d.next_offset:null;
    renderTrashList();renderTrashBar();
    $('#kb-trash-more').hidden=trash.next===null;
    return d;
  }
  const reloadTrash=()=>loadTrash(false);
  function showTrashResult(lines){
    // 已删除列表开着就写在列表里，否则只写主状态行；两处都不伪造成功。
    const box=$('#kb-trash-result');
    if(!$('#kb-trash-dialog').open){showReceipt(lines);return;}
    box.textContent='';
    for(const line of lines){const p=document.createElement('p');p.textContent=line;box.append(p);}
    const back=document.createElement('button');back.type='button';back.className='button';back.textContent='回到正常检索查看';
    back.onclick=()=>{$('#kb-trash-dialog').close();search().catch(e=>message(e.message));};
    box.append(back);
    box.hidden=false;
  }
  function restoreLines(result){
    const summary=result&&result.summary&&typeof result.summary==='object'?result.summary:{};
    const list=key=>Array.isArray(result&&result[key])?result[key]:null;
    const number=key=>{const value=list(key);return value===null?(Number.isInteger(summary[key])?summary[key]:'未统计'):value.length;};
    const lines=[`恢复结果：已恢复 ${number('restored')} 条 · 已解除隐藏 ${number('unhidden')} 条 · 不在已删除 ${number('not_found')} 条`];
    for(const item of (list('restored')||[]))if(item&&item.content_changed===true)lines.push('有一条正文与删除当时已不同，恢复的是当前版本，请核对。');
    if(list('unhidden')&&list('unhidden').length)lines.push('已解除隐藏，来源已不在索引，等待该来源同步。');
    if(list('not_found')&&list('not_found').length)lines.push('有些条目不在已删除里，可能已经恢复过。');
    if(list('restored')&&list('restored').length)lines.push('已恢复的条目回到常规检索，可在检索里查看。');
    return lines;
  }
  async function askRestore(ids){
    if(trash.busy)return;
    const wanted=[...new Set((Array.isArray(ids)?ids:[ids]).filter(id=>HEX32.test(String(id??''))))];
    if(!wanted.length){setTrashStatus('这条资料编号无效，没有提交恢复。');return;}
    const box=$('#kb-restore-confirm-items');box.textContent='';
    for(const id of wanted){
      const row=trash.items.find(x=>x.id===id);
      const line=document.createElement('div');line.className='kb-trash-item';
      const title=document.createElement('strong');title.textContent=(row&&row.title)||'标题读不出';
      const meta=document.createElement('small');meta.textContent=row?trashRowText(row):'来源：未记 · 删除时间未记录';
      line.append(title,meta);box.append(line);
    }
    trash.restoreIds=wanted;
    $('#kb-restore-confirm-title').textContent=`把这 ${wanted.length} 条资料恢复到常规检索？`;
    $('#kb-restore-confirm-status').textContent='';
    $('#kb-restore-confirm-ok').disabled=false;
    $('#kb-restore-confirm-dialog').showModal();
  }
  async function commitRestore(){
    if(trash.busy)return;
    const ids=trash.restoreIds.filter(id=>HEX32.test(String(id)));
    if(!ids.length){$('#kb-restore-confirm-status').textContent='没有可恢复的条目。';return;}
    const fromDocument=!$('#kb-trash-dialog').open;
    trash.busy=true;$('#kb-restore-confirm-ok').disabled=true;
    let result,error=null;
    try{result=await trashRequest('/api/knowledge/trash/restore',{ids});}
    catch(e){error=e;}
    trash.busy=false;$('#kb-restore-confirm-ok').disabled=false;
    if(error){$('#kb-restore-confirm-status').textContent=error.message;return;}
    trash.restoreIds=[];
    $('#kb-restore-confirm-dialog').close();
    showTrashResult(restoreLines(result));
    try{await reloadTrash();}catch(e){setTrashStatus(e.message);}
    try{await status();}catch(e){/* 计数刷新失败不影响已确认的恢复结果 */}
    if(fromDocument&&state.document&&ids.includes(state.document.id)){
      try{await preview(state.document.id);}catch(e){/* 读不到就保留原提示，不伪造已恢复 */}
    }
  }
  $('#kb-delete-selected').onclick=()=>askTrash(trash.picks.map(p=>p.id)).catch(e=>message(e.message));
  $('#kb-delete-clear').onclick=()=>{clearTrashPicks();renderResults();message('已取消选择，资料没有任何改动。');};
  $('#kb-trash-open').onclick=()=>{
    if(trash.busy)return;
    trash.busy=true;setTrashStatus('正在读取已删除列表…');
    loadTrash(false).then(()=>{setTrashStatus('');$('#kb-trash-dialog').showModal();})
      .catch(e=>{setTrashStatus(e.message);message(e.message);})
      .finally(()=>{trash.busy=false;});
  };
  $('#kb-trash-more').onclick=()=>{
    if(trash.busy)return;
    trash.busy=true;setTrashStatus('正在读取已删除列表…');
    loadTrash(true).then(()=>setTrashStatus('')).catch(e=>setTrashStatus(e.message)).finally(()=>{trash.busy=false;});
  };
  $('#kb-trash-restore-selected').onclick=()=>askRestore([...$('#kb-trash-list').querySelectorAll('[data-restore-pick]')].filter(b=>b.checked).map(b=>b.dataset.restorePick)).catch(e=>setTrashStatus(e.message));
  $('#kb-trash-confirm-ok').onclick=()=>{commitTrash().catch(e=>{$('#kb-trash-confirm-status').textContent=e.message;});};
  $('#kb-restore-confirm-ok').onclick=()=>{commitRestore().catch(e=>{$('#kb-restore-confirm-status').textContent=e.message;});};
  $('#kb-document-trash').onclick=()=>{if(state.document)askTrash([state.document.id]).catch(e=>message(e.message));};
  $('#kb-document-restore').onclick=()=>{if(state.document)askRestore([state.document.id]).catch(e=>message(e.message));};
  renderTrashBar();

  async function operation(button, action) {
    if(state.busy)return;state.busy=true;button.disabled=true;
    try{await action();}catch(e){message(e.message);}finally{state.busy=false;button.disabled=false;}
  }
  $('#kb-search-form').onsubmit=e=>{e.preventDefault();clearReceipt();state.tag='';search().catch(e=>message(e.message));};
  $('#kb-kind').onchange=e=>{clearReceipt();state.kind=e.target.value;state.source='';state.tag='';search().catch(e=>message(e.message));};
  $('#kb-more').onclick=()=>search(true).catch(e=>message(e.message));
  $('#kb-sync').onclick=()=>operation($('#kb-sync'),async()=>{await api('/api/knowledge/sync',{});await status();message('正在同步本机已有资料，完成后刷新检索结果。');});
  $('#kb-save-dossier').onclick=()=>operation($('#kb-save-dossier'),async()=>{await saveDossier();message('资料夹已保存，可以随时打开继续使用。');});
  $('#kb-create-draft').onclick=()=>operation($('#kb-create-draft'),createDraft);
  $('#kb-new-dossier').onclick=()=>{state.dossier=null;state.picks=[];$('#kb-dossier-title').value='';$('#kb-purpose').value='';$('#kb-purpose-ai-note').hidden=true;$('#kb-draft-note').hidden=true;renderPicks();message('新资料夹，之前保存的资料夹保留。');};
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
  setInterval(async()=>{if(polling)return;polling=true;try{const before=state.syncing||state.sources.some(s=>s.capture?.busy);await status();const after=state.syncing||state.sources.some(s=>s.capture?.busy);if(before&&!after){await search();notice('更新结束，检索结果已刷新；采集与索引状态见上方。');}}catch(e){problem(e.message);}finally{polling=false;}},2500);
  renderPicks();
  async function boot(){
    const d=await status();await search();
    const q=queryContext();
    const selected=d.sources.find(s=>s.id===q.source);
    if(selected){state.source=selected.id;state.kind=selected.document_kind||(selected.kind==='manual'?'notes':selected.kind==='gmail_cache'?'mail':selected.kind);$('#kb-kind').value=state.kind;await search();}
    if(q.intake!==null&&q.intake!=='')await intakeHandoff(q);
    if(q.document){
      if(!HEX32.test(q.document))markDocumentUnreadable('引用资料编号无效');
      else{
        const citation=q.p.has('start')||q.p.has('end')||q.p.has('hash')?{start:q.p.get('start'),end:q.p.get('end'),hash:q.p.get('hash')}:null;
        try{await preview(q.document,citation);}catch(e){markDocumentUnreadable(e.message);}
      }
    }
    if(d.sources.some(s=>s.status==='pending'))await api('/api/knowledge/sync',{});else await api('/api/knowledge/sync',{source:'articles'});
  }
  boot().catch(e=>message(e.message));
})();
