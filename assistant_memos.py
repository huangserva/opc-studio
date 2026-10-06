"""Bounded, local-only access to the installed MemOS Local SQLite plugin.

The Node bridge reuses the plugin's store methods. Read operations open SQLite
read-only, bypassing its migrating constructor; writes use the same methods in
one transaction. No OpenClaw runtime, model provider, viewer, or Hub is loaded.
"""
import json
import hashlib
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import tempfile
import time
from contextlib import closing


MAX_OUTPUT_BYTES = 128 * 1024
MAX_QUERY_CHARS = 500
MAX_WRITE_CHARS = 6000
DEFAULT_OWNERS = ('agent:main', 'public', 'agent:article-studio')

# All paths and owner filters below come from trusted backend configuration.
# A model can only supply a query or explicitly authorized memory text.
NODE_BRIDGE = r"""
import fs from 'node:fs';
import {createRequire} from 'node:module';
import {pathToFileURL} from 'node:url';
import {createHash} from 'node:crypto';

let db;
const hash = text => createHash('sha256').update(text).digest('hex');
const noLog = {debug(){},info(){},warn(){},error(){}};
try {
  const input = JSON.parse(fs.readFileSync(0, 'utf8'));
  const clean = value => {
    let text = String(value || '');
    for (const secret of input.sensitive_values || []) {
      if (typeof secret === 'string' && secret.length >= 6) text = text.split(secret).join('[已隐藏凭据]');
    }
    return text.replace(/-----BEGIN [^-]*PRIVATE KEY-----[\s\S]*?(?:-----END [^-]*PRIVATE KEY-----|$)/g,'[已隐藏私钥]')
      .replace(/\b(Bearer|Token)\s+[A-Za-z0-9._~+/=-]{8,}/gi,'$1 [已隐藏凭据]')
      .replace(/\b(?:sk-[A-Za-z0-9_-]{12,}|gh[pousr]_[A-Za-z0-9]{16,}|github_pat_[A-Za-z0-9_]{16,}|AIza[A-Za-z0-9_-]{20,})/gi,'[已隐藏凭据]')
      .replace(/((?:api[_ -]?key|access[_ -]?token|auth[_ -]?token|password|secret|密钥|密码)\s*["']?\s*[:=]\s*["']?)[^\s"',;]{6,}/gi,'$1[已隐藏凭据]');
  };
  const modulePath = input.plugin + '/src/storage/sqlite.ts';
  const {SqliteStore} = await import(pathToFileURL(modulePath));
  const require = createRequire(pathToFileURL(modulePath));
  const Database = require('better-sqlite3');
  db = new Database(input.db, {readonly:input.op !== 'write',fileMustExist:true,timeout:4000});
  if (input.op === 'write') db.pragma('foreign_keys = ON');
  // Constructor would run migrations. The existing database is opened as-is.
  const store = Object.create(SqliteStore.prototype);
  store.db = db;
  store.log = noLog;
  const columns = new Set(db.prepare('PRAGMA table_info(chunks)').all().map(c => c.name));
  const required = ['id','session_key','turn_id','seq','role','content','summary','owner','dedup_status','content_hash','created_at','updated_at'];
  if (required.some(c => !columns.has(c))) throw new Error('schema');
  const owners = input.owners;
  const marks = owners.map(() => '?').join(',');
  let result;
  if (input.op === 'status') {
    const total = db.prepare('SELECT count(*) AS n FROM chunks').get().n;
    const visible = db.prepare(`SELECT count(*) AS n FROM chunks WHERE owner IN (${marks}) AND dedup_status='active'`).get(...owners).n;
    const version = JSON.parse(fs.readFileSync(input.plugin + '/package.json','utf8')).version;
    result = {available:true,total_memories:total,visible_memories:visible,version};
  } else if (input.op === 'search') {
    const pool = input.limit * 5;
    const lists = [];
    lists.push(store.ftsSearch(input.query,pool,owners).map(x => x.chunkId));
    const words = input.query.replace(/[\s\p{P}\p{S}]+/gu,' ').trim().split(/\s+/).filter(Boolean);
    const patterns = new Set(words.filter(w => [...w].length === 2));
    for (const run of input.query.match(/[\u3400-\u9fff]{2,}/g) || []) {
      for (let i=0;i<run.length-1;i++) patterns.add(run.slice(i,i+2));
    }
    // Exact words also provide a bounded fallback when FTS tokenization misses.
    words.filter(w => w.length >= 3).slice(0,6).forEach(w => patterns.add(w));
    const safePatterns = [...patterns].filter(p => !/[%_]/.test(p)).slice(0,24);
    lists.push(store.patternSearch(safePatterns,{limit:pool,ownerFilter:owners}).map(x => x.chunkId));
    const scores = new Map();
    for (const list of lists) list.forEach((id,i) => scores.set(id,(scores.get(id)||0)+1/(60+i+1)));
    const entries = [...scores.entries()].sort((a,b)=>b[1]-a[1]).slice(0,input.limit);
    const results = entries.map(([id,score]) => {
      const c = store.getChunkForOwners(id,owners);
      if (!c || c.dedupStatus !== 'active') return null;
      const content = clean(c.content);
      const folded = content.toLowerCase();
      const positions = [...safePatterns,input.query].map(p=>folded.indexOf(p.toLowerCase())).filter(p=>p>=0);
      const start = positions.length ? Math.max(0,Math.min(...positions)-120) : 0;
      return {chunk_id:id,text:content.slice(start,start+1200),summary:clean(c.summary).slice(0,400),
        role:c.role,owner:c.owner,created_at:c.createdAt,score:Math.round(score*1000000)/1000000,
        start,total_chars:content.length,truncated:start>0||start+1200<content.length};
    }).filter(Boolean);
    result = {results,count:results.length,query_mode:'local_fts_pattern',semantic:false};
  } else if (input.op === 'write') {
    const id = 'article-studio-' + hash(input.workspace + ':' + input.request_id);
    const owner = 'agent:article-studio';
    result = db.transaction(() => {
      const prior = store.getChunk(id);
      if (prior) {
        if (prior.owner !== owner || prior.content !== input.text) return {conflict:true};
        return {saved:true,duplicate:true,chunk_id:prior.id};
      }
      const duplicate = store.findActiveChunkByHash(input.text,owner);
      if (duplicate) return {saved:true,duplicate:true,chunk_id:duplicate};
      const now = Date.now();
      store.insertChunk({id,sessionKey:'article-studio:'+hash(input.conversation_id||input.workspace).slice(0,32),
        turnId:'request:'+input.request_id,seq:0,role:'user',content:input.text,kind:'paragraph',summary:input.text.slice(0,400),
        taskId:null,skillId:null,owner,dedupStatus:'active',dedupTarget:null,dedupReason:null,createdAt:now,updatedAt:now});
      const verified = store.getChunk(id);
      if (!verified || verified.content !== input.text || verified.owner !== owner) throw new Error('verify');
      return {saved:true,duplicate:false,chunk_id:id};
    })();
  } else throw new Error('operation');
  process.stdout.write(JSON.stringify({ok:true,data:result}));
} catch {
  // Never return dependency exceptions, paths, credentials, or memory text.
  process.stdout.write(JSON.stringify({ok:false,error:'local_operation_failed'}));
  process.exitCode = 1;
} finally { if (db) db.close(); }
"""


def redacted_text(value, secrets=()):
    text = value if isinstance(value, str) else ''
    for secret in secrets:
        if isinstance(secret, str) and len(secret) >= 6:
            text = text.replace(secret, '[已隐藏凭据]')
    text = re.sub(r'-----BEGIN [^-]*PRIVATE KEY-----[\s\S]*?(?:-----END [^-]*PRIVATE KEY-----|$)', '[已隐藏私钥]', text)
    text = re.sub(r'(?i)\b(Bearer|Token)\s+[A-Za-z0-9._~+/=-]{8,}', r'\1 [已隐藏凭据]', text)
    text = re.sub(r'(?i)\b(?:sk-[A-Za-z0-9_-]{12,}|gh[pousr]_[A-Za-z0-9]{16,}|github_pat_[A-Za-z0-9_]{16,}|AIza[A-Za-z0-9_-]{20,})', '[已隐藏凭据]', text)
    text = re.sub(r'(?i)((?:api[_ -]?key|access[_ -]?token|auth[_ -]?token|password|secret|密钥|密码)\s*[\"\']?\s*[:=]\s*[\"\']?)[^\s\"\',;]{6,}', r'\1[已隐藏凭据]', text)
    return text


class MemosTools:
    def __init__(self, workspace, *, db_path=None, plugin_dir=None, node=None, owners=DEFAULT_OWNERS, sensitive_values=()):
        self.workspace = Path(workspace).resolve()
        self.db_path = Path(db_path or os.environ.get('ARTICLE_STUDIO_MEMOS_DB') or Path.home()/'.openclaw/memos-local/memos.db').expanduser().resolve()
        self.plugin_dir = Path(plugin_dir or os.environ.get('ARTICLE_STUDIO_MEMOS_PLUGIN') or Path.home()/'.openclaw/extensions/memos-local-openclaw-plugin').expanduser().resolve()
        self.node = node or os.environ.get('ARTICLE_STUDIO_NODE') or shutil.which('node')
        if not isinstance(owners, (tuple, list)) or not owners or len(owners)>8 or any(not isinstance(x,str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,80}',x) for x in owners):
            raise ValueError('MemOS 记忆范围配置无效')
        self.owners = tuple(dict.fromkeys((*owners,'agent:article-studio')))
        self.sensitive_values = tuple(sensitive_values)
        self.timeout = 12

    def capabilities(self):
        return {'provider': 'memos', 'storage': 'sqlite', 'local_only': True,
                'installed': self._ready(), 'tools': ['memos_status', 'memos_search', 'memos_write'],
                'owners': list(self.owners), 'query_mode': 'local_fts_pattern', 'semantic': False,
                'scope': '现有本机 MemOS Local；主 Agent、公共及工作台记忆的有限片段',
                'requires': '已安装本机插件、兼容 Node 和现有 SQLite 库；实时状态以 memos_status 为准'}

    def _ready(self):
        return bool(self.node and Path(self.node).is_file() and os.access(self.node,os.X_OK)
                    and self.db_path.is_file() and (self.plugin_dir/'src/storage/sqlite.ts').is_file())

    def _call(self, op, **args):
        if not self._ready():
            raise ValueError('MemOS Local 未就绪，请检查本机插件和 SQLite 数据库')
        payload = {'op':op,'db':str(self.db_path),'plugin':str(self.plugin_dir),'workspace':str(self.workspace),
                   'owners':list(self.owners),'sensitive_values':list(self.sensitive_values),**args}
        process = None
        with tempfile.TemporaryFile() as input_file, tempfile.TemporaryFile() as output:
            input_file.write(json.dumps(payload,ensure_ascii=False).encode());input_file.seek(0)
            try:
                process = subprocess.Popen([self.node,'--no-warnings','--experimental-transform-types','--input-type=module','-e',NODE_BRIDGE],
                    stdin=input_file,stdout=output,stderr=subprocess.DEVNULL)
                deadline = time.monotonic()+self.timeout
                while process.poll() is None:
                    if getattr(self, 'cancel_check', lambda: False)():
                        raise ValueError('定时任务已停止或超过运行上限')
                    if time.monotonic()>deadline:
                        message = 'MemOS 写入结果未确认；请先核查记忆，同一请求编号重试会去重' if op=='write' else 'MemOS Local 检索响应超时，请稍后重试'
                        raise ValueError(message)
                    if os.fstat(output.fileno()).st_size>MAX_OUTPUT_BYTES:
                        raise ValueError('MemOS Local 返回内容超过限制')
                    time.sleep(.025)
                output.seek(0);raw=output.read(MAX_OUTPUT_BYTES+1)
                if len(raw)>MAX_OUTPUT_BYTES:raise ValueError('MemOS Local 返回内容超过限制')
                try:result=json.loads(raw)
                except (ValueError,UnicodeError):raise ValueError('MemOS Local 未返回有效结果') from None
                if not isinstance(result,dict) or result.get('ok') is not True or not isinstance(result.get('data'),dict) or process.returncode:
                    raise ValueError('MemOS Local 操作失败，请检查插件兼容性和数据库权限')
                return result['data']
            except OSError:
                raise ValueError('无法运行 MemOS Local，请检查 Node 和本机插件') from None
            finally:
                if process and process.poll() is None:
                    process.terminate()
                    try:process.wait(timeout=1)
                    except subprocess.TimeoutExpired:process.kill();process.wait(timeout=1)

    def status(self):
        result = {'provider':'memos','storage':'sqlite','local_only':True,'owners':list(self.owners),
                  'query_mode':'local_fts_pattern','semantic':False}
        try:
            data=self._call('status')
            if data.get('available') is not True or any(type(data.get(k)) is not int or data[k]<0 for k in ('total_memories','visible_memories')):
                raise ValueError('MemOS Local 状态格式异常')
            writable=os.access(self.db_path,os.W_OK) and os.access(self.db_path.parent,os.W_OK)
            result.update(available=True,search_available=True,write_available=writable,
                total_memories=data['total_memories'],visible_memories=data['visible_memories'],
                version=redacted_text(data.get('version'),self.sensitive_values)[:40],
                summary='MemOS Local 可用；检索和记忆写入均在本机执行' if writable else 'MemOS Local 检索可用；当前进程没有记忆库写权限')
        except ValueError as error:
            result.update(available=False,search_available=False,write_available=False,error_code='local_unavailable',summary=str(error))
        return result

    def _text(self, value, maximum, label):
        if not isinstance(value,str) or not value.strip() or len(value)>maximum or any(ord(c)<32 and c not in '\n\t' for c in value):
            raise ValueError(f'{label}需要 1–{maximum} 个字符，不包含控制字符')
        if redacted_text(value,self.sensitive_values)!=value:
            raise ValueError(f'{label}不能包含密钥、密码或访问凭据')
        return value.strip()

    def search(self, query, limit=5):
        query=self._text(query,MAX_QUERY_CHARS,'记忆搜索词')
        if type(limit) is not int or not 1<=limit<=8:raise ValueError('记忆搜索结果数为 1–8')
        data=self._call('search',query=query,limit=limit)
        entries=data.get('results')
        if not isinstance(entries,list):raise ValueError('MemOS Local 搜索结果格式异常')
        results=[]
        for entry in entries[:limit]:
            if not isinstance(entry,dict) or not isinstance(entry.get('chunk_id'),str) or not isinstance(entry.get('text'),str):
                raise ValueError('MemOS Local 搜索结果格式异常')
            if entry.get('owner') not in self.owners:raise ValueError('MemOS Local 返回了范围外记忆')
            result={k:entry[k] for k in ('created_at','start','total_chars','truncated') if type(entry.get(k)) in (int,bool)}
            result.update(chunk_id=entry['chunk_id'][:128],text=redacted_text(entry['text'],self.sensitive_values)[:1200],
                summary=redacted_text(entry.get('summary'),self.sensitive_values)[:400],
                role=str(entry.get('role',''))[:20],owner=entry['owner'],ref='memos-local:'+entry['chunk_id'][:128])
            score=entry.get('score')
            if type(score) in (int,float) and abs(score)<=1:result['score']=score
            results.append(result)
        return {'provider':'memos','local_only':True,'query':query,'query_mode':'local_fts_pattern','semantic':False,
                'count':len(results),'results':results,'summary':f'找到 {len(results)} 条本机记忆片段；关键词匹配需核对上下文'}

    def write(self, text, request_id, conversation_id=None):
        text=self._text(text,MAX_WRITE_CHARS,'记忆内容')
        if not isinstance(request_id,str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{8,128}',request_id):
            raise ValueError('记忆写入需要有效请求编号')
        if conversation_id is not None and (not isinstance(conversation_id,str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,128}',conversation_id)):
            raise ValueError('记忆写入会话编号无效')
        # Bind every request, including content-deduplicated ones, without adding
        # tables to the user's MemOS database. The ledger stores hashes only.
        signature=hashlib.sha256(json.dumps([text,conversation_id],ensure_ascii=False).encode()).hexdigest()
        ledger=self.workspace/'.memos-write-requests.sqlite'
        try:
            self.workspace.mkdir(parents=True,exist_ok=True)
            with closing(sqlite3.connect(ledger,timeout=4)) as connection, connection:
                connection.execute('CREATE TABLE IF NOT EXISTS requests (id TEXT PRIMARY KEY, signature TEXT NOT NULL)')
                connection.execute('INSERT OR IGNORE INTO requests (id, signature) VALUES (?, ?)',(request_id,signature))
                prior=connection.execute('SELECT signature FROM requests WHERE id=?',(request_id,)).fetchone()
                if not prior or prior[0]!=signature:raise ValueError('同一记忆写入请求编号不能更改内容或会话')
        except (OSError,sqlite3.DatabaseError):
            raise ValueError('无法记录 MemOS 写入请求，请检查工作台目录权限') from None
        data=self._call('write',text=text,request_id=request_id,conversation_id=conversation_id)
        if data.get('conflict'):raise ValueError('同一记忆写入请求编号不能更改内容')
        if data.get('saved') is not True or type(data.get('duplicate')) is not bool or not isinstance(data.get('chunk_id'),str):
            raise ValueError('MemOS Local 未确认记忆保存，不能当作写入成功')
        return {'provider':'memos','local_only':True,'saved':True,'duplicate':data['duplicate'],
                'chunk_id':data['chunk_id'][:128],'request_id':request_id,'owner':'agent:article-studio',
                'summary':'这条记忆已存在，已避免重复写入' if data['duplicate'] else '已保存到本机 MemOS 工作台记忆'}
