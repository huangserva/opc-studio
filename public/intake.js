'use strict';
(() => {
const $ = s => document.querySelector(s), esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const kinds = { link: '链接', text: '文字', image: '图片', video: '视频' };
// 平台徽标与筛选：后端在 meta.source_platform / source_label 里打标，前端只读不猜。
const platformNames = { xiaohongshu: '小红书', douyin: '抖音', channels: '视频号' };
const platformId = item => item.source_platform || (item.meta && item.meta.source_platform) || '';
const platformLabel = item => item.source_label || platformNames[platformId(item)] || '';
const clock = v => v ? new Date(v).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', hour12: false }) : '时间未知';
const bytes = v => Number.isFinite(v) ? (v >= 1048576 ? (v / 1048576).toFixed(1) + ' MB' : v >= 1024 ? (v / 1024).toFixed(1) + ' KB' : v + ' B') : '大小未知';
const key = () => (crypto.randomUUID ? crypto.randomUUID() : 'x'.repeat(32)).replaceAll('-', '');
let items = [], kind = '', platform = '', range = '', from = '', to = '', busy = false, analysisReady = null, pendingFile = null, uploading = null;
// 「重新解析 / 重新识别」：同一时刻只发一条。resolving 记正在跑的那条 id，
// retryRound 记每条本次请求的「发出」与「返回」时刻（只在本页内存里，刷新列表不会丢）。
// 完成时间只能取本轮的终态时刻：非终态与未确认的结果不写时间（M1）。
let resolving = null;
let busyWhat = '';
const retryRound = new Map();
// 批量删除：勾选集合 + 一次最多 50 条（与后端 MAX_BATCH_DELETE 同口径）。
const MAX_BATCH = 50, DAY = 86400000;
let selected = new Set();
// 前后端不一致时（旧进程不认识分析接口），不能把「操作不存在」这类原始错误丢给用户。
const staleService = '工作台服务还是旧版本，需要重启后才能分析。请重启工作台，再刷新本页。';
const staleNote = '分析服务未就绪：工作台服务还是旧版本，重启后才能分析。';
function noteService() {
  const note = $('#in-service');
  if (!note) return;
  note.hidden = analysisReady !== false;
  note.textContent = analysisReady === false ? staleNote : '';
}
function markServiceStale() {
  if (analysisReady === false) return;
  analysisReady = false;
  noteService();
  render();
}

async function api(url, body, timeoutMs) {
  // 每个请求都有上限；链接读取最长 60 秒，超时就如实说可能还在后台跑。
  // 截止时间覆盖到响应体读完：响应头到了但 r.json() 卡住同样算超时，abort 后走超时回执（M3）。
  const limit = timeoutMs || 20000;
  const controller = typeof AbortController === 'function' ? new AbortController() : null;
  const options = body === undefined ? {} : { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Article-Studio': '1' }, body: JSON.stringify(body) };
  const timeoutNote = '超过 ' + Math.round(limit / 1000) + ' 秒没回话，后台可能仍在处理；点右上角“刷新”看最新结果。';
  let expired = false;
  const timer = setTimeout(() => { expired = true; if (controller) controller.abort(); }, limit);
  try {
    let r;
    try {
      if (controller) options.signal = controller.signal;
      r = await fetch(url, options);
    } catch (e) {
      if (expired || (e && e.name === 'AbortError')) throw new Error(timeoutNote);
      throw e;
    }
    let value = null, unreadable = false;
    try { value = await r.json(); }
    catch (e) {
      if (expired || (e && e.name === 'AbortError')) throw new Error(timeoutNote);
      // 响应体不是合法 JSON：不能吞成空对象当成一次成功回执（M2）。
      unreadable = true;
    }
    if (expired) throw new Error(timeoutNote);
    if (!r.ok) {
      // 404 在本页只意味着后端不认识这个接口（旧版本进程），原始文案对用户没有意义。
      const failure = new Error(r.status === 404 ? staleService
        : (value && value.error) || (unreadable ? '请求失败：服务返回的错误读不出原因' : '请求失败'));
      failure.status = r.status;
      if (r.status === 404) markServiceStale();
      throw failure;
    }
    if (unreadable || value === null || typeof value !== 'object' || Array.isArray(value)) {
      throw new Error('服务回话读不出结果（响应体不是可读的 JSON 回执）；这次结果未确认，请点右上角“刷新”核对。');
    }
    return value;
  } finally { clearTimeout(timer); }
}
function status(message) { $('#in-status').textContent = message || ''; }
function isLink(value) { return /^https?:\/\/[^\s]+$/i.test(value.trim()) && !/\s/.test(value.trim()); }
function tone(item) {
  if (item.outcome === 'failed') return 'failed';
  if (item.outcome === 'duplicate') return 'duplicate';
  if (item.outcome === 'pending') return 'pending';
  if (bodyVerdict(item).kind === 'unconfirmed') return 'pending';   // 未确认的结果不套成功色
  return item.meta && item.meta.fetched ? 'stored' : 'link-only';
}
function mediaKind(item) {
  // 附件类型看 media_kind；旧后端没给这个字段时退回 kind，保证旧进程也能正常显示。
  return item.media_kind || (item.file_path && ['image', 'video'].includes(item.kind) ? item.kind : '');
}
function displayKind(item) {
  // 类型口径与后端一致：链接/文字带附件时算「组合」，每条只归一个类型。
  return item.display_kind || (mediaKind(item) && ['link', 'text'].includes(item.kind) ? 'combo' : item.kind);
}
const kindLabel = value => value === 'combo' ? '组合' : (kinds[value] || value || '未分类');
// 失败归类：标记与后端 intake_store.fetch_receipt / collect_links 的人话口径一致。
// 顶部回执只说归类原因，不把原始错误全文（可能带命令行文案）丢给用户；条目里仍显示后端完整状态。
const FETCH_FAILURES = [
  [['未就绪', '未完成查询', '未返回有效结果', '无法启动联网服务'], '联网服务未就绪（remio 未运行）'],
  [['响应超时'], '联网服务响应超时'],
  [['没有返回可读正文'], '页面没有返回可读正文'],
  [['空壳文字'], '页面只返回空壳文字，没有作品正文'],
  [['登录墙'], '页面返回登录墙文字，没有作品正文'],
  [['需要登录浏览器', '没有公开的抓取入口', 'JS 渲染'], '这家平台的正文本机抓不到（要登录或 JS 渲染）'],
];
function failureReason(item) {
  const text = String(item.error || (item.meta || {}).fetch_error || '').trim();
  if (!text) return '原因未记录';
  for (const [marks, label] of FETCH_FAILURES) if (marks.some(mark => text.includes(mark))) return label;
  return '其他抓取失败';
}
// 后端只产这几值；读到的不在表里就按「结果未确认」处理，不猜成成功（M2）。
const RECOGNITION_STATES = ['queued', 'running', 'succeeded', 'failed', 'unavailable', 'interrupted', 'no_audio'];
const UNCONFIRMED = '结果未确认：服务这次回话读不出可确认的解析结果，请点右上角“刷新”或再试一次。';
function isItem(value) {
  // 可确认的回收项回执：对象本体带 id / status / outcome。空对象、缺字段一律未确认。
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value)
    && typeof value.id === 'string' && Boolean(value.id)
    && typeof value.status === 'string' && Boolean(value.status)
    && typeof value.outcome === 'string' && Boolean(value.outcome);
}
function bodyVerdict(item) {
  // 接口 200 不等于业务成功：结果只看 status / error / outcome，默认值不是成功（M2）。
  // 后端 _outcome 先判 duplicate，所以「抓取失败但同地址已有正文」会带 outcome=duplicate；
  // 这里先认 status=failed / 有 error / outcome=failed，失败不会被 duplicate 盖掉。
  if (!isItem(item)) return { kind: 'unconfirmed' };
  if (item.status === 'failed' || item.outcome === 'failed' || item.error) return { kind: 'failed', reason: failureReason(item) };
  if (item.status === 'pending' || item.outcome === 'pending') return { kind: 'pending' };
  if (item.status === 'resolved' && item.outcome === 'duplicate') return { kind: 'duplicate' };
  if (item.status === 'resolved' && item.outcome === 'stored') return { kind: (item.meta || {}).duplicate ? 'duplicate' : 'stored' };
  // 走到这里的就是未知 status/outcome 或自相矛盾的组合：一律未确认，默认值不是成功（M2）。
  return { kind: 'unconfirmed' };
}
function recognitionVerdict(item) {
  // 附件识别是另一层，不能和正文入库混成一句「已完成」。
  // 没附件时不适用（recognition={} 正常）；有附件但回执为空或枚举不认识 → 识别结果未确认。
  if (!mediaKind(item)) return { kind: 'none' };
  const r = item.recognition || {};
  if (!r.status) return { kind: 'unconfirmed', detail: {} };
  if (!RECOGNITION_STATES.includes(r.status)) return { kind: 'unconfirmed', detail: r };
  if (r.status === 'queued' || r.status === 'running') return { kind: 'working', detail: r };
  return { kind: r.status, detail: r };
}
const recognitionReason = r => String((r || {}).error || '').trim() || '原因未记录';
function retryAction(item) {
  // 按钮文案说清这次实际会做什么：只有正文确认可重识别时才说「重新识别附件」（L1）。
  const v = bodyVerdict(item);
  if ((v.kind === 'stored' || v.kind === 'duplicate') && mediaKind(item)) {
    return ['link', 'text'].includes(item.kind) ? '重新识别附件' : '重新识别';
  }
  return '重新解析';
}
function canRetry(item) {
  // 能不能重试按 status / error 判，不只看 outcome：失败链接即使被标成 duplicate 也要留重试入口（M2）。
  // 判定为失败（哪怕只有 outcome=failed）或结果未确认，同样不能把入口藏起来。
  const v = bodyVerdict(item);
  return ['failed', 'pending'].includes(item.status) || Boolean(item.error) || Boolean(mediaKind(item))
    || v.kind === 'failed' || v.kind === 'unconfirmed';
}
function retryReceipt(item) {
  // 顶部回执分三层：正文入库、附件识别、能不能确认。只有确认的终态才用「已…」。
  const v = bodyVerdict(item), verb = retryAction(item);
  if (v.kind === 'unconfirmed') return UNCONFIRMED;
  if (v.kind === 'failed') return verb + '仍失败：' + v.reason;
  if (v.kind === 'pending') return verb + '还没完成：这条仍在待解析，结果会自动刷新。';
  const rec = recognitionVerdict(item);
  if (rec.kind === 'working') return '正文已入库，附件识别' + (rec.detail.status === 'queued' ? '已提交排队' : '正在进行') + '，还没完成；结果会自动刷新。';
  if (rec.kind === 'failed') return '附件识别失败：' + recognitionReason(rec.detail) + '；正文与原始文件已保留，可再试一次。';
  if (rec.kind === 'unavailable') return '附件暂时识别不了：' + recognitionReason(rec.detail) + '；稍后可重试，这次不算识别成功。';
  if (rec.kind === 'interrupted') return '上次附件识别被中断（工作台重启），这次没跑完，请再试一次。';
  if (rec.kind === 'unconfirmed') return '正文已入库，但附件识别结果未确认：请点右上角“刷新”或再试一次。';
  if (rec.kind === 'no_audio') return '已' + verb + '：视频无音轨，未生成语音转写。';
  return v.kind === 'duplicate' ? '已' + verb + '：这条已存在，未重复导入。' : '已' + verb;
}
function dropReceipt(item) {
  // 首次投喂与「重新解析」共用同一套判定，不各写一份。
  const v = bodyVerdict(item);
  if (v.kind === 'failed') return '抓取失败：' + v.reason;
  if (v.kind === 'unconfirmed') return UNCONFIRMED;
  return item.state || (v.kind === 'duplicate' ? '已存在，未重复导入' : '已存入知识库');
}
// 任何在途操作都要把重试按钮的等待状态画出来；结束时也要重画，不能留着禁用样子。
function startBusy(what) { busy = true; busyWhat = what; render(); }
function endBusy() { busy = false; busyWhat = ''; render(); }
function waitingTitle() {
  if (resolving) {
    const item = items.find(old => old.id === resolving);
    return '上一条还在处理：正在' + (item ? retryAction(item) : '解析') + ' ' + String(resolving).slice(0, 8) + '…，这一条要等它结束再点。';
  }
  return '上一条还在处理：' + (busyWhat || '正在读上一条的结果') + '，这一条要等它结束再点。';
}
function retryButton(item) {
  if (!canRetry(item)) return '';
  const verb = retryAction(item);
  // 请求进行中：被点的那一行显示「解析中…」并禁用，其他行也给出可见的等待状态（M1）。
  if (resolving === item.id) return `<button class="button in-resolving" data-resolve="${esc(item.id)}" type="button" disabled>解析中…</button>`;
  if (busy) return `<button class="button in-waiting" data-resolve="${esc(item.id)}" type="button" disabled title="${esc(waitingTitle())}">${esc(verb)}</button>`;
  // 纯文字带附件不能说成「这条链接」：提示按 kind 区分（L1）。
  const hint = verb === '重新识别附件'
    ? (item.kind === 'text' ? '这段文字的正文已存过：只会重新识别附件内容，不会改动这段文字'
      : '这条链接的正文已存过：只会重新识别附件内容，不会重新抓链接正文')
    : verb === '重新识别' ? '只重新识别这个附件的内容（图片描述 / 视频语音转写）' : '';
  return `<button class="button" data-resolve="${esc(item.id)}" type="button"${hint ? ` title="${esc(hint)}"` : ''}>${esc(verb)}</button>`;
}
const noteLine = (label, at) => `<p class="in-retry-note">${esc(label)} ${clock(at)}</p>`;
function recognitionTime(item, round) {
  // 识别时间只认本轮的 finished_at：早于本次请求发出的是上一轮结果，不能当本次完成时间（M1）。
  const raw = (item.recognition || {}).finished_at || '';
  const at = Date.parse(raw);
  if (!Number.isFinite(at)) return '';
  const started = Date.parse(round.startedAt || '');
  if (Number.isFinite(started) && at < started) return '';
  return raw;
}
function retryNote(item) {
  // 只有能确认的业务终态才写时间；pending、识别排队中、未确认一律不写「本次…完成于」（M1）。
  // 时间来源：正文层用本次请求返回时刻，识别层用本轮的 recognition.finished_at（轮询转终态时同理）。
  const round = retryRound.get(item.id);
  if (!round) return '';
  const v = bodyVerdict(item);
  if (v.kind === 'failed') return noteLine('本次' + retryAction(item) + '失败于', round.returnedAt);
  if (v.kind !== 'stored' && v.kind !== 'duplicate') return '';
  if (mediaKind(item)) {
    const rec = recognitionVerdict(item);
    if (!['succeeded', 'no_audio', 'failed'].includes(rec.kind)) return '';
    const at = recognitionTime(item, round);
    if (!at) return '';
    const label = rec.kind === 'succeeded' ? '本次附件识别完成于'
      : rec.kind === 'no_audio' ? '本次附件识别检查结束于' : '本次附件识别失败于';
    return noteLine(label, at);
  }
  return noteLine('本次' + retryAction(item) + '完成于', round.returnedAt);
}
function shown() {
  // 当前筛选结果：类型 + 平台 + 时间范围。批量删除与「全选」都以这个范围为准。
  const start = rangeStart(), fromAt = dayStart(from), toAt = dayStart(to, true);
  return items.filter(item => {
    if (kind && displayKind(item) !== kind) return false;
    if (platform && platformId(item) !== platform) return false;
    if (!start && !fromAt && !toAt) return true;
    const at = Date.parse(item.received_at);
    if (!Number.isFinite(at)) return false;   // 时间未知的条目：设了时间条件就不算命中
    return (!start || at >= start) && (!fromAt || at >= fromAt) && (!toAt || at <= toAt);
  });
}
function rangeStart() {
  if (range === '1') { const day = new Date(); day.setHours(0, 0, 0, 0); return day.getTime(); }
  return range ? Date.now() - Number(range) * DAY : null;
}
function dayStart(value, endOfDay) {
  if (!value) return null;
  const at = Date.parse(value + 'T' + (endOfDay ? '23:59:59.999' : '00:00:00'));
  return Number.isFinite(at) ? at : null;
}
function source(item) {
  const file = item.file || {};
  const size = bytes(file.bytes);
  const shape = mediaKind(item) === 'image' ? `，${file.width}×${file.height}` : file.duration_seconds ? `，${Math.round(file.duration_seconds)} 秒` : '';
  const local = `本机文件 · ${esc(file.name || '未命名')}（${esc(file.format || '未知格式')}${shape}，${esc(size)}）`;
  if (item.kind === 'link') return item.url
    ? `<a href="${esc(item.url)}" target="_blank" rel="noopener noreferrer">${esc(item.url)} ↗</a>${item.file_path ? `<br>${local}` : ''}`
    : (item.file_path ? local : '链接未记录');
  if (item.kind === 'text') return `本机文字 · ${item.text_chars || 0} 字${item.file_path ? `<br>${local}` : ''}`;
  return local;
}
function detail(item) {
  const meta = item.meta || {};
  const parts = [];
  if (item.kind === 'link' && meta.fetched) parts.push(`正文 ${Number(meta.total_chars || 0).toLocaleString('zh-CN')} 字 · 抓取于 ${clock(meta.fetched_at)}${meta.pages && meta.pages.length > 1 ? ` · 分 ${meta.pages.length} 页读取` : ''}`);
  if (item.kind === 'link' && meta.fetched === false) parts.push('未抓取到正文');
  if (meta.source_short_link) parts.push('短链未解析：本机不联网解短链，作品编号待打开后补');
  if (item.content_scope) parts.push(esc(item.content_scope));
  if (meta.title_pending) parts.push('标题待补');
  return parts.join(' · ');
}
function recognition(item) {
  // 识别状态逐一如实说明：排队/进行中没有完成，unavailable 不是抓取失败，interrupted 是上次被中断，
  // no_audio 是确认无音轨（没有转写结果），failed 只说附件识别失败、正文与文件仍在。
  const r=item.recognition || {};
  if (!r.status) return '';
  if (['running','queued'].includes(r.status)) return '<p class="in-recognition">媒体识别'+(r.status==='queued'?'排队中':'进行中')+'…还没完成</p>';
  if (r.status==='succeeded') return `<section class="in-recognition"><strong>${esc(r.label || 'AI 识别，未核实')}</strong><p>${esc(r.text || '')}</p><small>${esc(r.model || '')} · ${esc(r.scope || '')}</small></section>`;
  if (r.status==='no_audio') return `<p class="in-recognition">${esc(r.scope || '视频无音轨；未生成语音转写')}</p>`;
  if (r.status==='interrupted') return `<p class="in-recognition">上次识别被中断（工作台重启），这次没跑完，可点下方按钮再试一次${r.error ? '：' + esc(r.error) : ''}</p>`;
  if (r.status==='unavailable') return `<p class="in-recognition">暂时识别不了附件：${esc(recognitionReason(r))}；稍后可重试，这不算识别成功</p>`;
  if (r.status==='failed') return `<p class="in-recognition">附件识别失败：${esc(recognitionReason(r))}；正文与原始文件已保留，可重试</p>`;
  return `<p class="in-recognition">附件识别结果未确认：${esc(r.status)} 不是已知状态，请刷新或再试一次</p>`;
}
// ── 写作入口（方案 A）────────────────────────────────────────────────────────
// 只带标识进知识库：document / intake / angle / analysis_at。正文、摘要、角度文字、
// 收藏理由都不进 URL，也不写 localStorage。点击本身不建资料夹、不建底稿、不跑模型。
const analysisVersion = a => String((a && (a.at || a.fingerprint)) || '');
function knowledgeViewUrl(item) {
  return item.knowledge_url || ('/knowledge.html?document=' + encodeURIComponent(item.document_id));
}
function knowledgeWriteUrl(item, angleIndex) {
  const params = new URLSearchParams({ document: String(item.document_id), intake: String(item.id) });
  if (Number.isInteger(angleIndex) && angleIndex >= 0) {
    params.set('angle', String(angleIndex));
    const version = analysisVersion(item.analysis);
    if (version) params.set('analysis_at', version);
  }
  return '/knowledge.html?' + params.toString();
}
function storedBody(item) {
  // 「这篇有正文可写」的口径与 tone() 一致：链接只认确认抓到的正文。
  if (!item.document_id) return false;
  return item.kind === 'link' ? (item.meta || {}).fetched === true : true;
}
const linkOnly = item => item.kind === 'link' && Boolean(item.document_id) && !storedBody(item);
function angleEntry(item, index) {
  // 每个角度旁一个入口；没正文（仅链接收藏）不给写作入口。
  if (!storedBody(item)) return '';
  return `<a class="button in-use-angle" href="${esc(knowledgeWriteUrl(item, index))}" title="打开这篇原文，带入这个角度的写作目标；片段要你自己选">用这个角度写</a>`;
}
function writingEntry(item) {
  if (!item.document_id) return '';            // 连正文都没存：不给任何写作入口
  const verdict = bodyVerdict(item);
  const openOriginal = item.url ? `<a class="button" href="${esc(item.url)}" target="_blank" rel="noopener noreferrer">打开原链接 ↗</a>` : '';
  if (verdict.kind === 'failed') {
    // 失败优先：失败与重试在上面，这里只留一个次要入口，不用「已收录」盖住错误。
    return `<section class="in-writing in-writing-failed"><p>${linkOnly(item) ? '这条链接已收藏，原文正文尚未保存。' : '这篇的正文这次没存好。'}先按上面的原因重试；正文补齐后才能用来整理写作资料。</p>` +
      `<div class="in-writing-actions"><a class="button in-writing-quiet" href="${esc(knowledgeViewUrl(item))}">${linkOnly(item) ? '查看收藏记录' : '查看已有资料'}</a>${openOriginal}</div></section>`;
  }
  if (linkOnly(item)) {
    return '<section class="in-writing in-writing-linkonly"><p>这条链接已收藏，原文正文尚未保存。补齐资料后可以建立写作底稿。</p>' +
      `<div class="in-writing-actions"><a class="button" href="${esc(knowledgeViewUrl(item))}">查看收藏记录</a>${openOriginal}</div></section>`;
  }
  if (verdict.kind === 'pending' || verdict.kind === 'unconfirmed') return '';
  if (verdict.kind === 'duplicate') {
    return '<section class="in-writing in-writing-duplicate"><p>这篇已在知识库，未重复导入。收录后可以用来整理写作资料、参考可写角度，或到卡库确认提示词。</p>' +
      `<div class="in-writing-actions"><a class="button" href="${esc(knowledgeViewUrl(item))}">查看已有资料</a>` +
      `<a class="button in-writing-primary" href="${esc(knowledgeWriteUrl(item))}" title="打开这篇原文，自己选片段后再建底稿">用这篇资料写</a>` +
      `<a class="button" href="${esc(knowledgeWriteUrl(item))}" title="打开这篇原文，选中要引用的片段再保存资料夹">选片段建资料夹</a></div></section>`;
  }
  const angles = Array.isArray((item.analysis || {}).angles) ? item.analysis.angles : [];
  if ((item.analysis || {}).status === 'done' && angles.length) return '';   // 每个角度旁已有「用这个角度写」
  return '<section class="in-writing"><p>这篇正文已存入知识库，可以用来整理写作资料。</p>' +
    `<div class="in-writing-actions"><a class="button in-writing-primary" href="${esc(knowledgeWriteUrl(item))}" title="打开这篇原文，自己选片段后再建底稿">用这篇资料写</a></div></section>`;
}
function analysis(item) {
  const a = item.analysis || {};
  if (['queued', 'running'].includes(a.status)) {
    // 识别一直不出来时也要有逃生口：可以现在就按理由/链接/文件名分析一次（走同一个 force 接口）。
    const now = a.status === 'queued' && a.error === '等识别完成再分析' && analysisReady !== false
      ? `<button class="button in-analyze-now" data-analyze-now="${esc(item.id)}" type="button">现在就按理由/链接分析</button>`
      : '';
    return `<p class="in-analysis in-analysis-wait">${a.status === 'queued' ? (a.error ? esc(a.error) + '…' : '分析排队中…') : '正在分析…'}<small>摘要要点、为什么值得收、可写角度与相关条目</small>${now}</p>`;
  }
  if (a.status === 'failed') {
    // 失败要说原因；服务未就绪时不误导用户去点一个后端根本不存在的接口。
    return `<p class="in-analysis in-analysis-failed">分析未完成：${esc(a.error || '原因未记录')}${analysisReady === false ? '；分析服务未就绪，重启工作台后才能重新分析。' : '，可点下方“重新分析”。'}</p>`;
  }
  if (a.status !== 'done') {
    if (!item.document_id) return '';
    if (analysisReady === false) return `<p class="in-analysis in-analysis-wait">分析服务未就绪<small>${esc(staleNote)}</small></p>`;
    return '<p class="in-analysis in-analysis-wait">还没开始分析<small>点下方“重新分析”会排队一次；模型正忙时如实排队，不重复占用推理服务。</small></p>';
  }
  const points = (a.points || []).length ? `<ul>${a.points.map(point => `<li>${esc(point)}</li>`).join('')}</ul>` : '<p class="in-analysis-none">模型未给出要点</p>';
  const angles = (a.angles || []).length ? `<ul>${a.angles.map((angle, index) => `<li><strong>${angle.title ? esc(angle.title) : '未命名角度'}</strong>${angle.how ? `<span>${esc(angle.how)}</span>` : ''}${angleEntry(item, index)}</li>`).join('')}</ul>` : '<p class="in-analysis-none">模型未给出可写角度</p>';
  const related = (a.related || []).length ? `<ul>${a.related.map(entry => `<li><a href="${esc(entry.url)}">${esc(entry.title || '未命名条目')} ↗</a>${entry.excerpt ? `<span>${esc(entry.excerpt)}</span>` : ''}</li>`).join('')}</ul>` : '<p class="in-analysis-none">知识库里没找到相关条目</p>';
  const cards = a.cards ? `<a class="in-analysis-cards" href="/cards.html">已生成 ${Number(a.cards).toLocaleString('zh-CN')} 张候选提示词卡 → 去卡库确认</a>` :
    (a.cards_error ? `<span class="in-analysis-note">提示词卡这一段没完成：${esc(a.cards_error)}；定时提炼会重试</span>` : '');
  const inherited = a.inherited_from ? '<span class="in-analysis-note">这条与已收藏的同源，沿用已有分析，没再调模型</span>' : '';
  const partial = (a.parse_notes || []).length ? `<span class="in-analysis-note">${esc(a.parse_notes.join('；'))}</span>` : '';
  const note = a.cards_error && a.cards ? `<span class="in-analysis-note">${esc(a.cards_error)}</span>` : '';
  return `<section class="in-analysis"><header><strong>即时分析</strong><small>AI 分析，不是原文${a.at ? ' · ' + clock(a.at) : ''}${a.model ? ' · ' + esc(a.model) : ''}</small></header>` +
    `<div class="in-analysis-block"><h4>摘要与要点</h4><p>${esc(a.summary || '')}</p>${points}</div>` +
    `<div class="in-analysis-block"><h4>为什么值得收</h4>${a.why ? `<p>${esc(a.why)}</p>` : '<p class="in-analysis-none">模型未给出为什么值得收</p>'}</div>` +
    `<div class="in-analysis-block"><h4>可写角度</h4>${angles}</div>` +
    `<div class="in-analysis-block"><h4>知识库里的相关条目</h4>${related}</div>` +
    `<footer>${cards}${inherited}${partial}${note}</footer></section>`;
}
function platformBadge(item) {
  const id = platformId(item);
  if (!id) return '';
  const short = item.meta && item.meta.source_short_link ? ' · 短链未解析' : '';
  return `<span class="in-platform in-platform-${esc(id)}">${esc(platformLabel(item) || '平台')}${esc(short)}</span>`;
}
function row(item) {
  const knowledge = item.document_id ? `<a class="button" href="${esc(item.knowledge_url)}">在知识库查看 ↗</a>` : '';
  const retry = retryButton(item);
  const reanalyze = analysisReady !== false && item.document_id && !['queued', 'running'].includes((item.analysis || {}).status) ? `<button class="button" data-analyze="${esc(item.id)}">重新分析</button>` : '';
  const remove = `<button class="button in-delete" data-delete="${esc(item.id)}">删除</button>`;
  const check = `<input class="in-check" type="checkbox" data-check="${esc(item.id)}" ${selected.has(item.id) ? 'checked' : ''} aria-label="选择这条回收项">`;
  return `<article class="in-item in-${esc(item.kind)} in-${tone(item)}" data-id="${esc(item.id)}"><header>${check}<span class="in-time">收到 ${clock(item.received_at)}</span><span class="in-kind">${esc(kinds[item.kind] || item.kind)}</span>${platformBadge(item)}<span class="in-state">${esc(item.state)}</span></header><p class="in-source">${source(item)}</p>${item.reason ? `<p class="in-why">我为什么收它：${esc(item.reason)}</p>` : '<p class="in-why in-why-empty">未填写收藏理由</p>'}<p class="in-detail">${detail(item)}</p>${recognition(item)}${analysis(item)}${retryNote(item)}<footer>${knowledge}${retry}${reanalyze}${remove}</footer>${writingEntry(item)}</article>`;
}
function render() {
  const visible = shown();
  $('#in-count').textContent = `${visible.length} 条回收项`;
  $('#in-list').innerHTML = visible.length ? visible.map(row).join('') : '<p class="in-empty">还没有回收项。粘贴一个链接、写一段想法，或拖一个文件进来。</p>';
  for (const button of $('#in-views').querySelectorAll('[data-kind]')) button.classList.toggle('selected', button.dataset.kind === kind);
  for (const button of $('#in-platforms').querySelectorAll('[data-platform]')) button.classList.toggle('selected', button.dataset.platform === platform);
  renderBatch(visible);
}
function renderBatch(visible) {
  const bar = $('#in-batch');
  if (!bar) return;
  const picked = visible.filter(item => selected.has(item.id)).length;
  bar.hidden = !visible.length;
  const all = $('#in-all');
  all.checked = picked > 0 && picked === visible.length;
  all.indeterminate = picked > 0 && picked < visible.length;
  $('#in-batch-count').textContent = `已选 ${picked} 条`;
  const outside = selected.size - picked;
  const note = $('#in-batch-note');
  if (picked > MAX_BATCH) note.textContent = `一次最多删 ${MAX_BATCH} 条，请减少选择（当前 ${picked} 条）`;
  else if (outside > 0) note.textContent = `另有 ${outside} 条已选但不在当前筛选结果里，不会被删`;
  else note.textContent = picked ? '确认框会先列出将删什么' : '勾选后可以批量删除';
  $('#in-batch-delete').disabled = !picked;
}
function remember(item) { items = [item, ...items.filter(old => old.id !== item.id)]; }
async function probeAnalysis() {
  // 初次加载先探测后端是否支持分析接口：404 = 旧版本，页面就不再给「点重新分析」这类误导文案。
  try {
    const data = await api('/api/intake/analysis-status', undefined, 5000);
    analysisReady = !!data && data.supported === true;
  } catch (e) {
    if (!e || e.status !== 404) analysisReady = null;  // 探测本身没给出答案：不谎报，保留原有入口
  }
  noteService();
}
async function load() {
  const data = await api('/api/intake');
  items = data.items || [];
  const live = new Set(items.map(item => item.id));
  selected = new Set([...selected].filter(id => live.has(id)));   // 已不在列表的勾选不留悬空
  render();
}
async function finish(saved) {
  // 回读不出可确认的回收项（空对象、缺 id/status/outcome）就不改列表、不谎称完成（M2）。
  if (!isItem(saved)) { status(UNCONFIRMED); return null; }
  remember(saved); render();
  if (saved.status === 'resolved') return saved;
  status(saved.kind === 'link' ? '正在读取链接…' : '正在入库…');
  const done = await api('/api/intake/' + encodeURIComponent(saved.id) + '/resolve', {}, 60000);
  if (!isItem(done) || done.id !== saved.id) { status(UNCONFIRMED); return saved; }
  remember(done); render();
  status(dropReceipt(done));
  return done;
}
async function submit(event) {
  if (event) event.preventDefault();
  if (busy) { status('上一条还在处理，请等它结束；要中止这次上传请点「取消当前上传」。'); return; }
  // 提交用快照：上传期间改的输入或换的文件不会被这次回执清掉。
  const picked = pendingFile, raw = $('#in-input').value, reason = $('#in-reason').value, value = raw.trim();
  if (!value && !picked) { status('先粘贴一个链接、写下一段想法，或选一个图片 / 视频文件。'); return; }
  const payload = { id: key(), reason };
  if (value) { if (isLink(value)) payload.kind = 'link', payload.url = value; else payload.kind = 'text', payload.text = raw; }
  startBusy('正在收下你刚投的那条（上传或入库）');
  try {
    // 选中的文件不单独成一条：先暂存，再和链接/文字 + 理由一起变成同一条回收项。
    if (picked) payload.file_token = (await stage(picked)).token;
    status('正在收下…');
    const saved = await api('/api/intake/drop', payload);
    // 只清这次提交的那份快照；上传期间改过的输入或换过的文件都留着。
    if ($('#in-input').value === raw) $('#in-input').value = '';
    if ($('#in-reason').value === reason) $('#in-reason').value = '';
    if (pendingFile === picked) setPendingFile(null);
    else status('已收下上一条；你后来换的文件还留着，再点一次「收下」提交它。');
    await finish(saved);
  } catch (e) { status(e.message); } finally { endBusy(); }
}
// 前端的支持集必须和后端 intake_store 的 IMAGE_FORMATS（PNG / JPEG / WebP）与
// VIDEO_SUFFIXES（.mp4 .mov .m4v .webm .mkv .avi）对齐：只按 MIME 大类放行会让 GIF /
// SVG / OGG 这类后端必然拒收的格式被静默选中，界面上说「已选」却永远传不上去。
// 名单是一一对应的：后端 IMAGE_FORMATS 的三种格式各对应两种常见后缀（.webp 只有一种，
// 别漏——漏了它，浏览器没给类型（File.type 为空）的合法 WebP 就被自己的提示文案打脸）。
const IMAGE_MIME = { 'image/png': 1, 'image/jpeg': 1, 'image/jpg': 1, 'image/pjpeg': 1, 'image/webp': 1 };
const IMAGE_SUFFIXES = ['.png', '.jpg', '.jpeg', '.webp'];
const VIDEO_MIME = { 'video/mp4': 1, 'video/quicktime': 1, 'video/m4v': 1, 'video/x-m4v': 1,
  'video/webm': 1, 'video/x-matroska': 1, 'video/x-msvideo': 1, 'video/avi': 1 };
const VIDEO_SUFFIXES = ['.mp4', '.mov', '.m4v', '.webm', '.mkv', '.avi'];
function suffixOf(name) {
  const hit = /\.([a-z0-9]+)$/i.exec(String(name || ''));
  return hit ? '.' + hit[1].toLowerCase() : '';
}
// {kind:'image'|'video', why:''} 表示支持；kind 为空表示不支持，why 是给用户看的原因。
function fileSupport(file) {
  const type = String((file && file.type) || '').toLowerCase().split(';')[0].trim();
  const suffix = suffixOf(file && file.name);
  if (IMAGE_MIME[type]) return { kind: 'image', why: '' };
  if (type.startsWith('video/')) {
    if (VIDEO_MIME[type] || VIDEO_SUFFIXES.includes(suffix)) return { kind: 'video', why: '' };
    return { kind: '', why: '视频格式不支持。' };
  }
  if (type.startsWith('image/')) return { kind: '', why: '图片格式不支持。' };
  // File.type 为空（部分系统的拖放与剪贴板文件就是这样）：按受支持后缀判断，不能一律当不支持。
  if (IMAGE_SUFFIXES.includes(suffix)) return { kind: 'image', why: '' };
  if (VIDEO_SUFFIXES.includes(suffix)) return { kind: 'video', why: '' };
  return { kind: '', why: '' };
}
// 拖入的类型/大小规则与后端 intake_store 的 MAX_IMAGE_BYTES / MAX_VIDEO_BYTES 同一组数字。
// 类型不对：当场拒收并说清原因（这类文件根本传不上去，选了也是白选）。
const MAX_IMAGE_BYTES = 10 * 1024 * 1024, MAX_VIDEO_BYTES = 1024 ** 3;
function fileKindProblem(file) {
  const support = fileSupport(file);
  if (support.kind) return '';
  const name = (file && file.name) || '这个文件';
  return `「${name}」${support.why || '不是图片或视频文件。'}这里收 PNG / JPEG / WebP 图片，以及 MP4 / MOV / M4V / WebM / MKV / AVI 视频；别的文件请先在电脑上打开，再把要点粘进来。`;
}
// 超大文件：当场提醒，但不替用户做主 —— 仍可选中，收下时由后端给出最终回执（与既有路径一致）。
function fileSizeWarning(file) {
  const kind = fileSupport(file).kind;
  const limit = kind === 'image' ? MAX_IMAGE_BYTES : kind === 'video' ? MAX_VIDEO_BYTES : 0;
  if (!limit || !(Number(file.size) > limit)) return '';
  return kind === 'image'
    ? `「${file.name}」${bytes(file.size)}，超过图片上限 10 MB：仍可选中，但收下时会被后端拒收，请先压缩或换一张。`
    : `「${file.name}」${bytes(file.size)}，超过视频上限 1 GB：仍可选中，但收下时会被后端拒收，请先裁短或换一段。`;
}
// 拖入、粘贴、选择框三条入口都走这里：类型不合格当场拒收并把原生框与「已选」恢复成一致，
// 超限先提醒；上传中维持既有提示。
function pickFile(file) {
  if (!file) return;
  if (uploading) { setPendingFile(file); return; }        // 上传中：沿用 setPendingFile 里的既有提示
  const problem = fileKindProblem(file);
  if (problem) {
    // 原生 change 入口里，浏览器的 files 已经换成这个不支持的文件（拖入/粘贴时则是拖进来的那个）。
    // 这里把两处一起恢复成上一个有效选择：没有选择就两处都清空。否则原生框显示新文件、
    // 「已选」还是旧文件，点「收下」实际上传旧文件，界面上说的和传上去的不是一回事（M1）。
    setPendingFile(pendingFile || null);
    status((pendingFile ? `仍是原来选中的 ${pendingFile.name}；` : '') + problem);
    return;
  }
  setPendingFile(file);
  status(fileSizeWarning(file));
}
async function stage(file) {
  // kind 用与前端闸门同一套支持集判定：File.type 为空但后缀受支持的文件也要能真的传上去。
  const what = fileSupport(file).kind;
  if (!what) throw new Error('只能投喂图片与视频文件；其他文件请先在电脑上打开，再粘贴要点。');
  const params = new URLSearchParams({ kind: what, name: file.name });
  const controller = typeof AbortController === 'function' ? new AbortController() : null;
  uploading = { file, controller, cancelled: false };
  renderPicked();
  status('正在上传 ' + file.name + '…');
  // 响应头到了不算传完：读响应体与校编号也在同一个 try/finally 里，
  // 取消按钮与 60 秒超时覆盖整个 stage 过程。
  const timer = setTimeout(() => controller && controller.abort(), 60000);
  try {
    const r = await fetch('/api/intake/stage?' + params.toString(), {
      method: 'POST', headers: { 'X-Article-Studio': '1' }, body: file, ...(controller ? { signal: controller.signal } : {})
    });
    let value = {};
    try { value = await r.json(); } catch (e) {
      if (e && (e.name === 'AbortError' || (uploading && uploading.cancelled))) throw e;
      value = {};
    }
    if (!r.ok) throw new Error((value && value.error) || '文件上传失败');
    if (!value.token) throw new Error('文件已上传但未拿到编号，请重试。');
    return value;
  } catch (e) {
    if (uploading && uploading.cancelled) throw new Error('已取消本次上传；选中的文件还在，可以再点一次「收下」。');
    if (e && e.name === 'AbortError') throw new Error('文件上传超过 60 秒没回话，未收下这条；请重试或点右上角“刷新”。');
    throw e;
  } finally { clearTimeout(timer); uploading = null; renderPicked(); }
}
function cancelUpload() {
  if (!uploading) return;
  uploading.cancelled = true;
  if (uploading.controller) { status('正在取消本次上传…'); uploading.controller.abort(); }
  else status('这个浏览器不能中断已发出的上传；等它结束前请不要换文件。');
}
function renderPicked() {
  const box = $('#in-picked'), input = $('#in-file'), button = $('#in-submit');
  // 上传进行中不让人换文件：选择框与提交按钮先锁住，只留「取消当前上传」。
  if (input) input.disabled = Boolean(uploading);
  if (button) button.disabled = Boolean(uploading);
  if (!box) return;
  if (uploading) {
    box.innerHTML = `正在上传 ${esc(uploading.file.name)}…（上传中不能改选）<button class="button in-cancel-upload" data-cancel-upload type="button">取消当前上传</button>`;
    return;
  }
  box.innerHTML = pendingFile
    ? `已选 ${esc(pendingFile.name)}（${bytes(pendingFile.size)}），点「收下」一起提交<button class="button in-discard" data-discard type="button">取消选择</button>`
    : '';
}
function setPendingFile(file) {
  if (uploading) { status('上传中不能换文件：要中止请点「取消当前上传」，换文件请等这次传完。'); return; }
  pendingFile = file;
  setInputFile(file);
  renderPicked();
}
// 原生文件框与右侧「已选 …」必须说同一件事：把文件写回 #in-file.files，浏览器就自己显示文件名。
// 浏览器不支持写回时（老 Safari 等）只藏起原生控件的文字，「选择图片 / 视频」仍可点开选择框。
function setInputFile(file) {
  const input = $('#in-file');
  if (!input) return;
  input.classList.remove('in-file-quiet');
  if (!file) {
    input.value = '';
    try { input.files = null; } catch (e) { }          // 显式清空：不能只指望 value=''
    return;
  }
  try {
    const transfer = new DataTransfer();
    transfer.items.add(file);
    input.files = transfer.files;
  } catch (e) {
    input.classList.add('in-file-quiet');
  }
}
async function resolve(id) {
  if (busy) { status('上一条还在读取，请稍等；也可以点右上角“刷新”看最新结果。'); return; }
  const before = items.find(old => old.id === id) || {};
  busy = true; resolving = id; busyWhat = ''; render();
  status('正在' + retryAction(before) + '…');
  const startedAt = new Date().toISOString();
  try {
    const done = await api('/api/intake/' + encodeURIComponent(id) + '/resolve', {}, 60000);
    // 回读不出可确认的回收项，或回执属于另一条记录：只说「结果未确认」。
    // 不能拿别人的 status/outcome 给自己发一张「已重新解析」，也不记本轮时刻、不换条目（M1）。
    if (!isItem(done) || done.id !== id) { status(UNCONFIRMED); return; }
    // HTTP 200 也可能业务仍失败或还没完成：按结果给本次重试明确回执，不再无条件清空顶部状态（M1）。
    // 本轮的发出/返回时刻都记下，完成时间只从本轮的终态取。
    retryRound.set(id, { startedAt, returnedAt: new Date().toISOString() });
    remember(done);
    render();
    status(retryReceipt(done));
  }
  catch (e) { status(e.message); }
  finally { resolving = null; endBusy(); }
}
async function analyze(id) {
  if (busy) { status('上一条还在处理，请稍等；也可以点右上角“刷新”看最新结果。'); return; }
  if (analysisReady === false) { status(staleService); return; }
  startBusy('正在排队重新分析 ' + String(id).slice(0, 8)); status('正在排队重新分析…');
  try {
    await api('/api/intake/' + encodeURIComponent(id) + '/analyze', {});
    await load();
    status('已开始重新分析，结果里会自动刷新。');
  } catch (e) { status(e.message); } finally { endBusy(); }
}
const analyzeNowNote = '现在就分析不会用到还没出来的图片描述，只按收藏理由、链接正文和文件名分析。\n识别结果出来后还可以点“重新分析”再分析一次。要现在就分析吗？';
async function analyzeNow(id) {
  // 识别还在等：先把“用不到图片描述”说清，再走现有的 force 重新分析接口。
  if (!window.confirm(analyzeNowNote)) { status('未分析：继续等识别结果。'); return; }
  await analyze(id);
}
function confirmText(item) {
  // 确认框要把“一起删什么”说清，并区分正文是谁存的：
  // 知识库先收藏的那篇不会被删；回收站自己存的只有到最后一条引用才删。
  const shares = Number(item.document_shares || 0);
  const owned = item.document_owned === true, unknown = item.document_owned === null || item.document_owned === undefined;
  const parts = ['这条回收项', '它的即时分析结果'];
  if (item.file_path) parts.push('本机媒体文件');
  let knowledge = '';
  if (item.document_id) {
    if (unknown) {
      // 归属读不到（旧后端缺字段等）：不能拿“最后一条引用”当删正文的充分条件。
      knowledge = shares > 1
        ? `知识库里的这篇正文还被另 ${shares - 1} 条回收项引用，删这一条不会删正文；只有在确认它是回收站自己存的、而且这是最后一条引用时才会删。`
        : '知识库里的这篇正文只有在确认它是回收站自己存的、而且这是最后一条引用时才会删（含分块与检索索引）。';
    } else if (!owned) {
      knowledge = '知识库里的这篇正文不是回收站存的，不会被删；只删这条回收项。';
    } else if (shares > 1) {
      knowledge = `知识库里的这篇正文是回收站存的，但还被另 ${shares - 1} 条回收项引用，删这一条不会删正文；只有这是最后一条引用时才会删。`;
    } else {
      knowledge = '知识库里的这篇正文是回收站自己存的，也没有别的回收项引用，会一起删掉（含分块与检索索引）。';
    }
  }
  const cards = '已生成的提示词卡不删，会在卡库标成「来源已删除」。';
  return `删除这条回收项？\n会一起删掉：${parts.join('、')}。\n${knowledge}${cards}\n这一步不能撤销。`;
}
async function remove(id) {
  if (busy) { status('上一条还在处理，请稍等；也可以点右上角“刷新”看最新结果。'); return; }
  const item = items.find(old => old.id === id);
  if (!item) { status('这条已不在列表里，请刷新。'); return; }
  if (!window.confirm(confirmText(item))) { status('未删除。'); return; }
  startBusy('正在删除 ' + String(id).slice(0, 8));
  try {
    await api('/api/intake/' + encodeURIComponent(id) + '/delete', {}, 30000);
    items = items.filter(old => old.id !== id);
    selected.delete(id);
    render();
    status('已删除。');
  } catch (e) { status(e.message); } finally { endBusy(); }
}
function batchConfirm(preview) {
  // 确认框与单删同一口径：正文删不删看出处与引用计数，卡不删只标来源已删除。
  const count = Number(preview.count || 0);
  const distribution = Object.entries(preview.kinds || {}).map(([name, n]) => `${kindLabel(name)} ${n}`).join(' · ') || '无';
  const documents = preview.documents || {};
  const cards = preview.cards || {};
  const lines = [`批量删除这 ${count} 条回收项？`, `类型分布：${distribution}`,
    `会一起删掉：${count} 条回收项、它们的即时分析结果` +
    (preview.files ? `、${preview.files} 个本机媒体文件` : '') + '。'];
  if (documents.total) {
    const kept = [];
    if (documents.kept_shared) kept.push(`${documents.kept_shared} 篇还有未选中的回收项引用同一篇正文`);
    if (documents.kept_not_owned) kept.push(`${documents.kept_not_owned} 篇不是回收站存的（知识库先收藏或其他入口）`);
    if (documents.kept_unknown) kept.push(`${documents.kept_unknown} 篇出处读不到`);
    lines.push(`知识库：将一并移除 ${documents.would_remove || 0} 篇正文（回收站自己存的、这次删掉最后一条引用；含分块与检索索引）` +
      (kept.length ? `；${kept.join('；')}，保留不删` : '') + '。');
  }
  lines.push(cards.cards
    ? `提示词卡：涉及 ${cards.cards} 张已生成的卡（来自 ${cards.drops} 条回收项），不删，会在卡库标成「来源已删除」。`
    : '已生成的提示词卡不删，会在卡库标成「来源已删除」。');
  if ((preview.missing || []).length) lines.push(`其中 ${preview.missing.length} 条已不在列表里，不会重复删。`);
  lines.push('这一步不能撤销；一条失败不会回滚已删掉的，结果会逐条显示。');
  return lines.join('\n');
}
function batchResult(result) {
  const done = result.deleted || [], skipped = result.skipped || [], summary = result.summary || {};
  const parts = [`已删除 ${done.length} 条`];
  if (summary.documents_removed) parts.push(`一并删了 ${summary.documents_removed} 篇知识库正文`);
  if (summary.documents_kept) parts.push(`${summary.documents_kept} 篇正文按规则保留`);
  if (Number.isInteger(summary.cards_kept) && summary.cards_kept > 0) parts.push(`保留 ${summary.cards_kept} 张提示词卡`);
  if (skipped.length) parts.push(`${skipped.length} 条未删：` + skipped.map(entry =>
    `${String(entry.id || '').slice(0, 8)}：${entry.reason || '原因未记录'}`).join('；'));
  if (summary.partial) parts.push(`${summary.partial} 条有未完成的部分：` + (summary.errors || []).map(entry =>
    `${String(entry.id || '').slice(0, 8)}：${entry.error}`).join('；'));
  return parts.join('。') + '。';
}
async function batchDelete() {
  if (busy) { status('上一条还在处理，请稍等；也可以点右上角“刷新”看最新结果。'); return; }
  const ids = shown().filter(item => selected.has(item.id)).map(item => item.id);
  if (!ids.length) { status('先勾选要删的回收项（可用「全选当前筛选结果」）。'); return; }
  if (ids.length > MAX_BATCH) { status(`一次最多删 ${MAX_BATCH} 条，当前选了 ${ids.length} 条，请减少选择后重试。`); return; }
  startBusy('正在批量删除选中的回收项');
  let preview;
  try {
    // 先让服务端把“会删到什么”算清楚；读不到预览就不猜，也不发删除。
    preview = await api('/api/intake/delete-preview', { ids }, 30000);
  } catch (e) { endBusy(); status('没能拿到删除预览：' + e.message + ' 未删除任何一条。'); return; }
  if (!window.confirm(batchConfirm(preview))) { endBusy(); status('未删除。'); return; }
  status('正在批量删除…');
  try {
    const result = await api('/api/intake/delete-many', { ids }, 120000);
    const gone = new Set((result.deleted || []).map(entry => entry.id));
    items = items.filter(item => !gone.has(item.id));
    selected = new Set([...selected].filter(id => !gone.has(id)));
    render();
    status(batchResult(result));
  } catch (e) { status(e.message + ' 部分结果可能已写入，请点右上角“刷新”核对。'); }
  finally { endBusy(); }
}
$('#in-form').addEventListener('submit', submit);
$('#in-file').addEventListener('change', e => { const file = e.target.files[0]; if (file) pickFile(file); else setPendingFile(null); });
$('#in-picked').addEventListener('click', e => {
  if (e.target.closest('[data-cancel-upload]')) { cancelUpload(); return; }
  if (e.target.closest('[data-discard]')) { setPendingFile(null); status('已取消选中的文件，这条不会带上它。'); }
});
$('#in-refresh').addEventListener('click', () => load().catch(e => status(e.message)));
$('#in-views').addEventListener('click', e => { const button = e.target.closest('[data-kind]'); if (button) { kind = button.dataset.kind; render(); } });
$('#in-platforms').addEventListener('click', e => { const button = e.target.closest('[data-platform]'); if (button) { platform = button.dataset.platform; render(); } });
for (const [selector, apply] of [['#in-range', value => { range = value; }], ['#in-from', value => { from = value; }], ['#in-to', value => { to = value; }]]) {
  const control = $(selector);
  if (control) control.addEventListener('change', e => { apply(e.target.value); render(); });
}
$('#in-list').addEventListener('change', e => {
  const box = e.target.closest('[data-check]');
  if (!box) return;
  if (box.checked) selected.add(box.dataset.check); else selected.delete(box.dataset.check);
  render();
});
$('#in-all').addEventListener('change', e => {
  const visible = shown();
  for (const item of visible) { if (e.target.checked) selected.add(item.id); else selected.delete(item.id); }
  render();
});
$('#in-batch-clear').addEventListener('click', () => { selected.clear(); render(); status('已取消选择。'); });
$('#in-batch-delete').addEventListener('click', () => batchDelete());
$('#in-list').addEventListener('click', e => {
  const button = e.target.closest('[data-resolve]');
  if (button) { resolve(button.dataset.resolve); return; }
  const again = e.target.closest('[data-analyze]');
  if (again) { analyze(again.dataset.analyze); return; }
  const now = e.target.closest('[data-analyze-now]');
  if (now) { analyzeNow(now.dataset.analyzeNow); return; }
  const gone = e.target.closest('[data-delete]');
  if (gone) remove(gone.dataset.delete);
});
// Drag a file anywhere onto the page; dropped text stays with the input box.
// 只对「带文件」的拖放阻止默认行为：文件必须 preventDefault 才不会让浏览器直接打开它；
// 文字拖放若也被 preventDefault，浏览器就不会把这段文字插进输入框了（M3）。
const dragTypes = e => (e.dataTransfer && e.dataTransfer.types) || [];
const dragsFile = e => Array.prototype.indexOf.call(dragTypes(e), 'Files') >= 0;
for (const name of ['dragover', 'drop']) document.addEventListener(name, e => { if (dragsFile(e)) e.preventDefault(); });
// 拖拽反馈：只对「带文件」的拖拽高亮，拖文字不高亮。
// dragleave 在子元素之间也会冒泡，用计数器判断是不是真的离开了页面。
let dragDepth = 0;
function dropHint(on) {
  const form = $('#in-form'), hint = $('#in-drop-hint');
  if (form) form.classList.toggle('in-dragging', Boolean(on));
  if (hint) hint.hidden = !on;
}
document.addEventListener('dragenter', e => {
  if (!dragsFile(e)) return;
  e.preventDefault(); dragDepth++; dropHint(true);
});
document.addEventListener('dragover', e => {
  if (!dragsFile(e)) return;
  e.preventDefault(); dropHint(true);
  if (e.dataTransfer) e.dataTransfer.dropEffect = uploading ? 'none' : 'copy';
});
document.addEventListener('dragleave', e => {
  if (!dragsFile(e)) return;
  dragDepth = Math.max(0, dragDepth - 1);
  if (!dragDepth) dropHint(false);
});
document.addEventListener('drop', e => {
  dragDepth = 0; dropHint(false);
  const file = e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0];
  if (file) pickFile(file);
});
// Esc 取消拖拽高亮：拖到一半按住 Esc 不松手时，提示不能留在页面上。
document.addEventListener('keydown', e => { if (e.key === 'Escape') { dragDepth = 0; dropHint(false); } });
// 粘贴图片/视频（截屏、复制文件）也走同一条入口：原生框与「已选 …」照样一致。
document.addEventListener('paste', e => {
  const items = (e.clipboardData && e.clipboardData.items) || [];
  for (const item of items) {
    if (item.kind && item.kind !== 'file') continue;
    const file = item.getAsFile && item.getAsFile();
    if (file) { e.preventDefault(); pickFile(file); return; }
  }
});
Promise.all([probeAnalysis(), load()]).catch(e => status(e.message));
function needsRefresh(item) {
  // 凡是回执里承诺「结果会自动刷新」的，都要真的去刷（M2）：
  // 识别/分析在跑，或这条自身还在待解析（status 或 outcome 为 pending）。
  // 已被确认为失败的（有 error / status=failed / outcome=failed）不再轮询，免得对着终态空转。
  if (['running', 'queued'].includes(item.recognition?.status) || ['running', 'queued'].includes(item.analysis?.status)) return true;
  return (item.status === 'pending' || item.outcome === 'pending') && bodyVerdict(item).kind !== 'failed';
}
const poll = setInterval(() => {
  // 间隔与识别/分析轮询同一个（2 秒），不额外提高请求频率。
  if (items.some(item => needsRefresh(item))) load().catch(e => status(e.message));
}, 2000);
window.addEventListener('pagehide', () => clearInterval(poll));
})();
