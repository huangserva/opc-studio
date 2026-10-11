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
const scriptNames=['lab-core.js','media-size.js','media-lora.js','lab-video.js','lab-image.js','lab-management.js','lab.js'];
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


test('单采默认、双采参数提交与本地偏好恢复',async t=>{
 const s=setup(t);
 assert.equal(s.query('#lab-video-sampling').value,'single');
 assert.equal(s.query('#lab-video-refine').hidden,true);
 s.select('#lab-video-sampling','dual');
 s.set('#lab-video-refine-steps',6);s.set('#lab-video-refine-denoise',0.25);s.set('#lab-video-upscale',1.5);
 assert.equal(s.query('#lab-video-refine').hidden,false);
 const payload=s.run('labParameters()');
 assert.equal(payload.video_sampling,'dual');assert.equal(payload.video_refine_steps,6);
 assert.equal(payload.video_refine_denoise,0.25);assert.equal(payload.video_upscale,1.5);
 s.set('#lab-prompt','a calm lake');
 await s.run('$("#lab-form").onsubmit({preventDefault(){}})');
 const sent=JSON.parse(s.fetchCalls.find(call=>/lab\/start/.test(call.url)).options.body);
 assert.equal(sent.video_sampling,'dual');assert.equal(sent.video_refine_steps,6);assert.equal(sent.video_upscale,1.5);
 const saved=JSON.parse(s.w.localStorage.getItem('studio-generation-lab-v1-video'));
 s.w.savedDraft=saved;s.run('configureLab(savedDraft)');
 assert.equal(s.query('#lab-video-refine-steps').value,'6');
 s.run('configureLab(currentLabValues())');
 assert.equal(s.query('#lab-video-sampling').value,'dual');
 s.run('configureLab()');assert.equal(s.query('#lab-video-sampling').value,'single');
 assert.equal(s.query('#lab-video-refine-steps').disabled,true);
});
test('沿用参数与历史卡片保留采样方式，旧任务按单采显示',t=>{
 const s=setup(t);
 s.w.fixtureJob={id:'job',kind:'video',model:'h3',prompt:'lake',settings:{size:'768x448',frames:39,seed:42,seconds:1.63,video_sampling:'dual',video_refine_steps:7,video_refine_denoise:0.4,video_upscale:2}};
 s.run('fillLab(fixtureJob)');
 assert.equal(s.query('#lab-video-sampling').value,'dual');assert.equal(s.query('#lab-video-refine-steps').value,'7');
 assert.match(s.run('jobSummary(fixtureJob)'),/双采.*精修 7 步/);
 s.run('delete fixtureJob.settings.video_sampling');assert.match(s.run('jobSummary(fixtureJob)'),/单采/);
});
test('非法精修参数和未对齐尺寸在提交前被拦截',t=>{
 const s=setup(t);s.select('#lab-video-sampling','dual');s.set('#lab-video-refine-steps',1.5);
 assert.throws(()=>s.run('validateVideoParameters(labParameters())'),/双采参数超出范围/);
 s.set('#lab-video-refine-steps',4);s.select('#media-size','768x432');
 assert.throws(()=>s.run('validateVideoParameters(labParameters())'),/32 的倍数/);
 assert.equal(s.fetchCalls.filter(call=>/lab\/start/.test(call.url)).length,0);
});
