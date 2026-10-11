'use strict';
// 产品的 edit_work 必须先在本轮 read_work。夹具先读当前版本，
// 随后只发合同指定的那一次 insert_text，不再发别的写入。
const counts=new Map();
function textOf(content){
 if(typeof content==='string')return content;
 if(Array.isArray(content))return content.map(part=>part&&(part.text||part.content)||'').join('');
 return '';
}
function lastUser(messages){
 const users=(messages||[]).filter(message=>message.role==='user');
 return textOf(users.at(-1)?.content);
}
function userPrompt(messages){
 return lastUser(messages).split('\n\n[工作台当前上下文')[0].replace(/\r\n/g,'\n').trim();
}
function contextOf(messages){
 const raw=lastUser(messages),marker='[工作台当前上下文，仅作定位数据]\n',at=raw.lastIndexOf(marker);
 if(at<0)return {};
 try{return JSON.parse(raw.slice(at+marker.length))}catch{return {}}
}
function calledNames(messages){
 const names=[];
 for(const message of messages||[])for(const call of message.tool_calls||[])names.push(call.function?.name||call.custom?.name||'');
 return names;
}
function readVersion(messages){
 let id='';
 for(const message of messages||[]){
  if(message.role!=='assistant')continue;
  for(const call of message.tool_calls||[])if((call.function?.name||call.custom?.name)==='read_work')id=call.id;
 }
 if(!id)return '';
 const tool=[...(messages||[])].reverse().find(message=>message.role==='tool'&&message.tool_call_id===id);
 if(!tool)return '';
 try{return JSON.parse(textOf(tool.content)).version||''}catch{return ''}
}
function operation(prompt){
 const paste=html=>({op:'insert_text',text_source:'paste',html});
 if(prompt.includes('今天发布甲方案的完整长文'))return paste('<p>短</p>');
 if(prompt.includes('再补一句过渡「接下来」'))return paste('<p>今天发布甲方案。</p>');
 if(prompt.includes('见 https://example.com/a。')){
  const n=(counts.get(prompt)||0)+1;counts.set(prompt,n);
  const href=n===1?'https://example.com/a':'https://example.com/a。以及';
  return paste('<p>见 <a href="'+href+'">https://example.com/a</a>。</p>');
 }
 if(prompt.includes('见 HTTPS://Example.COM/Path')){
  const n=(counts.get(prompt)||0)+1;counts.set(prompt,n);
  const href=n===1?'https://example.com/Path':'https://example.com/path';
  return paste('<p>见 <a href="'+href+'">HTTPS://Example.COM/Path</a></p>');
 }
 if(prompt.includes('见 https://a.com:xx/说明'))return paste('<p>见 <a href="https://a.com:xx/">https://a.com:xx/</a>说明</p>');
 if(prompt.includes('见 https://a.com:99999/说明'))return paste('<p>见 <a href="https://a.com:99999/">https://a.com:99999/</a>说明</p>');
 if(prompt.includes('见 https://[::1/说明'))return paste('<p>见 <a href="https://[::1/">https://[::1/</a>说明</p>');
 if(prompt.includes('round6-20261010 助手贴文测试'))return {operations:[
  {op:'insert_text',text_source:'paste_original'},
  {op:'insert_text',text_source:'compose',html:'<p>接下来继续观察。</p>'}]};
 return null;
}
function assistantReply(body){
 const messages=body.messages||[],names=calledNames(messages),context=contextOf(messages),slug=context.slug||'';
 if(!names.includes('read_work'))return {tool:{name:'read_work',arguments:{slug}}};
 if(userPrompt(messages).includes('同一响应三连')){
  const base=readVersion(messages)||context.head||context.viewed_version||'';
  const bad={name:'edit_work',arguments:{slug,base_version:base,summary:'错误抄写',operations:[{op:'insert_text',text_source:'paste',html:'<p>贴文</p>'}]}};
  const good={name:'edit_work',arguments:{slug,base_version:base,summary:'照原文',operations:[{op:'insert_text',text_source:'paste_original'}]}};
  return {tools:[bad,bad,good]};
 }
 if(userPrompt(messages).includes('再补一句「演示」')&&names.filter(name=>name==='edit_work').length<2){
  return {tool:{name:'edit_work',arguments:{slug,base_version:readVersion(messages)||context.head||context.viewed_version||'',summary:'错误抄写',operations:[{op:'insert_text',text_source:'paste',html:'<p>贴文</p>'}]}}};
 }
 if(!names.includes('edit_work')){
  const prompt=userPrompt(messages),op=operation(prompt);
  if(!op)return {content:'这条消息没有匹配到贴文夹具。'};
  return {tool:{name:'edit_work',arguments:{slug,base_version:readVersion(messages)||context.head||context.viewed_version||'',summary:'贴文',operations:op.operations||[op]}}};
 }
 return {content:'已按规则处理这一轮。'};
}
function isAssistant(body){
 const system=(body.messages||[]).filter(message=>message.role==='system').map(message=>textOf(message.content)).join('');
 const named=(body.tools||[]).some(tool=>{const name=tool.function?.name||tool.name;return name==='edit_work'||name==='read_work'});
 return system.includes('内容工作台助手')||named;
}
module.exports={assistantReply,isAssistant};
