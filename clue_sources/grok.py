"""Public X source protocol; preserve each channel's content and metrics independently."""
from clue_store_v2 import canonical, safe_text, safe_payload, safe_url, utc, raw_metrics, digest


def records(data, source):
    if data == '本次无' or isinstance(data, dict) and data.get('message') == '本次无':
        return []
    if isinstance(data, list):
        rows = data
    elif isinstance(data, dict):
        arrays = [data[k] for k in ('posts', 'entries', 'items') if k in data]
        if not arrays or any(not isinstance(x, list) for x in arrays):
            raise ValueError('missing_record_array')
        rows = sum(arrays, [])
    else:
        raise ValueError('invalid_document')
    channel = {'daily': 'grok:daily', 'alerts': 'grok:alerts', 'interest': 'grok:interest', 'bookmarks': 'x:bookmarks', 'questions': 'reader:questions'}[source]
    return [(channel, str(row.get('id') or row.get('tweet_id') or i) if isinstance(row, dict) else str(i), row) for i, row in enumerate(rows)]


def parse(row, meta, source):
    if not isinstance(row, dict):
        raise ValueError('record_not_object')
    key, platform, kind, url = canonical(row, 'x')
    if platform != 'x':
        raise ValueError('x_source_platform_conflict')
    body = safe_text(row.get('full_text') or row.get('text') or '')
    rich = safe_payload({k: row[k] for k in ('article', 'thread', 'quoted', 'media', 'card', 'usable', 'what_new', 'relevance', 'why', 'pattern', 'my_angle', 'outline', 'doable_resource', 'also_posted_by', 'original_author', 'is_retweet') if k in row})
    article = rich.get('article')
    if not body and isinstance(article, dict):
        body = article.get('body') or article.get('text') or ''
    complete = row.get('content_complete') is True and bool(body)
    raw, reasons = raw_metrics(row)
    author = row.get('author_handle') or row.get('handle') or row.get('author')
    if not isinstance(author, str):
        author = author.get('handle') if isinstance(author, dict) else None
    return dict(canonical_key=key, platform=platform, kind=kind, canonical_url=url,
                title=safe_text(row.get('title_zh') or row.get('what_new') or body)[:200],
                summary=safe_text(row.get('why') or body)[:600], author_key=safe_text(author), body=body, rich=rich,
                content_complete=complete, incomplete_reason=None if complete else safe_text(row.get('incomplete_reason') or row.get('content_incomplete_reason') or 'source_incomplete'),
                published_at=utc(row.get('published_at') or row.get('created_at')),
                bookmarked_at=utc(row.get('bookmarked_at')) if source == 'bookmarks' else None,
                first_seen_at=utc(row.get('fetched_at') or row.get('collected_at')),
                observed_at=utc(row.get('collected_at') or meta.get('collected_at') or meta.get('collected_at_iso') or meta.get('generated_at') or (meta.get('exported_at') if source == 'bookmarks' else None)),
                metrics=raw, missing_reasons=reasons, privacy_scope='public',
                aliases=[key, url] if url else [key],
                channel_metadata={k: safe_payload(row[k]) for k in ('topic', 'relevance', 'what_new', 'also_posted_by', 'is_retweet', 'original_author') if k in row},
                relations=relations(row), resources=rich.get('usable') if isinstance(rich.get('usable'), list) else [])


def relations(row):
    result = []
    quoted = row.get('quoted')
    if isinstance(quoted, dict):
        try:
            result.append(dict(to_key=canonical(quoted, 'x')[0], relation_type='quote'))
        except ValueError:
            pass
    for kind, field in [('retweet', 'referenced_tweet_id'), ('thread', 'parent_tweet_id')]:
        target = row.get(field)
        if target and str(target).isdigit():
            result.append(dict(to_key='x:' + str(target), relation_type=kind))
    return result
