/* X posts/threads that carry images: the confirm dialog must show which tweet gets
   which pictures, using local previews only, and every upload failure has to be
   readable inside the dialog. No real image host is contacted. */
const test = require('node:test'), assert = require('node:assert/strict'), fs = require('node:fs');
const { JSDOM } = require('jsdom');
const source = fs.readFileSync('public/buffer-publishing.js', 'utf8');
const tick = () => new Promise(resolve => setImmediate(resolve));

const TEXTS = ['第一条带一张图', '第二条带两张图', '第三条不带图'];
const IMAGES = [
  [{ file: 'images/one.png', bytes: 24500, width: 1200, height: 630, url: '/assets/fixture/v0003/images/one.png' }],
  [{ file: 'images/two.png', bytes: 5242880, width: 1200, height: 630, url: '/assets/fixture/v0003/images/two.png' },
   { file: 'images/three.gif', bytes: 90000, width: 480, height: 270, url: '/assets/fixture/v0003/images/three.gif' }],
  [],
];
const CHECK = {
  account: 'servasyy_ai', version: 'v0003', resolved: true,
  channel: { id: 'facade'.repeat(4), name: 'servasyy_ai', service: 'twitter' },
  message: 'Buffer 渠道 @servasyy_ai 与目标账号一致',
  posts: TEXTS.map((text, index) => ({
    text, assets: [], images: IMAGES[index], image_count: IMAGES[index].length,
  })),
  post_count: 3, blocking: [], missing_media: [], image_total: 3, submission_mode: 'draft',
};
const TEXT_CHECK = Object.assign({}, CHECK, {
  posts: TEXTS.map(text => ({ text, assets: [], images: [], image_count: 0 })),
  image_total: 0,
});

function fixture({ check = CHECK, media = { configured: true, host: 'media.example.com', problem: null },
                  rejectSubmit = null, deferCheck = false } = {}) {
  const dom = new JSDOM('<main><button id="export-package">导出</button><div id="delivery-history"></div></main>',
    { url: 'http://127.0.0.1:8766/?article=fixture', runScripts: 'outside-only' });
  const w = dom.window, calls = [], messages = [];
  let resolveCheck;
  w.$ = selector => w.document.querySelector(selector);
  w.state = { slug: 'fixture', platform: 'x', studio: { type: 'post' },
    article: { version: { id: 'v0003' }, deliveries: [] }, dirty: false };
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
      if (deferCheck) return new Promise(resolve => { resolveCheck = () => resolve(check); });
      return check;
    }
    if (path.startsWith('/api/buffer/media/')) return media;
    if (path.startsWith('/api/buffer/submit/')) {
      if (rejectSubmit) throw new Error(rejectSubmit);
      return { record: { message: 'Buffer 已接收该帖子', kind: 'buffer_draft' }, outcome: 'submitted', retryable: false };
    }
    if (path.startsWith('/api/buffer/status/')) return { record: { message: 'Buffer 当前状态：draft' }, outcome: 'ok' };
    return {};
  };
  w.eval(source);
  return {
    w, calls, messages,
    close: () => w.close(),
    mount: () => w.mountBufferPublishing(),
    finishCheck: () => resolveCheck && resolveCheck(),
    paths: () => calls.map(call => call.path),
    submitCalls: () => calls.filter(call => call.path.startsWith('/api/buffer/submit/')),
    mediaCalls: () => calls.filter(call => call.path.startsWith('/api/buffer/media/')),
    dialog: () => w.document.querySelector('dialog'),
    panel: () => w.document.querySelector('#buffer-channel-detail'),
  };
}

async function openDialog(f, button = '#buffer-draft') {
  f.w.document.querySelector(button).click();
  await tick();
  return f.dialog();
}

test('the panel counts the images and names the image host before submitting', async () => {
  const f = fixture();
  try {
    await f.mount();
    const panel = f.panel().textContent;
    assert.match(panel, /3 条推文 · 3 张图片/);
    assert.match(panel, /图片会在点击提交后上传到图床（media\.example\.com）/);
    assert.equal(f.mediaCalls().length, 1);
    assert.equal(f.w.document.querySelector('#buffer-draft').disabled, false);
  } finally { f.close(); }
});

test('the confirm dialog shows each tweet its own thumbnails, file names and counts', async () => {
  const f = fixture();
  try {
    await f.mount();
    const dialog = await openDialog(f);
    const tweets = dialog.querySelectorAll('.buffer-tweet');
    assert.equal(tweets.length, 3);
    const perTweet = [...tweets].map(tweet => tweet.querySelectorAll('.buffer-tweet-images img').length);
    assert.deepEqual(perTweet, [1, 2, 0], 'thumbnails must follow the tweet that carries them');
    const sources = [...dialog.querySelectorAll('.buffer-tweet-images img')].map(img => img.getAttribute('src'));
    assert.deepEqual(sources, ['/assets/fixture/v0003/images/one.png', '/assets/fixture/v0003/images/two.png',
      '/assets/fixture/v0003/images/three.gif']);
    const captions = [...dialog.querySelectorAll('.buffer-tweet-images figcaption')].map(node => node.textContent);
    assert.match(captions[0], /images\/one\.png · 24 KB/);
    assert.match(captions[1], /5\.0 MB/);
    const counts = [...tweets].map(tweet => tweet.querySelector('small').textContent);
    assert.deepEqual(counts, ['带 1 张图片', '带 2 张图片', '不带图片']);
    assert.match(dialog.textContent, /3 张图片/);
    assert.match(dialog.textContent, /我已核对目标账号、Buffer 渠道、全部 3 条推文全文与每条推文所带的图片/);
  } finally { f.close(); }
});

test('opening the dialog previews local images and uploads or submits nothing', async () => {
  const f = fixture();
  try {
    await f.mount();
    await openDialog(f);
    const sources = [...f.dialog().querySelectorAll('.buffer-tweet-images img')].map(img => img.getAttribute('src'));
    assert.ok(sources.every(src => src.startsWith('/assets/fixture/v0003/')), 'previews must stay local');
    assert.ok(!sources.some(src => src.includes('media.example.com')));
    assert.equal(f.submitCalls().length, 0);
    assert.deepEqual(f.paths().filter(path => !path.startsWith('/api/buffer/')), []);
  } finally { f.close(); }
});

test('an unconfigured image host blocks submit and says why in the panel', async () => {
  const f = fixture({ media: { configured: false, host: null, problem: '尚未配置图床（R2）：带图作品需要 ~/Library/Application Support/ArticleStudio/r2-media.json（或环境变量 ARTICLE_STUDIO_R2_CONFIG）；纯文字推文不受影响' } });
  try {
    await f.mount();
    assert.equal(f.w.document.querySelector('#buffer-draft').disabled, true);
    assert.equal(f.w.document.querySelector('#buffer-publish').disabled, true);
    assert.match(f.panel().textContent, /尚未配置图床（R2）/);
    await openDialog(f);
    assert.equal(f.dialog(), null, 'a blocked work must not open a submittable dialog');
    assert.equal(f.submitCalls().length, 0);
  } finally { f.close(); }
});

test('a missing image host leaves text-only works untouched', async () => {
  const f = fixture({ check: TEXT_CHECK, media: { configured: false, host: null, problem: '尚未配置图床（R2）：带图作品需要配置' } });
  try {
    await f.mount();
    assert.equal(f.w.document.querySelector('#buffer-draft').disabled, false);
    assert.equal(f.w.document.querySelector('#buffer-publish').disabled, false);
    assert.doesNotMatch(f.panel().textContent, /尚未配置图床/);
    const dialog = await openDialog(f);
    assert.ok(dialog);
    assert.match(dialog.textContent, /不带图片/);
    assert.doesNotMatch(dialog.textContent, /张图片/);
  } finally { f.close(); }
});

test('upload and public-link failures stay inside the dialog instead of a toast', async () => {
  const reason = '图片公开校验未通过：images/upload-9f3a.png → 链接返回 HTTP 404；未提交到 Buffer，可重试';
  const f = fixture({ rejectSubmit: reason });
  try {
    await f.mount();
    const dialog = await openDialog(f);
    const box = dialog.querySelector('input');
    box.checked = true; box.dispatchEvent(new f.w.Event('change'));
    const go = dialog.querySelector('[data-go]'), cancel = dialog.querySelector('[data-cancel]');
    assert.equal(go.disabled, false);
    go.click();
    await tick();
    const error = dialog.querySelector('.buffer-dialog-error');
    assert.equal(error.hidden, false);
    assert.equal(error.textContent, reason);
    assert.deepEqual(f.messages, [], 'a blurred toast behind a modal dialog is not readable');
    assert.equal(dialog.open, true, 'the dialog stays open so the reason can be read');
    assert.equal(cancel.disabled, false);
    assert.equal(go.disabled, false);
    assert.equal(f.submitCalls().length, 1, 'no automatic retry after a failed upload');
  } finally { f.close(); }
});

test('an oversized image is refused before anything is uploaded', async () => {
  const reason = '提交前检查未通过：images/upload-9f3a.png：6.0 MB 超过单张 5 MB 上限';
  const f = fixture({ rejectSubmit: reason });
  try {
    await f.mount();
    const dialog = await openDialog(f);
    const box = dialog.querySelector('input');
    box.checked = true; box.dispatchEvent(new f.w.Event('change'));
    dialog.querySelector('[data-go]').click();
    await tick();
    assert.match(dialog.querySelector('.buffer-dialog-error').textContent, /6\.0 MB 超过单张 5 MB 上限/);
    assert.deepEqual(f.messages, []);
  } finally { f.close(); }
});

test('the image host is asked once and re-checking the channel does not re-ask', async () => {
  const f = fixture();
  try {
    await f.mount();
    f.w.document.querySelector('#buffer-recheck').click();
    await tick();
    assert.equal(f.calls.filter(call => call.path.startsWith('/api/buffer/channels/')).length, 2);
    assert.equal(f.mediaCalls().length, 1);
  } finally { f.close(); }
});

test('while the channel check is running the panel is not actionable and nothing uploads', async () => {
  const f = fixture({ deferCheck: true });
  try {
    const mounted = f.mount();
    assert.equal(f.w.document.querySelector('#buffer-draft').disabled, true);
    assert.equal(f.w.document.querySelector('#buffer-publish').disabled, true);
    assert.deepEqual(f.paths().filter(path => path.startsWith('/api/buffer/submit/')), []);
    f.finishCheck();
    await mounted;
    assert.equal(f.w.document.querySelector('#buffer-draft').disabled, false);
  } finally { f.close(); }
});
