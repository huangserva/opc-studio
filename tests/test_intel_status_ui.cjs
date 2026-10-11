// fixes-intel-status M1/L1 的页面验收：初次加载就显示每个订阅号最近一次结果
// （时间、中文状态、三条路线、冷却、下一步），并显示发现合集的留痕。
// 只用 jsdom 与假 fetch：不发任何抓取请求，不访问真实接口。
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs');
const {JSDOM}=require('jsdom');const tick=()=>new Promise(r=>setImmediate(r));
const BIZ='MzB5NTkwMDAwMDE=',OTHER='MzB5NTkwMDAwMDI=';
const RESULT={job_id:'j'.repeat(32),mode:'scheduled',mode_label:'定时（每日抓最新）',status:'rate_limited',
  status_label:'限流冷却',headline:'公众号后台接口被限流（错误码 200013），这一轮已停止、没有重试',
  reason:'命中公众号后台限流（200013）',
  detail:'入库 0 篇（没有取到列表）。合集：本地没有该号的完整文章链接，无法发现合集；搜狗：9 条结果里 1 条确属本号，搜狗要求验证码（已进入 6 小时冷却）；后台：命中公众号后台限流（200013）（未重试）',
  at:'2026-10-10T00:00:00+00:00',at_label:'2026-10-10 08:00',source:'scheduled',
  cooldown_until:1789000000,cooldown_label:'后台接口冷却至 2026-10-10 20:00；搜狗冷却至 2026-10-10 14:00',
  routes:[{path:'album',label:'合集',text:'本地没有该号的完整文章链接，无法发现合集'},
    {path:'sogou',label:'搜狗',text:'9 条结果里 1 条确属本号，搜狗要求验证码（已进入 6 小时冷却）'},
    {path:'backend',label:'后台',text:'命中公众号后台限流（200013）（未重试）'}],
  suggestion:'后台接口被限流，等到 2026-10-10 20:00 之前不用再点抓取；发一篇该号文章的完整链接到「补充该号合集」。',
  found:0,added:0,active:false};
const SEARCH={attempted:true,kind:'article',albums_found:0,albums_known:0,at:'2026-10-10T00:30:00+00:00',
  at_label:'2026-10-10 08:30',has_chksm:true,
  note:'上次读的那篇文章里没有合集；这个号目前还没有任何合集记录（不等于这个号没有合集）。'};

async function fixture(options={}){
 const dom=new JSDOM(fs.readFileSync('public/intel.html','utf8'),{url:'http://127.0.0.1:8766/intel.html',runScripts:'outside-only'});
 const w=dom.window,d=w.document,calls=[],timers=[];
 w.open=()=>({document:{write(){},close(){}}});
 w.setInterval=(fn,ms)=>{timers.push(fn);return timers.length};
 w.clearInterval=()=>{timers.length=0};
 w.fireTimers=async()=>{const pending=timers.splice(0);for(const fn of pending)fn();await tick();await tick()};
 w.fetch=async(url,opts={})=>{const body=opts.body&&JSON.parse(opts.body);calls.push({url,body,opts});let data={};
  if(url==='/api/intel/status')data={schema:'intel-wechat-mp/v1',mode:'wechat_mp',cooldown_until:options.cooldown_until||0,
   cooldown_label:options.cooldown_label||'',credentials:'ready',detail:'后台凭证可用',rate_strikes:options.rate_strikes||0,
   subscriptions:options.subs===0?0:1,articles:0,awaiting:0,busy:!!options.busy,
   active_job:options.busy?{id:'j'.repeat(32),name:'数字生命卡兹克',status:'running'}:null,
   sogou_cooldown_until:options.sogouCooldown||1789000000,sogou_cooldown_label:options.sogouLabel||'2026-10-10 14:00'};
  else if(url==='/api/intel/subscriptions')data={subscriptions:options.subs===0?[]:[
    {fakeid:BIZ,name:'数字生命卡兹克',article_count:0,last_result:options.result===null?null:Object.assign({},RESULT,options.result||{})},
    {fakeid:OTHER,name:'另一个号',article_count:0,last_result:null}]};
  if(options.oldBackend){
    // 后端还没重启时的旧接口：没有 last_result / album_search / 搜狗冷却字段。
    if(url==='/api/intel/subscriptions')data={subscriptions:[{fakeid:BIZ,name:'旧后端',article_count:0}]};
    else if(url.startsWith('/api/intel/articles?'))data={articles:[],total:0,albums:[]};
    else if(url==='/api/intel/status')data={credentials:'ready',detail:'后台凭证可用',subscriptions:1,articles:0,awaiting:0,busy:false,active_job:null};
  }
  else if(url.startsWith('/api/intel/articles?'))data={articles:[],total:0,albums:options.albums||[],album_search:options.search===null?null:Object.assign({},SEARCH,options.search||{})};
  else if(url==='/api/intel/recommendations')data={items:[],pending_links:0,failed_links:0,ignored:0,subscribed:0};
  else if(url==='/api/schedules')data={tasks:[],timezone:'Asia/Shanghai'};
  else if(url==='/api/intel/jobs/'+(options.jobId||'j'.repeat(32)))data={id:options.jobId||'j'.repeat(32),
    status:options.jobStatus||'partial',status_label:'部分完成',reason:options.jobReason||'截断的原因',
    detail:options.jobDetail||'合集：1 个合集，取到 0 篇；搜狗：已关闭；后台：命中公众号后台限流（200013）（未重试）',
    found:0,added:0,bodies:0,pages:0};
  else if(url==='/api/intel/run')data={id:options.jobId||'j'.repeat(32),status:'running'};
  else if(url==='/api/intel/identify')data=options.identify||{fakeid:BIZ,nickname:'数字生命卡兹克',title:'占位文章',
    chars:1200,published_at:1700000000,url:'https://mp.weixin.qq.com/s?__biz='+BIZ+'&mid=1&chksm=x',albums_found:0,album_search:SEARCH};
  return {ok:true,json:async()=>data};};
 w.eval(fs.readFileSync('public/intel.js','utf8'));await tick();await tick();await tick();return {w,d,calls};}

test('first load shows every subscription account last result without starting anything',async()=>{
 const f=await fixture();
 assert(f.calls.every(c=>c.url!=='/api/intel/run'),'初次加载不能发抓取请求');
 const box=f.d.querySelector('#itl-results');
 assert(box,'页面要有常驻的最近结果区');
 assert.match(box.textContent,/数字生命卡兹克/);
 assert.match(box.textContent,/限流冷却/);
 assert.match(box.textContent,/2026-10-10 08:00/);
 assert.match(box.textContent,/定时（每日抓最新）/);
 assert.equal(box.querySelectorAll('.itl-result-routes li').length,3,'三条路线都要列出来');
 assert.deepEqual([...box.querySelectorAll('.itl-result-routes li')].map(n=>n.querySelector('b').textContent),['合集','搜狗','后台']);
 assert.match(box.textContent,/验证码/);
 assert.match(box.textContent,/冷却：后台接口冷却至 2026-10-10 20:00；搜狗冷却至 2026-10-10 14:00/);
 assert.match(box.textContent,/下一步：/);
 assert.match(box.textContent,/补充该号合集/);
 // 没抓过的号要如实说，不编造结果。
 assert.match(box.textContent,/另一个号/);
 assert.match(box.textContent,/还没抓过/);
 f.w.close();});

test('the plain-language headline leads and the raw code stays readable in the full receipt',async()=>{
 const f=await fixture();
 const card=f.d.querySelector('#itl-results .itl-result[data-status=rate_limited]');
 assert.match(card.querySelector('.itl-result-head').textContent,/错误码 200013/);
 assert.equal(card.querySelector('.itl-result-head').textContent.startsWith('200013'),false,'不能拿错误码当主文案');
 const details=card.querySelector('.itl-result-detail');
 assert(details,'完整回执要能展开');
 assert.match(details.textContent,/入库 0 篇/);
 assert.match(details.textContent,/9 条结果里 1 条确属本号/,'要用完整 detail，不只是截断的 reason');
 f.w.close();});

test('result text is escaped and never becomes markup',async()=>{
 const f=await fixture({result:{suggestion:'下一步：<img src=x onerror=alert(1)>',routes:[{path:'album',label:'合集',text:'<script>alert(1)</script>'}]}});
 const box=f.d.querySelector('#itl-results');
 assert.equal(box.querySelector('img'),null);assert.equal(box.querySelector('script'),null);
 assert.match(box.textContent,/onerror/);
 f.w.close();});

test('an account with no attempt says so instead of blaming the account',async()=>{
 const f=await fixture({result:null,search:{attempted:false,note:'还没有用文章链接反查过合集。补一篇该号的完整文章链接，或直接粘贴合集链接。'}});
 const box=f.d.querySelector('#itl-results');
 assert.match(box.textContent,/还没抓过/);
 assert.match(box.textContent,/这个号还没有抓取记录/);
 assert.match(box.textContent,/开始第一次/);
 assert.match(f.d.querySelector('#itl-album-note').textContent,/还没有用文章链接反查过合集/);
 f.w.close();});

test('a backend that does not send the field is not reported as never collected',async()=>{
 // 后端未重启时接口里根本没有 last_result 字段：不能当成「这个号还没抓过」。
 const f=await fixture({oldBackend:true});
 const box=f.d.querySelector('#itl-results');
 assert.match(box.textContent,/旧后端/);
 assert.equal(/还没抓过/.test(box.textContent),false,'缺字段不等于没抓过');
 assert.equal(/开始第一次/.test(box.textContent),false,'不能提示用户去开始第一次抓取');
 assert.match(box.textContent,/结果待更新/);
 assert.match(box.textContent,/最近采集结果要等服务更新后才能显示/);
 assert.match(box.textContent,/不代表这个号没有抓取记录/);
 const card=box.querySelector('.itl-result[data-status=unavailable]');
 assert(card,'缺字段要单独一种卡片，不复用「还没抓过」那一张');
 // 字段存在但为 null（新后端确认没有任务行）才说「还没抓过」。
 const g=await fixture({result:null});
 assert.match(g.d.querySelector('#itl-results').textContent,/还没抓过/);
 assert(g.d.querySelector('#itl-results .itl-result[data-status=none]'));
 assert.equal(g.d.querySelector('#itl-results .itl-result[data-status=unavailable]'),null);
 f.w.close();g.w.close();});

test('album discovery trace survives the reload and distinguishes article from account',async()=>{
 const f=await fixture();
 assert.match(f.d.querySelector('#itl-album-note').textContent,/上次读的那篇文章里没有合集/);
 assert.match(f.d.querySelector('#itl-album-note').textContent,/不等于这个号没有合集/);
 // 发现合集后仍然不自动抓取。
 f.d.querySelector('#itl-album-link').value='https://mp.weixin.qq.com/s?__biz='+BIZ+'&mid=1&chksm=x';
 f.d.querySelector('#itl-discover-album').click();await tick();await tick();await tick();
 assert.match(f.d.querySelector('#itl-status').textContent,/这篇文章尚未发现合集/);
 assert.match(f.d.querySelector('#itl-status').textContent,/那篇文章里没有合集/);
 assert(f.calls.every(c=>c.url!=='/api/intel/run'));
 f.w.close();});

test('sogou cooldown is shown separately from the backend credential status',async()=>{
 const f=await fixture();
 assert.match(f.d.querySelector('#itl-quota').textContent,/后台凭证可用/);
 assert.match(f.d.querySelector('#itl-quota').textContent,/搜狗冷却至 2026-10-10 14:00/);
 f.w.close();});

test('a finished run reports the full detail, not the truncated reason',async()=>{
 const f=await fixture({jobStatus:'rate_limited',jobReason:'命中限流（200013）',
   jobDetail:'合集：本地没有该号的完整文章链接，无法发现合集；后台：命中公众号后台限流（200013）（未重试）'});
 f.d.querySelector('#itl-run-latest').click();await tick();await f.w.fireTimers();
 const status=f.d.querySelector('#itl-status').textContent;
 assert.match(status,/限流冷却/);
 assert.match(status,/合集：本地没有该号的完整文章链接/);
 f.w.close();});

test('a running job is shown as busy and the buttons stay disabled',async()=>{
 const f=await fixture({busy:true});
 assert.equal(f.d.querySelector('#itl-run-latest').disabled,true);
 assert.equal(f.d.querySelector('#itl-run-history').disabled,true);
 f.w.close();});

test('an older backend without the new fields still renders instead of crashing',async()=>{
 // 后端未重启时，接口还没有 last_result / album_search / 搜狗冷却字段；页面不能报错。
 const f=await fixture({oldBackend:true});
 assert.match(f.d.querySelector('#itl-results').textContent,/旧后端/);
 assert.match(f.d.querySelector('#itl-album-note').textContent,/尚未发现合集/);
 assert.match(f.d.querySelector('#itl-quota').textContent,/后台凭证可用/);
 f.w.close();});

test('the interrupted wait time is stated on the page instead of left to guess',async()=>{
 // L1：进程被杀后残留的 running 要等 started_at 起 3600 秒才解除。规则不改，文字如实说。
 const f=await fixture();
 const note=f.d.querySelector('#itl-results [data-note=stale-wait]');
 assert(note,'最近结果区要写明残留任务的等待时间');
 assert.match(note.textContent,/开始时间起满 1 小时/);
 assert.match(note.textContent,/已中断/);
 assert.match(note.textContent,/不是页面卡住/);
 f.w.close();});
