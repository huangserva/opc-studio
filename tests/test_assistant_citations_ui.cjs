const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs');
const {JSDOM}=require('jsdom');
const escape=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const code=fs.readFileSync('public/assistant.js','utf8'),render=code.slice(code.indexOf('function renderAssistant('),code.indexOf('function assistantBusy('));
function setup(messages){
 const dom=new JSDOM('<div id="assistant-message-list"></div>',{url:'http://localhost',runScripts:'outside-only'}),w=dom.window;
 Object.assign(w,{assistantState:{messages},$:s=>w.document.querySelector(s),escapeHTML:escape,
  captureAssistantScroll:()=>({}),restoreAssistantScroll:()=>{},stopChatMedia:()=>{},syncChatMedia:()=>{},
  workflowCard:()=>'',assistantActions:()=>'',attachmentMarkup:()=>'',bindWorkflowCards:()=>{}});
 w.eval(fs.readFileSync('public/chat-links.js','utf8'));w.chatText=w.chatLinkedText;w.eval(render);w.renderAssistant();return w;
}
function citation(){const c={id:'a'.repeat(32),hash:'b'.repeat(64),start:1,end:5,excerpt:'合成原文',title:'合成来源',kind:'notes'};c.url='/knowledge.html?'+new URLSearchParams({document:c.id,start:c.start,end:c.end,hash:c.hash});return c;}
const turn=(id,citations=[])=>({id,prompt:'合成问题',answer:'回答未自行附引用',model:'本地测试替身',actions:[],status:'done',citations});
test('聊天展示真实读取卡片，流式重绘保留展开状态，重载历史仍能打开固定原文',()=>{
 const record=turn('first',[citation()]),w=setup([record]);let details=w.document.querySelector('[data-read-sources]');assert(details);assert.match(w.document.querySelector('.assistant-answer').textContent,/未自行附引用/);assert.match(details.textContent,/合成原文/);details.open=true;
 w.renderAssistant(turn('second'));details=w.document.querySelector('[data-read-sources]');assert(details.open);assert.equal(w.document.querySelectorAll('[data-read-sources]').length,1);
 const link=details.querySelector('article>a');assert.equal(new URL(link.href).searchParams.get('hash'),citation().hash);
 const reload=setup(JSON.parse(JSON.stringify([record])));assert.match(reload.document.querySelector('[data-read-sources]').textContent,/合成原文/);w.close();reload.close();
});
test('旧聊天与仅搜索的回合不补造已读来源，模型和资料文字作为文本展示',()=>{
 const c={...citation(),title:'<script>bad</script>',excerpt:'<img'};
 const w=setup([turn('old'),turn('candidate'),turn('read',[c])]);assert.equal(w.document.querySelectorAll('[data-read-sources]').length,1);assert.equal(w.document.querySelectorAll('script,img').length,0);assert.match(w.document.querySelector('[data-read-sources]').textContent,/<img/);w.close();
});
