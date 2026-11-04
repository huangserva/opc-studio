#!/usr/bin/env python3
"""按工作台兴趣方向搜抖音/小红书；独立快照，不写发现榜或K1/items。"""
from __future__ import annotations
import argparse
from datetime import datetime,timedelta,timezone
import fcntl
import json
import os
from pathlib import Path
import re
import random
import shlex
import sys
import time
import uuid

ROOT=Path(__file__).resolve().parents[1]
for path in (ROOT,Path(__file__).resolve().parent):
 if str(path) not in sys.path:sys.path.insert(0,str(path))
import video_radar as V
TZ=timezone(timedelta(hours=8))
CHANNEL='兴趣视频搜索'
PLATFORMS=('douyin','xhs')
ACTION=re.compile(r'教程|工作流|操作步骤|使用方法|节点|下载|仓库|命令|脚本|配置|github|workflow|tutorial|gguf|how.to',re.I)
EXPLANATION=re.compile(r'到底是什么|权重是什么|谁能活下来|未来展望|哲学思考|行业观点')
PROMO=re.compile(r'领福利|搞钱|躺赚|月入|暴富|赚钱秘籍|私信.{0,8}(?:领|送)|进群.{0,8}(?:领|送)|评论.{0,8}(?:领|送)|注册.{0,8}送|限时福利',re.I)
NEGATIVE_ACTION=re.compile(r'不(?:能|可|支持)(?:下载|部署)|不开源|没有教程|无教程|非教程')
SAFE_ERRORS={'credential_missing','credential_invalid','http_400','http_401','http_402','http_403','http_404','http_429','http_500','http_502','http_503','http_504','request_timeout_or_transport','transport_28','round_timeout','budget_exhausted','schema_error','platform_error'}

class BudgetError(ValueError):
 """Only fixed error codes; never include ledger values."""

def now():return datetime.now(TZ)
def stamp(at=None):return (at or now()).astimezone(TZ).isoformat()
def storage(root):
 from interest_sources import workspace
 return workspace(root)/'interest-video-search'
def error_code(exc):
 value=str(exc) if isinstance(exc,(V.RadarError,BudgetError)) else ''
 return value if value in {'budget_invalid_json','budget_invalid_structure','budget_invalid_day','budget_invalid_calls','budget_invalid_entry','budget_over_limit'} or value in SAFE_ERRORS or re.fullmatch(r'transport_\d{1,3}',value) else 'collection_failed'

def config(root,reports=None,path=None):
 import grok_bot_radar as G,interest_channel as I
 root_config=Path(root)/'scripts/interest_video_search_config.json'
 source=Path(path) if path is not None else root_config if (Path(root)/'scripts').is_dir() else Path(__file__).with_name('interest_video_search_config.json')
 try:
  if not source.is_file() or source.is_symlink():raise ValueError('配置缺失或不可读')
  cfg=json.loads(source.read_text())
  folder=Path(reports) if reports is not None else G.reports_dir()
  if not (folder/'interest-topics.md').is_file():raise ValueError('兴趣方向文件缺失')
  topics=I.topics(folder)['topics'];directions=cfg.get('directions')
  if not topics or len(set(topics))!=len(topics):raise ValueError('兴趣方向为空或重复')
  if not isinstance(directions,list) or [r.get('name') for r in directions if isinstance(r,dict)]!=topics:raise ValueError('方向名称与当前方向不一致')
  for row in directions:
   words=row.get('keywords')
   if not isinstance(words,list) or not 1<=len(words)<=3 or len(set(words))!=len(words) or any(not isinstance(w,str) or not w.strip() or len(w)>80 or any(ord(c)<32 for c in w) for w in words):raise ValueError('每方向须有1至3个非空搜索词')
  if set(cfg.get('platforms',{}))!=set(PLATFORMS) or any(type(v) is not bool for v in cfg['platforms'].values()):raise ValueError('平台开关格式错误')
  limits={'daily_call_limit':(1,80),'per_query_limit':(1,50),'max_age_hours':(1,168),'request_timeout_seconds':(1,30),'round_timeout_seconds':(60,1800),'max_retries':(0,2)}
  for key,(lo,hi) in limits.items():
   if type(cfg.get(key)) is not int or not lo<=cfg[key]<=hi:raise ValueError('配置数值不合法：'+key)
  if type(cfg.get('enabled')) is not bool:raise ValueError('采集开关格式错误')
  if not isinstance(cfg.get('times'),list) or len(cfg['times'])!=2 or len(set(cfg['times']))!=2 or any(not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d',str(v)) for v in cfg['times']):raise ValueError('须配置两个有效北京时间')
  if not isinstance(cfg.get('retry_codes'),list) or any(c not in SAFE_ERRORS for c in cfg['retry_codes']):raise ValueError('重试错误码无效')
  return cfg
 except ValueError:raise
 except (OSError,TypeError,KeyError):raise ValueError('兴趣视频搜索配置不可读') from None

def response_rows(data,platform):
 if not isinstance(data,dict) or data.get('code')!=200:raise V.RadarError('platform_error')
 body=data.get('data')
 if platform=='xhs':
  if not isinstance(body,dict) or body.get('code')!=0 or body.get('success') is not True:raise V.RadarError('platform_error')
  rows=body.get('data',{}).get('items') if isinstance(body.get('data'),dict) else None
  if not isinstance(rows,list):raise V.RadarError('schema_error')
  return [r['note'] for r in rows if isinstance(r,dict) and isinstance(r.get('note'),dict)]
 if not isinstance(body,list):raise V.RadarError('schema_error')
 result=[];valid=False
 for block in body:
  if not isinstance(block,dict):continue
  if block.get('status_code')!=0:
   if 'status_code' in block:raise V.RadarError('platform_error')
   continue
  if not isinstance(block.get('business_data'),list):continue
  valid=True
  for row in block['business_data']:
   payload=row.get('data') if isinstance(row,dict) else None
   video=payload.get('aweme_info') if isinstance(payload,dict) else None
   if isinstance(video,dict):result.append(video)
 if not valid:raise V.RadarError('schema_error')
 return result

def normalize(raw,platform,at,cfg):
 from clue_store_v2 import canonical,safe_text
 if not isinstance(raw,dict):return None,'shape'
 if platform=='douyin':
  if raw.get('aweme_type')!=0 or raw.get('media_type')!=4:return None,'not_video'
  ident=str(raw.get('aweme_id') or '');body=safe_text(raw.get('desc'));published=V.published(raw.get('create_time'));stats=raw.get('statistics') if isinstance(raw.get('statistics'),dict) else {};user=raw.get('author') if isinstance(raw.get('author'),dict) else {}
  metrics=dict(views=None,likes=V.integer(stats.get('digg_count')),bookmarks=V.integer(stats.get('collect_count')),replies=V.integer(stats.get('comment_count')),retweets=V.integer(stats.get('share_count')))
  url='https://www.douyin.com/video/'+ident
 else:
  if raw.get('type')!='video':return None,'not_video'
  ident=str(raw.get('id') or '');body='\n'.join(dict.fromkeys(v for v in (safe_text(raw.get('title')),safe_text(raw.get('desc'))) if v));published=V.published(raw.get('timestamp'));user=raw.get('user') if isinstance(raw.get('user'),dict) else {}
  metrics=dict(views=None,likes=V.integer(raw.get('liked_count')),bookmarks=V.integer(raw.get('collected_count')),replies=V.integer(raw.get('comments_count')),retweets=V.integer(raw.get('shared_count')));url='https://www.xiaohongshu.com/explore/'+ident
 if not published or published>at or (at-published).total_seconds()>cfg['max_age_hours']*3600:return None,'not_new_or_time_unknown'
 try:key,_,_,url=canonical({'id':ident,'url':url},platform)
 except ValueError:return None,'identity'
 if not body:return None,'empty_text'
 if PROMO.search(body):return None,'promotion'
 if not actionable(body):return None,'not_actionable'
 return dict(id=key,url=url,platform=platform,kind='video',title=body.split('\n',1)[0][:200],full_text=body,published_at=stamp(published),fetched_at=stamp(at),first_seen_at=stamp(at),last_seen_at=stamp(at),first_seen_basis='interest_video_first_observation',metrics=metrics,author={'name':safe_text(user.get('nickname') or user.get('name')),'handle':'','followers':None},sources=['aisa:interest-video'],channels=['aisa'],content_complete=False,incomplete_reason='平台搜索仅提供标题/简介，视频完整内容需打开原帖核对',why='方向词检索命中，包含可操作资源线索；未对视频内容作模型判断',usable=[],interest_topics=[],search_queries=[]),None

def resource_evidence(plain):
 # A resource claim cannot be assembled across sentences or comma clauses.
 if re.search(r'https?://[^\s]+|github(?:\.com/|\s+)[a-z0-9_-]+',plain,re.I):return True
 for phrase in re.split(r'[。！？；\n，,;!?]|(?<!\d)\.|\.(?!\d)',plain):
  if re.search(r'(?:附(?:带|上)?|下载)[^。！？；\n，,;!?]{0,24}(?:节点|工作流|脚本|命令|插件)|(?:提供|分享)[^。！？；\n，,;!?]{0,24}(?:节点|工作流|脚本|插件)[^。！？；\n，,;!?]{0,20}(?:下载|地址|链接|附件|仓库|资源)|(?:节点|工作流|脚本|插件)[^。！？；\n，,;!?]{0,20}(?:下载地址|仓库地址|资源入口)|(?:源码|完整代码)[^。！？；\n，,;!?]{0,16}(?:仓库|下载|链接)',phrase,re.I):return True
 return False

def action_semantics(plain):
 # Concrete teaching/usage paths, not the generic words 分享/代码/视频.
 teaching=re.search(r'教你(?:用|使用|搭建|配置|制作|操作|部署)|跟着(?:我|我们)?(?:操作|做)(?:走一遍|一遍)?|实用技巧|[一二三四五六七八九十0-9]+步(?:搭建|配置|制作|部署|安装|完成)',plain)
 path=re.search(r'打开[^。！？；\n，,→]{2,40}[，,→]\s*(?:找|选择|选中|点击|搜索)[^。！？；\n]{2,60}',plain)
 return bool(teaching or path)

def actionable(body):
 if not isinstance(body,str):return False
 plain=re.sub(r'#[^\s#]+','',body).strip()
 if len(re.sub(r'\W','',plain))<8 or NEGATIVE_ACTION.search(plain) or EXPLANATION.search(plain):return False
 guide=re.search(r'教程|操作步骤|使用方法|手把手|保姆级|tutorial|how.to',plain,re.I)
 steps=any(re.search(r'(?:步骤[一二三四1-9]|第一步|先.{2,35}再.{2,35})',sentence) for sentence in re.split(r'[。！？；\n;!?]',plain))
 return bool(guide or resource_evidence(plain) or steps or action_semantics(plain))

def request_spec(platform,word,default_filters=False):
 # Primary uses week/video filters; one HTTP400 fallback uses newest/all-time/video.
 # Local normalization still enforces age/type for every response.
 if platform=='xhs':return 'GET','xiaohongshu/app_v2/search_notes',dict(keyword=word,page=1,sort_type='time_descending',note_type='视频笔记',time_filter='一天内')
 params=dict(keyword=word,cursor=0,sort_type='2' if default_filters else '0',publish_time='0' if default_filters else '7',filter_duration='0',content_type='1',search_id='',backtrace='')
 return 'POST','douyin/search/fetch_multi_search',params

def budget_for(path,day):
 # Never recover an existing invalid ledger by resetting it to zero.
 if path.is_symlink():raise BudgetError('budget_invalid_structure')
 try:
  with path.open() as stream:value=json.load(stream)
 except FileNotFoundError:return {'day':day,'calls':[]}
 except (ValueError,UnicodeError):raise BudgetError('budget_invalid_json') from None
 if not isinstance(value,dict) or set(value)!={'day','calls'}:raise BudgetError('budget_invalid_structure')
 if value['day']!=day:raise BudgetError('budget_invalid_day')
 calls=value['calls']
 if not isinstance(calls,list):raise BudgetError('budget_invalid_calls')
 if len(calls)>80:raise BudgetError('budget_over_limit')
 if any(not isinstance(c,dict) or c.get('status') not in ('reserved','ok','failed') for c in calls):raise BudgetError('budget_invalid_entry')
 return value

def ordered_directions(cfg):
 from topic_registry import topic_key
 priority={'local-inference':0,'visual-models':1}
 return sorted(cfg['directions'],key=lambda d:priority.get(topic_key(d['name']),2))

def read(path,default):return V.read_json(path,default)
def write(path,value):V.atomic_json(Path(path),value)

def run(root,*,reports=None,config_path=None,slot=None,transport=None,key=None,clock=now):
 root=Path(root).resolve()
 if (root/'scripts').is_dir():(root/'workspace').mkdir(exist_ok=True)
 folder=storage(root)
 if any(p.is_symlink() for p in (folder,folder.parent)):raise ValueError('兴趣采集目录不能是链接')
 folder.mkdir(parents=True,exist_ok=True);started=clock();rid=uuid.uuid4().hex;receipt=dict(id=rid,slot=slot,started_at=stamp(started),status='running',calls=[],queries=[],items=[],qualified=0)
 target=folder/'rounds'/started.astimezone(TZ).date().isoformat()/(rid+'.json')
 lock=(folder/'budget.lock').open('a+')
 try:
  try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
  except BlockingIOError:
   receipt.update(status='busy',finished_at=stamp(clock()),error='另一轮持锁，本轮未发请求');write(target,receipt);return receipt
  try:cfg=config(root,reports,config_path)
  except (ValueError,OSError) as exc:
   # Configuration messages are locally generated, never provider text.
   message=str(exc) if isinstance(exc,ValueError) and str(exc).startswith(('配置','兴趣','方向','每方向','平台','须配置','重试','采集')) else '配置不可读'
   receipt.update(status='config_error',error=message,finished_at=stamp(clock()));write(target,receipt);write(folder/'status.json',receipt);raise ValueError(message) from None
  previous=read(folder/'page.json',{'items':[]});state={p['id']:p for p in previous.get('items',[]) if isinstance(p,dict) and p.get('id') and actionable(p.get('full_text') or p.get('title') or '')}
  receipt['planned_queries']=sum(len(r['keywords']) for r in cfg['directions'])*sum(cfg['platforms'].values())
  write(folder/'status.json',receipt)
  if not cfg['enabled'] or not any(cfg['platforms'].values()):
   receipt.update(status='disabled',finished_at=stamp(clock()));write(target,receipt);write(folder/'status.json',receipt);return receipt
  credential=key if key is not None else os.environ.get('AISA_API_KEY','')
  if not credential:
   receipt.update(status='failed',error='credential_missing',finished_at=stamp(clock()));write(target,receipt);write(folder/'status.json',receipt);return receipt
  transport=transport or V.curl_json
  from interest_match import classify,compile_topics
  directions=compile_topics([r['name'] for r in cfg['directions']]);configured={d['key']:d['configured_name'] for d in directions}
  from topic_registry import topic_key
  deadline=time.monotonic()+cfg['round_timeout_seconds'];stop=False;found={};failures=0
  receipt['priority_order']=[d['name'] for d in ordered_directions(cfg)]
  for direction in ordered_directions(cfg):
   if stop:break
   for platform in PLATFORMS:
    if stop:break
    if not cfg['platforms'][platform]:continue
    for word in direction['keywords']:
     if stop:break
     info=dict(direction=direction['name'],platform=platform,keyword=word,raw_count=0,accepted_count=0,rejected={},status='pending');receipt['queries'].append(info)
     method,path,params=request_spec(platform,word);response=None
     attempt=0;normal_retries=0;default_filters=False
     while True:
      if time.monotonic()>=deadline:info.update(status='failed',error='round_timeout');stop=True;break
      at=clock();day=at.astimezone(TZ).date().isoformat();budget_path=folder/'budget'/f'{day}.json';budget=budget_for(budget_path,day)
      if len(budget['calls'])>=min(80,cfg['daily_call_limit']):
       info.update(status='budget_exhausted',error='budget_exhausted');stop=True
       if default_filters:info['fallback']['status']='budget_exhausted'
       break
      call=dict(id=uuid.uuid4().hex,round_id=rid,method=method,path=path,platform=platform,keyword=word,direction=direction['name'],attempt=attempt+1,started_at=stamp(at),status='reserved',filters='default' if default_filters else 'configured')
      attempt+=1
      # Reservation is fsynced before transport. Crash/timeout/retry all count.
      budget['calls'].append(call);write(budget_path,budget);receipt['calls'].append(dict(call));write(folder/'status.json',receipt)
      try:
       response=transport(V.ORIGIN+V.BASE+path+('?' + __import__('urllib.parse',fromlist=['urlencode']).urlencode(params) if method=='GET' else ''),method,None if method=='GET' else params,credential,min(cfg['request_timeout_seconds'],max(1,deadline-time.monotonic())))
       response=V.clean_strings(response,credential);rows=response_rows(response,platform);call.update(status='ok',finished_at=stamp(clock()));info['status']='ok';info.pop('error',None)
       if default_filters:info['fallback']['status']='ok'
       break
      except Exception as exc:
       code=error_code(exc);call.update(status='failed',error=code,finished_at=stamp(clock()));info.update(status='failed',error=code)
       response=None
       if default_filters:
        info['fallback']['status']='failed';break
       if code=='http_400' and platform=='douyin':
        default_filters=True;method,path,params=request_spec(platform,word,True);info['fallback']={'reason':'http_400','filters':'default','status':'pending'}
       elif normal_retries<cfg['max_retries'] and code in cfg['retry_codes']:
        normal_retries+=1
       else:break
      finally:
       write(budget_path,budget);receipt['calls'][-1]=dict(call);write(folder/'status.json',receipt)
     if response is None:
      failures+=1;continue
     info['raw_count']=len(rows);info['samples']=[]
     sample_positions=set(random.Random(rid+platform+word).sample(range(min(len(rows),cfg['per_query_limit'])),min(5,len(rows),cfg['per_query_limit'])))
     for position,raw in enumerate(rows[:cfg['per_query_limit']]):
      candidate,reason=normalize(raw,platform,clock(),cfg)
      matches=classify(dict(candidate,source_topics=[],input_sources=['兴趣视频搜索']) if candidate else {},directions) if candidate else []
      if candidate and not any(m['direction']==topic_key(direction['name']) for m in matches):reason='direction_not_matched';candidate=None
      if candidate:
       candidate['interest_topics']=[configured[m['direction']] for m in matches];candidate['search_queries']=[word];info['accepted_count']+=1
       key_id=candidate['id'];prior=found.get(key_id) or state.get(key_id)
       if prior:
        candidate['first_seen_at']=min(prior['first_seen_at'],candidate['first_seen_at']);candidate['interest_topics']=list(dict.fromkeys(prior.get('interest_topics',[])+candidate['interest_topics']));candidate['search_queries']=list(dict.fromkeys(prior.get('search_queries',[])+candidate['search_queries']))
       found[key_id]=candidate
      else:info['rejected'][reason or 'unclassified']=info['rejected'].get(reason or 'unclassified',0)+1
      if position in sample_positions:
       from clue_store_v2 import safe_text
       info['samples'].append(dict(id=str(raw.get('aweme_id') or raw.get('id') or '') if isinstance(raw,dict) else '',text='\n'.join(dict.fromkeys(safe_text(raw.get(k)) for k in ('title','desc') if isinstance(raw,dict) and isinstance(raw.get(k),str)))[:2000],accepted=bool(candidate),reason=reason,published_at=candidate.get('published_at') if candidate else None))
     write(folder/'status.json',receipt)
  attempted={(q['direction'],q['platform'],q['keyword']) for q in receipt['queries'] if q['status']!='budget_exhausted'}
  receipt['skipped_queries']=[dict(direction=d['name'],platform=p,keyword=w,reason='budget_exhausted' if stop and any(q['status']=='budget_exhausted' for q in receipt['queries']) else 'round_timeout') for d in ordered_directions(cfg) for p in PLATFORMS if cfg['platforms'][p] for w in d['keywords'] if (d['name'],p,w) not in attempted]
  receipt['fallback_count']=sum(c['filters']=='default' for c in receipt['calls'])
  state.update(found);cut=clock()-timedelta(days=7)
  retained=[v for v in state.values() if V.published(v.get('last_seen_at')) and V.published(v['last_seen_at'])>=cut]
  receipt.update(items=list(found.values()),qualified=len(found),finished_at=stamp(clock()),status='budget_exhausted' if any(q['status']=='budget_exhausted' for q in receipt['queries']) else 'partial' if failures and any(q['status']=='ok' for q in receipt['queries']) else 'failed' if failures else 'ok',outcome='matched' if found else 'no_qualified_content')
  if any(q.get('error')=='round_timeout' for q in receipt['queries']):receipt['status']='partial' if found else 'failed'
  write(target,receipt);write(folder/'page.json',{'schema':1,'generated_at':receipt['finished_at'],'items':retained});write(folder/'status.json',receipt);return receipt
 except Exception as exc:
  if receipt.get('status')!='config_error':
   receipt.update(status='failed',error=error_code(exc),finished_at=stamp(clock()))
   write(target,receipt);write(folder/'status.json',receipt)
  raise
 finally:lock.close()

def status(root,reports=None):
 try:
  config(root,reports)
 except (ValueError,OSError) as exc:return {CHANNEL:{'updated_at':None,'status':'config_error','error':'配置错误：'+str(exc)}}
 try:value=read(storage(root)/'status.json',{})
 except (ValueError,OSError,TypeError):return {CHANNEL:{'updated_at':None,'status':'failed','error':'采集状态损坏'}}
 if not isinstance(value,dict):return {CHANNEL:{'updated_at':None,'status':'failed','error':'采集状态损坏'}}
 if not value:return {CHANNEL:{'updated_at':None,'status':'waiting','error':None,'note':'尚未运行'}}
 state=value.get('status');errors=sorted({q.get('error') for q in value.get('queries',[]) if q.get('error')})
 message=value.get('error') or ('接口异常：'+','.join(errors) if state in ('partial','failed','budget_exhausted') else None)
 return {CHANNEL:{'updated_at':value.get('finished_at') or value.get('started_at'),'status':state,'error':message,'note':('默认筛选回退：'+str(value['fallback_count'])+'个查询') if value.get('fallback_count') else '没合格内容' if value.get('outcome')=='no_qualified_content' and state=='ok' else '采集中' if state=='running' else '正常','round_id':value.get('id'),'calls':len(value.get('calls',[]))}}

def register(root,reports=None,enabled=None):
 from scheduled_tasks import ScheduledTasks
 cfg=config(root,reports);root=Path(root).resolve();(root/'workspace').mkdir(exist_ok=True);manager=ScheduledTasks(root/'workspace',autostart=False)
 try:
  existing=manager.listing()['tasks'];out=[]
  for at in cfg['times']:
   name=CHANNEL+' · '+at;prior=next((r for r in existing if r['name']==name),None)
   if prior:out.append(prior);continue
   body=shlex.join([sys.executable,'-B',str(root/'scripts/interest_video_search.py'),'--root',str(root),'--slot',at])
   result=manager.save(dict(name=name,kind='script',body=body,cwd=str(root),frequency='daily',at=at,weekdays=list(range(7)),timeout=cfg['round_timeout_seconds']+30,enabled=cfg['enabled'] if enabled is None else enabled,allow_commands=False,result_destination='runs'))
   out.append(next(r for r in manager.listing()['tasks'] if r['id']==result['id']))
  return out
 finally:manager.close()

def main():
 parser=argparse.ArgumentParser();parser.add_argument('--root',default=str(ROOT));parser.add_argument('--slot');parser.add_argument('--register',action='store_true');args=parser.parse_args()
 try:
  if args.register:print(json.dumps({'registered':[r['id'] for r in register(args.root)]}));return
  result=run(args.root,slot=args.slot);print(json.dumps({k:result.get(k) for k in ('id','status','qualified','outcome')},ensure_ascii=False));sys.exit(0 if result['status'] in ('ok','disabled','busy') else 1)
 except Exception as exc:print(json.dumps({'status':'failed','error':'config_error' if isinstance(exc,ValueError) else 'collection_failed'}));sys.exit(1)
if __name__=='__main__':main()
