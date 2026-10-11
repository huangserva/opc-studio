// 回收站三家平台链接：列表平台徽标与侧栏平台筛选（写法照 display_kind 的类型筛选）。
const test = require('node:test'), assert = require('node:assert/strict'), fs = require('node:fs');
const { JSDOM } = require('jsdom');
const tick = () => new Promise(resolve => setImmediate(resolve));
const settle = async () => { for (let i = 0; i < 8; i++) await tick(); };
const id = n => n.repeat(32);
const ok = data => ({ ok: true, json: async () => data });

const base = extra => ({ id: id('a'), kind: 'link', received_at: '2026-10-11T01:20:00+00:00',
  url: 'https://example.com/post', title: '', reason: '', status: 'resolved', error: '', document_id: id('b'),
  revision: 2, file_path: '', file: {}, media_kind: '', text_chars: 0, content_scope: '', meta: {},
  outcome: 'stored', state: '已存入知识库，已抓取正文，共 12 字', document_shares: 1, document_owned: true,
  source_platform: '', source_label: '', knowledge_url: '/knowledge.html?document=' + id('b'), ...extra });

const xhs = base({ url: 'https://www.xiaohongshu.com/explore/64f0a1b20000000012034567?xsec_token=AAA111',
  source_platform: 'xiaohongshu', source_label: '小红书',
  meta: { fetched: true, total_chars: 12, source_platform: 'xiaohongshu', source_label: '小红书', source_item_id: '64f0a1b20000000012034567' } });
const douyin = base({ id: id('d'), url: 'https://www.douyin.com/video/7412345678901234567',
  source_platform: 'douyin', source_label: '抖音',
  meta: { fetched: true, total_chars: 12, source_platform: 'douyin', source_label: '抖音', source_item_id: '7412345678901234567' } });
const sph = base({ id: id('e'), url: 'https://weixin.qq.com/sph/Aa1Bb2Cc3Dd',
  source_platform: 'channels', source_label: '视频号',
  meta: { fetched: true, total_chars: 12, source_platform: 'channels', source_label: '视频号', source_item_id: 'Aa1Bb2Cc3Dd' } });
const blog = base({ id: id('f'), url: 'https://blog.example.com/posts/one' });
const short = base({ id: id('g'), url: 'http://xhslink.com/a/AbCdEf123', source_platform: 'xiaohongshu', source_label: '小红书',
  status: 'failed', outcome: 'failed', error: '小红书正文需要登录浏览器，本机不代跑；联网服务未就绪（remio 未运行）',
  content_scope: '仅收藏链接；未读到作品正文（小红书正文需要登录浏览器，本机不代跑；联网服务未就绪（remio 未运行））',
  state: '抓取失败：小红书正文需要登录浏览器，本机不代跑；联网服务未就绪（remio 未运行）；已存入知识库（仅链接，未读到作品正文）',
  meta: { fetched: false, source_platform: 'xiaohongshu', source_label: '小红书', source_item_id: '', source_short_link: true } });

async function fixture(t, items = []) {
  const dom = new JSDOM(fs.readFileSync('public/intake.html', 'utf8'),
    { url: 'http://127.0.0.1:8766/intake.html', runScripts: 'outside-only', pretendToBeVisual: true });
  t.after(() => dom.window.close());
  const w = dom.window, d = w.document, calls = [];
  w.fetch = async (url) => {
    const target = String(url);
    calls.push(target);
    if (target === '/api/intake') return ok({ schema: 'intake/v1', items, note: '' });
    if (target === '/api/intake/analysis-status') return ok({ schema: 'intake-analysis-status/v1', supported: true, model_configured: true, working: 0 });
    return ok({});
  };
  w.eval(fs.readFileSync('public/intake.js', 'utf8'));
  await settle();
  return { w, d, calls, list: () => d.querySelector('#in-list').textContent, rows: () => d.querySelectorAll('#in-list .in-item') };
}

test('the sidebar offers a platform group next to the kind filters', async t => {
  const f = await fixture(t, []);
  const nav = f.d.querySelector('#in-platforms');
  assert.ok(nav, '侧栏要有平台筛选分组');
  assert.deepEqual([...nav.querySelectorAll('[data-platform]')].map(b => b.dataset.platform),
    ['', 'xiaohongshu', 'douyin', 'channels']);
  assert.deepEqual([...nav.querySelectorAll('[data-platform]')].map(b => b.textContent),
    ['全部平台', '小红书', '抖音', '视频号']);
  assert.equal(nav.querySelector('[data-platform=""]').classList.contains('selected'), true);
  assert.match(f.d.querySelector('.in-drop').textContent, /不代跑登录浏览器/);
});

test('recognized links carry a platform badge and others carry none', async t => {
  const f = await fixture(t, [xhs, douyin, sph, blog]);
  const badges = [...f.d.querySelectorAll('#in-list .in-platform')];
  assert.deepEqual(badges.map(b => b.textContent), ['小红书', '抖音', '视频号']);
  assert.deepEqual(badges.map(b => b.className),
    ['in-platform in-platform-xiaohongshu', 'in-platform in-platform-douyin', 'in-platform in-platform-channels']);
  assert.equal(f.rows().length, 4);
  const blogRow = [...f.d.querySelectorAll('#in-list .in-item')].find(r => r.textContent.includes('blog.example.com'));
  assert.equal(blogRow.querySelector('.in-platform'), null);
});

test('a short link badge says the work id is still unresolved', async t => {
  const f = await fixture(t, [short]);
  assert.match(f.d.querySelector('#in-list .in-platform').textContent, /小红书 · 短链未解析/);
  assert.match(f.list(), /短链未解析/);
  assert.match(f.list(), /未读到作品正文/);
  assert.match(f.list(), /小红书正文需要登录浏览器，本机不代跑/);
  assert.doesNotMatch(f.list(), /已抓取正文/);
});

test('the platform filter shows only that platform and marks itself selected', async t => {
  const f = await fixture(t, [xhs, douyin, sph, blog, short]);
  assert.equal(f.rows().length, 5);
  f.d.querySelector('#in-platforms [data-platform="xiaohongshu"]').click();
  assert.equal(f.rows().length, 2);
  assert.match(f.list(), /64f0a1b20000000012034567/);
  assert.match(f.list(), /xhslink\.com/);
  assert.doesNotMatch(f.list(), /douyin|weixin\.qq\.com|blog\.example\.com/);
  assert.equal(f.d.querySelector('#in-platforms [data-platform="xiaohongshu"]').classList.contains('selected'), true);
  assert.equal(f.d.querySelector('#in-platforms [data-platform=""]').classList.contains('selected'), false);
  // 类型筛选照旧可用，两个维度叠加。
  f.d.querySelector('#in-views [data-kind="link"]').click();
  assert.equal(f.rows().length, 2);
  f.d.querySelector('#in-platforms [data-platform=""]').click();
  assert.equal(f.rows().length, 5);
  f.d.querySelector('#in-platforms [data-platform="channels"]').click();
  assert.equal(f.rows().length, 1);
  assert.match(f.list(), /Aa1Bb2Cc3Dd/);
});

test('an older backend that only puts the platform in meta still gets a badge', async t => {
  const legacy = base({ source_platform: undefined, source_label: undefined,
    meta: { fetched: true, source_platform: 'douyin', source_item_id: '7412345678901234567' } });
  const f = await fixture(t, [legacy]);
  assert.equal(f.d.querySelector('#in-list .in-platform').textContent, '抖音');
  f.d.querySelector('#in-platforms [data-platform="douyin"]').click();
  assert.equal(f.rows().length, 1);
});

test('a platform label can never be injected as markup', async t => {
  const hostile = base({ source_label: '<img src=x onerror=alert(1)>', meta: { source_platform: 'xiaohongshu' } });
  const f = await fixture(t, [hostile]);
  assert.equal(f.d.querySelectorAll('#in-list img').length, 0);
  assert.equal(f.d.querySelectorAll('#in-list script').length, 0);
  assert.match(f.list(), /<img src=x onerror=alert\(1\)>/);
});
