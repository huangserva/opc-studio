const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs');
const {JSDOM}=require('jsdom');
const settle=async()=>{for(let i=0;i<8;i++)await new Promise(r=>setImmediate(r));};
async function fixture(t){
 const dom=new JSDOM(fs.readFileSync('public/deals.html','utf8'),{url:'http://127.0.0.1:8766/deals.html',runScripts:'outside-only'}),w=dom.window,d=w.document;t.after(()=>w.close());
 w.crypto.randomUUID=()=>require('node:crypto').randomUUID();w.HTMLElement.prototype.scrollIntoView=()=>{};w.HTMLElement.prototype.scrollTo=()=>{};
 w.HTMLDialogElement.prototype.showModal=function(){this.open=true;};w.HTMLDialogElement.prototype.close=function(){this.open=false;this.dispatchEvent(new w.Event('close'));};
 const deal={id:'a'.repeat(32),revision:1,title:'原始合作<script>bad</script>',customer:'客户',stage:'draft',quote:null,currency:null,quote_confirmed:false,needs_review:true,settled:false,archived:false,expected_deliverables:3,original_group:'已付',original_text:'已付，但日期未补',reported_payment:'client_paid',receipts:[],deliverables:[],publications:[],sources:[],audit:[],cash:{status:'unpaid',outstanding:null,totals:{CNY:'0.00',USD:'0.00'}}};
 const state={deals:[deal],summary:{signed:0,closed:0,published:0,received:{CNY:'0.00',USD:'0.00'},outstanding:{CNY:'0.00',USD:'0.00'},needs_review:1,unknown_dates:1,unknown_quote:1},imports:[]};
 const options={stages:{contact:'初步接洽',negotiating:'洽谈报价',signed:'合作敲定',writing:'待写',in_progress:'撰写中',draft:'初稿完成',review:'审核 / 修改',ready:'待发布',published:'已发布',payment_pending:'待结账',settled:'已结清',unknown:'进度待核对',closed:'已结案',cancelled:'未合作 / 已取消'},stage_groups:{'洽谈合作':['contact','negotiating','signed'],'创作交付':['writing','in_progress','draft','review'],'发布结账':['ready','published','payment_pending','settled'],'其他进度':['unknown','closed','cancelled']},delivery_stages:{pending:'待写',draft:'初稿完成'},works:[{slug:'work',title:'合成作品',version:'v0002',versions:['v0002','v0001']}],bindings:[{slug:'work',delivery_id:'b'.repeat(32),platform:'x',version:'v0001',title:'固定旧版本',url:'https://x.com/test/status/123',account:'test'}]};
 const calls=[];let postFailure=false;
 w.fetch=async(url,o={})=>{const body=o.body&&JSON.parse(o.body);calls.push({url,body});if(o.method==='POST'&&postFailure)return{ok:false,json:async()=>({error:'合成重试错误'})};return{ok:true,json:async()=>o.method==='POST'?deal:url==='/api/deals/options'?options:url.startsWith('/api/deals?')?state:deal};};
 w.eval(fs.readFileSync('public/deals.js','utf8'));await settle();return{w,d,calls,deal,state,setFailure:v=>{postFailure=v}};
}
test('unknown quote and reported paid remain unconfirmed, with source group and escaped titles',async t=>{
 const{d}=await fixture(t);assert.match(d.querySelector('#dl-list').textContent,/金额未确认/);assert.match(d.querySelector('#dl-list').textContent,/原记录“已付”/);assert.equal(d.querySelector('#dl-list script'),null);assert.match(d.querySelector('#dl-stat-note').textContent,/未归入月份/);
});
test('actual receipt sends exact amount, date, currency and revision; delivery stage is untouched',async t=>{
 const{w,d,calls}=await fixture(t);d.querySelector('[data-deal]').click();await settle();d.querySelector('[data-form="receipt"] details')?.click();const f=d.querySelector('[data-form="receipt"]');
 f.elements.amount.value='400.25';f.elements.currency.value='USD';f.elements.paid_on.value='2026-10-03';f.dispatchEvent(new w.Event('submit',{bubbles:true,cancelable:true}));await settle();
 const p=calls.find(c=>c.url.endsWith('/receipt')).body;assert.equal(p.amount,'400.25');assert.equal(p.paid_on,'2026-10-03');assert.equal(p.currency,'USD');assert.equal(p.revision,1);assert.equal('stage' in p,false);
});
test('failed receipt retry keeps idempotency key while double click cannot duplicate submission',async t=>{
 const{w,d,calls,setFailure}=await fixture(t);d.querySelector('[data-deal]').click();await settle();const f=d.querySelector('[data-form="receipt"]');f.elements.amount.value='100';f.elements.paid_on.value='2026-10-03';setFailure(true);
 f.dispatchEvent(new w.Event('submit',{bubbles:true,cancelable:true}));f.dispatchEvent(new w.Event('submit',{bubbles:true,cancelable:true}));await settle();assert.equal(calls.filter(c=>c.url.endsWith('/receipt')).length,1);assert.match(d.querySelector('#dl-error').textContent,/重试错误/);
 setFailure(false);f.dispatchEvent(new w.Event('submit',{bubbles:true,cancelable:true}));await settle();const ps=calls.filter(c=>c.url.endsWith('/receipt'));assert.equal(ps[0].body.request_key,ps[1].body.request_key);
});
test('registered publication uses its pinned version binding, without replacing it with latest work version',async t=>{
 const{w,d,calls}=await fixture(t);d.querySelector('[data-deal]').click();await settle();const f=d.querySelector('[data-form="publication"]');f.elements.binding.value='0';f.elements.binding.dispatchEvent(new w.Event('change',{bubbles:true}));
 assert.equal(f.elements.version.value,'v0001');assert.equal(f.elements.url.disabled,true);f.elements.published_on.value='2026-10-02';f.dispatchEvent(new w.Event('submit',{bubbles:true,cancelable:true}));await settle();
 const p=calls.find(c=>c.url.endsWith('/publication')).body;assert.equal(p.delivery_id,'b'.repeat(32));assert.equal(p.slug,'work');assert.equal(p.published_on,'2026-10-02');assert.equal('version' in p,false);
});
test('delivery can choose a historical work version',async t=>{
 const{w,d,calls}=await fixture(t);d.querySelector('[data-deal]').click();await settle();const f=d.querySelector('[data-form="deliverable"]');f.elements.slug.value='work';f.elements.slug.dispatchEvent(new w.Event('change',{bubbles:true}));f.elements.version.value='v0001';f.elements.title.value='第一篇';f.dispatchEvent(new w.Event('submit',{bubbles:true,cancelable:true}));await settle();assert.equal(calls.find(c=>c.url.endsWith('/deliverable')).body.version,'v0001');
});
test('manual new deal settlement flag does not submit invented receipt fields',async t=>{
 const{w,d,calls}=await fixture(t);d.querySelector('#dl-new').click();const f=d.querySelector('#dl-base');f.elements.title.value='新单';f.elements.customer.value='客户';f.elements.settled.checked=true;f.dispatchEvent(new w.Event('submit',{cancelable:true}));await settle();const p=calls.find(c=>c.url==='/api/deals').body;assert.equal(p.settled,true);assert.equal('paid_on' in p,false);assert.equal(p.signed_on,'');assert.equal(p.expected_deliverables,null);
});
test('original grouping and review filters work without changing global financial stats',async t=>{
 const{w,d,deal,state}=await fixture(t);state.deals.push({...deal,id:'c'.repeat(32),original_group:'未付',needs_review:false});d.querySelector('#dl-refresh').click();await settle();d.querySelector('#dl-review').checked=true;d.querySelector('#dl-review').dispatchEvent(new w.Event('change'));assert.equal(d.querySelectorAll('#dl-list tr').length,1);assert.match(d.querySelector('#dl-summary').textContent,/¥0.00 CNY/);
});
test('unbound publication can later select a fixed work version and register its actual date',async t=>{
 const{w,d,calls,deal}=await fixture(t);deal.publications.push({id:'d'.repeat(32),platform:'x',account:'test',url:'https://x.com/test/status/456',published_on:'',quality_note:'',slug:'',version:'',delivery_id:''});
 d.querySelector('[data-deal]').click();await settle();d.querySelector('[data-edit-publication]').click();const f=d.querySelector('[data-form="publication-edit"]');assert.equal(f.elements.slug.disabled,false);
 f.elements.slug.value='work';f.elements.slug.dispatchEvent(new w.Event('change',{bubbles:true}));f.elements.version.value='v0001';f.elements.published_on.value='2026-10-04';f.elements.quality_note.value='客户反馈待补';f.dispatchEvent(new w.Event('submit',{bubbles:true,cancelable:true}));await settle();
 const p=calls.find(c=>c.url.endsWith('/publication-edit')).body;assert.equal(p.child_id,'d'.repeat(32));assert.equal(p.version,'v0001');assert.equal(p.slug,'work');assert.equal(p.published_on,'2026-10-04');assert.ok(calls.filter(c=>c.url==='/api/deals/options').length>=2);
});
test('editing a publication already bound to an old version keeps that version pinned',async t=>{
 const{w,d,calls,deal}=await fixture(t);deal.publications.push({id:'d'.repeat(32),platform:'x',account:'test',url:'https://x.com/test/status/456',published_on:'',quality_note:'',slug:'work',version:'v0001',delivery_id:'b'.repeat(32)});
 d.querySelector('[data-deal]').click();await settle();d.querySelector('[data-edit-publication]').click();const f=d.querySelector('[data-form="publication-edit"]');assert.equal(f.elements.slug.disabled,true);assert.equal(f.elements.version.disabled,true);assert.equal(f.elements.version.value,'v0001');
 f.elements.published_on.value='2026-10-04';f.dispatchEvent(new w.Event('submit',{bubbles:true,cancelable:true}));await settle();const p=calls.find(c=>c.url.endsWith('/publication-edit')).body;assert.equal('slug' in p,false);assert.equal('version' in p,false);assert.equal(p.published_on,'2026-10-04');
});
test('timeline groups actual dates apart from undated raw list and folds all groups',async t=>{
 const{w,d,deal,state}=await fixture(t);d.querySelector('#dl-view').value='timeline';d.querySelector('#dl-view').dispatchEvent(new w.Event('change'));state.deals.push({...deal,id:'e'.repeat(32),title:'带原消息日期',last_business_at:'2026-10-05T17:00:00+00:00'});d.querySelector('#dl-refresh').click();await settle();assert.match(d.querySelector('#dl-timeline-list').textContent,/2026-10-06/);assert.match(d.querySelector('#dl-timeline-list').textContent,/时间待补/);d.querySelector('#dl-fold').click();assert.ok([...d.querySelectorAll('#dl-timeline-list>.dl-date-group')].every(g=>!g.open));d.querySelector('#dl-expand').click();assert.ok([...d.querySelectorAll('#dl-timeline-list>.dl-date-group')].every(g=>g.open));
});
test('wechat source review sends evidence identity and preserves receipt fields',async t=>{
 const{w,d,calls,deal}=await fixture(t);const e={id:'f'.repeat(32),state:'candidate',at:'2026-10-05T15:59:00Z',title:'合成微信来源',text:'报价4000元',provenance:{sender:'客户'},price_mentions:['4000元'],url:'/knowledge.html?document='+('f'.repeat(32))};deal.timeline=[{kind:'wechat',at:e.at,title:e.title,detail:e.text,evidence:e}];d.querySelector('[data-deal]').click();await settle();assert.match(d.querySelector('.dl-source-section').textContent,/2026\/10\/5/);const b=d.querySelector('[data-state="confirmed"]');b.click();await settle();const p=calls.find(c=>c.url.endsWith('/evidence')).body;assert.equal(p.evidence_id,e.id);assert.equal(p.state,'confirmed');assert.equal('amount' in p,false);
});
test('deal assistant context follows selected deal and can be cleared',async t=>{
 const{w,d,deal}=await fixture(t);d.querySelector('[data-deal]').click();await settle();assert.equal(w.DealsPage.context().ui.deal_id,deal.id);w.DealsPage.clear();assert.equal(w.DealsPage.context().ui.page,'deals');assert.equal('deal_id' in w.DealsPage.context().ui,false);
});
test('AI renders an actual tool proposal and applies only after confirmation',async t=>{
 const{w,d,deal,calls}=await fixture(t);const p={id:'f'.repeat(32),deal_id:deal.id,revision:1,title:deal.title,operation:'update',changes:{stage:'draft'},before:{stage:'writing'},reason:'用户明确要求'};w.DealsPage={context:()=>({ui:{page:'deals',deal_id:deal.id}}),target:()=>deal,refresh:async()=>{}};
 w.fetch=async(url,o={})=>{calls.push({url,body:o.body&&JSON.parse(o.body)});return {ok:true,json:async()=>url==='/api/assistant'?{model:'fixture',messages:[]}:url==='/api/assistant/send'?{id:'c'.repeat(32),prompt:'完成了',status:'running',actions:[]}:url.startsWith('/api/assistant/job/')?{id:'c'.repeat(32),prompt:'完成了',status:'done',answer:'请确认',actions:[{name:'propose_deal_update',status:'done',deal_proposal:p}]}:deal};};
 w.eval(fs.readFileSync('public/deals-assistant.js','utf8'));d.querySelector('#assistant-toggle').click();await settle();d.querySelector('#assistant-input').value='完成了';d.querySelector('#da-form').dispatchEvent(new w.Event('submit',{bubbles:true,cancelable:true}));await settle();assert.match(d.querySelector('.da-proposal').textContent,/待写/);assert.equal(calls.filter(c=>c.url.endsWith('/apply-proposal')).length,0);d.querySelector('[data-proposal]').click();d.querySelector('[data-proposal]').click();await settle();assert.equal(calls.filter(c=>c.url.endsWith('/apply-proposal')).length,1);assert.equal(d.querySelector('[data-proposal]').disabled,true);assert.equal(calls.find(c=>c.url==='/api/assistant/send').body.context.ui.deal_id,deal.id);
});
test('selected deal chat excludes another deal preview and whole-ledger mode restores it',async t=>{
 const{w,d,deal}=await fixture(t);let selected=deal;
 w.DealsPage={context:()=>({ui:{page:'deals',deal_id:selected?.id}}),target:()=>selected,refresh:async()=>{}};
 const other='9'.repeat(32);
 w.fetch=async()=>({ok:true,json:async()=>({model:'fixture',messages:[
  {id:'1',context:{ui:{page:'deals',deal_id:other}},prompt:'另一笔的付款信息',answer:'保留另一笔的记录',actions:[]},
  {id:'2',context:{ui:{page:'deals',deal_id:deal.id}},prompt:'当前这笔的初稿',answer:'当前这笔的记录',actions:[]}
 ]})});
 w.eval(fs.readFileSync('public/deals-assistant.js','utf8'));d.querySelector('#assistant-toggle').click();await settle();
 assert.match(d.querySelector('#da-messages').textContent,/当前这笔的记录/);assert.doesNotMatch(d.querySelector('#da-messages').textContent,/另一笔/);
 selected=null;w.dispatchEvent(new w.Event('deals-target-changed'));assert.match(d.querySelector('#da-messages').textContent,/保留另一笔的记录/);
});
test('old preview mixing payment waiting with fulfillment cannot be confirmed',async t=>{
 const{w,d,deal,calls}=await fixture(t);
 const p={id:'f'.repeat(32),deal_id:deal.id,revision:1,title:deal.title,operation:'update',changes:{stage:'review',deadline:'2026-10-31'},before:{stage:'unknown',deadline:''},reason:'对方10月底前打款',user_message:'这笔现在等待打款，对方说10月底之前'};
 w.DealsPage={context:()=>({ui:{page:'deals',deal_id:deal.id}}),target:()=>deal,refresh:async()=>{}};
 w.fetch=async(url,o={})=>{calls.push({url,body:o.body&&JSON.parse(o.body)});return{ok:true,json:async()=>url==='/api/assistant'?{model:'fixture',messages:[{id:'1',context:{ui:{page:'deals',deal_id:deal.id}},prompt:p.user_message,answer:'旧预览',actions:[{deal_proposal:p}]}]}:{}};};
 w.eval(fs.readFileSync('public/deals-assistant.js','utf8'));d.querySelector('#assistant-toggle').click();await settle();
 const b=d.querySelector('[data-proposal]');assert.equal(b.disabled,true);assert.match(b.textContent,/重新生成/);b.click();await settle();assert.equal(calls.filter(c=>c.url.endsWith('/apply-proposal')).length,0);assert.match(d.querySelector('.da-proposal').textContent,/台账尚未改动/);
});

test('commercial progress is the default, dates remain optional, and stage card filters without changing global counts',async t=>{
 const{w,d,state,calls}=await fixture(t);assert.equal(d.querySelector('#dl-view').value,'progress');assert.equal(d.querySelector('.dl-finance').open,false);assert.match(d.querySelector('#dl-timeline-list>.dl-date-group>summary').textContent,/初稿完成/);
 state.stage_counts={draft:7,payment_pending:2};d.querySelector('#dl-refresh').click();await settle();
 assert.match(d.querySelector('[data-stage="draft"]').textContent,/7/);d.querySelector('[data-stage="payment_pending"]').click();await settle();
 assert.equal(d.querySelector('#dl-stage').value,'payment_pending');assert.ok(calls.some(c=>c.url.includes('stage=payment_pending')));assert.match(d.querySelector('[data-stage="draft"]').textContent,/7/);assert.equal(d.querySelector('[data-stage="payment_pending"]').getAttribute('aria-pressed'),'true');
 d.querySelector('#dl-all-stages').click();await settle();assert.equal(d.querySelector('#dl-stage').value,'');
 d.querySelector('#dl-view').value='table';d.querySelector('#dl-view').dispatchEvent(new w.Event('change'));assert.equal(d.querySelector('.dl-table-wrap').hidden,false);assert.equal(d.querySelector('#dl-expand').hidden,true);
});
test('AI waiting-payment preview uses the commercial stage and can be confirmed without fake receipt',async t=>{
 const{w,d,deal,calls}=await fixture(t);const p={id:'f'.repeat(32),deal_id:deal.id,revision:1,title:deal.title,operation:'update',changes:{stage:'payment_pending',notes:'对方10月底前打款'},before:{stage:'published',notes:''},user_message:'现在等待打款，对方说10月底前'};
 w.DealsPage={target:()=>deal,context:()=>({ui:{page:'deals',deal_id:deal.id}}),refresh:async()=>{}};
 w.fetch=async(url,o={})=>{calls.push({url,body:o.body&&JSON.parse(o.body)});return {ok:true,json:async()=>url==='/api/assistant'?{messages:[{id:'1',context:{ui:{page:'deals',deal_id:deal.id}},prompt:p.user_message,actions:[{deal_proposal:p}]}]}:deal};};
 w.eval(fs.readFileSync('public/deals-assistant.js','utf8'));d.querySelector('#assistant-toggle').click();await settle();const b=d.querySelector('[data-proposal]');assert.equal(b.disabled,false);assert.match(d.querySelector('.da-proposal').textContent,/待结账/);assert.equal(calls.filter(c=>c.url.endsWith('/apply-proposal')).length,0);b.click();await settle();assert.equal(calls.filter(c=>c.url.endsWith('/apply-proposal')).length,1);assert.equal(calls.find(c=>c.url.endsWith('/apply-proposal')).body.proposal_id,p.id);
});

test('daily follow-up separates unknown history and keeps original unpaid quotes visible on review',async t=>{
 const{w,d,state,deal}=await fixture(t);state.deals.push({...deal,id:'e'.repeat(32),title:'合成项目甲',stage:'unknown',quote:null,original_group:'未付',original_text:'合成项目甲 1234',price_note:'合成项目甲 1234'});d.querySelector('#dl-refresh').click();await settle();
 assert.equal(d.querySelector('.dl-stage-details').open,false);assert.equal(d.querySelector('#dl-focus [data-scope="work"]').getAttribute('aria-pressed'),'true');assert.equal(d.querySelectorAll('.dl-deal-card').length,1);
 d.querySelector('[data-scope="review"]').click();await settle();assert.equal(d.querySelectorAll('.dl-deal-card').length,1);assert.match(d.querySelector('.dl-deal-card').textContent,/合成项目甲 1234/);assert.match(d.querySelector('.dl-cash-value').textContent,/原记未付/);assert.match(d.querySelector('#dl-scope-note').textContent,/缺少明确进度/);assert.equal(d.querySelector('#dl-timeline-list>.dl-date-group').open,true);
 d.querySelector('[data-scope="all"]').click();await settle();assert.equal(d.querySelectorAll('.dl-deal-card').length,2);
});
test('next-step progress is previewed then saved as a revision-pinned stage change only',async t=>{
 const{w,d,calls,deal}=await fixture(t);d.querySelector('[data-next="review"]').click();await settle();const f=d.querySelector('#dl-progress-form');assert.equal(d.querySelector('#dl-progress-dialog').open,true);assert.equal(f.elements.stage.value,'review');assert.equal(calls.filter(c=>c.url==='/api/deals').length,0);
 f.dispatchEvent(new w.Event('submit',{cancelable:true}));f.dispatchEvent(new w.Event('submit',{cancelable:true}));await settle();const mutations=calls.filter(c=>c.url==='/api/deals');assert.equal(mutations.length,1);assert.equal(mutations[0].body.revision,deal.revision);assert.equal(mutations[0].body.stage,'review');assert.equal('quote' in mutations[0].body,false);assert.equal('settled' in mutations[0].body,false);assert.equal('paid_on' in mutations[0].body,false);
});
test('settled progress requires explicit cash confirmation and never creates a receipt',async t=>{
 const{w,d,calls}=await fixture(t);d.querySelector('[data-progress-deal]').click();await settle();const f=d.querySelector('#dl-progress-form');f.elements.stage.value='settled';f.elements.stage.dispatchEvent(new w.Event('change'));assert.equal(d.querySelector('#dl-settlement-choice').hidden,false);
 f.dispatchEvent(new w.Event('submit',{cancelable:true}));await settle();assert.equal(calls.filter(c=>c.url==='/api/deals').length,0);assert.match(d.querySelector('#dl-progress-error').textContent,/确认全部到账/);
 f.elements.settled.checked=true;f.dispatchEvent(new w.Event('submit',{cancelable:true}));await settle();const p=calls.find(c=>c.url==='/api/deals').body;assert.equal(p.stage,'settled');assert.equal(p.settled,true);assert.equal('paid_on' in p,false);assert.equal(calls.filter(c=>c.url.endsWith('/receipt')).length,0);
});
test('payment and publication next steps open record forms without posting or registering cash',async t=>{
 const{d,state,deal,calls}=await fixture(t);deal.stage='payment_pending';d.querySelector('#dl-refresh').click();await settle();d.querySelector('[data-next="receipt"]').click();await settle();assert.equal(d.querySelector('#dl-detail').open,true);assert.equal(d.querySelector('[data-form="receipt"]').parentElement.open,true);assert.equal(calls.some(c=>c.body),false);
});

test('cash follow-up includes user-reported unpaid without inventing a payment_pending stage',async t=>{
 const{d,state,deal,calls}=await fixture(t);state.deals.push({...deal,id:'8'.repeat(32),title:'原记未付商单',stage:'unknown',original_group:'未付'});d.querySelector('#dl-refresh').click();await settle();assert.match(d.querySelector('[data-scope="payment"]').textContent,/收款跟进.*1/);
 d.querySelector('[data-scope="payment"]').click();await settle();assert.match(d.querySelector('.dl-deal-card').textContent,/进度待核对/);assert.match(d.querySelector('.dl-cash-value').textContent,/原记未付/);assert.equal(d.querySelector('#dl-timeline-list>.dl-date-group').open,true);assert.equal(d.querySelector('[data-next="receipt"]').textContent,'登记到账');assert.equal(state.deals[1].stage,'unknown');assert.equal(calls.some(c=>c.body),false);
});
