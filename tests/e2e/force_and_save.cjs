'use strict';
const assert=require('node:assert/strict');
const {setScenario}=require('./force_and_save_model.cjs');
module.exports=async function({test,api,until,base,fs,path,evidence,sleep,modelCalls,holdVision}){
 const save=(name,value)=>fs.writeFileSync(path.join(evidence,name+'.json'),JSON.stringify(value,null,2));
 await test('25-已有等待线程现在就分析',async p=>{
  const gate=holdVision();
  const requests=[];p.on('request',r=>{if(r.method()==='POST')requests.push(new URL(r.url()).pathname)});
  try{
   await p.goto(base+'/intake.html');
   await p.locator('#in-reason').fill('E2E25等待线程强制分析');
   await p.locator('#in-file').setInputFiles({name:'e2e-force-25.png',mimeType:'image/png',buffer:Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=','base64')});
   const start=modelCalls.length;
   await p.locator('#in-submit').click();
   const item=await until(async()=>(await api('/api/intake')).items.find(x=>x.file?.name==='e2e-force-25.png'));
   await until(async()=>{const d=await api('/api/intake/'+item.id);return gate.started&&d.recognition?.status==='running'&&d.analysis?.error==='等识别完成再分析'});
   await p.locator('#in-refresh').click();
   const row=p.locator('[data-id="'+item.id+'"]'),button=row.locator('[data-analyze-now]');
   await button.waitFor();assert.match(await row.innerText(),/等识别完成再分析/);
   await p.screenshot({path:path.join(evidence,'25-等待识别.png'),fullPage:true});
   let cancelled='';p.once('dialog',async d=>{cancelled=d.message();await d.dismiss()});
   await button.click();await sleep(200);
   const route='/api/intake/'+item.id+'/analyze';
   assert.equal(requests.filter(x=>x===route).length,0);
   assert.match(cancelled,/不会用到.*图片描述/);
   assert.equal((await api('/api/intake/'+item.id)).analysis.error,'等识别完成再分析');
   p.once('dialog',d=>d.accept());await button.click();
   await until(async()=>{const d=await api('/api/intake/'+item.id);return d.analysis?.error!=='等识别完成再分析'&&['queued','running','done'].includes(d.analysis?.status)});
   const done=await until(async()=>{const d=await api('/api/intake/'+item.id);return d.analysis?.status==='done'&&d});
   assert.equal(done.recognition.status,'running');
   const count=()=>modelCalls.slice(start).filter(x=>x.kind==='analysis').length;
   assert.equal(count(),1);assert.equal(requests.filter(x=>x===route).length,1);
   await p.locator('#in-refresh').click();assert.equal(await row.locator('[data-analyze-now]').count(),0);
   gate.release();
   await until(async()=>{const d=await api('/api/intake/'+item.id);return d.recognition?.status==='succeeded'&&d.analysis?.status==='done'&&count()>=2});
   await sleep(700);assert.equal(count(),2);  // 材料更新只需一次补跑。
   await p.locator('#in-refresh').click();
   save('25-观测',{id:item.id,cancelled,analyzePosts:requests.filter(x=>x===route),analysisBeforeRelease:1,analysisAfterRelease:count(),final:await api('/api/intake/'+item.id)});
  }finally{gate.release()}
 });
 const article=slug=>api('/api/article/'+slug);
 async function create(p,title){
  await p.goto(base+'/');await p.locator('#new-article-top').click();await p.locator('#new-dialog').waitFor({state:'visible'});
  await p.locator('#new-title').fill(title);
  const response=p.waitForResponse(r=>new URL(r.url()).pathname==='/api/create'&&r.request().method()==='POST');
  await p.locator('#create-article').click();const data=await(await response).json();
  await p.locator('#article-title',{hasText:title}).waitFor();await p.locator('#cancel-edit').click();
  await until(async()=>(await p.locator('#save-state').innerText()).includes('已保存'));return data.slug;
 }
 async function send(p,message,visible){
  if(await p.locator('#assistant-panel').isHidden())await p.locator('#assistant-toggle').click();
  await p.locator('#assistant-new-chat').click();await until(async()=>{const s=await api('/api/assistant');return !s.active_job&&s.messages.length===0});
  await p.locator('#assistant-input').fill(message);await p.locator('#assistant-send').click();
  const turn=await until(async()=>{const s=await api('/api/assistant');const last=s.messages.at(-1);return !s.active_job&&last&&!['running','stopping'].includes(last.status)&&last},90000);
  await until(async()=>(await p.locator('#assistant-message-list').innerText()).includes(visible));return api('/api/assistant/job/'+turn.id);
 }
 await test('26-原文空白与跨作品停止保存口径',async p=>{
  const original='把下面贴文贴上\n \t甲　<&>　\t \n　\n　乙\t丙　 ';
  const slug=await create(p,'E2E26空白');setScenario(slug,{kind:'whitespace'});
  await send(p,original,'已完成本轮测试');await p.reload();
  const white=await article(slug);assert.equal(white.head,'v0002');
  assert.deepEqual(white.blocks.map(b=>b.text),[' \t甲　<&>　\t ','　乙\t丙　 ']);
  await p.screenshot({path:path.join(evidence,'26-空白刷新.png'),fullPage:true});
  const a=await create(p,'E2E26作品A');
  await api('/api/save/'+a,{base_version:'v0001',title:'E2E26作品A',blocks:[{kind:'text',html:'<p>A原正文</p>'}]});
  const b=await create(p,'E2E26作品B');
  // 使用产品作品链接定位A，避免凭查询参数猜路由。
  await p.goto(base+'/');await p.locator('[data-slug="'+a+'"]').first().click();
  await p.locator('#article-title',{hasText:'E2E26作品A'}).waitFor();
  setScenario(a,{kind:'cross',other:b});
  const cross=await send(p,'修改第一段文字，把下面贴文贴上\n甲','停止前已有修改保存成功');
  assert.match(cross.answer,/停止前已有修改保存成功/);assert.doesNotMatch(cross.answer,/停止前没有保存新版本/);
  assert.equal((await article(a)).head,'v0002');assert.equal((await article(b)).head,'v0002');
  assert.equal((await article(b)).blocks[0].text,'B真实新正文');
  assert.deepEqual(cross.changed,[b]);assert.equal(cross.actions.filter(x=>x.name==='edit_work').length,2);
  await p.screenshot({path:path.join(evidence,'26-跨作品停止.png'),fullPage:true});
  setScenario(a,{kind:'noop'});
  const noop=await send(p,'修改第一段文字，把下面贴文贴上\n甲','停止前没有保存新版本');
  assert.match(noop.answer,/停止前没有保存新版本/);assert.doesNotMatch(noop.answer,/停止前已有修改保存成功/);
  assert.equal((await article(a)).head,'v0002');assert.deepEqual(noop.changed,[]);
  assert.equal(noop.actions.filter(x=>x.name==='edit_work').length,2);
  save('26-观测',{white,cross,noop,a:await article(a),b:await article(b)});
 });
};
