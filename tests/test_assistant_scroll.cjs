const {test}=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');
const path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../public/assistant-scroll.js'),'utf8');
function setup(){
  const listeners={},button={hidden:true,addEventListener:(type,fn)=>listeners['button-'+type]=fn};
  const box={scrollTop:0,scrollHeight:1800,clientHeight:400,
    getBoundingClientRect:()=>({top:100}),addEventListener:(type,fn)=>listeners[type]=fn,
    scrollTo({top}){this.scrollTop=Math.max(0,Math.min(top,this.scrollHeight-this.clientHeight));}};
  const rows=Array.from({length:6},(_,i)=>({dataset:{chatTurn:String(i)},start:i*300,height:300,
    getBoundingClientRect(){return {top:100+this.start-box.scrollTop,bottom:100+this.start+this.height-box.scrollTop};}}));
  const list={children:rows};let resize;
  const context=vm.createContext({document:{querySelector:id=>({'#assistant-messages':box,'#assistant-message-list':list,'#assistant-jump-latest':button})[id]},
    ResizeObserver:class{constructor(fn){resize=fn;}observe(){}}});
  vm.runInContext(source,context);
  return {box,rows,list,button,run:code=>vm.runInContext(code,context),resize:()=>resize(),
    scrollTo(top){box.scrollTo({top});listeners.scroll();},click:()=>listeners['button-click']()};
}
test('流式更新和完成重绘保留历史阅读位置，箭头回到底部后继续跟随',()=>{
  const s=setup();s.resize();assert.equal(s.box.scrollTop,1400);assert.equal(s.button.hidden,true);
  s.scrollTo(450);assert.equal(s.button.hidden,false);
  for(let i=0;i<3;i++){
    s.run('var position=captureAssistantScroll()');s.box.scrollHeight+=120;
    // Replacing the entire message DOM resets scrollTop before restoration.
    s.box.scrollTop=0;s.run('restoreAssistantScroll(position)');s.resize();
    assert.equal(s.box.scrollTop,450);assert.equal(s.button.hidden,false);
  }
  s.click();assert.equal(s.box.scrollTop,s.box.scrollHeight-400);assert.equal(s.button.hidden,true);
  s.box.scrollHeight+=240;s.resize();assert.equal(s.box.scrollTop,s.box.scrollHeight-400);
});
test('历史上方的图片加载与布局变化保持可见消息偏移',()=>{
  const s=setup();s.resize();s.scrollTo(650);
  s.rows[0].height+=180;for(const row of s.rows.slice(1))row.start+=180;s.box.scrollHeight+=180;
  s.resize();assert.equal(s.box.scrollTop,830);
  assert.equal(s.rows[2].getBoundingClientRect().top,50);
  s.box.clientHeight=250;s.resize();assert.equal(s.box.scrollTop,830);assert.equal(s.button.hidden,false);
});
test('手动回到底部恢复跟随；侧栏隐藏不改变阅读选择',()=>{
  const s=setup();s.resize();s.scrollTo(200);s.box.clientHeight=0;s.scrollTo(0);
  assert.equal(s.run('assistantScroll.followLatest'),false);
  s.box.clientHeight=400;s.resize();assert.equal(s.box.scrollTop,200);
  s.scrollTo(1400);assert.equal(s.button.hidden,true);s.box.scrollHeight+=100;s.resize();assert.equal(s.box.scrollTop,1500);
});
