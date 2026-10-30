// 从 twitter-radar.js 的安全 DOM 渲染器复制；所有反馈和来源只从参数读取。
export const safeText = value => typeof value === 'string' ? value : '';
const isObject = value => value && typeof value === 'object' && !Array.isArray(value);
export function grokNode(tag, cls, value) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (value !== undefined) node.textContent = value;
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
// 本地 SVG 路径：不依赖外部图片或字体。图标名称同时用于悬停与辅助阅读。
const sourceIcons = {
  G: ['Grok Bot 来源', 'M5 4h14v11H5z M8 1v3 M16 1v3 M8 9h1 M15 9h1 M9 12h6 M2 7v5 M22 7v5 M9 15v5 M15 15v5'],
  A: ['AIsa 来源', 'M3 20 10 4h4l7 16 M6 14h12 M12 5v15'],
  V: ['视频榜来源', 'M3 5h18v14H3z M10 9l5 3-5 3z'],
  L: ['linux.do 社区来源', 'M3 4h18v13H9l-5 4v-4H3z M7 8l3 3-3 3 M13 14h4']
};
export function sourceBadges(item) {
  const badges = grokNode('span', 'source-badges');
  for (const source of item.sources || []) {
    const [label, path] = sourceIcons[source] || [source, null];
    const badge = grokNode('span', 'source-badge'); badge.dataset.source = source;
    badge.title = label; badge.setAttribute('aria-label', label); badge.setAttribute('role', 'img');
    if (!path) {badge.textContent = source; badges.append(badge); continue;}
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.setAttribute('viewBox', '0 0 24 24'); svg.setAttribute('width', '18'); svg.setAttribute('height', '18');
    svg.setAttribute('fill', 'none'); svg.setAttribute('stroke', 'currentColor'); svg.setAttribute('stroke-width', '1.7');
    svg.setAttribute('stroke-linecap', 'round'); svg.setAttribute('stroke-linejoin', 'round'); svg.setAttribute('aria-hidden', 'true');
    const shape = document.createElementNS('http://www.w3.org/2000/svg', 'path'); shape.setAttribute('d', path);
    svg.append(shape); badge.append(svg); badges.append(badge);
  }
  return badges;
}
function hotBadge(item) {return item.hot ? grokNode('span', 'item-hot', '火爆') : null;}
export function actionBar(item, {feedback = item.feedback, status = item.status, onAction = () => {}} = {}) {
  const bar = grokNode('div', 'item-actions');
  const url = originalUrl(item);
  if (url) appendSafeLink(bar, url, '打开原帖');
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
export function renderPostCard(item, options = {}) {
  const card = grokNode('article', 'item-card'); card.dataset.itemId = item.id; card.tabIndex = 0;
  card.setAttribute('aria-label', (item.author?.name || item.author?.handle || '') + '：' + item.title);
  const heading = grokNode('div', 'item-card-meta');
  heading.append(grokNode('span', 'item-tier', item.tier));
  if(item.hot)heading.append(hotBadge(item));
  heading.append(grokNode('span', 'item-author', item.author?.name || item.author?.handle || '未知作者'), sourceBadges(item));
  card.append(heading, grokNode('div', 'item-summary', item.title));
  const metrics = item.metrics || {};
  if(item.content_insufficient)card.append(grokNode('div','interest-warning','内容不足'));
  if(item.channel === 'interest') {
    card.append(grokNode('div','item-card-foot','相关度 '+(item.relevance ?? '未知')+' / 3 · '+(item.published_at ? new Date(item.published_at).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false}) : '发布时间未知')));
    card.append(usableLinks(item));
    if(item.content_complete === false)card.append(grokNode('div','interest-warning','内容不完整：'+(item.incomplete_reasons || []).join('；')));
  } else card.append(grokNode('div', 'item-card-foot', '赞 ' + (metrics.likes ?? '—') + ' · 收藏 ' + (metrics.bookmarks ?? '未知') + ' · 像你 ' + (item.fit_score ?? '—') + ' · 可做 ' + (item.doable ?? '—')));
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

export function renderPostDetail(item, options = {}) {
  const content = grokNode('article', 'item-detail'); content.dataset.itemId = item.id;
  const author = grokNode('button', 'detail-author', (item.author?.name || '未知作者') + (item.author?.handle ? ' @' + item.author.handle : ''));
  author.type = 'button'; author.addEventListener('click', () => options.onAuthor?.(item.author));
  const heading = grokNode('div', 'detail-heading'); heading.append(author, sourceBadges(item));
  content.append(heading, grokNode('div', 'detail-meta', '粉丝 ' + (item.author?.followers ?? '—') + ' · ' + (item.published_at ? new Date(item.published_at).toLocaleString('zh-CN', {timeZone:'Asia/Shanghai',hour12:false}) : '时间未知')));
  const meta = grokNode('div', 'detail-meta'); meta.append(grokNode('span', 'item-tier', item.tier || ''), grokNode('span', '', item.channel==='interest'?'相关度 '+(item.relevance ?? '未知')+' / 3':'方向分 ' + (item.fit_score ?? '—') + ' · 可做分 ' + (item.doable ?? '—'))); if(item.hot)meta.prepend(hotBadge(item)); content.append(meta);
  if(item.channel==='interest')content.append(grokNode('h3','','新在哪'),grokNode('div','analysis-text',item.what_new || item.title),usableLinks(item));
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
  const metrics = grokNode('div', 'detail-metrics');
  const names = {views:'浏览', likes:'赞', bookmarks:'收藏', retweets:'转发', replies:'回复', views_per_hour:'每小时浏览', likes_per_hour:'每小时赞', delta:'每小时增量', score:'来源热度', heat:'排序热度', hours_since_created:'发帖距采集小时'};
  for (const [key,value] of Object.entries(item.metrics || {})) metrics.append(grokNode('span', '', (names[key] || key) + ' ' + (value ?? '—')));
  content.append(metrics);
  for (const [key,label] of [['why','为什么爆'],['pattern','可复制写法'],['my_angle','我的切入角度'],['outline','提纲'],['doable_resource','能用的资源']]) {
    if (item[key]) content.append(grokNode('h3','',key==='why'&&item.channel==='interest'?'为什么值得收':label), grokNode('div','analysis-text',typeof item[key] === 'string' ? item[key] : JSON.stringify(item[key], null, 2)));
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
