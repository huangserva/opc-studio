"""feedback HTTP routes, extracted without changing business behavior.

The entry point keeps security, body parsing, dispatch order and fallback.
"""
from urllib.parse import parse_qs, unquote
import re


def get_route(handler, parts):
    self = handler
    segments = [unquote(x) for x in parts.path.split('/') if x]
    query = parse_qs(parts.query)
    store = self.server.store
    if parts.path == '/api/feedback':
        return self.reply(200, store.feedback.bindings())
    if parts.path == '/api/feedback/observations':
        return self.reply(200, store.feedback.observations.listing())
    if parts.path == '/api/feedback/reviews':
        return self.reply(200, store.feedback.reviews.works())
    if parts.path == '/api/feedback/overviews':
        return self.reply(200, {'overviews': store.feedback.reviews.overviews()})
    if len(segments) == 4 and segments[:3] == ['api', 'feedback', 'review']:
        return self.reply(200, store.feedback.reviews.review(segments[3]))
    if len(segments) == 4 and segments[:3] == ['api', 'feedback', 'observation']:
        return self.reply(200, store.feedback.observations.get(segments[3]))
    if parts.path == '/api/feedback/snapshots':
        return self.reply(200, store.feedback.listing(slug=query.get('slug', [''])[0], delivery_id=query.get('delivery_id', [''])[0], limit=int(query.get('limit', [50])[0])))
    if len(segments) == 4 and segments[:3] == ['api', 'feedback', 'snapshot']:
        return self.reply(200, store.feedback.snapshot(segments[3]))
    return None


def post_route(handler, segments, payload):
    self = handler
    if segments == ['api', 'feedback', 'import']:
        return self.reply(201, self.server.store.feedback.import_snapshot(payload))
    if len(segments) == 5 and segments[:3] == ['api', 'feedback', 'review'] and segments[4] == 'notes':
        return self.reply(200, self.server.store.feedback.reviews.save(segments[3], payload))
    if len(segments) == 5 and segments[:3] == ['api', 'feedback', 'review'] and segments[4] == 'knowledge':
        return self.reply(201, self.server.store.feedback.reviews.export_note(segments[3]))
    if segments == ['api', 'feedback', 'overview']:
        # Read-only Buffer query, only on an explicit click; it reports the
        # whole organization, never one work.
        return self.reply(201, self.server.store.feedback.reviews.fetch_overview(payload))
    if segments == ['api', 'feedback', 'observe']:
        return self.reply(201, self.server.store.feedback.observations.observe(payload))
    if len(segments) == 5 and segments[:3] == ['api', 'feedback', 'observation'] and segments[4] == 'knowledge':
        return self.reply(201, self.server.store.feedback.observations.export_to_knowledge(segments[3]))
    if len(segments) == 5 and segments[:3] == ['api', 'feedback', 'observation'] and segments[4] == 'bind':
        return self.reply(201, self.server.store.feedback.observations.bind(segments[3], payload))
    if len(segments) == 5 and segments[:3] == ['api', 'feedback', 'snapshot'] and segments[4] == 'knowledge':
        return self.reply(201, self.server.store.feedback.export_to_knowledge(segments[3]))
    return None
