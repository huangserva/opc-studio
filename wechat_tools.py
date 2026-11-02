"""WeChat group tools: reference-only extraction, local inference, K1 import CLI."""
from __future__ import annotations
from datetime import datetime, timedelta, timezone
from contextlib import closing, contextmanager
from fcntl import flock, LOCK_EX, LOCK_NB
import os
import argparse
import hashlib
import html
import ipaddress
import json
from pathlib import Path
import re
import socket
import sqlite3
import sys
import urllib.error
import urllib.request
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from zoneinfo import ZoneInfo
import clue_store_v2 as S
import clue_ingest as I
import wechat_tools_config as config

ZONE = ZoneInfo('Asia/Shanghai')
POLICY = 'wechat-tools-v1'
MIN_RELEVANCE = 2
NUMBER = re.compile(r'^\s*(?:([①②③])|([1-3])[.、)）])\s*(.+?)\s*$')
HEADER = re.compile(r'^[^\w\u4e00-\u9fff]*小耳\s*Today\b.*?(?:(\d{4})[年/.-])?(\d{1,2})[月/.-](\d{1,2})', re.I)
URL = re.compile(r'https?://[^\s<>"\[\]]+')
FORBIDDEN = re.compile(r'(?i)wxid_[\w-]+|[\w-]+@chatroom|\bsk-(?:proj-)?[\w-]{12,}|(?:api[_-]?key|access[_-]?token|authorization|password)\s*[:=]\s*[^\s,;]+')
FIELDS = ('name','purpose','usage','classification','version')


def public_text(value, forbidden=()):
    value = S.safe_text(value if isinstance(value, str) else '')
    value = FORBIDDEN.sub('[私密信息已隐藏]', value)
    for name in forbidden:
        if isinstance(name, str) and name:
            value = value.replace(name, '[成员已隐藏]')
    return value.strip()


def tool_url(value):
    if not isinstance(value,str) or FORBIDDEN.search(value):return None
    value = S.safe_url(str(value).rstrip('，。；、）)'))
    if not value:
        return None
    p = urlsplit(value); host = (p.hostname or '').lower().rstrip('.')
    try: port=p.port
    except ValueError:return None
    blocked = ('douyin.com','iesdouyin.com','weibo.com','weibo.cn','mp.weixin.qq.com','weixin.qq.com','wx.qq.com','qq.com','xiaohongshu.com','xhslink.com')
    if any(host == x or host.endswith('.'+x) for x in blocked):
        return None
    try:
        if not ipaddress.ip_address(host).is_global:
            return None
    except ValueError:
        if host == 'localhost' or '.' not in host:
            return None
    if re.search(r'(?i)\.(?:pdf|docx?|xlsx?|pptx?|zip|rar|7z|dmg|exe|apk|mp[34]|mov|png|jpe?g)(?:$|/)',p.path) or re.search(r'(?i)(?:/file/|/download/|/status/\d+)', p.path):
        return None
    query = urlencode(sorted((k,v) for k,v in parse_qsl(p.query, keep_blank_values=True) if k.lower() not in ('gclid','fbclid','from','source')))
    return urlunsplit(('https', host + (':'+str(port) if port and port not in (80,443) else ''), p.path.rstrip('/') or '/', query, ''))


def group_message(row):
    try:p = json.loads(row.get('provenance') or '{}') if isinstance(row.get('provenance'),str) else row.get('provenance') or {}
    except (ValueError,TypeError):return False,'','',{}
    if not isinstance(p,dict):return False,'','',{}
    # Unknown/private is fail-closed. Never infer group status from a title.
    is_group = str(p.get('chatroom_id','')).endswith('@chatroom') and p.get('conversation_type') in (None,'group')
    group = str(row.get('title','')).partition(' · ')[0]
    return is_group, group, p.get('sender') or '', p


def message_time(row):
    """Use the source message clock, not the index/file modification clock."""
    provenance = group_message(row)[3]
    value = provenance.get('timestamp')
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            return datetime.fromtimestamp(value, timezone.utc)
        except (ValueError, OverflowError, OSError):
            pass
    return datetime.fromisoformat(row['modified'].replace('Z', '+00:00'))


def ordered_messages(rows):
    """Keep each chat's original sequence; cross-chat interleaving is irrelevant."""
    chats = {}
    for position, row in enumerate(rows):
        provenance = group_message(row)[3]
        raw = provenance.get('raw_local_id', provenance.get('local_id'))
        sequence = int(str(raw)) if str(raw).isdecimal() else None
        chats.setdefault(provenance.get('chatroom_id'), []).append((row, sequence, position))
    result = []
    for chat in chats.values():
        sequenced = all(number is not None for _, number, _ in chat)
        chat.sort(key=lambda r: (r[1], r[2]) if sequenced else (message_time(r[0]), r[0].get('_message_rowid', r[2])))
        result.extend(row for row, _, _ in chat)
    return result


def messages(root, days):
    dbpath = config.workspace(root)/'knowledge/knowledge.db'
    if not dbpath.is_file():
        return []
    dates = sorted(datetime.fromisoformat(d).replace(tzinfo=ZONE) for d in days)
    start = dates[0].timestamp()
    end = (dates[-1]+timedelta(days=2)).timestamp()
    db = sqlite3.connect(dbpath.resolve().as_uri()+'?mode=ro',uri=True,timeout=3);db.row_factory=sqlite3.Row
    try:
        columns = {r[1] for r in db.execute('PRAGMA table_info(documents)')}
        tags = 'tags' if 'tags' in columns else "'[]' AS tags"
        # Retain system/withdrawal messages: they break adjacency like any other message.
        clock = "COALESCE(CASE WHEN json_valid(provenance) THEN CASE WHEN json_type(provenance,'$.timestamp') IN ('integer','real') THEN json_extract(provenance,'$.timestamp') END END, CAST(strftime('%s',modified) AS REAL))"
        rows = [dict(r) for r in db.execute("SELECT rowid AS _message_rowid,id,title,body,modified,provenance,hash,"+tags+","+clock+" AS _source_time FROM documents WHERE source='wechat' AND _source_time>=? AND _source_time<? AND (CASE WHEN json_valid(provenance) THEN json_extract(provenance,'$.chatroom_id') END) LIKE '%@chatroom' AND COALESCE(CASE WHEN json_valid(provenance) THEN json_extract(provenance,'$.conversation_type') END,'group')='group' ORDER BY rowid",(start,end))]
        return ordered_messages(rows)
    finally:db.close()


def ordinal(match):
    return '①②③'.index(match[1])+1 if match[1] else int(match[2])


def _blocks(lines):
    found = [(i,m) for i,l in enumerate(lines) if (m:=NUMBER.match(l))]
    return [(ordinal(m),m[3],lines[i+1:found[n+1][0] if n+1<len(found) else len(lines)]) for n,(i,m) in enumerate(found)]


def _card(number, name, lines, row, group, day, sender):
    # Only the bounded numbered segment before its own resource link is used.
    candidates = [(i,tool_url(raw)) for i,line in enumerate(lines) if not re.match(r'^\s*(?:广告|赞助|推广|加群|优惠)[：:]',line) for raw in URL.findall(line)]
    candidates = [(i,u) for i,u in candidates if u]
    explicit = [(i,u) for i,u in candidates if re.search(r'链接|官网|地址|访问|🔗|→|↗',lines[i])]
    chosen = explicit[0] if len({u for _,u in explicit})==1 else candidates[0] if len({u for _,u in candidates})==1 else None
    if not chosen:
        return None
    end,url = chosen
    field_lines = [l.strip() for l in lines[:end] if l.strip() and not URL.search(l)]
    if not field_lines:
        return None
    clean = lambda x: public_text(re.sub(r'^[^\w\u4e00-\u9fff]*(?:(?:一句话|用途|说明|简介|是什么|用法|怎么用|上手)[：:]\s*)?', '', x), (sender,))
    purpose = clean(field_lines[0])
    usage = next((clean(l) for l in field_lines[1:] if re.search('上手|怎么用|用法|步骤|打开|输入|上传|复制|导入',l)), clean(field_lines[1]) if len(field_lines)>1 else '')
    if not usage:usage='打开工具链接，按其页面说明使用；具体步骤待核对'
    version = (m[0] if (m:=re.search(r'\bv\d+(?:\.\d+){0,3}\b',name,re.I)) else None)
    return dict(name=public_text(name,(sender,))[:100],purpose=purpose[:500],usage=usage[:500],classification='工具',url=url,version=version,
                group=group,namespace='knowledge:wechat',message_id=row['id'],shared_at=message_time(row).astimezone(timezone.utc).isoformat(),issue_date=day,ordinal=number,
                source_hash=row.get('hash') or hashlib.sha256(row['body'].encode()).hexdigest())


def system_message(row, provenance):
    tags = row.get('tags') or []
    if isinstance(tags, str):
        try:tags = json.loads(tags)
        except (ValueError, TypeError):tags = []
    return (isinstance(tags, list) and '系统' in tags or
            provenance.get('msg_type', provenance.get('message_type')) in (10000, 10002))


def extract_curated(rows, days, groups):
    cards=[];bad=0;bad_reasons={};states={};consumed=set();expanded=[]
    def reject(reason, count=1):
        nonlocal bad
        bad += count
        bad_reasons[reason] = bad_reasons.get(reason, 0) + count
    for original in rows:
        lines=original['body'].splitlines();starts=[i for i,line in enumerate(lines) if HEADER.match(line)]
        if len(starts)>1:
            for n,i in enumerate(starts):
                row=dict(original);row['hash']=original.get('hash') or hashlib.sha256(original['body'].encode()).hexdigest();row['body']='\n'.join(lines[i:starts[n+1] if n+1<len(starts) else len(lines)]);expanded.append(row)
        else:expanded.append(original)
    for row in expanded:
        is_group,group,sender,p=group_message(row)
        if not is_group:continue
        key=p.get('chatroom_id')
        # Every message in the physical chat breaks adjacency, even an untrusted title.
        previous=states.pop(key,None)
        if not config.curated(group,groups):continue
        if system_message(row,p):continue
        lines=row['body'].splitlines();headers=[(i,m) for i,l in enumerate(lines) if (m:=HEADER.match(l))]
        stamp=message_time(row)
        if headers:
            i,h=headers[-1]
            try:day=f'{int(h[1] or stamp.astimezone(ZONE).year):04d}-{int(h[2]):02d}-{int(h[3]):02d}';datetime.fromisoformat(day)
            except ValueError:reject('invalid_today_date');continue
            state=dict(day=day,last=0,time=stamp,sender=sender)
            blocks=_blocks(lines[i+1:])
            if not blocks and (any(tool_url(u) for u in URL.findall('\n'.join(lines[i+1:]))) or any(re.search('用途|用法|官网|链接',l) for l in lines[i+1:])):
                reject('missing_number')
        else:
            blocks=_blocks(lines)
            if not blocks or blocks[0][0] not in (2,3):continue
            reason = ('continuation_not_adjacent' if not previous else
                      'continuation_sender_changed' if sender!=previous['sender'] else
                      'continuation_different_day' if stamp.astimezone(ZONE).date()!=previous['time'].astimezone(ZONE).date() else
                      'continuation_gap_exceeded' if not 0 <= (stamp-previous['time']).total_seconds() <= 300 else
                      'continuation_wrong_number' if blocks[0][0]!=previous['last']+1 else None)
            if reason:
                reject(reason,len(blocks));continue
            state=previous
        if state['day'] not in days:continue
        for number,name,segment in blocks:
            if number!=state['last']+1:reject('wrong_number_sequence');continue
            state['last']=number;state['time']=stamp;consumed.add(row['id'])
            card=_card(number,name,segment,row,group,state['day'],sender)
            if card:cards.append(card)
            else:reject('missing_tool_link_or_fields')
        if state['last'] in (1,2):states[key]=state
    return cards,dict(bad=bad,bad_reasons=bad_reasons,consumed=consumed)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):return None


def verify_title(url, timeout=3):
    """Bounded public HTTP check; validate DNS again at every redirect."""
    try:
        for _ in range(4):
            p=urlsplit(url);host=p.hostname
            addresses=socket.getaddrinfo(host,p.port or 443,type=socket.SOCK_STREAM)
            if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):
                return dict(state='unverified',http_status=None,reason='non_public_address',title=None)
            req=urllib.request.Request(url,headers={'User-Agent':'ArticleStudio-ToolCheck/1.0','Accept':'text/html'})
            try:
                with urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect).open(req,timeout=timeout) as response:
                    raw=response.read(196608);status=response.status
                    text=raw.decode(response.headers.get_content_charset() or 'utf-8',errors='replace')
            except urllib.error.HTTPError as exc:
                if exc.code in (301,302,303,307,308):
                    from urllib.parse import urljoin
                    url=urljoin(url,exc.headers.get('Location',''))
                    if not tool_url(url):raise ValueError('unsafe_redirect')
                    continue
                return dict(state='unverified',http_status=exc.code,reason='http_error',title=None)
            match=re.search(r'<title[^>]*>(.*?)</title\s*>',text,re.I|re.S)
            return dict(state='checked' if match else 'unverified',http_status=status,reason=None if match else 'missing_title',title=public_text(html.unescape(re.sub(r'\s+',' ',match[1])))[:200] if match else None)
    except (OSError,ValueError):pass
    return dict(state='unverified',http_status=None,reason='network_or_redirect',title=None)


def model_config(root):
    root=config.workspace(root)
    db=sqlite3.connect((root/'schedules/tasks.db').resolve().as_uri()+'?mode=ro',uri=True)
    try:profiles={json.loads(r[0]).get('profile_id') for r in db.execute('SELECT data FROM tasks') if json.loads(r[0]).get('kind')=='wechat_radar'}
    finally:db.close()
    if len(profiles)!=1:raise ValueError('radar_profile_unavailable')
    from model_settings import profile_document,find_profile
    document=profile_document(json.loads((root/'private/article-studio-model.json').read_text()))
    return find_profile(document,profiles.pop())['config']


class ModelLeaseBusy(ValueError):
    """Another source owns this endpoint/model; leave messages pending."""


@contextmanager
def model_lease(workspace, settings):
    from wechat_source_loader import source_module
    # Reuse the radar's path function, not a second endpoint/model hash rule.
    lease_path = source_module('pi_executor').lease_path
    fd = os.open(lease_path(config.workspace(workspace), settings),
                 os.O_RDWR | os.O_CREAT | getattr(os, 'O_NOFOLLOW', 0), 0o600)
    try:
        try:
            flock(fd, LOCK_EX | LOCK_NB)
        except BlockingIOError:
            raise ModelLeaseBusy('model_lock_busy') from None
        yield
    finally:
        os.close(fd)  # Also releases the lease on every exception/process exit.


def infer_tool(text, urls, topics, settings, *, workspace):
    # Hold the same lease as R3-0 through slot inspection and the entire call.
    with model_lease(workspace, settings):
        from wechat_source_loader import source_module
        if source_module('model_service_status').backend_slots(settings)=='busy':raise ValueError('model_busy')
        prompt='仅从给出的群工具分享提取工具，不输出成员/聊天/微信号。若不是工具返回is_tool=false。严格JSON字段：is_tool(bool),name,purpose,usage,classification,relevance(0到3整数),version(null或明确版本)。兴趣方向：'+S.dumps(topics)+'\n工具链接：'+S.dumps(urls)+'\n内容：'+text[:3000]
        body=S.dumps(dict(model=settings['model'],messages=[{'role':'user','content':prompt}],temperature=0,max_tokens=600,stream=False)).encode()
        headers={'Content-Type':'application/json'}
        if settings.get('api_key'):headers['Authorization']='Bearer '+settings['api_key']
        request=urllib.request.Request(settings['endpoint'],data=body,headers=headers)
        with urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect).open(request,timeout=min(30,settings.get('timeout_seconds',30))) as response:
            data=json.loads(response.read(65536))
        text=data['choices'][0]['message']['content'].strip();text=re.sub(r'^```(?:json)?\s*|\s*```$','',text)
        value=json.loads(text)
        if not isinstance(value,dict) or type(value.get('is_tool')) is not bool:raise ValueError('model_schema')
        return value


def prepare(root, days, *, rows=None, infer=None, title_check=verify_title, curated_only=False):
    groups=config.read(root)['groups'];rows=messages(root,days) if rows is None else rows
    topics=I.current_topics(Path.home()/'grok-bot-reports') if config.workspace(root)==S.ROOT/'workspace' else _fixture_topics(root)
    cards,audit=extract_curated(rows,set(days),groups)
    stats=dict(messages=len(rows),curated=len(cards),bad=audit['bad'],bad_reasons=audit['bad_reasons'],private_skipped=0,non_tools=0,filtered=0,model_deferred=0,model_deferred_reasons={})
    evaluations=[];checked=set();dbpath=config.workspace(root)/'clues/clues.db'
    if dbpath.exists():
        with closing(S.connect(dbpath,True)) as db:
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='source_receipts'").fetchone():
                checked={(r['record_key'],r['source_version']) for r in db.execute("SELECT record_key,source_version FROM source_receipts WHERE channel='wechat:tool-evaluations'")}
    attempts=0;model_blocked=False
    if not curated_only:
        for row in rows:
            is_group,group,sender,_=group_message(row)
            if not is_group:stats['private_skipped']+=1;continue
            if config.curated(group,groups) or row['id'] in audit['consumed']:continue
            urls=list(dict.fromkeys(u for raw in URL.findall(row['body']) if (u:=tool_url(raw))))
            if not urls:continue
            source_hash=row.get('hash') or hashlib.sha256(row['body'].encode()).hexdigest()
            if (row['id'],source_hash) in checked:continue
            if model_blocked:
                stats['model_deferred']+=1
                stats['model_deferred_reasons']['model_lock_busy']=stats['model_deferred_reasons'].get('model_lock_busy',0)+1
                continue
            if attempts>=5:stats['model_deferred']+=1;continue
            attempts+=1
            # Raw sender metadata and wrapper line are never sent to inference.
            lines=row['body'].splitlines()
            if lines and lines[0].strip()==group+'：' or lines and lines[0].strip()==group+':':lines=lines[1:]
            text=public_text('\n'.join(lines),(sender,))
            try:value=(infer or (lambda text,urls,topics:infer_tool(text,urls,topics,model_config(root),workspace=root)))(text,urls,topics)
            except ModelLeaseBusy:
                model_blocked=True;stats['model_deferred']+=1
                stats['model_deferred_reasons']['model_lock_busy']=1
                continue
            except (OSError,ValueError,KeyError,TypeError):stats['model_deferred']+=1;continue
            if not isinstance(value,dict):stats['bad']+=1;continue
            if value.get('is_tool') is False:
                evaluations.append(dict(message_id=row['id'],source_hash=source_hash));stats['non_tools']+=1;continue
            if value.get('is_tool') is not True:stats['bad']+=1;continue
            if any(not isinstance(value.get(k),str) for k in ('name','purpose','usage','classification')) or type(value.get('relevance')) is not int or not 0<=value['relevance']<=3:
                stats['bad']+=1;continue
            evaluations.append(dict(message_id=row['id'],source_hash=source_hash))
            # A model cannot introduce a new URL or evidence reference.
            for n,url in enumerate(urls):
                card={k:public_text(value.get(k),(sender,))[:500] if k!='version' else value.get(k) if isinstance(value.get(k),str) and value['version'] in text else None for k in FIELDS}
                card.update(url=url,group=group,namespace='knowledge:wechat',message_id=row['id'],shared_at=S.utc(row['modified']),issue_date=datetime.fromisoformat(row['modified'].replace('Z','+00:00')).astimezone(ZONE).date().isoformat(),ordinal=n+1,source_hash=row.get('hash') or hashlib.sha256(row['body'].encode()).hexdigest(),relevance=value['relevance'])
                cards.append(card)
    for card in cards:
        trusted=config.curated(card['group'],groups)
        card.setdefault('relevance',None if trusted else 0)
        card['verification']=title_check(card['url'])
        from scripts.radar_common import today_relevance_gate
        verdict=today_relevance_gate(dict(title=card['name'],full_text=card['purpose']+'\n'+card['usage'],sources=['W']),topics,'interest')
        card['filtered']=not trusted and (card['relevance']<MIN_RELEVANCE or not verdict['visible'])
        card['filter_reason']=('相关度低于阈值' if card['relevance']<MIN_RELEVANCE else verdict['reason']) if card['filtered'] else None
        card['suggested_topic']=suggest_topic(card,topics)
        stats['filtered']+=int(card['filtered'])
    return dict(items=cards,_workspace=str(config.workspace(root)),_evaluations=evaluations),stats


def _fixture_topics(root):
    path=config.workspace(root)/'wechat-tools/interest-topics.json'
    return json.loads(path.read_text()) if path.exists() else list(S.DIRECTION_KEYS)


def suggest_topic(card,topics):
    text=(card['name']+' '+card['purpose']+' '+card['usage']).casefold()
    groups=(('模型','量化','weights'),('推理','vllm','mlx'),('视频','图像','配色','设计','image'),('api','网关'),('agent','编程','代码','mcp'),('安全','扫描'),('opencode','桌面'),('内容','自动化','教程'),('3d','游戏','硬件'))
    scores=[sum(term in text for term in terms) for terms in groups]
    return topics[max(range(len(scores)),key=lambda i:scores[i])] if topics and len(topics)==9 and max(scores)>0 else None


def run(root, days, batch_id, *, allow_real=False, curated_only=False, after_sync=False, **kwargs):
    root=config.workspace(root);path=root/'clues/clues.db'
    with I.ingest_lock(path,allow_real) as acquired:
        if not acquired:return dict(skipped=True,reason='clue_ingest_busy')
        sync=None
        if after_sync:
            with closing(sqlite3.connect((root/'knowledge/knowledge.db').resolve().as_uri()+'?mode=ro',uri=True,timeout=3)) as kb:
                row=kb.execute("SELECT status,synced_at FROM sources WHERE id='wechat'").fetchone()
            if not row or row[0]!='ready' or not row[1]:return dict(skipped=True,reason='wechat_sync_not_ready')
            sync=row[1]
            with closing(S.connect(path,True)) as db:
                prior=db.execute("SELECT source_version FROM ingest_checkpoints WHERE source='wechat_tools_sync' AND source_file='knowledge:wechat-sync'").fetchone()
            if prior and prior[0]==sync:return dict(skipped=True,reason='wechat_sync_unchanged',synced_at=sync)
        data,stats=prepare(root,days,curated_only=curated_only,**kwargs)
        I.register_batch(path,batch_id,allow_real=allow_real)
        try:
            result=I.import_document(path,'wechat_tools','knowledge:wechat',data,S.digest(data['items']),batch_id,allow_real=allow_real)
            if result['bad']:raise ValueError('tool_record_quarantined')
            with I.short_transaction(path,allow_real) as db:
                for evaluation in data['_evaluations']:
                    stamp=S.now();S.write(db,'source_receipts',S.identity('wechat:tool-evaluations',evaluation['message_id'],evaluation['source_hash']),dict(clue_id=None,channel='wechat:tool-evaluations',record_key=evaluation['message_id'],source_file='knowledge:wechat',file_hash=evaluation['source_hash'],source_version=evaluation['source_hash'],parser_version=POLICY,first_seen_at=stamp,last_seen_at=stamp,import_status='evaluated',error=None),batch_id)
            if sync and not stats['model_deferred']:
                with I.short_transaction(path,allow_real) as db:S.write(db,'ingest_checkpoints',S.identity('wechat_tools_sync','knowledge:wechat-sync'),dict(source_file='knowledge:wechat-sync',source='wechat_tools_sync',source_version=sync,committed_at=S.now(),counts_json=S.dumps(result)),batch_id,update=True)
            counts=S.complete_batch(path,batch_id,confirmed=True,allow_real=allow_real,source_names=('wechat_tools',))
            return dict(batch_id=batch_id,stats=stats,imported=result,counts=counts,synced_at=sync,finished_at=S.now(),sync_to_finish_seconds=(datetime.now(timezone.utc)-datetime.fromisoformat(sync.replace('Z','+00:00'))).total_seconds() if sync else None)
        except BaseException:
            with I.short_transaction(path,allow_real) as db:db.execute("UPDATE migrations SET status='failed',updated_at=? WHERE id=?",(S.now(),batch_id))
            raise


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--workspace',required=True);p.add_argument('--dates',nargs='+');p.add_argument('--batch-id');p.add_argument('--allow-real',action='store_true');p.add_argument('--curated-only',action='store_true');p.add_argument('--after-sync',action='store_true');args=p.parse_args()
    days=args.dates or [(datetime.now(ZONE)-timedelta(days=i)).date().isoformat() for i in range(3)]
    try:
        value=run(args.workspace,days,args.batch_id or 'wechat-tools-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f'),allow_real=args.allow_real,curated_only=args.curated_only,after_sync=args.after_sync)
        print(S.dumps(value))
    except Exception as exc:
        print(S.dumps(dict(ok=False,error_kind=type(exc).__name__)),file=sys.stderr);return 1
    return 0

if __name__=='__main__':sys.exit(main())
