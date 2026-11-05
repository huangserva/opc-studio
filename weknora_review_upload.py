"""Explicitly exported publication reviews/observations; no raw snapshot ingestion."""
from contextlib import closing
import json
import re
import sqlite3
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from scripts.weknora_push_longtext import Document, Ledger, SafetyStop, KB, sha, line, MARKER

REVIEW_SOURCES = {'manual': ('RP', '发布复盘笔记'), 'publication_observations': ('PO', '发布观察')}
REVIEW_TAGS = ('隐私-内部', '类型-知识')


class UploadLedger(Ledger):
    @staticmethod
    def owned(number):
        if isinstance(number, str) and re.fullmatch(r'(?:RP|PO)-[a-f0-9]{8}(?:-[a-f0-9]{8}){3}', number):
            return
        Ledger.owned(number)


def number(source, ident):
    if source not in REVIEW_SOURCES or not isinstance(ident, str) or not re.fullmatch('[a-f0-9]{32}', ident):
        raise SafetyStop('review_identity_invalid')
    # Group the local digest to retain a readable stable identifier in M4's sanitizer.
    return REVIEW_SOURCES[source][0] + '-' + '-'.join(ident[i:i+8] for i in range(0, 32, 8))


def provenance(row):
    p = json.loads(row['provenance'])
    if not isinstance(p, dict):
        raise SafetyStop('review_provenance_invalid')
    return p


def selected_provenance(row, p):
    source = row['source']
    locator = p.get('source_locator')
    parsed = urlsplit(locator) if isinstance(locator, str) else None
    if not parsed or parsed.scheme or parsed.netloc or parsed.path != '/feedback.html' or parsed.fragment:
        raise SafetyStop('review_origin_invalid')
    query = parse_qs(parsed.query)
    if source == 'manual':
        slug = p.get('slug')
        if (not isinstance(slug, str) or not re.fullmatch('[A-Za-z0-9][A-Za-z0-9_-]{0,79}', slug)
                or query != {'review': [slug]} or not isinstance(p.get('delivery_ids'), list)
                or not all(isinstance(x, str) for x in p['delivery_ids'])
                or not isinstance(p.get('review_body_sha256'), str)
                or p['review_body_sha256'] != sha(row['body'])):
            raise SafetyStop('review_body_or_origin_changed')
        keys = ('slug','type','review_updated_at','platforms','source_locator','delivery_ids','review_body_sha256')
    else:
        if (not isinstance(p.get('observation_id'), str) or not re.fullmatch('[a-f0-9]{32}', p['observation_id'])
                or query != {'observation': [p['observation_id']]}
                or any(not isinstance(p.get(k), str) or not re.fullmatch('[a-f0-9]{64}', p[k]) for k in ('observation_hash','raw_sha256'))
                or p.get('platform') != 'x' or p.get('platform_verified') is not False):
            raise SafetyStop('observation_origin_invalid')
        keys = ('source_locator','sourceUrl','platform','account','post_id','post_url','observed_at',
                'observation_id','observation_hash','raw_sha256','version_binding','window','platform_verified',
                'slug','version','delivery_id','parent_id','parent_hash')
    return {k: p[k] for k in keys if k in p}


def render_review(row, p, tags):
    ident = number(row['source'], row['id'])
    if not isinstance(row['body'], str) or not row['body'].strip():
        raise SafetyStop('empty_body')
    selected = selected_provenance(row, p)
    tagids = tuple(tags['tags'].get(name) for name in REVIEW_TAGS)
    if (tags.get('knowledge_base_id') != KB or any(not isinstance(t, str) or not t for t in tagids)
            or len(set(tagids)) != 2):
        raise SafetyStop('review_tags_missing')
    original_title = line(row['title'])
    marker = '【验收合成】' if original_title.startswith('【验收合成】') else ''
    title = marker + '【' + REVIEW_SOURCES[row['source']][1] + '】' + ident + ' ' + original_title.removeprefix(marker)
    content = '\n'.join(['# ' + title, '- 编号：' + ident,
                         '- 类型：' + REVIEW_SOURCES[row['source']][1], '- 来源类型：' + row['source'],
                         '- 隐私：内部', '- 本机文档：' + row['id'], '- 本机出处：' + selected['source_locator'],
                         '- 原文链接：' + line(row['url']), '- 时间：' + line(row['modified']),
                         '- 正文 SHA256：' + sha(row['body']),
                         '- 出处信息：' + json.dumps(selected, ensure_ascii=False, sort_keys=True)]) + MARKER + row['body']
    if len(content) > 200000:
        raise SafetyStop('longtext_exceeds_manual_limit')
    return Document(ident, row['id'], row['source'], title[:250], row['body'], content, sha(content), tagids)


def read_row(db, rowid):
    row = db.execute('SELECT * FROM documents WHERE rowid=?', (rowid,)).fetchone()
    if row is None:
        raise OSError('source row missing')
    return row


def read_review_documents(source_db, tags, unavailable):
    docs = []
    with closing(sqlite3.connect(Path(source_db).resolve().as_uri() + '?mode=ro', uri=True)) as db:
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA query_only=ON')
        db.execute('BEGIN')
        entries = db.execute("SELECT rowid,id,source FROM documents WHERE source IN ('manual','publication_observations') ORDER BY source,id").fetchall()
        for entry in entries:
            label = REVIEW_SOURCES[entry['source']][1]
            fallback = REVIEW_SOURCES[entry['source']][0] + '-unreadable-row-' + str(entry['rowid'])
            ident = fallback
            title = label
            size = 0
            try:
                ident = number(entry['source'], entry['id'])
                row = read_row(db, entry['rowid'])
                title += ' · ' + line(row['title'])
                p = provenance(row)
                # Arbitrary manually collected notes are NOT publication reviews.
                if row['source'] == 'manual' and 'review_body_sha256' not in p:
                    continue
                size = len(row['body']) if isinstance(row['body'], str) else 0
                docs.append(render_review(row, p, tags))
            except Exception as exc:
                code = str(exc) if isinstance(exc, SafetyStop) else 'review_read_or_render_failed'
                reasons = {'empty_body': '正文为空，请先补充复盘',
                           'longtext_exceeds_manual_limit': '渲染后内容超过 200000 字符上限',
                           'review_body_or_origin_changed': '复盘正文或出处已变化，请从复盘页重新回库',
                           'review_tags_missing': '复盘内部标签配置不完整',
                           'review_identity_invalid': '复盘本机编号无效',
                           'review_origin_invalid': '复盘出处无效',
                           'observation_origin_invalid': '发布观察出处无效'}
                docs.append(unavailable(ident, entry['id'] if isinstance(entry['id'], str) else '', entry['source'], title[:250],
                            '不可上传：' + reasons.get(code, '读取或渲染失败，请核对该篇复盘资料'),
                            code if code in reasons else 'review_read_or_render_failed', size))
    return docs
