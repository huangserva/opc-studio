import {grokNode as node,safeHttpUrl,sourceBadges} from './components/item-card.js';
const $=id=>document.getElementById(id), platforms={x:'X',wechat:'微信公众号',douyin:'抖音',xiaohongshu:'小红书',channels:'视频号'};
let selected=null,sequence=0;
const time=value=>value&&!Number.isNaN(Date.parse(value))?new Date(value).toLocaleString('zh-CN',{hour12:false,timeZone:'Asia/Shanghai'}):'未知';
async function request(url,options={}){const response=await fetch(url,{cache:'no-store',...options});const data=await response.json();if(!response.ok)throw Error(data.error||'请求失败');return data;}
function link(parent,url,label){if(url.startsWith('/')&&!url.startsWith('//')||safeHttpUrl(url)){const a=node('a','',label);a.href=url;if(!url.startsWith('/')){a.target='_blank';a.rel='noopener noreferrer';}parent.append(a);}}
function metrics(parent,values){const list=node('div','review-metrics');for(const m of Object.values(values)){const span=node('span','',m.label+' '+(m.value??'未知'));span.title=m.reason+(m.collected_at?' · '+time(m.collected_at):'');list.append(span);}parent.append(list);}
async function select(row){
 selected=row.delivery_id;const serial=++sequence;
 document.querySelectorAll('.published-card').forEach(c=>c.classList.toggle('selected',c.dataset.deliveryId===selected));
 $('review-detail').replaceChildren(node('p','','正在读取快照…'));
 try{const data=await request('/api/publish/review/'+encodeURIComponent(row.slug));if(serial!==sequence)return;
  const panel=$('review-detail');panel.replaceChildren(node('h2','',row.title));
  link(panel,'/?article='+encodeURIComponent(row.slug),'打开作品');
  for(const record of data.items.filter(r=>r.delivery_id===selected)){
   panel.append(node('h3','',platforms[record.platform]||record.platform));
   panel.append(node('p','',record.time_basis+'：'+time(record.sort_at)));metrics(panel,record.metrics);
   panel.append(node('h3','','全部快照 · '+record.snapshots.length));
   if(!record.snapshots.length)panel.append(node('p','review-empty','尚无指标快照，可手动同步 X 数据或在旧页导入。'));
   else{const table=node('table','snapshot-table'),head=node('tr');for(const title of ['采集时间','指标','来源 / 口径'])head.append(node('th','',title));const thead=node('thead');thead.append(head);const tbody=node('tbody');
    for(const point of record.snapshots){const tr=node('tr');tr.append(node('td','',time(point.collected_at)),node('td','',Object.entries(point.metrics).map(([k,v])=>k+' '+v).join(' · ')||'未知'),node('td','',point.source+(point.range?.label?' · '+point.range.label:'')));tbody.append(tr);}table.append(thead,tbody);panel.append(table);}
  }
  panel.append(node('h3','','复盘备注'),node('div','review-notes',data.review?.notes||'还没有复盘备注。'));
  link(panel,'/feedback.html?review='+encodeURIComponent(row.slug),'去旧页编辑复盘备注');
 }catch(error){if(serial===sequence)$('review-detail').replaceChildren(node('p','error',error.message));}
}
async function load(){
 try{const data=await request('/api/publish/review');$('total').textContent=data.total+' 条已发布';$('week-count').textContent=data.week_count;$('week-items').textContent=data.week_from_items;$('notice').textContent=data.notice+(data.warnings.length?' '+data.warnings.join('；'):'');
 const list=$('published-list');list.replaceChildren();
 if(!data.items.length)list.append(node('p','review-empty','还没有已发布记录。发布并登记后，数据会显示在这里。'));
 for(const row of data.items){const card=node('article','published-card');card.tabIndex=0;card.dataset.deliveryId=row.delivery_id;card.dataset.slug=row.slug;card.append(node('h2','',row.title),node('p','review-meta',(platforms[row.platform]||row.platform)+' · '+row.time_basis+' '+time(row.sort_at)));link(card,row.url,'打开发布链接');metrics(card,row.metrics);
  if(row.source_item){const source=node('div','review-source');source.append(node('span','','来源素材 '),sourceBadges(row.source_item));link(source,row.source_item.url,row.source_item.title);card.append(source);}
  card.onclick=e=>{if(!e.target.closest('a'))select(row);};card.onkeydown=e=>{if(e.key==='Enter'&&e.target===card){e.preventDefault();select(row);}};list.append(card);
 }list.setAttribute('aria-busy','false');
 const slug=new URLSearchParams(location.search).get('article');const chosen=data.items.find(r=>r.delivery_id===selected||r.slug===slug);if(chosen)select(chosen);
 }catch(error){$('published-list').replaceChildren(node('p','error',error.message));$('published-list').setAttribute('aria-busy','false');}
}
$('sync').onclick=async()=>{
 const button=$('sync');button.disabled=true;$('sync-status').textContent='正在手动同步…';
 try{const status=await request('/api/x-official/status');if(!status.connected||!status.account?.id)throw Error('请先到「X 发布 / 账号设置」连接账号');const end=new Date(Date.now()-60000),start=new Date(end.getTime()-7*86400000);
 await request('/api/x-official/feedback-sync',{method:'POST',headers:{'Content-Type':'application/json','X-Article-Studio':'1'},body:JSON.stringify({account_id:status.account.id,mode:'timeline',post_kind:'original',start_time:start.toISOString(),end_time:end.toISOString()})});$('sync-status').textContent='同步完成';await load();
 }catch(error){$('sync-status').textContent='同步失败：'+error.message;}finally{button.disabled=false;}
};
load();
