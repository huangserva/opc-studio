#!/usr/bin/env python3
"""One-time SD/JD import; explicit dry-run/provision/push/verify stages.

Never instantiates source stores, changes ledger schema, or retries uncertain
writes. Curated decisions are evidence-backed input, not model output.
"""
import argparse
from datetime import datetime,timezone
import fcntl
import hashlib
import json
from contextlib import closing
import os
import tempfile
from pathlib import Path
import re
import sqlite3
import sys
import time

ROOT=Path('/Users/huangzongning/development/article-studio')
PUBLIC_KB_ID='82b87a9e-2154-4ac4-8615-55bb26e2a9dd'
LEGACY_PRIVATE_KB_ID='cedb34b6-3cdd-461f-a3d5-e28b4739736c'
REQUIRED_TAG_NAMES=({'类型-'+v for v in ('商单','待办','热点','工具','知识','人物','决定','进展','长文·作品','长文·外部参考')} |
                    {'来源-'+v for v in ('微信私聊','微信群','邮件','GitHub','推特','linux.do','Agent会话','飞书')} |
                    {'隐私-'+v for v in ('公开','群内','私密','内部')})
STAGES={'contact':'建联','negotiating':'在谈','preparing':'做资料','writing':'待写','draft':'完成初稿','published':'已发布','closed':'结案','unknown':'进度待核对','cancelled':'已取消'}
COLS=['result_number','knowledge_base_id','knowledge_id','content_sha256','tag_ids_json','last_pushed_at','remote_state']


def digest(value):return hashlib.sha256(value.encode()).hexdigest()
def write_json(path,value):
 path=Path(path);temp=path.with_name(path.name+'.tmp');temp.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n');temp.chmod(0o600);temp.replace(path)
def load(path):return json.loads(Path(path).read_text())
def clean(value):
 value=str(value or '')
 value=re.sub(r'[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}', '[邮箱已脱敏]',value)
 value=re.sub(r'(?<!\d)(?:\+?86[- ]?)?1[3-9]\d{9}(?!\d)','[手机号已脱敏]',value)
 value=re.sub(r'\bwxid_[A-Za-z0-9_-]+\b','[微信账号已脱敏]',value,flags=re.I)
 value=re.sub(r'(?:微信号|微信|QQ|账号|电话|手机号)\s*[:：=]\s*[A-Za-z0-9_+@.-]+','[联系方式已脱敏]',value,flags=re.I)
 return value

def make_deal(d,audit=()):
 ident=d['id']
 if not re.fullmatch('[a-f0-9]{32}',ident):raise ValueError('Invalid SD source id')
 # Only explicit structured source evidence is trusted. A legacy bookkeeping
 # bucket (original_group=未付/已付/待写...) is never a chat-room identity.
 evidence=d.get('sources') or []
 kinds={s.get('chat_type') or s.get('conversation_type') for s in evidence if isinstance(s,dict)}
 ids=[str(s.get('chat_id') or s.get('chatroom_id') or '') for s in evidence if isinstance(s,dict)]
 if 'private' in kinds or any(x and not x.endswith('@chatroom') for x in ids):
  target,privacy,source,reason='private','私密','微信私聊','明确私聊来源或私聊会话ID'
 elif kinds and kinds <= {'group'} or ids and all(x.endswith('@chatroom') for x in ids):
  target,privacy,source,reason='public','群内','微信群','明确群聊来源/会话ID'
 else:
  target,privacy,source,reason='private','私密','未记录','原聊天来源未记录；sources为空或不可判定，按私密处理，不打来源标签'
 number='SD-'+ident;title='【商单】'+number+' '+clean(d['title'])
 quote='未确认'
 if d.get('quote') is not None:
  quote=f"{d.get('quote')} {d.get('currency') or '币种未记录'}（{'已确认字段，仍待整体核对' if d.get('quote_confirmed') else '表内未确认报价'}）"
 dates=[d.get(k) for k in ['contacted_on','signed_on'] if d.get(k)]
 event_time=min(dates) if dates else '业务时间未记录（建档时间不当作业务发生时间）'
 refs=f"workspace/deals/deals.db：deals.id={ident}，revision={d['revision']}；旧导入分组={d.get('original_group') or '空'}（不是聊天来源证据）"
 changes='\n'.join(f"- {r['at']}：revision={r['revision']}，{clean(r['action'])}" for r in audit) or '- 无逐次变更记录'
 receipts=[r for r in d.get('receipts',[]) if not r.get('voided_at')]
 receipt_text='；'.join(f"{r.get('date') or r.get('received_on') or '日期未记录'} {r.get('amount')} {r.get('currency') or ''}" for r in receipts) or '未登记已核对到账明细；旧清单付否分组不等于实际核实到账'
 content=f'''# {title}
- 状态：待核对
- 编号：{number}
- 类型：商单
- 摘要：{clean(d['customer'])}的「{clean(d['title'])}」商单，账面阶段{STAGES.get(d.get('stage'),d.get('stage'))}，整条待核对。
- 隐私：{privacy}
- 来源：{source}（{reason}）
- 原文位置：{refs}
- 时间：{event_time}
- 涉及：{clean(d.get('customer'))}；项目：{clean(d['title'])}
- 关联：工作台商单 {ident}
- 更新：{d.get('updated_at') or '未记录'}
- 对接人：未复制联系方式，回工作台核对
- 账面阶段：{STAGES.get(d.get('stage'),d.get('stage'))}（不代表本次已核实）

## 要点
- 品牌/产品：{clean(d.get('customer')) or '未记录'}
- 内容形式：{clean(d.get('agreement')) or '未记录，不从标题猜测'}
- 报价：{quote}
- 报价说明：{clean(d.get('price_note')) or '无'}
- 交付时间：{d.get('deadline') or '未记录'}
- 交付数量：{d.get('expected_deliverables') if d.get('expected_deliverables') is not None else '未记录'}
- 发布平台：{'有工作台发布登记，请回工作台核对' if d.get('publications') else '未记录'}
- 到账明细：{receipt_text}
- 备注：{clean(d.get('notes')) or '无'}
- 来源判定：{reason}

## 变更记录
{changes}

私密成果不复制原话、联系方式、账号；完整原始记录保留在本机工作台。
'''
 if target=='public':
  content+='\n## 原话\n'+str(d.get('original_text') or '未记录')+'\n'
 return pack(number,title,content,target,['类型-商单']+(['来源-'+source] if source!='未记录' else [])+['隐私-'+privacy],refs,reason)


_DATE=r'(?<![\d-])(?:(2026)-)?(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])(?![\d-])'
def _date_value(match):
 try:return datetime(2026,int(match.group(2)),int(match.group(3))).date().isoformat()
 except ValueError:return None

def _entry_date(line):
 # The entry's leading date is authoritative even if another date occurs in
 # the decision's content (for example an earlier revoked policy).
 bare=re.sub(r'^[\s>*#-]+','',line)
 if '可读部分：' in bare:bare=bare.split('可读部分：',1)[1]
 m=re.match(_DATE,bare)
 if m:return _date_value(m)
 m=re.search(r'黄总[^：。；→\n]{0,30}?'+_DATE,line)
 if m:return _date_value(m)
 m=re.search(r'日期[：:]\s*'+_DATE,line)
 if m:return _date_value(m)
 m=re.search(r'[（(]'+_DATE+r'[^）)\n]{0,20}黄总',line)
 return _date_value(m) if m else None

def decision_date(lines,line_number):
 index=line_number-1;direct=_entry_date(lines[index])
 if direct:return direct,'该条记录第 '+str(line_number)+' 行'
 headings=[];parents=[]
 for i,line in enumerate(lines[:index+1]):
  heading=re.match(r'^(#{1,6})\s+',line)
  if heading:
   level=len(heading.group(1))
   headings=[h for h in headings if h[0]<level]
   headings.append((level,i,line));parents=[]
  bullet=re.match(r'^(\s*)[-*]\s+',line)
  if bullet:
   indent=len(bullet.group(1))
   parents=[p for p in parents if p[0]<indent]
   if i<index:parents.append((indent,i,line))
 for _,i,line in reversed(parents):
  # Only a leading date defines an enclosing dated entry. An incidental
  # owner date in a long-running task's title must not date later updates.
  match=re.match(r'^\s*[-*]\s+'+_DATE,line)
  # A task's opening date does not date its subsequent multi-day log.
  suffix=line[match.end():].strip(' ：:') if match else None
  when=_date_value(match) if match and suffix in {'','本轮','日志','记录','决定','黄总决定'} else None
  if when:return when,'所属日期段第 '+str(i+1)+' 行'
 for _,i,line in reversed(headings):
  matches=list(re.finditer(_DATE,line))
  # A date range is not a precise decision date.
  if len(matches)==1:
   when=_date_value(matches[0])
   if when:return when,'所属日期段第 '+str(i+1)+' 行'
 return '未记录','该条记录及所属日期段均无明确日期'


def make_decision(r,source_dir):
 text=(Path(source_dir)/r['file']).read_text();quote=r['quote']
 if not quote.strip() or quote not in text:raise ValueError('Decision quotation missing from frozen source')
 if not isinstance(r['line'],int) or r['line']<1:raise ValueError('Invalid decision source line')
 source_lines=text.splitlines()
 window='\n'.join(source_lines[r['line']-1:r['line']-1+len(quote.splitlines())])
 if quote not in window:raise ValueError('Decision quotation belongs to a different source position')
 expected='JD-'+Path(r['file']).stem+'-'+digest(quote)[:10]
 if r['result_number']!=expected:raise ValueError('Decision id does not match source quote')
 refs=f"coordination/{r['file']}:{r['line']}；来源SHA256={digest(text)}"
 aliases=r.get('aliases') or []
 for a in aliases:
  alias_lines=(Path(source_dir)/a['file']).read_text().splitlines()
  window='\n'.join(alias_lines[a['line']-1:a['line']-1+len(a['quote'].splitlines())])
  if a['quote'] not in window:raise ValueError('Merged decision source mismatch')
 alias_text='\n'.join('- coordination/'+a['file']+':'+str(a['line']) for a in aliases)
 replacements='\n'.join('- '+x['result_number']+'（'+x['scope']+'）' for x in r.get('supersedes',[])) or '无已明确对应的旧决定'
 when,date_ref=decision_date(source_lines,r['line'])
 owner_quotes=r.get('owner_quotes') or [quote]
 if any(not q.strip() or q not in quote for q in owner_quotes):raise ValueError('Owner quotation is not a literal excerpt')
 title='【决定】'+expected+' '+r['title']
 content=f'''# {title}
- 编号：{expected}
- 类型：决定
- 摘要：{r['title']}。
- 隐私：内部
- 来源：Agent会话
- 原文位置：{refs}
- 时间：{when}
- 时间依据：{date_ref}
- 涉及：黄总；项目：article-studio
- 关联：{'; '.join('coordination/'+a['file']+':'+str(a['line']) for a in aliases) or '无重复记载'}
- 更新：2026-11-03（本次冻结整理批次）
- 状态：{r['status']}{'；已被 '+r['replaced_by']+' 取代' if r.get('replaced_by') else ''}
- 拍板人：黄总（来源为项目记录，非逐字语音转录）

## 决定了什么
{r['title']}。具体条件与范围以以下记录原文为准，不将记录中的主控建议或执行状态提升为黄总新决定。

## 为什么
记录没有独立解释的原因不补造；有原因的见原话。

## 影响什么
article-studio对应功能或协作安排；历史一次性授权只作知识归档，不构成本次或未来操作授权。

## 替代了哪条旧决定
{replacements}

## 原话
{chr(10).join('> '+line for q in owner_quotes for line in q.splitlines())}

## 同一决定的合并出处
{alias_text or '无'}
'''
 return pack(expected,title,content,'public',['类型-决定','来源-Agent会话','隐私-内部'],refs,'仅收明确黄总决定；隐私内部与正文一致')


def pack(number,title,content,target,tags,refs,reason):
 if not re.fullmatch(r'(?:SD|JD)-[A-Za-z0-9_-]+',number):raise ValueError('Invalid result number')
 return {'result_number':number,'title':title,'content':content,'target':target,'tag_names':tags,'content_sha256':digest(content),'source_ref':refs,'routing_reason':reason}

def prepare(args):
 with sqlite3.connect(Path(args.deals).resolve().as_uri()+'?mode=ro',uri=True) as db:
  db.row_factory=sqlite3.Row
  deals=[json.loads(r['data']) for r in db.execute('SELECT data FROM deals ORDER BY id')]
  audit={d['id']:[dict(r) for r in db.execute('SELECT revision,at,action FROM audit WHERE deal_id=? ORDER BY revision',(d['id'],))] for d in deals}
 rows=[make_deal(d,audit[d['id']]) for d in deals]+[make_decision(r,args.sources) for r in load(args.decisions)]
 validate(rows)
 client=client_for(args.root)
 if any(client.config.api_key in r['content'] for r in rows):raise ValueError('Credential detected in input; stopped before writing')
 write_json(args.manifest,rows)
 summary={'total':len(rows),'SD':sum(r['result_number'].startswith('SD-') for r in rows),'JD':sum(r['result_number'].startswith('JD-') for r in rows),'public':sum(r['target']=='public' for r in rows),'private':sum(r['target']=='private' for r in rows)}
 write_json(Path(args.manifest).with_name('dry-run-summary.json'),summary);print(json.dumps(summary))

def validate(rows):
 if len({r['result_number'] for r in rows})!=len(rows):raise ValueError('Duplicate result number')
 for r in rows:
  if not r['result_number'].startswith(('SD-','JD-')) or r['target'] not in ('public','private'):raise ValueError('Result outside authorized prefix or library')
  if r['content_sha256']!=digest(r['content']):raise ValueError('Manifest hash mismatch')
  if len(r['tag_names'])!=len(set(r['tag_names'])) or not 1<=len(r['tag_names'])<=3:raise ValueError('Invalid tag groups')
  if r['result_number'].startswith('JD-') and set(r['tag_names'])!={'类型-决定','来源-Agent会话','隐私-内部'}:raise ValueError('JD requires internal privacy tag')
  if r['target']=='private' and '隐私-私密' not in r['tag_names']:raise ValueError('Private result has wrong privacy tag')
  if r['result_number'].startswith('SD-') and '- 状态：待核对' not in r['content'].splitlines()[:3]:raise ValueError('SD requires opening pending-review status')


def client_for(root):
 sys.path.insert(0,str(Path(root).resolve()))
 from weknora_client import WeKnoraClient
 return WeKnoraClient.from_env_file(Path(root)/'.env.local')
def tag_rows(response):return response['data']['data']
def kb_id(value):
 if not re.fullmatch('[A-Za-z0-9_-]{1,128}',value):raise ValueError('Invalid remote id')
 return value

def public_library(config):
 # target remains a privacy classification, not a library selector.
 library=config['knowledge_bases']['public']
 if library['knowledge_base_id']!=PUBLIC_KB_ID:raise ValueError('Public library id differs from approved destination')
 return library


def destination(record,config):
 library=public_library(config)
 try:tags=[kb_id(library['tags'][name]) for name in record['tag_names']]
 except (KeyError,TypeError):raise ValueError('Public library lacks required named tags') from None
 if len(set(tags))!=len(tags):raise ValueError('Public tag ids are not distinct')
 return library['knowledge_base_id'],tags


def provision(args):
 # Validation only: never create libraries/tags, change settings or rewrite config.
 config=load(Path(args.root)/'coordination/weknora-tags.json');library=public_library(config)
 if not REQUIRED_TAG_NAMES<=set(library['tags']):raise ValueError('Public tag configuration incomplete')
 client=client_for(args.root);public=client._request('GET','/knowledge-bases/'+kb_id(PUBLIC_KB_ID))['data']
 if public.get('id')!=PUBLIC_KB_ID:raise ValueError('Public library not confirmed')
 tags={}
 for page in range(1,101):
  batch=tag_rows(client.list_tags(PUBLIC_KB_ID,page=page,page_size=50))
  for tag in batch:
   name,ident=tag['name'],tag['id']
   if name in tags and tags[name]!=ident:raise ValueError('Ambiguous public tag name')
   tags[name]=ident
  if len(batch)<50:break
 else:raise ValueError('Public tag pagination did not terminate')
 if any(tags.get(name)!=ident for name,ident in library['tags'].items()):raise ValueError('Public remote tags missing or ids differ')
 print(json.dumps({'public_id':PUBLIC_KB_ID,'public_tags':len(library['tags']),'readonly_verified':True}))


def ledger_connect(path,readonly=False):
 path=Path(path).resolve()
 if not path.is_file():raise ValueError('Shared ledger missing; no database created')
 db=sqlite3.connect(path.as_uri()+('?mode=ro' if readonly else '?mode=rw'),uri=True,timeout=30);db.row_factory=sqlite3.Row
 if readonly:db.execute('PRAGMA query_only=ON')
 if [r['name'] for r in db.execute('PRAGMA table_info(result_ledger)')]!=COLS:db.close();raise ValueError('Unexpected shared ledger schema; no DDL performed')
 return db



def manual_payload(record):
 payload={'title':record['title'],'content':record['content'],'status':'publish'}
 # Private results already include a hand-curated summary. Leave questions
 # and graph defaults untouched; public documents use all library defaults.
 if record['target']=='private':payload['process_config']={'summary_enabled':False}
 return payload


def push_one(client,db,record,kb,tags,journal,journal_path):
 number=record['result_number']
 if not number.startswith(('SD-','JD-')):raise ValueError('Unowned result prefix')
 row=db.execute('SELECT * FROM result_ledger WHERE result_number=?',(number,)).fetchone()
 prior=journal.get(number)
 if row:
  if row['remote_state']!='active' or row['knowledge_base_id']!=kb:raise ValueError('Ledger state/library mismatch; manual resolution required')
  ident=row['knowledge_id'];doc=client.get_knowledge(ident)['data'] # 404 propagates; never recreates.
  if doc['knowledge_base_id']!=kb:raise ValueError('Remote knowledge library mismatch')
  if row['content_sha256']==record['content_sha256']:
   if digest(doc['metadata']['content'])!=record['content_sha256']:raise ValueError('Remote content drift; refusing silent overwrite')
   action='skip'
  else:
   journal[number]={'state':'update-sent','knowledge_id':ident};write_json(journal_path,journal)
   payload=manual_payload(record)
   client._request('PUT','/knowledge/manual/'+kb_id(ident),payload);action='update'
 elif prior and prior.get('knowledge_id'):
  ident=prior['knowledge_id'];doc=client.get_knowledge(ident)['data']
  if doc['knowledge_base_id']!=kb or digest(doc['metadata']['content'])!=record['content_sha256']:raise ValueError('Interrupted write cannot be reconciled')
  action='recover'
 elif prior:
  raise ValueError('Uncertain previous create; manual reconciliation required, no retry')
 else:
  journal[number]={'state':'create-sent'};write_json(journal_path,journal)
  payload=manual_payload(record);payload.update(tag_ids=tags,channel='api')
  ident=client._request('POST','/knowledge-bases/'+kb_id(kb)+'/knowledge/manual',payload)['data']['id'];action='create'
  journal[number]={'state':'created','knowledge_id':ident};write_json(journal_path,journal)
 if action not in ('create',):
  doc=client.get_knowledge(ident)['data']
  if {x['id'] for x in (doc.get('tags') or [])}!=set(tags):client.replace_tags(ident,tags,kb)
 # No schema changes and no REPLACE: only this SD-/JD- primary key changes.
 values=(number,kb,ident,record['content_sha256'],json.dumps(tags),datetime.now(timezone.utc).isoformat(),'active')
 if action!='skip' or not row or set(json.loads(row['tag_ids_json']))!=set(tags):
  with db:db.execute('INSERT INTO result_ledger VALUES (?,?,?,?,?,?,?) ON CONFLICT(result_number) DO UPDATE SET knowledge_base_id=excluded.knowledge_base_id,knowledge_id=excluded.knowledge_id,content_sha256=excluded.content_sha256,tag_ids_json=excluded.tag_ids_json,last_pushed_at=excluded.last_pushed_at,remote_state=excluded.remote_state',values)
 journal[number]={'state':'recorded','knowledge_id':ident,'action':action};write_json(journal_path,journal)
 return {'result_number':number,'knowledge_id':ident,'action':action,'target':record['target']}


def push(args):
 rows=load(args.manifest);validate(rows)
 config=load(Path(args.root)/'coordination/weknora-tags.json')
 if args.numbers:
  wanted=set(args.numbers.split(','));rows=[r for r in rows if r['result_number'] in wanted]
  if {r['result_number'] for r in rows}!=wanted:raise ValueError('Unknown requested result number')
 # Resolve every named tag before sending the first write.
 routes=[destination(record,config) for record in rows]
 if getattr(args,'dry_run',False):
  plans=[]
  with closing(ledger_connect(Path(args.root)/'workspace/weknora/ledger.db',readonly=True)) as db:
   for record,(kb,tags) in zip(rows,routes):
    row=db.execute('SELECT * FROM result_ledger WHERE result_number=?',(record['result_number'],)).fetchone()
    action=('create' if row is None else 'resolve-ledger-state' if row['remote_state']!='active' else
            'relocate-private-first' if row['knowledge_base_id']==LEGACY_PRIVATE_KB_ID else
            'resolve-library-mismatch' if row['knowledge_base_id']!=kb else
            'update' if row['content_sha256']!=record['content_sha256'] else
            'retag' if set(json.loads(row['tag_ids_json']))!=set(tags) else 'skip')
    plans.append({'result_number':record['result_number'],'privacy_target':record['target'],'knowledge_base_id':kb,'tag_ids':tags,'action':action})
  print(json.dumps({'dry_run':True,'processed':len(plans),'basis':'local ledger only; remote content not checked','rows':plans},ensure_ascii=False))
  return
 client=client_for(args.root)
 if any(client.config.api_key in r['content'] for r in rows):raise ValueError('Credential detected in manifest')
 evidence=Path(args.evidence);journal_path=evidence/'write-journal.json';journal=load(journal_path) if journal_path.exists() else {}
 with (evidence/'push.lock').open('w') as lock:
  fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
  with closing(ledger_connect(Path(args.root)/'workspace/weknora/ledger.db')) as db,db:
   counts={}
   for record,(kb,tags) in zip(rows,routes):
    result=push_one(client,db,record,kb,tags,journal,journal_path)
    counts[result['action']]=counts.get(result['action'],0)+1
    with (evidence/'push-events.jsonl').open('a') as output:output.write(json.dumps(result)+'\n')
 print(json.dumps({'processed':len(rows),'actions':counts}))


def _private_rows(db):
 return [dict(row) for row in db.execute('SELECT * FROM result_ledger WHERE knowledge_base_id=? ORDER BY result_number',(LEGACY_PRIVATE_KB_ID,))]


def relocation_plan(client,rows,config):
 library=public_library(config)
 legacy=config.get('knowledge_bases',{}).get('private',{})
 if legacy.get('knowledge_base_id')!=LEGACY_PRIVATE_KB_ID:raise ValueError('Legacy private tag mapping missing')
 by_id={}
 for name,ident in legacy.get('tags',{}).items():
  if ident in by_id:raise ValueError('Ambiguous legacy tag id')
  by_id[ident]=name
 plans=[]
 for row in rows:
  if not row['result_number'].startswith(('SD-','JD-')):raise ValueError('Legacy library contains unowned result prefix')
  try:
   old_tags=json.loads(row['tag_ids_json'])
   if not isinstance(old_tags,list) or not old_tags or any(not isinstance(t,str) for t in old_tags) or len(set(old_tags))!=len(old_tags):raise ValueError('Invalid ledger tags')
   tags=[kb_id(library['tags'][by_id[ident]]) for ident in old_tags]
  except (KeyError,TypeError):raise ValueError('Tag name mapping missing; entire relocation stopped') from None
  if len(set(tags))!=len(tags):raise ValueError('Mapped public tag ids are not distinct')
  doc=client.get_knowledge(kb_id(row['knowledge_id']))['data']
  if doc.get('id')!=row['knowledge_id'] or doc.get('knowledge_base_id')!=PUBLIC_KB_ID:
   raise ValueError('Remote item has not moved to public library; entire relocation stopped')
  plans.append({'result_number':row['result_number'],'knowledge_id':row['knowledge_id'],'knowledge_base_id':PUBLIC_KB_ID,'tag_ids':tags})
 return plans


def backup_ledger(path,evidence):
 evidence=Path(evidence);evidence.mkdir(parents=True,exist_ok=True)
 fd,name=tempfile.mkstemp(prefix='ledger-before-relocate-private-',suffix='.db',dir=evidence)
 os.close(fd)
 backup=Path(name)
 # Separate read-only source connection: backup on a connection holding an
 # active write transaction can wait indefinitely. BEGIN IMMEDIATE in caller
 # keeps other writers out while this consistent SQLite backup is taken.
 with closing(ledger_connect(path,readonly=True)) as source,closing(sqlite3.connect(backup)) as target:
  source.backup(target)
  if target.execute('PRAGMA quick_check').fetchone()[0]!='ok':raise ValueError('Ledger backup failed integrity check')
 return backup


def relocate_private(args):
 path=Path(args.root)/'workspace/weknora/ledger.db'
 with closing(ledger_connect(path,readonly=True)) as db:rows=_private_rows(db)
 if not rows:
  print(json.dumps({'dry_run':bool(getattr(args,'dry_run',False)),'processed':0,'rows':[]}));return
 config=load(Path(args.root)/'coordination/weknora-tags.json')
 # Preflight the whole batch using GET only, before a backup or any UPDATE.
 plans=relocation_plan(client_for(args.root),rows,config)
 if getattr(args,'dry_run',False):
  print(json.dumps({'dry_run':True,'processed':len(plans),'rows':plans},ensure_ascii=False));return
 with closing(ledger_connect(path)) as db:
  try:
   db.execute('BEGIN IMMEDIATE')
   if _private_rows(db)!=rows:raise ValueError('Ledger changed during preflight; no rows updated')
   backup=backup_ledger(path,args.evidence)
   for old,plan in zip(rows,plans):
    encoded=json.dumps(plan['tag_ids'])
    cursor=db.execute('UPDATE result_ledger SET knowledge_base_id=?,tag_ids_json=? WHERE result_number=? AND knowledge_base_id=?',(PUBLIC_KB_ID,encoded,old['result_number'],LEGACY_PRIVATE_KB_ID))
    if cursor.rowcount!=1:raise ValueError('Ledger relocation row count mismatch')
    actual=dict(db.execute('SELECT * FROM result_ledger WHERE result_number=?',(old['result_number'],)).fetchone())
    if actual!={**old,'knowledge_base_id':PUBLIC_KB_ID,'tag_ids_json':encoded}:raise ValueError('Ledger relocation readback mismatch')
   if _private_rows(db):raise ValueError('Legacy ledger rows remain; transaction rolled back')
   db.commit()
  except BaseException:
   db.rollback();raise
 print(json.dumps({'processed':len(plans),'backup':str(backup),'verified_rows':len(plans),'remote_write_requests':0}))



def retag_jd_one(client,db,record,kb,tags):
 # This path never creates or updates a document body. It can only replace
 # a JD's complete tag set and change that same row's tag_ids_json.
 number=record['result_number']
 if not number.startswith('JD-') or record['target']!='public':raise ValueError('Tag-only migration is limited to public JD records')
 validate([record])
 if len(tags)!=3 or len(set(tags))!=3:raise ValueError('JD requires exactly three distinct tag ids')
 row=db.execute('SELECT * FROM result_ledger WHERE result_number=?',(number,)).fetchone()
 if row is None or row['remote_state']!='active' or row['knowledge_base_id']!=kb:raise ValueError('JD ledger state/library mismatch')
 if row['content_sha256']!=record['content_sha256']:raise ValueError('JD ledger content drift; no body updates allowed')
 ident=row['knowledge_id'];doc=client.get_knowledge(ident)['data']
 def check_body(value):
  if value['knowledge_base_id']!=kb or digest(value['metadata']['content'])!=record['content_sha256'] or value['metadata']['status']!='publish':raise ValueError('JD remote content/library drift; stopped')
 check_body(doc);wanted=set(tags);action='skip'
 if {x['id'] for x in (doc.get('tags') or [])}!=wanted:
  client.replace_tags(ident,tags,kb);action='replace-tags'
  doc=client.get_knowledge(ident)['data'];check_body(doc)
  if {x['id'] for x in (doc.get('tags') or [])}!=wanted:raise ValueError('JD tag replacement did not persist')
 # Recovery after an uncertain tags request is safe: re-read exact id; when
 # tags already match, only reconcile ledger instead of re-sending PUT.
 changed=set(json.loads(row['tag_ids_json']))!=wanted
 if changed:
  with db:
   cursor=db.execute('UPDATE result_ledger SET tag_ids_json=? WHERE result_number=? AND knowledge_id=? AND content_sha256=?',(json.dumps(tags),number,ident,record['content_sha256']))
   if cursor.rowcount!=1:raise ValueError('JD ledger changed concurrently')
 return {'result_number':number,'knowledge_id':ident,'action':action,'ledger_tags_changed':changed,'body_sha256_unchanged':True}


def retag_jd(args):
 rows=load(args.manifest);validate(rows)
 rows=[r for r in rows if r['result_number'].startswith('JD-')]
 if args.numbers:
  wanted=set(args.numbers.split(','));rows=[r for r in rows if r['result_number'] in wanted]
  if {r['result_number'] for r in rows}!=wanted:raise ValueError('Unknown JD selection')
 config=load(Path(args.root)/'coordination/weknora-tags.json');lib=config['knowledge_bases']['public']
 tags=[lib['tags'][name] for name in ['类型-决定','来源-Agent会话','隐私-内部']]
 client=client_for(args.root);counts={};evidence=Path(args.evidence)
 with (evidence/'retag-jd.lock').open('w') as lock:
  fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
  with ledger_connect(Path(args.root)/'workspace/weknora/ledger.db') as db:
   for record in rows:
    result=retag_jd_one(client,db,record,lib['knowledge_base_id'],tags)
    counts[result['action']]=counts.get(result['action'],0)+1
    with (evidence/'retag-events.jsonl').open('a') as out:out.write(json.dumps(result)+'\n')
 print(json.dumps({'processed':len(rows),'actions':counts}))


def verify(args):
 rows=load(args.manifest);validate(rows);client=client_for(args.root);config=load(Path(args.root)/'coordination/weknora-tags.json')
 if args.numbers:
  wanted=set(args.numbers.split(','));rows=[r for r in rows if r['result_number'] in wanted]
 checks=[]
 with closing(ledger_connect(Path(args.root)/'workspace/weknora/ledger.db',readonly=True)) as db:
  for record in rows:
   row=db.execute('SELECT * FROM result_ledger WHERE result_number=?',(record['result_number'],)).fetchone()
   if row is None:raise ValueError('Result absent from ledger')
   for _ in range(90 if args.wait_index else 1):
    doc=client.get_knowledge(row['knowledge_id'])['data']
    if doc['parse_status']=='completed':break
    if doc['parse_status'] in ('failed','cancelled'):raise ValueError('Remote indexing failed')
    if not args.wait_index:break
    time.sleep(.5)
   else:raise ValueError('Remote indexing timeout')
   target_id,target_tags=destination(record,config);expected=set(target_tags)
   if digest(doc['metadata']['content'])!=record['content_sha256'] or doc['metadata']['status']!='publish' or doc['knowledge_base_id']!=target_id or {x['id'] for x in doc['tags']}!=expected:raise ValueError('Remote verification mismatch')
   checks.append({'result_number':record['result_number'],'knowledge_id':doc['id'],'hash_match':True,'tags_match':True,'published':True,'parse_status':doc['parse_status']})
 write_json(Path(args.evidence)/('verify-'+('selected' if args.numbers else 'all')+'.json'),checks);print(json.dumps({'verified':len(checks),'hash_and_tags_match':True,'completed':sum(x['parse_status']=='completed' for x in checks),'background_pending':sum(x['parse_status']!='completed' for x in checks)}))


def main():
 p=argparse.ArgumentParser();p.add_argument('command',choices=['prepare','provision','push','verify','retag-jd','relocate-private']);p.add_argument('--root',default=str(ROOT));p.add_argument('--evidence',required=True);p.add_argument('--manifest');p.add_argument('--deals');p.add_argument('--decisions');p.add_argument('--sources');p.add_argument('--numbers');p.add_argument('--wait-index',action='store_true',help='Wait for all model processing; omit for approved overnight question generation')
 p.add_argument('--dry-run',action='store_true',help='Plan push/relocation without remote or ledger writes')
 args=p.parse_args()
 if args.command not in ('provision','relocate-private') and not args.manifest:p.error('--manifest is required for this command')
 if args.dry_run and args.command not in ('push','relocate-private'):p.error('--dry-run supports push and relocate-private only')
 if args.command=='relocate-private' and args.numbers:p.error('relocate-private processes the entire legacy batch; --numbers is not supported')
 globals()[args.command.replace('-','_')](args)
if __name__=='__main__':main()
