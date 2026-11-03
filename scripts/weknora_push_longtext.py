#!/usr/bin/env python3
"""作品/外部参考长文 → 大后方；默认dry-run，源库严格只读。

不创建/迁移共享账本，不碰其他编号，不自动重建丢失远端条目。
"""
from __future__ import annotations
import argparse
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime,timezone
import fcntl
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import sys
import time

KB='82b87a9e-2154-4ac4-8615-55bb26e2a9dd'
SOURCES={'articles':('CW','长文·作品','本机知识库·我的文案与作品'),
         'external-references':('WC','长文·外部参考','本机知识库·外部文章收藏')}
MARKER='\n\n---\n\n'
COLUMNS=('result_number','knowledge_base_id','knowledge_id','content_sha256','tag_ids_json','last_pushed_at','remote_state')

class SafetyStop(RuntimeError):pass

def sha(value):return hashlib.sha256(value.encode('utf-8')).hexdigest()
def now():return datetime.now(timezone.utc).isoformat()
def line(value):return ' '.join(str(value or '').splitlines()).strip()

@dataclass(frozen=True)
class Document:
    number:str
    source_id:str
    source:str
    title:str
    body:str
    content:str
    content_sha256:str
    tag_ids:tuple[str,...]


def render(row,tag_id):
    prefix,kind,source=SOURCES[row['source']]
    number=prefix+'-'+re.sub(r'[^A-Za-z0-9_-]','-',row['id'])
    title=line(row['title']) or '无标题'
    body=row['body']
    first=next((p.strip() for p in re.split(r'\n\s*\n',body) if p.strip()),'')
    summary=line(first)[:160]
    location='knowledge.db documents.id='+line(row['id'])
    if row['url']:location+='；'+line(row['url'])
    heading=f'【{kind}】{number} {title}'
    content='\n'.join(['# '+heading,'- 编号：'+number,'- 类型：'+kind,'- 摘要：'+summary,
                       '- 隐私：公开','- 来源：'+source,'- 原文位置：'+location,
                       '- 时间：'+line(row['modified']),'- 涉及：无','- 关联：无','- 更新：'+line(row['modified'])])+MARKER+body
    if len(content)>200000:raise SafetyStop('longtext_exceeds_manual_limit')
    return Document(number,row['id'],row['source'],heading[:250],body,content,sha(content),(tag_id,))


def read_documents(source_db,tags_path):
    tags=json.loads(Path(tags_path).read_text())
    if tags.get('knowledge_base_id')!=KB:raise SafetyStop('tag_knowledge_base_mismatch')
    tag_id=tags['tags']['隐私-公开']
    if not isinstance(tag_id,str) or not tag_id:raise SafetyStop('privacy_tag_missing')
    docs=[];empty=[];numbers=set()
    with closing(sqlite3.connect(Path(source_db).resolve().as_uri()+'?mode=ro',uri=True)) as db:
        db.row_factory=sqlite3.Row;db.execute('PRAGMA query_only=ON');db.execute('BEGIN')
        for row in db.execute("SELECT id,source,title,body,url,modified FROM documents WHERE source IN ('articles','external-references') ORDER BY source,id"):
            doc=render(row,tag_id)
            if doc.number in numbers:raise SafetyStop('result_number_collision')
            numbers.add(doc.number)
            if not doc.body.strip():
                empty.append({'number':doc.number,'title':row['title'],'source':doc.source});continue
            docs.append(doc)
    return docs,empty


class Ledger:
    def __init__(self,path):
        self.path=Path(path).resolve()
        # mode=rw is intentional: absence/schema mismatch must not make a new DB.
        with self.connect() as db:
            columns={r[1] for r in db.execute('PRAGMA table_info(result_ledger)')}
            if not set(COLUMNS)<=columns:raise SafetyStop('ledger_schema_mismatch')
    def connect(self):
        return closing(sqlite3.connect(self.path.as_uri()+'?mode=rw',uri=True,timeout=30))
    def get(self,number):
        self.owned(number)
        with self.connect() as db:
            db.row_factory=sqlite3.Row;row=db.execute('SELECT * FROM result_ledger WHERE result_number=?',(number,)).fetchone()
            return dict(row) if row else None
    @staticmethod
    def owned(number):
        if not re.fullmatch(r'(?:CW|WC)-[A-Za-z0-9_-]+',number):raise SafetyStop('ledger_prefix_not_owned')
    def put(self,doc,kid,*,digest=None,tags=None,state='active',only_new=False):
        self.owned(doc.number)
        with self.connect() as db,db:
            db.execute('BEGIN IMMEDIATE')
            values=(doc.number,KB,kid,digest if digest is not None else doc.content_sha256,json.dumps(list(doc.tag_ids) if tags is None else tags),now(),state)
            if only_new:db.execute('INSERT INTO result_ledger('+','.join(COLUMNS)+') VALUES(?,?,?,?,?,?,?)',values)
            else:
                db.execute('''INSERT INTO result_ledger(result_number,knowledge_base_id,knowledge_id,content_sha256,tag_ids_json,last_pushed_at,remote_state) VALUES(?,?,?,?,?,?,?) ON CONFLICT(result_number) DO UPDATE SET
                 knowledge_base_id=excluded.knowledge_base_id,knowledge_id=excluded.knowledge_id,
                 content_sha256=excluded.content_sha256,tag_ids_json=excluded.tag_ids_json,
                 last_pushed_at=excluded.last_pushed_at,remote_state=excluded.remote_state''',values)


def metadata(remote):
    result=remote.get('metadata') or {}
    return json.loads(result) if isinstance(result,str) else result


def remote_doc(client,kid):
    remote=client.get_knowledge(kid)['data']
    if remote.get('knowledge_base_id')!=KB:raise SafetyStop('remote_knowledge_base_mismatch')
    return remote


def push_one(client,ledger,doc):
    if client.config.api_key in doc.content:raise SafetyStop('credential_in_source_content')
    old=ledger.get(doc.number)
    if old and old['knowledge_base_id']!=KB:raise SafetyStop('ledger_knowledge_base_mismatch')
    if old and old['remote_state'] not in ('active','tags_pending'):
        raise SafetyStop('ledger_row_requires_manual_recovery')
    tags=list(doc.tag_ids)
    if old:
        kid=old['knowledge_id'];old_tags=json.loads(old['tag_ids_json'])
        if old['content_sha256']==doc.content_sha256 and old_tags==tags and old['remote_state']=='active':return 'skipped'
        try:remote_doc(client,kid)
        except Exception as error:
            if getattr(error,'status',None)==404:raise SafetyStop('remote_entry_missing') from None
            raise
        if old['content_sha256']!=doc.content_sha256:
            client.update_manual(kid,doc.title,doc.content)
            ledger.put(doc,kid,tags=old_tags,state='tags_pending' if old_tags!=tags else 'active')
        if old_tags!=tags or old['remote_state']=='tags_pending':
            client.replace_tags(kid,tags,KB)
        ledger.put(doc,kid)
        return 'updated'
    # Reserve this prefix before POST. If the remote accepts but local commit or
    # connection fails, future runs stop on creating instead of duplicating it.
    ledger.put(doc,'pending:'+doc.number,state='creating',only_new=True)
    created=client.create_manual(doc.title,doc.content,tags,KB)['data']
    kid=created.get('id')
    if not isinstance(kid,str) or not kid:raise SafetyStop('create_reply_missing_identifier')
    ledger.put(doc,kid)
    return 'created'


def push_all(client,ledger,docs,interval=1.0,emit=lambda value:None):
    summary={'created':0,'updated':0,'skipped':0,'failed':[],'stopped':False,'started_at':now()}
    for index,doc in enumerate(docs):
        if index:time.sleep(interval)
        try:
            action=push_one(client,ledger,doc);summary[action]+=1
            emit({'number':doc.number,'action':action,'at':now()})
        except Exception as error:
            row={'number':doc.number,'error_type':type(error).__name__,'status':getattr(error,'status',None)}
            if isinstance(error,SafetyStop):row['reason']=str(error)
            summary['failed'].append(row);emit(row)
            if isinstance(error,SafetyStop) or getattr(error,'status',None)==404:summary['stopped']=True;break
    summary['finished_at']=now()
    return summary


def preflight(client,docs):
    data=client._request('GET','/knowledge-bases/'+KB)['data']
    strategy=data.get('indexing_strategy') or {}
    if strategy.get('wiki_enabled') is not False or strategy.get('graph_enabled') is not False or (data.get('extract_config') or {}).get('enabled') is not False:
        raise SafetyStop('knowledge_base_settings_not_approved')
    tag_rows=client.list_tags(KB)['data']
    if isinstance(tag_rows,dict):tag_rows=tag_rows.get('data',tag_rows.get('items',[]))
    valid={r['id']:r['name'] for r in tag_rows}
    if any(valid.get(tag)!='隐私-公开' for doc in docs for tag in doc.tag_ids):raise SafetyStop('privacy_tag_not_verified')
    if any(client.config.api_key in doc.content for doc in docs):raise SafetyStop('credential_in_source_content')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project-root',type=Path,default=Path(__file__).resolve().parents[1])
    parser.add_argument('--push',action='store_true')
    parser.add_argument('--numbers',nargs='+')
    parser.add_argument('--interval',type=float,default=1.0)
    parser.add_argument('--summary',type=Path)
    args=parser.parse_args();root=args.project_root.resolve()
    if args.interval<0.2:parser.error('间隔至少0.2秒')
    docs,empty=read_documents(root/'workspace/knowledge/knowledge.db',root/'coordination/weknora-tags.json')
    if args.numbers:
        selected=set(args.numbers)
        if not selected<={d.number for d in docs}:raise SafetyStop('unknown_or_empty_result_number')
        docs=[d for d in docs if d.number in selected]
    sys.path.insert(0,str(root));from weknora_client import WeKnoraClient
    client=WeKnoraClient.from_env_file(root/'.env.local',timeout=60)
    if any(client.config.api_key in d.content for d in docs) or any(client.config.api_key in e['title'] for e in empty):raise SafetyStop('credential_in_source_content')
    ledger=Ledger(root/'workspace/weknora/ledger.db')
    plan={'dry_run':not args.push,'count':len(docs),'body_characters':sum(len(d.body) for d in docs),
          'markdown_characters':sum(len(d.content) for d in docs),'by_source':{s:sum(d.source==s for d in docs) for s in SOURCES},
          'sample_numbers':[d.number for d in docs[:5]],'empty_skipped':empty,
          'already_identical':sum(bool((r:=ledger.get(d.number)) and r['content_sha256']==d.content_sha256 and r['remote_state']=='active' and json.loads(r['tag_ids_json'])==list(d.tag_ids)) for d in docs)}
    print(json.dumps(plan,ensure_ascii=False),flush=True)
    result=plan
    if args.push:
        preflight(client,docs)
        lock=Path('/tmp')/('weknora-longtext-'+sha(str(root))[:16]+'.lock')
        with lock.open('a') as handle:
            fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
            result=push_all(client,ledger,docs,args.interval,lambda row:print(json.dumps(row,ensure_ascii=False),flush=True))
    if args.summary:
        args.summary.parent.mkdir(parents=True,exist_ok=True);args.summary.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    if args.push and (result['failed'] or result['stopped']):return 1
    return 0

if __name__=='__main__':
    try:exit_code=main()
    except Exception as error:
        print(json.dumps({'error_type':type(error).__name__,'status':getattr(error,'status',None)},ensure_ascii=False),flush=True)
        exit_code=1
    sys.exit(exit_code)
