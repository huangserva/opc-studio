"""情报采集 · 自动发现公众号文章链接（A：独立模块）。

黄总已定（10-11 更新）：搜索路线是 remio → 付费 Serper → 自建 SearXNG
（本机 127.0.0.1:8888）→ skipped；全部明确不可用才 skipped。本模块只做「搜索 →
规范化候选 → 读文章 → 校验归属 → 取合集」这一段，返回**纯结果包**：不写库、
不读凭证、不初始化数据库、不发真实网络请求（搜索提供者与文章读者都由调用方
注入；自建搜索只在真实接线时指向本机）。

设计依据 coordination/prep-intel-discovery-report.md §2 与
coordination/prep-self-search-report.md；接线（定时/手动）由 intel_store 侧（B）
负责，本模块不导入 Intel，也不预测入库结果。

铁律（沿用一期）：
1. 归属只认「页面解析出的 biz 与订阅 fakeid 严格相等」；昵称同名不算，URL 声明
   的 __biz 与 fakeid 冲突也拒绝。
2. 候选只接受精确 mp.weixin.qq.com 的 `/s` 文章链接（长链保留必要 query），
   拒绝伪域名、凭据、非标准端口、合集/后台/搜狗跳转链接。
3. 回执（可持久化/展示）不含 HTML、凭据或校验串：链接去掉 chksm 等校验参数；
   含完整规范链接的「保存用结果包」只通过 `save_articles` 交给接线方入库。
4. 正文只输出 textContent。
"""
import datetime
import fcntl
import json
import os
import re
import socket
import stat
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from assistant_web import WebTools
from intel_public import discover_album_ids
from intel_wechat_mp import article_url, fakeid

RECEIPT_VERSION = 1
SEARCH_SITE = 'mp.weixin.qq.com'
FRESHNESS_NO_ALBUM = 'pm'    # 无合集：初始化用月范围
FRESHNESS_WITH_ALBUM = 'pw'  # 已有合集：维护用周范围
FRESHNESS_VALUES = ('pd', 'pw', 'pm', 'py')
MAX_CANDIDATES = 8
MAX_READS = 3
MAX_SEARCHES = 1  # 逻辑查询次数上限（不是回退时的物理调用次数，见 _budget）
MAX_NAME_CHARS = 50
MAX_TITLE_CHARS = 240

# 自建 SearXNG（本机）：只允许回环地址，默认端口 8888，响应上限 1 MiB。
SELF_SEARCH_DEFAULT_BASE = 'http://127.0.0.1:8888'
SELF_SEARCH_DEFAULT_PORT = 8888
SELF_SEARCH_ALLOWED_HOSTS = frozenset({'127.0.0.1', 'localhost'})
SELF_SEARCH_TIMEOUT = 12.0
SELF_SEARCH_MAX_BYTES = 1024 * 1024
FRESHNESS_TIME_RANGE = {'pw': 'week', 'pm': 'month'}

# 付费 Serper（Google 搜索 API）：固定端点 + 主机白名单，密钥只从文件读。
SERPER_ENDPOINT = 'https://google.serper.dev/search'
SERPER_ALLOWED_HOST = 'google.serper.dev'
SERPER_DEFAULT_KEY_FILE = '~/.config/article-studio/serper.key'
SERPER_TIMEOUT = 12.0
SERPER_MAX_BYTES = 1024 * 1024
SERPER_MAX_NUM = 10            # Serper num 上限：单次最多取 10 条
SERPER_DAILY_CAP = 50          # 每日物理请求上限（成本护栏，默认值）
SERPER_USAGE_PATH = '~/Library/Application Support/ArticleStudio/self-search/serper-usage.json'
SERPER_KEY_MAX_BYTES = 4096    # 密钥文件读取上限（超过即视为非法）
SERPER_USAGE_MAX_BYTES = 65536  # 用量记录读取上限（超过即视为损坏）
# freshness → Serper 的 tbs 时间范围；未列出的 freshness 不带 tbs。
SERPER_FRESHNESS_TBS = {'pw': 'qdr:w', 'pm': 'qdr:m'}

# 回执里必须脱敏的校验类查询参数（完整链接只进保存用结果包）。
SENSITIVE_QUERY_KEYS = frozenset({'chksm', 'key', 'signature', 'sig'})

# 逐候选安全失败码（回执里只放码，不回显被拒链接）
CODE_REJECTED = 'rejected_link'
CODE_DECLARED_CONFLICT = 'declared_biz_conflict'
CODE_DUPLICATE = 'duplicate'
CODE_OVER_BUDGET = 'over_budget'
CODE_VERIFIED = 'verified'
CODE_BIZ_MISMATCH = 'biz_mismatch'
CODE_READ_FAILED = 'read_failed'
CODE_UNREAD = 'unread'

# Serper 专属安全失败码（只放码，不回显密钥/链接/路径内容）。
CODE_KEY_FILE_MISSING = 'key_file_missing'
CODE_KEY_FILE_EMPTY = 'key_file_empty'
CODE_KEY_FILE_UNREADABLE = 'key_file_unreadable'
CODE_KEY_FILE_PERMISSIONS = 'key_file_permissions'
CODE_KEY_FILE_INVALID = 'key_file_invalid'
CODE_KEY_INVALID = 'key_invalid'
CODE_QUOTA_EXHAUSTED = 'quota_exhausted'
CODE_DAILY_CAP = 'daily_cap'
CODE_USAGE_UNAVAILABLE = 'usage_unavailable'

STATE_OK = 'ok'
STATE_SKIPPED = 'skipped'
STATE_FAILED = 'failed'
STATE_CANCELLED = 'cancelled'

STOP_NONE = 'none'
STOP_CANCEL = 'cancel'
STOP_DEADLINE = 'deadline'
STOP_READ_BUDGET = 'read_budget'
STOP_PROVIDER_UNAVAILABLE = 'provider_unavailable'
STOP_REDIRECT_BLOCKED = 'redirect_blocked'
STOP_SEARCH_TIMEOUT = 'search_timeout'
STOP_SEARCH_FAILED = 'search_failed'

# 页面元数据里的稳定身份（短链/长链都可能缺，缺了如实留空）。
_MSGID = re.compile(r'var\s+msgid\s*=\s*["\']?(\d{5,30})')
_ITEMIDX = re.compile(r'var\s+(?:itemidx|idx)\s*=\s*["\']?(\d{1,3})')

# WebTools 把「桌面未运行 / 未配源 / 额度用完」和普通失败都收成 ValueError，
# 适配层按消息文本区分：命中的算明确不可用（可回退），其余按硬失败（不切换）。
_REMIO_UNAVAILABLE_MARKERS = ('未安装', '未就绪', '尚未配置搜索源', '额度已用完', '无法启动联网服务')
_REMIO_TIMEOUT_MARKERS = ('响应超时',)
_REMIO_CANCEL_MARKERS = ('任务已停止', '任务已取消')


class ProviderUnavailable(Exception):
    """提供者**明确**不可用（未安装/桌面未运行/未配源/额度用完）。可切换下一个。

    code：可选安全失败码（如 key_invalid/quota_exhausted/daily_cap），供接线方与
    回执区分不可用原因；**不含任何凭据**。
    """

    def __init__(self, message, provider='', code=''):
        super().__init__(message)
        self.provider = provider
        self.code = code


class SearchFailed(Exception):
    """搜索硬失败（格式异常等）。记 failed，不切换提供者。"""


class SearchTimeout(SearchFailed):
    """搜索超时。记 failed，不切换（避免迟到付费请求）。"""


class RedirectBlocked(SearchFailed):
    """默认传输遇到 3xx 重定向：一律当失败（安全码 redirect_blocked）。

    本机提供者返回 302 指向外部主机时，若跟随重定向就会越过主机边界；因此默认
    禁重定向，把 3xx 当硬失败，不回显被重定向的地址。
    """


class Cancelled(Exception):
    """任务被取消。"""


class DeadlineExceeded(Exception):
    """已到/已过截止时间。记 failed/deadline，不触发回退、不再读取。"""


# ── 小工具 ──────────────────────────────────────────────────────────────────
def _field(source, key, default=None):
    if isinstance(source, dict):
        return source.get(key, default)
    return getattr(source, key, default)


def _cancelled(cancel):
    if cancel is None:
        return False
    if callable(cancel):
        try:
            return bool(cancel())
        except TypeError:
            return bool(cancel)
    if hasattr(cancel, 'is_set'):
        return bool(cancel.is_set())
    return bool(cancel)


def _expired(clock, deadline):
    return deadline is not None and clock() >= deadline


def _bounded_int(value, default, hard_max):
    """预算项归一：bool/非整数/非正/不可转一律回默认，合法正整数取 min(值, hard_max)。

    这是硬上限：调用方传入再大的值也不会突破模块定义的预算。
    """
    if isinstance(value, bool):
        return default
    if isinstance(value, float) and not value.is_integer():
        return default
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    if number < 1:
        return default
    return min(number, hard_max)


def _match(pattern, text):
    if not isinstance(text, str):
        return ''
    found = pattern.search(text)
    return found.group(1) if found else ''


def build_query(name):
    """账号名 → 查询词。限制长度、拒绝控制字符，并限定 site。"""
    if not isinstance(name, str) or not name.strip():
        raise ValueError('公众号名称不能为空')
    cleaned = re.sub(r'\s+', ' ', name).strip()
    if len(cleaned) > MAX_NAME_CHARS:
        raise ValueError(f'公众号名称不超过 {MAX_NAME_CHARS} 字')
    if any(ord(ch) < 32 for ch in cleaned):
        raise ValueError('公众号名称含控制字符')
    return f'{cleaned} site:{SEARCH_SITE}'


def _normalize_candidate(url):
    """搜索结果的链接 → 规范文章链接；非文章/伪域名/带凭据/合集/后台/搜狗一律拒绝。"""
    try:
        return article_url(url)
    except (ValueError, TypeError, KeyError):
        return None


def _declared_biz(link):
    """长链 /s?__biz=… 声明的 biz；短链没有则为空。"""
    query = urllib.parse.urlsplit(link).query
    if not query:
        return ''
    values = urllib.parse.parse_qs(query).get('__biz') or []
    return values[0].strip() if values else ''


def _budget(budget):
    """归一预算并施加硬上限。

    max_searches 是**逻辑查询次数**（同一 query/freshness 一次），不是回退时的物理
    调用次数：remio 明确不可用后回退自建搜索，物理调用最多 2 次，仍算 1 次逻辑查询。
    """
    return {
        'max_searches': _bounded_int(_field(budget, 'max_searches', MAX_SEARCHES),
                                     MAX_SEARCHES, MAX_SEARCHES),
        'max_candidates': _bounded_int(_field(budget, 'max_candidates', MAX_CANDIDATES),
                                       MAX_CANDIDATES, MAX_CANDIDATES),
        'max_reads': _bounded_int(_field(budget, 'max_reads', MAX_READS),
                                  MAX_READS, MAX_READS),
    }


def _safe_link(url):
    """回执用安全投影：去掉 chksm 等校验类查询参数，其余规范部分原样。

    短链没有 query，原样返回；完整链接只放进保存用结果包，不进可持久化回执。
    """
    parts = urllib.parse.urlsplit(url)
    if not parts.query:
        return url
    pairs = urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
    kept = [(key, value) for key, value in pairs if key.lower() not in SENSITIVE_QUERY_KEYS]
    if len(kept) == len(pairs):
        return url
    return urllib.parse.urlunsplit(
        (parts.scheme, parts.netloc, parts.path, urllib.parse.urlencode(kept), ''))


def _safe_article(article):
    """回执用安全投影：只保留非正文标量与脱敏后的主链接。

    正文包（body，含 images 等嵌套 URL）**不进回执**：完整正文与图片只由
    `save_articles` 交给接线方。这样 images 里可能携带的 chksm/key/signature/sig
    等校验串不会出现在可持久化回执里。
    """
    return {
        'link': _safe_link(article['link']),
        'title': article['title'],
        'published_at': article['published_at'],
        'name': article['name'],
        'source': article['source'],
        'identity': dict(article['identity']),
    }


class _CancelBridge:
    """把入口 cancel（callable / Event）桥接成 WebTools 可判真假的对象。

    WebTools 只看 `job.get('cancel')` 的真值，直接塞 callable 会永远为真，因此用
    每次取真值时现算的包装对象。
    """

    def __init__(self, cancel):
        self._cancel = cancel

    def __bool__(self):
        return _cancelled(self._cancel)


def _provider_attempts(provider, success):
    value = getattr(provider, 'attempts', None)
    try:
        value = int(value)
    except (TypeError, ValueError):
        value = 0
    if success and value < 1:
        value = 1
    return value


def _self_search_base(base_url):
    """校验并归一自建搜索地址：只允许本机回环的 http（默认端口 8888）。"""
    if not isinstance(base_url, str) or not base_url.strip():
        raise ValueError('自建搜索地址无效')
    parts = urllib.parse.urlsplit(base_url.strip())
    host = parts.hostname
    # userinfo 用 `is not None` 判断：空 userinfo（http://@host）也必须拒绝，
    # 真值判断会把空串当「没有凭据」而放行。
    if parts.scheme != 'http' or host not in SELF_SEARCH_ALLOWED_HOSTS \
            or parts.username is not None or parts.password is not None \
            or parts.query or parts.fragment:
        raise ValueError('自建搜索只允许本机 http 地址（127.0.0.1/localhost）')
    port = parts.port or SELF_SEARCH_DEFAULT_PORT
    return f'http://{host}:{port}{parts.path.rstrip("/")}'


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """禁止重定向：拒绝生成新 Request，3xx 直接以安全码失败。

    所有 30x（301/302/303/307/308）最终都会调用 redirect_request；本处理器在此
    抛 RedirectBlocked，重定向目标**永远不会**被发出。相比返回 None（由
    http_error_default 抛 HTTPError），直接抛异常不产生 HTTPError 及其临时文件
    包装，避免无谓的资源告警。
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        try:
            fp.close()
        except Exception:
            pass
        raise RedirectBlocked('自建搜索拒绝重定向')


def _default_self_search_open(url, timeout, handlers=()):
    """默认传输：禁环境代理、**禁重定向**、限时请求，返回 (status, body_bytes)。

    handlers 仅供测试注入假 HTTP 处理器（零网络）；默认不含重定向处理器，
    本机服务返回 3xx 时不会把请求引向外部主机。
    """
    request = urllib.request.Request(url, headers={'Accept': 'application/json'})
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}), _NoRedirectHandler(), *handlers)
    with opener.open(request, timeout=timeout) as response:
        return response.status, response.read(SELF_SEARCH_MAX_BYTES + 1)


# ── Serper（付费 Google 搜索）辅助 ──────────────────────────────────────────
def _serper_endpoint(base_url):
    """校验并归一 Serper 端点：只允许 https://google.serper.dev（主机白名单）。

    userinfo 用 `is not None` 判断：空 userinfo（https://@host）也必须拒绝。
    """
    if not isinstance(base_url, str) or not base_url.strip():
        raise ValueError('Serper 地址无效')
    parts = urllib.parse.urlsplit(base_url.strip())
    if parts.scheme != 'https' or parts.hostname != SERPER_ALLOWED_HOST \
            or parts.username is not None or parts.password is not None \
            or parts.query or parts.fragment or parts.port not in (None, 443):
        raise ValueError('Serper 只允许 https://google.serper.dev')
    return urllib.parse.urlunsplit(
        ('https', SERPER_ALLOWED_HOST, parts.path or '/search', '', ''))


def _decode_serper_key(data):
    """密钥 bytes → (key, code)。仅接受可打印 ASCII 单行（允许一个行尾终止符）。

    拒绝内嵌换行、其他控制字符、非 ASCII 与非法 UTF-8；空内容回 key_file_empty。
    绝不回显内容。
    """
    if not isinstance(data, (bytes, bytearray)):
        return None, CODE_KEY_FILE_INVALID
    data = bytes(data)
    if data.endswith(b'\r\n'):
        body = data[:-2]
    elif data.endswith((b'\n', b'\r')):
        body = data[:-1]
    else:
        body = data
    if not body:
        return None, CODE_KEY_FILE_EMPTY
    try:
        text = body.decode('ascii')
    except UnicodeDecodeError:
        return None, CODE_KEY_FILE_INVALID
    if any(ch < ' ' or ch > '~' for ch in text):
        # 含控制字符（含内嵌换行）或非可打印字符。
        return None, CODE_KEY_FILE_INVALID
    return text, ''


def _load_serper_key(key_file=None):
    """只从文件读 Serper 密钥，返回 (key, code)。

    成功时 key 为非空字符串、code 为空；失败时 key 为 None、code 是安全失败码。
    **同一个已打开的 fd** 上先 fstat 校验（普通文件、权限 ≤0600、属主当前用户），
    再从该 fd 读取，消除 stat 与 read 之间的 TOCTOU 窗口；内容须为可打印 ASCII
    单行（见 `_decode_serper_key`）。**任何密钥文件问题都不抛异常**（含非法 UTF-8），
    只回安全码；密钥内容不进异常/日志/repr/回执。
    """
    if key_file:
        path = Path(key_file).expanduser()
    else:
        env = os.environ.get('SERPER_API_KEY_FILE', '').strip()
        path = Path(env).expanduser() if env else Path(SERPER_DEFAULT_KEY_FILE).expanduser()
    try:
        # O_NONBLOCK：路径若被误指为 FIFO/特殊文件，打开不会因无写端而阻塞；
        # 打开成功后仍用**同一个 fd** 做 fstat 校验（非普通文件立即拒绝），
        # 不回到路径 stat/read，避免 TOCTOU 窗口。软链按既有策略跟随（不加
        # O_NOFOLLOW），软链目标的权限/属主在同一 fd 上校验。
        fd = os.open(str(path), os.O_RDONLY | os.O_NONBLOCK)
    except FileNotFoundError:
        return None, CODE_KEY_FILE_MISSING
    except OSError:
        return None, CODE_KEY_FILE_UNREADABLE
    try:
        try:
            info = os.fstat(fd)
        except OSError:
            return None, CODE_KEY_FILE_UNREADABLE
        if not stat.S_ISREG(info.st_mode):
            return None, CODE_KEY_FILE_PERMISSIONS   # 非常规文件（目录/设备/FIFO…）
        if info.st_mode & 0o077:
            # 权限比 0600 宽：拒绝使用，只回安全码，不回显路径内容。
            return None, CODE_KEY_FILE_PERMISSIONS
        if info.st_uid != os.geteuid():
            return None, CODE_KEY_FILE_PERMISSIONS   # 属主非当前用户
        try:
            data = os.read(fd, SERPER_KEY_MAX_BYTES + 1)
        except OSError:
            return None, CODE_KEY_FILE_UNREADABLE
    finally:
        os.close(fd)
    if len(data) > SERPER_KEY_MAX_BYTES:
        return None, CODE_KEY_FILE_INVALID
    return _decode_serper_key(data)


def _serper_request_body(query, count, freshness):
    """构造 Serper 请求体：固定 gl/hl，num 硬上限 10，freshness 映射为 tbs。"""
    body = {'q': query, 'gl': 'cn', 'hl': 'zh-cn',
            'num': _bounded_int(count, SERPER_MAX_NUM, SERPER_MAX_NUM)}
    tbs = SERPER_FRESHNESS_TBS.get(freshness)
    if tbs:
        body['tbs'] = tbs
    return body


def _is_mp_link(link):
    """仅精确 mp.weixin.qq.com 主机的链接进入既有验证流程（拒绝伪域名）。"""
    try:
        return urllib.parse.urlsplit(link).hostname == SEARCH_SITE
    except ValueError:
        return False


def _classify_serper_http(code):
    """HTTP 状态码 → 异常（由调用方 raise）。密钥无效/额度用尽算明确不可用。"""
    if code in (401, 403):
        return ProviderUnavailable('Serper 密钥无效', provider='serper', code=CODE_KEY_INVALID)
    if code == 429:
        return ProviderUnavailable('Serper 额度已用尽', provider='serper',
                                   code=CODE_QUOTA_EXHAUSTED)
    if 300 <= code < 400:
        return RedirectBlocked('Serper 拒绝重定向')
    return SearchFailed(f'Serper 返回 HTTP {code}')


class _Reservation:
    """一次每日额度预留的结果。allowed 为真表示已成功预留 1 次物理请求配额。

    code 在未预留时给出原因（daily_cap / usage_unavailable），**不含路径**。
    """

    __slots__ = ('allowed', 'code')

    def __init__(self, allowed, code=''):
        self.allowed = allowed
        self.code = code


# 进程内线程锁：与 fcntl 进程锁配合，保证同进程多线程也不会同时进入临界区
# （否则同一进程两次 flock 同一文件会自我阻塞）。
_USAGE_THREAD_LOCK = threading.Lock()

# 用量记录日期：严格 `YYYY-MM-DD`（零填充两位月/日）。
_USAGE_DATE_RE = re.compile(r'^\d{4}-\d{2}-\d{2}$')


def _valid_usage_date(value):
    """严格校验用量记录日期：必须是 `YYYY-MM-DD` 且为真实日历日。

    仅形状匹配不够——`2000-02-30` 形状合法但不是真实日历日，须拒绝。
    非 str、格式不符或日历非法都返回 False。
    """
    if not isinstance(value, str) or not _USAGE_DATE_RE.match(value):
        return False
    try:
        datetime.date(int(value[0:4]), int(value[5:7]), int(value[8:10]))
    except ValueError:
        return False
    return True


class _SerperUsage:
    """Serper 每日请求计数：独立锁文件内原子完成「读→校验→预留→可靠落盘」。

    - 进程锁用 `fcntl.flock`（锁文件 `<usage>.lock`），线程锁用模块级
      `_USAGE_THREAD_LOCK`；读取、判上限、自增、落盘都在同一临界区，跨进程/跨线程
      都不会超发。读计数与写计数之间不存在窗口。
    - 落盘用唯一临时文件 + `os.fsync` + `os.replace` 原子替换，避免固定 `.tmp`
      文件名导致的并发覆盖。
    - 区分「首次无记录/合法跨天」（归零）与「损坏/不可读/写失败」（安全拒绝，
      返回 usage_unavailable）：后者不发放配额，宁可少搜也不越过成本护栏。
    - 预留即计数：物理请求前先记账，失败请求也计入（预留后不回减）。
    """

    def __init__(self, path=None, clock=None, daily_cap=SERPER_DAILY_CAP):
        self.path = Path(path).expanduser() if path else Path(SERPER_USAGE_PATH).expanduser()
        self.clock = clock or time.time
        self.daily_cap = _bounded_int(daily_cap, SERPER_DAILY_CAP, 10 ** 9)

    def _today(self):
        return time.strftime('%Y-%m-%d', time.localtime(self.clock()))

    def _lock_path(self):
        return self.path.with_name(self.path.name + '.lock')

    def _read_record(self, today):
        """返回 (record, ok)；ok 为假表示记录损坏/不可读，调用方须安全拒绝。"""
        try:
            fd = os.open(str(self.path), os.O_RDONLY)
        except FileNotFoundError:
            return {'date': today, 'count': 0}, True      # 首次无记录
        except OSError:
            return None, False                            # 不可读
        try:
            raw = os.read(fd, SERPER_USAGE_MAX_BYTES + 1)
        except OSError:
            return None, False
        finally:
            os.close(fd)
        if len(raw) > SERPER_USAGE_MAX_BYTES:
            return None, False                            # 过大：视为损坏
        try:
            document = json.loads(raw.decode('utf-8'))
        except (ValueError, UnicodeError):
            return None, False                            # 坏 JSON / 坏编码
        if not isinstance(document, dict):
            return None, False
        date = document.get('date')
        count = document.get('count')
        # M1：先完整校验整份记录，再判跨天。任一项不合法都安全拒绝，绝不
        # 清零放行（宁可少搜也不越过成本护栏）。
        if not _valid_usage_date(date):
            return None, False                            # 非法日期格式/非真实日历日
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            return None, False                            # 非法计数
        if date > today:
            return None, False                            # 未来日期：时钟回拨/异常
        if date == today:
            return {'date': today, 'count': count}, True
        return {'date': today, 'count': 0}, True          # 合法历史跨天：归零

    def _write_record(self, record):
        """唯一临时文件 + fsync + os.replace 原子落盘；失败返回 False（不抛）。"""
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=str(self.path.parent),
                                       prefix=self.path.name + '.', suffix='.tmp')
        except OSError:
            return False
        try:
            with os.fdopen(fd, 'wb') as handle:
                handle.write(json.dumps(record).encode('utf-8'))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, self.path)
        except OSError:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            return False
        return True

    def reserve(self):
        """原子预留 1 次配额，返回 _Reservation。

        整个「读→校验→判上限→自增→落盘」在同一锁临界区内完成；无法确认或保存
        用量时安全拒绝（usage_unavailable），不发放配额。
        """
        with _USAGE_THREAD_LOCK:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                lock_fd = os.open(str(self._lock_path()), os.O_CREAT | os.O_RDWR, 0o600)
            except OSError:
                return _Reservation(False, CODE_USAGE_UNAVAILABLE)
            try:
                try:
                    fcntl.flock(lock_fd, fcntl.LOCK_EX)
                except OSError:
                    return _Reservation(False, CODE_USAGE_UNAVAILABLE)
                try:
                    record, ok = self._read_record(self._today())
                    if not ok:
                        return _Reservation(False, CODE_USAGE_UNAVAILABLE)
                    if record['count'] >= self.daily_cap:
                        return _Reservation(False, CODE_DAILY_CAP)
                    record['count'] += 1
                    if not self._write_record(record):
                        return _Reservation(False, CODE_USAGE_UNAVAILABLE)
                    return _Reservation(True, '')
                finally:
                    fcntl.flock(lock_fd, fcntl.LOCK_UN)
            finally:
                os.close(lock_fd)


def _default_serper_open(url, timeout, body, headers, handlers=()):
    """默认传输：POST JSON，禁环境代理、**禁重定向**、限时，返回 (status, body_bytes)。

    handlers 仅供测试注入假 HTTP 处理器（零网络）；默认不含重定向处理器，
    3xx 一律以安全码失败，不会把请求引向其他主机。
    """
    request = urllib.request.Request(url, data=body, headers=dict(headers), method='POST')
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}), _NoRedirectHandler(), *handlers)
    with opener.open(request, timeout=timeout) as response:
        return response.status, response.read(SERPER_MAX_BYTES + 1)


# ── 搜索提供者 ──────────────────────────────────────────────────────────────
class RemioProvider:
    """remio 桌面搜索：复用 assistant_web.WebTools.search，只构建最小 job。

    最小 job 不含模型密钥，也不调用 WebTools.fetch：搜索返回的 markdown 不保证
    含 biz 与合集 HTML，正文一律由注入的 reader 取。
    """

    name = 'remio'

    def __init__(self, tools=None, clock=None):
        self.tools = tools if tools is not None else WebTools()
        self.clock = clock or time.monotonic
        self.attempts = 0

    def capabilities(self):
        installed = bool(self.tools.available())
        return {'provider': self.name, 'installed': installed, 'available': installed,
                'freshness': list(FRESHNESS_VALUES)}

    def search(self, query, count, freshness, cancel=None, deadline=None):
        self.attempts = 0
        if not self.tools.available():
            raise ProviderUnavailable('remio 未安装或不可执行', provider=self.name)
        if _cancelled(cancel):
            raise Cancelled('任务已取消')
        if _expired(self.clock, deadline):
            raise DeadlineExceeded('搜索已超过截止时间')
        # 入口 cancel 桥接进 job；WebTools 只看 job['cancel'] 的真值，不能直接塞 callable。
        job = {'cancel': _CancelBridge(cancel)}
        original_timeout = getattr(self.tools, 'timeout', None)
        if deadline is not None and isinstance(original_timeout, (int, float)) \
                and not isinstance(original_timeout, bool):
            remaining = deadline - self.clock()
            if remaining <= 0:
                raise DeadlineExceeded('搜索已超过截止时间')
            self.tools.timeout = min(original_timeout, remaining)
        self.attempts = 1
        try:
            data = self.tools.search({'query': query, 'count': count, 'freshness': freshness}, job)
        except ValueError as exc:
            raise _classify_remio_error(exc, self.name)
        except OSError:
            raise ProviderUnavailable('无法启动联网服务', provider=self.name)
        finally:
            if isinstance(original_timeout, (int, float)) and not isinstance(original_timeout, bool):
                self.tools.timeout = original_timeout
        results = data.get('results') if isinstance(data, dict) else None
        if not isinstance(results, list):
            raise SearchFailed('联网搜索返回格式异常')
        return {'provider': self.name, 'results': results}


def _classify_remio_error(exc, provider):
    message = str(exc)
    if any(marker in message for marker in _REMIO_CANCEL_MARKERS):
        return Cancelled(message)
    if any(marker in message for marker in _REMIO_UNAVAILABLE_MARKERS):
        return ProviderUnavailable(message, provider=provider)
    if any(marker in message for marker in _REMIO_TIMEOUT_MARKERS):
        return SearchTimeout(message)
    return SearchFailed(message)


class ApiProvider:
    """专用搜索 API：本合同只定义协议与「未配置」状态，不读任何凭据。

    黄总选定服务商后由部署合同提供 searcher；未配置时明确 Unavailable。
    """

    name = 'api'

    def __init__(self, configured=False, searcher=None):
        self.configured = bool(configured) and callable(searcher)
        self.searcher = searcher
        self.attempts = 0

    def capabilities(self):
        return {'provider': self.name, 'installed': self.configured, 'available': self.configured,
                'freshness': list(FRESHNESS_VALUES)}

    def search(self, query, count, freshness, cancel=None, deadline=None):
        self.attempts = 0
        if not self.configured:
            raise ProviderUnavailable('专用搜索 API 尚未配置', provider=self.name)
        if _cancelled(cancel):
            raise Cancelled('任务已取消')
        if _expired(time.monotonic, deadline):
            raise DeadlineExceeded('搜索已超过截止时间')
        self.attempts = 1
        try:
            results = self.searcher(query=query, count=count, freshness=freshness)
        except (ProviderUnavailable, SearchFailed, Cancelled):
            raise
        except Exception as exc:  # 适配器边界的未知异常按硬失败，不静默当不可用
            raise SearchFailed(str(exc) or '专用搜索 API 返回异常')
        if not isinstance(results, list):
            raise SearchFailed('专用搜索 API 返回格式异常')
        return {'provider': self.name, 'results': results}


class SelfHostedProvider:
    """自建 SearXNG（本机回环）搜索：remio 明确不可用后的回退来源，不采购付费 API。

    只允许 http://127.0.0.1:8888 或 http://localhost:8888（拒绝其他主机/协议）；
    请求 `/search?format=json`，只映射 results[].url/title/content。
    连接拒绝/服务没起 → ProviderUnavailable（链路回退到 skipped）；超时 → SearchTimeout
    （failed）；坏 JSON/格式异常 → SearchFailed；上游引擎全被拦且无结果 → ProviderUnavailable。
    """

    name = 'selfhosted'

    def __init__(self, base_url=SELF_SEARCH_DEFAULT_BASE, engines=('baidu', 'sogou'),
                 opener=None, timeout=SELF_SEARCH_TIMEOUT, clock=None):
        self.base_url = _self_search_base(base_url)
        self.engines = tuple(engines or ())
        self.opener = opener or _default_self_search_open
        self.timeout = timeout
        self.clock = clock or time.monotonic
        self.attempts = 0

    def capabilities(self):
        return {'provider': self.name, 'installed': True, 'available': True,
                'freshness': list(FRESHNESS_VALUES), 'base_url': self.base_url}

    def search(self, query, count, freshness, cancel=None, deadline=None):
        self.attempts = 0
        if _cancelled(cancel):
            raise Cancelled('任务已取消')
        if _expired(self.clock, deadline):
            raise DeadlineExceeded('搜索已超过截止时间')
        params = {'q': query, 'format': 'json', 'pageno': '1'}
        if self.engines:
            params['engines'] = ','.join(self.engines)
        time_range = FRESHNESS_TIME_RANGE.get(freshness)
        if time_range:
            params['time_range'] = time_range
        url = self.base_url + '/search?' + urllib.parse.urlencode(params)
        timeout = self.timeout
        if deadline is not None:
            remaining = deadline - self.clock()
            if remaining <= 0:
                raise DeadlineExceeded('搜索已超过截止时间')
            timeout = min(timeout, remaining)
        self.attempts = 1
        try:
            status, raw = self.opener(url, timeout)
        except (socket.timeout, TimeoutError):
            raise SearchTimeout('自建搜索响应超时')
        except (ConnectionRefusedError, ConnectionResetError, ConnectionError):
            raise ProviderUnavailable('自建搜索服务未运行', provider=self.name)
        except urllib.error.HTTPError as exc:
            if exc.code == 429:
                raise ProviderUnavailable('自建搜索上游限流', provider=self.name)
            if 300 <= exc.code < 400:
                # 默认传输禁重定向，3xx 一律当失败，不回显被重定向的地址。
                raise RedirectBlocked('自建搜索拒绝重定向')
            raise SearchFailed(f'自建搜索返回 HTTP {exc.code}')
        except urllib.error.URLError as exc:
            reason = getattr(exc, 'reason', None)
            if isinstance(reason, (socket.timeout, TimeoutError)):
                raise SearchTimeout('自建搜索响应超时')
            if isinstance(reason, (ConnectionRefusedError, ConnectionResetError, ConnectionError)):
                raise ProviderUnavailable('自建搜索服务未运行', provider=self.name)
            raise ProviderUnavailable('自建搜索服务不可达', provider=self.name)
        except OSError:
            raise ProviderUnavailable('自建搜索服务不可达', provider=self.name)

        if isinstance(status, int) and status >= 400:
            if status == 429:
                raise ProviderUnavailable('自建搜索上游限流', provider=self.name)
            raise SearchFailed(f'自建搜索返回 HTTP {status}')
        if not isinstance(raw, (bytes, bytearray)):
            raise SearchFailed('自建搜索返回格式异常')
        if len(raw) > SELF_SEARCH_MAX_BYTES:
            raise SearchFailed('自建搜索返回内容过大')
        try:
            payload = json.loads(bytes(raw).decode('utf-8'))
        except (ValueError, UnicodeError):
            raise SearchFailed('自建搜索返回格式异常')
        if not isinstance(payload, dict):
            raise SearchFailed('自建搜索返回格式异常')
        entries = payload.get('results')
        if not isinstance(entries, list):
            raise SearchFailed('自建搜索返回格式异常')
        if not entries and payload.get('unresponsive_engines'):
            # 配置引擎全部被上游拦截且无结果：视为不可用（skipped），不重试。
            raise ProviderUnavailable('自建搜索上游引擎不可用', provider=self.name)
        limit = count if isinstance(count, int) and not isinstance(count, bool) and count >= 1 \
            else len(entries)
        results = []
        for entry in entries[:limit]:
            if not isinstance(entry, dict):
                continue
            link = entry.get('url')
            if not isinstance(link, str) or not link.strip():
                continue
            results.append({'url': link.strip(),
                            'title': str(entry.get('title') or '')[:MAX_TITLE_CHARS],
                            'description': str(entry.get('content') or '')[:1000]})
        return {'provider': self.name, 'results': results}


class SerperProvider:
    """付费 Serper（Google 搜索 API）：remio 明确不可用后的第二跳。

    只允许 https://google.serper.dev（主机白名单、禁重定向）；密钥只从文件读，
    **不**进日志/异常/回执/repr。每次 search 最多 1 次物理请求、无重试，超时受
    剩余截止约束。401/403 → Unavailable(key_invalid)；429 → Unavailable
    (quota_exhausted)；超每日上限 → Unavailable(daily_cap)；5xx/坏 JSON → SearchFailed。
    """

    name = 'serper'

    def __init__(self, key_file=None, base_url=SERPER_ENDPOINT, opener=None,
                 timeout=SERPER_TIMEOUT, clock=None, daily_cap=SERPER_DAILY_CAP,
                 usage_path=None, usage=None):
        self.endpoint = _serper_endpoint(base_url)
        self._api_key, self._config_error = _load_serper_key(key_file)
        self.opener = opener or _default_serper_open
        self.timeout = timeout
        self.clock = clock or time.monotonic
        self.usage = usage if usage is not None else _SerperUsage(usage_path, daily_cap=daily_cap)
        self.attempts = 0

    def __repr__(self):
        # 显式 repr：绝不把密钥带进任何调试输出。
        state = 'configured' if self._api_key else 'unconfigured'
        return f'<SerperProvider {state} endpoint={self.endpoint}>'

    def capabilities(self):
        available = self._api_key is not None
        caps = {'provider': self.name, 'installed': available, 'available': available,
                'freshness': list(FRESHNESS_VALUES)}
        if not available:
            caps['reason'] = self._config_error
        return caps

    def search(self, query, count, freshness, cancel=None, deadline=None):
        self.attempts = 0
        if _cancelled(cancel):
            raise Cancelled('任务已取消')
        if _expired(self.clock, deadline):
            raise DeadlineExceeded('搜索已超过截止时间')
        if self._api_key is None:
            raise ProviderUnavailable('Serper 搜索尚未配置', provider=self.name,
                                      code=self._config_error)
        reservation = self.usage.reserve()
        if not reservation.allowed:
            if reservation.code == CODE_DAILY_CAP:
                raise ProviderUnavailable('Serper 每日搜索额度已用尽', provider=self.name,
                                          code=CODE_DAILY_CAP)
            raise ProviderUnavailable('Serper 用量记录不可用', provider=self.name,
                                      code=CODE_USAGE_UNAVAILABLE)
        # M3：额度预留（可能阻塞的 I/O）之后、物理请求之前，再按「取消 → 截止」
        # 复查并重算剩余 timeout。取消/过期时不发请求；预留额度保守保留、不回减，
        # 避免以盲目回减引入新的并发缺陷。
        if _cancelled(cancel):
            raise Cancelled('任务已取消（额度已预留，不再发请求）')
        if _expired(self.clock, deadline):
            raise DeadlineExceeded('搜索已超过截止时间（额度已预留，不再发请求）')
        timeout = self.timeout
        if deadline is not None:
            remaining = deadline - self.clock()
            if remaining <= 0:
                raise DeadlineExceeded('搜索已超过截止时间（额度已预留，不再发请求）')
            timeout = min(timeout, remaining)
        body = json.dumps(_serper_request_body(query, count, freshness)).encode('utf-8')
        headers = {'Content-Type': 'application/json', 'Accept': 'application/json',
                   'User-Agent': 'article-studio/1.0', 'X-API-KEY': self._api_key}
        self.attempts = 1
        try:
            status, raw = self.opener(self.endpoint, timeout, body, headers)
        except (socket.timeout, TimeoutError):
            raise SearchTimeout('Serper 响应超时') from None
        except RedirectBlocked:
            raise
        except urllib.error.HTTPError as exc:
            raise _classify_serper_http(exc.code) from None
        except urllib.error.URLError as exc:
            reason = getattr(exc, 'reason', None)
            if isinstance(reason, (socket.timeout, TimeoutError)):
                raise SearchTimeout('Serper 响应超时') from None
            raise ProviderUnavailable('Serper 服务不可达', provider=self.name) from None
        except OSError:
            # from None：清理异常链，避免原始异常文本（可能含敏感内容）进 traceback。
            raise ProviderUnavailable('Serper 服务不可达', provider=self.name) from None

        if isinstance(status, int) and status >= 300:
            # 含 3xx：默认传输已禁重定向，假传输直接回 3xx 也按重定向安全码处理。
            raise _classify_serper_http(status)
        if not isinstance(raw, (bytes, bytearray)):
            raise SearchFailed('Serper 返回格式异常')
        if len(raw) > SERPER_MAX_BYTES:
            raise SearchFailed('Serper 返回内容过大')
        try:
            payload = json.loads(bytes(raw).decode('utf-8'))
        except (ValueError, UnicodeError):
            raise SearchFailed('Serper 返回格式异常')
        if not isinstance(payload, dict):
            raise SearchFailed('Serper 返回格式异常')
        organic = payload.get('organic')
        if organic is None:
            organic = []
        if not isinstance(organic, list):
            raise SearchFailed('Serper 返回格式异常')
        results = []
        for entry in organic:
            if not isinstance(entry, dict):
                continue
            link = entry.get('link')
            if not isinstance(link, str) or not link.strip():
                continue
            # 只把精确 mp.weixin.qq.com 链接交给既有验证流程；其余（含伪域名）丢弃。
            if not _is_mp_link(link.strip()):
                continue
            results.append({'url': link.strip(),
                            'title': str(entry.get('title') or '')[:MAX_TITLE_CHARS],
                            'description': str(entry.get('snippet') or '')[:1000],
                            'date': str(entry.get('date') or '')})
            if len(results) >= SERPER_MAX_NUM:
                break
        return {'provider': self.name, 'results': results}


class ChainProvider:
    """remio 优先；仅当提供者**明确不可用**时回退下一个；超时/格式异常不切换。

    能力检查已知不可用（未安装）时连 search 都不调用；桌面未运行/未配源/额度用完
    由 search 抛 ProviderUnavailable 时回退。任一次成功（哪怕结果为空）即停。
    默认链路：remio → 付费 Serper → 自建 SearXNG。每次调用提供者前（含回退前）
    都查 cancel/deadline。
    """

    name = 'chain'

    def __init__(self, providers=None, clock=None):
        if providers is None:
            providers = [RemioProvider(), SerperProvider(), SelfHostedProvider()]
        self.providers = list(providers)
        self.clock = clock or time.monotonic
        self.attempts = 0

    def capabilities(self):
        return [provider.capabilities() for provider in self.providers]

    def search(self, query, count, freshness, cancel=None, deadline=None):
        self.attempts = 0
        last = None
        for provider in self.providers:
            if _cancelled(cancel):
                raise Cancelled('任务已取消')
            if _expired(self.clock, deadline):
                # 回退前也查截止：过了 deadline 不再启动下一个提供者。
                raise DeadlineExceeded('搜索已超过截止时间')
            caps = provider.capabilities() or {}
            label = caps.get('provider') or getattr(provider, 'name', '')
            if caps.get('available') is False:
                last = ProviderUnavailable(f'{label} 不可用', provider=label)
                continue
            try:
                outcome = provider.search(query, count, freshness, cancel=cancel, deadline=deadline)
            except ProviderUnavailable as exc:
                self.attempts += _provider_attempts(provider, success=False)
                last = exc
                continue
            except (SearchTimeout, SearchFailed, Cancelled):
                self.attempts += _provider_attempts(provider, success=False)
                raise
            self.attempts += _provider_attempts(provider, success=True)
            return outcome
        raise last or ProviderUnavailable('没有可用的搜索提供者')


# ── 候选收集与校验 ──────────────────────────────────────────────────────────
def _collect_candidates(results, identifier, max_candidates, receipt):
    """规范化/去重/声明 biz 校验；逐条写安全码，返回可读候选（带回执槽位）。"""
    accepted, seen = [], set()
    for entry in results:
        slot = len(receipt['candidates'])
        url = _normalize_candidate(entry.get('url')) if isinstance(entry, dict) else None
        if not url:
            receipt['candidates'].append({'code': CODE_REJECTED})
            continue
        declared = _declared_biz(url)
        if declared and declared != identifier:
            receipt['candidates'].append({'code': CODE_DECLARED_CONFLICT})
            continue
        if url in seen:
            receipt['candidates'].append({'code': CODE_DUPLICATE})
            continue
        if len(accepted) >= max_candidates:
            receipt['candidates'].append({'code': CODE_OVER_BUDGET})
            continue
        seen.add(url)
        receipt['candidates'].append({'code': CODE_UNREAD})
        accepted.append({'url': url, 'slot': slot,
                         'title': str(entry.get('title') or '')[:MAX_TITLE_CHARS]})
    return accepted


def _verified_article(candidate, parsed, identifier, account_name):
    raw = parsed.get('raw') if isinstance(parsed.get('raw'), str) else ''
    title = str(parsed.get('title') or candidate.get('title') or '').strip()[:MAX_TITLE_CHARS]
    try:
        published = int(parsed.get('published_at') or 0)
    except (TypeError, ValueError):
        published = 0
    name = str(parsed.get('nickname') or account_name or '').strip()
    text = parsed.get('text')
    text = text if isinstance(text, str) else ('' if text is None else str(text))
    images = parsed.get('images')
    images = [str(item) for item in images] if isinstance(images, list) else []
    try:
        chars = int(parsed.get('chars'))
    except (TypeError, ValueError):
        chars = len(text)
    body = {'text': text, 'chars': chars, 'images': images} if text.strip() else None
    return {
        'link': candidate['url'],
        'title': title,
        'published_at': published,
        'name': name,
        'source': 'discovery',
        'identity': {'fakeid': identifier, 'biz': str(parsed.get('biz') or '').strip(),
                     'msgid': _match(_MSGID, raw), 'itemidx': _match(_ITEMIDX, raw)},
        'body': body,
    }


class DiscoveryResult(dict):
    """发现结果。

    既是**可持久化/展示的回执**（dict，链接已脱敏），又通过 `save_articles` 属性
    携带**保存用结果包**（含完整规范链接与正文，只交给接线方入库）。属性不参与
    `json.dumps`，因此整份回执序列化后不含 chksm 等校验串。
    """

    def __init__(self, *args, save_articles=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.save_articles = list(save_articles or [])


# ── 入口 ────────────────────────────────────────────────────────────────────
def _classify_search_failure(exc):
    """搜索异常 → (state, stop_kind) 的纯分类（不含取消/截止优先级）。"""
    if isinstance(exc, Cancelled):
        return STATE_CANCELLED, STOP_CANCEL
    if isinstance(exc, DeadlineExceeded):
        return STATE_FAILED, STOP_DEADLINE
    if isinstance(exc, ProviderUnavailable):
        return STATE_SKIPPED, STOP_PROVIDER_UNAVAILABLE
    if isinstance(exc, RedirectBlocked):
        return STATE_FAILED, STOP_REDIRECT_BLOCKED
    if isinstance(exc, SearchTimeout):
        return STATE_FAILED, STOP_SEARCH_TIMEOUT
    return STATE_FAILED, STOP_SEARCH_FAILED


def _search_failed(receipt, exc, cancel, clock, deadline, provider):
    """搜索异常收口：先复查取消，再复查截止，最后才按异常分类。"""
    receipt['search_attempted'] = _provider_attempts(provider, success=False)
    if _cancelled(cancel):
        receipt['state'], receipt['stop_kind'] = STATE_CANCELLED, STOP_CANCEL
    elif _expired(clock, deadline):
        receipt['state'], receipt['stop_kind'] = STATE_FAILED, STOP_DEADLINE
    else:
        receipt['state'], receipt['stop_kind'] = _classify_search_failure(exc)
    return receipt


def discover_links(account, provider, reader, budget, cancel=None, deadline=None, clock=None):
    """搜索 → 规范化 → 读文章 → 校验归属 → 取合集，返回纯结果包（不写库）。

    account：{'fakeid', 'name', 'has_album'}；has_album=True 用周范围，否则月范围。
    provider：SearchProvider 协议实现（可用 ChainProvider 串 remio→自建 SearXNG）。
    reader：callable(link) -> 页面解析 dict（B 传 MPClient.fetch_article）。
    budget：{'max_searches','max_candidates','max_reads'}，缺省 1/8/3（硬上限）。
    cancel：callable 或带 is_set() 的对象；deadline：monotonic 截止值；clock：取时函数。
    返回 `DiscoveryResult`：可持久化回执 + `.save_articles`（含完整链接，供入库）。
    """
    clock = clock or time.monotonic
    if hasattr(provider, 'clock'):
        # 入口与提供者共用同一时钟，注入的假时钟也能贯通到回退前的截止检查。
        provider.clock = clock
    identifier = fakeid(_field(account, 'fakeid', ''))
    name = _field(account, 'name', '')
    has_album = bool(_field(account, 'has_album', False))
    query = build_query(name)
    freshness = FRESHNESS_WITH_ALBUM if has_album else FRESHNESS_NO_ALBUM
    limits = _budget(budget)

    receipt = DiscoveryResult({
        'version': RECEIPT_VERSION,
        'provider': '',
        'query': query,
        'freshness': freshness,
        'state': STATE_OK,
        'stop_kind': STOP_NONE,
        'search_attempted': 0,
        'results_found': 0,
        'candidates_accepted': 0,
        'reads_attempted': 0,
        'verified': 0,
        'biz_mismatch': 0,
        'read_failed': 0,
        'budget': limits,
        'album_ids': [],
        'verified_articles': [],
        'candidates': [],
    })

    if _cancelled(cancel):
        receipt['state'] = STATE_CANCELLED
        receipt['stop_kind'] = STOP_CANCEL
        return receipt
    if _expired(clock, deadline):
        receipt['state'] = STATE_FAILED
        receipt['stop_kind'] = STOP_DEADLINE
        return receipt

    try:
        outcome = provider.search(query, limits['max_candidates'], freshness,
                                  cancel=cancel, deadline=deadline)
    except (Cancelled, DeadlineExceeded, ProviderUnavailable, SearchFailed) as exc:
        # 所有搜索异常统一收口，顺序固定：取消 → 截止 → 异常分类。这样最后一个
        # 提供者先置取消再抛不可用（或超时）时，回执仍正确归类为 cancelled/cancel。
        return _search_failed(receipt, exc, cancel, clock, deadline, provider)

    if not isinstance(outcome, dict):
        receipt['state'] = STATE_FAILED
        receipt['stop_kind'] = STOP_SEARCH_FAILED
        return receipt

    receipt['provider'] = str(outcome.get('provider') or '')
    receipt['search_attempted'] = _provider_attempts(provider, success=True)

    # 搜索返回后立刻查 cancel/deadline：过期的成功结果不再进入读取阶段。
    if _cancelled(cancel):
        receipt['state'] = STATE_CANCELLED
        receipt['stop_kind'] = STOP_CANCEL
        return receipt
    if _expired(clock, deadline):
        receipt['state'] = STATE_FAILED
        receipt['stop_kind'] = STOP_DEADLINE
        return receipt

    results = outcome.get('results')
    if not isinstance(results, list):
        # 协议入口统一拒绝坏 results（不是静默当空结果），保留实际 search_attempted。
        receipt['state'] = STATE_FAILED
        receipt['stop_kind'] = STOP_SEARCH_FAILED
        return receipt
    receipt['results_found'] = len(results)

    candidates = _collect_candidates(results, identifier, limits['max_candidates'], receipt)
    receipt['candidates_accepted'] = len(candidates)

    albums, seen_albums, save_articles = [], set(), []
    stop = STOP_NONE
    for candidate in candidates:
        if receipt['reads_attempted'] >= limits['max_reads']:
            stop = STOP_READ_BUDGET
            break
        if _cancelled(cancel):
            stop = STOP_CANCEL
            break
        if _expired(clock, deadline):
            stop = STOP_DEADLINE
            break
        receipt['reads_attempted'] += 1  # 失败/验证码/biz 不符都占一次读取
        try:
            parsed = reader(candidate['url'])
        except Exception:
            parsed = None
        if not isinstance(parsed, dict):
            receipt['read_failed'] += 1
            receipt['candidates'][candidate['slot']] = {'code': CODE_READ_FAILED}
            continue
        page_biz = str(parsed.get('biz') or '').strip()
        if not page_biz or page_biz != identifier:
            receipt['biz_mismatch'] += 1
            receipt['candidates'][candidate['slot']] = {'code': CODE_BIZ_MISMATCH}
            continue
        receipt['verified'] += 1
        receipt['candidates'][candidate['slot']] = {'code': CODE_VERIFIED}
        for album_id in discover_album_ids(parsed.get('raw') or ''):
            if album_id not in seen_albums:
                seen_albums.add(album_id)
                albums.append(album_id)
        article = _verified_article(candidate, parsed, identifier, name)
        save_articles.append(article)  # 保存用：完整规范链接、正文与图片，保持原样
        receipt['verified_articles'].append(_safe_article(article))  # 回执：脱敏且不含正文包

    if stop == STOP_NONE:
        # 最后一次读取后才过截止/取消：收口成 failed/deadline 或 cancelled，不留 ok/none。
        if _cancelled(cancel):
            stop = STOP_CANCEL
        elif _expired(clock, deadline):
            stop = STOP_DEADLINE

    receipt['album_ids'] = albums
    receipt.save_articles = save_articles
    if stop != STOP_NONE:
        receipt['stop_kind'] = stop
        if stop == STOP_CANCEL:
            receipt['state'] = STATE_CANCELLED
        elif stop == STOP_DEADLINE:
            receipt['state'] = STATE_FAILED
    return receipt
