"""小红书 / 抖音 / 视频号链接识别：纯函数，不联网、不解析短链、不读账号。

依据 `coordination/research-social-collect-report.md` §4.2。只做四件事：

1. 认出这是**哪个平台**（小红书 / 抖音 / 视频号），不是三家就返回 ``None``，不误判。
2. 认出是**哪条作品或哪个账号**（笔记 id、视频 id、sec_uid、视频号 sph id）。
3. 给出三个口径：
   - ``canonical_url``：能打开的规范链接。小红书**保留 ``xsec_token``**（去掉就打不开），
     抖音与视频号去掉跟踪参数。
   - ``dedupe_url``：去 token、去跟踪参数的规范地址，**只用作品编号**。同一篇小红书
     笔记换 ``xsec_token`` 再贴一次、或从 ``/explore/`` 换成 ``/discovery/item/``，
     这个地址不变，知识库里只会有一条正文（``knowledge_store`` 就拿它当去重键）。
   - ``dedupe_key``：同一口径的逻辑键（``平台:类型:编号``），给人看、给测试用。
4. 说清本机**为什么拿不到正文**（``body_note``），以及抓回来的文字能不能算正文
   （``body_not_read``：登录墙与空壳文字一律不算正文）。

明确不做：不调任何网络、不解析短链（短链只标 ``short_link=True``）、不读 cookie 或
账号、不抓任何平台页面、不调用任何付费接口。
"""
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

# 平台口径与 collect_core.PLATFORMS 的三家 id 一致，回收站与情报页共用同一套 id。
PLATFORMS = {
    'xiaohongshu': {
        'label': '小红书',
        'needs_login': True,
        'body_note': '小红书正文需要登录浏览器，本机不代跑',
    },
    'douyin': {
        'label': '抖音',
        'needs_login': False,
        'body_note': '抖音是 JS 渲染页，公开抓取拿不到正文',
    },
    'channels': {
        'label': '视频号',
        'needs_login': True,
        'body_note': '视频号没有公开的抓取入口，分享链接要微信登录',
    },
}
LABELS = {key: value['label'] for key, value in PLATFORMS.items()}

# 短链只标出来，不联网解析：解析要发请求，属于抓取，本合同范围外。
SHORT_LINK_NOTE = '短链只标出来，本机不联网解析，作品编号待打开后补'

# 小红书分享链接带的 xsec_token 每次分享都不同，但打开笔记必需，规范链接要留着。
XHS_QUERY_KEEP = ('xsec_token', 'xsec_source')

XHS_HOSTS = frozenset({'xiaohongshu.com', 'www.xiaohongshu.com'})
XHS_SHORT_HOSTS = frozenset({'xhslink.com', 'www.xhslink.com'})
DOUYIN_HOSTS = frozenset({'douyin.com', 'www.douyin.com'})
DOUYIN_SHORT_HOSTS = frozenset({'v.douyin.com'})
SPH_SHARE_HOSTS = frozenset({'weixin.qq.com'})          # weixin.qq.com/sph/<id>
SPH_PREVIEW_HOSTS = frozenset({'channels.weixin.qq.com'})  # finder-preview 预览页
SPH_PREVIEW_PATH = '/finder-preview/pages/sph'

# 编号形态按公开分享格式取宽口径：认得出结构就算认得，不校验平台侧真的存在这条。
NOTE_ID = r'[0-9a-z]{16,32}'
USER_ID = r'[0-9a-zA-Z_-]{16,64}'
VIDEO_ID = r'[0-9]{5,25}'
SEC_UID = r'[A-Za-z0-9_-]{20,120}'
SPH_ID = r'[A-Za-z0-9_-]{6,64}'
SHORT_CODE = r'[A-Za-z0-9_-]{4,32}'

XHS_PATTERNS = (
    ('note', re.compile(r'\A/explore/(%s)/?\Z' % NOTE_ID)),
    ('note', re.compile(r'\A/discovery/item/(%s)/?\Z' % NOTE_ID)),
    ('note', re.compile(r'\A/search_result/(%s)/?\Z' % NOTE_ID)),
    ('user', re.compile(r'\A/user/profile/(%s)/?\Z' % USER_ID)),
)
DOUYIN_PATTERNS = (
    ('video', re.compile(r'\A/video/(%s)/?\Z' % VIDEO_ID)),
    ('user', re.compile(r'\A/user/(%s)/?\Z' % SEC_UID)),
)
SPH_SHARE_PATTERN = re.compile(r'\A/sph/(%s)/?\Z' % SPH_ID)

# 去重用的规范链接只跟作品编号走：同一篇笔记从 /explore/、/discovery/item/ 还是
# /search_result/ 贴进来，都归到同一个地址，知识库因此只会有一条正文。
XHS_DEDUPE_PATH = {'note': '/explore/%s', 'user': '/user/profile/%s'}
# 抖音同理：去重地址由类型与编号构造，不保留原 path，带不带末尾斜杠都归到同一个地址。
DOUYIN_DEDUPE_PATH = {'video': '/video/%s', 'user': '/user/%s'}

# 登录墙与空壳：抓回来的文字看着像页面壳子，就不能当作品正文存进知识库。
LOGIN_WALL_MARKS = ('登录后查看', '请先登录', '扫码登录', '登录小红书', '安全验证', '验证码拦截',
                    '当前内容无法查看', 'unusual login activity', 'verify your identity')
SHELL_MAX_CHARS = 200


def _result(platform, item_kind, item_id, canonical_url, dedupe_key, dedupe_url='', short_link=False):
    meta = PLATFORMS[platform]
    record = {'platform': platform, 'label': meta['label'], 'item_kind': item_kind,
              'item_id': item_id, 'canonical_url': canonical_url, 'dedupe_key': dedupe_key,
              'dedupe_url': dedupe_url or canonical_url, 'short_link': short_link,
              'needs_login': meta['needs_login'], 'body_note': meta['body_note']}
    if short_link:
        record['short_note'] = SHORT_LINK_NOTE
    return record


def _short_code(path):
    """短链要有短链的样子：最后一段得是一条短码。`v.douyin.com/` 这种没有码的不算作品链接。"""
    segments = [segment for segment in path.split('/') if segment]
    if not segments or len(segments) > 2:
        return ''
    last = segments[-1]
    return last if re.fullmatch(SHORT_CODE, last) else ''


def _kept_query(query, keep):
    """只留下打开作品必需的参数，其余跟踪参数一律去掉。

    留下来的值要重新编码：``parse_qsl`` 已把 ``%2B`` / ``%26`` / ``%3D`` 解成 ``+`` / ``&`` / ``=``，
    直接拿 ``&`` 拼回去会让 token 里的 ``&`` 变成参数分隔符、``+`` 变成空格，
    ``share_url`` 就不是原值了。
    """
    pairs = [(name, value) for name, value in parse_qsl(query, keep_blank_values=True) if name in keep]
    return urlencode(pairs)


def _has_control(value):
    """控制字符一律不接受：TAB、CR、LF、DEL 也算，不只 NUL。"""
    return any(ord(char) < 32 or ord(char) == 0x7f for char in value)


def identify(url):
    """认链接：返回平台、作品编号、规范链接与去重键；不是三家就返回 None。

    纯函数，不调网络。短链（xhslink.com、v.douyin.com）只标 ``short_link=True``，
    ``item_id`` 与 ``canonical_url`` 留空 —— 没打开之前不知道是哪条作品，不猜。
    """
    if not isinstance(url, str) or not url.strip() or len(url) > 2048 or _has_control(url):
        return None
    try:
        parts = urlsplit(url.strip())
        host = (parts.hostname or '').lower().rstrip('.')
        port = parts.port
    except ValueError:
        return None
    if parts.scheme.lower() not in ('http', 'https') or not host:
        return None
    if port not in (None, 80, 443):
        return None
    if parts.username is not None or parts.password is not None:
        return None
    path = parts.path or '/'

    if host in XHS_SHORT_HOSTS:
        if not _short_code(path):
            return None
        return _result('xiaohongshu', 'short', '', '', '', short_link=True)
    if host in DOUYIN_SHORT_HOSTS:
        if not _short_code(path):
            return None
        return _result('douyin', 'short', '', '', '', short_link=True)

    if host in XHS_HOSTS:
        for item_kind, pattern in XHS_PATTERNS:
            match = pattern.match(path)
            if not match:
                continue
            item_id = match.group(1)
            canonical = urlunsplit(('https', 'www.xiaohongshu.com', path,
                                    _kept_query(parts.query, XHS_QUERY_KEEP), ''))
            return _result('xiaohongshu', item_kind, item_id, canonical,
                           'xiaohongshu:%s:%s' % (item_kind, item_id),
                           'https://www.xiaohongshu.com' + (XHS_DEDUPE_PATH[item_kind] % item_id))
        return None

    if host in DOUYIN_HOSTS:
        for item_kind, pattern in DOUYIN_PATTERNS:
            match = pattern.match(path)
            if not match:
                continue
            item_id = match.group(1)
            # 抖音分享链接的 query 全是跟踪参数，规范链接一律不带。规范链接与去重地址都由
            # 类型与编号构造，不保留原 path：带不带末尾斜杠都要归到同一个地址。
            douyin_path = DOUYIN_DEDUPE_PATH[item_kind] % item_id
            canonical = urlunsplit(('https', 'www.douyin.com', douyin_path, '', ''))
            return _result('douyin', item_kind, item_id, canonical,
                           'douyin:%s:%s' % (item_kind, item_id), canonical)
        return None

    if host in SPH_SHARE_HOSTS:
        match = SPH_SHARE_PATTERN.match(path)
        if not match:
            return None
        item_id = match.group(1)
        return _result('channels', 'sph', item_id, 'https://weixin.qq.com/sph/' + item_id,
                       'channels:sph:' + item_id)

    if host in SPH_PREVIEW_HOSTS:
        if parts.path.rstrip('/') != SPH_PREVIEW_PATH:
            return None
        item_id = ''
        for name, value in parse_qsl(parts.query, keep_blank_values=False):
            if name == 'id' and re.fullmatch(SPH_ID, value):
                item_id = value
        if not item_id:
            return None
        # 预览页与 weixin.qq.com/sph 分享短链指向同一条作品，规范链接与去重键都归到分享形态。
        return _result('channels', 'sph', item_id, 'https://weixin.qq.com/sph/' + item_id,
                       'channels:sph:' + item_id)

    return None


def body_not_read(info, text):
    """抓回来的文字能不能当作品正文。返回 '' 表示可以当正文，否则返回人话原因。

    只对被认出的三家链接生效：这三家免登录抓回来的多半是登录墙或 JS 空壳，
    把那些文字当正文存进知识库比存空正文更糟。少于 ``SHELL_MAX_CHARS`` 字的按空壳处理，
    命中登录墙字样的按登录墙处理；正文够长且没有登录墙字样就照原样当正文。
    """
    if not info:
        return ''
    body = str(text or '').strip()
    if len(body) < SHELL_MAX_CHARS:
        return '页面只返回空壳文字，没有作品正文'
    lowered = body.lower()
    for mark in LOGIN_WALL_MARKS:
        if mark in lowered:
            return '页面返回登录墙文字，没有作品正文'
    return ''


def receipt(info, reason=''):
    """把「为什么没读到作品正文」拼成一句人话，不编造抓取结果。"""
    parts = []
    if info:
        parts.append(info['body_note'])
        if info.get('short_link'):
            parts.append(SHORT_LINK_NOTE)
    if reason:
        parts.append(reason)
    return '；'.join(parts)
