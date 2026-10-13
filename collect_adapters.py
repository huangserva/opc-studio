"""通用采集模块 · 平台适配器。

- `WechatMpAdapter`：把一期已有的 intel_* 整套包进来，行为不变。
- `ClueSourceAdapter`：线索中心三源（微信消息 / 邮件 / GitHub）只读状态。
  它们的抓取逻辑与定时任务一律不动 —— 真正的抓取仍由各自的既有入口负责，
  这里只是让统一采集页能看见同一份状态。

状态直接读线索中心、知识库、Gmail 缓存和 GitHub 自己的存储，不另存一份副本。
读状态不调用它们的抓取入口，也不新建定时任务。
"""
import json
from pathlib import Path

from collect_core import Adapter, PlannedAdapter, provenance
# 复用知识库唯一的「退出正常检索」口径：手动移到「已删除」的正文照旧可读，
# 但来源计数与「最新」不数它。这里不另写一份条件，免得两边口径漂移。
from knowledge_store import NOT_TRASHED

# 线索中心 / 知识库里已有的来源 id，我们只是把它们映射到平台名。
CLUE_SOURCES = {
    'wechat_msg': {'label': '微信消息', 'kinds': ('wechat', 'wechat_links'), 'home': '/clues.html',
                   'task': 'wechat_clues', 'via': '线索中心 · 微信来源'},
    'mail': {'label': '邮件', 'kinds': ('mail', 'gmail_cache'), 'home': '/knowledge.html',
             'task': None, 'via': '线索中心 · 邮箱来源'},
    'github': {'label': 'GitHub', 'kinds': ('github',), 'home': '/github.html',
               'task': 'github_clues', 'via': '线索中心 · GitHub 来源'},
}


def schedule_tasks(store, kind):
    """定时任务页上同一种类的任务。没有调度器就返回 None，不为此新建任务。"""
    if not kind:
        return None
    schedules = getattr(store, 'schedules', None)
    if schedules is None:
        return None
    try:
        tasks = schedules.listing().get('tasks') or []
    except Exception:  # noqa: BLE001 - 调度器读失败不能拖垮采集页
        return None
    return [task for task in tasks if task.get('kind') == kind]


def public_text(value):
    return value if isinstance(value, str) else ''


def public_count(value):
    return value if type(value) is int and value >= 0 else 0


def public_flag(value):
    return value if type(value) is bool else False


def public_source(row):
    """Allow only typed identity fields, never nested upstream material."""
    if not isinstance(row, dict):
        return None
    item = {key: public_text(row[key]) for key in ('id','name','kind','status') if key in row}
    if 'count' in row:
        item['count'] = public_count(row['count'])
    for key in ('repository','account'):
        if isinstance(row.get(key), str) and row[key]:
            item[key] = row[key]
    return item


def public_sources(rows):
    if not isinstance(rows, (list, tuple)):
        return []
    return [item for item in (public_source(row) for row in rows) if item is not None]


def public_tasks(tasks):
    if tasks is None:
        return None
    if not isinstance(tasks, (list, tuple)):
        return []
    return [{'id': public_text(task.get('id')), 'kind': public_text(task.get('kind')),
             'enabled': public_flag(task.get('enabled')), 'running': public_flag(task.get('running'))}
            for task in tasks if isinstance(task, dict)]


def public_gmail(row):
    if not isinstance(row, dict):
        return None
    return {'account': public_text(row.get('account')), 'count': public_count(row.get('count')),
            'query': public_text(row.get('query')), 'has_more': public_flag(row.get('has_more')),
            'captured_at': public_text(row.get('captured_at'))}


def clue_totals(store, source_ids):
    """线索中心按来源筛选时的条数。用的就是线索列表自己的统计。"""
    totals = []
    for source_id in source_ids:
        if not source_id:
            continue
        try:
            total = store.clues.listing(source=source_id, limit=1)['total']
        except Exception:  # noqa: BLE001 - 单条来源统计失败留空，不编造 0
            total = None
        totals.append({'id': source_id, 'total': total})
    return totals


def knowledge_sources(store, kinds):
    """知识库来源行。不走 knowledge.status()，免得读邮件时顺手去问微信雷达。"""
    wanted = set(kinds)
    knowledge = store.knowledge
    with knowledge.db() as connection:
        rows = connection.execute(
            '''SELECT s.config AS config, s.status AS status, s.detail AS detail,
                      s.synced_at AS synced_at, COUNT(d.id) AS count, MAX(d.modified) AS latest
               FROM sources s LEFT JOIN documents d ON d.source = s.id AND ''' + NOT_TRASHED + '''
               GROUP BY s.id ORDER BY s.rowid'''
        ).fetchall()
    found = []
    for row in rows:
        config = json.loads(row['config'])
        if config.get('kind') not in wanted:
            continue
        found.append({**config, 'status': row['status'], 'detail': row['detail'],
                      'synced_at': row['synced_at'], 'count': row['count'], 'latest': row['latest']})
    return found


class WechatMpAdapter(Adapter):
    """公众号。委托给一期已有的 Intel，行为与一期完全一致。"""

    platform = 'wechat_mp'
    label = '公众号'
    state = 'connected'
    home = '/intel.html'

    def __init__(self, intel):
        self.intel = intel

    def sources(self):
        return self.intel.subscriptions()

    def latest(self, source):
        return self.intel.run({'fakeid': source, 'mode': 'latest', 'sogou': True, 'backend': False})

    def history(self, source):
        return self.intel.run({'fakeid': source, 'mode': 'history', 'sogou': True, 'backend': False})

    def body(self, source, item):
        parsed = self.intel.client().fetch_article(item['link'])
        return self.intel.save_body(item['id'], parsed)

    def status(self):
        record = self.intel.status()
        return {'available': record.get('credentials') != 'missing',
                'count': public_count(record.get('subscriptions')), 'detail': public_text(record.get('detail')),
                'extra': {'articles': public_count(record.get('articles')),
                          'awaiting': public_count(record.get('awaiting')),
                          'cooldown_until': public_count(record.get('cooldown_until')),
                          'rate_strikes': public_count(record.get('rate_strikes')),
                          'busy': public_flag(record.get('busy')),
                          'tasks': public_tasks(schedule_tasks(self.intel.store, 'intel_wechat_mp')),
                          'home': self.home}}

    def provenance(self, source, item_id):
        return provenance(self.platform, source, item_id)

    def push_to_intake(self, item_id, intake):
        return self.intel.push_to_intake(item_id, intake)


class ClueSourceAdapter(Adapter):
    """线索中心三源之一。只读状态，不碰它们的抓取与定时任务。"""

    state = 'connected'

    def __init__(self, platform, store):
        self.platform = platform
        self.store = store
        self.meta = CLUE_SOURCES[platform]
        self.label = self.meta['label']
        self.home = self.meta['home']

    def sources(self):
        """订阅源 = 该来源在知识库里配置的资料源。"""
        return knowledge_sources(self.store, self.meta['kinds'])

    def latest(self, source):
        return self._delegate('抓最新')

    def history(self, source):
        return self._delegate('抓历史')

    def body(self, source, item):
        return self._delegate('抓正文')

    def status(self):
        try:
            if self.platform == 'wechat_msg':
                return self._wechat()
            if self.platform == 'github':
                return self._github()
            return self._mail()
        except Exception as exc:  # noqa: BLE001 - 状态读取失败要如实说，不编造
            return {'available': False, 'detail': f'读取状态失败：{type(exc).__name__}'}

    def _delegate(self, action):
        return {'platform': self.platform, 'action': action, 'delegated': True,
                'detail': f'{self.label}由既有入口负责采集，这里不代跑；请到 {self.home}',
                'home': self.home}

    def _wechat(self):
        client = self.store.wechat_source
        raw = client.clue_status() or {}
        live = client.status() or {}
        busy = bool(live.get('busy')) or bool(raw.get('running'))
        sources = public_sources(self.sources())
        checked = public_count(raw.get('processed_versions'))
        value = raw.get('pending_versions')
        pending = value if type(value) is int and value >= 0 else None
        if busy:
            detail = '微信线索采集进行中'
        elif raw.get('has_pending'):
            detail = f'微信仍有未分析消息，已检查 {checked} 个版本，待分析 {pending if pending is not None else "未知"} 个'
        else:
            detail = f'微信线索采集空闲，已检查 {checked} 个版本'
        checked_count = checked
        pending_count = pending
        return {'available': True, 'count': checked_count, 'detail': detail,
                'extra': {'busy': busy,
                          'processed_versions': checked_count,
                          'pending_versions': pending_count,
                          'has_pending': bool(raw.get('has_pending')),
                          'sources': public_sources(sources),
                          'clues': clue_totals(self.store, [item.get('id') for item in sources]),
                          'tasks': public_tasks(schedule_tasks(self.store, self.meta['task'])),
                          'home': self.home}}

    def _github(self):
        raw = self.store.github.status()
        connection = raw.get('connection') if isinstance(raw.get('connection'), dict) else {}
        sources = public_sources(raw.get('sources'))
        ready = bool(sources) and all(item.get('status') == 'ready' for item in sources)
        owner = public_text(connection.get('owner'))
        if owner and ready:
            detail = f'账号 {owner} · GitHub 来源已接通'
        elif owner:
            detail = f'账号 {owner} · 已核对账号'
        else:
            detail = '尚未连接 GitHub'
        retries = raw.get('retry_documents')
        return {'available': bool(owner or sources), 'count': len(sources), 'detail': detail[:200],
                'extra': {'busy': bool(raw.get('busy')), 'owner': owner,
                          'auth': connection.get('auth') if isinstance(connection.get('auth'), str) else '',
                          'retry_documents': public_count(retries),
                          'sources': public_sources(sources),
                          'clues': clue_totals(self.store, [item.get('id') for item in sources]),
                          'tasks': public_tasks(schedule_tasks(self.store, self.meta['task'])),
                          'home': self.home}}

    def _mail(self):
        sources = self.sources()
        caches = []
        database = Path(self.store.workspace) / 'mail' / 'gmail.db'
        if database.is_file():
            from gmail_source import GmailCache
            cache = GmailCache(self.store.workspace)
            for source in sources:
                if source.get('kind') == 'gmail_cache' and source.get('account'):
                    caches.append(cache.status(source['account']))
        total = sum(public_count(source.get('count')) for source in sources)
        latest = max((public_text(source.get('latest')) for source in sources), default='')
        if not sources:
            detail = '还没有配置邮件来源'
            available = False
        else:
            available = True
            detail = f'{len(sources)} 个邮箱来源，最近同步 {latest[:16] or "尚无"}'
        return {'available': available, 'count': total, 'detail': detail,
                'extra': {'syncing': bool(self.store.knowledge.sync_lock.locked()),
                          'sources': public_sources(sources),
                          'gmail': [item for item in (public_gmail(row) for row in caches) if item],
                          'clues': clue_totals(self.store, [item.get('id') for item in sources]),
                          'tasks': public_tasks(schedule_tasks(self.store, self.meta['task'])),
                          'home': self.home}}


class AIsaRadarAdapter(Adapter):
    """全网情报雷达（AIsa）。取数委托给独立的 aisa_source 模块，这里只看状态。"""

    platform = 'aisa'
    label = '全网情报雷达 (AIsa)'
    state = 'connected'
    home = '/clues.html'

    def __init__(self, source):
        self.source = source

    def sources(self):
        """AIsa 是单 Key 网关，没有可枚举的订阅源。"""
        return []

    def latest(self, source):
        return self._delegate('抓最新')

    def history(self, source):
        return self._delegate('抓历史')

    def body(self, source, item):
        return self._delegate('抓正文')

    def _delegate(self, action):
        return {'platform': self.platform, 'action': action, 'delegated': True,
                'detail': f'{self.label}由 AIsa 网关采集，这里不代跑；请到 {self.home}',
                'home': self.home}

    def status(self):
        try:
            record = self.source.status()
        except Exception as exc:  # noqa: BLE001 - 状态读取失败要如实说，不编造
            return {'available': False, 'detail': f'读取状态失败：{type(exc).__name__}'}
        return {'available': bool(record.get('available')),
                'count': public_count(record.get('count')),
                'detail': public_text(record.get('detail'))[:200],
                'extra': {'last_synced': public_text(record.get('last_synced')),
                          'home': self.home}}


def build_adapters(store):
    """装好全部适配器：公众号一个真正的，其余按平台分派。"""
    from collect_core import PLATFORMS
    adapters = {'wechat_mp': WechatMpAdapter(store.intel)}
    for platform in CLUE_SOURCES:
        adapters[platform] = ClueSourceAdapter(platform, store)
    try:
        from aisa_source import AIsaSource
        adapters['aisa'] = AIsaRadarAdapter(AIsaSource(store=store))
    except Exception:  # noqa: BLE001 - 模块暂不可用不能影响其他已有适配器
        pass
    for meta in PLATFORMS:
        adapters.setdefault(meta['id'], PlannedAdapter(meta['id'], meta['label'], meta['note']))
    return adapters
