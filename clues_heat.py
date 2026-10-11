"""Local heat calculations; SVG features are not post counts. No network/model calls."""
from datetime import datetime, timezone, timedelta
import math
import re

HEAT_THRESHOLDS = {'rising_ratio': 1.25, 'fading_ratio': 0.55, 'min_buckets': 2}
SITE_HEAT = {'up':'rising', 'peak':'peak', 'down':'fading',
             'rising':'rising', 'fading':'fading'}
SITE_EXPIRY_DAYS = 3


def bucket_time(value):
    if not isinstance(value,str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}(?:T(?:00|06|12|18))?',value):
        raise ValueError('观测时间槽无效')
    return datetime.fromisoformat(value).replace(tzinfo=timezone.utc)


def capture_bucket(value):
    date=datetime.fromisoformat(value.replace('Z','+00:00'))
    if date.utcoffset() is None:raise ValueError('抓取时间缺少时区')
    date=date.astimezone(timezone.utc)
    return date.replace(hour=date.hour//6*6,minute=0,second=0,microsecond=0).strftime('%Y-%m-%dT%H')


def svg_features(bars):
    if len(bars)<12 or any(type(x) not in (int,float) or not math.isfinite(x) or x<0 for x in bars):return {}
    recent=sum(bars[-6:]);previous=sum(bars[-12:-6])
    return {'bars_recent6':round(recent,2),'bars_prev6':round(previous,2),
            'ratio6_site':round((recent+.1)/(previous+.1),3)}


def ratio_heat(ratio,thresholds=HEAT_THRESHOLDS):
    return 'rising' if ratio>=thresholds['rising_ratio'] else 'fading' if ratio<=thresholds['fading_ratio'] else 'peak'


def compute_heat(series,thresholds=HEAT_THRESHOLDS,prefer_site=True):
    """Return (heat, confidence, reason). GitHub thresholds intentionally not defined.

    Approved 22h correction: ratio6_site uses normalized SVG heights (+0.1), not
    successive capture post counts. Post-ratio fallback requires adjacent UTC slots;
    its accuracy remains unvalidated and is not enabled for production recomputes.
    """
    rows=sorted((r for r in series if r.get('source')=='xradar'),key=lambda r:r['bucket'])
    if not rows:return '',None,'无支持的来源序列；GitHub computed 阈值待回测'
    latest=rows[-1]
    if prefer_site and latest.get('site_state') in SITE_HEAT:return SITE_HEAT[latest['site_state']],1.,'站方状态'
    ratio=latest.get('ratio6_site')
    if type(ratio) in (int,float) and math.isfinite(ratio) and ratio>=0:
        return ratio_heat(ratio,thresholds),None,'站内 SVG 最近6h/前6h（非真实帖数）'
    if len(rows)<thresholds['min_buckets']:return '',None,'观测不足'
    previous=rows[-2]
    if bucket_time(latest['bucket'])-bucket_time(previous['bucket'])!=timedelta(hours=6):return '',None,'非相邻6h槽'
    base=previous['mentions'];recent=latest['mentions']
    if base<=0:return '',None,'前槽为零，比例未定义'
    return ratio_heat(recent/base,thresholds),None,'跨抓取槽 posts 比值（准确率未验证）'


def recompute_heat(store,today=None,cancel=None,deadline=None,dry_run=False,sample_limit=10):
    """Local daily edit audit. Manual frozen; site expires after three missing days.

    dry_run=True：只报告将发生的改动，不写任何东西 —— 不写 edit entry、
    不前进 revision、不改 heat。用于启用前的首次影响评估（复核 M1：
    首个窗口在真实语料上一次会写 243 条 edit entry）。
    返回多带 would_update / site_to_computed / site_cleared / computed / samples / dry_run。
    默认行为（不传 dry_run）与之前逐字相同。
    """
    day=bucket_time(today or datetime.now(timezone.utc).date().isoformat())
    results=[];would=[];site_to_computed=0;site_cleared=0;computed_total=0
    with store.lock:
        with store.db() as db:ids=[r[0] for r in db.execute('SELECT DISTINCT thread_id FROM observations')]
        for ident in ids:
            if cancel is not None and cancel.is_set():break
            if deadline is not None:
                import time
                if time.monotonic()>=deadline:break
            before=store.detail(ident)
            if before.get('heat_source')=='manual':continue
            series=store.observations(ident);xrows=[r for r in series if r['source']=='xradar']
            if not xrows:continue
            latest=xrows[-1]
            site_dates=[bucket_time(r['bucket']) for r in xrows if r['site_state'] in SITE_HEAT]
            last_site=max(site_dates,default=None)
            if before.get('heat_source')=='site' and last_site and (day.date()-last_site.date()).days<SITE_EXPIRY_DAYS:continue
            active_site=latest['site_state'] in SITE_HEAT and (day.date()-bucket_time(latest['bucket']).date()).days<SITE_EXPIRY_DAYS
            value,_,reason=compute_heat(xrows,prefer_site=active_site)
            # Only approved SVG-derived computed signal is enabled; a capture-post
            # ratio without features is exposed by the pure function for trial tests.
            if not active_site and latest.get('ratio6_site') is None:value='';reason='站内特征不足；跨抓取槽口径待验证'
            source='site' if value and active_site else 'computed' if value else ''
            if (value,source)==(before.get('heat',''),before.get('heat_source','')):continue
            if source=='computed':computed_total+=1
            if before.get('heat_source')=='site':
                if source=='computed':site_to_computed+=1
                elif source=='':site_cleared+=1
            would.append({'id':ident,'from':before.get('heat_source','')+'/'+before.get('heat',''),
                          'to':source+'/'+value,'reason':reason})
            if dry_run:continue
            saved=store._save({'id':ident,'revision':before['revision'],'heat':value,'heat_source':source,
                              'reason':'每日 heat 重算：'+reason},[])
            results.append(saved)
    report={'updated':len(results),'items':results}
    if dry_run:
        report.update(dry_run=True,would_update=len(would),site_to_computed=site_to_computed,
                      site_cleared=site_cleared,computed=computed_total,samples=would[:max(int(sample_limit),0)])
    return report


def github_observations(provenance,captured_at):
    """Daily unique comments by creation date, not edit date; unknown authors excluded."""
    days={};seen=set()
    for c in provenance.get('comments',[]):
        key=c.get('id') or c.get('url')
        if not key or key in seen:continue
        try:
            date=datetime.fromisoformat(c.get('created_at','').replace('Z','+00:00'))
            if date.utcoffset() is None:continue
        except (ValueError,TypeError):continue
        seen.add(key);day=date.astimezone(timezone.utc).date().isoformat()
        group=days.setdefault(day,{'mentions':0,'authors':set()});group['mentions']+=1
        author=c.get('author')
        if author and author!='未知作者':group['authors'].add(author.casefold())
    return [dict(source='github',bucket=day,mentions=g['mentions'],talkers=len(g['authors']),views=None,
                 site_state='',captured_at=captured_at) for day,g in sorted(days.items())]
