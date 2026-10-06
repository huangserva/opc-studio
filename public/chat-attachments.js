/* Paste, drop, or choose images; only uploaded IDs enter chat requests. */
const chatAttachments={items:[]};
function renderAttachmentDraft(){
  const box=$('#chat-attachment-draft');box.replaceChildren();box.hidden=!chatAttachments.items.length;
  for(const item of chatAttachments.items){
    const tile=document.createElement('div');tile.className='chat-attachment-tile';
    const img=document.createElement('img');img.src=item.meta?.url||item.preview;img.alt=item.name;img.title='点击查看完整图片';img.onclick=()=>zoom(img.src);tile.append(img);
    const status=document.createElement('span');status.textContent=item.error?'上传失败':item.meta?'待发送':'上传中…';tile.append(status);
    const remove=document.createElement('button');remove.type='button';remove.textContent='×';remove.setAttribute('aria-label','移除图片 '+item.name);
    remove.onclick=()=>{if(assistantState.busy)return;item.removed=true;URL.revokeObjectURL(item.preview);chatAttachments.items=chatAttachments.items.filter(x=>x!==item);renderAttachmentDraft();};tile.append(remove);
    if(item.error)tile.title=item.error;box.append(tile);
  }
}
async function addChatImages(files){
  if(assistantState.busy){toast('请等当前消息发送完成后再添加图片');return;}
  for(const file of files){
    if(chatAttachments.items.length>=4){toast('每条消息最多附加 4 张图片');break;}
    if(!['image/png','image/jpeg','image/webp'].includes(file.type)){toast('支持 PNG、JPEG 和 WebP 图片');continue;}
    if(file.size>10*1024*1024){toast('每张图片最多 10 MB');continue;}
    const item={name:file.name||'粘贴的图片',preview:URL.createObjectURL(file),meta:null,error:null,removed:false};
    chatAttachments.items.push(item);renderAttachmentDraft();
    item.promise=(async()=>{
      try{
        const data=await new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(String(reader.result).split(',')[1]);reader.onerror=()=>reject(new Error('读取图片失败'));reader.readAsDataURL(file);});
        const meta=await api('/api/assistant/attachments',{name:item.name,data});
        if(!item.removed)item.meta=meta;
      }catch(e){item.error=e.message;toast('图片上传失败：'+e.message);}
      finally{renderAttachmentDraft();}
    })();
  }
}
async function readyChatAttachments(){
  await Promise.all(chatAttachments.items.map(item=>item.promise));
  if(chatAttachments.items.some(item=>!item.meta))throw new Error('有图片上传失败，请移除后重新添加');
  return chatAttachments.items.map(item=>item.meta.id);
}
function clearChatAttachments(){
  for(const item of chatAttachments.items){item.removed=true;URL.revokeObjectURL(item.preview);}
  chatAttachments.items=[];renderAttachmentDraft();
}
function attachmentMarkup(items=[]){
  return items.length?'<div class="chat-sent-images">'+items.filter(item=>/^[a-f0-9]{32}$/.test(item.id)).map(item=>`<a href="/chat-attachments/${item.id}.png" target="_blank" rel="noopener" title="点击查看图片"><img src="/chat-attachments/${item.id}.png" alt="${escapeHTML(item.name||'聊天图片')}" loading="lazy"></a>`).join('')+'</div>':'';
}
$('#chat-attach-button').onclick=()=>$('#chat-attach-input').click();
$('#chat-attach-input').onchange=event=>{addChatImages([...event.target.files]);event.target.value='';};
$('#assistant-input').addEventListener('paste',event=>{
  const files=[...(event.clipboardData?.items||[])].filter(i=>i.kind==='file'&&i.type.startsWith('image/')).map(i=>i.getAsFile()).filter(Boolean);
  if(files.length){event.preventDefault();addChatImages(files);}
});
const attachmentForm=$('#assistant-form');
attachmentForm.addEventListener('dragover',event=>{if([...event.dataTransfer.types].includes('Files')){event.preventDefault();attachmentForm.classList.add('attachment-drop');}});
attachmentForm.addEventListener('dragleave',event=>{if(!attachmentForm.contains(event.relatedTarget))attachmentForm.classList.remove('attachment-drop');});
attachmentForm.addEventListener('drop',event=>{event.preventDefault();attachmentForm.classList.remove('attachment-drop');addChatImages([...event.dataTransfer.files]);});
