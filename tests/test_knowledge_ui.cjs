const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs');
const {JSDOM}=require('jsdom');
const tick=()=>new Promise(resolve=>setImmediate(resolve));
async function fixture(url='http://127.0.0.1:8766/knowledge.html',body=null,sourceOverrides={},documentOverrides={}){
  const dom=new JSDOM(fs.readFileSync('public/knowledge.html','utf8'),{url,runScripts:'outside-only'}),w=dom.window,d=w.document,calls=[];
  let savedItems=[];
  const doc={id:'a'.repeat(32),source:'wechat',kind:'wechat',title:'<img src=x onerror=alert(1)>',body:'😀微信经验<script>alert(1)</script>',hash:'hash',url:'http://127.0.0.1:3000/groups/g',modified:'2026-10-03T00:00:00Z',tags:['<img>'],provenance:{chatroom_id:'<script>bad()</script>',local_id:42,database_path:'/Users/test/Library/radar.db'}};
  if(body!==null)doc.body=body;
  Object.assign(doc,documentOverrides);
  w.setInterval=()=>0;
  w.HTMLDialogElement.prototype.showModal=function(){this.open=true;};w.HTMLDialogElement.prototype.close=function(){this.open=false;};
  w.fetch=async(url,options={})=>{
    const body=options.body?JSON.parse(options.body):undefined;calls.push({url,body});
    let data;
    if(url==='/api/knowledge')data={sources:[{id:'wechat',name:'微信',kind:'wechat',path:'/Users/test/Library/radar.db',status:'ready',count:1,synced_at:doc.modified,...sourceOverrides}],dossiers:[],total:1,syncing:false};
    else if(url.startsWith('/api/knowledge/search'))data={results:[{...doc,excerpt:doc.body,excerpt_start:0}],total:1,next_offset:null};
    else if(url.startsWith('/api/knowledge/document/'))data=doc;
    else if(url==='/api/knowledge/dossier'){if(body.items)savedItems=body.items.map(x=>({...doc,...x,excerpt:doc.body.slice(2,6)}));data={id:'b'.repeat(32),title:body.title,items:savedItems};}
    else data={};
    return {ok:true,json:async()=>data};
  };
  w.eval(fs.readFileSync('public/knowledge.js','utf8'));await tick();await tick();
  return {w,d,calls,doc};
}
test('share records show coverage and a validated fixed-version message link without fetching pages',async()=>{
  const ref={id:'c'.repeat(32),hash:'e'.repeat(64),start:0,end:12};
  const f=await fixture(undefined,null,{id:'wechat-links',name:'微信分享链接 · 雷达',kind:'wechat_links'},{source:'wechat-links',kind:'wechat_links',body:'微信分享记录；外部网页正文未读取',provenance:{sourceUrl:'https://example.test/a',content_scope:'分享字段，外部网页正文未读取',original_message_reference:ref,original_message_url:'javascript:bad()',source_record_hash:'f'.repeat(64)}});
  assert(f.d.querySelector('#kb-kind option[value="wechat_links"]'));
  assert.match(f.d.querySelector('#kb-source-config').textContent,/外部网页正文未读取/);
  f.d.querySelector('[data-preview]').click();await tick();
  assert.match(f.d.querySelector('#kb-document-meta').textContent,/微信分享链接/);
  assert.match(f.d.querySelector('#kb-document-provenance').textContent,/分享消息时间/);
  assert.match(f.d.querySelector('#kb-document-provenance').textContent,/分享链接（规范化）/);
  const link=f.d.querySelector('#kb-document-provenance a'),params=new URL(link.href).searchParams;
  assert.equal(params.get('document'),ref.id);assert.equal(params.get('hash'),ref.hash);assert.equal(params.get('start'),'0');assert.equal(params.get('end'),'12');
  assert(f.calls.every(call=>call.url.startsWith('/api/')));f.w.close();
});
test('malformed source-message references cannot create a misleading origin link',async()=>{
  const f=await fixture(undefined,null,{}, {kind:'wechat_links',provenance:{original_message_reference:{id:'c'.repeat(32),hash:'<svg>',start:true,end:12},original_message_url:'https://evil.test/fake'}});
  f.d.querySelector('[data-preview]').click();await tick();
  assert.equal(f.d.querySelector('#kb-document-provenance a'),null);f.w.close();
});
test('external bookmarks show author, acquisition path and own comment without claiming a saved body',async()=>{
  const f=await fixture(undefined,'',{id:'external-references',name:'外部文章收藏',kind:'manual',document_kind:'external'},
    {source:'external-references',kind:'external',provenance:{ownership:'third_party',author:'<script>合成作者</script>',author_handle:'test_author',platform:'X',discovered_via:'微信转发',discovery_detail:'合成转发来源',reader_note:'我自己的评论',content_scope:'仅收藏链接；原文正文未保存',published_on:''}});
  assert.match(f.d.querySelector('#kb-results').textContent,/他人作品/);assert.match(f.d.querySelector('#kb-results').textContent,/@test_author/);
  assert.match(f.d.querySelector('#kb-results').textContent,/我的评论：我自己的评论/);assert.equal(f.d.querySelector('[data-pick]').disabled,true);
  assert.equal(f.d.querySelector('#kb-results script'),null);assert.equal(f.d.querySelector('#kb-source-nav [data-source="external-references"]').dataset.kind,'external');
  f.d.querySelector('[data-preview]').click();await tick();assert.equal(f.d.querySelector('#kb-document-body').value,'');
  assert.match(f.d.querySelector('#kb-document-provenance').textContent,/收藏时间/);assert.match(f.d.querySelector('#kb-document-provenance').textContent,/合成转发来源/);
  assert.match(f.d.querySelector('#kb-document-dialog .kb-form-status').textContent,/原文正文未保存/);f.w.close();
});
test('untrusted document text is displayed as text and selection uses Unicode code points',async()=>{
  const f=await fixture();
  assert.equal(f.d.querySelectorAll('#kb-results img,#kb-results script').length,0);
  f.d.querySelector('[data-preview]').click();await tick();
  const textarea=f.d.querySelector('#kb-document-body');textarea.setSelectionRange(2,6);
  f.d.querySelector('#kb-pick-selection').click();
  assert.match(f.d.querySelector('#kb-budget').textContent,/1 个片段 · 4/);
  f.d.querySelector('#kb-dossier-title').value='微信资料';f.d.querySelector('#kb-save-dossier').click();await tick();await tick();
  const saved=f.calls.find(c=>c.url==='/api/knowledge/dossier').body;
  assert.deepEqual(saved.items,[{id:f.doc.id,start:1,end:5,hash:'hash'}]);
  f.w.close();
});
test('CRLF selection retains the raw source offsets instead of silently shifting the citation',async()=>{
  const f=await fixture(undefined,'😀前言\r\n交付项目\r\n尾声');
  f.d.querySelector('[data-preview]').click();await tick();
  const t=f.d.querySelector('#kb-document-body'),start=t.value.indexOf('交付');t.setSelectionRange(start,start+4);
  f.d.querySelector('#kb-pick-selection').click();
  f.d.querySelector('#kb-dossier-title').value='交付';f.d.querySelector('#kb-save-dossier').click();await tick();await tick();
  const pick=f.calls.find(c=>c.url==='/api/knowledge/dossier').body.items[0];
  assert.equal(pick.start,5);assert.equal(pick.end,9);
  assert.equal(Array.from(f.doc.body).slice(pick.start,pick.end).join(''),'交付项目');
  f.w.close();
});
test('unchanged saved evidence can build a draft after its original source changed',async()=>{
  const f=await fixture();
  f.d.querySelector('[data-pick]').click();
  f.d.querySelector('#kb-dossier-title').value='固定片段';
  f.d.querySelector('#kb-save-dossier').click();await tick();await tick();
  f.doc.hash='new-source-hash';f.doc.body='原文已经更新';
  f.d.querySelector('#kb-purpose').value='基于已保存快照复盘';
  f.d.querySelector('#kb-create-draft').click();await tick();await tick();
  const saves=f.calls.filter(c=>c.url==='/api/knowledge/dossier');
  assert.equal(saves.length,2);
  assert(!Object.hasOwn(saves[1].body,'items'));
  assert.equal(saves[1].body.purpose,'基于已保存快照复盘');
  assert(f.calls.some(c=>c.url==='/api/knowledge/draft'&&c.body.id==='b'.repeat(32)));
  f.w.close();
});
test('ordinary document links do not report a missing citation, but incomplete citation ranges still do',async()=>{
  const base='http://127.0.0.1:8766/knowledge.html?document='+'a'.repeat(32);
  const f=await fixture(base);
  assert(f.d.querySelector('#kb-document-dialog').open);
  assert.equal(f.d.querySelector('#kb-document-dialog .kb-form-status').textContent,'在原文中选中需要的片段，再收集。');
  f.w.close();
  const invalid=await fixture(base+'&start=1');
  assert.match(invalid.d.querySelector('#kb-document-dialog .kb-form-status').textContent,/引用范围无法定位/);
  invalid.w.close();
});
test('citation links select Unicode range and changed-source references do not highlight the wrong text',async()=>{
  const base='http://127.0.0.1:8766/knowledge.html?document='+'a'.repeat(32)+'&start=1&end=5&hash=';
  const f=await fixture(base+'hash'),t=f.d.querySelector('#kb-document-body');
  assert(f.d.querySelector('#kb-document-dialog').open);
  assert.equal(t.value.slice(t.selectionStart,t.selectionEnd),'微信经验');
  assert.match(f.d.querySelector('#kb-document-dialog .kb-form-status').textContent,/已定位引用片段/);
  f.w.close();
  const stale=await fixture(base+'old');
  assert.match(stale.d.querySelector('#kb-document-dialog .kb-form-status').textContent,/原资料已更新/);
  assert.equal(stale.d.querySelector('#kb-document-body').selectionStart,stale.d.querySelector('#kb-document-body').selectionEnd);
  stale.w.close();
});
test('filter and refresh preserve picked excerpts and no draft or model is sent automatically',async()=>{
  const f=await fixture();
  f.d.querySelector('[data-pick]').click();
  f.d.querySelector('#kb-kind').value='wechat';f.d.querySelector('#kb-kind').dispatchEvent(new f.w.Event('change'));await tick();
  assert.match(f.calls.at(-1).url,/kind=wechat/);
  assert.match(f.d.querySelector('#kb-budget').textContent,/1 个片段/);
  f.d.querySelector('[data-pick]').click();
  assert.match(f.d.querySelector('#kb-budget').textContent,/0 个片段/);
  assert(!f.calls.some(c=>/draft|assistant|completions/.test(c.url)));
  f.w.close();
});
test('source scope and original provenance remain visible without executing untrusted metadata',async()=>{
  const f=await fixture();
  f.d.querySelector('#kb-manage-sources').click();
  assert(f.d.querySelector('#kb-source-dialog').open);
  assert.match(f.d.querySelector('#kb-source-config').textContent,/微信雷达已有数据库快照/);
  assert.match(f.d.querySelector('#kb-source-config').textContent,/\/Users\/test\/Library\/radar.db/);
  f.d.querySelector('[data-preview]').click();await tick();
  const metadata=f.d.querySelector('#kb-document-provenance');
  assert.match(metadata.textContent,/消息编号42/);
  assert.match(metadata.textContent,/<script>bad\(\)<\/script>/);
  assert.equal(metadata.querySelectorAll('script,img').length,0);
  f.d.querySelector('[data-sync-source]').click();await tick();await tick();
  assert.deepEqual(f.calls.find(c=>c.url==='/api/knowledge/sync'&&c.body?.source==='wechat').body,{source:'wechat'});
  f.w.close();
});
test('new message capture is explicit and has a separate clock and hourly opt-in from cache indexing',async()=>{
  const f=await fixture(undefined,null,{backend:'collector',synced_at:'2026-10-05T01:15:00+08:00',capture:{available:true,status:'done',last_success:{captured_at:'2026-10-05T00:50:00+08:00',index_status:'ready'}}});
  f.d.querySelector('#kb-manage-sources').click();
  const text=f.d.querySelector('#kb-source-config').textContent;
  assert.match(text,/最近成功采集：2026\/10\/5 00:50:00/);
  assert.match(text,/最近更新索引：2026\/10\/5 01:15:00/);
  assert(!f.d.querySelector('[name=capture_enabled]').checked);
  f.d.querySelector('[data-sync-source]').click();await tick();await tick();
  assert(!f.calls.some(c=>c.url==='/api/knowledge/wechat/capture'));
  f.d.querySelector('[data-capture-source]').click();await tick();await tick();
  assert.deepEqual(f.calls.find(c=>c.url==='/api/knowledge/wechat/capture').body,{});
  assert(!f.calls.some(c=>/assistant|completions/.test(c.url)));
  const form=f.d.querySelector('[data-source-options]');form.elements.capture_enabled.checked=true;
  form.dispatchEvent(new f.w.Event('submit',{cancelable:true}));await tick();await tick();
  assert.equal(f.calls.find(c=>c.url==='/api/knowledge/source').body.capture_enabled,true);
  f.w.close();
});
test('capture unavailable or busy cannot be started and untrusted diagnostics remain text',async()=>{
  for(const capture of [{available:false,status:'unavailable'},{available:true,busy:true,status:'running',detail:'<img onerror=alert(1)>'}]){
    const f=await fixture(undefined,null,{backend:'collector',capture});
    assert(f.d.querySelector('[data-capture-source]').disabled);
    assert.equal(f.d.querySelectorAll('#kb-source-config img,#kb-source-status img').length,0);
    if(!capture.available)assert(f.d.querySelector('[name=capture_enabled]').disabled);
    f.w.close();
  }
});

// ── fixes-intake-next-step-a：从创意回收接过来的写作闭环（方案 A）──────────────
// 验收对照 coordination/design-intake-next-step-report.md §4 与 §6 的 A 用例清单。
const okJson=data=>({ok:true,json:async()=>data});
const badJson=(error,code=400)=>({ok:false,status:code,json:async()=>({error})});
const HEX=n=>n.repeat(32);
const HANDOFF='http://127.0.0.1:8766/knowledge.html?document='+HEX('a')+'&intake='+HEX('c')+'&angle=0&analysis_at='+encodeURIComponent('2026-10-09T02:00:00+00:00');
async function intakeFixture(url=HANDOFF,options={}){
  const dom=new JSDOM(fs.readFileSync('public/knowledge.html','utf8'),{url,runScripts:'outside-only'}),w=dom.window,d=w.document,calls=[],gate={};
  const doc={id:HEX('a'),source:'wechat',kind:'wechat',title:'码头笔记原文',body:'😀黄昏码头的水面把天空再复制一遍，人物只留背影。',hash:'doc-hash',url:'',modified:'2026-10-03T00:00:00Z',tags:[],provenance:{}};
  Object.assign(doc,options.document||{});
  const drop={id:HEX('c'),kind:'link',status:'resolved',outcome:'stored',error:'',document_id:doc.id,reason:'这个开头方式可以借',
    analysis:{status:'done',at:'2026-10-09T02:00:00+00:00',fingerprint:'',angles:[{title:'写一条蹲拍笔记',how:'从水面倒影切入，交代蹲位与光线'}],summary:'模型摘要',points:[],why:'',related:[],cards:0}};
  if(options.drop===null)delete drop.id;
  else Object.assign(drop,options.drop||{});
  for(const key of options.dropOmits||[])delete drop[key];   // 追加：模拟回执真的不带这个字段
  w.setInterval=()=>0;
  w.HTMLDialogElement.prototype.showModal=function(){this.open=true;};w.HTMLDialogElement.prototype.close=function(){this.open=false;};
  if(options.longTimerNow){const real=w.setTimeout.bind(w);w.setTimeout=(handler,ms)=>(ms>=60000?(handler(),0):real(handler,ms));}
  w.fetch=async(url,opts={})=>{
    const target=String(url);calls.push({url:target,method:opts.method||'GET',body:opts.body?JSON.parse(opts.body):undefined});
    if(target==='/api/knowledge')return okJson({sources:[{id:'wechat',name:'微信',kind:'wechat',status:'ready',count:1,synced_at:doc.modified}],dossiers:[],total:1,syncing:false});
    if(target.startsWith('/api/knowledge/search'))return okJson({results:[],total:0,next_offset:null});
    if(target.startsWith('/api/knowledge/document/'))return options.documentFails?badJson('资料不存在或已被删除',404):okJson(doc);
    if(target.startsWith('/api/intake/'))return options.drop===null?badJson('回收项不存在',404):okJson(drop);
    if(target==='/api/knowledge/dossier'){
      if(options.dossierFails)return badJson('主题名称不能为空');
      return okJson({id:HEX('d'),title:(opts.body&&JSON.parse(opts.body).title)||'',purpose:(opts.body&&JSON.parse(opts.body).purpose)||'',
        items:((opts.body&&JSON.parse(opts.body).items)||[]).map(x=>({...x,title:doc.title,source:doc.source,kind:doc.kind,provenance:doc.provenance,excerpt:Array.from(doc.body).slice(x.start,x.end).join('')}))});
    }
    if(target==='/api/knowledge/draft'){
      if(options.draftNever){
        return new Promise((resolve,reject)=>{
          const fail=()=>{const error=new Error('已中止');error.name='AbortError';reject(error);};
          if(opts.signal&&opts.signal.aborted)fail();else if(opts.signal)opts.signal.addEventListener('abort',fail);
        });
      }
      if(options.draftBadJson)return {ok:true,json:()=>{throw new SyntaxError('Unexpected token <');}};
      if(options.draftFails)return badJson('作品目录写入失败',500);
      // gate 放在 response.json 上（不是 fetch 返回）：测试名说的「响应体读完前只发一次」才是实际受测路径。
      if(options.draftGate)return {ok:true,json:()=>new Promise(resolve=>{gate.draft=()=>resolve({id:HEX('e'),url:'/workspace.html?slug=x'});})};
      return okJson({id:HEX('e'),url:'/workspace.html?slug=x'});
    }
    return badJson('接口不存在',404);
  };
  w.eval(fs.readFileSync('public/knowledge.js','utf8'));
  for(let i=0;i<12;i++)await tick();
  const pick=(start,end)=>{const t=d.querySelector('#kb-document-body');t.setSelectionRange(start,end);d.querySelector('#kb-pick-selection').click();};
  return {w,d,calls,doc,drop,gate,pick,status:()=>d.querySelector('#kb-status').textContent,guide:()=>d.querySelector('#kb-intake-guide').textContent};
}

test('an intake handoff verifies the drop by read-only GET and prefills an editable goal only',async()=>{
  const f=await intakeFixture();
  assert.ok(f.calls.some(c=>c.url==='/api/intake/'+HEX('c')&&c.method==='GET'),'核对回收项只走现有 GET');
  const guide=f.guide();
  assert.match(guide,/核对资料/);assert.match(guide,/选择片段/);assert.match(guide,/建底稿/);
  assert.equal(f.d.querySelector('#kb-dossier-title').value,'写一条蹲拍笔记');
  assert.equal(f.d.querySelector('#kb-purpose').value,'从水面倒影切入，交代蹲位与光线');
  assert.equal(f.d.querySelector('#kb-purpose-ai-note').hidden,false,'AI 建议固定标未核实');
  assert.match(f.d.querySelector('#kb-intake-meta').textContent,/我的收藏理由/);
  assert.match(f.d.querySelector('#kb-intake-meta').textContent,/这个开头方式可以借/);
  assert.match(f.d.querySelector('#kb-document-body').value,/黄昏码头/, '原文照常可读');
  assert.match(f.d.querySelector('#kb-budget').textContent,/0 个片段/);
  assert(!f.calls.some(c=>/dossier|draft|assistant|completions/.test(c.url)),'跳转不建资料夹、不建底稿、不跑模型');
  assert.equal(f.w.localStorage.length,0,'不写 localStorage');
  assert.doesNotMatch(f.w.location.search,/黄昏|水面|摘要|开头方式/,'正文与理由不进 URL');
  f.w.close();
});

test('a stale analysis version or an out-of-range angle is refused instead of reusing the old angle',async()=>{
  for(const query of ['&angle=0&analysis_at='+encodeURIComponent('2026-01-01T00:00:00+00:00'),'&angle=5&analysis_at='+encodeURIComponent('2026-10-09T02:00:00+00:00'),'&angle=0']){
    const url='http://127.0.0.1:8766/knowledge.html?document='+HEX('a')+'&intake='+HEX('c')+query;
    const drop=query.endsWith('angle=0')?{analysis:{status:'queued'}}:undefined;
    const f=await intakeFixture(url,drop?{drop}:{});
    assert.match(f.guide(),/分析已更新，请重新选角度/);
    assert.equal(f.d.querySelector('#kb-dossier-title').value,'');
    assert.equal(f.d.querySelector('#kb-purpose').value,'');
    assert.equal(f.d.querySelector('#kb-purpose-ai-note').hidden,true);
    f.w.close();
  }
});

test('a drop pointing at another document or a malformed query cannot prefill or inject markup',async()=>{
  const other=await intakeFixture(HANDOFF,{drop:{document_id:HEX('f')}});
  assert.match(other.guide(),/这条回收项对应的资料不是当前打开的这篇/);
  assert.equal(other.d.querySelector('#kb-purpose').value,'');
  other.w.close();
  const malicious='http://127.0.0.1:8766/knowledge.html?document='+HEX('a')+'&intake=%3Cscript%3Ebad()%3C%2Fscript%3E&angle=-1&analysis_at=%3Cimg%3E';
  const f=await intakeFixture(malicious);
  assert.match(f.guide(),/回收项编号无效/);
  assert(!f.calls.some(c=>c.url.startsWith('/api/intake/')),'编号无效就不去读');
  assert.equal(f.d.querySelectorAll('.kb-intake-guide img,.kb-intake-guide script').length,0);
  assert.equal(f.d.querySelector('#kb-purpose').value,'');
  f.w.close();
  const badAngle=await intakeFixture('http://127.0.0.1:8766/knowledge.html?document='+HEX('a')+'&intake='+HEX('c')+'&angle=-1');
  assert.match(badAngle.guide(),/角度编号无效/);assert.equal(badAngle.d.querySelector('#kb-purpose').value,'');badAngle.w.close();
});

test('prefilled angle wording never becomes a citation and cancelling the selection posts nothing',async()=>{
  const f=await intakeFixture();
  f.d.querySelector('#kb-create-draft').click();await tick();await tick();
  assert(!f.calls.some(c=>/dossier|draft/.test(c.url)),'没选片段时不自动挑片段、不发请求');
  assert.match(f.status(),/先在原文里选中要引用的片段/);
  f.pick(2,6);
  assert.match(f.d.querySelector('#kb-budget').textContent,/1 个片段/);
  f.d.querySelector('#kb-save-dossier').click();await tick();await tick();
  const saved=f.calls.find(c=>c.url==='/api/knowledge/dossier').body;
  assert.equal(saved.items.length,1);
  assert.deepEqual(Object.keys(saved.items[0]).sort(),['end','hash','id','start']);
  assert.deepEqual([saved.items[0].start,saved.items[0].end],[1,5]);
  assert.match(f.d.querySelector('#kb-picks').textContent,/黄昏码头/,'片段文字来自原文选择');
  assert.doesNotMatch(JSON.stringify(saved.items),/蹲拍笔记|水面倒影切入|写一条/,'角度与做法文字不能当原文片段进 items');
  f.d.querySelector('#kb-picks button').click();   // 移除已选片段
  assert.match(f.d.querySelector('#kb-budget').textContent,/0 个片段/);
  f.d.querySelector('#kb-create-draft').click();await tick();await tick();
  assert.equal(f.calls.filter(c=>c.url==='/api/knowledge/draft').length,0,'取消选择不 POST');
  f.w.close();
});

test('a draft that never answers is unconfirmed, keeps the saved dossier and is never resent',async()=>{
  const f=await intakeFixture(HANDOFF,{draftNever:true,longTimerNow:true});
  f.pick(1,5);
  f.d.querySelector('#kb-create-draft').click();await tick();await tick();
  assert.equal(f.calls.filter(c=>c.url==='/api/knowledge/draft').length,1,'超时不自动重发');
  assert.match(f.status(),/创建结果未确认，请先到我的作品查看/);
  const note=f.d.querySelector('#kb-draft-note');
  assert.equal(note.hidden,false);
  assert.equal(note.dataset.dossier,HEX('d'),'资料夹编号保留');
  assert.match(note.textContent,/资料夹已保存/);
  assert.ok([...note.querySelectorAll('button')].some(b=>b.textContent==='打开这个资料夹'),'留打开资料夹的入口');
  assert(!f.calls.some(c=>/assistant|completions/.test(c.url)));
  f.w.close();
});

test('an unreadable draft receipt is unconfirmed and releases busy so the user can decide',async()=>{
  const f=await intakeFixture(HANDOFF,{draftBadJson:true});
  f.pick(1,5);
  f.d.querySelector('#kb-create-draft').click();await tick();await tick();
  assert.match(f.status(),/创建结果未确认，请先到我的作品查看/);
  assert.equal(f.d.querySelector('#kb-create-draft').disabled,false,'busy 要覆盖到 response.json 再释放');
  assert.equal(f.calls.filter(c=>c.url==='/api/knowledge/draft').length,1);
  f.w.close();
});

test('a failed dossier save never reaches the draft call',async()=>{
  const f=await intakeFixture(HANDOFF,{dossierFails:true});
  f.pick(1,5);
  f.d.querySelector('#kb-create-draft').click();await tick();await tick();
  assert.equal(f.calls.filter(c=>c.url==='/api/knowledge/draft').length,0);
  assert.match(f.status(),/资料夹没保存成功/);
  f.w.close();
});

test('double clicking the draft button sends one request until the response body finishes',async()=>{
  const f=await intakeFixture(HANDOFF,{draftGate:true});
  f.pick(1,5);
  f.d.querySelector('#kb-create-draft').click();
  f.d.querySelector('#kb-create-draft').click();
  await tick();await tick();
  assert.equal(f.calls.filter(c=>c.url==='/api/knowledge/draft').length,1);
  f.gate.draft();for(let i=0;i<4;i++)await tick();
  assert.equal(f.d.querySelector('#kb-create-draft').disabled,false);
  f.w.close();
});

test('a document that cannot be read blocks drafting, shows no stale content and offers the recycle entry',async()=>{
  const f=await intakeFixture(HANDOFF,{documentFails:true});
  assert.equal(f.d.querySelector('#kb-document-title').textContent,'');
  assert.equal(f.d.querySelector('#kb-document-body').value,'');
  assert.equal(f.d.querySelector('#kb-document-provenance').textContent,'');
  assert.match(f.guide(),/这篇资料读不到/);
  const links=[...f.d.querySelectorAll('#kb-intake-actions a')].map(a=>a.getAttribute('href'));
  assert(links.includes('/intake.html')&&links.includes('/knowledge.html'));
  f.d.querySelector('#kb-create-draft').click();await tick();await tick();
  assert(!f.calls.some(c=>/dossier|draft/.test(c.url)),'读不到原文就不能建底稿');
  assert.match(f.status(),/不能建底稿/);
  f.w.close();
});

test('selection inside an intake-opened document stays on Unicode code points and the 12,000 limit',async()=>{
  const f=await intakeFixture(HANDOFF,{document:{body:'😀黄昏码头的水面把天空再复制一遍'}});
  f.pick(2,6);
  const t=f.d.querySelector('#kb-document-body');
  assert.equal(t.value.slice(t.selectionStart,t.selectionEnd),'黄昏码头');
  f.d.querySelector('#kb-dossier-title').value='蹲拍';f.d.querySelector('#kb-save-dossier').click();await tick();await tick();
  const saved=f.calls.find(c=>c.url==='/api/knowledge/dossier').body.items[0];
  assert.deepEqual([saved.start,saved.end,saved.hash],[1,5,'doc-hash'],'偏移按 Unicode 码点，不带 emoji 的代理对');
  assert.equal(Array.from(f.doc.body).slice(saved.start,saved.end).join(''),'黄昏码头');
  f.w.close();
  const long=await intakeFixture(HANDOFF,{document:{body:'水'.repeat(12001)}});
  long.pick(0,12001);
  assert.match(long.status(),/最多选择 20 个片段、合计 12,000 字/);
  assert.match(long.d.querySelector('#kb-budget').textContent,/0 个片段/);
  long.w.close();
});

// ── fixes-intake-next-step-a2：预填前的文档归属严格核对（Codex 复核 1M）────────────
// 依据 coordination/review-intake-next-step-a-report.md §1：回执 document_id 缺失、空串、null、
// 类型不对都不能当「一致」；必须 query document 与回执 document_id 都是有效编号且严格相等才预填。
const OWNERSHIP_BAD=[
  ['为空串',{document_id:''}],
  ['为 null',{document_id:null}],
  ['缺字段',{document_id:undefined}],
  ['是数字',{document_id:123}],
  ['是对象',{document_id:{id:HEX('a')}}],
  ['是数组',{document_id:[HEX('a')]}],
  ['不是编号',{document_id:'doc-a'}],
  ['指向另一篇有效编号',{document_id:HEX('f')}],
];
for(const[label,patch]of OWNERSHIP_BAD)test('a drop receipt '+label+' cannot prefill another document angle',async()=>{
  const f=await intakeFixture(HANDOFF,{drop:patch,dropOmits:patch.document_id===undefined?['document_id']:[]});
  try{
    assert.equal(f.d.querySelector('#kb-dossier-title').value,'',label+'：不预填标题');
    assert.equal(f.d.querySelector('#kb-purpose').value,'',label+'：不预填写作目标');
    assert.equal(f.d.querySelector('#kb-purpose-ai-note').hidden,true,label+'：不亮 AI 建议标记');
    assert.match(f.guide(),/没有带入写作建议/,label+'：给同口径提示');
    assert.doesNotMatch(f.guide(),/写一条蹲拍笔记|水面倒影切入/,label+'：不沿用错角度');
    assert.match(f.d.querySelector('#kb-document-body').value,/黄昏码头/,label+'：原文照常可读');
    f.d.querySelector('#kb-dossier-title').value='自己写的标题';
    f.d.querySelector('#kb-purpose').value='自己写的目标';
    f.pick(2,6);
    f.d.querySelector('#kb-save-dossier').click();await tick();await tick();
    const saved=f.calls.find(c=>c.url==='/api/knowledge/dossier');
    assert.ok(saved,label+'：仍可手动建资料夹');
    assert.equal(saved.body.purpose,'自己写的目标',label+'：手写内容照常保存');
    assert.equal(saved.body.items.length,1,label+'：片段仍只来自原文选择');
    assert.deepEqual(Object.keys(saved.body.items[0]).sort(),['end','hash','id','start']);
  }finally{f.w.close();}
});

for(const query of ['&document=','&document=doc-a','&document='+'a'.repeat(31)])test('a handoff link '+query+' cannot prefill without a verifiable document id',async()=>{
  const f=await intakeFixture('http://127.0.0.1:8766/knowledge.html?intake='+HEX('c')+'&angle=0&analysis_at='+encodeURIComponent('2026-10-09T02:00:00+00:00')+query);
  try{
    assert.equal(f.d.querySelector('#kb-dossier-title').value,'',query+'：不预填标题');
    assert.equal(f.d.querySelector('#kb-purpose').value,'',query+'：不预填写作目标');
    assert.equal(f.d.querySelector('#kb-purpose-ai-note').hidden,true,query);
    assert.doesNotMatch(f.guide(),/写一条蹲拍笔记|水面倒影切入/,query+'：不显示错角度');
    f.d.querySelector('#kb-create-draft').click();await tick();await tick();
    assert(!f.calls.some(c=>/dossier|draft/.test(c.url)),query+'：链接本身不可核实就不给写入口');
  }finally{f.w.close();}
});

test('two duplicate recycle records for the same document keep their own intake ids and both prefill',async()=>{
  const rows=[[HEX('c'),0,'2026-10-09T02:00:00+00:00',[{title:'写一条蹲拍笔记',how:'从水面倒影切入，交代蹲位与光线'}],'写一条蹲拍笔记','从水面倒影切入，交代蹲位与光线'],
              [HEX('d'),1,'2026-10-09T03:00:00+00:00',[{title:'该回收项的其他角度',how:'同项其他做法'},{title:'另一个角度',how:'另一条做法'}],'另一个角度','另一条做法']];
  for(const[intakeId,angle,at,angles,title,how]of rows){
    const url='http://127.0.0.1:8766/knowledge.html?document='+HEX('a')+'&intake='+intakeId+'&angle='+angle+'&analysis_at='+encodeURIComponent(at);
    const f=await intakeFixture(url,{drop:{id:intakeId,document_id:HEX('a'),
      analysis:{status:'done',at,fingerprint:'',angles}}});
    try{
      assert.ok(f.calls.some(c=>c.url==='/api/intake/'+intakeId&&c.method==='GET'),'各按自己的回收编号只读核对');
      assert.doesNotMatch(f.guide(),/不是当前打开的这篇|没有可核实的资料编号/,'同文档的重复回收项不能被判归属不一致');
      assert.equal(f.d.querySelector('#kb-dossier-title').value,title,'重复项仍带入自己的角度');
      assert.equal(f.d.querySelector('#kb-purpose').value,how);
      assert.equal(f.d.querySelector('#kb-purpose-ai-note').hidden,false);
      assert(!f.calls.some(c=>/dossier|draft|assistant|completions/.test(c.url)),'跳转不写库、不跑模型');
    }finally{f.w.close();}
  }
});

// ── fixes-knowledge-delete-2-frontend：知识库「移到已删除 / 已删除列表 / 恢复」────────────
// 依据 coordination/fixes-knowledge-delete-2-frontend-contract.md（§3 文案逐字）与
// coordination/prep-knowledge-delete-2-report.md §2 接口草案。后端接口本期未上线，
// 这里用 jsdom 假后端按 §2 的响应形状喂数据；404 分支单独测「尚未启用」。
const HEX64=n=>n.repeat(64);
const TRASH_COPY='移出常规检索和来源计数，原文件与 remio 笔记不动。回收项、提示词卡、作品与已保存证据保留，历史引用仍可打开原文。可在“已删除”恢复；这不是彻底抹除，文本仍留在本机。';
const trashDoc=(letter,title)=>({id:HEX(letter),source:'wechat',kind:'wechat',title,
  body:'黄昏码头的水面把天空再复制一遍',hash:HEX64('1'),url:'',modified:'2026-10-03T00:00:00Z',tags:[],provenance:{},
  excerpt:'黄昏码头的水面',excerpt_start:0});
const TRASH_DOCS=[trashDoc('a','码头笔记'),trashDoc('b','交付复盘'),trashDoc('c','选型记录'),trashDoc('d','访谈纪要')];
const TRASH_ROWS=[
  {id:HEX('a'),title:'码头笔记',source:'wechat',deleted_at:'2026-10-10T08:00:00+08:00',hash:HEX64('1'),body_available:true,content_changed:false,status:'trashed',status_label:'已移出检索'},
  {id:HEX('b'),title:'<img src=x onerror=alert(1)>',source:'wechat',deleted_at:'2026-10-11T09:30:00+08:00',hash:HEX64('2'),body_available:false,content_changed:true,status:'source_gone',status_label:'来源已不在索引，解除隐藏后等待该来源同步'},
];
async function trashFixture(options={}){
  const dom=new JSDOM(fs.readFileSync('public/knowledge.html','utf8'),{url:'http://127.0.0.1:8766/knowledge.html',runScripts:'outside-only'}),w=dom.window,d=w.document,calls=[],gate={};
  const docs=options.docs||TRASH_DOCS,page2=options.page2||[];
  const docsById=new Map(docs.map(x=>[x.id,x]));
  // pollTurns 非空时保留真实的 setInterval 回调：L2 的复核要驱动真的轮询，
  // 不是用「加载更多」去顶替同一条代码路径。
  const pollHandlers=[];
  w.setInterval=(handler)=>{if(options.pollTurns)pollHandlers.push(handler);return 0;};
  let pollSeen=0;
  w.HTMLDialogElement.prototype.showModal=function(){this.open=true;};w.HTMLDialogElement.prototype.close=function(){this.open=false;};
  if(options.longTimerNow){const real=w.setTimeout.bind(w);w.setTimeout=(handler,ms)=>(ms>=60000?(handler(),0):real(handler,ms));}
  let timers=0;
  if(options.timeoutAt!==undefined){const real=w.setTimeout.bind(w);w.setTimeout=(handler,ms)=>(ms>=60000&&++timers===options.timeoutAt?(handler(),0):real(handler,ms));}
  const previewItem=id=>{
    const src=(options.previewItems||[]).find(x=>x.id===id);
    if(src)return src;
    const doc=docsById.get(id)||{title:'',source:'wechat',hash:HEX64('1')};
    return {id,title:doc.title,source:doc.source,hash:doc.hash,status:'ready',status_label:'可移到已删除',
      references:{counted:{recycle_drops:2,prompt_cards:1,dossier_snapshots:0},unknown:['作品与底稿引用','线索与事项证据']}};
  };
  w.fetch=async(url,opts={})=>{
    const target=String(url),method=opts.method||'GET',body=opts.body?JSON.parse(opts.body):undefined;
    calls.push({url:target,method,body});
    if(options.notEnabled&&target.startsWith('/api/knowledge/trash'))return badJson('操作不存在',404);
    if(target==='/api/knowledge'){
      // 轮询驱动时按脚本回报 syncing 序列，复现「进行中→结束」的真实转换。
      if(options.pollTurns){
        const script=options.pollScript||[false];
        const syncing=!!script[Math.min(pollSeen,script.length-1)];
        if(options.pollFails&&options.pollFails.includes(pollSeen))return badJson('知识库读取失败',500);
        pollSeen+=1;
        return okJson({sources:[{id:'wechat',name:'微信',kind:'wechat',status:syncing?'syncing':'ready',count:docs.length,synced_at:'2026-10-03T00:00:00Z'}],dossiers:[],total:docs.length,syncing});
      }
      return okJson({sources:[{id:'wechat',name:'微信',kind:'wechat',status:'ready',count:docs.length,synced_at:'2026-10-03T00:00:00Z'}],dossiers:[],total:docs.length,syncing:false});
    }
    if(target.startsWith('/api/knowledge/search')){
      const offset=Number(new URL(target,'http://127.0.0.1:8766').searchParams.get('offset')||0);
      const list=offset?page2:docs;
      return okJson({results:list,total:list.length+page2.length,next_offset:offset?null:(page2.length?page2.length:null)});
    }
    if(target.startsWith('/api/knowledge/document/')){
      const id=target.split('/').pop();
      const doc=docsById.get(id)||docs[0];
      return okJson({...doc,trashed:options.documentTrashed===true&&id===docs[0].id,deleted_at:options.documentTrashed&&id===docs[0].id?'2026-10-10T08:00:00+08:00':''});
    }
    if(target==='/api/knowledge/sync')return okJson({started:true});
    if(target==='/api/knowledge/trash/preview'){
      if(options.previewFails)return badJson(options.previewFails.error||'知识库正在更新，请稍后刷新核对',options.previewFails.status||503);
      const items=(body.ids||[]).map(previewItem);
      return okJson({schema:'knowledge-trash-preview/v1',items,summary:{requested:items.length,ready:items.length,already_trashed:0,missing:0},semantics:['移到已删除后可在已删除里恢复']});
    }
    if(target==='/api/knowledge/trash'){
      if(options.trashNever)return new Promise((resolve,reject)=>{
        const fail=()=>{const error=new Error('已中止');error.name='AbortError';reject(error);};
        if(opts.signal&&opts.signal.aborted)fail();else if(opts.signal)opts.signal.addEventListener('abort',fail);
      });
      const result=options.trashResult||{schema:'knowledge-trash/v1',deleted:[{id:HEX('a'),title:'码头笔记',source:'wechat',hash:HEX64('1'),deleted_at:'2026-10-11T10:00:00+08:00'}],already_trashed:[],missing:[],conflicts:[],errors:[],summary:{requested:1,deleted:1,already_trashed:0,missing:0,conflicts:0,errors:0}};
      if(options.trashError)return {ok:false,status:options.trashError.status,json:async()=>options.trashError.payload};
      if(options.trashBadJson)return {ok:true,json:()=>{throw new SyntaxError('Unexpected token <');}};
      if(options.trashGate)return {ok:true,json:()=>new Promise(resolve=>{gate.trash=()=>resolve(result);})};
      return okJson(result);
    }
    if(target.startsWith('/api/knowledge/trash?')){
      const rows=options.listRows===undefined?TRASH_ROWS:options.listRows;
      return okJson({results:rows,total:rows.length,offset:0,limit:30,next_offset:null,semantics:['恢复只是解除隐藏']});
    }
    if(target==='/api/knowledge/trash/restore'){
      if(options.restoreNever)return new Promise((resolve,reject)=>{
        const fail=()=>{const error=new Error('已中止');error.name='AbortError';reject(error);};
        if(opts.signal&&opts.signal.aborted)fail();else if(opts.signal)opts.signal.addEventListener('abort',fail);
      });
      return okJson(options.restoreResult||{schema:'knowledge-restore/v1',restored:[],unhidden:[{id:HEX('b'),title:'',source:'',note:'来源已不在索引，解除隐藏后等待该来源同步'}],not_found:[],summary:{requested:1,restored:0,unhidden:1,not_found:0}});
    }
    return badJson('接口不存在',404);
  };
  w.eval(fs.readFileSync('public/knowledge.js','utf8'));
  for(let i=0;i<10;i++)await tick();
  const flush=async(n=10)=>{for(let i=0;i<n;i++)await tick();};
  const boxes=()=>[...d.querySelectorAll('#kb-results [data-trash-pick]')];
  const pick=async(index,on=true)=>{const box=boxes()[index];box.checked=on;box.dispatchEvent(new w.Event('change'));await flush(2);return box;};
  const openTrash=async()=>{d.querySelector('#kb-trash-open').click();await flush();};
  // 真的调一次产品自己的 2.5 秒轮询回调（不改脚本，只是不让它自动跑）。
  const poll=async()=>{for(const handler of pollHandlers)await handler();await flush(12);};
  return {w,d,calls,gate,docs,flush,boxes,pick,openTrash,poll,
    status:()=>d.querySelector('#kb-status').textContent,
    trashStatus:()=>d.querySelector('#kb-trash-status').textContent,
    barCount:()=>d.querySelector('#kb-delete-count').textContent,
    posts:url=>calls.filter(c=>c.url===url&&c.method==='POST')};
}

test('每条结果都有选择框与「移到已删除」，批量条按 ID 计数、上限 50、取消零写入',async()=>{
  const docs=Array.from({length:51},(_,i)=>({...trashDoc('a','资料'+i),id:i.toString(16).padStart(2,'0').repeat(16)}));
  const f=await trashFixture({docs});
  try{
    assert.equal(f.boxes().length,51,'每条结果都要有选择框');
    assert.equal(f.d.querySelectorAll('#kb-results [data-trash-move]').length,51,'每条结果都要有文字入口，不用图标当唯一操作');
    assert.equal(f.d.querySelector('#kb-delete-bar').hidden,true,'没选时批量条不出现');
    await f.pick(0);await f.pick(1);
    assert.equal(f.d.querySelector('#kb-delete-bar').hidden,false);
    assert.match(f.barCount(),/已选 2 条/);
    await f.pick(0,false);
    assert.match(f.barCount(),/已选 1 条/);
    for(let i=1;i<=50;i++)await f.pick(i);
    assert.match(f.barCount(),/已选 50 条/);
    await f.pick(0);
    assert.match(f.barCount(),/已选 50 条/,'第 51 条不能加入选择');
    assert.match(f.status(),/一次最多选择 50 条/);
    assert.equal(f.boxes().filter(b=>b.checked).length,50,'超限那条要回到未勾选，不能看着像已选');
    f.d.querySelector('#kb-delete-clear').click();await f.flush(2);
    assert.equal(f.d.querySelector('#kb-delete-bar').hidden,true);
    assert.equal(f.boxes().filter(b=>b.checked).length,0);
    assert.equal(f.posts('/api/knowledge/trash').length,0,'选择与取消本身不写库');
    assert(!f.calls.some(c=>c.url==='/api/knowledge/trash/preview'),'只勾选不预览');
  }finally{f.w.close();}
});

test('加载更多跨页按 ID 去重，切换来源或重新检索会清除选择',async()=>{
  const f=await trashFixture({docs:TRASH_DOCS.slice(0,2),page2:[TRASH_DOCS[0],TRASH_DOCS[2]]});
  try{
    await f.pick(0);await f.pick(1);
    assert.match(f.barCount(),/已选 2 条/);
    assert.equal(f.d.querySelector('#kb-more').hidden,false);
    f.d.querySelector('#kb-more').click();await f.flush();
    assert(f.calls.some(c=>/\/api\/knowledge\/search\?.*offset=2/.test(c.url)),'翻页按 offset 追加');
    assert.equal(f.boxes().length,4,'翻页后共 4 行');
    await f.pick(2);   // 第二页里同一篇码头笔记再勾一次
    assert.match(f.barCount(),/已选 2 条/,'跨页按 ID 去重，不重复计数');
    f.d.querySelector('#kb-kind').value='articles';f.d.querySelector('#kb-kind').dispatchEvent(new f.w.Event('change'));await f.flush();
    assert.equal(f.d.querySelector('#kb-delete-bar').hidden,true,'切换来源清除选择');
    await f.pick(0);
    f.d.querySelector('#kb-query').value='码头';
    f.d.querySelector('#kb-search-form').dispatchEvent(new f.w.Event('submit',{cancelable:true}));await f.flush();
    assert.equal(f.d.querySelector('#kb-delete-bar').hidden,true,'重新检索清除选择');
    assert.equal(f.posts('/api/knowledge/trash').length,0,'清选择不写库');
  }finally{f.w.close();}
});

test('确认框逐字说明影响与引用分类；取消零写入，确认才带 expected_hash 与 confirm',async()=>{
  const f=await trashFixture();
  try{
    f.d.querySelector('[data-trash-move]').click();await f.flush();
    const preview=f.calls.find(c=>c.url==='/api/knowledge/trash/preview');
    assert.equal(preview.method,'POST','预览也要走受保护的 POST');
    assert.deepEqual(preview.body.ids,[HEX('a')]);
    const dialog=f.d.querySelector('#kb-trash-confirm-dialog');
    assert(dialog.open);
    assert.equal(f.d.querySelector('#kb-trash-confirm-title').textContent,'把这 1 条资料移到已删除？');
    assert.equal(f.d.querySelector('#kb-trash-confirm-note').textContent,TRASH_COPY,'§3 正文文案逐字');
    const items=f.d.querySelector('#kb-trash-confirm-items').textContent;
    assert.match(items,/码头笔记/);assert.match(items,/来源：微信/);assert.match(items,/可移到已删除/);
    assert.match(items,/回收项：2/);assert.match(items,/提示词卡：1/);assert.match(items,/资料夹快照：0/);
    assert.match(items,/作品与底稿引用：未统计/);assert.match(items,/线索与事项证据：未统计/);
    assert.doesNotMatch(items,/只有\s*\d+\s*处引用/,'不合成总数');
    assert.equal(f.posts('/api/knowledge/trash').length,0,'预览不写入');
    f.d.querySelector('#kb-trash-confirm-dialog [data-close]').click();
    assert.equal(dialog.open,false);
    assert.equal(f.posts('/api/knowledge/trash').length,0,'取消零写入');
    f.d.querySelector('[data-trash-move]').click();await f.flush();
    assert.equal(f.d.querySelector('#kb-trash-confirm-ok').textContent,'确认移到已删除');
    f.d.querySelector('#kb-trash-confirm-ok').click();await f.flush();
    const posts=f.posts('/api/knowledge/trash');
    assert.equal(posts.length,1);
    assert.deepEqual(posts[0].body.items,[{id:HEX('a'),expected_hash:HEX64('1')}],'每项带 expected_hash');
    assert.equal(posts[0].body.confirm,true,'必须显式 confirm');
    assert.equal(dialog.open,false);
    assert.match(f.status(),/已移出 1 条/);
  }finally{f.w.close();}
});

test('部分成功按分组报准确数量，hash 冲突不自动重发并保留失败项选择',async()=>{
  const result={schema:'knowledge-trash/v1',
    deleted:[{id:HEX('a'),title:'码头笔记'}],already_trashed:[{id:HEX('b'),title:'交付复盘'}],missing:[],
    conflicts:[{id:HEX('c'),title:'选型记录',reason:'预览之后正文已更新，请重新预览'}],
    errors:[{id:HEX('d'),title:'访谈纪要',reason:'这一条没有写成功'}],
    summary:{requested:4,deleted:1,already_trashed:1,missing:0,conflicts:1,errors:1}};
  const f=await trashFixture({trashResult:result});
  try{
    for(let i=0;i<4;i++)await f.pick(i);
    assert.match(f.barCount(),/已选 4 条/);
    f.d.querySelector('#kb-delete-selected').click();await f.flush();
    assert.equal(f.d.querySelector('#kb-trash-confirm-title').textContent,'把这 4 条资料移到已删除？');
    f.d.querySelector('#kb-trash-confirm-ok').click();await f.flush();
    assert.equal(f.posts('/api/knowledge/trash').length,1,'一次确认只发一次');
    const status=f.status();
    assert.match(status,/已移出 1 条/);assert.match(status,/已在已删除 1 条/);assert.match(status,/知识库没有 0 条/);
    assert.match(status,/版本已变化 1 条/);assert.match(status,/未写成功 1 条/);
    assert.match(status,/预览后资料已更新，未移出；请重新预览/);
    assert.equal(f.posts('/api/knowledge/trash').length,1,'冲突后不自动重发');
    assert.equal(f.calls.filter(c=>c.url==='/api/knowledge/trash/preview').length,1,'也不自动重新预览');
    assert.equal(f.d.querySelector('#kb-delete-bar').hidden,false,'部分成功仍保留失败项选择');
    assert.match(f.barCount(),/已选 2 条/,'冲突与未写成功的两条留在选择里');
    const kept=f.boxes().filter(b=>b.checked).map(b=>b.dataset.trashPick);
    assert.deepEqual(kept,[HEX('c'),HEX('d')]);
  }finally{f.w.close();}
});

test('已删除列表只用 deleted_at 显示删除时间，并如实标出正文与版本变化',async()=>{
  const f=await trashFixture();
  try{
    await f.openTrash();
    const get=f.calls.find(c=>c.url.startsWith('/api/knowledge/trash?'));
    assert.equal(get.method,'GET','列表只读走 GET');
    assert.match(get.url,/offset=0&limit=30/);
    assert(f.d.querySelector('#kb-trash-dialog').open);
    const list=f.d.querySelector('#kb-trash-list');
    assert.match(list.textContent,/码头笔记/);
    assert.match(list.textContent,/删除时间：2026\/10\/10 08:00:00/);
    assert.doesNotMatch(list.textContent,/2026\/10\/3/,'不拿收藏或更新时间冒充删除时间');
    assert.match(list.textContent,/正文仍在/);assert.match(list.textContent,/正文已不在索引/);
    assert.match(list.textContent,/正文自删除后已变化/);
    assert.match(list.textContent,/来源已不在索引，解除隐藏后等待该来源同步/);
    assert.equal(list.querySelectorAll('img,script').length,0,'列表里的不可信标题只当文本');
    assert.match(list.textContent,/<img src=x onerror=alert\(1\)>/);
    assert.equal(f.d.querySelector('#kb-trash-more').hidden,true,'没有下一页就不给加载更多');
    assert.match(f.d.querySelector('#kb-trash-count').textContent,/2/,'入口上显示已删除数量');
  }finally{f.w.close();}
});

test('恢复必须确认；缺正文只说解除隐藏不谎称恢复，并给回到正常检索的入口',async()=>{
  const f=await trashFixture();
  try{
    await f.openTrash();
    const buttons=[...f.d.querySelectorAll('#kb-trash-list [data-restore]')];
    assert.equal(buttons.length,2);
    buttons[1].click();await f.flush(2);
    assert(f.d.querySelector('#kb-restore-confirm-dialog').open,'恢复也要明确确认');
    assert.equal(f.d.querySelector('#kb-restore-confirm-title').textContent,'把这 1 条资料恢复到常规检索？');
    assert.equal(f.posts('/api/knowledge/trash/restore').length,0,'确认前不 POST');
    f.d.querySelector('#kb-restore-confirm-ok').click();await f.flush(16);
    const posts=f.posts('/api/knowledge/trash/restore');
    assert.equal(posts.length,1);assert.deepEqual(posts[0].body.ids,[HEX('b')]);
    const box=f.d.querySelector('#kb-trash-result');
    assert.equal(box.hidden,false);
    assert.match(box.textContent,/恢复结果：已恢复 0 条 · 已解除隐藏 1 条 · 不在已删除 0 条/);
    assert.match(box.textContent,/已解除隐藏，来源已不在索引，等待该来源同步/);
    assert.doesNotMatch(box.textContent,/已恢复 1 条/,'缺正文不能冒充恢复了正文');
    assert([...box.querySelectorAll('button')].some(b=>b.textContent==='回到正常检索查看'));
    assert(f.calls.filter(c=>c.url.startsWith('/api/knowledge/trash?')).length>=2,'恢复后刷新已删除计数');
    assert(f.calls.filter(c=>c.url==='/api/knowledge').length>=2,'恢复后刷新来源计数');
    box.querySelector('button').click();await f.flush();
    assert.equal(f.d.querySelector('#kb-trash-dialog').open,false,'回到正常检索会关掉列表并重新检索');
  }finally{f.w.close();}
});

test('已删除列表可批量恢复选中项',async()=>{
  const f=await trashFixture({restoreResult:{schema:'knowledge-restore/v1',
    restored:[{id:HEX('a'),title:'码头笔记',source:'wechat',hash:HEX64('1'),content_changed:true},{id:HEX('b'),title:'交付复盘',source:'wechat',hash:HEX64('2'),content_changed:false}],
    unhidden:[],not_found:[],summary:{requested:2,restored:2,unhidden:0,not_found:0}}});
  try{
    await f.openTrash();
    const checks=[...f.d.querySelectorAll('#kb-trash-list [data-restore-pick]')];
    assert.equal(checks.length,2);
    checks.forEach(c=>{c.checked=true;});
    f.d.querySelector('#kb-trash-restore-selected').click();await f.flush(2);
    assert.equal(f.d.querySelector('#kb-restore-confirm-title').textContent,'把这 2 条资料恢复到常规检索？');
    f.d.querySelector('#kb-restore-confirm-ok').click();await f.flush(16);
    const posts=f.posts('/api/knowledge/trash/restore');
    assert.equal(posts.length,1);
    assert.deepEqual(posts[0].body.ids,[HEX('a'),HEX('b')]);
    const box=f.d.querySelector('#kb-trash-result');
    assert.match(box.textContent,/恢复结果：已恢复 2 条/);
    assert.match(box.textContent,/正文与删除当时已不同/);
  }finally{f.w.close();}
});

test('打开已删资料给隐藏 banner 与恢复入口，不自动恢复，仍可选片段引用',async()=>{
  const f=await trashFixture({documentTrashed:true});
  try{
    f.d.querySelector('[data-preview]').click();await f.flush();
    const banner=f.d.querySelector('#kb-document-trash-banner');
    assert.equal(banner.hidden,false);
    assert.match(banner.textContent,/这篇已移出常规检索，原文仍可核对/);
    assert(f.d.querySelector('#kb-document-restore'),'给恢复入口');
    assert.equal(f.posts('/api/knowledge/trash/restore').length,0,'打开已删资料不自动恢复');
    assert.equal(f.d.querySelector('#kb-document-trash').hidden,true,'已删的这篇不再给删除入口');
    assert.match(f.d.querySelector('#kb-document-dialog .kb-form-status').textContent,/不会自动恢复/);
    const t=f.d.querySelector('#kb-document-body');
    assert.equal(f.d.querySelector('#kb-pick-selection').disabled,false,'隐藏不禁止核对与选片段');
    t.setSelectionRange(0,2);f.d.querySelector('#kb-pick-selection').click();
    assert.match(f.d.querySelector('#kb-budget').textContent,/1 个片段/);
    f.d.querySelector('#kb-document-restore').click();await f.flush(2);
    assert(f.d.querySelector('#kb-restore-confirm-dialog').open,'恢复要确认');
    assert.equal(f.posts('/api/knowledge/trash/restore').length,0);
    f.d.querySelector('[data-close]').click();
    f.d.querySelectorAll('[data-preview]')[1].click();await f.flush();
    assert.equal(f.d.querySelector('#kb-document-trash-banner').hidden,true,'换一篇要清掉上一篇的隐藏提示');
  }finally{f.w.close();}
});

test('回收写作入口到达隐藏正文时允许核对与选片段，只说明不自动恢复',async()=>{
  const f=await intakeFixture(HANDOFF,{document:{trashed:true,deleted_at:'2026-10-10T08:00:00+08:00'}});
  try{
    assert.equal(f.d.querySelector('#kb-document-trash-banner').hidden,false);
    assert.match(f.d.querySelector('#kb-document-trash-banner').textContent,/这篇已移出常规检索，原文仍可核对/);
    assert.match(f.d.querySelector('#kb-document-body').value,/黄昏码头/,'隐藏正文仍可读');
    f.pick(2,6);
    assert.match(f.d.querySelector('#kb-budget').textContent,/1 个片段/,'隐藏不等于不可引用');
    assert.equal(f.calls.filter(c=>c.url==='/api/knowledge/trash/restore').length,0,'写作入口不自动恢复');
    assert(!f.calls.some(c=>/draft|assistant|completions/.test(c.url)),'不自动建底稿、不跑模型');
  }finally{f.w.close();}
});

test('忙态覆盖到 JSON 读完；超时或 JSON 读不出只说未确认且不自动重发',async()=>{
  const gated=await trashFixture({trashGate:true});
  try{
    await gated.pick(0);
    gated.d.querySelector('#kb-delete-selected').click();await gated.flush();
    const ok=gated.d.querySelector('#kb-trash-confirm-ok');
    ok.click();ok.click();
    await gated.flush(4);
    assert.equal(gated.posts('/api/knowledge/trash').length,1,'响应体读完前只发一次');
    assert.equal(ok.disabled,true,'忙态要覆盖到 response.json');
    gated.gate.trash();await gated.flush(12);
    assert.equal(ok.disabled,false,'JSON 读完才释放忙态');
  }finally{gated.w.close();}
  const timedOut=await trashFixture({trashNever:true,timeoutAt:2});
  try{
    await timedOut.pick(0);
    timedOut.d.querySelector('#kb-delete-selected').click();await timedOut.flush();
    timedOut.d.querySelector('#kb-trash-confirm-ok').click();await timedOut.flush(12);
    assert.match(timedOut.status(),/操作结果未确认，请刷新已删除列表核对/);
    assert.equal(timedOut.posts('/api/knowledge/trash').length,1,'超时不自动重发');
    assert.equal(timedOut.d.querySelector('#kb-trash-confirm-ok').disabled,false,'未确认要释放忙态，让用户自己决定');
  }finally{timedOut.w.close();}
  const unreadable=await trashFixture({trashBadJson:true});
  try{
    await unreadable.pick(0);
    unreadable.d.querySelector('#kb-delete-selected').click();await unreadable.flush();
    unreadable.d.querySelector('#kb-trash-confirm-ok').click();await unreadable.flush(12);
    assert.match(unreadable.status(),/操作结果未确认，请刷新已删除列表核对/);
    assert.equal(unreadable.posts('/api/knowledge/trash').length,1,'JSON 读不出也不自动重发');
  }finally{unreadable.w.close();}
});

test('预览与列表里的不可信文字只当文本，错误码只给中文提示不显示原始异常',async()=>{
  const malicious=[{id:HEX('a'),title:'<img src=x onerror=alert(1)>',source:'wechat',hash:HEX64('1'),status:'ready',
    references:{counted:{recycle_drops:null,prompt_cards:1},unknown:['作品与底稿引用']}}];
  const f=await trashFixture({previewItems:malicious});
  try{
    f.d.querySelector('[data-trash-move]').click();await f.flush();
    const box=f.d.querySelector('#kb-trash-confirm-items');
    assert.equal(box.querySelectorAll('img,script').length,0);
    assert.match(box.textContent,/<img src=x onerror=alert\(1\)>/);
    assert.match(box.textContent,/回收项：未统计/,'数不到就说未统计，不当 0');
    assert.match(box.textContent,/提示词卡：1/);
  }finally{f.w.close();}
  for(const [payload,expected] of [
    [{status:500,payload:{code:'STORE_ERROR',error:'sqlite3.OperationalError: database is locked at /Users/test/inspiration/knowledge.db'}},/这次操作结果未确认，请刷新已删除列表核对/],
    [{status:503,payload:{code:'STORE_BUSY',error:'database is locked'}},/知识库正在更新，请稍后刷新核对/],
    [{status:403,payload:{code:'UNTRUSTED_ORIGIN'}},/这个页面不是本机工作台打开的/],
    [{status:413,payload:{code:'REQUEST_TOO_LARGE'}},/一次最多处理 50 条/],
    [{status:400,payload:{code:'INVALID_REQUEST',error:'请选择 1–50 条知识'}},/请选择 1–50 条知识/],
  ]){
    const f2=await trashFixture({trashError:payload});
    try{
      await f2.pick(0);
      f2.d.querySelector('#kb-delete-selected').click();await f2.flush();
      f2.d.querySelector('#kb-trash-confirm-ok').click();await f2.flush(12);
      assert.match(f2.d.querySelector('#kb-trash-confirm-status').textContent,expected,JSON.stringify(payload));
      assert.doesNotMatch(f2.status(),/sqlite|OperationalError|\/Users|Traceback/i,'不显示原始异常或路径');
      assert.equal(f2.posts('/api/knowledge/trash').length,1,'失败不自动重发');
    }finally{f2.w.close();}
  }
});

test('后端接口未上线时删除入口只提示尚未启用，页面其余功能不坏',async()=>{
  const f=await trashFixture({notEnabled:true});
  try{
    assert(f.d.querySelector('[data-trash-move]'),'入口照常显示');
    assert(f.d.querySelector('#kb-trash-open'),'已删除入口照常显示');
    f.d.querySelector('[data-trash-move]').click();await f.flush();
    assert.match(f.status(),/删除功能尚未启用/);
    assert.equal(f.d.querySelector('#kb-trash-confirm-dialog').open,false,'没有预览就不给确认框');
    assert.equal(f.posts('/api/knowledge/trash').length,0);
    await f.openTrash();
    assert.match(f.trashStatus(),/删除功能尚未启用/);
    assert.equal(f.d.querySelector('#kb-trash-dialog').open,false);
    assert(f.d.querySelector('#kb-results .kb-result'),'结果列表照常显示');
    f.d.querySelector('[data-preview]').click();await f.flush();
    assert(f.d.querySelector('#kb-document-dialog').open,'查看原文照常可用');
    assert.equal(f.d.querySelector('#kb-document-trash-banner').hidden,true);
  }finally{f.w.close();}
});

// ── fixes-knowledge-delete-2-frontend-b：GET 已删除列表/分页超时（M1）+ 分组只认完整数组（L1）
// 依据 coordination/review-knowledge-delete-1b-frontend-report.md §3、§4。
// 只追加：在 trashFixture 之上替换 w.fetch / w.setTimeout，不动既有夹具与断言。
async function trashListTimeoutFixture(options={}){
  const f=await trashFixture();
  const base=f.w.fetch;
  const real=f.w.setTimeout.bind(f.w);
  const at=options.timeoutAt||1;
  let listRequests=0,timers=0;
  // 只加速第 at 个 60 秒截止，其余走真定时器；否则一次超时会误伤同页其他请求。
  f.w.setTimeout=(handler,ms)=>(ms>=60000&&++timers===at?(handler(),0):real(handler,ms));
  f.w.fetch=async(url,opts={})=>{
    const target=String(url);
    if(target.startsWith('/api/knowledge/trash?')){
      listRequests+=1;
      if(listRequests===at){
        return new Promise((resolve,reject)=>{
          // 按 fetch 语义：拿到 signal 就要响应 abort；不带 signal 时这份假 fetch 永不返回。
          const fail=()=>{const error=new Error('已中止');error.name='AbortError';reject(error);};
          if(opts.signal&&opts.signal.aborted)fail();else if(opts.signal)opts.signal.addEventListener('abort',fail);
        });
      }
      if(listRequests===1&&options.firstPage)return okJson(options.firstPage);
      return okJson({results:[],total:0,offset:0,limit:30,next_offset:null,semantics:['恢复只是解除隐藏']});
    }
    return base(url,opts);
  };
  return {...f,listRequests:()=>listRequests};
}

test('已删除列表 GET 超时：中止请求、释放忙态、提示可稍后重试且不自动重发',async()=>{
  const f=await trashListTimeoutFixture();
  try{
    f.d.querySelector('#kb-trash-open').click();
    await f.flush(6);
    assert.match(f.trashStatus(),/读取超时，未确认结果，可稍后重试/,'超时要给可读提示，不能停在正在读取');
    assert.doesNotMatch(f.trashStatus(),/正在读取已删除列表/,'不能一直卡在读取中');
    assert.equal(f.d.querySelector('#kb-trash-dialog').open,false,'读不出就不打开列表，也不显示成空的');
    assert.equal(f.listRequests(),1,'超时不自动重发');
    await f.flush(8);
    assert.equal(f.listRequests(),1,'等一会儿也不会自己再发');
    f.d.querySelector('[data-trash-move]').click();await f.flush();
    assert(f.d.querySelector('#kb-trash-confirm-dialog').open,'忙态已释放，删除预览可用');
    f.d.querySelector('#kb-trash-confirm-dialog [data-close]').click();
    f.d.querySelector('#kb-trash-open').click();await f.flush(6);
    assert.equal(f.listRequests(),2,'只有用户手动重试才再发一次');
    assert.equal(f.d.querySelector('#kb-trash-dialog').open,true,'手动重试成功就正常打开列表');
    assert.equal(f.trashStatus(),'','重试成功要清掉超时提示');
  }finally{f.w.close();}
});

test('已删除列表分页 GET 超时：保留第一页、释放忙态、不自动重发',async()=>{
  const firstPage={results:[TRASH_ROWS[0]],total:2,offset:0,limit:30,next_offset:1,semantics:['恢复只是解除隐藏']};
  const f=await trashListTimeoutFixture({firstPage,timeoutAt:2});
  try{
    await f.openTrash();
    assert.equal(f.d.querySelectorAll('#kb-trash-list .kb-trash-row').length,1,'第一页照常显示');
    assert.equal(f.d.querySelector('#kb-trash-more').hidden,false,'有下一页就给「加载更多」');
    assert.equal(f.listRequests(),1);
    f.d.querySelector('#kb-trash-more').click();
    await f.flush(6);
    assert.match(f.trashStatus(),/读取超时，未确认结果，可稍后重试/);
    assert.doesNotMatch(f.trashStatus(),/正在读取已删除列表/);
    assert.equal(f.d.querySelectorAll('#kb-trash-list .kb-trash-row').length,1,'分页失败不清空已读到的第一页');
    assert.equal(f.listRequests(),2,'分页超时不自动重发');
    f.d.querySelector('#kb-trash-list [data-restore]').click();await f.flush();
    assert(f.d.querySelector('#kb-restore-confirm-dialog').open,'忙态已释放，恢复入口可用');
    f.d.querySelector('#kb-restore-confirm-dialog [data-close]').click();
    f.d.querySelector('#kb-trash-more').click();await f.flush(6);
    assert.equal(f.listRequests(),3,'用户手动再点才重发');
  }finally{f.w.close();}
});

test('删除回执缺分组数组时只说未统计，不用 summary 数字兜底也不崩',async()=>{
  const result={schema:'knowledge-trash/v1',summary:{requested:1,deleted:1,already_trashed:0,missing:0,conflicts:0,errors:0}};
  const f=await trashFixture({trashResult:result});
  try{
    await f.pick(0);
    f.d.querySelector('#kb-delete-selected').click();await f.flush();
    f.d.querySelector('#kb-trash-confirm-ok').click();await f.flush(12);
    const status=f.status();
    assert.match(status,/已移出 未统计 条/,'缺数组就如实说未统计，不拿 summary 数字冒充');
    assert.doesNotMatch(status,/NaN|undefined|\[object Object\]/);
    assert.equal(f.d.querySelector('#kb-trash-confirm-dialog').open,false,'确认框照常关闭');
    assert.equal(f.posts('/api/knowledge/trash').length,1,'缺数组也不自动重发');
    assert(f.d.querySelector('#kb-results .kb-result'),'页面照常刷新');
  }finally{f.w.close();}
});

// ── 追加（协调者 10-11）：M2 单条恢复未捕获 TypeError、L2 轮询抹掉删除回执
// 依据 coordination/e2e-knowledge-trash-report.md 与 fixes-knowledge-delete-2-frontend-b-contract.md「追加」。
test('已删除列表点「恢复」不抛未捕获错误；内部出错也把提示写进状态行',async()=>{
  const f=await trashFixture();
  try{
    const errors=[];
    f.w.addEventListener('error',e=>errors.push(e.message));
    await f.openTrash();
    f.d.querySelector('#kb-trash-list [data-restore]').click();await f.flush();
    assert.equal(errors.length,0,'单条恢复点击不能有未捕获错误：'+errors.join(' | '));
    assert(f.d.querySelector('#kb-restore-confirm-dialog').open,'确认框照常打开');
    assert.equal(f.posts('/api/knowledge/trash/restore').length,0,'确认前不 POST');
    f.d.querySelector('#kb-restore-confirm-dialog [data-close]').click();
    // 内部抛错（确认框条目区读不出）：askRestore 是 async，调用方的 catch 要接住并显示
    f.d.querySelector('#kb-restore-confirm-items').remove();
    f.d.querySelector('#kb-trash-list [data-restore]').click();await f.flush(4);
    assert.equal(errors.length,0,'内部错误要由 .catch 接住，不能冒到 window.onerror');
    assert.match(f.trashStatus(),/\S/,'出错提示要写进已删除列表状态行');
    f.d.querySelector('#kb-trash-restore-selected').click();await f.flush(4);
    assert.equal(errors.length,0,'「恢复选中」同样不能有未捕获错误');
    assert.match(f.trashStatus(),/\S/);
  }finally{f.w.close();}
});

test('删除回执不被检索重绘抹掉，用户主动检索或换来源才清掉',async()=>{
  const f=await trashFixture({page2:[TRASH_DOCS[2]]});
  try{
    await f.pick(0);
    f.d.querySelector('#kb-delete-selected').click();await f.flush();
    f.d.querySelector('#kb-trash-confirm-ok').click();await f.flush(12);
    const receipt=f.status();
    assert.match(receipt,/已删除结果：已移出 1 条/);
    assert.equal(f.d.querySelectorAll('#kb-results .kb-result').length,4,'删除后结果列表照常刷新');
    // 轮询走的就是这个 search()（夹具里 setInterval 被停用，用「加载更多」驱动同一条路径）
    assert.equal(f.d.querySelector('#kb-more').hidden,false,'有下一页才有加载更多');
    f.d.querySelector('#kb-more').click();await f.flush(12);
    assert.equal(f.status(),receipt,'检索重绘不能把刚给的删除回执抹掉');
    f.d.querySelector('#kb-more').click();await f.flush(12);
    assert.equal(f.status(),receipt,'连续几轮也不能抹');
    // 用户主动重新检索：回执让位给新状态
    f.d.querySelector('#kb-search-form button').click();await f.flush(12);
    assert.equal(f.status(),'','用户主动检索要清掉旧回执');
    assert.equal(f.posts('/api/knowledge/trash').length,1,'回执显示期间不自动重发删除');
  }finally{f.w.close();}
});

// ── 追加（协调者 10-11，frontend-c）：L2 遗留 —— 后台同步「进行中→结束」不能盖掉删除/恢复回执
// 依据 coordination/review-knowledge-delete-2-frontend-b-report.md 的复现：旧用例用「加载更多」
// 驱动 search()，只覆盖 search() 末尾，没覆盖轮询末尾那句额外的后台提示。这里驱动真的轮询回调。
test('后台同步「进行中→结束」不覆盖删除回执，回执逐字保留到用户主动操作',async()=>{
  const f=await trashFixture({pollTurns:true,pollScript:[false,true,false]});
  try{
    await f.pick(0);
    f.d.querySelector('#kb-delete-selected').click();await f.flush();
    f.d.querySelector('#kb-trash-confirm-ok').click();await f.flush(12);
    const receipt=f.status();
    assert.match(receipt,/已删除结果：已移出 1 条/);
    // 第 1 轮轮询：status 报 syncing=true → 不是转换点，回执照旧。
    await f.poll();
    assert.equal(f.status(),receipt,'同步进行中不动回执');
    // 第 2 轮：syncing 由 true 变 false，这正是会补一句「更新结束…」的分支。
    await f.poll();
    assert.equal(f.status(),receipt,'同步结束提示不能盖掉刚给的删除回执');
    assert.doesNotMatch(f.status(),/更新结束/, '后台提示要么让位要么并列，不能替换回执');
    assert.equal(f.posts('/api/knowledge/trash').length,1,'轮询不触发重发');
    // 再来一轮（syncing 已 false，无转换）仍然不动。
    await f.poll();
    assert.equal(f.status(),receipt,'稳态轮询也不能动回执');
    // 用户主动检索才让位。
    f.d.querySelector('#kb-search-form button').click();await f.flush(12);
    assert.equal(f.status(),'','主动检索后回执才让位');
  }finally{f.w.close();}
});

test('后台轮询出错时回执仍在第一行、错误附在下面；没有回执时照常只显示错误',async()=>{
  const f=await trashFixture({pollTurns:true,pollScript:[false],pollFails:[1]});
  try{
    await f.pick(0);
    f.d.querySelector('#kb-delete-selected').click();await f.flush();
    f.d.querySelector('#kb-trash-confirm-ok').click();await f.flush(12);
    const receipt=f.status();
    assert.match(receipt,/已删除结果：已移出 1 条/);
    await f.poll();   // 这一轮 /api/knowledge 读失败
    const shown=f.status();
    assert.match(shown,/已删除结果：已移出 1 条/,'回执不能被后台错误抹掉');
    assert.match(shown,/知识库读取失败/,'后台错误也要让用户看到，不能被回执吞掉');
    assert.ok(shown.startsWith(receipt),'回执逐字保留在第一段，错误附在后面；实际「' + shown + '」');
    assert.equal(shown, receipt + '\n知识库读取失败','回执整段不动，错误另起一行');
    assert.equal(f.posts('/api/knowledge/trash').length,1,'出错不重发');
  }finally{f.w.close();}
  const bare=await trashFixture({pollTurns:true,pollScript:[false],pollFails:[1]});
  try{
    await bare.poll();
    assert.match(bare.status(),/知识库读取失败/,'没有回执时就只显示后台错误');
  }finally{bare.w.close();}
});

test('没有回执时后台同步结束提示照常显示（不把提示改没了）',async()=>{
  const f=await trashFixture({pollTurns:true,pollScript:[false,true,false]});
  try{
    await f.poll();
    await f.poll();
    assert.match(f.status(),/更新结束，检索结果已刷新/,'平时仍要给出同步完成提示');
  }finally{f.w.close();}
});
