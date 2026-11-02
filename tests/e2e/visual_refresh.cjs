'use strict';
// Usage: node tests/e2e/visual_refresh.cjs /tmp/visual-refresh/<UTC> [before|candidate]
// Only GET/HEAD to the isolated data-copy instance. External services are represented
// by explicit unavailable receipts; no desktop profile, production port or write API.
const fs=require('node:fs'),path=require('node:path'),os=require('node:os'),assert=require('node:assert/strict');
const guard=require('./real_write_guard.cjs');guard.install();
const {chromium}=guard.playwright();
const out=path.resolve(process.argv[2]),mode=process.argv[3]||'candidate';guard.assertSandbox(out);
const base='http://127.0.0.1:'+fs.readFileSync(path.join(out,mode+'-port.txt'),'utf8').trim();guard.registerSandbox(base,path.join(out,'data-'+mode));
const pages=fs.readdirSync(path.join(out,mode,'public')).filter(f=>f.endsWith('.html')).sort();
const scenarios=[['today-interest','today.html?channel=interest&range=today'],['today-discovery','today.html?channel=hot&range=48h'],['library','library.html'],['deals','deals.html'],['schedules','schedules.html'],['settings','settings.html']];
const results={mode,pages:[],checks:[],contrast:[],externalFixtures:[],errors:[],requests:[]};let context;
const check=(name,value)=>{results.checks.push({name,pass:!!value});assert.ok(value,name)};
if(process.argv.includes('--compare-text')){
 const before=JSON.parse(fs.readFileSync(path.join(out,'before-text-audit.json'))),candidate=JSON.parse(fs.readFileSync(path.join(out,'candidate-text-audit.json'))),oldPages=new Map(before.pages.map(p=>[p.file+'|'+p.state,p])),rows=[];
 for(const page of candidate.pages){const old=oldPages.get(page.file+'|'+page.state),byKey=new Map(),byText=new Map();for(const n of old?.rows||[]){const key=n.key+'|'+n.kind;byKey.set(key,[...(byKey.get(key)||[]),n]);byText.set(n.text,[...(byText.get(n.text)||[]),n]);}
  for(const n of page.rows){const key=byKey.get(n.key+'|'+n.kind)||[],same=key.filter(m=>m.text===n.text),fallback=byText.get(n.text)||[],matches=same.length?same:key.length?key:fallback,prev=matches[0];const category=n.ratio>=4.5?'pass':prev&&n.ratio+1e-9>=prev.ratio?'residual':'degraded';rows.push({file:page.file,state:page.state,...n,beforeRatio:prev?.ratio??null,beforePath:prev?.path??null,match:same.length?'key+text':key.length?'key':fallback.length?'text':'new/unmatched',category});}
 }
 const counts={pass:0,residual:0,degraded:0};for(const r of rows)counts[r.category]++;
 const perPage=Object.fromEntries([...new Set(rows.map(r=>r.file))].sort().map(f=>[f,{pass:rows.filter(r=>r.file===f&&r.category==='pass').length,residual:rows.filter(r=>r.file===f&&r.category==='residual').length,degraded:rows.filter(r=>r.file===f&&r.category==='degraded').length,minimum:Math.min(...rows.filter(r=>r.file===f).map(r=>r.ratio))}]));
 const checks={twentyPages:new Set(rows.map(r=>r.file)).size===20,noDegradation:counts.degraded===0,todayAndLibraryAllText:rows.filter(r=>['today.html','library.html'].includes(r.file)).every(r=>r.ratio>=4.5),libraryTitleHierarchy:candidate.pages.filter(p=>p.file==='library.html').flatMap(p=>p.rows).filter(r=>r.classes.includes('library-title')).every(r=>parseFloat(r.fontSize)===23&&r.rawForeground==='rgb(34, 34, 32)'),inheritedErrorsIdentical:JSON.stringify(before.errors)===JSON.stringify(candidate.errors)};
 const result={counts,perPage,checks,threshold:4.5,comparisonTolerance:1e-9,observations:rows.length,states:candidate.pages.length,uniqueResidualNodes:new Set(rows.filter(r=>r.category==='residual').map(r=>r.file+'|'+r.key+'|'+r.text)).size,rows};
 fs.writeFileSync(path.join(out,'text-contrast-comparison.json'),JSON.stringify(result,null,2));fs.writeFileSync(path.join(out,'phase2-residuals.json'),JSON.stringify(rows.filter(r=>r.category==='residual'),null,2));console.log(JSON.stringify({counts,checks,states:result.states,observations:rows.length,uniqueResidualNodes:result.uniqueResidualNodes},null,2));assert(Object.values(checks).every(Boolean),'实际节点对比度/标题/继承错误门槛');return;
}

function rgb(c){return (c.match(/[\d.]+/g)||[]).slice(0,3).map(Number)}
function ratio(a,b){const lum=c=>rgb(c).map(v=>{v/=255;return v<=.04045?v/12.92:((v+.055)/1.055)**2.4}).reduce((n,v,i)=>n+v*[.2126,.7152,.0722][i],0),x=lum(a),y=lum(b);return (Math.max(x,y)+.05)/(Math.min(x,y)+.05)}
async function ready(p,file){
 if(file.startsWith('today')||file==='library.html')await p.waitForFunction(()=>document.querySelector('#item-list')?.getAttribute('aria-busy')==='false');
 else await p.waitForLoadState('networkidle',{timeout:7000}).catch(()=>{});
 await p.evaluate(()=>document.fonts.ready);
 if(file.startsWith('today')||file==='library.html'){
  const first=p.locator('#item-list .item-card .item-summary').first();if(await first.count())await first.click();
  if(await first.count())await p.locator('#reader .item-detail').waitFor();
 }
}
async function visit(file,width,shot){
 const p=await context.newPage(),errors=[];await p.setViewportSize({width,height:900});
 p.on('console',m=>{if(m.type()==='error')errors.push({type:'console',text:m.text()})});p.on('pageerror',e=>errors.push({type:'script',text:e.message}));p.on('requestfailed',r=>{if(r.failure()?.errorText!=='net::ERR_ABORTED')errors.push({type:'network',url:r.url(),text:r.failure()?.errorText})});
 await p.goto(base+'/'+file);await ready(p,file);await p.screenshot({path:path.join(out,'shots',shot),fullPage:false});
 const layout=await p.evaluate(()=>({width:innerWidth,scrollWidth:document.documentElement.scrollWidth,heading:document.querySelector('h1')?.textContent,themeFirst:[...document.querySelectorAll('link[rel=stylesheet]')][0]?.getAttribute('href'),title:document.querySelector('h1')?getComputedStyle(document.querySelector('h1')).fontFamily:null,body:getComputedStyle(document.body).backgroundColor}));
 results.pages.push({file,width,shot,layout,errors});results.errors.push(...errors.map(e=>({file,width,...e})));
 if(mode==='candidate'&&file.startsWith('today')){
  check(width+'/'+file+' 保留两行详情标题',await p.locator('.detail-title').evaluateAll(ns=>ns.every(n=>getComputedStyle(n).fontSize==='18px'&&getComputedStyle(n).webkitLineClamp==='2')));
  check(width+'/'+file+' 无普通噪音徽标',await p.locator('#item-list .item-tier').evaluateAll(ns=>ns.every(n=>!['普通','兴趣','其他','兴趣候选'].includes(n.textContent))));
  check(width+'/'+file+' 来源色块均有可见文字',await p.locator('#sources .status-block').evaluateAll(ns=>ns.length>0&&ns.every(n=>n.textContent.trim().length>0)));
 }
 await p.close();
}
async function auditText(context){
 const audit={mode,pages:[],errors:[],mergedSelectors:{},method:'WCAG sRGB; every visible DOM text run + form values/placeholders + text pseudo-elements; Range rectangles intersected with viewport/overflow ancestors; ancestor alpha backgrounds composited over root; gradient color stops use worst contrast; group opacity included; no screenshot color substitution'},fileDir=path.join(out,'audit');
 const snapshot=async(p,file,state)=>{
  const data=await p.evaluate(()=>{
   const parse=v=>{if(!v||v==='transparent')return[0,0,0,0];const m=v.match(/^rgba?\(([^)]+)\)/);if(!m)return null;const a=m[1].replaceAll('/',' ').split(/[,\s]+/).filter(Boolean).map(Number);return [a[0],a[1],a[2],a[3]??1]};
   const blend=(fg,bg,alpha=1)=>{const a=(fg[3]??1)*alpha;return fg.slice(0,3).map((x,i)=>x*a+bg[i]*(1-a)).concat(1)};
   const lum=c=>c.slice(0,3).map(v=>{v/=255;return v<=.04045?v/12.92:((v+.055)/1.055)**2.4}).reduce((s,v,i)=>s+v*[.2126,.7152,.0722][i],0),ratio=(a,b)=>(Math.max(lum(a),lum(b))+.05)/(Math.min(lum(a),lum(b))+.05);
   const path=n=>{let parts=[];while(n&&n.nodeType===1){if(n.id){parts.unshift('#'+CSS.escape(n.id));break;}let s=n.localName;const cls=[...n.classList].filter(c=>!['selected','active','hover','reading'].includes(c));if(cls.length)s+='.'+cls.map(c=>CSS.escape(c)).join('.');const parent=n.parentElement;if(parent){const same=[...parent.children].filter(x=>x.localName===n.localName);if(same.length>1)s+=':nth-of-type('+(same.indexOf(n)+1)+')';}parts.unshift(s);n=parent;}return parts.join(' > ')};
   const clipped=(n,rect)=>{let r={left:Math.max(0,rect.left),right:Math.min(innerWidth,rect.right),top:Math.max(0,rect.top),bottom:Math.min(innerHeight,rect.bottom)};for(let el=n;el;el=el.parentElement){const c=getComputedStyle(el);if(c.display==='none'||c.visibility!=='visible'||Number(c.opacity)===0)return false;if(el!==n&&/(hidden|clip|scroll|auto)/.test(c.overflowX+' '+c.overflowY)){const b=el.getBoundingClientRect();if(/hidden|clip|scroll|auto/.test(c.overflowX)){r.left=Math.max(r.left,b.left);r.right=Math.min(r.right,b.right);}if(/hidden|clip|scroll|auto/.test(c.overflowY)){r.top=Math.max(r.top,b.top);r.bottom=Math.min(r.bottom,b.bottom);}}}return r.right-r.left>.5&&r.bottom-r.top>.5};
   const resolve=(el,cs)=>{const chain=[];for(let a=el;a;a=a.parentElement)chain.unshift(a);let bg=[255,255,255,1],opacity=1,alternatives=[];const layers=[];for(const a of chain){const c=getComputedStyle(a);opacity*=Number(c.opacity);const raw=parse(c.backgroundColor);if(raw&&raw[3]>0){bg=blend(raw,bg,opacity);alternatives=alternatives.map(x=>blend(raw,x,opacity));layers.push({element:path(a),color:c.backgroundColor,opacity});}if(c.backgroundImage!=='none'){
    const stops=(c.backgroundImage.match(/rgba?\([^)]+\)/g)||[]).map(parse).filter(Boolean);if(stops.length){const bases=[bg,...alternatives],painted=stops.flatMap(x=>bases.map(base=>blend(x,base,opacity)));bg=painted[0];alternatives=painted.slice(1);layers.push({element:path(a),gradient:c.backgroundImage});}else layers.push({element:path(a),unresolvedImage:c.backgroundImage});
   }}const raw=parse(cs.color)||[0,0,0,1];const candidates=[bg,...alternatives].map(b=>({bg:b,fg:blend(raw,b,opacity),ratio:ratio(blend(raw,b,opacity),b)})).sort((a,b)=>a.ratio-b.ratio);return{...candidates[0],rawForeground:cs.color,opacity,layers}};
   const rows=[];function add(el,text,rects,kind='text',pseudo=''){
    if(!text?.trim()||!rects.some(r=>clipped(el,r)))return;const c=getComputedStyle(el,pseudo||null),colors=resolve(el,c);const rect=rects.find(r=>clipped(el,r));rows.push({key:path(el)+(pseudo||'')+'|'+kind,path:path(el),tag:el.localName,id:el.id,classes:[...el.classList],kind,pseudo,text:text.trim().replace(/\s+/g,' '),fontSize:c.fontSize,fontFamily:c.fontFamily,fontWeight:c.fontWeight,disabled:!!el.disabled,foreground:colors.fg,rawForeground:colors.rawForeground,background:colors.bg,ratio:colors.ratio,opacity:colors.opacity,layers:colors.layers,rect:{x:rect.x,y:rect.y,width:rect.width,height:rect.height}});
   }
   const walker=document.createTreeWalker(document.body,NodeFilter.SHOW_TEXT);while(walker.nextNode()){const n=walker.currentNode,el=n.parentElement;if(!el||/^(SCRIPT|STYLE|NOSCRIPT|OPTION|TEXTAREA)$/.test(el.tagName)||!n.textContent.trim())continue;const range=document.createRange();range.selectNodeContents(n);add(el,n.textContent,[...range.getClientRects()]);}
   for(const el of document.querySelectorAll('input:not([type=hidden]):not([type=checkbox]):not([type=radio]),textarea,select')){if(el.tagName==='SELECT')add(el,el.selectedOptions[0]?.textContent,[el.getBoundingClientRect()],'value');else add(el,el.value||el.placeholder,[el.getBoundingClientRect()],el.value?'value':'placeholder',!el.value?'::placeholder':'');}
   for(const el of document.querySelectorAll('body *'))for(const pseudo of ['::before','::after']){const c=getComputedStyle(el,pseudo);if(c.content&& !['none','normal','""','\'\''].includes(c.content)&&c.display!=='none'&&Number(c.opacity)!==0){const text=c.content.replace(/^["']|["']$/g,'');if(!text.startsWith('url('))add(el,text,[el.getBoundingClientRect()],'pseudo',pseudo);}}
   const sheet=[...document.styleSheets].find(s=>s.href?.endsWith('/theme.css')),selectors=[];if(sheet)for(const r of sheet.cssRules||[]){if(!r.selectorText||!(r.selectorText.includes(':is(')||r.selectorText.includes(',')))continue;try{const pseudo=r.selectorText.match(/::(?:before|after|placeholder)$/)?.[0]||'',query=pseudo?r.selectorText.slice(0,-pseudo.length):r.selectorText,els=[...document.querySelectorAll(query)];selectors.push({selector:r.selectorText,declarations:r.style.cssText,elements:els.map(el=>({path:path(el)+pseudo,tag:el.localName,id:el.id,classes:[...el.classList],text:(pseudo==='::placeholder'?el.placeholder||'':el.textContent).trim().replace(/\s+/g,' ').slice(0,120),fontSize:getComputedStyle(el,pseudo||null).fontSize,foreground:getComputedStyle(el,pseudo||null).color}))})}catch{}}
   return {rows,selectors,layout:{width:innerWidth,scrollWidth:document.documentElement.scrollWidth},title:document.title};
  });
  for(const group of data.selectors){const key=group.selector;audit.mergedSelectors[key]??={declarations:group.declarations,pages:{}};const targets=audit.mergedSelectors[key].pages[file]??={};for(const el of group.elements)targets[el.path]=el;}
  audit.pages.push({file,state,url:p.url(),...data,selectors:undefined});
  await p.screenshot({path:path.join(fileDir,mode+'-'+file.replace('.html','')+'-'+state+'.png')});
 };
 const click=async(p,selector)=>{const n=p.locator(selector).first();if(await n.count()&&await n.isVisible()){await n.click();await p.waitForTimeout(150);return true;}return false};
 for(const file of pages){
  const p=await context.newPage();p.on('console',m=>{if(m.type()==='error')audit.errors.push({file,type:'console',text:m.text()})});p.on('pageerror',e=>audit.errors.push({file,type:'script',text:e.message}));
  await p.goto(base+'/'+file);await ready(p,file);await snapshot(p,file,'default');
  if(file==='today.html'){
   for(const [channel,range]of [['hot','48h'],['interest','today'],['interest','48h'],['official','48h']]){
    await p.goto(base+'/today.html?channel='+channel+'&range='+range);await ready(p,file);await snapshot(p,file,'selected-'+channel+'-'+range);
    const c=p.locator('#item-list .item-card').first();if(await c.count()){await c.hover();await snapshot(p,file,'hover-'+channel+'-'+range);const other=p.locator('#item-list .item-card').nth(1);if(await other.count()){await other.hover();await snapshot(p,file,'hover-unselected-'+channel+'-'+range);}}
    if(await click(p,'#more-filters>summary'))await snapshot(p,file,'expanded-'+channel+'-'+range);
   }
  }else if(file==='library.html'){
   const c=p.locator('#item-list .item-card').first();if(await c.count()){await c.hover();await snapshot(p,file,'selected-hover');const other=p.locator('#item-list .item-card').nth(1);if(await other.count()){await other.hover();await snapshot(p,file,'hover-unselected');}}
   await click(p,'#research');await p.waitForFunction(()=>document.querySelector('#item-list')?.getAttribute('aria-busy')==='false');await p.locator('#q').fill('AI');await p.locator('#q').press('Enter');await ready(p,file);await snapshot(p,file,'research-selected');
   if(await p.locator('#item-list .item-card').count()){await p.locator('#item-list .item-card').first().hover();await snapshot(p,file,'research-hover');}
   await click(p,'#mine');await p.waitForFunction(()=>document.querySelector('#item-list')?.getAttribute('aria-busy')==='false');await click(p,'#trash');await p.waitForFunction(()=>document.querySelector('#item-list')?.getAttribute('aria-busy')==='false');await snapshot(p,file,'trash');
  }else{
   const map={'cards.html':'#cd-layout [data-view=time]','clues.html':'[data-view=unread]','deals.html':'#dl-focus [data-scope=work]','events.html':'[data-view=waiting]','github.html':'#gh-refresh','intake.html':'#in-views [data-kind=text]','intel.html':'#itl-by-link','knowledge.html':'#kb-search button[type=submit]','lab-test.html':'#lab-refresh','lab.html':'#lab-refresh','feedback.html':'[data-range=week]','index.html':'[data-type-filter=post]','publish.html':'.today-status a[href="/x-official.html"]','schedules.html':'[data-scope=external]','settings.html':'#refresh','twitter-radar.html':'#tab-vip-btn','video-radar.html':'[data-list=douyin_realtime]','x-official.html':'#mode'};
   const selector=map[file];if(selector&&file!=='publish.html'&&await click(p,selector)){await p.waitForTimeout(300);await snapshot(p,file,'interaction');}
   const hover=p.locator(file==='deals.html'?'.dl-deal-card':file==='schedules.html'?'.task-row':'.item-card,article.card,.history-card,main button').filter({visible:true}).first();if(await hover.count()){await hover.hover();await snapshot(p,file,'hover');}
   const ds=p.locator('main details>summary,.main details>summary').filter({visible:true});for(let i=0;i<Math.min(2,await ds.count());i++){await ds.nth(i).click();await snapshot(p,file,'expanded-'+i);}
  }
  // Move the main scroll containers to reveal text below the first viewport.
  await p.evaluate(()=>{const candidates=[document.scrollingElement,...document.querySelectorAll('main,section,aside,.item-list,.reader,.schedule-canvas,.canvas,.lab-scroll,.radar-scroll-area')];for(const el of candidates){if(el&&el.scrollHeight>el.clientHeight+10&&getComputedStyle(el).overflowY!=='hidden')el.scrollTop=Math.min(el.scrollHeight-el.clientHeight,Math.max(400,el.clientHeight*.8));}});await snapshot(p,file,'scrolled');
  await p.evaluate(()=>{for(const el of [document.scrollingElement,...document.querySelectorAll('main,section,aside,.item-list,.reader,.schedule-canvas,.canvas,.lab-scroll,.radar-scroll-area')])if(el&&el.scrollHeight>el.clientHeight+10&&getComputedStyle(el).overflowY!=='hidden')el.scrollTop=el.scrollHeight;});await snapshot(p,file,'bottom');
  await p.close();console.log('audit',mode,file,audit.pages.filter(x=>x.file===file).length);
 }
 fs.writeFileSync(path.join(out,mode+'-text-audit.json'),JSON.stringify(audit,null,2));
 const selectorOnly={};for(const [key,value]of Object.entries(audit.mergedSelectors))selectorOnly[key]={...value,pages:Object.fromEntries(Object.entries(value.pages).map(([page,els])=>[page,Object.values(els)]))};fs.writeFileSync(path.join(out,mode+'-merged-selectors.json'),JSON.stringify(selectorOnly,null,2));
 console.log('text audit finished',mode,'states',audit.pages.length,'nodes',audit.pages.reduce((n,p)=>n+p.rows.length,0),'errors',audit.errors.length);
}

(async()=>{try{
 context=await chromium.launchPersistentContext(path.join(out,'profile-'+mode+'-'+Date.now()),{headless:true,channel:'chrome',viewport:{width:1280,height:900},args:['--disable-background-networking','--disable-component-update']});
 await context.route('**/*',async r=>{const q=r.request(),u=new URL(q.url());if(!['GET','HEAD'].includes(q.method())){if(['/api/knowledge/sync','/api/local-publishing/connection'].includes(u.pathname)||(process.argv.includes('--audit-text')&&/^\/api\/clues\/[^/]+\/seen$/.test(u.pathname))){results.externalFixtures.push('POST '+u.pathname+' (blocked, fixture only)');return r.fulfill({json:{status:'idle',running:false}});}throw Error('禁止写请求 '+q.method()+' '+u.pathname);}if(u.origin!==base){if(q.resourceType()==='image')return r.fulfill({contentType:'image/svg+xml',body:'<svg xmlns="http://www.w3.org/2000/svg" width="100" height="60"><rect width="100%" height="100%" fill="#f4f5e8"/></svg>'});return r.abort();}
 // Backend probes are outside this visual candidate: do not contact 4090 or provider APIs.
 if(u.pathname==='/api/clues/wechat/status')return r.fulfill({json:{status:'unavailable',message:'隔离验收未连接微信雷达'}});
 if(/\/api\/generation\/backend-(health|inventory)/.test(u.pathname)){results.externalFixtures.push(u.pathname);return r.fulfill({json:{ok:false,available:false,error:'隔离验收未连接模型服务',models:[],inventory:[],state:'offline'}});}
 results.requests.push(u.pathname);return r.continue();});
 if(process.argv.includes('--audit-text')){await auditText(context);return;}
 for(const f of pages)await visit(f,1280,mode+'-audit-'+f.replace('.html','.png'));
 for(const width of [1280,1024])for(const [name,file]of scenarios)await visit(file,width,mode+'-'+name+'-'+width+'.png');
 if(mode==='candidate'){
  const p=await context.newPage();await p.goto(base+'/today.html?channel=interest&range=today');await ready(p,'today.html');
  await p.evaluate(()=>{const d=document.createElement('section');d.id='status-gallery';d.style.cssText='position:fixed;bottom:20px;right:20px;padding:18px;background:var(--c-surface);border:1px solid var(--c-line);z-index:999;display:flex;gap:8px';for(const [kind,text]of [['running','进行中'],['warning','异常 · 待确认'],['failed','失败 · 已断开'],['complete','正常 · 已完成'],['idle','空闲 · 未开始']]){const s=document.createElement('span');s.className='status-block status-'+kind;s.textContent=text;d.append(s);}document.body.append(d);});
  for(const [name,selector]of [['正文','.item-summary'],['次要文字','.detail-meta'],['选中卡片元信息','.item-card.selected .item-card-meta'],['选中卡片辅助文字','.today-card.selected .interest-direction,.today-card.selected .today-topic'],...['running','warning','failed','complete','idle'].map(k=>[k,'#status-gallery .status-'+k])]){
   const colors=await p.locator(selector).first().evaluate(n=>{let bg='rgba(0, 0, 0, 0)',el=n;while(el&&bg==='rgba(0, 0, 0, 0)'){bg=getComputedStyle(el).backgroundColor;el=el.parentElement;}return {fg:getComputedStyle(n).color,bg,fontSize:getComputedStyle(n).fontSize,text:n.textContent.trim()}});
   const value=ratio(colors.fg,colors.bg);results.contrast.push({name,...colors,ratio:value});check(name+' 对比度≥4.5',value>=4.5);
  }
  await p.screenshot({path:path.join(out,'audit/status-gallery.png')});await p.close();
  check('20页首先加载theme.css',results.pages.slice(0,pages.length).every(p=>p.layout.themeFirst==='/theme.css'));
  check('20页1280横向无新增溢出',results.pages.slice(0,pages.length).every(p=>p.layout.scrollWidth<=1281));
 }
 // Existing page errors are recorded for comparison, never silently discarded.
 console.log(JSON.stringify({mode,pages:results.pages.length,checks:results.checks.length,errors:results.errors.length,contrast:results.contrast.map(x=>[x.name,x.ratio.toFixed(2)])}));
 }catch(e){results.failure=e.stack;process.exitCode=1;console.error(e);}finally{fs.writeFileSync(path.join(out,mode+'-visual-results.json'),JSON.stringify(results,null,2));await context?.close();}})();
