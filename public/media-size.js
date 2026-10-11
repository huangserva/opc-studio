/* Shared preset + bounded custom dimensions and H3 duration. The payload always carries
   the actual WxH and the actual frame count; every bound below comes from the model
   catalog the backend publishes (generation/models.py / media-api workflows.py), so the
   UI never invents a range and never silently rewrites an out-of-range value. */
const mediaOptionModels={};
function mediaSizeLimits(model){
  const limits=(model&&model.custom_size)||{};
  return {min:Number(limits.min)||512,max:Number(limits.max)||2048,step:Number(limits.step)||16,
          max_area:Number(limits.max_area)||0};
}
// Reference and first/last-frame workflows need 32-aligned pixels; the backend rejects the
// rest, so the option list must drop them instead of letting the user pick a doomed value.
function filteredSizeModel(model){
  if(!model)return model;
  const aligned=size=>String(size).split('x').every(n=>Number(n)%32===0);
  const groups=(model.size_groups||[]).map(group=>({name:group.name,sizes:group.sizes.filter(aligned)}))
    .filter(group=>group.sizes.length);
  return {...model,
          sizes:(model.sizes||[]).filter(aligned),
          ...(model.size_groups?{size_groups:groups.length?groups:null}:{}),
          ...(model.size_labels?{size_labels:Object.fromEntries(Object.entries(model.size_labels).filter(([size])=>aligned(size)))}:{})};
}
function renderMediaSize(prefix,model,size){
  const select=$('#'+prefix+'-size');
  mediaOptionModels[prefix]=model||null;
  size=size||select.value;
  const option=s=>`<option value="${escapeHTML(s)}">${escapeHTML(model?.size_labels?.[s]||s)}</option>`;
  select.innerHTML=model?.size_groups?model.size_groups.map(g=>`<optgroup label="${escapeHTML(g.name)}">${g.sizes.map(option).join('')}</optgroup>`).join(''):(model?.sizes||[]).map(option).join('');
  if(model?.custom_size)select.innerHTML+='<option value="custom">自定义宽高…</option>';
  const custom=model?.custom_size&&/^\d+x\d+$/.test(size||'')&&!model.sizes.includes(size);
  select.value=model?.sizes.includes(size)?size:custom?'custom':model?.sizes[0]||'';
  if(custom){const [width,height]=size.split('x');$('#'+prefix+'-width').value=width;$('#'+prefix+'-height').value=height;}
  const limits=mediaSizeLimits(model);
  for(const axis of ['width','height']){
    const input=$('#'+prefix+'-'+axis);
    if(input){input.min=limits.min;input.max=limits.max;input.step=limits.step;}
  }
  const note=$('#'+prefix+'-custom-note');
  if(note)note.textContent=model?.size_hint||`宽高 ${limits.min}–${limits.max} 像素，按 ${limits.step} 像素递增`;
  updateCustomSize(prefix);
}
function updateCustomSize(prefix){
  const custom=$('#'+prefix+'-size').value==='custom';
  $('#'+prefix+'-custom-size').hidden=!custom;
  for(const axis of ['width','height'])$('#'+prefix+'-'+axis).disabled=!custom;
}
function customSizeProblem(model,size){
  // null means the value is inside the model's published custom bounds. Without a model
  // (a widget used before the catalog arrives) fall back to the conservative legacy
  // bounds instead of treating the value as unlimited.
  const limits=mediaSizeLimits(model);
  if(model&&!model.custom_size)return '该模型不支持自定义宽高';
  if(!/^\d+x\d+$/.test(size||''))return (model&&model.size_hint)||`自定义宽高须为 ${limits.min}–${limits.max} 像素，且为 ${limits.step} 的倍数`;
  const [width,height]=String(size).split('x').map(Number);
  if([width,height].some(n=>!Number.isInteger(n)||n<limits.min||n>limits.max||n%limits.step))
    return (model&&model.size_hint)||`自定义宽高须为 ${limits.min}–${limits.max} 像素，且为 ${limits.step} 的倍数`;
  if(limits.max_area&&width*height>limits.max_area)
    return `${model.name||'该模型'} 宽高面积 ${width*height} 像素超过上限 ${limits.max_area} 像素（例如 1024×576）`;
  return null;
}
function customSizeAllowed(model,size){return customSizeProblem(model,size)===null;}
// Reference and first/last-frame workflows also require 32-aligned pixels. The option list
// already drops the non-aligned presets, but a reused recipe or a tampered DOM could still
// carry one, so the submit path re-checks it instead of shipping a doomed payload.
function mediaWorkflowSizeProblem(model,size,workflow){
  if(!workflow||workflow==='t2v')return null;
  const value=String(size||'');
  if(!/^[0-9]{3,4}x[0-9]{3,4}$/.test(value))return `${workflow} 工作流需要一个具体宽高，请先选择或填写尺寸`;
  const [width,height]=value.split('x').map(Number);
  if(!Number.isInteger(width)||!Number.isInteger(height)||width%32||height%32)
    return `${workflow} 等参考/首尾帧工作流的宽高须为 32 的像素倍数；${value} 不适用，请改选 32 对齐的尺寸（例如 768×448、1024×576）或切回文生视频`;
  return null;
}
// The size select uses a 'custom' sentinel; anything that stores or compares sizes must
// resolve it to the actual WxH the user typed, otherwise switching a workflow would throw
// away a perfectly legal custom size.
function resolveMediaSize(prefix,value){
  if(value!=='custom')return value;   // only the sentinel is resolved; '' stays ''
  const model=mediaOptionModels[prefix];
  const axes=['width','height'].map(axis=>$('#'+prefix+'-'+axis)?.value);
  if(axes.some(v=>v===null||v===''||v===undefined))return value;
  const size=axes.map(Number).join('x');
  return customSizeAllowed(model,size)?size:value;
}
function mediaWorkflowSizeNotice(size,workflow,chosen){
  return `${size} 不适用于 ${workflow} 工作流`
    + (chosen==='custom'?'，请填写 32 对齐的宽高':`，已先改为 ${chosen}`)
    + '；可重新选择 32 对齐的尺寸（例如 768×448、1024×576）';
}
function selectedMediaSize(prefix,workflow){
  const size=$('#'+prefix+'-size').value;
  const model=mediaOptionModels[prefix];
  if(size!=='custom'){
    const problem=mediaWorkflowSizeProblem(model,size,workflow);
    if(problem)throw new Error(problem);
    return size;
  }
  const dimensions=['width','height'].map(axis=>Number($('#'+prefix+'-'+axis).value));
  const chosen=dimensions.join('x');
  const problem=customSizeProblem(model,chosen)||mediaWorkflowSizeProblem(model,chosen,workflow);
  if(problem)throw new Error(problem);
  return chosen;
}
function mediaFrameLimits(model){
  return {min:Number(model?.frames_min)||39,max:Number(model?.frames_max)||345,
          step:Number(model?.frames_step)||17,fps:Number(model?.fps)||24};
}
function renderMediaFrames(prefix,model,value){
  const select=$('#'+prefix+'-frames');if(!select)return;
  mediaOptionModels[prefix]=model||mediaOptionModels[prefix]||null;
  const limits=mediaFrameLimits(model),list=(model&&Array.isArray(model.frames)&&model.frames.length)?model.frames:[56,124];
  const measured=new Set((model&&model.frames_measured)||[]);
  const label=frames=>`约 ${(frames/limits.fps).toFixed(1)} 秒 · ${frames} 帧${measured.has(frames)?' · 已实测':''}`;
  let options=list.map(frames=>`<option value="${frames}">${label(frames)}</option>`).join('');
  const chosen=(value===null||value===''||value===undefined)?NaN:Number(value);
  // History and drafts keep their exact original value instead of being snapped to a preset.
  if(Number.isInteger(chosen)&&!list.includes(chosen)){
    const legal=chosen>=limits.min&&chosen<=limits.max&&(chosen-limits.min)%limits.step===0;
    options=`<option value="${chosen}">${label(chosen)}${legal?' · 非默认档':' · 超出允许范围'}</option>`+options;
  }
  select.innerHTML=options;
  const fallback=list.includes(56)?56:list[0];   // the historical default stays the default
  select.value=Number.isInteger(chosen)?String(chosen):String(fallback);
  const note=$('#'+prefix+'-frames-note');
  if(note)note.textContent=model?.constraint_note||'';
}
function selectedMediaFrames(prefix,model){
  const limits=mediaFrameLimits(model||mediaOptionModels[prefix]);
  const frames=Number($('#'+prefix+'-frames').value);
  if(!Number.isInteger(frames))throw new Error('视频时长须为整数帧');
  if(frames<limits.min||frames>limits.max||(frames-limits.min)%limits.step)
    throw new Error(`视频时长须为 ${limits.min}–${limits.max} 帧且按 ${limits.step} 帧递增（${limits.fps} FPS）`);
  return frames;
}
