'use strict';
const fs=require('fs'),path=require('path'),os=require('os'),assert=require('assert/strict'),{spawn}=require('child_process');
const {chromium}=require(path.join(os.homedir(),'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright'));
const repo=path.resolve(__dirname,'../..'),out=process.env.GROK_RADAR_EVIDENCE || fs.mkdtempSync('/tmp/grok-radar-browser-');
const py='/Users/huangzongning/development/article-importer/.venv/bin/python';
fs.mkdirSync(out,{recursive:true});fs.mkdirSync(path.join(out,'reports/radar'),{recursive:true});fs.mkdirSync(path.join(out,'workspace'),{recursive:true});fs.writeFileSync(path.join(out,'bundle.json'),'{"tweets":[],"vip_tweets":[]}');
const fixture=path.join(repo,'tests/fixtures/grok_radar'),radar=path.join(out,'reports/radar');const put=name=>fs.copyFileSync(path.join(fixture,name),path.join(radar,name));
let server,context,base,log='',errors=[],checks=[];
function check(name,ok){assert(ok,name);checks.push(name);console.log('通过 '+name)}
async function ready(p){await p.waitForFunction(()=>document.querySelector('#item-list')?.getAttribute('aria-busy')==='false'&&!document.querySelector('#item-list').inert);assert.equal(await p.locator('#item-list > .error').count(),0)}
async function get(url){const r=await context.request.get(base+url);assert.equal(r.status(),200);return r.json()}
(async()=>{try{
 server=spawn(py,['-u',path.join(repo,'tests/grok_radar_browser_server.py'),out],{cwd:repo,env:{...process.env,PYTHONDONTWRITEBYTECODE:'1',ARTICLE_STUDIO_MODEL_CONFIG:path.join(out,'absent.json')},stdio:['ignore','pipe','pipe']});server.stdout.on('data',b=>log+=b);server.stderr.on('data',b=>log+=b);
 for(let i=0;i<100&&!fs.existsSync(path.join(out,'port.txt'));i++){if(server.exitCode!==null)throw Error(log);await new Promise(r=>setTimeout(r,100));}base='http://127.0.0.1:'+fs.readFileSync(path.join(out,'port.txt'),'utf8').trim();
 context=await chromium.launchPersistentContext(path.join(out,'profile'),{headless:true,channel:'chrome',viewport:{width:1440,height:960},args:['--disable-background-networking','--disable-component-update']});
 await context.addInitScript(()=>{const Original=Date,fixed=Date.parse('2026-11-03T12:00:00Z');globalThis.Date=class extends Original{constructor(...args){super(...(args.length?args:[fixed]))}static now(){return fixed}}});
 await context.route('**/*',r=>{if(new URL(r.request().url()).origin===base&&['GET','HEAD'].includes(r.request().method()))return r.continue();return r.abort()});
 const page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));await page.goto(base+'/today.html?range=today');await ready(page);
 check('没有轮次时显示等待产出',(await page.locator('#sources').innerText()).includes('Grok 推特雷达 暂无 · 等待产出'));check('AIsa 停用原因可见',(await page.locator('#sources').innerText()).includes('已停用'));
 await page.screenshot({path:path.join(out,'waiting-1440.png')});
 for(const name of ['2026-11-03-0800-baseline.json','2026-11-03-1000-valid.json','2026-11-03-1000-repeat.json','2026-11-03-1200-null.json','state.json','targets-copy.json','manifest.json','2026-11-03-1800-half.json.tmp'])put(name);
 await page.reload();await ready(page);let data=await get('/api/today?range=today');
 check('真实发现入口读取观察、重复 slot 去重',data.items.filter(p=>p.id==='x:90001').length===1&&data.items.some(p=>p.id==='x:90002'));
 check('正常状态与更新时间',data.sources['grok:radar'].status==='ok'&&!!data.sources['grok:radar'].updated_at);
 check('起势事件和方向分类',data.items.find(p=>p.id==='x:90001').tier==='起势'&&data.items.find(p=>p.id==='x:90001').topic);
 check('null 指标与粉丝保持未知',data.items.find(p=>p.id==='x:90002').metrics.views===null&&data.items.find(p=>p.id==='x:90002').author.followers===null);
 let card=page.locator('.item-card[data-item-id="x:90001"]');await card.locator('.item-summary').click();await page.locator('#reader .item-detail[data-item-id="x:90001"]').waitFor();
 check('浏览器点击详情展开原文',(await page.locator('#reader .full-text').innerText()).includes('AI Agent 开源工作流'));check('详情精确指标8000',(await get('/api/today/item?id=x:90001')).metrics.views===8000);
 await page.screenshot({path:path.join(out,'normal-1440.png')});
 await page.locator('#more-filters').evaluate(n=>n.open=true);await page.locator('[data-source="G"]').click();await ready(page);check('Grok 旧来源筛选包含新雷达',await page.locator('.item-card[data-item-id="x:90001"]').count()===1);
 for(const name of ['2026-11-03-1400-incomplete.json','2026-11-03-1600-bad.json','2026-11-03-2000-schema.json'])put(name);
 await page.goto(base+'/today.html?range=today');await ready(page);data=await get('/api/today?range=today');
 check('坏JSON、schema和不完整轮次如实标异常',data.sources['grok:radar'].status==='error'&&data.sources['grok:radar'].error.includes('不完整')&&data.sources['grok:radar'].bad_files===3);
 const err=page.locator('#sources > button').filter({hasText:'Grok 推特雷达'});await err.click();await page.locator('#source-error').waitFor({state:'visible'});check('来源异常弹窗具体列出坏轮次',(await page.locator('#source-error').innerText()).includes('1600-bad.json'));
 await page.screenshot({path:path.join(out,'error-1440.png')});await page.locator('#source-error button').last().click();await page.setViewportSize({width:390,height:844});await page.screenshot({path:path.join(out,'error-390.png')});
 check('手机页面无水平溢出',await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));check('无浏览器脚本异常',errors.length===0);
 fs.writeFileSync(path.join(out,'results.json'),JSON.stringify({checks,errors,base,runtime:JSON.parse(fs.readFileSync(path.join(out,'runtime-code.json')))},null,2));console.log('证据 '+out);
 }finally{fs.writeFileSync(path.join(out,'server.log'),log);if(context)await context.close();if(server)server.kill('SIGTERM');}
})().catch(e=>{console.error(e);process.exitCode=1});
