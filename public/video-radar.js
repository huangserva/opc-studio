/* 视频爆款雷达 C2 前端。
 *
 * 纪律：所有来自 page.json 的外部文本一律用 textContent / 属性赋值写入，
 * 本文件不做任何 HTML 字符串拼接（标题、作者、链接、按钮文案都不例外）。
 */
'use strict';

const PAGE_URL = '/api/video-radar/page';
const FEEDBACK_URL = '/api/video-radar/feedback';
const LIST_KEYS = ['xiaohongshu', 'douyin_realtime', 'douyin_daily'];
const LIST_LABELS = {
  xiaohongshu: '小红书（每小时）',
  douyin_realtime: '抖音每小时热榜',
  douyin_daily: '抖音日榜（数据约晚 1–2 天）'
};
const VERDICTS = [
  { value: 'worth', label: '值得跟', className: 'is-worth' },
  { value: 'not_worth', label: '不值得', className: 'is-not-worth' },
  { value: 'irrelevant', label: '不相关', className: 'is-irrelevant' }
];
// 只放行 schema 白名单里的两种形态：
//   https://www.douyin.com/video/<数字 id>      （热榜的 item_url 是带时效签名的文件地址，天然不匹配）
//   https://www.xiaohongshu.com/explore/<...>
// 允许可选的 query/hash，其余一律不成链。
const SAFE_HREF = /^https:\/\/www\.douyin\.com\/video\/[0-9]{1,40}\/?(?:[?#][^\s]*)?$/i;
const SAFE_HREF_XHS = /^https:\/\/www\.xiaohongshu\.com\/explore\/[A-Za-z0-9_-]{1,120}(?:[?#][^\s]*)?$/i;
const DASH = '—';

let state = { status: 'missing', current: 'xiaohongshu', data: null, loadedAt: null, error: null };
const queues = new Map();
let requestSeq = 0;

function text(value, fallback) {
  if (value === null || value === undefined) return fallback === undefined ? DASH : fallback;
  if (typeof value === 'string') return value.length ? value : (fallback === undefined ? DASH : fallback);
  if (typeof value === 'number') return Number.isFinite(value) ? String(value) : (fallback === undefined ? DASH : fallback);
  if (typeof value === 'boolean') return value ? '是' : '否';
  return fallback === undefined ? DASH : fallback;
}

// null → 「—」，0 → 0（区分「未知」和「真的是 0」）
function count(value) {
  if (value === null || value === undefined) return DASH;
  if (typeof value === 'number' && Number.isFinite(value)) return formatInt(value);
  return DASH;
}

function rate(value, digits) {
  if (value === null || value === undefined) return DASH;
  const number = Number(value);
  if (!Number.isFinite(number)) return DASH;
  if (Number.isInteger(number)) return String(number);
  return number.toFixed(digits === undefined ? 1 : digits);
}

function formatInt(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return DASH;
  if (Math.abs(number) >= 10000) return (number / 10000).toFixed(1) + '万';
  return String(Math.round(number));
}

function relativeTime(iso) {
  if (typeof iso !== 'string' || !iso.trim()) return DASH;
  const stamp = Date.parse(iso);
  if (!Number.isFinite(stamp)) return DASH;
  const minutes = (Date.now() - stamp) / 60000;
  if (minutes < 0) return '时间异常';
  if (minutes < 60) return Math.max(0, Math.round(minutes)) + ' 分钟前';
  if (minutes < 1440) return (minutes / 60).toFixed(1) + ' 小时前';
  return (minutes / 1440).toFixed(1) + ' 天前';
}

// 链接白名单：只允许小红书 explore 与抖音 video 的 https 地址，其余不渲染成链接
function safeHref(url) {
  if (typeof url !== 'string') return '';
  const trimmed = url.trim();
  if (!trimmed) return '';
  if (/^\s*javascript:/i.test(trimmed)) return '';
  if (/^\s*data:/i.test(trimmed)) return '';
  if (!/^https:\/\//i.test(trimmed)) return '';
  try {
    const parsed = new URL(trimmed);
    if (parsed.protocol !== 'https:') return '';
    if (!SAFE_HREF.test(parsed.href) && !SAFE_HREF_XHS.test(parsed.href)) return '';
    return parsed.href;
  } catch (e) {
    return '';
  }
}

function elapsedHours(iso) {
  if (typeof iso !== 'string' || !iso.trim()) return null;
  const stamp = Date.parse(iso);
  if (!Number.isFinite(stamp)) return null;
  return Math.max(0, (Date.now() - stamp) / 3600000);
}

function element(tag, className, content) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (content !== undefined && content !== null) node.textContent = String(content);
  return node;
}

function showToast(message, kind, titleText) {
  const container = document.getElementById('vr-toast-container');
  if (!container) return;
  const toast = element('div', 'vr-toast ' + (kind === 'warning' ? 'is-warning' : 'is-success'));
  toast.appendChild(element('div', 'vr-toast-title', titleText || (kind === 'warning' ? '操作失败' : '已保存')));
  toast.appendChild(element('div', 'vr-toast-text', message));
  container.appendChild(toast);
  setTimeout(() => toast.remove(), kind === 'warning' ? 6000 : 3500);
}

function renderStatus() {
  const box = document.getElementById('vr-status');
  if (!box) return;
  box.className = 'vr-status is-' + (state.status === 'ok' ? 'ok' : (state.status === 'corrupt' ? 'corrupt' : 'missing'));
  box.textContent = '';
  if (state.status === 'ok') {
    const data = state.data;
    const keyword = text(data.keyword, DASH);
    const generated = text(data.generated_at, DASH);
    document.getElementById('vr-keyword').textContent = '关键词 ' + keyword;
    document.getElementById('vr-generated').textContent = '数据生成时间 ' + generated;
    const today = data.today || {};
    const parts = [
      '今天轮次 ' + count(today.rounds),
      '小红书成功 ' + count(today.xiaohongshu_ok),
      '抖音实时成功 ' + count(today.douyin_realtime_ok),
      '抖音日榜成功 ' + count(today.douyin_daily_ok),
      'AIsa 调用 ' + count(today.api_calls) + ' 次'
    ];
    box.appendChild(element('div', null, '今日（' + text(today.date, DASH) + '）：' + parts.join(' · ')));
    const sourceLine = element('div', null, '');
    LIST_KEYS.forEach((name) => {
      const meta = (data.sources || {})[name] || {};
      const age = typeof meta.age_minutes === 'number' ? (' ' + meta.age_minutes + ' 分钟前') : '';
      const flag = meta.stale ? ' ⚠️ 数据已过期' : '';
      const piece = document.createElement('span');
      piece.style.marginRight = '14px';
      piece.appendChild(element('strong', null, LIST_LABELS[name]));
      piece.appendChild(document.createTextNode('：最近成功 ' + text(meta.last_success_at, '从未') + age + '（' + text(meta.status, DASH) + '）'));
      if (flag) piece.appendChild(element('span', 'vr-stale-flag', flag));
      if (meta.note) piece.appendChild(element('span', 'vr-dim', ' · ' + text(meta.note)));
      sourceLine.appendChild(piece);
    });
    box.appendChild(sourceLine);
    if (data.feedback_status === 'invalid') {
      box.appendChild(element('div', 'vr-stale-flag', '反馈文件损坏：反馈不会被保存，请先修好 workspace/video-radar/feedback.json'));
    }
  } else if (state.status === 'missing') {
    document.getElementById('vr-keyword').textContent = '关键词 —';
    document.getElementById('vr-generated').textContent = '数据时间 —';
    box.appendChild(element('div', null, '视频榜还没数据：采集尚未产出 workspace/video-radar/page.json（监控未运行）。'));
  } else {
    document.getElementById('vr-keyword').textContent = '关键词 —';
    document.getElementById('vr-generated').textContent = '数据时间 —';
    box.appendChild(element('div', null, '视频榜数据异常：' + text(state.error, 'page.json 不可读')));
    box.appendChild(element('div', 'vr-dim', '页面不会显示任何残留旧数据。'));
  }
}

function renderTabs() {
  const tabs = document.getElementById('vr-tabs');
  if (!tabs) return;
  const buttons = tabs.querySelectorAll('.vr-tab');
  buttons.forEach((button) => {
    const name = button.dataset.list;
    button.classList.toggle('active', name === state.current);
    const stale = state.status === 'ok' && (state.data.sources || {})[name] && state.data.sources[name].stale;
    const existing = button.querySelector('.vr-tab-stale');
    if (stale && !existing) {
      button.appendChild(element('span', 'vr-tab-stale', '⚠️ 过期'));
    } else if (!stale && existing) {
      existing.remove();
    }
  });
}

function buildTitleCell(item) {
  const cell = element('td', 'vr-title');
  const title = text(item.title, '(无标题)');
  const href = safeHref(item.url);
  if (href) {
    const link = element('a', null, title);
    link.href = href;
    link.target = '_blank';
    link.rel = 'noopener noreferrer';
    cell.appendChild(link);
  } else {
    cell.appendChild(element('span', 'vr-no-link', title));
    cell.appendChild(element('div', 'vr-dim', '（链接不合规，未渲染为链接）'));
  }
  return cell;
}

function buildFlagCell(item) {
  const cell = element('td', null, '');
  const wrap = element('div', 'vr-flags');
  if (Array.isArray(item.flags) && item.flags.indexOf('low_fan_hot') >= 0) {
    wrap.appendChild(element('span', 'vr-flag is-lowfan', '低粉爆款'));
  }
  if (item.relevance === 'weak') {
    wrap.appendChild(element('span', 'vr-flag is-weak', '弱相关'));
  } else if (item.relevance === 'relevant' && item.relevance_reason) {
    wrap.appendChild(element('span', 'vr-flag is-xhs', text(item.relevance_reason)));
  }
  if (item.platform === 'douyin') {
    wrap.appendChild(element('span', 'vr-flag is-douyin', '抖音'));
  } else if (item.platform === 'xiaohongshu') {
    wrap.appendChild(element('span', 'vr-flag is-xhs', '小红书'));
  }
  if (Array.isArray(item.sources) && item.sources.length) {
    wrap.appendChild(element('span', 'vr-flag', item.sources.join(' / ')));
  }
  cell.appendChild(wrap);
  return cell;
}

function currentVerdict(key) {
  if (state.status !== 'ok' || !state.data) return null;
  const record = (state.data.feedback || {})[key];
  return record && record.verdict ? record.verdict : null;
}

function queueFor(key) {
  let queue = queues.get(key);
  if (!queue) {
    queue = { desired: null, confirmed: null, inFlight: false, seq: 0, appliedSeq: 0, ready: false };
    queues.set(key, queue);
  }
  if (!queue.ready) {
    queue.confirmed = currentVerdict(key);
    queue.desired = queue.confirmed;
    queue.ready = true;
  }
  return queue;
}

function refreshButtons() {
  document.querySelectorAll('.vr-btn').forEach((button) => {
    const key = button.dataset.key || '';
    const active = currentVerdict(key) === button.dataset.verdict;
    button.classList.toggle('active', active);
    button.setAttribute('aria-pressed', active ? 'true' : 'false');
  });
}

// 注意：这里绝不用 disabled——disabled 会让同一事件轮里的后续点击根本不触发，
// 用户连点两个不同按钮时后一次会被浏览器吞掉。忙态只做视觉与无障碍标记。
function markBusy(key, busy) {
  document.querySelectorAll('.vr-btn[data-key="' + CSS.escape(key) + '"]').forEach((button) => {
    if (busy) {
      button.setAttribute('data-busy', '1');
      button.setAttribute('aria-disabled', 'true');
    } else {
      button.removeAttribute('data-busy');
      button.removeAttribute('aria-disabled');
    }
  });
}

function onVerdictClick(event) {
  const button = event.target instanceof Element ? event.target.closest('.vr-btn') : null;
  if (!button) return;
  const key = button.dataset.key || '';
  const verdict = button.dataset.verdict;
  if (!key || VERDICTS.some((row) => row.value === verdict) === false) return;
  const queue = queueFor(key);
  queue.desired = queue.desired === verdict ? null : verdict;   // 再点同一按钮 = 撤销
  pump(key, verdict);
}

function pump(key, label) {
  const queue = queueFor(key);
  if (queue.inFlight) return;                                    // 在途：新意图只排队
  if (queue.desired === queue.confirmed) { markBusy(key, false); return; }
  queue.inFlight = true;
  markBusy(key, true);
  const seq = ++queue.seq;
  const target = queue.desired;
  const body = JSON.stringify({ key: key, verdict: target });
  fetch(FEEDBACK_URL, {
    method: 'POST',
    cache: 'no-store',
    headers: { 'Content-Type': 'application/json', 'X-Article-Studio': '1' },
    body: body
  }).then(async (response) => {
    if (!response.ok) {
      let reason = 'HTTP ' + response.status;
      try {
        const data = await response.json();
        if (data && typeof data.error === 'string') reason = data.error;
      } catch (e) { /* 非 JSON 错误体，保留状态码文案 */ }
      const failure = new Error(reason);
      failure.status = response.status;
      throw failure;
    }
    return response.json().catch(() => ({}));
  }).then((record) => {
    if (seq <= queue.appliedSeq) return;                         // 旧响应不覆盖较新的状态
    queue.appliedSeq = seq;
    const stored = (record && typeof record === 'object' && 'verdict' in record) ? record.verdict : target;
    if (!state.data) state.data = { feedback: {} };
    if (!state.data.feedback) state.data.feedback = {};
    if (stored === null || stored === undefined) delete state.data.feedback[key];
    else state.data.feedback[key] = { verdict: stored, updated_at: (record && record.updated_at) || null };
    queue.confirmed = stored === undefined ? null : stored;
    refreshButtons();
    const labelText = target === null ? '已撤销这条反馈' : '已标记为「' + label + '」';
    showToast(labelText, 'success');
  }).catch((error) => {
    queue.desired = queue.confirmed;                            // 失败：回到服务端最后确认状态
    refreshButtons();
    showToast('保存失败：' + (error && error.message ? error.message : '未知错误'), 'warning');
  }).then(() => {
    queue.inFlight = false;
    markBusy(key, queue.desired !== queue.confirmed);
    pump(key, label);
  });
}

function buildActionsCell(item) {
  const cell = element('td', 'vr-actions');
  const key = item.key || '';
  VERDICTS.forEach((row) => {
    const button = element('button', 'vr-btn ' + row.className, row.label);
    button.type = 'button';
    button.dataset.verdict = row.value;
    button.dataset.key = key;
    button.title = row.label + '：' + (item.title || '');
    const active = currentVerdict(key) === row.value;
    button.classList.toggle('active', active);
    button.setAttribute('aria-pressed', active ? 'true' : 'false');
    cell.appendChild(button);
  });
  return cell;
}

function emptyStateMessage() {
  if (state.status === 'missing') return '视频榜还没有数据：采集还没跑出 page.json。';
  if (state.status === 'corrupt') return '视频榜数据异常：page.json 不可读，已停止显示（不会展示残留旧数据）。';
  return '这个来源当前没有条目。';
}

function renderBody() {
  const host = document.getElementById('vr-body');
  if (!host) return;
  host.textContent = '';                                        // 清空：切视图/切状态都不留旧行
  let items = [];
  if (state.status === 'ok') {
    const lists = state.data.lists || {};
    items = Array.isArray(lists[state.current]) ? lists[state.current] : [];
  }
  if (state.status !== 'ok' || items.length === 0) {
    const message = state.status === 'ok'
      ? LIST_LABELS[state.current] + '：这个来源当前没有条目。'
      : emptyStateMessage();
    host.appendChild(element('div', 'vr-empty', message));
    return;
  }
  const table = element('table', 'vr-table');
  const head = element('thead');
  const headRow = element('tr');
  ['#', '标题', '作者', '粉丝', '播放', '发布时间', '赞', '赞/小时', '近一小时涨速', '标记', '我的判断'].forEach((label, index) => {
    const th = element('th', index >= 5 && index <= 7 ? 'vr-num' : '', label);
    headRow.appendChild(th);
  });
  head.appendChild(headRow);
  table.appendChild(head);
  const body = element('tbody');
  items.forEach((item, index) => {
    const row = element('tr');
    row.dataset.key = item.key || '';
    row.appendChild(element('td', 'vr-rank', index + 1));
    row.appendChild(buildTitleCell(item));
    const author = element('td', null, '');
    author.appendChild(element('div', null, text(item.author, DASH)));
    const published = text(item.published_at, '');
    if (published) {
      const stamp = Date.parse(published);
      if (Number.isFinite(stamp)) {
        const time = document.createElement('time');
        time.dateTime = published;
        time.title = published;
        time.textContent = relativeTime(published);
        author.appendChild(element('div', 'vr-dim', time.textContent));
      } else {
        author.appendChild(element('div', 'vr-dim', published));
      }
    } else {
      author.appendChild(element('div', 'vr-dim', DASH));
    }
    row.appendChild(author);
    row.appendChild(element('td', 'vr-num', count(item.author_fans)));
    row.appendChild(element('td', 'vr-num', count(item.plays)));
    row.appendChild(element('td', 'vr-num', relativeTime(item.published_at)));
    row.appendChild(element('td', 'vr-num', count(item.likes)));
    row.appendChild(element('td', 'vr-num', rate(item.likes_per_hour)));
    row.appendChild(element('td', 'vr-num', rate(item.likes_delta_per_hour)));
    row.appendChild(buildFlagCell(item));
    row.appendChild(buildActionsCell(item));
    body.appendChild(row);
  });
  table.appendChild(body);
  // 窄视口时表格在壳内横向滚动，不撑破页面（外壳只负责 overflow，语义不变）
  const scroll = element('div', 'vr-table-scroll');
  scroll.appendChild(table);
  host.appendChild(scroll);
  host.appendChild(element('div', 'vr-foot', LIST_LABELS[state.current] + '：共 ' + items.length + ' 条'));
}

function render() {
  renderStatus();
  renderTabs();
  renderBody();
}

async function loadPage() {
  try {
    const response = await fetch(PAGE_URL, { cache: 'no-store' });
    if (!response.ok) throw new Error('HTTP ' + response.status);
    const data = await response.json();
    const status = ['ok', 'missing', 'corrupt'].indexOf(data && data.status) >= 0 ? data.status : 'corrupt';
    state = {
      status: status,
      current: state.current,
      data: status === 'ok' ? data : null,
      loadedAt: Date.now(),
      error: status === 'ok' ? null : (data && data.error ? data.error : null)
    };
    queues.clear();                                             // 数据换了，旧的判定队列作废
  } catch (error) {
    state = { status: 'corrupt', current: state.current, data: null, loadedAt: Date.now(),
              error: error && error.message ? error.message : '未知错误' };
    queues.clear();
  }
  render();
}

document.addEventListener('DOMContentLoaded', () => {
  document.getElementById('vr-tabs')?.addEventListener('click', (event) => {
    const button = event.target instanceof Element ? event.target.closest('.vr-tab') : null;
    if (!button || !button.dataset.list) return;
    state.current = button.dataset.list;
    render();
  });
  document.addEventListener('click', (event) => { onVerdictClick(event); });
  loadPage();
  setInterval(() => { if (state.status === 'ok') render(); }, 60000);   // 只重画，不重取
});
