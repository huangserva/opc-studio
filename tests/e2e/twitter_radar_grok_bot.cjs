'use strict';
// 独立 /tmp profile，夹具模式随机端口；--real 验证重启后的 8766，反馈在 finally 恢复。
const fs = require('fs');
const os = require('os');
const path = require('path');
const assert = require('assert/strict');
const {spawn, spawnSync} = require('child_process');
const {chromium} = (() => {try {return require('playwright');} catch (_) {return require(path.join(process.env.HOME,'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright'));}})();
const REPO = path.resolve(__dirname,'../..');
const PY = path.resolve(REPO,'../article-importer/.venv/bin/python');
const REAL = process.argv.includes('--real');
const temp = fs.mkdtempSync(path.join('/tmp','grok-radar-e2e-'));
const evidence = REAL ? path.join(REPO,'coordination') : temp;
const results = [];
let server, context, base, reports, workspace, bundleFile, selectedId, originalFeedback;
const long = '第一行😀 <script>alert(1)</script> &amp; <b>原文</b>\n\n' + '完整长文' .repeat(600);
const iso = h => new Date(Date.now()-h*3600000).toISOString();
function check(name, condition, detail='') {assert.ok(condition, name+': '+detail); results.push({name, passed:true, detail}); console.log('通过 '+name+(detail?' '+detail:''));}
function writeReport(file, posts, when=iso(0.2)) {const out=path.join(reports,file);fs.mkdirSync(path.dirname(out),{recursive:true});fs.writeFileSync(out, JSON.stringify({collected_at:when, [file.startsWith('alerts')?'entries':'posts']:posts}));}
function row(id, extra={}) {return {id,handle:'fixture',author:'夹具作者',text:'AI Agent 完整原文',created_at:iso(2),url:'https://x.com/fixture/status/'+id,followers:20000,label:'爆款',topic:'Agent与Coding',list:'工具工作流',views:8202,likes:50,bookmarks:111,retweets:8,replies:4,views_per_hour:1953.71,hours_since_created:4.2,why:'值得学习的原因',pattern:'可复制的写法',...extra};}
function feedbackPaths() {return [path.join(workspace,'twitter-radar/grok-bot/feedback.json'),path.join(reports,'feedback/studio-feedback.json')];}
function snapshotFeedback() {return feedbackPaths().map(p=>({path:p,exists:fs.existsSync(p),raw:fs.existsSync(p)?fs.readFileSync(p):null}));}
function restoreFeedback() {
  if (!originalFeedback || !selectedId) return;
  for (const before of originalFeedback) {
    const current = fs.existsSync(before.path)?JSON.parse(fs.readFileSync(before.path,'utf8')):{};
    const old = before.raw?JSON.parse(before.raw):{};
    if (selectedId in old) current[selectedId]=old[selectedId]; else delete current[selectedId];
    if (!before.exists && Object.keys(current).length===0) {try{fs.unlinkSync(before.path);}catch(_){}continue;}
    const raw = JSON.stringify(current) === JSON.stringify(old) && before.raw ? before.raw : Buffer.from(JSON.stringify(current,null,2)+'\n');
    fs.mkdirSync(path.dirname(before.path),{recursive:true});const tmp=before.path+'.e2e-'+process.pid;fs.writeFileSync(tmp,raw);fs.renameSync(tmp,before.path);
  }
}
async function start() {
  if (REAL) {base='http://127.0.0.1:8766';workspace=path.join(REPO,'workspace');reports=path.join(os.homedir(),'grok-bot-reports');return;}
  workspace=path.join(temp,'workspace');reports=path.join(temp,'reports');
  fs.mkdirSync(workspace,{recursive:true});const publicDir=path.join(temp,'public');fs.mkdirSync(publicDir);
  for(const name of fs.readdirSync(path.join(REPO,'public'))) if(/\.(html|js|css|svg)$/.test(name)) fs.copyFileSync(path.join(REPO,'public',name),path.join(publicDir,name));
  bundleFile=path.join(publicDir,'twitter-radar-bundle.json');
  fs.writeFileSync(bundleFile,JSON.stringify({updated_at:iso(.1),run_id:'grok-fixture',coverage:{target:1,success:1,failed:0,complete:true},today_virals:1,h48_virals:1,vip_bloggers:[],tweets:[],topic_clusters:[],vip_tweets:[{id:'101',tweet_id:'101',user:'fixture',name:'夹具作者',fc:20000,text:'AIsa 摘要',views:100000,likes:500,track:'claude_code',published_at:iso(2),fetched_at:iso(1),url:'https://x.com/fixture/status/101'}]}));
  const rich = row('101',{text:long,full_text:long,article:{title:'长文标题',body:long},thread:[{author:'串推作者',handle:'fixture',text:'后续串推全文😀',url:'https://x.com/fixture/status/777'}],quoted:{author:'引用作者',handle:'other',text:'被引用帖完整正文',url:'https://x.com/other/status/888'},media:[{type:'photo',url:'https://images.example/a.png'},{type:'video',poster:'https://images.example/cover.jpg',url:'https://images.example/video.mp4'}],card:{title:'外链标题',description:'外链描述',url:'https://example.com/article',image:'https://images.example/card.png'}});
  writeReport('daily/a.json',[rich,row('102',{followers:2855,text:'别学 subagent 了，现在都玩单 Agent + Main Loop 了。'}),...['javascript:alert(1)','http://evil.com/x.com/status/1','https://x.com.evil.com/a/status/1',''].map((url,i)=>row(String(103+i),{url})),row('107',{label:'观察',text:'这是 AI 观察帖'}),row('108',{created_at:iso(49)}),row('109',{text:'AI 量化 合约'}),row('111',{label:'低粉爆款',followers:20000})]);
  writeReport('alerts/b.json',[row('101',{text:long,full_text:long,views:9000,bookmarks:333,...Object.fromEntries(['article','thread','quoted','media','card'].map(k=>[k,rich[k]]))})],iso(.1));
  const runner = `import sys; from pathlib import Path; sys.path.insert(0, ${JSON.stringify(REPO)}); import server, grok_bot_radar; server.ROOT=Path(${JSON.stringify(temp)}); grok_bot_radar.BUNDLE_PATH=Path(${JSON.stringify(bundleFile)}); sys.argv=['server.py','--workspace',${JSON.stringify(workspace)},'--port',sys.argv[1]]; server.main()`;
  const net=require('net');const port=await new Promise(resolve=>{const s=net.createServer().listen(0,'127.0.0.1',()=>{const p=s.address().port;s.close(()=>resolve(p));});});
  base='http://127.0.0.1:'+port;
  server=spawn(PY,['-u','-c',runner,String(port)],{cwd:REPO,env:{...process.env,GROK_BOT_REPORTS_DIR:reports,ARTICLE_STUDIO_MODEL_CONFIG:path.join(temp,'absent.json')},stdio:['ignore','pipe','pipe']});
  let log='';server.stdout.on('data',b=>log+=b);server.stderr.on('data',b=>log+=b);
  for(let i=0;i<200;i++){try{if((await fetch(base+'/api/radar/grok-bot')).ok)return;}catch(_){}if(server.exitCode!==null)throw new Error(log);await new Promise(r=>setTimeout(r,100));}
  throw new Error('启动超时 '+log.slice(-2000));
}
async function getData() {const response=await context.request.get(base+'/api/radar/grok-bot');assert.equal(response.status(),200);return response.json();}
(async()=>{
  try {
    await start();
    context=await chromium.launchPersistentContext(path.join(temp,'profile'),{channel:'chrome',headless:true,viewport:{width:1440,height:1100},args:['--disable-background-networking','--disable-component-update']});
    const errors=[];
    await context.route('**/*',r=>new URL(r.request().url()).origin===base?r.continue():(r.request().isNavigationRequest()?r.fulfill({status:200,contentType:'text/html',body:'<title>原帖地址验收</title>'}):r.abort()));
    const page=await context.newPage();page.on('pageerror',e=>errors.push(String(e)));
    await page.goto(base+'/twitter-radar.html');
    await page.waitForFunction(()=>!grokState.loading && DATA_SOURCE.kind==='json');
    check('仅保留原有四标签',await page.locator('.mode-tab-btn').count()===4 && await page.locator('#tab-grok-btn').count()===0);
    const data=await getData();
    await page.click('#tab-vip-btn');
    if(!REAL){
      const card=page.locator('#vip-tweets-feed .vip-tweet-card[data-tweet-id="101"]');
      check('反例11：双来源仅一张卡',await card.count()===1 && await card.locator('.radar-source-badge').allTextContents().then(a=>a.join(',')==='AIsa,Grok'));
      check('反例1：长原文逐字保真',await card.locator('.tweet-text').textContent()===long);
      check('脚本与标签作为纯文本',await page.evaluate(()=>typeof window.e2eInjected==='undefined') && await card.locator('.tweet-text script,.tweet-text b').count()===0);
      check('数字取最新',await card.locator('.vip-metrics-bar').innerText().then(t=>t.includes('9.0k')));
      check('长文串推引用完整',await card.locator('.grok-article .grok-rich-text').first().textContent()===long && await card.locator('.grok-thread').innerText().then(t=>t.includes('后续串推全文😀')) && await card.locator('.grok-quote').innerText().then(t=>t.includes('被引用帖完整正文')));
      check('图片视频封面与外链',await card.locator('.grok-media img').count()===2 && await card.getByText('播放原帖',{exact:true}).count()===1 && await card.locator('.grok-external-card').innerText().then(t=>t.includes('外链标题')));
      for(const id of ['103','104','105','106']) check('反例2：危险原帖链接 '+id,await page.locator('#vip-tweets-feed .vip-tweet-card[data-tweet-id="'+id+'"] .tweet-top a').count()===0);
      check('超期和噪声内容不进入榜单',await page.locator('#vip-tweets-feed .vip-tweet-card[data-tweet-id="108"],#vip-tweets-feed .vip-tweet-card[data-tweet-id="109"]').count()===0);
      await page.click('#tab-feed-btn');
      check('反例12：短正文高收藏明确提示',await page.locator('#tweets-feed .tweet-card[data-tweet-id="102"] .grok-incomplete').textContent()==='原文不完整，点开原帖看全文');
      check('低粉爆款标签按合同分榜',await page.locator('#tweets-feed .tweet-card[data-tweet-id="111"]').count()===1);
      const low=page.locator('#tweets-feed .tweet-card[data-tweet-id="102"]');
      check('反馈只有一组且三选项',await low.locator('.verdict-group').count()===1 && await low.locator('.verdict-btn').count()===3);
      for(const headers of [{'X-Article-Studio':'1'},{Origin:'http://evil.com','X-Article-Studio':'1'},{Origin:base}]) {
        const response=await context.request.post(base+'/api/radar/grok-bot/feedback',{headers,data:{tweet_id:'102',verdict:'worth'}});check('反例8：缺失或错误来源拒绝',response.status()===403);
      }
      check('未授权反馈不落盘',feedbackPaths().every(p=>!fs.existsSync(p)));
      const responses=await Promise.all(['101','102'].map(tweet_id=>context.request.post(base+'/api/radar/grok-bot/feedback',{headers:{Origin:base,'X-Article-Studio':'1'},data:{tweet_id,verdict:'worth'}})));
      check('反例7：并发 HTTP 反馈无丢失',responses.every(r=>r.status()===200) && Object.keys(JSON.parse(fs.readFileSync(feedbackPaths()[0]))).length===2 && fs.readFileSync(feedbackPaths()[0],'utf8')===fs.readFileSync(feedbackPaths()[1],'utf8'));
      await page.reload();await page.waitForFunction(()=>!grokState.loading && DATA_SOURCE.kind==='json');await page.click('#tab-feed-btn');
      check('反馈刷新保持',await page.locator('#tweets-feed .tweet-card[data-tweet-id="102"] .verdict-worth').getAttribute('aria-pressed')==='true');
      await page.locator('#tweets-feed .tweet-card[data-tweet-id="102"] .verdict-used').click();await page.waitForFunction(()=>grokState.feedback['102']?.verdict==='used');
      check('反馈可以改选',JSON.parse(fs.readFileSync(feedbackPaths()[1]))['102'].verdict==='used');
      writeReport('alerts/new.json',[row('110')]);fs.writeFileSync(path.join(reports,'daily/partial.json'),'{"posts":[');
      const hot=await getData();check('反例9：新报告热加载与半写不报错',hot.posts.some(p=>p.id==='110') && hot.bad_files===1);
      await page.click('#btn-refresh-radar');await page.waitForFunction(()=>grokState.posts.some(p=>p.id==='110'));
      await page.click('#tab-vip-btn');check('刷新页面读到新卡',await page.locator('#vip-tweets-feed .vip-tweet-card[data-tweet-id="110"]').count()===1);
      await page.click('.track-pill[data-track="claude_code"]');
      check('五赛道筛选包含 Grok',await page.locator('#vip-tweets-feed .vip-tweet-card[data-tweet-id="110"]').count()===1);
      await page.click('.track-pill[data-track="overseas_infra"]');
      check('赛道筛选排除其他赛道',await page.locator('#vip-tweets-feed .vip-tweet-card[data-tweet-id="110"]').count()===0);
      await page.click('.track-pill[data-track="all"]');
      // 可复用渲染函数不依赖旧视图，直接创建完整阅读卡。
      check('独立卡片与详情渲染可复用',await page.evaluate(()=>{const p=radarTweets('vip').find(p=>p.id==='101');return renderPostCard(p).querySelector('.tweet-text').textContent===p.text && renderPostDetail(p).querySelector('.grok-why').textContent===p._grok.why;}));
    } else {
      // 独立从报告文件取原始 ID 和全文，与 API/页面双重对照。
      const original=[];
      for(const kind of ['daily','alerts','weekly']){const folder=path.join(reports,kind);if(fs.existsSync(folder))for(const name of fs.readdirSync(folder).filter(n=>n.endsWith('.json'))){try{const d=JSON.parse(fs.readFileSync(path.join(folder,name)));for(const p of d.posts||d.entries||[]) if(new Date(p.created_at).getTime()>=Date.now()-48*3600000 && new Date(p.created_at)<=new Date())original.push(p);}catch(_){}}}
      check('真实报告去重数与 API 一致',new Set(original.map(p=>p.id||p.url.match(/status\/(\d+)/)?.[1])).size===data.count, '真实 '+data.count+' 条');
      const expected=data.merged_posts.filter(p=>p.grok && p.content_eligible!==false);
      await page.click('.vip-time-btn[data-time="all_48h"]');
      const vipIds=await page.locator('#vip-tweets-feed .vip-tweet-card[data-tweet-id]').evaluateAll(ns=>ns.map(n=>n.dataset.tweetId));
      await page.click('#tab-feed-btn');await page.click('.time-btn[data-time="48h"]');
      const feedIds=await page.locator('#tweets-feed .tweet-card[data-tweet-id]').evaluateAll(ns=>ns.map(n=>n.dataset.tweetId));
      check('真实 Grok 按分榜全部展示',expected.every(p=>(p.view_groups.includes('vip')?vipIds:feedIds).includes(p.id)), '符合内容规则 '+expected.length+' 条');
      let sampled=0;
      for(const p of expected.slice(0,3)){
        const mode=p.view_groups[0];await page.click(mode==='vip'?'#tab-vip-btn':'#tab-feed-btn');
        const card=page.locator((mode==='vip'?'#vip-tweets-feed .vip-tweet-card':'#tweets-feed .tweet-card')+'[data-tweet-id="'+p.id+'"]');
        check('真实原文逐字核对 '+p.id,await card.locator('.tweet-text').textContent()===(p.full_text||p.text));
        check('真实 why/pattern 逐字核对 '+p.id,await card.locator('.grok-why').textContent()===p.grok.why && await card.locator('.grok-pattern').textContent()===p.grok.pattern);
        const raw=original.find(r=>r.id===p.id);check('原始报告与统一数据一致 '+p.id,!!raw && (p.text===raw.text || p.text.length>=raw.text.length));sampled++;
      }
      check('抽查至少三张',sampled===3);
      const normalized=expected[0];
      check('真实指标取最新来源',await page.evaluate(p=>{
        const t=radarTweets(p.view_groups[0]).find(t=>t.id===p.id);
        return t.views===p.views && t.likes===p.likes && t.bookmarks===p.bookmarks;
      },normalized));
      const p=expected.find(p=>p.id==='2115715632532713672') || expected[0];selectedId=p.id;originalFeedback=snapshotFeedback();
      const mode=p.view_groups[0];await page.click(mode==='vip'?'#tab-vip-btn':'#tab-feed-btn');
      const selector=(mode==='vip'?'#vip-tweets-feed .vip-tweet-card':'#tweets-feed .tweet-card')+'[data-tweet-id="'+p.id+'"]';
      if(p.id==='2115715632532713672')check('真实短文不完整提示',await page.locator(selector+' .grok-incomplete').textContent()==='原文不完整，点开原帖看全文');
      const popupPromise=context.waitForEvent('page');await page.locator(selector+' .tweet-top a').first().click();const popup=await popupPromise;
      await new Promise(r=>setTimeout(r,200));check('打开原帖新标签 URL',popup.url()===p.grok.url, popup.url());await popup.close();
      const button=page.locator(selector+' .verdict-worth');if(await button.getAttribute('aria-pressed')!=='true')await button.click();
      await page.waitForFunction(id=>grokState.feedback[id]?.verdict==='worth',p.id);
      await page.reload();await page.waitForFunction(()=>!grokState.loading && DATA_SOURCE.kind==='json');await page.click(mode==='vip'?'#tab-vip-btn':'#tab-feed-btn');
      if(mode==='vip')await page.click('.vip-time-btn[data-time="all_48h"]');else await page.click('.time-btn[data-time="48h"]');
      check('真实反馈刷新保持并导出',await page.locator(selector+' .verdict-worth').getAttribute('aria-pressed')==='true' && JSON.parse(fs.readFileSync(feedbackPaths()[1]))[p.id].verdict==='worth');
      await page.screenshot({path:path.join(evidence,'impl-grok-bot-radar-real-feed.png'),fullPage:true});
      await page.click('#tab-vip-btn');await page.click('.vip-time-btn[data-time="all_48h"]');
      await page.screenshot({path:path.join(evidence,'impl-grok-bot-radar-real-vip.png'),fullPage:true});
    }
    if (!REAL) {
      fs.renameSync(reports,reports+'-saved');
      const empty=await getData();check('反例4：报告目录不存在仍返回200空态数据',empty.count===0 && empty.latest_report===null);
      await page.click('#btn-refresh-radar');await page.waitForFunction(()=>grokState.count===0 && !grokState.loading);
      check('空态并入原数据回执行',await page.locator('#radar-read-receipt').innerText().then(t=>t.includes('Grok Bot 还没有产出报告')));
    }
    check('无浏览器脚本错误',errors.length===0,errors.join(';'));
    await page.screenshot({path:path.join(evidence,REAL?'impl-grok-bot-radar-real.png':'fixture.png'),fullPage:true});
    console.log(JSON.stringify({passed:results.length,results,evidence},null,2));
  } finally {restoreFeedback();if(context)await context.close();if(server)server.kill('SIGTERM');}
})().catch(e=>{console.error(e);process.exitCode=1;});
