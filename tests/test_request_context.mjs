import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {manageImageContext, monitoredStream} from '../pi-runtime/request-context.mjs';
import {SessionManager} from '../pi-runtime/node_modules/@earendil-works/pi-coding-agent/dist/index.js';
import {estimateProjectedContextTokens, shouldCompact} from '../pi-runtime/node_modules/@earendil-works/pi-coding-agent/dist/core/compaction/compaction.js';
import {createAssistantMessageEventStream} from '../pi-runtime/node_modules/@earendil-works/pi-ai/dist/index.js';
const manager=SessionManager.inMemory('/tmp');
const image={type:'image',mimeType:'image/png',data:Buffer.from('fixture').toString('base64')};
const ref={id:'a'.repeat(32),name:'original.png',sha256:createHash('sha256').update('fixture').digest('hex')};
const answered=manager.appendMessage({role:'user',content:[{type:'text',text:'x'.repeat(100000)},image],timestamp:1});
const assistant=(content,stopReason='stop')=>({role:'assistant',content:[{type:'text',text:content}],stopReason,usage:{input:100,output:10,cacheRead:0,cacheWrite:0,totalTokens:110},timestamp:2});
manager.appendMessage(assistant('已分析：左红右蓝'));
for(let i=0;i<30;i++){manager.appendMessage({role:'user',content:'turn '+i,timestamp:3});manager.appendMessage(assistant('answer'));}
manager.appendMessage({role:'assistant',content:[{type:'toolCall',id:'call',name:'read_work',arguments:{}}],stopReason:'toolUse',timestamp:4});
manager.appendMessage({role:'toolResult',toolCallId:'call',toolName:'read_work',content:[{type:'text',text:'fact'}],isError:false,timestamp:5});
const raw=JSON.stringify(manager.getEntry(answered));
assert.equal(manageImageContext(manager,[ref]),1);
let projection=manager.buildSessionProjection();
assert.equal(projection.messages.filter(m=>m.role==='user').length,31);
assert.equal(projection.messages[0].content[0].text.length,100000);
assert.equal(JSON.stringify(manager.getEntry(answered)),raw);
assert.equal(projection.messages[0].content[1].type,'text');
assert.match(projection.messages[0].content[1].text,/attachment_id=/);
assert.equal(projection.messages.at(-1).toolCallId,projection.messages.at(-2).content[0].id);
const estimate=estimateProjectedContextTokens(projection,manager.getBranch());
assert.equal(estimate.lastUsageIndex,null);assert.ok(estimate.tokens>25000);
assert.equal(shouldCompact(estimate.tokens,4096,{enabled:true,reserveTokens:512}),true);
assert.equal(shouldCompact(estimate.tokens,262144,{enabled:true,reserveTokens:34816}),false);
assert.equal(manageImageContext(manager,[ref]),0); // Append-only edits are idempotent.
const pending=manager.appendMessage({role:'user',content:[image],timestamp:6});
manager.appendMessage(assistant('失败','error'));
manageImageContext(manager,[ref]);
assert.equal(manager.buildSessionProjection().entries.find(e=>e.sourceEntry.id===pending).messages[0].content[0].type,'image');
const unknown=SessionManager.inMemory('/tmp');unknown.appendMessage({role:'user',content:[image],timestamp:1});unknown.appendMessage(assistant('分析'));
manageImageContext(unknown,[]);assert.equal(unknown.buildSessionContext().messages[0].content[0].type,'image');
// A healthy stream can exceed its idle timeout in total duration.
const result=assistant('ok');
async function* chunks(options){for(let i=0;i<5;i++){await new Promise(r=>setTimeout(r,40));assert.equal(options.signal.aborted,false);yield {type:'text_delta',delta:'a',partial:result};}yield {type:'done',reason:'stop',message:result};}
const s=monitoredStream(chunks,{},.1,createAssistantMessageEventStream);
let n=0;for await(const e of s)if(e.type==='text_delta')n++;
assert.equal(n,5);assert.equal((await s.result()).stopReason,'stop');
console.log('Pi native context, usage invalidation, image lifecycle and idle timeout: passed');
