"""Index existing Radar share records; never fetch pages or call a model."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from urllib.parse import urlsplit, urlencode

from wechat_knowledge import source_db, ZONE

FIELDS = ('chatroom_id', 'local_id', 'date', 'sender', 'time', 'timestamp',
          'url', 'canonical_url', 'title', 'description', 'domain', 'source',
          'raw_kind', 'confidence', 'created_at')
NOTICE = '微信分享记录与解析字段；外部网页正文未读取，分享不代表本人认可或需要行动'


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


def import_links(knowledge, index, source, identify):
    parent_id = source.get('message_source', 'wechat')
    parent = index.execute('SELECT config,status FROM sources WHERE id=?', (parent_id,)).fetchone()
    if parent is None:
        raise ValueError('先接入本机微信原消息，再更新分享链接')
    config = json.loads(parent['config'])
    if config.get('enabled') is False:
        return '原消息来源已暂停；已有分享链接索引保留，未读取雷达', False
    if config.get('kind')!='wechat' or parent['status'] not in ('ready', 'partial', 'empty'):
        raise ValueError('先完成本机微信原消息索引，再更新分享链接；旧链接索引保留')
    path = Path(source['path']).resolve()
    count = omitted = unsupported = 0
    first = last = None
    with source_db(path) as radar:
        if not set(FIELDS).issubset({r[1] for r in radar.execute('PRAGMA table_info(message_links)')}):
            raise ValueError('微信雷达分享链接结构不匹配；旧索引保留')
        for values in radar.execute('SELECT '+','.join(FIELDS)+' FROM message_links ORDER BY timestamp,chatroom_id,local_id,canonical_url'):
            row = dict(zip(FIELDS, values))
            # Search and manually added resources do not prove a WeChat share.
            if row['source'] not in ('plain_url', 'wechat_raw'):
                unsupported += 1
                continue
            chat, local, ts = row['chatroom_id'], row['local_id'], row['timestamp']
            if not isinstance(chat,str) or not chat or '\x00' in chat or type(local) is not int or local<=0 or type(ts) is not int or not 0<ts<253402300799:
                raise ValueError('雷达分享记录缺少会话、消息编号或时间；旧索引保留')
            original = index.execute('SELECT id,hash,body,provenance FROM documents WHERE id=? AND source=?', (identify(parent_id,f'{chat}:{local}'),parent_id)).fetchone()
            if original is None or not original['body'] or json.loads(original['provenance']).get('timestamp')!=ts:
                omitted += 1
                continue
            parsed = web_url(row['canonical_url'])
            web_url(row['url'])
            for field in ('title','description','raw_kind'):
                value = row[field]
                if value is not None and (not isinstance(value,str) or '\x00' in value or len(value)>20000):
                    raise ValueError('雷达分享字段格式异常；旧索引保留')
            canonical = row['canonical_url']
            title = (row['title'] or '').strip() or '微信分享 · '+parsed.hostname
            lines = [NOTICE, '分享链接（雷达已规范化）：'+canonical]
            if row['title']:lines.append('雷达记录的标题：'+row['title'])
            if row['description']:lines.append('雷达记录的描述：'+row['description'])
            lines.append('解析方式：'+('分享卡片字段' if row['source']=='wechat_raw' else '消息中的明文链接'))
            stamp = datetime.fromtimestamp(ts,timezone.utc).isoformat()
            day = datetime.fromtimestamp(ts,ZONE).strftime('%Y-%m-%d')
            reference = {'id':original['id'],'hash':original['hash'],'start':0,'end':len(original['body'])}
            origin = '/knowledge.html?'+urlencode({'document':reference['id'],'hash':reference['hash'],'start':0,'end':reference['end']})
            # Retain the full local row without exposing share-attribution URL
            # parameters in model metadata. The public entry is canonical URL.
            record_hash = hashlib.sha256(json.dumps(row,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
            lines.append('分享记录指纹：'+record_hash)
            provenance = {'chatroom_id':chat,'local_id':local,'timestamp':ts,
                'database_path':str(path),'source_locator':json.dumps({'table':'message_links','key':[chat,local,canonical]},ensure_ascii=False),
                'content_scope':NOTICE,'sourceUrl':canonical,'source_record_hash':record_hash,
                'source_record':row,'original_message_reference':reference,'original_message_url':origin}
            key = json.dumps([chat,local,canonical],ensure_ascii=False,separators=(',',':'))
            knowledge._write(index,source['id'],key,'wechat_links',title,'\n\n'.join(lines),canonical,stamp,[day,parsed.hostname,'分享链接'],provenance)
            count += 1
            first = day if first is None or day<first else first
            last = day if last is None or day>last else last
    return f'雷达已有分享记录：{first or "无"} 至 {last or "无"}，{count:,} 条；仅匹配当前微信索引的会话、编号和时间。未匹配 {omitted:,} 条，其它来源 {unsupported:,} 条未导入；外部网页正文未读取', True
