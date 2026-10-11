'use strict';
(() => {
const $ = s => document.querySelector(s), esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const models = { qi21: 'Qwen Image 2.1', krea2: 'Krea2 Turbo', h3: 'MiniMax H3 · 视频' };
const statuses = { candidate: '候选', confirmed: '已确认', rejected: '已驳回' };
const sources = { verified: '来源已核对', stale: '来源原文已变化', missing: '来源资料已删除' };
const clock = v => v ? new Date(v).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', hour12: false }) : '时间未知';
// 组标题要给人看：先看有没有像样的标题，再看分析里已有的可写角度，最后退回类型+收下日期。
// 复合扩展名也算文件名：build.tar.gz、build.tar.bz2、build.tar.xz（tgz 这类简写一起收）。
const FILE_EXT = /\.(?:png|jpe?g|gif|webp|bmp|heic|heif|tiff|svg|pdf|docx?|xlsx?|pptx?|txt|md|csv|json|mp4|mov|m4v|webm|mp3|m4a|wav|zip|tar\.(?:gz|bz2|xz)|tgz|tbz2|txz)$/i;
const EXPORT_STAMP = /\d{8}T\d{6}/;   // 导出编号串，如 20261010T052433997515Z
const RAW_LINK = /^(?:[a-z][a-z0-9+.-]*:\/\/|\/\/|\/)/i;
// 机器导出的文件名/路径长这样：整串纯 ASCII，只有字母数字和 -_.+~，开头可以带一个下划线（_IMG_1234.png），最多再带目录分隔符。
// 只要出现中文、空格、中文标点或表情就当人写的标题——中文标题习惯不留空格，「如何使用a.md」不是文件名。
const NAME_CHARS = /^[A-Za-z0-9_][A-Za-z0-9._~+\-]*(?:[\\/][A-Za-z0-9._~+\-]+)*$/;
// 没有语义的日期名：20261010、2026-10-10-1430。
const DATE_NAME = /^(?:\d{4}[-_]?)?\d{2}[-_]?\d{2}(?:[-_.][A-Za-z0-9]+)*$/;
// 编号名只认这些相机/手机/聊天工具前缀：IMG_1234、DSC01234、PXL_2026...、VID_2026...、mmexport169...。
// 不用「几个字母 + 一串数字」这种宽数字正则：GPT123、CSS2026、ISO9001 是产品名/标准号，要按标题保留。
const STAMP_NAME = /^_?(?:IMG|DSC[NF]?|PXL|VID|PANO|MMEXPORT|WECHATIMG|FB_IMG|GIMP|CANON|NKF)[-_]?\d{2,}(?:[-_.]\d+)*$/i;
// 截图工具导出的名字带空格（Screenshot 2026-10-10 at 10.15.32.png）：前缀 + 完整日期 + 已知扩展名三个条件都满足才算导出名。
// 只放宽这一种写法，其它带空格的仍按人写的标题保留（photo copy.jpg）。
const ASCII_PRINTABLE = /^[\x20-\x7E]+$/;
const SCREENSHOT_NAME = /^screenshot[ _-]\d{4}[-_]\d{2}[-_]\d{2}/i;
const CLIP_TOLERANCE = 2;             // px，容掉亚像素误差
const TOPIC_LIMIT = 60;
const ANGLE_LABEL = '可写角度：';
// 成对符号：切「可写角度」时不能从引号/括号中间下刀。
// 单引号 ‘’ 也算引号，但只算独立成对的：it’s、don’t 里夹在字母中间的 ’ 是撇号，当普通字符看，不参与配对判定。
const PAIR_OPEN = new Set(['「', '『', '《', '〈', '（', '(', '【', '〔', '［', '[', '“', '‘']);
const PAIR_CLOSE = new Set(['」', '』', '》', '〉', '）', ')', '】', '〕', '］', ']', '”', '’']);
// 闭符号该配哪个开符号；英文直引号 “ 与 ” 之外还有同一个字符既开又闭的 "，靠栈里那个 " 配成对。
const PAIR_MATE = { '」': '「', '』': '『', '》': '《', '〉': '〈', '）': '（', ')': '(', '】': '【', '〕': '〔', '］': '［', ']': '[', '”': '“', '’': '‘', '"': '"' };
// 后端用句号把四块分析拼在一起，所以只有顶层句号才是「可写角度」这块的结尾；引号/括号里的句号是条目内部。
const SENTENCE_STOP = new Set(['。', '！', '？', '\n', '\r']);
const ENTRY_STOP = new Set(['、']);
const DANGLE_TAIL = /[的用和与而把在及或]$/;
const WORD_CHAR = /[A-Za-z0-9]/;
// 词中的撇号：’ 或 ‘ 两边紧贴英文字母/数字（it’s、don’t、rock’n’roll），不是引号。
function isApostrophe(text, i) {
  const ch = text[i];
  if (ch !== '’' && ch !== '‘') return false;
  return WORD_CHAR.test(text.charAt(i - 1)) && WORD_CHAR.test(text.charAt(i + 1));
}
// 这个位置是不是参与配对判定的引号/括号符号；词中撇号返回 false，按普通字符处理。
function isPairMark(text, i) {
  const ch = text[i];
  if (ch === '"') return true;
  if (!PAIR_OPEN.has(ch) && !PAIR_CLOSE.has(ch)) return false;
  return !isApostrophe(text, i);
}
// 这个位置是不是没收口的落单闭符号。
function isLoneClose(text, i, kind) {
  return kind[i] === 0 && isPairMark(text, i) && (PAIR_CLOSE.has(text[i]) || text[i] === '"');
}
let items = [], status = '', query = '', kind = '', view = 'source', counts = {}, pending = 0, busy = false;

async function api(url, body) {
  const r = await fetch(url, body === undefined ? {} : { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Article-Studio': '1' }, body: JSON.stringify(body) });
  const value = await r.json();
  if (!r.ok) throw new Error(value.error || '请求失败');
  return value;
}
function say(message) { $('#cd-status').textContent = message || ''; }
function listUrl() {
  const params = new URLSearchParams();
  if (status) params.set('status', status);
  if (query.trim()) params.set('q', query.trim());
  if (kind) params.set('kind', kind);
  params.set('view', view);
  params.set('limit', '200');
  return '/api/cards?' + params.toString();
}
function statsBar() {
  const parts = ['候选 ' + (counts.candidate || 0), '已确认 ' + (counts.confirmed || 0), '已驳回 ' + (counts.rejected || 0)];
  if (pending) parts.push('待提炼回收项 ' + pending + ' 条');
  $('#cd-stats').innerHTML = parts.map(part => '<span>' + esc(part) + '</span>').join('');
}
function refRow(ref) {
  return '<div class="cd-ref"><b>' + esc(ref.ref) + '</b><span>' + esc(ref.label || '来源片段') + ' · ' + ref.chars +
    ' 字 · 指纹 ' + esc(ref.sha256) + '</span><em>' + esc(ref.excerpt) + '</em></div>';
}
function actions(card) {
  const buttons = [];
  if (card.status !== 'confirmed') buttons.push('<button class="button primary" data-action="confirm">确认入库</button>');
  if (card.status === 'candidate') buttons.push('<button class="button" data-action="reject">驳回</button>');
  if (card.status === 'rejected') buttons.push('<button class="button" data-action="restore">恢复为候选</button>');
  if (card.status === 'confirmed') {
    buttons.push('<a class="button primary" href="' + esc(card.fill_url) + '">填进生图 ↗</a>');
    buttons.push('<button class="button" data-action="restore">改回候选</button>');
  }
  buttons.push('<button class="button subtle" data-action="delete" type="button">删除</button>');
  return buttons.join('');
}
function fromLabel(source) {
  if (source && source.channel === 'intel') return '情报推送';
  return (source && source.kind_label) || '回收项';
}
// 空标题、后端退回的链接、导出编号串，以及看着就像机器导出的文件名/路径，才算“原始出处”。
// 判据不再靠“有没有空格”：中文标题习惯不留空格，「如何使用a.md」得按正常标题保留。
function looksLikeFileName(text) {
  // 唯一放宽空格的导出名：截图工具前缀 + 完整日期 + 已知扩展名，三个条件缺一不可。
  if (ASCII_PRINTABLE.test(text) && SCREENSHOT_NAME.test(text) && FILE_EXT.test(text)) return true;
  if (!NAME_CHARS.test(text)) return false;                       // 含中文、空格、中文标点 → 不是导出文件名
  return FILE_EXT.test(text) || /[\\/]/.test(text) || DATE_NAME.test(text) || STAMP_NAME.test(text);
}
function isRawName(value) {
  const text = String(value || '').trim();
  if (!text) return true;
  if (RAW_LINK.test(text) || EXPORT_STAMP.test(text)) return true;
  return looksLikeFileName(text);
}
function shortDate(value) {
  if (!value) return '';
  const when = new Date(value);
  return Number.isNaN(when.getTime()) ? '' : when.toLocaleDateString('zh-CN', { month: 'numeric', day: 'numeric', timeZone: 'Asia/Shanghai' });
}
// 每个位置「在不在成对符号内部」。只有真配成对的引号/括号才算内部：落单的半个引号既不保护后面的句号，
// 也不算另一段的结尾。kind: +1 配对的开符号 / -1 配对的闭符号 / 0 普通字符或落单的半个；unmatched 是没收口的开符号。
function pairScan(text) {
  const kind = new Array(text.length).fill(0), stack = [];
  for (let i = 0; i < text.length; i++) {
    const ch = text[i];
    if (!isPairMark(text, i)) continue;                            // 普通字符，或 it’s 里的撇号
    if (PAIR_CLOSE.has(ch) || ch === '"') {
      const open = stack.length ? stack[stack.length - 1] : -1;
      if (open >= 0 && PAIR_MATE[ch] === text[open]) { kind[open] = 1; kind[i] = -1; stack.pop(); continue; }
      if (ch !== '"') continue;
    }
    if (PAIR_OPEN.has(ch) || ch === '"') stack.push(i);
  }
  const depth = new Array(text.length + 1).fill(0);
  for (let i = 0; i < text.length; i++) depth[i + 1] = depth[i] + kind[i];
  return { kind, depth, unmatched: stack };
}
// 从 from 起取到下一个顶层终止符之前。句号、顿号都走这一套层级判定：成对符号内部的不算分隔。
function topLevelSlice(text, from, stopAt) {
  const { depth } = pairScan(text);
  let out = '';
  for (let i = from; i < text.length; i++) {
    if (depth[i] > 0 || depth[i + 1] > 0) { out += text[i]; continue; }   // 在成对引号/括号里面
    if (stopAt.has(text[i])) break;
    out += text[i];
  }
  return out;
}
// 只挑已落库分析里现成的「可写角度」，不新写句子、不改出处。
// 后端用顿号把各角度拼在一起，所以只在“顶层顿号”处取第一条；引号/括号里的顿号是条目内部，不能切。
function firstAngleEntry(text) {
  return topLevelSlice(String(text || ''), 0, ENTRY_STOP).trim();
}
// 最后一个没配对的开括号位置；从它之前收刀，就不会切出「绿三角这种半句。
function unpairedOpenIndex(text) {
  const { unmatched } = pairScan(text);
  return unmatched.length ? unmatched[unmatched.length - 1] : -1;
}
// 引号/括号是不是都配上了（落单的半个开或闭都算没配上）。
function pairsBalanced(text) {
  const { kind, unmatched } = pairScan(text);
  if (unmatched.length) return false;
  for (let i = 0; i < text.length; i++) if (isLoneClose(text, i, kind)) return false;
  return true;
}
// 从第一个落单的引号/括号之前收刀，宁可少要一段，也不留半个引号。
function dropUnpaired(text) {
  const { kind, unmatched } = pairScan(text);
  let cut = text.length;
  for (const i of unmatched) cut = Math.min(cut, i);
  for (let i = 0; i < text.length; i++) if (isLoneClose(text, i, kind)) cut = Math.min(cut, i);
  return text.slice(0, cut);
}
// 顶层（引号/括号外）最后一个分句边界。
function lastTopLevelClause(text) {
  const { depth } = pairScan(text);
  let at = -1;
  for (let i = 0; i < text.length; i++) {
    const ch = text[i];
    if (depth[i] === 0 && depth[i + 1] === 0 && (ch === '，' || ch === '；' || ch === '、')) at = i;
  }
  return at;
}
function trimPunct(text) {
  return String(text).replace(/[，、；：,;:\s]+$/, '').trim();
}
// 收刀后剩下「……用」这种虚词尾巴，就退到上一个顶层分句边界。
function trimDangle(text) {
  if (!DANGLE_TAIL.test(text)) return trimPunct(text);
  const clause = lastTopLevelClause(text);
  return trimPunct(clause > 0 ? text.slice(0, clause) : text.slice(0, -1));
}
function clipTopic(entry) {
  if (entry.length <= TOPIC_LIMIT) return entry;
  let cut = entry.slice(0, TOPIC_LIMIT);
  const open = unpairedOpenIndex(cut);
  if (open >= 0) cut = cut.slice(0, open);          // 引号/括号装不下就整段去掉，不从中间切
  const clause = lastTopLevelClause(cut);
  if (clause >= Math.ceil(cut.length * 0.5)) cut = cut.slice(0, clause);   // 宁可退到上一个分句，也不留「……用」这种尾巴
  cut = trimPunct(cut);
  return cut ? cut + '…' : '';
}
function analysisTopic(line) {
  const text = String(line || '');
  const at = text.indexOf(ANGLE_LABEL);
  if (at < 0) return '';
  // 先按顶层句号收住「可写角度」这一块（引号里的句号留给条目），再按顶层顿号取第一条角度。
  let entry = trimPunct(firstAngleEntry(topLevelSlice(text, at + ANGLE_LABEL.length, SENTENCE_STOP)));
  if (entry && !pairsBalanced(entry)) entry = trimDangle(dropUnpaired(entry));
  // 还是不成对就干脆不用这条角度：宁可退回类型加收下日期，也不给「先留白这种半边引号。
  if (!entry || !pairsBalanced(entry)) return '';
  return clipTopic(entry);
}
function groupTitle(group) {
  const source = group.source || {};
  if (source.deleted) return '来源已删除';
  if (!isRawName(source.title)) return String(source.title).trim();
  const topic = analysisTopic(group.analysis_line);
  if (topic) return topic;
  const day = shortDate(source.received_at);
  return day ? fromLabel(source) + ' · ' + day + ' 收下' : fromLabel(source);
}
// 标题换成给人看的之后，原始文件名/链接原样留在这行小字里，不删也不改写。
function rawSourceName(source) {
  if (!source || source.deleted) return '';
  const raw = String(source.title || source.url || '').trim();
  return isRawName(raw) ? raw : '';
}
function cardView(card, showOrigin) {
  const note = 'data-note="' + card.id + '"';
  const source = card.source || {};
  const origin = showOrigin ? rawSourceName(source) : '';
  const originLine = origin ? '<p class="cd-source-name"><span>来源</span><b title="' + esc(origin) + '">' + esc(origin) + '</b></p>' : '';
  const knowledge = source.knowledge_url ? '<a class="cd-knowledge" href="' + esc(source.knowledge_url) + '">核对来源原文 ↗</a>' : '';
  const sourceBlock = originLine || knowledge ? '<div class="cd-source-block">' + originLine + knowledge + '</div>' : '';
  return '<article class="cd-card" data-id="' + card.id + '">' +
    '<header class="cd-card-head"><span class="cd-model">' + esc(models[card.model] || card.model) + '</span>' +
    '<span class="cd-status cd-' + card.status + '">' + esc(statuses[card.status] || card.status) + '</span>' +
    '<span class="cd-source-state cd-source-' + card.source_state + '">' + esc(sources[card.source_state] || card.source_state) + '</span>' +
    (source.deleted ? '<span class="cd-source-deleted">来源已删除</span>' : '') +
    '<span class="cd-from">' + esc(fromLabel(source)) + '</span></header>' +
    '<p class="cd-prompt">' + esc(card.prompt) + '</p>' +
    (card.scene ? '<p class="cd-scene">适用场景 · ' + esc(card.scene) + '</p>' : '') +
    (card.reason ? '<p class="cd-reason">处理说明 · ' + esc(card.reason) + '</p>' : '') +
    '<div class="cd-refs">' + card.refs.map(refRow).join('') + '</div>' +
    sourceBlock +
    '<label class="cd-note-label"><span>我的备注</span><textarea ' + note + ' rows="2" maxlength="1000">' + esc(card.note) + '</textarea></label>' +
    '<footer class="cd-card-foot"><span class="cd-meta">' + esc(clock(card.created_at)) +
    (card.model_name ? ' · 提炼模型 ' + esc(card.model_name) : '') + '</span>' +
    '<button class="button subtle" data-action="note" type="button">保存备注</button>' + actions(card) + '</footer></article>';
}
function groupLocally(list) {
  const index = new Map();
  const ordered = [];
  for (const card of list) {
    const key = card.drop_id || card.id;
    if (!index.has(key)) {
      const group = { drop_id: card.drop_id, source: card.source || {}, analysis_line: card.analysis_line || '', cards: [] };
      index.set(key, group);
      ordered.push(group);
    }
    const group = index.get(key);
    group.cards.push(card);
    if (!group.analysis_line && card.analysis_line) group.analysis_line = card.analysis_line;
  }
  ordered.sort((a, b) => String(b.source.received_at || '').localeCompare(String(a.source.received_at || '')));
  return ordered;
}
function groupView(group) {
  const source = group.source || {};
  const deleted = !!source.deleted;
  const title = groupTitle(group);
  const link = !deleted && (source.link || source.knowledge_url || source.url);
  const when = source.received_at ? '收下 ' + clock(source.received_at) : '';
  const origin = rawSourceName(source);
  const line = group.analysis_line || (deleted ? '这条没有留下四块分析。' : '这条还没有四块分析。');
  const cleanup = deleted ? '<button class="button" data-action="cleanup" type="button">清理这些卡片</button>' : '';
  return '<section class="cd-group" data-drop="' + esc(group.drop_id) + '"><header class="cd-group-head">' +
    '<div class="cd-group-top"><h2 title="' + esc(title) + '">' + esc(title) + '</h2>' +
    (link ? '<a class="cd-group-link" href="' + esc(link) + '">打开来源</a>' : '') +
    (when ? '<span class="cd-group-time">' + esc(when) + '</span>' : '') + cleanup + '</div>' +
    (origin ? '<p class="cd-group-origin"><span>原始来源</span><b title="' + esc(origin) + '">' + esc(origin) + '</b></p>' : '') +
    '<p class="cd-group-summary"><span class="cd-group-line">' + esc(line) + '</span></p></header>' +
    (group.cards || []).map(card => cardView(card, false)).join('') + '</section>';
}
// 展开入口按“这行分析实际有没有被裁掉”判定，不按字数猜：窄屏 80 字也能超 3 行，宽屏 300 字一行就放得下。
// jsdom 没有排版引擎，测量可注入 window.__cdMeasure；浏览器里走真实 scrollHeight/clientHeight。
function readBox(box, measure) {
  const probe = typeof measure === 'function' ? measure
    : (typeof window !== 'undefined' && typeof window.__cdMeasure === 'function' ? window.__cdMeasure : null);
  if (probe) return probe(box);
  return { scrollHeight: box.scrollHeight, clientHeight: box.clientHeight };
}
function summaryClipped(summary, measure) {
  const line = summary.querySelector('.cd-group-line');
  if (!line) return false;
  const open = summary.classList.contains('cd-group-open');
  if (open) summary.classList.remove('cd-group-open');   // 按收起状态量，展开态本身不裁切
  const box = readBox(line, measure);
  if (open) summary.classList.add('cd-group-open');
  const overflow = Number(box.scrollHeight) - Number(box.clientHeight);
  return Number.isFinite(overflow) && overflow > CLIP_TOLERANCE;
}
function syncGroupMore(section, measure) {
  const summary = section.querySelector('.cd-group-summary');
  if (!summary) return;
  const head = summary.parentElement || section;
  if (summaryClipped(summary, measure)) {
    let more = head.querySelector('.cd-group-more');
    if (!more) {
      more = document.createElement('button');
      more.className = 'cd-group-more';
      more.type = 'button';
      more.dataset.action = 'more';
      head.insertBefore(more, summary.nextSibling);
    }
    const open = summary.classList.contains('cd-group-open');
    more.setAttribute('aria-expanded', open ? 'true' : 'false');
    more.textContent = open ? '收起分析' : '展开完整分析';
    return;
  }
  const more = head.querySelector('.cd-group-more');
  if (more) more.remove();
  summary.classList.remove('cd-group-open');   // 没被裁切，展开态没有意义
}
function syncGroups(measure) {
  for (const section of document.querySelectorAll('.cd-group')) syncGroupMore(section, measure);
}
function render(data) {
  const list = items;
  if (!list.length) {
    $('#cd-list').innerHTML = '<p class="cd-empty">还没有卡片。定时任务跑过一轮后，候选卡会出现在这里；也可以先往回收站丢几条内容。</p>';
    return;
  }
  if (view === 'source') {
    const groups = Array.isArray(data.groups) && data.groups.length ? data.groups : groupLocally(list);
    $('#cd-list').innerHTML = groups.map(groupView).join('');
    syncGroups();
    return;
  }
  $('#cd-list').innerHTML = list.map(card => cardView(card, view !== 'source')).join('');
}
async function load() {
  try {
    const data = await api(listUrl());
    items = data.items || []; counts = data.counts || {}; pending = data.pending_drops || 0;
    statsBar();
    $('#cd-count').textContent = '共 ' + (data.total || 0) + ' 张';
    render(data);
  } catch (error) { say('读取失败：' + error.message); }
}
async function decide(id, action) {
  if (busy) return; busy = true;
  let reason = '';
  if (action === 'reject') {
    const answer = window.prompt('驳回原因（可留空）');
    if (answer === null) { busy = false; return; }
    reason = answer;
  }
  try {
    const updated = await api('/api/cards/' + id + '/' + action, reason ? { reason } : {});
    say(action === 'confirm' ? '已确认，这张卡进入提示词卡库，可在生成页使用。'
      : action === 'reject' ? '已驳回，卡片与原因保留在本机。'
        : action === 'restore' ? '已改回候选。' : '已更新。');
    items = items.map(item => item.id === id ? updated : item);
    await load();
  } catch (error) { say('操作未完成：' + error.message); } finally { busy = false; }
}
async function removeCard(id) {
  if (busy) return;
  if (!window.confirm('只删除这张卡片，不改回收项、知识库或其他卡片。确定删除？')) { say('已取消。'); return; }
  busy = true;
  try {
    await api('/api/cards/' + id + '/delete', {});
    say('已删除这张卡片。');
    await load();
  } catch (error) { say('操作未完成：' + error.message); } finally { busy = false; }
}
async function cleanup(dropId) {
  if (busy) return;
  if (!window.confirm('只删除这组来源已删除的卡片，不改其他数据。确定清理？')) { say('已取消。'); return; }
  busy = true;
  try {
    await api('/api/cards/cleanup', { drop_id: dropId });
    say('已清理这组卡片。');
    await load();
  } catch (error) { say('操作未完成：' + error.message); } finally { busy = false; }
}
async function saveNote(id) {
  if (busy) return; busy = true;
  const box = document.querySelector('[data-note="' + id + '"]');
  try {
    const updated = await api('/api/cards/' + id + '/note', { note: box ? box.value : '' });
    items = items.map(item => item.id === id ? updated : item);
    say('备注已保存。');
  } catch (error) { say('备注未保存：' + error.message); } finally { busy = false; }
}
$('#cd-list').addEventListener('click', event => {
  const button = event.target.closest('[data-action]'); if (!button) return;
  const action = button.dataset.action;
  if (action === 'more') {
    const head = event.target.closest('.cd-group-head');
    const summary = head && head.querySelector('.cd-group-summary');
    if (summary) {
      const open = summary.classList.toggle('cd-group-open');
      button.setAttribute('aria-expanded', open ? 'true' : 'false');
      button.textContent = open ? '收起分析' : '展开完整分析';
    }
    return;
  }
  if (action === 'cleanup') {
    const group = event.target.closest('[data-drop]');
    if (group) cleanup(group.dataset.drop);
    return;
  }
  const card = event.target.closest('[data-id]'); if (!card) return;
  if (action === 'note') saveNote(card.dataset.id);
  else if (action === 'delete') removeCard(card.dataset.id);
  else decide(card.dataset.id, action);
});
$('#cd-views').addEventListener('click', event => {
  const button = event.target.closest('[data-status]'); if (!button) return;
  for (const item of $('#cd-views').querySelectorAll('button')) item.classList.toggle('selected', item === button);
  status = button.dataset.status; load();
});
$('#cd-layout').addEventListener('click', event => {
  const button = event.target.closest('[data-view]'); if (!button) return;
  for (const item of $('#cd-layout').querySelectorAll('button')) item.classList.toggle('selected', item === button);
  view = button.dataset.view; load();
});
$('#cd-kinds').addEventListener('click', event => {
  const button = event.target.closest('[data-kind]'); if (!button) return;
  for (const item of $('#cd-kinds').querySelectorAll('button')) item.classList.toggle('selected', item === button);
  kind = button.dataset.kind; load();
});
let timer = null;
$('#cd-search').addEventListener('input', () => { clearTimeout(timer); timer = setTimeout(() => { query = $('#cd-search').value; load(); }, 300); });
// 窗口尺寸变了，被裁切的行数也跟着变：重新判定哪些摘要需要展开入口。
let resizeTimer = null;
window.addEventListener('resize', () => { clearTimeout(resizeTimer); resizeTimer = setTimeout(() => syncGroups(), 200); });
if (document.fonts && document.fonts.ready && typeof document.fonts.ready.then === 'function') {
  document.fonts.ready.then(() => syncGroups()).catch(() => {});
}
$('#cd-refresh').onclick = () => load();
load();
})();
