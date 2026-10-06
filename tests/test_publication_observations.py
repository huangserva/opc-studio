import copy
from datetime import datetime, timedelta, timezone
import hashlib
import tempfile
import unittest

from server import Store
from knowledge_store import Knowledge


class PublicationObservationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(self.temp.name)
        self.knowledge = Knowledge(self.store, sources=[])
        self.store._knowledge = self.knowledge
        self.observations = self.store.feedback.observations
        raw = 'Post Analytics\nImpressions\n1.2K\nVideo\nViews\n800\n'
        self.payload = {'platform': 'x', 'account': 'demo', 'post_url': 'https://x.com/demo/status/123',
                        'source_url': 'https://x.com/demo/status/123/analytics', 'title': '合成采集资料',
                        'observed_at': (datetime.now(timezone.utc)-timedelta(minutes=1)).isoformat(),
                        'raw': raw, 'metrics': [{'scope': 'Post', 'label': 'Impressions', 'value': '1.2K',
                                                'start': raw.index('Impressions'), 'end': raw.index('Video')} ]}

    def tearDown(self):
        self.temp.cleanup()

    def test_unbound_observation_is_immutable_and_does_not_create_published_delivery(self):
        item = self.observations.observe(self.payload)
        again = self.observations.observe(copy.deepcopy(self.payload))
        self.assertEqual(item['id'], again['id'])
        self.assertEqual(item['metrics'][0]['value'], '1.2K')
        self.assertIsNone(item['version_binding'])
        self.assertIsNone(item['window'])
        self.assertFalse(item['platform_verified'])
        self.assertEqual(item['raw_sha256'], hashlib.sha256(self.payload['raw'].encode()).hexdigest())
        self.assertEqual(self.store.feedback.bindings()['bindings'], [])
        self.assertEqual(self.store.feedback.listing()['total'], 0)
        self.assertNotIn('raw', self.observations.listing()['observations'][0])
        with self.assertRaises(ValueError):
            self.store.feedback.snapshot(item['id'])
        later = copy.deepcopy(self.payload)
        later['raw'] = later['raw'].replace('1.2K', '1.3K')
        later['metrics'][0]['value'] = '1.3K'
        updated = self.observations.observe(later)
        self.assertNotEqual(item['id'], updated['id'])
        self.assertEqual(self.observations.get(item['id'])['raw'], self.payload['raw'])

    def test_wrong_post_owner_or_source_cannot_replace_existing_evidence(self):
        item = self.observations.observe(self.payload)
        variants = [{'account': 'other'}, {'source_url': 'https://x.com/demo/status/456/analytics'},
                    {'source_url': 'https://x.com/other/status/123/analytics'},
                    {'post_url': 'https://x.com/home'}, {'source_url': 'https://evil.test/demo/status/123/analytics'},
                    {'source_url': 'https://x.com/demo/status/123/analytics?token=secret'},
                    {'version_binding': 'v0001'}, {'observed_at': (datetime.now(timezone.utc)+timedelta(days=1)).isoformat()}]
        for fields in variants:
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                self.observations.observe({**self.payload, **fields})
        self.assertEqual(self.observations.listing()['total'], 1)
        self.assertEqual(self.observations.get(item['id'])['raw'], self.payload['raw'])

    def test_metrics_require_original_text_offsets_and_preserve_distinct_scopes(self):
        for fields in [{'value': '0'}, {'label': 'Invented'}, {'start': True}, {'end': 99999},
                       {'start': -1}, {'value': 1200}]:
            payload = copy.deepcopy(self.payload)
            payload['metrics'][0].update(fields)
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                self.observations.observe(payload)
        payload = copy.deepcopy(self.payload)
        payload['metrics'] *= 2
        with self.assertRaises(ValueError):
            self.observations.observe(payload)
        payload['metrics'][1] = {**payload['metrics'][1], 'scope': 'Video'}
        self.assertEqual(len(self.observations.observe(payload)['metrics']), 2)

    def test_explicit_return_to_knowledge_preserves_raw_source_without_a_fake_version(self):
        item = self.observations.observe(self.payload)
        self.assertIsNone(item['knowledge_id'])
        exported = self.observations.export_to_knowledge(item['id'])
        doc = self.knowledge.document(exported['document_id'])
        self.assertEqual(doc['provenance']['post_id'], '123')
        self.assertEqual(doc['provenance']['sourceUrl'], self.payload['source_url'])
        self.assertIsNone(doc['provenance']['version_binding'])
        self.assertNotIn('version', doc['provenance'])
        self.assertIn('统计窗口：平台界面未显示', doc['body'])
        self.assertIn(self.payload['raw'], doc['body'])
        self.assertEqual(self.observations.export_to_knowledge(item['id'])['hash'], exported['hash'])
        self.assertEqual(self.store.feedback.path.stat().st_mode & 0o777, 0o600)

    def test_linking_uses_registered_post_and_fixed_version_and_preserves_parent_evidence(self):
        parent = self.observations.observe(self.payload)
        original = self.observations.export_to_knowledge(parent['id'])
        work = self.store.create({'title': '合成旧帖子', 'type': 'post'})
        delivery = self.store.record_delivery(work['slug'], {'platform': 'x', 'version': 'v0001', 'url': self.payload['post_url']})
        request = {'slug': work['slug'], 'delivery_id': delivery['id'], 'version': 'v0001'}
        linked = self.observations.bind(parent['id'], request)
        self.assertEqual(linked['version_binding']['version'], 'v0001')
        self.assertEqual(linked['parent_hash'], parent['hash'])
        self.assertNotEqual(linked['id'], parent['id'])
        self.assertEqual(self.observations.bind(parent['id'], request)['id'], linked['id'])
        self.assertEqual(self.observations.bind(linked['id'], request)['id'], linked['id'])
        self.assertEqual(self.observations.listing()['total'], 2)
        self.assertEqual(self.observations.export_to_knowledge(parent['id'])['hash'], original['hash'])
        self.assertEqual(self.knowledge.document(linked['knowledge_id'])['provenance']['version'], 'v0001')
        self.assertIsNone(linked['window'])

    def test_unrelated_publication_or_changed_version_cannot_claim_a_binding(self):
        parent = self.observations.observe(self.payload)
        work = self.store.create({'title': '合成无关帖子', 'type': 'post'})
        delivery = self.store.record_delivery(work['slug'], {'platform': 'x', 'version': 'v0001', 'url': 'https://x.com/demo/status/456'})
        for version in ('v0001', 'v0002'):
            with self.subTest(version=version), self.assertRaises(ValueError):
                self.observations.bind(parent['id'], {'slug': work['slug'], 'delivery_id': delivery['id'], 'version': version})
        self.assertIsNone(self.observations.get(parent['id'])['version_binding'])
        self.assertEqual(self.observations.listing()['total'], 1)


if __name__ == '__main__':
    unittest.main()
