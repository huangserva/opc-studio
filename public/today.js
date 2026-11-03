import {createListReader} from './components/list-reader.js';
import {grokNode as node,renderPostDetail,metricStrip,metricNumber,formatMetric} from './components/item-card.js';
const $ = id => document.getElementById(id);
const initialParams=new URLSearchParams(location.search);
const filteredPanel=$('filtered-panel');
let searchTimer;
let selectedPlatforms=[...new Set(initialParams.getAll('platform').flatMap(v=>v.toLowerCase().split(',')))], collectionChannel=(initialParams.get('collection_channel') || '').toLowerCase(), registryReady=false;
let channel = 'hot', range = 'today', page = 1, loadSequence = 0, authorSequence = 0, refreshTimer;
async function get(url) {const response=await fetch(url,{cache:'no-store'}); const body=await response.json(); if(!response.ok) throw new Error(body.error || '读取失败');return body;}
// 切栏保留最近 5 秒的列表；后台预取复用同一请求，避免快速往返反复读同一批文件。
// 筛选、搜索、自动刷新始终重新取数；页面重新获焦时清缓存。
const tabCache=new Map(), pendingLists=new Map();let didPrefetch=false;
async function getList(params, cached=false) {
  const day=params.get('range')==='today'?new Intl.DateTimeFormat('en-CA',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date()):'';
  const key=params.toString()+'|'+day, prior=tabCache.get(key);
  if(cached && prior && Date.now()-prior.at<5000)return prior.result;
  if(pendingLists.has(key))return pendingLists.get(key);
  const request=new URLSearchParams(params);if(request.get('channel')==='interest'){request.set('view','summary');request.set('audit','0');}
  const pending=get('/api/today?'+request).then(result=>{tabCache.set(key,{at:Date.now(),result});if(tabCache.size>24)tabCache.delete(tabCache.keys().next().value);return result;}).finally(()=>pendingLists.delete(key));
  pendingLists.set(key,pending);return pending;
}
function prefetchTabs(params) {
  const run=()=>{for(const target of ['hot','interest','official']){const next=new URLSearchParams(params);next.set('page','1');next.set('tier','');next.set('topic','');if(target==='hot')next.delete('channel');else next.set('channel',target);getList(next,true).catch(()=>{});}};
  if(window.requestIdleCallback)window.requestIdleCallback(run,{timeout:300});else setTimeout(run,30);
}
const app = createListReader({
  renderDetail:(item,options)=>{
    const panel=node('div','today-detail');
    const back=node('button','digest-back','← 回到今天该做的');back.type='button';back.onclick=()=>showDigest(true);
    panel.append(back,renderPostDetail(item,{...options,showTitle:true}));return panel;
  },
  draftFromToday:true, list:$('item-list'), reader:$('reader'), layout:$('layout'), divider:$('divider'),
  loadDetail:id=>{digestMode=false;const fromTodo=todoOpeningId===id;todoOpeningId=null;try{sessionStorage.setItem('today.selection',JSON.stringify({id,query:location.search,fromTodo}));}catch(_){}const target=currentItems.some(item=>item.id===id)?channel:'hot';return get('/api/today/item?id='+encodeURIComponent(id)+(fromTodo?'&today_todo=1':target==='interest'?'&channel=interest':target==='official'?'&channel=official':''));},
  onAction:async(id,action,item)=>{const response=await fetch('/api/today/feedback',{method:'POST',headers:{'Content-Type':'application/json','X-Article-Studio':'1'},body:JSON.stringify({id,action,...(item.channel==='interest'?{channel:'interest'}:{})})}); const body=await response.json();if(!response.ok) throw new Error(body.error || '保存失败');return body;},
  onAuthor:async author=>{const serial=++authorSequence, selected=$('reader').querySelector('.item-detail')?.dataset.itemId;try {const url='/api/today?author='+encodeURIComponent(author.handle || author.name)+(channel==='interest'?'&channel=interest&range=7d&view=summary&audit=0':channel==='official'?'&channel=official':'');const result=await get(url);for(let p=2;result.items.length<result.total;p++){const next=await get(url+'&page='+p);if(!next.items.length)break;result.items.push(...next.items);}if(serial===authorSequence && $('reader').querySelector('.item-detail')?.dataset.itemId===selected)app.showAuthor(result.items,author);}catch(error){if(serial===authorSequence)$('reader').append(node('p','error',error.message));}}
});
// Only today's DOM is rearranged; the shared renderer and other pages stay unchanged.
let todoOpeningId=null;
let digestMode=true, digestResult=null, currentItems=[], restoreSelection=true;

const dayLabel=value=>value?new Date(value).toLocaleDateString('sv-SE',{timeZone:'Asia/Shanghai'}):new Date().toLocaleDateString('sv-SE',{timeZone:'Asia/Shanghai'});
function scores(item,cls='today-scores'){
  const row=node('span',cls),parts=[];
  if(metricNumber(item.fit_score)!==null)parts.push('像 '+formatMetric(item.fit_score));
  if(metricNumber(item.doable)!==null)parts.push('做 '+formatMetric(item.doable));
  row.textContent=parts.join(' · ');return row;
}
function lineIcon(key){
  const paths={today:'M4 5h16v15H4z M8 3v4 M16 3v4 M4 11h16',library:'M4 4h5v16H4z M12 4h7v16h-7 M12 8h7',works:'M5 3h10l4 4v14H5z M14 3v5h5 M8 12h8 M8 16h6',publish:'M12 16V3 M7 8l5-5 5 5 M4 14v7h16v-7',deals:'M3 7h18v14H3z M8 7V3h8v4 M3 12h18 M10 12v3h4v-3',schedules:'M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18 M12 7v6l4 2',settings:'M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8 M12 2v3 M12 19v3 M2 12h3 M19 12h3 M5 5l2 2 M17 17l2 2 M5 19l2-2 M17 7l2-2',image:'M3 4h18v16H3z M3 16l6-6 5 5 3-3 4 4 M16 8h.1',video:'M3 5h12v14H3z M15 10l6-4v12l-6-4',x:'M4 4l16 16 M20 4L4 20',twitter:'M4 4l16 16 M20 4L4 20',wechat:'M15 11a6 6 0 0 1-7 5l-4 2 1-4a6 6 0 1 1 10-3 M15 9a5 5 0 1 1 4 9l-2 2-1-3 M7 9h.1 M11 9h.1',douyin:'M14 3v12a4 4 0 1 1-4-4 M14 3c0 4 4 6 7 6',tiktok:'M14 3v12a4 4 0 1 1-4-4 M14 3c0 4 4 6 7 6',github:'M8 20c-5 1-5-3-7-3 M9 22v-4c-5-1-6-7-3-10L5 3l5 2h4l5-2-1 5c3 3 2 9-3 10v4',xhs:'M7 3L3 9h6 M9 6l-6 9h7 M3 20l7-2 M12 6h9 M16.5 6v14 M11 20h11',linuxdo:'M4 3h16v18H4z M9 7v10h7',web:'M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20 M2 12h20 M12 2c-5 5-5 15 0 20 M12 2c5 5 5 15 0 20',wechat_mp:'M15 11a6 6 0 0 1-7 5l-4 2 1-4a6 6 0 1 1 10-3 M15 9a5 5 0 1 1 4 9l-2 2-1-3'};
  const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');svg.classList.add('today-line-icon');svg.setAttribute('viewBox','0 0 24 24');svg.setAttribute('aria-hidden','true');
  const path=document.createElementNS(svg.namespaceURI,'path');path.setAttribute('d',paths[key]||paths.web);svg.append(path);return svg;
}
for(const link of document.querySelectorAll('#workspace-nav [data-nav-item]')){
  const label=link.textContent.trim().replace(/^\S+\s*/, '');link.replaceChildren(lineIcon(link.dataset.navItem),node('span','nav-label',label));
}
function todayCard(item){
  const card=app.card(item),meta=card.querySelector('.item-card-meta'),title=card.querySelector('.item-summary'),foot=card.querySelector('.item-card-foot');
  card.classList.add('today-card');
  if(channel==='interest' && item.first_seen_basis==='since_repair_no_history'){const note=node('span','first-seen-note','首次时间从修复时点起算');note.title='没有可恢复的历史观察证据；从 '+(item.first_seen_at||'本次修复')+' 起算';meta.append(note);}
  const head=node('div','today-card-heading'),tier=meta.querySelector('.item-tier');if(tier)head.append(tier);head.append(title);card.prepend(head);
  const score=scores(item);if(score.textContent)card.prepend(score);
  const source=meta.querySelector('.source-badges');if(source){source.querySelector('img')?.replaceWith(lineIcon(item.platform_info?.key||'web'));source.querySelector('.collection-channels')?.remove();}
  if(foot){const direction=foot.querySelector('.interest-matches')|| (item.topic?node('span','today-topic',item.topic):null);if(direction)meta.append(direction);foot.remove();}
  // Metadata comes after the metrics. Resources/actions retain their original handlers.
  const metrics=card.querySelector('.item-card-metrics');(metrics||head).after(meta);
  return card;
}
const todoCollapsedKey='today.todo.collapsed';
let todoOpen=true;
try{todoOpen=localStorage.getItem(todoCollapsedKey)!=='true';}catch(_){}
function setTodoOpen(open,{remember=false}={}){
  todoOpen=Boolean(open);
  $('today-todo').classList.toggle('open',todoOpen);
  $('today-todo').classList.toggle('is-collapsed',!todoOpen);
  $('today-todo-content').hidden=!todoOpen;
  const label=(todoOpen?'收起':'展开')+'今天该做的';
  for(const id of ['today-todo-close','today-todo-toggle']){
    $(id).setAttribute('aria-expanded',String(todoOpen));
    $(id).setAttribute('aria-label',label);$(id).title=label;
  }
  if(remember)try{localStorage.setItem(todoCollapsedKey,String(!todoOpen));}catch(_){}
}
setTodoOpen(todoOpen);
// Keep the reader's document-level Enter shortcut from swallowing these native buttons.
for(const id of ['today-todo-close','today-todo-toggle'])$(id).addEventListener('keydown',event=>{
  if(event.key==='Enter'||event.key===' ')event.stopPropagation();
});
const todoBatchKey='today.todo.batch';
let todoBatch={date:null,index:0};
try{
  const saved=JSON.parse(localStorage.getItem(todoBatchKey));
  if(saved&&typeof saved.date==='string'&&Number.isSafeInteger(saved.index)&&saved.index>=0)todoBatch=saved;
}catch(_){}
function saveTodoBatch(){try{localStorage.setItem(todoBatchKey,JSON.stringify(todoBatch));}catch(_){} }
function todoBatches(todo){
  const first=Array.isArray(todo?.items)?todo.items:[];
  if(!first.length)return [];
  const seen=new Set(first.map(item=>item.id)),extras=[];
  for(const item of Array.isArray(todo.batch_candidates)?todo.batch_candidates:[])if(item&&typeof item.id==='string'&&!seen.has(item.id)){seen.add(item.id);extras.push(item);}
  const batches=[first];
  for(let i=0;i<extras.length;i+=3)batches.push(extras.slice(i,i+3));
  return batches;
}
$('today-todo-next').onclick=()=>{
  const batches=todoBatches(digestResult);
  if(batches.length<2)return;
  todoBatch.index=(todoBatch.index+1)%batches.length;saveTodoBatch();
  $('today-todo-content').scrollTop=0;showDigest();
};
$('today-todo-next').addEventListener('keydown',event=>{if(event.key==='Enter'||event.key===' ')event.stopPropagation();});
function showDigest(reset=false){
  if(reset){app.setItems(currentItems);for(const card of $('item-list').querySelectorAll('.selected'))card.classList.remove('selected');try{sessionStorage.removeItem('today.selection');}catch(_){}digestMode=true;$('today-todo-content').scrollTop=0;if(matchMedia('(max-width:1199px)').matches)setTodoOpen(true);}
  if(digestMode){const empty=node('div','reader-empty');empty.append(node('span','','先看今天值得做的'),node('p','','从左侧帖子或右侧「今天该做的」打开全文'));$('reader').replaceChildren(empty);}
  const panel=node('div','today-digest');
  const todo=digestResult||{status:'pending',items:[],reason:'正在读取今天的 Grok 日报…'};
  const date=todo.date||dayLabel(),batches=todoBatches(todo);
  if(todoBatch.date!==date){todoBatch={date,index:0};saveTodoBatch();}
  if(batches.length&&todoBatch.index>=batches.length){todoBatch.index=0;saveTodoBatch();}
  const index=batches.length?todoBatch.index:0;
  const next=$('today-todo-next');next.disabled=batches.length<2;
  next.title=todo.batch_error?'后续候选暂不可读':batches.length===1?'今日只有一批':batches.length?'显示下一批推荐':'暂无可换的推荐';
  panel.append(node('p','digest-source','Grok 日报 · '+date));
  const label=node('p','digest-batch',batches.length?'第 '+(index+1)+'/'+batches.length+' 批'+(todo.batch_error?' · 后续候选暂不可读':batches.length===1?' · 今日只有一批':''):'暂无可换的推荐');
  label.id='today-todo-batch';label.setAttribute('role','status');label.setAttribute('aria-live','polite');panel.append(label);
  if(index>0)panel.append(node('p','digest-batch-origin','后续批次：今日爆款余量（非 Grok 精选）'));
  $('today-todo-toggle').textContent='今天该做的'+(todo.items?.length?' '+todo.items.length:'');
  if(!todo.items?.length){panel.append(node('h3','digest-empty-title',todo.status==='empty'?'今天没有值得做的':todo.status==='unavailable'?'今日日报暂不可读':'今天的日报还没出'),node('p','digest-reason',todo.reason||'今天的日报还没出，09:00 后更新'));}
  for(const item of batches[index]||[]){
    const block=node('article','digest-item');block.dataset.digestId=item.id;block.tabIndex=0;block.setAttribute('role','button');block.setAttribute('aria-label','看详情：'+item.title);
    const heading=node('div','digest-item-heading'),tier=item.hot?'火爆':['爆款','低粉爆款'].includes(item.tier)?'爆款':item.tier==='起势'?'起势':'';
    if(tier)heading.append(node('span','item-tier',tier));const title=node('h3','',item.title);title.title=item.title;heading.append(title);
    const angle=typeof item.my_angle==='string'?item.my_angle:'';const text=node('p','digest-angle',angle||'日报未提供切入角度');text.title=angle;
    block.append(heading,text,scores(item,'digest-scores'));
    const metrics=metricStrip(item);if(metrics){for(const el of [...metrics.children])if(!['views','views_per_hour'].includes(el.dataset.metric))el.remove();if(metrics.childNodes.length)block.append(metrics);}
    block.onclick=()=>openDigestItem(item);block.onkeydown=event=>{if(['Enter',' '].includes(event.key)){event.preventDefault();openDigestItem(item);}};panel.append(block);
  }
  const scroll=$('today-todo-content').scrollTop;$('today-todo-content').replaceChildren(panel);$('today-todo-content').scrollTop=scroll;
}
function updateDigest(result){digestResult=result.today_todo||{status:'pending',date:dayLabel(),items:[],reason:'今天的日报还没出，09:00 后更新'};showDigest();}
async function openDigestItem(item){
  // The daily list is independent of filters; selecting it does not rewrite the current channel or list.
  todoOpeningId=item.id;await app.select(item);$('item-list').querySelector('[data-item-id="'+CSS.escape(item.id)+'"]')?.scrollIntoView({block:'nearest'});if(matchMedia('(max-width:1199px)').matches)setTodoOpen(false);
}
$('today-todo-toggle').onclick=()=>setTodoOpen(!todoOpen,{remember:true});
$('today-todo-close').onclick=()=>setTodoOpen(!todoOpen,{remember:true});
document.addEventListener('keydown',event=>{
  if(event.key==='Escape'&&todoOpen&&matchMedia('(max-width:1199px)').matches){
    setTodoOpen(false,{remember:true});$('today-todo-close').focus();
  }
});
function renderRegistry(registry) {
  if (!registry || registryReady) return;
  registryReady=true;
  const group=$('platform-filters');group.replaceChildren();
  for(const [key, row] of Object.entries(registry.platforms)) {
    const button=node('button','platform-filter');button.type='button';button.dataset.platform=key;
    button.title=row.display_name;button.setAttribute('aria-label',row.display_name);button.setAttribute('aria-pressed',String(selectedPlatforms.includes(key)));
    const icon=lineIcon(key),title=document.createElementNS(icon.namespaceURI,'title');title.textContent=row.display_name;icon.prepend(title);button.append(icon);
    button.onclick=()=>{selectedPlatforms=selectedPlatforms.includes(key)?selectedPlatforms.filter(k=>k!==key):[...selectedPlatforms,key];button.setAttribute('aria-pressed',String(selectedPlatforms.includes(key)));page=1;load();};group.append(button);
  }
  const channels=$('collection-channel');channels.replaceChildren();
  for(const row of [{key:'',display_name:'全部渠道'},...Object.values(registry.channels)]){const button=node('button','filter-chip',row.display_name);button.type='button';button.dataset.collectionChannel=row.key;button.setAttribute('aria-pressed',String(collectionChannel===row.key));button.onclick=()=>{collectionChannel=collectionChannel===row.key?'':row.key;$('source').value='';page=1;syncControls();load();};channels.append(button);}

}
function topicButtons(options,selected) {
  const all={key:'',display_name:'全部',count:null},rows=[all,...[...options].sort((a,b)=>b.count-a.count||a.display_name.localeCompare(b.display_name,'zh-CN'))];
  $('topic-buttons').replaceChildren();$('topic-extra').replaceChildren();
  rows.forEach((row,index)=>{const b=node('button','filter-chip',row.display_name+(row.count===null?'':'（'+row.count+'）'));b.type='button';b.dataset.topic=row.key;b.title=row.description||row.display_name;b.setAttribute('aria-pressed',String(row.key===selected));b.onclick=()=>{$('topic').value=$('topic').value===row.key?'':row.key;page=1;load();};(index<=6?$('topic-buttons'):$('topic-extra')).append(b);});
  $('topic-more').hidden=rows.length<=7;
  if(selected&&!rows.some(r=>r.key===selected)){const b=node('button','filter-chip',selected+' ×');b.type='button';b.dataset.topic=selected;b.setAttribute('aria-pressed','true');b.onclick=()=>{$('topic').value='';page=1;load();};$('topic-buttons').append(b);}
}
function setCollapsed(value){$('filters').hidden=value;$('filter-summary').hidden=!value;$('filter-collapse').setAttribute('aria-expanded',String(!value));try{localStorage.setItem('today.filters.collapsed',String(value));}catch(_){}syncControls();}
function syncControls(){
  for(const b of document.querySelectorAll('[data-sort]'))b.setAttribute('aria-pressed',String(b.dataset.sort===$('sort').value));
  for(const b of document.querySelectorAll('[data-tier]'))b.setAttribute('aria-pressed',String(b.dataset.tier===$('tier').value));
  for(const b of document.querySelectorAll('[data-source]'))b.setAttribute('aria-pressed',String(b.dataset.source===$('source').value));
  for(const b of document.querySelectorAll('[data-collection-channel]'))b.setAttribute('aria-pressed',String(b.dataset.collectionChannel===collectionChannel));
  $('hot-only').checked=$('tier').value==='爆款';$('relevance-sort').hidden=channel!=='interest';
  const topic=document.querySelector('[data-topic][aria-pressed="true"]')?.textContent||$('topic').value;
  const platform=selectedPlatforms.map(key=>document.querySelector('[data-platform="'+CSS.escape(key)+'"]')?.title||key).join('、');
  $('filter-summary').textContent=[{hot:'发现',interest:'兴趣',official:'官方号'}[channel],{today:'今天','48h':'48小时','7d':'7天'}[range],{'':'推荐',heat:'最热',time:'最新',relevance:'按相关度'}[$('sort').value],topic&&topic!=='全部'?topic:'',platform,$('tier').value,collectionChannel,$('source').value,$('q').value?'搜索：'+$('q').value:''].filter(Boolean).join(' · ')+'　展开 ▾';
}
function heatControls(thresholds){
  if(!thresholds)return;const label='来源标记爆款；火爆徽标：浏览 ≥ '+thresholds.hot_views+'，官方号还须超过自身粉丝数';$('hot-only').title=label;$('hot-only').parentElement.title=label;
  $('tier-buttons').replaceChildren();for(const [value,text] of [['','全部'],['爆款','爆款'],['起势','起势'],['其他','普通'],['低粉爆款','低粉爆款'],['火爆','火爆徽标'],['top','该做的3条']]){if(value==='top')continue;const b=node('button','filter-chip',text);b.type='button';b.dataset.tier=value;b.title=thresholds.tiers?.[value]||(value==='火爆'?label:'既有来源标签');b.onclick=()=>{$('tier').value=$('tier').value===value?'':value;page=1;syncControls();load();};$('tier-buttons').append(b);}syncControls();
}
function updateAddress(params) {
  const address=new URL(location.href);address.search=params.toString();history.replaceState(null,'',address);syncControls();
}
let sourceStates={}, sourceCatchup={status:'waiting',sources:[]}, catchupPending=false, catchupChecked=0;
function openSourceDialog(title,text,items=[]) {
  const dialog=$('source-error');dialog.querySelector('h2').textContent=title;dialog.querySelector('pre').textContent=text;
  dialog.querySelector('.source-incomplete-list')?.remove();
  if(items.length){
    const list=node('ul','source-incomplete-list');
    for(const item of items){
      const li=node('li',''),link=node('a','',item.title+'（'+item.id+'）');
      try{const url=new URL(item.url);if(['https:','http:'].includes(url.protocol)&&!url.username&&!url.password){link.href=url.href;link.target='_blank';link.rel='noopener noreferrer';}}catch(_){}
      li.append(link,node('p','',(item.reasons||[]).join('；')));list.append(li);
    }
    dialog.insertBefore(list,dialog.querySelector('button'));
  }
  dialog.showModal();
}
function renderSources(sources=sourceStates) {
  sourceStates=sources;
  $('sources').replaceChildren();
  for(const [key,status] of Object.entries(sources)) {
    const label={G:'Grok Bot','grok:radar':'Grok 推特雷达',A:'AIsa',V:'AIsa · 视频榜',L:'RSS · linux.do',W:'微信雷达',feedback:'反馈'}[key] || key;
    const time=status.updated_at ? new Date(status.updated_at).toLocaleTimeString('zh-CN',{timeZone:'Asia/Shanghai',hour:'2-digit',minute:'2-digit',hour12:false}) : '暂无';
    const entry=node(status.error?'button':'span','status-block source-update '+(status.error?'status-warning':status.status==='disabled'?'status-idle':status.updated_at?'status-complete':'status-idle'),label+' '+time+(status.error?' · 异常':status.status==='waiting'?' · 等待产出':status.status==='disabled'?' · 已停用':key==='grok:radar'?' · '+(status.note || '正常'):''));
    if(status.note)entry.title=status.note;
    if(key==='grok:radar' && status.status==='ok' && status.note){entry.classList.add('source-notice');entry.style.color='var(--c-muted)';}
    if(status.error) {entry.type='button';entry.onclick=()=>openSourceDialog('来源异常详情',label+'\n'+status.error+'\n最近更新：'+(status.updated_at || '暂无'));}
    $('sources').append(entry);
    if(channel==='interest' && key==='G' && status.incomplete_entries>0){
      const notice=node('button','status-block source-incomplete',status.incomplete_entries+' 条内容不完整');
      notice.type='button';notice.dataset.noticeSource=key;notice.title=label+' · 查看内容缺失详情';
      notice.onclick=()=>openSourceDialog('内容不完整',label+' · '+notice.textContent+'\n最近 7 天兴趣报告的正文未完整展开，帖子仍照常展示。',status.incomplete_items||[]);
      $('sources').append(notice);
    }
  }
  const labels={'grok-daily':'Grok 日报','grok-alerts':'Grok 快报','grok-interest':'Grok 兴趣','twitter-radar':'AIsa','video-radar':'视频榜','linuxdo':'linux.do','wechat-radar':'微信雷达'};
  const states={waiting:'等待产出',running:'补跑中',completed:'已补',unavailable:'不可补',failed:'补跑失败',unknown:'状态待确认',fault:'来源故障'};
  const causes={sleep:'休眠漏报',offline:'离线漏报',overdue:'逾期',missing:'漏报'};
  for(const row of sourceCatchup.sources || []) {
    if(!row || !labels[row.source_id] || !states[row.status])continue;
    const cause=['waiting','fault'].includes(row.status)?'':(causes[row.cause] || '漏报')+' · ';
    const entry=node('button','status-block '+({running:'status-running',completed:'status-complete',failed:'status-failed',fault:'status-warning',waiting:'status-idle',unavailable:'status-warning',unknown:'status-warning'}[row.status]),labels[row.source_id]+' · '+cause+states[row.status]);
    entry.type='button';entry.dataset.catchupSource=row.source_id;entry.dataset.catchupStatus=row.status;
    if(['completed','running','waiting','unknown'].includes(row.status))entry.style.color='inherit';
    entry.onclick=()=>{const expected=row.expected_at?new Date(row.expected_at).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false}):'待确认';openSourceDialog('来源异常详情',entry.textContent+'\n'+(row.detail || '等待下一次状态检查')+'\n应出时间：'+expected+(row.attempts===0 && row.status==='unavailable'?'\n尚未发起补跑':''));};
    $('sources').append(entry);
  }
  if(sourceCatchup.status==='unavailable' || sourceCatchup.status==='unknown') {
    const entry=node('span','status-block status-warning','漏报状态待确认');entry.title=sourceCatchup.detail || '状态暂不可读，不能据此判断补跑成功';$('sources').append(entry);
  }
  const entries=[...$('sources').children],bad=entries.filter(el=>el.matches('.status-warning,.status-failed,.status-running,.status-idle'));
  const notices=entries.filter(el=>el.matches('.source-incomplete,.source-notice')),normal=entries.filter(el=>!bad.includes(el)&&!notices.includes(el)),details=node('details','source-overview'),summary=node('summary','source-summary status-block '+(bad.length?'':'status-complete'));
  summary.textContent=bad.length?'其余 '+normal.length+' 个来源正常':'全部 '+normal.length+' 个来源正常';
  details.append(summary);const expanded=node('div','source-expanded');
  // Keep original nodes so error-dialog handlers remain attached.
  expanded.append(...entries);details.append(expanded);
  for(const entry of [...notices,...bad]){const compact=entry.cloneNode(true);compact.onclick=()=>entry.click();$('sources').append(compact);}
  $('sources').append(details);
}

async function refreshCatchup() {
  if(document.hidden || catchupPending || Date.now()-catchupChecked<2000)return;
  catchupPending=true;catchupChecked=Date.now();
  const controller=new AbortController(), timeout=setTimeout(()=>controller.abort(),5000);
  try {
    const response=await fetch('/api/today?source_catchup=1',{cache:'no-store',signal:controller.signal});
    if(!response.ok)throw new Error('receipt unavailable');
    const body=await response.json(), value=body.source_catchup;
    if(!value || !Array.isArray(value.sources))throw new Error('receipt invalid');
    sourceCatchup=value;
  } catch(_) {sourceCatchup={status:'unknown',sources:[]};}
  finally {clearTimeout(timeout);catchupPending=false;renderSources();}
}
// Only receipt metadata is polled. Returning from sleep never dispatches work from the browser.
setInterval(refreshCatchup,60000);
document.addEventListener('visibilitychange',()=>{if(!document.hidden)refreshCatchup();});
let pendingInterestFiltering=null, interestAuditParams=null, auditSequence=0;
function renderFiltering(filtering={by_source:{},items:[],total:0},eager=false) {
  $('filtered-summary').textContent=channel==='official'?'官方号栏显示全部官方帖子 · 点开查看口径':channel==='interest'?'兴趣栏跨来源匹配 · 已过滤 '+filtering.total+' 条 · 点开核对':'已过滤 '+filtering.total+' 条 · 点开核对';
  $('filtered-counts').replaceChildren();$('filtered-items').replaceChildren();
  if(filtering.policy)$('filtered-counts').append(node('p','official-policy',filtering.policy));
  for(const [source,c] of Object.entries(filtering.by_source))$('filtered-counts').append(node('p','',source+'：'+c.total+' → '+c.visible+' 条，已过滤 '+c.filtered+' 条（'+(100*c.ratio).toFixed(1)+'%），内容不足 '+c.insufficient+' 条'));
  if(filtering.aisa_all){const c=filtering.aisa_all;$('filtered-counts').append(node('p','','AIsa 51 人全量推文：'+c.total+' → '+c.visible+' 条，已过滤 '+c.filtered+' 条（'+(100*c.ratio).toFixed(1)+'%）；原始采样统计，不属于主列表候选'));}
  // 兴趣回放可有数千个未命中项；明细在用户展开时再建 DOM，计数始终立即显示。
  if(channel==='interest' && !$('filtered-panel').open && !eager){pendingInterestFiltering=filtering;return;}
  pendingInterestFiltering=null;
  const appendGroup=(key,title,description,items,raw=false)=>{const group=node('li','filter-group');group.dataset.filterGroup=key;group.append(node('h3','',title));if(description)group.append(node('p','',description));const list=node('ul','');for(const item of items){const li=node('li',''),a=node('a','',item.title);li.dataset.auditId=item.id;try{const u=new URL(item.url);if(['https:','http:'].includes(u.protocol)&&!u.username&&!u.password){a.href=u.href;a.target='_blank';a.rel='noopener noreferrer';}}catch(_){}const reason=raw&&!item.hidden?'内容不足（原始采样统计，相关性保留）':item.reason;li.append(a,node('span','',' · '+item.sources.join(' / ')+' · '+reason));list.append(li);}if(!items.length)list.append(node('li','','本组暂无过滤或内容不足条目'));group.append(list);$('filtered-items').append(group);};
  const mainItems=filtering.items.filter(item=>!item.sources.includes('AIsa 51 人全量推文'));
  appendGroup('candidates','主列表候选过滤明细','以下条目来自当前列表候选；内容不足的条目保留并后排。',mainItems);
  if(filtering.aisa_all)appendGroup('aisa-raw','AIsa 51 人原始采样统计','原始采样统计，不属于主列表候选。相关性保留数不代表主列表新增展示数。',filtering.aisa_all_items || filtering.items.filter(item=>item.sources.includes('AIsa 51 人全量推文')),true);
}
$('filtered-panel').addEventListener('toggle',async()=>{
  if(!$('filtered-panel').open || !pendingInterestFiltering)return;
  const pending=pendingInterestFiltering,serial=++auditSequence,loadSerial=loadSequence;
  if(channel==='interest' && !pending.items.length && pending.total && interestAuditParams){
    $('filtered-items').replaceChildren(node('li','','正在读取过滤明细…'));
    try{const result=await get('/api/today?'+interestAuditParams);if(serial===auditSequence && loadSerial===loadSequence && channel==='interest' && $('filtered-panel').open)renderFiltering(result.filtering,true);}
    catch(error){if(serial===auditSequence && loadSerial===loadSequence)$('filtered-items').replaceChildren(node('li','error',error.message));}
  }else renderFiltering(pending,true);
});
function renderMorning(report) {
  $('clue-morning-summary').textContent='微信线索晨报 · '+(report?.status==='ok' ? report.date+' · '+report.clue_count+' 条 · 点开阅读全文' : report?.message || '今日晨报尚未生成');
  $('clue-morning-content').textContent=report?.status==='ok' ? report.content : report?.message || '每天 08:30 运行，可在任务工作台立即运行';
}
function interestEmpty(result) {
  const context=result.filter_context,filters=context?.active_filters||[];
  if(!filters.length)return node('p','top-message','本次暂无兴趣条目，试试 48 小时或 7 天');
  const box=node('div','interest-filter-empty');box.setAttribute('role','status');
  const period={today:'今天','48h':'48 小时内','7d':'7 天内'}[context.range]||'所选范围内';
  const reason=context.source_tier==='爆款'?'没有来源标记为爆款的条目':'没有符合条件的兴趣条目';
  const count=Number.isInteger(context.range_total)?'；本范围共有 '+context.range_total+' 条兴趣条目':'';
  box.append(node('p','top-message','当前筛选（'+filters.join('＋')+'）下，'+period+reason+count+'。'));
  if(context.source_tier==='爆款'){
    const cancel=node('button','button','取消只看爆款');cancel.type='button';
    cancel.onclick=()=>{$('tier').value='';page=1;syncControls();load();};box.append(cancel);
  }
  const clear=node('button','button','清空筛选');clear.type='button';
  clear.onclick=()=>{
    clearTimeout(searchTimer);selectedPlatforms=[];collectionChannel='';
    for(const id of ['tier','source','topic','q'])$(id).value='';
    for(const b of document.querySelectorAll('[data-platform]'))b.setAttribute('aria-pressed','false');
    page=1;syncControls();load();
  };box.append(clear);return box;
}
function renderList(result, {preserveSelection=false}={}) {
  const card=item=>{const element=todayCard(item);if(item.relevance_note)element.append(node('div','interest-warning','相关性待确认：'+item.relevance_note));return element;};
  const ordered=result.items;
  const list=$('item-list'), scrollTop=list.scrollTop;
  currentItems=ordered;if(!preserveSelection)digestMode=true;
  app.setItems(ordered,{preserveSelection}); list.replaceChildren();list.setAttribute('aria-busy','false');
  if(channel==='interest'||channel==='official'||$('sort').value) {
    const preparing=channel==='interest' && result.interest_cache?.partial;
    list.append(node('h2','list-heading',preparing?'兴趣 · 正在准备':(channel==='official'?'官方号':channel==='interest'?'兴趣':'发现')+' · '+result.total+' 条'));
    if(channel==='interest') {const policy=node('p','interest-arrival-policy',result.arrival_policy||'按首次抵达时间统计，重复同步不计作新到达');policy.setAttribute('role','note');list.append(policy);}
    if(channel==='interest' && result.interest_cache && (!result.interest_cache.ready || result.interest_cache.checking)) {const status=node('p','interest-index-status',result.interest_cache.partial?'全来源索引正在首次建立，数量暂不完整':result.interest_cache.checking&&result.interest_cache.ready?'正在核对来源更新，暂显示上次完整结果':'全来源索引正在后台更新，暂显示上次完整结果');status.setAttribute('role','status');list.append(status);}
    if(channel==='interest' && result.sort_policy)list.append(node('p','top-message',result.sort_policy));
    for(const item of result.items)list.append(card(item));
    if(!result.items.length && !preparing)list.append(channel==='official'?node('p','top-message',range==='today'?'今天没有官方号帖子，可切 48 小时 / 7 天':'这个范围没有官方号帖子，可切换时间范围'):interestEmpty(result));
  } else {
    list.append(node('h2','list-heading','发现 · '+result.total+' 条'));
    for(const item of result.items)list.append(card(item));
    if(!ordered.length)list.append(node('p','top-message','这个范围内没有条目，试试 48 小时或 7 天'));
  }
  if(result.total>50) {
    const pager=node('div','pagination');
    for(const [label,delta] of [['上一页',-1],['下一页',1]]) {const b=node('button','',label);b.disabled=result.page+delta<1 || (result.page+delta-1)*50>=result.total;b.onclick=()=>{page+=delta;load();};pager.append(b);}
    pager.append(node('span','', '第 '+result.page+' 页'));list.append(pager);
  }
  list.append(filteredPanel);
  if(preserveSelection)list.scrollTop=scrollTop;
}
async function load({cached=false,background=false}={}) {
  clearTimeout(refreshTimer);
  const serial=++loadSequence;
  pendingInterestFiltering=null;interestAuditParams=null;++auditSequence;
  $('filtered-summary').textContent=(channel==='interest'?'兴趣栏':channel==='official'?'官方号栏':'发现栏')+' · 正在读取过滤口径…';
  $('filtered-counts').replaceChildren();$('filtered-items').replaceChildren();
  if(!background)$('item-list').inert=true; $('item-list').setAttribute('aria-busy','true');
  const params=new URLSearchParams({range,page:String(page),tier:$('tier').value,source:$('source').value,topic:$('topic').value,q:$('q').value});
  if($('sort').value)params.set('sort',$('sort').value);
  if(channel!=='hot')params.set('channel',channel);
  if(selectedPlatforms.length)params.set('platform',selectedPlatforms.join(','));
  if(collectionChannel)params.set('collection_channel',collectionChannel);
  updateAddress(params);if(channel==='interest')interestAuditParams=new URLSearchParams(params);
  try {
    const result=await getList(params,cached);
    if(serial!==loadSequence)return;
    renderRegistry(result.registries);heatControls(result.heat_thresholds);$('total').textContent=result.interest_cache?.partial?'正在准备':result.total+' 条';renderSources(result.sources);renderMorning(result.clue_morning);renderFiltering(result.filtering);renderList(result,{preserveSelection:background});updateDigest(result);if(channel==='interest' && $('filtered-panel').open && result.filtering.total && !result.filtering.items.length){pendingInterestFiltering=result.filtering;$('filtered-panel').dispatchEvent(new Event('toggle'));}
    const current=result.selected_topic || $('topic').value;
    $('topic').value=current;topicButtons(result.topic_options||[],current);if(result.selected_topic && params.get('topic')!==result.selected_topic){params.set('topic',result.selected_topic);updateAddress(params);}syncControls();
    if(restoreSelection){restoreSelection=false;try{const saved=JSON.parse(sessionStorage.getItem('today.selection')||'null'),found=performance.getEntriesByType('navigation')[0]?.type==='reload'&&saved?.query===location.search&&[...currentItems,...todoBatches(digestResult).flat()].find(item=>item.id===saved.id);if(found){if(saved.fromTodo)todoOpeningId=saved.id;await app.select(found);}}catch(_){}}
    else if(!background){try{sessionStorage.removeItem('today.selection');}catch(_){}}
    $('item-list').inert=false;if(cached || !didPrefetch){didPrefetch=true;prefetchTabs(params);}
    if(result.sources.L?.refreshing || result.interest_cache?.refreshing || result.interest_cache?.checking)refreshTimer=setTimeout(()=>load({background:true}),1200);
  }catch(error){if(serial===loadSequence){$('item-list').inert=false;$('item-list').setAttribute('aria-busy','false');$('item-list').replaceChildren(node('p','error','加载失败：'+error.message));}}
}
$('today-refresh').onclick=()=>{tabCache.clear();load({background:true});};
$('filters').onsubmit=event=>{event.preventDefault();clearTimeout(searchTimer);page=1;load();};
$('q').oninput=()=>{clearTimeout(searchTimer);searchTimer=setTimeout(()=>{page=1;load();},400);};
for(const button of document.querySelectorAll('[data-sort]'))button.onclick=()=>{$('sort').value=button.dataset.sort;page=1;syncControls();load();};
for(const button of document.querySelectorAll('[data-source]'))button.onclick=()=>{$('source').value=$('source').value===button.dataset.source?'':button.dataset.source;collectionChannel='';page=1;syncControls();load();};
$('hot-only').onchange=()=>{$('tier').value=$('hot-only').checked?'爆款':'';page=1;syncControls();load();};
$('filter-collapse').onclick=()=>setCollapsed(true);$('filter-summary').onclick=()=>setCollapsed(false);
for(const id of ['tier','source','topic','sort'])$(id).onchange=()=>{page=1;syncControls();load();};
for(const button of document.querySelectorAll('[data-range]'))button.onclick=()=>{range=button.dataset.range;page=1;for(const b of document.querySelectorAll('[data-range]'))b.setAttribute('aria-pressed',String(b===button));load();};
for(const button of document.querySelectorAll('[data-channel]'))button.onclick=()=>{channel=button.dataset.channel;page=1;++authorSequence;for(const b of document.querySelectorAll('[data-channel]'))b.setAttribute('aria-pressed',String(b===button));$('tier').value='';$('sort').value='';$('topic').value='';syncControls();load({cached:true});};
$('source-error').querySelector('button').onclick=()=>$('source-error').close();
if(['today','48h','7d'].includes(initialParams.get('range'))){range=initialParams.get('range');for(const b of document.querySelectorAll('[data-range]'))b.setAttribute('aria-pressed',String(b.dataset.range===range));}
if(initialParams.has('topic'))$('topic').value=initialParams.get('topic');
for(const id of ['tier','source','q','sort'])if(initialParams.has(id))$(id).value=id==='source'?initialParams.get(id).toUpperCase():initialParams.get(id);
window.addEventListener('focus',()=>{tabCache.clear();refreshCatchup();});
refreshCatchup();
const initialChannel=initialParams.get('channel');if(['interest','official'].includes(initialChannel)){channel=initialChannel;for(const button of document.querySelectorAll('[data-channel]'))button.setAttribute('aria-pressed',String(button.dataset.channel===channel));}try{setCollapsed(localStorage.getItem('today.filters.collapsed')==='true');}catch(_){setCollapsed(false);}load();
