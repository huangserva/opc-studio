function chatSourceURL(value){
  if(typeof value!=='string'||value.length>2048||/[\s\x00-\x1f\\]/.test(value))return null;
  try{
    if(value.startsWith('/')&&!value.startsWith('//')){
      const local=new URL(value,'http://localhost');
      return ['/knowledge.html','/events.html'].includes(local.pathname)?local.pathname+local.search+local.hash:null;
    }
    const url=new URL(value);
    return ['http:','https:'].includes(url.protocol)&&!url.username&&!url.password?url.href:null;
  }catch{return null;}
}
function chatPlainText(text){
  return escapeHTML(text).replace(/\*\*([^*\n]+)\*\*/g,'<strong>$1</strong>').replace(/`([^`\n]+)`/g,'<code>$1</code>').replace(/\n/g,'<br>');
}
function chatLinkedText(text){
  return String(text||'').split(/(\[[^\]\n]{1,240}\]\((?:https?:\/\/|\/)[^\s<>)]{1,2048}\))/g).map(part=>{
    const match=part.match(/^\[([^\]\n]+)\]\(((?:https?:\/\/|\/)[^\s<>)]*)\)$/);
    const url=match&&chatSourceURL(match[2]);
    return url?`<a class="chat-source-link" href="${escapeHTML(url)}" target="_blank" rel="noopener noreferrer">${escapeHTML(match[1])}</a>`:chatPlainText(part);
  }).join('');
}
function chatSourceLinks(sources){
  if(!Array.isArray(sources))return '';
  const seen=new Set(),links=[];
  for(const source of sources.slice(0,8)){
    const url=chatSourceURL(source?.url);if(!url||seen.has(url))continue;seen.add(url);
    links.push(`<a href="${escapeHTML(url)}" target="_blank" rel="noopener noreferrer" title="${escapeHTML(url)}">↗ ${escapeHTML(source.title||url)}</a>`);
  }
  return links.length?`<div class="assistant-source-links">${links.join('')}</div>`:'';
}
function chatReadSources(citations){
  if(!Array.isArray(citations))return '';
  const seen=new Set(),rows=[];
  for(const c of citations){
    if(!c||typeof c.id!=='string'||!/^[a-f0-9]{32}$/.test(c.id)||typeof c.hash!=='string'||!/^[a-f0-9]{64}$/.test(c.hash)||!Number.isInteger(c.start)||!Number.isInteger(c.end)||c.start<0||c.end<=c.start||typeof c.excerpt!=='string'||Array.from(c.excerpt).length!==c.end-c.start||c.end-c.start>4000)continue;
    const expected='/knowledge.html?'+new URLSearchParams({document:c.id,start:c.start,end:c.end,hash:c.hash});
    if(c.url!==expected||seen.has(expected))continue;seen.add(expected);
    const state=c.state==='stale'?'读取的是已选旧快照':c.state==='missing'?'读取的是已保存快照，当前来源不可用':'保留读取时的原文快照';
    const kind={wechat:'微信',wechat_links:'微信分享链接',mail:'邮件',projects:'项目',articles:'文案',notes:'资料'}[c.kind]||'资料';
    const readAt=c.read_at&&Number.isFinite(Date.parse(c.read_at))?new Date(c.read_at).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false}):'';
    rows.push(`<article class="assistant-read-source"><a href="${escapeHTML(expected)}" target="_blank" rel="noopener noreferrer">${escapeHTML(c.title||'来源资料')} ↗</a><small>原文第 ${c.start+1}–${c.end} 字 · ${escapeHTML(kind)} · ${escapeHTML(state)}</small><small title="${escapeHTML(c.hash)}">版本指纹：${escapeHTML(c.hash.slice(0,12))}${readAt?' · 读取于 '+escapeHTML(readAt)+'（北京时间）':''}</small><blockquote>${escapeHTML(c.excerpt)}</blockquote>${chatSourceLinks(c.source_url?[{url:c.source_url,title:'来源入口'}]:[])}</article>`);
  }
  return rows.length?`<details class="assistant-read-sources" data-read-sources><summary>本轮读取的资料 · ${rows.length} 个片段</summary><p>以下是工具实际读取的原文，不代表回答中的每项判断已验证。</p>${rows.join('')}</details>`:'';
}
