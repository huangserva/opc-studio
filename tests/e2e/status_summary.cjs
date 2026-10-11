'use strict';
const assert=require('node:assert/strict');
module.exports=async({test,api,until,base,fs,path,evidence,sleep,workspace})=>{
 const control=value=>{const tmp=path.join(workspace,'control.tmp');fs.writeFileSync(tmp,JSON.stringify(value));fs.renameSync(tmp,path.join(workspace,'status-summary-control.json'));};
 const record=(name,value)=>fs.writeFileSync(path.join(evidence,name+'.json'),JSON.stringify(value,null,2));
 await test('27-平台角标慢额度与刷新失败',async p=>{
  control({status27:true});
  try {
  const calls=[];p.on('request',r=>{if(new URL(r.url()).pathname==='/api/collect/status')calls.push(Date.now())});
  const started=Date.now(), response=p.waitForResponse(r=>new URL(r.url()).pathname==='/api/collect/platforms');
  await p.goto(base+'/intel.html');await response;const listingMs=Date.now()-started;assert(listingMs<1000,'platforms耗时'+listingMs);
  const card=p.locator('[data-platform="wechat_msg"]');await card.waitFor();assert.match(await card.innerText(),/核对中|待核对/);
  await p.screenshot({path:path.join(evidence,'27-冷启动.png'),fullPage:true});
  await until(async()=>calls.length===4,12000);
  await sleep(2600);assert.equal(calls.length,4,'首次加三次重拉后必须停止');const coldCalls=calls.length;
  assert.match(await card.innerText(),/核对中/);
  control({status27:true,release:true});
  await until(async()=> (await api('/api/collect/status')).platforms.some(r=>r.id==='wechat_msg'&&r.available===true));
  await p.reload();await until(async()=> (await card.innerText()).includes('已接通'));
  await card.click();assert.match(await p.locator('#itl-delegated').innerText(),/E2E27已核实/);
  control({status27:true,release:true,expire:true});
  await p.reload();await until(async()=> (await card.innerText()).includes('已接通（旧结果）'));
  await card.click();const detail=await p.locator('#itl-delegated').innerText();assert.match(detail,/读取状态失败：OSError（显示上次结果）/);assert(!/Invalid Date|undefined|null/.test(detail));
  const state=await api('/api/collect/status'),row=state.platforms.find(r=>r.id==='wechat_msg');assert.equal(row.available,true);assert.equal(row.count,27);assert.deepEqual(row.extra,{busy:false});assert.equal(row.stale,true);
  record('27-观测',{listingMs,coldCalls,detail,row});
  } finally {
   await p.goto('about:blank');control({});
   const cleanup=await api('/api/collect/status');assert.equal(cleanup.e2e27_cleaned,true,'27结束必须注销探针并恢复依赖');record('27-清理',{cleaned:cleanup.e2e27_cleaned});
  }
 });
 await test('28-批量删除保留数未知',async p=>{
  async function create(){const d=await api('/api/intake/drop',{kind:'link',url:'https://example.com/e2e-summary28',reason:'E2E28共享正文'});await api('/api/intake/'+d.id+'/resolve',{});return until(async()=>{const r=await api('/api/intake/'+d.id);return r.analysis?.status==='done'&&r})}
  const a=await create(),b=await create();assert.equal(a.document_id,b.document_id);
  await p.goto(base+'/intake.html');await p.locator('[data-check="'+a.id+'"]').check();control({summary28:true});
  p.once('dialog',d=>d.accept());const pending=p.waitForResponse(r=>new URL(r.url()).pathname==='/api/intake/delete-many');await p.locator('#in-batch-delete').click();const response=await pending;assert.equal(response.status(),200);const receipt=await response.json();assert.equal(receipt.summary.documents_kept,null);assert.deepEqual(receipt.deleted.map(r=>r.id),[a.id]);
  await until(async()=> (await p.locator('#in-status').innerText()).includes('已删除'));const text=await p.locator('#in-status').innerText();assert(!/null|undefined|篇正文按规则保留/.test(text));assert.match(text,/已删除 1 条/);
  await p.reload();await p.locator('[data-check="'+b.id+'"]').waitFor();assert.equal(await p.locator('[data-check="'+a.id+'"]').count(),0);assert.equal(await p.locator('[data-check="'+b.id+'"]').count(),1);assert.equal((await api('/api/knowledge/document/'+b.document_id)).id,b.document_id);
  record('28-观测',{a:a.id,b:b.id,receipt,text});control({});
 });
};
