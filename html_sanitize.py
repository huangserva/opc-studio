"""正文 HTML 清洗与格式回执；可变 Markdown 字面量策略由 server 注入。"""
from html import escape
import re
from urllib.parse import urlsplit

import rich_text

# 这些标签内部是代码：一个 Markdown 标记都不认，也不做上面那层实体转义，
# 保证示例代码逐字节原样。
CODE_TAGS = frozenset({'pre', 'code'})
# 这些标签前后断行：「行首转义」只在它们之后的那段文字上认。
# 少了这份名单，`<h2>01. 账本与底牌</h2>` 的标题文字会被当成有序列表标记转义掉。
# 这些标签会断行：进入它们之后的那段文字算行首。
LINE_BREAKING_TAGS = frozenset({'br', 'p', 'div', 'li', 'ul', 'ol', 'blockquote', 'pre',
                                'h1', 'h2', 'h3', 'h4', 'h5', 'h6'})
# 这些是**不换行**的内联/包裹标签：行首状态要穿过它们继续传下去。
# 少了这份传播，`<p><span>&#45; first</span></p>` 里的行首减号会被当成非行首放过，
# HTMLParser 又把实体解成明文，下一轮转换就把它认成真列表（复核 H1b）。
# 判据是「断不断行」，不是「是不是块级」——span/b/i/em/strong/code/mark 都不断行。
# 这些标签里的文字本身就是一个整体，不是段落开头的一行：`01. 账本与底牌` 写在
# `<h2>` 里是标题文字，写在段落行首才是列表标记。进了这些标签就不做行首转义。
WHOLE_LINE_TAGS = frozenset({'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'li', 'dt', 'dd'})
# 清洗器自己给链接补的固定安全属性。它不是用户请求的格式，不能出现在 dropped 里。
INTERNAL_REL = 'noopener noreferrer'


class SafeHTML(rich_text.RichHTML):
    """正文清洗：h2 的标记转义 + rich-style 的样式积木，两条支路各自留痕。

    **不整体替换成 `rich_text.RichHTML`**（rich-style 原补丁的做法）：那个类没有
    `_escape_data` / `line_start` / `whole_line` / `code_depth`，清洗层就不会把
    Markdown 标记转义成实体落盘，`markdown_blocks.signal_text` 判定时一字不解的
    前提随之失效，正文再存一次就会被重新解释成粗体/标题（复核 H-1）。h2 的这条
    不变量一个字不动，样式积木只作为**新增支路**挂进来。
    """
    # hr 是 Markdown 分隔线转换出来的唯一新增标签，仍是空元素，不放行任何属性。
    # 用户能感知到的格式。p/div/span/br 只是结构，不进 applied，避免回执膨胀。
    FORMAT_KINDS = {'strong': 'bold', 'b': 'bold', 'em': 'italic', 'i': 'italic', 'u': 'underline',
                    's': 'strike', 'h1': 'h1', 'h2': 'h2', 'h3': 'h3', 'ul': 'list', 'ol': 'list',
                    'li': 'list', 'blockquote': 'quote', 'code': 'code', 'pre': 'code', 'a': 'link', 'hr': 'hr'}
    HINTS = {
        'tag_not_allowed': '标签不在正文白名单；可用 p、h1–h3、strong、em、u、s、br、blockquote、pre、code、a、ul、ol、li',
        'tag_blocked': '脚本、样式表与嵌入对象不会保存',
        'attribute_not_allowed': '正文只保留链接的 href，以及 data-color/data-hl/data-size（行内）与 data-align/data-indent/data-space/data-size（段落）样式积木，取值见白名单；内联 style 会按白名单规范化（居中写 text-align:center、字号写 font-size:24px）；加粗用 strong、斜体用 em，别用 class 或白名单外的 style',
        'attribute_value_rejected': '样式积木的取值不在白名单内：颜色只认 red/blue/green/orange/gray/accent，高亮只认 yellow/green/pink，字号只认 s/m/l/xl，对齐只认 left/center/right，首行缩进只认 2，段距只认 tight/normal/loose；请从这几组取值，或改用内联 style 让它自动规范化',
        'href_rejected': '链接只保留 http、https、mailto 的 href',
    }
    # **不加 mark**：rich-style 把 mark 放进白名单，但 h2/A 已复核的
    # `test_disallowed_tag_and_bad_href_are_reported` 明确断言 `<mark data-hl=…>`
    # 走 `tag_not_allowed`（dropped 原因集合必须恰好是那两项）。合同要求不得改动
    # 已复核行为，所以 mark 的支持只留在 `rich_text.sanitize()` 里（公众号出口按它渲染），
    # 保存路径继续按 h2 的白名单把它当作不允许的标签。差异如实记在报告的已知边界里。
    allowed = {'p', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'strong', 'b', 'em', 'i', 's', 'u', 'br', 'hr', 'blockquote', 'pre', 'code', 'a', 'ul', 'ol', 'li', 'div', 'span'}

    def __init__(self):
        # 走 `RichHTML` 的构造：它把 `convert_charrefs=True` 传给 HTMLParser，
        # 并初始化 parts/text/blocked/dropped。直接 `super().__init__(convert_charrefs=True)`
        # 会把该参数透传给 `RichHTML.__init__`（它不接受），所以显式调父类实现。
        rich_text.RichHTML.__init__(self)
        # 被清洗掉的东西逐条记下来，回执要如实告诉模型哪些格式没生效。
        self.dropped = []
        self.code_depth = 0
        # 样式积木被丢弃的原因（人类可读字符串），与上面的字典通道分开：
        # 前者说「某个格式整体没生效」，后者说「哪个 data-* / style 没存上」。
        self.rich_dropped = []
        # 下一段文字是不是在行首：只有行首的标记才需要按位置转义。
        self.line_start = True
        self.whole_line = 0


    def _drop(self, reason, requested, detail=None):
        hint = self.HINTS[reason]
        if detail:
            # rich 层的具体原因（如「样式 position 不支持用于 p」）随 hint 透传，
            # 模型才知道到底哪一条声明没生效，而不是只知道「整个 style 失败了」。
            hint = hint + '；具体原因：' + detail
        entry = {'requested': requested[:120], 'reason': reason, 'hint': hint}
        if entry not in self.dropped:
            self.dropped.append(entry)

    def _style_failures(self, tag, style):
        """按 rich_text 的映射规则逐条解析内联 style，返回真正没生效的说明。

        成功映射、或「接近默认、视为无操作」的声明不计；映射到本标签不支持的积木、
        映射不了的具体取值、以及写法不完整的声明才算未生效。映射规则只有
        rich_text._map_style 一份，这里不另抄一套。

        无值（HTMLParser 给 None）/空值/只有空声明（如 ";;"）没有可生效的声明，
        也要给出真实原因而不是抛异常（M2b M2）。
        """
        if style is None:
            return ['style 没有取值，无法生效']
        decls = [decl for decl in style.split(';') if decl.strip()]
        if not decls:
            return ['style 没有可用的声明，无法生效']
        allowed_names = rich_text.DATA_FOR_TAG.get(tag, ())
        failures = []
        for decl in decls:
            prop, _, val = decl.partition(':')
            if not prop.strip() or not val.strip():
                # 不完整声明（如 "color" / "color:"）：rich 层也映射不出结果，
                # 但这是「写法不完整」而不是「取值不支持」，如实说明。
                failures.append('样式声明「' + decl.strip() + '」不完整，无法生效')
                continue
            name, note = rich_text._map_style(tag, decl)
            if name and name in allowed_names:
                continue
            if name:
                failures.append(name + ' 不能用在 ' + tag + ' 上')
            elif note:
                failures.append(note)
        return failures

    def _actual_blocks(self, tag, attrs):
        """清洗器**实际会落盘**的积木 dict（与 rich_text.RichHTML._rich_attrs 同一策略）。

        data-* 优先且同名取首项、取值须过白名单；随后只有**第一个** style 参与，其声明用
        setdefault 落位（data-* 已占的名字不改）。回执以这份实际结果为准——不能拿某个 style
        的理论映射当比较基准，否则 data 优先的组合会误报/漏报（M2c M1）。
        """
        allowed_names = rich_text.DATA_FOR_TAG.get(tag, ())
        data = {}
        for name in allowed_names:
            value = rich_text.RichHTML._attr(attrs, name)
            if value is None or value not in rich_text.VALID_VALUES.get(name, ()):
                continue
            data[name] = value
        style = rich_text.RichHTML._attr(attrs, 'style')
        if style:
            for decl in style.split(';'):
                name, note = rich_text._map_style(tag, decl)
                if name and name in allowed_names:
                    data.setdefault(name, note)
        return data

    def _style_effects(self, tag, style):
        """按声明顺序列出该 style 每条能映射的声明效果 [(声明, 属性名, 取值)]。"""
        allowed_names = rich_text.DATA_FOR_TAG.get(tag, ())
        effects = []
        for decl in (style or '').split(';'):
            name, note = rich_text._map_style(tag, decl)
            if name and name in allowed_names:
                effects.append((decl.strip(), name, note))
        return effects

    def _lost_detail(self, idx, lost, winners):
        """未生效的 style 效果 + 具体原因（被 data 覆盖 / 只采用第一个 style / 同 style 内重复）。

        `winners` 由调用处按**实际落盘的积木**算出：{积木名: (合法取值, 来源)}。来源只认真正
        赢过的那一份：首项 data-* 取值非法时它根本没落盘，这时还说「被 data-color=red 覆盖」
        就是凭空造一个输入里不存在的来源（复核 m2c 的 L1 / 残余 R06）——那份红其实来自首个
        style。落盘值本身没错，这里只把来源说对。
        """
        parts = []
        for _, name, value in lost:
            winner = winners.get(name)
            if winner is None:
                parts.append('%s=%s 未落盘' % (name, value))
            elif winner[1] == 'data':
                parts.append('%s=%s 被 %s=%s 覆盖' % (name, value, name, winner[0]))
            else:
                parts.append('%s=%s 未采用，实际 %s=%s 来自%s'
                             % (name, value, name, winner[0], winner[1]))
        head = '同一标签只采用第一个 style，本条未参与' if idx else '首个 style 的声明未生效'
        return head + '：' + '、'.join(parts)

    def _rich_extra(self, tag, attrs):
        """复用 `rich_text` 的积木映射，丢弃原因记到字符串通道 `rich_dropped`。

        直接调用父类的 `_rich_attrs`，只把 `self.dropped` 临时换成 `self.rich_dropped`
        ——映射规则因此只有一份，rich-style 1c 复核过的行为不会在这里漂移。
        """
        saved, self.dropped = self.dropped, self.rich_dropped
        try:
            return rich_text.RichHTML._rich_attrs(self, tag, attrs)
        finally:
            self.dropped = saved

    def handle_starttag(self, tag, attrs):
        if tag in CODE_TAGS:
            self.code_depth += 1
        if tag in rich_text.BLOCKED_TAGS:
            self.blocked += 1
            self._drop('tag_blocked', '<' + tag + '>')
        if self.blocked or tag not in self.allowed:
            if not self.blocked and tag not in rich_text.BLOCKED_TAGS:
                self._drop('tag_not_allowed', '<' + tag + '>')
            return
        # 样式积木：该标签允许携带的 data-* 名字（a 不带积木，与 rich-style 一致）。
        extra = self._rich_extra(tag, attrs)
        keep = {'href'} if tag == 'a' else set(rich_text.DATA_FOR_TAG.get(tag, ()))
        # 回执与清洗器**实际落盘的积木**对齐（M2c M1）：先算出真实结果 `actual`
        # （data-* 优先取首项 + 只有第一个 style 参与），再逐条请求判断其效果是否在里面。
        # 请求的效果在 actual 里 → 不报；不在 → 报未生效并说明具体原因（被 data 覆盖 /
        # 同标签只采用第一个 style / 重复 data 只取首项）。不再拿首个 style 的理论映射当基准。
        actual = self._actual_blocks(tag, attrs)
        style_index = 0
        for name, value in attrs:
            # 清洗器自己给链接补的固定安全 rel 不是用户请求的格式。
            # 把它报成「被丢弃」会凭空造出一次假的格式失败（复核 A-L1）。
            if name.lower() == 'rel' and tag == 'a' and (value or '').strip() == INTERNAL_REL:
                continue
            lname = name.lower()
            requested = '%s="%s"' % (name, value) if value is not None else name
            if lname == 'style':
                idx, style_index = style_index, style_index + 1
                failures = self._style_failures(tag, value)
                # 该 style 声明里，效果不在实际落盘结果里的才算未生效。
                lost = [(decl, n, v) for (decl, n, v) in self._style_effects(tag, value)
                        if actual.get(n) != v]
                if failures or lost:
                    detail = '；'.join(failures)
                    if lost:
                        # 获胜积木的合法取值与来源：只有真的落盘的那份才拿来解释来源。
                        # 同名 data-* 首项的取值等于实际落盘值，才说明它过了白名单并赢下这个
                        # 积木（data 优先，合法值必然胜出）；否则这份效果来自首个 style。
                        winners = {name: (have, 'data' if rich_text.RichHTML._attr(attrs, name) == have
                                          else '首个 style') for name, have in actual.items()}
                        more = self._lost_detail(idx, lost, winners)
                        detail = (detail + '；' + more) if detail else more
                    self._drop('attribute_not_allowed', requested, detail=detail)
                continue
            if lname in keep:
                # 白名单内的 data-*：名字对还不够，取值必须过 rich_text 的同一道校验。
                if lname.startswith('data-') and value not in rich_text.VALID_VALUES.get(lname, ()):
                    self._drop('attribute_value_rejected', requested,
                               detail='%s 的取值 %s 不受支持' % (lname, value))
                elif lname.startswith('data-') and actual.get(lname) != value:
                    # 同名 data-* 只取首项：本条与真正落盘的不同 → 未生效（M2c 要求 2）。
                    have = actual.get(lname)
                    self._drop('attribute_not_allowed', requested,
                               detail='重复的 %s 只取首项，本条未参与（实际落盘 %s）'
                                      % (lname, (lname + '=' + have) if have else '无'))
                continue
            self._drop('attribute_not_allowed', requested)
        # b/i 规范化成 strong/em（rich-style 1c），其余标签名一律原样。
        name = rich_text.NORMALIZE.get(tag, tag)
        if tag == 'a':
            href = dict(attrs).get('href', '')
            if urlsplit(href).scheme.lower() in ('https', 'http', 'mailto'):
                extra += ' href="' + escape(href, quote=True) + '" rel="' + INTERNAL_REL + '"'
            elif href:
                self._drop('href_rejected', 'href="' + href + '"')
        self.parts.append('<' + name + extra + '>')
        # 只有断行标签才置位；其余一律沿用当前状态（内联结构传播，覆写 H1b）。
        if tag in LINE_BREAKING_TAGS:
            self.line_start = True
        if tag in WHOLE_LINE_TAGS:
            self.whole_line += 1
        if tag == 'br':
            self.text.append('\n')

    def handle_endtag(self, tag):
        if tag in CODE_TAGS:
            self.code_depth = max(0, self.code_depth - 1)
        if tag in ('script', 'style', 'iframe', 'object'):
            self.blocked = max(0, self.blocked - 1)
            return
        name = rich_text.NORMALIZE.get(tag, tag)
        if not self.blocked and tag in self.allowed and tag not in ('br', 'hr'):
            self.parts.append('</' + name + '>')
            if tag in LINE_BREAKING_TAGS:
                self.line_start = True
            if tag in WHOLE_LINE_TAGS:
                self.whole_line = max(0, self.whole_line - 1)
            if tag in ('p', 'div', 'li', 'h1', 'h2', 'h3', 'blockquote'):
                self.text.append('\n')

    def handle_data(self, data):
        if not self.blocked:
            self.parts.append(self._escape_data(data, self.line_start))
            self.text.append(data)
            self.line_start = data.endswith('\n')



def clean_html_full(value, parser_class):
    """返回 (rich, plain, dropped, rich_dropped)：清洗结果 + 两条丢弃通道。

    第三项是 h2 的字典通道（格式整体没生效，助手回执用），第四项是 rich-style 的
    字符串通道（哪个 data-* / style 没存上，保存回执 `dropped_formats` 用）。
    两件事分开记，免得同一个原因在两条回执里各说一遍。
    """
    parser = parser_class()
    parser.feed(value)
    return (''.join(parser.parts), ''.join(parser.text).rstrip('\n'),
            parser.dropped, parser.rich_dropped)



def SafeHTML_collect_rich(value, parser_class):
    """跑一遍清洗器，只取样式积木的丢弃原因。"""
    parser = parser_class()
    parser.feed(value)
    return parser.rich_dropped



# 标签必须按边界匹配：'<s' 会误命中 '<strong'，'<u' 会误命中 '<ul'。
_FORMAT_TAG = {tag: re.compile(r'<' + tag + r'(?=[\s>/])') for tag in SafeHTML.FORMAT_KINDS}


def html_formats(rich):
    """清洗后的 HTML 里真实保留下来的用户可感知格式（去重，按标签表顺序）。"""
    kinds = []
    for tag, kind in SafeHTML.FORMAT_KINDS.items():
        if _FORMAT_TAG[tag].search(rich) and kind not in kinds:
            kinds.append(kind)
    return kinds


# 回执要进模型上下文，dropped 超过这个数就截断并置 truncated。
FORMAT_RECEIPT_LIMIT = 20


def format_receipt(records):
    """把每个正文块的清洗结果汇总成回执里的 format 段。"""
    applied, dropped = [], []
    for record in records or []:
        for kind in record['applied']:
            applied.append({'index': record['index'], 'kind': kind})
        for item in record['dropped']:
            dropped.append({'index': record['index'], **item})
    return {'applied': applied, 'dropped': dropped[:FORMAT_RECEIPT_LIMIT],
            'requested': len(records or []), 'kept': sum(1 for r in records or [] if r['applied']),
            'truncated': len(dropped) > FORMAT_RECEIPT_LIMIT}


def merge_dropped(*records):
    """把多遍清洗的 dropped 记录去重合并，顺序按出现先后。

    走 Markdown 那条路时 `markdown_blocks.convert` 会先把结构标签降掉，
    属性在这一步就没了，转换后再清洗看不到任何东西。**两遍记录都必须报**，
    否则用户要的红色根本没存上，回执却说 dropped 为空（复核 A-M1）。
    """
    out = []
    for record in records:
        for item in record or []:
            if item not in out:
                out.append(item)
    return out

