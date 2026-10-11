const test = require('node:test'), assert = require('node:assert/strict'), fs = require('node:fs');
const {JSDOM} = require('jsdom');
const tick = () => new Promise(resolve => setImmediate(resolve));
async function settle() { for (let count = 0; count < 4; count++) await tick(); }
const deferred = () => { let resolve; const promise = new Promise(done => {resolve = done;}); return {promise,resolve}; };
const taskId = 'a'.repeat(32), otherId = 'c'.repeat(32), runId = 'b'.repeat(32);
const task = {id:taskId,revision:3,name:'每日查询',body:'查询今日进展',kind:'pi',assistant_id:'workbench',tool_groups:['workspace'],profile_id:'fake',cwd:'/Users/example/projects/studio',frequency:'interval',minutes:360,weekdays:[0],at:'08:00',timeout:300,enabled:false,allow_commands:false,next_run:null,result_destination:'runs',result_category:'other',clue_id:''};
const run = {id:runId,task_id:taskId,snapshot:{...task,executor_name:'工作台助手（Pi）',model_name:'合成模型'},status:'succeeded',trigger:'manual',started_at:'2026-10-05T02:00:00Z',finished_at:'2026-10-05T02:01:05Z',exit_code:0,output:'查询结果'};
test('radar error receipts expose saved progress and actual validation reason',async t => {
  const records=[0,859].map((processed,i)=>({...run,id:String(i).repeat(32),status:i?'partial':'failed',snapshot:{...task,kind:'wechat_radar'},output:JSON.stringify({processed,has_more:true,error:'单条引用超量，最多 15 条'})}));
  const f=await fixture(t,{runs:records});f.d.querySelector('#run-tab').click();await settle();
  const text=f.d.querySelector('#runs').textContent;
  assert.match(text,/保存 859 条消息/);assert.match(text,/单条引用超量/);assert(!text.includes('运行结果格式异常'));
});
test('radar receipts separate run limit, no answer, empty output, model failure, rejection and quarantine',async t => {
  const cases=[['deadline','运行期限已到'],['model_unresponsive','模型无响应'],['empty_output','空输出'],['model_failed','模型执行失败'],['validation','未通过校验'],['quarantined','已隔离']];
  const records=cases.map(([kind],i)=>({...run,id:String(i+1).repeat(32),status:'partial',snapshot:{...task,kind:'wechat_radar'},output:JSON.stringify({processed:12,has_more:true,batches:3,error:'合成原因',error_kind:kind,quarantined:kind==='quarantined'?4:undefined,refs_capped:kind==='validation'?7:undefined})}));
  const f=await fixture(t,{runs:records});f.d.querySelector('#run-tab').click();await settle();
  const text=f.d.querySelector('#runs').textContent;
  for(const [,label] of cases)assert.ok(text.includes(label),`missing ${label} in ${text}`);
  assert.match(text,/保存 12 条消息 · 3 批/);assert.match(text,/已隔离 4 条资料/);assert.match(text,/已按批次上限裁剪 7 个/);
  assert.ok(!text.includes('运行结果格式异常'));
});
test('legacy stopped-source receipt is incomplete statistics, not invalid JSON',async t => {
  const item={...run,status:'timeout',snapshot:{...task,kind:'wechat_clues'},output:JSON.stringify({source_run_id:runId,state:'来源任务停止；已完成结果与检查点保留'})};
  const f=await fixture(t,{runs:[item]});f.d.querySelector('#run-tab').click();await settle();
  const text=f.d.querySelector('#runs').textContent;
  assert.match(text,/达到本次运行时限/);assert.match(text,/缺少最终统计/);assert(!text.includes('运行结果格式异常'));
});
test('durable in-flight progress distinguishes final receipt still pending',async t => {
  const item={...run,status:'timeout',snapshot:{...task,kind:'wechat_clues'},output:JSON.stringify({processed:274,skipped:65,clues:[],has_more:true,receipt_pending:true})};
  const f=await fixture(t,{runs:[item]});f.d.querySelector('#run-tab').click();await settle();
  const text=f.d.querySelector('#runs').textContent;
  assert.match(text,/分析 274 条/);assert.match(text,/最终停止回执尚未返回/);assert(!text.includes('运行结果格式异常'));
});
test('quarantine panel lists held radar batches and manual retry only re-queues them',async t => {
  const tasks=[{...task,kind:'wechat_radar'}];
  const items=[{document_id:'a'.repeat(32),hash:'b'.repeat(64),error_kind:'validation',at:'2026-10-09T11:00:00Z',run_id:'r'.repeat(32),detail:'二次修复仍无效，本批已隔离',title:'群 · 作者 · 日期'}];
  const posts=[];
  const f=await fixture(t,{tasks,handler:call=>{
    if(call.url==='/api/sources/wechat/radar-quarantine/retry'){posts.push(call.body);return response({schema:'wechat-source/v1',available:true,requeued:1});}
    if(call.url.startsWith('/api/sources/wechat/radar-quarantine'))return response({schema:'wechat-source/v1',available:true,items,counts:{validation:1},scope:'隔离只停止每小时重放'});
  }});
  await settle();
  const panel=f.d.querySelector('#radar-quarantine');
  assert.equal(panel.hidden,false);
  assert.match(panel.textContent,/1 条资料已隔离/);assert.match(panel.textContent,/模型输出未通过校验/);assert.match(panel.textContent,/二次修复仍无效/);
  const link=panel.querySelector('a[href*="knowledge.html"]');
  assert.match(link.getAttribute('href'),/document=a{32}/);
  assert.ok(!panel.innerHTML.includes('javascript:'));
  panel.querySelector('[data-retry-quarantine]').click();await settle();
  assert.deepEqual(posts,[{document_ids:['a'.repeat(32)]}]);
  assert.match(f.d.querySelector('#message').textContent,/已重新排队 1 条资料/);
});
test('quarantine panel stays hidden without a radar task and never reads as empty when the source is down',async t => {
  const down=call=>call.url.startsWith('/api/sources/wechat/radar-quarantine')?response({error:'微信雷达接口不可用'},false):undefined;
  const plain=await fixture(t,{tasks:[task],handler:down});await settle();
  assert.equal(plain.d.querySelector('#radar-quarantine').hidden,true);
  const blind=await fixture(t,{tasks:[{...task,kind:'wechat_radar'}],handler:down});await settle();
  assert.equal(blind.d.querySelector('#radar-quarantine').hidden,false);
  assert.match(blind.d.querySelector('#quarantine-items').textContent,/暂不可读/);
  assert.ok(!blind.d.querySelector('#quarantine-items').textContent.includes('当前没有隔离批次'));
  assert.equal(blind.d.querySelector('#quarantine-retry-all').disabled,true);
});
const response = (data,ok = true) => ({ok,json:async () => data});
async function fixture(t, options = {}) {
  const dom = new JSDOM(fs.readFileSync('public/schedules.html','utf8'), {url:options.url || 'http://127.0.0.1:8766/schedules.html',runScripts:'outside-only',pretendToBeVisual:true});
  t.after(() => dom.window.close());
  const w = dom.window, d = w.document, calls = [], intervals = [], timers = new Map();
  const listing = {tasks:options.tasks || [],default_cwd:'/Users/example/projects/studio',default_profile_id:'fake',models:[{profile_id:'old',display_name:'旧模型',configured:true},{profile_id:'fake',display_name:'合成模型',configured:true}],scheduler_health:{running:true,last_tick:'2026-10-05T02:00:00Z',last_error:null},concurrency:{max:2,active:0,available:2},...options.listing};
  w.setInterval = (callback,delay) => {intervals.push({callback,delay});return intervals.length;};
  w.setTimeout = (callback,delay) => {const id = timers.size + 1;timers.set(id,{callback,delay});return id;};
  w.clearTimeout = id => timers.delete(id);
  w.confirm = () => true;
  w.HTMLDialogElement.prototype.showModal = function() {this.open = true;};
  w.HTMLDialogElement.prototype.close = function() {this.open = false;};
  w.fetch = async (url,opts = {}) => {
    const call = {url:String(url),opts,body:opts.body && JSON.parse(opts.body)}; calls.push(call);
    if (options.handler) {const custom = await options.handler(call);if (custom !== undefined) return custom;}
    if (call.url === '/api/schedules') return response(JSON.parse(JSON.stringify(listing)));
    if (call.url === '/api/clues?view=all') return response({items:options.clues || []});
    if (call.url.startsWith('/api/schedules/runs?')) return response({runs:options.runs || [run],total:(options.runs || [run]).length,page:Number(new URL(call.url,'http://local').searchParams.get('page')),page_size:50,has_more:false});
    if (call.url.startsWith('/api/schedules/run/')) return response((options.runs || [run]).find(item => item.id === call.url.split('/').pop()) || run);
    return response({id:taskId,ok:true});
  };
  w.eval(fs.readFileSync('public/schedules.js','utf8'));
  await settle();
  return {w,d,calls,listing,intervals,timers,form:d.querySelector('#task-form'),postCalls:() => calls.filter(call => call.opts.method === 'POST'),runCalls:() => calls.filter(call => call.url.startsWith('/api/schedules/runs?')),flushSearch:async () => {for (const [id,timer] of timers) {timers.delete(id);timer.callback();}await settle();}};
}
function change(f,selector,value) {const element = f.d.querySelector(selector);if (element.type === 'checkbox') element.checked = value;else element.value = value;element.dispatchEvent(new f.w.Event('change',{bubbles:true}));return element;}
function search(f,value) {const element = f.d.querySelector('#search');element.value = value;element.dispatchEvent(new f.w.Event('input',{bubbles:true}));}
function submit(f) {f.form.dispatchEvent(new f.w.Event('submit',{cancelable:true}));return settle();}
function params(call) {return new URL(call.url,'http://local').searchParams;}
function select(f,id = taskId) {change(f,'[data-select-task="' + id + '"]',true);}

test('page load queries only, reports scheduler health and shows honest empty state',async t => {
  const f = await fixture(t,{listing:{scheduler_health:{running:false,last_tick:null,last_error:'调度检查失败'},concurrency:{max:2,active:1,available:1}}});
  assert.match(f.d.querySelector('#tasks').textContent,/暂无定时任务/);
  assert.equal(f.postCalls().length,0);assert.equal(f.intervals[0].delay,10000);
  assert.equal(f.d.querySelector('#scheduler-status').textContent,'调度器已停止');
  assert.equal(f.d.querySelector('#concurrency-status').textContent,'并发 1 / 2');
  assert.equal(f.d.querySelector('#health-error').hidden,false);
  assert.equal(f.d.querySelector('#health-error').textContent,'调度检查失败');
  assert(f.calls.every(call => !/ssh|backend|model\/load/.test(call.url)));
});

test('compact task row presents assistant, model, human frequency, relative time and result; full path is in details',async t => {
  const next = Math.floor(Date.now()/1000) + 500;
  const f = await fixture(t,{tasks:[{...task,enabled:true,next_run:next,last_run:run,executor_name:'工作台助手（Pi）',model_name:'合成模型'}]});
  const row = f.d.querySelector('.task-row');
  assert.match(row.textContent,/工作台助手（Pi）/);assert.match(row.textContent,/合成模型/);assert.match(row.textContent,/每 6 小时/);
  assert.match(row.querySelector('.task-schedule small').textContent,/分钟后/);
  assert.match(row.querySelector('.task-schedule small').title,/北京时间/);
  assert.match(row.querySelector('.task-last').textContent,/最近 · 成功/);assert.match(row.querySelector('.task-last').textContent,/1 分 5 秒/);
  assert(!row.textContent.includes('/Users/example'));
  assert.equal(row.querySelector('[data-action=run]').closest('details'),null);
  assert.equal(row.querySelector('[data-action=pause]').closest('details'),null);
  assert(row.querySelector('[data-action=edit]').closest('details'));
  row.querySelector('[data-action=detail]').click();await settle();
  assert(f.d.querySelector('#task-meta').textContent.includes(task.cwd));
});

test('daily schedules describe weekdays and task filters/search work together',async t => {
  const f = await fixture(t,{tasks:[{...task,enabled:true,frequency:'daily',weekdays:[0,1,2,3,4],next_run:Date.now()/1000+100},{...task,id:otherId,name:'脚本',kind:'script',enabled:false,running:true,frequency:'daily',weekdays:[5,6]}]});
  assert.match(f.d.querySelector('#tasks').textContent,/工作日 08:00/);
  assert.match(f.d.querySelector('#tasks').textContent,/周末 08:00/);
  change(f,'#task-filter','enabled');assert.equal(f.d.querySelectorAll('.task-row').length,1);
  change(f,'#task-filter','paused');assert.equal(f.d.querySelector('.task-row').dataset.taskId,otherId);
  change(f,'#task-filter','running');assert.equal(f.d.querySelector('.task-row').dataset.taskId,otherId);
  change(f,'#task-filter','all');search(f,'合成模型');assert.equal(f.d.querySelectorAll('.task-row').length,1);
  search(f,'不存在');assert.match(f.d.querySelector('#tasks').textContent,/没有符合/);
});

test('new Pi defaults to workbench, stays paused and keeps command execution opt in',async t => {
  const f = await fixture(t);f.d.querySelector('#new-task').click();
  assert.equal(f.form.elements.kind.value,'pi');assert.equal(f.form.elements.assistant_id.value,'workbench');
  assert.equal(f.form.elements.enabled.checked,false);assert.equal(f.form.elements.allow_commands.checked,false);
  assert.equal(f.form.elements.profile_id.value,'fake');assert.equal(f.d.querySelector('#advanced-settings').open,false);
  assert.equal(f.d.querySelector('#tool-group-options').hidden,false);
  assert.equal(f.d.querySelectorAll('[name=tool_groups]:checked').length,0);
  assert.match(f.d.querySelector('#tool-group-options').textContent,/记忆只读，不开放写入/);
});

test('Pi save carries only checked read scopes and explicit local command opt in',async t => {
  const f = await fixture(t);f.d.querySelector('#new-task').click();
  f.form.elements.name.value='只读巡检';f.form.elements.body.value='查工作台与 GPU';
  change(f,'[name=tool_groups][value=workspace]',true);change(f,'[name=tool_groups][value=ssh]',true);
  change(f,'[name=allow_commands]',true);await submit(f);
  const call = f.calls.find(item => item.url === '/api/schedules/save');
  assert.equal(call.body.assistant_id,'workbench');assert.deepEqual(call.body.tool_groups,['workspace','ssh']);
  assert.equal(call.body.allow_commands,true);assert.equal(call.body.enabled,false);
  assert.equal(call.opts.headers['X-Article-Studio'],'1');
});

test('legacy Pi task does not gain workbench permissions, and switching isolated clears scopes',async t => {
  const legacy = {...task};delete legacy.assistant_id;delete legacy.tool_groups;
  const f = await fixture(t,{tasks:[legacy]});f.d.querySelector('[data-action=edit]').click();await settle();
  assert.equal(f.form.elements.assistant_id.value,'isolated');assert.equal(f.d.querySelector('#tool-group-options').hidden,true);
  assert(f.d.querySelectorAll('[name=tool_groups]')[0].disabled);
  change(f,'[name=assistant_id]','workbench');change(f,'[name=tool_groups][value=memory]',true);
  change(f,'[name=assistant_id]','isolated');await submit(f);
  const payload = f.calls.find(item => item.url === '/api/schedules/save').body;
  assert.equal(payload.assistant_id,'isolated');assert.deepEqual(payload.tool_groups,[]);assert.equal(payload.id,taskId);assert.equal(payload.revision,3);
});

test('script save preserves interpreter/arguments, schedule and timezone while clearing Pi scope',async t => {
  const f = await fixture(t);f.d.querySelector('#new-task').click();
  change(f,'[name=tool_groups][value=web]',true);change(f,'[name=kind]','script');
  f.form.elements.name.value='巡检示例';f.form.elements.body.value='/usr/bin/python3 ~/.workbuddy-ai/bin/wb_watch_wechat.py --push';
  change(f,'[name=frequency]','daily');f.form.elements.ends_at.value='2026-11-01T08:00';
  await submit(f);const payload = f.calls.find(item => item.url === '/api/schedules/save').body;
  assert.equal(payload.body,'/usr/bin/python3 ~/.workbuddy-ai/bin/wb_watch_wechat.py --push');
  assert.equal(payload.kind,'script');assert.equal(payload.ends_at,'2026-11-01T08:00:00+08:00');
  assert.deepEqual(payload.weekdays,[0,1,2,3,4,5,6]);assert.equal(payload.assistant_id,'isolated');assert.deepEqual(payload.tool_groups,[]);
});

test('WeChat executor retains specialized restrictions and cannot carry general tool scopes',async t => {
  const f = await fixture(t);f.d.querySelector('#new-task').click();
  change(f,'[name=tool_groups][value=ssh]',true);change(f,'[name=allow_commands]',true);
  change(f,'[name=result_destination]','clues');change(f,'[name=kind]','wechat_clues');
  assert.equal(f.form.elements.result_destination.value,'runs');assert.equal(f.form.elements.result_destination.disabled,true);
  assert.equal(f.form.elements.allow_commands.checked,false);assert.equal(f.form.elements.allow_commands.disabled,true);
  assert.equal(f.d.querySelector('#assistant-select-label').hidden,true);assert.equal(f.d.querySelector('#tool-group-options').hidden,true);
  f.form.elements.name.value='微信分析';f.form.elements.body.value='只分析缓存';await submit(f);
  const payload = f.calls.find(item => item.url === '/api/schedules/save').body;
  assert.equal(payload.assistant_id,'isolated');assert.deepEqual(payload.tool_groups,[]);
  assert.equal(payload.allow_commands,false);assert.equal(payload.result_destination,'runs');assert.equal(payload.clue_id,'');
  assert.equal(f.d.querySelector('#wechat-mode-label').hidden,false);assert.equal(payload.wechat_mode,'incremental');assert.equal(payload.profile_id,'fake');
});

test('edit retains result ownership, exact daily fields and prevents silently replacing a removed model',async t => {
  const clueId = 'd'.repeat(32);
  const f = await fixture(t,{tasks:[{...task,profile_id:'removed',frequency:'daily',ends_at:'2026-11-01T00:00:00Z',result_destination:'clues',clue_id:clueId,result_category:'technology'}]});
  f.d.querySelector('[data-action=edit]').click();await settle();
  assert.equal(f.form.elements.profile_id.value,'removed');assert.equal(f.form.elements.clue_id.value,clueId);
  assert.equal(f.form.elements.ends_at.value,'2026-11-01T08:00');assert.equal(f.d.querySelector('#daily-options').hidden,false);
  await submit(f);assert.equal(f.postCalls().length,0);assert.match(f.d.querySelector('#form-error').textContent,/重新选择/);
  change(f,'[name=profile_id]','fake');await submit(f);
  const payload = f.calls.find(item => item.url === '/api/schedules/save').body;
  assert.equal(payload.clue_id,clueId);assert.equal(payload.result_destination,'clues');assert.equal(payload.result_category,'technology');
  assert.deepEqual(payload.weekdays,[0]);assert.equal(payload.revision,3);
});

test('ten second refresh preserves search/filter, open menu, selected revision and editor inputs',async t => {
  const f = await fixture(t,{tasks:[task]});
  f.d.querySelector('#batch-mode').click();select(f);search(f,'每日');change(f,'#task-filter','paused');
  f.d.querySelector('.task-more').open=true;f.d.querySelector('[data-action=edit]').click();await settle();
  f.form.elements.body.value='尚未保存的内容';f.d.querySelector('#advanced-settings').open=true;
  f.listing.tasks=[{...task,revision:4}];f.intervals[0].callback();await settle();
  assert.equal(f.d.querySelector('#search').value,'每日');assert.equal(f.d.querySelector('#task-filter').value,'paused');
  assert.equal(f.d.querySelector('.task-more').open,true);assert.equal(f.d.querySelector('[data-select-task]').checked,true);
  assert.equal(f.form.elements.body.value,'尚未保存的内容');assert.equal(f.d.querySelector('#advanced-settings').open,true);
  f.d.querySelector('#batch-pause').click();await settle();
  const payload=f.calls.find(item=>item.url==='/api/schedules/batch').body;
  assert.deepEqual(payload,{action:'pause',tasks:[{id:taskId,revision:3}]});
});

test('batch selects filtered tasks and submits revisions atomically without extra controls',async t => {
  const f = await fixture(t,{tasks:[task,{...task,id:otherId,revision:7,enabled:true}]});
  f.d.querySelector('#batch-mode').click();assert.equal(f.d.querySelector('#batch-resume').disabled,true);
  change(f,'#task-filter','paused');change(f,'#select-all',true);
  assert.match(f.d.querySelector('#selection-count').textContent,/1/);
  f.d.querySelector('#batch-resume').click();await settle();
  assert.deepEqual(f.postCalls().map(call=>({url:call.url,body:call.body})),[{url:'/api/schedules/batch',body:{action:'resume',tasks:[{id:taskId,revision:3}]}}]);
  assert.equal(f.d.querySelector('#batch-pause').disabled,true);
});

test('pending batch blocks inline run, enable and delete, including delegated synthetic clicks',async t => {
  const pending=deferred();
  const f=await fixture(t,{tasks:[task],handler:call=>call.url==='/api/schedules/batch'?pending.promise:undefined});
  f.d.querySelector('#batch-mode').click();select(f);f.d.querySelector('#batch-pause').click();await settle();
  for(const action of ['run','resume','delete']) {
    const button=f.d.querySelector('[data-action='+action+']');assert.equal(button.disabled,true);
    button.dispatchEvent(new f.w.MouseEvent('click',{bubbles:true}));
  }
  assert.equal(f.postCalls().length,1);assert.equal(f.d.querySelector('#batch-pause').disabled,true);
  pending.resolve(response({ok:true}));await settle();
  assert.equal(f.d.querySelector('[data-action=run]').disabled,false);
});

test('batch revision conflict refreshes, removes stale selection, asks to reselect and never retries',async t => {
  const f=await fixture(t,{tasks:[task],handler:call=>call.url==='/api/schedules/batch'?response({error:'所选任务已被更新，请刷新后重试'},false):undefined});
  f.d.querySelector('#batch-mode').click();select(f);f.listing.tasks=[{...task,revision:4}];
  f.d.querySelector('#batch-pause').click();await settle();
  assert.equal(f.postCalls().length,1);assert.equal(f.d.querySelector('[data-select-task]').checked,false);
  assert.equal(f.d.querySelector('#batch-pause').disabled,true);assert.match(f.d.querySelector('#message').textContent,/重新选择/);
  select(f);f.d.querySelector('#batch-pause').click();await settle();
  assert.equal(f.postCalls()[1].body.tasks[0].revision,4);
});

test('shared record search, task/status filters and paging are passed to the real API contract',async t => {
  const f=await fixture(t,{tasks:[task],handler:call=>call.url.startsWith('/api/schedules/runs?')?response({runs:[run],total:101,page:Number(params(call).get('page')),page_size:50,has_more:Number(params(call).get('page'))<3}):undefined});
  f.d.querySelector('[data-action=history]').click();await settle();
  assert.equal(params(f.runCalls().at(-1)).get('task_id'),taskId);
  search(f,'输出关键词');await f.flushSearch();change(f,'#run-status','failed');await settle();
  f.d.querySelector('#next-page').click();await settle();
  const query=params(f.runCalls().at(-1));assert.equal(query.get('q'),'输出关键词');assert.equal(query.get('status'),'failed');
  assert.equal(query.get('task_id'),taskId);assert.equal(query.get('page'),'2');assert.equal(query.get('page_size'),'50');
  assert.equal(f.d.querySelector('#page-label').textContent,'第 2 页');assert.equal(f.d.querySelector('#run-count').textContent,'共 101 条记录');
  f.d.querySelector('#previous-page').click();await settle();assert.equal(params(f.runCalls().at(-1)).get('page'),'1');
  f.d.querySelector('#all-runs').click();await settle();assert.equal(params(f.runCalls().at(-1)).get('task_id'),'');
  assert.equal(f.d.querySelector('#run-status').value,'failed');assert.equal(f.d.querySelector('#search').value,'输出关键词');
});

test('failed next page does not advance the rendered page and retries the same page',async t => {
  let failed=false;
  const f=await fixture(t,{handler:call=>{
    if(!call.url.startsWith('/api/schedules/runs?'))return;
    const page=Number(params(call).get('page'));
    if(page===2&&!failed){failed=true;return response({error:'分页读取失败'},false);}
    return response({runs:[run],total:101,page,page_size:50,has_more:true});
  }});
  f.d.querySelector('#run-tab').click();await settle();f.d.querySelector('#next-page').click();await settle();
  assert.equal(f.d.querySelector('#page-label').textContent,'第 1 页');assert.match(f.d.querySelector('#message').textContent,/分页读取失败/);
  f.d.querySelector('#next-page').click();await settle();
  assert.deepEqual(f.runCalls().map(call=>params(call).get('page')),['1','2','2']);
  assert.equal(f.d.querySelector('#page-label').textContent,'第 2 页');
});

test('stale successful and failed record responses cannot replace a fresh result or its status',async t => {
  const old=deferred(),older=deferred();
  const f=await fixture(t,{handler:call=>{
    if(!call.url.startsWith('/api/schedules/runs?'))return;
    const query=params(call).get('q');
    if(query==='旧失败')return old.promise;if(query==='旧成功')return older.promise;
    return response({runs:[{...run,snapshot:{...run.snapshot,name:query||'初始'}}],total:1,page:1,page_size:50,has_more:false});
  }});
  f.d.querySelector('#run-tab').click();await settle();
  search(f,'旧失败');f.flushSearch();await settle();search(f,'旧成功');f.flushSearch();await settle();
  search(f,'最新');await f.flushSearch();
  old.resolve(response({error:'旧查询失败'},false));older.resolve(response({runs:[run],total:1,page:1,page_size:50,has_more:false}));await settle();
  assert.match(f.d.querySelector('#runs').textContent,/最新/);assert.equal(f.d.querySelector('#message').textContent,'');
});

test('records show elapsed duration and preserve stop, clue collection, links and full details safely',async t => {
  const malicious={...run,snapshot:{...run.snapshot,name:'<script>bad</script>',body:'<img src=x>',cwd:'/private/full/path'},output:'<img onerror=evil()>',clue_id:'x&evil="yes',publication_error:'<svg onload=bad()>'};
  const active={...run,id:otherId,status:'running',finished_at:null};
  const collectable={...run,id:'d'.repeat(32)};
  const f=await fixture(t,{runs:[malicious,active,collectable]});f.d.querySelector('#run-tab').click();await settle();
  assert.match(f.d.querySelector('#runs').textContent,/1 分 5 秒/);assert.equal(f.d.querySelector('#runs script'),null);assert.equal(f.d.querySelector('#runs svg'),null);
  assert.equal(f.d.querySelector('[data-cancel]').textContent,'停止');
  assert(f.d.querySelector('.run-clue').getAttribute('href').includes(encodeURIComponent(malicious.clue_id)));
  f.d.querySelector('[data-run]').click();await settle();
  assert.equal(f.d.querySelector('#run-detail img'),null);assert.equal(f.d.querySelector('#run-output').textContent,malicious.output);
  assert(f.d.querySelector('#run-meta').textContent.includes('/private/full/path'));
  f.d.querySelector('[data-publish]').click();await settle();f.d.querySelector('[data-cancel]').click();await settle();
  assert(f.postCalls().some(call=>call.url==='/api/schedules/'+collectable.id+'/publish'));
  assert(f.postCalls().some(call=>call.url==='/api/schedules/'+otherId+'/cancel'));
});

test('WeChat trial records show validated per-run counts, pending work and actual candidate links without re-collecting',async t => {
  const clues=['1'.repeat(32),'2'.repeat(32),'3'.repeat(32),'4'.repeat(32)];
  const specialized={...run,snapshot:{...task,kind:'wechat_clues',wechat_mode:undefined},output:JSON.stringify({scope:'本机微信缓存最近七天，最多30条；不是实时全量采集',processed:10,clues,has_more:true,state:'候选待核对，未建立或接受待办'})};
  const f=await fixture(t,{tasks:[{...task,kind:'wechat_clues',wechat_mode:'incremental'}],runs:[specialized]});
  f.d.querySelector('#run-tab').click();await settle();
  const summary=f.d.querySelector('.run-result-summary').textContent;
  assert.match(summary,/缓存试跑（本机最近 7 天缓存，最多 30 条）/);assert.match(summary,/本轮分析 10 条/);
  assert.match(summary,/4 条待核对线索/);assert.match(summary,/仍有待分析消息，尚未全部处理/);
  assert(!summary.includes('持续增量'));assert.equal(f.d.querySelector('[data-publish]'),null);
  assert.deepEqual([...f.d.querySelectorAll('.run-result-links a')].map(link=>link.getAttribute('href')),clues.map(id=>'/clues.html?clue='+id));
  assert.equal(f.postCalls().length,0);
});

test('incremental results show exact recorded progress, skipped count and completion within scope',async t => {
  const specialized={...run,snapshot:{...task,kind:'wechat_clues',wechat_mode:'incremental'},output:JSON.stringify({processed:9,skipped:2,clues:['a'.repeat(32),'a'.repeat(32)],has_more:false})};
  const f=await fixture(t,{runs:[specialized]});f.d.querySelector('#run-tab').click();await settle();
  const summary=f.d.querySelector('.run-result-summary').textContent;
  assert.match(summary,/持续增量（本机最近 7 天消息） · 本轮分析 9 条 · 跳过 2 条 · 1 条待核对线索 · 本轮待分析消息已处理完/);
  assert.equal(f.d.querySelectorAll('.run-result-links a').length,1);
  assert.equal(f.d.querySelector('[data-publish]'),null);
});

test('large valid WeChat results retain all candidate links behind compact expansion',async t => {
  const clues=Array.from({length:9},(_,index)=>(index+1).toString().repeat(32));
  const specialized={...run,snapshot:{...task,kind:'wechat_clues'},output:JSON.stringify({processed:30,clues,has_more:true})};
  const f=await fixture(t,{runs:[specialized]});f.d.querySelector('#run-tab').click();await settle();
  assert.equal(f.d.querySelectorAll('.run-result-links > a').length,6);
  assert.equal(f.d.querySelector('.run-clue-more').open,false);
  assert.equal(f.d.querySelectorAll('.run-clue-more a').length,3);
  assert.equal(f.d.querySelector('.run-clue-more summary').textContent,'其余 3 条线索');
});

test('malformed WeChat summaries never invent counts or trust unsafe IDs and do not expose generic collection',async t => {
  const values=['not json',null,[],{processed:-1,clues:[],has_more:false},{processed:'10',clues:[],has_more:true},
    {processed:1,skipped:-2,clues:[]},{processed:1,clues:['<img onerror=evil()>'],has_more:false},
    {processed:1,clues:[42],has_more:false},{processed:1,clues:[],has_more:'false'}];
  const records=values.map((value,index)=>({...run,id:String(index).repeat(32),snapshot:{...task,kind:'wechat_clues'},output:typeof value==='string'?value:JSON.stringify(value)}));
  const f=await fixture(t,{runs:records});f.d.querySelector('#run-tab').click();await settle();
  assert.equal(f.d.querySelectorAll('.run-result-summary').length,values.length);
  assert([...f.d.querySelectorAll('.run-result-summary')].every(summary=>summary.textContent==='本轮结果未能解析，请打开详情核对分析范围与线索。'));
  assert.equal(f.d.querySelectorAll('.run-result-links a').length,0);assert.equal(f.d.querySelector('[data-publish]'),null);
  assert.equal(f.d.querySelector('#runs img'),null);
});

test('failure summaries explain the latest real failure, stay escaped and bounded, and retain full detail output',async t => {
  const reason='微信采集或索引未成功：访问微信文件尚未完成，请检查 macOS 授权或文件状态；旧资料保留；未开始模型分析';
  const records=['failed','timeout','interrupted','cancelled'].map((status,index)=>({...run,id:String(index).repeat(32),snapshot:{...task,kind:'wechat_clues',wechat_mode:'incremental'},status,exit_code:null,output:index===0?reason:'<img src=x onerror=evil()>\n'+ '输出'.repeat(140)}));
  const f=await fixture(t,{runs:records});f.d.querySelector('#run-tab').click();await settle();
  assert.equal(f.d.querySelector('.run-failure-summary').textContent,reason);
  assert.equal(f.d.querySelectorAll('.run-failure-summary').length,4);
  assert([...f.d.querySelectorAll('.run-failure-summary')].every(summary=>[...summary.textContent].length<=241));
  assert.equal(f.d.querySelector('#runs img'),null);assert.equal(f.d.querySelector('[data-publish]'),null);
  f.d.querySelectorAll('[data-run]')[1].click();await settle();
  assert.equal(f.d.querySelector('#run-output').textContent,records[1].output);assert.equal(f.d.querySelector('#run-output img'),null);
});

test('positive subsecond duration is honest and trigger labels never assert unrecorded catch-up provenance',async t => {
  const records=[{...run,trigger:'scheduled',finished_at:'2026-10-05T02:00:00.159Z'},
    {...run,id:otherId,trigger:'legacy-unknown',finished_at:run.started_at},
    {...run,id:'d'.repeat(32),trigger:'manual'}];
  const f=await fixture(t,{runs:records});f.d.querySelector('#run-tab').click();await settle();
  const rows=[...f.d.querySelectorAll('.run-row')];
  assert.match(rows[0].textContent,/耗时 <1 秒 · 自动运行/);
  assert.match(rows[1].textContent,/耗时 0 秒 · 来源未知/);
  assert.match(rows[2].textContent,/手动运行/);
  assert(!f.d.querySelector('#runs').textContent.includes('恢复后补跑'));
});

test('ordinary Pi and script outputs remain collectable while linked and empty results have no duplicate button',async t => {
  const records=[{...run,snapshot:{...task,kind:'pi'}},{...run,id:otherId,snapshot:{...task,kind:'script'}},
    {...run,id:'d'.repeat(32),clue_id:'e'.repeat(32)},{...run,id:'f'.repeat(32),output:' \n '},
    {...run,id:'0'.repeat(32),output:null}];
  const f=await fixture(t,{runs:records});f.d.querySelector('#run-tab').click();await settle();
  assert.deepEqual([...f.d.querySelectorAll('[data-publish]')].map(button=>button.dataset.publish),[runId,otherId]);
  assert.equal(f.d.querySelectorAll('.run-clue').length,1);
});

test('partial WeChat results show persisted progress, candidate links, batch errors, cooldown and retry time',async t => {
  const clues=['1'.repeat(32),'2'.repeat(32)], error={batch_id:'f'.repeat(64),message:'模型引用未通过校验',attempts:2,next_retry_at:'2026-10-06T01:30:00Z',documents:3};
  const partial={...run,status:'partial',snapshot:{...task,kind:'wechat_clues',wechat_mode:'incremental'},output:JSON.stringify({processed:10,skipped:1,clues,has_more:true,deferred:3,errors:[error]})};
  const f=await fixture(t,{tasks:[{...task,last_run:partial}],runs:[partial]});
  assert.match(f.d.querySelector('.task-last').textContent,/最近 · 部分完成/);
  f.d.querySelector('#run-tab').click();await settle();
  assert.equal(f.d.querySelector('#runs .state-pill').textContent,'部分完成');
  assert.match(f.d.querySelector('.run-result-summary').textContent,/持续增量（本机最近 7 天消息） · 本轮分析 10 条 · 跳过 1 条 · 2 条待核对线索/);
  assert.match(f.d.querySelector('.run-result-summary').textContent,/仍有待分析消息，尚未全部处理/);
  assert.equal(f.d.querySelectorAll('.run-result-links a').length,2);
  assert.match(f.d.querySelector('.run-failure-summary').textContent,/3 条消息 · 已尝试 2 次：模型引用未通过校验/);
  assert.match(f.d.querySelector('.run-retry-summary').textContent,/待重试或核对 3 条消息/);
  assert.match(f.d.querySelector('.run-error-item .run-retry-summary').textContent,/可重试时间：2026\/10\/06 09:30:00（北京时间）/);
  assert.equal(f.d.querySelector('[data-publish]'),null);
  change(f,'#run-status','partial');await settle();assert.equal(params(f.runCalls().at(-1)).get('status'),'partial');
  f.d.querySelector('[data-run]').click();await settle();assert.match(f.d.querySelector('#run-meta').textContent,/部分完成/);
  assert.equal(f.d.querySelector('#run-output').textContent,partial.output);
});

test('structured failures and stops retain saved WeChat counts and clues instead of hiding progress behind JSON',async t => {
  const records=['failed','timeout','cancelled','interrupted'].map((status,index)=>({...run,id:String(index).repeat(32),status,snapshot:{...task,kind:'wechat_clues',wechat_mode:'incremental'},output:JSON.stringify({processed:5,skipped:0,clues:['a'.repeat(32)],has_more:false,deferred:1,errors:[{message:'本批待重试',documents:1,attempts:1,next_retry_at:'2026-10-06T01:45:00+00:00'}]})}));
  const f=await fixture(t,{runs:records});f.d.querySelector('#run-tab').click();await settle();
  const rows=[...f.d.querySelectorAll('.run-row')];
  for(const row of rows) {
    assert.match(row.querySelector('.run-result-summary').textContent,/本轮分析 5 条.*1 条待核对线索.*本轮未全部完成/);
    assert.equal(row.querySelectorAll('.run-result-links a').length,1);
    assert.match(row.querySelector('.run-failure-summary').textContent,/本批待重试/);
    assert(!row.textContent.includes('{"processed"'));
    assert.equal(row.querySelector('[data-publish]'),null);
  }
});

test('capture warning exposes old-cache fallback without claiming the source was newly acquired',async t => {
  const warning='<img src=x onerror=evil()>新采集未成功', specialized={...run,status:'partial',snapshot:{...task,kind:'wechat_clues',wechat_mode:'incremental'},output:JSON.stringify({processed:8,clues:['a'.repeat(32)],has_more:true,errors:[],deferred:0,capture:{fresh:false,status:'error',warning}})};
  const f=await fixture(t,{runs:[specialized]});f.d.querySelector('#run-tab').click();await settle();
  assert.equal(f.d.querySelector('.run-capture-warning').textContent,'本次未获得新采集结果，仅处理已有成功缓存。'+warning);
  assert.equal(f.d.querySelector('#runs img'),null);
  assert.match(f.d.querySelector('.run-result-summary').textContent,/本轮分析 8 条/);
  assert(!f.d.querySelector('#runs').textContent.includes('新采集成功'));
  assert.equal(f.postCalls().length,0);
});

test('error text is escaped and bounded, ISO or legacy numeric retry time is safe, and extra errors can be expanded',async t => {
  const errors=Array.from({length:4},(_,index)=>({message:'<svg onload=evil()>\n'+'错误'.repeat(180),documents:index,attempts:1,next_retry_at:index===0?1791248400:'2026-10-06T01:30:00Z'}));
  const partial={...run,status:'partial',snapshot:{...task,kind:'wechat_clues'},output:JSON.stringify({processed:4,clues:[],has_more:true,errors,deferred:4})};
  const f=await fixture(t,{runs:[partial]});f.d.querySelector('#run-tab').click();await settle();
  assert.equal(f.d.querySelectorAll('.run-errors > .run-error-item').length,2);
  assert.equal(f.d.querySelectorAll('.run-errors-more .run-error-item').length,2);
  assert.equal(f.d.querySelector('.run-errors-more').open,false);assert.equal(f.d.querySelector('.run-errors-more summary').textContent,'其余 2 条错误');
  assert.equal(f.d.querySelector('#runs svg'),null);
  assert([...f.d.querySelectorAll('.run-failure-summary')].every(node=>[...node.textContent].length<300));
  assert.equal(f.d.querySelectorAll('.run-error-item .run-retry-summary').length,4);
  assert([...f.d.querySelectorAll('.run-error-item .run-retry-summary')].every(node=>node.textContent.includes('（北京时间）')));
});

test('invalid error or retry metadata cannot erase valid saved progress or fabricate cooldown and time',async t => {
  const records=[{processed:7,clues:['a'.repeat(32)],has_more:true,deferred:-4,errors:[{message:'错误已记录',documents:'3',attempts:-1,next_retry_at:'<img onerror=evil()>'}]},
    {processed:7,clues:['a'.repeat(32)],has_more:true,errors:'broken'},
    {processed:7,clues:['a'.repeat(32)],has_more:true,errors:[{message:'重试日期无效',attempts:1,documents:1,next_retry_at:'2026-02-30T01:30:00Z'}]}].map((data,index)=>({...run,id:String(index).repeat(32),status:'partial',snapshot:{...task,kind:'wechat_clues'},output:JSON.stringify(data)}));
  const f=await fixture(t,{runs:records});f.d.querySelector('#run-tab').click();await settle();
  assert([...f.d.querySelectorAll('.run-result-summary')].every(node=>/本轮分析 7 条.*1 条待核对线索/.test(node.textContent)));
  assert.equal(f.d.querySelectorAll('.run-result-links a').length,3);
  assert.equal(f.d.querySelectorAll('.run-retry-summary').length,0);
  assert.equal(f.d.querySelector('#runs img'),null);
  assert.equal(f.d.querySelectorAll('.run-failure-summary').length,5);
  assert.match(f.d.querySelector('#runs').textContent,/部分错误或重试信息格式无效/);
  assert(!f.d.querySelector('#runs').textContent.includes('3 条消息'));
});

test('empty partial and cooling-only results never claim completed processing; valid empty success stays honest',async t => {
  const records=[{status:'partial',data:{processed:0,clues:[],has_more:false,errors:[],deferred:0}},
    {status:'partial',data:{processed:0,clues:[],has_more:false,errors:[],deferred:6}},
    {status:'succeeded',data:{processed:0,clues:[],has_more:false,errors:[],deferred:0}}].map((entry,index)=>({...run,id:String(index).repeat(32),status:entry.status,snapshot:{...task,kind:'wechat_clues'},output:JSON.stringify(entry.data)}));
  const f=await fixture(t,{runs:records});f.d.querySelector('#run-tab').click();await settle();
  const rows=[...f.d.querySelectorAll('.run-row')];
  assert.match(rows[0].querySelector('.run-result-summary').textContent,/本轮未全部完成/);
  assert.match(rows[1].querySelector('.run-result-summary').textContent,/本轮未全部完成/);assert.match(rows[1].textContent,/待重试或核对 6 条消息/);
  assert.match(rows[2].querySelector('.run-result-summary').textContent,/本轮没有待分析消息/);
  assert.equal(f.d.querySelector('[data-publish]'),null);
});

test('needs-review batches stop automatic retry without inventing a retry date and explain manual task retry',async t => {
  const errors=[{message:'引用三次未通过',documents:4,attempts:3,state:'needs_review',next_retry_at:null},
    {message:'另一批待重试',documents:2,attempts:1,state:'waiting',next_retry_at:'2026-10-06T01:30:00Z'}];
  const partial={...run,status:'partial',snapshot:{...task,kind:'wechat_clues',wechat_mode:'incremental'},output:JSON.stringify({processed:8,clues:['a'.repeat(32)],has_more:true,deferred:6,errors})};
  const f=await fixture(t,{runs:[partial]});f.d.querySelector('#run-tab').click();await settle();
  const rows=[...f.d.querySelectorAll('.run-error-item')];
  assert.equal(rows[0].querySelector('.run-retry-summary').textContent,'多次失败，待核对；立即运行可手动重试。');
  assert(!rows[0].textContent.includes('可重试时间'));assert(!rows[0].textContent.includes('1970'));
  assert.match(rows[1].querySelector('.run-retry-summary').textContent,/可重试时间：2026\/10\/06 09:30:00（北京时间）/);
  assert.match(f.d.querySelector('.run-retry-summary').textContent,/待重试或核对 6 条消息/);
  assert.equal(f.postCalls().length,0);
});

test('malformed structured terminal output uses a human explanation while details retain raw output',async t => {
  const outputs=['{"processed":-1,"clues":[]}', '{"errors":[{"message":"失效"}]}','{"processed":2'];
  const records=outputs.map((output,index)=>({...run,id:String(index).repeat(32),status:index===0?'partial':'failed',snapshot:{...task,kind:'wechat_clues'},output}));
  const f=await fixture(t,{runs:records});f.d.querySelector('#run-tab').click();await settle();
  assert.equal(f.d.querySelectorAll('.run-failure-summary').length,3);
  assert([...f.d.querySelectorAll('.run-failure-summary')].every(node=>node.textContent==='运行结果格式异常，请打开详情核对已保存进度和错误。'));
  assert(!f.d.querySelector('#runs').textContent.includes('{"'));
  f.d.querySelector('[data-run]').click();await settle();assert.equal(f.d.querySelector('#run-output').textContent,outputs[0]);
});

test('task and error text remain escaped and errors visible; manual controls keep distinct routes',async t => {
  const f=await fixture(t,{tasks:[{...task,name:'<img src=x onerror=evil()>',kind:'script'}],handler:call=>call.url.endsWith('/resume')?response({error:'<img>启用失败'},false):undefined});
  assert.equal(f.d.querySelector('#tasks img'),null);
  f.d.querySelector('[data-action=run]').click();await settle();f.d.querySelector('[data-action=resume]').click();await settle();
  assert(f.postCalls().some(call=>call.url==='/api/schedules/'+taskId+'/run'));
  assert(f.postCalls().some(call=>call.url==='/api/schedules/'+taskId+'/resume'));
  assert.equal(f.d.querySelector('#message img'),null);assert.match(f.d.querySelector('#message').textContent,/启用失败/);
});

test('run deep link opens saved details without starting any work',async t => {
  const f=await fixture(t,{url:'http://127.0.0.1:8766/schedules.html?run='+runId});
  assert.equal(f.d.querySelector('#run-detail').open,true);assert.equal(f.d.querySelector('#run-view').hidden,false);
  assert.equal(f.postCalls().length,0);assert.equal(f.d.querySelector('#run-output').textContent,run.output);
});


test('conversation-day review stays explicit, survives editing and clears on one-shot mode',async t => {
  const wechat={...task,kind:'wechat_clues',wechat_mode:'incremental',wechat_review:true,assistant_id:'isolated',tool_groups:[]};
  const f=await fixture(t,{tasks:[wechat]});f.d.querySelector('[data-action=edit]').click();await settle();
  assert.equal(f.form.elements.wechat_review.checked,true);assert.equal(f.d.querySelector('#wechat-review-label').hidden,false);
  await submit(f);assert.equal(f.postCalls().at(-1).body.wechat_review,true);
  f.d.querySelector('[data-action=edit]').click();await settle();change(f,'[name=wechat_mode]','once');
  assert.equal(f.d.querySelector('#wechat-review-label').hidden,true);
  await submit(f);assert.equal(f.postCalls().at(-1).body.wechat_review,false);
});

test('daily review receipts show review and remaining coverage independently of new intake',async t => {
  const item={...run,snapshot:{...task,kind:'wechat_clues',wechat_mode:'incremental'},output:JSON.stringify({processed:3,reviewed:20,clues:[],has_more:false,daily_review:{pending_versions:80}})};
  const f=await fixture(t,{runs:[item]});f.d.querySelector('#run-tab').click();await settle();
  assert.match(f.d.querySelector('#runs').textContent,/复查 20 个消息版本/);
  assert.match(f.d.querySelector('#runs').textContent,/待复查 80 个版本/);
  assert.equal(f.postCalls().length,0);
});

test('clue and radar receipts display validated quote mapping counts',async t => {
  const records=['wechat_clues','wechat_radar'].map((kind,i)=>({...run,id:String(i+4).repeat(32),snapshot:{...task,kind},output:JSON.stringify({processed:1,skipped:0,clues:[],has_more:false,format:{quote_mapped:i+2}})}));
  const f=await fixture(t,{runs:records});f.d.querySelector('#run-tab').click();await settle();
  const text=f.d.querySelector('#runs').textContent;
  assert.match(text,/精确映射为证据编号 2 次/);assert.match(text,/精确映射为证据编号 3 次/);
});
test('invalid quote mapping metadata cannot inject public receipt content',async t => {
  const records=['-1','<img src=x onerror=alert(1)>',1.5].map((count,i)=>({...run,id:String(i+6).repeat(32),snapshot:{...task,kind:'wechat_radar'},output:JSON.stringify({processed:1,has_more:false,format:{quote_mapped:count}})}));
  const f=await fixture(t,{runs:records});f.d.querySelector('#run-tab').click();await settle();
  assert(!f.d.querySelector('#runs').textContent.includes('精确映射'));assert.equal(f.d.querySelector('#runs img'),null);
});
