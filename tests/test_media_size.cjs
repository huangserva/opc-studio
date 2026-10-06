const {test}=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs');
function setup(){
 const nodes={};for(const id of ['chat-media-size','chat-media-custom-size','chat-media-width','chat-media-height'])nodes['#'+id]={value:'1024',disabled:true,hidden:true};
 const c=vm.createContext({$:s=>nodes[s],escapeHTML:s=>String(s)});
 vm.runInContext(fs.readFileSync(require('node:path').join(__dirname,'../public/media-size.js'),'utf8'),c);
 return {c,nodes};
}
test('自定义任务回填真实宽高，发送实际尺寸；切换普通模型关闭输入',()=>{
 const {c,nodes}=setup();c.model={sizes:['1024x1024'],custom_size:{min:512,max:2048,step:16},size_groups:[{name:'1K 档',sizes:['1024x1024']}]};
 vm.runInContext("renderMediaSize('chat-media',model,'1600x1200')",c);
 assert.equal(nodes['#chat-media-size'].value,'custom');assert.equal(nodes['#chat-media-custom-size'].hidden,false);
 assert.equal(vm.runInContext("selectedMediaSize('chat-media')",c),'1600x1200');
 c.model={sizes:['1024x1024']};vm.runInContext("renderMediaSize('chat-media',model,'1600x1200')",c);
 assert.equal(nodes['#chat-media-size'].value,'1024x1024');assert.equal(nodes['#chat-media-width'].disabled,true);
 assert.equal(nodes['#chat-media-custom-size'].hidden,true);
});
test('自定义尺寸不静默舍入或越界',()=>{
 const {c,nodes}=setup();nodes['#chat-media-size'].value='custom';nodes['#chat-media-height'].value='1024';
 for(const width of ['', '513','2064','1.5']){
  nodes['#chat-media-width'].value=width;assert.throws(()=>vm.runInContext("selectedMediaSize('chat-media')",c),/16 的倍数/);
 }
});
