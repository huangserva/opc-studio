"""Metadata for explicitly collected third-party sources. Saving never fetches a URL."""
from datetime import date
import re
from urllib.parse import urlsplit, urlunsplit


CHANNELS = {'copied_link': '复制链接', 'wechat_share': '微信转发', 'email': '邮件',
            'remio': 'remio 收藏', 'chat_shared': '本次聊天提供', 'other': '其他 / 待补'}


def metadata(payload, collected_at):
    allowed = {'title', 'url', 'author', 'author_handle', 'published_on', 'discovered_via',
               'discovery_detail', 'reader_note', 'body', 'tags'}
    if not isinstance(payload, dict) or set(payload) - allowed:
        raise ValueError('外部收藏包含无效字段')
    limits = {'title': 500, 'url': 2048, 'author': 200, 'author_handle': 100, 'published_on': 10,
              'discovered_via': 40, 'discovery_detail': 2000, 'reader_note': 5000, 'body': 100000, 'tags': 500}
    values = {}
    for key, maximum in limits.items():
        value = payload.get(key, '')
        if not isinstance(value, str) or len(value) > maximum or '\x00' in value:
            raise ValueError('收藏字段格式或长度无效：' + key)
        if key == 'url' and any(ord(c) < 32 for c in value):
            raise ValueError('原始链接不能包含控制字符')
        values[key] = value.strip()
    if not values['title'] or not values['url']:
        raise ValueError('请填写文章标题和原始链接')
    try:
        u = urlsplit(values['url'])
        if u.scheme not in ('http', 'https') or not u.hostname or u.username or u.password or any(ord(c) <= 32 for c in values['url']):
            raise ValueError()
        u.port
    except ValueError:
        raise ValueError('原始链接需为有效的 HTTP / HTTPS 网址，不包含账号密码') from None
    canonical = urlunsplit((u.scheme.lower(), u.netloc.lower(), u.path or '/', u.query, ''))
    platform = u.hostname.lower()
    inferred = ''
    match = re.fullmatch(r'/([A-Za-z0-9_]{1,15})/status/(\d+)(?:/(?:photo|video)/\d+)?/?', u.path)
    if platform in ('x.com', 'www.x.com', 'twitter.com', 'www.twitter.com') and match:
        platform = 'X'
        inferred = match[1]
        canonical = 'https://x.com/' + inferred.lower() + '/status/' + match[2]
    published = values['published_on']
    if published:
        try:
            if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', published): raise ValueError()
            date.fromisoformat(published)
        except ValueError:
            raise ValueError('原文发布日期无效；未知请留空') from None
    channel = values['discovered_via'] or 'copied_link'
    if channel not in CHANNELS:
        raise ValueError('收集渠道无效')
    handle = values['author_handle'].lstrip('@') or inferred
    if platform == 'X' and handle and not re.fullmatch(r'[A-Za-z0-9_]{1,15}', handle):
        raise ValueError('X 原作者账号格式无效')
    provenance = {'ownership': 'third_party', 'author': values['author'], 'author_handle': handle,
                  'author_basis': '人工填写' if values['author'] or values['author_handle'] else '来自原帖链接账号，姓名待核对' if inferred else '作者待补',
                  'platform': platform, 'sourceUrl': values['url'], 'canonical_url': canonical,
                  'published_on': published, 'discovered_via': CHANNELS[channel],
                  'discovery_detail': values['discovery_detail'], 'intake_channel': '工作台收藏登记',
                  'captured_at': collected_at, 'reader_note': values['reader_note'],
                  'content_scope': '仅收藏链接；原文正文未保存' if not values['body'] else '人工粘贴的原文片段；未验证为全文'}
    return values, provenance
