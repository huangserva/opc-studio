/* Shared preset and bounded custom dimensions; payload always contains actual WxH. */
function renderMediaSize(prefix,model,size){
  const select=$('#'+prefix+'-size');
  size=size||select.value;
  const option=s=>`<option value="${escapeHTML(s)}">${escapeHTML(model?.size_labels?.[s]||s)}</option>`;
  select.innerHTML=model?.size_groups?model.size_groups.map(g=>`<optgroup label="${escapeHTML(g.name)}">${g.sizes.map(option).join('')}</optgroup>`).join(''):(model?.sizes||[]).map(option).join('');
  if(model?.custom_size)select.innerHTML+='<option value="custom">自定义宽高…</option>';
  const custom=model?.custom_size&&/^\d+x\d+$/.test(size||'')&&!model.sizes.includes(size);
  select.value=model?.sizes.includes(size)?size:custom?'custom':model?.sizes[0]||'';
  if(custom){const [width,height]=size.split('x');$('#'+prefix+'-width').value=width;$('#'+prefix+'-height').value=height;}
  updateCustomSize(prefix);
}
function updateCustomSize(prefix){
  const custom=$('#'+prefix+'-size').value==='custom';
  $('#'+prefix+'-custom-size').hidden=!custom;
  for(const axis of ['width','height'])$('#'+prefix+'-'+axis).disabled=!custom;
}
function selectedMediaSize(prefix){
  const size=$('#'+prefix+'-size').value;
  if(size!=='custom')return size;
  const dimensions=['width','height'].map(axis=>Number($('#'+prefix+'-'+axis).value));
  if(dimensions.some(n=>!Number.isInteger(n)||n<512||n>2048||n%16))throw new Error('自定义宽高须为 512–2048 像素，且为 16 的倍数');
  return dimensions.join('x');
}
