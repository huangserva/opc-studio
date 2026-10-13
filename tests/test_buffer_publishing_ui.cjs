/* The Buffer panel must only appear for X posts, confirm every tweet, and never
   submit twice or without an explicit confirmation. */
const test = require('node:test'), assert = require('node:assert/strict'), fs = require('node:fs');
const { JSDOM } = require('jsdom');
const source = fs.readFileSync('public/buffer-publishing.js', 'utf8');
const contentSource = fs.readFileSync('public/content.js', 'utf8');
const tick = () => new Promise(resolve => setImmediate(resolve));

const TEXTS = ['第一条：Claude 封号故事 🚀', '第二条：Sidrune AI 怎么接手', '第三条：完整方法 “引号” & 与号'];
const CHECK = {
  account: 'servasyy_ai', version: 'v0003', resolved: true,
  channel: { id: 'facade'.repeat(4), name: 'servasyy_ai', service: 'twitter' },
  message: 'Buffer 渠道 @servasyy_ai 与目标账号一致',
  posts: TEXTS.map(text => ({ text, assets: [] })), post_count: 3, blocking: [], missing_media: [],
  submission_mode: 'draft',
};

function fixture({ platform = 'x', type = 'post', check = CHECK, rejectCheck = null, deferSubmit = false, deferCheck = false } = {}) {
  const dom = new JSDOM('<main><button id="export-package">导出</button><div id="delivery-history"></div></main>',
    { url: 'http://127.0.0.1:8766/?article=fixture', runScripts: 'outside-only' });
  const w = dom.window, calls = [], messages = [];
  let resolveSubmit, resolveCheck;
  w.$ = selector => w.document.querySelector(selector);
  w.state = { slug: 'fixture', platform, studio: { type }, article: { version: { id: 'v0003' }, deliveries: [] }, dirty: false };
  w.escapeHTML = text => String(text ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  w.versionName = value => value;
  w.date = value => value;
  w.toast = message => messages.push(message);
  w.refreshDeliveries = async () => { calls.push({ path: '/refresh-deliveries' }); };
  w.HTMLDialogElement.prototype.showModal = function () { this.open = true; };
  w.HTMLDialogElement.prototype.close = function () { this.open = false; this.dispatchEvent(new w.Event('close')); };
  w.api = async (path, payload) => {
    calls.push({ path, payload });
    if (path.startsWith('/api/buffer/channels/')) {
      if (rejectCheck) throw new Error(rejectCheck);
      if (deferCheck) return new Promise(resolve => { resolveCheck = () => resolve(check); });
      return check;
    }
    if (path.startsWith('/api/buffer/submit/')) {
      if (deferSubmit) return new Promise(resolve => { resolveSubmit = () => resolve({ record: { message: 'Buffer 已接收该帖子',kind:'buffer_draft' }, outcome: 'submitted' }); });
      return { record: { message: 'Buffer 已接收该帖子', kind: 'buffer_draft' }, outcome: 'submitted', retryable: false };
    }
    if (path.startsWith('/api/buffer/status/')) {
      return { record: { message: 'Buffer 当前状态：draft' }, outcome: 'ok' };
    }
    return {};
  };
  w.eval(source);
  return {
    w, calls, messages, check,
    close: () => w.close(),
    mount: () => w.mountBufferPublishing(),
    finishSubmit: () => resolveSubmit && resolveSubmit(),
    finishCheck: () => resolveCheck && resolveCheck(),
    submitCalls: () => calls.filter(call => call.path.startsWith('/api/buffer/submit/')),
    dialog: () => w.document.querySelector('dialog'),
  };
}

test('X post panel offers draft, immediate publish and status refresh', async () => {
  const f = fixture();
  try {
    await f.mount();
    assert.ok(f.w.document.querySelector('#buffer-draft'));
    assert.ok(f.w.document.querySelector('#buffer-publish'));
    assert.ok(f.w.document.querySelector('#buffer-refresh'));
    assert.equal(f.calls[0].path, '/api/buffer/channels/fixture');
    assert.equal(f.calls[0].payload.version, 'v0003');
    assert.equal(f.calls[0].payload.platform, 'x');
    assert.match(f.w.document.querySelector('#buffer-channel-detail').textContent, /@servasyy_ai/);
    assert.equal(f.w.document.querySelector('#buffer-draft').disabled, false);
    assert.equal(f.w.document.querySelector('#buffer-publish').disabled, false);
  } finally { f.close(); }
});

test('long articles and other platforms get no Buffer panel at all', async () => {
  for (const options of [{ type: 'article' }, { type: 'video' }, { platform: 'xiaohongshu' }]) {
    const f = fixture(options);
    try {
      await f.mount();
      assert.equal(f.w.document.querySelector('.buffer-publishing'), null);
      assert.equal(f.calls.length, 0);
    } finally { f.close(); }
  }
});

test('content.js mounts the panel only on the X tab for post works', () => {
  assert.match(contentSource, /if\(platform==='x' && s\.type==='post'\)mountBufferPublishing\(\);/);
  assert.match(contentSource, /if\(platform==='x' && s\.type==='article'\)mountLocalPublishing\(\);/);
  assert.equal(contentSource.match(/mountBufferPublishing\(\)/g).length, 1);
});

test('confirmation lists the account, channel and every tweet in full', async () => {
  const f = fixture();
  try {
    await f.mount();
    f.w.document.querySelector('#buffer-draft').click();
    await tick();
    const dialog = f.dialog();
    assert.ok(dialog);
    assert.equal(dialog.querySelectorAll('.buffer-tweet').length, 3);
    const text = dialog.textContent;
    for (const tweet of TEXTS) assert.ok(text.includes(tweet), 'missing tweet text in confirmation');
    assert.match(text, /目标账号/);
    assert.match(text, /Buffer 渠道/);
    assert.match(text, /v0003/);
    assert.match(text, /不会公开发布/);
    assert.equal(f.submitCalls().length, 0);
  } finally { f.close(); }
});

test('immediate publish warns that Buffer publishes publicly and uses shareNow', async () => {
  const f = fixture();
  try {
    await f.mount();
    f.w.document.querySelector('#buffer-publish').click();
    await tick();
    assert.match(f.dialog().textContent, /立即公开发布到 X/);
    const box = f.dialog().querySelector('input');
    box.checked = true;
    box.onchange({ target: box });
    f.dialog().querySelector('[data-go]').click();
    await tick(); await tick();
    const payload = f.submitCalls()[0].payload;
    assert.equal(payload.version, 'v0003');
    assert.equal(payload.platform, 'x');
    assert.equal(payload.mode, 'publish_now');
    assert.equal(payload.confirmed, true);
  } finally { f.close(); }
});

test('cancelling the confirmation sends nothing', async () => {
  const f = fixture();
  try {
    await f.mount();
    f.w.document.querySelector('#buffer-draft').click();
    await tick();
    f.dialog().querySelector('[data-cancel]').click();
    await tick();
    assert.equal(f.dialog(), null);
    assert.equal(f.submitCalls().length, 0);
    assert.equal(f.w.document.querySelector('#buffer-draft').disabled, false);
  } finally { f.close(); }
});

test('submit stays locked until the reviewer ticks the confirmation', async () => {
  const f = fixture();
  try {
    await f.mount();
    f.w.document.querySelector('#buffer-draft').click();
    await tick();
    const go = f.dialog().querySelector('[data-go]');
    assert.equal(go.disabled, true);
    go.click();
    await tick();
    assert.equal(f.submitCalls().length, 0);
    const box = f.dialog().querySelector('input');
    box.checked = true;
    box.onchange({ target: box });
    assert.equal(go.disabled, false);
    go.click();
    await tick(); await tick();
    assert.equal(f.submitCalls().length, 1);
    assert.equal(f.submitCalls()[0].payload.confirmed, true);
    assert.equal(f.submitCalls()[0].payload.mode, 'draft');
    assert.ok(f.calls.some(call => call.path === '/refresh-deliveries'));
    assert.equal(f.dialog(), null);
  } finally { f.close(); }
});

test('an in-flight submit cannot be triggered twice', async () => {
  const f = fixture({ deferSubmit: true });
  try {
    await f.mount();
    f.w.document.querySelector('#buffer-draft').click();
    await tick();
    const box = f.dialog().querySelector('input');
    box.checked = true;
    box.onchange({ target: box });
    const go = f.dialog().querySelector('[data-go]');
    go.click(); await tick();
    go.click();
    f.w.document.querySelector('#buffer-publish').click();
    await tick();
    assert.equal(f.submitCalls().length, 1);
    f.finishSubmit();
    await tick(); await tick();
  } finally { f.close(); }
});

test('an unresolved Buffer channel blocks both buttons before any submit', async () => {
  const f = fixture({ check: { ...CHECK, resolved: false, channel: null, posts: [], message: 'Buffer 渠道中没有与目标账号 @servasyy_ai 一致的 X 渠道' } });
  try {
    await f.mount();
    assert.equal(f.w.document.querySelector('#buffer-draft').disabled, true);
    assert.equal(f.w.document.querySelector('#buffer-publish').disabled, true);
    assert.match(f.w.document.querySelector('#buffer-channel-detail [role=alert]').textContent, /没有与目标账号/);
    f.w.document.querySelector('#buffer-draft').click();
    f.w.document.querySelector('#buffer-publish').click();
    await tick();
    assert.equal(f.dialog(), null);
    assert.equal(f.submitCalls().length, 0);
  } finally { f.close(); }
});

test('a failed channel check leaves the panel unusable and explains why', async () => {
  const f = fixture({ rejectCheck: '读取 Buffer 渠道失败：认证失败' });
  try {
    await f.mount();
    assert.match(f.w.document.querySelector('#buffer-check').textContent, /读取 Buffer 渠道失败/);
    assert.equal(f.w.document.querySelector('#buffer-draft').disabled, true);
    assert.equal(f.w.document.querySelector('#buffer-publish').disabled, true);
    assert.equal(f.submitCalls().length, 0);
  } finally { f.close(); }
});

test('a rejected submit keeps the dialog open, reports the reason and re-arms the buttons', async () => {
  const f = fixture();
  const original = f.w.api;
  f.w.api = async (path, payload) => {
    if (path.startsWith('/api/buffer/submit/')) { f.calls.push({ path, payload }); throw new Error('该版本已有 Buffer 提交记录（已在 Buffer 草稿箱），为避免重复发布已停止提交'); }
    return original(path, payload);
  };
  try {
    await f.mount();
    f.w.document.querySelector('#buffer-draft').click();
    await tick();
    const box = f.dialog().querySelector('input');
    box.checked = true;
    box.onchange({ target: box });
    f.dialog().querySelector('[data-go]').click();
    await tick(); await tick();
    assert.ok(f.dialog());
    assert.equal(f.dialog().open, true);
    assert.equal(f.dialog().querySelector('[data-go]').disabled, false);
    assert.equal(f.dialog().querySelector('[data-cancel]').disabled, false);
    // The panel behind the open dialog stays locked until the reviewer leaves it.
    assert.equal(f.w.document.querySelector('#buffer-draft').disabled, true);
    f.dialog().querySelector('[data-cancel]').click();
    await tick();
    assert.equal(f.dialog(), null);
    assert.equal(f.w.document.querySelector('#buffer-draft').disabled, false);
    assert.equal(f.submitCalls().length, 1);
  } finally { f.close(); }
});

test('a refused submit shows the full backend reason inside the open dialog', async () => {
  const REASON = '该版本已有 Buffer 提交记录（已在 Buffer 草稿箱），为避免重复发布已停止提交；请先在 Buffer 核实或处理该帖子。结果不明的提交不会自动重试';
  const f = fixture();
  const original = f.w.api;
  f.w.api = async (path, payload) => {
    if (path.startsWith('/api/buffer/submit/')) { f.calls.push({ path, payload }); throw new Error(REASON); }
    return original(path, payload);
  };
  try {
    await f.mount();
    f.w.document.querySelector('#buffer-draft').click();
    await tick();
    const before = f.dialog().querySelector('[role=alert]');
    assert.ok(before, 'the confirmation dialog owns its alert region');
    assert.equal(before.hidden, true);
    const box = f.dialog().querySelector('input');
    box.checked = true;
    box.onchange({ target: box });
    f.dialog().querySelector('[data-go]').click();
    await tick(); await tick();
    const alert = f.dialog().querySelector('[role=alert]');
    assert.equal(alert.hidden, false);
    assert.equal(alert.textContent, REASON);
    assert.match(f.dialog().textContent, /为避免重复发布已停止提交/);
    // The reason must not depend on the out-of-dialog toast, which a native modal
    // keeps behind its backdrop and out of reach.
    assert.equal(f.messages.length, 0);
    assert.match(f.w.document.querySelector('#buffer-result').textContent, /发送未确认/);
    assert.equal(f.submitCalls().length, 1);
  } finally { f.close(); }
});

test('a second click inside the dialog reports the clash inside the dialog too', async () => {
  const f = fixture({ deferSubmit: true });
  try {
    await f.mount();
    f.w.document.querySelector('#buffer-draft').click();
    await tick();
    const box = f.dialog().querySelector('input');
    box.checked = true;
    box.onchange({ target: box });
    const go = f.dialog().querySelector('[data-go]');
    go.click(); await tick();
    go.disabled = false;
    go.click(); await tick();
    const alert = f.dialog().querySelector('[role=alert]');
    assert.equal(alert.hidden, false);
    assert.match(alert.textContent, /提交正在进行/);
    assert.equal(f.messages.length, 0);
    assert.equal(f.submitCalls().length, 1);
    f.finishSubmit();
    await tick(); await tick();
  } finally { f.close(); }
});

test('both submit buttons start disabled while the channel check is still running', async () => {
  const f = fixture({ deferCheck: true });
  try {
    const mounted = f.mount();
    await tick();
    assert.equal(f.w.document.querySelector('#buffer-draft').disabled, true);
    assert.equal(f.w.document.querySelector('#buffer-publish').disabled, true);
    assert.match(f.w.document.querySelector('#buffer-check').textContent, /正在核对 Buffer 渠道/);
    f.w.document.querySelector('#buffer-draft').click();
    f.w.document.querySelector('#buffer-publish').click();
    await tick();
    assert.equal(f.dialog(), null);
    assert.equal(f.submitCalls().length, 0);
    f.finishCheck();
    await mounted;
    assert.equal(f.w.document.querySelector('#buffer-draft').disabled, false);
    assert.equal(f.w.document.querySelector('#buffer-publish').disabled, false);
    assert.match(f.w.document.querySelector('#buffer-check').textContent, /与目标账号一致/);
  } finally { f.close(); }
});

test('a failed re-check locks the buttons again and states why', async () => {
  const f = fixture();
  let failNext = false;
  const original = f.w.api;
  f.w.api = async (path, payload) => {
    if (path.startsWith('/api/buffer/channels/') && failNext) { failNext = false; throw new Error('读取 Buffer 渠道失败：认证失败'); }
    return original(path, payload);
  };
  try {
    await f.mount();
    assert.equal(f.w.document.querySelector('#buffer-draft').disabled, false);
    failNext = true;
    f.w.document.querySelector('#buffer-recheck').click();
    await tick(); await tick();
    assert.match(f.w.document.querySelector('#buffer-check').textContent, /认证失败/);
    assert.match(f.w.document.querySelector('#buffer-channel-detail [role=alert]').textContent, /认证失败/);
    assert.doesNotMatch(f.w.document.querySelector('#buffer-channel-detail').textContent, /与目标账号一致/);
    assert.equal(f.w.document.querySelector('#buffer-draft').disabled, true);
    assert.equal(f.w.document.querySelector('#buffer-publish').disabled, true);
    assert.equal(f.w.document.querySelector('#buffer-recheck').disabled, false);
    f.w.document.querySelector('#buffer-draft').click();
    await tick();
    assert.equal(f.dialog(), null);
    assert.equal(f.submitCalls().length, 0);
  } finally { f.close(); }
});

test('status refresh reads the stored Buffer post and repaints the records', async () => {
  const f = fixture();
  try {
    await f.mount();
    f.w.document.querySelector('#buffer-refresh').click();
    await tick(); await tick();
    const status = f.calls.find(call => call.path.startsWith('/api/buffer/status/'));
    assert.equal(status.payload.version, 'v0003');
    assert.equal(status.payload.platform, 'x');
    assert.match(f.w.document.querySelector('#buffer-result').textContent, /Buffer 当前状态/);
    assert.ok(f.calls.some(call => call.path === '/refresh-deliveries'));
  } finally { f.close(); }
});

test('re-checking the channel re-reads it instead of trusting the cached panel', async () => {
  const f = fixture();
  try {
    await f.mount();
    f.w.document.querySelector('#buffer-recheck').click();
    await tick();
    assert.equal(f.calls.filter(call => call.path.startsWith('/api/buffer/channels/')).length, 2);
  } finally { f.close(); }
});

test('sending immediately shows waiting state in the modal and locks repeat clicks',async()=>{
 const f=fixture({deferSubmit:true});
 try{
  await f.mount();f.w.document.querySelector('#buffer-draft').click();
  const box=f.dialog().querySelector('input');box.checked=true;box.onchange();
  const go=f.dialog().querySelector('[data-go]');go.click();await tick();
  assert.equal(go.textContent,'正在发送…');
  const progress=f.dialog().querySelector('.buffer-dialog-progress');assert.equal(progress.hidden,false);assert.match(progress.textContent,/已等待.*请勿重复/);
  const cancel=new f.w.Event('cancel',{cancelable:true});f.dialog().dispatchEvent(cancel);assert.equal(cancel.defaultPrevented,true);
  assert.equal(f.submitCalls().length,1);f.finishSubmit();await tick();await tick();
  assert.match(f.w.document.querySelector('#buffer-result').textContent,/尚未发布/);
 }finally{f.close();}
});

test('draft status automatically becomes published, exposes X link, then stops polling',async()=>{
 const f=fixture(),scheduled=new Map();let serial=0,reads=0;
 try{
  Object.defineProperty(f.w.document,'hidden',{value:false,configurable:true});
  f.w.setTimeout=(callback,delay)=>{const id=++serial;scheduled.set(id,{callback,delay});return id;};
  f.w.clearTimeout=id=>scheduled.delete(id);
  const draft={id:'record',kind:'buffer_draft',buffer_status:'draft',version:'v0003',post_id:'stored-post',account:'servasyy_ai'};
  f.w.state.article.deliveries=[draft];const api=f.w.api;
  f.w.api=async(path,payload)=>{
   if(!path.startsWith('/api/buffer/status/'))return api(path,payload);
   reads++;const record=reads===1?draft:{...draft,kind:'buffer_published',buffer_status:'sent',url:'https://x.com/servasyy_ai/status/123',observed_at:'2026-10-11'};
   f.w.state.article.deliveries=[record];return {record,outcome:'ok'};
  };
  await f.mount();assert.equal(reads,1);
  const timer=[...scheduled.values()][0];assert.equal(timer.delay,180000);
  await timer.callback();assert.equal(reads,2);assert.equal(scheduled.size,0);
  assert.match(f.w.document.querySelector('#buffer-result').textContent,/已发布到 X/);
  const links=[...f.w.document.querySelectorAll('#buffer-result a')];assert.ok(links.some(a=>a.href==='https://x.com/servasyy_ai/status/123'));
  assert.match(f.w.document.querySelector('#buffer-auto-status').textContent,/停止自动核对/);assert.equal(f.submitCalls().length,0);
 }finally{f.close();}
});

test('background tabs do not poll and discarded panels release their timers',async()=>{
 const f=fixture(),scheduled=new Map();let serial=0;
 try{
  f.w.setTimeout=(callback,delay)=>{const id=++serial;scheduled.set(id,{callback,delay});return id;};f.w.clearTimeout=id=>scheduled.delete(id);
  f.w.state.article.deliveries=[{kind:'buffer_draft',buffer_status:'draft',version:'v0003',post_id:'post'}];
  await f.mount();const before=f.calls.filter(c=>c.path.startsWith('/api/buffer/status/')).length;
  await [...scheduled.values()][0].callback();assert.equal(f.calls.filter(c=>c.path.startsWith('/api/buffer/status/')).length,before);
  f.w.document.querySelector('.buffer-publishing').remove();await tick();assert.equal(scheduled.size,0);
 }finally{f.close();}
});

test('record labels separate drafts, scheduled, failed and sent-without-link; links escape content',()=>{
 const f=fixture();try{
  assert.match(f.w.bufferRecordState({kind:'buffer_draft',buffer_status:'scheduled'}).label,/已安排发布/);
  assert.match(f.w.bufferRecordState({kind:'buffer_draft',buffer_status:'failed'}).label,/发布失败/);
  assert.match(f.w.bufferRecordState({kind:'buffer_draft',buffer_status:'sent'}).label,/链接待确认/);
  const text=f.w.bufferRecordMarkup({kind:'buffer_draft',version:'v1',message:'<script>bad</script>',buffer_url:'javascript:alert(1)'});
  assert.ok(!text.includes('<script>'));assert.ok(text.includes('https://publish.buffer.com/'));assert.ok(!text.includes('javascript:'));
 }finally{f.close();}
});
