"""情报采集 · 三期：免后台的两条取文章路径。

背景：后台 appmsg list_ex 持续 200013。下面两条路**不需要任何登录凭证**。

1. **合集接口（主路）**：文章页 HTML 里带 album_id，用它翻合集。
   `GET /mp/appmsgalbum?action=getalbum&__biz=<fakeid>&album_id=<id>&count=10&f=json`
   实测卡兹克「产品推荐」ret=0、77 篇、continue_flag=1，可翻页。
   成功时 ret 在 **base_resp.ret**（不是 getalbum_resp.ret）；失败只有 base_resp。

2. **搜狗微信搜索（补充最新）**：只拿标题、账号名与时间。
   **实测 `/link` 跳转必定跳 antispider**，所以搜狗给不出可用的 mp 链接。
   这里如实把这类发现标成 link_blocked，绝不编造链接。

限流：公开接口相邻请求 ≥5 秒加抖动；每轮合集 ≤10 次、搜狗 ≤3 次；搜狗撞验证码立即停并冷却 6 小时。
"""
import html as html_module
import json
import random
import re
import time
import urllib.error
import urllib.parse

from intel_wechat_mp import DESKTOP_UA, _open as pinned_open

ALBUM_HOST = 'https://mp.weixin.qq.com'
SOGOU_HOST = 'https://weixin.sogou.com'
ALBUM_REQUEST_BUDGET = 10        # 每轮合集接口请求上限
SOGOU_REQUEST_BUDGET = 3         # 每轮搜狗请求上限（1 次搜索 + 最多 2 次跳转）
PUBLIC_MIN_INTERVAL = 5.0        # 公开接口相邻请求最小间隔
PUBLIC_JITTER = 2.0
SOGOU_COOLDOWN_SECONDS = 6 * 3600
ALBUM_PAGE_SIZE = 10
ALBUM_URL = re.compile(r'^https?://mp\.weixin\.qq\.com/s\?__biz=[^&]+&mid=\d+(&|$)')
MAX_RESPONSE = 24 * 1024 * 1024

# 文章页里 album_id 的几种出现形态（实测新版页面走 window.cgiDataNew）
ALBUM_ID_PATTERNS = (
    r'album_id\s*[=:]\s*["\']?(\d{8,30})',
    r'appmsgalbum\?[^\'"]*album_id=(\d{8,30})',
    r'"album_id"\s*:\s*"?(\d{8,30})',
)
SOGOU_ACCOUNT = re.compile(r'<span class="all-time-y2">(.*?)</span>', re.S)
SOGOU_LINK = re.compile(r'<h3>\s*<a[^>]+href="(/link\?url=[^"]+)"[^>]*>(.*?)</a>', re.S)
SOGOU_TIME = re.compile(r"timeConvert\('(\d{10,13})'\)")
SOGOU_BLOCKED = ('antispider', '验证码', '请输入验证码', '用户您好，您的访问过于频繁')


class SogouBlocked(Exception):
    """撞上搜狗反爬：立即停本轮并冷却。"""


class AlbumError(Exception):
    """合集接口没有返回可用的文章。"""


class PublicThrottle:
    """公开接口的独立节流：与后台闸门无关，保证相邻请求 ≥5 秒并叠加抖动。"""

    def __init__(self, now=time.monotonic, sleep=time.sleep, jitter=random.uniform):
        self.now = now
        self.sleep = sleep
        self.jitter = jitter
        self.last = None
        self.calls = 0

    def begin_round(self):
        """重置单轮尝试数，last 保留以保证跨轮请求仍相隔至少五秒。"""
        self.calls = 0

    def wait(self):
        # 第一次请求不必等：没有「上一次」可依。
        if self.last is not None:
            gap = PUBLIC_MIN_INTERVAL - (self.now() - self.last) + self.jitter(0, PUBLIC_JITTER)
            if gap > 0:
                self.sleep(gap)
        self.last = self.now()
        self.calls += 1


def _open(url, headers, timeout):
    """合集接口与搜狗 /link 走同一套跳转白名单，读数上限仍是公开接口自己的。"""
    return pinned_open(url, headers, timeout)[:MAX_RESPONSE]


def discover_album_ids(document):
    """从文章页 HTML 里找出一篇可能属于多个的 album_id。"""
    if not isinstance(document, str) or not document:
        return []
    found = []
    for pattern in ALBUM_ID_PATTERNS:
        for value in re.findall(pattern, document):
            if value not in found:
                found.append(value)
    return found[:8]


class PublicClient:
    """合集 + 搜狗。所有出网都经过 PublicThrottle，并受每轮预算约束。"""

    def __init__(self, throttle=None, opener=_open, album_budget=ALBUM_REQUEST_BUDGET,
                 sogou_budget=SOGOU_REQUEST_BUDGET):
        self.throttle = throttle or PublicThrottle()
        self.opener = opener
        self.album_budget = album_budget
        self.sogou_budget = sogou_budget

    def begin_round(self):
        self.throttle.begin_round()

    # ── 合集 ──────────────────────────────────────────────────────────────
    def album_page(self, identifier, album_id, begin_msgid=None, begin_itemidx=None):
        """取一页合集。预算耗尽直接停，不发请求。"""
        if self.throttle.calls >= self.album_budget + self.sogou_budget:
            raise AlbumError('本轮公开接口请求预算已用完')
        params = {'action': 'getalbum', '__biz': identifier, 'album_id': str(album_id),
                  'count': ALBUM_PAGE_SIZE, 'f': 'json'}
        if begin_msgid and begin_itemidx not in (None, ''):
            params.update(begin_msgid=begin_msgid, begin_itemidx=begin_itemidx)
        url = f'{ALBUM_HOST}/mp/appmsgalbum?' + urllib.parse.urlencode(params)
        self.throttle.wait()
        raw = self.opener(url, self._headers(), 40)
        text = raw.decode('utf-8', 'replace') if isinstance(raw, bytes) else str(raw)
        try:
            data = json.loads(text)
        except ValueError:
            raise AlbumError('合集接口返回的不是 JSON') from None
        if not isinstance(data, dict):
            raise AlbumError('合集接口返回格式无效')
        # 成功时 ret 在 base_resp；失败只有 base_resp（实测不存在的合集 ret=10004）
        base = data.get('base_resp') if isinstance(data.get('base_resp'), dict) else {}
        ret = base.get('ret')
        ret = ret if isinstance(ret, int) else 0
        if ret != 0:
            raise AlbumError(f'合集接口返回 ret={ret}')
        body = data.get('getalbum_resp')
        if not isinstance(body, dict):
            raise AlbumError('合集接口没有返回文章列表')
        items = []
        for entry in body.get('article_list') or []:
            if not isinstance(entry, dict):
                continue
            link = str(entry.get('url') or '').split('#')[0]
            if not link:
                continue  # 没有链接的条目用不了，正文也抓不到
            stamp = entry.get('create_time')
            items.append({'title': html_module.unescape(str(entry.get('title') or '')).strip(),
                          'link': link,
                          'published_at': int(stamp) if str(stamp).isdigit() else 0,
                          'msgid': str(entry.get('msgid') or ''),
                          'itemidx': str(entry.get('itemidx') or '')})
        info = body.get('base_info') if isinstance(body.get('base_info'), dict) else {}
        return {'title': str(info.get('title') or ''),
                'article_count': int(info.get('article_count') or 0) if str(info.get('article_count') or '').isdigit() else 0,
                'nickname': html_module.unescape(str(info.get('nickname') or '')).strip(),
                'items': items,
                'continue_flag': str(body.get('continue_flag') or '0') == '1'}

    # ── 搜狗 ──────────────────────────────────────────────────────────────
    def sogou_search(self, query):
        """只拿标题、账号名与时间。撞验证码立即停并抛 SogouBlocked。"""
        url = f'{SOGOU_HOST}/weixin?' + urllib.parse.urlencode({'type': 2, 'query': query, 'ie': 'utf8'})
        self.throttle.wait()
        raw = self.opener(url, self._headers(sogou=True), 40)
        document = raw.decode('utf-8', 'replace') if isinstance(raw, bytes) else str(raw)
        if self._blocked(document):
            raise SogouBlocked('搜狗要求验证码，已停止本轮并进入冷却')
        results = []
        for block in re.findall(r'<div class="txt-box">(.*?)(?=<div class="txt-box">|</li>)', document, re.S):
            link = SOGOU_LINK.search(block)
            who = SOGOU_ACCOUNT.search(block)
            when = SOGOU_TIME.search(block)
            if not link or not who:
                continue
            results.append({
                'account': html_module.unescape(re.sub(r'<[^>]+>', '', who.group(1))).strip(),
                'title': html_module.unescape(re.sub(r'<[^>]+>', '', link.group(2))).strip(),
                'published_at': int(when.group(1)) if when else 0,
                'sogou_link': f'{SOGOU_HOST}{html_module.unescape(link.group(1))}'})
        return results

    def sogou_resolve(self, sogou_link):
        """打开搜狗跳转链接取真实 mp 链接。**实测必定跳 antispider**，
        撞上就抛 SogouBlocked 让上层立刻停并冷却。"""
        self.throttle.wait()
        try:
            raw = self.opener(sogou_link, self._headers(sogou=True, referer=f'{SOGOU_HOST}/'), 40)
        except (urllib.error.URLError, urllib.error.HTTPError, OSError):
            return ''  # 网络层失败不当作验证码，如实返回「没拿到」
        document = raw.decode('utf-8', 'replace') if isinstance(raw, bytes) else str(raw)
        if self._blocked(document):
            raise SogouBlocked('搜狗跳转要求验证码，已停止本轮并进入冷却')
        found = re.search(r'(https?://mp\.weixin\.qq\.com/s[^\s"\'<>]+)', document)
        return html_module.unescape(found.group(1)) if found else ''

    @staticmethod
    def _blocked(document):
        low = document.lower()
        return any(marker in document or marker in low for marker in SOGOU_BLOCKED)

    @staticmethod
    def _headers(sogou=False, referer=''):
        headers = {'User-Agent': DESKTOP_UA, 'Accept-Language': 'zh-CN,zh;q=0.9',
                   'Accept': 'application/json, text/plain, */*' if not sogou else 'text/html,application/xhtml+xml'}
        if referer:
            headers['Referer'] = referer
        return headers


def is_album_link(link):
    """合集接口给的是 /s?__biz=… 长链，带 chksm，必须原样保留。"""
    return bool(ALBUM_URL.match(link or ''))