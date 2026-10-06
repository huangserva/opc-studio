const {test}=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');
const path=require('node:path');
const context=vm.createContext({URL,URLSearchParams,escapeHTML:s=>String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))});
vm.runInContext(fs.readFileSync(path.join(__dirname,'../public/chat-links.js'),'utf8'),context);
test('真实来源可点击，普通文字仍保留格式并转义 HTML',()=>{
  const html=context.chatLinkedText('**结论**\n[官方文档](https://example.com/docs?a=1&b=2) <script>bad</script>');
  assert.match(html,/<strong>结论<\/strong><br>/);assert.match(html,/href="https:\/\/example.com\/docs\?a=1&amp;b=2"/);
  assert.match(html,/rel="noopener noreferrer"/);assert.ok(!html.includes('<script>'));
});
test('本机知识与事件引用可点击，拒绝跨站协议路径与其它本机接口',()=>{
  const url='/knowledge.html?document='+'a'.repeat(32)+'&start=1&end=5&hash='+'b'.repeat(64);
  assert.match(context.chatLinkedText('[原文]('+url+')'),/href="\/knowledge.html\?document=/);
  assert.match(context.chatSourceLinks([{url:'/events.html?event='+'c'.repeat(32),title:'事件'}]),/href="\/events.html/);
  for(const invalid of ['//evil.example/events.html','/api/assistant','/events.html/../api/assistant','/%2f%2fevil.example'])assert.equal(context.chatSourceURL(invalid),null);
});
test('拒绝可执行链接和带凭据的网址，来源标题不能注入 HTML',()=>{
  assert.equal(context.chatSourceURL('javascript:alert(1)'),null);
  assert.equal(context.chatSourceURL('https://name:secret@example.com'),null);
  const html=context.chatSourceLinks([{url:'javascript:alert(1)',title:'bad'},
    {url:'https://example.com',title:'<img onerror="bad">'}, {url:'https://example.com',title:'duplicate'}]);
  assert.equal((html.match(/<a /g)||[]).length,1);assert.ok(!html.includes('<img'));assert.ok(!html.includes('javascript:'));
});

test('实际读取卡片保留 Unicode 片段和固定版本链接，不依赖模型自行写引用',()=>{
  const c={id:'a'.repeat(32),hash:'b'.repeat(64),start:1,end:5,excerpt:'😀原文呀',title:'合成来源 <img>',kind:'mail',state:'stale',read_at:'2026-10-05T00:00:00Z'};
  c.url='/knowledge.html?'+new URLSearchParams({document:c.id,start:c.start,end:c.end,hash:c.hash});
  const html=context.chatReadSources([c,c]);assert.match(html,/本轮读取的资料 · 1 个片段/);assert.match(html,/读取的是已选旧快照/);assert.match(html,/原文第 2–5 字/);assert.match(html,/😀原文呀/);assert.match(html,/hash=bbbb/);assert(!html.includes('<img>'));assert.match(html,/不代表回答中的每项判断已验证/);
});
test('搜索摘要、伪造范围、缺 hash 或替换引用地址不显示为读取原文',()=>{
  const c={id:'a'.repeat(32),hash:'b'.repeat(64),start:0,end:4,excerpt:'合成原文',title:'合成来源'};
  c.url='/knowledge.html?'+new URLSearchParams({document:c.id,start:c.start,end:c.end,hash:c.hash});
  for(const bad of [{...c,url:'https://evil.test'}, {...c,url:'/api/assistant'}, {...c,end:5}, {...c,start:true}, {...c,hash:undefined},{title:'搜索摘要',excerpt:'相似片段'}])assert.equal(context.chatReadSources([bad]),'');
  const html=context.chatReadSources([{...c,excerpt:'<img',source_url:'javascript:bad()'}]);assert(!html.includes('javascript:'));assert(!html.includes('<img'));
});
