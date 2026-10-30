'use strict';
// Synthetic fixture, random local port and independent headless profile only.
const assert=require('assert/strict'),fs=require('fs'),os=require('os'),path=require('path'),net=require('net');
const {spawn}=require('child_process');
const {chromium}=(()=>{try{return require('playwright')}catch(_){return require(path.join(os.homedir(),'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright'))}})();
const ROOT=path.resolve(__dirname,'../..'),tmp=fs.mkdtempSync('/tmp/wechat-links-r3-e2e-'),checks=[];
let child,context;
const check=(name,ok)=>{assert.ok(ok,name);checks.push({name,passed:true});console.log('通过 '+name)};
(async()=>{
 const port=await new Promise(resolve=>{const s=net.createServer().listen(0,'127.0.0.1',()=>{const p=s.address().port;s.close(()=>resolve(p))})}),base='http://127.0.0.1:'+port;
 const code=`import sys,tempfile,json;from pathlib import Path;sys.path[:0]=[${JSON.stringify(ROOT)},${JSON.stringify(ROOT+'/tests')}];tempfile.tempdir=${JSON.stringify(tmp)}
from test_wechat_links import WechatLinksTests
from http.server import ThreadingHTTPServer
import server
r=WechatLinksTests();r.setUp()
r.add('e2e@chatroom',30,'正文回落 https://example.test/e2e',url='https://example.test/e2e',title='wxid_synthetic')
r.add('e2e@chatroom',31,'https://wxapp.tc.qq.com/file')
with r.k.db() as c:
 c.execute('CREATE TEMP TABLE seen(id TEXT PRIMARY KEY)');r.k._write(c,'wechat-links','legacy-only','wechat_links','合成旧历史','合成旧正文','https://legacy.test/a','2026-10-01T00:00:00+00:00',[],{'legacy':True})
 c.execute("UPDATE sources SET status='partial' WHERE id='wechat-links'")
s=ThreadingHTTPServer(('127.0.0.1',${port}),server.Handler);s.store=r.store
try:s.serve_forever()
finally:r.doCleanups()`;
 child=spawn(path.resolve(ROOT,'../article-importer/.venv/bin/python'),['-u','-c',code],{cwd:ROOT,stdio:['ignore','ignore','pipe']});
 let errors='';child.stderr.on('data',d=>errors+=d);
 for(let n=0;n<100;n++){try{const r=await fetch(base+'/api/knowledge');if(r.ok)break;}catch(_){}await new Promise(r=>setTimeout(r,100));if(n===99)throw new Error('隔离服务未启动 '+errors.slice(-300));}
 context=await chromium.launchPersistentContext(path.join(tmp,'profile'),{headless:true,channel:'chrome',viewport:{width:1440,height:1000}});
 const page=await context.newPage();await page.route('**/*',r=>new URL(r.request().url()).origin===base?r.continue():r.abort());
 await page.goto(base+'/knowledge.html');await page.locator('[data-source="wechat-links"]').waitFor();
 await page.locator('#kb-add-source').click();await page.locator('[data-sync-source="wechat-links"]').click();
 let source;for(let n=0;n<100;n++){const data=await (await context.request.get(base+'/api/knowledge')).json();source=data.sources.find(x=>x.id==='wechat-links');if(!data.syncing&&source.count===4)break;await new Promise(r=>setTimeout(r,100));}
 check('点击更新来源后新增群分享并保留旧历史',source.count===4);
 const metrics=JSON.parse(source.detail.split('；').at(-1));check('下载链接被过滤并按原因计数',metrics.counts.wechat_file===1&&metrics.counts.added===3);
 check('来源说明展示群消息及历史保留范围',source.detail.includes('新增仅群消息')&&source.detail.includes('历史分享文档原样保留'));
 await page.locator('#kb-source-dialog [data-close]').click();await page.locator('[data-source="wechat-links"]').click();
 await page.locator('.kb-result').filter({hasText:'正文回落'}).locator('[data-preview]').click();await page.locator('#kb-document-dialog').waitFor({state:'visible'});
 check('wxid标题在真实页面回落正文开头',await page.locator('#kb-document-title').innerText()==='正文回落');
 const href=await page.locator('#kb-document-provenance a').getAttribute('href'),ref=new URL(href,base),doc=await (await context.request.get(base+'/api/knowledge/document/'+ref.searchParams.get('document'))).json();
 check('出处链接对应原消息版本及真实区间',doc.hash===ref.searchParams.get('hash')&&doc.body.slice(Number(ref.searchParams.get('start')),Number(ref.searchParams.get('end')))==='https://example.test/e2e');
 await page.screenshot({path:path.join(tmp,'synthetic-preview.png')});
 await page.locator('#kb-document-dialog [data-close]').click();await page.locator('#kb-add-source').click();await page.locator('[data-sync-source="wechat-links"]').click();
 for(let n=0;n<100;n++){const data=await (await context.request.get(base+'/api/knowledge')).json();source=data.sources.find(x=>x.id==='wechat-links');if(!data.syncing&&JSON.parse(source.detail.split('；').at(-1)).counts.duplicate===3)break;await new Promise(r=>setTimeout(r,100));}
 check('二次页面同步幂等且历史不删除',source.count===4&&JSON.parse(source.detail.split('；').at(-1)).counts.added===0);
 fs.writeFileSync(path.join(tmp,'results.json'),JSON.stringify({checks,tmp},null,2));console.log(JSON.stringify({passed:checks.length,evidence:tmp}));
})().catch(e=>{console.error(e.message);process.exitCode=1}).finally(async()=>{if(context)await context.close();if(child){child.kill('SIGTERM');await new Promise(r=>child.once('exit',r));}});
