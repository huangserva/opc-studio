/* X 推文/串文 → Buffer 草稿箱或直接发布。提交前必须确认，结果不明时不自动重试。 */
function bufferAccount(value) { return String(value || '').trim().replace(/^@/, '').toLowerCase(); }
function bufferModeLabel(mode) { return mode === 'publish_now' ? '直接发布到 X' : '发送到 Buffer 草稿箱'; }
function bufferImageCount(check) {
  const posts = (check && check.posts) || [];
  return Number(check && check.image_total) || posts.reduce((sum, post) => sum + ((post.images || []).length), 0);
}
function bufferImageSize(bytes) {
  const size = Number(bytes);
  if (!Number.isFinite(size) || size <= 0) return '大小未知';
  return size >= 1024 * 1024 ? (size / 1024 / 1024).toFixed(1) + ' MB' : Math.max(1, Math.round(size / 1024)) + ' KB';
}

function bufferRecordState(record) {
  const status=String(record.buffer_status||'').toLowerCase();
  if(record.kind==='buffer_published')return {label:'已发布到 X',tone:'success',terminal:true};
  if(record.kind==='buffer_failed'||['error','failed','not_sent','notsent'].includes(status))return {label:'发布失败',tone:'error',terminal:true};
  if(record.kind==='buffer_unknown')return {label:'提交结果待核实',tone:'warning',terminal:false};
  if(record.kind==='buffer_in_progress')return {label:'正在提交 · 结果尚未确认',tone:'pending',terminal:false};
  if(status==='sent')return {label:'Buffer 显示已发布 · 链接待确认',tone:'warning',terminal:false};
  if(status==='draft'||record.kind==='buffer_draft'&&!status)return {label:'已存入 Buffer 草稿箱 · 尚未发布',tone:'draft',terminal:false};
  if(['scheduled','pending','queued'].includes(status))return {label:'已排队 / 已安排发布 · 尚未发布',tone:'pending',terminal:false};
  return {label:'已提交 Buffer · 等待发布结果',tone:'pending',terminal:false};
}
function bufferRecordLinks(record) {
  // The public API exposes externalLink (X), but no Buffer editor permalink.
  // Use a supplied, verified Buffer link when available; otherwise provide dashboard access.
  let bufferURL='https://publish.buffer.com/';
  try{const value=new URL(record.buffer_url);if(value.protocol==='https:'&&value.hostname==='publish.buffer.com'&&!value.username&&!value.password)bufferURL=value.href;}catch{}
  return `<div class="buffer-record-links"><a class="button subtle" href="${escapeHTML(bufferURL)}" target="_blank" rel="noopener noreferrer">打开 Buffer ↗</a>`
    +(record.url?`<a class="button primary" href="${escapeHTML(record.url)}" target="_blank" rel="noopener noreferrer">查看 X 推文 ↗</a>`:'')+'</div>';
}
function bufferRecordMarkup(record) {
  const view=bufferRecordState(record);
  return `<strong class="buffer-state buffer-state-${view.tone}">${escapeHTML(view.label)}</strong>`
    +`<small>${escapeHTML(versionName(record.version))} · ${escapeHTML(date(record.created_at))} · @${escapeHTML(bufferAccount(record.account))}</small>`
    +(record.message?`<p class="delivery-message">${escapeHTML(record.message)}</p>`:'')
    +(record.error_message?`<p class="buffer-warning">${escapeHTML(record.error_message)}</p>`:'')
    +(record.observed_at?`<small>最后核对：${escapeHTML(date(record.observed_at))}</small>`:'')
    +(record.sent_at?`<small>Buffer 发布时间：${escapeHTML(date(record.sent_at))}</small>`:'')
    +(record.due_at?`<small>计划发布时间：${escapeHTML(date(record.due_at))}</small>`:'')
    +bufferRecordLinks(record)
    +(record.post_id?`<details><summary>帖子编号</summary><code>${escapeHTML(record.post_id)}</code></details>`:'');
}

async function mountBufferPublishing() {
  // Only ordinary X posts and threads use Buffer; long articles keep the local
  // browser draft flow and every other platform keeps export/manual records.
  if ((state.platform || 'x') !== 'x' || !state.studio || state.studio.type !== 'post') return;
  const slug = state.slug, version = state.article.version.id;
  const container = document.createElement('section');
  container.className = 'buffer-publishing';
  container.innerHTML = `<h2>Buffer · 推文发布</h2><p class="muted">先核对 Buffer 渠道与目标账号，再选择进入草稿箱或立即发布。提交后自动核对状态，也可手动刷新。</p><p id="buffer-check" role="status" class="muted">正在核对 Buffer 渠道…</p><div id="buffer-channel-detail" aria-live="polite"></div><div class="buffer-actions"><button class="button" id="buffer-draft" disabled>发送到 Buffer 草稿箱</button><button class="button primary" id="buffer-publish" disabled>直接发布到 X</button><button class="button subtle" id="buffer-refresh">刷新状态</button><button class="button subtle" id="buffer-recheck">重新核对渠道</button></div><div id="buffer-result" role="status" aria-live="polite"></div><small id="buffer-auto-status" role="status"></small>`;
  $('#export-package').before(container);

  const draftButton = container.querySelector('#buffer-draft');
  const publishButton = container.querySelector('#buffer-publish');
  const refreshButton = container.querySelector('#buffer-refresh');
  const recheckButton = container.querySelector('#buffer-recheck');
  const statusLine = container.querySelector('#buffer-check');
  const detail = container.querySelector('#buffer-channel-detail');
  const resultLine = container.querySelector('#buffer-result');
  const autoLine=container.querySelector('#buffer-auto-status');
  let autoTimer=null, autoChecks=0, lastStatusError=null;
  let check = null, busy = false, dialogOpen = false, checking = false, media = null;

  function currentPanel(){return container.isConnected&&state.slug===slug&&state.article.version.id===version&&state.platform==='x';}
  function latestRecord(){return [...(state.article.deliveries||[])].reverse().find(r=>r.version===version&&String(r.kind||'').startsWith('buffer_'));}
  function paintRecord(record){if(!currentPanel())return;resultLine.innerHTML=bufferRecordMarkup({...record,version:record.version||version,account:record.account||check?.account});}
  function scheduleAuto(){
    clearTimeout(autoTimer);
    if(!currentPanel())return;
    const record=latestRecord();
    if(!record?.post_id){autoLine.textContent=record?.kind==='buffer_unknown'?'提交结果待核实；请勿重复发送，请到 Buffer 核对。':'';return;}
    if(bufferRecordState(record).terminal){autoLine.textContent='已确认最终状态，停止自动核对。';return;}
    if(autoChecks>=20){autoLine.textContent='已完成本轮自动核对；需要时点击“刷新状态”。';return;}
    const delay=record.buffer_status==='draft'?180000:30000;
    autoLine.textContent=(lastStatusError?'上次核对失败：'+lastStatusError+'；保留上次结果。 ':'')+`页面保持打开时每${delay===180000?' 3 分钟':' 30 秒'}自动核对；后台暂停。`;
    autoTimer=setTimeout(async()=>{
      if(!currentPanel())return;
      if(document.hidden||busy||dialogOpen){scheduleAuto();return;}
      autoChecks++;
      await updateStatus(true);
      scheduleAuto();
    },delay);
  }
  async function updateStatus(automatic=false){
    if(busy||!currentPanel())return;
    busy=true;refreshButton.disabled=true;paintCheck();
    if(!automatic)autoLine.textContent='正在查询 Buffer 最新状态…';
    try{
      const reply=await api('/api/buffer/status/'+encodeURIComponent(slug),{version,platform:'x'});
      if(!currentPanel())return;
      lastStatusError=reply.outcome==='failed'?reply.record.message:null;
      paintRecord(reply.record);await refreshDeliveries();
    }catch(error){lastStatusError=error.message;if(currentPanel()){autoLine.textContent='状态核对失败：'+error.message+'；保留上次结果。';if(!automatic)toast(error.message);}}
    finally{busy=false;if(currentPanel()){refreshButton.disabled=false;paintCheck();}}
  }
  const detached=new MutationObserver(()=>{if(!container.isConnected){clearTimeout(autoTimer);detached.disconnect();}});
  detached.observe(container.parentNode,{childList:true,subtree:true});

  // The image host is operator-level config: the panel has to know whether a work
  // with images can be submitted at all, and the answer never carries a credential.
  async function loadMediaHost() {
    try { media = await api('/api/buffer/media/' + encodeURIComponent(slug), { version, platform: 'x' }); }
    catch (error) { media = { configured: false, problem: '图床（R2）状态无法核对：' + (error.message || '未知原因') }; }
  }

  function imageBlocked() {
    return !!check && bufferImageCount(check) > 0 && !!media && media.configured === false;
  }

  function imageWarning() {
    if (!imageBlocked()) return '';
    return `<div class="buffer-warning" role="alert">${escapeHTML(media.problem || '尚未配置图床（R2），带图作品无法提交')}</div>`;
  }

  // While the channel check is in flight the panel must not look actionable:
  // the reviewer cannot confirm against a channel that has not been verified yet.
  function paintChecking() {
    checking = true;
    draftButton.disabled = publishButton.disabled = true;
    recheckButton.disabled = true;
    statusLine.textContent = '正在核对 Buffer 渠道…';
    statusLine.className = 'muted';
  }

  function paintCheckFailure(message) {
    statusLine.textContent = message;
    statusLine.className = 'buffer-warning';
    // Replace any stale channel summary: a failed check must not leave the reviewer
    // looking at a channel list that was just not confirmed.
    detail.innerHTML = `<div class="buffer-warning" role="alert">${escapeHTML(message)}</div>`;
    draftButton.disabled = publishButton.disabled = true;
    recheckButton.disabled = false;
  }

  function paintCheck() {
    if (!check) { draftButton.disabled = publishButton.disabled = true; return; }
    statusLine.textContent = check.message || '';
    statusLine.className = check.resolved ? 'muted' : 'buffer-warning';
    if (check.resolved) {
      const images = bufferImageCount(check);
      detail.innerHTML = `<p>目标账号 <strong>@${escapeHTML(bufferAccount(check.account))}</strong> · Buffer 渠道 <strong>@${escapeHTML(check.channel.name)}</strong> · ${Number(check.post_count) || (check.posts || []).length} 条推文${images ? ` · ${images} 张图片` : ''}</p>`
        + (images ? `<p class="buffer-images-summary">图片会在点击提交后上传到图床${media && media.host ? `（${escapeHTML(media.host)}）` : ''}并校验公开链接，任一失败都不会提交到 Buffer。</p>` : '')
        + imageWarning()
        + ((check.blocking || []).length ? `<div class="buffer-warning" role="alert">${check.blocking.map(escapeHTML).join('；')}</div>` : '');
    } else {
      detail.innerHTML = `<div class="buffer-warning" role="alert">${escapeHTML(check.message || '无法核对 Buffer 渠道')}</div>` + imageWarning();
    }
    const existing=latestRecord();
    const held=existing&&existing.kind!=='buffer_failed';
    const blocked = !check.resolved || !(check.posts || []).length || !!dialogOpen || imageBlocked() || held;
    draftButton.disabled = publishButton.disabled = blocked || busy;
    recheckButton.disabled = busy || !!dialogOpen;
  }

  async function recheck() {
    paintChecking();
    let failure = null;
    try { check = await api('/api/buffer/channels/' + encodeURIComponent(slug), { version, platform: 'x' }); }
    catch (error) { failure = error; }
    checking = false;
    if (failure) { paintCheckFailure(failure.message); throw failure; }
    if (!media) await loadMediaHost();
    paintCheck();
    return check;
  }

  function confirmSubmit(mode) {
    if (busy || checking || dialogOpen || !check || !check.resolved || imageBlocked()) return;
    const posts = check.posts || [];
    if (!posts.length) { toast('没有可提交的推文，请先保存版本'); return; }
    const imageTotal = bufferImageCount(check);
    const dialog = document.createElement('dialog');
    dialog.className = 'buffer-confirm';
    dialog.innerHTML = `<h2>确认${escapeHTML(bufferModeLabel(mode))}</h2>`
      + `<p>${mode === 'publish_now' ? 'Buffer 会把下面每一条立即公开发布到 X。' : 'Buffer 只生成草稿，不会公开发布。'}</p>`
      + `<p>目标账号 <strong>@${escapeHTML(bufferAccount(check.account))}</strong> · Buffer 渠道 <strong>@${escapeHTML(check.channel.name)}</strong> · ${escapeHTML(versionName(version))}${imageTotal ? ` · ${imageTotal} 张图片` : ''}</p>`
      + (imageTotal ? `<p class="buffer-images-summary">下面每条推文列出它将带的图片；点击提交后才会上传到图床${media && media.host ? `（${escapeHTML(media.host)}）` : ''}并校验公开链接。</p>` : '')
      + `<div class="buffer-tweets">${posts.map((post, index) => {
          const images = post.images || [];
          return `<div class="buffer-tweet"><span>推文 ${index + 1}</span><pre>${escapeHTML(post.text || '')}</pre>`
            + (images.length
              ? `<div class="buffer-tweet-images">${images.map(image => `<figure><img src="${escapeHTML(image.url || '')}" alt="${escapeHTML(image.file || '')}" loading="lazy"><figcaption>${escapeHTML(image.file || '')} · ${escapeHTML(bufferImageSize(image.bytes))}</figcaption></figure>`).join('')}</div><small class="buffer-tweet-count">带 ${images.length} 张图片</small>`
              : '<small class="buffer-tweet-count">不带图片</small>')
            + '</div>';
        }).join('')}</div>`
      + `<label><input type="checkbox">我已核对目标账号、Buffer 渠道、全部 ${posts.length} 条推文全文${imageTotal ? '与每条推文所带的图片' : ''}</label>`
      + `<div class="buffer-dialog-progress" role="status" aria-live="polite" hidden></div><div class="buffer-dialog-error" role="alert" aria-live="assertive" hidden></div>`
      + `<div class="dialog-actions"><button class="button" data-cancel>取消</button><button class="button primary" data-go disabled>${escapeHTML(bufferModeLabel(mode))}</button></div>`;
    document.body.append(dialog);
    dialogOpen = true; paintCheck();
    const buttons = dialog.querySelectorAll('button');
    const box = dialog.querySelector('input');
    // The reason must be readable inside the modal: a native showModal() dialog sits
    // in the top layer, so a toast behind it is blurred and effectively invisible.
    const errorBox = dialog.querySelector('.buffer-dialog-error');
    const progressBox=dialog.querySelector('.buffer-dialog-progress');
    let sendingTimer=null;
    box.onchange = () => { buttons[1].disabled = !box.checked; };
    buttons[0].onclick = () => dialog.close();
    buttons[1].onclick = async () => {
      if (busy) { errorBox.textContent = '提交正在进行，请勿重复操作'; errorBox.hidden = false; return; }
      busy = true; errorBox.hidden = true; errorBox.textContent = '';
      const started=Date.now();
      const paintSending=()=>{const seconds=Math.floor((Date.now()-started)/1000);progressBox.hidden=false;progressBox.textContent=`${imageTotal?'正在上传图片、校验链接并提交到 Buffer':'正在提交到 Buffer'}… 已等待 ${seconds} 秒，请勿重复点击。`;resultLine.textContent=progressBox.textContent;};
      paintSending();sendingTimer=setInterval(paintSending,1000);
      buttons[1].textContent='正在发送…';
      dialog.oncancel=event=>{if(busy)event.preventDefault();};
      draftButton.disabled = publishButton.disabled = true;
      buttons[0].disabled = buttons[1].disabled = true;
      try {
        const reply = await api('/api/buffer/submit/' + encodeURIComponent(slug),
          { version, platform: 'x', mode, confirmed: true });
        clearInterval(sendingTimer);paintRecord(reply.record);
        if (reply.outcome === 'unknown') resultLine.className = 'buffer-warning';
        else resultLine.className = '';
        dialog.close();
        await refreshDeliveries();
        await recheck().catch(() => {});
        scheduleAuto();
      } catch (error) {
        clearInterval(sendingTimer);progressBox.hidden=true;
        resultLine.textContent = '发送未确认：'+error.message;
        errorBox.textContent = error.message || '提交没有完成';
        errorBox.hidden = false;
        buttons[0].disabled = false; buttons[1].disabled = !box.checked;
        busy = false; paintCheck();
      } finally {
        clearInterval(sendingTimer);buttons[1].textContent=bufferModeLabel(mode);
        if (!dialogOpen) { busy = false; paintCheck(); }
      }
    };
    dialog.onclose = () => { dialog.remove(); dialogOpen = false; if (!busy) paintCheck(); };
    dialog.showModal();
  }

  draftButton.onclick = () => confirmSubmit('draft');
  publishButton.onclick = () => confirmSubmit('publish_now');
  recheckButton.onclick = () => { recheck().catch(error => toast(error.message)); };
  refreshButton.onclick = async () => {autoChecks=0;await updateStatus();scheduleAuto();};

  try { await recheck();const record=latestRecord();if(record){paintRecord(record);if(record.post_id&&!bufferRecordState(record).terminal)await updateStatus(true);}scheduleAuto(); }
  catch (error) { /* recheck() already reported the reason and kept the buttons locked */ }
}
