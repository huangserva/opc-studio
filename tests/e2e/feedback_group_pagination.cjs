// R02 真实浏览器验收：本机合成 40 条旧评论（聚成 30 组）+ 续页 token，点“读取更多”读回 20 个新组，
// 首批新组必须立刻可见（旧码会跳到第 3 页，要翻回上一页才看得到）。
// 单独运行：node tests/e2e/feedback_group_pagination.cjs [证据目录]
// 依赖离线：真实 Chrome（Playwright）+ 本机静态服务 + 合成回执，全程不连 X、不连外网。
const fs = require('node:fs'), http = require('node:http'), path = require('node:path'), assert = require('node:assert/strict');
const ROOT = path.resolve(__dirname, '../..');
process.chdir(ROOT);
const OUT = process.argv[2] || path.join(ROOT, 'coordination/evidence/e2e', 'feedback-group-pagination');
fs.mkdirSync(OUT, { recursive: true });

function playwright() {
  for (const mod of [process.env.E2E_PLAYWRIGHT_PATH, 'playwright',
    path.join(__dirname, 'node_modules/playwright'),
    path.join(process.env.HOME, '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright')].filter(Boolean)) {
    try { return require(mod); } catch (e) { if (e.code !== 'MODULE_NOT_FOUND') throw e; }
  }
  throw new Error('缺少 Playwright。先 npm install --prefix tests/e2e，或设 E2E_PLAYWRIGHT_PATH。');
}

const ACCOUNT = 'fixture_user', ROOT_POST = '40001';
const day = hours => new Date(Date.now() - hours * 3600000).toISOString();
const dayKey = d => new Intl.DateTimeFormat('sv-SE', { timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit' }).format(d);
const shifted = n => { const d = new Date(dayKey(new Date()) + 'T00:00:00+08:00'); d.setDate(d.getDate() + n); return dayKey(d); };
const WINDOW = { start_time: new Date(shifted(-6) + 'T00:00:00+08:00').toISOString(),
  end_time: new Date(new Date(dayKey(new Date()) + 'T00:00:00+08:00').getTime() + 86400000).toISOString() };
const reader = (id, hours) => ({ id, text: '读者评论 ' + id, username: 'reader' + id, name: '读者 ' + id,
  author_id: '9' + id, conversation_id: ROOT_POST, parent_id: ROOT_POST, created_at: day(hours),
  metrics: {}, source: 'x', kind: 'post', images: [] });
const mine = (id, parent, hours) => ({ id, text: '我的回复 ' + id, username: ACCOUNT, name: '测试发布账号',
  author_id: '1', conversation_id: ROOT_POST, parent_id: parent, created_at: day(hours),
  metrics: {}, source: 'x', kind: 'post', images: [] });
// 40 条旧评论 → 30 组：30 条读者评论 + 我给前 10 位读者各回一条（回复并进对方的组）。
const oldComments = () => [...Array.from({ length: 30 }, (_, i) => reader('r' + (i + 1), 90 - (i + 1) * 0.3)),
  ...Array.from({ length: 10 }, (_, i) => mine('m' + (i + 1), 'r' + (i + 1), 89 - (i + 1) * 0.3))];
const newGroups = () => Array.from({ length: 20 }, (_, i) => reader('n' + (i + 1), 40 - (i + 1) * 0.3));

const state = { comments: oldComments(), ids: oldComments().map(c => c.id), next: 'token1', syncs: 0 };
const posts = () => [{ id: ROOT_POST, text: '合成原帖', title: '合成原帖', username: ACCOUNT, name: '测试发布账号',
  author_id: '1', conversation_id: ROOT_POST, created_at: day(96), metrics: { reply_count: 60 },
  source: 'x', kind: 'post', images: [] }];
const page = () => ({ ids: state.ids, next_token: state.next, mode: 'discussion', post_id: ROOT_POST, post_kind: '',
  start_time: WINDOW.start_time, end_time: WINDOW.end_time, synced_at: day(1) });
const feedback = () => ({ account: { id: '111', username: ACCOUNT }, connected: true, posts: posts(),
  comments: state.comments, pages: { discussion: page() }, synced_at: day(1) });
const json = (res, body, code = 200) => { res.writeHead(code, { 'Content-Type': 'application/json; charset=utf-8' }); res.end(JSON.stringify(body)); };
const emptyFeedback = () => ({ bindings: [], capabilities: {}, unavailable: 0 });

(async () => {
  const server = http.createServer((req, res) => {
    const url = new URL(req.url, 'http://127.0.0.1');
    if (url.pathname.startsWith('/api/')) {                     // 请求体收完再回合成回执
      const chunks = [];
      req.on('data', chunk => chunks.push(chunk));
      return req.on('end', () => respond(url, res, Buffer.concat(chunks).toString('utf8')));
    }
    serve(url, res);
  });
  function respond(url, res, raw) {
    let body = {};
    try { body = JSON.parse(raw || '{}'); } catch { }
    if (url.pathname === '/api/x-official/feedback') return json(res, feedback());
    if (url.pathname === '/api/x-official/status') return json(res, { account: feedback().account, connected: true, jobs: [] });
    if (url.pathname === '/api/x-official/feedback-sync') {
      const batch = body.next_token ? newGroups() : oldComments();
      state.comments = [...new Map([...state.comments, ...batch].map(c => [c.id, c])).values()];
      state.ids = [...new Set([...state.ids, ...batch.map(c => c.id)])];
      state.next = ''; state.syncs++;
      return json(res, { page: page(), result_count: batch.length, partial: false });
    }
    if (url.pathname === '/api/feedback') return json(res, emptyFeedback());
    if (url.pathname.startsWith('/api/feedback/')) return json(res, url.pathname.includes('snapshots') ? { snapshots: [], total: 0 } : { observations: [], total: 0 });
    if (url.pathname.startsWith('/api/x-official/')) return json(res, { error: '合成验收不提供该 X 接口' }, 404);
    return json(res, { error: '合成验收只读反馈列表' }, 404);
  }
  function serve(url, res) {
    const file = path.join(ROOT, 'public', url.pathname === '/' ? 'feedback.html' : url.pathname);
    if (!file.startsWith(path.join(ROOT, 'public')) || !fs.existsSync(file)) { res.writeHead(404); return res.end('not found'); }
    const types = { '.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8', '.css': 'text/css; charset=utf-8', '.svg': 'image/svg+xml' };
    res.writeHead(200, { 'Content-Type': types[path.extname(file)] || 'application/octet-stream' });
    res.end(fs.readFileSync(file));
  }
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const port = server.address().port;
  assert.notEqual(port, 8766, '验收端口不得是 8766');

  const { chromium } = playwright();
  const browser = await chromium.launch({ channel: process.env.E2E_BROWSER || 'chrome', headless: !process.env.E2E_HEADED });
  const page_ = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  const errors = [];
  page_.on('pageerror', e => errors.push(String(e)));
  const steps = [];
  await page_.goto('http://127.0.0.1:' + port + '/feedback.html');
  await page_.locator('[data-post="' + ROOT_POST + '"]').waitFor();
  await page_.locator('[data-post="' + ROOT_POST + '"]').click();
  await page_.selectOption('#xf-sort', 'earliest');
  steps.push(['起点（40 条旧评论聚成 30 组）', await page_.locator('#xf-reply-page').innerText()]);
  assert.match(await page_.locator('#xf-reply-page').innerText(), /^1 \/ 2 页/);
  // 本机分页先翻完第 2 页，再点一次才是向 X 续页读取
  for (let i = 0; i < 4 && state.syncs === 0; i++) {
    if (await page_.locator('#xf-reply-next').isDisabled()) break;
    await page_.locator('#xf-reply-next').click();
    await page_.waitForTimeout(150);
  }
  await page_.getByText(/本次读取 20 条/).waitFor();
  const after = await page_.locator('#xf-reply-page').innerText();
  steps.push(['续页读回 20 个新组之后', after]);
  await page_.screenshot({ path: path.join(OUT, 'first-batch-visible.png'), fullPage: true });
  const visible = await page_.locator('#xf-replies').innerText();
  assert.equal(state.syncs, 1, '只续页一次');
  assert.match(after, /^2 \/ 3 页 · 已读取 60 条$/, '首批新组应落在第 2 页：' + after);
  assert.ok(visible.includes('读者评论 n1'), '首批新组要立刻看得到');
  await page_.locator('#xf-reply-prev').click();
  const previous = await page_.locator('#xf-reply-page').innerText();
  steps.push(['手动翻回上一页', previous]);
  await page_.screenshot({ path: path.join(OUT, 'previous-page.png'), fullPage: true });
  await browser.close();
  server.close();
  fs.writeFileSync(path.join(OUT, 'result.json'), JSON.stringify({ port, steps, syncs: state.syncs, errors }, null, 2));
  for (const [label, value] of steps) console.log(label + '：' + value);
  console.log('浏览器 pageerror：' + (errors.length ? errors.join(' | ') : '无'));
  console.log('截图：' + OUT);
  assert.deepEqual(errors, [], '页面不能有脚本错误');
  console.log('PASS');
})().catch(error => { console.error(error.message || error); process.exit(1); });