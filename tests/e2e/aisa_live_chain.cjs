'use strict';
const fs=require('node:fs'),path=require('node:path'),os=require('node:os'),assert=require('node:assert/strict'),{spawn}=require('node:child_process'),readline=require('node:readline');
const ROOT=path.resolve(__dirname,'../..'),MODE=process.env.AISA_CHAIN_MODE||'fake',PY=process.env.AISA_CHAIN_PYTHON||path.resolve(ROOT,'../article-importer/.venv/bin/python');
function pw(){try{return require('playwright');}catch{return require(path.join(os.homedir(),'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright'));}}
(async()=>{
 const dir=process.env.AISA_CHAIN_EVIDENCE;if(!dir)throw Error('请设置临时或交付证据目录');fs.mkdirSync(dir,{recursive:true});
 const env={...process.env,AISA_API_KEY:''};for(const k of Object.keys(env))if(/^(http_proxy|https_proxy|all_proxy)$/i.test(k))delete env[k];
 const fixture=path.join(__dirname,'aisa_live_chain_fixture.py');
 const args=MODE==='live'?[process.env.AISA_CHAIN_KEY_LAUNCHER,fixture,'--mode',MODE]:[fixture,'--mode',MODE,...(MODE==='view'?['--workspace',process.env.AISA_CHAIN_VIEW_WORKSPACE]:[])];
 if(args.some(x=>!x))throw Error('真实模式需要只读进程环境启动器');
 const child=spawn(PY,args,{cwd:ROOT,env,stdio:['ignore','pipe','pipe']});let stderr='',browser;
 child.stderr.on('data',x=>stderr+=x);const lines=readline.createInterface({input:child.stdout});
 try{
  const config=await new Promise((resolve,reject)=>{const timer=setTimeout(()=>reject(Error('夹具启动超时')),10000);lines.once('line',x=>{clearTimeout(timer);resolve(JSON.parse(x));});child.once('exit',code=>{clearTimeout(timer);reject(Error('夹具退出'+code));});});
  assert.notEqual(new URL(config.base).port,'8766');
  browser=await pw().chromium.launch({channel:'chrome',headless:true,args:['--disable-background-networking','--disable-component-update']});
  const ctx=await browser.newContext({viewport:{width:1360,height:1000}}),page=await ctx.newPage();const errors=[],outside=[],badHTTP=[],posts=[];
  page.on('pageerror',e=>errors.push(e.message));page.on('response',r=>{if(r.status()>=500)badHTTP.push(r.url());});
  page.on('request',r=>{if(r.method()==='POST'&&r.url().includes('/api/collect/aisa/run'))posts.push(JSON.parse(r.postData()));});
  await ctx.route('**/*',route=>{
   const u=new URL(route.request().url());if(u.origin!==config.base){outside.push(u.origin);return route.abort();}
   if(u.pathname==='/api/schedules')return route.fulfill({status:200,contentType:'application/json',body:'{"tasks":[],"models":[]}'});
   if(u.pathname==='/api/knowledge')return route.fulfill({status:200,contentType:'application/json',body:'{"sources":[]}'});
   if(u.pathname==='/api/clues/wechat/status')return route.fulfill({status:200,contentType:'application/json',body:'{"configured":false,"progress":{"run_id":null}}'});
   return route.continue();
  });
  await page.goto(config.base+'/clues.html?source=aisa');if(MODE==='view')await page.waitForFunction(()=>parseInt(document.querySelector('#cl-count').textContent)>0);else await page.locator('#cl-count').filter({hasText:'0 条线索'}).waitFor();
  await page.click('#cl-aisa-open');const receipts=[];
  async function run(platform){
   await page.selectOption('#cl-aisa-platform',platform);await page.fill('#cl-aisa-limit','5');
   if(platform==='twitter')await page.fill('#cl-aisa-handles',MODE==='live'?'gkxspace':'test_user');
   const response=page.waitForResponse(r=>r.url().endsWith('/api/collect/aisa/run')&&r.request().method()==='POST');
   // 同一事件回合双击，按钮的同步锁必须阻止第二次POST。
   await page.dblclick('#cl-aisa-run');
   const accepted=await response;assert.equal(accepted.status(),202);
   await page.waitForFunction(()=>!document.querySelector('#cl-aisa-run').disabled&&!document.querySelector('#cl-aisa-status').textContent.includes('正在'),{},{timeout:50000});
   const job=await (await page.request.get(config.base+'/api/collect/aisa/job')).json();
   receipts.push(job);assert.equal(job.status,'done',JSON.stringify({status:job.status,errors:job.errors}));
   assert.ok(job.added+job.duplicates>0);assert.equal(await page.locator('#cl-source').inputValue(),'aisa');
  }
  if(MODE!=='view')for(const platform of ['twitter','douyin','xiaohongshu'])await run(platform);
  else await page.locator('#cl-aisa-status').filter({hasText:'采集完成'}).waitFor();
  const stats=await (await page.request.get(config.base+'/__fixture/stats')).json();
  assert.equal(stats.calls.length,MODE==='view'?0:3);assert.equal(posts.length,MODE==='view'?0:3);assert.ok(stats.listing.items.every(x=>x.sources.includes('aisa')));
  const tweet=stats.listing.items.find(x=>x.source_platforms.includes('twitter'));assert.ok(tweet.source_time&&tweet.source_time.endsWith('+00:00'));
  await page.locator(`[data-clue="${tweet.id}"]`).click();await page.locator('#cl-detail .cl-evidence a').waitFor();
  const link=await page.locator('#cl-detail .cl-evidence a').first().getAttribute('href');assert.match(link,/^https:\/\/x\.com\//);
  if(MODE==='fake')assert.match(await page.locator('#cl-detail').textContent(),/2026\/10\/13 02:30:00/);
  await page.screenshot({path:path.join(dir,MODE+'-twitter.png'),fullPage:true});
  const xhs=stats.listing.items.find(x=>x.source_platforms.includes('xiaohongshu'));assert.ok(xhs.title&&!xhs.title.endsWith('(热度 0)'));
  await page.locator(`[data-clue="${xhs.id}"]`).click();await page.locator('#cl-detail').filter({hasText:'仅 App 创作入口'}).waitFor();
  assert.equal(await page.locator('#cl-detail a[href^="xhsdiscover:"]').count(),0);
  let duplicate=null,failure=null,malformed=null,empty=null,manual=null;
  if(MODE==='fake'){
   await page.request.get(config.base+'/__fixture/metrics');await run('xiaohongshu');duplicate=receipts.at(-1);
   assert.equal(duplicate.added,0);assert.equal(duplicate.duplicates,1);assert.equal(duplicate.updated,1);
   await page.locator(`[data-clue="${xhs.id}"]`).click();await page.locator('#cl-detail').filter({hasText:'987600'}).waitFor();
   const unchanged=await (await page.request.get(config.base+'/api/clues?source=aisa')).json();assert.equal(unchanged.total,stats.listing.total);
   // M1：人工改过的标题/摘要要持续存活——第二次及以后的指标刷新不能把它覆盖回自动生成值。
   manual={before:xhs.title};
   await page.locator(`[data-clue="${xhs.id}"]`).click();await page.locator('#cl-detail [data-detail="edit"]').click();
   await page.fill('#cl-title','人工标题');await page.fill('#cl-summary','人工摘要');await page.fill('#cl-reason','人工整理');
   await page.click('#cl-form button[type=submit]');await page.locator('#cl-detail').filter({hasText:'人工标题'}).waitFor();
   for(const score of ['111222','333444']){
    await page.request.get(config.base+'/__fixture/metrics?score='+score);await run('xiaohongshu');
    const receipt=receipts.at(-1);assert.equal(receipt.updated,1);
    const card=await page.locator(`[data-clue="${xhs.id}"] h3`).textContent();assert.equal(card,'人工标题',`第 ${score} 次刷新覆盖了人工标题`);
    manual[score]={title:card,receipt};
   }
   await page.screenshot({path:path.join(dir,MODE+'-manual-refresh.png'),fullPage:true});
   await page.request.get(config.base+'/__fixture/failure');await page.selectOption('#cl-aisa-platform','douyin');await page.click('#cl-aisa-run');
   await page.locator('#cl-aisa-status').filter({hasText:'采集失败'}).waitFor();assert.match(await page.locator('#cl-aisa-status').textContent(),/鉴权失败/);
   failure=await (await page.request.get(config.base+'/api/collect/aisa/job')).json();assert.equal(failure.added,0);
   // M2：非空但全部不可解析的抖音列表要报格式错误；合法空数组仍按 0 条成功展示。两者页面提示必须不同。
   async function runDouyin(endpoint){
    await page.request.get(config.base+endpoint);await page.selectOption('#cl-aisa-platform','douyin');await page.fill('#cl-aisa-limit','5');
    const accepted=page.waitForResponse(r=>r.url().endsWith('/api/collect/aisa/run')&&r.request().method()==='POST');
    await page.click('#cl-aisa-run');assert.equal((await accepted).status(),202);
    let job;
    for(let i=0;i<80;i++){job=await (await page.request.get(config.base+'/api/collect/aisa/job')).json();if(!['queued','running'].includes(job.status))break;await new Promise(r=>setTimeout(r,100));}
    return job;
   }
   malformed=await runDouyin('/__fixture/malformed');
   assert.equal(malformed.status,'error');assert.equal(malformed.failed,1);assert.equal(malformed.added,0);assert.match(malformed.errors[0],/格式/);
   await page.waitForFunction(()=>{const t=document.querySelector('#cl-aisa-status').textContent;return !t.includes('正在')&&t.includes('抖音返回内容格式无法解析');});
   assert.match(await page.locator('#cl-aisa-status').textContent(),/采集失败/);
   await page.screenshot({path:path.join(dir,MODE+'-douyin-malformed.png'),fullPage:true});
   empty=await runDouyin('/__fixture/empty');
   assert.equal(empty.status,'done');assert.equal(empty.fetched,0);assert.equal(empty.failed,0);assert.deepEqual(empty.errors,[]);
   await page.waitForFunction(()=>{const t=document.querySelector('#cl-aisa-status').textContent;return !t.includes('正在')&&t.includes('采集完成')&&!t.includes('格式无法解析');});
   const emptyText=await page.locator('#cl-aisa-status').textContent();assert.match(emptyText,/取到 0 条/);assert.doesNotMatch(emptyText,/格式/);
   await page.screenshot({path:path.join(dir,MODE+'-douyin-empty.png'),fullPage:true});
  }else if(MODE==='live')assert.equal(stats.leak_scan.exact_or_fragment_matches,0);
  await page.screenshot({path:path.join(dir,MODE+'-results.png'),fullPage:true});
  await page.setViewportSize({width:390,height:844});await page.screenshot({path:path.join(dir,MODE+'-mobile.png'),fullPage:true});
  assert.deepEqual(errors,[]);assert.deepEqual(outside,[]);assert.deepEqual(badHTTP,[]);
  fs.writeFileSync(path.join(dir,MODE+'-result.json'),JSON.stringify({mode:MODE,base:config.base,workspace:config.workspace,receipts,calls:stats.calls,listing:stats.listing,duplicate,failure,malformed,empty,manual,pageerrors:errors,external:outside,http5xx:badHTTP,post_count:posts.length,leak_scan:stats.leak_scan},null,2));
  console.log('PASS Chrome '+MODE+'：手动采集三平台→线索可见、来源筛选、UTC+8、链接、双击无重复POST'+(MODE==='fake'?'、重复刷新指标/中文失败/抖音格式错误与合法空数组区分':''));
 }finally{if(browser)await browser.close();lines.close();child.kill('SIGTERM');await new Promise(r=>{if(child.exitCode!==null)return r();child.once('exit',r);});fs.writeFileSync(path.join(dir,MODE+'-fixture.log'),stderr);}
})().catch(e=>{console.error(e);process.exitCode=1;});
