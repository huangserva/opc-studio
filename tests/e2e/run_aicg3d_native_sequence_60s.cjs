const { chromium } = require('playwright');
const path = require('path');

(async () => {
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({ viewport: { width: 1520, height: 1080 } });
  const page = await context.newPage();

  page.on('console', msg => {
    if (msg.type() === 'error' || msg.text().includes('AICG3D') || msg.text().includes('无限流')) {
      console.log(`[Browser Console] ${msg.type()}: ${msg.text()}`);
    }
  });

  console.log('1. 打开 http://127.0.0.1:8766/lab-test.html ...');
  await page.goto('http://127.0.0.1:8766/lab-test.html', { waitUntil: 'networkidle' });
  await page.waitForTimeout(2000);

  console.log('2. 点击选择「极客荒岛」预设并验证前端可配置控件...');
  const islandBtn = page.locator('.drama-preset-btn[data-preset="island"]');
  await islandBtn.click();
  await page.waitForTimeout(800);

  // 检查是否已挂载主角定妆照，若未挂载则自动挂载最新定妆照
  const attachedInfo = await page.evaluate(async () => {
    const valid = (typeof videoInputs !== 'undefined' && videoInputs.images)
      ? videoInputs.images.filter(i => !i.error && !i.removed)
      : [];
    if (valid.length > 0) {
      return { alreadyAttached: true, id: valid[0].id };
    }
    const imgData = await api('/api/generation/lab?kind=image&scope=all&trash=0');
    const doneImgs = (imgData?.jobs || []).filter(j => j.status === 'done' && !j.deleted_at);
    const latestImg = doneImgs.find(j => (j.prompt || '').includes('定妆照')) || doneImgs.find(j => j.origin === 'lab') || doneImgs[0];
    if (latestImg && typeof safeAttachVideoGalleryImage === 'function') {
      const ref = await safeAttachVideoGalleryImage('images', latestImg.id);
      return { alreadyAttached: false, attachedFromJob: latestImg.id, prompt: latestImg.prompt?.slice(0, 40), id: ref?.id };
    }
    return { error: 'no_image_found' };
  });
  console.log('3. 主角参考图 (<Picture 1>) 状态:', JSON.stringify(attachedInfo));

  // 在界面上显式选择方案 B：hybrid + none (25步) + ref_image_size='1k' + 5s/幕
  await page.selectOption('#lab-workflow', 'r2v');
  await page.selectOption('#lab-checkpoint', 'hybrid');
  await page.selectOption('#lab-accelerator', 'none');
  await page.selectOption('#lab-ref-image-size', '1k');
  await page.selectOption('#lab-seq-seg-seconds', '5');
  await page.waitForTimeout(500);

  const uiConfig = await page.evaluate(() => ({
    workflow: document.querySelector('#lab-workflow')?.value,
    checkpoint: document.querySelector('#lab-checkpoint')?.value,
    accelerator: document.querySelector('#lab-accelerator')?.value,
    refImageSize: document.querySelector('#lab-ref-image-size')?.value,
    refImageOptions: [...(document.querySelector('#lab-ref-image-size')?.options || [])].map(o => o.value),
    seqSegSeconds: document.querySelector('#lab-seq-seg-seconds')?.value,
    statusBanner: document.querySelector('#drama-pipeline-status')?.innerText
  }));
  console.log('4. 页面方案 B 控件状态:', JSON.stringify(uiConfig, null, 2));

  const prevJobId = await page.evaluate(() => (typeof lab !== 'undefined' && lab.selected) ? lab.selected.id : null);
  console.log('5. 点击「⚡ AICG3D 无限流」按钮启动方案 B 实机生成 (前序任务 ID:', prevJobId, ')...');
  const runAllBtn = page.locator('#drama-run-all-btn');
  await runAllBtn.click();

  let lastLog = '';
  const startTime = Date.now();
  for (let i = 0; i < 1600; i++) {
    await page.waitForTimeout(3000);
    const state = await page.evaluate(() => {
      const statusText = document.querySelector('#drama-pipeline-status')?.innerText || '';
      const job = (typeof lab !== 'undefined' && lab.selected) ? {
        id: lab.selected.id,
        status: lab.selected.status,
        error: lab.selected.error,
        progress: lab.selected.progress,
        engine: lab.selected.settings?.engine,
        checkpoint: lab.selected.settings?.video_checkpoint,
        accelerator: lab.selected.settings?.video_accelerator,
        steps: lab.selected.settings?.steps,
        ref_image_size: lab.selected.settings?.video_ref_image_size,
        resolution: lab.selected.settings?.sequence_resolution,
        frames: lab.selected.settings?.frames,
        seconds: lab.selected.settings?.seconds,
        segmentCount: lab.selected.settings?.segments?.length
      } : null;
      const acts = (typeof actsState !== 'undefined')
        ? actsState.map(a => ({ id: a.id, status: a.status, elapsed: a.elapsed }))
        : [];
      return { statusText, job, acts };
    });

    const summary = `[${Math.round((Date.now() - startTime) / 1000)}s] Status="${state.statusText}" | Job=${state.job?.id?.slice(0, 8)}(${state.job?.status}, cp=${state.job?.checkpoint}, acc=${state.job?.accelerator}/${state.job?.steps}步, ref=${state.job?.ref_image_size}, segs=${state.job?.segmentCount || '-'}, sec=${state.job?.seconds || '-'}s, node=${state.job?.progress?.node || '-'}, label=${state.job?.progress?.label || '-'}, pct=${state.job?.progress?.percent ?? '-'}%) | Acts=${state.acts.map(a => `A${a.id}:${a.status}`).join(',')}`;
    if (summary !== lastLog) {
      console.log(summary);
      lastLog = summary;
    }

    if (state.job && state.job.id !== prevJobId && (state.job.status === 'done' || state.job.status === 'error' || state.job.status === 'cancelled')) {
      console.log('6. 方案 B 60s 新任务进入终态:', JSON.stringify(state.job, null, 2));
      break;
    }
  }

  await page.waitForTimeout(3000);
  const screenshotPath = path.resolve(__dirname, '../../coordination/evidence/aicg3d-plan-b-60s-complete.png');
  await page.screenshot({ path: screenshotPath, fullPage: true });
  console.log('7. 验收截图已保存至:', screenshotPath);

  await browser.close();
})();
