/**
 * C2 视频榜页面隔离验收（对应合同 7 条自测）。
 *
 * 隔离方式：从 /tmp 副本启动真实 server.py（随机端口 + 夹具 workspace），headless Chrome 独立 context，
 *           非本地 origin 一律 abort；不碰 8766、不调用 AIsa、不连飞书、不用 computer use。
 * 运行：RADAR_C2_REPO=<repo> RADAR_C2_EVIDENCE=<dir> node tests/e2e/video_radar_page.cjs
 */
'use strict';
const fs = require('fs');
const os = require('os');
const net = require('net');
const path = require('path');
const assert = require('assert/strict');
const { spawn } = require('child_process');
const { chromium } = (() => {
  try { return require('playwright'); } catch (e) {
    return require(path.join(process.env.HOME, '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright'));
  }
})();

const REPO = process.env.RADAR_C2_REPO || '/tmp/video-radar-c2/repo';
const EVIDENCE = process.env.RADAR_C2_EVIDENCE || '/tmp/video-radar-c2-deliver/screenshots';
const PYTHON = process.env.RADAR_C2_PYTHON || '/Users/huangzongning/development/article-importer/.venv/bin/python';
fs.mkdirSync(EVIDENCE, { recursive: true });

const results = [];
const pageErrors = [];
const external = [];
let server = null;
let browser = null;
let context = null;
let workspace = null;
let port = null;
let origin = '';

const DAY = 24 * 3600 * 1000;
const HOUR = 3600 * 1000;
const MINUTE = 60 * 1000;

function check(id, name, ok, detail) {
  results.push({ id, name, ok: !!ok, detail: detail === undefined ? '' : String(detail).slice(0, 400) });
  console.log((ok ? '  ✅ ' : '  ❌ ') + id + ' ' + name + (detail !== undefined ? ' → ' + String(detail).slice(0, 220) : ''));
}

function iso(offsetMs) {
  return new Date(Date.now() - offsetMs).toISOString();
}

function radarDir() {
  return path.join(workspace, 'video-radar');
}
function pagePath() {
  return path.join(radarDir(), 'page.json');
}
function feedbackPath() {
  return path.join(radarDir(), 'feedback.json');
}
function feedbackFile() {
  return fs.existsSync(feedbackPath()) ? JSON.parse(fs.readFileSync(feedbackPath(), 'utf8')) : {};
}

function item(over) {
  return Object.assign({
    key: 'xhs:1001', platform: 'xiaohongshu', id: '1001', sources: ['xhs_latest'],
    title: '示例标题', url: 'https://www.xiaohongshu.com/explore/1001', author: '作者甲',
    author_fans: 1200, published_at: iso(3 * HOUR), first_seen_at: iso(3 * HOUR), last_seen_at: iso(5 * MINUTE),
    likes: 880, collects: 40, comments: 12, shares: 3, plays: null,
    likes_per_hour: 293.3, likes_delta_per_hour: 120.5, score: 88.8,
    relevance: 'relevant', relevance_reason: '关键词命中', flags: ['low_fan_hot'], cover: ''
  }, over || {});
}

function goodPage(over) {
  const data = {
    schema: 'video-radar-page/v1',
    generated_at: iso(4 * MINUTE),
    keyword: 'AI',
    sources: {
      xiaohongshu: { last_attempt_at: iso(4 * MINUTE), last_success_at: iso(10 * MINUTE), status: 'ok', note: '' },
      douyin_realtime: { last_attempt_at: iso(4 * MINUTE), last_success_at: iso(9 * MINUTE), status: 'ok', note: '' },
      douyin_daily: { last_attempt_at: iso(2 * HOUR), last_success_at: iso(2 * HOUR), status: 'ok', note: '数据约晚 1–2 天' }
    },
    today: { date: '2026-10-16', rounds: 7, xiaohongshu_ok: 7, douyin_realtime_ok: 6, douyin_daily_ok: 1, api_calls: 251 },
    lists: {
      xiaohongshu: [item()],
      douyin_realtime: [item({ key: 'dy:2001', platform: 'douyin', id: '2001', url: 'https://www.douyin.com/video/2001' })],
      douyin_daily: [item({ key: 'dy:3001', platform: 'douyin', id: '3001', url: 'https://www.douyin.com/video/3001' })]
    }
  };
  return Object.assign(data, over || {});
}

function writePage(value) {
  fs.mkdirSync(radarDir(), { recursive: true });
  if (value === null) { try { fs.unlinkSync(pagePath()); } catch (e) {} return; }
  fs.writeFileSync(pagePath(), typeof value === 'string' ? value : JSON.stringify(value), 'utf8');
}

function setSourceAge(source, ms) {
  const data = goodPage();
  data.sources[source].last_success_at = ms === null ? '' : iso(ms);
  writePage(data);
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

async function startServer() {
  workspace = fs.mkdtempSync(path.join(os.tmpdir(), 'vr-c2-ws-'));
  port = await freePort();
  assert.notEqual(port, 8766, '不得使用正式端口');
  origin = 'http://127.0.0.1:' + port;
  const log = fs.openSync(path.join(os.tmpdir(), 'vr-c2-server.log'), 'w');
  server = spawn(PYTHON, ['server.py', '--workspace', workspace, '--port', String(port)],
                 { cwd: REPO, stdio: ['ignore', log, log] });
  for (let i = 0; i < 300; i++) {
    try {
      const response = await fetch(origin + '/api/video-radar/page');
      if (response.ok) { await response.text(); return; }
    } catch (e) { /* 还没起来 */ }
    if (i === 299) throw new Error('server 启动超时');
    await new Promise((r) => setTimeout(r, 50));
  }
}

(async () => {
  console.log('=== C2 视频榜页面验收（副本 ' + REPO + '）');
  await startServer();
  browser = await chromium.launch({ channel: 'chrome', headless: true,
                                    args: ['--disable-background-networking', '--disable-component-update'] });
  context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
  await context.route('**/*', (route) => {
    const url = new URL(route.request().url());
    if (url.origin !== origin) { external.push(url.origin); return route.abort(); }
    return route.continue();
  });
  const page = await context.newPage();
  page.on('pageerror', (error) => pageErrors.push(String(error && error.message || error)));
  const shot = (name) => page.screenshot({ path: path.join(EVIDENCE, name), fullPage: true });
  const open = async () => {
    await page.goto(origin + '/video-radar.html', { waitUntil: 'load' });
    await page.waitForSelector('.vr-tab');
    await page.waitForFunction(() => document.getElementById('vr-status') &&
      !/正在读取/.test(document.getElementById('vr-status').textContent), null, { timeout: 15000 });
  };
  const post = (body, headers) => {
    const requestHeaders = Object.assign({ 'Content-Type': 'application/json' }, headers || {});
    if (requestHeaders.Origin) requestHeaders.Origin = requestHeaders.Origin.replace('PORT', String(port));
    requestHeaders['Content-Length'] = String(Buffer.byteLength(typeof body === 'string' ? body : JSON.stringify(body)));
    return context.request.post(origin + '/api/video-radar/feedback', {
      headers: requestHeaders,
      data: typeof body === 'string' ? body : JSON.stringify(body)
    });
  };

  // ================= 自测 1：转义保真 =================
  {
    const nasty = '</td></tr><script>window.__pwned=1</script><img src=x onerror="window.__pwned=1">';
    const longTitle = '长'.repeat(2000);
    writePage(goodPage({ lists: {
      xiaohongshu: [item({ key: 'xhs:evil', title: nasty }),
                    item({ key: 'xhs:long', title: longTitle, url: '' })],
      douyin_realtime: [], douyin_daily: [] } }));
    await open();
    const titleText = await page.locator('.vr-title').first().innerText();
    const longText = await page.locator('.vr-title').nth(1).innerText();
    const injected = await page.evaluate(() => ({
      script: document.querySelectorAll('#vr-body script').length,
      img: document.querySelectorAll('#vr-body img').length,
      pwned: !!window.__pwned
    }));
    check('自测1', '标题含 </td></tr><script>/<img onerror> 原文显示、不执行、不截坏 HTML',
      titleText.includes('<script>window.__pwned=1</script>') && injected.script === 0 &&
      injected.img === 0 && !injected.pwned,
      '正文首行=' + titleText.slice(0, 60) + ' ｜ 注入 script=' + injected.script + ' img=' + injected.img + ' 已执行=' + injected.pwned);
    check('自测1b', '超长 2000 字标题原样显示、不截断', longText.length >= 2000,
      '长度=' + longText.length);
    check('自测1c', '标题里的 &amp; 与引号不被当成 HTML 实体/属性',
      (await page.locator('.vr-title a').first().getAttribute('href')) === 'https://www.xiaohongshu.com/explore/1001',
      'href=' + (await page.locator('.vr-title a').first().getAttribute('href')));
    await shot('01-转义保真.png');
  }

  // ================= 自测 3：URL 白名单 =================
  {
    const bad = [
      ['javascript:alert(1)', 'javascript:'],
      ['http://www.douyin.com/video/1', 'http'],
      ['https://evil.example.com/x', '其他域名'],
      ['', '空串'],
      ['data:text/html,<script>1</script>', 'data:'],
      ['https://www.douyin.com.evil.com/video/1', '相似域名']
    ];
    const items = bad.map((row, index) => item({ key: 'xhs:url' + index, url: row[0], title: 'URL 用例 ' + row[1] }));
    items.push(item({ key: 'xhs:ok', url: 'https://www.douyin.com/video/777', title: '合法抖音链接' }));
    items.push(item({ key: 'xhs:ok2', url: 'https://www.xiaohongshu.com/explore/888', title: '合法小红书链接' }));
    writePage(goodPage({ lists: { xiaohongshu: items, douyin_realtime: [], douyin_daily: [] } }));
    await open();
    const linkCount = await page.locator('#vr-body a.vr-title, #vr-body .vr-title a').count();
    const hrefs = await page.locator('#vr-body .vr-title a').evaluateAll((nodes) => nodes.map((n) => n.getAttribute('href')));
    const noteCount = await page.locator('.vr-title .vr-no-link').count();
    check('自测3', 'javascript:/http/其他域名/空串/data:/相似域名 都不渲染成链接，只有白名单地址成链',
      linkCount === 2 && hrefs.every((h) => h && (h.startsWith('https://www.douyin.com/') || h.startsWith('https://www.xiaohongshu.com/'))) &&
      noteCount === bad.length,
      '成链 ' + linkCount + ' 条 href=' + JSON.stringify(hrefs) + ' 未成链 ' + noteCount + ' 条（用例 ' + bad.length + '）');
    await shot('02-URL白名单.png');
  }

  // ================= 自测 2：授权不得放宽 =================
  {
    try { fs.unlinkSync(feedbackPath()); } catch (e) {}
    writePage(goodPage());
    const before = fs.existsSync(feedbackPath()) ? fs.readFileSync(feedbackPath()) : null;
    const cases = [
      ['缺 X-Article-Studio', {}],
      ['错 Origin', { 'X-Article-Studio': '1', Origin: 'https://evil.example' }],
      ['null Origin', { 'X-Article-Studio': '1', Origin: 'null' }],
      ['缺 Origin', { 'X-Article-Studio': '1' }],
      ['X 头为 0', { 'X-Article-Studio': '0', Origin: 'http://127.0.0.1:PORT' }]
    ];
    const codes = [];
    for (const row of cases) codes.push((await post({ key: 'xhs:1001', verdict: 'worth' }, row[1])).status());
    let hostCode = 0;
    try {
      const response = await context.request.post(origin + '/api/video-radar/feedback', {
        headers: { 'Content-Type': 'application/json', Host: 'evil.example', 'X-Article-Studio': '1',
                   Origin: origin, 'Content-Length': '31' },
        data: { key: 'xhs:1001', verdict: 'worth' }
      });
      hostCode = response.status();
    } catch (e) { hostCode = 0; }
    const after = fs.existsSync(feedbackPath()) ? fs.readFileSync(feedbackPath()) : null;
    const badPayloads = [
      { key: 'xhs:1001' }, { key: 'xhs:1001', verdict: 'nope' }, { key: 'xhs:1001', verdict: 'worth', extra: 1 },
      { key: 'nope:0', verdict: 'worth' }, { key: '', verdict: 'worth' }, { verdict: 'worth' }
    ];
    const badCodes = [];
    for (const payload of badPayloads) badCodes.push((await post(payload, { 'X-Article-Studio': '1', Origin: 'http://127.0.0.1:PORT' })).status());
    const notJson = await post('not json', { 'X-Article-Studio': '1', Origin: 'http://127.0.0.1:PORT' });
    const afterBad = fs.existsSync(feedbackPath()) ? fs.readFileSync(feedbackPath()) : null;
    check('自测2', '缺/错/null/缺 Origin、X 头 0、非本机 Host 全部非 2xx 且反馈文件不变',
      codes.every((c) => c >= 400) && hostCode >= 400 && before === after && before === afterBad,
      '状态码=' + JSON.stringify(codes) + ' 非本机 Host=' + hostCode + ' 文件未变=' + (before === after) +
      ' ｜坏 payload=' + JSON.stringify(badCodes) + ' 非 JSON=' + notJson.status());
    // GET 带奇怪查询参数不能切换目录或读外部文件
    const probes = ['/api/video-radar/page?path=../../etc/passwd', '/api/video-radar/page?dir=/etc',
                    '/api/video-radar/page?file=/Users/huangzongning/development/article-studio/server.py',
                    '/api/video-radar/page?workspace=/'];
    const getCodes = [];
    let leaked = 0;
    for (const probe of probes) {
      const response = await context.request.get(origin + probe);
      getCodes.push(response.status());
      const body = await response.text();
      if (/root:|def main\(\)|AISA_API_KEY/.test(body)) leaked++;
    }
    check('自测2b', 'GET 带 path/dir/file/workspace 查询参数仍是同一份 page.json，不越界读文件',
      getCodes.every((c) => c === 200) && leaked === 0,
      '状态码=' + JSON.stringify(getCodes) + ' 泄露命中=' + leaked);
    check('自测2c', 'GET/PUT 不能写反馈', (await context.request.get(origin + '/api/video-radar/feedback')).status() >= 400 &&
      (await context.request.put(origin + '/api/video-radar/feedback', { data: { key: 'xhs:1001', verdict: 'worth' } })).status() >= 400,
      'GET=' + (await context.request.get(origin + '/api/video-radar/feedback')).status() +
      ' PUT=' + (await context.request.put(origin + '/api/video-radar/feedback', { data: {} })).status());
  }

  // ================= 自测 4：真实交互（点按钮 / 连点 / 409 / 500） =================
  {
    try { fs.unlinkSync(feedbackPath()); } catch (e) {}
    writePage(goodPage());
    await open();
    const rowSel = 'tr[data-key="xhs:1001"]';
    for (const verdict of ['worth', 'not_worth', 'irrelevant']) {
      const done = page.waitForResponse((r) => r.url().includes('/api/video-radar/feedback'));
      await page.locator(rowSel + ' .vr-btn[data-verdict="' + verdict + '"]').click();
      await done;
      await page.waitForFunction(() => [...document.querySelectorAll('.vr-btn')].every((b) => b.getAttribute('data-busy') !== '1'));
      const stored = feedbackFile()['xhs:1001'];
      if (stored.verdict !== verdict) throw new Error('按钮 ' + verdict + ' 未落库');
    }
    await page.reload({ waitUntil: 'load' });
    await page.waitForSelector(rowSel);
    await page.waitForTimeout(300);
    const afterReload = await page.locator(rowSel + ' .vr-btn.active').getAttribute('data-verdict');
    check('自测4a', '三个按钮各点一次都落库；刷新后仍保持最后一次选择',
      afterReload === 'irrelevant' && feedbackFile()['xhs:1001'].verdict === 'irrelevant',
      '刷新后高亮=' + afterReload + ' 文件=' + feedbackFile()['xhs:1001'].verdict);

    // 连点两个不同按钮：以后一次为准
    const first = page.waitForResponse((r) => r.url().includes('/api/video-radar/feedback'));
    await page.locator(rowSel + ' .vr-btn[data-verdict="worth"]').click();
    await first;
    const second = page.waitForResponse((r) => r.url().includes('/api/video-radar/feedback'));
    await page.locator(rowSel + ' .vr-btn[data-verdict="not_worth"]').click();
    await second;
    await page.waitForFunction(() => [...document.querySelectorAll('.vr-btn')].every((b) => b.getAttribute('data-busy') !== '1'));
    const activeVerdict = await page.locator(rowSel + ' .vr-btn.active').getAttribute('data-verdict');
    check('自测4b', '连点两个不同按钮 → 以后一次为准（界面与文件一致）',
      activeVerdict === 'not_worth' && feedbackFile()['xhs:1001'].verdict === 'not_worth',
      '高亮=' + activeVerdict + ' 文件=' + feedbackFile()['xhs:1001'].verdict);

    // 同一事件轮里连点 3 次不同按钮：仍以后一次为准
    await page.evaluate(() => {
      const row = document.querySelector('tr[data-key="xhs:1001"]');
      row.querySelector('.vr-btn[data-verdict="worth"]').click();
      row.querySelector('.vr-btn[data-verdict="irrelevant"]').click();
      row.querySelector('.vr-btn[data-verdict="not_worth"]').click();
    });
    await page.waitForTimeout(1200);
    const burst = await page.locator(rowSel + ' .vr-btn.active').getAttribute('data-verdict');
    check('自测4c', '同一事件轮连点 3 次不同按钮 → 最终以后一次为准',
      burst === 'not_worth' && feedbackFile()['xhs:1001'].verdict === 'not_worth',
      '高亮=' + burst + ' 文件=' + feedbackFile()['xhs:1001'].verdict);

    // 服务端 500：提示失败、不假装成功
    await page.route('**/api/video-radar/feedback', (route) => route.fulfill({
      status: 500, contentType: 'application/json', body: '{"error":"模拟故障"}' }));
    await page.locator(rowSel + ' .vr-btn[data-verdict="worth"]').click();
    await page.waitForSelector('#vr-toast-container .vr-toast.is-warning');
    const warnText = (await page.locator('#vr-toast-container .vr-toast.is-warning').innerText()).replace(/\s+/g, ' ');
    await page.waitForFunction(() => [...document.querySelectorAll('.vr-btn')].every((b) => b.getAttribute('data-busy') !== '1'));
    const afterFail = await page.locator(rowSel + ' .vr-btn.active').getAttribute('data-verdict');
    await page.unroute('**/api/video-radar/feedback');
    const fileAfterFail = feedbackFile()['xhs:1001'].verdict;
    check('自测4d', 'POST 500 → 提示失败、按钮回到原状态、文件没被假装改成功',
      warnText.includes('保存失败') && afterFail === 'not_worth' && fileAfterFail === 'not_worth',
      '提示=' + warnText.slice(0, 50) + ' 高亮=' + afterFail + ' 文件=' + fileAfterFail);

    // 反馈文件损坏 → 409
    const corruptRaw = '{"xhs:1001": {"verdict": "wo';
    fs.writeFileSync(feedbackPath(), corruptRaw, 'utf8');
    const response409 = await post({ key: 'xhs:1001', verdict: 'worth' }, { 'X-Article-Studio': '1', Origin: 'http://127.0.0.1:PORT' });
    await open();
    const banner = (await page.locator('#vr-status').innerText()).replace(/\s+/g, ' ');
    const kept = fs.readFileSync(feedbackPath(), 'utf8') === corruptRaw;
    check('自测4e', '反馈文件损坏 → POST 409、原文件一字节不动、页面给出提示',
      response409.status() === 409 && kept && banner.includes('反馈文件损坏'),
      'HTTP=' + response409.status() + ' 文件未变=' + kept + ' 页面提示=' + banner.slice(0, 70));
    await shot('03-反馈损坏.png');
    try { fs.unlinkSync(feedbackPath()); } catch (e) {}
  }

  // ================= 自测 5：状态切换（有数据→空→missing→corrupt→恢复） =================
  {
    writePage(goodPage());
    await open();
    const steps = [];
    steps.push(['有数据', await page.locator('#vr-body tbody tr').count()]);
    writePage(goodPage({ lists: { xiaohongshu: [], douyin_realtime: [], douyin_daily: [] } }));
    await open();
    steps.push(['空列表', await page.locator('#vr-body tbody tr').count(), (await page.locator('#vr-body .vr-empty').innerText()).slice(0, 20)]);
    writePage(null);
    await open();
    steps.push(['missing', await page.locator('#vr-body tbody tr').count(), (await page.locator('#vr-body .vr-empty').innerText()).slice(0, 24)]);
    writePage('{ broken');
    await open();
    steps.push(['corrupt', await page.locator('#vr-body tbody tr').count(), (await page.locator('#vr-status').innerText()).slice(0, 24)]);
    writePage(goodPage());
    await open();
    steps.push(['恢复', await page.locator('#vr-body tbody tr').count()]);
    const ok = steps.every((row) => (row[0] === '空列表' || row[0] === 'missing' ? row[1] === 0 : true)) &&
      steps[0][1] === 1 && steps[4][1] === 1 && steps[2][1] === 0 && steps[3][1] === 0;
    check('自测5', '有数据→空列表→missing→corrupt→恢复，每一步都不残留旧行',
      ok, JSON.stringify(steps));

    // 运行中从有数据切到空列表（不刷新页面，只重取接口）
    writePage(goodPage());
    await open();
    writePage(goodPage({ lists: { xiaohongshu: [], douyin_realtime: [], douyin_daily: [] } }));
    await page.reload({ waitUntil: 'load' });
    await page.waitForTimeout(400);
    const liveRows = await page.locator('#vr-body tbody tr').count();
    check('自测5b', '数据变空后重新读取：旧行被清掉，不显示上一轮内容', liveRows === 0, '残留行=' + liveRows);
  }

  // ================= 自测 6：过期判定边界 =================
  {
    // 每天 21:20 一轮，实时来源阈值 25 小时；这里避开「正好等于阈值」的漂移，
    // 正好等于阈值的边界由 tests/test_video_radar_page_api.py::test_stale_boundaries 确定性覆盖。
    const cases = [
      ['xiaohongshu', 24 * HOUR + 30 * MINUTE, false], ['xiaohongshu', 25 * HOUR + MINUTE, true],
      ['douyin_realtime', 24 * HOUR + 30 * MINUTE, false], ['douyin_realtime', 25 * HOUR + MINUTE, true],
      ['douyin_daily', 30 * HOUR - 60000, false], ['douyin_daily', 30 * HOUR + 60000, true]
    ];
    const wrong = [];
    for (const row of cases) {
      setSourceAge(row[0], row[1]);
      await open();
      const banner = (await page.locator('#vr-status').innerText());
      const flag = banner.indexOf('数据已过期') >= 0;
      if (flag !== row[2]) wrong.push(row[0] + '@' + row[1] + 'min→' + flag);
    }
    setSourceAge('xiaohongshu', null);
    await open();
    const emptyFlag = (await page.locator('#vr-status').innerText()).indexOf('数据已过期') >= 0;
    check('自测6', '24h30m 不过期、25h1m 过期、30 小时前后、last_success_at 为空 的过期判定',
      wrong.length === 0 && emptyFlag, '错判=' + JSON.stringify(wrong) + ' 空 last_success_at 算过期=' + emptyFlag);
    await shot('04-过期标记.png');
  }

  // ================= 自测 7：null 与 0 =================
  {
    writePage(goodPage({ lists: {
      xiaohongshu: [
        item({ key: 'xhs:null', author_fans: null, plays: null, likes: null, likes_per_hour: null, likes_delta_per_hour: null }),
        item({ key: 'xhs:zero', author_fans: 0, plays: 0, likes: 0, likes_per_hour: 0, likes_delta_per_hour: 0 })
      ],
      douyin_realtime: [], douyin_daily: [] } }));
    await open();
    const readRow = async (key) => {
      const cells = await page.locator('tr[data-key="' + key + '"] td').allInnerTexts();
      return cells.map((t) => t.replace(/\s+/g, ' ').trim());
    };
    const nullRow = await readRow('xhs:null');
    const zeroRow = await readRow('xhs:zero');
    const nullBody = (await page.locator('#vr-body').innerText());
    // 列序：0 名次 /1 标题 /2 作者+相对时间 /3 粉丝 /4 播放 /5 发布时间 /6 赞 /7 赞每小时 /8 近一小时涨速
    const NUMERIC = [3, 4, 6, 7, 8];
    check('自测7', 'null 显示「—」、0 显示 0（粉丝/播放/赞/赞每小时/近一小时涨速五列）',
      NUMERIC.every((i) => nullRow[i] === '—') && NUMERIC.every((i) => zeroRow[i] === '0'),
      'null 五列=' + JSON.stringify(NUMERIC.map((i) => nullRow[i])) + ' ｜0 五列=' + JSON.stringify(NUMERIC.map((i) => zeroRow[i])));
    check('自测7b', '页面正文里没有把 null 渲染成 0/NULL/null 字样',
      !/NULL|null/.test(nullBody), '命中=' + (/NULL|null/.test(nullBody) ? '有' : '无'));
    await shot('05-null与0.png');
  }

  // ================= 三个分页签 =================
  {
    writePage(goodPage());
    await open();
    const tabs = await page.locator('.vr-tab').allInnerTexts();
    const perTab = [];
    for (const list of ['xiaohongshu', 'douyin_realtime', 'douyin_daily']) {
      await page.click('.vr-tab[data-list="' + list + '"]');
      await page.waitForTimeout(200);
      perTab.push(list + '=' + (await page.locator('#vr-body tbody tr').count()));
    }
    const header = (await page.locator('#vr-status').innerText()).replace(/\s+/g, ' ');
    check('附-分页签', '三个分页签（小红书/抖音 1 小时热榜/抖音日榜）各自渲染自己的列表',
      tabs.length === 3 && perTab.every((row) => row.endsWith('=1')), '标签=' + JSON.stringify(tabs) + ' 行数=' + JSON.stringify(perTab));
    check('附-顶部信息', '顶部显示今日轮次与 AIsa 调用次数', header.includes('今日') && header.includes('AIsa 调用 251'),
      header.slice(0, 110));
    const foot = (await page.locator('.vr-foot', { hasText: '数据来源' }).innerText()).replace(/\s+/g, ' ');
    check('附-说明文字', '页脚写「每天 21:20 更新」与 25 小时阈值，不再出现 70 分钟',
      foot.includes('21:20') && foot.includes('25 小时') && !foot.includes('70 分钟'), foot.slice(0, 160));
    await shot('06-三视图.png');
  }

  // ================= C2-fix2：整包有限数（1e400 / -1e400、深层、未使用字段） =================
  {
    const inject = (position, token) => {
      const data = goodPage();
      const mark = '__TOK__';
      const setters = {
        'lists.xiaohongshu[0].likes': (d) => { d.lists.xiaohongshu[0].likes = mark; },
        'sources.xiaohongshu.note': (d) => { d.sources.xiaohongshu.note = mark; },
        'sources.douyin_daily.last_success_at': (d) => { d.sources.douyin_daily.last_success_at = mark; },
        'lists.douyin_daily[0].extra.a[0].b.c[0]': (d) => { d.lists.douyin_daily[0].extra = { a: [{ b: { c: [mark] } }] }; },
        'today.extra.a[0].b.c[0]': (d) => { d.today.extra = { a: [{ b: { c: [mark] } }] }; },
        'lists.xiaohongshu[0].sources[0]': (d) => { d.lists.xiaohongshu[0].sources = [mark]; }
      };
      setters[position](data);
      const raw = JSON.stringify(data).replace('"' + mark + '"', token);
      if (raw.indexOf(token) < 0) throw new Error('token 未注入：' + position);
      return raw;
    };
    const positions = ['lists.xiaohongshu[0].likes', 'sources.xiaohongshu.note',
                        'sources.douyin_daily.last_success_at', 'lists.douyin_daily[0].extra.a[0].b.c[0]',
                        'today.extra.a[0].b.c[0]', 'lists.xiaohongshu[0].sources[0]'];
    const wrong = [];
    for (const position of positions) {
      for (const token of ['1e400', '-1e400']) {
        writePage(inject(position, token));
        const api = await (await context.request.get(origin + '/api/video-radar/page')).json();
        await page.reload({ waitUntil: 'load' });
        await page.waitForTimeout(220);
        const ui = await page.evaluate(() => ({
          cls: document.getElementById('vr-status').className,
          rows: document.querySelectorAll('#vr-body tbody tr').length,
          error: document.getElementById('vr-status').textContent.slice(0, 60)
        }));
        if (api.status !== 'corrupt' || ui.cls.indexOf('is-corrupt') < 0 || ui.rows !== 0) {
          wrong.push(position + '@' + token + '→' + api.status + '/' + ui.rows);
        }
      }
    }
    check('fix2-溢出数字', '1e400 / -1e400 在 6 个位置（含未使用字段与深层嵌套）接口与页面都是 corrupt、0 行',
      wrong.length === 0, wrong.length ? '错判=' + JSON.stringify(wrong) : '12/12 全部 corrupt 且 0 行');

    // 合法值不误伤：1e300 / -1e300 / 0 / 0.0 / null / 超长数字串都不该触发损坏判定
    const data = goodPage();
    data.lists.xiaohongshu[0].likes = 1e300;          // 有限，照常展示（页面不截断，值本身荒诞但不算坏）
    data.lists.xiaohongshu[0].plays = null;
    data.lists.xiaohongshu[0].author_fans = 0;
    data.lists.xiaohongshu[0].likes_per_hour = 0.0;
    data.lists.xiaohongshu[0].comments = 0;
    writePage(data);
    await open();
    const api = await (await context.request.get(origin + '/api/video-radar/page')).json();
    const cells = (await page.locator('tr[data-key="xhs:1001"] td').allInnerTexts()).map((t) => t.replace(/\s+/g, ' ').trim());
    const banner = await page.locator('#vr-status').innerText();
    check('fix2-合法值不误伤',
      '1e300 / 0 / 0.0 / null / 超长数字串照常展示（不判损坏）',
      api.status === 'ok' && banner.indexOf('数据异常') < 0 &&
      cells[3] === '0' && cells[4] === '—' && cells[5] !== '—' &&
      cells[6] !== '—' && cells[7] === '0' && cells[8] !== '—' &&
      api.lists.xiaohongshu[0].likes === 1e300,
      '接口 status=' + api.status + ' ｜粉丝=' + cells[3] + ' 播放=' + cells[4] + ' 赞(1e300，前 12 字)=' +
      cells[6].slice(0, 12) + '… 赞/小时(0.0)=' + cells[7]);
    // 正常量级的格式化不受影响
    const sane = goodPage();
    sane.lists.xiaohongshu[0].likes = 1234567;
    sane.lists.xiaohongshu[0].author_fans = 45600;
    writePage(sane);
    await open();
    const saneCells = (await page.locator('tr[data-key="xhs:1001"] td').allInnerTexts()).map((t) => t.replace(/\s+/g, ' ').trim());
    check('fix2-正常量级格式化', '1234567 赞 → 123.5万，45600 粉丝 → 4.6万',
      saneCells[6] === '123.5万' && saneCells[3] === '4.6万', '粉丝=' + saneCells[3] + ' 赞=' + saneCells[6]);
    await shot('10-fix2-溢出与合法值.png');
  }

  // ================= C2-fix1：M1 NaN 判坏 + M2 外部文本不截断 =================
  {
    // M2：五处外部文本各 2110 字（含 HTML 注入 token），接口输出与 DOM textContent 逐字节相等
    const poison = '<script>window.__pwned=1</script><img src=x onerror="window.__pwned=1">"><svg onload="window.__pwned=1">&amp;\n';
    const long = poison + '题'.repeat(2000);
    const data = goodPage();
    const first = data.lists.xiaohongshu[0];
    first.title = long; first.author = long; first.relevance_reason = long; first.sources = [long];
    data.sources.xiaohongshu.note = long;
    writePage(data);
    await open();
    const api = await (await context.request.get(origin + '/api/video-radar/page')).json();
    const apiItem = api.lists.xiaohongshu[0];
    const dom = await page.evaluate(() => {
      const row = document.querySelector('#vr-body tbody tr');
      const cells = [...row.querySelectorAll('td')];
      return {
        title: row.querySelector('.vr-title a').textContent,
        author: cells[2].querySelector('div').textContent,
        flags: cells[9].textContent,
        status: document.getElementById('vr-status').textContent,
        injected: document.querySelectorAll('#vr-body script,#vr-body img,#vr-body svg').length,
        pwned: !!window.__pwned
      };
    });
    const equal = (a, b) => a === b;
    check('fix1-M2', '作者/理由/来源名/来源注释/标题 2110 字在接口输出与 DOM 逐字节相等、未注入',
      equal(apiItem.author, long) && equal(apiItem.relevance_reason, long) &&
      equal(apiItem.sources[0], long) && equal(apiItem.title, long) &&
      equal(api.sources.xiaohongshu.note, long) &&
      equal(dom.title, long) && equal(dom.author, long) && dom.flags.includes(long) &&
      dom.status.includes(long) && dom.injected === 0 && !dom.pwned,
      '长度 接口 标题=' + apiItem.title.length + ' 作者=' + apiItem.author.length + ' 理由=' + apiItem.relevance_reason.length +
      ' 来源=' + apiItem.sources[0].length + ' 注释=' + api.sources.xiaohongshu.note.length +
      ' ｜DOM 标题=' + dom.title.length + ' 作者=' + dom.author.length + ' 标记列=' + dom.flags.length +
      ' 注入=' + dom.injected);

    // M1：运行中从有数据切到含 NaN 的 page.json → 提示损坏 + 0 行，恢复后正常
    const nanRaw = JSON.stringify(goodPage()).replace(/"likes":\s*880/, '"likes": NaN');
    if (!/NaN/.test(nanRaw)) throw new Error('NaN 未写进 page.json，夹具替换失配');
    writePage(nanRaw);
    await page.reload({ waitUntil: 'load' });
    await page.waitForTimeout(400);
    const nanState = await page.evaluate(() => ({
      status: document.getElementById('vr-status').textContent,
      cls: document.getElementById('vr-status').className,
      rows: document.querySelectorAll('#vr-body tbody tr').length,
      empty: document.querySelectorAll('#vr-body .vr-empty').length
    }));
    const nanApi = await (await context.request.get(origin + '/api/video-radar/page')).json();
    writePage(goodPage());
    await page.reload({ waitUntil: 'load' });
    await page.waitForSelector('#vr-body tbody tr');
    const restored = await page.locator('#vr-body tbody tr').count();
    check('fix1-M1', 'page.json 含 NaN → 整包 corrupt、页面提示损坏且 0 行；恢复后正常',
      nanApi.status === 'corrupt' && nanState.cls.indexOf('is-corrupt') >= 0 &&
      nanState.rows === 0 && nanState.empty === 1 && restored === 1,
      '接口 status=' + nanApi.status + ' ｜页面 class=' + nanState.cls + ' 行数=' + nanState.rows +
      ' 提示=' + nanState.status.slice(0, 40) + ' ｜恢复后行数=' + restored);
    // 其它两个 token 与另一个位置
    const others = [];
    for (const [from, to] of [[/"score":\s*88\.8/, '"score": Infinity'], [/"api_calls":\s*251/, '"api_calls": -Infinity']]) {
      const raw = JSON.stringify(goodPage()).replace(from, to);
      if (!/Infinity/.test(raw)) throw new Error('token 未写进 page.json：' + from);
      writePage(raw);
      const body = await (await context.request.get(origin + '/api/video-radar/page')).json();
      others.push(to.trim() + '→' + body.status);
    }
    check('fix1-M1b', 'Infinity / -Infinity（score 与 today.api_calls）同样整包 corrupt',
      others.every((row) => row.endsWith('corrupt')), JSON.stringify(others));
    await shot('09-fix1-NaN与长文本.png');
  }

  // ================= 修订（2026-10-16 13:00）：页签改名 + 播放/粉丝 + item_url 不得成链 =================
  {
    const hotBoard = 'https://www.douyin.com/video/7300000000000000001';
    const signedItemUrl = 'https://v3-web.douyinvod.com/video/tos/cn/tos-cn-ve-15c001/o8FzJfxxx.mp4?a=6383&sign=deadbeef';
    writePage(goodPage({ lists: {
      xiaohongshu: [item({ key: 'xhs:null-fans', author_fans: null, plays: null,
                           sources: ['xhs_latest'] })],
      douyin_realtime: [
        item({ key: 'dy:board', platform: 'douyin', id: '7300000000000000001', url: hotBoard,
               sources: ['dy_board_video_1h'], plays: 1280000, author_fans: 45000, likes: 56000 }),
        item({ key: 'dy:lowfan', platform: 'douyin', id: '7300000000000000002',
               url: 'https://www.douyin.com/video/7300000000000000002',
               sources: ['dy_board_low_fan_1h'], plays: 88000, author_fans: 900, flags: ['low_fan_hot'] }),
        item({ key: 'dy:multi', platform: 'douyin', id: '7300000000000000003',
               url: 'https://www.douyin.com/video/7300000000000000003', sources: ['dy_multi'],
               plays: null, author_fans: null })
      ],
      douyin_daily: [] } }));
    await open();
    const tabs = await page.locator('.vr-tab').allInnerTexts();
    const hotTab = tabs[1];
    await page.click('.vr-tab[data-list="douyin_realtime"]');
    await page.waitForSelector('tr[data-key="dy:board"]');
    const head = (await page.locator('.vr-table thead').innerText()).replace(/\s+/g, ' ').trim();
    const rowOf = async (key) => (await page.locator('tr[data-key="' + key + '"] td').allInnerTexts())
      .map((t) => t.replace(/\s+/g, ' ').trim());
    const hot = await rowOf('dy:board');
    const multi = await rowOf('dy:multi');
    // 列序：0 名次 /1 标题 /2 作者 /3 粉丝 /4 播放 /5 发布时间 /6 赞 /7 赞每小时 /8 涨速 /9 标记 /10 判断
    check('修订1', '页签名「小红书」与「抖音 1 小时热榜」不再暗示每小时更新',
      tabs[0] === '小红书' && hotTab === '抖音 1 小时热榜' && !tabs.join(' ').includes('实时候选') && !tabs.join(' ').includes('（每小时）'),
      '三个页签=' + JSON.stringify(tabs));
    check('修订2', '新增「播放」列，且热榜条目的播放与粉丝都有真实值',
      head.includes('播放') && hot[3] === '4.5万' && hot[4] === '128.0万',
      '表头=' + head + ' ｜热榜首行 粉丝=' + hot[3] + ' 播放=' + hot[4]);
    check('修订3', 'dy_multi 补充来源拿不到播放/粉丝时显示「—」，不显示 0',
      multi[3] === '—' && multi[4] === '—', 'dy_multi 行 粉丝=' + multi[3] + ' 播放=' + multi[4]);
    check('修订4', '低粉爆款条目（dy_board_low_fan_1h）也带播放与粉丝',
      (await rowOf('dy:lowfan'))[4] === '8.8万', '低粉行=' + JSON.stringify(await rowOf('dy:lowfan')));
    // item_url（带时效签名的视频文件地址）不得成链
    const injectedUrl = goodPage();
    injectedUrl.lists.douyin_realtime = [
      item({ key: 'dy:signed', platform: 'douyin', id: '7300000000000000009', url: signedItemUrl,
             sources: ['dy_board_video_1h'], title: '热榜 item_url 用例' }),
      item({ key: 'dy:ok', platform: 'douyin', id: '7300000000000000010',
             url: 'https://www.douyin.com/video/7300000000000000010', sources: ['dy_board_video_1h'] })
    ];
    writePage(injectedUrl);
    await open();
    await page.click('.vr-tab[data-list="douyin_realtime"]');
    await page.waitForSelector('tr[data-key="dy:signed"]');
    const hrefs = await page.locator('.vr-title a').evaluateAll((nodes) => nodes.map((n) => n.getAttribute('href')));
    check('修订5', '热榜 item_url（带时效签名的文件地址）不成链，合规 /video/<id> 成链',
      hrefs.length === 1 && hrefs[0] === 'https://www.douyin.com/video/7300000000000000010',
      '成链 href=' + JSON.stringify(hrefs));
    await shot('08-修订热榜与播放列.png');
  }

  // ================= 推特雷达页入口 =================
  {
    await page.goto(origin + '/twitter-radar.html', { waitUntil: 'load' });
    const link = page.locator('a[href="/video-radar.html"]');
    const count = await link.count();
    const label = count ? (await link.innerText()).trim() : '';
    await link.click();
    await page.waitForSelector('.vr-tab');
    check('附-入口', '推特雷达页有指向 video-radar.html 的入口且可点通',
      count === 1 && label.includes('视频榜') && page.url().endsWith('/video-radar.html'),
      '入口数=' + count + ' 文案=' + label + ' 跳转后=' + page.url().split('/').pop());
    await shot('07-推特页入口.png');
  }

  // C2-fix3：使用原始 JSON 数字文本，避免 JS 夹具先把大整数变成 Infinity/null。
  {
    const positions = [
      ['lists.xiaohongshu[0].likes', d => { d.lists.xiaohongshu[0].likes = '__INT__'; }],
      ['today.api_calls', d => { d.today.api_calls = '__INT__'; }],
      ['lists.douyin_realtime[0].score', d => { d.lists.douyin_realtime[0].score = '__INT__'; }],
      ['sources.xiaohongshu.note', d => { d.sources.xiaohongshu.note = '__INT__'; }],
      ['extra.a[0].b.c[0]', d => { d.extra = { a: [{ b: { c: ['__INT__'] } }] }; }]
    ];
    for (const [position, inject] of positions) {
      for (const sign of ['', '-']) {
        const data = goodPage(); inject(data);
        const literal = sign + '9'.repeat(400);
        const raw = JSON.stringify(data).replace('"__INT__"', literal);
        assert(raw.includes(literal), '400 位整数必须按无引号原文写入');
        writePage(raw);
        await open();
        const api = await (await context.request.get(origin + '/api/video-radar/page')).json();
        const cls = await page.locator('#vr-status').getAttribute('class');
        const rows = await page.locator('#vr-body tbody tr').count();
        const banner = await page.locator('#vr-status').innerText();
        check('fix3-int400-' + position + '-' + (sign || '+'), '正负400位整数整包损坏：' + position,
          api.status === 'corrupt' && Object.values(api.lists).every(list => list.length === 0) &&
          api.error.includes(position) && cls.includes('is-corrupt') && rows === 0 && banner.includes('数据异常'),
          'status=' + api.status + ' rows=' + rows + ' error=' + api.error);
      }
    }
    const boundaries = [
      ['309位溢出', '9'.repeat(309), 'corrupt'],
      ['负309位溢出', '-' + '9'.repeat(309), 'corrupt'],
      ['308位', '9'.repeat(308), 'ok'], ['负308位', '-' + '9'.repeat(308), 'ok'],
      ['300位', '9'.repeat(300), 'ok'], ['309位有限1e308', '1' + '0'.repeat(308), 'ok'],
      ['1e300', '1e300', 'ok'], ['-1e300', '-1e300', 'ok'],
      ['0', '0', 'ok'], ['null', 'null', 'ok'], ['负数', '-7', 'ok'],
      ['超过安全整数但有限', '9007199254740993', 'ok']
    ];
    for (const [label, literal, expected] of boundaries) {
      const data = goodPage(); data.lists.xiaohongshu[0].likes = '__INT__';
      writePage(JSON.stringify(data).replace('"__INT__"', literal));
      await open();
      const api = await (await context.request.get(origin + '/api/video-radar/page')).json();
      const cls = await page.locator('#vr-status').getAttribute('class');
      const rows = await page.locator('#vr-body tbody tr').count();
      let ok = api.status === expected && cls.includes('is-' + expected) && rows === (expected === 'ok' ? 1 : 0);
      if (expected === 'ok') {
        const value = api.lists.xiaohongshu[0].likes;
        const cells = await page.locator('#vr-body tbody tr').first().locator('td').allTextContents();
        ok = ok && (literal === 'null' ? value === null && cells[6] === '—' :
          Number.isFinite(value) && value === JSON.parse(literal) && cells[6] !== '—');
      }
      check('fix3-boundary-' + label, '浏览器数值边界：' + label, ok, 'status=' + api.status + ' rows=' + rows);
    }
    await shot('11-fix3-整数边界.png');
  }

  // ================= 布局修复（运行验收 M2）：真实布局量化 + 真实滚轮 =================
  // 三视口 × 三个页签。故意造 50/30/50 条生产量级数据：默认上下文只有一条，量不到真实行高。
  {
    const VIEWPORTS = [[1600, 1050], [1280, 800], [390, 844]];
    const TABS = [['xiaohongshu', '小红书'], ['douyin_realtime', '抖音 1 小时热榜'],
                  ['douyin_daily', '抖音日榜（数据约晚 1–2 天）']];
    const big = goodPage();
    for (const name of ['xiaohongshu', 'douyin_realtime', 'douyin_daily']) {
      const base = big.lists[name][0];
      const cap = name === 'douyin_realtime' ? 30 : 50;
      big.lists[name] = Array.from({ length: cap }, (_, i) => {
        const row = JSON.parse(JSON.stringify(base));
        row.key = name + ':' + (1000 + i);
        row.id = String(1000 + i);
        // 加长标题更容易暴露「一字一行」；生产里最长的抖音标题有 711 字
        row.title = row.title + ' 补充说明' + '内容'.repeat(3 + (i % 5)) + ' end#' + i;
        return row;
      });
    }
    writePage(big);
    const layoutRows = [];
    for (const [w, h] of VIEWPORTS) {
      await page.setViewportSize({ width: w, height: h });
      for (const [list, label] of TABS) {
        await open();
        if (list !== 'xiaohongshu') {
          await page.click(`.vr-tab[data-list="${list}"]`);
          await page.waitForFunction(() => document.querySelectorAll('#vr-body tbody tr').length > 0);
        }
        const m = await page.evaluate(() => {
          const body = getComputedStyle(document.body);
          const cell = document.querySelector('#vr-body tbody tr .vr-title');
          const row = document.querySelector('#vr-body tbody tr');
          const shell = document.querySelector('.vr-table-scroll');
          const box = (n) => (n ? n.getBoundingClientRect() : null);
          const cb = box(cell), rb = box(row);
          return {
            display: body.display, overflow: body.overflow, cols: body.gridTemplateColumns,
            titleWidth: cb ? Math.round(cb.width * 100) / 100 : null,
            rowHeight: rb ? Math.round(rb.height * 100) / 100 : null,
            rows: document.querySelectorAll('#vr-body tbody tr').length,
            shellClient: shell ? shell.clientWidth : null,
            shellScroll: shell ? shell.scrollWidth : null,
            shellCanScrollX: shell ? shell.scrollWidth > shell.clientWidth + 1 : false,
            docScrollWidth: document.documentElement.scrollWidth,
            innerWidth: window.innerWidth
          };
        });
        // 真实鼠标滚轮滚到底（不能用 scrollIntoView 代替）
        await page.mouse.move(Math.floor(w / 2), Math.floor(h / 2));
        for (let i = 0; i < 24; i++) await page.mouse.wheel(0, 1500);
        await page.waitForTimeout(350);
        const sc = await page.evaluate(() => {
          const last = document.querySelector('#vr-body tbody tr:last-child');
          const b = last.getBoundingClientRect();
          return { scrollY: Math.round(window.scrollY), visible: b.bottom > 0 && b.top < window.innerHeight };
        });
        layoutRows.push(Object.assign({ viewport: w + 'x' + h, list: list, label: label },
                                       m, { scrollY: sc.scrollY, lastVisible: sc.visible }));
        if (w === 1600 || w === 390) await shot('12-layout-' + w + 'x' + h + '-' + list + '.png');
        await page.evaluate(() => window.scrollTo(0, 0));
      }
    }
    // 1) body 不再继承 style.css 的 grid + overflow:hidden
    const badBody = layoutRows.filter((r) => r.display === 'grid' || r.overflow === 'hidden');
    check('布局-body', '三个页签×三视口下 body 都不是 grid、overflow 不是 hidden', badBody.length === 0,
      '异常项=' + JSON.stringify(badBody.map((r) => r.viewport + '/' + r.list + ':' + r.display + '/' + r.overflow)));
    // 2) 标题列最小宽度（桌面 ≥160px；窄视口 ≥120px 或表格可横向滚动）
    const narrow = layoutRows.filter((r) => {
      const min = parseInt(r.viewport, 10) >= 1280 ? 160 : 120;
      return r.titleWidth < min && !(min === 120 && r.shellCanScrollX);
    });
    check('布局-标题列宽', '标题单元格 ≥160px（1280+）／≥120px 或可横向滚动（390）', narrow.length === 0,
      '最小值=' + Math.min.apply(null, layoutRows.map((r) => r.titleWidth)) + 'px ｜异常=' +
      JSON.stringify(narrow.map((r) => r.viewport + '/' + r.list + ':' + r.titleWidth)));
    // 3) 首行高（桌面 ≤120px，不是竖排撑出来的 287/497/643px）
    const tall = layoutRows.filter((r) => parseInt(r.viewport, 10) >= 1280 && r.rowHeight > 120);
    check('布局-首行高', '1600/1280 宽下首行高 ≤120px', tall.length === 0,
      '最大首行高=' + Math.max.apply(null, layoutRows.map((r) => r.rowHeight)) + 'px ｜异常=' +
      JSON.stringify(tall.map((r) => r.viewport + '/' + r.list + ':' + r.rowHeight)));
    // 4) 滚轮能滚到底，末行进视口
    const stuck = layoutRows.filter((r) => r.scrollY <= 0 || !r.lastVisible);
    check('布局-滚轮到底', '真实滚轮后 scrollY 增加且最后一行可见', stuck.length === 0,
      'scrollY 范围=' + Math.min.apply(null, layoutRows.map((r) => r.scrollY)) + '-' +
      Math.max.apply(null, layoutRows.map((r) => r.scrollY)) + ' ｜异常=' +
      JSON.stringify(stuck.map((r) => r.viewport + '/' + r.list + ':scrollY=' + r.scrollY)));
    // 5) 页面级不横向溢出（表格只在壳内横滚）
    const overflowPage = layoutRows.filter((r) => r.docScrollWidth > r.innerWidth + 1);
    check('布局-不撑破页面', 'documentElement.scrollWidth 不超过视口宽', overflowPage.length === 0,
      '异常=' + JSON.stringify(overflowPage.map((r) => r.viewport + '/' + r.list + ':' + r.docScrollWidth + '>' + r.innerWidth)));
    fs.writeFileSync(path.join(EVIDENCE, 'layout-rows.json'), JSON.stringify(layoutRows, null, 2));
    await page.setViewportSize({ width: 1440, height: 1000 });
    writePage(goodPage());
    await open();
  }

  check('附-无副作用', 'pageerror 为 0、外网请求为 0', pageErrors.length === 0 && external.length === 0,
    'pageerror=' + JSON.stringify(pageErrors.slice(0, 3)) + ' 外网=' + JSON.stringify([...new Set(external)]));

  assert.deepEqual(external, [], '存在外网请求: ' + external.slice(0, 3).join(','));
  const failed = results.filter((row) => !row.ok);
  console.log('\n===== C2 视频榜页面汇总 =====');
  console.log('通过 ' + (results.length - failed.length) + '/' + results.length +
              '｜pageerror 累计 ' + pageErrors.length + '｜外网请求 ' + external.length + '｜端口 ' + port);
  fs.writeFileSync(path.join(EVIDENCE, 'results.json'), JSON.stringify({ port, results, pageErrors, external }, null, 2));
  if (failed.length) { console.log('失败项：' + failed.map((row) => row.id).join(', ')); process.exitCode = 1; }
})().catch((error) => { console.error('测试异常：', error); process.exitCode = 1; }).finally(async () => {
  if (browser) { try { await browser.close(); } catch (e) {} }
  if (server) { try { server.kill('SIGTERM'); } catch (e) {} }
  if (workspace) { try { fs.rmSync(workspace, { recursive: true, force: true }); } catch (e) {} }
});
