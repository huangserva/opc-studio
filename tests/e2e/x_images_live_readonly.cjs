// Read-only production acceptance: local cache + public CDN images, no X API sync/write.
const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const http=require('node:http');
function allowed(url,method,origin){
 const u=new URL(url);
 return method==='GET' && u.origin===origin &&
  (!u.pathname.startsWith('/api/') || ['/api/x-official/feedback','/api/x-official/status','/api/x-official/image','/api/feedback','/api/feedback/observations','/api/feedback/snapshots','/api/feedback/reviews'].includes(u.pathname));
}
async function protect(context,origin,blocked){
 await context.route('**/*',async route=>{
  const r=route.request();
  if(!allowed(r.url(),r.method(),origin)){blocked.push({method:r.method(),url:r.url()});return route.abort('blockedbyclient');}
  // Browser redirects can bypass a second route callback: never fulfill a redirect.
  const response=await route.fetch({maxRedirects:0,maxRetries:0});
  if(response.status()>=300&&response.status()<400){
   blocked.push({method:r.method(),url:r.url(),status:response.status(),location:response.headers().location});
   await response.dispose();
   return route.abort('blockedbyclient');
  }
  await route.fulfill({response});
  await response.dispose();
 });
}
async function selfTest(){
 const received=[];
 const server=http.createServer((req,res)=>{
  received.push(req.method+' '+req.url);
  if(req.url==='/redirect-probe'){res.writeHead(302,{Location:'/api/x-official/feedback-sync'});return res.end();}
  res.setHeader('Content-Type','text/html');
  res.end('<html>synthetic guard probe</html>');
 });
 await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
 const origin='http://127.0.0.1:'+server.address().port;
 const browser=await chromium.launch();
 try{
  const context=await browser.newContext({serviceWorkers:'block'}),blocked=[];
  await protect(context,origin,blocked);
  const page=await context.newPage();
  await page.goto(origin+'/feedback.html');
  await page.evaluate(async()=>{
   await Promise.all([
    ['/api/x-official/configure','POST'],['/unrelated-write','PUT'],
    ['/api/x-official/feedback-sync','GET'],['/api/x-official/mentions','GET']
   ].map(([url,method])=>fetch(url,{method}).catch(()=>null)));
  });
  assert.equal(blocked.length,4);
  assert.deepEqual(received,['GET /feedback.html']);
  assert.equal(allowed('https://api.x.com/2/tweets','GET',origin),false);
  const redirectResult=await page.evaluate(()=>fetch('/redirect-probe').then(()=>true).catch(()=>false));
  assert.equal(redirectResult,false,'Redirect must be aborted before browser follows it');
  assert.equal(blocked.length,5);
  assert.equal(blocked[4].status,302);
  assert.equal(blocked[4].location,'/api/x-official/feedback-sync');
  assert.deepEqual(received,['GET /feedback.html','GET /redirect-probe'],
   'Forbidden redirect destination must never reach the fake backend');
  console.log('PASS guard counterexamples: 4 direct requests and 302 redirect aborted; fake backend received zero writes or paid reads');
 }finally{await browser.close();await new Promise(resolve=>server.close(resolve));}
}
(async()=>{
 if(process.argv.includes('--self-test'))return selfTest();
 const browser=await chromium.launch();
 try {
  const context=await browser.newContext({viewport:{width:1580,height:1060},serviceWorkers:'block'});
  const forbidden=[];
  const origin='http://127.0.0.1:8766';
  await protect(context,origin,forbidden);
  const page=await context.newPage();
  // APIRequestContext bypasses routing: constrain its calls through this GET-only wrapper.
  const safeGet=url=>{assert.ok(allowed(url,'GET',origin));return context.request.get(url,{maxRedirects:0});};
  await page.goto('http://127.0.0.1:8766/feedback.html');
  await page.waitForFunction(()=>document.querySelector('#xf-account').value);
  const response=await safeGet('http://127.0.0.1:8766/api/x-official/feedback');
  assert.equal(response.status(),200);
  const data=await response.json();
  const originals=data.posts.filter(p=>p.username===data.account.username&&!p.parent_id);
  assert.ok(originals.some(p=>p.images?.length),'Need at least one cached real original post with images');
  for(const post of originals){
   if(post.images?.length){
    const image=await safeGet('http://127.0.0.1:8766/api/x-official/image?url='+encodeURIComponent(post.images[0].url));
    assert.equal(image.status(),200,'New X image route must be loaded');
    assert.match(image.headers()['content-type'],/^image\/(jpeg|png|webp|gif)/);
   }
   assert.ok(post.avatar,'Every cached original must have an avatar URL');
   const date=new Intl.DateTimeFormat('sv-SE',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date(post.created_at));
   await page.locator('[data-range=custom]').click();
   await page.locator('#xf-start').fill(date);
   await page.locator('#xf-end').fill(date);
   await page.locator('#xf-apply').click();
   await page.locator('#xf-search').fill(post.text.split(/\r?\n/)[0].slice(0,18));
   await page.locator('.xf-post-item').first().click();
   await page.locator('#xf-original details').first().evaluate(e=>e.open=true);
   await page.waitForFunction(hasImage=>{
    const avatar=document.querySelector('#xf-original .xf-author img');
    const thumb=document.querySelector('.xf-post-item.selected .xf-thumbnail img');
    const picture=document.querySelector('#xf-original .xf-post-image');
    return avatar?.naturalWidth>0&&!avatar.hidden&&(!hasImage||(thumb?.naturalWidth>0&&picture?.naturalWidth>0&&!thumb.hidden&&!picture.hidden));
   },Boolean(post.images?.length),{timeout:20000});
   const widths=await page.evaluate(()=>({
    avatar:document.querySelector('#xf-original .xf-author img')?.naturalWidth||0,
    thumbnail:document.querySelector('.xf-post-item.selected .xf-thumbnail img')?.naturalWidth||0,
    body:document.querySelector('#xf-original .xf-post-image')?.naturalWidth||0
   }));
   console.log(JSON.stringify({post_id:post.id,has_image:Boolean(post.images?.length),naturalWidth:widths}));
  }
  assert.deepEqual(forbidden,[],'Never synchronize, authorize or publish during this acceptance');
  console.log('PASS real original-post thumbnails and body images; no X API synchronization or writes');
 } finally {await browser.close();}
})().catch(e=>{console.error(e.message);process.exitCode=1;});
