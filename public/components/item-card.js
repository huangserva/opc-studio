// 从 twitter-radar.js 的安全 DOM 渲染器复制；所有反馈和来源只从参数读取。
// 只替换单个实体 token；解码结果始终送入 textContent，绝不解析整段 HTML。
export function decodeEntities(value) {
  if (typeof value !== 'string') return value;
  return value.replace(/&(?:#[xX][0-9a-fA-F]+|#[0-9]+|[A-Za-z][A-Za-z0-9]+);/g, token => new DOMParser().parseFromString(token, 'text/html').body.textContent);
}
export const safeText = value => typeof value === 'string' ? value : '';
const isObject = value => value && typeof value === 'object' && !Array.isArray(value);
export function grokNode(tag, cls, value) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (value !== undefined) node.textContent = typeof value === 'string' ? decodeEntities(value) : value;
  return node;
}
export function grokOriginalUrl(value) {
  return typeof value === 'string' && /^https:\/\/(?:x\.com|twitter\.com)\/[A-Za-z0-9_]{1,15}\/status\/[0-9]{1,25}$/.test(value) && !/\s/.test(value) ? value : null;
}
export function safeHttpUrl(value) {
  if (typeof value !== 'string' || /[\s\u0000-\u001f]/.test(value)) return null;
  try { const url = new URL(value); return ['http:', 'https:'].includes(url.protocol) && !url.username && !url.password ? url.href : null; } catch (_) { return null; }
}
export function originalUrl(item) {
  if (item.kind === 'tool' && item.sources?.includes('W')) return safeHttpUrl(item.url);
  if (item.channel === 'interest' && item.input_sources?.length && item.kind !== 'video') return safeHttpUrl(item.url);
  if ((item.sources || []).includes('L')) {
    const value = safeHttpUrl(item.url);
    if (!value) return null;
    const url = new URL(value);
    return url.origin === 'https://linux.do' && /^\/t\/(?:[^/]+\/)?[0-9]+\/?$/.test(url.pathname) && !url.search && !url.hash ? value : null;
  }
  if (item.kind !== 'video') return grokOriginalUrl(item.url);
  const value = safeHttpUrl(item.url);
  if (!value) return null;
  const url = new URL(value);
  return ['www.douyin.com', 'douyin.com', 'www.xiaohongshu.com', 'xiaohongshu.com', 'www.xhslink.com', 'xhslink.com', 'v.douyin.com'].includes(url.hostname) ? value : null;
}
function appendSafeLink(parent, value, label, original = false) {
  const url = original ? grokOriginalUrl(value) : safeHttpUrl(value);
  if (!url) return;
  const a = grokNode('a', '', label); a.href = url; a.target = '_blank'; a.rel = 'noopener noreferrer'; parent.append(a);
}
// 品牌与渠道元信息只接受后端唯一登记表产物；前端不从 ID 推导平台。
export function sourceBadges(item) {
  const badges = grokNode('span', 'source-badges');
  const platform = item.platform_info || {key:'web', display_name:'网页', icon:'/icons/platforms/web.svg'};
  const details = item.channel_details || [];
  const time = item.fetched_at ? new Date(item.fetched_at).toLocaleTimeString('zh-CN', {timeZone:'Asia/Shanghai', hour:'2-digit',minute:'2-digit',hour12:false}) : '未知';
  const channels = details.map(row=>row.display_name+(row.note ? ' · '+row.note : '')).join(' + ') || '渠道未知';
  badges.title = '平台：'+platform.display_name+'｜渠道：'+channels+'｜采集于 '+time;
  badges.setAttribute('aria-label', badges.title);
  const logo = grokNode('img','platform-logo');
  logo.src = /^\/icons\/platforms\/[a-z0-9-]+\.svg$/.test(platform.icon) ? platform.icon : '/icons/platforms/web.svg';
  logo.alt = platform.display_name; logo.title = platform.display_name; logo.width=18; logo.height=18;
  badges.append(logo, grokNode('span','collection-channels',details.map(row=>row.short_name || row.display_name).join(' + ') || '渠道未知'));
  badges.dataset.platform = platform.key;
  return badges;
}
function officialBadge(item) {if(!item.official?.is_official)return null;const badge=grokNode('span','item-official','官方');badge.title=item.official.reason+' · '+item.official.eligibility;return badge;}
function hotBadge(item) {return item.hot ? grokNode('span', 'item-hot', '火爆') : null;}
function interestBadges(item) {
  const raw=item.interest_matches || item.analysis?.interest_matches;
  const matches=Array.isArray(raw)?raw.filter(m=>m && typeof m.name==='string' && typeof m.word==='string').slice(0,100):[];
  if(!matches.length)return null;
  const row=grokNode('div','interest-matches');
  const names=[...new Set(matches.map(m=>m.name))];
  const badge=grokNode('span','interest-direction',names[0]+(names.length>1?' · 另 '+(names.length-1)+' 项':''));
  badge.title=matches.map(m=>m.basis==='original_interest'?'原兴趣候选保留，尚未命中当前方向':m.name+'｜命中：'+m.word+'｜位置：'+({topic:'来源方向',title:'标题',full_text:'正文',expanded_text:'补充正文'}[m.field] || m.field)).join('\n');
  badge.setAttribute('aria-label',badge.title);row.append(badge);
  if(item.discovery_overlap)row.append(grokNode('span','interest-overlap','发现栏也有'));
  return row;
}
export function actionBar(item, {feedback = item.feedback, status = item.status, onAction = () => {}} = {}) {
  const bar = grokNode('div', 'item-actions');
  const url = originalUrl(item);
  if (url) appendSafeLink(bar, url, item.kind === 'tool' ? '打开工具' : '打开原帖');
  const actions = item.channel === 'interest' ? [['keep','存进素材库'],['accurate','准'],['inaccurate','不准']] : [['accurate','准'], ['inaccurate','不准'], ['keep','存'], ['use','拿去写']];
  for (const [action, label] of actions) {
    const button = grokNode('button', 'item-action', label); button.type = 'button'; button.dataset.action = action;
    const pressed = action === feedback || action === 'keep' && status === 'kept' || action === 'use' && status === 'used';
    button.setAttribute('aria-pressed', String(pressed));
    button.addEventListener('click', event => {event.stopPropagation(); onAction(action === feedback ? 'clear' : action, item, event);});
    bar.append(button);
  }
  bar.append(grokNode('span', 'action-notice')); return bar;
}
// 三栏与详情共用格式化；0是有效值，空值、布尔、非有限数不显示。
export function metricNumber(value) {
  if(value===null || value===undefined || !['number','string'].includes(typeof value) || typeof value==='string' && !value.trim())return null;
  const number=Number(value);return Number.isFinite(number)&&number>=0?number:null;
}
export function formatMetric(value) {
  const n=metricNumber(value);if(n===null)return '';
  const scale=n>=10000?10000:n>=1000?1000:1;
  return (scale===1?String(n):(n/scale).toFixed(1))+(scale===10000?'万':scale===1000?'千':'');
}
function relativeTime(value) {
  if(!value)return '';const ms=Date.parse(value);if(!Number.isFinite(ms))return '';
  const minutes=Math.max(0,Math.floor((Date.now()-ms)/60000));
  return minutes<1?'刚刚':minutes<60?minutes+' 分钟前':minutes<1440?Math.floor(minutes/60)+' 小时前':Math.floor(minutes/1440)+' 天前';
}
function metricSpan(key,label,value) {
  const display=formatMetric(value);if(!display)return null;
  const span=grokNode('span','item-metric',label+' '+display);span.dataset.metric=key;span.title=label+' '+Number(value).toLocaleString('zh-CN');return span;
}
export function metricStrip(item,detail=false) {
  const row=grokNode('div',detail?'detail-metrics':'item-card-metrics');row.setAttribute('aria-label',detail?'帖子数据':'互动数据');
  const m=item.metrics || {},names=[['views','浏览'],['likes','赞'],['bookmarks','收藏'],['retweets','转发'],['replies','回复'],...(detail?[['quotes','引用']]:[])];
  for(const [key,label] of names){const span=metricSpan(key,label,key==='quotes'?(m.quotes??m.quote_count??m.quoted):m[key]);if(span)row.append(span);}
  if(item.reply_change!==undefined && item.reply_change!==null && String(item.reply_change).trim())row.append(grokNode('span','item-reply-change','回复变化 '+item.reply_change));
  const rate=metricSpan('views_per_hour',detail?'每小时浏览':'↑ 每小时',m.views_per_hour);if(rate){rate.classList.add('item-rate');row.append(rate);}
  return row.childNodes.length?row:null;
}
function displaySource(item){
  const badges=sourceBadges(item);
  if(!item.channel_details?.length)badges.querySelector('.collection-channels')?.remove();
  return badges;
}
function scoreLine(item) {
  const parts=[];if(item.topic)parts.push(item.topic);
  if(item.channel==='interest'&&metricNumber(item.relevance)!==null)parts.push('相关度 '+formatMetric(item.relevance)+'/3');
  if(metricNumber(item.fit_score)!==null)parts.push('像你 '+formatMetric(item.fit_score));
  if(metricNumber(item.doable)!==null)parts.push('可做 '+formatMetric(item.doable));
  return parts.join(' · ');
}
export function renderPostCard(item, options = {}) {
  const card = grokNode('article', 'item-card'); card.dataset.itemId = item.id; card.tabIndex = 0;
  card.setAttribute('aria-label', (item.author?.name || item.author?.handle || '') + '：' + item.title);
  const heading = grokNode('div', 'item-card-meta');
  const tier=item.hot?'火爆':['爆款','低粉爆款'].includes(item.tier)?'爆款':item.tier==='起势'?'起势':'';
  if(tier)heading.append(grokNode('span','item-tier',tier));heading.append(displaySource(item));
  const authorName=grokNode('span','item-author');authorName.append(grokNode('span','author-label',item.author?.name || item.author?.handle || ''));
  if(item.official)authorName.title='官方识别依据：'+item.official.reason+(item.official.eligibility?' · '+item.official.eligibility:'');
  if(item.official?.is_official)authorName.append(officialBadge(item));heading.append(authorName);
  const followers=metricSpan('followers','粉丝',item.author?.followers??item.metrics?.followers??item.official?.followers);if(followers)heading.append(followers);
  const relative=relativeTime(item.published_at);if(relative){const time=grokNode('time','item-relative-time',relative);time.dateTime=item.published_at;time.title=new Date(item.published_at).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false});heading.append(time);}
  card.append(heading,grokNode('div','item-summary',item.title));
  const metrics=metricStrip(item);if(metrics)card.append(metrics);
  const interests=interestBadges(item),foot=grokNode('div','item-card-foot');
  if(interests)foot.append(interests);
  const score=scoreLine({...item,topic:interests?'':item.topic});if(score)foot.append(grokNode('span','',score));if(foot.childNodes.length)card.append(foot);
  // 工具资源和来源警示保留，位于统一四行之后。
  if(item.content_insufficient)card.append(grokNode('div','interest-warning','内容不足'));
  if(item.channel==='interest'){
    const links=usableLinks(item);if(links.childNodes.length)card.append(links);
    if(item.kind==='tool'){const toolMeta=grokNode('div','item-card-foot');if(metricNumber(item.share_count)!==null)toolMeta.append(grokNode('span','','分享 '+formatMetric(item.share_count)+' 次'));if(item.in_tools)toolMeta.append(grokNode('span','tool-collection','已进工具'));if(toolMeta.childNodes.length)card.append(toolMeta);if(item.verification?.state!=='checked')card.append(grokNode('div','interest-warning','链接未核对'));}
    if(item.content_complete===false)card.append(grokNode('div','interest-warning','内容不完整：'+(item.incomplete_reasons||[]).join('；')));
  }
  if (options.actions !== false) card.append(actionBar(item, options));
  card.addEventListener('click', event => {if (!event.target.closest('button,a')) options.onSelect?.(item);});
  card.addEventListener('keydown', event => {if (event.key === 'Enter' && event.target === card) {event.preventDefault(); options.onSelect?.(item);}});
  return card;
}

function usableLinks(item) {
  const box=grokNode('div','interest-resources');
  const names={weights:'权重',api:'API',repo:'仓库',tutorial:'教程',paper:'论文',other:'其他'};
  for(const entry of item.usable || []) {
    const label=(names[entry.type] || '其他')+'：'+(entry.note || '查看资源');
    if(safeHttpUrl(entry.url))appendSafeLink(box,entry.url,label);
    else box.append(grokNode('span','',label+'（链接不可用）'));
  }
  return box;
}
export function renderRichContent(parent, post, depth = 0) {
  if (!post || depth > 3) return;
  if (typeof post === 'string') { parent.append(grokNode('div', 'grok-rich-text', post)); return; }
  if (!isObject(post)) return;
  if (post.full_text && post.full_text !== post.text) parent.append(grokNode('div', 'grok-full-text', safeText(post.full_text)));
  if (post.article) {
    const article = grokNode('section', 'grok-article');
    article.append(grokNode('strong', '', 'X 长文'));
    if (typeof post.article === 'string') article.append(grokNode('div', 'grok-rich-text', post.article));
    else if (isObject(post.article)) {
      article.append(grokNode('h4', '', safeText(post.article.title)), grokNode('div', 'grok-rich-text', safeText(post.article.full_text) || safeText(post.article.text) || safeText(post.article.body) || safeText(post.article.content)));
      appendSafeLink(article, post.article.url, '打开长文');
      renderRichContent(article, { media: post.article.media }, depth + 1);
    }
    parent.append(article);
  }
  if (Array.isArray(post.thread)) post.thread.forEach((part, i) => {
    const item = grokNode('section', 'grok-thread'); item.append(grokNode('strong', '', '串推 ' + (i + 1)));
    if (typeof part === 'string') item.append(grokNode('div', 'grok-rich-text', part));
    else if (isObject(part)) {
      item.append(grokNode('div', 'grok-meta', safeText(part.author) + ' @' + safeText(part.handle)), grokNode('div', 'grok-rich-text', safeText(part.full_text) || safeText(part.text)));
      appendSafeLink(item, part.url, '打开这条串推', true); renderRichContent(item, {...part, full_text: null, text: null}, depth + 1);
    }
    parent.append(item);
  });
  if (post.quoted) {
    const quote = grokNode('blockquote', 'grok-quote'); quote.append(grokNode('strong', '', '被引用帖'));
    if (typeof post.quoted === 'string') quote.append(grokNode('div', 'grok-rich-text', post.quoted));
    else if (isObject(post.quoted)) {
      quote.append(grokNode('div', 'grok-meta', safeText(post.quoted.author) + ' @' + safeText(post.quoted.handle)), grokNode('div', 'grok-rich-text', safeText(post.quoted.full_text) || safeText(post.quoted.text)));
      appendSafeLink(quote, post.quoted.url, '打开被引用帖', true); renderRichContent(quote, {...post.quoted, full_text: null, text: null}, depth + 1);
    }
    parent.append(quote);
  }
  const media = Array.isArray(post.media) ? post.media : (post.media ? [post.media] : []);
  for (const entry of media) {
    const item = typeof entry === 'string' ? {url: entry, type: 'photo'} : entry;
    if (!isObject(item)) continue;
    const video = /video|gif/i.test(safeText(item.type));
    const src = safeHttpUrl(video ? (item.poster || item.preview_image_url || item.thumbnail_url || item.cover) : (item.url || item.media_url_https || item.media_url));
    const figure = grokNode('figure', 'grok-media');
    if (src) { const img = grokNode('img'); img.src = src; img.alt = safeText(item.alt) || (video ? '视频封面' : '推文图片'); img.loading = 'lazy'; img.referrerPolicy = 'no-referrer'; img.addEventListener('error', () => {img.hidden = true; figure.append(grokNode('div', 'media-unavailable', video ? '封面暂不可用，请播放原帖' : '图片暂不可用，请打开原帖'));}, {once:true}); figure.append(img); }
    if (video) appendSafeLink(figure, post.url, '播放原帖', post.kind !== 'video');
    else appendSafeLink(figure, item.url || item.media_url_https || item.media_url, '查看图片');
    parent.append(figure);
  }
  if (post.card) {
    const card = grokNode('section', 'grok-external-card');
    if (typeof post.card === 'string') card.append(grokNode('div', 'grok-rich-text', post.card));
    else if (isObject(post.card)) {
      card.append(grokNode('strong', '', safeText(post.card.title)), grokNode('div', 'grok-rich-text', safeText(post.card.description) || safeText(post.card.text)));
      appendSafeLink(card, post.card.url, '打开外链');
      const image = safeHttpUrl(post.card.image || post.card.image_url);
      if (image) {const img = grokNode('img'); img.src = image; img.alt = safeText(post.card.title); img.loading = 'lazy'; img.referrerPolicy = 'no-referrer'; card.append(img);}
    }
    parent.append(card);
  }
}

function analysisPresent(value) {
  if (value == null || typeof value === 'string' && !value.trim()) return false;
  if (Array.isArray(value)) return value.some(analysisPresent);
  if (isObject(value)) return Object.values(value).some(analysisPresent);
  return true;
}
function analysisSummary(value, depth=0) {
  if (depth > 5) return '…';
  if (Array.isArray(value)) return value.slice(0,20).map(v=>analysisSummary(v,depth+1)).filter(Boolean).join('；');
  if (isObject(value)) return Object.entries(value).slice(0,20).map(([k,v])=>k+'：'+analysisSummary(v,depth+1)).join('；');
  return value == null ? '' : String(value).slice(0,2000);
}
export function renderAnalysis(value, ordered=false, depth=0) {
  if (!analysisPresent(value)) return null;
  if (depth >= 2 && typeof value === 'object') return grokNode('span','analysis-summary',analysisSummary(value));
  if (Array.isArray(value)) {
    const list=grokNode(ordered?'ol':'ul','analysis-list');
    for (const entry of value) {const child=renderAnalysis(entry,false,depth+1); if(child){const li=grokNode('li');li.append(child);list.append(li);}}
    return list.childNodes.length ? list : null;
  }
  if (isObject(value)) {
    const box=grokNode('div','analysis-entry');
    // 可读字段优先级：title/name → text/description/note/content → url；其余按原键逐项兜底。
    const consumed=new Set();
    for (const keys of [['title','name'],['text','description','note','content']]) {
      const key=keys.find(k=>analysisPresent(value[k]));
      if(key) {const child=renderAnalysis(value[key],false,depth+1);if(child)box.append(child);consumed.add(key);}
    }
    if (analysisPresent(value.url)) {
      const url=safeHttpUrl(value.url);
      if(url)appendSafeLink(box,url,value.url);else box.append(grokNode('span','analysis-url',analysisSummary(value.url)));
      consumed.add('url');
    }
    for(const [key,entry] of Object.entries(value)) {
      if(consumed.has(key) || !analysisPresent(entry))continue;
      const row=grokNode('div','analysis-property');row.append(grokNode('span','',key+'：'));
      const child=renderAnalysis(entry,false,depth+1);if(child)row.append(child);box.append(row);
    }
    return box.childNodes.length ? box : null;
  }
  return grokNode('div','analysis-text',String(value));
}

export function renderPostDetail(item, options = {}) {
  const content = grokNode('article', 'item-detail'); content.dataset.itemId = item.id;
  const author = grokNode('button', 'detail-author', (item.author?.name || '') + (item.author?.handle ? ' @' + item.author.handle : ''));
  if(item.official)author.title='官方识别依据：'+item.official.reason+(item.official.eligibility?' · '+item.official.eligibility:'');
  if(item.official?.is_official)author.append(officialBadge(item));
  author.type = 'button'; author.addEventListener('click', () => options.onAuthor?.(item.author));
  const heading = grokNode('div', 'detail-heading');if(author.textContent.trim())heading.append(author);heading.append(displaySource(item));
  if(options.showTitle&&item.title){const title=grokNode('h2','detail-title',item.title);title.title=item.title;content.append(title);}
  content.append(heading);
  const authorMeta=grokNode('div','detail-meta');const followers=metricSpan('followers','粉丝',item.author?.followers??item.metrics?.followers??item.official?.followers);if(followers)authorMeta.append(followers);
  if(item.published_at&&Number.isFinite(Date.parse(item.published_at)))authorMeta.append(grokNode('time','',new Date(item.published_at).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false})));
  if(authorMeta.childNodes.length)content.append(authorMeta);
  const metrics=metricStrip(item,true);if(metrics)content.append(metrics);
  const meta=grokNode('div','detail-meta');meta.append(grokNode('span','item-tier',['兴趣','兴趣候选','其他',''].includes(item.tier||'')?'普通':item.tier));const score=scoreLine(item);if(score)meta.append(grokNode('span','',score));content.append(meta);
  const interests=interestBadges(item);if(interests)content.append(interests);
  if(item.channel==='interest')content.append(grokNode('h3','','新在哪'),grokNode('div','analysis-text',item.what_new || item.title),usableLinks(item));
  if(item.kind==='tool'){content.append(grokNode('p','tool-collection',item.in_tools?'已进工具 · 兴趣方向待确认':'兴趣候选 · 待确认'));content.append(grokNode('p','detail-meta',item.verification?.state==='checked'?'链接已核对 · HTTP '+item.verification.http_status:'链接未核对'));}
  const reasons = item.incomplete_reasons || (item.content_complete === false ? ['来源标记内容不完整'] : []);
  if (reasons.length) {
    const warning = grokNode('aside', 'incomplete-warning');
    warning.append(grokNode('strong', '', item.channel==='interest'?'内容不完整':'原文不完整，点开原帖看全文'), grokNode('div', '', reasons.join('；'))); content.append(warning);
  }
  content.append(grokNode('div', 'full-text', safeText(item.full_text) || safeText(item.text)));
  // 统一输入有 rich_sources；直接传 merged_posts 也支持 grok/aisa，无下划线字段依赖。
  const supplements = item.rich_sources || [item.aisa, item.grok].filter(Boolean);
  const seen = new Set();
  for (const source of supplements.length ? supplements : [item]) {
    const rich = Object.fromEntries(['article','thread','quoted','media','card'].filter(k => source[k]).map(k => [k,source[k]]));
    const signature = JSON.stringify(rich);
    if (seen.has(signature)) continue;
    seen.add(signature); renderRichContent(content, {...rich, url: originalUrl({...item, url:source.url || item.url}), kind:item.kind});
  }
  for (const [key,label] of [['why','为什么爆'],['pattern','可复制写法'],['my_angle','我的切入角度'],['outline','提纲'],['doable_resource','能用的资源']]) {
    const rendered = renderAnalysis(item[key], key === 'outline');
    if (rendered) content.append(grokNode('h3','',key==='why'&&item.channel==='interest'?'为什么值得收':label), rendered);
  }
  content.append(actionBar(item,options)); return content;
}
function richMarkdown(source) {
  if (typeof source === 'string') return source;
  if (!source || typeof source !== 'object') return '';
  const lines = [source.title, source.full_text || source.text || source.body || source.content];
  if (source.article) lines.push(richMarkdown(source.article));
  if (source.thread) lines.push(...source.thread.map(richMarkdown));
  if (source.quoted) lines.push('引用：' + richMarkdown(source.quoted));
  return lines.filter(Boolean).join('\n\n');
}
export function itemMarkdown(item) {
  const display = v => typeof v === 'string' ? v : JSON.stringify(v || '', null, 2);
  return '# ' + item.title + '\n\n' + item.full_text + '\n\n' + (item.rich_sources || []).map(source => richMarkdown({...source, text:null, full_text:null})).filter(Boolean).join('\n\n') + '\n\n原帖：' + (originalUrl(item) || '') + '\n\n数据：' + display(item.metrics) + '\n\n为什么爆\n' + display(item.why) + '\n\n切入角度\n' + display(item.my_angle) + '\n\n提纲\n' + display(item.outline) + '\n\n资源\n' + display(item.doable_resource);
}
