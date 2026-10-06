// Only this local workbench origin can request a browser delivery.
window.addEventListener('message', async event => {
  if(event.source!==window || event.origin!==location.origin || event.data?.channel!=='studio-x-request')return;
  const {id,command,payload}=event.data;
  if(!['ping','run','publish'].includes(command))return;
  try {
    const result=await chrome.runtime.sendMessage({command,payload});
    window.postMessage({channel:'studio-x-response',id,result},location.origin);
  } catch(e){window.postMessage({channel:'studio-x-response',id,result:{error:e.message}},location.origin);}
});
chrome.runtime.onMessage.addListener((msg,sender,reply)=>{
  if(msg.command!=='checkpoint')return;
  fetch('/api/local-publishing/job/'+msg.id,{method:'POST',headers:{'Content-Type':'application/json','X-Article-Studio':'1'},body:JSON.stringify(msg.payload)})
    .then(async r=>{const x=await r.json();if(!r.ok)throw Error(x.error);reply(x);})
    .catch(e=>reply({error:e.message}));return true;
});
