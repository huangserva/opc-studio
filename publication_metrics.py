"""User-imported publication statistics; no remote requests or derived claims.

Each immutable snapshot binds an existing published delivery and its saved version.
CSV evidence stays in the local database; only selected metrics enter the knowledge index.
"""
from contextlib import closing, contextmanager
import csv
from datetime import datetime, timezone
import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import threading
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from publishing.registry import PLATFORMS

DOMAINS = {'x': ('x.com', 'twitter.com'), 'wechat': ('mp.weixin.qq.com',),
           'douyin': ('douyin.com',), 'xiaohongshu': ('xiaohongshu.com', 'xhslink.com'),
           'channels': ('weixin.qq.com', 'weixinbridge.com')}
PUBLISHED = {'published_manual', 'published_browser'}
MAX_CSV = 256000
SOURCE = 'publication_feedback'
NOTICE = '用户导入，未向平台校验；指标含义和统计时间窗口不同，不能直接比较。登记时间不是实际发布时间。'


def now():
    return datetime.now(timezone.utc).isoformat()


def text(value, label, maximum, required=False):
    if not isinstance(value, str) or '\x00' in value or len(value)>maximum or required and not value.strip():
        raise ValueError(f'{label}须为文字，最多 {maximum} 字' + ('，不能为空' if required else ''))
    return value.strip()


def identity(value):
    if not isinstance(value,str) or not re.fullmatch('[a-f0-9]{32}',value):
        raise ValueError('记录编号无效')
    return value


def slug_value(value):
    if not isinstance(value,str) or not re.fullmatch('[a-zA-Z0-9][a-zA-Z0-9_-]{0,79}',value):
        raise ValueError('作品编号无效')
    return value


def timestamp(value, label):
    if not isinstance(value,str) or len(value)>50 or not re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d{1,6})?)?(?:Z|[+-]\d{2}:\d{2})',value):
        raise ValueError(f'{label}须为含时区的 ISO 日期时间')
    try:
        parsed=datetime.fromisoformat(value.replace('Z','+00:00'))
    except ValueError:
        raise ValueError(f'{label}无效') from None
    return parsed.isoformat(),parsed


def platform_url(value, platform, required=True):
    if not value and not required:
        return ''
    value=text(value,'来源链接',2048,True)
    if re.search(r'[\s\x00-\x1f\\]',value):
        raise ValueError('来源链接格式无效')
    try:
        parsed=urlsplit(value)
        valid=parsed.scheme=='https' and parsed.hostname and not parsed.username and not parsed.password and parsed.port in (None,443)
    except ValueError:
        valid=False
    if not valid or not any(parsed.hostname==d or parsed.hostname.endswith('.'+d) for d in DOMAINS[platform]):
        raise ValueError('来源链接须为所选平台的 HTTPS 页面，不得包含账号密码')
    if any(re.search(r'^(?:access_token|oauth_token|authorization|api_key|apikey|token|session|sessionid|password|code)$',k,re.I) for k,_ in parse_qsl(parsed.query)):
        raise ValueError('来源链接含登录参数，请移除这些参数后导入')
    return value


def normalized_url(value, platform):
    parts=urlsplit(platform_url(value,platform))
    host=parts.hostname.lower()
    if platform=='x' and host in ('x.com','www.x.com','twitter.com','www.twitter.com'):
        host='x.com'
    return urlunsplit(('https',host,parts.path.rstrip('/') or '/',urlencode(sorted(parse_qsl(parts.query,keep_blank_values=True))),''))


def payload_object(value, fields, label):
    if not isinstance(value,dict) or set(value)-set(fields):
        raise ValueError(label+'参数无效或包含未知字段')


class PublicationMetrics:
    def __init__(self,store):
        self.store=store
        self.lock=threading.RLock()
        self._observations = None
        self._reviews = None
        self.root=Path(store.workspace)/'publication-feedback'
        self.root.mkdir(parents=True,exist_ok=True,mode=0o700)
        os.chmod(self.root,0o700)
        self.path=self.root/'snapshots.db'
        with closing(sqlite3.connect(self.path,timeout=30)) as c:
            with c:
                c.execute('PRAGMA journal_mode=WAL')
                c.execute('''CREATE TABLE IF NOT EXISTS snapshots(
                    id TEXT PRIMARY KEY, slug TEXT NOT NULL, delivery_id TEXT NOT NULL,
                    platform TEXT NOT NULL, version TEXT NOT NULL, collected_at TEXT NOT NULL,
                    created_at TEXT NOT NULL, fingerprint TEXT NOT NULL UNIQUE, data TEXT NOT NULL,
                    knowledge_id TEXT
                )''')
                c.execute('CREATE INDEX IF NOT EXISTS snapshots_delivery ON snapshots(slug,delivery_id,collected_at)')
        self._secure()

    def _secure(self):
        for suffix in ('','-wal','-shm'):
            path=Path(str(self.path)+suffix)
            if path.exists():os.chmod(path,0o600)

    @property
    def observations(self):
        with self.lock:
            if self._observations is None:
                from publication_observations import PublicationObservations
                self._observations = PublicationObservations(self)
            return self._observations

    @property
    def reviews(self):
        # The review view is opened on purpose: loading the feedback page must not
        # read every work or reach any remote service.
        with self.lock:
            if self._reviews is None:
                from publication_reviews import PublicationReviews
                self._reviews = PublicationReviews(self)
            return self._reviews

    @contextmanager
    def db(self,write=False):
        c=sqlite3.connect(self.path,timeout=30,isolation_level=None)
        c.row_factory=sqlite3.Row
        if not write:c.execute('PRAGMA query_only=ON')
        try:
            c.execute('BEGIN IMMEDIATE' if write else 'BEGIN')
            yield c
            c.commit()
        except Exception:
            c.rollback();raise
        finally:
            c.close();self._secure()

    @staticmethod
    def capabilities():
        from publication_reviews import METRIC_FIELDS
        return {'platforms':PLATFORMS,'max_metrics':40,'max_csv_chars':MAX_CSV,'max_csv_rows':1000,
                'range_kinds':['interval','cumulative'],'platform_verified':False,'notice':NOTICE,
                'metric_fields':METRIC_FIELDS}

    def _binding(self,slug,delivery_id,platform=None,version=None):
        slug_value(slug);identity(delivery_id)
        with self.store.lock:
            records=self.store.delivery_records(slug)
            record=next((r for r in records if r.get('id')==delivery_id),None)
            if not record or record.get('kind') not in PUBLISHED or record.get('platform') not in PLATFORMS:
                raise ValueError('统计必须绑定已经登记的发布项；导出记录不能作为已发布项')
            if platform is not None and record['platform']!=platform:
                raise ValueError('平台与发布项不一致')
            if version is not None and record['version']!=version:
                raise ValueError('作品版本与发布项不一致，不能改绑当前版本')
            manifest,revision,_=self.store.info(slug,record['version'])
            if revision.get('id')!=record['version']:
                raise ValueError('发布项的固定版本不存在或不匹配')
            url=platform_url(record.get('url',''),record['platform'])
            return {'slug':slug,'delivery_id':record['id'],'platform':record['platform'],'version':record['version'],
                    'title':manifest['title'],'url':url,'account':str(record.get('account',''))[:200],
                    'registration_at':record.get('created_at',''),'delivery_kind':record['kind'],
                    'work_url':'/?'+urlencode({'article':slug,'version':record['version']})}

    def bindings(self):
        bindings=[];unavailable=0
        # Do not call Store.listing(): a feedback view must not initiate intake imports.
        with self.store.lock:
            for folder in sorted(self.store.library.iterdir()):
                if not (folder/'article.json').is_file():continue
                for record in self.store.delivery_records(folder.name):
                    if record.get('kind') not in PUBLISHED:continue
                    try:bindings.append(self._binding(folder.name,record.get('id')))
                    except (ValueError,OSError,KeyError):unavailable+=1
        return {'bindings':bindings,'unavailable':unavailable,'capabilities':self.capabilities()}

    def import_snapshot(self,payload):
        payload_object(payload,('slug','delivery_id','platform','version','collected_at','range','metrics','provenance'),'统计快照')
        platform=payload.get('platform')
        if not isinstance(platform,str) or platform not in PLATFORMS:raise ValueError('请选择平台')
        binding=self._binding(payload.get('slug'),payload.get('delivery_id'),platform,payload.get('version'))
        if payload.get('version') is None:raise ValueError('请提供发布项的固定版本')
        collected,when=timestamp(payload.get('collected_at'),'采集时间')
        if when>datetime.now(timezone.utc):raise ValueError('采集时间不能晚于当前时间')
        window=payload.get('range')
        payload_object(window,('kind','start','end','label'),'统计范围')
        if window.get('kind') not in ('interval','cumulative'):raise ValueError('统计范围须为 interval 或 cumulative')
        end,end_time=timestamp(window.get('end'),'统计截止时间')
        start=''
        if 'start' in window and not isinstance(window['start'],str):raise ValueError('统计开始时间须为含时区的日期时间或空文字')
        if window.get('start'):
            start,start_time=timestamp(window['start'],'统计开始时间')
            if start_time>end_time:raise ValueError('统计开始时间不能晚于截止时间')
        elif window['kind']=='interval':raise ValueError('区间统计必须提供开始时间')
        if end_time>when:raise ValueError('统计截止时间不能晚于采集时间')
        window={'kind':window['kind'],'start':start,'end':end,'label':text(window.get('label',''),'统计口径',500,True)}
        origin=payload.get('provenance')
        if not isinstance(origin,dict):raise ValueError('请保留统计页面或原始 CSV 来源')
        if origin.get('type')=='manual':
            payload_object(origin,('type','url','note'),'页面来源')
            origin={'type':'manual','url':platform_url(origin.get('url'),platform),
                    'note':text(origin.get('note',''),'来源说明',1000),'platform_verified':False}
            metrics=self._metrics(payload.get('metrics'))
        elif origin.get('type')=='csv':
            if 'metrics' in payload:raise ValueError('CSV 指标必须来自指定原始行，不能另填替代值')
            origin,metrics=self._csv(origin,binding)
        else:raise ValueError('统计来源须为 manual 或 csv')
        data={'schema_version':1,'binding':binding,'collected_at':collected,'range':window,
              'metrics':metrics,'provenance':origin,'platform_verified':False,'notice':NOTICE}
        encoded=json.dumps(data,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)
        fingerprint=hashlib.sha256(encoded.encode()).hexdigest();ident=fingerprint[:32]
        with self.lock,self.db(write=True) as c:
            existing=c.execute('SELECT id FROM snapshots WHERE fingerprint=?',(fingerprint,)).fetchone()
            if not existing:
                c.execute('INSERT INTO snapshots(id,slug,delivery_id,platform,version,collected_at,created_at,fingerprint,data) VALUES(?,?,?,?,?,?,?,?,?)',
                          (ident,binding['slug'],binding['delivery_id'],platform,binding['version'],collected,now(),fingerprint,encoded))
        return self.snapshot(ident)

    @staticmethod
    def _metrics(metrics):
        if not isinstance(metrics,dict) or not 1<=len(metrics)<=40:raise ValueError('请提供 1–40 个原始指标；缺失值不能补成 0')
        result={}
        for key,value in metrics.items():
            key=text(key,'指标名称',80,True)
            if key in result:raise ValueError('指标名称重复')
            if isinstance(value,bool) or value is None:raise ValueError('原始指标须为数值或原始文字，不能用布尔值或空值')
            if isinstance(value,(int,float)):
                # Net-change metrics may be negative; retain the original value.
                if abs(value)>10**18 or not math.isfinite(value):raise ValueError('指标数值无效')
            elif isinstance(value,str):
                if not value.strip() or len(value)>200 or '\x00' in value:raise ValueError('原始指标文字须为 1–200 字；未提供的指标请不要添加')
            else:raise ValueError('原始指标格式无效')
            result[key]=value
        return result

    def _csv(self,origin,binding):
        payload_object(origin,('type','url','filename','text','row','url_column','metric_columns'),'CSV 来源')
        raw=origin.get('text')
        if not isinstance(raw,str) or not raw or len(raw)>MAX_CSV or '\x00' in raw:raise ValueError('原始 CSV 最多 256,000 字，不能是空文件')
        filename=text(origin.get('filename'),'CSV 文件名',200,True)
        if '/' in filename or '\\' in filename or not filename.lower().endswith('.csv'):raise ValueError('请保留原始 CSV 文件名，不能包含路径')
        selected=origin.get('row')
        if type(selected) is not int or not 1<=selected<=1000:raise ValueError('CSV 行号为 1–1000（不含表头）')
        url_column=text(origin.get('url_column'),'发布链接列',80,True)
        columns=origin.get('metric_columns')
        if not isinstance(columns,list) or not 1<=len(columns)<=40 or any(not isinstance(k,str) or not k.strip() or len(k)>80 for k in columns) or len(set(columns))!=len(columns):raise ValueError('请选择 1–40 个不重复的原始指标列')
        reader=csv.reader(io.StringIO(raw.lstrip('\ufeff'),newline=''),strict=True)
        try:
            headers=next(reader)
            if not headers or len(headers)>100 or any(not h.strip() or len(h)>80 for h in headers) or len(set(headers))!=len(headers):raise ValueError('CSV 表头无效或重复')
            if url_column not in headers or any(k not in headers for k in columns):raise ValueError('CSV 缺少指定链接或指标列')
            chosen=None;count=0;line_start=0;line_end=reader.line_num
            for count,row in enumerate(reader,1):
                before=line_end;line_end=reader.line_num
                if count>1000 or len(row)!=len(headers):raise ValueError('CSV 最多 1,000 行，且每行列数必须与表头一致')
                if count==selected:chosen=dict(zip(headers,row));line_start=before+1;chosen_end=line_end
            if chosen is None:raise ValueError('CSV 不包含指定行')
        except (csv.Error,StopIteration):raise ValueError('原始 CSV 无法解析，请保留完整表头与行内容') from None
        if normalized_url(chosen[url_column],binding['platform'])!=normalized_url(binding['url'],binding['platform']):
            raise ValueError('CSV 指定行的发布链接与已登记发布项不一致，不能按标题猜测关联')
        metrics=self._metrics({k:chosen[k] for k in columns})
        return {'type':'csv','url':platform_url(origin.get('url',''),binding['platform'],required=False),'filename':filename,
                'text':raw,'sha256':hashlib.sha256(raw.encode()).hexdigest(),'row':selected,'url_column':url_column,
                'metric_columns':columns,'selected_row':chosen,'line_start':line_start,'line_end':chosen_end,
                'platform_verified':False},metrics

    @staticmethod
    def _decode(row,full=True):
        data=json.loads(row['data'])
        if not full and data['provenance']['type']=='csv':
            data['provenance'].pop('text',None);data['provenance'].pop('selected_row',None)
        return {**data,'id':row['id'],'created_at':row['created_at'],'hash':row['fingerprint'],
                'knowledge_id':row['knowledge_id'],'url':'/feedback.html?'+urlencode({'snapshot':row['id']})}

    def listing(self,slug='',delivery_id='',limit=50):
        if type(limit) is not int or not 1<=limit<=100:raise ValueError('每次最多读取 100 个统计快照')
        clauses=[];args=[]
        if slug:clauses.append('slug=?');args.append(slug_value(slug))
        if delivery_id:clauses.append('delivery_id=?');args.append(identity(delivery_id))
        where=' WHERE '+' AND '.join(clauses) if clauses else ''
        with self.db() as c:
            total=c.execute('SELECT COUNT(*) FROM snapshots'+where,args).fetchone()[0]
            rows=c.execute('SELECT * FROM snapshots'+where+' ORDER BY julianday(collected_at) DESC,created_at DESC,id LIMIT ?',[*args,limit]).fetchall()
        return {'snapshots':[self._decode(r,False) for r in rows],'total':total}

    def snapshot(self,ident):
        identity(ident)
        with self.db() as c:row=c.execute('SELECT * FROM snapshots WHERE id=?',(ident,)).fetchone()
        if row is None:raise ValueError('统计快照不存在')
        return self._decode(row)

    def export_to_knowledge(self,ident):
        with self.lock:
            snapshot=self.snapshot(ident);binding=snapshot['binding'];origin=snapshot['provenance'];window=snapshot['range']
            body=['# '+binding['title']+' · 发布表现原始快照',NOTICE,
                  '平台：'+PLATFORMS[binding['platform']], '发布项：'+binding['delivery_id'],
                  '作品：'+binding['slug']+' / '+binding['version'],'发布链接：'+binding['url'],
                  '作品版本入口：'+binding['work_url'],'采集时间：'+snapshot['collected_at'],
                  '统计范围：'+window['kind']+' / '+(window['start'] or '起点未提供')+' 至 '+window['end'],
                  '统计口径：'+window['label'],'快照指纹：'+snapshot['hash'],
                  '本机原始快照：'+snapshot['url'],'\n## 原始指标（未推算、未补零）']
            body.extend(key+'：'+str(value) for key,value in snapshot['metrics'].items())
            body.append('\n## 来源')
            if origin['type']=='csv':
                body.extend(['原始 CSV：'+origin['filename'],'原文件 SHA-256：'+origin['sha256'],
                             '数据行：'+str(origin['row'])+'；原文件行 '+str(origin['line_start'])+'–'+str(origin['line_end']),
                             '匹配的发布链接列：'+origin['url_column'],'指标来自列：'+'、'.join(origin['metric_columns'])])
            else:body.append('用户录入来源说明：'+origin['note'])
            if origin['url']:body.append('统计来源页面：'+origin['url'])
            body.append('\n复盘时请先核对本快照与固定作品版本。事实和推断分开；当前数据不证明某种写法导致表现变化。')
            knowledge=self.store.knowledge
            from knowledge_store import identifier
            config={'id':SOURCE,'name':'发布表现与复盘','kind':'manual'}
            with knowledge.db() as c:
                c.execute('INSERT OR IGNORE INTO sources(id,config,status) VALUES(?,?,?)',(SOURCE,json.dumps(config,ensure_ascii=False),'ready'))
                c.execute('CREATE TEMP TABLE seen(id TEXT PRIMARY KEY)')
                knowledge._write(c,SOURCE,ident,'notes',binding['title']+' · '+PLATFORMS[binding['platform']]+' 表现 / '+snapshot['collected_at'],
                                 '\n\n'.join(body),binding['work_url'],snapshot['collected_at'],['发布反馈',binding['platform']],
                                 {'slug':binding['slug'],'version':binding['version'],'source_locator':snapshot['url'],
                                  'sourceUrl':origin['url'] or binding['url'],'publication_snapshot':ident,
                                  'publication_snapshot_hash':snapshot['hash'],'delivery_id':binding['delivery_id'],
                                  'platform':binding['platform'],'platform_verified':False})
                c.execute("UPDATE sources SET status='ready',detail='用户导入的固定版本统计快照；未向平台校验',synced_at=? WHERE id=?",(now(),SOURCE))
            document_id=identifier(SOURCE,ident);doc=knowledge.document(document_id)
            with self.db(write=True) as c:c.execute('UPDATE snapshots SET knowledge_id=? WHERE id=?',(document_id,ident))
            return {'snapshot_id':ident,'document_id':document_id,'hash':doc['hash'],
                    'url':'/knowledge.html?'+urlencode({'document':document_id,'start':0,'end':len(doc['body']),'hash':doc['hash']}),
                    'summary':'原始统计快照已回库；未生成模型复盘，事实与来源可核对'}
