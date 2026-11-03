"""可删除的兴趣读模型。请求只读摘要；更新单飞后台执行，源库始终只读。"""
from __future__ import annotations
from contextlib import closing,contextmanager
import copy
import hashlib
import json
import logging
import os
from pathlib import Path
import sqlite3
import tempfile
import threading
import time
from datetime import datetime, timezone

SCHEMA=3
LOCK=threading.RLock()
STATES={}
LOG=logging.getLogger(__name__)

def path(root):
    from interest_sources import workspace
    return workspace(root)/'today/interest-readmodel-v2.sqlite3'

def dumps(value):return json.dumps(value,ensure_ascii=False,separators=(',',':'),allow_nan=False)

def probe(root,now):
    """便宜的变更提示，不作内容版本；无关写入只触发后台核对。"""
    import interest_channel as I,interest_sources as S
    from today_view import feedback_path
    reports=I.grok.reports_dir().resolve();base=S.workspace(root)
    files=[p for p,_ in S.files(root,now,reports)]
    files.extend([*reports.joinpath('interest').glob('*'),reports/'interest-topics.md',reports/'feedback/studio-feedback.json',I.grok.feedback_path(root),feedback_path(root),I.grok.BUNDLE_PATH])
    for rel in ('clues/clues.db','knowledge/knowledge.db','github/github.db','intel/intel.db','aisa/aisa.db','xradar/xradar.db'):
        p=base/rel;files.extend([p,Path(str(p)+'-wal')])
    entries=[]
    for p in files:
        try:st=Path(p).stat();entries.append((str(p),st.st_mtime_ns,st.st_size))
        except FileNotFoundError:entries.append((str(p),None,None))
    return hashlib.sha256(dumps(entries).encode()).hexdigest()

def fingerprint(root,now):
    """仅后台调用；profile/官方名单不会使兴趣匹配投影失效。"""
    import interest_channel as I,interest_sources as S
    from interest_match import VERSION
    from today_view import feedback_path
    reports=I.grok.reports_dir().resolve()
    files=[*reports.joinpath('interest').glob('*.json'),reports/'feedback/studio-feedback.json',I.grok.feedback_path(root),feedback_path(root),I.grok.BUNDLE_PATH]
    parts=[SCHEMA,VERSION,S.signature(root,now,reports),I.topics()['topics']]
    for p in files:
        try:parts.append((str(p),S.content_digest(p)))
        except FileNotFoundError:parts.append((str(p),None))
    return hashlib.sha256(dumps(parts).encode()).hexdigest()

def publish(root,now=None):
    """显式离线构建，或后台调用；一次原子替换三个时间范围。"""
    import interest_channel as I
    from today_view import SUMMARY
    import interest_match as M
    now=now or datetime.now(timezone.utc)
    import interest_sources as S
    source_key=(str(S.workspace(root).resolve()),str(I.grok.reports_dir().resolve()))
    with S.LOCK:
        if source_key in S.STATES:S.STATES[source_key]['checked']=0
    indexed,status=S.candidates(root,now)
    if status.get('refreshing'):
        key=(str(S.workspace(root).resolve()),str(I.grok.reports_dir().resolve()))
        with S.LOCK:worker=S.STATES[key].get('worker')
        if worker is not None:worker.join()
        with S.LOCK:indexed=S.STATES[key]['data']
    if not indexed.get('ready') or indexed.get('signature')!=S.signature(root,now,I.grok.reports_dir().resolve()):
        raise ValueError('source_changed_during_index')
    target=path(root)
    if target.is_symlink() or target.parent.is_symlink():raise ValueError('readmodel_link')
    target.parent.mkdir(parents=True,exist_ok=True)
    fd,name=tempfile.mkstemp(prefix='.interest-readmodel-',dir=target.parent);os.close(fd)
    try:
        with closing(sqlite3.connect(name)) as db,db:
            db.executescript('CREATE TABLE meta(period TEXT PRIMARY KEY,data TEXT); CREATE TABLE cards(period TEXT,ordinal INTEGER,id TEXT,summary TEXT,detail TEXT,search TEXT,author TEXT,PRIMARY KEY(period,id)); CREATE TABLE audit(period TEXT,ordinal INTEGER,data TEXT,PRIMARY KEY(period,ordinal));')
            sig=fingerprint(root,now)
            fields=(*SUMMARY,'first_seen_at','last_seen_at','first_seen_basis','channel','what_new','usable','relevance','incomplete_reasons','canonical_key','clue_id','identity_state','input_sources','interest_matches','discovery_overlap','interest_rule_version','interest_rank',*M.TOOL_FIELDS,'_discovery_candidates','author_type','author_verification_type')
            for period in ('today','48h','7d'):
                result=I._cross_source_page(root,{'range':[period]},now)
                if not result['interest_cache'].get('ready'):raise ValueError('source_index_not_ready')
                until=result.pop('_valid_until').isoformat()
                items=result.pop('items');audit=result['filtering'].pop('items')
                for n,p in enumerate(items):
                    summary={k:p.get(k) for k in fields if k in p}
                    search=(p['title']+p['full_text']+str(p['author'])+' '.join(m['name'] for m in p['interest_matches'])).casefold()
                    author=[p['author'].get('name','').casefold(),p['author'].get('handle','').casefold()]
                    db.execute('INSERT INTO cards VALUES(?,?,?,?,?,?,?)',(period,n,p['id'],dumps(summary),dumps(p),search,dumps(author)))
                db.executemany('INSERT INTO audit VALUES(?,?,?)',[(period,n,dumps(p)) for n,p in enumerate(audit)])
                db.execute('INSERT INTO meta VALUES(?,?)',(period,dumps(dict(schema=SCHEMA,signature=sig,probe=probe(root,now),start=now.isoformat(),until=until,result=result))))
        if fingerprint(root,now)!=sig:raise ValueError('source_changed_during_projection')
        os.chmod(name,0o600);os.replace(name,target)
    finally:
        if os.path.exists(name):os.unlink(name)
    return dict(path=str(target),signature=sig,bytes=target.stat().st_size)

@contextmanager
def connect(root):
    db=sqlite3.connect(path(root).resolve().as_uri()+'?mode=ro',uri=True,timeout=.05)
    try:
        db.execute('PRAGMA query_only=ON')
        yield db
    finally:db.close()

def state(root):
    key=str(path(root).resolve())
    with LOCK:return STATES.setdefault(key,dict(running=False,updating=False,checked=0,error=None,worker=None,probe=None))

def refresh(root,st,now):
    """所有同根/范围共用一名更新者；耗时操作不持有请求锁。"""
    try:
        initial_probe=probe(root,now)
        current=None
        try:
            with connect(root) as db:current=json.loads(db.execute("SELECT data FROM meta WHERE period='7d'").fetchone()[0])
        except (OSError,sqlite3.Error,ValueError,TypeError):pass
        sig=fingerprint(root,now)
        expired=not current or now>=datetime.fromisoformat(current['until'])
        if expired or current['signature']!=sig:
            with LOCK:st['updating']=True
            publish(root,now)
        with LOCK:st.update(error=None,probe=initial_probe)
    except Exception as exc:
        LOG.warning('interest_readmodel_refresh_failed kind=%s',type(exc).__name__)
        with LOCK:st['error']='兴趣读模型更新失败，保留上次完整结果'
    finally:
        with LOCK:st.update(running=False,updating=False)

def schedule(root,st,now,*,force=False):
    with LOCK:
        if st['running'] or not force and time.monotonic()-st['checked']<1:return
        st['running']=True;st['checked']=time.monotonic()
        worker=threading.Thread(target=refresh,args=(root,st,now),name='interest-readmodel',daemon=True)
        st['worker']=worker;worker.start()

def empty(root):
    import interest_channel as I
    return dict(channel='interest',items=[],total=0,page=1,page_size=50,top=[],top_message='',sources={},filtering=dict(by_source={},items=[],total=0,policy='正在首次建立跨来源兴趣读模型'),configured_topics=I.topics()['topics'],daily_counts={},sort_policy='',interest_cache=dict(ready=False,partial=True,refreshing=True,error=None,built_at=None,build_ms=None))

def page(root,query):
    now=datetime.now(timezone.utc);period=query.get('range',['today'])[0]
    if period not in ('today','48h','7d'):raise ValueError('时间范围只能是 today、48h、7d')
    if int(query.get('page',['1'])[0])<1:raise ValueError('页码至少为 1')
    st=state(root)
    try:
        with connect(root) as db:
            record=json.loads(db.execute('SELECT data FROM meta WHERE period=?',(period,)).fetchone()[0])
            if record['schema']!=SCHEMA:raise ValueError('readmodel_schema')
            result=record['result'];q=query.get('q',[''])[0].casefold();author=query.get('author',[''])[0].casefold()
            sql='SELECT summary FROM cards WHERE period=?'
            args=[period]
            if q:sql+=' AND instr(search,?)>0';args.append(q)
            if author:sql+=' AND EXISTS(SELECT 1 FROM json_each(cards.author) WHERE value=?)';args.append(author)
            sql+=' ORDER BY ordinal'
            result['items']=[json.loads(row[0]) for row in db.execute(sql,args)]
            result['filtering']['items']=[] if query.get('audit')==['0'] else [json.loads(row[0]) for row in db.execute('SELECT data FROM audit WHERE period=? ORDER BY ordinal',(period,))]
    except (OSError,sqlite3.Error,ValueError,TypeError,KeyError):
        schedule(root,st,now,force=True);return empty(root)
    expired=now>=datetime.fromisoformat(record['until'])
    hint=probe(root,now)
    with LOCK:previous_hint=st.get('probe') or record.get('probe')
    changed=hint!=previous_hint
    if expired or changed:schedule(root,st,now)
    with LOCK:refreshing=expired or st['updating'];checking=st['running'] and not st['updating'];error=st['error']
    result['interest_cache'].update(ready=not refreshing,partial=False,refreshing=refreshing,checking=checking,error=error)
    result['interest_cache']['projection']=dict(source='readmodel',fallbacks={},stale=expired)
    return result

def detail(root,identifier):
    try:
        with connect(root) as db:
            row=db.execute("SELECT detail FROM cards WHERE period='7d' AND id=?",(identifier,)).fetchone()
            if row:return json.loads(row[0])
    except (OSError,sqlite3.Error,ValueError):pass
    return targeted_detail(root,identifier)

def targeted_detail(root,identifier):
    """读模型缺失时，只聚合用户点开的那条；不计算整池/计数/发现列表。"""
    import interest_channel as I,interest_sources as S,interest_match as M
    from today_view import prepare_content,_load_feedback
    from wechat_tools_view import cards
    key=(str(S.workspace(root).resolve()),str(I.grok.reports_dir().resolve()))
    with S.LOCK:data=(S.STATES.get(key) or {}).get('data')
    if data is None:
        try:data=S.read_json(S.cache_path(root))
        except (OSError,ValueError):data={'items':[]}
    directions=M.compile_topics(I.topics()['topics']);live=[]
    current,_=I.snapshot(root)
    try:tools=cards(root,include_filtered=True)
    except (OSError,ValueError,sqlite3.Error):tools=[]
    for p in current+tools:
        p=copy.deepcopy(p);p.update(canonical_key=p['id'],identity_state='k1' if p.get('clue_id') else 'pending_ingest',input_sources=['微信群工具' if p.get('kind')=='tool' else 'Grok 兴趣'],input_ids=[p['id']],source_topics=[p.get('topic','')],interest_original=True);live.append(p)
    try:S.attach_k1(root,live)
    except (OSError,ValueError,sqlite3.Error):pass
    values=[copy.deepcopy(p) for p in [*data['items'],*live] if identifier in (p.get('id'),p.get('canonical_key'))]
    for p in values:p['interest_matches']=[] if p.get('filtered') else M.classify(p,directions)
    if not any(p['interest_matches'] for p in values):return None
    result=M.deduplicate(values)
    if not result:return None
    p=result[0];prepare_content(p);p.update(topic=p['interest_matches'][0]['name'],discovery_overlap=False,interest_rule_version=M.VERSION,interest_rank=dict(direction=max(m['weight'] for m in p['interest_matches']),trust=max(S.SOURCE_TRUST.get(s,1) for s in p['input_sources'])))
    try:record=_load_feedback(root).get('interest:'+p['id'])
    except ValueError:record=None
    if record:p.update(status=record.get('status','new'),feedback=record.get('feedback'))
    p.setdefault('official',None)
    return p
