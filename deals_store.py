"""Local deal ledger: independently versioned fulfillment, cash receipts and publications."""
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import date, datetime, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo
from urllib.parse import urlencode

STAGES = {'contact':'初步接洽','negotiating':'洽谈报价','signed':'合作敲定','writing':'待写','in_progress':'撰写中','draft':'初稿完成','review':'审核 / 修改','ready':'待发布','published':'已发布','payment_pending':'待结账','settled':'已结清','unknown':'进度待核对','closed':'已结案','cancelled':'未合作 / 已取消'}
# Keep the existing writing key as 待写; old records must not become 撰写中.
STAGE_GROUPS = {'洽谈合作':('contact','negotiating','signed'),'创作交付':('writing','in_progress','draft','review'),'发布结账':('ready','published','payment_pending','settled'),'其他进度':('unknown','closed','cancelled')}
DELIVERY_STAGES = {'pending':'待写','draft':'初稿完成','review':'客户审核','approved':'已确认','published':'已发布'}
CURRENCIES = ('CNY','USD')

def payment_stage_conflict(changes, message):
    waiting=bool(re.search(r'(?:等待|等对方|待)(?:.{0,6})(?:打款|付款|收款)',message or ''))
    return waiting and (('stage' in changes and changes['stage']!='payment_pending' and not re.search(r'初稿|写完|撰写|交稿|交付|签约|结案|发布|审核|审稿|稿件',message or '',re.I)) or ('deadline' in changes and not re.search(r'交稿|交付|截稿|稿件|履约截止',message or '')))

class DealConflict(ValueError):
    pass

def now():
    return datetime.now(timezone.utc).isoformat()

def text(v, name, limit=4000, required=False):
    if not isinstance(v,str) or '\x00' in v or len(v)>limit:
        raise ValueError(name+'格式无效')
    v=v.strip()
    if required and not v:raise ValueError('请填写'+name)
    return v

def day(v, name):
    if v=='':return ''
    if not isinstance(v,str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}',v):raise ValueError(name+'请使用实际日期')
    try:date.fromisoformat(v)
    except ValueError:raise ValueError(name+'无效') from None
    return v

def amount(v, required=False):
    if v in (None,'') and not isinstance(v,bool):
        if required:raise ValueError('请填写金额')
        return None
    if isinstance(v,bool) or not isinstance(v,(str,int)) or not re.fullmatch(r'\d{1,12}(?:\.\d{1,2})?',str(v)):
        raise ValueError('金额需为非负数，最多两位小数')
    return format(Decimal(str(v)),'.2f')

def currency(v, required=False):
    if v in (None,'') and not required:return None
    if v not in CURRENCIES:raise ValueError('请选择人民币或美元')
    return v

def boolean(v,name):
    if type(v) is not bool:raise ValueError(name+'需为开关')
    return v

def fields(p, allowed):
    if not isinstance(p,dict) or set(p)-set(allowed):raise ValueError('请求包含无效字段')

def identity(v):
    if not isinstance(v,str) or not re.fullmatch(r'[a-f0-9]{32}',v):raise ValueError('无效记录标识')
    return v

class Deals:
    def __init__(self,store):
        self.store=store;self.lock=threading.RLock();self.root=store.workspace/'deals'
        self.root.mkdir(parents=True,exist_ok=True);self.root.chmod(0o700);self.path=self.root/'deals.db'
        with self.db() as c:
            c.executescript('''CREATE TABLE IF NOT EXISTS deals(id TEXT PRIMARY KEY,revision INTEGER NOT NULL,data TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS audit(deal_id TEXT,revision INTEGER,at TEXT,action TEXT,data TEXT,PRIMARY KEY(deal_id,revision));
              CREATE TABLE IF NOT EXISTS requests(key TEXT PRIMARY KEY,digest TEXT,result TEXT);
              CREATE TABLE IF NOT EXISTS sources(clue_id TEXT PRIMARY KEY,deal_id TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS posts(url TEXT PRIMARY KEY,deal_id TEXT NOT NULL,publication_id TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS imports(key TEXT PRIMARY KEY,data TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS evidence(deal_id TEXT,id TEXT,data TEXT,PRIMARY KEY(deal_id,id));
              CREATE TABLE IF NOT EXISTS evidence_scans(deal_id TEXT PRIMARY KEY,data TEXT);
              CREATE TABLE IF NOT EXISTS proposals(id TEXT PRIMARY KEY,deal_id TEXT,data TEXT,result TEXT);''')
        self.path.chmod(0o600)
    @contextmanager
    def db(self):
        c=sqlite3.connect(self.path,timeout=15);c.row_factory=sqlite3.Row
        try:
            with c:yield c
        finally:c.close()
    def _read(self,c,ident):
        row=c.execute('SELECT data FROM deals WHERE id=?',(identity(ident),)).fetchone()
        if not row:raise ValueError('商单不存在')
        return json.loads(row[0])
    def _write(self,c,d,action):
        d['revision']+=1;d['updated_at']=now()
        value=json.dumps(d,ensure_ascii=False)
        c.execute('INSERT OR REPLACE INTO deals VALUES(?,?,?)',(d['id'],d['revision'],value))
        c.execute('INSERT INTO audit VALUES(?,?,?,?,?)',(d['id'],d['revision'],d['updated_at'],action,value))
        return d
    def _edit(self,c,ident,p):
        d=self._read(c,ident)
        if type(p.get('revision')) is not int or p['revision']!=d['revision']:raise DealConflict('商单已有更新，请刷新后重新操作')
        return d
    def _once(self,c,p,scope):
        key=text(p.get('request_key',''),'操作标识',100,True)
        if not re.fullmatch(r'[A-Za-z0-9_-]{8,100}',key):raise ValueError('操作标识无效')
        digest=hashlib.sha256(json.dumps([scope,{k:v for k,v in p.items() if k!='revision'}],sort_keys=True).encode()).hexdigest()
        row=c.execute('SELECT digest,result FROM requests WHERE key=?',(key,)).fetchone()
        if row:
            if row[0]!=digest:raise DealConflict('同一操作标识的内容已变化，请重新提交')
            return key,digest,json.loads(row[1])
        return key,digest,None
    def _remember(self,c,key,digest,d):
        c.execute('INSERT INTO requests VALUES(?,?,?)',(key,digest,json.dumps({'id':d['id']},ensure_ascii=False)))
    def _clean(self,p):
        out={}
        for k in ('title','customer','contact','agreement','notes','price_note'):
            if k in p:out[k]=text(p[k],k,12000 if k=='notes' else 2000,k in ('title','customer'))
        if 'stage' in p:
            if not isinstance(p['stage'],str) or p['stage'] not in STAGES:raise ValueError('商单进度无效')
            out['stage']=p['stage']
        for k in ('signed_on','completed_on','deadline'):
            if k in p:out[k]=day(p[k],k)
        for k in ('quote_confirmed','settled','needs_review','archived'):
            if k in p:out[k]=boolean(p[k],k)
        if 'quote' in p:out['quote']=amount(p['quote'])
        if 'currency' in p:out['currency']=currency(p['currency'])
        if 'expected_deliverables' in p:
            v=p['expected_deliverables']
            if v is not None and (type(v) is not int or not 1<=v<=1000):raise ValueError('交付数量需为1–1000或未知')
            out['expected_deliverables']=v
        return out
    def _valid(self,d):
        if d['quote_confirmed'] and (d['quote'] is None or not d['currency']):raise ValueError('确认报价需填写金额及币种')
        if d['quote'] is not None and not d['currency']:raise ValueError('填写报价时需确认币种；不确定的原话请保留在报价说明')
        if d['signed_on'] and d['completed_on'] and d['completed_on']<d['signed_on']:raise ValueError('结案日期不能早于签约日期')
        if d['stage']=='settled' and self.cash(d)['status']!='settled':raise ValueError('标记已结清前，请确认全部到账，或补齐实际到账明细；不会自动补造收入')
    def _new(self,p):
        d=dict(id=uuid.uuid4().hex,revision=0,created_at=now(),updated_at=now(),title='',customer='',contact='',agreement='',notes='',price_note='',
            stage='contact',quote=None,currency=None,quote_confirmed=False,settled=False,needs_review=True,archived=False,
            signed_on='',completed_on='',deadline='',expected_deliverables=None,original_group='',original_text='',reported_payment='unknown',
            deliverables=[],receipts=[],publications=[],sources=[])
        d.update(self._clean(p));self._valid(d)
        if not d['title'] or not d['customer']:raise ValueError('请填写商单名称与客户')
        return d
    def save(self,p):
        allowed=('id','revision','request_key','title','customer','contact','agreement','notes','price_note','stage','quote','currency','quote_confirmed','settled','needs_review','archived','signed_on','completed_on','deadline','expected_deliverables')
        fields(p,allowed)
        with self.lock,self.db() as c:
            c.execute('BEGIN IMMEDIATE')
            key,digest,old=self._once(c,p,'save')
            if old:return self.detail(old['id'])
            if p.get('id'):
                d=self._edit(c,p['id'],p);d.update(self._clean(p));self._valid(d)
            else:d=self._new(p)
            self._write(c,d,'修改商单' if p.get('id') else '新建商单');self._remember(c,key,digest,d)
        return self.detail(d['id'])
    def cash(self,d):
        totals={k:Decimal(0) for k in CURRENCIES}
        for r in d['receipts']:
            if not r.get('voided_at'):totals[r['currency']]+=Decimal(r['amount'])
        any_cash=any(totals.values());mixed=bool(d['currency'] and any(v for k,v in totals.items() if k!=d['currency']))
        outstanding=None
        if d['quote_confirmed'] and not mixed:outstanding=max(Decimal(0),Decimal(d['quote'])-totals[d['currency']])
        if d['settled']:status='settled';outstanding=Decimal(0) if d['quote_confirmed'] else None
        elif outstanding==0 and not mixed and d['quote_confirmed']:status='settled'
        elif any_cash:status='partial'
        elif d['reported_payment']=='client_paid':status='reported_paid';outstanding=None
        else:status='unpaid'
        return dict(status=status,totals={k:format(v,'.2f') for k,v in totals.items()},outstanding=format(outstanding,'.2f') if outstanding is not None else None,mixed_currency=mixed)
    def detail(self,ident):
        with self.lock,self.db() as c:
            d=self._read(c,ident);d['audit']=[dict(revision=r['revision'],at=r['at'],action=r['action']) for r in c.execute('SELECT revision,at,action FROM audit WHERE deal_id=? ORDER BY revision DESC LIMIT 100',(ident,))]
            d['evidence']=[json.loads(r[0]) for r in c.execute('SELECT data FROM evidence WHERE deal_id=?',(ident,))]
            scan=c.execute('SELECT data FROM evidence_scans WHERE deal_id=?',(ident,)).fetchone()
            d['evidence_scan']=json.loads(scan[0]) if scan else None
        d['cash']=self.cash(d)
        for p in d['publications']:
            p['feedback']=None
            if p.get('delivery_id') and not p.get('voided_at'):
                try:p['feedback']=next(iter(self.store.feedback.listing(slug=p['slug'],delivery_id=p['delivery_id'],limit=1)['snapshots']),None)
                except (OSError,ValueError,KeyError):p['feedback_unavailable']=True
        from deals_evidence import timeline
        d['timeline']=timeline(d)
        return d

    def scan_evidence(self,ident,query=''):
        from deals_evidence import scan
        query=text(query,'品牌关键词',500)
        with self.lock,self.db() as c:d=self._read(c,ident)
        result=scan(self.store.knowledge,d,query); candidates=result.pop('candidates')
        result.update(checked_at=now(),saved_candidates=len(candidates))
        with self.lock,self.db() as c:
            c.execute('BEGIN IMMEDIATE');self._read(c,ident)
            for item in candidates:
                # A repeated scan never changes a human decision on the same snapshot.
                c.execute('INSERT OR IGNORE INTO evidence VALUES(?,?,?)',(ident,item['id'],json.dumps(item,ensure_ascii=False)))
            c.execute('INSERT OR REPLACE INTO evidence_scans VALUES(?,?)',(ident,json.dumps(result,ensure_ascii=False)))
        return self.detail(ident)

    def evidence_candidates(self,ident):
        """Stored WeChat candidates for one deal; read-only, used to spot newly matched evidence."""
        with self.db() as c:
            return [json.loads(r[0]) for r in c.execute('SELECT data FROM evidence WHERE deal_id=?',(identity(ident),))]
    def pending_proposals(self,limit=50):
        """Proposals waiting for the user's confirmation; nothing here has touched the ledger."""
        if type(limit) is not int or not 1<=limit<=200:raise ValueError('建议数量无效')
        with self.db() as c:
            return [json.loads(r[0]) for r in c.execute('SELECT data FROM proposals WHERE result IS NULL ORDER BY rowid DESC LIMIT ?',(limit,))]
    def pending_proposal_keys(self,ident):
        """Same deal plus same operation and same field set is never proposed twice."""
        with self.db() as c:
            rows=list(c.execute('SELECT data FROM proposals WHERE deal_id=? AND result IS NULL',(identity(ident),)))
        return {(p.get('operation'),tuple(sorted(p.get('changes',{})))) for p in (json.loads(r[0]) for r in rows)}
    def activity_window(self,start,end,limit=2000):
        """Ledger edits inside a time window, for the weekly report. Audit rows only."""
        if not isinstance(start,str) or not isinstance(end,str) or start>end:raise ValueError('统计区间无效')
        if type(limit) is not int or not 1<=limit<=5000:raise ValueError('统计条数无效')
        with self.db() as c:
            rows=list(c.execute('SELECT deal_id,revision,at,action,data FROM audit WHERE at>=? AND at<? ORDER BY at,deal_id,revision LIMIT ?',(start,end,limit)))
        out=[]
        for r in rows:
            d=json.loads(r['data'])
            out.append(dict(deal_id=r['deal_id'],revision=r['revision'],at=r['at'],action=r['action'],
                            title=d['title'],customer=d['customer'],stage=d['stage']))
        return out
    def review_evidence(self,ident,p):
        fields(p,('revision','request_key','evidence_id','state'))
        if p.get('state') not in ('confirmed','rejected','candidate'):raise ValueError('来源核对状态无效')
        with self.lock,self.db() as c:
            c.execute('BEGIN IMMEDIATE');key,digest,old=self._once(c,p,ident+'/evidence')
            if old:return self.detail(old['id'])
            d=self._edit(c,ident,p);eid=identity(p.get('evidence_id'))
            row=c.execute('SELECT data FROM evidence WHERE deal_id=? AND id=?',(ident,eid)).fetchone()
            if not row:raise ValueError('微信来源候选不存在')
            item=json.loads(row[0]);item.update(state=p['state'],reviewed_at=now())
            c.execute('UPDATE evidence SET data=? WHERE deal_id=? AND id=?',(json.dumps(item,ensure_ascii=False),ident,eid))
            self._write(c,d,'核对微信来源 · '+{'confirmed':'关联确认','rejected':'排除关联','candidate':'恢复待核对'}[p['state']]);self._remember(c,key,digest,d)
        return self.detail(ident)

    def apply_proposal(self,p):
        fields(p,('proposal_id',));pid=identity(p.get('proposal_id'))
        with self.lock,self.db() as c:
            c.execute('BEGIN IMMEDIATE')
            row=c.execute('SELECT data,result FROM proposals WHERE id=?',(pid,)).fetchone()
            if not row:raise ValueError('AI修改建议不存在，请重新提出')
            if row['result']:return self.detail(json.loads(row['result'])['id'])
            proposal=json.loads(row['data'])
            prior=c.execute('SELECT result FROM requests WHERE key=?',('proposal_'+pid,)).fetchone()
            if prior:
                c.execute('UPDATE proposals SET result=? WHERE id=?',(prior[0],pid));return self.detail(json.loads(prior[0])['id'])
            if proposal['operation']=='update' and payment_stage_conflict(proposal['changes'],proposal.get('user_message','')):
                raise ValueError('等待打款应使用待结账，不能替代审核状态或交稿截止；请让助手重新生成，台账未改动')
            self._edit(c,proposal['deal_id'],{'revision':proposal['revision']})
            c.commit()
            request={**proposal['changes'],'revision':proposal['revision'],'request_key':'proposal_'+pid}
            if proposal['operation']=='update':d=self.save({**request,'id':proposal['deal_id']})
            else:d=self.child(proposal['deal_id'],'receipt',request)
            c.execute('UPDATE proposals SET result=? WHERE id=?',(json.dumps({'id':d['id']}),pid))
        return d
    def proposal_status(self,ids):
        if not isinstance(ids,list) or len(ids)>20:raise ValueError('每次最多查询20条建议')
        ids=[identity(v) for v in ids]
        with self.db() as c:
            return {v:bool((r:=c.execute('SELECT result FROM proposals WHERE id=?',(v,)).fetchone()) and r[0]) for v in ids}
    def options(self):
        works=[]
        for folder in sorted(self.store.library.iterdir()):
            if folder.is_dir() and (folder/'article.json').is_file():
                try:
                    manifest,rev,_=self.store.info(folder.name)
                    versions=sorted([p.name for p in (folder/'versions').iterdir() if re.fullmatch(r'v\d{4,}',p.name)],reverse=True)
                    works.append(dict(slug=folder.name,title=manifest['title'],version=rev['id'],versions=versions))
                except (OSError,ValueError,KeyError):continue
        return dict(stages=STAGES,stage_groups=STAGE_GROUPS,delivery_stages=DELIVERY_STAGES,currencies=CURRENCIES,works=works,bindings=self.store.feedback.bindings()['bindings'])
    def listing(self,month='',q='',stage='',payment='',archived=False):
        month=month or datetime.now(ZoneInfo('Asia/Shanghai')).strftime('%Y-%m')
        if not re.fullmatch(r'\d{4}-(?:0[1-9]|1[0-2])',month):raise ValueError('统计月份无效')
        q=text(q,'搜索',500).casefold()
        if stage and stage not in STAGES:raise ValueError('状态无效')
        if payment and payment not in ('unpaid','partial','settled','reported_paid'):raise ValueError('收款状态无效')
        with self.lock,self.db() as c:
            all_deals=[json.loads(r[0]) for r in c.execute('SELECT data FROM deals ORDER BY rowid DESC')]
            imports=[json.loads(r[0]) for r in c.execute('SELECT data FROM imports')]
        s=dict(month=month,signed=0,closed=0,published=0,received={k:Decimal(0) for k in CURRENCIES},outstanding={k:Decimal(0) for k in CURRENCIES},unknown_quote=0,unknown_dates=0,needs_review=0,receipt_count=0,recorded_quote={k:Decimal(0) for k in CURRENCIES},recorded_quote_count=0)
        result=[];stage_counts={k:0 for k in STAGES}
        for d in all_deals:
            cash=self.cash(d)
            if not d['archived']:stage_counts[d['stage']]+=1
            if not d['archived'] and d['stage']!='cancelled' and d['quote'] is not None and d['currency']:
                s['recorded_quote'][d['currency']]+=Decimal(d['quote']);s['recorded_quote_count']+=1
            with self.db() as c:
                evidence=[json.loads(r[0]) for r in c.execute('SELECT data FROM evidence WHERE deal_id=?',(d['id'],))]
                row=c.execute('SELECT data FROM evidence_scans WHERE deal_id=?',(d['id'],)).fetchone()
            d['evidence_counts']={state:sum(x['state']==state for x in evidence) for state in ('confirmed','candidate','rejected')}
            d['evidence_scan']=json.loads(row[0]) if row else None
            d['last_evidence_at']=max([x['at'] for x in evidence if x['state']=='candidate'],default='')
            d['last_business_at']=max([x['at'] for x in evidence if x['state']=='confirmed']+[d['signed_on'],d['completed_on']]+[x['paid_on'] for x in d['receipts'] if not x.get('voided_at')]+[x['published_on'] for x in d['publications'] if not x.get('voided_at')],default='')
            s['signed']+=int(d['signed_on'].startswith(month))
            s['closed']+=int(d['completed_on'].startswith(month) and d['stage'] in ('closed','settled'))
            for r in d['receipts']:
                if not r.get('voided_at') and r['paid_on'].startswith(month):
                    s['received'][r['currency']]+=Decimal(r['amount']);s['receipt_count']+=1
            for p in d['publications']:
                if not p.get('voided_at'):
                    s['published']+=int(p['published_on'].startswith(month));s['unknown_dates']+=int(not p['published_on'])
            if not d['archived'] and d['stage']!='cancelled':
                s['needs_review']+=int(d['needs_review'])
                s['unknown_dates']+=int(not d['signed_on'] and d['stage'] not in ('contact','negotiating'))
                if cash['status']!='settled' and d['stage'] not in ('contact','negotiating'):
                    if cash['outstanding'] is not None:s['outstanding'][d['currency']]+=Decimal(cash['outstanding'])
                    else:s['unknown_quote']+=1
            if (archived or not d['archived']) and (not stage or d['stage']==stage) and (not payment or cash['status']==payment) and (not q or q in ' '.join(str(d[k]) for k in ('title','customer','notes','original_text')).casefold()):
                result.append({**d,'cash':cash})
        for k in ('received','outstanding','recorded_quote'):s[k]={c:format(v,'.2f') for c,v in s[k].items()}
        return dict(deals=result,summary=s,stage_counts=stage_counts,imports=imports,total=len(result))
    def _bind_post(self,ident,key,child):
        from publication_metrics import PUBLISHED,normalized_url
        canonical=normalized_url(child['url'],child['platform'])
        # Register the user-confirmed external URL in the existing publication service.
        # A stable marker recovers a crash between its file write and this ledger commit.
        if child['slug'] and not child['delivery_id']:
            marker='商单发布关联 '+ident+'/'+key
            with self.store.lock:
                candidates=self.store.delivery_records(child['slug'])
                record=None
                for candidate in candidates:
                    # 复用统计服务那份已发布清单：X 官方接口与 Buffer 确认已发布的记录同样是已发布项，
                    # 商单复盘不该另写一份少两类的名单，不然同一条发布会被重复登记成新记录。
                    if candidate.get('kind') not in PUBLISHED or candidate.get('platform')!=child['platform'] or candidate.get('version')!=child['version']:continue
                    try:matches=normalized_url(candidate.get('url',''),child['platform'])==canonical
                    except (ValueError,TypeError):continue
                    if matches and (candidate.get('account')==child['account'] or candidate.get('note')==marker):
                        record=candidate;break
                if record is None:
                    record=self.store.record_delivery(child['slug'],dict(platform=child['platform'],version=child['version'],url=child['url'],account=child['account'],note=marker))
            child['delivery_id']=record['id']
    def child(self,ident,kind,p):
        allowed={'receipt':('revision','request_key','amount','currency','paid_on','note'),
                 'deliverable':('revision','request_key','child_id','title','stage','platform','due_on','slug','version','notes'),
                 'publication-edit':('revision','request_key','child_id','published_on','quality_note','slug','version'),
                 'publication':('revision','request_key','deliverable_id','slug','delivery_id','platform','url','account','published_on','quality_note','version'),
                 'void':('revision','request_key','kind','child_id','reason')}[kind]
        fields(p,allowed)
        with self.lock,self.db() as c:
            c.execute('BEGIN IMMEDIATE');key,digest,old=self._once(c,p,ident+'/'+kind)
            if old:return self.detail(old['id'])
            d=self._edit(c,ident,p)
            child=dict(id=uuid.uuid4().hex,created_at=now())
            if kind=='receipt':
                child.update(amount=amount(p.get('amount'),True),currency=currency(p.get('currency'),True),paid_on=day(p.get('paid_on',''),'到账日期'),note=text(p.get('note',''),'收款说明'))
                if not child['paid_on'] or Decimal(child['amount'])<=0:raise ValueError('收款需为正数，并填写实际到账日期')
                d['receipts'].append(child)
            elif kind=='deliverable':
                child.update(title=text(p.get('title',''),'交付名称',2000,True),stage=p.get('stage','pending'),platform=text(p.get('platform',''),'平台',100),due_on=day(p.get('due_on',''),'交付日期'),notes=text(p.get('notes',''),'交付说明'),slug='',version='')
                if not isinstance(child['stage'],str) or child['stage'] not in DELIVERY_STAGES:raise ValueError('交付状态无效')
                if p.get('slug'):
                    if not p.get('version'):raise ValueError('关联作品必须选择固定版本')
                    manifest,rev,_=self.store.info(p['slug'],p['version']);child.update(slug=p['slug'],version=rev['id'])
                if p.get('child_id'):
                    previous=next((x for x in d['deliverables'] if x['id']==p['child_id'] and not x.get('voided_at')),None)
                    if not previous:raise ValueError('交付项不存在')
                    previous.update({k:v for k,v in child.items() if k not in ('id','created_at')});previous['updated_at']=now()
                else:d['deliverables'].append(child)
            elif kind=='publication':
                from publication_metrics import DOMAINS,platform_url,normalized_url
                slug=p.get('slug','');delivery=p.get('delivery_id','')
                if delivery:
                    binding=self.store.feedback._binding(slug,delivery)
                    child.update({k:binding[k] for k in ('slug','delivery_id','platform','version','url','account')})
                else:
                    platform=p.get('platform','')
                    if not isinstance(platform,str) or platform not in DOMAINS:raise ValueError('发布平台无效')
                    child.update(platform=platform,url=platform_url(p.get('url',''),platform),account=text(p.get('account',''),'发布账号',200,True),slug='',version='',delivery_id='')
                    if slug:
                        if not p.get('version'):raise ValueError('关联作品需固定版本')
                        _,rev,_=self.store.info(slug,p['version']);child.update(slug=slug,version=rev['id'])
                child.update(published_on=day(p.get('published_on',''),'实际发布时间'),quality_note=text(p.get('quality_note',''),'效果复盘'),deliverable_id=p.get('deliverable_id',''))
                if child['deliverable_id'] and not any(x['id']==child['deliverable_id'] and not x.get('voided_at') for x in d['deliverables']):raise ValueError('交付项不存在')
                if not child['slug'] and child['deliverable_id']:
                    item=next(x for x in d['deliverables'] if x['id']==child['deliverable_id'])
                    if item.get('slug'):
                        self.store.info(item['slug'],item['version'])
                        child.update(slug=item['slug'],version=item['version'])
                canonical=normalized_url(child['url'],child['platform'])
                if c.execute('SELECT 1 FROM posts WHERE url=?',(canonical,)).fetchone():raise DealConflict('此发布链接已关联商单，请到原商单中查看')
                self._bind_post(ident,key,child)
                c.execute('INSERT INTO posts VALUES(?,?,?)',(canonical,ident,child['id']));d['publications'].append(child)
            elif kind=='publication-edit':
                target=next((x for x in d['publications'] if x['id']==p.get('child_id') and not x.get('voided_at')),None)
                if not target:raise ValueError('发布项不存在')
                target.update(published_on=day(p.get('published_on',''),'实际发布日期'),quality_note=text(p.get('quality_note',''),'效果复盘'),updated_at=now())
                if p.get('slug'):
                    if target.get('delivery_id'):
                        if p['slug']!=target['slug'] or p.get('version')!=target['version']:raise ValueError('此发布项已有固定作品版本，不能自动替换')
                    else:
                        if not p.get('version'):raise ValueError('请选择固定作品版本')
                        _,rev,_=self.store.info(p['slug'],p['version']);target.update(slug=p['slug'],version=rev['id'])
                        self._bind_post(ident,key,target)
            else:
                group={'receipt':'receipts','deliverable':'deliverables','publication':'publications'}.get(p.get('kind'))
                if not group:raise ValueError('记录类型无效')
                target=next((x for x in d[group] if x['id']==p.get('child_id')),None)
                if not target or target.get('voided_at'):raise ValueError('记录不存在或已作废')
                target.update(voided_at=now(),void_reason=text(p.get('reason',''),'作废原因',2000,True))
                if group=='publications':c.execute('DELETE FROM posts WHERE deal_id=? AND publication_id=?',(ident,target['id']))
            if kind=='void' and p['kind']=='receipt' and d['stage']=='settled' and self.cash(d)['status']!='settled':d['stage']='payment_pending'
            if any(len(d[k])>1000 for k in ('receipts','deliverables','publications')):raise ValueError('单个商单记录已超过1000条')
            self._write(c,d,{'receipt':'登记实际到账','deliverable':'保存交付项','publication':'关联已发布内容','publication-edit':'补充发布日期与复盘','void':'作废记录（原记录保留）'}[kind]);self._remember(c,key,digest,d)
        return self.detail(ident)
    def from_clue(self,p):
        fields(p,('clue_id','deal_id','revision','request_key','title','customer'))
        clue=self.store.clues.detail(p.get('clue_id'))
        if 'business' not in clue['categories']:raise ValueError('请选择商单类线索；先在原线索中核对分类')
        with self.lock,self.db() as c:
            c.execute('BEGIN IMMEDIATE');key,digest,old=self._once(c,p,'from-clue')
            row=c.execute('SELECT deal_id FROM sources WHERE clue_id=?',(clue['id'],)).fetchone()
            if row:
                if p.get('deal_id') and p['deal_id']!=row[0]:raise DealConflict('线索已关联其他商单')
                return self.detail(row[0])
            d=self._edit(c,p['deal_id'],p) if p.get('deal_id') else self._new(dict(title=p.get('title',clue['title']),customer=p.get('customer',''),notes=clue['summary']))
            d['sources'].append(dict(clue_id=clue['id'],revision=clue['revision'],title=clue['title'],summary=clue['summary'],recorded_at=now(),url='/clues.html?'+urlencode({'clue':clue['id']})))
            c.execute('INSERT INTO sources VALUES(?,?)',(clue['id'],d['id']));self._write(c,d,'人工确认商单线索');self._remember(c,key,digest,d)
        return self.detail(d['id'])
    def import_initial(self):
        path=os.environ.get('ARTICLE_STUDIO_DEALS_SEED','').strip()
        if path:
            try:
                with Path(path).open('rb') as source:raw=source.read(2*1024*1024+1)
                if len(raw)>2*1024*1024:raise ValueError('商单导入文件过大')
                document=json.loads(raw)
            except (OSError,UnicodeDecodeError,json.JSONDecodeError) as exc:
                raise ValueError('无法读取商单导入文件；请核对 JSON 格式与本机路径') from exc
            fields(document,('rows','unassigned','source_url'))
            rows=document.get('rows',[]);unassigned=document.get('unassigned',[])
            if not isinstance(rows,list) or len(rows)>1000 or not isinstance(unassigned,list) or len(unassigned)>1000:
                raise ValueError('商单导入 rows 和 unassigned 必须是最多 1000 项的列表')
            unassigned=[text(v,'未分配记录',4000) for v in unassigned]
            source_url=text(document.get('source_url',''),'导入来源',2000)
            key='initial-json-'+hashlib.sha256(raw).hexdigest()
        else:
            try:
                import deals_seed as seed
            except ImportError:
                return dict(configured=False,imported=False,count=0,ids=[],unassigned=[],
                            message='未配置商单导入文件；请新建商单，或配置 ARTICLE_STUDIO_DEALS_SEED 后导入。')
            rows=list(getattr(seed,'ROWS',[]));unassigned=list(getattr(seed,'UNASSIGNED',[]))
            if len(rows)>1000 or len(unassigned)>1000:
                raise ValueError('商单导入 rows 和 unassigned 必须是最多 1000 项的列表')
            unassigned=[text(v,'未分配记录',4000) for v in unassigned]
            source_url=text(getattr(seed,'SOURCE_URL',''),'导入来源',2000)
            # 稳定键：本机私有种子沿用既有 imports 记录，避免重复导入。
            key='huang-ledger-20261006-v1'
        prepared=[]
        for row in rows:
            fields(row,('title','customer','stage','text','group','notes','quote','currency','count'))
            original=text(row.get('text',''),'原始记录',8000)
            group=text(row.get('group',''),'原始分组',80)
            d=self._new(dict(title=row.get('title',''),customer=row.get('customer',row.get('title','')),stage=row.get('stage','unknown'),price_note=original,notes=row.get('notes',''),quote=row.get('quote'),currency=row.get('currency'),expected_deliverables=row.get('count')))
            d.update(original_group=group,original_text=original,reported_payment='client_paid' if group=='已付' else 'unpaid' if group=='未付' else 'unknown')
            prepared.append(d)
        with self.lock,self.db() as c:
            c.execute('BEGIN IMMEDIATE')
            old=c.execute('SELECT data FROM imports WHERE key=?',(key,)).fetchone()
            if old:return json.loads(old[0])
            ids=[]
            for d in prepared:
                self._write(c,d,'导入配置清单（待核对，无到账推断）');ids.append(d['id'])
            result=dict(key=key,configured=True,imported=True,created_at=now(),ids=ids,count=len(ids),unassigned=unassigned,source_url=source_url)
            c.execute('INSERT INTO imports VALUES(?,?)',(key,json.dumps(result,ensure_ascii=False)))
        return result
