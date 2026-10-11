"""X Articles draft jobs, evidence audit and conservative resume planning."""
import re, difflib
from pathlib import Path
from datetime import datetime, timezone
from urllib.parse import urlsplit
from ...core.common import *
MARKER = re.compile(r"⟦XARTICLE_IMAGE_\d+⟧")

def draft_id(url):
    p = urlsplit(url)
    match = re.fullmatch(r'/compose/articles/edit/(\d+)(?:/(?:preview/)?focus|/preview)?/?', p.path)
    if p.scheme != 'https' or p.netloc != 'x.com' or not match:
        raise ValueError('Expected an https://x.com/compose/articles/edit/<id> draft URL')
    return match.group(1)


def account_name(name):
    name = name.removeprefix('@')
    if not re.fullmatch(r'[A-Za-z0-9_]{1,15}', name):
        raise ValueError('Invalid X account handle')
    return name.lower()


def init_job(args):
    from ...core.article_package import load_package
    manifest = load_package(args.package)
    job = Path(args.job)
    if job.exists():
        raise ValueError('Job already exists; use plan/audit to resume')
    config = {'schema_version': 1, 'package_id': manifest['package_id'],
              'package': str(Path(args.package).resolve()), 'account': account_name(args.account),
              'draft_id': draft_id(args.draft_url) if args.draft_url else None,
              'draft_url': args.draft_url, 'created_at': now(), 'last_audit': None}
    job.mkdir(parents=True)
    write_json(job / 'job.json', config)
    return config


def bind(args):
    path = Path(args.job) / 'job.json'
    job = read_json(path)
    ident = draft_id(args.draft_url)
    if job['draft_id'] and job['draft_id'] != ident:
        raise ValueError('Job already bound to another draft; refusing to replace it')
    job.update(draft_id=ident, draft_url=args.draft_url)
    write_json(path, job)
    return job


def analyse(job, manifest, snapshot):
    issues = []
    if snapshot.get('schema_version') != 1:
        raise ValueError('Expected snapshot schema_version 1')
    if not job['draft_id'] or draft_id(snapshot['url']) != job['draft_id']:
        raise ValueError('Observation is not of the bound draft')
    if account_name(snapshot['account']) != job['account']:
        raise ValueError('Observation account differs from job account')
    if snapshot.get('package_id') != manifest['package_id']:
        raise ValueError('Observation belongs to another package')
    captured = datetime.fromisoformat(snapshot['captured_at'])
    if captured.tzinfo is None:
        raise ValueError('captured_at must include a timezone')
    age = (datetime.now(timezone.utc) - captured).total_seconds()
    fresh = -60 <= age <= 900
    if not fresh:
        issues.append('stale_observation')
    if snapshot.get('title') != manifest['title']:
        issues.append('title_mismatch')
    elements = snapshot['blocks']
    actual_text = ''.join(b.get('text', '') for b in elements if b['kind'] == 'text')
    found_markers = MARKER.findall(actual_text)
    expected_markers = {m['marker'] for m in manifest['body_media']}
    if len(found_markers) != len(set(found_markers)) or set(found_markers) - expected_markers:
        issues.append('unknown_or_duplicate_markers')
    actual_clean = normalized(MARKER.sub('', actual_text))
    expected_text = normalized(''.join(b.get('display_text', b.get('text', '')) for b in manifest['blocks'] if b['kind'] == 'text'))
    text_matches = actual_clean == expected_text
    if not text_matches:
        issues.append('body_text_mismatch')
    images = [b for b in elements if b['kind'] == 'image']
    other = [b for b in elements if b['kind'] not in ('text', 'image')]
    if other:
        issues.append('unsupported_editor_blocks')
    known = {m['id']: m for m in manifest['body_media']}
    ids = [b.get('media_id') for b in images]
    if len(ids) != len(set(ids)):
        issues.append('duplicate_or_unidentified_images')
    if any(b.get('media_id') not in known or not b.get('identity_evidence') for b in images):
        issues.append('image_identity_needs_review')
    if any(b.get('loaded') is not True for b in images):
        issues.append('unloaded_images')
    if len(images) > len(known):
        issues.append('too_many_images')
    observed = {b.get('media_id') for b in images if b.get('identity_evidence')}
    expected_order = [m['id'] for m in manifest['body_media'] if m['id'] in observed]
    if ids != expected_order:
        issues.append('image_order_mismatch')
    # Position is relative to all preceding text, independent of paragraph splitting.
    expected_positions, prefix = {}, ''
    for block in manifest['blocks']:
        if block['kind'] == 'text':
            prefix += block.get('display_text', block.get('text', ''))
        else:
            expected_positions[block['id']] = len(normalized(prefix))
    prefix = ''
    marker_positions = {}
    for block in elements:
        if block['kind'] == 'text':
            text = block.get('text', '')
            for match in MARKER.finditer(text):
                marker_positions[match[0]] = len(normalized(prefix + MARKER.sub('', text[:match.start()])))
            prefix += MARKER.sub('', text)
        elif block['kind'] == 'image' and block.get('media_id') in known:
            if len(normalized(prefix)) != expected_positions[block['media_id']]:
                issues.append('image_position_mismatch')
    for item in manifest['body_media']:
        if item['marker'] in marker_positions and marker_positions[item['marker']] != expected_positions[item['id']]:
            issues.append('marker_position_mismatch')
    cover_ok = not manifest['cover'] or bool(snapshot.get('cover', {}).get('loaded') is True and snapshot['cover'].get('identity_evidence') and snapshot['cover'].get('sha256') == manifest['cover']['sha256'])
    actions, ambiguous = [], []
    for item in reversed(manifest['body_media']):
        present, marker_present = item['id'] in observed, item['marker'] in found_markers
        if present and marker_present:
            actions.append({'action': 'remove_marker_after_identity_review', 'media_id': item['id'], 'marker': item['marker']})
        elif marker_present:
            actions.append({'action': 'insert_image_at_marker', 'media_id': item['id'], 'marker': item['marker'], 'file': item['file']})
        elif not present:
            ambiguous.append(item['id'])
    if ambiguous:
        issues.append('missing_image_without_marker')
    if not cover_ok:
        issues.append('cover_needs_review')
    fatal = set(issues) - {'cover_needs_review'}
    complete = not issues and not actions and len(images) == len(known) and snapshot.get('reloaded') is True
    return {'status': 'complete_verified' if complete else 'needs_review' if fatal else 'partial',
            'text_matches_ignoring_whitespace': text_matches,
            'body_images': len(images), 'expected_body_images': len(known),
            'remaining_markers': found_markers, 'cover_verified_by_reviewer': cover_ok,
            'reloaded': snapshot.get('reloaded') is True, 'fresh': fresh,
            'issues': sorted(set(issues)), 'ambiguous_media': ambiguous,
            'next_action': actions[0] if actions and not fatal else None,
            'pending_actions': actions if not fatal else [],
            'identity_verification': 'reviewer-supplied evidence, not remote image byte hashes',
            'text_diff': ''.join(difflib.unified_diff([expected_text], [actual_clean], fromfile='expected', tofile='observed'))[:4000] if not text_matches else ''}


def inspect_job(args):
    from ...core.article_package import load_package
    job_path = Path(args.job)
    job = read_json(job_path / 'job.json')
    manifest = load_package(job['package'])
    if manifest['package_id'] != job['package_id']:
        raise ValueError('Package changed since job creation')
    snapshot = read_json(args.snapshot)
    report = analyse(job, manifest, snapshot)
    report.update(draft_url=job['draft_url'], account=job['account'], checked_at=now())
    if args.command == 'audit':
        evidence_hash = digest(canonical(snapshot))
        write_json(job_path / 'observations' / (evidence_hash + '.json'), snapshot)
        write_json(job_path / 'audits' / (evidence_hash + '.json'), report)
        job['last_audit'] = {'observation': evidence_hash, 'status': report['status'], 'at': report['checked_at']}
        write_json(job_path / 'job.json', job)
    return report

