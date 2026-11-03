'use strict';
// 只读真实轮次的 /tmp 副本；不访问生产服务或用户浏览器。
const fs=require('fs'),path=require('path'),os=require('os'),assert=require('assert/strict'),{spawn}=require('child_process');
const {chromium}=require(path.join(os.homedir(),'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright'));
const repo=path.resolve(__dirname,'../..'),out=process.argv[2];assert(out&&path.resolve(out).startsWith('/tmp/'),'证据目录必须在 /tmp');
const checks=[],errors=[];let context,server,log='';const check=(name,value)=>{assert(value,name);checks.push(name);console.log('通过 '+name)};
(async()=>{try{
fs.mkdirSync(out,{recursive:true});fs.writeFileSync(path.join(out,'bundle.json'),'{"tweets":[],"vip_tweets":[]}');fs.rmSync(path.join(out,'port.txt'),{force:true});
server=spawn('/Users/huangzongning/development/article-importer/.venv/bin/python',['-u',path.join(repo,'tests/grok_radar_browser_server.py'),out],{cwd:repo,env:{...process.env,GROK_RADAR_TEST_NOW:'2026-11-03T15:00:00+00:00',PYTHONDONTWRITEBYTECODE:'1'},stdio:['ignore','pipe','pipe']});server.stdout.on('data',b=>log+=b);server.stderr.on('data',b=>log+=b);
for(let i=0;i<100&&!fs.existsSync(path.join(out,'port.txt'));i++){if(server.exitCode!==null)throw Error(log);await new Promise(r=>setTimeout(r,100))}
const base='http://127.0.0.1:'+fs.readFileSync(path.join(out,'port.txt'),'utf8').trim();context=await chromium.launchPersistentContext(path.join(out,'browser-profile'),{headless:true,channel:'chrome',viewport:{width:1440,height:960},args:['--disable-background-networking','--disable-component-update']});
await context.addInitScript(()=>{const Original=Date,fixed=Date.parse('2026-11-03T15:00:00Z');globalThis.Date=class extends Original{constructor(...args){super(...(args.length?args:[fixed]))}static now(){return fixed}}});
await context.route('**/*',r=>new URL(r.request().url()).origin===base&&['GET','HEAD'].includes(r.request().method())?r.continue():r.abort());
const page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));await page.goto(base+'/today.html?range=today');await page.waitForFunction(()=>document.querySelector('#item-list')?.getAttribute('aria-busy')==='false'&&!document.querySelector('#item-list').inert);
const listing=await(await context.request.get(base+'/api/today?range=today')).json();
check('真实baseline来源正常且1轮0坏文件',listing.sources['grok:radar'].status==='ok'&&listing.sources['grok:radar'].round_count===1&&listing.sources['grok:radar'].bad_files===0);
check('414观察按旧门槛47条再经今日闸门展示39条',listing.filtering.by_source['Grok 推特雷达'].total===47&&listing.total===39);
check('截断未知轻提示可见且未标异常',(await page.locator('#sources > .source-notice').innerText()).includes('截断情况未知')&&await page.locator('#sources > button').filter({hasText:'Grok 推特雷达'}).count()===0);
check('baseline没有制造规则事件',listing.items.every(r=>r.radar_events.length===0));
await page.locator('.item-card').first().locator('.item-summary').click();await page.locator('#reader .item-detail').waitFor();check('真实观察详情有原文',(await page.locator('#reader .full-text').innerText()).trim().length>10);
await page.screenshot({path:path.join(out,'real-baseline-1440.png')});await page.setViewportSize({width:390,height:844});await page.screenshot({path:path.join(out,'real-baseline-390.png')});check('手机布局无水平溢出',await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));check('无前端脚本异常',errors.length===0);
fs.writeFileSync(path.join(out,'browser-results.json'),JSON.stringify({checks,errors,base,total:listing.total,source:listing.sources['grok:radar']},null,2));
}finally{fs.writeFileSync(path.join(out,'browser-server.log'),log);if(context)await context.close();if(server)server.kill('SIGTERM')}})().catch(e=>{console.error(e);process.exitCode=1});
