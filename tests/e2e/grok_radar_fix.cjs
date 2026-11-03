'use strict';
// M1/M2 定向验收：独立 profile、随机端口、真实 Handler/Store，禁止外网和生产端口。
const fs=require('fs'),path=require('path'),os=require('os'),assert=require('assert/strict'),{spawn}=require('child_process');
const {chromium}=require(path.join(os.homedir(),'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright'));
const repo=path.resolve(__dirname,'../..'),out=path.resolve(process.argv[2]||''),real=path.resolve(process.argv[3]||'');
assert(out.startsWith('/tmp/')&&real.startsWith('/tmp/'),'输入和证据只用 /tmp');
const fixture=path.join(repo,'tests/fixtures/grok_radar'),checks=[],errors=[],results=[];
const check=(name,value)=>{assert(value,name);checks.push(name);console.log('通过 '+name)};
async function scenario(name,prepare,run){
 const folder=path.join(out,name);fs.mkdirSync(path.join(folder,'reports/radar'),{recursive:true});
 fs.writeFileSync(path.join(folder,'bundle.json'),'{"tweets":[],"vip_tweets":[]}');prepare(folder);
 let server,context,log='';try{
 server=spawn('/Users/huangzongning/development/article-importer/.venv/bin/python',['-B','-u',path.join(repo,'tests/grok_radar_browser_server.py'),folder],{cwd:repo,env:{...process.env,GROK_RADAR_TEST_NOW:'2026-11-03T15:00:00+00:00',ARTICLE_STUDIO_MODEL_CONFIG:path.join(folder,'absent.json')},stdio:['ignore','pipe','pipe']});
 server.stdout.on('data',b=>log+=b);server.stderr.on('data',b=>log+=b);
 for(let i=0;i<100&&!fs.existsSync(path.join(folder,'port.txt'));i++){if(server.exitCode!==null)throw Error(log);await new Promise(r=>setTimeout(r,100))}
 const base='http://127.0.0.1:'+fs.readFileSync(path.join(folder,'port.txt'),'utf8').trim();assert(!base.endsWith(':8766'));
 context=await chromium.launchPersistentContext(path.join(folder,'profile'),{channel:'chrome',headless:true,viewport:{width:1440,height:960},args:['--disable-background-networking','--disable-component-update']});
 await context.addInitScript(()=>{const Original=Date,fixed=Date.parse('2026-11-03T15:00:00Z');globalThis.Date=class extends Original{constructor(...args){super(...(args.length?args:[fixed]))}static now(){return fixed}}});
 await context.route('**/*',r=>new URL(r.request().url()).origin===base&&['GET','HEAD'].includes(r.request().method())?r.continue():r.abort());
 const page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));
 const ready=()=>page.waitForFunction(()=>document.querySelector('#item-list')?.getAttribute('aria-busy')==='false'&&!document.querySelector('#item-list').inert);
 const api=async channel=>{const response=await context.request.get(base+'/api/today?range=today'+(channel==='interest'?'&channel=interest&view=summary&audit=0':''));assert.equal(response.status(),200);return response.json()};
 const pollInterest=async predicate=>{let data;for(let i=0;i<100;i++){data=await api('interest');if(data.interest_cache?.ready&&predicate(data))return data;await new Promise(r=>setTimeout(r,100))}throw Error('兴趣状态未就绪 '+JSON.stringify(data.sources))};
 await page.goto(base+'/today.html?range=today');await ready();
 await run({folder,page,ready,api,pollInterest});
 results.push({name,base,runtime:JSON.parse(fs.readFileSync(path.join(folder,'runtime-code.json')))});
 }finally{if(context)await context.close();if(server){server.kill('SIGTERM');await new Promise(resolve=>server.exitCode!==null?resolve():server.once('exit',resolve))}fs.writeFileSync(path.join(folder,'server.log'),log)}
}
(async()=>{fs.mkdirSync(out,{recursive:true});try{
 await scenario('waiting',()=>{},async({page,ready,api,pollInterest,folder})=>{
 check('M2 发现API等待产出',(await api('hot')).sources['grok:radar'].status==='waiting');
 await page.locator('[data-channel="interest"]').click();await ready();const data=await pollInterest(d=>d.sources['grok:radar']?.status==='waiting');
 await page.reload();await ready();check('M2 兴趣API无帖子也有等待状态',data.sources['grok:radar'].updated_at===null&&data.sources['grok:radar'].error===null);
 check('M2 兴趣顶部显示等待产出',(await page.locator('#sources').innerText()).includes('Grok 推特雷达 暂无 · 等待产出'));await page.screenshot({path:path.join(folder,'interest-waiting.png')});
 });
 await scenario('complete',folder=>fs.copyFileSync(path.join(fixture,'2026-11-03-0800-baseline.json'),path.join(folder,'reports/radar/2026-11-03-0800-baseline.json')),async({page,ready,pollInterest,folder})=>{
 await page.locator('[data-channel="interest"]').click();await ready();const data=await pollInterest(d=>d.sources['grok:radar']?.status==='ok');await page.reload();await ready();
 check('M2 兴趣正常API含最后更新时间',data.sources['grok:radar'].complete_coverage===true&&!!data.sources['grok:radar'].updated_at);
 await page.locator('#sources .source-overview').evaluate(n=>n.open=true);check('M2 兴趣顶部正常文字',(await page.locator('#sources').innerText()).includes('Grok 推特雷达 08:05 · 正常'));await page.screenshot({path:path.join(folder,'interest-normal.png')});
 });
 await scenario('retry-unknown',folder=>{
 const radar=path.join(folder,'reports/radar');fs.copyFileSync(path.join(fixture,'2026-11-03-0800-baseline.json'),path.join(radar,'2026-11-03-0800-baseline.json'));
 const template=JSON.parse(fs.readFileSync(path.join(fixture,'2026-11-03-1000-valid.json')));
 for(const [name,time,views,status] of [['earlier','2026-11-03T02:00:00Z',11111,'incomplete'],['later','2026-11-03T02:01:00Z',99999,'ok']]){
 const data=structuredClone(template);Object.assign(data,{run_id:name,round_started_at:time,finished_at:time,events:[],status,all_groups_complete:status==='ok'});
 data.accounts.forEach(a=>a.truncated=status==='ok'?1:false);Object.assign(data.observations[0],{views,fetched_at:time});fs.writeFileSync(path.join(radar,'2026-11-03-1000-'+name+'.json'),JSON.stringify(data));
 }},async({folder,page,api})=>{
 const data=await api('hot');const row=data.items.find(p=>p.id==='x:90001');check('M1 浏览器入口选择稍后成功未知指标99999',row?.metrics.views===99999);
 check('M1 成功未知保持浅色状态及无事件',data.sources['grok:radar'].status==='ok'&&data.sources['grok:radar'].complete_coverage===null&&row.radar_events.length===0&&await page.locator('#sources > .source-notice').filter({hasText:'截断情况未知'}).isVisible());
 await page.locator('.item-card[data-item-id="x:90001"] .item-summary').click();await page.locator('#reader .item-detail').waitFor();check('M1 真实详情接口指标99999',(await (await page.request.get(new URL('/api/today/item?id=x:90001',page.url()).href)).json()).metrics.views===99999);await page.screenshot({path:path.join(folder,'retry-unknown.png')});
 });
 await scenario('real',folder=>{fs.copyFileSync(real,path.join(folder,'reports/radar',path.basename(real)));fs.mkdirSync(path.join(folder,'reports/daily'));fs.writeFileSync(path.join(folder,'reports/daily/2026-11-03-0800.json'),JSON.stringify({collected_at:'2026-11-03T00:00:00Z',posts:[]}))},async({folder,page,ready,api,pollInterest})=>{
 const hot=await api('hot');check('真实发现47候选经今日闸门后39',hot.filtering.by_source['Grok 推特雷达'].total===47&&hot.total===39);
 check('真实发现API截断未知',hot.sources['grok:radar'].status==='ok'&&hot.sources['grok:radar'].complete_coverage===null);
 await page.locator('[data-channel="interest"]').click();await ready();let data=await pollInterest(d=>d.sources['grok:radar']?.status==='ok');await page.reload();await ready();
 check('真实兴趣API提供完整雷达状态',JSON.stringify(data.sources['grok:radar'])===JSON.stringify(hot.sources['grok:radar']));
 const notice=page.locator('#sources > .source-notice').filter({hasText:'Grok 推特雷达'});check('真实兴趣顶部截断未知浅色提示可见',await notice.isVisible()&&(await notice.innerText()).includes('截断情况未知')&&await notice.evaluate(n=>n.style.color==='var(--c-muted)'&&!n.matches('button,.status-warning')));
 check('真实兴趣存在雷达条目和首次抵达时间',data.items.some(p=>p.input_sources?.includes('Grok 推特雷达'))&&data.items.filter(p=>p.input_sources?.includes('Grok 推特雷达')).every(p=>!!p.first_seen_at));
 await page.screenshot({path:path.join(folder,'interest-unknown-1440.png')});await page.setViewportSize({width:390,height:844});await page.screenshot({path:path.join(folder,'interest-unknown-390.png')});check('兴趣手机布局无横向溢出',await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));await page.setViewportSize({width:1440,height:960});
 fs.copyFileSync(path.join(fixture,'2026-11-03-1600-bad.json'),path.join(folder,'reports/radar/2026-11-03-1600-bad.json'));
 const bad=await pollInterest(d=>d.sources['grok:radar']?.status==='error');await page.reload();await ready();
 check('坏文件兴趣API显示雷达异常',bad.sources['grok:radar'].error.includes('1600-bad.json'));
 for(const key of Object.keys(data.sources).filter(k=>!['grok:radar','跨来源索引'].includes(k)))assert.deepEqual(bad.sources[key],data.sources[key]);check('坏文件不影响其他兴趣来源',true);
 const err=page.locator('#sources > button').filter({hasText:'Grok 推特雷达'});check('坏文件兴趣顶部显示异常',await err.isVisible()&&(await err.innerText()).includes('异常'));await err.click();await page.locator('#source-error').waitFor({state:'visible'});check('兴趣异常弹窗给出坏文件名',(await page.locator('#source-error').innerText()).includes('1600-bad.json'));await page.locator('#source-error button').last().click();await page.screenshot({path:path.join(folder,'interest-error.png')});
 await page.locator('[data-channel="hot"]').click();await ready();const badHot=await api('hot');check('坏文件发现API显示雷达异常',badHot.sources['grok:radar'].status==='error'&&badHot.sources['grok:radar'].error.includes('1600-bad.json'));
 check('坏文件发现顶部异常',await page.locator('#sources > button').filter({hasText:'Grok 推特雷达'}).isVisible());
 for(const key of Object.keys(hot.sources).filter(k=>k!=='grok:radar'))assert.deepEqual(badHot.sources[key],hot.sources[key]);check('坏文件不影响其他发现来源',true);
 check('坏文件保留原有发现条目',badHot.total===39);await page.screenshot({path:path.join(folder,'discovery-error.png')});
 fs.writeFileSync(path.join(folder,'api-sources.json'),JSON.stringify({hot:hot.sources,interest:data.sources,badHot:badHot.sources,badInterest:bad.sources},null,2));
 });
 check('浏览器无脚本错误',errors.length===0);
 }finally{fs.writeFileSync(path.join(out,'results.json'),JSON.stringify({checks,errors,results},null,2))}})().catch(e=>{console.error(e);process.exitCode=1});
