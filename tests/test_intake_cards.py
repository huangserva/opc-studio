"""创意回收工厂第二期：候选提示词卡的提炼校验、来源核对与确认流（intake_cards）。"""
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import MagicMock, patch
from isolation_patches import module_time, module_thread

import hashlib
import uuid

from intake_cards import IntakeCards, analysis_sentence, copied_ref_id, parse_json, source_channel
from intake_store import Intake
from knowledge_store import Knowledge
from server import Store

SOURCE = ('黄昏的旧码头先把木头泡软，绳索在风里轻轻晃，远处渡船只留一条灯线。'
          '拍照的人蹲下来，让水面把天空再复制一遍，人物只留一个背影，不露脸。')
ANSWER = json.dumps({'cards': [{'prompt': '旧码头黄昏，蹲拍视角，水面倒映天空，人物只留背影，暖色低对比',
                                'model': 'qi21', 'scene': '公众号头图', 'refs': ['e0']}]}, ensure_ascii=False)


def payload(value):
    return json.dumps(value, ensure_ascii=False)


class CardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(self.tmp.name)
        self.store._knowledge = Knowledge(self.store, sources=[])
        self.intake = Intake(self.store.knowledge, self.tmp.name)
        self.cards = IntakeCards(self.intake, self.store.knowledge, self.tmp.name)
        self.calls = []

    def tearDown(self):
        for worker in list(self.intake.recognition_workers.values()):
            worker.join(timeout=5)
        self.tmp.cleanup()

    def drop(self, text=SOURCE, reason='这个开头方式可以借'):
        record = self.intake.receive({'kind': 'text', 'text': text, 'reason': reason})
        return self.intake.resolve(record['id'])

    def answer_with(self, payload, calls=None):
        """Replace only the model turn; every validation below still runs for real."""
        def fake(identity, prompt, system, cancel, config, deadline):
            self.calls.append({'identity': identity, 'prompt': prompt, 'system': system})
            return payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
        self.cards.call_model = fake

    def fail_retry(self, run, at):
        def failure(*args):
            self.calls.append(args[0]); raise ValueError('合成模型失败')
        self.cards.call_model = failure
        with module_time('intake_cards', time=lambda: at, monotonic=lambda: 0):
            return self.cards.analyze(run*32, {'body': ''}, threading.Event(),
                                      {'model': 'synthetic', 'timeout_seconds': 10}, 300)

    def test_intake_due_blocks_the_next_run(self):
        self.drop()
        self.fail_retry('a', 1000)
        self.fail_retry('b', 2799)
        self.assertEqual(len(self.calls), 1)
        self.fail_retry('c', 2800)
        self.assertEqual(len(self.calls), 2)
        with self.cards.db() as db:
            row = db.execute('SELECT * FROM extraction_retry').fetchone()
            self.assertEqual((row['attempts'], row['next_retry_at'], row['first_failed_at']), (2, 6400, 1000))
        self.assertEqual(self.cards.listing()['pending_drops'], 1)

    def test_intake_third_attempt_or_24h_stops(self):
        self.drop()
        for run, at in (('a',1000),('b',2800),('c',6400),('d',10000)):
            result = self.fail_retry(run, at)
        self.assertEqual(len(self.calls), 3)
        self.assertTrue(any('需核对' in error for error in json.loads(result[2])['errors']))
        with self.cards.db() as db:
            self.assertEqual(db.execute('SELECT state FROM extraction_retry').fetchone()[0], 'needs_review')
            self.assertEqual(db.execute('SELECT COUNT(*) FROM seen').fetchone()[0], 0)
        other = self.drop(SOURCE+'合成第二条不同资料')
        self.fail_retry('e', 20000)
        self.fail_retry('f', 106400)
        self.assertEqual(len(self.calls), 4)
        with self.cards.db() as db:
            self.assertEqual(db.execute('SELECT state FROM extraction_retry WHERE drop_id=?', (other['id'],)).fetchone()[0], 'needs_review')

    def test_intake_delete_and_new_fingerprint_reset(self):
        drop = self.drop()
        self.fail_retry('a', 1000)
        with self.store.knowledge.db() as db:
            db.execute('UPDATE documents SET body=?,hash=? WHERE id=?',
                       (SOURCE+'合成正文更新', 'new-synthetic-hash', drop['document_id']))
        self.fail_retry('b', 1001)
        self.assertEqual(len(self.calls), 2)
        with self.cards.db() as db:
            rows = db.execute('SELECT fingerprint,attempts FROM extraction_retry').fetchall()
            self.assertEqual(len(rows), 2)
            self.assertTrue(all(row['attempts']==1 for row in rows))
        self.intake.delete(drop['id'])
        with self.cards.db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM extraction_retry').fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM extraction_epochs').fetchone()[0], 0)

    def test_manual_reanalysis_opens_new_epoch(self):
        drop = self.drop()
        self.fail_retry('a', 1000)
        self.cards.model_provider = lambda: {'model':'synthetic','timeout_seconds':10}
        self.cards.busy_probe = lambda config: False
        self.cards.analyze_drop(drop['id'], force=True)
        self.fail_retry('b', 1001)
        self.assertEqual(len(self.calls), 3)  # one manual failed call plus fresh scheduled attempt
        with self.cards.db() as db:
            self.assertEqual(db.execute('SELECT epoch FROM extraction_epochs WHERE drop_id=?', (drop['id'],)).fetchone()[0], 1)
            self.assertEqual([row[0] for row in db.execute('SELECT attempts FROM extraction_retry ORDER BY epoch')], [1,1])

    def test_intake_and_instant_do_not_double_count(self):
        from tests.test_intake_instant_analysis import READING
        drop = self.drop()
        self.cards.model_provider = lambda: {'model':'synthetic','timeout_seconds':10}
        self.cards.busy_probe = lambda config: False
        answers = iter([json.dumps(READING, ensure_ascii=False), ANSWER])
        self.cards.call_model = lambda *args: next(answers)
        self.assertEqual(self.cards.analyze_drop(drop['id'])['status'], 'done')
        self.cards.call_model = lambda *args: self.fail('Seen fingerprint must not be reanalyzed')
        with module_time('intake_cards', monotonic=lambda: 0):
            self.cards.analyze('r'*32, {'body':''}, threading.Event(), {'model':'synthetic'}, 300)
        with self.cards.db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM extraction_retry').fetchone()[0], 0)

    def test_evidence_windows_are_numbered_and_fingerprinted(self):
        record = self.drop()
        document, refs = self.cards.evidence(record)
        self.assertEqual(list(refs), ['e0'])
        ref = refs['e0']
        self.assertEqual((ref['origin'], ref['document_id'], ref['start']), ('document', document['id'], 0))
        self.assertEqual(ref['text'], document['body'])
        self.assertEqual(len(ref['sha256']), 64)
        # The model only ever sees the label and the text, never the fingerprint.
        prompt = self.cards.card_prompt(record, document, refs, {'body': ''})
        self.assertIn('"text"', prompt)
        self.assertNotIn(ref['sha256'], prompt)

    def test_evidence_covers_recognition_text_and_caps_the_windows(self):
        record = self.drop('短正文')
        document, refs = self.cards.evidence(record)
        self.assertEqual(len(refs), 1)
        long_text = '长' * 9000
        record['recognition'] = {'status': 'succeeded', 'text': long_text, 'label': 'AI 语音转写，未核实'}
        document, refs = self.cards.evidence(record)
        self.assertLessEqual(len(refs), 8)
        self.assertTrue(any(ref['origin'] == 'recognition' for ref in refs.values()))
        self.assertTrue(all(len(ref['text']) <= 1200 for ref in refs.values()))

    def test_model_may_only_use_given_ref_numbers(self):
        accepted, rejected = self.cards.parse_cards(
            payload({'cards': [{'prompt': '新描述', 'model': 'qi21', 'scene': '', 'refs': ['e7']}]}), {'e0': {'id': 'e0', 'text': SOURCE}})
        self.assertEqual(accepted, [])
        self.assertEqual(rejected[0][1], '来源引用编号不存在')

    def test_card_without_citation_is_dropped(self):
        _, rejected = self.cards.parse_cards(
            payload({'cards': [{'prompt': '新描述', 'model': 'qi21', 'refs': []}]}), {'e0': {'id': 'e0', 'text': SOURCE}})
        self.assertEqual(rejected[0][1], '缺少来源引用')

    def test_hand_copied_source_is_dropped(self):
        _, refs = None, {'e0': {'id': 'e0', 'text': SOURCE}}
        copied = payload({'cards': [{'prompt': '请把这段直接用：' + SOURCE, 'model': 'qi21', 'scene': '', 'refs': ['e0']}]})
        accepted, rejected = self.cards.parse_cards(copied, refs)
        self.assertEqual(accepted, [])
        self.assertEqual(rejected[0][1], '提示词手抄原文')
        self.assertEqual(rejected[0][2], {'ref': 'e0'})
        self.assertEqual(copied_ref_id('旧码头黄昏，蹲拍视角，水面倒映天空', refs), '')

    def test_unsupported_model_and_unknown_fields_are_dropped(self):
        refs = {'e0': {'id': 'e0', 'text': SOURCE}}
        _, rejected = self.cards.parse_cards(payload(
            {'cards': [{'prompt': '新描述', 'model': 'midjourney', 'refs': ['e0']},
                       {'prompt': '新描述', 'model': 'qi21', 'refs': ['e0'], 'url': 'https://evil.example'}]}), refs)
        self.assertEqual([reason for _, reason, _ in rejected], ['适配模型不在已接入范围', '卡片字段无效'])

    def test_too_many_citations_and_missing_list_are_refused(self):
        refs = {f'e{n}': {'id': f'e{n}', 'text': '片段' + str(n)} for n in range(6)}
        _, rejected = self.cards.parse_cards(payload(
            {'cards': [{'prompt': '新描述', 'model': 'qi21', 'refs': [f'e{n}' for n in range(4)]}]}), refs)
        self.assertEqual(rejected[0][1], '单卡来源引用过多')
        with self.assertRaises(ValueError):
            self.cards.parse_cards(payload({'clues': []}), refs)
        with self.assertRaises(ValueError):
            parse_json('这不是 JSON')

    def test_analyze_creates_candidate_cards_and_remembers_processed_drops(self):
        record = self.drop()
        self.answer_with(ANSWER)
        status, code, output = self.cards.analyze('r' * 32, {'body': ''}, threading.Event(), {'model': 'mock-model'},
                                                  time.monotonic() + 300)
        self.assertEqual((status, code), ('succeeded', None))
        receipt = json.loads(output)
        self.assertEqual((receipt['checked'], receipt['cards'], receipt['dropped']), (1, 1, 0))
        listing = self.cards.listing()
        self.assertEqual(listing['counts'], {'candidate': 1, 'confirmed': 0, 'rejected': 0})
        card = listing['items'][0]
        self.assertEqual(card['status'], 'candidate')
        self.assertEqual(card['note'], '这个开头方式可以借')          # 收藏理由原样带进备注
        self.assertEqual(card['source_state'], 'verified')
        self.assertEqual(card['refs'][0]['ref'], 'e0')
        self.assertEqual(card['source']['reason'], '这个开头方式可以借')
        self.assertTrue(card['fill_url'].startswith('/lab.html?kind=image&card='))
        # The same drop is not fed to the model twice.
        self.calls.clear()
        status, _, output = self.cards.analyze('r2' + 'r' * 30, {'body': ''}, threading.Event(), {}, time.monotonic() + 300)
        self.assertEqual(self.calls, [])
        self.assertEqual(json.loads(output)['checked'], 0)
        self.assertEqual(status, 'succeeded')

    def test_failed_model_call_is_retried_next_run(self):
        self.drop()

        def broken(identity, prompt, system, cancel, config, deadline):
            self.calls.append(identity)
            raise ValueError('模型未返回有效正文，本条未推进')

        self.cards.call_model = broken
        with module_time('intake_cards', time=lambda: 1000):
            first = json.loads(self.cards.analyze('r' * 32, {'body': ''}, threading.Event(), {}, time.monotonic() + 300)[2])
        self.assertEqual(first['checked'], 0)
        self.assertEqual(first['cards'], 0)
        self.assertIn('模型未返回有效正文', first['errors'][0])
        with module_time('intake_cards', time=lambda: 2799):
            self.cards.analyze('r2' + 'r' * 30, {'body': ''}, threading.Event(), {}, time.monotonic() + 300)
        self.assertEqual(len(self.calls), 1)
        with module_time('intake_cards', time=lambda: 2800):
            self.cards.analyze('r3' + 'r' * 30, {'body': ''}, threading.Event(), {}, time.monotonic() + 300)
        self.assertEqual(len(self.calls), 2)

    def test_unrecognised_media_body_is_recorded_without_a_model_call(self):
        record = self.drop('本机视频：clip.mp4，MP4，时长未读取，5 B。视频正文与语音未读取。')
        self.answer_with(ANSWER)
        receipt = json.loads(self.cards.analyze('r' * 32, {'body': ''}, threading.Event(), {}, time.monotonic() + 300)[2])
        self.assertEqual(receipt['cards'], 0)
        self.assertEqual(receipt['drop_reasons'], ['资料正文未识别，暂不提炼'])
        self.assertEqual(self.calls, [])
        self.assertEqual(self.cards.rejections()[0]['reason'], '资料正文未识别，暂不提炼')
        self.assertEqual(self.intake.detail(record['id'])['status'], 'resolved')
        # Once recognition lands, the same drop becomes extractable material.
        raw = self.intake._raw(record['id'])
        with self.intake.db() as connection:
            connection.execute('UPDATE drops SET recognition=? WHERE id=?',
                               (json.dumps({'status': 'succeeded', 'text': '旁白：先把镜头放在水面上。',
                                            'label': 'AI 语音转写，未核实'}, ensure_ascii=False), record['id']))
        self.assertTrue(self.cards.extractable(self.cards.evidence(self.intake.detail(record['id']))[1]))
        self.assertFalse(self.cards.extractable(self.cards.evidence(record)[1]))
        receipt = json.loads(self.cards.analyze('r2' + 'r' * 30, {'body': ''}, threading.Event(), {}, time.monotonic() + 300)[2])
        self.assertEqual((receipt['checked'], receipt['cards']), (1, 1))
        self.assertEqual(len(self.calls), 1)

    def test_duplicate_prompt_is_dropped(self):
        self.drop()
        self.answer_with(ANSWER)
        self.cards.analyze('r' * 32, {'body': ''}, threading.Event(), {}, time.monotonic() + 300)
        self.drop(SOURCE.replace('码头', '渡口'))
        self.cards.analyze('r2' + 'r' * 30, {'body': ''}, threading.Event(), {}, time.monotonic() + 300)
        self.assertEqual(self.cards.listing()['counts']['candidate'], 1)
        self.assertIn('与已有卡片重复', [row['reason'] for row in self.cards.rejections()])

    def test_batch_limit_defers_the_rest_and_reports_has_more(self):
        for index in range(7):
            self.drop(SOURCE + '第 %d 条回收项。' % index)
        self.answer_with({'cards': []})
        receipt = json.loads(self.cards.analyze('r' * 32, {'body': ''}, threading.Event(), {}, time.monotonic() + 300)[2])
        self.assertEqual(receipt['checked'], 6)
        self.assertTrue(receipt['has_more'])
        self.assertEqual(receipt['waiting'], 1)
        self.assertEqual(self.cards.listing()['pending_drops'], 1)

    def test_deadline_leaves_room_for_one_more_call(self):
        # 20r replaces the fixed 90-second reserve: deterministic dynamic boundary.
        self.drop()
        self.answer_with(ANSWER)
        with module_time('intake_cards', monotonic=lambda: 0):
            status, _, output = self.cards.analyze('r' * 32, {'body': ''}, threading.Event(),
                                                   {'timeout_seconds': 10}, 14.9)
        self.assertEqual(self.calls, [])
        self.assertEqual(status, 'partial')
        self.assertIn('运行期限临近', json.loads(output)['errors'][0])

    def test_intake_dynamic_budget_skips_the_drop(self):
        self.drop()
        self.answer_with({'cards': []})
        with module_time('intake_cards', monotonic=lambda: 0):
            result = self.cards.analyze('r' * 32, {'body': ''}, threading.Event(),
                                        {'timeout_seconds': 100}, 104.9)
        self.assertEqual(self.calls, [])
        self.assertIn('运行期限临近', json.loads(result[2])['errors'][0])
        with module_time('intake_cards', monotonic=lambda: 0):
            self.cards.analyze('s' * 32, {'body': ''}, threading.Event(),
                               {'timeout_seconds': 10}, 15)
        self.assertEqual(len(self.calls), 1)

    def test_intake_call_model_aborted_or_blank_is_not_seen(self):
        self.drop()
        clock_base=time.time()
        for number, result in enumerate(({'status': 'error', 'answer': 'aborted text', 'stop_reason': 'aborted'},
                       {'status': 'done', 'answer': ' ', 'stop_reason': 'stop'})):
            with self.subTest(result=result), patch('pi_runtime.PiRuntime') as runtime:
                runtime.return_value.run.return_value = {**result, 'usage': {'input': 0, 'output': 0}}
                with module_time('intake_cards', monotonic=lambda: 0, time=lambda: clock_base+1800*number), module_thread('intake_cards', constructor=MagicMock()):
                    status, _, output = self.cards.analyze(('r' if number==0 else 's') * 32, {'body': ''}, threading.Event(),
                                                          {'timeout_seconds': 100}, 105)
                self.assertTrue(json.loads(output)['errors'])
                self.assertEqual(self.cards.listing()['pending_drops'], 1)
                runtime.assert_called_once()
                receipt = json.loads((runtime.call_args.args[0].folder/'execution-receipt.json').read_text())
                self.assertEqual(receipt['stop_reason'], result['stop_reason'])
                self.assertTrue(receipt['sent']); self.assertEqual(receipt['provider_requests'], 1)
                self.assertEqual(receipt['failure_kind'], 'model_transient')

    def test_intake_call_model_budget_and_cancel_receipts(self):
        for kind, remaining in (('not_sent', 104.9), ('deadline', 0), ('cancelled', 0)):
            with self.subTest(kind=kind), patch('pi_runtime.PiRuntime') as runtime:
                cancel = threading.Event()
                if kind == 'cancelled': cancel.set()
                with module_time('intake_cards', monotonic=lambda: 0), module_thread('intake_cards', constructor=lambda **kwargs: MagicMock()):
                    with self.assertRaises(ValueError) as caught:
                        self.cards.call_model(kind, 'synthetic secret prompt', 'synthetic system', cancel,
                                              {'timeout_seconds': 100}, remaining)
                self.assertEqual(getattr(caught.exception, 'failure_kind', None), kind)
                runtime.assert_not_called()
                receipt = json.loads((self.cards.pi_folder/kind/'execution-receipt.json').read_text())
                self.assertFalse(receipt['sent']); self.assertEqual(receipt['provider_requests'], 0)
                self.assertNotIn('synthetic secret prompt', json.dumps(receipt))
        with patch('pi_runtime.PiRuntime') as runtime:
            runtime.return_value.run.return_value = {'status': 'done', 'answer': ANSWER, 'stop_reason': 'stop',
                                                       'usage': {'input': 1, 'output': 2}}
            with module_time('intake_cards', monotonic=lambda: 0), module_thread('intake_cards', constructor=lambda **kwargs: MagicMock()):
                self.assertEqual(self.cards.call_model('allowed', 'synthetic prompt', 'system', threading.Event(),
                                                       {'timeout_seconds': 100}, 105), ANSWER)
            runtime.assert_called_once()
            receipt = json.loads((self.cards.pi_folder/'allowed'/'execution-receipt.json').read_text())
            self.assertEqual(receipt['usage'], {'input': 1, 'output': 2})
            self.assertTrue(receipt['sent']); self.assertEqual(receipt['provider_requests'], 1)

    def test_instant_analysis_keeps_two_call_cap(self):
        from tests.test_intake_instant_analysis import READING
        record = self.drop()
        self.cards.model_provider = lambda: {'model': 'synthetic', 'timeout_seconds': 10}
        self.cards.busy_probe = lambda config: False
        answers = iter(['not-json', json.dumps(READING, ensure_ascii=False)])
        calls = []
        def model(*args):
            calls.append(args[0]); return next(answers)
        self.cards.call_model = model
        state = self.cards.analyze_drop(record['id'])
        self.assertEqual(state['status'], 'done')
        self.assertEqual(len(calls), 2)
        self.assertTrue(calls[1].endswith('-repair'))
        self.assertIn('两次', state['cards_error'])

    def test_only_confirmed_cards_can_be_used(self):
        self.drop()
        self.answer_with(ANSWER)
        self.cards.analyze('r' * 32, {'body': ''}, threading.Event(), {}, time.monotonic() + 300)
        card = self.cards.listing()['items'][0]
        with self.assertRaisesRegex(ValueError, '还没确认'):
            self.cards.prompt_card(card['id'])
        confirmed = self.cards.decide(card['id'], 'confirm')
        self.assertEqual(confirmed['status'], 'confirmed')
        ready = self.cards.prompt_card(card['id'])
        self.assertEqual((ready['id'], ready['model'], ready['kind']), (card['id'], 'qi21', 'image'))
        self.assertEqual(ready['prompt'], json.loads(ANSWER)['cards'][0]['prompt'])

    def test_confirm_rejects_and_restores_with_reason(self):
        self.drop()
        self.answer_with(ANSWER)
        self.cards.analyze('r' * 32, {'body': ''}, threading.Event(), {}, time.monotonic() + 300)
        card = self.cards.listing()['items'][0]
        rejected = self.cards.decide(card['id'], 'reject', '太抽象，不像能直接提交的描述')
        self.assertEqual((rejected['status'], rejected['reason']), ('rejected', '太抽象，不像能直接提交的描述'))
        self.assertEqual(self.cards.listing('rejected')['total'], 1)
        with self.assertRaisesRegex(ValueError, '还没确认'):
            self.cards.prompt_card(card['id'])
        self.assertEqual(self.cards.decide(card['id'], 'restore')['status'], 'candidate')
        with self.assertRaises(ValueError):
            self.cards.decide(card['id'], 'delete')
        with self.assertRaises(ValueError):
            self.cards.decide('zz', 'confirm')

    def test_confirm_refuses_when_source_text_changed(self):
        record = self.drop()
        self.answer_with(ANSWER)
        self.cards.analyze('r' * 32, {'body': ''}, threading.Event(), {}, time.monotonic() + 300)
        card = self.cards.listing()['items'][0]
        with self.store.knowledge.db() as connection:
            connection.execute('UPDATE documents SET body=? WHERE id=?', ('正文被改掉了。', record['document_id']))
        detail = self.cards.detail(card['id'])
        self.assertEqual(detail['source_state'], 'stale')
        with self.assertRaisesRegex(ValueError, '指纹已变化'):
            self.cards.decide(card['id'], 'confirm')
        with self.store.knowledge.db() as connection:
            connection.execute('DELETE FROM documents WHERE id=?', (record['document_id'],))
        self.assertEqual(self.cards.detail(card['id'])['source_state'], 'missing')

    def test_note_saves_verbatim_and_search_filters(self):
        self.drop()
        self.answer_with(ANSWER)
        self.cards.analyze('r' * 32, {'body': ''}, threading.Event(), {}, time.monotonic() + 300)
        card = self.cards.listing()['items'][0]
        saved = self.cards.save_note(card['id'], '  先留白，等拍完再改  \n')
        self.assertEqual(saved['note'], '  先留白，等拍完再改  \n')
        self.assertEqual(self.cards.listing(query='留白')['total'], 1)
        self.assertEqual(self.cards.listing(query='不存在')['total'], 0)
        self.assertEqual(self.cards.listing(status='confirmed')['total'], 0)
        with self.assertRaises(ValueError):
            self.cards.listing(status='all')
        with self.assertRaises(ValueError):
            self.cards.save_note(card['id'], 'x' * 1001)

    def test_listing_and_rejections_reject_bad_arguments(self):
        for kwargs in ({'status': 'x'}, {'query': 'y' * 201}, {'limit': 0}, {'limit': True}, {'offset': -1},
                       {'kind': 'nope'}, {'view': 'grid'}):
            with self.subTest(**kwargs), self.assertRaises(ValueError):
                self.cards.listing(**kwargs)
        with self.assertRaises(ValueError):
            self.cards.rejections(0)
        self.assertEqual(self.cards.stats()['counts']['candidate'], 0)

    def _write_analysis(self, record, summary='旧码头的黄昏适合低对比背影', related=None):
        data = json.dumps({'summary': summary, 'points': ['水面倒影'], 'why': '开头方式能借',
                           'angles': [{'title': '蹲拍', 'how': '让水面复制天空'}],
                           'related': related if related is not None else [{'title': '另一条'}]}, ensure_ascii=False)
        moment = '2026-10-09T02:00:00+00:00'
        with self.cards.db() as connection:
            connection.execute('''INSERT INTO analysis(drop_id,document_id,status,error,retryable,fingerprint,run_id,
                model_name,attempts,started_at,finished_at,updated_at,data) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                (record['id'], record.get('document_id') or '', 'done', '', 0, 'abc', 'r' * 32, 'synthetic',
                 1, moment, moment, moment, data))

    def _analysis_status(self, drop_id):
        with self.cards.db() as connection:
            row = connection.execute('SELECT status FROM analysis WHERE drop_id=?', (drop_id,)).fetchone()
        return row['status'] if row else ''

    def _clone_card(self, drop_id, prompt):
        with self.cards.db() as connection:
            base = dict(connection.execute('SELECT * FROM cards ORDER BY created_at LIMIT 1').fetchone())
            base['id'] = uuid.uuid4().hex
            base['drop_id'] = drop_id
            base['prompt'] = prompt
            base['prompt_sha'] = hashlib.sha256(prompt.encode()).hexdigest()
            base['created_at'] = '2026-10-09T03:00:00+00:00'
            base['updated_at'] = base['created_at']
            columns = list(base)
            connection.execute('INSERT INTO cards(%s) VALUES(%s)' % (','.join(columns), ','.join('?' * len(columns))),
                               [base[column] for column in columns])

    def test_listing_groups_filters_and_deletes_only_cards(self):
        self.assertEqual(source_channel('image', ''), 'image')
        self.assertEqual(source_channel('video', '随便'), 'video')
        self.assertEqual(source_channel('text', '情报采集 · 公众号「甲」'), 'intel')
        self.assertEqual(source_channel('link', ''), 'link')
        self.assertEqual(analysis_sentence({
            'summary': '旧码头的黄昏适合低对比背影', 'points': ['水面倒影'], 'why': '开头方式能借',
            'angles': [{'title': '蹲拍', 'how': '让水面复制天空'}], 'related': [{'title': '另一条'}],
        }), '旧码头的黄昏适合低对比背影。值得收：开头方式能借。可写角度：蹲拍。相关 1 条。')
        self.assertEqual(analysis_sentence({'points': ['只有要点']}), '要点：只有要点。')
        self.assertEqual(analysis_sentence({}), '')

        text = self.drop()
        self.answer_with({'cards': [
            {'prompt': '旧码头黄昏，蹲拍视角，水面倒映天空，人物只留背影，暖色低对比',
             'model': 'qi21', 'scene': '公众号头图', 'refs': ['e0']},
            {'prompt': '夜色渡口，远处只留一条灯线，低机位，不出现人脸，冷暖交界',
             'model': 'krea2', 'scene': '封面', 'refs': ['e0']},
        ]})
        self.cards.analyze('r' * 32, {'body': ''}, threading.Event(), {}, time.monotonic() + 300)
        self._write_analysis(text)
        intel = self.drop(SOURCE + '情报这一条单独成篇。', '情报采集 · 公众号「样例号」')
        self.answer_with({'cards': [{'prompt': '公众号情报只取结构：标题在上，正文留白，不出现具体人名',
                                     'model': 'h3', 'scene': '情报封面', 'refs': ['e0']}]})
        self.cards.analyze('i' * 32, {'body': ''}, threading.Event(), {}, time.monotonic() + 300)
        self._write_analysis(intel, '情报只保留结构')
        link = self.intake.receive({'kind': 'link', 'url': 'https://example.com/dock',
                                    'title': '码头链接', 'reason': '只是一条链接'})
        self._clone_card(link['id'], '链接来源单独一句描述，不抄任何原文窗口')

        listing = self.cards.listing()
        groups = {group['drop_id']: group for group in listing['groups']}
        self.assertEqual(len(groups[text['id']]['cards']), 2)
        self.assertIn('可写角度：蹲拍', groups[text['id']]['analysis_line'])
        self.assertIn('文字', groups[text['id']]['source']['title'])
        self.assertTrue(groups[text['id']]['source']['received_at'])
        self.assertFalse(groups[text['id']]['source']['deleted'])
        self.assertEqual(groups[intel['id']]['source']['channel'], 'intel')
        self.assertEqual(groups[intel['id']]['source']['channel_label'], '情报推送')
        self.assertIn('情报只保留结构', groups[intel['id']]['analysis_line'])
        self.assertEqual(groups[link['id']]['source']['channel'], 'link')
        self.assertEqual(groups[link['id']]['source']['link'], 'https://example.com/dock')
        self.assertEqual(groups[link['id']]['analysis_line'], '')
        self.assertEqual(self.cards.listing(kind='text')['total'], 2)
        self.assertEqual({item['drop_id'] for item in self.cards.listing(kind='intel')['items']}, {intel['id']})
        self.assertEqual({item['drop_id'] for item in self.cards.listing(kind='link')['items']}, {link['id']})
        self.assertEqual(self.cards.listing(kind='image')['total'], 0)
        self.assertEqual(self.cards.listing(kind='video')['total'], 0)
        timed = [item['created_at'] for item in self.cards.listing(view='time')['items']]
        self.assertEqual(timed, sorted(timed, reverse=True))

        text_ids = [item['id'] for item in listing['items'] if item['drop_id'] == text['id']]
        self.cards.delete_card(text_ids[0])
        self.assertEqual(len([item for item in self.cards.listing()['items'] if item['drop_id'] == text['id']]), 1)
        self.assertTrue(self.intake.exists(text['id']))
        self.assertEqual(self._analysis_status(text['id']), 'done')
        self.assertEqual(self._analysis_status(intel['id']), 'done')
        with self.assertRaisesRegex(ValueError, '还在'):
            self.cards.delete_deleted_source(text['id'])

        self.intake.delete(text['id'])
        gone = self.cards.listing()
        self.assertTrue(gone['groups'][-1]['source']['deleted'])
        self.assertEqual(gone['groups'][-1]['drop_id'], text['id'])
        self.assertEqual(gone['groups'][-1]['analysis_line'], '')
        self.assertEqual(gone['groups'][-1]['source']['link'], '')
        self.assertEqual(self._analysis_status(intel['id']), 'done')
        self.assertTrue(self.intake.exists(intel['id']))
        self.assertTrue(self.intake.exists(link['id']))
        intel_doc = self.intake.detail(intel['id'])['document_id']
        self.assertTrue(self.store.knowledge.document(intel_doc).get('body'))

        cleaned = self.cards.delete_deleted_source(text['id'])
        self.assertEqual(cleaned['deleted_cards'], 1)
        self.assertFalse(any(item['drop_id'] == text['id'] for item in self.cards.listing()['items']))
        self.assertEqual(self._analysis_status(intel['id']), 'done')
        self.assertEqual(self.cards.listing(kind='intel')['total'], 1)
        self.assertEqual(self.cards.listing(kind='link')['total'], 1)
        with self.assertRaisesRegex(ValueError, '还在'):
            self.cards.delete_deleted_source(intel['id'])
        with self.assertRaises(ValueError):
            self.cards.decide(self.cards.listing(kind='intel')['items'][0]['id'], 'delete')

    RELATED_READING = {'summary': '黄昏码头水面倒影与背影的拍法', 'why': '可借的取景方式',
                       'points': [], 'angles': []}

    def _note(self, index):
        return self.store.knowledge.add_note({
            'title': '码头摄影笔记 %d' % index,
            'body': '黄昏码头水面倒影与背影的拍法，第 %d 条。' % index})

    def test_related_entries_excludes_trashed(self):
        for index in range(3):
            self._note(index)
        record = self.drop()
        document = self.store.knowledge.document(record['document_id'])
        before = self.cards.related_entries(record, document, self.RELATED_READING)
        self.assertTrue(before)
        hit = before[0]['document_id']
        note = self.store.knowledge.document(hit)
        self.store.knowledge.trash_many([{'id': hit, 'expected_hash': note['hash']}], confirm=True)
        # 新检索按当前口径过滤：已删资料不再进入相关条目，其余未删的照常命中。
        after = self.cards.related_entries(record, document, self.RELATED_READING)
        self.assertNotIn(hit, [entry['document_id'] for entry in after])
        self.assertTrue(after)

    def test_cached_related_retains_historical_reference(self):
        note = self._note(0)
        record = self.drop()
        cached = [{'document_id': note['id'], 'title': note['title'], 'kind': note['kind'],
                   'url': '/knowledge.html?document=' + note['id'], 'excerpt': note['body'][:80]}]
        self._write_analysis(record, related=cached)
        self.store.knowledge.trash_many([{'id': note['id'], 'expected_hash': note['hash']}], confirm=True)
        # 旧分析里的 related 是历史快照：被引用资料移到「已删除」后缓存原样保留，不偷偷改写。
        states = self.cards.analysis_states([self.intake.detail(record['id'])])
        self.assertEqual(states[record['id']]['related'], cached)
        # 新检索按当前口径过滤：已删资料不再进入新的相关条目。
        document = self.store.knowledge.document(record['document_id'])
        fresh = self.cards.related_entries(record, document, self.RELATED_READING)
        self.assertNotIn(note['id'], [entry['document_id'] for entry in fresh])


if __name__ == '__main__':
    unittest.main()
