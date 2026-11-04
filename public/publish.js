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
// Grok 原文只写 textContent，不解析HTML、不改写结论。
function textNode(tag,cls='',value){const el=document.createElement(tag);if(cls)el.className=cls;if(value!==undefined)el.textContent=String(value);return el;}
const xLabels={views:'浏览',likes:'赞',bookmarks:'收藏',retweets:'转发',replies:'回复',quotes:'引用'};
const number=value=>typeof value==='number'&&Number.isFinite(value)&&value>=0?value.toLocaleString('zh-CN'):'未知';
let xRows=[],xSelected='';
function xMetrics(parent,values){const box=textNode('div','review-metrics');for(const [key,label]of Object.entries(xLabels))box.append(textNode('span','',label+' '+number(values?.[key])));parent.append(box);}
function xSource(parent,source){if(!source)return;const box=textNode('div','review-source x-source');link(box,source.url,'来自今日页 · '+(source.title||'打开原条目'));box.title=source.basis+(source.report?' · '+source.report:'');parent.append(box);}
function xCard(row){const card=textNode('article','x-post-card');card.dataset.postId=row.id;card.tabIndex=0;card.setAttribute('aria-label',row.type+' '+(row.text||'正文未知').slice(0,60));card.append(textNode('p','review-meta',row.type+' · 发帖（北京） '+time(row.created_at)),textNode('p','x-post-text',row.text||'正文未知'));xMetrics(card,row.metrics);card.append(textNode('small','muted','Grok 抓取 '+time(row.captured_at)));xSource(card,row.from_radar);card.onclick=e=>{if(!e.target.closest('a'))selectX(row);};card.onkeydown=e=>{if(e.key==='Enter'&&e.target===card){e.preventDefault();selectX(row);}};return card;}
function svgElement(tag,attrs={},value){const el=document.createElementNS('http://www.w3.org/2000/svg',tag);for(const[key,val]of Object.entries(attrs))el.setAttribute(key,String(val));if(value!==undefined)el.textContent=value;return el;}
function growthChart(parent,row,metric){parent.replaceChildren();const series=row.series||[];if(series.length<2){parent.append(textNode('p','single-point',series.length?'只有 1 次数据':'还没有数据'));return;}
 const dated=series.filter(p=>p.captured_at&&Number.isFinite(Date.parse(p.captured_at))),valid=dated.filter(p=>typeof p.metrics?.[metric]==='number');
 if(valid.length<2){parent.append(textNode('p','single-point',valid.length===1?'只有 1 次有效'+xLabels[metric]+'数据，其余为未知':'这项数据未知，无法绘图'));return;}
 const width=640,height=240,left=62,right=18,top=24,bottom=48,lo=Math.min(...dated.map(p=>Date.parse(p.captured_at))),hi=Math.max(...dated.map(p=>Date.parse(p.captured_at)));if(hi===lo){parent.append(textNode('p','single-point','只有 1 次数据'));return;}
 const max=Math.max(1,...valid.map(p=>p.metrics[metric])),x=t=>left+(t-lo)/(hi-lo)*(width-left-right),y=v=>height-bottom-v/max*(height-top-bottom);
 const svg=svgElement('svg',{viewBox:`0 0 ${width} ${height}`,role:'img','aria-label':xLabels[metric]+'增长曲线','data-metric':metric});
 svg.append(svgElement('title',{},xLabels[metric]+'快照曲线，横轴为北京时间，未知值不连线'));
 for(const value of [0,max/2,max]){const yy=y(value);svg.append(svgElement('line',{x1:left,y1:yy,x2:width-right,y2:yy,class:'chart-grid'}),svgElement('text',{x:left-8,y:yy+4,'text-anchor':'end',class:'chart-label'},number(Math.round(value))));}
 let segment=[];const flush=()=>{if(segment.length>1)svg.append(svgElement('polyline',{points:segment.join(' '),fill:'none',class:'chart-line'}));segment=[];};
 for(const p of dated){const value=p.metrics?.[metric];if(typeof value!=='number'){flush();continue;}segment.push(`${x(Date.parse(p.captured_at))},${y(value)}`);}flush();
 for(const p of valid){const circle=svgElement('circle',{cx:x(Date.parse(p.captured_at)),cy:y(p.metrics[metric]),r:4,class:'chart-point'});circle.append(svgElement('title',{},time(p.captured_at)+' · '+xLabels[metric]+' '+number(p.metrics[metric])));svg.append(circle);}
 const label=t=>new Date(t).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false});svg.append(svgElement('text',{x:left,y:height-17,class:'chart-label'},label(lo)),svgElement('text',{x:width-right,y:height-17,'text-anchor':'end',class:'chart-label'},label(hi)));parent.append(svg,textNode('p','muted','累计'+xLabels[metric]+' · 北京时间 · '+valid.length+'次有效快照；未知值不补零、不连线。'));
}
function selectX(row){xSelected=row.id;document.querySelectorAll('.x-post-card').forEach(c=>c.classList.toggle('selected',c.dataset.postId===row.id));const panel=$('x-detail');panel.replaceChildren(textNode('h3','',row.type+' · 发帖（北京） '+time(row.created_at)),textNode('div','x-full-text',row.text||'正文未知'));if(row.type_history?.length){const label=p=>p.captured_at?new Date(p.captured_at).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false}):'时间未知';panel.append(textNode('p','type-history','Grok 曾标为：'+row.type_history.map(p=>p.type+'（'+label(p)+'）').join('、')));}link(panel,row.url,'打开 X 原帖');xSource(panel,row.from_radar);xMetrics(panel,row.metrics);panel.append(textNode('p','muted','最新抓取 '+time(row.captured_at)),textNode('h3','','增长曲线'));
 const tools=textNode('div','chart-controls'),label=textNode('label','','曲线指标 '),select=textNode('select');select.setAttribute('aria-label','曲线指标');for(const key of ['views','likes','bookmarks']){const opt=textNode('option','',xLabels[key]);opt.value=key;select.append(opt);}label.append(select);tools.append(label);panel.append(tools);const chart=textNode('div','growth-chart');panel.append(chart);growthChart(chart,row,'views');select.onchange=()=>growthChart(chart,row,select.value);
 const details=textNode('details','x-series'),summary=textNode('summary','','全部快照（'+(row.series||[]).length+'）'),table=textNode('table','snapshot-table'),thead=textNode('thead'),tr=textNode('tr');for(const name of ['抓取时间（北京）',...Object.values(xLabels)])tr.append(textNode('th','',name));thead.append(tr);const tbody=textNode('tbody');for(const point of row.series||[]){const line=textNode('tr');line.append(textNode('td','',time(point.captured_at)));for(const key of Object.keys(xLabels))line.append(textNode('td','',number(point.metrics?.[key])));tbody.append(line);}table.append(thead,tbody);details.append(summary,table);panel.append(details);
}
// Markdown 直接构造 DOM；所有原文均经 textContent，绝不把 Grok 文本送进 innerHTML。
function markdownInline(parent,value){
 const pattern=/(`+)([^`]*?)\1|\[([^\]\n]+)\]\(([^\s]*?)(?:\s+"[^"]*")?\)|\*\*([^*]+)\*\*|__([^_]+)__|\*([^*]+)\*/g;let offset=0,match;
 while((match=pattern.exec(value))){parent.append(textNode('span','',value.slice(offset,match.index)));if(match[1])parent.append(textNode('code','',match[2]));else if(match[3]){if(safeHttpUrl(match[4])){const a=textNode('a','',match[3]);a.href=match[4];a.target='_blank';a.rel='noopener noreferrer';parent.append(a);}else parent.append(textNode('span','',match[0]));}else parent.append(textNode(match[5]||match[6]?'strong':'em','',match[5]||match[6]||match[7]));offset=pattern.lastIndex;}
 parent.append(textNode('span','',value.slice(offset)));
}
function markdownReview(value){
 const box=textNode('div','grok-markdown'),lines=value.replace(/\r\n/g,'\n').split('\n');
 const cells=line=>line.trim().replace(/^\||\|$/g,'').split(/(?<!\\)\|/).map(v=>v.trim().replace(/\\\|/g,'|'));
 const rule=line=>line.includes('|')&&cells(line).every(v=>/^:?-{3,}:?$/.test(v));
 const block=line=>/^ {0,3}(?:#{1,6}\s|`{3,}|~{3,}|>\s?|[-+*]\s|\d+[.)]\s)/.test(line);
 for(let i=0;i<lines.length;){const line=lines[i];if(!line.trim()){i++;continue;}
  const fence=line.match(/^ {0,3}(`{3,}|~{3,})/);if(fence){let content=[],j=i+1;const end=new RegExp('^ {0,3}'+fence[1][0]+'{'+fence[1].length+',}\\s*$');while(j<lines.length&&!end.test(lines[j]))content.push(lines[j++]);const pre=textNode('pre'),code=textNode('code','',content.join('\n'));pre.append(code);box.append(pre);i=j<lines.length?j+1:j;continue;}
  const heading=line.match(/^ {0,3}(#{1,6})\s+(.+?)\s*#*\s*$/);if(heading){const h=textNode('h'+Math.min(6,heading[1].length+1));markdownInline(h,heading[2]);box.append(h);i++;continue;}
  if(i+1<lines.length&&line.includes('|')&&rule(lines[i+1])){const heads=cells(line),rows=[];let j=i+2;while(j<lines.length&&lines[j].trim()&&lines[j].includes('|'))rows.push(cells(lines[j++]));
   if(heads.length===cells(lines[i+1]).length&&rows.every(r=>r.length===heads.length)){const wrap=textNode('div','grok-table-scroll'),table=textNode('table'),thead=textNode('thead'),tr=textNode('tr'),tbody=textNode('tbody');for(const c of heads){const th=textNode('th');markdownInline(th,c);tr.append(th);}thead.append(tr);for(const row of rows){const tr=textNode('tr');for(const c of row){const td=textNode('td');markdownInline(td,c);tr.append(td);}tbody.append(tr);}table.append(thead,tbody);wrap.append(table);box.append(wrap);i=j;continue;}}
  const list=line.match(/^\s*([-+*]|\d+[.)])\s+(.*)$/);if(list){const ordered=/^\d/.test(list[1]),ul=textNode(ordered?'ol':'ul');if(ordered)ul.start=parseInt(list[1],10);let j=i;while(j<lines.length){const m=lines[j].match(/^\s*([-+*]|\d+[.)])\s+(.*)$/);if(!m||/^\d/.test(m[1])!==ordered)break;const li=textNode('li');markdownInline(li,m[2]);ul.append(li);j++;}box.append(ul);i=j;continue;}
  if(/^ {0,3}>/.test(line)){const quote=textNode('blockquote');let content=[];while(i<lines.length&&/^ {0,3}>/.test(lines[i]))content.push(lines[i++].replace(/^ {0,3}> ?/,''));quote.append(markdownReview(content.join('\n')));box.append(quote);continue;}
  if(/^\s*(?:-{3,}|\*{3,}|_{3,})\s*$/.test(line)){box.append(textNode('hr'));i++;continue;}
  const content=[line];i++;while(i<lines.length&&lines[i].trim()&&!block(lines[i])&&!(i+1<lines.length&&rule(lines[i+1]))){content.push(lines[i++]);}const p=textNode('p','grok-verbatim');markdownInline(p,content.join('\n'));box.append(p);
 }
 return box;
}
function renderReviews(data){const weekly=$('weekly-review');weekly.replaceChildren(textNode('h2','','每周复盘'));if(!data.weekly?.markdown&&!data.weekly?.summary_text?.length)weekly.append(textNode('p','review-empty','这周 Grok 复盘还没出'));else{weekly.append(textNode('p','muted',data.weekly.week+' · Grok 生成 '+time(data.weekly.generated_at)),data.weekly.markdown?markdownReview(data.weekly.markdown):textNode('div','grok-verbatim',data.weekly.summary_text.join('\n\n')));}for(const warning of data.weekly?.warnings||[])weekly.append(textNode('p','muted',warning));
 const daily=$('daily-review');daily.replaceChildren(textNode('h2','','Grok 每日复盘（帖子）'));if(!data.daily?.date)daily.append(textNode('p','review-empty','Grok 每日复盘还没开始'));else{daily.append(textNode('p','muted',(data.daily.is_today?'今天':'最近一份')+' · '+data.daily.date+' · Grok 生成 '+time(data.daily.generated_at)));if(!data.daily.summary?.length)daily.append(textNode('p','review-empty','这份 Grok 复盘未提供摘要'));for(const sentence of data.daily.summary||[])daily.append(textNode('p','grok-verbatim',sentence));for(const[key,title]of [['best','表现较好'],['worst','表现较弱']]){if(!data.daily[key]?.length)continue;daily.append(textNode('h3','',title));for(const entry of data.daily[key]){const line=textNode('p','grok-verbatim');link(line,'https://x.com/servasyy_ai/status/'+entry.id,entry.id);line.append(textNode('span','',' · '+entry.why));daily.append(line);}}for(const note of data.daily.notes||[])daily.append(textNode('p','grok-verbatim',note));}for(const warning of data.daily?.warnings||[])daily.append(textNode('p','muted',warning));
}
function renderStudioReview(data){
 const box=$('studio-grok-review');box.replaceChildren(textNode('h2','','Grok Bot 每日复盘（工作台）'));
 if(data?.status!=='ok'){box.append(textNode('p',data?.status==='error'?'studio-alert':'review-empty',data?.message||'工作台复盘还没开始，每天 09:30 运行'));return;}
 box.append(textNode('p','muted',(data.is_today?'今天':'最近一份')+' · '+data.date+' · 生成 '+time(data.generated_at)),textNode('p','muted','过去24小时 · '+time(data.window_start)+' 至 '+time(data.window_end)));
 const normal=textNode('details','studio-normal');normal.append(textNode('summary','','正常检查（'+data.checks.filter(c=>c.status==='正常').length+'）'));
 for(const c of data.checks){const row=textNode('section','studio-check '+(c.status==='正常'?'':'studio-alert'));row.append(textNode('h3','',c.status+' · '+c.title),textNode('p','',c.detail));if(c.files_24h!==undefined){row.append(textNode('p','','24小时内文件：'+c.files_24h+' · 最新产出：'+time(c.latest_at)));row.append(textNode('p','muted','最新文件：'+time(c.latest_file_at)+' · '+(c.latest_file||'无')));}
 if(c.N!==undefined)row.append(textNode('p','','名单人数 N：'+(c.N??'未知')));
 if(c.key==='radar_slots'){row.append(textNode('p','','24小时内轮次：'+c.rounds_24h+' · 今天轮次：'+c.today_rounds+' · 漏轮：'+c.missing_slots.length));const fold=textNode('details');fold.append(textNode('summary','','查看轮次与漏轮时点'));for(const slot of c.missing_slots)fold.append(textNode('p','','缺 · '+time(slot)));for(const round of c.rounds)fold.append(textNode('p','',time(round.slot)+' · 覆盖 '+round.coverage_count+'/'+(round.N??'未知')+' · '+(round.complete?'完整':'不完整')+' · '+(round.on_time?'按时':'迟到')+' · '+round.file));row.append(fold);}
 if(c.bad_files!==undefined){row.append(textNode('p','','坏文件：'+c.bad_files+' · 辅助文件：'+c.auxiliary_files));for(const e of c.errors||[])row.append(textNode('p','',e.file+' · '+e.reason));}
 if(c.key==='events'){row.append(textNode('p','','判定不一致：'+(c.mismatch_count??'未知')+' 条（最多展示10条）'));for(const [key,label]of [['lowfan_burst','低粉爆款'],['new_fast','新帖起势'],['hourly_delta','相邻增速'],['upgrade_100k','十万升级']])row.append(textNode('p','',label+' · 复算 '+(c.computed[key]??'未复算')+' / Grok '+(c.grok[key]??'未提供')));for(const m of c.mismatches)row.append(textNode('p','',m.id+' · '+m.event+' · 浏览 '+number(m.views)+' / 赞 '+number(m.likes)+' / 粉丝 '+number(m.followers)+' · 复算'+(m.computed?'命中':'未命中')+' / Grok'+(m.grok?'命中':'未命中')));}
 if(c.modified_in_window!==undefined)row.append(textNode('p','','窗口内新增/更新候选：'+(c.modified_in_window??'未知')+' · 严格新增：'+(c.new_count??'未提供')));
 if(c.status==='正常')normal.append(row);else box.append(row);}box.append(normal);
 const usage=textNode('details');usage.append(textNode('summary','','额度用量（原字段）'),textNode('pre','studio-facts',typeof data.usage==='string'?data.usage:JSON.stringify(data.usage,null,2)));box.append(usage,textNode('h3','','Pi 结论'),textNode('p','grok-verbatim studio-judgment',data.judgment),textNode('h3','','可直接转给 Grok 的话'),textNode('p','grok-verbatim studio-to-grok',data.to_grok));
 const button=textNode('button','studio-copy','复制转给 Grok 的话'),status=textNode('span','muted');status.setAttribute('role','status');button.type='button';button.disabled=!data.finalized;
 button.onclick=async()=>{try{await navigator.clipboard.writeText(data.to_grok);status.textContent='已复制';}catch(_){status.textContent='复制失败，请选中文字复制';}};box.append(button,status);
}
function renderGrok(data){xRows=[...(data.posts||[]),...(data.replies||[]),...(data.other_posts||[])];$('total').textContent=(data.total??0)+' 条 X 帖子';const week=data.week||{},box=$('x-week');box.replaceChildren();for(const kind of ['主帖','长文','引用','回复'])box.append(textNode('span','x-stat',kind+' '+(week.counts?.[kind]??'未知')));if(week.other_count)box.append(textNode('span','x-stat','其他 '+week.other_count));box.append(textNode('span','muted','近7天抓取 '+(week.captured_days??0)+'/7 天'+(week.missing_days?'（有缺抓）':'')));box.append(textNode('strong','x-stat','合计浏览 '+number(week.views)));if(week.views_unknown_posts)box.append(textNode('span','muted',week.views_unknown_posts+'帖浏览未知'));$('x-notice').textContent=(week.notice||'')+' '+(data.notice||'');const days=$('capture-days');days.replaceChildren();for(const day of data.days||[])days.append(textNode('li',day.status==='ok'?'':'capture-missing',day.date+' · '+(day.status==='ok'?'已抓取':day.message||'Grok 当天没抓')));
 const list=$('x-posts-list');list.replaceChildren();if(!data.posts?.length)list.append(textNode('p','review-empty','还没有主帖、长文或引用数据；缺抓不当作 0。'));for(const row of data.posts||[])list.append(xCard(row));list.setAttribute('aria-busy','false');
 for(const[id,values,label]of [['x-replies',data.replies||[],'回复'],['x-other',data.other_posts||[],'其他类型']]){const fold=$(id),target=$(id+'-list');fold.querySelector('summary').textContent=label+'（'+values.length+'）';fold.open=false;target.replaceChildren();if(id==='x-other')fold.hidden=!values.length;fold.ontoggle=()=>{if(fold.open&&!target.childElementCount)for(const row of values)target.append(xCard(row));};}
 renderReviews(data);const selected=xRows.find(r=>r.id===xSelected)||data.posts?.[0];if(selected)selectX(selected);else $('x-detail').replaceChildren(textNode('p','review-empty','选择帖子查看增长曲线'));
}

async function load(){
 try{const data=await request('/api/publish/review');renderGrok(data.grok || {});renderStudioReview(data.studio_grok_review);$('legacy-total').textContent='（'+data.total+'条）';$('week-count').textContent=data.week_count;$('week-items').textContent=data.week_from_items;$('notice').textContent=data.notice+(data.warnings.length?' '+data.warnings.join('；'):'');
 const list=$('published-list');list.replaceChildren();
 if(!data.items.length)list.append(node('p','review-empty','还没有已发布记录。发布并登记后，数据会显示在这里。'));
 for(const row of data.items){const card=node('article','published-card');card.tabIndex=0;card.dataset.deliveryId=row.delivery_id;card.dataset.slug=row.slug;card.append(node('h2','',row.title),node('p','review-meta',(platforms[row.platform]||row.platform)+' · '+row.time_basis+' '+time(row.sort_at)));link(card,row.url,'打开发布链接');metrics(card,row.metrics);
  if(row.source_item){const source=node('div','review-source');source.append(node('span','','来源素材 '),sourceBadges(row.source_item));link(source,row.source_item.url,row.source_item.title);card.append(source);}
  card.onclick=e=>{if(!e.target.closest('a'))select(row);};card.onkeydown=e=>{if(e.key==='Enter'&&e.target===card){e.preventDefault();select(row);}};list.append(card);
 }list.setAttribute('aria-busy','false');
 const slug=new URLSearchParams(location.search).get('article');const chosen=data.items.find(r=>r.delivery_id===selected||r.slug===slug);if(chosen)select(chosen);
 }catch(error){$('x-posts-list').replaceChildren(node('p','error',error.message));$('x-posts-list').setAttribute('aria-busy','false');$('published-list').replaceChildren(node('p','error',error.message));$('published-list').setAttribute('aria-busy','false');}
}
$('sync').onclick=async()=>{
 const button=$('sync');button.disabled=true;$('sync-status').textContent='正在手动同步…';
 try{const status=await request('/api/x-official/status');if(!status.connected||!status.account?.id)throw Error('请先到「X 发布 / 账号设置」连接账号');const end=new Date(Date.now()-60000),start=new Date(end.getTime()-7*86400000);
 await request('/api/x-official/feedback-sync',{method:'POST',headers:{'Content-Type':'application/json','X-Article-Studio':'1'},body:JSON.stringify({account_id:status.account.id,mode:'timeline',post_kind:'original',start_time:start.toISOString(),end_time:end.toISOString()})});$('sync-status').textContent='同步完成';await load();
 }catch(error){$('sync-status').textContent='同步失败：'+error.message;}finally{button.disabled=false;}
};
load();
