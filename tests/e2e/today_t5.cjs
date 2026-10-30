'use strict';
const writeGuard=require('./real_write_guard.cjs');writeGuard.install();
if(process.argv.includes('--real')){writeGuard.readOnlyMain('today_t5').catch(e=>{console.error(e);process.exitCode=1});return;}

// 隔离 Chromium profile + 随机端口，后端 NetworkGuard 与 RSS 夹具；--real 仅合同明确授权的线上验收。
const fs=require('fs'),path=require('path'),os=require('os'),net=require('net'),assert=require('assert/strict');
const {spawn}=require('child_process');
const {chromium}=(()=>{try{return require('playwright')}catch(_){return require(path.join(os.homedir(),'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright'))}})();
const ROOT=path.resolve(__dirname,'../..'),PY=path.resolve(ROOT,'../article-importer/.venv/bin/python'),REAL=false;
const tmp=fs.mkdtempSync('/tmp/today-t5-'),out='/tmp/impl-t5';fs.mkdirSync(out,{recursive:true});
let child,context,base,workspace,serverLog='';const checks=[],errors=[],network=[];let timing;
const check=(name,ok,detail='')=>{assert.ok(ok,name+' '+detail);checks.push({name,passed:true,detail});console.log('通过 '+name+(detail?' '+detail:''));};
async function start(){
 workspace=path.join(tmp,'workspace');writeGuard.assertSandbox(workspace);const reports=path.join(tmp,'reports');fs.mkdirSync(path.join(reports,'daily'),{recursive:true});fs.mkdirSync(workspace,{recursive:true});
 const stamp=new Date(Date.now()-60000).toISOString();
 const rows=[99999,100000,null,'100000'].map((views,i)=>({id:String(8800+i),handle:'fixture',author:'阈值夹具作者',text:'AI Agent 完整原文',url:'https://x.com/fixture/status/'+(8800+i),created_at:stamp,label:'爆款',topic:'Agent',views,likes:2,bookmarks:2}));
 fs.writeFileSync(path.join(reports,'daily/t5.json'),JSON.stringify({collected_at:stamp,posts:rows}));
 fs.writeFileSync(path.join(tmp,'bundle.json'),JSON.stringify({updated_at:stamp,tweets:[],vip_tweets:[]}));
 const vd=path.join(workspace,'video-radar');fs.mkdirSync(vd);fs.writeFileSync(path.join(vd,'page.json'),JSON.stringify({schema:'video-radar-page/v1',generated_at:stamp,sources:{},lists:{douyin_daily:[{key:'douyin:9900',id:'9900',platform:'douyin',title:'AI 视频十万播放夹具',author:'视频夹具作者',published_at:stamp,last_seen_at:stamp,url:'https://www.douyin.com/video/9900',plays:150000,likes:1,relevance:'relevant',flags:['low_fan_hot']}]}}));
 const port=await new Promise(resolve=>{const s=net.createServer().listen(0,'127.0.0.1',()=>{const p=s.address().port;s.close(()=>resolve(p));});});base='http://127.0.0.1:'+port;writeGuard.registerSandbox(base,workspace);
 const code=`import sys,time,json;from pathlib import Path;from http.server import ThreadingHTTPServer;from datetime import datetime,timezone;from email.utils import format_datetime;sys.path.insert(0,${JSON.stringify(ROOT)});import server,grok_bot_radar,linuxdo_source,write_from_item;from tests._network_guard import NetworkGuard;from tests.test_linuxdo_source import rss;root=Path(${JSON.stringify(workspace)});temp=Path(${JSON.stringify(tmp)});guard=NetworkGuard();guard.__enter__();grok_bot_radar.BUNDLE_PATH=temp/'bundle.json';write_from_item.ItemWriter._run=lambda self,item,state:None
body=rss().replace(b'Fri, 30 Oct 2026 07:00:00 GMT',format_datetime(datetime.now(timezone.utc)).encode())
def fixture():
 (temp/'rss-started').write_text('started')
 if (temp/'fail-refresh').exists():raise linuxdo_source.HTTPError(linuxdo_source.RSS_URL,403,'challenge',{},None)
 time.sleep(8)
 if guard.blocked:raise AssertionError('EXTERNAL_NETWORK')
 (temp/'rss-done').write_text('done')
 return linuxdo_source.parse_rss(body),[]
linuxdo_source.fetch_feeds=fixture
s=ThreadingHTTPServer(('127.0.0.1',${port}),server.Handler);s.store=server.Store(root);s.serve_forever()`;
 child=spawn(PY,['-u','-c',code],{cwd:ROOT,env:{...process.env,PYTHONDONTWRITEBYTECODE:'1',GROK_BOT_REPORTS_DIR:reports},stdio:['ignore','pipe','pipe']});child.stdout.on('data',b=>serverLog+=b);child.stderr.on('data',b=>serverLog+=b);
 for(let i=0;i<100;i++){try{if((await fetch(base+'/api/items')).ok)return;}catch(_){}if(child.exitCode!==null)throw Error(serverLog);await new Promise(r=>setTimeout(r,100));}throw Error('隔离服务未启动 '+serverLog);
}
async function get(url){const r=await context.request.get(base+url);assert.equal(r.status(),200);return r.json();}
async function ready(page){await page.waitForFunction(()=>document.getElementById('item-list').getAttribute('aria-busy')==='false'&&!document.getElementById('item-list').inert);}
async function filter(page,id,value){const wait=page.waitForResponse(r=>{const u=new URL(r.url());return u.pathname==='/api/today'&&u.searchParams.get(id)===value;});await page.selectOption('#'+id,value);await wait;await ready(page);}
async function choose(page,id){await page.locator('#item-list .item-card[data-item-id="'+id+'"]').click();await page.locator('#reader .item-detail[data-item-id="'+id+'"]').waitFor();}
async function iconLayout(page){return page.locator('#item-list .item-card').evaluateAll(cards=>cards.every(card=>{const badges=card.querySelector('.source-badges'),summary=card.querySelector('.item-summary'),actions=card.querySelector('.item-actions');const b=badges.getBoundingClientRect(),s=summary.getBoundingClientRect(),c=card.getBoundingClientRect();return b.right<=c.right&&b.left>=c.left&&b.bottom<=s.top&&(!actions||b.bottom<=actions.getBoundingClientRect().top)&&[...badges.children].every(n=>n.title&&n.getAttribute('aria-label')&&n.querySelector('svg'));}));}
(async()=>{try{
 await start();context=await chromium.launchPersistentContext(path.join(tmp,'profile'),{headless:true,channel:'chrome',viewport:{width:1440,height:900},args:['--disable-background-networking','--disable-component-update']});
 await context.route('**/*',route=>{const url=new URL(route.request().url());if(url.origin===base)return route.continue();network.push(url.origin);return route.abort();});
 const page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));
 const started=performance.now();await page.goto(base+'/today.html');await page.locator('#item-list .item-card').first().waitFor();timing=performance.now()-started;
 check('首屏列表小于1秒',timing<1000,timing.toFixed(1)+' ms');
 if(!REAL)check('8秒RSS仍在后台执行',fs.existsSync(path.join(tmp,'rss-started'))&&!fs.existsSync(path.join(tmp,'rss-done')));
 check('1440卡片右上角本地SVG有名称且不挡标题动作',await iconLayout(page));
 check('共享组件保留其他来源名称',await page.evaluate(async()=>{const m=await import('/components/item-card.js');const badge=m.sourceBadges({sources:['manual']}).firstChild;return badge.textContent==='manual'&&badge.title==='manual';}));
 const rangeWait=page.waitForResponse(r=>{const u=new URL(r.url());return u.pathname==='/api/today'&&u.searchParams.get('range')==='48h';});await page.locator('[data-range="48h"]').click();await rangeWait;await ready(page);
 await filter(page,'tier','火爆');let hot=await get('/api/today?tier='+encodeURIComponent('火爆')+'&range=48h');
 check('火爆筛选只返回浏览≥100000且hot为真',hot.items.length>0&&hot.items.every(n=>n.hot&&Number(n.metrics.views)>=100000));
 check('卡片火爆徽标与档位并存',await page.locator('#item-list .item-card').count()>0&&await page.locator('#item-list .item-card').evaluateAll(ns=>ns.every(n=>n.querySelector('.item-hot')?.textContent==='火爆'&&n.querySelector('.item-tier'))));
 if(!REAL)check('边界99999和空值排除、字符串与视频播放保留',hot.items.map(n=>n.id).sort().join(',')===['x:8801','x:8803','douyin:9900'].sort().join(','));
 const visibleHot=await page.locator('#item-list .item-card').first().getAttribute('data-item-id');await choose(page,visibleHot);check('阅读器火爆文字与来源图标存在',await page.locator('#reader .item-hot').textContent()==='火爆'&&await page.locator('#reader .detail-heading .source-badge svg').count()>0);
 await page.screenshot({path:path.join(out,(REAL?'real':'fixture')+'-1440-hot.png')});
 await filter(page,'source','L');check('火爆叠加linux.do为空',await page.locator('#item-list .item-card').count()===0);
 await filter(page,'tier','');
 await page.locator('#item-list .source-badge[data-source="L"]').first().waitFor({timeout:20000});await ready(page);
 if(!REAL)check('后台慢刷新结束后自动出现linux.do列表',fs.existsSync(path.join(tmp,'rss-done')));
 const listing=await get('/api/today?source=L&range=7d');check('linux.do统一列表且浏览未知不火爆',listing.items.length>0&&listing.items.every(n=>n.sources.includes('L')&&n.metrics.views===null&&!n.hot));
 const id=await page.locator('#item-list .item-card').first().getAttribute('data-item-id');const detail=await get('/api/today/item?id='+encodeURIComponent(id));await choose(page,id);
 check('点开linux.do完整正文逐字一致',await page.locator('#reader .full-text').textContent()===detail.full_text&&detail.full_text.length>0);
 check('linux.do原帖链接可以打开',await page.locator('#reader .item-actions a').getAttribute('href')===detail.url);
 check('无script、onerror及javascript链接注入',await page.evaluate(()=>!window.PWNED)&&await page.locator('#reader .full-text script,#reader .full-text [onerror],#reader a[href^="javascript:"]').count()===0);
 for(const action of ['accurate','inaccurate']){await page.locator('#reader [data-action="'+action+'"]').click();await page.locator('#reader [data-action="'+action+'"][aria-pressed=true]').waitFor();}check('linux.do准与不准可保存',true);
 await page.locator('#reader [data-action="keep"]').click();await page.locator('#reader [data-action="keep"][aria-pressed=true]').waitFor();
 await page.screenshot({path:path.join(out,(REAL?'real':'fixture')+'-1440-linuxdo.png')});
 await page.goto(base+'/library.html');await ready(page);const q=detail.title.slice(0,16);const searched=page.waitForResponse(r=>{const u=new URL(r.url());return u.pathname==='/api/items'&&u.searchParams.get('q')===q;});await page.locator('#q').fill(q);await searched;await ready(page);
 check('存后素材库按标题能搜到',await page.locator('.item-card[data-item-id="'+id+'"]').count()===1);
 await page.screenshot({path:path.join(out,(REAL?'real':'fixture')+'-library-saved.png')});
 if(!REAL){await choose(page,id);const popupPromise=page.waitForEvent('popup');await page.locator('#reader [data-action="use"]').click();const popup=await popupPromise;await popup.waitForURL(u=>u.searchParams.has('article'));const slug=new URL(popup.url()).searchParams.get('article'),draft=await get('/api/article/'+encodeURIComponent(slug));check('linux.do拿去写保留全文和链接',draft.blocks.some(b=>b.text?.includes(detail.full_text))&&draft.blocks.some(b=>b.text?.includes(detail.url)));await popup.close();}
 await page.setViewportSize({width:390,height:844});await page.goto(base+'/today.html');await ready(page);await filter(page,'source','L');await page.locator('#item-list .item-card').first().waitFor();
 check('390卡片图标不遮挡标题和动作且页面无横向溢出',await iconLayout(page)&&await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));await page.screenshot({path:path.join(out,(REAL?'real':'fixture')+'-390-list.png')});
 await choose(page,id);check('390阅读器来源图标在标题区内',await page.locator('#reader .detail-heading .source-badges').evaluate(n=>{const r=n.getBoundingClientRect();return r.left>=0&&r.right<=innerWidth;}));await page.screenshot({path:path.join(out,(REAL?'real':'fixture')+'-390-reader.png')});
 if(!REAL){fs.writeFileSync(path.join(tmp,'fail-refresh'),'1');fs.unlinkSync(path.join(workspace,'linuxdo/status.json'));await page.goto(base+'/today.html');await ready(page);await filter(page,'source','L');await page.locator('#sources button').filter({hasText:'linux.do'}).waitFor();check('403异常显示且旧快照仍可打开',await page.locator('#item-list .source-badge[data-source="L"]').count()>0);await choose(page,id);check('403后旧全文保持一致',await page.locator('#reader .full-text').textContent()===detail.full_text);}
 check('页面无脚本错误',errors.length===0,JSON.stringify(errors));
 }catch(error){console.error(error);process.exitCode=1;}finally{fs.writeFileSync(path.join(out,(REAL?'real':'fixture')+'-results.json'),JSON.stringify({tmp,base,timing,checks,errors,network},null,2));if(context)await context.close();if(child)child.kill('SIGTERM');fs.writeFileSync(path.join(tmp,'server.log'),serverLog);console.log('证据 '+out+'；隔离目录 '+tmp+'；'+checks.length+'项通过');}
})();
