"""Sanitised tool records only; never accepts raw messages as card content."""
from urllib.parse import urlsplit
import json
import clue_store_v2 as S
import wechat_tools_config as config
from wechat_tools import public_text, tool_url, POLICY, MIN_RELEVANCE, _fixture_topics, suggest_topic

PARSER_VERSION = POLICY


def channel_for(row):
    return 'wechat:tools:'+S.identity(row['group'],row['namespace'],row['message_id'])


def records(data, source='wechat_tools'):
    return [(channel_for(row),row['message_id']+':'+row['issue_date']+':'+str(row['ordinal']),row) for row in data['items']]


def parse(row, meta, source='wechat_tools'):
    url=tool_url(row.get('url'))
    if not url:raise ValueError('not_tool_resource')
    group=config.validate_group(row['group'])
    if row.get('namespace')!='knowledge:wechat' or not isinstance(row.get('message_id'),str) or not row['message_id'].isalnum():
        raise ValueError('invalid_message_reference')
    trusted=config.curated(group,config.read(meta.get('_workspace',S.ROOT/'workspace'))['groups'])
    text={k:public_text(row.get(k))[:500] for k in ('name','purpose','usage','classification')}
    if not all(text[k] for k in ('name','purpose','usage')):raise ValueError('missing_tool_fields')
    from pathlib import Path
    from clue_ingest import current_topics
    root=Path(meta.get('_workspace',S.ROOT/'workspace'))
    topics=current_topics(Path.home()/'grok-bot-reports') if root==S.ROOT/'workspace' else _fixture_topics(root)
    from scripts.radar_common import today_relevance_gate
    relevance=row.get('relevance') if type(row.get('relevance')) is int and 0<=row['relevance']<=3 else None if trusted else 0
    verdict=today_relevance_gate(dict(title=text['name'],full_text=text['purpose']+'\n'+text['usage'],sources=['W']),topics,'interest')
    filtered=not trusted and (relevance<MIN_RELEVANCE or not verdict['visible'])
    version=row.get('version')
    if version is not None and (not isinstance(version,str) or len(version)>80):raise ValueError('invalid_version')
    key='tool:'+S.digest([url,version]);channel=channel_for(row);record_key=row['message_id']+':'+row['issue_date']+':'+str(row['ordinal'])
    rid=S.identity(channel,record_key,S.digest(row))
    verification=row.get('verification') or {}
    checked=verification.get('state')=='checked' and type(verification.get('http_status')) is int and 200<=verification['http_status']<300
    # Allowlist every emitted field; unknown model/JSON fields never enter storage.
    metadata={**text,'group':group,'namespace':'knowledge:wechat','message_id':row['message_id'],'issue_date':row['issue_date'],
              'shared_at':S.utc(row['shared_at']),'url':url,'version':version,'curated':trusted,
              'relevance':relevance,'filtered':filtered,
              'filter_reason':('相关度低于阈值' if relevance<MIN_RELEVANCE else verdict['reason']) if filtered else '',
              'verification':dict(state='checked' if checked else 'unverified',http_status=verification.get('http_status') if type(verification.get('http_status')) is int else None,title=public_text(verification.get('title'))[:200],reason=public_text(verification.get('reason'))),
              'direction_state':'pending_confirmation','policy':POLICY}
    return dict(canonical_key=key,platform='web',kind='tool',canonical_url=url,title=text['name'],summary=text['purpose'],
                body='',rich={},author_key=None,published_at=S.utc(row['shared_at']),first_seen_at=S.utc(row['shared_at']),observed_at=S.utc(row['shared_at']),bookmarked_at=None,
                content_complete=True,incomplete_reason=None,metrics={k:None for k in S.METRICS},missing_reasons={k:'tool_metric_unavailable' for k in S.METRICS},privacy_scope='private',
                aliases=[key],channel_metadata=metadata,suggested_topic=suggest_topic(dict(name=text['name'],purpose=text['purpose'],usage=text['usage']),topics),
                evidence=[dict(privacy_scope='private',reference=dict(namespace='knowledge:wechat',message_id=row['message_id']))],
                resources=[dict(type='tool',url=url,version=version,evidence_id=S.identity(rid,0))])


def after_persist(db,item,cid,rid,batch_id):
    metadata=item['channel_metadata']
    if metadata['curated']:
        S.write(db,'topic_assignments',S.identity(cid,'collection:tools'),dict(clue_id=cid,topic_id='collection:tools',state='confirmed',confidence=1,reason='exact_group_whitelist',model_version=POLICY,confirmed_by='rule:'+POLICY),batch_id)
        if not db.execute("SELECT 1 FROM clue_relations WHERE from_id=? AND relation_type='primary_destination'",(cid,)).fetchone():
            S.write(db,'clue_relations',S.identity(cid,'primary_destination'),dict(from_id=cid,to_key='collection:tools',relation_type='primary_destination',evidence_json=S.dumps(dict(rule=POLICY,receipt_id=rid))),batch_id)
    first=S.utc(metadata['shared_at'])
    prior=db.execute('SELECT first_seen_at FROM clue_objects WHERE id=?',(cid,)).fetchone()[0]
    if first and first<prior:
        S.write(db,'clue_objects',cid,dict(first_seen_at=first),batch_id,update=True)
    host=urlsplit(item['canonical_url']).hostname
    for resource in db.execute("SELECT DISTINCT r.clue_id,r.url,o.canonical_key FROM resource_links r JOIN clue_objects o ON o.id=r.clue_id WHERE r.resource_type='tool' AND r.clue_id!=?",(cid,)):
        if urlsplit(resource['url']).hostname==host and urlsplit(resource['url']).path.rstrip('/')!=urlsplit(item['canonical_url']).path.rstrip('/'):
            # Same host alone is weak evidence: retain a candidate relation, never merge.
            S.write(db,'clue_relations',S.identity(cid,resource['canonical_key'],'possible_duplicate_resource'),dict(from_id=cid,to_key=resource['canonical_key'],relation_type='possible_duplicate_resource',evidence_json=S.dumps(dict(reason='same_host_different_path',confidence=0.5,receipt_id=rid,decision='suggested_only'))),batch_id)
