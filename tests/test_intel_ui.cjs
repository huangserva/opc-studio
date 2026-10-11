const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs');
const {JSDOM}=require('jsdom');const tick=()=>new Promise(r=>setImmediate(r));
const BIZ='MzB5NTkwMDAwMDE=',OTHER='MzB5NTkwMDAwMDI=';
const article={id:'a'.repeat(32),fakeid:BIZ,aid:'2247483001',source:'album',link_ok:1,link:'https://mp.weixin.qq.com/s/AAAAAAAAAAAA',name:'占位公众号',title:'占位文章<标题>',published_at:1700000000,published_label:'2023-11-15 00:00',body_status:'awaiting_confirm',chars:3726,images:['https://mmbiz.qpic.cn/x'],error:'',drop_id:'',job_id:'',added_at:'2026-10-09T00:00:00Z'};
async function fixture(options={}){
 const dom=new JSDOM(fs.readFileSync('public/intel.html','utf8'),{url:'http://127.0.0.1:8766/intel.html',runScripts:'outside-only'});
 const w=dom.window,d=w.document,calls=[],timers=[];
 w.open=()=>({document:{write(){},close(){}}});
 // 轮询用 setInterval；测试里手动触发，避免真的等 1.5 秒。
 w.setInterval=(fn,ms)=>{timers.push(fn);return timers.length};
 w.clearInterval=()=>{timers.length=0};
 w.fireTimers=async()=>{const pending=timers.splice(0);for(const fn of pending)fn();await tick();await tick()};
 w.fetch=async(url,opts={})=>{const body=opts.body&&JSON.parse(opts.body);calls.push({url,body,opts});let data={};
  // L2 用：把定时任务开关的请求挂起，用来观察「请求进行中」的禁用是不是单独管的。
  if(options.pauseGate&&url.startsWith('/api/schedules/'))await options.pauseGate;
  if(url==='/api/intel/status')data={schema:'intel-wechat-mp/v1',mode:'wechat_mp',cooldown_until:options.cooldown_until||0,cooldown_label:options.cooldown_label||'',credentials:options.credentials||'ready',detail:(options.credentials==='missing')?'需重新登录后台：钥匙串里没有公众号后台凭证':'后台凭证可用',rate_strikes:options.rate_strikes||0,subscriptions:options.subs||1,articles:options.articles??1,awaiting:options.awaiting??1,busy:!!options.busy,active_job:options.busy?{id:'j'.repeat(32),name:'占位公众号',status:'running'}:null};
  else if(url==='/api/intel/subscriptions')data={subscriptions:options.subs===0?[]:[{fakeid:BIZ,name:'占位公众号',article_count:1},{fakeid:OTHER,name:'另一个号',article_count:0}]};
  else if(url==='/api/intel/recommendations/scan')data={scan:{links:120,skipped:12,known:100,needs_fetch:8,accounts:20},resolve:{attempted:8,resolved:7,failed:1,stopped:''},listing:options.listing||{items:[],pending_links:1,failed_links:0,ignored:0,subscribed:0}};
  else if(url==='/api/intel/recommendations')data=options.listing||{items:[],pending_links:options.pending||0,failed_links:0,ignored:0,subscribed:1};
  else if(url.startsWith('/api/intel/recommendations/'))data=options.recoResult||{fakeid:BIZ,name:'占位公众号',ignored:true};
  else if(url==='/api/schedules')data={tasks:options.task===null?[]:[{id:'k'.repeat(32),kind:'intel_wechat_mp',enabled:!!options.taskEnabled,frequency:'daily',at:'08:00',name:'公众号订阅（每日抓最新）'}],timezone:'Asia/Shanghai'};
  else if(url.startsWith('/api/intel/articles?'))data={articles:options.empty?[]:[Object.assign({},article,options.noLink?{link:'',link_ok:0}:{},options.pathSource?{source:options.pathSource}:{},options.badLink?{link:options.badLink,link_ok:1}:{},options.missingReason?{link_missing_reason:options.missingReason}:{})],total:options.empty?0:1,albums:options.albums||[]};
  else if(url.startsWith('/api/intel/accounts'))data={candidates:options.candidates===undefined?[{fakeid:BIZ,nickname:'占位公众号',signature:'简介一',round_head_img:''},{fakeid:OTHER,nickname:'占位公众号',signature:'同名例子',round_head_img:''}]:options.candidates};
  else if(url==='/api/intel/identify')data=options.identify||{fakeid:BIZ,nickname:'占位公众号',title:'占位文章',chars:3726,published_at:1700000000,url:'https://mp.weixin.qq.com/s/AAAAAAAAAAAA'};
  else if(url==='/api/intel/jobs/'+(options.jobId||'j'.repeat(32)))data={id:options.jobId||'j'.repeat(32),status:options.jobStatus||'done',status_label:'已完成',reason:options.jobReason||'列表 20 篇，新增 20 篇，正文 3 篇待确认',found:20,added:20,bodies:3,pages:1};
  else if(url==='/api/intel/run')data=options.reused?{id:'j'.repeat(32),status:'running',reused:true,name:'另一个号'}:{id:options.jobId||'j'.repeat(32),status:'running'};
  else if(url.endsWith('/intake'))data={drop_id:'d'.repeat(32),provenance:'intel:wechat_mp:'+BIZ+':2247483001',document_pending:false};
  else if(url.startsWith('/api/intel/articles/'))data=options.detail||{...article,body:'占位正文'};
  return {ok:true,json:async()=>data};};
 w.eval(fs.readFileSync('public/intel.js','utf8'));await tick();await tick();return {w,d,calls};}

test('page loads without sending any run request',async()=>{const f=await fixture();
 assert(f.calls.some(c=>c.url==='/api/intel/status'));assert(f.calls.every(c=>c.url!=='/api/intel/run'));
 assert.match(f.d.querySelector('#itl-list').textContent,/占位文章/);f.w.close();});

test('duplicate account names are shown as separate choices and never auto-selected',async()=>{
 const f=await fixture();f.d.querySelector('#itl-query').value='占位号';f.d.querySelector('#itl-search').click();await tick();
 const buttons=f.d.querySelectorAll('.itl-candidate');
 assert.equal(buttons.length,2);assert.notEqual(buttons[0].dataset.fakeid,buttons[1].dataset.fakeid);
 assert.match(f.d.querySelector('#itl-status').textContent,/请确认 fakeid/);
 assert(f.calls.every(c=>!(c.url==='/api/intel/subscriptions'&&c.opts.method==='POST')));  // 搜索本身不录入
 buttons[1].click();await tick();await tick();await tick();
 const post=f.calls.find(c=>c.url==='/api/intel/subscriptions'&&c.opts.method==='POST');
 assert.equal(post.body.fakeid,OTHER);assert.equal(post.opts.headers['X-Article-Studio'],'1');f.w.close();});

test('empty search results are reported as not found, not as a failure of the source',async()=>{
 const f=await fixture({candidates:[]});f.d.querySelector('#itl-query').value='查无此号';f.d.querySelector('#itl-search').click();await tick();
 assert.match(f.d.querySelector('#itl-candidates').textContent,/没有搜到/);f.w.close();});

test('pasted article link reverse-looks-up the account and asks before recording',async()=>{
 const f=await fixture();f.d.querySelector('#itl-link').value='https://mp.weixin.qq.com/s/AAAAAAAAAAAA';
 f.d.querySelector('#itl-by-link').click();await tick();
 assert(f.calls.some(c=>c.url==='/api/intel/identify'&&c.body.url.endsWith('AAAAAAAAAAAA')));
 assert(f.calls.every(c=>!(c.url==='/api/intel/subscriptions'&&c.opts.method==='POST')));
 assert.match(f.d.querySelector('#itl-status').textContent,/确认无误后/);f.w.close();});

test('both run buttons send the selected fakeid and mode, and nothing auto-starts',async()=>{
 const f=await fixture();f.d.querySelector('#itl-run-latest').click();await tick();
 let post=f.calls.find(c=>c.url==='/api/intel/run');
 assert.equal(post.body.fakeid,BIZ);assert.equal(post.body.mode,'latest');
 await f.w.fireTimers();
 f.d.querySelector('#itl-run-history').click();await tick();
 post=f.calls.filter(c=>c.url==='/api/intel/run').at(-1);
 assert.equal(post.body.mode,'history');f.w.close();});

test('rate limited job stops polling and surfaces the cooldown deadline',async()=>{
 const f=await fixture({jobId:'k'.repeat(32),jobStatus:'rate_limited',jobReason:'命中公众号后台限流（200013），已停止本次抓取'});
 f.d.querySelector('#itl-run-latest').click();await tick();await f.w.fireTimers();
 assert.match(f.d.querySelector('#itl-status').textContent,/限流冷却/);
 assert.equal(f.d.querySelector('#itl-status').dataset.tone,'bad');
 assert.equal(f.calls.filter(c=>c.url.startsWith('/api/intel/jobs/')).length,1);  // 停了，没有轮询到底
 f.w.close();});

test('credential loss is surfaced verbatim rather than reported as an empty source',async()=>{
 const f=await fixture({credentials:'missing'});assert.match(f.d.querySelector('#itl-quota').textContent,/需重新登录后台/);
 assert(f.d.querySelector('#itl-quota').querySelector('[data-tone=bad]'));f.w.close();});

test('cooldown deadline is displayed to the user, not hidden behind a generic error',async()=>{
 const f=await fixture({cooldown_until:1789000000,cooldown_label:'2026-10-09 20:00',rate_strikes:2});
 assert.match(f.d.querySelector('#itl-quota').textContent,/接口冷却至 2026-10-09 20:00/);
 assert(f.d.querySelector('#itl-quota').querySelector('[data-tone]'));f.w.close();});

test('partial rate-limit strikes are shown as a warning before the cooldown starts',async()=>{
 const f=await fixture({rate_strikes:2});
 assert.match(f.d.querySelector('#itl-quota').textContent,/限流累计 2 次/);
 assert.match(f.d.querySelector('#itl-quota').textContent,/连续 3 次进入 6 小时冷却/);f.w.close();});

test('article titles are escaped and the list never leaks full body text',async()=>{
 const f=await fixture();const row=f.d.querySelector('.itl-article');
 assert.equal(row.querySelector('script'),null);
 assert.match(row.querySelector('h3').textContent,/占位文章<标题>/);
 assert.equal(row.textContent.includes('占位正文'),false);
 assert.match(row.textContent,/正文 3726 字/);f.w.close();});

test('send to recycle bin is an explicit click carrying contract provenance',async()=>{
 const f=await fixture();f.d.querySelector('[data-action=push]').click();await tick();
 const post=f.calls.find(c=>c.url.endsWith('/intake'));
 assert(post);assert.deepEqual(post.body,{});assert.equal(post.opts.headers['X-Article-Studio'],'1');
 assert.match(f.d.querySelector('#itl-status').textContent,/intel:wechat_mp:/);
 assert.match(f.d.querySelector('#itl-status').textContent,/知识库尚未入库/);f.w.close();});

test('already sent article stops offering the button again',async()=>{
 const f=await fixture();assert(f.d.querySelector('[data-action=push]'));
 assert.equal(f.d.querySelectorAll('[data-action=push]').length,1);f.w.close();});

test('empty list explains the workflow instead of blaming the source',async()=>{
 const f=await fixture({empty:true});assert.match(f.d.querySelector('#itl-list').textContent,/抓最新/);
 assert.equal(f.d.querySelector('#itl-list').textContent.includes('失败'),false);f.w.close();});
// ── 二期 A：推荐订阅 ──────────────────────────────────────────────────────
const listing=()=>({items:[{fakeid:BIZ,nickname:'占位公众号',count:3,name_known:true,
   sources:[{kind:'intake',count:1},{kind:'knowledge',count:2}],examples:[{kind:'intake',id:'a'.repeat(32),title:'回收项'}]},
  {fakeid:OTHER,nickname:'',count:1,name_known:false,sources:[{kind:'knowledge',count:1}],examples:[]}],
  pending_links:2,failed_links:1,ignored:1,subscribed:0});

test('page never scans or enables anything on load',async()=>{const f=await fixture();
 assert(f.calls.every(c=>c.url!=='/api/intel/recommendations/scan'));
 assert(f.calls.every(c=>!(c.url||'').startsWith('/api/schedules/k')));f.w.close();});

test('scan is an explicit click and reports the shape breakdown honestly',async()=>{const f=await fixture({listing:listing()});
 f.d.querySelector('#itl-scan').click();await tick();await tick();await tick();
 const post=f.calls.find(c=>c.url==='/api/intel/recommendations/scan');
 assert(post&&post.opts.method==='POST'&&post.opts.headers['X-Article-Studio']==='1');
 const text=f.d.querySelector('#itl-status').textContent;
 assert.match(text,/扫到链接 120 条/);assert.match(text,/12 条不是文章页已跳过/);
 assert.match(text,/100 条直接从链接认出公众号/);assert.match(text,/成功 7 条/);
 assert.match(text,/失败 1 条/);f.w.close();});

test('unknown account name is shown as unknown, never faked from the fakeid',async()=>{const f=await fixture({listing:listing()});
 const rows=f.d.querySelectorAll('.itl-reco-item');
 assert.equal(rows.length,2);
 assert.match(rows[0].querySelector('b').textContent,/占位公众号/);
 assert.match(rows[1].querySelector('b').textContent,/号名未取到/);
 assert.equal(rows[1].querySelector('b').textContent.includes(OTHER),false);f.w.close();});

test('recommendation shows count and where it came from',async()=>{const f=await fixture({listing:listing()});
 const meta=f.d.querySelectorAll('.itl-reco-item')[0].querySelector('.itl-reco-meta').textContent;
 assert.match(meta,/出现 3 次/);assert.match(meta,/回收项/);assert.match(meta,/知识库/);f.w.close();});

test('source breakdown is aggregated, never a repeated list of the same kind',async()=>{const f=await fixture({listing:listing()});
 const meta=f.d.querySelectorAll('.itl-reco-item')[0].querySelector('.itl-reco-meta').textContent;
 assert(!/知识库、知识库/.test(meta),'同一来源不应重复罗列');
 assert.match(meta,/知识库 2 条/);f.w.close();});

test('subscribe and ignore are separate explicit clicks',async()=>{const f=await fixture({listing:listing()});
 f.d.querySelector('[data-reco=subscribe]').click();await tick();await tick();await tick();
 let post=f.calls.find(c=>c.url.endsWith('/recommendations/subscribe'));
 assert.equal(post.body.fakeid,BIZ);
 assert.match(f.d.querySelector('#itl-status').textContent,/不会再出现在推荐里/);
 post=f.calls.find(c=>c.url.endsWith('/recommendations/ignore'));
 assert(!post,'搜索/订阅都不应自动忽略');
 f.w.close();});

test('ignore sends its own verb and says data is kept',async()=>{const f=await fixture({listing:listing()});
 f.d.querySelectorAll('[data-reco=ignore]')[1].click();await tick();await tick();await tick();
 const post=f.calls.find(c=>c.url.endsWith('/recommendations/ignore'));
 assert.equal(post.body.fakeid,OTHER);
 assert.match(f.d.querySelector('#itl-status').textContent,/数据仍在/);f.w.close();});

test('empty recommendation list invites a scan instead of blaming the source',async()=>{const f=await fixture();
 assert.match(f.d.querySelector('#itl-reco').textContent,/暂无推荐/);
 assert.equal(f.d.querySelector('#itl-reco').textContent.includes('失败'),false);f.w.close();});

// ── 二期 B：定时订阅 ──────────────────────────────────────────────────────
test('schedule starts disabled and offers an explicit enable',async()=>{const f=await fixture({task:true,taskEnabled:false});
 assert.match(f.d.querySelector('#itl-sched-state').textContent,/未开启/);
 assert.match(f.d.querySelector('#itl-sched-state').textContent,/每天 08:00/);
 assert.equal(f.d.querySelector('#itl-sched-toggle').textContent,'开启');f.w.close();});

test('enabling the schedule is an explicit click with the pause/resume verb',async()=>{const f=await fixture({task:true,taskEnabled:false});
 f.d.querySelector('#itl-sched-toggle').click();await tick();await tick();await tick();
 const post=f.calls.find(c=>c.url==='/api/schedules/'+'k'.repeat(32)+'/resume');
 assert(post&&post.opts.method==='POST');f.w.close();});

test('an enabled schedule offers to stop and explains the rate discipline',async()=>{const f=await fixture({task:true,taskEnabled:true});
 assert.match(f.d.querySelector('#itl-sched-state').textContent,/已开启/);
 assert.equal(f.d.querySelector('#itl-sched-toggle').textContent,'停用');
 assert.match(f.d.querySelector('#itl-sched-card').textContent,/连续 3 次冷却 6 小时/);
 assert.match(f.d.querySelector('#itl-sched-card').textContent,/不自动送回收站或知识库/);f.w.close();});

test('missing schedule registration is reported instead of a broken toggle',async()=>{const f=await fixture({task:null});
 assert.match(f.d.querySelector('#itl-sched-state').textContent,/未登记/);
 assert.equal(f.d.querySelector('#itl-sched-toggle').disabled,true);f.w.close();});

// L2：定时任务从「未登记」变成存在后，开关要跟着恢复可用（订阅推荐后会再 loadSchedule）。
test('the schedule toggle becomes usable again once the task shows up',async()=>{
 const options={task:null,listing:{items:[{fakeid:OTHER,nickname:'新发现的号',name_known:true,count:3,sources:[{kind:'intake',count:2}]}],pending_links:1,failed_links:0,ignored:0,subscribed:0}};
 const f=await fixture(options);
 const toggle=f.d.querySelector('#itl-sched-toggle');
 assert.equal(toggle.disabled,true,'任务还没登记时开关禁用');
 assert.match(f.d.querySelector('#itl-sched-state').textContent,/未登记/);
 options.task=true;options.taskEnabled=true;                     // 其他页面把任务建好了
 f.d.querySelector('[data-reco="subscribe"][data-fakeid="'+OTHER+'"]').click();
 for(let i=0;i<6;i++)await tick();
 assert.equal(toggle.disabled,false,'读到合法任务后开关必须恢复可用');
 assert.equal(toggle.textContent,'停用');
 assert.equal(toggle.dataset.action,'pause');
 assert.match(f.d.querySelector('#itl-sched-state').textContent,/已开启/);
 assert.match(f.d.querySelector('#itl-sched-state').textContent,/每天 08:00/);
 assert(f.calls.every(c=>!(c.url.startsWith('/api/schedules/')&&c.opts.method==='POST')),'恢复开关本身不能顺手改任务状态');
 f.w.close();});

test('a schedule task that disappears again disables the toggle again',async()=>{
 const options={task:true,taskEnabled:true,listing:{items:[{fakeid:OTHER,nickname:'新发现的号',name_known:true,count:2,sources:[{kind:'intake',count:1}]}],pending_links:1,failed_links:0,ignored:0,subscribed:0}};
 const f=await fixture(options);
 const toggle=f.d.querySelector('#itl-sched-toggle');
 assert.equal(toggle.disabled,false);
 options.task=null;                                                     // 任务又被删了
 f.d.querySelector('[data-reco="subscribe"][data-fakeid="'+OTHER+'"]').click();
 for(let i=0;i<6;i++)await tick();
 assert.equal(toggle.disabled,true,'任务没了要重新禁用，不能留着能点的开关');
 assert.match(f.d.querySelector('#itl-sched-state').textContent,/未登记/);
 f.w.close();});

test('the toggle stays disabled while a pause/resume request is in flight',async()=>{
 let release;const options={task:true,taskEnabled:true,pauseGate:new Promise(resolve=>{release=resolve;})};
 const f=await fixture(options);
 const toggle=f.d.querySelector('#itl-sched-toggle');
 assert.equal(toggle.disabled,false);
 toggle.click();await tick();await tick();
 options.taskEnabled=false;                                             // 服务端已停用，下一次 GET 返回 enabled:false
 assert.equal(toggle.disabled,true,'请求进行中要禁用（由 busy 单独管，不是靠缺任务）');
 assert.match(toggle.textContent,/处理中/);
 toggle.click();await tick();
 assert.equal(f.calls.filter(c=>c.url==='/api/schedules/'+'k'.repeat(32)+'/pause').length,1,'连点不能发出第二次操作');
 release();for(let i=0;i<6;i++)await tick();
 assert.equal(toggle.disabled,false,'请求结束后恢复可用');
 assert.equal(toggle.textContent,'开启');
 assert.equal(toggle.dataset.action,'resume');
 f.w.close();});

// ── round4 B：抓取进行中禁用按钮，不再连点出并发任务 ──────────────────────
test('both run buttons are enabled when nothing is running',async()=>{const f=await fixture({busy:false});
 const latest=f.d.querySelector('#itl-run-latest'),history=f.d.querySelector('#itl-run-history');
 assert.equal(latest.disabled,false);assert.equal(history.disabled,false);
 assert.match(latest.textContent,/抓最新/);f.w.close();});

test('a running fetch disables both buttons and says so',async()=>{const f=await fixture({busy:true});
 const latest=f.d.querySelector('#itl-run-latest'),history=f.d.querySelector('#itl-run-history');
 assert.equal(latest.disabled,true);assert.equal(history.disabled,true);
 assert.match(latest.textContent,/抓取进行中/);assert.match(history.textContent,/抓取进行中/);f.w.close();});

test('a disabled run button cannot be clicked into a second job',async()=>{const f=await fixture({busy:true});
 f.d.querySelector('#itl-run-latest').click();await tick();await tick();
 assert.equal(f.calls.filter(c=>c.url==='/api/intel/run').length,0);f.w.close();});

test('server says reused and the page explains no new job was made',async()=>{const f=await fixture({reused:true});
 f.d.querySelector('#itl-run-latest').click();await tick();await tick();
 assert.match(f.d.querySelector('#itl-status').textContent,/已有抓取任务在进行中/);
 assert.match(f.d.querySelector('#itl-status').textContent,/这次没有新建/);f.w.close();});

test('a reused job still polls the task it was given',async()=>{const f=await fixture({reused:true,jobStatus:'done',jobReason:'列表 20 篇'});
 f.d.querySelector('#itl-run-latest').click();await tick();await f.w.fireTimers();
 assert(f.calls.some(c=>c.url.startsWith('/api/intel/jobs/')));f.w.close();});

// ── 三期：三路取文章 ──────────────────────────────────────────────────────
test('article rows are labelled with the path they came from',async()=>{const f=await fixture();
 const src=f.d.querySelector('.itl-src');
 assert.ok(src,'应标明来源');assert.equal(src.dataset.source,'album');
 assert.match(src.textContent,/合集/);f.w.close();});

test('sogou and backend labels are distinct',async()=>{const f=await fixture({pathSource:'sogou'});
 assert.match(f.d.querySelector('.itl-src').textContent,/搜狗/);
 assert.equal(f.d.querySelector('.itl-src').dataset.source,'sogou');f.w.close();});

test('an article without a real link never renders a fake href',async()=>{const f=await fixture({noLink:true});
 const row=f.d.querySelector('.itl-article');
 assert.equal(row.querySelector('a[href]'),null,'没有真实链接就不该渲染 href');
 assert.match(row.textContent,/没有可用链接/);
 assert.doesNotMatch(row.textContent,/链接被反爬拦住/);f.w.close();});

test('sogou title-only rows keep the anti-crawl wording and no href',async()=>{
 const f=await fixture({noLink:true,pathSource:'sogou',missingReason:'sogou_title_only'});
 const row=f.d.querySelector('.itl-article');
 assert.equal(row.querySelector('a[href]'),null);
 assert.match(row.querySelector('.itl-nolink').textContent,/搜狗只拿到标题，链接被反爬拦住/);f.w.close();});

test('an invalid album link is described as hidden, not as a sogou block',async()=>{
 const f=await fixture({noLink:true,missingReason:'invalid_link'});
 const row=f.d.querySelector('.itl-article');
 assert.equal(row.querySelector('a[href]'),null);
 assert.match(row.querySelector('.itl-nolink').textContent,/链接不是公众号文章地址，已隐藏/);
 assert.doesNotMatch(row.textContent,/反爬/);f.w.close();});

test('a missing-link reason suppresses href even when a weixin url is still present',async()=>{
 const f=await fixture({missingReason:'sogou_title_only'});
 assert.equal(f.d.querySelector('.itl-article a[href]'),null);
 assert.match(f.d.querySelector('.itl-nolink').textContent,/链接被反爬拦住/);f.w.close();});

test('only an https mp.weixin.qq.com link is rendered as href',async()=>{
 const good=await fixture();
 assert.equal(good.d.querySelector('.itl-article a[href]').getAttribute('href'),'https://mp.weixin.qq.com/s/AAAAAAAAAAAA');
 good.w.close();
 for(const bad of ['javascript:alert(1)','https://evil.example/s/AAAAAAAAAAAA','http://mp.weixin.qq.com/s/AAAAAAAAAAAA','ftp://mp.weixin.qq.com/s/AAAAAAAAAAAA','https://mp.weixin.qq.com.evil.example/s/AAAAAAAAAAAA']){
  const f=await fixture({badLink:bad});
  assert.equal(f.d.querySelector('.itl-article a[href]'),null,bad);
  f.w.close();
 }});

test('backend third path is off by default and togglable',async()=>{const f=await fixture();
 const sogou=f.d.querySelector('#itl-use-sogou'),backend=f.d.querySelector('#itl-use-backend');
 assert.equal(sogou.checked,true);assert.equal(backend.checked,false);
 f.d.querySelector('#itl-run-latest').click();await tick();await tick();
 let post=f.calls.find(c=>c.url==='/api/intel/run');
 assert.equal(post.body.backend,false);assert.equal(post.body.sogou,true);
 await f.w.fireTimers();
 backend.checked=true;sogou.checked=false;
 f.d.querySelector('#itl-run-history').click();await tick();await tick();
 post=f.calls.filter(c=>c.url==='/api/intel/run').at(-1);
 assert.equal(post.body.backend,true);assert.equal(post.body.sogou,false);f.w.close();});

test('capability limits are stated on the page',async()=>{const f=await fixture();
 const warn=f.d.querySelector('.itl-warn').textContent;
 assert.match(warn,/不在任何合集里的老文章可能缺失/);
 assert.match(warn,/搜狗的跳转链接实测必被反爬拦住/);f.w.close();});

test('missing album discovery is explained rather than left blank',async()=>{const f=await fixture({albums:[]});
 assert.match(f.d.querySelector('#itl-album-note').textContent,/尚未发现合集/);f.w.close();});

test('known albums are reported',async()=>{const f=await fixture({albums:[{album_id:'1234567890123456',title:'占位合集'}]});
 assert.match(f.d.querySelector('#itl-album-note').textContent,/已发现 1 个合集/);f.w.close();});


test('accepted run immediately locks both buttons until its poll completes',async()=>{
 const f=await fixture(); f.d.querySelector('#itl-run-history').click(); await tick();
 assert.equal(f.d.querySelector('#itl-run-latest').disabled,true);
 assert.equal(f.d.querySelector('#itl-run-history').disabled,true);
 assert.match(f.d.querySelector('#itl-run-history').textContent,/抓取进行中/);
 f.d.querySelector('#itl-run-latest').click();await tick();
 assert.equal(f.calls.filter(c=>c.url==='/api/intel/run').length,1);
 await f.w.fireTimers();
 assert.equal(f.d.querySelector('#itl-run-latest').disabled,false); f.w.close();});

test('refresh with a running job resumes read-only polling',async()=>{
 const f=await fixture({busy:true,jobStatus:'running'}); await f.w.fireTimers();
 assert(f.calls.some(c=>c.url.startsWith('/api/intel/jobs/')));
 assert.equal(f.d.querySelector('#itl-run-history').disabled,true);
 assert.equal(f.calls.filter(c=>c.url==='/api/intel/run').length,0); f.w.close();});

test('queued job keeps buttons locked',async()=>{
 const f=await fixture({jobStatus:'queued'});f.d.querySelector('#itl-run-latest').click();await tick();await f.w.fireTimers();
 assert.equal(f.d.querySelector('#itl-run-history').disabled,true);f.w.close();});

test('manual album link reuses identify and does not run a fetch job',async()=>{
 const f=await fixture({identify:{fakeid:BIZ,albums_found:1}});
 f.d.querySelector('#itl-album-link').value='https://mp.weixin.qq.com/mp/appmsgalbum?__biz='+encodeURIComponent(BIZ)+'&album_id=3501527933040967680';
 f.d.querySelector('#itl-discover-album').click();await tick();await tick();
 assert(f.calls.some(c=>c.url==='/api/intel/identify'));
 assert.equal(f.calls.filter(c=>c.url==='/api/intel/run').length,0);
 assert.match(f.d.querySelector('#itl-status').textContent,/已发现并记录合集/);f.w.close();});

test('wrong account and article lacking checksum are rejected before request',async()=>{
 const f=await fixture();
 for(const url of ['https://mp.weixin.qq.com/s?__biz='+encodeURIComponent(OTHER)+'&mid=1&chksm=x','https://mp.weixin.qq.com/s?__biz='+encodeURIComponent(BIZ)+'&mid=1']){
 f.d.querySelector('#itl-album-link').value=url;f.d.querySelector('#itl-discover-album').click();await tick();}
 assert.equal(f.calls.filter(c=>c.url==='/api/intel/identify').length,0);f.w.close();});
