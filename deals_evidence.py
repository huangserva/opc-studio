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
    if not path:
        private=Path(__file__).resolve().parent/'deals_aliases.json'
        path=str(private) if private.is_file() else ''
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
    if deal.get('original_text'):item('original','黄总原始清单 · '+deal.get('original_group',''),'',deal['original_text'],basis='原记录未提供发生时间；导入时间不替代业务时间')
    for e in deal.get('evidence',[]):
        if e['state']=='rejected':continue
        item('wechat',e['title'],e['at'],e['text'],evidence=e,basis='微信原消息时间')
    if deal.get('signed_on'):item('signed','实际签约',deal['signed_on'])
    if deal.get('completed_on'):item('completed','实际结案',deal['completed_on'])
    for x in deal['receipts']:item('receipt','到账 '+x['amount']+' '+x['currency'],x['paid_on'],x.get('note',''),voided=bool(x.get('voided_at')))
    for x in deal['publications']:item('publication','发布 · '+x['platform'],x['published_on'],x.get('quality_note',''),url=x['url'],voided=bool(x.get('voided_at')))
    for x in deal.get('audit',[]):item('audit',x['action'],x['at'],'商单版本 '+str(x['revision']),basis='台账操作时间，非合作发生时间')
    return sorted(events,key=lambda x:x['at'],reverse=True)


def business_dates(deal):
    """Business dates only; neither import timestamps nor audit clocks are events."""
    result=[]
    labels={'contacted_on':'建联','signed_on':'签约','deadline':'交稿截止','completed_on':'完成'}
    for key,label in labels.items():
        if deal.get(key):result.append(dict(kind=key,date=deal[key],label=label,**deal.get('date_sources',{}).get(key,{})))
    for group,key,label in [('publications','published_on','发布'),('receipts','paid_on','到账')]:
        for x in deal.get(group,[]):
            if not x.get('voided_at') and x.get(key):
                result.append(dict(kind=key,date=x[key],label=label,amount=x.get('amount'),currency=x.get('currency'),url=x.get('url',''),source='实际记录'))
    for x in deal.get('date_events',[]):
        if any(y['kind']==x['kind'] and y['date']==x['date'] for y in result):continue
        result.append(dict(kind=x['kind'],date=x['date'],label='发布' if x['kind']=='published_on' else '到账日期（金额待补）',source=x['source'],evidence=x.get('evidence')))
    return sorted(result,key=lambda x:x['date'])


def evidence_date(fragment, at):
    """Return a bounded candidate date, never silently turn message time into event time."""
    from datetime import timedelta,date
    from zoneinfo import ZoneInfo
    try:base=datetime.fromisoformat(at).astimezone(ZoneInfo('Asia/Shanghai')).date()
    except (ValueError,TypeError):return '', '不确定：消息时间无效'
    matches=list(re.finditer(r'(?<![A-Za-z0-9.])(?:(20\d{2})[年/.-])?(\d{1,2})[月/.-](\d{1,2})(?:日|号)?(?![\d.])',fragment))
    for match in reversed(matches):
        if re.match(r'\s*(?:元|美金|美元|功能|版本|模型)',fragment[match.end():]):continue
        if re.search(r'(?:版本|version|audio|seed|v)\s*$',fragment[:match.start()],re.I):continue
        try:
            value=date(int(match[1] or base.year),int(match[2]),int(match[3]))
            if not match[1] and (value-base).days>180:value=value.replace(year=value.year-1)
            return value.isoformat(), '原文明确日期' if match[1] else '推算：年份按消息时间补齐'
        except ValueError:continue
    for word,delta in [('前天',-2),('昨天',-1),('今天',0),('明天',1),('后天',2)]:
        if word in fragment:return (base+timedelta(days=delta)).isoformat(),'推算：'+word+'按消息时间换算'
    match=re.search(r'(下周|本周|这周)([一二三四五六日天])?|(?:(周)([一二三四五六日天]))',fragment)
    if match:
        weekday=match[2] or match[4]
        offset='一二三四五六日天'.index(weekday) if weekday else 0
        offset=min(offset,6)
        value=base-timedelta(days=base.weekday())+timedelta(days=offset+(7 if match[1]=='下周' else 0))
        return value.isoformat(),'推算：'+match[0]+('，未指明星期，暂取周一，需修改确认' if not weekday else '按消息时间换算')
    return '', '不确定：原文没有业务日期'


def completed_statement(fragment, pattern, allow_feedback=False):
    """Validate the event clause separately from a following feedback question."""
    text=fragment.strip();match=re.search(pattern,text)
    if not match:return False
    claim=text[:match.end()];tail=text[match.end():].strip()
    # The event itself must be asserted, rather than requested or hypothetical.
    if re.search(r'[?？]|吗|是否|有没有|明天|后天|下周|预计|计划|打算|准备|希望|如果|假如|将要|将会|还没|尚未|还未|并未|(?:没|未)(?:有|曾)?(?:写|交|发|完成|到|收|打|转)|(?:请|麻烦|辛苦|务必|需要|要求|记得).{0,24}(?:写|交|发|完成|到|收|打|转)',claim):return False
    feedback=bool(allow_feedback and re.match(r'^(?:再)?(?:(?:请|麻烦|劳烦|帮忙)?(?:看看|看下|看一眼|过目|查收)|(?:你|您)(?:看|觉得)|要不要|需不需要|是否需要|有没有|有无).*(?:改|修改|调整|意见|问题|优化|润色)',tail))
    if re.match(r'^(?:了|啦|咯)?(?:后|以后|之后|再|就|才|提交|放|发我|发给|发到|上传)',tail) and not feedback:return False
    if re.search(r'[?？]|吗|么|是否|有没有|要不要|需不需要',tail) and not feedback:return False
    return True


def completion_receipt(fragment):
    """Written/delivered draft receipts, including colloquial completed aspect."""
    obj=r'(?:初稿|稿子|稿件|文案|视频|文章)'
    verb=r'(?:交(?:给(?:你|您|客户)|过去|付|稿)?|提交(?:给(?:你|您|客户))?|发(?:送)?(?:给(?:你|您|客户)|过去|你|您))'
    pattern=(obj+r'.{0,4}(?:已(?:经)?'+verb+r'|'+verb+r'(?:了|啦|咯)|发过去(?:了|啦|咯)?)'
        +r'|(?:初稿|稿子|稿件|文案).{0,6}(?:已(?:经)?完成|完成了|已(?:经)?写好|写好了|写完了|好了)'
        +r'|(?:我|我们|本人|这边).{0,4}(?:(?:已经|已)(?:写好|写完)|(?:写好|写完)(?:了|初稿|稿件|稿子))'
        +r'|(?:我|我们|本人|这边).{0,4}已(?:经)?(?:交|提交|发过去).{0,4}(?:初稿|稿子|稿件|文案)'
        +r'|^(?:已(?:经)?)?(?:写好|写完)了[!！。\s]*$|^(?:初稿|稿子|稿件)(?:已(?:经)?)?完成[!！。\s]*$')
    return completed_statement(fragment,pattern,allow_feedback=True)


def payment_receipt(fragment):
    """Personal funds received/paid statements, never product account balances."""
    if re.search(r'余额|积分|充值|credits|退款|退费',fragment,re.I):return False
    money=r'(?:稿费|合作费|尾款|首款|余款|定金|款项|款|费用)'
    verb=r'(?:到账|到款|到|收到|打款|转账|转)'
    pattern=(money+r'.{0,2}(?:已(?:经)?'+verb+r'|'+verb+r'(?:了|啦|咯))'
        +r'|'+money+r'(?:已(?:经)?)?收到(?:了|啦)?'
        +r'|(?:已(?:经)?收到|收到了)'+money+r'|收到'+money+r'(?:了|啦)'
        +r'|已(?:经)?(?:打款|转(?:账)?)(?:了|给你|给您|$)')
    return completed_statement(fragment,pattern)


def publication_receipt(fragment):
    """Identify the object of a completed publication, not its subject matter."""
    predicate=r'(?:已(?:经)?|刚(?:刚)?|今天|昨天).{0,4}(?:发布|发帖|发推)(?:了)?|(?:发布|发帖|发推)了'
    match=re.search(predicate,fragment)
    if not match or not completed_statement(fragment,predicate):return False
    action=re.search(r'发布|发帖|发推',match.group())
    verb_start=match.start()+action.start();verb_end=match.start()+action.end()
    after=fragment[verb_end:];after=re.sub(r'^了','',after).strip()
    # "已发布状态" names a UI state, rather than asserting an event.
    if re.match(r'^(?:的)?状态',after):return False
    content={'文章','稿子','稿件','推文','帖子','视频','图文'}
    nouns=r'文章|稿子|稿件|推文|帖子|视频|图文|网站|网页|站点|产品|模型|芯片|版本|页面|应用'
    before_objects=list(re.finditer(nouns,fragment[:verb_start]))
    objects=before_objects
    # A direct post-verbal noun phrase can be the object (发布了…文章).
    # Locatives (文章已发布到网站) and follow-up requests are not objects.
    if after and not re.match(r'到|至|在|于|请|看看|看下|谢谢|辛苦',after):
        phrase=re.match(r'[\u4e00-\u9fffA-Za-z0-9-]{1,24}',after)
        after_objects=list(re.finditer(nouns,phrase[0])) if phrase else []
        if after_objects:objects=after_objects
    if objects:return objects[-1][0] in content
    return action[0] in ('发帖','发推')


def explicitly_other_order(body):
    """Keep explicit attribution across clauses; ordinary hearsay stays uncertain."""
    if re.search(r'(?:与|和)(?:我|我们)(?:的)?(?:这|本)?(?:一)?单.{0,4}(?:无关|没关系|没有关系)|(?:不是|不属于|并非)(?:我|我们)(?:的)?(?:这|本)?(?:一)?单',body):return True
    other=r'(?:另一个|另一位|其他|别的)(?:博主|达人|作者|创作者)(?:的)?.{0,12}(?:稿子|稿件|初稿|文章|视频|推文|帖子|商单|合作)|(?:别人|他人)的(?:稿子|稿件|初稿|文章|视频|推文|帖子|商单)'
    for match in re.finditer(other,body):
        if not re.search(r'(?:不是|并非|不属于)$',body[:match.start()]):return True
    return False


def lifecycle_candidates(deal):
    """Conservative extraction from already matched WeChat evidence; zero model calls.

    Candidate attribution is deliberately unconfirmed. Product/news release language
    and hypothetical contract terms must not become the owner's publication facts.
    """
    from deals_store import canonical_stage
    usable=[]
    for e in deal.get('evidence',[]):
        if e.get('state')=='rejected':continue
        body=e.get('text','');p=e.get('provenance',{})
        if explicitly_other_order(body):continue
        # Platform billing notices describe the service, not the owner's order.
        if re.search(r'充值|支付渠道|平台余额|credits|积分|计费|退费|费用退还|模型请求|内容安全审核',body,re.I) and re.search(r'规则|通知|公告|调整|最低|额度|比例|计算|credits|余额',body,re.I):continue
        commercial=bool(re.search(r'老师|稿子|稿件|尾款|首款|余款|定金|款项|合作费|稿费|底薪|商单|交稿|初稿|推广服务|合作|报价|排期|宣发|合同|协议|打款|结款|到账|费用|ddl|提交.{0,6}脚本',body))
        if not commercial and not completion_receipt(body) and not payment_receipt(body):continue
        if re.search(r'没找我|没选中|没有合作|未选中|不是一波|合同条款|甲方及|乙方及',body):continue
        # A general group news post is not an order-specific conversation.
        title=e.get('title','').casefold();terms=e.get('terms',[])
        if p.get('conversation_type')=='group' and not any(len(t)>1 and t.casefold() in title for t in terms) and not re.search(r'合作Brief|合作报价单|商单（|合作.{0,8}宣发',body,re.I):continue
        if len(body)>1600 or re.search(r'AI日报|【产品发布|【模型发布|新闻简报',body):continue
        usable.append(e)
    usable.sort(key=lambda x:x.get('at',''))
    out=[]
    def candidate(field,value,e,fragment,reason,entered=''):
        if not value:return
        if field not in ('stage','published_on','paid_on') and deal.get(field):return
        if field=='stage' and (deal['stage']!='unknown' or canonical_stage(deal['stage'])==value):return
        if field in ('published_on','paid_on') and any(x['kind']==field and x['date']==value for x in business_dates(deal)):return
        provenance=e.get('provenance',{})
        out.append(dict(field=field,value=value,entered_on=entered,certainty='不确定，归属与业务含义待核对',reason=reason,
            evidence=dict(id=e['id'],excerpt=fragment[:600],at=e.get('at',''),conversation=e.get('title','').split(' · ')[0],sender=provenance.get('sender',''),url=e.get('url',''))))
    # Earliest known business message is a candidate for first contact, not proof of it.
    if usable and not deal.get('contacted_on'):
        e=usable[0];value,_=evidence_date('今天',e.get('at'))
        candidate('contacted_on',value,e,e['text'],'不确定：已知最早合作消息的日期，可能早已建联；请核对或修改')
    for e in usable:
        body=e['text'];message_day,_=evidence_date('今天',e.get('at'))
        for fragment in re.split(r'[\n。；;，,]',body):
            if not fragment.strip():continue
            value,basis=evidence_date(fragment,e.get('at'))
            completed=completion_receipt(fragment);paid=payment_receipt(fragment);published=publication_receipt(fragment)
            if not value and (completed or paid or published or re.search(r'刚到账|到账了|已到账|收到了',fragment)):value,basis=message_day,'推算：完成态消息以消息日期作为候选，需核实实际发生日'
            planned=bool(re.search(r'[?？]|吗|是否|还没|没有|尚未|(?:没|未)(?:有)?(?:到账|到款|收款|收到)|预计|计划|打算|准备|能否|可以|能不能|希望|需要|麻烦|之后|届时|如果|待|将|明天|后天|下周|要发布|还请|请|有档期|会发布',fragment))
            if value:
                if re.search(r'交稿|截稿|截止|初稿|最迟|提交.{0,6}脚本',fragment) and not completed and not re.search(r'交稿了|已交稿',fragment):candidate('deadline',value,e,fragment,basis+'；交稿时间待确认')
                if re.search(r'签约|签署|签订|签合同',fragment) and not planned:candidate('signed_on',value,e,fragment,basis+'；签约事实与日期待确认')
                if published and not planned:candidate('published_on',value,e,fragment,basis+'；发布事实与日期待确认')
                if (paid or re.search(r'到账|收到.{0,8}(款|元|美金)',fragment)) and not planned and not re.search(r'余额|积分|充值|credits',fragment,re.I):candidate('paid_on',value,e,fragment,basis+'；只登记日期，不生成到账金额或结清状态')
            stage=None
            if completed or not planned:
                if completed or re.search(r'已交稿|交稿了',fragment) and not re.search(r'[?？]|吗|没|未|请|如果|之后|以后',fragment):stage='draft'
                elif published:stage='published'
                elif re.search(r'开始写|正在写|在写',fragment):stage='writing'
                elif re.search(r'做资料|整理资料|已收.{0,4}(素材|需求)|brief|素材已',fragment,re.I):stage='preparing'
            if stage:
                candidate('stage',stage,e,fragment,'不确定：消息描述可能对应此进度，未证实属于本单；进入日期按消息北京时间推算，需核实实际发生日',value or message_day)
        # Negotiation is safe only as an explicitly uncertain proposal for unknown records.
        if deal['stage']=='unknown' and not any(x['field']=='stage' and x['evidence']['id']==e['id'] for x in out) and re.search(r'合作|报价|合同|协议|推广|宣发|需求|商单|费用|底薪',body):
            candidate('stage','negotiating',e,body,'不确定：出现合作讨论，仅能支持“在谈”候选，当前进度可能更晚',message_day)
    # Bounded UI: retain earliest contact, latest candidate for each other field.
    chosen={}
    for item in out:
        if item['field']=='contacted_on' and item['field'] in chosen:continue
        if item['field']=='stage' and item['field'] in chosen and '仅能支持' in item['reason'] and '仅能支持' not in chosen[item['field']]['reason']:continue
        chosen[item['field']]=item
    return list(chosen.values())
