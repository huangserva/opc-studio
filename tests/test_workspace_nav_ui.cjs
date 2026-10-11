/* nav-unify：12 页左侧主导航条目、顺序、当前项高亮一致。
   静态 HTML（不跑脚本）和 workspace-nav.js 渲染后的 DOM 都要满足同一份清单。 */
const test = require('node:test'), assert = require('node:assert/strict'), fs = require('node:fs');
const { JSDOM } = require('jsdom');

const NAV_SCRIPT = fs.readFileSync('public/workspace-nav.js', 'utf8');
const PAGES = ['index.html', 'knowledge.html', 'intake.html', 'cards.html', 'events.html', 'feedback.html',
  'schedules.html', 'clues.html', 'deals.html', 'intel.html', 'github.html', 'lab.html'];

// 统一顺序（ui-audit-1 §3 建议）：我的作品 → 图片生成 → 视频生成 → 知识库 → 创意回收 → 提示词卡
// → 线索中心 → 事项与日程 → 发布反馈 → 商单管理 → 情报采集 → GitHub 来源 → 定时任务
const EXPECT = [
  { id: 'works', href: '/', text: '▤ 我的作品' },
  { id: 'image', href: '/lab.html?kind=image', text: '▧ 图片生成' },
  { id: 'video', href: '/lab.html?kind=video', text: '▷ 视频生成' },
  { id: 'knowledge', href: '/knowledge.html', text: '▥ 知识库' },
  { id: 'intake', href: '/intake.html', text: '◈ 创意回收' },
  { id: 'cards', href: '/cards.html', text: '✦ 提示词卡' },
  { id: 'clues', href: '/clues.html', text: '⌁ 线索中心' },
  { id: 'events', href: '/events.html', text: '◷ 事项与日程' },
  { id: 'feedback', href: '/feedback.html', text: '↗ 发布反馈' },
  { id: 'deals', href: '/deals.html', text: '▣ 商单管理' },
  { id: 'intel', href: '/intel.html', text: '☰ 情报采集' },
  { id: 'github', href: '/github.html', text: '⑂ GitHub 来源' },
  { id: 'schedules', href: '/schedules.html', text: '◴ 定时任务' },
];
const CURRENT = {
  'index.html': 'works', 'knowledge.html': 'knowledge', 'intake.html': 'intake', 'cards.html': 'cards',
  'events.html': 'events', 'feedback.html': 'feedback', 'schedules.html': 'schedules', 'clues.html': 'clues',
  'deals.html': 'deals', 'intel.html': 'intel', 'github.html': 'github', 'lab.html': 'image',
};
// 页面专属的二级导航/视图切换必须原样保留。
const SECONDARY = {
  'index.html': ['#collections-nav'], 'knowledge.html': ['#kb-source-nav'], 'intake.html': ['#in-views', '#in-platforms'],
  'cards.html': ['#cd-views'], 'events.html': ['#ev-view-nav'], 'feedback.html': [], 'schedules.html': [],
  'clues.html': ['#cl-views'], 'deals.html': ['#dl-focus'], 'intel.html': ['#itl-accounts'], 'github.html': [],
  'lab.html': ['.lab-kind-tabs'],
};
const OLD_CLASSES = ['cd-main-nav', 'kb-main-nav', 'in-main-nav', 'ev-main-nav', 'fb-nav', 'cl-main-nav',
  'itl-main-nav', 'studio-generation-nav', 'schedule-nav'];

// fixes-nav-icons：13 个条目图标必须两两不同，且静态兜底清单与 ITEMS 的图标一致。
function iconOf(text) { return [...text.trim()][0]; }
const ICONS = EXPECT.map(i => iconOf(i.text));
// workspace-nav.js 里 ITEMS 的 icon 字段（脚本是唯一数据源）。
function itemsFromScript() {
  return [...NAV_SCRIPT.matchAll(/\{\s*id: '([a-z]+)',\s*href: '([^']+)',\s*icon: '(.*?)',\s*label: '(.*?)'\s*\}/g)]
    .map(m => ({ id: m[1], href: m[2], icon: m[3], label: m[4] }));
}

function checkNav(nav, expectedSelected, note) {
  assert.ok(nav, note + '：缺少 #workspace-nav');
  assert.ok(nav.classList.contains('workspace-nav'), note + '：容器缺 workspace-nav class');
  assert.equal(nav.getAttribute('aria-label'), '工作台栏目', note + '：aria-label 未统一');
  const links = [...nav.querySelectorAll('a')];
  assert.deepEqual(links.map(a => a.getAttribute('href')), EXPECT.map(i => i.href), note + '：条目或顺序不一致');
  assert.deepEqual(links.map(a => a.textContent.trim()), EXPECT.map(i => i.text), note + '：条目文字不一致');
  const selected = [...nav.querySelectorAll('a.selected')];
  assert.equal(selected.length, 1, note + '：高亮项不是唯一');
  assert.equal(selected[0].getAttribute('href'), EXPECT.find(i => i.id === expectedSelected).href, note + '：高亮项错误');
  assert.equal(selected[0].getAttribute('aria-current'), 'page', note + '：当前项缺 aria-current="page"');
  assert.equal(nav.querySelectorAll('a[aria-current]').length, 1, note + '：aria-current 不唯一');
}

function staticDom(page) {
  return new JSDOM(fs.readFileSync('public/' + page, 'utf8'), { url: 'http://127.0.0.1:8766/' + page });
}

test('the 13 nav icons are pairwise different and stay single plain glyphs', () => {
  // 以脚本里的 ITEMS（唯一数据源）为准，同时保证测试清单不重复。
  const groups = [['workspace-nav.js ITEMS', itemsFromScript().map(i => i.icon + ' ' + i.label)], ['测试清单 EXPECT', EXPECT.map(i => i.text)]];
  for (const [source, texts] of groups) {
    const seen = new Map();
    assert.equal(texts.length, EXPECT.length, `${source}：条目数不是 ${EXPECT.length}`);
    for (const [i, text] of texts.entries()) {
      const icon = iconOf(text);
      if (seen.has(icon)) assert.fail(`${source} 图标重复：${seen.get(icon)} 与 ${text} 都是 ${icon}`);
      seen.set(icon, text);
      const cps = [...icon];
      assert.equal(cps.length, 1, `${source}：${text} 的图标不是单字符`);
      const cp = cps[0].codePointAt(0);
      assert.ok(cp <= 0xffff, `${source}：${text} 的图标不在 BMP，容易变豆腐块`);
      assert.ok(cp < 0x1f000 || cp > 0x1faff, `${source}：${text} 的图标落在 emoji 区，会变彩色字形`);
    }
  }
});

test('workspace-nav.js ITEMS carries exactly those icons', () => {
  const items = itemsFromScript();
  assert.equal(items.length, EXPECT.length, 'workspace-nav.js 的 ITEMS 条目数与清单不一致');
  assert.deepEqual(items.map(i => i.id), EXPECT.map(i => i.id), 'ITEMS 条目或顺序不一致');
  assert.deepEqual(items.map(i => i.icon), ICONS, 'ITEMS 的 icon 与清单不一致：' + items.map(i => i.icon).join(' '));
  assert.deepEqual(items.map(i => i.icon + ' ' + i.label), EXPECT.map(i => i.text), 'ITEMS 渲染出的条目文字与清单不一致');
});

test('every page static fallback repeats the ITEMS icons, not a stale copy', () => {
  for (const page of PAGES) {
    const dom = staticDom(page);
    const links = [...dom.window.document.querySelectorAll('#workspace-nav a')];
    assert.deepEqual(links.map(a => iconOf(a.textContent)), ICONS, page + '：静态兜底清单的图标与 ITEMS 不一致');
    dom.window.close();
  }
});

test('every page keeps one identical static main nav as the no-JS fallback', () => {
  for (const page of PAGES) {
    const dom = staticDom(page);
    const d = dom.window.document;
    checkNav(d.querySelector('#workspace-nav'), CURRENT[page], page + ' 静态兜底');
    assert.equal(d.querySelectorAll('#workspace-nav').length, 1, page + '：主导航容器不唯一');
    for (const cls of OLD_CLASSES) assert.ok(!d.querySelector('.' + cls), page + '：仍残留旧主导航 ' + cls);
    for (const selector of SECONDARY[page]) assert.ok(d.querySelector(selector), page + '：二级导航 ' + selector + ' 被删掉了');
    dom.window.close();
  }
});

test('every page loads the shared nav stylesheet and script once', () => {
  for (const page of PAGES) {
    const html = fs.readFileSync('public/' + page, 'utf8');
    assert.equal((html.match(/workspace-nav\.css/g) || []).length, 1, page + '：workspace-nav.css 引用不是一份');
    assert.equal((html.match(/workspace-nav\.js/g) || []).length, 1, page + '：workspace-nav.js 引用不是一份');
    assert.ok(html.includes('<link rel="stylesheet" href="/workspace-nav.css">'), page + '：缺 workspace-nav.css');
    assert.ok(html.includes('<script src="/workspace-nav.js" defer></script>'), page + '：缺 workspace-nav.js');
  }
});

test('the shared script renders the same nav and one correct highlight on all 12 pages', () => {
  for (const page of PAGES) {
    const dom = new JSDOM(fs.readFileSync('public/' + page, 'utf8'), { url: 'http://127.0.0.1:8766/' + page, runScripts: 'outside-only' });
    dom.window.eval(NAV_SCRIPT);
    checkNav(dom.window.document.querySelector('#workspace-nav'), CURRENT[page], page + ' 渲染后');
    for (const selector of SECONDARY[page]) assert.ok(dom.window.document.querySelector(selector), page + '：脚本改动了二级导航 ' + selector);
    dom.window.close();
  }
});

test('lab highlights 图片生成 or 视频生成 according to the kind query', () => {
  for (const [query, expected] of [['', 'image'], ['?kind=image', 'image'], ['?kind=video', 'video'], ['?kind=', 'image']]) {
    const dom = new JSDOM(fs.readFileSync('public/lab.html', 'utf8'), { url: 'http://127.0.0.1:8766/lab.html' + query, runScripts: 'outside-only' });
    dom.window.eval(NAV_SCRIPT);
    checkNav(dom.window.document.querySelector('#workspace-nav'), expected, 'lab' + query);
    dom.window.close();
  }
});

test('works entry is / and stays current on the collection, index and reader urls', () => {
  assert.equal(EXPECT[0].href, '/', '我的作品应统一为 /');
  for (const url of ['http://127.0.0.1:8766/', 'http://127.0.0.1:8766/?collection=all', 'http://127.0.0.1:8766/index.html', 'http://127.0.0.1:8766/read/some-slug']) {
    const dom = new JSDOM(fs.readFileSync('public/index.html', 'utf8'), { url, runScripts: 'outside-only' });
    dom.window.eval(NAV_SCRIPT);
    checkNav(dom.window.document.querySelector('#workspace-nav'), 'works', url);
    dom.window.close();
  }
});

test('the script is the single source of truth: a stale or empty nav is rebuilt', () => {
  const html = fs.readFileSync('public/knowledge.html', 'utf8').replace(
    /(<nav id="workspace-nav"[^>]*>).*?(<\/nav>)/s, '$1<a href="/deals.html">▣ 商单管理</a>$2');
  const dom = new JSDOM(html, { url: 'http://127.0.0.1:8766/knowledge.html', runScripts: 'outside-only' });
  assert.equal(dom.window.document.querySelectorAll('#workspace-nav a').length, 1, '兜底应已被清空');
  dom.window.eval(NAV_SCRIPT);
  checkNav(dom.window.document.querySelector('#workspace-nav'), 'knowledge', 'knowledge 重建');
  dom.window.close();
});

test('a page without the nav container or with a broken location never breaks on the script', () => {
  const bare = new JSDOM('<body><aside class="sidebar"></aside></body>', { url: 'http://127.0.0.1:8766/knowledge.html', runScripts: 'outside-only' });
  bare.window.eval(NAV_SCRIPT);
  assert.equal(bare.window.document.querySelectorAll('a').length, 0);
  bare.window.close();
  // 未知页面（例如后端新加的页面）保留静态清单，不清空、不抛错。
  const dom = new JSDOM(fs.readFileSync('public/intel.html', 'utf8'), { url: 'http://127.0.0.1:8766/unknown.html', runScripts: 'outside-only' });
  dom.window.eval(NAV_SCRIPT);
  const nav = dom.window.document.querySelector('#workspace-nav');
  assert.equal(nav.querySelectorAll('a').length, EXPECT.length);
  assert.equal(nav.querySelectorAll('a.selected').length, 0);
  dom.window.close();
});

test('the shared nav rules exist and no page css keeps a dead main-nav rule', () => {
  const shared = fs.readFileSync('public/workspace-nav.css', 'utf8');
  for (const rule of ['.workspace-nav', '.workspace-nav a', '.workspace-nav a.selected', '.workspace-nav a[aria-current="page"]']) {
    assert.ok(shared.includes(rule), 'workspace-nav.css 缺少 ' + rule);
  }
  for (const cls of OLD_CLASSES) {
    for (const css of fs.readdirSync('public').filter(f => f.endsWith('.css'))) {
      assert.ok(!fs.readFileSync('public/' + css, 'utf8').includes('.' + cls), css + ' 仍保留 .' + cls + ' 规则');
    }
  }
});
