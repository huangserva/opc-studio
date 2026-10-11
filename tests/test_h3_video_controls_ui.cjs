/* H3 video controls in the real lab DOM (jsdom): the option lists, the custom-size bounds
   and the duration ladder all come from the model catalog the backend serves, invalid
   combinations never reach the network, and drafts/history keep their exact original value.

   The fixture below mirrors the catalog generation/models.py publishes (the Python parity
   test pins that equality); this test proves the page consumes it faithfully.

   Run: node --test tests/test_h3_video_controls_ui.cjs
*/
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const {JSDOM}=require('jsdom');

const root=path.join(__dirname,'..');
const html=fs.readFileSync(path.join(root,'public/lab.html'),'utf8');
// lab.html loads lab-image.js before lab.js; configureLab calls configureImageReferences.
const scriptNames=['lab-core.js','media-size.js','media-lora.js','lab-video.js','lab-image.js','lab.js'];
const scripts=scriptNames.map(name=>({name,source:fs.readFileSync(path.join(root,'public',name),'utf8').replace(/\nrefreshLab\(true\);\s*$/,'\n')}));

const h3Catalog={
  id:'h3',name:'MiniMax H3 · 4090',kind:'video',steps:8,
  sizes:['768x448','448x768','768x432','1024x576','432x768','576x1024','768x576','576x768','768x512','512x768','512x512','768x768'],
  size_labels:Object.fromEntries([['768x448','12:7'],['448x768','7:12'],['768x432','16:9'],['1024x576','16:9'],['432x768','9:16'],['576x1024','9:16'],['768x576','4:3'],['576x768','3:4'],['768x512','3:2'],['512x768','2:3'],['512x512','1:1'],['768x768','1:1']].map(([s,r])=>[s,`${r} · ${s}`])),
  size_groups:[{name:'已实测（产物 ffprobe 核对）',sizes:['768x448','448x768']},{name:'16:9 / 9:16',sizes:['768x432','1024x576','432x768','576x1024']},{name:'4:3 / 3:4',sizes:['768x576','576x768']},{name:'3:2 / 2:3',sizes:['768x512','512x768']},{name:'1:1',sizes:['512x512','768x768']}],
  custom_size:{min:256,max:1024,step:32,max_area:589824},
  size_hint:'H3 自定义宽高须为 256–1024 像素、32 的倍数，且面积不超过 589824 像素（即 1024×576）',
  sizes_measured:['768x448','448x768'],
  frames:[...Array(19)].map((_,index)=>39+index*17),
  frames_min:39,frames_max:345,frames_step:17,frames_measured:[56,124],fps:24,
  constraint_note:'尺寸按 32 像素对齐，可选 256–1024 像素且面积≤1024×576；时长 39–345 帧（24 FPS，按 17 帧递增）。实测按组合逐项核对：只有列在“已实测组合”里的搭配经过产物核对，其余组合允许提交但未经逐项实测；尺寸或时长上的“已实测”标记只代表该单项曾出现在实测组合中，不代表任意搭配都已验证。768×432 / 432×768 不是 32 的倍数，只用于文生视频；参考素材与首尾帧工作流只列 32 对齐的尺寸。',
  workflows:[{id:'t2v',name:'文生视频'},{id:'r2v',name:'多参考素材生视频'}],
  checkpoints:[{id:'singularity',name:'Singularity',modes:['t2v','r2v']}],
  accelerators:[{id:'ref8',name:'ref2v Turbo · 8 步',steps:8,modes:['t2v','r2v']}]
};

function setup(t,{jobs=[]}={}){
  const dom=new JSDOM(html,{url:'http://127.0.0.1:8766/lab.html?kind=video',runScripts:'outside-only'});
  t.after(()=>dom.window.close());
  const w=dom.window,context=dom.getInternalVMContext();
  const fetchCalls=[],timers=new Map();
  w.setTimeout=(fn,delay)=>{timers.set(Math.floor(Math.random()*1e9),{fn,delay});return 1;};
  w.clearTimeout=id=>timers.delete(id);
  w.fetch=async(url,options)=>{
    fetchCalls.push({url,options});
    if(/^\/api\/generation\/lab\?/.test(url)){
      return {ok:true,json:async()=>({models:[h3Catalog],jobs,busy:false,preferences:{video:{}},image_skill:{styles:[]}})};
    }
    // any generation attempt is recorded and refused, so the test can prove what was sent
    return {ok:false,json:async()=>({error:'refused by test'})};
  };
  w.renderMediaProgress=()=>{};w.attachMediaProgressHost=()=>{};w.markMediaProgressUnavailable=()=>{};
  w.document.querySelector('#lab-model').innerHTML='<option value="h3">H3</option>';
  w.fixtureModel=h3Catalog;
  for(const script of scripts)vm.runInContext(script.source,context,{filename:script.name});
  vm.runInContext('lab.models=[fixtureModel];configureLab({});',context,{filename:'bootstrap'});
  const run=source=>vm.runInContext(source,context);
  return {w,run,fetchCalls,
    query:selector=>w.document.querySelector(selector),
    select(selector,value){const element=w.document.querySelector(selector);element.value=String(value);
      element.dispatchEvent(new w.Event('change',{bubbles:true}));return element;},
    set(selector,value){const element=w.document.querySelector(selector);element.value=String(value);
      element.dispatchEvent(new w.Event('input',{bubbles:true}));return element;}};
}

test('时长下拉来自后端档位：19 档、显示真实秒数、标注已实测与约束说明',t=>{
  const s=setup(t);
  const options=s.query('#lab-frames').options;
  assert.equal(options.length,19);
  assert.equal(options[0].value,'39');
  assert.match(options[0].textContent,/约 1\.6 秒 · 39 帧/);
  const measured=[...options].filter(o=>/已实测/.test(o.textContent)).map(o=>o.value);
  assert.deepEqual(measured,['56','124']);
  assert.match(s.query('#lab-frames-note').textContent,/实测按组合逐项核对/);
  assert.match(s.query('#lab-frames-note').textContent,/不代表任意搭配/);
  assert.match(s.query('#lab-frames-note').textContent,/未经逐项实测/);
  assert.ok(![...options].some(o=>Number(o.value)>345),'node schema maxima are never offered');
});

test('尺寸下拉按比例分组，1024×576 与自定义宽高都能进入提交 payload',t=>{
  const s=setup(t);
  const groups=s.query('#media-size').querySelectorAll('optgroup');
  assert.equal(groups.length,5);
  assert.equal(groups[0].label,'已实测（产物 ffprobe 核对）');
  const values=[...s.query('#media-size').options].map(o=>o.value);
  assert.ok(values.includes('1024x576')&&values.includes('custom'),values.join(','));
  const labels=[...s.query('#media-size').options].map(o=>o.textContent);
  assert.ok(labels.includes('12:7 · 768x448'),labels.join(','));
  assert.ok(!labels.includes('16:9 · 768x448'),'an approximate ratio must not be sold as exact');
  assert.ok(labels.includes('16:9 · 1024x576'));
  s.select('#media-size','1024x576');
  assert.equal(s.run('labParameters()').size,'1024x576');
  s.select('#media-size','custom');
  assert.equal(s.query('#media-custom-size').hidden,false,'custom inputs become visible');
  s.set('#media-width','800');s.set('#media-height','480');
  assert.equal(s.run('labParameters()').size,'800x480');
  assert.equal(s.run('labParameters()').frames,56,'default duration stays 56');
});

test('自定义宽高越界时提交被拦下，且不会发出任何生成请求',t=>{
  const s=setup(t);
  s.select('#media-size','custom');
  s.set('#media-width','777');s.set('#media-height','448');
  assert.throws(()=>s.run('labParameters()'),/256–1024 像素、32 的倍数/);
  s.set('#media-width','1024');s.set('#media-height','608');
  assert.throws(()=>s.run('labParameters()'),/589824/);
  s.run('$("#lab-prompt").value="a neutral test scene";$("#lab-form").dispatchEvent(new Event("submit",{cancelable:true,bubbles:true}));');
  const generation=s.fetchCalls.filter(call=>/lab\/start/.test(call.url));
  assert.equal(generation.length,0,'invalid values must never reach the server');
  assert.match(s.query('#lab-submit-status').textContent,/589824/);
});

test('时长越界的历史值不被静默改写，但提交时会被拒绝',t=>{
  const s=setup(t);
  s.run('configureLab({size:"768x448",frames:40});');
  assert.equal(s.query('#lab-frames').value,'40','the original value is preserved in the control');
  assert.match([...s.query('#lab-frames').options][0].textContent,/超出允许范围/);
  assert.throws(()=>s.run('labParameters()'),/39–345/);
});

test('切换工作流只保留 32 对齐尺寸，并明确说明改动',t=>{
  const s=setup(t);
  s.select('#media-size','768x432');
  assert.equal(s.run('labParameters()').size,'768x432');
  s.select('#lab-workflow','r2v');
  const values=[...s.query('#media-size').options].map(o=>o.value);
  assert.ok(!values.includes('768x432')&&!values.includes('432x768'),values.join(','));
  assert.match(s.query('#lab-model-note').textContent,/768x432 不适用于 r2v 工作流，已先改为 768x448/);
  assert.match(s.query('#media-size-note').textContent,/可重新选择 32 对齐的尺寸/);
  assert.equal(s.run('labParameters()').size,'768x448');
});

test('草稿与历史沿用保留原尺寸与原帧数，不回到默认档',t=>{
  const s=setup(t);
  s.select('#media-size','1024x576');
  s.select('#lab-frames','175');
  s.set('#lab-prompt','a neutral test scene');
  s.run('rememberLab();');
  const draft=s.run('readLabDraft()');
  assert.equal(draft.size,'1024x576');
  assert.equal(draft.frames,175);
  s.run('configureLab(readLabDraft());');
  assert.equal(s.query('#media-size').value,'1024x576');
  assert.equal(s.query('#lab-frames').value,'175');
  const job={id:'a'.repeat(32),kind:'video',status:'done',model:'h3',model_name:'H3',origin:'lab',
    prompt:'a neutral test scene',created_at:'2026-10-06T12:00:00Z',
    settings:{size:'1024x576',seed:42,frames:175,fps:24,seconds:7.29,workflow:'t2v'}};
  s.run(`renderLabJob(${JSON.stringify(job)});fillLab(lab.selected);`);
  assert.equal(s.query('#media-size').value,'1024x576');
  assert.equal(s.query('#lab-frames').value,'175');
  assert.equal(s.query('#lab-seed').value,'42');
  assert.equal(s.run('labParameters()').frames,175);
});

test('切换工作流/模型保留合法自定义宽高与时长，不把 custom 哨兵当尺寸',t=>{
  const s=setup(t);
  s.select('#media-size','custom');
  s.set('#media-width','800');s.set('#media-height','480');
  s.select('#lab-frames','175');
  s.run('rememberLab();');
  const switchTo=id=>{s.select('#lab-workflow',id);s.run('$("#lab-workflow").dispatchEvent(new Event("change"));');};
  // 800x480 is 32-aligned and inside the published area cap, so it is legal in both modes
  switchTo('r2v');
  assert.equal(s.query('#media-size').value,'custom','a legal custom size survives the workflow switch');
  assert.equal(s.query('#media-width').value,'800');assert.equal(s.query('#media-height').value,'480');
  assert.equal(s.query('#lab-frames').value,'175','and so does the chosen duration');
  assert.equal(s.query('#media-size-note').textContent,'','no warning for a legal value');
  const payload=s.run('labParameters()');
  assert.deepEqual({size:payload.size,frames:payload.frames,workflow:payload.workflow},
    {size:'800x480',frames:175,workflow:'r2v'});
  // a portrait custom pair behaves the same across two switches
  s.set('#media-width','480');s.set('#media-height','800');
  switchTo('t2v');switchTo('r2v');
  assert.equal(s.query('#media-width').value,'480');assert.equal(s.query('#media-height').value,'800');
  assert.equal(s.run('labParameters()').size,'480x800');
  // a page reload restores the same concrete values from the draft, not a preset
  s.run('rememberLab();');
  s.run(`$('#media-size').innerHTML='';$('#media-width').value='';$('#media-height').value='';`);
  s.run('configureLab(readLabDraft());');
  assert.equal(s.query('#media-size').value,'custom');
  assert.equal(s.query('#media-width').value,'480');assert.equal(s.query('#media-height').value,'800');
  const reloaded=s.run('labParameters()');
  assert.deepEqual({size:reloaded.size,frames:reloaded.frames},{size:'480x800',frames:175});
});

test('刷新页面后草稿原值回填，提交 payload 与界面一致',async t=>{
  const s=setup(t);
  s.select('#media-size','custom');
  s.set('#media-width','896');s.set('#media-height','512');
  s.select('#lab-frames','175');
  s.set('#lab-prompt','a neutral test scene');
  s.run('rememberLab();');
  // a workflow/model reconfigure that carries no size must keep the typed custom size
  s.run('configureLab({});');
  assert.equal(s.query('#media-size').value,'custom','a reconfigure keeps the typed custom size');
  assert.equal(s.query('#media-width').value,'896');
  assert.equal(s.query('#media-height').value,'512');
  // simulate a page reload: fresh controls plus the stored draft
  s.run(`$('#media-size').innerHTML='';$('#media-width').value='';$('#media-height').value='';`);
  s.run('configureLab({});');
  assert.equal(s.query('#media-size').value,'768x448','a fresh page starts at the default');
  s.run('configureLab(readLabDraft());');
  assert.equal(s.query('#media-size').value,'custom');
  assert.equal(s.query('#media-width').value,'896');
  assert.equal(s.query('#media-height').value,'512');
  assert.equal(s.query('#lab-frames').value,'175');
  const payload=s.run('labParameters()');
  assert.deepEqual({size:payload.size,frames:payload.frames},{size:'896x512',frames:175});
  s.run('$("#lab-form").dispatchEvent(new Event("submit",{cancelable:true,bubbles:true}));');
  await new Promise(resolve=>setImmediate(resolve));
  const generation=s.fetchCalls.filter(call=>/lab\/start/.test(call.url));
  assert.equal(generation.length,1,'exactly one submission for a valid form');
  const sent=JSON.parse(generation[0].options.body);
  assert.deepEqual({size:sent.size,frames:sent.frames},{size:'896x512',frames:175});
  assert.equal(sent.model,'h3');
});
