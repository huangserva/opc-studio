"""Private Gmail state/outbox and index. No document goes into the public index."""
from contextlib import contextmanager, closing
from pathlib import Path
import hashlib
import json
import os
import re
import sqlite3
import time
from gmail_source import GmailCache, account_address
from gmail_auth import GmailError


def history_id(value):
    if not isinstance(value,str) or not re.fullmatch('[0-9]{1,100}',value):raise GmailError('format')
    return value

class PrivateIndex:
    def __init__(self,workspace):
        self.root=Path(workspace)/'mail';self.root.mkdir(mode=0o700,parents=True,exist_ok=True);os.chmod(self.root,0o700)
        self.path=self.root/'gmail-private.db'
        if self.path.is_symlink() or self.root.is_symlink():raise GmailError('boundary')
        with closing(sqlite3.connect(self.path)) as db:
            db.executescript('''CREATE TABLE IF NOT EXISTS documents(id TEXT PRIMARY KEY,source TEXT NOT NULL,title TEXT NOT NULL,body TEXT NOT NULL,record TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS sources(id TEXT PRIMARY KEY,record TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS dossiers(id TEXT PRIMARY KEY,record TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS document_trash(document_id TEXT PRIMARY KEY,deleted_at TEXT NOT NULL,deleted_hash TEXT NOT NULL DEFAULT '',title TEXT NOT NULL DEFAULT '',source TEXT NOT NULL DEFAULT '');
            CREATE VIRTUAL TABLE IF NOT EXISTS private_fts USING fts5(title,body,content='documents',content_rowid='rowid',tokenize='trigram');
            CREATE TRIGGER IF NOT EXISTS pi_insert AFTER INSERT ON documents BEGIN INSERT INTO private_fts(rowid,title,body) VALUES(new.rowid,new.title,new.body); END;
            CREATE TRIGGER IF NOT EXISTS pi_delete AFTER DELETE ON documents BEGIN INSERT INTO private_fts(private_fts,rowid,title,body) VALUES('delete',old.rowid,old.title,old.body); END;
            CREATE TRIGGER IF NOT EXISTS pi_update AFTER UPDATE ON documents BEGIN INSERT INTO private_fts(private_fts,rowid,title,body) VALUES('delete',old.rowid,old.title,old.body); INSERT INTO private_fts(rowid,title,body) VALUES(new.rowid,new.title,new.body); END;''');db.commit()
        os.chmod(self.path,0o600)
    @contextmanager
    def db(self):
        with closing(sqlite3.connect(self.path,timeout=5)) as db:
            db.row_factory=sqlite3.Row
            with db:yield db
    def put(self,record):
        record=dict(record)
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            # Reindex and a local deletion serialize before reading the deletion state.
            previous=db.execute('SELECT record FROM documents WHERE id=?',(record['id'],)).fetchone()
            previous=json.loads(previous[0]) if previous else {}
            mark=db.execute('SELECT deleted_at FROM document_trash WHERE document_id=?',(record['id'],)).fetchone()
            record['trashed']=True if mark else previous.get('trashed',False) or record.get('trashed',False)
            record['deleted_at']=mark[0] if mark else previous.get('deleted_at','')
            db.execute('INSERT INTO documents VALUES(?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET title=excluded.title,body=excluded.body,record=excluded.record',(record['id'],record['source'],record['title'],record['body'],json.dumps(record,ensure_ascii=False)))
    def source(self,record):
        with self.db() as db:db.execute('INSERT INTO sources VALUES(?,?) ON CONFLICT(id) DO UPDATE SET record=excluded.record',(record['id'],json.dumps(record,ensure_ascii=False)))
    def sources(self):
        with self.db() as db:return [json.loads(r[0]) for r in db.execute('SELECT record FROM sources')]
    def get(self,ident):
        with self.db() as db:r=db.execute('SELECT record FROM documents WHERE id=?',(ident,)).fetchone()
        return json.loads(r[0]) if r else None
    def search(self,query='',source='',offset=0,mode=''):
        args=[];where=[];join=''
        if source:where.append('d.source=?');args.append(source)
        terms=str(query).split()[:12]
        if mode=='fts':
            terms=[x for x in terms if len(x)>=3]
            if not terms:return dict(results=[],total=0,offset=offset,next_offset=None)
            join=' JOIN private_fts f ON f.rowid=d.rowid';where.append('private_fts MATCH ?');args.append(' AND '.join('"'+x.replace('"','""')+'"' for x in terms))
        else:
            for term in terms:
                where.append("(d.title LIKE ? ESCAPE '\\' OR d.body LIKE ? ESCAPE '\\')");pattern='%'+term.replace('\\','\\\\').replace('%','\\%').replace('_','\\_')+'%';args.extend([pattern,pattern])
        # Hidden/tombstoned rows remain as evidence but never appear in active search.
        where.append("COALESCE(json_extract(d.record,'$.gmail_hidden'),0)=0 AND COALESCE(json_extract(d.record,'$.trashed'),0)=0")
        sql=' FROM documents d'+join+' WHERE '+' AND '.join(where)
        with self.db() as db:
            total=db.execute('SELECT COUNT(*)'+sql,args).fetchone()[0];rows=db.execute('SELECT d.record'+sql+' ORDER BY d.id LIMIT 30 OFFSET ?',[*args,offset]).fetchall()
        results=[]
        for row in rows:
            r=json.loads(row[0]);body=r.pop('body');r.update(excerpt=body[:550],excerpt_start=0);results.append(r)
        return dict(results=results,total=total,offset=offset,next_offset=offset+30 if offset+30<total else None)

class SyncStore:
    def __init__(self,workspace,clock=time.time):
        self.workspace=Path(workspace);self.cache=GmailCache(workspace);self.path=self.cache.path;self.clock=clock;self.index=PrivateIndex(workspace)
        with self.cache.db(write=True) as db:
            db.executescript('''CREATE TABLE IF NOT EXISTS gmail_connections(ref TEXT PRIMARY KEY,data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS gmail_outbox(account TEXT NOT NULL,message_id TEXT NOT NULL,revision INTEGER NOT NULL,PRIMARY KEY(account,message_id));
            CREATE TABLE IF NOT EXISTS gmail_marks(account TEXT NOT NULL,message_id TEXT NOT NULL,deleted INTEGER NOT NULL DEFAULT 0,hidden INTEGER NOT NULL DEFAULT 0,stale INTEGER NOT NULL DEFAULT 0,PRIMARY KEY(account,message_id));''')
    def state(self):
        with self.cache.db() as db:r=db.execute('SELECT data FROM gmail_connections LIMIT 1').fetchone()
        return json.loads(r[0]) if r else None
    def save(self,state,*,expected_generation=None):
        # Schema allow-list: credentials/client JSON never enter this database.
        allowed={'ref','client_ref','credential_backend','scopes','connected','paused','status','error','history_id','phase','page_token','baseline','lower','upper','days','labels','interval','next_due','cooldown','last_success','next_retry_at','fetched','indexed','lease_until','config_changed','error_count','retry_pending','generation'}
        if set(state)-allowed:raise GmailError('boundary')
        with self.cache.db(write=True) as db:
            if expected_generation is not None:self._check_generation(db,state,expected_generation)
            db.execute('INSERT INTO gmail_connections VALUES(?,?) ON CONFLICT(ref) DO UPDATE SET data=excluded.data',(state['ref'],json.dumps(state)))
    def _check_generation(self,db,state,expected,*,active=False):
        row=db.execute('SELECT data FROM gmail_connections LIMIT 1').fetchone()
        current=json.loads(row[0]) if row else {}
        if current.get('ref')!=state['ref'] or current.get('generation',0)!=expected or active and (not current.get('connected') or current.get('paused')):
            raise GmailError('paused')
    def update(self,*,invalidate=False,expected_generation=None,**values):
        with self.cache.db(write=True) as db:
            row=db.execute('SELECT data FROM gmail_connections LIMIT 1').fetchone();s=json.loads(row[0])
            if expected_generation is not None:self._check_generation(db,s,expected_generation)
            if invalidate or any(key in values and values[key]!=s.get(key) for key in ('connected','paused')):s['generation']=s.get('generation',0)+1
            if values.get('paused') and 'status' not in values:values['status']='paused'
            s.update(values)
            db.execute('UPDATE gmail_connections SET data=? WHERE ref=?',(json.dumps(s),s['ref']))
            return s
    def account(self):
        # Only a single authenticated account may bind to an existing cache.
        with self.cache.db() as db:rows=db.execute('SELECT DISTINCT account FROM messages UNION SELECT account FROM accounts').fetchall()
        if len(rows)>1:raise GmailError('account')
        return rows[0][0] if rows else None
    def bind(self,account):
        account=account_address(account)
        if self.account() not in (None,account):raise GmailError('account')
    def commit_page(self,account,messages,marks,state):
        """Messages, source observations, pending obligations and cursor are one transaction."""
        normalized=[]
        if len(messages)>100 or len(json.dumps(messages,ensure_ascii=False).encode())>10*1024*1024:raise GmailError('format')
        if any(len(json.dumps(message,ensure_ascii=False).encode())>2*1024*1024 for message in messages):raise GmailError('format')
        for message in messages:normalized.append(self.cache._normalize(message,account,'gmail-api',True,__import__('datetime').datetime.now(__import__('datetime').timezone.utc).isoformat()))
        with self.cache.db(write=True) as db:
            # Cancellation is checked under the same write lock as messages/outbox/cursor.
            self._check_generation(db,state,state.get('generation',0),active=True)
            for r in normalized:
                db.execute('INSERT INTO messages VALUES(?,?,?,?,?) ON CONFLICT(account,message_id) DO UPDATE SET thread_id=excluded.thread_id,internal_date=excluded.internal_date,record=excluded.record',(account,r['message_id'],r['thread_id'],int(r['internal_date']),json.dumps(r,ensure_ascii=False)))
                db.execute('INSERT INTO gmail_outbox VALUES(?,?,1) ON CONFLICT(account,message_id) DO UPDATE SET revision=revision+1',(account,r['message_id']))
            for mid,deleted,hidden,stale in marks:
                db.execute('INSERT INTO gmail_marks VALUES(?,?,?,?,?) ON CONFLICT(account,message_id) DO UPDATE SET deleted=excluded.deleted,hidden=excluded.hidden,stale=excluded.stale',(account,mid,deleted,hidden,stale))
                db.execute('INSERT INTO gmail_outbox VALUES(?,?,1) ON CONFLICT(account,message_id) DO UPDATE SET revision=revision+1',(account,mid))
            db.execute('INSERT INTO accounts VALUES(?,?,?,?) ON CONFLICT(account) DO UPDATE SET query=excluded.query,has_more=excluded.has_more,captured_at=excluded.captured_at',(account,'gmail-api',int(state['phase']!='idle'),str(self.clock())))
            db.execute('INSERT INTO gmail_connections VALUES(?,?) ON CONFLICT(ref) DO UPDATE SET data=excluded.data',(state['ref'],json.dumps(state)))
    def drain(self,account,limit=500):
        from knowledge_store import identifier
        sid='gmail-'+identifier('gmail',account)[:12]
        for _ in range(limit):
            with self.cache.db() as db:
                out=db.execute('SELECT * FROM gmail_outbox WHERE account=? LIMIT 1',(account,)).fetchone()
                if not out:break
                row=db.execute('SELECT record FROM messages WHERE account=? AND message_id=?',(account,out['message_id'])).fetchone();mark=db.execute('SELECT * FROM gmail_marks WHERE account=? AND message_id=?',(account,out['message_id'])).fetchone()
            if row:
                r=json.loads(row[0]);hidden=bool(mark and (mark['hidden'] or mark['deleted']))
                record=mail_document(r,sid);record.update(gmail_hidden=hidden);self.index.put(record)
            with self.cache.db(write=True) as db:db.execute('DELETE FROM gmail_outbox WHERE account=? AND message_id=? AND revision=?',(account,out['message_id'],out['revision']))
        with self.cache.db() as db:return db.execute('SELECT COUNT(*) FROM gmail_outbox WHERE account=?',(account,)).fetchone()[0]
    def is_hidden(self,ident):
        # Recheck the source before publishing old index rows (outbox may be delayed).
        from knowledge_store import identifier
        with self.cache.db() as db:
            rows=db.execute('SELECT account,message_id,deleted,hidden FROM gmail_marks WHERE deleted=1 OR hidden=1').fetchall()
        return any(identifier('gmail-'+identifier('gmail',r['account'])[:12],r['message_id'])==ident for r in rows)

def mail_document(r,sid):
    from knowledge_store import identifier
    provenance={k:r.get(k) for k in ('message_id','thread_id','account','from','to','date_sent','date_header','rfc_message_id','captured_at','content_scope','attachments')}
    provenance['source_locator']=r['source_url'];record=dict(id=identifier(sid,r['message_id']),source=sid,kind='mail',title=r['subject'] or '无主题邮件',body=r['body'],url=r['source_url'],modified=r['modified'],tags=r.get('labels',[]),provenance=provenance,access_policy='gmail_owner')
    fingerprint={k:record[k] for k in ('id','source','kind','title','body','url','modified','tags')};fingerprint['tags']=json.dumps(fingerprint['tags'])
    record['hash']=hashlib.sha256(json.dumps(fingerprint,ensure_ascii=False,sort_keys=True).encode()).hexdigest();return record
