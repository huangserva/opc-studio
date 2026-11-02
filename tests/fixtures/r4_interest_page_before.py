"""冻结 R4 前兴趣合并分页逻辑，仅供逐字段差分回归。"""
from datetime import datetime,timedelta,timezone
from wechat_tools_view import cards
def interest_page(root, query, now=None):
    from interest_channel import page_view,snapshot
    from grok_bot_radar import stamp,BEIJING
    now=now or datetime.now(timezone.utc);page=int(query.get('page',['1'])[0]);period=query.get('range',['today'])[0]
    base_query={**query,'page':['1']};base=page_view(root,base_query,now);grok_items=list(base['items'])
    # Reuse all existing Grok filtering/feedback semantics, then paginate the union.
    for number in range(2,(base['total']+49)//50+1):grok_items.extend(page_view(root,{**base_query,'page':[str(number)]},now)['items'])
    cutoff=now.astimezone(BEIJING).replace(hour=0,minute=0,second=0,microsecond=0) if period=='today' else now-timedelta(hours=48 if period=='48h' else 168)
    tools=cards(root,include_filtered=True);selected=[];filtered=[]
    get=lambda k:query.get(k,[''])[0]
    for item in tools:
        if not cutoff<=stamp(item['fetched_at'])<=now:continue
        if get('topic') and get('topic')!=item['topic']:continue
        if get('author') and get('author')!=item['author']['name']:continue
        if get('q').casefold() not in (item['title']+item['full_text']+item['author']['name']+item['topic']).casefold():continue
        (filtered if item['filtered'] else selected).append(item)
    grok_times={p['id']:p['fetched_at'] for p in snapshot(root,now)[0]} if grok_items else {}
    combined=grok_items+selected;combined.sort(key=lambda p:(p['content_insufficient'],-stamp(p.get('fetched_at') or grok_times.get(p['id']) or p['published_at']).timestamp(),p['id']))
    filtering=base['filtering'];filtering['total']+=len(filtered);filtering['by_source']['微信工具']=dict(total=len(selected)+len(filtered),visible=len(selected),filtered=len(filtered),ratio=len(filtered)/(len(selected)+len(filtered)) if selected or filtered else 0,insufficient=0)
    filtering['items'].extend(dict(id=p['id'],title=p['title'],url=p['url'],sources=['微信工具'],hidden=True,reason='相关度低于阈值或未通过相关性闸门') for p in filtered)
    return {**base,'items':combined[(page-1)*50:page*50],'page':page,'total':len(combined),'sources':{**base['sources'],'W':dict(updated_at=max((p['fetched_at'] for p in tools),default=None),error=None)},'topics':sorted(set(base['topics'])|{p['topic'] for p in tools})}
