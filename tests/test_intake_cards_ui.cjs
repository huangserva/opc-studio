const test = require('node:test'), assert = require('node:assert/strict'), fs = require('node:fs');
const { JSDOM } = require('jsdom');
const tick = () => new Promise(resolve => setImmediate(resolve));
const settle = async () => { for (let i = 0; i < 10; i++) await tick(); };
const id = n => n.repeat(32);
const ok = data => ({ ok: true, json: async () => data });
const bad = (error, code = 400) => ({ ok: false, status: code, json: async () => ({ error }) });
const response = (data, isOk = true) => ({ ok: isOk, json: async () => data });

const card = (extra = {}) => ({
  id: id('c'), created_at: '2026-10-09T02:00:00+00:00', updated_at: '2026-10-09T02:00:00+00:00',
  drop_id: id('a'), document_id: id('b'), prompt: '旧码头黄昏，蹲拍视角，水面倒映天空，人物只留背影',
  model: 'qi21', scene: '公众号头图', note: '这个开头方式可以借', status: 'candidate', reason: '',
  model_name: 'synthetic-model', run_id: id('r'),
  refs: [{ ref: 'e0', label: '知识库正文', document_id: id('b'), drop_id: id('a'), start: 0, end: 64, chars: 64,
           excerpt: '黄昏的旧码头先把木头泡软…', sha256: '0123456789abcdef' }],
  source_state: 'verified',
  source: { kind: 'text', kind_label: '文字', title: '创意回收 · 文字', received_at: '2026-10-09T01:50:00+00:00',
            knowledge_url: '/knowledge.html?document=' + id('b'), reason: '这个开头方式可以借' },
  fill_url: '/lab.html?kind=image&card=' + id('c'), ...extra,
});

async function fixture(t, options = {}) {
  const dom = new JSDOM(fs.readFileSync('public/cards.html', 'utf8'),
    { url: 'http://127.0.0.1:8766/cards.html', runScripts: 'outside-only', pretendToBeVisual: true });
  t.after(() => dom.window.close());
  const w = dom.window, d = w.document, calls = [];
  let items = options.items || [card()];
  let counts = { candidate: items.filter(x => x.status === 'candidate').length,
                 confirmed: items.filter(x => x.status === 'confirmed').length,
                 rejected: items.filter(x => x.status === 'rejected').length };
  w.fetch = async (url, opts = {}) => {
    const target = String(url);
    calls.push({ url: target, opts });
    if (target.startsWith('/api/cards?')) {
      const params = new w.URLSearchParams(target.split('?')[1]);
      const status = params.get('status') || '', query = (params.get('q') || '').trim();
      const kind = params.get('kind') || '';
      const channelOf = item => (String(item.source?.reason || '').startsWith('情报采集') ? 'intel' : (item.source?.channel || item.source?.kind || ''));
      const shown = items.filter(x => (!status || x.status === status) &&
        (!kind || channelOf(x) === kind) &&
        (!query || [x.prompt, x.scene, x.note].some(value => String(value).includes(query))));
      return ok({ schema: 'intake-cards/v1', total: shown.length, items: shown, counts, pending_drops: 2, note: '', view: params.get('view') || 'source' });
    }
    const removed = target.match(/^\/api\/cards\/([a-f0-9]{32})\/delete$/);
    if (removed) {
      const before = items.length;
      items = items.filter(item => item.id !== removed[1]);
      counts = { candidate: items.filter(x => x.status === 'candidate').length,
                 confirmed: items.filter(x => x.status === 'confirmed').length,
                 rejected: items.filter(x => x.status === 'rejected').length };
      return ok({ schema: 'intake-cards-delete/v1', deleted_cards: before - items.length });
    }
    if (target === '/api/cards/cleanup') {
      const body = opts.body ? JSON.parse(opts.body) : {};
      const before = items.length;
      items = items.filter(item => item.drop_id !== body.drop_id);
      counts = { candidate: items.filter(x => x.status === 'candidate').length,
                 confirmed: items.filter(x => x.status === 'confirmed').length,
                 rejected: items.filter(x => x.status === 'rejected').length };
      return ok({ schema: 'intake-cards-delete/v1', deleted_cards: before - items.length, drop_id: body.drop_id });
    }
    const decision = target.match(/^\/api\/cards\/([a-f0-9]{32})\/(confirm|reject|restore|note)$/);
    if (decision) {
      const [, cardId, action] = decision;
      const body = opts.body ? JSON.parse(opts.body) : {};
      if (action === 'note' && Object.keys(body).some(key => key !== 'note')) return bad('卡片备注只接受 note');
      if (action !== 'note' && Object.keys(body).some(key => key !== 'reason')) return bad('卡片确认只接受 reason');
      items = items.map(item => item.id === cardId ? {
        ...item,
        status: action === 'confirm' ? 'confirmed' : action === 'reject' ? 'rejected' : action === 'restore' ? 'candidate' : item.status,
        reason: action === 'reject' ? (body.reason || '') : '',
        note: action === 'note' ? body.note : item.note,
        fill_url: action === 'confirm' ? item.fill_url : item.fill_url,
      } : item);
      counts = { candidate: items.filter(x => x.status === 'candidate').length,
                 confirmed: items.filter(x => x.status === 'confirmed').length,
                 rejected: items.filter(x => x.status === 'rejected').length };
      return ok(items.find(item => item.id === cardId));
    }
    return bad('接口不存在');
  };
  if (options.promptReply !== undefined) w.prompt = () => options.promptReply;
  w.__confirms = [];
  w.confirm = message => { w.__confirms.push(message); return options.confirmReply === true; };
  w.eval(fs.readFileSync('public/cards.js', 'utf8'));
  await settle();
  return { w, d, calls, state: () => ({ items, counts }) };
}

test('candidate card shows prompt, model, citation and fingerprint', async t => {
  const { d } = await fixture(t);
  const view = d.querySelector('.cd-card');
  assert.ok(view);
  assert.equal(view.dataset.id, id('c'));
  assert.match(view.querySelector('.cd-prompt').textContent, /旧码头黄昏/);
  assert.equal(view.querySelector('.cd-model').textContent, 'Qwen Image 2.1');
  assert.equal(view.querySelector('.cd-status').textContent, '候选');
  assert.equal(view.querySelector('.cd-source-state').textContent, '来源已核对');
  assert.match(view.querySelector('.cd-ref').textContent, /e0/);
  assert.match(view.querySelector('.cd-ref').textContent, /0123456789abcdef/);
  assert.match(d.querySelector('#cd-stats').textContent, /候选 1/);
  assert.match(d.querySelector('#cd-stats').textContent, /待提炼回收项 2 条/);
});

test('a candidate has no fill link until it is confirmed', async t => {
  const { d, calls } = await fixture(t);
  assert.equal(d.querySelectorAll('.cd-card a[href*="lab.html"]').length, 0);
  d.querySelector('[data-action="confirm"]').click();
  await settle();
  assert.ok(calls.some(call => call.url === `/api/cards/${id('c')}/confirm`));
  const link = d.querySelector('.cd-card a[href*="lab.html"]');
  assert.ok(link, 'confirmed card should offer 填进生图');
  assert.equal(link.getAttribute('href'), '/lab.html?kind=image&card=' + id('c'));
  assert.equal(link.textContent.trim(), '填进生图 ↗');
  // Confirming never submits a generation task.
  assert.equal(calls.filter(call => String(call.opts?.method || '') === 'POST' && call.url.includes('generation')).length, 0);
});

test('reject keeps the reason and restore turns the card back into a candidate', async t => {
  const { d, calls, state } = await fixture(t, { promptReply: '太抽象，不像能直接提交的描述' });
  d.querySelector('[data-action="reject"]').click();
  await settle();
  const rejected = calls.find(call => call.url.endsWith('/reject'));
  assert.ok(rejected);
  assert.equal(JSON.parse(rejected.opts.body).reason, '太抽象，不像能直接提交的描述');
  assert.equal(state().items[0].status, 'rejected');
  assert.match(d.querySelector('.cd-status').textContent, /已驳回/);
  d.querySelector('[data-action="restore"]').click();
  await settle();
  assert.ok(calls.some(call => call.url.endsWith('/restore')));
  assert.equal(state().items[0].status, 'candidate');
});

test('a cancelled reject sends nothing', async t => {
  const { d, calls, state } = await fixture(t, { promptReply: null });
  d.querySelector('[data-action="reject"]').click();
  await settle();
  assert.equal(calls.filter(call => call.url.endsWith('/reject')).length, 0);
  assert.equal(state().items[0].status, 'candidate');
});

test('note is saved verbatim, including the spaces the user typed', async t => {
  const { d, calls, state } = await fixture(t);
  const box = d.querySelector('[data-note]');
  box.value = '  先留白，等拍完再改  \n';
  d.querySelector('[data-action="note"]').click();
  await settle();
  const saved = calls.find(call => call.url.endsWith('/note'));
  assert.equal(JSON.parse(saved.opts.body).note, '  先留白，等拍完再改  \n');
  assert.equal(state().items[0].note, '  先留白，等拍完再改  \n');
});

test('filters and search narrow the list through the API', async t => {
  const { w, d, calls } = await fixture(t, { items: [card(), card({ id: id('d'), status: 'confirmed', scene: '视频开场' })] });
  d.querySelector('#cd-views [data-status="confirmed"]').click();
  await settle();
  assert.ok(calls[calls.length - 1].url.includes('status=confirmed'));
  assert.equal(d.querySelectorAll('.cd-card').length, 1);
  const search = d.querySelector('#cd-search');
  search.value = '视频开场';
  search.dispatchEvent(new w.Event('input'));
  await new Promise(resolve => setTimeout(resolve, 350));
  await settle();
  assert.ok(calls[calls.length - 1].url.includes('q=' + encodeURIComponent('视频开场')));
  assert.equal(d.querySelectorAll('.cd-card').length, 1);
});

test('a stale citation is shown honestly and the source stays reachable', async t => {
  const { d } = await fixture(t, { items: [card({ source_state: 'stale' })] });
  assert.equal(d.querySelector('.cd-source-state').textContent, '来源原文已变化');
  assert.ok(d.querySelector('.cd-source-state').className.includes('cd-source-stale'));
  assert.equal(d.querySelector('.cd-knowledge').getAttribute('href'), '/knowledge.html?document=' + id('b'));
});

test('an empty library explains what to do next', async t => {
  const { d } = await fixture(t, { items: [] });
  assert.match(d.querySelector('.cd-empty').textContent, /定时任务/);
});

test('filling the lab reads a confirmed card and never submits a task', async t => {
  const dom = new JSDOM('<div id="lab-model"></div><textarea id="lab-prompt"></textarea><div id="toast"></div>',
    { url: 'http://127.0.0.1:8766/lab.html?kind=image', runScripts: 'outside-only', pretendToBeVisual: true });
  t.after(() => dom.window.close());
  const w = dom.window, calls = [];
  w.document.querySelector('#lab-model').innerHTML = '<option value="qi21">Qwen</option><option value="krea2">Krea2</option>';
  w.eval('var lab={kind:"image",models:[{id:"qi21",kind:"image"},{id:"h3",kind:"video"}]};var remembered=0;function rememberLab(){remembered++;}');
  w.eval(fs.readFileSync('public/lab-core.js', 'utf8'));
  w.fetch = async url => {
    calls.push(String(url));
    if (String(url) === `/api/cards/${id('c')}/prompt`) return ok({ id: id('c'), prompt: '旧码头黄昏，人物只留背影', model: 'qi21', kind: 'image' });
    return bad('这张卡还没确认，确认后才能在生成页使用');
  };
  const cardValue = await w.eval('fillLabCard')(id('c'));
  assert.deepEqual(calls, [`/api/cards/${id('c')}/prompt`]);
  assert.equal(w.document.querySelector('#lab-prompt').value, '旧码头黄昏，人物只留背影');
  assert.equal(w.document.querySelector('#lab-model').value, 'qi21');
  assert.equal(w.eval('remembered'), 1);
  assert.match(w.document.querySelector('#toast').textContent, /未提交生成/);
  assert.equal(cardValue.model, 'qi21');
  // An unconfirmed card cannot be pulled into the lab.
  await assert.rejects(() => w.eval('fillLabCard')(id('e')), /还没确认/);
  assert.equal(w.document.querySelector('#lab-prompt').value, '旧码头黄昏，人物只留背影');
  assert.match(w.document.querySelector('#toast').textContent, /未提交生成/);
  assert.deepEqual(calls, [`/api/cards/${id('c')}/prompt`, `/api/cards/${id('e')}/prompt`]);
});

test('the lab page consumes the card parameter once and drops it from the URL', () => {
  const source = fs.readFileSync('public/lab.js', 'utf8');
  assert.match(source, /query\.get\('card'\)/);
  assert.match(source, /query\.delete\('card'\)/);
  assert.match(source, /await fillLabCard\(cardId\)/);
  assert.match(source, /无法使用该提示词卡/);
  assert.match(fs.readFileSync('public/lab.html', 'utf8'), /lab-core\.js[\s\S]*lab\.js/);
});

// ---------------------------------------------------------------- 定时任务页

async function scheduleFixture(t, options = {}) {
  const dom = new JSDOM(fs.readFileSync('public/schedules.html', 'utf8'),
    { url: 'http://127.0.0.1:8766/schedules.html', runScripts: 'outside-only', pretendToBeVisual: true });
  t.after(() => dom.window.close());
  const w = dom.window, d = w.document, calls = [];
  const task = { id: 'e'.repeat(32), revision: 1, name: '创意提炼', body: '优先能直接做公众号头图的描述',
    kind: 'intake_cards', assistant_id: 'isolated', tool_groups: [], profile_id: 'fake',
    cwd: '/Users/example/studio', frequency: 'daily', minutes: 60, weekdays: [0, 1, 2, 3, 4, 5, 6], at: '09:30',
    timeout: 900, enabled: true, allow_commands: false, next_run: Date.now() / 1000 + 3600,
    result_destination: 'runs', result_category: 'other', clue_id: '', executor_name: '创意提炼（Pi）' };
  const run = { id: 'f'.repeat(32), task_id: task.id, status: 'partial', started_at: '2026-10-09T01:30:00Z',
    finished_at: '2026-10-09T01:31:00Z', exit_code: null, snapshot: { kind: 'intake_cards' },
    output: JSON.stringify({ checked: 6, cards: 4, dropped: 2, drop_reasons: ['来源引用编号不存在', '提示词手抄原文'],
      errors: ['回收项 abcd1234 · 模型未返回有效正文，本条未推进'], waiting: 3, has_more: true }) };
  const listing = { tasks: options.tasks === undefined ? [task] : options.tasks,
    default_cwd: '/Users/example/studio', default_profile_id: 'fake',
    models: [{ profile_id: 'fake', display_name: '合成模型', configured: true }],
    scheduler_health: { running: true, last_tick: '2026-10-09T01:00:00Z', last_error: null },
    concurrency: { max: 2, active: 0, available: 2 } };
  w.setInterval = () => 0; w.setTimeout = () => 0; w.clearTimeout = () => {}; w.confirm = () => true;
  w.HTMLDialogElement.prototype.showModal = function () { this.open = true; };
  w.HTMLDialogElement.prototype.close = function () { this.open = false; };
  w.fetch = async (url, opts = {}) => {
    const call = { url: String(url), opts, body: opts.body && JSON.parse(opts.body) }; calls.push(call);
    if (call.url === '/api/schedules') return response(JSON.parse(JSON.stringify(listing)));
    if (call.url === '/api/clues?view=all') return response({ items: [] });
    if (call.url.startsWith('/api/schedules/runs?')) return response({ runs: options.runs || [], total: (options.runs || []).length, page: 1, page_size: 50, has_more: false });
    if (call.url.startsWith('/api/schedules/run/')) return response((options.runs || [])[0]);
    return response({ id: task.id, ok: true });
  };
  w.eval(fs.readFileSync('public/schedules.js', 'utf8'));
  await settle();
  return { w, d, calls, field: name => d.querySelector(`#task-form [name=${name}]`) };
}
function change(f, element, value) {
  if (element.type === 'checkbox') element.checked = value; else element.value = value;
  element.dispatchEvent(new f.w.Event('change', { bubbles: true }));
  return element;
}

test('创意提炼 is offered as a daily task with its own executor and guards', async t => {
  assert.match(fs.readFileSync('public/schedules.html', 'utf8'), /<option value="intake_cards">创意提炼（Pi）<\/option>/);
  const f = await scheduleFixture(t, { tasks: [] });
  f.d.querySelector('#new-task').click();
  change(f, f.field('kind'), 'intake_cards');
  assert.equal(f.field('frequency').value, 'daily');
  assert.equal(f.field('at').value, '09:30');
  assert.equal(f.field('timeout').value, '900');
  assert.equal(f.field('assistant_id').disabled, true);
  assert.equal(f.field('allow_commands').disabled, true);
  assert.equal(f.field('result_destination').value, 'runs');
  assert.match(f.d.querySelector('#assistant-help').textContent, /手抄原文或编造来源会被丢弃/);
  assert.match(f.d.querySelector('#assistant-help').textContent, /不会自动提交生成/);
});

test('a frequency 黄总 changed by hand is not overwritten by the daily default', async t => {
  const f = await scheduleFixture(t, { tasks: [] });
  f.d.querySelector('#new-task').click();
  change(f, f.field('kind'), 'intake_cards');
  assert.equal(f.field('frequency').value, 'daily');
  change(f, f.field('frequency'), 'interval');
  change(f, f.field('kind'), 'pi');
  change(f, f.field('kind'), 'intake_cards');
  assert.equal(f.field('frequency').value, 'interval');
});

test('the task list and detail state the limits and that cards still need confirming', async t => {
  const f = await scheduleFixture(t);
  assert.match(f.d.querySelector('#tasks').textContent, /创意提炼（Pi）/);
  f.d.querySelector('[data-action="detail"]').click();
  const meta = f.d.querySelector('#task-meta').textContent;
  assert.match(meta, /每轮最多 6 条新回收项/);
  assert.match(meta, /证据编号与原文指纹/);
  assert.match(meta, /需在提示词卡库点确认/);
  assert.match(meta, /提示词卡库（待你确认）/);
});

test('an extraction run reports counts, drop reasons and the leftover queue', async t => {
  const run = { id: 'f'.repeat(32), task_id: 'e'.repeat(32), trigger: 'scheduled', status: 'partial',
    started_at: '2026-10-09T01:30:00Z', finished_at: '2026-10-09T01:31:00Z', exit_code: null,
    snapshot: { kind: 'intake_cards', name: '创意提炼', revision: 1, executor_name: '创意提炼（Pi）' },
    output: JSON.stringify({ checked: 6, cards: 4, dropped: 2, drop_reasons: ['来源引用编号不存在', '提示词手抄原文'],
      errors: ['回收项 abcd1234 · 模型未返回有效正文，本条未推进'], waiting: 3, has_more: true,
      summary: '本轮提炼 6 条回收项' }) };
  const f = await scheduleFixture(t, { runs: [run] });
  f.d.querySelector('#run-tab').click();
  await settle();
  const text = f.d.querySelector('#runs').textContent;
  assert.match(text, /回收项 6 条/);
  assert.match(text, /新增候选卡 4 张/);
  assert.match(text, /丢弃 2 张/);
  assert.match(text, /待提炼 3 条/);
  assert.match(text, /来源引用编号不存在、提示词手抄原文/);
  assert.match(text, /本轮限量未完成/);
  assert.match(text, /模型未返回有效正文/);
  assert.ok(f.d.querySelector('#runs a[href="/cards.html"]'));
  assert.ok(!text.includes('运行结果格式异常'));
});

test('a malformed extraction receipt falls back instead of pretending to be empty', async t => {
  const run = { id: 'g'.repeat(32), task_id: 'e'.repeat(32), trigger: 'scheduled', status: 'failed',
    started_at: '2026-10-09T01:30:00Z', finished_at: '2026-10-09T01:30:01Z', exit_code: null,
    snapshot: { kind: 'intake_cards', name: '创意提炼', revision: 1 }, output: '创意提炼入口未连接' };
  const f = await scheduleFixture(t, { runs: [run] });
  f.d.querySelector('#run-tab').click();
  await settle();
  const text = f.d.querySelector('#runs').textContent;
  assert.match(text, /创意提炼入口未连接/);
  assert.ok(!text.includes('新增候选卡'));
});

test('a card whose drop was deleted says 来源已删除 and keeps no dead link', async t => {
  const items = [card({ source_state: 'missing',
    source: { kind: '', kind_label: '回收项已删除', title: '', received_at: '', knowledge_url: '', reason: '', deleted: true } })];
  const { d } = await fixture(t, { items });
  const view = d.querySelector('.cd-card');
  assert.equal(view.querySelector('.cd-source-deleted').textContent, '来源已删除');
  assert.equal(view.querySelector('.cd-source-state').textContent, '来源资料已删除');
  assert.match(view.textContent, /回收项已删除/);
  // 不能留一个点开就报错的“核对来源原文”链接。
  assert.equal(d.querySelectorAll('.cd-knowledge').length, 0);
  assert.equal(d.querySelectorAll('.cd-card a[href^="/knowledge.html"]').length, 0);
  assert.match(d.querySelector('.cd-prompt').textContent, /旧码头黄昏/);
});

test('a card still citing a shared document keeps its citation link and has no deleted badge', async t => {
  const { d } = await fixture(t, { items: [card({ source: { ...card().source, deleted: false } })] });
  assert.equal(d.querySelector('.cd-source-deleted'), null);
  assert.equal(d.querySelector('.cd-knowledge').getAttribute('href'), '/knowledge.html?document=' + id('b'));
});

test('cards from one drop share a header, and 按时间 lists them without groups', async t => {
  const second = card({ id: id('d'), prompt: '夜色渡口只留一条灯线，低机位，不出现人脸', model: 'krea2',
    analysis_line: '旧码头的黄昏适合低对比背影。值得收：开头方式能借。可写角度：蹲拍。相关 1 条。' });
  const { d, calls } = await fixture(t, { items: [card({ analysis_line: second.analysis_line }), second] });
  assert.equal(d.querySelector('#cd-layout [data-view="source"]').classList.contains('selected'), true);
  assert.equal(d.querySelectorAll('.cd-group').length, 1);
  const head = d.querySelector('.cd-group-head');
  assert.equal(head.querySelector('h2').textContent, '创意回收 · 文字');
  assert.match(head.querySelector('.cd-group-time').textContent, /收下/);
  assert.equal(head.querySelector('.cd-group-link').getAttribute('href'), '/knowledge.html?document=' + id('b'));
  assert.match(head.querySelector('.cd-group-summary').textContent, /可写角度：蹲拍/);
  assert.equal(d.querySelectorAll('.cd-card').length, 2);
  assert.ok(calls[0].url.includes('view=source'));
  d.querySelector('#cd-layout [data-view="time"]').click();
  await settle();
  assert.equal(d.querySelector('.cd-group'), null);
  assert.equal(d.querySelectorAll('.cd-card').length, 2);
  assert.ok(calls.at(-1).url.includes('view=time'));
});

test('source type filters ask the API for 文字 and 情报推送 separately', async t => {
  const intel = card({ id: id('e'), drop_id: id('f'), prompt: '情报封面只留标题和留白',
    source: { kind: 'text', kind_label: '文字', channel: 'intel', channel_label: '情报推送', title: '公众号样例',
      received_at: '2026-10-09T01:40:00+00:00', knowledge_url: '/knowledge.html?document=' + id('b'),
      reason: '情报采集 · 公众号「样例号」', deleted: false } });
  const { d, calls } = await fixture(t, { items: [card(), intel] });
  d.querySelector('#cd-kinds [data-kind="text"]').click();
  await settle();
  assert.ok(calls.at(-1).url.includes('kind=text'));
  assert.equal(d.querySelectorAll('.cd-card').length, 1);
  assert.match(d.querySelector('.cd-prompt').textContent, /旧码头黄昏/);
  d.querySelector('#cd-kinds [data-kind="intel"]').click();
  await settle();
  assert.ok(calls.at(-1).url.includes('kind=intel'));
  assert.equal(d.querySelectorAll('.cd-card').length, 1);
  assert.match(d.querySelector('.cd-from').textContent, /情报推送/);
  assert.match(d.querySelector('.cd-prompt').textContent, /情报封面/);
});

test('cleanup asks first and then deletes only the cards whose source is gone', async t => {
  const gone = { kind: '', kind_label: '回收项已删除', title: '', received_at: '', knowledge_url: '', reason: '', deleted: true };
  const items = [card({ source_state: 'missing', source: gone }),
    card({ id: id('d'), prompt: '同一条已删来源上的第二张卡', source_state: 'missing', source: gone }),
    card({ id: id('e'), drop_id: id('f'), prompt: '另一条还在的卡片' })];
  const cancelled = await fixture(t, { items, confirmReply: false });
  cancelled.d.querySelector('[data-action="cleanup"]').click();
  await settle();
  assert.match(cancelled.w.__confirms[0], /只删除这组来源已删除的卡片/);
  assert.equal(cancelled.calls.filter(call => call.url === '/api/cards/cleanup').length, 0);
  assert.equal(cancelled.d.querySelectorAll('.cd-card').length, 3);
  const { d, calls, state } = await fixture(t, { items, confirmReply: true });
  const deletedHead = [...d.querySelectorAll('.cd-group-head')].find(head => head.querySelector('h2').textContent === '来源已删除');
  assert.ok(deletedHead);
  assert.equal(deletedHead.querySelector('.cd-group-link'), null);
  assert.match(deletedHead.querySelector('.cd-group-summary').textContent, /没有留下四块分析/);
  deletedHead.querySelector('[data-action="cleanup"]').click();
  await settle();
  const posted = calls.find(call => call.url === '/api/cards/cleanup');
  assert.equal(JSON.parse(posted.opts.body).drop_id, id('a'));
  assert.equal(state().items.length, 1);
  assert.equal(d.querySelectorAll('.cd-card').length, 1);
  assert.match(d.querySelector('.cd-prompt').textContent, /另一条还在的卡片/);
  assert.equal(d.querySelector('[data-action="cleanup"]'), null);
});

test('deleting one card asks first and leaves the others', async t => {
  const items = [card(), card({ id: id('d'), prompt: '夜色渡口只留一条灯线' })];
  const cancelled = await fixture(t, { items, confirmReply: false });
  cancelled.d.querySelector('[data-action="delete"]').click();
  await settle();
  assert.match(cancelled.w.__confirms[0], /只删除这张卡片/);
  assert.equal(cancelled.calls.filter(call => call.url.endsWith('/delete')).length, 0);
  assert.equal(cancelled.state().items.length, 2);
  const { d, calls, state } = await fixture(t, { items, confirmReply: true });
  d.querySelector('[data-action="delete"]').click();
  await settle();
  const posted = calls.find(call => call.url.endsWith('/delete'));
  assert.equal(posted.url, '/api/cards/' + id('c') + '/delete');
  assert.equal(JSON.parse(posted.opts.body) && true, true);
  assert.equal(Object.keys(JSON.parse(posted.opts.body)).length, 0);
  assert.equal(state().items.map(item => item.id).join(','), id('d'));
  assert.equal(d.querySelectorAll('.cd-card').length, 1);
});
