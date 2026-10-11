/* 作品复盘区块（jsdom）：只在点击后读取，指标随时间、同类对比、复盘笔记与回库。

   The review must never fetch on page load, never invent a figure, and never write
   to the knowledge base without a click. Synthetic data only.

   Run: node --test tests/test_feedback_review_ui.cjs
*/
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs');
const {JSDOM}=require('jsdom');
const tick=()=>new Promise(resolve=>setImmediate(resolve));
async function flush(){for(let i=0;i<6;i++)await tick();}
const copy=value=>JSON.parse(JSON.stringify(value));
const slug='synthetic-post',peer='synthetic-peer',doc='d'.repeat(32);
const review={slug,title:'合成复盘作品 <img>',type:'post',work_url:'/?article=synthetic-post&version=v0001',platforms:['x'],
  snapshots:2,notice:'复盘只使用用户导入的原始统计快照，未向平台校验。',series_notice:'区间统计与累计统计分开展示。',
  deliveries:[{delivery_id:'a'.repeat(32),platform:'x',version:'v0001',url:'https://x.com/demo/status/123',account:'demo',
               registration_at:'2026-10-04T00:00:00Z',work_url:'/?article=synthetic-post&version=v0001',kind:'published_manual'}],
  series:[{delivery_id:'a'.repeat(32),range_kind:'interval',label:'views',points:[
      {collected_at:'2026-10-05T02:00:00+08:00',range_start:'',range_end:'',value:'1,200',number:1200,unit:'',provenance:'manual',snapshot_id:'b'.repeat(32),knowledge_id:null},
      {collected_at:'2026-10-06T02:00:00+08:00',range_start:'',range_end:'',value:'2,500',number:2500,unit:'',provenance:'csv',snapshot_id:'c'.repeat(32),knowledge_id:null,delta:1300},
      {collected_at:'2026-10-07T02:00:00+08:00',range_start:'',range_end:'',value:'2.5K',number:null,unit:'',provenance:'manual',snapshot_id:'e'.repeat(32),knowledge_id:null,delta:null}]}],
  latest:{views:{value:'2.5K',number:null,unit:'',collected_at:'2026-10-07T02:00:00+08:00',range_kind:'interval',delivery_id:'a'.repeat(32)}},
  peers:[{platform:'x',type:'post',notice:'同类作品指同一平台、同一作品类型且已导入统计的其他作品；样本少时只能作参考。',
    works:[{slug:peer,title:'同类合成作品',version:'v0001'}],
    metrics:[{label:'views',value:'2.5K',number:null,unit:'',count:2,median:900,minimum:100,maximum:1500,
              peers:[{slug:peer,title:'同类合成作品',value:'900'}]}]}],
  observations:[],note:{notes:'',notes_updated_at:'',documents:[]},overviews:[],metric_fields:['Views','Likes']};
function works(){return {works:[{slug,title:'合成复盘作品',type:'post',platforms:['x'],deliveries:1,snapshots:2,work_url:review.work_url,note_updated_at:''},
  {slug:peer,title:'同类合成作品',type:'post',platforms:['x'],deliveries:1,snapshots:1,work_url:'/?article=x&version=v0001',note_updated_at:''}],
  notice:review.notice,metric_fields:{x:['Views','Likes','Bookmarks']}};}
async function fixture({reviewData=review,onRequest,url='http://127.0.0.1:8766/feedback.html',worksData=works()}={}){
  const dom=new JSDOM(fs.readFileSync('public/feedback.html','utf8'),{url,runScripts:'outside-only'}),w=dom.window,d=w.document,calls=[],model={works:copy(worksData),review:copy(reviewData)};
  w.HTMLDialogElement.prototype.showModal=function(){this.open=true;};w.HTMLDialogElement.prototype.close=function(){this.open=false;};
  w.fetch=async(raw,options={})=>{
    const call={url:raw,method:options.method||'GET',headers:options.headers||{},body:options.body?JSON.parse(options.body):undefined};calls.push(call);
    let result=onRequest?await onRequest(call,model):undefined;
    if(result===undefined){
      if(raw==='/api/feedback')result={bindings:[],capabilities:{metric_fields:{x:['Views','Likes','Bookmarks']}},unavailable:0};
      else if(raw==='/api/feedback/observations')result={observations:[],total:0};
      else if(raw.startsWith('/api/feedback/snapshots?'))result={snapshots:[],total:0};
      else if(raw==='/api/feedback/reviews')result=model.works;
      else if(raw.startsWith('/api/feedback/review/'+slug))result=model.review;
      else if(raw.startsWith('/api/feedback/review/'))result={slug:raw.split('/')[4],title:'其他作品',type:'post',work_url:'',platforms:['x'],snapshots:0,notice:'',series_notice:'',deliveries:[],series:[],latest:{},peers:[],observations:[],note:{notes:'',notes_updated_at:'',documents:[]},overviews:[],metric_fields:[]};
      else if(raw==='/api/feedback/overviews')result={overviews:model.review.overviews};
      else throw new Error('Unexpected synthetic endpoint '+raw);
    }
    if(result?.httpError)return {ok:false,status:result.httpError,json:async()=>({error:result.error})};
    return {ok:true,status:200,json:async()=>copy(result)};
  };
  w.eval(fs.readFileSync('public/feedback.js','utf8'));await flush();
  return {w,d,calls,model,open:()=>{d.querySelector('#fb-review-open').click();return flush();},close:()=>w.close()};
}
const pick=f=>{f.d.querySelector('#fb-review-work').value=slug;f.d.querySelector('#fb-review-work').dispatchEvent(new f.w.Event('change'));return flush();};

test('打开复盘前不读取复盘接口，打开后显示指标随时间与差值',async()=>{
  const f=await fixture();
  assert(f.d.querySelector('#fb-review').hidden);
  assert.equal(f.calls.filter(c=>c.url.startsWith('/api/feedback/review')).length,0);
  await f.open();
  assert(!f.d.querySelector('#fb-review').hidden);
  assert.equal(f.d.querySelector('#fb-review-work').options.length,2);
  const body=f.d.querySelector('#fb-review-body');
  assert.match(body.textContent,/合成复盘作品/);
  assert.match(body.textContent,/1,200/);assert.match(body.textContent,/2,500/);assert.match(body.textContent,/2\.5K/);
  assert.match(body.textContent,/\+1300/);assert.match(body.textContent,/原值不可解析/);
  assert.match(body.textContent,/区间统计与累计统计分开展示/);
  assert(f.calls.filter(c=>c.method==='GET').length>=3);assert(!f.calls.some(c=>c.method==='POST'));
  f.close();
});

test('同类对比给出中位数、范围与样本数，并保留不能直接比较的提示',async()=>{
  const f=await fixture();await f.open();
  const card=[...f.d.querySelectorAll('.fb-review-card')].find(node=>/同类对比/.test(node.textContent));
  assert.match(card.textContent,/同类对比 · X · post/);
  assert.match(card.textContent,/900/);assert.match(card.textContent,/100 – 1500/);
  assert.match(card.textContent,/样本少时只能作参考/);
  assert.match(card.textContent,/同类作品：同类合成作品/);
  f.close();
});

test('复盘笔记只在点击后保存，保存不会写入知识库',async()=>{
  let saved='';
  const f=await fixture({onRequest:(call,model)=>{
    if(call.url==='/api/feedback/review/'+slug+'/notes'){saved=call.body.notes;return {slug,notes:saved,notes_updated_at:'2026-10-08T02:00:00+08:00',documents:[],summary:'复盘笔记已保存在本机；尚未回库。'};}
    if(call.url==='/api/feedback/review/'+slug)return {...model.review,note:{notes:saved,notes_updated_at:saved?'2026-10-08T02:00:00+08:00':'',documents:[]}};
  }});await f.open();
  f.d.querySelector('#fb-review-notes').value='合成结论：样本太少，只记录事实。';
  f.d.querySelector('#fb-review-save').click();f.d.querySelector('#fb-review-save').click();await flush();
  const call=f.calls.find(c=>c.url==='/api/feedback/review/'+slug+'/notes');
  assert.equal(f.calls.filter(c=>c.url.endsWith('/notes')).length,1);
  assert.equal(call.method,'POST');assert.equal(call.body.notes,'合成结论：样本太少，只记录事实。');
  assert.equal(call.headers['X-Article-Studio'],'1');
  assert(!f.calls.some(c=>c.url.endsWith('/knowledge')));
  assert.match(f.d.querySelector('#fb-status').textContent,/尚未回库/);
  assert.equal(f.d.querySelector('#fb-review-notes').value,'合成结论：样本太少，只记录事实。');
  f.close();
});

test('存为知识库笔记需要显式点击，回库后给出笔记链接且不重复写入',async()=>{
  let exported=false;
  const f=await fixture({onRequest:(call,model)=>{
    if(call.url==='/api/feedback/review/'+slug+'/knowledge'){exported=true;return {slug,duplicate:false,document_id:doc,hash:'f'.repeat(64),url:'/knowledge.html?document='+doc+'&start=0&end=20&hash='+'f'.repeat(64),summary:'复盘笔记已存为知识库笔记（候选资料）；未自动改写写作铁律或风格库。'};}
    if(call.url==='/api/feedback/review/'+slug)return {...model.review,note:{notes:'合成结论',notes_updated_at:'2026-10-08T02:00:00+08:00',documents:exported?[{document_id:doc,hash:'f'.repeat(64),created_at:'2026-10-08T02:00:00+08:00'}]:[]}};
  }});
  await f.open();
  assert(!f.calls.some(c=>c.url.endsWith('/knowledge')));
  f.d.querySelector('#fb-review-knowledge').click();await flush();
  assert(f.calls.some(c=>c.url==='/api/feedback/review/'+slug+'/knowledge'&&c.method==='POST'));
  assert.match(f.d.querySelector('#fb-status').textContent,/候选资料/);
  const link=[...f.d.querySelectorAll('#fb-review-body a')].find(a=>a.href.includes('/knowledge.html?document='+doc));
  assert(link,'expected a knowledge note link after export');
  assert.match(f.d.querySelector('#fb-review-body').textContent,/已回库 1 份/);
  f.close();
});

test('Buffer 组织级汇总只在点击后读取，并标明不能归因到单个作品',async()=>{
  const overview={id:'e'.repeat(32),scope:'organization',organization_name:'合成组织',window:{start:'2026-10-01',end:'2026-10-07'},
    metrics:[{name:'views',value:4321,unit:'count'}],fetched_at:'2026-10-08T02:00:00+08:00',notice:'Buffer 只提供组织级汇总，不能归因到单个作品。'};
  const f=await fixture({onRequest:(call,model)=>{
    if(call.url==='/api/feedback/overview'){model.review.overviews=[overview];return {slug,scope:'organization',organization_id:'org1',organization_name:'合成组织',window:overview.window,metrics:overview.metrics,fetched_at:overview.fetched_at,notice:overview.notice,platform_verified:false,summary:'Buffer 组织级统计已读取；这是整个组织在该时间窗的汇总，不属于单个作品'};}
  }});
  await f.open();
  assert.match(f.d.querySelector('#fb-review-body').textContent,/尚未读取/);
  f.d.querySelector('#fb-overview-start').value='2026-10-01';f.d.querySelector('#fb-overview-end').value='2026-10-07';
  f.d.querySelector('#fb-overview-fetch').click();await flush();
  const call=f.calls.find(c=>c.url==='/api/feedback/overview');
  assert.equal(call.method,'POST');assert.deepEqual(call.body,{start:'2026-10-01',end:'2026-10-07'});
  assert.match(f.d.querySelector('#fb-review-body').textContent,/4321/);
  assert.match(f.d.querySelector('#fb-review-body').textContent,/不能归因到单个作品/);
  f.close();
});

test('没有可复盘作品时给出空态，不显示假数据',async()=>{
  const f=await fixture({worksData:{works:[],notice:review.notice,metric_fields:{}}});
  await f.open();
  assert.match(f.d.querySelector('#fb-review-body').textContent,/还没有可复盘的已发布作品/);
  assert.equal(f.calls.filter(c=>c.url.startsWith('/api/feedback/review/')).length,0);
  f.close();
});

test('标题、笔记与同类作品名只按文本渲染，不产生可执行节点',async()=>{
  const hostile=copy(review);hostile.title='<img src=x onerror=bad()>';hostile.peers[0].works[0].title='<script>bad()</script>';
  hostile.note={notes:'<iframe src=x></iframe> 结论',notes_updated_at:'2026-10-08T02:00:00+08:00',documents:[]};
  const f=await fixture({reviewData:hostile});await f.open();
  const body=f.d.querySelector('#fb-review-body');
  assert.equal(body.querySelectorAll('img,script,iframe').length,0);
  assert.match(body.textContent,/<img src=x onerror=bad\(\)>/);
  assert.equal(f.d.querySelector('#fb-review-notes').value,'<iframe src=x></iframe> 结论');
  f.close();
});

test('导入表单按平台给出指标字段提示，点一下只填名称不编数值',async()=>{
  const binding={slug,delivery_id:'a'.repeat(32),platform:'x',version:'v0001',title:'合成发布项',url:'https://x.com/demo/status/123',account:'demo',registration_at:'2026-10-04T00:00:00Z',work_url:review.work_url};
  const f=await fixture({onRequest:call=>call.url==='/api/feedback'?{bindings:[binding],capabilities:{metric_fields:{x:['Views','Likes','Bookmarks']}},unavailable:0}:undefined});
  f.d.querySelector('#fb-new').click();await flush();
  const hints=[...f.d.querySelectorAll('#fb-metric-hints [data-metric-hint]')];
  assert.deepEqual(hints.map(button=>button.dataset.metricHint),['Views','Likes','Bookmarks']);
  hints[0].click();
  assert.equal(f.d.querySelector('.fb-metric-name').value,'Views');
  assert.equal(f.d.querySelector('.fb-metric-value').value,'');
  assert.equal(f.d.querySelectorAll('#fb-metric-rows .fb-metric-row').length,1);
  assert.equal(f.calls.filter(c=>c.method==='POST').length,0);
  f.close();
});
