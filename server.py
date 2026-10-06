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
import tempfile
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit, parse_qs, unquote

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from publishing.core import article_package as importer
content_spec = importlib.util.spec_from_file_location('content_store', ROOT / 'content_store.py')
content = importlib.util.module_from_spec(content_spec)
content_spec.loader.exec_module(content)
assistant_spec = importlib.util.spec_from_file_location('assistant_agent', ROOT / 'assistant_agent.py')
assistant_module = importlib.util.module_from_spec(assistant_spec)
assistant_spec.loader.exec_module(assistant_module)


class SafeHTML(HTMLParser):
    allowed = {'p', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'strong', 'b', 'em', 'i', 's', 'u', 'br', 'blockquote', 'pre', 'code', 'a', 'ul', 'ol', 'li', 'div', 'span'}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.text, self.blocked = [], [], 0

    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style', 'iframe', 'object'):
            self.blocked += 1
        if self.blocked or tag not in self.allowed:
            return
        extra = ''
        if tag == 'a':
            href = dict(attrs).get('href', '')
            if urlsplit(href).scheme.lower() in ('https', 'http', 'mailto'):
                extra = ' href="' + escape(href, quote=True) + '" rel="noopener noreferrer"'
        self.parts.append('<' + tag + extra + '>')
        if tag == 'br':
            self.text.append('\n')

    def handle_endtag(self, tag):
        if tag in ('script', 'style', 'iframe', 'object'):
            self.blocked = max(0, self.blocked - 1)
            return
        if not self.blocked and tag in self.allowed and tag != 'br':
            self.parts.append('</' + tag + '>')
            if tag in ('p', 'div', 'li', 'h1', 'h2', 'h3', 'blockquote'):
                self.text.append('\n')

    def handle_data(self, data):
        if not self.blocked:
            self.parts.append(escape(data))
            self.text.append(data)


def safe_html(value):
    parser = SafeHTML()
    parser.feed(value)
    return ''.join(parser.parts), ''.join(parser.text).rstrip('\n')


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

    @property
    def knowledge(self):
        with self.lock:
            if self._knowledge is None:
                from knowledge_store import Knowledge
                self._knowledge = Knowledge(self)
            return self._knowledge

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

    def save(self, slug, payload):
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
                if 'blocks' in payload:
                    if not isinstance(payload['blocks'], list) or len(payload['blocks']) > 5000:
                        raise ValueError('正文格式不正确或段落过多')
                    blocks = []
                    for item in payload['blocks']:
                        if 'index' in item:
                            index = item['index']
                            if not isinstance(index, int) or not 0 <= index < len(manifest['blocks']):
                                raise ValueError('无效正文引用')
                            blocks.append(copy.deepcopy(manifest['blocks'][index]))
                        elif item.get('kind') == 'text':
                            rich, plain = safe_html(item.get('html', ''))
                            if plain.strip():
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
                    for key, markup in payload.get('text_blocks', {}).items():
                        index = int(key)
                        if not 0 <= index < len(blocks) or blocks[index]['kind'] != 'text':
                            raise ValueError('无效正文段落')
                        rich, plain = safe_html(markup)
                        if rich != safe_html(blocks[index]['html'])[0]:
                            blocks[index].update(html=rich, text=plain, display_text=plain)
                studio = self.normalize_studio(slug, payload.get('studio'), old, out)
                if title == manifest['title'] and blocks == manifest['blocks'] and studio == content.studio_at(old):
                    return {'version': payload['base_version'], 'unchanged': True}
                importer.finalize_package(out, title, copy.deepcopy(manifest['cover']), blocks,
                                          importer.digest(importer.canonical({'title': title, 'blocks': blocks})), manifest.get('warnings', []))
                content.seal(out, studio, importer)
                rev = self.commit(slug, out, payload.get('summary', '').strip()[:180] or '编辑图文', '工作台编辑')
            return {'version': rev, 'unchanged': rev == payload['base_version']}

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
    def log_message(self, *args):
        pass

    def trusted(self):
        return self.headers.get('Host') in (f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}')

    def reply(self, status, data, mime='application/json; charset=utf-8'):
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
            if parts.path == '/api/sources/wechat/status':
                return self.reply(200, store.wechat_source.status())
            if parts.path == '/api/sources/wechat/results':
                if set(query) - {'kind', 'q', 'category', 'offset', 'limit'} or any(len(v) != 1 for v in query.values()):
                    raise ValueError('微信来源查询参数无效')
                return self.reply(200, store.wechat_source.results(kind=query.get('kind',['clues'])[0], query=query.get('q',[''])[0], category=query.get('category',[''])[0], offset=query.get('offset',[0])[0], limit=query.get('limit',[30])[0]))
            if parts.path == '/api/deals':
                return self.reply(200, store.deals.listing(month=query.get('month',[''])[0], q=query.get('q',[''])[0], stage=query.get('stage',[''])[0], payment=query.get('payment',[''])[0], archived=query.get('archived',['0'])[0]=='1'))
            if parts.path == '/api/deals/proposals/status':
                params=parse_qs(parts.query,keep_blank_values=True)
                if set(params)-{'ids'} or len(params.get('ids',[]))!=1:raise ValueError('建议状态参数无效')
                return self.reply(200,self.server.store.deals.proposal_status(params['ids'][0].split(',') if params['ids'][0] else []))
            if parts.path == '/api/deals/options':
                return self.reply(200, store.deals.options())
            if len(segments)==3 and segments[:2]==['api','deals']:
                return self.reply(200, store.deals.detail(segments[2]))
            if parts.path == '/api/feedback':
                return self.reply(200, store.feedback.bindings())
            if parts.path == '/api/feedback/observations':
                return self.reply(200, store.feedback.observations.listing())
            if len(segments) == 4 and segments[:3] == ['api', 'feedback', 'observation']:
                return self.reply(200, store.feedback.observations.get(segments[3]))
            if parts.path == '/api/feedback/snapshots':
                return self.reply(200, store.feedback.listing(slug=query.get('slug', [''])[0], delivery_id=query.get('delivery_id', [''])[0], limit=int(query.get('limit', [50])[0])))
            if len(segments) == 4 and segments[:3] == ['api', 'feedback', 'snapshot']:
                return self.reply(200, store.feedback.snapshot(segments[3]))
            if parts.path == '/api/clues/wechat/status':
                return self.reply(200,store.wechat_source.clue_status())
            if parts.path == '/api/clues':
                return self.reply(200, store.clues.listing(query=query.get('q',[''])[0],category=query.get('category',[''])[0],view=query.get('view',['all'])[0],source=query.get('source',[''])[0],year=query.get('year',[''])[0],offset=query.get('offset',[0])[0],tag=query.get('tag',[''])[0],sort=query.get('sort',['updated'])[0],period=query.get('period',[''])[0],priority=query.get('priority',[''])[0]))
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
                return self.reply(200, store.knowledge.document(segments[3]))
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
        allowed_origins = {f'http://127.0.0.1:{self.server.server_port}', f'http://localhost:{self.server.server_port}'}
        if not self.trusted() or self.headers.get('Origin') not in allowed_origins or self.headers.get('X-Article-Studio') != '1':
            return self.reply(403, {'error': '请求来源不受支持'})
        try:
            length = int(self.headers.get('Content-Length', 0))
        except ValueError:
            return self.reply(400, {'error': '无效文件长度'})
        route = urlsplit(self.path)
        segments = route.path.strip('/').split('/')
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
        if not 0 < length <= 32_000_000:
            return self.reply(413, {'error': '请求过大或为空'})
        try:
            payload = json.loads(self.rfile.read(length))
            segments = urlsplit(self.path).path.strip('/').split('/')
            if segments == ['api','sources','wechat','run']:
                if not isinstance(payload,dict) or set(payload)!={'kind'} or payload['kind'] not in ('clues','radar'):
                    raise ValueError('请选择已有微信线索或雷达分析任务')
                kind = 'wechat_clues' if payload['kind']=='clues' else 'wechat_radar'
                tasks = [t for t in self.server.schedules.listing()['tasks'] if t['kind']==kind]
                if len(tasks)!=1:raise ValueError('需要唯一已配置的微信分析任务；未另建分析器或猜测模型')
                run = self.server.schedules.claim(tasks[0]['id'],'manual')
                return self.reply(202, {'schema':'wechat-source/v1','owner':'wechat-assistant','task_id':tasks[0]['id'],'run_id':run['id']})
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
                return self.reply(201, self.server.store.knowledge.add_reference(payload))
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
                return self.reply(200, self.server.store.save(segments[2], payload))
            if len(segments) == 3 and segments[:2] == ['api', 'restore']:
                return self.reply(200, self.server.store.restore(segments[2], payload))
            return self.reply(404, {'error': '操作不存在'})
        except Conflict as exc:
            return self.reply(409, {'error': str(exc)})
        except (OSError, ValueError, KeyError, TypeError) as exc:
            return self.reply(400, {'error': str(exc)})


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
    config_path = os.environ.get('ARTICLE_STUDIO_MODEL_CONFIG', str(ROOT.parents[1] / 'work/private/article-studio-model.json'))
    server.assistant = assistant_module.Assistant(store, content, config_path)
    server.assistant.generation = server.generation
    from scheduled_tasks import ScheduledTasks
    server.schedules = ScheduledTasks(store.workspace, server.assistant, autostart=False)
    store.schedules = server.schedules
    server.schedules.publisher = store.clues.from_run
    server.schedules.wechat_analyzer = store.wechat_source.analyze_clues
    server.schedules.radar_analyzer = store.wechat_source.analyze_radar
    server.schedules.github_analyzer = store.github.analyze
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
