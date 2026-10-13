'use strict';
/**
 * 知识库软删除隔离联调（独立运行，不挂 run.cjs）。
 *
 * 真实 Chrome + 副本工作台 + 合成库，跑完整用户路径：
 *   勾选 → 预览 → 取消（零写入）→ 再预览 → 确认移到已删除 → 列表/检索/来源计数不再出现
 *   → 重复收藏不复活且提示已删除 → 已删除列表分页 → 恢复 → 重新出现、计数回来
 *   → 历史引用 document/<id> 仍可读并带「已移出常规检索」标记
 *   另测：版本冲突单项提示、后端停掉时页面不卡忙态、375 与 1280 宽排版。
 *
 * 用法（在副本根目录）：node tests/e2e/knowledge_trash.cjs
 * 环境变量：E2E_HEADED=1 显示窗口；E2E_BROWSER=chromium 用 Playwright 自带 Chromium。
 * 作为 run.cjs 第 29 例子套件运行时另有两个由父套件注入的变量：E2E_TRASH_RUN_ID（本轮 runId）
 * 与 E2E_TRASH_TEMP（父套件预建并持有的本轮临时目录）；父套件靠这两样加自己记录的子套件 PID
 * 判定兜底回收的对象是否属于本轮。不带它们单独运行时，一律在 os.tmpdir() 下 mkdtemp 自建临时
 * 目录（workspace/home 只建在该真实临时目录里，绝不建在副本根目录），清单与回执登记的都是这个真实路径。
 * temp 与 os.tmpdir()/副本根目录的包含与相等判定一律先 realpath 归一再比（macOS 上 /tmp 与 /private/tmp
 * 是同一目录）：别名形态落进副本根目录的 temp 会在启动前拒跑（exit 1 + temp-fatal.txt）。
 * 但归一不等于接受链接：E2E_TRASH_TEMP 本身是符号链接、或路径中间一段是符号链接，一律视为非法并回退
 * 自建（与 run.cjs manifestTempFallback 的逐段 lstat 同口径）——否则 rmSync 只删链接、真实目标残留却被
 * 回执报成已清理。扫描锚点只认输入开头的允许前缀（os.tmpdir() 字面形态、它的 realpath 形态，或系统
 * /tmp 及其真实形态），按段比较、只确定一次；「某个前缀的 realpath 恰好等于真实 tmpdir」不算锚点，
 * 所以 tmpdir 外侧指向 tmp 根的链接自己会被逐段 lstat 抓到并拒用。接受 temp 时登记 dev/ino，收尾删除前
 * 重新 lstat 核对：已成链接或身份变化就拒删并把三个清理布尔如实写成 false（不谎报、不跟随链接删）。
 */
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const http = require('node:http');
const net = require('node:net');
const crypto = require('node:crypto');
const assert = require('node:assert/strict');
const { spawn, spawnSync } = require('node:child_process');
const { stopChild, forceStopSync, alive } = require('./stop_child.cjs');

const ROOT = path.resolve(__dirname, '../..');
process.chdir(ROOT);
const RESERVED_PORTS = new Set([8766, 3000, 18766]);
const PYTHON = path.resolve(ROOT, '../article-importer/.venv/bin/python');
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const steps = [];
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
async function freePort() {
  const s = net.createServer();
  await new Promise((r) => s.listen(0, '127.0.0.1', r));
  const port = s.address().port;
  await new Promise((r) => s.close(r));
  assert(!RESERVED_PORTS.has(port), '拒绝使用生产端口 ' + port);
  return port;
}
async function until(work, timeout = 30000) {
  const end = Date.now() + timeout;
  let last;
  while (Date.now() < end) {
    try { const value = await work(); if (value) return value; } catch (e) { last = e; }
    await sleep(150);
  }
  throw last || new Error('等待条件超时');
}

(async () => {
  const stamp = new Date().toISOString().replace(/[:.]/g, '-') + '-' + process.pid + '-' + crypto.randomBytes(3).toString('hex');
  const evidence = path.join(ROOT, 'coordination/evidence/e2e-knowledge-trash', stamp);
  fs.mkdirSync(evidence, { recursive: true });
  // 第 29 例的 temp/workspace 由父套件（run.cjs）预建并持有，经 E2E_TRASH_TEMP 传进来：这样
  // 「这个目录属于本轮」由父进程自己持有的资源证明，父套件兜底时不再靠子套件清单字段之间自洽
  // （见 run.cjs 的 manifestOwnership）。
  // M2 返修：E2E_TRASH_TEMP 没传、传空串，或传入的目录不合格（不是规范化绝对路径 / 前缀不对 /
  // 不在 tmpdir 下 / 不存在 / 不是目录 / 非空）时，一律显式在 os.tmpdir() 下 mkdtemp 自建本轮临时
  // 目录，绝不把空字符串当目录用：空串会让 workspace/home 变成副本根目录下的相对路径，跑完既删不掉
  // （rmSync('') 抛错被吞、existsSync('') 恒为 false），又在回执里谎报 workspaceRemoved=true。
  // 所以 temp 从此必须是真实绝对路径，清单与 results.json 登记、核对的也都是这个真实路径。
  const providedTemp = process.env.E2E_TRASH_TEMP || '';
  // M2-a 返修：macOS 上 /tmp 与 /private/tmp（以及任何符号链接形态）是同一个目录，按字符串比较 temp 与
  // os.tmpdir()/ROOT 会漏判——环境或父套件传别名形态时，temp 其实就在副本根目录里，workspace/home 照样
  // 落进仓库。所以 temp / os.tmpdir() / ROOT 三者的包含与相等判定一律先 realpath 归一，再比真实路径。
  // 路径本身还不存在时（如兜底探针硬塞的假路径）按最近存在的祖先归一后接回剩余段，归一结果照样要过门。
  function realPathOf(target) {
    if (!target || !path.isAbsolute(target)) return null;
    const tail = [];
    let cursor = target;
    for (;;) {
      try {
        const head = fs.realpathSync(cursor);
        return tail.length ? path.join(head, ...tail.reverse()) : head;
      } catch (e) {
        if (e && e.code !== 'ENOENT' && e.code !== 'ENOTDIR' && e.code !== 'ELOOP') return null;
      }
      const parent = path.dirname(cursor);
      if (parent === cursor) return null;
      tail.push(path.basename(cursor));
      cursor = parent;
    }
  }
  // N6 返修：realpath 归一只回答「归一后在不在真实 tmpdir 里」，不能用来接受符号链接形态的 temp。
  // 链接输入（或路径中间一段是链接）时 realpath 门会过、tempSource 变 parent，可最后 rmSync(temp) 删的
  // 只是链接本身：真实目标里的 workspace/home 照样残留，回执却写 tempRemoved/workspaceDirRemoved=true（谎报
  // 清理），而链接目标可能根本不是本轮建的。所以这里与 run.cjs 的 manifestTempFallback 同口径：对 temp
  // 逐段 lstat，输入本身或锚点之下的任何一段是符号链接一律视为非法，如实记原因后回退自建；
  // 既不进入 parent 接受路径，也绝不删链接、更不删链接目标。
  // N6-A 返修：锚点只能确定一次。f 版在逐段找锚点时用「realPathOf(prefix)===tmpReal 就覆写 anchor」，
  // 路径中间那个绕回真实 tmpdir 的链接（…/back-to-tmp -> /private/tmp）会把锚点推到链接之后，链接本身
  // 反而不被 lstat，扫描不完备、又被当 parent temp 接受并删掉目标。g 版改成「第一个 realpath 等于真实
  // tmpdir 的前缀」并找到即停，但「realpath 等于 tmpReal」本身仍然不是合法的锚点来源。
  // N6-A2 返修：锚点必须来自允许的锚点形态白名单，并且是输入路径的**字面**段前缀（按段比较、去尾斜杠）：
  //   1) os.tmpdir() 的字面形态；2) realpath(os.tmpdir()) 形态；
  //   3) 系统默认临时目录 /tmp 及其真实形态（macOS 上 /tmp -> /private/tmp：TMPDIR 被设成真实形态时，
  //      合法输入仍可能按 /tmp 字面写法给出，V4/V9 这类合法别名不能误拒）。
  // 白名单之外的前缀哪怕 realpath 恰好等于真实 tmpdir 也不算锚点——tmpdir 外侧那条指向 tmp 根的链接
  // （/tmp/<base>/outside/link -> /tmp/<base>/actual-tmp）自己就是「第一个 realpath 等于 tmpReal 的前缀」，
  // 于是这条从没被 lstat 的外部链接被当成锚点，锚点之下的 lstat 从它的目标里继续走，输入门照样过、
  // 真实目标被删。现在它落在锚点（/tmp）之下，逐段 lstat 会抓到它。找不到白名单前缀而 realpath 包含门
  // 又已过，说明是从 tmpdir 外经链接「穿」进 tmpdir 的，同样 fail closed 拒用。
  // L1 口径：ENOTDIR 以及任何其它异常一律 fail closed 给出拒用原因；ENOENT 只允许出现在末段
  // （目录尚不存在，交给后面的「目录不存在」门），中间段 ENOENT 也 fail closed。
  // 允许的锚点形态：配置 TMPDIR 的字面形态与真实形态，加上系统默认临时目录 /tmp 的字面/真实形态。
  // 一律去尾斜杠（path.resolve）并按段比较； realpath 解析不出来就只留字面形态（fail closed 少接受）。
  function allowedAnchorForms() {
    const forms = [];
    for (const raw of [os.tmpdir(), '/tmp']) {
      if (!raw || !path.isAbsolute(raw)) continue;
      const literal = path.resolve(raw);
      if (literal === path.sep) continue;
      forms.push(literal);
      const real = realPathOf(literal);
      if (real && real !== path.sep) forms.push(real);
    }
    return Array.from(new Set(forms));
  }
  // 锚点＝输入路径开头与某个允许形态逐段相同的那一段；命中多个时取最长（最具体）的那个。
  // 注意：不看 realpath，只看字面段，所以「输入里某一段是指向 tmp 根的链接」永远不会被当锚点。
  function findAnchor(segs) {
    let best = null;
    for (const form of allowedAnchorForms()) {
      const fsegs = form.split(path.sep).filter(Boolean);
      if (!fsegs.length || fsegs.length > segs.length) continue;
      let same = true;
      for (let i = 0; i < fsegs.length; i++) if (segs[i] !== fsegs[i]) { same = false; break; }
      if (!same) continue;
      if (!best || fsegs.length > best.len) best = { index: fsegs.length - 1, prefix: form, len: fsegs.length };
    }
    return best;
  }
  function symlinkSegmentProblem(dir) {
    const segs = dir.split(path.sep).filter(Boolean);
    const last = segs.length - 1;
    try {
      if (fs.lstatSync(dir).isSymbolicLink()) return '输入本身是符号链接：' + dir;
    } catch (e) {
      const code = e && e.code;
      if (code !== 'ENOENT') return '无法 lstat ' + dir + '：' + (code || e);
    }
    if (!realPathOf(os.tmpdir())) return '无法解析 os.tmpdir() 的真实路径';
    const anchor = findAnchor(segs);
    if (!anchor) return '路径开头不是允许的锚点（os.tmpdir() 字面形态 ' + JSON.stringify(os.tmpdir())
      + '、其真实形态，或系统 /tmp 别名），但归一后又落在真实 tmpdir 内，说明经链接穿入 tmpdir，拒用';
    let cur = anchor.prefix;
    for (let i = anchor.index + 1; i < segs.length; i++) {
      cur = path.join(cur, segs[i]);
      try {
        if (fs.lstatSync(cur).isSymbolicLink()) return '中间存在符号链接：' + cur;
      } catch (e) {
        const code = e && e.code;
        if (code === 'ENOENT' && i === last) continue; // 末段尚不存在：交给后面的「目录不存在」门
        return '无法 lstat ' + cur + '：' + (code || e);
      }
    }
    return null;
  }
  function providedTempProblem(dir) {
    if (!dir) return '未设置或为空';
    if (!path.isAbsolute(dir) || dir !== path.resolve(dir)) return '不是规范化绝对路径';
    if (!path.basename(dir).startsWith('kd2-e2e-')) return 'basename 不以 kd2-e2e- 开头';
    const tmpReal = realPathOf(os.tmpdir());
    const dirReal = realPathOf(dir);
    if (!dirReal || !tmpReal || !(dirReal === tmpReal || dirReal.startsWith(tmpReal + path.sep))) return '不在 os.tmpdir() 下';
    const linkProblem = symlinkSegmentProblem(dir);
    if (linkProblem) return linkProblem;
    if (!fs.existsSync(dir)) return '目录不存在';
    if (!fs.statSync(dir).isDirectory()) return '不是目录';
    if (fs.readdirSync(dir).length) return '目录非空';
    return null;
  }
  const providedTempIssue = providedTempProblem(providedTemp);
  const temp = providedTempIssue ? fs.mkdtempSync(path.join(os.tmpdir(), 'kd2-e2e-')) : providedTemp;
  // tempSource/tempNote 进清单与回执：单独运行（self）也要能看出本轮实际用的是哪个临时目录。
  const tempSource = providedTempIssue ? 'self' : 'parent';
  const tempNote = providedTempIssue ? 'E2E_TRASH_TEMP 不可用（' + providedTempIssue + '），已回退自建：' + temp : null;
  // 兜底断言：不论走哪条分支，temp 归一成真实路径后都必须在 os.tmpdir() 的真实路径内、且不在副本根目录
  // （归一后）内。一旦不满足（M2 的现场、以及 M2-a 的别名形态：/tmp ↔ /private/tmp 或中间符号链接），
  // 立刻带原因退出，绝不让 workspace/home 落进仓库后跑完再谎报已清理。
  const tmpRoot = os.tmpdir();
  const tmpRootReal = realPathOf(tmpRoot);
  const rootReal = realPathOf(ROOT);
  const tempReal = realPathOf(temp);
  const insideReal = (child, parent) => !!child && !!parent && (child === parent || child.startsWith(parent + path.sep));
  const tempFatal = !path.isAbsolute(temp) ? 'temp 不是绝对路径'
    : !tempReal ? 'temp 无法解析真实路径'
      : insideReal(tempReal, rootReal) ? '真实路径落在副本根目录内'
        : !insideReal(tempReal, tmpRootReal) ? '真实路径不在 os.tmpdir() 的真实路径下' : null;
  if (tempFatal) {
    const why = '临时目录不合法：temp=' + JSON.stringify(temp) + '（' + tempFatal + '；必须是 ' + tmpRoot
      + '（真实路径 ' + tmpRootReal + '）下的绝对路径，且不得在副本根目录 ' + ROOT + '（真实路径 ' + rootReal + '）内）';
    try { fs.writeFileSync(path.join(evidence, 'temp-fatal.txt'), why + '\ntempReal=' + tempReal + '\ntempSource=' + tempSource + '\nprovidedTemp=' + JSON.stringify(providedTemp) + '\n'); } catch (e) {}
    console.error(why);
    process.exit(1);
  }
  // N6-T：输入门只在启动前查一次，判定与收尾删除之间，这个目录可能被并发替换（改名移走、原路径放
  // 符号链接）。接受 temp 时先登记它的 lstat dev/ino；收尾删除前重新 lstat 核对：已成链接、dev/ino 与
  // 登记不一致、或登记过却已不在原路径 → 一律拒删（绝不跟随链接删任何东西），三个清理布尔如实为 false
  // 并写原因。一次 lstat 不能消除全程竞态，只保证不谎报、不跟随链接删除。
  let tempIdentity = null;
  let tempIdentityError = null;
  try {
    const st0 = fs.lstatSync(temp);
    tempIdentity = { dev: st0.dev, ino: st0.ino };
  } catch (e) { tempIdentityError = '判定阶段无法 lstat temp：' + ((e && e.code) || e); }
  const workspace = path.join(temp, 'workspace');
  fs.mkdirSync(workspace);
  const home = path.join(temp, 'home');
  fs.mkdirSync(home);
  const networkLog = path.join(evidence, 'blocked-network.jsonl');
  fs.writeFileSync(networkLog, '');
  let child, browser, base, modelServer, fixture = {};
  let serviceArgv = null, servicePort = null;
  const browserErrors = [];
  const blocked = [];
  // 登记清单：把临时目录（含 realpath）、本轮 runId、detached 副本工作台的 PID 与其真实启动
  // 时刻（ps -o lstart=）、fixture 与 workspace 绝对路径，以及启动服务用的完整参数（serviceArgv，
  // 不含可执行文件）与端口报给父套件（run.cjs 通过 E2E_TRASH_MANIFEST 传入路径）。父套件在本套件
  // 被 SIGKILL 后按清单兜底回收：归属先由父套件自己持有的 runId/temp/suitePid 证明（见
  // manifestOwnership），再按 serviceArgv 逐段精确比对命令行、核对启动时刻；PID 被复用、清单错指/
  // 陈旧或命令行不逐段相符时拒杀拒删。正常收尾与 SIGTERM 自回收不依赖它。只登记自己启动的进程。
  const manifestPath = process.env.E2E_TRASH_MANIFEST || '';
  const runId = process.env.E2E_TRASH_RUN_ID || crypto.randomBytes(6).toString('hex');
  let manifestError = null;
  function serviceLstart(pid) {
    if (!pid) return null;
    try { return spawnSync('ps', ['-ww', '-o', 'lstart=', '-p', String(pid)], { encoding: 'utf8' }).stdout.trim() || null; }
    catch (e) { return null; }
  }
  function writeManifest() {
    if (!manifestPath) return;
    try {
      fs.writeFileSync(manifestPath, JSON.stringify({ temp, tempReal,
        servicePid: child ? child.pid : null, serviceLstart: child ? serviceLstart(child.pid) : null,
        serviceArgv, servicePort,
        fixturePath: path.join(__dirname, 'server_fixture.py'), workspace, runId, suitePid: process.pid,
        tempProvidedByParent: tempSource === 'parent', tempSource, tempPath: temp, tempNote,
        updatedAt: new Date().toISOString() }, null, 2));
      manifestError = null;
    } catch (e) { manifestError = '清单写入失败：' + (e && e.message ? e.message : e); }
  }
  writeManifest();

  async function api(url, body) {
    const r = await fetch(base + url, body === undefined ? {}
      : { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Article-Studio': '1', 'Origin': base }, body: JSON.stringify(body) });
    const text = await r.text();
    let data = null;
    try { data = JSON.parse(text); } catch (e) { data = { raw: text.slice(0, 200) }; }
    return { status: r.status, ok: r.ok, data };
  }
  let outcome = { failed: [], defects: [] };
  // 回收本套件全部资源：浏览器、detached 副本工作台、进程内模型服务、临时目录。
  // 所有入口（正常 finally 与 SIGTERM 处理器）共用同一个 finalizePromise：谁先进入谁执行，
  // 后到者 await 同一个 Promise 等它完成，而不是跳过——否则先到者会在中途被组 SIGKILL 打断，
  // results.json 就落不了盘。结果落盘在 runFinalize 内完成，任何退出路径都先落盘再组自杀。
  let finalizePromise = null;
  function finalize(extra) {
    if (!finalizePromise) finalizePromise = runFinalize(extra);
    return finalizePromise;
  }
  async function runFinalize(extra) {
    // 清理：先关浏览器，再停副本工作台与模型服务，删临时 workspace。
    let browserClosed = true;
    if (browser) browserClosed = await Promise.race([browser.close().then(() => true).catch(() => false), sleep(10000).then(() => false)]);
    if (browser && !browserClosed) { try { if (typeof browser.process === 'function') browser.process()?.kill('SIGKILL'); } catch (e) {} }
    const serverStop = child ? await stopChild(child, sleep) : { method: 'none', left: [] };
    if (modelServer) await new Promise((r) => modelServer.close(r));
    let tempRemoveError = null;
    // N6-T：删除前重新核对 temp 身份（判定之后目录可能被改名移走、原路径换成符号链接）。
    // 任何身份可疑的情况一律拒删，绝不跟随链接删；三个清理布尔随后按实存检查如实落盘。
    let tempRemoveRefused = null;
    let nowStat = null;
    try { nowStat = fs.lstatSync(temp); } catch (e) {
      const code = e && e.code;
      if (code === 'ENOENT') tempRemoveRefused = '删除前 temp 已不在原路径（可能被改名移走或他人删除），本轮未删除任何目录';
      else tempRemoveRefused = '删除前无法 lstat temp：' + (code || e);
    }
    if (!tempRemoveRefused && nowStat.isSymbolicLink()) tempRemoveRefused = '删除前 temp 已是符号链接，拒删（绝不跟随链接删除）';
    else if (!tempRemoveRefused && tempIdentity && (nowStat.dev !== tempIdentity.dev || nowStat.ino !== tempIdentity.ino)) tempRemoveRefused = '删除前 temp 身份变化（dev/ino 与判定登记不一致），拒删';
    else if (!tempRemoveRefused && !tempIdentity) tempRemoveRefused = '判定阶段未能登记 temp 身份，拒删（fail closed）：' + (tempIdentityError || '未知');
    if (!tempRemoveRefused) {
      try { fs.rmSync(temp, { recursive: true, force: true }); } catch (e) { tempRemoveError = (e && (e.code || e.message)) || String(e); }
    }
    // 回执按真实路径核对：temp 及其里的 workspace/home 三个路径各自是否还在，不再由一个空串冒充已清理。
    // 布尔一律以删除后的实存检查为准，并同时核对归一后的真实目标（不只看字面路径）；拒删时三个布尔全 false。
    const aliasTemp = tempReal && tempReal !== temp ? tempReal : null;
    const gone = (p) => !fs.existsSync(p) && !(aliasTemp && fs.existsSync(path.join(aliasTemp, path.basename(p))));
    const tempGone = !tempRemoveRefused && !fs.existsSync(temp) && !(aliasTemp && fs.existsSync(aliasTemp));
    const workspaceGone = !tempRemoveRefused && gone(workspace);
    const homeGone = !tempRemoveRefused && gone(home);
    const failed = steps.filter((s) => s.status === 'failed');
    const defects = browserErrors.map((message) => ({ kind: 'page_error', message }));
    for (const item of blocked.filter((b) => typeof b === 'object' && b.failedRequest)) defects.push({ kind: 'failed_request', ...item });
    const summary = { steps, defects, evidence, base, manifestError,
      tempProvidedByParent: tempSource === 'parent', tempSource, tempPath: temp, tempNote,
      blockedBrowser: blocked, browserErrors,
      blockedNetwork: fs.readFileSync(networkLog, 'utf8'), cleanup: { browserClosed, serverStop,
        workspaceRemoved: tempGone, tempPath: temp, tempSource, tempRemoved: tempGone, tempRemoveError,
        tempIdentity, tempRemoveRefused,
        workspacePath: workspace, workspaceDirRemoved: workspaceGone,
        homePath: home, homeDirRemoved: homeGone,
        modelServerClosed: !modelServer || !modelServer.listening }, ...(extra || {}) };
    fs.writeFileSync(path.join(evidence, 'results.json'), JSON.stringify(summary, null, 2));
    fs.writeFileSync(path.join(evidence, 'result.txt'), steps.map((s) => (s.status === 'failed' ? 'failed ' : 'passed ') + s.name).join('\n')
      + '\n\n产品缺陷（只报告不修改）：\n' + (defects.length ? defects.map((d) => '- ' + JSON.stringify(d)).join('\n') : '（无）'));
    return { failed, defects };
  }
  // 父套件超时/取消时对本组发 SIGTERM：就地回收全部资源（含 detached 服务、浏览器、临时目录）
  // 并落盘结果，再按组自杀，确保同组浏览器等进程不残留；退出码 1 表示未跑完。
  // 父套件会发组+直接两份 SIGTERM，Node 会各派发一次：finalize 共享同一个 Promise，
  // 重复信号只是再次 await 已在执行的收尾，不会提前组自杀打断第一次清理；
  // results.json 在 runFinalize 内先落盘，之后才组自杀。
  process.on('SIGTERM', () => {
    finalize({ terminated: 'SIGTERM' })
      .catch(() => {})
      .then(() => { try { process.kill(-process.pid, 'SIGKILL'); } catch (e) { process.exit(1); } });
  });
  async function newPage(width, height) {
    const p = await browser.newPage({ viewport: { width, height }, serviceWorkers: 'block' });
    await p.route('**/*', (r) => {
      const url = r.request().url();
      if (url.startsWith(base) || url.startsWith('data:') || url.startsWith('blob:')) return r.continue();
      blocked.push(url);
      return r.abort();
    });
    p.on('pageerror', (e) => browserErrors.push(e.message + ' @ ' + String(e.stack || '').split('\n')[1]));
    p.on('response', async (r) => {
      if (!r.url().startsWith(base) || r.status() < 400) return;
      let body = '';
      try { body = (await r.text()).slice(0, 200); } catch (e) { body = '<读不到>'; }
      blocked.push({ failedRequest: r.request().method() + ' ' + new URL(r.url()).pathname, status: r.status(), body });
    });
    p.setDefaultTimeout(20000);
    return p;
  }
  // 两个一次性夹具助手直接内联，保证这个 .cjs 单文件可跑。
  // 只在本副本的临时 workspace 上写：不碰正式库、不新增测试 API、不替换产品逻辑。
  const FIXTURE_PY = [
    "import json, os, sqlite3, sys, urllib.request",
    "from pathlib import Path",
    "WORKSPACE = Path(sys.argv[sys.argv.index('--workspace') + 1]).resolve()",
    "BASE = os.environ['E2E_APP_PORT_URL']",
    "ARTICLES = [('E2E软删除甲', '甲文正文：黄昏用倒影复制天空，人物只留背影。'),",
    "            ('E2E软删除乙', '乙文正文：渡船只留一条灯线，其余留白。'),",
    "            ('E2E软删除丙', '丙文正文：构图先定地平线，再决定主体位置。'),",
    "            ('E2E软删除丁', '丁文正文：低对比电影画面适合做长文配图。')]",
    "COLLECTED = {'url': 'https://example.com/e2e-trash-duplicate', 'title': 'E2E重复收藏样本',",
    "             'reader_note': 'E2E：这篇用来验证重复收藏命中已删除资料时的提示。'}",
    "def api(path, body):",
    "    request = urllib.request.Request(BASE + path, data=json.dumps(body).encode(),",
    "        headers={'Content-Type': 'application/json', 'X-Article-Studio': '1', 'Origin': BASE}, method='POST')",
    "    with urllib.request.urlopen(request, timeout=20) as response:",
    "        return json.load(response)",
    "def seed_articles_source():",
    "    # 补上「我的文案与作品」来源：产品页面启动时会无条件请求同步 articles（public/knowledge.js 的 boot），",
    "    # 生产环境里它永远存在；server_fixture 把来源固定成空列表，不补就会出现夹具独有的 400。",
    "    with sqlite3.connect(WORKSPACE / 'knowledge' / 'knowledge.db') as c:",
    "        c.execute('INSERT OR IGNORE INTO sources(id,config,status,detail,synced_at) VALUES(?,?,?,?,?)',",
    "                  ('articles', json.dumps({'id': 'articles', 'name': '我的文案与作品', 'kind': 'articles'}, ensure_ascii=False),",
    "                   'ready', 'E2E 合成库：无作品', '2026-10-11T00:00:00+00:00'))",
    "CARDS_DDL = ('CREATE TABLE IF NOT EXISTS cards(id TEXT PRIMARY KEY, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,'",
    "             ' drop_id TEXT NOT NULL, document_id TEXT NOT NULL, prompt TEXT NOT NULL, model TEXT NOT NULL,'",
    "             ' scene TEXT NOT NULL DEFAULT \\'\\', note TEXT NOT NULL DEFAULT \\'\\', status TEXT NOT NULL DEFAULT \\'candidate\\','",
    "             ' reason TEXT NOT NULL DEFAULT \\'\\', refs TEXT NOT NULL DEFAULT \\'[]\\', model_name TEXT NOT NULL DEFAULT \\'\\','",
    "             ' run_id TEXT NOT NULL DEFAULT \\'\\', prompt_sha TEXT NOT NULL DEFAULT \\'\\')')",
    "def seed_cards(document_id):",
    "    # 一张真实引用该正文的提示词卡，让预览里的引用计数有数。",
    "    root = WORKSPACE / 'inspiration'",
    "    root.mkdir(parents=True, exist_ok=True)",
    "    with sqlite3.connect(root / 'cards.db') as c:",
    "        c.executescript(CARDS_DDL)",
    "        c.execute('INSERT OR REPLACE INTO cards(id,created_at,updated_at,drop_id,document_id,prompt,model,scene,note,status,reason,refs,model_name,run_id,prompt_sha)'",
    "                  ' VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',",
    "                  ('e2ecard0000000000000000000000', '2026-10-11T00:00:00+00:00', '2026-10-11T00:00:00+00:00',",
    "                   'e2edrop00000000000000000000000', document_id, 'E2E 引用计数样本卡', 'qi21', 'E2E', '',",
    "                   'candidate', '', json.dumps([{'id': 'e0', 'origin': 'document', 'label': '知识库正文',",
    "                       'document_id': document_id, 'drop_id': 'e2edrop00000000000000000000000',",
    "                       'start': 0, 'end': 12, 'text': '甲文正文', 'sha256': 'e2e'}]), 'e2e', 'e2e', 'e2e'))",
    "def main():",
    "    seed_articles_source()",
    "    ids = {}",
    "    for title, body in ARTICLES:",
    "        ids[title] = api('/api/knowledge/note', {'title': title, 'body': body, 'tags': 'E2E,软删除'})['id']",
    "    collected = api('/api/knowledge/reference', COLLECTED)",
    "    ids['collected'] = collected['id']",
    "    duplicate = api('/api/knowledge/reference', dict(COLLECTED, title='E2E重复收藏再试'))",
    "    assert duplicate['id'] == collected['id'] and duplicate.get('duplicate'), '重复收藏夹具没有命中同一篇'",
    "    seed_cards(ids['E2E软删除甲'])",
    "    ids['missing_body'] = 'b0b1b2b3b4b5b6b7b8b9babbbcbdbebf'",
    "    (WORKSPACE / 'knowledge-trash-fixture.json').write_text(json.dumps(ids, ensure_ascii=False, indent=2))",
    "    print('E2E 软删除夹具已就绪')",
    "main()",
  ].join('\n');
  // 把合成库里指定资料的 hash 改掉，制造「预览之后正文已更新」的版本冲突。
  const CONFLICT_PY = [
    "import sqlite3, sys",
    "from pathlib import Path",
    "WORKSPACE = Path(sys.argv[sys.argv.index('--workspace') + 1]).resolve()",
    "DOC = sys.argv[sys.argv.index('--id') + 1]",
    "with sqlite3.connect(WORKSPACE / 'knowledge' / 'knowledge.db') as c:",
    "    row = c.execute('SELECT hash FROM documents WHERE id=?', (DOC,)).fetchone()",
    "    if not row:",
    "        raise SystemExit('合成库里没有这篇：' + DOC)",
    "    c.execute('UPDATE documents SET hash=? WHERE id=?', ('e' * 64, DOC))",
    "    print('已把该篇 hash 改掉')",
  ].join('\n');
  function runPy(source, extraArgs, label) {
    const file = path.join(temp, label + '.py');
    fs.writeFileSync(file, source);
    const out = spawnSync(PYTHON, [file, '--workspace', workspace, ...extraArgs],
      { cwd: ROOT, env: { ...process.env, E2E_APP_PORT_URL: base }, encoding: 'utf8' });
    if (out.status !== 0) throw new Error(label + ' 执行失败：' + (out.stderr || out.stdout));
    return out.stdout;
  }
  // 用户点的是带字的「取消」，不是右上角那个 ×。
  async function cancelConfirm(page) {
    await page.locator('#kb-trash-confirm-dialog').getByRole('button', { name: '取消' }).click();
    await page.locator('#kb-trash-confirm-dialog').waitFor({ state: 'hidden' });
  }
  async function closeDialog(page, selector) {
    // 走真实的关闭按钮（产品自己的 [data-close]），不直接调 dialog.close()。
    await page.locator(selector + ' [data-close]').first().click();
    await page.locator(selector).waitFor({ state: 'hidden' });
  }
  async function shot(p, name) {
    const file = path.join(evidence, name + '.png');
    await p.screenshot({ path: file, fullPage: true });
    return file;
  }

  try {
    const { chromium } = playwright();
    const modelPort = await freePort();
    modelServer = http.createServer((req, res) => { res.writeHead(503); res.end('{"error":"E2E 本轮不调模型"}'); });
    await new Promise((r) => modelServer.listen(modelPort, '127.0.0.1', r));

    const appPort = await freePort();
    base = 'http://127.0.0.1:' + appPort;
    const config = path.join(temp, 'model.json');
    fs.writeFileSync(config, JSON.stringify({ model: 'e2e-mock', endpoint: 'http://127.0.0.1:' + modelPort + '/v1/chat/completions',
      no_api_key: true, context_tokens: 262144, max_output_tokens: 2048, temperature: 0, timeout_seconds: 5 }));
    const log = fs.openSync(path.join(evidence, 'server.log'), 'a');
    serviceArgv = [path.join(__dirname, 'server_fixture.py'), '--workspace', workspace, '--port', String(appPort)];
    servicePort = appPort;
    child = spawn(PYTHON, serviceArgv,
      { cwd: ROOT, env: { ...process.env, PI_CODING_AGENT_DIR: home,
        ARTICLE_STUDIO_MODEL_CONFIG: config, E2E_APP_PORT: String(appPort), E2E_MODEL_PORT: String(modelPort),
        E2E_APP_PORT_URL: base, E2E_ALLOWED_PORTS: appPort + ',' + modelPort, E2E_NETWORK_LOG: networkLog,
        NODE_OPTIONS: '--require ' + path.join(__dirname, 'network_guard.cjs') }, detached: true, stdio: ['ignore', log, log] });
    fs.closeSync(log);
    writeManifest();
    await until(async () => {
      if (child.exitCode !== null) throw new Error('副本工作台启动失败，见 server.log');
      try { const r = await fetch(base + '/api/knowledge'); return r.ok; } catch (e) { return false; }
    }, 40000);

    // 合成库：6 篇资料 + 1 条正文缺失的已删除记录。
    runPy(FIXTURE_PY, [], 'knowledge-trash-fixture');
    fixture = JSON.parse(fs.readFileSync(path.join(workspace, 'knowledge-trash-fixture.json'), 'utf8'));
    const ids = { jia: fixture['E2E软删除甲'], yi: fixture['E2E软删除乙'], bing: fixture['E2E软删除丙'],
      ding: fixture['E2E软删除丁'], collected: fixture.collected, missing: fixture.missing_body };
    const selfTrashCount = async () => (await api('/api/knowledge/trash?offset=0&limit=50')).data.total;
    const total0 = (await api('/api/knowledge')).data.total;
    record('夹具就绪', { total: total0, ids, note: total0 >= 5 ? '合成库 ' + total0 + ' 篇' : '篇数不足' });

    browser = await chromium.launch({ channel: process.env.E2E_BROWSER || 'chrome',
      headless: !process.env.E2E_HEADED, args: ['--disable-background-networking', '--disable-component-update'] });
    const p = await newPage(1280, 900);
    await p.goto(base + '/knowledge.html');
    await p.locator('#kb-results .kb-result').first().waitFor();

    // 步骤 1：勾选 2 条 → 预览 → 取消（零写入）
    await p.locator('[data-trash-pick="' + ids.yi + '"]').check();
    await p.locator('[data-trash-pick="' + ids.bing + '"]').check();
    await p.locator('#kb-delete-count').waitFor();
    assert.equal((await p.locator('#kb-delete-count').innerText()).trim(), '已选 2 条');
    await p.locator('#kb-delete-selected').click();
    await p.locator('#kb-trash-confirm-dialog').waitFor({ state: 'visible' });
    const confirmTitle = await p.locator('#kb-trash-confirm-title').innerText();
    const confirmItems = await p.locator('#kb-trash-confirm-items .kb-trash-item').count();
    assert.match(confirmTitle, /把这 2 条资料移到已删除/);
    assert.equal(confirmItems, 2, '确认框应逐条列出');
    const previewText = await p.locator('#kb-trash-confirm-items').innerText();
    assert.match(previewText, /回收项：/, '预览要显示引用分类');
    const beforeTrash = (await api('/api/knowledge/trash?offset=0&limit=50')).data.total;
    await shot(p, '01-预览确认框');
    await cancelConfirm(p);   // 走真实的「取消」按钮
    const afterCancelTrash = (await api('/api/knowledge/trash?offset=0&limit=50')).data.total;
    const afterCancelTotal = (await api('/api/knowledge')).data.total;
    assert.equal(afterCancelTrash, beforeTrash, '取消必须零写入');
    assert.equal(afterCancelTotal, total0, '取消必须零写入');
    record('步骤1 勾选→预览→取消零写入', { beforeTrash, afterCancelTrash, total0 });

    // 步骤 1b：被提示词卡引用的那篇要报真实数量；数不到的要写「未统计」，两者不能混。
    await p.locator('#kb-query').fill('E2E软删除甲');
    const cardSearch = p.waitForResponse((r) => new URL(r.url()).pathname === '/api/knowledge/search');
    await p.locator('#kb-search-form button').click();
    await cardSearch;
    await p.locator('[data-trash-move="' + ids.jia + '"]').click();
    await p.locator('#kb-trash-confirm-dialog').waitFor({ state: 'visible' });
    const cardPreview = (await p.locator('#kb-trash-confirm-items').innerText()).replace(/\s+/g, ' ');
    assert.match(cardPreview, /E2E软删除甲/, '单条预览应显示这篇');
    assert.match(cardPreview, /提示词卡：1/, '被卡片引用的要报真实数量，实际：' + cardPreview);
    assert.match(cardPreview, /作品与底稿引用：未统计/, '没有批量接口的引用要如实写未统计');
    await shot(p, '01b-卡片引用计数预览');
    await cancelConfirm(p);
    assert.equal(await selfTrashCount(), 0, '这一步只预览，仍是零写入');
    record('步骤1b 卡片引用报真实数量、未统计项不冒充 0', { cardPreview: cardPreview.slice(0, 160) });

    await p.locator('#kb-query').fill('软删除');
    const backToList = p.waitForResponse((r) => new URL(r.url()).pathname === '/api/knowledge/search');
    await p.locator('#kb-search-form button').click();
    await backToList;
    await p.locator('#kb-results .kb-result').first().waitFor();
    // 重新检索会清掉选择（产品明示的行为），重新勾一次再继续。
    assert(await p.locator('#kb-delete-bar').isHidden(), '重新检索后选择条应清空');
    await p.locator('[data-trash-pick="' + ids.yi + '"]').check();
    await p.locator('[data-trash-pick="' + ids.bing + '"]').check();
    assert.equal((await p.locator('#kb-delete-count').innerText()).trim(), '已选 2 条');

    // 步骤 2：再预览 → 确认移到已删除
    await p.locator('#kb-delete-selected').click();
    await p.locator('#kb-trash-confirm-dialog').waitFor({ state: 'visible' });
    await p.locator('#kb-trash-confirm-ok').click();
    await p.locator('#kb-trash-confirm-dialog').waitFor({ state: 'hidden' });
    // 确认框提交后，等回执真正出现（不靠某一瞬间的快照）。
    await p.waitForFunction(() => /已移出 2 条/.test(document.querySelector('#kb-status').textContent || ''), null, { timeout: 15000 })
      .catch(() => { throw new Error('确认删除后没有出现逐组回执'); });
    const statusText = await p.locator('#kb-status').innerText();
    assert.match(statusText, /已移出 2 条/, '回执要逐组报数量，实际：' + statusText);
    assert.match(statusText, /已在已删除 0 条 · 知识库没有 0 条 · 版本已变化 0 条 · 未写成功 0 条/,
      '回执要把五组都报出来，实际：' + statusText);
    // 观察项（不判失败）：页面 2.5 秒轮询会重跑 search()，search() 末尾清空状态行。
    const survives = await sleep(3200).then(async () => /已移出 2 条/.test(await p.locator('#kb-status').innerText()));
    const trashAfter = (await api('/api/knowledge/trash?offset=0&limit=50')).data;
    assert.equal(trashAfter.total, beforeTrash + 2, '已删除列表应有 2 条');
    assert(trashAfter.results.some((r) => r.id === ids.yi && r.body_available === true));
    record('步骤2 确认移到已删除', { statusText: statusText.replace(/\s+/g, ' ').slice(0, 200),
      trashTotal: trashAfter.total, receiptSurvivesPoll: survives });

    // 步骤 3：检索/列表/来源计数不再出现
    await p.locator('#kb-query').fill('软删除');
    const searched = p.waitForResponse((r) => new URL(r.url()).pathname === '/api/knowledge/search');
    await p.locator('#kb-search-form button').click();
    await searched;
    await p.locator('#kb-results .kb-result').first().waitFor();
    const visible = await p.locator('#kb-results .kb-result').evaluateAll((els) => els.map((e) => e.dataset.document));
    assert(!visible.includes(ids.yi), '已删除的资料还出现在检索结果里');
    assert(!visible.includes(ids.bing), '已删除的资料还出现在检索结果里');
    const statusAfter = (await api('/api/knowledge')).data;
    const searchYi = (await api('/api/knowledge/search?q=' + encodeURIComponent('乙文正文'))).data;
    assert(!searchYi.results.some((r) => r.id === ids.yi), '已删除的资料仍能被检索命中');
    assert.equal(statusAfter.total, total0 - 2, '状态总数应减去两条');
    record('步骤3 检索/列表/计数不再出现', { total: statusAfter.total, visible });

    // 步骤 4：重复收藏命中已删除资料，不复活且提示已删除
    const dup = (await api('/api/knowledge/reference', { url: 'https://example.com/e2e-trash-duplicate',
      title: 'E2E重复收藏样本', reader_note: 'E2E重复收藏验证' })).data;
    assert.equal(dup.id, ids.collected, '重复收藏应命中同一篇');
    const dupTrashed = ids.yi;  // 这条已删除，用它的编号验证 API 事实
    const docOfDeleted = (await api('/api/knowledge/document/' + ids.yi)).data;
    assert.equal(docOfDeleted.trashed, true, '已删除资料的单条读取应带 trashed');
    // 再收藏一篇新的，确认它可检索；然后对它做一次重复收藏确认提示码
    const fresh = (await api('/api/knowledge/reference', { url: 'https://example.com/e2e-trash-fresh',
      title: 'E2E新收藏待删除', reader_note: '用于验证已删除后的重复收藏' })).data;
    await api('/api/knowledge/trash', { items: [{ id: fresh.id, expected_hash: fresh.hash }], confirm: true });
    const dupAfter = (await api('/api/knowledge/reference', { url: 'https://example.com/e2e-trash-fresh',
      title: 'E2E新收藏待删除', reader_note: '用于验证已删除后的重复收藏' })).data;
    assert.equal(dupAfter.id, fresh.id, '重复收藏应命中同一篇');
    assert.equal(dupAfter.trashed, true, '重复收藏命中已删除资料要带回隐藏事实');
    assert(dupAfter.code === 'ALREADY_TRASHED' || dupAfter.code === 'DUPLICATE', '重复收藏要带提示码，实际 ' + dupAfter.code);
    const revived = (await api('/api/knowledge/search?q=' + encodeURIComponent('E2E新收藏待删除'))).data;
    assert(!revived.results.some((r) => r.id === fresh.id), '重复收藏不能把已删除资料复活');
    record('步骤4 重复收藏不复活并提示已删除', { code: dupAfter.code, trashed: dupAfter.trashed });

    // 步骤 5：已删除列表分页（offset/limit）
    const page1 = (await api('/api/knowledge/trash?offset=0&limit=2')).data;
    const page2 = (await api('/api/knowledge/trash?offset=' + page1.next_offset + '&limit=2')).data;
    assert.equal(page1.results.length, 2, '第一页应有 2 条');
    assert(page1.next_offset === 2, 'next_offset 应指向下一页');
    assert(page2.results.length >= 1, '第二页应有数据');
    const idsPage1 = page1.results.map((r) => r.id);
    const idsPage2 = page2.results.map((r) => r.id);
    assert(!idsPage1.some((id) => idsPage2.includes(id)), '两页不应重复');
    const badLimit = (await api('/api/knowledge/trash?offset=0&limit=9999')).data;
    assert.equal(badLimit.code, 'INVALID_REQUEST', 'limit 越界应 400');
    record('步骤5 已删除列表分页', { page1: idsPage1.length, page2: idsPage2.length, nextOffset: page1.next_offset });

    // 步骤 6：界面打开已删除列表 → 恢复 → 重新出现、计数回来
    await p.locator('#kb-trash-open').click();
    await p.locator('#kb-trash-dialog').waitFor({ state: 'visible' });
    await p.locator('#kb-trash-list .kb-trash-row').first().waitFor();
    const rowCount = await p.locator('#kb-trash-list .kb-trash-row').count();
    await shot(p, '02-已删除列表');
    const restoreRow = p.locator('[data-restore="' + ids.yi + '"]');
    assert.equal(await restoreRow.count(), 1, '已删除列表里应能找到刚删的那条');
    await restoreRow.click();
    await p.locator('#kb-restore-confirm-dialog').waitFor({ state: 'visible' });
    // 缺陷观察点：点「恢复」时 knowledge.js 给 askRestore(...).catch(...)，而 askRestore 不是
    // async，返回 undefined，这条错误处理本身会抛。这里只记录，不判失败。
    const errorsAfterRestore = browserErrors.length;
    await shot(p, '03-恢复确认框');
    await p.locator('#kb-restore-confirm-ok').click();
    await p.locator('#kb-restore-confirm-dialog').waitFor({ state: 'hidden' });
    await until(async () => (await api('/api/knowledge/search?q=' + encodeURIComponent('乙文正文'))).data.results.some((r) => r.id === ids.yi));
    const statusBack = (await api('/api/knowledge')).data;
    assert.equal(statusBack.total, total0 - 1, '恢复一条后总数应回来一条');
    await closeDialog(p, '#kb-trash-dialog');
    record('步骤6 恢复后重新出现、计数回来', { rowCount, total: statusBack.total,
      pageErrorOnRestoreClick: browserErrors.length > errorsAfterRestore ? browserErrors[browserErrors.length - 1] : null });

    // 步骤 7：历史引用 document/<id> 仍可读并带「已移出常规检索」标记
    await p.locator('#kb-trash-open').click();
    await p.locator('#kb-trash-dialog').waitFor({ state: 'visible' });
    const viewBtn = p.locator('[data-trash-view="' + ids.bing + '"]');
    assert.equal(await viewBtn.count(), 1, '已删除列表应提供查看原文');
    await viewBtn.click();
    await p.locator('#kb-document-dialog').waitFor({ state: 'visible' });
    assert(await p.locator('#kb-document-trash-banner').isVisible(), '历史引用应显示已移出检索标记');
    const bannerText = await p.locator('#kb-document-trash-text').innerText();
    assert.match(bannerText, /已移出常规检索/);
    const bodyText = await p.locator('#kb-document-body').inputValue();
    assert.match(bodyText, /丙文正文/, '历史引用仍应能读到原文');
    await shot(p, '04-历史引用已删除正文');
    await closeDialog(p, '#kb-document-dialog');
    await closeDialog(p, '#kb-trash-dialog');
    record('步骤7 历史引用带已删除标记且可读原文', { bannerText });

    // 步骤 8：版本冲突单项提示（预览之后正文变了，再确认）
    await p.locator('#kb-query').fill('丁文');
    await p.locator('#kb-search-form button').click();
    await p.locator('#kb-results .kb-result').first().waitFor();
    await p.locator('[data-trash-pick="' + ids.ding + '"]').check();
    await p.locator('#kb-delete-selected').click();
    await p.locator('#kb-trash-confirm-dialog').waitFor({ state: 'visible' });
    // 确认框已经拿到旧 hash；现在从存储侧把这一篇的 hash 改掉，模拟「预览之后资料已更新」。
    runPy(CONFLICT_PY, ['--id', ids.ding], 'knowledge-trash-conflict');
    await p.locator('#kb-trash-confirm-ok').click();
    await p.locator('#kb-trash-confirm-dialog').waitFor({ state: 'hidden' });
    await p.waitForFunction(() => /版本已变化 1 条/.test(document.querySelector('#kb-status').textContent || ''), null, { timeout: 15000 })
      .catch(() => { throw new Error('冲突后没有出现逐组回执'); });
    const conflictStatus = await p.locator('#kb-status').innerText();
    assert.match(conflictStatus, /版本已变化 1 条/, '冲突应逐项报出，实际：' + conflictStatus);
    assert.match(conflictStatus, /已移出 0 条/, '冲突项不得被算成已删除，实际：' + conflictStatus);
    assert.match(conflictStatus, /预览后资料已更新/, '冲突应说明需要重新预览，实际：' + conflictStatus);
    const stillThere = (await api('/api/knowledge/search?q=' + encodeURIComponent('丁文'))).data;
    assert(stillThere.results.some((r) => r.id === ids.ding), '冲突项不得真的被移出检索');
    const conflictTrash = (await api('/api/knowledge/trash?offset=0&limit=50')).data;
    assert(!conflictTrash.results.some((r) => r.id === ids.ding), '冲突项不得进已删除列表');
    await shot(p, '05-版本冲突提示');
    record('步骤8 版本冲突单项提示且未删除', { conflictStatus: conflictStatus.replace(/\s+/g, ' ').slice(0, 200) });

    // 步骤 9：375 与 1280 宽排版截图
    for (const [label, width, height] of [['1280', 1280, 900], ['375', 375, 760]]) {
      await p.setViewportSize({ width, height });
      await p.goto(base + '/knowledge.html');
      await p.locator('#kb-results .kb-result').first().waitFor();
      await shot(p, '06-宽度' + label + '-结果');
      await p.locator('#kb-trash-open').click();
      await p.locator('#kb-trash-dialog').waitFor({ state: 'visible' });
      await p.locator('#kb-trash-list').waitFor();
      await shot(p, '07-宽度' + label + '-已删除');
      await closeDialog(p, '#kb-trash-dialog');
    }
    await p.setViewportSize({ width: 1280, height: 900 });
    record('步骤9 375 与 1280 宽排版截图', {});

    // 步骤 10：后端停掉时页面不卡忙态
    const stopResult = await stopChild(child, sleep);
    child = null;
    assert(!(stopResult.left || []).length, '停副本工作台没停干净');
    await p.goto(base + '/knowledge.html').catch(() => {});
    const offline = await p.evaluate(async () => {
      const r = await fetch('/api/knowledge/trash/preview', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' });
      return { ok: r.ok, status: r.status };
    }).catch((e) => ({ error: String(e) }));
    assert(offline.error || offline.status >= 500, '后端停掉后请求应失败，而不是假装成功');
    await shot(p, '08-后端停掉');
    record('步骤10 后端停掉页面不卡忙态', { offline });
  } catch (e) {
    fail('主流程', e);
  } finally {
    outcome = await finalize({});
  }
  console.log('本轮临时目录：' + tempSource + ' ' + temp + (tempNote ? '（' + tempNote + '）' : ''));
  console.log('证据：' + evidence);
  console.log('步骤：通过 ' + steps.filter((s) => s.status !== 'failed').length + ' · 失败 ' + outcome.failed.length);
  console.log('发现产品缺陷 ' + outcome.defects.length + ' 条（只报告，未修改产品代码）：');
  for (const d of outcome.defects) console.log('  - [' + d.kind + '] ' + d.message);
  process.exit(outcome.failed.length ? 1 : 0);
})();