/* Uses only the rendered X UI. No cookies, credentials, or private endpoints. */
const visible=e=>e?.getClientRects().length>0;
const norm=s=>String(s||'').replace(/\s+/g,'');
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
let activeOperation=false;
const documentInstance=crypto.randomUUID();
function failure(code,message,diagnostics){const e=Error(message);e.code=code;if(diagnostics)e.diagnostics=diagnostics;return e;}
function account(){
  // Read only dedicated, visible account UI, never arbitrary page mentions.
  const handles=new Set();
  for(const link of document.querySelectorAll('a[data-testid="AppTabBar_Profile_Link"]')){
    if(!visible(link))continue;
    const handle=new URL(link.href).pathname.slice(1);
    if(/^[A-Za-z0-9_]{1,15}$/.test(handle))handles.add(handle.toLowerCase());
  }
  for(const control of document.querySelectorAll('[data-testid="SideNav_AccountSwitcher_Button"]')){
    if(!visible(control))continue;
    for(const match of control.innerText.matchAll(/@([A-Za-z0-9_]{1,15})(?![A-Za-z0-9_])/g))handles.add(match[1].toLowerCase());
  }
  if(handles.size>1)throw failure('ACCOUNT_AMBIGUOUS','X 账号显示不一致，请检查当前账号');
  if(handles.size===1)return [...handles][0];
  throw failure('ACCOUNT_NOT_VISIBLE','当前编辑页没有可见的账号信息，正在通过 X 文章列表核验');
}
async function waitAccount(){
  const until=Date.now()+20000;
  while(Date.now()<until){
    try{return account();}catch(e){if(e.code!=='ACCOUNT_NOT_VISIBLE')throw e;}
    await sleep(300);
  }
  throw failure('ACCOUNT_NOT_READY','X 账号信息未加载完成，请检查文章列表页是否能正常打开；不会据此判定未登录');
}
async function ensureAccount(bundle){
  let observed;
  try{observed=account();}catch(e){
    if(e.code!=='ACCOUNT_NOT_VISIBLE')throw e;
    const response=await chrome.runtime.sendMessage({command:'verify-account',id:bundle.job.id});
    if(!response?.account)throw failure(response?.code||'ACCOUNT_NOT_READY',response?.error||'本机账号核验连接中断，已停止操作');
    observed=response.account;
  }
  if(observed!==bundle.job.account)throw failure('ACCOUNT_MISMATCH','实际登录 @'+observed+'，目标为 @'+bundle.job.account+'；请在 X 切换账号');
  return observed;
}
const editPath=/^\/compose\/articles\/edit\/\d+/;
const enabled=e=>!e.disabled&&e.getAttribute('aria-disabled')!=='true';
function controls(pattern,root=document){
  const hits=[...root.querySelectorAll('button,[role="button"],a[role="link"],a[href],[role="menuitem"]')]
    .filter(e=>visible(e)&&enabled(e)&&pattern.test((e.innerText||e.textContent||e.getAttribute('aria-label')||'').trim()));
  // A button and its nested role=button describe one actionable control.
  return hits.filter(e=>!hits.some(other=>other!==e&&e.contains(other)));
}
function button(pattern,root=document){
  const hits=controls(pattern,root);
  if(hits.length!==1)throw failure('CONTROL_NOT_UNIQUE','X 控件识别失败（匹配 '+hits.length+' 个），已停止并保留进度');
  return hits[0];
}
function editor(){
  const preferred=[...document.querySelectorAll('[data-testid="composer"] [contenteditable="true"]')].filter(visible);
  const fallback=[...document.querySelectorAll('[contenteditable="true"][role="textbox"]')]
    .filter(e=>visible(e)&&!/title|标题/i.test(e.getAttribute('aria-label')||e.getAttribute('data-placeholder')||''));
  const all=preferred.length?preferred:fallback;
  if(all.length!==1)throw failure('BODY_NOT_UNIQUE','无法唯一识别 X 文章正文输入框');return all[0];
}
function title(){
  let all=[...document.querySelectorAll('textarea,input,[contenteditable="true"]')]
    .filter(e=>visible(e)&&/title|标题/i.test([e.placeholder,e.getAttribute('aria-label'),e.getAttribute('data-placeholder')].join(' ')));
  // The native X Article title is a textarea; some versions expose no placeholder.
  if(!all.length)all=[...document.querySelectorAll('textarea')].filter(e=>visible(e)&&!e.closest('[role="dialog"]'));
  if(all.length!==1)throw failure('TITLE_NOT_UNIQUE','无法唯一识别 X 文章标题输入框');return all[0];
}
async function waitEditor(bound){
  const until=Date.now()+25000;let last;
  while(Date.now()<until){
    try{editor();title();return;}catch(e){last=e;}
    await sleep(300);
  }
  const code=bound?'DRAFT_UNAVAILABLE':'EDITOR_NOT_READY';
  throw failure(code,(bound?'原草稿编辑器未就绪，已保留绑定；':'新草稿已创建并保存链接，但编辑器未就绪；')+(last?.message||'请检查页面加载情况'),{phase:'editor_lookup',url:location.href,ready:document.readyState,visibility:document.visibilityState,title:document.title,body_sample:document.body.innerText.slice(-1800),controls:[...document.querySelectorAll('[contenteditable],textarea,input[type="file"]')].slice(0,12).map(e=>({tag:e.tagName,attrs:[...e.attributes].map(a=>[a.name,a.value]),parent:e.parentElement.outerHTML.slice(0,450)}))});
}
function selectContents(e){e.focus();const range=document.createRange();range.selectNodeContents(e);const s=window.getSelection();s.removeAllRanges();s.addRange(range);}
function titleValue(){const e=title();return 'value' in e?e.value:e.innerText;}
function setTitle(value){
  const e=title();
  if('value' in e){const proto=e.tagName==='TEXTAREA'?HTMLTextAreaElement.prototype:HTMLInputElement.prototype;Object.getOwnPropertyDescriptor(proto,'value').set.call(e,value);e.dispatchEvent(new Event('input',{bubbles:true}));}
  else{selectContents(e);if(!document.execCommand('insertText',false,value))throw failure('TITLE_WRITE_FAILED','X 未接受标题写入');}
}
function contentRoot(){
  const e=editor(),all=[...e.querySelectorAll('[data-contents="true"]')];
  if(all.length>1)throw failure('TEXT_ROOT_AMBIGUOUS','X 正文内容区域存在歧义，已停止读取');
  return all[0]||e;
}
function emojiImage(e){
  try{const src=new URL(e.currentSrc||e.src);return src.hostname==='abs.twimg.com'&&src.pathname.includes('/emoji/');}catch{return false;}
}
function semanticText(root){
  // Read stored text from the body content, not its toolbar or visual layout.
  // X can represent an emoji as an image; its alt is still article text.
  if(root.nodeType===Node.TEXT_NODE)return root.textContent;
  if(root.nodeType===Node.ELEMENT_NODE){
    if(root.matches('script,style,[data-placeholder="true"]'))return '';
    // Native atomic media include editor controls and caption placeholders.
    if(root.matches('[data-block="true"][contenteditable="false"]'))return '';
    if(root.tagName==='IMG')return emojiImage(root)?root.getAttribute('alt')||'':'';
    if(root.tagName==='BR')return '\n';
  }
  return [...root.childNodes].map(semanticText).join('');
}
function bodyText(){return semanticText(contentRoot());}
const withoutMarkers=s=>norm(s.replace(/⟦XARTICLE_IMAGE_\d+⟧/g,''));
function textDifference(expected,observed){
  const a=norm(expected),b=norm(observed);let index=0;
  while(index<Math.min(a.length,b.length)&&a[index]===b[index])index++;
  let end=0;while(end<Math.min(a.length,b.length)-index&&a[a.length-1-end]===b[b.length-1-end])end++;
  return {matches:a===b,expected_chars:a.length,observed_chars:b.length,
    ...(a===b?{}:{span_difference:{index,expected:a.slice(index,a.length-end).slice(0,200),observed:b.slice(index,b.length-end).slice(0,200),expected_length:a.length-end-index,observed_length:b.length-end-index},first_difference:{index,expected:a.slice(Math.max(0,index-24),index+80),observed:b.slice(Math.max(0,index-24),index+80),
      expected_codepoints:[...a.slice(index,index+6)].map(c=>'U+'+c.codePointAt(0).toString(16).toUpperCase()),
      observed_codepoints:[...b.slice(index,index+6)].map(c=>'U+'+c.codePointAt(0).toString(16).toUpperCase())}})};
}
function textDiagnostics(bundle,cp){
  const root=contentRoot(),rawImages=[...root.querySelectorAll('img')];
  return {phase:'text_check',title:textDifference(bundle.title,titleValue()),body:textDifference(bundle.text,withoutMarkers(bodyText())),
    rendered_body_images:rawImages.filter(e=>!emojiImage(e)).length,rendered_emoji_images:rawImages.filter(emojiImage).length,
    expected_body_images:bundle.images.length,uploaded_body_images:Object.keys(cp.media||{}).length,
    expected_cover:Boolean(bundle.cover),uploaded_cover:Boolean(cp.cover),observed_at:new Date().toISOString()};
}
async function waitText(bundle,cp){
  const until=Date.now()+8000;let diagnostics;
  do{
    diagnostics=textDiagnostics(bundle,cp);
    if(diagnostics.title.matches&&diagnostics.body.matches)return;
    await sleep(300);
  }while(Date.now()<until);
  const what=diagnostics.title.matches?'正文':'标题';
  const diff=diagnostics.title.matches?diagnostics.body:diagnostics.title;
  throw failure('DRAFT_CONTENT_MISMATCH',what+'校验未通过（原文 '+diff.expected_chars+' 字符，草稿 '+diff.observed_chars+' 字符）；已暂停上传并保存具体差异',diagnostics);
}
function mediaKey(src){
  // X changes size/format query parameters after saving. Preserve media identity.
  try{const url=new URL(src);if(url.hostname==='pbs.twimg.com'&&url.pathname.startsWith('/media/'))return url.origin+url.pathname.replace(/\.(jpg|jpeg|png|webp)$/i,'');}catch{}
  return src;
}
function images(){return [...contentRoot().querySelectorAll('img')].filter(e=>!emojiImage(e)).map(e=>{
  const block=e.closest('[data-block="true"][contenteditable="false"]'),blockKey=block?.getAttribute('data-offset-key');
  return {src:e.currentSrc||e.src,key:blockKey?'block:'+blockKey:mediaKey(e.currentSrc||e.src),loaded:e.complete&&e.naturalWidth>0,element:e,block};
});}
const sourceRasters=new Map();
function raster(image){const c=document.createElement('canvas');c.width=c.height=128;const ctx=c.getContext('2d');ctx.fillStyle='#fff';ctx.fillRect(0,0,128,128);ctx.drawImage(image,0,0,128,128);return ctx.getImageData(0,0,128,128).data;}
async function sourceRaster(item){
  if(!sourceRasters.has(item.sha256))sourceRasters.set(item.sha256,(async()=>{const bitmap=await createImageBitmap(file(item));const pixels=raster(bitmap),ratio=bitmap.width/bitmap.height;bitmap.close();return {pixels,ratio};})());
  return sourceRasters.get(item.sha256);
}
async function imageDifference(item,image){
  const source=await sourceRaster(item);if(Math.abs(source.ratio-image.naturalWidth/image.naturalHeight)>0.01)return Infinity;
  const pixels=raster(image);let total=0;for(let i=0;i<pixels.length;i++)if(i%4!==3)total+=Math.abs(pixels[i]-source.pixels[i]);return total/(pixels.length*0.75);
}
async function verifyImage(item,image,bundle){
  if(!image.block)return; // Older native editors expose a stable CDN image directly.
  const difference=await imageDifference(item,image.element);
  if(difference<=6)return {difference,method:'source_raster'};
  // X downsizes large diagrams and recompresses their small text. A unique
  // nearest source in this immutable package provides an additional identity
  // check; never accept merely by increasing the pixel-error threshold.
  const candidates=bundle?await Promise.all(bundle.images.map(async candidate=>({id:candidate.id,difference:await imageDifference(candidate,image.element)}))):[];
  candidates.sort((a,b)=>a.difference-b.difference);
  if(difference<=16&&candidates[0]?.id===item.id&&(!candidates[1]||candidates[1].difference-difference>Math.max(0.5,difference*0.1)))return {difference,method:'unique_nearest_source',runner_up:candidates[1]};
  throw failure('MEDIA_IDENTITY_MISMATCH','配图画面与源文件无法唯一对应，已停止上传',{phase:'media_identity',id:item.id,difference,candidates:candidates.slice(0,4)});
}
async function mediaDiagnostics(bundle,cp){
  const root=contentRoot(),list=images(),prefixes=expectedPrefixes(bundle);
  const blocks=[...root.querySelectorAll('[data-block="true"]')].filter(e=>e.querySelector('[contenteditable="false"],[role="button"]'));
  return {phase:'media_check',pending:cp.pending||null,positions:list.map(x=>({key:x.key,prefix_chars:textBefore(root,x.element).length})),
    inventory:[...root.querySelectorAll('[data-block="true"][contenteditable="false"]')].map(e=>({key:'block:'+e.getAttribute('data-offset-key'),prefix_chars:textBefore(root,e).length,images:e.querySelectorAll('img').length})),
    blocks:blocks.slice(-2).map(e=>({html:e.outerHTML.slice(0,700),prefix_chars:textBefore(root,e).length})),
    expected:bundle.images.map(i=>({id:i.id,prefix_chars:prefixes[i.id].length})),
    images:await Promise.all([...new Map([...list.filter(x=>!Object.values(cp.media||{}).some(m=>m.key===x.key)),...list.slice(-3)].map(x=>[x.key,x])).values()].slice(0,5).map(async x=>{
      let sha256=null;if(x.src.startsWith('blob:'))try{
        const bytes=await (await fetch(x.src,{signal:AbortSignal.timeout(3000)})).arrayBuffer();
        sha256=[...new Uint8Array(await crypto.subtle.digest('SHA-256',bytes))].map(n=>n.toString(16).padStart(2,'0')).join('');
      }catch{}
      const prefix=textBefore(root,x.element);
      return {src:x.src.slice(0,300),sha256,width:x.element.naturalWidth,height:x.element.naturalHeight,
        key:x.key,parent:x.element.parentElement.parentElement.outerHTML.slice(0,500),prefix_chars:prefix.length,prefix_tail:prefix.slice(-60)};
    }))};
}
function coverImages(){
  const body=editor();
  return [...document.querySelectorAll('main img,[role="main"] img')]
    .filter(e=>visible(e)&&!body.contains(e)&&!e.closest('[role="dialog"]')&&/^https:\/\/pbs\.twimg\.com\/media\//.test(e.currentSrc||e.src))
    .map(e=>({src:e.currentSrc||e.src,key:mediaKey(e.currentSrc||e.src),loaded:e.complete&&e.naturalWidth>0}));
}
function file(item){
  const bytes=Uint8Array.from(atob(item.data),c=>c.charCodeAt(0));
  const ext=item.name.split('.').pop().toLowerCase(),type={png:'image/png',jpg:'image/jpeg',jpeg:'image/jpeg',webp:'image/webp',gif:'image/gif'}[ext];
  if(!type)throw failure('MEDIA_TYPE_UNSUPPORTED','不支持的图片格式：'+ext);
  return new File([bytes],item.name,{type});
}
function insertHTML(html){
  // Route through the editor's native paste handler so React/Draft.js updates
  // its stored state. execCommand('insertHTML') only changed the visible DOM.
  const e=editor();selectContents(e);
  const data=new DataTransfer();data.setData('text/html',html);
  const parsed=document.createElement('div');parsed.innerHTML=html;
  data.setData('text/plain',parsed.innerText||parsed.textContent);
  const event=new ClipboardEvent('paste',{clipboardData:data,bubbles:true,cancelable:true});
  e.dispatchEvent(event);
  if(!event.defaultPrevented)throw failure('BODY_WRITE_FAILED','X 未接管正文粘贴事件，已停止写入');
}
function markerRange(marker){
  const root=editor(),walk=document.createTreeWalker(root,NodeFilter.SHOW_TEXT);let n,hits=[];
  while(n=walk.nextNode()){let i=-1;while((i=n.textContent.indexOf(marker,i+1))>=0)hits.push([n,i]);}
  if(hits.length!==1)throw failure('IMAGE_ANCHOR_INVALID','图片占位标记缺失或重复，停止续传以免图片错位');
  const [node,i]=hits[0],r=document.createRange();r.setStart(node,i);r.setEnd(node,i+marker.length);return r;
}
async function progress(bundle,message,checkpoint,extra={}){
  const r=await chrome.runtime.sendMessage({command:'upload-progress',id:bundle.job.id,payload:{status:'running',observed_account:await ensureAccount(bundle),message,checkpoint,...extra}});
  if(!r||r.error)throw failure(r?.code||'WORKBENCH_DISCONNECTED',r?.error||'工作台连接中断，停止上传并保留原草稿');
}
async function pollValue(read,message,ms=30000){
  const until=Date.now()+ms;while(Date.now()<until){const v=read();if(v)return v;await sleep(300);}
  throw failure('X_UPLOAD_TIMEOUT',message+'；已保存进度，不会自动重复提交文件');
}
function uploadInput(input,item){
  const transfer=new DataTransfer();transfer.items.add(file(item));input.files=transfer.files;
  input.dispatchEvent(new Event('change',{bubbles:true}));
}
async function uploadCover(bundle,cp){
  if(!bundle.cover)return;
  if(cp.cover){
    if(cp.cover.sha256!==bundle.cover.sha256||!coverImages().some(e=>e.loaded&&e.key===(cp.cover.key||mediaKey(cp.cover.src))))throw failure('COVER_IDENTITY_MISMATCH','草稿封面与任务记录不符，已停止覆盖');
    return;
  }
  if(cp.pending)throw failure('UPLOAD_UNCERTAIN','上次文件提交结果尚未核验，已保留草稿并停止重复上传',await mediaDiagnostics(bundle,cp));
  const before=coverImages();if(before.length)throw failure('COVER_IDENTITY_MISMATCH','草稿已有未登记封面，已停止覆盖');
  const input=await pollValue(()=>{
    const all=[...document.querySelectorAll('input[type="file"][data-testid="fileInput"]')].filter(e=>!e.closest('[role="dialog"]'));
    return all.length===1?all[0]:null;
  },'未能唯一识别原生封面上传控件',15000);
  cp.phase='cover_upload';cp.pending={kind:'cover',id:'cover',sha256:bundle.cover.sha256};
  await progress(bundle,'正在通过 X 原生控件上传封面',cp);
  uploadInput(input,bundle.cover);
  const apply=await pollValue(()=>[...document.querySelectorAll('[data-testid="applyButton"]')].find(e=>visible(e)&&enabled(e)),'封面裁剪界面未加载');
  await ensureAccount(bundle);apply.click();
  const added=await pollValue(()=>{const all=coverImages().filter(x=>x.loaded&&!before.some(y=>y.key===x.key));return all.length===1?all[0]:null;},'封面上传后未显示');
  cp.cover={sha256:bundle.cover.sha256,key:added.key,src:added.src};delete cp.pending;
  await progress(bundle,'封面已上传，正在保存正文与配图',cp);
}
async function selectRange(target){
  const e=editor(),read=typeof target==='function'?target:()=>target;
  // A previous paste/render can temporarily block Draft's selection events.
  // Settle focus first, then reacquire the anchor and notify the native editor
  // across separate turns. Never assume a visible DOM caret is stored state.
  e.blur();await sleep(150);e.focus({preventScroll:true});await sleep(250);
  for(let i=0;i<3;i++){
    const range=read();
    if(!range.startContainer.isConnected||!range.endContainer.isConnected)throw failure('SELECTION_NOT_READY','正文重新渲染，插入位置尚未稳定；已保留进度');
    range.startContainer.parentElement?.scrollIntoView?.({block:'center'});
    const selection=window.getSelection();selection.removeAllRanges();selection.addRange(range);
    document.dispatchEvent(new Event('selectionchange',{bubbles:true}));
    e.dispatchEvent(new MouseEvent('mouseup',{bubbles:true}));
    e.dispatchEvent(new KeyboardEvent('keyup',{key:'Shift',bubbles:true}));
    await sleep(300);
  }
  const wanted=read(),selection=window.getSelection();
  if(!selection.rangeCount)throw failure('SELECTION_NOT_READY','X 正文光标丢失，已停止文件提交');
  const actual=selection.getRangeAt(0);
  const boundary=(node,offset)=>{
    const r=document.createRange();r.selectNodeContents(contentRoot());r.setEnd(node,offset);
    const fragment=r.cloneContents();return {text:withoutMarkers(semanticText(fragment)),media:[...fragment.querySelectorAll('[data-block="true"][contenteditable="false"]')].map(e=>e.getAttribute('data-offset-key'))};
  };
  const signature=range=>({start:boundary(range.startContainer,range.startOffset),end:boundary(range.endContainer,range.endOffset),selected:norm(semanticText(range.cloneContents())),collapsed:range.collapsed});
  // Native editors can canonicalize a paragraph-end caret into the following
  // empty block. Text AND preceding media must agree at both endpoints.
  const expected=signature(wanted),observed=signature(actual);
  if(JSON.stringify(expected)!==JSON.stringify(observed))throw failure('SELECTION_NOT_READY','X 正文光标未停在源稿位置，已停止文件提交',{phase:'selection_check',expected:{start_chars:expected.start.text.length,end_chars:expected.end.text.length,media:expected.start.media,selected:expected.selected.slice(0,80)},observed:{start_chars:observed.start.text.length,end_chars:observed.end.text.length,media:observed.start.media,selected:observed.selected.slice(0,80)}});
}
function cursorForPrefix(index,afterBlock=null){
  const walk=document.createTreeWalker(contentRoot(),NodeFilter.SHOW_TEXT|NodeFilter.SHOW_ELEMENT);let node,seen=0;
  while(node=walk.nextNode()){
    if(node.parentElement.closest('[contenteditable="false"],[data-placeholder="true"]'))continue;
    if(node.nodeType===Node.ELEMENT_NODE){
      if(node.matches('img')&&emojiImage(node)){
        if(seen===index&&(!afterBlock||afterBlock.compareDocumentPosition(node)&Node.DOCUMENT_POSITION_FOLLOWING)){const r=document.createRange();r.setStartBefore(node);r.collapse(true);return r;}
        seen+=norm(node.alt).length;
      }
      continue;
    }
    const text=node.textContent,markers=[...text.matchAll(/⟦XARTICLE_IMAGE_\d+⟧/g)];
    for(let i=0;i<text.length;i++){
      const marker=markers.find(m=>m.index===i);if(marker){i+=marker[0].length-1;continue;}
      if(seen===index&&(!afterBlock||afterBlock.compareDocumentPosition(node)&Node.DOCUMENT_POSITION_FOLLOWING)){const r=document.createRange();r.setStart(node,i);r.collapse(true);return r;}
      if(!/\s/.test(text[i]))seen++;
    }
    if(seen===index&&(!afterBlock||afterBlock.compareDocumentPosition(node)&Node.DOCUMENT_POSITION_FOLLOWING)){const r=document.createRange();r.setStart(node,text.length);r.collapse(true);return r;}
  }
  throw failure('IMAGE_ANCHOR_INVALID','无法定位原稿中的图片位置');
}
async function pasteAt(range,text){
  await selectRange(range);const data=new DataTransfer();data.setData('text/plain',text);
  const ev=new ClipboardEvent('paste',{clipboardData:data,bubbles:true,cancelable:true});editor().dispatchEvent(ev);
  if(!ev.defaultPrevented)throw failure('BODY_WRITE_FAILED','X 未接受文字修复，已保留进度');await sleep(300);
}
async function removeMarker(bundle,item,cp){
  if(!bodyText().includes(item.marker))return;
  // Let Draft's native paste handler remove the selection from its model.
  // execCommand('delete') can erase an atomic block only in the live DOM;
  // the next React render then corrupts the image/text boundary.
  await pasteAt(()=>markerRange(item.marker),' ');
  if(bodyText().includes(item.marker))throw failure('IMAGE_ANCHOR_INVALID','X 尚未保存图片占位文字的移除，已停止上传');
  await waitText(bundle,cp);
}
function missingSpans(expected,observed){
  const a=norm(expected),b=norm(observed),spans=[];let i=0,j=0;
  while(j<b.length){
    if(a[i]===b[j]){i++;j++;continue;}
    const anchor=b.slice(j,j+40),window=a.slice(i,i+120);
    const offset=window.indexOf(anchor);
    if(anchor.length<40||offset<=0||offset>80||window.indexOf(anchor,offset+1)!==-1)return null;
    spans.push({source_index:i,index:j,length:offset});i+=offset;
    if(spans.length>4)return null;
  }
  if(i!==a.length)return null;return spans;
}
function sourceSpan(text,index,length){
  let start=0,count=0;for(let i=0;i<text.length;i++){if(count===index){start=i;break;}if(!/\s/.test(text[i]))count++;}
  count=0;let end=start;for(;end<text.length&&count<length;end++)if(!/\s/.test(text[end]))count++;
  return text.slice(start,end).replace(/^[\r\n]+/,'');
}
async function restoreMissing(bundle,cp){
  const missing=missingSpans(bundle.text,withoutMarkers(bodyText()));
  if(!missing)throw failure('DRAFT_CONTENT_MISMATCH','草稿存在无法自动恢复的文字差异，已停止修复',textDiagnostics(bundle,cp));
  for(const span of missing.reverse())await pasteAt(cursorForPrefix(span.index),sourceSpan(bundle.text,span.source_index,span.length));
  await waitText(bundle,cp);
}
async function restoreExtraPunctuation(bundle,cp){
  const observed=withoutMarkers(bodyText()),spans=missingSpans(observed,bundle.text);
  function knownBoundary(fragment){
    if(/^[。，；：！？、]{1,4}$/.test(fragment))return true;
    return bundle.images.some(item=>cp.media[item.id]&&new RegExp('^'+item.marker.slice(1).replace(/[.*+?^${}()|[\]\\]/g,'\\$&')+'[。，；：！？、]{0,4}$').test(fragment));
  }
  if(!spans?.length||spans.some(s=>!knownBoundary(observed.slice(s.source_index,s.source_index+s.length))))return false;
  cp.text_repairs=cp.text_repairs||[];cp.text_repairs.push({backup_text:bodyText(),spans,at:new Date().toISOString()});
  await progress(bundle,'已保存边界差异快照，正在恢复源稿标点',cp);
  for(const span of [...spans].reverse()){
    const start=cursorForPrefix(span.source_index),end=cursorForPrefix(span.source_index+span.length),range=document.createRange();
    range.setStart(start.startContainer,start.startOffset);range.setEnd(end.startContainer,end.startOffset);
    await pasteAt(range,' ');
  }
  await waitText(bundle,cp);return true;
}
async function recoverRecordedMissing(bundle,cp){
  const root=contentRoot(),list=images(),known=Object.values(cp.media);
  const blocks=new Set([...root.querySelectorAll('[data-block="true"][contenteditable="false"]')].map(e=>'block:'+e.getAttribute('data-offset-key')));
  const missing=bundle.images.filter(item=>cp.media[item.id]?.key?.startsWith('block:')&&!blocks.has(cp.media[item.id].key));
  if(!missing.length)return [];
  if(list.some(x=>!known.some(k=>k.key===x.key)))throw failure('MEDIA_IDENTITY_MISMATCH','草稿有未登记配图，停止恢复缺失图片',await mediaDiagnostics(bundle,cp));
  await waitText(bundle,cp);
  if(missing.some(item=>(cp.missing_retries?.[item.id]||0)>=1))throw failure('MEDIA_AUDIT_FAILED','同一配图再次丢失，已保存草稿并停止自动重传',await mediaDiagnostics(bundle,cp));
  cp.missing_retries=cp.missing_retries||{};
  cp.missing_media=cp.missing_media||[];
  for(const item of missing){
    cp.missing_retries[item.id]=1;
    cp.missing_media.push({id:item.id,previous:cp.media[item.id],at:new Date().toISOString()});
    cp.recovered_ids=[...new Set([...(cp.recovered_ids||[]),item.id])];
    delete cp.media[item.id];
  }
  await progress(bundle,'已核实配图块缺失，按原文件和段落位置恢复一次',cp);
  return missing;
}
async function auditOrRecover(bundle,cp){
  try{await auditRecorded(bundle,cp);return;}catch(error){
    if(error.code!=='MEDIA_AUDIT_FAILED')throw error;
    const missing=await recoverRecordedMissing(bundle,cp);if(!missing.length)throw error;
    for(const item of missing)await insertImage(bundle,item,cp,bundle.images.indexOf(item));
    await auditRecorded(bundle,cp);
  }
}
async function auditRecorded(bundle,cp){
  const list=images(),root=contentRoot(),prefixes=expectedPrefixes(bundle);
  if(list.some(x=>!Object.values(cp.media).some(m=>m.key===x.key)))throw failure('UNEXPECTED_MEDIA','正文存在未登记的图片块，已停止后续插图',await mediaDiagnostics(bundle,cp));
  for(const item of bundle.images){
    const known=cp.media[item.id];if(!known)continue;
    const image=list.find(x=>x.key===known.key&&x.loaded);
    if(!image||textBefore(root,image.element)!==prefixes[item.id])throw failure('MEDIA_AUDIT_FAILED','已上传配图的位置或保存状态发生变化，已暂停',await mediaDiagnostics(bundle,cp));
    await verifyImage(item,image,bundle);
  }
}
function atomicByKey(key){return [...contentRoot().querySelectorAll('[data-block="true"][contenteditable="false"]')].find(e=>'block:'+e.getAttribute('data-offset-key')===key);}
async function removeAtomic(bundle,cp,key){
  const block=atomicByKey(key);if(!block)return;
  const blocks=[...contentRoot().querySelectorAll('[data-block="true"]')];
  const ordered=blocks.some(e=>e.getAttribute('contenteditable')!=='false')?blocks:[...contentRoot().children];
  const index=ordered.indexOf(block),previous=ordered[index-1],next=ordered[index+1];
  if(!previous||!next||previous.getAttribute('contenteditable')==='false'||next.getAttribute('contenteditable')==='false')throw failure('MEDIA_REMOVE_NOT_READY','图片块两侧没有可核验的文字边界，已保留原草稿');
  const previousKey=previous.getAttribute('data-offset-key'),nextKey=next.getAttribute('data-offset-key');
  const anchor=()=>{
    const all=[...contentRoot().querySelectorAll('[data-block="true"]')];
    const before=previousKey?all.find(e=>e.getAttribute('data-offset-key')===previousKey):previous;
    const after=nextKey?all.find(e=>e.getAttribute('data-offset-key')===nextKey):next;
    if(!before?.isConnected||!after?.isConnected)throw failure('SELECTION_NOT_READY','移除图片时正文边界已变化，已停止');
    const left=document.createTreeWalker(before,NodeFilter.SHOW_TEXT),right=document.createTreeWalker(after,NodeFilter.SHOW_TEXT);let last,n;
    while(n=left.nextNode())last=n;
    const first=right.nextNode(),range=document.createRange();
    range.setStart(last||before,last?last.textContent.length:before.childNodes.length);range.setEnd(first||after,0);
    const selected=range.cloneContents();
    const selectedBlocks=[...selected.querySelectorAll('[data-block="true"][contenteditable="false"]')];
    if(selectedBlocks.length!==1||'block:'+selectedBlocks[0].getAttribute('data-offset-key')!==key||withoutMarkers(semanticText(selected)))throw failure('MEDIA_REMOVE_NOT_READY','待移除范围包含额外正文或配图，已停止');
    return range;
  };
  // Native paste replaces the media-only range with a paragraph boundary.
  // Both neighbouring text blocks and their styled text stay in Draft's model.
  await pasteAt(anchor,'\n');
  await pollValue(()=>!atomicByKey(key),'完整图片块尚未移除',10000);
  await waitText(bundle,cp);await waitSaved();
  if(atomicByKey(key))throw failure('MEDIA_REMOVE_NOT_READY','图片块保存后重新出现，已停止重复上传');
}
async function recoverRemovedBlocks(bundle,cp){
  // Older native Remove-media clicks can leave a reusable atomic placeholder.
  // Touch only exact keys whose removal was already proved and checkpointed.
  const known=new Set(Object.values(cp.media).map(x=>x.key));
  for(const record of cp.recoveries||[]){
    if(known.has(record.key)||!atomicByKey(record.key))continue;
    const image=images().find(x=>x.key===record.key&&x.loaded);
    let item=bundle.images.find(i=>i.id===record.id);
    if(image){
      try{await verifyImage(item,image,bundle);}catch(error){
        const candidates=await Promise.all(bundle.images.map(async i=>({item:i,difference:await imageDifference(i,image.element)})));
        candidates.sort((a,b)=>a.difference-b.difference);const match=candidates[0];
        if(!match||(!cp.media[match.item.id]&&cp.pending?.id!==match.item.id)||match.difference>16||candidates[1]&&candidates[1].difference-match.difference<=Math.max(0.5,match.difference*0.1))throw error;
        await verifyImage(match.item,image,bundle);item=match.item;
      }
    }
    cp.orphan_recoveries=cp.orphan_recoveries||[];
    cp.orphan_recoveries.push({key:record.key,id:item.id,sha256:item.sha256,backup_text:bodyText(),at:new Date().toISOString()});
    if(cp.pending?.id===item.id)cp.pending.recovery={key:record.key,sha256:item.sha256,stage:'remove_verified'};
    await progress(bundle,'已核实旧图片空块，正在清理完整块以防误复用',cp);
    await removeAtomic(bundle,cp,record.key);
  }
}
function nativeLimitAlert(alerts){return (alerts||[]).find(text=>/exceeds the media limit per Article|超过.*(?:文章|媒体).*上限/i.test(text));}
async function recordNativeLimit(bundle,cp,count,alert){
  cp.native_limits={body_images:count,account:bundle.job.account,observed_at:new Date().toISOString(),message:alert};
  const message='图片数量超限：正文 '+bundle.images.length+' 张，X 已接受 '+count+' 张并拒绝第 '+(count+1)+' 张，超出已实测单篇上限 '+(bundle.images.length-count)+' 张。已停止上传；分批上传不能增加单篇文章容量。';
  await progress(bundle,message,cp);
  throw failure('MEDIA_LIMIT',message,{phase:'media_limit',limit:count,source_images:bundle.images.length,native_message:alert});
}
async function recoverPending(bundle,cp){
  const pending=cp.pending;
  if(pending?.kind!=='body'||!pending.before.every(k=>k.startsWith('block:')))return false;
  const item=bundle.images.find(i=>i.id===pending.id&&i.sha256===pending.sha256);
  const limitAlert=nativeLimitAlert(pending.alerts);if(limitAlert){await waitText(bundle,cp);await auditRecorded(bundle,cp);await recordNativeLimit(bundle,cp,pending.before.length,limitAlert);}
  await progress(bundle,'正在核验上次文件提交，尚未重传图片',cp,{diagnostics:await mediaDiagnostics(bundle,cp)});
  const extra=images().filter(x=>!pending.before.includes(x.key));
  if(item&&!extra.length&&!pending.recovery){
    // After reloading the bound draft, a zero-result submission is retryable
    // only if every prior media block, file and text position still matches.
    await waitText(bundle,cp);await auditRecorded(bundle,cp);await waitSaved();
    const live=images(),blocks=[...contentRoot().querySelectorAll('[data-block="true"][contenteditable="false"]')];
    if(live.length!==pending.before.length||pending.before.some(key=>!live.some(x=>x.key===key&&x.loaded))||blocks.length!==live.length)throw failure('UPLOAD_UNCERTAIN','上次提交仍有未核验的图片状态，不重传',await mediaDiagnostics(bundle,cp));
    if((cp.submission_retries?.[item.id]||0)>=1)throw failure('UPLOAD_REJECTED','同一文件两次提交后均未出现在 X 草稿，已保存进度和页面提示',await mediaDiagnostics(bundle,cp));
    cp.submission_retries=cp.submission_retries||{};cp.submission_retries[item.id]=1;
    cp.absent_submissions=cp.absent_submissions||[];cp.absent_submissions.push({pending:structuredClone(pending),saved_keys:live.map(x=>x.key),at:new Date().toISOString()});
    cp.recovered_ids=[...new Set([...(cp.recovered_ids||[]),item.id])];delete cp.pending;
    await progress(bundle,'重新打开草稿后已核实文件未添加，保留其他图片并重试一次',cp);return true;
  }
  if(item&&pending.recovery?.key&&!extra.length){
    await restoreMissing(bundle,cp);cp.recovered_ids=[...new Set([...(cp.recovered_ids||[]),item.id])];delete cp.pending;
    await progress(bundle,'已核实错位图片已移除，恢复原稿插入位置',cp);return true;
  }
  if(!item||extra.length!==1||!extra[0].loaded)throw failure('UPLOAD_UNCERTAIN','待核验提交无法唯一对应，停止重复上传',await mediaDiagnostics(bundle,cp));
  const image=extra[0];await verifyImage(item,image,bundle);
  if(textBefore(contentRoot(),image.element)===expectedPrefixes(bundle)[item.id]&&withoutMarkers(bodyText())===norm(bundle.text)){
    cp.media[item.id]={key:image.key,src:image.src,sha256:item.sha256,marker:item.marker};delete cp.pending;
    await progress(bundle,'已核实上次提交的配图，不重复上传',cp);return true;
  }
  if(!missingSpans(bundle.text,withoutMarkers(bodyText())))throw failure('DRAFT_CONTENT_MISMATCH','配图提交后的正文差异无法安全恢复',{...textDiagnostics(bundle,cp),media:await mediaDiagnostics(bundle,cp)});
  cp.recoveries=cp.recoveries||[];cp.recoveries.push({id:item.id,key:image.key,backup_text:bodyText(),at:new Date().toISOString()});
  cp.pending.recovery={key:image.key,sha256:item.sha256,stage:'remove_verified'};
  await progress(bundle,'已核实错位文件，正在恢复该张配图的位置',cp);
  await removeAtomic(bundle,cp,image.key);
  await restoreMissing(bundle,cp);cp.recovered_ids=[...new Set([...(cp.recovered_ids||[]),item.id])];delete cp.pending;
  await progress(bundle,'中断时的配图位置已恢复，继续按原稿插图',cp);return true;
}
async function repairLegacy(bundle,cp){
  const oldIds=cp.repair?.source_ids||[...Object.keys(cp.media),...(cp.pending?.kind==='body'?[cp.pending.id]:[])];
  const sources=bundle.images.filter(i=>oldIds.includes(i.id));
  if(cp.repair?.complete)return false;
  if(!sources.length||(!cp.repair&&Object.values(cp.media).some(x=>x.key?.startsWith('block:'))))return false;
  const current=images(),matches=[];
  for(const image of current){
    if(!image.loaded||!image.block)throw failure('MEDIA_IDENTITY_MISMATCH','旧配图尚未加载，停止修复');
    const candidates=await Promise.all(sources.map(async item=>({item,score:await imageDifference(item,image.element)})));
    candidates.sort((a,b)=>a.score-b.score);
    if(candidates[0].score>6||candidates[1]&&candidates[1].score-candidates[0].score<0.15)throw failure('MEDIA_IDENTITY_MISMATCH','旧配图与源文件无法唯一对应，停止修复');
    if(matches.some(m=>m.item.id===candidates[0].item.id))throw failure('MEDIA_IDENTITY_MISMATCH','旧配图出现重复，停止修复');
    matches.push({image,...candidates[0]});
  }
  const missing=missingSpans(bundle.text,withoutMarkers(bodyText()));
  if(!missing)throw failure('DRAFT_CONTENT_MISMATCH','草稿存在无法自动恢复的文字差异，已停止修复',textDiagnostics(bundle,cp));
  if(!cp.repair){cp.repair={source_ids:oldIds,previous_media:structuredClone(cp.media),previous_pending:cp.pending,backup_text:bodyText(),started_at:new Date().toISOString(),removed:[]};await progress(bundle,'已保存旧上传快照，正在修复已确认错位的配图',cp);}
  for(const {image,item,score} of matches){
    cp.repair.pending_remove={id:item.id,key:image.key,score};await progress(bundle,'正在修复旧配图位置：'+item.id,cp);
    button(/^(Remove media|移除媒体|删除媒体)$/i,image.block).click();
    await pollValue(()=>!images().some(x=>x.key===image.key),'旧配图移除未完成，已保留修复进度',10000);
    cp.repair.removed.push(cp.repair.pending_remove);delete cp.repair.pending_remove;await progress(bundle,'已保存旧配图修复进度',cp);
  }
  await restoreMissing(bundle,cp);
  await waitText(bundle,cp);if(images().length)throw failure('MEDIA_IDENTITY_MISMATCH','旧配图修复尚未完成');
  cp.media={};delete cp.pending;cp.repair.complete=true;
  await progress(bundle,'旧上传错误已修复，正文核验通过，重新按原稿位置插图',cp);return true;
}
function imageAnchor(bundle,item,cp){
  if(bodyText().includes(item.marker))return markerRange(item.marker);
  if(!((cp.repair?.complete&&cp.repair.source_ids.includes(item.id))||cp.recovered_ids?.includes(item.id)||cp.anchor_removed_ids?.includes(item.id)))return markerRange(item.marker);
  const prefixes=expectedPrefixes(bundle),index=bundle.images.indexOf(item);
  const previous=bundle.images.slice(0,index).reverse().find(i=>cp.media[i.id]&&prefixes[i.id]===prefixes[item.id]);
  const previousBlock=previous?(()=>{const found=images().find(x=>x.key===cp.media[previous.id].key);return found?.block||found?.element;})():null;
  return cursorForPrefix(prefixes[item.id].length,previousBlock);
}
async function insertImage(bundle,item,cp,index,retry=false){
  if(cp.pending)throw failure('UPLOAD_UNCERTAIN','上次文件提交结果尚未核验，已保留草稿并停止重复上传',await mediaDiagnostics(bundle,cp));
  // Remove the textual anchor before submitting this image. Deleting the
  // marker beside a just-created native atomic block can also delete its media.
  if(bodyText().includes(item.marker)){
    cp.anchor_removed_ids=[...new Set([...(cp.anchor_removed_ids||[]),item.id])];
    await progress(bundle,'正在准备第 '+(index+1)+' 张配图的原稿位置',cp);
    await removeMarker(bundle,item,cp);await waitSaved();await auditOrRecover(bundle,cp);
  }
  const anchor=()=>{
    const range=imageAnchor(bundle,item,cp);
    range.collapse(true);return range;
  };
  await selectRange(anchor);
  const before=images();
  const stable=[...document.querySelectorAll('button[data-testid="addMediaButton"],button[data-testid="add-media"],button[aria-label="Add media"],button[aria-label="添加媒体内容"]')].filter(e=>visible(e)&&enabled(e));
  const insert=stable.length===1?stable[0]:button(/^(Insert|插入)$/i);
  insert.click();
  const media=await pollValue(()=>{const candidates=controls(/^(Media|媒体)$/i);return candidates.length===1?candidates[0]:null;},'未识别 X 原生插入媒体菜单',10000);
  media.click();
  const input=await pollValue(()=>{const all=[...document.querySelectorAll('[role="dialog"] input[type="file"]')];return all.length===1?all[0]:null;},'未识别正文图片文件选择控件',10000);
  cp.phase='body_upload';cp.pending={kind:'body',id:item.id,sha256:item.sha256,marker:item.marker,before:before.map(x=>x.key)};
  await progress(bundle,'正在上传正文图片 '+(index+1)+'/'+bundle.images.length,cp);
  const alerts=[];
  const readAlerts=()=>{for(const e of document.querySelectorAll('[role="alert"],[data-testid="toast"]')){const text=e.innerText?.trim();if(text&&!alerts.includes(text))alerts.push(text.slice(0,500));}};
  const observer=new MutationObserver(readAlerts);observer.observe(document.body,{childList:true,subtree:true});
  uploadInput(input,item);
  let added;
  try{added=await pollValue(()=>{readAlerts();
    if(nativeLimitAlert(alerts))throw failure('MEDIA_LIMIT','X 已提示单篇媒体数量超限，立即停止上传');
    const all=images().filter(x=>x.loaded&&!before.some(y=>y.key===x.key));
    if(all.length>1)throw failure('UNEXPECTED_MEDIA','出现多张未登记图片，停止续传');
    return all.length===1?all[0]:null;
  },'X 未显示已提交的正文图片',45000);}catch(error){
    cp.pending.alerts=alerts;
    const limitAlert=nativeLimitAlert(alerts);if(limitAlert)await recordNativeLimit(bundle,cp,before.length,limitAlert);
    await progress(bundle,'文件提交结果待核验，已保存 X 页面提示',cp);
    error.diagnostics={...await mediaDiagnostics(bundle,cp),upload_alerts:alerts};throw error;
  }finally{observer.disconnect();}
  await verifyImage(item,added,bundle);
  await sleep(500);
  const current=images().find(x=>x.key===added.key&&x.loaded);
  if(!current||textBefore(contentRoot(),current.element)!==expectedPrefixes(bundle)[item.id]){
    // Repair only this proved source file, once. Unknown submissions and
    // repeated failures stop, rather than duplicate files or loop indefinitely.
    if(retry||(cp.position_retries?.[item.id]||0)>=1)throw failure('IMAGE_POSITION_MISMATCH','X 再次插错该张配图，已保留草稿和核验记录',await mediaDiagnostics(bundle,cp));
    cp.position_retries=cp.position_retries||{};cp.position_retries[item.id]=1;
    await progress(bundle,'检测到第 '+(index+1)+' 张配图错位，核实源文件后修复一次',cp);
    await recoverPending(bundle,cp);
    return insertImage(bundle,item,cp,index,true);
  }
  await waitText(bundle,cp);
  cp.media[item.id]={src:added.src,key:added.key,sha256:item.sha256,marker:item.marker};delete cp.pending;
  // Persist file identity before the saved-state audit. A failed ACK stops here.
  await progress(bundle,'图片 '+(index+1)+' 已上传，正在保存位置',cp);
  await waitSaved();await auditOrRecover(bundle,cp);
  await progress(bundle,'已上传 '+(index+1)+'/'+bundle.images.length+' 张正文图片',cp);
}
async function waitSaved(){
  await sleep(1000);
  await pollValue(()=>{
    const labels=[...document.querySelectorAll('span,div,p')].filter(visible).map(e=>[...e.childNodes].filter(n=>n.nodeType===Node.TEXT_NODE).map(n=>n.textContent).join('').trim());
    return !labels.some(t=>/^(Saving(?:…|\.\.\.)?|正在保存|保存中)/i.test(t))&&labels.some(t=>/^Last saved\b|^上次保存|^已保存|^刚刚保存/i.test(t));
  },'X 尚未显示保存完成，暂停重新加载核验',25000);
}
function textBefore(root,node){const r=document.createRange();r.selectNodeContents(root);r.setEndBefore(node);return withoutMarkers(semanticText(r.cloneContents()));}
const prefixCache=new WeakMap();
function expectedPrefixes(bundle){
  if(prefixCache.has(bundle))return prefixCache.get(bundle);
  const root=document.createElement('div');root.innerHTML=bundle.html;const result={};
  for(const item of bundle.images){
    const walk=document.createTreeWalker(root,NodeFilter.SHOW_TEXT);let n,found;
    while(n=walk.nextNode()){if(n.textContent.includes(item.marker)){if(found)throw failure('IMAGE_ANCHOR_INVALID','文章包中的图片标记重复');found=n;}}
    if(!found)throw failure('IMAGE_ANCHOR_INVALID','文章包中的图片标记缺失');
    const r=document.createRange();r.selectNodeContents(root);r.setEnd(found,found.textContent.indexOf(item.marker));
    result[item.id]=withoutMarkers(semanticText(r.cloneContents()));
  }
  prefixCache.set(bundle,result);return result;
}
async function upload(bundle){
  await ensureAccount(bundle);const cp=structuredClone(bundle.job.checkpoint||{});cp.media=cp.media||{};
  // X renders an empty editable shell before hydrating a saved long article.
  // A previously recorded upload must be read only after its text has loaded.
  if(cp.pending||cp.cover||Object.keys(cp.media).length){
    try{await waitText(bundle,cp);}catch(e){if(!await restoreExtraPunctuation(bundle,cp)&&!await recoverPending(bundle,cp)&&!await repairLegacy(bundle,cp)){e.diagnostics={...e.diagnostics,media:await mediaDiagnostics(bundle,cp)};throw e;}}
  }
  await recoverRemovedBlocks(bundle,cp);
  if(cp.pending&&!await recoverPending(bundle,cp)&&!await repairLegacy(bundle,cp))throw failure('UPLOAD_UNCERTAIN','上次文件提交结果尚未核验，已保留草稿并停止重复上传',await mediaDiagnostics(bundle,cp));
  await recoverRecordedMissing(bundle,cp);
  const existing=images();
  if(existing.length&&!Object.keys(cp.media).length)throw failure('MEDIA_IDENTITY_MISMATCH','此草稿已有历史配图，需要核验位置后续传；不会重复上传或覆盖');
  cp.phase='text_check';await progress(bundle,'正在检查标题和正文；'+(Object.keys(cp.media).length?'继续核验已有上传进度':'封面与配图尚未开始上传'),cp);
  if(!withoutMarkers(bodyText())){if(titleValue()&&norm(titleValue())!==norm(bundle.title))throw failure('DRAFT_CONTENT_MISMATCH','原草稿标题不同，停止覆盖');setTitle(bundle.title);insertHTML(bundle.html);await sleep(500);}
  await waitText(bundle,cp);
  await progress(bundle,'标题与正文校验通过，开始上传封面和 '+bundle.images.length+' 张正文配图',cp);
  await uploadCover(bundle,cp);
  await auditRecorded(bundle,cp);
  for(let i=0;i<bundle.images.length;i++){
    const item=bundle.images[i],known=cp.media[item.id];
    if(known){
      const image=images().find(x=>x.loaded&&x.key===(known.key||mediaKey(known.src)));
      if(known.sha256!==item.sha256||!image)throw failure('MEDIA_IDENTITY_MISMATCH','已上传图片与任务记录不符，停止重复上传');
      await verifyImage(item,image,bundle);
      if(bodyText().includes(item.marker)){await removeMarker(bundle,item,cp);await waitSaved();await auditOrRecover(bundle,cp);await progress(bundle,'已恢复图片位置记录',cp);}
      continue;
    }
    await insertImage(bundle,item,cp,i);
  }
  await restoreParagraphs(bundle,cp);
  cp.phase='saved_check';await progress(bundle,'正在等待 X 保存完成，随后重新加载核验图片',cp);await waitSaved();return {checkpoint:cp,evidence:await check(bundle,cp)};
}
function paragraphPlan(bundle){
  const source=document.createElement('div');source.innerHTML=bundle.html;
  const expected=[...source.children].map(element=>({element,text:withoutMarkers(semanticText(element))})).filter(x=>x.text);
  const root=contentRoot(),native=[...root.querySelectorAll('[data-block="true"]')];
  const actual=(native.some(e=>e.getAttribute('contenteditable')!=='false')?native:[...root.children])
    .filter(e=>e.getAttribute('contenteditable')!=='false').map(element=>({element,text:withoutMarkers(semanticText(element))})).filter(x=>x.text);
  const repairs=[];let index=0;
  for(let i=0;i<expected.length;i++){
    const target=expected[i],parts=[];let text='';
    while(index<actual.length&&text.length<target.text.length){parts.push(actual[index++]);text+=parts.at(-1).text;}
    if(text!==target.text)throw failure('PARAGRAPH_AUDIT_FAILED','正文段落边界与源稿不一致，已停止发布确认',{phase:'paragraph_check',source_paragraphs:expected.length,draft_paragraphs:actual.length,index:i,expected:target.text.slice(0,120),observed:text.slice(0,120)});
    if(parts.length>1){
      const range=document.createRange();range.setStartBefore(parts[0].element);range.setEndAfter(parts.at(-1).element);
      if(range.cloneContents().querySelector('[data-block="true"][contenteditable="false"],img:not([src*="/emoji/"])')||parts.length>4)throw failure('PARAGRAPH_AUDIT_FAILED','分段差异涉及配图或复杂结构，已保留草稿供核对');
      repairs.push({source:target.element.outerHTML,parts});
    }
  }
  if(index!==actual.length)throw failure('PARAGRAPH_AUDIT_FAILED','草稿含额外正文段落，已停止发布确认');
  return {repairs,count:expected.length};
}
async function restoreParagraphs(bundle,cp){
  await waitText(bundle,cp);const plan=paragraphPlan(bundle);
  if(!plan.repairs.length)return;
  if(plan.repairs.length>8)throw failure('PARAGRAPH_AUDIT_FAILED','正文分段差异超过自动修复范围，已保留源稿');
  cp.paragraph_repairs=cp.paragraph_repairs||[];
  for(const repair of [...plan.repairs].reverse()){
    cp.paragraph_repairs.push({source_html:repair.source,backup_html:repair.parts.map(x=>x.element.outerHTML).join(''),at:new Date().toISOString()});
    await progress(bundle,'已保存分段差异，按源稿恢复该段正文格式',cp);
    const range=()=>{
      const left=document.createTreeWalker(repair.parts[0].element,NodeFilter.SHOW_TEXT),right=document.createTreeWalker(repair.parts.at(-1).element,NodeFilter.SHOW_TEXT);
      const first=left.nextNode();let last,n;while(n=right.nextNode())last=n;
      if(!first?.isConnected||!last?.isConnected)throw failure('SELECTION_NOT_READY','段落修复边界已变化，停止写入');
      const r=document.createRange();r.setStart(first,0);r.setEnd(last,last.textContent.length);return r;
    };
    await selectRange(range);const data=new DataTransfer();data.setData('text/html',repair.source);
    const raw=document.createElement('div');raw.innerHTML=repair.source;data.setData('text/plain',semanticText(raw));
    const event=new ClipboardEvent('paste',{clipboardData:data,bubbles:true,cancelable:true});editor().dispatchEvent(event);
    if(!event.defaultPrevented)throw failure('BODY_WRITE_FAILED','X 未接管段落格式恢复，已保留快照');
    await sleep(400);await waitText(bundle,cp);
  }
  await waitSaved();await auditRecorded(bundle,cp);
  if(paragraphPlan(bundle).repairs.length)throw failure('PARAGRAPH_AUDIT_FAILED','X 尚未保存恢复后的段落格式');
}
async function check(bundle,cp){
  await waitEditor(true);await waitText(bundle,cp);const observed=await ensureAccount(bundle),list=images(),prefixes=expectedPrefixes(bundle);
  if(cp.pending)throw failure('UPLOAD_UNCERTAIN','有未核验的图片提交，不能进入发布确认');
  if(list.length!==bundle.images.length||bundle.images.some((item,i)=>{
    const known=cp.media?.[item.id];return !known||known.sha256!==item.sha256||!list[i].loaded||list[i].key!==(known.key||mediaKey(known.src))||textBefore(editor(),list[i].element)!==prefixes[item.id];
  }))throw failure('MEDIA_AUDIT_FAILED','图片身份、顺序或正文位置核验未通过');
  const paragraphs=paragraphPlan(bundle);if(paragraphs.repairs.length)throw failure('PARAGRAPH_AUDIT_FAILED','保存后的正文出现额外断段，已停止发布确认');
  const media_checks=[];for(let i=0;i<list.length;i++)media_checks.push({id:bundle.images[i].id,...await verifyImage(bundle.images[i],list[i],bundle)});
  if(norm(bodyText())!==norm(bundle.text)||norm(titleValue())!==norm(bundle.title))throw failure('TEXT_AUDIT_FAILED','正文、标题或占位标记核验未通过');
  if(bundle.cover&&(!cp.cover||cp.cover.sha256!==bundle.cover.sha256||!coverImages().some(x=>x.loaded&&x.key===(cp.cover.key||mediaKey(cp.cover.src)))))throw failure('COVER_AUDIT_FAILED','封面保存核验未通过');
  return {account:observed,title:titleValue(),image_count:list.length,text_checked:true,order_checked:true,positions_checked:true,cover_checked:true,paragraphs_checked:true,paragraph_count:paragraphs.count,media_checks,observed_at:new Date().toISOString(),user_visual_review_required:true};
}
async function prepare(bundle){
  await ensureAccount(bundle);
  if(!editPath.test(location.pathname)){
    if(bundle.job.draft_url)throw failure('DRAFT_UNAVAILABLE','原草稿未打开，已停止上传并保留绑定');
    let create=await pollValue(()=>{
      const preferred=[...document.querySelectorAll('button[data-testid="create"],[role="button"][data-testid="create"],button[aria-label="create"]')].filter(e=>visible(e)&&enabled(e));
      if(preferred.length===1)return preferred[0];
      const candidates=controls(/^(Write|Create|New article|Create article|撰写|撰写文章|创建|新建|创建文章)$/i);
      return candidates.length===1?candidates[0]:null;
    },'X 新建文章控件未就绪或存在歧义',20000);
    create.click();await pollValue(()=>editPath.test(location.pathname),'X 没有进入新文章编辑页',20000);
  }
  const url=location.origin+location.pathname.replace(/\/(?:preview\/)?focus\/?$|\/preview\/?$/,'');
  // Bind immediately after navigation, BEFORE waiting for title/body selectors.
  await progress(bundle,'草稿已绑定，正在等待编辑器',bundle.job.checkpoint||{},{draft_url:url});
  await waitEditor(Boolean(bundle.job.draft_url));return {url,checkpoint:bundle.job.checkpoint};
}
async function publish(bundle){
  if(bundle.job.status!=='publish_requested'||bundle.job.publish_attempted)throw failure('PUBLISH_NOT_CONFIRMED','请先在工作台核对并确认公开发布');
  await ensureAccount(bundle);
  await check(bundle,bundle.job.checkpoint);
  button(/^(Publish|发布)$/i).click();
  // The workbench confirmation authorizes this exact article/account.
  // Complete the native confirmation once, never repeatedly submit.
  let finalClicked=false;
  const until=Date.now()+30000;
  while(Date.now()<until){
    const links=[...document.querySelectorAll('a[href]')].filter(visible).map(e=>e.href).filter(url=>new RegExp('^https://x.com/'+bundle.job.account+'/(status|article)/[0-9]+$').test(url));
    const current=location.href.split('?')[0];
    if(new RegExp('^https://x.com/'+bundle.job.account+'/(status|article)/[0-9]+$').test(current))return {url:current};
    // Do not infer success from unrelated timeline links.
    const dialogs=[...document.querySelectorAll('[role="dialog"]')].filter(visible);
    for(const dialog of dialogs){
      if(/published|已发布|发布成功/i.test(dialog.innerText)){const link=[...dialog.querySelectorAll('a[href]')].find(e=>links.includes(e.href));if(link)return {url:link.href};}
      if(!finalClicked){const candidates=controls(/^(Publish|发布)$/i,dialog);if(candidates.length===1){await ensureAccount(bundle);finalClicked=true;candidates[0].click();}}
    }
    await sleep(500);
  }
  throw Error('发布结果待核验：请检查 X 确认窗口或文章列表，不要重复发布');
}
chrome.runtime.onMessage.addListener((msg,sender,reply)=>{
  if(!['ping','account','prepare','upload','check','publish'].includes(msg.command))return;
  if(['prepare','upload','publish'].includes(msg.command)&&activeOperation){reply({error:'X 页面已有任务正在执行，请勿重复启动'});return;}
  const mutating=['prepare','upload','publish'].includes(msg.command);
  if(mutating)activeOperation=true;
  (async()=>{try{
    if(msg.command==='ping')return {ready:document.readyState!=='loading',url:location.href,version:'0.1.33',instance:documentInstance};
    if(msg.command==='account')return {account:await waitAccount()};
    if(msg.command==='prepare')return await prepare(msg.bundle);
    if(msg.command==='upload')return await upload(msg.bundle);
    if(msg.command==='check'){await waitEditor(true);return {evidence:await check(msg.bundle,msg.bundle.job.checkpoint)};}
    return await publish(msg.bundle);
  }catch(e){return {error:e.message,code:e.code,diagnostics:e.diagnostics};}finally{if(mutating)activeOperation=false;}})().then(reply);return true;
});
