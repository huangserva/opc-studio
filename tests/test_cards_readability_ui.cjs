// fixes-cards-readability：提示词卡页可读性（H1 标题、M1 摘要收起、M2 分区）+ 通用按钮风格（L1）
const test = require('node:test'), assert = require('node:assert/strict'), fs = require('node:fs');
const { JSDOM } = require('jsdom');
const tick = () => new Promise(resolve => setImmediate(resolve));
const settle = async () => { for (let i = 0; i < 10; i++) await tick(); };
const id = n => n.repeat(32);
const ok = data => ({ ok: true, json: async () => data });
const bad = (error, code = 400) => ({ ok: false, status: code, json: async () => ({ error }) });

const FILE = 'round7-20261010-20261010T052433997515Z-v2-1-M2.png';
const ANALYSIS = '本资料收录 Python json 模块官方参考页，涵盖 dump、dumps、loads 等全部函数签名与参数详解。' +
  '值得收：这是内置数据格式处理能力的权威参考，日常写作涉及格式转换时要以官方文档为准。' +
  '可写角度：绿三角与黄方块的极简海报、把官方文档写成避坑清单。相关 2 条。';

// jsdom 没有排版引擎，拿不到真高度。给 cards.js 一个可注入的「量尺寸」探针：
// 按每行能放多少字折算行数，超过 clamp 行就是被 CSS 裁掉了。
function fakeMeasure(options = {}) {
  const charsPerLine = options.charsPerLine || 20, lineHeight = options.lineHeight || 20, clamp = options.clamp || 3;
  return box => {
    const lines = Math.max(1, Math.ceil(String(box.textContent || '').length / charsPerLine));
    return { scrollHeight: lines * lineHeight, clientHeight: Math.min(lines, clamp) * lineHeight };
  };
}
// 375 宽下实测会折成 5 行、但字数不足 90 的摘要（旧代码按 >90 字判定，不给展开入口）。
const SHORT_ANALYSIS = '这份资料把 json 模块的常用参数列了一遍，值得收：写格式转换时可以直接对照。可写角度：把官方文档写成避坑清单。';

const card = (extra = {}) => ({
  id: id('c'), created_at: '2026-10-10T05:41:31+00:00', updated_at: '2026-10-10T05:41:31+00:00',
  drop_id: id('a'), document_id: id('b'),
  prompt: '极简扁平化海报，纯白背景，左侧实心翠绿色三角形，右侧实心亮黄色正方形，上下各一条粗黑横线',
  model: 'qi21', scene: '极简图标背景', note: '构图可以借', status: 'candidate', reason: '',
  model_name: 'synthetic-model', run_id: id('r'),
  refs: [{ ref: 'e8', label: 'AI 描述，未核实', document_id: id('b'), drop_id: id('a'), start: 0, end: 233, chars: 233,
           excerpt: '主体：左侧为一个绿色的三角形，右侧为一个黄色的正方形。', sha256: '5eceb5697d56ec8d' }],
  source_state: 'verified',
  source: { kind: 'image', kind_label: '图片', channel: 'image', channel_label: '图片', title: FILE,
            received_at: '2026-10-10T05:33:40+00:00', knowledge_url: '/knowledge.html?document=' + id('b'),
            url: 'https://example.com/' + FILE, link: 'https://example.com/' + FILE, reason: '', deleted: false },
  analysis_line: ANALYSIS,
  fill_url: '/lab.html?kind=image&card=' + id('c'), ...extra,
});

async function fixture(t, options = {}) {
  const dom = new JSDOM(fs.readFileSync('public/cards.html', 'utf8'),
    { url: 'http://127.0.0.1:8766/cards.html', runScripts: 'outside-only', pretendToBeVisual: true });
  t.after(() => dom.window.close());
  const w = dom.window, d = w.document, calls = [];
  // 默认模拟窄屏（375 宽那一档）：每行 20 字，超过 3 行就被裁。
  w.__cdMeasure = options.measure || fakeMeasure({});
  // jsdom 不会抓 <link>，把页面 CSS 内联进来才能读 computed style。
  for (const sheet of options.styles || ['public/cards.css']) {
    const style = d.createElement('style');
    style.textContent = fs.readFileSync(sheet, 'utf8');
    d.head.appendChild(style);
  }
  let items = options.items || [card()];
  let counts = { candidate: 0, confirmed: 0, rejected: 0 };
  const recount = () => {
    counts = { candidate: items.filter(x => x.status === 'candidate').length,
               confirmed: items.filter(x => x.status === 'confirmed').length,
               rejected: items.filter(x => x.status === 'rejected').length };
  };
  recount();
  w.fetch = async (url, opts = {}) => {
    const target = String(url);
    calls.push({ url: target, opts });
    if (target.startsWith('/api/cards?')) {
      const params = new w.URLSearchParams(target.split('?')[1]);
      const status = params.get('status') || '', query = (params.get('q') || '').trim(), kind = params.get('kind') || '';
      const channelOf = item => (String(item.source?.reason || '').startsWith('情报采集') ? 'intel' : (item.source?.channel || item.source?.kind || ''));
      const shown = items.filter(x => (!status || x.status === status) && (!kind || channelOf(x) === kind) &&
        (!query || [x.prompt, x.scene, x.note].some(value => String(value).includes(query))));
      return ok({ schema: 'intake-cards/v1', total: shown.length, items: shown, counts, pending_drops: 0,
                  view: params.get('view') || 'source' });
    }
    if (target === '/api/cards/cleanup') {
      const body = opts.body ? JSON.parse(opts.body) : {};
      items = items.filter(item => item.drop_id !== body.drop_id);
      recount();
      return ok({ schema: 'intake-cards-delete/v1', deleted_cards: 1, drop_id: body.drop_id });
    }
    const removed = target.match(/^\/api\/cards\/([a-f0-9]{32})\/delete$/);
    if (removed) {
      items = items.filter(item => item.id !== removed[1]);
      recount();
      return ok({ schema: 'intake-cards-delete/v1', deleted_cards: 1 });
    }
    const decision = target.match(/^\/api\/cards\/([a-f0-9]{32})\/(confirm|reject|restore|note)$/);
    if (decision) {
      const [, cardId, action] = decision;
      const body = opts.body ? JSON.parse(opts.body) : {};
      if (action === 'note' && Object.keys(body).some(key => key !== 'note')) return bad('卡片备注只接受 note');
      if (action !== 'note' && Object.keys(body).some(key => key !== 'reason')) return bad('卡片操作只接受 reason');
      items = items.map(item => item.id === cardId ? {
        ...item,
        status: action === 'confirm' ? 'confirmed' : action === 'reject' ? 'rejected' : action === 'restore' ? 'candidate' : item.status,
        reason: action === 'reject' ? (body.reason || '') : '',
        note: action === 'note' ? body.note : item.note,
      } : item);
      recount();
      return ok(items.find(item => item.id === cardId));
    }
    return bad('接口不存在');
  };
  if (options.promptReply !== undefined) w.prompt = () => options.promptReply;
  w.__confirms = [];
  w.confirm = message => { w.__confirms.push(message); return options.confirmReply === true; };
  w.eval(fs.readFileSync('public/cards.js', 'utf8'));
  await settle();
  return { w, d, calls, state: () => ({ items, counts }) };
}

// ------------------------------------------------------------------ H1 组标题

test('组标题不再是原始文件名，原始文件名仍原样留在来源信息里', async t => {
  const { d } = await fixture(t);
  const head = d.querySelector('.cd-group-head');
  const title = head.querySelector('h2').textContent;
  assert.notEqual(title, FILE);
  assert.ok(!title.includes('.png'), '标题里不能出现导出文件名: ' + title);
  assert.ok(!title.includes('20261010T052433997515Z'), '标题里不能是编号串: ' + title);
  assert.equal(title, '绿三角与黄方块的极简海报');
  // 出处一个字都不改：小字一行里还是那个文件名，悬停给全称。
  const origin = head.querySelector('.cd-group-origin');
  assert.ok(origin, '组头要有原始来源行');
  assert.equal(origin.querySelector('b').textContent, FILE);
  assert.equal(origin.querySelector('b').getAttribute('title'), FILE);
  assert.match(origin.textContent, /原始来源/);
  // 卡片头不再把同一个文件名重复一遍。
  assert.ok(!d.querySelector('.cd-card-head .cd-from').textContent.includes(FILE));
});

test('没有可写角度时用类型加收下日期当标题', async t => {
  const { d } = await fixture(t, { items: [card({ analysis_line: '' })] });
  assert.equal(d.querySelector('.cd-group-head h2').textContent, '图片 · 10/10 收下');
  assert.equal(d.querySelector('.cd-group-origin b').textContent, FILE);
});

test('回收项本来就有像样的标题时直接用它，来源行不再重复一遍', async t => {
  const named = card({ source: { ...card().source, title: '旧码头黄昏的拍法' } });
  const { d } = await fixture(t, { items: [named] });
  assert.equal(d.querySelector('.cd-group-head h2').textContent, '旧码头黄昏的拍法');
  assert.equal(d.querySelector('.cd-group-origin'), null);
});

test('来源已删除仍然如实写，不造一个指不到东西的标题', async t => {
  const gone = { kind: '', kind_label: '回收项已删除', title: '', received_at: '', knowledge_url: '', url: '', link: '', reason: '', deleted: true };
  const { d } = await fixture(t, { items: [card({ source_state: 'missing', source: gone, analysis_line: '' })] });
  assert.equal(d.querySelector('.cd-group-head h2').textContent, '来源已删除');
  assert.equal(d.querySelector('.cd-group-origin'), null);
  assert.equal(d.querySelector('.cd-group-link'), null);
  assert.match(d.querySelector('.cd-group-summary').textContent, /没有留下四块分析/);
});

// ------------------------------------------------------------------ M1 摘要

test('完整分析默认收起，展开只改本地状态、不多发请求', async t => {
  const { w, d, calls } = await fixture(t);
  const summary = d.querySelector('.cd-group-summary');
  assert.ok(!summary.classList.contains('cd-group-open'), '默认应是收起状态');
  const clipped = w.getComputedStyle(summary.querySelector('.cd-group-line'));
  assert.equal(clipped.overflow, 'hidden', '收起状态必须真的视觉折叠');
  assert.equal(clipped.getPropertyValue('-webkit-line-clamp'), '3', '组头默认只给 2–3 行来源说明');
  // 收起只是视觉折叠，一个字都没有从 DOM 里删掉。
  assert.equal(summary.querySelector('.cd-group-line').textContent, ANALYSIS);
  const more = d.querySelector('.cd-group-more');
  assert.ok(more, '长分析要给展开入口');
  assert.equal(more.textContent, '展开完整分析');
  assert.equal(more.getAttribute('aria-expanded'), 'false');
  const before = calls.length;
  more.click();
  await settle();
  assert.equal(calls.length, before, '展开完整分析是只读的，不该调接口');
  assert.ok(summary.classList.contains('cd-group-open'));
  const opened = w.getComputedStyle(summary.querySelector('.cd-group-line'));
  assert.notEqual(opened.overflow, 'hidden', '展开后不再裁切');
  assert.equal(summary.querySelector('.cd-group-line').textContent, ANALYSIS);
  assert.equal(d.querySelector('.cd-group-more').getAttribute('aria-expanded'), 'true');
  assert.equal(d.querySelector('.cd-group-more').textContent, '收起分析');
  d.querySelector('.cd-group-more').click();
  await settle();
  assert.ok(!summary.classList.contains('cd-group-open'));
  assert.equal(calls.length, before);
});

test('短分析不给展开按钮，也不会被截断', async t => {
  const { d } = await fixture(t, { items: [card({ analysis_line: '旧码头的黄昏适合低对比背影。值得收：开头方式能借。' })] });
  assert.equal(d.querySelector('.cd-group-more'), null);
  assert.equal(d.querySelector('.cd-group-summary .cd-group-line').textContent, '旧码头的黄昏适合低对比背影。值得收：开头方式能借。');
});

// ------------------------------------------------ M1 补：按实际裁切判定，不按字数（fixes-cards-readability-2）

test('不足 90 字但窄屏实际被裁到 3 行的摘要也必须给展开入口', async t => {
  assert.ok(SHORT_ANALYSIS.length < 90, '用例本身要短于旧的 90 字阈值，实测 ' + SHORT_ANALYSIS.length);
  const { d, w, calls } = await fixture(t, {
    items: [card({ analysis_line: SHORT_ANALYSIS })],
    measure: fakeMeasure({ charsPerLine: 16 }),   // 375 宽那一档：80 字折成 5 行
  });
  const summary = d.querySelector('.cd-group-summary'), line = summary.querySelector('.cd-group-line');
  const box = w.__cdMeasure(line);
  assert.ok(box.scrollHeight > box.clientHeight, '这条摘要在窄屏确实被裁');
  const more = d.querySelector('.cd-group-more');
  assert.ok(more, '被裁切却没有展开入口，用户就看不到剩下的分析');
  assert.equal(more.textContent, '展开完整分析');
  assert.equal(more.getAttribute('aria-expanded'), 'false');
  const before = calls.length;
  more.click();
  await settle();
  assert.ok(summary.classList.contains('cd-group-open'));
  assert.equal(d.querySelector('.cd-group-more').textContent, '收起分析');
  assert.equal(line.textContent, SHORT_ANALYSIS, '展开只是把已渲染的字放出来，一个字没补写');
  assert.equal(calls.length, before, '展开仍是只读，不发请求');
});

test('同样这条摘要在宽屏放得下时不给多余入口（判定看高度不看字数）', async t => {
  const { d } = await fixture(t, {
    items: [card({ analysis_line: SHORT_ANALYSIS })],
    measure: fakeMeasure({ charsPerLine: 400 }),
  });
  assert.equal(d.querySelector('.cd-group-more'), null, '没被裁切就不该出现展开按钮');
  assert.equal(d.querySelector('.cd-group-summary .cd-group-line').textContent, SHORT_ANALYSIS);
});

test('超过 90 字但实际没被裁切时也不给入口', async t => {
  assert.ok(ANALYSIS.length > 90, '用例本身要长于旧的 90 字阈值，实测 ' + ANALYSIS.length);
  const { d } = await fixture(t, { items: [card({ analysis_line: ANALYSIS })], measure: fakeMeasure({ charsPerLine: 400 }) });
  assert.equal(d.querySelector('.cd-group-more'), null, '旧代码按字数给入口，宽屏会多出一个没用的按钮');
});

test('窗口变窄或变宽后重新判定展开入口', async t => {
  const { d, w } = await fixture(t, {
    items: [card({ analysis_line: SHORT_ANALYSIS })],
    measure: fakeMeasure({ charsPerLine: 400 }),
  });
  assert.equal(d.querySelector('.cd-group-more'), null);
  w.__cdMeasure = fakeMeasure({ charsPerLine: 16 });
  w.dispatchEvent(new w.Event('resize'));
  await new Promise(resolve => setTimeout(resolve, 300));
  await settle();
  assert.ok(d.querySelector('.cd-group-more'), '变窄后被裁切，要补上入口');
  w.__cdMeasure = fakeMeasure({ charsPerLine: 400 });
  w.dispatchEvent(new w.Event('resize'));
  await new Promise(resolve => setTimeout(resolve, 300));
  await settle();
  assert.equal(d.querySelector('.cd-group-more'), null, '变宽后放得下，入口要收掉');
});

// ------------------------------------------------ L1 补：标题边界（fixes-cards-readability-2）

test('标题里提到文件名不等于整条标题就是文件名', async t => {
  for (const title of ['如何使用 a.md', 'Python 3.14 的新能力', '第 2.0 版发布', '正常中文标题.后续']) {
    const { d } = await fixture(t, { items: [card({ source: { ...card().source, title } })] });
    assert.equal(d.querySelector('.cd-group-head h2').textContent, title, '人写的标题必须原样保留：' + title);
    assert.equal(d.querySelector('.cd-group-origin'), null, '标题本身就是出处，不再重复一行');
  }
});

test('整串无空白的导出文件名与图片名仍判为原始出处', async t => {
  for (const name of [FILE, 'IMG_1234.JPG', 'round6-B.png', 'notes.txt']) {
    const { d } = await fixture(t, { items: [card({ source: { ...card().source, title: name } })] });
    const title = d.querySelector('.cd-group-head h2').textContent;
    assert.notEqual(title, name, '导出文件名不能当标题：' + name);
    assert.equal(d.querySelector('.cd-group-origin b').textContent, name, '原始文件名仍原样留着：' + name);
  }
});

// ------------------------------------------------ L2 补：可写角度截法（fixes-cards-readability-2）

test('引号里的顿号不是分隔符，按完整角度条目取第一条', async t => {
  const line = '本资料是官方文档。可写角度：「绿三角、黄方块」构图。相关 2 条。';
  const { d } = await fixture(t, { items: [card({ analysis_line: line })] });
  assert.equal(d.querySelector('.cd-group-head h2').textContent, '「绿三角、黄方块」构图');
  assert.equal(d.querySelector('.cd-group-summary .cd-group-line').textContent, line, '原分析一个字没改');
});

test('顶层顿号仍按角度分隔，取第一条', async t => {
  const { d } = await fixture(t, { items: [card({ analysis_line: '可写角度：绿三角与黄方块的极简海报、把官方文档写成避坑清单。相关 2 条。' })] });
  assert.equal(d.querySelector('.cd-group-head h2').textContent, '绿三角与黄方块的极简海报');
});

test('超长可写角度截到 60 字加省略号，不从引号中间切', async t => {
  const longInside = '绿色三角形与黄色正方形并排，中间留一条粗黑横线，上下各留白，整幅画面只用两种颜色，不加渐变也不加阴影';
  const { d } = await fixture(t, {
    items: [card({ analysis_line: '可写角度：把官方文档写成避坑清单，用「' + longInside + '」的极简构图作封面。' })],
  });
  const title = d.querySelector('.cd-group-head h2').textContent;
  assert.ok(title.length <= 61, '标题不该超过 60 字加省略号，实测 ' + title.length + '：' + title);
  assert.ok(title.endsWith('…'), '截断要留省略号：' + title);
  assert.equal((title.match(/「/g) || []).length, (title.match(/」/g) || []).length, '引号不能不成对：' + title);
  assert.ok(!title.includes('「'), '引号内装不下时要从引号前收刀，不切半句：' + title);
  assert.ok(!/[的用和与而把在，、]$/.test(title.slice(0, -1)), '省略号前不该是一个虚词尾巴：' + title);
});

test('超长角度在引号外截断时保留完整引号段', async t => {
  const line = '可写角度：把官方文档写成避坑清单，用「绿三角、黄方块」的极简构图作封面，' +
    '再补一段自定义 encoder 的写法与常见坑，最后给一张速查表收尾。';
  const { d } = await fixture(t, { items: [card({ analysis_line: line })] });
  const title = d.querySelector('.cd-group-head h2').textContent;
  assert.ok(title.includes('「绿三角、黄方块」'), '引号段要么完整保留要么整段去掉：' + title);
  assert.ok(title.endsWith('…'), '截断要留省略号：' + title);
  assert.equal((title.match(/「/g) || []).length, (title.match(/」/g) || []).length, '引号不能不成对：' + title);
  assert.ok(!/[的用和与而把在，、]$/.test(title.slice(0, -1)), '省略号前不该是一个虚词尾巴：' + title);
});

// -------------------------------- L1 补2：不再靠「有没有空格」判文件名（fixes-cards-readability-3）

test('无空格的中文标题不再被当成文件名，含中文的标题一律原样保留', async t => {
  for (const title of ['如何使用a.md', '一篇叫笔记.txt的草稿', '如何用「.md」做笔记', '2026年10月的新变化', '正常中文标题.后续', '使用说明.md']) {
    const { d } = await fixture(t, { items: [card({ source: { ...card().source, title } })] });
    assert.equal(d.querySelector('.cd-group-head h2').textContent, title, '含中文的标题必须原样保留：' + title);
    assert.equal(d.querySelector('.cd-group-origin'), null, '标题本身就是出处，不再重复一行：' + title);
  }
});

test('纯 ASCII 的导出名、带路径的名字与日期编号名仍判为原始出处', async t => {
  for (const name of [FILE, 'IMG_1234.JPG', 'round6-B.png', 'notes.txt', 'DSC_0042.HEIC',
    'screenshot-2026-10-10-14-30-22.png', 'workspace/notes.md', 'IMG_1234', '20261010']) {
    const { d } = await fixture(t, { items: [card({ source: { ...card().source, title: name } })] });
    const title = d.querySelector('.cd-group-head h2').textContent;
    assert.notEqual(title, name, '机器导出的名字不能当标题：' + name);
    assert.equal(title, '绿三角与黄方块的极简海报', '判为文件名后要退回分析里的可写角度：' + name);
    assert.equal(d.querySelector('.cd-group-origin b').textContent, name, '原始名字仍原样留着：' + name);
  }
});

// -------------------------------- L2 补2：引号/括号内的句号不算截断点（fixes-cards-readability-3）

test('引号或括号里的句号不再截出半边引号', async t => {
  const cases = [
    ['「先留白。再构图」的海报', '可写角度：「先留白。再构图」的海报、备用角度。'],
    ['『先留白。再构图』的海报', '可写角度：『先留白。再构图』的海报、备用角度。'],
    ['“先留白。再构图”的海报', '可写角度：“先留白。再构图”的海报、备用角度。'],
    ['"先留白。再构图"的海报', '可写角度："先留白。再构图"的海报、备用角度。'],
    ['海报（先留白。再构图）的写法', '可写角度：海报（先留白。再构图）的写法、备用角度。'],
    ['海报(先留白。再构图)的写法', '可写角度：海报(先留白。再构图)的写法、备用角度。'],
    ['【先留白。再构图】的海报', '可写角度：【先留白。再构图】的海报、备用角度。'],
    ['《先留白。再构图》的海报', '可写角度：《先留白。再构图》的海报、备用角度。'],
  ];
  for (const [want, line] of cases) {
    const { d } = await fixture(t, { items: [card({ analysis_line: line })] });
    const title = d.querySelector('.cd-group-head h2').textContent;
    assert.equal(title, want, '引号/括号里的句号是条目内部，不能截断：' + line);
    assert.equal(d.querySelector('.cd-group-summary .cd-group-line').textContent, line, '原分析一个字没改');
  }
});

test('引号里多个句号仍按完整条目取，顶层句号才是这块分析的结尾', async t => {
  const line = '可写角度：「先留白。再构图。最后压色」的海报。相关 5 条。';
  const { d } = await fixture(t, { items: [card({ analysis_line: line })] });
  const title = d.querySelector('.cd-group-head h2').textContent;
  assert.equal(title, '「先留白。再构图。最后压色」的海报');
  assert.ok(!title.includes('相关'), '不能吞掉后面那块分析：' + title);
});

test('嵌套括号里的句号按配对层级判定，整条角度完整保留', async t => {
  const line = '可写角度：海报（主标「先留白。再构图」）的写法、备用角度。';
  const { d } = await fixture(t, { items: [card({ analysis_line: line })] });
  assert.equal(d.querySelector('.cd-group-head h2').textContent, '海报（主标「先留白。再构图」）的写法');
});

test('落单的引号：宁可退回类型加收下日期，也不给半个引号', async t => {
  for (const line of ['可写角度：「先留白。再构图的海报、备用角度。', '可写角度：用「绿三角、黄方块的极简海报。备用角度。']) {
    const { d } = await fixture(t, { items: [card({ analysis_line: line })] });
    const title = d.querySelector('.cd-group-head h2').textContent;
    assert.equal(title, '图片 · 10/10 收下', '取不出成对完整的角度就退回下一个标题层级：' + line);
    assert.ok(!title.includes('「'), '组标题里不能留半个引号：' + title);
    assert.equal(d.querySelector('.cd-group-origin b').textContent, FILE, '原始来源行不受影响');
  }
});

test('落单的闭引号只丢掉那个符号，不牵连整条角度', async t => {
  const line = '可写角度：先留白」。再构图的海报。';
  const { d } = await fixture(t, { items: [card({ analysis_line: line })] });
  const title = d.querySelector('.cd-group-head h2').textContent;
  assert.equal(title, '先留白', '只去掉落单的半个引号，不从它后面全丢：' + title);
});

// ---------------- L1 补3：编号判据收窄 + 开头下划线/复合扩展名（fixes-cards-readability-4）

test('产品名、标准号、版本名与带空格的 ASCII 名按标题保留，不再被当成机器编号', async t => {
  // GPT123/CSS2026/ISO9001 是上一轮点名的误判；README、v2.0、Node.js 等是复核表里应保持标题的正例。
  // photo copy.jpg、screenshot 2026-10-10 my review notes 是已采纳的保守取舍：带空格按人写的标题。
  const titles = ['GPT123', 'CSS2026', 'ISO9001', 'ISO-9001:2015', 'GPT-128k', 'README', 'v2.0', 'Node.js', 'API', 'HTTP', 'GPT4', 'MD5', 'v1.2.3',
    'photo copy.jpg', 'screenshot 2026-10-10 my review notes', '_notes'];
  for (const title of titles) {
    const { d } = await fixture(t, { items: [card({ source: { ...card().source, title } })] });
    assert.equal(d.querySelector('.cd-group-head h2').textContent, title, '产品/标准/版本名必须原样保留：' + title);
    assert.equal(d.querySelector('.cd-group-origin'), null, '标题本身就是出处，不再重复一行：' + title);
  }
});

test('相机、手机与聊天工具的编号名仍判为原始出处', async t => {
  for (const name of ['IMG_1234', 'DSC01234', 'DSC_0042.HEIC', 'DSCF1234.JPG', 'DSCN0042.jpg', 'IMG_20261010_143022',
    'PXL_20261010_120456789', 'VID_20261010_143022', 'mmexport1699999999999.jpg', 'screenshot-2026-10-10-14-30-22.png',
    'Screenshot 2026-10-10 at 10.15.32.png', '100MEDIA/IMG_1234.JPG']) {
    const { d } = await fixture(t, { items: [card({ source: { ...card().source, title: name } })] });
    const title = d.querySelector('.cd-group-head h2').textContent;
    assert.notEqual(title, name, '导出编号名不能当标题：' + name);
    assert.equal(title, '绿三角与黄方块的极简海报', '判为文件名后要退回分析里的可写角度：' + name);
    assert.equal(d.querySelector('.cd-group-origin b').textContent, name, '原始名字仍原样留着：' + name);
  }
});

test('开头下划线的导出名与复合扩展名判为原始出处', async t => {
  for (const name of ['_IMG_1234.png', '_DSC01234.jpg', 'build.tar.gz', 'build.tar.bz2', 'build.tar.xz', 'app.tgz', 'archive.zip']) {
    const { d } = await fixture(t, { items: [card({ source: { ...card().source, title: name } })] });
    const title = d.querySelector('.cd-group-head h2').textContent;
    assert.notEqual(title, name, '这类名字就是文件名，不能当标题：' + name);
    assert.equal(title, '绿三角与黄方块的极简海报', '判为文件名后要退回分析里的可写角度：' + name);
    assert.equal(d.querySelector('.cd-group-origin b').textContent, name, '原始名字仍原样留着：' + name);
  }
});

// ---------------- L2 补3：独立成对的单引号 ‘’（fixes-cards-readability-4）

test('独立成对的单引号里的句号不截断，拿得到完整角度', async t => {
  const cases = [
    ['‘先留白。再构图’海报', '可写角度：‘先留白。再构图’海报。'],            // 上一轮点名的探针句，旧结果 ‘先留白
    ['‘先留白。再构图’海报', '可写角度：‘先留白。再构图’海报、备用角度。'],
    ['‘先留白。再构图。最后压色’的海报', '可写角度：‘先留白。再构图。最后压色’的海报。相关 3 条。'],
    ['把 ‘先留白’ 写成海报', '可写角度：把 ‘先留白’ 写成海报、备用角度。'],
  ];
  for (const [want, line] of cases) {
    const { d } = await fixture(t, { items: [card({ analysis_line: line })] });
    const title = d.querySelector('.cd-group-head h2').textContent;
    assert.equal(title, want, '单引号里的句号是条目内部，不能截断：' + line);
    assert.equal((title.match(/‘/g) || []).length, (title.match(/’/g) || []).length, '单引号不能不成对：' + title);
    assert.equal(d.querySelector('.cd-group-summary .cd-group-line').textContent, line, '原分析一个字没改');
  }
});

test('英文撇号不当引号：it’s、don’t、rock’n’roll 里的角度照常取', async t => {
  const cases = [
    ['it’s a poster with negative space', '可写角度：it’s a poster with negative space。备用角度。'],
    ['don’t 满版铺色', '可写角度：don’t 满版铺色、备用角度。'],
    ['rock’n’roll 的海报', '可写角度：rock’n’roll 的海报、备用角度。'],
    ['it’s 里给 ‘先留白’ 留位', '可写角度：it’s 里给 ‘先留白’ 留位、备用角度。'],
  ];
  for (const [want, line] of cases) {
    const { d } = await fixture(t, { items: [card({ analysis_line: line })] });
    const title = d.querySelector('.cd-group-head h2').textContent;
    assert.equal(title, want, '词中撇号不是引号，不能因此丢掉整条角度：' + line);
  }
});

test('落单的单引号：开引号退回类型加收下日期，闭引号只丢那个符号', async t => {
  const open = '可写角度：‘先留白。再构图的海报、备用角度。';
  const { d: d1 } = await fixture(t, { items: [card({ analysis_line: open })] });
  const title1 = d1.querySelector('.cd-group-head h2').textContent;
  assert.equal(title1, '图片 · 10/10 收下', '单引号没封口就退回下一个标题层级：' + open);
  assert.ok(!title1.includes('‘'), '组标题里不能留半个引号：' + title1);
  assert.equal(d1.querySelector('.cd-group-origin b').textContent, FILE, '原始来源行不受影响');
  const close = '可写角度：先留白’。再构图的海报。';
  const { d: d2 } = await fixture(t, { items: [card({ analysis_line: close })] });
  assert.equal(d2.querySelector('.cd-group-head h2').textContent, '先留白', '只去掉落单的半个引号，不从它后面全丢：' + close);
});

// ---------------- R03 第三版：一次线性扫描定配对（smallfix-cards-metrics-c）

// 直接跑 public/cards.js 里的真实函数，不经过 DOM，方便逐位检查扫描结果与计时。
function cardsFns() {
  const src = fs.readFileSync('public/cards.js', 'utf8');
  const start = src.indexOf('const FILE_EXT ='), end = src.indexOf('function groupTitle(');
  assert.ok(start >= 0 && end > start, 'cards.js 的函数区间变了，测试要跟着改');
  return new Function(src.slice(start, end) + '\nreturn { isApostrophe, pairScan, pairsBalanced, dropUnpaired, analysisTopic, clipTopic };')();
}

// 扫描逐位标记：P 按撇号处理 / < 配对的开引号 / > 配对的闭引号 / X 落单的开或闭 / . 普通字符
function scanMarks(pairScan, text) {
  const { kind, unmatched, lone, apostropheAt } = pairScan(text);
  let out = '';
  for (let i = 0; i < text.length; i++) {
    out += apostropheAt[i] ? 'P' : kind[i] === 1 ? '<' : kind[i] === -1 ? '>'
      : (unmatched.includes(i) || lone.includes(i)) ? 'X' : '.';
  }
  return out;
}

test('一次线性扫描的逐位判定：撇号、成对引号、落单符号各归各位', async t => {
  const { pairScan, pairsBalanced, analysisTopic } = cardsFns();
  const cases = [
    ['rock ’n’ roll', '.....P.P.....'],                 // 空格缩略语两个撇号都不参与配对
    ['it’s、don’t', '..P.....P.'],                    // 词中撇号
    ['rock’n’roll', '....P.P....'],                    // 紧挨字母的撇号
    ['‘90s 的翻红复刻', 'P.........'],                 // 后面没有闭引号 → 词首省略撇号
    ['‘90s。再构图’的海报', '<.......>...'],       // 后面有闭引号 → 真开引号，句号在引号内
    ['把 ’em 写进‘标题’的海报', '..P.....<..>...'],  // 词首撇号与真引号同现：那个 ’ 不参与闭合
    ['’90s 搭配‘标题’的海报', 'P......<..>...'],
    ['‘90s 里的‘怀旧’。再构图’的海报', '<......<..>....>...'],   // 嵌套：两层各自配对
    ['先留白’。再构图的海报', '...X.......'],         // 落单闭引号仍算落单
    ['‘先留白', 'X...'],                            // 不是省略形状的落单开引号
    ['“a’b”c', '<.P.>.'],                          // “ ” 永远按引号算，只有 ’ 能当撇号
    ['“先留白”', '<...>'],                        // 双弯引号照常配对
    ['「先留白」', '<...>'],                        // 直角括号照常配对
    ['‘ok。先留白’', '<......>'],                  // 名单外小写词开头的真引号也照常配对
    ['把 ’em 当默认', '..P......'],
    ['’90s’ 复古', 'P...X...'],                   // 词首撇号保留，落单的闭引号单独记出来
    ['“90s。再构图的海报', 'X..........'],          // 未闭合双弯引号不降格成撇号，留 unmatched
    ['“90s 搭配‘标题’。再构图的海报', 'X......<..>.......'],   // 双弯未闭合、里面的单引号照常配对
    ['前缀“90s。再构图的海报', '..X..........'],
    ['“90s。再构图”的海报', '<.......>...'],       // 成对的双弯引号照常配对
  ];
  for (const [text, want] of cases) assert.equal(scanMarks(pairScan, text), want, '扫描逐位判定错了：' + text);
  assert.equal(pairsBalanced('rock ’n’ roll 的海报'), true);
  assert.equal(pairsBalanced('‘90s 的翻红复刻'), true);
  assert.equal(pairsBalanced('把 ’em 写进‘标题’的海报'), true);
  assert.equal(pairsBalanced('‘先留白'), false);
  assert.equal(pairsBalanced('先留白’'), false);
  assert.equal(pairsBalanced('“90s。再构图的海报'), false);   // 落单的双弯引号必须报未配平
  assert.equal(pairsBalanced('“90s。再构图”的海报'), true);
  assert.equal(analysisTopic('可写角度：‘90s 的翻红复刻、备用角度。'), '‘90s 的翻红复刻');
  assert.equal(analysisTopic('可写角度：rock ’n’ roll 的海报、备用角度。'), 'rock ’n’ roll 的海报');
});

test('词首省略撇号与空格缩略语不当引号：整条角度照常取', async t => {
  // 旧码把 ‘90s 当成没封口的开引号（标题空掉），把 rock ’n’ roll 截成 rock。
  const cases = [
    ['‘90s 的翻红复刻', '可写角度：‘90s 的翻红复刻、备用角度。'],
    ['’90s 的翻红复刻', '可写角度：’90s 的翻红复刻、备用角度。'],
    ['rock ’n’ roll 的海报', '可写角度：rock ’n’ roll 的海报、备用角度。'],
    ['rock ’em out 的排版', '可写角度：rock ’em out 的排版、备用角度。'],
    ['把 ’til 结尾的短句写进主标', '可写角度：把 ’til 结尾的短句写进主标、备用角度。'],
    ['‘90s 复古滤镜 配 冷调底色', '可写角度：‘90s 复古滤镜 配 冷调底色、备用角度。'],
    ['把 ’em 当默认风格 don’t 收尾', '可写角度：把 ’em 当默认风格 don’t 收尾、备用角度。'],
  ];
  for (const [want, line] of cases) {
    const { d } = await fixture(t, { items: [card({ analysis_line: line })] });
    const title = d.querySelector('.cd-group-head h2').textContent;
    assert.equal(title, want, '词首省略撇号/空格缩略语不是引号，不能因此丢掉整条角度：' + line);
    assert.equal(d.querySelector('.cd-group-summary .cd-group-line').textContent, line, '原分析一个字没改');
  }
});

test('省略撇号不误伤真引号：数字与名单外的小写词仍按成对引号算', async t => {
  const cases = [
    ['‘90s’ 复古滤镜', '可写角度：‘90s’ 复古滤镜、备用角度。'],
    ['‘em’ 那个词怎么排版', '可写角度：‘em’ 那个词怎么排版、备用角度。'],
    ['‘ok。先留白’的检查表', '可写角度：‘ok。先留白’的检查表、备用角度。'],
    ['‘poster’ 这个词怎么排', '可写角度：‘poster’ 这个词怎么排、备用角度。'],
    ['‘90s 复古 don’t 收尾的海报', '可写角度：‘90s 复古 don’t 收尾的海报、备用角度。'],
  ];
  for (const [want, line] of cases) {
    const { d } = await fixture(t, { items: [card({ analysis_line: line })] });
    const title = d.querySelector('.cd-group-head h2').textContent;
    assert.equal(title, want, '成对引号里的句号/空格不能被当成省略撇号：' + line);
    assert.equal((title.match(/‘/g) || []).length, (title.match(/’/g) || []).length, '单引号不能不成对：' + title);
  }
});

test('复核反例：数字或省略词开头的真正成对引号，标题必须完整', async t => {
  // 第一轮复核 M1 的六例：词首形状和省略撇号一样，但后面有能配上的闭引号，是真引号，不能截断。
  const cases = [
    ['‘90s。再构图’的海报', '可写角度：‘90s。再构图’的海报、备用角度。'],
    ['‘90s 的复古。再构图’的海报', '可写角度：‘90s 的复古。再构图’的海报、备用角度。'],
    ['‘em。再构图’的海报', '可写角度：‘em。再构图’的海报、备用角度。'],
    ['‘cause and effect。关系图’的海报', '可写角度：‘cause and effect。关系图’的海报、备用角度。'],
    ['‘90s、复古’的海报', '可写角度：‘90s、复古’的海报、备用角度。'],
    ['‘90s；复古’的海报', '可写角度：‘90s；复古’的海报、备用角度。'],
    ['‘90s 里的‘怀旧’。再构图’的海报', '可写角度：‘90s 里的‘怀旧’。再构图’的海报、备用角度。'],   // 返修 b 新增的嵌套例
  ];
  for (const [want, line] of cases) {
    const { d } = await fixture(t, { items: [card({ analysis_line: line })] });
    const title = d.querySelector('.cd-group-head h2').textContent;
    assert.equal(title, want, '成对引号里的句号、顿号、分号都不是分隔：' + line);
    assert.equal((title.match(/‘/g) || []).length, (title.match(/’/g) || []).length, '单引号不能不成对：' + title);
    assert.equal(d.querySelector('.cd-group-summary .cd-group-line').textContent, line, '原分析一个字没改');
  }
});

test('返修 c 反例：词首撇号与真引号同现时不能错配，标题必须完整', async t => {
  // 本次复核 M1：’em、’til、’90s 这种词首撇号后面紧跟真正的开引号，不能被错配成一对。
  const cases = [
    ['把 ’em 写进‘标题’的海报', '把'],
    ['把 ’til 搭配‘标题’的海报', '把'],
    ['’90s 搭配‘标题’的海报', ''],
    ['把 ’em 写进‘poster’的海报', '把'],
    ['把 ’em 搭配‘90s。复古’的海报', '把'],
  ];
  for (const [angle, wrongOnB] of cases) {
    const line = '可写角度：' + angle + '、备用角度。';
    const { d } = await fixture(t, { items: [card({ analysis_line: line })] });
    const title = d.querySelector('.cd-group-head h2').textContent;
    assert.equal(title, angle, '词首撇号不参与闭合，整条角度要完整保留：' + line);
    assert.notEqual(title, wrongOnB, '不能退回返修 b 的错配结果：' + line);
    assert.equal(d.querySelector('.cd-group-summary .cd-group-line').textContent, line, '原分析一个字没改');
  }
});

test('返修 d 反例：未闭合的双弯引号永远按引号算，不降格成撇号', async t => {
  // 本次复核 M1：数字或名单词开头的未闭合双引号被当成省略撇号，标题留下半边 “90s。
  // 基座对这几例都退到下一个标题层级，修后必须一致。
  const cases = [
    ['“90s。再构图的海报', '图片 · 10/10 收下'],
    ['“2026 年留白。再构图的海报', '图片 · 10/10 收下'],
    ['“em。再构图的海报', '图片 · 10/10 收下'],
    ['“90s 搭配‘标题’。再构图的海报', '图片 · 10/10 收下'],
    ['前缀“90s。再构图的海报', '前缀'],
    ['“先留白。再构图的海报', '图片 · 10/10 收下'],
  ];
  for (const [angle, want] of cases) {
    const line = '可写角度：' + angle + '、备用角度。';
    const { d } = await fixture(t, { items: [card({ analysis_line: line })] });
    const title = d.querySelector('.cd-group-head h2').textContent;
    assert.equal(title, want, '未闭合双引号不能被降格成撇号：' + line);
    assert.ok(!title.includes('“') && !title.includes('”'), '组标题里不能留半边双引号：' + title);
    assert.equal(d.querySelector('.cd-group-summary .cd-group-line').textContent, line, '原分析一个字没改');
  }
  // 复核的超长未闭合双引号：712 字符，也不能给 “90s 怀旧… 这种半边双引号。
  const longDouble = '“90s ' + '怀旧'.repeat(350) + '。再构图的海报';
  assert.equal(longDouble.length, 712, '探针长度变了要重算：' + longDouble.length);
  const { d: d2 } = await fixture(t, { items: [card({ analysis_line: '可写角度：' + longDouble + '、备用。' })] });
  const title2 = d2.querySelector('.cd-group-head h2').textContent;
  assert.ok(!title2.includes('“') && !title2.includes('”'), '超长未闭合双引号不能留半边引号：' + title2.slice(0, 80));
  assert.equal(title2, '图片 · 10/10 收下', '装不下就退回下一个标题层级：' + title2.slice(0, 80));
  // 成对的双弯引号照常完整保留。
  const paired = '可写角度：“90s。再构图”的海报、备用角度。';
  const { d: d3 } = await fixture(t, { items: [card({ analysis_line: paired })] });
  assert.equal(d3.querySelector('.cd-group-head h2').textContent, '“90s。再构图”的海报', '成对的双弯引号仍要完整保留');
});

test('落单引号原有行为不回退：开引号退回类型加收下日期，闭引号只丢那个符号', async t => {
  for (const line of ['可写角度：‘先留白。再构图的海报、备用角度。', '可写角度：「先留白。再构图的海报、备用角度。']) {
    const { d } = await fixture(t, { items: [card({ analysis_line: line })] });
    const title = d.querySelector('.cd-group-head h2').textContent;
    assert.equal(title, '图片 · 10/10 收下', '不是省略形状的落单开引号仍退回下一个标题层级：' + line);
    assert.ok(!title.includes('‘') && !title.includes('「'), '组标题里不能留半个引号：' + title);
  }
  const { d } = await fixture(t, { items: [card({ analysis_line: '可写角度：先留白’。再构图的海报。' })] });
  assert.equal(d.querySelector('.cd-group-head h2').textContent, '先留白', '落单闭引号只丢那个符号：先留白’。再构图的海报。');
});

test('超长角度里的长引号装不下：整段去掉或退回下一层，不留半边引号', async t => {
  // 复核 L1 探针：722 字符，闭引号落在 600 字符以外，也不给 ‘90s 怀旧… 这种半边引号。
  const angle = '‘90s ' + '怀旧'.repeat(350) + '。再构图’的海报';
  const line = '可写角度：' + angle + '、备用。';
  assert.equal(line.length, 722, '探针长度变了要重算：' + line.length);
  const { d } = await fixture(t, { items: [card({ analysis_line: line })] });
  const title = d.querySelector('.cd-group-head h2').textContent;
  assert.equal((title.match(/‘/g) || []).length, (title.match(/’/g) || []).length, '组标题不能留半边引号：' + title.slice(0, 80));
  assert.equal(title, '图片 · 10/10 收下', '引号段整段装不下就退回下一个标题层级：' + title.slice(0, 80));
  assert.equal(d.querySelector('.cd-group-summary .cd-group-line').textContent, line, '原分析一个字没改');
  // 引号段前面还有分句可留时，从引号前收刀，不留半边引号。
  const line2 = '可写角度：' + '怀旧'.repeat(20) + '，‘90s ' + '怀旧'.repeat(30) + '。再构图’的海报、备用。';
  const { d: d2 } = await fixture(t, { items: [card({ analysis_line: line2 })] });
  const title2 = d2.querySelector('.cd-group-head h2').textContent;
  assert.ok(title2.endsWith('…'), '超长仍要截断加省略号：' + title2);
  assert.equal((title2.match(/‘/g) || []).length, (title2.match(/’/g) || []).length, '截断后不能留半边引号：' + title2);
});

test('性能：多个词首省略撇号不反复重扫，n=2000 也在线性时间内返回', async t => {
  // 本次复核 M2：返修 b 在 135 字符上 3 秒不返回、10015 字符直接栈溢出。
  // 实测新版 n=2000 约 7ms；阈值放到 250ms（目标 50ms 的 5 倍）避免机器偶发。
  const { analysisTopic } = cardsFns();
  for (const n of [24, 200, 2000]) {
    const line = '可写角度：' + '‘90s '.repeat(n) + '复古海报、备用角度。';
    const started = process.hrtime.bigint();
    const topic = analysisTopic(line);
    const ms = Number(process.hrtime.bigint() - started) / 1e6;
    assert.ok(topic.length > 0, 'n=' + n + ' 不能拿不到角度');
    assert.ok(topic.length <= 61, 'n=' + n + ' 标题仍要截到 60 字加省略号：' + topic.length);
    assert.ok(ms < 250, '线性扫描不该随省略撇号个数爆炸：n=' + n + '（' + line.length + ' 字符）实测 ' + ms.toFixed(1) + 'ms');
  }
});

test('证据编号、原文摘录与指纹在收起摘要后仍然看得到', async t => {
  const { d } = await fixture(t);
  const ref = d.querySelector('.cd-ref');
  assert.match(ref.textContent, /e8/);
  assert.match(ref.textContent, /5eceb5697d56ec8d/);
  assert.match(ref.querySelector('em').textContent, /左侧为一个绿色的三角形/);
  assert.ok(d.querySelector('.cd-knowledge'));
});

// ------------------------------------------------------------------ M2 分区

test('按回收项时来源在组头，按时间时卡片自己带上原始来源名', async t => {
  const { d, w } = await fixture(t);
  assert.equal(d.querySelector('.cd-card .cd-source-name'), null, '分组视图里卡片不该再重复一遍来源名');
  d.querySelector('#cd-layout [data-view="time"]').click();
  await settle();
  assert.equal(d.querySelector('.cd-group'), null);
  const name = d.querySelector('.cd-card .cd-source-name');
  assert.ok(name, '按时间视图里卡片要能看出来源是谁');
  assert.equal(name.querySelector('b').textContent, FILE);
  assert.equal(name.querySelector('b').getAttribute('title'), FILE);
  d.querySelector('#cd-layout [data-view="source"]').click();
  await settle();
  assert.ok(d.querySelector('.cd-group-origin'));
  assert.equal(w.document.querySelectorAll('.cd-group').length, 1);
});

test('核对来源原文与我的备注分成两块，中间有实际间距', async t => {
  const { d, w } = await fixture(t);
  const block = d.querySelector('.cd-source-block');
  assert.ok(block, '来源区要独立成块');
  assert.ok(block.contains(d.querySelector('.cd-knowledge')), '核对来源原文属于来源区');
  const note = d.querySelector('.cd-note-label');
  assert.ok(!block.contains(note), '备注不属于来源区');
  assert.ok((block.compareDocumentPosition(note) & w.Node.DOCUMENT_POSITION_FOLLOWING) !== 0, '备注排在来源区之后');
  const gap = parseInt(w.getComputedStyle(block).marginBottom, 10);
  assert.ok(Number.isFinite(gap) && gap >= 12, '来源区与备注区之间要有明显间距，实测 ' + gap);
});

// ------------------------------------------------------------------ 行为不变

test('确认、备注、驳回、删除、组清理仍打到原来的接口', async t => {
  const gone = { kind: '', kind_label: '回收项已删除', title: '', received_at: '', knowledge_url: '', url: '', link: '', reason: '', deleted: true };
  const items = [card(), card({ id: id('e'), prompt: '再一张候选卡，旧码头只留侧影', drop_id: id('g') }),
    card({ id: id('d'), prompt: '另一张卡，渡口只留一条灯线', drop_id: id('f'), source_state: 'missing', source: gone })];
  const { d, calls, state } = await fixture(t, { items, promptReply: '太抽象', confirmReply: true });
  d.querySelector('[data-id="' + id('c') + '"] [data-action="confirm"]').click();
  await settle();
  assert.ok(calls.some(call => call.url === '/api/cards/' + id('c') + '/confirm'));
  assert.equal(state().items.find(x => x.id === id('c')).status, 'confirmed');
  const box = d.querySelector('[data-note="' + id('c') + '"]');
  box.value = '  先留白  ';
  d.querySelector('[data-id="' + id('c') + '"] [data-action="note"]').click();
  await settle();
  const note = calls.find(call => call.url === '/api/cards/' + id('c') + '/note');
  assert.equal(JSON.parse(note.opts.body).note, '  先留白  ');
  d.querySelector('[data-id="' + id('e') + '"] [data-action="reject"]').click();
  await settle();
  const rejected = calls.find(call => call.url === '/api/cards/' + id('e') + '/reject');
  assert.equal(JSON.parse(rejected.opts.body).reason, '太抽象');
  assert.equal(state().items.find(x => x.id === id('e')).status, 'rejected');
  d.querySelector('[data-id="' + id('e') + '"] [data-action="delete"]').click();
  await settle();
  assert.ok(calls.some(call => call.url === '/api/cards/' + id('e') + '/delete'));
  assert.equal(state().items.length, 2);
  d.querySelector('[data-action="cleanup"]').click();
  await settle();
  const cleanup = calls.find(call => call.url === '/api/cards/cleanup');
  assert.equal(JSON.parse(cleanup.opts.body).drop_id, id('f'));
  assert.equal(state().items.length, 1);
  assert.equal(d.querySelectorAll('.cd-card').length, 1);
});

test('状态筛选、来源类型、搜索与排列方式仍打到原来的接口', async t => {
  const { d, w, calls } = await fixture(t, { items: [card(), card({ id: id('d'), status: 'confirmed', scene: '海报头图' })] });
  d.querySelector('#cd-views [data-status="confirmed"]').click();
  await settle();
  assert.ok(calls.at(-1).url.includes('status=confirmed'));
  assert.equal(d.querySelectorAll('.cd-card').length, 1);
  d.querySelector('#cd-kinds [data-kind="image"]').click();
  await settle();
  assert.ok(calls.at(-1).url.includes('kind=image'));
  const search = d.querySelector('#cd-search');
  search.value = '海报头图';
  search.dispatchEvent(new w.Event('input'));
  await new Promise(resolve => setTimeout(resolve, 350));
  await settle();
  assert.ok(calls.at(-1).url.includes('q=' + encodeURIComponent('海报头图')));
  d.querySelector('#cd-layout [data-view="time"]').click();
  await settle();
  assert.ok(calls.at(-1).url.includes('view=time'));
  d.querySelector('#cd-refresh').click();
  await settle();
  assert.ok(calls.at(-1).url.startsWith('/api/cards?'));
});

// ------------------------------------------------------------------ L1 按钮

test('同一行的链接按钮与按钮字体颜色一致，主操作与危险操作保留各自颜色', async t => {
  const dom = new JSDOM('<style>' + fs.readFileSync('public/style.css', 'utf8') + '</style>' +
    '<style>' + fs.readFileSync('public/lab.css', 'utf8') + '</style>' +
    '<a class="button" id="link">回收站 ↗</a><button class="button" id="plain">刷新</button>' +
    '<button class="button primary" id="primary">确认入库</button><button class="button subtle" id="subtle">删除</button>' +
    '<button class="button lab-danger" id="danger">删除记录</button>',
    { url: 'http://127.0.0.1:8766/cards.html' });
  t.after(() => dom.window.close());
  const w = dom.window, d = w.document;
  const style = sel => {
    const el = d.querySelector(sel);
    const cs = w.getComputedStyle(el);
    return { color: cs.color, family: cs.fontFamily, weight: cs.fontWeight };
  };
  const link = style('#link'), plain = style('#plain');
  assert.equal(link.color, plain.color, '链接按钮与按钮文字颜色必须一致');
  assert.equal(link.family, plain.family, '链接按钮与按钮字体必须一致');
  assert.equal(link.weight, plain.weight);
  assert.equal(w.getComputedStyle(d.querySelector('#primary')).color, 'rgb(255, 255, 255)', '主操作仍是白字蓝底');
  assert.notEqual(w.getComputedStyle(d.querySelector('#subtle')).color, plain.color, '次级操作保留自己的颜色');
  assert.equal(w.getComputedStyle(d.querySelector('#danger')).color, 'rgb(184, 87, 85)', '危险操作保留危险色');
  assert.notEqual(plain.color, 'rgb(66, 99, 235)', '普通按钮不该被染成主操作蓝');
});
