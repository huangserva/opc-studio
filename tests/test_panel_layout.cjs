const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {JSDOM} = require('jsdom');
const script = fs.readFileSync('public/panel-layout.js', 'utf8');
function fixture({saved, narrow=false, assistant=true}={}) {
  const dom = new JSDOM(`<body><aside class="sidebar"></aside><main class="main"><header class="topbar"></header><textarea>未保存的草稿</textarea></main>${assistant?'<aside id="assistant-panel" hidden><header class="assistant-head"></header><textarea>对话输入</textarea></aside>':''}</body>`, {url:'http://127.0.0.1:8766/knowledge.html', runScripts:'outside-only'});
  const w=dom.window;
  w.matchMedia=()=>({matches:narrow,addEventListener(){}});
  if (saved) w.localStorage.setItem('studio-panel-layout-v1', JSON.stringify(saved));
  w.eval(script);
  const click = label => [...w.document.querySelectorAll('button')].find(b=>b.getAttribute('aria-label')===label).click();
  return {w,d:w.document,click,close:()=>w.close()};
}
test('legacy global collapse cannot hide the new page and is discarded',()=>{
  const f=fixture({saved:{navigation:false,content:true}});
  assert.equal(f.d.querySelector('.main').hidden,false);
  assert.equal(f.d.body.dataset.contentCollapsed,'false');
  assert.deepEqual(JSON.parse(f.w.localStorage.getItem('studio-panel-layout-v1')),{navigation:false});
  f.close();
});
test('content collapse applies only to the current page, navigation preference survives',()=>{
  const f=fixture({saved:{navigation:true}});
  f.click('收起内容区');
  assert.equal(f.d.querySelector('.main').hidden,true);
  const saved=JSON.parse(f.w.localStorage.getItem('studio-panel-layout-v1'));
  const next=fixture({saved,assistant:false});
  assert.equal(next.d.querySelector('.main').hidden,false);
  assert.equal(next.d.body.dataset.navigationCollapsed,'true');
  f.close();next.close();
});
test('reset recovers content from collapsed panels without destroying drafts',()=>{
  const f=fixture();const draft=f.d.querySelector('.main textarea');
  draft.value='正在编辑的草稿';f.click('收起内容区');f.click('恢复默认布局');
  assert.equal(f.d.querySelector('.main').hidden,false);
  assert.equal(f.d.querySelector('.main textarea'),draft);
  assert.equal(draft.value,'正在编辑的草稿');
  assert.equal(f.d.body.dataset.navigationCollapsed,'false');
  f.close();
});
test('reset exits navigation maximization and preserves the existing assistant',()=>{
  const f=fixture();const assistant=f.d.querySelector('#assistant-panel');
  f.w.openAssistant=()=>f.w.StudioPanels.assistantChanged(true);
  f.w.openAssistant();f.click('最大化左侧导航');
  assert.equal(f.d.body.dataset.maximized,'navigation');
  f.click('恢复默认布局');
  assert.equal(f.d.body.dataset.maximized,undefined);
  assert.equal(f.d.querySelector('.main').hidden,false);
  assert.equal(f.d.querySelector('#assistant-panel'),assistant);
  assert.equal(assistant.hidden,false);
  f.close();
});
test('narrow reset restores content and leaves navigation accessible through its rail',()=>{
  const f=fixture({narrow:true,assistant:false,saved:{content:true,navigation:false}});
  f.click('收起内容区');f.click('恢复默认布局');
  assert.equal(f.d.querySelector('.main').hidden,false);
  assert.equal(f.d.body.dataset.navigationCollapsed,'true');
  assert.equal(f.d.querySelector('[data-panel-rail="navigation"]').hidden,false);
  f.close();
});
