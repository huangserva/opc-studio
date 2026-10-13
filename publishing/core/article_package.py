#!/usr/bin/env python3
"""Local article packaging, conservative resume planning and draft auditing.

No network, browser launch, credentials, or publish operation. Python 3.10+.
"""
import argparse
import difflib
import hashlib
import html
import io
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from urllib.parse import urlsplit
from zipfile import ZipFile
import xml.etree.ElementTree as ET

from .common import *
from ..platforms.x.article_workflow import draft_id, account_name, init_job, bind, analyse, inspect_job

NS = {
    'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main',
    'a': 'http://schemas.openxmlformats.org/drawingml/2006/main',
    'r': 'http://schemas.openxmlformats.org/officeDocument/2006/relationships',
}
W, R = NS['w'], NS['r']
MARKER = re.compile(r'⟦XARTICLE_IMAGE_\d+⟧')

# 样式积木在离线预览 article.html 里的展示样式；色板/段距/hr/mark 与 style.css 同值（L1），
# 段距用相对单位 em，随字号缩放，与作品页计算样式一致。
RICH_PREVIEW_CSS = (
    '[data-color=red]{color:#d63031}[data-color=blue]{color:#2f6fe4}'
    '[data-color=green]{color:#1e8e4f}[data-color=orange]{color:#d97a1a}'
    '[data-color=gray]{color:#7c8396}[data-color=accent]{color:#4263eb}'
    # 高亮只给底色：mark 要 color:inherit 沿用父级颜色，但 [data-hl] 与 [data-color]
    # 是同优先级的独立积木，写在一起会让黄底盖掉红字（第 13 轮验收 M2）。
    'mark{background:#fff3ad;color:inherit;padding:0 2px;border-radius:3px}'
    '[data-hl=yellow]{background:#fff3ad;padding:0 2px;border-radius:3px}'
    '[data-hl=green]{background:#d3f2d9;padding:0 2px;border-radius:3px}'
    '[data-hl=pink]{background:#ffd9e2;padding:0 2px;border-radius:3px}'
    '[data-size=s]{font-size:14px}[data-size=m]{font-size:16px}'
    '[data-size=l]{font-size:20px}[data-size=xl]{font-size:26px}'
    '[data-align=left]{text-align:left}[data-align=center]{text-align:center}'
    '[data-align=right]{text-align:right}[data-indent="2"]{text-indent:2em}'
    '[data-space=tight]{margin:.4em 0}[data-space=normal]{margin:.9em 0}'
    '[data-space=loose]{margin:1.6em 0}hr{border:none;border-top:2px solid #dfe4ee;margin:30px 0}'
)




















def text_of(element):
    return ''.join(e.text or '' for e in element.iter('{' + W + '}t'))


def plain_html(markup):
    return html.unescape(re.sub('<[^>]*>', '', markup))


class DocxConverter:
    def __init__(self, source, output, cover, font):
        self.source, self.output, self.cover_mode = source, output, cover
        self.font_path = font
        self.blocks, self.images, self.warnings = [], [], []
        self.table_count = 0

    def image(self, raw):
        from PIL import Image
        im = Image.open(io.BytesIO(raw))
        if getattr(im, 'n_frames', 1) > 1:
            raise ValueError('Animated image requires explicit conversion; refusing to discard frames')
        if im.mode in ('RGBA', 'LA', 'P'):
            rgba = im.convert('RGBA')
            im = Image.new('RGBA', rgba.size, 'white')
            im.alpha_composite(rgba)
            im = im.convert('RGB')
        else:
            im = im.convert('RGB')
        name = f'images/figure-{len(self.images):03d}.png'
        im.save(self.output / name, optimize=True)
        item = {'kind': 'image', 'file': name, 'width': im.width, 'height': im.height,
                'source_sha256': digest(raw)}
        self.images.append(item)
        return item

    def inline(self, element):
        """Yield rich text / image tokens in document order, including mixed runs."""
        for child in element:
            tag = child.tag.rsplit('}', 1)[-1]
            if tag == 'r':
                props = child.find('w:rPr', NS)
                wrappers = []
                for key, markup in [('b', 'strong'), ('i', 'em'), ('strike', 's'), ('u', 'u')]:
                    prop = props.find('w:' + key, NS) if props is not None else None
                    if prop is not None and prop.get('{' + W + '}val', '1') not in ('0', 'false', 'off', 'none'):
                        wrappers.append(markup)
                for part in child:
                    local = part.tag.rsplit('}', 1)[-1]
                    if local == 't':
                        s = html.escape(part.text or '')
                    elif local in ('br', 'cr'):
                        s = '<br>'
                    elif local == 'tab':
                        s = '&#9;'
                    elif local == 'drawing':
                        for blip in part.findall('.//a:blip', NS):
                            rid = blip.get('{' + R + '}embed')
                            if not rid or rid not in self.rels:
                                raise ValueError('Linked/missing image cannot be exported offline')
                            target, mode = self.rels[rid]
                            if mode == 'External':
                                raise ValueError('External image needs a local copy')
                            import posixpath
                            member = posixpath.normpath(posixpath.join('word', target)) if not target.startswith('/') else target.lstrip('/')
                            yield self.image(self.archive.read(member))
                        continue
                    else:
                        continue
                    for wrapper in wrappers:
                        s = f'<{wrapper}>{s}</{wrapper}>'
                    yield s
            elif tag == 'hyperlink':
                target, mode = self.rels.get(child.get('{' + R + '}id'), ('', ''))
                anchor = child.get('{' + W + '}anchor')
                url = target or ('#' + anchor if anchor else '')
                safe = not urlsplit(url).scheme or urlsplit(url).scheme.lower() in ('http', 'https', 'mailto')
                for token in self.inline(child):
                    yield f'<a href="{html.escape(url, quote=True)}">{token}</a>' if isinstance(token, str) and url and safe else token
            elif tag in ('smartTag', 'sdtContent'):
                yield from self.inline(child)

    def paragraph(self, element, quote=False):
        style = element.find('w:pPr/w:pStyle', NS)
        style_name = style.get('{' + W + '}val', '') if style is not None else ''
        level = re.fullmatch(r'Heading([1-6])', style_name, re.I)
        tag = 'blockquote' if quote else 'h' + level[1] if level else 'p'
        is_list = element.find('w:pPr/w:numPr', NS) is not None
        if is_list:
            self.warnings.append('Word list paragraphs exported as bullets; inspect numbering/indentation.')
        parts = []

        def flush():
            if parts:
                rich = ''.join(parts)
                prefix = '• ' if is_list else ''
                self.blocks.append({'kind': 'text', 'html': f'<{tag}>{prefix}{rich}</{tag}>',
                                    'text': plain_html(rich), 'display_text': prefix + plain_html(rich)})
                parts.clear()

        for token in self.inline(element):
            if isinstance(token, str):
                parts.append(token)
            else:
                flush()
                self.blocks.append(token)
        flush()

    def table(self, element):
        from PIL import Image, ImageDraw, ImageFont
        if element.findall('.//w:tbl', NS) or element.findall('.//a:blip', NS):
            raise ValueError('Nested tables or images inside tables need explicit conversion')
        rows = [['\n'.join(text_of(p) for p in cell.findall('w:p', NS))
                 for cell in row.findall('w:tc', NS)] for row in element.findall('w:tr', NS)]
        if not rows or not any(rows):
            return
        if len(rows) == 1 and len(rows[0]) == 1:
            for paragraph in element.findall('w:tr/w:tc/w:p', NS):
                self.paragraph(paragraph, quote=True)
            return
        if element.findall('.//w:gridSpan', NS) or element.findall('.//w:vMerge', NS):
            raise ValueError('Merged table cells need explicit conversion; refusing to flatten their meaning')
        font_path = self.font_path
        if not font_path:
            for candidate in ['/System/Library/Fonts/STHeiti Light.ttc', '/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc']:
                if Path(candidate).exists():
                    font_path = candidate
                    break
        if not font_path:
            raise ValueError('A font is required to render tables; supply --font /path/to/CJK-font')
        font = ImageFont.truetype(font_path, 32)
        cols = max(map(len, rows))
        col_width, line_height = 440, 48
        if cols > 12:
            raise ValueError('Table has more than 12 columns; choose a readable layout first')
        wrapped = []
        for row in rows:
            cells = []
            for cell in row:
                lines = []
                for line in cell.split('\n'):
                    current = ''
                    for char in line:
                        if current and font.getlength(current + char) > col_width - 40:
                            lines.append(current)
                            current = ''
                        current += char
                    lines.append(current)
                cells.append(lines)
            wrapped.append(cells)
        heights = [max(len(cell) for cell in row) * line_height + 40 for row in wrapped]
        if sum(heights) > 25000:
            raise ValueError('Table image would be too tall; split the table explicitly')
        im = Image.new('RGB', (cols * col_width, sum(heights)), 'white')
        draw, y = ImageDraw.Draw(im), 0
        for ri, (row, height) in enumerate(zip(wrapped, heights)):
            draw.rectangle((0, y, im.width, y + height), fill='#edf2f7' if ri == 0 else 'white')
            for ci, lines in enumerate(row):
                for li, line in enumerate(lines):
                    draw.text((ci * col_width + 20, y + 20 + li * line_height), line, font=font, fill='#17202a')
                draw.line((ci * col_width, y, ci * col_width, y + height), fill='#cbd5e1', width=2)
            y += height
            draw.line((0, y - 1, im.width, y - 1), fill='#cbd5e1', width=2)
        self.table_count += 1
        name = f'images/table-{self.table_count:03d}.png'
        im.save(self.output / name, optimize=True)
        self.blocks.append({'kind': 'table', 'file': name, 'rows': rows, 'text': text_of(element),
                            'width': im.width, 'height': im.height})

    def run(self, title_override):
        with ZipFile(self.source) as archive:
            self.archive = archive
            rels = ET.fromstring(archive.read('word/_rels/document.xml.rels'))
            self.rels = {e.get('Id'): (e.get('Target'), e.get('TargetMode', 'Internal')) for e in rels}
            body = ET.fromstring(archive.read('word/document.xml')).find('w:body', NS)
            if body is None:
                raise ValueError('No Word document body')
            for tag in ['ins', 'del', 'moveFrom', 'moveTo', 'txbxContent', 'pict', 'footnoteReference', 'endnoteReference', 'altChunk']:
                if body.findall('.//w:' + tag, NS):
                    raise ValueError(f'Unsupported Word content ({tag}); resolve/export it explicitly first')
            if body.findall('.//{http://schemas.openxmlformats.org/officeDocument/2006/math}oMath'):
                raise ValueError('Native equations require explicit rendering first')
            original_text = text_of(body)
            if MARKER.search(original_text):
                raise ValueError('Source already contains importer markers')
            paragraphs = [p for p in body.findall('w:p', NS) if text_of(p).strip()]
            if not paragraphs:
                raise ValueError('No text paragraph to use as a title')
            title_element = paragraphs[0]
            if title_element.findall('.//a:blip', NS):
                raise ValueError('Title paragraph also contains an image; separate them first')
            source_title = text_of(title_element)
            title = title_override or source_title
            self.warnings.append('First nonempty body paragraph used as title; review title before upload.')
            for element in body:
                if element is title_element:
                    continue
                if element.tag == '{' + W + '}p':
                    self.paragraph(element)
                elif element.tag == '{' + W + '}tbl':
                    self.table(element)
                elif element.tag != '{' + W + '}sectPr' and text_of(element):
                    raise ValueError('Unsupported body container: ' + element.tag)
            if normalized(source_title + ''.join(b.get('text', '') for b in self.blocks)) != normalized(original_text):
                raise ValueError('Source text comparison failed; package not created')
            cover = None
            if self.cover_mode == 'first':
                if not self.images:
                    raise ValueError('--cover first requires at least one image')
                cover = self.images[0]
                self.blocks.remove(cover)
            return title, cover, len(original_text)


class NativeConverter:
    """Shared article model for chat/editor JSON and Markdown. No network fetches."""
    def __init__(self, source, output, cover_mode, font):
        self.source, self.output, self.cover_mode = source, output, cover_mode
        self.media = DocxConverter(source, output, 'none', font)
        self.blocks, self.warnings = [], []

    def image(self, src):
        if not isinstance(src, str) or not src.strip():
            raise ValueError('Image src must be a local file path')
        if urlsplit(src).scheme or src.startswith('//'):
            raise ValueError('Remote image requires a local asset first: ' + src)
        path = Path(src)
        if not path.is_absolute():
            path = self.source.parent / path
        item = self.media.image(path.read_bytes())
        return item

    def text(self, rich, plain, tag='p'):
        if MARKER.search(plain):
            raise ValueError('Source contains reserved importer markers')
        self.blocks.append({'kind': 'text', 'html': f'<{tag}>{rich}</{tag}>',
                            'text': plain, 'display_text': plain})

    def table(self, rows):
        if not isinstance(rows, list) or not rows or not all(isinstance(r, list) and r for r in rows):
            raise ValueError('Table rows must be a nonempty array of nonempty arrays')
        if len({len(r) for r in rows}) != 1:
            raise ValueError('Table rows must have equal lengths')
        table = ET.Element('{' + W + '}tbl')
        for row in rows:
            tr = ET.SubElement(table, '{' + W + '}tr')
            for cell in row:
                if not isinstance(cell, str):
                    raise ValueError('Table cells must be strings')
                tc = ET.SubElement(tr, '{' + W + '}tc')
                p = ET.SubElement(tc, '{' + W + '}p')
                r = ET.SubElement(p, '{' + W + '}r')
                ET.SubElement(r, '{' + W + '}t').text = cell
        before = len(self.media.blocks)
        self.media.table(table)
        self.blocks.extend(self.media.blocks[before:])

    def spans(self, block):
        spans = block.get('spans', [{'text': block.get('text', '')}])
        if not isinstance(spans, list):
            raise ValueError('spans must be an array')
        plain, rich = [], []
        for span in spans:
            text = span['text']
            if not isinstance(text, str):
                raise ValueError('Span text must be a string')
            plain.append(text)
            value = html.escape(text).replace('\n', '<br>')
            for key, tag in [('bold', 'strong'), ('italic', 'em'), ('strike', 's'), ('code', 'code')]:
                if span.get(key):
                    value = f'<{tag}>{value}</{tag}>'
            if span.get('href'):
                href = span['href']
                if urlsplit(href).scheme.lower() not in ('http', 'https', 'mailto'):
                    raise ValueError('Link must use http, https or mailto')
                value = '<a href="' + html.escape(href, quote=True) + '">' + value + '</a>'
            rich.append(value)
        return ''.join(rich), ''.join(plain)

    def json(self, title_override):
        article = read_json(self.source)
        if article.get('schema_version') != 1 or not isinstance(article.get('blocks'), list):
            raise ValueError('Expected article JSON with schema_version 1 and blocks array')
        title = title_override or article.get('title')
        if not isinstance(title, str) or not title.strip():
            raise ValueError('Article title is required')
        cover = self.image(article['cover']) if article.get('cover') else None
        for block in article['blocks']:
            kind = block['type']
            if kind in ('paragraph', 'heading', 'quote'):
                rich, plain = self.spans(block)
                level = block.get('level', 2)
                if kind == 'heading' and (not isinstance(level, int) or not 1 <= level <= 6):
                    raise ValueError('Heading level must be 1..6')
                self.text(rich, plain, 'h' + str(level) if kind == 'heading' else 'blockquote' if kind == 'quote' else 'p')
            elif kind == 'code':
                self.text('<code>' + html.escape(block['text']) + '</code>', block['text'], 'pre')
            elif kind == 'image':
                self.blocks.append(self.image(block['src']))
                if block.get('caption'):
                    self.text(html.escape(block['caption']), block['caption'])
            elif kind == 'table':
                self.table(block['rows'])
            elif kind == 'list':
                for i, item in enumerate(block['items'], start=block.get('start', 1)):
                    rich, plain = self.spans({'text': item} if isinstance(item, str) else item)
                    prefix = str(i) + '. ' if block.get('ordered') else '• '
                    self.text(prefix + rich, prefix + plain)
            else:
                raise ValueError('Unsupported article block type: ' + str(kind))
        return self.finish(title, cover)

    def markdown(self, title_override):
        from markdown_it import MarkdownIt
        parser = MarkdownIt('commonmark', {'html': False}).enable('table').enable('strikethrough')
        tokens = parser.parse(self.source.read_text(encoding='utf-8'))
        title = title_override
        if len(tokens) >= 3 and tokens[0].type == 'heading_open' and tokens[0].tag == 'h1':
            title = title or self.inline_plain(tokens[1].children)
            tokens = tokens[3:]
        if not title:
            raise ValueError('Markdown needs a leading # title or --title')
        contexts, lists, i = [], [], 0
        while i < len(tokens):
            token = tokens[i]
            if token.type == 'table_open':
                rows, row = [], None
                i += 1
                while i < len(tokens) and tokens[i].type != 'table_close':
                    cell = tokens[i]
                    if cell.type == 'tr_open':
                        row = []
                        rows.append(row)
                    elif cell.type == 'inline':
                        if any(c.type == 'image' for c in cell.children or []):
                            raise ValueError('Images inside Markdown tables need separate blocks')
                        row.append(self.inline_plain(cell.children))
                    i += 1
                self.table(rows)
                self.warnings.append('Markdown table cell formatting is rendered as plain text in table images.')
            elif token.type in ('bullet_list_open', 'ordered_list_open'):
                lists.append({'ordered': token.type == 'ordered_list_open', 'next': int(token.attrGet('start') or 1), 'prefix': ''})
            elif token.type in ('bullet_list_close', 'ordered_list_close'):
                lists.pop()
            elif token.type == 'list_item_open':
                lists[-1]['prefix'] = (str(lists[-1]['next']) + '. ') if lists[-1]['ordered'] else '• '
                lists[-1]['next'] += 1
            elif token.type in ('heading_open', 'paragraph_open', 'blockquote_open'):
                contexts.append(token.tag)
            elif token.type in ('heading_close', 'paragraph_close', 'blockquote_close'):
                contexts.pop()
            elif token.type == 'inline':
                children = token.children or []
                images = [c for c in children if c.type == 'image']
                if images:
                    others = [c for c in children if c.type != 'image' and (c.type != 'text' or c.content.strip()) and c.type not in ('softbreak', 'hardbreak')]
                    if others:
                        raise ValueError('Place Markdown images on their own paragraph, separate from text')
                    for image in images:
                        self.blocks.append(self.image(image.attrGet('src')))
                else:
                    prefix = lists[-1]['prefix'] if lists else ''
                    if lists:
                        lists[-1]['prefix'] = ''
                        self.warnings.append('Markdown lists rendered with explicit labels; inspect nested indentation.')
                    tag = 'blockquote' if 'blockquote' in contexts else contexts[-1] if contexts else 'p'
                    self.text(prefix + parser.renderer.renderInline(children, parser.options, {}), prefix + self.inline_plain(children), tag)
            elif token.type in ('fence', 'code_block'):
                self.text('<code>' + html.escape(token.content) + '</code>', token.content, 'pre')
            elif token.type == 'hr':
                self.text('—', '—')
            elif token.type != 'list_item_close':
                raise ValueError('Unsupported Markdown token: ' + token.type)
            i += 1
        return self.finish(title, None)

    @staticmethod
    def inline_plain(children):
        return ''.join('\n' if c.type in ('softbreak', 'hardbreak') else c.content if c.type in ('text', 'code_inline', 'html_inline') else '' for c in children or [])

    def finish(self, title, cover):
        if self.cover_mode == 'first' and not cover:
            cover = next((b for b in self.blocks if b['kind'] == 'image'), None)
            if cover is None:
                raise ValueError('--cover first requires a body image')
            self.blocks.remove(cover)
        self.warnings.extend(self.media.warnings)
        return title, cover, len(title) + sum(len(b.get('text', '')) for b in self.blocks)


def finalize_package(out, title, cover, blocks, source_hash, warnings, source_text_chars=None):
    media = []
    for block in blocks:
        if block['kind'] in ('image', 'table'):
            block['id'] = f'media-{len(media) + 1:03d}'
            block['marker'] = f'⟦XARTICLE_IMAGE_{len(media) + 1:02d}⟧'
            media.append(block)
    for item in media + ([cover] if cover else []):
        content = under(out, item['file']).read_bytes()
        item['sha256'], item['bytes'] = digest(content), len(content)
        if len(content) > 5_000_000:
            warnings.append(f"Large image {item['file']} ({len(content)} bytes); check target platform requirements.")
    paste = ''.join(b['html'] if b['kind'] == 'text' else '<p>' + b['marker'] + '</p>' for b in blocks)
    body_html = ''.join(b['html'] if b['kind'] == 'text' else '<p><img src="' + html.escape(b['file'], quote=True) + '" alt=""></p>' for b in blocks)
    cover_html = '<img src="' + html.escape(cover['file'], quote=True) + '" alt="封面">' if cover else ''
    preview = '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>' + html.escape(title) + '</title><style>body{max-width:900px;margin:40px auto;padding:24px;font:16px/1.8 system-ui}img{max-width:100%}blockquote{background:#f3f5f7;padding:16px}h1{line-height:1.4}' + RICH_PREVIEW_CSS + '</style><h1>' + html.escape(title) + '</h1>' + cover_html + body_html + '</html>'
    files = {'paste-body.html': paste, 'article.html': preview,
             'body-text.txt': '\n'.join(b.get('display_text', b.get('text', '')) for b in blocks if b['kind'] == 'text')}
    for name, data in files.items():
        (out / name).write_text(data, encoding='utf-8')
    manifest = {'schema_version': 1, 'title': title, 'source_sha256': source_hash,
                'source_text_chars': source_text_chars, 'cover': cover, 'blocks': blocks,
                'body_media': media, 'warnings': sorted(set(warnings)),
                'generated_files': {name: digest((out / name).read_bytes()) for name in files}}
    manifest['package_id'] = digest(canonical(manifest))
    write_json(out / 'manifest.json', manifest)
    return {'package': str(out), 'package_id': manifest['package_id'], 'body_images': len(media),
            'cover': bool(cover), 'warnings': manifest['warnings']}


def build(args):
    source, dest = Path(args.source).resolve(), Path(args.out).resolve()
    if dest.exists():
        raise ValueError('Output directory already exists; choose a new directory to preserve snapshots')
    dest.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix='.article-build-', dir=dest.parent))
    try:
        (staging / 'images').mkdir()
        if args.command == 'prepare':
            extension = source.suffix.lower()
            if extension == '.docx':
                converter = DocxConverter(source, staging, args.cover, args.font)
                title, cover, chars = converter.run(args.title)
            elif extension in ('.md', '.markdown', '.json'):
                converter = NativeConverter(source, staging, args.cover, args.font)
                title, cover, chars = converter.json(args.title) if extension == '.json' else converter.markdown(args.title)
            else:
                raise ValueError('Supported inputs: article JSON, Markdown (.md/.markdown), DOCX')
            result = finalize_package(staging, title, cover, converter.blocks, digest(source.read_bytes()), converter.warnings, chars)
        else:
            old = read_json(source / 'manifest.json')
            cover, blocks = old.get('cover'), old['blocks']
            for item in [b for b in blocks if b['kind'] != 'text'] + ([cover] if cover else []):
                target = under(staging, item['file'])
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(under(source, item['file']), target)
            result = finalize_package(staging, old['title'], cover, blocks,
                                      old.get('source_sha256', digest(canonical(old))),
                                      old.get('warnings', []) + ['Imported a prepared package; source DOCX was not rechecked.'], old.get('source_text_chars'))
        staging.rename(dest)
        result['package'] = str(dest)
        return result
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def load_package(path):
    path = Path(path)
    manifest = read_json(path / 'manifest.json')
    unsigned = {k: v for k, v in manifest.items() if k != 'package_id'}
    if manifest.get('schema_version') != 1 or manifest.get('package_id') != digest(canonical(unsigned)):
        raise ValueError('Manifest changed or unsupported version; prepare a new package')
    for item in manifest['body_media'] + ([manifest['cover']] if manifest['cover'] else []):
        if digest(under(path, item['file']).read_bytes()) != item['sha256']:
            raise ValueError('Media changed: ' + item['file'])
    for name, expected in manifest['generated_files'].items():
        if digest(under(path, name).read_bytes()) != expected:
            raise ValueError('Generated content changed: ' + name)
    return manifest










def compare(args):
    before, after = load_package(args.before), load_package(args.after)
    a = before['title'] + '\n' + '\n'.join(b.get('text', '') for b in before['blocks'])
    b = after['title'] + '\n' + '\n'.join(b.get('text', '') for b in after['blocks'])
    a_media = [m['sha256'] for m in before['body_media']]
    b_media = [m['sha256'] for m in after['body_media']]
    diff = ''.join(difflib.unified_diff(a.splitlines(True), b.splitlines(True), fromfile='before', tofile='after'))
    return {'before': before['package_id'], 'after': after['package_id'], 'text_diff': diff,
            'media_sequence_changed': a_media != b_media,
            'cover_changed': (before['cover'] or {}).get('sha256') != (after['cover'] or {}).get('sha256'),
            'formatting_changed': [x.get('html') for x in before['blocks']] != [x.get('html') for x in after['blocks']]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('prepare', help='Article JSON, Markdown or DOCX to a new immutable article package')
    p.add_argument('source'); p.add_argument('--out', required=True)
    p.add_argument('--cover', choices=['first', 'none'], default='none')
    p.add_argument('--title'); p.add_argument('--font')
    p = sub.add_parser('adopt', help='Migrate a previously prepared manifest/images package')
    p.add_argument('source'); p.add_argument('--out', required=True)
    p = sub.add_parser('check', help='Verify local package integrity')
    p.add_argument('package')
    p = sub.add_parser('init', help='Create a local job without contacting X')
    p.add_argument('--package', required=True); p.add_argument('--job', required=True)
    p.add_argument('--account', required=True); p.add_argument('--draft-url')
    p = sub.add_parser('bind', help='Bind an existing local job to one draft')
    p.add_argument('--job', required=True); p.add_argument('--draft-url', required=True)
    for name in ['plan', 'audit']:
        p = sub.add_parser(name, help='Inspect fresh UI evidence; audit also saves a checkpoint')
        p.add_argument('--job', required=True); p.add_argument('--snapshot', required=True)
    p = sub.add_parser('diff', help='Compare two immutable packages')
    p.add_argument('before'); p.add_argument('after')
    args = parser.parse_args()
    try:
        if args.command in ('prepare', 'adopt'):
            result = build(args)
        elif args.command == 'check':
            m = load_package(args.package)
            result = {'valid': True, 'package_id': m['package_id'], 'body_images': len(m['body_media'])}
        elif args.command == 'init':
            result = init_job(args)
        elif args.command == 'bind':
            result = bind(args)
        elif args.command in ('plan', 'audit'):
            result = inspect_job(args)
        else:
            result = compare(args)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if args.command == 'audit' and result['status'] != 'complete_verified':
            return 2
        return 0
    except (ValueError, KeyError, OSError, ET.ParseError, ImportError) as exc:
        print(json.dumps({'error': str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
