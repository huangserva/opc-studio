'use strict';
const writeGuard=require('./real_write_guard.cjs');writeGuard.install();
if(process.argv.includes('--real')){writeGuard.readOnlyMain('interest_channel').catch(e=>{console.error(e);process.exitCode=1});return;}

// 独立 Chromium profile；合成模式随机端口 + /tmp 数据；--real 只读连接 8766。
const fs=require('fs'),path=require('path'),os=require('os'),net=require('net'),assert=require('assert/strict');
const {spawn}=require('child_process');
const {chromium}=(()=>{try{return require('playwright')}catch(_){return require(path.join(os.homedir(),'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright'))}})();
const ROOT=path.resolve(__dirname,'../..'),PY=path.resolve(ROOT,'../article-importer/.venv/bin/python'),REAL=false;
const tmp=fs.mkdtempSync('/tmp/interest-e2e-'),out='/tmp/impl-interest-p1';fs.mkdirSync(out,{recursive:true});
const checks=[],errors=[],mode=REAL?'real':'fixture';let context,child,base,reports,topicsBefore,topicsPath,changed=false,serverError='';
const check=(name,ok,detail='')=>{assert.ok(ok,name+' '+detail);checks.push({name,detail,passed:true});console.log('通过 '+name+' '+detail);};
const delay=ms=>new Promise(r=>setTimeout(r,ms));
async function start(){
 writeGuard.assertSandbox(path.join(tmp,'workspace'));reports=path.join(tmp,'reports');fs.mkdirSync(path.join(reports,'interest'),{recursive:true});
 fs.copyFileSync(path.join(os.homedir(),'grok-bot-reports/interest-topics.md'),path.join(reports,'interest-topics.md'));
 const stamp=new Date(Date.now()-60000).toISOString(),day=new Intl.DateTimeFormat('en-CA',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date());
 const record={id:'101',url:'https://x.com/fixture/status/101',author_handle:'fixture',author_followers:12,published_at:stamp,collected_at:stamp,topic:'Agent',what_new:'兴趣隔离验收新工具',relevance:3,full_text:'完整原文\n\n<script>window.PWNED=1</script><img src=x onerror="window.PWNED=1">',content_complete:true,views:12,usable:[{type:'repo',url:'https://example.test/repo',note:'源码仓库'},{type:'api',url:'javascript:window.PWNED=1',note:'不安全链接'}],article:{html:'<p>文章补充全文</p><script>window.PWNED=1</script>'},thread:[{text:'后续串推'}],quoted:{text:'引用完整正文'},media:[{url:'javascript:alert(1)',type:'photo'}]};
 fs.writeFileSync(path.join(reports,'interest',day+'-1030.json'),JSON.stringify({posts:[record,{id:'102',url:'https://x.com.evil.com/a/status/102',full_text:'不完整条目'}]}));
 fs.writeFileSync(path.join(reports,'interest',day+'-1031.json'),'{"posts":[');
 for(let d=0;d<7;d++)for(const hour of ['1040','2240']){const date=new Date(Date.now()-d*86400000),dateName=new Intl.DateTimeFormat('en-CA',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit'}).format(date);const rows=Array.from({length:15},(_,i)=>({...record,id:String(20000+d*1000+Number(hour)+i),url:'https://x.com/fixture/status/'+(20000+d*1000+Number(hour)+i),what_new:'性能夹具 '+d+' '+hour+' '+i,full_text:'性能全文'.repeat(250),collected_at:dateName+'T'+hour.slice(0,2)+':'+hour.slice(2)+':00+08:00'}));fs.writeFileSync(path.join(reports,'interest',dateName+'-'+hour+'.json'),JSON.stringify({posts:rows}));}
 fs.writeFileSync(path.join(reports,'interest',day+'-2358.json'),Buffer.alloc(2*1024*1024+1,120));
 const port=await new Promise(resolve=>{const server=net.createServer().listen(0,'127.0.0.1',()=>{const p=server.address().port;server.close(()=>resolve(p));});});base='http://127.0.0.1:'+port;writeGuard.registerSandbox(base,path.join(tmp,'workspace'));
 const code=`import sys;from pathlib import Path;from http.server import ThreadingHTTPServer;sys.path.insert(0,${JSON.stringify(ROOT)});import server,linuxdo_source;from tests._network_guard import NetworkGuard;guard=NetworkGuard();guard.__enter__();linuxdo_source.refresh_background=lambda root:None;root=Path(${JSON.stringify(tmp)})/'workspace';root.mkdir();s=ThreadingHTTPServer(('127.0.0.1',${port}),server.Handler);s.store=server.Store(root);s.serve_forever()`;
 child=spawn(PY,['-u','-c',code],{cwd:ROOT,env:{...process.env,GROK_BOT_REPORTS_DIR:reports,PYTHONDONTWRITEBYTECODE:'1'},stdio:['ignore','ignore','pipe']});child.stderr.on('data',b=>serverError+=b);
 for(let i=0;i<100;i++){try{if((await fetch(base+'/api/today?channel=interest')).ok)return;}catch(_){}if(child.exitCode!==null)throw Error(serverError);await delay(100);}throw Error('隔离服务未启动');
}
async function get(url){const r=await context.request.get(base+url);assert.equal(r.status(),200);return r.json();}
async function post(url,data,header=true){return context.request.post(base+url,{headers:{Origin:base,...(header?{'X-Article-Studio':'1'}:{})},data});}
async function ready(page){await page.waitForFunction(()=>document.querySelector('#item-list')?.getAttribute('aria-busy')==='false'&&!document.querySelector('#item-list').inert);}
async function interestTab(page){const loaded=page.waitForResponse(r=>new URL(r.url()).pathname==='/api/today'&&new URL(r.url()).searchParams.get('channel')==='interest');await page.locator('[data-channel=interest]').click();await loaded;await ready(page);}
(async()=>{try{
 await start();topicsPath=path.join(reports,'interest-topics.md');topicsBefore=fs.readFileSync(topicsPath);fs.writeFileSync(path.join(out,mode+'-topics-before.md'),topicsBefore);
 context=await chromium.launchPersistentContext(path.join(tmp,'profile'),{headless:true,channel:'chrome',viewport:{width:1440,height:1000},args:['--disable-background-networking','--disable-component-update']});
 await context.route('**/*',r=>{const u=new URL(r.request().url());if(u.origin!==base)return r.abort();if(!REAL&&u.pathname==='/api/schedules')return r.fulfill({json:{tasks:[]}});if(!REAL&&u.pathname==='/api/schedules/runs')return r.fulfill({json:{runs:[]}});if(!REAL&&u.pathname==='/api/assistant/settings')return r.fulfill({json:{profiles:[]}});return r.continue();});
 const page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));
 let listing;const timings=[];for(let i=0;i<5;i++){const t=performance.now();listing=await get('/api/today?channel=interest&range=7d');timings.push(performance.now()-t);}
 check('兴趣接口5次均小于1秒',timings.every(t=>t<1000),timings.map(t=>t.toFixed(1)).join(',')+' ms');
 if(REAL&&!listing.total)throw Error('真实文件验收待补：interest 尚无可读条目');
 check('兴趣报告已读取',listing.total>0,'条目数 '+listing.total);
 await page.goto(base+'/today.html');await ready(page);await interestTab(page);
 if(!(await page.locator('#item-list .item-card').count())){await page.locator('[data-range=7d]').click();await ready(page);await page.locator('#item-list .item-card').first().waitFor();}
 check('兴趣与发现列表分开',await page.locator('[data-channel=interest]').getAttribute('aria-pressed')==='true'&&await page.locator('#item-list .item-card').evaluateAll(cards=>cards.every(c=>c.querySelector('.item-tier')?.textContent==='兴趣')));
 const target=REAL?(listing.items.find(i=>i.content_complete)||listing.items[0]):listing.items.find(i=>i.id==='x:101'),id=target.id;
 const card=page.locator('#item-list .item-card[data-item-id="'+id+'"]');await card.waitFor();
 check('卡片仅有存准不准三个动作',await card.locator('.item-action').count()===3&&await card.locator('[data-action=use]').count()===0);
 check('右上角X本地平台图标与Grok渠道',await card.locator('.source-badges[data-platform=x] img.platform-logo[alt=X]').count()===1 && await card.locator('.collection-channels').textContent()==='Grok');
 check('卡片展示相关度',await card.innerText().then(t=>t.includes('相关度')));
 await page.screenshot({path:path.join(out,mode+'-interest-1440.png')});
 const detail=await get('/api/today/item?channel=interest&id='+encodeURIComponent(id));
 await card.locator('.item-summary').click();await page.locator('#reader .item-detail[data-item-id="'+id+'"]').waitFor();
 check('打开全文内容逐字一致',await page.locator('#reader .full-text').textContent()===detail.full_text);
 check('没有脚本事件和不安全链接注入',!await page.evaluate(()=>window.PWNED)&&await page.locator('#reader script,#reader [onerror],#reader a[href^="javascript:"],#item-list a[href^="javascript:"]').count()===0);
 check('资源外链均有安全rel',await page.locator('.interest-resources a').evaluateAll(as=>as.every(a=>a.rel==='noopener noreferrer'&&/^https?:/.test(a.href))));
 if(!REAL){check('article/thread/quoted补充均显示',(await page.locator('#reader').innerText()).includes('文章补充全文')&&(await page.locator('#reader').innerText()).includes('后续串推')&&(await page.locator('#reader').innerText()).includes('引用完整正文'));
  check('不完整标识和异常来源显示',await page.locator('#item-list .interest-warning').count()>0&&listing.sources.G.bad_files===2);
  for(const action of ['accurate','inaccurate']){await page.locator('#reader [data-action='+action+']').click();await page.locator('#reader [data-action='+action+'][aria-pressed=true]').waitFor();}
  check('准不准不会入库',(await get('/api/items?q='+encodeURIComponent(target.title))).total===0);
 }
 await page.locator('#reader [data-action=keep]').click();await page.locator('#reader [data-action=keep][aria-pressed=true]').waitFor();
 await page.locator('#reader [data-action=keep]').click();await page.locator('#reader .action-notice').getByText('已保存',{exact:true}).waitFor();
 check('重复点存按帖子id幂等',(await get('/api/items?q='+encodeURIComponent(target.title))).items.filter(i=>i.id===id).length===1);
 await page.screenshot({path:path.join(out,mode+'-detail-1440.png')});
 await page.setViewportSize({width:390,height:844});await page.waitForTimeout(200);await page.screenshot({path:path.join(out,mode+'-detail-390.png'),fullPage:true});
 await page.locator('.reader-back').click();await page.screenshot({path:path.join(out,mode+'-interest-390.png')});
 check('390兴趣页无横向溢出',await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
 await page.setViewportSize({width:1440,height:1000});await page.goto(base+'/library.html');await ready(page);await page.locator('#q').fill(target.title);await page.locator('#filters button[type=submit]').click();await ready(page);await page.locator('#item-list .item-card[data-item-id="'+id+'"]').waitFor();
 check('素材库搜索找到刚存的帖子',await page.locator('#item-list .item-card[data-item-id="'+id+'"]').count()===1);
 await page.screenshot({path:path.join(out,mode+'-library-1440.png')});
 await page.goto(base+'/settings.html');await page.locator('#interest-notice').getByText('已读取当前方向',{exact:true}).waitFor();
 const original=await get('/api/settings/interest-topics');
 check('POST缺X-Article-Studio被拒绝',(await post('/api/settings/interest-topics',{topics:original.topics,revision:original.revision},false)).status()===403);
 const marker='兴趣临时验收方向';await page.locator('#interest-topic-input').fill(marker);await page.locator('#interest-add-form button').click();await page.locator('#interest-save').click();await page.locator('#interest-notice').getByText('兴趣清单已保存',{exact:true}).waitFor();changed=true;
 let updated=await get('/api/settings/interest-topics');check('设置页新增已同步文件',updated.topics.includes(marker)&&fs.readFileSync(topicsPath,'utf8').includes('- '+marker));
 const suffix=bytes=>bytes.subarray(bytes.indexOf(Buffer.from('## 说明')));
 check('说明段逐字保留',suffix(fs.readFileSync(topicsPath)).equals(suffix(topicsBefore)));
 check('陈旧版本保存返回409',(await post('/api/settings/interest-topics',{topics:['不应覆盖'],revision:original.revision})).status()===409);
 await page.locator('#interest-title').scrollIntoViewIfNeeded();await page.screenshot({path:path.join(out,mode+'-settings-1440.png')});await page.setViewportSize({width:390,height:844});await page.waitForTimeout(200);await page.screenshot({path:path.join(out,mode+'-settings-390.png'),fullPage:true});
 check('390设置页无横向溢出',await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
 await page.getByRole('button',{name:'删除方向：'+marker,exact:true}).click();await page.locator('#interest-save').click();await page.locator('#interest-notice').getByText('兴趣清单已保存',{exact:true}).waitFor();changed=false;
 check('删除临时项后整份清单逐字恢复',fs.readFileSync(topicsPath).equals(topicsBefore));
 check('页面无脚本错误',errors.length===0,JSON.stringify(errors));
 fs.writeFileSync(path.join(out,mode+'-evidence.json'),JSON.stringify({checks,timings,id,total:listing.total,realFiles:REAL?fs.readdirSync(path.join(reports,'interest')).filter(f=>f.endsWith('.json')):null,errors},null,2));
 }catch(e){console.error(e);process.exitCode=1;fs.writeFileSync(path.join(out,mode+'-failure.json'),JSON.stringify({checks,errors,error:e.message},null,2));}
 finally{
  if(changed&&context){try{const current=await get('/api/settings/interest-topics');const original=topicsBefore.toString('utf8').split('## 当前方向')[1].split('## ')[0].split('\n').filter(l=>l.startsWith('- ')).map(l=>l.slice(2));const expected=[...original,'兴趣临时验收方向'];if(JSON.stringify(current.topics)===JSON.stringify(expected)){const r=await post('/api/settings/interest-topics',{topics:original,revision:current.revision});assert.equal(r.status(),200);assert.ok(fs.readFileSync(topicsPath).equals(topicsBefore));}else console.error('清单已有外部修改，未盲目覆盖');}catch(e){console.error('恢复清单失败：'+e.message);}}
  if(context)await context.close();if(child)child.kill('SIGTERM');console.log('证据 '+out+'；'+checks.length+'项通过；模式 '+mode);
 }
})();
