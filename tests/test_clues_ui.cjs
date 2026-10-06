const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs');
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
return {ok:true,json:async()=>data};};w.eval(fs.readFileSync('public/clues.js','utf8'));await tick();await tick();return {w,d,calls};}
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
