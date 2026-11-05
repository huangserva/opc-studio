"""Desktop OAuth for the owner's Gmail. No legacy connector access, no sending."""
import base64
import ctypes as C
import hashlib
import http.server
import json
import os
from pathlib import Path
import re
import secrets
import threading
import time
from urllib.parse import urlencode, urlsplit, parse_qs
from urllib.request import Request, build_opener, HTTPRedirectHandler, ProxyHandler
from urllib.error import HTTPError, URLError

READ_SCOPE = 'https://www.googleapis.com/auth/gmail.readonly'
AUTH_URL = 'https://accounts.google.com/o/oauth2/v2/auth'
TOKEN_URL = 'https://oauth2.googleapis.com/token'
API_URL = 'https://gmail.googleapis.com/gmail/v1/users/me'
SERVICE = 'article-studio.gmail.p1'

class GmailError(Exception):
    MESSAGES = {'credentials':'无法读取本机凭据', 'auth':'授权已失效，请重新连接',
        'oauth':'授权会话无效或已过期，请重新连接', 'scope':'未获得收信权限，请重新授权',
        'account':'授权账号与本机缓存不符，未开始同步', 'client':'请选择有效的 Desktop 客户端 JSON，端点不可改写',
        'network':'网络暂不可用，稍后续跑', 'rate':'请求受限，等待后续重试',
        'policy':'账号权限或组织策略阻止读取', 'format':'邮件数据未通过校验，已保留待处理记录',
        'busy':'该账号正在同步或处于冷却期', 'paused':'已暂停，现有缓存保留',
        'history':'增量水位过期，正在补建', 'page':'分页游标失效，正在重新列举',
        'index':'采集结果已保存，私有索引待重试', 'boundary':'请求未获本机授权'}
    def __init__(self, code):
        self.code = code if code in self.MESSAGES else 'format'
        super().__init__(self.MESSAGES[self.code])

class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs): return None

class GoogleHTTP:
    """Production endpoints are literals; imported clients cannot replace them."""
    def request(self, method, path, *, data=None, token=None, query=None):
        if path == 'token' and method == 'POST':
            url = TOKEN_URL
            body = urlencode(data).encode()
            headers = {'Content-Type':'application/x-www-form-urlencoded'}
        elif method == 'GET' and re.fullmatch(r'/(?:profile|labels|messages(?:/[a-fA-F0-9]{1,64})?|history)', path):
            url = API_URL + path + ('?' + urlencode(query or {}, doseq=True) if query else '')
            body = None; headers = {'Authorization':'Bearer ' + token}
        else: raise GmailError('boundary')
        opener = build_opener(NoRedirect(), ProxyHandler({}))
        try:
            with opener.open(Request(url, data=body, headers=headers, method=method), timeout=25) as response:
                raw = response.read(10 * 1024 * 1024 + 1)
                if len(raw) > 10 * 1024 * 1024: raise GmailError('format')
                value = json.loads(raw)
                if not isinstance(value, dict): raise GmailError('format')
                return value
        except HTTPError as error:
            status = error.code
            # Extract only an enum. Provider error text, headers and request are never retained.
            try:
                value = json.loads(error.read(65536)); reason = value.get('error')
                reasons = [x.get('reason') for x in reason.get('errors', [])] if isinstance(reason, dict) else []
            except Exception: reason = None; reasons = []
            error.close()
            if reason == 'invalid_grant': raise GmailError('auth') from None
            if status == 401: raise GmailError('auth') from None
            if status == 404: raise GmailError('history' if path == '/history' else 'page') from None
            if status == 400: raise GmailError('page' if method == 'GET' else 'oauth') from None
            if status == 429 or status == 403 and any(r in ('rateLimitExceeded','userRateLimitExceeded','quotaExceeded') for r in reasons): raise GmailError('rate') from None
            if status == 403: raise GmailError('policy') from None
            raise GmailError('network') from None
        except GmailError: raise
        except Exception: raise GmailError('network') from None

class Keychain:
    """Native generic-password API; secrets never appear in argv or shell output."""
    def __init__(self):
        try:
            self.cf=C.CDLL('/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation')
            self.sec=C.CDLL('/System/Library/Frameworks/Security.framework/Security')
            self.cf.CFStringCreateWithCString.argtypes=[C.c_void_p,C.c_char_p,C.c_uint32];self.cf.CFStringCreateWithCString.restype=C.c_void_p
            self.cf.CFDataCreate.argtypes=[C.c_void_p,C.c_void_p,C.c_long];self.cf.CFDataCreate.restype=C.c_void_p
            self.cf.CFDictionaryCreate.argtypes=[C.c_void_p,C.c_void_p,C.c_void_p,C.c_long,C.c_void_p,C.c_void_p];self.cf.CFDictionaryCreate.restype=C.c_void_p
            self.cf.CFDataGetLength.argtypes=[C.c_void_p];self.cf.CFDataGetLength.restype=C.c_long
            self.cf.CFDataGetBytePtr.argtypes=[C.c_void_p];self.cf.CFDataGetBytePtr.restype=C.c_void_p
            self.cf.CFRelease.argtypes=[C.c_void_p]
            self.sec.SecItemCopyMatching.argtypes=[C.c_void_p,C.POINTER(C.c_void_p)]
            self.sec.SecItemAdd.argtypes=[C.c_void_p,C.c_void_p]
            self.sec.SecItemUpdate.argtypes=[C.c_void_p,C.c_void_p]
            self.sec.SecItemDelete.argtypes=[C.c_void_p]
        except Exception: raise GmailError('credentials') from None
    def const(self, name):return C.c_void_p.in_dll(self.sec if name.startswith('kSec') else self.cf,name).value
    def _run(self, ref, action, value=None):
        if not re.fullmatch('[a-f0-9]{32}',ref): raise GmailError('credentials')
        owned=[]
        def string(x):
            v=self.cf.CFStringCreateWithCString(None,x.encode(),0x08000100);owned.append(v);return v
        def dictionary(pairs):
            keys=(C.c_void_p*len(pairs))(*(k for k,v in pairs));values=(C.c_void_p*len(pairs))(*(v for k,v in pairs))
            v=self.cf.CFDictionaryCreate(None,keys,values,len(pairs),None,None);owned.append(v);return v
        try:
            pairs=[(self.const('kSecClass'),self.const('kSecClassGenericPassword')),(self.const('kSecAttrService'),string(SERVICE)),(self.const('kSecAttrAccount'),string(ref))]
            if action=='get':
                query=dictionary(pairs+[(self.const('kSecReturnData'),self.const('kCFBooleanTrue'))]);result=C.c_void_p()
                if self.sec.SecItemCopyMatching(query,C.byref(result))!=0:raise GmailError('credentials')
                owned.append(result.value);raw=C.string_at(self.cf.CFDataGetBytePtr(result),self.cf.CFDataGetLength(result));return json.loads(raw)
            query=dictionary(pairs)
            if action=='delete':
                if self.sec.SecItemDelete(query) not in (0,-25300):raise GmailError('credentials')
                return
            raw=json.dumps(value,separators=(',',':')).encode();data=self.cf.CFDataCreate(None,raw,len(raw));owned.append(data)
            changes=dictionary([(self.const('kSecValueData'),data)])
            status=self.sec.SecItemUpdate(query,changes)
            if status==-25300:status=self.sec.SecItemAdd(dictionary(pairs+[(self.const('kSecValueData'),data)]),None)
            if status!=0:raise GmailError('credentials')
        except GmailError:raise
        except Exception:raise GmailError('credentials') from None
        finally:
            for value in reversed(owned):
                if value:self.cf.CFRelease(value)
    def get(self,ref):return self._run(ref,'get')
    def put(self,ref,value):self._run(ref,'put',value)
    def delete(self,ref):self._run(ref,'delete')

class FileCredentials:
    """Explicit unencrypted fallback; directory-fd operations also reject swap races."""
    def __init__(self, directory, repository):
        self.root=Path(directory).expanduser().absolute()
        if self.root.resolve().is_relative_to(Path(repository).resolve()):raise GmailError('credentials')
        fd=None
        try:
            fd=os.open('/',os.O_RDONLY|os.O_DIRECTORY)
            for component in self.root.parts[1:]:
                try:child=os.open(component,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd)
                except FileNotFoundError:
                    os.mkdir(component,0o700,dir_fd=fd);child=os.open(component,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd)
                os.close(fd);fd=child
            os.fchmod(fd,0o700);self.fd=fd;fd=None
        except Exception:raise GmailError('credentials') from None
        finally:
            if fd is not None:os.close(fd)
    def __del__(self):
        if hasattr(self,'fd'):
            try:os.close(self.fd)
            except OSError:pass
    def path(self,ref):
        import stat
        if not re.fullmatch('[a-f0-9]{32}',ref):raise GmailError('credentials')
        try:
            if stat.S_ISLNK(os.stat(ref,dir_fd=self.fd,follow_symlinks=False).st_mode):raise GmailError('credentials')
        except FileNotFoundError:pass
        if os.fstat(self.fd).st_mode&0o077:raise GmailError('credentials')
        return self.root/ref
    def get(self,ref):
        try:
            self.path(ref);fd=os.open(ref,os.O_RDONLY|os.O_NOFOLLOW,dir_fd=self.fd)
            with os.fdopen(fd) as f:
                if os.fstat(f.fileno()).st_mode&0o077:raise GmailError('credentials')
                return json.load(f)
        except Exception:raise GmailError('credentials') from None
    def put(self,ref,value):
        temp='.'+secrets.token_hex(16)
        try:
            self.path(ref);fd=os.open(temp,os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW,0o600,dir_fd=self.fd)
            with os.fdopen(fd,'w') as f:json.dump(value,f);f.flush();os.fsync(f.fileno())
            self.path(ref);os.replace(temp,ref,src_dir_fd=self.fd,dst_dir_fd=self.fd);os.fsync(self.fd)
        except Exception:raise GmailError('credentials') from None
        finally:
            try:os.unlink(temp,dir_fd=self.fd)
            except FileNotFoundError:pass
    def delete(self,ref):
        self.path(ref)
        try:os.unlink(ref,dir_fd=self.fd)
        except FileNotFoundError:pass


def client_json(value):
    try:
        if not isinstance(value,dict) or set(value)!={'installed'}:raise ValueError()
        c=value['installed']
        if not isinstance(c,dict) or set(c)-{'client_id','project_id','auth_uri','token_uri','auth_provider_x509_cert_url','client_secret','redirect_uris'}:raise ValueError()
        if c.get('auth_uri')!='https://accounts.google.com/o/oauth2/auth' and c.get('auth_uri')!=AUTH_URL:raise ValueError()
        if c.get('token_uri')!=TOKEN_URL:raise ValueError()
        if c.get('auth_provider_x509_cert_url','https://www.googleapis.com/oauth2/v1/certs')!='https://www.googleapis.com/oauth2/v1/certs':raise ValueError()
        if not isinstance(c.get('redirect_uris'),list) or not c['redirect_uris'] or any(v not in ('http://localhost','http://127.0.0.1') for v in c['redirect_uris']):raise ValueError()
        if not re.fullmatch(r'[A-Za-z0-9._-]{3,250}\.apps\.googleusercontent\.com',c['client_id']):raise ValueError()
        if not isinstance(c['client_secret'],str) or not 1<=len(c['client_secret'])<=512:raise ValueError()
        return {k:c[k] for k in ('client_id','client_secret')}
    except Exception:raise GmailError('client') from None

class OAuth:
    def __init__(self, vault, http=None, clock=time.time):
        self.vault=vault;self.http=http or GoogleHTTP();self.clock=clock;self.sessions={};self.lock=threading.Lock();self.refresh_lock=threading.Lock();self.generation=0
    def begin(self, client_ref, account_ref, expected_account, complete, failed=None):
        client=self.vault.get(client_ref);state=secrets.token_urlsafe(32);verifier=secrets.token_urlsafe(64)
        nonce=secrets.token_hex(16);outer=self
        class Callback(http.server.BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_GET(self):
                try:
                    parsed=urlsplit(self.path);query=parse_qs(parsed.query,keep_blank_values=True)
                    if self.headers.get('Host')!=f'127.0.0.1:{self.server.server_port}' or parsed.path!='/callback/'+nonce or set(query)-{'state','code','scope','authuser','prompt'} or any(len(v)!=1 for v in query.values()):raise GmailError('oauth')
                    outer.finish(state,query.get('state',[''])[0],query.get('code',[''])[0],nonce)
                    status=200;body='授权完成，请返回工作台。'
                except Exception:status=400;body='授权未完成，请返回工作台重新连接。'
                self.send_response(status);self.send_header('Content-Type','text/plain; charset=utf-8');self.send_header('Cache-Control','no-store');self.send_header('Referrer-Policy','no-referrer');self.end_headers();self.wfile.write(body.encode())
        listener=http.server.HTTPServer(('127.0.0.1',0),Callback);redirect=f'http://127.0.0.1:{listener.server_port}/callback/{nonce}'
        self.sessions[state]=dict(verifier=verifier,nonce=nonce,expires=self.clock()+300,generation=self.generation,client=client,account_ref=account_ref,expected=expected_account,complete=complete,failed=failed,redirect=redirect,listener=listener)
        def wait():
            listener.timeout=.25
            while True:
                with self.lock:current=self.sessions.get(state)
                if not current or self.clock()>=current['expires']:break
                listener.handle_request()
            with self.lock:self.sessions.pop(state,None)
            listener.server_close()
        threading.Thread(target=wait,daemon=True).start()
        challenge=base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')
        return AUTH_URL+'?'+urlencode(dict(client_id=client['client_id'],redirect_uri=redirect,response_type='code',scope=READ_SCOPE,access_type='offline',prompt='consent',state=state,code_challenge=challenge,code_challenge_method='S256'))
    def finish(self, session_state, state, code, nonce):
        with self.lock:
            s=self.sessions.get(session_state)
            if not s or not secrets.compare_digest(state,session_state) or not secrets.compare_digest(nonce,s['nonce']) or self.clock()>=s['expires'] or not isinstance(code,str) or not code or len(code)>4096:raise GmailError('oauth')
            self.sessions.pop(session_state)
        # Single use even if provider/Keychain fails; callback has no Origin requirement.
        try:
            result=self.http.request('POST','token',data={**s['client'],'code':code,'code_verifier':s['verifier'],'redirect_uri':s['redirect'],'grant_type':'authorization_code'})
            result=self._tokens(result)
            profile=self.http.request('GET','/profile',token=result['access_token'])
            from gmail_source import account_address
            account=account_address(profile.get('emailAddress'))
            if s['expected'] and account!=s['expected']:raise GmailError('account')
            if not result.get('refresh_token'):raise GmailError('auth')
            with self.lock:
                if s['generation']!=self.generation or self.clock()>=s['expires']:raise GmailError('oauth')
                self.vault.put(s['account_ref'],dict(**result,client=s['client'],account=account))
                s['complete'](account,result['scope'].split(),profile.get('historyId'))
        except Exception as error:
            code=error.code if isinstance(error,GmailError) else 'oauth'
            if s.get('failed'):s['failed'](code)
            raise GmailError(code) from None
    def cancel_all(self):
        with self.lock:self.generation+=1;self.sessions.clear()
    def _tokens(self,result,old=None):
        try:
            scope=result.get('scope',(old or {}).get('scope',''))
            if not isinstance(scope,str) or READ_SCOPE not in scope.split():raise GmailError('scope')
            if any(x!=READ_SCOPE for x in scope.split()):raise GmailError('scope')
            if not isinstance(result['access_token'],str) or not 1<=len(result['access_token'])<=8192:raise ValueError()
            refresh=result.get('refresh_token',(old or {}).get('refresh_token'))
            if not isinstance(refresh,str) or not 1<=len(refresh)<=8192:raise ValueError()
            expires=result.get('expires_in',3600)
            if type(expires) is not int or not 0<expires<=86400:raise ValueError()
            return dict(access_token=result['access_token'],refresh_token=refresh,scope=scope,expires_at=self.clock()+expires)
        except GmailError:raise
        except Exception:raise GmailError('auth') from None
    def access(self, ref, force=False):
        with self.refresh_lock:
            with self.lock:generation=self.generation
            old=self.vault.get(ref)
            if not force and old.get('expires_at',0)>self.clock()+60:return old['access_token']
            value=self.http.request('POST','token',data={**old['client'],'refresh_token':old['refresh_token'],'grant_type':'refresh_token'})
            result={**old,**self._tokens(value,old)}
            with self.lock:
                if generation!=self.generation:raise GmailError('auth')
                self.vault.put(ref,result)
            return result['access_token']
    def identity(self,ref):
        from gmail_source import account_address
        return account_address(self.vault.get(ref).get('account'))
    def read(self,ref,path,query=None):
        token=self.access(ref)
        try:return self.http.request('GET',path,token=token,query=query)
        except GmailError as error:
            if error.code!='auth':raise
        token=self.access(ref,force=True)
        return self.http.request('GET',path,token=token,query=query)
