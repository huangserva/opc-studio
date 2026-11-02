const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {JSDOM} = require('jsdom');

const root = path.join(__dirname, '..');
const html = fs.readFileSync(path.join(root, 'public/lab-test.html'), 'utf8');

const scriptNames = ['lab-core.js', 'media-progress.js', 'media-size.js', 'media-lora.js', 'lab-video.js', 'lab-management.js', 'lab-test.js'];
const scripts = scriptNames.map(name => ({
  name,
  source: fs.readFileSync(path.join(root, 'public', name), 'utf8').replace(/\nrefreshLab\(true\);\s*$/, '\n')
}));

function setupDom(t) {
  const dom = new JSDOM(html, {
    url: 'http://127.0.0.1:8766/lab-test.html',
    runScripts: 'outside-only'
  });
  t.after(() => dom.window.close());
  const w = dom.window;
  const context = dom.getInternalVMContext();

  context.document = w.document;
  context.window = w;
  context.$ = selector => w.document.querySelector(selector);
  context.setTimeout = (fn, ms) => {
    if (fn === context.refreshLab || String(fn).includes('refreshLab')) return 0;
    return setTimeout(fn, 0);
  };
  context.clearTimeout = () => {};
  context.setInterval = () => 0;
  context.clearInterval = () => {};
  w.setTimeout = context.setTimeout;
  w.clearTimeout = context.clearTimeout;
  w.setInterval = context.setInterval;
  w.clearInterval = context.clearInterval;

  const fetchCalls = [];
  const fetchMock = async (url, options = {}) => {
    fetchCalls.push({ url, options });
    const method = options.method || 'GET';
    const body = options.body ? JSON.parse(options.body) : {};

    if (url.includes('/api/generation/lab?')) {
      return {
        ok: true,
        json: async () => ({
          models: [{ id: 'h3', name: 'MiniMax H3', kind: 'video', sizes: ['768x448', '448x768'] }],
          jobs: [],
          busy: false,
          preferences: { video: {} },
          image_skill: { styles: [] }
        })
      };
    }
    if (url.includes('/api/generation/lab/start')) {
      return {
        ok: true,
        status: 202,
        json: async () => ({
          id: 'test_job_' + Math.random().toString(36).slice(2, 10),
          status: 'running',
          model: body.model,
          prompt: body.prompt,
          kind: 'video',
          settings: { frames: body.frames, steps: body.steps }
        })
      };
    }
    if (url.includes('/api/generation/job/')) {
      const jobId = url.split('/').pop();
      return {
        ok: true,
        json: async () => ({
          id: jobId,
          status: 'done',
          kind: 'video',
          asset_file: 'result.mp4',
          settings: { frames: 345, steps: 4 }
        })
      };
    }
    if (url.includes('/api/generation/lab/concat')) {
      return {
        ok: true,
        status: 201,
        json: async () => ({
          id: 'merged_60s_job_' + Math.random().toString(36).slice(2, 8),
          status: 'done',
          kind: 'video',
          prompt: body.title,
          asset_file: 'result.mp4',
          settings: { workflow: 'concat', source_job_ids: body.job_ids }
        })
      };
    }
    return { ok: true, json: async () => ({}) };
  };
  w.fetch = fetchMock;
  context.fetch = fetchMock;

  w.renderMediaProgress = () => {};
  w.attachMediaProgressHost = () => {};
  w.markMediaProgressUnavailable = () => {};
  w.configureVideo = () => {};
  w.renderMediaSize = () => {};
  w.renderLoraFields = () => {};
  w.selectedMediaFrames = () => 345;
  w.selectedMediaSize = () => '768x448';
  w.selectedLoraParameters = () => ({});
  w.videoParameters = () => ({ workflow: 't2v', references: {}, video_loras: [] });
  w.updateCustomSize = () => {};
  w.updateLoraEnabled = () => {};

  for (const s of scripts) {
    vm.runInContext(s.source, context, { filename: s.name });
  }

  context.toast = (msg) => {
    const el = w.document.getElementById('toast');
    if (el) el.textContent = msg;
  };
  w.toast = context.toast;

  context.refreshLab = async () => {};
  w.refreshLab = context.refreshLab;

  vm.runInContext('initSequenceControls();', context);

  return { w, dom, context, fetchCalls };
}

test('4 幕导演台：初始载入 4 幕卡片且包含默认极客荒岛剧本', async t => {
  const { w } = setupDom(t);
  const container = w.document.getElementById('act-cards-container');
  assert.ok(container, '应存在 act-cards-container 容器');
  
  const cards = container.querySelectorAll('.act-card');
  assert.equal(cards.length, 4, '应有 4 幕独立卡片 (Act 1 ~ 4)');

  const act1Badge = cards[0].querySelector('.act-badge');
  assert.equal(act1Badge.textContent.trim(), 'Act 1');

  const act1Prompt = cards[0].querySelector('.act-prompt-input');
  assert.ok(act1Prompt.value.includes('AI Island Live'), '第 1 幕应预置荒岛直播剧本');
});

test('4 幕导演台：切换题材预设（赛博追杀）即时同步 4 幕剧本', async t => {
  const { w } = setupDom(t);
  const cyberpunkBtn = w.document.querySelector('.drama-preset-btn[data-preset="cyberpunk"]');
  assert.ok(cyberpunkBtn, '应存在赛博追杀预设按钮');

  cyberpunkBtn.click();

  const cards = w.document.querySelectorAll('#act-cards-container .act-card');
  const act1Prompt = cards[0].querySelector('.act-prompt-input').value;
  assert.ok(act1Prompt.includes('cyberpunk') || act1Prompt.includes('tactical trench coat'), '第 1 幕应切换为赛博朋克剧本');

  const act2Prompt = cards[1].querySelector('.act-prompt-input').value;
  assert.ok(act2Prompt.includes('maglev') || act2Prompt.includes('enforcers'), '第 2 幕应切换为突袭追杀剧本');
});

test('4 幕导演台：生成单幕 (15s) 采用 Singularity + Turbo 4步 + 345帧参数', async t => {
  const { w, context, fetchCalls } = setupDom(t);
  const renderBtn1 = w.document.querySelector('.act-card[data-act-index="0"] .act-render-btn');
  assert.ok(renderBtn1, '第 1 幕应有生成按钮');

  await vm.runInContext('generateSingleAct(0)', context);

  const startCall = fetchCalls.find(c => c.url.includes('/api/generation/lab/start'));
  assert.ok(startCall, '应向 /api/generation/lab/start 发起请求');
  const payload = JSON.parse(startCall.options.body);
  assert.equal(payload.model, 'h3');
  assert.equal(payload.video_checkpoint, 'singularity');
  assert.equal(payload.video_accelerator, 'ref4');
  assert.equal(payload.frames, 345, '时长应锁定 345 帧 (14.4~15秒)');
  assert.equal(payload.steps, undefined, 'H3 步数由 ref4 自动决定，不应直接传 steps 参数');
});

test('4 幕导演台：至少 2 幕完成即可启用合并短剧按钮，点击调用 /api/generation/lab/concat', async t => {
  const { w, context, fetchCalls } = setupDom(t);
  const concatBtn = w.document.getElementById('drama-concat-btn');
  assert.ok(concatBtn, '应存在合并按钮');
  assert.ok(concatBtn.disabled, '初始未渲染时合并按钮应禁用');

  // 模拟完成 Act 1 与 Act 2
  vm.runInContext(`
    actsState[0].status = 'done';
    actsState[0].job = { id: 'job_act1', status: 'done', kind: 'video' };
    actsState[1].status = 'done';
    actsState[1].job = { id: 'job_act2', status: 'done', kind: 'video' };
    renderActCards();
  `, context);

  assert.equal(concatBtn.disabled, false, '完成 2 幕后合并按钮应启用');

  await vm.runInContext('concatFullDrama()', context);

  const concatCall = fetchCalls.find(c => c.url.includes('/api/generation/lab/concat'));
  assert.ok(concatCall, '应向 /api/generation/lab/concat 发起合并请求');
  const body = JSON.parse(concatCall.options.body);
  assert.deepEqual(body.job_ids, ['job_act1', 'job_act2'], '应传入已完成的幕任务编号');
});
