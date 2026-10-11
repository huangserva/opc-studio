"""Work-level review: series, peer comparison, notes, knowledge export, org overview.

The fake `buffer` executable keeps the organization-wide query offline; nothing in
this file reaches a real Buffer account, publishes anything, or invents a figure.
"""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest

from server import Store
from publication_metrics import PublicationMetrics
from publication_reviews import PublicationReviews, number, METRIC_FIELDS
from knowledge_store import Knowledge

FAKE = '''#!/usr/bin/env python3
import json, os, sys
LOG = __LOG__
SCRIPT = __SCRIPT__
calls = json.load(open(LOG, encoding='utf-8')) if os.path.exists(LOG) else []
calls.append({'argv': sys.argv[1:], 'stdin': sys.stdin.read()})
json.dump(calls, open(LOG, 'w', encoding='utf-8'), ensure_ascii=False)
steps = json.load(open(SCRIPT, encoding='utf-8'))['steps']
step = steps[min(len(calls) - 1, len(steps) - 1)]
if step.get('stdout') is not None:
    sys.stdout.write(step['stdout'])
if step.get('stderr') is not None:
    sys.stderr.write(step['stderr'])
sys.exit(step.get('returncode', 0))
'''


def account(*organizations):
    return json.dumps({'id': 'acct', 'organizations': [{'id': item[0], 'name': item[1], 'channelCount': 1}
                                                       for item in organizations]})


def metrics(**values):
    return json.dumps({'metrics': [{'name': name, 'type': 'total', 'unit': 'count', 'value': value}
                                   for name, value in values.items()],
                       'metricsUpdatedAt': '2026-10-08T00:00:00Z'})


class PublicationReviewsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = Store(str(self.root / 'workspace'))
        self.store._knowledge = Knowledge(self.store, sources=[])
        self.metrics = PublicationMetrics(self.store)
        self.reviews = PublicationReviews(self.metrics)
        self.when = datetime.now(timezone.utc) - timedelta(hours=2)
        self.work = self.store.create({'title': '合成复盘作品', 'type': 'post'})
        self.delivery = self.store.record_delivery(self.work['slug'], {'platform': 'x', 'version': 'v0001',
                                                                       'url': 'https://x.com/demo/status/123'})
        self.peer = self.store.create({'title': '同类合成作品', 'type': 'post'})
        self.peer_delivery = self.store.record_delivery(self.peer['slug'], {'platform': 'x', 'version': 'v0001',
                                                                            'url': 'https://x.com/demo/status/456'})
        self.other = self.store.create({'title': '不同类型合成作品', 'type': 'video'})
        self.other_delivery = self.store.record_delivery(self.other['slug'], {'platform': 'x', 'version': 'v0001',
                                                                              'url': 'https://x.com/demo/status/789'})
        self.fake = self.root / 'buffer'
        self.fake.write_text(FAKE.replace('__LOG__', repr(str(self.root / 'calls.json')))
                                 .replace('__SCRIPT__', repr(str(self.root / 'script.json'))), encoding='utf-8')
        self.fake.chmod(0o755)

    def tearDown(self):
        self.temp.cleanup()

    def script(self, *steps):
        (self.root / 'script.json').write_text(json.dumps({'steps': list(steps)}), encoding='utf-8')
        self.store.buffer_cli_command = [str(self.fake)]

    def notes(self):
        with self.store.knowledge.db() as c:
            return [row['id'] for row in c.execute("SELECT id FROM documents WHERE kind='notes' ORDER BY id")]

    def import_metrics(self, slug, delivery=None, when=None, kind='interval', **values):
        delivery = delivery or {self.work['slug']: self.delivery, self.peer['slug']: self.peer_delivery,
                                self.other['slug']: self.other_delivery}[slug]
        when = when or self.when
        start = '' if kind == 'cumulative' else (when - timedelta(days=1)).isoformat()
        return self.metrics.import_snapshot({
            'slug': slug, 'delivery_id': delivery['id'], 'platform': 'x', 'version': 'v0001',
            'collected_at': when.isoformat(),
            'range': {'kind': kind, 'start': start, 'end': when.isoformat(),
                      'label': '合成口径；平台原始次数，不与累计比较'},
            'metrics': values,
            'provenance': {'type': 'manual', 'url': 'https://analytics.x.com/content', 'note': '仅合成测试'}})

    def test_works_lists_only_registered_publications_and_counts(self):
        self.import_metrics(self.work['slug'], views=100)
        listed = self.reviews.works()
        self.assertEqual([item['slug'] for item in listed['works']],
                         sorted([self.work['slug'], self.peer['slug'], self.other['slug']]))
        mine = next(item for item in listed['works'] if item['slug'] == self.work['slug'])
        self.assertEqual(mine['snapshots'], 1)
        self.assertEqual(mine['platforms'], ['x'])
        self.assertEqual(mine['type'], 'post')
        self.assertEqual(mine['note_updated_at'], '')
        self.assertEqual(listed['metric_fields'], METRIC_FIELDS)
        empty = PublicationReviews(PublicationMetrics(Store(str(self.root / 'empty'))))
        self.assertEqual(empty.works()['works'], [])

    def test_series_keeps_original_text_orders_by_time_and_only_diffs_parseable_numbers(self):
        self.import_metrics(self.work['slug'], when=self.when - timedelta(days=2), views='1,200', likes='0')
        self.import_metrics(self.work['slug'], when=self.when - timedelta(days=1), views='1.2K', likes='7')
        self.import_metrics(self.work['slug'], when=self.when, views='2,500', likes='0', kind='cumulative')
        review = self.reviews.review(self.work['slug'])
        interval = {item['label']: item for item in review['series'] if item['range_kind'] == 'interval'}
        self.assertEqual([point['value'] for point in interval['views']['points']], ['1,200', '1.2K'])
        self.assertEqual([point.get('delta') for point in interval['views']['points']], [None, None])
        self.assertNotIn('delta', interval['views']['points'][0])
        self.assertIsNone(interval['views']['points'][1]['delta'])
        self.assertEqual(interval['views']['points'][0]['number'], 1200.0)
        self.assertIsNone(interval['views']['points'][1]['number'])
        self.assertEqual([point.get('delta') for point in interval['likes']['points']], [None, 7.0])
        cumulative = {item['label']: item for item in review['series'] if item['range_kind'] == 'cumulative'}
        self.assertEqual([p['value'] for p in cumulative['views']['points']], ['2,500'])
        self.assertEqual(review['latest']['views']['value'], '2,500')
        self.assertEqual(review['latest']['views']['range_kind'], 'cumulative')
        self.assertIn('区间统计与累计统计分开展示', review['series_notice'])

    def test_peers_compare_same_platform_and_type_only(self):
        self.import_metrics(self.work['slug'], views=100, likes='3')
        self.import_metrics(self.peer['slug'], views=900)
        self.import_metrics(self.other['slug'], views=5000)
        review = self.reviews.review(self.work['slug'])
        self.assertEqual(len(review['peers']), 1)
        group = review['peers'][0]
        self.assertEqual(group['platform'], 'x')
        self.assertEqual(group['type'], 'post')
        self.assertEqual([item['slug'] for item in group['works']], [self.peer['slug']])
        views = next(item for item in group['metrics'] if item['label'] == 'views')
        self.assertEqual((views['count'], views['median'], views['minimum'], views['maximum']), (1, 900.0, 900.0, 900.0))
        self.assertEqual(views['peers'], [{'slug': self.peer['slug'], 'title': '同类合成作品', 'value': '900'}])
        self.assertEqual([item['label'] for item in group['metrics']], ['views'])
        self.assertIn('样本少时只能作参考', group['notice'])

    def test_review_rejects_unknown_works_and_bad_slugs(self):
        for slug in ['missing-work', '../etc', 'a' * 90, 'bad slug']:
            with self.assertRaises(ValueError):
                self.reviews.review(slug)
        with self.assertRaises(ValueError):
            self.reviews.save('../etc', {'notes': 'x'})

    def test_notes_stay_local_until_clicked_and_validate_before_writing(self):
        self.import_metrics(self.work['slug'], views=100)
        self.assertEqual(self.reviews.review(self.work['slug'])['note']['notes'], '')
        saved = self.reviews.save(self.work['slug'], {'notes': '合成结论：标题第二版更直白，待更多样本验证。'})
        self.assertIn('待更多样本验证', saved['notes'])
        self.assertTrue(saved['notes_updated_at'])
        self.assertIn('尚未回库', saved['summary'])
        self.assertEqual(self.notes(), [])
        for change in [{'notes': 5}, {'notes': 'x' * 20001}, {'notes': None}, {'extra': 1}]:
            with self.assertRaises(ValueError):
                self.reviews.save(self.work['slug'], change)
        self.assertIn('待更多样本验证', self.reviews.review(self.work['slug'])['note']['notes'])

    def test_export_note_reuses_knowledge_add_note_and_cites_facts(self):
        snapshot = self.import_metrics(self.work['slug'], views='1,200')
        self.import_metrics(self.peer['slug'], views=900)
        self.reviews.save(self.work['slug'], {'notes': '合成结论：保留疑问，不推定原因。'})
        result = self.reviews.export_note(self.work['slug'])
        self.assertFalse(result['duplicate'])
        document = self.store.knowledge.document(result['document_id'])
        self.assertEqual(document['source'], 'manual')
        self.assertIn('发布复盘', document['tags'])
        self.assertIn('合成结论：保留疑问，不推定原因。', document['body'])
        self.assertIn('views：1,200', document['body'])
        self.assertIn('同类中位 900', document['body'])
        self.assertIn(snapshot['hash'], document['body'])
        self.assertIn('不证明某种写法导致表现变化', document['body'])
        self.assertEqual(document['provenance']['slug'], self.work['slug'])
        self.assertEqual(document['provenance']['delivery_ids'], [self.delivery['id']])
        self.assertNotIn('platform', document['provenance'])
        again = self.reviews.export_note(self.work['slug'])
        self.assertTrue(again['duplicate'])
        self.assertEqual(again['document_id'], result['document_id'])
        self.assertEqual(self.notes(), [result['document_id']])
        self.reviews.save(self.work['slug'], {'notes': '合成结论已改写。'})
        newer = self.reviews.export_note(self.work['slug'])
        self.assertFalse(newer['duplicate'])
        self.assertNotEqual(newer['document_id'], result['document_id'])
        self.assertEqual(len(self.reviews.review(self.work['slug'])['note']['documents']), 2)

    def test_export_requires_a_note_before_touching_the_knowledge_base(self):
        self.import_metrics(self.work['slug'], views=100)
        with self.assertRaises(ValueError):
            self.reviews.export_note(self.work['slug'])
        self.assertEqual(self.notes(), [])

    def test_organization_overview_is_read_only_and_never_claimed_as_one_work(self):
        self.script({'stdout': account(('org1', '合成组织'))}, {'stdout': metrics(views=4321, engagements=90)})
        self.store.buffer_cli_command = [str(self.fake)]
        result = self.reviews.fetch_overview({'start': '2026-10-01', 'end': '2026-10-07'})
        self.assertEqual(result['scope'], 'organization')
        self.assertFalse(result['platform_verified'])
        self.assertIn('不能归因到单个作品', result['notice'])
        self.assertEqual([item['name'] for item in result['metrics']], ['views', 'engagements'])
        argv = json.loads((self.root / 'calls.json').read_text(encoding='utf-8'))
        self.assertEqual([call['argv'][0] for call in argv], ['account', 'aggregatedPostMetrics'])
        payload = json.loads([call for call in argv if call['argv'][0] == 'aggregatedPostMetrics'][0]['argv'][2])
        self.assertEqual(payload, {'organizationId': 'org1', 'startDateTime': '2026-10-01T00:00:00Z',
                                   'endDateTime': '2026-10-07T00:00:00Z'})
        self.assertEqual(len(self.reviews.overviews()), 1)
        review = self.reviews.review(self.work['slug'])
        self.assertEqual(review['overviews'][0]['metrics'][0]['value'], 4321)

    def test_overview_stops_on_ambiguous_account_or_cli_failure(self):
        self.script({'stdout': account(('org1', '甲组织'), ('org2', '乙组织'))})
        with self.assertRaisesRegex(ValueError, '多个组织'):
            self.reviews.fetch_overview({'start': '2026-10-01', 'end': '2026-10-07'})
        self.script({'returncode': 1, 'stderr': json.dumps({'message': 'Insights not enabled'})})
        with self.assertRaisesRegex(ValueError, 'Insights not enabled'):
            self.reviews.fetch_overview({'start': '2026-10-01', 'end': '2026-10-07', 'organization_id': 'org1'})
        self.script({'stdout': account(('org1', '合成组织'))}, {'returncode': 0, 'stdout': ''})
        with self.assertRaisesRegex(ValueError, '无法解析'):
            self.reviews.fetch_overview({'start': '2026-10-01', 'end': '2026-10-07'})
        self.assertEqual(self.reviews.overviews(), [])
        for change in [{'start': ''}, {'end': 'not-a-date'}, {'start': '2026-10-08', 'end': '2026-10-01'},
                       {'organization_id': '../x'}, {'channel_ids': 'not-a-list'}, {'extra': 1}]:
            with self.assertRaises(ValueError):
                self.reviews.fetch_overview({'start': '2026-10-01', 'end': '2026-10-07', **change})

    def test_unparsed_values_stay_unparsed(self):
        for value, expected in [('1,200', 1200.0), ('0', 0.0), ('1.25%', 1.25), ('-3', -3.0), ('1.2K', None),
                                ('3万', None), ('', None), (True, None), (None, None), (7, 7.0)]:
            self.assertEqual(number(value)[0], expected, value)


if __name__ == '__main__':
    unittest.main()
