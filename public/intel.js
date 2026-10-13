'use strict';
(() => {
const $ = s => document.querySelector(s), esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const bodyStates = { pending: '正文待抓', awaiting_confirm: '正文待确认', failed: '正文抓取失败', pushed: '已送入回收站' };
const pathLabels = { album: '合集', sogou: '搜狗', backend: '后台' };
const jobLabels = { running: '进行中', done: '已完成', partial: '部分完成', rate_limited: '限流冷却', credentials: '需重新登录后台', interrupted: '已中断', cooldown: '冷却中，本轮跳过', busy: '已有任务在跑', error: '失败' };
let accounts = [], active = '', onlyActive = true, polling = 0, pollingJobId = '';

async function api(url, body) {
  const r = await fetch(url, body === undefined ? {} : { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Article-Studio': '1' }, body: JSON.stringify(body) });
  const value = await r.json();
  if (!r.ok) throw new Error(value.error || '请求失败');
  return value;
}
function say(message, tone) {
  const node = $('#itl-status'); node.textContent = message || '';
  if (tone) node.dataset.tone = tone; else delete node.dataset.tone;
}
async function guard(work) {
  try { return await work(); }
  catch (error) { say(error.message, 'bad'); return null; }
}

function quotaBar(record) {
  record = record || {};
  const parts = [`订阅号 ${record.subscriptions ?? '未知'} 个`, `文章 ${record.articles ?? '未知'} 篇`, `待确认 ${record.awaiting ?? '未知'} 篇`];
  const credentialTone = { ready: '', missing: 'bad', expired: 'bad', unknown: 'warn' }[record.credentials] || '';
  parts.push(record.detail || '凭证状态未知');
  const cooling = !!record.cooldown_until;
  if (cooling) parts.push(`接口冷却至 ${record.cooldown_label || record.cooldown_until}`);
  else if (record.rate_strikes) parts.push(`限流累计 ${record.rate_strikes} 次（连续 3 次进入 6 小时冷却）`);
  // 搜狗冷却与后台冷却相互独立，分开写，不让 ready 掩盖掉验证码冷却。
  if (record.sogou_cooldown_until) parts.push(`搜狗冷却至 ${record.sogou_cooldown_label || record.sogou_cooldown_until}`);
  // 凭证问题与限流分开标记：前者要重新登录，后者只能等。
  $('#itl-quota').innerHTML = parts.map((part, index) => {
    const tone = index === 2 ? credentialTone : (cooling ? 'warn' : (record.rate_strikes ? 'warn' : ''));
    return `<span${tone ? ` data-tone="${tone}"` : ''}>${esc(part)}</span>`;
  }).join('');
}

function accountNav() {
  $('#itl-accounts').innerHTML = accounts.length ? accounts.map(a =>
    `<button data-fakeid="${esc(a.fakeid)}"${a.fakeid === active ? ' class="selected"' : ''}>` +
    `<span>${esc(a.name || a.fakeid)}</span><small>${a.article_count} 篇</small></button>`).join('')
    : '<p class="itl-note" style="padding:0 12px">还没有订阅号</p>';
}

function articleRow(a) {
  const state = a.drop_id ? 'pushed' : a.body_status;
  const label = a.drop_id ? bodyStates.pushed : (bodyStates[a.body_status] || a.body_status);
  const push = state === 'awaiting_confirm'
    ? `<button class="button" data-action="push" data-id="${esc(a.id)}">送入回收站</button>` : '';
  const from = a.source && pathLabels[a.source]
    ? `<span class="itl-src" data-source="${esc(a.source)}">${esc(pathLabels[a.source])}</span>` : '';
  // href 只允许 https://mp.weixin.qq.com/ 开头。空链接、占位串、http、别的主机和脚本协议都不渲染。
  // 没有可用链接时按入库原因说明，三种都不输出 href。认不出原因就用中性说法。
  const reason = a.link_missing_reason;
  const blocked = reason === 'sogou_title_only' || reason === 'invalid_link';
  const usable = !blocked && typeof a.link === 'string' && a.link.startsWith('https://mp.weixin.qq.com/');
  const noLinkCopy = reason === 'sogou_title_only' ? '搜狗只拿到标题，链接被反爬拦住'
    : reason === 'invalid_link' ? '链接不是公众号文章地址，已隐藏' : '没有可用链接';
  const link = usable
    ? `<a href="${esc(a.link)}" target="_blank" rel="noreferrer">原文链接 ↗</a>`
    : `<span class="itl-nolink">${noLinkCopy}</span>`;
  return `<article class="itl-article"><header><span class="itl-account">${esc(a.name || a.fakeid)}</span>` +
    `<span class="itl-time">${esc(a.published_label || '发布时间未知')}</span>${from}` +
    `<span class="itl-body-state" data-status="${esc(state)}">${esc(label)}</span></header>` +
    `<h3>${esc(a.title || '（无标题）')}</h3>` +
    `<p class="itl-meta">${link}` +
    (a.chars ? ` · 正文 ${a.chars} 字` : '') + (a.images.length ? ` · ${a.images.length} 张图` : '') +
    (a.error ? ` · ${esc(a.error)}` : '') + `</p>` +
    `<footer><button class="button subtle" data-action="detail" data-id="${esc(a.id)}">看正文</button>${push}</footer></article>`;
}

async function loadArticles() {
  const params = new URLSearchParams({ limit: '200' });
  if (active && onlyActive) params.set('fakeid', active);
  try {
    const data = await api('/api/intel/articles?' + params.toString());
    $('#itl-list').innerHTML = data.articles.length ? data.articles.map(articleRow).join('')
      : '<p class="itl-empty">还没有文章。选一个订阅号，点「抓最新」或「抓历史」。<br>正文抓取后先落库待确认，不会自动进知识库。</p>';
    $('#itl-count').textContent = `共 ${data.total} 篇`;
    renderAlbums(data.albums || [], data.album_search);
  } catch (error) { say('读取文章失败：' + error.message, 'bad'); }
}

function renderAlbums(albums, search) {
  // 没发现合集时把「为什么抓不到」说清楚，而不是让用户猜；
  // 上次发现尝试的留痕（哪篇文章、找到几个）也从后端带过来。
  $('#itl-album-note').textContent = albums.length
    ? `已发现 ${albums.length} 个合集`
    : '尚未发现合集：' + ((search && search.note) || '先用文章链接反查，或等抓到带合集的文章页');
}

// 最近一次采集结果：时间、状态、三条路线、冷却与下一步。刷新后仍在。
// 「接口里没有这个字段」和「后端明确说没有记录」是两件事：前者是后端进程还没更新，
// 绝不能对用户说这个号「还没抓过」；只有后端确认没有任务行时才那么说。
function resultCard(sub) {
  const name = esc(sub.name || sub.fakeid);
  const result = sub.last_result;
  if (!('last_result' in sub)) {
    return `<div class="itl-result" data-status="unavailable"><header><b>${name}</b>` +
      `<span class="itl-result-state" data-tone="warn">结果待更新</span></header>` +
      `<p class="itl-note">最近采集结果要等服务更新后才能显示。这里读不到不代表这个号没有抓取记录，` +
      `也不表示它没抓过。</p></div>`;
  }
  if (!result) {
    return `<div class="itl-result" data-status="none"><header><b>${name}</b>` +
      `<span class="itl-result-state" data-tone="warn">还没抓过</span></header>` +
      `<p class="itl-note">这个号还没有抓取记录。选中它，点「抓最新」或「抓历史」开始第一次。</p></div>`;
  }
  const tone = result.active ? '' : ({ done: '', partial: 'warn', busy: 'warn', cooldown: 'warn' }[result.status] || 'bad');
  const routes = (result.routes || []).map(line =>
    `<li data-path="${esc(line.path)}"><b>${esc(line.label)}</b><span>${esc(line.text)}</span></li>`).join('');
  // 分阶段摘要：后端给了结构化 outcome 就直接显示（空列表成功、复用、预算范围、正文未请求都在里面）。
  // 旧后端/旧任务没有 outcome：标「不可用」并按 detail/reason 回退，不猜计数。
  const outcome = result.outcome;
  // 没有可用的结构化摘要就标「不可用」：新后端确认缺 outcome（outcome_available=false），
  // 或真正旧后端两个字段都没有（outcome_available 既非 true 也非 false），都走这条回退。
  const stages = outcome && typeof outcome.summary === 'string' && outcome.summary
    ? `<p class="itl-result-head" data-note="stages">${esc(outcome.summary)}</p>`
    : (result.outcome_available !== true
        ? '<p class="itl-muted" data-note="stages-unavailable">分阶段结果不可用（旧后端或旧任务），以下按完整回执显示，不猜计数。</p>'
        : '');
  // 发现安全回执：只做安全投影（provider、状态、计数、范围三态）；未知版本提示不可用。
  const discovery = result.discovery;
  const range = (discovery && discovery.range_counts) || {};
  // 计数口径分开写（R10）：「来源命中」是各路来源各自命中的条数（同一篇被发现/合集/后台
  // 多路命中会各算一次）；「新增文章」是本轮按入库身份去重后真正新增的唯一文章数。
  // 两个数字分开显示，避免把命中数当成入库数。旧任务回执里没有这个口径：不显示、不猜。
  const hits = outcome && outcome.list && Number.isFinite(outcome.list.found) ? outcome.list.found : null;
  const uniqueNew = Number.isFinite(result.unique_new_articles) ? result.unique_new_articles : null;
  const counts = (hits === null || uniqueNew === null)
    ? ''
    : `<p class="itl-muted" data-note="counts">来源命中 ${esc(hits)} 篇 · 新增文章 ${esc(uniqueNew)} 篇` +
      `<span class="itl-muted">（来源命中按各路分别计数，同一篇文章被多个来源命中会各算一次；` +
      `新增文章按入库身份去重，只算本轮真正新增的）</span></p>`;
  const discoveryBlock = discovery
    ? `<p class="itl-muted" data-note="discovery">发现：来源 ${esc(discovery.provider || '未知')}` +
      ` · 状态 ${esc(discovery.state || '')} · 验证 ${esc(discovery.verified || 0)} 篇` +
      ` · 候选 ${esc(discovery.candidates_accepted || 0)} 个 · 读取 ${esc(discovery.reads_attempted || 0)} 次` +
      ` · 范围 窗口内 ${esc(range.within || 0)} / 未知 ${esc(range.unknown || 0)} / 窗口外 ${esc(range.outside || 0)}` +
      (discovery.stop_note ? ` · ${esc(discovery.stop_note)}` : '') + `</p>`
    : (result.discovery_present
        ? '<p class="itl-muted" data-note="discovery-unavailable">发现回执不可用（版本不识别），不猜计数。</p>'
        : '');
  return `<div class="itl-result" data-status="${esc(result.status)}">` +
    `<header><b>${name}</b>` +
    `<span class="itl-result-state"${tone ? ` data-tone="${tone}"` : ''}>${esc(result.status_label)}</span>` +
    `<span class="itl-muted">${esc(result.mode_label || '')}${result.at_label ? ' · ' + esc(result.at_label) : ''}</span></header>` +
    `<p class="itl-result-head">${esc(result.headline || result.status_label)}</p>` +
    stages +
    counts +
    discoveryBlock +
    (routes ? `<ul class="itl-result-routes">${routes}</ul>` : '') +
    (result.cooldown_label ? `<p class="itl-result-cooldown">冷却：${esc(result.cooldown_label)}</p>` : '') +
    // 所有权提示：后端只给安全状态与人话，绝不展示 owner token/dev/ino。
    (result.ownership_state === 'unknown' && result.ownership_note
      ? `<p class="itl-note" data-note="ownership">${esc(result.ownership_note)}</p>` : '') +
    (result.suggestion ? `<p class="itl-result-next">下一步：${esc(result.suggestion)}</p>` : '') +
    (result.detail ? `<details class="itl-result-detail"><summary>完整回执</summary><p>${esc(result.detail)}</p></details>` : '') +
    '</div>';
}

// 进程被杀或工作台重启后，那条「进行中」要等它自己的开始时间起满 1 小时才会自动标为「已中断」。
// 这是既有规则（避免误杀另一个进程里还在跑的抓取），本次不改，只把等待时间如实写清楚。
const STALE_WAIT_NOTE = '<p class="itl-note" data-note="stale-wait">抓取被中断（工作台重启、进程被杀）后，'
  + '那条「进行中」要等它开始时间起满 1 小时才会自动标为「已中断」，这期间抓取按钮会一直显示「抓取进行中…」。'
  + '这是为了避免误杀另一个进程里还在跑的抓取，不是页面卡住。</p>';

function renderResults() {
  const box = $('#itl-results');
  if (!box) return;
  box.innerHTML = accounts.length ? accounts.map(resultCard).join('') + STALE_WAIT_NOTE
    : '<p class="itl-empty">还没有订阅号。先录入一个，再抓最新或抓历史。</p>';
}

async function loadAccounts() {
  await guard(async () => {
    const data = await api('/api/intel/subscriptions');
    accounts = data.subscriptions || [];
    if (active && !accounts.some(a => a.fakeid === active)) active = '';
    if (!active && accounts.length) active = accounts[0].fakeid;
    accountNav(); renderResults(); await loadArticles();
  });
}

function showRunPanel(account) {
  $('#itl-run').hidden = false;
  $('#itl-run-name').textContent = account ? (account.name || account.fakeid) : '未选择订阅号';
  $('#itl-run-label').textContent = account ? account.fakeid : '先在左边选一个';
  renderBusy();
}

// 抓取进行中就禁用两个按钮：后台接口全局串行，连点只会白白排队并更容易触发限流。
let busyJob = null;
function renderBusy() {
  const off = !$('#itl-run-latest') || !active || !!busyJob;
  const latest = $('#itl-run-latest'), history = $('#itl-run-history'), discovery = $('#itl-run-discovery');
  if (!latest || !history) return;
  latest.disabled = off; history.disabled = off;
  if (discovery) discovery.disabled = off;
  latest.textContent = busyJob ? '抓取进行中…' : '抓最新（1 页 ≤20 篇）';
  history.textContent = busyJob ? '抓取进行中…' : '抓历史（≤30 篇）';
  if (discovery) discovery.textContent = busyJob ? '抓取进行中…' : '自动发现（搜索→合集）';
}

async function startRun(mode) {
  if (!active) return say('请先在左边选一个订阅号', 'bad');
  if (busyJob) return;
  busyJob = { id: '' }; renderBusy();
  const accepted = await guard(async () => {
    const job = await api('/api/intel/run', {
      fakeid: active, mode,
      sogou: $('#itl-use-sogou').checked,
      backend: $('#itl-use-backend').checked,
    });
    busyJob = job; renderBusy();
    if (job.reused) {
      // 后台接口全局串行，已有任务在跑就不新建；如实说明这是同一个任务。
      say('已有抓取任务在进行中（「' + (job.name || job.fakeid || '') + '」），这次没有新建。');
    } else {
      say(mode === 'latest' ? '已开始抓最新，请稍候…'
        : mode === 'discovery' ? '已开始自动发现（搜索→合集），请稍候…'
        : '已开始抓历史（≤30 篇），请稍候…');
    }
    pollJob(job.id);
    return job;
  });
  if (!accepted) { busyJob = null; renderBusy(); }
}

function pollJob(jobId) {
  if (pollingJobId === jobId) return;
  clearInterval(polling);
  pollingJobId = jobId;
  // 后台接口相邻请求 ≥90 秒，轮询只读本地任务表，不给公众号后台加压。
  polling = setInterval(async () => {
    let job = null;
    try { job = await api('/api/intel/jobs/' + jobId); } catch (error) { clearInterval(polling); pollingJobId = ''; return say('读取任务失败：' + error.message, 'bad'); }
    if (['queued', 'running'].includes(job.status)) { say('抓取中：已用 ' + job.pages + ' 页，新增文章 ' + job.added + ' 篇…'); return; }
    clearInterval(polling); pollingJobId = '';
    // 结束回执：优先用结构化 outcome.summary（与刷新后最近结果卡是同一份），
    // 没有就回退到完整 detail；done 正常、partial 警示、error 坏色。
    const summary = (job.outcome && job.outcome.summary) || job.detail || job.reason || '';
    const tone = job.status === 'done' ? '' : (job.status === 'partial' ? 'warn' : 'bad');
    say((jobLabels[job.status] || job.status) + ' · ' + summary, tone);
    busyJob = null; renderBusy();
    await loadAccounts(); await refreshQuota();
  }, 1500);
}

async function refreshQuota() {
  await guard(async () => {
    const record = await api('/api/intel/status');
    quotaBar(record);
    busyJob = record.busy ? (record.active_job || { id: '' }) : null;
    renderBusy();
    if (busyJob && busyJob.id) pollJob(busyJob.id);
  });
}

async function discoverAlbum() {
  if (!active) return say('请先选择订阅号', 'bad');
  const button = $('#itl-discover-album');
  await guard(async () => {
    const link = $('#itl-album-link').value.trim();
    let parsed;
    try { parsed = new URL(link); } catch (_) { throw new Error('请填写有效文章或合集链接'); }
    const q = parsed.searchParams;
    if (!['https:', 'http:'].includes(parsed.protocol) || parsed.host !== 'mp.weixin.qq.com' || parsed.username || parsed.password
        || q.getAll('__biz').length !== 1 || q.get('__biz') !== active)
      throw new Error('请粘贴当前订阅号的链接（__biz 必须一致）');
    if (!((parsed.pathname === '/s' && q.getAll('chksm').length === 1 && q.get('chksm').trim())
        || (parsed.pathname === '/mp/appmsgalbum' && q.getAll('album_id').length === 1 && /^\d{8,30}$/.test(q.get('album_id')))))
      throw new Error('文章链接必须带 chksm；合集链接必须带 album_id');
    button.disabled = true;
    try {
      const result = await api('/api/intel/identify', { url: link });
      if (result.fakeid !== active) throw new Error('返回的公众号与当前账号不一致');
      await loadArticles();
      const trace = result.album_search && !result.albums_found ? '（' + result.album_search.note + '）' : '';
      say((result.albums_found ? '已发现并记录合集，可以开始抓取。' : '这篇文章尚未发现合集，请补充另一篇文章或合集链接。') + trace);
    } finally { button.disabled = false; }
  });
}

async function search() {
  const query = $('#itl-query').value.trim();
  if (!query) return say('请填写公众号名称', 'bad');
  $('#itl-candidates').innerHTML = '<p class="itl-note">搜索中…</p>';
  const found = await guard(() => api('/api/intel/accounts?query=' + encodeURIComponent(query)));
  if (!found) return;
  const list = found.candidates || [];
  if (!list.length) { $('#itl-candidates').innerHTML = '<p class="itl-note">没有搜到这个号名。</p>'; return; }
  // 同名公众号很多，结果给用户选，不自动认第一个。
  $('#itl-candidates').innerHTML = list.map(c =>
    `<button class="itl-candidate" data-fakeid="${esc(c.fakeid)}" data-name="${esc(c.nickname || c.fakeid)}">` +
    `<b>${esc(c.nickname || '（无名称）')}</b><span>${esc(c.signature || '无简介')} · ${esc(c.fakeid)}</span></button>`).join('');
  say('搜到 ' + list.length + ' 个同名公众号，请确认 fakeid 后再录入。');
}

async function subscribe(fakeid, name) {
  await guard(async () => {
    await api('/api/intel/subscriptions', { fakeid, name });
    active = fakeid; $('#itl-query').value = ''; $('#itl-candidates').innerHTML = '';
    say('已录入「' + name + '」。同名号请核对 fakeid 是否一致。');
    await loadAccounts(); showRunPanel(accounts.find(a => a.fakeid === fakeid)); await refreshQuota();
  });
}

async function byLink() {
  const url = $('#itl-link').value.trim();
  if (!url) return say('请粘贴文章链接', 'bad');
  say('正在读文章页反查公众号…');
  const found = await guard(() => api('/api/intel/identify', { url }));
  if (!found) return;
  say('反查到「' + (found.nickname || found.fakeid) + '」，编号 ' + found.fakeid + '。确认无误后点下面按钮录入。');
  $('#itl-candidates').innerHTML =
    `<button class="itl-candidate" data-fakeid="${esc(found.fakeid)}" data-name="${esc(found.nickname || found.fakeid)}">` +
    `<b>${esc(found.nickname || '（无名称）')}</b><span>${esc(found.fakeid)} · 由文章链接反查</span></button>`;
}

// ── 二期 A：推荐订阅 ────────────────────────────────────────────────────
function renderRecommendations(data) {
  const items = data.items || [];
  const notes = [`链接 ${data.pending_links || 0} 条待解析`];
  if (data.failed_links) notes.push(`${data.failed_links} 条解析失败`);
  if (data.ignored) notes.push(`已忽略 ${data.ignored} 个`);
  $('#itl-reco-summary').textContent = notes.join(' · ');
  $('#itl-reco').innerHTML = items.length ? items.map(item => {
    // 号名取不到时如实显示，不拿 fakeid 冒充号名。
    const name = item.name_known ? esc(item.nickname)
      : `<span class="itl-unnamed">号名未取到（仅从链接识别）</span>`;
    const sources = (item.sources || [])
      .map(s => `${s.kind === 'intake' ? '回收项' : '知识库'} ${s.count} 条`).join('、');
    return `<div class="itl-reco-item"><div class="itl-reco-info"><b>${name}</b>` +
      `<p class="itl-reco-meta">出现 ${item.count} 次 · ${esc(item.fakeid)}` +
      (sources ? ` · 来自 ${sources}` : '') + `</p></div>` +
      `<button class="button primary" data-reco="subscribe" data-fakeid="${esc(item.fakeid)}">订阅</button>` +
      `<button class="button subtle" data-reco="ignore" data-fakeid="${esc(item.fakeid)}">忽略</button></div>`;
  }).join('') : '<p class="itl-empty">暂无推荐。点「扫描回收站与知识库」看看有哪些公众号。</p>';
}

async function loadRecommendations() {
  await guard(async () => { renderRecommendations(await api('/api/intel/recommendations')); });
}

async function scan() {
  const button = $('#itl-scan'); button.disabled = true;
  button.textContent = '扫描中…';
  await guard(async () => {
    const result = await api('/api/intel/recommendations/scan', {});
    const s = result.scan || {}, r = result.resolve || {};
    renderRecommendations(result.listing);
    const stopped = r.stopped ? `；${r.stopped}` : '';
    say(`扫到链接 ${s.links} 条：其中 ${s.skipped} 条不是文章页已跳过，${s.known} 条直接从链接认出公众号，` +
      `${s.needs_fetch} 条需要读页面。本轮解析 ${r.attempted || 0} 条，成功 ${r.resolved || 0} 条，` +
      `失败 ${r.failed || 0} 条${stopped}`);
  });
  button.disabled = false; button.textContent = '扫描回收站与知识库';
}

// ── 二期 B：定时订阅 ────────────────────────────────────────────────────
let schedule = null, scheduleBusy = false;
async function loadSchedule() {
  await guard(async () => {
    const data = await api('/api/schedules');
    schedule = (data.tasks || []).find(t => t.kind === 'intel_wechat_mp') || null;
    const toggle = $('#itl-sched-toggle');
    if (!schedule) {
      $('#itl-sched-state').textContent = '未登记「公众号订阅（每日抓最新）」。请到定时任务页新建，或重启工作台后重试。';
      toggle.disabled = true; return;
    }
    // 读到合法任务就要把开关恢复成可用：之前因「未登记」禁用过，不能一直禁用下去。
    // 请求进行中的禁用由 scheduleBusy 单独管，不跟这里的任务存在性混在一起。
    toggle.disabled = scheduleBusy;
    const when = schedule.frequency === 'daily' ? `每天 ${schedule.at}` : '频率无效';
    const count = accounts.length;
    $('#itl-sched-state').innerHTML = schedule.enabled
      ? `<span class="itl-sched-state-on">已开启</span> · ${esc(when)} · 订阅 ${count} 个`
      : `<span>未开启</span> · ${esc(when)} · 订阅 ${count} 个`;
    toggle.textContent = schedule.enabled ? '停用' : '开启';
    toggle.dataset.action = schedule.enabled ? 'pause' : 'resume';
  });
}

async function toggleSchedule() {
  if (!schedule || scheduleBusy) return;
  const toggle = $('#itl-sched-toggle'), action = toggle.dataset.action;
  scheduleBusy = true; toggle.disabled = true; toggle.textContent = '处理中…';
  try {
    await guard(async () => {
      await api('/api/schedules/' + schedule.id + '/' + action, {});
      say(action === 'resume' ? '已开启，之后每天自动抓最新。' : '已停用，不再自动抓取。');
      await loadSchedule();
    });
  } finally {
    scheduleBusy = false;
    // 请求失败时 loadSchedule 不会跑，这里按当前 schedule 把开关与文字恢复回去。
    toggle.disabled = !schedule;
    if (schedule) toggle.textContent = schedule.enabled ? '停用' : '开启';
  }
}

let platformView = { byId: {} };
let selectedPlatform = 'wechat_mp';
let platformRefreshTimer = 0, platformLoadGeneration = 0;

function platformBadge(meta, row) {
  if (meta.state === 'planned') return '规划中';
  if (row?.available === false) return '未就绪';
  if (row && row.available == null && row.refreshing) return '核对中';
  if (row?.available !== true) return '待核对';
  return row.stale ? '已接通（旧结果）' : '已接通';
}

function platformCheckedTime(row) {
  if (!row || row.available !== true || !row.stale || !row.checked_at) return '';
  const date = new Date(row.checked_at);
  return Number.isNaN(date.getTime()) ? '' : '上次核对：' + date.toLocaleString();
}

function clearPlatformRefresh() {
  clearTimeout(platformRefreshTimer);
  platformRefreshTimer = 0;
}

function stopPlatformRefresh() {
  clearPlatformRefresh();
  platformLoadGeneration += 1;
}
window.addEventListener('pagehide', stopPlatformRefresh);
document.addEventListener('visibilitychange', () => {
  if (document.visibilityState === 'hidden') stopPlatformRefresh();
});

function renderPlatforms(listing, status) {
  clearPlatformRefresh();
  const board = $('#itl-platforms');
  if (!board) return;
  const sections = listing && listing.sections;
  if (!Array.isArray(sections) || !sections.length) {
    board.innerHTML = '<p class="itl-note">平台清单还没有返回。公众号的录入和抓取仍可在下面使用。</p>';
    return;
  }
  const byId = {};
  for (const row of (status && status.platforms) || []) byId[row.id] = row;
  platformView = { sections, byId };
  board.innerHTML = sections.map(section => {
    const cards = (section.platforms || []).map(item => {
      const row = byId[item.id] || {};
      const selected = item.id === selectedPlatform ? ' selected' : '';
      return `<button type="button" class="itl-platform${selected}" data-platform="${esc(item.id)}" data-state="${esc(item.state)}">` +
        `<b>${esc(item.label)}</b><span class="itl-platform-state">${esc(platformBadge(item, byId[item.id]))}</span>` +
        `<small>${esc([row.detail || item.note || '', platformCheckedTime(row)].filter(Boolean).join(' · '))}</small></button>`;
    }).join('');
    return `<div class="itl-platform-group"><h2>${esc(section.name)}</h2><div class="itl-platform-row">${cards}</div></div>`;
  }).join('');
}

function platformMeta(id) {
  for (const section of platformView.sections || []) {
    const found = (section.platforms || []).find(item => item.id === id);
    if (found) return found;
  }
  return null;
}

function platformFacts(row) {
  const extra = row.extra || {};
  const lines = [];
  if (row.count != null) lines.push(`计数 ${row.count}`);
  if (extra.last_synced) lines.push(`缓存最近同步：${extra.last_synced}`);
  const names = (extra.sources || []).map(item => item.name || item.repository || item.account || item.id).filter(Boolean);
  if (names.length) lines.push('来源：' + names.join('、'));
  const clues = (extra.clues || []).filter(item => item.total != null);
  if (clues.length) lines.push('线索 ' + clues.map(item => `${item.id} ${item.total} 条`).join('，'));
  const tasks = extra.tasks || [];
  if (tasks.length) {
    lines.push(tasks.map(task => `${task.enabled ? '定时已启用' : '定时未启用'}${task.running ? '，正在运行' : ''}`).join('；'));
  }
  return lines;
}

function selectPlatform(id) {
  selectedPlatform = id;
  document.querySelectorAll('#itl-platforms [data-platform]').forEach(node => {
    node.classList.toggle('selected', node.dataset.platform === id);
  });
  const meta = platformMeta(id) || { id, label: id, state: '', note: '', home: '' };
  const row = platformView.byId[id] || {};
  const mp = $('#itl-mp');
  const delegated = $('#itl-delegated');
  if (id === 'wechat_mp') {
    if (mp) mp.hidden = false;
    if (delegated) delegated.hidden = true;
    return;
  }
  if (mp) mp.hidden = true;
  if (!delegated) return;
  delegated.hidden = false;
  const planned = meta.state === 'planned';
  const home = meta.home || (row.extra && row.extra.home) || '';
  const link = !planned && home ? `<p><a class="itl-link" href="${esc(home)}">打开原入口 ↗</a></p>` : '';
  const note = planned
    ? `${esc(meta.note || '规划中，尚未接通。')} 这里不会发起抓取。`
    : '抓最新、抓历史和抓正文仍由原来的入口负责。这个页面只显示同一份状态，不会代跑，也不会新建定时任务。';
  const facts = platformFacts(row).map(line => `<p class="itl-platform-facts">${esc(line)}</p>`).join('');
  delegated.innerHTML = `<header class="itl-card-head"><strong>${esc(meta.label || row.label || id)}</strong>` +
    `<span class="itl-muted">${esc(platformBadge(meta, platformView.byId[id]))}</span></header>` +
    `<p class="itl-platform-facts">${esc(row.detail || meta.note || '')}</p>${facts}` +
    (platformCheckedTime(row) ? `<p class="itl-platform-facts">${esc(platformCheckedTime(row))}</p>` : '') +
    `<p class="itl-note">${note}</p>${link}`;
}

function schedulePlatformRefresh(listing, status, generation, attempts = 0) {
  clearPlatformRefresh();
  if (attempts >= 3 || !(status?.platforms || []).some(row => row.refreshing)) return;
  platformRefreshTimer = setTimeout(async () => {
    platformRefreshTimer = 0;
    if (generation !== platformLoadGeneration || document.visibilityState === 'hidden') return;
    try {
      const next = await api('/api/collect/status');
      if (generation !== platformLoadGeneration || document.visibilityState === 'hidden') return;
      renderPlatforms(listing, next);
      selectPlatform(selectedPlatform);
      schedulePlatformRefresh(listing, next, generation, attempts + 1);
    } catch (error) {
      if (generation === platformLoadGeneration && document.visibilityState !== 'hidden') {
        schedulePlatformRefresh(listing, status, generation, attempts + 1);
      }
    }
  }, 2000);
}

async function loadPlatforms() {
  clearPlatformRefresh();
  const generation = ++platformLoadGeneration;
  const board = $('#itl-platforms');
  if (!board) return;
  try {
    const listing = await api('/api/collect/platforms');
    const status = await api('/api/collect/status');
    if (generation !== platformLoadGeneration) return;
    renderPlatforms(listing, status);
    selectPlatform(selectedPlatform);
    schedulePlatformRefresh(listing, status, generation);
  } catch (error) {
    if (generation !== platformLoadGeneration) return;
    board.innerHTML = '<p class="itl-note">平台清单还没有返回。公众号的录入和抓取仍可在下面使用。</p>';
  }
}

document.addEventListener('click', event => {
  const platformButton = event.target.closest('#itl-platforms [data-platform]');
  if (platformButton) {
    selectPlatform(platformButton.dataset.platform);
    return;
  }
  const reco = event.target.closest('[data-reco]');
  if (reco) {
    const verb = reco.dataset.reco, fakeid = reco.dataset.fakeid;
    reco.disabled = true;
    return guard(async () => {
      const done = await api('/api/intel/recommendations/' + verb, { fakeid });
      if (verb === 'subscribe') {
        say('已订阅「' + (done.name || fakeid) + '」。它不会再出现在推荐里。');
        await loadAccounts(); await loadSchedule();
      } else {
        say('已忽略，这个号以后不再推荐（数据仍在，只是不再打扰你）。');
      }
      reco.disabled = false; await loadRecommendations();
    });
  }
  const candidate = event.target.closest('.itl-candidate');
  if (candidate) return subscribe(candidate.dataset.fakeid, candidate.dataset.name);
  const nav = event.target.closest('#itl-accounts button');
  if (nav) { active = nav.dataset.fakeid; accountNav(); showRunPanel(accounts.find(a => a.fakeid === active)); loadArticles(); return; }
  const action = event.target.closest('button[data-action]');
  if (!action) return;
  const id = action.dataset.id;
  if (action.dataset.action === 'push') {
    action.disabled = true;
    return guard(async () => {
      const done = await api('/api/intel/articles/' + id + '/intake', {});
      say('已送入回收站，来源标记 ' + done.provenance + '；知识库尚未入库。');
      action.disabled = false; await loadArticles();
    });
  }
  if (action.dataset.action === 'detail') {
    return guard(async () => {
      const row = await api('/api/intel/articles/' + id);
      const view = window.open('', '_blank');
      if (!view) { say('浏览器拦截了新窗口，请允许弹出后重试', 'bad'); return; }
      view.document.write('<meta charset="utf-8"><title>' + esc(row.title || '正文') + '</title>' +
        '<body style="font:16px/2 -apple-system,sans-serif;max-width:720px;margin:40px auto;padding:0 20px;white-space:pre-wrap">' +
        esc(row.title) + '<hr>' + esc(row.body || '（正文未抓取）') + '</body>');
      view.document.close();
    });
  }
});

$('#itl-search').addEventListener('click', search);
$('#itl-scan').addEventListener('click', scan);
$('#itl-sched-toggle').addEventListener('click', toggleSchedule);
$('#itl-query').addEventListener('keydown', e => { if (e.key === 'Enter') search(); });
$('#itl-by-link').addEventListener('click', byLink);
$('#itl-discover-album').addEventListener('click', discoverAlbum);
$('#itl-run-latest').addEventListener('click', () => startRun('latest'));
$('#itl-run-history').addEventListener('click', () => startRun('history'));
$('#itl-run-discovery').addEventListener('click', () => startRun('discovery'));
$('#itl-run-close').addEventListener('click', () => { $('#itl-run').hidden = true; });
$('#itl-filter-account').checked = onlyActive;
$('#itl-filter-account').addEventListener('change', e => { onlyActive = e.target.checked; loadArticles(); });

(async () => { await loadAccounts(); await refreshQuota(); await loadRecommendations(); await loadSchedule();
  showRunPanel(accounts.find(a => a.fakeid === active));
  try { await loadPlatforms(); } catch (error) { /* 平台栏失败不挡住公众号 */ }
})();
})();
