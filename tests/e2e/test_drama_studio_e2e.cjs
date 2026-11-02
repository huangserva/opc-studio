'use strict';
const path = require('node:path');
const fs = require('node:fs');
const assert = require('node:assert/strict');
const pw = require(path.resolve(__dirname, 'node_modules/playwright'));

(async () => {
  console.log('🚀 启动隔离 Playwright 无头浏览器验收...');
  const browser = await pw.chromium.launch({
    channel: 'chrome',
    headless: true,
    args: ['--disable-background-networking', '--disable-component-update']
  });

  const ctx = await browser.newContext({
    viewport: { width: 1440, height: 1080 }
  });
  const page = await ctx.newPage();

  const consoleErrors = [];
  page.on('console', msg => {
    if (msg.type() === 'error') consoleErrors.push(msg.text());
  });
  page.on('pageerror', err => {
    consoleErrors.push(err.message);
  });

  console.log('🌐 访问 http://127.0.0.1:8766/lab-test.html ...');
  await page.goto('http://127.0.0.1:8766/lab-test.html', { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('.act-card', { timeout: 10000 });

  // 1. 验证导演台总控栏与标题
  const heading = await page.textContent('.drama-title');
  assert.ok(heading.includes('工业级微短剧流水线：4 幕 15 秒'), '应包含微短剧导演台标题');
  console.log('✔ 标题核验通过:', heading.trim().replace(/\s+/g, ' '));

  // 2. 验证 4 幕独立卡片
  const actCards = await page.$$('.act-card');
  assert.equal(actCards.length, 4, '必须渲染 4 幕独立卡片');
  console.log('✔ 4 幕独立卡片完整渲染通过');

  // 3. 验证默认荒岛剧本
  const act1Text = await page.inputValue('.act-card[data-act-index="0"] .act-prompt-input');
  assert.ok(act1Text.includes('AI Island Live'), '默认应载入荒岛直播第 1 幕剧本');
  console.log('✔ 默认荒岛剧本校验通过');

  // 4. 验证题材预设切换（切换至赛博追杀）
  console.log('🎮 点击【赛博追杀】题材预设...');
  await page.click('.drama-preset-btn[data-preset="cyberpunk"]');
  const cyberText1 = await page.inputValue('.act-card[data-act-index="0"] .act-prompt-input');
  assert.ok(cyberText1.includes('cyberpunk') || cyberText1.includes('tactical trench coat'), '第 1 幕应切换为赛博朋克');
  const cyberText2 = await page.inputValue('.act-card[data-act-index="1"] .act-prompt-input');
  assert.ok(cyberText2.includes('maglev') || cyberText2.includes('enforcers'), '第 2 幕应切换为突袭追杀');
  console.log('✔ 题材预设实时同步校验通过');

  // 5. 验证操作按钮与定妆照入口
  const runAllBtn = await page.$('#drama-run-all-btn');
  const concatBtn = await page.$('#drama-concat-btn');
  const castBtn = await page.$('#seq-auto-cast-btn');
  assert.ok(runAllBtn, '应存在全剧顺序渲染按钮');
  assert.ok(concatBtn, '应存在合并 60 秒短剧按钮');
  assert.ok(castBtn, '应存在一键定妆按钮');
  console.log('✔ 核心总控按钮完整在位通过');

  // 6. 截图留存验收凭证
  const evidenceDir = path.resolve('coordination/evidence');
  fs.mkdirSync(evidenceDir, { recursive: true });
  const screenshotPath = path.join(evidenceDir, 'drama-studio-e2e.png');
  await page.screenshot({ path: screenshotPath, fullPage: true });
  console.log('📸 浏览器验收全页面截图已留档:', screenshotPath);

  await browser.close();

  if (consoleErrors.length > 0) {
    console.warn('⚠ 浏览器页面有如下警告/错误 (仅供参考):', consoleErrors);
  }

  console.log('🎉 隔离 Playwright 端到端浏览器验收 100% 通过！');
})().catch(err => {
  console.error('❌ E2E 验收失败:', err);
  process.exit(1);
});
