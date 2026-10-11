"""HTTP surface for the work-level review: works, review, notes, knowledge export.

Synthetic works and imported snapshots only. The Buffer organization query runs
against a fake executable, so no request leaves this machine.
"""
import json
from pathlib import Path
import unittest
from datetime import datetime, timedelta, timezone

from tests import test_events_api as api_fixture
from publication_reviews import PublicationReviews

FAKE = '''#!/usr/bin/env python3
import json, sys
steps = json.load(open(__SCRIPT__, encoding='utf-8'))['steps']
sys.stdout.write(steps[0].get('stdout') or '')
sys.exit(steps[0].get('returncode', 0))
'''


class PublicationReviewApiTests(unittest.TestCase):
    setUp = api_fixture.EventApiTests.setUp
    tearDown = api_fixture.EventApiTests.tearDown

    def request(self, path, payload=None, origin=True, expected_status=None):
        headers = {'Content-Type': 'application/json', 'X-Article-Studio': '1'}
        if origin is not False:
            headers['Origin'] = self.base if origin is True else origin
        from urllib.request import Request, urlopen
        from urllib.error import HTTPError
        req = Request(self.base + path,
                      data=json.dumps(payload, ensure_ascii=False).encode('utf-8') if payload is not None else None,
                      headers=headers)
        try:
            with urlopen(req, timeout=5) as response:
                if expected_status is not None:
                    self.assertEqual(response.status, expected_status)
                return json.load(response)
        except HTTPError as raised:
            if expected_status is not None:
                self.assertEqual(raised.code, expected_status)
                with raised as response:
                    return json.load(response)
            raise

    def setUp(self):
        api_fixture.EventApiTests.setUp(self)
        self.when = datetime.now(timezone.utc) - timedelta(hours=1)
        self.work = self.store.create({'title': '合成复盘作品', 'type': 'post'})
        self.delivery = self.store.record_delivery(self.work['slug'], {'platform': 'x', 'version': 'v0001',
                                                                      'url': 'https://x.com/demo/status/123'})
        self.peer = self.store.create({'title': '同类合成作品', 'type': 'post'})
        self.store.record_delivery(self.peer['slug'], {'platform': 'x', 'version': 'v0001',
                                                       'url': 'https://x.com/demo/status/456'})
        self.snapshot = self.request('/api/feedback/import', {
            'slug': self.work['slug'], 'delivery_id': self.delivery['id'], 'platform': 'x', 'version': 'v0001',
            'collected_at': self.when.isoformat(),
            'range': {'kind': 'cumulative', 'start': '', 'end': self.when.isoformat(), 'label': '合成累计口径'},
            'metrics': {'views': '1,200', 'likes': 12},
            'provenance': {'type': 'manual', 'url': 'https://analytics.x.com/content', 'note': '仅合成测试'}},
            expected_status=201)
        self.request('/api/feedback/import', {
            'slug': self.peer['slug'], 'delivery_id': self.store.delivery_records(self.peer['slug'])[0]['id'],
            'platform': 'x', 'version': 'v0001', 'collected_at': self.when.isoformat(),
            'range': {'kind': 'cumulative', 'start': '', 'end': self.when.isoformat(), 'label': '合成累计口径'},
            'metrics': {'views': 900},
            'provenance': {'type': 'manual', 'url': 'https://analytics.x.com/content', 'note': '仅合成测试'}},
            expected_status=201)

    def test_works_and_review_are_read_only_and_carry_series_and_peers(self):
        works = self.request('/api/feedback/reviews')
        self.assertEqual(sorted(item['slug'] for item in works['works']),
                         sorted([self.work['slug'], self.peer['slug']]))
        self.assertIn('metric_fields', works)
        review = self.request('/api/feedback/review/' + self.work['slug'])
        self.assertEqual(review['title'], '合成复盘作品')
        self.assertEqual(review['snapshots'], 1)
        series = {item['label']: item for item in review['series']}
        self.assertEqual([point['value'] for point in series['views']['points']], ['1,200'])
        self.assertEqual([point['value'] for point in series['likes']['points']], ['12'])
        self.assertEqual(review['latest']['views']['value'], '1,200')
        group = review['peers'][0]
        self.assertEqual([item['slug'] for item in group['works']], [self.peer['slug']])
        self.assertEqual(next(item['median'] for item in group['metrics'] if item['label'] == 'views'), 900.0)
        self.assertEqual(review['note']['notes'], '')
        self.assertEqual(self.request('/api/feedback/overviews'), {'overviews': []})
        self.assertIn(self.delivery['id'], [item['delivery_id'] for item in self.request('/api/feedback')['bindings']])

    def test_notes_save_then_export_once_and_reject_bad_input(self):
        self.request('/api/feedback/review/' + self.work['slug'] + '/knowledge', {}, expected_status=400)
        saved = self.request('/api/feedback/review/' + self.work['slug'] + '/notes',
                             {'notes': '合成结论：样本太少，只记录事实。'}, expected_status=200)
        self.assertIn('尚未回库', saved['summary'])
        first = self.request('/api/feedback/review/' + self.work['slug'] + '/knowledge', {}, expected_status=201)
        self.assertFalse(first['duplicate'])
        document = self.store.knowledge.document(first['document_id'])
        self.assertIn('合成结论：样本太少，只记录事实。', document['body'])
        self.assertIn('同类中位 900', document['body'])
        again = self.request('/api/feedback/review/' + self.work['slug'] + '/knowledge', {}, expected_status=201)
        self.assertTrue(again['duplicate'])
        self.assertEqual(again['document_id'], first['document_id'])
        self.request('/api/feedback/review/missing-work/notes', {'notes': 'x'}, expected_status=400)
        self.request('/api/feedback/review/' + self.work['slug'] + '/notes', {'notes': 5}, expected_status=400)
        self.request('/api/feedback/review/bad%20slug/notes', {'notes': 'x'}, expected_status=400)

    def test_write_routes_refuse_foreign_origins(self):
        self.request('/api/feedback/review/' + self.work['slug'] + '/notes', {'notes': 'x'},
                     origin=False, expected_status=403)
        self.request('/api/feedback/review/' + self.work['slug'] + '/knowledge', {},
                     origin=False, expected_status=403)
        self.request('/api/feedback/overview', {'start': '2026-10-01', 'end': '2026-10-02'},
                     origin=False, expected_status=403)

    def test_overview_route_uses_the_configured_cli_and_stays_organization_wide(self):
        script = Path(self.temp.name) / 'script.json'
        script.write_text(json.dumps({'steps': [{'stdout': json.dumps(
            {'id': 'acct', 'organizations': [{'id': 'org1', 'name': '合成组织', 'channelCount': 1}]})}]}),
            encoding='utf-8')
        fake = Path(self.temp.name) / 'buffer'
        fake.write_text(FAKE.replace('__SCRIPT__', repr(str(script))), encoding='utf-8')
        fake.chmod(0o755)
        self.store.buffer_cli_command = [str(fake)]
        result = self.request('/api/feedback/overview', {'start': '2026-10-01', 'end': '2026-10-07'},
                              expected_status=201)
        self.assertEqual(result['scope'], 'organization')
        self.assertIn('不能归因到单个作品', result['notice'])
        self.assertEqual(len(self.request('/api/feedback/overviews')['overviews']), 1)
        self.request('/api/feedback/overview', {'start': '2026-10-01'}, expected_status=400)
        self.request('/api/feedback/overview', {'start': '2026-10-08', 'end': '2026-10-01'}, expected_status=400)

    def test_review_of_a_work_without_snapshots_is_still_readable(self):
        review = self.request('/api/feedback/review/' + self.peer['slug'])
        self.assertEqual(review['latest']['views']['value'], '900')
        self.assertEqual(review['peers'][0]['works'], [{'slug': self.work['slug'], 'title': '合成复盘作品',
                                                        'version': 'v0001'}])
        self.assertEqual(PublicationReviews(self.store.feedback).review(self.peer['slug'])['snapshots'], 1)


if __name__ == '__main__':
    unittest.main()
