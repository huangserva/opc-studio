// R02：远程讨论续页的落点按新增分组在最终列表里的真实位置算，不按“新读了多少条”推算。
// 合成夹具：本机缓存里已有旧评论（聚成若干组）+ 续页 token，点“读取更多”读回新内容；全程本机合成，不连 X。
const test = require('node:test'), assert = require('node:assert/strict'), fs = require('node:fs');
const { JSDOM } = require('jsdom');
const tick = () => new Promise(resolve => setImmediate(resolve));
const settle = async () => { for (let i = 0; i < 12; i++) await tick(); };
const ACCOUNT = 'owner';
const ROOT_POST = '1000';
const day = hours => new Date(Date.now() - hours * 3600000).toISOString();   // 默认范围是近 7 天
// 与页面同口径的默认时间窗：缓存页必须带同一组 start/end 才会被选中。
const dayKey = d => new Intl.DateTimeFormat('sv-SE', { timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit' }).format(d);
const shifted = n => { const d = new Date(dayKey(new Date()) + 'T00:00:00+08:00'); d.setDate(d.getDate() + n); return dayKey(d); };
const WINDOW = { start_time: new Date(shifted(-6) + 'T00:00:00+08:00').toISOString(),
  end_time: new Date(new Date(dayKey(new Date()) + 'T00:00:00+08:00').getTime() + 86400000).toISOString() };

const reader = (id, hours, parent = ROOT_POST) => ({ id, text: '读者评论 ' + id, username: 'reader' + id, name: '读者' + id,
  author_id: '2' + id, conversation_id: ROOT_POST, parent_id: parent, created_at: day(hours),
  metrics: {}, source: 'x', kind: 'post', images: [] });
const mine = (id, parent, hours) => ({ id, text: '我的回复 ' + id, username: ACCOUNT, name: '作者',
  author_id: '1', conversation_id: ROOT_POST, parent_id: parent, created_at: day(hours),
  metrics: {}, source: 'x', kind: 'post', images: [] });
const post = () => ({ id: ROOT_POST, text: '合成原帖', title: '合成原帖', username: ACCOUNT, name: '作者',
  author_id: '1', conversation_id: ROOT_POST, created_at: day(96), metrics: { reply_count: 60 },
  source: 'x', kind: 'post', images: [] });

const readers = (from, to, base) => Array.from({ length: to - from + 1 }, (_, i) => reader(base + (from + i), 90 - (from + i) * 0.3));
// 默认旧内容：40 条评论聚成 30 组（30 条读者评论 + 我给前 10 位读者各回一条，回复并进对方的组）。
const oldComments = () => [...readers(1, 30, 'old'), ...Array.from({ length: 10 }, (_, i) => mine('mine' + (i + 1), 'old' + (i + 1), 89 - (i + 1) * 0.3))];
const newGroups = (count, base = 'new') => Array.from({ length: count }, (_, i) => reader(base + (i + 1), 40 - (i + 1) * 0.3));

// old/fresh/full/allowToken 都是合成回执的可调项：old 是进页面时缓存里的内容，fresh 是续页读回来的内容，
// full 是不带 token 的整段读取内容（默认与 old 相同）。
function fixture(t, { old = oldComments, full = null, fresh = () => newGroups(20), allowToken = false } = {}) {
  const dom = new JSDOM(fs.readFileSync('public/feedback.html', 'utf8'),
    { url: 'http://127.0.0.1:8766/feedback.html', runScripts: 'outside-only', pretendToBeVisual: true });
  t.after(() => dom.window.close());
  const w = dom.window, d = w.document, calls = [];
  w.HTMLDialogElement.prototype.showModal = function () { this.open = true; };
  w.HTMLDialogElement.prototype.close = function () { this.open = false; };
  w.mountXOfficialPanel = async () => ({ readPost() {} });
  const seeded = old();
  const state = { comments: [...seeded], ids: seeded.map(c => c.id), next: 'token1' };
  const page = () => ({ ids: state.ids, next_token: state.next, mode: 'discussion', post_id: ROOT_POST, post_kind: '',
    start_time: WINDOW.start_time, end_time: WINDOW.end_time, synced_at: day(1) });
  const feedback = () => ({ account: { id: '111', username: ACCOUNT }, connected: true, posts: [post()],
    comments: state.comments, pages: { discussion: page() }, synced_at: day(1) });
  w.fetch = async (url, opts = {}) => {
    const target = String(url), call = { url: target, body: opts.body ? JSON.parse(opts.body) : undefined };
    calls.push(call);
    if (target === '/api/x-official/feedback') return { ok: true, json: async () => feedback() };
    if (target === '/api/x-official/status') return { ok: true, json: async () => ({ account: feedback().account, connected: true, jobs: [] }) };
    if (target === '/api/x-official/feedback-sync') {
      const batch = call.body.next_token ? fresh() : (full || old)();       // 没有 token 是整段读取
      state.comments = [...new Map([...state.comments, ...batch].map(c => [c.id, c])).values()];
      state.ids = [...new Set([...state.ids, ...batch.map(c => c.id)])];
      state.next = allowToken ? 'token2' : '';
      return { ok: true, json: async () => ({ page: page(), result_count: batch.length, partial: false }) };
    }
    return { ok: false, status: 404, json: async () => ({ error: '合成验收只读反馈列表' }) };
  };
  w.eval(fs.readFileSync('public/feedback-inbox.js', 'utf8'));
  return { w, d, calls, state };
}

const pageLabel = d => d.querySelector('#xf-reply-page').textContent;
const shown = d => [...d.querySelectorAll('#xf-replies .xf-comment .xf-body')].map(n => n.textContent);
const syncCalls = f => f.calls.filter(c => c.url === '/api/x-official/feedback-sync').length;
const open = async f => { await settle(); f.d.querySelector('[data-post]').click(); await settle(); };
const sortBy = async (f, value) => { f.d.querySelector('#xf-sort').value = value; f.d.querySelector('#xf-sort').dispatchEvent(new f.w.Event('change')); await settle(); };
// 一直点“读取更多”，直到真的发出同步请求为止（本机分页先翻完，末页才续页读取）。
const readMore = async f => {
  const before = f.calls.length;
  for (let i = 0; i < 4; i++) {
    const button = f.d.querySelector('#xf-reply-next');
    if (button.disabled) break;
    button.click(); await settle(); await settle();
    if (syncCalls(f)) break;
  }
  return f.calls.length - before;
};

test('40 条旧评论聚成 30 组后读回 20 个新组：首批新组立刻在第 2 页', async t => {
  const f = fixture(t);
  await open(f); await sortBy(f, 'earliest');
  assert.equal(pageLabel(f.d), '1 / 2 页 · 已读取 40 条', '起点：40 条旧评论聚成 30 组，正好两页');
  const extra = await readMore(f);
  // 新组排在最后（最早优先），第一批落在第 31–40 组即第 2 页；按条数推算会跳到第 3 页。
  assert.equal(pageLabel(f.d), '2 / 3 页 · 已读取 60 条', '首批新组在第 2 页，不能跳到第 3 页：' + pageLabel(f.d));
  assert.ok(shown(f.d).includes('读者评论 new1'), '首批新组要立刻看得到');
  assert.equal(syncCalls(f), 1, '续页只发一次同步');
  assert.ok(extra <= 2, '定位不发额外请求，只重载本机记录：' + extra + ' 次');
});

test('最新优先时新组排在最前，就停在第 1 页', async t => {
  const f = fixture(t);
  await open(f);
  assert.equal(pageLabel(f.d), '1 / 2 页 · 已读取 40 条');
  await readMore(f);
  assert.equal(pageLabel(f.d), '1 / 3 页 · 已读取 60 条', '最新优先时新组在最前，不该跳页：' + pageLabel(f.d));
  assert.ok(shown(f.d).includes('读者评论 new1'));
});

test('新内容并进旧分组时按它所在的分组定位：留在第 1 页', async t => {
  // 续页读回的是我给 old1–old10 的回复：不产生新分组，它们在第 11–20 组，也就是第 1 页。
  const f = fixture(t, { fresh: () => Array.from({ length: 10 }, (_, i) => mine('again' + (i + 1), 'old' + (i + 1), 30 - i * 0.1)) });
  await open(f); await sortBy(f, 'earliest');
  await readMore(f);
  assert.equal(pageLabel(f.d), '1 / 2 页 · 已读取 50 条', '按新分组真实位置定位，不按条数跳页：' + pageLabel(f.d));
  assert.ok(shown(f.d).some(text => text.includes('我的回复 again1')), '这条新回复就在本页');
});

test('重复分组：续页里混着旧 id，不该被当成新分组', async t => {
  // 30 条旧评论（30 组）+ 续页返回 20 条旧 id 与 20 个新组：去重后 50 组，新组在第 31 位起 → 第 2 页。
  const f = fixture(t, { old: () => readers(1, 30, 'old'),
    fresh: () => [...readers(1, 20, 'old'), ...newGroups(20)] });
  await open(f); await sortBy(f, 'earliest');
  assert.equal(pageLabel(f.d), '1 / 2 页 · 已读取 30 条');
  await readMore(f);
  assert.equal(pageLabel(f.d), '2 / 3 页 · 已读取 50 条', '重复 id 不该算成新分组：' + pageLabel(f.d));
  assert.ok(shown(f.d).includes('读者评论 new1'), '新组在这一页');
});

test('空缓存整段读取：全部是新分组，停在第 1 页且不多发请求', async t => {
  // 本机还没有这页讨论：点“读取回复”走的是整段读取（不带续页 token），读完仍落在第 1 页。
  const f = fixture(t, { old: () => [], full: () => newGroups(20) });
  await open(f);
  assert.equal(pageLabel(f.d), '尚无回复', '起点：本机还没有这页讨论');
  f.d.querySelector('#xf-read-comments').click(); await settle(); await settle();
  assert.equal(pageLabel(f.d), '1 / 1 页 · 已读取 20 条', '整段读取后停在第 1 页：' + pageLabel(f.d));
  assert.equal(syncCalls(f), 1);
  assert.ok(f.calls.every(c => ['/api/x-official/feedback', '/api/x-official/status', '/api/x-official/feedback-sync'].includes(c.url)),
    '定位不发额外请求：' + [...new Set(f.calls.map(c => c.url))].join(' '));
});

test('读回来的内容不落在本页时停在当前页，不乱翻', async t => {
  // 续页读回一条超出当前时间窗的旧评论：不进列表，位置算不出来就保持当前页。
  const f = fixture(t, { fresh: () => [reader('outside', 24 * 40)] });
  await open(f); await sortBy(f, 'earliest');
  await readMore(f);
  assert.equal(pageLabel(f.d), '2 / 2 页 · 已读取 40 条', '算不出新分组时停在当前页：' + pageLabel(f.d));
});
test('续页没有新 id（同一页又发了一遍）时留在当前页', async t => {
  const f = fixture(t, { fresh: () => readers(1, 5, 'old') });
  await open(f); await sortBy(f, 'earliest');
  await readMore(f);
  assert.equal(pageLabel(f.d), '2 / 2 页 · 已读取 40 条', '没有新增分组就别乱翻页：' + pageLabel(f.d));
});
