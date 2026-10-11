const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs');
const {JSDOM}=require('jsdom');
function setup(){
 const dom=new JSDOM(fs.readFileSync('public/lab.html','utf8'),{runScripts:'outside-only',url:'http://localhost/lab.html'}),w=dom.window;
 w.eval(`var lab={kind:'image',uploading:0};var $=s=>document.querySelector(s);function updateGenerateState(){};function rememberLab(){};function toast(x){window.lastToast=x;}`);
 w.document.querySelector('#lab-model').innerHTML='<option value="qi21">Qwen</option><option value="krea2">Krea</option>';
 w.eval(fs.readFileSync('public/lab-image.js','utf8').replace('let imageReferences=','var imageReferences='));return w;
}
test('refill previews, preserve model switches, remove refs, use img2img strength',()=>{
 const w=setup(),id='a'.repeat(32);
 w.eval(`configureImageReferences({references:{images:['${id}']},reference_media:{'${id}':{name:'test.png'}},denoise:0.6})`);
 assert.equal(w.document.querySelectorAll('#lab-image-previews img').length,1);
 assert.deepEqual([...w.eval('imageParameters().references.images')],[id]);
 w.document.querySelector('#lab-model').value='krea2';w.eval('configureImageReferences({})');
 assert.equal(w.document.querySelector('#lab-image-denoise-label').hidden,false);
 assert.equal(w.eval('imageParameters().denoise'),0.6);
 w.document.querySelector('#lab-image-previews button').click();assert.equal(w.eval('imageParameters().references.images.length'),0);
});
test('model limit and incomplete uploads block generation',()=>{
 const w=setup();w.eval(`imageReferences=[{id:'${'a'.repeat(32)}'},{id:'${'b'.repeat(32)}'}]`);
 w.document.querySelector('#lab-model').value='krea2';assert.throws(()=>w.eval('imageParameters()'),/最多使用 1/);
 w.eval('imageReferences=[{pending:true}];renderImageReferences()');assert.equal(w.eval('lab.uploading'),1);assert.throws(()=>w.eval('imageParameters()'),/仍在上传/);
});
test('paste and file chooser both upload images; ordinary text paste stays intact',async()=>{
 const w=setup(),calls=[];
 w.FileReader=class{readAsDataURL(){this.result='data:image/png;base64,fixture';queueMicrotask(()=>this.onload());}};
 w.api=async(path,payload)=>{calls.push({path,payload});return {id:String(calls.length).padStart(32,'a'),url:'/fixture.png',name:payload.name};};
 const file={type:'image/png',size:100,name:'clipboard.png'};
 const paste=new w.Event('paste',{bubbles:true,cancelable:true});Object.defineProperty(paste,'clipboardData',{value:{items:[{kind:'file',type:'image/png',getAsFile:()=>file}]}});
 w.document.querySelector('#lab-prompt').dispatchEvent(paste);assert.equal(paste.defaultPrevented,true);
 await new Promise(resolve=>setImmediate(resolve));assert.equal(calls.length,1);assert.equal(calls[0].path,'/api/generation/references');assert.equal(w.eval('imageParameters().references.images.length'),1);
 const input=w.document.querySelector('#lab-image-upload');Object.defineProperty(input,'files',{value:[{...file,name:'upload.png'}]});input.dispatchEvent(new w.Event('change'));
 await new Promise(resolve=>setImmediate(resolve));assert.equal(calls.length,2);assert.equal(calls[1].payload.name,'upload.png');assert.equal(w.eval('imageParameters().references.images.length'),2);
 const text=new w.Event('paste',{bubbles:true,cancelable:true});Object.defineProperty(text,'clipboardData',{value:{items:[{kind:'string',type:'text/plain'}]}});w.document.querySelector('#lab-prompt').dispatchEvent(text);assert.equal(text.defaultPrevented,false);
});
