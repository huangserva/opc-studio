"""通用采集模块 · 核心：平台适配器接口与各平台共用的底座。

一个平台适配器要回答五件事：**找订阅源 / 抓最新 / 抓历史 / 抓正文 / 状态**。
底座负责五件所有平台都一样的事，适配器不重复实现：

1. **限流与冷却** —— 按平台独立配额，一个平台被限流不影响别的平台。
2. **去重指纹** —— 同一篇文章跨轮次只入库一次。
3. **任务队列** —— 同一来源已有任务在跑就不再新建，返回现有任务。
4. **回执** —— 每一篇都注明来自**哪个平台、哪一路**。
5. **送入回收站** —— provenance 写成 `collect:<平台>:<来源>:<编号>`，
   同时**认得**一期留下的 `intel:wechat_mp:` 旧前缀（只增不删，老数据照样读）。

设计约束：线索中心那三源（微信消息 / 邮件 / GitHub）**只被读取状态**，
它们的抓取逻辑与定时任务一律不动 —— 真正的抓取仍由各自的既有入口负责。
"""
import hashlib
import json
import threading
import time
from datetime import datetime, timezone

STATUS_WAIT_SECONDS = 0.25
STATUS_CACHE_SECONDS = 30
STATUS_ERROR_RETRY_SECONDS = 15

SCHEMA = 'collect-core/v1'

# ── 平台清单 ───────────────────────────────────────────────────────────────
# state: connected 已接通 / planned 仅占位（不实现抓取）
PLATFORMS = (
    {'id': 'wechat_mp', 'label': '公众号', 'state': 'connected', 'kind': 'feed',
     'via': '合集接口 → 搜狗补最新 → 后台接口（可选）',
     'note': '一期功能，已迁入本模块。抓取走普通 HTTP，不需要后台凭证也能用。'},
    {'id': 'wechat_msg', 'label': '微信消息', 'state': 'connected', 'kind': 'stream',
     'via': '线索中心 · 微信来源', 'note': '由微信雷达既有任务持续送入，这里只看状态。'},
    {'id': 'mail', 'label': '邮件', 'state': 'connected', 'kind': 'stream',
     'via': '线索中心 · 邮箱来源', 'note': '由既有邮件同步送入，这里只看状态。'},
    {'id': 'github', 'label': 'GitHub', 'state': 'connected', 'kind': 'stream',
     'via': '线索中心 · GitHub 来源', 'note': '由既有 GitHub 定时任务送入，这里只看状态。'},
    # AIsa 雷达：适配器已接通（取数委托给 aisa_source），但没配 Key 时状态如实显示未就绪。
    {'id': 'aisa', 'label': '全网情报雷达 (AIsa)', 'state': 'connected', 'kind': 'stream',
     'via': 'AIsa 网关（单 Key）→ 线索中心',
     'note': '在线索中心手动采集；不会定时计费，未配置 AISA_API_KEY 环境变量时不可用。'},
    {'id': 'channels', 'label': '视频号', 'state': 'planned', 'kind': 'feed',
     'via': '', 'note': '链接格式已能识别（collect_links），抓取未接通。'},
    {'id': 'xiaohongshu', 'label': '小红书', 'state': 'planned', 'kind': 'feed',
     'via': '', 'note': '链接格式已能识别（collect_links），抓取未接通。'},
    {'id': 'douyin', 'label': '抖音', 'state': 'planned', 'kind': 'feed',
     'via': '', 'note': '链接格式已能识别（collect_links），抓取未接通。'},
    {'id': 'twitter', 'label': '推特', 'state': 'planned', 'kind': 'feed',
     'via': '', 'note': '规划中，尚未接通。'},
    {'id': 'other', 'label': '其他', 'state': 'planned', 'kind': 'feed',
     'via': '', 'note': '规划中，尚未接通。'},
)
CONNECTED = tuple(p['id'] for p in PLATFORMS if p['state'] == 'connected')
PLANNED = tuple(p['id'] for p in PLATFORMS if p['state'] == 'planned')


# ── provenance ─────────────────────────────────────────────────────────────
LEGACY_PREFIX = 'intel:wechat_mp:'


def legacy_provenance(platform, source, item_id):
    """一期写下的旧格式。只用于保持已有回收项身份稳定，新数据不要再用它。"""
    return f'intel:wechat_mp:{source}:{item_id}'


def provenance(platform, source, item_id):
    """统一来源标记。新数据一律用 collect: 前缀。"""
    platform, source, item_id = str(platform), str(source), str(item_id)
    for value, name in ((platform, '平台'), (source, '来源'), (item_id, '编号')):
        if not value.strip() or ':' in value:
            raise ValueError(f'{name}不能为空且不能含冒号')
    return f'collect:{platform}:{source}:{item_id}'


def _split_once(rest):
    """按第一个冒号切开。fakeid 里的 ``==`` 不是分隔符，必须留在来源里。"""
    if not rest or rest.startswith(':'):
        return None
    if ':' in rest:
        source, item_id = rest.split(':', 1)
    else:
        source, item_id = rest, ''
    if not source:
        return None
    return source, item_id


def parse_provenance(value):
    """认得新旧两种前缀，返回 (platform, source, item_id, legacy)。

    一期的 ``intel:wechat_mp:<fakeid>:<aid>`` 仍能解析。
    fakeid 常以 ``==`` 结尾，只按冒号切，不按等号切。
    """
    if not isinstance(value, str):
        return None
    text = value.strip()
    if text.startswith(LEGACY_PREFIX):
        parsed = _split_once(text[len(LEGACY_PREFIX):])
        if parsed is None:
            return None
        source, item_id = parsed
        return ('wechat_mp', source, item_id, True)
    if not text.startswith('collect:'):
        return None
    parsed = _split_once(text[len('collect:'):])
    if parsed is None:
        return None
    platform, rest = parsed
    if ':' not in rest:
        return (platform, rest, '', False) if rest else None
    source, item_id = rest.split(':', 1)
    if not source:
        return None
    return (platform, source, item_id, False)


# ── 去重指纹 ───────────────────────────────────────────────────────────────
def fingerprint(*parts):
    """跨轮次稳定的去重指纹。空值也要参与，否则空标题会互相撞。"""
    material = '␟'.join('' if p is None else str(p) for p in parts)
    return hashlib.sha256(material.encode('utf-8')).hexdigest()


# ── 按平台独立的配额 ───────────────────────────────────────────────────────
class PlatformQuota:
    """每个平台一份独立配额。一个平台限流不会连累别的平台。"""

    def __init__(self, platform, settings, min_interval=90.0, cooldown_seconds=6 * 3600):
        self.platform = platform
        self.settings = settings
        self.min_interval = min_interval
        self.cooldown_seconds = cooldown_seconds
        self.lock = threading.Lock()

    def key(self, name):
        return f'collect.{self.platform}.{name}'

    def _get(self, name):
        try:
            return float(self.settings.get(self.key(name), '0') or 0)
        except (TypeError, ValueError):
            return 0.0

    def _set(self, name, value):
        self.settings.set(self.key(name), str(value))

    def cooldown_until(self):
        with self.lock:
            until = self._get('cooldown_until')
        return int(until) if until > time.time() else 0

    def record(self, rate_limited):
        """登记一次结果。命中限流就开冷却，**读状态不会清计数**。"""
        with self.lock:
            if not rate_limited:
                self._set('cooldown_until', '0')
                self._set('strikes', '0')
                return 0
            strikes = int(self._get('strikes')) + 1
            if strikes >= 3:
                until = int(time.time()) + self.cooldown_seconds
                self._set('strikes', '0')
                self._set('cooldown_until', str(until))
                return until
            self._set('strikes', str(strikes))
            return 0

    def strikes(self):
        with self.lock:
            return int(self._get('strikes'))

    def state(self):
        return {'cooldown_until': self.cooldown_until(), 'strikes': self.strikes(),
                'cooling': bool(self.cooldown_until())}


class QuotaRegistry:
    """按平台发配额。平台之间完全隔离。"""

    def __init__(self, settings, **kwargs):
        self.settings = settings
        self.kwargs = kwargs
        self._quotas = {}

    def for_platform(self, platform):
        with self._settings_lock:
            if platform not in self._quotas:
                self._quotas[platform] = PlatformQuota(platform, self.settings, **self.kwargs)
            return self._quotas[platform]

    _settings_lock = threading.Lock()

    def states(self):
        return {platform: self.for_platform(platform).state() for platform in CONNECTED}


# ── 任务队列（同源去重）────────────────────────────────────────────────────
class TaskQueue:
    """同一来源已有任务在跑就不新建，返回现有的那个。"""

    def __init__(self):
        self.lock = threading.Lock()
        self._active = {}   # (platform, source) -> task_id

    def submit(self, platform, source, create):
        """create 是一个零参可调用对象：真正建任务。已存在则不调用它。"""
        key = (str(platform), str(source))
        with self.lock:
            existing = self._active.get(key)
            if existing:
                return existing, True
            task_id = create()
            self._active[key] = task_id
            return task_id, False

    def finish(self, platform, source, task_id=None):
        key = (str(platform), str(source))
        with self.lock:
            if task_id is None or self._active.get(key) == task_id:
                self._active.pop(key, None)

    def active_for(self, platform, source):
        with self.lock:
            return self._active.get((str(platform), str(source)))

    def snapshot(self):
        with self.lock:
            return {f'{p}/{s}': task for (p, s), task in self._active.items()}


# ── 回执 ───────────────────────────────────────────────────────────────────
class Receipt:
    """一次采集的回执：每篇都注明来自哪个平台、哪一路。"""

    def __init__(self, platform, source, label=''):
        self.platform = platform
        self.source = source
        self.label = label
        self.counts = {}
        self.notes = []
        self.added = 0

    def note(self, path, count=0):
        if path:
            self.counts[path] = self.counts.get(path, 0) + int(count)
        if self.label and self.label not in self.notes:
            self.notes.append(self.label)

    def say(self, message):
        text = str(message).strip()
        if text and text not in self.notes:
            self.notes.append(text)

    def as_dict(self):
        summary = '；'.join(f'{key} {value} 篇' for key, value in sorted(self.counts.items())) or '没有取到列表'
        detail = f'入库 {self.added} 篇（{summary}）'
        if self.notes:
            detail += '。' + '；'.join(self.notes)
        return {'platform': self.platform, 'source': self.source, 'added': self.added,
                'paths': dict(self.counts), 'detail': detail}


# ── 适配器接口 ─────────────────────────────────────────────────────────────
class Adapter:
    """平台适配器。子类实现五件事；底座方法都有安全默认值。"""

    platform = ''
    label = ''
    state = 'planned'
    home = ''

    def sources(self):
        """找账号/订阅源。"""
        raise NotImplementedError

    def latest(self, source):
        """抓最新。"""
        raise NotImplementedError

    def history(self, source):
        """抓历史。"""
        raise NotImplementedError

    def body(self, source, item):
        """抓正文。"""
        raise NotImplementedError

    def status(self):
        """状态。默认可用；子类按需覆盖。"""
        return {'available': True, 'detail': ''}


class PlannedAdapter(Adapter):
    """规划中的平台占位：不实现抓取，任何动作都明确拒绝。"""

    state = 'planned'

    def __init__(self, platform, label, note=''):
        self.platform, self.label, self.note_text = platform, label, note

    def sources(self):
        return []

    def latest(self, source):
        raise ValueError(f'{self.label}尚未接通，暂时不能抓取')

    def history(self, source):
        return self.latest(source)

    def body(self, source, item):
        return self.latest(source)

    def status(self):
        return {'available': False, 'detail': self.note_text or f'{self.label}规划中，尚未接通'}


# ── 注册表 ─────────────────────────────────────────────────────────────────
class Registry:
    """统一采集入口：平台清单 + 适配器 + 回执汇总。"""

    def __init__(self, store, adapters=None):
        self.store = store
        self.adapters = dict(adapters or {})
        self.quota = QuotaRegistry(_SettingsAdapter(store))
        self.queue = TaskQueue()
        self._status_lock = threading.Lock()
        self._status_cache = {}
        self._status_workers = {}
        self._status_errors = {}

    def register(self, adapter):
        self.adapters[adapter.platform] = adapter
        return adapter

    def adapter(self, platform):
        found = self.adapters.get(platform)
        if found is None:
            raise ValueError(f'未知平台：{platform}')
        return found

    def connected(self):
        return [p for p in PLATFORMS if p['state'] == 'connected']

    def planned(self):
        return [p for p in PLATFORMS if p['state'] == 'planned']

    def listing(self):
        """按平台分栏的清单：已接通 / 规划中。"""
        sections = []
        for group, name in ((self.connected(), '已接通'), (self.planned(), '规划中')):
            items = []
            for meta in group:
                adapter = self.adapters.get(meta['id'])
                record = {'id': meta['id'], 'label': meta['label'], 'state': meta['state'],
                          'kind': meta['kind'], 'via': meta['via'], 'note': meta['note'],
                          'home': getattr(adapter, 'home', '') if adapter is not None else ''}
                if adapter is not None:
                    record['adapter'] = adapter.__class__.__name__
                items.append(record)
            sections.append({'name': name, 'platforms': items})
        return {'schema': SCHEMA, 'sections': sections,
                'active_tasks': self.queue.snapshot()}

    def _status_snapshot(self, key, read, deadline):
        """单飞刷新；慢操作不持锁，超时后保留上次完整结果。"""
        with self._status_lock:
            cached = self._status_cache.get(key)
            running = self._status_workers.get(key)
            failure = self._status_errors.get(key)
            due = (time.monotonic() - failure[0] >= STATUS_ERROR_RETRY_SECONDS if failure else
                   cached is None or time.monotonic() - cached[0] >= STATUS_CACHE_SECONDS)
            if running is None and due:
                done = threading.Event()
                def refresh():
                    error = None
                    try:
                        result = read()
                    except Exception as exc:
                        error = f'读取状态失败：{type(exc).__name__}'
                    with self._status_lock:
                        if error:
                            self._status_errors[key] = (time.monotonic(), error)
                        else:
                            self._status_cache[key] = (time.monotonic(), datetime.now(timezone.utc).isoformat(), result)
                            self._status_errors.pop(key, None)
                        self._status_workers.pop(key, None)
                    done.set()
                running = done
                self._status_workers[key] = done
                try:
                    threading.Thread(target=refresh, name='collect-status', daemon=True).start()
                except Exception:
                    self._status_workers.pop(key, None)
                    done.set()
        if running is not None:
            running.wait(max(0, deadline - time.monotonic()))
        with self._status_lock:
            cached = self._status_cache.get(key)
            pending = key in self._status_workers
            failure = self._status_errors.get(key)
        result = cached[2] if cached else None
        detail = ''
        if failure:
            detail = failure[1] + ('（显示上次结果）' if cached else '')
            if key != 'quota':
                result = {**(result or {'available': False}), 'detail': detail}
        return result, {
            'checked_at': cached[1] if cached else None,
            'stale': bool(failure) or cached is None or time.monotonic() - cached[0] >= STATUS_CACHE_SECONDS,
            'failed': bool(failure), **({'detail': detail} if key == 'quota' and detail else {}),
            'refreshing': pending, 'timed_out': pending and time.monotonic() >= deadline}

    def status(self):
        """并行刷新，整次读取共用等待预算，挂住的适配器不阻塞页面。"""
        deadline = time.monotonic() + STATUS_WAIT_SECONDS
        # 先发起全部刷新，再等待，不能按平台累加等待预算。
        reads = {meta['id']: (lambda adapter=adapter: adapter.status() or {})
                 for meta in PLATFORMS if (adapter := self.adapters.get(meta['id'])) is not None}
        for platform, read in reads.items():
            self._status_snapshot((platform, id(self.adapters[platform])), read, time.monotonic())
        self._status_snapshot('quota', self.quota.states, time.monotonic())
        rows = []
        for meta in PLATFORMS:
            adapter = self.adapters.get(meta['id'])
            if adapter is None:
                rows.append({'id': meta['id'], 'label': meta['label'], 'state': meta['state'],
                             'available': False, 'detail': meta['note'] or '尚未接通'})
                continue
            result, metadata = self._status_snapshot((meta['id'], id(adapter)), reads[meta['id']], deadline)
            if result is not None:
                rows.append({'id': meta['id'], 'label': meta['label'], 'state': meta['state'],
                             'available': result.get('available') is not False,
                             'detail': str(result.get('detail') or '')[:200],
                             'count': result.get('count'), 'extra': result.get('extra') or {}, **metadata})
            else:
                rows.append({'id': meta['id'], 'label': meta['label'], 'state': meta['state'],
                             'available': None, 'count': None, 'extra': {},
                             'detail': '状态核对超时，尚无已核实结果', **metadata})
        quota, quota_status = self._status_snapshot('quota', self.quota.states, deadline)
        return {'schema': SCHEMA, 'platforms': rows, 'quota': quota,
                'quota_status': quota_status,
                'active_tasks': self.queue.snapshot()}


class _SettingsAdapter:
    """把 Registry 的按平台配额接到 store 的键值表上（只增不删）。"""

    def __init__(self, store):
        self.store = store

    def _collection(self):
        intel = getattr(self.store, 'intel', None)
        return intel

    def get(self, key, default=None):
        intel = self._collection()
        if intel is None:
            return default
        return intel.settings().get(key, default)

    def set(self, key, value):
        intel = self._collection()
        if intel is not None:
            intel.settings().set(key, value)


def settings_of(store):
    """复用 intel.db 的键值表，让按平台配额与一期状态存在同一个库里。"""
    return _SettingsAdapter(store)


def load_json(value, default=None):
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, type(default)) else default
    except (TypeError, ValueError):
        return default
