'use strict';
const assert = require('node:assert/strict');
const {execFileSync} = require('node:child_process');
module.exports = async function ({test,api,until,base,fs,path,evidence,ROOT,workspace}) {
 const record=(name,value)=>fs.writeFileSync(path.join(evidence,name+'.json'),JSON.stringify(value,null,2));
 const list=async()=> (await api('/api/intake')).items;
 async function clear(){const rows=await list();for(let n=0;n<rows.length;n+=50)await api('/api/intake/delete-many',{ids:rows.slice(n,n+50).map(x=>x.id)});assert.equal((await list()).length,0);}
 async function text(label,resolve=false){const item=await api('/api/intake/drop',{kind:'text',text:label+'：黄昏灯塔旁的水面倒影复制天空，低机位取景，人物留背影，绳索在风里轻轻晃动。',reason:label});if(!resolve)return item;await api('/api/intake/'+item.id+'/resolve',{});return until(async()=>{const row=await api('/api/intake/'+item.id);return row.analysis?.status==='done'&&row});}
 async function link(url,allowFailure=false){const item=await api('/api/intake/drop',{kind:'link',url,reason:'E2E批量链接'});await api('/api/intake/'+item.id+'/resolve',{});return until(async()=>{const row=await api('/api/intake/'+item.id);return (row.analysis?.status==='done'||(allowFailure&&row.analysis?.status==='failed'))&&row});}
 async function open(p){await p.goto(base+'/intake.html');await p.locator('#in-all').waitFor();}
 async function choose(p,ids){for(const id of ids)await p.locator('[data-check="'+id+'"]').check();}
 async function confirm(p,accept=true){const dialog=p.waitForEvent('dialog');const clicked=p.locator('#in-batch-delete').click();const d=await dialog;const message=d.message();assert.equal(d.type(),'confirm');await (accept?d.accept():d.dismiss());await clicked;return message;}
 async function remove(p){const response=p.waitForResponse(r=>new URL(r.url()).pathname==='/api/intake/delete-many');const dialog=await confirm(p);const reply=await response;assert.equal(reply.status(),200);const receipt=await reply.json();await until(async()=> (await p.locator('#in-status').innerText()).includes('已删除'));return {dialog,receipt,status:await p.locator('#in-status').innerText()};}
 await test('15-多选取消与批量删除',async p=>{
  await clear();const a=await text('E2E15甲独立正文',true),b=await link('https://example.com/e2e-batch15'),c=await text('E2E15丙独立正文',true),survivor=await text('E2E15保留行');
  const ids=[a.id,b.id,c.id],cards=(await api('/api/cards')).items.map(x=>x.id).sort();
  await open(p);await choose(p,ids);assert.match(await p.locator('#in-batch-count').innerText(),/已选 3 条/);
  const posts=[];p.on('request',r=>{if(r.method()==='POST'&&new URL(r.url()).pathname==='/api/intake/delete-many')posts.push(r.postDataJSON());});
  const cancel=await confirm(p,false);assert.equal(posts.length,0);assert.deepEqual((await list()).map(x=>x.id).sort(),[...ids,survivor.id].sort());
  const out=await remove(p);assert.match(out.dialog,/3 条回收项/);assert.match(out.dialog,/文字 2/);assert.match(out.dialog,/链接 1/);assert.match(out.dialog,/移除 3 篇正文/);assert.match(out.status,/已删除 3 条/);assert.deepEqual(out.receipt.deleted.map(x=>x.id).sort(),ids.sort());
  await p.reload();for(const id of ids)assert.equal(await p.locator('[data-check="'+id+'"]').count(),0);assert.equal(await p.locator('[data-id="'+survivor.id+'"] [data-delete]').count(),1);
  for(const row of [a,b,c]){assert.equal((await p.request.get(base+'/api/knowledge/document/'+row.document_id)).status(),400);const found=(await api('/api/knowledge/search?q='+encodeURIComponent(row===b?'E2E灯塔':row.reason))).results;assert(!found.some(x=>x.id===row.document_id));}
  assert.deepEqual((await api('/api/cards')).items.map(x=>x.id).sort(),cards);record('15-取消与删除实际行为',{cancel,cancelDeletePosts:0,...out,ids,remaining:await list()});
  // 补充只读复核所需的隔离实证：共享正文按篇预览，逐条删除后是否仍按篇汇总。
  const sharedA=await link('https://example.com/e2e-batch-shared'),sharedB=await link('https://example.com/e2e-batch-shared');assert.equal(sharedA.document_id,sharedB.document_id);
  const one=await api('/api/intake/delete-preview',{ids:[sharedA.id]});assert.equal(one.documents.kept_shared,1);
  const both=await api('/api/intake/delete-preview',{ids:[sharedA.id,sharedB.id]});assert.equal(both.documents.would_remove,1);
  const sharedReceipt=await api('/api/intake/delete-many',{ids:[sharedA.id,sharedB.id]});assert.equal(sharedReceipt.deleted.length,2);assert.equal(sharedReceipt.summary.documents_removed,1);assert.equal(sharedReceipt.summary.documents_kept,0);
  assert.equal(sharedReceipt.summary.cards_kept,both.cards.cards);assert.equal(sharedReceipt.summary.cards_kept_drops,2);
  const afterSharedStatus=(await p.request.get(base+'/api/knowledge/document/'+sharedA.document_id)).status();assert.equal(afterSharedStatus,400);
  record('复核-共享正文汇总',{document_id:sharedA.document_id,one,both,sharedReceipt,afterSharedStatus,
    duplicateKeptCount:sharedReceipt.summary.documents_kept!==0});

 });
 await test('16-类型时间筛选仅删可见选择',async p=>{
  await clear();const today=await text('E2E16今天'),old=await text('E2E16八天前'),other=await api('/api/intake/drop',{kind:'link',url:'https://example.com/e2e-batch16'});
  // 仅在套件临时库修改测试记录时间；不访问正式库。
  execFileSync(path.resolve(ROOT,'../article-importer/.venv/bin/python'),[path.join(__dirname,'batch_fixture.py'),workspace,old.id]);
  const image=await p.request.post(base+'/api/intake/drop?kind=image&name=batch16.png',{headers:{Origin:base,'X-Article-Studio':'1'},data:Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=','base64')});assert.equal(image.status(),201);const pic=await image.json();
  await open(p);await p.locator('[data-kind="text"]').click();await p.locator('#in-all').check();assert.match(await p.locator('#in-batch-count').innerText(),/已选 2 条/);
  await p.locator('[data-kind="link"]').click();assert.equal(await p.locator('[data-check="'+other.id+'"]').isChecked(),false);assert.match(await p.locator('#in-batch-note').innerText(),/不在当前筛选/);
  await p.locator('[data-kind="text"]').click();await p.locator('#in-range').selectOption('1');await p.locator('#in-all').check();assert.match(await p.locator('#in-batch-count').innerText(),/已选 1 条/);
  const out=await remove(p);assert.deepEqual(out.receipt.deleted.map(x=>x.id),[today.id]);await p.reload();await p.locator('#in-range').selectOption('');await p.locator('[data-kind=""]').click();assert.deepEqual((await list()).map(x=>x.id).sort(),[old.id,other.id,pic.id].sort());record('16-筛选实际行为',{...out,old:old.id,link:other.id,image:pic.id,remaining:await list()});
 });
 await test('17-五十一条拒绝与五十条删除',async p=>{
  await clear();const rows=[];for(let n=0;n<51;n++)rows.push(await text('E2E17-'+n));await open(p);await p.locator('#in-all').check();assert.match(await p.locator('#in-batch-count').innerText(),/已选 51 条/);
  const posts=[];p.on('request',r=>{if(r.method()==='POST'&&/\/api\/intake\/delete-(preview|many)$/.test(new URL(r.url()).pathname))posts.push(r.url());});
  await p.locator('#in-batch-delete').click();assert.match(await p.locator('#in-status').innerText(),/一次最多删 50 条.*51/);assert.equal(posts.length,0);await p.reload();assert.equal((await list()).length,51);
  for(const endpoint of ['delete-preview','delete-many']){const denied=await p.request.post(base+'/api/intake/'+endpoint,{headers:{Origin:'https://untrusted.example','X-Article-Studio':'1'},data:{ids:[rows[0].id]}});assert.equal(denied.status(),403);assert.equal((await list()).length,51);}
  const direct=await p.request.post(base+'/api/intake/delete-many',{headers:{Origin:base,'X-Article-Studio':'1'},data:{ids:rows.map(x=>x.id)}});assert.equal(direct.status(),400);const rejected=await direct.json();assert.match(rejected.error,/1–50/);assert.equal((await list()).length,51);
  await choose(p,rows.slice(0,50).map(x=>x.id));const out=await remove(p);assert.equal(out.receipt.deleted.length,50);await p.reload();assert.deepEqual((await list()).map(x=>x.id),[rows[50].id]);record('17-上限实际行为',{...out,overLimitRequests:0,directStatus:direct.status(),rejected,remaining:await list()});
 });
 await test('18-预览后并发删除部分失败如实显示',async p=>{
  await clear();const rows=[await text('E2E18甲'),await text('E2E18乙'),await text('E2E18丙')];await open(p);await choose(p,rows.map(x=>x.id));
  await api('/api/intake/'+rows[2].id+'/delete',{});const out=await remove(p);assert.deepEqual(out.receipt.deleted.map(x=>x.id).sort(),rows.slice(0,2).map(x=>x.id).sort());assert.equal(out.receipt.skipped.length,1);assert.equal(out.receipt.skipped[0].id,rows[2].id);assert.match(out.receipt.skipped[0].reason,/不存在/);assert.match(out.status,/已删除 2 条/);assert.match(out.status,/1 条未删/);assert(out.status.includes(rows[2].id.slice(0,8)));assert.match(out.status,/不存在/);await p.locator('#in-batch-clear').click();assert.match(await p.locator('#in-status').innerText(),/已取消选择/);await p.reload();assert.equal((await list()).length,0);await p.reload();assert.equal((await list()).length,0);record('18-部分失败实际行为',out);
 });
 await test('19-批量删除保留知识优先收藏与提示词卡',async p=>{
  await clear();const url='https://example.com/e2e-batch19',title='E2E19独立收藏灯塔',reader_note='E2E19独立评论逐字保留';const original=await api('/api/knowledge/reference',{url,title,reader_note});const kept=await link(url,true),owned=await text('E2E19回收站自有正文',true);assert.equal(kept.document_id,original.id);
  const cards=(await api('/api/cards')).items.filter(x=>[kept.id,owned.id].includes(x.drop_id));assert(cards.length>0,'必须真实生成卡片，不能跳过来源删除验证');await open(p);await choose(p,[kept.id,owned.id]);const out=await remove(p);assert.match(out.dialog,/移除 1 篇正文/);assert.match(out.dialog,/1 篇不是回收站存的.*保留不删/);await p.reload();assert.equal((await list()).length,0);
  const retained=await api('/api/knowledge/document/'+original.id);assert.equal(retained.title,title);assert.equal(retained.provenance.reader_note,reader_note);assert.deepEqual(retained,original);assert((await api('/api/knowledge/search?q='+encodeURIComponent(title))).results.some(x=>x.id===original.id));assert(!(await api('/api/knowledge/search?q='+encodeURIComponent(owned.reason))).results.some(x=>x.id===owned.document_id));
  await p.goto(base+'/knowledge.html');await p.locator('#kb-query').fill(title);const searched=p.waitForResponse(r=>new URL(r.url()).pathname==='/api/knowledge/search');await p.locator('#kb-search-form button').click();await searched;await p.locator('[data-document="'+original.id+'"]').waitFor();await p.screenshot({path:path.join(evidence,'19-知识优先检索.png'),fullPage:true});
  await p.goto(base+'/cards.html');for(const card of cards){const row=p.locator('[data-id="'+card.id+'"]');await row.waitFor();assert.match(await row.innerText(),/来源已删除/);assert.equal(await row.locator('a[href*="intake.html"]').count(),0);}record('19-知识优先实际行为',{...out,original,retained,cards});
 });
};
