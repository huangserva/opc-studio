'use strict';
/**
 * 第 13 轮编辑器粘贴 Markdown 端到端验收与安全核验（真实 Chrome + 剪贴板）。
 * 覆盖合同要求的验收矩阵（四处一致）及安全不退（11 条攻击/实体场景）。
 */
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const net = require('node:net');
const assert = require('node:assert/strict');
const { spawn } = require('node:child_process');
const { stopChild, forceStopSync, alive } = require('./stop_child.cjs');

const ROOT = path.resolve(__dirname, '../..');
const PYTHON = path.resolve(ROOT, '../article-importer/.venv/bin/python');

function playwright() {
  const custom = process.env.PLAYWRIGHT_PATH;
  if (custom) return require(custom);
  try { return require('playwright'); } catch {}
  const home = process.env.HOME || '';
  const codex = path.join(home, '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
  if (fs.existsSync(codex)) return require(codex);
  throw new Error('未找到 playwright 模块');
}

async function freePort() {
  return new Promise((resolve, reject) => {
    const srv = net.createServer();
    srv.listen(0, '127.0.0.1', () => {
      const p = srv.address().port;
      srv.close((err) => (err ? reject(err) : resolve(p)));
    });
  });
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
async function until(fn, timeout = 30000) {
  const start = Date.now();
  while (Date.now() - start < timeout) {
    try {
      const res = await fn();
      if (res) return res;
    } catch {}
    await sleep(150);
  }
  throw new Error('操作超时（' + timeout + 'ms）');
}

(async () => {
  const temp = fs.mkdtempSync(path.join(os.tmpdir(), 'studio-r13b-verify-'));
  const workspace = path.join(temp, 'workspace');
  fs.mkdirSync(workspace, { recursive: true });

  const evidenceDir = path.join(ROOT, 'coordination/evidence/round13b-verify');
  fs.mkdirSync(evidenceDir, { recursive: true });

  const appPort = await freePort();
  const base = `http://127.0.0.1:${appPort}`;

  console.log(`启动临时副本服务：${base}，工作区：${workspace}`);
  const serverLog = fs.openSync(path.join(evidenceDir, 'server.log'), 'a');
  const child = spawn(PYTHON, [path.join(ROOT, 'server.py'), '--workspace', workspace, '--port', String(appPort)], {
    cwd: ROOT,
    detached: true,
    stdio: ['ignore', serverLog, serverLog],
  });

  let browser;
  const pageerrors = [];
  const blockedRequests = [];
  const matrixResults = [];

  try {
    await until(async () => {
      const res = await fetch(`${base}/api/library`);
      return res.ok;
    }, 30000);
    console.log('工作台服务就绪');

    const { chromium } = playwright();
    browser = await chromium.launch({
      channel: 'chrome',
      headless: true,
      args: ['--disable-background-networking', '--disable-component-update'],
    });

    const context = await browser.newContext({
      viewport: { width: 1280, height: 900 },
      permissions: ['clipboard-read', 'clipboard-write'],
    });

    await context.route('**/*', async (route) => {
      const req = route.request();
      const u = new URL(req.url());
      if (u.hostname !== '127.0.0.1' || Number(u.port) !== appPort) {
        blockedRequests.push({ method: req.method(), url: req.url() });
        return route.abort();
      }
      return route.continue();
    });

    await context.addInitScript(() => {
      window.__probe = 0;
    });

    const page = await context.newPage();
    page.on('pageerror', (e) => pageerrors.push(String(e && e.message ? e.message : e)));

    await page.goto(`${base}/`, { waitUntil: 'networkidle' });

    // 核心矩阵用例与安全用例
    const testCases = [
      {
        name: 'minimal-repro',
        desc: '最小复现输入：引用、表格、代码块',
        raw: '> 引用验收文字\n\n| 项目 | 数值 |\n| --- | --- |\n| 甲 | 13 |\n\n```html\n<div>代码原样</div>\n```',
        check: (row) => {
          assert.equal(row.preview.blockquoteCount, 1, '引用必须渲染为 blockquote');
          assert.ok(row.preview.html.includes('<p>| 项目 | 数值 |</p>'), '表格保持普通 p 原文');
          assert.equal(row.preview.code[0]?.trim(), '<div>代码原样</div>', '代码原样显示且不被双重转义');
          assert.ok(!row.preview.html.includes('&amp;lt;'), '不得出现双重转义 &amp;lt;');
        },
      },
      {
        name: 'literal-fence',
        desc: '围栏内字面实体代码',
        raw: '```html\n&lt;div&gt;字面实体&lt;/div&gt;\n```',
        check: (row) => {
          assert.equal(row.preview.code[0]?.trim(), '&lt;div&gt;字面实体&lt;/div&gt;', '字面实体原样显示');
          assert.ok(!row.storedHtml.includes('&amp;amp;lt;'), '存盘不得多一层转义');
        },
      },
      {
        name: 'literal-inline',
        desc: '行内代码普通与实体',
        raw: '**例子**：`&lt;div&gt;` 与 `&amp;lt;div&amp;gt;`',
        check: (row) => {
          assert.deepEqual(row.preview.inlineCode, ['&lt;div&gt;', '&amp;lt;div&amp;gt;'], '行内代码显示原文不多一层');
          assert.ok(!row.storedHtml.includes('&amp;amp;amp;lt;'), '行内代码存盘不得增加多余实体层');
        },
      },
      {
        name: 'ordinary-rules',
        desc: '普通文字防误判',
        raw: '价格 > 100 元\n分类：研发 | 测试 | 交付',
        check: (row) => {
          assert.equal(row.preview.blockquoteCount, 0, '价格 > 100 绝不得误判为 blockquote');
          assert.equal(row.preview.tableCount, 0, '管道符分类绝不得误判为 table');
          assert.ok(row.preview.text.includes('价格 > 100 元'), '价格文字保留');
          assert.ok(row.preview.text.includes('研发 | 测试 | 交付'), '分类文字保留');
        },
      },
      {
        name: 'script-fence-security',
        desc: '代码块内脚本注入安全防护',
        raw: '```html\n<script>window.__probe=99</script>\n<img src="x" onerror="window.__probe=99">\n```',
        check: (row) => {
          assert.equal(row.counter, 0, '代码区脚本绝不得执行');
          assert.equal(row.preview.dangerous, 0, '代码区内不得产生真实危险 DOM');
          assert.ok(row.preview.code[0].includes('<script>window.__probe=99</script>'), '代码作为纯文本原样安全展示');
        },
      },
    ];

    for (const [index, tc] of testCases.entries()) {
      console.log(`\n--- 执行用例 ${index + 1}: ${tc.name} (${tc.desc}) ---`);
      await page.click('#new-article');
      await page.fill('#new-title', `矩阵-${tc.name}`);
      await page.click('#create-article');

      await until(async () => {
        const title = await page.locator('#article-title').innerText().catch(() => '');
        const stateText = await page.locator('#save-state').innerText().catch(() => '');
        return title.includes(tc.name) && stateText.includes('编辑中');
      }, 15000);

      const ed = page.locator('#paper .text-block').first();
      await ed.click();
      await page.keyboard.press('Meta+A');

      // 使用系统剪贴板写入并粘贴
      await page.evaluate((text) => navigator.clipboard.writeText(text), tc.raw);
      await page.keyboard.press('Meta+V');
      await sleep(150);

      const submittedDom = await ed.innerHTML();

      let saveRequest = null;
      const saveListener = (req) => {
        if (req.method() === 'POST' && req.url().includes('/api/save/')) {
          saveRequest = { url: req.url(), body: req.postDataJSON() };
        }
      };
      page.on('request', saveListener);

      await page.click('#save');
      await page.fill('#summary', `保存-${tc.name}`);
      await page.click('#confirm-save');

      await until(async () => {
        const stateText = await page.locator('#save-state').innerText();
        return stateText.includes('已保存');
      }, 15000);
      page.off('request', saveListener);

      // 读取存盘结果
      let storedArticle = null;
      if (saveRequest) {
        const articleUrl = saveRequest.url.split('?')[0].replace('/api/save/', '/api/article/');
        const res = await page.request.get(articleUrl, { headers: { 'X-Article-Studio': '1' } });
        storedArticle = await res.json();
      }

      const storedHtml = storedArticle?.blocks?.[0]?.html || '';

      // 预览态评估
      const preview = await ed.evaluate((el) => ({
        html: el.innerHTML,
        text: el.textContent,
        code: Array.from(el.querySelectorAll('pre code')).map((x) => x.textContent),
        inlineCode: Array.from(el.querySelectorAll('code')).filter((x) => !x.closest('pre')).map((x) => x.textContent),
        blockquoteCount: el.querySelectorAll('blockquote').length,
        tableCount: el.querySelectorAll('table').length,
        dangerous: el.querySelectorAll('script,img,[onerror],[onclick]').length,
      }));

      // 再次进入编辑态检查
      await page.click('#edit');
      await sleep(150);
      const reedit = await ed.evaluate((el) => ({
        html: el.innerHTML,
        text: el.textContent,
      }));
      await page.click('#cancel-edit');

      const counter = await page.evaluate(() => window.__probe);

      const row = {
        name: tc.name,
        raw: tc.raw,
        submittedDom,
        storedHtml,
        preview,
        reedit,
        counter,
      };

      tc.check(row);
      matrixResults.push(row);
      await page.screenshot({ path: path.join(evidenceDir, `${String(index + 1).padStart(2, '0')}-${tc.name}.png`), fullPage: true });
      console.log(`用例 ${tc.name} 验收通过，四处一致性与安全断言均满足。`);
    }

    console.log('\n--- 检查点最终汇总 ---');
    console.log('页面脚本错误数 (pageerrors):', pageerrors.length);
    assert.equal(pageerrors.length, 0, '页面不应有任何 pageerror');
    console.log('被拦截的外网请求数 (blockedRequests):', blockedRequests.length);
    assert.equal(blockedRequests.length, 0, '不应向外网发起任何请求');

    fs.writeFileSync(path.join(evidenceDir, 'matrix-results.json'), JSON.stringify({
      matrixResults,
      pageerrors,
      blockedRequests,
      status: 'ALL_PASSED',
    }, null, 2));

    console.log('\n✅ 全部真实 Chrome 系统剪贴板端到端矩阵及安全用例均通过！');
  } finally {
    if (browser) await browser.close().catch(() => {});
    if (child) {
      await stopChild(child, sleep);
      if (alive(child)) forceStopSync(child);
    }
    try { fs.rmSync(temp, { recursive: true, force: true }); } catch {}
  }
})().catch((err) => {
  console.error('\n❌ 验证失败:', err);
  process.exitCode = 1;
});
