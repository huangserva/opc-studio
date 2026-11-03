"""跨来源兴趣的只读投影。唯一写入是可删除的派生索引，绝不回写采集/K1/items。"""
from __future__ import annotations
from contextlib import contextmanager
from collections import OrderedDict
from datetime import datetime,timedelta,timezone
import hashlib
import json
import logging
import os
from pathlib import Path
import sqlite3
import stat
import tempfile
import threading
import time
import copy

import grok_bot_radar as G
from platform_registry import identity,derive_platform
from clue_store_v2 import canonical,safe_text,safe_url,safe_payload,utc,raw_metrics

LOG=logging.getLogger(__name__)
VERSION=3
LOCK=threading.RLock()
STATES={}
FILE_CACHE=OrderedDict()
FILE_LOCK=threading.RLock()
FILE_CACHE_LIMIT=None  # 当前七日文件集合保留全部规范化结果；持久层跨重启复用。
SIGNATURE_CACHE={}

def content_digest(path,max_bytes=64*1024*1024):
 path=Path(path)
 if path.parent.is_symlink():raise ValueError('source_directory_link')
 fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
 with os.fdopen(fd,'rb') as stream:
  info=os.fstat(stream.fileno())
  if not stat.S_ISREG(info.st_mode) or info.st_size>max_bytes:raise ValueError('source_size')
  digest=hashlib.sha256();seen=0
  for block in iter(lambda:stream.read(1024*1024),b''):
   seen+=len(block)
   if seen>max_bytes:raise ValueError('source_size')
   digest.update(block)
 return digest.hexdigest()

def filecache_path(root):return workspace(root)/'today/interest-filecache-v2.sqlite3'

def prune_filecache(root,active):
 """只清本派生库中过了七日选择范围的文件，空页由后续插入复用。"""
 target=filecache_path(root)
 if not target.is_file() or target.is_symlink() or target.parent.is_symlink():return
 try:
  with __import__('contextlib').closing(sqlite3.connect(target)) as db,db:
   stored=[row[0] for row in db.execute('SELECT path FROM files')]
   db.executemany('DELETE FROM files WHERE path=?',[(p,) for p in stored if p not in active])
 except sqlite3.DatabaseError:LOG.warning('interest_filecache_prune_unavailable')

def cached_file(root,path,kind):
 """按文件内容保存规范化条目；97个文件不会在16槽LRU里循环失效。"""
 info=path.stat();key=(str(path),info.st_mtime_ns,info.st_size)
 with FILE_LOCK:
  rows=FILE_CACHE.get(key)
  if rows is not None:return rows
 raw_hash=content_digest(path)
 target=filecache_path(root)
 if target.is_symlink() or target.parent.is_symlink():raise ValueError('filecache_link')
 target.parent.mkdir(parents=True,exist_ok=True)
 try:
  with __import__('contextlib').closing(sqlite3.connect(target)) as db,db:
   db.execute('CREATE TABLE IF NOT EXISTS files(path TEXT PRIMARY KEY, digest TEXT, kind TEXT, data TEXT)')
   cache_kind=str(VERSION)+':'+kind
   row=db.execute('SELECT data FROM files WHERE path=? AND digest=? AND kind=?',(str(path),raw_hash,cache_kind)).fetchone()
   rows=json.loads(row[0]) if row else parse_file(path,kind)
   if not row:db.execute('INSERT OR REPLACE INTO files VALUES(?,?,?,?)',(str(path),raw_hash,cache_kind,json.dumps(rows,ensure_ascii=False,separators=(',',':'))))
  os.chmod(target,0o600)
 except (OSError,ValueError,sqlite3.DatabaseError):
  LOG.warning('interest_filecache_unavailable; parsing_readonly_source')
  rows=parse_file(path,kind)
 with FILE_LOCK:
  for old in [k for k in FILE_CACHE if k[0]==str(path) and k!=key]:FILE_CACHE.pop(old,None)
  FILE_CACHE[key]=rows
 return rows
SOURCE_TRUST={'Grok 兴趣':5,'微信群工具':5,'X 书签':4,'Grok 日报':4,'Grok 快报':4,'Grok 周报':4,'GitHub API':4,'公众号文章':4,'外部文章收藏':3,'微信线索摘要':3,'AIsa 线索摘要':3,'AIsa X':2,'AIsa 手动取数':2,'linux.do':2,'抖音视频榜':1,'小红书灵感榜':1,'X 榜单':1,'读者问题':3}

def workspace(root):
 root=Path(root).resolve()
 return root/'workspace' if (root/'workspace').is_dir() else root

@contextmanager
def readonly(path):
 db=sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro',uri=True,timeout=.1)
 db.row_factory=sqlite3.Row
 try:
  db.execute('PRAGMA query_only=ON')
  forbidden={sqlite3.SQLITE_INSERT,sqlite3.SQLITE_UPDATE,sqlite3.SQLITE_DELETE,sqlite3.SQLITE_CREATE_TABLE,sqlite3.SQLITE_DROP_TABLE,sqlite3.SQLITE_ALTER_TABLE,sqlite3.SQLITE_ATTACH,sqlite3.SQLITE_DETACH}
  db.set_authorizer(lambda action,*_:sqlite3.SQLITE_DENY if action in forbidden else sqlite3.SQLITE_OK)
  db.execute('BEGIN')
  yield db
  assert db.total_changes==0
 finally:db.close()

def read_json(path, max_bytes=64*1024*1024):
 path=Path(path)
 if path.parent.is_symlink():raise ValueError('source_directory_link')
 fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
 with os.fdopen(fd,'rb') as stream:
  info=os.fstat(stream.fileno())
  if not stat.S_ISREG(info.st_mode) or info.st_size>max_bytes:raise ValueError('source_size')
  raw=stream.read(max_bytes+1)
  if len(raw)>max_bytes:raise ValueError('source_size')
  return json.loads(raw,parse_constant=lambda _:(_ for _ in ()).throw(ValueError('nonfinite')))

def item(row,source,channel,when=None,*,identifier=None,platform=None,body=None,title=None,topic=None,kind=None):
 """显式的逐源字段路径；不会在嵌套数组里搜索首个同名 text。"""
 raw_id=identifier or row.get('id') or row.get('tweet_id')
 url=safe_url(row.get('url')) or ''
 if str(raw_id).startswith(('tool:','legacy:','question:')):
  key=str(raw_id);plat=platform or 'web';content_kind=kind or 'post'
 else:
  key,plat,content_kind,_=canonical(dict(id=raw_id,url=url,topic_id=row.get('topic_id')),platform)
  if platform=='wechat':plat='wechat' # 已知公众号适配器的元信息，不从 URL 另做平台推导。
 body=safe_text(body if body is not None else row.get('full_text') or row.get('text') or '')
 title=safe_text(title if title is not None else row.get('title') or row.get('title_zh') or row.get('what_new') or body)[:200]
 author=row.get('author')
 followers=next((row[k] for k in ('author_followers','followers','author_fans') if row.get(k) is not None),None)
 author=author if isinstance(author,dict) else dict(name=row.get('author_name') or author or '',handle=row.get('author_handle') or row.get('handle') or '',followers=followers)
 author=dict(name=safe_text(author.get('name')),handle=safe_text(author.get('handle')),followers=G.number(author.get('followers')))
 published=utc(row.get('published_at') or row.get('created_at'))
 fetched=utc(row.get('fetched_at') or row.get('collected_at') or row.get('last_seen_at')) or utc(when)
 if not fetched:return None
 metrics,_=raw_metrics(row)
 result=dict(id=key,canonical_key=key,clue_id=None,identity_state='pending_ingest',platform=plat,kind=kind or content_kind,channel='interest',
             sources=[channel],channels=[],input_sources=[source],input_ids=[str(raw_id or key)],source_topics=[safe_text(topic if topic is not None else row.get('topic') or '')],
             title=title,what_new=title,full_text=body,url=url,author=safe_payload(author),published_at=published,fetched_at=fetched,first_seen_at=utc(row.get('first_seen_at')),last_seen_at=fetched,first_seen_basis=safe_text(row.get('first_seen_basis') or ''),
             metrics=metrics,tier='兴趣候选',topic=safe_text(topic if topic is not None else row.get('topic') or ''),relevance=None,fit_score=None,doable=None,hot=False,
             why=safe_payload(row.get('why','')),pattern=safe_payload(row.get('pattern','')),outline=safe_payload(row.get('outline','')),my_angle=safe_payload(row.get('my_angle','')),doable_resource=safe_payload(row.get('doable_resource','')),
             usable=[safe_payload(v) for v in row.get('usable',[]) if isinstance(v,dict)] if isinstance(row.get('usable',[]),list) else [],rich_sources=[safe_payload({k:row[k] for k in ('article','thread','quoted','media','card') if k in row})],
             content_complete=bool(row.get('content_complete')),incomplete_reason=safe_text(row.get('incomplete_reason') or '来源未提供完整正文'),incomplete_reasons=[],status='new',feedback=None,
             author_type=row.get('author_type'),author_verification_type=row.get('author_verification_type'))
 result.update(identity(result))
 if not result['content_complete']:result['incomplete_reasons']=[result['incomplete_reason']]
 return result

def files(root,now,reports=None):
 base=workspace(root);cut=(now-timedelta(days=7)).astimezone(G.BEIJING).date().isoformat();reports=(Path(reports) if reports is not None else G.reports_dir()).resolve()
 out=[]
 for name in ('daily','alerts','weekly','bookmarks','questions'):
  folder=reports/name
  if folder.is_symlink():continue
  out.extend((p,'report:'+name) for p in sorted(folder.glob('*.json')) if p.name[:10]>=cut)
 for directory,kind in (('twitter-radar/snapshots','aisa'),('video-radar/snapshots','video')):
  folder=base/directory
  if folder.is_symlink():continue
  out.extend((p,kind) for p in sorted(folder.glob('*/*.json')) if p.parent.name>=cut and not p.parent.is_symlink())
 for rel,kind in (('video-radar/page.json','video'),('linuxdo/top-daily.json','linuxdo'),('linuxdo/previous.json','linuxdo')):
  p=base/rel
  if p.is_file():out.append((p,kind))
 return out

def parse_file(path,kind):
 data=read_json(path);result=[]
 def add(row,*args,**kwargs):
  if not isinstance(row,dict):return
  try:
   value=item(row,*args,**kwargs)
   if value:result.append(value)
  except (ValueError,TypeError,OverflowError):pass
 if kind.startswith('report:'):
  name=kind.partition(':')[2]
  if not isinstance(data,dict):return []
  when=G.stamp(data.get('collected_at') or data.get('generated_at') or data.get('exported_at'))
  if not when:return []
  for key in ('posts','entries','items'):
   for row in data.get(key,[]) if isinstance(data.get(key),list) else []:
    if name=='questions':
     from clue_sources.questions import parse
     try:
      q=parse(row,data)
      add(dict(id=q['canonical_key'],url=row.get('parent_post_url'),content_complete=True),'读者问题','reader:questions',when.isoformat(),title=q['title'],body=q['body'],platform='x',kind='question_cluster')
     except (ValueError,TypeError):pass
    else:
     source={'daily':'Grok 日报','alerts':'Grok 快报','weekly':'Grok 周报','bookmarks':'X 书签'}[name]
     add(row,source,'x:bookmarks' if name=='bookmarks' else 'G',when.isoformat(),platform='x',topic=row.get('suggested_topic') or row.get('topic'))
 elif kind=='aisa' and isinstance(data,dict):
  for row in data.get('tweets',[]):
   if isinstance(row,dict) and not row.get('is_stale'):
    add(row,'AIsa X','A',data.get('finished_at') or data.get('base_time'),platform='x',body=row.get('text'))
 elif kind=='video' and isinstance(data,dict):
  for board,rows in data.get('lists',{}).items():
   if not isinstance(rows,list):continue
   platform='xhs' if board=='xiaohongshu' else 'douyin'
   for row in rows:
    if isinstance(row,dict):add(row,'小红书灵感榜' if platform=='xhs' else '抖音视频榜','V',data.get('generated_at'),platform=platform,body=row.get('full_text') or row.get('desc_excerpt') or row.get('title'),kind='video')
 elif kind=='linuxdo' and isinstance(data,dict):
  for row in data.get('items',[]):add(row,'linux.do','L',data.get('updated_at'),platform='linuxdo',kind='topic')
 return result

def signature(root,now,reports=None):
 # metadata仅用于发现需要检查，最终签名来自相关内容；无关任务表写入不改变签名。
 base=workspace(root);selected=files(root,now,reports)
 paths=[p for p,_ in selected]
 for rel in ('clues/clues.db','knowledge/knowledge.db','github/github.db','intel/intel.db','aisa/aisa.db','xradar/xradar.db'):
  p=base/rel;paths.extend([p,Path(str(p)+'-wal')])
 from interest_match import VERSION as rules_version
 from interest_channel import topics
 configured=topics(reports)['topics'];probe=[rules_version,configured]
 for path in paths:
  try:st=path.stat();probe.append((str(path),st.st_mtime_ns,st.st_size))
  except FileNotFoundError:probe.append((str(path),0,0))
 key=(str(base.resolve()),str(reports));probe=json.dumps(probe,separators=(',',':'))
 with FILE_LOCK:old=SIGNATURE_CACHE.get(key)
 if old and old[0]==probe:return old[1]
 parts=[VERSION,rules_version,configured]
 for path,kind in selected:
  parts.append((str(path),kind,content_digest(path)))
 errors=[];rows=database_items(root,now,errors);attach_k1(root,rows)
 # K1身份/方向会影响文件条目；仅摘要身份表，不触及私密evidence或任务表。
 identity_tables=[];db_path=base/'clues/clues.db'
 if db_path.exists():
  with readonly(db_path) as db:
   tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
   first_column=',first_seen_at' if any(r[1]=='first_seen_at' for r in db.execute('PRAGMA table_info(clue_objects)')) else ''
   for table,sql in [('clue_objects','SELECT id,canonical_key,platform'+first_column+' FROM clue_objects ORDER BY id'),('clue_aliases','SELECT alias,clue_id FROM clue_aliases ORDER BY alias'),('clue_channels','SELECT clue_id,channel FROM clue_channels ORDER BY clue_id,channel'),('legacy_object_links','SELECT thread_id,clue_id FROM legacy_object_links ORDER BY thread_id'),('topic_assignments',"SELECT a.clue_id,t.name,a.state FROM topic_assignments a JOIN topics t ON t.id=a.topic_id WHERE t.kind='direction' ORDER BY a.clue_id,t.name")]:
    if table in tables:identity_tables.append((table,[tuple(r) for r in db.execute(sql)]))
 parts.extend([rows,errors,identity_tables])
 result=hashlib.sha256(json.dumps(parts,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()
 with FILE_LOCK:SIGNATURE_CACHE[key]=(probe,result)
 return result

def database_items(root,now,errors=None):
 errors=errors if errors is not None else []
 base=workspace(root);cut=(now-timedelta(days=7)).isoformat();result=[]
 disabled_github=set()
 def append(row,*args,**kwargs):
  try:
   value=item(row,*args,**kwargs)
   if value:result.append(value)
  except (ValueError,TypeError,OverflowError):pass
 path=base/'intel/intel.db'
 if path.is_file():
  try:
   with readonly(path) as db:
    has_body_time=any(r[1]=='body_at' for r in db.execute('PRAGMA table_info(articles)'))
    seen='CASE WHEN length(body)>0 AND julianday(body_at)>COALESCE(julianday(added_at),-1e10) THEN body_at ELSE added_at END' if has_body_time else 'added_at'
    for row in db.execute('SELECT id,title,body,link,name,published_at,'+seen+' AS received_at FROM articles WHERE julianday('+seen+')>=julianday(?)',(cut,)):
     append(dict(id='wechat:'+row['id'],url=row['link'],author=dict(name=row['name'],handle=''),published_at=row['published_at'],content_complete=bool(row['body'])), '公众号文章','wechat:mp',row['received_at'],platform='wechat',title=row['title'],body=row['body'],kind='article')
  except (OSError,ValueError,TypeError,KeyError,sqlite3.DatabaseError) as exc:
   errors.append('database:'+path.parent.name);LOG.warning('interest_source_read_unavailable source=%s kind=%s',path.parent.name,type(exc).__name__)
 path=base/'knowledge/knowledge.db'
 if path.is_file():
  try:
   with readonly(path) as db:
    disabled_github={row['id'] for row in db.execute("SELECT id FROM sources WHERE json_extract(config,'$.enabled')=0")}
    sql="""SELECT d.* FROM documents d WHERE (d.kind='github' OR d.source='external-references')
      AND julianday(d.modified)>=julianday(?)
      AND NOT EXISTS(SELECT 1 FROM document_trash t WHERE t.document_id=d.id)
      AND NOT EXISTS(SELECT 1 FROM sources s WHERE s.id=d.source AND json_extract(s.config,'$.enabled')=0)"""
    for row in db.execute(sql,(cut,)):
     github=row['kind']=='github';p=json.loads(row['provenance']);url=p.get('canonical_url') or p.get('sourceUrl') or row['url']
     append(dict(url=url,id='',author=dict(name=p.get('author') or '',handle=p.get('author_handle') or ''),content_complete=False), 'GitHub API' if github else '外部文章收藏','github:api' if github else 'manual',row['modified'],platform='github' if github else None,title=row['title'],body=row['body'],kind='article' if not github else None)
  except (OSError,ValueError,TypeError,KeyError,sqlite3.DatabaseError) as exc:
   errors.append('database:'+path.parent.name);LOG.warning('interest_source_read_unavailable source=%s kind=%s',path.parent.name,type(exc).__name__)
 path=base/'github/github.db'
 if path.is_file():
  try:
   with readonly(path) as db:
    for row in db.execute('SELECT source,data FROM items'):
     if row['source'] in disabled_github:continue
     raw=json.loads(row['data'])
     if not utc(raw.get('modified')) or utc(raw['modified'])<cut:continue
     append(raw,'GitHub API','github:api',raw['modified'],platform='github',title=raw.get('title'),body=raw.get('body'))
  except (OSError,ValueError,TypeError,KeyError,sqlite3.DatabaseError) as exc:
   errors.append('database:'+path.parent.name);LOG.warning('interest_source_read_unavailable source=%s kind=%s',path.parent.name,type(exc).__name__)
 path=base/'aisa/aisa.db'
 if path.is_file():
  try:
   with readonly(path) as db:
    for row in db.execute('SELECT platform,item_id,captured_at,payload FROM items WHERE julianday(captured_at)>=julianday(?)',(cut,)):
     raw=json.loads(row['payload']);raw['id']=row['item_id']
     append(raw,'AIsa 手动取数','A',row['captured_at'],platform=row['platform'],title=raw.get('title') or raw.get('text'),body=raw.get('text') or raw.get('desc'))
  except (OSError,ValueError,TypeError,KeyError,sqlite3.DatabaseError) as exc:
   errors.append('database:'+path.parent.name);LOG.warning('interest_source_read_unavailable source=%s kind=%s',path.parent.name,type(exc).__name__)
 path=base/'xradar/xradar.db'
 if path.is_file():
  try:
   with readonly(path) as db:
    for row in db.execute('SELECT day,board,key,data,clue_id FROM items WHERE day>=?',(cut[:10],)):
     raw=json.loads(row['data']);raw['id']='';raw['url']=raw.get('url') or 'https://xbangdan.com/words/'
     if row['board']=='topics':
      if not row['clue_id']:
       if 'X榜单主题缺少已发表线索身份' not in errors:errors.append('X榜单主题缺少已发表线索身份')
       continue
      raw['id']='legacy:'+row['clue_id']
     append(raw,'X 榜单','rss:xbangdan',row['day']+'T00:00:00+08:00',title=raw.get('title') or raw.get('keyword'),body=raw.get('summary') or '')
  except (OSError,ValueError,TypeError,KeyError,sqlite3.DatabaseError) as exc:
   errors.append('database:'+path.parent.name);LOG.warning('interest_source_read_unavailable source=%s kind=%s',path.parent.name,type(exc).__name__)
 path=base/'clues/clues.db'
 if path.is_file():
  try:
   with readonly(path) as db:
    # 摘要来自既有线索视图；不 SELECT evidence.excerpt / 群名 / 邮件正文。
    tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if {'clue_content_versions','clue_channels','clue_aliases'}<=tables:
     # RSS历史快照可能已轮换；K1只选公开正文版本，不读取私密evidence。
     sql="""SELECT o.*,c.last_seen_at AS fetched,v.body,v.rich_json,v.content_complete AS version_complete,
       v.incomplete_reason AS version_reason,(SELECT a.alias FROM clue_aliases a WHERE a.clue_id=o.id AND a.alias LIKE 'https://linux.do/%' LIMIT 1) AS origin_url
       FROM clue_objects o JOIN clue_channels c ON c.clue_id=o.id
       JOIN clue_content_versions v ON v.clue_id=o.id AND v.channel=c.channel
       WHERE c.channel='linuxdo:rss' AND v.privacy_scope='public' AND julianday(c.last_seen_at)>=julianday(?)"""
     for row in db.execute(sql,(cut,)):
      rich=json.loads(row['rich_json']);rich=rich if isinstance(rich,dict) else {}
      append(dict(id=row['canonical_key'],url=row['origin_url'] or '',first_seen_at=row['first_seen_at'],first_seen_basis='k1_first_seen',published_at=row['published_at'],content_complete=bool(row['version_complete']),incomplete_reason=row['version_reason']),
             'linux.do','L',row['fetched'],platform='linuxdo',title=row['title'],body=row['body'],topic=rich.get('topic',''),kind='topic')
    if {'threads','entries'}<=tables:
     evidence={}
     sql="""SELECT e.thread_id,json_extract(j.value,'$.kind') kind,json_extract(j.value,'$.url') url,
       json_extract(j.value,'$.origin_url') origin_url,json_extract(j.value,'$.document_id') document_id
       FROM entries e JOIN threads t ON t.id=e.thread_id JOIN json_each(e.data,'$.evidence') j
       WHERE julianday(t.updated_at)>=julianday(?)"""
     for row in db.execute(sql,(cut,)):
      evidence.setdefault(row['thread_id'],[]).append(dict(row))
     links={r['thread_id']:r['canonical_key'] for r in db.execute('SELECT l.thread_id,o.canonical_key FROM legacy_object_links l JOIN clue_objects o ON o.id=l.clue_id')} if 'legacy_object_links' in tables else {}
     for row in db.execute('SELECT id,data,created_at,updated_at FROM threads WHERE julianday(updated_at)>=julianday(?)',(cut,)):
      refs=evidence.get(row['id'],[]);kinds={v['kind'] for v in refs}
      # 邮件、聊天原文、项目源码仍不直接接入。只读已提炼的微信/公开线索。
      if not kinds&{'wechat','wechat_links','aisa','github','xradar'}:continue
      data=json.loads(row['data']);public=next((v for v in refs if v['kind'] in ('aisa','github','xradar') and safe_url(v['url'])),None)
      key=links.get(row['id']) or 'legacy:'+row['id'];url=public['url'] if public else ''
      # 公开单原文可复用 K1 原文身份；混合/私密证据不猜合并。
      if public and len({v['url'] for v in refs if v['kind'] in ('aisa','github','xradar') and safe_url(v['url'])})==1:
       try:key=canonical(dict(url=url))[0]
       except ValueError:pass
      source='微信线索摘要' if kinds&{'wechat','wechat_links'} else 'GitHub API' if 'github' in kinds else 'AIsa 线索摘要'
      append(dict(id=key,url=url,first_seen_at=row['created_at'],first_seen_basis='source_created_at',content_complete=False,incomplete_reason='仅展示已有线索摘要，原始证据保留在线索中心'),source,'W' if source=='微信线索摘要' else 'github:api' if source=='GitHub API' else 'A',row['updated_at'],title=data.get('title'),body=data.get('summary'),kind='post')
  except (OSError,ValueError,TypeError,KeyError,sqlite3.DatabaseError) as exc:
   errors.append('database:'+path.parent.name);LOG.warning('interest_source_read_unavailable source=%s kind=%s',path.parent.name,type(exc).__name__)
 return result

def attach_k1(root,items):
 path=workspace(root)/'clues/clues.db'
 if not path.is_file():return
 with readonly(path) as db:
  tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
  if not {'clue_objects','clue_channels'}<=tables:return
  columns={r[1] for r in db.execute('PRAGMA table_info(clue_objects)')}
  first_column=',first_seen_at' if 'first_seen_at' in columns else ''
  records={r['canonical_key']:dict(r) for r in db.execute('SELECT id,canonical_key,platform'+first_column+' FROM clue_objects')}
  by_id={r['id']:r for r in records.values()}
  aliases={r['alias']:by_id[r['clue_id']] for r in db.execute('SELECT alias,clue_id FROM clue_aliases') if r['clue_id'] in by_id} if 'clue_aliases' in tables else {}
  legacy={r['thread_id']:by_id[r['clue_id']] for r in db.execute('SELECT thread_id,clue_id FROM legacy_object_links') if r['clue_id'] in by_id} if 'legacy_object_links' in tables else {}
  channels={}
  for r in db.execute('SELECT clue_id,channel FROM clue_channels'):channels.setdefault(r['clue_id'],[]).append(r['channel'])
  topics={}
  if {'topic_assignments','topics'}<=tables:
   for r in db.execute("SELECT a.clue_id,t.name FROM topic_assignments a JOIN topics t ON t.id=a.topic_id WHERE a.state IN ('suggested','confirmed') AND t.kind='direction'"):
    topics.setdefault(r['clue_id'],[]).append(r['name'])
  for value in items:
   rec=records.get(value['canonical_key']) or aliases.get(value['id']) or legacy.get(value['id'].removeprefix('legacy:')) or (aliases.get(value.get('url')) if value.get('kind')!='question_cluster' else None)
   if rec:
    value.update(canonical_key=rec['canonical_key'],clue_id=rec['id'],identity_state='k1')
    first=utc(rec.get('first_seen_at'))
    if first and (not value.get('first_seen_at') or G.stamp(first)<G.stamp(value['first_seen_at'])):
     value.update(first_seen_at=first,first_seen_basis='k1_first_seen')
    value['source_topics']=list(dict.fromkeys(value.get('source_topics',[])+topics.get(rec['id'],[])))
    value.update(identity(value,dict(platform=rec['platform'],channels=channels.get(rec['id'],[]))))

def build(root,now,reports=None,*,light=False):
 started=time.perf_counter();errors=[];unique={};orders={};cut=now-timedelta(days=7)
 import interest_first_seen as first_seen
 first_seen.apply(root,[],now,reports)
 def remember(value):
  moment=G.stamp(value.get('fetched_at'))
  if not moment or not cut<=moment<=now:return
  key=(value['input_sources'][0],value['canonical_key']);order=(moment.timestamp(),len(value.get('full_text','')))
  prior=unique.get(key)
  earliest=min((v for v in (value.get('first_seen_at') or value['fetched_at'], (prior.get('first_seen_at') or prior['fetched_at']) if prior else None) if v),key=G.stamp)
  if key not in orders or order>orders[key]:unique[key]=copy.deepcopy(value);orders[key]=order
  unique[key]['first_seen_at']=earliest
 selected=files(root,now,reports)
 active={str(p) for p,_ in selected}
 prune_filecache(root,active)
 with FILE_LOCK:
  for old in [k for k in FILE_CACHE if k[0] not in active]:FILE_CACHE.pop(old,None)
 if light:
  current=[(p,k) for p,k in selected if p.parent==workspace(root)/'linuxdo' or p.name=='page.json']
  for kind in ('aisa','report:daily','report:alerts','report:weekly'):
   values=[(p,k) for p,k in selected if k==kind]
   if values:current.append(values[-1])
  selected=current
 # 先读新文件，老版本只作补缺；不深拷贝十万重复观察到一个大列表里。
 for path,kind in sorted(selected,key=lambda v:str(v[0]),reverse=True):
  try:
   rows=cached_file(root,path,kind)
   for value in rows:remember(value)
  except (OSError,ValueError,TypeError,sqlite3.DatabaseError):errors.append(kind)
 if not light:
  try:
   for value in database_items(root,now,errors):remember(value)
  except (OSError,ValueError,TypeError,sqlite3.DatabaseError) as exc:
   errors.append('database');LOG.warning('interest_source_unavailable kind=%s',type(exc).__name__)
 values=list(unique.values());attach_k1(root,values)
 first_seen.apply(root,values,now,reports)
 from interest_channel import topics
 from interest_match import classify,compile_topics,profile_key
 from today_view import prepare_content
 configured=topics(reports)['topics'];directions=compile_topics(configured)
 for value in values:
  prepare_content(value);value['interest_matches']=classify(value,directions)
 return dict(items=values,match_profile=profile_key(configured),states={},errors=sorted(set(errors)),ready=not light,built_at=now.isoformat(),build_ms=round((time.perf_counter()-started)*1000,2))

def cache_path(root):return workspace(root)/'today/interest-index-v1.json'

def persist(root,data):
 path=cache_path(root)
 if path.is_symlink() or path.parent.is_symlink():raise ValueError('cache_link')
 path.parent.mkdir(parents=True,exist_ok=True)
 fd,tmp=tempfile.mkstemp(prefix='.interest-index-',dir=path.parent)
 try:
  with os.fdopen(fd,'w') as stream:
   os.fchmod(stream.fileno(),0o600);json.dump(data,stream,ensure_ascii=False,separators=(',',':'),allow_nan=False)
  os.replace(tmp,path)
 finally:
  if os.path.exists(tmp):os.unlink(tmp)

def refresh(root,now,sig,state,reports):
 try:
  data=dict(build(root,now,reports),schema=VERSION,signature=sig)
  try:persist(root,data)
  except (OSError,ValueError):LOG.warning('interest_cache_write_unavailable')
  with LOCK:state.update(data=data,refreshing=False,error=None)
 except Exception as exc:
  LOG.warning('interest_cache_rebuild_failed kind=%s',type(exc).__name__)
  with LOCK:state.update(refreshing=False,error='派生索引重建失败，保留上次结果')

def candidates(root,now=None):
 now=now or datetime.now(timezone.utc);reports=G.reports_dir().resolve();key=(str(workspace(root).resolve()),str(reports))
 with LOCK:
  state=STATES.setdefault(key,dict(data=None,refreshing=False,checked=0,signature='',error=None))
  if time.monotonic()-state['checked']>1:
   state['signature']=signature(root,now,reports);state['checked']=time.monotonic()
  sig=state['signature']
  if state['data'] is None:
   try:
    saved=read_json(cache_path(root))
    if not valid_cache(saved):raise ValueError('cache_schema')
    state['data']=saved
   except (OSError,ValueError,TypeError):
    LOG.warning('interest_cache_missing_or_bad; computing_readonly')
    # 降级现算轻量当前快照；明确不完整，完整7日重建由后台完成。
    state['data']=dict(build(root,now,reports,light=True),signature='partial')
  if state['data'] and (not state['data'].get('ready') or state['data'].get('signature')!=sig) and not state['refreshing']:
   state['refreshing']=True
   worker=threading.Thread(target=refresh,args=(root,now,sig,state,reports),name='interest-index',daemon=True)
   state['worker']=worker;worker.start()
  data=state['data'] or dict(items=[],states={},errors=['cache'],built_at=None,build_ms=None)
  return data,dict(ready=bool(data.get('ready') and not state['refreshing']),partial=not data.get('ready'),refreshing=state['refreshing'],error=state['error'],built_at=data['built_at'],build_ms=data['build_ms'])

def valid_cache(value):
 if not isinstance(value,dict) or value.get('schema')!=VERSION or not value.get('ready') or not isinstance(value.get('items'),list) or not isinstance(value.get('built_at'),str) or not isinstance(value.get('build_ms'),(int,float)):return False
 for p in value['items']:
  if not isinstance(p,dict) or any(not isinstance(p.get(k),str) for k in ('id','canonical_key','title','full_text','fetched_at','first_seen_at')):return False
  if any(not isinstance(p.get(k),list) or any(not isinstance(v,str) for v in p[k]) for k in ('input_sources','sources','source_topics')):return False
  if not isinstance(p.get('author'),dict) or any(not isinstance(p['author'].get(k,''),str) for k in ('name','handle')) or not isinstance(p.get('metrics'),dict) or not isinstance(p.get('interest_matches'),list):return False
  if any(not isinstance(m,dict) or any(not isinstance(m.get(k),str) for k in ('direction','name','word','field','basis')) or not isinstance(m.get('weight'),int) for m in p['interest_matches']):return False
 return True
