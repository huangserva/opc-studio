"""intel_collect HTTP routes, extracted without changing business behavior.

The entry point keeps security, body parsing, dispatch order and fallback.
"""
from urllib.parse import parse_qs, unquote
import re


def get_route(handler, parts):
    self = handler
    segments = [unquote(x) for x in parts.path.split('/') if x]
    query = parse_qs(parts.query)
    store = self.server.store
    if parts.path == '/api/collect/platforms':
        return self.reply(200, store.collect.listing())
    if parts.path == '/api/collect/aisa/job':
        return self.reply(200, store.collect.adapter('aisa').job(query.get('id', [''])[0]))
    if parts.path == '/api/collect/status':
        return self.reply(200, store.collect.status())
    if parts.path == '/api/intel/recommendations':
        return self.reply(200, store.intel_recommend.listing())
    if parts.path == '/api/intel/status':
        return self.reply(200, store.intel.status())
    if parts.path == '/api/intel/subscriptions':
        return self.reply(200, {'subscriptions': store.intel.subscriptions()})
    if parts.path == '/api/intel/jobs':
        return self.reply(200, {'jobs': store.intel.jobs(int(query.get('limit', [30])[0]))})
    if parts.path == '/api/intel/articles':
        account = query.get('fakeid', [''])[0] or None
        return self.reply(200, {**store.intel.articles(
            account, int(query.get('limit', [100])[0]), int(query.get('offset', [0])[0])),
            'albums': store.intel.albums(account),
            # 只读：上次「发现合集」的留痕，刷新页面后也能看到。
            'album_search': store.intel.album_search(account)})
    if parts.path == '/api/intel/accounts':
        if set(query) - {'query'} or len(query.get('query', [])) != 1:
            raise ValueError('公众号搜索只接受一个 query 参数')
        return self.reply(200, {'candidates': store.intel.search_accounts(query['query'][0])})
    if len(segments) == 4 and segments[:3] == ['api', 'intel', 'articles']:
        return self.reply(200, store.intel.article(segments[3]))
    if len(segments) == 4 and segments[:3] == ['api', 'intel', 'jobs']:
        return self.reply(200, store.intel.job(segments[3]))
    return None


def post_route(handler, segments, payload):
    self = handler
    if segments == ['api', 'collect', 'aisa', 'run']:
        return self.reply(202, self.server.store.collect.adapter('aisa').start(payload))
    if segments == ['api', 'intel', 'recommendations', 'scan']:
        # 扫描 + 解析合并成一步；解析走普通 HTTP，不碰公众号后台。
        if payload:
            raise ValueError('推荐扫描不接受额外字段')
        counted = self.server.store.intel_recommend.collect()
        resolved = self.server.store.intel_recommend.resolve()
        return self.reply(200, {'scan': counted, 'resolve': resolved,
                                'listing': self.server.store.intel_recommend.listing()})
    if len(segments) == 4 and segments[:3] == ['api', 'intel', 'recommendations']:
        action = segments[3]
        if set(payload) - {'fakeid'}:
            raise ValueError('推荐订阅操作只接受 fakeid')
        if action == 'subscribe':
            match = next((i for i in self.server.store.intel_recommend.listing()['items']
                          if i['fakeid'] == payload.get('fakeid')), None)
            if match is None:
                raise ValueError('这条推荐已不在列表里，请重新扫描')
            return self.reply(201, self.server.store.intel.subscribe({
                'fakeid': match['fakeid'], 'name': match['nickname'] or match['fakeid'],
                'origin': 'recommendation'}))
        if action == 'ignore':
            return self.reply(200, self.server.store.intel_recommend.ignore(payload.get('fakeid')))
        if action == 'unignore':
            return self.reply(200, self.server.store.intel_recommend.unignore(payload.get('fakeid')))
        raise ValueError('未知推荐操作')
    if segments[:2] == ['api', 'intel']:
        from intel_wechat_mp import RateLimited
        try:
            if segments == ['api', 'intel', 'subscriptions']:
                return self.reply(201, self.server.store.intel.subscribe(payload))
            if segments == ['api', 'intel', 'identify']:
                if not isinstance(payload, dict) or set(payload) - {'url'}:
                    raise ValueError('反查公众号只接受一个 url 参数')
                return self.reply(200, self.server.store.intel.identify_from_link(payload.get('url', '')))
            if segments == ['api', 'intel', 'run']:
                return self.reply(202, self.server.store.intel.run(payload))
            if segments == ['api', 'intel', 'unsubscribe']:
                if not isinstance(payload, dict) or set(payload) - {'fakeid'}:
                    raise ValueError('解除订阅只接受 fakeid')
                return self.reply(200, self.server.store.intel.remove_subscription(payload['fakeid']))
            if len(segments) == 5 and segments[:3] == ['api', 'intel', 'articles'] and segments[4] == 'intake':
                if payload:
                    raise ValueError('送入回收站不接受额外字段')
                return self.reply(201, self.server.store.intel.push_to_intake(
                    segments[3], self.server.store.creative_intake))
            return self.reply(404, {'error': '情报采集接口不存在'})
        except RateLimited as exc:
            # 限流不是错误语义：明确回执 + 冷却截止时间，前端据此停手。
            return self.reply(429, {'error': str(exc),
                                    'cooldown_until': exc.until or self.server.store.intel.quota.cooldown_until()})
    return None
