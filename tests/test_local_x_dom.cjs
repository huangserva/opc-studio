/* DOM integration fixture, not a claim of live X compatibility. */
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs');
const {JSDOM}=require('jsdom');
const source=fs.readFileSync('publishing/local-extension/x.js','utf8');
// 本机 35k 字长文夹具不进公开仓库：用 ARTICLE_STUDIO_X_DOM_FIXTURE 指定本机路径，未设置或文件不存在时跳过该条。
const X_DOM_FIXTURE=process.env.ARTICLE_STUDIO_X_DOM_FIXTURE||'';
const X_DOM_FIXTURE_MISSING=!X_DOM_FIXTURE||!fs.existsSync(X_DOM_FIXTURE);
function fixture({noEditor=false,ambiguousCreate=false,rejectCover=false,atomicMedia=false,modelSelection=false,misplaceOnce=false,misplaceAlways=false,loseOnce=false,loseAlways=false,maxImages=null,rejectInlineOnce=false}={}){
 const dom=new JSDOM('<main><a data-testid="AppTabBar_Profile_Link" href="https://x.com/servasyy">Profile</a><a role="button" id="write" href="#"><span role="button">Write</span></a>'+ (ambiguousCreate?'<button>Write</button>':'')+'</main>',{url:'https://x.com/compose/articles',runScripts:'outside-only'});
 const w=dom.window,d=w.document;let listener,clock=0,creates=0,uploads=0,checkpoints=[],insertions=0,losses=0,rejectedAt=null;
 w.HTMLElement.prototype.getClientRects=function(){return [{}];};
 Object.defineProperty(w.HTMLElement.prototype,'innerText',{get(){return this.textContent;}});
 Object.defineProperty(w.HTMLImageElement.prototype,'complete',{get(){return true;}});
 Object.defineProperty(w.HTMLImageElement.prototype,'naturalWidth',{get(){return 100;}});
 Object.defineProperty(w.HTMLImageElement.prototype,'naturalHeight',{get(){return 100;}});
 w.createImageBitmap=async file=>({width:100,height:100,pixel:file.name.startsWith('1')?20:100,close(){}});
 w.HTMLCanvasElement.prototype.getContext=function(){let value=0;return {fillRect(){},drawImage(image){value=image.pixel;},getImageData(){return {data:new Uint8ClampedArray(128*128*4).fill(value)};}};};
 const NativeDate=w.Date;w.Date=class extends NativeDate{static now(){return clock;}};
 w.setTimeout=(fn,ms)=>{clock+=ms;Promise.resolve().then(fn);return 1;};
 w.structuredClone=structuredClone;
 w.DataTransfer=class{constructor(){this.files=[];this.data={};this.items={add:file=>this.files.push(file)};}setData(k,v){this.data[k]=v;}getData(k){return this.data[k]||'';}};
 w.ClipboardEvent=class extends w.Event{constructor(type,opts){super(type,opts);this.clipboardData=opts.clipboardData;}};
 w.chrome={runtime:{onMessage:{addListener:fn=>listener=fn},sendMessage:async msg=>{checkpoints.push(structuredClone(msg.payload));if(msg.payload.message?.startsWith('图片 2 已上传')&&(loseAlways||(loseOnce&&losses===0))){const key=msg.payload.checkpoint.media['02'].key;d.querySelector('[data-offset-key="'+key.slice(6)+'"]').remove();losses++;}return {ok:true};}}};
 d.execCommand=(action,unused,value)=>{
  const sel=w.getSelection(),r=sel.getRangeAt(0);
  if(action==='insertHTML'){r.deleteContents();r.insertNode(r.createContextualFragment(value));}
  else if(action==='insertText'){r.deleteContents();r.insertNode(d.createTextNode(value));}
  else if(action==='delete')r.deleteContents();else throw Error(action);return true;
 };
 function inputFiles(e){Object.defineProperty(e,'files',{value:[],writable:true});}
 d.getElementById('write').addEventListener('click',e=>{
  e.preventDefault();creates++;w.history.replaceState({},'', '/compose/articles/edit/555');
  if(noEditor)return;
  d.querySelector('main').insertAdjacentHTML('beforeend','<textarea placeholder="Add a title"></textarea><input type="file" data-testid="fileInput"><div data-testid="composer"><div contenteditable="true" role="textbox" data-contents="true"></div></div><button id="insert">Insert</button><span>Last saved just now</span>');
  d.querySelector('[contenteditable]').addEventListener('paste',event=>{event.preventDefault();const html=event.clipboardData.getData('text/html');d.execCommand(html?'insertHTML':'insertText',false,html||event.clipboardData.getData('text/plain'));});
  d.querySelector('[contenteditable]').addEventListener('keydown',event=>{
   if(!['Backspace','Delete'].includes(event.key))return;
   const range=w.getSelection().getRangeAt(0),textBlock=range.startContainer.nodeType===3?range.startContainer.parentElement:range.startContainer;
   let block=textBlock;while(block.parentElement!==d.querySelector('[contenteditable]'))block=block.parentElement;
   const previous=block.previousElementSibling;
   if(event.key==='Backspace'&&range.startOffset===0&&previous?.getAttribute('contenteditable')==='false'){event.preventDefault();previous.remove();}
   const next=block.nextElementSibling;if(event.key==='Delete'&&range.startOffset===range.startContainer.textContent.length&&next?.getAttribute('contenteditable')==='false'){event.preventDefault();next.remove();}
  });
  let selectedModel;
  if(modelSelection){d.querySelector('[contenteditable]').addEventListener('keyup',()=>{selectedModel=w.getSelection().getRangeAt(0).cloneRange();});}
  const cover=d.querySelector('input[type=file]');inputFiles(cover);
  cover.addEventListener('change',()=>{
    uploads++;if(rejectCover)return;
    d.querySelector('main').insertAdjacentHTML('beforeend','<div role="dialog" id="crop"><button data-testid="applyButton">Apply</button></div>');
    d.querySelector('[data-testid="applyButton"]').onclick=()=>{d.querySelector('#crop').remove();d.querySelector('main').insertAdjacentHTML('beforeend','<img id="cover" src="https://pbs.twimg.com/media/cover?format=jpg&name=small">');};
  });
  d.querySelector('#insert').onclick=()=>{
    const selected=modelSelection?selectedModel.cloneRange():w.getSelection().getRangeAt(0).cloneRange();
    d.querySelector('main').insertAdjacentHTML('beforeend','<button role="menuitem" id="media">Media</button>');
    d.querySelector('#media').onclick=()=>{
      d.querySelector('#media').remove();
      d.querySelector('main').insertAdjacentHTML('beforeend','<div role="dialog" id="inline"><input type="file"></div>');
      const input=d.querySelector('#inline input');inputFiles(input);
      input.addEventListener('change',()=>{
       uploads++;if(maxImages!==null&&insertions>=maxImages){rejectedAt=clock;const alert=d.createElement('div');alert.setAttribute('role','alert');alert.textContent='Your latest media has not been added as it exceeds the media limit per Article.';d.querySelector('main').append(alert);d.querySelector('#inline').remove();return;}if(rejectInlineOnce&&uploads===3){d.querySelector('#inline').remove();return;}const img=d.createElement('img');img.src='https://pbs.twimg.com/media/body'+(++insertions)+'?format=jpg&name=small';img.pixel=input.files[0].name.startsWith('1')?20:100;
       let inserted=img;
       if(atomicMedia){const section=d.createElement('section');section.setAttribute('data-block','true');section.setAttribute('contenteditable','false');section.setAttribute('data-offset-key','media'+insertions+'-0-0');section.append(img);const caption=d.createElement('span');caption.textContent='Edit Provide a caption (optional)';section.append(caption);const remove=d.createElement('button');remove.textContent='Remove media';remove.onclick=()=>section.remove();section.append(remove);inserted=section;for(const old of d.querySelectorAll('[contenteditable] img'))old.src='blob:https://x.com/rerender-'+uploads+'-'+old.closest('section').getAttribute('data-offset-key');}
       selected.collapse(true);if((misplaceOnce&&insertions===2)||(misplaceAlways&&insertions>=2)){const blank=d.createElement('p');blank.append(d.createTextNode(' '));d.querySelector('[contenteditable]').append(inserted,blank);}else{const parent=selected.startContainer.nodeType===3?selected.startContainer.parentElement:selected.startContainer;const block=parent.closest('p,h2,blockquote');if(block){const tail=selected.cloneRange();tail.setEnd(block,block.childNodes.length);const head=selected.cloneRange();head.setStart(block,0);if(!tail.toString().trim())block.after(inserted);else if(!head.toString().trim())block.before(inserted);else selected.insertNode(inserted);}else selected.insertNode(inserted);}d.querySelector('#inline').remove();
      });
    };
  };
 });
 w.eval(source);
 return {w,d,checkpoints,creates:()=>creates,uploads:()=>uploads,rejectionWait:()=>clock-rejectedAt,send:msg=>new Promise(resolve=>listener(msg,{},resolve))};
}
function bundle(){return {job:{id:'a'.repeat(32),account:'servasyy',draft_url:'',checkpoint:{}},title:'自动上传测试',text:'开头中段结尾',html:'<p>开头</p><p>⟦XARTICLE_IMAGE_01⟧</p><p>中段</p><p>⟦XARTICLE_IMAGE_02⟧</p><p>结尾</p>',cover:{name:'cover.webp',data:'YQ==',sha256:'cover-hash'},images:[{id:'01',name:'1.png',data:'YQ==',sha256:'hash1',marker:'⟦XARTICLE_IMAGE_01⟧'},{id:'02',name:'2.jpg',data:'YQ==',sha256:'hash2',marker:'⟦XARTICLE_IMAGE_02⟧'}]};}
async function upload(f,b){const p=await f.send({command:'prepare',bundle:b});assert(!p.error,p.error);b.job.draft_url=p.url;const r=await f.send({command:'upload',bundle:b});assert(!r.error,r.error);b.job.checkpoint=r.checkpoint;return r;}
test('automatic creation, native cover crop, inline uploads, saved identities and anchors',async()=>{
 const f=fixture(),b=bundle(),r=await upload(f,b);
 assert.equal(f.creates(),1);assert.equal(f.uploads(),3);assert.equal(r.evidence.image_count,2);assert.equal(r.evidence.cover_checked,true);assert.equal(r.evidence.positions_checked,true);
 assert.equal(f.checkpoints[0].draft_url,'https://x.com/compose/articles/edit/555');
 assert.equal(f.d.querySelector('textarea').value,b.title);
 // A later saved editor can use different X CDN rendition parameters.
 for(const img of f.d.querySelectorAll('img'))img.src=img.src.replace('name=small','name=large').replace('format=jpg','format=webp');
 const saved=await f.send({command:'check',bundle:b});assert(!saved.error,saved.error);
 const retry=await f.send({command:'upload',bundle:b});assert(!retry.error,retry.error);assert.equal(f.uploads(),3);
});
test('new draft binding is persisted before an editor-render failure',async()=>{
 const f=fixture({noEditor:true}),r=await f.send({command:'prepare',bundle:bundle()});
 assert.equal(r.code,'EDITOR_NOT_READY');assert.equal(f.creates(),1);assert.equal(f.checkpoints[0].draft_url,'https://x.com/compose/articles/edit/555');
});
test('two independent Write controls block without arbitrary first match',async()=>{
 const f=fixture({ambiguousCreate:true}),r=await f.send({command:'prepare',bundle:bundle()});assert.equal(r.code,'X_UPLOAD_TIMEOUT');assert.equal(f.creates(),0);
});
test('image identities in wrong text positions cannot pass the saved audit',async()=>{
 const f=fixture(),b=bundle();await upload(f,b);
 const body=f.d.querySelector('[contenteditable]'),imgs=body.querySelectorAll('img');body.append(imgs[0]);
 const r=await f.send({command:'check',bundle:b});assert.equal(r.code,'MEDIA_AUDIT_FAILED');
});
test('a missing cover cannot pass audit even when all body images are present',async()=>{
 const f=fixture(),b=bundle();await upload(f,b);f.d.querySelector('#cover').remove();
 const r=await f.send({command:'check',bundle:b});assert.equal(r.code,'COVER_AUDIT_FAILED');
});
test('an uncertain file submission is saved before dispatch and never blindly repeated',async()=>{
 const f=fixture({rejectCover:true}),b=bundle();const p=await f.send({command:'prepare',bundle:b});b.job.draft_url=p.url;
 const r=await f.send({command:'upload',bundle:b});assert.equal(r.code,'X_UPLOAD_TIMEOUT');assert.equal(f.uploads(),1);
 b.job.checkpoint=f.checkpoints.at(-1).checkpoint;assert.equal(b.job.checkpoint.pending.kind,'cover');
 const retry=await f.send({command:'upload',bundle:b});assert.equal(retry.code,'UPLOAD_UNCERTAIN');assert.equal(f.uploads(),1);
});
test('only a workbench-confirmed article completes the native publish dialog once',async()=>{
 const f=fixture(),b=bundle();await upload(f,b);
 let first=0,final=0;
 const publish=f.d.createElement('button');publish.textContent='Publish';f.d.querySelector('main').append(publish);
 publish.onclick=()=>{
  first++;f.d.querySelector('main').insertAdjacentHTML('beforeend','<div role="dialog" id="publish-dialog"><button>Publish</button></div>');
  f.d.querySelector('#publish-dialog button').onclick=()=>{final++;f.w.history.replaceState({},'','/servasyy/article/777');f.d.querySelector('#publish-dialog').remove();};
 };
 let r=await f.send({command:'publish',bundle:b});assert.equal(r.code,'PUBLISH_NOT_CONFIRMED');assert.equal(first,0);
 b.job.status='publish_requested';r=await f.send({command:'publish',bundle:b});assert(!r.error,r.error);assert.equal(r.url,'https://x.com/servasyy/article/777');assert.equal(first,1);assert.equal(final,1);
});
test('genuine text mismatch reports an exact difference and uploads no files',async()=>{
 const f=fixture(),b=bundle();b.text='开头有改中段结尾';
 const p=await f.send({command:'prepare',bundle:b});b.job.draft_url=p.url;
 const r=await f.send({command:'upload',bundle:b});
 assert.equal(r.code,'DRAFT_CONTENT_MISMATCH');assert.equal(r.diagnostics.phase,'text_check');assert.equal(r.diagnostics.title.matches,true);
 assert.equal(r.diagnostics.body.first_difference.index,2);assert.equal(r.diagnostics.body.expected_chars,8);assert.equal(r.diagnostics.body.observed_chars,6);assert.equal(f.uploads(),0);
});
test('X emoji images remain article text, not uploaded body media',async()=>{
 const f=fixture(),b=bundle();b.text='🔍开头中段结尾';b.html=b.html.replace('开头','🔍开头');
 const nativeInsert=f.d.execCommand;
 f.d.execCommand=(action,...args)=>{
  const ok=nativeInsert(action,...args);
  if(action==='insertHTML'){
   const first=f.d.querySelector('[contenteditable] p');first.innerHTML=first.innerHTML.replace('🔍','<img alt="🔍" src="https://abs.twimg.com/emoji/v2/svg/1f50d.svg">');
  }
  return ok;
 };
 const r=await upload(f,b);assert.equal(r.evidence.image_count,2);assert.equal(f.uploads(),3);assert.equal(f.d.querySelectorAll('[contenteditable] img').length,3);
 const saved=await f.send({command:'check',bundle:b});assert(!saved.error,saved.error);
});
test('body readback uses the native contents region and excludes adjacent editor controls',async()=>{
 const f=fixture(),b=bundle();await upload(f,b);
 const editor=f.d.querySelector('[contenteditable]');
 const contents=f.d.createElement('div');contents.setAttribute('data-contents','true');while(editor.firstChild)contents.append(editor.firstChild);editor.append(contents);
 const controls=f.d.createElement('span');controls.textContent='额外的编辑控件文字';editor.append(controls);
 const r=await f.send({command:'check',bundle:b});assert(!r.error,r.error);
});
test('the actual 35k-character article preserves all text and 29 image anchors in the DOM fixture',{skip:X_DOM_FIXTURE_MISSING?'需设置 ARTICLE_STUDIO_X_DOM_FIXTURE 指向本机夹具（未复制进公开仓库）':false},async()=>{
 const f=fixture(),b=JSON.parse(fs.readFileSync(X_DOM_FIXTURE,'utf8'));
 const r=await upload(f,b);assert.equal(r.evidence.image_count,29);assert.equal(f.uploads(),30);assert.equal(r.evidence.positions_checked,true);
 assert(!f.d.querySelector('[contenteditable]').textContent.includes('XARTICLE_IMAGE_'));
});
test('native model selection is synchronized and changing blob URLs preserve atomic identities',async()=>{
 const f=fixture({atomicMedia:true,modelSelection:true}),b=bundle();await upload(f,b);
 assert.equal(f.uploads(),3);assert.equal(b.job.checkpoint.media['01'].key,'block:media1-0-0');
 assert(!f.d.querySelector('[contenteditable]').textContent.includes('XARTICLE_IMAGE_'));
 for(const img of f.d.querySelectorAll('[contenteditable] img'))img.src='blob:https://x.com/saved-'+img.closest('section').getAttribute('data-offset-key');
 const saved=await f.send({command:'check',bundle:b});assert(!saved.error,saved.error);assert.equal(saved.evidence.positions_checked,true);
});
test('a changed picture cannot pass identity verification with the same native block ID',async()=>{
 const f=fixture({atomicMedia:true,modelSelection:true}),b=bundle();await upload(f,b);
 f.d.querySelector('[contenteditable] img').pixel=220;
 const saved=await f.send({command:'check',bundle:b});assert.equal(saved.code,'MEDIA_IDENTITY_MISMATCH');
});
test('a pending misplaced image is verified, removed and reinserted without duplicating earlier files',async()=>{
 const f=fixture({atomicMedia:true,modelSelection:true,misplaceAlways:true}),b=bundle();
 const p=await f.send({command:'prepare',bundle:b});b.job.draft_url=p.url;
 const first=await f.send({command:'upload',bundle:b});assert.equal(first.code,'IMAGE_POSITION_MISMATCH');
 b.job.checkpoint=f.checkpoints.at(-1).checkpoint;
 assert.equal(Object.keys(b.job.checkpoint.media).length,1);assert.equal(b.job.checkpoint.pending.id,'02');
 assert.equal(f.uploads(),4);assert.equal(b.job.checkpoint.position_retries['02'],1);
 assert.equal(b.job.checkpoint.recoveries.length,1);
 // A second wrong insertion stops after one repair instead of an endless loop.
 assert.equal(f.d.querySelectorAll('[contenteditable] img').length,2);
});
test('a saved pending submission is registered without uploading the same file again',async()=>{
 const f=fixture({atomicMedia:true,modelSelection:true}),b=bundle();await upload(f,b);
 const saved=b.job.checkpoint.media['02'];delete b.job.checkpoint.media['02'];
 b.job.checkpoint.pending={kind:'body',id:'02',sha256:'hash2',marker:b.images[1].marker,before:[b.job.checkpoint.media['01'].key]};
 const resumed=await f.send({command:'upload',bundle:b});assert(!resumed.error,resumed.error);
 assert.equal(f.uploads(),3);assert.equal(resumed.checkpoint.media['02'].key,saved.key);
});
test('a confirmed absent atomic block is restored from its source while other images are preserved',async()=>{
 const f=fixture({atomicMedia:true,modelSelection:true}),b=bundle();await upload(f,b);
 f.d.querySelector('[contenteditable] section').remove();
 const resumed=await f.send({command:'upload',bundle:b});assert(!resumed.error,resumed.error);
 assert.equal(resumed.evidence.image_count,2);assert.equal(f.uploads(),4);
 assert.equal(resumed.checkpoint.media['02'].key,'block:media2-0-0');
 assert.equal(resumed.checkpoint.missing_media[0].id,'01');
});
test('marker removal uses the native paste model rather than DOM-only deletion',async()=>{
 const f=fixture({atomicMedia:true,modelSelection:true}),b=bundle(),native=f.d.execCommand;
 f.d.execCommand=(action,...args)=>{assert.notEqual(action,'delete');return native(action,...args);};
 const r=await upload(f,b);assert.equal(r.evidence.positions_checked,true);
});
test('known damaged anchor residue is repaired with a snapshot without dropping article text',async()=>{
 const f=fixture({atomicMedia:true,modelSelection:true}),b=bundle();
 const middle=Array.from({length:70},(_,i)=>String.fromCharCode(0x4e00+i)).join('');
 b.text='开头。'+middle+'结尾';b.html=b.html.replace('开头','开头。').replace('中段',middle);
 await upload(f,b);
 const first=f.d.querySelector('[contenteditable] p');first.append(f.d.createTextNode('XARTICLE_IMAGE_01⟧。'));
 const resumed=await f.send({command:'upload',bundle:b});assert(!resumed.error,resumed.error);
 assert.equal(resumed.evidence.positions_checked,true);assert.equal(f.uploads(),3);
 assert.equal(resumed.checkpoint.text_repairs.length,1);assert(resumed.checkpoint.text_repairs[0].backup_text.includes('XARTICLE_IMAGE_01⟧。'));
});

test('one verified wrong insertion is repaired automatically without a user resume',async()=>{
 const f=fixture({atomicMedia:true,modelSelection:true,misplaceOnce:true}),b=bundle();
 const r=await upload(f,b);assert.equal(r.evidence.image_count,2);assert.equal(f.uploads(),4);
 assert.equal(r.checkpoint.media['01'].key,'block:media1-0-0');assert.equal(r.checkpoint.media['02'].key,'block:media3-0-0');
 assert.equal(r.checkpoint.position_retries['02'],1);assert.equal(r.checkpoint.recoveries.length,1);
});
test('a different article with consecutive images at its beginning and end needs no cover',async()=>{
 const f=fixture({atomicMedia:true,modelSelection:true}),b=bundle();
 b.title='另一篇：连续配图';b.cover=null;b.text='新的开头正文和结束';
 b.images.push({...b.images[1],id:'03',name:'3.png',sha256:'hash3',marker:'⟦XARTICLE_IMAGE_03⟧'});
 b.html='<p>'+b.images[0].marker+'</p><p>'+b.images[1].marker+'</p><h2>新的开头</h2><p><strong>正文</strong>和<em>结束</em></p><p>'+b.images[2].marker+'</p>';
 const r=await upload(f,b);assert.equal(r.evidence.image_count,3);assert.equal(f.uploads(),3);assert.equal(r.checkpoint.cover,undefined);
 const checked=await f.send({command:'check',bundle:b});assert(!checked.error,checked.error);
 assert.equal(f.d.querySelector('h2').textContent,'新的开头');assert.equal(f.d.querySelector('strong').textContent,'正文');
});

test('restoring a missing second consecutive image preserves the first image order',async()=>{
 const f=fixture({atomicMedia:true,modelSelection:true}),b=bundle();b.cover=null;b.text='正文';
 b.html='<p>'+b.images[0].marker+'</p><p>'+b.images[1].marker+'</p><p>正文</p>';
 await upload(f,b);f.d.querySelectorAll('[contenteditable] section')[1].remove();
 const r=await f.send({command:'upload',bundle:b});assert(!r.error,r.error);assert.equal(r.evidence.order_checked,true);
 assert.equal(r.checkpoint.media['01'].key,'block:media1-0-0');assert.equal(r.checkpoint.media['02'].key,'block:media3-0-0');
});
test('a previously removed atomic block that reappears is deleted from the native model',async()=>{
 const f=fixture({atomicMedia:true,modelSelection:true}),b=bundle();await upload(f,b);
 const extra=f.d.querySelector('[contenteditable] section').cloneNode(true);extra.setAttribute('data-offset-key','old-0-0');extra.querySelector('img').pixel=20;
 const p=f.d.querySelector('[contenteditable] p'),boundary=f.d.createElement('p');boundary.append(f.d.createTextNode(' '));p.after(extra,boundary);
 b.job.checkpoint.recoveries=[{id:'01',key:'block:old-0-0'}];
 const r=await f.send({command:'upload',bundle:b});assert(!r.error,r.error);
 assert.equal(r.checkpoint.orphan_recoveries[0].key,'block:old-0-0');assert.equal(f.d.querySelectorAll('[contenteditable] section').length,2);
 assert.equal(f.uploads(),3);assert.equal(r.evidence.image_count,2);
});
test('an accidental paragraph split is detected and restored without moving images',async()=>{
 const f=fixture({atomicMedia:true,modelSelection:true}),b=bundle();b.text='第一段完整内容中段结尾';b.html=b.html.replace('开头','第一段完整内容');
 await upload(f,b);
 const p=f.d.querySelector('[contenteditable] p');p.textContent='第一段';const extra=f.d.createElement('p');extra.textContent='完整内容';p.after(extra);
 const wrong=await f.send({command:'check',bundle:b});assert.equal(wrong.code,'PARAGRAPH_AUDIT_FAILED');
 const r=await f.send({command:'upload',bundle:b});assert(!r.error,r.error);
 assert.equal(r.evidence.paragraphs_checked,true);assert.equal(r.checkpoint.paragraph_repairs.length,1);assert.equal(f.uploads(),3);
 assert.equal(r.checkpoint.media['01'].key,'block:media1-0-0');
});


test('confirmed media disappearance recovers once automatically without duplicating saved images',async()=>{
 const f=fixture({atomicMedia:true,modelSelection:true,loseOnce:true}),b=bundle();
 const r=await upload(f,b);assert.equal(r.evidence.image_count,2);assert.equal(f.uploads(),4);
 assert.equal(r.checkpoint.media['01'].key,'block:media1-0-0');assert.equal(r.checkpoint.media['02'].key,'block:media3-0-0');
 assert.equal(r.checkpoint.missing_retries['02'],1);assert.equal(r.checkpoint.missing_media.length,1);
});
test('a repeatedly disappearing media block stops instead of retrying forever',async()=>{
 const f=fixture({atomicMedia:true,modelSelection:true,loseAlways:true}),b=bundle();
 const p=await f.send({command:'prepare',bundle:b});b.job.draft_url=p.url;
 const r=await f.send({command:'upload',bundle:b});assert.equal(r.code,'MEDIA_AUDIT_FAILED');assert.equal(f.uploads(),4);
 assert.equal(f.checkpoints.at(-1).checkpoint.missing_retries['02'],1);
});
test('each marker is removed before its file submission, leaving the source text and media positions intact',async()=>{
 const f=fixture({atomicMedia:true,modelSelection:true}),b=bundle();
 const record=f.w.chrome.runtime.sendMessage;
 f.w.chrome.runtime.sendMessage=async msg=>{
  if(msg.payload?.checkpoint?.pending?.kind==='body'){
   const item=b.images.find(x=>x.id===msg.payload.checkpoint.pending.id);
   assert(!f.d.querySelector('[contenteditable]').textContent.includes(item.marker));
  }
  return record(msg);
 };
 const r=await upload(f,b);assert.equal(r.evidence.positions_checked,true);assert.equal(f.uploads(),3);
});


test('native media-limit feedback persists a precise limit and prevents silent completion',async()=>{
 const f=fixture({atomicMedia:true,modelSelection:true,maxImages:1}),b=bundle();
 b.images.push({id:'03',name:'3.jpg',data:'YQ==',sha256:'hash3',marker:'⟦XARTICLE_IMAGE_03⟧'});b.html+='<p>⟦XARTICLE_IMAGE_03⟧</p>';
 const p=await f.send({command:'prepare',bundle:b});b.job.draft_url=p.url;
 const r=await f.send({command:'upload',bundle:b});assert.equal(r.code,'MEDIA_LIMIT');
 assert(f.rejectionWait()<1000,'native limit must stop immediately, not after the 45-second upload timeout');
 assert.match(r.error,/正文 3 张/);assert.match(r.error,/超出已实测单篇上限 2 张/);assert(!r.error.includes('分篇适配'));
 const cp=f.checkpoints.at(-1).checkpoint;assert.equal(cp.native_limits.body_images,1);assert.equal(cp.native_limits.account,'servasyy');assert.equal(Object.keys(cp.media).length,1);
 b.job.checkpoint=cp;const again=await f.send({command:'upload',bundle:b});assert.equal(again.code,'MEDIA_LIMIT');assert.equal(f.uploads(),3);
});
test('a reloaded draft proves an unsuccessful file submission absent before one retry',async()=>{
 const f=fixture({atomicMedia:true,modelSelection:true,rejectInlineOnce:true}),b=bundle();
 const p=await f.send({command:'prepare',bundle:b});b.job.draft_url=p.url;
 const first=await f.send({command:'upload',bundle:b});assert.equal(first.code,'X_UPLOAD_TIMEOUT');
 b.job.checkpoint=f.checkpoints.at(-1).checkpoint;
 const second=await f.send({command:'upload',bundle:b});assert(!second.error,second.error);assert.equal(second.evidence.image_count,2);
 assert.equal(second.checkpoint.submission_retries['02'],1);assert.equal(f.uploads(),4);assert.equal(second.checkpoint.media['01'].key,'block:media1-0-0');
});
