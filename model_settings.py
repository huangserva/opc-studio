"""Server-side model settings; saved secrets are never returned to the browser."""
import ipaddress
import json
import math
import os
from pathlib import Path
import tempfile
from urllib.parse import urlsplit

DEFAULTS={'model':'','endpoint':'','context_tokens':262144,'max_output_tokens':32768,
          'temperature':0.25,'timeout_seconds':100,'no_api_key':False}

def public_config(config):
    return {**{k:config.get(k,v) for k,v in DEFAULTS.items()},
            'has_api_key':bool(config.get('api_key')),
            'configured':bool(config.get('endpoint') and config.get('model') and (config.get('api_key') or config.get('no_api_key')))}

def validate_config(payload, current):
    if not isinstance(payload,dict):raise ValueError('模型配置格式错误')
    config={k:payload.get(k,current.get(k,v)) for k,v in DEFAULTS.items()}
    for field in ('endpoint','model'):
        if not isinstance(config[field],str):raise ValueError('接口地址和模型名称必须是文本')
        config[field]=config[field].strip()
    if not config['model'] or len(config['model'])>200 or any(ord(c)<32 for c in config['model']):
        raise ValueError('请填写有效模型名称，最多 200 字')
    endpoint=config['endpoint'];u=urlsplit(endpoint)
    if not u.hostname or u.username or u.password or u.query or u.fragment or len(endpoint)>2000 or any(c.isspace() for c in endpoint):
        raise ValueError('请填写完整接口地址，不要把密钥写在地址里')
    if u.scheme!='https':
        local=u.hostname=='localhost'
        try:
            ip=ipaddress.ip_address(u.hostname)
            local=ip.is_loopback or (ip.version==4 and any(ip in ipaddress.ip_network(n) for n in ('10.0.0.0/8','172.16.0.0/12','192.168.0.0/16')))
        except ValueError:pass
        if u.scheme!='http' or not local:raise ValueError('公网接口请使用 HTTPS；HTTP 仅支持本机或局域网 IP')
    if not u.path.rstrip('/').endswith('/chat/completions'):
        raise ValueError('当前支持 Chat Completions 接口，请填写以 /chat/completions 结尾的完整地址')
    for field,lo,hi in [('context_tokens',4096,2097152),('max_output_tokens',1,131072),('timeout_seconds',10,1800)]:
        value=config[field]
        if isinstance(value,bool) or not isinstance(value,int) or not lo<=value<=hi:
            raise ValueError(f'{field} 必须是 {lo} 至 {hi} 的整数')
    t=config['temperature']
    if isinstance(t,bool) or not isinstance(t,(int,float)) or not math.isfinite(t) or not 0<=t<=2:
        raise ValueError('温度必须在 0 至 2 之间')
    if not isinstance(config['no_api_key'],bool):raise ValueError('无密钥选项格式错误')
    key=payload.get('api_key','')
    if not isinstance(key,str) or len(key)>8192 or '\n' in key or '\r' in key:raise ValueError('API 密钥格式错误')
    key=key.strip()
    if config['no_api_key']:
        config['api_key']=''
    elif key:
        config['api_key']=key
    elif endpoint==current.get('endpoint') and current.get('api_key'):
        config['api_key']=current['api_key']
    else:
        raise ValueError('请填写 API 密钥；更换接口地址时需重新填写，避免把旧密钥发送到新地址')
    return config

def save_private(path, config):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    fd,name=tempfile.mkstemp(prefix='.model-',dir=path.parent)
    try:
        os.fchmod(fd,0o600)
        with os.fdopen(fd,'w',encoding='utf-8') as f:
            json.dump(config,f,ensure_ascii=False,indent=2);f.flush();os.fsync(f.fileno())
        os.replace(name,path)
    finally:
        if os.path.exists(name):os.unlink(name)


def profile_document(value):
    """Read legacy single-model files without changing them until the next save."""
    if value.get('schema_version') == 2:
        return value
    if value.get('model'):
        return {'schema_version':2,'active_profile_id':'default',
                'profiles':[{'id':'default','display_name':value['model'],'config':value}]}
    return {'schema_version':2,'active_profile_id':None,'profiles':[]}


def find_profile(document, identity):
    for profile in document['profiles']:
        if profile['id'] == identity:
            return profile
    raise ValueError('该模型配置不存在，请刷新模型列表')


def public_document(document):
    profiles=[{'profile_id':p['id'],'display_name':p.get('display_name') or p['config']['model'],
               **public_config(p['config'])} for p in document['profiles']]
    active=next((p for p in profiles if p['profile_id']==document['active_profile_id']),public_config({}))
    return {**active,'active_profile_id':document['active_profile_id'],'profiles':profiles}
