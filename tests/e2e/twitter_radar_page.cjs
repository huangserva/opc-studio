'use strict';
/**
 * A2-b 推特雷达页面返修 · 隔离 Playwright 验收测试
 * 覆盖：复核 3 个阻断项（M1 穿透错位 / M2 未来时间 / M3 不完整 JSON）+ L1（无证据标题数字）
 *       + 原 13 条反例（防回退）+ 复核报告 4 个失败场景镜像 + 本轮 10 条自测反例。
 * 运行：RADAR_A2_DIR=<副本>/public RADAR_A2_EVIDENCE=<证据目录> [RADAR_A2_REAL_BUNDLE=<真实bundle>]
 * 说明：断言全部绑定到「被注入/被点击的那个具体元素」；pageerror 全程累计，不在 reload 时清空；
 *       固定浏览器时钟（NOW），避免 24h/48h 边界漂移；非本地 origin 一律 abort。
 */
const fs = require('fs');
const path = require('path');
const http = require('http');
const { chromium } = (() => {
  try { return require('playwright'); } catch (e) {
    return require(path.join(process.env.HOME, '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright'));
  }
})();

const PAGE_DIR = process.env.RADAR_A2_DIR || path.resolve(__dirname, '../../public');
const EVIDENCE = process.env.RADAR_A2_EVIDENCE || '/tmp/radar-a2b-deliver/screenshots';
fs.mkdirSync(EVIDENCE, { recursive: true });

const NOW = Date.now();
const HOUR = 3600 * 1000;
const TRACKS = ['claude_code', 'ai_models', 'github_open', 'money_indie', 'overseas_infra'];
let seq = 0;

function iso(ms) { return new Date(ms).toISOString(); }
function snowflakeAt(ms) { return (((BigInt(Math.round(ms)) - 1288834974657n) << 22n) | BigInt(++seq)).toString(); }
function mkTweet(o) {
  o = o || {};
  const ms = o.ms !== undefined ? o.ms : NOW - (o.hours === undefined ? 1 : o.hours) * HOUR;
  const id = o.tweetId !== undefined ? o.tweetId : snowflakeAt(ms);
  const t = {
    tweet_id: id,
    user: o.handle || 'Author_' + (seq),
    name: o.name || '作者' + seq,
    fc: o.fc === undefined ? 800 : o.fc,
    views: o.views === undefined ? 25000 : o.views,
    likes: o.likes === undefined ? 260 : o.likes,
    retweets: 3,
    ratio: 6.5,
    track: o.track || 'ai_models',
    track_label: '赛道-' + (o.track || 'ai_models'),
    tier_badge: '审计档位',
    tier_class: 'tier-10k',
    text: o.text || ('正文样本 ' + seq + '：把 Agent 流水线跑通'),
    url: o.url || ('https://x.com/' + (o.handle || 'x') + '/status/' + id),
    published_at: iso(ms),
    fetched_at: iso(NOW - 60000),
    is_stale: o.isStale === true
  };
  if (o.dropPublishedAt) delete t.published_at;
  if (o.dropFetchedAt) delete t.fetched_at;
  return t;
}

function buildVipBloggers(n, handles) {
  const out = [];
  for (let i = 0; i < n; i++) {
    out.push({ handle: (handles && handles[i]) || ('vip_' + i), name: '特关' + i, fc: 1000 + i, desc: '简介', track_label: '⭐ 特别关注博主', top_views: 0 });
  }
  return out;
}

// 基础完整 bundle（A1 新格式）
function baseBundle(over) {
  const t = mkTweet({ handle: 'Audit_User', name: '审计用户', ms: NOW - 3 * HOUR });
  const v = mkTweet({ handle: 'Vip_User', name: '特关审计', fc: 42000, views: 90000, likes: 600, ms: NOW - 2 * HOUR });
  const b = {
    updated_at: iso(NOW),
    run_id: 'audit-run-' + NOW,
    coverage: { target: 51, success: 51, failed: 0, complete: true },
    today_virals: 0,
    h48_virals: 0,
    tweets: [t],
    vip_tweets: [v],
    vip_bloggers: buildVipBloggers(51, ['Audit_User', 'Vip_User']),
    topic_clusters: [{
      id: 'topic-evidence', track: 'ai_models', tag: '模型', title: '有证据的话题', heat: '2.5万曝光',
      burst_summary: '有依据的摘要', core_conflict: '实测', article_plan: '计划', counter_check: '反查',
      embedded_tweets: [t]
    }]
  };
  return Object.assign(b, over || {});
}

// 20 位作者（曝光降序），用于穿透目标回归
function rankBundle() {
  const authors = Array.from({ length: 20 }, (_, i) =>
    mkTweet({ handle: 'Rank_User_' + i, name: '排名' + i, views: 300000 - i * 10000, fc: 500 + i, ms: NOW - (1 + i * 0.2) * HOUR }));
  return baseBundle({ tweets: authors, vip_tweets: [] });
}

// 静态服务（随机端口，bundle 响应可编程控制）
const state = { status: 200, body: JSON.stringify(baseBundle()) };
const MIME = { '.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8', '.css': 'text/css; charset=utf-8', '.json': 'application/json; charset=utf-8' };
const requests = [];
let server, browser, context, origin;

const results = [];
const pageErrors = [];   // 真正的 JS 异常（pageerror），全程累计，reload 不清空
const consoleErrors = []; // 真正的 console.error（JS 报错），单独统计
const resourceErrors = []; // 浏览器对故意注入的网络失败的固有上报
const external = [];     // 外网请求

function ensure(ok, msg) { if (!ok) throw new Error(msg); }

async function openPage(options) {
  options = options || {};
  state.status = options.status === undefined ? 200 : options.status;
  state.body = options.raw !== undefined ? options.raw : JSON.stringify(options.bundle === undefined ? baseBundle() : options.bundle);
  const p = await context.newPage();
  p.on('pageerror', (e) => pageErrors.push({ scenario: currentScenario, message: String(e && e.message || e), url: p.url() }));
  p.on('console', (m) => {
    if (m.type() !== 'error') return;
    const text = m.text();
    // 浏览器对「故意注入的 503 / 非法 JSON」等网络失败的固有上报，不算页面 JS 错误
    if (/Failed to load resource/.test(text)) { resourceErrors.push({ scenario: currentScenario, message: text, url: p.url() }); return; }
    consoleErrors.push({ scenario: currentScenario, message: 'console.error: ' + text, url: p.url() });
  });
  await p.addInitScript((now) => {
    const RealDate = Date;
    class FrozenDate extends RealDate {
      constructor(...args) { super(...(args.length ? args : [now])); }
      static now() { return now; }
    }
    window.Date = FrozenDate;
  }, NOW);
  await p.goto(origin + '/twitter-radar.html');
  await p.waitForFunction(() => typeof DATA_SOURCE !== 'undefined' && (DATA_SOURCE.kind === 'json' || DATA_SOURCE.error !== null), null, { timeout: 15000 });
  return p;
}

let currentScenario = 'init';
async function scenario(id, name, fn) {
  currentScenario = id;
  const opened = [];
  const make = async (opts) => { const p = await openPage(opts); opened.push(p); return p; };
  const shotPath = path.join(EVIDENCE, id + '.png');
  try {
    const detail = await fn(make);
    results.push({ id, name, ok: true, detail: detail === undefined ? '' : String(detail), shot: shotPath });
    console.log('  ✅ ' + id + ' ' + name + (detail ? ' → ' + String(detail).replace(/\s+/g, ' ').slice(0, 220) : ''));
  } catch (e) {
    results.push({ id, name, ok: false, detail: String(e && e.message || e), shot: shotPath });
    console.log('  ❌ ' + id + ' ' + name + ' → ' + String(e && e.message || e));
    if (opened.length) {
      try { await opened[opened.length - 1].screenshot({ path: path.join(EVIDENCE, id + '-failure.png'), fullPage: true }); } catch (e2) {}
    }
  } finally {
    for (const p of opened) { try { await p.close(); } catch (e) {} }
  }
}

async function shot(p, name) { await p.screenshot({ path: path.join(EVIDENCE, name), fullPage: true }); return name; }
async function feed(p) { await p.locator('#tab-feed-btn').click(); await p.waitForTimeout(120); }
async function clusters(p) { await p.locator('#tab-clusters-btn').click(); await p.waitForTimeout(120); }
function receipt(p) { return p.locator('#radar-read-receipt').innerText(); }
function banner(p) { return p.locator('#radar-data-banner').innerText(); }

// 绑定到具体被注入元素：无 inline 事件属性、无注入 flag、无注入节点
async function assertNoInjection(p, flags) {
  await p.waitForTimeout(80);
  const x = await p.evaluate((names) => ({
    inlineAttrs: [...document.querySelectorAll('*')].flatMap(el =>
      [...el.attributes].filter(a => /^on/i.test(a.name)).map(a => el.tagName + '@' + a.name + '=' + a.value)),
    flags: names.filter(n => window[n] !== undefined),
    injectedAttrs: [...document.querySelectorAll('[data-audit-injected]')].length,
    imgs: document.querySelectorAll('#tweets-feed img,#topics-list img,#vip-tweets-feed img,#blogger-dossier-content img').length
  }), flags);
  ensure(x.inlineAttrs.length === 0, 'inline 事件属性: ' + JSON.stringify(x.inlineAttrs));
  ensure(x.flags.length === 0, '注入 flag 被置位: ' + JSON.stringify(x.flags));
  ensure(x.injectedAttrs === 0, '注入了额外 data-* 属性');
  ensure(x.imgs === 0, '出现了注入 img 节点 ' + x.imgs);
  return x;
}

(async () => {
  // ---------------- 服务与浏览器 ----------------
  server = http.createServer((req, res) => {
    const u = new URL(req.url, 'http://127.0.0.1');
    requests.push(u.pathname);
    if (u.pathname === '/twitter-radar-bundle.json') {
      res.writeHead(state.status, { 'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'no-store' });
      res.end(state.body);
      return;
    }
    // 隔离副本只放页面需要的文件：侧边栏脚本/图标给空桩，避免与页面逻辑无关的 404 噪音
    if (u.pathname === '/workspace-nav.js') { res.writeHead(200, { 'Content-Type': 'text/javascript; charset=utf-8' }); res.end('/* 隔离副本空桩 */'); return; }
    if (u.pathname === '/workspace-nav.css') { res.writeHead(200, { 'Content-Type': 'text/css; charset=utf-8' }); res.end('/* 隔离副本空桩 */'); return; }
    if (u.pathname === '/icon.svg' || u.pathname === '/favicon.ico') { res.writeHead(204); res.end(); return; }
    const name = u.pathname === '/' ? '/twitter-radar.html' : u.pathname;
    const file = path.join(PAGE_DIR, path.normalize(name).replace(/^(\.\.[/\\])+/, ''));
    if (!file.startsWith(PAGE_DIR) || !fs.existsSync(file) || !fs.statSync(file).isFile()) { res.writeHead(404); res.end(); return; }
    res.writeHead(200, { 'Content-Type': MIME[path.extname(file)] || 'application/octet-stream' });
    res.end(fs.readFileSync(file));
  });
  await new Promise((r) => server.listen(0, '127.0.0.1', r));
  ensure(server.address().port !== 8766, '不得占用正式端口 8766');
  origin = 'http://127.0.0.1:' + server.address().port;
  browser = await chromium.launch({ channel: 'chrome', headless: true, args: ['--disable-background-networking', '--disable-component-update'] });
  context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, timezoneId: 'America/Los_Angeles' });
  await context.route('**/*', (r) => {
    if (new URL(r.request().url()).origin !== origin) { external.push(r.request().url()); return r.abort(); }
    return r.continue();
  });

  console.log('=== A2-b 页面返修反例实测（副本：' + PAGE_DIR + '，时钟固定 ' + iso(NOW) + '）===');

  // ================= 原 13 条（防回退） =================
  await scenario('R01-零值', 'today_virals=0 / h48_virals=0 显示 0，不是 14/28', async (make) => {
    const p = await make({ bundle: baseBundle({ today_virals: 0, h48_virals: 0 }) });
    const r = await receipt(p);
    ensure(r.includes('今日爆款：0 篇') && r.includes('48h 累计：0 篇'), '回执零值丢失: ' + r);
    ensure(!/今日爆款：\d+ 篇/.test(r.replace('今日爆款：0 篇', '')), '出现非零今日爆款: ' + r);
    await shot(p, 'R01-零值.png');
    return r.replace(/\s+/g, ' ').slice(0, 150);
  });

  await scenario('R02-HTTP503', 'HTTP 503 → 读取失败 + 离线旧数据 + 不显示「已是最新」', async (make) => {
    const p = await make({ status: 503 });
    const b = await banner(p);
    const btn = await p.locator('#btn-refresh-radar').innerText();
    const topics = await p.locator('#topics-list .topic-card').count();
    ensure(b.includes('读取失败') && b.includes('HTTP 503'), '缺读取失败原因: ' + b);
    ensure(b.includes('离线旧数据'), '缺离线旧数据标注: ' + b);
    ensure(!btn.includes('已是最新'), '按钮仍称已是最新: ' + btn);
    ensure(topics > 0, '未退回内嵌数据');
    ensure(!(await receipt(p)).includes('audit-run'), '失败读取却显示新 run_id');
    await shot(p, 'R02-HTTP503.png');
    return '按钮=' + btn.trim() + ' 横幅=' + b.replace(/\s+/g, ' ').slice(0, 90) + ' 内嵌话题卡=' + topics;
  });

  await scenario('R03-非法JSON', '返回非法 JSON → 读取失败（JSON 解析失败）+ 离线旧数据', async (make) => {
    const p = await make({ raw: '这不是 JSON <<<>>>' });
    const b = await banner(p);
    ensure(b.includes('读取失败') && b.includes('JSON 解析失败'), '缺解析失败原因: ' + b);
    ensure(b.includes('离线旧数据'), '缺离线旧数据标注');
    ensure(!(await p.locator('#btn-refresh-radar').innerText()).includes('已是最新'), '按钮仍称已是最新');
    await shot(p, 'R03-非法JSON.png');
    return b.replace(/\s+/g, ' ').slice(0, 120);
  });

  await scenario('R04-URL注入', '恶意 url 只绑定被注入的那条帖：不执行、链接按 tweet_id 重建', async (make) => {
    const bad = mkTweet({ handle: 'Url_Victim', name: 'URL受害者', text: '唯一标记 URLVICTIM', url: 'https://x.com/a" onmouseover="window.auditXssUrl=1', ms: NOW - 2 * HOUR });
    const p = await make({ bundle: baseBundle({ tweets: [bad, mkTweet({ handle: 'Other_A', name: '其他', ms: NOW - 3 * HOUR })] }) });
    await feed(p);
    const card = p.locator('#tweets-feed .tweet-card').filter({ hasText: 'URLVICTIM' });
    ensure(await card.count() === 1, '未唯一定位被注入卡片');
    const link = card.locator('a.tweet-link-btn.primary');
    const href = await link.getAttribute('href');
    ensure(href === 'https://x.com/Url_Victim/status/' + bad.tweet_id, '链接未按 tweet_id 重建: ' + href);
    await link.hover();
    const x = await assertNoInjection(p, ['auditXssUrl']);
    ensure(await link.getAttribute('href') === href, 'hover 后 href 被改写');
    await shot(p, 'R04-URL注入.png');
    return 'href=' + href + ' 无注入=' + JSON.stringify(x);
  });

  await scenario('R05-handle注入', '恶意 handle：无穿透按钮/无外链；点击合法博主穿透到本人且不执行', async (make) => {
    const badHandle = "a');window.auditXssHandle=1;('";
    const bad = mkTweet({ handle: badHandle, name: 'Handle受害者', text: '唯一标记 HANDLEBAD', ms: NOW - 2 * HOUR });
    const good = mkTweet({ handle: 'Good_User', name: '正常人', text: '唯一标记 GOODCARD', ms: NOW - 3 * HOUR });
    const p = await make({ bundle: baseBundle({ tweets: [bad, good], vip_bloggers: buildVipBloggers(3, [badHandle]) }) });
    await feed(p);
    const badCard = p.locator('#tweets-feed .tweet-card').filter({ hasText: 'HANDLEBAD' });
    const goodCard = p.locator('#tweets-feed .tweet-card').filter({ hasText: 'GOODCARD' });
    ensure(await badCard.count() === 1, '未唯一定位恶意 handle 卡片');
    ensure(await badCard.locator('[data-drill-handle]').count() === 0, '恶意 handle 生成了穿透按钮');
    ensure(await badCard.locator('a').count() === 0, '恶意 handle 生成了外链');
    await goodCard.locator('[data-drill-handle="Good_User"]').click();
    await p.waitForTimeout(150);
    const h = await p.locator('#blogger-dossier-content .d-handle').innerText();
    ensure(h === '@Good_User', '点击合法博主穿透错位: ' + h);
    const x = await assertNoInjection(p, ['auditXssHandle']);
    await shot(p, 'R05-handle注入.png');
    return '恶意卡无按钮无外链；合法博主穿透=' + h + ' ' + JSON.stringify(x);
  });

  await scenario('R06-文本注入', '昵称/正文的 <img onerror> 只作为字面文本显示', async (make) => {
    const payload = '<img src=x onerror=window.auditXssText=1>';
    const t = mkTweet({ handle: 'Text_Victim', name: payload, text: payload + ' 唯一标记 TEXTBAD', ms: NOW - 2 * HOUR });
    const p = await make({ bundle: baseBundle({ tweets: [t] }) });
    await feed(p);
    const card = p.locator('#tweets-feed .tweet-card').filter({ hasText: 'TEXTBAD' });
    ensure(await card.count() === 1, '未唯一定位文本注入卡片');
    const nameText = await card.locator('.t-name').innerText();
    const bodyText = await card.locator('.tweet-text').innerText();
    ensure(nameText === payload, '昵称被改写: ' + nameText);
    ensure(bodyText.indexOf(payload) >= 0, '正文未按字面保留: ' + bodyText);
    const x = await assertNoInjection(p, ['auditXssText']);
    await shot(p, 'R06-文本注入.png');
    return '昵称字面一致，正文字面保留，img 节点 0';
  });

  await scenario('R07-覆盖率不完整', 'coverage.complete=false → 本轮采集不完整（47/51）', async (make) => {
    const p = await make({ bundle: baseBundle({ coverage: { target: 51, success: 47, failed: 4, complete: false } }) });
    const b = await banner(p);
    ensure(b.includes('本轮采集不完整（47/51）'), '缺不完整提示: ' + b);
    ensure((await receipt(p)).includes('47/51'), '回执未显示覆盖率');
    await shot(p, 'R07-覆盖率不完整.png');
    return b.replace(/\s+/g, ' ').slice(0, 100);
  });

  await scenario('R08-标旧帖', 'is_stale=true → 灰色「旧数据（采于 北京时间）」', async (make) => {
    const t = mkTweet({ handle: 'Stale_One', name: '标旧的', isStale: true, ms: NOW - 4 * HOUR });
    t.fetched_at = '2026-10-15T01:02:00Z';
    const p = await make({ bundle: baseBundle({ tweets: [t] }) });
    await feed(p);
    const badge = p.locator('#tweets-feed .tweet-stale-badge');
    ensure(await badge.count() === 1, '标旧标签数量异常: ' + await badge.count());
    const txt = await badge.innerText();
    ensure(txt.includes('旧数据') && txt.includes('09:02'), '标旧时间展示错误: ' + txt);
    await shot(p, 'R08-标旧帖.png');
    return txt.replace(/\s+/g, ' ');
  });

  await scenario('R09-时间未知', '无 published_at 且 tweet_id 非法 → 时间未知，不进今日/最新', async (make) => {
    const unknown = mkTweet({ handle: 'Unknown_Time', name: '时间未知', tweetId: 'not-a-snowflake', dropPublishedAt: true, views: 999999, ms: NOW });
    const known = mkTweet({ handle: 'Known_Time', name: '时间正常', ms: NOW - HOUR });
    const p = await make({ bundle: baseBundle({ tweets: [unknown, known] }) });
    await feed(p);
    const note = await p.locator('#feed-unknown-note').innerText();
    ensure(note.includes('时间未知') && note.includes('Unknown_Time'), '缺可见提示或未点名: ' + note);
    let handles = await p.locator('#tweets-feed .t-handle').allInnerTexts();
    ensure(!handles.includes('@Unknown_Time'), '时间未知帖进入了 24h 窗口: ' + JSON.stringify(handles));
    await p.locator('.sort-btn[data-sort="time"]').click(); await p.waitForTimeout(150);
    handles = await p.locator('#tweets-feed .t-handle').allInnerTexts();
    ensure(!handles.includes('@Unknown_Time'), '时间未知帖排到了最新: ' + JSON.stringify(handles));
    ensure(handles.includes('@Known_Time'), '正常帖被误伤');
    await shot(p, 'R09-时间未知.png');
    return note.replace(/\s+/g, ' ').slice(0, 110);
  });

  await scenario('R10-null指标', 'views/likes=null → 显示「—」，排在真实 0 之后且不报错', async (make) => {
    const nullT = mkTweet({ handle: 'Null_Views', name: '未知曝光', views: null, likes: null, ms: NOW - 2 * HOUR });
    const zeroT = mkTweet({ handle: 'Zero_Views', name: '零曝光', views: 0, likes: 0, ms: NOW - 3 * HOUR });
    const p = await make({ bundle: baseBundle({ tweets: [nullT, zeroT] }) });
    await feed(p);
    await p.locator('.sort-btn[data-sort="views"]').click(); await p.waitForTimeout(150);
    const handles = await p.locator('#tweets-feed .t-handle').allInnerTexts();
    ensure(handles[0] === '@Zero_Views' && handles[1] === '@Null_Views', 'null 与 0 排序错位: ' + JSON.stringify(handles));
    const nullCard = p.locator('#tweets-feed .tweet-card').filter({ hasText: '@Null_Views' });
    ensure(/曝光浏览量\s*—/.test(await nullCard.innerText()), 'null 指标未显示 —');
    await shot(p, 'R10-null指标.png');
    return '排序=' + JSON.stringify(handles) + ' null 指标显示 —';
  });

  await scenario('R11-旧格式bundle', '旧格式 bundle 正常渲染，缺失元字段显示「未知」', async (make) => {
    const b = baseBundle();
    delete b.run_id; delete b.coverage; delete b.today_virals; delete b.h48_virals;
    // 注意：embedded_tweets 与 tweets 可能共享同一个对象引用，必须 map 成新对象，
    // 否则同一对象被转换两次会把 id 抹掉（复核报告也踩过这个夹具坑）。
    const toLegacy = (t) => {
      const copy = Object.assign({}, t);
      copy.id = copy.tweet_id;
      delete copy.tweet_id; delete copy.published_at; delete copy.fetched_at; delete copy.is_stale;
      return copy;
    };
    b.tweets = b.tweets.map(toLegacy);
    b.vip_tweets = b.vip_tweets.map(toLegacy);
    b.topic_clusters.forEach((c) => { c.embedded_tweets = (c.embedded_tweets || []).map(toLegacy); });
    const p = await make({ bundle: b });
    const r = await receipt(p);
    ensure(r.includes('run_id：未知') && r.includes('覆盖率：未知') && r.includes('今日爆款：未知 篇'), '元数据未显示未知: ' + r);
    await feed(p);
    ensure(await p.locator('#tweets-feed .tweet-card').count() === 1, '旧格式帖子未渲染');
    await clusters(p);
    ensure(await p.locator('#topics-list .topic-card').count() === 1, '旧格式话题未渲染');
    await shot(p, 'R11-旧格式bundle.png');
    return r.replace(/\s+/g, ' ').slice(0, 150);
  });

  await scenario('R12-功能回归', '4 视图 / 6 赛道（内容断言）/ 搜索 / 分页 / 穿透目标 全可用', async (make) => {
    const tweets = Array.from({ length: 37 }, (_, i) =>
      mkTweet({ handle: 'Author_' + i, name: '作者' + i, views: 200000 - i * 3000, fc: 500 + i, track: TRACKS[i % 5], text: '搜索用正文 ' + i, ms: NOW - (1 + i * 0.2) * HOUR }));
    const b = baseBundle({
      tweets,
      topic_clusters: TRACKS.map((t, i) => ({ id: t, track: t, tag: t, title: '话题-' + t, heat: '1万曝光', burst_summary: '证据', core_conflict: 'x', article_plan: 'y', counter_check: 'z', embedded_tweets: [tweets[i]] }))
    });
    const p = await make({ bundle: b });
    for (const tab of ['vip', 'feed', 'blogger', 'clusters']) {
      await p.locator('#tab-' + tab + '-btn').click(); await p.waitForTimeout(120);
      ensure(await p.locator('#' + tab + '-view-section').isVisible(), '视图不可见: ' + tab);
    }
    await feed(p);
    ensure(await p.locator('.track-pill').count() === 6, '赛道按钮数量不是 6');
    for (const track of TRACKS) {
      await p.locator('.track-pill[data-track="' + track + '"]').click(); await p.waitForTimeout(150);
      const badges = await p.locator('#tweets-feed .tweet-track-badge').allInnerTexts();
      ensure(badges.length > 0, '赛道结果为空: ' + track);
      ensure(badges.every(x => x === '赛道-' + track), '赛道内容不匹配: ' + track + ' → ' + JSON.stringify(badges.slice(0, 3)));
    }
    await p.locator('.track-pill[data-track="all"]').click(); await p.waitForTimeout(150);
    ensure(await p.locator('#tweets-feed .tweet-card').count() === 30, '首页不是 30 条');
    await p.locator('#search-input').fill('搜索用正文 36'); await p.waitForTimeout(200);
    ensure(await p.locator('#tweets-feed .tweet-card').count() === 1, '搜索命中数不对');
    await p.locator('#search-input').fill(''); await p.waitForTimeout(200);
    await p.locator('.page-num-btn[data-page="2"]').click(); await p.waitForTimeout(250);
    ensure(await p.locator('#tweets-feed .tweet-card').count() === 7, '第 2 页数量不对');
    const page2First = await p.locator('#tweets-feed .t-handle').first().innerText();
    await p.locator('.page-num-btn[data-page="1"]').click(); await p.waitForTimeout(250);
    const page1First = await p.locator('#tweets-feed .t-handle').first().innerText();
    ensure(page1First !== page2First, '两页内容相同');
    await p.locator('#tweets-feed [data-drill-handle="Author_5"]').click(); await p.waitForTimeout(200);
    ensure(await p.locator('#blogger-dossier-content .d-handle').innerText() === '@Author_5', '博主穿透目标错误');
    await shot(p, 'R12-功能回归.png');
    return '5 赛道内容断言通过；分页 30+7；穿透=@Author_5';
  });

  await scenario('R13-无凭据字样', '两个页面文件不出现 AIsa 凭据字样', async () => {
    const js = fs.readFileSync(path.join(PAGE_DIR, 'twitter-radar.js'), 'utf8');
    const html = fs.readFileSync(path.join(PAGE_DIR, 'twitter-radar.html'), 'utf8');
    const needle = new RegExp('sk' + '-' + 'aisa');
    ensure(!needle.test(js) && !needle.test(html), '页面出现凭据字样');
    ensure(!/onclick=|onmouseover=|onerror=/i.test(js + html), '残留 inline 事件属性');
    return { files: 2, matches: 0 };
  });

  // ================= M1：博主穿透错位 =================
  await scenario('M1-穿透热门榜以外', '第 1/9/20/最后 1 名博主 → 穿透标题都是被点的那个人', async (make) => {
    const p = await make({ bundle: rankBundle() });
    await feed(p);
    const picked = ['Rank_User_0', 'Rank_User_8', 'Rank_User_19'];
    const seen = [];
    for (const handle of picked) {
      const card = p.locator('#tweets-feed .tweet-card').filter({ hasText: '@' + handle });
      ensure(await card.count() === 1, '未唯一定位 ' + handle + ' 的卡片');
      await card.locator('[data-drill-handle="' + handle + '"]').click();
      await p.waitForTimeout(200);
      const h = await p.locator('#blogger-dossier-content .d-handle').innerText();
      const n = await p.locator('#blogger-dossier-content .d-name').innerText();
      ensure(h === '@' + handle, '穿透错位: 点 ' + handle + ' 显示 ' + h);
      ensure(n === '排名' + handle.split('_').pop(), '档案昵称错误: ' + n);
      seen.push(handle + '→' + h);
      await feed(p); // 回到列表继续点下一个
    }
    await shot(p, 'M1-穿透热门榜以外.png');
    return seen.join(', ');
  });

  await scenario('M1-搜索后穿透', '搜索过滤后再点博主，仍穿透到被点的人', async (make) => {
    const p = await make({ bundle: rankBundle() });
    await feed(p);
    await p.locator('#search-input').fill('Rank_User_13'); await p.waitForTimeout(220);
    const cards = await p.locator('#tweets-feed .tweet-card').count();
    ensure(cards === 1, '搜索未唯一命中: ' + cards);
    await p.locator('#tweets-feed [data-drill-handle="Rank_User_13"]').click(); await p.waitForTimeout(200);
    const h = await p.locator('#blogger-dossier-content .d-handle').innerText();
    ensure(h === '@Rank_User_13', '搜索后穿透错位: ' + h);
    const currentChip = await p.locator('.blogger-chip-current').innerText().catch(() => '');
    ensure(currentChip.includes('@Rank_User_13'), '未显示当前穿透对象: ' + currentChip);
    await shot(p, 'M1-搜索后穿透.png');
    return '命中 ' + cards + ' 条，穿透=' + h + '，当前穿透标记=' + currentChip.replace(/\s+/g, ' ').slice(0, 60);
  });

  // ================= M2：未来时间 =================
  await scenario('M2-未来时间异常', '发布时间 = 当前+2分钟 / +10秒 / +1天 → 时间异常且不进今日/48h/最新', async (make) => {
    const specs = [['Fut_Plus2Min', 2 * 60000], ['Fut_Plus10Sec', 10000], ['Fut_Plus1Day', 24 * HOUR]];
    const tweets = specs.map(([h, ahead]) => mkTweet({ handle: h, name: '未来帖-' + h, views: 500000, ms: NOW + ahead }));
    const past = mkTweet({ handle: 'Past_1Min', name: '过去1分钟', views: 400000, ms: NOW - 60000 });
    const p = await make({ bundle: baseBundle({ tweets: tweets.concat([past]) }) });
    await feed(p);
    const note = await p.locator('#feed-unknown-note').innerText();
    ensure(note.includes('时间异常'), '缺时间异常提示: ' + note);
    specs.forEach(([h]) => ensure(note.includes(h), '时间异常提示未点名 ' + h + ': ' + note));
    let handles = await p.locator('#tweets-feed .t-handle').allInnerTexts();
    ensure(!specs.some(([h]) => handles.includes('@' + h)), '未来帖进入 24h 窗口: ' + JSON.stringify(handles));
    ensure(handles.includes('@Past_1Min'), '正常帖被误伤');
    await p.locator('.time-btn[data-time="48h"]').click(); await p.waitForTimeout(200);
    handles = await p.locator('#tweets-feed .t-handle').allInnerTexts();
    ensure(!specs.some(([h]) => handles.includes('@' + h)), '未来帖进入 48h 窗口: ' + JSON.stringify(handles));
    await p.locator('.sort-btn[data-sort="time"]').click(); await p.waitForTimeout(200);
    handles = await p.locator('#tweets-feed .t-handle').allInnerTexts();
    ensure(handles[0] === '@Past_1Min', '未来帖排到了最新: ' + JSON.stringify(handles));
    const r = await receipt(p);
    ensure(r.includes('时间异常：3 条'), '回执未统计时间异常: ' + r);
    await shot(p, 'M2-未来时间异常.png');
    return note.replace(/\s+/g, ' ').slice(0, 130) + ' ｜ 24h 窗口=' + JSON.stringify(handles);
  });

  await scenario('M2b-过去1分钟', '发布时间 = 当前 -1 分钟 → 正常进入今日', async (make) => {
    const t = mkTweet({ handle: 'Past_1Min_Only', name: '过去一分钟', views: 120000, ms: NOW - 60000 });
    const p = await make({ bundle: baseBundle({ tweets: [t] }) });
    await feed(p);
    const handles = await p.locator('#tweets-feed .t-handle').allInnerTexts();
    ensure(handles.includes('@Past_1Min_Only'), '正常帖未进入今日: ' + JSON.stringify(handles));
    const tag = await p.locator('#tweets-feed .tweet-time-tag').first().innerText();
    ensure(!/时间异常|时间未知/.test(tag), '正常帖被标异常: ' + tag);
    const r = await receipt(p);
    ensure(r.includes('时间异常：0 条'), '回执异常计数不为 0: ' + r);
    await shot(p, 'M2b-过去1分钟.png');
    return '进入今日，标签=' + tag.replace(/\s+/g, ' ');
  });

  // ================= M3：不完整 JSON =================
  const incompleteCases = [
    ['M3a-缺tweets', (b) => { delete b.tweets; }],
    ['M3b-缺vip_tweets', (b) => { delete b.vip_tweets; }],
    ['M3c-缺topic_clusters', (b) => { delete b.topic_clusters; }],
    ['M3d-字段类型错误', (b) => { b.vip_tweets = 'not-an-array'; }]
  ];
  for (const [id, mutate] of incompleteCases) {
    await scenario(id, 'JSON 缺/错必需列表 → 数据不完整 + 整页退回内嵌 + 不显示新 run_id', async (make) => {
      const b = baseBundle({ run_id: 'partial-new', updated_at: iso(NOW) });
      mutate(b);
      const p = await make({ bundle: b });
      const bn = await banner(p);
      const r = await receipt(p);
      ensure(bn.includes('读取失败') && bn.includes('数据不完整'), '缺数据不完整提示: ' + bn);
      ensure(bn.includes('离线旧数据'), '未退回内嵌数据: ' + bn);
      ensure(!r.includes('partial-new'), '回执仍显示新 run_id: ' + r);
      ensure(!/覆盖率：51\/51/.test(r), '回执仍显示新覆盖率: ' + r);
      const topics = await p.locator('#topics-list .topic-card').count();
      ensure(topics >= 10, '未使用内嵌话题（内嵌 10 个，实际 ' + topics + '）');
      await feed(p);
      ensure(await p.locator('#tweets-feed .tweet-card').count() > 0, '内嵌低粉榜为空');
      await clusters(p);
      ensure(!(await p.locator('#topics-list').innerText()).includes('有证据的话题'), '内嵌数据被清空/污染');
      await shot(p, id + '.png');
      return bn.replace(/\s+/g, ' ').slice(0, 130);
    });
  }

  // ================= L1：无证据话题标题数字 =================
  await scenario('L1-无证据标题', '无支持帖的话题：只显示「暂无证据」，标题/热度/摘要都不带旧数字', async (make) => {
    const b = baseBundle({
      topic_clusters: [
        { id: 'empty', track: 'ai_models', tag: 'AI', title: '999万曝光实测战绩', heat: '999999万', burst_summary: 'CLAIM_EMPTY_999', core_conflict: '冲突断言', article_plan: '规划断言', counter_check: '反查断言', embedded_tweets: [] },
        { id: 'expired', track: 'github_open', tag: '开源', title: '过期主题', heat: '88888万', burst_summary: 'CLAIM_EXPIRED_888', core_conflict: 'x', article_plan: 'y', counter_check: 'z', embedded_tweets: [mkTweet({ handle: 'Old_One', name: '过期帖', ms: NOW - 49 * HOUR })] },
        { id: 'ok', track: 'claude_code', tag: '代码', title: '有证据的主题', heat: '3万曝光', burst_summary: '真实摘要', core_conflict: 'x', article_plan: 'y', counter_check: 'z', embedded_tweets: [mkTweet({ handle: 'Fresh_One', name: '新鲜帖', ms: NOW - 2 * HOUR })] }
      ]
    });
    const p = await make({ bundle: b });
    await clusters(p);
    const cards = p.locator('#topics-list .topic-no-evidence-card');
    ensure(await cards.count() === 2, '无证据卡数量不对: ' + await cards.count());
    for (let i = 0; i < await cards.count(); i++) {
      const txt = await cards.nth(i).innerText();
      ensure(txt.includes('暂无证据'), '第 ' + i + ' 张无证据卡缺提示');
      ensure(!/999万曝光实测战绩|999999万|88888万/.test(txt), '第 ' + i + ' 张仍保留旧数字断言: ' + txt);
      ensure(!/CLAIM_/.test(txt), '第 ' + i + ' 张仍显示旧摘要: ' + txt);
      ensure(!/曝光\s*\d|\d+\s*万/.test(txt), '第 ' + i + ' 张仍有曝光数字: ' + txt);
    }
    const all = await p.locator('#topics-list').innerText();
    ensure(!all.includes('999万曝光实测战绩'), '旧数字标题仍出现在话题区');
    ensure(all.includes('有证据的主题'), '有证据话题被误伤');
    await shot(p, 'L1-无证据标题.png');
    return '2 张无证据卡均无数字断言；有证据话题保留';
  });

  // ================= 复核报告 4 个失败场景镜像 =================
  await scenario('E10-未来2分钟(复核镜像)', '复核 E10：未来 2 分钟帖不作为今日新帖', async (make) => {
    const t = mkTweet({ handle: 'Future_2_Min', name: '未来2分钟', ms: NOW + 2 * 60000 });
    const p = await make({ bundle: baseBundle({ tweets: [t] }) });
    await feed(p);
    const hs = await p.locator('#tweets-feed .t-handle').allInnerTexts();
    ensure(!hs.includes('@Future_2_Min'), '未来帖被接受: ' + JSON.stringify(hs));
    return '今日窗口=' + JSON.stringify(hs);
  });

  await scenario('E15-缺列表JSON(复核镜像)', '复核 E15：缺列表 JSON 不把内嵌旧数据标成本轮成功', async (make) => {
    const p = await make({ bundle: { run_id: 'partial-new', updated_at: iso(NOW), coverage: { target: 51, success: 51, failed: 0, complete: true }, tweets: [] } });
    const r = await receipt(p);
    const topics = await p.locator('#topics-list .topic-card').count();
    ensure(!(r.includes('partial-new') && topics > 0), '新 run 与内嵌话题混合 topics=' + topics);
    ensure(r.includes('run_id：未知'), '回执 run_id 未显示未知: ' + r);
    return 'receipt run_id 未知，topics=' + topics;
  });

  await scenario('E16-第9名以外穿透(复核镜像)', '复核 E16：12 位作者里点最后一位，穿透仍是本人', async (make) => {
    const tweets = Array.from({ length: 12 }, (_, i) => mkTweet({ handle: 'Rank_User_' + i, name: '排名' + i, views: 200000 - i * 10000, ms: NOW - (1 + i * 0.3) * HOUR }));
    const p = await make({ bundle: baseBundle({ tweets, vip_tweets: [] }) });
    await feed(p);
    await p.locator('#tweets-feed [data-drill-handle="Rank_User_11"]').click(); await p.waitForTimeout(200);
    const h = await p.locator('#blogger-dossier-content .d-handle').innerText();
    ensure(h === '@Rank_User_11', '被重置到榜首: ' + h);
    await shot(p, 'E16-第9名以外穿透.png');
    return '穿透=' + h;
  });

  await scenario('E17-无证据标题(复核镜像)', '复核 E17：无支持帖标题不保留具体曝光战绩', async (make) => {
    const p = await make({ bundle: baseBundle({ topic_clusters: [{ id: 'title', track: 'ai_models', tag: 'AI', title: '999万曝光实测战绩', heat: '99万', burst_summary: '999万断言', core_conflict: 'x', article_plan: 'y', counter_check: 'z', embedded_tweets: [] }] }) });
    const s = await p.locator('#topics-list').innerText();
    ensure(!s.includes('999万曝光实测战绩'), '旧数字标题仍在: ' + s.replace(/\s+/g, ' ').slice(0, 120));
    ensure(s.includes('暂无证据'), '缺暂无证据提示');
    return s.replace(/\s+/g, ' ').slice(0, 110);
  });

  // ================= 真实 bundle 冒烟（内容断言，不用 >=0） =================
  const realBundle = process.env.RADAR_A2_REAL_BUNDLE;
  if (realBundle && fs.existsSync(realBundle)) {
    await scenario('R14-真实bundle冒烟', '真实快照四视图渲染出具体内容且无 pageerror', async (make) => {
      const b = JSON.parse(fs.readFileSync(realBundle, 'utf8'));
      const p = await make({ bundle: b });
      const counts = {};
      await clusters(p); counts.clusters = await p.locator('#topics-list .topic-card').count();
      await feed(p); counts.feed = await p.locator('#tweets-feed .tweet-card').count();
      await p.locator('#tab-vip-btn').click(); await p.waitForTimeout(200);
      counts.vip = await p.locator('#vip-tweets-feed .vip-tweet-card, #vip-tweets-feed > div').count();
      await p.locator('#tab-blogger-btn').click(); await p.waitForTimeout(250);
      const dossierText = await p.locator('#blogger-dossier-content').innerText();
      counts.blogger = dossierText.includes('粉丝') ? 1 : 0;
      ensure(counts.clusters > 0, '话题为空');
      ensure(counts.feed > 0, '低粉榜为空');
      ensure(counts.vip > 0, '特关视图为空');
      ensure(counts.blogger === 1, '博主档案无内容');
      await shot(p, 'R14-真实bundle冒烟.png');
      return JSON.stringify(counts) + ' ｜ ' + (await receipt(p)).replace(/\s+/g, ' ').slice(0, 120);
    });
  }

  // ================= M4：合法空包残留旧战绩（复核 N18 / N19） =================
  await scenario('M4a-空包初载', '合法空包首次加载：四视图无旧 chip/旧计数，热度博主区显示「本轮暂无爆款」', async (make) => {
    const p = await make({ bundle: baseBundle({ run_id: 'empty-four', tweets: [], vip_tweets: [], vip_bloggers: [], topic_clusters: [] }) });
    await clusters(p);
    ensure(await p.locator('#topics-list .topic-card').count() === 0, '话题区仍有旧卡');
    await feed(p);
    ensure(await p.locator('#tweets-feed .tweet-card').count() === 0, '低粉榜仍有旧卡');
    await p.locator('#tab-vip-btn').click(); await p.waitForTimeout(200);
    ensure(await p.locator('#vip-total-count').innerText() === '0', '特关今日计数不是 0: ' + await p.locator('#vip-total-count').innerText());
    await p.locator('#tab-blogger-btn').click(); await p.waitForTimeout(250);
    const chips = await p.locator('.blogger-chips-row').innerText();
    ensure(chips.includes('本轮暂无爆款'), '缺中性空态: ' + chips);
    ensure(!/\d+(?:[.,]\d+)?[kKwW万]?(?:阅|曝光)/.test(chips), '残留旧曝光战绩: ' + chips);
    ensure(!/ClearOld|Sac @|liftoff|Gorden/.test(chips), '残留旧博主: ' + chips);
    await shot(p, 'M4a-空包初载.png');
    return '空态文案=' + chips.replace(/\s+/g, ' ').slice(0, 60);
  });

  await scenario('M4b-刷新为空包', '非空包 → 点「读取最新数据」读空包：旧热门 chip 与战绩全部消失', async (make) => {
    const old = Array.from({ length: 12 }, (_, i) => mkTweet({ handle: 'ClearOld_' + i, name: '清旧' + i, views: 200000 - i * 1000, ms: NOW - (1 + i * 0.2) * HOUR }));
    const p = await make({ bundle: baseBundle({ run_id: 'nonempty-old', tweets: old, vip_tweets: [] }) });
    await p.locator('#tab-blogger-btn').click(); await p.waitForTimeout(250);
    const before = await p.locator('.blogger-chips-row').innerText();
    ensure(before.includes('ClearOld_0'), '夹具未生效: ' + before);
    await p.waitForFunction(() => !document.querySelector('#btn-refresh-radar').disabled);
    state.body = JSON.stringify(baseBundle({ run_id: 'empty-fresh', tweets: [], vip_tweets: [], vip_bloggers: [], topic_clusters: [] }));
    await p.locator('#btn-refresh-radar').click();
    await p.waitForFunction(() => DATA_BUNDLE.run_id === 'empty-fresh');
    await p.waitForTimeout(200);
    const after = await p.locator('.blogger-chips-row').innerText();
    ensure(!after.includes('ClearOld_0'), '空包后仍显示上一包 chip: ' + after);
    ensure(!/20\.0万阅|19\.9万阅/.test(after), '空包后仍显示上一包战绩: ' + after);
    ensure(after.includes('本轮暂无爆款'), '空包后缺中性空态: ' + after);
    const receiptText = await receipt(p);
    ensure(receiptText.includes('empty-fresh'), '回执未更新为新 run');
    await p.locator('#tab-vip-btn').click(); await p.waitForTimeout(200);
    ensure(await p.locator('#vip-total-count').innerText() === '0', '空包后特关计数不是 0');
    await shot(p, 'M4b-刷新为空包.png');
    return '刷新前含 ClearOld_0 → 刷新后：' + after.replace(/\s+/g, ' ').slice(0, 70);
  });

  await scenario('M4c-空包再读有数据包', '空包 → 有数据包：正常显示新数据，不被空态卡住', async (make) => {
    const p = await make({ bundle: baseBundle({ run_id: 'empty-first', tweets: [], vip_tweets: [], vip_bloggers: [], topic_clusters: [] }) });
    await p.locator('#tab-blogger-btn').click(); await p.waitForTimeout(200);
    ensure((await p.locator('.blogger-chips-row').innerText()).includes('本轮暂无爆款'), '首屏未显示空态');
    await p.waitForFunction(() => !document.querySelector('#btn-refresh-radar').disabled);
    const fresh = Array.from({ length: 4 }, (_, i) => mkTweet({ handle: 'Back_' + i, name: '回来' + i, views: 90000 - i * 1000, ms: NOW - (2 + i) * HOUR }));
    state.body = JSON.stringify(baseBundle({ run_id: 'back-nonempty', tweets: fresh, vip_tweets: [] }));
    await p.locator('#btn-refresh-radar').click();
    await p.waitForFunction(() => DATA_BUNDLE.run_id === 'back-nonempty');
    await p.waitForTimeout(250);
    const chips = await p.locator('.blogger-chips-row').innerText();
    ensure(chips.includes('Back_0'), '恢复数据后未显示新 chip: ' + chips);
    ensure(!chips.includes('本轮暂无爆款'), '恢复数据后仍显示空态: ' + chips);
    ensure(await p.locator('#blogger-dossier-content .d-handle').innerText() === '@Back_0', '恢复数据后档案未指向新榜首');
    await feed(p);
    ensure(await p.locator('#tweets-feed .tweet-card').count() === 4, '低粉榜未恢复: ' + await p.locator('#tweets-feed .tweet-card').count());
    await shot(p, 'M4c-空包再读有数据包.png');
    return '空态 → 恢复：chip=' + chips.replace(/\s+/g, ' ').slice(0, 70);
  });

  // ================= L1：无证据标题数字变体 =================
  await scenario('L1b-标题数字变体', '无证据话题标题含 13.9万/139,000/1.2w/2万+/百万/10K 等写法 → 只显示「暂无证据」', async (make) => {
    const variants = [
      ['233.7万+曝光', '233.7'], ['曝光233万，点赞2万', '233'], ['三百万曝光实测', '三百万'],
      ['50K views', '50K'], ['13.9万曝光', '13.9'], ['139,000 曝光', '139,000'],
      ['1.2w 阅读', '1.2w'], ['2万+ 点赞', '2万'], ['百万曝光实测', '百万'], ['10K 播放', '10K']
    ];
    for (const [title, needle] of variants) {
      const p = await make({ bundle: baseBundle({ topic_clusters: [{ id: 'v', track: 'ai_models', tag: 'AI', title, heat: '9万曝光', burst_summary: '旧摘要', core_conflict: 'x', article_plan: 'y', counter_check: 'z', embedded_tweets: [] }] }) });
      await clusters(p);
      const shown = await p.locator('#topics-list .topic-title').first().innerText();
      const card = await p.locator('#topics-list .topic-card').first().innerText();
      ensure(!shown.includes(needle), '变体漏网 [' + title + '] 标题显示为：' + shown);
      ensure(shown === '暂无证据', '变体 [' + title + '] 标题未统一为暂无证据：' + shown);
      ensure(card.includes('暂无证据'), '变体 [' + title + '] 缺暂无证据提示');
      ensure(!/旧摘要|9万曝光/.test(card), '变体 [' + title + '] 仍显示旧摘要/热度');
      ensure(await p.locator('#topics-list .topic-title-hint').count() === 0, '仍有已废弃的标题提示元素');
      await p.close();
    }
    return variants.length + ' 种数字写法全部只显示「暂无证据」';
  });

  await scenario('L1c-有证据标题保留', '有支持帖的话题 → 原标题与热度照常显示；无支持帖 → 一律「暂无证据」', async (make) => {
    // 本场景按 A2-d 合同改写：上一版这里断言「无证据的版本号/年份标题原样保留」，
    // 与「无支持帖一律显示暂无证据」冲突，已改为断言一律暂无证据。
    const p = await make({ bundle: baseBundle({ topic_clusters: [
      { id: 'with', track: 'ai_models', tag: 'AI', title: '有证据：13.9万曝光的实测话题', heat: '13.9万曝光', burst_summary: '真实摘要', core_conflict: 'x', article_plan: 'y', counter_check: 'z', embedded_tweets: [mkTweet({ handle: 'Ev_A', name: '有证据', ms: NOW - 3 * HOUR })] },
      { id: 'without', track: 'github_open', tag: '开源', title: 'Gemini 4.5 与 2026 模型观察', heat: '8万', burst_summary: '无证据摘要', core_conflict: 'x', article_plan: 'y', counter_check: 'z', embedded_tweets: [] }
    ] }) });
    await clusters(p);
    const cards = p.locator('#topics-list .topic-card');
    ensure(await cards.count() === 2, '话题卡数量不对');
    const withText = await cards.nth(0).innerText();
    ensure(withText.includes('有证据：13.9万曝光的实测话题'), '有证据标题被误隐藏: ' + withText.split('\n')[1]);
    ensure(withText.includes('真实摘要'), '有证据摘要被隐藏');
    const withoutTitle = await cards.nth(1).locator('.topic-title').innerText();
    ensure(withoutTitle === '暂无证据', '无证据版本号标题未按合同显示暂无证据: ' + withoutTitle);
    ensure(await p.locator('#topics-list .topic-no-evidence-card').count() === 1, '无证据卡数量不对');
    ensure(await p.locator('#topics-list .topic-title-hint').count() === 0, '出现已废弃的误报提示');
    await shot(p, 'L1c-有证据标题保留.png');
    return '有证据标题保留原样；无证据版本号/年份标题改为「暂无证据」';
  });

  // ================= A2-d：一律暂无证据（合同字面实现） =================
  await scenario('D1-无证据一律暂无证据', '无支持帖的标题：纯文字/十万曝光/破百万/13.9万/空字符串 → 全部「暂无证据」', async (make) => {
    const titles = ['Claude Code 新玩法', '十万曝光', '破百万', '13.9万', '', '   ', '普通主题没有任何数字', 'Gemini 4.5 与 2026 模型观察'];
    for (const title of titles) {
      const p = await make({ bundle: baseBundle({ topic_clusters: [{ id: 'd1', track: 'ai_models', tag: 'AI', title, heat: 'HIDDEN_HEAT', burst_summary: 'HIDDEN_SUMMARY', core_conflict: 'x', article_plan: 'y', counter_check: 'z', embedded_tweets: [] }] }) });
      await clusters(p);
      const shown = await p.locator('#topics-list .topic-title').first().innerText();
      ensure(shown === '暂无证据', '标题 [' + JSON.stringify(title) + '] 显示为 ' + JSON.stringify(shown));
      const whole = await p.locator('#topics-list').innerText();
      ensure(!whole.includes('HIDDEN_'), '旧热度/摘要仍显示');
      if (title.trim()) ensure(!whole.includes(title.trim()), '原标题片段仍残留: ' + title);
      await p.close();
    }
    return titles.length + ' 种无证据标题全部显示「暂无证据」';
  });

  await scenario('D2-支持帖缺失或全被过滤', 'embedded_tweets 缺失 / 非数组 / 全部超48h / 全部未来或时间未知 → 一律无证据卡且标题「暂无证据」', async (make) => {
    const cases = [
      ['missing', {}],
      ['not-array', { embedded_tweets: 'oops' }],
      ['all-expired', { embedded_tweets: [mkTweet({ handle: 'Old_1', name: '过期', ms: NOW - 49 * HOUR })] }],
      ['all-future', { embedded_tweets: [mkTweet({ handle: 'Fut_1', name: '未来', ms: NOW + 30 * 60000 })] }],
      ['all-unknown', { embedded_tweets: [mkTweet({ handle: 'Unk_1', name: '未知', tweetId: 'bad-id', dropPublishedAt: true, ms: NOW })] }]
    ];
    for (const [id, extra] of cases) {
      const cluster = Object.assign({ id, track: 'ai_models', tag: 'AI', title: '破百万', heat: 'HIDDEN_HEAT', burst_summary: 'HIDDEN_SUMMARY', core_conflict: 'x', article_plan: 'y', counter_check: 'z' }, extra);
      const p = await make({ bundle: baseBundle({ topic_clusters: [cluster] }) });
      await clusters(p);
      ensure(await p.locator('#topics-list .topic-no-evidence-card').count() === 1, id + ' 未被判为无证据卡');
      const shown = await p.locator('#topics-list .topic-title').first().innerText();
      ensure(shown === '暂无证据', id + ' 标题为 ' + JSON.stringify(shown));
      ensure(!(await p.locator('#topics-list').innerText()).includes('破百万'), id + ' 原标题残留');
      await p.close();
    }
    return cases.length + ' 种「没有有效支持帖」情况全部显示「暂无证据」';
  });

  await scenario('D3-有证据标题不受影响', '有支持帖的 5 种标题（含数字与版本号）全部原样显示，且无卡片被降级', async (make) => {
    const titles = ['十万曝光', '破百万', '139,000', '普通主题没有任何数字', 'Gemini 4.5 与 2026 模型观察'];
    const clusters_ = titles.map((title, i) => ({ id: 'ok' + i, track: 'ai_models', tag: 'AI', title, heat: '有证据热度', burst_summary: '支持摘要', core_conflict: 'x', article_plan: 'y', counter_check: 'z', embedded_tweets: [mkTweet({ handle: 'Ev_' + i, name: '证据' + i, ms: NOW - (2 + i) * HOUR })] }));
    const p = await make({ bundle: baseBundle({ topic_clusters: clusters_ }) });
    await clusters(p);
    const shown = await p.locator('#topics-list .topic-title').allInnerTexts();
    ensure(JSON.stringify(shown) === JSON.stringify(titles), '有证据标题被改写: ' + JSON.stringify(shown));
    ensure(await p.locator('#topics-list .topic-no-evidence-card').count() === 0, '有证据卡片被误判为无证据');
    await shot(p, 'D3-有证据标题不受影响.png');
    return '5 种有证据标题原样保留';
  });

  // ================= 汇总 =================
  ensure(external.length === 0, '存在外网请求: ' + external.slice(0, 3).join(','));
  ensure(pageErrors.length === 0, '存在 JS 异常: ' + JSON.stringify(pageErrors.slice(0, 3)));
  ensure(consoleErrors.length === 0, '存在 console.error: ' + JSON.stringify(consoleErrors.slice(0, 3)));
  const failed = results.filter((r) => !r.ok);
  console.log('\n===== A2-b 汇总 =====');
  console.log('通过 ' + (results.length - failed.length) + '/' + results.length +
    '｜pageerror 累计 ' + pageErrors.length + '｜console.error ' + consoleErrors.length +
    '｜网络失败上报(预期内) ' + resourceErrors.length +
    '｜外网请求 ' + external.length + '｜冻结时钟 ' + iso(NOW) + '｜端口 ' + server.address().port);
  fs.writeFileSync(path.join(EVIDENCE, 'results.json'), JSON.stringify({
    origin, now: iso(NOW), results, pageErrors, consoleErrors, resourceErrors, external, requests: [...new Set(requests)]
  }, null, 2));
  if (failed.length) { console.log('失败项：' + failed.map((f) => f.id).join(', ')); process.exitCode = 1; }
})().catch((e) => { console.error('测试执行异常：', e); process.exitCode = 1; }).finally(async () => {
  if (browser) { try { await browser.close(); } catch (e) {} }
  if (server) { try { await new Promise((r) => server.close(r)); } catch (e) {} }
});