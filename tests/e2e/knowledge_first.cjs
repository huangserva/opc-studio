'use strict';
const assert=require('node:assert/strict');
module.exports=async function({test,api,until,base,fs,path,evidence,ROOT}){
 const fixed=fs.existsSync(path.join(ROOT,'coordination/review6-fixes-a-report.md'));
 await test('11-知识库先收藏后删除回收项',async p=>{
  const url='https://example.com/e2e-knowledge-first';
  const title='E2E独立收藏灯塔笔记',readerNote='E2E先在知识库收藏，评论与回收项独立保留';
  const original=await api('/api/knowledge/reference',{url,title,reader_note:readerNote});
  assert.equal(original.title,title);assert.equal(original.provenance.reader_note,readerNote);
  const query='/api/knowledge/search?q='+encodeURIComponent(title);
  const beforeSearch=(await api(query)).results.filter(x=>x.id===original.id);assert.equal(beforeSearch.length,1);
  await p.goto(base+'/intake.html');await p.locator('#in-input').fill(url);await p.locator('#in-reason').fill('E2E回收站再次收藏');await p.locator('#in-submit').click();
  const item=await until(async()=> (await api('/api/intake')).items.find(x=>x.url===url&&x.document_id));
  assert.equal(item.document_id,original.id);assert.equal((await api('/api/knowledge/document/'+original.id)).title,title);
  await p.locator('#in-refresh').click();const button=p.locator('[data-id="'+item.id+'"] [data-delete]');await button.waitFor();
  const response=p.waitForResponse(r=>new URL(r.url()).pathname==='/api/intake/'+item.id+'/delete');
  const dialog=p.waitForEvent('dialog');const clicked=button.click();const d=await dialog;const message=d.message();assert.equal(d.type(),'confirm');await d.accept();await clicked;
  const reply=await response;assert.equal(reply.status(),200);const receipt=await reply.json();assert.equal(receipt.deleted,true);
  await p.locator('[data-id="'+item.id+'"]').waitFor({state:'detached'});
  const documentReply=await p.request.get(base+'/api/knowledge/document/'+original.id);
  const afterDocument=await documentReply.json();const afterSearch=(await api(query)).results.filter(x=>x.id===original.id);
  const retained=documentReply.ok(),searchUnchanged=JSON.stringify(afterSearch)===JSON.stringify(beforeSearch);
  // 保存确认框原文，区分“只删回收项/不删正文”和承诺会删知识库正文的旧文案。
  const deletionClause=(message.match(/会一起删掉：[\s\S]*?(?:。|\n)/)||[''])[0];
  const threatensDocument=/知识库.*正文/.test(deletionClause)&&!/(不会|不删|保留)/.test(deletionClause);
  const record={mode:fixed?'regression':'known_defect_reproduction',fixReportPresent:fixed,url,document_id:original.id,drop_id:item.id,
   original,dialog:message,receipt,documentResponseStatus:documentReply.status(),afterDocument,beforeSearch,afterSearch,
   documentRetained:retained,searchUnchanged,dialogThreatensDocument:threatensDocument,
   defectReproduced:!retained||!searchUnchanged||threatensDocument};
  fs.writeFileSync(path.join(evidence,'11-知识库先收藏实际行为.json'),JSON.stringify(record,null,2));
  await p.goto(base+'/knowledge.html');await p.locator('#kb-query').fill(title);
  const searched=p.waitForResponse(r=>new URL(r.url()).pathname==='/api/knowledge/search');await p.locator('#kb-search-form button').click();await searched;
  await until(async()=> (await p.locator('[data-document="'+original.id+'"]').count())===(retained?1:0));
  if(fixed){assert(retained,'知识库独立收藏被删除');assert.equal(afterDocument.title,title);assert.equal(afterDocument.provenance.reader_note,readerNote);assert.deepEqual(afterSearch,beforeSearch);assert(!threatensDocument,'确认框错误宣称删除独立收藏正文');return;}
  return {status:'known_defect_reproduction',reason:'修复报告不存在，按合同只记实证，不计通过或失败',defectReproduced:record.defectReproduced,documentRetained:retained};
 });
};
