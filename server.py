#!/usr/bin/env python3
"""Loopback-only article workbench. Files and immutable versions stay on this Mac."""
import argparse
import sys
import base64
import io
import uuid
from PIL import Image, ImageOps
import copy
import difflib
import hashlib
from html import escape
from html.parser import HTMLParser
import importlib.util
import json
import mimetypes
import os
from pathlib import Path
import re
import shutil
import sqlite3
import tempfile
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit, parse_qs, unquote

import markdown_blocks

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
# 批量软删除回执里每一组的稳定提示码，前端据此区分「已移出 / 已存在 / 不存在 / 版本冲突 / 本条失败」。
TRASH_GROUP_CODES = {'deleted': 'TRASHED', 'already_trashed': 'ALREADY_TRASHED',
                     'missing': 'DOCUMENT_NOT_FOUND', 'errors': 'STORE_ERROR'}
IMPORTER = ROOT.parent / 'article-importer' / 'scripts' / 'article_importer.py'
spec = importlib.util.spec_from_file_location('importer', IMPORTER)
importer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(importer)
content_spec = importlib.util.spec_from_file_location('content_store', ROOT / 'content_store.py')
content = importlib.util.module_from_spec(content_spec)
content_spec.loader.exec_module(content)
assistant_spec = importlib.util.spec_from_file_location('assistant_agent', ROOT / 'assistant_agent.py')
assistant_module = importlib.util.module_from_spec(assistant_spec)
assistant_spec.loader.exec_module(assistant_module)
# 样式积木（复核 rich-style）：白名单与映射规则都在 rich_text.py，这里只引入模块，
# SafeHTML 在其之上扩展。markdown_blocks（哨兵/nonce）走的是另一条通道，不受影响。
import rich_text

# 这几个字符成对/成串出现就是 Markdown 标记：粗体、斜体、删除线、行内代码、链接、
# 标题。正文里逐字要显示它们时（`\*\*literal\*\*`、`\# 字面标题`），清洗层把它们
# 转义成实体落盘，转换判定不再二次解释，浏览器显示一模一样。理由见 SafeHTML._escape_data。
MARKUP_LITERALS = '#*~`[]'
# 行首才成立的标记（列表、分隔线）**按位置**转义：全文每个减号、句点都转义会把
# 「96.5GB」「MoE-混合」这类正文撑成实体，收益为零。正则与 markdown_blocks 的迹象表
# 保持一致——迹象表认不出来的形态，转义了也没用。
MARKUP_LINE_RULES = (
    # 无序列表项：- / + 后面跟空白再加文字
    (re.compile(r'(?m)^([ \t]{0,3})([-+])(?=[ \t]+\S)'), lambda m: m.group(1) + _literal_ref(m.group(2))),
    # 有序列表项：1. / 1)
    (re.compile(r'(?m)^([ \t]{0,3})(\d{1,9})([.)])(?=[ \t]+\S)'),
     lambda m: m.group(1) + m.group(2) + _literal_ref(m.group(3))),
    # 分隔线：整行只有三个以上减号
    (re.compile(r'(?m)^([ \t]{0,3})(-{3,})([ \t]*)$'),
     lambda m: m.group(1) + _literal_ref(m.group(2)) + m.group(3)),
)


def _literal_ref(text):
    """字面量字符的实体写法。统一用数字实体，`markdown_source` 只认这一类。"""
    return ''.join('&#%d;' % ord(char) for char in text)


# 井号是 MARKUP_LITERALS 里唯一会出现在 `&#NN;` 内部的字符，单独用带守卫的正则转义。
_MARKUP_HASH = re.compile(r'#(?![0-9]+;)')


def _escape_line_start(text):
    """转义行首的列表/分隔线标记，其它位置的同名字符原样保留。

    必须在 `MARKUP_LITERALS` 那轮**之前**跑完：`#` 变成 `&#35;` 之后这三条正则就再也
    匹配不到标题行，两轮不会互相踩，实体也不会被再转义一次。
    """
    for pattern, replace in MARKUP_LINE_RULES:
        text = pattern.sub(replace, text)
    return text


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

    def _escape_data(self, data, at_line_start=False):
        """正文文字转义。

        HTMLParser 用 convert_charrefs=True，会把 `&#42;` 先解码成 `*` 再交给
        handle_data。也就是说**实体存不住**：光把星号写成实体，这一层读完就
        又变回明文星号，第二次保存时照样被当成粗体重新解释一次（复核 H-1）。
        要真正幂等，只能在清洗这一层就把 Markdown 标记转义掉，让它们以实体形态
        落盘；`markdown_blocks.signal_text` 判定时一字不解，
        `markdown_blocks.markdown_source` 用哨兵护住，于是这段文字再存多少次
        都是字面量。浏览器显示完全一样。

        覆写 H-1b：用户后续在已存正文旁边新增 `**bold**` 时，别处的新迹象会触发
        整块重排——实体必须在那条路径上同样不被还原，否则旧字面量会被解释掉。
        """
        out = escape(data)
        if self.code_depth:
            # pre/code 内部一个标记都不认，保持逐字节原样，不做这层转义。
            return out
        if at_line_start and not self.whole_line:
            out = _escape_line_start(out)
        for char in MARKUP_LITERALS:
            if char not in out:
                continue
            # `#` 要跳过 `&#NN;` 里已有的那个，否则实体自己会被再转义成 `&&#35;45;`。
            # MARKUP_LITERALS 里 `#` 排第一，它生成的 `&#35;` 不会被本轮再扫一遍。
            out = _MARKUP_HASH.sub(_literal_ref('#'), out) if char == '#' \
                else out.replace(char, _literal_ref(char))
        return out

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


def clean_html_full(value):
    """返回 (rich, plain, dropped, rich_dropped)：清洗结果 + 两条丢弃通道。

    第三项是 h2 的字典通道（格式整体没生效，助手回执用），第四项是 rich-style 的
    字符串通道（哪个 data-* / style 没存上，保存回执 `dropped_formats` 用）。
    两件事分开记，免得同一个原因在两条回执里各说一遍。
    """
    parser = SafeHTML()
    parser.feed(value)
    return (''.join(parser.parts), ''.join(parser.text).rstrip('\n'),
            parser.dropped, parser.rich_dropped)


def SafeHTML_collect_rich(value):
    """跑一遍清洗器，只取样式积木的丢弃原因。"""
    parser = SafeHTML()
    parser.feed(value)
    return parser.rich_dropped


def clean_html(value):
    """返回 (rich, plain, dropped)：清洗后的正文、纯文本、以及被清洗掉的格式清单。

    dropped 每项是 {'requested','reason','hint'}，助手侧原样透传进回执，
    模型据此如实告诉用户哪一项格式没有生效。safe_html 保持两值返回，
    只读清洗的既有调用点一行都不用改。
    """
    return clean_html_full(value)[:3]


def safe_html(value):
    return clean_html(value)[:2]


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



def markdown_aware_html(markup, from_editor=False):
    """正文块入参 → (html, plain, dropped)：带 Markdown 迹象时按 Markdown 转换。

    正文此前只做白名单清洗，粘贴进来的 `## 标题`、`**粗体**`、`- 列表` 会原样留着。
    纯文本没有迹象就逐字通过，既有作品与普通粘贴不受影响。

    已经是 HTML 的块不会被拆掉重排：真实标记（标题、加粗、链接、列表、引用、代码）
    原样保留，只在纯文本段里转行内 Markdown；`pre`/`code` 内部一个标记都不认。
    转换后仍走 `safe_html`，不绕过任何安全校验。

    dropped 报**原输入**与**最终落盘**两遍清洗的并集：转换把结构标签降成纯文本时
    属性已经没了，只报转换后那一遍就会漏掉用户真正被清洗掉的格式（复核 A-M1）。
    """
    rich, plain, dropped = clean_html(markup)
    if markdown_blocks.looks_like_markdown(plain):
        final, final_plain, final_dropped = clean_html(markdown_blocks.convert(markup, from_editor=from_editor))
        return final, final_plain, merge_dropped(dropped, final_dropped)
    return rich, plain, dropped


def rich_dropped_of(markup):
    """只取样式积木被丢弃的原因（字符串，去重）。

    单独走一遍清洗而不是让 `markdown_aware_html` 多返回一个值：
    A 补丁与 h2 的单测按**三值**契约写 `markdown_aware_html`，
    `unconverted_story()` 还会把它 mock 成 `clean_html` 来造「修复前导入」的旧作品。
    四值变体必须**仍然经过这个可 mock 的入口**，否则旧作品夹具会绕过 mock、
    被真的转换一遍，重排测试随即误判成 `unchanged`。
    多这一次解析只在 `save()` 的每块上发生，产物只有积木丢弃原因，不参与清洗结果。
    """
    return sorted(set(SafeHTML_collect_rich(markup)))


def markdown_aware_html_full(markup, from_editor=False):
    """`markdown_aware_html` 的三值 + 样式积木丢弃原因，供 `save()` 用。

    既有调用点与签名一律不动；四值只在这个新变体里出现。
    兼容 unconverted_story() 将 markdown_aware_html mock 成 clean_html 的场景。
    """
    try:
        rich, plain, dropped = markdown_aware_html(markup, from_editor=from_editor)
    except TypeError:
        rich, plain, dropped = markdown_aware_html(markup)
    return rich, plain, dropped, rich_dropped_of(markup)


def uid(value):
    if not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_-]{0,79}', value):
        raise ValueError('文章目录名只能包含英文、数字、短横线和下划线')
    return value


class Conflict(ValueError):
    pass


class Store(content.ContentMixin):
    def __init__(self, workspace):
        self.workspace = Path(workspace).resolve()
        self.inbox, self.library = self.workspace / 'inbox', self.workspace / 'library'
        self.inbox.mkdir(parents=True, exist_ok=True)
        self.library.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.status_path = self.workspace / 'intake.json'
        self.intake = importer.read_json(self.status_path) if self.status_path.exists() else {}
        self.last_scan = 0
        self.scan_cache = {}
        self.collections_path = self.workspace / 'collections.json'
        self._knowledge = None
        self._events = None
        self._feedback = None
        self._creative_intake = None
        self._intake_cards = None
        self._intel = None
        self._intel_recommend = None
        self._collect = None

    @property
    def knowledge(self):
        with self.lock:
            if self._knowledge is None:
                from knowledge_store import Knowledge
                self._knowledge = Knowledge(self)
            return self._knowledge

    @property
    def creative_intake(self):
        # 创意回收工厂（第一期）。名字避开 self.intake，那是文章导入状态。
        with self.lock:
            if self._creative_intake is None:
                from intake_store import Intake
                self._creative_intake = Intake(self.knowledge, self.workspace)
            return self._creative_intake

    @property
    def intake_cards(self):
        # 创意回收工厂（第二期）：候选提示词卡。只读回收项，自己存卡片与证据指纹。
        with self.lock:
            if self._intake_cards is None:
                from intake_cards import IntakeCards
                self._intake_cards = IntakeCards(self.creative_intake, self.knowledge, self.workspace)
                # 收下即分析：列表与详情里的分析状态由卡库提供，不另建一份状态。
                self.creative_intake.analysis_provider = self._intake_cards.analysis_states
                # 媒体识别（图片描述 / 视频转写）完成后自动排队一次分析；排队失败不动已保存的识别结果。
                self.creative_intake.analysis_after_recognition = self._intake_cards.request_analysis_after_recognition
            return self._intake_cards

    @property
    def intel(self):
        # 情报采集（第一期 · 微信公众号）。独立库，不碰 intake* / knowledge_store。
        with self.lock:
            if self._intel is None:
                from intel_store import Intel
                self._intel = Intel(self)
            return self._intel

    @property
    def collect(self):
        # 通用采集模块。公众号走一期已有的 intel；三源只读状态。
        with self.lock:
            if self._collect is None:
                from collect_adapters import build_adapters
                from collect_core import Registry
                self._collect = Registry(self, build_adapters(self))
            return self._collect

    @property
    def intel_recommend(self):
        # 回收站/知识库推荐订阅：只读那两个库，只往 intel.db 写。
        with self.lock:
            if self._intel_recommend is None:
                from intel_recommend import Recommender
                self._intel_recommend = Recommender(self.intel)
            return self._intel_recommend

    @property
    def feedback(self):
        with self.lock:
            if self._feedback is None:
                from publication_metrics import PublicationMetrics
                self._feedback = PublicationMetrics(self)
            return self._feedback

    @property
    def clues(self):
        with self.lock:
            if not hasattr(self, '_clues'):
                from clues_store import Clues
                self._clues = Clues(self)
            return self._clues

    @property
    def clue_pipeline(self):
        with self.lock:
            if not hasattr(self,'_clue_pipeline'):
                from clue_pipeline import CluePipeline
                self._clue_pipeline=CluePipeline(self)
            return self._clue_pipeline

    @property
    def wechat_source(self):
        with self.lock:
            if not hasattr(self, '_wechat_source'):
                from wechat_source_client import WechatSourceClient
                self._wechat_source = WechatSourceClient(self)
            return self._wechat_source

    @property
    def deals(self):
        with self.lock:
            if not hasattr(self, '_deals'):
                from deals_store import Deals
                self._deals = Deals(self)
            return self._deals

    @property
    def deals_review(self):
        with self.lock:
            if not hasattr(self, '_deals_review'):
                from deals_review import DealsReview
                self._deals_review = DealsReview(self)
            return self._deals_review

    @property
    def github(self):
        with self.lock:
            if not hasattr(self, '_github'):
                from github_source import GitHub
                self._github = GitHub(self)
            return self._github

    @property
    def events(self):
        with self.lock:
            if self._events is None:
                from events_store import Events
                self._events = Events(self)
            return self._events

    def article(self, slug):
        return importer.under(self.library, uid(slug))

    def meta(self, slug):
        return importer.read_json(self.article(slug) / 'article.json')

    def package(self, slug, revision=None):
        revision = revision or self.meta(slug)['head']
        if not re.fullmatch(r'v\d{4,}', revision):
            raise ValueError('无效版本')
        return self.article(slug) / 'versions' / revision

    def info(self, slug, revision=None):
        path = self.package(slug, revision)
        manifest = importer.read_json(path / 'manifest.json')
        version = importer.read_json(path / 'version.json')
        return manifest, version, path

    def commit(self, slug, package, summary, source, fingerprint=None):
        """Copy into a staging folder, atomically expose version, then advance head."""
        with self.lock:
            article = self.article(slug)
            versions = article / 'versions'
            versions.mkdir(parents=True, exist_ok=True)
            meta = self.meta(slug) if (article / 'article.json').exists() else {'slug': slug, 'head': None}
            previous = importer.read_json(self.package(slug) / 'manifest.json') if meta['head'] else None
            manifest = importer.load_package(package)
            if manifest.get('studio_sha256'):
                if content.sha(Path(package) / 'studio.json') != manifest['studio_sha256']:
                    raise ValueError('作品元数据校验失败')
                for asset in content.assets(content.studio_at(package)):
                    if content.sha(content.safe_path(package, asset['file'])) != asset['sha256']:
                        raise ValueError('作品素材校验失败')
            if previous and previous['package_id'] == manifest['package_id']:
                return meta['head']
            numbers = [int(p.name[1:]) for p in versions.iterdir() if re.fullmatch(r'v\d{4,}', p.name)]
            rev = f'v{max(numbers, default=0) + 1:04d}'
            staging = Path(tempfile.mkdtemp(prefix='.new-', dir=versions))
            try:
                shutil.copytree(package, staging, dirs_exist_ok=True, copy_function=content.link_copy)
                metadata = {'id': rev, 'created_at': datetime.now(timezone.utc).isoformat(),
                            'summary': summary, 'source': source, 'parent': meta['head'],
                            'source_fingerprint': fingerprint}
                importer.write_json(staging / 'version.json', metadata)
                staging.rename(versions / rev)
                meta.update(head=rev, title=manifest['title'], updated_at=metadata['created_at'])
                importer.write_json(article / 'article.json', meta)
                return rev
            finally:
                if staging.exists():
                    shutil.rmtree(staging)

    def scan(self, force=False):
        with self.lock:
            if not force and time.monotonic() - self.last_scan < 5:
                return
            self.last_scan = time.monotonic()
            for folder in sorted(self.inbox.iterdir()):
                if not folder.is_dir() or folder.is_symlink() or folder.name.startswith('.'):
                    continue
                try:
                    slug = uid(folder.name)
                    raw = {}
                    sources = [folder / n for n in ('content.json', 'article.json', 'article.md', 'article.docx') if (folder / n).is_file()]
                    if not sources:
                        continue
                    if len(sources) > 1:
                        raise ValueError('同一目录请只放一份 content.json、article.json、article.md 或 article.docx')
                    source = sources[0]
                    sha = hashlib.sha256()
                    files = sorted(p for p in folder.rglob('*') if p.is_file() and not any(x.startswith('.') for x in p.relative_to(folder).parts))
                    signature = tuple((str(p), p.stat().st_size, p.stat().st_mtime_ns) for p in files)
                    if not force and self.scan_cache.get(slug) == signature:
                        continue
                    for file in files:
                        if file.is_symlink() or not file.resolve().is_relative_to(folder.resolve()):
                            raise ValueError('投递文件不能是指向外部的链接')
                        sha.update(str(file.relative_to(folder)).encode())
                        with file.open('rb') as stream:
                            for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                                sha.update(chunk)
                    fingerprint = sha.hexdigest()
                    self.scan_cache[slug] = signature
                    if self.intake.get(slug, {}).get('fingerprint') == fingerprint:
                        continue
                    # JSON image references must stay in the intake directory.
                    if source.suffix == '.json':
                        raw = importer.read_json(source)
                        refs = ([raw['cover']] if raw.get('cover') else []) + [b['src'] for b in raw.get('blocks', []) if b.get('type') == 'image']
                        for reference in refs:
                            importer.under(folder, reference)
                    elif source.suffix == '.md':
                        from markdown_it import MarkdownIt
                        for token in MarkdownIt('commonmark').enable('table').parse(source.read_text(encoding='utf-8')):
                            for child in token.children or []:
                                if child.type == 'image':
                                    importer.under(folder, child.attrGet('src'))
                    with tempfile.TemporaryDirectory(prefix='studio-import-') as tmp:
                        out = Path(tmp) / 'package'
                        importer.build(argparse.Namespace(command='prepare', source=str(source), out=str(out), cover='none', title=None, font=None))
                        old = self.package(slug) if (self.article(slug) / 'article.json').exists() else None
                        if raw.get('studio') is not None or (old and (old / 'studio.json').exists()):
                            studio = self.normalize_studio(slug, raw.get('studio'), old, out, intake=folder)
                            content.seal(out, studio, importer)
                        initial = not (self.article(slug) / 'article.json').exists()
                        rev = self.commit(slug, out, '首次导入' if initial else '接收新的图文文件', '文件投递', fingerprint)
                    self.intake[slug] = {'fingerprint': fingerprint, 'version': rev, 'source': str(source), 'error': None}
                except Exception as exc:
                    self.intake[folder.name] = {'error': str(exc), 'source': str(folder)}
            importer.write_json(self.status_path, self.intake)

    def collections(self):
        with self.lock:
            return importer.read_json(self.collections_path) if self.collections_path.exists() else []

    def save_collection(self, payload):
        with self.lock:
            records = self.collections()
            identity = payload.get('id')
            record = next((c for c in records if c['id'] == identity), None)
            if identity and record is None:
                raise ValueError('合集不存在')
            name = str(payload.get('name', record['name'] if record else '')).strip()
            if not name or len(name) > 80:
                raise ValueError('请填写 1 至 80 字的合集名称')
            if any(c['name'] == name and c['id'] != identity for c in records):
                raise ValueError('已有同名合集，请使用其他名称')
            if record is None:
                record = {'id': 'collection-' + uuid.uuid4().hex[:12], 'name': name, 'articles': [],
                          'created_at': datetime.now(timezone.utc).isoformat()}
                records.append(record)
            record['name'] = name
            # Incremental operations preserve simultaneous membership changes.
            for action in ('add', 'remove'):
                for slug in payload.get(action, []):
                    self.meta(slug)
                    if action == 'add' and slug not in record['articles']:
                        record['articles'].append(slug)
                    elif action == 'remove' and slug in record['articles']:
                        record['articles'].remove(slug)
            record['updated_at'] = datetime.now(timezone.utc).isoformat()
            importer.write_json(self.collections_path, records)
            return record

    def listing(self):
        self.scan()
        articles = []
        with self.lock:
            for folder in self.library.iterdir():
                if not (folder / 'article.json').is_file():
                    continue
                meta = self.meta(folder.name)
                manifest, version, path = self.info(folder.name)
                studio = content.studio_at(path)
                preview = studio.get('poster')
                articles.append({**meta, 'content_type': studio['type'], 'has_video': bool(studio.get('video')), 'platforms': {k: v['status'] for k, v in studio['platforms'].items()}, 'version_count': len(list((folder / 'versions').glob('v*'))),
                                 'images': len(manifest['body_media']) + bool(manifest['cover']),
                                 'cover': self.asset_url(folder.name, meta['head'], preview['file']) if preview else self.asset_url(folder.name, meta['head'], (manifest['cover'] or manifest['body_media'][0])['file']) if manifest['cover'] or manifest['body_media'] else None,
                                 'excerpt': next((b.get('text', '')[:85] for b in manifest['blocks'] if b['kind'] == 'text'), '')})
        return {'articles': sorted(articles, key=lambda a: a['updated_at'], reverse=True),
                'inbox': str(self.inbox), 'workspace': str(self.workspace), 'collections': self.collections(),
                'import_errors': [{'article': key, 'error': item['error']} for key, item in self.intake.items() if item.get('error')]}

    @staticmethod
    def asset_url(slug, rev, file):
        from urllib.parse import quote
        return '/assets/' + quote(slug) + '/' + quote(rev) + '/' + quote(file)

    def detail(self, slug, revision=None):
        with self.lock:
            manifest, version, path = self.info(slug, revision)
            blocks = copy.deepcopy(manifest['blocks'])
            for i, block in enumerate(blocks):
                block['index'] = i
                if block['kind'] == 'text':
                    block['html'], _ = safe_html(block['html'])
                else:
                    block['url'] = self.asset_url(slug, version['id'], block['file'])
            cover = copy.deepcopy(manifest['cover'])
            if cover:
                cover['url'] = self.asset_url(slug, version['id'], cover['file'])
            versions = [importer.read_json(p / 'version.json') for p in sorted(path.parent.glob('v*'), reverse=True)]
            return {'slug': slug, 'title': manifest['title'], 'head': self.meta(slug)['head'],
                    'version': version, 'versions': versions, 'cover': cover, 'blocks': blocks,
                    'studio': self.studio_public(slug, version['id'], content.studio_at(path)), 'deliveries': self.delivery_records(slug),
                    'image_count': len(manifest['body_media']) + bool(cover),
                    'characters': len(manifest['title']) + sum(len(b.get('text', '')) for b in blocks),
                    'intake_path': str(self.inbox / slug), 'package_path': str(path), 'warnings': manifest.get('warnings', [])}

    def create(self, payload):
        with self.lock:
            kind = payload.get('type', 'article')
            if kind not in content.TYPES:
                raise ValueError('请选择文章、短推文、图集或视频')
            collection = payload.get('collection')
            if collection and not any(c['id'] == collection for c in self.collections()):
                raise ValueError('合集不存在')
            title = str(payload.get('title', '')).strip() or '未命名图文'
            if len(title) > 500:
                raise ValueError('标题请控制在 500 字以内')
            slug = 'article-' + datetime.now().strftime('%Y%m%d') + '-' + uuid.uuid4().hex[:8]
            folder = self.inbox / slug
            (folder / 'assets').mkdir(parents=True)
            source = folder / 'article.json'
            importer.write_json(source, {'schema_version': 1, 'title': title, 'blocks': [], 'studio': content.empty_studio(kind)})
            self.scan(force=True)
            if self.intake.get(slug, {}).get('error'):
                raise ValueError(self.intake[slug]['error'])
            if collection:
                self.save_collection({'id': collection, 'add': [slug]})
            return self.detail(slug)

    def save(self, slug, payload, source='工作台编辑', from_editor=False):
        with self.lock:
            if self.meta(slug)['head'] != payload['base_version']:
                raise Conflict('已有新版本，请先查看最新内容，再保存您的修改。')
            manifest, _, old = self.info(slug)
            title = payload.get('title', '').strip()
            if not title:
                raise ValueError('标题不能为空')
            with tempfile.TemporaryDirectory(prefix='studio-edit-') as tmp:
                out = Path(tmp)
                shutil.copytree(old / 'images', out / 'images')
                dropped_formats = []  # 样式积木丢弃原因（字符串），随保存回执返回
                if 'blocks' in payload:
                    if not isinstance(payload['blocks'], list) or len(payload['blocks']) > 5000:
                        raise ValueError('正文格式不正确或段落过多')
                    blocks, formats = [], []  # formats 汇总每块的 applied/dropped，进回执
                    for item in payload['blocks']:
                        if 'index' in item:
                            index = item['index']
                            if not isinstance(index, int) or not 0 <= index < len(manifest['blocks']):
                                raise ValueError('无效正文引用')
                            blocks.append(copy.deepcopy(manifest['blocks'][index]))
                        elif item.get('kind') == 'text':
                            raw_html = item.get('html', '')
                            rich, plain, dropped, rich_dropped = markdown_aware_html_full(raw_html, from_editor=from_editor)
                            dropped_formats.extend(rich_dropped)
                            formats.append({'index': len(blocks), 'applied': html_formats(rich), 'dropped': dropped})
                            # Markdown 的 `---` 转出来只有 <hr>：plain 为空但仍是有效排版，
                            # 不能按纯文本判空把整块丢掉（复核 M-1）。
                            if plain.strip() or markdown_blocks.is_layout_only(rich):
                                blocks.append({'kind': 'text', 'html': rich, 'text': plain, 'display_text': plain})
                        elif item.get('kind') == 'image':
                            try:
                                data = base64.b64decode(item.get('data', ''), validate=True)
                                if not data or len(data) > 8 * 1024 * 1024:
                                    raise ValueError('每张图片最大 8 MB')
                                with Image.open(io.BytesIO(data)) as im:
                                    if im.width * im.height > 40_000_000:
                                        raise ValueError('图片像素过大，请缩小到 4000 万像素以内')
                                    normalized = ImageOps.exif_transpose(im).convert('RGBA' if 'A' in im.getbands() else 'RGB')
                                    name = 'images/upload-' + hashlib.sha256(data).hexdigest()[:24] + '.png'
                                    normalized.save(out / name, 'PNG')
                                    blocks.append({'kind': 'image', 'file': name, 'width': normalized.width,
                                                   'height': normalized.height, 'source_sha256': hashlib.sha256(data).hexdigest()})
                            except (OSError, Image.DecompressionBombError) as exc:
                                raise ValueError('无法读取图片，请使用 PNG、JPEG 或 WebP') from exc
                        else:
                            raise ValueError('不支持的正文类型')
                else:
                    blocks = copy.deepcopy(manifest['blocks'])
                    formats = []
                    for key, markup in payload.get('text_blocks', {}).items():
                        index = int(key)
                        if not 0 <= index < len(blocks) or blocks[index]['kind'] != 'text':
                            raise ValueError('无效正文段落')
                        rich, plain, dropped, rich_dropped = markdown_aware_html_full(markup, from_editor=from_editor)
                        dropped_formats.extend(rich_dropped)
                        formats.append({'index': index, 'applied': html_formats(rich), 'dropped': dropped})
                        if rich != safe_html(blocks[index]['html'])[0]:
                            blocks[index].update(html=rich, text=plain, display_text=plain)
                studio = self.normalize_studio(slug, payload.get('studio'), old, out)
                receipt = {'format': format_receipt(formats),
                           'dropped_formats': sorted(set(dropped_formats))}
                if title == manifest['title'] and blocks == manifest['blocks'] and studio == content.studio_at(old):
                    # 用户要了格式但什么都没变，模型也必须知道（prep §2.3）。
                    return {**receipt, 'version': payload['base_version'], 'unchanged': True}
                importer.finalize_package(out, title, copy.deepcopy(manifest['cover']), blocks,
                                          importer.digest(importer.canonical({'title': title, 'blocks': blocks})), manifest.get('warnings', []))
                content.seal(out, studio, importer)
                rev = self.commit(slug, out, payload.get('summary', '').strip()[:180] or '编辑图文', source)
            return {**receipt, 'version': rev, 'unchanged': rev == payload['base_version']}

    def reflow(self, slug, payload):
        """按 Markdown 重新排版当前作品，保存为新版本（旧版本不动，可回退）。

        只重排带 Markdown 迹象的文本块，图片块位置与块顺序都不变，
        因此版本对照与历史回退的粒度保持原样。没有迹象就原样返回，不产生空版本。
        """
        with self.lock:
            # 先校验 base_version：没有这一步，读到的会是别人刚存下的新版本，
            # 既可能空转返回 unchanged，也可能把重排做到用户没看过的那一版上。
            if self.meta(slug)['head'] != payload['base_version']:
                raise Conflict('已有新版本，请先查看最新内容，再重新排版。')
            manifest, _, package = self.info(slug)
            if (content.studio_at(package) or {}).get('type') == 'post':
                raise ValueError('串文按推文分条，不能整篇按 Markdown 重排')
            blocks = copy.deepcopy(manifest['blocks'])
            _, report = markdown_blocks.reflow_blocks(blocks)
            if not report['changed']:
                # 没有迹象也要给统一的 format 回执：助手侧拿到空对象时会自己补 {},
                # 回执格式不统一，模型就分不清「真的什么都没丢」和「这条路径不报」（复核 A-L2）。
                return {'version': payload['base_version'], 'unchanged': True, 'reflow': report,
                        'format': format_receipt([]),
                        'summary': markdown_blocks.summary(report, len(blocks))}
            changed = set(report['changed'])
            rewritten, carried = [], []
            for index, block in enumerate(blocks):
                if index not in changed:
                    rewritten.append({'index': index})
                    continue
                text = block.get('html') or block.get('text') or block.get('display_text') or ''
                try:
                    rich, _, dropped = markdown_aware_html(text, from_editor=False)
                except TypeError:
                    rich, _, dropped = markdown_aware_html(text)
                # 重排前的这一次清洗也产生了 dropped，不能丢：save 只看得到已经
                # 转成 HTML 的 rich，转换过程中被清洗掉的格式到这里就查不到了（复核 A-M1）。
                carried.extend({'index': index, **item} for item in dropped)
                rewritten.append({'kind': 'text', 'html': rich})
            summary = payload.get('summary', '').strip()[:180] or markdown_blocks.summary(report, len(blocks))
            result = self.save(slug, {'base_version': payload['base_version'], 'title': manifest['title'],
                                      'blocks': rewritten, 'summary': summary}, source='Markdown 重排', from_editor=False)
            if carried:
                receipt = dict(result.get('format') or format_receipt([]))
                # 截断判定必须在**裁剪前**比 `> LIMIT`：裁完再 `== LIMIT` 会把
                # 「刚好 20 条、一条没丢」误报成 truncated，摘要跟着说「至少 20 项」
                # （复核 A-L1）。19/20/21 块围栏重排实测 (19,false)/(20,false)/(20,true)。
                merged = merge_dropped(receipt.get('dropped'), carried)
                receipt['dropped'] = merged[:FORMAT_RECEIPT_LIMIT]
                receipt['truncated'] = len(merged) > FORMAT_RECEIPT_LIMIT
                result['format'] = receipt
            result['reflow'] = report
            result['summary'] = summary
            return result

    def markdown_signals(self, slug, payload):
        """只读体检：这份作品里哪些正文块带 Markdown 迹象，供界面提示按钮用。"""
        manifest, _, _ = self.info(slug)
        _, report = markdown_blocks.reflow_blocks(copy.deepcopy(manifest['blocks']))
        report['total'] = len(manifest['blocks'])
        return report

    def restore(self, slug, payload):
        with self.lock:
            if self.meta(slug)['head'] != payload['base_version']:
                raise Conflict('已有新版本，请刷新后重试。')
            source = self.package(slug, payload['version'])
            return {'version': self.commit(slug, source, '从 ' + payload['version'] + ' 恢复', '恢复历史版本')}

    def compare(self, slug, before, after):
        a, _, _ = self.info(slug, before)
        b, _, _ = self.info(slug, after)
        def signature(block):
            return 'text:' + block['html'] if block['kind'] == 'text' else 'image:' + block['sha256']
        matcher = difflib.SequenceMatcher(None, [signature(x) for x in a['blocks']], [signature(x) for x in b['blocks']], autojunk=False)
        changes = []
        def present(block, rev):
            block = copy.deepcopy(block)
            if block['kind'] == 'text':
                block['html'], _ = safe_html(block['html'])
            else:
                block['url'] = self.asset_url(slug, rev, block['file'])
            return block
        def rich_inline_pair(left_html, right_html):
            # 只解析经过现有白名单的 HTML；diff 标记由服务端生成，不放宽正文白名单。
            # 在解码后的文本节点切字符，实体与标签不会被切开，标记不会跨越标签边界。
            class Fragment(HTMLParser):
                def __init__(self, markup):
                    super().__init__(convert_charrefs=True)
                    self.events, self.stack, self.text = [], [], []
                    self.feed(safe_html(markup)[0])
                    self.close()
                    while self.stack:
                        self.end(self.stack.pop())
                    self.plain = ''.join(self.text).rstrip('\n')

                def end(self, tag):
                    self.events.append(('html', '</' + tag + '>'))
                    if tag in ('p', 'div', 'li', 'h1', 'h2', 'h3', 'blockquote'):
                        self.events.append(('break', '\n'))
                        self.text.append('\n')

                def handle_starttag(self, tag, attrs):
                    extra = ''.join(' ' + name + '="' + escape(value or '', quote=True) + '"'
                                    for name, value in attrs)
                    self.events.append(('html', '<' + tag + extra + '>'))
                    if tag == 'br':
                        self.events.append(('break', '\n'))
                        self.text.append('\n')
                    elif tag != 'hr':
                        self.stack.append(tag)

                def handle_endtag(self, tag):
                    # 旧存档可能漏闭标签；只关闭实际打开的标签，并补齐内层闭合。
                    if tag in self.stack:
                        while self.stack:
                            opened = self.stack.pop()
                            self.end(opened)
                            if opened == tag:
                                break

                def handle_data(self, data):
                    self.events.append(('text', data))
                    self.text.append(data)

                def marked(self, ranges, marker):
                    parts, offset, index = [], 0, 0
                    for kind, value in self.events:
                        if kind == 'html':
                            parts.append(value)
                            continue
                        if kind == 'break':
                            offset += len(value)
                            continue
                        start = offset
                        end = start + len(value)
                        while offset < end:
                            while index < len(ranges) and ranges[index][1] <= offset:
                                index += 1
                            changed = index < len(ranges) and ranges[index][0] <= offset
                            cut = min(end, ranges[index][1] if changed else
                                      ranges[index][0] if index < len(ranges) else end)
                            text = escape(value[offset - start:cut - start])
                            parts.append('<' + marker + '>' + text + '</' + marker + '>' if changed else text)
                            offset = cut
                    return ''.join(parts)

            left, right = Fragment(left_html), Fragment(right_html)
            operations = difflib.SequenceMatcher(None, left.plain, right.plain, autojunk=False).get_opcodes()
            removed = [(x, y) for tag, x, y, _, _ in operations if tag != 'equal' and x < y]
            added = [(u, v) for tag, _, _, u, v in operations if tag != 'equal' and u < v]
            return left.marked(removed, 'del'), right.marked(added, 'ins'), left.plain == right.plain

        for op, i, j, k, l in matcher.get_opcodes():
            if op == 'equal':
                continue
            removed, added = a['blocks'][i:j], b['blocks'][k:l]
            change = {'type': op, 'before': [present(x, before) for x in removed], 'after': [present(x, after) for x in added]}
            if len(removed) == len(added) == 1 and removed[0]['kind'] == added[0]['kind'] == 'text':
                left, right = removed[0]['text'], added[0]['text']
                old, new = [], []
                for tag, x, y, u, v in difflib.SequenceMatcher(None, left, right, autojunk=False).get_opcodes():
                    old.append(escape(left[x:y]) if tag == 'equal' else '<del>' + escape(left[x:y]) + '</del>')
                    new.append(escape(right[u:v]) if tag == 'equal' else '<ins>' + escape(right[u:v]) + '</ins>')
                change['inline_before'], change['inline_after'] = ''.join(old), ''.join(new)
                rich_before, rich_after, same_text = rich_inline_pair(
                    change['before'][0]['html'], change['after'][0]['html'])
                change['rich_inline_before'], change['rich_inline_after'] = rich_before, rich_after
                change['format_changed'] = same_text and change['before'][0]['html'] != change['after'][0]['html']
            changes.append(change)
        def cover(m, rev):
            return {**m['cover'], 'url': self.asset_url(slug, rev, m['cover']['file'])} if m['cover'] else None
        old_studio = content.studio_at(self.package(slug, before))
        new_studio = content.studio_at(self.package(slug, after))
        return {'studio_changed': old_studio != new_studio,
                'studio_before': self.studio_public(slug, before, old_studio),
                'studio_after': self.studio_public(slug, after, new_studio),
                'before': before, 'after': after, 'title_before': a['title'], 'title_after': b['title'],
                'title_changed': a['title'] != b['title'], 'changes': changes,
                'cover_changed': (a['cover'] or {}).get('sha256') != (b['cover'] or {}).get('sha256'),
                'cover_before': cover(a, before), 'cover_after': cover(b, after)}


class Handler(BaseHTTPRequestHandler):
    # 重新解析专用日志：一次请求一行，只含下面这七个白名单字段。
    # 故意没有实际回收项 id、URL、query、正文、error 原文、异常消息与任何请求头。
    RESOLVE_LOG_PATH = '/api/intake/<redacted>/resolve'
    RESOLVE_OUTCOMES = ('pending', 'stored', 'duplicate', 'failed')
    RESOLVE_ERROR_KINDS = ('none', 'bad_request', 'forbidden', 'too_large', 'not_found', 'fetch_failed', 'unexpected')

    # ── 知识库「移到已删除」新接口的固定回执 ────────────────────────────────
    # 存储层的校验文案是对着人写的、白名单内的才原样透出；其余一律脱敏，
    # 库路径、SQL、正文与原始异常永远不落到前端。换了文案就退到通用提示，失败方向是安全的。
    TRASH_ID_PATTERN = r'[a-f0-9]{32}'
    TRASH_HASH_PATTERN = r'[a-f0-9]{64}'
    TRASH_SAFE_MESSAGES = frozenset({
        '请选择 1–50 条知识', '资料编号格式无效', '请在确认后提交删除', '删除请求包含无效字段',
        '资料版本信息缺失或无效，请重新预览后再删除', '资料不存在，请重新检索',
    })

    def log_message(self, *args):
        pass

    def trusted(self):
        return self.headers.get('Host') in (f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}')

    # ── 知识库「移到已删除」：局部处理函数（只有这 4 条路由用得到）──────────
    #
    # 不碰 resolve 日志、x-official 路由和其他路由；异常映射也只在这几条里生效，
    # 全服务器的既有 400/409 语义一个字不动。

    def trash_error(self, status, code, message):
        return self.reply(status, {'error': message, 'code': code})

    def trash_invalid(self, message='请求格式无效'):
        return self.trash_error(400, 'INVALID_REQUEST', message)

    def trash_safe_failure(self, exc):
        """存储层抛出来的：白名单文案原样透出，其余全部脱敏，不回显原始异常。"""
        message = str(exc) if isinstance(exc, ValueError) else ''
        if message in self.TRASH_SAFE_MESSAGES:
            return self.trash_invalid(message)
        return self.trash_error(500, 'STORE_ERROR', '这次操作结果未确认，请刷新已删除列表核对')

    def trash_ids(self, payload):
        """只接受 {ids} 一个字段、1–50 个 32 位小写 hex。返回 None 表示整体非法（零变更）。"""
        if not isinstance(payload, dict) or set(payload) != {'ids'} or not isinstance(payload['ids'], list):
            return None
        if not 1 <= len(payload['ids']) <= 50:
            return None
        if any(not isinstance(value, str) or not re.fullmatch(self.TRASH_ID_PATTERN, value) for value in payload['ids']):
            return None
        return payload['ids']

    def trash_items(self, payload):
        """只接受 {items, confirm}；confirm 必须是 JSON 的 true，其余一律整体非法。"""
        if not isinstance(payload, dict) or set(payload) != {'items', 'confirm'} or payload['confirm'] is not True:
            return None
        items = payload['items']
        if not isinstance(items, list) or not 1 <= len(items) <= 50:
            return None
        for item in items:
            if not isinstance(item, dict) or set(item) != {'id', 'expected_hash'}:
                return None
            if not isinstance(item['id'], str) or not re.fullmatch(self.TRASH_ID_PATTERN, item['id']):
                return None
            if not isinstance(item['expected_hash'], str) or not re.fullmatch(self.TRASH_HASH_PATTERN, item['expected_hash']):
                return None
        return items

    @staticmethod
    def trash_bounded(query, key, default, maximum, minimum=0):
        """列表参数必须是 minimum..maximum 的十进制整数。

        没给就用默认值；给了就必须合法 —— `limit=` 这种显式空值算非法，不蒙混成默认值。
        """
        if key not in query:
            return default
        raw = query[key][0] if len(query[key]) == 1 else ''
        if not re.fullmatch(r'\d{1,9}', raw):
            raise ValueError(key)
        value = int(raw)
        if not minimum <= value <= maximum:
            raise ValueError(key)
        return value

    def knowledge_trash_preview(self, payload):
        ids = self.trash_ids(payload)
        if ids is None:
            return self.trash_invalid('请选择 1–50 条知识再预览')
        try:
            return self.reply(200, self.server.store.knowledge.trash_preview(ids))
        except sqlite3.OperationalError:
            return self.trash_error(503, 'STORE_BUSY', '知识库正在更新，请稍后刷新核对')
        except Exception as exc:  # noqa: BLE001 - 新接口的失败一律走脱敏映射，不改全服务器语义
            return self.trash_safe_failure(exc)

    def knowledge_trash_commit(self, payload):
        if not isinstance(payload, dict) or payload.get('confirm') is not True:
            return self.trash_invalid('请在确认后提交删除')
        items = self.trash_items(payload)
        if items is None:
            return self.trash_invalid('删除请求包含无效字段')
        try:
            # 事务提交后才会有 deleted：提交阶段失败会整批回滚，异常向上抛，下面一律不报成功。
            receipt = self.server.store.knowledge.trash_many(items, confirm=True)
            return self.reply(200, {**receipt, 'codes': TRASH_GROUP_CODES, 'conflicts': [self.trash_conflict(c) for c in receipt['conflicts']]})
        except sqlite3.OperationalError:
            return self.trash_error(503, 'STORE_BUSY', '知识库正在更新，请稍后刷新核对')
        except Exception as exc:  # noqa: BLE001 - 同上，失败不外泄
            return self.trash_safe_failure(exc)

    @staticmethod
    def trash_conflict(conflict):
        """冲突项带稳定提示码：页面据此说「预览后资料已更新」，不自动替用户重发。"""
        return {**conflict, 'code': 'VERSION_CONFLICT'}

    def knowledge_trash_restore(self, payload):
        ids = self.trash_ids(payload)
        if ids is None:
            return self.trash_invalid('请选择 1–50 条知识再恢复')
        try:
            return self.reply(200, self.server.store.knowledge.restore_many(ids))
        except sqlite3.OperationalError:
            return self.trash_error(503, 'STORE_BUSY', '知识库正在更新，请稍后刷新核对')
        except Exception as exc:  # noqa: BLE001 - 同上
            return self.trash_safe_failure(exc)

    def knowledge_trash_page(self):
        # 自己重新解析 query（keep_blank_values）：`limit=` 这种显式空值要当非法处理，
        # 而不是悄悄用默认值蒙混过去。do_GET 的解析方式一个字不动。
        query = parse_qs(urlsplit(self.path).query, keep_blank_values=True)
        try:
            offset = self.trash_bounded(query, 'offset', 0, 100000)
            limit = self.trash_bounded(query, 'limit', 30, 50, minimum=1)
        except ValueError:
            return self.trash_invalid('分页参数无效')
        if set(query) - {'offset', 'limit'} or any(len(values) != 1 for values in query.values()):
            return self.trash_invalid('分页参数无效')
        try:
            return self.reply(200, self.server.store.knowledge.trash_list(offset, limit))
        except sqlite3.OperationalError:
            return self.trash_error(503, 'STORE_BUSY', '知识库正在更新，请稍后刷新核对')
        except Exception as exc:  # noqa: BLE001 - 同上
            return self.trash_safe_failure(exc)

    @staticmethod
    def reference_receipt(document):
        """收藏回执：重复收藏命中已删除的资料时，如实带回隐藏事实与统一提示码。

        旧键与新键两条重复分支都走这里，页面因此能统一处理：命中隐藏资料不显示
        「收藏成功」，改为提示可在「已删除」里显式恢复 —— 收藏与同步都不是复活通道。
        传入的 document 必须已带 duplicate 标记；不自动恢复，也不改 document_id/hash。
        """
        if document.get('duplicate'):
            return {**document, 'duplicate': True,
                    'code': 'ALREADY_TRASHED' if document.get('trashed') else 'DUPLICATE'}
        return {**document, 'code': 'COLLECTED'}

    def _resolve_request(self):
        """只判断「是不是重新解析这一条路由」，不把 self.path 或 id 留到别处。"""
        try:
            segments = urlsplit(self.path).path.strip('/').split('/')
        except ValueError:
            return None
        return segments[2] if len(segments) == 4 and segments[:2] == ['api', 'intake'] and segments[3] == 'resolve' else None

    @staticmethod
    def _drop_ref(drop_id):
        """可关联但不可还原：回收项 id 的单向摘要，不是 id 本身。"""
        return 'd' + hashlib.sha256(str(drop_id).encode()).hexdigest()[:12]

    @staticmethod
    def _error_kind(status, outcome):
        # 只在真发出响应时按状态分类；「什么都没发出」由入口的初始值 unexpected 负责，
        # 那里是唯一知道「请求没走到 reply」的地方。
        if status == 403:
            return 'forbidden'
        if status == 413:
            return 'too_large'
        if status == 404:
            return 'not_found'
        if not 200 <= status < 300:
            return 'bad_request'
        return 'fetch_failed' if outcome == 'failed' else 'none'

    def _log_resolve(self, context):
        """写一行到进程 stderr。日志自己失败绝不能改写业务结果。"""
        line = json.dumps({'method': 'POST', 'path': self.RESOLVE_LOG_PATH,
                           'status': context['status'], 'outcome': context['outcome'],
                           'error_kind': context['error_kind'], 'drop_ref': context['drop_ref'],
                           'elapsed_ms': max(0, round((time.monotonic() - context['started']) * 1000))},
                          ensure_ascii=False, separators=(',', ':'))
        try:
            sys.stderr.write(line + '\n')
            sys.stderr.flush()
        except OSError:
            pass

    def reply(self, status, data, mime='application/json; charset=utf-8'):
        context = getattr(self, '_resolve_log', None)
        if context is not None:
            # 只记本次请求「打算回什么」，不是「客户端已经收到」：写 body 可能失败。
            outcome = data.get('outcome') if isinstance(data, dict) else None
            context['outcome'] = outcome if outcome in self.RESOLVE_OUTCOMES else 'unknown'
            context['status'] = status
            context['error_kind'] = self._error_kind(status, context['outcome'])
        body = json.dumps(data, ensure_ascii=False).encode() if not isinstance(data, bytes) else data
        self.send_response(status)
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Content-Security-Policy', "default-src 'self'; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline'; script-src 'self'; object-src 'none'; frame-ancestors 'none'")
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(body)

    def serve_file(self, path, download=False):
        size = path.stat().st_size
        start, end, status = 0, size - 1, 200
        header = self.headers.get('Range')
        if header:
            match = re.fullmatch(r'bytes=(\d*)-(\d*)', header)
            if not match or not any(match.groups()):
                return self.reply(416, {'error': '无效文件范围'})
            first, last = match.groups()
            if first:
                start = int(first)
                end = min(int(last), size - 1) if last else size - 1
            else:
                start = max(0, size - int(last))
            if start >= size or start > end:
                self.send_response(416)
                self.send_header('Content-Range', f'bytes */{size}')
                self.send_header('Content-Length', '0')
                self.end_headers()
                return
            status = 206
        self.send_response(status)
        self.send_header('Content-Type', mimetypes.guess_type(path.name)[0] or 'application/octet-stream')
        self.send_header('Content-Length', str(max(0, end-start+1)))
        self.send_header('Accept-Ranges', 'bytes')
        self.send_header('X-Content-Type-Options', 'nosniff')
        if download:
            self.send_header('Content-Disposition', 'attachment; filename="content-package.zip"')
        if status == 206:
            self.send_header('Content-Range', f'bytes {start}-{end}/{size}')
        self.end_headers()
        if self.command == 'HEAD':
            return
        try:
            with path.open('rb') as stream:
                stream.seek(start)
                remaining = end-start+1
                while remaining > 0:
                    data = stream.read(min(1024*1024, remaining))
                    if not data:
                        break
                    self.wfile.write(data)
                    remaining -= len(data)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        if not self.trusted():
            return self.reply(403, {'error': '仅限本机访问'})
        parts = urlsplit(self.path)
        segments = [unquote(x) for x in parts.path.split('/') if x]
        query = parse_qs(parts.query)
        store = self.server.store
        try:
            if parts.path.startswith('/api/x-official/'):
                from publishing.x_official import get_route
                return get_route(self, parts, query)
            if parts.path == '/api/sources/wechat/status':
                return self.reply(200, store.wechat_source.status())
            if parts.path == '/api/sources/wechat/results':
                if set(query) - {'kind', 'q', 'category', 'offset', 'limit'} or any(len(v) != 1 for v in query.values()):
                    raise ValueError('微信来源查询参数无效')
                return self.reply(200, store.wechat_source.results(kind=query.get('kind',['clues'])[0], query=query.get('q',[''])[0], category=query.get('category',[''])[0], offset=query.get('offset',[0])[0], limit=query.get('limit',[30])[0]))
            if parts.path == '/api/sources/wechat/radar-quarantine':
                # Read-only: radar batches held out of the hourly replay after a second invalid answer.
                params = parse_qs(parts.query, keep_blank_values=True)
                if set(params) - {'limit'} or any(len(v) != 1 for v in params.values()):
                    raise ValueError('隔离列表只接受一个可选的 limit 参数')
                return self.reply(200, store.wechat_source.radar_quarantine(int(params['limit'][0]) if params.get('limit') else 50))
            if parts.path == '/api/deals':
                return self.reply(200, store.deals.listing(month=query.get('month',[''])[0], q=query.get('q',[''])[0], stage=query.get('stage',[''])[0], payment=query.get('payment',[''])[0], archived=query.get('archived',['0'])[0]=='1'))
            if parts.path == '/api/deals/proposals/status':
                params=parse_qs(parts.query,keep_blank_values=True)
                if set(params)-{'ids'} or len(params.get('ids',[]))!=1:raise ValueError('建议状态参数无效')
                return self.reply(200,self.server.store.deals.proposal_status(params['ids'][0].split(',') if params['ids'][0] else []))
            if parts.path == '/api/deals/options':
                return self.reply(200, store.deals.options())
            if parts.path == '/api/deals/review':
                return self.reply(200, store.deals_review.summary())
            if parts.path == '/api/deals/review/report':
                params = parse_qs(parts.query, keep_blank_values=True)
                if set(params) - {'week'} or len(params.get('week', [])) != 1:
                    raise ValueError('周报参数无效')
                return self.reply(200, store.deals_review.report(params['week'][0]))
            if parts.path == '/api/intake':
                params = parse_qs(parts.query, keep_blank_values=True)
                if set(params) - {'limit'} or len(params.get('limit', [])) > 1:
                    raise ValueError('回收站列表只接受一个可选的 limit 参数')
                return self.reply(200, store.creative_intake.listing(int(params['limit'][0]) if params.get('limit') else 80))
            if parts.path == '/api/intake/analysis-status':
                # 回收站页面用它判断这个后端是否支持即时分析。旧版本没有这个接口（404），
                # 页面就如实显示「服务未就绪」，不再给出误导性的「点重新分析」。
                cards = store.intake_cards
                try:
                    configured = bool(cards.analysis_model())
                except (OSError, ValueError, KeyError):
                    configured = False
                return self.reply(200, {'schema': 'intake-analysis-status/v1', 'supported': True,
                                        'model_configured': configured, 'working': len(cards.analysis_workers),
                                        'note': '即时分析只复用「创意提炼」已配置的模型，不新增服务、不读密钥'})
            if len(segments) == 4 and segments[:2] == ['api', 'intake'] and segments[3] == 'file':
                return self.serve_file(store.creative_intake.file_path(segments[2]))
            if len(segments) == 3 and segments[:2] == ['api', 'intake']:
                return self.reply(200, store.creative_intake.detail(segments[2]))
            if parts.path == '/api/cards':
                params = parse_qs(parts.query, keep_blank_values=True)
                if set(params) - {'status', 'q', 'limit', 'offset', 'kind', 'view'} or any(len(values) > 1 for values in params.values()):
                    raise ValueError('提示词卡列表只接受 status、q、limit、offset、kind、view 各一个')
                return self.reply(200, store.intake_cards.listing(
                    params.get('status', [''])[0], params.get('q', [''])[0],
                    int(params['limit'][0]) if params.get('limit') else 60,
                    int(params['offset'][0]) if params.get('offset') else 0,
                    params.get('kind', [''])[0], params.get('view', ['source'])[0] or 'source'))
            if len(segments) == 4 and segments[:2] == ['api', 'cards'] and segments[3] == 'prompt':
                return self.reply(200, store.intake_cards.prompt_card(segments[2]))
            if len(segments) == 3 and segments[:2] == ['api', 'cards']:
                return self.reply(200, store.intake_cards.detail(segments[2]))
            if len(segments)==3 and segments[:2]==['api','deals']:
                return self.reply(200, store.deals.detail(segments[2]))
            if parts.path == '/api/collect/platforms':
                return self.reply(200, store.collect.listing())
            if parts.path == '/api/collect/aisa/job':
                return self.reply(200, store.collect.adapter('aisa').job(query.get('id', [''])[0]))
            if parts.path == '/api/collect/status':
                return self.reply(200, store.collect.status())
            if parts.path == '/api/intel/recommendations':
                return self.reply(200, store.intel_recommend.listing())
            if parts.path == '/api/intel/status':
                return self.reply(200, store.intel.status())
            if parts.path == '/api/intel/subscriptions':
                return self.reply(200, {'subscriptions': store.intel.subscriptions()})
            if parts.path == '/api/intel/jobs':
                return self.reply(200, {'jobs': store.intel.jobs(int(query.get('limit', [30])[0]))})
            if parts.path == '/api/intel/articles':
                account = query.get('fakeid', [''])[0] or None
                return self.reply(200, {**store.intel.articles(
                    account, int(query.get('limit', [100])[0]), int(query.get('offset', [0])[0])),
                    'albums': store.intel.albums(account),
                    # 只读：上次「发现合集」的留痕，刷新页面后也能看到。
                    'album_search': store.intel.album_search(account)})
            if parts.path == '/api/intel/accounts':
                if set(query) - {'query'} or len(query.get('query', [])) != 1:
                    raise ValueError('公众号搜索只接受一个 query 参数')
                return self.reply(200, {'candidates': store.intel.search_accounts(query['query'][0])})
            if len(segments) == 4 and segments[:3] == ['api', 'intel', 'articles']:
                return self.reply(200, store.intel.article(segments[3]))
            if len(segments) == 4 and segments[:3] == ['api', 'intel', 'jobs']:
                return self.reply(200, store.intel.job(segments[3]))
            if parts.path == '/api/feedback':
                return self.reply(200, store.feedback.bindings())
            if parts.path == '/api/feedback/observations':
                return self.reply(200, store.feedback.observations.listing())
            if parts.path == '/api/feedback/reviews':
                return self.reply(200, store.feedback.reviews.works())
            if parts.path == '/api/feedback/overviews':
                return self.reply(200, {'overviews': store.feedback.reviews.overviews()})
            if len(segments) == 4 and segments[:3] == ['api', 'feedback', 'review']:
                return self.reply(200, store.feedback.reviews.review(segments[3]))
            if len(segments) == 4 and segments[:3] == ['api', 'feedback', 'observation']:
                return self.reply(200, store.feedback.observations.get(segments[3]))
            if parts.path == '/api/feedback/snapshots':
                return self.reply(200, store.feedback.listing(slug=query.get('slug', [''])[0], delivery_id=query.get('delivery_id', [''])[0], limit=int(query.get('limit', [50])[0])))
            if len(segments) == 4 and segments[:3] == ['api', 'feedback', 'snapshot']:
                return self.reply(200, store.feedback.snapshot(segments[3]))
            if parts.path == '/api/clues/wechat/status':
                return self.reply(200,store.wechat_source.clue_status())
            if parts.path == '/api/clues':
                return self.reply(200, store.clues.listing(query=query.get('q',[''])[0],category=query.get('category',[''])[0],view=query.get('view',['all'])[0],source=query.get('source',[''])[0],year=query.get('year',[''])[0],offset=query.get('offset',[0])[0],tag=query.get('tag',[''])[0],sort=query.get('sort',['updated'])[0],period=query.get('period',[''])[0],priority=query.get('priority',[''])[0],heat=query.get('heat',[''])[0]))
            if len(segments)==3 and segments[:2]==['api','clues']:
                return self.reply(200, store.clues.detail(segments[2],offset=int(query.get('offset',[0])[0])))
            if parts.path == '/api/events':
                return self.reply(200, store.events.list_events(query=query.get('q', [''])[0], view=query.get('view', ['all'])[0], status=query.get('status', [''])[0], limit=int(query.get('limit', [50])[0])))
            if len(segments) == 3 and segments[:2] == ['api', 'events']:
                return self.reply(200, store.events.event(segments[2]))
            if parts.path == '/api/github':
                return self.reply(200, {**store.github.status(), 'tasks':[t for t in self.server.schedules.listing()['tasks'] if t['kind']=='github_clues']})
            if parts.path == '/api/knowledge':
                return self.reply(200, store.knowledge.status())
            if parts.path == '/api/knowledge/search':
                return self.reply(200, store.knowledge.search(query.get('q', [''])[0], query.get('kind', [''])[0], query.get('tag', [''])[0], query.get('offset', [0])[0], query.get('source', [''])[0]))
            if len(segments) == 4 and segments[:3] == ['api', 'knowledge', 'document']:
                try:
                    document = store.knowledge.document(segments[3])
                except ValueError:
                    # 没有正文：只给安全回执，不把「不存在」说成已移出检索。
                    return self.trash_error(404, 'DOCUMENT_NOT_FOUND', '资料不存在，请重新检索')
                return self.reply(200, document)
            if parts.path == '/api/knowledge/trash':
                return self.knowledge_trash_page()
            if len(segments) == 4 and segments[:3] == ['api', 'knowledge', 'dossier']:
                return self.reply(200, store.knowledge.dossier(segments[3]))
            if len(segments)==2 and segments[0]=='chat-attachments' and re.fullmatch(r'[a-f0-9]{32}\.png',segments[1]):
                return self.serve_file(self.server.assistant.attachment_path(segments[1][:-4]))
            if len(segments)==2 and segments[0]=='generation-references':
                return self.serve_file(self.server.generation.reference_path(segments[1]))
            if len(segments)==4 and segments[:3]==['api','generation','reference']:
                return self.reply(200,self.server.generation.reference_info(segments[3]))
            if parts.path == '/api/generation':
                return self.reply(200, {**self.server.generation.listing(),'preferences':self.server.assistant.media_preferences(),
                    'image_skill':self.server.assistant.image_skill.status()})
            if parts.path in ('/api/generation/backend-health', '/api/generation/backend-inventory'):
                parameters = parse_qs(parts.query, keep_blank_values=True)
                if set(parameters) - {'model'} or len(parameters.get('model', [])) > 1:
                    raise ValueError('部署查询仅接受一个可选的模型编号')
                model = parameters.get('model', [None])[0]
                if model is not None and model not in ('qi21', 'krea2', 'h3'):
                    raise ValueError('请选择已接入的模型 qi21、krea2 或 h3；省略则查询全部')
                operation = (self.server.generation.backend_health if parts.path.endswith('backend-health')
                             else self.server.generation.backend_inventory)
                return self.reply(200, operation(model))
            if parts.path == '/api/generation/lab':
                kind=query.get('kind',['image'])[0]
                if kind not in ('image','video'):raise ValueError('未知生成类型')
                return self.reply(200,{**self.server.generation.listing(limit=100,kind=kind,origin=None if query.get('scope',['lab'])[0]=='all' else 'lab',trash=query.get('trash',['0'])[0]=='1'),
                    'preferences':self.server.assistant.media_preferences(),'image_skill':self.server.assistant.image_skill.status()})
            if len(segments) == 4 and segments[:3] == ['api', 'generation', 'job']:
                return self.reply(200, self.server.generation.public(segments[3]))
            if len(segments) == 2 and segments[0] == 'generation-assets':
                return self.serve_file(self.server.generation.asset(segments[1]))
            if len(segments) == 2 and segments[0] == 'generation-thumbnails':
                return self.serve_file(self.server.generation.thumbnail(segments[1]))
            if len(segments) == 3 and segments[:2] == ['api', 'wechat-preview']:
                return self.reply(200, store.wechat_preview(segments[2], query.get('version', [None])[0]).encode('utf-8'), 'text/html; charset=utf-8')
            if parts.path == '/api/local-publishing/connection':
                return self.reply(200,self.server.local_deliveries.connection_status())
            if parts.path == '/api/local-publishing/jobs':
                return self.reply(200, self.server.local_deliveries.listing(query.get('slug',[''])[0],query.get('version',[''])[0]))
            if parts.path == '/api/local-publishing/preflight':
                return self.reply(200, self.server.local_deliveries.preflight(query.get('slug',[''])[0],{'version':query.get('version',[''])[0],'account':query.get('account',[''])[0]}))
            if len(segments)==4 and segments[:3]==['api','local-publishing','bundle']:
                return self.reply(200,self.server.local_deliveries.bundle(segments[3]))
            if len(segments)==4 and segments[:3]==['api','local-publishing','job']:
                return self.reply(200,self.server.local_deliveries.get(segments[3]))
            if parts.path == '/api/publishing/capabilities':
                from publishing.registry import capabilities
                return self.reply(200, capabilities())
            if parts.path == '/api/schedules':
                return self.reply(200, self.server.schedules.listing())
            if len(segments)==4 and segments[:3]==['api','schedules','run']:
                return self.reply(200,self.server.schedules.get_run(segments[3]))
            if len(segments)==4 and segments[:2]==['api','schedules'] and segments[3]=='outsourced_prompt':
                # 只读：按任务定义拼一份可直接粘给 WorkBuddy 的提示词（impl-outsourced-tasks 新增的唯一路由）。
                return self.reply(200,self.server.schedules.outsourced_prompt(segments[2]))
            if parts.path == '/api/schedules/runs':
                parameters = parse_qs(parts.query, keep_blank_values=True)
                allowed = {'task_id', 'q', 'status', 'page', 'page_size'}
                if set(parameters) - allowed or any(len(values) != 1 for values in parameters.values()):
                    raise ValueError('运行记录查询参数无效或重复')
                page = int(parameters.get('page', ['1'])[0])
                page_size = int(parameters.get('page_size', ['50'])[0])
                if page < 1 or not 1 <= page_size <= 100:
                    raise ValueError('页码至少为 1，每页记录数必须为 1–100')
                return self.reply(200, self.server.schedules.runs(
                    task_id=parameters.get('task_id', [None])[0] or None,
                    q=parameters.get('q', [''])[0],
                    status=parameters.get('status', [None])[0] or None,
                    page=page, page_size=page_size))
            if parts.path == '/api/assistant/settings':
                return self.reply(200, self.server.assistant.settings())
            if parts.path == '/api/assistant':
                return self.reply(200, self.server.assistant.status())
            if parts.path == '/api/assistant/capabilities':
                parameters = parse_qs(parts.query, keep_blank_values=True)
                if set(parameters) - {'kind'} or len(parameters.get('kind', [])) > 1:
                    raise ValueError('能力查询仅接受一个可选的 kind 参数')
                return self.reply(200, self.server.assistant.capabilities(parameters.get('kind', ['all'])[0]))
            if parts.path == '/api/assistant/ssh':
                if parts.query:
                    raise ValueError('SSH 连接测试不接受主机、命令或其他参数')
                return self.reply(200, self.server.assistant.ssh.status())
            if parts.path == '/api/assistant/memory':
                if parts.query:
                    raise ValueError('本机记忆连接测试不接受地址、命令或检索参数')
                return self.reply(200, self.server.assistant.memory_tools.status())
            if len(segments) == 4 and segments[:3] == ['api', 'assistant', 'job']:
                return self.reply(200, self.server.assistant.public_job(segments[3]))
            if len(segments) == 2 and segments[0] == 'assistant-images' and re.fullmatch(r'[a-f0-9]{32}\.png', segments[1]):
                return self.serve_file(store.workspace / 'assistant' / 'images' / segments[1])
            if len(segments) == 3 and segments[0] == 'uploads':
                _, path = store.upload_info(segments[1], segments[2])
                return self.serve_file(path)
            if len(segments) == 2 and segments[0] == 'exports' and re.fullmatch(r'[a-f0-9]{32}\.zip', segments[1]):
                return self.serve_file(store.workspace / 'exports' / segments[1], download=True)
            if parts.path == '/api/library':
                return self.reply(200, store.listing())
            if len(segments) == 3 and segments[:2] == ['api', 'article']:
                return self.reply(200, store.detail(segments[2], query.get('version', [None])[0]))
            if len(segments) == 3 and segments[:2] == ['api', 'compare']:
                return self.reply(200, store.compare(segments[2], query['before'][0], query['after'][0]))
            if segments and segments[0] == 'assets' and len(segments) >= 4:
                package = store.package(segments[1], segments[2])
                relative = '/'.join(segments[3:])
                if not relative.startswith(('images/', 'media/')):
                    raise ValueError('只能读取文章图片')
                path = importer.under(package, relative)
                return self.serve_file(path)
            if parts.path in ('/', '/index.html') or parts.path.startswith('/read/'):
                return self.reply(200, (ROOT / 'public/index.html').read_bytes(), 'text/html; charset=utf-8')
            path = importer.under(ROOT / 'public', parts.path.lstrip('/'))
            if path.is_file():
                return self.reply(200, path.read_bytes(), mimetypes.guess_type(path.name)[0] or 'application/octet-stream')
            return self.reply(404, {'error': '页面不存在'})
        except (OSError, ValueError, KeyError) as exc:
            return self.reply(400, {'error': str(exc)})

    def do_POST(self):
        # 只有重新解析这一条路由记日志。入口每次清空上下文：keep-alive 上一次的状态不会串到下一次。
        drop_id = self._resolve_request()
        # 初始 error_kind 就是「还没发出任何响应」，未预期异常走到 finally 时会原样带出去。
        self._resolve_log = None if drop_id is None else {'started': time.monotonic(), 'status': None,
                                                          'outcome': 'unknown', 'error_kind': 'unexpected',
                                                          'drop_ref': self._drop_ref(drop_id)}
        try:
            self._post_routes()
        finally:
            context, self._resolve_log = self._resolve_log, None
            if context is not None:
                self._log_resolve(context)

    def _post_routes(self):
        allowed_origins = {f'http://127.0.0.1:{self.server.server_port}', f'http://localhost:{self.server.server_port}'}
        if not self.trusted() or self.headers.get('Origin') not in allowed_origins or self.headers.get('X-Article-Studio') != '1':
            return self.reply(403, {'error': '请求来源不受支持'})
        try:
            length = int(self.headers.get('Content-Length', 0))
        except ValueError:
            return self.reply(400, {'error': '无效文件长度'})
        route = urlsplit(self.path)
        segments = route.path.strip('/').split('/')
        if segments == ['api', 'x-official', 'upload']:
            try:
                from publishing.x_official import service
                name = parse_qs(route.query).get('name', [''])[0]
                return self.reply(201, service(self.server).upload_local(self.rfile, length, name))
            except (OSError, ValueError, KeyError) as exc:
                self.close_connection = True
                return self.reply(400, {'error': str(exc)})
        if segments == ['api', 'generation', 'reference-upload']:
            try:
                options = parse_qs(route.query)
                result = self.server.generation.upload_media_reference(options.get('kind', [''])[0],
                    options.get('name', [''])[0], self.rfile, length)
                return self.reply(201, result)
            except (OSError, ValueError, KeyError) as exc:
                self.close_connection = True
                return self.reply(400, {'error': str(exc)})
        if len(segments) == 3 and segments[:2] == ['api', 'upload']:
            try:
                if not 0 < length <= 1024**3:
                    raise ValueError('视频最大 1 GB')
                result = self.server.store.accept_upload(segments[2], parse_qs(route.query).get('name', [''])[0], self.rfile, length)
                return self.reply(201, result)
            except (OSError, ValueError, KeyError) as exc:
                self.close_connection = True
                return self.reply(400, {'error': str(exc)})
        if segments == ['api', 'intake', 'drop']:
            # Same route, two payload styles: JSON for links/text, raw bytes for files.
            try:
                if (self.headers.get('Content-Type') or '').split(';')[0].strip().lower() == 'application/json':
                    if not 0 < length <= 32_000_000:
                        raise ValueError('请求过大或为空')
                    return self.reply(201, self.server.store.creative_intake.receive(json.loads(self.rfile.read(length))))
                options = parse_qs(route.query)
                if set(options) - {'kind', 'name', 'reason'}:
                    raise ValueError('文件投喂只接受 kind、name、reason')
                return self.reply(201, self.server.store.creative_intake.receive_file(
                    options.get('kind', [''])[0], options.get('name', [''])[0], options.get('reason', [''])[0],
                    self.rfile, length))
            except (OSError, ValueError, KeyError) as exc:
                self.close_connection = True
                return self.reply(400, {'error': str(exc)})
        if segments == ['api', 'intake', 'stage']:
            # 选了文件先暂存；点「收下」时链接/文字 + 理由 + 文件一起变成同一条回收项。
            # 和文件投喂同一档大小上限，所以放在通用长度闸门之前。
            try:
                options = parse_qs(route.query)
                if set(options) - {'kind', 'name'}:
                    raise ValueError('文件暂存只接受 kind、name')
                return self.reply(201, self.server.store.creative_intake.stage_file(
                    options.get('kind', [''])[0], options.get('name', [''])[0], self.rfile, length))
            except (OSError, ValueError, KeyError) as exc:
                self.close_connection = True
                return self.reply(400, {'error': str(exc)})
        if not 0 < length <= 32_000_000:
            return self.reply(413, {'error': '请求过大或为空'})
        try:
            payload = json.loads(self.rfile.read(length))
            segments = urlsplit(self.path).path.strip('/').split('/')
            if segments == ['api', 'collect', 'aisa', 'run']:
                return self.reply(202, self.server.store.collect.adapter('aisa').start(payload))
            if segments[:2] == ['api', 'x-official']:
                from publishing.x_official import post_route
                return post_route(self, route.path, payload)
            if segments == ['api','sources','wechat','run']:
                if not isinstance(payload,dict) or set(payload)!={'kind'} or payload['kind'] not in ('clues','radar'):
                    raise ValueError('请选择已有微信线索或雷达分析任务')
                kind = 'wechat_clues' if payload['kind']=='clues' else 'wechat_radar'
                tasks = [t for t in self.server.schedules.listing()['tasks'] if t['kind']==kind]
                if len(tasks)!=1:raise ValueError('需要唯一已配置的微信分析任务；未另建分析器或猜测模型')
                run = self.server.schedules.claim(tasks[0]['id'],'manual')
                return self.reply(202, {'schema':'wechat-source/v1','owner':'wechat-assistant','task_id':tasks[0]['id'],'run_id':run['id']})
            if segments == ['api','sources','wechat','radar-quarantine','retry']:
                # Manual retry only re-queues the quarantined documents at the source;
                # no original text, finding or checkpoint is deleted here. An empty
                # request must never clear the whole ledger, so ids are mandatory.
                ids = payload.get('document_ids') if isinstance(payload, dict) else None
                if not isinstance(payload, dict) or set(payload) - {'document_ids'} or not isinstance(ids, list) or not 1 <= len(ids) <= 200:
                    raise ValueError('隔离重试只接受 1–200 个 document_ids')
                return self.reply(200, self.server.store.wechat_source.retry_radar_quarantined(ids))
            if segments[:2]==['api','deals']:
                from deals_store import DealConflict
                try:
                    deals=self.server.store.deals
                    if segments==['api','deals']:return self.reply(201,deals.save(payload))
                    if segments==['api','deals','from-clue']:return self.reply(201,deals.from_clue(payload))
                    if segments==['api','deals','apply-proposal']:return self.reply(200,deals.apply_proposal(payload))
                    if len(segments)==4 and segments[3]=='scan-wechat':
                        from deals_store import fields
                        fields(payload,('query',));return self.reply(200,deals.scan_evidence(segments[2],payload.get('query','')))
                    if len(segments)==4 and segments[3]=='evidence':return self.reply(200,deals.review_evidence(segments[2],payload))
                    if segments==['api','deals','import-initial']:
                        if payload!={}:raise ValueError('导入参数无效')
                        return self.reply(200,deals.import_initial())
                    if len(segments)==4 and segments[3] in ('receipt','deliverable','publication','publication-edit','void'):
                        return self.reply(200,deals.child(segments[2],segments[3],payload))
                    return self.reply(404,{'error':'商单接口不存在'})
                except DealConflict as exc:return self.reply(409,{'error':str(exc)})
            if len(segments) == 4 and segments[:2] == ['api', 'intake'] and segments[3] == 'resolve':
                if payload:
                    raise ValueError('解析回收项不接受额外字段')
                result = self.server.store.creative_intake.resolve(segments[2])
                # 入库成功就排队一次即时分析；模型调用在后台线程，不拖住这次响应。
                if result.get('status') == 'resolved':
                    try:
                        result['analysis'] = self.server.store.intake_cards.request_analysis(segments[2])
                    except ValueError as exc:
                        result['analysis'] = {'status': 'failed', 'error': str(exc)[:300], 'retryable': True}
                return self.reply(200, result)
            if len(segments) == 4 and segments[:2] == ['api', 'intake'] and segments[3] == 'analyze':
                # 手动重跑（失败后可用，也可用于媒体识别完成后刷新分析）。
                if payload:
                    raise ValueError('重新分析不接受额外字段')
                return self.reply(200, self.server.store.intake_cards.request_analysis(segments[2], force=True))
            if len(segments) == 4 and segments[:2] == ['api', 'intake'] and segments[3] == 'delete':
                # 删除：回收项 + 即时分析 + （最后一条引用时）知识库正文 + 本机媒体文件。
                # 已生成的提示词卡不删，卡库会显示「来源已删除」。
                if payload:
                    raise ValueError('删除回收项不接受额外字段')
                return self.reply(200, self.server.store.creative_intake.delete(segments[2]))
            if segments == ['api', 'intake', 'delete-preview']:
                # 批量删除前的预览：只读，给确认框用（条数、类型分布、正文删几篇留几篇、涉及几张卡）。
                if not isinstance(payload, dict) or set(payload) - {'ids'} or not isinstance(payload.get('ids'), list):
                    raise ValueError('批量删除预览只接受 ids 列表')
                return self.reply(200, self.server.store.creative_intake.delete_preview(payload['ids']))
            if segments == ['api', 'intake', 'delete-many']:
                # 批量删除：逐条走单条删除的同一套规则，一次最多 50 条，逐条回执。
                if not isinstance(payload, dict) or set(payload) - {'ids'} or not isinstance(payload.get('ids'), list):
                    raise ValueError('批量删除只接受 ids 列表')
                return self.reply(200, self.server.store.creative_intake.delete_many(payload['ids']))
            if len(segments) == 4 and segments[:2] == ['api', 'cards'] and segments[3] in ('confirm', 'reject', 'restore'):
                if set(payload) - {'reason'}:
                    raise ValueError('卡片确认只接受 reason')
                return self.reply(200, self.server.store.intake_cards.decide(segments[2], segments[3],
                                                                            payload.get('reason', '')))
            if len(segments) == 4 and segments[:2] == ['api', 'cards'] and segments[3] == 'note':
                if set(payload) - {'note'}:
                    raise ValueError('卡片备注只接受 note')
                return self.reply(200, self.server.store.intake_cards.save_note(segments[2], payload.get('note', '')))
            if len(segments) == 4 and segments[:2] == ['api', 'cards'] and segments[3] == 'delete':
                if payload:
                    raise ValueError('删除卡片不接受额外字段')
                return self.reply(200, self.server.store.intake_cards.delete_card(segments[2]))
            if segments == ['api', 'cards', 'cleanup']:
                if not isinstance(payload, dict) or set(payload) - {'drop_id'}:
                    raise ValueError('清理卡片只接受 drop_id')
                return self.reply(200, self.server.store.intake_cards.delete_deleted_source(payload.get('drop_id', '')))
            if segments == ['api', 'intel', 'recommendations', 'scan']:
                # 扫描 + 解析合并成一步；解析走普通 HTTP，不碰公众号后台。
                if payload:
                    raise ValueError('推荐扫描不接受额外字段')
                counted = self.server.store.intel_recommend.collect()
                resolved = self.server.store.intel_recommend.resolve()
                return self.reply(200, {'scan': counted, 'resolve': resolved,
                                        'listing': self.server.store.intel_recommend.listing()})
            if len(segments) == 4 and segments[:3] == ['api', 'intel', 'recommendations']:
                action = segments[3]
                if set(payload) - {'fakeid'}:
                    raise ValueError('推荐订阅操作只接受 fakeid')
                if action == 'subscribe':
                    match = next((i for i in self.server.store.intel_recommend.listing()['items']
                                  if i['fakeid'] == payload.get('fakeid')), None)
                    if match is None:
                        raise ValueError('这条推荐已不在列表里，请重新扫描')
                    return self.reply(201, self.server.store.intel.subscribe({
                        'fakeid': match['fakeid'], 'name': match['nickname'] or match['fakeid'],
                        'origin': 'recommendation'}))
                if action == 'ignore':
                    return self.reply(200, self.server.store.intel_recommend.ignore(payload.get('fakeid')))
                if action == 'unignore':
                    return self.reply(200, self.server.store.intel_recommend.unignore(payload.get('fakeid')))
                raise ValueError('未知推荐操作')
            if segments[:2] == ['api', 'intel']:
                from intel_wechat_mp import RateLimited
                try:
                    if segments == ['api', 'intel', 'subscriptions']:
                        return self.reply(201, self.server.store.intel.subscribe(payload))
                    if segments == ['api', 'intel', 'identify']:
                        if not isinstance(payload, dict) or set(payload) - {'url'}:
                            raise ValueError('反查公众号只接受一个 url 参数')
                        return self.reply(200, self.server.store.intel.identify_from_link(payload.get('url', '')))
                    if segments == ['api', 'intel', 'run']:
                        return self.reply(202, self.server.store.intel.run(payload))
                    if segments == ['api', 'intel', 'unsubscribe']:
                        if not isinstance(payload, dict) or set(payload) - {'fakeid'}:
                            raise ValueError('解除订阅只接受 fakeid')
                        return self.reply(200, self.server.store.intel.remove_subscription(payload['fakeid']))
                    if len(segments) == 5 and segments[:3] == ['api', 'intel', 'articles'] and segments[4] == 'intake':
                        if payload:
                            raise ValueError('送入回收站不接受额外字段')
                        return self.reply(201, self.server.store.intel.push_to_intake(
                            segments[3], self.server.store.creative_intake))
                    return self.reply(404, {'error': '情报采集接口不存在'})
                except RateLimited as exc:
                    # 限流不是错误语义：明确回执 + 冷却截止时间，前端据此停手。
                    return self.reply(429, {'error': str(exc),
                                            'cooldown_until': exc.until or self.server.store.intel.quota.cooldown_until()})
            if segments == ['api','github','connect']:
                return self.reply(200, self.server.store.github.connect(payload.get('owner')))
            if segments == ['api','github','source']:
                return self.reply(200, self.server.store.github.configure(payload))
            if segments == ['api','github','run']:
                existing=[t for t in self.server.schedules.listing()['tasks'] if t['kind']=='github_clues']
                if existing and existing[0]['running']:raise ValueError('GitHub 任务已在运行，请查看运行记录')
                profile=payload.get('profile_id')
                task={'name':'GitHub 仓库动态 · Pi 分析','kind':'github_clues','body':payload.get('topics') or 'Agent、记忆系统、推理性能、显存优化与本人项目进展',
                      'cwd':str(self.server.store.workspace),'frequency':'interval','minutes':payload.get('minutes',180),'at':'08:00','weekdays':list(range(7)),
                      'ends_at':None,'timeout':900,'enabled':payload.get('enabled',False),'profile_id':profile,'allow_commands':False}
                if existing:task.update(id=existing[0]['id'],revision=existing[0]['revision'])
                saved=self.server.schedules.save(task)
                return self.reply(200, self.server.schedules.control(saved['id'],'run'))
            if segments == ['api', 'feedback', 'import']:
                return self.reply(201, self.server.store.feedback.import_snapshot(payload))
            if len(segments) == 5 and segments[:3] == ['api', 'feedback', 'review'] and segments[4] == 'notes':
                return self.reply(200, self.server.store.feedback.reviews.save(segments[3], payload))
            if len(segments) == 5 and segments[:3] == ['api', 'feedback', 'review'] and segments[4] == 'knowledge':
                return self.reply(201, self.server.store.feedback.reviews.export_note(segments[3]))
            if segments == ['api', 'feedback', 'overview']:
                # Read-only Buffer query, only on an explicit click; it reports the
                # whole organization, never one work.
                return self.reply(201, self.server.store.feedback.reviews.fetch_overview(payload))
            if segments == ['api', 'feedback', 'observe']:
                return self.reply(201, self.server.store.feedback.observations.observe(payload))
            if len(segments) == 5 and segments[:3] == ['api', 'feedback', 'observation'] and segments[4] == 'knowledge':
                return self.reply(201, self.server.store.feedback.observations.export_to_knowledge(segments[3]))
            if len(segments) == 5 and segments[:3] == ['api', 'feedback', 'observation'] and segments[4] == 'bind':
                return self.reply(201, self.server.store.feedback.observations.bind(segments[3], payload))
            if len(segments) == 5 and segments[:3] == ['api', 'feedback', 'snapshot'] and segments[4] == 'knowledge':
                return self.reply(201, self.server.store.feedback.export_to_knowledge(segments[3]))
            if segments[:2]==['api','clues']:
                from events_store import EventConflict
                try:
                    if segments==['api','clues']:return self.reply(201,self.server.store.clues.save(payload))
                    if segments==['api','clues','import-event']:return self.reply(201,self.server.store.clues.import_event(payload.get('event_id')))
                    if segments==['api','clues','from-run']:return self.reply(201,self.server.schedules.deliver_result(payload.get('run_id')))
                    if segments==['api','clues','wechat','run']:
                        if not isinstance(payload,dict) or set(payload)-{'profile_id','enabled'}:raise ValueError('微信分析参数无效')
                        enabled=payload.get('enabled',False)
                        if type(enabled) is not bool:raise ValueError('定时开关无效')
                        scheduler=self.server.schedules
                        profile_id=payload.get('profile_id')
                        scheduler.model_config(profile_id)
                        existing=next((t for t in scheduler.listing()['tasks'] if t['kind']=='wechat_clues'),None)
                        task=dict(name='微信采集与重要线索 · Pi 分析',kind='wechat_clues',body='关注商单、新技术、热点话题、项目进展；暂不能归类但值得关注的信息放其他。保留事实依据与不确定性。',cwd=str(self.server.store.workspace),frequency='interval',minutes=60,at='08:00',timeout=900 if enabled else 300,enabled=enabled,allow_commands=False,profile_id=profile_id,result_destination='runs',wechat_mode='incremental' if enabled else 'once')
                        if existing:
                            if existing['running']:raise ValueError('微信线索分析正在运行，请查看运行记录')
                            task.update(id=existing['id'],revision=existing['revision'],name=existing['name'],body=existing['body'],wechat_review=bool(enabled and existing.get('wechat_review')))
                        saved=scheduler.save(task)
                        run=scheduler.control(saved['id'],'run')
                        return self.reply(201,{'task_id':saved['id'],'run_id':run['id'],'enabled':enabled})
                    if len(segments)==4 and segments[3]=='seen':return self.reply(200,self.server.store.clues.seen(segments[2],payload.get('revision')))
                    if len(segments)==4 and segments[3]=='promote':return self.reply(201,self.server.store.clues.promote(segments[2],payload.get('revision')))
                    raise ValueError('未知线索操作')
                except EventConflict as exc:return self.reply(409,{'error':str(exc)})
            if segments == ['api', 'events'] or (len(segments) == 4 and segments[:2] == ['api', 'events'] and segments[3] == 'actions'):
                from events_store import EventConflict
                try:
                    result = self.server.store.events.save_event(payload) if len(segments) == 2 else self.server.store.events.save_action(segments[2], payload)
                    return self.reply(200 if payload.get('id') else 201, result)
                except EventConflict as exc:
                    return self.reply(409, {'error': str(exc)})
            if segments == ['api', 'knowledge', 'trash', 'preview']:
                return self.knowledge_trash_preview(payload)
            if segments == ['api', 'knowledge', 'trash', 'restore']:
                return self.knowledge_trash_restore(payload)
            if segments == ['api', 'knowledge', 'trash']:
                return self.knowledge_trash_commit(payload)
            if segments == ['api', 'knowledge', 'sync']:
                return self.reply(202, self.server.store.knowledge.start_sync(payload.get('source')))
            if segments == ['api', 'knowledge', 'wechat', 'capture']:
                return self.reply(202, self.server.store.knowledge.start_wechat_capture())
            if segments == ['api', 'knowledge', 'source']:
                return self.reply(200, self.server.store.knowledge.configure_source(payload))
            if segments == ['api', 'knowledge', 'gmail', 'import']:
                return self.reply(201, self.server.store.knowledge.ingest_gmail(payload))
            if segments == ['api', 'knowledge', 'project']:
                return self.reply(201, self.server.store.knowledge.add_project(payload))
            if segments == ['api', 'knowledge', 'note']:
                return self.reply(201, self.server.store.knowledge.add_note(payload))
            if segments == ['api', 'knowledge', 'reference']:
                # 旧键衔接：接入平台识别之前收藏的三家长链接，知识库拿 host+path+query 当主键；
                # 现在去重键换成了 dedupe_url，这个入口不衔接就会把同一链接再收一次时多出一篇。
                # 查找跟回收站入口（intake_store）共用 external_reference 里同一个函数，两个入口
                # 不会分叉。命中旧记录时的返回与 add_reference 自己的判重完全一致（同一篇 +
                # duplicate），知识库页面沿用既有「该原帖已经收藏」文案，不新造说法。
                #
                # 顺序：先校验、再查旧键。校验复用 add_reference 内部同一个
                # external_reference.metadata（不另写一套规则），所以缺标题、标题非字符串、
                # 多了未知字段这些非法请求在旧文档命中之前就已照原样 400，不会被当成收藏成功。
                # metadata 只读 payload、不写库；add_reference 会再跑一次同样的校验再写。
                from external_reference import existing_legacy_document, metadata
                from knowledge_store import now
                knowledge = self.server.store.knowledge
                values, _ = metadata(payload, now())
                legacy = existing_legacy_document(values['url'], knowledge)
                if legacy:
                    document = knowledge.document(legacy)
                    document['duplicate'] = True
                    return self.reply(201, self.reference_receipt(document))
                return self.reply(201, self.reference_receipt(knowledge.add_reference(payload)))
            if segments == ['api', 'knowledge', 'dossier']:
                return self.reply(201, self.server.store.knowledge.save_dossier(payload))
            if segments == ['api', 'knowledge', 'draft']:
                return self.reply(201, self.server.store.knowledge.draft(payload))
            if segments == ['api','assistant','attachments']:
                return self.reply(201,self.server.assistant.upload_chat_image(payload))
            if segments == ['api', 'generation', 'preferences']:
                return self.reply(200, self.server.assistant.save_media_preferences(payload))
            if segments == ['api', 'generation', 'start']:
                return self.reply(202, self.server.assistant.start_media(payload))
            if segments == ['api','generation','references']:
                return self.reply(201,self.server.generation.upload_reference(payload))
            if segments == ['api', 'generation', 'reference-from-job']:
                # The page names a finished image record; the server copies its own file
                # into the reference folder, so nothing is downloaded and re-uploaded.
                return self.reply(201,self.server.generation.reference_from_job(payload.get('job_id')))
            if segments == ['api','generation','manage']:
                return self.reply(200,self.server.generation.manage(payload.get('ids'),payload.get('action')))
            if segments == ['api', 'generation', 'lab', 'start']:
                return self.reply(202,self.server.assistant.start_lab_media(payload))
            if segments == ['api', 'generation', 'health']:
                return self.reply(200, self.server.generation.health(payload['model']))
            if len(segments) == 4 and segments[:3] == ['api', 'generation', 'save']:
                return self.reply(200, self.server.generation.save(segments[3], payload))
            if len(segments) == 5 and segments[:3] == ['api', 'generation', 'job'] and segments[4] == 'cancel':
                # Only the media API owns a cancellable job; the workbench never
                # fabricates a cancellation for a job it cannot see.
                return self.reply(200, self.server.generation.cancel(segments[3]))
            if len(segments) == 5 and segments[:3] == ['api', 'generation', 'job'] and segments[4] == 'reconcile':
                # Re-check an uncertain job under its existing idempotency key; this
                # never creates or resubmits a generation.
                return self.reply(200, self.server.generation.reconcile(segments[3]))
            if segments == ['api', 'schedules', 'save']:
                return self.reply(200, self.server.schedules.save(payload))
            if segments == ['api', 'schedules', 'batch']:
                return self.reply(200, self.server.schedules.batch(payload))
            if len(segments) == 4 and segments[:2] == ['api', 'schedules']:
                if segments[3] == 'publish':
                    return self.reply(201,self.server.schedules.deliver_result(segments[2]))
                if segments[3] == 'cancel':
                    return self.reply(200, self.server.schedules.cancel(segments[2]))
                return self.reply(200, self.server.schedules.control(segments[2], segments[3]))
            if segments == ['api', 'assistant', 'settings']:
                return self.reply(200, self.server.assistant.save_settings(payload))
            if segments == ['api', 'assistant', 'select-model']:
                return self.reply(200, self.server.assistant.select_model(payload))
            if segments == ['api', 'assistant', 'test-settings']:
                return self.reply(200, self.server.assistant.test_settings(payload))
            if segments == ['api', 'assistant', 'send']:
                return self.reply(202, self.server.assistant.start(payload))
            if len(segments)==5 and segments[:3]==['api','assistant','workflows'] and segments[4]=='save':
                return self.reply(200,self.server.assistant.workflows.save(segments[3]))
            if segments == ['api', 'assistant', 'stop']:
                return self.reply(200, self.server.assistant.stop(payload['id']))
            if segments == ['api', 'assistant', 'new-chat']:
                return self.reply(200, self.server.assistant.new_chat())
            if segments==['api','local-publishing','connection']:
                return self.reply(200,self.server.local_deliveries.report_connection(payload))
            if len(segments)==4 and segments[:3]==['api','local-publishing','start']:
                return self.reply(202,self.server.local_deliveries.start(segments[3],payload))
            if len(segments)==4 and segments[:3]==['api','local-publishing','adapt']:
                return self.reply(200,self.server.local_deliveries.adapt(segments[3],payload))
            if len(segments)==4 and segments[:3]==['api','local-publishing','job']:
                return self.reply(200,self.server.local_deliveries.update(segments[3],payload))
            if len(segments) == 3 and segments[:2] == ['api', 'publication-plan']:
                return self.reply(200, self.server.store.publication_plan(segments[2], payload))
            # X posts/threads through the official Buffer CLI. Read-only check first,
            # then an explicitly confirmed submit, then a status read-back.
            if len(segments) == 4 and segments[:3] == ['api', 'buffer', 'channels']:
                return self.reply(200, self.server.store.buffer_channels(segments[3], payload))
            if len(segments) == 4 and segments[:3] == ['api', 'buffer', 'submit']:
                return self.reply(200, self.server.store.buffer_submit(segments[3], payload))
            if len(segments) == 4 and segments[:3] == ['api', 'buffer', 'status']:
                return self.reply(200, self.server.store.buffer_refresh(segments[3], payload))
            if len(segments) == 4 and segments[:3] == ['api', 'buffer', 'media']:
                # Read-only image-host readiness for the confirm dialog. Never any credential.
                return self.reply(200, self.server.store.buffer_media_status())
            if len(segments) == 3 and segments[:2] == ['api', 'export']:
                return self.reply(200, self.server.store.export_delivery(segments[2], payload))
            if len(segments) == 3 and segments[:2] == ['api', 'record']:
                return self.reply(200, self.server.store.record_delivery(segments[2], payload))
            if segments == ['api', 'collection']:
                return self.reply(200, self.server.store.save_collection(payload))
            if segments == ['api', 'create']:
                return self.reply(201, self.server.store.create(payload))
            if segments == ['api', 'scan']:
                self.server.store.scan(force=True)
                return self.reply(200, self.server.store.listing())
            if len(segments) == 3 and segments[:2] == ['api', 'save']:
                return self.reply(200, self.server.store.save(segments[2], payload, from_editor=True))
            if len(segments) == 3 and segments[:2] == ['api', 'reflow']:
                return self.reply(200, self.server.store.reflow(segments[2], payload))
            if len(segments) == 3 and segments[:2] == ['api', 'markdown-signals']:
                return self.reply(200, self.server.store.markdown_signals(segments[2], payload))
            if len(segments) == 3 and segments[:2] == ['api', 'restore']:
                return self.reply(200, self.server.store.restore(segments[2], payload))
            return self.reply(404, {'error': '操作不存在'})
        except Conflict as exc:
            return self.reply(409, {'error': str(exc)})
        except (OSError, ValueError, KeyError, TypeError) as exc:
            return self.reply(400, {'error': str(exc)})


def seed_intel_schedule(schedules, workspace):
    """登记「公众号订阅（每日抓最新）」。幂等：已存在就原样保留，不覆盖黄总的改动。

    初始 enabled=False —— 按合同，必须黄总在页面点「开启」才会每天自动跑。
    """
    try:
        existing = next((t for t in schedules.listing()['tasks'] if t['kind'] == 'intel_wechat_mp'), None)
        if existing:
            return existing
        saved = schedules.save({'name': '公众号订阅（每日抓最新）', 'kind': 'intel_wechat_mp',
            'body': '每天对已订阅的公众号各抓最新 1 页，新文章入库待确认。', 'cwd': str(workspace),
            'frequency': 'daily', 'at': '08:00', 'weekdays': [0, 1, 2, 3, 4, 5, 6],
            'timeout': 1800, 'enabled': False, 'allow_commands': False, 'result_destination': 'runs'})
        return next((t for t in schedules.listing()['tasks'] if t['id'] == saved['id']), saved)
    except (OSError, ValueError, KeyError):
        return None  # 登记失败不阻断工作台启动；页面会如实提示「未登记」


def wire_intake_analysis(server, store):
    """收下即分析的接线：只复用「创意提炼」已配置的模型，不新增服务、不读密钥。

    优先用定时任务「创意提炼（Pi）」选的模型（即使该任务当前处于关闭状态）；
    没有则退回工作台助手当前模型。两者都没配置时返回 None，分析会如实记为可重试失败。
    """
    def model():
        profile_id = None
        try:
            tasks = server.schedules.listing()['tasks']
        except (OSError, ValueError, KeyError):
            tasks = []
        for task in tasks:
            if task['kind'] == 'intake_cards' and task.get('profile_id'):
                profile_id = task['profile_id']
                break
        if profile_id:
            try:
                config = server.schedules.model_config(profile_id)
            except ValueError:
                config = None
            if config:
                config['profile_id'] = profile_id
                return config
        if not server.assistant:
            return None
        try:
            config = server.assistant.config()
        except ValueError:
            return None
        config['profile_id'] = server.assistant.model_document().get('active_profile_id')
        return config

    def busy(config):
        profile_id = (config or {}).get('profile_id')
        if not profile_id:
            return False
        return server.schedules.model_in_use(profile_id)

    cards = store.intake_cards
    cards.model_provider = model
    cards.busy_probe = busy
    return cards


def wire_intake_media(store):
    """图片识别接工作台当前模型配置：复用即时分析那条模型与繁忙探测。

    不新建服务、不新建模型配置、不读密钥：config 只在进程内递给 PiRuntime，
    和聊天看图、定时提炼走同一条 chat/completions 通道。
    """
    from intake_media_recognition import MediaRecognizer, ProfileVision
    cards, intake = store.intake_cards, store.creative_intake
    if intake.media_recognizer is None:
        intake.media_recognizer = MediaRecognizer(
            ProfileVision(cards.analysis_model, cards.model_busy, cards.pi_folder))
    return intake.media_recognizer


class _IntakeLedger:
    """删除回收项时的接线：同步清掉情报侧留下的链接痕迹。

    清三样：link_index 里只剩这一条来源的链接、推荐订阅里的 intake 来源、
    情报文章上的「已进回收站」标记。没跑过情报采集（intel.db 不存在）就不新建库。

    单条 delete：__call__(links, drop_id) 里现算 live（与以前一致）。
    批量 delete_many：先 prepare() 拿一份 live 快照逐条透传（省掉逐条全表扫描）；
    但删除前快照会把「本批才删掉的链接」也算成存在，留下 knowledge 来源与推荐计数残留，
    所以批末再 finalize() 用新算的 live 对本批涉及的 URL 统一对账一次（本批第 2 次扫描）。
    """

    def __init__(self, store):
        self.store = store
        self._live = None
        self._touched = set()

    def _ready(self):
        return (self.store.workspace / 'intel' / 'intel.db').is_file()

    def prepare(self):
        self._live = None
        self._touched = set()
        if not self._ready():
            return None
        self._live = self.store.intel_recommend.live_links()
        return self._live

    def __call__(self, links, drop_id, live=None):
        if not self._ready():
            return
        if live is None:
            live = self._live if self._live is not None else self.store.intel_recommend.live_links()
        for link in (links or []):
            if link:
                self._touched.add(link)
        self.store.intel.forget_intake_drop(drop_id)
        self.store.intel_recommend.forget_intake_source(links, drop_id, live=live)

    def finalize(self):
        if not self._ready() or not self._touched:
            return
        touched, self._touched = self._touched, set()
        self.store.intel_recommend.reconcile_links(touched)


def wire_intake_links(store):
    """把情报侧的删除收尾接到回收项与知识库上，并返回接线对象。

    两处：
    - `creative_intake.link_ledger`：回收项删了要清链接痕迹；
    - `knowledge.document_removed_hook`：知识库正文删了要按**文档**精确清推荐来源行。
      推荐来源的主键是 `(source_kind, source_id, link)`，`source_id` 就是文档 id，
      而清理链接痕迹只按 URL 判 —— 同一 URL 有多份文档时，删掉一份会留下一条孤儿来源，
      推荐次数多算 1（reverify-perf-knowledge-report.md §6）。
    """
    ledger = _IntakeLedger(store)
    store.creative_intake.link_ledger = ledger

    def forget_document(document_id):
        # 情报侧没跑过（intel.db 不存在）就不动：删个回收项不该凭空建一个情报库。
        # 与 _IntakeLedger._ready 同一口径。
        if not (Path(store.workspace) / 'intel' / 'intel.db').is_file():
            return 0, 0
        # 用完整的收尾（清来源行 + 对账该文档的链接），不只是清来源行：
        # 直接删除入口没有批末 finalize 来做 URL 级对账，只清来源行会让 link_index
        # 留下已无真实引用的孤儿行（review-recommend-doc-granularity-report.md §3 L1）。
        return store.intel_recommend.forget_document_cleanup([document_id])

    store.knowledge.document_removed_hook = forget_document
    return ledger


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--workspace', default=str(ROOT / 'workspace'))
    parser.add_argument('--port', type=int, default=8766)
    parser.add_argument('--seed', help='Existing article package to show on first startup')
    args = parser.parse_args()
    store = Store(args.workspace)
    if args.seed and not (store.article('v100-qwen') / 'article.json').exists():
        store.commit('v100-qwen', Path(args.seed), '导入完整图文原稿', '原始文章包')
    store.scan(force=True)
    server = ThreadingHTTPServer(('127.0.0.1', args.port), Handler)
    server.store = store
    store.knowledge.start_watch()
    from publishing.local_jobs import LocalDeliveries
    server.local_deliveries = LocalDeliveries(store)
    from generation.service import Generation
    server.generation = Generation(store)
    config_path = os.environ.get('ARTICLE_STUDIO_MODEL_CONFIG', str(ROOT / 'workspace/private/article-studio-model.json'))
    server.assistant = assistant_module.Assistant(store, content, config_path)
    server.assistant.generation = server.generation
    from scheduled_tasks import ScheduledTasks
    server.schedules = ScheduledTasks(store.workspace, server.assistant, autostart=False)
    store.schedules = server.schedules
    from clues_heat import recompute_heat
    server.schedules.heat_recomputer = lambda cancel, deadline: recompute_heat(store.clues,cancel=cancel,deadline=deadline)
    server.schedules.seed_heat_schedule(store.workspace)
    server.schedules.publisher = store.clues.from_run
    server.schedules.outsourced_clues = store.clues
    server.schedules.wechat_analyzer = store.wechat_source.analyze_clues
    server.schedules.radar_analyzer = store.wechat_source.analyze_radar
    server.schedules.github_analyzer = store.github.analyze
    server.schedules.deals_analyzer = store.deals_review.analyze
    server.schedules.intake_analyzer = store.intake_cards.analyze
    wire_intake_analysis(server, store)
    wire_intake_media(store)
    wire_intake_links(store)
    server.schedules.intel_analyzer = store.intel.analyze_scheduled
    seed_intel_schedule(server.schedules, store.workspace)
    server.schedules.start()
    print(f'图文工作台：http://127.0.0.1:{server.server_port}', flush=True)
    print(f'投递目录：{store.inbox}', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.schedules.close()
        store.knowledge.stop_event.set()
        store.knowledge.capture.close()
        server.generation.close()
        server.server_close()


if __name__ == '__main__':
    main()
