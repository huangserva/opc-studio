#!/usr/bin/env python3
"""只读旧库 -> 人工收藏素材库；--dry-run 在临时库计算相同对账，不修改 workspace。"""
from __future__ import annotations
import argparse
import hashlib
import json
import sys
import tempfile
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from items_store import ItemsStore, readonly, from_document, decoded, canonical_id, workspace

SOURCES=('external-references','articles','manual','remio')


def migrate(root, dry_run=False):
    root=workspace(root)
    temp=tempfile.TemporaryDirectory(prefix='items-dry-',dir='/tmp') if dry_run else None
    store=ItemsStore(Path(temp.name) if temp else root)
    counters={}
    missing_documents=[]
    connections=[]
    def connect(path):
        if not path.exists():return None
        c=readonly(path);connections.append(c);return c
    kb=connect(root/'knowledge/knowledge.db')
    intake=connect(root/'inspiration/intake.db')
    cards=connect(root/'inspiration/cards.db')
    docs={};mapping={}
    def document(identifier):
        if identifier not in docs:
            row=kb.execute('SELECT * FROM documents WHERE id=?',(identifier,)).fetchone() if kb else None
            docs[identifier]=dict(row) if row else None
        return docs[identifier]
    def put(source,item):
        count=counters.setdefault(source,dict(old=0,new=0,merged=0,skipped=0,reasons={}))
        count['old']+=1
        identifier=canonical_id(item)
        try:store.detail(identifier);exists=True
        except ValueError:exists=False
        result=store.upsert(item)
        count['merged' if exists else 'new']+=1
        return result['id']
    def ensure_doc(identifier):
        if not identifier:return ''
        if identifier in mapping:return mapping[identifier]
        doc=document(identifier)
        if not doc:return ''
        mapping[identifier]=put('card-reference-documents',from_document(doc))
        return mapping[identifier]
    try:
        if kb:
            for source in SOURCES:
                counters.setdefault(source,dict(old=0,new=0,merged=0,skipped=0,reasons={}))
                for row in kb.execute('SELECT d.*,t.deleted_at FROM documents d LEFT JOIN document_trash t ON t.document_id=d.id WHERE d.source=? ORDER BY d.id',(source,)):
                    doc=dict(row);doc['trashed']=bool(doc.get('deleted_at'));docs[doc['id']]=doc
                    mapping[doc['id']]=put(source,from_document(doc))
        if intake:
            for row in intake.execute('SELECT * FROM drops ORDER BY id'):
                drop=dict(row);doc=document(drop.get('document_id',''))
                item=from_document(doc) if doc else dict(legacy_id=drop['id'],title=drop['title'],full_text=drop['text'],url=drop['url'],legacy_refs=[],sources=[],kind=drop['kind'])
                item['legacy_refs'].append({'store':'intake','id':drop['id']})
                item['sources'].append('intake')
                item.update(note=drop['reason'],created_at=drop['received_at'],updated_at=drop['received_at'])
                if not item['url']:item['url']=drop['url']
                if not item['title']:item['title']=drop['title'] or drop['text'][:120] or drop['url']
                if len(drop['text'])>len(item['full_text']):item['full_text']=drop['text']
                meta=decoded(drop['file_meta'],{})
                item['attachments']=[{'path':drop['file_path'],'meta':meta}] if drop['file_path'] else []
                recognition=decoded(drop.get('recognition'),{})
                item['analysis']={'intake_meta':decoded(drop['meta'],{}),'recognition':recognition}
                ident=put('intake',item)
                if drop.get('document_id'):mapping[drop['document_id']]=ident
        if cards:
            for row in cards.execute('SELECT * FROM cards ORDER BY id'):
                card=dict(row);refs=decoded(card['refs'],[])
                parent=ensure_doc(card.get('document_id',''))
                for ref in refs:
                    if isinstance(ref,dict):ensure_doc(ref.get('document_id',''))
                if not parent:
                    missing_id=card['document_id']
                    fragments=[r.get('text','') for r in refs if isinstance(r,dict) and r.get('document_id')==missing_id]
                    parent=put('missing-card-parents',dict(legacy_id=missing_id,kind='article',title='原资料已缺失，仅保留引用片段',full_text='原资料已缺失，仅保留引用片段\n\n'+'\n\n'.join(fragments),content_complete=False,incomplete_reason='旧知识库已不存在原文；仅保留卡片的历史引用片段',legacy_refs=[{'store':'cards','id':card['id']}],sources=['cards'],source='cards',analysis={'missing_document_id':missing_id,'card_id':card['id']},created_at=card['created_at'],updated_at=card['updated_at']))
                    mapping[missing_id]=parent
                    missing_documents.append({'card_id':card['id'],'missing_document_id':missing_id,'parent_id':parent})
                put('cards',dict(kind='card',prompt_sha=card['prompt_sha'],title=card['scene'] or card['prompt'][:120],full_text=card['prompt'],note=card['note'],parent_id=parent,sources=['cards'],source='cards',legacy_refs=[{'store':'cards','id':card['id']}],analysis={'refs':refs,'document_id':card['document_id'],'drop_id':card['drop_id'],'model':card['model'],'legacy_status':card['status'],'reason':card['reason']},created_at=card['created_at'],updated_at=card['updated_at']))
        feedback=root/'today/feedback.json'
        if feedback.exists():
            from today_view import snapshot
            records=json.loads(feedback.read_text())
            available={i['id']:i for i in snapshot(root,author_mode=True)[0]}
            for identifier,record in records.items():
                if record.get('status') not in ('kept','used'):continue
                if identifier in available:
                    item=available[identifier];item.update(status=record['status'],feedback=record.get('feedback'),why_viral=item.get('why',''),updated_at=record.get('updated_at') or item.get('published_at'))
                    put('today',item)
                else:
                    stats=counters.setdefault('today',dict(old=0,new=0,merged=0,skipped=0,reasons={}))
                    stats['old']+=1;stats['skipped']+=1;stats['reasons']['原帖已不在现有快照']=stats['reasons'].get('原帖已不在现有快照',0)+1
        with store.db() as c:
            rows=[dict(r) for r in c.execute('SELECT * FROM items ORDER BY id')]
        hashes={r['id']:hashlib.sha256(json.dumps(r,ensure_ascii=False,sort_keys=True).encode()).hexdigest() for r in rows}
        return dict(dry_run=dry_run,counts=counters,missing_documents=missing_documents,total=len(rows),content_hash=hashlib.sha256(json.dumps(hashes,sort_keys=True).encode()).hexdigest(),item_hashes=hashes)
    finally:
        for c in connections:c.close()
        if temp:temp.cleanup()

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--workspace',required=True);parser.add_argument('--dry-run',action='store_true');args=parser.parse_args()
    print(json.dumps(migrate(args.workspace,args.dry_run),ensure_ascii=False,indent=2))
