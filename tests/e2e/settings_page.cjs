'use strict';
// 独立 /tmp 工作区、随机端口、headless profile；只用合成数据，禁止外网。
const fs=require('fs'),path=require('path'),os=require('os'),net=require('net'),assert=require('assert/strict');
const {spawn}=require('child_process');
const {chromium}=(()=>{try{return require('playwright')}catch(_){return require(path.join(os.homedir(),'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright'))}})();
const ROOT=path.resolve(__dirname,'../..'),PY=path.resolve(ROOT,'../article-importer/.venv/bin/python');
const tmp=fs.mkdtempSync('/tmp/settings-p4-e2e-'),results=[],timings=[];
let child,context,base,stderr='';
const check=(name,ok,detail='')=>{assert.ok(ok,name+' '+detail);results.push({name,passed:true,detail});console.log('通过 '+name+' '+detail)};
async function start(){
 const port=await new Promise(resolve=>{const s=net.createServer().listen(0,'127.0.0.1',()=>{const p=s.address().port;s.close(()=>resolve(p))})});base='http://127.0.0.1:'+port;
 const code=`import sys,json,sqlite3;from pathlib import Path;sys.path.insert(0,${JSON.stringify(ROOT)});import server,settings_view,grok_bot_radar;from http.server import ThreadingHTTPServer;from scheduled_tasks import ScheduledTasks;from tests.test_clues import KnowledgeFake;from types import SimpleNamespace
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
t=s.schedules.save(dict(name='GitHub 合成任务',kind='script',body='github_source.py',cwd=str(tmp),frequency='interval',minutes=60,at='08:00',weekdays=list(range(7)),ends_at=None,timeout=5,enabled=True,allow_commands=False));(tmp/'task-id').write_text(t['id'])
with s.schedules.db() as db:db.execute('INSERT INTO runs(id,task_id,snapshot,trigger,status,started_at,finished_at,exit_code,output) VALUES(?,?,?,?,?,?,?,?,?)',('e'*32,t['id'],json.dumps(t),'manual','succeeded','2026-10-29T08:00:00Z','2026-10-29T08:01:00Z',0,'fixture'))
doc={'id':'a'*32,'source':'wechat','kind':'wechat','title':'合成来源','body':'可回看的原文依据','hash':'b'*64,'url':'http://example.test/source','modified':'2026-10-29T00:00:00Z','provenance':{}}
s.store._knowledge=KnowledgeFake({doc['id']:doc});clue=s.store.clues.save({'title':'合成线索','summary':'核对入口与原文','picks':[{'id':doc['id'],'hash':doc['hash'],'start':0,'end':len(doc['body'])}]});(tmp/'clue-id').write_text(clue['id'])
s.serve_forever()`;
 child=spawn(PY,['-u','-c',code],{cwd:ROOT,stdio:['ignore','ignore','pipe']});child.stderr.on('data',b=>stderr+=b);
 for(let i=0;i<100;i++){try{if((await fetch(base+'/api/settings/overview')).ok)return}catch(_){}if(child.exitCode!==null)throw Error(stderr);await new Promise(r=>setTimeout(r,100))}throw Error('隔离服务未启动 '+stderr);
}
const api=async url=>{const r=await context.request.get(base+url);return {status:r.status(),body:await r.json()}};
async function ready(p){await p.locator('#sources[aria-busy=false]').waitFor();await p.locator('#tasks [data-task]').first().waitFor();await p.locator('#models [data-profile]').first().waitFor()}
(async()=>{try{
 await start();context=await chromium.launchPersistentContext(path.join(tmp,'profile'),{headless:true,channel:'chrome',viewport:{width:1440,height:1000}});
 await context.route('**/*',r=>new URL(r.request().url()).origin===base?r.continue():r.abort());
 const page=await context.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));
 for(let i=0;i<5;i++){const start=performance.now();await page.goto(base+'/settings.html');await ready(page);timings.push(Number(((performance.now()-start)/1000).toFixed(4)))}
 check('P8 设置页加载5次均小于1秒',timings.every(t=>t<1),JSON.stringify(timings));
 const overview=(await api('/api/settings/overview')).body,schedules=(await api('/api/schedules')).body;
 check('G1 7个真实来源及定时任务数量一致',overview.sources.length===7&&overview.schedule_task_count===schedules.tasks.length&&await page.locator('#sources .card').count()===7&&await page.locator('#tasks [data-task-row]').count()===schedules.tasks.length&&await page.locator('#tasks [data-external-row]').count()===9&&await page.locator('#tasks [data-external-row] [data-task],#tasks [data-external-row] button').count()===0&&await page.locator('#tasks tr').count()===schedules.tasks.length+9);
 check('来源实际成功时间与夹具一致',(await page.locator('[data-source=grok]').innerText()).includes('2026')&&overview.sources.find(s=>s.id==='grok').last_success==='2026-10-29T08:00:00Z');
 assert.equal(overview.sources.find(s=>s.id==='twitter').last_success,'2026-10-29T14:00:38.594Z');
 assert.equal(overview.sources.find(s=>s.id==='aisa').last_success,'2026-10-29T14:00:38.594Z');
 const today=(await api('/api/today?source=A&range=48h')).body;
 assert.equal(Math.floor(Date.parse(overview.sources.find(s=>s.id==='aisa').last_success)/1000),Math.floor(Date.parse(today.sources.A.updated_at.replace(' ','T')+'+08:00')/1000));
 assert.match(await page.locator('[data-source=twitter]').innerText(),/22:00:38|14:00:38/);
 assert.match(await page.locator('[data-source=intel]').innerText(),/2026-10-29T14:00:38Z.*200013/);
 for(const s of overview.sources){const p=page.locator('[data-source='+s.id+'] p[title]').first();await p.hover();assert.equal(await p.getAttribute('title'),s.reason);assert.match(s.reason,/\.db|\.json/)}
 fs.writeFileSync(path.join(tmp,'source-evidence.json'),JSON.stringify(overview.sources,null,2));
 check('后台服务5个且没有启停按钮',await page.locator('#services li').count()===5&&await page.locator('#services button').count()===0);
 for(const query of ['name=../../.env','name=service%2F..','lines=99999','lines=-1'])check('P1 '+query,(await api('/api/settings/logs?'+query)).status===400);
 const logs=(await api('/api/settings/logs')).body;
 await page.locator('#logs').getByText('normal fixture line',{exact:false}).waitFor();
 check('P2 日志API脱敏',['fixture-auth-secret','sk-fixture-log-secret','fixture-cookie-secret'].every(s=>!JSON.stringify(logs).includes(s)));
 const dom=await page.content();check('P2 页面看不到日志凭据',!['fixture-auth-secret','sk-fixture-log-secret','fixture-cookie-secret'].some(s=>dom.includes(s)));
 const modelResponse=await api('/api/assistant/settings');
 check('P4 模型接口和DOM无假密钥片段',['test-placeholder-not-a-key','test-placeholder','not-a-key'].every(fragment=>!JSON.stringify(modelResponse).includes(fragment)&&!dom.includes(fragment)&&!JSON.stringify(overview).includes(fragment))&&await page.getByText('可用 · 密钥已配置',{exact:true}).count()===2);
 const switchResponse=page.waitForResponse(r=>r.url().endsWith('/api/assistant/select-model'));await page.locator('[data-profile=two]').click();assert.equal((await switchResponse).status(),200);await page.locator('#active-model').getByText('当前 profile：合成模型二',{exact:true}).waitFor();check('模型切换复用现有接口且配置真实改变',(await api('/api/assistant/settings')).body.active_profile_id==='two');
 const taskId=fs.readFileSync(path.join(tmp,'task-id'),'utf8');
 for(const action of ['pause','resume']){
  await page.goto(base+'/settings.html');await ready(page);
  const response=page.waitForResponse(r=>r.url().endsWith('/'+taskId+'/'+action));await page.locator('#tasks [data-task="'+taskId+'"]').click();assert.equal((await response).status(),200);
  const expected=action==='resume';assert.equal((await api('/api/schedules')).body.tasks.find(t=>t.id===taskId).enabled,expected);
  await page.goto(base+'/schedules.html');const row=page.locator('[data-task-id="'+taskId+'"]');await row.waitFor();await row.locator('[data-action="'+(expected?'pause':'resume')+'"]').waitFor();
  check('P5 设置页'+(expected?'恢复':'暂停')+'后旧任务页状态同步',await row.innerText().then(t=>expected?!t.includes('已暂停'):t.includes('已暂停')));
 }
 // 避免页面自动状态读取触发本地连接器；只读 /tmp 业务数据，其余状态用固定回执。
 await context.route('**/api/clues/wechat/status',r=>r.fulfill({json:{processed_versions:0,has_pending:false}}));
 await context.route('**/api/knowledge',r=>r.fulfill({json:{sources:[]}}));
 await context.route('**/api/collect/**',r=>r.fulfill({json:{platforms:[],active_tasks:[]}}));
 await context.route('**/api/intel/**',r=>r.fulfill({json:{subscriptions:[],jobs:[]}}));
 const pages=fs.readdirSync(path.join(ROOT,'public')).filter(f=>f.endsWith('.html')&&fs.readFileSync(path.join(ROOT,'public',f),'utf8').includes('workspace-nav.js'));
 const priorErrors=errors.length;
 for(const f of pages){await page.goto(base+'/'+f);await page.locator('#workspace-nav>a').first().waitFor();assert.deepEqual(await page.locator('#workspace-nav>a').evaluateAll(ns=>ns.map(n=>n.dataset.navItem)),['today','library','works','publish','deals','settings']);assert.equal(await page.locator('#workspace-nav [data-nav-group=legacy] a').count(),9);await page.screenshot({path:path.join(tmp,f+'.png')})}
 check('P6 每个共享导航页面都有6项且顺序正确',pages.length>=16,String(pages.length)+'页');
 for(const url of ['/clues.html','/intel.html','/github.html','/schedules.html','/feedback.html','/x-official.html','/knowledge.html','/intake.html','/cards.html','/events.html']){const r=await context.request.get(base+url);assert.equal(r.status(),200)}check('P6 旧版9个路径与events直接地址全部可打开',true);
 await page.goto(base+'/events.html');await page.locator('#ev-view-nav').waitFor();check('events直接打开保留页面功能',await page.locator('#ev-view-nav button').count()>0);
 await page.goto(base+'/clues.html');const clueId=fs.readFileSync(path.join(tmp,'clue-id'),'utf8');await page.locator('[data-clue="'+clueId+'"]').click();await page.locator('#cl-detail h2').getByText('合成线索',{exact:true}).waitFor();
 check('P7 线索事件入口隐藏且追加依据可用',!await page.locator('#cl-import').isVisible()&&!await page.locator('[data-detail=promote]').isVisible()&&await page.locator('[data-detail=append]').isVisible());
 await page.locator('[data-detail=append]').click();await page.locator('#cl-editor').waitFor();check('线索其他操作真实打开',await page.locator('#cl-editor-title').innerText()==='追加新依据');await page.locator('#cl-editor').evaluate(d=>d.close());
 const clue=(await api('/api/clues/'+clueId+'?offset=0')).body;
 const promoted=await context.request.post(base+'/api/clues/'+clueId+'/promote',{headers:{Origin:base,'X-Article-Studio':'1'},data:{revision:clue.revision}});assert.equal(promoted.status(),201);
 const event=(await promoted.json()).event_id;assert.equal((await api('/api/events/'+event)).body.title,'合成线索');
 await page.reload();await page.locator('[data-clue="'+clueId+'"]').click();const associated=page.locator('#cl-detail a[href^="/events.html?event="]');await associated.waitFor({state:'attached'});
 check('P7 已有关联事项的链接也隐藏，events原数据接口仍可用',!await associated.isVisible());
 await context.route('**/api/assistant',r=>r.fulfill({json:{configured:false,profiles:[],messages:[],active_job:null}}));
 await page.goto(base+'/');await page.locator('#assistant-toggle').click();await page.locator('#assistant-panel').waitFor();
 await page.evaluate(()=>{document.querySelector('#assistant-message-list').innerHTML=assistantActions([{status:'done',label:'合成事件建议',event_proposal:{title:'合成事件'}}])});
 check('P7 助手事件建议跳转隐藏而回执可见',!await page.locator('[data-event-proposal]').isVisible()&&await page.getByText('✓ 合成事件建议',{exact:true}).isVisible());
 // 只针对本次页面检查脚本错误；旧页面/未装配集成错误分别留证，不冒称回归全过。
 await page.goto(base+'/settings.html');await ready(page);await page.screenshot({path:path.join(tmp,'settings-final.png'),fullPage:true});
 await page.setViewportSize({width:390,height:844});await page.waitForTimeout(100);await page.screenshot({path:path.join(tmp,'settings-mobile.png')});check('设置页窄屏无横向溢出',await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
 fs.writeFileSync(path.join(tmp,'page-errors.json'),JSON.stringify({settingsBeforeNav:errors.slice(0,priorErrors),otherPageErrors:errors.slice(priorErrors)},null,2));check('设置页本身无脚本错误',priorErrors===0);
 }catch(e){console.error(e);process.exitCode=1}finally{fs.writeFileSync(path.join(tmp,'results.json'),JSON.stringify({results,timings},null,2));console.log('证据 '+tmp+'；'+results.length+'项通过');if(context)await context.close();if(child)child.kill('SIGTERM')}
})();
