const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs');
const {JSDOM}=require('jsdom');
const tick=()=>new Promise(resolve=>setImmediate(resolve));
async function flush(){for(let i=0;i<5;i++)await tick();}
const copy=value=>JSON.parse(JSON.stringify(value));
const ids={doc:'a'.repeat(32),event:'b'.repeat(32),evidence:'c'.repeat(32),action:'d'.repeat(32)};
const hash='e'.repeat(64);
const documentFixture={id:ids.doc,hash,title:'合成测试资料',body:'😀微信经验<script>alert(1)</script>',kind:'notes',source:'manual',url:'javascript:alert(1)',provenance:{local_path:'<img src=x onerror=bad()>'}};
test('source timeline orders real source clocks and keeps audit creation time separate',async()=>{
  const event=eventFixture({evidence:[
    {id:'1'.repeat(32),document_id:'1'.repeat(32),hash,title:'较晚邮件',kind:'mail',modified:'2026-10-04T00:00:00Z',provenance:{date_sent:'Fri, 02 Oct 2026 08:59:39 -0700'}},
    {id:'2'.repeat(32),document_id:'2'.repeat(32),hash,title:'较早微信',kind:'wechat',modified:'2026-10-04T00:00:00Z',provenance:{timestamp:1790956275}},
    {id:'3'.repeat(32),document_id:'3'.repeat(32),hash,title:'未知时间',kind:'notes',provenance:{captured_at:'2026-10-01T00:00:00Z'}}
  ]});
  const f=await fixture({events:[event],url:'http://127.0.0.1:8766/events.html?event='+ids.event});
  const rows=[...f.d.querySelectorAll('.ev-source-timeline li')];
  assert.deepEqual(rows.map(r=>r.querySelector('a').textContent),['较早微信','较晚邮件','未知时间']);
  assert.match(rows[0].textContent,/微信消息时间/);assert.match(rows[1].textContent,/邮件头发信时间/);
  assert.match(rows[2].textContent,/时间待核对/);assert.match(f.d.querySelector('#ev-detail').textContent,/关联仍待确认/);
  assert.match(f.d.querySelector('#ev-detail').textContent,/事项变更记录/);
  const url=new URL(rows[0].querySelector('a').href);assert.equal(url.searchParams.get('hash'),hash);f.close();
});
test('source timeline ignores unzoned dates and escapes untrusted source titles',async()=>{
  const event=eventFixture({evidence:[{id:ids.evidence,document_id:ids.doc,hash,title:'<img src=x onerror=bad()>',kind:'mail',modified:'2026-10-03',provenance:{date_sent:'2026-10-03 00:59',captured_at:'2026-10-03T00:00:00Z'}}]});
  const f=await fixture({events:[event],url:'http://127.0.0.1:8766/events.html?event='+ids.event});
  const row=f.d.querySelector('.ev-source-timeline li');assert.match(row.textContent,/时间待核对/);assert.equal(row.querySelector('img'),null);f.close();
});
function eventFixture(overrides={}){
  return {id:ids.event,title:'合成测试事项',summary:'只用于本地测试',priority:'important',revision:2,needs_confirmation:true,evidence:[{id:ids.evidence,document_id:ids.doc,hash,title:documentFixture.title,excerpt:'微信经验',start:1,end:5,state:'fresh',url:documentFixture.url,provenance:documentFixture.provenance}],actions:[],history:[{id:1,entity_type:'event',entity_id:ids.event,before:null,after:{title:'合成测试事项'},origin:'manual',reason:'测试建立',created_at:'2026-10-04T00:00:00Z'}],...overrides};
}
async function fixture({events=[],proposal,onRequest,url='http://127.0.0.1:8766/events.html'}={}){
  const dom=new JSDOM(fs.readFileSync('public/events.html','utf8'),{url,runScripts:'outside-only'});
  const w=dom.window,d=w.document,calls=[],model={events:copy(events),doc:copy(documentFixture)};
  w.HTMLDialogElement.prototype.showModal=function(){this.open=true;};w.HTMLDialogElement.prototype.close=function(){this.open=false;};
  if(proposal!==undefined)w.sessionStorage.setItem('studio-event-proposal',JSON.stringify(proposal));
  w.fetch=async(url,options={})=>{
    const call={url,method:options.method||'GET',headers:options.headers||{},body:options.body?JSON.parse(options.body):undefined};calls.push(call);
    let result=onRequest?await onRequest(call,model):undefined;
    if(result===undefined){
      if(url.startsWith('/api/events?'))result={events:model.events,counts:{all:model.events.length,important:model.events.filter(e=>e.priority==='important').length,mine:0,waiting:0,confirm:0,calendar:0}};
      else if(url==='/api/events'){
        const old=call.body.id?model.events.find(e=>e.id===call.body.id):null;
        const evidence=call.body.items?call.body.items.map((item,i)=>({...model.doc,...item,id:ids.evidence,document_id:item.id,excerpt:Array.from(model.doc.body).slice(item.start,item.end).join(''),state:'fresh'})):old.evidence;
        result={...eventFixture(),...old,...call.body,id:old?.id||ids.event,revision:old?old.revision+1:1,evidence};delete result.items;
        model.events=model.events.filter(e=>e.id!==result.id).concat(result);
      }else if(url.endsWith('/actions')){
        const current=model.events.find(e=>e.id===url.split('/')[3]),body=call.body;
        const old=body.id?current.actions.find(a=>a.id===body.id):null;
        const action={...body,id:old?.id||ids.action,revision:old?old.revision+1:1};
        result={...current,revision:current.revision+1,actions:current.actions.filter(a=>a.id!==action.id).concat(action)};
        model.events=model.events.filter(e=>e.id!==current.id).concat(result);
      }else if(url.startsWith('/api/events/'))result=model.events.find(e=>e.id===url.split('/')[3]);
      else if(url.startsWith('/api/knowledge/search'))result={results:[{...model.doc,body:undefined,excerpt:model.doc.body,excerpt_start:0}],total:1,next_offset:null};
      else if(url.startsWith('/api/knowledge/document/'))result=model.doc;
      else throw new Error('Unexpected synthetic endpoint: '+url);
    }
    if(result&&result.httpError)return {ok:false,status:result.httpError,json:async()=>({error:result.error})};
    return {ok:true,status:200,json:async()=>copy(result)};
  };
  w.eval(fs.readFileSync('public/events.js','utf8'));await flush();
  return {w,d,calls,model,close:()=>w.close()};
}
function submit(f,id){f.d.querySelector(id).dispatchEvent(new f.w.Event('submit',{bubbles:true,cancelable:true}));}
async function openFirst(f){f.d.querySelector('[data-event]').click();await flush();}
async function chooseRange(f){
  f.d.querySelector('#ev-knowledge-query').value='微信';f.d.querySelector('#ev-knowledge-search').click();await flush();
  f.d.querySelector('[data-preview]').click();await flush();
  f.d.querySelector('#ev-document-body').setSelectionRange(2,6);f.d.querySelector('#ev-pick-selection').click();
  f.d.querySelector('#ev-document-dialog [data-close]').click();
}
test('empty state is real and initial navigation makes no write or model request',async()=>{
  const f=await fixture();
  assert.match(f.d.querySelector('#ev-list').textContent,/还没有事项/);assert.equal(f.d.querySelectorAll('[data-event]').length,0);
  assert.match(f.calls[0].url,/view=all/);assert(f.calls.every(c=>c.method==='GET'));
  assert(!f.calls.some(c=>/assistant|completions|generation|sync/.test(c.url)));
  const links=Array.from(f.d.querySelectorAll('.ev-main-nav a'),a=>a.getAttribute('href'));
  for(const required of ['/?collection=all','/knowledge.html','/events.html'])assert(links.includes(required));f.close();
});
test('a valid event deep link loads its detail even when it is absent from the current list',async()=>{
  const f=await fixture({events:[eventFixture()],url:'http://127.0.0.1:8766/events.html?event='+ids.event,onRequest:call=>call.url.startsWith('/api/events?')?{events:[],counts:{all:1}}:undefined});
  assert(f.calls.some(call=>call.url==='/api/events/'+ids.event));assert.match(f.d.querySelector('#ev-detail').textContent,/合成测试事项/);assert(f.calls.every(call=>call.method==='GET'));f.close();
});
test('invalid and missing event deep links report real errors without creating an event',async()=>{
  const invalid=await fixture({url:'http://127.0.0.1:8766/events.html?event=../../invalid'});
  assert(!invalid.calls.some(call=>call.url.startsWith('/api/events/')));assert.match(invalid.d.querySelector('#ev-status').textContent,/编号无效/);invalid.close();
  const missing=await fixture({url:'http://127.0.0.1:8766/events.html?event='+ids.event,onRequest:call=>call.url==='/api/events/'+ids.event?{httpError:404,error:'事件不存在'}:undefined});
  assert.match(missing.d.querySelector('#ev-status').textContent,/事件不存在/);assert(missing.calls.every(call=>call.method==='GET'));missing.close();
});
test('an explicit event link does not open or consume a pending proposal',async()=>{
  const proposal={title:'保留的建议',summary:'',priority:'normal',needs_confirmation:true,items:[{id:ids.doc,hash,start:1,end:5}]};
  const f=await fixture({events:[eventFixture()],proposal,url:'http://127.0.0.1:8766/events.html?event='+ids.event});
  assert.match(f.d.querySelector('#ev-detail').textContent,/合成测试事项/);assert(!f.d.querySelector('#ev-event-dialog').open);assert(f.w.sessionStorage.getItem('studio-event-proposal'));assert(!f.calls.some(call=>call.url.startsWith('/api/knowledge/document')));f.close();
});
test('untrusted evidence and audit fields render as text with fixed-version source links',async()=>{
  const event=eventFixture({title:'<img src=x onerror=alert(1)>',summary:'<script>bad()</script>'});
  event.evidence[0].state='stale';event.evidence[0].excerpt='<svg onload=bad()>证据';event.history[0].reason='<img src=x>';
  const f=await fixture({events:[event]});await openFirst(f);
  assert.equal(f.d.querySelectorAll('#ev-list img,#ev-detail img,#ev-detail script,#ev-detail svg').length,0);
  assert.match(f.d.querySelector('#ev-detail').textContent,/<script>bad\(\)<\/script>/);
  assert.match(f.d.querySelector('#ev-detail').textContent,/原资料已更新/);
  const link=f.d.querySelector('.ev-evidence>a'),params=new URL(link.href).searchParams;
  assert.equal(params.get('document'),ids.doc);assert.equal(params.get('hash'),hash);assert.equal(params.get('start'),'1');assert.equal(params.get('end'),'5');
  assert(!Array.from(f.d.querySelectorAll('#ev-detail a')).some(a=>/^(javascript|data):/.test(a.href)));f.close();
});
test('explicit event save captures Unicode code-point span and document fingerprint',async()=>{
  const f=await fixture();f.d.querySelector('#ev-new').click();await chooseRange(f);
  assert.equal(f.d.querySelectorAll('#ev-picks .ev-pick').length,1);
  f.d.querySelector('#ev-event-title').value='本人确认的事项';f.d.querySelector('#ev-event-summary').value='核对后的概要';
  assert.equal(f.calls.filter(c=>c.method==='POST').length,0);
  submit(f,'#ev-event-form');await flush();
  const saved=f.calls.find(c=>c.url==='/api/events'&&c.method==='POST');
  assert.deepEqual(saved.body.items,[{id:ids.doc,hash,start:1,end:5}]);assert.equal(saved.body.title,'本人确认的事项');
  assert.equal(saved.body.origin,'manual');assert.equal(saved.headers['X-Article-Studio'],'1');assert(!('id'in saved.body));
  assert.match(f.d.querySelector('#ev-detail').textContent,/本人确认的事项/);f.close();
});
test('selection maps normalized textarea line endings to the original Unicode span',async()=>{
  const f=await fixture({onRequest:(call,model)=>{if(call.url.startsWith('/api/knowledge/document/'))return {...model.doc,body:'😀A\r\nB\rC'};}});
  f.d.querySelector('#ev-new').click();f.d.querySelector('#ev-knowledge-query').value='测试';f.d.querySelector('#ev-knowledge-search').click();await flush();
  f.d.querySelector('[data-preview]').click();await flush();
  const field=f.d.querySelector('#ev-document-body');assert.equal(field.value,'😀A\nB\nC');field.setSelectionRange(4,7);
  f.d.querySelector('#ev-pick-selection').click();f.d.querySelector('#ev-document-dialog [data-close]').click();f.d.querySelector('#ev-event-title').value='原文换行映射';submit(f,'#ev-event-form');await flush();
  assert.deepEqual(f.calls.find(c=>c.method==='POST').body.items,[{id:ids.doc,hash,start:4,end:7}]);f.close();
});
test('refresh and a late detail response do not overwrite an open edit',async()=>{
  let release;const later=new Promise(resolve=>{release=resolve;});let details=0;
  const f=await fixture({events:[eventFixture()],onRequest:async call=>{if(call.url==='/api/events/'+ids.event&&++details===2)return later;}});await openFirst(f);
  f.d.querySelector('#ev-refresh').click();await tick();f.d.querySelector('#ev-edit').click();
  f.d.querySelector('#ev-event-title').value='尚未保存的名称';f.d.querySelector('#ev-event-reason').value='本人正在核对';
  release(eventFixture({title:'服务端新名称',revision:8}));await flush();
  f.d.querySelector('#ev-refresh').click();await flush();
  assert.equal(f.d.querySelector('#ev-event-title').value,'尚未保存的名称');assert.equal(f.d.querySelector('#ev-event-reason').value,'本人正在核对');assert.equal(details,2);
  assert.match(f.d.querySelector('#ev-status').textContent,/编辑的内容已保留/);assert(f.calls.every(c=>c.method==='GET'));f.close();
});
test('update retains existing source snapshots without resubmitting stale documents',async()=>{
  const event=eventFixture();event.evidence[0].state='stale';
  const f=await fixture({events:[event]});await openFirst(f);f.d.querySelector('#ev-edit').click();
  f.d.querySelector('#ev-event-title').value='重新核对的名称';submit(f,'#ev-event-form');await flush();
  assert.equal(f.calls.filter(c=>c.method==='POST').length,0);assert.match(f.d.querySelector('#ev-event-status').textContent,/修改原因/);
  f.d.querySelector('#ev-event-reason').value='本人核实名称';submit(f,'#ev-event-form');await flush();
  const saved=f.calls.find(c=>c.method==='POST');assert.equal(saved.body.id,ids.event);assert.equal(saved.body.revision,2);assert.equal(saved.body.reason,'本人核实名称');
  assert(!('items'in saved.body));assert(!('picks'in saved.body));assert.equal(f.model.events[0].evidence[0].hash,hash);f.close();
});
test('action requires evidence, preserves unknown time, and marks self only by checkbox',async()=>{
  const f=await fixture({events:[eventFixture()]});await openFirst(f);f.d.querySelector('#ev-add-action').click();f.d.querySelector('#ev-action-summary').value='核对下一步';
  submit(f,'#ev-action-form');assert.match(f.d.querySelector('#ev-action-form-status').textContent,/行动依据/);assert.equal(f.calls.filter(c=>c.method==='POST').length,0);
  f.d.querySelector('#ev-action-evidence input').checked=true;f.d.querySelector('#ev-action-due').value='2026-10-05T14:00';submit(f,'#ev-action-form');
  assert.match(f.d.querySelector('#ev-action-form-status').textContent,/带时区/);
  f.d.querySelector('#ev-action-due').value='2026-02-30T14:00:00+08:00';submit(f,'#ev-action-form');assert.equal(f.calls.filter(c=>c.method==='POST').length,0);
  f.d.querySelector('#ev-action-due').value='';f.d.querySelector('#ev-action-self').checked=true;submit(f,'#ev-action-form');await flush();
  const saved=f.calls.find(c=>c.url.endsWith('/actions'));
  assert.deepEqual(saved.body,{summary:'核对下一步',owner:'',owner_is_self:true,due_at:'',status:'open',evidence_ids:[ids.evidence],origin:'manual',reason:'手动添加行动'});
  assert.match(f.d.querySelector('#ev-detail').textContent,/时间待确认/);assert.match(f.d.querySelector('#ev-detail').textContent,/由我跟进/);
  f.d.querySelector('#ev-edit').click();f.d.querySelector('#ev-event-reason').value='继续核对';submit(f,'#ev-event-form');await flush();
  assert.equal(f.calls.find(c=>c.url==='/api/events'&&c.method==='POST').body.revision,3);f.close();
});
test('action edits send their revision, explicit timezone and manual reason',async()=>{
  const action={id:ids.action,summary:'测试行动',owner:'某负责人',owner_is_self:false,due_at:'',status:'waiting',revision:4,evidence_ids:[ids.evidence]};
  const f=await fixture({events:[eventFixture({actions:[action]})]});await openFirst(f);f.d.querySelector('[data-action]').click();
  f.d.querySelector('#ev-action-status').value='done';f.d.querySelector('#ev-action-due').value='2026-10-05T14:00:00+08:00';submit(f,'#ev-action-form');assert.equal(f.calls.filter(c=>c.method==='POST').length,0);
  f.d.querySelector('#ev-action-reason').value='本人确认已完成';submit(f,'#ev-action-form');await flush();
  const saved=f.calls.find(c=>c.url.endsWith('/actions'));assert.equal(saved.body.id,ids.action);assert.equal(saved.body.revision,4);assert.equal(saved.body.due_at,'2026-10-05T14:00:00+08:00');assert.equal(saved.body.reason,'本人确认已完成');f.close();
});
test('calendar excludes missing, unconfirmed, invalid, completed and cancelled dates',async()=>{
  const actions=[['明确约定','open','2026-10-05T14:00:00+08:00'],['未确认建议','needs_confirmation','2026-10-05T14:00:00+08:00'],['未约定','open',''],['已取消','cancelled','2026-10-06T14:00:00Z'],['已完成','done','2026-10-06T14:00:00Z'],['无效日期','open','2026-02-30T00:00:00Z']].map(([summary,status,due_at],i)=>({id:String(i).repeat(32),summary,status,due_at,owner:'',evidence_ids:[]}));
  const f=await fixture({events:[eventFixture({actions})]});f.d.querySelector('[data-view="calendar"]').click();await flush();
  const rows=f.d.querySelectorAll('.ev-calendar-row');assert.equal(rows.length,1);assert.match(rows[0].textContent,/明确约定/);assert.match(f.calls.at(-1).url,/view=calendar/);f.close();
});
test('unconfirmed suggestions are counted separately from accepted follow-up actions',async()=>{
  const actions=[{id:ids.action,summary:'待核对建议',status:'needs_confirmation',due_at:'',owner:'',evidence_ids:[]}];
  const f=await fixture({events:[eventFixture({actions})]});
  const card=f.d.querySelector('.ev-card-foot');assert.match(card.textContent,/0 项待跟进/);assert.match(card.textContent,/1 项待确认/);f.close();
});
test('my follow-ups show only accepted personal actions and use Beijing dates with exact evidence',async()=>{
  const own=(id,summary,status,due_at)=>({id:id.repeat(32),summary,status,due_at,owner_is_self:true,owner:'',evidence_ids:[ids.evidence]});
  const actions=[own('1','昨天的已接受约定','open','2026-10-04T15:30:00Z'),own('2','今天等待回复','waiting','2026-10-05T02:00:00Z'),own('3','北京时间明天的约定','open','2026-10-05T17:00:00Z'),own('4','尚无约定时间','open',''),own('5','无时区时间需核对','open','2026-10-05T15:00'),own('6','本人未确认建议','needs_confirmation','2026-10-04T12:00:00Z'),own('7','已完成行动','done',''),own('8','已取消行动','cancelled',''),{...own('9','其他人跟进','open',''),owner_is_self:false}];
  const f=await fixture({events:[eventFixture({actions})]});
  f.w.Date.now=()=>Date.parse('2026-10-05T00:10:00Z');
  f.d.querySelector('[data-view="mine"]').click();await flush();
  assert.equal(f.d.querySelector('#ev-result-count').textContent,'5 项行动');
  assert.deepEqual([...f.d.querySelectorAll('.ev-followup-group')].map(x=>x.dataset.group),['past','today','later','unknown']);
  assert.deepEqual([...f.d.querySelectorAll('.ev-followup-row strong')].map(x=>x.textContent),actions.slice(0,5).map(a=>a.summary));
  assert.match(f.d.querySelector('[data-group="today"]').textContent,/10:00:00.*北京时间/);
  assert.match(f.d.querySelector('[data-group="later"]').textContent,/2026\/10\/6/);
  assert.equal(f.d.querySelectorAll('[data-group="unknown"] .ev-followup-row').length,2);
  const selected=f.d.querySelector('[data-focus-action="'+'2'.repeat(32)+'"]');
  assert.match(selected.textContent,/1 条依据/);selected.click();await flush();
  const action=f.d.getElementById('ev-action-'+'2'.repeat(32));assert.equal(f.d.activeElement,action);
  const params=new URL(action.querySelector('.ev-action-evidence a').href).searchParams;
  assert.equal(params.get('document'),ids.doc);assert.equal(params.get('hash'),hash);assert.equal(params.get('start'),'1');assert.equal(params.get('end'),'5');
  assert(!f.calls.some(call=>call.method==='POST'));f.close();
});
test('a personal candidate does not create a visible accepted follow-up',async()=>{
  const actions=[{id:ids.action,summary:'仍需确认',owner_is_self:true,status:'needs_confirmation',due_at:'2026-10-05T09:00:00Z',evidence_ids:[ids.evidence]}];
  const f=await fixture({events:[eventFixture({actions})]});f.d.querySelector('[data-view="mine"]').click();await flush();
  assert.equal(f.d.querySelectorAll('.ev-followup-row').length,0);assert.match(f.d.querySelector('#ev-list').textContent,/未确认的建议.*待确认/);
  assert.equal(f.d.querySelector('#ev-result-count').textContent,'0 项行动');assert(!f.calls.some(call=>call.method==='POST'));f.close();
});
test('backend conflict shows its real error and preserves event draft and picked evidence',async()=>{
  const f=await fixture({onRequest:call=>call.url==='/api/events'?{httpError:409,error:'这份来源已更新，请重新读取'}:undefined});
  f.d.querySelector('#ev-new').click();await chooseRange(f);f.d.querySelector('#ev-event-title').value='冲突时保留的名称';submit(f,'#ev-event-form');await flush();
  assert(f.d.querySelector('#ev-event-dialog').open);assert.equal(f.d.querySelector('#ev-event-title').value,'冲突时保留的名称');assert.equal(f.d.querySelectorAll('.ev-pick').length,1);
  assert.match(f.d.querySelector('#ev-event-status').textContent,/这份来源已更新/);assert(!f.d.querySelector('#ev-event-save').disabled);f.close();
});
test('proposal validates current source, stays a preview, and saves only whitelisted event fields',async()=>{
  const proposal={title:'待本人核对的建议',summary:'合成建议概要',priority:'normal',needs_confirmation:true,items:[{id:ids.doc,hash,start:1,end:5}],action_candidates:[{summary:'候选行动',owner:'',owner_is_self:false,due_at:'',status:'needs_confirmation'}],evidence_preview:[{private:'not a save field'}]};
  const f=await fixture({proposal});assert(f.d.querySelector('#ev-event-dialog').open);assert(f.calls.every(c=>c.method==='GET'));assert.match(f.d.querySelector('#ev-proposed-actions').textContent,/不会自动添加/);
  submit(f,'#ev-event-form');await flush();const writes=f.calls.filter(c=>c.method==='POST');assert.equal(writes.length,1);assert.equal(writes[0].url,'/api/events');assert(!('action_candidates'in writes[0].body));assert(!('evidence_preview'in writes[0].body));assert.equal(f.w.sessionStorage.getItem('studio-event-proposal'),null);
  f.d.querySelector('[data-candidate]').click();assert.match(f.d.querySelector('#ev-action-form-status').textContent,/尚未保存/);assert.equal(f.calls.filter(c=>c.method==='POST').length,1);assert.equal(f.d.querySelectorAll('#ev-action-evidence input:checked').length,0);
  f.d.querySelector('#ev-action-evidence input').checked=true;submit(f,'#ev-action-form');await flush();assert.equal(f.d.querySelectorAll('[data-candidate]').length,0);assert.equal(f.model.events[0].actions.length,1);f.close();
});
test('stale and malformed proposals cannot create events or select a misleading excerpt',async()=>{
  const valid={title:'合成建议',summary:'',priority:'normal',needs_confirmation:true,items:[{id:ids.doc,hash:'f'.repeat(64),start:1,end:5}]};
  const stale=await fixture({proposal:valid});assert(!stale.d.querySelector('#ev-event-dialog').open);assert.match(stale.d.querySelector('#ev-status').textContent,/原资料已变化/);assert(stale.calls.every(c=>c.method==='GET'));assert(stale.w.sessionStorage.getItem('studio-event-proposal'));stale.close();
  const malformed=await fixture({proposal:{...valid,items:[{id:'bad',hash,start:0,end:5}]}});assert(!malformed.calls.some(c=>c.url.startsWith('/api/knowledge/document')));assert.match(malformed.d.querySelector('#ev-status').textContent,/有效/);malformed.close();
});
