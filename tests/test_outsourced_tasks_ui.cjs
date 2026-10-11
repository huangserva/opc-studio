const test = require('node:test'), assert = require('node:assert/strict'), fs = require('node:fs');
const {JSDOM} = require('jsdom');
const tick = () => new Promise(resolve => setImmediate(resolve));
async function settle() { for (let count = 0; count < 4; count++) await tick(); }
const response = value => ({ok:true,json:async () => value});
const taskId = 'a'.repeat(32);
const base = {id:taskId,revision:3,name:'GitHub Trending AI 日报',body:'抓 GitHub Trending 的 AI 仓库',
  kind:'outsourced',assistant_id:'workbench',tool_groups:[],profile_id:'fake',
  cwd:'/Users/example/workbuddy-outbox/github-trending-ai',frequency:'daily',minutes:60,
  weekdays:[0,1,2,3,4,5,6],at:'09:30',timeout:600,enabled:true,allow_commands:false,next_run:null,
  result_destination:'runs',result_category:'other',clue_id:'',slug:'github-trending-ai',fallback:'pi'};
const run = {id:'b'.repeat(32),task_id:taskId,snapshot:{...base},status:'succeeded',trigger:'scheduled',
  started_at:'2026-10-10T01:30:00Z',finished_at:'2026-10-10T01:31:00Z',exit_code:0,
  output:JSON.stringify({producer:'workbuddy',imported:5,skipped_duplicates:0,warnings:[]})};

async function fixture(t, options = {}) {
  const dom = new JSDOM(fs.readFileSync('public/schedules.html','utf8'),
    {url:'http://127.0.0.1:8766/schedules.html',runScripts:'outside-only',pretendToBeVisual:true});
  t.after(() => dom.window.close());
  const w = dom.window, d = w.document, calls = [], intervals = [], timers = new Map();
  const listing = {tasks:options.tasks || [],default_cwd:'/Users/example/projects/studio',
    default_profile_id:'fake',models:[{profile_id:'fake',display_name:'合成模型',configured:true}],
    scheduler_health:{running:true,last_tick:'2026-10-05T02:00:00Z',last_error:null},
    concurrency:{max:2,active:0,available:2}};
  w.setInterval = (callback,delay) => {intervals.push({callback,delay});return intervals.length;};
  w.setTimeout = (callback,delay) => {const id = timers.size + 1;timers.set(id,{callback,delay});return id;};
  w.clearTimeout = id => timers.delete(id);
  w.confirm = () => true;
  w.HTMLDialogElement.prototype.showModal = function() {this.open = true;};
  w.HTMLDialogElement.prototype.close = function() {this.open = false;};
  const copied = [];
  Object.defineProperty(w.navigator,'clipboard',{value:{writeText:async text => {copied.push(text);}},configurable:true});
  w.prompt = () => {};
  w.fetch = async (url,opts = {}) => {
    const call = {url:String(url),opts,body:opts.body && JSON.parse(opts.body)}; calls.push(call);
    if (options.handler) {const custom = await options.handler(call);if (custom !== undefined) return custom;}
    if (call.url === '/api/schedules') return response(JSON.parse(JSON.stringify(listing)));
    if (call.url === '/api/clues?view=all') return response({items:[]});
    if (call.url.startsWith('/api/schedules/runs?')) return response({runs:options.runs || [],total:(options.runs || []).length,page:1,page_size:50,has_more:false});
    if (call.url.endsWith('/outsourced_prompt')) return response({prompt:options.prompt || '合成提示词',slug:'github-trending-ai',folder:base.cwd});
    return response({id:taskId,ok:true});
  };
  w.eval(fs.readFileSync('public/schedules.js','utf8'));
  await settle();
  return {w,d,calls,copied,listing,intervals,timers,
    form:d.querySelector('#task-form'),
    postCalls:() => calls.filter(call => call.opts.method === 'POST')};
}
function change(f,selector,value) {
  const element = f.d.querySelector(selector);
  if (element.type === 'checkbox') element.checked = value; else element.value = value;
  element.dispatchEvent(new f.w.Event('change',{bubbles:true}));
  return element;
}
function openEditor(f) { f.d.querySelector('[data-action="edit"]').click(); return settle(); }

test('外包任务出现在类型下拉里',async t => {
  const f = await fixture(t,{tasks:[base]});
  const options = [...f.d.querySelectorAll('select[name="kind"] option')].map(o => o.value);
  assert.ok(options.includes('outsourced'), `下拉缺少 outsourced：${options}`);
  const label = [...f.d.querySelectorAll('select[name="kind"] option')]
    .find(o => o.value === 'outsourced').textContent;
  assert.match(label, /外包任务/);
});

test('slug 字段只在选外包任务时出现',async t => {
  const f = await fixture(t,{tasks:[base]});
  assert.equal(f.d.querySelector('#outsourced-slug-label').hidden, true, '默认不应显示');
  await openEditor(f);
  change(f,'select[name="kind"]','outsourced');
  await settle();
  assert.equal(f.d.querySelector('#outsourced-slug-label').hidden, false, '选了外包任务就该显示');
  assert.equal(f.d.querySelector('#outsourced-hint').hidden, false);
  assert.equal(f.d.querySelector('[name="slug"]').required, true);
  assert.match(f.d.querySelector('#assistant-help').textContent, /WorkBuddy/);
  change(f,'select[name="kind"]','pi');
  await settle();
  assert.equal(f.d.querySelector('#outsourced-slug-label').hidden, true, '换回 Pi 就该隐藏');
});

test('任务卡片写明最近一次是谁交付的',async t => {
  const cases = [
    ['workbuddy', /WorkBuddy 交付/],
    ['local-pi', /本机 Pi 兜底/],
    [null, /未交付/],
  ];
  for (const [producer, pattern] of cases) {
    const output = producer ? JSON.stringify({producer,imported:1,skipped_duplicates:0,warnings:[]}) : '';
    const f = await fixture(t,{tasks:[{...base,last_run:{...run,output}}]});
    assert.match(f.d.querySelector('#tasks').textContent, pattern,
      `producer=${producer} 时卡片应显示 ${pattern}`);
  }
});

test('卡片提供复制 WorkBuddy 提示词按钮并真的复制',async t => {
  const f = await fixture(t,{tasks:[{...base,last_run:run}]});
  const button = f.d.querySelector('[data-action="outsourced-prompt"]');
  assert.ok(button, '外包任务卡片应有复制按钮');
  button.click();
  await settle();
  const call = f.calls.find(c => c.url.endsWith('/outsourced_prompt'));
  assert.ok(call, '应调用只读的 outsourced_prompt 接口');
  assert.equal(call.opts.method, undefined, '这是只读接口，不该用 POST');
  assert.equal(f.copied.length, 1, '提示词应写进剪贴板');
  assert.equal(f.copied[0], '合成提示词');
  assert.match(f.d.querySelector('#message').textContent, /已复制/);
});

test('非外包任务不显示复制按钮与交付方',async t => {
  const plain = {...base,kind:'pi',name:'普通 Pi 任务',slug:undefined};
  const f = await fixture(t,{tasks:[plain]});
  assert.equal(f.d.querySelector('[data-action="outsourced-prompt"]'), null);
  assert.ok(!f.d.querySelector('#tasks').textContent.includes('WorkBuddy 交付'));
});

test('接口失败时如实报错，不假装已复制',async t => {
  const f = await fixture(t,{tasks:[{...base,last_run:run}],
    handler:async call => (call.url.endsWith('/outsourced_prompt') ? response({prompt:''}) : undefined)});
  f.d.querySelector('[data-action="outsourced-prompt"]').click();
  await settle();
  assert.equal(f.copied.length, 0, '没有提示词就不该写剪贴板');
  assert.match(f.d.querySelector('#message').textContent, /未能生成提示词/);
});

test('剪贴板不可用时退回手动选中，不静默失败',async t => {
  const f = await fixture(t,{tasks:[{...base,last_run:run}]});
  Object.defineProperty(f.w.navigator,'clipboard',{value:{writeText:async () => {throw new Error('denied');}},configurable:true});
  let prompted = null;
  f.w.prompt = (message, text) => {prompted = text;};
  f.d.querySelector('[data-action="outsourced-prompt"]').click();
  await settle();
  assert.equal(prompted, '合成提示词', '剪贴板被拒时应把提示词摆出来给黄总手动复制');
});
// ── fixes-outsourced-tasks-2：N1 提交字段 / L1 切类型清空 / L2 真实失败与注入 ──
async function submitForm(f) {
  f.d.querySelector('#editor').open = true;
  f.form.dispatchEvent(new f.w.Event('submit',{bubbles:true,cancelable:true}));
  await settle(); await settle();
  return f.postCalls().filter(call => call.url.endsWith('/save')).pop();
}
async function openNew(f) {
  f.d.querySelector('#new-task').click();
  await settle();
}

test('N1 外包任务提交时带上 slug 与 fallback',async t => {
  const f = await fixture(t,{tasks:[]});
  await openNew(f);
  change(f,'select[name="kind"]','outsourced');
  f.d.querySelector('[name="slug"]').value = 'github-trending-ai';
  f.d.querySelector('[name="fallback"]').value = 'pi';
  f.d.querySelector('[name="body"]').value = '抓 AI 仓库';
  f.d.querySelector('[name="cwd"]').value = '/Users/example/workbuddy-outbox/github-trending-ai';
  f.d.querySelector('[name="profile_id"]').value = 'fake';
  const call = await submitForm(f);
  assert.ok(call, '应发出保存请求');
  assert.equal(call.body.slug, 'github-trending-ai', 'slug 必须提交，否则后端收不建任务');
  assert.equal(call.body.fallback, 'pi');
});

test('N1 兜底方式选「不兜底」时提交 none',async t => {
  const f = await fixture(t,{tasks:[]});
  await openNew(f);
  change(f,'select[name="kind"]','outsourced');
  f.d.querySelector('[name="slug"]').value = 'github-trending-ai';
  f.d.querySelector('[name="fallback"]').value = 'none';
  f.d.querySelector('[name="body"]').value = '抓 AI 仓库';
  f.d.querySelector('[name="profile_id"]').value = 'fake';
  const call = await submitForm(f);
  assert.equal(call.body.fallback, 'none');
});

test('N1 兜底下拉只在选外包任务时出现',async t => {
  const f = await fixture(t,{tasks:[base]});
  assert.equal(f.d.querySelector('#outsourced-fallback-label').hidden, true);
  await openEditor(f);
  change(f,'select[name="kind"]','outsourced');
  await settle();
  assert.equal(f.d.querySelector('#outsourced-fallback-label').hidden, false);
  change(f,'select[name="kind"]','pi');
  await settle();
  assert.equal(f.d.querySelector('#outsourced-fallback-label').hidden, true);
});

test('N1 非外包任务提交时既不带 slug 也不带 fallback',async t => {
  const f = await fixture(t,{tasks:[]});
  await openNew(f);
  change(f,'select[name="kind"]','pi');
  f.d.querySelector('[name="body"]').value = '查一下进展';
  f.d.querySelector('[name="profile_id"]').value = 'fake';
  const call = await submitForm(f);
  assert.ok(call, '应发出保存请求');
  assert.ok(!('slug' in call.body), '非外包不该带 slug：%r' % call.body);
  assert.ok(!('fallback' in call.body), '非外包不该带 fallback：%r' % call.body);
});

test('L1 从外包切到别的类型会清掉 slug 的值',async t => {
  const f = await fixture(t,{tasks:[base]});
  await openEditor(f);
  assert.equal(f.d.querySelector('[name="slug"]').value, 'github-trending-ai', '先确认回填正确');
  change(f,'select[name="kind"]','pi');
  await settle();
  assert.equal(f.d.querySelector('[name="slug"]').value, '', '隐藏不等于清空，值必须也被抹掉');
  assert.equal(f.d.querySelector('[name="slug"]').required, false);
});

test('L1 切走再切回外包，slug 是空的需要自己重填',async t => {
  const f = await fixture(t,{tasks:[base]});
  await openEditor(f);
  change(f,'select[name="kind"]','pi');
  await settle();
  change(f,'select[name="kind"]','outsourced');
  await settle();
  assert.equal(f.d.querySelector('[name="slug"]').value, '');
  assert.equal(f.d.querySelector('[name="slug"]').required, true);
});

test('L1 编辑外包任务本身不会被自己的清空逻辑抹掉',async t => {
  const f = await fixture(t,{tasks:[base]});
  await openEditor(f);
  await settle();
  assert.equal(f.d.querySelector('[name="slug"]').value, 'github-trending-ai');
});

test('L2 复制提示词接口 HTTP 失败时报错，且不写剪贴板',async t => {
  const f = await fixture(t,{tasks:[{...base,last_run:run}],
    handler:async call => (call.url.endsWith('/outsourced_prompt')
      ? {ok:false,status:500,json:async () => ({error:'合成失败'})} : undefined)});
  f.d.querySelector('[data-action="outsourced-prompt"]').click();
  await settle(); await settle();
  assert.equal(f.copied.length, 0, '接口失败就不该写剪贴板');
  const shown = f.d.querySelector('#message').textContent + f.d.querySelector('#form-error').textContent;
  assert.match(shown, /失败|错误/, '要把失败说出来：%r' % shown);
});

test('L2 任务名与提示词里的 HTML 不会被当成结构执行',async t => {
  const nasty = '<img src=x onerror="window.__pwned=1">';
  const f = await fixture(t,{tasks:[{...base,name:nasty,last_run:{...run,output:'{}'}}],
    prompt:`<script>window.__pwned2=1</script>${nasty}`});
  const card = f.d.querySelector('#tasks');
  assert.equal(f.w.__pwned, undefined, '任务名里的标签不能变成真实元素');
  assert.equal(card.querySelector('img'), null, '卡片里不该冒出 img 元素');
  assert.match(card.textContent, /<img src=x/, '原文应该作为文本显示出来');
  f.d.querySelector('[data-action="outsourced-prompt"]').click();
  await settle(); await settle();
  assert.equal(f.w.__pwned2, undefined, '提示词里的 script 不能执行');
  assert.equal(f.copied.length, 1);
  assert.match(f.copied[0], /<script>/, '提示词按原文复制，不做 HTML 清洗');
});

// ── fixes-outsourced-tasks-3 / L2：把页面真正提交的那份 payload 交给后端 ──
// Python 侧不再用正则读 schedules.js，而是让这里在 jsdom 里真点一次提交，
// 把 fetch 实际收到的 body 原样写进 OT_CAPTURE_PAYLOAD 指定的 JSON 文件，
// 由 tests/test_outsourced_tasks.py 读回去送进 ScheduledTasks.validate()。
// 字段怎么拼、按类型带哪些字段，全由 schedules.js 自己决定，测试不替它补。
async function realSubmitBody(t, kind) {
  const f = await fixture(t, {tasks: []});
  await openNew(f);
  change(f, 'select[name="kind"]', kind);
  if (kind === 'outsourced') {
    f.d.querySelector('[name="slug"]').value = 'github-trending-ai';
    f.d.querySelector('[name="fallback"]').value = 'pi';
  }
  f.d.querySelector('[name="name"]').value = 'GitHub Trending AI 日报';
  f.d.querySelector('[name="body"]').value = '抓 AI 相关仓库';
  // cwd 给一个 Python 侧真实存在的临时目录，否则后端「工作目录必须是本机存在的绝对目录」会先拒收。
  f.d.querySelector('[name="cwd"]').value = process.env.OT_CAPTURE_CWD || '/Users/example/workbuddy-outbox/github-trending-ai';
  f.d.querySelector('[name="profile_id"]').value = 'fake';
  const call = await submitForm(f);
  assert.ok(call, `${kind} 提交应发出保存请求`);
  return call.body;
}

test('ot3-L2 外包提交：真实 payload 带 slug 与 fallback', async t => {
  const body = await realSubmitBody(t, 'outsourced');
  assert.equal(body.slug, 'github-trending-ai');
  assert.equal(body.fallback, 'pi');
  if (process.env.OT_CAPTURE_PAYLOAD) {
    fs.writeFileSync(process.env.OT_CAPTURE_PAYLOAD, JSON.stringify(body));
  }
});

test('ot3-L2 普通 Pi 提交：真实 payload 既不带 slug 也不带 fallback', async t => {
  const body = await realSubmitBody(t, 'pi');
  assert.ok(!('slug' in body), `pi 提交不该带 slug：${JSON.stringify(body)}`);
  assert.ok(!('fallback' in body), 'pi 提交不该带 fallback');
  if (process.env.OT_CAPTURE_PAYLOAD) {
    fs.writeFileSync(process.env.OT_CAPTURE_PAYLOAD + '.pi.json', JSON.stringify(body));
  }
});
