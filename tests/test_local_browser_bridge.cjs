const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
function fixture(account,options={}){
  let listener,clock=0,nextTab=20;const writes=[],commands=[],pages=new Map([
    [7,'https://x.com/compose/articles/edit/123/focus'],[8,'https://x.com/compose/articles']
  ]);
  const generations=new Map(),stale=new Map();
  const chrome={runtime:{onMessage:{addListener:fn=>listener=fn}},tabs:{
    query:async()=>[...pages].map(([id,url])=>({id,url})),
    update:async(id)=>({id,url:pages.get(id)}),
    create:async({url})=>{const id=nextTab++;pages.set(id,url);return {id,url};},
    reload:async(tab)=>{commands.push({tab,command:'reload'});generations.set(tab,(generations.get(tab)||0)+1);if(options.staleReload)stale.set(tab,2);},
    sendMessage:async(tab,msg)=>{
      commands.push({tab,command:msg.command});
      if(msg.command==='checkpoint'){writes.push(msg.payload);if(options.missingCheckpoint)return undefined;if(msg.payload.action==='attempt'&&options.rejectAttempt)return {error:'发布许可失效'};return {ok:true};}
      if(msg.command==='ping'){const remaining=stale.get(tab)||0;if(remaining)stale.set(tab,remaining-1);return {ready:true,instance:tab+':'+((generations.get(tab)||0)-(remaining?1:0)),version:options.oldExecutor?'0.1.2':JSON.parse(fs.readFileSync('publishing/local-extension/manifest.json')).version,url:options.unavailableDraft&&pages.get(tab).includes('/edit/')?'https://x.com/compose/articles':pages.get(tab)};}
      if(msg.command==='account'||msg.command==='prepare')assert.equal(stale.get(tab)||0,0,'old document must not receive a task after reload');
      if(msg.command==='account')return {account};
      if(msg.command==='prepare')return {url:'https://x.com/compose/articles/edit/123'};
      if(msg.command==='upload')return {error:'页面拒绝上传',code:options.textMismatch?'DRAFT_CONTENT_MISMATCH':undefined,diagnostics:options.textMismatch?{phase:'text_check',body:{expected_chars:10,observed_chars:9},uploaded_body_images:0}:undefined};
      if(msg.command==='publish')return options.publishError?{error:'结果未核验'}:{url:'https://x.com/demo_account/article/456'};
      throw Error('Unexpected write');
    }
  }};
  const timers=(fn,ms)=>{clock+=ms;Promise.resolve().then(fn);};
  vm.runInNewContext(fs.readFileSync('publishing/local-extension/worker.js','utf8'),{chrome,Set,Map,URL,Date:{now:()=>clock},Promise,Error,setTimeout:timers});
  return {writes,commands,pages,send:(msg,sender={tab:{id:2},url:'http://127.0.0.1:8766/'})=>new Promise(resolve=>listener(msg,sender,resolve))};
}
const bundle=()=>({job:{id:'a'.repeat(32),account:'demo_account',draft_url:'https://x.com/compose/articles/edit/123/focus',status:'queued'}});
test('wrong login is checked on article list and cannot upload or publish',async()=>{
  const f=fixture('other'),r=await f.send({command:'run',payload:bundle()});
  assert.match(r.error,/实际登录/);assert.equal(f.commands.find(x=>x.command==='account').tab,8);
  assert(!f.commands.some(x=>['prepare','publish'].includes(x.command)));
});
test('public publishing requires explicit confirmed state',async()=>{
  const f=fixture('demo_account'),r=await f.send({command:'publish',payload:bundle()});
  assert.match(r.error,/确认发布/);assert(!f.commands.some(x=>x.command==='publish'));assert(!f.writes.some(x=>x.action==='attempt'));
  assert.equal(f.writes.at(-1).status,'blocked');
});
test('reuse draft identity without focus and bind before upload',async()=>{
  const f=fixture('demo_account'),r=await f.send({command:'run',payload:bundle()});
  assert.match(r.error,/拒绝上传/);assert(f.writes.some(x=>x.draft_url==='https://x.com/compose/articles/edit/123'));
  assert.equal(f.pages.get(7),'https://x.com/compose/articles/edit/123/focus');
  assert.equal(f.pages.get(f.commands.find(x=>x.command==='prepare').tab),'https://x.com/compose/articles/edit/123');
  assert.equal(f.writes.at(-1).status,'blocked');
});
test('unavailable bound draft stops without creating another article',async()=>{
  const f=fixture('demo_account',{unavailableDraft:true}),r=await f.send({command:'run',payload:bundle()});
  assert.equal(r.code,'DRAFT_UNAVAILABLE');assert(!f.commands.some(x=>x.command==='prepare'));
  assert.equal(f.writes.at(-1).error_code,'DRAFT_UNAVAILABLE');assert(!f.writes.some(x=>x.draft_url));
});
test('missing checkpoint acknowledgement stops before X writes',async()=>{
  const f=fixture('demo_account',{missingCheckpoint:true}),r=await f.send({command:'run',payload:bundle()});
  assert.equal(r.code,'WORKBENCH_DISCONNECTED');assert(!f.commands.some(x=>x.command==='prepare'));
});
test('confirmed publishing preserves confirmation until attempt is recorded',async()=>{
  const f=fixture('demo_account'),b=bundle();b.job.status='publish_requested';
  const r=await f.send({command:'publish',payload:b});assert.equal(r.ok,true);
  assert(!f.writes.some(x=>x.status==='running'));assert.equal(f.writes[0].action,'attempt');assert.equal(f.writes.at(-1).status,'published');
});
test('denied attempt stays blocked; only an attempted publish can become unknown',async()=>{
  for(const options of [{rejectAttempt:true},{publishError:true}]){
    const f=fixture('demo_account',options),b=bundle();b.job.status='publish_requested';
    await f.send({command:'publish',payload:b});assert.equal(f.writes.at(-1).status,options.rejectAttempt?'blocked':'unknown');
    if(options.rejectAttempt)assert(!f.commands.some(x=>x.command==='publish'));
  }
});
test('focused page cannot request another task account evidence',async()=>{
  const f=fixture('demo_account'),r=await f.send({command:'verify-account',id:'unknown'},{tab:{id:7},url:'https://x.com/compose/articles/edit/123/focus'});
  assert.equal(r.code,'SESSION_MISSING');assert(!f.commands.some(x=>x.command==='account'));
});

test('old page executor cannot run under the new worker',async()=>{
  const f=fixture('demo_account',{oldExecutor:true}),r=await f.send({command:'run',payload:bundle()});
  assert.equal(r.code,'X_EXECUTOR_OUTDATED');assert(!f.commands.some(x=>x.command==='prepare'));
});

test('the actual X text mismatch diagnostics reach the persistent job checkpoint',async()=>{
  const f=fixture('demo_account',{textMismatch:true}),r=await f.send({command:'run',payload:bundle()});
  assert.equal(r.code,'DRAFT_CONTENT_MISMATCH');assert.equal(f.writes.at(-1).diagnostics.phase,'text_check');assert.equal(f.writes.at(-1).diagnostics.uploaded_body_images,0);
});

test('reload readiness requires a new document, not the previous ready executor',async()=>{
 const f=fixture('demo_account',{staleReload:true});await f.send({command:'run',payload:bundle()});
 assert(f.commands.some(x=>x.command==='prepare'));
 assert(f.commands.filter(x=>x.command==='ping').length>=6);
});
