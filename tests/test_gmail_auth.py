"""Synthetic OAuth provider and adversarial tests. Never reads real credentials."""
import base64,copy,hashlib,io,json,os,secrets,tempfile,time,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import parse_qs,urlsplit
from urllib.error import HTTPError
from gmail_auth import OAuth,READ_SCOPE,GmailError,GoogleHTTP,client_json,FileCredentials

FAKE_ACCESS='SYNTHETIC_ACCESS_NOT_A_CREDENTIAL'
FAKE_REFRESH='SYNTHETIC_REFRESH_NOT_A_CREDENTIAL'
ACCOUNT='owner@example.test'
CLIENT={'installed':dict(client_id='synthetic.apps.googleusercontent.com',client_secret='SYNTHETIC_CLIENT_NOT_A_SECRET',auth_uri='https://accounts.google.com/o/oauth2/auth',token_uri='https://oauth2.googleapis.com/token',redirect_uris=['http://localhost'])}
class MemoryVault:
    def __init__(self):self.values={};self.locked=False
    def get(self,ref):
        if self.locked or ref not in self.values:raise GmailError('credentials')
        return copy.deepcopy(self.values[ref])
    def put(self,ref,value):
        if self.locked:raise GmailError('credentials')
        self.values[ref]=copy.deepcopy(value)
    def delete(self,ref):self.values.pop(ref,None)
class Provider:
    def __init__(self,clock=None):
        self.clock=clock or (lambda:1793876400);self.account=ACCOUNT;self.watermark='900719925474099312341';self.calls=[];self.codes={};self.messages={};self.fail={};self.events=[];self.scope=READ_SCOPE;self.refresh_count=0;self.hook=None
    def issue(self,challenge):
        code=secrets.token_hex(16);self.codes[code]=challenge;return code
    def message(self,mid='aa01',labels=None,body='SYNTHETIC_MAIL_BODY',date=None):
        return dict(id=mid,threadId=mid,historyId=self.watermark,internalDate=str(int(date or self.clock())*1000),labelIds=labels if labels is not None else ['INBOX'],sizeEstimate=100000000,payload=dict(mimeType='text/plain',headers=[dict(name='Subject',value='SYNTHETIC_SUBJECT_'+mid)],body=dict(size=len(body),data=base64.urlsafe_b64encode(body.encode()).decode())))
    def request(self,method,path,*,data=None,token=None,query=None):
        self.calls.append((method,path,copy.deepcopy(query or {})))
        if self.hook:self.hook(method,path)
        failure=self.fail.get(path)
        if failure:
            if isinstance(failure,list):
                code=failure.pop(0)
                if not failure:self.fail.pop(path)
                if code:raise GmailError(code)
            else:raise GmailError(failure)
        if path=='token':
            if data.get('grant_type')=='authorization_code':
                challenge=self.codes.pop(data.get('code'),None);actual=base64.urlsafe_b64encode(hashlib.sha256(data.get('code_verifier','').encode()).digest()).decode().rstrip('=')
                if not challenge or challenge!=actual:raise GmailError('oauth')
                return dict(access_token=FAKE_ACCESS,refresh_token=FAKE_REFRESH,scope=self.scope,expires_in=3600)
            self.refresh_count+=1;return dict(access_token=FAKE_ACCESS,scope=self.scope,expires_in=3600)
        if path=='/profile':return dict(emailAddress=self.account,historyId=self.watermark)
        if path=='/history':
            page=int((query or {}).get('pageToken',0));pages=self.events or [dict(history=[],historyId=self.watermark)];return copy.deepcopy(pages[page])
        if path=='/labels':return dict(labels=[dict(id='INBOX',name='收件箱')])
        if path=='/messages':
            start=int((query or {}).get('pageToken',0));limit=int((query or {}).get('maxResults',100));ids=sorted(self.messages);selected=ids[start:start+limit];result=dict(messages=[dict(id=x) for x in selected],resultSizeEstimate=999999)
            if start+limit<len(ids):result['nextPageToken']=str(start+limit)
            return result
        if path.startswith('/messages/'):
            mid=path.rsplit('/',1)[1]
            if mid not in self.messages:raise GmailError('page')
            return copy.deepcopy(self.messages[mid])
        raise GmailError('boundary')

class AuthTests(unittest.TestCase):
    def setUp(self):
        self.now=[1000.];self.v=MemoryVault();self.p=Provider();self.o=OAuth(self.v,self.p,lambda:self.now[0]);self.cref='a'*32;self.ref='b'*32;self.v.put(self.cref,client_json(CLIENT));self.completed=[];self.addCleanup(self.o.sessions.clear)
    def begin(self,expected=ACCOUNT):
        url=self.o.begin(self.cref,self.ref,expected,lambda *x:self.completed.append(x));q={k:v[0] for k,v in parse_qs(urlsplit(url).query).items()};s=self.o.sessions[q['state']];code=self.p.issue(q['code_challenge']);return q,s,code
    def finish(self,q,s,code):return self.o.finish(q['state'],q['state'],code,s['nonce'])
    def test_state_wrong_expired_replay_and_nonce(self):
        q,s,code=self.begin()
        for state,nonce in [('wrong',s['nonce']),(q['state'],'wrong')]:
            with self.assertRaises(GmailError):self.o.finish(q['state'],state,code,nonce)
        self.finish(q,s,code);self.assertEqual(len(self.completed),1)
        with self.assertRaises(GmailError):self.finish(q,s,code)
        q,s,code=self.begin();self.now[0]+=301
        with self.assertRaises(GmailError):self.finish(q,s,code)
    def test_pkce_wrong_and_fake_callback(self):
        q,s,code=self.begin();s['verifier']='forged'
        with self.assertRaises(GmailError):self.finish(q,s,code)
        self.assertNotIn(self.ref,self.v.values)
        with self.assertRaises(GmailError):self.o.finish('absent','absent','forged','forged')
    def test_wrong_account_missing_scope_and_extra_scope(self):
        for account,scope in [('other@example.test',READ_SCOPE),(ACCOUNT,''),(ACCOUNT,READ_SCOPE+' https://www.googleapis.com/auth/gmail.send')]:
            with self.subTest(account=account,scope=scope):
                self.p.account=account;self.p.scope=scope;q,s,code=self.begin()
                with self.assertRaises(GmailError):self.finish(q,s,code)
                self.assertNotIn(self.ref,self.v.values)
    def test_desktop_and_endpoint_allowlist(self):
        self.assertIn('client_id',client_json(CLIENT))
        for key in ['auth_uri','token_uri','auth_provider_x509_cert_url','redirect_uris']:
            c=copy.deepcopy(CLIENT);c['installed'][key]=['https://evil.test'] if key=='redirect_uris' else 'https://evil.test'
            with self.assertRaises(GmailError):client_json(c)
        with self.assertRaises(GmailError):client_json({'web':CLIENT['installed']})
        for method,path in [('POST','/messages/send'),('GET','https://evil.test'),('GET','/messages/../profile')]:
            with self.assertRaises(GmailError):GoogleHTTP().request(method,path)
    def test_refresh_retains_refresh_and_401_only_once(self):
        q,s,code=self.begin();self.finish(q,s,code);self.now[0]+=4000;self.o.access(self.ref);self.assertEqual(self.v.get(self.ref)['refresh_token'],FAKE_REFRESH)
        self.p.fail['/profile']=['auth','auth']
        with self.assertRaises(GmailError):self.o.read(self.ref,'/profile')
        self.assertEqual(self.p.refresh_count,2)
    def test_invalid_grant_and_keychain_no_plaintext_fallback(self):
        q,s,code=self.begin();self.finish(q,s,code);self.p.fail['token']='auth'
        with self.assertRaises(GmailError):self.o.access(self.ref,force=True)
        self.v.locked=True
        with self.assertRaisesRegex(GmailError,'无法读取本机凭据'):self.o.access(self.ref)
    def test_file_storage_permissions_symlinks_and_repository(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t).resolve();repo=root/'repo';repo.mkdir();v=FileCredentials(root/'external',repo);v.put(self.ref,dict(synthetic=FAKE_ACCESS));self.assertEqual(v.get(self.ref)['synthetic'],FAKE_ACCESS);self.assertEqual(v.root.stat().st_mode&0o777,0o700);self.assertEqual((v.root/self.ref).stat().st_mode&0o777,0o600)
            (v.root/self.ref).unlink();(v.root/self.ref).symlink_to(root/'elsewhere')
            with self.assertRaises(GmailError):v.put(self.ref,{})
            with self.assertRaises(GmailError):FileCredentials(repo/'creds',repo)
    def test_provider_errors_do_not_include_values_and_403_distinction(self):
        for status,body,kind in [(400,{'error':'invalid_grant'},'auth'),(403,{'error':{'message':FAKE_ACCESS,'errors':[{'reason':'domainPolicy'}]}},'policy'),(403,{'error':{'message':FAKE_ACCESS,'errors':[{'reason':'rateLimitExceeded'}]}},'rate'),(429,{},'rate'),(500,{'message':FAKE_ACCESS},'network')]:
            opener=SimpleNamespace(open=lambda *a,**k:(_ for _ in ()).throw(HTTPError('https://fixture.invalid',status,FAKE_ACCESS,{},io.BytesIO(json.dumps(body).encode()))))
            with patch('gmail_auth.build_opener',return_value=opener):
                with self.assertRaises(GmailError) as caught:GoogleHTTP().request('POST','token',data={})
            self.assertEqual(caught.exception.code,kind);self.assertNotIn(FAKE_ACCESS,str(caught.exception))

    def test_disconnect_during_exchange_and_refresh_cannot_resurrect_credentials(self):
        import threading
        for operation in ['exchange','refresh']:
            with self.subTest(operation=operation):
                self.setUp();q,s,code=self.begin();entered=threading.Event();release=threading.Event()
                if operation=='refresh':self.finish(q,s,code)
                def hook(method,path):
                    if path=='token':entered.set();release.wait(2)
                self.p.hook=hook;errors=[]
                def work():
                    try:self.finish(q,s,code) if operation=='exchange' else self.o.access(self.ref,force=True)
                    except GmailError as e:errors.append(e.code)
                thread=threading.Thread(target=work);thread.start();self.assertTrue(entered.wait(2));self.o.cancel_all();self.v.delete(self.ref);release.set();thread.join();self.assertNotIn(self.ref,self.v.values);self.assertTrue(errors)

    def test_loopback_callback_wrong_host_and_state_return_safe_400(self):
        import urllib.request,urllib.error
        q,s,code=self.begin();url=s['redirect']+'?state=wrong&code='+code
        for host in [None,'evil.test']:
            headers={'Host':host} if host else {}
            with self.assertRaises(urllib.error.HTTPError) as caught:urllib.request.urlopen(urllib.request.Request(url,headers=headers),timeout=3)
            self.assertEqual(caught.exception.code,400);self.assertNotIn(FAKE_ACCESS.encode(),caught.exception.read());caught.exception.close()
        self.assertNotIn(self.ref,self.v.values)

    def test_native_keychain_locked_statuses_are_safe_without_native_access(self):
        from gmail_auth import Keychain
        from types import SimpleNamespace
        chain=Keychain.__new__(Keychain);released=[];counter=iter(range(10,1000))
        chain.const=lambda name:1
        chain.cf=SimpleNamespace(CFStringCreateWithCString=lambda *a:next(counter),CFDictionaryCreate=lambda *a:next(counter),CFDataCreate=lambda *a:next(counter),CFRelease=released.append)
        chain.sec=SimpleNamespace(SecItemCopyMatching=lambda *a:-25308,SecItemUpdate=lambda *a:-25308,SecItemAdd=lambda *a:(_ for _ in ()).throw(AssertionError('must not add on lock')),SecItemDelete=lambda *a:-25308)
        for action in [lambda:chain.get(self.ref),lambda:chain.put(self.ref,{'synthetic':FAKE_ACCESS}),lambda:chain.delete(self.ref)]:
            with self.assertRaisesRegex(GmailError,'无法读取本机凭据'):action()
        self.assertTrue(released)
