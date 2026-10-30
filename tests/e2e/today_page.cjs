'use strict';
const writeGuard=require('./real_write_guard.cjs');writeGuard.install();
if(process.argv.includes('--real')){writeGuard.readOnlyMain('today_page').catch(e=>{console.error(e);process.exitCode=1});return;}

// 所有浏览器均为 /tmp 独立 profile + headless；夹具随机端口；--real 提前进入只读 GET 路径。
const fs=require('fs'),os=require('os'),path=require('path'),net=require('net'),crypto=require('crypto');
const assert=require('assert/strict');const {spawn}=require('child_process');
const {chromium}=(()=>{try{return require('playwright');}catch(_){return require(path.join(os.homedir(),'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright'));}})();
const REPO=path.resolve(__dirname,'../..'),PY=path.resolve(REPO,'../article-importer/.venv/bin/python'),REAL=false;
const temp=fs.mkdtempSync('/tmp/today-e2e-'),evidence=REAL?path.join(REPO,'coordination/impl-today-p1-screenshots'):temp;
fs.mkdirSync(evidence,{recursive:true});
const results=[],errors=[],timings=[],perf={},snapshots=[],imageResponses=[];let server,context,base,workspace,reports,bundle;
const iso=h=>new Date(Date.now()-h*3600000).toISOString();
const long='首行😀 <script>alert(1)</script> <img src=x onerror=alert(1)> &amp;\n\n'+'完整中文正文'.repeat(450)+'\n末行　\t';
const check=(name,ok,detail='')=>{assert.ok(ok,name+' '+detail);results.push({name,passed:true,detail});console.log('通过 '+name+(detail?' '+detail:''));};
const row=(id,extra={})=>({id,handle:'fixture',author:'夹具作者',text:'AI Agent 完整原文',url:'https://x.com/fixture/status/'+id,created_at:iso(1),followers:20000,label:'爆款',topic:'Agent',views:9000,likes:100,bookmarks:111,why:'为什么爆：AI 实操',pattern:'可复制写法',...extra});
function writeReport(rows){const p=path.join(reports,'daily/a.json');fs.mkdirSync(path.dirname(p),{recursive:true});fs.writeFileSync(p,JSON.stringify({collected_at:iso(.1),posts:rows}));}
async function start(){
 workspace=path.join(temp,'workspace');writeGuard.assertSandbox(workspace);reports=path.join(temp,'reports');fs.mkdirSync(workspace,{recursive:true});
 fs.cpSync(path.join(REPO,'public'),path.join(temp,'public'),{recursive:true,filter:src=>!src.endsWith('twitter-radar-bundle.json')});
 bundle=path.join(temp,'public/twitter-radar-bundle.json');
 fs.writeFileSync(bundle,JSON.stringify({updated_at:iso(.05),tweets:[],vip_tweets:[{id:'101',user:'fixture',name:'夹具作者',text:'AIsa 短摘要',url:'https://x.com/fixture/status/101',views:12000,likes:200,bookmarks:8,published_at:iso(1),fetched_at:iso(.01),article:{body:'AIsa 独有长文'},thread:['AIsa 独有串推'],quoted:{text:'AIsa 独有引用'},media:[{type:'photo',url:'https://images.example/aisa.png'}]}]}));
 writeReport([row('101',{text:long,article:{title:'Grok 长文标题',body:long},thread:['Grok 独有串推😀'],quoted:{text:'Grok 独有引用'},media:[{type:'photo',url:'https://images.example/grok.png'},{type:'video',poster:'https://images.example/grok-video.png'}],fit_score:3,doable:2,my_angle:'我的切入角度',outline:['提纲一','提纲二'],doable_resource:'本地 CLI'}),row('102',{content_complete:false}),...Array.from({length:10},(_,i)=>row(String(103+i),{text:'AI Agent 工作流第 '+i+' 条完整原文'})),row('120',{created_at:iso(20*24)})]);
 const vd=path.join(workspace,'video-radar');fs.mkdirSync(vd,{recursive:true});
 fs.writeFileSync(path.join(vd,'page.json'),JSON.stringify({schema:'video-radar-page/v1',generated_at:iso(.1),sources:{},lists:{douyin_daily:[{key:'douyin:901',id:'901',platform:'douyin',title:'AI 视频标题',author:'视频作者',published_at:iso(1),last_seen_at:iso(.1),url:'https://www.douyin.com/video/901',cover:'https://images.example/video.jpg',likes:301,collects:21,plays:12000,relevance:'relevant',relevance_reason:'命中 AI',flags:['low_fan_hot']}]}}));
 const port=await new Promise(resolve=>{const s=net.createServer().listen(0,'127.0.0.1',()=>{const n=s.address().port;s.close(()=>resolve(n));});});base='http://127.0.0.1:'+port;writeGuard.registerSandbox(base,workspace);
 const runner=`import sys;from pathlib import Path;sys.path.insert(0,${JSON.stringify(REPO)});import server,grok_bot_radar,linuxdo_source;linuxdo_source.refresh_background=lambda root:None;server.ROOT=Path(${JSON.stringify(temp)});grok_bot_radar.BUNDLE_PATH=Path(${JSON.stringify(bundle)});sys.argv=['server.py','--workspace',${JSON.stringify(workspace)},'--port',sys.argv[1]];server.main()`;
 server=spawn(PY,['-u','-c',runner,String(port)],{cwd:REPO,env:{...process.env,PYTHONDONTWRITEBYTECODE:'1',GROK_BOT_REPORTS_DIR:reports,ARTICLE_STUDIO_MODEL_CONFIG:path.join(temp,'absent.json')},stdio:['ignore','pipe','pipe']});
 let log='';server.stdout.on('data',b=>log+=b);server.stderr.on('data',b=>log+=b);
 for(let i=0;i<150;i++){try{if((await fetch(base+'/api/today')).ok)return;}catch(_){}if(server.exitCode!==null)throw Error(log);await new Promise(r=>setTimeout(r,100));}throw Error(log);
}
const feedbackPaths=()=>[path.join(workspace,'today/feedback.json'),path.join(workspace,'twitter-radar/grok-bot/feedback.json'),path.join(reports,'feedback/studio-feedback.json'),path.join(workspace,'video-radar/feedback.json')];
const hash=raw=>raw===null?'absent':crypto.createHash('sha256').update(raw).digest('hex');
function snapshot(){for(const file of feedbackPaths()){const raw=fs.existsSync(file)?fs.readFileSync(file):null;snapshots.push({file,raw,before:hash(raw)});}}
function restore(){if(!REAL)return;for(const s of snapshots){if(s.raw===null){if(fs.existsSync(s.file))fs.unlinkSync(s.file);}else{fs.mkdirSync(path.dirname(s.file),{recursive:true});const p=s.file+'.today-e2e-'+process.pid;fs.writeFileSync(p,s.raw);fs.renameSync(p,s.file);}s.after=hash(fs.existsSync(s.file)?fs.readFileSync(s.file):null);assert.equal(s.before,s.after,'真实反馈恢复 SHA '+s.file);}}
async function get(url){const r=await context.request.get(base+url);assert.equal(r.status(),200);return r.json();}
async function post(id,action,headers={Origin:base,'X-Article-Studio':'1'}){return context.request.post(base+'/api/today/feedback',{headers,data:{id,action}});}
async function filter(page,value){const response=page.waitForResponse(r=>{const url=new URL(r.url());return url.pathname==='/api/today' && url.searchParams.get('source')===value;});await page.selectOption('#source',value);await response;await page.waitForFunction(()=>!document.getElementById('item-list').inert);}
async function select(page,id){const card=page.locator('#item-list .item-card[data-item-id="'+id+'"]');await card.click();await page.locator('#reader .item-detail[data-item-id="'+id+'"]').waitFor();}
(async()=>{try{
 await start();snapshot();
 context=await chromium.launchPersistentContext(path.join(temp,'profile'),{headless:true,channel:'chrome',viewport:{width:1440,height:900},permissions:['clipboard-read','clipboard-write'],args:['--disable-background-networking','--disable-component-update']});
 await context.route('**/*',route=>{const req=route.request(),url=new URL(req.url());if(url.origin===base)return route.continue();if(REAL && req.resourceType()==='image' && req.method()==='GET' && url.protocol==='https:' && /\.(xhscdn\.com|douyinpic\.com|byteimg\.com)$/.test(url.hostname))return route.continue();if(!REAL && req.resourceType()==='image')return route.fulfill({contentType:'image/png',body:Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/l9sAAAAASUVORK5CYII=','base64')});return req.isNavigationRequest()?route.fulfill({status:200,contentType:'text/html',body:'<title>原帖验收</title>'}):route.abort();});
 context.on('response',r=>{if(r.request().resourceType()==='image')imageResponses.push({host:new URL(r.url()).hostname,status:r.status()});});
 const oldCsp="default-src 'self'; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline'; script-src 'self'; object-src 'none'; frame-ancestors 'none'";
 for(const url of ['/twitter-radar.html','/video-radar.html','/index.html'])check('旧页CSP逐字不变 '+url,(await context.request.get(base+url)).headers()['content-security-policy']===oldCsp);
 const todayResponse=await context.request.get(base+'/today.html');check('今日页仅增加HTTPS图片且保留no-referrer',todayResponse.headers()['content-security-policy']===oldCsp.replace("img-src 'self' data: blob:;","img-src 'self' data: blob: https:;")&&todayResponse.headers()['referrer-policy']==='no-referrer');
 const httpTest=await context.newPage();await httpTest.goto(base+'/today.html');await httpTest.locator('#item-list .item-card').first().waitFor();
 await httpTest.evaluate(async base=>{const m=await import(base+'/components/item-card.js');const root=document.createElement('div');root.id='http-image-test';document.body.append(root);m.renderRichContent(root,{kind:'video',url:'https://www.douyin.com/video/1',media:[{type:'video',poster:'http://images.example/insecure.png'}]});},base);
 await httpTest.locator('#http-image-test .media-unavailable').waitFor();check('HTTP图片仍被CSP拦截并显示占位',await httpTest.locator('#http-image-test img').evaluate(n=>n.naturalWidth===0&&n.hidden));await httpTest.close();
 const page=await context.newPage();page.on('pageerror',e=>errors.push(String(e)));
 for(let i=0;i<3;i++){const start=performance.now();await page.goto(base+'/today.html');await page.locator('#item-list .item-card').first().waitFor();timings.push(Math.round((performance.now()-start)*10)/10);}
 check('首个列表项三次均小于1秒',timings.every(v=>v<1000),JSON.stringify(timings)+' ms');
 const visible=await page.locator('#item-list .item-card').evaluateAll(cards=>cards.filter(c=>{const r=c.getBoundingClientRect(),p=c.parentElement.getBoundingClientRect();return r.top>=p.top && r.bottom<=Math.min(p.bottom,innerHeight);}).length);
 check('1440×900 首屏完整可见至少5条',visible>=5,String(visible));
 await page.screenshot({path:path.join(evidence,'today-first-screen.png')});
 let listing=await get('/api/today?range=48h');
 if(REAL){const ids=listing.items.map(p=>p.id);check('反例15真实数据412较早帖排在111较晚帖前',ids.includes('x:2115655889428623568') && ids.indexOf('x:2115655889428623568')<ids.indexOf('x:2115715632532713672'));check('真实AIsa未知收藏文案',await page.locator('#item-list .item-card').evaluateAll(ns=>ns.some(n=>n.querySelector('.source-badge')?.dataset.source==='A'&&n.textContent.includes('收藏 未知'))));}
 check('摘要接口无全文或结构化补充字段',listing.items.every(p=>!('full_text'in p)&&!('rich_sources'in p)&&Array.from(p.title).length<=120));
 const firstG=listing.items.find(p=>p.sources.includes('G'));assert.ok(firstG);
 if(!REAL){
  const item=await get('/api/today/item?id=x:101');
  await select(page,'x:101');
  check('2400字以上正文含emoji多行逐字一致',await page.locator('#reader .full-text').textContent()===long);
  check('正文标签纯文本无注入节点',await page.locator('#reader .full-text script,#reader .full-text img').count()===0 && await page.locator('#reader .full-text').textContent().then(t=>t.includes('&amp;')));
  check('跨来源去重、最新采样数字和更完整正文',listing.items.filter(p=>p.id==='x:101').length===1 && item.metrics.views===12000 && item.full_text===long && item.sources.join(',')==='A,G');
  for(const headers of [{'X-Article-Studio':'1'},{Origin:'http://evil.com','X-Article-Studio':'1'},{Origin:base}])check('来源校验拒绝且403', (await post('x:101','accurate',headers)).status()===403);
  check('未授权POST不落盘',feedbackPaths().every(p=>!fs.existsSync(p)));
  const responses=await Promise.all(['x:101','x:102'].map(id=>post(id,'accurate')));
  check('并发POST不同id无丢失',responses.every(r=>r.status()===200) && Object.keys(JSON.parse(fs.readFileSync(feedbackPaths()[0]))).length===2);
  check('Bot镜像与工作台Grok反馈逐字一致',fs.readFileSync(feedbackPaths()[1],'utf8')===fs.readFileSync(feedbackPaths()[2],'utf8'));
  await post('x:102','clear');check('accurate后clear最终无记录',!('x:102'in JSON.parse(fs.readFileSync(feedbackPaths()[0])))&&!('102'in JSON.parse(fs.readFileSync(feedbackPaths()[1]))));
  // 空白隔离页直接引入组件，绝不初始化雷达页面状态。
  const blank=await context.newPage();await blank.goto(base+'/today.html');await blank.setContent('<main id="reuse"></main>');
  const reuse=await blank.evaluate(async({base,summary,item})=>{
   const m=await import(base+'/components/item-card.js');const root=document.getElementById('reuse');let action=null;
   root.append(m.renderPostCard(summary,{feedback:'accurate',onAction:a=>action=a}));
   const labels=[...root.querySelectorAll('button')].map(n=>n.textContent);const badges=[...root.querySelectorAll('.source-badge')].map(n=>n.dataset.source);
   const pressed=root.querySelector('[data-action=accurate]').getAttribute('aria-pressed');root.querySelector('[data-action=accurate]').click();
   root.append(m.renderPostDetail(item));
   const details=root.querySelector('.item-detail').textContent;
   const media=root.querySelectorAll('.item-detail .grok-media img').length;
   return {labels,badges,pressed,action,details,media,globalState:typeof window.grokState};
  },{base,summary:listing.items.find(p=>p.id==='x:101'),item});
  check('reuse：仅传摘要渲染双来源和完整动作组',reuse.badges.join(',')==='A,G' && reuse.labels.join(',')==='准,不准,存,拿去写' && reuse.globalState==='undefined');
  check('reuse：反馈参数和回调，无全局状态',reuse.pressed==='true' && reuse.action==='clear');
  check('AIsa无收藏显示收藏未知',await blank.evaluate(async base=>{const m=await import(base+'/components/item-card.js');return m.renderPostCard({id:'x:unknown',kind:'post',sources:['A'],title:'摘要',author:{},metrics:{bookmarks:null}}).textContent.includes('收藏 未知');},base));
  for(const token of ['Grok 长文标题',long,'Grok 独有串推😀','Grok 独有引用','AIsa 独有长文','AIsa 独有串推','AIsa 独有引用'])check('统一详情直接渲染不丢 '+token.slice(0,16),reuse.details.includes(token));
  check('统一详情保留两来源图片和视频封面',reuse.media===3);
  const links=await blank.evaluate(async base=>{const m=await import(base+'/components/item-card.js');let rejected=[];for(const url of ['javascript:alert(1)','https://x.com.evil.com/a/status/1','','//evil.com'])rejected.push(!m.renderPostDetail({id:'x:1',kind:'post',url,author:{},metrics:{}}).querySelector('a'));const safe=m.renderPostDetail({id:'x:1',kind:'post',url:'https://x.com/a/status/1',author:{},metrics:{}}).querySelector('a');const root=document.createElement('div');m.renderRichContent(root,{kind:'video',url:'https://www.douyin.com/video/1',media:[{type:'video',poster:'javascript:alert(1)'},{type:'video',poster:'https://example.com/cover.jpg'}]});return {rejected,safe:!!safe,images:root.querySelectorAll('img').length};},base);
  check('原帖链接拒绝四种危险输入且合法链接可用',links.rejected.every(Boolean)&&links.safe);check('视频封面仅允许http(s)',links.images===1);
  await blank.close();
  const author=page.locator('#reader .detail-author');await author.click();await page.locator('#reader h2').waitFor();
  check('点作者显示已有30天历史（含20天前条目）',await page.locator('#reader [data-item-id="x:120"]').count()===1);
  await select(page,'x:101');await page.keyboard.press('j');await page.waitForFunction(()=>document.querySelector('#reader .item-detail')?.dataset.itemId!=='x:101');check('J切换下一条',await page.locator('#reader .item-detail').getAttribute('data-item-id')!=='x:101');
  await page.keyboard.press('k');await page.locator('#reader .item-detail[data-item-id="x:101"]').waitFor();await page.keyboard.press('Enter');check('K返回且Enter聚焦阅读器',await page.evaluate(()=>document.activeElement.id==='reader'));
  const popup=page.waitForEvent('popup');await page.keyboard.press('o');const original=await popup;await original.waitForLoadState();check('O打开原帖',original.url()==='https://x.com/fixture/status/101');await original.close();
  const oldWidth=await page.locator('#item-list').evaluate(n=>n.getBoundingClientRect().width);await page.locator('#divider').focus();await page.keyboard.press('ArrowRight');check('分栏可调整',await page.locator('#item-list').evaluate(n=>n.getBoundingClientRect().width)>oldWidth);
  // 新数据不重启；无评分不凑数；半写和异常状态仍保留其他来源。
  writeReport(Array.from({length:10},(_,i)=>row(String(101+i),{my_angle:'我的切入角度',outline:['提纲一','提纲二']})));
  await page.reload();await page.locator('.top-message').waitFor();check('全部缺评分显示等待文案且无置顶条目',(await get('/api/today?range=48h')).top.length===0 && await page.locator('.top-message').textContent()==='等 Grok Bot 按画像打分后出现');
  fs.writeFileSync(path.join(reports,'daily/broken.json'),'{"posts":[');await page.reload();await page.locator('#sources button').first().waitFor();check('半写来源异常状态红字可展开',await page.locator('#sources button').first().textContent().then(t=>t.includes('异常')));await page.locator('#sources button').first().click();check('来源错误详情可见',await page.locator('dialog').isVisible());await page.locator('dialog button').click();
  await page.setViewportSize({width:390,height:844});await page.locator('#item-list .item-card').first().click();await page.locator('#reader .item-detail').waitFor();check('窄屏全屏阅读器和返回按钮',await page.locator('.reader-back').isVisible() && await page.locator('#reader').evaluate(n=>n.getBoundingClientRect().width)>=380);await page.locator('.reader-back').click();check('窄屏返回列表',!(await page.locator('#reader').isVisible()));await page.setViewportSize({width:1440,height:900});
  // 延迟筛选：旧列表不可交互，响应到达后新卡可正常打开，旧详情请求不会挂住。
  await page.route('**/api/today?**',async route=>{await new Promise(r=>setTimeout(r,250));await route.continue();});
  const pendingFilter=filter(page,'V');await page.waitForFunction(()=>document.getElementById('item-list').inert);check('筛选刷新时旧列表不可交互',await page.locator('#item-list').evaluate(n=>n.inert));await pendingFilter;await page.locator('#item-list .item-card').first().click();await page.locator('#reader .item-detail').waitFor();check('延迟筛选后打开新条目不挂加载',await page.locator('#reader .item-detail').getAttribute('data-item-id')==='douyin:901');await page.unroute('**/api/today?**');
  const existing=JSON.parse(fs.readFileSync(path.join(reports,'daily/a.json'))).posts;
  writeReport([...existing,row('201',{bookmarks:412,likes:322,views_per_hour:1000,created_at:iso(8)}),row('202',{bookmarks:111,likes:50,views_per_hour:1000,created_at:iso(4)})]);
  await page.goto(base+'/today.html');await page.locator('#item-list .item-card').first().waitFor();
  const ids=await page.locator('#item-list .item-card').evaluateAll(ns=>ns.map(n=>n.dataset.itemId));check('反例15：同档位收藏412较早帖排在111较晚帖前',ids.indexOf('x:201')>=0 && ids.indexOf('x:201')<ids.indexOf('x:202'));
 }
 // 真/夹具共有：视频、不完整、三个详情、反馈撤销、拿去写建底稿。
 await page.goto(base+'/today.html');await page.locator('#item-list .item-card').first().waitFor();
 await select(page,firstG.id);await page.locator('#reader [data-action=accurate]').click();await page.locator('#reader [data-action=accurate][aria-pressed=true]').waitFor();
 await page.locator('#reader [data-action=accurate]').click();await page.locator('#reader [data-action=accurate][aria-pressed=false]').waitFor();
 check('真实路径：准再点撤销并落盘',JSON.parse(fs.readFileSync(feedbackPaths()[0],'utf8'))[firstG.id]===undefined);
 const target=await get('/api/today/item?id='+encodeURIComponent(firstG.id));
 const popupPromise=page.waitForEvent('popup');await page.locator('#reader [data-action=use]').click();const studio=await popupPromise;await studio.waitForURL(u=>u.searchParams.has('article'));const slug=new URL(studio.url()).searchParams.get('article');const draft=await get('/api/article/'+encodeURIComponent(slug));const body=draft.blocks.map(b=>b.text||'').join('\n');
 check('拿去写建底稿保留全文链接why角度提纲且打开指定作品',body.includes(target.full_text)&&body.includes(target.url)&&body.includes(target.why)&&body.includes('切入角度')&&body.includes('提纲')&&['/index.html','/'].includes(new URL(studio.url()).pathname));
 check('底稿保留切入角度和提纲实际值',(!target.my_angle||body.includes(typeof target.my_angle==='string'?target.my_angle:JSON.stringify(target.my_angle)))&&(!Array.isArray(target.outline)||target.outline.every(line=>body.includes(line))));
 check('拿去写记录used和作品关联',JSON.parse(fs.readFileSync(feedbackPaths()[0],'utf8'))[firstG.id].status==='used'&&(await get('/api/items/'+encodeURIComponent(firstG.id))).used_work_id===slug);
 await studio.locator('.article-body').waitFor();await studio.screenshot({path:path.join(evidence,'today-draft.png')});await studio.close();
 await filter(page,'V');
 await page.locator('#item-list .item-card').first().click();await page.locator('#reader .item-detail').waitFor();check('视频详情封面与播放原帖',await page.locator('#reader .grok-media img').count()>0 && await page.getByText('播放原帖',{exact:true}).count()>0);
 if(!REAL){await page.waitForFunction(()=>document.querySelector('#reader .grok-media img')?.naturalWidth>0);check('有效图片数据可解码为视频封面',await page.locator('#reader .grok-media img').first().evaluate(n=>n.naturalWidth>0));}
 if(REAL){const img=page.locator('#reader .grok-media img').first();await img.evaluate(n=>n.complete?Promise.resolve():new Promise(resolve=>{n.addEventListener('load',resolve,{once:true});n.addEventListener('error',resolve,{once:true});setTimeout(resolve,5000);}));console.log('真实封面加载',await img.evaluate(n=>({complete:n.complete,naturalWidth:n.naturalWidth})),JSON.stringify(imageResponses));check('封面不可用时给原帖回退提示',await img.evaluate(n=>n.naturalWidth>0)||await page.getByText('封面暂不可用，请播放原帖',{exact:true}).count()===1);}
 await page.screenshot({path:path.join(evidence,'today-video.png')});
 await filter(page,'G');await page.locator('#item-list .item-card').first().waitFor();const gList=await get('/api/today?range=48h&source=G');const incomplete=gList.items.find(p=>p.content_complete===false);assert.ok(incomplete);await select(page,incomplete.id);check('不完整详情显著提示并有原因',await page.locator('.incomplete-warning strong').textContent()==='原文不完整，点开原帖看全文' && await page.locator('.incomplete-warning div').textContent().then(t=>t.length>0));
 await page.screenshot({path:path.join(evidence,'today-incomplete.png')});
 await filter(page,'');await page.locator('#item-list .item-card').first().waitFor();const another=(await get('/api/today?range=48h')).items.find(p=>p.id!==firstG.id&&p.id!==incomplete.id&&p.kind==='post');await select(page,another.id);check('第三条详情正常打开',await page.locator('#reader .full-text').textContent().then(t=>t.length>0));
 for(const [name,url] of [['list','/api/today'],['detail','/api/today/item?id='+encodeURIComponent(firstG.id)]]){perf[name]=[];for(let i=0;i<5;i++){const t=performance.now();await get(url);perf[name].push(Number(((performance.now()-t)/1000).toFixed(5)));}check(name+'接口连续5次均<1秒',perf[name].every(n=>n<1),JSON.stringify(perf[name]));}
 perf.feedback=[];for(const action of ['accurate','clear','accurate','clear','clear']){const t=performance.now();assert.equal((await post(firstG.id,action)).status(),200);perf.feedback.push(Number(((performance.now()-t)/1000).toFixed(5)));}check('feedback接口连续5次均<1秒',perf.feedback.every(n=>n<1),JSON.stringify(perf.feedback));
 check('无页面脚本错误',errors.length===0,JSON.stringify(errors));
 }catch(error){console.error(error);process.exitCode=1;}finally{
  try{restore();if(REAL)check('真实反馈文件恢复前后SHA完全一致',snapshots.every(s=>s.before===s.after));}catch(error){console.error(error);process.exitCode=1;}
  fs.writeFileSync(path.join(temp,'results.json'),JSON.stringify({real:REAL,results,errors,timings,perf,imageResponses,feedback:snapshots.map(({file,before,after})=>({file,before,after})),evidence},null,2));
  console.log('证据 '+temp+'; '+results.length+' 项通过');if(context)await context.close();if(server)server.kill('SIGTERM');
 }
})();
