import {grokNode as node, renderPostCard, renderPostDetail, originalUrl, actionBar} from './item-card.js';

// 每个实例独立持有条目、反馈和请求序号；不读取任何页面全局状态。
export function createListReader({list, reader, layout, divider, loadDetail, onAction, onAuthor, renderDetail = renderPostDetail, draftFromToday = false}) {
  let items = [], selected = -1, detail = null, request = 0, queued = Promise.resolve(), destroyed = false;
  const drafts = new Map(), polling = new Map();
  async function draftRequest(id, method='GET', body) {
    const response = await fetch('/api/items/'+encodeURIComponent(id)+(method==='GET'?'/draft-status':'/draft'), {method,cache:'no-store',headers:method==='GET'?{}:{'Content-Type':'application/json','X-Article-Studio':'1'},...(body?{body:JSON.stringify(body)}:{})});
    const data=await response.json(); if(!response.ok)throw Error(data.error||'建稿失败');return data;
  }
  function showDraft(id, state) {
    if(destroyed)return; drafts.set(id,state);
    const label=state.status==='pending'?'AI 正在起草 · '+Math.round(state.elapsed||0)+' 秒':state.notice||'';
    const card=list.querySelector('[data-item-id="'+CSS.escape(id)+'"]');
    if(card){let status=card.querySelector('.draft-state');if(!status){status=node('div','draft-state');status.style='font-size:12px;line-height:1.6;color:#53694b;margin-top:8px';card.append(status);}status.textContent=label;status.setAttribute('role','status');}
    if(detail?.id===id){const notice=reader.querySelector('.action-notice');if(notice){notice.textContent=label;notice.setAttribute('role','status');}}
  }
  function watch(id, state) {
    showDraft(id,state);clearTimeout(polling.get(id));
    if(state.status==='pending')polling.set(id,setTimeout(async()=>{try{watch(id,await draftRequest(id));}catch(error){showDraft(id,{notice:'起草状态读取失败：'+error.message});}},700));
  }
  async function restoreDraft(item) {
    if(item.status!=='used')return;try{const state=await draftRequest(item.id);if(state.slug)watch(item.id,state);}catch(_){}
  }
  const select = async (item, {focus = false} = {}) => {
    const serial = ++request;
    selected = items.findIndex(p => p.id === item.id);
    for (const card of list.querySelectorAll('.item-card')) card.classList.toggle('selected', card.dataset.itemId === item.id);
    reader.replaceChildren(node('div','reader-loading','正在读取全文…')); layout.classList.add('reading');
    try {
      const next = await loadDetail(item.id);
      if (serial !== request) return;
      detail = next; draw(); restoreDraft(next);
      if (focus) reader.focus();
    } catch (error) {if (serial === request) reader.replaceChildren(node('p','error','全文加载失败：' + error.message));}
  };
  function draw() {
    const back = node('button','reader-back','返回列表'); back.type = 'button';
    back.onclick = () => {layout.classList.remove('reading'); list.querySelector('.selected')?.focus();};
    reader.replaceChildren(back, renderDetail(detail, {onAction:act, onAuthor}));
    if(drafts.has(detail.id))showDraft(detail.id,drafts.get(detail.id));
  }
  function act(action, item, event) {
    if(action==='use') {
      // 必须同步拿窗口句柄；POST 成功后再跳转到具体作品。
      const opened=window.open('about:blank','_blank');if(opened)opened.opener=null;
      const force=!!event?.shiftKey;
      const operation=async()=>{
        try{
          const state=await draftRequest(item.id,'POST',{force,from_today:draftFromToday});
          if(opened)opened.location.replace('/index.html?article='+encodeURIComponent(state.slug));
          item.status='used';item.used_work_id=state.slug;
          const found=items.find(p=>p.id===item.id);if(found)Object.assign(found,{status:'used',used_work_id:state.slug});
          if(detail?.id===item.id){Object.assign(detail,{status:'used',used_work_id:state.slug});draw();}
          watch(item.id,state);
          if(!opened){const notice=reader.querySelector('.action-notice');if(notice){notice.append(document.createTextNode(' · '));const a=node('a','','打开已建底稿');a.href='/index.html?article='+encodeURIComponent(state.slug);a.target='_blank';a.rel='noopener noreferrer';notice.append(a);}}
        }catch(error){if(opened)opened.close();const notice=reader.querySelector('.action-notice');if(notice)notice.textContent='建稿失败：'+error.message;}
      };
      return operation();
    }
    queued = queued.catch(() => {}).then(async () => {
      for (const b of reader.querySelectorAll('.item-action')) b.disabled = true;
      try {
        const record = await onAction(item.id, action, item);
        const found = items.find(p => p.id === item.id); if (found) Object.assign(found, record);
        const card = list.querySelector('[data-item-id="'+CSS.escape(item.id)+'"]');
        if(card && item.channel==='interest') {Object.assign(item,record);card.querySelector('.item-actions')?.replaceWith(actionBar(item,{onAction:act}));card.querySelector('.action-notice').textContent='已保存';}
        if (detail?.id === item.id) {Object.assign(detail, record); draw(); reader.querySelector('.action-notice').textContent = record?.notice || '已保存';}
      } catch (error) {
        const notice = reader.querySelector('.action-notice') || list.querySelector('[data-item-id="'+CSS.escape(item.id)+'"] .action-notice'); if (notice) notice.textContent = '操作失败：' + error.message;
      } finally {for (const b of reader.querySelectorAll('.item-action')) b.disabled = false;}
    });
    return queued;
  }
  const keydown = event => {
    if (event.ctrlKey || event.metaKey || event.altKey || event.target.closest('input,textarea,select,[contenteditable=true]')) return;
    const key = event.key.toLowerCase();
    if (key === 'j' || key === 'k') {
      event.preventDefault(); const index = Math.max(0, Math.min(items.length - 1, selected + (key === 'j' ? 1 : -1)));
      if (items[index]) {select(items[index]); list.querySelector('[data-item-id="' + CSS.escape(items[index].id) + '"]')?.scrollIntoView({block:'nearest'});}
    } else if (key === 'o' && detail && originalUrl(detail)) {event.preventDefault(); window.open(originalUrl(detail), '_blank','noopener,noreferrer');}
    else if (key === 'enter' && detail) {event.preventDefault(); reader.focus();}
  };
  document.addEventListener('keydown', keydown);
  const resize = clientX => {const rect=layout.getBoundingClientRect(); layout.style.setProperty('--list-width', Math.max(300, Math.min(rect.width-360, clientX-rect.left))+'px');};
  const pointerMove = event => resize(event.clientX);
  const pointerUp = () => {document.removeEventListener('pointermove',pointerMove);document.removeEventListener('pointerup',pointerUp);};
  divider?.addEventListener('pointerdown',event=>{event.preventDefault();document.addEventListener('pointermove',pointerMove);document.addEventListener('pointerup',pointerUp);});
  divider?.addEventListener('keydown', event => {if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') {event.preventDefault(); resize(divider.getBoundingClientRect().left + (event.key === 'ArrowLeft' ? -20 : 20));}});
  return {
    select,
    setItems(next) {items = next; for(const item of next)if(item.status==='used')setTimeout(()=>restoreDraft(item),0); selected = -1; detail = null; ++request; layout.classList.remove('reading'); reader.replaceChildren(node('div','reader-empty','点左侧条目查看全文'));},
    card(item) {const card=renderPostCard(item,{actions:item.channel==='interest',onSelect:select,onAction:act});if(drafts.has(item.id))setTimeout(()=>showDraft(item.id,drafts.get(item.id)),0);return card;},
    showAuthor(authorItems, author) {
      ++request;
      const back = node('button','','返回全文');back.onclick=()=>{if(detail)draw();};
      reader.replaceChildren(back,node('h2','', (author.name || author.handle) + ' · 近 30 天'));
      if (!authorItems.length) reader.append(node('p','','近 30 天没有其他条目'));
      for (const item of authorItems) reader.append(renderPostCard(item,{actions:false,onSelect:select}));
    },
    destroy() {destroyed=true;for(const timer of polling.values())clearTimeout(timer);document.removeEventListener('keydown',keydown);pointerUp();}
  };
}
