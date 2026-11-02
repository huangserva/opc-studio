"""RSS snapshot: capture source integers/reply changes, retain off-topic evidence."""
from clue_store_v2 import canonical, safe_text, safe_payload, utc, raw_metrics


def records(data, source='linuxdo'):
    if not isinstance(data, dict) or not isinstance(data.get('items'), list):
        raise ValueError('missing_items')
    return [('linuxdo:rss', str(r.get('topic_id') or r.get('id') or i) if isinstance(r, dict) else str(i), r) for i, r in enumerate(data['items'])]


def parse(row, meta, source='linuxdo'):
    if not isinstance(row, dict):
        raise ValueError('record_not_object')
    key, platform, kind, url = canonical(row, 'linuxdo')
    body = safe_text(row.get('full_text'))
    metrics, missing = raw_metrics(row)
    from scripts.radar_common import today_relevance_gate
    gate = today_relevance_gate(row)
    return dict(canonical_key=key, platform=platform, kind=kind, canonical_url=url, title=safe_text(row.get('title')),
                summary=body[:600], body=body, rich=safe_payload({k: row[k] for k in ('rich_sources', 'rss_boards', 'reply_delta', 'reply_change', 'topic') if k in row}),
                author_key=None, published_at=utc(row.get('published_at')), first_seen_at=utc(row.get('fetched_at')),
                observed_at=utc(row.get('fetched_at') or meta.get('updated_at')), bookmarked_at=None,
                content_complete=row.get('content_complete') is True and bool(body), incomplete_reason=None if row.get('content_complete') is True and body else safe_text(row.get('incomplete_reason')) or 'rss_excerpt_only',
                metrics=metrics, missing_reasons=missing, privacy_scope='public', aliases=[str(row.get('id')), key] + ([url] if url else []),
                channel_metadata=dict(rss_boards=safe_payload(row.get('rss_boards') or []), relevance_gate=safe_payload(gate), source_status=safe_payload(meta.get('_source_status') or {})))
