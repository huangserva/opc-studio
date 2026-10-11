"""Metadata for explicitly collected third-party sources. Saving never fetches a URL."""
from datetime import date
import re
from urllib.parse import urlsplit, urlunsplit

from collect_links import identify as identify_platform


CHANNELS = {'copied_link': '复制链接', 'wechat_share': '微信转发', 'email': '邮件',
            'remio': 'remio 收藏', 'chat_shared': '本次聊天提供', 'other': '其他 / 待补'}

# 旧库主键衔接（只读，不迁移、不回填）。
# 接入平台识别之前，外部收藏的去重键就是 host+path+query；`knowledge_store.add_reference`
# 拿 `provenance['canonical_url']` 生成 documents 主键，所以三家长链接换成 dedupe_url 后，
# 同一链接再收一次会多出一篇。下面的函数只把旧口径的编号算出来、并到库里查一遍，让所有
# 收藏入口（回收站 intake_store、知识库页面 POST /api/knowledge/reference）都能查到并复用
# 旧那篇；不改 knowledge_store，也不动旧库里已有的数据。
LEGACY_SOURCE = 'external-references'


def legacy_canonical(url):
    """旧口径的 canonical：scheme + host + path + query 原样，只丢 fragment。

    口径照抄识别接入之前那一段：`urlunsplit((scheme.lower(), netloc.lower(), path or '/', query, ''))`，
    并且只接受当时能过校验的地址（http/https、有主机名、不带账号密码、端口能解析）。
    """
    try:
        parts = urlsplit(str(url or ''))
        if not parts.hostname or parts.username or parts.password:
            return ''
        parts.port
    except ValueError:
        return ''
    if parts.scheme.lower() not in ('http', 'https'):
        return ''
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path or '/', parts.query, ''))


def legacy_document_id(url):
    """旧口径那篇文档的编号；地址无效时返回 ''。编号算法与知识库写入时一致。"""
    key = legacy_canonical(url)
    if not key:
        return ''
    from knowledge_store import identifier
    return identifier(LEGACY_SOURCE, key)


def existing_legacy_document(url, knowledge):
    """旧口径（host+path+query）已经登记过这篇吗？登记过就返回那篇的编号，否则 ''。

    所有收藏入口（回收站 `intake_store`、知识库页面 `POST /api/knowledge/reference`）都调
    这一个，免得入口之间口径不一致：接入平台识别后三家长链接的去重键换成了 dedupe_url，
    旧库存的还是旧口径，不衔接就会把同一链接再收一次时多出一篇。

    只查不写：只走知识库的公开读接口（`document_hashes` 只取 id 与正文指纹，不读正文），
    不迁移、不回填旧库。只有识别到三家可去重的长链接（`dedupe_url` 非空）才查；短链识别
    不出可去重的长链接，不带 query 的三家长链接新旧口径又本来就是同一个键，这两种情况
    `add_reference` 自己的去重已经覆盖。
    """
    found = identify_platform(url)
    if not found or not found['dedupe_url']:
        return ''
    candidate = legacy_document_id(url)
    if not candidate:
        return ''
    return candidate if knowledge.document_hashes([candidate]).get(candidate) else ''


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
    # 小红书 / 抖音 / 视频号：认平台与作品编号。canonical 只当去重键用（knowledge_store
    # 就拿它当 documents 主键），所以三家一律换成不带 token / 跟踪参数的 dedupe_url：
    # 同一篇小红书笔记换 xsec_token 再贴一次，知识库里只会有一条正文。能打开的原样
    # 链接仍在 sourceUrl，带 token 的规范链接另存 share_url。
    found = identify_platform(values['url'])
    if found:
        platform = found['label']
        if found['dedupe_url']:
            canonical = found['dedupe_url']
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
    if found:
        # 只补新字段，不改已有字段的语义；xsec_token 在 share_url 与 sourceUrl 里都留着。
        provenance['source_item_id'] = found['item_id']
        provenance['short_link'] = found['short_link']
        provenance['share_url'] = found['canonical_url'] or values['url']
        provenance['platform_needs_login'] = found['needs_login']
    return values, provenance
