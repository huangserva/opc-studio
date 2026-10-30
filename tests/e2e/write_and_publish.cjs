'use strict';
const writeGuard=require('./real_write_guard.cjs');writeGuard.install();
if(process.argv.includes('--real')){writeGuard.readOnlyMain('write_and_publish').catch(e=>{console.error(e);process.exitCode=1});return;}

// 隔离 headless profile；默认随机端口真实 Handler/Store + 可控假模型；--real 验收 8766 当前模型。
const fs=require('fs'),path=require('path'),os=require('os'),net=require('net'),assert=require('assert/strict');
const {spawn,execFileSync}=require('child_process');
const {chromium}=(()=>{try{return require('playwright')}catch(_){return require(path.join(os.homedir(),'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright'))}})();
const ROOT=path.resolve(__dirname,'../..'),PY=path.resolve(ROOT,'../article-importer/.venv/bin/python'),REAL=false;
const tmp=fs.mkdtempSync('/tmp/write-p3-e2e-'),out=path.join(ROOT,'coordination/impl-write-p3-screenshots',REAL?'real':'isolated');fs.mkdirSync(out,{recursive:true});
const long='首行😀 <script>window.INJECTED=1</script> <img src=x onerror=alert(1)> &amp;\n\n'+'完整中文原文与参数'.repeat(350)+'\n末行';
const iso=h=>new Date(Date.now()-h*3600000).toISOString(),results=[],errors=[],timings={},artifacts=[];
let child,context,base,workspace,remoteRequests=0,syncRequests=0;
const check=(name,ok,detail='')=>{assert.ok(ok,name+' '+detail);results.push({name,passed:true,detail});console.log('通过 '+name+(detail?' '+detail:''))};
async function api(url,data,method=data===undefined?'GET':'POST',headers){const r=await context.request.fetch(base+url,{method,headers:headers??(method==='GET'?{}:{Origin:base,'X-Article-Studio':'1'}),...(data===undefined?{}:{data})});const body=await r.json();return {status:r.status(),body};}
async function get(url){const r=await api(url);assert.equal(r.status,200,JSON.stringify(r.body));return r.body;}
async function post(url,data){const r=await api(url,data);assert.ok(r.status<300,JSON.stringify(r.body));return r.body;}
const draftPath=id=>'/api/items/'+encodeURIComponent(id)+'/draft',statusPath=id=>'/api/items/'+encodeURIComponent(id)+'/draft-status';
async function state(id){for(let i=0;i<380;i++){const s=await get(statusPath(id));if(s.status!=='pending')return s;await new Promise(r=>setTimeout(r,500))}throw Error('起草超过测试时限');}
async function ready(page){await page.waitForFunction(()=>document.querySelector('#item-list')?.getAttribute('aria-busy')==='false');}
async function select(page,id){await page.locator('.item-card[data-item-id="'+id+'"]').click();await page.locator('#reader .item-detail[data-item-id="'+id+'"]').waitFor();}
async function clickWrite(page,shift=false){const p=page.waitForEvent('popup');await page.locator('#reader [data-action=use]').click({modifiers:shift?['Shift']:[]});const popup=await p;await popup.waitForURL(u=>u.searchParams.has('article'));await popup.locator('.article-body').waitFor();return popup;}
function release(id){fs.writeFileSync(path.join(tmp,id+'.release'),'release')}
async function start(){
 workspace=path.join(tmp,'workspace');writeGuard.assertSandbox(workspace);const reports=path.join(tmp,'reports');fs.mkdirSync(path.join(reports,'daily'),{recursive:true});fs.mkdirSync(workspace,{recursive:true});
 fs.writeFileSync(path.join(reports,'daily/p3.json'),JSON.stringify({collected_at:iso(.01),posts:[{id:'9001',handle:'fixture',author:'素材作者',text:long,url:'https://x.com/fixture/status/9001',created_at:iso(1),label:'爆款',topic:'Agent',views:24000,likes:120,bookmarks:19,why:'真实流程带参数',pattern:'保留失败条件和输入输出',fit_score:3,doable:3,my_angle:'从收藏到独立起草',outline:['真实问题','我亲手跑流程','工具参数链接']}]}));
 fs.writeFileSync(path.join(tmp,'bundle.json'),JSON.stringify({tweets:[],vip_tweets:[],updated_at:iso(0)}));
 fs.writeFileSync(path.join(tmp,'model.json'),JSON.stringify({model:'fixture',endpoint:'http://127.0.0.1:1/v1/chat/completions',no_api_key:true,timeout_seconds:60,max_output_tokens:8192}));
 const port=await new Promise(resolve=>{const s=net.createServer().listen(0,'127.0.0.1',()=>{const p=s.address().port;s.close(()=>resolve(p))})});base='http://127.0.0.1:'+port;writeGuard.registerSandbox(base,workspace);
 const run=`import sys,time,json,os;from pathlib import Path;sys.path.insert(0,${JSON.stringify(ROOT)});import server,grok_bot_radar,write_from_item,pi_runtime,linuxdo_source;linuxdo_source.refresh_background=lambda root:None;from items_store import ItemsStore;from tests.test_write_from_item import answer;import publishing.x_official as xo;tmp=Path(${JSON.stringify(tmp)});root=Path(${JSON.stringify(workspace)});grok_bot_radar.BUNDLE_PATH=tmp/'bundle.json'\ndef no_remote(*a,**k):\n with (tmp/'remote-calls').open('a') as f:f.write('unexpected\\n')\n raise AssertionError('禁止真实 X 调用')\nxo.remote=no_remote\ndef fake(self,item):\n if item['id']=='x:9003':raise RuntimeError('模型不可用')\n while not (tmp/(item['id']+'.release')).exists():time.sleep(.02)\n if write_from_item.draft_kind(item)=='thread':return json.dumps({'title':'【验收】我准备把收藏接进写作流程','parts':['我的问题是收藏越来越多，却总要重新找原文。\\n\\n【待补：我的实测数据】','我准备先跑通从素材到底稿的流程，再记录输入、输出和冲突处理。现在还没有亲测结论。','工具和参数\\n\\n来源链接：'+item.get('url','')+'\\n\\n引用 @fixture 的思路，等我亲手跑完再补截图和数据。']},ensure_ascii=False)\n return answer(item)\nwrite_from_item.ItemWriter._model=fake\npi_runtime.PiRuntime.run=lambda self,job,system,tools:{'answer':'聊天正常开始，起草没有占用助手锁。','status':'done'}\ns=ItemsStore(root)\nfor key in ('9002','9003'):s.upsert({'id':'x:'+key,'title':'【验收】'+key+' 素材','kind':'article','full_text':'独立素材内容 '+key,'sources':['G']})\nsys.argv=['server.py','--workspace',str(root),'--port',str(${port})];server.main()`;
 child=spawn(PY,['-u','-c',run],{cwd:ROOT,env:{...process.env,PYTHONDONTWRITEBYTECODE:'1',GROK_BOT_REPORTS_DIR:reports,ARTICLE_STUDIO_MODEL_CONFIG:path.join(tmp,'model.json')},stdio:['ignore','pipe','pipe']});let log='';child.stdout.on('data',b=>log+=b);child.stderr.on('data',b=>log+=b);child.on('exit',()=>fs.writeFileSync(path.join(out,'server.log'),log));
 for(let i=0;i<160;i++){try{if((await fetch(base+'/api/today')).ok)return}catch(_){}if(child.exitCode!==null)throw Error(log);await new Promise(r=>setTimeout(r,100))}throw Error(log);
}
(async()=>{try{
 await start();context=await chromium.launchPersistentContext(path.join(tmp,'profile'),{headless:true,channel:'chrome',viewport:{width:1440,height:900},args:['--disable-background-networking','--disable-component-update']});
 await context.route('**/*',async route=>{const request=route.request(),url=new URL(request.url());if(url.origin!==base){if(/(?:api\.)?(?:x|twitter)\.com$/.test(url.hostname))remoteRequests++;return route.abort();}
  if(url.pathname.startsWith('/api/x-official/')){
   if(url.pathname.endsWith('/feedback-sync')){syncRequests++;if(REAL)throw Error('真实验收禁止 X 同步');return route.fulfill({contentType:'application/json',body:'{}'});}
   if(url.pathname.endsWith('/status')&&!REAL)return route.fulfill({contentType:'application/json',body:JSON.stringify({connected:true,account:{id:'42'}})});
  }
  if(REAL&&url.pathname.startsWith('/api/items/')&&url.pathname.endsWith('/draft')&&request.method()==='POST')return route.continue({postData:JSON.stringify({...request.postDataJSON(),title_prefix:'【验收】'})});
  return route.continue();
 });
 context.on('page',p=>p.on('pageerror',e=>errors.push(e.message)));
 const page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));await page.goto(base+'/today.html');await ready(page);
 const today=await get('/api/today?range=48h');const target=REAL?today.items.find(p=>p.kind==='post'&&p.sources.includes('G')):today.items.find(p=>p.id==='x:9001');assert.ok(target,'今日必须有可写素材');const original=await get('/api/today/item?id='+encodeURIComponent(target.id));
 if(!REAL){const count=(await get('/api/library')).articles.length;for(const headers of [{'X-Article-Studio':'1'},{Origin:'https://evil.example','X-Article-Studio':'1'},{Origin:base}]){const r=await api(draftPath(target.id),{from_today:true},'POST',headers);check('来源校验403 '+JSON.stringify(headers),r.status===403);}check('非法请求没有建作品',(await get('/api/library')).articles.length===count);}
 await select(page,target.id);const began=performance.now(),opened=await clickWrite(page,REAL),slug=new URL(opened.url()).searchParams.get('article');timings.base_ms=Math.round(performance.now()-began);artifacts.push(slug);
 check('默认弹窗策略打开指定作品且底稿可见≤3秒',timings.base_ms<=3000&&await opened.locator('.article-body').innerText().then(t=>t.includes(original.full_text)),String(timings.base_ms)+' ms');
 const item=await get('/api/items/'+encodeURIComponent(target.id));const baseDraft=await get('/api/article/'+slug+'?version=v0002');const baseText=baseDraft.blocks.map(b=>b.text||'').join('\n');
 check('底稿真含全文、数据、为什么爆、角度、提纲',baseText.includes(original.full_text)&&baseText.includes(String(original.metrics.likes))&&baseText.includes(original.why)&&baseText.includes('我的切入角度')&&baseText.includes('提纲'));
 check('底稿版本v0002且双向来源一致',baseDraft.version.id==='v0002'&&item.used_work_id===slug&&JSON.parse(fs.readFileSync(path.join(workspace,'library',slug,'source-item.json'))).item_id===target.id);
 check('原文脚本与img未生成执行节点',await opened.locator('.article-body script,.article-body img').count()===0&&await opened.evaluate(()=>window.INJECTED===undefined));
 await opened.screenshot({path:path.join(out,'01-base.png')});
 if(!REAL){
  check('后台起草pending且助手active为空',(await get(statusPath(target.id))).status==='pending'&&(await get('/api/assistant')).active_job===null);
  await opened.locator('#assistant-toggle').click();await opened.locator('#assistant-input').fill('请解释这个素材的输入输出，不要修改作品');const sent=opened.waitForResponse(r=>new URL(r.url()).pathname==='/api/assistant/send');await opened.locator('#assistant-send').click();check('起草中真实聊天框仍能正常开始',(await sent).status()===202);await opened.locator('.assistant-answer').getByText('聊天正常开始，起草没有占用助手锁。',{exact:true}).waitFor();await opened.locator('#assistant-toggle').click();
  const reused=[];for(let i=0;i<3;i++){const popup=await clickWrite(page);reused.push(new URL(popup.url()).searchParams.get('article'));await popup.close();}check('同一素材重复点击3次只建一个作品',reused.every(s=>s===slug)&&(await get('/api/library')).articles.length===1);
  release(target.id);
 }
 const done=await state(target.id);check('AI真实保存新正文而非原文复制',done.status==='done',JSON.stringify(done));timings.ai_seconds=done.elapsed;
 const ai=await get('/api/article/'+slug);const body=ai.blocks.map(b=>b.text||'').join('\n');check('AI版本v0003、亲测占位、禁词和原文复制检查',ai.version.id==='v0003'&&body.includes('【待补：我的实测数据】')&&body!==original.full_text&&!/🔥|🚀|炸裂|最狠|说句大实话|兄弟们直接冲|[0-9#*]\uFE0F?\u20E3/.test(body));
 await opened.reload();await opened.locator('.article-body').waitFor();await opened.screenshot({path:path.join(out,'02-ai.png')});
 const before=(await get('/api/publish/review')).total;
 if(!REAL){
  const shifted=await clickWrite(page,true);const shiftedSlug=new URL(shifted.url()).searchParams.get('article');artifacts.push(shiftedSlug);check('Shift点击才另建新稿',shiftedSlug!==slug);await shifted.close();await state(target.id);
  await page.goto(base+'/library.html?item=x%3A9002');await page.locator('#reader [data-action=use]').waitFor();const conflictPopup=await clickWrite(page),conflictSlug=new URL(conflictPopup.url()).searchParams.get('article');artifacts.push(conflictSlug);
  const pending=await get(statusPath('x:9002'));await post('/api/save/'+conflictSlug,{base_version:pending.base_version,title:'黄总的手写标题',blocks:[{kind:'text',html:'<p>人工修改必须保留，这是已修改的正文。</p>'}]});release('x:9002');check('后台遇人工修改变conflict',(await state('x:9002')).status==='conflict');await page.locator('#reader .action-notice').getByText('AI 初稿已另存，没有覆盖你的修改',{exact:true}).waitFor();
  check('人工内容不覆盖且ai-draft.md真含初稿',(await get('/api/article/'+conflictSlug)).blocks[0].text.includes('人工修改必须保留')&&fs.readFileSync(path.join(workspace,'library',conflictSlug,'ai-draft.md'),'utf8').includes('【待补：我的实测数据】'));await page.screenshot({path:path.join(out,'03-conflict.png')});await conflictPopup.close();
  await page.goto(base+'/library.html?item=x%3A9003');await page.locator('#reader [data-action=use]').waitFor();const failPopup=await clickWrite(page),failSlug=new URL(failPopup.url()).searchParams.get('article');artifacts.push(failSlug);check('模型失败底稿仍在且原因可读',(await state('x:9003')).status==='failed'&&(await get('/api/article/'+failSlug)).version.id==='v0002');await page.locator('#reader .action-notice').getByText('模型调用失败，底稿保留；请检查当前模型配置或稍后重试',{exact:true}).waitFor();await failPopup.close();
  const record=await post('/api/record/'+slug,{platform:'x',version:ai.version.id,url:'https://x.com/fixture/status/9999',account:'fixture'});
  const collected=iso(.01);await post('/api/feedback/import',{slug,delivery_id:record.id,platform:'x',version:ai.version.id,collected_at:collected,range:{kind:'cumulative',start:'',end:collected,label:'验收快照'},metrics:{Likes:42,Views:'1200',Replies:3,Bookmarks:0},provenance:{type:'manual',url:'https://x.com/fixture/status/9999',note:'隔离夹具'}});
  await post('/api/feedback/review/'+slug+'/notes',{notes:'这次复盘先检查正文和来源，不能只看数字。'});
 }
 const liveCount=fs.readdirSync(path.join(workspace,'library')).reduce((n,name)=>{const p=path.join(workspace,'library',name,'deliveries.json');if(!fs.existsSync(p))return n;const data=JSON.parse(fs.readFileSync(p));return n+data.filter(r=>['published_manual','published_browser','published_x_api','buffer_published'].includes(r.kind)).length},0);
 const review=await get('/api/publish/review');check('真实库已发布总数与聚合数一致',review.total===liveCount,liveCount+' / '+review.total);
 await page.goto(base+'/publish.html');await page.locator('#published-list[aria-busy=false]').waitFor();check('发布复盘实际卡片数与真实库一致',await page.locator('.published-card').count()===liveCount,String(liveCount));
 check('近7天两个数字与API一致',Number(await page.locator('#week-count').textContent())===review.week_count&&Number(await page.locator('#week-items').textContent())===review.week_from_items);
 if(!REAL){
  check('手动登记的发布新增且来源标题和图标可见',review.total===before+1&&await page.locator('.review-source').textContent().then(t=>t.includes(original.title))&&await page.locator('.review-source .source-badge').count()>0);
  check('无曝光显示未知并说明原因',await page.getByText('曝光 未知',{exact:true}).first().getAttribute('title').then(t=>t.includes('未提供')));
  await page.locator('.published-card').first().click();await page.getByText('这次复盘先检查正文和来源，不能只看数字。',{exact:true}).waitFor();check('详情真含快照和只读备注',await page.locator('.snapshot-table tbody tr').count()===1&&(await page.locator('#review-detail').textContent()).includes('Likes 42')&&await page.locator('#review-detail textarea').count()===0);
  const link=page.locator('.review-source a');await link.click();await page.locator('#reader .item-detail').waitFor();check('来源链接能定位素材全文',await page.locator('#reader .full-text').textContent()===long);
  await page.goto(base+'/publish.html');await page.locator('#published-list[aria-busy=false]').waitFor();
 }
 check('加载复盘页面不会自动同步X',syncRequests===0&&remoteRequests===0&&!fs.existsSync(path.join(tmp,'remote-calls')));
 await page.screenshot({path:path.join(out,'04-publish.png')});
 const perf=[];for(let i=0;i<5;i++){const t=performance.now();await get('/api/publish/review');perf.push(Math.round((performance.now()-t)*10)/10)}timings.review_ms=perf;check('复盘接口5次均<1秒',perf.every(v=>v<1000),JSON.stringify(perf)+' ms');
 if(!REAL){await page.locator('#sync').click();await page.getByText('同步完成',{exact:true}).waitFor();check('仅点击同步按钮才调用一次feedback-sync',syncRequests===1&&remoteRequests===0);}
 check('无页面未捕获脚本异常',errors.length===0,JSON.stringify(errors));
 }catch(error){console.error(error.stack);results.push({name:'执行异常',passed:false,error:String(error)});process.exitCode=1;
 }finally{if(context)await context.close();if(child){if(child.exitCode===null){child.kill('SIGTERM');await new Promise(r=>child.once('exit',r));}}fs.writeFileSync(path.join(out,'results.json'),JSON.stringify({real:REAL,results,timings,artifacts,remoteRequests,syncRequests,tmp,workspace},null,2));console.log('证据 '+out+'；通过 '+results.filter(r=>r.passed).length+' 项');}
})();
