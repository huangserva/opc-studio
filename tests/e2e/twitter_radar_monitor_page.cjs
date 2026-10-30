'use strict';
/**
 * B2-b 雷达页「每小时新增」列 + 值得跟/不值得 + #tweet= 深链 —— 隔离 Playwright 验收。
 * 隔离方式：从 /tmp 副本启动真实 server.py（随机端口 + 临时 workspace），headless Chrome 独立 context，
 *           非本地 origin 一律 abort；不碰 8766、不调用 AIsa、不连飞书、不用 computer use。
 * 运行：RADAR_B2B_REPO=/tmp/viral-b2b-work/repo RADAR_B2B_EVIDENCE=/tmp/viral-b2b-deliver/screenshots \
 *       RADAR_B2B_PYTHON=/Users/huangzongning/development/article-importer/.venv/bin/python \
 *       node tests/e2e/twitter_radar_monitor_page.cjs
 */
const fs = require('fs');
const os = require('os');
const net = require('net');
const path = require('path');
const assert = require('assert/strict');
const { spawn, spawnSync } = require('child_process');
const { chromium } = (() => {
  try { return require('playwright'); } catch (e) {
    return require(path.join(process.env.HOME, '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright'));
  }
})();

const REPO = process.env.RADAR_B2B_REPO || '/tmp/viral-b2b-work/repo';
const EVIDENCE = process.env.RADAR_B2B_EVIDENCE || '/tmp/viral-b2b-deliver/screenshots';
const PYTHON = process.env.RADAR_B2B_PYTHON || '/Users/huangzongning/development/article-importer/.venv/bin/python';
fs.mkdirSync(EVIDENCE, { recursive: true });

function float(text) { return Number(text); }

const results = [];
const pageErrors = [];
const external = [];
let server = null;
let workspace = null;
let port = null;
let context = null;
let browser = null;

function check(id, name, ok, detail) {
  results.push({ id, name, ok: !!ok, detail: detail === undefined ? '' : String(detail) });
  console.log((ok ? '  ✅ ' : '  ❌ ') + id + ' ' + name + (detail ? ' → ' + String(detail).replace(/\s+/g, ' ').slice(0, 200) : ''));
}

function freePort() {
  return new Promise((resolve, reject) => {
    const socket = net.createServer();
    socket.on('error', reject);
    socket.listen(0, '127.0.0.1', () => {
      const value = socket.address().port;
      socket.close(() => resolve(value));
    });
  });
}

const BUNDLE_PATH = path.join(REPO, 'public/twitter-radar-bundle.json');
let bundleBackup = null;

function installFixtureBundle(payload) {
  if (bundleBackup === null) {
    bundleBackup = fs.existsSync(BUNDLE_PATH) ? fs.readFileSync(BUNDLE_PATH) : null;
  }
  fs.writeFileSync(BUNDLE_PATH, JSON.stringify(payload, null, 2), 'utf8');
}

function restoreFixtureBundle() {
  if (bundleBackup === null) return;
  if (bundleBackup === null) { try { fs.unlinkSync(BUNDLE_PATH); } catch (e) {} }
  else fs.writeFileSync(BUNDLE_PATH, bundleBackup);
  bundleBackup = null;
}

async function startServer() {
  workspace = fs.mkdtempSync(path.join(os.tmpdir(), 'b2b-ws-'));
  port = await freePort();
  server = spawn(PYTHON, ['server.py', '--workspace', workspace, '--port', String(port)],
                 { cwd: REPO, stdio: ['ignore', 'pipe', 'pipe'] });
  let out = '';
  await new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error('server 启动超时: ' + out.slice(-400))), 40000);
    server.stdout.on('data', (chunk) => {
      out += chunk;
      if (out.includes('图文工作台：http://127.0.0.1:' + port)) { clearTimeout(timer); resolve(); }
    });
    server.once('exit', (code) => { clearTimeout(timer); reject(new Error('server 退出 ' + code + ': ' + out.slice(-400))); });
  });
  assert.notEqual(port, 8766, '不得使用正式端口');
  const monitor = path.join(workspace, 'twitter-radar/monitor');
  fs.mkdirSync(monitor, { recursive: true });
  return monitor;
}

function monitorDir() { return path.join(workspace, 'twitter-radar/monitor'); }

function writeHourly(payload) {
  fs.mkdirSync(monitorDir(), { recursive: true });
  if (payload === null) { try { fs.unlinkSync(path.join(monitorDir(), 'page-hourly.json')); } catch (e) {} return; }
  fs.writeFileSync(path.join(monitorDir(), 'page-hourly.json'),
                   typeof payload === 'string' ? payload : JSON.stringify(payload, null, 2), 'utf8');
}

function hoursAgoIso(hours) { return new Date(Date.now() - hours * 3600000).toISOString(); }

function viralTweet(over) {
  const hours = over && over.hours !== undefined ? over.hours : 2;
  return Object.assign({
    tweet_id: (over && over.tweet_id) || '2110333000000000001',
    user: 'vip_one', name: '特关一号', fc: 42000, views: 120000, likes: 900, retweets: 40, replies: 12,
    ratio: 5.5, track: 'ai_models', track_label: '🤖 AI 模型', tier_badge: '💥 100K+', tier_class: 'tier-100k',
    text: '监控联调正文：把 Agent 流水线跑通', published_at: hoursAgoIso(hours), fetched_at: hoursAgoIso(0.05),
    is_stale: false
  }, over || {});
}

async function apiPost(headers, body) {
  // 用 data 让 Playwright 自己序列化并补 Content-Length（body 字符串会变成分块编码，服务器读不到长度）。
  const payload = typeof body === 'string' ? body : JSON.stringify(body);
  const requestHeaders = Object.assign({ 'Content-Type': 'application/json' }, headers || {});
  if (requestHeaders.Origin) requestHeaders.Origin = requestHeaders.Origin.replace('PORT', String(port));
  requestHeaders['Content-Length'] = String(Buffer.byteLength(payload));
  return context.request.post('http://127.0.0.1:' + port + '/api/radar/feedback', {
    headers: requestHeaders, data: payload
  });
}

(async () => {
  console.log('=== B2-b 页面联调（副本 ' + REPO + '）===');
  await startServer();
  browser = await chromium.launch({ channel: 'chrome', headless: true, args: ['--disable-background-networking', '--disable-component-update'] });
  context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
  const base = 'http://127.0.0.1:' + port;
  await context.route('**/*', (route) => {
    const url = new URL(route.request().url());
    if (url.origin !== base) { external.push(url.origin); return route.abort(); }
    return route.continue();
  });
  const page = await context.newPage();
  page.on('pageerror', (error) => pageErrors.push(String(error && error.message || error)));
  const shot = async (name) => page.screenshot({ path: path.join(EVIDENCE, name), fullPage: true });

  // 夹具：3 条特关帖子，监控数据里分别给不同数值形态
  const bundle = {
    updated_at: hoursAgoIso(0.2), run_id: 'b2b-run', coverage: { target: 51, success: 51, failed: 0, complete: true },
    today_virals: 3, h48_virals: 3,
    tweets: [],
    vip_tweets: [
      viralTweet({ tweet_id: '2110333000000000001', user: 'vip_one', name: '特关一号', hours: 2, views: 120000, likes: 900 }),
      viralTweet({ tweet_id: '2110333000000000002', user: 'vip_two', name: '特关二号', hours: 4, views: 60000, likes: 400 }),
      viralTweet({ tweet_id: '2110333000000000003', user: 'vip_three', name: '特关三号', hours: 6, views: 90000, likes: 700 })
    ],
    vip_bloggers: [{ handle: 'vip_one', name: '特关一号', fc: 42000 }, { handle: 'vip_two', name: '特关二号', fc: 30000 },
                   { handle: 'vip_three', name: '特关三号', fc: 21000 }],
    topic_clusters: []
  };
  const hourly = {
    schema: 1, run_id: 'b2b-run', generated_at: hoursAgoIso(0.1),
    tweets: {
      // 注意：tweet_id 必须用字符串键，19 位数字字面量在 JS 里会丢精度。
      '2110333000000000001': { hourly_delta: 12345, views_delta: 12345, interval_hours: 1, views: 120000, fetched_at: hoursAgoIso(0.05), is_stale: false, hit: 'lowfan_burst', pushed: true },
      '2110333000000000002': { hourly_delta: null, views_delta: null, interval_hours: null, views: 60000, fetched_at: hoursAgoIso(0.05), is_stale: false, hit: null, pushed: false },
      '2110333000000000003': { hourly_delta: -12, views_delta: null, interval_hours: null, views: 90000, fetched_at: hoursAgoIso(0.05), is_stale: false, hit: 'made_up_type', pushed: false }
    }
  };
  installFixtureBundle(bundle);            // 页面读服务器 public 下的 bundle，换成夹具以便断言具体帖子

  async function loadVipView() {
    await page.goto(base + '/twitter-radar.html', { waitUntil: 'load' });
    await page.waitForFunction(() => typeof DATA_SOURCE !== 'undefined' && DATA_SOURCE.kind === 'json');
    await page.click('#tab-vip-btn');
    await page.waitForSelector('.vip-tweet-card .tweet-monitor-row');
  }

  // ---------------- 反例 1：来源校验 ----------------
  {
    const feedbackFile = path.join(monitorDir(), 'feedback.json');
    const before = fs.existsSync(feedbackFile) ? fs.readFileSync(feedbackFile, 'utf8') : null;
    const noHeader = await apiPost({}, JSON.stringify({ tweet_id: '1', verdict: 'worth' }));
    const badOrigin = await apiPost({ 'X-Article-Studio': '1', Origin: 'http://evil.example' }, JSON.stringify({ tweet_id: '1', verdict: 'worth' }));
    const badOrigin2 = await apiPost({ 'X-Article-Studio': '1', Origin: 'http://127.0.0.1:9' }, JSON.stringify({ tweet_id: '1', verdict: 'worth' }));
    const wrongHeader = await apiPost({ 'X-Article-Studio': '0', Origin: 'http://127.0.0.1:PORT' }, JSON.stringify({ tweet_id: '1', verdict: 'worth' }));
    const foreignHost = await context.request.post('http://127.0.0.1:' + port + '/api/radar/feedback', {
      headers: { 'Content-Type': 'application/json', Host: 'example.com', 'X-Article-Studio': '1',
                 Origin: 'http://127.0.0.1:' + port, 'Content-Length': '30' },
      data: { tweet_id: '1', verdict: 'worth' }
    }).catch(() => ({ status: () => 0 }));
    const after = fs.existsSync(feedbackFile) ? fs.readFileSync(feedbackFile, 'utf8') : null;
    // 两条既有 POST 路由对照：同样必须先过来源校验
    const other1 = await context.request.post(base + '/api/collect/aisa/run', {
      headers: { 'Content-Type': 'application/json' }, data: { source: 'twitter' }
    });
    const other2 = await context.request.post(base + '/api/intake/drop', {
      headers: { 'Content-Type': 'application/json' }, data: { title: 'x' }
    });
    check('反例1', '缺 header / 错 Origin / 非本机 → 403，反馈文件不变；既有路由校验一致',
      noHeader.status() === 403 && badOrigin.status() === 403 && badOrigin2.status() === 403 &&
      wrongHeader.status() === 403 && Number(foreignHost.status()) !== 200 &&
      before === after && other1.status() === 403 && other2.status() === 403,
      '无 header=' + noHeader.status() + ' 错 Origin=' + badOrigin.status() + ' 异端口 Origin=' + badOrigin2.status() +
      ' header=0 → ' + wrongHeader.status() + ' 异 Host=' + foreignHost.status() +
      ' ｜既有路由 aisa/run=' + other1.status() + ' intake/drop=' + other2.status() +
      ' ｜文件未变=' + (before === after));
  }

  // ---------------- 反例 2：非法 payload 一律 400 ----------------
  {
    const bad = [
      { tweet_id: '12a', verdict: 'worth' }, { tweet_id: '../x', verdict: 'worth' },
      { tweet_id: 12345, verdict: 'worth' }, { tweet_id: '1'.repeat(30), verdict: 'worth' },
      { tweet_id: '', verdict: 'worth' }, { tweet_id: '1', verdict: 'WORTH' },
      { tweet_id: '1', verdict: 'yes' }, { tweet_id: '1', verdict: ['worth'] },
      { tweet_id: '1', verdict: 'worth', extra: 1 }
    ];
    const feedbackFile = path.join(monitorDir(), 'feedback.json');
    const before = fs.existsSync(feedbackFile) ? fs.readFileSync(feedbackFile, 'utf8') : null;
    const codes = [];
    for (const payload of bad) codes.push((await apiPost({ 'X-Article-Studio': '1', Origin: 'http://127.0.0.1:PORT' }, JSON.stringify(payload))).status());
    const notJson = await apiPost({ 'X-Article-Studio': '1', Origin: 'http://127.0.0.1:PORT' }, 'not json at all');
    const huge = await apiPost({ 'X-Article-Studio': '1', Origin: 'http://127.0.0.1:PORT' }, JSON.stringify({ tweet_id: '1', verdict: 'worth', pad: 'x'.repeat(40000) }));
    const after = fs.existsSync(feedbackFile) ? fs.readFileSync(feedbackFile, 'utf8') : null;
    check('反例2', '9 类非法 payload + 非 JSON + 超大请求体 → 全部 400，文件不变',
      codes.every((code) => code === 400) && notJson.status() === 400 && (huge.status() === 400 || huge.status() === 413) && before === after,
      '状态码=' + JSON.stringify(codes) + ' 非 JSON=' + notJson.status() + ' 超大=' + huge.status() + ' 文件未变=' + (before === after));
  }

  // ---------------- 反例 3：连点 5 次 ----------------
  {
    writeHourly(hourly);
    fs.writeFileSync(path.join(monitorDir(), 'feedback.json'), '', 'utf8');
    try { fs.unlinkSync(path.join(monitorDir(), 'feedback.json')); } catch (e) {}
    await loadVipView();
    const button = page.locator('.vip-tweet-card[data-tweet-id="2110333000000000001"] .verdict-worth');
    for (let index = 0; index < 5; index++) {
      const done = page.waitForResponse((response) => response.url().includes('/api/radar/feedback') && response.request().method() === 'POST');
      await button.click();
      await done;
      await page.waitForTimeout(60);
    }
    const active = await button.getAttribute('aria-pressed');
    const stored = JSON.parse(fs.readFileSync(path.join(monitorDir(), 'feedback.json'), 'utf8'));
    const uiActive = await page.locator('.vip-tweet-card[data-tweet-id="2110333000000000001"] .verdict-worth.active').count();
    check('反例3', '同一帖连点「值得跟」5 次 → 奇数次为选中，文件只有 1 条，界面与文件一致',
      active === 'true' && uiActive === 1 && Object.keys(stored).length === 1 && stored['2110333000000000001'].verdict === 'worth',
      'aria-pressed=' + active + ' 高亮按钮数=' + uiActive + ' 文件=' + JSON.stringify(stored));
    await shot('01-按钮选中.png');
  }

  // ---------------- 反例 4：值得跟 → 不值得 ----------------
  {
    const notWorth = page.locator('.vip-tweet-card[data-tweet-id="2110333000000000001"] .verdict-not_worth');
    const done = page.waitForResponse((response) => response.url().includes('/api/radar/feedback'));
    await notWorth.click();
    await done;
    await page.waitForTimeout(120);
    const stored = JSON.parse(fs.readFileSync(path.join(monitorDir(), 'feedback.json'), 'utf8'));
    const worthActive = await page.locator('.vip-tweet-card[data-tweet-id="2110333000000000001"] .verdict-worth.active').count();
    const notWorthActive = await page.locator('.vip-tweet-card[data-tweet-id="2110333000000000001"] .verdict-not_worth.active').count();
    check('反例4', '「值得跟」→「不值得」切换后文件里只剩 not_worth，界面同步',
      Object.keys(stored).length === 1 && stored['2110333000000000001'].verdict === 'not_worth' &&
      worthActive === 0 && notWorthActive === 1,
      '文件=' + JSON.stringify(stored) + ' 值得跟高亮=' + worthActive + ' 不值得高亮=' + notWorthActive);
  }

  // ---------------- 反例 5：POST 失败必须恢复 ----------------
  {
    await page.route('**/api/radar/feedback', (route) => route.fulfill({ status: 500, contentType: 'application/json', body: '{"error":"模拟故障"}' }));
    const button = page.locator('.vip-tweet-card[data-tweet-id="2110333000000000002"] .verdict-worth');
    await button.click();
    await page.waitForSelector('#radar-toast-container .radar-toast.warning');
    const toastText = await page.locator('#radar-toast-container .radar-toast.warning').innerText();
    const activeAfterFail = await button.getAttribute('aria-pressed');
    const storedAfterFail = JSON.parse(fs.readFileSync(path.join(monitorDir(), 'feedback.json'), 'utf8'));
    await page.unroute('**/api/radar/feedback');

    await page.route('**/api/radar/feedback', (route) => route.abort('connectionfailed'));
    const button2 = page.locator('.vip-tweet-card[data-tweet-id="2110333000000000003"] .verdict-worth');
    await button2.click();
    await page.waitForSelector('#radar-toast-container .radar-toast.warning');
    const activeAfterAbort = await button2.getAttribute('aria-pressed');
    await page.unroute('**/api/radar/feedback');
    const storedFinal = JSON.parse(fs.readFileSync(path.join(monitorDir(), 'feedback.json'), 'utf8'));
    await shot('02-失败恢复.png');
    check('反例5', '500 与断网 → 提示失败、按钮恢复原状态、文件不被改成已保存',
      toastText.includes('保存失败') && activeAfterFail === 'false' && activeAfterAbort === 'false' &&
      !storedAfterFail['2110333000000000002'] && !storedFinal['2110333000000000003'],
      '提示=' + toastText.replace(/\s+/g, ' ').slice(0, 60) + ' 500 后 aria-pressed=' + activeAfterFail +
      ' 断网后 aria-pressed=' + activeAfterAbort + ' 文件键=' + JSON.stringify(Object.keys(storedFinal)));
  }

  // ---------------- 反例 6：并发写两条不同帖 ----------------
  {
    writeHourly(hourly);
    await loadVipView();
    const responses = [];
    const watch = (response) => { if (response.url().includes('/api/radar/feedback')) responses.push(response.status()); };
    page.on('response', watch);
    // 同一事件轮里对两个真实按钮派发 click（后端线程级并发由 tests/test_radar_page_api.py 单独覆盖）
    await page.evaluate(() => {
      const first = document.querySelector('.vip-tweet-card[data-tweet-id="2110333000000000002"] .verdict-worth');
      const second = document.querySelector('.vip-tweet-card[data-tweet-id="2110333000000000003"] .verdict-not_worth');
      first.click();
      second.click();
    });
    await page.waitForTimeout(800);
    page.off('response', watch);
    const stored = JSON.parse(fs.readFileSync(path.join(monitorDir(), 'feedback.json'), 'utf8'));
    check('反例6', '同时点两条不同帖 → 两条都在，文件是合法 JSON',
      stored['2110333000000000002'] && stored['2110333000000000003'] && responses.length === 2 &&
      responses.every((code) => code === 200),
      '文件=' + JSON.stringify(stored).slice(0, 160) + ' 响应=' + JSON.stringify(responses));
  }

  // ---------------- 反例 7：监控文件四种状态 ----------------
  {
    const states = [];
    writeHourly(null);
    await loadVipView();
    states.push(['missing', await page.locator('.tweet-monitor-value').first().innerText()]);
    await shot('03-监控未运行.png');
    writeHourly('{ broken json');
    await loadVipView();
    states.push(['invalid-broken', await page.locator('.tweet-monitor-value').first().innerText()]);
    await shot('04-监控数据异常.png');
    const badSchema = Object.assign({}, hourly, { schema: 2 });
    writeHourly(badSchema);
    await loadVipView();
    states.push(['invalid-schema', await page.locator('.tweet-monitor-value').first().innerText()]);
    writeHourly(Object.assign({}, hourly, { generated_at: hoursAgoIso(3) }));
    await loadVipView();
    states.push(['stale', await page.locator('.tweet-monitor-value').first().innerText()]);
    await shot('05-数据已过期.png');
    const pageText = await page.locator('#vip-view-section').innerText();
    const hasFakeNumber = /每小时新增\s*\+?[\d]/.test(pageText.replace(/每小时新增(监控未运行|监控数据异常|数据已过期（\d+ 小时前）)/g, ''));
    check('反例7', '文件不存在/损坏/schema=2/3 小时前 → 四种中文提示，列里没有假数字',
      states[0][1] === '监控未运行' && states[1][1] === '监控数据异常' && states[2][1] === '监控数据异常' &&
      /^数据已过期（\d+ 小时前）$/.test(states[3][1]) && !hasFakeNumber,
      JSON.stringify(states) + ' 残留数字=' + hasFakeNumber);
  }

  // ---------------- 反例 5：推特页 150 分钟过期边界（真实页面） ----------------
  {
    writeHourly(Object.assign({}, hourly, { generated_at: hoursAgoIso(2 + 10 / 60) }));
    await loadVipView();
    const okCell = await page.locator('.tweet-monitor-value').first().innerText();
    writeHourly(Object.assign({}, hourly, { generated_at: hoursAgoIso(2 + 31 / 60) }));
    await loadVipView();
    const staleCell = await page.locator('.tweet-monitor-value').first().innerText();
    check('反例5-推特阈值', 'generated_at 2 小时 10 分 → ok（显示增量），2 小时 31 分 → 数据已过期',
      okCell === '+1.2万/h' && /^数据已过期（\d+ 小时前）$/.test(staleCell),
      '2h10m=' + okCell + ' ｜ 2h31m=' + staleCell);
    await shot('05b-150分钟边界.png');
    writeHourly(hourly);
  }

  // ---------------- 反例 8/9：数值形态与未知 hit ----------------
  {
    const extended = {
      schema: 1, run_id: 'b2b-run', generated_at: hoursAgoIso(0.1),
      tweets: {
        '2110333000000000001': { hourly_delta: 12345, views: 120000, fetched_at: hoursAgoIso(0.05), is_stale: false, hit: 'lowfan_burst' },
        '2110333000000000002': { hourly_delta: null, views: 60000, fetched_at: hoursAgoIso(0.05), is_stale: false, hit: null },
        '2110333000000000003': { hourly_delta: 1e12, views: 90000, fetched_at: hoursAgoIso(0.05), is_stale: false, hit: 'made_up_type' }
      }
    };
    writeHourly(extended);
    await loadVipView();
    const values = await page.locator('.tweet-monitor-value').allInnerTexts();
    const hits = await page.locator('.tweet-monitor-hit').allInnerTexts();
    const html = await page.locator('#vip-view-section').innerHTML();
    check('反例8', 'hourly_delta 12345 / null / 1e12 → +1.2万/h、「—」、+100000000000000.0万/h…无 NaN/undefined',
      values[0] === '+1.2万/h' && values[1] === '—' && !values[2].includes('NaN') && !values[2].includes('undefined') &&
      values[2].startsWith('+1.0e+') === false && !/NaN|undefined/.test(html),
      '取值=' + JSON.stringify(values));
    check('反例9', 'hit 为未知字符串 → 不显示徽标、不报错',
      hits.length === 1 && hits[0] === '低粉大爆款' && pageErrors.length === 0,
      '徽标=' + JSON.stringify(hits) + ' pageerror=' + pageErrors.length);
    await shot('06-正常数据.png');
  }

  // ---------------- 反例 10：#tweet= 深链 ----------------
  {
    pageErrors.length = 0;
    await page.goto(base + '/twitter-radar.html#tweet=2110333000000000003', { waitUntil: 'load' });
    await page.waitForFunction(() => typeof DATA_SOURCE !== 'undefined' && DATA_SOURCE.kind === 'json');
    await page.click('#tab-vip-btn');
    await page.waitForTimeout(400);
    const focused = await page.locator('.vip-tweet-card.tweet-focus').count();
    const focusedId = focused ? await page.locator('.vip-tweet-card.tweet-focus').getAttribute('data-tweet-id') : null;
    await page.goto(base + '/twitter-radar.html#tweet=abc', { waitUntil: 'load' });
    await page.waitForTimeout(300);
    await page.goto(base + '/twitter-radar.html#tweet=' + encodeURIComponent('<img src=x onerror=alert(1)>'), { waitUntil: 'load' });
    await page.waitForTimeout(400);
    const injected = await page.locator('#vip-view-section img').count();
    const focusedBad = await page.locator('.tweet-focus').count();
    check('反例10', '#tweet=<数字> 滚动高亮；#tweet=abc 与 <img onerror> 忽略且无 pageerror',
      focused >= 1 && focusedId === '2110333000000000003' && injected === 0 && focusedBad === 0 && pageErrors.length === 0,
      '高亮=' + focused + ' 目标=' + focusedId + ' 注入 img=' + injected + ' 非法 hash 高亮=' + focusedBad + ' pageerror=' + pageErrors.length);
    await shot('07-深链高亮.png');
  }

  // ---------------- 反例 11：监控接口 500 时 bundle 照常 ----------------
  {
    pageErrors.length = 0;
    await page.route('**/api/radar/monitor', (route) => route.fulfill({ status: 500, contentType: 'application/json', body: '{"error":"boom"}' }));
    await page.goto(base + '/twitter-radar.html', { waitUntil: 'load' });
    await page.waitForFunction(() => typeof DATA_SOURCE !== 'undefined' && DATA_SOURCE.kind === 'json');
    await page.click('#tab-vip-btn');
    await page.waitForSelector('.vip-tweet-card');
    const cards = await page.locator('.vip-tweet-card').count();
    const hint = await page.locator('.tweet-monitor-value').first().innerText();
    const bundleOk = await page.locator('#radar-read-receipt').innerText();
    await page.unroute('**/api/radar/monitor');
    check('反例11', '/api/radar/monitor 500 → bundle 照常渲染，列显示「监控数据异常」',
      cards >= 1 && hint === '监控数据异常' && bundleOk.includes('b2b-run') && pageErrors.length === 0,
      '特关卡=' + cards + ' 列提示=' + hint + ' 回执含 run_id=' + bundleOk.includes('b2b-run'));
  }

  // ---------------- N1 慢请求（3 秒）期间连点 3 次不同按钮 ----------------
  {
    writeHourly(hourly);
    try { fs.unlinkSync(path.join(monitorDir(), 'feedback.json')); } catch (e) {}
    await loadVipView();
    await page.route('**/api/radar/feedback', async (route) => {
      await new Promise((resolve) => setTimeout(resolve, 3000));
      await route.continue();
    });
    let posts = 0;
    const counter = (request) => { if (request.url().includes('/api/radar/feedback')) posts++; };
    page.on('request', counter);
    const cardSel = '.vip-tweet-card[data-tweet-id="2110333000000000001"]';
    await page.locator(cardSel + ' .verdict-worth').click();
    await page.waitForTimeout(120);
    await page.locator(cardSel + ' .verdict-not_worth').click();
    await page.waitForTimeout(120);
    await page.locator(cardSel + ' .verdict-worth').click();
    await page.waitForFunction(() => [...document.querySelectorAll('.verdict-btn')].every((b) => b.dataset.busy !== '1'), null, { timeout: 20000 });
    page.off('request', counter);
    await page.unroute('**/api/radar/feedback');
    const stored = JSON.parse(fs.readFileSync(path.join(monitorDir(), 'feedback.json'), 'utf8'));
    const worthPressed = await page.locator(cardSel + ' .verdict-worth').getAttribute('aria-pressed');
    const notWorthPressed = await page.locator(cardSel + ' .verdict-not_worth').getAttribute('aria-pressed');
    check('N1', '3 秒慢请求期间连点 3 次不同按钮 → 只发最后一次意图，最终界面=服务端写入',
      stored['2110333000000000001'] && stored['2110333000000000001'].verdict === 'worth' &&
      worthPressed === 'true' && notWorthPressed === 'false' && posts === 1,
      '发出请求数=' + posts + ' 文件 verdict=' + (stored['2110333000000000001'] || {}).verdict +
      ' 值得跟=' + worthPressed + ' 不值得=' + notWorthPressed);
    await shot('08-慢请求连点.png');
  }

  // ---------------- N2 请求失败后再点 ----------------
  {
    try { fs.unlinkSync(path.join(monitorDir(), 'feedback.json')); } catch (e) {}
    await loadVipView();
    const cardSel = '.vip-tweet-card[data-tweet-id="2110333000000000002"]';
    let attempts = 0;
    await page.route('**/api/radar/feedback', (route) => {
      attempts++;
      if (attempts === 1) return route.fulfill({ status: 500, contentType: 'application/json', body: '{"error":"第一次故意失败"}' });
      return route.continue();
    });
    await page.locator(cardSel + ' .verdict-worth').click();
    await page.waitForSelector('#radar-toast-container .radar-toast.warning');
    const afterFail = await page.locator(cardSel + ' .verdict-worth').getAttribute('aria-pressed');
    const fileAfterFail = fs.existsSync(path.join(monitorDir(), 'feedback.json'));
    await page.locator(cardSel + ' .verdict-worth').click();
    await page.waitForSelector('#radar-toast-container .radar-toast.success');
    await page.waitForFunction(() => [...document.querySelectorAll('.verdict-btn')].every((b) => b.dataset.busy !== '1'));
    await page.unroute('**/api/radar/feedback');
    const stored = JSON.parse(fs.readFileSync(path.join(monitorDir(), 'feedback.json'), 'utf8'));
    const afterRetry = await page.locator(cardSel + ' .verdict-worth').getAttribute('aria-pressed');
    check('N2', '第一次失败提示并回退 → 再点一次成功保存，界面最终为已保存',
      afterFail === 'false' && !fileAfterFail && stored['2110333000000000002'].verdict === 'worth' && afterRetry === 'true',
      '失败后 aria=' + afterFail + ' 失败后文件存在=' + fileAfterFail + ' 重试后文件=' + JSON.stringify(stored['2110333000000000002']) +
      ' 重试后 aria=' + afterRetry);
    await shot('09-失败后再点.png');
  }

  // ---------------- N3 两标签页交替操作后各自点「读取」 ----------------
  {
    writeHourly(hourly);
    await loadVipView();
    const second = await context.newPage();
    second.on('pageerror', (error) => pageErrors.push(String(error && error.message || error)));
    const cardSel = '.vip-tweet-card[data-tweet-id="2110333000000000003"]';
    await page.locator(cardSel + ' .verdict-worth').click();
    await page.waitForFunction(() => [...document.querySelectorAll('.verdict-btn')].every((b) => b.dataset.busy !== '1'));
    await second.goto(base + '/twitter-radar.html', { waitUntil: 'load' });
    await second.waitForFunction(() => typeof DATA_SOURCE !== 'undefined' && DATA_SOURCE.kind === 'json');
    await second.click('#tab-vip-btn');
    await second.waitForSelector('.vip-tweet-card .verdict-btn');
    const staleBefore = await second.locator(cardSel + ' .verdict-worth').getAttribute('aria-pressed');
    await second.locator(cardSel + ' .verdict-not_worth').click();
    await second.waitForFunction(() => [...document.querySelectorAll('.verdict-btn')].every((b) => b.dataset.busy !== '1'));
    // 第一个标签页还没同步：点「读取最新数据」后必须跟着服务端走
    const staleBeforeRefresh = await page.locator(cardSel + ' .verdict-worth').getAttribute('aria-pressed');
    for (const target of [page, second]) {
      const done = target.waitForResponse((response) => response.url().includes('/api/radar/monitor'));
      await target.locator('#btn-refresh-radar').click();
      await done;
      await target.waitForTimeout(200);
    }
    const firstAfter = await page.locator(cardSel + ' .verdict-worth').getAttribute('aria-pressed');
    const firstNotWorth = await page.locator(cardSel + ' .verdict-not_worth').getAttribute('aria-pressed');
    const secondAfter = await second.locator(cardSel + ' .verdict-worth').getAttribute('aria-pressed');
    const secondNotWorth = await second.locator(cardSel + ' .verdict-not_worth').getAttribute('aria-pressed');
    await second.close();
    await shot('10-双标签读取后.png');
    check('N3', '两个标签页交替写入后各自点「读取」→ 按钮与服务端一致',
      staleBefore === 'true' && staleBeforeRefresh === 'true' &&
      firstAfter === 'false' && firstNotWorth === 'true' && secondAfter === 'false' && secondNotWorth === 'true',
      '第二标签页加载后 aria=' + staleBefore + '（应已看到 worth） 第二标签写入 not_worth 后第一标签仍显示 worth=' + staleBeforeRefresh +
      ' ｜读取后 第一标签 值得=' + firstAfter + '/不值得=' + firstNotWorth +
      ' 第二标签 值得=' + secondAfter + '/不值得=' + secondNotWorth);
  }

  // ---------------- N4 深链在 bundle 读取失败时 ----------------
  {
    pageErrors.length = 0;
    void 0;
    let intercepted = 0;
    const bundleMatcher = (url) => url.pathname.endsWith('twitter-radar-bundle.json');
    await page.route(bundleMatcher, (route) => {
      intercepted++;
      return route.fulfill({ status: 500, contentType: 'text/plain', body: 'boom' });
    });
    // 加一个查询参数，确保是真导航（只改 hash 是同文档跳转，不会重新读 bundle）
    await page.goto(base + '/twitter-radar.html?b2b2=' + Date.now() + '#tweet=2110333000000000001', { waitUntil: 'load' });
    try {
      await page.waitForSelector('#radar-toast-container .radar-toast.warning', { timeout: 15000 });
    } catch (e) {
      const dump = await page.evaluate(() => ({
        toasts: [...document.querySelectorAll('.radar-toast')].map((n) => n.className + '|' + n.innerText.replace(/\s+/g, ' ')),
        dataSource: typeof DATA_SOURCE !== 'undefined' ? JSON.stringify(DATA_SOURCE) : 'undef',
        hash: window.location.hash,
        monitorStatus: typeof monitorState !== 'undefined' ? monitorState.status : 'undef',
        intercepted: window.__interceptedBundleRequests || 0
      }));
      check('N4', '深链 + bundle 读取失败 → 提示「该帖不在当前雷达数据中」，无 pageerror', false, JSON.stringify(dump));
    }
    const toasts = await page.locator('#radar-toast-container .radar-toast').allInnerTexts();
    await page.unroute(bundleMatcher);
    const hint = toasts.join(' ').replace(/\s+/g, ' ');
    check('N4', '深链 + bundle 读取失败 → 提示「该帖不在当前雷达数据中」，无 pageerror',
      hint.includes('该帖不在当前雷达数据中') && pageErrors.length === 0,
      '提示=' + hint.slice(0, 120) + ' 拦截 bundle 请求数=' + intercepted + ' pageerror=' + pageErrors.length);
    await shot('11-深链读取失败.png');
  }

  // ---------------- N6 反馈文件损坏 → 409、提示文案、按钮恢复 ----------------
  {
    writeHourly(hourly);
    await loadVipView();
    const feedbackFile = path.join(monitorDir(), 'feedback.json');
    const corrupt = '{"2110333000000000001": {"verdict": "wor';
    fs.writeFileSync(feedbackFile, corrupt, 'utf8');
    const cardSel = '.vip-tweet-card[data-tweet-id="2110333000000000001"]';
    let status = 0;
    const watcher = (response) => { if (response.url().includes('/api/radar/feedback')) status = response.status(); };
    page.on('response', watcher);
    await page.locator(cardSel + ' .verdict-not_worth').click();
    await page.waitForSelector('#radar-toast-container .radar-toast.warning');
    await page.waitForFunction(() => [...document.querySelectorAll('.verdict-btn')].every((b) => b.dataset.busy !== '1'));
    page.off('response', watcher);
    const toastText = (await page.locator('#radar-toast-container .radar-toast.warning').innerText()).replace(/\s+/g, ' ');
    const afterPressed = await page.locator(cardSel + ' .verdict-not_worth').getAttribute('aria-pressed');
    const bytesKept = fs.readFileSync(feedbackFile, 'utf8') === corrupt;
    await shot('12-反馈文件损坏.png');
    check('N6', '反馈文件损坏 → 服务端 409、页面提示「反馈文件损坏，已停止写入」、按钮恢复、文件一字节不动',
      status === 409 && toastText.includes('反馈文件损坏') && afterPressed === 'false' && bytesKept,
      'HTTP=' + status + ' 提示=' + toastText.slice(0, 60) + ' 按钮 aria=' + afterPressed + ' 文件未变=' + bytesKept);
  }

  // ---------------- N5 M5 每种坏数值各 1 例 ----------------
  {
    // 直接拼原始 JSON：JSON.stringify 会把 Infinity/NaN 写成 null，测不到「非有限数」这条路径
    const badTokens = ['1e400', '-1e400', '"Infinity"', '"-Infinity"', '"nan"', '"abc"', 'true', '1e20'];
    const results2 = [];
    let allJson = true;
    for (const token of badTokens) {
      writeHourly('{"schema":1,"run_id":"b2b-run","generated_at":"' + hoursAgoIso(0.1) +
                  '","tweets":{"2110333000000000001":{"hourly_delta":' + token + ',"views":100}}}');
      const response = await context.request.get(base + '/api/radar/monitor');
      let body = null;
      try { body = await response.json(); } catch (e) { allJson = false; }
      const tweetCount = body && body.tweets ? Object.keys(body.tweets).length : -1;
      results2.push(token + '→' + response.status() + '/' + (body && body.status) + '/tweets' + tweetCount);
      if (!response.ok() || !body || body.status !== 'invalid' || tweetCount !== 0) allJson = false;
    }
    // 页面展示：最后一个坏数值时列里显示中文提示而不是数字
    await loadVipView();
    const hint = await page.locator('.tweet-monitor-value').first().innerText();
    const html = await page.locator('#vip-view-section').innerHTML();
    check('N5', 'M5 8 种坏数值（inf/nan/字符串 Infinity/超范围/布尔/超长）→ 接口永远返回 JSON 且 status=invalid',
      allJson && hint === '监控数据异常' && !/NaN|Infinity/.test(html),
      JSON.stringify(results2) + ' ｜页面提示=' + hint);
  }

  // ---------------- 反例 13：静态检查 ----------------
  {
    const js = fs.readFileSync(path.join(REPO, 'public/twitter-radar.js'), 'utf8');
    const mine = (js.match(/innerHTML/g) || []).length;
    const baselineDir = process.env.RADAR_B2B_BASELINE || path.join(REPO, '../official-baseline');
    const baselineJs = path.join(baselineDir, 'twitter-radar.js');
    const baselineServer = path.join(baselineDir, 'server.py');
    const hasBaseline = fs.existsSync(baselineJs) && fs.existsSync(baselineServer);
    const base = hasBaseline ? (fs.readFileSync(baselineJs, 'utf8').match(/innerHTML/g) || []).length : null;
    // 无论有没有基线，B2-b 自己的那一段里都不允许出现 HTML 字符串拼接
    const section = js.slice(js.indexOf('B2-b：每小时新增列'), js.indexOf('M4：读取本地 JSON'));
    const sectionClean = section.length > 0 && !/innerHTML/.test(section);
    const serverSource = fs.readFileSync(path.join(REPO, 'server.py'), 'utf8');
    const diffText = hasBaseline
      ? spawnSync('git', ['diff', '--no-index', '--stat', baselineServer, path.join(REPO, 'server.py')], { encoding: 'utf8' }).stdout || ''
      : '';
    check('反例13', '页面新增 0 处 innerHTML 拼接；server.py 只多两条路由',
      sectionClean && (hasBaseline ? mine === base : true) &&
      /radar\/monitor/.test(serverSource) && /'api', 'radar', 'feedback'/.test(serverSource) &&
      (hasBaseline ? !/generation\/lab|lab-test/.test(diffText) : true),
      'B2-b 段落无 HTML 字符串拼接=' + sectionClean +
      (hasBaseline ? ' ｜innerHTML 官方=' + base + ' 本版=' + mine + ' ｜server.py 差异=' + (diffText.match(/\|\s*\d+/) || [''])[0].trim() +
                     ' ｜含 lab 改动=' + /generation\/lab|lab-test/.test(diffText) : ' ｜未提供基线目录，跳过与基线的逐行比对'));
  }

  // B2-b3 additions. All B2-b2 checks above remain unchanged.
  const b3id = '2110333000000000001';
  const b3delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const b3defer = () => { let resolve; const promise = new Promise((r) => { resolve = r; }); return { promise, resolve }; };
  const b3feedbackPath = path.join(monitorDir(), 'feedback.json');
  const b3feedback = () => JSON.parse(fs.readFileSync(b3feedbackPath, 'utf8'));
  function b3reset(value = bundle) {
    installFixtureBundle(value);
    fs.writeFileSync(b3feedbackPath, '{}');
    writeHourly(hourly);
  }
  async function b3fresh(hash = '') {
    const p = await context.newPage();
    p.on('pageerror', (error) => pageErrors.push(String(error)));
    await p.goto(base + '/twitter-radar.html' + hash);
    await p.waitForFunction(() => DATA_SOURCE.kind === 'json' && !document.getElementById('btn-refresh-radar').disabled);
    return p;
  }
  async function b3vip(p) { await p.locator('#tab-vip-btn').click(); await p.locator('.verdict-worth').first().waitFor(); }
  const b3btn = (p, verdict = 'worth') => p.locator('.vip-tweet-card[data-tweet-id="' + b3id + '"] .verdict-' + verdict);
  async function b3idle(p) { await p.waitForFunction(() => [...verdictQueues.values()].every((s) => !s.inFlight && s.desired === s.confirmed)); }
  async function b3state(p) {
    return { file: b3feedback()[b3id]?.verdict ?? null,
      worth: await b3btn(p).getAttribute('aria-pressed'),
      notWorth: await b3btn(p, 'not_worth').getAttribute('aria-pressed') };
  }
  function b3agrees(s) { return s.worth === (s.file === 'worth' ? 'true' : 'false') && s.notWorth === (s.file === 'not_worth' ? 'true' : 'false'); }
  async function b3read(p) {
    await p.waitForFunction(() => !document.getElementById('btn-refresh-radar').disabled);
    await p.locator('#btn-refresh-radar').click();
    await p.waitForFunction(() => !document.getElementById('btn-refresh-radar').disabled);
  }
  async function b3case(id, name, fn) {
    const initial = new Set(context.pages());
    try { const detail = await fn(); check(id, name, true, JSON.stringify(detail)); }
    catch (error) { check(id, name, false, String(error)); }
    finally { for (const p of context.pages()) if (!initial.has(p)) await p.close(); }
  }

  await b3case('B3-four-tabs', '四标签交替改判/撤销，各自读取后均等于文件', async () => {
    b3reset(); const pages = [];
    for (let i = 0; i < 4; i++) { const p = await b3fresh(); await b3vip(p); pages.push(p); }
    for (const [index, verdict] of [[0, 'worth'], [1, 'not_worth'], [2, 'worth'], [3, 'worth'], [1, 'not_worth']]) {
      await b3btn(pages[index], verdict).click(); await b3idle(pages[index]);
      for (const p of pages) await b3read(p);
      for (const p of pages) assert(b3agrees(await b3state(p)), '标签状态与文件不同');
    }
    return Promise.all(pages.map(b3state));
  });

  // G0 captures the old table, P commits worth, G1 captures the new table.
  // All three real responses are delayed >=3s. Exercise all 3! delivery orders.
  const b3orders = [['G0', 'P', 'G1'], ['G0', 'G1', 'P'], ['P', 'G0', 'G1'],
                    ['P', 'G1', 'G0'], ['G1', 'G0', 'P'], ['G1', 'P', 'G0']];
  for (const order of b3orders) await b3case('B3-order-' + order.join('-'), '慢 GET/POST 三回执全部排列', async () => {
    b3reset(); const p = await b3fresh(); await b3vip(p);
    const gates = Object.fromEntries(['G0', 'P', 'G1'].map((tag) => [tag, { ready: b3defer(), release: b3defer() }]));
    let reads = 0, posts = 0;
    await p.evaluate(() => {
      window.b3arrivals = []; let reads = 0; const original = window.fetch.bind(window);
      window.fetch = async (...args) => {
        const url = String(args[0]); const tag = url.includes('/api/radar/monitor') ? 'G' + reads++ : 'P';
        const started = Date.now(), response = await original(...args), parse = response.json.bind(response);
        response.json = async () => { const data = await parse(); window.b3arrivals.push({ tag, ms: Date.now() - started, revision: data.feedback_revision }); return data; };
        return response;
      };
    });
    async function hold(route, tag) {
      const response = await route.fetch(); gates[tag].ready.resolve();
      await gates[tag].release.promise; await route.fulfill({ response }).catch(() => {});
    }
    await p.route('**/api/radar/monitor', (route) => hold(route, 'G' + reads++));
    await p.route('**/api/radar/feedback', (route) => { posts++; return posts === 1 ? hold(route, 'P') : route.continue(); });
    try {
      await p.evaluate(() => { void fetchMonitorView(); }); await gates.G0.ready.promise;
      await b3btn(p).click(); await gates.P.ready.promise;
      await p.evaluate(() => { void fetchMonitorView(); }); await gates.G1.ready.promise;
      await b3delay(3000);
      for (const tag of order) {
        gates[tag].release.resolve();
        await p.waitForFunction((tag) => window.b3arrivals.some((a) => a.tag === tag), tag);
      }
      await b3idle(p); const state = await b3state(p), arrivals = await p.evaluate(() => window.b3arrivals);
      assert.deepEqual(arrivals.map((a) => a.tag), order);
      assert(arrivals.every((a) => a.ms >= 3000), '回执不足3秒');
      assert(state.file === 'worth' && b3agrees(state) && posts === 1, JSON.stringify({ state, posts }));
      fs.writeFileSync(path.join(EVIDENCE, 'B3-order-' + order.join('-') + '.json'), JSON.stringify({ state, arrivals, posts }, null, 2));
      return { state, arrivals, posts };
    } finally { Object.values(gates).forEach((g) => g.release.resolve()); }
  });

  await b3case('B3-inflight-refresh', '真实写入后延迟回执3秒，刷新恢复服务端状态', async () => {
    b3reset(); const p = await b3fresh(); await b3vip(p); const ready = b3defer(), release = b3defer();
    await p.route('**/api/radar/feedback', async (route) => { const response = await route.fetch(); ready.resolve(); await release.promise; await route.fulfill({ response }).catch(() => {}); });
    try {
      await b3btn(p).click(); await ready.promise;
      await p.reload(); await p.waitForFunction(() => DATA_SOURCE.kind === 'json' && !document.getElementById('btn-refresh-radar').disabled);
      await b3delay(3000); release.resolve(); await b3vip(p);
      const state = await b3state(p); assert(state.file === 'worth' && b3agrees(state), JSON.stringify(state)); return state;
    } finally { release.resolve(); }
  });

  const b3feed = viralTweet({ tweet_id: '201', user: 'lowfan_user', fc: 500, views: 4000, likes: 20 });
  const b3blog = viralTweet({ tweet_id: '301', user: 'Beyond_Top', fc: 42000, views: 40000, likes: 20 });
  const b3topic = viralTweet({ tweet_id: '401', user: 'Topic_Only', fc: 42000, views: 3000, likes: 20 });
  const b3bundle = { ...bundle, tweets: [b3feed], vip_tweets: [...bundle.vip_tweets, b3blog],
    topic_clusters: [{ id: 'only-topic', title: '深链话题', embedded_tweets: [b3topic] }] };
  for (const [mode, id] of [['clusters', '401'], ['vip', b3id], ['feed', '201'], ['blogger', '301']]) {
    await b3case('B3-deep-' + mode, '四视图深链自动定位可见目标：' + mode, async () => {
      b3reset(b3bundle); const p = await b3fresh('#tweet=' + id);
      const state = await p.evaluate(() => ({ mode: currentMode, ids: [...document.querySelectorAll('.tweet-focus')].filter((n) => n.getClientRects().length).map((n) => n.dataset.tweetId) }));
      assert.deepEqual(state, { mode, ids: [id] }); const outline = await p.locator('.tweet-focus:visible').evaluate((n) => ({ width: getComputedStyle(n).outlineWidth, style: getComputedStyle(n).outlineStyle })); assert.deepEqual(outline, { width: '3px', style: 'solid' }); await p.screenshot({ path: path.join(EVIDENCE, 'B3-deep-' + mode + '.png'), fullPage: true }); return state;
    });
  }
  await b3case('B3-deep-hidden-hash', '卡片已渲染但视图隐藏，只改hash仍切换可见', async () => {
    b3reset(b3bundle); const p = await b3fresh(); await b3vip(p); await p.locator('#tab-feed-btn').click();
    await p.evaluate((id) => { location.hash = '#tweet=' + id; }, b3id);
    await p.waitForFunction(() => currentMode === 'vip' && [...document.querySelectorAll('.tweet-focus')].some((n) => n.getClientRects().length));
    assert.equal(await p.locator('.tweet-focus:visible').getAttribute('data-tweet-id'), b3id); return { mode: 'vip', visible: 1 };
  });
  await b3case('B3-deep-feed-page2', '低粉榜第二页目标自动定位与分页一致', async () => {
    const filler = Array.from({ length: 35 }, (_, i) => viralTweet({ tweet_id: String(500 + i), user: 'Low_' + i, fc: 300, hours: 1, views: 4000, likes: 20 }));
    b3reset({ ...b3bundle, tweets: [...filler, { ...b3feed, published_at: hoursAgoIso(2) }] });
    const p = await b3fresh('#tweet=201');
    const state = await p.evaluate(() => ({ mode: currentMode, page: currentPage }));
    assert.deepEqual(state, { mode: 'feed', page: 2 }); assert.equal(await p.locator('.tweet-focus:visible').getAttribute('data-tweet-id'), '201'); return state;
  });
  await b3case('B3-deep-vip-48h-filter', '旧筛选和24h窗口不阻碍36h目标定位', async () => {
    const old = viralTweet({ tweet_id: '601', hours: 36, views: 60000, likes: 400 });
    b3reset({ ...b3bundle, vip_tweets: [...bundle.vip_tweets, old] }); const p = await b3fresh(); await b3vip(p);
    await p.locator('.vip-tier-btn[data-tier="200k"]').click(); await p.evaluate(() => { location.hash = '#tweet=601'; });
    await p.waitForFunction(() => !!document.querySelector('.tweet-focus[data-tweet-id="601"]'));
    const state = await p.evaluate(() => ({ mode: currentMode, window: currentVipTime, tier: currentVipTier }));
    assert.deepEqual(state, { mode: 'vip', window: 'all_48h', tier: 'all' }); return state;
  });

  await b3case('B3-deleted-tweet-old-read', '较新的空表不能被旧读取复活，连本地未建队列的帖也覆盖', async () => {
    b3reset(); const a = await b3fresh(); await b3vip(a);
    // A has never seen a feedback entry for this tweet. Another tab creates it.
    const b = await b3fresh(); await b3vip(b); await b3btn(b).click(); await b3idle(b);
    const ready = b3defer(), release = b3defer(); let first = true;
    await a.route('**/api/radar/monitor', async (route) => {
      const response = await route.fetch();
      if (first) { first = false; ready.resolve(); await release.promise; }
      await route.fulfill({ response }).catch(() => {});
    });
    try {
      await a.evaluate(() => { void fetchMonitorView(); }); await ready.promise;
      await b3btn(b).click(); await b3idle(b); // real deletion, no tombstone required
      await a.evaluate(() => fetchMonitorView());
      release.resolve(); await b3delay(100);
      const state = await b3state(a); assert(state.file === null && b3agrees(state), JSON.stringify(state)); return state;
    } finally { release.resolve(); }
  });

  const b3corrupt = [['string', 'broken'], ['array', []], ['null', null], ['nested-array', [[{ broken: [] }]]], ['nested-known-field', { verdict: { deep: [] } }]];
  for (const [name, value] of b3corrupt) await b3case('B3-file-' + name, '反馈坏条目整表拒写且字节/inode不变：' + name, async () => {
    b3reset(); fs.writeFileSync(b3feedbackPath, JSON.stringify({ '202': value, [b3id]: { verdict: 'worth' } }));
    const before = fs.readFileSync(b3feedbackPath), inode = fs.statSync(b3feedbackPath).ino;
    const read = await context.request.get(base + '/api/radar/monitor'); const data = await read.json();
    const response = await apiPost({ Origin: 'http://127.0.0.1:PORT', 'X-Article-Studio': '1' }, { tweet_id: b3id, verdict: 'not_worth' });
    assert.equal(data.feedback_status, 'invalid'); assert.deepEqual(data.feedback, {}); assert.equal(response.status(), 409);
    assert(before.equals(fs.readFileSync(b3feedbackPath))); assert.equal(fs.statSync(b3feedbackPath).ino, inode);
    assert.deepEqual(fs.readdirSync(monitorDir()).filter((n) => n.startsWith('feedback.') && n !== 'feedback.json'), []);
    return { get: read.status(), feedback_status: data.feedback_status, post: response.status(), bytes_same: true, inode_same: true };
  });
  for (const name of ['missing', 'empty-object', 'invalid-verdict']) await b3case('B3-file-' + name, '三种非损坏情况独立验证：' + name, async () => {
    b3reset();
    if (name === 'missing') fs.unlinkSync(b3feedbackPath);
    if (name === 'invalid-verdict') fs.writeFileSync(b3feedbackPath, JSON.stringify({ '202': { verdict: 'YES' } }));
    const read = await context.request.get(base + '/api/radar/monitor'), data = await read.json();
    assert.equal(data.feedback_status, name === 'missing' ? 'missing' : 'ok');
    if (name === 'invalid-verdict') assert.equal(data.feedback['202'].verdict, null);
    const response = await apiPost({ Origin: 'http://127.0.0.1:PORT', 'X-Article-Studio': '1' }, { tweet_id: b3id, verdict: 'worth' });
    assert.equal(response.status(), 200); assert.equal(b3feedback()[b3id].verdict, 'worth');
    return { feedback_status: data.feedback_status, post: response.status() };
  });

  assert.deepEqual(external, [], '存在外网请求: ' + external.slice(0, 3).join(','));
  const failed = results.filter((row) => !row.ok);
  console.log('\n===== B2-b 页面联调汇总 =====');
  console.log('通过 ' + (results.length - failed.length) + '/' + results.length +
              '｜pageerror 累计 ' + pageErrors.length + '｜外网请求 ' + external.length + '｜端口 ' + port);
  fs.writeFileSync(path.join(EVIDENCE, 'results.json'), JSON.stringify({ port, results, pageErrors, external }, null, 2));
  if (failed.length) { console.log('失败项：' + failed.map((row) => row.id).join(', ')); process.exitCode = 1; }
})().catch((error) => { console.error('测试异常：', error); process.exitCode = 1; }).finally(async () => {
  restoreFixtureBundle();
  if (browser) { try { await browser.close(); } catch (e) {} }
  if (server) { try { server.kill('SIGTERM'); } catch (e) {} }
  if (workspace) { try { fs.rmSync(workspace, { recursive: true, force: true }); } catch (e) {} }
});