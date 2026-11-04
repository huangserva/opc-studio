const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {JSDOM}=require(process.env.JSDOM_MODULE||'/Users/huangzongning/development/article-studio/node_modules/jsdom');
const source=fs.readFileSync(process.env.PUBLISH_JS||path.join(__dirname,'../public/publish.js'),'utf8');
const dom=new JSDOM('<body></body>',{runScripts:'outside-only'}),w=dom.window;
w.safeHttpUrl=value=>typeof value==='string'&&/^https?:\/\//.test(value)&&!/[\s\x00-\x1f]/.test(value);
// 执行候选原函数，不复制渲染实现；DOM 的结果是安全断言对象。
w.eval(source.slice(source.indexOf('function textNode('),source.indexOf('const xLabels='))+source.slice(source.indexOf('function markdownInline('),source.indexOf('function renderReviews('))+'\nwindow.renderMarkdown=markdownReview;');
let checks=0;const check=(v,m)=>{assert(v,m);checks++;};
const render=s=>w.renderMarkdown(s);
let box=render('## 本周产出复盘\n\n原句 **重点**。\n\n- 一\n- 二\n\n| 项 | 值 |\n| --- | ---: |\n| 浏览 | 27 |\n');
check(box.querySelector('h3').textContent==='本周产出复盘','heading');check(box.querySelector('strong').textContent==='重点','emphasis');check(box.querySelectorAll('li').length===2,'list');check(box.querySelectorAll('tbody tr').length===1,'table');
box=render('## 本周产出复盘\n\n<script>window.__injected=1</script>\n\n<img src=x onerror=alert(1)>\n\n[坏](javascript:alert) [数据](data:text/html,evil) [相对](/secret) [好](https://example.com/a) [明文](http://example.com/)');
check(box.querySelectorAll('script,img').length===0,'HTML escaped, no script/img DOM');check(box.textContent.includes('<script>window.__injected=1</script>'),'HTML literal preserved');check(box.querySelectorAll('a').length===2,'only http/https links');check([...box.querySelectorAll('a')].every(a=>/^https?:/.test(a.href)),'URL protocol');check(box.textContent.includes('[坏](javascript:alert)'),'unsafe link remains literal');
box=render('## 本周产出复盘\n| 一 | 二 |\n| broken | -- |\n| 三 |\n');check(box.textContent.includes('| broken | -- |'),'bad table literal preserved');
box=render('```html\n<script>window.bad=1</script>\n```');check(box.querySelector('code').textContent.includes('<script>'),'fenced code escaped');check(box.querySelectorAll('script').length===0,'code not HTML');
console.log(JSON.stringify({checks,passed:true}));
