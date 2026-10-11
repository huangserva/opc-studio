/* Persistent jobs on this Mac; the Chrome extension executes visible X UI steps. */
function localBridge(command,payload,timeout=4000){
  const id=crypto.randomUUID();
  return new Promise((resolve,reject)=>{
    const timer=setTimeout(()=>{window.removeEventListener('message',receive);reject(Error('未连接本机发布扩展，请按页面说明加载后刷新工作台'));},timeout);
    function receive(event){if(event.source!==window||event.origin!==location.origin||event.data?.channel!=='studio-x-response'||event.data.id!==id)return;clearTimeout(timer);window.removeEventListener('message',receive);const r=event.data.result;if(r?.error)reject(Error(r.error));else resolve(r);}
    window.addEventListener('message',receive);window.postMessage({channel:'studio-x-request',id,command,payload},location.origin);
  });
}
function localDraftPreviewURL(url){return url.replace(/\/(?:preview\/)?focus\/?$|\/preview\/?$/,'')+'/preview';}
function localTargetAccount(value){return String(value||'').trim().replace(/^@/,'').toLowerCase();}
function localTargetPreference(slug){
  try{return localStorage.getItem('article-studio:x-target:'+slug)||'';}catch{return '';}
}
function localTargetLastUsed(){
  try{return localStorage.getItem('article-studio:x-target:last')||'';}catch{return '';}
}
function rememberLocalTarget(slug,account){
  if(!/^[a-z0-9_]{1,15}$/.test(account))return;
  try{localStorage.setItem('article-studio:x-target:'+slug,account);localStorage.setItem('article-studio:x-target:last',account);}catch{}
}
function localMediaLimitMessage(preflight){
  return `图片数量超限：正文 ${preflight.source_images} 张，@${preflight.account} 已实测单篇上限 ${preflight.limit.body_images} 张，超出 ${preflight.source_images-preflight.limit.body_images} 张。已停止上传；分批上传不能增加单篇文章容量。`;
}
function localSavedSummary(job){
  if(job.status!=='review_ready'||!job.evidence)return '';
  const evidence=job.evidence;
  if(['text_checked','order_checked','positions_checked','cover_checked','paragraphs_checked'].some(key=>evidence[key]!==true))return '<p>旧检查记录不完整，请先点击「重新检查草稿」。</p>';
  return `<p>保存后已重新加载检查：${Number(evidence.image_count)||0} 张正文配图${job.checkpoint?.cover?'、封面':''}；图片身份、顺序、位置和正文段落均通过核验。</p>`;
}
function localUploadDiagnostics(j){
  const d=j.diagnostics;if(!d||d.phase!=='text_check')return j.error_code==='DRAFT_CONTENT_MISMATCH'&&!j.checkpoint?.cover&&!Object.keys(j.checkpoint?.media||{}).length?'<p>插件停在文字校验，尚未开始提交封面或正文配图文件。</p>':'';
  const detail=d.title?.matches?d.body:d.title;
  let html=`<p>已记录：封面 ${d.uploaded_cover?1:0}/${d.expected_cover?1:0}，正文配图 ${Number(d.uploaded_body_images)||0}/${Number(d.expected_body_images)||0}。</p>`;
  if(detail?.first_difference){const diff=detail.first_difference;html+=`<details><summary>查看具体文字差异（第 ${Number(diff.index)+1} 个字符）</summary><p>原文 ${Number(detail.expected_chars)} 字符；草稿 ${Number(detail.observed_chars)} 字符。</p><p>原文片段：${escapeHTML(diff.expected||'')}</p><p>草稿片段：${escapeHTML(diff.observed||'')}</p></details>`;}
  return html;
}
async function mountLocalPublishing(){
  const slug=state.slug,version=state.article.version.id;
  const target=localTargetPreference(slug)||getPlatformDraft('x').account||localTargetLastUsed()||'';
  const container=document.createElement('section');container.className='local-publishing';
  container.innerHTML=`<h2>本机 X 发布</h2><p class="muted">使用这台 Mac 已登录的 Chrome，自动新建草稿、填入正文、上传封面和配图；保存检查后再确认公开发布。</p><label>目标账号<input id="local-x-account" placeholder="目标 X 账号" value="${escapeHTML(target)}"></label><p class="muted">修改目标后使用该账号的独立任务；X 登录账号需与目标一致。旧任务保留在其他账号历史中。</p><details><summary>接入已有草稿（可选）</summary><label>草稿链接<input id="local-x-draft" type="url" placeholder="https://x.com/compose/articles/edit/…"></label></details><p id="local-x-connection" role="status">正在检查本机扩展…</p><div id="local-x-preflight" aria-live="polite"></div><button class="button primary" id="local-x-start">上传到 X 草稿 / 继续任务</button><div id="local-x-jobs" aria-live="polite"></div><details><summary>首次连接本机 Chrome</summary><p>在 Chrome 打开 chrome://extensions，开启「开发者模式」，点击「加载已解压的扩展程序」，选择下方目录。然后刷新工作台和 X 标签页。</p><code style="overflow-wrap:anywhere">publishing/local-extension</code>（位于本工作台仓库目录下）<p class="muted">扩展仅用于本机工作台和 x.com，不读取密码或 Cookie。普通推文发布入口尚未接通。</p></details>`;
  $('#export-package').before(container);
  let connected=false,executing=false,preparing=false,paintSerial=0;
  const accountInput=container.querySelector('#local-x-account');
  function selectedAccount(){
    const account=localTargetAccount(accountInput.value);
    if(!/^[a-z0-9_]{1,15}$/.test(account))throw Error('请输入有效的 X 账号名');
    return account;
  }
  function assertTarget(job){if(!job||localTargetAccount(job.account)!==selectedAccount())throw Error('目标账号已改变，请使用当前账号的任务；原任务记录保留');}
  async function assertMediaCapacity(account){
    const preflight=await api('/api/local-publishing/preflight?slug='+encodeURIComponent(slug)+'&version='+encodeURIComponent(version)+'&account='+encodeURIComponent(account));
    if(preflight.over_limit||preflight.needs_split)throw Error(localMediaLimitMessage(preflight));
  }
  const start=container.querySelector('#local-x-start');start.disabled=true;
  try{const reply=await localBridge('ping');if(reply?.connected!==true)throw Error('扩展未返回有效连接确认');if(reply.version!=='0.1.33')throw Error('发布扩展需要更新：在 chrome://extensions 点击「内容工作台 · 本机 X 发布」的刷新图标，再刷新工作台和 X 页面');connected=true;container.querySelector('#local-x-connection').textContent='本机扩展 0.1.33 已连接 · 将在 X 文章列表核验账号';}catch(e){container.querySelector('#local-x-connection').textContent=e.message;}
  async function paint(){
    if(!container.isConnected)return;
    const account=localTargetAccount(accountInput.value),serial=++paintSerial;
    if(!/^[a-z0-9_]{1,15}$/.test(account)){
      start.disabled=true;
      const preparation=container.querySelector('#local-x-preflight');preparation.textContent='请输入有效的 X 账号名';delete preparation.dataset.rendered;
      for(const b of container.querySelectorAll('#local-x-jobs button'))b.disabled=true;
      return;
    }
    const [jobs,preflight]=await Promise.all([api('/api/local-publishing/jobs?slug='+encodeURIComponent(slug)+'&version='+encodeURIComponent(version)),api('/api/local-publishing/preflight?slug='+encodeURIComponent(slug)+'&version='+encodeURIComponent(version)+'&account='+encodeURIComponent(account))]);
    if(!container.isConnected||serial!==paintSerial||account!==localTargetAccount(accountInput.value))return;
    const currentJobs=jobs.filter(j=>localTargetAccount(j.account)===preflight.account);
    const otherJobs=jobs.filter(j=>localTargetAccount(j.account)!==preflight.account);
    const overLimit=preflight.over_limit||preflight.needs_split;
    start.disabled=!connected||overLimit||executing||preparing;
    accountInput.disabled=executing||preparing;
    start.textContent=currentJobs.some(j=>j.status==='review_ready')?'重新检查 X 草稿':currentJobs.some(j=>!['published','superseded'].includes(j.status))?'上传到 X 草稿 / 继续任务':`上传到 @${preflight.account} 的 X 草稿`;
    const preparation=container.querySelector('#local-x-preflight');
    const reference=preflight.reference_limit;
    const preparationHTML=overLimit?`<div class="local-limit-warning" role="alert"><strong>${escapeHTML(localMediaLimitMessage(preflight))}</strong></div>`:`<p class="muted">正文 ${preflight.source_images} 张配图${preflight.limit?' · 已实测单篇上限 '+preflight.limit.body_images+' 张':' · 此账号图片上限尚未验证'}。</p>${!preflight.limit&&reference&&preflight.source_images>reference.body_images?`<div class="local-limit-warning" role="alert">上传前提示：正文 ${preflight.source_images} 张，已超过本机 @${escapeHTML(reference.account)} 实测的 ${reference.body_images} 张单篇上限。@${escapeHTML(preflight.account)} 的上限尚未验证，不能据此保证完整上传；如 X 拒绝，任务立即停止。</div>`:''}`;
    if(preparation.dataset.rendered!==preparationHTML){preparation.innerHTML=preparationHTML;preparation.dataset.rendered=preparationHTML;}
    if(!container.isConnected)return;
    const names={queued:'等待连接',running:'正在上传',blocked:'需要处理',review_ready:'等待预览确认',publish_requested:'等待发布',publishing:'正在发布',unknown:'结果待核验',published:'已发布',superseded:'原任务已保留'};
    const jobsHTML=currentJobs.map(j=>`<div class="delivery-entry"><strong>@${escapeHTML(j.account)} · ${names[j.status]||j.status}</strong><p>${escapeHTML(j.message)}</p>${localSavedSummary(j)}${j.checkpoint?.executor?`<small>执行：本机 Chrome 扩展 ${escapeHTML(j.checkpoint.executor.version)}</small>`:''}${j.status==='blocked'?localUploadDiagnostics(j):''}<small>${versionName(j.version)} · ${date(j.updated_at)}</small>${j.draft_url?`<p><a href="${escapeHTML(j.draft_url)}" target="_blank" rel="noopener">打开原草稿检查 ↗</a></p>`:''}${j.published_url?`<a href="${escapeHTML(j.published_url)}" target="_blank" rel="noopener">查看已发布文章 ↗</a>`:''}${j.status==='review_ready'?`<p><a href="${escapeHTML(localDraftPreviewURL(j.draft_url))}" target="_blank" rel="noopener">查看 X 完整预览 ↗</a></p><button class="button primary" data-confirm="${j.id}">核对预览并确认发布</button>`:''}${j.status==='blocked'&&j.error_code==='DRAFT_UNAVAILABLE'?`<button class="button" data-new-draft="${j.id}">保留原记录，另建草稿</button>`:''}${!['published','publishing','unknown','superseded'].includes(j.status)&&j.error_code!=='MEDIA_LIMIT'?`<button class="button" data-resume="${j.id}">${j.status==='publish_requested'?'执行已确认发布':j.status==='review_ready'?'重新检查草稿':'继续上传'}</button>`:''}</div>`).join('')+(!currentJobs.length?`<p>当前目标 @${escapeHTML(preflight.account)} 暂无上传任务。点击上方按钮，为此账号创建独立草稿。</p>`:'')+(otherJobs.length?`<details class="local-other-accounts"><summary>其他账号历史任务（${otherJobs.length}）</summary><p class="muted">以下任务保留原账号绑定；如需继续，请将目标账号改回对应账号。</p>${otherJobs.map(j=>`<div class="delivery-entry"><strong>@${escapeHTML(j.account)} · ${names[j.status]||j.status}</strong><p>${escapeHTML(j.message)}</p><small>${versionName(j.version)} · ${date(j.updated_at)}</small></div>`).join('')}</details>`:'');
    const jobsContainer=container.querySelector('#local-x-jobs');if(jobsContainer.dataset.rendered!==jobsHTML){jobsContainer.innerHTML=jobsHTML;jobsContainer.dataset.rendered=jobsHTML;}
    for(const b of jobsContainer.querySelectorAll('button'))b.disabled=executing||preparing||overLimit;
    for(const b of container.querySelectorAll('[data-resume]'))b.onclick=()=>execute(b.dataset.resume).catch(e=>toast(e.message));
    for(const b of container.querySelectorAll('[data-new-draft]'))b.onclick=async()=>{
      if(!connected){toast('请先更新并连接本机发布扩展');return;}
      if(executing||preparing)return;
      try{assertTarget(currentJobs.find(j=>j.id===b.dataset.newDraft));await assertMediaCapacity(selectedAccount());assertTarget(currentJobs.find(j=>j.id===b.dataset.newDraft));}catch(e){toast(e.message);return;}
      if(!confirm('原草稿是否仍存在尚未确认。此操作会保留原草稿及任务记录，另建一份草稿并重新上传当前固定版本；不会公开发布。确定另建吗？'))return;
      preparing=true;b.disabled=true;accountInput.disabled=true;
      try{const replacement=await api('/api/local-publishing/job/'+b.dataset.newDraft,{action:'new-draft',confirmed:true});assertTarget(replacement);await paint();await execute(replacement.id,{prepared:true});}catch(e){toast(e.message);}finally{preparing=false;accountInput.disabled=executing;await paint();}
    };
    for(const b of container.querySelectorAll('[data-confirm]'))b.onclick=()=>confirmJob(currentJobs.find(j=>j.id===b.dataset.confirm)).catch(e=>toast(e.message));
  }
  async function execute(id,{prepared=false}={}){
    if(!connected)throw Error('请先连接本机 Chrome 扩展');
    if(executing||(preparing&&!prepared))throw Error('任务正在执行，请等待当前操作完成');
    executing=true;accountInput.disabled=true;
    try{
    let bundle=await api('/api/local-publishing/bundle/'+id);
    assertTarget(bundle.job);
    await assertMediaCapacity(bundle.job.account);
    const manualDraft=container.querySelector('#local-x-draft').value.trim();
    if(!bundle.job.draft_url&&manualDraft){
      const bound=await api('/api/local-publishing/start/'+encodeURIComponent(slug),{version,account:bundle.job.account,draft_url:manualDraft});
      if(bound.id!==id)throw Error('任务已变更，请刷新后选择当前任务');
      bundle=await api('/api/local-publishing/bundle/'+id);
    }
    assertTarget(bundle.job);
    const promise=localBridge(bundle.job.status==='publish_requested'?'publish':'run',bundle,15*60*1000);
    paint().catch(()=>{});
    await promise;
    }finally{executing=false;accountInput.disabled=preparing;await paint();}
  }
  async function confirmJob(job){
    assertTarget(job);
    await assertMediaCapacity(job.account);assertTarget(job);
    const dialog=document.createElement('dialog');dialog.innerHTML=`<h2>确认公开发布到 @${escapeHTML(job.account)}</h2><p>${escapeHTML(job.title)} · ${versionName(job.version)}</p><p><a href="${escapeHTML(localDraftPreviewURL(job.draft_url))}" target="_blank" rel="noopener">打开 X 完整预览 ↗</a>，检查正文、封面、全部图片及顺序。</p><label><input type="checkbox">我已核对目标账号与完整草稿</label><div class="dialog-actions"><button class="button">返回检查</button><button class="button primary" disabled>确认公开发布</button></div>`;document.body.append(dialog);
    const buttons=dialog.querySelectorAll('button');buttons[0].onclick=()=>dialog.close();dialog.querySelector('input').onchange=e=>buttons[1].disabled=!e.target.checked;
    buttons[1].onclick=async()=>{if(executing||preparing)return;buttons[1].disabled=true;preparing=true;accountInput.disabled=true;try{assertTarget(job);await assertMediaCapacity(job.account);await api('/api/local-publishing/job/'+job.id,{action:'confirm',account:job.account,version:job.version,reviewed:true});dialog.close();await execute(job.id,{prepared:true});await refreshDeliveries();}catch(e){toast(e.message);}finally{preparing=false;accountInput.disabled=executing;await paint();}};
    dialog.onclose=()=>dialog.remove();dialog.showModal();
  }
  start.onclick=async()=>{
    if(state.dirty){toast('请先保存新版本，再上传固定版本');return;}
    if(executing||preparing)return;
    preparing=true;start.disabled=true;accountInput.disabled=true;
    try{const account=selectedAccount();rememberLocalTarget(slug,account);await assertMediaCapacity(account);const job=await api('/api/local-publishing/start/'+encodeURIComponent(slug),{version,account,draft_url:container.querySelector('#local-x-draft').value});assertTarget(job);await paint();await execute(job.id,{prepared:true});}catch(e){toast(e.message);}finally{preparing=false;accountInput.disabled=executing;await paint();}
  };
  function targetChanged(){
    rememberLocalTarget(slug,localTargetAccount(accountInput.value));
    container.querySelector('#local-x-draft').value='';
    start.disabled=true;
    for(const b of container.querySelectorAll('#local-x-jobs button'))b.disabled=true;
    paint().catch(e=>toast(e.message));
  }
  accountInput.addEventListener('input',targetChanged);
  accountInput.addEventListener('change',targetChanged);
  await paint();
  const timer=setInterval(()=>{if(!container.isConnected){clearInterval(timer);return;}paint().catch(()=>{});},2000);
}

// Let local diagnostics observe the application's own extension handshake without
// depending on Codex's separate Chrome control extension or exposing X sessions.
const localPublishingClient=crypto.randomUUID();
window.addEventListener('load',()=>{
  let probing=false;
  async function report(){
    if(probing)return;probing=true;
    try{
      let connected=false;
      try{const r=await localBridge('ping');connected=r?.connected===true;}catch{}
      await api('/api/local-publishing/connection',{client:localPublishingClient,connected});
    }catch{}finally{probing=false;}
  }
  report();setInterval(report,15000);
});
