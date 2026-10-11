// 回收站批量删除的前端静态测试：勾选与全选范围、类型/时间筛选、确认框措辞、逐条结果如实显示。
// 只跑 jsdom + 假 fetch：不发真实请求、不起服务、不碰任何本机数据。
const test = require('node:test'), assert = require('node:assert/strict'), fs = require('node:fs');
const { JSDOM } = require('jsdom');
const tick = () => new Promise(resolve => setImmediate(resolve));
const settle = async () => { for (let i = 0; i < 10; i++) await tick(); };
const id = n => n.repeat(32);
const ok = data => ({ ok: true, json: async () => data });
const bad = (error, code = 400) => ({ ok: false, status: code, json: async () => ({ error }) });
const DAY = 86400000;
const at = days => new Date(Date.now() - days * DAY).toISOString();
const format = n => n.toString(16).padStart(32, '0');

const base = extra => ({ id: id('a'), kind: 'link', received_at: at(1), url: 'https://example.com/post', title: '',
  reason: '', status: 'resolved', error: '', document_id: id('b'), revision: 2, file_path: '', file: {}, media_kind: '',
  display_kind: 'link', text_chars: 0, content_scope: '', meta: {}, outcome: 'stored', state: '已存入知识库',
  document_shares: 1, document_owned: true, recognition: {}, analysis: {},
  knowledge_url: '/knowledge.html?document=' + id('b'), ...extra });

const FIVE = [
  base({ id: id('1'), kind: 'link', display_kind: 'link', received_at: at(1), document_id: id('d1') }),
  base({ id: id('2'), kind: 'text', display_kind: 'text', received_at: at(3), document_id: id('d2'), url: '' }),
  base({ id: id('3'), kind: 'image', display_kind: 'image', received_at: at(10), document_id: id('d3'),
    file_path: 'media/a.png', media_kind: 'image', file: { name: 'a.png', bytes: 2048, format: 'PNG', width: 24, height: 16 } }),
  base({ id: id('4'), kind: 'link', display_kind: 'combo', received_at: at(0), document_id: id('d4'),
    file_path: 'media/b.png', media_kind: 'image', file: { name: 'b.png', bytes: 4096, format: 'PNG', width: 8, height: 8 } }),
  base({ id: id('5'), kind: 'video', display_kind: 'video', received_at: at(40), document_id: id('d5'),
    file_path: 'media/c.mp4', media_kind: 'video', file: { name: 'c.mp4', bytes: 8192, format: 'MP4' } }),
];

function previewOf(ids, options) {
  const picked = FIVE.filter(item => ids.includes(item.id));
  const kinds = {};
  for (const item of picked) kinds[item.display_kind] = (kinds[item.display_kind] || 0) + 1;
  const documents = options.documents || { total: picked.length, would_remove: picked.length, kept: 0,
    kept_shared: 0, kept_not_owned: 0, kept_unknown: 0 };
  return { schema: 'intake-delete-preview/v1', requested: ids.length, count: picked.length, kinds,
    files: picked.filter(item => item.file_path).length, documents,
    cards: options.cards || { drops: 2, cards: 2 }, missing: ids.filter(x => !picked.some(i => i.id === x)),
    rejected: [], items: picked, note: '' };
}

async function fixture(t, options = {}) {
  const dom = new JSDOM(fs.readFileSync('public/intake.html', 'utf8'),
    { url: 'http://127.0.0.1:8766/intake.html', runScripts: 'outside-only', pretendToBeVisual: true });
  t.after(() => dom.window.close());
  const w = dom.window, d = w.document, calls = [];
  const items = options.items || FIVE;
  const asked = { preview: [], many: [] };
  w.confirm = message => { (w.confirms = w.confirms || []).push(message); return options.confirm !== false; };
  w.fetch = async (url, opts = {}) => {
    const target = String(url);
    calls.push({ url: target, opts });
    if (target === '/api/intake') return ok({ schema: 'intake/v1', items, note: '' });
    if (target === '/api/intake/analysis-status') return ok({ schema: 'intake-analysis-status/v1', supported: true });
    if (target === '/api/intake/delete-preview') {
      if (options.noPreview) return bad('操作不存在', 404);
      const ids = JSON.parse(opts.body).ids;
      asked.preview.push(ids);
      return ok(previewOf(ids, options));
    }
    if (target === '/api/intake/delete-many') {
      const ids = JSON.parse(opts.body).ids;
      asked.many.push(ids);
      const deleted = ids.filter(x => !(options.missing || []).includes(x));
      const skipped = ids.filter(x => (options.missing || []).includes(x))
        .map(x => ({ id: x, ok: false, reason: '回收项不存在，请刷新列表' }));
      return ok({ schema: 'intake-delete-many/v1', requested: ids.length,
        deleted: deleted.map(x => ({ id: x, ok: true, deleted: true, document_id: id('d'), document_removed: true,
          document_kept_reason: '', document_error: '', file_removed: false, file_error: '', records_error: '',
          ledger_error: '', analysis_removed: 1, seen_removed: 0, cards_kept: true })),
        skipped, summary: { requested: ids.length, deleted: deleted.length, skipped: skipped.length,
          documents_removed: deleted.length, documents_kept: options.keptDocs || 0, files_removed: 0,
          analysis_removed: deleted.length, seen_removed: 0, cards_kept: options.cardCount === undefined ? deleted.length : options.cardCount, cards_kept_drops: deleted.length, partial: 0, errors: [] },
        note: '' });
    }
    return bad('接口不存在');
  };
  w.eval(fs.readFileSync('public/intake.js', 'utf8'));
  await settle();
  return { w, d, calls, asked, rows: () => [...d.querySelectorAll('#in-list .in-item')],
    ids: () => [...d.querySelectorAll('#in-list .in-item')].map(node => node.dataset.id),
    checks: () => [...d.querySelectorAll('#in-list [data-check]')],
    status: () => d.querySelector('#in-status').textContent, confirms: () => w.confirms || [],
    batch: () => d.querySelector('#in-batch'), batchCount: () => d.querySelector('#in-batch-count').textContent,
    note: () => d.querySelector('#in-batch-note').textContent, count: () => d.querySelector('#in-count').textContent,
    pick: ids => { for (const one of ids) { const box = d.querySelector(`#in-list [data-check="${one}"]`);
      if (box) { box.checked = true; box.dispatchEvent(new w.Event('change', { bubbles: true })); } } },
    all: () => { const box = d.querySelector('#in-all'); box.checked = true; box.dispatchEvent(new w.Event('change', { bubbles: true })); },
    filter: (kind, value) => {
      if (kind !== null) d.querySelector(`#in-views [data-kind="${kind}"]`).click();
      if (value !== undefined) { const select = d.querySelector('#in-range'); select.value = value;
        select.dispatchEvent(new w.Event('change', { bubbles: true })); }
    },
    remove: ids => { for (const one of ids) { const box = d.querySelector(`#in-list [data-check="${one}"]`);
      if (box) { box.checked = false; box.dispatchEvent(new w.Event('change', { bubbles: true })); } } },
    run: () => { d.querySelector('#in-batch-delete').click(); return settle(); } };
}
test('the list offers a checkbox per item and a batch bar', async t => {
  const f = await fixture(t);
  assert.equal(f.rows().length, 5);
  assert.equal(f.checks().length, 5);
  assert.equal(f.batch().hidden, false);
  assert.equal(f.batchCount(), '已选 0 条');
  assert.match(f.note(), /勾选后可以批量删除/);
  assert.equal(f.d.querySelector('#in-batch-delete').disabled, true);
  assert.ok(f.d.querySelector('#in-views [data-kind="combo"]'));
});

test('the type filter puts every item in exactly one bucket', async t => {
  const f = await fixture(t);
  f.filter('combo');
  await settle();
  assert.deepEqual(f.ids(), [id('4')]);
  assert.equal(f.count(), '1 条回收项');
  f.filter('image');
  await settle();
  assert.deepEqual(f.ids(), [id('3')]);
  f.filter('link');
  await settle();
  assert.deepEqual(f.ids(), [id('1')]);          // 带附件的那条已归到「组合」
  f.filter('');
  await settle();
  assert.equal(f.rows().length, 5);
});

test('the time range filter narrows the list by received time', async t => {
  const f = await fixture(t);
  f.filter('', '7');
  await settle();
  assert.deepEqual(f.ids(), [id('1'), id('2'), id('4')]);
  f.filter('', '1');
  await settle();
  assert.deepEqual(f.ids(), [id('4')]);
  f.filter('', '');
  await settle();
  assert.equal(f.rows().length, 5);
  const from = f.d.querySelector('#in-from'), to = f.d.querySelector('#in-to');
  from.value = new Date(Date.now() - 5 * DAY).toISOString().slice(0, 10);
  from.dispatchEvent(new f.w.Event('change', { bubbles: true }));
  to.value = new Date(Date.now() - 2 * DAY).toISOString().slice(0, 10);
  to.dispatchEvent(new f.w.Event('change', { bubbles: true }));
  await settle();
  assert.deepEqual(f.ids(), [id('2')]);          // 只剩 3 天前那条落在自定义区间里
});

test('select all picks only the current filtered result', async t => {
  const f = await fixture(t);
  f.filter('', '7');
  await settle();
  f.all();
  await settle();
  assert.equal(f.batchCount(), '已选 3 条');
  assert.equal(f.checks().filter(box => box.checked).length, 3);
  f.filter('', '1');
  await settle();
  assert.equal(f.batchCount(), '已选 1 条');
  assert.match(f.note(), /另有 2 条已选但不在当前筛选结果里，不会被删/);
});

test('the batch confirm lists counts, type distribution and what the knowledge base loses', async t => {
  const f = await fixture(t);
  f.all();
  await settle();
  await f.run();
  assert.deepEqual(f.asked.preview, [[id('1'), id('2'), id('3'), id('4'), id('5')]]);
  const message = f.confirms()[0];
  assert.match(message, /批量删除这 5 条回收项？/);
  assert.match(message, /类型分布：链接 1 · 文字 1 · 图片 1 · 组合 1 · 视频 1/);
  assert.match(message, /会一起删掉：5 条回收项、它们的即时分析结果、3 个本机媒体文件/);
  assert.match(message, /知识库：将一并移除 5 篇正文（回收站自己存的、这次删掉最后一条引用；含分块与检索索引）/);
  assert.match(message, /提示词卡：涉及 2 张已生成的卡（来自 2 条回收项），不删，会在卡库标成「来源已删除」/);
  assert.match(message, /这一步不能撤销/);
});

test('the batch confirm keeps knowledge-first bodies and shared bodies promised safe', async t => {
  const f = await fixture(t, { documents: { total: 5, would_remove: 2, kept: 3, kept_shared: 1, kept_not_owned: 1, kept_unknown: 1 } });
  f.all();
  await settle();
  await f.run();
  const message = f.confirms()[0];
  assert.match(message, /将一并移除 2 篇正文/);
  assert.match(message, /1 篇还有未选中的回收项引用同一篇正文/);
  assert.match(message, /1 篇不是回收站存的（知识库先收藏或其他入口）/);
  assert.match(message, /1 篇出处读不到，保留不删/);
});

test('the batch delete posts once and reports every result honestly', async t => {
  const f = await fixture(t, { missing: [id('5')] });
  f.pick([id('1'), id('2'), id('3'), id('4'), id('5')]);
  await settle();
  await f.run();
  assert.deepEqual(f.asked.many, [[id('1'), id('2'), id('3'), id('4'), id('5')]]);
  assert.deepEqual(f.ids(), [id('5')]);                       // 没删掉的那条仍在列表里
  assert.match(f.status(), /已删除 4 条/);
  assert.match(f.status(), /一并删了 4 篇知识库正文/);
  assert.match(f.status(), /1 条未删：[0-9a-f]{8}：回收项不存在，请刷新列表/);
  assert.equal(f.batchCount(), '已选 1 条');                   // 已删的勾选被清掉
});

test('a partial batch names the item that did not finish', async t => {
  const f = await fixture(t);
  f.pick([id('1'), id('2')]);
  await settle();
  await f.run();
  assert.match(f.status(), /已删除 2 条/);
  assert.match(f.status(), /2 篇知识库正文/);
  assert.doesNotMatch(f.status(), /未删|未完成/);
});

test('cancelling the batch confirm deletes nothing', async t => {
  const f = await fixture(t, { confirm: false });
  f.all();
  await settle();
  await f.run();
  assert.equal(f.asked.preview.length, 1);
  assert.equal(f.asked.many.length, 0);
  assert.equal(f.rows().length, 5);
  assert.equal(f.status(), '未删除。');
});

test('more than fifty selected is refused before any request', async t => {
  const many = Array.from({ length: 51 }, (_, n) => base({ id: format(n), document_id: format(100 + n),
    display_kind: 'text', kind: 'text', url: '' }));
  const f = await fixture(t, { items: many });
  f.all();
  await settle();
  assert.match(f.note(), /一次最多删 50 条，请减少选择（当前 51 条）/);
  await f.run();
  assert.match(f.status(), /一次最多删 50 条，当前选了 51 条/);
  assert.equal(f.asked.preview.length, 0);
  assert.equal(f.asked.many.length, 0);
  assert.equal(f.rows().length, 51);
});

test('a backend without the batch routes refuses instead of guessing', async t => {
  const f = await fixture(t, { noPreview: true });
  f.pick([id('1')]);
  await settle();
  await f.run();
  assert.match(f.status(), /没能拿到删除预览/);
  assert.match(f.status(), /未删除任何一条/);
  assert.equal(f.asked.many.length, 0);
  assert.equal(f.rows().length, 5);
});

test('a selected item outside the current filter is not deleted', async t => {
  const f = await fixture(t);
  f.filter('', '7');
  await settle();
  f.all();
  await settle();
  f.filter('', '1');
  await settle();
  await f.run();
  assert.deepEqual(f.asked.many, [[id('4')]]);
  f.filter(null, '');
  await settle();
  assert.deepEqual(f.ids(), [id('1'), id('2'), id('3'), id('5')]);   // 其余三条只是被筛掉，没被删
});

test('the batch bar disappears and the count stays honest when the list is empty', async t => {
  const f = await fixture(t, { items: [] });
  assert.equal(f.batch().hidden, true);
  assert.match(f.d.querySelector('#in-list').textContent, /还没有回收项/);
});


test('batch summary shows final document and real card counts with explicit units', async t => {
  const f = await fixture(t, { cardCount: 3 });
  f.pick([id('1'), id('2')]); await settle(); await f.run();
  assert.match(f.status(), /保留 3 张提示词卡/);
  assert.match(f.status(), /一并删了 2 篇知识库正文/);
  assert.doesNotMatch(f.status(), /篇正文按规则保留/);
});

test('unknown card count is omitted rather than coerced to zero', async t => {
  const f = await fixture(t, { cardCount: null, keptDocs: 1 });
  f.pick([id('1')]); await settle(); await f.run();
  assert.match(f.status(), /1 篇正文按规则保留/);
  assert.doesNotMatch(f.status(), /张提示词卡|null|undefined/);
});
