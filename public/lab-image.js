/* References belong to the image draft and survive model changes and history refill. */
let imageReferences=[],imageReferenceEpoch=0;
function imageParameters(){
 if(imageReferences.some(item=>item.pending||item.error))throw new Error('参考图仍在上传或上传失败，请稍候或移除后重试');
 const limit=$('#lab-model').value==='qi21'?4:1;
 if(imageReferences.length>limit)throw new Error(`当前模型最多使用 ${limit} 张参考图，请移除多余图片`);
 return {references:{images:imageReferences.map(item=>item.id)},denoise:Number($('#lab-image-denoise').value),reference_media:Object.fromEntries(imageReferences.map(item=>[item.id,item]))};
}
function configureImageReferences(settings={}){
 $('#lab-image-controls').hidden=lab.kind!=='image';if(lab.kind!=='image')return;
 if(Object.hasOwn(settings,'references')){
  imageReferenceEpoch++;
  imageReferences=(settings.references?.images||[]).map(id=>({id,...settings.reference_media?.[id],url:'/generation-references/'+encodeURIComponent(id)}));
 }
 $('#lab-image-denoise').value=settings.denoise??$('#lab-image-denoise').value??0.7;
 renderImageReferences();
}
function renderImageReferences(){
 const krea=$('#lab-model').value==='krea2';
 $('#lab-image-note').textContent=krea?'可上传或粘贴 1 张图片，结合描述重绘；降低重绘强度可保留更多原图。':'可上传或粘贴最多 4 张参考图，结合描述编辑；第一张图片决定画面构图，尺寸按所选输出尺寸调整。';
 $('#lab-image-denoise-label').hidden=!krea;
 const host=$('#lab-image-previews');host.replaceChildren();
 imageReferences.forEach((item,index)=>{
  const tile=document.createElement('div');tile.className='video-reference-tile';
  if(item.url){const img=document.createElement('img');img.src=item.url;img.alt='参考图 '+(index+1);tile.append(img);}
  const name=document.createElement('small');name.textContent=item.error|| (item.pending?'正在上传…':item.name||'参考图 '+(index+1));tile.append(name);
  const remove=document.createElement('button');remove.type='button';remove.className='button subtle';remove.textContent='移除';remove.onclick=()=>{imageReferences=imageReferences.filter(row=>row!==item);renderImageReferences();rememberLab();};tile.append(remove);host.append(tile);
 });
 lab.uploading=imageReferences.filter(item=>item.pending).length;updateGenerateState();
}
async function addImageReferences(files){
 const epoch=imageReferenceEpoch,limit=$('#lab-model').value==='qi21'?4:1;
 if(files.length+imageReferences.length>limit){toast(`当前模型最多使用 ${limit} 张参考图`);return;}
 for(const file of files){
  if(epoch!==imageReferenceEpoch)break;
  const item={name:file.name||'粘贴的图片',pending:true};imageReferences.push(item);renderImageReferences();
  try{
   if(!['image/png','image/jpeg','image/webp'].includes(file.type)||file.size>10000000)throw new Error('请选择不超过 10 MB 的 PNG、JPEG 或 WebP 图片');
   const data=await new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(reader.result);reader.onerror=()=>reject(new Error('图片读取失败'));reader.readAsDataURL(file);});
   const result=await api('/api/generation/references',{data,name:item.name});
   if(epoch===imageReferenceEpoch&&imageReferences.includes(item))Object.assign(item,result);
  }catch(e){item.error=e.message;}
  finally{item.pending=false;if(epoch===imageReferenceEpoch){renderImageReferences();rememberLab();}}
 }
 }
$('#lab-image-upload').onchange=event=>{const files=[...event.target.files];event.target.value='';addImageReferences(files);};
$('#lab-form').addEventListener('paste',event=>{
 if(lab.kind!=='image')return;
 const files=[...(event.clipboardData?.items||[])].filter(item=>item.kind==='file'&&item.type.startsWith('image/')).map(item=>item.getAsFile()).filter(Boolean);
 if(files.length){event.preventDefault();addImageReferences(files);}
});
