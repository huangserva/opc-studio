"""Versioned content extensions, local media and platform delivery packages."""
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
import uuid
import zipfile
from datetime import datetime, timezone
from html import escape
from urllib.parse import urlsplit
from PIL import Image

TYPES = {'article', 'post', 'gallery', 'video'}
from publishing.registry import PLATFORMS

def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))

def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name('.' + path.name + '-' + uuid.uuid4().hex)
    try:
        temporary.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2), encoding='utf-8')
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)

def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()

def link_copy(src, dst):
    # Only immutable media are shared; metadata always receives independent copies.
    if Path(src).suffix.lower() in {'.mp4', '.mov', '.webm'}:
        try:
            os.link(src, dst)
            return str(dst)
        except OSError:
            pass
    return shutil.copy2(src, dst)

def safe_path(root, name):
    root = Path(root).resolve()
    path = (root / name).resolve()
    if not path.is_relative_to(root) or path == root:
        raise ValueError('素材路径必须位于作品目录内')
    return path

def empty_studio(kind='article'):
    return {'type': kind, 'video': None, 'poster': None, 'script': '', 'subtitles': '', 'platforms': {}}

def studio_at(package):
    path = Path(package) / 'studio.json'
    return read(path) if path.exists() else empty_studio()

def assets(studio):
    result = [studio.get('video'), studio.get('poster')]
    for target in studio.get('platforms', {}).values():
        result += [target.get('video'), target.get('poster')]
    return [a for a in result if a]

def seal(package, studio, importer):
    write(Path(package) / 'studio.json', studio)
    manifest = importer.read_json(Path(package) / 'manifest.json')
    manifest.pop('package_id', None)
    manifest['studio_sha256'] = sha(Path(package) / 'studio.json')
    manifest['package_id'] = importer.digest(importer.canonical(manifest))
    importer.write_json(Path(package) / 'manifest.json', manifest)

def check_asset(path, kind):
    suffix = path.suffix.lower()
    size = path.stat().st_size
    if kind == 'video':
        if suffix not in {'.mp4', '.webm', '.mov'} or not 0 < size <= 1024**3:
            raise ValueError('视频请使用 MP4、WebM 或 MOV，最大 1 GB')
        with path.open('rb') as f:
            head = f.read(16)
        if not (head[4:8] == b'ftyp' or head[:4] == b'\x1aE\xdf\xa3'):
            raise ValueError('视频文件格式无法识别')
    else:
        if suffix not in {'.png', '.jpg', '.jpeg', '.webp'} or not 0 < size <= 8 * 1024**2:
            raise ValueError('封面请使用 PNG、JPEG、WebP，最大 8 MB')
        with Image.open(path) as im:
            if im.width * im.height > 40_000_000:
                raise ValueError('封面图片过大')
            im.verify()
    return {'sha256': sha(path), 'bytes': size, 'name': path.name, 'kind': kind}

from publishing.service import PublishingMixin

class ContentMixin(PublishingMixin):
    def accept_upload(self, slug, filename, stream, length):
        self.meta(slug)
        suffix = Path(filename).suffix.lower()
        kind = 'video' if suffix in {'.mp4', '.webm', '.mov'} else 'poster'
        maximum = 1024**3 if kind == 'video' else 8*1024**2
        if not 0 < length <= maximum:
            raise ValueError('文件超过大小限制')
        folder = self.workspace / 'uploads'
        folder.mkdir(exist_ok=True)
        token = uuid.uuid4().hex
        temporary = folder / ('.upload-' + token + suffix)
        try:
            with temporary.open('wb') as output:
                remaining = length
                while remaining:
                    chunk = stream.read(min(1024*1024, remaining))
                    if not chunk:
                        raise ValueError('上传中断，请重新选择文件')
                    output.write(chunk)
                    remaining -= len(chunk)
            info = check_asset(temporary, kind)
            info.update(name=Path(filename).name[:200], file=token + suffix, slug=slug)
            temporary.rename(folder / info['file'])
            write(folder / (token + '.json'), info)
            return {'upload': token, 'name': info['name'], 'bytes': info['bytes'], 'kind': kind,
                    'preview_url': '/uploads/' + slug + '/' + token}
        finally:
            temporary.unlink(missing_ok=True)

    def upload_info(self, slug, token):
        if not re.fullmatch('[a-f0-9]{32}', token):
            raise ValueError('无效素材编号')
        info = read(self.workspace / 'uploads' / (token + '.json'))
        if info['slug'] != slug:
            raise ValueError('素材不属于当前作品')
        return info, self.workspace / 'uploads' / info['file']

    def normalize_studio(self, slug, raw, old, out, intake=None):
        previous = studio_at(old) if old else empty_studio()
        raw = raw if raw is not None else previous
        if raw.get('type', 'article') not in TYPES:
            raise ValueError('无效作品类型')
        allowed = {a['file']: a for a in assets(previous)}
        def resolve(item, kind):
            if not item:
                return None
            if not isinstance(item, dict):
                raise ValueError('素材引用格式错误')
            if 'src' in item and intake:
                source = safe_path(intake, item['src'])
                info = check_asset(source, kind)
            elif 'upload' in item:
                info, source = self.upload_info(slug, item['upload'])
                if info['kind'] != kind:
                    raise ValueError('素材类型不匹配')
            elif old and item.get('file') in allowed:
                info = allowed[item['file']]
                if info.get('kind') != kind:
                    raise ValueError('素材类型不匹配')
                source = safe_path(old, info['file'])
            else:
                raise ValueError('素材不存在，请重新上传或通过投递目录导入')
            name = 'media/' + info['sha256'] + source.suffix.lower()
            dest = safe_path(out, name)
            dest.parent.mkdir(exist_ok=True)
            if not dest.exists():
                link_copy(source, dest)
            return {k: v for k, v in {**info, 'file': name}.items() if k in {'sha256', 'bytes', 'name', 'kind', 'file'}}
        normalized = empty_studio(raw.get('type', 'article'))
        for key in ('script', 'subtitles'):
            normalized[key] = str(raw.get(key, ''))[:500_000]
        for key in ('video', 'poster'):
            normalized[key] = resolve(raw.get(key), key)
        targets = raw.get('platforms', {})
        if not isinstance(targets, dict) or set(targets) - set(PLATFORMS):
            raise ValueError('无效发布平台')
        for platform, item in targets.items():
            if item.get('status', 'draft') not in ('draft', 'ready'):
                raise ValueError('平台准备状态只能为草稿或待发布')
            target = {key: str(item.get(key, ''))[:100_000] for key in ('title', 'caption', 'topics', 'account', 'subtitles')}
            target.update(status=item.get('status', 'draft'), video=resolve(item.get('video'), 'video'), poster=resolve(item.get('poster'), 'poster'))
            normalized['platforms'][platform] = target
        return normalized

    def studio_public(self, slug, revision, studio):
        value = copy.deepcopy(studio)
        for asset in assets(value):
            asset['url'] = self.asset_url(slug, revision, asset['file'])
        return value
