"""情报采集 · 存储：订阅号、抓取任务、文章（fakeid + 链接双去重）。

独立库 workspace/intel/intel.db，不碰 intake* / knowledge_store / publication_*。
正文入库一律标为「待确认」，默认不自动进知识库；只有用户点「送入回收站」
才写 provenance=intel:wechat_mp:<fakeid>:<aid> 调既有回收站接口。
"""
import hashlib
import json
import logging
import re
import sqlite3
import threading
import time
import uuid
from urllib.parse import urlsplit, parse_qs
from contextlib import contextmanager, closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

from intel_wechat_mp import (MAX_BODIES_PER_RUN, MAX_PAGES_PER_RUN, HISTORY_PAGE_SIZE,
                             LATEST_PAGE_SIZE, BackendQuota, CredentialExpired, CredentialsMissing,
                             RateLimited, article_url, fakeid)
from intel_public import (ALBUM_REQUEST_BUDGET, AlbumError, SOGOU_COOLDOWN_SECONDS,
                          SOGOU_REQUEST_BUDGET, SogouBlocked, discover_album_ids)

SCHEMA = '''
CREATE TABLE IF NOT EXISTS subscriptions(
    fakeid TEXT PRIMARY KEY, name TEXT NOT NULL DEFAULT '', note TEXT NOT NULL DEFAULT '',
    origin TEXT NOT NULL DEFAULT '', added_at TEXT NOT NULL, last_run_at TEXT NOT NULL DEFAULT '',
    last_status TEXT NOT NULL DEFAULT '', UNIQUE(fakeid));
CREATE TABLE IF NOT EXISTS jobs(
    id TEXT PRIMARY KEY, fakeid TEXT NOT NULL, name TEXT NOT NULL DEFAULT '',
    mode TEXT NOT NULL, status TEXT NOT NULL, reason TEXT NOT NULL DEFAULT '',
    detail TEXT NOT NULL DEFAULT '', routes TEXT NOT NULL DEFAULT '', found INTEGER NOT NULL DEFAULT 0,
    added INTEGER NOT NULL DEFAULT 0, bodies INTEGER NOT NULL DEFAULT 0,
    pages INTEGER NOT NULL DEFAULT 0, started_at TEXT NOT NULL, finished_at TEXT NOT NULL DEFAULT '',
    UNIQUE(id));
CREATE INDEX IF NOT EXISTS jobs_fakeid ON jobs(fakeid, started_at DESC);
CREATE TABLE IF NOT EXISTS articles(
    id TEXT PRIMARY KEY, fakeid TEXT NOT NULL, aid TEXT NOT NULL, link TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL DEFAULT '', title TEXT NOT NULL DEFAULT '', published_at INTEGER NOT NULL DEFAULT 0,
    body_status TEXT NOT NULL DEFAULT 'pending', body TEXT NOT NULL DEFAULT '',
    chars INTEGER NOT NULL DEFAULT 0, images TEXT NOT NULL DEFAULT '[]', error TEXT NOT NULL DEFAULT '',
    job_id TEXT NOT NULL DEFAULT '', drop_id TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT '', link_ok INTEGER NOT NULL DEFAULT 1,
    added_at TEXT NOT NULL, body_at TEXT NOT NULL DEFAULT '',
    UNIQUE(fakeid, aid));
CREATE INDEX IF NOT EXISTS articles_fakeid ON articles(fakeid, published_at DESC);
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS link_index(
    link TEXT PRIMARY KEY, fakeid TEXT NOT NULL DEFAULT '', nickname TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending', detail TEXT NOT NULL DEFAULT '',
    source_kind TEXT NOT NULL DEFAULT '', resolved_at TEXT NOT NULL DEFAULT '');
CREATE INDEX IF NOT EXISTS link_index_fakeid ON link_index(fakeid);
CREATE TABLE IF NOT EXISTS recommendations(
    fakeid TEXT PRIMARY KEY, nickname TEXT NOT NULL DEFAULT '', count INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'new', first_seen TEXT NOT NULL, last_seen TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS albums(
    fakeid TEXT NOT NULL, album_id TEXT NOT NULL, title TEXT NOT NULL DEFAULT '',
    article_count INTEGER NOT NULL DEFAULT 0, found_from TEXT NOT NULL DEFAULT '',
    found_at TEXT NOT NULL, UNIQUE(fakeid, album_id));
CREATE INDEX IF NOT EXISTS albums_fakeid ON albums(fakeid);
CREATE TABLE IF NOT EXISTS recommendation_sources(
    fakeid TEXT NOT NULL, source_kind TEXT NOT NULL, source_id TEXT NOT NULL, link TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT '', seen_at TEXT NOT NULL,
    PRIMARY KEY(source_kind, source_id, link));
CREATE INDEX IF NOT EXISTS recommendation_sources_fakeid ON recommendation_sources(fakeid);
CREATE TABLE IF NOT EXISTS album_searches(
    id TEXT PRIMARY KEY, fakeid TEXT NOT NULL DEFAULT '', kind TEXT NOT NULL DEFAULT '',
    albums_found INTEGER NOT NULL DEFAULT 0, has_chksm INTEGER NOT NULL DEFAULT 0,
    detail TEXT NOT NULL DEFAULT '', searched_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS album_searches_fakeid ON album_searches(fakeid, searched_at DESC);
'''

# 推荐订阅（二期A）：每轮最多解析多少条未解析链接。
MAX_RESOLVE_PER_ROUND = 20

NAME_LIMIT = 100
NOTE_LIMIT = 200
# 超过这个时长还挂着 running/queued 的任务按「重启残留」处理。
# 定时任务的 timeout 上限是 1800 秒，给足余量，正常在跑的任务不会被误杀。
STALE_JOB_SECONDS = 3600
ABANDONED_JOB_SECONDS = 60
START_FAILURE_RETRY_SECONDS = 0.05
_PROCESS_ABANDONED = set()
_LOG = logging.getLogger(__name__)
_PROCESS_WORKERS = {}
_PROCESS_WORKERS_LOCK = threading.RLock()
STARTUP_STALE_REASON = '工作台重启中断了这次抓取；点一次抓取即可重来'
LIVE_STALE_REASON = '抓取超过时限仍未结束，已中断；点一次抓取即可重来'
ALBUM_SEARCH_FAILED = '文章页读取失败'

# 三条路线与任务状态的中文文案。页面直接用它，不再自己拼。
PATH_LABELS = {'album': '合集', 'sogou': '搜狗', 'backend': '后台'}
PATH_ORDER = ('album', 'sogou', 'backend')
MODE_LABELS = {'latest': '抓最新', 'history': '抓历史', 'scheduled': '定时（每日抓最新）'}
STATUS_LABELS = {'running': '进行中', 'queued': '排队中', 'done': '已完成', 'partial': '部分完成',
                 'rate_limited': '限流冷却', 'credentials': '需重新登录后台', 'interrupted': '已中断',
                 'error': '失败', 'cooldown': '冷却中，本轮跳过', 'busy': '已有任务在跑'}

# 主文案给人看，错误码只放在括号里对照。命中第一条即用。
_HUMAN_REASONS = (
    ('200013', '公众号后台接口被限流（错误码 200013），这一轮已停止、没有重试'),
    ('接口限流冷却中', '后台接口还在冷却期，这一轮没有发任何请求'),
    ('需重新登录', '公众号后台的登录已失效，要重新登录并更新凭证'),
    ('钥匙串里没有', '这台电脑上还没有存公众号后台的凭证，要先在后台登录一次'),
    ('验证码', '搜狗的跳转被验证码拦住，这一路只拿得到标题和时间'),
    ('线程没有启动', '抓取线程没能启动；再点一次抓取即可'),
    ('重启中断', '工作台重启打断了这次抓取；再点一次抓取即可'),
    ('超过时限', '这次抓取超过时限被中断；再点一次抓取即可'),
    ('未预期的错误', '抓取过程中出现未预期的错误，已保留入库的结果'),
)


def humanize(text):
    """把技术文案/错误码换成给人看的主文案；原文仍在 detail 里保留。"""
    text = ' '.join(str(text or '').split())
    if not text:
        return ''
    for marker, phrase in _HUMAN_REASONS:
        if marker in text:
            return phrase
    return text


def label(value):
    """库里的 ISO 时间 → 本地可读；拿不到就如实留空。"""
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return ''
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone().strftime('%Y-%m-%d %H:%M')


def now():
    return datetime.now(timezone.utc).isoformat()


def stamp(value):
    """Unix 秒 → 本地可读时间；拿不到就如实留空，不编造。"""
    try:
        seconds = int(value)
    except (TypeError, ValueError):
        return ''
    if seconds <= 0:
        return ''
    return datetime.fromtimestamp(seconds, timezone.utc).astimezone().strftime('%Y-%m-%d %H:%M')


def clean(value, limit):
    value = '' if value is None else str(value)
    value = re.sub(r'\s+', ' ', value).strip()
    if len(value) > limit:
        raise ValueError(f'内容超过 {limit} 字上限')
    return value


def article_fingerprint(link, seed=''):
    """没有后台 aid 时的文章身份。

    有链接就用链接指纹；**没有链接时**（搜狗只拿到标题的情况）退回「标题+时间」
    的指纹 —— 否则 article_url('') 会抛错，整轮抓取被一条无链接的记录带崩。
    """
    material = article_url(link) if link else f'no-link:{seed}'
    return hashlib.sha256(material.encode()).hexdigest()[:32]


def accepted_link(link):
    """入库前过 article_url。不合格的合集链接不当成 href，改走无链接占位。"""
    text = '' if link is None else str(link).strip()
    if not text:
        return ''
    try:
        return article_url(text)
    except ValueError:
        return ''


# 对外行用。只认入库时写下的 source，不从占位串反推：合集不合格链接也存成 sogou-no-link:。
_LINK_MISSING_REASON = {'sogou': 'sogou_title_only', 'album': 'invalid_link'}


def annotate_missing_link(data):
    """无链接的对外行按入库路径填原因。认不出路径就不填，页面用中性说法。"""
    if data.get('link_ok', 1):
        return data
    data['link'] = ''
    reason = _LINK_MISSING_REASON.get(data.get('source') or '')
    if reason:
        data['link_missing_reason'] = reason
    return data


def interrupt_stale_jobs(connection, reason, protected=(), abandoned=()):
    """把超时仍挂着的 queued/running 标为中断。调用方负责提交。"""
    cutoff = (datetime.now(timezone.utc) - timedelta(seconds=STALE_JOB_SECONDS)).isoformat()
    protected = tuple(protected)
    exclusion = (' AND id NOT IN (' + ','.join('?' for _ in protected) + ')') if protected else ''
    moment = now()
    connection.execute("UPDATE jobs SET status='interrupted',finished_at=?,reason=? "
                       "WHERE status IN ('queued','running') AND started_at < ?" + exclusion,
                       (moment, reason, cutoff, *protected))
    # 无存活本机线程不能证明其他进程没有 worker；短宽限只给明确启动失败的行。
    abandoned = tuple(set(abandoned) - set(protected))
    if abandoned:
        short_cutoff = (datetime.now(timezone.utc) - timedelta(seconds=ABANDONED_JOB_SECONDS)).isoformat()
        # 启动失败时 reason 已写明「线程没有启动」。短宽限只补空原因，不改写成超时文案。
        connection.execute("UPDATE jobs SET status='interrupted',finished_at=?,"
                           "reason=CASE WHEN reason!='' THEN reason ELSE ? END "
                           "WHERE status IN ('queued','running') AND started_at < ? AND id IN ("
                           + ','.join('?' for _ in abandoned) + ')',
                           (moment, reason, short_cutoff, *abandoned))
    # 中断也是一种收口：订阅上的「最近状态」要跟着走，否则页面永远停在旧状态。
    connection.execute("UPDATE subscriptions SET last_run_at=?,last_status='interrupted' "
                       "WHERE fakeid IN (SELECT fakeid FROM jobs WHERE status='interrupted' AND finished_at=?)",
                       (moment, moment))


class Settings:
    """限流器用的键值表；冷却状态因此能跨进程重启存活。"""

    def __init__(self, store):
        self.store = store

    def get(self, key, default=None):
        with self.store.db() as connection:
            row = connection.execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()
        return row['value'] if row else default

    def set(self, key, value):
        with self.store.db() as connection:
            connection.execute('INSERT INTO settings(key,value) VALUES(?,?) '
                               'ON CONFLICT(key) DO UPDATE SET value=excluded.value', (key, str(value)))


class Intel:
    """情报采集 · 第一期（微信公众号）。所有抓取都经 intel_wechat_mp 的限流器。"""

    def __init__(self, store, client_factory=None, public_factory=None):
        self.store = store
        self.workspace = Path(store.workspace) / 'intel'
        self.path = self.workspace / 'intel.db'
        self.lock = threading.RLock()
        self.workers = {}
        self.client_factory = client_factory
        self.public_factory = public_factory
        self._client = None
        self._public = None
        self._settings = None

    @contextmanager
    def db(self):
        self._ensure()
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute('PRAGMA busy_timeout=30000')
            with self.lock:
                connection.execute('BEGIN IMMEDIATE')
            yield connection
            connection.commit()
        finally:
            connection.close()

    def _ensure(self):
        if getattr(self, '_ready', False):
            return
        with self.lock:
            if getattr(self, '_ready', False):
                return
            self.workspace.mkdir(parents=True, mode=0o700, exist_ok=True)
            self.workspace.chmod(0o700)
            connection = sqlite3.connect(self.path)
            try:
                connection.executescript(SCHEMA)
                columns = {row[1] for row in connection.execute('PRAGMA table_info(jobs)')}
                if 'routes' not in columns:
                    connection.execute("ALTER TABLE jobs ADD COLUMN routes TEXT NOT NULL DEFAULT ''")
                columns = {row[1] for row in connection.execute('PRAGMA table_info(link_index)')}
                if 'source_kind' not in columns:
                    connection.execute("ALTER TABLE link_index ADD COLUMN source_kind TEXT NOT NULL DEFAULT ''")
                columns = {row[1] for row in connection.execute('PRAGMA table_info(articles)')}
                for column, declaration in (('source', "TEXT NOT NULL DEFAULT ''"),
                                            ('link_ok', 'INTEGER NOT NULL DEFAULT 1')):
                    if column not in columns:
                        connection.execute(f'ALTER TABLE articles ADD COLUMN {column} {declaration}')
                # 进程重启后不可能还有活的抓取线程；把「放了很久还在跑」的行标为中断，
                # 否则它们会永远占着「已有任务」的位子，让这个号再也发不起抓取。
                # 只按时间判定，不看是哪个实例：否则同一 workspace 上第二个 Intel
                # 实例刚建好就会把第一个实例正在跑的任务误杀。
                interrupt_stale_jobs(connection, STARTUP_STALE_REASON, self._live_worker_ids(), self._abandoned_ids())
                connection.commit()
                self._release_settled_abandoned(connection)
            finally:
                connection.close()
            self.path.chmod(0o600)
            self._ready = True

    # ── 客户端 ────────────────────────────────────────────────────────────
    def client(self):
        if self._client is None:
            if self.client_factory is not None:
                self._client = self.client_factory(self)
            else:
                from intel_wechat_mp import (BackendQuota, BodyThrottle, KeychainCredentials,
                                             WechatMpClient)
                quota = BackendQuota(Settings(self))
                self._client = WechatMpClient(KeychainCredentials(), quota, BodyThrottle())
        return self._client

    def quota(self):
        return self.client().quota

    # ── 订阅号 ────────────────────────────────────────────────────────────
    def subscribe(self, payload):
        """录入订阅号。号名搜索的结果必须由用户选定 fakeid，不自动认同名号。"""
        if not isinstance(payload, dict) or set(payload) - {'fakeid', 'name', 'note', 'origin'}:
            raise ValueError('录入订阅号包含无效字段')
        identifier = fakeid(payload.get('fakeid'))
        name = clean(payload.get('name'), NAME_LIMIT)
        note = clean(payload.get('note'), NOTE_LIMIT)
        origin = clean(payload.get('origin'), NAME_LIMIT)
        if not name:
            raise ValueError('请填写公众号名称')
        with self.db() as connection:
            connection.execute('''INSERT INTO subscriptions(fakeid,name,note,origin,added_at)
                VALUES(?,?,?,?,?) ON CONFLICT(fakeid) DO UPDATE SET
                name=CASE WHEN excluded.name!='' THEN excluded.name ELSE subscriptions.name END,
                note=excluded.note, origin=CASE WHEN excluded.origin!='' THEN excluded.origin ELSE subscriptions.origin END''',
                (identifier, name, note, origin, now()))
        return self.subscription(identifier)

    def subscription(self, identifier):
        with self.db() as connection:
            row = connection.execute('SELECT * FROM subscriptions WHERE fakeid=?',
                                     (fakeid(identifier),)).fetchone()
        return self._subscription_row(row) if row else None

    @staticmethod
    def _subscription_row(row):
        data = dict(row)
        data['id'] = data['fakeid']
        return data

    def cooldown_snapshot(self):
        """只读 settings 表拿两个冷却截止时间；不构造客户端、不读凭证。"""
        out = {}
        for key, name in ((BackendQuota.K_COOLDOWN, 'backend'), (self.K_SOGOU_COOLDOWN, 'sogou')):
            try:
                value = float(self.settings().get(key, '0') or 0)
            except (TypeError, ValueError):
                value = 0.0
            out[name] = int(value) if value > time.time() else 0
        return out

    def _attach_results(self, records):
        """给每个订阅号挂上「最近一次结果」。

        只从 jobs 表回读，不再维护第二份状态；定时轮次也写任务行，所以手动与定时共用同一份。
        """
        if not records:
            return records
        identifiers = [record['fakeid'] for record in records]
        with self.db() as connection:
            rows = connection.execute('SELECT * FROM jobs WHERE fakeid IN ('
                                      + ','.join('?' for _ in identifiers) + ') ORDER BY started_at DESC',
                                      identifiers).fetchall()
        latest = {}
        for row in rows:
            latest.setdefault(row['fakeid'], row)
        cooldowns = self.cooldown_snapshot()
        for record in records:
            record['last_result'] = self._last_result(latest.get(record['fakeid']), cooldowns)
        return records

    def _last_result(self, row, cooldowns=None):
        """把一条 jobs 行整理成页面能直接渲染的「最近结果」。"""
        if row is None:
            return None
        job = self._public_job(row)
        cooldowns = cooldowns or {}
        routes = {line['path']: line['text'] for line in job['routes']}
        cooling = []
        if cooldowns.get('backend'):
            cooling.append(f"后台接口冷却至 {stamp(cooldowns['backend'])}")
        if cooldowns.get('sogou'):
            cooling.append(f"搜狗冷却至 {stamp(cooldowns['sogou'])}")
        result = dict(job)
        result.update(source='scheduled' if job['mode'] == 'scheduled' else 'manual',
                      at=job['finished_at'] or job['started_at'],
                      cooldown_until=max(list(cooldowns.values()) + [0]),
                      cooldown_label='；'.join(cooling),
                      route_map=routes,
                      suggestion=self._suggestion(job, routes, cooldowns))
        return result

    @staticmethod
    def _suggestion(job, routes, cooldowns):
        """下一步建议：只说现在真正能做的事，不鼓励反复点抓取。"""
        text = f"{job.get('reason', '')} {job.get('detail', '')}"
        album = routes.get('album') or ''
        tips = []
        if job['status'] == 'credentials':
            tips.append('先重新登录公众号后台、更新凭证，再试一次。')
        if job['status'] in ('rate_limited', 'cooldown') or '200013' in text or '限流' in text:
            until = cooldowns.get('backend')
            tips.append((f"后台接口被限流，等到 {stamp(until)} 之前不用再点抓取" if until
                         else '后台接口被限流，短时间内再点也只会再被拦') + '；这期间最有用的是补一条合集链接。')
        if '验证码' in text:
            tips.append('搜狗这一路被验证码拦住，只能等冷却过去；想稳定拿到文章还是补合集最直接。')
        if any(word in album for word in ('没有', '尚未发现', '还没发现')):
            tips.append('发一篇该号文章的完整链接到「补充该号合集」，或直接粘贴合集链接（带 album_id）。')
        if job['status'] == 'interrupted':
            tips.append(f'这次被中断了；再点一次抓取即可，已入库的文章不会重复。'
                        f'进程被杀留下的「进行中」要等开始时间起 {STALE_JOB_SECONDS} 秒才会自动标为已中断。')
        if not tips and job['status'] in ('done', 'partial') and not job.get('added'):
            tips.append('这一轮没有新文章。如果这个号有合集，补一条合集链接后成功率最高。')
        return ' '.join(tips[:3])

    def subscriptions(self):
        with self.db() as connection:
            rows = connection.execute('''SELECT s.*, (SELECT COUNT(*) FROM articles a WHERE a.fakeid=s.fakeid) AS article_count
                FROM subscriptions s ORDER BY s.added_at DESC''').fetchall()
        return self._attach_results([self._subscription_row(row) for row in rows])

    def remove_subscription(self, identifier):
        """只解除订阅关系；已抓到的文章保留，不删除任何数据。"""
        identifier = fakeid(identifier)
        with self.db() as connection:
            connection.execute('DELETE FROM subscriptions WHERE fakeid=?', (identifier,))
        return {'fakeid': identifier, 'removed': True}

    # ── 三期：免后台取文章 ────────────────────────────────────────────────
    K_SOGOU_COOLDOWN = 'mp.sogou.cooldown_until'

    def sogou_cooldown_until(self):
        try:
            value = float(self.settings().get(self.K_SOGOU_COOLDOWN, '0') or 0)
        except (TypeError, ValueError):
            return 0
        return int(value) if value > time.time() else 0

    def set_sogou_cooldown(self):
        until = int(time.time()) + SOGOU_COOLDOWN_SECONDS
        self.settings().set(self.K_SOGOU_COOLDOWN, repr(until))
        return until

    def settings(self):
        if self._settings is None:
            self._settings = Settings(self)
        return self._settings

    def public_client(self):
        """合集/搜狗客户端。测试通过 public_factory 注入假实现，绝不出网。"""
        if self._public is None:
            if self.public_factory is not None:
                self._public = self.public_factory(self)
            else:
                from intel_public import PublicClient
                self._public = PublicClient()
        return self._public

    def albums(self, identifier=None):
        with self.db() as connection:
            if identifier:
                rows = connection.execute('SELECT * FROM albums WHERE fakeid=? ORDER BY found_at DESC',
                                          (fakeid(identifier),)).fetchall()
            else:
                rows = connection.execute('SELECT * FROM albums ORDER BY found_at DESC LIMIT 200').fetchall()
        return [dict(row) for row in rows]

    def remember_albums(self, identifier, found, found_from=''):
        """记录「这个号有哪些合集」。同一合集重复发现不会重复插入。"""
        kept = 0
        with self.db() as connection:
            for album_id in found:
                if not str(album_id).isdigit():
                    continue
                connection.execute('''INSERT INTO albums(fakeid,album_id,found_from,found_at) VALUES(?,?,?,?)
                    ON CONFLICT(fakeid,album_id) DO NOTHING''',
                    (identifier, str(album_id), found_from[:120], now()))
                kept += 1
        return kept

    def note_album(self, identifier, album_id, title, article_count):
        """记录合集元信息。只在拿到非零值时覆盖，避免用空值抹掉已知信息。"""
        with self.db() as connection:
            connection.execute('''INSERT INTO albums(fakeid,album_id,title,article_count,found_at) VALUES(?,?,?,?,?)
                ON CONFLICT(fakeid,album_id) DO UPDATE SET
                  title=CASE WHEN excluded.title!='' THEN excluded.title ELSE albums.title END,
                  article_count=CASE WHEN excluded.article_count>0 THEN excluded.article_count
                                     ELSE albums.article_count END''',
                (identifier, str(album_id), str(title or '')[:200], int(article_count or 0), now()))

    @staticmethod
    def complete_article_link(url, identifier):
        """只接受该号带校验串的完整文章链接，不凭同名或残缺 URL 猜测。"""
        try:
            link = article_url(url)
            parts = urlsplit(link)
            query = parse_qs(parts.query)
            if (parts.path != '/s' or query.get('__biz') != [identifier]
                    or len(query.get('chksm', [])) != 1 or not query['chksm'][0].strip()):
                return ''
            return link
        except (ValueError, KeyError, TypeError):
            return ''

    def local_album_links(self, identifier):
        """仅查本地三个来源，按优先级去重，最多两条；不初始化或扫描知识库。"""
        found = []
        def take(rows):
            for row in rows:
                link = self.complete_article_link(row[0], identifier)
                if link and link not in found:
                    found.append(link)
                if len(found) == 2:
                    break
        with self.db() as connection:
            take(connection.execute('SELECT link FROM link_index ORDER BY resolved_at DESC'))
        path = Path(self.store.workspace) / 'knowledge' / 'knowledge.db'
        if len(found) < 2 and path.is_file():
            # ro 避免写入业务库；缺表/旧库不阻断另外两个来源。
            try:
                with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)) as connection:
                    take(connection.execute("SELECT url FROM documents WHERE url LIKE '%mp.weixin.qq.com/%' ORDER BY modified DESC"))
            except sqlite3.DatabaseError:
                pass
        if len(found) < 2:
            with self.db() as connection:
                take(connection.execute('SELECT link FROM recommendation_sources WHERE fakeid=? ORDER BY seen_at DESC', (identifier,)))
        return found

    def discover_albums_from_links(self, identifier, links, max_reads=2, on_read=None):
        """尚无合集时读取本地完整文章来发现合集。

        最多读 max_reads 篇，并且不超过 2。返回实际读取次数，失败的读取也计入。
        已有合集时返回 0，不再发请求。
        """
        if self.albums(identifier):
            return 0
        cap = 2 if type(max_reads) is not int else max(0, min(max_reads, 2))
        if cap == 0:
            return 0
        usable = []
        for candidate in links:
            link = self.complete_article_link(candidate, identifier)
            if link and link not in usable:
                usable.append(link)
            if len(usable) >= cap:
                break
        reads = 0
        for link in usable:
            reads += 1
            if on_read is not None:
                on_read(reads)
            try:
                parsed = self.client().fetch_article(link)
            except (ValueError, OSError, RateLimited, CredentialsMissing, CredentialExpired):
                continue
            if parsed.get('biz') != identifier:
                continue
            found = discover_album_ids(parsed.get('raw') or '')
            if found:
                self.remember_albums(identifier, found, found_from=link)
                return reads
        return reads

    def album_items(self, identifier, wanted=HISTORY_PAGE_SIZE * MAX_PAGES_PER_RUN, max_requests=ALBUM_REQUEST_BUDGET):
        """主路：合集接口翻页，直到够数或没有下一页。"""
        client = self.public_client()
        catalog = self.albums(identifier)
        items, used, stop = [], 0, ''
        if not catalog:
            return {'items': [], 'requests': 0, 'stopped': '这个号还没发现任何合集', 'albums': 0}
        for album in catalog:
            if used >= max_requests or len(items) >= wanted:
                break
            begin = None
            for _ in range(max_requests - used):
                used += 1  # 失败的页请求也占本轮预算。
                try:
                    page = client.album_page(identifier, album['album_id'], *(begin or (None, None)))
                except AlbumError as exc:
                    stop = str(exc)
                    break
                self.note_album(identifier, album['album_id'], page['title'] or album['title'],
                                page['article_count'] or album['article_count'])
                for item in page['items']:
                    if not item['link']:
                        continue
                    item['source'] = 'album'
                    item['album_id'] = album['album_id']
                    items.append(item)
                if not page['continue_flag'] or not page['items']:
                    break
                last = page['items'][-1]
                begin = (last['msgid'], last['itemidx'])
                if used >= max_requests or len(items) >= wanted:
                    break
        if used >= max_requests and stop == '':
            stop = f'本轮合集请求已达上限 {max_requests} 次，剩余留给下一轮'
        return {'items': items[:wanted], 'requests': used, 'stopped': stop, 'albums': len(catalog)}

    def sogou_items(self, identifier, name, max_requests=SOGOU_REQUEST_BUDGET, cooldown=0):
        """补充最新：搜狗。**实测跳转必定被反爬拦住**，所以通常只拿得到标题与时间。"""
        if cooldown > 0:
            return {'items': [], 'requests': 0, 'blocked': True,
                    'stopped': f'搜狗冷却中，本轮跳过（至 {stamp(cooldown)}）'}
        client = self.public_client()
        if not name:
            return {'items': [], 'requests': 0, 'blocked': False,
                    'stopped': '没有号名，搜狗要求按名称搜索'}
        used = 1  # 搜索尝试固定占一次，无论成功或失败。
        try:
            hits = client.sogou_search(name)
        except SogouBlocked as exc:
            return {'items': [], 'requests': used, 'blocked': True, 'stopped': str(exc)}
        except (ValueError, OSError) as exc:
            return {'items': [], 'requests': used, 'blocked': False, 'stopped': str(exc)}
        # 只保留账号名与订阅号完全一致的结果：实测结果里混有他号文章。
        mine = [hit for hit in hits if hit['account'] == name]
        items, blocked, note = [], False, ''
        for hit in mine:
            if used >= max_requests:
                note = f'本轮搜狗请求已达上限 {max_requests} 次'
                break
            used += 1  # 先计尝试，验证码和网络错误同样占额度。
            link = ''
            try:
                link = client.sogou_resolve(hit['sogou_link'])
            except SogouBlocked as exc:
                note, blocked = str(exc), True
                break
            except (ValueError, OSError):
                pass  # 网络层失败不算验证码，如实记「没拿到链接」
            items.append({'title': hit['title'][:300], 'link': link,
                          'published_at': int(hit['published_at'] or 0), 'source': 'sogou',
                          'aid': '', 'link_ok': bool(link), 'album_id': ''})
        return {'items': items, 'requests': used, 'blocked': blocked, 'stopped': note,
                'scanned': len(hits), 'matched': len(mine)}

    # ── 二期 B：定时订阅（每日抓最新） ────────────────────────────────────
    SCHEDULED_ROUTES = {'album': '定时轮次不走合集（只走后台接口那一路）',
                        'sogou': '定时轮次不走搜狗（只走后台接口那一路）'}

    @staticmethod
    def _routes_detail(routes, extra=''):
        """三条路线的结果拼成给人看的 detail。"""
        lines = [f"{PATH_LABELS[path]}：{routes[path]}" for path in PATH_ORDER if routes.get(path)]
        if extra:
            lines.append(extra)
        return '；'.join(lines)

    def _claim_scheduled_job(self, record):
        """定时也走和手动 run 同一个单飞闸门。

        检查活跃任务与建任务放在同一个 BEGIN IMMEDIATE 事务里；有任务在跑就返回 None，
        这次定时算跳过，不排队、不重试。
        """
        with self.db() as connection:
            busy = self.active_job(connection)
            if busy:
                connection.rollback()
                return None
            job_id = self._start_job(connection, record['fakeid'], record['name'], 'scheduled')
            connection.execute('UPDATE subscriptions SET last_run_at=?,last_status=? WHERE fakeid=?',
                               (now(), 'running', record['fakeid']))
        self._release_settled_abandoned()
        return job_id

    def _record_scheduled_skip(self, record, status, reason, routes):
        """整轮没发请求也要留痕：写一条已结束的定时任务行，刷新页面后还能看到。"""
        with self.db() as connection:
            if self.active_job(connection):
                connection.rollback()
                return False
            moment = now()
            connection.execute('''INSERT INTO jobs(id,fakeid,name,mode,status,reason,detail,routes,started_at,finished_at)
                VALUES(?,?,?,?,?,?,?,?,?,?)''',
                               (uuid.uuid4().hex, record['fakeid'], record['name'], 'scheduled', status,
                                str(reason)[:300], self._routes_detail(routes, reason)[:2000],
                                json.dumps(routes, ensure_ascii=False)[:2000], moment, moment))
            connection.execute('UPDATE subscriptions SET last_run_at=?,last_status=? WHERE fakeid=?',
                               (moment, status, record['fakeid']))
        return True

    def _scheduled_account(self, record, job_id, routes):
        """认领之后的整段：请求 → 落库 → 统计 → 收口。

        返回 (action, extra)：action 是 'continue'（下一个号）或 'stop'（本轮到此为止）；
        extra 带要累计的计数与回执文案。限流/凭证/网络异常在这里就地收口；
        其他意外异常（落库、统计、收口这些都在内）原样上抛，由 daily_latest 统一收口，
        绝不把任务行留在 running。
        """
        try:
            items = self.client().appmsg_list(record['fakeid'], 0, LATEST_PAGE_SIZE)
            routes['backend'] = f'取到 {len(items)} 篇'
        except RateLimited as exc:
            # 200013：立即停本轮，不重试；下一轮若仍未冷却会累计到 3 次转 6 小时冷却。
            routes['backend'] = f'{exc}（未重试）'
            self._finish_job(job_id, 'rate_limited', str(exc), routes=routes,
                             detail=self._routes_detail(routes))
            return 'stop', {'state': 'rate_limited', 'detail': f'{exc}；本轮已停止，未重试',
                            'cooldown_until': exc.until or self.client().quota.cooldown_until()}
        except CredentialsMissing as exc:
            routes['backend'] = str(exc)
            self._finish_job(job_id, 'credentials', str(exc), routes=routes,
                             detail=self._routes_detail(routes))
            return 'stop', {'state': 'credentials', 'detail': str(exc)}
        except CredentialExpired as exc:
            routes['backend'] = str(exc)
            self._finish_job(job_id, 'credentials', str(exc), routes=routes,
                             detail=self._routes_detail(routes))
            return 'stop', {'state': 'credentials', 'detail': str(exc)}
        except (ValueError, OSError) as exc:
            routes['backend'] = str(exc)
            self._finish_job(job_id, 'error', f"{record['name'] or record['fakeid']} · {exc}",
                             routes=routes, detail=self._routes_detail(routes))
            return 'continue', {'detail': f"{record['name'] or record['fakeid']} · {exc}"}
        listed = len(items)
        created_count = 0
        with self.db() as connection:
            for item in items:
                item['name'] = record['name']
                # 复用外层事务：_upsert_article 自己开事务会自锁。
                _, created = self._upsert_article(record['fakeid'], item, job_id, connection)
                created_count += int(created)
        self._finish_job(job_id, 'done', '', routes=routes, detail=self._routes_detail(routes),
                         found=listed, added=created_count)
        return 'continue', {'listed': listed, 'new_articles': created_count}

    def _close_scheduled_round(self, job_id, record, routes, exc, receipt):
        """认领之后任何一步出意外都要把任务行收口，不能留 running 把单飞闸门堵到超时。

        收口本身也失败（例如数据库不可写）时不吞错：记日志、在回执里写明恢复路径——
        那一行会按残留规则等到 started_at 起 STALE_JOB_SECONDS 秒被标为已中断。
        """
        routes['backend'] = f'未预期的错误：{type(exc).__name__}'
        reason = f'定时轮次出现未预期的错误：{type(exc).__name__}'
        try:
            self._finish_job(job_id, 'error', reason, routes=routes, detail=self._routes_detail(routes))
            closed = True
        except Exception as finish_error:  # noqa: BLE001
            closed = False
            _LOG.exception('情报定时任务 %s 收口失败：%s', job_id, finish_error)
        receipt['state'] = 'error'
        receipt['detail'] = (f"{record['name'] or record['fakeid']} · 未预期的错误：{type(exc).__name__}；本轮已停止"
                             + ('' if closed else
                                f'；任务行收口也失败，会按残留规则等到开始时间起 '
                                f'{STALE_JOB_SECONDS} 秒后标为已中断'))
        return receipt

    def daily_latest(self, run_id='', cancel=None, deadline=None):
        """一轮「已订阅号各抓最新 1 页」。严格走一期限流器，并与手动抓取共用单飞闸门。

        回执写明：处理号数、新文章数、限流/冷却/凭证失效状态。新文章只进 intel.db
        待确认，**不自动送回收站、也不进知识库**。每个号都留一条任务行，所以刷新页面
        后仍能看到这次为什么没抓到。
        """
        quota = self.client().quota
        until = quota.cooldown_until()
        if until:
            # 冷却中整轮跳过，一个后台请求都不发；但「这次为什么没跑」要留痕。
            routes = dict(self.SCHEDULED_ROUTES, backend=f'冷却中，本轮跳过（至 {stamp(until)}）')
            reason = f'接口限流冷却中，本轮整轮跳过，未发送任何请求；冷却至 {stamp(until)}'
            for record in self.subscriptions():
                self._record_scheduled_skip(record, 'cooldown', reason, routes)
            return {'accounts': 0, 'new_articles': 0, 'listed': 0, 'skipped': len(self.subscriptions()),
                    'state': 'cooldown', 'cooldown_until': until, 'detail': reason}
        receipt = {'accounts': 0, 'new_articles': 0, 'listed': 0, 'state': 'done', 'detail': ''}
        for record in self.subscriptions():
            if cancel is not None and cancel.is_set():
                receipt['state'] = 'cancelled'
                receipt['detail'] = '本轮已请求停止，已处理的号保留结果'
                break
            if deadline is not None and time.monotonic() >= deadline:
                receipt['state'] = 'timeout'
                receipt['detail'] = '本轮到达时限，剩下的号留到下一轮'
                break
            job_id = self._claim_scheduled_job(record)
            if job_id is None:
                # 已有任务在跑（手动或上一轮定时）：这次定时跳过，不排队、不重试，也不发请求。
                busy = self.active_job() or {}
                receipt['state'] = 'busy'
                receipt['detail'] = (f"已有抓取任务在进行中（「{busy.get('name') or busy.get('id') or '另一个任务'}」），"
                                     '本轮定时这次直接跳过，没有排队也没有重试，未发送任何请求')
                receipt['active_job'] = {'id': busy.get('id', ''), 'name': busy.get('name', ''),
                                         'status': busy.get('status', '')}
                break
            routes = dict(self.SCHEDULED_ROUTES)
            receipt['accounts'] += 1
            try:
                # 认领之后占着单飞闸门：请求、落库、统计、收口任何一步意外都不能把任务行留在 running。
                action, extra = self._scheduled_account(record, job_id, routes)
            except Exception as exc:  # noqa: BLE001
                self._close_scheduled_round(job_id, record, routes, exc, receipt)
                break
            receipt['listed'] += extra.get('listed', 0)
            receipt['new_articles'] += extra.get('new_articles', 0)
            for key in ('state', 'detail', 'cooldown_until'):
                if extra.get(key) is not None:
                    receipt[key] = extra[key]
            if action == 'stop':
                break
        if not receipt['detail']:
            receipt['detail'] = (f"处理 {receipt['accounts']} 个订阅号，列表 {receipt['listed']} 篇，"
                                 f"新增 {receipt['new_articles']} 篇待确认")
        return receipt

    def analyze_scheduled(self, run_id, task, cancel, config, deadline):
        """scheduled_tasks 的执行入口。返回 (status, exit_code, output_json)。"""
        receipt = self.daily_latest(run_id=run_id, cancel=cancel, deadline=deadline)
        status = {'done': 'succeeded', 'rate_limited': 'partial', 'cooldown': 'partial', 'busy': 'partial',
                  'credentials': 'partial', 'cancelled': 'cancelled', 'timeout': 'partial'}.get(receipt['state'], 'partial')
        return status, None, json.dumps(receipt, ensure_ascii=False)

    def search_accounts(self, query):
        return self.client().search_biz(query)

    def identify_from_link(self, url):
        """复用既有入口：文章反查并发现合集，合集链接无需请求即可补录。

        结果（时间、找到几个合集、这篇文章没合集）会留痕，刷新页面后仍能看到；
        只存结果，不存链接本身与 chksm 这类参数。
        """
        parts = urlsplit(str(url or '').strip())
        query = parse_qs(parts.query)
        has_chksm = len(query.get('chksm', [])) == 1 and bool(query['chksm'][0].strip())
        if 'album_id' in query:
            if (parts.scheme not in ('http', 'https') or parts.netloc != 'mp.weixin.qq.com'
                    or parts.path != '/mp/appmsgalbum' or len(query.get('__biz', [])) != 1
                    or len(query['album_id']) != 1 or not re.fullmatch(r'\d{8,30}', query['album_id'][0])):
                raise ValueError('请填写有效的该号合集链接（含 __biz 和 album_id）')
            identifier = fakeid(query['__biz'][0])
            count = self.remember_albums(identifier, query['album_id'], found_from=url)
            self.note_album_search(identifier, 'album', count, has_chksm)
            return {'fakeid': identifier, 'nickname': '', 'title': '合集入口', 'chars': 0,
                    'published_at': 0, 'url': url, 'albums_found': count,
                    'album_search': self.album_search(identifier)}
        declared = query.get('__biz')
        target = declared[0] if len(declared or []) == 1 else ''
        try:
            parsed = self.client().fetch_article(article_url(url))
        except (ValueError, OSError, RateLimited, CredentialExpired, CredentialsMissing) as exc:
            self.note_album_search(target, 'article', 0, has_chksm, f'{ALBUM_SEARCH_FAILED}：{exc}')
            raise
        if declared and declared != [parsed['biz']]:
            self.note_album_search(parsed['biz'], 'article', 0, has_chksm, '文章页的公众号与链接不一致，未记录')
            raise ValueError('文章页面公众号编号与链接不一致，未记录合集')
        found = discover_album_ids(parsed.get('raw') or '')
        count = self.remember_albums(parsed['biz'], found, found_from=url)
        self.note_album_search(parsed['biz'], 'article', count, has_chksm)
        return {'fakeid': parsed['biz'], 'nickname': parsed['nickname'],
                'title': parsed['title'], 'chars': parsed['chars'],
                'published_at': parsed['published_at'], 'url': parsed['url'], 'albums_found': count,
                'album_search': self.album_search(parsed['biz'])}

    def note_album_search(self, identifier, kind, found, has_chksm=False, detail=''):
        """留痕「上次试过发现合集，结果如何」。

        只存结果：链接本身（含 chksm）不存，凭证一律不落库。
        """
        try:
            key = fakeid(identifier) if identifier else ''
        except ValueError:
            key = ''
        with self.db() as connection:
            connection.execute('INSERT INTO album_searches(id,fakeid,kind,albums_found,has_chksm,detail,searched_at) '
                               'VALUES(?,?,?,?,?,?,?)',
                               (uuid.uuid4().hex, key, str(kind)[:20], int(found or 0),
                                1 if has_chksm else 0, str(detail)[:200], now()))
            # 每个号只留最近 20 条，够页面回看，也不会无限长。
            connection.execute('DELETE FROM album_searches WHERE fakeid=? AND id NOT IN '
                               '(SELECT id FROM album_searches WHERE fakeid=? ORDER BY searched_at DESC LIMIT 20)',
                               (key, key))
        return True

    def album_search(self, identifier):
        """刷新页面后还能看到的「发现合集」留痕，并区分本篇没合集 / 该号没合集。"""
        try:
            key = fakeid(identifier) if identifier else ''
        except ValueError:
            key = ''
        row, known = None, 0
        if key:
            with self.db() as connection:
                row = connection.execute('SELECT * FROM album_searches WHERE fakeid=? '
                                         'ORDER BY searched_at DESC LIMIT 1', (key,)).fetchone()
                known = connection.execute('SELECT COUNT(*) FROM albums WHERE fakeid=?', (key,)).fetchone()[0]
        if row is None:
            return {'attempted': False, 'albums_known': known, 'albums_found': 0, 'kind': '', 'at': '',
                    'at_label': '', 'has_chksm': False,
                    'note': '还没有用文章链接反查过合集。补一篇该号的完整文章链接，或直接粘贴合集链接。'}
        found = int(row['albums_found'] or 0)
        detail = str(row['detail'] or '')
        if detail.startswith(ALBUM_SEARCH_FAILED):
            note = f'上次想用它发现合集，但{detail}。'
        elif row['kind'] == 'album':
            note = f'上次粘贴的是合集链接：{"已记录 " + str(found) + " 个合集" if found else "这个合集之前已经记过了"}。'
        elif found:
            note = f'上次读的那篇文章里发现 {found} 个合集，抓取会直接用它们。'
        elif known:
            note = f'上次读的那篇文章里没有合集；这个号已记录 {known} 个合集，抓取会直接用它们。'
        else:
            note = '上次读的那篇文章里没有合集；这个号目前还没有任何合集记录（不等于这个号没有合集）。'
        if detail and not detail.startswith(ALBUM_SEARCH_FAILED):
            note = f'{note}（{detail}）'
        return {'attempted': True, 'albums_known': known, 'albums_found': found, 'kind': row['kind'],
                'at': row['searched_at'], 'at_label': label(row['searched_at']),
                'has_chksm': bool(row['has_chksm']), 'note': note}

    # ── 文章 ──────────────────────────────────────────────────────────────
    def articles(self, identifier=None, limit=100, offset=0):
        identifier = fakeid(identifier) if identifier else None
        limit, offset = max(1, min(int(limit), 300)), max(0, int(offset))
        query = ('SELECT a.*, (SELECT drop_id FROM articles d WHERE d.id=a.id AND d.drop_id!=\'\') AS drop_id '
                 'FROM articles a WHERE (? IS NULL OR a.fakeid=?) ORDER BY a.published_at DESC, a.added_at DESC LIMIT ? OFFSET ?')
        with self.db() as connection:
            rows = connection.execute(query, (identifier, identifier, limit, offset)).fetchall()
            total = connection.execute('SELECT COUNT(*) FROM articles WHERE (? IS NULL OR fakeid=?)',
                                       (identifier, identifier)).fetchone()[0]
        return {'articles': [self._article_row(row) for row in rows], 'total': total}

    @staticmethod
    def _article_row(row):
        data = dict(row)
        data.pop('body', None)  # 列表不返回正文全文
        data['images'] = json.loads(data.get('images') or '[]')
        data['published_label'] = stamp(data.get('published_at'))
        # 库里为了让 UNIQUE 成立存了占位串；对外一律还原成「没有链接」，
        # 并按 source 注明原因，页面才不会把它渲染成一个打不开的 href。
        return annotate_missing_link(data)

    def article(self, article_id):
        with self.db() as connection:
            row = connection.execute('SELECT * FROM articles WHERE id=?', (article_id,)).fetchone()
        if row is None:
            raise ValueError('文章不存在')
        data = dict(row)
        data['images'] = json.loads(data.get('images') or '[]')
        data['published_label'] = stamp(data.get('published_at'))
        return annotate_missing_link(data)

    def _upsert_article(self, identifier, item, job_id, connection=None):
        """fakeid + 链接双去重。已存在的行不会被后来的列表结果覆盖正文。

        传入 connection 时复用调用方的事务 —— 在已开启的事务里再开一个会自锁。
        不合格链接在算指纹之前清掉，这样会存成占位而不是把整条跳过。
        """
        item = dict(item)
        item['link'] = accepted_link(item.get('link'))
        aid = str(item.get('aid') or '').strip()[:64] or article_fingerprint(
            item.get('link') or '', f"{item.get('published_at', 0)}|{item.get('title', '')}")
        identifier_id = hashlib.sha256(f'{identifier}:{aid}'.encode()).hexdigest()[:32]
        if connection is not None:
            return self._write_article(connection, identifier, aid, identifier_id, item, job_id)
        with self.db() as own:
            return self._write_article(own, identifier, aid, identifier_id, item, job_id)

    @staticmethod
    def _write_article(connection, identifier, aid, identifier_id, item, job_id):
        real_link = item.get('link') or ''
        # articles.link 上有 UNIQUE 约束。搜狗只拿得到标题时没有链接，
        # 若真存空串，第二条无链接记录就会撞 UNIQUE 把整轮抓取带崩。
        # 所以无链接的行存一个**不可解析**的占位值，并用 link_ok=0 标明它不是链接。
        stored = real_link or f'sogou-no-link:{identifier}:{aid}'
        existing = connection.execute('SELECT id FROM articles WHERE link=? OR (fakeid=? AND aid=?)',
                                      (stored, identifier, aid)).fetchone()
        if existing:
            connection.execute('UPDATE articles SET title=?, name=CASE WHEN name!=\'\' THEN name ELSE ? END '
                               'WHERE id=?', (item['title'], item.get('name', ''), existing['id']))
            return existing['id'], False
        connection.execute('''INSERT INTO articles(id,fakeid,aid,link,name,title,published_at,job_id,added_at,source,link_ok)
            VALUES(?,?,?,?,?,?,?,?,?,?,?)''',
            (identifier_id, identifier, aid, stored, item.get('name', ''), item['title'],
             item.get('published_at', 0), job_id, now(), item.get('source', ''),
             1 if real_link else 0))
        return identifier_id, True

    def save_body(self, article_id, parsed):
        """正文入库并标为「待确认」；绝不自动进知识库。"""
        with self.db() as connection:
            row = connection.execute('SELECT * FROM articles WHERE id=?', (article_id,)).fetchone()
            if row is None:
                raise ValueError('文章不存在')
            name = parsed.get('nickname') or row['name'] or ''
            connection.execute('''UPDATE articles SET body=?,chars=?,images=?,name=?,body_status='awaiting_confirm',
                body_at=?,error='' WHERE id=?''',
                (parsed['text'], int(parsed['chars']), json.dumps(parsed['images'], ensure_ascii=False),
                 name, now(), article_id))
        return self.article(article_id)

    def fail_body(self, article_id, message):
        with self.db() as connection:
            connection.execute("UPDATE articles SET body_status='failed',error=? WHERE id=?",
                               (str(message)[:200], article_id))
        return self.article(article_id)

    def push_to_intake(self, article_id, intake):
        """送入回收站：调用既有创意回收接口。

        provenance 从一期的 `intel:wechat_mp:<fakeid>:<aid>` 换成通用采集模块的
        `collect:<平台>:<来源>:<编号>`（合同 collect-core 第 9 条）。
        旧前缀仍然**认得**（collect_core.parse_provenance），老数据不会变成脏数据。
        """
        from collect_core import legacy_provenance, provenance as build_provenance
        record = self.article(article_id)
        if record['body_status'] != 'awaiting_confirm' or not record['body']:
            raise ValueError('这篇文章还没有正文，先抓正文再送入回收站')
        marker = build_provenance('wechat_mp', record['fakeid'], record['aid'])
        reason = clean(f'情报采集 · 公众号「{record["name"]}」', NOTE_LIMIT)
        # 回收项编号沿用一期的算法：同一篇重复点不会产生第二条，
        # 换 provenance 格式也不会让已送过的文章变成第二条。
        drop_id = hashlib.sha256(
            legacy_provenance('wechat_mp', record['fakeid'], record['aid']).encode()).hexdigest()[:32]
        saved = intake.receive({'id': drop_id, 'kind': 'text', 'title': record['title'],
                                'text': record['body'], 'reason': reason})
        with self.db() as connection:
            connection.execute('UPDATE articles SET drop_id=? WHERE id=?', (saved['id'], article_id))
        return {'drop_id': saved['id'], 'provenance': marker, 'document_pending': False}

    def forget_intake_drop(self, drop_id):
        """回收项被删后清掉 articles.drop_id 痕迹。

        不然情报页会一直显示「已进回收站」，点开却找不到东西；清掉后同一篇可以重新送。
        """
        if not isinstance(drop_id, str) or not re.fullmatch(r'[a-f0-9]{32}', drop_id):
            raise ValueError('回收项编号格式无效')
        with self.db() as connection:
            return {'cleared': connection.execute("UPDATE articles SET drop_id='' WHERE drop_id=?",
                                                  (drop_id,)).rowcount}

    # ── 抓取任务 ──────────────────────────────────────────────────────────
    def jobs(self, limit=30):
        with self.db() as connection:
            rows = connection.execute('''SELECT j.*, s.name AS account_name FROM jobs j
                LEFT JOIN subscriptions s ON s.fakeid=j.fakeid ORDER BY j.started_at DESC LIMIT ?''',
                (max(1, min(int(limit), 100)),)).fetchall()
        return [self._public_job(row) for row in rows]

    @staticmethod
    def _public_job(row):
        data = dict(row)
        data['status_label'] = STATUS_LABELS.get(data['status'], data['status'])
        data['mode_label'] = MODE_LABELS.get(data.get('mode'), data.get('mode') or '')
        # 主文案给人看，错误码只放在括号里；原文完整保留在 reason/detail。
        data['headline'] = humanize(data.get('reason') or data.get('detail'))
        data['at_label'] = label(data.get('finished_at') or data.get('started_at'))
        data['routes'] = Intel._route_lines(data.get('routes'))
        data['active'] = data['status'] in Intel.ACTIVE
        return data

    @staticmethod
    def _route_lines(raw):
        """jobs.routes 里的 JSON → 页面可直接渲染的三条路线。"""
        try:
            parsed = json.loads(raw or '{}')
        except (TypeError, ValueError):
            return []
        if not isinstance(parsed, dict):
            return []
        return [{'path': path, 'label': PATH_LABELS[path], 'text': str(parsed[path])}
                for path in PATH_ORDER if parsed.get(path)]

    def job(self, job_id):
        for row in self.jobs(200):
            if row['id'] == job_id:
                return row
        raise ValueError('抓取任务不存在')

    def _start_job(self, connection, identifier, name, mode):
        job_id = uuid.uuid4().hex
        connection.execute('INSERT INTO jobs(id,fakeid,name,mode,status,started_at) VALUES(?,?,?,?,?,?)',
                           (job_id, identifier, name, mode, 'running', now()))
        return job_id

    ACTIVE = ('queued', 'running')

    def _abandoned_ids(self):
        with _PROCESS_WORKERS_LOCK:
            return tuple(job_id for path, job_id in _PROCESS_ABANDONED
                         if path == str(self.path.resolve()))

    def _discard_abandoned(self, job_id):
        with _PROCESS_WORKERS_LOCK:
            _PROCESS_ABANDONED.discard((str(self.path.resolve()), job_id))

    def _release_settled_abandoned(self, connection=None):
        """已经离开 queued/running 的放弃行不再占着进程集合。仍在跑的留着，短宽限才认它们。

        调用方必须已经提交；run() 发现忙会回滚，不能在那次事务里改这个集合。
        """
        if connection is None:
            if not self._abandoned_ids():
                return
            with self.db() as own:
                self._release_settled_abandoned(own)
            return
        abandoned = self._abandoned_ids()
        if not abandoned:
            return
        rows = connection.execute(
            'SELECT id,status FROM jobs WHERE id IN (' + ','.join('?' for _ in abandoned) + ')',
            abandoned).fetchall()
        found = {row[0]: row[1] for row in rows}
        settled = [job_id for job_id in abandoned if found.get(job_id) not in self.ACTIVE]
        if not settled:
            return
        with _PROCESS_WORKERS_LOCK:
            path = str(self.path.resolve())
            for job_id in settled:
                _PROCESS_ABANDONED.discard((path, job_id))

    def _live_worker_ids(self):
        with _PROCESS_WORKERS_LOCK:
            live = {key for key, worker in self.workers.items() if worker.is_alive()}
            live.update(job_id for (path, job_id), worker in _PROCESS_WORKERS.items()
                        if path == str(self.path.resolve()) and worker.is_alive())
            live.difference_update(self._abandoned_ids())
        return tuple(live)

    def active_job(self, connection=None):
        """当前是否已有抓取任务在跑。

        后台接口是**全局串行**的，所以哪怕是两个不同的号，也不该同时跑 ——
        第二个任务只会在限流器里干等 90 秒，既没有意义也更容易触发 200013。
        检查与建任务放在同一个 BEGIN IMMEDIATE 事务里，两个并发请求只有一个能建。
        """
        if connection is not None:
            # 本进程明确放弃的行用短宽限；其他行保留原有长宽限与存活线程保护。
            # 与调用方同一事务：status()/run() 提交后才生效，避免再开一个连接把自己锁死。
            protected = self._live_worker_ids()
            abandoned = self._abandoned_ids()
            interrupt_stale_jobs(connection, LIVE_STALE_REASON, protected, abandoned)
            live_clause = (' OR id IN (' + ','.join('?' for _ in protected) + ')') if protected else ''
            exclusion = (' AND id NOT IN (' + ','.join('?' for _ in abandoned) + ')') if abandoned else ''
            row = connection.execute(
                f"SELECT * FROM jobs WHERE (status IN {self.ACTIVE}{live_clause}){exclusion} ORDER BY started_at LIMIT 1",
                (*protected, *abandoned)).fetchone()
            return dict(row) if row else None
        with self.db() as own:
            found = self.active_job(own)
        self._release_settled_abandoned()
        return found

    def _finish_job(self, job_id, status, reason='', routes=None, detail=None, **counts):
        columns = ['status=?', 'reason=?', 'finished_at=?']
        values = [status, str(reason)[:300], now()]
        if routes is not None:
            columns.append('routes=?')
            values.append(json.dumps(routes, ensure_ascii=False)[:2000])
        if detail is not None:
            columns.append('detail=?')
            values.append(str(detail)[:2000])
        for key, value in counts.items():
            columns.append(f'{key}=?')
            values.append(int(value))
        values.append(job_id)
        with self.db() as connection:
            connection.execute(f'UPDATE jobs SET {",".join(columns)} WHERE id=? AND status != \'interrupted\'', values)
            # 订阅上的最近状态跟着任务收口一起更新。成功/部分/失败/限流/中断都走这里，
            # 不会只更新成功那一路；状态从库里回读，避免和已标为中断的行不一致。
            row = connection.execute('SELECT fakeid,status FROM jobs WHERE id=?', (job_id,)).fetchone()
            if row:
                connection.execute('UPDATE subscriptions SET last_run_at=?,last_status=? WHERE fakeid=?',
                                   (now(), row['status'], row['fakeid']))
        if status not in self.ACTIVE:
            self._discard_abandoned(job_id)

    def _write_job_detail(self, job_id, detail):
        with self.db() as connection:
            connection.execute("UPDATE jobs SET detail=? WHERE id=? AND status != 'interrupted'", (detail[:2000], job_id))

    def run(self, payload):
        """起一次抓取。合集优先 → 搜狗补最新 → 后台（默认关闭）。"""
        allowed = {'fakeid', 'mode', 'bodies', 'sogou', 'backend'}
        if not isinstance(payload, dict) or set(payload) - allowed:
            raise ValueError('抓取参数无效')
        identifier = fakeid(payload.get('fakeid'))
        mode = payload.get('mode')
        if mode not in ('latest', 'history'):
            raise ValueError('请选择抓最新或抓历史')
        bodies = payload.get('bodies', MAX_BODIES_PER_RUN)
        if type(bodies) is not int or not 0 <= bodies <= MAX_BODIES_PER_RUN:
            raise ValueError(f'每次最多抓 {MAX_BODIES_PER_RUN} 篇正文')
        record = self.subscription(identifier)
        if record is None:
            raise ValueError('请先录入这个订阅号')
        # 后台闸门只在后台那一路真正要发请求时才拦；公开接口有自己的冷却与预算。
        use_backend = payload.get('backend', False)
        use_sogou = payload.get('sogou', True)
        if type(use_backend) is not bool or type(use_sogou) is not bool:
            raise ValueError('抓取路线的开关必须是开或关')
        if use_backend:
            until = self.client().quota.cooldown_until()
            if until:
                pass  # 后台那一路在 _execute 里跳过；公开两路照常走
        with self.db() as connection:
            # 同号（或全局）已有任务在跑就不新建，把现有的返回给页面。
            busy = self.active_job(connection)
            if busy:
                connection.rollback()
                existing = self._public_job(busy)
                existing['reused'] = True
                return existing
            job_id = self._start_job(connection, identifier, record['name'], mode)
            connection.execute('UPDATE subscriptions SET last_run_at=?,last_status=? WHERE fakeid=?',
                               (now(), 'running', identifier))
        self._release_settled_abandoned()
        worker = threading.Thread(target=self._execute,
                                  args=(job_id, identifier, record['name'], mode, bodies, use_sogou, use_backend),
                                  name=f'intel-mp-{job_id[:8]}', daemon=True)
        start_error = None
        with _PROCESS_WORKERS_LOCK:
            self.workers[job_id] = worker
            key = (str(self.path.resolve()), job_id)
            _PROCESS_WORKERS[key] = worker
            try:
                worker.start()
            except Exception as exc:
                _PROCESS_ABANDONED.add(key)
                self.workers.pop(job_id, None)
                _PROCESS_WORKERS.pop(key, None)
                start_error = exc
        if start_error is not None:
            # 库写入放在放开线程锁之后，避免和 active_job 的「先库后锁」交叉。
            reason = '任务线程没有启动，已中断：' + str(start_error)
            failures = []
            for attempt in range(2):
                try:
                    self._finish_job(job_id, 'interrupted', reason)
                    break
                except Exception as finish_error:
                    failures.append(str(finish_error))
                    _LOG.exception('情报任务 %s 启动失败后的写回失败（第 %s 次）', job_id, attempt + 1)
                    if attempt == 0:
                        time.sleep(START_FAILURE_RETRY_SECONDS)
            if failures:
                # 独立尽力写入，不能让辅助记录失败覆盖原始启动异常。
                try:
                    with self.db() as connection:
                        connection.execute('UPDATE jobs SET detail=?,reason=? WHERE id=?',
                                           (('启动失败写回异常：' + '；'.join(failures))[:2000], reason[:300], job_id))
                except Exception:
                    _LOG.exception('情报任务 %s 启动失败原因也未能落库', job_id)
            raise ValueError(reason) from start_error
        return self.job(job_id)

    def wait_for(self, job_id, timeout=5):
        worker = self.workers.get(job_id)
        if worker is not None:
            worker.join(timeout)
        return self.job(job_id)

    def _execute(self, job_id, identifier, name, mode, bodies, use_sogou=True, use_backend=False):
        """后台线程。三路取文章：合集 → 搜狗 → 后台（默认关闭）。

        公开接口与后台各有自己的限流器，互不影响。任何一路命中限制就只停那一路，
        绝不自动重试。回执写明每条文章来自哪一路。
        """
        wanted = HISTORY_PAGE_SIZE * MAX_PAGES_PER_RUN if mode == 'history' else LATEST_PAGE_SIZE
        added = done_bodies = probe_reads = body_reads = 0
        def record_probe(count):
            nonlocal probe_reads
            probe_reads = count
        paths, routes, notes = {}, {}, []
        merged = {}

        def absorb(items, source):
            for item in items:
                item.setdefault('source', source)
                item['name'] = name
                key = item.get('link') or f"{item.get('published_at', 0)}|{item.get('title', '')}"
                # 同一篇文章可能几条路都给：链接优先，没有链接的先用有链接的补上。
                if key in merged:
                    if not merged[key].get('link') and item.get('link'):
                        merged[key] = {**merged[key], **item, 'source': merged[key]['source']}
                    continue
                merged[key] = item
                paths.setdefault(item['source'], 0)
                paths[item['source']] += 1

        try:
            client = self.client()
            # run() 的 active_job 事务保证公开路径单飞；只清预算计数，保留节流时刻。
            begin_round = getattr(self.public_client(), 'begin_round', None)
            if begin_round is not None:
                begin_round()
            # ── 主路：合集 ──
            known = self.albums(identifier)
            if not known:
                links = self.local_album_links(identifier)
                # 抓历史的文章页总预算是 MAX_BODIES_PER_RUN，探测读取也算进去。
                # 抓最新仍只受「最多两篇探测」和本次正文参数分别约束。
                probe_cap = min(2, MAX_BODIES_PER_RUN) if mode == 'history' else 2
                probe_reads = self.discover_albums_from_links(identifier, links, max_reads=probe_cap, on_read=record_probe)
            album = self.album_items(identifier, wanted=wanted)
            absorb(album['items'], 'album')
            # 回执必须写明每一路的情况，成功也要写，不能只写失败。
            # 每一路的结果单独存（routes），页面刷新后还能按路线分开看。
            if not album['albums']:
                routes['album'] = ('本地没有该号的完整文章链接，无法发现合集；可在回收站收下一篇该号文章后重试'
                                   if not known and not links else '已尝试本地完整文章链接，尚未发现合集；可手动补充该号文章或合集链接')
            elif album['stopped']:
                routes['album'] = album['stopped']
            else:
                routes['album'] = f"{album['albums']} 个合集，取到 {len(album['items'])} 篇"

            # ── 补充：搜狗 ──
            if use_sogou:
                sogou_cooldown = self.sogou_cooldown_until()
                sogou = self.sogou_items(identifier, name, cooldown=sogou_cooldown)
                absorb(sogou['items'], 'sogou')
                if sogou.get('scanned') is not None:
                    sogou_note = f"{sogou['scanned']} 条结果里 {sogou['matched']} 条确属本号"
                else:
                    sogou_note = ''
                if sogou['stopped']:
                    sogou_note = f"{sogou_note}；{sogou['stopped']}" if sogou_note else sogou['stopped']
                if sogou['blocked'] and not sogou_cooldown:
                    self.set_sogou_cooldown()
                    sogou_note += '（已进入 6 小时冷却）'
                routes['sogou'] = sogou_note or '没有取到结果'
            else:
                routes['sogou'] = '已关闭'

            # ── 第三路：后台（默认关闭）──
            if use_backend:
                until = self.client().quota.cooldown_until()
                if until:
                    routes['backend'] = f'冷却中，跳过（至 {stamp(until)}）'
                else:
                    try:
                        self.client().credentials_ready()
                        pages = 1 if mode == 'latest' else MAX_PAGES_PER_RUN
                        size = LATEST_PAGE_SIZE if mode == 'latest' else HISTORY_PAGE_SIZE
                        backend_items = []
                        for page in range(pages):
                            backend_items.extend(self.client().appmsg_list(identifier, page * size, size))
                        absorb(backend_items, 'backend')
                        routes['backend'] = f'取到 {len(backend_items)} 篇'
                    except RateLimited as exc:
                        routes['backend'] = f'{exc}（未重试）'
                    except (CredentialsMissing, CredentialExpired) as exc:
                        routes['backend'] = str(exc)
                    except (ValueError, OSError) as exc:
                        routes['backend'] = str(exc)
            else:
                routes['backend'] = '已关闭（可用页面开关打开）'

            # ── 合并去重、按时间排序、入库 ──
            ordered = sorted(merged.values(), key=lambda i: i.get('published_at', 0), reverse=True)[:wanted]
            skipped = 0
            for item in ordered:
                try:
                    _, created = self._upsert_article(identifier, item, job_id)
                    added += int(created)
                except (ValueError, sqlite3.Error):
                    # 一条坏链接不能把整轮抓取带崩：记下来，继续下一条。
                    skipped += 1
            if skipped:
                notes.append(f'有 {skipped} 条链接无法识别，已跳过（其余照常入库）')

            # ── 正文：只抓真正有链接的 ──
            # 抓历史时，探测和正文共用文章页总预算。失败的读取也占名额。
            body_cap = bodies
            if mode == 'history':
                body_cap = min(bodies, max(0, MAX_BODIES_PER_RUN - probe_reads))
            for row in self.articles(identifier, limit=300)['articles']:
                if body_reads >= body_cap:
                    break
                if row['body_status'] != 'pending' or not row['link']:
                    continue
                body_reads += 1
                try:
                    self.save_body(row['id'], client.fetch_article(row['link']))
                    done_bodies += 1
                except RateLimited as exc:
                    notes.append(f'{exc}；已停止，不再重试')
                    break
                except (ValueError, OSError) as exc:
                    self.fail_body(row['id'], str(exc))
        except (RateLimited, CredentialsMissing, CredentialExpired, ValueError, OSError) as exc:
            notes.append(str(exc))
        except Exception as exc:  # noqa: BLE001
            # 工作线程里的意外异常绝不能静默死掉：那会让任务永远停在 running。
            notes.append(f'抓取过程中出现未预期的错误：{type(exc).__name__}；已保留已入库的结果')

        # 三条路线的结果排在前面，其他补充说明跟在后面。
        notes = [f"{PATH_LABELS[path]}：{routes[path]}" for path in PATH_ORDER if routes.get(path)] + notes
        finished = False
        try:
            summary = '；'.join(f'{key} {value} 篇' for key, value in sorted(paths.items())) or '没有取到列表'
            detail = (f'入库 {added} 篇（{summary}），探测读取 {probe_reads} 篇，'
                      f'正文读取 {body_reads} 篇，正文 {done_bodies} 篇待确认')
            if notes:
                detail += '。' + '；'.join(notes)
            self._finish_job(job_id, 'done' if added or paths else 'partial', detail[:300], routes=routes,
                             found=len(merged), added=added, bodies=done_bodies, pages=0)
            finished = True
            # 状态已经写成 done/partial。详情写失败不能再把状态改成 error。
            self._write_job_detail(job_id, detail)
        except Exception:
            if not finished:
                try:
                    self._finish_job(job_id, 'error', '结束任务时写回失败')
                except Exception as finish_error:  # noqa: BLE001
                    # 收口本身也失败（例如库不可写）不能吞错：如实记录，
                    # 那一行会按残留规则等到开始时间起 STALE_JOB_SECONDS 秒被标为已中断。
                    _LOG.exception('情报任务 %s 收口失败：%s', job_id, finish_error)
        finally:
            with _PROCESS_WORKERS_LOCK:
                self.workers.pop(job_id, None)
                _PROCESS_WORKERS.pop((str(self.path.resolve()), job_id), None)

    def status(self):
        """界面总览：凭证状态 + 冷却截止时间 + 计数。

        **只读**：不发送任何后台请求，也不改动 strike 计数。曾经这里调用
        clear_cooldown() 把 strike 清零，导致页面每刷新一次就把「连续 3 次
        触发冷却」这条纪律抹掉一次，冷却永远不会触发。
        """
        record = {'schema': 'intel-wechat-mp/v1', 'mode': 'wechat_mp', 'cooldown_until': 0,
                  'cooldown_label': '', 'credentials': 'unknown', 'detail': ''}
        strikes = 0
        try:
            state = self.client().quota.state()
            strikes = state['strikes']
            record.update(cooldown_until=state['cooldown_until'])
            if state['cooling']:
                record['cooldown_label'] = stamp(state['cooldown_until'])
            self.client().credentials_ready()
            record['credentials'] = 'ready'
            record['detail'] = '后台凭证可用' if not state['cooling'] else '冷却中，恢复后可再抓取'
        except CredentialsMissing as exc:
            record['credentials'] = 'missing'
            record['detail'] = str(exc)
        except (CredentialExpired, ValueError, OSError) as exc:
            record['credentials'] = 'expired'
            record['detail'] = str(exc)
        # 计数在事务外读：限流状态也在同一张表里，嵌套写事务会自锁。
        record['rate_strikes'] = strikes
        cooldowns = self.cooldown_snapshot()
        # 搜狗冷却与后台冷却相互独立，页面要分开显示，不能只报一个 ready。
        record['sogou_cooldown_until'] = cooldowns['sogou']
        record['sogou_cooldown_label'] = stamp(cooldowns['sogou']) if cooldowns['sogou'] else ''
        with self.db() as connection:
            record['subscriptions'] = connection.execute('SELECT COUNT(*) FROM subscriptions').fetchone()[0]
            record['articles'] = connection.execute('SELECT COUNT(*) FROM articles').fetchone()[0]
            record['awaiting'] = connection.execute(
                "SELECT COUNT(*) FROM articles WHERE body_status='awaiting_confirm' AND drop_id=''").fetchone()[0]
            busy = self.active_job(connection)
        self._release_settled_abandoned()
        # 页面据此禁用抓取按钮，避免连点产生第二个并发任务。
        record['busy'] = bool(busy)
        record['active_job'] = {'id': busy['id'], 'name': busy['name'], 'status': busy['status']} if busy else None
        return record
