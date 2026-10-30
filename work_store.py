"""Workspace stores and immutable work versions; HTML policy stays in server.Store."""
import argparse
import copy
import hashlib
import importlib.util
import re
import shutil
import tempfile
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import markdown_blocks

ROOT = Path(__file__).resolve().parent
IMPORTER = ROOT.parent / 'article-importer' / 'scripts' / 'article_importer.py'
spec = importlib.util.spec_from_file_location('importer', IMPORTER)
importer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(importer)
content_spec = importlib.util.spec_from_file_location('content_store', ROOT / 'content_store.py')
content = importlib.util.module_from_spec(content_spec)
content_spec.loader.exec_module(content)


def uid(value):
    if not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_-]{0,79}', value):
        raise ValueError('文章目录名只能包含英文、数字、短横线和下划线')
    return value


class Conflict(ValueError):
    pass


class WorkStoreBase(content.ContentMixin):

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

