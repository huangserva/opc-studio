"""Read models and explicit user feedback; extraction never imports this writer."""
from datetime import datetime, timedelta, timezone
import base64
import json
from pathlib import Path
from urllib.parse import urlsplit
import clue_store_v2 as S
import clue_ingest as I
import wechat_tools_config as config
from wechat_tools import POLICY


def cards(root, *, include_filtered=False):
    path=config.workspace(root)/'clues/clues.db'
    if not path.exists():return []
    db=S.connect(path,True)
    try:
        db.execute('PRAGMA busy_timeout=50') # 今日只读入口不等待采集写事务；调用方保留来源异常提示。
        db.execute('BEGIN')
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name='clue_channels'").fetchone():return []
        grouped={}
        for row in db.execute("SELECT o.*,c.metadata_json,c.channel,r.first_seen_at AS fetched FROM clue_channels c JOIN clue_objects o ON o.id=c.clue_id JOIN source_receipts r ON r.clue_id=o.id AND r.channel=c.channel WHERE c.source='wechat_tools' AND r.import_status='imported' ORDER BY r.first_seen_at,c.channel"):
            meta=json.loads(row['metadata_json']);item=grouped.setdefault(row['id'],dict(row=row,metadata=meta,channels=set(),first=meta['shared_at'],fetched=row['fetched'],filtered=True,curated=False))
            item['channels'].add(row['channel']);item['first']=min(item['first'],meta['shared_at']);item['filtered'] &= meta.get('filtered',False);item['curated'] |= meta.get('curated',False)
            if row['fetched']>item['fetched']:item['fetched']=row['fetched'];item['metadata']=meta
        result=[]
        for cid,item in grouped.items():
            if item['filtered'] and not include_filtered:continue
            row=item['row'];meta=item['metadata'];assignments=[dict(r) for r in db.execute("SELECT a.state,t.name,t.kind FROM topic_assignments a JOIN topics t ON t.id=a.topic_id WHERE a.clue_id=?",(cid,))]
            tools=any(t['kind']=='collection' and t['state']=='confirmed' and t['name']=='工具' for t in assignments)
            topic=next((t['name'] for t in assignments if t['kind']=='direction' and t['state']=='suggested'),'待归类')
            destinations=[dict(r) for r in db.execute("SELECT to_key,relation_type,evidence_json FROM clue_relations WHERE from_id=? AND relation_type IN ('primary_destination','additional_destination','user_feedback')",(cid,))]
            kept=any(d['relation_type'] in ('primary_destination','additional_destination') and d['to_key'].startswith('items:') for d in destinations)
            feedback=next((json.loads(d['evidence_json']).get('feedback') for d in destinations if d['relation_type']=='user_feedback'),None)
            v=dict(id=row['canonical_key'],clue_id=cid,kind='tool',source='wechat',sources=['W'],channel='interest',title=row['title'],url=meta['url'],
                   author=dict(name=meta['group'],handle='',followers=None),published_at=item['first'],fetched_at=item['fetched'],
                   full_text=meta['purpose']+'\n\n怎么用：'+meta['usage'],what_new=meta['purpose'],why='精选群白名单自动收录' if tools else '跨来源兴趣候选',
                   usable=[dict(type='other',url=meta['url'],note=meta['name'])],relevance=meta.get('relevance'),tier='微信工具',topic=topic,
                   metrics={},fit_score=None,doable=None,hot=False,content_complete=True,content_insufficient=False,incomplete_reasons=[],
                   relevance_note=None,rss_boards=[],reply_change=None,status='kept' if kept else 'new',feedback=feedback,
                   in_tools=tools,direction_state='pending_confirmation',share_count=len(item['channels']),first_shared_at=item['first'],
                   classification=meta['classification'],tool_version=meta['version'],verification=meta['verification'],filtered=item['filtered'])
            result.append(v)
        return result
    finally:db.close()


def overlay_feedback(root,items):
    """只读当前页工具的分拣状态，避免预建摘要回退用户刚点的存/准/不准。"""
    targets={p['clue_id']:p for p in items if p.get('kind')=='tool' and p.get('clue_id')}
    if not targets:return items
    path=config.workspace(root)/'clues/clues.db'
    if not path.is_file():return items
    db=None
    try:
        db=S.connect(path,True);db.execute('PRAGMA busy_timeout=50');db.execute('BEGIN')
        marks=','.join('?' for _ in targets)
        grouped={key:[] for key in targets}
        for row in db.execute("SELECT from_id,to_key,relation_type,evidence_json FROM clue_relations WHERE from_id IN ("+marks+") AND relation_type IN ('primary_destination','additional_destination','user_feedback')",list(targets)):
            grouped[row['from_id']].append(row)
        for key,p in targets.items():
            rows=grouped[key]
            p['status']='kept' if any(r['relation_type'] in ('primary_destination','additional_destination') and r['to_key'].startswith('items:') for r in rows) else 'new'
            p['feedback']=next((json.loads(r['evidence_json']).get('feedback') for r in rows if r['relation_type']=='user_feedback'),None)
        assert db.total_changes==0
    except (OSError,ValueError,__import__('sqlite3').DatabaseError) as exc:
        __import__('logging').getLogger(__name__).warning('interest_tool_feedback_read_unavailable kind=%s',type(exc).__name__)
    finally:
        if db is not None:db.close()
    return items


def collection_inbox(path, *, collection, limit=30, cursor=None, source=None, channel=None, status=None):
    if collection!='tools':raise ValueError('未知集合')
    if not 1<=limit<=100:raise ValueError('limit 必须为1–100')
    if status and status not in ('pending','discarded','archived','triaged'):raise ValueError('未知状态')
    if not Path(path).exists():return dict(items=[],next_cursor=None,schema_version=S.SCHEMA_VERSION,ready=False)
    db=S.connect(path,True)
    try:
        db.execute('BEGIN')
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name='topic_assignments'").fetchone():return dict(items=[],next_cursor=None,schema_version=S.SCHEMA_VERSION,ready=False)
        where=["a.topic_id='collection:tools'","a.state='confirmed'"];args=[]
        for key,value in [('source',source),('channel',channel)]:
            if value:where.append('EXISTS(SELECT 1 FROM clue_channels c WHERE c.clue_id=o.id AND c.'+key+'=?)');args.append(value)
        if status:where.append('o.triage_state=?');args.append(status)
        if cursor:
            try:
                if len(cursor)>512:raise ValueError()
                values=json.loads(base64.urlsafe_b64decode(cursor+'='*(-len(cursor)%4)))
                if not isinstance(values,list) or len(values)!=2 or any(not isinstance(x,str) for x in values):raise ValueError()
                where.append('(o.last_seen_at,o.id)<(?,?)');args.extend(values)
            except Exception:raise ValueError('cursor 无效') from None
        rows=[dict(r) for r in db.execute('SELECT o.* FROM topic_assignments a JOIN clue_objects o ON o.id=a.clue_id WHERE '+' AND '.join(where)+' ORDER BY o.last_seen_at DESC,o.id DESC LIMIT ?',args+[limit+1])]
        next_cursor=base64.urlsafe_b64encode(S.dumps([rows[limit-1]['last_seen_at'],rows[limit-1]['id']]).encode()).decode().rstrip('=') if len(rows)>limit else None
        rows=rows[:limit]
        for row in rows:
            row.pop('batch_id',None);row['channels']=[dict(r) for r in db.execute('SELECT channel,source FROM clue_channels WHERE clue_id=? ORDER BY channel',(row['id'],))]
            row['resources']=[dict(r) for r in db.execute('SELECT resource_type,url,version,evidence_id FROM resource_links WHERE clue_id=?',(row['id'],))]
        return S.wire_numbers(dict(items=rows,next_cursor=next_cursor,schema_version=S.SCHEMA_VERSION,ready=True,collection=collection))
    finally:db.close()


def interest_page(root, query, now=None, *, unpaged=False):
    from interest_channel import page_view,snapshot
    from grok_bot_radar import stamp,BEIJING
    now=now or datetime.now(timezone.utc);page=int(query.get('page',['1'])[0]);period=query.get('range',['today'])[0]
    base_query={**query,'page':['1']};base=page_view(root,base_query,now,unpaged=True);grok_items=list(base['items'])
    # Reuse all existing Grok filtering/feedback semantics, then paginate the union.

    cutoff=now.astimezone(BEIJING).replace(hour=0,minute=0,second=0,microsecond=0) if period=='today' else now-timedelta(hours=48 if period=='48h' else 168)
    tools=cards(root,include_filtered=True);selected=[];filtered=[]
    get=lambda k:query.get(k,[''])[0]
    for item in tools:
        if not cutoff<=stamp(item['fetched_at'])<=now:continue
        if get('topic') and get('topic')!=item['topic']:continue
        if get('author') and get('author')!=item['author']['name']:continue
        if get('q').casefold() not in (item['title']+item['full_text']+item['author']['name']+item['topic']).casefold():continue
        (filtered if item['filtered'] else selected).append(item)
    grok_times={p['id']:p.get('fetched_at') for p in grok_items}
    combined=grok_items+selected;combined.sort(key=lambda p:(p['content_insufficient'],-stamp(p.get('fetched_at') or grok_times.get(p['id']) or p['published_at']).timestamp(),p['id']))
    filtering=base['filtering'];filtering['total']+=len(filtered);filtering['by_source']['微信工具']=dict(total=len(selected)+len(filtered),visible=len(selected),filtered=len(filtered),ratio=len(filtered)/(len(selected)+len(filtered)) if selected or filtered else 0,insufficient=0)
    filtering['items'].extend(dict(id=p['id'],title=p['title'],url=p['url'],sources=['微信工具'],hidden=True,reason='相关度低于阈值或未通过相关性闸门') for p in filtered)
    return {**base,'items':combined if unpaged else combined[(page-1)*50:page*50],'page':page,'total':len(combined),'sources':{**base['sources'],'W':dict(updated_at=max((p['fetched_at'] for p in tools),default=None),error=None)},'topics':sorted(set(base['topics'])|{p['topic'] for p in tools})}


def save_item_explicit(root,item,action):
    if action!='keep':raise ValueError('写素材库必须由用户显式点击存')
    from items_store import ItemsStore
    # Reference the clue, never copy message references/raw evidence into items.
    return ItemsStore(root).upsert({**item,'status':'kept','legacy_refs':[dict(namespace='clue',id=item['clue_id'])],
                                   'analysis':dict(clue_id=item['clue_id'],additional_destination=True),
                                   'author':dict(name=item['author']['name'],handle='')})


def feedback(root,payload):
    if not isinstance(payload,dict) or set(payload)!={'id','channel','action'} or payload['channel']!='interest' or payload['action'] not in ('keep','accurate','inaccurate','clear'):
        raise ValueError('工具反馈参数无效')
    item=next((p for p in cards(root) if p['id']==payload['id']),None)
    if not item:raise ValueError('工具线索不存在')
    path=config.workspace(root)/'clues/clues.db';real=path.resolve()==S.REAL_DB.resolve();action=payload['action']
    with I.ingest_lock(path,real) as acquired:
        if not acquired:raise config.Conflict('线索正在导入，请稍后重试')
        batch='wechat-tool-action:'+S.identity(item['id'],action)
        I.register_batch(path,batch,allow_real=real)
        if action=='keep':
            saved=save_item_explicit(root,item,action)
            with I.short_transaction(path,real) as db:
                destination='items:'+saved['id']
                primary=db.execute("SELECT to_key FROM clue_relations WHERE from_id=? AND relation_type='primary_destination'",(item['clue_id'],)).fetchone()
                if not primary or primary[0]!=destination:
                    kind='additional_destination' if primary else 'primary_destination'
                    relation_id=S.identity(item['clue_id'],'items',saved['id']) if primary else S.identity(item['clue_id'],'primary_destination')
                    S.write(db,'clue_relations',relation_id,dict(from_id=item['clue_id'],to_key=destination,relation_type=kind,evidence_json=S.dumps(dict(actor='user',explicit_action='keep',recorded_at=S.now()))),batch)
        else:
            with I.short_transaction(path,real) as db:
                S.write(db,'clue_relations',S.identity(item['clue_id'],'feedback'),dict(from_id=item['clue_id'],to_key='user:feedback',relation_type='user_feedback',evidence_json=S.dumps(dict(actor='user',feedback=None if action=='clear' else action))),batch,update=True)
        S.complete_batch(path,batch,confirmed=True,allow_real=real)
    return dict(id=item['id'],channel='interest',status='kept' if action=='keep' or item['status']=='kept' else 'new',feedback=None if action=='clear' else action if action!='keep' else item['feedback'])
