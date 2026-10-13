// 创意回收页拖入文件（R02 + 返修 b M1–M3）的真实浏览器验收。
// 真实 Chrome、真实前端/原生控件/可信拖放事件/CSS/fetch；后端是本机合成的 /api/intake/*。
// 覆盖：复核已通过的 17 项正向路径，以及 M1（原生框拒收后两处要一致、不能上传旧文件）、
// M2（GIF/SVG/OGG 当场拒收、空 MIME 按后缀退路）、M3（文字拖放不被拦、文件拖放仍要拦）。
//
// 单独运行（cwd 必须是仓库根；不联网、随机端口、绝不用 8766）：
//   node tests/e2e/intake_drop.cjs [证据目录]
//   BASE_MODE=1 node tests/e2e/intake_drop.cjs [证据目录]   # 只记录，不判红（跑基座/上一版用）
// Playwright 优先读 E2E_PLAYWRIGHT_PATH，其次本目录安装包。
const fs = require('node:fs'), path = require('node:path'), http = require('node:http'), assert = require('node:assert/strict');
const BASE_MODE = process.env.BASE_MODE === '1';
const ROOT = process.env.CARDS_ROOT || process.cwd();
const OUT = path.resolve(process.argv[2] || path.join(ROOT, 'evidence-intake-drop-b'));
fs.mkdirSync(OUT, { recursive: true });

const item = { id: 'a'.repeat(32), kind: 'text', received_at: '2026-10-13T01:20:00+00:00', url: '', title: '合成条目',
  reason: '', status: 'resolved', error: '', document_id: 'b'.repeat(32), revision: 2, file_path: '', file: {},
  media_kind: '', text_chars: 2, content_scope: '', meta: {}, outcome: 'stored', state: '', document_shares: 1,
  document_owned: true, knowledge_url: '/knowledge.html?document=' + 'b'.repeat(32) };
const calls = [], passes = [], findings = [];
let hold = false, held = null, stage = null, browser = null;
const json = (res, obj, code = 200) => { res.writeHead(code, { 'Content-Type': 'application/json' }); res.end(JSON.stringify(obj)); };
const server = http.createServer((req, res) => {
  const url = new URL(req.url, 'http://127.0.0.1'), chunks = [];
  req.on('data', b => chunks.push(b));
  return req.on('end', () => {
    const bytes = Buffer.concat(chunks);
    calls.push({ url: url.pathname, method: req.method, bytes: bytes.length, name: url.searchParams.get('name') });
    if (!url.pathname.startsWith('/api/')) {
      const file = path.join(ROOT, 'public', url.pathname === '/' ? 'intake.html' : url.pathname);
      if (!file.startsWith(path.join(ROOT, 'public')) || !fs.existsSync(file)) return res.writeHead(404).end();
      res.setHeader('Content-Type', ({ '.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8',
        '.css': 'text/css; charset=utf-8', '.svg': 'image/svg+xml' })[path.extname(file)] || 'application/octet-stream');
      return res.end(fs.readFileSync(file));
    }
    if (url.pathname === '/api/intake') return json(res, { schema: 'intake/v1', items: [item], note: '' });
    if (url.pathname === '/api/intake/analysis-status') return json(res, { supported: true });
    if (url.pathname === '/api/intake/stage') {
      stage = { name: url.searchParams.get('name'), kind: url.searchParams.get('kind'), bytes: bytes.length };
      const answer = () => json(res, { schema: 'intake-stage/v1', token: 'e'.repeat(32), kind: stage.kind, file: { ...stage, format: 'PNG' } });
      if (hold) { held = answer; return; }
      return answer();
    }
    if (url.pathname === '/api/intake/drop') {
      const body = JSON.parse(bytes.toString() || '{}');
      calls.at(-1).body = body; calls.at(-1).staged_file = stage && stage.name;
      return json(res, { ...item, kind: body.kind || 'text', reason: body.reason, text_chars: (body.text || '').length,
        status: 'pending', outcome: 'pending', document_id: '', knowledge_url: '' });
    }
    if (/^\/api\/intake\/[0-9a-f]{32}\/resolve$/.test(url.pathname))
      return json(res, { ...item, kind: 'text', status: 'resolved', outcome: 'stored', document_id: 'b'.repeat(32) });
    return json(res, item);
  });
});
function playwright() {
  for (const mod of [process.env.E2E_PLAYWRIGHT_PATH, 'playwright', path.join(ROOT, 'tests/e2e/node_modules/playwright')]
    .filter(Boolean)) { try { return require(mod); } catch (e) { if (e.code !== 'MODULE_NOT_FOUND') throw e; } }
  throw new Error('缺少 Playwright：请设置 E2E_PLAYWRIGHT_PATH');
}
const state = page => page.evaluate(() => ({
  native: [...document.querySelector('#in-file').files].map(f => f.name),
  picked: document.querySelector('#in-picked').textContent,
  status: document.querySelector('#in-status').textContent,
  highlight: document.querySelector('#in-form').classList.contains('in-dragging'),
  hint: document.querySelector('#in-drop-hint') ? document.querySelector('#in-drop-hint').hidden : null,
  quiet: document.querySelector('#in-file').classList.contains('in-file-quiet'),
  input: document.querySelector('#in-input').value,
}));
// 页面内构造真实 DragEvent + DataTransfer/File：投放源是合成的，页面处理链与渲染是真实 Chrome。
const drag = (page, name, type, event = 'drop', size = null, target = 'body') => page.evaluate(({ name, type, event, size, target }) => {
  const dt = new DataTransfer();
  const file = new File(['SYNTHETIC-BYTES'], name, { type });
  if (size !== null) Object.defineProperty(file, 'size', { value: size });
  dt.items.add(file);
  const ev = new DragEvent(event, { bubbles: true, cancelable: true, dataTransfer: dt });
  document.querySelector(target).dispatchEvent(ev);
  return { prevented: ev.defaultPrevented, dropEffect: dt.dropEffect, trusted: ev.isTrusted };
}, { name, type, event, size, target });
async function clear(page) { const b = page.locator('#in-picked [data-discard]'); if (await b.count()) await b.click(); }
const note = (name, value) => (BASE_MODE ? findings.push({ case: name, value }) : passes.push(name + '：' + value));

(async () => {
  await new Promise(r => server.listen(0, '127.0.0.1', r));
  const base = 'http://127.0.0.1:' + server.address().port;
  assert.notEqual(server.address().port, 8766, '验收端口不得是 8766');
  const { chromium } = playwright();
  browser = await chromium.launch({ channel: process.env.E2E_BROWSER || 'chrome', headless: !process.env.E2E_HEADED });
  const page = await browser.newPage({ viewport: { width: 1400, height: 1050 } });
  const errors = [], outside = [];
  page.on('pageerror', e => errors.push(String(e)));
  page.on('request', r => { if (!r.url().startsWith(base) && !r.url().startsWith('data:')) outside.push(r.url()); });
  await page.goto(base + '/intake.html');
  await page.waitForFunction(() => document.querySelector('#in-count').textContent.includes('1'));
  await page.evaluate(() => {
    window.auditEvents = [];
    for (const type of ['dragenter', 'dragover', 'dragleave', 'drop', 'paste', 'keydown'])
      document.addEventListener(type, e => window.auditEvents.push({ type, trusted: e.isTrusted,
        types: e.dataTransfer ? [...e.dataTransfer.types] : [], prevented: e.defaultPrevented, key: e.key }));
  });

  // ── 17 项正向路径（复核已通过，不得回退）──
  await drag(page, 'roll.mp4', 'video/mp4', 'dragenter', null, '#in-form');
  await drag(page, 'roll.mp4', 'video/mp4', 'dragover');
  let s = await state(page);
  assert.equal(s.highlight, true); assert.equal(s.hint, false);
  await page.screenshot({ path: path.join(OUT, 'highlight.png') });
  await page.keyboard.press('Escape');
  assert.equal((await state(page)).highlight, false, 'Esc 要复位');
  // 真实浏览器的顺序：进入页面 → 进入子元素（dragenter）→ 离开外层（dragleave）。计数器要顶住这一对。
  await drag(page, 'roll.mp4', 'video/mp4', 'dragenter');
  await drag(page, 'roll.mp4', 'video/mp4', 'dragenter', null, '#in-input');
  await drag(page, 'roll.mp4', 'video/mp4', 'dragleave', null, '#in-form');
  assert.equal((await state(page)).highlight, true, '子元素进出不闪烁');
  await drag(page, 'roll.mp4', 'video/mp4', 'dragleave');
  assert.equal((await state(page)).highlight, false, '真正离开页面复位');
  await drag(page, 'roll.mp4', 'video/mp4');
  s = await state(page); assert.deepEqual(s.native, ['roll.mp4']); assert.match(s.picked, /roll\.mp4/);
  assert.equal(s.highlight, false, 'drop 后复位');
  await clear(page); s = await state(page);
  assert.deepEqual(s.native, []); assert.equal(s.picked, ''); note('1 mp4 拖入两侧一致 + 取消清空', 'ok');
  await drag(page, 'shot.png', 'image/png');
  s = await state(page); assert.deepEqual(s.native, ['shot.png']); assert.match(s.picked, /shot\.png/); await clear(page);
  note('2 png 拖入两侧一致', 'ok');
  await page.evaluate(() => { const dt = new DataTransfer(); dt.items.add(new File(['PNG'], 'paste.png', { type: 'image/png' }));
    document.querySelector('#in-input').dispatchEvent(new ClipboardEvent('paste', { clipboardData: dt, bubbles: true, cancelable: true })); });
  s = await state(page); assert.deepEqual(s.native, ['paste.png']); assert.match(s.picked, /paste\.png/); await clear(page);
  note('3 粘贴文件两侧一致', 'ok');
  for (const [name, type] of [['book.pdf', 'application/pdf'], ['archive.zip', 'application/zip']]) {
    await drag(page, name, type); s = await state(page);
    assert.equal(s.picked, ''); assert.equal(s.native.length, 0); assert.match(s.status, /不是图片或视频文件/);
  }
  note('4 PDF/ZIP 拖入当场拒收且不进两侧', 'ok');
  for (const [name, type, size, pattern] of [['huge.png', 'image/png', 10 * 1024 * 1024 + 1, /超过图片上限 10 MB/],
    ['huge.mp4', 'video/mp4', 1024 ** 3 + 1, /超过视频上限 1 GB/]]) {
    await drag(page, name, type, 'drop', size); s = await state(page);
    assert.match(s.status, pattern); assert.deepEqual(s.native, [name]); await clear(page);
  }
  note('5 超限当场提示且仍可选中', 'ok');
  // 原生框写回失败：只藏起原生文字，label 仍能打开选择器换文件，取消后恢复。
  await page.evaluate(() => { const input = document.querySelector('#in-file');
    window.nativeFilesDescriptor = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'files');
    Object.defineProperty(input, 'files', { configurable: true,
      get() { return window.nativeFilesDescriptor.get.call(this); },
      set() { throw new Error('SYNTHETIC_FILES_ASSIGNMENT_DENIED'); } }); });
  await drag(page, 'fallback.mp4', 'video/mp4'); s = await state(page);
  assert.equal(s.quiet, true); assert.match(s.picked, /fallback\.mp4/);
  const quietBox = await page.locator('#in-file').boundingBox(); assert.ok(quietBox.width <= 1 && quietBox.height <= 1);
  const chooser = page.waitForEvent('filechooser');
  await page.locator('.in-file-button').click();
  const choice = await chooser;
  await choice.setFiles({ name: 'fallback-new.png', mimeType: 'image/png', buffer: Buffer.from('PNG') });
  s = await state(page); assert.match(s.picked, /fallback-new\.png/); assert.equal(s.quiet, true);
  await clear(page); s = await state(page);
  assert.equal(s.picked, ''); assert.equal(s.quiet, false); assert.deepEqual(s.native, []);
  await page.evaluate(() => delete document.querySelector('#in-file').files);
  note('6 写回失败退路：隐藏原生文字、label 仍能换文件、取消恢复', 'ok');
  // 上传中不能换文件：真实的挂起 fetch，拖新文件被拒，释放后正常暂存→提交→解析。
  hold = true;
  await drag(page, 'upload.mp4', 'video/mp4');
  await page.fill('#in-input', '上传中合成');
  await page.click('#in-submit');
  await page.waitForFunction(() => document.querySelector('#in-file').disabled === true);
  const effect = await drag(page, 'replacement.png', 'image/png', 'dragover');
  assert.equal(effect.dropEffect, 'none');
  await drag(page, 'replacement.png', 'image/png'); s = await state(page);
  assert.match(s.status, /上传中不能换文件/); assert.deepEqual(s.native, ['upload.mp4']);
  held(); hold = false; held = null;
  await page.waitForFunction(() => document.querySelector('#in-picked').textContent === '' && document.querySelector('#in-status').textContent.includes('已存入知识库'));
  assert.equal(calls.filter(c => c.url === '/api/intake/drop').at(-1).staged_file, 'upload.mp4');
  note('7 上传中不可换文件 + 正常收下→暂存→提交→解析', 'ok');

  // ── M1：原生框选了不支持的文件，两处必须一致，收下不得上传它 ──
  await drag(page, 'old.mp4', 'video/mp4');
  await page.locator('#in-file').setInputFiles({ name: 'archive.zip', mimeType: 'application/zip', buffer: Buffer.from('ZIP') });
  s = await state(page);
  findings.push({ case: 'm1-旧选择+原生选 ZIP', state: s });
  await page.screenshot({ path: path.join(OUT, 'm1-invalid-retains-old.png') });
  if (!BASE_MODE) {
    assert.deepEqual(s.native, ['old.mp4'], '原生框必须回到实际会提交的那个文件');
    assert.match(s.picked, /已选 old\.mp4/);
    assert.match(s.status, /不是图片或视频文件/);
    assert.match(s.status, /仍是原来选中的 old\.mp4/);
  }
  await page.fill('#in-input', 'M1 合成提交');
  await page.click('#in-submit');
  await page.waitForFunction(() => document.querySelector('#in-picked').textContent === '');
  const m1Stage = stage && stage.name, m1Drop = calls.filter(c => c.url === '/api/intake/drop').at(-1);
  findings.push({ case: 'm1-提交', stage: m1Stage, staged_file: m1Drop.staged_file, has_token: Boolean(m1Drop.body.file_token) });
  if (!BASE_MODE) assert.equal(m1Stage, 'old.mp4', '拒收的文件不许被上传');
  await clear(page);
  await page.locator('#in-file').setInputFiles({ name: 'book.pdf', mimeType: 'application/pdf', buffer: Buffer.from('PDF') });
  s = await state(page);
  findings.push({ case: 'm1-无选择+原生选 PDF', state: s });
  await page.screenshot({ path: path.join(OUT, 'm1-invalid-empty.png') });
  if (!BASE_MODE) { assert.deepEqual(s.native, [], '没有有效选择时原生框也要清空'); assert.equal(s.picked, ''); }
  note('8 M1 原生框拒收后两侧一致、提交不串文件', 'ok');

  // ── M2：GIF / SVG / OGG 当场拒收；空 MIME 的 .mp4 按后缀退路接受 ──
  for (const [name, type, why] of [['anim.gif', 'image/gif', /图片格式不支持/], ['drawing.svg', 'image/svg+xml', /图片格式不支持/],
    ['clip.ogg', 'video/ogg', /视频格式不支持/]]) {
    await drag(page, name, type); s = await state(page);
    findings.push({ case: 'm2-' + name, state: s });
    if (!BASE_MODE) {
      assert.equal(s.picked, '', name + ' 不能变成「已选」');
      assert.equal(s.native.length, 0, name + ' 不能写进原生框');
      assert.match(s.status, why);
      assert.match(s.status, /PNG \/ JPEG \/ WebP/);
      assert.match(s.status, /MP4 \/ MOV \/ M4V \/ WebM \/ MKV \/ AVI/);
    }
    await clear(page);
  }
  await drag(page, 'unknown.mp4', ''); s = await state(page);
  findings.push({ case: 'm2-空 MIME mp4', state: s });
  if (!BASE_MODE) { assert.match(s.picked, /已选 unknown\.mp4/); assert.deepEqual(s.native, ['unknown.mp4']); }
  await page.click('#in-submit');
  await page.waitForFunction(() => document.querySelector('#in-picked').textContent === '');
  findings.push({ case: 'm2-空 MIME 提交', stage: stage && { name: stage.name, kind: stage.kind } });
  if (!BASE_MODE) assert.equal(stage.kind, 'video', '空 MIME 的 .mp4 也要按视频暂存，不能在后端才炸');
  await clear(page);
  note('9 M2 GIF/SVG/OGG 拒收 + 空 MIME 按后缀接受', 'ok');

  // ── M3：可信文字拖入 textarea；普通文字粘贴不受影响；文件拖放仍要拦 ──
  await page.fill('#in-input', '');
  await page.evaluate(() => { const el = document.createElement('div'); el.id = 'audit-text-source';
    el.textContent = '合成拖入文字'; el.draggable = true;
    el.style.cssText = 'position:fixed;left:300px;top:100px;z-index:99999;padding:16px;background:#ffd';
    el.addEventListener('dragstart', e => e.dataTransfer.setData('text/plain', '合成拖入文字'));
    document.body.append(el); });
  await page.locator('#audit-text-source').dragTo(page.locator('#in-input'));
  s = await state(page);
  const textDrop = (await page.evaluate(() => window.auditEvents)).filter(e => e.type === 'drop').at(-1);
  findings.push({ case: 'm3-可信文字拖入', input: s.input, drop: textDrop });
  await page.screenshot({ path: path.join(OUT, 'm3-text-drop.png') });
  if (!BASE_MODE) {
    assert.ok(textDrop.trusted, '文字拖放必须是 Chrome 的可信事件');
    assert.equal(textDrop.prevented, false, '文字拖放不能被 preventDefault');
    assert.match(s.input, /合成拖入文字/, '文字必须真的进输入框');
    assert.equal(s.highlight, false, '文字不高亮');
  }
  const fileDrop = (await page.evaluate(() => window.auditEvents)).filter(e => e.type === 'drop' && e.types.includes('Files')).at(-1);
  findings.push({ case: 'm3-文件 drop 是否被拦', prevented: fileDrop && fileDrop.prevented });
  if (!BASE_MODE) assert.equal(fileDrop.prevented, true, '文件拖放仍要 preventDefault，否则浏览器会直接打开它');
  await page.evaluate(() => { const el = document.querySelector('#in-input'); el.value = '已有文字';
    const dt = new DataTransfer(); dt.setData('text/plain', '粘进来的文字');
    el.dispatchEvent(new ClipboardEvent('paste', { clipboardData: dt, bubbles: true, cancelable: true })); });
  s = await state(page);
  findings.push({ case: 'm3-普通文字粘贴', input: s.input });
  if (!BASE_MODE) assert.equal(s.input, '已有文字', '页面不插手普通文字粘贴');
  note('10 M3 文字拖入/粘贴不受拦，文件拖放仍被拦', 'ok');

  // ── C：空 MIME 的 WebP 必须可选（后端收，提示文案也写着支持 WebP）──
  const stagesBeforeC = calls.filter(c => c.url === '/api/intake/stage').length;
  await drag(page, 'blank.webp', ''); s = await state(page);
  findings.push({ case: 'c-空 MIME webp 拖入', state: s });
  await page.screenshot({ path: path.join(OUT, 'c-empty-webp-drop.png') });
  if (!BASE_MODE) {
    assert.deepEqual(s.native, ['blank.webp'], '原生框必须显示 blank.webp');
    assert.match(s.picked, /已选 blank\.webp/);
    assert.equal(s.status, '', '合法文件不该留下警告');
  }
  await clear(page);
  await page.locator('#in-file').setInputFiles({ name: 'blank.webp', mimeType: '', buffer: Buffer.from('RIFF....WEBPVP8 ') });
  s = await state(page);
  findings.push({ case: 'c-空 MIME webp 原生选择', state: s });
  if (!BASE_MODE) { assert.deepEqual(s.native, ['blank.webp']); assert.match(s.picked, /已选 blank\.webp/); }
  await page.fill('#in-input', '空 MIME 的 webp');
  await page.click('#in-submit');
  await page.waitForFunction(() => document.querySelector('#in-picked').textContent === '');
  findings.push({ case: 'c-空 MIME webp 提交', stage: stage && { name: stage.name, kind: stage.kind } });
  if (!BASE_MODE) {
    assert.equal(stage.name, 'blank.webp', '收下时暂存的就是界面上那个文件');
    assert.equal(stage.kind, 'image', '空 MIME 的 WebP 必须按图片暂存，不能在前端就拒掉');
  }
  note('11 C 空 MIME 的 WebP 两侧一致、可收下并以 kind=image 暂存', 'ok');

  // ── C：前后端支持集逐项一致（后缀名单直接从后端常量核对）──
  const backendSuffixes = () => page.evaluate(() => {
    // 后端名单由 tests 的常驻用例核对；浏览器这里用同一份字面名单，来源见 intake_store.py:36-38。
    return { image: ['.png', '.jpg', '.jpeg', '.webp'], video: ['.mp4', '.mov', '.m4v', '.webm', '.mkv', '.avi'] };
  });
  const backend = await backendSuffixes();
  const stagedKinds = {};
  for (const [suffix, kind] of [...backend.image.map(x => [x, 'image']), ...backend.video.map(x => [x, 'video'])]) {
    const name = 'probe' + suffix;
    await drag(page, name, ''); s = await state(page);
    if (!BASE_MODE) {
      assert.deepEqual(s.native, [name], suffix + ' 空 MIME 必须可选');
      assert.match(s.picked, new RegExp('已选 ' + name.replace('.', '\\.')), suffix + ' 要进「已选」');
      assert.equal(s.status, '', suffix + ' 不该留下警告');
    }
    await page.click('#in-submit');
    await page.waitForFunction(() => document.querySelector('#in-picked').textContent === '');
    stagedKinds[suffix] = stage && stage.kind;
    if (!BASE_MODE) assert.equal(stage.kind, kind, suffix + ' 必须按 ' + kind + ' 暂存');
    findings.push({ case: 'c-后缀 ' + suffix, native: s.native, picked: s.picked.slice(0, 24), staged: stagedKinds[suffix] });
    await clear(page);
  }
  for (const suffix of ['.gif', '.svg', '.bmp', '.tif', '.ogv', '.wmv', '.flv', '.heic', '.bin']) {
    const name = 'reject' + suffix;
    await drag(page, name, ''); s = await state(page);
    findings.push({ case: 'c-拒收 ' + suffix, native: s.native, picked: s.picked.slice(0, 24), status: s.status.slice(0, 40) });
    if (!BASE_MODE) {
      assert.equal(s.picked, '', suffix + ' 不能变成「已选」');
      assert.deepEqual(s.native, [], suffix + ' 不能写进原生框');
      assert.match(s.status, /不是图片或视频文件|格式不支持/);
    }
    await clear(page);
  }
  const stagesInC = calls.filter(c => c.url === '/api/intake/stage').length - stagesBeforeC;
  findings.push({ case: 'c-本轮暂存次数', stages: stagesInC, expect: 11 });
  if (!BASE_MODE) assert.equal(stagesInC, 11, '本轮只有被接受的 11 个文件（1 个 webp + 10 个后缀）发过暂存');
  note('12 C 前后端支持集逐项一致（10 个后缀按对 kind 暂存 + 9 个后缀拒收）', 'ok');

  // ── 补充：CDP 外部文件拖放（OS 来源），进出窗口与 Esc 的高亮复位 ──
  const onDisk = path.join(OUT, 'os-drag.mp4');
  fs.writeFileSync(onDisk, 'SYNTHETIC');
  const cdp = await page.context().newCDPSession(page);
  const inputBox = await page.locator('#in-input').boundingBox();
  const data = { items: [], files: [onDisk], dragOperationsMask: 1 };
  const inside = { x: inputBox.x + 20, y: inputBox.y + 20 }, offWindow = { x: -20, y: -20 };
  await cdp.send('Input.dispatchDragEvent', { type: 'dragEnter', ...inside, data });
  await cdp.send('Input.dispatchDragEvent', { type: 'dragOver', ...inside, data });
  const cdpEnter = await state(page);
  await cdp.send('Input.dispatchDragEvent', { type: 'dragOver', ...offWindow, data });
  const cdpExit = await state(page);
  await cdp.send('Input.dispatchDragEvent', { type: 'dragEnter', ...inside, data });
  await page.keyboard.press('Escape');
  const cdpEsc = await state(page);
  await cdp.send('Input.dispatchDragEvent', { type: 'dragCancel', ...inside, data });
  findings.push({ case: 'cdp-OS 文件拖放', enter: cdpEnter.highlight, exit: cdpExit.highlight, escape: cdpEsc.highlight });
  await page.screenshot({ path: path.join(OUT, 'cdp-file-drag.png') });
  if (!BASE_MODE) {
    assert.equal(cdpEnter.highlight, true, 'OS 来源的文件拖入也要高亮');
    assert.equal(cdpExit.highlight, false, '移出窗口要复位');
    assert.equal(cdpEsc.highlight, false, 'Esc 要复位');
  }

  await browser.close(); browser = null;
  await new Promise(r => server.close(r));
  fs.writeFileSync(path.join(OUT, 'result.json'), JSON.stringify({ root: ROOT, base, base_mode: BASE_MODE, passes, findings,
    calls, pageerrors: errors, external_requests: outside, scope: '真实 Chrome、真实前端/原生控件/可信拖放事件/CSS/fetch；后端为本机合成的 /api/intake/*；文件投放源为页面内构造，文字用可信拖放，另有 CDP 的 OS 来源文件拖放' }, null, 2));
  console.log((BASE_MODE ? 'BASE-RECORDED ' : 'PASS ') + '通过 ' + passes.length + ' 项，记录 ' + findings.length + ' 项');
  for (const line of passes) console.log('  ✓ ' + line);
  console.log('  pageerror：' + (errors.length ? errors.join(' | ') : '无') + '；非本机请求：' + outside.length);
  assert.deepEqual(errors, [], '页面不能有脚本错误');
  assert.deepEqual(outside, [], '不能有非本机请求');
})().catch(e => { console.error(e && e.message || e); if (browser) browser.close(); server.close(); process.exit(1); });
