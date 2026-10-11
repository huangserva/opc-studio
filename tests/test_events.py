import json
from pathlib import Path
import sqlite3
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from events_store import Events, EventConflict
from knowledge_store import Knowledge


class EventsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = SimpleNamespace(workspace=self.root / 'workspace')
        self.store.workspace.mkdir()
        self.store.knowledge = Knowledge(self.store, sources=[])
        self.wechat = self.document('wechat', 'm1', '项目会议', '演示消息：周二十点讨论项目，负责人尚未确认。', {
            'chatroom_id': 'demo-chat', 'local_id': 7, 'timestamp': 1791000000,
            'database_path': '/demo/collector.db', 'source_locator': 'wechat:demo-chat:7',
        })
        self.mail = self.document('mail', 'mail1', '项目会议', '演示邮件：会议改为周三十点，请确认是否参加。', {
            'noteId': 'demo-mail', 'sourceUrl': 'https://example.test/message/mail1',
            'source_locator': 'https://example.test/message/mail1',
        })
        self.events = Events(self.store)

    def tearDown(self):
        self.temp.cleanup()

    def document(self, source, key, title, body, provenance):
        with self.store.knowledge.db() as c:
            c.execute('CREATE TEMP TABLE seen(id TEXT PRIMARY KEY)')
            self.store.knowledge._write(c, source, key, source, title, body,
                'https://example.test/' + source + '/' + key, '2026-10-03T08:00:00+08:00', provenance=provenance)
        return self.store.knowledge.search('', source=source)['results'][0] | {'body': body}

    @staticmethod
    def pick(document):
        return {'id': document['id'], 'hash': document['hash'], 'start': 0, 'end': len(document['body'])}

    def create_event(self, **fields):
        return self.events.save_event({'title': '演示项目会议', 'items': [self.pick(self.wechat)], **fields})

    def action(self, event, **fields):
        return self.events.save_action(event['id'], {'summary': '确认会议时间',
            'evidence_ids': [event['evidence'][0]['id']], **fields})

    def test_cross_source_evidence_is_preserved_without_automatic_tasks_or_merging(self):
        event = self.create_event(picks=[self.pick(self.wechat)], items=[self.pick(self.wechat)])
        updated = self.events.save_event({'id': event['id'], 'revision': event['revision'],
            'items': [self.pick(self.mail)], 'reason': '用户确认两份资料属于同一会议'})
        self.assertEqual([e['source'] for e in updated['evidence']], ['wechat', 'mail'])
        self.assertEqual(updated['evidence'][0]['provenance'], self.wechat['provenance'])
        self.assertEqual(updated['evidence'][0]['modified'], self.wechat['modified'])
        self.assertEqual(updated['evidence'][0]['excerpt'], self.wechat['body'])
        self.assertEqual(updated['evidence'][1]['excerpt'], self.mail['body'])
        self.assertEqual(updated['actions'], [])
        another = self.create_event()
        self.assertNotEqual(event['id'], another['id'])
        self.assertEqual(self.events.list_events()['counts']['all'], 2)

    def test_creation_requires_real_current_nonempty_evidence_and_valid_ranges(self):
        invalid = [None, [], [self.pick(self.wechat) | {'start': True}],
            [self.pick(self.wechat) | {'end': len(self.wechat['body']) + 1}],
            [self.pick(self.wechat) | {'id': 'f' * 32}],
            [self.pick(self.wechat) | {'hash': '0' * 64}],
            [self.pick(self.wechat) | {'hash': ''}],
            [self.pick(self.wechat)] * 21]
        for picks in invalid:
            with self.subTest(picks=picks), self.assertRaises(ValueError):
                self.events.save_event({'title': '不能成为事件的资料', 'items': picks})
        with self.assertRaises(ValueError):
            self.events.save_event({'title': '没有证据'})
        self.assertEqual(self.events.list_events()['counts']['total'], 0)

    def test_source_change_and_deletion_keep_history_and_original_snapshot(self):
        event = self.create_event()
        original = json.loads(json.dumps(event['evidence'][0]))
        self.document('wechat', 'm1', '会议修订', '演示消息已经变更。', {'local_id': 7})
        stale = self.events.event(event['id'])
        self.assertEqual(stale['evidence'][0]['state'], 'stale')
        self.assertEqual(stale['evidence'][0]['excerpt'], original['excerpt'])
        self.assertEqual(stale['evidence'][0]['provenance'], original['provenance'])
        with self.store.knowledge.db() as c:
            c.execute('DELETE FROM documents WHERE id=?', (self.wechat['id'],))
        missing = Events(self.store).event(event['id'])
        self.assertEqual(missing['evidence'][0]['state'], 'missing')
        self.assertEqual(missing['evidence'][0]['hash'], original['hash'])
        self.assertEqual(missing['history'], event['history'])

    def test_unknown_owner_and_time_remain_unknown_and_self_is_explicit(self):
        event = self.action(self.create_event())
        action = event['actions'][0]
        self.assertEqual((action['owner'], action['due_at'], action['status']), ('', '', 'needs_confirmation'))
        self.assertFalse(action['owner_is_self'])
        self.assertEqual(self.events.list_events(view='calendar')['events'], [])
        self.assertEqual(self.events.list_events(view='mine')['events'], [])
        event = self.events.save_action(event['id'], {'id': action['id'], 'revision': action['revision'],
            'owner': '黄总', 'reason': '填写负责人'})
        self.assertEqual(self.events.list_events(view='mine')['events'], [])
        action = event['actions'][0]
        event = self.events.save_action(event['id'], {'id': action['id'], 'revision': action['revision'],
            'owner_is_self': True, 'reason': '用户明确选择由我跟进'})
        self.assertEqual(self.events.list_events(view='mine')['events'], [])
        action = event['actions'][0]
        self.events.save_action(event['id'], {'id': action['id'], 'revision': action['revision'],
            'status': 'open', 'reason': '本人已核对并接受这个行动'})
        self.assertEqual(self.events.list_events(view='mine')['counts']['mine'], 1)
        self.assertEqual(len(self.events.list_events(view='todos')['events']), 1)

    def test_my_actions_require_both_explicit_self_and_accepted_status(self):
        event = self.create_event()
        event = self.action(event, owner_is_self=True, status='needs_confirmation', due_at='2026-10-10T10:00:00+08:00')
        # A confirmed action assigned to somebody else cannot make the self
        # candidate in this same event become an accepted personal task.
        event = self.action(event, owner_is_self=False, status='open')
        self.assertEqual(self.events.list_events()['counts']['todos'], 0)
        self.assertEqual(self.events.list_events(view='mine')['events'], [])
        self.assertEqual(self.events.list_events(view='confirm')['counts']['confirm'], 1)
        own = next(a for a in event['actions'] if a['owner_is_self'])
        for status, count in [('open', 1), ('waiting', 1), ('done', 0), ('cancelled', 0), ('needs_confirmation', 0)]:
            event = self.events.save_action(event['id'], {'id': own['id'], 'revision': own['revision'],
                'status': status, 'reason': '合成测试：本人明确调整行动状态'})
            own = next(a for a in event['actions'] if a['id'] == own['id'])
            self.assertEqual(self.events.list_events(view='todos')['counts']['todos'], count, status)
            self.assertEqual(len(self.events.list_events(view='mine')['events']), count, status)

    def test_personal_action_count_counts_actions_before_event_limit_and_query(self):
        event = self.create_event(title='合成 V100 项目')
        event = self.action(event, owner_is_self=True, status='open')
        self.action(event, owner_is_self=True, status='waiting')
        other = self.create_event(title='另一件合成事项')
        self.action(other, owner_is_self=True, status='open')
        candidate = self.create_event(title='候选事项')
        self.action(candidate, owner_is_self=True, status='needs_confirmation')
        listed = self.events.list_events(view='mine', limit=1)
        self.assertEqual(len(listed['events']), 1)
        self.assertEqual(listed['counts']['mine'], 2)
        self.assertEqual(listed['counts']['mine_actions'], 3)
        matching = self.events.list_events(view='mine', query='V100')
        self.assertEqual(matching['counts']['mine'], 1)
        self.assertEqual(matching['counts']['mine_actions'], 2)

    def test_unconfirmed_date_stays_out_of_calendar_until_action_is_confirmed(self):
        event = self.action(self.create_event(), due_at='2026-10-10T10:00:00+08:00')
        self.assertEqual(self.events.list_events(view='calendar')['events'], [])
        self.assertEqual(self.events.list_events(view='confirm')['counts']['confirm'], 1)
        action = event['actions'][0]
        self.events.save_action(event['id'], {'id': action['id'], 'revision': action['revision'],
            'status': 'open', 'reason': '事项与约定时间已核对'})
        self.assertEqual(self.events.list_events(view='calendar')['counts']['calendar'], 1)

    def test_manual_reschedule_completion_and_cancellation_are_auditable(self):
        event = self.action(self.create_event(needs_confirmation=False), status='open',
            due_at='2026-10-10T10:00:00+08:00')
        self.assertEqual(len(self.events.list_events(view='calendar')['events']), 1)
        action = event['actions'][0]
        event = self.events.save_action(event['id'], {'id': action['id'], 'revision': action['revision'],
            'due_at': '2026-10-11T11:00:00+08:00', 'reason': '用户手动确认改期'})
        reschedule = event['history'][-1]
        self.assertEqual(reschedule['before']['due_at'], '2026-10-10T10:00:00+08:00')
        self.assertEqual(reschedule['after']['due_at'], '2026-10-11T11:00:00+08:00')
        self.assertEqual((reschedule['origin'], reschedule['reason']), ('manual', '用户手动确认改期'))
        action = event['actions'][0]
        with self.assertRaises(EventConflict):
            self.events.save_action(event['id'], {'id': action['id'], 'revision': 1,
                'status': 'cancelled', 'reason': '不能覆盖新版本'})
        event = self.events.save_action(event['id'], {'id': action['id'], 'revision': action['revision'],
            'status': 'done', 'reason': '用户确认已完成'})
        self.assertEqual(self.events.list_events(view='calendar')['events'], [])
        action = event['actions'][0]
        event = self.events.save_action(event['id'], {'id': action['id'], 'revision': action['revision'],
            'status': 'cancelled', 'reason': '用户确认取消保留历史'})
        self.assertEqual(event['history'][-1]['before']['status'], 'done')
        self.assertEqual(event['history'][-1]['after']['status'], 'cancelled')
        self.assertEqual(len([h for h in event['history'] if h['entity_type'] == 'action']), 4)

    def test_updates_need_manual_reason_or_new_evidence_and_are_atomic(self):
        event = self.action(self.create_event(), status='open')
        action = event['actions'][0]
        before = self.events.event(event['id'])
        for fields in [{'status': 'done'}, {'due_at': '2026-10-11T11:00:00+08:00'},
                {'status': 'done', 'origin': 'evidence', 'reason': '重复旧证据不能证明完成'}]:
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                self.events.save_action(event['id'], {'id': action['id'], 'revision': action['revision'], **fields})
        with self.assertRaises(ValueError):
            self.events.save_event({'id': event['id'], 'revision': event['revision'], 'items': [self.pick(self.mail)]})
        self.assertEqual(self.events.event(event['id']), before)
        event = self.events.save_event({'id': event['id'], 'revision': event['revision'],
            'items': [self.pick(self.mail)], 'reason': '用户将后续邮件归到会议事件'})
        mail_evidence = next(e['id'] for e in event['evidence'] if e['source'] == 'mail')
        event = self.events.save_action(event['id'], {'id': action['id'], 'revision': action['revision'],
            'status': 'done', 'origin': 'evidence', 'evidence_ids': [mail_evidence]})
        self.assertEqual(event['history'][-1]['origin'], 'evidence')
        self.assertEqual(event['actions'][0]['status'], 'done')

    def test_evidence_from_another_event_or_stale_new_evidence_cannot_change_action(self):
        event = self.action(self.create_event(), status='open')
        other = self.create_event(items=[self.pick(self.mail)])
        with self.assertRaises(ValueError):
            self.events.save_action(event['id'], {'summary': '非法关联', 'evidence_ids': [other['evidence'][0]['id']]})
        with self.assertRaises(ValueError):
            self.events.save_action(other['id'], {'id': event['actions'][0]['id'], 'revision': 1,
                'status': 'done', 'reason': '不能跨事件覆盖'})
        event = self.events.save_event({'id': event['id'], 'revision': event['revision'],
            'items': [self.pick(self.mail)], 'reason': '补充证据'})
        evidence_id = event['evidence'][1]['id']
        self.document('mail', 'mail1', '已变化的邮件', '原邮件已修订。', {})
        with self.assertRaises(EventConflict):
            self.events.save_action(event['id'], {'id': event['actions'][0]['id'], 'revision': 1,
                'status': 'done', 'origin': 'evidence', 'evidence_ids': [evidence_id]})
        self.assertEqual(self.events.event(event['id'])['actions'][0]['status'], 'open')

    def test_timezone_parameter_types_and_empty_proof_fail_before_writing(self):
        event = self.create_event()
        for due in ['2026-10-10', '2026-10-10T10:00:00', '2026-02-30T10:00:00+08:00',
                '2026-10-10T99:00:00Z', 123, None]:
            with self.subTest(due=due), self.assertRaises(ValueError):
                self.action(event, due_at=due)
        for fields in [{'owner_is_self': 'true'}, {'evidence_ids': []}, {'status': 'pending'},
                {'summary': 12}, {'unexpected': 'unsupported'}]:
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                self.action(event, **fields)
        event = self.action(event, due_at='2026-10-10T10:30Z')
        self.assertEqual(event['actions'][0]['due_at'], '2026-10-10T10:30:00+00:00')

    def test_event_revision_protects_against_concurrent_updates(self):
        event = self.create_event()
        other_store = Events(self.store)
        barrier = threading.Barrier(2)
        results = []
        def change(backend, summary):
            barrier.wait()
            try:
                backend.save_event({'id': event['id'], 'revision': 1, 'summary': summary, 'reason': '并发编辑'})
                results.append('saved')
            except EventConflict:
                results.append('conflict')
        threads = [threading.Thread(target=change, args=(backend, str(i))) for i, backend in enumerate([self.events, other_store])]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(5)
        self.assertEqual(sorted(results), ['conflict', 'saved'])
        final = self.events.event(event['id'])
        self.assertEqual(final['revision'], 2)
        self.assertEqual(len(final['history']), 2)

    def test_views_counts_literal_query_and_limit_use_bounded_metadata_reads(self):
        first = self.create_event(title='V100 项目', priority='important', needs_confirmation=False)
        self.action(first, owner='测试联系人', owner_is_self=True, status='waiting', due_at='2026-10-10T10:00:00+08:00')
        self.create_event(title='待确认的不同事件')
        counts = self.events.list_events()['counts']
        self.assertEqual((counts['all'], counts['important'], counts['mine'], counts['waiting'], counts['confirm'], counts['calendar']), (2, 1, 1, 1, 1, 1))
        self.assertEqual(len(self.events.list_events(query='v100', view='important')['events']), 1)
        self.assertEqual(len(self.events.list_events(query='测试联系人', status='waiting')['events']), 1)
        self.assertEqual(self.events.list_events(query='%')['events'], [])
        for limit in [101, 0, True, '1']:
            with self.subTest(limit=limit), self.assertRaises(ValueError):
                self.events.list_events(limit=limit)
        with patch.object(self.store.knowledge, 'document', side_effect=AssertionError('列表不得读取来源全文')):
            listed = self.events.list_events(limit=1)
            self.assertEqual(len(listed['events']), 1)
            self.assertEqual(listed['events'][0]['evidence'][0]['state'], 'fresh')

    def test_saved_evidence_budget_and_private_files_persist(self):
        doc = self.document('projects', 'large', '演示长文', '这是合成资料。' * 2500,
            {'local_path': '/demo/project.md', 'version': 'demo-v1'})
        with self.assertRaises(ValueError):
            self.events.save_event({'title': '超预算证据', 'picks': [self.pick(doc)]})
        event = self.create_event()
        reopened = Events(self.store).event(event['id'])
        self.assertEqual(reopened, event)
        self.assertEqual(self.events.root.stat().st_mode & 0o777, 0o700)
        self.assertEqual(self.events.path.stat().st_mode & 0o777, 0o600)


if __name__ == '__main__':
    unittest.main()
