"""情报采集 · 公众号第一期：客户端、限流纪律与正文解析。

铁律：
1. 正文只用普通 HTTP GET（桌面 Chrome UA + Accept-Language: zh-CN），不带后台 cookie、
   不用无头浏览器、不用任何自动化特征。普通 HTTP 失败不静默回退到浏览器。
2. 后台接口（searchbiz / appmsg）全局串行，相邻请求间隔 ≥90 秒并叠加随机抖动。
3. 收到 base_resp.ret=200013 立即停；连续 3 次进入 6 小时冷却，界面显示冷却截止时间。
4. 凭证只从 macOS 钥匙串读取，不打印、不写日志、不进任何报告或代码。

方法与实测结论见 coordination/intel-wechat-mp-contract.md。
"""
import contextlib
import contextvars
import html as html_module
import json
import random
import re
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request

# ── 限流纪律（硬性常量，测试与运行共用同一组数字） ──────────────────────────
BACKEND_MIN_INTERVAL = 90.0        # 后台接口相邻请求最小间隔（秒）
BACKEND_JITTER = 30.0              # 叠加的随机抖动上限（秒）
BODY_MIN_INTERVAL = 5.0            # 正文抓取相邻请求最小间隔（秒）
RATE_LIMIT_RET = 200013            # base_resp.ret 命中即限流
COOLDOWN_AFTER_STRIKES = 3         # 连续命中几次进入冷却
COOLDOWN_SECONDS = 6 * 3600        # 冷却时长
MAX_PAGES_PER_RUN = 2              # 每号每次运行最多 2 页
LATEST_PAGE_SIZE = 20              # 「抓最新」1 页，≤20 篇
HISTORY_PAGE_SIZE = 15             # 「抓历史」2 页 × 15 = ≤30 篇
MAX_BODIES_PER_RUN = 3             # 每次运行最多抓 3 篇正文
BACKEND_TIMEOUT = 40
BODY_TIMEOUT = 60
MIN_HTTP_TIMEOUT = 0.01             # 剩余时间再短也不传 0/负数：那是直接报错而非超时
MAX_HTML_BYTES = 24 * 1024 * 1024
MIN_ARTICLE_BYTES = 400  # 低于此长度必然是异常页；真实文章页实测 3.5 MB
MAX_BODY_CHARS = 200_000
KEYCHAIN_SERVICE = 'article-studio.wechat-mp'
BACKEND_HOST = 'https://mp.weixin.qq.com'
ARTICLE_URL = re.compile(r'^https://mp\.weixin\.qq\.com/s(?:/[A-Za-z0-9_-]{6,64}|\?__biz=[^\s&#]+)(?:\?[^\s#]*)?(?:#\S*)?$')
FAKEID = re.compile(r'^M[A-Za-z0-9+/=_-]{5,80}$')

DESKTOP_UA = ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 '
              '(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36')


# ── 请求级超时（R09）─────────────────────────────────────────────────────
# 任务期限（time.monotonic() 基准）跟着**调用上下文**走，绝不替换共享客户端的 opener：
# WechatMpClient 是全进程共享的，换掉它的 opener 会让同一时刻在跑的非任务读
# （identify_from_link、intel_recommend、collect_adapters）继承被压小的超时。
# contextvar 按线程/上下文隔离：只有显式进入 request_deadline(...) 的那一路受影响。
_TASK_DEADLINE = contextvars.ContextVar('intel_http_deadline', default=None)


@contextlib.contextmanager
def request_deadline(deadline):
    """把任务截止时刻带进当前上下文的每一次 HTTP；None / 非法值等于不设限。"""
    value = deadline if isinstance(deadline, (int, float)) and not isinstance(deadline, bool) else None
    token = _TASK_DEADLINE.set(value)
    try:
        yield value
    finally:
        _TASK_DEADLINE.reset(token)


def request_timeout(budget, timeout=None):
    """单次 HTTP 超时 = min(既有预算, 显式 timeout, 剩余期限)。

    只会比预算更小，绝不放宽既有请求预算（BACKEND_TIMEOUT=40 / BODY_TIMEOUT=60）；
    没进任务期限上下文时就是原预算。
    """
    value = float(budget)
    if timeout is not None:
        value = min(value, float(timeout))
    deadline = _TASK_DEADLINE.get()
    if deadline is not None:
        value = min(value, float(deadline) - time.monotonic())
    return max(value, MIN_HTTP_TIMEOUT)


class TaskDeadlinePassed(Exception):
    """任务期限已在等待中耗尽：这一次请求**没有发出去**。

    与 CredentialExpired 严格分开：期限到点是「本轮超时」，不是「需重新登录后台」；
    也不能因为把超时压到 0.01 秒就当成功、返回一份其实没读完的列表（M2）。
    """


def guard_deadline(where=''):
    """真正出网前的最后一道闸：期限已过就不发请求，按超时如实收口。"""
    deadline = _TASK_DEADLINE.get()
    if deadline is None or time.monotonic() < deadline:
        return
    raise TaskDeadlinePassed(f'本轮时限已到，未发送{where or "本次"}请求')


class CredentialsMissing(Exception):
    """钥匙串里没有后台凭证，或已经取不出来。"""


class CredentialExpired(Exception):
    """凭证已失效：登录页，或 base_resp.ret 既非 0 也非 200013。"""


class RateLimited(Exception):
    """命中 200013，或仍在冷却窗口内。不重试。"""

    def __init__(self, message, until=None):
        super().__init__(message)
        self.until = until


def fakeid(value):
    """校验并归一 fakeid；搜索结果必须靠它去重，同名公众号很多。"""
    if not isinstance(value, str):
        raise ValueError('公众号编号格式无效')
    cleaned = value.strip()
    if not FAKEID.fullmatch(cleaned):
        raise ValueError('公众号编号格式无效')
    return cleaned


def article_url(value):
    """校验文章链接。**query 必须原样保留** —— 实测没有 chksm 的
    `/s?__biz=…` 长链会返回「未知错误/失效的验证页」，带 chksm 的才打得开。

    接受两种形态：`/s/<短码>` 与 `/s?__biz=…&mid=…&idx=…&sn=…&chksm=…`。
    片段（#rd 之类）丢掉，query 一个字都不动。

    必需参数一律**显式校验**：缺 mid、空 mid、缺/空 __biz 都是「链接不合格」，
    统一抛可识别的 ValueError。绝不能抛 KeyError —— 保存路径只接 ValueError，
    一条坏链接会变成未预期异常，把整批（整轮任务）一起带崩（R01）。

    判据只看解析出来的原值，**不做 strip 之类的规整**：入库身份是这条链接的指纹，
    旧库里按同一判据存过的行必须被同样的判据再次判成不合格，才不会重采时另建一条
    （`mid=%202650000004%20` 旧判据存成无链接占位、`__biz=%20&mid=…` 旧判据存成带链接）。
    放宽或收紧判据都会改掉旧行的身份键，制造重复入库（M1）。
    """
    message = '请填写 mp.weixin.qq.com/s/ 开头的文章链接'
    if not isinstance(value, str) or not value.strip():
        raise ValueError(message)
    cleaned = value.strip().split('#')[0]
    parts = urllib.parse.urlsplit(cleaned)
    # 合集接口给的是 http:// 链接（实测），统一升到 https 再校验与使用。
    if parts.scheme not in ('http', 'https') or parts.netloc != 'mp.weixin.qq.com':
        raise ValueError(message)
    ok = bool(re.fullmatch(r'/s/[A-Za-z0-9_-]{6,64}', parts.path))
    if not ok and parts.path == '/s' and parts.query:
        # keep_blank_values：`mid=` 这种空值要看得见，不能因为 parse_qs 丢键再炸一次。
        # 值一律用解析原样，不 strip：与旧库落库时的判据逐字一致（M1）。
        query = urllib.parse.parse_qs(parts.query, keep_blank_values=True)
        biz = str(query.get('__biz', [''])[0])
        mid = str(query.get('mid', [''])[0])
        ok = bool(biz) and mid.isdigit()
    if not ok:
        raise ValueError(message)
    return urllib.parse.urlunsplit(('https', parts.netloc, parts.path, parts.query, ''))


# 出网跳转白名单。文章页与合集只走 https。搜狗主机仍允许 http：现有入口是
# https://weixin.sogou.com，但默认 urllib 会跟随该主机上的 http 跳转；合同要求
# 按这个现状放行，并在报告里说明。落到 mp.weixin.qq.com 的一跳仍必须是 https。
PINNED_SCHEMES = {
    'mp.weixin.qq.com': frozenset({'https'}),
    'weixin.sogou.com': frozenset({'http', 'https'}),
}
MAX_PINNED_REDIRECTS = 4
PINNED_URL_MESSAGE = '不允许的跳转目标'


def assert_pinned_url(url):
    """拒绝 ftp、带用户信息或端口的地址，以及白名单以外的主机。不回显目标地址。"""
    if not isinstance(url, str) or not url or '\\' in url or '\n' in url or '\r' in url:
        raise urllib.error.URLError(PINNED_URL_MESSAGE)
    parts = urllib.parse.urlsplit(url.strip())
    host = parts.hostname
    if not host or parts.username or parts.password or parts.port is not None or parts.netloc != host:
        raise urllib.error.URLError(PINNED_URL_MESSAGE)
    allowed = PINNED_SCHEMES.get(host)
    if allowed is None or parts.scheme not in allowed:
        raise urllib.error.URLError(PINNED_URL_MESSAGE)


class PinnedRedirect(urllib.request.HTTPRedirectHandler):
    """每一跳都重新核对主机与协议，并限制跳转次数。"""

    max_redirections = MAX_PINNED_REDIRECTS
    max_repeats = 2

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        try:
            assert_pinned_url(newurl)
        except urllib.error.URLError:
            raise urllib.error.HTTPError(req.full_url, code, PINNED_URL_MESSAGE, headers, fp)
        prepared = super().redirect_request(req, fp, code, msg, headers, newurl)
        if prepared is None:
            return None
        old_host = urllib.parse.urlsplit(req.full_url).hostname
        new_host = urllib.parse.urlsplit(newurl).hostname
        if old_host != new_host:
            # 后台 Cookie 不能跟着跨到另一个允许的主机。
            for key in list(prepared.headers):
                if key.lower() == 'cookie':
                    del prepared.headers[key]
        return prepared

    def http_error_302(self, req, fp, code, msg, headers):
        if 'location' in headers:
            newurl = headers['location']
        elif 'uri' in headers:
            newurl = headers['uri']
        else:
            return None
        try:
            assert_pinned_url(urllib.parse.urljoin(req.full_url, newurl))
        except urllib.error.URLError:
            raise urllib.error.HTTPError(req.full_url, code, PINNED_URL_MESSAGE, headers, fp)
        return super().http_error_302(req, fp, code, msg, headers)

    http_error_301 = http_error_303 = http_error_307 = http_error_308 = http_error_302


def pinned_opener():
    """不用环境代理；用自定义跳转处理替换 urllib 默认的 http/https/ftp 跟随。"""
    return urllib.request.build_opener(urllib.request.ProxyHandler({}), PinnedRedirect)


# ── 凭证：只从 macOS 钥匙串读取 ─────────────────────────────────────────────
class KeychainCredentials:
    """后台 cookie 与 token 存在钥匙串；任何一处取不到就明确报「需重新登录后台」。"""

    def __init__(self, service=KEYCHAIN_SERVICE, runner=subprocess.run):
        self.service = service
        self.runner = runner

    def _read(self, account):
        try:
            result = self.runner(['security', 'find-generic-password', '-s', self.service,
                                  '-a', account, '-w'], capture_output=True, text=True, timeout=15)
        except (OSError, subprocess.SubprocessError) as exc:
            raise CredentialsMissing('需重新登录后台：读取钥匙串失败') from exc
        if result.returncode != 0:
            raise CredentialsMissing('需重新登录后台：钥匙串里没有公众号后台凭证')
        value = (result.stdout or '').strip()
        if not value:
            raise CredentialsMissing('需重新登录后台：钥匙串里的公众号后台凭证为空')
        return value

    def load(self):
        cookie = self._read('cookie')
        token = self._read('token')
        if not re.fullmatch(r'\d{1,12}', token):
            raise CredentialsMissing('需重新登录后台：公众号后台 token 格式无效')
        return cookie, token


# ── 限流器：全局串行 + 抖动 + 冷却状态机 ────────────────────────────────────
class BackendQuota:
    """后台接口的唯一闸门。所有状态经 settings 读写，进程重启后冷却仍然有效。"""

    def __init__(self, settings, now=time.time, sleep=time.sleep, jitter=random.uniform):
        self.settings = settings
        self.now = now
        self.sleep = sleep
        self.jitter = jitter

    # 持久化键
    K_LAST = 'mp.last_request_at'
    K_STRIKE = 'mp.strike_count'
    K_COOLDOWN = 'mp.cooldown_until'

    def _float(self, key):
        try:
            return float(self.settings.get(key, '0') or 0)
        except (TypeError, ValueError):
            return 0.0

    def cooldown_until(self):
        until = self._float(self.K_COOLDOWN)
        return int(until) if until > self.now() else 0

    def strike_count(self):
        return int(self._float(self.K_STRIKE))

    def state(self):
        until = self.cooldown_until()
        return {'cooldown_until': until, 'strikes': self.strike_count(), 'cooling': bool(until)}

    def _wait(self):
        """冷却中直接停，不发请求；否则等到距上次请求满最小间隔，并叠加抖动。"""
        until = self.cooldown_until()
        if until:
            raise RateLimited('接口限流冷却中，本次未发送请求', until=until)
        last = self._float(self.K_LAST)
        if last:
            elapsed = self.now() - last
            gap = (BACKEND_MIN_INTERVAL - elapsed) + self.jitter(0, BACKEND_JITTER)
            if gap > 0:
                self.sleep(gap)

    def record(self, ret):
        """登记一次后台响应。返回是否仍在冷却。"""
        self.settings.set(self.K_LAST, repr(self.now()))
        if ret == RATE_LIMIT_RET:
            strikes = self.strike_count() + 1
            if strikes >= COOLDOWN_AFTER_STRIKES:
                until = int(self.now()) + COOLDOWN_SECONDS
                self.settings.set(self.K_STRIKE, '0')
                self.settings.set(self.K_COOLDOWN, repr(until))
                return until
            self.settings.set(self.K_STRIKE, str(strikes))
        else:
            self.settings.set(self.K_STRIKE, '0')
        return self.cooldown_until()

    def clear_cooldown(self):
        """冷却到期后自然失效。

        这里**只**清冷却截止时间，绝不碰 strike 计数 —— strike 只在一次成功
        的后台响应后由 record(0) 清零。任何只读路径调用本方法都不会削弱
        「连续 3 次触发冷却」这条纪律。
        """
        if not self.cooldown_until():
            self.settings.set(self.K_COOLDOWN, '0')

    def before(self):
        self._wait()

    def after(self, ret):
        return self.record(ret)


class BodyThrottle:
    """正文抓取的独立节流：与后台闸门无关，只保证相邻正文请求 ≥5 秒。"""

    def __init__(self, now=time.time, sleep=time.sleep):
        self.now = now
        self.sleep = sleep
        self.last = 0.0

    def wait(self):
        gap = (self.now() - self.last) - BODY_MIN_INTERVAL
        if gap < 0:
            self.sleep(-gap)
        self.last = self.now()


# ── 正文解析 ────────────────────────────────────────────────────────────────
def _first(*patterns, text):
    for pattern in patterns:
        found = re.search(pattern, text)
        if found and found.group(1).strip():
            return found.group(1).strip()
    return ''


def _balanced_div(document, start):
    """从 #js_content 的起始标签起做 div 配平，避免被嵌套 div 或后续脚本截断。"""
    depth, index = 0, start
    while True:
        opening = document.find('<div', index)
        closing = document.find('</div>', index)
        if closing == -1:
            return document[start:]
        if opening != -1 and opening < closing:
            depth += 1
            index = opening + 4
        else:
            depth -= 1
            index = closing + 6
            if depth == 0:
                return document[start:closing + 6]


def visible_text(fragment):
    """把正文 HTML 变成可读纯文本：去标签、去脚本样式、解实体、压缩空白。"""
    fragment = re.sub(r'<(script|style)[^>]*>.*?</\1>', ' ', fragment, flags=re.S | re.I)
    fragment = re.sub(r'<(br|/p|/section|/div|/h[1-6]|/li)\s*/?>', '\n', fragment, flags=re.I)
    fragment = re.sub(r'<[^>]+>', '', fragment)
    fragment = html_module.unescape(fragment)
    fragment = fragment.replace('\xa0', ' ').replace('\u200b', '').replace('\ufeff', '')
    fragment = re.sub(r'[ \t]+', ' ', fragment)
    return re.sub(r'\n{3,}', '\n\n', re.sub(r'\n\s*\n+', '\n\n', fragment)).strip()


def parse_article(document, url=''):
    """从文章页解析 biz / nickname / msg_title / ct / js_content / mmbiz 图片。"""
    if not isinstance(document, str) or not document.strip():
        raise ValueError('文章页内容为空')
    business = ''
    for found in re.finditer(r'var\s+biz\s*=\s*"([^"]*)"', document):
        if found.group(1).strip():
            business = found.group(1).strip()
            break
    if not business:
        business = _first(r'__biz=([A-Za-z0-9=+/_-]{5,80})', text=document)
    nickname = html_module.unescape(_first(
        r'var\s+nickname\s*=\s*htmlDecode\(\s*"([^"]*)"',
        r'var\s+nickname\s*=\s*"([^"]*)"',
        r'property="og:site_name"[^>]*content="([^"]*)"',
        text=document))
    title = _first(
        r'var\s+msg_title\s*=\s*[\'"]([^\'"]{0,300}?)\'?\s*\.html',
        r'var\s+msg_title\s*=\s*[\'"]([^\'"]{0,300})',
        r'property="og:title"[^>]*content="([^"]*)"',
        r'<h1[^>]*class="rich_media_title[^"]*"[^>]*>(.*?)</h1>',
        text=document)
    title = html_module.unescape(re.sub(r'<[^>]+>', '', title)).strip()
    stamp = _first(r'var\s+ct\s*=\s*"?(\d{9,13})"?', text=document)
    published = int(stamp) if stamp else 0
    match = re.search(r'<div[^>]*\bid="js_content"[^>]*>', document)
    body = ''
    if match:
        body = visible_text(_balanced_div(document, match.start()))[:MAX_BODY_CHARS]
    images, seen = [], set()
    for source in re.findall(r'data-src="(https://mmbiz[^"]+)"', document):
        clean = html_module.unescape(source)
        if clean not in seen:
            seen.add(clean)
            images.append(clean)
    if not business:
        raise ValueError('文章页里没有公众号编号，可能不是公众号文章')
    return {'biz': business, 'nickname': nickname, 'title': title, 'published_at': published,
            'text': body, 'images': images[:50], 'url': url, 'chars': len(body)}


def _open(url, headers, timeout):
    assert_pinned_url(url)
    request = urllib.request.Request(url, headers=headers)
    with pinned_opener().open(request, timeout=timeout) as response:
        return response.read(MAX_HTML_BYTES)


class WechatMpClient:
    """公众号后台 + 文章页的唯一出口。所有出网请求都必须经过这里的限流器。"""

    def __init__(self, credentials, quota, throttle=None, opener=_open):
        self.credentials = credentials
        self.quota = quota
        self.throttle = throttle or BodyThrottle()
        self.opener = opener
        # 最近一次 appmsg_list 里链接不合格的单条：上层据此在回执里报失败条数（M3）。
        self.isolated_items = []

    # ── 后台接口 ──────────────────────────────────────────────────────────
    def _backend(self, path, params, cookie, token, timeout=None):
        self.quota.before()
        query = dict(params, token=token, lang='zh_CN')
        if 'f' not in query:
            query.update(f='json', ajax='1')
        url = f'{BACKEND_HOST}{path}?' + urllib.parse.urlencode(query)
        headers = {'User-Agent': DESKTOP_UA, 'Accept-Language': 'zh-CN,zh;q=0.9',
                   'Accept': 'application/json, text/plain, */*', 'Cookie': cookie,
                   'Referer': f'{BACKEND_HOST}/cgi-bin/home?t=home/index&lang=zh_CN&token={token}',
                   'X-Requested-With': 'XMLHttpRequest'}
        # 出网前的最后一道闸：冷却等待与取凭据都可能把期限耗尽，这时不能只把超时压到
        # 0.01 秒继续发 —— 要么不发（按超时如实收口），要么真的读完（M2）。
        budget = request_timeout(BACKEND_TIMEOUT, timeout)
        guard_deadline('后台')
        try:
            # 请求级超时：后台单次 HTTP 最多 BACKEND_TIMEOUT，在任务期限里再压到剩余时间。
            raw = self.opener(url, headers, budget)
        except (urllib.error.URLError, urllib.error.HTTPError, OSError, ValueError) as exc:
            # 网络失败无法判断 ret，不计入 200013；直接如实报错，不重试。
            raise CredentialExpired('需重新登录后台：公众号后台没有返回可解析结果') from exc
        text = raw.decode('utf-8', 'replace') if isinstance(raw, bytes) else str(raw)
        if 'cgi-bin/loginpage' in text or 'login' in text[:400].lower() and '"ret"' not in text[:400]:
            raise CredentialExpired('需重新登录后台：公众号后台返回登录页')
        try:
            data = json.loads(text)
        except ValueError:
            raise CredentialExpired('需重新登录后台：公众号后台返回的不是 JSON') from None
        if not isinstance(data, dict):
            raise CredentialExpired('需重新登录后台：公众号后台返回格式无效')
        base = data.get('base_resp') or {}
        ret = base.get('ret') if isinstance(base, dict) else None
        ret = ret if isinstance(ret, int) else -1
        until = self.quota.after(ret)
        if ret == RATE_LIMIT_RET:
            raise RateLimited('命中公众号后台限流（200013），已停止本次抓取', until=until or None)
        if ret != 0:
            raise CredentialExpired(f'需重新登录后台：公众号后台返回 ret={ret}')
        return data

    def credentials_ready(self):
        """探活：只确认凭证取得到，不发任何后台请求。缺失/失效如实抛出。"""
        return self.credentials.load()

    def _credentials(self):
        # 缺失与失效都从 load() 直接抛出，调用方不吞、不重试。
        return self.credentials.load()

    def search_biz(self, query):
        """号名 → 候选 fakeid。结果交给用户选，绝不自动认第一个。"""
        if not isinstance(query, str) or not query.strip() or len(query) > 50:
            raise ValueError('请填写公众号名称（≤50 字）')
        cookie, token = self._credentials()
        data = self._backend('/cgi-bin/searchbiz',
                             {'action': 'search_biz', 'begin': 0, 'count': 5, 'query': query.strip()}, cookie, token)
        found, seen = [], set()
        for item in data.get('list') or []:
            if not isinstance(item, dict):
                continue
            identifier = item.get('fakeid')
            if not isinstance(identifier, str) or not FAKEID.fullmatch(identifier.strip()):
                continue
            identifier = identifier.strip()
            if identifier in seen:
                continue
            seen.add(identifier)
            found.append({'fakeid': identifier,
                          'nickname': html_module.unescape(str(item.get('nickname') or '')).strip(),
                          'signature': html_module.unescape(str(item.get('signature') or '')).strip()[:120],
                          'round_head_img': str(item.get('round_head_img') or '')})
        return found

    def appmsg_list(self, identifier, begin, count):
        """fakeid → 文章列表。list_ex 不可用时回退 appmsgpublish（publish_page 需二次解析）。

        链接不合格的单条只隔离这一条，不拖垮整批；被隔离的单条放在
        ``client.isolated_items``（每次调用先清空），供上层在回执里报出失败条数。
        返回的列表仍只有可用条目，既有调用方签名与内容不变（M3）。
        """
        self.isolated_items = []
        cookie, token = self._credentials()
        data = self._backend('/cgi-bin/appmsg',
                             {'action': 'list_ex', 'fakeid': identifier, 'begin': int(begin),
                              'count': int(count), 'type': 9, 'query': ''}, cookie, token)
        raw = data.get('app_msg_list')
        if raw is None:
            raw = data.get('publish_page')
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except ValueError:
                raw = []
        items = (raw or {}).get('publish_list') if isinstance(raw, dict) else raw
        result = []
        for item in items or []:
            if not isinstance(item, dict):
                continue
            raw_link = str(item.get('link') or item.get('appmsg_url') or '').strip()
            # 只去掉 #rd 之类的前端片段；**query 必须保留** —— 实测没有 chksm 的
            # 长链会被判「未知错误/失效的验证页」，抓不到正文。
            try:
                link = article_url(raw_link)
            except ValueError as exc:
                # 坏单条只隔离这一条，不拖垮整批；但必须**留下来**：上层要能报出
                # 「另 N 条链接不合格已隔离」，不能静默消失（M3）。
                title = html_module.unescape(str(item.get('title') or '')).strip()
                self.isolated_items.append({'title': title, 'reason': str(exc),
                                            'link': raw_link[:200]})
                continue
            stamp = item.get('create_time') or item.get('update_time') or 0
            result.append({'title': html_module.unescape(str(item.get('title') or '')).strip(),
                           'link': link, 'published_at': int(stamp) if str(stamp).isdigit() else 0,
                           'aid': str(item.get('aid') or item.get('appmsgid') or '').strip()})
        return result

    # ── 正文（普通 HTTP，无 cookie） ─────────────────────────────────────
    def fetch_article(self, url, timeout=None):
        url = article_url(url)
        self.throttle.wait()
        headers = {'User-Agent': DESKTOP_UA, 'Accept-Language': 'zh-CN,zh;q=0.9',
                   'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8'}
        # 节流等待同样可能跨过截止：期限已到就不发这次正文请求，按超时如实收口（M2）。
        budget = request_timeout(BODY_TIMEOUT, timeout)
        guard_deadline('正文')
        try:
            # 请求级超时：不共享替换 opener，只算这一次该用多久。
            raw = self.opener(url, headers, budget)
        except (urllib.error.URLError, urllib.error.HTTPError, OSError, ValueError) as exc:
            raise ValueError('文章页抓取失败：普通 HTTP 请求没有拿到页面') from exc
        document = raw.decode('utf-8', 'replace') if isinstance(raw, bytes) else str(raw)
        # 拦截环境异常页/验证页：它们很短，且没有公众号文章结构。
        if len(document) < MIN_ARTICLE_BYTES or 'js_content' not in document:
            raise ValueError('文章页内容异常，未取到正文')
        parsed = parse_article(document, url)
        # 原文交给调用方用于发现合集（album_id），不进数据库。
        parsed['raw'] = document
        return parsed