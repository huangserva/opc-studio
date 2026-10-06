"""Local WeChat evidence candidates. Brand matches never confirm an order or payment."""
import hashlib
import json
import os
from pathlib import Path
import re
from xml.etree import ElementTree as ET
from datetime import datetime, timezone
from urllib.parse import urlencode

def aliases():
    path=os.environ.get('ARTICLE_STUDIO_DEAL_ALIASES','').strip()
    if not path:return {}
    try:
        with Path(path).open('rb') as source:raw=source.read(256*1024+1)
        if len(raw)>256*1024:raise ValueError('商单别名文件过大')
        data=json.loads(raw)
    except (OSError,UnicodeDecodeError,json.JSONDecodeError) as exc:
        raise ValueError('无法读取商单别名 JSON 文件') from exc
    if not isinstance(data,dict) or len(data)>1000:raise ValueError('商单别名须为最多1000项的对象')
    for key,values in data.items():
        if not isinstance(key,str) or not key.strip() or len(key)>80 or not isinstance(values,list) or not 1<=len(values)<=20 or any(not isinstance(v,str) or not v.strip() or len(v)>80 for v in values):
            raise ValueError('商单别名须为名称到非空文本列表的映射')
    return data

BUSINESS = re.compile(r'\d+\s*单|写好|写完|初稿完成|合作|报价|预算|稿费|商单|结款|付款|转账|收款|合同|协议|排期|初稿|交稿|定金|尾款|推广|宣发|需求|费用|发布|到账|\d+\s*(?:元|美金|美元|USD|CNY)',re.I)
MONEY = re.compile(r'(?:[¥￥$]\s*\d[\d,]*(?:\.\d{1,2})?|\d[\d,]*(?:\.\d{1,2})?\s*(?:万元|美金|美元|元|USD|CNY))(?:\s*[/／]\s*(?:篇|条|个|单))?',re.I)

def visible_text(raw):
    # Media XML contains transport keys; show only visible messages and references.
    start=raw.find('<msg>')
    if start<0:return raw
    try:
        root=ET.fromstring(raw[start:]);parts=[]
        for selector in ('.//appmsg/title','.//appmsg/des','.//refermsg/content'):
            for n in root.findall(selector):
                if n.text and n.text.strip():parts.append(n.text.strip())
        return '\n'.join(dict.fromkeys(parts)) or '媒体 / 文件消息（文本未转录）'
    except ET.ParseError:return '媒体 / 引用消息（文本不完整，请打开原始来源核对）'

def scan(knowledge, deal, query=''):
    if not isinstance(query,str):raise ValueError('微信核对关键词格式错误')
    terms = [x.strip() for x in query.split(',') if x.strip()] if query else aliases().get(deal['title'],[deal['customer'].split(' · ')[0]])
    if not 1 <= len(terms) <= 6 or any(len(x)>80 for x in terms):raise ValueError('品牌关键词为1–6个，每项最多80字，用英文逗号分隔')
    matches={};counts={};capped=False
    # Read indexed cache only, never capture/decrypt WeChat or send a corpus to a model.
    with knowledge.db() as c:
        coverage=[dict(r) for r in c.execute("SELECT source,COUNT(*) AS count,MIN(modified) AS earliest,MAX(modified) AS latest FROM documents WHERE kind='wechat' GROUP BY source")]
        for term in terms:
            escaped=term.replace('\\','\\\\').replace('%','\\%').replace('_','\\_')
            if len(term)>=3:
                join=''
                where=' AND d.rowid IN (SELECT rowid FROM document_fts WHERE document_fts MATCH ?)'
                args=['"'+term.replace('"','""')+'"']
            else:
                join='';where=" AND (d.title LIKE ? ESCAPE '\\' OR d.body LIKE ? ESCAPE '\\')";args=['%'+escaped+'%']*2
            sql=" FROM documents d"+join+" WHERE d.kind='wechat'"+where
            count=c.execute('SELECT COUNT(*)'+sql,args).fetchone()[0];counts[term]=count;capped|=count>2000
            for row in c.execute('SELECT d.*'+sql+' ORDER BY d.modified DESC LIMIT 2000',args):
                doc=dict(row);p=json.loads(doc['provenance']);body=visible_text(doc['body']);doc['visible_text']=body
                hits=BUSINESS.findall(body)
                if not hits:continue
                # Private exchanges and self statements are candidates too, never facts.
                score=min(len(hits),5)+(12 if p.get('conversation_type')=='private' else 0)+(3 if p.get('is_self') else 0)
                if term.casefold() in doc['title'].split(' · ')[0].casefold():score+=5
                if len(body)>1000:score-=3
                if term.casefold() in body.casefold():score+=2
                if doc['id'] not in matches:matches[doc['id']]={**doc,'provenance':p,'terms':[],'score':score}
                matches[doc['id']]['terms'].append(term)
    candidates=[]
    for doc in sorted(matches.values(),key=lambda x:(x['score'],x['modified']),reverse=True)[:40]:
        body=doc['visible_text'];digest=hashlib.sha256((doc['id']+doc['hash']).encode()).hexdigest()[:32]
        candidates.append(dict(id=digest,document_id=doc['id'],hash=doc['hash'],title=doc['title'],text=body,
            at=doc['modified'],provenance=doc['provenance'],terms=doc['terms'],state='candidate',
            price_mentions=list(dict.fromkeys(MONEY.findall(body)))[:8],
            url='/knowledge.html?'+urlencode(dict(document=doc['id'],start=0,end=len(doc['body']),hash=doc['hash'])),
            matched_at=datetime.now(timezone.utc).isoformat(),basis='品牌关键词与合作用语命中；尚未核实订单、对象或金额归属'))
    return dict(candidates=candidates,terms=terms,counts=counts,coverage=coverage,
        relevant_total=len(matches),limited=capped or len(matches)>40,
        notice='仅核对当前本机已索引微信范围；候选不是已确认商单。每个关键词最多检索最近2000条，按相关度保留40条，未读媒体与未采集历史不在范围内。')

def timeline(deal):
    events=[]
    def item(kind,title,at,detail='',**kw):
        events.append(dict(kind=kind,title=title,at=at or '',detail=detail,**kw))
    if deal.get('original_text'):item('original','用户原始清单 · '+deal.get('original_group',''),'',deal['original_text'],basis='原记录未提供发生时间；导入时间不替代业务时间')
    for e in deal.get('evidence',[]):
        if e['state']=='rejected':continue
        item('wechat',e['title'],e['at'],e['text'],evidence=e,basis='微信原消息时间')
    if deal.get('signed_on'):item('signed','实际签约',deal['signed_on'])
    if deal.get('completed_on'):item('completed','实际结案',deal['completed_on'])
    for x in deal['receipts']:item('receipt','到账 '+x['amount']+' '+x['currency'],x['paid_on'],x.get('note',''),voided=bool(x.get('voided_at')))
    for x in deal['publications']:item('publication','发布 · '+x['platform'],x['published_on'],x.get('quality_note',''),url=x['url'],voided=bool(x.get('voided_at')))
    for x in deal.get('audit',[]):item('audit',x['action'],x['at'],'商单版本 '+str(x['revision']),basis='台账操作时间，非合作发生时间')
    return sorted(events,key=lambda x:x['at'],reverse=True)
