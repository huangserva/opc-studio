const busy=new Set();
const sessions=new Map();
const extensionVersion='0.1.33';
const sleep=ms=>new Promise(resolve=>setTimeout(resolve,ms));
function failure(code,message){const e=Error(message);e.code=code;return e;}
function canonicalDraft(url){
  const match=/^https:\/\/x\.com\/compose\/articles\/edit\/(\d+)(?:\/(?:preview\/)?focus|\/preview)?\/?$/.exec(url);
  if(!match)throw failure('DRAFT_UNAVAILABLE','草稿链接格式不正确');
  return 'https://x.com/compose/articles/edit/'+match[1];
}
async function checkpoint(tab,id,payload){
  const r=await chrome.tabs.sendMessage(tab,{command:'checkpoint',id,payload});
  if(!r||r.error)throw failure('WORKBENCH_DISCONNECTED',r?.error||'工作台连接中断，已停止操作');
  return r;
}
async function sendX(tab,msg){
  const r=await chrome.tabs.sendMessage(tab,msg);
  if(!r||r.error){const e=failure(r?.code||'X_PAGE_ERROR',r?.error||'X 页面没有返回有效结果');if(r?.diagnostics)e.diagnostics=r.diagnostics;throw e;}
  return r;
}
async function waitTab(tab,url,previousInstance){
  const until=Date.now()+20000;
  while(Date.now()<until){
    let r;try{r=await chrome.tabs.sendMessage(tab,{command:'ping'});}catch{}
    if(r?.ready){
      if(r.version!==extensionVersion)throw failure('X_EXECUTOR_OUTDATED','X 页面仍在运行旧执行器，请刷新此 X 标签页后继续');
      if(previousInstance&&r.instance===previousInstance){await sleep(300);continue;}
      if(r.url&&new URL(r.url).pathname===new URL(url).pathname)return;
    }
    await sleep(300);
  }
  throw failure(url.includes('/edit/')?'DRAFT_UNAVAILABLE':'X_PAGE_NOT_READY',url.includes('/edit/')?
    '原草稿页面未能加载或发生跳转，已停止上传并保留原记录；可重试或选择另建草稿':
    'X 文章列表尚未就绪，请检查本机 Chrome 页面加载情况');
}
async function reloadTab(tab,url){
  let previous;try{previous=await chrome.tabs.sendMessage(tab,{command:'ping'});}catch{}
  await chrome.tabs.reload(tab);
  // reload() acknowledges scheduling, not replacement of the old document.
  // Never start a task on a ready response from the page being torn down.
  await waitTab(tab,url,previous?.instance);
}
async function xTab(url,{refresh=true,active=true}={}){
  const all=await chrome.tabs.query({url:'https://x.com/*'});
  let tab=all.find(t=>t.url===url);
  if(!tab)tab=await chrome.tabs.create({url,active});
  else{if(active)await chrome.tabs.update(tab.id,{active:true});if(refresh)await reloadTab(tab.id,url);}
  if(active&&tab.windowId!==undefined)await chrome.windows.update(tab.windowId,{focused:true});
  await waitTab(tab.id,url);
  return tab;
}
async function verifyLogin(session,{refresh=true}={}){
  if(!session.loginCheck)session.loginCheck=(async()=>{
    if(refresh)await reloadTab(session.probe,'https://x.com/compose/articles');
    const observed=await sendX(session.probe,{command:'account'});
    if(observed.account!==session.account)throw failure('ACCOUNT_MISMATCH','实际登录 @'+observed.account+'，目标为 @'+session.account+'；请在 X 切换账号');
    return observed;
  })().finally(()=>{session.loginCheck=null;});
  return session.loginCheck;
}
chrome.runtime.onMessage.addListener((msg,sender,reply)=>{
  if(['upload-progress','verify-account'].includes(msg.command)){
    const session=sessions.get(msg.id);
    if(!session||session.x!==sender.tab?.id||!/^https:\/\/x\.com\//.test(sender.url||'')){reply({error:'此页面没有有效的工作台任务',code:'SESSION_MISSING'});return;}
    (msg.command==='verify-account'?verifyLogin(session):checkpoint(session.local,msg.id,msg.payload))
      .then(result=>{if(msg.command==='upload-progress'&&msg.payload?.draft_url)session.draft_url=msg.payload.draft_url;reply(result);}).catch(e=>reply({error:e.message,code:e.code}));return true;
  }
  if(!sender.tab||!/^http:\/\/(127\.0\.0\.1|localhost):8766\//.test(sender.url||''))return;
  if(msg.command==='ping'){reply({connected:true,version:extensionVersion});return;}
  if(!['run','publish'].includes(msg.command))return;
  const bundle=msg.payload,id=bundle?.job?.id;
  if(!id||busy.has(id)){reply({error:'任务正在执行，请查看进度'});return;}
  busy.add(id);
  (async()=>{
    let attempted=false;
    try{
      if(bundle.job.publish_attempted)throw Error('已有发布尝试，请先在 X 核对结果');
      if(msg.command==='publish'&&bundle.job.status!=='publish_requested')throw Error('请先在工作台确认发布');
      if(msg.command==='run'){bundle.job.checkpoint={...bundle.job.checkpoint,executor:{name:'local-chrome-extension',version:extensionVersion},phase:'account_check'};await checkpoint(sender.tab.id,id,{status:'running',checkpoint:bundle.job.checkpoint,diagnostics:null,message:'正在打开 X 文章列表核验登录账号'});}
      const probe=await xTab('https://x.com/compose/articles',{refresh:true,active:false});
      const session={local:sender.tab.id,probe:probe.id,account:bundle.job.account};
      const checked=await verifyLogin(session,{refresh:false});
      if(msg.command==='run')await checkpoint(sender.tab.id,id,{status:'running',observed_account:checked.account,message:'已核验 @'+checked.account+'，正在打开文章编辑器'});
      const tab=bundle.job.draft_url?await xTab(canonicalDraft(bundle.job.draft_url)):await chrome.tabs.create({url:'https://x.com/compose/articles',active:true});
      if(!bundle.job.draft_url)await waitTab(tab.id,'https://x.com/compose/articles');
      await chrome.tabs.update(tab.id,{active:true});
      session.x=tab.id;sessions.set(id,session);
      if(msg.command==='publish'){
        // Do not change the confirmed state before the server records this attempt.
        await checkpoint(sender.tab.id,id,{action:'attempt'});attempted=true;
        const r=await sendX(tab.id,{command:'publish',bundle});
        await checkpoint(sender.tab.id,id,{status:'published',observed_account:checked.account,published_url:r.url,message:'已发布'});
      }else{
        let r=await sendX(tab.id,{command:'prepare',bundle});
        await checkpoint(sender.tab.id,id,{status:'running',observed_account:checked.account,draft_url:r.url,message:'草稿已绑定，正在检查正文与配图'});
        bundle.job.draft_url=r.url;
        bundle.job.checkpoint=r.checkpoint||bundle.job.checkpoint;
        r=await sendX(tab.id,{command:'upload',bundle});
        bundle.job.checkpoint=r.checkpoint;
        await reloadTab(tab.id,bundle.job.draft_url);
        const saved=await sendX(tab.id,{command:'check',bundle});
        await checkpoint(sender.tab.id,id,{status:'review_ready',observed_account:checked.account,draft_url:bundle.job.draft_url,evidence:saved.evidence,checkpoint:r.checkpoint,message:'上传检查完成，请预览正文、封面与图片顺序，再确认发布'});
      }
      reply({ok:true});
    }catch(e){
      try{await checkpoint(sender.tab.id,id,{status:attempted?'unknown':'blocked',message:e.message,error_code:e.code||null,diagnostics:e.diagnostics||null});}catch{}
      reply({error:e.message,code:e.code});
    }finally{busy.delete(id);sessions.delete(id);}
  })();return true;
});
