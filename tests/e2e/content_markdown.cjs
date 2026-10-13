'use strict';
/**
 * 正文 Markdown 转换与「按 Markdown 重新排版」隔离联调（独立运行；登记为 run.cjs 第 30 例时以子进程方式接入）。
 *
 * 真实 Chrome + 副本工作台 + 临时库 + 假模型，跑完整用户路径：
 *   1. 打开一篇「修复前导入」的作品：正文里 ## 标题、**粗体**、- 列表仍是字面量（截图留证）
 *   2. 编辑态点「按 Markdown 重新排版」→ 新版本 → 标题/粗体/列表正确渲染（截图）
 *      旧版本仍在版本记录里，可回退
 *   3. 创作助手：用黄总原话请求 → 模型自己判断并改稿 → 新版本（截图）
 *   4. 创作助手：「这段太啰嗦了帮我精简」→ 新版本，旧版本仍在（截图）
 *
 * 用法（在副本根目录）：node tests/e2e/content_markdown.cjs
 * 环境变量：E2E_HEADED=1 显示窗口；E2E_BROWSER=chromium 用 Playwright 自带 Chromium。
 * 由 run.cjs 作为第 30 例接入时，父套件传 E2E_CM_MANIFEST（身份清单落盘路径）与
 * E2E_CM_RUN_ID（本轮随机标识）；单独运行不带这两个变量，行为不变。
 */
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const http = require('node:http');
const net = require('node:net');
const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const { spawn, spawnSync } = require('node:child_process');
const { stopChild, forceStopSync, alive } = require('./stop_child.cjs');
const { isAssistant } = require('./assistant_model.cjs');

const ROOT = path.resolve(__dirname, '../..');
process.chdir(ROOT);
const RESERVED_PORTS = new Set([8766, 3000, 18766]);
const PYTHON = path.resolve(ROOT, '../article-importer/.venv/bin/python');
const HUANG_REFLOW = '看一下这个文章，很多地方本来是要加粗的…是 Markdown 格式，我们这边没有做好，要改一改，标题也应该是标题的格式';
const HUANG_WHOLE = '你要通篇看文章，然后处理啊…是要你自己判断啊';
const TRIM = '这段太啰嗦了帮我精简';
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
let base = '', browser = null;
const steps = [];
function gate(name) {
  try { fs.appendFileSync(process.env.E2E_GATE_LOG || '/tmp/cm-gate.log',
    new Date().toISOString().slice(11, 23) + ' ' + name + '\n'); } catch { /* 取证失败不影响主流程 */ }
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

// 发一条助手消息：先等模型配置真的加载完、面板确实打开、发送按钮可用，
// 否则在配置还没回来的窗口里点发送会被静默吞掉，表现为「模型一直没被调用」。
async function sendToAssistant(page, message) {
  gate('send:begin ' + message.slice(0, 10));
  // 上一轮没真正跑完时，服务端会拒收下一条（助手一次只处理一条），
  // 页面上表现为消息根本没进聊天记录。先等它空下来。
  await until(async () => !(await api('/api/assistant')).active_job, 60000);
  gate('send:idle-ok');
  if (await page.locator('#assistant-panel').evaluate((el) => el.hidden))
    await page.locator('#assistant-toggle').click();
  gate('send:panel-open');
  await visible(page, '#assistant-input');
  gate('send:input-visible');
  await until(async () => !(await page.locator('#assistant-model').innerText()).includes('连接模型中'), 30000);
  gate('send:model-ready ' + (await page.locator('#assistant-model').innerText()).slice(0, 20));
  await until(async () => await page.locator('#assistant-send').isEnabled(), 30000);
  gate('send:enabled');
  await page.locator('#assistant-input').fill(message);
  await page.locator('#assistant-send').click();
  gate('send:clicked');
  try {
    await until(async () => (await page.locator('#assistant-message-list').textContent()).includes(message.slice(0, 12)), 30000);
  } catch (error) {
    fs.appendFileSync(path.join(evidence, '99-发送失败现场.txt'),
      ['面板：' + await page.locator('#assistant-message-list').textContent().catch(() => '(取不到)'),
       '状态：' + await page.locator('#assistant-status').innerText().catch(() => '(取不到)'),
       '提示：' + await page.locator('#toast').innerText().catch(() => '(取不到)'),
       '输入框：' + JSON.stringify(await page.locator('#assistant-input').inputValue().catch(() => '(取不到)')),
       '配置：' + JSON.stringify(await api('/api/assistant')).slice(0, 500),
       '状态机：' + JSON.stringify(await page.evaluate(() => ({
         busy: window.assistantState && assistantState.busy,
         loaded: window.assistantState && assistantState.loaded,
         active: window.assistantState && assistantState.active,
         open: window.assistantState && assistantState.open,
         mediaLoading: window.composerMedia && composerMedia.loading,
         dirty: window.state && state.dirty })))].join('\n') + '\n');
    throw error;
  }
}
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
    server.listen(0, '127.0.0.1', () => {
      const port = server.address().port;
      server.close(() => resolve(port));
    });
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
function textOf(content) {
  if (typeof content === 'string') return content;
  if (Array.isArray(content)) return content.map((part) => part && (part.text || part.content) || '').join('');
  return '';
}
function userPrompt(messages) {
  const users = (messages || []).filter((m) => m.role === 'user');
  return textOf(users.at(-1) && users.at(-1).content);
}
function contextOf(messages) {
  const raw = userPrompt(messages);
  const marker = '[工作台当前上下文，仅作定位数据]\n';
  const at = raw.lastIndexOf(marker);
  if (at < 0) return {};
  try { return JSON.parse(raw.slice(at + marker.length)); } catch { return {}; }
}
function calledNames(messages) {
  const names = [];
  for (const message of messages || [])
    for (const call of message.tool_calls || []) names.push((call.function || {}).name || '');
  return names;
}
function readVersion(messages) {
  let id = '';
  for (const message of messages || []) {
    if (message.role !== 'assistant') continue;
    for (const call of message.tool_calls || [])
      if (((call.function || {}).name) === 'read_work') id = call.id;
  }
  if (!id) return '';
  const tool = (messages || []).slice().reverse().find((m) => m.role === 'tool' && m.tool_call_id === id);
  if (!tool) return '';
  try { return JSON.parse(textOf(tool.content)).version || ''; } catch { return ''; }
}

/** 假模型：先 read_work 拿版本，再按黄总那句话真的调改稿工具。 */
const TRIMMED = '<p>一个 <strong>96.5GB</strong> 体积的混合专家模型，一张 <strong>48GB</strong> 显存的单显卡。</p>'
  + '<h2>01. 账本与底牌</h2><p>正文一段 <strong>加粗</strong> 文字。</p>';
// 只看本轮（最后一条 user 之后）发生过的工具调用：同一段对话里上一轮调过什么，
// 不能让这一轮直接跳过 read_work 或以为工具已经发过。
function namesSinceLastUser(messages) {
  const list = messages || [];
  let at = -1;
  for (let i = list.length - 1; i >= 0; i--) if (list[i].role === 'user') { at = i; break; }
  const names = [];
  for (const message of list.slice(at + 1)) for (const call of message.tool_calls || []) names.push((call.function || {}).name || '');
  return names;
}

function assistantReply(body) {
  const messages = body.messages || [];
  const names = namesSinceLastUser(messages);
  const context = contextOf(messages);
  const slug = context.slug || '';
  const prompt = userPrompt(messages);
  if (!names.includes('read_work')) return { tool: { name: 'read_work', arguments: { slug } } };
  const base = readVersion(messages) || context.head || context.viewed_version || '';
  // 工具只发一次：第二轮必须给正文收尾，否则助手会一直循环调用同一个工具。
  if (prompt.includes('Markdown') || prompt.includes('通篇') || prompt.includes('处理')) {
    if (names.includes('reflow_markdown')) return { content: '已按 Markdown 重新排版整篇并保存为新版本；旧版本在版本记录里，可以随时回退。' };
    return { tool: { name: 'reflow_markdown', arguments: { slug, base_version: base, summary: '按 Markdown 重新排版整篇' } } };
  }
  if (prompt.includes('精简')) {
    if (names.includes('update_text')) return { content: '已把啰嗦的一段精简，保存为新版本；旧版本可以回退。' };
    return { tool: { name: 'update_text', arguments: { slug, base_version: base, summary: '精简啰嗦段落',
      edits: [{ index: 0, html: TRIMMED }] } } };
  }
  return { content: '这一轮没有匹配到夹具指令。' };
}

(async () => {
  const temp = fs.mkdtempSync(path.join(os.tmpdir(), 'studio-markdown-e2e-'));
  const workspace = path.join(temp, 'workspace');
  const evidence = path.join(ROOT, 'coordination', 'evidence', 'content-markdown-e2e',
    new Date().toISOString().replace(/[-:]/g, '').slice(0, 15) + '-' + process.pid);
  fs.mkdirSync(evidence, { recursive: true });
  fs.mkdirSync(workspace, { recursive: true });
  fs.mkdirSync(path.join(temp, 'home'), { recursive: true });
  const networkLog = path.join(temp, 'blocked-network.jsonl');
  let child = null, modelServer = null;
  const cleanup = { stopped: false, browserClosed: false };
  let slug = '', slugs = {}, writes = [];
  // 登记清单（run.cjs 第 30 例接入时经 E2E_CM_MANIFEST/E2E_CM_RUN_ID 传入）：把临时目录
  //（含 realpath）、本轮 runId、detached 副本工作台的 PID 与真实启动时刻（ps -o lstart=）、
  // fixture 与 workspace 绝对路径报给父套件。父套件在本套件被 SIGKILL 后按清单兜底回收，
  // 但必须核对全部身份字段一致才动手，PID 复用或清单错指/陈旧时拒杀；正常收尾与 SIGTERM
  // 自回收不依赖它。只登记自己启动的进程。
  const manifestPath = process.env.E2E_CM_MANIFEST || '';
  const runId = process.env.E2E_CM_RUN_ID || crypto.randomBytes(6).toString('hex');
  let manifestError = null;
  const serviceLstart = (pid) => {
    if (!pid) return null;
    try { return spawnSync('ps', ['-ww', '-o', 'lstart=', '-p', String(pid)], { encoding: 'utf8' }).stdout.trim() || null; }
    catch { return null; }
  };
  function writeManifest() {
    if (!manifestPath) return;
    try {
      fs.writeFileSync(manifestPath, JSON.stringify({ temp, tempReal: fs.realpathSync(temp),
        servicePid: child ? child.pid : null, serviceLstart: child ? serviceLstart(child.pid) : null,
        fixturePath: path.join(__dirname, 'server_fixture.py'), workspace, runId, suitePid: process.pid,
        updatedAt: new Date().toISOString() }, null, 2));
      manifestError = null;
    } catch (error) { manifestError = '清单写入失败：' + (error && error.message ? error.message : error); }
  }
  writeManifest();
  // 回收本套件全部资源：浏览器、detached 副本工作台、进程内模型服务、临时目录。
  // 所有入口（正常 finally 与 SIGTERM 处理器）共用同一个 finalizePromise：谁先进入谁执行，
  // 后到者 await 同一个 Promise 等它完成，而不是跳过——否则先到者会在中途被组 SIGKILL 打断，
  // 结果就落不了盘。result.txt / cleanup.json 在 runFinalize 内落盘，任何退出路径都先落盘。
  let finalizePromise = null;
  function finalize(extra) {
    if (!finalizePromise) finalizePromise = runFinalize(extra);
    return finalizePromise;
  }
  async function runFinalize(extra) {
    try { if (browser) { await Promise.race([browser.close(), sleep(10000)]); cleanup.browserClosed = true; } } catch { cleanup.browserClosed = false; }
    try {
      if (modelServer) await new Promise((r) => modelServer.close(r));
      if (child) { await stopChild(child, sleep); if (alive(child)) { forceStopSync(child); } cleanup.stopped = true; }
    } catch (error) { cleanup.error = String(error && error.message || error); }
    try { fs.rmSync(temp, { recursive: true, force: true }); } catch { /* 证据已写出，临时目录清理失败不影响结论 */ }
    fs.writeFileSync(path.join(evidence, 'result.txt'),
      steps.map((s) => (s.status === 'failed' ? '失败 ' : '通过 ') + s.name + (s.error ? '：' + s.error : '')).join('\n') + '\n');
    fs.writeFileSync(path.join(evidence, 'cleanup.json'), JSON.stringify(cleanup, null, 2));
    if (extra) fs.writeFileSync(path.join(evidence, 'results-terminated.json'),
      JSON.stringify({ slugs, steps, writes, cleanup, manifestError, ...extra }, null, 2));
    console.log('证据目录 ' + evidence);
  }
  // 父套件超时/取消时对本组发 SIGTERM：就地回收全部资源（含 detached 服务、浏览器、临时目录）
  // 并落盘结果，再按组自杀，确保同组浏览器等进程不残留。父套件会发组+直接两份 SIGTERM，
  // Node 会各派发一次：finalize 共享同一个 Promise，重复信号只是再次 await 已在执行的收尾，
  // 不会提前组自杀打断第一次清理。
  process.on('SIGTERM', () => {
    finalize({ terminated: 'SIGTERM' })
      .catch(() => {})
      .then(() => { try { process.kill(-process.pid, 'SIGKILL'); } catch { process.exit(1); } });
  });
  async function shot(page, name) {
    const file = path.join(evidence, name + '.png');
    await page.screenshot({ path: file, fullPage: true });
    return file;
  }
  try {
    const { chromium } = playwright();
    const piModules = path.join(ROOT, 'pi-runtime/node_modules');
    if (!fs.existsSync(piModules)) {
      const official = process.env.ARTICLE_STUDIO_PI_MODULES;
      if (!official || !fs.existsSync(official)) throw new Error('副本缺少 pi-runtime/node_modules，无法启动助手');
      fs.symlinkSync(official, piModules, 'dir');
    }

    modelServer = http.createServer(async (req, res) => {
      let raw = '';
      for await (const chunk of req) raw += chunk;
      try {
        const body = JSON.parse(raw);
        const system = (body.messages || []).filter((m) => m.role === 'system')
          .map((m) => JSON.stringify(m.content)).join('');
        const chat = !system.includes('即时分析') && !JSON.stringify(body.messages || '').includes('image_url') && isAssistant(body);
        if (!chat) {
          res.writeHead(200, { 'Content-Type': 'application/json' });
          res.end(JSON.stringify({ model: 'e2e-mock', choices: [{ message: { role: 'assistant', content: '{}' }, finish_reason: 'stop' }] }));
          return;
        }
        const reply = assistantReply(body);
        fs.appendFileSync(path.join(evidence, 'model-calls.log'),
          JSON.stringify({ at: new Date().toISOString().slice(11, 23), stream: !!body.stream,
            prompt: userPrompt(body.messages).slice(0, 60), called: calledNames(body.messages),
            sent: (reply.tools || (reply.tool ? [reply.tool] : [])).map((c) => c.name) }) + '\n');
        const list = reply.tools || (reply.tool ? [reply.tool] : null);
        // 只在这里统一成 OpenAI 的 tool_calls 形状，SSE 与 JSON 两条分支共用同一份，
        // 免得两处各拼一次、其中一处拼错就把整轮助手带偏。
        const calls = list ? list.map((call, i) => ({
          id: 'call-' + i, type: 'function',
          function: { name: call.name, arguments: JSON.stringify(call.arguments) } })) : null;
        const message = calls
          ? { role: 'assistant', content: null, tool_calls: calls }
          : { role: 'assistant', content: reply.content };
        const chunk = (delta, finish) => 'data: ' + JSON.stringify({ id: 'e2e', object: 'chat.completion.chunk', created: 1,
          model: 'e2e-mock', choices: [{ index: 0, delta, finish_reason: finish }],
          usage: { prompt_tokens: 10, completion_tokens: 10, total_tokens: 20 } }) + '\n\n';
        if (body.stream) {
          res.writeHead(200, { 'Content-Type': 'text/event-stream' });
          if (calls) {
            for (const [index, call] of calls.entries())
              res.write(chunk({ tool_calls: [{ index, id: call.id, type: 'function', function: call.function }] }, null));
            res.write(chunk({}, 'tool_calls'));
          } else {
            res.write(chunk({ role: 'assistant', content: reply.content }, null));
            res.write(chunk({}, 'stop'));
          }
          res.end('data: [DONE]\n\n');
        } else {
          res.setHeader('Content-Type', 'application/json');
          res.end(JSON.stringify({ model: 'e2e-mock', choices: [{ message, finish_reason: calls ? 'tool_calls' : 'stop' }],
            usage: { prompt_tokens: 10, completion_tokens: 10, total_tokens: 20 } }));
        }
      } catch (error) {
        // SSE 已经发过头就不能再写 500，否则整个夹具进程会崩在这里，掩盖真正的失败。
        if (!res.headersSent) res.writeHead(500);
        if (!res.writableEnded) res.end(JSON.stringify({ error: String(error) }));
        fs.appendFileSync(path.join(evidence, 'model-errors.log'), String(error && error.stack || error) + '\n');
      }
    });
    const modelPort = await listenLocal(modelServer);

    const appPort = await freePort();
    if (RESERVED_PORTS.has(appPort) || appPort === modelPort) throw new Error('临时端口撞上保留端口');
    base = 'http://127.0.0.1:' + appPort;
    const config = path.join(temp, 'model.json');
    fs.writeFileSync(config, JSON.stringify({ model: 'e2e-mock', endpoint: 'http://127.0.0.1:' + modelPort + '/v1/chat/completions',
      no_api_key: true, context_tokens: 262144, max_output_tokens: 2048, temperature: 0, timeout_seconds: 30 }));
    const log = fs.openSync(path.join(evidence, 'server.log'), 'a');
    child = spawn(PYTHON, [path.join(__dirname, 'server_fixture.py'), '--workspace', workspace, '--port', String(appPort)],
      { cwd: ROOT, env: { ...process.env, PI_CODING_AGENT_DIR: path.join(temp, 'home'),
        ARTICLE_STUDIO_MODEL_CONFIG: config, E2E_APP_PORT: String(appPort), E2E_MODEL_PORT: String(modelPort),
        E2E_ALLOWED_PORTS: appPort + ',' + modelPort, E2E_NETWORK_LOG: networkLog,
        NODE_OPTIONS: '--require ' + path.join(__dirname, 'network_guard.cjs') }, detached: true, stdio: ['ignore', log, log] });
    fs.closeSync(log);
    writeManifest();
    await until(async () => {
      if (child.exitCode !== null) throw new Error('副本工作台启动失败，见 server.log');
      const r = await fetch(base + '/api/library');
      return r.ok;
    }, 40000);

    // 造一篇「修复前导入」的作品：正文里 Markdown 全是字面量。
    slugs = JSON.parse(spawnSyncText(path.join(__dirname, 'content_markdown_fixture.py'), [workspace]));
    slug = slugs.button;
    const before = await api('/api/article/' + encodeURIComponent(slug));
    record('夹具就绪', { slug, head: before.version.id, blocks: before.blocks.length,
      note: '导入时未转换，正文保留 ** 与 ## 字面量' });

    browser = await chromium.launch({ channel: process.env.E2E_BROWSER || 'chrome', headless: !process.env.E2E_HEADED,
      args: ['--disable-background-networking', '--disable-component-update'] });
    const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
    const pageErrors = [];
    page.on('pageerror', (e) => pageErrors.push(String(e && e.message || e)));
    page.on('request', (r) => {
      const u = new URL(r.url());
      if (u.origin === base && /\/(reflow|save|restore)\//.test(u.pathname))
        writes.push(new Date().toISOString().slice(11, 23) + ' ' + r.method() + ' ' + u.pathname);
    });

    // 1. 修复前：字面量确实显示给用户。
    await page.goto(base + '/?article=' + encodeURIComponent(slug));
    await visible(page, '.text-block');
    const rawText = await page.locator('.article-body').innerText();
    assert.match(rawText, /\*\*96\.5GB\*\*/, '修复前正文应仍有字面量 **');
    assert.match(rawText, /^#+\s*01\./m, '修复前正文应仍有字面量 ## 标题');
    assert.equal(await page.locator('.article-body h2').count(), 0, '修复前不应有 h2');
    await shot(page, '01-修复前-字面量');
    record('修复前字面量如实显示', { note: '截图 01' });

    // 2. 编辑态点「按 Markdown 重新排版」。
    await until(async () => (await page.locator('#crumb').innerText()).trim() !== '正在读取', 20000);
    await page.locator('#edit').click();
    await visible(page, '#reflow-markdown');
    assert.equal(await page.locator('#reflow-markdown').isVisible(), true, '按钮应当可见');
    await shot(page, '02-编辑态-重新排版按钮');
    await page.locator('#reflow-markdown').click();
    await until(async () => (await api('/api/article/' + encodeURIComponent(slug))).version.id !== before.version.id, 30000);
    const afterButton = await api('/api/article/' + encodeURIComponent(slug));
    assert.equal(afterButton.versions.length, 2, '旧版本必须保留');
    assert.ok(afterButton.versions.some((v) => v.id === before.version.id), '旧版本仍在，可回退');
    assert.match(afterButton.versions[0].summary, /Markdown/, '版本备注写明做了什么');
    await page.reload();
    await visible(page, '.text-block');
    assert.ok(await page.locator('.article-body h2').count() >= 1, '新版本应有 h2');
    assert.ok(await page.locator('.article-body strong').count() >= 1, '新版本应有 strong');
    assert.ok(await page.locator('.article-body ul li').count() >= 1, '新版本应有列表');
    assert.doesNotMatch(await page.locator('.article-body').innerText(), /\*\*/, '新版本不应再有字面量 **');
    await shot(page, '03-按钮重排后-正确渲染');
    record('按钮重排生成新版本', { version: afterButton.version.id, note: '截图 03' });

    // 3. 创作助手：黄总两句原话 → 每句各自一篇未重排过的作品。
    const cases = [[HUANG_REFLOW, 'huang_reflow'], [HUANG_WHOLE, 'huang_whole']];
    for (const [index, [message, key]] of cases.entries()) {
      const target = slugs[key];
      gate('turn' + index + ':goto ' + key);
      await page.goto(base + '/?article=' + encodeURIComponent(target));
      gate('turn' + index + ':loaded');
      await visible(page, '.text-block');
      gate('turn' + index + ':text-block');
      const head = (await api('/api/article/' + encodeURIComponent(target))).version.id;
      await sendToAssistant(page, message);
      let next = null;
      try {
        next = await until(async () => {
          const detail = await api('/api/article/' + encodeURIComponent(target));
          return detail.version.id !== head ? detail : null;
        }, 90000);
      } catch (error) {
        fs.writeFileSync(path.join(evidence, '99-助手面板-' + key + '.txt'),
          ['面板：' + await page.locator('#assistant-message-list').textContent().catch(() => '(取不到)'),
           '状态：' + await page.locator('#assistant-status').innerText().catch(() => '(取不到)'),
           '提示：' + await page.locator('#toast').innerText().catch(() => '(取不到)'),
           '配置：' + JSON.stringify(await api('/api/assistant')).slice(0, 400)].join('\n'));
        throw error;
      }
      assert.ok(next.versions.some((v) => v.id === head), '助手改稿后旧版本仍在');
      assert.match(next.versions[0].summary, /重新排版/, '回执写明按 Markdown 重新排版');
      assert.ok(await next.blocks.some((b) => (b.html || '').includes('<h2>')), '新版本应有 h2');
      assert.doesNotMatch(next.blocks[0].text, /\*\*/, '新版本不应再有字面量 **');
      record('助手按黄总原话改稿 ' + (index + 1), { article: target, version: next.version.id,
        summary: next.versions[0].summary, note: '旧版本 ' + head + ' 保留' });
      await page.reload();
      await visible(page, '.text-block');
      assert.ok(await page.locator('.article-body h2').count() >= 1, '助手改稿后新版本应有 h2');
      if (index === 0) {
        // 回执文案从助手自己的接口读，不依赖面板此刻是展开还是收起。
        const receipt = await until(async () => {
          const text = JSON.stringify(await api('/api/assistant'));
          return text.includes('重新排版') ? text : null;
        }, 90000);
        assert.ok(receipt.includes('可以随时回退'), '回执要说明旧版本可回退');
        await page.locator('#assistant-toggle').click();
        await visible(page, '#assistant-message-list');
        await shot(page, '04-助手黄总原话-新版本');
        record('助手回执截图', { note: '截图 04' });
      }
    }

    // 4. 追加合同要求：精简指令也要生成新版本，旧版本仍在。
    const target = slugs.trim;
    await page.goto(base + '/?article=' + encodeURIComponent(target));
    await visible(page, '.text-block');
    const head = (await api('/api/article/' + encodeURIComponent(target))).version.id;
    await sendToAssistant(page, TRIM);
    const trimmed = await until(async () => {
      const detail = await api('/api/article/' + encodeURIComponent(target));
      return detail.version.id !== head ? detail : null;
    }, 90000);
    assert.ok(trimmed.versions.some((v) => v.id === head), '精简后旧版本仍在');
    assert.match(trimmed.versions[0].summary, /精简/, '回执写明精简');
    await page.reload();
    await visible(page, '.text-block');
    await shot(page, '05-助手精简-新版本');
    record('助手精简生成新版本', { article: target, version: trimmed.version.id, note: '旧版本 ' + head + ' 保留' });

    // 5. 版本记录里旧版本还在，可以打开回看（回退的前提）。
    await page.locator('#history-toggle').click();
    await visible(page, '.version-entry');
    const entries = await page.locator('.version-entry').count();
    assert.ok(entries >= 2, '版本记录应列出旧版本，实际 ' + entries);
    await page.locator('.version-entry').last().click();
    await until(async () => /\*\*/.test(await page.locator('.article-body').innerText()), 20000);
    await shot(page, '06-版本记录可回看旧版');
    record('版本记录完整且旧版可回看', { versions: entries, note: '截图 06' });

    assert.deepEqual(pageErrors, [], '页面不应有脚本错误');
    record('无页面脚本错误', { note: '0 pageerror' });

    fs.writeFileSync(path.join(evidence, 'results.json'), JSON.stringify({ slugs, steps, pageErrors, writes }, null, 2));
  } catch (error) {
    fail('联调', error);
    if (browser) {
      try {
        await shot(await browser.newPage({ viewport: { width: 1280, height: 900 } }), '99-失败时现场').catch(() => {});
        const pages = browser.contexts().flatMap((c) => c.pages());
        for (const p of pages.slice(0, 1)) {
          fs.writeFileSync(path.join(evidence, '99-失败页面文本.txt'),
            (await p.title()) + '\n---\n' + (await p.evaluate(() => document.body.innerText.slice(0, 600))));
        }
      } catch { /* 取不到现场不影响主结论 */ }
    }
    fs.writeFileSync(path.join(evidence, 'results.json'), JSON.stringify({ slugs, steps, writes }, null, 2));
  } finally {
    await finalize();
  }
  process.exit(steps.some((s) => s.status === 'failed') ? 1 : 0);
})();

function spawnSyncText(script, args) {
  const result = require('node:child_process').spawnSync(PYTHON, [script, ...args], { cwd: ROOT, encoding: 'utf8' });
  if (result.status !== 0) throw new Error('夹具失败：' + (result.stderr || result.stdout));
  return result.stdout;
}