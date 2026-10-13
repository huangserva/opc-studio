"""把 Markdown 文本转成作品正文的 HTML。

作品导入与工作台编辑此前只做 HTML 白名单清洗（`server.safe_html`），粘贴进来的
Markdown 标记（`## 标题`、`**粗体**`、`- 列表`）会原样留在正文里。本模块提供统一入口。

两条硬规矩，都是复核 H-1 打回来的：

1. **已有 HTML 不得被破坏。** 输入里已经有真实标记（标题、加粗、链接、列表、
   引用、代码）时，只在纯文本段里做**行内** Markdown，绝不把整块降成纯文本再重解析。
2. **`pre`/`code` 内部永不做 Markdown 识别。** 示例代码里的 `## `、`**` 就是字面量。

复核 d 把前两条的适用范围又收紧了一圈：

3. **字面量实体在任何路径上都不还原。** 判定用 `signal_text`（一字不解），
   送进解析器的源用 `markdown_source`（哨兵护住）。上两轮只护住判定，
   用户后续在已存正文旁边新增 `**bold**` 时，别处的新迹象触发整块重排，
   旧的 `&#42;&#42;literal&#42;&#42;` 照样被解码成粗体——这条是本轮补的。
4. **行首转义同样受保护。** `\\# 标题`、`\\- first`、`\\---`、`1\\. literal`
   首存成实体，再存/再重排都不会变回标题、列表、分隔线。清洗层按**位置**
   转义这些标记，`96.5GB` 里的点和连字符原样保留。
5. **围栏按开启长度匹配。** 四反引号围栏里放三反引号示例，内层不能被当成结束行。

「有迹象才转换」的分寸同样重要：误判会把普通中文正文改坏，所以宁可漏判。
"""
import hashlib
import re
import secrets
from html import escape, unescape
from html.parser import HTMLParser
from urllib.parse import unquote

# 只含这些标签、且不带任何属性的 markup，等价于纯文本，可以整块按 Markdown 解析。
_STRUCTURAL = {'p', 'div', 'span'}
_VOID = {'br', 'hr', 'img'}
# 进了这些标签就当作代码，里面的内容原样保留，一个 Markdown 标记都不认。
_CODE_TAGS = {'pre', 'code'}

# 强迹象：出现任意一条就认为正文里确有 Markdown。
_STRONG = (
    ('标题', re.compile(r'^[ \t]{0,3}#{1,6}[ \t]+\S', re.M)),
    ('围栏代码', re.compile(r'^[ \t]{0,3}(?:`{3,}|~{3,})[ \t]*\S*', re.M)),
    ('引用', re.compile(r'^[ \t]{0,3}>[ \t]+\S', re.M)),
    # 行首锚点必须加：`a ---` 是普通行尾破折号，不是分隔线（复核 M-1）。
    ('分隔线', re.compile(r'^[ \t]{0,3}(?:-{3,}|\*{3,}|_{3,})[ \t]*$', re.M)),
    ('粗体', re.compile(r'\*\*(?!\s)[^*\n]{1,500}?\*\*')),
    ('删除线', re.compile(r'~~(?!\s)[^~\n]{1,500}?~~')),
    ('链接', re.compile(r'\[[^\]\n]{1,200}\]\((?:https?://|mailto:)[^)\n]{1,500}\)')),
)

# 列表要求整篇至少两行列表项：单行的「- 说明」在中文正文里太常见，两行以上才是真的列表。
_LIST_LINE = re.compile(r'^[ \t]{0,3}(?:[-*+][ \t]+\S|\d{1,9}[.)][ \t]+\S)', re.M)
_LIST_MIN_LINES = 2

# 弱迹象：中文技术正文里容易撞车（例如 `IQ2_XXS`、破折号），需要两条以上才认定。
_WEAK = (
    ('行内代码', re.compile(r'`[^`\n]{1,300}`')),
    ('斜体', re.compile(r'(?:^|[\s（(「『“"])\*(?!\s)[^*\n]{1,300}?\*(?!\w)')),
)

_WEAK_THRESHOLD = 2

# 独立成行的分隔线前后补空行，避免它被当成上一段的 setext 标题；行首锚点不可省。
_LINE_BREAK = re.compile(r'(?:(?<=\n)|\A)[ \t]{0,3}(?:-{3,}|\*{3,}|_{3,})[ \t]*(?=\r?\n|\Z)')
# 围栏开启行必须按**完整长度与类型**捕获：四反引号围栏里可以放三反引号示例代码。
# 只抓三个字符会让内层短围栏被误认成结束行，代码内容随即被改写（复核 H-1c）。
_FENCE_OPEN = re.compile(r'^[ \t]{0,3}(`{3,}|~{3,})[ \t]*([^\n]*)$')
# 结束行：同种字符、长度不短于开启行、其后不得再有别的字符（CommonMark 的规则）。
_FENCE_END = {}


def fence_end(mark):
    """按开启围栏的实际长度/类型匹配结束行的正则。"""
    pattern = _FENCE_END.get(mark)
    if pattern is None:
        pattern = _FENCE_END[mark] = re.compile(r'^[ \t]{0,3}%s{%d,}[ \t]*$' % (re.escape(mark[0]), len(mark)))
    return pattern


def split_lines(text):
    """只按 ``\\n`` 切行，行尾换行符一并留下。

    ``str.splitlines`` 还会按 ``\\v``、``\\f``、``\\u2028`` 等切行，围栏代码里
    出现这些字节时会被当成新行拆开，代码内容就不再原样（复核 H-1）。
    """
    parts = (text or '').split('\n')
    last = parts.pop()
    return [part + '\n' for part in parts] + ([last] if last else [])


def fence_open(line):
    """这一行是不是围栏开启行？是则返回完整围栏标记，否则返回 None。"""
    matched = _FENCE_OPEN.match(line)
    if not matched:
        return None
    mark = matched.group(1)
    # 反引号围栏的 info string 里不能再有反引号（CommonMark）。
    if mark[0] == '`' and '`' in matched.group(2):
        return None
    return mark


def fenced_lines(text):
    """逐行标出「不能被排版预处理改写」的行：围栏内部连同开启/收尾行。

    围栏里的 `---` 是代码内容，补空行会把它改成 `a\\n\\n\\n---\\n\\n\\nb`。
    ``mask_code`` 只解决「迹象误判」，补空行同样要跳过围栏（复核 H-1）。
    """
    flags, fence = [], None
    for line in split_lines(text):
        if fence is not None:
            flags.append(True)
            if fence_end(fence).match(line):
                fence = None
            continue
        opened = fence_open(line)
        if opened:
            fence = opened
        flags.append(bool(opened))
    return flags


def mask_code(text):
    """把围栏代码块的内容挖空，只留行结构。

    迹象识别必须先做这一步：示例代码里的 `## ` 和 `**` 是字面量，
    不能被当成真标题或真粗体（复核 H-1 的 legacy_md_code）。
    """
    if not text:
        return ''
    out, fence = [], None
    for line in split_lines(text):
        if fence is not None:
            out.append('\n' * line.count('\n'))
            if fence_end(fence).match(line):
                fence = None
            continue
        opened = fence_open(line)
        if opened:
            fence = opened
            # 围栏的开启行要留出标记（迹象识别靠它），内容整段挖空。
            out.append('\n' * line.count('\n') + fence)
            continue
        out.append(line)
    return ''.join(out)


def signals(text):
    """返回正文里出现的 Markdown 迹象名称；空列表表示纯文本。"""
    if not isinstance(text, str) or not text:
        return []
    masked = mask_code(text)
    found = [name for name, pattern in _STRONG if pattern.search(masked)]
    if len(_LIST_LINE.findall(masked)) >= _LIST_MIN_LINES:
        found.append('列表')
    weak = [name for name, pattern in _WEAK if pattern.search(masked)]
    if len(weak) >= _WEAK_THRESHOLD:
        found.extend(weak)
    return found


def looks_like_markdown(text):
    """有 Markdown 迹象才转换；纯文本原样通过。"""
    return bool(signals(text))


def isolate_breaks(text):
    """把独立成行的分隔线前后补空行，避免它被当成上一段的 setext 标题。

    围栏代码内部逐字跳过：那里的 `---` 是代码内容，补空行等于改坏示例代码。
    """
    if not text:
        return text
    return ''.join(line if fenced else _LINE_BREAK.sub(_isolate, line)
                   for line, fenced in zip(split_lines(text), fenced_lines(text)))


def _isolate(match):
    return '\n\n' + match.group(0).strip() + '\n\n'


def _parser():
    from markdown_it import MarkdownIt
    return MarkdownIt('commonmark', {'html': False}).enable('table').enable('strikethrough')


def render(text):
    """Markdown 文本 → 正文 HTML 片段。原始 HTML 一律转义，不放行行内 HTML。"""
    return _parser().render(isolate_breaks(text or '')).strip()


def render_fragment(text):
    """一段纯文本 → 正文 HTML 片段。

    整段就是一个段落时剥掉外层 ``<p>``，让它能并进外面已有的 ``<p>``；
    出现标题、列表、围栏这类块级结构时原样保留块级输出。

    **片段两端的空白必须原样带回来**（复核 H-1）。`merge_inline` 是逐个 text
    token 调这里的，而 token 常常以空格开头：``<strong>原</strong> **新增** 后文``
    里的空格就在 token 开头，整段 strip 掉会把「原」和「新增」粘成一个词。
    """
    core = text or ''
    head = core[:len(core) - len(core.lstrip())]
    tail = core[len(core.rstrip()):]
    rendered = _parser().render(isolate_breaks(core.strip())).strip()
    match = re.fullmatch(r'<p>([\s\S]*)</p>', rendered)
    return head + (match.group(1) if match else rendered) + tail


class _Splitter(HTMLParser):
    """把 markup 切成 (kind, 原文) 序列。

    kind：``tag`` 真实标签、``text`` 文字、``entity`` 实体引用、``code`` 代码区原文。
    convert_charrefs=False：实体引用原样保留，绝不先解码再当 HTML 读
    （复核 H-1：`&lt;div&gt;hi&lt;/div&gt;` 解码成 `<div>hi</div>` 再清洗，尖括号就没了）。
    """

    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.parts = []
        self.code_depth = 0
        self.has_real_markup = False

    def _tag(self, tag, attrs, closing):
        if closing:
            raw = '</' + tag + '>'
        else:
            raw = '<' + tag + ''.join(' ' + name + ('="' + value + '"' if value is not None else '')
                                      for name, value in attrs) + '>'
        self.parts.append(('code' if self.code_depth else 'tag', raw))
        if not closing and tag not in _STRUCTURAL and tag not in _VOID:
            self.has_real_markup = True

    def handle_starttag(self, tag, attrs):
        if tag in _CODE_TAGS:
            self.code_depth += 1
        self._tag(tag, attrs, False)

    def handle_startendtag(self, tag, attrs):
        self._tag(tag, attrs, False)
        if tag not in _VOID:
            self._tag(tag, attrs, True)

    def handle_endtag(self, tag):
        self._tag(tag, None, True)
        if tag in _CODE_TAGS:
            self.code_depth = max(0, self.code_depth - 1)

    def handle_data(self, data):
        self.parts.append(('code' if self.code_depth else 'text', data))

    def handle_entityref(self, name):
        self.parts.append(('code' if self.code_depth else 'entity', '&' + name + ';'))

    def handle_charref(self, name):
        self.parts.append(('code' if self.code_depth else 'entity', '&#' + name + ';'))

    def handle_comment(self, data):
        self.parts.append(('markup', '<!--' + data + '-->'))

    def handle_decl(self, decl):
        self.parts.append(('markup', '<!' + decl + '>'))


def split_markup(markup):
    """切成 (kind, 原文) 序列，并报告这段 markup 里是否已有真实标记。

    Markdown 自动链接 `<https://…>` 由 `_AUTOLINK` 摘出来按文字处理：
    HTMLParser 会把它当成 HTML 标签，连显示文字一起吃掉（复核 f-H1）。
    """
    markup = markup or ''
    if not _AUTOLINK.search(markup):
        splitter = _Splitter()
        splitter.feed(markup)
        splitter.close()
        return splitter.parts, splitter.has_real_markup
    splitter, parts, cut = _Splitter(), [], 0
    for match in _AUTOLINK.finditer(markup):
        splitter.feed(markup[cut:match.start()])
        parts.extend(splitter.parts)
        splitter.parts = []
        # 自动链接落在 `<pre>`/`<code>` 里时按代码原文保留，与其他代码区一致。
        parts.append(('code' if splitter.code_depth else 'text', match.group(0)))
        cut = match.end()
    splitter.feed(markup[cut:])
    parts.extend(splitter.parts)
    splitter.close()
    return parts, splitter.has_real_markup


# 结构标签在「当成 Markdown 源」时对应的换行：<br> 是换行，段落/标题/列表结束是分段。
# 少了这一步，`<p>甲<br><br>## 乙</p>` 里的标题会被粘到上一行，识别不到。
_LINE_BREAK_TAGS = {'br'}
_PARA_BREAK_TAGS = {'p', 'div', 'li', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'blockquote'}
NL = chr(10)


def _tag_name(raw):
    # '</p>' 的第一个字符是 '/'，只去掉 '<' 会把标签名算成空。
    name = raw.lstrip('</').split()[0] if raw.lstrip('</').split() else ''
    return name.rstrip('/>').lower()


def plain_text(markup):
    """markup 的纯文本形态：结构标签翻译成换行、实体引用解码、代码区原样。

    这是转换共用的唯一入口——拿 ``text`` 反推 markup 会把合法的尖括号
    当成标签吃掉（复核 H-1 的 legacy_escaped_code）。
    """
    parts, _ = split_markup(markup)
    out = []
    for kind, value in parts:
        if kind in ('text', 'code'):
            out.append(unescape(value))
        elif kind == 'entity':
            out.append(unescape(value))
        elif kind == 'tag':
            name = _tag_name(value)
            if name in _LINE_BREAK_TAGS:
                out.append('\n')
            elif value.startswith('</') and name in _PARA_BREAK_TAGS:
                out.append('\n\n')
            elif name == 'hr' and not value.startswith('</'):
                # <hr> 在纯文本里什么都不剩，整块按 Markdown 源解析时会被丢掉
                # （复核 B-M1）。翻译成分隔线行，render 才会把它变回 <hr>。
                out.append('\n\n---\n\n')
    return ''.join(out)


def signal_text(markup):
    """迹象判定用的文本：实体引用**一字不解**。

    `safe_html` 把正文里的 Markdown 标记存成实体（`&#42;&#42;`），那是
    「这里要显示字面量星号」的既定事实。判定时若把实体解码回 `**`，
    已存正文会在第二次保存时被当成粗体重新解释一次（复核 H-1 的转义二次解释）。
    """
    parts, _ = split_markup(markup)
    out = []
    for kind, value in parts:
        if kind in ('text', 'code'):
            out.append(value)
        elif kind == 'entity':
            # 结构实体（&gt; &lt; &amp; 等）须解码以正确判定行首引用等 Markdown 迹象；
            # 字面量标记实体（&#42;&#42;、&#35; 等）保持一字不解，防二次解释。
            out.append(_STRUCTURAL_REFS.get(value, value))
        elif kind == 'tag':
            name = _tag_name(value)
            if name in _LINE_BREAK_TAGS:
                out.append('\n')
            elif value.startswith('</') and name in _PARA_BREAK_TAGS:
                out.append('\n\n')
    return ''.join(out)


# 送进 markdown-it 的中间表示：用哨兵把「字面量实体」护住。
# markdown-it 会把 `&` 转义成 `&amp;`，实体直接留在源里会被显示成 `&#42;`。
# 哨兵换回原来的实体写法，渲染完再换回去。
#
# **哨兵为什么是 ASCII 而不是私用区字符**（复核 f-H1 的根因）：
# 私用区字符进链接目标会被 markdown-it 百分号编码成 `%EE%80%80`，`_restore`
# 只认原始字符，于是链接参数被永久改写。实测 ASCII 字母数字串在**所有**合法
# URL 写法下都原样穿过 `mdurl.encode`——嵌套括号、角括号目标、autolink、
# 引用定义换行、转义括号、mailto 一律不碰，所以哨兵不再需要「避开 URL」，
# `_URL_PLACE` 那种猜语法位置的正则整类消失（复核 f 要求改结构性方案）。
# 只用 ASCII 字母数字：它们在 URL 里不会被百分号编码，在正文里也不引入
# 新的转义歧义。用户原文里真的出现这串时由 `_isolate_user_token` 先隔离。
#
# **两套标记的分工**（复核 g-H1 / g-M1 的根因，见 `_spend`）：
# `zQq…qQz` 是**待恢复**哨兵，整个流水线里认它的只有 `_spend` 与终态的 `_restore`；
# `Zzq…Zqz` 是**封层**哨兵，只由终态 `_restore` 剥除。两串字符集互不重叠，
# 所以中间阶段永远看不到封层串，「已经换回原文的用户串」不可能再被当成内部哨兵。
_SENTINEL = 'zQq'
_SENTINEL_END = 'qQz'
# 封层哨兵：与 `_SENTINEL` 字符集**完全不相交**（`zQq` 不出现在 `Zzq…Zqz` 里，
# 反向亦然），因此它对任何中间扫描都是不可见的。
_SEAL = 'Zzq'
_SEAL_END = 'Zqz'
# **每次转换的随机 nonce**（复核 g-H1 的结构性修复，见 `_fresh_nonce`）：
# 24 位十六进制，96 位熵。标记 = 索引 + 本次 nonce，nonce 放在**结尾**，
# 所以索引仍是正则的第 1 组（旧变异脚本与旧机制断言都靠这一点保持可读）。
_NONCE_BYTES = 12
_NONCE_LEN = _NONCE_BYTES * 2
_NONCE_BODY = r'[0-9a-f]{%d}' % _NONCE_LEN
_NONCE_CAP = r'([0-9a-f]{%d})' % _NONCE_LEN
_NONCE_SHAPE = r'(?:%s)?' % _NONCE_BODY
# 测试钩子：置成 `lambda: '<24 位十六进制>'` 即可钉住 nonce，
# 生产路径为 None，走 `secrets.token_hex`（零外网、零依赖安装）。
_NONCE_FACTORY = None
_NONCE_TRIES = 32
# 结构实体必须解码：它们是 HTML 转义（含 html.escape）对字符做的转义，
# 还原成字符才能被 markdown-it 正确处理（&gt; 还原成 > 才能正确识别与渲染 blockquote，
# AT&amp;T 要还原成 AT&T 再转义回去）。
# 字面量标记实体（如 &#42;&#42;、&#35;、&#45;）当字面量，由哨兵护住防二次解释。
_STRUCTURAL_REFS = {'&amp;': '&', '&lt;': '<', '&gt;': '>', '&quot;': '"', '&apos;': "'", '&#39;': "'", '&#x27;': "'"}
_LITERAL_REF = re.compile(r'&(?!amp;|lt;|gt;|quot;|apos;|#39;|#x27;)(?:[a-zA-Z][a-zA-Z0-9]*|#(?:x[0-9a-fA-F]+|[0-9]+));')
_STRUCTURAL_CODE_REFS = {'&amp;': '&', '&lt;': '<', '&gt;': '>', '&quot;': '"', '&apos;': "'", '&#39;': "'", '&#x27;': "'"}
_STRUCT_CODE_RE = re.compile(r'&(?:amp|lt|gt|quot|apos|#39|#x27);')


def _decode_structural_code(text):
    """编辑器序列化代码区内部解码结构实体（含 &amp;、&lt;、&gt; 等）。

    浏览器 contenteditable 序列化时会对用户输入的代码做一层 HTML 转义（用户输入的
    & 变 &amp;，< 变 &lt;）。对于来自编辑器的输入，在送进 markdown-it 前解码恰好一层，
    让 markdown-it 的单次转义产生正确的 HTML 实体，在浏览器中显示原样代码且不执行。
    直接 Markdown 源不执行此解码，以保持用户刻意书写的实体代码原生展现（复核 M1）。
    """
    return _STRUCT_CODE_RE.sub(lambda m: _STRUCTURAL_CODE_REFS.get(m.group(0), m.group(0)), text) if text else text
_SENTINEL_TOKEN = re.compile(_SENTINEL + r'([0-9]+)' + _SENTINEL_END + _NONCE_CAP)
_SEAL_TOKEN = re.compile(_SEAL + r'([0-9]+)' + _SEAL_END + _NONCE_CAP)
# 终态用的合并式：待恢复与封层一次扫完。`re.sub` 的替换结果**不会被二次扫描**，
# 所以一次扫描就够，换回来的用户串不会被同一遍再解释一遍。
# 四组：1/2 待恢复的索引与 nonce，3/4 封层的索引与 nonce。
_TOKENS = re.compile(r'(?:%s)|(?:%s)'
                     % (_SENTINEL + r'([0-9]+)' + _SENTINEL_END + _NONCE_CAP,
                        _SEAL + r'([0-9]+)' + _SEAL_END + _NONCE_CAP))
# 用户形状：带 nonce 的内部标记，以及**不带 nonce** 的旧形（`zQq1qQz` / `Zzq1Zqz`）。
# 两者都要隔离：前者是 nonce 校验万一失效时的最后一道网，后者是用户真的写了
# 这串时的无损保证（隔离再换回，输出逐字不变）。
_USER_SHAPES = re.compile(r'(?:%s)|(?:%s)'
                          % (_SENTINEL + r'[0-9]+' + _SENTINEL_END + _NONCE_SHAPE,
                             _SEAL + r'[0-9]+' + _SEAL_END + _NONCE_SHAPE))
# 代码 token：这些 token 的内容由 markdown-it 按字节原样输出，哨兵原样留在里面，
# 最后由 `convert` 的 `_restore` 一次性换回。链接目标的位置由 token 流定位，
# 不再用正则猜语法位置（复核 f-H1 / f-M1）。
_CODE_TOKENS = frozenset({'fence', 'code_block', 'code_inline'})
# Markdown 自动链接 `<https://…>` / `<a@b.com>`：`split_markup` 用的是 HTMLParser，
# 它会把 `<https://example.com/auto>` 当成 HTML 标签吃掉，整条链接连显示文字一起消失
# （复核 f-H1 的「链接丢失两条」）。这里只把**成形**的自动链接从标签解析里摘出来，
# 其余 `<` 的行为一个字不动（`a < b`、`5<6` 仍按原来的纯文本走）。
_AUTOLINK = re.compile(
    r'<(?:[A-Za-z][A-Za-z0-9+.\-]{1,31}:[^<>\x00-\x20]*'
    r'|[A-Za-z0-9.!#$%&\'*+/=?^_`{|}~\-]+@[A-Za-z0-9](?:[A-Za-z0-9\-]{0,61}[A-Za-z0-9])?'
    r'(?:\.[A-Za-z0-9](?:[A-Za-z0-9\-]{0,61}[A-Za-z0-9])?)+)>')
# 整行文字标签：里面的内容是一整行标题/列表项文字，不是段落，所以只走行内 Markdown。
# 与 server 清洗层的 `WHOLE_LINE_TAGS` 同一个不变量：整行文字不做块级解释
# （复核 f-附加：h2/li 内插 ul）。
_WHOLE_LINE_TAGS = frozenset({'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'li', 'dt', 'dd'})


class _Held(list):
    """哨兵表 = `list` 原文 + 本次转换的 `nonce`。

    用 `list` 子类而不是改签名：`held[i]` / `len(held)` / `append` 的语义一个字没动，
    只是多带一个「本次标记的通行证」。索引空间仍然共享（封层与待恢复共用一张表）。
    """

    def __init__(self, nonce):
        super().__init__()
        self.nonce = nonce


def _fresh_nonce(markup):
    """每次转换现生成 nonce，并**校验它不会出现在用户能产出的任何字符流里**。

    复核 g-H1 的根因是「只按原文里已完整出现的串做隔离」：markdown-it 解码实体后
    才拼出的标记（`https://example.com/zQq&#49;qQz` → `zQq1qQz`）永远不在原文里，
    终态的全局扫描于是把用户的合法 URL 当成内部哨兵换掉。

    这里不再猜「什么字符拼不出标记」——任何可见字符都能用 `&#NN;` / `&#xNN;` 拼出来，
    连跨边界拼接也一样。改成**每次转换换一个 96 位随机 nonce**，标记 = 索引 + nonce，
    `_restore` 只认带**本次** nonce 的完整标记；要撞车，用户输入里必须已经含有这串
    nonce，而生成时就把它挡掉了：nonce 一旦出现在

    - 原文；
    - 原文按实体解码后的形态（`&#49;`→`1`、`&#x31;`→`1`、`&Zcy;`→`Z`，
      以及结构实体的解码，任何跨边界拼接都落在这个形态里）；
    - 原文经**原生本地 markdown-it** 渲染后的 HTML，以及它的属性解码与两次
      unquote 形态（= 复核与单测真正用来对比的形态）；
    - 渲染结果里每个 `href`/`src` 单独 unquote 两次后的形态；

    中任意一处，就**重生成**。标记串本身全是 ASCII 字母数字，在 URL 里不会被
    百分号编码，实体解码只能一次贡献一个字符——所以「nonce 不在上述形态里」
    就是「用户字符不可能拼出本次标记」的构造性保证，不依赖字符生僻程度。

    输出与 nonce 无关：所有标记都在终态被换回，最终 HTML 里不会有 nonce。

    **找不到安全 nonce 时返回空串**，`convert` 据此原样返回输入（不转换）——
    这是唯一能保证「绝不误换用户字符」的降级：转换是锦上添花，改坏用户正文不是。
    绝不返回**已知会撞上**的 nonce：`_isolate_user_token` 兜不住这类输入，因为用户串
    是被实体劈开的，原文里本就不是完整形状（`zQq&#49;qQz` + nonce，raw 文本扫不到）。
    """
    forms = _nonce_forms(markup)

    def usable(nonce):
        return bool(_NONCE_BODY_RE.fullmatch(nonce or '')) and not any(nonce in form for form in forms)

    for _ in range(_NONCE_TRIES):
        # 测试钉死的工厂只是**额外**一个候选源：它恒发同一个值时，旁边的随机源
        # 仍然能救回来，生产路径也永远只走 `secrets`。
        if _NONCE_FACTORY is not None:
            nonce = _NONCE_FACTORY()
            if usable(nonce):
                return nonce
        nonce = secrets.token_hex(_NONCE_BYTES)
        if usable(nonce):
            return nonce
    # 随机源也被换掉 / 极端故障：按输入派生的确定性候选逐个试，它们互不相同。
    for index in range(_NONCE_TRIES):
        nonce = hashlib.sha256(('%d\x00%s' % (index, markup or '')).encode('utf-8')).hexdigest()[:_NONCE_LEN]
        if usable(nonce):
            return nonce
    return ''


_NONCE_BODY_RE = re.compile(_NONCE_BODY)


def _nonce_forms(markup):
    """用户字符可能变成的全部形态，用来校验 nonce 不在里面。"""
    text = markup or ''
    forms = {text, unescape(text)}
    try:
        rendered = _parser().render(text)
    except Exception:
        rendered = ''
    if rendered:
        forms.add(rendered)
        forms.add(unescape(rendered))
        for url in _hrefs(rendered):
            forms.add(url)
            forms.add(unquote(unquote(url)))
            forms.add(unescape(url))
    return forms


class _HrefReader(HTMLParser):
    """收集渲染结果里的 `href` / `src`，用于 nonce 的属性形态校验。"""

    def __init__(self, blob):
        super().__init__(convert_charrefs=True)
        self.urls = []
        self.feed(blob)

    def handle_starttag(self, tag, attrs):
        for name, value in attrs:
            if name in ('href', 'src') and value:
                self.urls.append(value)


def _hrefs(blob):
    reader = _HrefReader(blob)
    return reader.urls


def _marker(head, end, held, index):
    """带本次 nonce 的完整标记：索引在前、nonce 在后。"""
    return head + str(index) + end + held.nonce


def _token(held, original):
    held.append(original)
    return _marker(_SENTINEL, _SENTINEL_END, held, len(held) - 1)


def _seal(held, original):
    """封层：与 `_token` 共用同一张 `held` 表，只是换一套只认终态的标记。

    索引空间共享是刻意的——封层串剥开后拿回的仍是 `held` 里的原文，
    终态不必再判断「这个槽位当初是怎么进去的」。封的是**自己刚 append 的那个槽位**
    （`len(held) - 1`），引用前一个槽位会让别人的原文被换到这里。
    """
    held.append(original)
    return _marker(_SEAL, _SEAL_END, held, len(held) - 1)


def _isolate_user_token(text, held):
    """用户原文里可能真的出现标记形状的串（例如从别处复制来的），先隔离再当文字。

    复核 M1：不能靠「假定输入不含这串」保证无损。隔离后换回的是**原字符**，
    用户看到的还是他自己那几个字符（复核要求不丢不替换，不是要求删掉）。
    复核 g：**两种标记都要隔离**——封层标记同样可能出现在用户原文里，
    只隔离待恢复哨兵会让用户写的 `Zzq0Zqz` 被终态当成内部封层剥掉。
    复核 h：**带本次 nonce 的形状也隔离**——nonce 校验是主防线，这里是第二道；
    两种形状一条正则 `_USER_SHAPES` 覆盖，缺任何一支都会被门禁断言打红。
    """
    return _USER_SHAPES.sub(lambda match: _token(held, match.group(0)), text) if text else text


def _protect(text, held):
    """把字面量实体换成哨兵；markdown-it 看不见它们。"""
    return _LITERAL_REF.sub(lambda match: _token(held, match.group(0)), text or '')


def _decode_structure(text, held):
    """只解码结构实体（`&amp; &lt; &quot;` 等），其余换成哨兵护住。

    顺带把用户原文里长得像哨兵的串隔离掉，见 `_isolate_user_token`。
    """
    out, rest = [], _isolate_user_token(text, held)
    while rest:
        cut = rest.find('&')
        if cut < 0:
            out.append(rest)
            break
        out.append(rest[:cut])
        rest = rest[cut:]
        matched = _LITERAL_REF.match(rest)
        if matched:
            held.append(matched.group(0))
            out.append(_marker(_SENTINEL, _SENTINEL_END, held, len(held) - 1))
            rest = rest[matched.end():]
            continue
        for name, plain in _STRUCTURAL_REFS.items():
            if rest.startswith(name):
                out.append(plain)
                rest = rest[len(name):]
                break
        else:
            out.append('&')
            rest = rest[1:]
    return ''.join(out)


def _spend(text, held, indexes=None):
    """中间阶段把待恢复哨兵换回原文，并给**换回来的串套一层封层**。

    这是复核 g-H1 / g-M1 的结构性修复。f 的根因是同一段文字被恢复了两遍：
    `render_source` 先对 URL token 与代码 token 调 `_restore` 还原出用户自己的
    `zQq1qQz`，`convert` 末尾又对整体调一次 `_restore`，于是这段**已经恢复过的
    用户原文**被当成另一号内部哨兵，换成了别人的实体（`&#42;` → `*`）。

    换哨兵字符不能解决：索引空间是同一张表，构造一个「用户串的序号 ≠ 实体序号
    且指向别的槽位」的输入就会命中。真正的不变量是——
    **一次恢复之后的内容不再参与内部替换**：

    - 逐处封层：每换回一处就新占一个 `held` 槽位并套上只有终态认的标记，
      同一处展开两次也互不串味（哨兵表长度可变，展开不依赖预先算好的长度）；
    - 只封**这一次替换**的产物，不动文本里其余仍待恢复的哨兵
      （`indexes` 限定 + 越界原样返回，两条都不经手封层）；
    - 实体换回后不需要封层：字面实体是一串 `&#NN;`，字符集与两种标记都不相交，
      仍原样交给 markdown-it 按 URL 语义解码。

    g-H1 补的那一半（用户字符被**实体解码后**拼出的完整标记）由本次 nonce 根治：
    `_SENTINEL_TOKEN` 只认带 `held.nonce` 的标记，用户拼不出这个后缀。

    `indexes=None` 表示换回全部待恢复哨兵（代码 token 用）。
    """
    def give_back(match):
        nonce = getattr(held, 'nonce', None)
        if match.group(2) != nonce:
            return match.group(0)
        index = int(match.group(1))
        if (indexes is not None and index not in indexes) or index >= len(held):
            return match.group(0)
        value = held[index]
        return _seal(held, value) if _USER_SHAPES.search(value) else value
    return _SENTINEL_TOKEN.sub(give_back, text or '')


def _restore(text, held):
    """终态恢复：待恢复哨兵与封层哨兵**一次扫完**，替换结果不再被二次扫描。

    `re.sub` 不会回头再扫替换产物，所以这一遍是干净的：换回的用户原文落到输出里，
    就是它本来的样子。索引越界时原样留着，绝不抛异常（复核 M1 的 IndexError）。
    nonce 不是本次转换的（用户串、或上一轮转换漏下来的）一律**原样留着当文字**——
    这正是 g-H1 的判据：恢复必须能从构造上区分内部标记与用户字符。
    """
    def give_back(match):
        index = match.group(1) if match.group(1) is not None else match.group(3)
        nonce = match.group(2) if match.group(2) is not None else match.group(4)
        if nonce != getattr(held, 'nonce', None) or int(index) >= len(held):
            return match.group(0)
        return held[int(index)]
    return _TOKENS.sub(give_back, text or '')


def _code_aware(line, held, from_editor=False):
    """一行里逐段处理：反引号包住的行内代码原样保留，其余按实体规则解码。

    反斜杠转义的反引号不是行内代码的开始——CommonMark 里它就是字面反引号，
    先按普通文字处理，反斜杠由 markdown-it 自己去掉（复核 d 的 backtick 族）。
    from_editor 为 True 时，对行内代码内容解码一层结构实体（含 &amp;、&lt;、&gt; 等）。
    """
    out, cut = [], 0
    while True:
        found = _INLINE_CODE_OPEN.search(line, cut)
        if not found:
            out.append(_decode_structure(line[cut:], held))
            return ''.join(out)
        if _escaped_at(line, found.start()):
            out.append(_decode_structure(line[cut:found.start() + 1], held))
            cut = found.start() + 1
            continue
        ticks = found.group(0)
        close = _inline_code_close(line, found.end(), ticks)
        if close < 0:
            # 没有配对的收尾反引号：这段不是行内代码，按普通文字处理。
            out.append(_decode_structure(line[cut:], held))
            return ''.join(out)
        out.append(_decode_structure(line[cut:found.start()], held))
        code_content = line[found.start() + len(ticks):close]
        if from_editor:
            code_content = _decode_structural_code(code_content)
        out.append(ticks + _isolate_user_token(code_content, held) + ticks)
        cut = close + len(ticks)


def _line_aware(block, held, fence, from_editor=False):
    """一段文字送进 Markdown 源：围栏/行内代码原样，其余按实体规则解码。

    当 from_editor 为 True 时，针对编辑器 contenteditable 序列化的输入，
    在代码区内部解码一层结构实体（含 &amp;、&lt;、&gt;），让 markdown-it 的单次转义产生正确的
    HTML 实体，从而在编辑器和预览中显示原样字符且不执行（第 13 轮 M1 验收）。
    当 from_editor 为 False 时，直接 Markdown 源保持逐字节原样（复核 M1 原期望）。
    """
    out = []
    for line in split_lines(block):
        if fence is not None:
            content = _decode_structural_code(line) if from_editor else line
            out.append(_isolate_user_token(content, held))
            if fence_end(fence).match(line):
                fence = None
            continue
        opened = fence_open(line)
        out.append(_code_aware(line, held, from_editor=from_editor))
        if opened:
            fence = opened
    return ''.join(out), fence


def _escaped_at(line, index):
    """这个位置的反引号前面是不是一个**未被转义**的反斜杠。"""
    backslashes = 0
    at = index - 1
    while at >= 0 and line[at] == _BACKSLASH:
        backslashes += 1
        at -= 1
    return backslashes % 2 == 1


def _inline_code_close(line, start, ticks):
    """找与 `ticks` **等长**的收尾反引号串，返回其起始下标；找不到返回 -1。

    CommonMark 的规则只有一条：收尾反引号串的长度必须与开启串相同（`` `a`b` ``
    里的第二个反引号就是收尾）。两侧是否空白不参与判定——加上那条限制会把
    `&#42; &amp; &#35;` 这种正常的行内代码误判成普通文字。
    """
    at = line.find(ticks, start)
    return at


_INLINE_CODE_OPEN = re.compile(r'`+')
_BACKSLASH = chr(0x5C)


def _walk(tokens):
    for item in tokens:
        yield item
        for child in _walk(item.children or []):
            yield child


def _expand_indexes(source, held, indexes):
    """把指定序号的哨兵换回原文，其余留着。

    换回的串若仍是哨兵形状（就是用户的原文），由 `_spend` 当场封一层：
    链接目标里的用户串因此不会在终态被当成另一号内部哨兵再换一次。
    """
    return _spend(source, held, indexes)


def _unspend_codes(tokens, held):
    """代码 token 的内容在渲染前就地换回原文本，并逐处封层。

    markdown-it 会把 `&` 转义成 `&amp;`，所以换回必须发生在渲染**之前**，
    围栏内容才会逐字节原样（与 `md.render(原文)` 完全一致，复核 f-M1）。
    封层保证换回的用户串不会被终态 `_restore` 再解释一次（复核 g-M1）。
    """
    for item in _walk(tokens):
        if item.type in _CODE_TOKENS and item.content:
            item.content = _spend(item.content, held)


def render_source(source, held):
    """渲染带哨兵的 Markdown 源，链接目标与代码都按 token 结构处理。

    复核 f 要求不再靠全源正则猜语法位置，这里改成解析 markdown-it 的 token 流：

    1. 先解析一次，从 `link_open`/`image` 的 href/src 里**定位**真正落在
       链接目标上的哨兵（嵌套括号、角括号目标、autolink、引用定义换行、
       转义括号全部由 markdown-it 自己判定，正则无从插嘴）。
    2. 代码 token（围栏/缩进/行内）的内容在**渲染前**就地换回原文本，让 markdown-it
       对它做该做的转义——围栏因此逐字节原样，用户原文与代码示例里的私用区、伪哨兵
       不会被二次恢复（复核 f-M1：上轮 `_unsentinel_urls` 先恢复一遍、`_restore`
       再恢复一遍，围栏里的 `S0E` 变成 `S`）。这与 `md.render(原文)` 的结果逐字相同。
    3. 若链接目标里确实有哨兵，把这些哨兵换回原实体后**重新解析**一次，
       让 markdown-it 按它自己的 URL 语义解码实体并百分号编码——
       `?q=&#42;` 得 `?q=*`、`&gt;` 得 `%3E`、`&#20013;` 得 `%E4%B8%AD`。

    两处换回都走 `_spend`：换回即封层，终态的 `_restore` 不可能把用户原文
    当成内部哨兵（复核 g-H1 / g-M1 的根因）。
    """
    parser = _parser()
    env = {}
    # `isolate_breaks` 与末尾 strip 与 `render` 保持一致：独立成行的分隔线要补空行，
    # 否则会被当成上一段的 setext 标题。
    tokens = parser.parse(isolate_breaks(source), env)
    url_indexes = set()
    for item in _walk(tokens):
        url = (item.attrGet('href') if item.type == 'link_open'
               else item.attrGet('src') if item.type == 'image' else None)
        if url and _SENTINEL_TOKEN.search(url):
            url_indexes.update(int(match[0]) for match in _SENTINEL_TOKEN.findall(url)
                               if match[1] == getattr(held, 'nonce', None))
    _unspend_codes(tokens, held)
    if url_indexes:
        # 链接目标上的哨兵换回实体再解析：markdown-it 会给出与用户输入一致的 URL。
        env = {}
        tokens = parser.parse(isolate_breaks(_expand_indexes(source, held, url_indexes)), env)
        _unspend_codes(tokens, held)
    return parser.renderer.render(tokens, parser.options, env).strip()


def markdown_source(markup, from_editor=False):
    """整块按 Markdown 源解析时用的中间文本：结构标签翻译成换行、代码区原样。

    返回 ``(带哨兵的源, 哨兵表)``：**哨兵不能在这里就换回实体**——
    markdown-it 渲染时会把 `&` 转义成 `&amp;`，实体留在源里会显示成 `&amp;gt;`。
    必须等 `render` 跑完，由 `convert` 再换回去。
    哨兵带本次转换的 nonce（`_fresh_nonce`）；找不到安全 nonce 时返回
    ``(None, None)``，调用方据此原样返回输入。

    与 `plain_text` 的唯一区别在实体：**字面量实体不还原**，用哨兵护住。
    这正是复核 H-1b 要求的「结构化 HTML 的既有正文按字面处理，
    只有用户新输入的 Markdown 才解释」——

        输入  <p>&#42;&#42;literal&#42;&#42; 新 **bold**</p>
        旧法  <p><strong>literal</strong> 新 <strong>bold</strong></p>   ← 字面量被解释掉
        现在  <p>&#42;&#42;literal&#42;&#42; 新 <strong>bold</strong></p>
    """
    # 先把 text/entity 拼成**完整的行**再判代码：一段行内代码会被 `split_markup`
    # 拆成 text/entity 多个 part（`&#42;` 就是独立的 entity part），按 part 判看不到
    # 配对的收尾反引号，行装配才看得见整行的语法结构。
    parts, _ = split_markup(markup)
    nonce = _fresh_nonce(markup)
    if not nonce:
        # 找不到安全 nonce（见 `_fresh_nonce`）：原样返回，`convert` 据此不做转换。
        # 宁可漏转换，也不能用会撞车的标记改掉用户的正文。
        return None, None
    out, held, fence = [], _Held(nonce), None
    for raw, line in _assemble_lines(parts):
        if raw:
            # `<pre>`/`<code>` 区：整段原样，不参与 Markdown 语法判定。
            out.append(_isolate_user_token(line, held))
            continue
        value, fence = _line_aware(line, held, fence, from_editor=from_editor)
        out.append(value)
    return ''.join(out), held


def _assemble_lines(parts):
    """把 (kind, 原文) 序列拼成完整的行，交给 `_line_aware` 按行判定代码区。

    `split_markup` 会把一段行内代码拆成多个 part（`` `a &amp; b` `` → text、entity、
    text），按 part 判语法看不到配对的收尾反引号，这里先把整行还原。

    产出 ``(是否原样, 整行)``：
    - True  是 `<pre>`/`<code>` 区（kind == 'code'），原样输出，不参与语法判定；
    - False 是普通文字与实体拼出来的一行，或结构标签翻译出来的换行/分隔线。
    """
    lines = []
    for kind, value in parts:
        if kind == 'code':
            lines.append((True, value))
        elif kind == 'tag':
            name = _tag_name(value)
            if name in _LINE_BREAK_TAGS:
                lines.append((False, NL))
            elif value.startswith('</') and name in _PARA_BREAK_TAGS:
                lines.append((False, NL + NL))
            elif name == 'hr' and not value.startswith('</'):
                # <hr> 在纯文本里什么都不剩，整块按 Markdown 源解析时会被丢掉
                # （复核 B-M1）。翻译成分隔线行，render 才会把它变回 <hr>。
                lines.append((False, NL + NL + '---' + NL + NL))
        else:
            lines.append((False, value))
    # 相邻的普通段先合并、再按行切：`split_markup` 会把 `` &#42; &amp; `` 拆成
    # text/entity/text 三个 part，不合并就看不到配对的收尾反引号（复核 f-M1 行内代码）。
    return _split_lines(_flatten(lines))
def _flatten(lines):
    """把相邻的同类型段合并：`split_markup` 会把一段文字拆成多个 part。"""
    out = []
    for raw, value in lines:
        if out and out[-1][0] is raw:
            out[-1] = (raw, out[-1][1] + value)
        else:
            out.append((raw, value))
    return out


def _split_lines(lines):
    """把含多行的段拆成逐行；原样段（`raw`，即 `<pre>`/`<code>` 区）不拆。"""
    out = []
    for raw, value in lines:
        out.extend((raw, piece) for piece in (value if raw else split_lines(value)))
    return out

def convert(markup, from_editor=False):
    """正文块入参 → 转换后的 markup。

    - 没有 Markdown 迹象：原样返回。
    - 本来就是纯文本（只有 p/div/span/br 与文字、实体）：整块按 Markdown 源解析，
      标题、列表、围栏代码都能生效。
    - 已经有真实标记：只转换纯文本段里的行内标记，已有结构与代码一个字都不动。

    迹象一律按 `signal_text` 判（实体一字不解）：转换必须幂等，否则保存一次
    的正文再存一次就会变样，版本对照与回退全部对不上（复核 H-1）。
    送进解析器的源由 `markdown_source` 生成，字面量实体同样不被还原（复核 H-1b）。

    哨兵恢复只在这里发生一次（复核 g-H1 / g-M1）：中间阶段一律走 `_spend`，
    换回的原文逐处封层，因此已经恢复过的用户串不会被这遍再当成内部哨兵。

    复核 g-H1：隔离只覆盖「原文里已完整出现的标记串」，而 markdown-it 解码实体后
    才拼出的串（`…/zQq&#49;qQz` → `…/zQq1qQz`）不在原文里，终态全局扫描会把用户的
    合法 URL 当成内部哨兵换掉。因此标记改带**本次转换的随机 nonce**：
    - 标记带 nonce，`_restore` 只认带本次 nonce 的完整标记，用户字符无论用十进制/
      十六进制/命名实体、`%xx` 还是跨边界拼接都拼不出它；
    - nonce 生成时校验它不出现在原文、实体解码后的形态、原生 markdown-it 渲染出的
      HTML 及其属性解码/unquote 形态里，出现就重生成（`_fresh_nonce`）；
    - 幂等：同一输入反复转换输出逐字相同（与 nonce 无关，输出里也没有 nonce）。
    """
    if not looks_like_markdown(signal_text(markup)):
        return markup
    _, has_real_markup = split_markup(markup)
    if has_real_markup:
        return merge_inline(markup)
    source, held = markdown_source(markup, from_editor=from_editor)
    if source is None:
        return markup
    return _restore(render_source(source, held), held)


def merge_inline(markup):
    """保留已有标记，只在纯文本段里做行内 Markdown；代码区与标记原样输出。

    text token 两端的空白由 `render_fragment` 带回来，逐 token strip 会把
    `<strong>原</strong> **新增**` 之间的空格吃掉（复核 H-1）。

    整行文字标签（h1-h6/li/dt/dd）内部一律走 `render_inline`，**不产块级结构**：
    `<h2><span><em>- alpha\n- beta</em></span></h2>` 的 `- ` 是标题文字，
    用 `render_fragment` 会在标题里插一个 `<ul>`，再存一次就变成真列表、版本连着涨
    （复核 f-附加）。这与清洗层 `WHOLE_LINE_TAGS` 的行首保护是同一个不变量：
    整行文字既不做块级转义，也不做块级转换。
    """
    parts, _ = split_markup(markup)
    out, whole_line = [], 0
    for kind, value in parts:
        if kind == 'tag':
            name = _tag_name(value)
            if name in _WHOLE_LINE_TAGS:
                whole_line += -1 if value.startswith('</') else 1
            out.append(value)
        elif kind == 'text':
            if not looks_like_markdown(value):
                out.append(escape(value, quote=False))
            elif whole_line > 0:
                out.append(_parser().renderInline(isolate_breaks(value).strip()))
            else:
                out.append(render_fragment(value))
        else:
            out.append(value)
    return ''.join(out)


def is_layout_only(html):
    """这个块只有排版元素（分隔线、换行），没有文字。

    Markdown 的 `---` 转出来就是这种块：plain 为空不代表没有内容，
    直接按 plain 判空会把独立分隔线整块丢掉（复核 M-1）。
    """
    stripped = re.sub(r'\s+', '', html or '')
    return bool(stripped) and re.fullmatch(r'(?:</?(?:hr|br)\s*/?>)+', stripped) is not None


def reflow_blocks(blocks):
    """按 Markdown 重新排版一组正文块。

    只改写「带 Markdown 迹象且转换后确实会变」的文本块，其余块逐字保留；
    图片块位置不动，不拆块、不并块，版本对照与回退的粒度保持不变。

    判定与转换都以块里存的 ``html`` 为准，不拿 ``text`` 反推 markup——
    plain 文本里合法的尖括号当 HTML 读会被吃掉（复核 H-1）。

    返回 ``(新块列表, 报告)``；报告里 ``changed`` 是发生改写的块索引，
    ``skipped`` 是刻意没动的块索引，``signals`` 是全文迹象。

    判定与 `convert` 用同一个 `signal_text`：报告里说「有迹象」就必须真能改写，
    否则按钮转一圈什么都没变，还报了一堆改过的块。历史作品里明文的 `## `、`**`、
    围栏都按明文判定（清洗层只把**用户刻意写成字面量**的标记存成实体），
    所以老文章照样排得干净（复核 H-1）。
    """
    result, changed, skipped = [], [], []
    found = []
    for index, block in enumerate(blocks or []):
        if not isinstance(block, dict) or block.get('kind') != 'text':
            result.append(block)
            continue
        markup = block.get('html') or block.get('text') or ''
        marks = signals(signal_text(markup)) if markup else []
        if not marks:
            result.append(block)
            skipped.append(index)
            continue
        converted = convert(markup)
        if converted == markup:
            # 代码区里的 ## 与 ** 只在块层面看着像迹象，转换后一字未动。
            result.append(block)
            skipped.append(index)
            continue
        found.extend(marks)
        updated = dict(block)
        updated['markdown_reflowed'] = True
        result.append(updated)
        changed.append(index)
    return result, {'changed': changed, 'skipped': skipped, 'signals': sorted(set(found))}


def summary(report, total):
    """给人看的一句概要，写进助手回执和新版本备注。"""
    changed = report.get('changed') or []
    if not changed:
        return '正文没有 Markdown 标记，未做改动'
    marks = '、'.join(report.get('signals') or []) or 'Markdown'
    return '按 Markdown 重新排版 %d/%d 个正文块（%s）' % (len(changed), total, marks)