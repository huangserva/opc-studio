'use strict';
const fs=require('node:fs'),path=require('node:path'),os=require('node:os'),assert=require('node:assert/strict');
const {chromium}=require(path.join(os.homedir(),'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright'));
const [base,baseline,out]=process.argv.slice(2);
for(const url of [base,baseline])if(!/^http:\/\/127\.0\.0\.1:\d+$/.test(url)||[8766,3000].includes(+new URL(url).port))throw Error('只读随机端口副本');
fs.mkdirSync(out,{recursive:true});const checks=[],counts=[],negatives=[],errors=[];let ctx;
const check=(name,value)=>{assert(value,name);checks.push(name);console.log('通过 '+name)};
(async()=>{try{
ctx=await chromium.launchPersistentContext(fs.mkdtempSync(path.join(out,'profile-')),{headless:true,channel:'chrome',viewport:{width:1440,height:1000}});
await ctx.route('**/*',route=>{const r=route.request(),u=new URL(r.url());return [base,baseline].includes(u.origin)&&r.method()==='GET'?route.continue():route.abort()});
const page=await ctx.newPage();page.on('pageerror',e=>errors.push(e.message));let dialogs=0;page.on('dialog',d=>{dialogs++;d.dismiss()});
async function api(params){const r=await ctx.request.get(base+'/api/today?'+new URLSearchParams({range:'7d',...params}));assert.equal(r.status(),200);return r.json()}
async function view(params={}){await page.goto(base+'/today.html?'+new URLSearchParams({range:'7d',...params}));await page.locator('#item-list[aria-busy="false"]').waitFor();check('页面加载 '+JSON.stringify(params),!(await page.locator('.error').count()));}
for(const width of [1440,1280,390]){
await page.setViewportSize({width,height:1000});await page.goto(baseline+'/today.html');await page.locator('#item-list[aria-busy="false"]').waitFor();await page.screenshot({path:path.join(out,'before-'+width+'.png')});
await view({platform:'douyin',collection_channel:'aisa'});const card=page.locator('#item-list .item-card').first();check(width+' 抖音logo与AIsa',await card.locator('.platform-logo').getAttribute('alt')==='抖音'&&await card.locator('.collection-channels').textContent()==='AIsa');
check(width+' 无横向溢出',await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));check(width+' 身份单行且未挤出卡片',await card.evaluate(n=>{const b=n.querySelector('.source-badges'),r=b.getBoundingClientRect(),c=n.getBoundingClientRect();return r.right<=c.right&&r.left>=c.left&&b.querySelector('img').getBoundingClientRect().top===b.querySelector('span').getBoundingClientRect().top-1 || r.right<=c.right&&r.height<=20}));
await page.screenshot({path:path.join(out,'douyin-'+width+'.png')});await card.click();await page.locator('#reader .item-detail').waitFor();await page.screenshot({path:path.join(out,'detail-'+width+'.png')});check(width+' 详情也带平台渠道',await page.locator('.detail-heading .platform-logo').getAttribute('alt')==='抖音');
}
await page.setViewportSize({width:1440,height:1000});
const all=await api({});let full=[...all.items];for(let p=2;full.length<all.total;p++)full.push(...(await api({page:String(p)})).items);
for(const [name,params] of [['抖音',{platform:'douyin'}],['X',{platform:'x'}],['多平台',{platform:'x,douyin'}],['Grok',{collection_channel:'grok'}],['平台×渠道',{platform:'x',collection_channel:'aisa'}]]){
const data=await api(params);await view(params);const number=Number((await page.locator('#total').textContent()).split(' ')[0]);const expected=full.filter(i=>(!params.platform||params.platform.split(',').includes(i.platform))&&(!params.collection_channel||i.channels.includes(params.collection_channel))).length;
check(name+' 后端/页面/集合计数一致',data.total===number&&number===expected);counts.push({name,backend:data.total,page:number,expected});
check(name+' URL刷新保持筛选',Object.entries(params).every(([k,v])=>new URL(page.url()).searchParams.get(k)===v));
}
await view({});await page.locator('.platform-filter[data-platform="x"]').click();await page.locator('#item-list[aria-busy="false"]').waitFor();check('平台按钮真实多选第一项',new URL(page.url()).searchParams.get('platform')==='x');await page.locator('.platform-filter[data-platform="douyin"]').click();await page.locator('#item-list[aria-busy="false"]').waitFor();check('平台按钮真实多选第二项',new URL(page.url()).searchParams.get('platform')==='x,douyin');await page.selectOption('#collection-channel','grok');await page.locator('#item-list[aria-busy="false"]').waitFor();check('渠道下拉真实筛选',new URL(page.url()).searchParams.get('collection_channel')==='grok');
for(const source of ['G','A','V','L']){const data=await api({source});const expected=full.filter(i=>i.sources.includes(source));check('旧source='+source+'结果集合保留',data.total===expected.length&&data.items.every(i=>i.sources.includes(source)));counts.push({name:'旧source='+source,backend:data.total,expected:expected.length});}
await view({platform:'linuxdo'});check('linux.do logo+RSS',await page.locator('.platform-logo[alt="linux.do"]').count()>0&&await page.locator('.collection-channels').first().textContent()==='RSS');await page.screenshot({path:path.join(out,'linuxdo.png')});
await view({channel:'interest',collection_channel:'wechat'});check('兴趣微信工具卡有身份标识',await page.locator('#item-list .source-badges').count()>0);await page.screenshot({path:path.join(out,'interest-wechat.png')});
await view({channel:'interest',platform:'x',collection_channel:'grok'});check('兴趣平台×渠道均可筛',Number((await page.locator('#total').textContent()).split(' ')[0])===(await api({channel:'interest',platform:'x',collection_channel:'grok'})).total);
await view({channel:'official'});check('官方号三栏及官方标签继承',await page.locator('[data-channel]').allTextContents().then(v=>v.join('/')==='发现/兴趣/官方号')&&await page.locator('.item-official').count()>0);
const official=await api({channel:'official'});const droid=official.items.find(i=>i.id==='x:2115934579446182213');assert(droid);await page.locator('[data-item-id="'+droid.id+'"]').click();await page.locator('#reader .full-text').waitFor();check('真实Droid实体已解码',await page.locator('#reader .full-text').textContent().then(t=>t.includes('>more accurate')&&!t.includes('&gt;more accurate')));await page.screenshot({path:path.join(out,'droid-decoded.png')});
const outline=full.find(i=>i.id==='x:2115937378674372783')||full.find(i=>i.sources.includes('G'));
for(const width of [1440,390]){await page.setViewportSize({width,height:1000});await view({q:outline.author.handle});const matching=page.locator('[data-item-id="'+outline.id+'"]').first();await matching.click();await page.locator('#reader .item-detail').waitFor();check(width+' 真实提纲按序列表',await page.locator('#reader ol.analysis-list').count()>0);await page.screenshot({path:path.join(out,'outline-'+width+'.png')});}
// 统一方向、各栏口径和切栏加载态。
for(const tab of ['hot','interest','official']) {
 const data=await api({channel:tab});await view({channel:tab});
 const labels=await page.locator('#topic option').allTextContents();check(tab+' 下拉无内部英文键',labels.every(v=>!/[a-z_]+/.test(v.replace(/Agent|Skill|OpenCode|API|AI|3D/g,''))));
 check(tab+' 方向每项带数且去重',labels.slice(1).every(v=>/（[0-9]+）$/.test(v))&&new Set(labels).size===labels.length);
 check(tab+' 待归类固定最后',!labels.some(v=>v.startsWith('待归类'))||labels.at(-1).startsWith('待归类'));
 const allRows=[];for(let n=1;allRows.length<data.total;n++)allRows.push(...(await api({channel:tab,page:String(n)})).items);
 for(const option of data.topic_options){const selected=await api({channel:tab,topic:option.key});check(tab+' 方向计数 '+option.key,selected.total===option.count);}
 if(tab==='hot'){const main=data.filtering.items.filter(x=>x.hidden&&!x.sources.includes('AIsa 51 人全量推文'));check('主列表过滤摘要不含51人原始采样',data.filtering.total===new Set(main.map(x=>x.id)).size);}
 if(tab==='official')check('官方号摘要注明完整收录口径',(await page.locator('#filtered-summary').textContent()).includes('官方号栏显示全部'));
}
const modern=await api({topic:'agent-coding'});for(const raw of ['claude_code','Agent与Coding','Agent 编程工具（Claude Code、Codex、MCP）']){const selected=await api({topic:raw});check('旧topic别名兼容 '+raw,selected.total===modern.total);}
for(const width of [1440,390]){await page.setViewportSize({width,height:1000});await view({channel:'interest'});await page.locator('#topic').focus();await page.keyboard.press('Space');await page.screenshot({path:path.join(out,'topics-'+width+'.png')});await page.keyboard.press('Escape');}
await view({channel:'official',range:'today'});const officialToday=await api({channel:'official',range:'today'});if(officialToday.total===0)check('官方号今天空态给切换建议',(await page.locator('#item-list .top-message').textContent()).includes('今天没有官方号帖子，可切 48 小时 / 7 天'));
// 共用组件反例：四栏独立实例，实体仅解一层，所有内容均为纯文本。
const components=await page.evaluate(async()=>{
 const m=await import('/components/item-card.js');const root=document.createElement('section');root.id='r4-matrix';document.body.append(root);const results=[];
 const cases=[['&gt;','>'],['&lt;','<'],['&amp;','&'],['&quot;','"'],['&#39;',"'"],['&#x3C;','<'],['&amp;gt;','&gt;'],['&amp;amp;','&amp;'],['&lt;script&gt;alert(1)&lt;/script&gt;','<script>alert(1)</script>'],['&lt;img src=x onerror=alert(1)&gt;','<img src=x onerror=alert(1)>']];
 for(const tab of ['hot','interest','official','library'])for(const [raw,expected] of cases){const item={id:'x:test',kind:'post',channel:tab,title:raw,full_text:raw,author:{},metrics:{},why:raw};const card=m.renderPostCard(item,{actions:false}),detail=m.renderPostDetail(item);root.append(card,detail);results.push({name:tab+' '+raw,ok:card.querySelector('.item-summary').textContent===expected&&detail.querySelector('.full-text').textContent===expected&&card.querySelectorAll('.item-summary script,.item-summary img').length===0&&detail.querySelectorAll('.full-text script,.full-text img').length===0});card.remove();detail.remove();}
 const shapes=[['字符串','一段话'],['字符串数组',['第一','第二']],['对象数组',[{title:'标题',text:'正文',url:'https://example.com/x',extra:'额外'}]],['混合数组',['第一',{text:'第二'},3]],['嵌套数组',[['第一',['第二',['第三']]]]],['空数组',[]],['null',null],['数字',42],['注入',[{title:'<script>alert(1)</script>',url:'javascript:alert(1)',text:'<img src=x onerror=alert(1)>'}]]];
 for(const tab of ['hot','interest','official','library'])for(const [name,value] of shapes){const detail=m.renderPostDetail({id:'x:shapes',channel:tab,author:{},metrics:{},outline:value,why:value});root.append(detail);const text=detail.textContent;const empty=value===null||Array.isArray(value)&&!value.length;results.push({name:tab+' '+name,ok:empty?![...detail.querySelectorAll('h3')].some(h=>['提纲','为什么爆','为什么值得收'].includes(h.textContent)):!text.includes('["')&&!text.includes('\\"')&&detail.querySelectorAll('script,.analysis-list img,a[href^="javascript:"]').length===0&&[...detail.querySelectorAll('.analysis-list a')].every(a=>a.rel==='noopener noreferrer')&&(!Array.isArray(value)||!!detail.querySelector('ol.analysis-list'))});detail.remove();}
 root.remove();return results;
});for(const result of components)check('反例 '+result.name,result.ok);check('无XSS弹窗',dialogs===0);
// 旧组件负对照：同一个断言须测出旧行为错误。
await page.route('**/components/r4-old.js',async route=>{const r=await ctx.request.get(baseline+'/components/item-card.js');return route.fulfill({contentType:'text/javascript',body:await r.text()})});
await page.goto(baseline+'/today.html');await page.locator('#item-list[aria-busy="false"]').waitFor();const oldTopics=await page.locator('#topic option').allTextContents();negatives.push({name:'旧方向英文键',passed:oldTopics.every(v=>!(/^[a-z_]+$/).test(v))});check('旧方向不出现英文键应报F',oldTopics.some(v=>(/^[a-z_]+$/).test(v)));await page.goto(base+'/today.html?range=7d');await page.locator('#item-list[aria-busy="false"]').waitFor();
const old=await page.evaluate(async()=>{const m=await import('/components/r4-old.js');const value=m.renderPostDetail({id:'x:old',author:{},metrics:{},full_text:'&gt;',outline:['第一','第二']});return {entity:value.querySelector('.full-text').textContent==='>',outline:!!value.querySelector('ol.analysis-list')}});for(const [name,passed] of Object.entries(old)){negatives.push({name,passed});check('旧组件应报F '+name,passed===false)}
for(const params of [{platform:'<script>'},{platform:'x'.repeat(201)},{collection_channel:'javascript:alert(1)'},{source:'bad'}]){const r=await ctx.request.get(base+'/api/today?'+new URLSearchParams(params));check('非法筛选拒绝 '+Object.keys(params)[0],r.status()===400)}
check('无页面脚本错误',errors.length===0);fs.writeFileSync(path.join(out,'results.json'),JSON.stringify({checks,counts,negatives,errors},null,2));console.log(checks.length+' checks passed');
}finally{if(ctx)await ctx.close()}})().catch(e=>{fs.writeFileSync(path.join(out,'failure.json'),JSON.stringify({error:e.stack,checks,counts,errors},null,2));console.error(e);process.exitCode=1});
