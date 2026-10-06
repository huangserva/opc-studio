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
