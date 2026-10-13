"""通用采集模块 · 平台适配器。

- `WechatMpAdapter`：把一期已有的 intel_* 整套包进来，行为不变。
- `ClueSourceAdapter`：线索中心三源（微信消息 / 邮件 / GitHub）只读状态。
  它们的抓取逻辑与定时任务一律不动 —— 真正的抓取仍由各自的既有入口负责，
  这里只是让统一采集页能看见同一份状态。

状态直接读线索中心、知识库、Gmail 缓存和 GitHub 自己的存储，不另存一份副本。
读状态不调用它们的抓取入口，也不新建定时任务。
"""
import json
import os
import fcntl
import sqlite3
import threading
import re
from datetime import datetime, timezone
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
    """全网情报雷达（AIsa）。手动触发由独立模块取数，幂等回执/工作区文件锁保护计费。"""

    platform = 'aisa'
    label = '全网情报雷达 (AIsa)'
    state = 'connected'
    home = '/clues.html'

    def __init__(self, source):
        self.source = source

    def _job_db(self):
        store = self.source.store
        if store is None or not getattr(store, 'workspace', None):
            raise ValueError('AIsa 采集未配置工作区')
        root = Path(store.workspace) / 'aisa'
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        root.chmod(0o700)
        path = root / 'manual-jobs.db'
        db = sqlite3.connect(path, timeout=3)
        db.row_factory = sqlite3.Row
        db.execute('CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY, config TEXT NOT NULL, data TEXT NOT NULL)')
        db.execute("CREATE INDEX IF NOT EXISTS jobs_state ON jobs(json_extract(data,'$.status'))")
        db.commit()
        path.chmod(0o600)
        return db

    @staticmethod
    def _config(payload):
        if not isinstance(payload, dict) or set(payload) - {'platform', 'handles', 'limit', 'request_id'}:
            raise ValueError('AIsa 采集参数无效')
        platform = payload.get('platform')
        handles = payload.get('handles', [])
        limit = payload.get('limit', 20)
        identity = payload.get('request_id')
        if platform not in ('twitter', 'douyin', 'xiaohongshu'):
            raise ValueError('请选择推特、抖音或小红书')
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError('条数须为 1–100 的整数')
        if not isinstance(identity, str) or not re.fullmatch(r'[a-f0-9]{32}', identity):
            raise ValueError('本次采集标识无效，请刷新页面')
        if not isinstance(handles, list) or len(handles) > 5 or any(not isinstance(x, str) for x in handles):
            raise ValueError('一次最多采集 5 个推特账号')
        handles = [x.strip().lstrip('@').lower() for x in handles]
        if any(not re.fullmatch(r'[A-Za-z0-9_]{1,15}', x) for x in handles):
            raise ValueError('推特账号只能使用字母、数字或下划线，最多15字')
        handles = list(dict.fromkeys(handles))
        if platform == 'twitter' and not handles:
            raise ValueError('请填写至少一个推特账号')
        if platform != 'twitter' and handles:
            raise ValueError('此平台不接受推特账号参数')
        return identity, {'platform': platform, 'handles': handles, 'limit': limit}

    def job(self, identity=''):
        if identity and (not isinstance(identity, str) or not re.fullmatch(r'[a-f0-9]{32}', identity)):
            raise ValueError('采集任务编号无效')
        from contextlib import closing
        with closing(self._job_db()) as db:
            row = db.execute('SELECT data FROM jobs WHERE id=?', (identity,)).fetchone() if identity else db.execute('SELECT data FROM jobs ORDER BY rowid DESC LIMIT 1').fetchone()
        if row is None:
            if identity:
                raise ValueError('采集任务不存在')
            return {'status': 'idle',
                    'available': bool(self.source.api_key) and not self.source.config_error}
        job = self.source._scrub(json.loads(row['data']))
        if job.get('status') in ('queued', 'running'):
            lockfile = Path(self.source.store.workspace) / 'aisa' / 'manual.lock'
            fd = os.open(lockfile, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
            try:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    return job
                # 重新读取，防止旧running快照读完后任务刚刚成功结束。
                with closing(self._job_db()) as db:
                    current = db.execute('SELECT data FROM jobs WHERE id=?', (job['job_id'],)).fetchone()
                job = self.source._scrub(json.loads(current['data']))
                if job['status'] in ('queued', 'running'):
                    job.update(status='interrupted', errors=['上次采集已中断，不自动重试'], finished_at=datetime.now(timezone.utc).isoformat())
                    self._write_job(job)
            finally:
                os.close(fd)
        return job

    def _write_job(self, job):
        from contextlib import closing
        with closing(self._job_db()) as db:
            db.execute('UPDATE jobs SET data=? WHERE id=?',
                       (json.dumps(self.source._scrub(job), ensure_ascii=False), job['job_id']))
            db.commit()

    def start(self, payload):
        identity, config = self._config(payload)
        material = json.dumps(config, sort_keys=True)
        if self.source.api_key and self.source.api_key in material:
            raise ValueError('采集参数不得包含凭证')
        from contextlib import closing
        with closing(self._job_db()) as db:
            row = db.execute('SELECT config,data FROM jobs WHERE id=?', (identity,)).fetchone()
        if row:
            if row['config'] != material:
                raise ValueError('同一次采集标识不能更换参数')
            return {**self.job(identity), 'reused': True}
        if self.source.config_error:
            # 超长凭据（复核 M2）：在启动采集、发第一个请求之前就报清楚的中文配置错误。
            raise ValueError(self.source.config_error)
        if not self.source.api_key:
            raise ValueError('AIsa 尚未配置，请先安全配置环境变量')
        lockfile = Path(self.source.store.workspace) / 'aisa' / 'manual.lock'
        fd = os.open(lockfile, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(fd)
            active = self.job()
            if active.get('status') in ('queued', 'running'):
                return {**active, 'reused': True}
            raise ValueError('已有 AIsa 采集正在启动，请稍后查看') from None
        handed_off = False
        try:
            with closing(self._job_db()) as db:
                db.execute('BEGIN IMMEDIATE')
                row = db.execute('SELECT config,data FROM jobs WHERE id=?', (identity,)).fetchone()
                if row:
                    if row['config'] != material:
                        raise ValueError('同一次采集标识不能更换参数')
                    return {**self.source._scrub(json.loads(row['data'])), 'reused': True}
                # 只有获得同一工作区文件锁后，才确认上次不存在执行者；不按年龄重发。
                for old in db.execute("SELECT id,data FROM jobs WHERE json_extract(data,'$.status') IN ('queued','running')").fetchall():
                    data = json.loads(old['data'])
                    if data.get('status') in ('queued', 'running'):
                        data.update(status='interrupted', errors=['上次采集已中断，不自动重试'], finished_at=datetime.now(timezone.utc).isoformat())
                        db.execute('UPDATE jobs SET data=? WHERE id=?', (json.dumps(self.source._scrub(data), ensure_ascii=False), old['id']))
                job = {'job_id': identity, **config, 'status': 'queued', 'completed': 0,
                       'total': len(config['handles']) if config['platform'] == 'twitter' else 1,
                       'fetched': 0, 'added': 0, 'duplicates': 0, 'updated': 0, 'failed': 0,
                       'errors': [], 'created_at': datetime.now(timezone.utc).isoformat(),
                       'finished_at': '', 'reused': False}
                db.execute('INSERT INTO jobs VALUES(?,?,?)', (identity, material, json.dumps(self.source._scrub(job), ensure_ascii=False)))
                db.commit()
            try:
                worker = threading.Thread(target=self._run_job, args=(job, fd), name='aisa-manual', daemon=True)
                worker.start()
                handed_off = True
            except Exception:
                job.update(status='error', errors=['采集任务未能启动，请稍后重新发起'])
                self._write_job(job)
                raise ValueError('采集任务未能启动，请稍后重新发起') from None
            return {**self.source._scrub(job), 'reused': False}
        finally:
            if not handed_off:
                os.close(fd)

    def _run_job(self, job, fd):
        try:
            job['status'] = 'running'
            self._write_job(job)
            sources = job['handles'] if job['platform'] == 'twitter' else ['']
            for handle in sources:
                if job['platform'] == 'twitter':
                    items = self.source.get_twitter_tweets(handle, job['limit'])
                elif job['platform'] == 'douyin':
                    items = self.source.fetch_douyin_hot_searches(job['limit'])
                else:
                    items = self.source.fetch_xiaohongshu_inspirations(job['limit'])
                if self.source.last_error and not items:
                    job['failed'] += 1
                    message = self.source.last_error
                    if '404' in message:
                        message = '采集接口暂不可用（404），请稍后核对'
                    elif 'URLError' in message or 'Timeout' in message:
                        message = '采集网络连接失败或超时，请稍后再试'
                    job['errors'].append(message[:200])
                job['fetched'] += len(items)
                for item in items:
                    try:
                        result = self.source.store.clues.ingest_aisa(self.source, item)
                        job['duplicates' if result['duplicate'] else 'added'] += 1
                        job['updated'] += bool(result.get('updated'))
                    except Exception:
                        job['failed'] += 1
                        job['errors'].append('一条采集结果保存失败，已保留此前成功结果')
                job['completed'] += 1
                self._write_job(job)
                if '429' in self.source.last_error:
                    break  # 限流不重试，也不继续下一个账号。
            job['status'] = 'partial' if job['failed'] and (job['added'] + job['duplicates']) else 'error' if job['failed'] else 'done'
        except Exception:
            job['status'] = 'partial' if job['added'] + job['duplicates'] else 'error'
            job['errors'].append('采集任务异常结束，已保留此前成功结果')
        finally:
            job['finished_at'] = datetime.now(timezone.utc).isoformat()
            try:
                self._write_job(job)
            finally:
                os.close(fd)

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
        # 配置错误（例如 Key 超长）时 available=False，detail 直接把中文原因显示到页面上。
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
