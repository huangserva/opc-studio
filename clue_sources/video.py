"""Actual platform IDs, separate board channels. No network hydration."""
from clue_store_v2 import canonical, safe_text, safe_payload, utc, raw_metrics


def records(data, source='video'):
    if not isinstance(data, dict) or not isinstance(data.get('lists'), dict):
        raise ValueError('missing_lists')
    out = []
    for board, rows in data['lists'].items():
        if not isinstance(rows, list):
            raise ValueError('invalid_board')
        out.extend(('video:' + board, str(r.get('key') or r.get('id') or i) if isinstance(r, dict) else str(i), r) for i, r in enumerate(rows))
    return out


def parse(row, meta, source='video'):
    if not isinstance(row, dict):
        raise ValueError('record_not_object')
    key, platform, kind, url = canonical(row, row.get('platform'))
    body = safe_text(row.get('full_text') or row.get('desc_excerpt'))
    raw, reasons = raw_metrics(row)
    return dict(canonical_key=key, platform=platform, kind=kind, canonical_url=url, title=safe_text(row.get('title')), summary=body[:600], body=body,
                rich=safe_payload({k: row[k] for k in ('cover', 'sources', 'flags', 'relevance_reason', 'relevance') if k in row}), author_key=None,
                published_at=utc(row.get('published_at')), first_seen_at=utc(row.get('first_seen_at')), observed_at=utc(row.get('last_seen_at') or meta.get('generated_at')),
                bookmarked_at=None, content_complete=False, incomplete_reason='榜单仅提供摘要，未抓取完整视频正文', metrics=raw, missing_reasons=reasons,
                privacy_scope='public', aliases=[key] + ([url] if url else []), channel_metadata=dict(sources=safe_payload(row.get('sources') or [])))
