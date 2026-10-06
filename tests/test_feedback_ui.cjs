const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs');
const {JSDOM}=require('jsdom');
const tick=()=>new Promise(resolve=>setImmediate(resolve));
async function flush(){for(let i=0;i<5;i++)await tick();}
const copy=value=>JSON.parse(JSON.stringify(value));
const ident='a'.repeat(32),snapId='b'.repeat(32),hash='c'.repeat(64);
const binding={slug:'synthetic-post',delivery_id:ident,platform:'x',version:'v0001',title:'合成发布项目',url:'https://x.com/test/status/123',account:'test',registration_at:'2026-10-04T00:00:00Z',work_url:'/?article=synthetic-post&version=v0001'};
function snapshot(overrides={}){return {id:snapId,binding:copy(binding),metrics:{impressions:'1,200',likes:'0'},collected_at:'2026-10-04T02:00:00+08:00',created_at:'2026-10-04T03:00:00+08:00',range:{kind:'interval',start:'2026-10-02T00:00:00+08:00',end:'2026-10-03T00:00:00+08:00',label:'合成区间指标口径'},provenance:{type:'manual',url:'https://analytics.x.com/content',note:'合成后台说明'},platform_verified:false,notice:'用户导入，未向平台校验；指标含义和统计时间窗口不同，不能直接比较。登记时间不是实际发布时间。',hash,knowledge_id:null,...overrides};}
async function fixture({bindings=[binding],snapshots=[],onRequest,url='http://127.0.0.1:8766/feedback.html'}={}){
  const dom=new JSDOM(fs.readFileSync('public/feedback.html','utf8'),{url,runScripts:'outside-only'}),w=dom.window,d=w.document,calls=[],model={bindings:copy(bindings),snapshots:copy(snapshots)};
  w.HTMLDialogElement.prototype.showModal=function(){this.open=true;};w.HTMLDialogElement.prototype.close=function(){this.open=false;};
  w.fetch=async(url,options={})=>{
    const call={url,method:options.method||'GET',headers:options.headers||{},body:options.body?JSON.parse(options.body):undefined};calls.push(call);
    let result=onRequest?await onRequest(call,model):undefined;
    if(result===undefined){
      if(url==='/api/feedback')result={bindings:model.bindings,capabilities:{},unavailable:0};
      else if(url==='/api/feedback/observations')result={observations:[],total:0};
      else if(url.startsWith('/api/feedback/snapshots?'))result={snapshots:model.snapshots.map(s=>({...s,provenance:{...s.provenance,text:undefined}})),total:model.snapshots.length};
      else if(url==='/api/feedback/import'){result=snapshot({...call.body,binding:model.bindings.find(b=>b.delivery_id===call.body.delivery_id),metrics:call.body.metrics||{impressions:'1,200'},provenance:call.body.provenance});model.snapshots.push(result);}
      else if(url.endsWith('/knowledge'))result={snapshot_id:snapId,document_id:ident,hash,url:'/knowledge.html?document='+ident+'&start=0&end=100&hash='+hash,summary:'原始统计已回库，尚未生成模型复盘。'};
      else if(url.startsWith('/api/feedback/snapshot/'))result=model.snapshots.find(s=>s.id===url.split('/').at(-1));
      else throw new Error('Unexpected synthetic endpoint '+url);
    }
    if(result?.httpError)return {ok:false,status:result.httpError,json:async()=>({error:result.error})};
    return {ok:true,status:200,json:async()=>copy(result)};
  };
  w.eval(fs.readFileSync('public/feedback.js','utf8'));await flush();
  return {w,d,calls,model,close:()=>w.close()};
}
const submit=f=>f.d.querySelector('#fb-form').dispatchEvent(new f.w.Event('submit',{bubbles:true,cancelable:true}));
function commonFields(f){f.d.querySelector('#fb-collected').value='2026-10-04T10:00:00+08:00';f.d.querySelector('#fb-range-start').value='2026-10-02T00:00:00+08:00';f.d.querySelector('#fb-range-end').value='2026-10-03T00:00:00+08:00';f.d.querySelector('#fb-range-label').value='原始区间口径，不与累计比较';}
function manualFields(f,name='likes',value='0'){commonFields(f);f.d.querySelector('#fb-manual-url').value='https://analytics.x.com/content';f.d.querySelector('.fb-metric-name').value=name;f.d.querySelector('.fb-metric-value').value=value;}

test('no registered publications has a clear empty state and no automatic writes or model calls',async()=>{
  const f=await fixture({bindings:[]});assert(!f.d.querySelector('#fb-empty-bindings').hidden);assert(f.d.querySelector('#fb-new').disabled);assert.match(f.d.querySelector('#fb-list').textContent,/尚未导入/);assert(f.calls.every(c=>c.method==='GET'));assert(!f.calls.some(c=>/assistant|completions|generation|sync/.test(c.url)));f.close();
});
test('platform observations remain visible without a work binding and preserve raw values and source',async()=>{
  const o={id:ident,platform:'x',title:'合成资料 <img>',account:'demo',post_url:'https://x.com/demo/status/123',source_url:'https://x.com/demo/status/123/analytics',observed_at:'2026-10-04T12:00:00+08:00',notice:'版本待关联，时间区间未提供',raw_sha256:hash,metrics:[{scope:'Video',label:'Views',value:'1.2K'}],raw:'Views\n1.2K\n<script>data()</script>',knowledge_id:null};
  const f=await fixture({bindings:[],onRequest:call=>call.url==='/api/feedback/observations'?{observations:[o],total:1}:call.url==='/api/feedback/observation/'+ident?o:undefined});
  assert(!f.d.querySelector('#fb-observations').hidden);assert(f.d.querySelector('#fb-new').disabled);assert.match(f.d.querySelector('#fb-observation-list').textContent,/1.2K/);assert.equal(f.d.querySelectorAll('#fb-observation-list img').length,0);assert(f.calls.every(c=>c.method==='GET'));
  f.d.querySelector('[data-observation-raw]').click();await flush();assert.equal(f.d.querySelector('.fb-observation-raw textarea').value,o.raw);assert.equal(f.d.querySelectorAll('#fb-observation-list script').length,0);
  f.d.querySelector('[data-observation-knowledge]').click();await flush();assert(f.calls.some(c=>c.url==='/api/feedback/observation/'+ident+'/knowledge'&&c.method==='POST'));assert(!f.d.querySelector('.fb-observation-knowledge-link').hidden);f.close();
});
test('explicit import binds fixed delivery version, preserves zero, and does not auto-export to knowledge',async()=>{
  const f=await fixture();f.d.querySelector('#fb-new').click();manualFields(f);assert.match(f.d.querySelector('#fb-binding-info').textContent,/v0001/);assert.equal(f.calls.filter(c=>c.method==='POST').length,0);
  submit(f);await flush();const call=f.calls.find(c=>c.url==='/api/feedback/import');assert.equal(call.body.delivery_id,ident);assert.equal(call.body.version,'v0001');assert.deepEqual(call.body.metrics,{likes:'0'});assert.equal(call.body.range.label,'原始区间口径，不与累计比较');assert.equal(call.headers['X-Article-Studio'],'1');assert(!f.calls.some(c=>c.url.endsWith('/knowledge')));assert(!f.d.querySelector('#fb-dialog').open);assert.match(f.d.querySelector('#fb-detail').textContent,/未向平台校验/);f.close();
});
test('CSV is read locally and only sends exact file and selected-row parameters after confirmation',async()=>{
  const f=await fixture();f.d.querySelector('#fb-new').click();commonFields(f);f.d.querySelector('#fb-source-kind').value='csv';f.d.querySelector('#fb-source-kind').dispatchEvent(new f.w.Event('change'));
  const raw='Post permalink,impressions\r\nhttps://x.com/test/status/123,"1,200"\r\n',file={name:'原始统计.csv',size:Buffer.byteLength(raw),text:async()=>raw};Object.defineProperty(f.d.querySelector('#fb-csv-file'),'files',{value:[file]});f.d.querySelector('#fb-csv-file').dispatchEvent(new f.w.Event('change'));await flush();
  assert.equal(f.calls.filter(c=>c.method==='POST').length,0);assert.match(f.d.querySelector('#fb-csv-preview').textContent,/Post permalink/);f.d.querySelector('#fb-csv-url-column').value='Post permalink';f.d.querySelector('#fb-csv-columns').value='impressions';submit(f);await flush();const saved=f.calls.find(c=>c.url==='/api/feedback/import').body;assert(!('metrics'in saved));assert.equal(saved.provenance.text,raw);assert.equal(saved.provenance.row,1);assert.deepEqual(saved.provenance.metric_columns,['impressions']);assert.equal(saved.provenance.filename,'原始统计.csv');f.close();
});
test('untrusted titles, metric values, source notes and original CSV render as text',async()=>{
  const item=snapshot({binding:{...binding,title:'<img src=x onerror=bad()>'},metrics:{'<svg>':'<script>bad()</script>'},provenance:{type:'csv',filename:'<img>.csv',url:'javascript:bad()',text:'<script>bad()</script>\r\n',row:1,line_start:2,line_end:2,metric_columns:['<img>'],sha256:hash}});
  const f=await fixture({snapshots:[item]});f.d.querySelector('[data-snapshot]').click();await flush();assert.equal(f.d.querySelectorAll('#fb-list img,#fb-detail img,#fb-detail svg,#fb-detail script').length,0);assert.match(f.d.querySelector('#fb-detail').textContent,/<script>bad\(\)<\/script>/);assert.equal(f.d.querySelector('#fb-raw-csv').value,item.provenance.text.replace(/\r\n?/g,'\n'));assert(!Array.from(f.d.querySelectorAll('#fb-detail a')).some(a=>/^(javascript|data):/.test(a.href)));
  let downloaded;f.w.Blob=class{constructor(parts){downloaded=parts.join('');}};f.w.URL.createObjectURL=()=> 'blob:synthetic';f.w.URL.revokeObjectURL=()=>{};f.w.HTMLAnchorElement.prototype.click=function(){};f.d.querySelector('#fb-download-csv').click();assert.equal(downloaded,item.provenance.text);f.close();
});
test('knowledge export occurs only on user click and retains a version-hash citation',async()=>{
  const f=await fixture({snapshots:[snapshot()]});f.d.querySelector('[data-snapshot]').click();await flush();assert(f.calls.every(c=>c.method==='GET'));f.d.querySelector('#fb-knowledge').click();await flush();const call=f.calls.find(c=>c.url.endsWith('/knowledge'));assert.equal(call.method,'POST');assert.deepEqual(call.body,{});assert.equal(call.headers['X-Article-Studio'],'1');assert(!f.d.querySelector('#fb-knowledge-link').hidden);assert.equal(new URL(f.d.querySelector('#fb-knowledge-link').href).searchParams.get('hash'),hash);assert.match(f.d.querySelector('#fb-status').textContent,/尚未生成模型复盘/);f.close();
});
test('server rejection keeps the original form and does not claim import succeeded',async()=>{
  const f=await fixture({onRequest:call=>call.url==='/api/feedback/import'?{httpError:400,error:'作品版本与发布项不一致'}:undefined});f.d.querySelector('#fb-new').click();manualFields(f);submit(f);await flush();assert(f.d.querySelector('#fb-dialog').open);assert.equal(f.d.querySelector('.fb-metric-value').value,'0');assert.match(f.d.querySelector('#fb-form-status').textContent,/版本与发布项不一致/);assert(!f.d.querySelector('#fb-import').disabled);assert(!f.calls.some(c=>c.url.endsWith('/knowledge')));f.close();
});
test('prototype-shaped metric names remain original data and duplicate import clicks write once',async()=>{
  let release;const pending=new Promise(resolve=>release=resolve);const f=await fixture({onRequest:async call=>{if(call.url==='/api/feedback/import'){await pending;return snapshot({metrics:call.body.metrics});}}});f.d.querySelector('#fb-new').click();manualFields(f,'__proto__','0');submit(f);submit(f);await tick();assert.equal(f.calls.filter(c=>c.url==='/api/feedback/import').length,1);assert.deepEqual(f.calls.find(c=>c.url==='/api/feedback/import').body.metrics,JSON.parse('{"__proto__":"0"}'));release();await flush();f.close();
});

test('observation association offers only the same original post and sends its fixed version explicitly',async()=>{
  const o={id:ident,platform:'x',title:'合成采集',account:'test',post_url:'https://x.com/test/status/123',source_url:'https://x.com/test/status/123/analytics',observed_at:'2026-10-04T12:00:00+08:00',notice:'版本待关联',raw_sha256:hash,metrics:[{scope:'Post',label:'Views',value:'1.2K'}],raw:'Views\n1.2K',version_binding:null};
  const other={...binding,delivery_id:'d'.repeat(32),url:'https://x.com/test/status/456'},linked={...o,id:snapId,version_binding:binding,knowledge_id:'e'.repeat(32),notice:'统计窗口待核对'};let associated=false;
  const f=await fixture({bindings:[other,binding],onRequest:call=>{
    if(call.url==='/api/feedback/observations')return {observations:associated?[o,linked]:[o],total:associated?2:1};
    if(call.url==='/api/feedback/observation/'+ident+'/bind'){associated=true;return linked;}
    if(call.url==='/api/feedback/observation/'+snapId)return linked;
  }});
  const select=f.d.querySelector('[data-observation-binding]');assert.equal(select.options.length,1);assert.equal(select.value,binding.slug+':'+binding.delivery_id);assert(f.calls.every(c=>c.method==='GET'));
  f.d.querySelector('[data-observation-bind]').click();await flush();
  const call=f.calls.find(c=>c.url.endsWith('/bind'));assert.deepEqual(call.body,{slug:binding.slug,delivery_id:binding.delivery_id,version:binding.version});assert.equal(call.headers['X-Article-Studio'],'1');
  const card=f.d.getElementById('observation-'+snapId);assert.match(card.textContent,/已关联 v0001/);assert.equal(card.querySelector('[data-observation-bind]'),null);assert.equal(card.querySelector('textarea').value,o.raw);assert.equal(f.d.querySelectorAll('.fb-observation').length,2);assert(!f.calls.some(c=>/assistant|completions/.test(c.url)));f.close();
});
test('a retained historical observation opens by ID even when it falls outside the latest list',async()=>{
  const o={id:ident,platform:'x',title:'合成历史资料',account:'test',post_url:binding.url,source_url:binding.url+'/analytics',observed_at:'2026-10-04T12:00:00+08:00',notice:'版本待关联',raw_sha256:hash,metrics:[{scope:'Post',label:'Views',value:'1.2K'}],raw:'Views\n1.2K'};
  const f=await fixture({bindings:[],url:'http://127.0.0.1:8766/feedback.html?observation='+ident,onRequest:call=>call.url==='/api/feedback/observation/'+ident?o:undefined});assert(!f.d.querySelector('#fb-observations').hidden);assert.equal(f.d.querySelector('textarea').value,o.raw);assert(f.calls.every(c=>c.method==='GET'));f.close();
});

test('deal entry selects its exact published binding and opens an unsent statistics form',async()=>{
  const f=await fixture({url:'http://127.0.0.1:8766/feedback.html?binding='+encodeURIComponent(binding.slug+':'+binding.delivery_id)+'&new=1'});
  assert.equal(f.d.querySelector('#fb-filter').value,binding.slug+':'+binding.delivery_id);
  assert.equal(f.d.querySelector('#fb-binding').value,binding.slug+':'+binding.delivery_id);
  assert.equal(f.d.querySelector('#fb-dialog').open,true);assert.match(f.d.querySelector('#fb-binding-info').textContent,/v0001/);
  assert.equal(f.calls.filter(c=>c.method==='POST').length,0);f.close();
});
