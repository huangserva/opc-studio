'use strict';
(() => {
  const $ = id => document.getElementById(id);
  const labels = {pending:'未上传', uploaded:'已上传 · 内容相同', changed:'内容或状态有变化 · 请先核对', created:'上传成功', recovered:'已恢复并登记', skipped:'已上传 · 本次跳过', failed:'上传未完成', not_attempted:'本次未尝试'};
  let items = [], selected = new Set(), busy = false;
  async function api(url, payload) {
    const r = await fetch(url, payload === undefined ? {} : {method:'POST', headers:{'Content-Type':'application/json','X-Article-Studio':'1'},body:JSON.stringify(payload)});
    const d = await r.json(); if(!r.ok) throw new Error(d.error || '请求未完成'); return d;
  }
  const visible = () => items.filter(d => (!$('pending-only').checked || d.state !== 'uploaded') && (!$('upload-source').value || ($('upload-source').value==='reviews' ? ['manual','publication_observations'].includes(d.source) : d.source===$('upload-source').value)));
  function controls() {
    $('upload').disabled = busy || !selected.size;
    $('upload').textContent = busy ? '正在上传，请等待…' : `上传所选（${selected.size}）`;
    ['select-visible','clear-selection','refresh','pending-only','upload-source'].forEach(id => $(id).disabled=busy);
  }
  function render() {
    const host=$('documents');host.replaceChildren();
    for(const d of visible()) {
      const row=document.createElement('label');row.className='document';row.dataset.number=d.number;row.dataset.state=d.state;
      const input=document.createElement('input');input.type='checkbox';input.checked=selected.has(d.number);input.disabled=busy || d.state==='changed';input.setAttribute('aria-label',d.title);
      input.onchange=()=>{if(input.checked){if(selected.size>=50){input.checked=false;return;}selected.add(d.number);}else selected.delete(d.number);controls();};
      const text=document.createElement('div'),title=document.createElement('strong'),meta=document.createElement('small'),result=document.createElement('small');
      title.textContent=d.title;meta.textContent=`${({articles:'文案与作品','external-references':'外部参考',manual:'发布复盘笔记 · 隐私-内部',publication_observations:'发布观察 · 隐私-内部'})[d.source] || '未知来源'} · ${d.body_characters} 字 · ${d.number}`;
      result.className='result';result.textContent=labels[d.action||d.state]+(d.error?'：'+d.error:'');text.append(title,meta,result);row.append(input,text);host.append(row);
    }
    host.setAttribute('aria-busy',String(busy)); controls();
  }
  async function load() {
    try{const d=await api('/api/knowledge/upload-candidates');items=d.items;selected.clear();render();$('status').className='';$('status').textContent=`共 ${items.length} 篇；未上传 ${items.filter(d=>d.state==='pending').length} 篇。`;}catch(e){$('status').className='error';$('status').textContent=e.message;$('documents').setAttribute('aria-busy','false');}
  }
  $('pending-only').onchange=render;
  $('upload-source').onchange=()=>{selected.clear();render();};
  if(new URLSearchParams(location.search).get('source')==='reviews')$('upload-source').value='reviews';
  $('select-visible').onclick=()=>{selected=new Set(visible().filter(d=>d.state!=='changed').slice(0,50).map(d=>d.number));render();};
  $('clear-selection').onclick=()=>{selected.clear();render();};$('refresh').onclick=load;
  $('upload').onclick=async()=>{
    if(busy || !selected.size)return;
    const chosen=items.filter(d=>selected.has(d.number));busy=true;render();$('status').className='';$('status').textContent=`正在上传 ${chosen.length} 篇，请等待回执。`;
    try{
      const d=await api('/api/knowledge/upload',{documents:chosen.map(({number,content_sha256})=>({number,content_sha256}))});
      for(const result of d.results){const item=items.find(x=>x.number===result.number);Object.assign(item,result);if(['created','recovered','skipped'].includes(result.action))item.state='uploaded';else if(result.action==='failed')item.state='failed';}
      const ok=d.results.filter(x=>['created','recovered'].includes(x.action)).length,skip=d.results.filter(x=>x.action==='skipped').length,fail=d.results.filter(x=>x.action==='failed').length;
      $('status').textContent=`已完成 ${ok} 篇，跳过 ${skip} 篇，失败 ${fail} 篇。${d.stopped?'远端返回异常，本批已停止，请先核对回执。':''}`;if(fail)$('status').className='error';
      // Keep selection/results: repeat click proves idempotency and failed entries can be retried.
    }catch(e){$('status').className='error';$('status').textContent=e.message;}
    finally{busy=false;render();}
  };
  load();
})();
