"""deals HTTP routes, extracted without changing business behavior.

The entry point keeps security, body parsing, dispatch order and fallback.
"""
from urllib.parse import parse_qs, unquote
import re


def get_route(handler, parts):
    self = handler
    segments = [unquote(x) for x in parts.path.split('/') if x]
    query = parse_qs(parts.query)
    store = self.server.store
    if parts.path == '/api/deals':
        return self.reply(200, store.deals.listing(month=query.get('month',[''])[0], q=query.get('q',[''])[0], stage=query.get('stage',[''])[0], payment=query.get('payment',[''])[0], archived=query.get('archived',['0'])[0]=='1'))
    if parts.path == '/api/deals/proposals/status':
        params=parse_qs(parts.query,keep_blank_values=True)
        if set(params)-{'ids'} or len(params.get('ids',[]))!=1:raise ValueError('建议状态参数无效')
        return self.reply(200,self.server.store.deals.proposal_status(params['ids'][0].split(',') if params['ids'][0] else []))
    if parts.path == '/api/deals/options':
        return self.reply(200, store.deals.options())
    if parts.path == '/api/deals/review':
        return self.reply(200, store.deals_review.summary())
    if parts.path == '/api/deals/review/report':
        params = parse_qs(parts.query, keep_blank_values=True)
        if set(params) - {'week'} or len(params.get('week', [])) != 1:
            raise ValueError('周报参数无效')
        return self.reply(200, store.deals_review.report(params['week'][0]))
    if len(segments)==3 and segments[:2]==['api','deals']:
        return self.reply(200, store.deals.detail(segments[2]))
    return None


def post_route(handler, segments, payload):
    self = handler
    if segments[:2]==['api','deals']:
        from deals_store import DealConflict
        try:
            deals=self.server.store.deals
            if segments==['api','deals']:return self.reply(201,deals.save(payload))
            if segments==['api','deals','from-clue']:return self.reply(201,deals.from_clue(payload))
            if segments==['api','deals','apply-proposal']:return self.reply(200,deals.apply_proposal(payload))
            if len(segments)==4 and segments[3]=='scan-wechat':
                from deals_store import fields
                fields(payload,('query',));return self.reply(200,deals.scan_evidence(segments[2],payload.get('query','')))
            if len(segments)==4 and segments[3]=='evidence':return self.reply(200,deals.review_evidence(segments[2],payload))
            if segments==['api','deals','import-initial']:
                if payload!={}:raise ValueError('导入参数无效')
                return self.reply(200,deals.import_initial())
            if len(segments)==4 and segments[3] in ('receipt','deliverable','publication','publication-edit','void'):
                return self.reply(200,deals.child(segments[2],segments[3],payload))
            return self.reply(404,{'error':'商单接口不存在'})
        except DealConflict as exc:return self.reply(409,{'error':str(exc)})
    return None
