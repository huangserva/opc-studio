const test = require('node:test'), assert = require('node:assert/strict'), fs = require('node:fs');
const { JSDOM } = require('jsdom');
const tick = () => new Promise(resolve => setImmediate(resolve));
const settle = async () => { for (let i = 0; i < 6; i++) await tick(); };
const dealId = 'a'.repeat(32), proposalId = 'b'.repeat(32), clueId = 'c'.repeat(32);
const response = (data, ok = true) => ({ ok, json: async () => data });

// ---------------------------------------------------------------- schedules page

async function scheduleFixture(t, options = {}) {
  const dom = new JSDOM(fs.readFileSync('public/schedules.html', 'utf8'),
    { url: 'http://127.0.0.1:8766/schedules.html', runScripts: 'outside-only', pretendToBeVisual: true });
  t.after(() => dom.window.close());
  const w = dom.window, d = w.document, calls = [];
  const task = { id: 'd'.repeat(32), revision: 1, name: '商单定时分析', body: '关注交付与到账', kind: 'deals_review',
    assistant_id: 'isolated', tool_groups: [], profile_id: 'fake', cwd: '/Users/example/studio',
    frequency: 'interval', minutes: 240, weekdays: [0], at: '09:00', timeout: 900, enabled: true,
    allow_commands: false, next_run: Date.now() / 1000 + 7200, result_destination: 'runs', result_category: 'other',
    clue_id: '', executor_name: '商单分析（Pi）' };
  const listing = { tasks: options.tasks || [task], default_cwd: '/Users/example/studio', default_profile_id: 'fake',
    models: [{ profile_id: 'fake', display_name: '合成模型', configured: true }],
    scheduler_health: { running: true, last_tick: '2026-10-08T02:00:00Z', last_error: null },
    concurrency: { max: 2, active: 0, available: 2 } };
  w.setInterval = () => 0; w.setTimeout = () => 0; w.clearTimeout = () => {}; w.confirm = () => true;
  w.HTMLDialogElement.prototype.showModal = function () { this.open = true; };
  w.HTMLDialogElement.prototype.close = function () { this.open = false; };
  w.fetch = async (url, opts = {}) => {
    const call = { url: String(url), opts, body: opts.body && JSON.parse(opts.body) }; calls.push(call);
    if (call.url === '/api/schedules') return response(JSON.parse(JSON.stringify(listing)));
    if (call.url === '/api/clues?view=all') return response({ items: [] });
    if (call.url.startsWith('/api/schedules/runs?')) {
      const runs = options.runs || [];
      return response({ runs, total: runs.length, page: 1, page_size: 50, has_more: false });
    }
    if (call.url.startsWith('/api/schedules/run/')) return response((options.runs || [])[0]);
    return response({ id: task.id, ok: true });
  };
  w.eval(fs.readFileSync('public/schedules.js', 'utf8'));
  await settle();
  return { w, d, calls, form: d.querySelector('#task-form'), field: name => d.querySelector(`#task-form [name=${name}]`) };
}
function change(f, element, value) {
  if (element.type === 'checkbox') element.checked = value; else element.value = value;
  element.dispatchEvent(new f.w.Event('change', { bubbles: true }));
  return element;
}

test('deal review is offered as a task type with its own executor label and 240 minute default', async t => {
  const f = await scheduleFixture(t, { tasks: [] });
  assert.match(f.d.querySelector('#tasks').textContent, /暂无定时任务/);
  f.d.querySelector('#new-task').click();
  change(f, f.field('kind'), 'deals_review');
  assert.equal(f.field('minutes').value, '240');
  assert.equal(f.field('assistant_id').disabled, true);
  assert.equal(f.field('allow_commands').disabled, true);
  assert.equal(f.field('result_destination').disabled, true);
  assert.equal(f.field('result_destination').value, 'runs');
  assert.match(f.d.querySelector('#assistant-help').textContent, /不会自动改商单/);
  assert.match(f.d.querySelector('#assistant-help').textContent, /每周一 09:00/);
});

test('a manually chosen interval survives switching task types', async t => {
  const f = await scheduleFixture(t, { tasks: [] });
  f.d.querySelector('#new-task').click();
  change(f, f.field('kind'), 'deals_review');
  change(f, f.field('minutes'), '60');
  change(f, f.field('kind'), 'pi');
  change(f, f.field('kind'), 'deals_review');
  assert.equal(f.field('minutes').value, '60');
});

test('saving a deal review task posts an isolated task with no commands and no clue delivery', async t => {
  const f = await scheduleFixture(t, { tasks: [] });
  f.d.querySelector('#new-task').click();
  const form = f.form;
  form.elements.name.value = '商单分析';
  form.elements.body.value = '关注交付与到账';
  change(f, form.elements.kind, 'deals_review');
  form.elements.enabled.checked = true;
  form.dispatchEvent(new f.w.Event('submit', { cancelable: true }));
  await settle();
  const saved = f.calls.filter(c => c.url === '/api/schedules/save').pop();
  assert.equal(saved.body.kind, 'deals_review');
  assert.equal(saved.body.minutes, 240);
  assert.equal(saved.body.assistant_id, 'isolated');
  assert.deepEqual(saved.body.tool_groups, []);
  assert.equal(saved.body.allow_commands, false);
  assert.equal(saved.body.result_destination, 'runs');
});

test('deal review task detail states the limited scope and that suggestions still need confirming', async t => {
  const f = await scheduleFixture(t);
  f.d.querySelector('[data-action="detail"]').click();
  const meta = f.d.querySelector('#task-meta').textContent;
  assert.match(meta, /商单分析（Pi）/);
  assert.match(meta, /每次最多重扫 6 笔/);
  assert.match(meta, /需你确认后才会写入台账/);
  assert.match(f.d.querySelector('#tasks').textContent, /商单分析（Pi）/);
});

test('deal review run receipts show counts, the weekly report and honest leftovers', async t => {
  const run = { id: 'e'.repeat(32), task_id: 'd'.repeat(32), trigger: 'scheduled', status: 'partial',
    started_at: '2026-10-08T02:00:00Z', finished_at: '2026-10-08T02:03:00Z', exit_code: null,
    snapshot: { kind: 'deals_review', name: '商单分析', revision: 2, executor_name: '商单分析（Pi）' },
    output: JSON.stringify({ progress: { overdue: 2, due_soon: 1, awaiting_payment: 3, stalled: 1, needs_review: 9, checked: 34 },
      evidence: { checked: 6, new_evidence: 4, proposals: 2, pending: 5, deferred: 28, has_more: true },
      conflicts: { clues: 2, possible: 1, deferred: 0, has_more: false }, report: { week: '2026-W41' },
      has_more: true, errors: ['合成品牌：微信来源核对失败 · 合成原因'], summary: '商单分析摘要' }) };
  const f = await scheduleFixture(t, { runs: [run] });
  f.d.querySelector('#run-tab').click();
  await settle();
  const text = f.d.querySelector('#runs').textContent;
  assert.match(text, /检查 34 笔/);
  assert.match(text, /截止已过 2/);
  assert.match(text, /待核对 9 个/);
  assert.match(text, /新增待确认建议 2 条/);
  assert.match(text, /可能冲突 1 条/);
  assert.match(text, /周报 2026-W41 已生成/);
  assert.match(text, /本轮限量未完成/);
  assert.match(text, /微信来源核对失败/);
  assert.ok(!text.includes('运行结果格式异常'));
});

test('a malformed deal review receipt falls back instead of pretending to be empty', async t => {
  const run = { id: 'f'.repeat(32), task_id: 'd'.repeat(32), trigger: 'scheduled', status: 'failed',
    started_at: '2026-10-08T02:00:00Z', finished_at: '2026-10-08T02:00:01Z', exit_code: null,
    snapshot: { kind: 'deals_review', name: '商单分析', revision: 2 }, output: '商单分析入口未连接' };
  const f = await scheduleFixture(t, { runs: [run] });
  f.d.querySelector('#run-tab').click();
  await settle();
  assert.match(f.d.querySelector('#runs').textContent, /商单分析入口未连接/);
});

// ---------------------------------------------------------------- deals page

async function dealFixture(t, options = {}) {
  const dom = new JSDOM(fs.readFileSync('public/deals.html', 'utf8'),
    { url: 'http://127.0.0.1:8766/deals.html', runScripts: 'outside-only' });
  const w = dom.window, d = w.document;
  t.after(() => w.close());
  w.crypto.randomUUID = () => require('node:crypto').randomUUID();
  w.HTMLElement.prototype.scrollIntoView = () => {}; w.HTMLElement.prototype.scrollTo = () => {};
  w.HTMLDialogElement.prototype.showModal = function () { this.open = true; };
  w.HTMLDialogElement.prototype.close = function () { this.open = false; this.dispatchEvent(new w.Event('close')); };
  const deal = { id: dealId, revision: 4, title: '合成客户乙 记忆合作', customer: '合成客户乙', stage: 'writing', quote: null,
    currency: null, quote_confirmed: false, needs_review: false, settled: false, archived: false,
    expected_deliverables: null, original_group: '', original_text: '', reported_payment: 'unknown', receipts: [],
    deliverables: [], publications: [], sources: [], audit: [], evidence: [], timeline: [],
    cash: { status: 'unpaid', outstanding: null, totals: { CNY: '0.00', USD: '0.00' } } };
  const optionsData = { stages: { writing: '待写' }, stage_groups: { '创作交付': ['writing'] }, delivery_stages: {},
    currencies: ['CNY'], works: [], bindings: [] };
  const review = options.review || {
    run_id: 'r'.repeat(32), generated_at: '2026-10-08T02:00:00Z', last_clue_scan_at: '2026-10-08T02:00:00Z',
    findings: { overdue: [{ deal_id: dealId, title: '合成客户乙 记忆合作', detail: '交稿截止已过 · 2026-10-01 · 撰写中', days: 7, ref: '' }],
      needs_review: [{ deal_id: '', title: '', detail: '待核对 9 个', days: 0, ref: '' }] },
    conflicts: [{ clue_id: clueId, clue_title: '新的记忆赛道邀约', deal_id: dealId, deal_title: '合成客户乙 记忆合作',
      reason: '同赛道，需核对排他范围', evidence: [], at: '2026-10-08T02:00:00Z' }],
    proposals: [{ id: proposalId, deal_id: dealId, title: '合成客户乙 记忆合作', operation: 'update',
      changes: { stage: 'draft' }, reason: '微信来源写明初稿完成', revision: 4 }],
    reports: [{ week: '2026-W41', generated_at: '2026-10-08T02:00:00Z' }] };
  const calls = [];
  w.fetch = async (url, o = {}) => {
    const body = o.body && JSON.parse(o.body); calls.push({ url: String(url), body });
    if (url === '/api/deals/options') return response(optionsData);
    if (url === '/api/deals/review') return response(review);
    if (String(url).startsWith('/api/deals/review/report')) {
      if (options.badReport) return response({ error: '该周周报尚未生成' }, false);
      return response({ week: '2026-W41', start: '2026-10-19T00:00:00+08:00', end: '2026-10-26T00:00:00+08:00',
        markdown: '# 商单周报 · 2026-W41\n## 到账\n- 实际到账 ¥1000.0 CNY' });
    }
    if (String(url).startsWith('/api/deals/apply-proposal')) return response({ ...deal, stage: 'draft', revision: 5 });
    if (String(url).startsWith('/api/deals?')) {
      return response({ deals: [deal], summary: { month: '2026-10', signed: 0, closed: 0, published: 0,
        received: { CNY: '0.00', USD: '0.00' }, outstanding: { CNY: '0.00', USD: '0.00' }, needs_review: 0,
        unknown_dates: 0, unknown_quote: 0, receipt_count: 0, recorded_quote: { CNY: '0.00', USD: '0.00' },
        recorded_quote_count: 0 }, stage_counts: { writing: 1 }, imports: [], total: 1 });
    }
    return response(deal);
  };
  w.eval(fs.readFileSync('public/deals.js', 'utf8'));
  await settle();
  return { w, d, calls, deal, review };
}

test('the deals page shows the latest review with reminders, conflicts and pending proposals', async t => {
  const { d } = await dealFixture(t);
  const box = d.querySelector('#dl-analysis');
  assert.equal(box.hidden, false);
  const text = box.textContent;
  assert.match(text, /最近一次商单分析/);
  assert.match(text, /交稿截止已过/);
  assert.match(text, /待核对 9 个/);
  assert.match(text, /新的记忆赛道邀约/);
  assert.match(text, /同赛道，需核对排他范围/);
  assert.match(text, /微信来源写明初稿完成/);
  assert.match(text, /不会自动改商单/);
  assert.ok(d.querySelector('[data-apply-proposal]'));
  assert.ok(d.querySelector('[data-report="2026-W41"]'));
});

test('confirming a scheduled proposal uses the existing apply-proposal flow only', async t => {
  const { w, d, calls } = await dealFixture(t);
  d.querySelector('[data-apply-proposal]').click();
  await settle();
  const applied = calls.filter(c => c.url === '/api/deals/apply-proposal');
  assert.equal(applied.length, 1);
  assert.equal(applied[0].body.proposal_id, proposalId);
  assert.equal(calls.filter(c => c.url === '/api/deals' && c.body).length, 0);
  assert.match(d.querySelector('#dl-status').textContent, /已保存/);
});

test('weekly reports open read-only and a missing report says so', async t => {
  const { d } = await dealFixture(t);
  d.querySelector('[data-report]').click();
  await settle();
  assert.equal(d.querySelector('#dl-report-dialog').open, true);
  assert.match(d.querySelector('#dl-report-body').textContent, /商单周报 · 2026-W41/);
  assert.match(d.querySelector('#dl-report-note').textContent, /只保存在本机/);
});

test('a deals page without any review stays explicit instead of blank', async t => {
  const { d } = await dealFixture(t, { review: { run_id: '', generated_at: '', findings: {}, conflicts: [], proposals: [], reports: [] } });
  assert.match(d.querySelector('#dl-analysis').textContent, /还没有分析记录/);
  assert.match(d.querySelector('#dl-analysis').textContent, /定时任务/);
});

test('review markup is escaped and never becomes HTML', async t => {
  const { d } = await dealFixture(t, { review: { run_id: 'r'.repeat(32), generated_at: '2026-10-08T02:00:00Z',
    findings: { overdue: [{ deal_id: dealId, title: '<img src=x>', detail: '<script>bad</script>', days: 1, ref: '' }] },
    conflicts: [], proposals: [], reports: [] } });
  assert.equal(d.querySelector('#dl-analysis script'), null);
  assert.equal(d.querySelector('#dl-analysis img'), null);
});
