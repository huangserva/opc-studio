const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
const {execFileSync}=require('node:child_process');
const root=path.resolve(__dirname,'..');
function render(workflow){
  const context=vm.createContext({$(){return {};},escapeHTML:s=>String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])),
    URL,mediaSendReady(){},clearMediaTool(){},workflow});
  for(const file of ['chat-links.js','assistant-workflow.js'])vm.runInContext(fs.readFileSync(path.join(root,'public',file),'utf8'),context);
  return vm.runInContext('workflowCard(workflow)',context);
}
test('未完成的工作流不能保存，审稿结果与来源展示且转义模型文本',()=>{
  const flow={id:'a'.repeat(32),status:'running',stages:[{id:'research',label:'联网调研',status:'running'}],sources:[]};
  assert.doesNotMatch(render(flow),/data-save-workflow/);
  flow.result={title:'<script>bad</script>',markdown:'内容 [官网](https://pi.dev/docs/latest)',verdict:'needs_review',notes:['修订说明'],unresolved:['<img src=x onerror=1>']};
  flow.status='done';const html=render(flow);
  assert.match(html,/data-save-workflow/);assert.match(html,/待核实/);assert.match(html,/href="https:\/\/pi.dev\/docs\/latest"/);
  assert.doesNotMatch(html,/<script>|<img/);
  flow.saved={slug:'saved-work',version:'v0001'};assert.doesNotMatch(render(flow),/data-save-workflow/);assert.match(render(flow),/查看已保存作品/);
});
test('真实 OpenPI 沙箱执行依赖顺序，拒绝文件访问和不让出控制的脚本',()=>{
  const code=`
    import assert from 'node:assert/strict';
    import {runWorkflowSandbox} from './pi-runtime/vendor/openpi/sandbox.ts';
    import {RunController} from './pi-runtime/vendor/openpi/controller.ts';
    const c=new RunController(undefined,1,3),seen=[];
    const base={args:{},cwd:process.cwd(),signal:c.signal,maxConcurrency:1,maxAgentCalls:3,onPhase:()=>{},onLog:()=>{},usageSnapshot:()=>c.capacity(),onAgent:p=>c.schedule(async()=>{seen.push(p);return {ok:true,output:p};})};
    const result=await runWorkflowSandbox({...base,source:'const a=await agent("research");const b=await agent(a.output+"-draft");return await agent(b.output+"-review");'});
    assert.equal(result.output,'research-draft-review');assert.deepEqual(seen,['research','research-draft','research-draft-review']);await c.settle();
    await assert.rejects(runWorkflowSandbox({...base,source:'return require("node:fs").readFileSync("/etc/passwd","utf8");'}));
    await assert.rejects(runWorkflowSandbox({...base,source:'while(true){}'}));
  `;
  execFileSync(process.execPath,['--experimental-strip-types','--input-type=module','-e',code],{cwd:root,timeout:10000,stdio:'pipe'});
});
