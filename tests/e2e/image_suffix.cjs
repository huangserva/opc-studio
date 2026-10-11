'use strict';
const assert=require('node:assert/strict'),crypto=require('node:crypto');
const PNG=Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=','base64');
const sha256=bytes=>crypto.createHash('sha256').update(bytes).digest('hex');
module.exports=async function({test,api,until,base,fs,path,evidence,ROOT}){
 const fixed=fs.existsSync(path.join(ROOT,'coordination/fixes-a2-report.md'));
 await test('14-组合图片按真实格式存盘',async p=>{
  const observations=[];
  for(const input of [
   {name:'noext',kind:'link',text:'https://example.com/e2e-suffix-noext'},
   {name:'fake.jpg',kind:'text',text:'E2E后缀回归：这张附件的字节是PNG，文件名故意写成fake.jpg。'}
  ]){
   const before=(await api('/api/intake')).items.map(item=>item.id);
   await p.goto(base+'/intake.html');
   await p.locator('#in-input').fill(input.text);
   await p.locator('#in-reason').fill('E2E真实图片格式与后缀回归');
   await p.locator('#in-file').setInputFiles({name:input.name,mimeType:'image/png',buffer:PNG});
   const submitted=p.waitForResponse(r=>new URL(r.url()).pathname==='/api/intake/drop'&&r.request().method()==='POST');
   await p.locator('#in-submit').click();
   const response=await submitted,saved=await response.json();
   assert.equal(response.status(),201,JSON.stringify(saved));
   assert.equal(saved.kind,input.kind);
   assert.equal(saved.file.name,input.name);
   assert.equal(saved.file.format,'PNG');
   assert(!before.includes(saved.id));
   await until(async()=>{
    const row=await api('/api/intake/'+saved.id);
    return row.status==='resolved'&&row.recognition?.status==='succeeded'&&row.analysis?.status==='done';
   });
   const fileUrl=base+'/api/intake/'+saved.id+'/file';
   const reply=await p.request.get(fileUrl),bytes=await reply.body();
   assert.equal(reply.status(),200,'附件必须能回读；不能把初始化或文件丢失记作后缀已知缺陷');
   await p.locator('#in-refresh').click();
   await p.locator('[data-id="'+saved.id+'"]').waitFor();
   // 产品回收列表没有内建图片预览；在实际条目内加解码探针，src直连真实文件接口，不替换响应。
   const image=await p.evaluate(async({id,url})=>{
    const row=document.querySelector('[data-id="'+id+'"]');
    const label=document.createElement('p');label.textContent='E2E图片解码探针（真实附件接口）';row.append(label);
    const img=document.createElement('img');img.alt='E2E '+id;img.style.width='96px';img.style.height='96px';row.append(img);
    return new Promise(resolve=>{
     let done=false;
     const finish=event=>{if(done)return;done=true;clearTimeout(timer);resolve({event,naturalWidth:img.naturalWidth,naturalHeight:img.naturalHeight,complete:img.complete});};
     const timer=setTimeout(()=>finish('timeout'),5000);
     img.onload=()=>finish('load');img.onerror=()=>finish('error');img.src=url;
    });
   },{id:saved.id,url:fileUrl});
   const contentType=reply.headers()['content-type']||'';
   observations.push({name:input.name,kind:input.kind,drop_id:saved.id,file_path:saved.file_path,
    fileStatus:reply.status(),contentType,expectedSha256:sha256(PNG),actualSha256:sha256(bytes),
    bytesUnchanged:bytes.equals(PNG),image,valid:contentType==='image/png'&&bytes.equals(PNG)&&image.naturalWidth>0});
   await p.screenshot({path:path.join(evidence,'14-'+input.name+'-实际图片响应.png'),fullPage:true});
  }
  const record={mode:fixed?'regression':'known_defect_reproduction',fixReportPresent:fixed,
   observations,defectReproduced:observations.some(item=>!item.valid),
   note:'两条均经真实Chrome表单收下；图片探针在测试DOM内添加，不代表产品已有预览功能。'};
  fs.writeFileSync(path.join(evidence,'14-组合图片真实格式实际行为.json'),JSON.stringify(record,null,2));
  if(fixed){
   for(const item of observations){
    assert.equal(item.contentType,'image/png',item.name+' 的响应类型未按真实PNG格式');
    assert.equal(item.actualSha256,item.expectedSha256,item.name+' 的图片字节改变');
    assert(item.image.naturalWidth>0,item.name+' 在页面中无法解码');
   }
   return;
  }
  return {status:'known_defect_reproduction',reason:'fixes-a2-report.md不存在；真实记录后缀缺陷，不计正式通过',
   defectReproduced:record.defectReproduced,observations};
 });
};
