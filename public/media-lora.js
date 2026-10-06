/* Shared adapter controls for the chat composer and generation dialog. */
function rawLoraParameters(prefix){
  return {lora:$('#'+prefix+'-lora').value,lora_strength:Number($('#'+prefix+'-lora-strength').value||0.8),lora_trigger:$('#'+prefix+'-lora-trigger').value};
}
function renderLoraFields(prefix,models,modelId,settings=rawLoraParameters(prefix)){
  const model=models.find(m=>m.id===modelId),choices=model?.loras||[];
  $('#'+prefix+'-lora-fields').hidden=modelId!=='krea2';
  const select=$('#'+prefix+'-lora');
  select.innerHTML='<option value="">不使用 LoRA</option>'+choices.map(l=>`<option value="${escapeHTML(l.id)}">${escapeHTML(l.name)}</option>`).join('');
  select.value=choices.some(l=>l.id===settings.lora)?settings.lora:'';
  $('#'+prefix+'-lora-strength').value=settings.lora_strength??0.8;
  $('#'+prefix+'-lora-trigger').value=settings.lora_trigger||'';
  updateLoraEnabled(prefix);
}
function updateLoraEnabled(prefix){
  const disabled=!$('#'+prefix+'-lora').value;
  $('#'+prefix+'-lora-strength').disabled=disabled;
  $('#'+prefix+'-lora-trigger').disabled=disabled;
}
function selectedLoraParameters(prefix,modelId){
  return modelId==='krea2'?rawLoraParameters(prefix):{};
}
