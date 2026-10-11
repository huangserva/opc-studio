'use strict';
const assert=require('node:assert/strict');
module.exports=async function({test,api,drop,until,base,httpCalls,fixture,fs,path,evidence}){
 const screenshot=(p,name)=>p.screenshot({path:path.join(evidence,name+'.png'),fullPage:true});
 async function dialogClick(p,button,accept){const dialog=p.waitForEvent('dialog');const clicked=button.click();const d=await dialog;const message=d.message();assert.equal(d.type(),'confirm');await (accept?d.accept():d.dismiss());await clicked;return message}
 const ids=p=>p.locator('.cd-card').evaluateAll(xs=>xs.map(x=>x.dataset.id).sort());
 const cards=async()=> (await api('/api/cards')).items;
 const snapshot=async()=>({drops:(await api('/api/intake')).items,cards:await cards()});
 async function choose(p,selector){const response=p.waitForResponse(r=>new URL(r.url()).pathname==='/api/cards');await p.locator(selector).click();assert.equal((await response).status(),200);await until(async()=>{const data=await api(new URL((await response).url()).pathname+new URL((await response).url()).search);return JSON.stringify(await ids(p))===JSON.stringify(data.items.map(x=>x.id).sort())});}
 await test('09-回收项取消删除与共用正文',async p=>{
  const url='https://example.com/e2e-shared-delete';const first=await drop(p,url);
  // 第二次收下同一链接，必须验证真实共用 document_id，不能用相似正文代替。
  await p.locator('#in-input').fill(url);await p.locator('#in-submit').click();
  const second=await until(async()=> (await api('/api/intake')).items.find(x=>x.url===url&&x.id!==first.id&&x.analysis?.status==='done'));
  assert.equal(first.document_id,second.document_id);await p.locator('#in-refresh').click();
  const row=p.locator('[data-id="'+first.id+'"]');await row.waitFor();
  const before=await snapshot(),posts=httpCalls.filter(x=>x.method==='POST'&&x.path.endsWith('/delete')).length;
  const message=await dialogClick(p,row.locator('[data-delete]'),false);
  for(const text of ['回收项','即时分析','知识库','最后一条','不会删正文','提示词卡不删','不能撤销'])assert(message.includes(text),message);
  await p.waitForFunction(()=>document.querySelector('#in-status').textContent.includes('未删除'));
  assert.deepEqual(await snapshot(),before);assert.equal(httpCalls.filter(x=>x.method==='POST'&&x.path.endsWith('/delete')).length,posts);
  const document=await api('/api/knowledge/document/'+first.document_id);
  const search=await api('/api/knowledge/search?q='+encodeURIComponent('E2E灯塔'));const body=search.results.find(x=>x.id===first.document_id);assert(body);
  const response=p.waitForResponse(r=>new URL(r.url()).pathname==='/api/intake/'+first.id+'/delete');
  await dialogClick(p,row.locator('[data-delete]'),true);const receipt=await (await response).json();
  assert.equal(receipt.deleted,true);assert.equal(receipt.document_removed,false);assert(receipt.document_shared_by>=1);
  await row.waitFor({state:'detached'});await p.reload();assert.equal(await row.count(),0);
  assert((await api('/api/intake')).items.some(x=>x.id===second.id));
  assert.deepEqual((await api('/api/knowledge/search?q='+encodeURIComponent('E2E灯塔'))).results.find(x=>x.id===first.document_id),body);
  assert.deepEqual(await api('/api/knowledge/document/'+first.document_id),document);
  await screenshot(p,'09-共用正文删除后刷新');
  fs.writeFileSync(path.join(evidence,'09-删除回执.json'),JSON.stringify({dialog:message,receipt,cancelPosts:0},null,2));
 });
 await test('10-卡片分组筛选删除与清理',async p=>{
  await p.goto(base+'/cards.html');await p.locator('.cd-group').first().waitFor();
  for(const f of fixture){const group=p.locator('[data-drop="'+f.drop_id+'"]');assert.equal(await group.locator('.cd-card').count(),3);assert.match(await group.locator('.cd-group-summary').innerText(),/E2E.*四块分析摘要/)}
  await screenshot(p,'10-按回收项');
  await choose(p,'[data-view="time"]');assert.equal(await p.locator('.cd-group').count(),0);
  const times=await p.locator('.cd-card').evaluateAll(xs=>xs.map(x=>x.dataset.id));const expected=(await cards()).map(x=>x.id);assert.deepEqual(times,expected);
  await screenshot(p,'10-按时间');await choose(p,'[data-view="source"]');
  for(const status of ['candidate','confirmed','rejected']){await choose(p,'#cd-views [data-status="'+status+'"]');const actual=await ids(p);for(const f of fixture)assert(actual.includes(f.cards.find(c=>c.status===status).id));assert((await api('/api/cards?status='+status)).items.every(c=>c.status===status));}
  await choose(p,'#cd-views [data-status=""]');
  for(const channel of ['link','text','image','video','intel']){await choose(p,'[data-kind="'+channel+'"]');const actual=await ids(p),f=fixture.find(x=>x.channel===channel);for(const c of f.cards)assert(actual.includes(c.id));const filtered=(await api('/api/cards?kind='+channel)).items;assert(filtered.every(c=>c.source.channel===channel));for(const other of fixture.filter(x=>x.channel!==channel))for(const c of other.cards)assert(!actual.includes(c.id));}
  await screenshot(p,'10-情报筛选');await choose(p,'[data-kind=""]');
  const f=fixture.find(x=>x.channel==='text'),card=f.cards[0].id;const before=await snapshot();const document=await api('/api/knowledge/document/'+f.document_id);const countPosts=()=>httpCalls.filter(x=>x.method==='POST'&&x.path.startsWith('/api/cards/')).length;
  let posts=countPosts();const message=await dialogClick(p,p.locator('[data-id="'+card+'"] [data-action="delete"]'),false);assert.match(message,/只删除这张卡片.*不改回收项、知识库或其他卡片/);await p.waitForFunction(()=>document.querySelector('#cd-status').textContent==='已取消。');assert.deepEqual(await snapshot(),before);assert.equal(countPosts(),posts);
  await dialogClick(p,p.locator('[data-id="'+card+'"] [data-action="delete"]'),true);await p.locator('[data-id="'+card+'"]').waitFor({state:'detached'});
  assert.deepEqual((await cards()).map(c=>c.id).sort(),before.cards.filter(c=>c.id!==card).map(c=>c.id).sort());assert.deepEqual((await api('/api/intake')).items,before.drops);assert.deepEqual(await api('/api/knowledge/document/'+f.document_id),document);
  const sourceBefore=await api('/api/intake/'+f.drop_id);assert.equal(sourceBefore.document_id,f.document_id);
  await p.reload();await p.locator('.cd-group').first().waitFor();assert.equal(await p.locator('[data-id="'+card+'"]').count(),0);
  // 来源删除也用真实回收站确认框；已有两张卡须保留后才能执行组清理。
  await p.goto(base+'/intake.html');await p.locator('[data-id="'+f.drop_id+'"] [data-delete]').waitFor();await dialogClick(p,p.locator('[data-id="'+f.drop_id+'"] [data-delete]'),true);await p.locator('[data-id="'+f.drop_id+'"]').waitFor({state:'detached'});
  await p.goto(base+'/cards.html');const group=p.locator('[data-drop="'+f.drop_id+'"]');await group.waitFor();assert.equal(await group.locator('.cd-card').count(),2);assert.equal(await group.locator('h2').innerText(),'来源已删除');await screenshot(p,'10-来源已删除');
  const beforeCleanup=await snapshot();posts=countPosts();const cleanupMessage=await dialogClick(p,group.locator('[data-action="cleanup"]'),false);assert.match(cleanupMessage,/只删除这组来源已删除的卡片.*不改其他数据/);await p.waitForFunction(()=>document.querySelector('#cd-status').textContent==='已取消。');assert.deepEqual(await snapshot(),beforeCleanup);assert.equal(countPosts(),posts);
  await dialogClick(p,group.locator('[data-action="cleanup"]'),true);await group.waitFor({state:'detached'});await p.reload();await p.locator('.cd-group').first().waitFor();assert.equal(await group.count(),0);
  assert.deepEqual((await cards()).map(c=>c.id).sort(),beforeCleanup.cards.filter(c=>c.drop_id!==f.drop_id).map(c=>c.id).sort());assert.deepEqual((await api('/api/intake')).items,beforeCleanup.drops);
  for(const other of fixture.filter(x=>x.drop_id!==f.drop_id))assert.equal((await api('/api/intake/'+other.drop_id)).document_id,other.document_id);
  await screenshot(p,'10-来源清理后');
 });
};
