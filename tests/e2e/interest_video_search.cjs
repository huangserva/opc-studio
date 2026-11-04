'use strict';
// Explicit /tmp evidence root; every database/profile/server belongs to this copy.
const fs=require('fs'),path=require('path'),os=require('os'),net=require('net'),assert=require('assert/strict'),{spawn}=require('child_process');
const {chromium}=(()=>{try{return require('playwright')}catch(_){return require(path.join(os.homedir(),'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright'))}})();
const E=path.resolve(process.argv[2]||'');if(!E.startsWith('/tmp/interest-video/')&&!E.startsWith('/private/tmp/interest-video/'))throw Error('必须指定隔离证据目录');
const REPO=path.resolve(__dirname,'../..'),PY='/Users/huangzongning/development/article-importer/.venv/bin/python',W=path.join(E,'browser/workspace');
const results=[],errors=[];let server,context,base;
const check=(name,value,detail={})=>{assert.ok(value,name);results.push({name,passed:true,...detail});console.log('通过 '+name)};
(async()=>{try{
 const port=await new Promise(resolve=>{let s=net.createServer().listen(0,'127.0.0.1',()=>{let p=s.address().port;s.close(()=>resolve(p))})});base='http://127.0.0.1:'+port;
 const runner=`import sys;from pathlib import Path;sys.path.insert(0,${JSON.stringify(REPO)});import server,grok_bot_radar,linuxdo_source;linuxdo_source.refresh_background=lambda root:None;grok_bot_radar.BUNDLE_PATH=Path(${JSON.stringify(path.join(E,'bundle.json'))});sys.argv=['server.py','--workspace',${JSON.stringify(W)},'--port',sys.argv[1]];server.main()`;
 server=spawn(PY,['-B','-u','-c',runner,String(port)],{cwd:REPO,env:{...process.env,GROK_BOT_REPORTS_DIR:path.join(E,'reports'),ARTICLE_STUDIO_MODEL_CONFIG:path.join(E,'absent.json')},stdio:['ignore','pipe','pipe']});
 let log='';server.stdout.on('data',b=>log+=b);server.stderr.on('data',b=>log+=b);
 for(let n=0;n<100;n++){try{if((await fetch(base+'/api/today?channel=interest')).ok)break;}catch(_){}if(server.exitCode!==null)throw Error('副本服务退出 '+log);await new Promise(r=>setTimeout(r,100));}
 context=await chromium.launchPersistentContext(path.join(E,'browser/profile'),{headless:true,channel:'chrome',viewport:{width:1440,height:1000},args:['--disable-background-networking','--disable-component-update']});
 await context.route('**/*',route=>new URL(route.request().url()).origin===base?route.continue():route.abort());
 const page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));
 const get=async params=>{let r=await context.request.get(base+'/api/today?channel=interest&range=today&view=summary&audit=0'+params);assert.equal(r.status(),200);return r.json()};
 const wait=async()=>{await page.waitForFunction(()=>!document.querySelector('#item-list').inert);await page.waitForTimeout(100)};
 await page.goto(base+'/today.html?channel=interest');await page.locator('#item-list .item-card').first().waitFor();await wait();
 const all=await get(''),expected=all.items.filter(p=>p.input_sources.some(s=>s.includes('兴趣搜索')));
 check('真实采集的抖音小红书经过完整兴趣管线',expected.some(p=>p.platform==='douyin')&&expected.some(p=>p.platform==='xhs'),{backend:all.total});
 check('首次抵达与规范身份输出',expected.every(p=>p.first_seen_at&&p.canonical_key===p.id&&p.interest_matches.length));
 check('列表无重复规范身份',new Set(all.items.map(p=>p.id)).size===all.items.length);
 await page.screenshot({path:path.join(E,'browser/all.png'),fullPage:true});
 for(const platform of ['douyin','xhs']){
  await page.locator('#platform-filters [data-platform="'+platform+'"]').click();await wait();
  const api=await get('&platform='+platform),ids=await page.locator('#item-list .item-card').evaluateAll(ns=>ns.map(n=>n.dataset.itemId));
  check('平台筛选与后端计数一致 '+platform,ids.length===api.total&&ids.every(id=>id.startsWith(platform+':')),{ui:ids.length,backend:api.total});
  check('卡片平台图标和方向 '+platform,await page.locator('#item-list .item-card svg').count()>0&&await page.locator('#item-list .item-card').first().textContent().then(t=>/ComfyUI|本地部署/.test(t)));
  await page.screenshot({path:path.join(E,'browser/'+platform+'.png'),fullPage:true});
  await page.locator('#platform-filters [data-platform="'+platform+'"]').click();await wait();
 }
 const topic='visual-models';await page.locator('#topic-buttons [data-topic="'+topic+'"]').click();await wait();
 let r=await get('&topic='+topic),n=await page.locator('#item-list .item-card').count();check('方向筛选与后端计数一致',r.total===n&&n>0,{ui:n,backend:r.total});
 await page.screenshot({path:path.join(E,'browser/topic.png'),fullPage:true});
 await page.setViewportSize({width:390,height:844});if(await page.locator('#today-todo-close').isVisible())await page.locator('#today-todo-close').click();await page.screenshot({path:path.join(E,'browser/mobile.png'),fullPage:true});await page.setViewportSize({width:1440,height:1000});
 await page.locator('#topic-buttons [data-topic="'+topic+'"]').click();await wait();
 await page.locator('#q').fill('绝不命中的隔离空态词_xyz_20261104');await page.locator('#q').press('Enter');await page.waitForFunction(()=>document.querySelectorAll('#item-list .item-card').length===0);await wait();
 check('无结果空态', (await get('&q='+encodeURIComponent('绝不命中的隔离空态词_xyz_20261104'))).total===0);await page.screenshot({path:path.join(E,'browser/empty.png'),fullPage:true});
 const cfgpath=path.join(REPO,'scripts/interest_video_search_config.json'),original=fs.readFileSync(cfgpath);
 try{
  let cfg=JSON.parse(original);cfg.directions[0].name+='错误';fs.writeFileSync(cfgpath,JSON.stringify(cfg));await page.goto(base+'/today.html?channel=interest');await page.locator('#sources button').filter({hasText:'兴趣视频搜索'}).first().waitFor();await page.locator('#sources button').filter({hasText:'兴趣视频搜索'}).first().click();
  check('方向配置错误在页面状态行可见',await page.locator('dialog').textContent().then(t=>t.includes('方向名称与当前方向不一致')));await page.screenshot({path:path.join(E,'browser/config-error.png'),fullPage:true});
 }finally{fs.writeFileSync(cfgpath,original)}
 check('浏览器无脚本错误',errors.length===0,{errors});
 }catch(e){results.push({passed:false,error:String(e)});process.exitCode=1}finally{if(context)await context.close();if(server)server.kill('SIGTERM');fs.writeFileSync(path.join(E,'browser/results.json'),JSON.stringify(results,null,2));console.log(JSON.stringify(results))}})();
