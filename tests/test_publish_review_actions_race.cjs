const fs=require('node:fs'),assert=require('node:assert/strict'),{chromium}=require(process.env.PLAYWRIGHT_MODULE||'./e2e/node_modules/playwright');
const E=process.env.PUBLISH_ACTIONS_EVIDENCE,label=process.env.RACE_LABEL||'after',cfg=JSON.parse(fs.readFileSync(E+'/port.json')),origin='http://127.0.0.1:'+cfg.port;
(async()=>{const ctx=await chromium.launchPersistentContext(E+'/race-profile-'+label,{headless:true,viewport:{width:1440,height:1000}}),results=[];await ctx.route('**/*',r=>r.request().url().startsWith(origin)?r.continue():r.abort());if(process.env.RACE_MUTATION_ROOT){for(const file of ['/publish.js','/components/publish-review-actions.js'])await ctx.route(origin+file,r=>r.fulfill({contentType:'application/javascript',body:fs.readFileSync(process.env.RACE_MUTATION_ROOT+'/public'+file,'utf8')}));}if(process.env.RACE_MUTATION_GUARD){const original=fs.readFileSync(__dirname+'/../public/publish.js','utf8'),wrong=original.replace('if(!current())return;renderGrok','renderGrok');assert.notEqual(wrong,original);await ctx.route(origin+'/publish.js',r=>r.fulfill({contentType:'application/javascript',body:wrong}));}const api=async(path,body)=>{const r=body===undefined?await ctx.request.get(origin+path):await ctx.request.post(origin+path,{headers:{Origin:origin,'X-Article-Studio':'1'},data:body});assert(r.ok());return r.json();};try{
let list=await api('/api/publish/review');if(!list.items.some(r=>r.slug===cfg.slug))await api('/api/record/'+cfg.slug,{platform:'x',version:'v0001',url:'https://x.com/fixture/status/123'});
const cases=[{switch:true,draft:true,save:true},{switch:true,draft:true,save:false},{switch:true,draft:false,save:true},{switch:false,draft:true,save:true},{switch:false,draft:true,save:false},{switch:true,draft:false,save:false}];
for(let index=0;index<cases.length;index++){
 const c=cases[index],p=await ctx.newPage();p.setDefaultTimeout(10000);let release,paused,completed;const gate=new Promise(r=>release=r),signal=new Promise(r=>paused=r),done=new Promise(r=>completed=r);let hold=false;
 await api('/api/feedback/review/'+cfg.slug+'/notes',{notes:'A 原备注 '+index});await api('/api/feedback/review/'+cfg.other+'/notes',{notes:'B 原备注 '+index});
 await p.route(origin+'/api/publish/review',async r=>{const response=await r.fetch();const data=await response.json();data.items.sort((a,b)=>Number(b.slug===cfg.other)-Number(a.slug===cfg.other));if(hold){if(c.switch)data.notice="STALE_A_RESPONSE_"+index;hold=false;paused();await gate;await r.fulfill({json:data});completed();}else await r.fulfill({json:data});});
 try{
 await p.goto(origin+'/publish.html?article='+cfg.slug);await p.locator('#legacy-publishing > summary').click();await p.getByLabel('复盘备注',{exact:true}).waitFor();
 if(c.draft)await p.getByLabel('复盘备注',{exact:true}).fill('A 未保存草稿 '+index);
 await p.locator('.review-import > summary').click();await p.getByLabel('统计口径说明',{exact:true}).fill('竞态原始指标 '+label+index);await p.getByLabel('统计来源页面',{exact:true}).fill('https://analytics.x.com/content');await p.getByLabel('原始指标名称',{exact:true}).fill('Views');await p.getByLabel('原始指标数值或文字',{exact:true}).fill(String(700+index));hold=true;await p.getByRole('button',{name:'导入指标快照',exact:true}).click();await signal;
 if(c.switch){await p.locator('.published-card[data-slug="'+cfg.other+'"]').click();await p.waitForFunction(()=>document.querySelector('#review-detail h2')?.textContent==='带规范来源字段的发布');await p.getByLabel('复盘备注',{exact:true}).waitFor();}
 release();await done;await p.waitForTimeout(300);
 const target=c.switch?cfg.other:cfg.slug,expected=c.switch?'B 原备注 '+index:c.draft?'A 未保存草稿 '+index:'A 原备注 '+index,actual=await p.getByLabel('复盘备注',{exact:true}).inputValue();
 const staleDiscarded=!c.switch||!(await p.locator('body').textContent()).includes('STALE_A_RESPONSE_'+index);
 let persisted=true;if(c.save){await p.getByRole('button',{name:'保存备注',exact:true}).click();await p.locator('.review-action-status').filter({hasText:'备注已保存在本机'}).waitFor();persisted=(await api('/api/feedback/review/'+target)).note.notes===expected;}
 const inputPreserved=c.switch||await p.getByLabel('原始指标名称',{exact:true}).inputValue()==='Views'&&await p.getByLabel('原始指标数值或文字',{exact:true}).inputValue()===String(700+index);
 const titleCorrect=(await p.locator('#review-detail h2').textContent())===(c.switch?'带规范来源字段的发布':'隔离发布复盘操作验收');
 let returnDraftCorrect=true;
 if(c.switch){await p.locator('.published-card[data-slug="'+cfg.slug+'"]').click();await p.waitForFunction(()=>document.querySelector('#review-detail h2')?.textContent==='隔离发布复盘操作验收');await p.getByLabel('复盘备注',{exact:true}).waitFor();returnDraftCorrect=await p.getByLabel('复盘备注',{exact:true}).inputValue()===(c.draft?'A 未保存草稿 '+index:'A 原备注 '+index)&&await p.getByLabel('原始指标名称',{exact:true}).inputValue()==='Views'&&await p.getByLabel('原始指标数值或文字',{exact:true}).inputValue()===String(700+index);}
 results.push({case:index,...c,titleCorrect,notesCorrect:actual===expected,persistedCorrect:persisted,inputPreserved,returnDraftCorrect,staleDiscarded,passed:titleCorrect&&actual===expected&&persisted&&inputPreserved&&returnDraftCorrect&&staleDiscarded});
 }finally{release();await p.close();}
}
// Successful import followed by a failed GET must not expose either raw path or HTML.
for(const failDetail of [false,true]){
 const p=await ctx.newPage();p.setDefaultTimeout(10000);await p.goto(origin+'/publish.html?article='+cfg.slug);await p.locator('#legacy-publishing > summary').click();await p.getByLabel('复盘备注',{exact:true}).waitFor();await p.locator('.review-import > summary').click();await p.getByLabel('统计口径说明',{exact:true}).fill('刷新错误 '+label+failDetail);await p.getByLabel('统计来源页面',{exact:true}).fill('https://analytics.x.com/content');await p.getByLabel('原始指标名称',{exact:true}).fill('Views');await p.getByLabel('原始指标数值或文字',{exact:true}).fill('901');
 await p.route(origin+'/api/publish/review'+(failDetail?'/'+cfg.slug:''),r=>r.fulfill({status:400,json:{error:'/Users/synthetic/private/file.json <img src=x onerror=alert(1)>'}}));await p.getByRole('button',{name:'导入指标快照',exact:true}).click();await p.waitForTimeout(350);const text=await p.locator('body').textContent();const safe=!text.includes('/Users/synthetic')&&!text.includes('<img src=x onerror');const informed=text.includes('已导入')&&text.includes('刷新失败');results.push({case:failDetail?'detail-get-error':'list-get-error',safe,informed,path_pattern_hits:(text.match(/\/(?:Users|private|tmp|var)\//g)||[]).length,html_pattern_hits:(text.match(/<(?:img|script)\b/g)||[]).length,passed:safe&&informed});await p.close();
}

// Other asynchronous writes retain their immutable A target even when the response is delayed.
for(const action of ['notes','knowledge']){
 const p=await ctx.newPage();p.setDefaultTimeout(10000);await api('/api/feedback/review/'+cfg.slug+'/notes',{notes:'A 写路径原备注'});await api('/api/feedback/review/'+cfg.other+'/notes',{notes:'B 写路径原备注'});
 await p.goto(origin+'/publish.html?article='+cfg.slug);await p.locator('#legacy-publishing > summary').click();await p.getByLabel('复盘备注',{exact:true}).waitFor();
 let release,paused;const gate=new Promise(r=>release=r),signal=new Promise(r=>paused=r);await p.route(origin+'/api/feedback/review/'+cfg.slug+'/'+action,async r=>{const response=await r.fetch();paused();await gate;await r.fulfill({response});});
 if(action==='notes')await p.getByLabel('复盘备注',{exact:true}).fill('A 写路径新备注');await p.getByRole('button',{name:action==='notes'?'保存备注':'存为知识库笔记',exact:true}).click();await signal;await p.locator('.published-card[data-slug="'+cfg.other+'"]').click();await p.waitForFunction(()=>document.querySelector('#review-detail h2')?.textContent==='带规范来源字段的发布');await p.getByLabel('复盘备注',{exact:true}).waitFor();release();await p.waitForTimeout(300);
 const b=await api('/api/feedback/review/'+cfg.other),a=await api('/api/feedback/review/'+cfg.slug);const correctB=await p.getByLabel('复盘备注',{exact:true}).inputValue()==='B 写路径原备注'&&b.note.notes==='B 写路径原备注'&&await p.locator('.review-note-links a').count()===0;
 const correctA=action==='notes'?a.note.notes==='A 写路径新备注':a.note.documents.length>0;results.push({case:'late-'+action,correctB,correctA,passed:correctB&&correctA});await p.close();
}

fs.writeFileSync(E+'/race-'+label+'.json',JSON.stringify({cases:results.length,passed:results.every(r=>r.passed),results},null,2));console.log(JSON.stringify({cases:results.length,passed:results.filter(r=>r.passed).length,failed:results.filter(r=>!r.passed).length}));if(!results.every(r=>r.passed))process.exitCode=1;
}finally{await ctx.close();}})().catch(e=>{console.error(e);process.exitCode=1;});
