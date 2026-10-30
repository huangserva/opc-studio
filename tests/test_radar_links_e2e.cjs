'use strict';
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const { chromium } = (() => {
  try { return require('playwright'); } catch {
    return require(path.join(process.env.HOME, '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright'));
  }
})();
const publicDir = path.resolve(__dirname, '../public');
const source = fs.readFileSync(path.join(publicDir, 'twitter-radar.js'), 'utf8');
const embedded = JSON.parse(source.match(/^const DATA_BUNDLE = (.+);$/m)[1]);
const now = Date.parse(embedded.updated_at.replace(' ', 'T') + '+08:00');
const brokenIds = ['2109483321453981696', '2109526716767228160', '2109297645164875776',
  '2109477012356702208', '2109559384724647936', '2109289299493699840'];
const rows = [...embedded.tweets, ...embedded.vip_tweets,
  ...embedded.topic_clusters.flatMap(topic => topic.embedded_tweets || [])];
const expected = new Set(rows.map(row => `https://x.com/${row.user}/status/${row.tweet_id || row.id}`));
const expectedProfiles = new Set([...rows.map(row => row.user),
  ...embedded.vip_bloggers.map(row => row.handle || row.u)].map(handle => `https://x.com/${handle}`));

test('隔离浏览器：话题、特关、低粉榜的原帖链接真实对应帖子，六个旧坏ID均不出现', { timeout: 30000 }, async t => {
  const server = http.createServer((req, res) => {
    const pathname = new URL(req.url, 'http://localhost').pathname;
    // 固定内嵌真实历史快照，禁止依赖正式8766或正式workspace的数据。
    if (pathname.endsWith('.json') || pathname.startsWith('/api/')) {
      res.writeHead(503); res.end('{}'); return;
    }
    const file = path.resolve(publicDir, '.' + pathname);
    if (!file.startsWith(publicDir + path.sep) || !fs.existsSync(file) || !fs.statSync(file).isFile()) {
      res.writeHead(404); res.end(); return;
    }
    res.setHeader('Content-Type', file.endsWith('.js') ? 'text/javascript' : file.endsWith('.css') ? 'text/css' : 'text/html');
    res.end(fs.readFileSync(file));
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  let browser;
  t.after(async () => {
    // after 按注册顺序执行：先关浏览器，避免 server.close 等它的连接而卡住。
    try {
      if (browser) await browser.close();
    } finally {
      await new Promise((resolve, reject) => {
        server.close(error => error ? reject(error) : resolve());
        // close 只回收空闲 HTTP 连接；尚未发请求的预连接也必须释放。
        server.closeAllConnections();
      });
    }
  });
  const origin = `http://127.0.0.1:${server.address().port}`;
  browser = await chromium.launch({ channel: 'chrome', headless: true, args: ['--disable-background-networking', '--disable-component-update'] });
  const context = await browser.newContext();
  await context.route('**/*', route => new URL(route.request().url()).origin === origin ? route.continue() : route.abort());
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', error => errors.push(String(error)));
  await page.addInitScript(frozen => {
    const RealDate = Date;
    window.Date = class extends RealDate {
      constructor(...args) { super(...(args.length ? args : [frozen])); }
      static now() { return frozen; }
    };
  }, now);
  await page.goto(origin + '/twitter-radar.html');
  await page.waitForFunction(() => typeof DATA_SOURCE !== 'undefined' && DATA_SOURCE.error !== null);
  for (const button of ['#tab-clusters-btn', '#tab-vip-btn', '#tab-feed-btn']) {
    await page.locator(button).click();
    const links = await page.locator('a.tweet-link-btn:visible').evaluateAll(els => els.map(el => el.href));
    assert.ok(links.length > 0, button + ' 必须实际渲染链接，不能空数组通过');
    for (const href of links) {
      if (new URL(href).pathname.includes('/status/')) {
        assert.ok(expected.has(href), '原帖链接必须由同一快照的真实作者和帖子ID组成：' + href);
        assert.match(href, /^https:\/\/x\.com\/[A-Za-z0-9_]+\/status\/[0-9]{1,25}$/);
      } else {
        assert.ok(expectedProfiles.has(href), '作者主页链接必须对应同一快照的作者：' + href);
        assert.match(href, /^https:\/\/x\.com\/[A-Za-z0-9_]+$/);
      }
      for (const id of brokenIds) assert.ok(!href.includes(id), '旧坏ID不得出现：' + id);
    }

  }
  assert.deepEqual(errors, []);
});
