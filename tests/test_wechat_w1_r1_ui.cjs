const fs=require('fs'),path=require('path'),vm=require('vm'),assert=require('assert/strict');
const source=fs.readFileSync(process.env.W1_UI_SOURCE||path.join(__dirname,'../public/schedules.js'),'utf8');
const code=source.slice(source.indexOf('function runCompletion('),source.indexOf('function completionSummary('));
const busy=JSON.parse(fs.readFileSync(process.env.W1_BUSY_RECEIPT||path.join(__dirname,'fixtures/wechat_w1_model_busy.json')));const checks=[];
function run(data,status='partial',kind='wechat_radar'){const context={run:{snapshot:{kind},status,output:JSON.stringify(data)},excerpt:v=>String(v).slice(0,240)};vm.runInNewContext(code+';result=runCompletion(run)',context);return context.result;}
function check(name,data,type,status='partial'){assert.equal(run(data,status).type,type,name);checks.push(name)}
check('真实后端model_busy可续跑',busy.data,'continuation',busy.status);
for(const [name,edit] of [
 ['错误类别不能伪装续跑',d=>d.error_kind='analysis_error'],
 ['必须确实本批未发送',d=>d.error='模型执行失败'],
 ['本轮新错误仍失败',d=>d.errors=[{message:'真实执行失败'}]],
 ['非法errors仍失败',d=>d.errors={}],
 ['缺失completion不能免错',d=>delete d.completion],
 ['明确failed不能免错',d=>d.completion.type='failed'],
 ['错误处理数不能免错',d=>d.completion.processed++],
 ['已隔离数据不能免错',d=>d.quarantined=1],
 ['非分析阶段不能免错',d=>d.error_stage='publish_links']]){const d=structuredClone(busy.data);edit(d);check(name,d,'failed')}
for(const status of ['failed','timeout','cancelled','interrupted'])check(status+'原始失败不被遮盖',busy.data,'failed',status);
console.log(JSON.stringify({checks:checks.length,names:checks}));
