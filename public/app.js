const $ = (selector) => document.querySelector(selector);
const escapeHTML = (text) => String(text ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const state = {library: [], article: null, slug: null, edit: false, dirty: false, view: 'read', inbox: '', changed: new Set(), uploads: new Map(), anchor: null, handoffArticle: false, page:'article', collections:[], collection:'all', collectionQuery:'', editingCollection:null, organizeAfterCreate:false};
const TYPE_NAMES={article:'长文章',post:'短推文 / 串文',gallery:'图集',video:'视频'};
let reader = location.pathname.startsWith('/read/');
if (reader) document.body.classList.add('reader');
document.querySelectorAll('.toolbar button').forEach(button=>button.disabled=true);

async function api(path, data) {
  const response = await fetch(path, data ? {method:'POST',headers:{'Content-Type':'application/json','X-Article-Studio':'1'},body:JSON.stringify(data)} : {});
  const value = await response.json();
  if (!response.ok) throw new Error(value.error || '操作没有完成，请重试');
  return value;
}
function toast(message) {
  $('#toast').textContent = message;
  $('#toast').classList.add('visible');
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => $('#toast').classList.remove('visible'), 3800);
}
const dateFormatter = new Intl.DateTimeFormat('zh-CN', {timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit',hourCycle:'h23'});
const date = value => {
  const parsed=new Date(value);
  if(!value || Number.isNaN(parsed.getTime()))return '时间未知';
  const parts=Object.fromEntries(dateFormatter.formatToParts(parsed).map(p=>[p.type,p.value]));
  return `${parts.year}-${parts.month}-${parts.day} ${parts.hour}:${parts.minute}:${parts.second}`;
};
const versionName = value => 'V' + Number(value.replace('v',''));
function wordCount(n) { return n.toLocaleString('zh-CN') + ' 字'; }
function imageMarkup(item, index) {
  return `<figure class="article-image" data-image="${index}"><img src="${escapeHTML(item.url)}" alt="${item.kind === 'table' ? '数据表格' : '文章配图'}" loading="lazy"><figcaption>${item.kind === 'table' ? '数据表格' : '配图'} · 点击查看大图</figcaption></figure>`;
}
function zoom(url) { $('#large-image').src=url; $('#lightbox').showModal(); }

async function loadLibrary({initial=false}={}) {
  const result = await api('/api/library');
  state.library = result.articles;
  state.collections=result.collections||[]; renderCollectionNav();
  state.inbox = result.inbox;
  if (!$('#intake-dialog').open) $('#inbox-path').textContent = result.inbox;
  $('#article-count').textContent = result.articles.length;
  $('#library').innerHTML = result.articles.map(a => `<button class="article-card ${a.slug===state.slug?'selected':''}" data-slug="${escapeHTML(a.slug)}"><span class="card-icon">▤</span><div><strong>${escapeHTML(a.title)}</strong><small>${TYPE_NAMES[a.content_type||'article']} · ${a.images} 张图片 <span>·</span> ${a.version_count} 个版本</small><small class="recent-modified">修改于 ${date(a.updated_at)}</small></div></button>`).join('');
  const errors = $('#import-errors');
  errors.hidden = !result.import_errors.length;
  errors.textContent = result.import_errors.map(e => `${e.article}：${e.error}`).join('；');
  if(initial && !reader && !new URLSearchParams(location.search).has('article')) {
    openCollection(new URLSearchParams(location.search).get('collection')||'all',{reveal:false});
  } else if (initial && result.articles.length) {
    const requested = reader ? decodeURIComponent(location.pathname.split('/')[2]) : new URLSearchParams(location.search).get('article');
    const chosen = result.articles.find(a => a.slug === requested) || result.articles.find(a=>a.slug==='v100-qwen') || result.articles[0];
    await openArticle(chosen.slug, new URLSearchParams(location.search).get('version') || history.state?.studioReturn?.version,{reveal:false});
  } else if (initial) {
    $('#loading').innerHTML = '这里还没有文章。<br><small>点击「新建作品」开始写作，也可以接收已有的图文文件。</small>';
  } else if(state.page==='collections'){
    renderCollectionCards();
  } else if (state.article) {
    const current = result.articles.find(a => a.slug === state.slug);
    if (current && current.head !== state.article.head) {
      $('#version-notice').hidden=false;
      $('#version-notice').innerHTML='检测到新版本。<button id="load-latest">查看最新图文</button>';
      $('#load-latest').onclick=()=>requestOpen(state.slug);
    }
  }
}

function requestOpen(slug, revision) {
  if (state.dirty && !confirm('您有尚未保存的修改，确定放弃并切换吗？')) return;
  openArticle(slug,revision).catch(e=>toast(e.message));
}
async function openArticle(slug, revision, {reveal=true}={}) {
  const article = await api('/api/article/'+encodeURIComponent(slug)+(revision?'?version='+encodeURIComponent(revision):''));
  state.page='article'; document.body.classList.remove('browsing-collections'); $('#collection-page').hidden=true; $('.toolbar').hidden=false;
  state.studio=structuredClone(article.studio); state.mediaBusy=false;
  state.slug=slug; state.article=article; state.edit=false; state.dirty=false; state.changed.clear(); state.uploads.clear(); state.anchor=null; state.view='read';
  document.querySelectorAll('.toolbar button').forEach(button=>button.disabled=false);
  document.body.classList.remove('library-open');
  if (!reader) history.replaceState(null,'','/?article='+encodeURIComponent(slug));
  document.title=article.title+' · 内容工作台';
  $('#version-notice').hidden=article.version.id===article.head;
  $('#version-notice').textContent='您正在查看历史版本 '+versionName(article.version.id)+'，原稿保持不变。';
  $('#loading').hidden=true; $('#paper').hidden=false;
  $('#crumb').textContent=article.title;
  $('#gallery').textContent='图片 '+article.image_count;
  document.querySelectorAll('[data-slug]').forEach(el=>el.classList.toggle('selected',el.dataset.slug===slug));
  $('#publishing').hidden=true; renderPaper(); renderHistory(); editControls(); renderView();
  $('#canvas').scrollTop=0;
  if(reveal)window.StudioPanels?.contentRequested();
}

function renderPaper() {
  const a=state.article;
  const meta=`<div class="paper-meta"><span class="version-tag">${versionName(a.version.id)}</span><span>${date(a.version.created_at)}</span><span>${wordCount(a.characters)}</span><span>${a.image_count} 张图片</span></div>`;
  $('#paper').innerHTML=`${meta}<span class="content-kind">${TYPE_NAMES[a.studio.type]}</span><h1 id="article-title">${escapeHTML(a.title)}</h1>${a.cover?`<figure class="cover"><img src="${escapeHTML(a.cover.url)}" alt="文章封面"></figure>`:''}<div id="content-extras"></div><div class="article-body">${a.blocks.map(b=>b.kind==='text'?`<section class="text-block" data-index="${b.index}" id="block-${b.index}">${b.html}</section>`:imageMarkup(b,b.index)).join('')}</div><footer class="article-end"><span></span>全文完<span></span></footer>`;
  $('#paper').querySelectorAll('figure img').forEach(img=>img.onclick=()=>{ if(!state.edit) zoom(img.src); });
  renderContentExtras();
  $('#paper').dataset.type=a.studio.type;
  const headings=[];
  a.blocks.forEach(b=>{
    if(b.kind!=='text')return;
    const node=$(`#block-${b.index}`);
    const heading=node.querySelector('h1,h2,h3');
    if(heading) headings.push({index:b.index,text:heading.textContent});
  });
  $('#outline').innerHTML=headings.length?headings.map((h,i)=>`<button data-scroll="${h.index}" title="${escapeHTML(h.text)}"><span>${String(i+1).padStart(2,'0')}</span>${escapeHTML(h.text)}</button>`).join(''):'<p class="muted">文章中的小标题会出现在这里</p>';
}

function renderHistory() {
  const a=state.article;
  $('#version-list').innerHTML=a.versions.map((v,i)=>`<button class="version-entry ${v.id===a.version.id?'selected':''}" data-version="${v.id}"><div class="version-line"><strong>${versionName(v.id)}</strong>${v.id===a.head?'<span class="latest-tag">最新</span>':''}<time datetime="${escapeHTML(v.created_at)}" title="北京时间 · 版本保存时间">${date(v.created_at)}</time></div><p>${escapeHTML(v.summary)}</p><small>${escapeHTML(v.source)}</small></button>`).join('');
  $('#restore').hidden=a.version.id===a.head;
}

function editControls() {
  $('#edit').hidden=state.edit || !state.article || state.article.version.id!==state.article.head;
  $('#writing-tools').hidden=!state.edit || state.view!=='read';
  $('#save').disabled=!!state.mediaBusy;
  $('#add-paragraph').textContent=state.studio?.type==='post'?'＋ 下一条推文':'＋ 段落';
  $('#cancel-edit').hidden=!state.edit; $('#save').hidden=!state.edit;
  $('#save-state').textContent=state.dirty?'有未保存的修改':state.edit?'编辑中':'已保存到本机';
  $('#save-state').classList.toggle('unsaved',state.dirty);
  $('#paper').classList.toggle('editing',state.edit);
}
function markDirty() { state.dirty=true; editControls(); }
function enableBlock(block) {
  block.contentEditable='true';
  block.setAttribute('role','textbox');
  block.setAttribute('aria-label','编辑正文段落');
  block.setAttribute('data-placeholder','写下正文，也可以粘贴已经写好的内容…');
  block.onfocus=()=>{state.anchor=block;};
  block.oninput=()=>{state.changed.add(block.dataset.index);markDirty();};
}
function appendText(heading=false, dirty=true) {
  const block=document.createElement('section');
  block.className='text-block'; block.dataset.new='true';
  block.innerHTML=heading?'<h2><br></h2>':'<p><br></p>';
  insertBlock(block); enableBlock(block); block.focus();
  if(dirty)markDirty();
  addBlockControls();
}
function insertBlock(block) {
  if(state.anchor?.isConnected && state.anchor.parentElement?.classList.contains('article-body')) state.anchor.after(block);
  else $('.article-body').append(block);
  state.anchor=block;
}
function beginEdit() {
  if(!state.article || state.article.version.id!==state.article.head)return;
  state.view='read'; renderView(); state.edit=true; editControls();
  $('#article-title').contentEditable='true';
  $('#article-title').oninput=markDirty;
  $('#paper').querySelectorAll('.text-block').forEach(enableBlock);
  $('#paper').querySelectorAll('.article-image').forEach(block=>block.onclick=()=>{state.anchor=block;});
  if(!state.article.blocks.length && state.studio.type!=='gallery' && state.studio.type!=='video')appendText(false,false);
  renderContentExtras(); addBlockControls();
}
async function addImages(files) {
  if([...files].some(f=>f.size>8*1024*1024))throw new Error('每张图片最大 8 MB，请先缩小图片');
  const total=[...state.uploads.values()].reduce((n,d)=>n+d.length,0)+[...files].reduce((n,f)=>n+f.size*4/3,0);
  if(total>28_000_000)throw new Error('这次新增图片较多，请先保存一版再继续添加');
  for(const file of files){
    const data=await new Promise((resolve,reject)=>{const r=new FileReader();r.onload=()=>resolve(r.result);r.onerror=()=>reject(new Error('图片读取失败'));r.readAsDataURL(file);});
    const key=crypto.randomUUID(); state.uploads.set(key,data.split(',')[1]);
    const block=document.createElement('figure'); block.className='article-image';block.dataset.upload=key;
    const img=document.createElement('img');img.src=data;img.alt=file.name;
    block.append(img);block.onclick=()=>{state.anchor=block;};insertBlock(block);markDirty();
  }
  if(state.studio.type!=='gallery')appendText(false,false);
  addBlockControls();
}
async function saveEdit() {
  const blocks=[...$('.article-body').children].filter(b=>!b.classList.contains('block-actions')).map(block=>{
    if(block.dataset.upload)return {kind:'image',data:state.uploads.get(block.dataset.upload)};
    if(block.classList.contains('text-block')){
      if(block.dataset.index!==undefined && !state.changed.has(block.dataset.index))return {index:Number(block.dataset.index)};
      return {kind:'text',html:block.innerHTML};
    }
    return {index:Number(block.dataset.image)};
  });
  const data={base_version:state.article.version.id,title:$('#article-title').innerText.trim(),blocks,studio:state.studio,summary:$('#summary').value};
  $('#confirm-save').disabled=true;
  try{
    const result=await api('/api/save/'+state.slug,data);
    $('#save-dialog').close();
    await openArticle(state.slug,result.version); await loadLibrary();
    toast(result.unchanged?'内容没有变化，无需新增版本':'已保存 '+versionName(result.version)+'，旧版本已保留');
  } finally { $('#confirm-save').disabled=false; }
}
function newArticleDialog(){
  if(state.dirty && !confirm('当前修改还未保存，确定放弃并新建吗？'))return;
  $('#new-title').value='';$('#new-dialog').showModal();$('#new-title').focus();
}
async function createArticle(){
  $('#create-article').disabled=true;
  try{
    const article=await api('/api/create',{title:$('#new-title').value,type:document.querySelector('[name="new-type"]:checked').value,collection:state.page==='collections'&&!['all','unfiled'].includes(state.collection)?state.collection:null});
    $('#new-dialog').close(); await openArticle(article.slug);await loadLibrary();beginEdit();
    toast('作品已创建，可以开始创作');
  }finally{$('#create-article').disabled=false;}
}
function openIntake(specific=false){
  if(specific && state.dirty){toast('请先保存当前修改，再交给 Claude 续写');return;}
  state.handoffArticle=specific;
  $('#inbox-path').textContent=specific?state.article.intake_path:state.inbox;
  $('#intake-dialog h2').textContent=specific?'让 Claude 继续写这件作品':'让写作工具把图文放到这里';
  $('#intake-dialog pre').textContent=specific?`当前版本：${versionName(state.article.version.id)}
标题：${state.article.title}

content.json 或原有 article.json
assets/（图片、视频）

复制交接要求后发给 Claude 即可。`:'inbox/\n└── my-article/\n    ├── article.json（也支持 article.md）\n    └── assets/';
  document.body.classList.remove('library-open');$('#intake-dialog').showModal();
}

function renderView({loadComparison=true}={}) {
  if(!state.article)return;
  $('#view-read').classList.toggle('active',state.view==='read');
  $('#view-diff').classList.toggle('active',state.view==='diff');
  $('#view-publish').classList.toggle('active',state.view==='publish');
  $('#publishing').hidden=state.view!=='publish';
  if(state.view==='publish')renderPublishing();
  editControls();
  $('#paper').hidden=state.view!=='read'; $('#comparison').hidden=state.view!=='diff';
  if(state.view==='diff'&&loadComparison)showComparison().catch(e=>toast(e.message));
}
async function showComparison(before,after) {
  const a=state.article;
  if(a.versions.length<2){$('#comparison').innerHTML='<div class="empty compare-empty"><span>◷</span><h2>第一个版本，完整保留</h2><p>编辑后保存，或让 Claude 投递新版文件，<br>就能在这里看到文字和图片的变化。</p><button class="button" id="empty-edit">编辑当前文章</button></div>';$('#empty-edit').onclick=beginEdit;return;}
  after=after||a.version.id;
  const position=a.versions.findIndex(v=>v.id===after);
  before=before||a.versions[Math.min(position+1,a.versions.length-1)].id;
  if(before===after)before=a.versions.find(v=>v.id!==after).id;
  const diff=await api(`/api/compare/${state.slug}?before=${before}&after=${after}`);
  const options=(selected)=>a.versions.map(v=>`<option value="${v.id}" ${selected===v.id?'selected':''}>${versionName(v.id)} · ${escapeHTML(v.summary)}</option>`).join('');
  const blockHTML=(b)=>b.kind==='text'?b.html:`<figure><img src="${escapeHTML(b.url)}" alt="版本图片"></figure>`;
  let changes=studioDiffMarkup(diff);
  if(diff.title_changed)changes+=`<section class="change"><div class="change-caption">标题修改</div><div class="diff-columns"><div class="removed"><h2>${escapeHTML(diff.title_before)}</h2></div><div class="added"><h2>${escapeHTML(diff.title_after)}</h2></div></div></section>`;
  if(diff.cover_changed)changes+=`<section class="change"><div class="change-caption">封面修改</div><div class="diff-columns"><div class="removed">${diff.cover_before?blockHTML(diff.cover_before):'无封面'}</div><div class="added">${diff.cover_after?blockHTML(diff.cover_after):'无封面'}</div></div></section>`;
  for(const [i,c] of diff.changes.entries()){
    const label=c.type==='insert'?'新增内容':c.type==='delete'?'删除内容':c.format_changed?'格式修改':'修改内容';
    const inline=(side)=>c[`rich_inline_${side}`]!=null?`<div class="rich-inline-diff">${c[`rich_inline_${side}`]}</div>`:c[`inline_${side}`]??(c[side].map(blockHTML).join('')||'<span class="empty-side">无内容</span>');
    changes+=`<section class="change"><div class="change-caption"><span>${String(i+1).padStart(2,'0')}</span>${label}</div><div class="diff-columns"><div class="removed">${inline('before')}</div><div class="added">${inline('after')}</div></div></section>`;
  }
  $('#comparison').innerHTML=`<div class="compare-heading"><span class="eyebrow">VERSION COMPARISON</span><h2>每一次修改，都看得见</h2><p>${diff.changes.length+(diff.title_changed?1:0)+(diff.cover_changed?1:0)+(diff.studio_changed?1:0)} 处变化 · 包括正文、排版和图片</p></div><div class="compare-selectors"><label>修改前<select id="compare-before">${options(before)}</select></label><span>与</span><label>修改后<select id="compare-after">${options(after)}</select></label></div>${changes||'<div class="empty">这两个版本的正文、图片与排版一致。</div>'}`;
  $('#compare-before').onchange=()=>showComparison($('#compare-before').value,$('#compare-after').value).catch(e=>toast(e.message));
  $('#compare-after').onchange=$('#compare-before').onchange;
  $('#comparison').querySelectorAll('img').forEach(img=>img.onclick=()=>zoom(img.src));
}

function handoffPrompt() {
  const specific=state.handoffArticle, a=state.article;
  const destination=specific?a.intake_path:state.inbox+'/<英文作品名>/';
  const current=specific?`当前作品：${a.title}；类型：${TYPE_NAMES[a.studio.type]}。先读取快照 ${a.package_path}/manifest.json，以及同目录 studio.json（存在时），并读取引用的图片、视频。以此版本继续创作，不要用旧投递源稿覆盖后续修改。\n`:'';
  return `${current}把确认好的内容交付到本机目录：\n${destination}\n\n使用 content.json 或原有 article.json（同一目录只能保留一种源文件），媒体放 assets/。schema_version:1，title，blocks 为正文与图片的有序数组。正文 type 可为 paragraph、heading、image、quote、list、code、table。图片用 src 相对路径，可附 caption。\n\n另有 studio 对象：type 为 article、post、gallery、video；script 是脚本；subtitles 是 SRT/WebVTT 文本；video 和 poster 使用 {"src":"assets/文件名"}。platforms 可包含 x、xiaohongshu、douyin、channels、wechat；每个平台可填 title、caption、topics、account、status（draft 或 ready）、subtitles，以及专用 video/poster。post 的每个正文块为一条推文，图片归其前一条；gallery 按 blocks 顺序排列图片与图注。视频允许 blocks:[]，无需先写文章。\n\n先保存全部素材，最后原子替换 JSON。所有 src 必须在作品目录内。从快照续写时，把引用的 images/、media/ 素材复制到 assets/ 并改成 src，不能直接保留旧版本的 file/url 字段。未要求修改的内容和平台版本应保留。\n\n不要检查 Claude 配置或操作其他应用；此步骤只交付本地文件，不上传或公开发布。\n\n最小视频示例：\n${JSON.stringify({schema_version:1,title:'视频标题',blocks:[],studio:{type:'video',video:{src:'assets/final.mp4'},script:'脚本',subtitles:'',platforms:{douyin:{title:'抖音标题',caption:'发布文案',topics:'#话题',status:'draft'}}}},null,2)}`;
}

async function copy(value) {
  try{await navigator.clipboard.writeText(value);toast('已复制');}
  catch{toast('复制未获浏览器允许，请在弹窗中选中文字复制。');}
}

document.addEventListener('click',event=>{
  const collection=event.target.closest('[data-collection]');if(collection)openCollection(collection.dataset.collection);
  const card=event.target.closest('[data-slug]');if(card)requestOpen(card.dataset.slug);
  const version=event.target.closest('[data-version]');if(version)requestOpen(state.slug,version.dataset.version);
  const heading=event.target.closest('[data-scroll]');if(heading){state.view='read';renderView();$('#block-'+heading.dataset.scroll)?.scrollIntoView({behavior:'smooth',block:'start'});}
  const close=event.target.closest('[data-close]');if(close)close.closest('dialog').close();
});
$('#view-read').onclick=()=>{state.view='read';renderView();};
$('#view-diff').onclick=()=>{if(state.edit){toast('请先保存或取消编辑，再比较版本。');return;}state.view='diff';renderView();};
$('#history-toggle').onclick=()=>document.body.classList.toggle('history-open');
$('#library-toggle').onclick=()=>document.body.classList.toggle('library-open');
$('#close-history').onclick=()=>document.body.classList.remove('history-open');
$('#edit').onclick=beginEdit;
$('#cancel-edit').onclick=()=>requestOpen(state.slug,state.article.version.id);
$('#save').onclick=()=>{if(!state.dirty){toast('还没有修改内容');return;}$('#summary').value='';$('#save-dialog').showModal();$('#summary').focus();};
$('#confirm-save').onclick=()=>saveEdit().catch(e=>toast(e.message));
$('#open-intake').onclick=()=>openIntake(false);
$('#handoff').onclick=()=>openIntake(true);
$('#new-article').onclick=newArticleDialog;
$('#new-article-top').onclick=newArticleDialog;
$('#create-article').onclick=()=>createArticle().catch(e=>toast(e.message));
$('#new-title').onkeydown=e=>{if(e.key==='Enter' && !e.isComposing)$('#create-article').click();};
$('#add-paragraph').onclick=()=>appendText();
$('#add-heading').onclick=()=>appendText(true);
$('#add-image').onclick=()=>$('#image-file').click();
$('#image-file').onchange=async()=>{try{await addImages($('#image-file').files);}catch(e){toast(e.message);}finally{$('#image-file').value='';}};
$('#copy-path').onclick=()=>copy($('#inbox-path').textContent);
$('#copy-prompt').onclick=()=>copy(handoffPrompt());
$('#scan-now').onclick=async()=>{try{await api('/api/scan',{});await loadLibrary({initial:!state.article});toast('已检查投递目录');}catch(e){toast(e.message);}};
$('#full-read').onclick=()=>enterFullReading();
$('#reader-print').onclick=()=>window.print();
$('#restore').onclick=async()=>{
  if(!confirm('将此历史版本另存为最新版？现有版本会全部保留。'))return;
  try{const result=await api('/api/restore/'+state.slug,{base_version:state.article.head,version:state.article.version.id});await openArticle(state.slug,result.version);await loadLibrary();toast('已恢复为新版本');}catch(e){toast(e.message);}
};
$('#gallery').onclick=()=>{
  const a=state.article, images=[...(a.cover?[{...a.cover,label:'封面'}]:[]),...a.blocks.filter(b=>b.kind!=='text').map((b,i)=>({...b,label:(b.kind==='table'?'表格':'配图')+' '+(i+1)}))];
  $('#gallery-title').textContent=`全部图片 · ${images.length} 张`;
  $('#gallery-grid').innerHTML=images.map((m,i)=>`<button data-gallery="${i}"><img src="${escapeHTML(m.url)}" alt="${m.label}" loading="lazy"><span>${m.label}</span></button>`).join('');
  $('#gallery-grid').querySelectorAll('button').forEach(b=>b.onclick=()=>zoom(images[Number(b.dataset.gallery)].url));
  $('#gallery-dialog').showModal();
};
function collectionArticles(){
  const group=state.collections.find(c=>c.id===state.collection);
  const assigned=new Set(state.collections.flatMap(c=>c.articles));
  return state.library.filter(a=>(!state.typeFilter||state.typeFilter==='all'||a.content_type===state.typeFilter) && (state.collection==='all'||(state.collection==='unfiled'?!assigned.has(a.slug):group?.articles.includes(a.slug))) &&
    (a.title+' '+a.excerpt+' '+date(a.updated_at)).toLowerCase().includes(state.collectionQuery.toLowerCase()));
}
function renderCollectionNav(){
  const assigned=new Set(state.collections.flatMap(c=>c.articles));
  const entries=[{id:'all',name:'全部作品',count:state.library.length},{id:'unfiled',name:'未归入合集',count:state.library.filter(a=>!assigned.has(a.slug)).length},...state.collections.map(c=>({...c,count:state.library.filter(a=>c.articles.includes(a.slug)).length}))];
  $('#collections-nav').innerHTML=entries.map(c=>`<button class="collection-nav-item ${state.page==='collections'&&state.collection===c.id?'selected':''}" data-collection="${c.id}"><span>▦</span><b>${escapeHTML(c.name)}</b><small>${c.count}</small></button>`).join('');
}
function openCollection(id='all',{reveal=true}={}){
  if(state.dirty&&!confirm('您有尚未保存的修改，确定放弃并打开合集吗？'))return;
  if(!['all','unfiled'].includes(id)&&!state.collections.some(c=>c.id===id))id='all';
  state.page='collections';state.collection=id;state.collectionQuery='';state.typeFilter='all';state.dirty=false;state.edit=false;
  document.body.classList.add('browsing-collections');document.body.classList.remove('library-open','history-open');
  $('.toolbar').hidden=true;$('#writing-tools').hidden=true;$('#paper').hidden=true;$('#comparison').hidden=true;$('#publishing').hidden=true;$('#loading').hidden=true;$('#version-notice').hidden=true;$('#collection-page').hidden=false;
  history.replaceState(null,'','/?collection='+encodeURIComponent(id));
  const group=state.collections.find(c=>c.id===id), name=group?.name||(id==='all'?'全部作品':'未归入合集');
  $('#crumb').textContent=name;document.title=name+' · 内容合集';
  $('#collection-page').innerHTML=`<div class="collection-heading"><span class="eyebrow">MY COLLECTIONS</span><h1>${escapeHTML(name)}</h1><p>过去做过的作品，都在这里。打开一篇，查看完整内容与每次修改。</p><div class="collection-actions"><button class="button primary" id="write-in-collection">＋ 新建作品</button><button class="button" id="create-collection-page">＋ 新建合集</button>${group?'<button class="button" id="rename-collection">重命名合集</button>':''}</div></div>${id==='all'?'<div id="collection-tiles"></div>':''}<div class="content-type-tabs">${[['all','全部'],...Object.entries(TYPE_NAMES)].map(([id,label])=>`<button data-type-filter="${id}" class="${id==='all'?'selected':''}">${label}</button>`).join('')}</div><div class="collection-filter"><label class="search-field"><span>⌕</span><input id="article-search" type="search" placeholder="搜索标题、摘要或日期" aria-label="搜索历史作品"></label><span id="collection-result-count"></span></div><p class="collection-sort-note">按最后修改时间从新到旧排列 · 北京时间</p><div id="collection-grid"></div>`;
  $('#write-in-collection').onclick=newArticleDialog;$('#create-collection-page').onclick=()=>collectionDialog();
  if(group)$('#rename-collection').onclick=()=>collectionDialog(group);
  $('#article-search').oninput=e=>{state.collectionQuery=e.target.value;renderCollectionCards();};
  document.querySelectorAll('[data-type-filter]').forEach(b=>b.onclick=()=>{state.typeFilter=b.dataset.typeFilter;document.querySelectorAll('[data-type-filter]').forEach(x=>x.classList.toggle('selected',x===b));renderCollectionCards();});
  renderCollectionNav();renderCollectionCards();$('#canvas').scrollTop=0;
  if(reveal)window.StudioPanels?.contentRequested();
}
function renderCollectionCards(){
  if(state.page!=='collections')return;
  const tiles=$('#collection-tiles');
  if(tiles)tiles.innerHTML=state.collections.map(c=>`<button class="collection-tile" data-collection="${c.id}"><span>▦</span><strong>${escapeHTML(c.name)}</strong><small>${state.library.filter(a=>c.articles.includes(a.slug)).length} 件作品 →</small></button>`).join('');
  const articles=collectionArticles();$('#collection-result-count').textContent=articles.length+' 件作品';
  $('#collection-grid').innerHTML=articles.length?articles.map(a=>`<button class="history-card" data-slug="${escapeHTML(a.slug)}">${a.cover?`<img class="history-cover" src="${escapeHTML(a.cover)}" alt="图文封面" loading="lazy">`:`<div class="history-cover no-cover">${a.content_type==='video'?'▷':a.content_type==='post'?'𝕏':'▤'}<span>${TYPE_NAMES[a.content_type||'article']}</span></div>`}<div class="history-card-body"><span class="content-kind">${TYPE_NAMES[a.content_type||'article']}${a.has_video?' · ▶':''}</span><h2>${escapeHTML(a.title)}</h2><p>${escapeHTML(a.excerpt||'从一个想法开始，继续写下这件作品。')}</p><div class="collection-tags">${Object.entries(a.platforms||{}).map(([p,status])=>`<span>${({x:'X',douyin:'抖音',xiaohongshu:'小红书',channels:'视频号',wechat:'微信公众号'})[p]} · ${status==='ready'?'待发布':'准备中'}</span>`).join('')}${state.collections.filter(c=>c.articles.includes(a.slug)).map(c=>`<span>${escapeHTML(c.name)}</span>`).join('')}</div><footer><span>${a.has_video?'含视频 · ':''}${a.images} 张图 · ${a.version_count} 个版本</span><time class="card-modified" datetime="${escapeHTML(a.updated_at)}" title="北京时间 · 最后修改"><span>最后修改</span><b>${date(a.updated_at)}</b></time></footer></div></button>`).join(''):`<div class="empty collection-empty"><h2>${state.collectionQuery?'没有找到匹配的图文':'这个合集还没有图文'}</h2><p>${state.collectionQuery?'试试其他关键词。':'点击「新建作品」，或打开已有文章，点击「加入合集」。'}</p></div>`;
}
function collectionDialog(record=null, after=false){
  state.organizeAfterCreate=after;
  state.editingCollection=record?.id||null;$('#collection-name').value=record?.name||'';
  $('#collection-dialog-title').textContent=record?'重命名合集':'建立内容合集';$('#save-collection').textContent=record?'保存名称':'建立合集';
  $('#collection-dialog').showModal();$('#collection-name').focus();
}
async function saveCollection(){
  $('#save-collection').disabled=true;
  try{
    const c=await api('/api/collection',{id:state.editingCollection,name:$('#collection-name').value});
    $('#collection-dialog').close();await loadLibrary();
    if(state.organizeAfterCreate){state.organizeAfterCreate=false;organizeArticle();}
    else openCollection(c.id);
    toast(state.editingCollection?'合集名称已保存':'合集已建立');
  }finally{$('#save-collection').disabled=false;}
}
function organizeArticle(){
  if(!state.article)return;
  $('#organize-title').textContent=state.article.title;
  $('#organize-list').innerHTML=state.collections.length?state.collections.map(c=>`<label class="membership-option"><input type="checkbox" value="${c.id}" ${c.articles.includes(state.slug)?'checked':''}><span>${escapeHTML(c.name)}</span><small>${c.articles.length} 篇</small></label>`).join(''):'<p class="empty">还没有合集，先建立一个吧。</p>';
  $('#organize-dialog').showModal();
}
async function saveMembership(){
  $('#save-membership').disabled=true;
  try{
    for(const input of $('#organize-list').querySelectorAll('input')){
      const group=state.collections.find(c=>c.id===input.value), was=group.articles.includes(state.slug);
      if(was!==input.checked)await api('/api/collection',{id:input.value,[input.checked?'add':'remove']:[state.slug]});
    }
    $('#organize-dialog').close();await loadLibrary();toast('合集归属已保存');
  }finally{$('#save-membership').disabled=false;}
}
$('#collection-home').onclick=()=>openCollection('all');
$('#new-collection').onclick=()=>{if(state.dirty){toast('请先保存当前修改，再建立合集');return;}collectionDialog();};
$('#save-collection').onclick=()=>saveCollection().catch(e=>toast(e.message));
$('#collection-name').onkeydown=e=>{if(e.key==='Enter'&&!e.isComposing)$('#save-collection').click();};
$('#organize').onclick=organizeArticle;
$('#organize-new').onclick=()=>{$('#organize-dialog').close();collectionDialog(null,true);};
$('#save-membership').onclick=()=>saveMembership().catch(e=>toast(e.message));
window.addEventListener('beforeunload',event=>{if(state.dirty){event.preventDefault();event.returnValue='';}});
