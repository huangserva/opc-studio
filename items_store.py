"""人工收藏素材库。旧知识/回收/卡片库始终只读，路由也不启动旧库写入器。"""
from __future__ import annotations
import hashlib
import json
import logging
import re
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit, unquote

from platform_registry import identity, annotate_items

LOG = logging.getLogger(__name__)
SHORT_SEARCH_SECONDS = 0.25
SHORT_SEARCH_ROWS = 8192
JSON_FIELDS = {'channels':'channels_json', 'sources':'sources_json', 'legacy_refs':'legacy_refs', 'attachments':'attachments_json', 'tags':'tags_json', 'metrics':'metrics_json', 'thread':'thread_json', 'quoted':'quoted_json', 'media':'media_json', 'rich_sources':'rich_sources_json', 'analysis':'analysis_json'}
TEXT_FIELDS = ('kind','source','author_handle','author_name','url','published_at','fetched_at','title','full_text','article_text','incomplete_reason','tier','topic','doable_resource','why_viral','my_angle','outline','status','feedback','used_work_id','parent_id','trashed_at','note','created_at','updated_at')
SUMMARY = 'platform,channels_json,fetched_at,id,kind,source,sources_json,author_handle,author_name,published_at,title,content_complete,tier,topic,status,feedback,tags_json,parent_id,trashed_at,note,created_at,updated_at'


def now():
    return datetime.now(timezone.utc).isoformat()


def workspace(root):
    root = Path(root)
    return root / 'workspace' if (root / 'workspace').is_dir() else root


def canonical_url(value):
    if not isinstance(value, str) or not value:
        return ''
    parts = urlsplit(value)
    if parts.scheme.lower() not in ('http', 'https') or not parts.hostname or parts.username or parts.password:
        return value  # 本地作品的相对路径也保留，前端单独验证链接。
    host = parts.hostname.lower()
    host = 'x.com' if host in ('twitter.com','www.twitter.com','www.x.com','mobile.twitter.com','mobile.x.com') else host
    port = parts.port
    netloc = host + (':' + str(port) if port and (parts.scheme, port) not in [('http',80),('https',443)] else '')
    query = urlencode(sorted((k,v) for k,v in parse_qsl(parts.query, keep_blank_values=True) if not k.lower().startswith('utm_') and k not in ('s','t','fbclid','gclid')))
    return urlunsplit((parts.scheme.lower(),netloc,parts.path.rstrip('/') or '/',query,''))


def canonical_id(item):
    if item.get('kind') == 'card':
        return 'card:' + (item.get('prompt_sha') or hashlib.sha256(str(item.get('full_text','')).encode()).hexdigest())
    url = canonical_url(item.get('url',''))
    parts = urlsplit(url)
    host = (parts.hostname or '').lower()
    path = unquote(parts.path)
    match = re.search(r'/status/(\d+)', path)
    if host == 'x.com' and match:
        return 'x:' + match[1]
    if host == 'mp.weixin.qq.com':
        return 'wechat:' + hashlib.sha256(url.encode()).hexdigest()[:16]
    if host in ('github.com','www.github.com'):
        repo = '/'.join(path.strip('/').split('/')[:2]).removesuffix('.git')
        if '/' in repo:
            return 'github:' + repo.lower()
    if host in ('douyin.com','www.douyin.com') and (match := re.search(r'/video/([^/]+)',path)):
        return 'douyin:' + match[1]
    if host in ('xiaohongshu.com','www.xiaohongshu.com') and (match := re.search(r'/(?:explore|discovery/item)/([^/]+)',path)):
        return 'xhs:' + match[1]
    identifier = item.get('id','')
    if isinstance(identifier,str) and re.fullmatch(r'(?:x|douyin|xhs):[A-Za-z0-9_-]+',identifier):
        return identifier
    if url:
        return 'url:' + hashlib.sha256(url.encode()).hexdigest()[:16]
    old = item.get('legacy_id') or identifier
    if not isinstance(old,str) or not old:
        raise ValueError('素材必须有链接或旧编号')
    return old if old.startswith('kb:') else 'kb:' + old


def decoded(value, fallback):
    if not isinstance(value,str):
        return value if value is not None else fallback
    try:
        return json.loads(value)
    except (ValueError,TypeError):
        return fallback


def encode(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,allow_nan=False,separators=(',',':'))


def union(left, right):
    result = list(left)
    for value in right:
        if value not in result:
            result.append(value)
    return result


class ItemsStore:
    def __init__(self, root):
        self.workspace = workspace(root)
        self.path = self.workspace / 'items/items.db'

    @contextmanager
    def db(self, write=False):
        self.path.parent.mkdir(parents=True,exist_ok=True)
        # sqlite may replace a chmod-readonly database via a writable directory; fail explicitly.
        if write and self.path.exists() and not self.path.stat().st_mode & 0o222:
            raise PermissionError('素材库只读')
        c = sqlite3.connect(self.path,timeout=8)
        c.row_factory=sqlite3.Row
        c.execute('PRAGMA foreign_keys=ON')
        try:
            if not c.execute("SELECT 1 FROM sqlite_master WHERE name='items'").fetchone():
                self._schema(c)
            self._ensure_short_index(c)
            if write:
                c.execute('BEGIN IMMEDIATE')
            yield c
            c.commit()
        except Exception:
            c.rollback()
            raise
        finally:
            c.close()

    @staticmethod
    def _schema(c):
        # IF NOT EXISTS also makes concurrent first requests safe.
        columns = ','.join(f"{k} TEXT NOT NULL DEFAULT ''" for k in TEXT_FIELDS)
        jsons = ','.join(f"{k} TEXT NOT NULL DEFAULT '{'{}' if k in ('metrics_json','quoted_json','analysis_json') else '[]'}'" for k in JSON_FIELDS.values())
        c.executescript(f'''CREATE TABLE IF NOT EXISTS items(id TEXT PRIMARY KEY,platform TEXT NOT NULL DEFAULT 'web',identity_backup_json TEXT NOT NULL DEFAULT '{{}}',{columns},{jsons},author_followers INTEGER,fit_score INTEGER,doable INTEGER,content_complete INTEGER NOT NULL DEFAULT 1);
        CREATE INDEX IF NOT EXISTS items_list ON items(trashed_at,updated_at DESC,id);
        CREATE INDEX IF NOT EXISTS items_kind ON items(kind,trashed_at);
        CREATE INDEX IF NOT EXISTS items_status ON items(status,trashed_at);
        CREATE INDEX IF NOT EXISTS items_parent ON items(parent_id);
        CREATE INDEX IF NOT EXISTS items_title ON items(title);
        CREATE TABLE IF NOT EXISTS item_sources(item_id TEXT REFERENCES items(id) ON DELETE CASCADE,source TEXT,PRIMARY KEY(item_id,source));
        CREATE INDEX IF NOT EXISTS item_sources_lookup ON item_sources(source,item_id);
        CREATE TABLE IF NOT EXISTS item_tags(item_id TEXT REFERENCES items(id) ON DELETE CASCADE,tag TEXT,PRIMARY KEY(item_id,tag));
        CREATE INDEX IF NOT EXISTS item_tags_lookup ON item_tags(tag,item_id);
        CREATE VIRTUAL TABLE IF NOT EXISTS items_fts USING fts5(title,full_text,article_text,author_handle,note,tags_json,content='items',content_rowid='rowid',tokenize='trigram');
        CREATE TRIGGER IF NOT EXISTS items_ai AFTER INSERT ON items BEGIN
        INSERT INTO items_fts(rowid,title,full_text,article_text,author_handle,note,tags_json) VALUES(new.rowid,new.title,new.full_text,new.article_text,new.author_handle,new.note,new.tags_json); END;
        CREATE TRIGGER IF NOT EXISTS items_ad AFTER DELETE ON items BEGIN
        INSERT INTO items_fts(items_fts,rowid,title,full_text,article_text,author_handle,note,tags_json) VALUES('delete',old.rowid,old.title,old.full_text,old.article_text,old.author_handle,old.note,old.tags_json); END;
        CREATE TRIGGER IF NOT EXISTS items_au AFTER UPDATE ON items BEGIN
        INSERT INTO items_fts(items_fts,rowid,title,full_text,article_text,author_handle,note,tags_json) VALUES('delete',old.rowid,old.title,old.full_text,old.article_text,old.author_handle,old.note,old.tags_json);
        INSERT INTO items_fts(rowid,title,full_text,article_text,author_handle,note,tags_json) VALUES(new.rowid,new.title,new.full_text,new.article_text,new.author_handle,new.note,new.tags_json); END;''')

    @staticmethod
    def _short_terms(row):
        text='\n'.join(str(row[k] or '') for k in ('title','full_text','article_text','author_handle','note','tags_json')).casefold()
        return {text[i:i+n] for n in (1,2) for i in range(len(text)-n+1) if not any(ch.isspace() for ch in text[i:i+n])}

    @classmethod
    def _index_short(cls,c,row):
        c.execute('DELETE FROM item_short_terms WHERE item_id=?',(row['id'],))
        c.executemany('INSERT OR IGNORE INTO item_short_terms(term,item_id) VALUES(?,?)',[(t,row['id']) for t in cls._short_terms(row)])

    @classmethod
    def _ensure_short_index(cls,c):
        # Trigram MATCH cannot answer 1/2-character searches. A separate tiny-term
        # lookup keeps those queries indexed too, without LIKE or corpus copies.
        if c.execute("SELECT 1 FROM sqlite_master WHERE name='item_short_terms'").fetchone():return
        c.execute('BEGIN IMMEDIATE')
        c.execute('CREATE TABLE IF NOT EXISTS item_short_terms(term TEXT,item_id TEXT REFERENCES items(id) ON DELETE CASCADE,PRIMARY KEY(term,item_id)) WITHOUT ROWID')
        for row in c.execute('SELECT id,title,full_text,article_text,author_handle,note,tags_json FROM items').fetchall():cls._index_short(c,row)
        c.commit()

    def upsert(self, item):
        if not isinstance(item,dict):
            raise ValueError('素材格式无效')
        for field in ('title','full_text','note','url','parent_id'):
            if field in item and not isinstance(item[field],str):
                raise ValueError(field + ' 必须是文字')
        for field in ('sources','legacy_refs','tags','attachments'):
            if field in item and not isinstance(item[field],list):
                raise ValueError(field + ' 必须是列表')
        for field in ('sources','tags'):
            if any(not isinstance(v,str) for v in item.get(field,[])):
                raise ValueError(field + ' 必须是文字列表')
        identifier = canonical_id(item)
        with self.db(True) as c:
            prior=c.execute('SELECT * FROM items WHERE id=?',(identifier,)).fetchone()
            values=dict(prior) if prior else {'id':identifier,'kind':'article','status':'kept','created_at':item.get('created_at') or now()}
            for key in TEXT_FIELDS:
                if key in ('full_text','article_text') and prior and len(item.get(key) or '') < len(prior[key]):
                    continue
                if key in item and key not in ('note','status','feedback','trashed_at','created_at','updated_at'):
                    v=item[key]
                    values[key]=v if isinstance(v,str) else encode(v) if v is not None else ''
            if not prior:
                for key in ('note','status','feedback','trashed_at'):
                    values[key]=item.get(key) or '' if key != 'status' else item.get(key) or 'kept'
            author=item.get('author') or {}
            for short,long in [('handle','author_handle'),('name','author_name'),('followers','author_followers')]:
                if short in author: values[long]=author[short]
            for key in ('author_followers','fit_score','doable','content_complete'):
                if key in item: values[key]=item[key]
            for key,column in JSON_FIELDS.items():
                if key in item:
                    value=item[key]
                    if key in ('sources','legacy_refs','tags','attachments') and prior:
                        value=union(decoded(prior[column],[]),value)
                    values[column]=encode(value)
            provenance = {**item, 'id':identifier, 'sources':decoded(values.get('sources_json','[]'),[]), 'source':values.get('source','')}
            annotate_items(self.workspace, [provenance])
            values.update(platform=provenance['platform'], channels_json=encode(provenance['channels']))
            values['url']=canonical_url(values.get('url',''))
            # Identical replay is a true no-op, including updated_at, for migration hashes.
            if prior and all(prior[k]==v for k,v in values.items()):
                return self._decode(prior)
            values['updated_at']=item.get('updated_at') or now()
            keys=list(values)
            c.execute(f"INSERT INTO items({','.join(keys)}) VALUES({','.join('?' for _ in keys)}) ON CONFLICT(id) DO UPDATE SET "+','.join(f'{k}=excluded.{k}' for k in keys if k!='id'),tuple(values[k] for k in keys))
            for table,key,column in [('item_sources','source','sources_json'),('item_tags','tag','tags_json')]:
                c.execute(f'DELETE FROM {table} WHERE item_id=?',(identifier,))
                c.executemany(f'INSERT OR IGNORE INTO {table}(item_id,{key}) VALUES(?,?)',[(identifier,v) for v in decoded(values.get(column,'[]'),[])])
            row=c.execute('SELECT * FROM items WHERE id=?',(identifier,)).fetchone()
            self._index_short(c,row)
            return self._decode(row)

    @staticmethod
    def _decode(row):
        item=dict(row)
        for key,column in JSON_FIELDS.items():
            if column in item:item[key]=decoded(item.pop(column),{} if key in ('metrics','quoted','analysis') else [])
        item['author']={k:item.get(v) for k,v in [('handle','author_handle'),('name','author_name'),('followers','author_followers')]}
        for key in ('why_viral','pattern','my_angle','outline','doable_resource'):
            value=item.get(key)
            if isinstance(value,str) and value.lstrip().startswith(('[','{')):
                parsed=decoded(value,None)
                if isinstance(parsed,(dict,list)):item[key]=parsed
        item['why']=item.get('why_viral','')
        item['content_complete']=bool(item.get('content_complete',True))
        if not item.get('rich_sources'):
            item['rich_sources']=[{k:item[k] for k in ('thread','quoted','media') if item.get(k)}]
            if item.get('article_text'):item['rich_sources'].append({'article':item['article_text']})
        item.update(identity(item))
        return item

    def detail(self, identifier):
        with self.db() as c:
            row=c.execute('SELECT * FROM items WHERE id=?',(identifier,)).fetchone()
        if row is None: raise ValueError('素材不存在')
        return annotate_items(self.workspace, [self._decode(row)])[0]

    def listing(self, query=None):
        q=parameters(query,{'q','source','kind','tag','status','trashed','page'})
        page=page_number(q)
        if q.get('trashed','0') not in ('0','1'):raise ValueError('回收站参数无效')
        where=["i.trashed_at<>''" if q.get('trashed')=='1' else "i.trashed_at=''"]
        args=[]
        text=q.get('q','').strip()
        if text:
            if len(text)<3:
                where.append('i.id IN (SELECT item_id FROM item_short_terms WHERE term=?)')
                args.append(text.casefold())
            else:
                where.append('i.rowid IN (SELECT rowid FROM items_fts WHERE items_fts MATCH ?)')
                args.append(fts_query(text))
        for field in ('kind','status'):
            if q.get(field):where.append('i.'+field+'=?');args.append(q[field])
        for field,table,col in [('source','item_sources','source'),('tag','item_tags','tag')]:
            if q.get(field):where.append(f'i.id IN (SELECT item_id FROM {table} WHERE {col}=?)');args.append(q[field])
        clause=' AND '.join(where)
        with self.db() as c:
            total=c.execute('SELECT count(*) FROM items i WHERE '+clause,args).fetchone()[0]
            rows=c.execute('SELECT '+','.join('i.'+s for s in SUMMARY.split(','))+' FROM items i WHERE '+clause+' ORDER BY i.updated_at DESC,i.id LIMIT 50 OFFSET ?',args+[(page-1)*50]).fetchall()
            sources=[r[0] for r in c.execute('SELECT DISTINCT source FROM item_sources ORDER BY source')]
            tags=[r[0] for r in c.execute('SELECT DISTINCT tag FROM item_tags ORDER BY tag')]
        return dict(items=annotate_items(self.workspace,[self._decode(r) for r in rows]),total=total,page=page,page_size=50,sources=sources,tags=tags)

    def mark_used(self, identifier, slug):
        """关联本期创建的作品，兼容旧 /use 动作。used_work_id 已在二期 schema。"""
        if not isinstance(slug,str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,79}',slug):
            raise ValueError('作品编号无效')
        with self.db(True) as c:
            if not c.execute('SELECT 1 FROM items WHERE id=?',(identifier,)).fetchone():
                raise ValueError('素材不存在')
            c.execute("UPDATE items SET status='used',used_work_id=?,updated_at=? WHERE id=?",(slug,now(),identifier))
        return self.detail(identifier)

    def action(self, identifier, action, payload=None):
        with self.db(True) as c:
            row=c.execute('SELECT * FROM items WHERE id=?',(identifier,)).fetchone()
            if row is None:raise ValueError('素材不存在')
            if action in ('trash','restore'):
                timestamp=now() if action=='trash' else ''
                c.execute('UPDATE items SET trashed_at=? WHERE id=?',(timestamp,identifier))
                # Do not revive a child trashed independently before its parent.
                c.execute("UPDATE items SET trashed_at=? WHERE parent_id=? AND kind='card' AND trashed_at=?",(timestamp,identifier,'' if action=='trash' else row['trashed_at']))
            elif action=='delete':
                if not row['trashed_at']:raise ValueError('请先移到回收站，再彻底删除')
                if c.execute("SELECT 1 FROM items WHERE parent_id=? AND kind<>'card' LIMIT 1",(identifier,)).fetchone():raise ValueError('有其他素材引用此条，不能彻底删除')
                child_ids=[r[0] for r in c.execute("SELECT id FROM items WHERE parent_id=? AND kind='card'",(identifier,))]
                if child_ids and c.execute('SELECT 1 FROM items WHERE parent_id IN ('+','.join('?' for _ in child_ids)+') LIMIT 1',child_ids).fetchone():raise ValueError('子卡片被其他素材引用，不能彻底删除')
                c.execute("DELETE FROM items WHERE parent_id=? AND kind='card'",(identifier,))
                c.execute('DELETE FROM items WHERE id=?',(identifier,))
                return {'id':identifier,'deleted':True}
            elif action=='note':
                if not isinstance(payload,dict) or set(payload)!={'note'} or not isinstance(payload['note'],str):raise ValueError('收藏理由格式无效')
                c.execute('UPDATE items SET note=?,updated_at=? WHERE id=?',(payload['note'],now(),identifier))
                self._index_short(c,c.execute('SELECT * FROM items WHERE id=?',(identifier,)).fetchone())
            elif action=='use':
                c.execute("UPDATE items SET status='used',updated_at=? WHERE id=?",(now(),identifier))
            else:raise ValueError('素材动作无效')
        return self.detail(identifier)


def parameters(query, allowed):
    query=query or {}
    if set(query)-allowed or any(not isinstance(v,list) or len(v)!=1 for v in query.values()):raise ValueError('查询参数无效')
    return {k:v[0] for k,v in query.items()}


def page_number(q):
    page=int(q.get('page','1'))
    if not 1<=page<=100000:raise ValueError('页码无效')
    return page


def fts_query(text):
    # Treat user input as one literal phrase, never FTS operators or SQL.
    return '"'+text.replace('"','""')+'"'


def readonly(path):
    path=Path(path).resolve()
    try:
        c=sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=.5)
        c.execute('SELECT 1 FROM sqlite_master LIMIT 1').fetchone()
    except sqlite3.OperationalError:
        c=sqlite3.connect(path.as_uri()+'?mode=ro&immutable=1',uri=True,timeout=.5)
    c.row_factory=sqlite3.Row
    c.execute('PRAGMA mmap_size=268435456')  # connection-local, does not write the old DB
    return c


def short_title_search(c, text, source, page, deadline):
    """Bound work independently of matches; never scan until a rare term fills a page."""
    pattern='%'+text.replace('\\','\\\\').replace('%','\\%').replace('_','\\_')+'%'
    offset=(page-1)*50
    rows=[];scanned=0;partial=True
    # Do not put the match/source predicates in WHERE: LIMIT must bound examined
    # documents, including nonmatches. Rowid order uses the existing table btree.
    sql="""SELECT d.id,d.source,d.kind,d.title,d.url,d.modified AS published_at,
        substr(d.title,1,240) AS snippet,
        (d.title LIKE ? ESCAPE '\\' AND (?='' OR d.source=?)
         AND NOT EXISTS (SELECT 1 FROM document_trash t WHERE t.document_id=d.id)) AS matched
        FROM documents d ORDER BY d.rowid DESC LIMIT ?"""
    cursor=None
    c.set_progress_handler(lambda: int(time.monotonic()>=deadline),256)
    try:
        cursor=c.execute(sql,(pattern,source,source,SHORT_SEARCH_ROWS))
        for row in cursor:
            scanned+=1
            if row['matched']:
                item=dict(row);item.pop('matched');rows.append(item)
                if len(rows)>offset+50:break
            if time.monotonic()>=deadline:break
        else:
            partial=scanned>=SHORT_SEARCH_ROWS
    except sqlite3.OperationalError as exc:
        if getattr(exc,'sqlite_errorcode',None)!=sqlite3.SQLITE_INTERRUPT:raise
    finally:
        if cursor is not None:cursor.close()
        c.set_progress_handler(None,0)
    visible=rows[offset:offset+50];more=len(rows)>offset+50
    # Lower bound counts actual matches, never the requested page's offset.
    total=min(len(rows),offset+50)
    return dict(items=visible,total=total,total_label=('≥' if partial or more else '')+str(total),
                has_more=more,page=page,page_size=50,short_query=True,
                partial=partial,scanned_count=scanned)


def library_search(root,query):
    q=parameters(query,{'q','source','page'})
    page=page_number(q);text=q.get('q','').strip();short=len(text)<3
    if not text:return dict(items=[],total=0,page=page,short_query=True)
    path=workspace(root)/'knowledge/knowledge.db'
    if not path.exists():return dict(items=[],total=0,page=page,short_query=short)
    deadline=time.monotonic()+SHORT_SEARCH_SECONDS
    c=readonly(path)
    try:
        if short:
            from library_search import catalog_search
            result,status=catalog_search(root,c,text,q.get('source',''),page)
            if result is None:
                result=short_title_search(c,text,q.get('source',''),page,deadline)
                result['catalog_state']=status
            result['sources']=[r[0] for r in c.execute('SELECT id FROM sources ORDER BY id')] if c.execute("SELECT 1 FROM sqlite_master WHERE name='sources'").fetchone() else []
            return result
        args=[]
        join='JOIN document_fts ON document_fts.rowid=d.rowid';where='document_fts MATCH ?';args.append(fts_query(text));snippet="snippet(document_fts,1,char(1),char(2),'…',40)"
        where+=' AND NOT EXISTS (SELECT 1 FROM document_trash t WHERE t.document_id=d.id)'
        if q.get('source'):where+=' AND d.source=?';args.append(q['source'])
        # No body projection, no full COUNT; fetch at most one extra row for paging.
        order=' ORDER BY d.rowid DESC' if short else ''
        rows=c.execute(f'SELECT d.id,d.source,d.kind,d.title,d.url,d.modified AS published_at,{snippet} AS snippet FROM documents d {join} WHERE {where}{order} LIMIT 51 OFFSET ?',args+[(page-1)*50]).fetchall()
        more=len(rows)>50
        sources=[r[0] for r in c.execute('SELECT id FROM sources ORDER BY id')] if c.execute("SELECT 1 FROM sqlite_master WHERE name='sources'").fetchone() else []
        return dict(sources=sources,items=[dict(r) for r in rows[:50]],total=(page-1)*50+min(len(rows),50),total_label=('≥'+str(page*50) if more else str((page-1)*50+len(rows))),has_more=more,page=page,page_size=50,short_query=short)
    finally:c.close()


def from_document(document):
    p=decoded(document.get('provenance'),{})
    return dict(legacy_id=document['id'],kind='article',source=document.get('source','knowledge'),sources=[document.get('source','knowledge')],title=document.get('title',''),full_text=document.get('body',''),url=document.get('url',''),published_at=document.get('modified',''),created_at=document.get('modified','') or now(),updated_at=document.get('modified','') or now(),tags=decoded(document.get('tags'),[]),author={'name':p.get('author',''),'handle':p.get('author_handle','')},note=p.get('reader_note') or p.get('reason',''),legacy_refs=[{'store':'knowledge','id':document['id']}],trashed_at=document.get('deleted_at','') if document.get('trashed') else '')


def upsert_from_reference(root, document):
    """Best-effort mirror: original reference API always keeps its own receipt/result."""
    try:ItemsStore(root).upsert(from_document(document))
    except Exception:LOG.exception('素材镜像写入失败；旧收藏已保存')
    return document


def get_route(handler,parts,query):
    try:
        if parts.path=='/api/library/search':return handler.reply(200,library_search(handler.server.store.workspace,query))
        store=ItemsStore(handler.server.store.workspace)
        if parts.path=='/api/items':return handler.reply(200,store.listing(query))
        identifier=unquote(parts.path[len('/api/items/'):])
        return handler.reply(200,store.detail(identifier))
    except (ValueError,TypeError) as exc:return handler.reply(400,{'error':str(exc)})
    except (OSError,sqlite3.Error):return handler.reply(503,{'error':'素材库暂不可用，请稍后重试'})


def post_route(handler,payload):
    try:
        store=ItemsStore(handler.server.store.workspace)
        path=urlsplit(handler.path).path
        if path=='/api/items':
            if isinstance(payload,dict) and set(payload)=={'document_id'}:
                # Direct read-only document retrieval; do not instantiate KnowledgeStore.
                c=readonly(workspace(handler.server.store.workspace)/'knowledge/knowledge.db')
                try:
                    row=c.execute('SELECT d.* FROM documents d WHERE id=? AND NOT EXISTS (SELECT 1 FROM document_trash t WHERE t.document_id=d.id)',(payload['document_id'],)).fetchone()
                    if not row:raise ValueError('资料不存在或已删除')
                    payload=from_document(dict(row))
                finally:c.close()
            return handler.reply(201,store.upsert(payload))
        identifier,action=path[len('/api/items/'):].rsplit('/',1)
        return handler.reply(200,store.action(unquote(identifier),action,payload))
    except (ValueError,TypeError) as exc:return handler.reply(400,{'error':str(exc)})
    except (OSError,sqlite3.Error):return handler.reply(503,{'error':'素材未保存，请稍后重试'})


def delete_route(handler):
    origins={f'http://127.0.0.1:{handler.server.server_port}',f'http://localhost:{handler.server.server_port}'}
    if not handler.trusted() or handler.headers.get('Origin') not in origins or handler.headers.get('X-Article-Studio')!='1':return handler.reply(403,{'error':'请求来源不受支持'})
    path=urlsplit(handler.path).path
    if not path.startswith('/api/items/'):return handler.reply(404,{'error':'接口不存在'})
    try:return handler.reply(200,ItemsStore(handler.server.store.workspace).action(unquote(path[len('/api/items/'):]),'delete'))
    except ValueError as exc:return handler.reply(400,{'error':str(exc)})
    except (OSError,sqlite3.Error):return handler.reply(503,{'error':'素材未删除，请稍后重试'})
