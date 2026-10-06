const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
function fixture({profile,menu,probeAccount,delayed=false,editorMissing=false}={}){
 let listener,clock=0,reads=0,verificationCalls=0,writes=0;
 const element={href:'https://x.com/'+profile,innerText:menu,getClientRects:()=>[{}]};
 const document={readyState:'complete',visibilityState:'visible',title:'X',body:{innerText:''},querySelectorAll:selector=>{
   if(selector==='a[data-testid="AppTabBar_Profile_Link"]'){reads++;return profile&&(!delayed||reads>=3)?[element]:[];}
   if(selector==='[data-testid="SideNav_AccountSwitcher_Button"]')return menu?[element]:[];
   if(selector.includes('button')){writes++;return [];}
   return [];
 }};
 const chrome={runtime:{onMessage:{addListener:fn=>listener=fn},sendMessage:async msg=>{if(msg.command==='verify-account')verificationCalls++;return {account:probeAccount,ok:true};}}};
 const context={document,chrome,crypto:{randomUUID:()=> 'document-1'},URL,Set,Date:{now:()=>clock},Promise,Error,setTimeout:(fn,ms)=>{clock+=ms;Promise.resolve().then(fn);},location:{pathname:'/compose/articles/edit/123/focus',origin:'https://x.com',href:'https://x.com/compose/articles/edit/123/focus'}};
 vm.runInNewContext(fs.readFileSync('publishing/local-extension/x.js','utf8'),context);
 return {send:msg=>new Promise(resolve=>listener(msg,{},resolve)),verificationCalls:()=>verificationCalls,writes:()=>writes};
}
const bundle={job:{status:'publish_requested',id:'a'.repeat(32),account:'demo_account',draft_url:'https://x.com/compose/articles/edit/123/focus'}};
test('account wait tolerates delayed X sidebar render',async()=>{
 const f=fixture({profile:'demo_account',delayed:true});assert.equal((await f.send({command:'account'})).account,'demo_account');
});
test('account switcher identifies visible handle without profile link',async()=>{
 const f=fixture({menu:'Demo User\n@demo_account'});assert.equal((await f.send({command:'account'})).account,'demo_account');
});
test('conflicting visible account signals block operation',async()=>{
 const f=fixture({profile:'other',menu:'@demo_account'});assert.equal((await f.send({command:'account'})).code,'ACCOUNT_AMBIGUOUS');
});
test('focused page uses actual probe account and refuses mismatched target',async()=>{
 const f=fixture({probeAccount:'other'}),r=await f.send({command:'publish',bundle});
 assert.equal(r.code,'ACCOUNT_MISMATCH');assert.equal(f.verificationCalls(),1);assert.equal(f.writes(),0);
});
test('unavailable editor gets a distinct draft error after bounded wait',async()=>{
 const f=fixture({probeAccount:'demo_account'}),r=await f.send({command:'prepare',bundle});
 assert.equal(r.code,'DRAFT_UNAVAILABLE');assert.equal(f.verificationCalls(),2);assert.equal(f.writes(),0);
});
