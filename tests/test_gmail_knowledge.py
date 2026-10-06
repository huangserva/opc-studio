from pathlib import Path
import copy
import tempfile
import unittest
from server import Store
from knowledge_store import Knowledge
from events_store import Events
from tests.test_gmail_source import GmailCacheTests


class GmailKnowledgeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / 'studio')
        self.k = Knowledge(self.store, sources=[])
        self.store._knowledge = self.k
        self.fixture = GmailCacheTests()
        self.fixture.account = 'demo.user@gmail.com'
        self.payload = {'account': self.fixture.account, 'query': 'newer_than:7d', 'has_more': True,
            'captured_at': '2026-10-04T12:00:00+08:00', 'messages': [self.fixture.message(content='会议改期：请确认周三十点。')]}

    def tearDown(self):
        self.temp.cleanup()

    def test_index_has_fixed_message_identity_provenance_partial_scope_and_reimport(self):
        first = self.k.ingest_gmail(self.payload)
        again = self.k.ingest_gmail(self.payload)
        self.assertEqual(first['source'], again['source'])
        self.assertEqual(again['total'], 1)
        self.assertFalse(again['syncing'])
        source = again['sources'][0]
        self.assertEqual(source['status'], 'partial')
        self.assertIn('更新索引不等于抓取新邮件', source['detail'])
        d = self.k.document(self.k.search('会议', kind='mail')['results'][0]['id'])
        self.assertEqual(d['provenance']['message_id'], 'aa11')
        self.assertEqual(d['provenance']['rfc_message_id'], '<demo-aa11@example.test>')
        self.assertEqual(d['body'], '会议改期：请确认周三十点。')
        self.assertEqual(d['provenance']['content_scope']['query'], 'newer_than:7d')
        self.assertIn('#all/aa11', d['url'])

    def test_narrower_import_keeps_prior_evidence_and_invalid_batch_preserves_index(self):
        self.k.ingest_gmail(self.payload)
        first = self.k.search('', kind='mail')['results'][0]
        second = copy.deepcopy(self.payload)
        second.update(query='subject:another', has_more=False, messages=[self.fixture.message('aa22', content='另一封邮件')])
        self.k.ingest_gmail(second)
        self.assertEqual(self.k.search('', kind='mail')['total'], 2)
        self.assertEqual(self.k.document(first['id'])['hash'], first['hash'])
        second['messages'].append({'id': '../bad'})
        with self.assertRaises(ValueError):
            self.k.ingest_gmail(second)
        self.assertFalse(self.k.sync_lock.locked())
        self.assertEqual(self.k.search('', kind='mail')['total'], 2)

    def test_gmail_and_wechat_link_into_one_evidenced_event_and_reschedule_same_action(self):
        self.k.ingest_gmail(self.payload)
        self.k.add_note({'title': '演示微信会议消息', 'body': '演示微信：项目甲会议周二十点。'})
        mail = self.k.document(self.k.search('会议', kind='mail')['results'][0]['id'])
        note = self.k.document(self.k.search('项目甲')['results'][0]['id'])
        pick = lambda d: {'id': d['id'], 'hash': d['hash'], 'start': 0, 'end': len(d['body'])}
        events = Events(self.store)
        event = events.save_event({'title': '演示项目会议', 'items': [pick(note)], 'needs_confirmation': True})
        event = events.save_event({'id': event['id'], 'revision': event['revision'], 'items': [pick(mail)], 'reason': '演示用户核对为同一会议'})
        self.assertEqual(len(event['evidence']), 2)
        self.assertEqual(event['evidence'][1]['provenance']['message_id'], 'aa11')
        event = events.save_action(event['id'], {'summary': '确认演示会议时间', 'owner': '演示用户', 'owner_is_self': True,
            'due_at': '2026-10-06T10:00:00+08:00', 'status': 'open', 'evidence_ids': [event['evidence'][0]['id']]})
        action = event['actions'][0]
        event = events.save_action(event['id'], {'id': action['id'], 'revision': action['revision'], 'due_at': '2026-10-07T10:00:00+08:00',
            'origin': 'evidence', 'evidence_ids': [event['evidence'][1]['id']]})
        self.assertEqual(event['actions'][0]['id'], action['id'])
        self.assertEqual(event['actions'][0]['due_at'], '2026-10-07T10:00:00+08:00')
        self.assertEqual(len(events.list_events(view='calendar')['events']), 1)


if __name__ == '__main__':
    unittest.main()
