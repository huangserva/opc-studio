'use strict';
// 独立 /tmp 工作区、随机端口、headless profile；只用合成数据，禁止外网。
const fs=require('fs'),path=require('path'),os=require('os'),net=require('net'),assert=require('assert/strict');
const {spawn}=require('child_process');
const {chromium}=(()=>{try{return require('playwright')}catch(_){return require(path.join(os.homedir(),'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright'))}})();
const ROOT=path.resolve(__dirname,'../..'),PY=path.resolve(ROOT,'../article-importer/.venv/bin/python');
const tmp=fs.mkdtempSync(path.join(path.resolve(ROOT,'..'),'browser-')),results=[],timings=[];
let child,context,base,stderr='';
const check=(name,ok,detail='')=>{assert.ok(ok,name+' '+detail);results.push({name,passed:true,detail});console.log('通过 '+name+' '+detail)};
async function start(){
 const port=await new Promise(resolve=>{const s=net.createServer().listen(0,'127.0.0.1',()=>{const p=s.address().port;s.close(()=>resolve(p))})});base='http://127.0.0.1:'+port;
 const code=`import sys,json,sqlite3;from pathlib import Path;sys.path.insert(0,${JSON.stringify(ROOT)});import server,settings_view,grok_bot_radar,external_tasks;from http.server import ThreadingHTTPServer;from scheduled_tasks import ScheduledTasks;from tests.test_clues import KnowledgeFake;from types import SimpleNamespace
tmp=Path(${JSON.stringify(tmp)});root=tmp/'workspace';root.mkdir();grok_bot_radar.reports_dir=lambda:tmp/'reports'
(tmp/'public').mkdir();grok_bot_radar.BUNDLE_PATH=tmp/'public/twitter-radar-bundle.json';grok_bot_radar.BUNDLE_PATH.write_text(json.dumps({'updated_at':'2026-10-29 22:00:38','collected_at':'2026-10-29T14:00:38.594Z','tweets':[],'vip_tweets':[]}))
(root/'twitter-radar/monitor').mkdir(parents=True);(root/'twitter-radar/monitor/state.json').write_text(json.dumps({'runs':{'2026-10-29T22':{'success':True,'run_id':'20261029-140008-f6d22d61'}}}))
(root/'twitter-radar/snapshots/2026-10-29').mkdir(parents=True);(root/'twitter-radar/snapshots/2026-10-29/20261029-140008-f6d22d61.json').write_text(json.dumps({'run':{'run_id':'20261029-140008-f6d22d61','finished_at':'2026-10-29T14:00:38.594Z'}}))
(root/'intel').mkdir()
with sqlite3.connect(root/'intel/intel.db') as db:
 db.execute('CREATE TABLE jobs(status TEXT,reason TEXT,detail TEXT,started_at TEXT,finished_at TEXT)');db.execute('INSERT INTO jobs VALUES(?,?,?,?,?)',('partial','','后台限流（200013），没有取到列表','2026-10-29T13:00:00Z','2026-10-29T14:00:38Z'))
(tmp/'reports/daily').mkdir(parents=True);(tmp/'reports/daily/fixture.json').write_text(json.dumps({'collected_at':'2026-10-29T08:00:00Z','posts':[]}))
(root/'logs').mkdir();(root/'logs/service.log').write_text('normal fixture line\\nAuthorization: Bearer fixture-auth-secret\\nAISA_API_KEY=sk-fixture-log-secret\\ncookie=fixture-cookie-secret\\n')
(root/'video-radar').mkdir();(root/'video-radar/page.json').write_text(json.dumps({'sources':{'douyin_daily':{'last_success_at':'2026-10-29T09:00:00Z','status':'ok'}}}))
model={'model':'fixture-one','endpoint':'http://127.0.0.1:1/v1/chat/completions','api_key':'test-placeholder-not-a-key','no_api_key':False,'context_tokens':8192,'max_output_tokens':1024,'timeout_seconds':30,'temperature':0.2}
config=tmp/'model.json';config.write_text(json.dumps({'schema_version':2,'active_profile_id':'one','profiles':[{'id':'one','display_name':'合成模型一','config':model},{'id':'two','display_name':'合成模型二','config':dict(model,model='fixture-two')}]}))
s=ThreadingHTTPServer(('127.0.0.1',${port}),server.Handler);s.store=server.Store(root);s.assistant=server.assistant_module.Assistant(s.store,server.content,config);s.schedules=ScheduledTasks(root,s.assistant,autostart=False);s.store.schedules=s.schedules
(tmp/'expected-external-count').write_text(str(len(external_tasks.TASKS)+len(external_tasks.SERVICES)))
t=s.schedules.save(dict(name='GitHub 合成任务',kind='script',body='github_source.py',cwd=str(tmp),frequency='interval',minutes=60,at='08:00',weekdays=list(range(7)),ends_at=None,timeout=5,enabled=True,allow_commands=False));(tmp/'task-id').write_text(t['id'])
with s.schedules.db() as db:db.execute('INSERT INTO runs(id,task_id,snapshot,trigger,status,started_at,finished_at,exit_code,output) VALUES(?,?,?,?,?,?,?,?,?)',('e'*32,t['id'],json.dumps(t),'manual','succeeded','2026-10-29T08:00:00Z','2026-10-29T08:01:00Z',0,'fixture'))
with s.schedules.db() as db:
 for n in range(55):db.execute('INSERT INTO runs(id,task_id,snapshot,trigger,status,started_at,finished_at,exit_code,output) VALUES(?,?,?,?,?,?,?,?,?)',(str(n).zfill(32),t['id'],json.dumps(dict(t,name='分页合成任务 '+str(n))),'manual','failed' if n%2 else 'succeeded','2026-10-28T08:00:00Z','2026-10-28T08:00:01Z',1 if n%2 else 0,'分页合成内容'))
doc={'id':'a'*32,'source':'wechat','kind':'wechat','title':'合成来源','body':'可回看的原文依据','hash':'b'*64,'url':'http://example.test/source','modified':'2026-10-29T00:00:00Z','provenance':{}}
s.store._knowledge=KnowledgeFake({doc['id']:doc});clue=s.store.clues.save({'title':'合成线索','summary':'核对入口与原文','picks':[{'id':doc['id'],'hash':doc['hash'],'start':0,'end':len(doc['body'])}]});(tmp/'clue-id').write_text(clue['id'])
for n in range(13):s.schedules.save(dict(name='总览合成任务 '+str(n),kind='script',body='printf overview',cwd=str(tmp),frequency='daily',at='08:30',weekdays=[0,2,4],timeout=5,enabled=False,allow_commands=False))
external=[dict(id='external-'+str(n),name='外部合成任务 '+str(n),owner='合成来源',schedule='每天 09:00',status='waiting',status_label='等待产出',last_output_at='2026-10-31T01:00:00Z',detail='合成状态',rule='本地合成') for n in range(9)]+[dict(id='missing-fields')]
s.schedules.external_tasks.snapshot=lambda:dict(tasks=external,checked_at='2026-11-01T01:00:00Z')
s.serve_forever()`;
 child=spawn(PY,['-u','-c',code],{cwd:ROOT,stdio:['ignore','ignore','pipe']});child.stderr.on('data',b=>stderr+=b);
 for(let i=0;i<100;i++){try{if((await fetch(base+'/api/settings/overview')).ok)return}catch(_){}if(child.exitCode!==null)throw Error(stderr);await new Promise(r=>setTimeout(r,100))}throw Error('隔离服务未启动 '+stderr);
}
const api=async url=>{const r=await context.request.get(base+url);return {status:r.status(),body:await r.json()}};
async function ready(p){await p.locator('#sources[aria-busy=false]').waitFor();await p.locator('#models [data-profile]').first().waitFor()}
async function failureProbes() {
 const outcomes=[];
 for(const variant of ['503','timeout','external','recovery']) {
  const p=await context.newPage();let failed=true;
  // Exercise real AbortController cancellation quickly; production still has a 8s bound.
  if(variant==='timeout')await p.addInitScript(()=>{const set=window.setTimeout;window.setTimeout=(fn,ms,...args)=>set(fn,ms===8000?75:ms,...args)});
  await p.route('**/api/schedules',async route=>{
   if(variant==='external'){
    const r=await route.fetch(),d=await r.json();d.external={last_error:'合成外部503',checked_at:null};return route.fulfill({json:d});
   }
   if(failed){
    if(variant==='timeout')return; // Headers never arrive: the real client must abort the fetch.
    return route.fulfill({status:503,json:{error:'合成503暂不可读'}});
   }
   return route.continue();
  });
  try {
   await p.goto(base+'/schedules.html');if(variant==='external')await p.locator('#overview-count').filter({hasText:'内置 14'}).waitFor();await p.waitForTimeout(350);
   await p.screenshot({path:path.join(tmp,'fix1-'+variant+'-error.png')});
   const text=await p.locator('.schedule-overview').innerText();
   if(variant==='external'){
    assert.equal(await p.locator('#overview-count').innerText(),'（内置 14 · 外部 未知）');
    assert.equal(await p.locator('[data-overview-task]').count(),14);
    assert.match(await p.locator('[data-external-error]').innerText(),/外部任务读取失败.*刷新重试/);
    assert.equal(await p.locator('#overview-error').count(),0);
   }else{
    assert.equal(await p.locator('#overview-count').innerText(),'（内置 未知 · 外部 未知）');assert.equal(await p.locator('#stat-all').innerText(),'未知');assert(!(await p.locator('#tasks').innerText()).includes('正在读取'));
    assert.match(await p.locator('#overview-tasks').innerText(),/读取失败.*刷新重试/);
    assert.match(await p.locator('#overview-error').innerText(),variant==='timeout'?/超时/:/合成503/);
   }
   assert(!text.includes('正在读取'));assert(await p.locator('#overview-runs button').count()>0);
   if(variant==='recovery'){
    failed=false;await p.locator('#refresh').click();await p.locator('#overview-count').filter({hasText:'内置 14 · 外部 10'}).waitFor();
    assert.equal(await p.locator('#overview-error').count(),0);assert.equal(await p.locator('#stat-all').innerText(),'24');assert(!(await p.locator('.schedule-overview').innerText()).includes('读取失败'));
    await p.screenshot({path:path.join(tmp,'fix1-recovery-restored.png')});
   }
   outcomes.push({variant,passed:true});console.log('通过 fix1 '+variant);
  }catch(error){outcomes.push({variant,passed:false,error:error.name+': '+error.message});console.log('失败 fix1 '+variant+' '+error.name)}
  finally{await p.close()}
 }
 const stale=await context.newPage();let unavailable=false;
 await stale.route('**/api/schedules',r=>unavailable?r.fulfill({status:503,json:{error:'合成刷新503'}}):r.continue());
 try{
  await stale.goto(base+'/schedules.html');await stale.locator('#overview-count').filter({hasText:'内置 14 · 外部 10'}).waitFor();
  unavailable=true;await stale.locator('#refresh').click();await stale.locator('#overview-error').filter({hasText:'以下为上次数据'}).waitFor();
  assert.equal(await stale.locator('[data-overview-task]').count(),14);assert(!(await stale.locator('#tasks').innerText()).includes('正在读取'));assert.equal(await stale.locator('.task-row button:enabled').count(),0);
  await stale.screenshot({path:path.join(tmp,'snapshot-refresh-failed.png')});
  unavailable=false;await stale.locator('#overview-error [data-retry]').click();await stale.locator('#overview-count').filter({hasText:'内置 14 · 外部 10'}).waitFor();assert.equal(await stale.locator('#overview-error').count(),0);
  assert.equal(await stale.locator('#stat-all').innerText(),'24');outcomes.push({variant:'snapshot-recovery',passed:true});console.log('通过 snapshot-recovery');
 }catch(error){outcomes.push({variant:'snapshot-recovery',passed:false,error:error.name+': '+error.message})}finally{await stale.close()}
 fs.writeFileSync(path.join(tmp,'fix1-failure-results.json'),JSON.stringify(outcomes,null,2));
 assert.equal(outcomes.filter(r=>!r.passed).length,0,'失败态、恢复与快照反例必须全过');
 for(const result of outcomes)results.push({name:'fix1 '+result.variant,passed:true});
}
(async()=>{try{
 await start();context=await chromium.launchPersistentContext(path.join(tmp,'profile'),{headless:true,channel:'chrome',viewport:{width:1440,height:1000}});
 await context.route('**/*',r=>new URL(r.request().url()).origin===base?r.continue():r.abort());
 await context.route('**/api/settings/interest-topics',r=>r.fulfill({json:{topics:['合成方向'],revision:'fixture'}}));
 await context.route('**/api/settings/official-accounts',r=>r.fulfill({json:{include:['fixture'],exclude:[],history:[],revision:'fixture'}}));
 await context.route('**/api/settings/wechat-tools',r=>r.fulfill({json:{groups:['合成精选群'],revision:'fixture'}}));
 await failureProbes();
 if(process.env.SCHEDULE_FAILURES_ONLY==='1')return;
 const page=await context.newPage(),errors=[],consoleErrors=[];page.on('pageerror',e=>errors.push(e.message));page.on('console',m=>{if(m.type()==='error')consoleErrors.push(m.text())});
 const beforeHtml=path.resolve(ROOT,'../baseline-settings.html'),beforeJs=path.resolve(ROOT,'../baseline-settings.js');
 // On independent review runs, the current checkout may have no pre-migration snapshot.
 let oldCounts;
 if(fs.existsSync(beforeHtml)&&fs.existsSync(beforeJs)){
  await page.route('**/settings.html',r=>r.fulfill({contentType:'text/html',body:fs.readFileSync(beforeHtml)}));
  await page.route('**/settings.js',r=>r.fulfill({contentType:'text/javascript',body:fs.readFileSync(beforeJs)}));
  await page.goto(base+'/settings.html');await page.locator('#tasks [data-task-row]').first().waitFor();
  oldCounts=await page.locator('#task-count').innerText();assert.equal(oldCounts,'（内置 14 · 外部 10）');
  await page.unroute('**/settings.html');await page.unroute('**/settings.js');
 }
 await page.goto(base+'/schedules.html');await page.locator('#overview-tasks [data-overview-task]').first().waitFor();
 const snapshot=(await api('/api/schedules')).body;
 check('总览与同接口旧设置页计数一致',await page.locator('#overview-count').innerText()===(oldCounts||'（内置 14 · 外部 10）'));
 assert.equal(await page.locator('#overview-tasks [data-overview-task]').count(),snapshot.tasks.length);
 assert.equal(await page.locator('#overview-tasks [data-external-row]').count(),snapshot.external.tasks.length);
 assert.equal(await page.locator('#overview-tasks [data-external-row] button').count(),0);
 check('外部缺失字段明确显示未知',/未知/.test(await page.locator('[data-external-row="missing-fields"]').innerText()));
 check('计划与外部检查时间显示北京时间',/08:30/.test(await page.locator('#overview-tasks').innerText())&&/北京时间/.test(await page.locator('.schedule-overview').innerText())&&/09:00:00/.test(await page.locator('#external-checked').innerText()));
 const nav=page.locator('#workspace-nav');
 assert.deepEqual(await nav.locator(':scope > a').evaluateAll(ns=>ns.map(n=>n.dataset.navItem)),['today','library','works','publish','deals','schedules','settings']);
 check('定时任务在主组且当前唯一高亮',await nav.locator(':scope > a[aria-current="page"][data-nav-item=schedules]').count()===1&&await nav.locator('[aria-current=page]').count()===1);
 await page.screenshot({path:path.join(tmp,'schedules-1440.png')});
 await page.locator('#new-task').click();await page.locator('#editor').waitFor();
 await page.locator('[name=name]').fill('界面合成任务');await page.locator('[name=kind]').selectOption('script');await page.locator('[name=body]').fill('sleep 1.5; printf schedule-ui-fixture');
 await page.locator('#advanced-settings').evaluate(e=>e.open=true);await page.locator('[name=cwd]').fill(tmp);await page.locator('[name=enabled]').uncheck();
 await page.locator('#task-form button[type=submit]').click();await page.locator('#editor').waitFor({state:'hidden'});
 let list=(await api('/api/schedules')).body;const created=list.tasks.find(t=>t.name==='界面合成任务');assert(created&&!created.enabled);const row=page.locator('[data-task-id="'+created.id+'"]');await row.waitFor();
 await row.locator('.task-more summary').click();await row.locator('[data-action=edit]').click();await page.locator('[name=name]').fill('界面编辑后任务');await page.locator('#task-form button[type=submit]').click();await page.locator('#editor').waitFor({state:'hidden'});
 check('添加编辑保存原功能可用',(await api('/api/schedules')).body.tasks.find(t=>t.id===created.id).name==='界面编辑后任务');
 await row.locator('[data-action=run]').click();await page.locator('#stat-running').filter({hasText:'1'}).waitFor();check('立即运行同时更新概况与状态胶囊',/运行中/.test(await row.locator('.task-state').innerText()));
 for(let i=0;i<50;i++){const rs=(await api('/api/schedules/runs?task_id='+created.id)).body.runs;if(rs[0]?.status==='succeeded')break;await page.waitForTimeout(50)}
 await page.locator('#refresh').click();await page.locator('#stat-running').filter({hasText:'0'}).waitFor();check('运行完成同步概况状态与结果胶囊',/成功/.test(await row.locator('.result-badge').innerText()));
 await page.locator('#run-tab').click();const runRow=page.locator('.run-row').filter({hasText:'界面编辑后任务'});await runRow.waitFor();await runRow.locator('[data-run]').click();await page.locator('#run-detail').waitFor();
 check('运行记录和详情显示真实隔离脚本输出',(await page.locator('#run-output').innerText())==='schedule-ui-fixture');await page.locator('#run-detail [data-close]').click();
 await page.locator('#refresh').click();await page.locator('#overview-runs').getByRole('button',{name:'界面编辑后任务'}).click();await page.locator('#run-detail').waitFor();assert.equal(await page.locator('#run-output').innerText(),'schedule-ui-fixture');await page.locator('#run-detail [data-close]').click();
 // All writes below target only this random-port temporary backend.
 await page.locator('#task-tab').click();
 const pausedBefore=Number(await page.locator('#stat-paused').innerText());
 const more=async action=>{const d=row.locator('.task-more');if(!await d.evaluate(e=>e.open))await d.locator('summary').click();await row.locator('[data-action='+action+']').click();};
 await more('resume');await page.waitForFunction(n=>Number(document.querySelector('#stat-paused').textContent)===n,pausedBefore-1);
 assert.match(await row.locator('.task-state').innerText(),/已启用/);assert.notEqual(await row.locator('.next-cell').innerText(),'—');
 await more('pause');await page.waitForFunction(n=>Number(document.querySelector('#stat-paused').textContent)===n,pausedBefore);
 check('暂停启用联动概况表格和下次时间',/已暂停/.test(await row.locator('.task-state').innerText())&&(await row.locator('.next-cell').innerText())==='—');
 await row.locator('.task-name').click();await page.locator('#task-detail').waitFor();assert.match(await page.locator('#task-meta').innerText(),new RegExp(tmp.replace(/[.*+?^${}()|[]\]/g,'\$&')));await page.locator('#task-detail [data-close]').click();check('非按钮点击任务行可打开完整详情',true);
 await page.locator('#search').fill('界面编辑后任务');await page.locator('#task-filter').selectOption('paused');assert.equal(await page.locator('.task-row').count(),1);
 await page.locator('#batch-mode').click();await page.locator('#select-all').check();await page.locator('#batch-resume').click();await page.waitForFunction(()=>document.querySelectorAll('.task-row').length===0);
 await page.locator('#task-filter').selectOption('enabled');await page.locator('#select-all').check();await page.locator('#batch-pause').click();await page.waitForFunction(()=>document.querySelectorAll('.task-row').length===0);
 check('筛选搜索批量启用与暂停保持原行为',!(await api('/api/schedules')).body.tasks.find(t=>t.id===created.id).enabled);
 await page.locator('#batch-mode').click();await page.locator('#search').fill('');await page.locator('#task-filter').selectOption('all');
 await page.locator('[data-summary=paused]').click();assert.equal(await page.locator('[data-external-row]').count(),0);assert.equal(await page.locator('.task-row').count(),Number(await page.locator('#stat-paused').innerText()));await page.locator('[data-summary=paused]').click();
 await page.locator('[data-scope=external]').click();assert.equal(await page.locator('.task-row').count(),0);assert.equal(await page.locator('[data-external-row]').count(),10);assert.equal(await page.locator('[data-external-row] button,[data-external-row] [data-action]').count(),0);
 const posts=[];const track=req=>{if(req.method()==='POST')posts.push(new URL(req.url()).pathname)};page.on('request',track);
 await page.locator('[data-external-row]').first().click();await page.locator('#task-detail').waitFor();await page.locator('#task-detail [data-close]').click();await page.waitForTimeout(30);page.off('request',track);check('概况筛选来源切换与外部行完全只读',posts.length===0);
 await page.locator('[data-scope=all]').click();await page.locator('#recent-all').click();await page.locator('.run-row').first().waitFor();
 assert.equal(await page.locator('#next-page').isDisabled(),false);await page.locator('#next-page').click();await page.locator('#page-label').filter({hasText:'第 2 页'}).waitFor();await page.locator('#previous-page').click();await page.locator('#page-label').filter({hasText:'第 1 页'}).waitFor();
 await page.locator('#run-status').selectOption('failed');await page.waitForFunction(()=>[...document.querySelectorAll('.run-row .state-pill')].every(e=>e.textContent==='失败'));
 await page.locator('#run-status').selectOption('');await page.locator('#search').fill('schedule-ui-fixture');await page.waitForFunction(()=>document.querySelectorAll('.run-row').length===1);check('记录结果筛选搜索分页与查看全部入口可用',/界面编辑后任务/.test(await page.locator('.run-row').innerText()));
 await page.locator('#search').fill('');await page.locator('#task-tab').click();
 const prefix='<script>"😀',longName=prefix+'长'.repeat(100-Array.from(prefix).length);assert.equal(Array.from(longName).length,100);
 const post=await context.request.post(base+'/api/schedules/save',{headers:{Origin:base,'X-Article-Studio':'1'},data:{name:longName,kind:'script',body:'printf boundary',cwd:tmp,frequency:'interval',minutes:60,timeout:5,enabled:false,allow_commands:false}});assert.equal(post.status(),200);
 const longTask=await post.json();await page.locator('#refresh').click();const longRow=page.locator('[data-task-id="'+longTask.id+'"]');await longRow.waitFor();assert.equal(await longRow.locator('.task-name').getAttribute('title'),longName);assert.equal(await longRow.locator('.task-name').evaluate(e=>getComputedStyle(e).textOverflow),'ellipsis');assert.equal(await longRow.locator('script,img').count(),0);
 check('100字符含脚本引号emoji安全省略且完整title可见',await longRow.locator('.task-name').innerText()===longName);
 await longRow.locator('.task-more summary').click();page.once('dialog',d=>d.accept());await longRow.locator('[data-action=delete]').click();await longRow.waitFor({state:'detached'});check('删除确认和原任务记录保留可用',!(await api('/api/schedules')).body.tasks.some(t=>t.id===longTask.id));
 // Read-only quarantine receipt on a separate page; only the local fake retry route is used.
 const qp=await context.newPage();let retried=0;
 await qp.route('**/api/schedules',async r=>{const res=await r.fetch(),d=await res.json();d.tasks.push({...created,id:'f'.repeat(32),kind:'wechat_radar',name:'隔离面板合成任务'});return r.fulfill({json:d})});
 await qp.route('**/api/sources/wechat/radar-quarantine?*',r=>r.fulfill({json:{available:true,items:[{document_id:'a'.repeat(32),hash:'b'.repeat(64),title:'合成隔离资料',error_kind:'validation'}]}}));
 await qp.route('**/api/sources/wechat/radar-quarantine/retry',r=>{retried++;return r.fulfill({json:{requeued:1}})});
 await qp.goto(base+'/schedules.html');await qp.locator('#radar-quarantine').waitFor();await qp.locator('[data-retry-quarantine]').click();await qp.locator('#message').filter({hasText:'已重新排队'}).waitFor();check('雷达隔离批次与人工重试保持原有回执',retried===1);await qp.close();
 await page.locator('#workspace-nav [data-nav-item=settings]').click();await ready(page);
 for(const id of ['interest-notice','official-notice','wechat-tools-notice'])await page.locator('#'+id).filter({hasText:'已读取'}).waitFor();
 for(const id of ['interest-title','official-title','wechat-tools-title','sources-title','models-title','logs-title','services-title'])assert.equal(await page.locator('#'+id).count(),1);
 assert.equal(await page.locator('#tasks,#runs').count(),0);assert.equal(await page.locator('#services li').count(),5);assert.equal(await page.locator('#sources .card').count(),7);
 await page.locator('#refresh').click();await ready(page);check('设置其他分区和公共刷新均正常',await nav.locator(':scope > a[data-nav-item=settings][aria-current=page]').count()===1);
 await page.screenshot({path:path.join(tmp,'settings-1440.png'),fullPage:true});
 await page.getByRole('link',{name:'前往定时任务页'}).click();await page.locator('#overview-tasks tr').first().waitFor();
 await page.setViewportSize({width:390,height:844});await page.waitForTimeout(100);await page.screenshot({path:path.join(tmp,'schedules-390.png')});
 fs.writeFileSync(path.join(tmp,'mobile-layout.json'),JSON.stringify(await page.evaluate(()=>({width:innerWidth,scroll:document.documentElement.scrollWidth,overflows:[...document.querySelectorAll('body *')].filter(e=>e.getBoundingClientRect().right>innerWidth+1&&!e.closest('.overview-scroll')).map(e=>({tag:e.tagName,id:e.id,cls:e.className,width:e.getBoundingClientRect().width,right:e.getBoundingClientRect().right}))})),null,2));
 check('390主导航可见且无页面横向溢出',await nav.locator(':scope > a[data-nav-item=schedules]').isVisible()&&await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
 const scroll=page.locator('.overview-scroll');assert(await scroll.evaluate(e=>e.scrollWidth<=e.clientWidth));assert.equal(await page.locator('.task-row').first().evaluate(e=>getComputedStyle(e).display),'grid');const mobileRun=page.locator('.task-row [data-action=run]').first();await mobileRun.scrollIntoViewIfNeeded();check('390任务卡片无需横向滚动即可查看操作',await mobileRun.isVisible()&&await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
 await page.locator('#workspace-nav [data-nav-item=settings]').click();await ready(page);await page.screenshot({path:path.join(tmp,'settings-390.png'),fullPage:true});assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
 await page.setViewportSize({width:1440,height:1000});
 await page.locator('#workspace-nav summary').filter({hasText:'旧版'}).click();
 // Test legacy expansion on a real legacy page while isolating unrelated source integrations.
 await context.route('**/api/intel/**',r=>r.fulfill({json:{subscriptions:[],jobs:[],platforms:[]}}));
 await context.route('**/api/collect/**',r=>r.fulfill({json:{platforms:[],active_tasks:[]}}));
 await page.locator('[data-nav-item=intel]').click();await page.locator('[data-nav-group=legacy][open] [data-nav-item=intel][aria-current=page]').waitFor();check('旧版页自动展开所在组并正确高亮',true);await page.screenshot({path:path.join(tmp,'legacy-nav-1440.png')});
 fs.writeFileSync(path.join(tmp,'browser-errors.json'),JSON.stringify({errors,consoleErrors},null,2));check('设置与定时任务及旧版导航浏览器控制台0错误',errors.length===0&&consoleErrors.length===0,JSON.stringify({errors,consoleErrors}));
 }catch(e){console.error(e);process.exitCode=1}finally{fs.writeFileSync(path.join(tmp,'results.json'),JSON.stringify({results},null,2));console.log('证据 '+tmp+'；'+results.length+'项通过');if(context)await context.close();if(child)child.kill('SIGTERM')}
})();
