import {createListReader} from './components/list-reader.js';
import {grokNode as node,renderPostDetail,safeHttpUrl} from './components/item-card.js';
const $=id=>document.getElementById(id);
let mode='mine',trashed=false,page=1,sequence=0,timer;
const label={weknora:'大后方 · WeKnora',projects:'项目资料',wechat:'微信消息','wechat-links':'微信分享链接',intake:'创意回收',cards:'提示词卡','external-references':'外部收藏',articles:'我的作品',manual:'手动保存',remio:'remio',G:'Grok Bot',A:'AIsa',V:'视频榜'};
async function request(url,method='GET',data){const response=await fetch(url,{method,cache:'no-store',headers:method==='GET'?{}:{'Content-Type':'application/json','X-Article-Studio':'1'},...(data===undefined?{}:{body:JSON.stringify(data)})});const body=await response.json();if(!response.ok)throw Error(body.error||'请求失败');return body;}
const itemPath=id=>'/api/items/'+encodeURIComponent(id);
function actionButton(action,text,item,options){const b=node('button','item-action',text);b.type='button';b.dataset.action=action;b.onclick=event=>options.onAction(action,item,event);return b;}
function renderDetail(item,options){
 const content=renderPostDetail(item,{});
 content.querySelectorAll('.detail-author,.detail-meta,.detail-metrics').forEach(n=>n.remove());
 const source=(item.sources||[item.source]).map(s=>label[s]||s).join(' · ');
 const time=item.published_at||item.created_at;
 const refs=item.provenance?[item.provenance]:(item.legacy_refs||[]).filter(r=>r.store==='weknora');
 if(refs.length)content.querySelectorAll('.incomplete-warning').forEach(n=>n.remove());
 for(const ref of refs){
  const meta=node('aside','remote-reference','编号：'+(ref.result_number||'未知')+' · 隐私：'+(ref.privacy||'未知')+'\n远端知识 ID：'+(ref.remote_knowledge_id||ref.id||'未知')+'\n块 ID：'+(ref.remote_chunk_id||ref.chunk_id||'未知'));
  if(ref.local_document_id){const a=node('a','','本机原文：'+ref.local_document_id);a.href='/knowledge.html?document='+encodeURIComponent(ref.local_document_id);meta.append(a);}
  const rid=item.id.startsWith('kb:wk:')?item.id.slice(3):item.id;
  if(rid.startsWith('wk:')){const a=node('a','','固定检索片段出处');a.href='/library.html?remote='+encodeURIComponent(rid);meta.append(a);}
  content.prepend(meta);
 }
 if(item.incomplete_reason)content.prepend(node('aside','incomplete-warning',item.incomplete_reason));
 content.prepend(node('h2','library-title',item.title),node('div','library-origin',source+(time?' · '+new Date(time).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false}):''))); 
 content.querySelector('.item-actions')?.remove();
 // Read-only research never exposes today's feedback/write actions.
 const bar=node('div','item-actions');
 const url=safeHttpUrl(item.url);if(url){const a=node('a','','打开来源');a.href=url;a.target='_blank';a.rel='noopener noreferrer';bar.append(a);}
 if(mode==='research')bar.append(actionButton('save','存为素材',item,options));
 else if(trashed)bar.append(actionButton('restore','恢复',item,options),actionButton('delete','彻底删除',item,options));
 else{
  content.append(node('h3','','收藏理由'));const note=node('textarea');note.id='note-edit';note.value=item.note||'';note.setAttribute('aria-label','收藏理由');content.append(note);
  if(item.attachments?.length){content.append(node('h3','','附件'));for(const a of item.attachments)content.append(node('div','attachment-list',a.path+'\n'+JSON.stringify(a.meta||{})));}
  bar.append(actionButton('use','拿去写',item,options),actionButton('note','保存收藏理由',item,options),actionButton('trash','移到回收站',item,options));
 }
 bar.append(node('span','action-notice'));content.append(bar);return content;
}
const app=createListReader({list:$('item-list'),reader:$('reader'),layout:$('layout'),divider:$('divider'),renderDetail,
 loadDetail:async id=>{
  if(mode==='mine')return request(itemPath(id));
  const d=await request(id.startsWith('wk:')?'/api/library/remote?'+new URLSearchParams({id,include_private:$('include-private').checked?'1':'0'}):'/api/knowledge/document/'+encodeURIComponent(id));
  return {...d,full_text:d.body,sources:[d.source],author:{name:label[d.source]||d.source},published_at:d.modified,metrics:{},content_complete:d.content_complete!==false};
 },
 onAction:async(id,action)=>{
  if(action==='save'){await request('/api/items','POST',id.startsWith('wk:')?{remote_id:id,include_private:$('include-private').checked}:{document_id:id});return {saved:true};}
  if(action==='delete'){
   if(!confirm('彻底删除后无法恢复。确定删除这条素材及其提示词卡？'))return {notice:'已取消'};
   if(!confirm('请再次确认彻底删除。原始资料仍保留在旧版中。'))return {notice:'已取消'};
   await request(itemPath(id),'DELETE');await load();return {};
  }
  const data=action==='note'?{note:$('note-edit').value}:{};
  const result=await request(itemPath(id)+'/'+action,'POST',data);
  if(action==='trash'||action==='restore')await load();
  return result;
 }
});
function highlight(parent,text){
 // Only our control-character markers become <mark>. Every source byte stays textContent.
 let marked=false;for(const part of String(text||'').split(/([\u0001\u0002])/)){
  if(part==='\u0001'){marked=true;continue;}if(part==='\u0002'){marked=false;continue;}
  if(marked)parent.append(node('mark','',part));else parent.append(document.createTextNode(part));
 }
}
function card(item){
 const c=node('article','item-card');c.tabIndex=0;c.dataset.itemId=item.id;c.setAttribute('aria-label',item.title);
 const source=(item.sources||[item.source]).map(s=>label[s]||s).join(' · ');
 const ref=item.provenance;
 if(ref)c.append(node('div','remote-reference',(ref.result_number||'')+' · 隐私：'+(ref.privacy||'未知')+' · 检索片段'));
 c.append(node('div','item-card-meta',source),node('div','item-summary',item.title));
 if(mode==='research'){const snippet=node('div','library-snippet');highlight(snippet,item.snippet);c.append(snippet);}
 else if(item.note)c.append(node('div','library-note',item.note));
 const time=item.published_at||item.created_at;c.append(node('div','library-meta',(time?new Date(time).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false}):'时间未知')+(mode==='mine'?' · '+({used:'已拿去写',kept:'已收藏'}[item.status]||'素材'):'')));
 c.onclick=()=>app.select(item);c.onkeydown=e=>{if(e.key==='Enter'){e.preventDefault();app.select(item);}};return c;
}
function choices(id,values,title){const current=$(id).value;$(id).replaceChildren(new Option(title,''),...values.map(v=>new Option(label[v]||v,v)));if(values.includes(current))$(id).value=current;}
async function load(){
 const serial=++sequence;app.setItems([]);$('item-list').inert=true;$('item-list').setAttribute('aria-busy','true');
 const params=new URLSearchParams({q:$('q').value,source:$('source').value,page:String(page)});
 if(mode==='mine')for(const key of ['kind','tag','status'])params.set(key,$(key).value);
 if(mode==='research')params.set('include_private',$('include-private').checked?'1':'0');
 if(mode==='mine')params.set('trashed',trashed?'1':'0');
 try{
  const result=await request((mode==='mine'?'/api/items?':'/api/library/search?')+params);if(serial!==sequence)return;
  app.setItems(result.items);$('item-list').replaceChildren(...result.items.map(card));
  if(!result.items.length)$('item-list').append(node('div','library-empty',mode==='research'&&!$('q').value?'输入关键词，检索已有资料。喜欢的内容可以存为素材。':mode==='research'&&result.partial?'本次检查的资料中没有找到，标题目录就绪后可重新搜索':trashed?'回收站是空的':'没有找到内容，换个关键词试试'));
  $('total').textContent=(result.total_label??result.total)+' 条';
  $('page-notice').textContent=result.short_query&&$('q').value?(result.catalog_state==='building'?'标题目录正在后台构建，暂用限时扫描（检查 '+result.scanned_count+' 条），结果可能不完整。':result.catalog_state==='unavailable'?'标题目录暂不可用，暂用限时扫描（检查 '+result.scanned_count+' 条），结果可能不完整。':result.partial?'只搜了最近 '+result.scanned_count+' 条资料，结果可能不完整。':'已搜索全库标题。')+'短词仅搜索资料标题；输入 3 个字以上可搜索正文':'';
  if(mode==='research'&&result.remote)$('page-notice').textContent=[result.remote.notice,$('page-notice').textContent].filter(Boolean).join('；');
  if(mode==='mine'){choices('source',result.sources,'全部来源');choices('tag',result.tags,'全部标签');}else if(result.sources?.length)choices('source',result.sources,'全部来源');
  const more=mode==='mine'?page*50<result.total:result.has_more;
  if(page>1||more){const pager=node('div','pagination');for(const [text,delta,disabled] of [['上一页',-1,page===1],['下一页',1,!more]]){const b=node('button','',text);b.disabled=disabled;b.onclick=()=>{page+=delta;load();};pager.append(b);}pager.append(node('span','','第 '+page+' 页'));$('item-list').append(pager);}
 }catch(e){if(serial===sequence)$('item-list').replaceChildren(node('p','error',e.message));}
 finally{if(serial===sequence){$('item-list').inert=false;$('item-list').setAttribute('aria-busy','false');}}
}
function switchMode(next){mode=next;$('private-filter').hidden=mode!=='research';trashed=false;page=1;$('mine').setAttribute('aria-selected',String(mode==='mine'));$('research').setAttribute('aria-selected',String(mode==='research'));$('trash').hidden=$('trash-bottom').hidden=mode==='research';for(const id of ['kind','tag','status'])$(id).hidden=mode==='research';$('tab-hint').textContent=mode==='mine'?'存下来的内容，随时拿去写':'搜索已有语料，选择值得留下的内容';choices('source',mode==='research'?['wechat','projects','github','gmail','external-references','articles','manual','remio','weknora']:[],'全部来源');$('trash').textContent='回收站';load();}
$('mine').onclick=()=>switchMode('mine');$('research').onclick=()=>switchMode('research');
for(const id of ['trash','trash-bottom'])$(id).onclick=()=>{if(mode!=='mine')return;trashed=!trashed;page=1;$('trash').textContent=trashed?'返回素材':'回收站';$('tab-hint').textContent=trashed?'回收站 · 可恢复或彻底删除':'存下来的内容，随时拿去写';load();};
$('filters').onsubmit=e=>{e.preventDefault();clearTimeout(timer);page=1;load();};$('q').oninput=()=>{clearTimeout(timer);timer=setTimeout(()=>{page=1;load();},300);};for(const id of ['source','kind','tag','status'])$(id).onchange=()=>{page=1;load();};
$('include-private').onchange=()=>{page=1;load();};
load().then(async()=>{const remote=new URLSearchParams(location.search).get('remote');if(remote){switchMode('research');await load();await app.select({id:remote});return;}const id=new URLSearchParams(location.search).get('item');if(id)try{await app.select(await request(itemPath(id)));}catch(error){$('reader').replaceChildren(node('p','error',error.message));$('layout').classList.add('reading');}});
