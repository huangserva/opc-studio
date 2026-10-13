'use strict';
/**
 * 正文 Markdown：链接目标「实体解码后拼出内部标记」的真实 Chrome 编辑保存复验（复核 g-H1）。
 *
 * 复核在 g 上用这个路径**确定复现**了缺陷：把
 *   [目标](https://example.com/Zzq&#49;Zqz) Zzq6Zqz Zzq7Zqz **新**
 * 填进 contenteditable → 保存新版本 → 重载，读回的 href 是 `…/Zzq7Zqz`，
 * 原生 markdown-it 应给 `…/Zzq1Zqz`。本脚本是它的**可重放**版本：
 * 同一路径、同一批输入，修后必须与原生 markdown-it 逐字相等。
 *
 * 独立运行（不挂 run.cjs）：node tests/e2e/content_markdown_collision.cjs
 * 环境变量：E2E_HEADED=1 显示窗口；E2E_BROWSER=chromium 用 Playwright 自带 Chromium。
 */
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const http = require('node:http');
const net = require('node:net');
const assert = require('node:assert/strict');
const { spawn, spawnSync } = require('node:child_process');
const { stopChild, forceStopSync, alive } = require('./stop_child.cjs');

const ROOT = path.resolve(__dirname, '../..');
process.chdir(ROOT);
const RESERVED_PORTS = new Set([8766, 3000, 18766]);
const PYTHON = path.resolve(ROOT, '../article-importer/.venv/bin/python');
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
let base = '', browser = null;
const steps = [];

// 复核点名的两条 + 合同要求覆盖的形态。判据只有一条：
// **href 经属性解码 + 两次 unquote 后，与原生本地 markdown-it 渲染同一段原文逐字相等**。
const CASES = [
  ['seal-digit-chrome', '[目标](https://example.com/Zzq&#49;Zqz) Zzq6Zqz Zzq7Zqz **新**'],
  ['pending-digit', '[目标](https://example.com/zQq&#49;qQz) &#42; **新**'],
  ['seal-prefix', '[目标](https://example.com/&#90;zq1Zqz) &#42; **新**'],
  ['pending-suffix', '[目标](https://example.com/zQq1qQ&#122;) &#42; **新**'],
  ['hex-entity', '[目标](https://example.com/Zzq&#x31;Zqz) &#35; **新**'],
  ['reference', '[目标][r]\n\n[r]: https://example.com/zQq&#49;qQz\n\n&#42; **新**'],
  ['query', '[目标](https://example.com/?q=Zzq&#49;Zqz) &#35; **新**'],
  ['mailto', '[邮件](mailto:test@example.com?subject=zQq&#49;qQz) &#42; **新**'],
];

function record(name, detail) {
  steps.push({ name, ...(detail || {}) });
  console.log('通过 ' + name + (detail && detail.note ? '：' + detail.note : ''));
}
function fail(name, error) {
  steps.push({ name, status: 'failed', error: error && error.message ? error.message : String(error) });
  console.log('失败 ' + name + '：' + (error && error.message ? error.message : error));
}
function playwright() {
  for (const mod of [process.env.E2E_PLAYWRIGHT_PATH, 'playwright',
    path.join(__dirname, 'node_modules', 'playwright'),
    path.join(os.homedir(), '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright')].filter(Boolean)) {
    try { return require(mod); } catch (e) { if (e.code !== 'MODULE_NOT_FOUND') throw e; }
  }
  throw new Error('缺少 Playwright：先 npm install --prefix tests/e2e。套件不会自行下载。');
}
function freePort() {
  return new Promise((resolve, reject) => {
    const server = net.createServer();
    server.unref();
    server.on('error', reject);
    server.listen(0, '127.0.0.1', () => { const port = server.address().port; server.close(() => resolve(port)); });
  });
}
function listenLocal(server) {
  return new Promise((resolve, reject) => {
    server.on('error', reject);
    server.listen(0, '127.0.0.1', () => resolve(server.address().port));
  });
}
async function until(check, timeout = 30000, interval = 150) {
  const deadline = Date.now() + timeout;
  let last;
  while (Date.now() < deadline) {
    try { const value = await check(); if (value) return value; } catch (error) { last = error; }
    await sleep(interval);
  }
  throw new Error('等待超时' + (last && last.message ? '：' + last.message : ''));
}
async function visible(page, selector, timeout = 45000) {
  return until(async () => {
    const n = await page.locator(selector).count();
    return n > 0 && await page.locator(selector).first().isVisible();
  }, timeout);
}
async function api(route) {
  const response = await fetch(base + route, { headers: { 'X-Article-Studio': '1' } });
  if (!response.ok) throw new Error('GET ' + route + ' -> ' + response.status);
  return response.json();
}
function spawnSyncText(script, args) {
  const result = spawnSync(PYTHON, [script, ...args], { cwd: ROOT, encoding: 'utf8' });
  if (result.status !== 0) throw new Error('夹具失败：' + (result.stderr || result.stdout));
  return result.stdout;
}

/** 原生 markdown-it 渲染同一段原文，取出 href（属性解码 + 两次 unquote）。 */
function nativeHrefs() {
  const script = [
    'import sys, json',
    'sys.path.insert(0, sys.argv[1])',
    'import markdown_blocks as m',
    'from html.parser import HTMLParser',
    'from urllib.parse import unquote',
    'class R(HTMLParser):',
    '    def __init__(self, b):',
    '        super().__init__(convert_charrefs=True); self.urls = []; self.feed(b)',
    '    def handle_starttag(self, t, a):',
    '        a = dict(a)',
    '        if t in ("a", "img"):',
    '            self.urls.append(unquote(unquote(a.get("href" if t == "a" else "src", ""))))',
    'out = {}',
    'for raw in json.load(sys.stdin):',
    '    out[raw] = R(m._parser().render(raw)).urls',
    'print(json.dumps(out, ensure_ascii=False))',
  ].join('\n');
  const result = spawnSync(PYTHON, ['-c', script, ROOT], {
    cwd: ROOT, encoding: 'utf8', input: JSON.stringify(CASES.map((c) => c[1])),
  });
  if (result.status !== 0) throw new Error('原生 markdown-it 对照失败：' + result.stderr);
  return JSON.parse(result.stdout);
}

/** 浏览器读回的 href，与上面同一套规则（属性解码 + 两次 unquote）。 */
async function domHref(page) {
  // 只认正文里**用户这一段**的链接：页面别处也有 `#block-0`（旧版本回看面板），
  // 取错元素会读到空 href，所以先按可见性筛。
  const all = await page.locator('.article-body a').evaluateAll(
    (nodes) => nodes.map((n) => n.getAttribute('href')).filter(Boolean));
  const raw = all[0] || null;
  // 与复核/单测同一套口径：属性按 HTML 语义解码 + 两次 unquote。
  // 脚本必须 join 成**一个** argv：`spawnSync` 的数组元素是各自的参数。
  const script = [
    'import sys',
    'from html import unescape',
    'from urllib.parse import unquote',
    'print(unquote(unquote(unescape(sys.argv[1]))))',
  ].join('\n');
  const probe = spawnSync(PYTHON, ['-c', script, raw || ''], { encoding: 'utf8' });
  if (probe.status !== 0) throw new Error('href 解码失败：' + probe.stderr);
  return { raw, decoded: probe.stdout.trim(), all };
}

(async () => {
  const temp = fs.mkdtempSync(path.join(os.tmpdir(), 'studio-collision-e2e-'));
  const workspace = path.join(temp, 'workspace');
  const evidence = path.join(ROOT, 'coordination', 'evidence', 'content-markdown-collision-e2e',
    new Date().toISOString().replace(/[-:]/g, '').slice(0, 15) + '-' + process.pid);
  fs.mkdirSync(evidence, { recursive: true });
  fs.mkdirSync(workspace, { recursive: true });
  fs.mkdirSync(path.join(temp, 'home'), { recursive: true });
  let child = null, modelServer = null;
  const cleanup = { stopped: false, browserClosed: false };
  let slug = '';
  async function shot(page, name) {
    await page.screenshot({ path: path.join(evidence, name + '.png'), fullPage: true });
  }
  try {
    const { chromium } = playwright();
    const piModules = path.join(ROOT, 'pi-runtime/node_modules');
    if (!fs.existsSync(piModules)) {
      const official = process.env.ARTICLE_STUDIO_PI_MODULES;
      if (!official || !fs.existsSync(official)) throw new Error('副本缺少 pi-runtime/node_modules');
      fs.symlinkSync(official, piModules, 'dir');
    }
    // 本套件不发助手消息，模型端口只要占位、返回空回复即可。
    modelServer = http.createServer((req, res) => {
      let raw = '';
      req.on('data', (c) => { raw += c; });
      req.on('end', () => {
        res.writeHead(200, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ model: 'e2e-mock',
          choices: [{ message: { role: 'assistant', content: '{}' }, finish_reason: 'stop' }] }));
      });
    });
    const modelPort = await listenLocal(modelServer);

    const appPort = await freePort();
    if (RESERVED_PORTS.has(appPort) || appPort === modelPort) throw new Error('临时端口撞上保留端口');
    base = 'http://127.0.0.1:' + appPort;
    const config = path.join(temp, 'model.json');
    fs.writeFileSync(config, JSON.stringify({ model: 'e2e-mock',
      endpoint: 'http://127.0.0.1:' + modelPort + '/v1/chat/completions', no_api_key: true,
      context_tokens: 262144, max_output_tokens: 2048, temperature: 0, timeout_seconds: 30 }));
    const log = fs.openSync(path.join(evidence, 'server.log'), 'a');
    child = spawn(PYTHON, [path.join(__dirname, 'server_fixture.py'), '--workspace', workspace, '--port', String(appPort)],
      { cwd: ROOT, env: { ...process.env, PI_CODING_AGENT_DIR: path.join(temp, 'home'),
        ARTICLE_STUDIO_MODEL_CONFIG: config, E2E_APP_PORT: String(appPort), E2E_MODEL_PORT: String(modelPort),
        E2E_ALLOWED_PORTS: appPort + ',' + modelPort, E2E_NETWORK_LOG: path.join(temp, 'blocked-network.jsonl'),
        NODE_OPTIONS: '--require ' + path.join(__dirname, 'network_guard.cjs') },
        detached: true, stdio: ['ignore', log, log] });
    fs.closeSync(log);
    await until(async () => {
      if (child.exitCode !== null) throw new Error('副本工作台启动失败，见 server.log');
      return (await fetch(base + '/api/library')).ok;
    }, 40000);

    const slugs = JSON.parse(spawnSyncText(path.join(__dirname, 'content_markdown_fixture.py'), [workspace]));
    slug = slugs.trim;
    const want = nativeHrefs();

    browser = await chromium.launch({ channel: process.env.E2E_BROWSER || 'chrome',
      headless: !process.env.E2E_HEADED, args: ['--disable-background-networking'] });
    const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
    const pageErrors = [];
    page.on('pageerror', (e) => pageErrors.push(String(e && e.message || e)));

    const rows = [];
    for (const [name, raw] of CASES) {
      await page.goto(base + '/?article=' + encodeURIComponent(slug));
      await visible(page, '.text-block');
      const head = (await api('/api/article/' + encodeURIComponent(slug))).version.id;
      await page.locator('#edit').click();
      await page.locator('.text-block').first().fill(raw);
      await page.locator('#save').click();
      await page.locator('#summary').fill('碰撞复验 ' + name);
      await page.locator('#confirm-save').click();
      await until(async () => (await api('/api/article/' + encodeURIComponent(slug))).version.id !== head);
      await page.reload();
      await visible(page, '.article-body a');
      const got = await domHref(page);
      const bodyHtml = await page.locator('.article-body').innerHTML();
      const row = { name, input: raw, expected: want[raw], dom_raw: got.raw, actual: got.decoded,
        equal: (want[raw] || []).length > 0 && got.decoded === want[raw][0],
        body_html: bodyHtml.slice(0, 1200) };
      rows.push(row);
      assert.ok((want[raw] || []).length > 0, '判据无效：这条原文本身没有链接');
      assert.deepEqual([got.decoded], want[raw], name + ' 的链接目标与原生 markdown-it 不一致\n'
        + 'raw=' + JSON.stringify(got.raw) + ' all=' + JSON.stringify(got.all)
        + '\nDOM: ' + bodyHtml.slice(0, 800));
      if (name === 'seal-digit-chrome') await shot(page, '01-封层实体碰撞-编辑保存回写');
    }
    record('八条实体碰撞输入：编辑保存后 href 与原生 markdown-it 逐字相等',
      { cases: rows.length, note: '截图 01（Chrome 复核原始复现路径）' });
    assert.deepEqual(pageErrors, [], '页面不应有脚本错误');
    record('无页面脚本错误', { note: '0 pageerror' });
    fs.writeFileSync(path.join(evidence, 'collision.json'), JSON.stringify({ rows, pageErrors }, null, 2));
    fs.writeFileSync(path.join(evidence, 'results.json'), JSON.stringify({ steps, slug, pageErrors }, null, 2));
  } catch (error) {
    fail('碰撞复验', error);
    fs.writeFileSync(path.join(evidence, 'results.json'), JSON.stringify({ steps, slug }, null, 2));
  } finally {
    try { if (browser) { await Promise.race([browser.close(), sleep(10000)]); cleanup.browserClosed = true; } } catch { cleanup.browserClosed = false; }
    try {
      if (modelServer) await new Promise((r) => modelServer.close(r));
      if (child) { await stopChild(child, sleep); if (alive(child)) { forceStopSync(child); } cleanup.stopped = true; }
    } catch (error) { cleanup.error = String(error && error.message || error); }
    try { fs.rmSync(temp, { recursive: true, force: true }); } catch { /* 证据已写出 */ }
    fs.writeFileSync(path.join(evidence, 'result.txt'),
      steps.map((s) => (s.status === 'failed' ? '失败 ' : '通过 ') + s.name + (s.error ? '：' + s.error : '')).join('\n') + '\n');
    fs.writeFileSync(path.join(evidence, 'cleanup.json'), JSON.stringify(cleanup, null, 2));
    console.log('证据目录 ' + evidence);
  }
  process.exit(steps.some((s) => s.status === 'failed') ? 1 : 0);
})();