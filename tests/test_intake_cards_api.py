"""创意回收工厂第二期的对外入口：提示词卡 API、定时任务登记与助手按卡片取正文。"""
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from assistant_agent import Assistant, TOOLS
from assistant_policy import policy_for
from intake_cards import IntakeCards
import intake_store
from tests.intake_dependency_patches import guard_intake_dependencies, patch_intake_dependency
from intake_store import Intake
from knowledge_store import Knowledge
from scheduled_tasks import ScheduledTasks
from server import Handler, Store, ThreadingHTTPServer

SOURCE = ('黄昏的旧码头先把木头泡软，绳索在风里轻轻晃，远处渡船只留一条灯线。'
          '拍照的人蹲下来，让水面把天空再复制一遍，人物只留一个背影，不露脸。')
ANSWER = json.dumps({'cards': [{'prompt': '旧码头黄昏，蹲拍视角，水面倒映天空，人物只留背影，暖色低对比',
                                'model': 'qi21', 'scene': '公众号头图', 'refs': ['e0']}]}, ensure_ascii=False)


class FakeWeb:
    def __call__(self):
        return self

    def fetch(self, args, job):
        raise ValueError('联网服务未完成查询')


class CardAPITests(unittest.TestCase):
    def setUp(self):
        guard_intake_dependencies(self)
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(self.tmp.name)
        self.store._knowledge = Knowledge(self.store, sources=[])
        self.intake = Intake(self.store.knowledge, self.tmp.name)
        self.store._creative_intake = self.intake
        self.cards = IntakeCards(self.intake, self.store.knowledge, self.tmp.name)
        self.store._intake_cards = self.cards
        patch_intake_dependency(self, "WebTools", FakeWeb)
        self.seed()
        with patch.object(IntakeCards, 'call_model', lambda *args: ANSWER):
            self.cards.analyze('r' * 32, {'body': ''}, threading.Event(), {'model': 'synthetic'},
                               time.monotonic() + 300)
        self.card_id = self.cards.listing()['items'][0]['id']
        self.srv = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.srv.store = self.store
        self.thread = threading.Thread(target=self.srv.serve_forever, daemon=True)
        self.thread.start()
        self.base = f'http://127.0.0.1:{self.srv.server_port}'
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        self.thread.join()
        for worker in list(self.intake.recognition_workers.values()):
            worker.join(timeout=5)
        self.tmp.cleanup()

    def api(self, path, body=None, method=None):
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(self.base + path, data=data, method=method or ('POST' if data else 'GET'),
                                         headers={'Origin': self.base, 'X-Article-Studio': '1',
                                                  'Content-Type': 'application/json'})
        try:
            with self.opener.open(request, timeout=5) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read())

    def seed(self):
        record = self.intake.receive({'kind': 'text', 'text': SOURCE, 'reason': '这个开头方式可以借'})
        return self.intake.resolve(record['id'])

    def test_cards_are_candidates_until_the_page_confirms_them(self):
        status, listing = self.api('/api/cards')
        self.assertEqual(status, 200)
        self.assertEqual(listing['schema'], 'intake-cards/v1')
        self.assertEqual(listing['counts'], {'candidate': 1, 'confirmed': 0, 'rejected': 0})
        card = listing['items'][0]
        self.assertEqual(card['source_state'], 'verified')
        self.assertEqual(card['refs'][0]['sha256'][:16], self.cards.detail(self.card_id)['refs'][0]['sha256'][:16])
        code, refused = self.api('/api/cards/%s/prompt' % self.card_id)
        self.assertEqual(code, 400)
        self.assertIn('还没确认', refused['error'])
        code, confirmed = self.api('/api/cards/%s/confirm' % self.card_id, {})
        self.assertEqual((code, confirmed['status']), (200, 'confirmed'))
        code, ready = self.api('/api/cards/%s/prompt' % self.card_id)
        self.assertEqual(code, 200)
        self.assertEqual((ready['model'], ready['kind']), ('qi21', 'image'))
        self.assertEqual(ready['prompt'], json.loads(ANSWER)['cards'][0]['prompt'])

    def test_filters_search_and_note_round_trip(self):
        self.api('/api/cards/%s/note' % self.card_id, {'note': '  先留白  '})
        _, listing = self.api('/api/cards?status=candidate&q=' + urllib.parse.quote('留白') + '&limit=10&offset=0')
        self.assertEqual(listing['total'], 1)
        self.assertEqual(listing['items'][0]['note'], '  先留白  ')
        _, none = self.api('/api/cards?status=confirmed')
        self.assertEqual(none['items'], [])
        for path in ('/api/cards?status=all', '/api/cards?limit=0', '/api/cards?q=x&other=1'):
            code, body = self.api(path)
            with self.subTest(path=path):
                self.assertEqual(code, 400)
                self.assertTrue(body['error'])

    def test_reject_and_restore_keep_the_reason(self):
        _, rejected = self.api('/api/cards/%s/reject' % self.card_id, {'reason': '太抽象'})
        self.assertEqual((rejected['status'], rejected['reason']), ('rejected', '太抽象'))
        _, restored = self.api('/api/cards/%s/restore' % self.card_id, {})
        self.assertEqual(restored['status'], 'candidate')
        code, body = self.api('/api/cards/%s/reject' % self.card_id, {'reason': 'x' * 2001})
        self.assertEqual(code, 400)
        code, body = self.api('/api/cards/%s/confirm' % self.card_id, {'note': '不接受额外字段'})
        self.assertEqual(code, 400)
        code, body = self.api('/api/cards/not-a-card/confirm', {})
        self.assertEqual(code, 400)

    def test_prompt_route_refuses_a_card_whose_source_disappeared(self):
        self.api('/api/cards/%s/confirm' % self.card_id, {})
        with self.store.knowledge.db() as connection:
            connection.execute('DELETE FROM documents')
        code, body = self.api('/api/cards/%s/prompt' % self.card_id)
        self.assertEqual(code, 400)
        self.assertIn('来源资料已不在知识库', body['error'])
        self.assertEqual(self.api('/api/cards/%s' % self.card_id)[1]['source_state'], 'missing')

    def test_kind_view_delete_and_cleanup_round_trip(self):
        code, listing = self.api('/api/cards?kind=text&view=source')
        self.assertEqual(code, 200)
        self.assertEqual((listing['total'], listing['kind'], listing['view']), (1, 'text', 'source'))
        self.assertEqual(len(listing['groups']), 1)
        drop_id = listing['groups'][0]['drop_id']
        code, none = self.api('/api/cards?kind=intel&view=time')
        self.assertEqual((code, none['total'], none['view']), (200, 0, 'time'))
        for path in ('/api/cards?kind=nope', '/api/cards?view=grid', '/api/cards?kind=text&other=1'):
            code, body = self.api(path)
            self.assertEqual(code, 400, path)
            self.assertTrue(body['error'])
        code, refused = self.api('/api/cards/cleanup', {'drop_id': drop_id})
        self.assertEqual(code, 400)
        self.assertIn('还在', refused['error'])
        code, extra = self.api('/api/cards/%s/delete' % self.card_id, {'reason': '不接受'})
        self.assertEqual(code, 400)
        code, deleted = self.api('/api/cards/%s/delete' % self.card_id, {})
        self.assertEqual((code, deleted['deleted_cards']), (200, 1))
        self.assertEqual(self.api('/api/cards')[1]['total'], 0)
        self.assertTrue(self.intake.exists(drop_id))
        # 来源还在时不能按组清理；删掉回收项后，清理只拿走卡片。
        record = self.intake.receive({'kind': 'text', 'text': SOURCE + '另一条只用来验证清理。', 'reason': '先留着'})
        resolved = self.intake.resolve(record['id'])
        with patch.object(IntakeCards, 'call_model', lambda *args: ANSWER):
            self.cards.analyze('c' * 32, {'body': ''}, threading.Event(), {'model': 'synthetic'},
                               time.monotonic() + 300)
        kept = self.cards.listing()['total']
        self.assertEqual(kept, 1)
        self.intake.delete(resolved['id'])
        self.assertEqual(self.api('/api/cards')[1]['total'], 1)
        code, cleaned = self.api('/api/cards/cleanup', {'drop_id': resolved['id']})
        self.assertEqual((code, cleaned['deleted_cards']), (200, 1))
        self.assertEqual(self.api('/api/cards')[1]['total'], 0)
        self.assertFalse(self.intake.exists(resolved['id']))
        self.assertTrue(self.intake.exists(drop_id))


class SchedulerKindTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.scheduler = ScheduledTasks(self.tmp.name, autostart=False)
        self.addCleanup(self.scheduler.close)
        config = {'model': 'synthetic', 'endpoint': 'http://127.0.0.1:1/v1/chat/completions', 'api_key': 'x'}
        self.scheduler.assistant = SimpleNamespace(
            model_document=lambda: {'profiles': [{'id': 'fake', 'config': config}]},
            settings=lambda: {'profiles': [{'profile_id': 'fake', 'display_name': '合成模型',
                                            'model': 'synthetic', 'configured': True}]})

    def payload(self, **changes):
        payload = dict(name='创意提炼', kind='intake_cards', body='优先能直接做公众号头图的描述', cwd=self.tmp.name,
                       frequency='daily', minutes=60, at='09:30', weekdays=list(range(7)), ends_at=None,
                       timeout=900, enabled=True, profile_id='fake', allow_commands=False)
        payload.update(changes)
        return payload

    def test_intake_cards_is_a_selectable_kind_with_its_own_executor(self):
        self.scheduler.save(self.payload())
        task = self.scheduler.listing()['tasks'][0]
        self.assertEqual(task['kind'], 'intake_cards')
        self.assertEqual(task['executor_name'], '创意提炼（Pi）')
        self.assertEqual(task['assistant_id'], 'isolated')
        self.assertEqual(task['tool_groups'], [])
        self.assertIsNotNone(task['next_run'])

    def test_intake_cards_rejects_commands_clue_delivery_and_missing_model(self):
        for changes in ({'allow_commands': True}, {'result_destination': 'clues'}, {'clue_id': 'a' * 32},
                        {'profile_id': 'missing'}, {'assistant_id': 'workbench'}, {'tool_groups': ['memory']}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.scheduler.save(self.payload(**changes))

    def test_missing_analyzer_entry_is_an_honest_failure(self):
        saved = self.scheduler.save(self.payload())
        run = self.scheduler.control(saved['id'], 'run')['id']
        for _ in range(100):
            row = self.scheduler.get_run(run)
            if row['status'] != 'running':
                break
            time.sleep(.05)
        self.assertEqual(row['status'], 'failed')
        self.assertIn('创意提炼入口未连接', row['output'])

    def test_analyzer_output_is_recorded_and_partial_keeps_no_exit_code(self):
        saved = self.scheduler.save(self.payload())
        receipt = {'checked': 1, 'cards': 1, 'dropped': 0, 'drop_reasons': [], 'errors': [],
                   'waiting': 2, 'has_more': True, 'summary': '本轮提炼 1 条回收项'}
        self.scheduler.intake_analyzer = lambda run_id, task, cancel, config, deadline: (
            'partial', 0, json.dumps(receipt, ensure_ascii=False))
        run = self.scheduler.control(saved['id'], 'run')['id']
        for _ in range(100):
            row = self.scheduler.get_run(run)
            if row['status'] != 'running':
                break
            time.sleep(.05)
        self.assertEqual(row['status'], 'partial')
        self.assertIsNone(row['exit_code'])
        self.assertEqual(json.loads(row['output'])['cards'], 1)


class AssistantCardTests(unittest.TestCase):
    def setUp(self):
        # Bypass the constructor so the tests cannot start Pi or a model service.
        self.agent = Assistant.__new__(Assistant)
        self.agent.generation = Mock(spec=['start'])
        self.agent.generation.start.return_value = {'id': 'mock-image', 'status': 'connecting',
                                                    'model_name': 'Qwen Image 2.1 · 4090'}
        self.agent.image_skill = SimpleNamespace(compose=lambda prompt, style: (prompt, None))
        self.agent.media_preferences = Mock(return_value={'image': {'model': 'qi21', 'size': '1024x1024'},
                                                          'video': {'model': 'h3', 'size': '768x432', 'frames': 56}})
        self.cards = {'c' * 32: {'id': 'c' * 32, 'prompt': '旧码头黄昏，人物只留背影', 'model': 'qi21',
                                 'kind': 'image', 'note': ''}}

        def prompt_card(card_id):
            if card_id not in self.cards:
                raise ValueError('这张卡还没确认，确认后才能在生成页使用')
            return self.cards[card_id]

        self.agent.store = SimpleNamespace(intake_cards=SimpleNamespace(prompt_card=prompt_card))
        self.job = {'id': 'mock-turn', 'cancel': False, 'context': {}, 'conversation_id': 'mock-conversation',
                    'policy': policy_for('生成一张图')}

    def test_tool_schema_offers_the_card_without_losing_the_guard(self):
        schema = next(tool['function']['parameters'] for tool in TOOLS if tool['function']['name'] == 'generate_media')
        self.assertIn('card', schema['properties'])
        self.assertEqual(schema['required'], ['model'])
        self.assertNotIn('references', schema['properties'])
        with self.assertRaises(ValueError):
            self.agent.execute('generate_media', {'model': 'qi21', 'references': {'first': 'a' * 32}}, self.job)
        self.agent.generation.start.assert_not_called()

    def test_confirmed_card_supplies_the_prompt_the_service_reads(self):
        result = self.agent.execute('generate_media', {'model': 'qi21', 'card': 'c' * 32}, self.job)
        self.assertEqual(result['media_job'], 'mock-image')
        self.assertEqual(result['prompt_card'], 'c' * 32)
        self.assertIn('已确认提示词卡', result['summary'])
        args = self.agent.generation.start.call_args.args[0]
        self.assertEqual(args['prompt'], '旧码头黄昏，人物只留背影')
        self.assertNotIn('card', args)

    def test_unconfirmed_card_or_a_rewritten_prompt_is_refused(self):
        with self.assertRaisesRegex(ValueError, '还没确认'):
            self.agent.execute('generate_media', {'model': 'qi21', 'card': 'd' * 32}, self.job)
        with self.assertRaisesRegex(ValueError, '不能同时自行填写'):
            self.agent.execute('generate_media', {'model': 'qi21', 'card': 'c' * 32, 'prompt': '改成我自己写的'}, self.job)
        video_job = {'id': 'mock-video-turn', 'cancel': False, 'context': {}, 'conversation_id': 'mock-conversation',
                     'policy': policy_for('生成一个视频')}
        with self.assertRaisesRegex(ValueError, '不是同一类'):
            self.agent.execute('generate_media', {'model': 'h3', 'card': 'c' * 32}, video_job)
        with self.assertRaisesRegex(ValueError, '请填写画面描述'):
            self.agent.execute('generate_media', {'model': 'qi21'}, self.job)
        self.agent.generation.start.assert_not_called()


if __name__ == '__main__':
    unittest.main()
