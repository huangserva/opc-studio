"use strict";
const fs=require('node:fs'),path=require('node:path'),os=require('node:os');
const {spawn}=require('node:child_process');
const assert=require('node:assert/strict');
const readline=require('node:readline');
const ROOT=path.resolve(__dirname,'../..');
const PYTHON=process.env.COMPARE_TEST_PYTHON||path.resolve(ROOT,'../article-importer/.venv/bin/python');
function playwright(){try{return require('playwright');}catch{return require(path.join(os.homedir(),'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright'));}}
(async()=>{
 const evidence=process.env.COMPARE_TEST_EVIDENCE||path.join(ROOT,'coordination/evidence/compare-rich-inline');fs.mkdirSync(evidence,{recursive:true});
 const fixture=spawn(PYTHON,[path.join(__dirname,'compare_rich_inline_fixture.py')],{cwd:ROOT,stdio:['ignore','pipe','pipe']});
 let logs='',browser;fixture.stderr.on('data',x=>logs+=x);
 const lines=readline.createInterface({input:fixture.stdout});
 try{
  const config=await new Promise((resolve,reject)=>{const timer=setTimeout(()=>reject(new Error('夹具未启动：'+logs)),10000);lines.once('line',line=>{clearTimeout(timer);resolve(JSON.parse(line));});fixture.once('exit',code=>{clearTimeout(timer);reject(new Error('夹具退出 '+code+': '+logs));});});
  const {base,slug,before}=config;assert.notEqual(new URL(base).port,'8766');
  browser=await playwright().chromium.launch({channel:'chrome',headless:true,args:['--disable-background-networking','--disable-component-update']});
  const context=await browser.newContext({viewport:{width:1440,height:1000}}),page=await context.newPage();
  const errors=[],external=[],ancillary=[],httpErrors=[];
  page.on('pageerror',e=>errors.push(e.message));page.on('response',r=>{if(r.status()>=500)httpErrors.push({url:r.url(),status:r.status()});});
  await context.route('**/*',route=>{
   const u=new URL(route.request().url());if(u.origin!==base){external.push(u.origin+u.pathname);return route.abort();}
   if(u.pathname.startsWith('/api/')&&!/^\/api\/(library$|article\/|compare\/|save\/)/.test(u.pathname)){
    ancillary.push(u.pathname);return route.fulfill({status:200,contentType:'application/json',body:JSON.stringify({connected:false,available:false,threads:[],tasks:[],jobs:[],platforms:[],capabilities:[],active_job:null,settings:{},error:'隔离验收不启用其他服务'})});
   }return route.continue();
  });
  await page.goto(base+'/?article='+slug);await page.locator('#edit').waitFor({state:'visible'});
  async function edit(markup,add=false){
   await page.click('#view-read');await page.click('#edit');if(add)await page.click('#add-paragraph');
   const ed=add?page.locator('#paper .text-block').last():page.locator('#paper .text-block').first();
   await ed.click();await ed.press('Meta+A');
   // 使用 Chrome 的真实富文本编辑操作，触发 input，再用正式保存按钮落盘。
   await ed.evaluate((el,html)=>{el.focus();const selection=window.getSelection(),range=document.createRange();range.selectNodeContents(el);selection.removeAllRanges();selection.addRange(range);document.execCommand('insertHTML',false,html);},markup);
   await page.click('#save');await page.fill('#summary','富文本对比验收');
   const response=page.waitForResponse(r=>r.url().includes('/api/save/')&&r.request().method()==='POST');
   await page.click('#confirm-save');const receipt=await (await response).json();
   await page.locator('#save-state').filter({hasText:'已保存到本机'}).waitFor();return receipt.version;
  }
  async function compare(a,b){await page.click('#view-diff');await page.locator('#compare-after').waitFor();await page.selectOption('#compare-after',b);await page.selectOption('#compare-before',a);await page.waitForFunction(({a,b})=>document.querySelector('#compare-before')?.value===a&&document.querySelector('#compare-after')?.value===b,{a,b});}
  const after=await edit('<p data-align="center"><span data-color="red" data-hl="yellow">甲新乙</span></p>');
  await compare(before,after);await page.locator('#comparison .added [data-color=red] ins').waitFor();
  const replacement=await page.locator('#comparison').evaluate(el=>{
   const span=el.querySelector('.added [data-color=red]'),p=el.querySelector('.added [data-align=center]'),ins=span.querySelector('ins');
   return {color:getComputedStyle(span).color,background:getComputedStyle(span).backgroundColor,align:getComputedStyle(p).textAlign,insColor:getComputedStyle(ins).color,insBackground:getComputedStyle(ins).backgroundColor,insText:ins.textContent,delText:el.querySelector('del')?.textContent,html:el.innerHTML};});
  assert.equal(replacement.color,'rgb(214, 48, 49)');assert.equal(replacement.background,'rgb(255, 243, 173)');assert.equal(replacement.align,'center');assert.equal(replacement.insColor,replacement.color);assert.equal(replacement.insText,'新');assert.equal(replacement.delText,'旧');
  await page.screenshot({path:path.join(evidence,'replacement.png'),fullPage:true});
  const blue=await edit('<p data-align="center"><span data-color="blue" data-hl="yellow">甲新乙</span></p>');
  await compare(after,blue);await page.locator('#comparison .added [data-color=blue]').waitFor();
  const formatOnly=await page.locator('#comparison').evaluate(el=>({label:el.querySelector('.change-caption').textContent,before:getComputedStyle(el.querySelector('.removed [data-color=red]')).color,after:getComputedStyle(el.querySelector('.added [data-color=blue]')).color,marks:el.querySelectorAll('ins,del').length}));
  assert.match(formatOnly.label,/格式修改/);assert.equal(formatOnly.before,'rgb(214, 48, 49)');assert.equal(formatOnly.after,'rgb(47, 111, 228)');assert.equal(formatOnly.marks,0);
  const inserted=await edit('<p><span data-hl="yellow">无颜色高亮</span></p>',true);
  await compare(blue,inserted);await page.locator('#comparison .added [data-hl=yellow]').waitFor();
  const insertedResult=await page.locator('#comparison .added [data-hl=yellow]').evaluate(el=>({color:getComputedStyle(el).color,background:getComputedStyle(el).backgroundColor,richWrapper:!!el.closest('.rich-inline-diff'),text:el.textContent}));
  assert.equal(insertedResult.background,'rgb(255, 243, 173)');assert.equal(insertedResult.richWrapper,false);assert.equal(insertedResult.text,'无颜色高亮');
  let baseline=null;
  if(process.env.COMPARE_BASELINE_CSS){
   const css=fs.readFileSync(process.env.COMPARE_BASELINE_CSS,'utf8');await page.evaluate(css=>{document.querySelector('link[href="/style.css"]').disabled=true;const s=document.createElement('style');s.id='baseline-css';s.textContent=css;document.head.append(s);},css);
   baseline=await page.locator('#comparison .added [data-hl=yellow]').evaluate(el=>({color:getComputedStyle(el).color,background:getComputedStyle(el).backgroundColor}));
   assert.equal(insertedResult.color,baseline.color);assert.equal(insertedResult.background,baseline.background);
   await page.evaluate(()=>{document.querySelector('#baseline-css').remove();document.querySelector('link[href="/style.css"]').disabled=false;});
  }
  await page.screenshot({path:path.join(evidence,'inserted-no-color.png'),fullPage:true});
  assert.deepEqual(errors,[]);assert.deepEqual(external,[]);assert.deepEqual(httpErrors,[]);
  fs.writeFileSync(path.join(evidence,'result.json'),JSON.stringify({config,channel:'chrome',replacement,formatOnly,inserted:insertedResult,baseline,pageerrors:errors,external,httpErrors,ancillary:[...new Set(ancillary)]},null,2));
  console.log('PASS Chrome：替换红字黄底居中 + ins/del；只改格式红→蓝；新增块/无文字色高亮与基座 CSS 一致；0 pageerror。');
 }finally{if(browser)await browser.close();lines.close();fixture.kill('SIGTERM');await new Promise(resolve=>{if(fixture.exitCode!==null||fixture.signalCode!==null)return resolve();fixture.once('exit',resolve);});fs.writeFileSync(path.join(evidence,'fixture.log'),logs);}
})().catch(e=>{console.error(e);process.exitCode=1;});
