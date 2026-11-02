"""Conservative one-thread-one-object mapping. Private bodies stay in existing stores."""
import json
from clue_store_v2 import connect, safe_text, safe_url, utc, digest, canonical, METRICS


def snapshot(path):
    db = connect(path, True)
    try:
        db.execute('BEGIN')
        entries = {}
        for entry in db.execute('SELECT * FROM entries ORDER BY rowid'):
            entries.setdefault(entry['thread_id'], []).append(dict(entry))
        rows = [dict(row, entries=entries.get(row['id'], [])) for row in db.execute('SELECT * FROM threads ORDER BY id')]
        return {'items': rows}
    finally:
        db.close()


def records(data, source='legacy'):
    return [('legacy', r['id'], r) for r in data['items']]


def parse(row, meta, source='legacy'):
    data = json.loads(row['data'])
    evidence = []
    entry_refs = []
    private = False
    sources = set()
    aliases = []
    for entry in row['entries']:
        payload = json.loads(entry['data'])
        entry_refs.append(dict(entry_id=entry['id'], revision=payload.get('revision'), created_at=entry['created_at'], clock=entry['clock'], entry_type=entry['entry_type'], producer_key_hash=digest(entry.get('producer_key'))))
        for e in payload.get('evidence', []):
            kind = e.get('kind', '')
            is_private = kind not in ('github', 'aisa', 'xradar') or (e.get('provenance') or {}).get('content_scope') in ('private', 'chat', 'mail')
            private |= is_private
            sources.add('wechat' if kind.startswith('wechat') else 'mail' if kind == 'mail' else kind or 'unknown')
            ref = {k: e[k] for k in ('document_id', 'hash', 'start', 'end', 'captured_at') if k in e}
            ref['kind'] = kind
            # Reconstruct internal references rather than retaining potentially keyed original links.
            ref['legacy_entry_id'] = entry['id']
            if not is_private:
                url = safe_url(e.get('url')) or safe_url(e.get('origin_url'))
                if url:
                    ref['url'] = url
                    try:
                        aliases.append(canonical({'url': url})[0])
                    except ValueError:
                        pass
                p = e.get('provenance') or {}
                ref['provenance'] = {k: safe_text(p[k]) if isinstance(p[k], str) else p[k] for k in ('repository', 'item_kind', 'item_key', 'number', 'updated_at', 'platform', 'original_id', 'published_at') if k in p}
            evidence.append(dict(reference=ref, privacy_scope='private' if is_private else 'public'))
    # Unknown legacy/manual provenance is restricted too: no body copy without public evidence.
    private |= not sources
    key = 'legacy:' + row['id']
    return dict(canonical_key=key, platform='web', kind='legacy_thread', canonical_url=None,
                title='历史线索（私密证据引用）' if private else safe_text(data.get('title')), summary='' if private else safe_text(data.get('summary')),
                body='', rich={}, author_key=None, published_at=None, first_seen_at=utc(row['created_at']), observed_at=utc(row['updated_at']), bookmarked_at=None,
                content_complete=False, incomplete_reason='legacy_reference_only', metrics={k: None for k in METRICS},
                missing_reasons={k: 'legacy_metrics_not_equivalent' for k in METRICS}, privacy_scope='private' if private else 'public',
                aliases=[key], canonical_alias_candidates=sorted(set(aliases)), evidence=evidence,
                channel_metadata=dict(sources=sorted(sources)), legacy_state=dict(thread_id=row['id'], revision=row['revision'], seen_revision=row['seen_revision'], unread=row['revision']>row['seen_revision'], status=data.get('status'), tracking=data.get('tracking'), legacy_task_event_id=data.get('event_id'), entries=entry_refs))
