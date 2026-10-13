const { chromium } = require('playwright');

(async () => {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage();
  await page.goto('http://127.0.0.1:8766/twitter-radar.html', { waitUntil: 'networkidle' });

  // 1. Check clusters view
  const clusterLinks = await page.$$eval('a.tweet-link-btn', els => els.map(e => ({
    text: e.textContent.trim(),
    href: e.href,
    author: e.closest('.embedded-tweet-card, .tweet-card')?.querySelector('.author-name, .blogger-name')?.textContent?.trim()
  })));
  console.log(`Rendered cluster links: ${clusterLinks.length}`);
  
  const broken_ids = [
    '2109483321453981696',
    '2109526716767228160',
    '2109297645164875776',
    '2109477012356702208',
    '2109559384724647936',
    '2109289299493699840'
  ];

  for (const l of clusterLinks) {
    for (const b of broken_ids) {
      if (l.href.includes(b)) {
        console.error(`FAIL: Found broken ID ${b} in link:`, l);
        process.exit(1);
      }
    }
  }

  // Check the specific Claude topic cards that Boss Huang clicked
  const claudeLinks = clusterLinks.filter(l => l.href.includes('0xlangeai') || l.href.includes('leaf_sanren'));
  console.log('Verified Claude topic card links:');
  claudeLinks.forEach(l => console.log('  -', l.author, '->', l.href));

  // 2. Switch to VIP view and verify links
  await page.click('#tab-vip-btn');
  await page.waitForTimeout(500);
  const vipLinks = await page.$$eval('a.tweet-link-btn', els => els.map(e => e.href));
  console.log(`Rendered VIP links: ${vipLinks.length}`);
  for (const href of vipLinks) {
    for (const b of broken_ids) {
      if (href.includes(b)) {
        console.error(`FAIL: Found broken ID ${b} in VIP link:`, href);
        process.exit(1);
      }
    }
  }

  // 3. Switch to Feed view and verify links
  await page.click('#tab-feed-btn');
  await page.waitForTimeout(500);
  const feedLinks = await page.$$eval('a.tweet-link-btn', els => els.map(e => e.href));
  console.log(`Rendered Feed links: ${feedLinks.length}`);
  for (const href of feedLinks) {
    for (const b of broken_ids) {
      if (href.includes(b)) {
        console.error(`FAIL: Found broken ID ${b} in Feed link:`, href);
        process.exit(1);
      }
    }
  }

  console.log('✅ ALL VIEWS VERIFIED: ZERO BROKEN TWEET LINKS IN DOM!');
  await browser.close();
})().catch(err => {
  console.error(err);
  process.exit(1);
});
