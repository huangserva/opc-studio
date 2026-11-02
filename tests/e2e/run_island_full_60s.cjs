'use strict';
const path = require('node:path');
const fs = require('node:fs');
const assert = require('node:assert/strict');
const pw = require(path.resolve(__dirname, 'node_modules/playwright'));

(async () => {
  console.log('🚀 启动隔离 Playwright 浏览器，实机执行【极客荒岛 4幕60s】全流程...');
  const browser = await pw.chromium.launch({
    channel: 'chrome',
    headless: true,
    args: ['--disable-background-networking', '--disable-component-update']
  });

  const ctx = await browser.newContext({
    viewport: { width: 1440, height: 1200 }
  });
  const page = await ctx.newPage();

  page.on('console', msg => {
    if (msg.type() === 'error' || msg.type() === 'warning') {
      console.log(`[Browser ${msg.type()}]`, msg.text());
    }
  });
  page.on('pageerror', err => {
    console.error('[Browser PageError]', err.message);
  });
  page.on('response', async res => {
    const url = res.url();
    if (url.includes('/api/generation/lab/start') || url.includes('/api/generation/lab/concat') || url.includes('/api/generation/reference-from-job')) {
      let bodyText = '';
      try { bodyText = await res.text(); } catch {}
      console.log(`[API ${res.status()}] ${url} -> ${bodyText.slice(0, 220)}`);
    }
  });

  console.log('🌐 1. 打开 http://127.0.0.1:8766/lab-test.html ...');
  await page.goto('http://127.0.0.1:8766/lab-test.html', { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('.act-card', { timeout: 15000 });
  // 等待 refreshLab 初始化完成
  await page.waitForFunction(() => typeof lab !== 'undefined' && lab.initialized === true, {}, { timeout: 15000 });

  console.log('🏝️ 2. 点击选择【极客荒岛 (4幕60s·实测验证)】预设...');
  await page.click('.drama-preset-btn[data-preset="island"]');
  await page.click('#drama-reset-btn');

  console.log('🎯 3. 点击【依据当前剧情自动生成主角定妆照 (Qwen 2.1)】...');
  await page.click('#seq-auto-cast-btn');

  // 等待定妆完成
  const castStart = Date.now();
  while (true) {
    await page.waitForTimeout(3000);
    const statusText = await page.textContent('#seq-director-status');
    const elapsed = Math.round((Date.now() - castStart) / 1000);
    console.log(`   [定妆进度 ${elapsed}s] ${statusText.trim()}`);
    if (statusText.includes('主角已定妆')) {
      console.log('✅ 主角定妆照生成并自动绑定 <Picture 1> 成功！');
      break;
    }
    if (statusText.includes('定妆失败') || elapsed > 240) {
      throw new Error('定妆失败或超时: ' + statusText);
    }
  }

  // 确认 <Picture 1> 已挂载
  const refTiles = await page.$$('#lab-video-refs .video-reference-tile');
  console.log(`   当前已挂载参考图数量: ${refTiles.length}`);
  assert.ok(refTiles.length >= 1, '应至少挂载 1 张主角定妆参考图 <Picture 1>');

  // 依次点击生成第 1 ~ 4 幕
  for (let actIdx = 0; actIdx < 4; actIdx++) {
    console.log(`\n🎬 4.${actIdx + 1} 点击生成【第 ${actIdx + 1} 幕 (15s)】...`);
    const btnSelector = `.act-card[data-act-index="${actIdx}"] .act-render-btn`;
    await page.waitForSelector(btnSelector + ':not([disabled])', { timeout: 30000 });
    await page.click(btnSelector);

    const actStart = Date.now();
    while (true) {
      await page.waitForTimeout(4000);
      const badgeText = await page.textContent(`.act-card[data-act-index="${actIdx}"] .act-status-badge`);
      const toastText = await page.textContent('#toast');
      const elapsed = Math.round((Date.now() - actStart) / 1000);
      console.log(`   [Act ${actIdx + 1} · ${elapsed}s] 状态: ${badgeText.trim()} | 提示: ${toastText.trim()}`);
      if (badgeText.includes('已就绪')) {
        console.log(`✅ 第 ${actIdx + 1} 幕 (15s) 渲染完成！耗时约 ${elapsed}s`);
        break;
      }
      if (badgeText.includes('失败') || elapsed > 300) {
        throw new Error(`第 ${actIdx + 1} 幕渲染失败或超时: ${badgeText} / ${toastText}`);
      }
    }
  }

  console.log('\n🎞️ 5. 4 幕全部就绪！点击【一键合并导出 60 秒完整短剧】...');
  await page.waitForSelector('#drama-concat-btn:not([disabled])', { timeout: 15000 });
  await page.click('#drama-concat-btn');

  await page.waitForTimeout(3000);
  const selectedJob = await page.evaluate(() => typeof lab !== 'undefined' ? lab.selected : null);
  console.log('🎉 合并完成！当前右侧大屏选中任务:', JSON.stringify({
    id: selectedJob?.id,
    status: selectedJob?.status,
    prompt: selectedJob?.prompt,
    settings: selectedJob?.settings
  }));
  assert.ok(selectedJob && selectedJob.id, '应生成并选中 60 秒合并短剧任务');
  assert.equal(selectedJob.settings?.workflow, 'concat', '任务类型应为 concat 合并短剧');

  const evidenceDir = path.resolve('coordination/evidence');
  fs.mkdirSync(evidenceDir, { recursive: true });
  const screenshotPath = path.join(evidenceDir, 'island-60s-complete.png');
  await page.screenshot({ path: screenshotPath, fullPage: true });
  console.log('📸 完整 4 幕 + 60s 成片全屏截图已保存:', screenshotPath);

  await browser.close();
  console.log('🏆【极客荒岛 4幕60s】端到端实机生成与合体全流程圆满完成！');
})().catch(err => {
  console.error('❌ 执行中止:', err);
  process.exit(1);
});
