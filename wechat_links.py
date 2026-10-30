"""Append verified group shares from the source-owned local Radar projection."""
from datetime import datetime, timezone
import hashlib
from html import unescape
import json
from pathlib import Path
import re
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode, quote, urljoin
import xml.etree.ElementTree as ET

from wechat_knowledge import source_db, ZONE

FIELDS = ('document_id', 'url', 'date', 'title', 'chatroom_id', 'chat_name',
          'sender', 'local_id', 'modified', 'hash', 'start', 'end', 'text', 'source_url')
NOTICE = '微信分享记录与解析字段；外部网页正文未读取，分享不代表本人认可或需要行动'
SCOPE = '新增仅群消息、自雷达任务 radar_since 起；历史分享文档原样保留；外部网页正文未读取'
# Match parsed host boundaries and path segments, never a URL substring.
DOWNLOAD_RULES = (
    ('wechat_file', 'wxapp.tc.qq.com', '/'),
    ('wechat_file', 'file.wx.qq.com', '/'),
    ('wechat_image', 'qpic.cn', '/'),
    ('wechat_image', 'qlogo.cn', '/'),
    ('wechat_file', 'weixin.qq.com', '/cgi-bin/mmsupport-bin/stodownload'),
    ('wechat_file', 'weixin.qq.com', '/stodownload'),
    ('wechat_image', 'weixin.qq.com', '/cgi-bin/mmwebwx-bin/webwxgetmsgimg'),
    ('wechat_file', 'weixin.qq.com', '/cgi-bin/mmwebwx-bin/webwxgetmedia'),
    ('wechat_file', 'weixin.qq.com', '/cgi-bin/mmwebwx-bin/webwxgetvideo'),
)
# Same attribution-removal rules as the former message-links.ts normalizeUrl.
TRACKING_PARAMS = frozenset(('utm_source','utm_medium','utm_campaign','utm_term','utm_content',
    'sharer_shareinfo','sharer_shareinfo_first','sharer_sharetime','sharer_userid','sharer_appid',
    'scene','ascene','subscene','clicktime','chksm','key','exportkey','pass_ticket',
    'isappinstalled','snipping_id','snipping_uuid','poc_token','srcid','mpshare',
    'wx_header','uin','sessionid','session_id','x5referer','fromtype'))
ARTICLE_PARAMS = ('__biz','mid','idx','sn')


def web_url(value):
    if not isinstance(value, str) or not value or len(value)>16384 or any(ord(x)<32 for x in value):
        raise ValueError('微信分享链接字段无效；旧索引保留')
    try:
        parsed = urlsplit(value)
        if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError()
        parsed.port
    except ValueError:
        raise ValueError('微信分享链接须为有效的 HTTP(S) URL；旧索引保留') from None
    return parsed


def canonical_url(value):
    """Mirror the former source normalization so existing share keys still match."""
    cleaned=unescape(value).rstrip('),，。；;!?！？、]}>').strip()
    cleaned=re.sub(r'\.{3,}$','',cleaned)
    parsed=web_url(cleaned)
    host=parsed.hostname.lower().encode('idna').decode('ascii')
    netloc=('['+host+']') if ':' in host else host
    if parsed.port and (parsed.scheme,parsed.port) not in (('http',80),('https',443)):
        netloc+=':'+str(parsed.port)
    pairs=parse_qsl(parsed.query,keep_blank_values=True)
    if host=='mp.weixin.qq.com' and re.match(r'^/s/[^/]+',parsed.path):pairs=[]
    elif host=='mp.weixin.qq.com' and any(k in ARTICLE_PARAMS and v for k,v in pairs):
        pairs=[(k,next(v for name,v in pairs if name==k)) for k in ARTICLE_PARAMS if any(name==k and v for name,v in pairs)]
    else:pairs=sorted([(k,v) for k,v in pairs if k.lower() not in TRACKING_PARAMS],key=lambda p:p[0])
    path=urlsplit(urljoin(parsed.scheme+'://'+netloc+'/','/.'+(parsed.path or '/'))).path
    path=quote(path,safe="/:@!$&'()*+,;=-._~%")
    query=urlencode(pairs,safe='*').replace('~','%7E')
    return urlunsplit((parsed.scheme,netloc,path,query,''))


def download_reason(parsed):
    host=parsed.hostname.lower();path=parsed.path.lower()
    for reason,domain,prefix in DOWNLOAD_RULES:
        if host!=domain and not host.endswith('.'+domain):continue
        if prefix=='/' or path==prefix or path.startswith(prefix+'/'):return reason
    return None


def share_title(row,body):
    def useful(value):
        text=' '.join(str(value or '').split()).strip()
        return text[:160] if text and not re.match(r'^wxid(?:_|\b)',text,re.I) else ''
    title=useful(row.get('title'))
    # The projection uses a hostname when a plain URL has no card title.
    if title==web_url(row['url']).hostname:title=''
    if title:return title,'projection'
    if '<msg' in body or '<?xml' in body:
        try:
            root=ET.fromstring(body[body.index('<'):])
            for tag in ('title','des','digest'):
                candidate=next((useful(n.text) for n in root.iter() if n.tag==tag and useful(n.text)),'')
                if candidate:return candidate,'message_card'
        except (ET.ParseError,ValueError):pass
    else:
        candidate=useful(re.sub(r'https?://[^\s<>]+',' ',body))
        if candidate:return candidate,'message_excerpt'
    return '无标题','untitled'


def import_links(knowledge, index, source, identify):
    parent_id = source.get('message_source', 'wechat')
    parent = index.execute('SELECT config,status FROM sources WHERE id=?', (parent_id,)).fetchone()
    if parent is None:raise ValueError('先接入本机微信原消息，再更新分享链接')
    config = json.loads(parent['config'])
    if config.get('enabled') is False:
        return '原消息来源已暂停；已有分享链接索引保留，未读取雷达', False
    if config.get('kind')!='wechat' or parent['status'] not in ('ready', 'partial', 'empty'):
        raise ValueError('先完成本机微信原消息索引，再更新分享链接；旧链接索引保留')
    # Never resume the obsolete radar.db writer, or treat its path as this schema.
    path = (Path(knowledge.store.workspace)/'radar-analysis/assistant.db').resolve()
    counts={k:0 for k in ('candidates','added','duplicate','unmatched','private','wechat_file','wechat_image','sensitive','invalid_url')}
    titles={};seen=set();first=last=None
    # Fingerprints, bodies, titles and provenance of existing shares stay untouched.
    # Returning incomplete also prevents Knowledge.sync from pruning history.
    with source_db(path) as radar:
        if not set(FIELDS).issubset({r[1] for r in radar.execute('PRAGMA table_info(radar_links)')}):
            raise ValueError('微信雷达分享链接结构不匹配；旧索引保留')
        for values in radar.execute('SELECT '+','.join(FIELDS)+' FROM radar_links ORDER BY modified,document_id,url'):
            row = dict(zip(FIELDS, values));counts['candidates']+=1
            chat,local=row['chatroom_id'],row['local_id']
            if not isinstance(chat,str) or not chat or '\x00' in chat or type(local) is not int or local<=0:
                raise ValueError('雷达分享记录缺少会话、消息编号或时间；旧索引保留')
            if not chat.endswith('@chatroom'):counts['private']+=1;continue
            try:
                parsed=web_url(row['url']);canonical=canonical_url(row['url'])
            except (ValueError,UnicodeError):
                counts['invalid_url']+=1;continue
            reason=download_reason(parsed)
            if reason:counts[reason]+=1;continue
            if re.search(r'token|credential|password|secret|authkey|encryptkey',parsed.query,re.I):
                counts['sensitive']+=1;continue
            try:
                stamp=datetime.fromisoformat(row['modified'].replace('Z','+00:00'))
                if stamp.tzinfo is None:raise ValueError()
                ts=int(stamp.timestamp())
                if not 0<ts<253402300799:raise ValueError()
            except (ValueError,TypeError,AttributeError,OverflowError):
                raise ValueError('雷达分享时间无效；旧索引保留') from None
            original=index.execute('SELECT id,hash,body,provenance FROM documents WHERE id=? AND source=? AND kind=?',
                (row['document_id'],parent_id,'wechat')).fetchone()
            if (original is None or original['id']!=identify(parent_id,f'{chat}:{local}')
                    or original['hash']!=row['hash'] or json.loads(original['provenance']).get('timestamp')!=ts):
                counts['unmatched']+=1;continue
            start,end=row['start'],row['end']
            if (type(start) is not int or type(end) is not int or not 0<=start<end<=len(original['body'])
                    or original['body'][start:end]!=row['text'] or unescape(row['text'])!=row['url']):
                counts['unmatched']+=1;continue
            key=json.dumps([chat,local,canonical],ensure_ascii=False,separators=(',',':'))
            if key in seen or index.execute('SELECT 1 FROM documents WHERE id=? AND source=?',(identify(source['id'],key),source['id'])).fetchone():
                counts['duplicate']+=1;continue
            seen.add(key)
            title,title_kind=share_title(row,original['body']);titles[title_kind]=titles.get(title_kind,0)+1
            date=stamp.astimezone(ZONE).date().isoformat()
            reference={'id':original['id'],'hash':original['hash'],'start':start,'end':end}
            origin='/knowledge.html?'+urlencode({'document':reference['id'],'hash':reference['hash'],'start':start,'end':end})
            # Missing former fields remain absent; none are guessed from chat identity.
            record={**row,'canonical_url':canonical,'timestamp':ts,'description':'','raw_kind':'radar_projection',
                'confidence':None,'created_at':datetime.now(timezone.utc).isoformat()}
            fingerprint=hashlib.sha256(json.dumps(record,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
            provenance={'chatroom_id':chat,'local_id':local,'timestamp':ts,'database_path':str(path),
                'source_locator':json.dumps({'table':'radar_links','key':[row['document_id'],row['url']]},ensure_ascii=False),
                'content_scope':NOTICE+'；'+SCOPE,'sourceUrl':canonical,'source_record_hash':fingerprint,
                'source_record':record,'original_message_reference':reference,'original_message_url':origin,'title_basis':title_kind}
            body='\n\n'.join([NOTICE,SCOPE,'分享链接（雷达已规范化）：'+canonical,'分享记录指纹：'+fingerprint])
            knowledge._write(index,source['id'],key,'wechat_links',title,body,canonical,stamp.astimezone(timezone.utc).isoformat(),[date,parsed.hostname,'分享链接'],provenance)
            counts['added']+=1;first=date if first is None or date<first else first;last=date if last is None or date>last else last
    detail='新雷达投影：'+SCOPE+'；'+f'{first or "无新增"} 至 {last or "无新增"}；'+json.dumps({'counts':counts,'titles':titles},ensure_ascii=False,sort_keys=True)
    return detail,False
