const $=selector=>document.querySelector(selector);
const escapeHTML=text=>String(text??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
async function api(path,data){const r=await fetch(path,data?{method:'POST',headers:{'Content-Type':'application/json','X-Article-Studio':'1'},body:JSON.stringify(data)}:{});const value=await r.json();if(!r.ok)throw new Error(value.error||'请求失败');return value;}
function toast(message){$('#toast').textContent=message;$('#toast').classList.add('visible');clearTimeout(toast.timer);toast.timer=setTimeout(()=>$('#toast').classList.remove('visible'),3500);}
function labDate(value){return new Date(value).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false});}
// 提示词卡库的「填进生图」：只带入已确认卡片的描述与适配模型，不自动提交生成。
async function fillLabCard(cardId){
 const card=await api('/api/cards/'+encodeURIComponent(cardId)+'/prompt');
 if(typeof lab!=='undefined'&&lab.models&&lab.models.some(m=>m.id===card.model&&m.kind===lab.kind))$('#lab-model').value=card.model;
 $('#lab-prompt').value=card.prompt;
 if(typeof rememberLab==='function')rememberLab();
 $('#lab-prompt').focus();
 toast('已带入提示词卡的描述与适配模型，可继续调整；未提交生成');
 return card;
}
