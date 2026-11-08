const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync('public/assistant.js','utf8');
const polling=source.slice(source.indexOf('async function pollAssistant('),source.indexOf('async function sendAssistant('));
test('迟到的旧轮询回执不覆盖新轮次及其素材',async()=>{
 let resolve;const pending=new Promise(r=>resolve=r),renders=[];
 const context=vm.createContext({assistantState:{active:'old',pollEpoch:0,messages:[]},clearTimeout(){},setTimeout(){},assistantBusy(){},
   api:()=>pending,renderAssistant:job=>renders.push(job),showCurrentModel(){},$:()=>({}),state:{}});
 vm.runInContext(polling,context);
 const task=vm.runInContext("pollAssistant('old')",context);
 vm.runInContext("assistantState.pollEpoch++;assistantState.active='new';assistantState.messages=[{id:'new',materials:[{id:'new-card',text:'新素材'}]}]",context);
 resolve({id:'old',status:'done',materials:[{id:'old-card',text:'旧素材'}]});await task;
 assert.deepEqual(renders,[]);assert.equal(vm.runInContext('assistantState.messages[0].materials[0].text',context),'新素材');
});
