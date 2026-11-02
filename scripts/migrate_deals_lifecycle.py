#!/usr/bin/env python3
"""Explicit, backed-up owner-list migration; never run on production without its window."""
import argparse,hashlib,json,re,shutil,sqlite3,sys
from datetime import datetime,timezone
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from deals_store import Deals

CONTACT_MANIFEST=Path(__file__).with_name('deals-lifecycle-contact-20261101.json')

def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def backup(workspace,destination):
    destination=Path(destination)
    if destination.exists():raise ValueError('备份目录已存在，拒绝覆盖')
    source=Path(workspace)/'deals';destination.mkdir(parents=True)
    for path in source.iterdir():
        if path.is_file() and path.suffix=='.db':
            with sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True) as src,sqlite3.connect(destination/path.name) as dst:src.backup(dst)
        elif path.is_dir():shutil.copytree(path,destination/path.name)
        elif path.is_file() and not path.name.endswith(('-wal','-shm','-journal')):shutil.copy2(path,destination/path.name)
    hashes={str(p.relative_to(destination)):sha(p) for p in destination.rglob('*') if p.is_file()}
    (destination/'sha256.json').write_text(json.dumps(hashes,indent=2))
    return hashes

def readonly_deals(workspace):
    with sqlite3.connect((Path(workspace)/'deals/deals.db').resolve().as_uri()+'?mode=ro',uri=True) as c:return [json.loads(r[0]) for r in c.execute('SELECT data FROM deals')]

def reconciliation(owner_path,deals):
    headings={'已付','未付','待写','完成初稿','做资料','在谈','建联'};group='';rows=[];matched=set()
    # These are name equivalences only. No group, amount or database row is changed.
    aliases={'MailAgent  之前':'MailAgent','即梦 300美金 临时单':'即梦 · 临时单','百度2单  7000元':'百度 · 2单','字节1单':'字节 · 1单','MiniMax M3.1':'MiniMax M3.1新模型','即梦        创作最后 1单  总共1100美金  即梦视频创作':'即梦 · 创作最后1单','OKX+卡台刚才这个  1+1单':'OKX + 卡台'}
    norm=lambda v:re.sub(r'[\s·+：:，,]','',v).casefold()
    for number,line in enumerate(Path(owner_path).read_text().splitlines(),1):
        raw=line.strip()
        if not raw or raw.startswith('#'):continue
        if raw in headings:group=raw;continue
        if raw.startswith('http') or raw=='DD：':
            rows.append(dict(line=number,text=raw,group=group,status='来源链接/联系人标记',matches=[]));continue
        compact=re.sub(r'\s+',' ',raw)
        key=re.split(r'\t| {2,}|\s+(?=\d)|(?=\d+单)|签了协议',raw)[0].strip()
        alias=next((v for k,v in aliases.items() if raw==k or raw.startswith(k)),None)
        targets=[d for d in deals if norm(d['title'])==norm(alias or key)]
        if not targets and len(key)>2:targets=[d for d in deals if norm(d['title'])==norm(key.replace('中转',''))]
        # Evidence of an ambiguous repeated brand is reported, never auto-matched.
        if not targets:status='清单有，库里没有（或名称待核对）'
        elif len(targets)>1:status='同名多笔，待核对'
        else:
            d=targets[0];matched.add(d['id']);differences=[]
            if d.get('original_group')!=group:differences.append('分组不一致')
            amounts=re.findall(r'\d+(?:\.\d+)?',raw)
            if amounts:
                if d.get('quote') is None:differences.append('金额库中未知，手记有数值')
                elif float(d['quote']) not in [float(n) for n in amounts]:differences.append('金额不一致')
            status='；'.join(differences) or '对应（金额/币种仍按原文核对）'
        rows.append(dict(line=number,text=raw,group=group,status=status,matches=[dict(id=d['id'],title=d['title'],group=d['original_group'],quote=d['quote'],currency=d['currency']) for d in targets]))
    extras=[dict(id=d['id'],title=d['title'],group=d['original_group'],quote=d['quote'],currency=d['currency']) for d in deals if d['id'] not in matched]
    return dict(rows=rows,database_only=extras,notice='只作对账，不新建、删除、修改任何商单；模糊名不能视为确认匹配')

def reconciliation_markdown(data):
    esc=lambda v:str(v or '').replace('|','\\|').replace('\n',' ')
    lines=['# 黄总手记清单逐行对账','','只出差异，不自动新增、删除或改分组/金额。','','| 行 | 清单原文 | 分组 | 库中对应 | 结论 |','|---|---|---|---|---|']
    for r in data['rows']:
        matches='；'.join(f"{d['title']} / {d['group']} / {d['quote']} {d['currency']}" for d in r['matches'])
        lines.append(f"| {r['line']} | {esc(r['text'])} | {r['group']} | {esc(matches)} | {r['status']} |")
    lines+=['','## 库里有、清单未精确对应','','| 商单 | 原分组 | 金额 | ID |','|---|---|---|---|']
    for d in data['database_only']:lines.append(f"| {esc(d['title'])} | {esc(d['group'])} | {d['quote']} {d['currency']} | {d['id']} |")
    return '\n'.join(lines)+'\n'

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--workspace',required=True);parser.add_argument('--output',required=True);parser.add_argument('--owner-list',default='coordination/deals-owner-list-2026-11-01.md');parser.add_argument('--apply',action='store_true');parser.add_argument('--production-approved',action='store_true');args=parser.parse_args()
    workspace=Path(args.workspace).resolve();output=Path(args.output).resolve()
    if output.exists():raise ValueError('输出目录已存在，拒绝覆盖')
    if args.apply and workspace==Path(__file__).resolve().parents[1]/'workspace' and not args.production_approved:raise ValueError('生产写入必须先获批窗口，再显式传 production-approved')
    output.mkdir(parents=True);data=reconciliation(args.owner_list,readonly_deals(workspace));(output/'reconciliation.json').write_text(json.dumps(data,ensure_ascii=False,indent=2));(output/'reconciliation.md').write_text(reconciliation_markdown(data))
    if not args.apply:print('只读对账完成 '+str(output));return
    hashes=backup(workspace,output/'backup')
    store=SimpleNamespace(workspace=workspace);store.deals=Deals(store)
    first=store.deals.migrate_owner_paid();first_hash=sha(store.deals.path);second=store.deals.migrate_owner_paid();second_hash=sha(store.deals.path)
    if second or first_hash!=second_hash:raise AssertionError('迁移重复执行不幂等')
    candidates=[]
    for d in store.deals.listing()['deals']:
        # These two orders have separately reviewed contact context below. Do not
        # mix the old 3200 order and new 4000 order through generic brand rules.
        if d['title'] in ('AI for Math','大模型评测'):continue
        r=store.deals.generate_lifecycle_proposals(d['id']);candidates.append(dict(id=d['id'],title=d['title'],original_group=d['original_group'],**r))
    bundle=json.loads(CONTACT_MANIFEST.read_text());contact=store.deals.migrate_owner_contact_bundle(bundle)
    full_hash=sha(store.deals.path);contact_second=store.deals.migrate_owner_contact_bundle(bundle);full_second_hash=sha(store.deals.path)
    if contact_second['created'] or contact_second['proposals_added'] or contact_second['evidence_added'] or full_hash!=full_second_hash:raise AssertionError('联系人迁移重复执行不幂等')
    (output/'migration-diff.json').write_text(json.dumps(first,ensure_ascii=False,indent=2));(output/'candidates.json').write_text(json.dumps(candidates,ensure_ascii=False,indent=2));(output/'contact-migration.json').write_text(json.dumps(contact,ensure_ascii=False,indent=2))
    result=dict(changed=len(first),second_changes=second,idempotent_sha=first_hash,backup=hashes,model_calls=0,new_deals=int(contact['created']),contact_proposals=contact['proposals_added'],contact_second=contact_second,full_idempotent_sha=full_hash,manifest_sha=sha(CONTACT_MANIFEST))
    (output/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
    print(json.dumps(dict(changed=len(first),second_changes=len(second),same_sha=first_hash==second_hash,new_deals=result['new_deals'],contact_proposals=result['contact_proposals'],candidates=sum(r['created'] for r in candidates)+contact['proposals_added'],output=str(output)),ensure_ascii=False))
if __name__=='__main__':main()
