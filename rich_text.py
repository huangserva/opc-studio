#!/usr/bin/env python3
"""正文富文本清洗：「样式积木」白名单（合同 fixes-rich-style 第一段）。

只允许固定的 data-* 属性与固定取值，不放开任意 CSS：
- 行内 span：data-color / data-hl / data-size；
- 块级 p/h1–h3/li/blockquote：data-align / data-indent / data-space / data-size；
- mark 与 hr 新增进白名单；b→strong、i→em 规范化；
- 常见内联 style 尽量映射成积木，映射不了的丢弃并记入回执列表。

sanitize(value) -> (rich, plain, dropped)：
  rich    清洗后的 HTML（data-* 积木已校验）；
  plain   纯文本；
  dropped 被丢弃的格式描述列表（人类可读，供保存回执 / AI 工具回执使用）。
"""
import re
from html import escape
from html.parser import HTMLParser
from urllib.parse import urlsplit

# 积木取值 → 展示值（style.css、公众号导出、离线预览共用同一套色板/字号）。
COLORS = {'red': '#d63031', 'blue': '#2f6fe4', 'green': '#1e8e4f',
          'orange': '#d97a1a', 'gray': '#7c8396', 'accent': '#4263eb'}
HIGHLIGHTS = {'yellow': '#fff3ad', 'green': '#d3f2d9', 'pink': '#ffd9e2'}
SIZES = {'s': '14px', 'm': '16px', 'l': '20px', 'xl': '26px'}
ALIGNS = ('left', 'center', 'right')
SPACES = ('tight', 'normal', 'loose')
INDENTS = ('2',)
# 公众号导出的段距（紧/松；normal 用默认 margin）。
WECHAT_SPACES = {'tight': '6px 0', 'normal': '15px 0', 'loose': '30px 0'}

BLOCK_TAGS = frozenset({'p', 'h1', 'h2', 'h3', 'li', 'blockquote'})
INLINE_TAGS = frozenset({'span'})
NORMALIZE = {'b': 'strong', 'i': 'em'}
ALLOWED = frozenset({'p', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'strong', 'b', 'em', 'i', 's', 'u',
                     'br', 'blockquote', 'pre', 'code', 'a', 'ul', 'ol', 'li', 'div', 'span',
                     'mark', 'hr'})
BLOCKED_TAGS = ('script', 'style', 'iframe', 'object')

# 常见内联样式 → 积木取值（尽量映射；映射不了的丢弃并记录）。
COLOR_INPUTS = {
    'red': 'red', '#f00': 'red', '#ff0000': 'red',
    'blue': 'blue', '#00f': 'blue', '#0000ff': 'blue',
    'green': 'green', '#008000': 'green',
    'orange': 'orange', '#ffa500': 'orange',
    'gray': 'gray', 'grey': 'gray', '#808080': 'gray',
    '#4263eb': 'accent',
}
HL_INPUTS = {
    'yellow': 'yellow', '#ff0': 'yellow', '#ffff00': 'yellow',
    'green': 'green', '#0f0': 'green', '#00ff00': 'green',
    'pink': 'pink', '#ffc0cb': 'pink',
}
# 每个标签允许携带的积木属性名。
DATA_FOR_TAG = {
    'span': ('data-color', 'data-hl', 'data-size'),
    'mark': (),
    **{tag: ('data-align', 'data-indent', 'data-space', 'data-size') for tag in BLOCK_TAGS},
}
VALID_VALUES = {
    'data-color': frozenset(COLORS),
    'data-hl': frozenset(HIGHLIGHTS),
    'data-size': frozenset(SIZES),
    'data-align': frozenset(ALIGNS),
    'data-indent': frozenset(INDENTS),
    'data-space': frozenset(SPACES),
}
DATA_ORDER = ('data-color', 'data-hl', 'data-size', 'data-align', 'data-indent', 'data-space')
PROPERTY_LABELS = {'color': '颜色', 'background-color': '背景高亮', 'background': '背景高亮',
                   'font-size': '字号', 'text-align': '对齐', 'text-indent': '首行缩进',
                   'line-height': '段间距'}


def _px(value):
    match = re.fullmatch(r'(\d+(?:\.\d+)?)px', value.strip())
    return float(match.group(1)) if match else None


def _size_of(value):
    px = _px(value)
    if px is not None:
        return 's' if px <= 14 else 'm' if px <= 18 else 'l' if px <= 26 else 'xl'
    return {'small': 's', 'medium': 'm', 'large': 'l', 'x-large': 'xl'}.get(value)


def _map_style(tag, raw):
    """把一条 style 声明映射成积木；返回 (属性名, 取值) 或 (None, 丢弃说明)。"""
    prop, _, val = raw.partition(':')
    prop, val = prop.strip().lower(), val.strip().lower()
    if not prop or not val:
        return None, None
    if prop == 'color' and tag in INLINE_TAGS:
        return ('data-color', COLOR_INPUTS.get(val)) if val in COLOR_INPUTS else \
               (None, '颜色 ' + val + ' 不在支持色板内')
    if prop in ('background-color', 'background') and tag in INLINE_TAGS:
        return ('data-hl', HL_INPUTS.get(val)) if val in HL_INPUTS else \
               (None, '高亮色 ' + val + ' 不在支持色板内')
    if prop == 'text-align' and tag in BLOCK_TAGS:
        return ('data-align', val) if val in ALIGNS else (None, '对齐方式 ' + val + ' 不支持')
    if prop == 'text-indent' and tag in BLOCK_TAGS:
        return ('data-indent', '2') if val in ('2em', '2') else (None, '只支持首行缩进 2 字')
    if prop == 'font-size':
        size = _size_of(val)
        return ('data-size', size) if size else (None, '字号 ' + val + ' 无法映射')
    if prop == 'line-height' and tag in BLOCK_TAGS:
        px = _px(val)
        num = px if px is not None else float(val) if re.fullmatch(r'\d+(?:\.\d+)?', val) else None
        if num is None:
            return None, '行高 ' + val + ' 无法映射'
        if num >= 2:
            return 'data-space', 'loose'
        if num <= 1.4:
            return 'data-space', 'tight'
        return None, None  # 接近默认行高，视为无操作
    return None, PROPERTY_LABELS.get(prop, '样式 ' + prop) + ' 不支持用于 ' + tag


class RichHTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.text, self.blocked, self.dropped = [], [], 0, []

    @staticmethod
    def _attr(attrs, name):
        for key, value in attrs:
            if key == name:
                return value
        return None

    def _rich_attrs(self, tag, attrs):
        """产出该标签允许的积木属性串（已校验取值），并记录丢弃项。"""
        allowed_names = DATA_FOR_TAG.get(tag, ())
        data = {}
        for name in allowed_names:
            value = self._attr(attrs, name)
            if value is None:
                continue
            if value in VALID_VALUES.get(name, ()):
                data[name] = value
            else:
                self.dropped.append(name + ' 的取值 ' + value + ' 不受支持')
        style = self._attr(attrs, 'style')
        if style:
            for decl in style.split(';'):
                name, note = _map_style(tag, decl)
                if name and name in allowed_names:
                    data.setdefault(name, note)
                elif name:
                    # 映射产物与直接写 data-* 走同一道标签白名单（M5），否则保存与读取不一致。
                    self.dropped.append(name + ' 不能用在 ' + tag + ' 上')
                elif note:
                    self.dropped.append(note)
        for key, _ in attrs:
            if key.startswith('data-') and key not in allowed_names:
                self.dropped.append(key + ' 不能用在 ' + tag + ' 上')
        return ''.join(' ' + name + '="' + data[name] + '"' for name in DATA_ORDER if name in data)

    def handle_starttag(self, tag, attrs):
        if tag in BLOCKED_TAGS:
            self.blocked += 1
        if self.blocked or tag not in ALLOWED:
            return
        name = NORMALIZE.get(tag, tag)
        extra = ''
        if tag == 'a':
            # a 只保留安全 href，不携带积木属性；但 style/直接 data-* 仍要过
            # 同一道校验，把「不能用在 a 上」如实记入丢弃回执（1c）。
            self._rich_attrs(tag, attrs)
            href = self._attr(attrs, 'href') or ''
            if urlsplit(href).scheme.lower() in ('https', 'http', 'mailto'):
                extra = ' href="' + escape(href, quote=True) + '" rel="noopener noreferrer"'
        else:
            extra = self._rich_attrs(tag, attrs)
        self.parts.append('<' + name + extra + '>')
        if tag == 'br':
            self.text.append('\n')

    def handle_endtag(self, tag):
        if tag in BLOCKED_TAGS:
            self.blocked = max(0, self.blocked - 1)
            return
        name = NORMALIZE.get(tag, tag)
        if not self.blocked and name in ALLOWED and tag not in ('br', 'hr'):
            self.parts.append('</' + name + '>')
            if name in ('p', 'div', 'li', 'h1', 'h2', 'h3', 'blockquote'):
                self.text.append('\n')

    def handle_data(self, data):
        if not self.blocked:
            self.parts.append(escape(data))
            self.text.append(data)


def sanitize(value):
    parser = RichHTML()
    parser.feed(str(value))
    parser.close()
    return ''.join(parser.parts), ''.join(parser.text).rstrip('\n'), parser.dropped
