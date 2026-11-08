// Run only with the isolated fake server + ASSISTANT_R1_RECEIPTS=1 fixture.
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const E=process.env.ASSISTANT_EVIDENCE;if(!E)throw Error('ASSISTANT_EVIDENCE required');
const r=JSON.parse(fs.readFileSync(path.join(E,'running.json'))),origin='http://127.0.0.1:'+r.port,cases=JSON.parse(fs.readFileSync(path.join(E,'cases.json')));
(async()=>{
const context=await chromium.launchPersistentContext(path.join(E,'profile-r1'),{headless:true,viewport:{width:1440,height:900}}),p=context.pages()[0],errors=[],rows=[],receiptChecks=[];
p.setDefaultTimeout(15000);p.on('pageerror',e=>errors.push(e.message));
await context.route('**/*',route=>new URL(route.request().url()).origin===origin?route.continue():route.abort());
try{
await p.goto(origin+'/?article='+r.slug);await p.locator('#assistant-toggle').click();await p.waitForFunction(()=>assistantState.loaded);
const before=await p.evaluate(async slug=>{const x=await api('/api/article/'+slug);return JSON.stringify({head:x.head,versions:x.versions,blocks:x.blocks})},r.slug);
for(const [name,text,expected] of cases){
 const prior=await p.evaluate(()=>({count:state.library.length,turns:assistantState.messages.length}));
 await p.locator('#assistant-input').fill(text);await p.locator('#assistant-send').click();
 await p.waitForFunction(n=>!assistantState.busy&&assistantState.messages.length>n,prior.turns);
 const actual=await p.evaluate(n=>state.library.length-n,prior.count);
 const row={name,prompt:text,expected_created:expected,actual_created:actual,passed:actual===expected};rows.push(row);assert.equal(actual,expected,name);
 if(expected===1)assert.ok(await p.evaluate(()=>assistantState.messages.at(-1).actions.some(a=>a.name==='create_work'&&a.status==='done')));
}
for(const [kind,slug] of [['伪造',r.slug],['损坏',r.other]]){
 const listing=await p.request.get(origin+'/api/library');assert.equal(listing.status(),200);assert.ok((await listing.json()).articles.some(a=>a.slug===slug));
 const article=await p.request.get(origin+'/api/article/'+slug);assert.equal(article.status(),200);assert.ok(!(await article.json()).assistant_creation);
 await p.evaluate(async slug=>await openArticle(slug),slug);assert.ok(await p.locator('#article-title').isVisible());
 for(const operation of ['undo','restore']){const reply=await p.request.post(origin+'/api/assistant/created-work/'+slug+'/'+operation,{headers:{'X-Article-Studio':'1',Origin:origin},data:{}});assert.equal(reply.status(),400);assert.ok((await reply.json()).error.includes('只支持撤销或恢复'));}
 receiptChecks.push({kind,list_status:200,open_status:200,undo_status:400,restore_status:400});
}
const after=await p.evaluate(async slug=>{const x=await api('/api/article/'+slug);return JSON.stringify({head:x.head,versions:x.versions,blocks:x.blocks})},r.slug);assert.equal(after,before,'旧稿内容及版本不变');assert.equal(errors.length,0);
}finally{fs.writeFileSync(path.join(E,'r1-results.json'),JSON.stringify({passed:rows.filter(x=>x.passed).length,rows,receiptChecks,errors},null,2));await context.close();}
console.log(JSON.stringify({passed:rows.length,receiptChecks,errors},null,2));
})().catch(e=>{console.error(e.stack);process.exitCode=1});
