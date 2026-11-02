"""Reader question protocol v1 (local JSON only; missing directory is an honest empty).

Document: {generated_at: UTC|null, items:[{cluster_id?: str, parent_post_url: str,
question: str, frequency: integer, replies:[{id,url,text?}], answerability?:{},
reply_draft?:str}]}. Stable upstream cluster_id wins. Otherwise use
question:v1:sha256(canonical_parent + NFKC/casefold/whitespace-normalised question).
No send endpoint. Replies are evidence, never separate events/clues per frequency.
"""
import unicodedata
from clue_store_v2 import canonical, digest, safe_text, safe_payload, utc, METRICS
from clue_sources.grok import records as generic_records

def records(data, source='questions'):
    return [(channel, str(row.get('cluster_id') or key) if isinstance(row, dict) else key, row) for channel,key,row in generic_records(data,source)]


def parse(row, meta, source='questions'):
    if not isinstance(row, dict):
        raise ValueError('record_not_object')
    question = safe_text(row.get('question'))
    if not question:
        raise ValueError('missing_question')
    parent = canonical({'url': row.get('parent_post_url')}, 'x')[0]
    cluster_id = row.get('cluster_id')
    if cluster_id is not None and (not isinstance(cluster_id, str) or not cluster_id.strip()):
        raise ValueError('invalid_cluster_id')
    normalized = ' '.join(unicodedata.normalize('NFKC', question).casefold().split())
    key = 'question:' + cluster_id if cluster_id else 'question:v1:' + digest([parent, normalized])
    frequency = row.get('frequency')
    if type(frequency) is not int or frequency < 1:
        raise ValueError('invalid_frequency')
    replies = row.get('replies') or []
    if not isinstance(replies, list):
        raise ValueError('invalid_replies')
    replies = list({str(r['id']): safe_payload(r) for r in replies if isinstance(r, dict) and r.get('id')}.values())
    return dict(canonical_key=key, platform='x', kind='question_cluster', canonical_url=None, title=question[:200], summary=question[:600], body=question,
                rich=dict(frequency=frequency, replies=replies, parent_key=parent, answerability=safe_payload(row.get('answerability') or {}), reply_draft=safe_text(row.get('reply_draft')), cluster_algorithm='v1'),
                author_key=None, published_at=None, first_seen_at=None, observed_at=utc(meta.get('generated_at')), bookmarked_at=None,
                content_complete=True, incomplete_reason=None, metrics={k: None for k in METRICS},
                missing_reasons={k: 'not_applicable_question_cluster' for k in METRICS}, privacy_scope='public', aliases=[key],
                channel_metadata=dict(frequency=frequency), relations=[dict(to_key=parent, relation_type='question_for')])
