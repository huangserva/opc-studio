"""Freeze explicitly selected local evidence; never enumerate the knowledge index."""
import copy
import json
from pathlib import Path
import re
from urllib.parse import urlencode, urlsplit


MAX_ITEMS = 20
MAX_EXCERPT_CHARS = 12000
MAX_MANIFEST_BYTES = 512000


def citation_url(identity, start, end, digest):
    return '/knowledge.html?' + urlencode({'document': identity, 'start': start, 'end': end, 'hash': digest})


def _text(value, name, maximum, default=None):
    if value is None and default is not None:
        return default
    if not isinstance(value, str) or len(value) > maximum or '\x00' in value:
        raise ValueError(f'知识快照{name}格式无效')
    return value


def _source_url(value):
    value = _text(value, '来源链接', 2048, '')
    if not value or value.startswith('/?article='):
        return value
    try:
        parsed = urlsplit(value)
        if parsed.scheme in ('http', 'https') and parsed.hostname and not parsed.username and not parsed.password:
            return value
    except ValueError:
        pass
    raise ValueError('知识快照来源链接无效')


def _snapshots(knowledge, raw):
    if not isinstance(raw, list) or not 1 <= len(raw) <= MAX_ITEMS:
        raise ValueError('固定知识上下文须包含 1–20 个已保存片段')
    items, size, current = [], 0, {}
    for value in raw:
        if not isinstance(value, dict):
            raise ValueError('知识快照片段格式无效')
        identity, digest = value.get('id'), value.get('hash')
        if not isinstance(identity, str) or not re.fullmatch('[a-f0-9]{32}', identity):
            raise ValueError('知识快照资料编号无效')
        if not isinstance(digest, str) or not re.fullmatch('[a-f0-9]{64}', digest):
            raise ValueError('知识快照 hash 无效')
        start, end = value.get('start'), value.get('end')
        if type(start) is not int or type(end) is not int or not 0 <= start < end <= 2000000:
            raise ValueError('知识快照片段范围无效')
        excerpt = _text(value.get('excerpt'), '正文', MAX_EXCERPT_CHARS)
        if len(excerpt) != end - start:
            raise ValueError('知识快照片段范围与已保存正文长度不符')
        size += len(excerpt)
        if size > MAX_EXCERPT_CHARS:
            raise ValueError('固定知识片段合计最多 12,000 字')
        provenance = value.get('provenance', {})
        if not isinstance(provenance, dict):
            raise ValueError('知识快照出处格式无效')
        try:
            metadata_size = len(json.dumps(provenance, ensure_ascii=False, allow_nan=False))
        except (ValueError, TypeError):
            raise ValueError('知识快照出处须为有效 JSON 对象') from None
        if metadata_size > 12000:
            raise ValueError('知识快照出处元数据过长')
        item = {'id': identity, 'hash': digest, 'start': start, 'end': end, 'excerpt': excerpt,
                'source': _text(value.get('source'), '来源', 128, ''),
                'kind': _text(value.get('kind'), '资料类型', 80, ''),
                'title': _text(value.get('title'), '标题', 500, ''),
                'url': _source_url(value.get('url', '')),
                'modified': _text(value.get('modified'), '时间', 80, ''),
                'provenance': copy.deepcopy(provenance)}
        if identity not in current:
            try:
                current[identity] = knowledge.document(identity)
            except ValueError:
                current[identity] = None
        doc = current[identity]
        item['state'] = 'missing' if doc is None else 'stale' if doc.get('hash') != digest else 'fresh'
        if item['state'] == 'fresh' and doc.get('body', '')[start:end] != excerpt:
            raise ValueError('已保存知识片段与同 hash 原文不符，请核对来源清单')
        item['citation_url'] = citation_url(identity, start, end, digest)
        items.append(item)
    return items


def freeze_context(store, request):
    """Return descriptor-only routing metadata plus private full fixed snapshots.

    A draft without a sidecar returns None. Explicit corrupt/mismatched selections
    fail closed. Old unversioned sidecars can only be attached to the current head.
    """
    if not isinstance(request, dict):
        raise ValueError('固定知识上下文请求须为对象')
    kind = request.get('kind')
    if kind == 'dossier':
        if set(request) - {'kind', 'id'} or not isinstance(request.get('id'), str) or not re.fullmatch('[a-f0-9]{32}', request['id']):
            raise ValueError('请选择有效资料夹')
        dossier = store.knowledge.dossier(request['id'])
        descriptor = {'kind': kind, 'id': request['id'], 'title': _text(dossier.get('title'), '资料夹标题', 500),
                      'modified': _text(dossier.get('modified'), '资料夹时间', 80, '')}
        raw = dossier.get('items')
    elif kind == 'draft':
        if set(request) - {'kind', 'slug', 'version'}:
            raise ValueError('稿件知识上下文参数无效')
        slug, version = request.get('slug'), request.get('version')
        if not isinstance(slug, str) or not re.fullmatch('[A-Za-z0-9][A-Za-z0-9_-]{0,79}', slug):
            raise ValueError('稿件编号无效')
        if not isinstance(version, str) or not re.fullmatch(r'v\d{4,}', version):
            raise ValueError('请选择稿件的实际版本')
        article = Path(store.article(slug))
        root = Path(store.workspace).resolve() / 'library'
        if root.is_symlink() or article.is_symlink() or not article.resolve().is_relative_to(root.resolve()):
            raise ValueError('稿件来源清单路径无效')
        refs = article / 'knowledge-sources.json'
        if refs.is_symlink():
            raise ValueError('不能读取符号链接的稿件来源清单')
        package = article / 'versions' / version
        if (package.parent.is_symlink() or package.is_symlink()
                or not package.resolve().is_relative_to(article.resolve())
                or any(path.is_symlink() for path in (article / 'article.json', package / 'manifest.json', package / 'version.json'))):
            raise ValueError('稿件版本路径无效')
        try:
            manifest, _, actual_package = store.info(slug, version)
            head = store.meta(slug)['head']
        except (OSError, KeyError, TypeError, json.JSONDecodeError):
            raise ValueError('稿件或所选版本不存在') from None
        if Path(actual_package).resolve() != package.resolve():
            raise ValueError('稿件版本路径无效')
        if not refs.exists():
            return None
        if not refs.is_file() or refs.stat().st_size > MAX_MANIFEST_BYTES:
            raise ValueError('稿件来源清单无效或过大')
        try:
            data = json.loads(refs.read_text(encoding='utf-8'))
        except (OSError, UnicodeError, json.JSONDecodeError):
            raise ValueError('稿件来源清单不是有效 JSON') from None
        if not isinstance(data, dict):
            raise ValueError('稿件来源清单格式无效')
        bound = data.get('version')
        legacy = bound is None
        if (legacy and version != head) or (not legacy and bound != version):
            raise ValueError('来源清单未绑定所选稿件版本，请使用清单对应版本或重新核对资料')
        descriptor = {'kind': kind, 'slug': slug, 'version': version,
                      'title': _text(manifest.get('title'), '稿件标题', 500),
                      'legacy_binding_unverified': legacy}
        if data.get('dossier') is not None:
            if not isinstance(data['dossier'], str) or not re.fullmatch('[a-f0-9]{32}', data['dossier']):
                raise ValueError('来源清单资料夹编号无效')
            descriptor['id'] = data['dossier']
        raw = data.get('items')
    else:
        raise ValueError('固定知识上下文类型须为 dossier 或 draft')
    items = _snapshots(store.knowledge, raw)
    descriptor['items'] = [{key: copy.deepcopy(item[key]) for key in
        ('id', 'hash', 'start', 'end', 'title', 'source', 'kind', 'url', 'modified', 'state', 'citation_url')} for item in items]
    return {'descriptor': descriptor, 'items': items}
