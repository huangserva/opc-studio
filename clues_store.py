"""Local clue threads: immutable evidence, append-only updates, no model calls."""
from contextlib import contextmanager
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import hashlib
import json
import math
import re
import os
from pathlib import Path
import sqlite3
import uuid
import threading
from functools import wraps
from zoneinfo import ZoneInfo
from events_store import EventConflict, identity, text, flag, due_time

CATEGORIES={'business':'商单','technology':'新技术','topics':'热点话题','projects':'项目进展','other':'其他'}
ZONE=ZoneInfo('Asia/Shanghai')
FIELDS={'title','summary','relevance','categories','priority','kind','tracking','status','tags','heat','heat_source'}
TECH_TAGS={'agents':'Agent 与自动化','memory':'记忆与检索','inference':'推理与显存','models':'模型与训练','tools':'开发工具','multimodal':'图像音视频','hardware':'硬件与设备','security':'安全与隐私','other-tech':'未归类技术'}
TECH_PATTERNS={
 'agents':r'agent|智能体|自动化|工作流|harness|mcp|openclaw|workbuddy',
 'memory':r'记忆|检索|知识库|向量|\brag\b|embedding|memos|remio',
 'inference':r'推理|显存|量化|预填充|prefill|token[s]?/s|\bkv\b|cuda|gguf|tensor|hipblas|kernel|deltanet|gather|dma',
 'models':r'模型|训练|微调|蒸馏|qwen|千问|claude|gpt|gemini|deepseek|llama|moe',
 'tools':r'开发|编程|代码|\bcli\b|\bsdk\b|ide|开源|github|gitlab|截图|prettyshot|浏览器|插件|编辑器',
 'multimodal':r'图像|图片|视频|音频|语音|多模态|tts|asr|diffusion|flux|sora',
 'hardware':r'硬件|显卡|gpu|cpu|nvlink|rtx|\bv100\b|\bh100\b|芯片|服务器|nvidia|amd|苹果|macbook',
 'security':r'安全|隐私|漏洞|权限|加密|鉴权|认证|攻击',
}
def tech_tags(data):
    if 'technology' not in data.get('categories',[]):return []
    if isinstance(data.get('tags'),list):return data['tags'] or ['other-tech']
    content=(data.get('title','')+' '+data.get('summary','')).casefold()
    return [tag for tag,pattern in TECH_PATTERNS.items() if re.search(pattern,content)] or ['other-tech']


def now():return datetime.now(timezone.utc).isoformat()
def digest(value):return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True).encode()).hexdigest()

def source_clock(snapshot):
    p=snapshot.get('provenance') or {}
    if snapshot.get('kind') in ('wechat','wechat_links'):
        value=p.get('timestamp')
        if type(value) in (float,int) and math.isfinite(value) and 0<value<=253402300799:
            return datetime.fromtimestamp(value,timezone.utc).isoformat(),'微信消息时间'
    if snapshot.get('kind')=='aisa':
        if p.get('published_at'):
            try:
                stamp=datetime.fromisoformat(p['published_at'].replace('Z','+00:00'))
                if stamp.utcoffset() is not None:return stamp.astimezone(timezone.utc).isoformat(),'推特发布时间'
            except (ValueError,TypeError,AttributeError):pass
        return '', '来源未提供发布时间'
    if snapshot.get('kind')=='xradar':
        from clues_heat import bucket_time, capture_bucket
        try:
            bucket=p.get('bucket') or capture_bucket(snapshot.get('captured_at',''))
            return bucket_time(bucket).isoformat(),'X 雷达观测槽时间（非首发时间）'
        except (ValueError,TypeError,AttributeError):pass
    if snapshot.get('kind')=='github' and p.get('updated_at'):
        try:
            date=datetime.fromisoformat(p['updated_at'].replace('Z','+00:00'))
            if date.utcoffset() is not None:return date.isoformat(),'GitHub 原文更新时间'
        except (ValueError,TypeError,OverflowError):pass
    if snapshot.get('kind')=='mail' and p.get('date_sent'):
        try:
            date=parsedate_to_datetime(p['date_sent'])
            if date.utcoffset() is not None:return date.isoformat(),'邮件头发信时间'
        except (ValueError,TypeError,OverflowError):pass
    return '', '来源发生时间未知'

def serialized(fn):
    @wraps(fn)
    def wrapped(self,*args,**kwargs):
        with self.lock:return fn(self,*args,**kwargs)
    return wrapped

class Clues:
    def __init__(self,store):
        self.store=store;self.lock=threading.RLock();self.root=Path(store.workspace)/'clues';self.root.mkdir(parents=True,mode=0o700,exist_ok=True);self.root.chmod(0o700)
        self.path=self.root/'clues.db'
        with self.db() as db:
            db.executescript('''CREATE TABLE IF NOT EXISTS threads(id TEXT PRIMARY KEY,data TEXT NOT NULL,revision INTEGER NOT NULL,seen_revision INTEGER NOT NULL DEFAULT 0,created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS entries(id TEXT PRIMARY KEY,thread_id TEXT NOT NULL REFERENCES threads(id),data TEXT NOT NULL,created_at TEXT NOT NULL,clock TEXT NOT NULL,year TEXT NOT NULL,month TEXT NOT NULL,entry_type TEXT NOT NULL,producer_key TEXT UNIQUE);
            CREATE TABLE IF NOT EXISTS observations(thread_id TEXT NOT NULL REFERENCES threads(id),source TEXT NOT NULL,bucket TEXT NOT NULL,mentions INTEGER NOT NULL DEFAULT 0,talkers INTEGER NOT NULL DEFAULT 0,views INTEGER,site_state TEXT NOT NULL DEFAULT '',captured_at TEXT NOT NULL,bars_recent6 REAL,bars_prev6 REAL,ratio6_site REAL,PRIMARY KEY(thread_id,source,bucket));
            CREATE INDEX IF NOT EXISTS observations_day ON observations(bucket);
            CREATE INDEX IF NOT EXISTS entries_thread ON entries(thread_id,created_at);
            CREATE INDEX IF NOT EXISTS entries_year ON entries(year,clock);''')
        self.path.chmod(0o600)

    @contextmanager
    def db(self):
        db=sqlite3.connect(self.path,timeout=15);db.row_factory=sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON');db.create_function('fold',1,lambda v:str(v or '').casefold())
        db.create_function('tech_tags',1,lambda value:json.dumps(tech_tags(json.loads(value))))
        try:
            with db:yield db
        finally:db.close()

    def _row(self,db,thread_id):
        row=db.execute('SELECT * FROM threads WHERE id=?',(identity(thread_id,'线索编号'),)).fetchone()
        if not row:raise ValueError('线索不存在')
        data=json.loads(row['data']);data.setdefault('heat','');data.setdefault('heat_source','');data['tags_source']='manual' if isinstance(data.get('tags'),list) else 'suggested';data['tags']=tech_tags(data)
        return {**data,**{k:row[k] for k in ('id','revision','seen_revision','created_at','updated_at')},'unread':row['revision']>row['seen_revision']}

    def detail(self,thread_id,offset=0,limit=50):
        if type(offset) is not int or offset<0 or type(limit) is not int or not 1<=limit<=100:raise ValueError("历史分页无效")
        with self.db() as db:
            item=self._row(db,thread_id)
            item['entries']=[{**json.loads(r['data']),**{k:r[k] for k in ('id','created_at','clock','year','month','entry_type')}} for r in db.execute('SELECT * FROM entries WHERE thread_id=? ORDER BY rowid DESC LIMIT ? OFFSET ?',(thread_id,limit,offset))]
            item['entries_total']=db.execute('SELECT COUNT(*) FROM entries WHERE thread_id=?',(thread_id,)).fetchone()[0]
            item['entries_next_offset']=offset+limit if offset+limit<item['entries_total'] else None
        for entry in item['entries']:
            for evidence in entry.get('evidence',[]):
                evidence['state']='saved'
                if evidence.get('document_id'):
                    try:evidence['state']='fresh' if self.store.knowledge.document(evidence['document_id'])['hash']==evidence['hash'] else 'changed'
                    except ValueError:evidence['state']='unavailable'
        return item

    @staticmethod
    def _fields(payload,before=None):
        before=before or {};result={}
        for name,maxlen,required in [('title',200,True),('summary',4000,False),('relevance',2000,False)]:result[name]=text(payload.get(name,before.get(name,'')),name,maxlen,required)
        categories=payload.get('categories',before.get('categories',['other']))
        if not isinstance(categories,list) or not categories or len(categories)>5 or any(not isinstance(c,str) or c not in CATEGORIES for c in categories):raise ValueError('请选择有效的线索分类')
        result['categories']=list(dict.fromkeys(categories))
        if len(result['categories'])>1:result['categories']=[c for c in result['categories'] if c!='other']
        for name,allowed,default in [('priority',{'normal','important'},'normal'),('kind',{'information','discussion'},'information'),('status',{'candidate','verified','archived'},'candidate'),('heat',{'','rising','peak','fading'},''),('heat_source',{'','site','computed','manual'},'')]:
            value=payload.get(name,before.get(name,default))
            if not isinstance(value,str) or value not in allowed:raise ValueError('线索状态或类型无效')
            result[name]=value
        if bool(result['heat']) != bool(result['heat_source']):raise ValueError('heat 与 heat_source 必须同时为空或非空')
        result['tracking']=flag(payload.get('tracking',before.get('tracking',False)),'持续跟踪')
        tags=payload.get('tags',before.get('tags') if before.get('tags_source')!='suggested' else None)
        if tags is not None and (not isinstance(tags,list) or len(tags)>len(TECH_TAGS) or any(not isinstance(t,str) or t not in TECH_TAGS for t in tags)):raise ValueError('技术标签无效')
        result['tags']=list(dict.fromkeys(tags)) if tags is not None else None
        result['event_id']=before.get('event_id','')
        return result

    def save(self,payload):
        if not isinstance(payload,dict) or set(payload)-FIELDS-{'id','revision','picks','reason','occurred_at','note'}:raise ValueError('线索参数格式错误')
        picks=payload.get('picks',[])
        snapshots=self.store.events._capture(picks) if picks else []
        for s in snapshots:s['origin_url']=f"/knowledge.html?document={s['document_id']}&start={s['start']}&end={s['end']}&hash={s['hash']}"
        if 'heat' in payload and 'heat_source' not in payload:
            payload={**payload,'heat_source':'manual' if payload['heat'] else ''}
        return self._save(payload,snapshots,manual_heat=('heat' in payload))

    @serialized
    def _save(self,payload,snapshots,producer_key=None,linked_event=None,observations=None,manual_heat=False,refresh_aisa=False):
        # WeChat plugin may pass per-message daily observations here at its existing
        # persist_candidate call; plugin wiring is a separate delivery. No key changes.
        thread_id=identity(payload['id'],'线索编号') if payload.get('id') else uuid.uuid4().hex
        # AIsa 刷新语义需要一个稳定的「本轮自动生成的标题/摘要」基准，写进 evidence 条目，
        # 下次刷新拿它比；不能拿最新条目的 after 当基准（见下面刷新分支）。
        auto_generated={'title':payload.get('title',''),'summary':payload.get('summary','')} if refresh_aisa else None
        occurred=due_time(payload.get('occurred_at',''))
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            if producer_key:
                prior=db.execute('SELECT thread_id FROM entries WHERE producer_key=?',(producer_key,)).fetchone()
                if prior:
                    if not refresh_aisa or not producer_key.startswith('aisa:'):
                        return {'id':prior['thread_id'],'duplicate':True}
                    thread_id=prior['thread_id']
                    previous=self._row(db,thread_id)
                    latest=db.execute("SELECT data FROM entries WHERE thread_id=? AND entry_type='evidence' ORDER BY rowid DESC LIMIT 1",(thread_id,)).fetchone()
                    latest_entry=json.loads(latest['data']) if latest else {}
                    old_evidence=latest_entry.get('evidence',[])
                    if old_evidence and snapshots and old_evidence[0].get('hash')==snapshots[0].get('hash'):
                        return {'id':thread_id,'duplicate':True,'updated':False}
                    # 同原始ID刷新指标，保留用户分类/核对/归档/重要度/手动热度等状态。
                    # 「标题/摘要还是不是自动内容」只能跟**上一轮自动生成的值**比：最新条目的 after
                    # 存的是合并后的当前值，人工标题一旦在第一次刷新后被写进 after，第二次刷新
                    # 就会把 previous==after 误判成自动内容而覆盖（复核 M1）。
                    # 旧数据没有 auto_fields（升级前的线索）：不能拿最新条目的 after 当自动基准——
                    # 那个 after 里已经混进人工改过的值，第一次刷新就会把人工内容盖掉（复核 M1·c）。
                    auto=latest_entry.get('auto_fields')
                    if auto is None:
                        auto=self._legacy_auto_baseline(db,thread_id)
                    incoming_title=payload['title'] if previous['title']==auto.get('title') else previous['title']
                    incoming_summary=payload['summary'] if previous['summary']==auto.get('summary') else previous['summary']
                    # auto_fields 只写「这一轮真的采纳了的自动值」当下轮基准。判成人工的字段必须
                    # 留在基准外，否则下一轮就会拿人工值当自动基准，把它盖掉。
                    auto_generated={name:payload.get(name,'') for name in ('title','summary')
                                    if auto.get(name) is not None and previous[name]==auto[name]}
                    payload={**{k:previous[k] for k in FIELDS if k in previous},
                             'id':thread_id,'revision':previous['revision'],
                             'title':incoming_title,'summary':incoming_summary,
                             'reason':'重复采集刷新来源指标','note':payload['summary']}
                    if previous.get('tags_source')=='suggested':payload['tags']=None
                    producer_key=None  # 原有唯一来源映射保留在首次entry，新版本不占第二个映射。
            row=db.execute('SELECT id FROM threads WHERE id=?',(thread_id,)).fetchone();before=self._row(db,thread_id) if row else None
            if payload.get('id') and not before:raise ValueError('线索不存在')
            if before and (type(payload.get('revision')) is not int or payload['revision']!=before['revision']):raise EventConflict('线索已有新内容，请刷新后再保存')
            # 人工冻结的 heat 只有两条路能改：显式 manual_heat（公开 save），
            # 或 payload 自带 heat_source=='manual'。**「没给 heat_source」不等于「人工」** ——
            # 早先这里用 payload.get('heat_source','manual')!='manual' 判断，
            # 于是只带 heat 的内部写入会被当成人工改动而放行，把 manual 线索的 heat 改掉、
            # heat_source 却还挂着 manual（22h-2 L1）。
            wants_manual=manual_heat or payload.get('heat_source')=='manual'
            if before and (before.get('heat_source')=='manual' and not wants_manual
                           or snapshots and before.get('heat_source')=='site' and payload.get('heat_source')=='computed'):
                payload={**payload,'heat':before.get('heat',''),'heat_source':before['heat_source']}
            fields=self._fields(payload,before)
            if linked_event: fields['event_id']=identity(linked_event,'事项编号')
            reason=text(payload.get('reason',''),'更新原因',2000)
            note=text(payload.get('note',fields['summary'] if snapshots else reason),'本次变化',4000)
            if not before and not snapshots:raise ValueError('新线索至少需要一份可追溯的原文依据')
            if before and not snapshots and not reason:raise ValueError('调整分类或状态请填写原因')
            if len(snapshots)>20 or sum(len(s.get('excerpt','')) for s in snapshots)>12000:raise ValueError('一次最多保留 20 个片段、合计 12,000 字')
            before_fields=self._fields({},before) if before else None
            if before and not snapshots and fields==before_fields:return {'id':thread_id,'revision':before['revision'],'unchanged':True}
            stamp=now();revision=before['revision']+1 if before else 1
            old=before_fields
            db.execute('INSERT INTO threads VALUES(?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data,revision=excluded.revision,updated_at=excluded.updated_at',
                       (thread_id,json.dumps(fields,ensure_ascii=False),revision,0,before['created_at'] if before else stamp,stamp))
            clocks=[source_clock(s) for s in snapshots];known=[c for c in clocks if c[0]]
            # 时间已知按来源时钟语义归类；未知来源也参与，不能伪称单一来源。
            clock_sources={c[1] if c[0] else ('unknown',s.get('kind'),s.get('source'))
                           for s,c in zip(snapshots,clocks)}
            clock=occurred or (max(known,key=lambda c:datetime.fromisoformat(c[0]).timestamp())[0] if known else '')
            # 单一来源时说清「这个时间到底是什么」：xradar 是观测槽时间，
            # 不是话题首发时间（22h-2 M3：source_clock 早就返回了专属 label，
            # 但这里只取 c[0]、label 一直没落到用户可见处）。
            # 多来源沿用通用文案：混了不同来源的时钟，说哪一种都不准确。
            if occurred:
                clock_label='人工核对的发生时间'
            elif known and len(clock_sources)==1:
                clock_label=max(known,key=lambda c:datetime.fromisoformat(c[0]).timestamp())[1]
            elif known:
                clock_label='本次依据时间（最近）'
            else:
                clock_label='来源未提供发布时间' if len(snapshots)==1 and snapshots[0].get('kind')=='aisa' else '发生时间未知'
            local=datetime.fromisoformat(clock).astimezone(ZONE) if clock else None
            entry_type='evidence' if snapshots else 'edit'
            entry={'revision':revision,'before':old,'after':fields,'note':note,'reason':reason,'evidence':snapshots,'clock_label':clock_label,'occurred_at':occurred}
            # 空字典也要写：它标记「这一条是采集链路写的、已按新规则判过基准」，
            # 下次刷新就不会再退回旧数据保守判定，人工字段继续受保护。
            if auto_generated is not None and snapshots:entry['auto_fields']=auto_generated
            db.execute('INSERT INTO entries VALUES(?,?,?,?,?,?,?,?,?)',(uuid.uuid4().hex,thread_id,json.dumps(entry,ensure_ascii=False),stamp,clock,str(local.year) if local else 'unknown',f'{local.month:02}' if local else '',entry_type,producer_key))
            if snapshots:
                for item in observations or []:self._observe(db,thread_id,item)
        result={'id':thread_id,'revision':revision,'duplicate':False}
        if refresh_aisa:result.update(duplicate=bool(before),updated=bool(before))
        return result

    @staticmethod
    def _collector_entry(entry):
        """这条历史是不是采集链路自己写的。

        人工保存只会挂知识库快照（document/wechat/xradar…）或不挂快照，不会挂 aisa 快照；
        所以「带快照且快照全是采集来源」以外的更新条目，都算人工编辑记录。不区分来源平台：
        任何非采集链路写入的条目都按人工处理。
        """
        evidence=entry.get('evidence') or []
        return bool(evidence) and all(e.get('kind')=='aisa' for e in evidence)

    def _legacy_auto_baseline(self,db,thread_id):
        """旧线索（历史里没有 auto_fields）的自动基准：认不出来就当人工维护。

        复核 M1·c：旧版第一次升级刷新会把人工摘要当自动值覆盖，因为它拿最新条目的 after
        当基准，而那个 after 已经是「合并后的当前值」，里面就是人工内容。这里改成保守回落，
        一个字段只有同时满足两条才认它是自动值：
        1 该线索历史上没有任何人工编辑记录改过这个字段（edit 历史 / 非采集来源的更新条目）；
        2 当前值能跟一份可靠还原的自动生成值对上：标题＝最近一条采集条目快照里的自动标题，
          摘要＝最近一条采集条目里由采集链路写入的摘要（在没有人工改动记录的前提下它必然是自动值）。
        任一条不满足就不写进基准，该字段按人工维护保留、不覆盖；指标数字照常刷新。
        宁可少刷新一次文案，也不能盖掉人工内容。
        """
        history=[json.loads(r['data']) for r in db.execute('SELECT data FROM entries WHERE thread_id=? ORDER BY rowid ASC',(thread_id,))]
        captured=[e for e in history if self._collector_entry(e)]
        baseline={}
        for name in ('title','summary'):
            if any(not self._collector_entry(e) and e.get('before')
                   and e['before'].get(name)!=(e.get('after') or {}).get(name) for e in history):
                continue  # 有人工改过这个字段的记录：认人工，不覆盖
            value=self._recoverable_auto_value(name,captured)
            if value:baseline[name]=value
        return baseline

    def _recoverable_auto_value(self,name,captured):
        """能从历史里可靠还原的自动生成值；还原不出来返回空串（就当人工，不覆盖）。"""
        if not captured:
            return ''   # 连一条采集链路写的条目都没有：还原不出自动值，按人工保留
        if name=='title':
            # 快照里的 title 是采集链路写入的自动标题，取最近一条采集快照的自动标题。
            return captured[-1]['evidence'][0].get('title','')
        for entry in reversed(captured):  # 摘要没有独立快照字段，取最近一条采集条目里写入的值
            if (entry.get('after') or {}).get('summary'):
                return entry['after']['summary']
        return ''

    def ingest_aisa(self,source,item):
        # 正常_save仍负责事务/版本/证据/人工状态保护，仅为AIsa精确ID开刷新语义。
        item=source._scrub(item)
        candidate=source.to_clue_item(item)
        platform=item['platform']
        excerpt=(item.get('text') or candidate['summary'])[:4000]
        snapshot={'kind':'aisa','source':'aisa','title':candidate['title'],
                  'hash':digest(item),'start':0,'end':len(excerpt),'excerpt':excerpt,
                  'captured_at':now(),'origin_url':item.get('url',''),
                  'provenance':{'platform':platform,'original_id':source._item_id(item,platform),
                                'published_at':item.get('created_at',''),'marker':candidate['provenance'],
                                'link_label':item.get('link_label',''),'deeplink':item.get('deeplink',''),
                                'metrics':{k:item[k] for k in ('views','likes','retweets','replies','hot_value','heat_score','score_text') if k in item}}}
        payload={k:v for k,v in candidate.items() if k not in ('producer_key','provenance')}
        # 一次采集只有分数，不能据此推断“上升/下降”趋势，保留人工趋势选择。
        payload.update(heat='',heat_source='',reason='手动采集AIsa来源',note=candidate['summary'])
        return self._save(source._scrub(payload),[source._scrub(snapshot)],candidate['producer_key'],refresh_aisa=True)

    @staticmethod
    def _observe(db,thread_id,item):
        from clues_heat import bucket_time, SITE_HEAT
        bucket_time(item.get('bucket'))
        source=item.get('source')
        if source not in ('xradar','github','wechat','run','manual'):raise ValueError('观测来源无效')
        if source=='xradar' and len(item['bucket'])!=13:raise ValueError('xradar 必须是6h槽')
        for key in ('mentions','talkers'):
            if type(item.get(key)) is not int or item[key]<0:raise ValueError('观测计数无效')
        if item.get('views') is not None and (type(item['views']) is not int or item['views']<0):raise ValueError('观测曝光无效')
        if item.get('site_state','') not in {'',*SITE_HEAT}:raise ValueError('站方状态无效')
        date=datetime.fromisoformat(item.get('captured_at','').replace('Z','+00:00'))
        if date.utcoffset() is None:raise ValueError('观测抓取时间无效')
        for name in ('bars_recent6','bars_prev6','ratio6_site'):
            value=item.get(name)
            if value is not None and (type(value) not in (int,float) or not math.isfinite(value) or value<0):raise ValueError('站内特征无效')
        names=('source','bucket','mentions','talkers','views','site_state','captured_at','bars_recent6','bars_prev6','ratio6_site')
        values=[item.get(k,'' if k=='site_state' else None) for k in names]
        db.execute('INSERT INTO observations VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(thread_id,source,bucket) DO UPDATE SET mentions=excluded.mentions,talkers=excluded.talkers,views=excluded.views,site_state=excluded.site_state,captured_at=excluded.captured_at,bars_recent6=excluded.bars_recent6,bars_prev6=excluded.bars_prev6,ratio6_site=excluded.ratio6_site',[thread_id,*values])

    def observations(self,thread_id):
        with self.db() as db:return [dict(r) for r in db.execute('SELECT * FROM observations WHERE thread_id=? ORDER BY bucket,source',(thread_id,))]

    def seen(self,thread_id,revision):
        if type(revision) is not int or revision<1:raise ValueError('已读版本无效')
        with self.db() as db:
            item=self._row(db,thread_id)
            if revision>item['revision']:raise ValueError('已读版本超出当前版本')
            db.execute('UPDATE threads SET seen_revision=MAX(seen_revision,?) WHERE id=?',(revision,thread_id))
        return {'ok':True}

    def listing(self,query='',category='',view='all',source='',year='',offset=0,limit=30,tag='',sort='updated',period='',priority='',heat=''):
        query=text(query,'检索词',200).casefold();source=text(source,'来源',200)
        if tag and tag not in TECH_TAGS:raise ValueError('技术标签无效')
        if sort not in ('updated','source_desc','source_asc','priority'):raise ValueError('排序无效')
        if period not in ('','today','yesterday','3d','7d','30d'):raise ValueError('时间范围无效')
        if heat not in ('','rising','peak','fading'):raise ValueError('heat 无效')
        if priority not in ('','important','normal'):raise ValueError('重要程度无效')
        if category and category not in CATEGORIES:raise ValueError('分类无效')
        if view not in ('all','latest','tracking','discussion','year','unread'):raise ValueError('视图无效')
        if year and year!='unknown' and (not year.isdigit() or len(year)!=4):raise ValueError('年份无效')
        try:offset=int(offset);limit=int(limit)
        except (ValueError,TypeError):raise ValueError('分页无效') from None
        if offset<0 or not 1<=limit<=100:raise ValueError('分页范围无效')
        with self.db() as db:
            # SQLite JSON is stored verbatim so history and frozen excerpts remain searchable.
            where=['1'];args=[]
            if query:
                needle='%'+query.replace('\\','\\\\').replace('%','\\%').replace('_','\\_')+'%'
                where.append("(fold(t.data) LIKE ? ESCAPE '\\' OR EXISTS(SELECT 1 FROM entries x WHERE x.thread_id=t.id AND fold(x.data) LIKE ? ESCAPE '\\'))");args.extend([needle,needle])
            if source:
                where.append("EXISTS(SELECT 1 FROM entries x,json_each(x.data,'$.evidence') e WHERE x.thread_id=t.id AND json_extract(e.value,'$.source')=?)");args.append(source)
            if year:
                where.append("EXISTS(SELECT 1 FROM entries x WHERE x.thread_id=t.id AND x.entry_type='evidence' AND x.year=?)");args.append(year)
            source_time="(SELECT x.clock FROM entries x WHERE x.thread_id=t.id AND x.entry_type='evidence' ORDER BY x.rowid DESC LIMIT 1)"
            if priority:where.append("json_extract(t.data,'$.priority')=?");args.append(priority)
            if period:
                from datetime import timedelta
                stamp=datetime.now(ZONE)
                midnight=stamp.replace(hour=0,minute=0,second=0,microsecond=0)
                if period=='yesterday':
                    # 昨天是一个完整的日子，必须给出上界：只写下界的话，
                    # 今天已经发生的原文也会被算进昨天。含下界、不含上界。
                    where.append(f'julianday({source_time})>=julianday(?) AND julianday({source_time})<julianday(?)')
                    args.extend([(midnight-timedelta(days=1)).isoformat(),midnight.isoformat()])
                elif period=='today':
                    where.append(f'julianday({source_time})>=julianday(?)');args.append(midnight.isoformat())
                else:
                    # 3d / 7d / 30d 沿用原有的滚动切点（此刻往前推 N 天），不改口径
                    where.append(f'julianday({source_time})>=julianday(?)');args.append((stamp-timedelta(days=int(period[:-1]))).isoformat())
            common=' AND '.join(where)
            counts={c:db.execute("SELECT COUNT(*) FROM threads t WHERE "+common+" AND EXISTS(SELECT 1 FROM json_each(t.data,'$.categories') WHERE value=?)",[*args,c]).fetchone()[0] for c in CATEGORIES}
            if category:where.append("EXISTS(SELECT 1 FROM json_each(t.data,'$.categories') WHERE value=?)");args.append(category)
            if view=='tracking':where.append("json_extract(t.data,'$.tracking')=1")
            if view=='discussion':where.append("json_extract(t.data,'$.kind')='discussion'")
            if view=='unread':where.append('t.revision>t.seen_revision')
            if view!='all':where.append("json_extract(t.data,'$.status')!='archived'")
            facets=' AND '.join(where)
            tag_counts={r['tag']:r['n'] for r in db.execute('SELECT j.value tag,COUNT(*) n FROM threads t,json_each(tech_tags(t.data)) j WHERE '+facets+' GROUP BY j.value',args)}
            heat_counts={h:db.execute("SELECT COUNT(*) FROM threads t WHERE "+facets+" AND json_extract(t.data,'$.heat')=?",[*args,h]).fetchone()[0] for h in ('rising','peak','fading')}
            if heat:where.append("json_extract(t.data,'$.heat')=?");args.append(heat)
            if tag:where.append('EXISTS(SELECT 1 FROM json_each(tech_tags(t.data)) WHERE value=?)');args.append(tag)
            clause=' AND '.join(where)
            total=db.execute('SELECT COUNT(*) FROM threads t WHERE '+clause,args).fetchone()[0]
            chronological=f'(julianday({source_time}) IS NULL),julianday({source_time}) '+('ASC' if sort=='source_asc' else 'DESC')+',t.updated_at DESC,t.id'
            ordering={'updated':'t.updated_at DESC,t.id','source_desc':chronological,'source_asc':chronological,'priority':"CASE WHEN json_extract(t.data,'$.priority')='important' THEN 0 ELSE 1 END,"+chronological}[sort]
            rows=db.execute('SELECT t.id FROM threads t WHERE '+clause+' ORDER BY '+ordering+' LIMIT ? OFFSET ?',[*args,limit,offset]).fetchall()
            items=[]
            for row in rows:
                item=self._row(db,row['id'])
                entry=db.execute("SELECT data,clock,created_at FROM entries WHERE thread_id=? AND entry_type='evidence' ORDER BY rowid DESC LIMIT 1",(row['id'],)).fetchone()
                item['source_time']=entry['clock'] if entry else ''
                data=json.loads(entry['data']) if entry else {}
                item['source_time_label']=data.get('clock_label','')
                item['source_platforms']=list(dict.fromkeys(e.get('provenance',{}).get('platform') for e in data.get('evidence',[]) if e.get('kind')=='aisa'))
                item['latest_note']=json.loads(entry['data'])['note'] if entry else ''
                sources=db.execute("SELECT DISTINCT json_extract(e.value,'$.source') source FROM entries x,json_each(x.data,'$.evidence') e WHERE x.thread_id=?",(row['id'],)).fetchall()
                item['sources']=[r['source'] for r in sources];items.append(item)
            timeline=[]
            if view=='year':
                timewhere=[clause,"x.thread_id=t.id","x.entry_type='evidence'"];timeargs=list(args)
                if year:timewhere.append('x.year=?');timeargs.append(year)
                if source:timewhere.append("EXISTS(SELECT 1 FROM json_each(x.data,'$.evidence') e WHERE json_extract(e.value,'$.source')=?)");timeargs.append(source)
                timeline_total=db.execute('SELECT COUNT(*) FROM entries x,threads t WHERE '+' AND '.join(timewhere),timeargs).fetchone()[0]
                records=db.execute('SELECT x.*,t.data thread FROM entries x,threads t WHERE '+' AND '.join(timewhere)+" ORDER BY (x.clock=''),x.clock DESC,x.created_at DESC,x.id LIMIT ? OFFSET ?",[*timeargs,limit,offset]).fetchall()
                timeline=[{**dict(r),'data':json.loads(r['data']),'thread':json.loads(r['thread'])} for r in records];total=timeline_total
            years=[r[0] for r in db.execute("SELECT DISTINCT year FROM entries WHERE entry_type='evidence' ORDER BY year DESC")]
            sources=[r[0] for r in db.execute("SELECT DISTINCT json_extract(e.value,'$.source') FROM entries x,json_each(x.data,'$.evidence') e ORDER BY 1")]
        return {'items':items,'timeline':timeline,'total':total,'next_offset':offset+limit if offset+limit<total else None,'categories':CATEGORIES,'counts':counts,'years':years,'sources':sources,'view':view,'heat_counts':heat_counts,'heat_names':{'rising':'上升中','peak':'见顶','fading':'退烧'},'tag_names':TECH_TAGS,'tag_counts':tag_counts,'sort':'source_desc' if view=='year' else sort}

    @serialized
    def import_event(self,event_id):
        event=self.store.events.event(event_id)
        snapshots=[]
        for s in event['evidence']:
            e={k:v for k,v in s.items() if k not in ('state','id')};e['origin_url']=f"/knowledge.html?document={e['document_id']}&start={e['start']}&end={e['end']}&hash={e['hash']}";snapshots.append(e)
        producer=f"event:{event_id}:{event['revision']}"
        with self.db() as db:existing=db.execute("SELECT id FROM threads WHERE json_extract(data,'$.event_id')=?",(event_id,)).fetchone()
        payload={'title':event['title'],'summary':event['summary'],'relevance':'由已有事件导入，保留原有核对状态；分类待整理','categories':['other'],'priority':event['priority'],'status':'candidate' if event['needs_confirmation'] else 'verified','reason':'导入既有事件，不确认行动归属','note':event['summary']}
        if existing:
            current=self.detail(existing['id']);payload.update(id=current['id'],revision=current['revision'],categories=current['categories'],relevance=current['relevance'])
        return self._save(payload,snapshots,producer,linked_event=event_id)

    @serialized
    def from_run(self,run_id):
        run=self.store.schedules.get_run(run_id)
        if run['snapshot'].get('kind')=='github_clues':raise ValueError('GitHub 任务通过原文引用直接生成线索，请查看本次运行里的线索链接')
        if run['status']!='succeeded':raise ValueError('只有真实成功的运行结果可以入线索中心；失败请查看运行记录')
        if not run['output'].strip():return {'skipped':True,'reason':'任务没有输出'}
        if len(run['output'])>12000:raise ValueError('输出超过 12,000 字，请先人工选择原文片段')
        task=run['snapshot'];fingerprint=digest({'task_id':run['task_id'],'body':task['body'],'kind':task['kind'],'output':run['output']})
        producer='run:'+fingerprint
        evidence={'kind':'task_run','source':'scheduled-tasks','title':task['name'],'run_id':run_id,'hash':digest({k:run[k] for k in ('id','snapshot','status','output','started_at','finished_at','exit_code')}),'start':0,'end':len(run['output']),'excerpt':run['output'],'modified':run['finished_at'],'captured_at':now(),'provenance':{'task_id':run['task_id'],'task_revision':task['revision'],'execution_status':run['status'],'exit_code':run['exit_code']},'origin_url':'/schedules.html?run='+run_id}
        payload={'title':task['name'],'summary':run['output'][:4000],'relevance':'定时任务结果，尚未核对业务原文；执行成功不等于内容已证实','categories':[task.get('result_category','other')],'status':'candidate','reason':'成功运行结果写入工作台，不发送飞书','note':run['output'][:4000]}
        thread_id=task.get('clue_id')
        if thread_id:
            current=self.detail(thread_id);payload.update(id=thread_id,revision=current['revision'],title=current['title'],categories=current['categories'],relevance=current['relevance'],tracking=current['tracking'],kind=current['kind'],status='archived' if current['status']=='archived' else 'candidate')
        else:
            with self.db() as db:existing=db.execute("SELECT thread_id FROM entries WHERE producer_key LIKE ? ORDER BY rowid LIMIT 1",('task:'+run['task_id']+':%',)).fetchone()
            if existing:
                current=self.detail(existing['thread_id']);payload.update(id=current['id'],revision=current['revision'],title=current['title'],categories=current['categories'],relevance=current['relevance'],tracking=current['tracking'],kind=current['kind'],status='archived' if current['status']=='archived' else 'candidate')
        result=self._save(payload,[evidence],producer)
        # An explicit producer association groups only this same task's future outputs.
        with self.db() as db:
            db.execute('INSERT OR IGNORE INTO entries VALUES(?,?,?,?,?,?,?,?,?)',(uuid.uuid4().hex,result['id'],json.dumps({'note':'绑定此任务后续结果','evidence':[]},ensure_ascii=False),now(),'','unknown','','association','task:'+run['task_id']+':'+result['id']))
        return result

    @serialized
    def promote(self,thread_id,revision):
        current=self.detail(thread_id)
        if current['revision']!=revision:raise EventConflict('线索已更新，请重新核对后建立事项')
        if current.get('event_id'):return {'event_id':current['event_id'],'existing':True}
        if current['entries_total']>50:raise ValueError('这条线索历史较长，请在事项页面明确选择所需依据')
        picks=[];seen=set();size=0
        fresh_documents={e['document_id'] for entry in current['entries'] for e in entry.get('evidence',[]) if e.get('document_id') and e['state']=='fresh'}
        for entry in current['entries']:
            for e in entry.get('evidence',[]):
                if not e.get('document_id'):continue
                key=(e['document_id'],e['hash'],e['start'],e['end'])
                if key in seen:continue
                if e['state']!='fresh':
                    if e['document_id'] in fresh_documents:continue
                    raise EventConflict('已有原文发生变化，请补充当前版本的依据后再建立事项')
                if len(picks)>=20 or size+len(e['excerpt'])>12000:raise ValueError('依据超过事项上限，请在事项页面选择需要的片段')
                picks.append({'id':key[0],'hash':key[1],'start':key[2],'end':key[3]});seen.add(key);size+=len(e['excerpt'])
        if not picks:raise ValueError('任务输出不能替代业务原文；请先追加知识库原文依据')
        event_id=hashlib.sha256(('clue-event:'+thread_id).encode()).hexdigest()[:32]
        try:self.store.events.event(event_id)
        except ValueError:self.store.events.save_event({'revision':0,'title':current['title'],'summary':current['summary'],'priority':current['priority'],'needs_confirmation':True,'picks':picks,'reason':'从线索建立待确认事项；未接受待办或安排日程'},creation_id=event_id)
        self._save({'id':thread_id,'revision':revision,'reason':'建立待确认事项，保留线索与事项关联'},[],linked_event=event_id)
        return {'event_id':event_id,'needs_confirmation':True}
