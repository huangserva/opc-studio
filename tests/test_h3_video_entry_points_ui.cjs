/* H3 video entry points in the real workbench DOM (jsdom): the chat composer and the
   AI-video dialog render the catalog the backend publishes, the payload carries exactly
   what the user picked, and an out-of-range choice never reaches the network.
   It also pins the element wiring: each prefix's custom-size note and duration note must
   be the element inside that entry point, otherwise the two entries overwrite each other's
   hints and the user reads the wrong bounds.

   Run: node --test tests/test_h3_video_entry_points_ui.cjs
*/
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const {JSDOM}=require('jsdom');

const root=path.join(__dirname,'..');
const html=fs.readFileSync(process.env.H3_INDEX_HTML||path.join(root,'public/index.html'),'utf8');
const staticNotes={};                       // the placeholder text shipped in index.html
{
  const plain=new JSDOM(html);
  for(const id of ['chat-media-custom-note','media-custom-note','chat-media-frames-note','media-frames-note'])
    staticNotes[id]=plain.window.document.querySelector('#'+id)?.textContent;
  plain.window.close();
}
const h3Catalog={
  id:'h3',name:'MiniMax H3 · 4090',kind:'video',steps:8,
  sizes:['768x448','448x768','768x432','1024x576','432x768','576x1024','768x576','576x768','768x512','512x768','512x512','768x768'],
  size_labels:Object.fromEntries([['768x448','12:7'],['448x768','7:12'],['768x432','16:9'],['1024x576','16:9'],
    ['432x768','9:16'],['576x1024','9:16'],['768x576','4:3'],['576x768','3:4'],['768x512','3:2'],['512x768','2:3'],
    ['512x512','1:1'],['768x768','1:1']].map(([size,ratio])=>[size,`${ratio} · ${size}`])),
  size_groups:[{name:'已实测（产物 ffprobe 核对）',sizes:['768x448','448x768']},
    {name:'16:9 / 9:16',sizes:['768x432','1024x576','432x768','576x1024']},
    {name:'4:3 / 3:4',sizes:['768x576','576x768']},{name:'3:2 / 2:3',sizes:['768x512','512x768']},
    {name:'1:1',sizes:['512x512','768x768']}],
  custom_size:{min:256,max:1024,step:32,max_area:589824},
  size_hint:'H3 自定义宽高须为 256–1024 像素、32 的倍数，且面积不超过 589824 像素（即 1024×576）',
  sizes_measured:['768x448','448x768'],
  frames:[...Array(19)].map((_,index)=>39+index*17),
  frames_min:39,frames_max:345,frames_step:17,frames_measured:[56,124],fps:24,
  constraint_note:'尺寸按 32 像素对齐，可选 256–1024 像素且面积≤1024×576；时长 39–345 帧（24 FPS，按 17 帧递增）。'
    +'实测按组合逐项核对：只有列在“已实测组合”里的搭配经过产物核对，其余组合允许提交但未经逐项实测；'
    +'尺寸或时长上的“已实测”标记只代表该单项曾出现在实测组合中，不代表任意搭配都已验证。',
  workflows:[{id:'t2v',name:'文生视频'},{id:'i2v',name:'图生视频'},{id:'r2v',name:'多参考素材生视频'}],
  checkpoints:[{id:'singularity',name:'Singularity',modes:['t2v','i2v','r2v']}],
  accelerators:[{id:'ref8',name:'ref2v Turbo · 8 步',steps:8,modes:['t2v','i2v','r2v']}]
};
const qi21={id:'qi21',name:'Qwen Image 2.1',kind:'image',steps:20,sizes:['1024x1024']};

function setup(t,{preferences=null,jobs=[]}={}){
  const dom=new JSDOM(html,{url:'http://127.0.0.1:8766/',runScripts:'outside-only'});
  t.after(()=>dom.window.close());
  const w=dom.window,context=dom.getInternalVMContext(),calls=[];let timer=0;
  w.state={page:'library',dirty:false};w.assistantState={busy:false,config:{configured:true}};
  w.writingWorkflow={selected:false};w.selectWritingWorkflow=selected=>{w.writingWorkflow.selected=selected;};
  w.assistantContext=()=>({view:'library'});w.openAssistant=async()=>{};w.jumpToLatestAssistant=()=>{};
  w.renderLoraFields=()=>{};w.selectedLoraParameters=()=>({});w.updateLoraEnabled=()=>{};
  w.renderMediaProgress=()=>{};w.markMediaProgressUnavailable=()=>{};w.attachMediaProgressHost=()=>{};
  w.setTimeout=()=>++timer;w.clearTimeout=()=>{};
  w.HTMLDialogElement.prototype.showModal=function(){this.open=true;};
  w.HTMLDialogElement.prototype.close=function(){this.open=false;};
  w.fetch=async(url,options={})=>{
    const payload=options.body?JSON.parse(options.body):undefined;calls.push({url,payload});
    let data;
    if(url==='/api/generation')data={models:[h3Catalog,qi21],jobs,busy:false,
      preferences:preferences||{video:{model:'h3',size:'768x448',frames:56},image:{model:'qi21',size:'1024x1024'}},
      image_skill:{ready:false,styles:[]}};
    else if(url.startsWith('/api/generation/job/'))data={id:url.split('/').pop(),kind:'video',status:'queued',
      model:'h3',model_name:'H3',prompt:'neutral test scene',settings:{size:'768x448',frames:56,seed:1}};
    else if(url==='/api/generation/preferences')data={ok:true};
    else if(url==='/api/generation/start')data={id:'e'.repeat(32)};
    else if(url==='/api/assistant/send')data={media_job:'e'.repeat(32)};
    else throw new Error('Unexpected test request '+url);
    return {ok:true,json:async()=>data};
  };
  for(const name of ['lab-core.js','media-size.js','media-progress.js','generation.js','media-composer.js'])
    vm.runInContext(fs.readFileSync(path.join(root,'public',name),'utf8'),context,{filename:name});
  w.fixtureJobs=jobs;
  const run=code=>vm.runInContext(code,context),query=selector=>w.document.querySelector(selector);
  return {w,run,query,calls,
    chat:()=>JSON.parse(run('JSON.stringify(composerParameters())')),
    sent:url=>calls.filter(call=>call.url===url).map(call=>call.payload)};
}

test('提示元素归属：每个入口只写自己容器内的提示（防止两个入口互相污染）',async t=>{
  const s=setup(t);
  const doc=s.w.document;
  // the element each prefix writes must be inside that entry point's own container
  assert.ok(doc.querySelector('#chat-media-custom-size #chat-media-custom-note'),
    'the composer hint belongs to the composer');
  assert.ok(doc.querySelector('#media-custom-size #media-custom-note'),
    'the AI-video dialog hint belongs to the dialog');
  assert.ok(doc.querySelector('#chat-media-settings #chat-media-frames-note'));
  assert.ok(doc.querySelector('#media-video-fields #media-frames-note'));
  assert.equal(doc.querySelectorAll('#chat-media-custom-note').length,1);
  assert.equal(doc.querySelectorAll('#media-custom-note').length,1);
  await s.run('chooseMediaTool("video")');
  assert.equal(doc.querySelector('#chat-media-custom-size #chat-media-custom-note').textContent,
    h3Catalog.size_hint);
  assert.equal(doc.querySelector('#media-custom-size #media-custom-note').textContent,
    staticNotes['media-custom-note'],'the dialog still shows its own placeholder');
  await s.run('openMediaConnection("video")');
  assert.equal(doc.querySelector('#media-custom-size #media-custom-note').textContent,h3Catalog.size_hint);
  assert.equal(doc.querySelector('#chat-media-custom-size #chat-media-custom-note').textContent,
    h3Catalog.size_hint,'the composer keeps the hint it wrote earlier');
});

test('独立生成页同样把提示放在自己的容器内',async t=>{
  const lab=fs.readFileSync(path.join(root,'public/lab.html'),'utf8');
  const dom=new JSDOM(lab);t.after(()=>dom.window.close());
  const doc=dom.window.document;
  assert.ok(doc.querySelector('#media-custom-size #media-custom-note'),
    'lab writes its own custom-size hint');
  assert.ok(doc.querySelector('#lab-form #lab-frames-note'),'lab has its own duration note');
  assert.equal(doc.querySelector('#lab-frames').innerHTML.trim(),'','options come from the catalog, not hardcoded');
  assert.equal(doc.querySelector('#media-size').innerHTML.trim(),'');
});

test('聊天入口渲染后端目录：19 档真实秒数、12 尺寸 5 组、真实比例与已实测标注',async t=>{
  const s=setup(t);
  await s.run('chooseMediaTool("video")');
  const frames=[...s.query('#chat-media-frames').options];
  assert.deepEqual(frames.map(o=>Number(o.value)),[...Array(19)].map((_,i)=>39+i*17));
  assert.match(frames.find(o=>o.value==='175').textContent,/约 7\.3 秒 · 175 帧/);
  assert.deepEqual(frames.filter(o=>/已实测/.test(o.textContent)).map(o=>Number(o.value)),[56,124],
    'only the measured durations may claim 已实测');
  const sizes=[...s.query('#chat-media-size').options];
  assert.equal(sizes.filter(o=>o.value!=='custom').length,12);
  assert.deepEqual([...s.query('#chat-media-size').querySelectorAll('optgroup')].map(g=>g.label),
    h3Catalog.size_groups.map(g=>g.name));
  assert.equal(sizes.find(o=>o.value==='768x448').textContent,'12:7 · 768x448','768x448 is not 16:9');
  assert.equal(sizes.find(o=>o.value==='1024x576').textContent,'16:9 · 1024x576');
  assert.equal(s.query('#chat-media-custom-note').textContent,h3Catalog.size_hint);
  assert.equal(s.query('#chat-media-frames-note').textContent,h3Catalog.constraint_note);
  assert.equal(s.query('#media-custom-note').textContent,staticNotes['media-custom-note'],
    'the chat entry must not write into the AI-video dialog');
  assert.equal(s.query('#media-frames-note').textContent,staticNotes['media-frames-note']);
  for(const axis of ['width','height']){
    const input=s.query('#chat-media-'+axis);
    assert.equal(input.min,'256');assert.equal(input.max,'1024');assert.equal(input.step,'32');
  }
});

test('聊天入口越界自定义宽高只提示真实边界，不发请求也不建任务',async t=>{
  const s=setup(t);
  await s.run('chooseMediaTool("video")');
  s.query('#chat-media-size').value='custom';
  s.run("updateCustomSize('chat-media')");
  s.query('#chat-media-width').value='1024';s.query('#chat-media-height').value='608';
  assert.throws(()=>s.chat(),/589824/);
  await assert.rejects(()=>s.run('submitComposerMedia("中性测试场景")'),/589824/);
  assert.deepEqual(s.sent('/api/assistant/send'),[],'an invalid size must never reach the server');
  assert.deepEqual(s.sent('/api/generation/start'),[]);
  s.query('#chat-media-height').value='576';
  assert.equal(s.chat().size,'1024x576','the same fields become valid once inside the published area cap');
});

test('聊天入口选择 175 帧与自定义 800x480，提交 payload 与界面完全一致',async t=>{
  const s=setup(t);
  await s.run('chooseMediaTool("video")');
  s.query('#chat-media-frames').value='175';
  s.query('#chat-media-frames').dispatchEvent(new s.w.Event('change'));
  s.query('#chat-media-size').value='custom';
  s.run("updateCustomSize('chat-media')");
  s.query('#chat-media-width').value='800';s.query('#chat-media-height').value='480';
  await s.run('submitComposerMedia("中性测试场景")');
  const [request]=s.sent('/api/assistant/send');
  assert.equal(request.media.frames,175);assert.equal(request.media.size,'800x480');
  assert.equal(request.media.model,'h3');
  assert.equal(request.media.frames%17,5,'the ladder is what the backend enforces');
});

test('沿用历史任务与刷新偏好保留原帧数原尺寸，不静默回到 56',async t=>{
  const source={id:'a'.repeat(32),kind:'video',status:'done',model:'h3',model_name:'H3',prompt:'中性测试场景',
    settings:{workflow:'i2v',video_checkpoint:'singularity',video_accelerator:'ref8',size:'448x768',frames:124,seed:7}};
  const s=setup(t,{jobs:[source],preferences:{video:{model:'h3',size:'1024x576',frames:175},image:{}}});
  await s.run('repeatMediaJob(fixtureJobs[0])');
  assert.equal(s.query('#chat-media-frames').value,'124');
  assert.equal(s.query('#chat-media-size').value,'448x768');
  assert.equal(s.chat().frames,124);assert.equal(s.chat().size,'448x768');
  await s.run('chooseMediaTool("video")');
  assert.equal(s.query('#chat-media-frames').value,'175','a stored 175-frame draft reloads as 175');
  assert.equal(s.query('#chat-media-size').value,'1024x576');
});

test('AI 视频弹窗与聊天入口共用同一目录，提示元素互不污染',async t=>{
  const s=setup(t);
  await s.run('openMediaConnection("video")');
  const frames=[...s.query('#media-frames').options];
  assert.deepEqual(frames.map(o=>Number(o.value)),[...Array(19)].map((_,i)=>39+i*17));
  assert.equal(s.query('#media-custom-note').textContent,h3Catalog.size_hint);
  assert.equal(s.query('#media-frames-note').textContent,h3Catalog.constraint_note);
  assert.equal(s.query('#chat-media-custom-note').textContent,staticNotes['chat-media-custom-note'],
    'the dialog must not write into the chat composer');
  assert.equal(s.query('#chat-media-frames-note').textContent,staticNotes['chat-media-frames-note']);
  s.query('#media-frames').value='39';
  s.query('#media-size').value='768x448';
  await s.query('#media-form').onsubmit({preventDefault(){}});
  const [start]=s.sent('/api/generation/start').slice(-1);
  assert.equal(start.frames,39);assert.equal(start.size,'768x448');assert.equal(start.model,'h3');
  const [preference]=s.sent('/api/generation/preferences').slice(-1);
  assert.equal(preference.frames,39);assert.equal(preference.size,'768x448');
});

test('沿用 i2v 参考工作流：聊天入口只列 32 对齐尺寸，合法值可发、非对齐预设不可提交',async t=>{
  const source={id:'a'.repeat(32),kind:'video',status:'done',model:'h3',model_name:'H3',prompt:'中性测试场景',
    settings:{workflow:'i2v',video_checkpoint:'singularity',video_accelerator:'ref8',size:'448x768',frames:124,seed:7}};
  const s=setup(t,{jobs:[source]});
  await s.run('repeatMediaJob(fixtureJobs[0])');
  const sizes=[...s.query('#chat-media-size').options].map(o=>o.value);
  assert.ok(!sizes.includes('768x432')&&!sizes.includes('432x768'),
    'a reused reference workflow must not offer the non-32-aligned presets');
  assert.ok(sizes.includes('1024x576')&&sizes.includes('448x768'),'32-aligned presets stay selectable');
  assert.equal(s.query('#chat-media-size').value,'448x768','the original legal size is kept');
  s.query('#chat-media-size').value='1024x576';
  await s.run('submitComposerMedia("中性测试场景")');
  const [request]=s.sent('/api/assistant/send');
  assert.equal(request.media.workflow,'i2v');assert.equal(request.media.size,'1024x576');
  assert.equal(request.media.frames,124);
  // a stale tab or tampered DOM carrying a non-aligned preset is refused before any request
  const option=s.w.document.createElement('option');
  option.value='768x432';option.textContent='16:9 · 768x432';
  s.query('#chat-media-size').appendChild(option);s.query('#chat-media-size').value='768x432';
  await assert.rejects(()=>s.run('submitComposerMedia("中性测试场景")'),/32 的像素倍数/);
  assert.equal(s.sent('/api/assistant/send').length,1,'the incompatible choice never reaches the server');
  assert.equal(s.sent('/api/generation/start').length,0);
});

test('文生视频沿用仍保留 768x432 / 432x768（只有参考类工作流过滤）',async t=>{
  const source={id:'b'.repeat(32),kind:'video',status:'done',model:'h3',model_name:'H3',prompt:'中性测试场景',
    settings:{workflow:'t2v',video_checkpoint:'singularity',video_accelerator:'ref8',size:'768x432',frames:56,seed:3}};
  const s=setup(t,{jobs:[source]});
  await s.run('repeatMediaJob(fixtureJobs[0])');
  const sizes=[...s.query('#chat-media-size').options].map(o=>o.value);
  assert.ok(sizes.includes('768x432')&&sizes.includes('432x768'));
  assert.equal(s.query('#chat-media-size').value,'768x432');
  await s.run('submitComposerMedia("中性测试场景")');
  const [request]=s.sent('/api/assistant/send');
  assert.equal(request.media.workflow,'t2v');assert.equal(request.media.size,'768x432');
});

test('沿用 r2v 任务：AI 视频弹窗同样过滤尺寸并提示不可用原值',async t=>{
  const source={id:'c'.repeat(32),kind:'video',status:'done',model:'h3',model_name:'H3',prompt:'中性测试场景',
    settings:{workflow:'r2v',video_checkpoint:'singularity',video_accelerator:'ref8',size:'768x512',frames:56,seed:9,
      references:{images:['r'.repeat(32)]}}};
  const s=setup(t,{jobs:[source]});
  await s.run('openMediaConnection("video")');
  s.run('fillMediaJobForm(fixtureJobs[0])');
  const sizes=[...s.query('#media-size').options].map(o=>o.value);
  assert.ok(!sizes.includes('768x432')&&!sizes.includes('432x768'),'the dialog filters the same way');
  assert.equal(s.query('#media-size').value,'768x512','the reused size stays');
  assert.equal(s.query('#media-size-note').textContent,'','a legal reuse needs no warning');
  // a history record whose size is illegal for its workflow gets an explicit, correctable hint
  s.run(`mediaState.videoSettings=${JSON.stringify({workflow:'r2v',video_checkpoint:'singularity',video_accelerator:'ref8'})};`);
  s.run('updateMediaSizes({size:"768x432"})');
  assert.match(s.query('#media-size-note').textContent,/不适用于 r2v 工作流/);
  assert.match(s.query('#media-size-note').textContent,/32 对齐的尺寸/);
  assert.notEqual(s.query('#media-size').value,'768x432','the illegal value is not silently kept selected');
  await s.query('#media-form').onsubmit({preventDefault(){}});
  assert.deepEqual(s.sent('/api/generation/start'),[],'an illegal size never starts a job');
  assert.deepEqual(s.sent('/api/generation/preferences'),[],'and never writes a preference either');
  // the user can correct it: a legal 1024x576 goes through with the reused workflow intact
  s.query('#media-size').value='1024x576';
  await s.query('#media-form').onsubmit({preventDefault(){}});
  const [start]=s.sent('/api/generation/start');
  assert.equal(start.size,'1024x576');assert.equal(start.workflow,'r2v');
});

test('弹窗不列出非法时长；被篡改的非法时长只提示边界，不创建 GPU 任务',async t=>{
  const s=setup(t);
  await s.run('openMediaConnection("video")');
  const options=[...s.query('#media-frames').options];
  assert.ok(options.length===19&&options.every(o=>{const f=Number(o.value);
    return f>=39&&f<=345&&(f-39)%17===0;}),'the select only offers the published ladder');
  // tamper with the DOM the way a stale tab could: an option that the contract forbids
  const illegal=s.w.document.createElement('option');
  illegal.value='40';illegal.textContent='约 1.7 秒 · 40 帧';
  s.query('#media-frames').appendChild(illegal);s.query('#media-frames').value='40';
  await s.query('#media-form').onsubmit({preventDefault(){}});
  assert.deepEqual(s.sent('/api/generation/start'),[],'an illegal duration must not start a job');
  assert.match(s.query('#media-connection-status').textContent,/39–345/);
  assert.deepEqual(s.sent('/api/generation/preferences'),[],'and no preference write either');
});
