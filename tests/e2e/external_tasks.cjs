'use strict';
// H1：随机端口 + /tmp 工作区 + 独立 Chromium profile；不使用真实桌面或真实 Chrome profile。
const fs=require('fs'),path=require('path'),os=require('os'),net=require('net'),assert=require('assert/strict');
const {spawn}=require('child_process');
const {chromium}=(()=>{try{return require('playwright')}catch(_){return require(path.join(os.homedir(),'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright'))}})();
const ROOT=path.resolve(__dirname,'../..'),PY=path.resolve(ROOT,'../article-importer/.venv/bin/python');
const out=process.env.H1_EVIDENCE_DIR || '/tmp/impl-hub-h1',tmp=fs.mkdtempSync('/tmp/h1-e2e-');fs.mkdirSync(out,{recursive:true});
const results=[],errors=[],requests=[],timings=[];let child,context,base,stderr='';
const check=(name,ok)=>{assert.ok(ok,name);results.push({name,passed:true});console.log('通过 '+name)};
const delay=ms=>new Promise(r=>setTimeout(r,ms));
(async()=>{try{
 const port=await new Promise(resolve=>{const s=net.createServer().listen(0,'127.0.0.1',()=>{const p=s.address().port;s.close(()=>resolve(p))})});base='http://127.0.0.1:'+port;
 child=spawn(PY,['-u',path.join(__dirname,'external_tasks_fixture.py'),tmp,String(port)],{cwd:ROOT,stdio:['ignore','ignore','pipe']});child.stderr.on('data',b=>stderr+=b);
 for(let i=0;i<100;i++){try{if((await fetch(base+'/api/schedules')).ok)break}catch(_){}if(child.exitCode!==null)throw Error(stderr);await delay(100)}
 context=await chromium.launchPersistentContext(path.join(tmp,'profile'),{headless:true,channel:'chrome',viewport:{width:1440,height:1000}});
 await context.route('**/*',r=>new URL(r.request().url()).origin===base?r.continue():r.abort());
 const page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));page.on('request',r=>{if(r.method()!=='GET')requests.push({method:r.method(),url:r.url()})});
 await page.goto(base+'/settings.html');await page.locator('#tasks [data-external-row="grok-interest"] .status-overdue').waitFor();
 const body=await(await context.request.get(base+'/api/schedules')).json();
 check('全部内置与8项产出监测、2项服务同表',await page.locator('#tasks [data-task-row]').count()===body.tasks.length&&body.external.tasks.length===10&&await page.locator('#tasks tr').count()===body.tasks.length+10);
 check('外部行没有可写操作',await page.locator('[data-external-row] button,[data-external-row] [data-task]').count()===0&&await page.locator('[data-external-row]').count()===10);
 check('外部标明只读、计划和上次产出',await page.locator('[data-external-row="grok-interest"]').innerText().then(t=>t.includes('外部 · 只读 · Grok')&&t.includes('10:30 / 22:30')&&t.includes('2026')&&t.includes('逾期')));
 check('digest明确待确认',await page.locator('[data-external-row="grok-digest"] .status-unconfirmed').innerText()==='待确认');
 check('常驻服务显示PID与退出码',await page.locator('[data-external-row="com.servasyy.wechat-radar"]').innerText().then(t=>t.includes('运行中')&&t.includes('123'))&&await page.locator('[data-external-row="com.servasyy.wechat-source"]').innerText().then(t=>t.includes('已退出')&&t.includes('退出码：9')));
 await page.locator('#tasks-title').scrollIntoViewIfNeeded();await page.locator('[aria-labelledby=tasks-title]').screenshot({path:path.join(out,'overdue-1440.png')});
 await page.setViewportSize({width:390,height:844});await page.locator('#tasks-title').scrollIntoViewIfNeeded();await page.locator('[aria-labelledby=tasks-title]').screenshot({path:path.join(out,'overdue-390.png')});
 check('390总览无页面横向溢出',await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
 check('390可见逾期文字与颜色',await page.locator('[data-external-row="grok-interest"] .status-overdue').isVisible()&&await page.locator('.status-overdue').evaluate(e=>getComputedStyle(e).backgroundColor==='rgb(255, 240, 217)'));
 check('总览读取没有POST副作用',requests.length===0);
 fs.writeFileSync(path.join(tmp,'refresh-failure'),'fail');
 for(let i=0;i<60;i++){const b=await(await context.request.get(base+'/api/schedules')).json();if(b.external.last_error)break;await delay(100)}
 const failed=await(await context.request.get(base+'/api/schedules')).json();
 check('刷新失败保留外部快照并独立标错',failed.external.last_error==='外部心跳刷新失败'&&!!failed.external.last_error_at&&JSON.stringify(failed.external.tasks)===JSON.stringify(body.external.tasks)&&failed.external.checked_at===body.external.checked_at&&failed.last_error===null);
 check('刷新失败时调度健康仍正常',(await(await context.request.get(base+'/api/schedules/health')).json()).ok===true);
 for(const width of [1440,390]){
   await page.setViewportSize({width,height:width===390?844:1000});await page.locator('#refresh').click();await page.locator('#refresh:not([disabled])').waitFor();
   check(width+'显示外部刷新失败与保留快照',await page.locator('#external-checked').innerText().then(t=>t.includes('外部心跳刷新失败')&&t.includes('保留上次快照'))&&await page.locator('[data-external-row]').count()===10);
   check(width+'故障提示无页面横向溢出',await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
   await page.locator('[aria-labelledby=tasks-title]').screenshot({path:path.join(out,'refresh-failed-'+width+'.png')});
 }
 fs.unlinkSync(path.join(tmp,'refresh-failure'));
 for(let i=0;i<60;i++){const b=await(await context.request.get(base+'/api/schedules')).json();if(!b.external.last_error)break;await delay(100)}
 await page.locator('#refresh').click();await page.locator('#refresh:not([disabled])').waitFor();
 const recovered=await(await context.request.get(base+'/api/schedules')).json();
 check('刷新恢复清除错误及页面提示',recovered.external.last_error===null&&recovered.external.last_error_at===null&&!(await page.locator('#external-checked').innerText()).includes('外部心跳刷新失败'));
 await page.setViewportSize({width:1440,height:1000});
 const row=page.locator('#tasks [data-task-row]');await row.locator('[data-action=resume]').click();await row.locator('[data-action=pause]').waitFor();
 check('内置任务可恢复',await row.innerText().then(t=>t.includes('已启用')));
 await row.locator('[data-action=pause]').click();await row.locator('[data-action=resume]').waitFor();
 check('内置任务可暂停',await row.innerText().then(t=>t.includes('已暂停')));
 await row.locator('[data-action=run]').click();
 for(let i=0;i<50;i++){await page.getByRole('button',{name:'刷新状态',exact:true}).click();await page.locator('#refresh:not([disabled])').waitFor();if((await page.locator('#runs').innerText()).includes('成功'))break;await delay(100)}
 check('立即运行真实脚本并显示运行记录',await page.locator('#runs').innerText().then(t=>t.includes('成功')&&t.includes('H1 隔离内置任务')));
 for(let i=0;i<5;i++){const t=performance.now();const r=await context.request.get(base+'/api/schedules');assert.equal(r.status(),200);timings.push(performance.now()-t)}
 check('缓存接口5次均小于1秒',timings.every(t=>t<1000));
 check('兴趣清单仍可读取',await page.locator('#interest-topics').innerText().then(t=>t.includes('H1 隔离测试')));
 check('设置页无脚本错误',errors.length===0);
 }catch(e){console.error(e);process.exitCode=1;errors.push(e.message)}finally{
 fs.writeFileSync(path.join(out,'browser-results.json'),JSON.stringify({results,errors,requests,timings,tmp,base},null,2));if(context)await context.close();if(child)child.kill('SIGTERM');console.log('证据 '+out+'；'+results.length+' 项通过');
 }
})();
