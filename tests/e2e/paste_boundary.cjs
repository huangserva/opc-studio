'use strict';
const assert=require('node:assert/strict');
module.exports=async function({test,api,until,base,fs,path,evidence}){
 const article=slug=>api('/api/article/'+encodeURIComponent(slug));
 const receipt=turn=>[turn.answer||'',...(turn.actions||[]).map(action=>action.result||'')].join('\n');
 async function create(p,title){
  await p.goto(base+'/');
  await p.locator('#new-article-top').click();
  await p.locator('#new-dialog').waitFor({state:'visible'});
  await p.locator('#new-title').fill(title);
  const created=p.waitForResponse(r=>new URL(r.url()).pathname==='/api/create'&&r.request().method()==='POST');
  await p.locator('#create-article').click();
  const response=await created;assert.equal(response.ok(),true);
  const data=await response.json();
  await p.locator('#article-title',{hasText:title}).waitFor();
  await p.locator('#cancel-edit').click();
  await until(async()=>(await p.locator('#save-state').innerText()).includes('已保存'));
  return data.slug;
 }
 async function freshChat(p){
  if(await p.locator('#assistant-panel').isHidden())await p.locator('#assistant-toggle').click();
  await p.locator('#assistant-input').waitFor();
  await p.locator('#assistant-new-chat').click({timeout:20000});
  await until(async()=>{const status=await api('/api/assistant');return !status.active_job&&status.messages.length===0});
 }
 async function send(p,text,visible){
  const before=(await api('/api/assistant')).messages.length;
  await p.locator('#assistant-input').fill(text);
  await p.locator('#assistant-send').click();
  const turn=await until(async()=>{
   const status=await api('/api/assistant');
   if(status.active_job)return false;
   const last=status.messages.at(-1);
   if(!last||status.messages.length<=before)return false;
   if(last.status==='running'||last.status==='stopping')return false;
   return last;
  },90000);
  await until(async()=>(await p.locator('#assistant-message-list').innerText()).includes(visible),20000);
  return turn;
 }
 await test('20-长引号补一句被拒并提示换行',async p=>{
  const slug=await create(p,'E2E贴文20');
  await freshChat(p);
  const head=(await article(slug)).head;
  const rejected=await send(p,'把下面贴文贴上，再补一句「今天发布甲方案的完整长文。」\n短','请换行后再贴原文');
  const stayed=await article(slug);
  assert.equal(stayed.head,head);
  assert.equal(stayed.blocks.length,0);
  assert.match(receipt(rejected),/请换行后再贴原文/);
  assert.equal(await p.locator('.article-body').innerText(),'');
  await freshChat(p);
  await send(p,'把下面贴文贴上，再补一句过渡「接下来」。\n今天发布甲方案。','今天发布甲方案。');
  const saved=await article(slug);
  assert.notEqual(saved.head,head);
  assert.equal(saved.versions.length,stayed.versions.length+1);
  assert.equal(saved.blocks.at(-1).text.trim(),'今天发布甲方案。');
  await until(async()=>(await p.locator('.article-body').innerText()).includes('今天发布甲方案。'));
 });
 await test('21-中文句号紧跟网址',async p=>{
  const slug=await create(p,'E2E贴文21'),text='把下面贴文贴上\n见 https://example.com/a。';
  await freshChat(p);
  const head=(await article(slug)).head;
  await send(p,text,'见 https://example.com/a。');
  const saved=await article(slug);
  assert.notEqual(saved.head,head);
  const html=saved.blocks.at(-1).html;
  assert.match(html,/href="https:\/\/example\.com\/a"/);
  assert.match(html,/<\/a>。/);
  assert.doesNotMatch(html,/href="[^"]*。/);
  assert.equal(saved.blocks.at(-1).text.includes('见 https://example.com/a。'),true);
  await until(async()=>(await p.locator('.article-body').innerText()).includes('见 https://example.com/a。'));
  await freshChat(p);
  const refused=await send(p,text,'原文里没有的链接');
  const stayed=await article(slug);
  assert.equal(stayed.head,saved.head);
  assert.equal(stayed.blocks.length,saved.blocks.length);
  assert.match(receipt(refused),/原文里没有的链接/);
  assert.doesNotMatch(stayed.blocks.map(block=>block.html).join('\n'),/。以及/);
 });
 await test('22-大写HTTPS',async p=>{
  const slug=await create(p,'E2E贴文22'),text='把下面贴文贴上\n见 HTTPS://Example.COM/Path';
  await freshChat(p);
  const head=(await article(slug)).head;
  await send(p,text,'HTTPS://Example.COM/Path');
  const saved=await article(slug);
  assert.notEqual(saved.head,head);
  const href=saved.blocks.at(-1).html.match(/href="([^"]+)"/)[1];
  const url=new URL(href);
  assert.equal(url.protocol,'https:');
  assert.equal(url.hostname,'example.com');
  assert.equal(url.pathname,'/Path');
  await freshChat(p);
  const refused=await send(p,text,'原文里没有的链接');
  const stayed=await article(slug);
  assert.equal(stayed.head,saved.head);
  assert.match(receipt(refused),/原文里没有的链接/);
  assert.equal(stayed.blocks.at(-1).html.match(/href="([^"]+)"/)[1],'https://example.com/Path');
 });
 await test('23-非法端口',async p=>{
  const slug=await create(p,'E2E贴文23');
  const head=(await article(slug)).head;
  const log=path.join(evidence,'server.log'),start=fs.statSync(log).size;
  const rows=[['见 https://a.com:xx/说明','https://a.com:xx/'],['见 https://a.com:99999/说明','https://a.com:99999/'],['见 https://[::1/说明','https://[::1/']];
  for(const [line,href] of rows){
   await freshChat(p);
   const turn=await send(p,'把下面贴文贴上\n'+line,'原文里没有的链接');
   assert.match(receipt(turn),/原文里没有的链接/);
   assert.doesNotMatch(receipt(turn),/Traceback|Invalid IPv6|Port out of range|could not be cast/);
   assert.equal((await article(slug)).head,head);
   assert.equal((await p.locator('.article-body').innerText()).includes(href),false);
  }
  const body=(await article(slug)).blocks.map(block=>block.html||'').join('\n');
  for(const [,href] of rows)assert.equal(body.includes(href),false);
  assert.doesNotMatch(fs.readFileSync(log,'utf8').slice(start),/Traceback/);
 });
 await test('24-服务端照原文插入并在连续拒绝后停止',async p=>{
  const source=fs.readFileSync(path.resolve(__dirname,'../../coordination/round6-assets/round6-assistant-text.txt'),'utf8').replace(/\r\n/g,'\n').trim();
  const lines=source.split('\n').map(line=>line.trim()).filter(Boolean);
  assert.equal(lines.length,4);
  const slug=await create(p,'E2E贴文24');
  await freshChat(p);
  const before=await article(slug);
  await send(p,'把下面贴文贴上，再补一句过渡。原文：'+source,'已按规则处理这一轮。');
  await until(async()=>(await p.locator('.article-body').innerText()).includes('接下来继续观察。'));
  const saved=await article(slug);
  assert.notEqual(saved.head,before.head);
  assert.equal(saved.versions.length,before.versions.length+1);
  const texts=saved.blocks.map(block=>block.text.trim());
  assert.deepEqual(texts,lines.concat(['接下来继续观察。']));
  for(const line of lines)assert.equal(texts.includes(line),true);
  assert.equal(texts.at(-1),'接下来继续观察。');
  await freshChat(p);
  const stayedHead=saved.head;
  const refused=await send(p,'把下面贴文贴上，再补一句「演示」。\n今天发布甲方案。','连续被拒');
  const stayed=await article(slug);
  assert.equal(stayed.head,stayedHead);
  assert.deepEqual(stayed.blocks.map(block=>block.text.trim()),texts);
  assert.match(receipt(refused),/连续被拒/);
  assert.match(receipt(refused),/paste_original/);
  assert.doesNotMatch(receipt(refused),/模型没有返回回复正文/);
  const edits=(refused.actions||[]).filter(action=>action.name==='edit_work');
  assert.equal(edits.length,2);
  assert.equal(edits.every(action=>action.status==='error'),true);
  await freshChat(p);
  const tripleBefore=await article(slug);
  const triple=await send(p,'把下面贴文贴上\n今天发布甲方案。同一响应三连','连续被拒');
  const tripleSaved=await article(slug);
  assert.equal(tripleSaved.head,tripleBefore.head);
  assert.deepEqual(tripleSaved.blocks.map(block=>block.text.trim()),texts);
  assert.match(receipt(triple),/连续被拒/);
  assert.match(receipt(triple),/没有保存新版本/);
  assert.doesNotMatch(receipt(triple),/模型没有返回回复正文/);
  const tripleEdits=(triple.actions||[]).filter(action=>action.name==='edit_work');
  assert.equal(tripleEdits.length,2);
  assert.equal(tripleEdits.every(action=>action.status==='error'),true);
 });
};
