const test = require('node:test'), assert = require('node:assert/strict'), fs = require('node:fs');
const { JSDOM } = require('jsdom');
const tick = () => new Promise(resolve => setImmediate(resolve));
const settle = async () => { for (let i = 0; i < 8; i++) await tick(); };
const id = n => n.repeat(32);
const ok = data => ({ ok: true, json: async () => data });
const bad = (error, code = 400) => ({ ok: false, status: code, json: async () => ({ error }) });

const base = extra => ({ id: id('a'), kind: 'link', received_at: '2026-10-09T01:20:00+00:00', url: 'https://example.com/post',
  title: '', reason: '', status: 'resolved', error: '', document_id: id('b'), revision: 2, file_path: '', file: {},
  media_kind: '', text_chars: 0, content_scope: '', meta: {}, outcome: 'stored', state: '', document_shares: 1,
  document_owned: true,
  knowledge_url: '/knowledge.html?document=' + id('b'), ...extra });

async function fixture(t, options = {}) {
  const dom = new JSDOM(fs.readFileSync('public/intake.html', 'utf8'),
    { url: 'http://127.0.0.1:8766/intake.html', runScripts: 'outside-only', pretendToBeVisual: true });
  t.after(() => dom.window.close());
  const w = dom.window, d = w.document, calls = [];
  const items = options.items || [];
  let dropped = null, deleted = null, staged = null;
  const gate = {};
  w.confirm = message => { (w.confirms = w.confirms || []).push(message); return options.confirm !== false; };
  w.fetch = async (url, opts = {}) => {
    const target = String(url);
    calls.push({ url: target, opts });
    // 每次回读都给一份新副本：模拟服务端每次回最新状态，也让测试里改 list 不会直接改到页内对象。
    if (target === '/api/intake') return ok({ schema: 'intake/v1', items: items.map(item => ({ ...item })), note: '' });
    if (target.endsWith('/delete')) {
      if (options.deleteFails) return bad('回收项不存在，请刷新列表');
      deleted = target.split('/')[3];
      return ok({ schema: 'intake-delete/v1', id: deleted, deleted: true, document_removed: true,
        analysis_removed: 1, document_shared_by: 0, cards_kept: true, file_removed: false, file_error: '', ledger_error: '' });
    }
    if (target.startsWith('/api/intake/drop?')) {
      const params = new w.URLSearchParams(target.split('?')[1]);
      return ok(base({ id: id('c'), kind: params.get('kind'), file: { name: params.get('name'), bytes: 2048, format: 'PNG', width: 24, height: 16 }, file_path: 'media/x.png', status: 'pending', outcome: 'pending', state: '已收下，尚未解析', document_id: '', knowledge_url: '', meta: {} }));
    }
    if (target.startsWith('/api/intake/stage?')) {
      const params = new w.URLSearchParams(target.split('?')[1]);
      staged = { kind: params.get('kind'), name: params.get('name') };
      if (options.stageFails) return bad('每张图片最多 10 MB');
      const reply = () => ok({ schema: 'intake-stage/v1', token: id('e'), kind: params.get('kind'),
        file: { name: params.get('name'), bytes: 2048, format: 'PNG', width: 24, height: 16, kind: params.get('kind') } });
      if (options.stageGate) {
        return new Promise((resolvePromise, rejectPromise) => {
          const fail = () => { const error = new Error('已中止'); error.name = 'AbortError'; rejectPromise(error); };
          if (opts.signal && opts.signal.aborted) fail();
          else if (opts.signal) opts.signal.addEventListener('abort', fail);
          gate.stage = { resolve: () => resolvePromise(reply()) };
        });
      }
      if (options.stageBodyGate) {
        // 响应头已经到了，响应体还卡着。
        return { ok: true, json: () => new Promise((resolvePromise, rejectPromise) => {
          const fail = () => { const error = new Error('已中止'); error.name = 'AbortError'; rejectPromise(error); };
          if (opts.signal && opts.signal.aborted) fail();
          else if (opts.signal) opts.signal.addEventListener('abort', fail);
          gate.body = { resolve: () => resolvePromise(reply()) };
        }) };
      }
      return reply();
    }
    if (target === '/api/intake/drop') {
      dropped = JSON.parse(opts.body);
      const withFile = Boolean(dropped.file_token);
      return ok(base({ id: dropped.id || id('c'), kind: dropped.kind || (staged ? staged.kind : 'text'),
        url: dropped.url || '', text_chars: (dropped.text || '').length, reason: dropped.reason || '',
        file: withFile ? { name: (staged && staged.name) || 'shot.png', bytes: 2048, format: 'PNG', width: 24, height: 16, kind: 'image' } : {},
        file_path: withFile ? 'media/x.png' : '', media_kind: withFile ? 'image' : '',
        status: 'pending', outcome: 'pending', state: '已收下，尚未解析', document_id: '', knowledge_url: '', meta: {} }));
    }
    if (target.endsWith('/resolve')) {
      if (options.slowResolve) {
        return new Promise((resolvePromise, rejectPromise) => {
          const fail = () => { const error = new Error('没回话'); error.name = 'AbortError'; rejectPromise(error); };
          if (opts.signal && opts.signal.aborted) fail(); else if (opts.signal) opts.signal.addEventListener('abort', fail);
        });
      }
      // rawResolve：不做 id 补齐，原样回一个回执（用来测空对象、缺字段、未知值）。
      if (options.rawResolve !== undefined) {
        return ok(typeof options.rawResolve === 'function' ? options.rawResolve(opts) : options.rawResolve);
      }
      if (options.resolve !== undefined) {
        // resolve 也可以是个函数：在请求发生时才造回执（识别 finished_at 要属于本轮）。
        const reply = typeof options.resolve === 'function' ? options.resolve(opts) : options.resolve;
        return ok({ ...reply, id: dropped?.id || reply.id });
      }
      const kind = dropped ? (dropped.kind || (staged ? staged.kind : 'text')) : 'link';
      const attached = dropped && dropped.file_token ? '；另存本机图片：shot.png（PNG，24×16，2.0 KB）' : '';
      const text = kind === 'text'
        ? { state: '已存入知识库，原文逐字保存' + (attached ? '；图片内容未识别' : ''), content_scope: `用户原文逐字保存，共 ${(dropped.text || '').length} 字` + attached, meta: {} }
        : { state: '已存入知识库，已抓取正文，共 12 字' + (attached ? '；图片内容未识别' : ''), content_scope: '已抓取正文，共 12 字' + attached, meta: { fetched: true, fetched_at: '2026-10-09T01:21:00+00:00', total_chars: 12, pages: [{ start: 0, end: 12 }] } };
      return ok(base({ id: dropped?.id || id('c'), kind, url: dropped?.url || '', reason: dropped?.reason || '', text_chars: (dropped?.text || '').length,
        file: dropped && dropped.file_token ? { name: (staged && staged.name) || 'shot.png', bytes: 2048, format: 'PNG', width: 24, height: 16, kind: 'image' } : {},
        file_path: dropped && dropped.file_token ? 'media/x.png' : '', media_kind: dropped && dropped.file_token ? 'image' : '', ...text }));
    }
    if (target === '/api/intake/analysis-status') return options.staleService
      ? bad('页面不存在', 404)
      : ok({ schema: 'intake-analysis-status/v1', supported: options.analysisSupported !== false, model_configured: true, working: 0 });
    if (target.endsWith('/analyze')) return options.staleAnalyze ? bad('操作不存在', 404) : ok({ status: 'queued', error: '', retryable: true });
    return bad('接口不存在');
  };
  if (options.nowTimeout) w.setTimeout = handler => { handler(); return 0; };
  let pollNow = null;
  if (options.capturePoll) {
    // 把 2 秒轮询的回调抓一份出来，好手动触发一次：断言「该刷的刷了、到终态就不刷」，不用干等。
    const realInterval = w.setInterval.bind(w);
    w.setInterval = (handler, ms) => { pollNow = handler; return realInterval(handler, ms); };
  }
  if (options.longTimerNow) {
    // 只把 60 秒级（resolve/stage）计时器立即触发；20 秒级的 drop 请求照常走完。
    const realSetTimeout = w.setTimeout.bind(w);
    w.setTimeout = (handler, ms) => (ms >= 60000 ? (handler(), 0) : realSetTimeout(handler, ms));
  }
  w.eval(fs.readFileSync('public/intake.js', 'utf8'));
  await settle();
  return { w, d, calls, list: () => d.querySelector('#in-list').textContent, rows: () => d.querySelectorAll('#in-list .in-item'),
    confirms: () => w.confirms || [], deleted: () => deleted, stageGate: () => gate.stage, bodyGate: () => gate.body,
    poll: () => pollNow && pollNow(), gets: () => calls.filter(c => c.url === '/api/intake').length };
}
const submit = async f => { f.d.querySelector('#in-submit').dispatchEvent(new f.w.Event('submit', { bubbles: true, cancelable: true })); await settle(); };
const fire = f => { f.d.querySelector('#in-submit').dispatchEvent(new f.w.Event('submit', { bubbles: true, cancelable: true })); };
const pick = (f, name) => { const event = new f.w.Event('drop', { bubbles: true, cancelable: true });
  event.dataTransfer = { files: [{ name, type: 'image/png', size: 2048 }] }; f.d.dispatchEvent(event); };
// 与 intake.js 里的 clock 同口径，用来比对行内时间取的是哪个时刻。
const clockOf = v => new Date(v).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', hour12: false });
const attachedMedia = extra => base({ kind: 'image', url: '', media_kind: 'image', file_path: 'media/x.png',
  file: { name: 'shot.png', bytes: 2048, format: 'PNG', width: 24, height: 16, kind: 'image' },
  state: '已存入知识库；图片内容未识别', ...extra });

test('the page offers one drop box, an optional reason and honest limits', async t => {
  const f = await fixture(t, { items: [] });
  assert.match(f.d.querySelector('#in-input').placeholder, /链接|想法/);
  assert.match(f.d.querySelector('#in-reason').placeholder, /.+/);
  assert.match(f.d.querySelector('.in-heading').textContent, /原样保存，不判断、不改写、不补全/);
  assert.match(f.d.querySelector('.in-heading').textContent, /收藏理由单独存，不会混进原文/);
  assert.match(f.d.querySelector('#in-form').textContent, /异步进行/);
  assert.match(f.d.querySelector('#in-list').textContent, /还没有回收项/);
  for (const phrase of ['智能分析', '自动学习', '一键变作品', 'AI 读懂']) assert.doesNotMatch(f.d.body.textContent, new RegExp(phrase));
  assert.match(f.d.querySelector('.workspace-nav').textContent, /创意回收/);
});

test('a link is filed into the knowledge base and the row says the body was fetched', async t => {
  const f = await fixture(t, { items: [] });
  f.d.querySelector('#in-input').value = 'https://example.com/post';
  f.d.querySelector('#in-reason').value = '这个开头方式可以借';
  await submit(f);
  const drop = f.calls.find(c => c.url === '/api/intake/drop');
  assert.equal(drop.opts.method, 'POST');
  assert.equal(JSON.parse(drop.opts.body).kind, 'link');
  assert.equal(JSON.parse(drop.opts.body).url, 'https://example.com/post');
  assert.ok(f.calls.some(c => c.url.endsWith('/resolve')));
  assert.match(f.list(), /已存入知识库，已抓取正文，共 12 字/);
  assert.match(f.list(), /我为什么收它：这个开头方式可以借/);
  assert.match(f.list(), /正文 12 字/);
  const link = f.d.querySelector('#in-list .in-item a[href^="/knowledge.html?document="]');
  assert.equal(link.getAttribute('href'), '/knowledge.html?document=' + id('b'));
});

test('text is sent verbatim and never mixed with the reason', async t => {
  const f = await fixture(t, { items: [] });
  f.d.querySelector('#in-input').value = '第一行\n  保留缩进\n第三行';
  f.d.querySelector('#in-reason').value = '以后写稿要用';
  await submit(f);
  const sent = JSON.parse(f.calls.find(c => c.url === '/api/intake/drop').opts.body);
  assert.equal(sent.kind, 'text');
  assert.equal(sent.text, '第一行\n  保留缩进\n第三行');
  assert.equal(sent.reason, '以后写稿要用');
  assert.match(f.list(), /已存入知识库，原文逐字保存/);
  assert.match(f.list(), /本机文字 · 14 字/);
});

test('a failed fetch shows the real reason and never looks like success', async t => {
  const failed = base({ id: id('c'), kind: 'link', status: 'failed', outcome: 'failed', error: '联网服务响应超时，请稍后重试',
    state: '抓取失败：联网服务响应超时，请稍后重试；已存入知识库（仅链接）', content_scope: '仅收藏链接；原文正文未读取（抓取失败：联网服务响应超时，请稍后重试）', meta: { fetched: false } });
  const f = await fixture(t, { items: [], resolve: failed });
  f.d.querySelector('#in-input').value = 'https://example.com/slow';
  await submit(f);
  assert.match(f.list(), /抓取失败：联网服务响应超时，请稍后重试/);
  assert.doesNotMatch(f.list(), /已抓取正文/);
  assert.match(f.d.querySelector('#in-status').textContent, /抓取失败/);
  assert.equal(f.d.querySelectorAll('#in-list [data-resolve]').length, 1);
  assert.equal(f.d.querySelector('.in-item').className.includes('in-failed'), true);
});

test('an already collected link says it already exists instead of pretending to be new', async t => {
  const duplicate = base({ id: id('c'), kind: 'link', outcome: 'duplicate', state: '已存在，未重复导入', content_scope: '仅收藏链接；原文正文未读取', meta: { fetched: true, duplicate: true } });
  const f = await fixture(t, { items: [], resolve: duplicate });
  f.d.querySelector('#in-input').value = 'https://example.com/kept';
  await submit(f);
  assert.match(f.list(), /已存在，未重复导入/);
  assert.equal(f.d.querySelector('.in-item').className.includes('in-duplicate'), true);
});

test('image and video rows state what was not read', async t => {
  const items = [base({ kind: 'image', url: '', file: { name: 'shot.png', bytes: 2048, format: 'PNG', width: 1024, height: 768, kind: 'image' }, file_path: 'media/x.png', media_kind: 'image', state: '已存入知识库；图片内容未识别', content_scope: '仅保存本机图片与尺寸格式；图片内容未识别' }),
    base({ id: id('d'), kind: 'video', url: '', file: { name: 'clip.mp4', bytes: 5242880, format: 'MP4', duration_seconds: 90, kind: 'video' }, file_path: 'media/y.mp4', media_kind: 'video', state: '已存入知识库；视频正文与语音未读取', content_scope: '仅保存本机视频与大小格式时长；视频正文与语音未读取' })];
  const f = await fixture(t, { items });
  assert.match(f.list(), /本机文件 · shot\.png（PNG，1024×768，2\.0 KB）/);
  assert.match(f.list(), /图片内容未识别/);
  assert.match(f.list(), /视频正文与语音未读取/);
  assert.match(f.list(), /clip\.mp4/);
  assert.doesNotMatch(f.list(), /media\/|\/Users\/|workspace/);
});

test('picking or dropping a file uploads nothing by itself', async t => {
  const f = await fixture(t, { items: [] });
  const event = new f.w.Event('drop', { bubbles: true, cancelable: true });
  event.dataTransfer = { files: [{ name: 'shot.png', type: 'image/png', size: 2048 }] };
  f.d.dispatchEvent(event);
  await settle();
  assert.deepEqual(f.calls.filter(c => c.url.startsWith('/api/intake/stage') || c.url.startsWith('/api/intake/drop')), []);
  assert.match(f.d.querySelector('#in-picked').textContent, /已选 shot\.png/);
  assert.match(f.d.querySelector('#in-picked').textContent, /点「收下」一起提交/);
  assert.equal(f.d.querySelectorAll('#in-picked [data-discard]').length, 1);
});

test('a link plus a picked file become one drop: stage then one submit', async t => {
  const f = await fixture(t, { items: [] });
  const event = new f.w.Event('drop', { bubbles: true, cancelable: true });
  event.dataTransfer = { files: [{ name: 'shot.png', type: 'image/png', size: 2048 }] };
  f.d.dispatchEvent(event);
  await settle();
  f.d.querySelector('#in-input').value = 'https://example.com/post';
  f.d.querySelector('#in-reason').value = '构图可参考';
  await submit(f);
  const stage = f.calls.find(c => c.url.startsWith('/api/intake/stage?'));
  assert.match(stage.url, /kind=image&name=shot\.png/);
  assert.equal(stage.opts.headers['X-Article-Studio'], '1');
  const sent = JSON.parse(f.calls.find(c => c.url === '/api/intake/drop').opts.body);
  assert.equal(sent.kind, 'link');
  assert.equal(sent.url, 'https://example.com/post');
  assert.equal(sent.reason, '构图可参考');
  assert.equal(sent.file_token, id('e'));
  assert.equal(f.calls.filter(c => c.url === '/api/intake/drop').length, 1);
  assert.equal(f.rows().length, 1);
  assert.ok(f.calls.some(c => c.url.endsWith('/resolve')));
  assert.match(f.list(), /example\.com\/post/);
  assert.match(f.list(), /本机文件 · shot\.png（PNG，24×16，2\.0 KB）/);
  assert.match(f.list(), /图片内容未识别/);
  assert.equal(f.d.querySelector('#in-picked').textContent, '');
});

test('a picked file with no text still submits as one file-only drop', async t => {
  const f = await fixture(t, { items: [] });
  Object.defineProperty(f.d.querySelector('#in-file'), 'files',
    { value: [{ name: 'only.png', type: 'image/png', size: 2048 }], configurable: true });
  f.d.querySelector('#in-file').dispatchEvent(new f.w.Event('change', { bubbles: true }));
  await settle();
  f.d.querySelector('#in-reason').value = '只要这张图';
  await submit(f);
  const sent = JSON.parse(f.calls.find(c => c.url === '/api/intake/drop').opts.body);
  assert.equal(sent.file_token, id('e'));
  assert.equal(sent.reason, '只要这张图');
  assert.equal(sent.url, undefined);
  assert.equal(sent.text, undefined);
  assert.equal(f.rows().length, 1);
});

test('a failed file upload says why, keeps the picked file and creates no drop', async t => {
  const f = await fixture(t, { items: [], stageFails: true });
  const event = new f.w.Event('drop', { bubbles: true, cancelable: true });
  event.dataTransfer = { files: [{ name: 'big.png', type: 'image/png', size: 11000000 }] };
  f.d.dispatchEvent(event);
  await settle();
  f.d.querySelector('#in-input').value = 'https://example.com/post';
  await submit(f);
  assert.match(f.d.querySelector('#in-status').textContent, /每张图片最多 10 MB/);
  assert.equal(f.calls.filter(c => c.url === '/api/intake/drop').length, 0);
  assert.match(f.d.querySelector('#in-picked').textContent, /big\.png/);
  assert.match(f.d.querySelector('#in-input').value, /example\.com\/post/);
});

test('cancelling the picked file leaves only the text to submit', async t => {
  const f = await fixture(t, { items: [] });
  const event = new f.w.Event('drop', { bubbles: true, cancelable: true });
  event.dataTransfer = { files: [{ name: 'shot.png', type: 'image/png', size: 2048 }] };
  f.d.dispatchEvent(event);
  await settle();
  f.d.querySelector('#in-picked [data-discard]').click();
  await settle();
  assert.equal(f.d.querySelector('#in-picked').textContent, '');
  f.d.querySelector('#in-input').value = '一段想法';
  await submit(f);
  assert.equal(f.calls.some(c => c.url.startsWith('/api/intake/stage')), false);
  assert.equal(JSON.parse(f.calls.find(c => c.url === '/api/intake/drop').opts.body).file_token, undefined);
});

test('changing or dropping another file while an upload is in flight cannot replace it', async t => {
  const f = await fixture(t, { items: [], stageGate: true });
  pick(f, 'first.png');
  await settle();
  f.d.querySelector('#in-input').value = 'https://example.com/one';
  fire(f);
  await settle();
  assert.match(f.d.querySelector('#in-picked').textContent, /正在上传 first\.png/);
  assert.equal(f.d.querySelectorAll('#in-picked [data-cancel-upload]').length, 1);
  assert.equal(f.d.querySelectorAll('#in-picked [data-discard]').length, 0);
  assert.ok(f.d.querySelector('#in-file').disabled);
  assert.ok(f.d.querySelector('#in-submit').disabled);
  // 上传中换文件：选择框与拖拽都不接受，正在飞行的那次仍是 first.png。
  Object.defineProperty(f.d.querySelector('#in-file'), 'files',
    { value: [{ name: 'second.png', type: 'image/png', size: 2048 }], configurable: true });
  f.d.querySelector('#in-file').dispatchEvent(new f.w.Event('change', { bubbles: true }));
  const event = new f.w.Event('drop', { bubbles: true, cancelable: true });
  event.dataTransfer = { files: [{ name: 'third.png', type: 'image/png', size: 2048 }] };
  f.d.dispatchEvent(event);
  await settle();
  assert.match(f.d.querySelector('#in-status').textContent, /上传中不能换文件/);
  assert.match(f.d.querySelector('#in-picked').textContent, /正在上传 first\.png/);
  f.stageGate().resolve();
  await settle();
  const sent = JSON.parse(f.calls.find(c => c.url === '/api/intake/drop').opts.body);
  assert.equal(sent.url, 'https://example.com/one');
  assert.equal(sent.file_token, id('e'));
  assert.equal(f.calls.filter(c => c.url.startsWith('/api/intake/stage')).length, 1);
  assert.equal(f.d.querySelector('#in-picked').textContent, '');
  assert.ok(!f.d.querySelector('#in-file').disabled);
  assert.ok(!f.d.querySelector('#in-submit').disabled);
});

test('editing the box while an upload is in flight submits the snapshot and keeps the new text', async t => {
  const f = await fixture(t, { items: [], stageGate: true });
  pick(f, 'shot.png');
  await settle();
  f.d.querySelector('#in-input').value = 'https://example.com/one';
  f.d.querySelector('#in-reason').value = '第一条理由';
  fire(f);
  await settle();
  f.d.querySelector('#in-input').value = 'https://example.com/two';
  f.d.querySelector('#in-reason').value = '改过的理由';
  f.stageGate().resolve();
  await settle();
  const sent = JSON.parse(f.calls.find(c => c.url === '/api/intake/drop').opts.body);
  assert.equal(sent.url, 'https://example.com/one');
  assert.equal(sent.reason, '第一条理由');
  assert.equal(f.d.querySelector('#in-input').value, 'https://example.com/two');
  assert.equal(f.d.querySelector('#in-reason').value, '改过的理由');
  assert.equal(f.d.querySelector('#in-picked').textContent, '');
});

test('cancelling the current upload sends nothing and keeps the picked file', async t => {
  const f = await fixture(t, { items: [], stageGate: true });
  pick(f, 'shot.png');
  await settle();
  f.d.querySelector('#in-input').value = 'https://example.com/one';
  fire(f);
  await settle();
  f.d.querySelector('#in-picked [data-cancel-upload]').click();
  await settle();
  assert.match(f.d.querySelector('#in-status').textContent, /已取消本次上传/);
  assert.equal(f.calls.filter(c => c.url === '/api/intake/drop').length, 0);
  assert.match(f.d.querySelector('#in-picked').textContent, /已选 shot\.png/);
  assert.equal(f.d.querySelectorAll('#in-picked [data-discard]').length, 1);
  assert.ok(!f.d.querySelector('#in-file').disabled);
  // 「取消选择」只影响下一条：文件还在，再点一次「收下」会重新上传。
  f.d.querySelector('#in-picked [data-discard]').click();
  await settle();
  assert.equal(f.d.querySelector('#in-picked').textContent, '');
});

test('cancelling while the stage response body is still arriving stops before the drop', async t => {
  const f = await fixture(t, { items: [], stageBodyGate: true });
  pick(f, 'shot.png');
  await settle();
  f.d.querySelector('#in-input').value = 'https://example.com/one';
  fire(f);
  await settle();
  // 响应头到了、响应体还没读完：仍算在上传里，取消按钮与禁用都要还在。
  assert.ok(f.bodyGate(), '响应体读取应仍在上传守卫里');
  assert.match(f.d.querySelector('#in-picked').textContent, /正在上传 shot\.png/);
  assert.equal(f.d.querySelectorAll('#in-picked [data-cancel-upload]').length, 1);
  assert.ok(f.d.querySelector('#in-file').disabled);
  f.d.querySelector('#in-picked [data-cancel-upload]').click();
  await settle();
  assert.match(f.d.querySelector('#in-status').textContent, /已取消本次上传/);
  assert.equal(f.calls.filter(c => c.url === '/api/intake/drop').length, 0);
  assert.match(f.d.querySelector('#in-picked').textContent, /已选 shot\.png/);
  assert.ok(!f.d.querySelector('#in-file').disabled);
});

test('the 60 second upload timer still covers the stage response body', async t => {
  const f = await fixture(t, { items: [], stageBodyGate: true, longTimerNow: true });
  pick(f, 'shot.png');
  await settle();
  f.d.querySelector('#in-input').value = 'https://example.com/one';
  fire(f);
  await settle();
  assert.match(f.d.querySelector('#in-status').textContent, /超过 60 秒没回话/);
  assert.equal(f.calls.filter(c => c.url === '/api/intake/drop').length, 0);
  assert.match(f.d.querySelector('#in-picked').textContent, /已选 shot\.png/);
  assert.ok(!f.d.querySelector('#in-file').disabled);
});

test('an empty box with no file is refused without any request', async t => {
  const f = await fixture(t, { items: [] });
  await submit(f);
  assert.match(f.d.querySelector('#in-status').textContent, /先粘贴一个链接、写下一段想法，或选一个图片/);
  assert.equal(f.calls.filter(c => c.url === '/api/intake/drop').length, 0);
});

test('the kind filters only change what is shown', async t => {
  const items = [base({}), base({ id: id('e'), kind: 'text', url: '', text_chars: 40, state: '已存入知识库，原文逐字保存' })];
  const f = await fixture(t, { items });
  assert.equal(f.rows().length, 2);
  f.d.querySelector('#in-views [data-kind="text"]').click();
  assert.equal(f.rows().length, 1);
  assert.match(f.list(), /本机文字 · 40 字/);
  f.d.querySelector('#in-views [data-kind=""]').click();
  assert.equal(f.rows().length, 2);
});

test('stored text can never be injected as markup', async t => {
  const items = [base({ reason: '<img src=x onerror=alert(1)>', url: 'https://example.com/<script>alert(1)</script>', state: '已存入知识库，已抓取正文，共 1 字', meta: { fetched: true, total_chars: 1 } })];
  const f = await fixture(t, { items });
  assert.equal(f.d.querySelectorAll('#in-list img').length, 0);
  assert.equal(f.d.querySelectorAll('#in-list script').length, 0);
  assert.match(f.list(), /<img src=x onerror=alert\(1\)>/);
});

test('the list keeps working when the server returns an unexpected shape', async t => {
  const f = await fixture(t, { items: [] });
  f.w.fetch = async () => ok({ items: [{ id: 'x', kind: 'mystery', received_at: '', url: '', title: '', reason: '', status: 'pending', error: '', document_id: '', file: {}, meta: {}, outcome: 'pending', state: '' }] });
  f.d.querySelector('#in-refresh').click();
  await settle();
  assert.equal(f.rows().length, 1);
  assert.match(f.list(), /mystery/);
});

test('reason textarea submits spaces tabs and newlines verbatim for text and files', async t => {
  const f=await fixture(t);
  const reason=' \t收藏理由\n第二行\t ';
  assert.equal(f.d.querySelector('#in-reason').tagName,'TEXTAREA');
  f.d.querySelector('#in-input').value='原文';f.d.querySelector('#in-reason').value=reason;
  f.d.querySelector('#in-form').dispatchEvent(new f.w.Event('submit',{bubbles:true,cancelable:true}));await settle();
  assert.equal(JSON.parse(f.calls.find(c=>c.url==='/api/intake/drop').opts.body).reason,reason);
  f.d.querySelector('#in-reason').value=reason;
  const event=new f.w.Event('drop',{bubbles:true,cancelable:true});event.dataTransfer={files:[{name:'shot.png',type:'image/png',size:2048}]};
  f.d.dispatchEvent(event);await settle();
  f.d.querySelector('#in-input').value='另一条';
  f.d.querySelector('#in-reason').value=reason;
  await submit(f);
  const second=JSON.parse(f.calls.filter(c=>c.url==='/api/intake/drop').pop().opts.body);
  assert.equal(second.reason,reason);
  assert.equal(second.file_token,id('e'));
});

test('media recognition shows unverified result model scope and reparse entry safely',async t=>{
 const f=await fixture(t,{items:[base({kind:'image',url:'',file:{name:'shot.png',bytes:2048,format:'PNG',width:24,height:16,kind:'image'},file_path:'media/x.png',media_kind:'image',recognition:{status:'succeeded',label:'AI 描述，未核实',text:'<script>bad()</script>红色方块',model:'synthetic vision',scope:'本张图片'}}),base({id:id('d'),kind:'video',file:{name:'clip.mp4',bytes:4096,format:'MP4',kind:'video'},file_path:'media/y.mp4',media_kind:'video',recognition:{status:'unavailable',error:'4090 正在使用，语音转写暂缓'}})]});
 assert.match(f.list(),/AI 描述，未核实/);assert.match(f.list(),/synthetic vision/);assert.match(f.list(),/语音转写暂缓/);
 assert.equal(f.d.querySelectorAll('[data-resolve]').length,2);assert.equal(f.d.querySelector('#in-list script'),null);
});

const analysed = extra => base({ analysis: { status: 'done', summary: '旧码头黄昏的蹲拍方法：水面把天空再复制一遍。',
  points: ['水面倒影是主手法', '人物只留背影'], why: '开头就能直接借的取景方式。',
  angles: [{ title: '写一条蹲拍笔记', how: '从水面倒影切入，交代蹲位与光线' }],
  related: [{ document_id: id('c'), title: '码头摄影笔记', url: '/knowledge.html?document=' + id('c'), excerpt: '黄昏码头，水面倒影与背影的拍法。' }],
  cards: 2, at: '2026-10-09T02:00:00+00:00', model: 'synthetic', inherited_from: '' }, ...extra });

test('an analysed row shows the four blocks and links to the related knowledge entries', async t => {
  const f = await fixture(t, { items: [analysed()] });
  for (const phrase of ['即时分析', '摘要与要点', '为什么值得收', '可写角度', '知识库里的相关条目']) assert.match(f.list(), new RegExp(phrase));
  assert.match(f.list(), /水面倒影是主手法/);
  assert.match(f.list(), /从水面倒影切入/);
  assert.match(f.list(), /码头摄影笔记/);
  assert.match(f.list(), /已生成 2 张候选提示词卡/);
  const related = f.d.querySelector('#in-list .in-analysis-block a[href^="/knowledge.html?document="]');
  assert.equal(related.getAttribute('href'), '/knowledge.html?document=' + id('c'));
  assert.equal(f.d.querySelectorAll('#in-list .in-analysis script').length, 0);
});

test('analysis text can never be injected as markup and says it is not the original', async t => {
  const f = await fixture(t, { items: [analysed({ analysis: { status: 'done', summary: '<img src=x onerror=alert(1)>', why: '<script>bad()</script>', points: [], angles: [], related: [], cards: 0 } })] });
  assert.equal(f.d.querySelectorAll('#in-list img').length, 0);
  assert.equal(f.d.querySelectorAll('#in-list script').length, 0);
  assert.match(f.list(), /AI 分析，不是原文/);
  assert.match(f.list(), /知识库里没找到相关条目/);
});

test('a queued analysis shows progress and the page keeps refreshing until it finishes', async t => {
  const f = await fixture(t, { items: [base({ analysis: { status: 'queued', error: '' } })] });
  assert.match(f.list(), /分析排队中/);
  const before = f.calls.filter(c => c.url === '/api/intake').length;
  await new Promise(resolve => setTimeout(resolve, 2200));
  assert.ok(f.calls.filter(c => c.url === '/api/intake').length > before, 'polling should continue while analysing');
});

test('a queued analysis waiting for media recognition shows the waiting reason', async t => {
  const f = await fixture(t, { items: [base({ analysis: { status: 'queued', error: '等识别完成再分析' } })] });
  assert.match(f.list(), /等识别完成再分析…/);
  assert.doesNotMatch(f.list(), /分析排队中/);
});

test('waiting on recognition offers an analyse-now escape that says what it skips', async t => {
  const f = await fixture(t, { items: [base({ analysis: { status: 'queued', error: '等识别完成再分析' } })] });
  const button = f.d.querySelector('#in-list [data-analyze-now]');
  assert.ok(button, '识别一直不出来时要有「现在就按理由/链接分析」的出口');
  assert.equal(button.textContent, '现在就按理由/链接分析');
  assert.equal(f.d.querySelectorAll('#in-list [data-analyze]').length, 0);   // 排队中不给普通「重新分析」
  button.click();
  await settle();
  assert.match(f.confirms().join('\n'), /不会用到还没出来的图片描述/);
  assert.match(f.confirms().join('\n'), /只按收藏理由、链接正文和文件名分析/);
  assert.ok(f.calls.some(c => c.url.endsWith('/api/intake/' + id('a') + '/analyze')));
  assert.match(f.d.querySelector('#in-status').textContent, /已开始重新分析，结果里会自动刷新/);
});

test('declining the analyse-now confirm sends nothing and keeps waiting', async t => {
  const f = await fixture(t, { confirm: false, items: [base({ analysis: { status: 'queued', error: '等识别完成再分析' } })] });
  f.d.querySelector('#in-list [data-analyze-now]').click();
  await settle();
  assert.ok(!f.calls.some(c => c.url.endsWith('/analyze')), '不确认就不能发分析请求');
  assert.match(f.d.querySelector('#in-status').textContent, /未分析：继续等识别结果/);
});

test('an analysis that fell behind the newest recognition result says so on the card', async t => {
  const f = await fixture(t, { items: [analysed({ analysis: { ...analysed().analysis,
    parse_notes: ['识别结果又更新过，可点重新分析'] } })] });
  assert.match(f.list(), /识别结果又更新过，可点重新分析/);
});

test('a queued analysis for another reason offers no analyse-now', async t => {
  const f = await fixture(t, { items: [base({ analysis: { status: 'queued', error: '同一模型已有后台任务，排队等待，不重复占用推理服务' } })] });
  assert.equal(f.d.querySelectorAll('#in-list [data-analyze-now]').length, 0);
  assert.match(f.list(), /同一模型已有后台任务/);
});

test('a running analysis offers no analyse-now either', async t => {
  const f = await fixture(t, { items: [base({ analysis: { status: 'running' } })] });
  assert.equal(f.d.querySelectorAll('#in-list [data-analyze-now]').length, 0);
  assert.match(f.list(), /正在分析…/);
});

test('an analysis service that is not ready offers no analyse-now button', async t => {
  const f = await fixture(t, { staleService: true, items: [base({ analysis: { status: 'queued', error: '等识别完成再分析' } })] });
  await settle();
  assert.equal(f.d.querySelectorAll('#in-list [data-analyze-now]').length, 0);
});

test('polling stops once no analysis or recognition is running', async t => {
  const f = await fixture(t, { items: [analysed()] });
  const before = f.calls.filter(c => c.url === '/api/intake').length;
  await new Promise(resolve => setTimeout(resolve, 2200));
  assert.equal(f.calls.filter(c => c.url === '/api/intake').length, before);
});

test('a failed analysis states the real reason and offers a rerun that is not silent', async t => {
  const f = await fixture(t, { items: [base({ analysis: { status: 'failed', error: '模型未返回有效正文，本条未推进', retryable: true } })] });
  assert.match(f.list(), /分析未完成：模型未返回有效正文/);
  const button = f.d.querySelector('#in-list [data-analyze]');
  assert.ok(button);
  button.click();
  await settle();
  assert.ok(f.calls.some(c => c.url.endsWith('/api/intake/' + id('a') + '/analyze')));
  assert.match(f.d.querySelector('#in-status').textContent, /已开始重新分析/);
});

test('an already collected link shows the analysis its document already has', async t => {
  const f = await fixture(t, { items: [analysed({ outcome: 'duplicate', state: '已存在，未重复导入',
    analysis: { ...analysed().analysis, inherited_from: id('f') } })] });
  assert.match(f.list(), /已存在，未重复导入/);
  assert.match(f.list(), /沿用已有分析/);
  assert.match(f.list(), /摘要与要点/);
});

test('a link read that never answers stops waiting and tells the user to refresh', async t => {
  const f = await fixture(t, { items: [], slowResolve: true, longTimerNow: true });
  f.d.querySelector('#in-input').value = 'https://example.com/slow';
  await submit(f);
  assert.match(f.d.querySelector('#in-status').textContent, /超过 60 秒没回话/);
  assert.match(f.d.querySelector('#in-status').textContent, /刷新/);
});

test('a running resolve shows the clicked row working and sends no second request', async t => {
  const f = await fixture(t, { items: [base({ outcome: 'pending', status: 'pending', state: '已收下，尚未解析', document_id: '', knowledge_url: '' })], slowResolve: true });
  f.d.querySelector('#in-list [data-resolve]').click();
  await settle();
  const button = f.d.querySelector('#in-list [data-resolve]');
  assert.equal(button.textContent, '解析中…');
  assert.ok(button.disabled, '进行中的按钮要禁用，不能看起来还能点');
  assert.match(f.d.querySelector('#in-status').textContent, /正在重新解析…/);
  button.click();
  await settle();
  assert.equal(f.calls.filter(c => c.url.endsWith('/resolve')).length, 1, '进行中不能再发第二条解析请求');
});

test('a resolved item with no analysis yet says it has not started and offers one queue', async t => {
  const f = await fixture(t, { items: [base({})] });
  assert.match(f.list(), /还没开始分析/);
  assert.match(f.list(), /排队一次/);
  assert.doesNotMatch(f.list(), /这条还没分析/);
  assert.equal(f.d.querySelectorAll('#in-list [data-analyze]').length, 1);
  assert.equal(f.d.querySelector('#in-service').hidden, true);
});

test('a backend that does not know the analysis routes is named as an old service', async t => {
  const f = await fixture(t, { staleService: true, items: [
    base({ analysis: { status: 'failed', error: '模型未返回有效正文，本条未推进', retryable: true } }),
    base({ id: id('k') })] });
  assert.match(f.list(), /分析服务未就绪/);
  assert.match(f.list(), /工作台服务还是旧版本/);
  assert.match(f.list(), /分析未完成：模型未返回有效正文/);
  assert.doesNotMatch(f.list(), /这条还没分析/);
  assert.doesNotMatch(f.list(), /可点下方“重新分析”/);
  assert.doesNotMatch(f.list(), /操作不存在|页面不存在/);
  assert.equal(f.d.querySelectorAll('#in-list [data-analyze]').length, 0);
  assert.match(f.d.querySelector('#in-service').textContent, /重启后才能分析/);
  assert.equal(f.d.querySelector('#in-service').hidden, false);
});

test('a 404 on the analyse call says old service, never 操作不存在, and drops the misleading entry', async t => {
  const f = await fixture(t, { items: [analysed()], staleAnalyze: true });
  const button = f.d.querySelector('#in-list [data-analyze]');
  assert.ok(button);
  button.click();
  await settle();
  assert.match(f.d.querySelector('#in-status').textContent, /工作台服务还是旧版本/);
  assert.doesNotMatch(f.d.querySelector('#in-status').textContent, /操作不存在/);
  assert.equal(f.d.querySelectorAll('#in-list [data-analyze]').length, 0);
});

test('a backend that says it cannot analyse shows an unready service, not a rerun entry', async t => {
  const f = await fixture(t, { items: [base({})], analysisSupported: false });
  assert.match(f.list(), /分析服务未就绪/);
  assert.doesNotMatch(f.list(), /还没开始分析|这条还没分析/);
  assert.equal(f.d.querySelectorAll('#in-list [data-analyze]').length, 0);
  assert.match(f.d.querySelector('#in-service').textContent, /分析服务未就绪/);
});

// ── 手动删除：确认框、共用正文、删后即时移除 ────────────────────────────────

test('every row offers a delete entry, even while an analysis is running', async t => {
  const items = [base({}), base({ id: id('k'), kind: 'text', url: '', analysis: { status: 'running', error: '' } }),
    base({ id: id('m'), kind: 'image', url: '', file_path: 'media/x.png', outcome: 'pending', status: 'pending', document_id: '', knowledge_url: '' })];
  const f = await fixture(t, { items });
  assert.equal(f.d.querySelectorAll('#in-list [data-delete]').length, 3);
  for (const button of f.d.querySelectorAll('#in-list [data-delete]')) assert.equal(button.textContent, '删除');
  assert.match(f.d.querySelector('#in-list .in-delete') ? f.d.querySelector('#in-list .in-delete').className : '', /in-delete/);
});

test('deleting asks first and the confirm box names everything that goes with it', async t => {
  const f = await fixture(t, { items: [base({ file_path: 'media/x.png', analysis: { status: 'done', summary: '摘要', points: [], why: '理由', angles: [], related: [], cards: 0 } })], confirm: false });
  f.d.querySelector('#in-list [data-delete]').click();
  await settle();
  assert.equal(f.confirms().length, 1);
  const text = f.confirms()[0];
  assert.match(text, /这条回收项/);
  assert.match(text, /即时分析/);
  assert.match(text, /知识库里的这篇正文/);
  assert.match(text, /检索索引/);
  assert.match(text, /本机媒体文件/);
  assert.match(text, /来源已删除/);
  assert.match(text, /不能撤销/);
  assert.equal(f.deleted(), null);
  assert.equal(f.rows().length, 1);
  assert.match(f.d.querySelector('#in-status').textContent, /未删除/);
});

test('a shared document is explained in the confirm box instead of silently kept', async t => {
  const f = await fixture(t, { items: [base({ document_shares: 3 })], confirm: false });
  f.d.querySelector('#in-list [data-delete]').click();
  await settle();
  assert.match(f.confirms()[0], /还被另 2 条回收项引用/);
  assert.match(f.confirms()[0], /删这一条不会删正文/);
  assert.match(f.confirms()[0], /只有这是最后一条引用时才会删/);
});

const deletionClause = text => (text.match(/会一起删掉：[\s\S]*?(?:。|\n)/) || [''])[0];

test('a body collected in the knowledge base first is promised to stay', async t => {
  const f = await fixture(t, { items: [base({ document_owned: false })], confirm: false });
  f.d.querySelector('#in-list [data-delete]').click();
  await settle();
  const text = f.confirms()[0];
  assert.match(text, /知识库里的这篇正文不是回收站存的/);
  assert.match(text, /不会被删/);
  assert.match(text, /只删这条回收项/);
  // 开头那句「会一起删掉」不能许下删知识库正文。
  assert.ok(!/知识库.*正文/.test(deletionClause(text)), deletionClause(text));
  assert.equal(f.deleted(), null);
});

test('a body the recycle bin stored itself is promised to go with it', async t => {
  const f = await fixture(t, { items: [base({ document_owned: true })], confirm: false });
  f.d.querySelector('#in-list [data-delete]').click();
  await settle();
  const text = f.confirms()[0];
  assert.match(text, /回收站自己存的[\s\S]*会一起删掉/);
  assert.match(text, /检索索引/);
  assert.ok(!/知识库.*正文/.test(deletionClause(text)), deletionClause(text));
});

test('an unknown owner is described conservatively instead of promising a deletion', async t => {
  const f = await fixture(t, { items: [base({ document_owned: null })], confirm: false });
  f.d.querySelector('#in-list [data-delete]').click();
  await settle();
  const text = f.confirms()[0];
  assert.match(text, /只有在确认它是回收站自己存的、而且这是最后一条引用时才会删/);
  assert.ok(!/知识库.*正文/.test(deletionClause(text)), deletionClause(text));
  const shared = await fixture(t, { items: [base({ document_owned: null, document_shares: 4 })], confirm: false });
  shared.d.querySelector('#in-list [data-delete]').click();
  await settle();
  assert.match(shared.confirms()[0], /还被另 3 条回收项引用/);
  assert.match(shared.confirms()[0], /删这一条不会删正文/);
  // 旧后端缺字段时也不能只拿「最后一条引用」当删正文的充分条件。
  assert.match(shared.confirms()[0], /只有在确认它是回收站自己存的、而且这是最后一条引用时才会删/);
  assert.ok(!/只有这是最后一条引用时才会删/.test(shared.confirms()[0]), shared.confirms()[0]);
  const missing = await fixture(t, { items: [base({ document_owned: undefined, document_shares: 2 })], confirm: false });
  missing.d.querySelector('#in-list [data-delete]').click();
  await settle();
  assert.match(missing.confirms()[0], /只有在确认它是回收站自己存的/);
});

test('confirming deletes once, removes the row at once and says it is gone', async t => {
  const f = await fixture(t, { items: [base({}), base({ id: id('k'), kind: 'text', url: '' })] });
  f.d.querySelector('#in-list [data-delete]').click();
  await settle();
  const call = f.calls.find(c => c.url === '/api/intake/' + id('a') + '/delete');
  assert.ok(call, 'a delete call is expected');
  assert.equal(call.opts.method, 'POST');
  assert.equal(call.opts.headers['X-Article-Studio'], '1');
  assert.deepEqual(JSON.parse(call.opts.body), {});
  assert.equal(f.rows().length, 1);
  assert.match(f.list(), /本机文字/);
  assert.doesNotMatch(f.list(), /example\.com\/post/);
  assert.match(f.d.querySelector('#in-status').textContent, /已删除/);
});

test('a failed delete keeps the row and states the real reason', async t => {
  const f = await fixture(t, { items: [base({})], deleteFails: true });
  f.d.querySelector('#in-list [data-delete]').click();
  await settle();
  assert.equal(f.rows().length, 1);
  assert.match(f.d.querySelector('#in-status').textContent, /回收项不存在/);
});

test('a delete while another action is running says so instead of doing nothing', async t => {
  const f = await fixture(t, { items: [base({ outcome: 'pending', status: 'pending', document_id: '', knowledge_url: '' })], slowResolve: true });
  f.d.querySelector('#in-list [data-resolve]').click();
  await tick();
  f.d.querySelector('#in-list [data-delete]').click();
  await settle();
  assert.equal(f.confirms().length, 0);
  assert.match(f.d.querySelector('#in-status').textContent, /上一条还在处理/);
});

test('a tolerant reading still shows the angles it kept and says what it dropped', async t => {
  const items = [base({ analysis: { status: 'done', summary: '摘要一句。', points: ['要点一'], why: '',
    angles: [{ title: '只有一句话的角度', how: '' }, { title: '', how: '只有写法没有标题' }],
    related: [], cards: 0, parse_notes: ['可写角度 2 条不合要求已忽略', '要点 1 条不合要求已忽略'] } })];
  const f = await fixture(t, { items });
  assert.match(f.list(), /只有一句话的角度/);
  assert.match(f.list(), /未命名角度/);
  assert.match(f.list(), /模型未给出为什么值得收/);
  assert.match(f.list(), /可写角度 2 条不合要求已忽略/);
  assert.doesNotMatch(f.list(), /分析未完成/);
});

// ── 重新解析：结果可见（M1）、按钮语义（L1）、失败仍可重试（M2）─────────────────

const failedLink = extra => base({ status: 'failed', outcome: 'failed', error: '联网服务响应超时，请稍后重试',
  state: '抓取失败：联网服务响应超时，请稍后重试；已存入知识库（仅链接）',
  content_scope: '仅收藏链接；原文正文未读取（抓取失败：联网服务响应超时，请稍后重试）', meta: { fetched: false }, ...extra });

test('a retry that answers 200 but is still failed says so and never clears the receipt', async t => {
  const f = await fixture(t, { items: [failedLink()], resolve: failedLink() });
  f.d.querySelector('#in-list [data-resolve]').click();
  await settle();
  const status = f.d.querySelector('#in-status').textContent;
  assert.match(f.d.querySelector('#in-status').textContent, /重新解析仍失败：联网服务响应超时/);
  assert.doesNotMatch(status, /请稍后重试/, '顶部只给归类原因，不显示原始错误全文');
  assert.match(f.list(), /本次重新解析失败于/);
  assert.ok(f.d.querySelector('#in-list .in-retry-note'), '该行要显示本次重试完成时间');
  assert.equal(f.d.querySelectorAll('#in-list [data-resolve]').length, 1);
  assert.equal(f.d.querySelector('#in-list [data-resolve]').disabled, false, '结束后按钮要恢复可点');
});

test('a retry that succeeds says 已重新解析 and stamps the row with the time', async t => {
  const stored = base({ status: 'resolved', outcome: 'stored', error: '', state: '已存入知识库，已抓取正文，共 12 字',
    meta: { fetched: true, fetched_at: '2026-10-11T01:00:00+00:00', total_chars: 12 } });
  const f = await fixture(t, { items: [failedLink()], resolve: stored });
  f.d.querySelector('#in-list [data-resolve]').click();
  await settle();
  assert.equal(f.d.querySelector('#in-status').textContent, '已重新解析');
  assert.match(f.list(), /本次重新解析完成于/);
});

test('an item with no attachment is judged by the body layer even with an empty recognition receipt', async t => {
  // 无附件时 recognition={} 完全正常，不能当成「识别未确认」。
  const f = await fixture(t, { items: [failedLink()], resolve: base({ recognition: {} }) });
  f.d.querySelector('#in-list [data-resolve]').click();
  await settle();
  assert.equal(f.d.querySelector('#in-status').textContent, '已重新解析');
  assert.doesNotMatch(f.d.querySelector('#in-status').textContent, /识别结果未确认/);
  assert.match(f.list(), /本次重新解析完成于/);
});

test('a failed link the backend labels duplicate still keeps its retry entry', async t => {
  const dupFailed = base({ status: 'failed', outcome: 'duplicate', error: '页面没有返回可读正文',
    state: '已存在，未重复导入', meta: { fetched: false, duplicate: true } });
  const f = await fixture(t, { items: [dupFailed], resolve: dupFailed });
  const button = f.d.querySelector('#in-list [data-resolve]');
  assert.ok(button, 'status=failed 或有 error 就要显示重试按钮，不能只看 outcome');
  assert.equal(button.textContent, '重新解析');
  button.click();
  await settle();
  assert.match(f.d.querySelector('#in-status').textContent, /重新解析仍失败：页面没有返回可读正文/);
  assert.match(f.list(), /本次重新解析失败于/);
});

test('attachment, media and failed link each name what the button will redo', async t => {
  const items = [
    base({ kind: 'link', media_kind: 'image', file_path: 'media/x.png', file: { name: 'shot.png', bytes: 2048, format: 'PNG', kind: 'image' }, state: '已存入知识库，已抓取正文，共 12 字；图片内容未识别' }),
    base({ id: id('d'), kind: 'image', url: '', media_kind: 'image', file_path: 'media/y.png', file: { name: 'again.png', bytes: 2048, format: 'PNG', kind: 'image' } }),
    failedLink({ id: id('e') })];
  const f = await fixture(t, { items });
  const buttons = [...f.d.querySelectorAll('#in-list [data-resolve]')];
  assert.deepEqual(buttons.map(button => button.textContent), ['重新识别附件', '重新识别', '重新解析']);
  assert.match(buttons[0].getAttribute('title'), /不会重新抓链接正文/);
  assert.match(buttons[1].getAttribute('title'), /只重新识别这个附件的内容/);
});

test('a media retry answers with the same wording as the button it came from', async t => {
  const attached = base({ kind: 'link', media_kind: 'image', file_path: 'media/x.png', status: 'resolved', outcome: 'stored', error: '',
    file: { name: 'shot.png', bytes: 2048, format: 'PNG', kind: 'image' }, state: '已存入知识库，已抓取正文，共 12 字；图片内容已识别（AI 结果，未核实）' });
  const f = await fixture(t, { items: [attached], resolve: () => ({ ...attached,
    recognition: { status: 'succeeded', label: 'AI 描述，未核实', text: '一个红色方块', model: 'synthetic vision', scope: '本张图片', finished_at: new Date().toISOString() } }) });
  const button = f.d.querySelector('#in-list [data-resolve]');
  assert.equal(button.textContent, '重新识别附件');
  button.click();
  await settle();
  assert.equal(f.d.querySelector('#in-status').textContent, '已重新识别附件');
  assert.match(f.list(), /本次附件识别完成于/);
});

test('while one retry runs the clicked row works and the others show they are waiting', async t => {
  const f = await fixture(t, { items: [failedLink(), failedLink({ id: id('g') })], slowResolve: true });
  f.d.querySelector('#in-list [data-resolve]').click();
  await settle();
  const buttons = [...f.d.querySelectorAll('#in-list [data-resolve]')];
  assert.equal(buttons.length, 2);
  assert.equal(buttons[0].textContent, '解析中…');
  assert.ok(buttons[0].disabled);
  assert.ok(buttons[0].className.includes('in-resolving'));
  assert.ok(buttons[1].disabled, 'busy 时其他行不能看起来可点却没反应');
  assert.ok(buttons[1].className.includes('in-waiting'));
  assert.match(buttons[1].getAttribute('title'), /上一条还在处理：正在重新解析/);
  buttons[1].click();
  await settle();
  assert.equal(f.calls.filter(c => c.url.endsWith('/resolve')).length, 1, '进行中不能再发第二条请求');
});

test('the first drop and a later retry read the same failure verdict', async t => {
  const raw = failedLink({ error: 'Command failed: remio fetch exit 1' });
  const f = await fixture(t, { items: [], resolve: raw });
  f.d.querySelector('#in-input').value = 'https://example.com/x';
  await submit(f);
  assert.equal(f.d.querySelector('#in-status').textContent, '抓取失败：其他抓取失败');
  f.d.querySelector('#in-list [data-resolve]').click();
  await settle();
  assert.equal(f.d.querySelector('#in-status').textContent, '重新解析仍失败：其他抓取失败');
  assert.doesNotMatch(f.d.querySelector('#in-status').textContent, /Command failed|remio fetch/);
});

test('the retry button states need styles so a waiting button does not read as dead', async t => {
  const css = fs.readFileSync('public/intake.css', 'utf8');
  assert.match(css, /\.in-item footer \.button:disabled\{[^}]*cursor:not-allowed\}/);
  assert.match(css, /\.in-item footer \.in-resolving\{/);
  assert.match(css, /\.in-item footer \.in-waiting\{[^}]*cursor:progress\}/);
  assert.match(css, /\.in-retry-note\{/);
});

test('a resolve that never answers keeps the timeout notice and stamps no completion time', async t => {
  const f = await fixture(t, { items: [failedLink()], slowResolve: true, longTimerNow: true });
  f.d.querySelector('#in-list [data-resolve]').click();
  await settle();
  assert.match(f.d.querySelector('#in-status').textContent, /超过 60 秒没回话/);
  assert.equal(f.d.querySelector('#in-list .in-retry-note'), null, '没回话不能谎称本次已完成');
  assert.equal(f.d.querySelector('#in-list [data-resolve]').disabled, false, '超时后按钮要恢复');
});

test('while another action is in flight the retry buttons say what they are waiting for', async t => {
  const f = await fixture(t, { items: [failedLink()], stageGate: true });
  pick(f, 'shot.png');
  await settle();
  f.d.querySelector('#in-input').value = 'https://example.com/one';
  fire(f);
  await settle();
  const button = f.d.querySelector('#in-list [data-resolve]');
  assert.ok(button.disabled, '上传在途时重试按钮要看得出在等，不是点了没反应');
  assert.match(button.getAttribute('title'), /上一条还在处理：正在收下你刚投的那条/);
  assert.equal(f.calls.filter(c => c.url.endsWith('/resolve')).length, 0);
  f.stageGate().resolve();
  await settle();
  assert.equal(f.d.querySelector('#in-list [data-resolve]').disabled, false, '那一步结束后按钮要恢复可点');
});

// ── 返工 1b：非终态不写完成时间（M1）、不可确认不说成功（M2）、响应体也有截止（M3）、识别状态补全 ──

test('an unknown status or outcome never gets a completion time or a success word', async t => {
  const f = await fixture(t, { items: [failedLink()], rawResolve: { id: id('a'), kind: 'link', status: 'mystery', outcome: 'mystery' } });
  f.d.querySelector('#in-list [data-resolve]').click();
  await settle();
  assert.equal(f.d.querySelector('#in-list .in-retry-note'), null, '未确认完成不能显示完成时间');
  assert.doesNotMatch(f.list(), /完成于/);
  assert.match(f.d.querySelector('#in-status').textContent, /结果未确认/);
  assert.doesNotMatch(f.d.querySelector('#in-status').textContent, /已重新解析|已重新识别/);
  const button = f.d.querySelector('#in-list [data-resolve]');
  assert.ok(button, '未确认的结果要保留重试入口');
  assert.equal(button.disabled, false, '结束后按钮要恢复');
});

test('a pending answer says it is not finished and stamps no completion time', async t => {
  const f = await fixture(t, { items: [failedLink()], rawResolve: { id: id('a'), kind: 'link', status: 'pending', outcome: 'pending', error: '', state: '已收下，尚未解析' } });
  f.d.querySelector('#in-list [data-resolve]').click();
  await settle();
  assert.equal(f.d.querySelector('#in-list .in-retry-note'), null, 'pending 不是终态，不能写完成时间');
  assert.doesNotMatch(f.list(), /完成于/);
  assert.match(f.d.querySelector('#in-status').textContent, /还没完成/);
  assert.doesNotMatch(f.d.querySelector('#in-status').textContent, /已重新解析|已重新识别/);
});

for (const [name, reply] of [['an empty object', {}], ['no id', { status: 'resolved', outcome: 'stored' }],
  ['no outcome', { id: id('a'), status: 'resolved' }], ['no status', { id: id('a'), outcome: 'stored' }]])
  test(`a resolve answer with ${name} is unconfirmed and keeps the original row`, async t => {
    const f = await fixture(t, { items: [failedLink()], rawResolve: reply });
    f.d.querySelector('#in-list [data-resolve]').click();
    await settle();
    assert.match(f.d.querySelector('#in-status').textContent, /结果未确认/);
    assert.doesNotMatch(f.d.querySelector('#in-status').textContent, /已重新解析|已重新识别|已存入知识库/);
    assert.equal(f.rows().length, 1, '不能插入无 id 条目');
    assert.match(f.list(), /抓取失败：联网服务响应超时/, '原条目不能被删掉');
    assert.equal(f.d.querySelector('#in-list .in-retry-note'), null);
    const button = f.d.querySelector('#in-list [data-resolve]');
    assert.ok(button && !button.disabled, '重试入口要留着');
  });

test('an answer whose outcome says failed is a failure even with no error text', async t => {
  const f = await fixture(t, { items: [failedLink()], rawResolve: { id: id('a'), kind: 'link', status: 'resolved', outcome: 'failed', error: '', state: '' } });
  f.d.querySelector('#in-list [data-resolve]').click();
  await settle();
  assert.match(f.d.querySelector('#in-status').textContent, /重新解析仍失败：原因未记录/);
  assert.doesNotMatch(f.d.querySelector('#in-status').textContent, /已重新解析/);
  assert.match(f.list(), /本次重新解析失败于/);
  const button = f.d.querySelector('#in-list [data-resolve]');
  assert.ok(button && !button.disabled, '只有 outcome=failed 也要保留重试入口');
});

test('a response body that is not valid JSON gets an error receipt, never a silent success', async t => {
  const f = await fixture(t, { items: [failedLink()] });
  const original = f.w.fetch;
  f.w.fetch = async (url, opts) => String(url).endsWith('/resolve')
    ? { ok: true, json: async () => { throw new SyntaxError('Unexpected token < in JSON at position 0'); } }
    : original(url, opts);
  f.d.querySelector('#in-list [data-resolve]').click();
  await settle();
  assert.match(f.d.querySelector('#in-status').textContent, /读不出结果/);
  assert.doesNotMatch(f.d.querySelector('#in-status').textContent, /已重新解析|已重新识别/);
  assert.equal(f.rows().length, 1, 'JSON 读不出不能改列表');
  assert.match(f.list(), /抓取失败：联网服务响应超时/);
  assert.equal(f.d.querySelector('#in-list .in-retry-note'), null);
  assert.equal(f.d.querySelector('#in-list [data-resolve]').disabled, false, '按钮要恢复');
});

test('a resolve whose response body never arrives times out and re-enables the button', async t => {
  const f = await fixture(t, { items: [failedLink()] });
  const original = f.w.fetch;
  let aborted = false, release;
  f.w.setTimeout = (handler, ms) => setTimeout(handler, ms >= 60000 ? 10 : ms);
  f.w.clearTimeout = clearTimeout;
  f.w.fetch = async (url, opts) => String(url).endsWith('/resolve')
    ? { ok: true, json: () => new Promise(resolve => {
        release = () => resolve(failedLink());
        opts.signal.addEventListener('abort', () => { aborted = true; resolve(failedLink()); });
      }) }
    : original(url, opts);
  f.d.querySelector('#in-list [data-resolve]').click();
  await new Promise(resolve => setTimeout(resolve, 40));
  assert.equal(aborted, true, '响应体也要受截止时间约束');
  assert.match(f.d.querySelector('#in-status').textContent, /超过 60 秒没回话/);
  assert.doesNotMatch(f.d.querySelector('#in-status').textContent, /已重新解析/);
  assert.equal(f.d.querySelector('#in-list [data-resolve]').disabled, false, '超时后按钮要全部恢复');
  assert.equal(f.d.querySelector('#in-list .in-retry-note'), null, '超时不能写完成时间');
  release();
  await settle();
  assert.equal(f.rows().length, 1);
});

const mediaReply = build => () => ({ ...attachedMedia(), recognition: build() });
const runMedia = async (t, build) => {
  const f = await fixture(t, { items: [attachedMedia()], resolve: mediaReply(build) });
  f.d.querySelector('#in-list [data-resolve]').click();
  await settle();
  return { f, status: f.d.querySelector('#in-status').textContent, note: f.d.querySelector('#in-list .in-retry-note') };
};

test('a queued attachment says submitted for recognition and stamps no time', async t => {
  const { f, status, note } = await runMedia(t, () => ({ status: 'queued', started_at: '2026-10-11T01:00:00+00:00' }));
  assert.match(status, /附件识别已提交排队/);
  assert.match(status, /还没完成/);
  assert.doesNotMatch(status, /已重新识别/);
  assert.equal(note, null, '识别排队中不能写完成时间');
  assert.doesNotMatch(f.list(), /完成于/);
  assert.match(f.list(), /媒体识别排队中/);
});

test('a running attachment says recognition is still going and stamps no time', async t => {
  const { f, status, note } = await runMedia(t, () => ({ status: 'running', started_at: '2026-10-11T01:00:00+00:00' }));
  assert.match(status, /附件识别正在进行/);
  assert.match(status, /还没完成/);
  assert.doesNotMatch(status, /已重新识别/);
  assert.equal(note, null);
  assert.doesNotMatch(f.list(), /完成于/);
});

test('a finished recognition stamps its own finished_at, not the request time', async t => {
  let finishedAt = '';
  const f = await fixture(t, { items: [attachedMedia()], resolve: () => {
    finishedAt = new Date().toISOString();
    return { ...attachedMedia(), recognition: { status: 'succeeded', label: 'AI 描述，未核实', text: '一个红色方块', model: 'synthetic vision', scope: '本张图片', finished_at: finishedAt } };
  } });
  f.d.querySelector('#in-list [data-resolve]').click();
  await settle();
  assert.equal(f.d.querySelector('#in-status').textContent, '已重新识别');
  const note = f.d.querySelector('#in-list .in-retry-note');
  assert.ok(note);
  assert.match(note.textContent, /本次附件识别完成于/);
  assert.ok(note.textContent.includes(clockOf(finishedAt)), note.textContent);
});

test('a recognition result left over from an earlier round gets no completion time', async t => {
  const { f, note } = await runMedia(t, () => ({ status: 'succeeded', label: 'AI 描述，未核实', text: '旧结果', model: 'synthetic', scope: '本张图片', finished_at: '2026-10-09T01:00:00+00:00' }));
  assert.equal(note, null, '不是本轮的识别结果不能当本次完成时间');
  assert.doesNotMatch(f.list(), /完成于/);
});

test('a failed attachment says the body and file are kept and never claims a re-read', async t => {
  const { f, status, note } = await runMedia(t, () => ({ status: 'failed', error: '媒体识别失败，原始文件和入库记录保留', finished_at: new Date().toISOString() }));
  assert.match(status, /附件识别失败：媒体识别失败/);
  assert.match(status, /正文与原始文件已保留/);
  assert.doesNotMatch(status, /已重新识别/);
  assert.match(f.list(), /附件识别失败/);
  assert.match(note.textContent, /本次附件识别失败于/);
});

test('an unavailable attachment says retry later and is not sold as recognised', async t => {
  const { f, status, note } = await runMedia(t, () => ({ status: 'unavailable', error: '4090 正在使用，语音转写暂缓；可稍后重新解析', finished_at: new Date().toISOString() }));
  assert.match(status, /附件暂时识别不了/);
  assert.match(status, /不算识别成功/);
  assert.doesNotMatch(status, /已重新识别/);
  assert.match(f.list(), /4090 正在使用/);
  assert.doesNotMatch(f.list(), /抓取失败/);
  assert.equal(note, null, '暂不可用不写识别完成时间');
});

test('an interrupted recognition says the last run was cut short, not this one finished', async t => {
  const { f, status, note } = await runMedia(t, () => ({ status: 'interrupted', error: '工作台重启中断媒体识别；可重新解析' }));
  assert.match(status, /上次附件识别被中断/);
  assert.doesNotMatch(status, /已重新识别/);
  assert.match(f.list(), /上次识别被中断/);
  assert.equal(note, null, '中断没有 finished_at，要允许缺失且不写时间');
  assert.doesNotMatch(f.list(), /完成于/);
});

test('a video with no audio track says there is nothing to transcribe', async t => {
  const f = await fixture(t, { items: [attachedMedia({ kind: 'video', media_kind: 'video', file_path: 'media/y.mp4', file: { name: 'clip.mp4', bytes: 4096, format: 'MP4', kind: 'video' }, state: '已存入知识库；视频正文与语音未读取' })],
    resolve: () => ({ ...attachedMedia({ kind: 'video', media_kind: 'video', file_path: 'media/y.mp4', file: { name: 'clip.mp4', bytes: 4096, format: 'MP4', kind: 'video' } }),
      recognition: { status: 'no_audio', text: '', label: '语音转写，未核实', scope: '视频无音轨；未生成语音转写', model: 'synthetic', finished_at: new Date().toISOString() } }) });
  f.d.querySelector('#in-list [data-resolve]').click();
  await settle();
  assert.match(f.d.querySelector('#in-status').textContent, /视频无音轨，未生成语音转写/);
  assert.doesNotMatch(f.d.querySelector('#in-status').textContent, /语音已|已转写/);
  const note = f.d.querySelector('#in-list .in-retry-note');
  assert.match(note.textContent, /本次附件识别检查结束于/);
});

test('an attachment with an empty recognition receipt is unconfirmed, not done', async t => {
  const { f, status, note } = await runMedia(t, () => ({}));
  assert.match(status, /附件识别结果未确认/);
  assert.doesNotMatch(status, /已重新识别/);
  assert.equal(note, null);
  assert.doesNotMatch(f.list(), /完成于/);
});

test('an attachment with an unknown recognition status is unconfirmed', async t => {
  const { f, status, note } = await runMedia(t, () => ({ status: 'mystery', error: 'synthetic' }));
  assert.match(status, /附件识别结果未确认/);
  assert.match(f.list(), /不是已知状态/);
  assert.equal(note, null);
  assert.doesNotMatch(f.list(), /完成于/);
});

test('a failed body stays failed even when the attachment was recognised', async t => {
  const broken = failedLink({ kind: 'link', media_kind: 'image', file_path: 'media/x.png', file: { name: 'shot.png', bytes: 2048, format: 'PNG', kind: 'image' } });
  const f = await fixture(t, { items: [broken], resolve: () => ({ ...broken, recognition: { status: 'succeeded', label: 'AI 描述，未核实', text: '一张图', model: 'synthetic', scope: '本张图片', finished_at: new Date().toISOString() } }) });
  f.d.querySelector('#in-list [data-resolve]').click();
  await settle();
  assert.match(f.d.querySelector('#in-status').textContent, /重新解析仍失败：联网服务响应超时/);
  assert.doesNotMatch(f.d.querySelector('#in-status').textContent, /已重新识别/);
});

test('a recognition that finishes later stamps the recognition time, not the request time', async t => {
  const queued = attachedMedia({ recognition: { status: 'queued', started_at: '2026-10-11T01:00:00+00:00' } });
  const f = await fixture(t, { items: [queued], resolve: () => queued });
  f.d.querySelector('#in-list [data-resolve]').click();
  await settle();
  assert.equal(f.d.querySelector('#in-list .in-retry-note'), null, '排队中先不写时间');
  let finishedAt = '';
  f.w.fetch = async () => {
    finishedAt = new Date().toISOString();
    return ok({ schema: 'intake/v1', note: '', items: [{ ...queued, recognition: { status: 'succeeded', label: 'AI 描述，未核实', text: '一个红色方块', model: 'synthetic vision', scope: '本张图片', finished_at: finishedAt } }] });
  };
  f.d.querySelector('#in-refresh').click();
  await settle();
  const note = f.d.querySelector('#in-list .in-retry-note');
  assert.ok(note, '识别转成终态后要给出时间');
  assert.match(note.textContent, /本次附件识别完成于/);
  assert.ok(note.textContent.includes(clockOf(finishedAt)), note.textContent);
});

test('a text drop with an attachment is never called a link', async t => {
  const items = [
    base({ kind: 'text', url: '', text_chars: 20, media_kind: 'image', file_path: 'media/x.png', file: { name: 'shot.png', bytes: 2048, format: 'PNG', kind: 'image' }, state: '已存入知识库，原文逐字保存；图片内容未识别' }),
    base({ id: id('d'), kind: 'link', media_kind: 'image', file_path: 'media/y.png', file: { name: 'shot.png', bytes: 2048, format: 'PNG', kind: 'image' } })];
  const f = await fixture(t, { items });
  const buttons = [...f.d.querySelectorAll('#in-list [data-resolve]')];
  assert.deepEqual(buttons.map(button => button.textContent), ['重新识别附件', '重新识别附件']);
  assert.match(buttons[0].getAttribute('title'), /这段文字的正文已存过/);
  assert.doesNotMatch(buttons[0].getAttribute('title'), /这条链接/);
  assert.match(buttons[1].getAttribute('title'), /这条链接的正文已存过/);
});

// ---- fixes-intake-resolve-1c：回执归属（M1）与仅 pending 的自动刷新（M2）----
// 另一条记录的回执：形状合法、状态是终态，但 id 不是被点的那一条。
const foreign = extra => base({ id: id('c'), status: 'resolved', outcome: 'stored', error: '',
  state: '已存入知识库，已抓取正文，共 12 字', meta: { fetched: true, total_chars: 12 }, ...extra });

for (const [name, reply] of [['a stored body', foreign()],
  ['a duplicate body', foreign({ outcome: 'duplicate' })],
  ['a recognised attachment', attachedMedia({ id: id('c'), recognition: { status: 'succeeded', label: 'AI 描述，未核实', text: '一个红色方块', model: 'synthetic vision', scope: '本张图片', finished_at: '2026-10-11T02:00:00+00:00' } })]])
  test(`a resolve answer belonging to another item (${name}) cannot claim success`, async t => {
    const f = await fixture(t, { items: [failedLink()], rawResolve: reply });
    f.d.querySelector('#in-list [data-resolve]').click();
    await settle();
    const receipt = f.d.querySelector('#in-status').textContent;
    assert.match(receipt, /结果未确认/);
    assert.doesNotMatch(receipt, /已重新解析|已重新识别|已存入知识库|这条已存在|未重复导入/);
    assert.equal(f.rows().length, 1, '不能把别人的条目插进列表');
    assert.match(f.list(), /抓取失败：联网服务响应超时/, '原条目要原样留着');
    assert.doesNotMatch(f.list(), /一个红色方块|已抓取正文，共 12 字/, '别人的内容不能出现在这一行');
    assert.equal(f.d.querySelector('#in-list .in-retry-note'), null, '本轮未确认就不能写完成时间');
    assert.doesNotMatch(f.list(), /完成于/);
    const button = f.d.querySelector('#in-list [data-resolve]');
    assert.ok(button && !button.disabled, '重试入口要留着');
  });

test('a list holding only a pending item is still polled', async t => {
  // 回执说「结果会自动刷新」，那么只有 pending 条目时也要真的去刷（Codex 探针转正式用例）。
  const f = await fixture(t, { items: [base({ status: 'pending', outcome: 'pending', state: '已收下，尚未解析', document_id: '', knowledge_url: '' })] });
  const before = f.gets();
  await new Promise(resolve => setTimeout(resolve, 2200));
  assert.ok(f.gets() > before, '仅 pending 的条目也要继续轮询');
});

test('a pending item refreshes to its terminal state and then stops polling', async t => {
  const list = [base({ status: 'pending', outcome: 'pending', state: '已收下，尚未解析', document_id: '', knowledge_url: '' })];
  const f = await fixture(t, { items: list, capturePoll: true });
  assert.match(f.list(), /已收下，尚未解析/);
  f.poll();
  await settle();
  assert.ok(f.gets() > 1, 'pending 条目要触发一次列表刷新');
  list[0] = base({});   // 后端把这条解析完了
  f.poll();
  await settle();
  assert.doesNotMatch(f.list(), /已收下，尚未解析/, '刷新后要看到终态');
  const settled = f.gets();
  await new Promise(resolve => setTimeout(resolve, 2200));
  assert.equal(f.gets(), settled, '到终态后不能再发 GET');
});

test('an item already confirmed failed is not polled forever', async t => {
  // 自相矛盾的回执（status=pending 但已带 error）：按失败说，不再对着终态空转请求。
  const f = await fixture(t, { items: [failedLink({ status: 'pending', outcome: 'pending' })], capturePoll: true });
  const before = f.gets();
  f.poll();
  await settle();
  assert.equal(f.gets(), before, '已确认失败的条目不再轮询');
});

// ── fixes-intake-next-step-a：收下之后的写作入口（方案 A，纯前端）──────────────
// 验收对照 coordination/design-intake-next-step-report.md §4 与 §6 的 A 用例清单。
const storedLink = extra => base({ meta: { fetched: true, fetched_at: '2026-10-09T01:21:00+00:00', total_chars: 12 }, ...extra });
const writeParams = (w, anchor) => new w.URLSearchParams(new w.URL(anchor.href).search);

test('a finished analysis puts one writing entry beside every angle carrying only identifiers', async t => {
  const f = await fixture(t, { items: [storedLink({ analysis: { status: 'done', at: '2026-10-09T02:00:00+00:00',
    summary: '旧码头黄昏的蹲拍方法', angles: [{ title: '写一条蹲拍笔记', how: '从水面倒影切入' }, { title: '第二个角度', how: '' } ] } })] });
  const entries = [...f.d.querySelectorAll('#in-list .in-use-angle')];
  assert.equal(entries.length, 2);
  assert.deepEqual(entries.map(a => writeParams(f.w, a).get('angle')), ['0', '1']);
  for (const anchor of entries) {
    const params = writeParams(f.w, anchor);
    assert.equal(params.get('document'), id('b'));
    assert.equal(params.get('intake'), id('a'));
    assert.equal(params.get('analysis_at'), '2026-10-09T02:00:00+00:00');
    assert.deepEqual([...params.keys()].sort(), ['analysis_at', 'angle', 'document', 'intake']);
  }
  // 有角度时不再另给通用入口，也不给「一键建底稿」这类过头承诺。
  assert.equal(f.d.querySelector('#in-list .in-writing-primary'), null);
  assert.doesNotMatch(f.list(), /一键/);
});

test('the angle link falls back to the analysis fingerprint when there is no finished clock', async t => {
  const f = await fixture(t, { items: [storedLink({ analysis: { status: 'done', at: '', fingerprint: 'ab12cd34ef56', angles: [{ title: '角度' }] } })] });
  assert.equal(writeParams(f.w, f.d.querySelector('.in-use-angle')).get('analysis_at'), 'ab12cd34ef56');
});

test('a stored body without usable angles still offers one plain writing entry', async t => {
  for (const analysis of [{ status: 'queued' }, { status: 'running' }, { status: 'failed', error: '模型没回话' }, { status: 'done', angles: [] }, {}]) {
    const f = await fixture(t, { items: [storedLink({ analysis })] });
    const entry = f.d.querySelector('#in-list .in-writing-primary');
    assert.ok(entry, JSON.stringify(analysis) + ' 时也要能用这篇资料写');
    assert.match(entry.textContent, /用这篇资料写/);
    const params = writeParams(f.w, entry);
    assert.deepEqual([...params.keys()].sort(), ['document', 'intake'], '不带角度时不塞 angle/analysis_at');
    assert.equal(f.d.querySelector('#in-list .in-use-angle'), null);
  }
});

test('a duplicate row states what the existing copy is for and offers the three entries', async t => {
  const f = await fixture(t, { items: [storedLink({ outcome: 'duplicate', meta: { duplicate: true, fetched: true, total_chars: 12 },
    analysis: { status: 'done', at: '2026-10-09T02:00:00+00:00', angles: [{ title: '可写角度' } ] } })] });
  assert.match(f.list(), /这篇已在知识库，未重复导入。收录后可以用来整理写作资料、参考可写角度，或到卡库确认提示词。/);
  const actions = [...f.d.querySelectorAll('.in-writing-duplicate .in-writing-actions a')].map(a => a.textContent.trim());
  assert.deepEqual(actions, ['查看已有资料', '用这篇资料写', '选片段建资料夹']);
  const view = f.d.querySelector('.in-writing-duplicate a[href="' + '/knowledge.html?document=' + id('b') + '"]');
  assert.ok(view, '查看已有资料只带原编号');
  assert.deepEqual([...writeParams(f.w, f.d.querySelector('.in-writing-duplicate .in-writing-primary')).keys()].sort(), ['document', 'intake']);
  assert.equal(f.d.querySelectorAll('#in-list .in-use-angle').length, 1, '重复行仍可按角度进入');
});

test('a failed link the backend labels duplicate keeps the failure first and offers no writing entry', async t => {
  const f = await fixture(t, { items: [base({ error: '需要登录浏览器', outcome: 'duplicate', state: '抓取失败：需要登录浏览器；已存入知识库（仅链接）',
    meta: { duplicate: true, fetched: false } })] });
  assert.match(f.list(), /抓取失败：需要登录浏览器/, '失败口径仍然在最前面');
  assert.match(f.list(), /先按上面的原因重试/);
  assert.doesNotMatch(f.list(), /这篇已在知识库，未重复导入/, '不能用「已收录」盖住错误');
  assert.doesNotMatch(f.list(), /用这篇资料写|用这个角度写|选片段建资料夹/);
  assert.equal(f.d.querySelectorAll('#in-list .in-writing-primary, #in-list .in-use-angle').length, 0);
  assert.ok(f.d.querySelector('.in-writing-failed .in-writing-quiet'), '已有资料只作次按钮');
  assert.ok(f.d.querySelector('[data-resolve]'), '重试入口保留');
});

test('a bookmark with only a link says so and offers the record and the original link only', async t => {
  const f = await fixture(t, { items: [base({ meta: { fetched: false } })] });
  assert.match(f.list(), /这条链接已收藏，原文正文尚未保存。/);
  const actions = [...f.d.querySelectorAll('.in-writing-linkonly .in-writing-actions a')].map(a => a.textContent.trim());
  assert.deepEqual(actions, ['查看收藏记录', '打开原链接 ↗']);
  assert.equal(f.d.querySelector('.in-writing-linkonly .in-writing-actions a').getAttribute('href'), '/knowledge.html?document=' + id('b'));
  assert.equal(f.d.querySelector('#in-list .in-writing-primary, #in-list .in-use-angle'), null, '没正文不给写作入口');
});

test('an item whose body was never stored gets no writing entry at all', async t => {
  const f = await fixture(t, { items: [base({ document_id: '', knowledge_url: '', status: 'pending', outcome: 'pending' })] });
  assert.equal(f.d.querySelector('#in-list .in-writing'), null);
  assert.equal(f.d.querySelector('#in-list a[href^="/knowledge.html"]'), null);
});

test('writing entries escape untrusted text and carry no body, summary, angle wording or reason', async t => {
  const f = await fixture(t, { items: [storedLink({ reason: '<img src=x onerror=alert(1)>收藏理由',
    analysis: { status: 'done', at: '2026-10-09T02:00:00+00:00', summary: '<script>bad()</script>摘要正文',
      angles: [{ title: '<script>角度标题</script>', how: '<img src=x>角度做法' }] } })] });
  assert.equal(f.d.querySelectorAll('#in-list img, #in-list script').length, 0);
  assert.match(f.list(), /<script>角度标题<\/script>/, '角度文字仍然按文本显示');
  for (const anchor of f.d.querySelectorAll('#in-list a[href^="/knowledge.html"]')) {
    const href = anchor.getAttribute('href');
    for (const leak of ['角度标题', '角度做法', '摘要正文', '收藏理由', 'onerror']) {
      assert.ok(!decodeURIComponent(href).includes(leak), href + ' 不该带上 ' + leak);
    }
  }
});

test('the new writing block keeps the knowledge, retry, reanalyse, cards and delete entries working', async t => {
  const f = await fixture(t, { items: [storedLink({ analysis: { status: 'done', at: '2026-10-09T02:00:00+00:00', cards: 2, angles: [{ title: '角度' }] } })] });
  assert.ok(f.d.querySelector('#in-list footer a[href="/knowledge.html?document=' + id('b') + '"]'), '在知识库查看仍在');
  assert.ok(f.d.querySelector('[data-analyze]'), '重新分析仍在');
  assert.ok(f.d.querySelector('[data-delete]'), '删除仍在');
  assert.match(f.list(), /去卡库确认/);
  assert.equal(f.d.querySelectorAll('#in-list footer a[href^="/knowledge.html"]').length, 1, '写作入口不挤掉原有查看入口');
});
