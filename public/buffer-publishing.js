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

async function mountBufferPublishing() {
  // Only ordinary X posts and threads use Buffer; long articles keep the local
  // browser draft flow and every other platform keeps export/manual records.
  if ((state.platform || 'x') !== 'x' || !state.studio || state.studio.type !== 'post') return;
  const slug = state.slug, version = state.article.version.id;
  const container = document.createElement('section');
  container.className = 'buffer-publishing';
  container.innerHTML = `<h2>Buffer · 推文发布</h2><p class="muted">先核对 Buffer 渠道与目标账号，再选择进入草稿箱或立即发布。提交后在这里刷新状态。</p><p id="buffer-check" role="status" class="muted">正在核对 Buffer 渠道…</p><div id="buffer-channel-detail" aria-live="polite"></div><div class="buffer-actions"><button class="button" id="buffer-draft" disabled>发送到 Buffer 草稿箱</button><button class="button primary" id="buffer-publish" disabled>直接发布到 X</button><button class="button subtle" id="buffer-refresh">刷新状态</button><button class="button subtle" id="buffer-recheck">重新核对渠道</button></div><p id="buffer-result" role="status" aria-live="polite"></p>`;
  $('#export-package').before(container);

  const draftButton = container.querySelector('#buffer-draft');
  const publishButton = container.querySelector('#buffer-publish');
  const refreshButton = container.querySelector('#buffer-refresh');
  const recheckButton = container.querySelector('#buffer-recheck');
  const statusLine = container.querySelector('#buffer-check');
  const detail = container.querySelector('#buffer-channel-detail');
  const resultLine = container.querySelector('#buffer-result');
  let check = null, busy = false, dialogOpen = false, checking = false, media = null;

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
    const blocked = !check.resolved || !(check.posts || []).length || !!dialogOpen || imageBlocked();
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
      + `<div class="buffer-dialog-error" role="alert" aria-live="assertive" hidden></div>`
      + `<div class="dialog-actions"><button class="button" data-cancel>取消</button><button class="button primary" data-go disabled>${escapeHTML(bufferModeLabel(mode))}</button></div>`;
    document.body.append(dialog);
    dialogOpen = true; paintCheck();
    const buttons = dialog.querySelectorAll('button');
    const box = dialog.querySelector('input');
    // The reason must be readable inside the modal: a native showModal() dialog sits
    // in the top layer, so a toast behind it is blurred and effectively invisible.
    const errorBox = dialog.querySelector('.buffer-dialog-error');
    box.onchange = () => { buttons[1].disabled = !box.checked; };
    buttons[0].onclick = () => dialog.close();
    buttons[1].onclick = async () => {
      if (busy) { errorBox.textContent = '提交正在进行，请勿重复操作'; errorBox.hidden = false; return; }
      busy = true; errorBox.hidden = true; errorBox.textContent = '';
      draftButton.disabled = publishButton.disabled = true;
      buttons[0].disabled = buttons[1].disabled = true;
      try {
        const reply = await api('/api/buffer/submit/' + encodeURIComponent(slug),
          { version, platform: 'x', mode, confirmed: true });
        resultLine.textContent = reply.record.message || '已提交到 Buffer';
        if (reply.outcome === 'unknown') resultLine.className = 'buffer-warning';
        else resultLine.className = '';
        dialog.close();
        await refreshDeliveries();
        await recheck().catch(() => {});
      } catch (error) {
        resultLine.textContent = '';
        errorBox.textContent = error.message || '提交没有完成';
        errorBox.hidden = false;
        buttons[0].disabled = false; buttons[1].disabled = !box.checked;
        busy = false; paintCheck();
      } finally {
        if (!dialogOpen) { busy = false; paintCheck(); }
      }
    };
    dialog.onclose = () => { dialog.remove(); dialogOpen = false; if (!busy) paintCheck(); };
    dialog.showModal();
  }

  draftButton.onclick = () => confirmSubmit('draft');
  publishButton.onclick = () => confirmSubmit('publish_now');
  recheckButton.onclick = () => { recheck().catch(error => toast(error.message)); };
  refreshButton.onclick = async () => {
    if (busy) return;
    busy = true; refreshButton.disabled = true;
    try {
      const reply = await api('/api/buffer/status/' + encodeURIComponent(slug), { version, platform: 'x' });
      resultLine.textContent = reply.record.message || '状态已刷新';
      await refreshDeliveries();
      await recheck().catch(() => {});
    } catch (error) { toast(error.message); }
    finally { busy = false; refreshButton.disabled = false; paintCheck(); }
  };

  try { await recheck(); }
  catch (error) { /* recheck() already reported the reason and kept the buttons locked */ }
}
