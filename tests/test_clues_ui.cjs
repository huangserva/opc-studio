const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs');
// 每个 fixture 的 window 都登记下来，用例结束后统一关掉。
// clues.js 里有 setInterval(…,10000)：窗口不关，node --test 会等事件循环排空而一直挂着。
// 用例失败时（红阶段）断言前的 f.w.close() 根本走不到，挂的就是这种时候 —— 红必须很快结束。
const openWindows=[];test.afterEach(()=>{while(openWindows.length)openWindows.pop().close();});
const {JSDOM}=require('jsdom');const tick=()=>new Promise(r=>setImmediate(r));
const sourceDoc={id:'a'.repeat(32),hash:'b'.repeat(64),source:'wechat',kind:'wechat',title:'<img src=x>',body:'😀前言\r\n项目延期\r\n尾声'};
const clue={id:'c'.repeat(32),revision:1,seen_revision:0,unread:true,title:'<script>bad</script>',summary:'待核对摘要',relevance:'项目依据',categories:['other'],status:'candidate',priority:'normal',kind:'information',tracking:false,event_id:'',updated_at:'2026-10-05T00:00:00Z',sources:['wechat'],entries:[],entries_next_offset:null};
async function fixture(options={}){const dom=new JSDOM(fs.readFileSync('public/clues.html','utf8'),{url:options.url||'http://127.0.0.1:8766/clues.html',runScripts:'outside-only'}),w=dom.window,d=w.document,calls=[];
w.HTMLDialogElement.prototype.showModal=function(){this.open=true};w.HTMLDialogElement.prototype.close=function(){this.open=false};
w.fetch=async(url,opts={})=>{const body=opts.body&&JSON.parse(opts.body);calls.push({url,body,opts});let data={};
if(url==='/api/clues/wechat/status')data={processed_versions:0,has_pending:true};
else if(url==='/api/schedules')data={tasks:[],models:[{configured:true,profile_id:'local',model:'qwen3.8-flash-next-iq3_s',display_name:'指定模型',endpoint:'https://example.test/v1/chat/completions'}],default_profile_id:'local'};
else if(url==='/api/clues/wechat/run')data={run_id:'f'.repeat(32)};
else if(url==='/api/knowledge')data={sources:[{id:'wechat',name:'本机微信',capture:options.capture}]};
else if(url.startsWith('/api/clues?'))data={items:options.empty?[]:[clue],timeline:options.timeline||[],total:options.empty?0:1,counts:{other:1},categories:{other:'其他'},years:['2026','unknown'],sources:['wechat'],next_offset:null};
else if(url.startsWith('/api/clues/'+clue.id+'?'))data={...clue,...options.detail};
else if(url==='/api/clues')data={id:clue.id};
else if(url.endsWith('/promote'))data={event_id:'d'.repeat(32)};
else if(url.startsWith('/api/knowledge/search'))data={results:[{...sourceDoc,excerpt:'检索预览'}]};
else if(url.startsWith('/api/knowledge/document/'))data=sourceDoc;
else if(url.startsWith('/api/events?'))data={events:[{id:'d'.repeat(32),title:'已有待确认事件',summary:'来源明确'}]};
else if(url==='/api/clues/import-event')data={id:clue.id};
return {ok:true,json:async()=>data};};w.eval(fs.readFileSync('public/clues.js','utf8'));openWindows.push(w);await tick();await tick();return {w,d,calls};}
test('five categories and global search keep other and sources separate',async()=>{const f=await fixture();assert.equal(f.d.querySelectorAll('[data-category]').length,5);assert.match(f.d.querySelector('#cl-categories').textContent,/其他/);f.d.querySelector('[data-category=other]').click();await tick();f.d.querySelector('#cl-reset').click();await tick();const url=f.calls.filter(c=>c.url.startsWith('/api/clues?')).at(-1).url;assert.equal(new URL(url,'http://test').searchParams.get('category'),'');f.w.close()});
test('empty and untrusted data do not imply source failure or inject HTML',async()=>{let f=await fixture();assert.equal(f.d.querySelector('#cl-list script'),null);f.d.querySelector('[data-clue]').click();await tick();assert.equal(f.d.querySelector('#cl-detail script'),null);assert(f.calls.some(c=>c.url.endsWith('/seen')&&c.body.revision===1));f.w.close();f=await fixture({empty:true});assert.match(f.d.querySelector('#cl-list').textContent,/不表示来源中没有资料/);f.w.close()});
test('selection preserves Unicode and CRLF original ranges',async()=>{const f=await fixture();f.d.querySelector('#cl-new').click();f.d.querySelector('#cl-knowledge-query').value='项目';f.d.querySelector('#cl-knowledge-search').click();await tick();f.d.querySelector('[data-document]').click();await tick();const area=f.d.querySelector('#cl-document-body');const start=area.value.indexOf('项目');area.setSelectionRange(start,start+4);f.d.querySelector('#cl-use-selection').click();f.d.querySelector('#cl-title').value='合成线索';f.d.querySelector('#cl-form').dispatchEvent(new f.w.Event('submit',{cancelable:true}));await tick();const payload=f.calls.find(c=>c.url==='/api/clues').body;assert.deepEqual(payload.picks,[{id:sourceDoc.id,hash:sourceDoc.hash,start:5,end:9}]);assert.equal(payload.status,'candidate');assert.equal(payload.categories[0],'other');assert.equal(payload.tracking,false);f.w.close()});
test('reclassification carries optimistic revision and cannot silently mark verified',async()=>{const f=await fixture();f.d.querySelector('[data-clue]').click();await tick();f.d.querySelector('[data-detail=edit]').click();assert.equal(f.d.querySelector('#cl-pick-section').hidden,true);f.d.querySelector('#cl-reason').value='重新归类';f.d.querySelector('#cl-category-fields input[value=technology]').checked=true;f.d.querySelector('#cl-form').dispatchEvent(new f.w.Event('submit',{cancelable:true}));await tick();const c=f.calls.find(c=>c.url==='/api/clues');assert.equal(c.body.revision,1);assert.equal(c.body.status,'candidate');assert(c.body.categories.includes('technology'));assert.equal(c.opts.headers['X-Article-Studio'],'1');f.w.close()});
test('annual unknown time is visibly separate from capture time',async()=>{const f=await fixture({timeline:[{id:'e'.repeat(32),thread_id:clue.id,year:'unknown',month:'',clock:'',created_at:'2026-10-05T00:00:00Z',thread:clue,data:{note:'发生时间不明',clock_label:'发生时间未知'}}]});f.d.querySelector('[data-view=year]').click();await tick();assert.match(f.d.querySelector('#cl-list').textContent,/时间未知/);assert.match(f.d.querySelector('#cl-list').textContent,/采集／记录/);f.w.close()});
test('promote is explicit and never submits actions or calendar writes',async()=>{const f=await fixture();f.d.querySelector('[data-clue]').click();await tick();assert(f.calls.every(c=>!c.url.includes('promote')));f.d.querySelector('[data-detail=promote]').click();await tick();assert(f.calls.some(c=>c.url.endsWith('/promote')&&c.body.revision===1));assert(f.calls.every(c=>!c.url.includes('/actions')));assert.match(f.d.querySelector('#cl-status').textContent,/尚未接受待办/);f.w.close()});
test('knowledge document entry opens a source picker instead of auto importing full text',async()=>{const f=await fixture({url:'http://127.0.0.1:8766/clues.html?document='+sourceDoc.id});assert.equal(f.d.querySelector('#cl-document').open,true);assert(f.calls.every(c=>c.url!=='/api/clues'));assert.equal(f.d.querySelector('#cl-title').value,sourceDoc.title);f.w.close()});

test('wechat analysis starts only on explicit action with selected model and recurrence opt in',async()=>{const f=await fixture();assert(f.calls.every(c=>!c.url.endsWith('/wechat/run')));f.d.querySelector('#cl-analyze').click();await tick();assert.equal(f.d.querySelector('#cl-analysis-model').value,'local');assert.equal(f.d.querySelector('#cl-analysis-hourly').checked,false);f.d.querySelector('#cl-analysis-hourly').checked=true;f.d.querySelector('#cl-analysis-run').click();await tick();const c=f.calls.find(c=>c.url.endsWith('/wechat/run'));assert.equal(c.body.profile_id,'local');assert.equal(c.body.enabled,true);assert.match(f.d.querySelector('#cl-status').textContent,/后台开始/);f.w.close()});

test('capture failure is prominent and zero clues cannot imply no business opportunities',async()=>{const f=await fixture({capture:{status:'error',detail:'后台访问授权超时'}});const warning=f.d.querySelector('#cl-coverage-warning');assert.equal(warning.hidden,false);assert.match(warning.textContent,/后台访问授权超时/);assert.match(warning.textContent,/不能据此判断今天没有商单/);assert(f.calls.every(c=>!c.url.endsWith('/wechat/run')));f.w.close()});

test('technical tag, time, and importance filters reach the server together',async()=>{
 const f=await fixture();f.d.querySelector('[data-category=technology]').click();await tick();
 assert.equal(f.d.querySelector('#cl-tech-tags').hidden,false);
 f.d.querySelector('[data-tag=inference]').click();await tick();
 for(const [id,value] of [['cl-sort','priority'],['cl-period','7d'],['cl-priority-filter','important']]){
  f.d.querySelector('#'+id).value=value;f.d.querySelector('#'+id).dispatchEvent(new f.w.Event('change'));await tick();
 }
 const p=new URL(f.calls.filter(c=>c.url.startsWith('/api/clues?')).at(-1).url,'http://test').searchParams;
 assert.equal(p.get('tag'),'inference');assert.equal(p.get('sort'),'priority');assert.equal(p.get('period'),'7d');assert.equal(p.get('priority'),'important');
 f.d.querySelector('#cl-reset').click();await tick();assert.equal(f.d.querySelector('#cl-tech-tags').hidden,true);assert.equal(f.d.querySelector('#cl-period').value,'');f.w.close();
});

test('manual technical labels and automatic suggestion choice survive the editor payload',async()=>{
 const f=await fixture({detail:{categories:['technology'],tags:['inference'],tags_source:'manual'}});
 f.d.querySelector('[data-clue]').click();await tick();f.d.querySelector('[data-detail=edit]').click();
 assert.equal(f.d.querySelector('#cl-tag-fields').hidden,false);assert.equal(f.d.querySelector('#cl-tags-auto').checked,false);
 assert.equal(f.d.querySelector('#cl-tags-options input[value=inference]').checked,true);
 f.d.querySelector('#cl-reason').value='确认方向';f.d.querySelector('#cl-form').dispatchEvent(new f.w.Event('submit',{cancelable:true}));await tick();
 assert.deepEqual(f.calls.find(c=>c.url==='/api/clues').body.tags,['inference']);f.w.close();
});

// ── impl-clues-time-filter：醒目的时间 / 排序按钮组 ─────────────────────────
const chipParams=f=>{const p=new URL(f.calls.filter(c=>c.url.startsWith('/api/clues?')).at(-1).url,'http://test').searchParams;return{period:p.get('period'),sort:p.get('sort')};};
const pressed=(f,group)=>[...f.d.querySelectorAll(`#${group} .cl-chip`)].filter(b=>b.getAttribute('aria-pressed')==='true').map(b=>b.dataset.period??b.dataset.sort);

test('time and sort chips render above the list with the full range set',async()=>{
 const f=await fixture();
 const periods=[...f.d.querySelectorAll('#cl-period-chips .cl-chip')].map(b=>b.dataset.period);
 const sorts=[...f.d.querySelectorAll('#cl-sort-chips .cl-chip')].map(b=>b.dataset.sort);
 assert.deepEqual(periods,['','today','yesterday','3d','7d','30d'],'时间范围要齐：全部/今天/昨天/最近三天/最近七天/最近三十天');
 assert.deepEqual(sorts,['updated','source_desc','source_asc','priority']);
 const head=f.d.querySelector('.cl-list-head'),bar=f.d.querySelector('.cl-range-bar');
 assert(bar.compareDocumentPosition(head)&f.w.Node.DOCUMENT_POSITION_FOLLOWING,'按钮组必须在列表标题上方');
 assert.match(f.d.querySelector('#cl-period-chips').getAttribute('aria-label'),/原文时间/);
 f.w.close();
});

test('the old small dropdowns are no longer shown',async()=>{
 const f=await fixture();
 assert.equal(f.d.querySelector('#cl-sort').closest('label').hidden,true,'旧排序下拉不该再露出来');
 assert.equal(f.d.querySelector('#cl-period').closest('label').hidden,true,'旧时间下拉不该再露出来');
 assert.equal(f.d.querySelector('#cl-priority-filter').closest('label').hidden,false,'重要程度不在本次范围里，保持原样');
 // 光有 hidden 属性不算：clues.css 里 .cl-filter-row label{display:flex} 是作者样式，
 // 会盖掉浏览器默认的 [hidden]{display:none}。真正让它消失的是下面这条 !important。
 // jsdom 不加载外部样式表，测不到渲染，所以把这条依赖写死在测试里。
 assert.match(fs.readFileSync('public/clues.css','utf8'),/\[hidden\]\{display:none!important\}/,'没有这条 !important，旧下拉在浏览器里仍然会露出来');
 f.w.close();
});

test('clicking a time chip requests it and moves the highlight',async()=>{
 const f=await fixture();
 for(const value of ['today','yesterday','3d','7d','30d']){
  f.d.querySelector(`#cl-period-chips [data-period="${value}"]`).click();await tick();
  assert.equal(chipParams(f).period,value);
  assert.deepEqual(pressed(f,'cl-period-chips'),[value],`选中 ${value} 时只应它高亮`);
 }
 f.d.querySelector('#cl-period-chips [data-period=""]').click();await tick();
 assert.equal(chipParams(f).period,'');
 assert.deepEqual(pressed(f,'cl-period-chips'),['']);
 f.w.close();
});

test('clicking a sort chip requests it and moves the highlight',async()=>{
 const f=await fixture();
 for(const value of ['source_desc','source_asc','priority','updated']){
  f.d.querySelector(`#cl-sort-chips [data-sort="${value}"]`).click();await tick();
  assert.equal(chipParams(f).sort,value);
  assert.deepEqual(pressed(f,'cl-sort-chips'),[value]);
 }
 f.w.close();
});

test('picking a time range explains that unknown source time is excluded',async()=>{
 const f=await fixture();
 const note=f.d.querySelector('#cl-period-note');
 assert.equal(note.hidden,true,'没选范围时不该显示这句口径');
 f.d.querySelector('#cl-period-chips [data-period="yesterday"]').click();await tick();
 assert.equal(note.hidden,false);
 assert.match(note.textContent,/按原文时间筛选/);
 assert.match(note.textContent,/原文时间未知的线索不显示/);
 assert.ok(f.d.querySelector('#cl-count').compareDocumentPosition(note)&f.w.Node.DOCUMENT_POSITION_FOLLOWING,'口径说明要挨着列表计数（紧随其下）');
 f.w.close();
});

test('selection survives a reload through the URL',async()=>{
 const f=await fixture();
 f.d.querySelector('#cl-period-chips [data-period="3d"]').click();await tick();
 f.d.querySelector('#cl-sort-chips [data-sort="priority"]').click();await tick();
 const url=f.w.location.href;
 assert.match(url,/period=3d/,'选择要写进 URL，刷新后才还在');
 assert.match(url,/sort=priority/);
 f.w.close();
 const again=await fixture({url});
 assert.equal(again.d.querySelector('#cl-period').value,'3d','刷新后应从 URL 恢复');
 assert.equal(again.d.querySelector('#cl-sort').value,'priority');
 assert.deepEqual(pressed(again,'cl-period-chips'),['3d'],'恢复后高亮也要跟着回来');
 assert.deepEqual(pressed(again,'cl-sort-chips'),['priority']);
 assert.equal(chipParams(again).period,'3d','恢复的选择要真的带进请求');
 assert.equal(chipParams(again).sort,'priority');
 again.w.close();
});

test('a nonsense range in the URL is ignored instead of breaking the page',async()=>{
 const f=await fixture({url:'http://127.0.0.1:8766/clues.html?period=yesterda&sort=banana'});
 assert.equal(f.d.querySelector('#cl-period').value,'');
 assert.equal(f.d.querySelector('#cl-sort').value,'updated');
 assert.deepEqual(pressed(f,'cl-period-chips'),['']);
 f.w.close();
});

test('reset returns to all clues and clears the range from the URL',async()=>{
 const f=await fixture();
 f.d.querySelector('#cl-period-chips [data-period="yesterday"]').click();await tick();
 f.d.querySelector('#cl-sort-chips [data-sort="source_asc"]').click();await tick();
 f.d.querySelector('#cl-reset').click();await tick();
 const p=chipParams(f);
 assert.equal(p.period,'');assert.equal(p.sort,'updated');
 assert.equal(f.d.querySelector('#cl-period-note').hidden,true);
 assert.deepEqual(pressed(f,'cl-period-chips'),['']);
 assert.match(f.w.location.href,/period=&sort=updated|sort=updated/);
 f.w.close();
});

test('year view locks sorting because the list is a timeline',async()=>{
 const f=await fixture();
 f.d.querySelector('#cl-sort-chips [data-sort="priority"]').click();await tick();
 f.d.querySelector('[data-view=year]').click();await tick();
 const chips=[...f.d.querySelectorAll('#cl-sort-chips .cl-chip')];
 assert(chips.every(b=>b.disabled),'年度记事下排序没有意义，按钮应一起锁住');
 f.w.close();
});

test('year view rewrites the URL to the sort it actually applied',async()=>{
 // 后端在年度记事下强制 source_desc；不同步回 URL，刷新就会回到一个页面上看不到的排序。
 const f=await fixture();
 f.d.querySelector('#cl-sort-chips [data-sort="priority"]').click();await tick();
 f.d.querySelector('[data-view=year]').click();await tick();
 assert.equal(chipParams(f).sort,'source_desc','年度记事里请求要带实际生效的排序');
 assert.match(f.w.location.href,/sort=source_desc/,'URL 要跟页面实际显示的一致');
 assert.deepEqual(pressed(f,'cl-sort-chips'),['source_desc']);
 f.w.close();
});

test('narrow-screen rule is written down so the bar scrolls instead of squeezing the search box',async()=>{
 // 注意：这条只是**静态检查规则写没写下来**，不是行为验证 ——
 // jsdom 的 fixture 不加载外部样式表（没有 resources:'usable'），
 // CSS 渲染效果任何自动化都测不到，只能靠真机确认。写这条是为了让
 // 「有人把窄屏规则删了」在测试里可见，不是为了证明窄屏真的能滚。
 const css=fs.readFileSync('public/clues.css','utf8');
 // 少了 min-width:0 的话网格项会被撑到 min-content，按钮组自己就没得滚了 —— 这条曾经真实发生过。
 assert.match(css,/\.cl-board>\*\{min-width:0\}/, '网格项要放开 min-width，否则按钮组撑不出滚动');
 assert.match(css,/\.cl-range-bar\{[^}]*flex-wrap:nowrap[^}]*overflow-x:auto/, '按钮组应单行横向滚动而不是换行挤压');
 assert.match(css,/\.cl-chip\{[^}]*white-space:nowrap/, '按钮本身不该换行');
 assert.match(css,/\.cl-chip\[aria-pressed="true"\]\{[^}]*background:#4362ef/, '选中项要有明显的视觉高亮');
 // 旧下拉靠 hidden 属性藏，但 .cl-filter-row label{display:flex} 是作者样式，会盖掉 UA 的 [hidden]。
 assert.match(css,/\[hidden\]\{display:none!important\}/,'没这条 !important，旧排序/时间下拉会和新按钮组同时露出来');
 assert.doesNotMatch(css,/scroll-snap-align/, '没写 scroll-snap-type，光写 snap-align 是不生效的死规则，不如删掉');
});
