// 创意回收页：拖入文件的提示（原生文件框与「已选」一致、拖拽高亮、不支持类型给明确提示）。
// 全程本机合成：jsdom + 假 fetch，不碰后端、不上传。
const test = require('node:test'), assert = require('node:assert/strict'), fs = require('node:fs');
const { JSDOM } = require('jsdom');
const tick = () => new Promise(resolve => setImmediate(resolve));
const settle = async () => { for (let i = 0; i < 8; i++) await tick(); };
const id = n => n.repeat(32);

async function fixture(t, options = {}) {
  const dom = new JSDOM(fs.readFileSync('public/intake.html', 'utf8'),
    { url: 'http://127.0.0.1:8766/intake.html', runScripts: 'outside-only', pretendToBeVisual: true });
  t.after(() => dom.window.close());
  const w = dom.window, d = w.document, calls = [];
  let staged = null;
  const item = { id: id('a'), kind: 'link', received_at: '2026-10-09T01:20:00+00:00', url: 'https://example.com/post',
    title: '', reason: '', status: 'resolved', error: '', document_id: id('b'), revision: 2, file_path: '', file: {},
    media_kind: '', text_chars: 0, content_scope: '', meta: {}, outcome: 'stored', state: '', document_shares: 1,
    document_owned: true, knowledge_url: '/knowledge.html?document=' + id('b') };
  w.confirm = () => true;
  w.fetch = async (url, opts = {}) => {
    const target = String(url);
    // 暂存上传的 body 是文件本身（不是 JSON 串），只有字符串 body 才解析。
    calls.push({ url: target, body: typeof opts.body === 'string' ? JSON.parse(opts.body) : undefined,
      file: typeof opts.body === 'string' ? null : opts.body });
    if (target === '/api/intake') return { ok: true, json: async () => ({ schema: 'intake/v1', items: [{ ...item }], note: '' }) };
    if (target.startsWith('/api/intake/stage?')) {
      const params = new w.URLSearchParams(target.split('?')[1]);
      staged = { kind: params.get('kind'), name: params.get('name') };
      return { ok: true, json: async () => ({ schema: 'intake-stage/v1', token: id('e'), kind: params.get('kind'),
        file: { name: params.get('name'), bytes: staged.size, format: 'PNG', width: 24, height: 16, kind: params.get('kind') } }) };
    }
    if (target === '/api/intake/drop') {
      return { ok: true, json: async () => ({ ...item, id: id('c'), kind: 'text', text_chars: 3, status: 'pending', outcome: 'pending', state: '已收下', document_id: '', knowledge_url: '' }) };
    }
    return { ok: false, status: 404, json: async () => ({ error: '合成验收只用列表与收下' }) };
  };
  // jsdom 没有 DataTransfer，也不允许把普通数组写进 input.files（要真正的 FileList）。
  // 这里给一个最小 DataTransfer，并把 #in-file.files 换成可写数组：验证的是「产品代码
  // 确实把同一个文件写回了原生框」，而不是 jsdom 的 FileList 实现细节。
  w.DataTransfer = class {
    constructor() { this.items = { add: file => { this.file = file; } }; }
  };
  Object.defineProperty(w.DataTransfer.prototype, 'files', {
    get() { return this.file ? [this.file] : []; },
  });
  if (options.noDataTransfer) delete w.DataTransfer;
  else {
    const fileInput = d.querySelector('#in-file');
    let files = [];
    Object.defineProperty(fileInput, 'files', { configurable: true, get: () => files, set: value => { files = value || []; } });
  }
  w.eval(fs.readFileSync('public/intake.js', 'utf8'));
  await settle();
  return { w, d, calls, staged: () => staged };
}

const makeFile = (name, type, size) => ({ name, type, size });
// 真实浏览器里 dragenter/dragover/dragleave/drop 都带 dataTransfer；jsdom 自己造。
const fire = (w, target, name, files = [], types = ['Files']) => {
  const event = new w.Event(name, { bubbles: true, cancelable: true });
  event.dataTransfer = { files, types, items: files.map(f => ({ kind: 'file', type: f.type, getAsFile: () => f })) };
  target.dispatchEvent(event);
  return event;
};
const dropFile = (w, files) => {
  fire(w, w.document.body, 'dragenter', files);
  fire(w, w.document.body, 'dragover', files);
  fire(w, w.document.body, 'drop', files);
};
const input = d => d.querySelector('#in-file');
const picked = d => d.querySelector('#in-picked').textContent;
const statusText = d => d.querySelector('#in-status').textContent;

test('拖入视频：原生文件框与「已选」说同一件事，点「取消选择」两处同时清空', async t => {
  const f = await fixture(t);
  const clip = makeFile('roll.mp4', 'video/mp4', 2_500_000);
  dropFile(f.w, [clip]);
  await settle();
  assert.match(picked(f.d), /已选 roll\.mp4/, '右侧要显示已选');
  assert.match(picked(f.d), /2\.4 MB|2\.5 MB/, '右侧要显示大小');
  assert.equal(input(f.d).files[0].name, 'roll.mp4', '原生文件框必须同步显示同一个文件');
  assert.equal(input(f.d).files.length, 1);
  f.d.querySelector('[data-discard]').click();
  await settle();
  assert.equal(picked(f.d), '', '取消后右侧清空');
  assert.equal(input(f.d).files.length, 0, '取消后原生文件框也要清空');
});

test('拖拽时高亮表单并提示松手，拖文字不高亮，离开页面与 Esc 撤销高亮', async t => {
  const f = await fixture(t);
  const form = f.d.querySelector('#in-form'), hint = f.d.querySelector('#in-drop-hint');
  assert.ok(hint, '页面要有一个拖拽提示位');
  assert.equal(hint.hidden, true, '平时不显示');
  const png = [makeFile('a.png', 'image/png', 10)];
  fire(f.w, f.d.body, 'dragenter', png);
  fire(f.w, f.d.body, 'dragover', png);
  assert.equal(form.classList.contains('in-dragging'), true, '带文件的拖拽要高亮');
  assert.equal(hint.hidden, false, '要显示松手提示');
  assert.match(hint.textContent, /松手即可添加图片 \/ 视频/);
  // 子元素冒泡的 dragleave 不该撤销高亮：计数器要顶住
  // 真实顺序：移进子元素先 dragenter（计数 +1），离开外层再 dragleave（计数 −1）。
  fire(f.w, f.d.querySelector('#in-input'), 'dragenter', [makeFile('a.png', 'image/png', 10)]);
  fire(f.w, f.d.body, 'dragleave', [makeFile('a.png', 'image/png', 10)]);
  assert.equal(form.classList.contains('in-dragging'), true, '在子元素之间移动不该撤销高亮');
  fire(f.w, f.d.body, 'dragleave', png);
  assert.equal(form.classList.contains('in-dragging'), false, '真正离开页面要撤销高亮');
  assert.equal(hint.hidden, true);
  // 拖文字不高亮，文字仍走输入框
  fire(f.w, f.d.body, 'dragenter', [], ['text/plain']);
  assert.equal(form.classList.contains('in-dragging'), false, '拖文字不该高亮');
  // Esc 撤销
  fire(f.w, f.d.body, 'dragenter', [makeFile('b.png', 'image/png', 10)]);
  assert.equal(form.classList.contains('in-dragging'), true);
  const esc = new f.w.KeyboardEvent('keydown', { key: 'Escape', bubbles: true });
  f.d.dispatchEvent(esc);
  assert.equal(form.classList.contains('in-dragging'), false, 'Esc 要撤销高亮');
  assert.equal(hint.hidden, true);
});

test('拖入不支持的类型与超限文件：给明确中文提示，不静默忽略', async t => {
  const f = await fixture(t);
  dropFile(f.w, [makeFile('book.pdf', 'application/pdf', 1024)]);
  await settle();
  assert.match(statusText(f.d), /不是图片或视频文件/, 'PDF 要说清楚为什么不收');
  assert.match(statusText(f.d), /book\.pdf/);
  assert.equal(picked(f.d), '', '不合格的文件不能变成「已选」');
  assert.equal(input(f.d).files.length, 0);
  const big = makeFile('huge.mp4', 'video/mp4', 1024 ** 3 + 1024);
  dropFile(f.w, [big]);
  await settle();
  assert.match(statusText(f.d), /超过视频上限 1 GB/, '超限视频要当场说清上限');
  assert.match(picked(f.d), /已选 huge\.mp4/, '超限不替用户做主：仍可选中，收下时由后端给最终回执');
  assert.equal(input(f.d).files[0].name, 'huge.mp4', '超限文件也写回原生框，界面不说两遍');
  const hugeImage = makeFile('huge.png', 'image/png', 10 * 1024 * 1024 + 1);
  dropFile(f.w, [hugeImage]);
  await settle();
  assert.match(statusText(f.d), /超过图片上限 10 MB/);
  // 合格的边界值仍然收
  dropFile(f.w, [makeFile('edge.mp4', 'video/mp4', 1024 ** 3)]);
  await settle();
  assert.match(picked(f.d), /已选 edge\.mp4/, '刚好到上限要能选');
  assert.equal(statusText(f.d), '', '正常文件不该留下警告');
});

test('浏览器不支持写回原生框时：只藏起原生文字，选择入口与「已选」照常', async t => {
  const f = await fixture(t, { noDataTransfer: true });
  dropFile(f.w, [makeFile('clip.mp4', 'video/mp4', 2048)]);
  await settle();
  assert.equal(input(f.d).classList.contains('in-file-quiet'), true, '写回失败要藏起原生控件文字');
  assert.match(picked(f.d), /已选 clip\.mp4/, '「已选 …」仍然保留');
  f.d.querySelector('[data-discard]').click();
  await settle();
  assert.equal(input(f.d).classList.contains('in-file-quiet'), false, '取消后恢复原生控件');
  assert.equal(input(f.d).files.length, 0);
});

test('粘贴图片走同一条入口：原生框与「已选」一致', async t => {
  const f = await fixture(t);
  const shot = makeFile('screenshot.png', 'image/png', 4096);
  const event = new f.w.Event('paste', { bubbles: true, cancelable: true });
  event.clipboardData = { items: [{ kind: 'file', type: shot.type, getAsFile: () => shot }] };
  f.d.querySelector('#in-input').dispatchEvent(event);
  await settle();
  assert.match(picked(f.d), /已选 screenshot\.png/);
  assert.equal(input(f.d).files[0].name, 'screenshot.png');
});

test('拖入后点「收下」照常走暂存与提交，取消选择后不再带上文件', async t => {
  const f = await fixture(t);
  const clip = makeFile('roll.mp4', 'video/mp4', 2048);
  dropFile(f.w, [clip]);
  await settle();
  f.d.querySelector('#in-input').value = '剪一条片头';
  f.d.querySelector('#in-form').dispatchEvent(new f.w.Event('submit', { bubbles: true, cancelable: true }));
  await settle();
  const staged = f.calls.find(c => c.url.startsWith('/api/intake/stage?'));
  const dropped = f.calls.find(c => c.url === '/api/intake/drop');
  assert.ok(staged, '要真的暂存这个文件');
  assert.equal(staged.url.includes('name=roll.mp4'), true);
  assert.ok(dropped && dropped.body.file_token, '收下时要把文件编号一起提交');
  assert.equal(dropped.body.text, '剪一条片头');
  assert.equal(picked(f.d), '', '提交完这次快照要清空已选');
  assert.equal(input(f.d).files.length, 0, '提交完原生框也要清空');
});

// ── 返修 b：M1 原生框拒收后要同步、M2 按后端支持集判格式、M3 文字拖放不拦 ──

// 真实控件的顺序是：浏览器先把 files 换成新选择，再发 change（jsdom 夹具里 files 可写）。
const nativeChange = (d, w, file) => {
  const el = d.querySelector('#in-file');
  el.files = file ? [file] : [];
  el.dispatchEvent(new w.Event('change', { bubbles: true }));
};
const nativeNames = d => [...(d.querySelector('#in-file').files || [])].map(f => f.name);
const dropEvent = (w, files, types = ['Files']) => {
  const event = new w.Event('drop', { bubbles: true, cancelable: true });
  event.dataTransfer = { files, types, items: files.map(f => ({ kind: 'file', type: f.type, getAsFile: () => f })) };
  w.document.body.dispatchEvent(event);
  return event;
};
const submitForm = f => {
  f.d.querySelector('#in-form').dispatchEvent(new f.w.Event('submit', { bubbles: true, cancelable: true }));
};

test('M1 原生框选了不支持的文件：两处一起恢复成旧选择，收下不会上传它', async t => {
  const f = await fixture(t);
  dropFile(f.w, [makeFile('old.mp4', 'video/mp4', 2048)]);
  await settle();
  assert.match(picked(f.d), /已选 old\.mp4/);
  nativeChange(f.d, f.w, makeFile('archive.zip', 'application/zip', 512));
  await settle();
  assert.match(statusText(f.d), /不是图片或视频文件/, '要说清为什么不收');
  assert.match(statusText(f.d), /仍是原来选中的 old\.mp4/, '要说清保留的是哪一个文件');
  assert.match(picked(f.d), /已选 old\.mp4/, '右侧显示的必须是实际会提交的那个');
  assert.deepEqual(nativeNames(f.d), ['old.mp4'], '原生框必须跟着回到同一个文件，不能显示 zip');
  f.d.querySelector('#in-input').value = '合成提交';
  submitForm(f);
  await settle();
  const staged = f.calls.find(c => c.url.startsWith('/api/intake/stage?'));
  assert.equal(staged.url.includes('name=old.mp4'), true, '只能暂存界面上显示的那个文件');
  assert.equal(staged.url.includes('archive.zip'), false, '拒收的文件不许被上传');
});

test('M1 本来没有选择时选了不支持的文件：原生框与「已选」同时清空', async t => {
  const f = await fixture(t);
  nativeChange(f.d, f.w, makeFile('book.pdf', 'application/pdf', 1024));
  await settle();
  assert.match(statusText(f.d), /不是图片或视频文件/);
  assert.equal(picked(f.d), '', '没有有效选择就不该显示已选');
  assert.deepEqual(nativeNames(f.d), [], '原生框里那个不支持的文件也要清掉');
});

test('M1 拖入不支持的文件也不会覆盖已有选择', async t => {
  const f = await fixture(t);
  dropFile(f.w, [makeFile('old.mp4', 'video/mp4', 2048)]);
  await settle();
  dropFile(f.w, [makeFile('archive.zip', 'application/zip', 512)]);
  await settle();
  assert.match(picked(f.d), /已选 old\.mp4/);
  assert.deepEqual(nativeNames(f.d), ['old.mp4']);
  assert.match(statusText(f.d), /仍是原来选中的 old\.mp4/);
});

test('M2 后端不支持的格式当场拒收并说清支持哪些：GIF / SVG / OGG', async t => {
  const f = await fixture(t);
  const cases = [['anim.gif', 'image/gif', /图片格式不支持/], ['drawing.svg', 'image/svg+xml', /图片格式不支持/],
    ['clip.ogg', 'video/ogg', /视频格式不支持/]];
  for (const [name, type, why] of cases) {
    dropFile(f.w, [makeFile(name, type, 4096)]);
    await settle();
    assert.equal(picked(f.d), '', name + ' 不能变成「已选」');
    assert.equal(input(f.d).files.length, 0, name + ' 不能写进原生框');
    assert.match(statusText(f.d), why, name + ' 要说清是格式不支持');
    assert.match(statusText(f.d), /PNG \/ JPEG \/ WebP/, '要说清支持哪些图片');
    assert.match(statusText(f.d), /MP4 \/ MOV \/ M4V \/ WebM \/ MKV \/ AVI/, '要说清支持哪些视频');
  }
});

test('M2 File.type 为空时按受支持后缀判断：.mp4 / .mkv 收，.gif / .bin 拒', async t => {
  const f = await fixture(t);
  dropFile(f.w, [makeFile('unknown.mp4', '', 2048)]);
  await settle();
  assert.match(picked(f.d), /已选 unknown\.mp4/, '空 MIME 的 mp4 是常见情况，不能一律当不支持');
  assert.deepEqual(nativeNames(f.d), ['unknown.mp4']);
  f.d.querySelector('#in-input').value = '空 MIME 的 mp4';
  submitForm(f);
  await settle();
  const staged = f.calls.find(c => c.url.startsWith('/api/intake/stage?'));
  assert.equal(staged.url.includes('kind=video'), true, '收下时也要按视频暂存，不能在后端才炸');
  dropFile(f.w, [makeFile('clip.mkv', '', 2048)]);
  await settle();
  assert.match(picked(f.d), /已选 clip\.mkv/, '后端收的 MKV 也要能选');
  dropFile(f.w, [makeFile('anim.gif', '', 2048)]);
  await settle();
  assert.match(picked(f.d), /已选 clip\.mkv/, '拒收的文件不能顶掉已有选择');
  assert.deepEqual(nativeNames(f.d), ['clip.mkv'], '拒收后两处仍一致');
  assert.match(statusText(f.d), /不是图片或视频文件/, '后端不支持的 GIF 后缀即使 MIME 为空也要拒收');
  dropFile(f.w, [makeFile('notes.bin', '', 2048)]);
  await settle();
  assert.match(picked(f.d), /已选 clip\.mkv/, '不认识的空 MIME 后缀同样拒收');
  assert.deepEqual(nativeNames(f.d), ['clip.mkv']);
  assert.match(statusText(f.d), /不是图片或视频文件/);
});

test('M3 文字拖放不拦默认行为（浏览器才能把文字插进输入框），文件拖放仍然要拦', async t => {
  const f = await fixture(t);
  const form = f.d.querySelector('#in-form'), box = f.d.querySelector('#in-input');
  const textDrop = () => {
    const event = new f.w.Event('drop', { bubbles: true, cancelable: true });
    event.dataTransfer = { files: [], types: ['text/plain'], getData: () => '拖进来的文字' };
    box.dispatchEvent(event);
    return event;
  };
  assert.equal(textDrop().defaultPrevented, false, '文字拖放被 preventDefault 就插不进输入框');
  assert.equal(form.classList.contains('in-dragging'), false, '文字不该高亮');
  assert.equal(dropEvent(f.w, [makeFile('a.png', 'image/png', 10)]).defaultPrevented, true,
    '文件拖放仍要拦，否则浏览器会把拖到页面上的文件直接打开');
  const mixedDrop = new f.w.Event('drop', { bubbles: true, cancelable: true });
  mixedDrop.dataTransfer = { files: [makeFile('a.png', 'image/png', 10)], types: ['Files', 'text/plain'] };
  f.d.body.dispatchEvent(mixedDrop);
  assert.equal(mixedDrop.defaultPrevented, true, '同时带文件与文字时按文件处理');
});

test('M3 普通文字粘贴不受影响：不拦、也不动页面状态', async t => {
  const f = await fixture(t);
  const box = f.d.querySelector('#in-input');
  box.value = '已有文字';
  const event = new f.w.Event('paste', { bubbles: true, cancelable: true });
  event.clipboardData = { items: [{ kind: 'string', type: 'text/plain', getAsFile: () => null }], getData: () => '粘进来的文字' };
  box.dispatchEvent(event);
  await settle();
  assert.equal(event.defaultPrevented, false, '文字粘贴不能被拦');
  assert.equal(box.value, '已有文字', '页面不插手文字粘贴');
  assert.equal(picked(f.d), '');
});

// ── 返修 c：空 MIME 的 WebP 也要能选，前后端支持集逐项一致 ──

test('C 空 MIME 的 WebP：拖入与原生选择后两处一致，可「收下」并以 kind=image 暂存', async t => {
  const f = await fixture(t);
  dropFile(f.w, [makeFile('blank.webp', '', 2048)]);
  await settle();
  assert.match(picked(f.d), /已选 blank\.webp/, '后端收 WebP，提示文案也写着支持 WebP，就不能自己拒收');
  assert.deepEqual(nativeNames(f.d), ['blank.webp'], '原生框与「已选」必须同步');
  assert.equal(statusText(f.d), '', '合法文件不该留下警告');
  // 原生选择框选的同一个文件也要一致
  nativeChange(f.d, f.w, makeFile('blank.webp', '', 4096));
  await settle();
  assert.match(picked(f.d), /已选 blank\.webp/);
  assert.deepEqual(nativeNames(f.d), ['blank.webp']);
  f.d.querySelector('#in-input').value = '空 MIME 的 webp';
  submitForm(f);
  await settle();
  const staged = f.calls.find(c => c.url.startsWith('/api/intake/stage?'));
  assert.equal(staged.url.includes('kind=image'), true, '收下时必须按图片暂存，不能在前端就拒掉');
  assert.equal(staged.url.includes('name=blank.webp'), true);
});

// 前后端支持集的逐项一致性：名单直接从 intake_store.py 读出来，后端改了就必须跟着改前端。
function backendSuffixes() {
  const source = fs.readFileSync('intake_store.py', 'utf8');
  const imageMap = /IMAGE_SUFFIXES\s*=\s*\{([^}]*)\}/.exec(source);
  const videoList = /VIDEO_SUFFIXES\s*=\s*\(([^)]*)\)/.exec(source);
  assert.ok(imageMap, '读不到 intake_store.IMAGE_SUFFIXES');
  assert.ok(videoList, '读不到 intake_store.VIDEO_SUFFIXES');
  return {
    image: [...imageMap[1].matchAll(/'(\.\w+)'/g)].map(m => m[1]),
    video: [...videoList[1].matchAll(/'(\.\w+)'/g)].map(m => m[1]),
  };
}

test('C 前后端支持集逐项一致：后端收的后缀前端都收，前端不多收一个', async t => {
  const backend = backendSuffixes();
  assert.deepEqual(backend.image.sort(), ['.jpg', '.png', '.webp'], '后端图片后缀名单变了，先更新本用例再改前端');
  assert.deepEqual(backend.video.sort(), ['.avi', '.m4v', '.mkv', '.mov', '.mp4', '.webm']);
  const f = await fixture(t);
  for (const [suffix, kind] of [...backend.image.map(s => [s, 'image']), ...backend.video.map(s => [s, 'video'])]) {
    const name = 'blank' + suffix;
    dropFile(f.w, [makeFile(name, '', 2048)]);
    await settle();
    assert.match(picked(f.d), new RegExp('已选 ' + name.replace('.', '\\.')), suffix + ' 空 MIME 必须可选');
    assert.deepEqual(nativeNames(f.d), [name], suffix + ' 原生框要同步');
    assert.equal(statusText(f.d), '', suffix + ' 不该留下警告');
    f.d.querySelector('#in-input').value = '后缀一致 ' + suffix;
    submitForm(f);
    await settle();
    const staged = f.calls.filter(c => c.url.startsWith('/api/intake/stage?')).at(-1);
    assert.equal(staged.url.includes('kind=' + kind), true, suffix + ' 必须按 ' + kind + ' 暂存');
  }
});

test('C 前端不放行后端不支持的后缀', async t => {
  const backend = backendSuffixes();
  const f = await fixture(t);
  for (const suffix of ['.gif', '.svg', '.bmp', '.tif', '.tiff', '.ogv', '.wmv', '.flv', '.heic', '.bin', '.pdf', '.zip']) {
    assert.equal(backend.image.includes(suffix) || backend.video.includes(suffix), false,
      suffix + ' 已在后端名单里，本用例的前提不再成立');
    dropFile(f.w, [makeFile('doc' + suffix, '', 2048)]);
    await settle();
    assert.equal(picked(f.d), '', suffix + ' 不能变成「已选」');
    assert.deepEqual(nativeNames(f.d), [], suffix + ' 不能写进原生框');
    assert.match(statusText(f.d), /不是图片或视频文件|格式不支持/, suffix + ' 要说清为什么不收');
  }
  assert.equal(f.calls.filter(c => c.url.startsWith('/api/intake/stage?')).length, 0, '拒收的文件不许发暂存');
});
