const writingWorkflow={selected:false};
function selectWritingWorkflow(selected){
  writingWorkflow.selected=selected;
  $('#assistant-research-write').setAttribute('aria-pressed',String(selected));
  $('#chat-writing-workflow').hidden=!selected;
  if(selected)clearMediaTool();
  mediaSendReady();
  $('#assistant-input').placeholder=selected?'输入写作主题、读者和长度要求…':'输入问题，或粘贴、拖入图片…';
}
function workflowCard(workflow){
  if(!workflow)return '';
  const status={pending:'待开始',running:'进行中',done:'完成',error:'失败',cancelled:'已停止',skipped:'未执行',interrupted:'已中断'};
  const result=workflow.result;
  return `<section class="chat-workflow" data-workflow-id="${escapeHTML(workflow.id)}" aria-label="调研写稿工作流"><strong>联网调研 → 写稿 → 审稿</strong><ol>${workflow.stages.map(stage=>`<li data-stage="${escapeHTML(stage.id)}" data-status="${escapeHTML(stage.status)}"><span>${escapeHTML(stage.label)}</span><small>${escapeHTML(status[stage.status]||stage.status)}</small>${stage.error?`<p>${escapeHTML(stage.error)}</p>`:''}</li>`).join('')}</ol>${chatSourceLinks(workflow.sources)}${workflow.error?`<p class="workflow-error">${escapeHTML(workflow.error)}</p>`:''}${result?`<p class="workflow-verdict">${result.verdict==='passed'?'已通过本次模型审稿':'有待核实问题'} · ${escapeHTML(result.title)}</p><details data-workflow-details="${escapeHTML(workflow.id)}"><summary>查看草稿与审稿说明</summary><div class="workflow-draft">${chatLinkedText(result.markdown)}</div><div class="workflow-review"><strong>审稿说明</strong><ul>${[...result.notes,...result.unresolved.map(note=>'待核实：'+note)].map(note=>`<li>${escapeHTML(note)}</li>`).join('')}</ul><small>模型根据本次读取的来源审稿。</small></div></details>${workflow.saved?`<button type="button" class="assistant-version-link" data-assistant-slug="${escapeHTML(workflow.saved.slug)}" data-assistant-version="${escapeHTML(workflow.saved.version)}">查看已保存作品 →</button>`:`<button type="button" class="button workflow-save" data-save-workflow="${escapeHTML(workflow.id)}">保存为新作品</button>`}`:''}</section>`;
}
function bindWorkflowCards(box){
  box.querySelectorAll('[data-save-workflow]').forEach(button=>button.onclick=async()=>{
    button.disabled=true;button.textContent='正在保存…';
    try{
      await api('/api/assistant/workflows/'+button.dataset.saveWorkflow+'/save',{});
      const fresh=await api('/api/assistant');assistantState.messages=fresh.messages;renderAssistant();
      await loadLibrary();toast('草稿已保存为新作品');
    }catch(error){button.disabled=false;button.textContent='保存为新作品';toast(error.message);}
  });
}
$('#assistant-research-write').onclick=()=>{
  if(assistantState.busy)return;
  selectWritingWorkflow(!writingWorkflow.selected);$('#assistant-input').focus();
};
$('#chat-writing-clear').onclick=()=>selectWritingWorkflow(false);
