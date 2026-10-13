window.mountXOfficialPanel=async function(host, options={}){
 const response=await fetch('/x-official.html');if(!response.ok)throw Error('X 发布面板加载失败');
 const doc=new DOMParser().parseFromString(await response.text(),'text/html');
 const main=doc.querySelector('main');main.querySelector('header').remove();
 host.classList.add('xo-panel');host.innerHTML=main.innerHTML+doc.querySelector("dialog").outerHTML;
 const sections=host.querySelectorAll(':scope > section');
 sections[1].hidden=options.view==='feedback';sections[3].hidden=options.view!=='feedback';
 if(options.view==='feedback')sections[2].hidden=true;
 sections[0].querySelector('h2').textContent='X 官方账号';
 if(options.view!=='feedback')sections[1].querySelector('h2').textContent='通过 X 官方接口发布';
 return window.startXOfficialPanel(host,{...options,embedded:true});
};
