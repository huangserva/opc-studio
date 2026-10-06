/* OpenPI's actual sandbox/controller; Studio owns stage tools and persistence. */
import { RunController } from './vendor/openpi/controller.ts';
import { runWorkflowSandbox } from './vendor/openpi/sandbox.ts';

const emit = value => process.stdout.write(JSON.stringify(value) + '\n');
let buffer = '', started = false;
const abort = new AbortController(), pending = new Map();
const SOURCE = `
const research = await agent(args.topic, {label:'research',inputs:args.knowledge?{knowledge:args.knowledge}:undefined});
if (!research.ok) return {ok:false,stage:'research',error:research.error};
const draft = await agent(args.topic, {label:'draft',inputs:{...research.structured,...(args.knowledge?{knowledge:args.knowledge}:{})}});
if (!draft.ok) return {ok:false,stage:'draft',error:draft.error};
const review = await agent(args.topic, {label:'review',inputs:{research:research.structured,draft:draft.structured,...(args.knowledge?{knowledge:args.knowledge}:{})}});
if (!review.ok) return {ok:false,stage:'review',error:review.error};
return {ok:true,structured:review.structured};
`;
async function run(input) {
  const controller = new RunController(abort.signal, 1, 3);
  try {
    const result = await runWorkflowSandbox({ source:SOURCE,args:{topic:input.topic,knowledge:input.knowledge},cwd:input.cwd,
      signal:controller.signal,maxConcurrency:1,maxAgentCalls:3,usageSnapshot:()=>controller.capacity(),
      onLog:()=>{},onPhase:()=>{},
      onAgent:(prompt,options,signal)=>controller.schedule(async()=>{
        if (!['research','draft','review'].includes(options.label)) throw new Error('Invalid stage');
        const id=crypto.randomUUID();
        return await new Promise((resolve,reject)=>{
          const stop=()=>{pending.delete(id);reject(new Error('cancelled'));};
          signal.addEventListener('abort',stop,{once:true});
          pending.set(id,{resolve,reject,cleanup:()=>signal.removeEventListener('abort',stop)});
          emit({type:'stage',id,stage:options.label,prompt,inputs:options.inputs});
        });
      },signal),
    });
    emit({type:'done',result});
  } catch {
    emit({type:'done',result:{ok:false,error:abort.signal.aborted?'已停止工作流':'工作流执行未完成'}});
  } finally {
    await controller.settle({abort:true,timeoutMs:2000});
    for(const p of pending.values()){p.cleanup();p.reject(new Error('closed'));}
    pending.clear();process.stdin.destroy();
  }
}
process.stdin.setEncoding('utf8');
process.stdin.on('data',chunk=>{
  buffer+=chunk;let end;
  while((end=buffer.indexOf('\n'))>=0){
    const raw=buffer.slice(0,end);buffer=buffer.slice(end+1);
    let msg;try{msg=JSON.parse(raw);}catch{continue;}
    if(msg.type==='run'&&!started){started=true;void run(msg);}
    else if(msg.type==='cancel')abort.abort();
    else if(msg.type==='result'){
      const p=pending.get(msg.id);if(p){pending.delete(msg.id);p.cleanup();p.resolve(msg.result);}
    }
  }
});
process.stdin.on('end',()=>abort.abort());
