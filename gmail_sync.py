"""Bounded read-only Gmail adapter. The unified runner calls run_due; no daemon."""
from contextlib import contextmanager
import fcntl
import json
import os
import re
import secrets
import time
from gmail_auth import GmailError
from gmail_sync_store import history_id


def api_message(value):
    """Convert Google JSON to the existing bounded MIME cache format."""
    def part(p,depth=0):
        if depth>30 or not isinstance(p,dict):raise GmailError('format')
        body=p.get('body') or {};mime=p.get('mimeType','');filename=p.get('filename','');detached=body.get('attachmentId')
        attachment=bool(filename) or mime not in ('text/plain','text/html')
        data=body.get('data','') if not attachment and not detached else ''
        return dict(mime_type=mime,filename=filename,part_id=p.get('partId',''),headers=p.get('headers',[]),body=dict(size=body.get('size',0),base64_url_content=data,attachment_id=detached or ''),parts=[part(x,depth+1) for x in p.get('parts',[])])
    return dict(id=value['id'],thread_id=value.get('threadId',value['id']),label_ids=value.get('labelIds',[]),snippet=value.get('snippet',''),history_id=value.get('historyId',''),internal_date=value['internalDate'],remote_size_estimate=value.get('sizeEstimate',0),payload=part(value.get('payload',{})))

class GmailAdapter:
    source='gmail';event='ingest_and_index'
    def __init__(self,store,auth,clock=time.time,budget=300,message_limit=500):self.store=store;self.auth=auth;self.clock=clock;self.budget=budget;self.limit=message_limit
    @property
    def source_instance(self):
        return 'gmail:'+self.store.state()['ref']
    def registration(self):
        return dict(source=self.source_instance,event=self.event,interval_minutes=self.store.state().get('interval',60),cooldown_seconds=300,registered=False)
    def _save(self,s,generation=None):
        self.store.save(s,expected_generation=s.get('generation',0) if generation is None else generation)
    def configure(self,days=30,interval=60,labels=None):
        if type(days) is not int or not 1<=days<=365 or type(interval) is not int or interval not in (30,60) or not isinstance(labels or [],list) or len(labels or [])>100 or any(not re.fullmatch('[A-Za-z0-9_-]{1,128}',x) for x in labels or []):raise GmailError('format')
        s=self.store.state()
        if s.get('status')=='syncing':raise GmailError('busy')
        s.update(days=days,interval=interval,labels=list(dict.fromkeys(labels or [])),config_changed=True);self._save(s)
    def run_due(self):
        s=self.store.state()
        if not s or not s.get('connected') or s.get('paused') or self.clock()<max(s.get('next_due',0),s.get('next_retry_at',0),s.get('cooldown',0)):return {'status':'waiting'}
        return self.run()
    def run(self):
        s=self.store.state()
        if not s or not s.get('connected'):raise GmailError('auth')
        if s.get('paused'):raise GmailError('paused')
        if self.clock()<max(s.get('cooldown',0),s.get('next_retry_at',0)):raise GmailError('busy')
        lock=self.store.cache.root/'gmail-sync.lock'
        if lock.is_symlink():raise GmailError('boundary')
        fd=os.open(lock,os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
        try:
            try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:raise GmailError('busy') from None
            s=self.store.state();generation=s.get('generation',0)
            if not s.get('connected'):raise GmailError('auth')
            if s.get('paused'):raise GmailError('paused')
            if self.clock()<s.get('cooldown',0):raise GmailError('busy')
            s.update(cooldown=self.clock()+300,status='syncing',error='');self._save(s)
            started=self.clock();processed=0;rebuilt=False
            account=self.store.account();stage='fetch'
            try:
                profile=self.auth.read(s['ref'],'/profile');from gmail_source import account_address
                actual=account_address(profile['emailAddress'])
                if actual!=self.auth.identity(s['ref']):raise GmailError('account')
                self.store.bind(actual);account=actual
                if not s.get('baseline') and not s.get('history_id') or s.get('config_changed'):
                    self._backfill(s,profile);s['config_changed']=False;self._save(s)
                elif s.get('phase','idle')=='idle':s.update(phase='history',page_token='',baseline=s['history_id']);self._save(s)
                while processed<self.limit and self.clock()-started<self.budget:
                    if self.store.state().get('paused'):raise GmailError('paused')
                    s['paused']=False
                    phase=s['phase'];remaining=min(100,self.limit-processed)
                    pending=s.get('retry_pending')
                    try:
                        if pending:
                            page=pending['page'];ids=pending['ids'];deleted=set(pending['deleted'])
                        elif phase=='backfill':
                            # labels are OR; no Gmail labelIds parameter (which would be AND).
                            query={'q':f"after:{s['lower']} before:{s['upper']} -in:trash -in:spam -in:drafts",'maxResults':remaining}
                            if s.get('page_token'):query['pageToken']=s['page_token']
                            page=self.auth.read(s['ref'],'/messages',query);ids=list(dict.fromkeys(x['id'] for x in page.get('messages',[])));deleted=set()
                        elif phase=='history':
                            query={'startHistoryId':s['baseline'],'maxResults':min(100,remaining)}
                            if s.get('page_token'):query['pageToken']=s['page_token']
                            page=self.auth.read(s['ref'],'/history',query);ids=[];deleted=set()
                            for event in page.get('history',[]):
                                history_id(event['id'])
                                for key in ('messagesAdded','labelsAdded','labelsRemoved'):
                                    ids.extend(x['message']['id'] for x in event.get(key,[]))
                                deleted.update(x['message']['id'] for x in event.get('messagesDeleted',[]))
                            ids=list(dict.fromkeys(ids));ids=[x for x in ids if x not in deleted]
                            # History pages may expand into > maxResults messages. Replay entire page next run.
                            pass
                        else:break
                    except GmailError as error:
                        if error.code=='history':
                            if rebuilt:raise
                            rebuilt=True;profile=self.auth.read(s['ref'],'/profile')
                            with self.store.cache.db(write=True) as db:
                                db.execute('INSERT INTO gmail_marks(account,message_id,stale) SELECT account,message_id,1 FROM messages WHERE account=? ON CONFLICT(account,message_id) DO UPDATE SET stale=1',(account,))
                            self._backfill(s,profile);s.update(status='rebuilding',error='history');self._save(s);continue
                        if error.code=='page' and phase=='backfill':s['page_token']='';self._save(s)
                        raise
                    # A durable obligation makes a large/failing page resumable without advancing its watermark.
                    s['retry_pending']={'ids':ids,'deleted':list(deleted),'page':{'nextPageToken':page.get('nextPageToken',''),'historyId':page.get('historyId','')}};self._save(s)
                    selected_ids=ids[:remaining];left_ids=ids[remaining:]
                    messages=[];marks=[]
                    for mid in selected_ids:
                        if self.clock()-started>=self.budget:raise GmailError('network')
                        if self.store.state().get('paused'):raise GmailError('paused')
                        # A read failure never gets translated into a deletion.
                        value=self.auth.read(s['ref'],'/messages/'+mid,{'format':'full'})
                        record=api_message(value);labels=set(record['label_ids']);date=int(record['internal_date'])/1000
                        selected=not s.get('labels') or bool(labels&set(s['labels']))
                        hidden=bool(labels&{'TRASH','SPAM','DRAFT'}) or not selected
                        if phase=='backfill' and date<s['lower']:continue
                        if not selected:
                            with self.store.cache.db() as db:known=db.execute('SELECT 1 FROM messages WHERE account=? AND message_id=?',(account,mid)).fetchone()
                            if not known:
                                marks.append((mid,0,1,0));continue
                        messages.append(record);marks.append((mid,0,int(hidden),0))
                    for mid in deleted:marks.append((mid,1,1,0))
                    next_page=page.get('nextPageToken','')
                    if left_ids:
                        s['retry_pending']['ids']=left_ids
                    else:
                        s['retry_pending']=None;s['page_token']=next_page
                    if not left_ids and not next_page:
                        if phase=='backfill':s.update(phase='history',baseline=s['baseline'])
                        else:s.update(phase='idle',history_id=history_id(page['historyId']))
                    s['fetched']=s.get('fetched',0)+len(messages)
                    # Final history_id only becomes visible in the transaction holding every message + outbox.
                    stage='persist'
                    self.store.commit_page(account,messages,marks,s);stage='fetch';processed+=len(selected_ids) or 1
                    if s['phase']=='idle':break
                if self.store.state().get('paused'):raise GmailError('paused')
                stage='index';pending=self.store.drain(account,self.limit)
                with self.store.index.db() as db:indexed_count=db.execute('SELECT COUNT(*) FROM documents').fetchone()[0]
                s=self.store.state()
                if s.get('generation',0)!=generation or s.get('paused') or not s.get('connected'):raise GmailError('paused') from None
                s.update(status='index_pending' if pending else 'ready' if s['phase']=='idle' else 'backfill',error='',last_success=self.clock() if s['phase']=='idle' else s.get('last_success'),next_due=self.clock()+s.get('interval',60)*60,next_retry_at=0,error_count=0,indexed=indexed_count);self._save(s,generation)
                return dict(status=s['status'],processed=processed,pending=pending,history_id=s.get('history_id',''))
            except GmailError as error:
                s=self.store.state()
                if s.get('generation',0)!=generation or s.get('status') in ('authorizing','disconnected'):raise GmailError(error.code) from None
                if s.get('paused'):error=GmailError('paused')
                s.update(connected=False if error.code in ('auth','scope','account','credentials') else s.get('connected'),status='needs_auth' if error.code in ('auth','scope','account','credentials') else 'paused' if error.code=='paused' else 'waiting',error=error.code,error_count=min(s.get('error_count',0)+1,8),next_retry_at=self.clock()+min(3600,60*2**s.get('error_count',0)));self._save(s,generation);raise GmailError(error.code) from None
            except Exception:
                with self.store.index.db() as db:indexed_count=db.execute('SELECT COUNT(*) FROM documents').fetchone()[0]
                code='index' if stage=='index' else 'format'
                s=self.store.state()
                if s.get('generation',0)!=generation or s.get('paused') or not s.get('connected'):raise GmailError('paused') from None
                s.update(status='index_pending' if stage=='index' else 'waiting',error=code,next_retry_at=self.clock()+300);self._save(s,generation);raise GmailError(code) from None
        finally:os.close(fd)
    def _backfill(self,s,profile):
        s.update(phase='backfill',page_token='',retry_pending=None,baseline=history_id(profile['historyId']),upper=int(self.clock())+1,lower=int(self.clock())-s.get('days',30)*86400,status='backfill')
