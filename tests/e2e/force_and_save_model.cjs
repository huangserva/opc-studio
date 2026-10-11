'use strict';
const scenarios=new Map();
function setScenario(slug,scenario){scenarios.set(slug,scenario)}
function reply(body){
 const messages=body.messages||[],users=messages.filter(m=>m.role==='user');
 const raw=users.at(-1)?.content;
 const text=typeof raw==='string'?raw:(raw||[]).map(x=>x.text||'').join('');
 const marker='[工作台当前上下文，仅作定位数据]\n',at=text.lastIndexOf(marker);
 let context={};try{context=JSON.parse(text.slice(at+marker.length))}catch{}
 const slug=context.slug,scenario=scenarios.get(slug);if(!scenario)return null;
 const calls=messages.flatMap(m=>m.tool_calls||[]);
 const tool=(name,args)=>({name,arguments:args});
 if(!calls.some(c=>c.function?.name==='read_work')){
  return {tools:[tool('read_work',{slug}),...(scenario.other?[tool('read_work',{slug:scenario.other})]:[])]};
 }
 if(calls.some(c=>['edit_work','update_text'].includes(c.function?.name)))return {content:'已完成本轮测试。'};
 const target=scenario.other||slug;
 if(scenario.kind==='whitespace')return {tool:tool('edit_work',{slug,base_version:'v0001',summary:'原文空白',operations:[{op:'insert_text',text_source:'paste_original'}]})};
 const update=scenario.kind==='cross'
  ?{slug:target,base_version:'v0001',summary:'真实保存B',edits:[],append_text:['<p>B真实新正文</p>']}
  :{slug:target,base_version:'v0002',summary:'不改正文',edits:[{index:0,html:'<p>A原正文</p>'}]};
 const bad=tool('edit_work',{slug:target,base_version:'v0002',summary:'连续错误',operations:[{op:'insert_text',text_source:'paste',html:'<p>错</p>'}]});
 return {tools:[tool('update_text',update),bad,bad]};
}
module.exports={setScenario,reply};
