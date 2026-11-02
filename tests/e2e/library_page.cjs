'use strict';
const writeGuard=require('./real_write_guard.cjs');writeGuard.install();
if(process.argv.includes('--real')){writeGuard.readOnlyMain('library_page').catch(e=>{console.error(e);process.exitCode=1});return;}

// /tmp 独立 profile + 随机端口；--real 提前进入共用只读 GET 路径。
const fs=require('fs'),path=require('path'),os=require('os'),net=require('net'),assert=require('assert/strict'),crypto=require('crypto');
const {spawn,execFileSync}=require('child_process');
const {chromium}=(()=>{try{return require('playwright')}catch(_){return require(path.join(os.homedir(),'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright'))}})();
const ROOT=path.resolve(__dirname,'../..'),PY=path.resolve(ROOT,'../article-importer/.venv/bin/python'),REAL=false;
const tmp=fs.mkdtempSync('/tmp/library-e2e-'),out=REAL?path.join(ROOT,'coordination/impl-library-p2-screenshots'):tmp;
fs.mkdirSync(out,{recursive:true});
const long='首行😀 <script>alert(1)</script> <img src=x onerror=alert(1)> &amp;\n\n'+'完整中文正文'.repeat(450)+'\n末行　\t';
const results=[],errors=[],perf={},timings=[],documents=[];let child,context,base,workspace,before,keep;
const sha=p=>fs.existsSync(p)?crypto.createHash('sha256').update(fs.readFileSync(p)).digest('hex'):'absent';
const dbs=()=>['knowledge/knowledge.db','inspiration/intake.db','inspiration/cards.db'].map(p=>path.join(workspace,p));
const hashes=()=>Object.fromEntries(dbs().map(p=>[p,sha(p)]));
const check=(name,ok,detail='')=>{assert.ok(ok,name+' '+detail);results.push({name,passed:true,detail});console.log('通过 '+name+(detail?' '+detail:''));};
async function api(url,data,method=data===undefined?'GET':'POST',headers){const r=await context.request.fetch(base+url,{method,headers:headers??(method==='GET'?{}:{Origin:base,'X-Article-Studio':'1'}),...(data===undefined?{}:{data})});const body=await r.json();return {status:r.status(),body};}
async function get(url){const r=await api(url);assert.equal(r.status,200,JSON.stringify(r.body));return r.body;}
async function post(url,data){const r=await api(url,data);assert.ok(r.status<300,JSON.stringify(r.body));return r.body;}
const ready=async page=>{await page.waitForFunction(()=>document.getElementById('item-list').getAttribute('aria-busy')==='false');};
async function select(page,id){await page.locator('.item-card[data-item-id="'+id+'"]').click();await page.locator('#reader .item-detail[data-item-id="'+id+'"]').waitFor();}
async function search(page,q){const wait=page.waitForResponse(r=>{const u=new URL(r.url());return u.pathname==='/api/'+(awaitMode==='research'?'library/search':'items') && u.searchParams.get('q')===q;});await page.locator('#q').fill(q);await wait;await ready(page);}
let awaitMode='mine';
async function start(){
 workspace=path.join(tmp,'workspace');writeGuard.assertSandbox(workspace);fs.mkdirSync(workspace,{recursive:true});
 const setup=`import sys;from pathlib import Path;sys.path.insert(0,${JSON.stringify(ROOT)});from tests.test_items_store import knowledge_fixture;from items_store import ItemsStore;root=Path(${JSON.stringify(workspace)});knowledge_fixture(root);s=ItemsStore(root);s.upsert({'id':'x:fixture','title':'素材注入 <img src=x onerror=alert(1)>','full_text':${JSON.stringify(long)},'sources':['manual'],'note':'笔记 <svg onload=alert(1)>','url':'javascript:alert(1)','tags':['fixture']});s.upsert({'id':'x:second','title':'第二条素材','full_text':'第二条完整正文','sources':['manual']})`;
 execFileSync(PY,['-c',setup],{cwd:ROOT});
 const sparse=`import sqlite3; c=sqlite3.connect(${JSON.stringify(path.join(workspace,'knowledge/knowledge.db'))}); c.executemany('INSERT INTO documents(id,source,title) VALUES(?,?,?)',[(f'sparse-{i}','manual','鲸鱼夹具标题' if i==0 else '普通资料') for i in range(8300)]); c.commit(); c.close()`;
 execFileSync(PY,['-c',sparse],{cwd:ROOT});
 const reports=path.join(tmp,'reports');fs.mkdirSync(path.join(reports,'daily'),{recursive:true});const stamp=new Date().toISOString();
 fs.writeFileSync(path.join(reports,'daily/test.json'),JSON.stringify({collected_at:stamp,posts:[{id:'12345678',handle:'fixture',author:'今日夹具',url:'https://x.com/fixture/status/12345678',text:long,created_at:stamp,label:'爆款',topic:'agent',views:1000}]}));fs.writeFileSync(path.join(tmp,'bundle.json'),JSON.stringify({tweets:[],vip_tweets:[],updated_at:stamp}));
 const port=await new Promise(resolve=>{const s=net.createServer().listen(0,'127.0.0.1',()=>{const p=s.address().port;s.close(()=>resolve(p));});});base='http://127.0.0.1:'+port;writeGuard.registerSandbox(base,workspace);
 // 实际 Handler / API / SQLite / 前端。旧全文读取只用只读连接，避免启动后台采集器。
 const run=`import sys;from pathlib import Path;from types import SimpleNamespace;from http.server import ThreadingHTTPServer;sys.path.insert(0,${JSON.stringify(ROOT)});import server,grok_bot_radar,linuxdo_source;linuxdo_source.refresh_background=lambda root:None;grok_bot_radar.BUNDLE_PATH=Path(${JSON.stringify(path.join(tmp,'bundle.json'))});from items_store import readonly;import library_search,time;root=Path(${JSON.stringify(workspace)});original_build=library_search.build_catalog\ndef gated_build(root):\n while not Path(${JSON.stringify(path.join(tmp,'build-release'))}).exists():time.sleep(.01)\n return original_build(root)\nlibrary_search.build_catalog=gated_build\nclass Knowledge:\n def document(self,id):\n  c=readonly(root/'knowledge/knowledge.db');r=c.execute('SELECT * FROM documents WHERE id=?',(id,)).fetchone();c.close();return dict(r)\ns=ThreadingHTTPServer(('127.0.0.1',${port}),server.Handler);s.store=server.Store(root);s.store._knowledge=Knowledge();s.assistant=server.assistant_module.Assistant(s.store,server.content,root/'absent.json');s.serve_forever()`;
 child=spawn(PY,['-u','-c',run],{cwd:ROOT,env:{...process.env,PYTHONDONTWRITEBYTECODE:'1',GROK_BOT_REPORTS_DIR:path.join(tmp,'reports')},stdio:['ignore','pipe','pipe']});let log='';child.stdout.on('data',b=>log+=b);child.stderr.on('data',b=>log+=b);
 for(let i=0;i<120;i++){try{if((await fetch(base+'/api/items')).ok)return;}catch(_){}if(child.exitCode!==null)throw Error(log);await new Promise(r=>setTimeout(r,100));}throw Error(log);
}
(async()=>{try{
 await start();before=hashes();
 context=await chromium.launchPersistentContext(path.join(tmp,'profile'),{headless:true,channel:'chrome',viewport:{width:1440,height:900},permissions:['clipboard-read','clipboard-write'],args:['--disable-background-networking','--disable-component-update']});
 await context.route('**/*',route=>new URL(route.request().url()).origin===base?route.continue():route.abort());
 const page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));
 for(let i=0;i<3;i++){const t=performance.now();await page.goto(base+'/library.html');await page.locator('#item-list .item-card').first().waitFor();await ready(page);timings.push(Number(((performance.now()-t)/1000).toFixed(5)));}
 check('首屏3次均<1秒',timings.every(t=>t<1),JSON.stringify(timings));
 const csp=(await context.request.get(base+'/library.html')).headers()['content-security-policy'];check('素材库CSP允许https图片且不允许http',csp.includes(' https:;')&&!csp.includes(' http:'));
 check('导航今日后紧跟素材库',await page.locator('#workspace-nav>a').evaluateAll(ns=>ns.slice(0,2).map(n=>n.dataset.navItem).join(','))==='today,library');
 check('旧版九个入口完整且工具分组独立',await page.locator('#workspace-nav details[data-nav-group=legacy] [data-nav-item]').evaluateAll(ns=>ns.map(n=>n.dataset.navItem).join(','))==='clues,intel,github,schedules,feedback,x-official,knowledge,intake,cards' && await page.locator('#workspace-nav details[data-nav-group=tools] [data-nav-item]').count()===2);
 await page.screenshot({path:path.join(out,'library-first-screen.png')});
 if(!REAL){
  await select(page,'x:fixture');check('2400字中文emoji原文逐字一致',await page.locator('#reader .full-text').textContent()===long.replaceAll('&amp;','&'));
  check('标题正文note均无注入节点',await page.locator('.library-title img,.full-text img,.full-text script,.library-note svg').count()===0);
  check('危险链接不会产生href',await page.locator('#reader a[href^="javascript:"]').count()===0);
  await page.locator('#note-edit').fill('编辑后的收藏理由 <img src=x onerror=alert(1)>');await page.getByRole('button',{name:'保存收藏理由',exact:true}).click();await page.locator('.action-notice').getByText('已保存',{exact:true}).waitFor();
  check('收藏理由保存', (await get('/api/items/x%3Afixture')).note==='编辑后的收藏理由 <img src=x onerror=alert(1)>');
  const popup=page.waitForEvent('popup');await page.getByRole('button',{name:'拿去写',exact:true}).click();const opened=await popup;await opened.waitForURL(u=>u.searchParams.has('article'));const used=await get('/api/items/x%3Afixture');const draft=await get('/api/article/'+encodeURIComponent(used.used_work_id));check('拿去写建底稿保留全文并记录used',draft.blocks.some(b=>b.text?.includes(long))&&used.status==='used'&&new URL(opened.url()).searchParams.get('article')===used.used_work_id);await opened.close();
  const ids=await page.locator('#item-list .item-card').evaluateAll(ns=>ns.map(n=>n.dataset.itemId));
  await page.keyboard.press('k');await page.locator('#reader .item-detail[data-item-id="'+ids[0]+'"]').waitFor();
  await page.keyboard.press('j');await page.locator('#reader .item-detail[data-item-id="'+ids[Math.min(1,ids.length-1)]+'"]').waitFor();check('J/K按具体id切换全文',true);
 }
 if(!REAL){
  await page.goto(base+'/today.html');await page.locator('.item-card[data-item-id="x:12345678"]').waitFor();await select(page,'x:12345678');
  await page.locator('#reader [data-action="keep"]').click();await page.locator('.action-notice').getByText('已保存',{exact:true}).waitFor();
  await page.goto(base+'/library.html');await ready(page);await select(page,'x:12345678');
  check('今日页点存后素材库按同一id显示全文',await page.locator('#reader .full-text').textContent()===long.replaceAll('&amp;','&'));
 }
 const tabResponse=page.waitForResponse(r=>new URL(r.url()).pathname==='/api/library/search');await page.getByRole('tab',{name:'资料检索',exact:true}).click();await tabResponse;await ready(page);awaitMode='research';
 const word=REAL?'agent':'agent';await search(page,word);const research=await get('/api/library/search?q='+word);assert(research.items.length);const chosen=research.items[0];
 await select(page,chosen.id);const doc=await get('/api/knowledge/document/'+chosen.id);check('资料全文与旧接口逐字一致',await page.locator('#reader .full-text').textContent()===doc.body.replace(/&(?:#[xX][0-9a-fA-F]+|#[0-9]+|[A-Za-z][A-Za-z0-9]+);/g,token=>({'&amp;':'&','&gt;':'>','&lt;':'<','&quot;':'\"','&#39;':"'"}[token] || token)));
 check('资料动作只有存为素材',await page.locator('#reader .item-action').allTextContents().then(xs=>xs.join(',')==='存为素材'));
 if(!REAL){await search(page,'alert');check('恶意snippet高亮保持纯文本',await page.locator('.library-snippet').first().textContent().then(t=>t.includes('script')||t.includes('onerror')) && await page.locator('.library-snippet mark').count()>0 && await page.locator('.library-snippet img,.library-snippet script').count()===0);await search(page,word);await select(page,chosen.id);}
 if(!REAL){
  await search(page,'鲸');const building=await get('/api/library/search?q='+encodeURIComponent('鲸'));
  check('目录构建期间真实搜索有提示且不阻塞',building.catalog_state==='building'&&building.partial&&building.scanned_count<=8192&&await page.locator('#page-notice').textContent().then(t=>t.includes('标题目录正在后台构建')&&t.includes('结果可能不完整')));
  await page.screenshot({path:path.join(out,'library-building.png')});
  fs.writeFileSync(path.join(tmp,'build-release'),'ready');
  for(let i=0;i<200;i++){if((await get('/api/library/search?q='+encodeURIComponent('鲸'))).catalog_state==='ready')break;await new Promise(r=>setTimeout(r,20));}
  await page.locator('#q').fill('');await search(page,'鲸');const partial=await get('/api/library/search?q='+encodeURIComponent('鲸'));
  check('短词全库命中扫描窗口之外的旧资料',!partial.partial&&partial.catalog_state==='ready'&&partial.items[0].id==='sparse-0'&&partial.total_label==='≥1'&&await page.locator('#page-notice').textContent().then(t=>t.includes('已搜索全库标题')));
  await search(page,'龘');const empty=await get('/api/library/search?q='+encodeURIComponent('龘'));
  check('稀有短词完整查库后显示空页',!empty.partial&&empty.catalog_state==='ready'&&empty.items.length===0&&empty.total_label==='≥0'&&await page.locator('.library-empty').textContent().then(t=>t.includes('没有找到内容'))&&await page.locator('#page-notice').textContent().then(t=>t.includes('已搜索全库标题')));
  await search(page,word);await select(page,chosen.id);
 }
 await page.screenshot({path:path.join(out,'library-research.png')});
 const response=page.waitForResponse(r=>new URL(r.url()).pathname==='/api/items'&&r.request().method()==='POST');await page.getByRole('button',{name:'存为素材',exact:true}).click();const saved=await (await response).json();keep=saved.id;
 await page.locator('.action-notice').getByText('已保存',{exact:true}).waitFor();check('存为素材保留原文及旧id',saved.full_text===doc.body&&saved.legacy_refs.some(r=>r.store==='knowledge'&&r.id===chosen.id));
 const mineResponse=page.waitForResponse(r=>new URL(r.url()).pathname==='/api/items');await page.getByRole('tab',{name:'我的素材',exact:true}).click();await mineResponse;await ready(page);awaitMode='mine';
 // Use title search to find the saved row even after >50 personal items.
 await search(page,doc.title.slice(0,Math.max(3,Math.min(12,doc.title.length))));await select(page,saved.id);check('收藏后素材库能找到',await page.locator('#reader .full-text').textContent()===doc.body.replace(/&(?:#[xX][0-9a-fA-F]+|#[0-9]+|[A-Za-z][A-Za-z0-9]+);/g,token=>({'&amp;':'&','&gt;':'>','&lt;':'<','&quot;':'\"','&#39;':"'"}[token] || token)));
 const trashResponse=page.waitForResponse(r=>new URL(r.url()).pathname.endsWith('/trash'));await page.getByRole('button',{name:'移到回收站',exact:true}).click();assert.equal((await trashResponse).status(),200);await ready(page);
 const trashList=page.waitForResponse(r=>{const u=new URL(r.url());return u.pathname==='/api/items'&&u.searchParams.get('trashed')==='1';});await page.locator('#trash').click();await trashList;await ready(page);await select(page,saved.id);
 const restore=page.waitForResponse(r=>new URL(r.url()).pathname.endsWith('/restore'));await page.getByRole('button',{name:'恢复',exact:true}).click();assert.equal((await restore).status(),200);await ready(page);
 check('回收站恢复后内容完全一致',(await get('/api/items/'+encodeURIComponent(saved.id))).full_text===doc.body);
 await page.screenshot({path:path.join(out,'library-restored.png')});
 if(!REAL){
  await post('/api/items/'+encodeURIComponent(saved.id)+'/trash',{});await page.reload();await ready(page);await page.locator('#trash').click();await ready(page);await select(page,saved.id);
  page.once('dialog',d=>d.dismiss());await page.getByRole('button',{name:'彻底删除',exact:true}).click();await page.locator('.action-notice').getByText('已取消',{exact:true}).waitFor();check('取消删除不落盘且准确提示',(await api('/api/items/'+encodeURIComponent(saved.id))).status===200);
  let confirms=0;page.on('dialog',async d=>{confirms++;await d.accept();});const del=page.waitForResponse(r=>r.request().method()==='DELETE');await page.getByRole('button',{name:'彻底删除',exact:true}).click();assert.equal((await del).status(),200);await ready(page);check('彻底删除需二次确认且刷新不再出现',confirms===2&&(await api('/api/items/'+encodeURIComponent(saved.id))).status===400);
 }
 for(const url of ['/api/items','/api/library/search?q=agent']){perf[url]=[];for(let i=0;i<5;i++){const t=performance.now();await get(url);perf[url].push(Number(((performance.now()-t)/1000).toFixed(5)));}check('列表接口5次均<1秒 '+url,perf[url].every(t=>t<1),JSON.stringify(perf[url]));}
 for(const word of ['agent','微信','MiniMax H3','4090','AI']){perf[word]=[];for(let i=0;i<3;i++){const t=performance.now();const r=await get('/api/library/search?q='+encodeURIComponent(word));perf[word].push(Number(((performance.now()-t)/1000).toFixed(5)));if(word==='AI')assert.equal(r.short_query,true);}check('资料检索3次均<1秒 '+word,perf[word].every(t=>t<1),JSON.stringify(perf[word]));}
 check('旧三个数据库SHA不变',JSON.stringify(hashes())===JSON.stringify(before));check('页面无脚本错误',errors.length===0,JSON.stringify(errors));
 }catch(e){console.error(e);process.exitCode=1;}
 finally{fs.writeFileSync(path.join(tmp,'results.json'),JSON.stringify({real:REAL,results,errors,timings,perf,oldBefore:before,oldAfter:workspace?hashes():{},savedItem:keep,evidence:out},null,2));console.log('证据 '+tmp+'; '+results.length+' 项通过');if(context)await context.close();if(child)child.kill('SIGTERM');}
})();
