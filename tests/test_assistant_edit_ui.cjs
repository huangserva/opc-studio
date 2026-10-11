const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs');
const {JSDOM}=require('jsdom');
const escape=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const source=fs.readFileSync('public/assistant.js','utf8');
const actions=source.slice(source.indexOf('function assistantActions('),source.indexOf('function renderAssistant('));
const polling=source.slice(source.indexOf('async function pollAssistant('),source.indexOf('async function sendAssistant('));
function actionContext(){
 const dom=new JSDOM('<div id="box"></div>',{url:'http://localhost',runScripts:'outside-only'}),w=dom.window;
 Object.assign(w,{escapeHTML:escape,chatSourceLinks:()=>''});
 w.eval(actions);
 w.render=list=>{w.document.getElementById('box').innerHTML=w.assistantActions(list);return w.document.getElementById('box');};
 return w;
}
function pollContext(changed){
 const dom=new JSDOM('<div></div>',{url:'http://localhost',runScripts:'outside-only'}),w=dom.window;
 const calls=[];
 Object.assign(w,{assistantState:{messages:[],active:'job-1',renderedSignature:''},api:async()=>({status:'done',changed,answer:'好了',actions:[]}),
  renderAssistant:()=>{},assistantBusy:busy=>calls.push('busy:'+busy),showCurrentModel:()=>{},mediaSendReady:()=>{},syncChatMedia:()=>{},
  loadLibrary:async()=>calls.push('library'),updateAssistantContext:()=>calls.push('context'),
  state:{page:'article',slug:'sidrune-thread-20261008',dirty:false},openArticle:async slug=>calls.push('open:'+slug),
  clearTimeout:()=>{},setTimeout:()=>0,document:w.document,location:{href:'http://localhost'}});
 w.eval(polling);return {w,calls};
}
test('插入图片的助手卡片写明版本和插入位置，并给出打开该版本的链接',()=>{
 const w=actionContext();
 const box=w.render([{name:'edit_work',label:'修改正文结构并保存版本',status:'done',slug:'sidrune-thread-20261008',version:'v0007',
  result:'已保存 v0007：图片附件「邮件截图.png」→第 1 条推文后（图片属于第 1 条）；新增文字→第 3 条推文后'}]);
 assert.match(box.textContent,/已保存 v0007/);
 assert.match(box.textContent,/第 1 条推文后/);
 const link=box.querySelector('[data-assistant-slug]');
 assert.equal(link.dataset.assistantSlug,'sidrune-thread-20261008');
 assert.equal(link.dataset.assistantVersion,'v0007');
 assert.match(link.textContent,/查看 v0007/);
});
test('文字卡片插入和失败的插入都不会伪造已保存的链接',()=>{
 const w=actionContext();
 const box=w.render([{name:'create_card',label:'生成文字封面 / 信息卡',status:'done',slug:'sidrune-thread-20261008',version:'v0008',result:'已生成文字卡片并插入到第 3 条推文后，保存 v0008',image_url:'/assistant-images/'+'a'.repeat(32)+'.png'},
  {name:'edit_work',label:'修改正文结构并保存版本',status:'error',result:'生成任务当前状态为 running，尚未完成，不能插入作品'}]);
 assert.match(box.textContent,/第 3 条推文后，保存 v0008/);
 assert.match(box.textContent,/尚未完成，不能插入作品/);
 assert.equal(box.querySelectorAll('[data-assistant-slug]').length,1,'失败的插入不提供打开版本链接');
 assert.equal(box.querySelectorAll('img').length,1);
});
test('保存新版本后作品预览重新打开该作品，未保存草稿不被覆盖',async()=>{
 const slug='sidrune-thread-20261008';
 const clean=pollContext([slug]);
 await clean.w.pollAssistant('job-1');
 assert.deepEqual(clean.calls.filter(c=>c!=='busy:true'&&c!=='busy:false'),['library','open:'+slug,'context']);
 const dirty=pollContext([slug]);
 dirty.w.state.dirty=true;
 await dirty.w.pollAssistant('job-1');
 assert.ok(!dirty.calls.some(c=>c.startsWith('open:')),'有未保存修改时不刷新，避免丢弃用户草稿');
 const unrelated=pollContext(['other-work']);
 await unrelated.w.pollAssistant('job-1');
 assert.ok(!unrelated.calls.some(c=>c.startsWith('open:')));
});
