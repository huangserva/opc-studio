import {createListReader} from './components/list-reader.js';
import {grokNode as node} from './components/item-card.js';
const $ = id => document.getElementById(id);
let channel = 'hot', range = 'today', page = 1, loadSequence = 0, authorSequence = 0, refreshTimer;
async function get(url) {const response=await fetch(url,{cache:'no-store'}); const body=await response.json(); if(!response.ok) throw new Error(body.error || '读取失败');return body;}
const app = createListReader({
  draftFromToday:true, list:$('item-list'), reader:$('reader'), layout:$('layout'), divider:$('divider'),
  loadDetail:id=>get('/api/today/item?id='+encodeURIComponent(id)+(channel==='interest'?'&channel=interest':'')),
  onAction:async(id,action,item)=>{const response=await fetch('/api/today/feedback',{method:'POST',headers:{'Content-Type':'application/json','X-Article-Studio':'1'},body:JSON.stringify({id,action,...(item.channel==='interest'?{channel:'interest'}:{})})}); const body=await response.json();if(!response.ok) throw new Error(body.error || '保存失败');return body;},
  onAuthor:async author=>{const serial=++authorSequence, selected=$('reader').querySelector('.item-detail')?.dataset.itemId;try {const url='/api/today?author='+encodeURIComponent(author.handle || author.name)+(channel==='interest'?'&channel=interest&range=7d':'');const result=await get(url);for(let p=2;result.items.length<result.total;p++){const next=await get(url+'&page='+p);if(!next.items.length)break;result.items.push(...next.items);}if(serial===authorSequence && $('reader').querySelector('.item-detail')?.dataset.itemId===selected)app.showAuthor(result.items,author);}catch(error){if(serial===authorSequence)$('reader').append(node('p','error',error.message));}}
});
function renderSources(sources) {
  $('sources').replaceChildren();
  for(const [key,status] of Object.entries(sources)) {
    const label={G:'Grok',A:'AIsa',V:'视频榜',L:'linux.do',feedback:'反馈'}[key] || key;
    const time=status.updated_at ? new Date(status.updated_at).toLocaleTimeString('zh-CN',{timeZone:'Asia/Shanghai',hour:'2-digit',minute:'2-digit',hour12:false}) : '暂无';
    const entry=node(status.error?'button':'span','',label+' '+time+(status.error?' · 异常':''));
    if(status.error) {entry.type='button';entry.onclick=()=>{$('source-error').querySelector('pre').textContent=label+'\n'+status.error+'\n最近更新：'+(status.updated_at || '暂无');$('source-error').showModal();};}
    $('sources').append(entry);
  }
}
function renderFiltering(filtering={by_source:{},items:[],total:0}) {
  $('filtered-summary').textContent='已过滤 '+filtering.total+' 条 · 点开核对';
  $('filtered-counts').replaceChildren();$('filtered-items').replaceChildren();
  for(const [source,c] of Object.entries(filtering.by_source))$('filtered-counts').append(node('p','',source+'：'+c.total+' → '+c.visible+' 条，已过滤 '+c.filtered+' 条（'+(100*c.ratio).toFixed(1)+'%），内容不足 '+c.insufficient+' 条'));
  if(filtering.aisa_all){const c=filtering.aisa_all;$('filtered-counts').append(node('p','','AIsa 51 人全量推文：'+c.total+' → '+c.visible+' 条，已过滤 '+c.filtered+' 条（'+(100*c.ratio).toFixed(1)+'%）；原始采样统计，不属于主列表候选'));}
  const appendGroup=(key,title,description,items,raw=false)=>{const group=node('li','filter-group');group.dataset.filterGroup=key;group.append(node('h3','',title));if(description)group.append(node('p','',description));const list=node('ul','');for(const item of items){const li=node('li',''),a=node('a','',item.title);li.dataset.auditId=item.id;try{const u=new URL(item.url);if(['https:','http:'].includes(u.protocol)&&!u.username&&!u.password){a.href=u.href;a.target='_blank';a.rel='noopener noreferrer';}}catch(_){}const reason=raw&&!item.hidden?'内容不足（原始采样统计，相关性保留）':item.reason;li.append(a,node('span','',' · '+item.sources.join(' / ')+' · '+reason));list.append(li);}if(!items.length)list.append(node('li','','本组暂无过滤或内容不足条目'));group.append(list);$('filtered-items').append(group);};
  const mainItems=filtering.items.filter(item=>!item.sources.includes('AIsa 51 人全量推文'));
  appendGroup('candidates','主列表候选过滤明细','以下条目来自当前列表候选；内容不足的条目保留并后排。',mainItems);
  if(filtering.aisa_all)appendGroup('aisa-raw','AIsa 51 人原始采样统计','原始采样统计，不属于主列表候选。相关性保留数不代表主列表新增展示数。',filtering.aisa_all_items || filtering.items.filter(item=>item.sources.includes('AIsa 51 人全量推文')),true);
}
function renderMorning(report) {
  $('clue-morning-summary').textContent='微信线索晨报 · '+(report?.status==='ok' ? report.date+' · '+report.clue_count+' 条 · 点开阅读全文' : report?.message || '今日晨报尚未生成');
  $('clue-morning-content').textContent=report?.status==='ok' ? report.content : report?.message || '每天 08:30 运行，可在任务工作台立即运行';
}
function renderList(result) {
  const card=item=>{const element=app.card(item);if(item.rss_boards?.length)element.append(node('div','item-card-foot',item.rss_boards.join(' / ')+' · 回复 '+(item.reply_change || '—')));if(item.relevance_note)element.append(node('div','interest-warning','相关性待确认：'+item.relevance_note));return element;};
  const topIds=new Set(result.top.map(p=>p.id));
  const ordered=[...result.top,...result.items.filter(p=>!topIds.has(p.id))];
  app.setItems(ordered); const list=$('item-list');list.replaceChildren();list.setAttribute('aria-busy','false');
  if(channel==='interest') {
    list.append(node('h2','list-heading','兴趣 · '+result.total+' 条'));
    for(const item of result.items)list.append(card(item));
    if(!result.items.length)list.append(node('p','top-message','本次暂无兴趣条目，试试 48 小时或 7 天'));
  } else {
  list.append(node('h2','list-heading','今天该做的 3 条'));
  if(result.top_message)list.append(node('div','top-message',result.top_message));
  for(const item of result.top)list.append(card(item));
  list.append(node('h2','list-heading other-heading','其余 '+Math.max(0,result.total-result.top.length)+' 条'));
  const rest=result.items.filter(p=>!topIds.has(p.id));
  for(const item of rest)list.append(card(item));
  if(!ordered.length) list.append(node('p','top-message','这个范围内没有条目，试试 48 小时或 7 天'));
  }
  if(result.total>50) {
    const pager=node('div','pagination');
    for(const [label,delta] of [['上一页',-1],['下一页',1]]) {const b=node('button','',label);b.disabled=result.page+delta<1 || (result.page+delta-1)*50>=result.total;b.onclick=()=>{page+=delta;load();};pager.append(b);}
    pager.append(node('span','', '第 '+result.page+' 页'));list.append(pager);
  }
}
async function load() {
  clearTimeout(refreshTimer);
  const serial=++loadSequence;
  $('item-list').inert=true; $('item-list').setAttribute('aria-busy','true');
  const params=new URLSearchParams({range,page:String(page),tier:$('tier').value,source:$('source').value,topic:$('topic').value,q:$('q').value});
  if(channel==='interest')params.set('channel',channel);
  try {
    const result=await get('/api/today?'+params);
    if(serial!==loadSequence)return;
    $('total').textContent=result.total+' 条';renderSources(result.sources);renderMorning(result.clue_morning);renderFiltering(result.filtering);renderList(result);
    const current=$('topic').value;
    $('topic').replaceChildren(new Option('全部方向',''),...result.topics.map(t=>new Option(t,t)));$('topic').value=current;
    $('item-list').inert=false;
    if(result.sources.L?.refreshing)refreshTimer=setTimeout(load,1200);
  }catch(error){if(serial===loadSequence){$('item-list').inert=false;$('item-list').setAttribute('aria-busy','false');$('item-list').replaceChildren(node('p','error','加载失败：'+error.message));}}
}
$('filters').onsubmit=event=>{event.preventDefault();page=1;load();};
for(const id of ['tier','source','topic'])$(id).onchange=()=>{page=1;load();};
for(const button of document.querySelectorAll('[data-range]'))button.onclick=()=>{range=button.dataset.range;page=1;for(const b of document.querySelectorAll('[data-range]'))b.setAttribute('aria-pressed',String(b===button));load();};
for(const button of document.querySelectorAll('[data-channel]'))button.onclick=()=>{channel=button.dataset.channel;page=1;++authorSequence;for(const b of document.querySelectorAll('[data-channel]'))b.setAttribute('aria-pressed',String(b===button));for(const id of ['tier','source']){$(id).disabled=channel==='interest';$(id).value='';}$('topic').value='';load();};
$('source-error').querySelector('button').onclick=()=>$('source-error').close();
load();
