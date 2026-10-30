"""linux.do 公开 RSS：限量抓取、每小时后台刷新、失败保留上次快照。"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import subprocess
import re
import threading
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html import escape
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import getproxies

from html_sanitize import SafeHTML, clean_html_full

RSS_URL = 'https://linux.do/tag/%E4%BA%BA%E5%B7%A5%E6%99%BA%E8%83%BD.rss'
# 实测 /c/ai/111.rss 为 404；开发调优包含非 AI，作为候选版块仍须统一闸门。
RSS_FEEDS = (('AI 标签', RSS_URL), ('开发调优', 'https://linux.do/c/develop/4.rss'),
             ('日榜', 'https://linux.do/top.rss?period=daily'), ('周榜', 'https://linux.do/top.rss?period=weekly'))
MAX_BYTES = 2 * 1024 * 1024
TIMEOUT = 10
REFRESH_SECONDS = 3600
RETRY_SECONDS = 600
FEED_VERSION = 3
_LOCK = threading.Lock()
_PENDING = set()


def directory(root):
    root = Path(root)
    return (root / 'workspace' if (root / 'workspace').is_dir() else root) / 'linuxdo'


def _read(path):
    try:
        if path.stat().st_size > MAX_BYTES * len(RSS_FEEDS) * 2:
            return {}
        value = json.loads(path.read_text(encoding='utf-8'))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError, UnicodeError):
        return {}


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False), encoding='utf-8')
    temp.replace(path)


def topic_url(value):
    try:
        url = urlsplit(value)
        if url.scheme == 'https' and url.netloc == 'linux.do' and re.fullmatch(r'/t/(?:[^/]+/)?[0-9]+/?', url.path) and not url.query and not url.fragment:
            return value.rstrip('/')
    except ValueError:
        pass
    return None


class _RSSHTML(SafeHTML):
    # 只取清洗后的纯文本；提供宿主要求的字面量转义钩子，无 Markdown 重解释。
    def _escape_data(self, data, at_line_start=False):
        return escape(data)

    def handle_starttag(self, tag, attrs):
        # RSS 统计段前可能紧接内联链接，仍须保留段落边界。
        if tag == 'p' and not self.blocked and self.text and not self.text[-1].endswith('\n'):
            self.text.append('\n')
        super().handle_starttag(tag, attrs)


def parse_rss(body):
    if len(body) > MAX_BYTES:
        raise ValueError('响应超过上限')
    if re.search(br'<!\s*(?:DOCTYPE|ENTITY)', body, re.I):
        raise ValueError('不接受 XML 实体声明')
    root = ET.fromstring(body)
    if root.tag != 'rss' or root.find('channel') is None:
        raise ValueError('不是 RSS，可能是挑战页')
    items = {}
    for node in root.findall('./channel/item')[:100]:
        url = topic_url(node.findtext('link', ''))
        if not url or url in items:
            continue
        title = node.findtext('title', '').strip()
        if not title:
            continue
        # Discourse 的置顶标记实际在扩展命名空间；不凭标题推断置顶。
        if any((node.findtext(k, '').strip().lower() in ('true', '1')) for k in ('{http://www.discourse.org/}topicPinned', 'pinned', 'pinned_globally')):
            continue
        published = parsedate_to_datetime(node.findtext('pubDate', ''))
        published = published.replace(tzinfo=timezone.utc) if published.tzinfo is None else published
        _, plain, _, _ = clean_html_full(node.findtext('description', ''), _RSSHTML)
        # Discourse 的阅读入口和统计独占尾行；正文中的帖子数字不是回复统计。
        plain = re.sub(r'(?:^|\n)\s*阅读完整话题\s*\Z', '', plain).rstrip()
        posts = re.search(r'(?:^|\n)[ \t]*(\d+)[ \t]*个帖子[ \t]*-[ \t]*\d+[ \t]*位参与者\s*\Z', plain)
        if posts:
            plain = plain[:posts.start()].rstrip()
        identifier = 'url:' + hashlib.sha256(url.encode()).hexdigest()[:16]
        items[url] = dict(id=identifier, kind='article', sources=['L'],
                          title=title[:120], full_text=plain, url=url,
                          author=dict(name=node.findtext('{http://purl.org/dc/elements/1.1/}creator', '').strip(), handle='', followers=None),
                          published_at=published.astimezone(timezone.utc).isoformat(), topic=node.findtext('category', '').strip(),
                          metrics=dict(views=None, replies=max(0, int(posts[1]) - 1) if posts else None),
                          tier='其他', fit_score=None, doable=None, content_complete=True,
                          why='', pattern='', my_angle='', outline='', doable_resource='', rich_sources=[], status='new', feedback=None)
        items[url]['topic_id'] = url.rstrip('/').rsplit('/', 1)[-1]
    if not items:
        raise ValueError('RSS 没有有效主题')
    return list(items.values())


def fetch_rss(url=None):
    # -q 禁止 curlrc 注入 Cookie/身份头；不跟随重定向，不登录。
    # 本机 curl 8.7 支持未知长度响应的流式 max-filesize 上限。
    # 复用无凭证系统代理，避免绕过本机正常网络配置；代理口令不会进子进程参数。
    proxies = getproxies()
    proxy = proxies.get('https') or proxies.get('all')
    if proxy:
        parsed = urlsplit(proxy)
        if parsed.username or parsed.password or parsed.scheme not in ('http', 'https', 'socks5', 'socks5h') or not parsed.hostname or re.search(r'[\s\x00-\x1f]', proxy):
            raise ValueError('不支持的代理配置')
    env = {k: v for k, v in os.environ.items() if k.lower() not in ('http_proxy', 'https_proxy', 'all_proxy', 'no_proxy')}
    command = ['curl', '-q', '--silent', '--fail', '--max-time', '9.5', '--max-filesize', str(MAX_BYTES),
               '--proto', '=https', '--max-redirs', '0', '--user-agent', 'ArticleStudio-RSS/1.0',
               '--header', 'Accept: application/rss+xml, application/xml', '--write-out', '\n%{http_code}']
    command += ['--proxy', proxy] if proxy else ['--noproxy', '*']
    url = url or RSS_URL
    command.append(url)
    try:
        result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=env, timeout=TIMEOUT, check=False)
    except subprocess.TimeoutExpired:
        raise TimeoutError('来源超时') from None
    body, _, status = result.stdout.rpartition(b'\n')
    if result.returncode == 28:
        raise TimeoutError('来源超时')
    if result.returncode == 63 or len(body) > MAX_BYTES:
        raise ValueError('响应超过上限')
    if status.isdigit() and 400 <= int(status) <= 599:
        raise HTTPError(url, int(status), '来源响应异常', {}, None)
    if result.returncode or status != b'200':
        raise ValueError('来源响应异常')
    return parse_rss(body)


def fetch_feeds():
    merged, errors = {}, []
    for label, url in RSS_FEEDS:
        try:
            for item in fetch_rss(url):
                key = item['topic_id']
                if key in merged:
                    merged[key]['rss_boards'].append(label)
                else:
                    merged[key] = dict(item, rss_boards=[label])
        except Exception as exc:
            errors.append(label + '：' + ('HTTP ' + str(exc.code) if isinstance(exc, HTTPError) else 'RSS 抓取或解析失败'))
    if not merged:
        raise ValueError('全部 RSS 不可用')
    return list(merged.values()), errors


def _epoch(value):
    try:
        return datetime.fromisoformat(value).timestamp()
    except (ValueError, TypeError):
        return 0


def _refresh_interval(state):
    # 成功按一小时；失败或上次刷新中断按十分钟退避。
    if state.get('feed_version') != FEED_VERSION:
        return 0  # 单源快照升级为多源时，首次后台刷新不沿用旧冷却。
    return REFRESH_SECONDS if state.get('refreshing') is False and not state.get('error') else RETRY_SECONDS


def refresh(root, now=None):
    """同步刷新仅供后台线程/离线测试调用；跨进程锁覆盖小时节流。"""
    folder = directory(root)
    folder.mkdir(parents=True, exist_ok=True)
    with (folder / '.refresh.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        now = now or datetime.now(timezone.utc)
        state = _read(folder / 'status.json')
        if now.timestamp() - _epoch(state.get('attempted_at')) < _refresh_interval(state):
            return
        state = dict(attempted_at=now.isoformat(), error=None, refreshing=True, feed_version=FEED_VERSION)
        _write(folder / 'status.json', state)
        try:
            items, errors = fetch_feeds()
            previous = _read(folder / 'top-daily.json')
            old = {p.get('topic_id') or p['url'].rstrip('/').rsplit('/', 1)[-1]: p for p in previous.get('items', [])}
            for item in items:
                item['fetched_at'] = now.isoformat()
                before = old.get(item['topic_id'])
                a, b = item['metrics'].get('replies'), (before or {}).get('metrics', {}).get('replies')
                delta = a - b if a is not None and b is not None else None
                item['reply_delta'] = delta
                item['reply_change'] = '新' if before is None else '—' if delta in (None, 0) else ('↑+' + str(delta) if delta > 0 else '↓' + str(delta))
            if previous:
                _write(folder / 'previous.json', previous)
            _write(folder / 'top-daily.json', dict(updated_at=now.isoformat(), items=items))
            state['feed_errors'] = errors
            # 部分源失败保留有效结果，但十分钟后重试失败源。
            state['error'] = '；'.join(errors) or None
        except Exception as exc:
            # 不记录异常原文、响应体或请求头，避免凭据/挑战页进入快照与日志。
            state['error'] = ('HTTP ' + str(exc.code)) if isinstance(exc, HTTPError) else '来源超时' if isinstance(exc, TimeoutError) else 'RSS 抓取或解析失败'
        finally:
            state['refreshing'] = False
            _write(folder / 'status.json', state)


def refresh_background(root):
    folder = directory(root).resolve()
    state = _read(folder / 'status.json')
    if time.time() - _epoch(state.get('attempted_at')) < _refresh_interval(state):
        return
    with _LOCK:
        if folder in _PENDING:
            return
        _PENDING.add(folder)
    def run():
        try:
            refresh(root)
        finally:
            with _LOCK:
                _PENDING.discard(folder)
    threading.Thread(target=run, daemon=True, name='linuxdo-rss').start()


def snapshot(root):
    folder = directory(root)
    data = _read(folder / 'top-daily.json')
    state = _read(folder / 'status.json')
    with _LOCK:
        pending = folder.resolve() in _PENDING
    refreshing = pending or bool(state.get('refreshing') and time.time() - _epoch(state.get('attempted_at')) < len(RSS_FEEDS) * TIMEOUT + 2)
    items = data.get('items', [])
    if not isinstance(items, list):
        items = []
    return items, dict(updated_at=data.get('updated_at'), error=state.get('error') or (None if items or refreshing else '尚无快照，等待后台刷新'), refreshing=refreshing)
