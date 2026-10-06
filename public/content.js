const PLATFORM_NAMES={x:'X',xiaohongshu:'小红书',douyin:'抖音',channels:'视频号',wechat:'微信公众号'};
function assetURL(asset){return asset?.url||asset?.preview_url||'';}
function videoMarkup(video,poster){return video?`<video class="content-video" controls playsinline preload="metadata" src="${escapeHTML(assetURL(video))}" ${poster?`poster="${escapeHTML(assetURL(poster))}"`:''}></video><small class="media-filename">${escapeHTML(video.name||'视频成片')} · ${(video.bytes/1024/1024).toFixed(1)} MB</small>`:'<div class="video-empty"><span>▷</span><strong>把成片放到这里</strong><p>支持 MP4、WebM、MOV，最大 1 GB<br>建议使用 H.264 / AAC 编码的 MP4</p></div>';}
function renderContentExtras(){
  const box=$('#content-extras');if(!box||!state.studio)return;
  const s=state.studio,isVideo=s.type==='video',editable=state.edit;
  box.innerHTML=`${isVideo?`<section class="video-section">${videoMarkup(s.video,s.poster)}${editable?'<div class="media-actions"><button class="button primary" data-media="video">选择 / 替换视频</button><button class="button" data-media="poster">选择 / 替换封面</button></div>':''}<p id="media-progress" role="status"></p>${s.poster?`<details><summary>封面预览</summary><img class="video-poster" src="${escapeHTML(assetURL(s.poster))}" alt="视频封面"></details>`:''}</section>`:''}${isVideo?`<section class="script-section"><h2>脚本与字幕</h2><label>视频脚本<textarea id="video-script" ${editable?'':'readonly'} placeholder="脚本、镜头说明或修改要求">${escapeHTML(s.script)}</textarea></label><label>字幕（SRT / WebVTT）<textarea id="video-subtitles" ${editable?'':'readonly'} placeholder="粘贴字幕文件内容，导出时保存为独立字幕文件">${escapeHTML(s.subtitles)}</textarea></label></section>`:''}${s.type==='post'?'<div class="type-hint">每个正文块代表一条推文，图片属于其前面的那一条。新建单条或继续添加，按顺序预览。</div>':''}${s.type==='gallery'?'<div class="type-hint">图片按下方顺序展示；编辑时可调整顺序、添加图注。</div>':''}`;
  box.querySelectorAll('[data-media]').forEach(b=>b.onclick=()=>chooseMedia(b.dataset.media));
  for(const [id,key] of [['video-script','script'],['video-subtitles','subtitles']]){const el=$('#'+id);if(el&&editable)el.oninput=()=>{state.studio[key]=el.value;markDirty();};}
  box.querySelectorAll('video').forEach(v=>v.onerror=()=>{const line=box.querySelector('.media-filename');if(line)line.textContent+=' · 当前浏览器无法播放此编码，请换用 H.264 MP4';});
}
function addBlockControls(){
  const body=$('.article-body');if(!body||!state.edit)return;
  body.querySelectorAll('.block-actions').forEach(b=>b.remove());
  const blocks=[...body.children];
  for(const [index,block] of blocks.entries()){
    const row=document.createElement('div');row.className='block-actions';
    row.innerHTML=`<span>${block.classList.contains('text-block')?(state.studio.type==='post'?'推文':'正文'):'图片'} ${index+1}</span><button ${index===0?'disabled':''} data-action="up" aria-label="上移内容">↑</button><button ${index===blocks.length-1?'disabled':''} data-action="down" aria-label="下移内容">↓</button><button data-action="delete">移除</button>`;
    block.before(row);
    row.querySelectorAll('button').forEach(button=>button.onclick=()=>{
      const action=button.dataset.action;
      if(action==='delete')block.remove();
      if(action==='up')body.insertBefore(block,blocks[index-1]);
      if(action==='down')blocks[index+1].after(block);
      markDirty();addBlockControls();
    });
  }
}
function chooseMedia(kind,platform=null){
  if(state.mediaBusy){toast('当前文件还在上传');return;}
  state.mediaDestination={kind,platform};
  $('#content-media-file').accept=kind==='video'?'.mp4,.webm,.mov':'.png,.jpg,.jpeg,.webp';
  $('#content-media-file').click();
}
function uploadMedia(file,slug){
  return new Promise((resolve,reject)=>{
    const xhr=new XMLHttpRequest();xhr.open('POST','/api/upload/'+encodeURIComponent(slug)+'?name='+encodeURIComponent(file.name));
    xhr.setRequestHeader('Content-Type','application/octet-stream');xhr.setRequestHeader('X-Article-Studio','1');
    xhr.upload.onprogress=e=>{const p=$('#media-progress')||$('#platform-progress');if(p)p.textContent=e.lengthComputable?`正在保存到本机 ${Math.round(e.loaded/e.total*100)}%`:'正在保存到本机…';};
    xhr.onerror=()=>reject(new Error('上传失败，请检查工作台服务后重试'));
    xhr.onload=()=>{try{const result=JSON.parse(xhr.responseText);if(xhr.status>=400)throw new Error(result.error);resolve(result);}catch(e){reject(e);}};
    xhr.send(file);
  });
}
$('#content-media-file').onchange=async()=>{
  const file=$('#content-media-file').files[0],dest=state.mediaDestination,slug=state.slug,version=state.article.version.id;
  if(!file)return;
  const maximum=dest.kind==='video'?1024**3:8*1024**2;
  if(file.size>maximum){toast(dest.kind==='video'?'视频最大 1 GB':'封面最大 8 MB');$('#content-media-file').value='';return;}
  state.mediaBusy=true;state.edit=true;markDirty();
  try{
    const result=await uploadMedia(file,slug);
    if(state.slug!==slug||state.article.version.id!==version)throw new Error('已切换作品，素材未写入当前页面，请回到原作品重新选择');
    if(dest.platform){const target=getPlatformDraft(dest.platform);target[dest.kind]=result;state.studio.platforms[dest.platform]=target;renderPublishing();}
    else{state.studio[dest.kind]=result;renderContentExtras();}
    toast('素材已接收，点击「保存新版本」保留这次修改');
  }catch(e){toast(e.message);}finally{state.mediaBusy=false;$('#content-media-file').value='';editControls();}
};
function getPlatformDraft(platform){
  return structuredClone(state.studio.platforms[platform]||{title:state.article.title,caption:platform==='wechat'?'':state.article.blocks.filter(b=>b.kind==='text').map(b=>b.text).join('\n\n'),topics:'',account:'',status:'draft',video:null,poster:null,subtitles:''});
}
function platformReadOnly(){return state.article.version.id!==state.article.head;}
function renderPublishing(){
  const platform=state.platform||'x';state.platform=platform;
  const target=getPlatformDraft(platform),historical=platformReadOnly(),disabled=historical?'disabled':'';
  const s=state.studio,video=target.video||s.video,poster=target.poster||s.poster;
  $('#publishing').innerHTML=`<div class="publish-heading"><span class="eyebrow">PREPARE & EXPORT</span><h1>一个作品，准备多个平台版本</h1><p>保存平台文案与素材；X 长文章可通过本机 Chrome 上传草稿，核对后确认发布。</p></div><div class="platform-tabs">${Object.entries(PLATFORM_NAMES).map(([id,name])=>`<button class="${id===platform?'active':''}" data-platform="${id}">${name}</button>`).join('')}</div><div class="publish-layout"><section class="platform-form"><div class="section-heading"><h2>${PLATFORM_NAMES[platform]} · ${versionName(state.article.version.id)}</h2><span class="content-kind">${historical?'历史快照':'发布准备'}</span></div>${historical?'<p class="notice">历史版本只读；导出仍使用此版本。</p>':''}<label>发布账号<input id="target-account" value="${escapeHTML(target.account)}" placeholder="填写账号名称，便于区分" ${disabled}></label><label>平台标题<input id="target-title" value="${escapeHTML(target.title)}" ${disabled}></label><label>${platform==='wechat'?'公众号摘要（正文沿用作品图文顺序）':'发布文案'}<textarea id="target-caption" rows="7" ${disabled}>${escapeHTML(target.caption)}</textarea></label><label>话题<input id="target-topics" value="${escapeHTML(target.topics)}" placeholder="#话题1 #话题2" ${disabled}></label><label>准备状态<select id="target-status" ${disabled}><option value="draft" ${target.status==='draft'?'selected':''}>准备中</option><option value="ready" ${target.status==='ready'?'selected':''}>待发布</option></select></label>${s.type==='video'?`<label>本平台字幕（留空沿用作品字幕）<textarea id="target-subtitles" rows="3" ${disabled}>${escapeHTML(target.subtitles)}</textarea></label>`:''}<p class="muted">修改后点击顶部「保存新版本」。平台文案和所选素材会一起留档。</p><button class="button" id="check-publication">检查发布方式</button><div id="publication-plan" class="muted" role="status"></div>${platform==='wechat'?'<button class="button" id="wechat-preview">公众号排版预览</button><p class="muted">导出含排版 HTML、配图与封面。公众号图片需上传核验；在线发布尚未接通。</p>':''}<button class="button primary" id="export-package">导出 ${PLATFORM_NAMES[platform]} 发布包</button><div id="export-result"></div></section><section class="platform-assets"><h2>发布素材</h2>${s.type==='video'?videoMarkup(video,poster):`<p>${state.article.image_count} 张图片 · ${TYPE_NAMES[s.type]}</p><p class="muted">导出包会包含正文、图片及其顺序。</p>`}${s.type==='video'&&!historical?'<div class="media-actions"><button class="button" id="variant-video">本平台专用视频</button><button class="button" id="variant-poster">本平台专用封面</button><button class="button subtle" id="variant-reset">沿用作品素材</button></div>':''}${poster?`<img class="video-poster" src="${escapeHTML(assetURL(poster))}" alt="发布封面">`:''}<p id="platform-progress" role="status"></p><p class="muted">${target.video||target.poster?'已选择本平台专用素材':'沿用作品当前版本的素材'}</p><h2>发布记录</h2><p class="muted">发布后粘贴链接，记录本次使用的版本。记录为手动登记，不代表平台自动验证。</p><label>已发布链接<input id="published-url" type="url" placeholder="https://…"></label><button class="button" id="record-published">登记当前版本已发布</button><div id="delivery-history">${deliveryMarkup(platform)}</div></section></div>`;
  if($('#wechat-preview'))$('#wechat-preview').onclick=async()=>{if(state.dirty){toast('请先保存新版本，再预览公众号排版');return;}let dialog=$('#wechat-preview-dialog');if(!dialog){dialog=document.createElement('dialog');dialog.id='wechat-preview-dialog';dialog.style.cssText='width:min(760px,94vw);max-width:760px;';dialog.innerHTML='<h2>公众号排版预览</h2><p class="muted">按当前保存版本排版；公众号图片需上传核验。</p><iframe title="微信公众号排版" sandbox="" style="width:100%;height:60vh;border:1px solid #e7eaf0;background:white;"></iframe><div class="dialog-actions"><button class="button">关闭预览</button></div>';dialog.querySelector('button').onclick=()=>dialog.close();document.body.append(dialog);}try{const response=await fetch('/api/wechat-preview/'+encodeURIComponent(state.slug)+'?version='+encodeURIComponent(state.article.version.id));if(!response.ok){const error=await response.json();throw new Error(error.error||'排版预览失败');}dialog.querySelector('iframe').srcdoc=await response.text();dialog.showModal();}catch(error){toast(error.message);}};
  $('#publishing').querySelectorAll('[data-platform]').forEach(b=>b.onclick=()=>{state.platform=b.dataset.platform;renderPublishing();});
  const fields=['account','title','caption','topics','status','subtitles'];
  function change(){const value=getPlatformDraft(platform);for(const key of fields){const input=$('#target-'+key);if(input)value[key]=input.value;}state.studio.platforms[platform]=value;state.edit=true;markDirty();}
  for(const key of fields){const input=$('#target-'+key);if(input)input.oninput=change;}
  if($('#variant-video'))$('#variant-video').onclick=()=>chooseMedia('video',platform);
  if($('#variant-poster'))$('#variant-poster').onclick=()=>chooseMedia('poster',platform);
  if($('#variant-reset'))$('#variant-reset').onclick=()=>{const value=getPlatformDraft(platform);value.video=null;value.poster=null;state.studio.platforms[platform]=value;state.edit=true;markDirty();renderPublishing();};
  $('#check-publication').onclick=()=>checkPublication().catch(e=>toast(e.message));
  $('#export-package').onclick=()=>exportPackage().catch(e=>toast(e.message));
  $('#record-published').onclick=()=>recordPublished().catch(e=>toast(e.message));
  if(platform==='x' && s.type==='article')mountLocalPublishing();
}
function deliveryMarkup(platform){
  const records=state.article.deliveries.filter(r=>r.platform===platform).slice().reverse();
  return records.length?records.map(r=>`<div class="delivery-entry"><strong>${r.kind==='published_manual'?'已发布 · 手动登记':r.kind==='published_browser'?'已发布 · 本机浏览器':'已导出 · 尚未确认发布'}</strong><small>${versionName(r.version)} · ${date(r.created_at)} ${escapeHTML(r.account||'')}</small>${r.url?`<a href="${escapeHTML(r.url)}" target="_blank" rel="noopener noreferrer">查看发布链接 ↗</a>`:''}</div>`).join(''):'<p class="muted">还没有发布记录</p>';
}
async function exportPackage(){
  if(state.dirty){toast('请先保存新版本，再导出对应素材');return;}
  $('#export-package').disabled=true;
  try{const result=await api('/api/export/'+state.slug,{version:state.article.version.id,platform:state.platform});$('#export-result').innerHTML=`<a class="download-link" href="${escapeHTML(result.url)}" download>下载发布包 ZIP ↓</a><small>正文、素材和平台文案均来自 ${versionName(result.version)}</small>`;await refreshDeliveries();toast('发布包已生成，点击下载即可');}finally{$('#export-package').disabled=false;}
}
async function refreshDeliveries(){const detail=await api('/api/article/'+state.slug+'?version='+state.article.version.id);state.article.deliveries=detail.deliveries;$('#delivery-history').innerHTML=deliveryMarkup(state.platform);}
async function recordPublished(){
  if(state.dirty){toast('请先保存当前修改，再登记发布版本');return;}
  const url=$('#published-url').value.trim();if(!url){toast('请填写发布后的作品链接');return;}
  $('#record-published').disabled=true;
  try{await api('/api/record/'+state.slug,{version:state.article.version.id,platform:state.platform,url});$('#published-url').value='';await refreshDeliveries();toast('发布链接与版本已登记');}finally{$('#record-published').disabled=false;}
}
function studioDiffMarkup(diff){
  if(!diff.studio_changed)return '';
  const a=diff.studio_before,b=diff.studio_after;
  const panel=s=>`<p>${TYPE_NAMES[s.type]}</p>${s.video?videoMarkup(s.video,s.poster):'<p class="muted">无视频</p>'}${s.poster?`<img class="video-poster" src="${escapeHTML(assetURL(s.poster))}" alt="版本封面">`:''}<h3>脚本</h3><pre>${escapeHTML(s.script||'无')}</pre><h3>字幕</h3><pre>${escapeHTML(s.subtitles||'无')}</pre>${Object.entries(s.platforms).map(([p,t])=>`<h3>${PLATFORM_NAMES[p]}</h3><pre>${escapeHTML([t.title,t.caption,t.topics,t.account,t.status==='ready'?'待发布':'准备中',t.subtitles].filter(Boolean).join('\n'))}</pre>${t.video?videoMarkup(t.video,t.poster):''}${t.poster?`<img src="${escapeHTML(assetURL(t.poster))}" alt="平台版本封面">`:''}`).join('')}`;
  return `<section class="change"><div class="change-caption">视频、脚本、字幕或平台文案变化</div><div class="diff-columns"><div class="removed">${panel(a)}</div><div class="added">${panel(b)}</div></div></section>`;
}
$('#view-publish').onclick=()=>{state.view='publish';renderView();};

loadLibrary({initial:true}).then(restoreReaderNavigation).catch(e=>{$('#loading').textContent='暂时无法打开工作台：'+e.message;});
setInterval(()=>{if(!reader)loadLibrary().catch(()=>{});},8000);

async function checkPublication(){
  if(state.dirty){toast('请先保存新版本，再检查发布方式');return;}
  const button=$('#check-publication');button.disabled=true;
  try{
    const plan=await api('/api/publication-plan/'+state.slug,{version:state.article.version.id,platform:state.platform});
    $('#publication-plan').innerHTML='<p><strong>'+escapeHTML(plan.label)+'</strong></p>'+(plan.blocking||[]).map(x=>'<p>'+escapeHTML(x)+'</p>').join('')+'<p>检查完成，尚未上传或发布。</p>';
  }finally{button.disabled=false;}
}
