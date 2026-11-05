"""Owner-only Gmail UI/API. No event scheduler, sender, or legacy credentials."""
import json
from pathlib import Path
import secrets
import threading
import time
from http.cookies import SimpleCookie
from urllib.parse import urlsplit, parse_qs
from gmail_auth import OAuth, Keychain, FileCredentials, GmailError, client_json
from gmail_sync_store import SyncStore
from gmail_sync import GmailAdapter

_LOCK=threading.Lock()
class GmailService:
    def __init__(self,store,vault_factory=None,http=None,clock=time.time):
        self.store=store;self.clock=clock;self.sync_store=SyncStore(store.workspace);self.sessions={};self.http=http;self.vault_factory=vault_factory;self._auth=None;self._backend=None
    def auth(self,backend=None):
        state=self.sync_store.state();backend=backend or (state or {}).get('credential_backend','keychain')
        if self._auth is None or self._backend!=backend:
            if self.vault_factory:vault=self.vault_factory(backend)
            elif backend=='keychain':vault=Keychain()
            elif backend=='file':vault=FileCredentials(Path.home()/'Library/Application Support/article-studio/credentials/gmail',Path(__file__).parent)
            else:raise GmailError('boundary')
            self._auth=OAuth(vault,self.http,self.clock);self._backend=backend
        return self._auth
    def new_session(self):
        ident=secrets.token_hex(32);self.sessions[ident]=dict(nonce=secrets.token_hex(32),expires=self.clock()+1800,owner=False);return ident
    def session(self,handler,nonce=True,owner=False):
        origin=f'http://127.0.0.1:{handler.server.server_port}';localhost=f'http://localhost:{handler.server.server_port}'
        if handler.headers.get('Host') not in (origin[7:],localhost[7:]) or handler.headers.get('Sec-Fetch-Site')=='cross-site':raise GmailError('boundary')
        cookies=SimpleCookie()
        try:cookies.load(handler.headers.get('Cookie',''));ident=cookies['gmail_session'].value;s=self.sessions[ident]
        except Exception:raise GmailError('boundary') from None
        if s['expires']<=self.clock() or nonce and not secrets.compare_digest(handler.headers.get('X-Gmail-Nonce',''),s['nonce']) or owner and not s['owner']:raise GmailError('boundary')
        if handler.command=='POST' and (handler.headers.get('Origin') not in (origin,localhost) or handler.headers.get('X-Article-Studio')!='1'):raise GmailError('boundary')
        return s
    def status(self,owner=False):
        s=self.sync_store.state() or {}
        if s.get('status')=='authorizing' and self.clock()>s.get('lease_until',0):s=self.sync_store.update(status='needs_auth',error='oauth')
        allowed=('connected','paused','status','error','history_id','phase','days','labels','interval','last_success','next_due','next_retry_at','credential_backend')
        result={k:s.get(k) for k in allowed};result.setdefault('status','disconnected');result['client_configured']=bool(s.get('client_ref'));result['error_text']=GmailError.MESSAGES.get(s.get('error'),'')
        if owner:
            with self.sync_store.cache.db() as db:result.update(cached=db.execute('SELECT COUNT(*) FROM messages').fetchone()[0],pending=db.execute('SELECT COUNT(*) FROM gmail_outbox').fetchone()[0],indexed=s.get('indexed',0),fetched=s.get('fetched',0))
        return result
    def import_client(self,value,backend):
        if backend not in ('keychain','file'):raise GmailError('boundary')
        current=self.sync_store.state()
        if current and current.get('connected'):raise GmailError('busy')
        client=client_json(value);ref=(current or {}).get('ref') or secrets.token_hex(16);cref=(current or {}).get('client_ref') or secrets.token_hex(16)
        self.auth(backend).vault.put(cref,client)
        self.sync_store.save(dict(ref=ref,client_ref=cref,credential_backend=backend,connected=False,paused=False,status='disconnected',days=30,interval=60,labels=[],phase='backfill',history_id='',scopes=[],generation=(current or {}).get('generation',0)+1))
        return self.status()
    def connect(self,expected):
        s=self.sync_store.state()
        if not s or not s.get('client_ref'):raise GmailError('client')
        if s.get('status')=='syncing':raise GmailError('busy')
        from gmail_source import account_address
        # Old cache binding overrides arbitrary client-provided addresses.
        expected=account_address(expected) if expected else self.sync_store.account()
        if not expected:raise GmailError('account')
        existing=self.sync_store.account()
        if existing and expected!=existing:raise GmailError('account')
        def complete(account,scopes,watermark):
            self.sync_store.bind(account)
            current=self.sync_store.state()
            if current.get('status')!='authorizing' or current.get('paused'):raise GmailError('oauth')
            current.update(connected=True,paused=False,status='connected',error='',scopes=scopes,next_retry_at=0,cooldown=0);self.sync_store.save(current,expected_generation=authorization_generation)
        self.auth().cancel_all()
        authorization_generation=self.sync_store.update(invalidate=True,status='authorizing',paused=False,error='',lease_until=self.clock()+300)['generation']
        url=self.auth().begin(s['client_ref'],s['ref'],expected,complete,lambda code:self.sync_store.update(expected_generation=authorization_generation,connected=False,status='needs_auth',error=code))
        return {'authorization_url':url}
    def search(self,q):
        account=self.sync_store.account()
        if not account:return dict(results=[],total=0)
        from knowledge_store import identifier
        sid='gmail-'+identifier('gmail',account)[:12]
        result=self.sync_store.index.search(q,source=sid)
        result['results']=[r for r in result['results'] if not self.sync_store.is_hidden(r['id'])];result['total']=len(result['results']) if any(self.sync_store.is_hidden(r['id']) for r in self.sync_store.index.search(q,source=sid)['results']) else result['total']
        return result
    def detail(self,ident):
        r=self.sync_store.index.get(ident)
        if not r or r.get('gmail_hidden') or self.sync_store.is_hidden(ident):raise GmailError('boundary')
        return r
    def action(self,name,payload,session):
        fields={'unlock':set(),'client':{'client','backend'},'connect':{'expected_account'},'configure':{'days','interval','labels'},'sync':set(),'pause':set(),'resume':set(),'disconnect':set()}
        if name not in fields or not isinstance(payload,dict) or set(payload)-fields[name]:raise GmailError('boundary')
        if name=='unlock':session['owner']=True;return self.status(True)
        if not session['owner']:raise GmailError('boundary')
        if name=='client':return self.import_client(payload.get('client'),payload.get('backend','keychain'))
        if name=='connect':return self.connect(payload.get('expected_account',''))
        if name=='configure':GmailAdapter(self.sync_store,self.auth(),self.clock).configure(**payload)
        elif name=='sync':return GmailAdapter(self.sync_store,self.auth(),self.clock).run()
        elif name=='pause':self.sync_store.update(invalidate=True,paused=True,status='paused')
        elif name=='resume':self.sync_store.update(invalidate=True,paused=False,status='connected')
        elif name=='disconnect':
            state=self.sync_store.state()
            if state:
                self.auth().cancel_all();self.sync_store.update(invalidate=True,connected=False,paused=True,status='disconnected')
                self.auth().vault.delete(state['ref'])
        return self.status(True)

def service(server):
    with _LOCK:
        if not hasattr(server,'gmail_service'):server.gmail_service=GmailService(server.store)
        return server.gmail_service

def get_route(handler,parts):
    try:
        s=service(handler.server)
        if parts.path=='/gmail':
            if handler.headers.get('Sec-Fetch-Site')=='cross-site':raise GmailError('boundary')
            ident=s.new_session();raw=(Path(__file__).parent/'public/gmail.html').read_bytes()
            handler.send_response(200);handler.send_header('Content-Type','text/html; charset=utf-8');handler.send_header('Set-Cookie','gmail_session='+ident+'; HttpOnly; SameSite=Strict; Path=/');handler.send_header('Cache-Control','no-store');handler.send_header('Content-Security-Policy',"default-src 'self'; img-src 'self'; object-src 'none'; frame-ancestors 'none'");handler.end_headers();handler.wfile.write(raw);return
        session=s.session(handler,nonce=parts.path!='/api/gmail/session')
        if parts.path=='/api/gmail/session':return handler.reply(200,dict(nonce=session['nonce'],owner=session['owner']))
        if parts.path=='/api/gmail/status':return handler.reply(200,s.status(session['owner']))
        if not session['owner']:raise GmailError('boundary')
        query=parse_qs(parts.query)
        if parts.path=='/api/gmail/search':
            if set(query)-{'q'}:raise GmailError('boundary')
            return handler.reply(200,s.search(query.get('q',[''])[0][:200]))
        if parts.path.startswith('/api/gmail/document/'):
            return handler.reply(200,s.detail(parts.path.rsplit('/',1)[1]))
        raise GmailError('boundary')
    except GmailError as error:return handler.reply(403 if error.code=='boundary' else 400,dict(error=error.code,message=str(error)))
    except Exception:return handler.reply(400,dict(error='format',message='邮件请求未完成，请核对本机状态'))

def post_route(handler,payload,parts):
    try:
        s=service(handler.server);session=s.session(handler);result=s.action(parts.path.rsplit('/',1)[1],payload,session);return handler.reply(200,result)
    except GmailError as error:return handler.reply(403 if error.code=='boundary' else 409,dict(error=error.code,message=str(error)))
    except Exception:return handler.reply(400,dict(error='format',message='邮件请求未完成，请核对本机状态'))

def owner_route(handler,callback):
    """Explicit capability header, never a private=true query or ambient localhost trust."""
    path=urlsplit(handler.path).path
    scoped=path.startswith('/api/knowledge') or path=='/api/events' or path.startswith('/api/events/')
    if not scoped or not handler.headers.get('X-Gmail-Nonce'):return callback()
    # Preserve the existing generic POST Origin/Host errors before private dispatch.
    if handler.command=='POST':
        origins={f'http://127.0.0.1:{handler.server.server_port}',f'http://localhost:{handler.server.server_port}'}
        if not handler.trusted() or handler.headers.get('Origin') not in origins or handler.headers.get('X-Article-Studio')!='1':return callback()
    try:service(handler.server).session(handler,owner=True)
    except GmailError:return handler.reply(403,{'error':'邮件请求未获本人本机会话授权'})
    with handler.server.store.knowledge.gmail_owner():return callback()
