"""One reusable official-account decision: overrides > Fx profile > Grok type.

Only public handle/verification/count/time fields enter the profile cache.
Pages never wait for the rate-limited background profile worker.
"""
from platform_registry import derive_platform
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import logging
import math
import os
from pathlib import Path
import queue
import re
import stat
import tempfile
import threading
import time
import urllib.request

ROOT = Path(__file__).resolve().parent
DEFAULT_INCLUDE = ['factoryai', 'pika_labs', 'comfyui', 'opencode', 'openclaw', 'higgsfield']
SUCCESS_TTL = 86400
FAILURE_TTL = 300
PROFILE_TIMEOUT = 2
RATE_SECONDS = 1
LOG = logging.getLogger(__name__)

class Conflict(ValueError): pass

def workspace(root):
    root=Path(root).resolve()
    return root/'workspace' if (root/'workspace').is_dir() else root

def stamp():return datetime.now(timezone.utc).isoformat()

def handle(value):
    if not isinstance(value,str):raise ValueError('账号须为文本')
    value=value.removeprefix('@')
    if not re.fullmatch(r'[A-Za-z0-9_]{1,15}',value):raise ValueError('账号只能含1–15位英文字母、数字、下划线，不能有空白')
    return value.casefold()

def _list(values):
    if not isinstance(values,list) or len(values)>500:raise ValueError('名单最多500个账号')
    result=[handle(v) for v in values]
    if len(set(result))!=len(result):raise ValueError('账号不能重复（不区分大小写）')
    return result

def path_for(root):return workspace(root)/'settings/official-accounts.json'

def read_rules(root):
    path=path_for(root)
    if path.is_symlink() or path.parent.is_symlink():raise ValueError('名单不能是符号链接')
    try:fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
    except FileNotFoundError:return dict(include=list(DEFAULT_INCLUDE),exclude=[],history=[],revision='missing')
    with os.fdopen(fd,'rb') as stream:
        info=os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size>1048576:raise ValueError('名单文件无效')
        raw=stream.read(1048577)
    data=json.loads(raw)
    if not isinstance(data,dict) or set(data)!={'version','include','exclude','history'} or data['version']!=1:raise ValueError('名单格式无效')
    include,exclude=_list(data['include']),_list(data['exclude'])
    if not isinstance(data['history'],list):raise ValueError('变更记录无效')
    return dict(include=include,exclude=exclude,history=data['history'],revision=hashlib.sha256(raw).hexdigest())

def _atomic(path,data,mode=0o600):
    path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    fd,name=tempfile.mkstemp(prefix='.official-',dir=path.parent)
    try:
        with os.fdopen(fd,'w') as stream:
            os.fchmod(stream.fileno(),mode);json.dump(data,stream,ensure_ascii=False,indent=2);stream.write('\n');stream.flush();os.fsync(stream.fileno())
        os.replace(name,path)
    finally:
        if os.path.exists(name):os.unlink(name)

def save_rules(root,payload):
    if not isinstance(payload,dict) or set(payload)!={'include','exclude','revision'} or not isinstance(payload['revision'],str):raise ValueError('只允许include、exclude和revision')
    include,exclude=_list(payload['include']),_list(payload['exclude'])
    path=path_for(root)
    if path.is_symlink() or path.parent.is_symlink():raise ValueError('名单不能是符号链接')
    path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    lock=os.open(path.parent/'.official-accounts.lock',os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600)
    try:
        fcntl.flock(lock,fcntl.LOCK_EX);before=read_rules(root)
        if before['revision']!=payload['revision']:raise Conflict('名单已被修改，请刷新后重试')
        changes=[];when=stamp()
        for key,new in [('include',include),('exclude',exclude)]:
            for account in sorted(set(before[key])-set(new)):changes.append(dict(at=when,action='remove_'+key,handle=account))
            for account in sorted(set(new)-set(before[key])):changes.append(dict(at=when,action='add_'+key,handle=account))
        if changes:
            if read_rules(root)['revision']!=before['revision']:raise Conflict('名单已被修改，请刷新后重试')
            _atomic(path,dict(version=1,include=include,exclude=exclude,history=before['history']+changes),stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o600)
        return read_rules(root)
    finally:os.close(lock)

def number(value):
    if isinstance(value,bool):return None
    try:value=float(value)
    except (ValueError,TypeError):return None
    return value if math.isfinite(value) and value>=0 else None

def fetch_profile(account):
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self,*args,**kwargs):return None
    opener=urllib.request.build_opener(NoRedirect)
    request=urllib.request.Request('https://api.fxtwitter.com/'+handle(account),headers={'User-Agent':'article-studio-official/1.0','Accept':'application/json'})
    with opener.open(request,timeout=PROFILE_TIMEOUT) as response:
        data=json.loads(response.read(262145))
    user=data['user']
    if data.get('code')!=200 or handle(user['screen_name'])!=account:raise ValueError('profile_identity')
    verification=user.get('verification') or {}
    return dict(verification_type=verification.get('type') if verification.get('type') in ('organization','government','individual') else None,followers=number(user.get('followers')))

class Profiles:
    def __init__(self,root,*,fetcher=fetch_profile,network=None):
        self.root=workspace(root);self.path=self.root/'official-accounts/profiles.json';self.fetcher=fetcher
        self.network=self.root==ROOT/'workspace' if network is None else network
        self.lock=threading.Lock();self.pending=set();self.records={};self.jobs=queue.Queue(512);self.worker=None
        try:
            if not self.path.is_symlink() and not self.path.parent.is_symlink() and self.path.stat().st_size<=2097152:
                data=json.loads(self.path.read_text())
                for account,record in data.items():
                    if handle(account)==account and isinstance(record,dict) and type(record.get('expires')) in (int,float):
                        self.records[account]={k:record.get(k) for k in ('verification_type','followers','checked_at','expires','state')}
        except (OSError,ValueError,TypeError,AttributeError):pass

    def lookup(self,account):
        with self.lock:
            value=self.records.get(account)
            if value and value['expires']>time.time():return dict(value)
            if self.network and account not in self.pending and len(self.pending)<500:
                self.pending.add(account);self.jobs.put_nowait(account)
                if self.worker is None:
                    self.worker=threading.Thread(target=self._run,name='official-profiles',daemon=True);self.worker.start()
        return dict(state='unknown',followers=None,verification_type=None,checked_at=None)

    def _run(self):
        while True:
            try:account=self.jobs.get_nowait()
            except queue.Empty:
                with self.lock:
                    if self.jobs.empty():self.worker=None;return
                continue
            began=time.monotonic()
            try:
                result=self.fetcher(account)
                if not isinstance(result,dict):raise ValueError('profile_schema')
                result=dict(state='known',verification_type=result.get('verification_type') if result.get('verification_type') in ('organization','government','individual') else None,followers=number(result.get('followers')),checked_at=stamp(),expires=time.time()+SUCCESS_TTL)
            except Exception as exc:
                LOG.warning('official_profile_unknown account=%s error=%s',account,type(exc).__name__)
                result=dict(state='unknown',verification_type=None,followers=None,checked_at=stamp(),expires=time.time()+FAILURE_TTL)
            with self.lock:
                self.records[account]=result;self.pending.discard(account);saved=dict(self.records)
            try:
                if self.path.is_symlink() or self.path.parent.is_symlink():raise ValueError('profile_cache_link')
                _atomic(self.path,saved)
            except (OSError,ValueError):LOG.warning('official_profile_cache_unavailable')
            time.sleep(max(0,RATE_SECONDS-(time.monotonic()-began)))

_profiles_lock=threading.Lock();_profiles={}
def profiles(root):
    key=str(workspace(root))
    with _profiles_lock:
        if key not in _profiles:_profiles[key]=Profiles(root)
        return _profiles[key]

def decision(item,rules,profile):
    author=item.get('author') or {}
    try:account=handle(author.get('handle',''))
    except ValueError:account=''
    is_x=derive_platform(item.get('id'))=='x'
    official=False;source='unknown';reason='官方身份未知'
    if not is_x:reason='非X账号'
    elif account in rules['exclude']:source='settings_exclude';reason='设置名单强制排除'
    elif account in rules['include']:official=True;source='settings_include';reason='设置名单强制算进'
    elif profile.get('verification_type')=='organization':official=True;source='fxtwitter_organization';reason='X认证类型 organization（本地缓存）'
    else:
        author_type=item.get('author_type')
        if isinstance(author_type,str) and author_type.strip().casefold() in ('official','person'):
            official=author_type.strip().casefold()=='official';source='grok_author_type';reason='Grok author_type='+author_type.strip().casefold()
    followers=number(author.get('followers'));followers_source='post'
    if followers is None:followers=number(profile.get('followers'));followers_source='fxtwitter_profile'
    views=number((item.get('metrics') or {}).get('views'))
    breakout=bool(official and views is not None and views>=100000 and followers is not None and views>followers)
    eligibility=('浏览≥10万且超过自身粉丝数' if breakout else '粉丝数缺失，按不满足火爆标准处理' if official and followers is None else '需同时满足浏览≥10万且超过自身粉丝数' if official else '')
    return dict(is_official=official,source=source,reason=reason,hot=breakout,followers=followers,followers_source=followers_source,eligibility=eligibility,profile_state=profile.get('state','unknown'),profile_checked_at=profile.get('checked_at'))

def annotate(items,root,*,hints=None):
    rules=read_rules(root);cache=profiles(root)
    for item in items:
        try:account=handle((item.get('author') or {}).get('handle',''))
        except ValueError:account=''
        if hints and item['id'] in hints:item.update(hints[item['id']])
        profile=cache.lookup(account) if account and derive_platform(item.get('id'))=='x' else {}
        item['official']=decision(item,rules,profile)
        if item['official']['is_official']:item['hot']=item['official']['hot']
    return items

def interest_hints():
    # The current interest reader intentionally narrows its schema; keep this
    # author metadata adapter separate from the single decision above.
    import grok_bot_radar as grok
    result={};orders={}
    folder=grok.reports_dir()/'interest'
    if folder.is_symlink():return result
    for path in sorted(folder.glob('*.json')):
        try:
            if path.is_symlink() or path.stat().st_size>8388608:continue
            data=json.loads(path.read_text());rows=data if isinstance(data,list) else next((data[k] for k in ('posts','entries','items') if isinstance(data.get(k),list)),[])
            for row in rows:
                if not isinstance(row,dict):continue
                identifier='x:'+str(row.get('id',''));fallback=datetime.strptime(path.stem,'%Y-%m-%d-%H%M').replace(tzinfo=grok.BEIJING)
                order=((grok.stamp(row.get('collected_at')) or fallback).timestamp(),path.name)
                if identifier not in orders or order>orders[identifier]:result[identifier]={k:row[k] for k in ('author_type','author_verification_type') if k in row};orders[identifier]=order
        except (OSError,ValueError,AttributeError,TypeError):continue
    return result
