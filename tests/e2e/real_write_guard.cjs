'use strict';
// Fail closed: only this process's generated /tmp fixture origins may write.
const fs=require('node:fs'),path=require('node:path'),os=require('node:os');
const ENABLED=true; // Mutation tests change ONLY an isolated copy of this line.
const ROOT=path.resolve(__dirname,'../..'),allowed=new Set(),reads=new Set(),blocked=[];
const reserved=new Set(['8766','3000','18766']);
function canonical(p){p=path.resolve(p);let tail=[];while(!fs.existsSync(p)){tail.unshift(path.basename(p));const parent=path.dirname(p);if(parent===p)break;p=parent;}return path.join(fs.realpathSync(p),...tail);}
function inside(p,root){return p===root||p.startsWith(root+path.sep);}
function assertSandbox(p){if(!ENABLED)return p;const real=canonical(p),tmp=canonical('/tmp');if((!inside(real,tmp)&&!inside(real,canonical(os.tmpdir())))||inside(real,canonical(path.join(ROOT,'workspace')))||inside(real,canonical(path.join(os.homedir(),'grok-bot-reports'))))throw Error('E2E_GUARD: workspace 必须是独立临时目录');return real;}
function origin(u){return new URL(u).origin;}
function deny(message,quiet=false){blocked.push(message);if(!quiet)console.log('E2E_GUARD_BLOCK '+message);throw Error('E2E_GUARD: '+message);}
function check(u,method='GET'){if(!ENABLED)return;const url=new URL(u),write=!['GET','HEAD','OPTIONS'].includes(method.toUpperCase());if(write&&!allowed.has(url.origin))deny(method+' '+url.origin+url.pathname);if(!write&&!allowed.has(url.origin)&&!reads.has(url.origin))deny('未登记读取 '+url.origin);}
function registerSandbox(base,workspace){assertSandbox(workspace);const u=new URL(base);if(ENABLED&&(!['127.0.0.1','localhost','[::1]'].includes(u.hostname)||reserved.has(u.port)||!u.port||process.env.E2E_GUARD_PRODUCTION_ORIGINS?.split(',').includes(u.origin)))deny('拒绝登记生产或非本地端口');allowed.add(u.origin);return u.origin;}
function assertSandboxBase(base){if(ENABLED&&!allowed.has(origin(base)))deny('helper 未登记隔离 base '+origin(base));}
function allowRead(base){reads.add(origin(base));}
const nativeFetch=global.fetch;
function redirect(response,url){const status=typeof response.status==='function'?response.status():response.status;const headers=typeof response.headers==='function'?response.headers():response.headers;const location=headers.get?headers.get('location'):headers.location;if(status>=300&&status<400&&location){const target=new URL(location,url);check(target.href,'GET');if(target.origin!==origin(url))deny('跨端口重定向 '+target.origin);}}
function wrapAPI(api,base){if(api.__realWriteGuard)return api;Object.defineProperty(api,'__realWriteGuard',{value:true});for(const name of ['fetch','get','head','post','put','patch','delete']){const raw=api[name].bind(api);api[name]=async function(url,opts={}){const request=typeof url==='string'?null:url;const target=request?request.url():new URL(url,base).href,method=opts.method||request?.method()||(name==='fetch'?'GET':name.toUpperCase());check(target,method);const r=await raw(url,{...opts,maxRedirects:0});redirect(r,target);return r;};}return api;}
async function guardContext(context){if(context.__realWriteGuard)return context;Object.defineProperty(context,'__realWriteGuard',{value:true});wrapAPI(context.request);
 const guardRoute=route=>{const request=route.request();try{check(request.url(),request.method());}catch(_){return route.abort('blockedbyclient');}return route.continue();};
 function protectRoute(route){return new Proxy(route,{get(target,key){if(key==='continue')return async opts=>{const request=route.request();try{check(opts?.url||request.url(),opts?.method||request.method());if(['GET','HEAD'].includes(opts?.method||request.method())&&!request.isNavigationRequest())return route.continue(opts);const response=await route.fetch({...opts,maxRedirects:0});redirect(response,request.url());return route.fulfill({response});}catch(err){await route.abort('blockedbyclient');if(!String(err).includes('E2E_GUARD'))throw err;}};if(key==='fallback')return opts=>{const request=route.request();check(opts?.url||request.url(),opts?.method||request.method());return target.fallback(opts);};if(key==='fetch')return async opts=>{const request=route.request();check(opts?.url||request.url(),opts?.method||request.method());const response=await target.fetch({...opts,maxRedirects:0});redirect(response,opts?.url||request.url());return response;};const value=target[key];return typeof value==='function'?value.bind(target):value;}});}
 function wrapRoutes(surface){const raw=surface.route.bind(surface);surface.route=(pattern,handler,opts)=>raw(pattern,route=>{try{if(!['GET','HEAD','OPTIONS'].includes(route.request().method()))check(route.request().url(),route.request().method());}catch(_){return route.abort('blockedbyclient');}return handler(protectRoute(route));},opts);}
 wrapRoutes(context);const rawNewPage=context.newPage.bind(context);context.newPage=async(...args)=>{const page=await rawNewPage(...args);wrapRoutes(page);return page;};context.on('page',page=>{if(!page.__guardRoutes){Object.defineProperty(page,'__guardRoutes',{value:true});wrapRoutes(page);}});
 await context.route('**/*',guardRoute);return context;
}
function playwright(){try{return require('playwright')}catch(_){return require(path.join(os.homedir(),'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright'));}}
let installed=false;
function install(){if(installed)return;installed=true;
 // Capture native descriptor functions before wrapping: descriptor checks must not recurse.
 const nativeStat=fs.fstatSync.bind(fs),descriptors=new Map();
 const protectedRoots=[canonical(path.join(ROOT,'workspace')),canonical(path.join(os.homedir(),'grok-bot-reports'))];
 const standardStreams=new Set();
 function protectedInode(stat){
  function scan(dir){
   let entries;try{entries=fs.readdirSync(dir,{withFileTypes:true});}catch(error){if(error.code==='ENOENT')return false;throw error;}
   for(const entry of entries){const file=path.join(dir,entry.name),value=fs.lstatSync(file);if(value.dev===stat.dev&&value.ino===stat.ino)return true;if(entry.isDirectory()&&scan(file))return true;}
   return false;
  }
  return protectedRoots.some(scan);
 }
 const writeMask=['O_WRONLY','O_RDWR','O_TRUNC','O_APPEND','O_CREAT','O_EXCL'].reduce((mask,name)=>mask|(fs.constants[name]||0),0);
 const writes=flags=>typeof flags==='number'?(flags&writeMask)!==0:typeof flags==='string'&&/[wa+]/.test(flags);
 function writable(file){
  if(!ENABLED)return;
  if(file&&typeof file==='object'&&Number.isInteger(file.fd))return writable(file.fd);
  if(typeof file==='number'){
   if(descriptors.has(file))return writable(descriptors.get(file));
   const stat=nativeStat(file);
   if(file>=0&&file<=2){
    const identity=stat.dev+':'+stat.ino;
    if(!stat.isFile()||standardStreams.has(identity))return;
    if(protectedInode(stat))deny('标准输出指向正式目录 '+file,true);
    standardStreams.add(identity);return;
   }
   // A descriptor inherited/opened before installation has no trustworthy path.
   // Fail closed for regular files; pipes/TTYs (including stdout) do not mutate files.
   if(nativeStat(file).isFile())deny('来源未登记的文件描述符写入 '+file,true);
   return;
  }
  const input=Buffer.isBuffer(file)?file.toString():file instanceof URL?require('node:url').fileURLToPath(file):String(file);
  const real=canonical(input),lexical=path.resolve(input);
  if(protectedRoots.some(root=>inside(real,root)||inside(lexical,root)))deny('正式目录写入 '+real,true);
  if(fs.existsSync(real)){const stat=fs.statSync(real);if(stat.isFile()&&stat.nlink>1&&protectedInode(stat))deny('硬链接指向正式文件 '+real,true);}
 }
 const pathMethods=['writeFile','appendFile','mkdir','mkdtemp','mkdtempDisposable','rm','rmdir','unlink','truncate','chmod','chown','lchmod','lchown','utimes','lutimes'];
 const fdMethods=['write','writev','ftruncate','fchmod','fchown','futimes','fsync','fdatasync'];
 function checkArgs(name,args){
  if(name==='rename'||name==='link'){writable(args[0]);writable(args[1]);}
  else writable(args[['copyFile','cp','symlink'].includes(name)?1:0]);
 }
 for(const name of [...pathMethods,...fdMethods,'rename','copyFile','cp','symlink','link'])for(const suffix of ['', 'Sync']){
  const key=name+suffix,raw=fs[key];if(typeof raw!=='function')continue;
  fs[key]=function(...args){checkArgs(name,args);return raw.apply(this,args);};
 }
 for(const name of [...pathMethods,'rename','copyFile','cp','symlink','link']){
  const raw=fs.promises[name];if(typeof raw!=='function')continue;
  fs.promises[name]=async function(...args){checkArgs(name,args);return raw.apply(this,args);};
 }
 const rawOpenSync=fs.openSync,rawOpen=fs.open,rawPromiseOpen=fs.promises.open;
 fs.openSync=function(file,flags,...args){if(writes(flags))writable(file);const fd=rawOpenSync.call(this,file,flags,...args);descriptors.set(fd,file);return fd;};
 fs.open=function(file,flags,...args){if(writes(flags))writable(file);const cb=args.pop();return rawOpen.call(this,file,flags,...args,(err,fd)=>{if(!err)descriptors.set(fd,file);cb(err,fd);});};
 function protectHandle(handle,file){
  descriptors.set(handle.fd,file);
  // Patch the shared FileHandle prototype when a guarded open returns a handle.
  const proto=Object.getPrototypeOf(handle);
  if(!proto.__realWriteGuard){Object.defineProperty(proto,'__realWriteGuard',{value:true});
   for(const key of ['write','writev','writeFile','appendFile','truncate','chmod','chown','utimes','sync','datasync','createWriteStream']){
    const raw=proto[key];if(typeof raw!=='function')continue;
    proto[key]=function(...args){writable(this.fd);return raw.apply(this,args);};
   }
   const close=proto.close;
   // close is an instance property in some Node versions; stale fd entries are overwritten on every open.
   if(typeof close==='function')proto.close=function(...args){const fd=this.fd;descriptors.delete(fd);return close.apply(this,args);};
  }
  const close=handle.close.bind(handle);handle.close=(...args)=>{descriptors.delete(handle.fd);return close(...args);};
  return handle;
 }
 fs.promises.open=async function(file,flags,...args){if(writes(flags))writable(file);return protectHandle(await rawPromiseOpen.call(this,file,flags,...args),file);};
 const close=fs.close,closeSync=fs.closeSync;
 fs.close=function(fd,...args){descriptors.delete(fd);return close.call(this,fd,...args);};
 fs.closeSync=function(fd,...args){descriptors.delete(fd);return closeSync.call(this,fd,...args);};
 for(const key of ['createWriteStream','WriteStream']){const raw=fs[key];fs[key]=function(file,opts,...args){writable(opts?.fd??file);return Reflect.construct(raw,[file,opts,...args],new.target||raw);};fs[key].prototype=raw.prototype;}
 // createReadStream can accept write-capable flags and truncate during its open.
 for(const key of ['createReadStream','ReadStream']){const raw=fs[key];fs[key]=function(file,opts,...args){if(writes(opts?.flags))writable(opts?.fd??file);return Reflect.construct(raw,[file,opts,...args],new.target||raw);};fs[key].prototype=raw.prototype;}
 require('node:module').syncBuiltinESMExports();

 global.fetch=async function(input,opts={}){const url=typeof input==='string'||input instanceof URL?String(input):input.url;check(url,opts.method||input.method||'GET');const r=await nativeFetch(input,{...opts,redirect:'manual'});redirect(r,url);return r;};
 for(const protocol of ['http','https']){const mod=require('node:'+protocol),raw=mod.request;mod.request=function(input,options,callback){const opts=typeof input==='object'&&!(input instanceof URL)?input:(typeof options==='object'?options:{});const url=typeof input==='string'||input instanceof URL?String(input):protocol+'://'+(opts.hostname||opts.host||'localhost')+(opts.port?':'+opts.port:'')+(opts.path||'/');check(url,opts.method||'GET');return raw.apply(this,arguments);};mod.get=function(...args){const req=mod.request(...args);req.end();return req;};}
 const pw=playwright();for(const type of [pw.chromium,pw.firefox,pw.webkit]){const raw=type.launchPersistentContext.bind(type);type.launchPersistentContext=async(profile,opts)=>{assertSandbox(profile);return guardContext(await raw(profile,{...opts,serviceWorkers:'block'}));};const launch=type.launch.bind(type);type.launch=async(...args)=>{const browser=await launch(...args),newContext=browser.newContext.bind(browser);browser.newContext=async(opts)=>guardContext(await newContext({...opts,serviceWorkers:'block'}));const newPage=browser.newPage.bind(browser);browser.newPage=async(opts)=>{const page=await newPage({...opts,serviceWorkers:'block'});await guardContext(page.context());return page;};return browser;};}
 const newContext=pw.request.newContext.bind(pw.request);pw.request.newContext=async opts=>wrapAPI(await newContext({...opts,ignoreHTTPSErrors:false}),opts?.baseURL);
}
function argument(name,fallback){const i=process.argv.indexOf(name);const inline=process.argv.find(x=>x.startsWith(name+'='));return inline?inline.slice(name.length+1):i<0?fallback:process.argv[i+1];}
async function readOnlyMain(script){const base=origin(argument('--base',process.env.E2E_REAL_BASE||'http://127.0.0.1:8766'));allowRead(base);const tmp=fs.mkdtempSync('/tmp/e2e-readonly-');let context;try{const pw=playwright();context=await pw.chromium.launchPersistentContext(path.join(tmp,'profile'),{headless:true,channel:'chrome',args:['--disable-background-networking','--disable-component-update']});const page=await context.newPage();const target=script==='interest_channel'?'today.html?channel=interest':script==='write_and_publish'?'publish.html':script==='library_page'?'library.html':'today.html';await page.goto(base+'/'+target);await page.locator('body').waitFor();const r=await context.request.get(base+'/api/items');if(!r.ok())throw Error('只读 API '+r.status());
 if(process.argv.includes('--safety-probe')){const identity=await context.request.get(base+'/__guard_fixture_identity');if(!identity.ok())throw Error('仅允许私有夹具探针');const proof=await identity.json();if(proof.nonce!==process.env.E2E_GUARD_FIXTURE_NONCE||!proof.nonce)throw Error('夹具身份不符');assertSandbox(proof.workspace);if(reserved.has(new URL(base).port))throw Error('生产端口禁止探针');let escaped=0;const data={id:'guard:'+script,title:'隔离防护反例',full_text:'synthetic',sources:['manual']},headers={Origin:base,'X-Article-Studio':'1'};for(const action of [()=>context.request.post(base+'/api/items',{data,headers}),()=>fetch(base+'/api/items',{method:'POST',headers:{...headers,'Content-Type':'application/json'},body:JSON.stringify({...data,id:data.id+':node'})}),()=>page.evaluate(async({base,data})=>fetch(base+'/api/items',{method:'POST',headers:{'Content-Type':'application/json','X-Article-Studio':'1'},body:JSON.stringify({...data,id:data.id+':browser'})}).then(r=>r.status),{base,data})]){try{await action();escaped++;}catch(_){}}if(escaped)throw Error('F: '+escaped+' 个写通道逃逸');if(blocked.length<3)throw Error('F: 缺少阻断证据');}
 await page.screenshot({path:path.join(tmp,script+'.png'),mask:[page.locator('main')]});console.log(script+' --real 只读 GET，通过；证据 '+tmp);
 }finally{await context?.close();}}
module.exports={install,assertSandbox,registerSandbox,assertSandboxBase,allowRead,check,blocked,guardContext,playwright,readOnlyMain,canonical};
