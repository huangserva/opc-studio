// Full reading keeps the current workbench mounted, so returning preserves its UI.
const initialReaderReturn=reader?null:history.state?.studioReturn;
let readerReturnSession=null;
let readingEditableNodes=[];
function readingReturnURL(slug,version){
  const params=new URLSearchParams({article:slug});
  if(version)params.set('version',version);
  return '/?'+params;
}
function validReadingReturn(saved){
  if(!saved||typeof saved.url!=='string')return false;
  try{
    const u=new URL(saved.url,location.origin);
    return u.origin===location.origin&&u.pathname==='/'&&u.searchParams.get('article')===saved.slug;
  }catch{return false;}
}
function captureReadingOrigin(){
  const canvas=$('#canvas'),top=canvas.getBoundingClientRect().top;
  const block=state.view==='read'?[...document.querySelectorAll('#paper .article-body > section, #paper .article-body > figure')].find(el=>el.getBoundingClientRect().bottom>top):null;
  return {url:readingReturnURL(state.slug,state.article.version.id),slug:state.slug,version:state.article.version.id,
    scrollTop:canvas.scrollTop,anchor:block?(block.id?'#'+block.id:`#paper [data-image="${block.dataset.image}"]`):null,
    anchorOffset:block?block.getBoundingClientRect().top-top:0,view:state.view,
    before:$('#compare-before')?.value,after:$('#compare-after')?.value,platform:state.platform,
    collection:state.collection,collectionQuery:state.collectionQuery,typeFilter:state.typeFilter,
    historyOpen:document.body.classList.contains('history-open'),libraryOpen:document.body.classList.contains('library-open'),
    assistantOpen:document.body.classList.contains('assistant-open')};
}
function setReaderBackLink(){
  if(!reader)return;
  const saved=history.state?.studioReaderReturn;
  const slug=decodeURIComponent(location.pathname.split('/')[2]||'');
  $('.reader-back').href=validReadingReturn(saved)?saved.url:readingReturnURL(slug,new URLSearchParams(location.search).get('version'));
}
function applyReadingLayout(){
  reader=true;document.body.classList.add('reader');
  window.StudioPanels?.readingChanged();
  readingEditableNodes=[...document.querySelectorAll('#paper [contenteditable=true]')];
  readingEditableNodes.forEach(el=>el.contentEditable='false');
  $('#paper').hidden=false;$('#comparison').hidden=true;$('#publishing').hidden=true;
  document.querySelectorAll('#paper video').forEach(v=>v.pause());
  setReaderBackLink();window.scrollTo(0,0);
  $('.reader-back').focus({preventScroll:true});
}
function restoreReadingPosition(saved){
  requestAnimationFrame(()=>{
    const canvas=$('#canvas');canvas.scrollTo({top:Number(saved.scrollTop)||0,behavior:'instant'});
    if(saved.view==='read'&&typeof saved.anchor==='string'&&/^#(?:block-\d+|paper \[data-image="\d+"\])$/.test(saved.anchor)){
      const block=$(saved.anchor);
      if(block)canvas.scrollBy({top:block.getBoundingClientRect().top-canvas.getBoundingClientRect().top-(Number(saved.anchorOffset)||0),behavior:'instant'});
    }
    $('#full-read').focus({preventScroll:true});
  });
}
function enterFullReading(){
  if(state.dirty){toast('请先保存修改，再打开完整阅读。');return;}
  if(reader||!state.article)return;
  const saved=captureReadingOrigin();readerReturnSession=saved;
  history.replaceState({...history.state,studioReturn:saved},'',saved.url);
  history.pushState({studioReaderReturn:saved},'',`/read/${encodeURIComponent(state.slug)}?version=${encodeURIComponent(saved.version)}`);
  applyReadingLayout();
}
function leaveReadingLayout(saved){
  reader=false;document.body.classList.remove('reader');window.scrollTo(0,0);
  window.StudioPanels?.readingChanged();
  readingEditableNodes.forEach(el=>el.contentEditable='true');readingEditableNodes=[];
  $('#paper').hidden=state.view!=='read';$('#comparison').hidden=state.view!=='diff';$('#publishing').hidden=state.view!=='publish';
  // Existing DOM, sidebar selection, chat input, and comparison selections stay intact.
  restoreReadingPosition(saved);
}
async function restoreReaderNavigation(){
  if(reader){setReaderBackLink();return;}
  const saved=initialReaderReturn;
  if(!validReadingReturn(saved)||saved.slug!==state.slug)return;
  state.collection=saved.collection||'all';state.collectionQuery=saved.collectionQuery||'';state.typeFilter=saved.typeFilter||'all';
  state.platform=saved.platform;
  state.view=['read','diff','publish'].includes(saved.view)?saved.view:'read';
  renderView({loadComparison:false});
  if(state.view==='diff')await showComparison(saved.before,saved.after);
  if(saved.assistantOpen)await openAssistant();
  document.body.classList.toggle('history-open',!!saved.historyOpen);
  document.body.classList.toggle('library-open',!!saved.libraryOpen);
  restoreReadingPosition(saved);
}
$('.reader-back').addEventListener('click',event=>{
  if(event.button!==0||event.metaKey||event.ctrlKey||event.shiftKey||event.altKey)return;
  if(validReadingReturn(history.state?.studioReaderReturn)){
    event.preventDefault();history.back();
  }
});
window.addEventListener('popstate',()=>{
  const nextReader=location.pathname.startsWith('/read/');
  if(readerReturnSession&&state.slug===readerReturnSession.slug){
    if(nextReader)applyReadingLayout();
    else leaveReadingLayout(readerReturnSession);
  }else if(nextReader!==reader){
    // After reloading a reading page, the previous entry must rebuild the workbench.
    location.reload();
  }
});
setReaderBackLink();
