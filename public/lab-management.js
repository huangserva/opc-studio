function labHistoryActions(j){
 const inactive=['done','error'].includes(j.status),id=escapeHTML(j.id);
 return `<div class="lab-card-actions"><label><input type="checkbox" data-select="${id}" ${lab.selection.has(j.id)?'checked':''} ${inactive?'':'disabled'} aria-label="选择记录 ${j.id.slice(0,8)}">选择</label><button class="button subtle" type="button" data-refill="${id}">沿用参数</button>${j.status==='done'?`<a class="button subtle" href="${escapeHTML(j.asset_url)}" download="生成结果-${j.id.slice(0,8)}.${j.kind==='image'?'png':'mp4'}">下载</a>`:''}<button class="button lab-danger" type="button" data-manage="${id}" ${inactive?'':'disabled title="生成完成后可删除"'}>${lab.trash?'恢复':'删除'}</button></div>`;
}
function clearLabSelectionPreview(){
 lab.selected=null;lab.previewKey=null;$('#lab-progress').hidden=true;$('#lab-result-actions').hidden=true;$('#lab-job-details').hidden=true;$('#lab-result-model').textContent='';$('#lab-preview video')?.pause();$('#lab-preview').replaceChildren();const p=document.createElement('p');p.className='lab-muted';p.textContent='选择一条记录查看结果，或开始新的生成。';$('#lab-preview').append(p);
}
function updateLabSelection(){
 const eligible=lab.jobs.filter(j=>['done','error'].includes(j.status)&&[j.prompt,j.model_name,j.settings.lora_name||''].join(' ').toLowerCase().includes($('#lab-history-search').value.trim().toLowerCase()));
 $('#lab-selection-count').textContent=`已选 ${lab.selection.size} 项`;$('#lab-batch-manage').disabled=!lab.selection.size;$('#lab-batch-manage').textContent=lab.trash?'恢复所选':'删除所选';
 $('#lab-select-all').checked=eligible.length>0&&eligible.every(j=>lab.selection.has(j.id));$('#lab-select-all').indeterminate=eligible.some(j=>lab.selection.has(j.id))&&!$('#lab-select-all').checked;
 $('#lab-trash-toggle').textContent=lab.trash?'← 返回生成记录':'回收站';
}
function bindLabManagement(list){
 list.querySelectorAll('[data-select]').forEach(e=>e.onchange=()=>{if(e.checked)lab.selection.add(e.dataset.select);else lab.selection.delete(e.dataset.select);updateLabSelection();});
 list.querySelectorAll('[data-refill]').forEach(e=>e.onclick=()=>{const j=lab.jobs.find(j=>j.id===e.dataset.refill);if(j)fillLab(j);});
 list.querySelectorAll('[data-manage]').forEach(e=>e.onclick=()=>manageLabJobs([e.dataset.manage],lab.trash?'restore':'delete'));
 updateLabSelection();
}
async function performLabManage(ids,action){
 try{await api('/api/generation/manage',{ids,action});for(const id of ids){lab.selection.delete(id);lab.compare.delete(id);}lab.historySignature=null;await refreshLab();renderLabCompare();toast(action==='delete'?'已移入回收站，可随时恢复':'已恢复生成记录');}
 catch(e){toast(e.message);}
}
function manageLabJobs(ids,action){
 if(action==='restore')return performLabManage(ids,action);
 lab.pendingDelete=ids;$('#lab-delete-message').textContent=`将删除 ${ids.length} 条生成记录。`;$('#lab-delete-dialog').showModal();
}
$('#lab-delete-cancel').onclick=()=>{$('#lab-delete-dialog').close();lab.pendingDelete=null;};
$('#lab-delete-confirm').onclick=async()=>{const ids=lab.pendingDelete;if(!ids)return;lab.pendingDelete=null;$('#lab-delete-dialog').close();await performLabManage(ids,'delete');};
$('#lab-delete-result').onclick=()=>{if(lab.selected)manageLabJobs([lab.selected.id],lab.selected.deleted_at?'restore':'delete');};
$('#lab-batch-manage').onclick=()=>manageLabJobs([...lab.selection],lab.trash?'restore':'delete');
$('#lab-trash-toggle').onclick=()=>{lab.trash=!lab.trash;lab.selection.clear();lab.compare.clear();clearLabSelectionPreview();renderLabCompare();lab.historySignature=null;refreshLab();};
$('#lab-select-all').onchange=()=>{for(const j of lab.jobs){if(!['done','error'].includes(j.status))continue;if(![j.prompt,j.model_name,j.settings.lora_name||''].join(' ').toLowerCase().includes($('#lab-history-search').value.trim().toLowerCase()))continue;if($('#lab-select-all').checked)lab.selection.add(j.id);else lab.selection.delete(j.id);}lab.historySignature=null;renderLabHistory();};
