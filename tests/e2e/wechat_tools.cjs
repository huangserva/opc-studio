'use strict';
// Isolated profile, random-port write tests; live mode permits GET only.
const fs=require('node:fs'),path=require('node:path'),os=require('node:os'),crypto=require('node:crypto');
const {chromium}=(()=>{try{return require('playwright')}catch(_){return require(path.join(os.homedir(),'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright'))}})();
const out=process.argv[2],base=process.argv[3],mode=process.argv[4]||'isolated';
if(!out?.startsWith('/tmp/r3-1/')||!/^http:\/\/127\.0\.0\.1:\d+$/.test(base)||!['isolated','live'].includes(mode))throw Error('验收参数必须是本单隔离目录和本机服务');
if(mode==='isolated'&&base.endsWith(':8766'))throw Error('写入验收禁止连接真实8766');
const sha=v=>crypto.createHash('sha256').update(typeof v==='string'?v:JSON.stringify(v)).digest('hex');
(async()=>{const profile=fs.mkdtempSync('/tmp/r3-1/profile-'),context=await chromium.launchPersistentContext(path.join(profile,'profile'),{headless:true,channel:'chrome',viewport:{width:1440,height:1000},args:['--disable-background-networking']});let writes=false;const errors=[],responses=[];
try{
 await context.route('**/*',route=>{const r=route.request(),u=new URL(r.url());if(u.origin!==base)return route.abort();if(r.method()==='GET')return route.continue();if(mode==='isolated'&&writes&&['/api/today/feedback','/api/settings/wechat-tools'].includes(u.pathname))return route.continue();return route.abort();});
 const page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));page.on('response',r=>{if(r.status()>=400)responses.push({path:new URL(r.url()).pathname,status:r.status()})});
 await page.goto(base+'/today.html?channel=interest',{waitUntil:'domcontentloaded'});await page.locator('#item-list[aria-busy="false"] .item-card').first().waitFor({timeout:30000});
 const tools=page.locator('.item-card').filter({has:page.locator('[data-source="W"]')}),x=page.locator('.item-card').filter({has:page.locator('[data-source="G"]')});
 if(await tools.count()!==6||await x.count()<1)throw Error('兴趣栏缺少真实六张微信卡或X来源');
 if(await page.locator('.tool-collection').filter({hasText:'已进工具'}).count()!==6)throw Error('已进工具标记缺失');
 const shots=[];
 for(const width of [1440,390]){await page.setViewportSize({width,height:1000});await page.waitForTimeout(150);await x.first().evaluate(el=>el.scrollIntoView({block:'center'}));await page.waitForTimeout(100);const visible=await page.locator('.item-card').evaluateAll(nodes=>({W:nodes.some(n=>n.querySelector('[data-source="W"]')&&n.getBoundingClientRect().bottom>270&&n.getBoundingClientRect().top<innerHeight),G:nodes.some(n=>n.querySelector('[data-source="G"]')&&n.getBoundingClientRect().bottom>270&&n.getBoundingClientRect().top<innerHeight)}));if(!visible.W||!visible.G)throw Error('截图未同时显示微信和X卡片');const file=path.join(out,mode+'-interest-'+width+'.png');await page.screenshot({path:file,fullPage:true});shots.push({width,file,sha256:sha(fs.readFileSync(file)),horizontal_overflow:await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1)});}
 const chosenTool=mode==='isolated'?tools.filter({has:page.locator('[data-action="keep"][aria-pressed="false"]')}).first():tools.first();const firstId=await chosenTool.getAttribute('data-item-id');const get=async url=>{const r=await context.request.get(base+url);if(r.status()!==200)throw Error('接口失败 '+r.status());return r.json()};
 const collection=await get('/api/clue-inbox?collection=tools');if(collection.items.length!==6)throw Error('工具集合不是6');const detail=await get('/api/clue-objects/'+encodeURIComponent(firstId));
 const checks={mode,tools:6,x:await x.count(),collection:collection.items.length,shots,source_to_clue_chain:collection.items.map(i=>({clue_id:i.id,resource_count:i.resources.length})),evidence_sha256:sha(detail.evidence)};
 if(mode==='isolated'){
  const before=await get('/api/items');writes=true;const saved=page.waitForResponse(r=>new URL(r.url()).pathname==='/api/today/feedback'&&r.request().method()==='POST');await chosenTool.locator('[data-action="keep"]').click();const response=await saved;if(response.status()!==200)throw Error('显式存失败');await page.waitForTimeout(100);
  const after=await get('/api/items'),updated=await get('/api/clue-objects/'+encodeURIComponent(firstId));if(after.total!==before.total+1)throw Error('素材不是新增1条');if(sha(updated.evidence)!==sha(detail.evidence))throw Error('原始证据发生变化');if(!updated.relations.some(r=>r.relation_type==='additional_destination'))throw Error('没有追加去向');checks.explicit_save={before:before.total,after:after.total,evidence_unchanged:true,additional_destination:true};
  await page.setViewportSize({width:1440,height:1000});await page.goto(base+'/settings.html');await page.locator('#wechat-tools-save:not([disabled])').waitFor();
  const initial=await get('/api/settings/wechat-tools');await page.locator('#wechat-tools-input').fill('合成浏览器精选群');await page.locator('#wechat-tools-add button').click();await page.locator('#wechat-tools-save').click();await page.locator('#wechat-tools-notice').filter({hasText:'精选群已保存'}).waitFor();
  const current=await get('/api/settings/wechat-tools');if(!current.groups.includes('合成浏览器精选群'))throw Error('白名单保存未落盘');
  const external=await page.evaluate(async data=>{const r=await fetch('/api/settings/wechat-tools',{method:'POST',headers:{'Content-Type':'application/json','X-Article-Studio':'1'},body:JSON.stringify({groups:data.groups.filter(g=>g!=='合成浏览器精选群'),revision:data.revision})});return r.status},current);if(external!==200)throw Error('冲突前置保存失败');
  await page.locator('#wechat-tools-save').click();await page.locator('#wechat-tools-notice').filter({hasText:'白名单已被修改'}).waitFor();checks.whitelist={saved:true,conflict_status:409};
  await page.screenshot({path:path.join(out,'isolated-settings.png'),fullPage:true});writes=false;
 }
 if(errors.length)throw Error('页面脚本错误');if(responses.some(r=>!(mode==='isolated'&&r.path==='/api/settings/wechat-tools'&&r.status===409)))throw Error('接口错误');
 if(shots.some(s=>s.horizontal_overflow))throw Error('移动端横向溢出');
 checks.script_errors=errors;checks.http_observations=responses;fs.writeFileSync(path.join(out,mode+'-browser.json'),JSON.stringify(checks,null,2));console.log(JSON.stringify(checks));
}finally{await context.close()}})().catch(error=>{fs.writeFileSync(path.join(out,mode+'-browser-failure.json'),JSON.stringify({error:error.message},null,2));console.error(error.message);process.exitCode=1});
