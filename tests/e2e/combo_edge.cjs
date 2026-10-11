'use strict';
const assert=require('node:assert/strict'),crypto=require('node:crypto');
const PNG_A=Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=','base64');
const PNG_B=Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==','base64');
const sha256=buf=>crypto.createHash('sha256').update(buf).digest('hex');
const REFUSAL=/不能改选|不能更换|不能再选|请等上传|上传完成前|上传中不能|暂不能选|先完成这次上传/;
module.exports=async function({test,base,fs,path,evidence,ROOT,sleep}){
 const fixed=fs.existsSync(path.join(ROOT,'coordination/combo-fixes-stage-report.md'));
 const headers={'X-Article-Studio':'1',Origin:base};
 async function stage(name,bytes){
  const r=await fetch(base+'/api/intake/stage?kind=image&name='+encodeURIComponent(name),{method:'POST',headers:{...headers,'Content-Type':'application/octet-stream'},body:bytes});
  const data=await r.json().catch(()=>({}));
  if(!r.ok||!data.token)throw new Error('暂存失败 '+r.status+' '+JSON.stringify(data));
  return data;
 }
 async function submit(payload){
  const r=await fetch(base+'/api/intake/drop',{method:'POST',headers:{...headers,'Content-Type':'application/json'},body:JSON.stringify(payload)});
  return {status:r.status,data:await r.json().catch(()=>({}))};
 }
 async function readFile(id){
  const r=await fetch(base+'/api/intake/'+id+'/file');
  const bytes=Buffer.from(await r.arrayBuffer());
  return {status:r.status,bytes,sha256:r.ok?sha256(bytes):null,error:r.ok?'':bytes.toString('utf8').slice(0,300)};
 }
 await test('12-同id重复提交不毁原附件',async p=>{
  const id=crypto.randomBytes(16).toString('hex');
  const firstStage=await stage('edge-original.png',PNG_A);
  const first=await submit({id,kind:'image',reason:'E2E同编号第一次',file_token:firstStage.token});
  assert.equal(first.status,201,JSON.stringify(first.data));
  assert.equal(first.data.id,id);
  const before=await readFile(id);
  assert.equal(before.status,200,'第一次收下后原图应能回读');
  assert.equal(before.sha256,sha256(PNG_A));
  assert.equal(before.bytes.length,PNG_A.length);
  const secondStage=await stage('edge-original.png',PNG_B);
  assert.notEqual(secondStage.token,firstStage.token);
  assert.notEqual(sha256(PNG_A),sha256(PNG_B));
  const second=await submit({id,kind:'image',reason:'E2E同编号第二次',file_token:secondStage.token});
  const after=await readFile(id);
  const bytesUnchanged=after.status===200&&after.sha256===before.sha256&&after.bytes.equals(PNG_A);
  const record={mode:fixed?'regression':'known_defect_reproduction',fixReportPresent:fixed,drop_id:id,
   originalSha256:sha256(PNG_A),replacementSha256:sha256(PNG_B),firstToken:firstStage.token,secondToken:secondStage.token,
   firstStatus:first.status,secondStatus:second.status,
   secondBody:{id:second.data.id,error:second.data.error||'',file:second.data.file||null,file_path:second.data.file_path||''},
   before:{status:before.status,sha256:before.sha256,bytes:before.bytes.length},
   after:{status:after.status,sha256:after.sha256,bytes:after.bytes.length,error:after.error},
   bytesUnchanged,stillReadable:after.status===200&&after.sha256===sha256(PNG_A),defectReproduced:!bytesUnchanged};
  fs.writeFileSync(path.join(evidence,'12-同id重复提交实际行为.json'),JSON.stringify(record,null,2));
  await p.goto(base+'/intake.html');
  await p.locator('[data-id="'+id+'"]').waitFor();
  if(fixed){
   assert(record.stillReadable,'同 id 再提交后原图不可回读');
   assert(bytesUnchanged,'同 id 再提交后原附件字节变了');
   return;
  }
  return {status:'known_defect_reproduction',reason:'修复报告不存在，按合同只记实证，不计通过或失败',defectReproduced:record.defectReproduced,bytesUnchanged,stillReadable:record.stillReadable,secondStatus:second.status,afterStatus:after.status};
 });
 await test('13-上传中改选文件不丢失',async p=>{
  await p.goto(base+'/intake.html');
  let release=()=>{};
  const hold=new Promise(r=>{release=r;});
  let markSeen=()=>{};
  const seen=new Promise(r=>{markSeen=()=>{if(markSeen.done)return;markSeen.done=true;r();};});
  await p.route(url=>{try{return new URL(url).pathname==='/api/intake/stage'}catch{return false}},async route=>{
   markSeen();
   const pending=route.fetch();
   await hold;
   await route.fulfill({response:await pending});
  });
  await p.locator('#in-file').setInputFiles({name:'edge-a.png',mimeType:'image/png',buffer:PNG_A});
  await p.locator('#in-picked').getByText('edge-a.png').waitFor();
  let during,dropWait;
  try{
   await p.locator('#in-submit').click();
   await Promise.race([seen,sleep(8000).then(()=>{throw new Error('stage 请求没有进入拦截')})]);
   let changeError='';
   try{await p.locator('#in-file').setInputFiles({name:'edge-b.png',mimeType:'image/png',buffer:PNG_B},{timeout:1500})}
   catch(e){if(!await p.locator('#in-file').isDisabled())throw e;changeError=e.message}
   during={picked:await p.locator('#in-picked').innerText(),status:await p.locator('#in-status').innerText(),
    form:await p.locator('#in-form').innerText(),fileDisabled:await p.locator('#in-file').isDisabled(),changeError};
   await p.screenshot({path:path.join(evidence,'13-上传中改选瞬间.png'),fullPage:true});
   dropWait=p.waitForResponse(r=>new URL(r.url()).pathname==='/api/intake/drop'&&r.request().method()==='POST');
  }finally{release()}
  const dropResponse=await dropWait;
  const dropBody=await dropResponse.json();
  assert.equal(dropResponse.status(),201,JSON.stringify(dropBody));
  assert.equal(dropBody.file&&dropBody.file.name,'edge-a.png','飞行中的提交应仍是先选中的 A');
  await sleep(400);
  const after={picked:await p.locator('#in-picked').innerText(),status:await p.locator('#in-status').innerText()};
  const bDuring=during.picked.includes('edge-b.png'),bKept=after.picked.includes('edge-b.png');
  const blocked=!bDuring&&during.fileDisabled&&REFUSAL.test(during.form);
  const acceptable=bKept||blocked;
  const record={mode:fixed?'regression':'known_defect_reproduction',fixReportPresent:fixed,drop_id:dropBody.id,
   submittedName:dropBody.file&&dropBody.file.name,during,after,bDuring,bKept,blocked,acceptable,defectReproduced:!acceptable,
   note:'通过条件：A 提交成功后 B 仍在已选，或上传期间禁止改选并有明确提示。B 被静默清掉为缺陷。'};
  fs.writeFileSync(path.join(evidence,'13-上传中改选实际行为.json'),JSON.stringify(record,null,2));
  if(fixed){
   assert(acceptable,bDuring&&!bKept?'上传期间改选的 B 在 A 成功后被静默清掉':'上传期间既没有留下 B，也没有明确禁止改选');
   return;
  }
  return {status:'known_defect_reproduction',reason:'修复报告不存在，按合同只记实证，不计通过或失败',defectReproduced:!acceptable,bKept,blocked,bDuring,submittedName:record.submittedName};
 });
};
