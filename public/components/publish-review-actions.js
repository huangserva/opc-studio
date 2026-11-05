import {grokNode as node} from './item-card.js';
// These are the legacy feedback endpoints: provenance and body-hash dedup stay server-side.
async function post(path,payload){
 let response,data;
 try{response=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json','X-Article-Studio':'1'},body:JSON.stringify(payload)});data=await response.json();}
 catch(_){throw Error('操作未完成，请检查连接后重试。');}
 if(!response.ok)throw Error(response.status===403?'请求未获授权，请刷新页面后重试。':'操作未完成，请检查输入与发布登记后重试。');
 return data;
}
function input(label,kind='text',value=''){
 const wrap=node('label','review-field',label),el=node(kind==='textarea'?'textarea':'input');if(kind!=='textarea')el.type=kind;el.value=value;el.setAttribute('aria-label',label);wrap.append(el);return [wrap,el];
}
function options(label,values){const wrap=node('label','review-field',label),el=node('select');el.setAttribute('aria-label',label);for(const[value,text]of values){const opt=node('option','',text);opt.value=value;el.append(opt);}wrap.append(el);return[wrap,el];}
function button(text){const el=node('button','review-action',text);el.type='button';return el;}
function localTime(){return new Date(Date.now()+8*3600000).toISOString().slice(0,16);}
function beijing(value){return value?new Date(value+':00+08:00').toISOString():'';}
function knowledgeLink(box,url){
 // Only the legacy local knowledge route is valid for this action's response.
 try{const parsed=new URL(url,location.origin);if(parsed.origin!==location.origin||parsed.pathname!=='/knowledge.html'||!parsed.searchParams.get('document'))return false;
 const a=node('a','','打开知识库笔记');a.href=parsed.pathname+parsed.search;box.replaceChildren(a);return true;}catch(_){return false;}
}
const editorDrafts=new Map();
function ownerMatches(draft,row){return draft?.owner?.slug===row.slug&&draft?.owner?.delivery_id===row.delivery_id;}
export function attachReviewActions(panel,row,state,draft,refresh){
 const owner={slug:row.slug,delivery_id:row.delivery_id},ownerKey=JSON.stringify([owner.slug,owner.delivery_id]);
 const incoming=ownerMatches(draft,row)?draft:editorDrafts.get(ownerKey);
 const previous=ownerMatches(incoming,row)?incoming:null,formDraft=previous?.import||{};
 let remember=()=>{};
 const section=node('section','review-editor'),[noteLabel,notes]=input('复盘备注','textarea',previous?.notesDirty?previous.notes:state.notes??'');notes.maxLength=20000;notes.rows=6;
 const saved=node('p','muted','本机保存，最多20,000字；存为知识库笔记沿用旧页的出处与去重。'),actions=node('div','review-actions'),save=button('保存备注'),knowledge=button('存为知识库笔记'),status=node('p','review-action-status',ownerMatches(draft,row)?draft.notice||'':''),links=node('div','review-note-links');status.setAttribute('role','status');status.setAttribute('aria-live','polite');
 section.append(node('h3','','复盘备注'),noteLabel,actions,saved,status,links);actions.append(save,knowledge);panel.append(section);
 let busy=false,savedNotes=state.notes||'';
 const message=(text,error=false)=>{status.textContent=text;status.dataset.error=String(error);};
 async function action(work){if(busy)return;busy=true;const controls=[...section.querySelectorAll('input,textarea,select,button')];const old=controls.map(el=>el.disabled);controls.forEach(el=>el.disabled=true);try{await work();}catch(error){message(error.message,true);}finally{busy=false;controls.forEach((el,i)=>el.disabled=old[i]);}}
 save.onclick=()=>action(async()=>{const submitted=notes.value;await post('/api/feedback/review/'+encodeURIComponent(owner.slug)+'/notes',{notes:submitted});savedNotes=submitted;remember();message('备注已保存在本机。');});
 knowledge.onclick=()=>action(async()=>{
  if(notes.value!==savedNotes)throw Error('请先保存备注，再存为知识库笔记。');
  const result=await post('/api/feedback/review/'+encodeURIComponent(owner.slug)+'/knowledge',{});
  if(!knowledgeLink(links,result.url))throw Error('笔记已处理，但返回链接无法核对，请到旧页查看。');
  message(result.duplicate?'同一份笔记已在知识库，未重复入库。':'已存为知识库笔记（候选资料）。');
 });
 const details=node('details','review-import'),summary=node('summary','','导入指标快照'),form=node('form','review-import-form');details.append(summary,form);section.append(details);
 form.append(node('p','muted','只录入平台后台的原始指标，缺失值留空，不补零。快照绑定本次已发布项与固定版本；采集时间和统计范围请核实。'));
 const binding=node('p','review-binding','固定版本 '+row.version+' · '+row.url);form.append(binding);
 const fields=node('div','review-import-fields');form.append(fields);
 const [ctLabel,collected]=input('采集时间（北京时间）','datetime-local',formDraft.collected||localTime());collected.required=true;
 const [rkLabel,kind]=options('统计范围',[['cumulative','累计'],['interval','区间']]);
 const [rsLabel,start]=input('开始时间（北京时间）','datetime-local',formDraft.start||'');
 const [reLabel,end]=input('截止时间（北京时间）','datetime-local',formDraft.end||collected.value);end.required=true;
 const [rlLabel,rangeLabel]=input('统计口径说明');rangeLabel.required=true;rangeLabel.maxLength=500;rangeLabel.placeholder='例如：平台后台显示的累计浏览和点赞';
 const [skLabel,sourceKind]=options('快照来源',[['manual','手动录入'],['csv','原始 CSV']]);
 const [urlLabel,url]=input('统计来源页面','url');url.required=true;url.placeholder='平台统计页面的完整链接';
 const [pnLabel,provenanceNote]=input('来源说明','textarea');provenanceNote.maxLength=1000;provenanceNote.rows=2;
 kind.value=formDraft.kind||'cumulative';rangeLabel.value=formDraft.rangeLabel||'';sourceKind.value=formDraft.sourceKind||'manual';url.value=formDraft.url||'';provenanceNote.value=formDraft.provenanceNote||'';
 fields.append(ctLabel,rkLabel,rsLabel,reLabel,rlLabel,skLabel,urlLabel,pnLabel);kind.onchange=()=>start.required=kind.value==='interval';
 const manual=node('div','review-manual'),rows=node('div','review-metric-rows'),add=button('添加指标');manual.append(rows,add);form.append(manual);
 function addMetric(initialName='',initialValue=''){if(rows.children.length>=40){message('最多录入40个原始指标。',true);return;}const line=node('div','review-metric-row'),[nLabel,name]=input('原始指标名称'),[vLabel,value]=input('原始指标数值或文字'),remove=button('移除');name.value=initialName;value.value=initialValue;name.maxLength=80;value.maxLength=200;name.placeholder='例如 Views';value.placeholder='缺失时移除此行';line.append(nLabel,vLabel,remove);remove.onclick=()=>{line.remove();remember();};rows.append(line);}
 add.onclick=()=>{addMetric();remember();};if(Array.isArray(formDraft.metrics)){for(const entry of formDraft.metrics)addMetric(entry.name,entry.value);}else addMetric();
 const csv=node('div','review-csv');csv.hidden=true;
 const [fileLabel,file]=input('原始 CSV 文件','file');file.accept='.csv,text/csv';
 const [rowLabel,csvRow]=input('CSV 数据行号','number','1');csvRow.min='1';csvRow.step='1';
 const [urlColumnLabel,urlColumn]=input('CSV 原帖链接列名');
 const [columnsLabel,columns]=input('CSV 指标列名（每行一个）','textarea');columns.rows=3;
 csvRow.value=formDraft.csvRow||'1';urlColumn.value=formDraft.urlColumn||'';columns.value=formDraft.columns||'';
 let selectedFile=formDraft.file||null;
 const preview=node('pre','review-csv-preview');csv.append(fileLabel,rowLabel,urlColumnLabel,columnsLabel,preview);form.append(csv);
 sourceKind.onchange=()=>{const isCsv=sourceKind.value==='csv';manual.hidden=isCsv;csv.hidden=!isCsv;file.required=isCsv&&!selectedFile;urlColumn.required=isCsv;columns.required=isCsv;};sourceKind.onchange();kind.onchange();
 file.onchange=()=>{selectedFile=file.files?.[0]||null;file.required=sourceKind.value==='csv'&&!selectedFile;preview.textContent=selectedFile?'已选择本地 CSV；提交时校验并导入原始内容。':'';};
 if(selectedFile){preview.textContent='已保留所选 CSV：'+selectedFile.name;file.required=false;}
 const submit=button('导入指标快照');submit.type='submit';form.append(submit);
 function snapshot(notice=''){return {owner:{...owner},notes:notes.value,notesDirty:notes.value!==savedNotes,notice,import:{collected:collected.value,kind:kind.value,start:start.value,end:end.value,rangeLabel:rangeLabel.value,sourceKind:sourceKind.value,url:url.value,provenanceNote:provenanceNote.value,metrics:[...rows.children].map(line=>{const inputs=line.querySelectorAll('input');return {name:inputs[0].value,value:inputs[1].value};}),csvRow:csvRow.value,urlColumn:urlColumn.value,columns:columns.value,file:selectedFile}};}
 remember=()=>editorDrafts.set(ownerKey,snapshot());section.addEventListener('input',remember);section.addEventListener('change',remember);
 form.onsubmit=event=>{event.preventDefault();return action(async()=>{
  let collectedAt,endAt,startAt;try{collectedAt=beijing(collected.value);endAt=beijing(end.value);startAt=beijing(start.value);}catch(_){throw Error('请填写有效的北京时间。');}
  remember();const payload={slug:owner.slug,delivery_id:owner.delivery_id,platform:row.platform,version:row.version,collected_at:collectedAt,range:{kind:kind.value,start:startAt,end:endAt,label:rangeLabel.value.trim()}};
  if(sourceKind.value==='csv'){
   const selected=file.files?.[0]||selectedFile;if(!selected||selected.size>1024000||!selected.name.toLowerCase().endsWith('.csv'))throw Error('请选择不超过256,000字的原始 CSV 文件。');
   let text;try{text=await selected.text();}catch(_){throw Error('无法读取所选 CSV，请重新选择。');}if(Array.from(text).length>256000)throw Error('原始 CSV 最多256,000字。');
   payload.provenance={type:'csv',filename:selected.name,text,row:Number(csvRow.value),url_column:urlColumn.value.trim(),metric_columns:columns.value.split('\n').map(v=>v.trim()).filter(Boolean),url:url.value.trim()};
  }else{
   const values=Object.create(null);for(const line of rows.children){const inputs=line.querySelectorAll('input'),name=inputs[0].value.trim(),value=inputs[1].value;if(!name&&!value.trim())continue;if(!name||!value.trim())throw Error('每个指标都须填写原始名称和数值；缺失值请移除此行。');if(Object.hasOwn(values,name))throw Error('原始指标名称不能重复。');values[name]=value;}
   if(!Object.keys(values).length)throw Error('请至少填写一个原始指标；未知值不能补零。');payload.metrics=values;payload.provenance={type:'manual',url:url.value.trim(),note:provenanceNote.value.trim()};
  }
  await post('/api/feedback/import',payload);message('指标快照已导入。');const next=snapshot('指标快照已导入；未填写的指标仍为未知。');editorDrafts.set(ownerKey,next);await refresh(next);
 });};
}
