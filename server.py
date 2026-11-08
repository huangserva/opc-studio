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

# Browser asset/API bursts must survive a busy request thread without TCP resets.
ThreadingHTTPServer.request_queue_size = 128

import markdown_blocks

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
# 批量软删除回执里每一组的稳定提示码，前端据此区分「已移出 / 已存在 / 不存在 / 版本冲突 / 本条失败」。
TRASH_GROUP_CODES = {'deleted': 'TRASHED', 'already_trashed': 'ALREADY_TRASHED',
                     'missing': 'DOCUMENT_NOT_FOUND', 'errors': 'STORE_ERROR'}
from work_store import WorkStoreBase, Conflict, uid, importer, content
assistant_spec = importlib.util.spec_from_file_location('assistant_agent', ROOT / 'assistant_agent.py')
assistant_module = importlib.util.module_from_spec(assistant_spec)
assistant_spec.loader.exec_module(assistant_module)
# 样式积木（复核 rich-style）：白名单与映射规则都在 rich_text.py，这里只引入模块，
# SafeHTML 在其之上扩展。markdown_blocks（哨兵/nonce）走的是另一条通道，不受影响。
import rich_text

import html_sanitize
from html_sanitize import (CODE_TAGS, LINE_BREAKING_TAGS, WHOLE_LINE_TAGS, INTERNAL_REL,
                           _FORMAT_TAG, FORMAT_RECEIPT_LIMIT, html_formats,
                           format_receipt, merge_dropped)

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


# 字面量策略留在本模块，兼容现有 server.xxx / studio.xxx monkeypatch。
class SafeHTML(html_sanitize.SafeHTML):
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



def clean_html_full(value):
    return html_sanitize.clean_html_full(value, SafeHTML)


def SafeHTML_collect_rich(value):
    return html_sanitize.SafeHTML_collect_rich(value, SafeHTML)


def clean_html(value):
    """返回 (rich, plain, dropped)：清洗后的正文、纯文本、以及被清洗掉的格式清单。

    dropped 每项是 {'requested','reason','hint'}，助手侧原样透传进回执，
    模型据此如实告诉用户哪一项格式没有生效。safe_html 保持两值返回，
    只读清洗的既有调用点一行都不用改。
    """
    return clean_html_full(value)[:3]



def safe_html(value):
    return clean_html(value)[:2]



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


class Store(WorkStoreBase):
    """Keep HTML-policy lookups in this module, including unregistered studio aliases."""

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
            creation=self.assistant_creation_for_read(slug)
            return {**({'assistant_creation':creation} if creation else {}),
                    'slug': slug, 'title': manifest['title'], 'head': self.meta(slug)['head'],
                    'version': version, 'versions': versions, 'cover': cover, 'blocks': blocks,
                    'studio': self.studio_public(slug, version['id'], content.studio_at(path)), 'deliveries': self.delivery_records(slug),
                    'image_count': len(manifest['body_media']) + bool(cover),
                    'characters': len(manifest['title']) + sum(len(b.get('text', '')) for b in blocks),
                    'intake_path': str(self.inbox / slug), 'package_path': str(path), 'warnings': manifest.get('warnings', [])}

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
        if self.command == 'POST' and status == 201 and urlsplit(self.path).path == '/api/knowledge/reference': __import__('items_store').upsert_from_reference(self.server.store.workspace, data)
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
        self.send_header('Content-Security-Policy', "default-src 'self'; img-src 'self' data: blob:" + (" https:;" if urlsplit(self.path).path in ('/today.html', '/library.html', '/publish.html') else ";") + " style-src 'self' 'unsafe-inline'; script-src 'self'; object-src 'none'; frame-ancestors 'none'")
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

    def do_DELETE(self):
        from items_store import delete_route
        return delete_route(self)

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        return __import__('gmail_view').owner_route(self,self._get_routes)

    def _get_routes(self):
        if not self.trusted():
            return self.reply(403, {'error': '仅限本机访问'})
        parts = urlsplit(self.path)
        segments = [unquote(x) for x in parts.path.split('/') if x]
        query = parse_qs(parts.query)
        store = self.server.store
        try:
            if parts.path == '/gmail' or parts.path.startswith('/api/gmail/'):
                return __import__('gmail_view').get_route(self,parts)
            if parts.path == '/api/clue-inbox' or parts.path.startswith('/api/clue-objects/'):
                return __import__('clue_ingest_api').get_route(self, parts, query)
            if parts.path == '/api/knowledge/upload-candidates':
                return __import__('weknora_upload').get_route(self)
            if parts.path == '/api/settings/wechat-tools': return __import__('settings_view').wechat_tools_route(self)
            if parts.path == '/api/settings/official-accounts': return __import__('settings_view').official_accounts_route(self)
            if parts.path == '/api/settings/overview': return __import__('settings_view').get_route(self, parts)
            if parts.path == '/api/settings/logs': return __import__('settings_view').get_route(self, parts)
            if parts.path == '/api/settings/interest-topics': return __import__('interest_channel').topics_route(self)
            if parts.path.startswith('/api/x-official/'):
                from publishing.x_official import get_route
                return get_route(self, parts, query)
            if len(segments) == 4 and segments[:2] == ['api', 'items'] and segments[3] == 'draft-status':
                from write_from_item import status_route
                return status_route(self, segments[2])
            if parts.path == '/api/publish/review' or parts.path.startswith('/api/publish/review/'):
                from publish_review import get_route
                return get_route(self, parts, query)
            if parts.path == '/api/items' or parts.path.startswith('/api/items/') or parts.path in ('/api/library/search', '/api/library/remote'):
                from items_store import get_route
                return get_route(self, parts, query)
            if parts.path == '/api/today':
                from today_view import page_view
                return self.reply(200, page_view(store.workspace, query))
            if parts.path == '/api/today/item':
                from today_view import item_view
                return self.reply(200, item_view(store.workspace, query))
            if parts.path == '/api/radar/grok-bot':
                from grok_bot_radar import radar_view
                if set(query) - {'hours'} or any(len(values) != 1 for values in query.values()):
                    raise ValueError('只允许一个 hours 参数')
                return self.reply(200, radar_view(store.workspace, query.get('hours', ['48'])[0]))
            if parts.path == '/api/radar/grok-bot/feedback':
                from grok_bot_radar import load_feedback
                return self.reply(200, load_feedback(store.workspace))
            if parts.path == '/api/radar/monitor':
                # 雷达页「每小时新增」列：文件缺失/损坏/过期都在模块内收敛，这里不改既有校验。
                from radar_page_api import monitor_view
                return self.reply(200, monitor_view(store.workspace))
            if parts.path == '/api/video-radar/page':
                # 视频榜页面数据：missing/corrupt 都在模块内收敛成 200，这里不改既有校验。
                from video_radar_page_api import page_view
                return self.reply(200, page_view(store.workspace))
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
            if ((parts.path == '/api/deals') or
                    (parts.path == '/api/deals/proposals/status') or
                    (parts.path == '/api/deals/options') or
                    (parts.path == '/api/deals/review') or
                    (parts.path == '/api/deals/review/report')):
                from deals_api import get_route
                return get_route(self, parts)
            if ((parts.path == '/api/intake') or
                    (parts.path == '/api/intake/analysis-status') or
                    (len(segments) == 4 and segments[:2] == ['api', 'intake'] and segments[3] == 'file') or
                    (len(segments) == 3 and segments[:2] == ['api', 'intake']) or
                    (parts.path == '/api/cards') or
                    (len(segments) == 4 and segments[:2] == ['api', 'cards'] and segments[3] == 'prompt') or
                    (len(segments) == 3 and segments[:2] == ['api', 'cards'])):
                from intake_cards_api import get_route
                return get_route(self, parts)
            if ((len(segments)==3 and segments[:2]==['api','deals'])):
                from deals_api import get_route
                return get_route(self, parts)
            if ((parts.path == '/api/collect/platforms') or
                    (parts.path == '/api/collect/aisa/job') or
                    (parts.path == '/api/collect/status') or
                    (parts.path == '/api/intel/recommendations') or
                    (parts.path == '/api/intel/status') or
                    (parts.path == '/api/intel/subscriptions') or
                    (parts.path == '/api/intel/jobs') or
                    (parts.path == '/api/intel/articles') or
                    (parts.path == '/api/intel/accounts') or
                    (len(segments) == 4 and segments[:3] == ['api', 'intel', 'articles']) or
                    (len(segments) == 4 and segments[:3] == ['api', 'intel', 'jobs'])):
                from intel_collect_api import get_route
                return get_route(self, parts)
            if ((parts.path == '/api/feedback') or
                    (parts.path == '/api/feedback/observations') or
                    (parts.path == '/api/feedback/reviews') or
                    (parts.path == '/api/feedback/overviews') or
                    (len(segments) == 4 and segments[:3] == ['api', 'feedback', 'review']) or
                    (len(segments) == 4 and segments[:3] == ['api', 'feedback', 'observation']) or
                    (parts.path == '/api/feedback/snapshots') or
                    (len(segments) == 4 and segments[:3] == ['api', 'feedback', 'snapshot'])):
                from feedback_api import get_route
                return get_route(self, parts)
            if parts.path == '/api/clues/wechat/status':
                return self.reply(200,store.wechat_source.clue_status())
            if parts.path == '/api/clues':
                return self.reply(200, store.clues.listing(query=query.get('q',[''])[0],category=query.get('category',[''])[0],view=query.get('view',['all'])[0],source=query.get('source',[''])[0],year=query.get('year',[''])[0],offset=query.get('offset',[0])[0],tag=query.get('tag',[''])[0],sort=query.get('sort',['updated'])[0],period=query.get('period',[''])[0],priority=query.get('priority',[''])[0],heat=query.get('heat',[''])[0],**__import__('raw_materials_meta').clue_read_options(parts.query)))
            if len(segments)==3 and segments[:2]==['api','clues']:
                return self.reply(200, store.clues.detail(segments[2],offset=int(query.get('offset',[0])[0])))
            if parts.path == '/api/events':
                return self.reply(200, store.events.list_events(query=query.get('q', [''])[0], view=query.get('view', ['all'])[0], status=query.get('status', [''])[0], limit=int(query.get('limit', [50])[0])))
            if len(segments) == 3 and segments[:2] == ['api', 'events']:
                return self.reply(200, store.events.event(segments[2]))
            if parts.path == '/api/github':
                return self.reply(200, {**store.github.status(), 'tasks':[t for t in self.server.schedules.listing()['tasks'] if t['kind']=='github_clues']})
            if parts.path in ('/api/knowledge/meta', '/api/knowledge/health'):
                return __import__('raw_materials_meta').get_route(self, parts)
            if parts.path == '/api/knowledge':
                return self.reply(200, store.knowledge.status(cached=True))
            if parts.path == '/api/knowledge/search':
                offset = __import__('raw_materials_meta').material_search_offset(parts.query)
                return self.reply(200, store.knowledge.search(query.get('q', [''])[0], query.get('kind', [''])[0], query.get('tag', [''])[0], offset, query.get('source', [''])[0]))
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
            if ((len(segments)==2 and segments[0]=='chat-attachments' and re.fullmatch(r'[a-f0-9]{32}\.png',segments[1]))):
                from schedules_assistant_api import get_route
                return get_route(self, parts)
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
            if parts.path == '/api/generation/lab/aicg3d-presets':
                from generation.aicg3d_compiler import get_presets_and_skills_payload
                return self.reply(200, get_presets_and_skills_payload())
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
            if ((parts.path == '/api/schedules') or
                    (parts.path == '/api/schedules/health') or
                    (len(segments)==4 and segments[:3]==['api','schedules','run']) or
                    (len(segments)==4 and segments[:2]==['api','schedules'] and segments[3]=='outsourced_prompt') or
                    (parts.path == '/api/schedules/runs') or
                    (parts.path == '/api/assistant/settings') or
                    (parts.path == '/api/assistant') or
                    (parts.path == '/api/assistant/capabilities') or
                    (parts.path == '/api/assistant/ssh') or
                    (parts.path == '/api/assistant/memory') or
                    (len(segments) == 4 and segments[:3] == ['api', 'assistant', 'job']) or
                    (len(segments) == 2 and segments[0] == 'assistant-images' and re.fullmatch(r'[a-f0-9]{32}\.png', segments[1]))):
                from schedules_assistant_api import get_route
                return get_route(self, parts)
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
        return __import__('gmail_view').owner_route(self,self._post_routes_unscoped)

    def _post_routes_unscoped(self):
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
            if len(segments)==3 and segments[:2]==['api','gmail']:
                return __import__('gmail_view').post_route(self,payload,route)
            if segments == ['api', 'knowledge', 'upload']:
                return __import__('weknora_upload').post_route(self, payload)
            if segments == ['api', 'settings', 'official-accounts']: return __import__('settings_view').official_accounts_route(self, payload)
            if segments == ['api', 'settings', 'wechat-tools']: return __import__('settings_view').wechat_tools_route(self, payload)
            if segments == ['api', 'settings', 'interest-topics']:
                return __import__('interest_channel').topics_route(self, payload)
            if segments == ['api', 'today', 'feedback']:
                from today_view import feedback_route
                return feedback_route(self, payload)
            if segments == ['api', 'radar', 'grok-bot', 'feedback']:
                from grok_bot_radar import FeedbackCorrupt, save_feedback
                try:
                    record = save_feedback(self.server.store.workspace, payload)
                except FeedbackCorrupt as exc:
                    return self.reply(409, {'error': str(exc), 'code': 'FEEDBACK_CORRUPT'})
                except ValueError as exc:
                    return self.reply(400, {'error': str(exc)})
                except OSError:
                    return self.reply(500, {'error': '反馈未保存，请稍后重试'})
                return self.reply(200, record)
            if segments == ['api', 'radar', 'feedback']:
                # 值得跟/不值得：复用本函数既有的来源校验与 body 限制，非法 payload 一律 400；
                # 反馈文件损坏时返回 409 且原文件一字节不动（FeedbackCorrupt 是 ValueError 的子类，必须先判）。
                from radar_page_api import FeedbackCorrupt, monitor_view, save_feedback
                try:
                    snapshot = monitor_view(self.server.store.workspace)['tweets'].get(
                        str(payload.get('tweet_id'))) if isinstance(payload, dict) else None
                    record = save_feedback(self.server.store.workspace, payload, snapshot=snapshot)
                except FeedbackCorrupt as exc:
                    return self.reply(409, {'error': str(exc), 'code': 'FEEDBACK_CORRUPT'})
                except ValueError as exc:
                    return self.reply(400, {'error': str(exc)})
                except OSError:
                    return self.reply(500, {'error': '反馈未保存，请稍后重试'})
                return self.reply(200, record)
            if segments == ['api', 'video-radar', 'feedback']:
                # 值得跟/不值得/不相关：沿用本函数既有的来源校验与 body 限制，坏 payload 400、
                # 反馈文件损坏 409（FeedbackCorrupt 是 ValueError 子类，必须先判）、写失败 500。
                from video_radar_page_api import FeedbackCorrupt, save_feedback
                try:
                    record = save_feedback(self.server.store.workspace, payload)
                except FeedbackCorrupt as exc:
                    return self.reply(409, {'error': str(exc), 'code': 'FEEDBACK_CORRUPT'})
                except ValueError as exc:
                    return self.reply(400, {'error': str(exc)})
                except OSError:
                    return self.reply(500, {'error': '反馈未保存，请稍后重试'})
                return self.reply(200, record)
            if ((segments == ['api', 'collect', 'aisa', 'run'])):
                from intel_collect_api import post_route
                return post_route(self, segments, payload)
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
            if ((segments[:2]==['api','deals'])):
                from deals_api import post_route
                return post_route(self, segments, payload)
            if ((len(segments) == 4 and segments[:2] == ['api', 'intake'] and segments[3] == 'resolve') or
                    (len(segments) == 4 and segments[:2] == ['api', 'intake'] and segments[3] == 'analyze') or
                    (len(segments) == 4 and segments[:2] == ['api', 'intake'] and segments[3] == 'delete') or
                    (segments == ['api', 'intake', 'delete-preview']) or
                    (segments == ['api', 'intake', 'delete-many']) or
                    (len(segments) == 4 and segments[:2] == ['api', 'cards'] and segments[3] in ('confirm', 'reject', 'restore')) or
                    (len(segments) == 4 and segments[:2] == ['api', 'cards'] and segments[3] == 'note') or
                    (len(segments) == 4 and segments[:2] == ['api', 'cards'] and segments[3] == 'delete') or
                    (segments == ['api', 'cards', 'cleanup'])):
                from intake_cards_api import post_route
                return post_route(self, segments, payload)
            if ((segments == ['api', 'intel', 'recommendations', 'scan']) or
                    (len(segments) == 4 and segments[:3] == ['api', 'intel', 'recommendations']) or
                    (segments[:2] == ['api', 'intel'])):
                from intel_collect_api import post_route
                return post_route(self, segments, payload)
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
            if ((segments == ['api', 'feedback', 'import']) or
                    (len(segments) == 5 and segments[:3] == ['api', 'feedback', 'review'] and segments[4] == 'notes') or
                    (len(segments) == 5 and segments[:3] == ['api', 'feedback', 'review'] and segments[4] == 'knowledge') or
                    (segments == ['api', 'feedback', 'overview']) or
                    (segments == ['api', 'feedback', 'observe']) or
                    (len(segments) == 5 and segments[:3] == ['api', 'feedback', 'observation'] and segments[4] == 'knowledge') or
                    (len(segments) == 5 and segments[:3] == ['api', 'feedback', 'observation'] and segments[4] == 'bind') or
                    (len(segments) == 5 and segments[:3] == ['api', 'feedback', 'snapshot'] and segments[4] == 'knowledge')):
                from feedback_api import post_route
                return post_route(self, segments, payload)
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
                try:__import__('gmail_view').service(self.server).session(self,owner=True)
                except Exception:return self.reply(403, {'error':'邮件导入需要本人本机会话'})
                return self.reply(201, self.server.store.knowledge.ingest_gmail(payload))
            if segments == ['api', 'knowledge', 'project']:
                return self.reply(201, self.server.store.knowledge.add_project(payload))
            if segments == ['api', 'knowledge', 'note']:
                return self.reply(201, self.server.store.knowledge.add_note(payload))
            if len(segments) == 4 and segments[:2] == ['api', 'items'] and segments[3] == 'draft':
                from write_from_item import post_route as draft_route
                return draft_route(self, segments[2], payload)
            if segments == ['api', 'items'] or segments[:2] == ['api', 'items']:
                from items_store import post_route
                return post_route(self, payload)
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
            if ((segments == ['api','assistant','attachments'])):
                from schedules_assistant_api import post_route
                return post_route(self, segments, payload)
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
            if segments == ['api', 'generation', 'lab', 'concat']:
                return self.reply(201, self.server.generation.concat_lab_videos(payload))
            if segments == ['api', 'generation', 'lab', 'aicg3d-compile']:
                from generation.aicg3d_compiler import compile_aicg3d_script
                return self.reply(200, compile_aicg3d_script(
                    story=payload.get('story', ''),
                    skill_id=payload.get('skill_id', 'short-scene-director-lite'),
                    segment_count=payload.get('segment_count', 12),
                    segment_seconds=payload.get('segment_seconds', 5),
                    has_ref_image=bool(payload.get('has_ref_image', True)),
                    character_desc=payload.get('character_desc', ''),
                    model_config=self.server.assistant.config(),
                ))
            if segments == ['api', 'generation', 'lab', 'aicg3d-parse']:
                from generation.aicg3d_compiler import parse_raw_script_payload
                return self.reply(200, parse_raw_script_payload(
                    raw_script=payload.get('raw_script', ''),
                    segment_seconds=payload.get('segment_seconds', 5),
                    has_ref_image=bool(payload.get('has_ref_image', True)),
                ))
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
            if ((segments == ['api', 'schedules', 'save']) or
                    (segments == ['api', 'schedules', 'batch']) or
                    (len(segments) == 4 and segments[:2] == ['api', 'schedules']) or
                    (segments == ['api', 'assistant', 'settings']) or
                    (segments == ['api', 'assistant', 'select-model']) or
                    (segments == ['api', 'assistant', 'test-settings']) or
                    (segments == ['api', 'assistant', 'send']) or
                    (len(segments)==5 and segments[:3]==['api','assistant','workflows'] and segments[4]=='save') or
                    (segments == ['api', 'assistant', 'stop']) or
                    (segments == ['api', 'assistant', 'new-chat']) or
                    (len(segments)==5 and segments[:3]==['api','assistant','created-work'] and segments[4] in ('undo','restore'))):
                from schedules_assistant_api import post_route
                return post_route(self, segments, payload)
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


# 调用时读取当前依赖，保留 server.ROOT 等测试替换的语义。
import server_boot
from server_boot import (seed_intel_schedule, wire_intake_analysis, wire_intake_media,
                         _IntakeLedger, wire_intake_links)


def main():
    def wire_outsourced_clues(server, store):
        server.schedules.outsourced_clues = store.clues

    return server_boot.main(ROOT, Store, Handler, assistant_module, content,
                            wire_outsourced_clues)


if __name__ == '__main__':
    main()
