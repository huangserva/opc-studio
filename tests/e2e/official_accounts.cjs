'use strict';
// Independent profile; only writes the official-list file on an isolated random-port copy.
const fs=require('node:fs'),path=require('node:path'),os=require('node:os'),assert=require('node:assert/strict');
const {chromium}=require(path.join(os.homedir(),'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright'));
const [base,baseline,out]=process.argv.slice(2);if(!/^http:\/\/127\.0\.0\.1:\d+$/.test(base)||[8766,3000].includes(+new URL(base).port))throw Error('仅限随机端口副本');
fs.mkdirSync(out,{recursive:true});const profile=fs.mkdtempSync(path.join(out,'profile-'));let context;const checks=[],errors=[];const check=(name,ok)=>{assert(ok,name);checks.push(name)};
(async()=>{try{
 context=await chromium.launchPersistentContext(profile,{headless:true,channel:'chrome',viewport:{width:1440,height:1000}});
 await context.route('**/*',route=>{const r=route.request(),u=new URL(r.url());if(![base,baseline].includes(u.origin))return route.abort();if(r.method()!=='GET'&&u.pathname!=='/api/settings/official-accounts')return route.abort();return route.continue()});
 const page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));
 async function view(channel){await page.goto(base+'/today.html?channel='+channel);await page.locator('#item-list[aria-busy="false"]').waitFor();await page.locator('[data-range="7d"]').click();await page.locator('#item-list[aria-busy="false"]').waitFor();}
 async function api(query){return context.request.get(base+'/api/today?range=7d&'+query).then(r=>r.json())}
 const beforeConfig=await context.request.get(base+'/api/settings/official-accounts').then(r=>r.json());
 for(const width of [1440,1280,390]){
  await page.setViewportSize({width,height:1000});await page.goto(baseline+'/today.html');await page.locator('#item-list[aria-busy="false"]').waitFor();await page.screenshot({path:path.join(out,'before-today-'+width+'.png')});
  await view('official');check(width+' 三栏',await page.locator('[data-channel]').allTextContents().then(v=>v.join('/')==='发现/兴趣/官方号'));
  check(width+' 无横向溢出',await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));await page.screenshot({path:path.join(out,'official-'+width+'.png')});
  const cards=page.locator('#item-list .item-card');check(width+' 官方标签',await cards.count()>0&&await cards.first().locator('.item-official').count()===1);
  await page.goto(base+'/settings.html');await page.locator('#official-save:not([disabled])').waitFor();await page.locator('#official-title').scrollIntoViewIfNeeded();await page.screenshot({path:path.join(out,'settings-'+width+'.png')});check(width+' 设置无溢出',await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
 }
 const allOfficial=await api('channel=official');const expected=['factoryai','pika_labs','comfyui','opencode','openclaw','higgsfield'];const handles=allOfficial.items.map(p=>p.author.handle.toLowerCase());for(const h of expected)check('真实名单 '+h,handles.includes(h));
 const droid=allOfficial.items.find(p=>p.author.handle.toLowerCase()==='factoryai'&&/Droid 1.0/.test(p.title));check('真实Droid在官方号',!!droid);
 const hot=await api('q=Droid');check('真实Droid不在发现和top',!hot.items.concat(hot.top).some(p=>p.id===droid.id));
 check('官方号按时间倒序',allOfficial.items.every((p,i)=>i===0||Date.parse(p.published_at)<=Date.parse(allOfficial.items[i-1].published_at)));
 await view('official');await page.reload();await page.locator('#item-list[aria-busy="false"]').waitFor();check('URL刷新保持官方号',await page.locator('[data-channel="official"]').getAttribute('aria-pressed')==='true');
 await view('interest');await page.locator('#q').fill('pika_labs');await page.locator('#filters').evaluate(f=>f.requestSubmit());await page.locator('#item-list[aria-busy="false"]').waitFor();const retainedOfficial=await api('channel=interest&q=pika_labs');check('真实兴趣保留官方帖子身份',retainedOfficial.items.some(p=>p.id==='x:2115860471215788262'&&p.official.is_official));check('真实兴趣保留官方标签',await page.locator('[data-item-id="x:2115860471215788262"] .item-official').count()===1);await page.screenshot({path:path.join(out,'interest-official.png')});
 await page.goto(base+'/settings.html');await page.locator('#official-save:not([disabled])').waitFor();await page.locator('#official-handle').fill('@MistralAI');await page.locator('#official-add button').click();await page.locator('#official-save').click();await page.locator('#official-notice').filter({hasText:'已保存'}).waitFor();
 const mistral=await api('channel=official&q=Mistral');check('设置添加MistralAI无需重启',mistral.items.some(p=>p.author.handle.toLowerCase()==='mistralai'&&p.official.source==='settings_include'));
 await page.locator('[aria-label="删除规则：include mistralai"]').click();await page.locator('#official-save').click();await page.locator('#official-notice').filter({hasText:'已保存'}).waitFor();
 const config=await context.request.get(base+'/api/settings/official-accounts').then(r=>r.json());check('副本测试名单恢复',JSON.stringify(config.include)===JSON.stringify(beforeConfig.include)&&JSON.stringify(config.exclude)===JSON.stringify(beforeConfig.exclude));check('变更有时间账号增删记录',config.history.slice(-2).every(x=>x.at&&x.handle==='mistralai'));
 check('没有页面脚本错误',errors.length===0);fs.writeFileSync(path.join(out,'results.json'),JSON.stringify({checks,errors,real_droid_id:droid.id,official_total:allOfficial.total,source_dates:'real report copy; use 7d to include Shanghai 10-30 Droid post',only_write:'isolated official-account settings'},null,2));console.log(checks.length+' checks passed');
}finally{if(context)await context.close();fs.rmSync(profile,{recursive:true,force:true});}})().catch(e=>{fs.writeFileSync(path.join(out,'failure.json'),JSON.stringify({message:e.message,checks,errors},null,2));console.error(e);process.exitCode=1});
