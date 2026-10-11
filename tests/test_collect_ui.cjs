const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {JSDOM} = require('jsdom');
const tick = () => new Promise(resolve => setImmediate(resolve));

const listing = {
  schema: 'collect-core/v1',
  sections: [
    {name: '已接通', platforms: [
      {id: 'wechat_mp', label: '公众号', state: 'connected', note: '已迁入', home: '/intel.html'},
      {id: 'wechat_msg', label: '微信消息', state: 'connected', note: '只看状态', home: '/clues.html'},
      {id: 'mail', label: '邮件', state: 'connected', note: '只看状态', home: '/knowledge.html'},
      {id: 'github', label: 'GitHub', state: 'connected', note: '只看状态', home: '/github.html'},
    ]},
    {name: '规划中', platforms: [
      {id: 'channels', label: '视频号', state: 'planned', note: '规划中，尚未接通。', home: ''},
      {id: 'xiaohongshu', label: '小红书', state: 'planned', note: '规划中，尚未接通。', home: ''},
      {id: 'douyin', label: '抖音', state: 'planned', note: '规划中，尚未接通。', home: ''},
      {id: 'twitter', label: '推特', state: 'planned', note: '规划中，尚未接通。', home: ''},
      {id: 'other', label: '其他', state: 'planned', note: '规划中，尚未接通。', home: ''},
    ]},
  ],
};
const status = {
  platforms: [
    {id: 'wechat_mp', label: '公众号', state: 'connected', available: true, detail: '后台凭证可用', count: 1},
    {id: 'wechat_msg', label: '微信消息', state: 'connected', available: true, detail: '微信线索采集空闲，已检查 4 个版本', count: 4,
      extra: {home: '/clues.html', clues: [{id: 'wechat', total: 2}], tasks: [{enabled: true, running: false}]}},
    {id: 'mail', label: '邮件', state: 'connected', available: true, detail: '2 个邮箱来源，最近同步 2026-10-04', count: 3,
      extra: {home: '/knowledge.html', sources: [{id: 'mail', name: '邮箱 · remio'}, {id: 'gmail-demo', name: 'Gmail · demo'}]}},
    {id: 'github', label: 'GitHub', state: 'connected', available: true, detail: '账号 demo · GitHub 来源已接通', count: 1,
      extra: {home: '/github.html', sources: [{id: 'gh-demo', name: 'GitHub · demo/repo'}], tasks: [{enabled: false, running: false}]}},
    {id: 'channels', label: '视频号', state: 'planned', available: false, detail: '规划中，尚未接通。'},
    {id: 'xiaohongshu', label: '小红书', state: 'planned', available: false, detail: '规划中，尚未接通。'},
    {id: 'douyin', label: '抖音', state: 'planned', available: false, detail: '规划中，尚未接通。'},
    {id: 'twitter', label: '推特', state: 'planned', available: false, detail: '规划中，尚未接通。'},
    {id: 'other', label: '其他', state: 'planned', available: false, detail: '规划中，尚未接通。'},
  ],
};

async function fixture() {
  const dom = new JSDOM(fs.readFileSync('public/intel.html', 'utf8'), {
    url: 'http://127.0.0.1:8766/intel.html', runScripts: 'outside-only'});
  const window = dom.window;
  const calls = [];
  window.open = () => ({document: {write() {}, close() {}}});
  window.setInterval = () => 1;
  window.clearInterval = () => {};
  window.fetch = async (url, opts = {}) => {
    calls.push({url, method: opts.method || 'GET'});
    let data = {};
    if (url === '/api/collect/platforms') data = listing;
    else if (url === '/api/collect/status') data = status;
    else if (url === '/api/intel/subscriptions') data = {subscriptions: []};
    else if (url.startsWith('/api/intel/articles')) data = {articles: [], total: 0, albums: []};
    else if (url === '/api/intel/status') data = {subscriptions: 0, articles: 0, awaiting: 0, credentials: 'ready', detail: '后台凭证可用', rate_strikes: 0, busy: false};
    else if (url === '/api/intel/recommendations') data = {items: [], pending_links: 0, failed_links: 0, ignored: 0, subscribed: 0};
    else if (url === '/api/schedules') data = {tasks: []};
    return {ok: true, json: async () => data};
  };
  window.eval(fs.readFileSync('public/intel.js', 'utf8'));
  await tick();
  await tick();
  return {window, document: window.document, calls};
}

function card(document, label) {
  return [...document.querySelectorAll('#itl-platforms [data-platform]')].find(node => node.querySelector('b').textContent === label);
}

test('nav name stays 情报采集 and the board splits connected from planned', async () => {
  const page = await fixture();
  assert.match(page.document.querySelector('h1').textContent, /情报采集/);
  assert.match(page.document.querySelector('.workspace-nav .selected').textContent, /情报采集/);
  const headings = [...page.document.querySelectorAll('.itl-platform-group h2')].map(node => node.textContent);
  assert.deepEqual(headings, ['已接通', '规划中']);
  for (const label of ['公众号', '微信消息', '邮件', 'GitHub', '视频号', '小红书', '抖音', '推特', '其他']) {
    assert.ok(card(page.document, label), label);
  }
  page.window.close();
});

test('loading the page does not start a fetch on any platform', async () => {
  const page = await fixture();
  const banned = ['/api/intel/run', '/api/github/run', '/api/clues/wechat/run', '/api/github/connect'];
  assert.equal(page.calls.some(call => banned.includes(call.url)), false);
  assert.ok(page.calls.some(call => call.url === '/api/collect/platforms'));
  assert.ok(page.calls.some(call => call.url === '/api/collect/status'));
  page.window.close();
});

test('a planned platform only explains itself and never offers a scrape button', async () => {
  const page = await fixture();
  card(page.document, '小红书').click();
  await tick();
  const panel = page.document.querySelector('#itl-delegated');
  assert.equal(panel.hidden, false);
  assert.match(panel.textContent, /尚未接通/);
  assert.match(panel.textContent, /不会发起抓取/);
  assert.equal(panel.querySelector('button'), null);
  assert.equal(panel.querySelector('a'), null);
  assert.equal(page.document.querySelector('#itl-mp').hidden, true);
  assert.equal(page.calls.some(call => call.method === 'POST'), false);
  page.window.close();
});

test('github shows the same status and a link to the existing page', async () => {
  const page = await fixture();
  card(page.document, 'GitHub').click();
  await tick();
  const panel = page.document.querySelector('#itl-delegated');
  assert.match(panel.textContent, /账号 demo · GitHub 来源已接通/);
  assert.match(panel.textContent, /GitHub · demo\/repo/);
  assert.match(panel.textContent, /不会代跑/);
  assert.match(panel.textContent, /定时未启用/);
  const link = panel.querySelector('a');
  assert.equal(link.getAttribute('href'), '/github.html');
  assert.equal(page.calls.some(call => call.url === '/api/github/run'), false);
  page.window.close();
});

test('selecting 公众号 returns to the existing capture panel', async () => {
  const page = await fixture();
  card(page.document, '邮件').click();
  await tick();
  assert.match(page.document.querySelector('#itl-delegated').textContent, /邮箱 · remio/);
  card(page.document, '公众号').click();
  await tick();
  assert.equal(page.document.querySelector('#itl-mp').hidden, false);
  assert.equal(page.document.querySelector('#itl-delegated').hidden, true);
  assert.ok(page.document.querySelector('#itl-query'));
  assert.ok(page.document.querySelector('#itl-run-latest'));
  page.window.close();
});
