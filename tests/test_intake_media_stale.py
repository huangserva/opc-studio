"""媒体等待与补跑调度：无网络、无真实模型，以事件控制识别/分析边界。"""
from isolation_patches import target_start
import copy
import json
import sqlite3
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import intake_cards
from intake_cards import IntakeCards


class MediaStaleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.drop = {'id': 'a' * 32, 'status': 'resolved', 'document_id': 'document',
                     'recognition': {'status': 'running', 'text': ''}}
        self.cards = IntakeCards(type('Intake', (), {'detail': lambda _, key: copy.deepcopy(self.drop)})(),
                                 None, self.tmp.name)
        self.row = None
        self.calls = []
        self.started = threading.Event()
        self.release = threading.Event()
        self.block = False
        self.cards._analysis_row = lambda key: self.row
        self.cards._public_analysis = lambda row: copy.deepcopy(row)
        self.cards._write_analysis = self.write
        self.cards.analyze_drop = self.analyze

    def write(self, drop, status, **fields):
        self.row = dict(self.row or {}, status=status, **fields)
        return copy.deepcopy(self.row)

    def analyze(self, key, force=False):
        drop = copy.deepcopy(self.drop)
        self.calls.append(drop)
        self.write(drop, 'running', fingerprint=self.cards.fingerprint(drop))
        self.started.set()
        if self.block and len(self.calls) == 1:
            self.release.wait(3)
        return self.write(drop, 'done', fingerprint=self.cards.fingerprint(drop),
                          summary=drop['recognition'].get('text') or '理由+链接+文件名')

    def settle(self):
        limit = time.monotonic() + 3
        while self.cards.analysis_workers and time.monotonic() < limit:
            for worker in list(self.cards.analysis_workers.values()):
                worker.join(.1)
        self.assertFalse(self.cards.analysis_workers)

    def test_success_waits_and_uses_current_description_once(self):
        state = self.cards.request_analysis(self.drop['id'])
        self.assertEqual(state['error'], '等识别完成再分析')
        self.assertFalse(self.started.wait(.1))
        self.drop['recognition'] = {'status': 'succeeded', 'text': '图片描述'}
        self.cards.request_analysis_after_recognition(self.drop['id'])
        self.settle()
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.row['summary'], '图片描述')
        self.assertEqual(self.row['fingerprint'], self.cards.fingerprint(self.drop))

    def test_failure_and_unavailable_finish_without_callback(self):
        for status in ('failed', 'unavailable'):
            with self.subTest(status=status):
                self.row = None
                self.calls.clear()
                self.drop['recognition'] = {'status': 'running'}
                self.cards.request_analysis(self.drop['id'])
                self.drop['recognition'] = {'status': status, 'error': '不可识别'}
                self.settle()
                self.assertEqual(len(self.calls), 1)
                self.assertEqual(self.row['summary'], '理由+链接+文件名')

    def test_running_old_analysis_reruns_once(self):
        self.block = True
        self.cards.request_analysis(self.drop['id'], force=True)
        self.assertTrue(self.started.wait(1))
        self.drop['recognition'] = {'status': 'succeeded', 'text': '新描述'}
        for _ in range(3):
            self.cards.request_analysis_after_recognition(self.drop['id'])
        self.release.set()
        self.settle()
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(self.row['fingerprint'], self.cards.fingerprint(self.drop))

    def test_unchanged_fingerprint_does_not_rerun(self):
        self.block = True
        self.cards.request_analysis(self.drop['id'], force=True)
        self.assertTrue(self.started.wait(1))
        self.cards.request_analysis_after_recognition(self.drop['id'])
        self.release.set()
        self.settle()
        self.assertEqual(len(self.calls), 1)

    def test_text_runs_immediately(self):
        self.drop['recognition'] = {}
        self.cards.request_analysis(self.drop['id'])
        self.assertTrue(self.started.wait(1))
        self.settle()
        self.assertEqual(len(self.calls), 1)

    def test_waiting_media_does_not_hold_text_analysis_gate(self):
        media_id = self.drop['id']
        text = dict(self.drop, id='b' * 32, recognition={})
        self.cards.intake.detail = lambda key: copy.deepcopy(self.drop if key == media_id else text)
        seen = threading.Event()
        original = self.cards.analyze_drop
        self.cards.analyze_drop = lambda key, force=False: seen.set() if key == text['id'] else original(key, force)
        self.cards.request_analysis(media_id)
        self.cards.request_analysis(text['id'])
        self.assertTrue(seen.wait(1))
        self.drop['recognition'] = {'status': 'failed'}
        self.settle()

    def test_real_analysis_receives_recognition_evidence(self):
        from tests import test_intake_instant_analysis as fixture
        case = fixture.InstantAnalysisTests()
        case.setUp()
        try:
            key = case.drop_link()
            case.intake.resolve(key)
            # 识别状态写回真的库：等待循环直读回收站库，不靠 patch detail。
            def recognize(**value):
                with case.intake.db() as db:
                    db.execute('UPDATE drops SET recognition=? WHERE id=?',
                               (json.dumps(value, ensure_ascii=False), key))
            recognize(status='running', text='')
            case.cards.request_analysis(key)
            time.sleep(.1)
            self.assertEqual(case.calls, [])
            description = '图片中一艘渡船在黄昏码头的水面投下清晰倒影。'
            recognize(status='succeeded', text=description, label='图片描述')
            case.answers['read'] = fixture.payload(dict(fixture.READING, summary=description))
            case.cards.request_analysis_after_recognition(key)
            case.settle()
            row = case.cards._analysis_row(key)
            self.assertEqual(row['fingerprint'], case.cards.fingerprint(case.intake.detail(key)))
            self.assertEqual(case.cards._public_analysis(row)['summary'], description)
            reads = [call for call in case.calls if call['identity'].endswith('-read')]
            self.assertEqual(len(reads), 1)
            self.assertIn(description, reads[0]['prompt'])
        finally:
            case.tearDown()

    def test_failed_recognition_during_old_analysis_reruns_without_callback(self):
        self.block = True
        self.cards.request_analysis(self.drop['id'], force=True)
        self.assertTrue(self.started.wait(1))
        self.drop['recognition'] = {'status': 'failed', 'error': '无法识别'}
        self.release.set()
        self.settle()
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(self.row['fingerprint'], self.cards.fingerprint(self.drop))

    def test_stuck_recognition_times_out_from_started_at_and_annotates_result(self):
        from datetime import datetime, timedelta, timezone
        from unittest.mock import patch
        from tests import test_intake_instant_analysis as fixture
        case = fixture.InstantAnalysisTests()
        case.setUp()
        try:
            key = case.drop_link()
            case.intake.resolve(key)
            started = (datetime.now(timezone.utc) - timedelta(seconds=60)).isoformat()
            with case.intake.db() as db:
                db.execute('UPDATE drops SET recognition=? WHERE id=?',
                           (json.dumps({'status': 'running', 'started_at': started, 'text': ''},
                                       ensure_ascii=False), key))
            with patch('intake_cards.MEDIA_RECOGNITION_WAIT_SECONDS', 30):
                before = time.monotonic()
                case.cards.request_analysis(key)
                case.settle()
                self.assertLess(time.monotonic() - before, 2)
            row = case.cards._analysis_row(key)
            public = case.cards._public_analysis(row)
            self.assertEqual(public['status'], 'done')
            self.assertIn('识别超时，已按理由/链接/文件名分析', public['parse_notes'])
            reads = [call for call in case.calls if call['identity'].endswith('-read')]
            self.assertEqual(len(reads), 1)
            self.assertIn('这个开头方式可以借', reads[0]['prompt'])
            self.assertIn('https://example.com/pier', reads[0]['prompt'])
            self.assertEqual(row['fingerprint'], case.cards.fingerprint(case.intake.detail(key)))
        finally:
            case.tearDown()

    def test_missing_started_at_is_bounded_too(self):
        from unittest.mock import patch
        self.drop['recognition'] = {'status': 'running'}
        reasons = []
        self.cards._note_recognition_timeout = reasons.append
        with patch('intake_cards.MEDIA_RECOGNITION_WAIT_SECONDS', .02):
            self.cards.request_analysis(self.drop['id'])
            self.settle()
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(reasons, [self.drop['id']])

    def test_delete_while_waiting_finishes_without_any_write_back(self):
        from unittest.mock import patch
        from tests import test_intake_instant_analysis as fixture
        case = fixture.InstantAnalysisTests()
        case.setUp()
        try:
            key = case.drop_link()
            case.intake.resolve(key)
            with case.intake.db() as db:
                db.execute('UPDATE drops SET recognition=? WHERE id=?',
                           (json.dumps({'status': 'running'}, ensure_ascii=False), key))
            entered = threading.Event()
            waiting = case.cards._wait_for_recognition
            case.cards._wait_for_recognition = lambda value: (entered.set(), waiting(value))[1]
            with patch.object(case.cards, '_write_analysis', wraps=case.cards._write_analysis) as writes:
                case.cards.request_analysis(key)
                self.assertTrue(entered.wait(1))
                case.intake.delete(key)
                before = time.monotonic()
                case.settle()
                self.assertLess(time.monotonic() - before, 1)
                self.assertEqual(writes.call_count, 1)  # 只有删除前的 queued 写入。
            self.assertFalse(case.cards.analysis_workers)
            self.assertIsNone(case.cards._analysis_row(key))
            self.assertFalse(case.intake.exists(key))
            self.assertEqual(case.calls, [])
            with case.cards.db() as connection:
                for table in ('analysis', 'cards', 'seen', 'drops_rejected'):
                    self.assertEqual(connection.execute(
                        'SELECT COUNT(*) FROM ' + table + ' WHERE drop_id=?', (key,)).fetchone()[0], 0)
        finally:
            case.tearDown()


class MediaForceHandoffTests(unittest.TestCase):
    setUp = MediaStaleTests.setUp
    write = MediaStaleTests.write
    analyze = MediaStaleTests.analyze
    settle = MediaStaleTests.settle
    """强制请求复用等待线程，所有模型和回收项均为临时替身。"""

    def test_force_wakes_existing_waiter_and_releases_one_slot(self):
        key = self.drop['id']
        self.block = True
        forces = []
        analyze = self.cards.analyze_drop
        self.cards.analyze_drop = lambda key, force=False: (forces.append(force), analyze(key, force))[1]
        self.cards.request_analysis(key)
        worker = self.cards.analysis_workers[key]
        try:
            with patch('intake_cards.MEDIA_RECOGNITION_POLL_SECONDS', 30):
                state = self.cards.request_analysis(key, force=True)
                self.assertEqual(state['status'], 'queued')
                self.assertEqual(state['error'], '')
                self.assertTrue(self.started.wait(.25))
            self.assertIs(self.cards.analysis_workers[key], worker)
            self.assertEqual(self.cards.analysis_wait_slots._value, 4)
            self.assertEqual(forces, [True])
            self.cards.request_analysis(key, force=True)  # 已在模型中，不能再起线程。
            self.assertEqual(forces, [True])
            self.drop['recognition'] = {'status': 'succeeded', 'text': '识别完成'}
            self.cards._recognition_finished(key)
            self.cards.request_analysis_after_recognition(key)
        finally:
            self.release.set()
            self.settle()
        self.assertEqual(len(forces), 2)  # 只按既有指纹更新规则补跑一次。
        self.assertEqual(self.cards.analysis_wait_slots._value, 4)
        self.assertFalse(self.cards.analysis_force)
        self.assertFalse(self.cards.analysis_waiting)
        self.assertFalse(self.cards.analysis_wake)

    def test_pending_force_starts_without_waiting_or_taking_a_slot(self):
        key = self.drop['id']
        for _ in range(4):
            self.assertTrue(self.cards.analysis_wait_slots.acquire(False))
        try:
            self.cards.request_analysis(key)
            self.assertIn(key, self.cards.analysis_wait_pending)
            self.assertNotIn(key, self.cards.analysis_workers)
            self.cards.request_analysis(key, force=True)
            self.assertTrue(self.started.wait(.25))
            self.settle()
            self.assertNotIn(key, self.cards.analysis_wait_pending)
            self.assertEqual(self.cards.analysis_wait_slots._value, 0)
        finally:
            for _ in range(4):
                self.cards.analysis_wait_slots.release()

    def test_force_during_model_keeps_current_worker_and_state(self):
        key = self.drop['id']
        self.block = True
        self.cards.request_analysis(key, force=True)
        try:
            self.assertTrue(self.started.wait(.25))
            worker = self.cards.analysis_workers[key]
            state = self.cards.request_analysis(key, force=True)
            self.assertEqual(state['status'], 'running')
            self.assertIs(self.cards.analysis_workers[key], worker)
            self.assertEqual(len(self.calls), 1)
            self.assertFalse(self.cards.analysis_force)
            self.assertEqual(self.cards.analysis_wait_slots._value, 4)
        finally:
            self.release.set()
            self.settle()
        self.assertEqual(len(self.calls), 1)

    def test_force_before_wait_event_registration_is_not_lost(self):
        entered, proceed = threading.Event(), threading.Event()
        wait = self.cards._wait_for_recognition
        def delayed(key):
            entered.set()
            proceed.wait(2)
            return wait(key)
        self.cards._wait_for_recognition = delayed
        key = self.drop['id']
        self.cards.request_analysis(key)
        try:
            self.assertTrue(entered.wait(.25))
            self.cards.request_analysis(key, force=True)
            proceed.set()
            self.assertTrue(self.started.wait(.25))
        finally:
            proceed.set()
            self.drop['recognition'] = {'status': 'failed'}
            self.cards._recognition_finished(key)
            self.settle()
        self.assertEqual(self.cards.analysis_wait_slots._value, 4)


class MediaGateHandoffTests(unittest.TestCase):
    def test_wait_end_clears_reason_before_gate_without_duplicate_worker(self):
        from tests import test_intake_instant_analysis as fixture
        for ending in ('succeeded', 'failed', 'timeout', 'force'):
            with self.subTest(ending=ending):
                case = fixture.InstantAnalysisTests()
                case.setUp()
                entered, release = threading.Event(), threading.Event()
                try:
                    holder = case.drop_link()
                    case.intake.resolve(holder)
                    key = case.drop_link()
                    case.intake.resolve(key)
                    analyze = case.cards.analyze_drop
                    media_calls = []
                    def blocked(drop_id, force=False):
                        if drop_id == holder:
                            entered.set()
                            release.wait(3)
                        else:
                            media_calls.append(force)
                        return analyze(drop_id, force=force)
                    case.cards.analyze_drop = blocked
                    case.cards.request_analysis(holder)
                    self.assertTrue(entered.wait(1))  # 另一条真实 worker 占住闸门。
                    recognition = {'status': 'running'}
                    if ending == 'timeout':
                        recognition['started_at'] = (datetime.now(timezone.utc) -
                                                     timedelta(seconds=intake_cards.MEDIA_RECOGNITION_WAIT_SECONDS + 60)).isoformat()
                    with case.intake.db() as db:
                        db.execute('UPDATE drops SET recognition=? WHERE id=?',
                                   (json.dumps(recognition), key))
                    # 同锁保留种子注记，并记下最初 worker，避免超时立即交接的竞态。
                    with case.cards.analysis_lock:
                        state = case.cards.request_analysis(key)
                        self.assertEqual(state['error'], '等识别完成再分析')
                        worker = case.cards.analysis_workers[key]
                        case.cards._write_analysis(case.intake.detail(key), 'queued',
                                                  error=state['error'], data={'parse_notes': ['既有注记']})
                    if ending in ('succeeded', 'failed'):
                        with case.intake.db() as db:
                            db.execute('UPDATE drops SET recognition=? WHERE id=?',
                                       (json.dumps({'status': ending, 'text': '新描述'}), key))
                        case.cards.request_analysis_after_recognition(key)
                    elif ending == 'force':
                        case.cards.request_analysis(key, force=True)
                    deadline = time.monotonic() + 1
                    while time.monotonic() < deadline:
                        with case.cards.analysis_lock:
                            waiting = key in case.cards.analysis_waiting
                        if not waiting:
                            break
                        time.sleep(.01)
                    self.assertFalse(waiting)
                    row = case.cards._analysis_row(key)
                    self.assertEqual(row['status'], 'queued')
                    self.assertEqual(row['error'], '')
                    self.assertEqual(json.loads(row['data'])['parse_notes'], ['既有注记'])
                    state = case.cards.request_analysis(key, force=True)
                    self.assertEqual(state['error'], '')
                    self.assertEqual(state['status'], 'queued')
                    self.assertIs(case.cards.analysis_workers[key], worker)
                    self.assertEqual(media_calls, [])
                    release.set()
                    case.settle()
                    self.assertEqual(media_calls, [ending == 'force'])
                    final = case.cards._public_analysis(case.cards._analysis_row(key))
                    self.assertEqual(final['status'], 'done')
                    if ending == 'timeout':
                        self.assertIn('识别超时，已按理由/链接/文件名分析', final['parse_notes'])
                finally:
                    release.set()
                    case.tearDown()


class MediaStaleRoundThreeTests(unittest.TestCase):
    """fixes-media-stale-3：识别收尾唤醒、等待槽位上限、补跑上限、线程起不来、状态读取保护。

    仍然全部假模型、假抓取：不联网、不调真实模型、不读密钥。
    """

    def case(self):
        from tests import test_intake_instant_analysis as fixture
        case = fixture.InstantAnalysisTests()
        case.setUp()
        self.addCleanup(case.tearDown)
        return case

    def installed(self, case):
        key = case.drop_link()
        case.intake.resolve(key)        # 走回收站入库路径，拿到 document_id（不自动排队分析）
        return key

    def recognize(self, case, drop_id, **value):
        with case.intake.db() as db:
            db.execute('UPDATE drops SET recognition=? WHERE id=?',
                       (json.dumps(value, ensure_ascii=False), drop_id))

    def fake(self, drop, workspace, public=True):
        cards = IntakeCards(type('Intake', (), {'detail': lambda _, key: copy.deepcopy(drop)})(),
                            None, workspace)
        if public:
            cards._public_analysis = lambda row: copy.deepcopy(row) if row else None
        return cards

    def settle(self, cards, timeout=4):
        deadline = time.monotonic() + timeout
        while cards.analysis_workers and time.monotonic() < deadline:
            for worker in list(cards.analysis_workers.values()):
                worker.join(.05)
        self.assertFalse(cards.analysis_workers)

    # ── 中-2 / 低-4：注记写入不得抹掉失败原因、可重试标记和分析时间 ──────────────
    def test_the_timeout_note_keeps_the_failure_reason_and_the_analysis_time(self):
        case = self.case()
        key = self.installed(case)
        case.cards._write_analysis(case.intake.detail(key), 'failed', fingerprint='fp',
                                   error='未配置提炼模型：请在定时任务页为「创意提炼（Pi）」选一个模型后重新分析',
                                   retryable=True, finished_at='2026-10-09T01:02:03+00:00')
        case.cards._note_recognition_timeout(key)
        row = case.cards._analysis_row(key)
        self.assertEqual(row['status'], 'failed')
        self.assertIn('未配置提炼模型', row['error'])
        self.assertEqual(row['retryable'], 1)
        self.assertEqual(row['finished_at'], '2026-10-09T01:02:03+00:00')
        self.assertIn('识别超时，已按理由/链接/文件名分析', json.loads(row['data'])['parse_notes'])
        case.cards._note_recognition_timeout(key)
        notes = json.loads(case.cards._analysis_row(key)['data'])['parse_notes']
        self.assertEqual(notes.count('识别超时，已按理由/链接/文件名分析'), 1)

    # ── 中-1：补跑进行中的识别完成不得丢弃；最多首跑 + 两次补跑，再落后就写注记 ──
    def test_at_most_two_reruns_and_a_visible_note_after_that(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        drop = {'id': 'c' * 32, 'status': 'resolved', 'document_id': 'document', 'recognition': {'status': 'failed'}}
        cards = self.fake(drop, tmp.name)
        row, texts, notes = {}, [], []
        cards._analysis_row = lambda key: dict(row) if row else None
        cards._write_analysis = lambda d, status, **f: (row.update(status=status, **f), dict(row))[1]
        cards._note_analysis = lambda key, reason: notes.append(reason)

        def analyze(key, force=False):
            texts.append(drop['recognition'].get('text', ''))
            row.update(status='done', fingerprint=cards.fingerprint(drop))
            # 每次分析进行中都再来一次识别完成：最新那份永远追不上。
            drop['recognition'] = {'status': 'succeeded', 'text': '第%d次描述' % (len(texts) + 1)}
            cards.request_analysis_after_recognition(key)

        cards.analyze_drop = analyze
        cards.request_analysis(drop['id'], force=True)
        self.settle(cards)
        self.assertEqual(texts, ['', '第2次描述', '第3次描述'])          # 首跑 + 最多两次补跑
        self.assertEqual(notes, ['识别结果又更新过，可点重新分析'])
        self.assertFalse(cards.analysis_rerun)

    def test_a_recognition_landing_during_the_rerun_still_gets_analysed(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        drop = {'id': 'c' * 32, 'status': 'resolved', 'document_id': 'document', 'recognition': {'status': 'failed'}}
        cards = self.fake(drop, tmp.name)
        row, texts = {}, []
        cards._analysis_row = lambda key: dict(row) if row else None
        cards._write_analysis = lambda d, status, **f: (row.update(status=status, **f), dict(row))[1]
        cards._note_analysis = lambda key, reason: self.fail('一次补跑就追上了，不该写落后注记')

        def analyze(key, force=False):
            texts.append(drop['recognition'].get('text', ''))
            row.update(status='done', fingerprint=cards.fingerprint(drop))
            if len(texts) == 1:      # 只在第一次分析进行中补一次识别完成
                drop['recognition'] = {'status': 'succeeded', 'text': '新描述'}
                cards.request_analysis_after_recognition(key)

        cards.analyze_drop = analyze
        cards.request_analysis(drop['id'], force=True)
        self.settle(cards)
        self.assertEqual(texts, ['', '新描述'])
        self.assertEqual(row['fingerprint'], cards.fingerprint(drop))   # 最新识别结果进了分析

    # ── 中-3：线程起不来要回收登记并如实写失败，不能永远停在 queued ──────────────
    def test_a_thread_that_cannot_start_says_so_instead_of_queuing_forever(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        drop = {'id': 'd' * 32, 'status': 'resolved', 'document_id': 'document', 'recognition': {}}
        cards = self.fake(drop, tmp.name)
        row, calls = {}, []
        cards._analysis_row = lambda key: dict(row) if row else None
        cards._write_analysis = lambda d, status, **f: (row.update(status=status, **f), dict(row))[1]

        def analyze(key, force=False):
            calls.append(force)
            row.update(status='done', fingerprint=cards.fingerprint(drop))

        cards.analyze_drop = analyze
        with target_start(lambda worker: getattr(worker, "_target", None) == cards._analysis_worker, RuntimeError("can't start new thread")):
            state = cards.request_analysis(drop['id'])
        self.assertEqual(state['status'], 'failed')
        self.assertEqual(state['error'], '分析线程起不来，请稍后重新分析')
        self.assertTrue(state['retryable'])
        self.assertFalse(cards.analysis_workers)                        # 登记已回收
        cards.request_analysis(drop['id'])                              # 再点一次能真的排队
        self.settle(cards)
        self.assertEqual(calls, [False])

    # ── 低-1：行被并发删掉要说「回收项已删除」，不能把 None 丢给 API ─────────────
    def test_a_row_deleted_under_the_request_says_the_drop_is_gone(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        drop = {'id': 'e' * 32, 'status': 'resolved', 'document_id': 'document', 'recognition': {}}
        cards = self.fake(drop, tmp.name, public=False)      # 用真的 _public_analysis，不抢它的保护
        cards.analyze_drop = lambda key, force=False: None
        cards._analysis_row = lambda key: None                          # 回读时行已不在
        cards._write_analysis = lambda d, status, **f: None             # 父行守卫拒绝写回
        self.assertIsNone(cards._public_analysis(None))
        with self.assertRaises(ValueError) as raised:
            cards.request_analysis(drop['id'])
        self.assertIn('回收项已删除', str(raised.exception))
        cards.analysis_workers[drop['id']] = threading.current_thread()
        with self.assertRaises(ValueError) as raised:
            cards.request_analysis(drop['id'])
        self.assertIn('回收项已删除', str(raised.exception))
        cards.analysis_workers.clear()

    # ── 低-5：兜底上限从首次观察的单调时刻起算，started_at 不可信也不能拉长等待 ──
    def test_a_future_started_at_cannot_extend_the_wait_beyond_the_ceiling(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        future = (datetime.now(timezone.utc) + timedelta(seconds=30)).isoformat()
        drop = {'id': 'f' * 32, 'status': 'resolved', 'document_id': 'document',
                'recognition': {'status': 'running', 'started_at': future}}
        cards = self.fake(drop, tmp.name)
        with patch('intake_cards.MEDIA_RECOGNITION_WAIT_SECONDS', .3), \
             patch('intake_cards.MEDIA_RECOGNITION_POLL_SECONDS', .05):
            before = time.monotonic()
            timed_out = cards._wait_for_recognition(drop['id'])
            elapsed = time.monotonic() - before
        self.assertTrue(timed_out)
        self.assertLess(elapsed, 2)      # 不兜底就会按未来时间等 30 秒以上

    # ── 中-4：识别收尾（含失败）叫醒分析侧，不靠轮询发现 ────────────────────────
    def test_recognition_wrap_up_wakes_the_analysis_side(self):
        case = self.case()
        key = self.installed(case)

        def boom(*args, **kwargs):
            raise ValueError('识别失败')

        self.assertTrue(any(getattr(w, '__name__', '') == '_recognition_finished'
                            for w in case.intake.recognition_wakers))   # 卡库自己接上的回调
        woken = []
        case.intake.register_recognition_waker(woken.append)
        case.intake.media_recognizer = type('No', (), {'recognize_image': boom, 'recognize_video': boom})()
        case.intake._start_recognition(key)
        for worker in list(case.intake.recognition_workers.values()):
            worker.join(5)
        self.assertEqual(woken, [key])
        self.assertEqual(case.intake.detail(key)['recognition']['status'], 'failed')

    def test_a_finished_recognition_wakes_the_waiter_before_the_poll(self):
        from tests import test_intake_instant_analysis as fixture
        case = self.case()
        key = self.installed(case)
        self.recognize(case, key, status='running', text='',
                       started_at=datetime.now(timezone.utc).isoformat())
        entered = threading.Event()
        waiting = case.cards._wait_for_recognition
        case.cards._wait_for_recognition = lambda value: (entered.set(), waiting(value))[1]
        with patch('intake_cards.MEDIA_RECOGNITION_POLL_SECONDS', 30):   # 兜底轮询放到 30 秒
            case.cards.request_analysis(key)
            self.assertTrue(entered.wait(2))
            self.assertEqual(case.calls, [])                             # 还在等识别
            self.recognize(case, key, status='succeeded', text='渡船倒影')
            case.answers['read'] = fixture.payload(dict(fixture.READING, summary='渡船倒影'))
            before = time.monotonic()
            case.cards._recognition_finished(key)                        # 识别收尾唤醒
            case.settle()
            self.assertLess(time.monotonic() - before, 5)
        reads = [call for call in case.calls if call['identity'].endswith('-read')]
        self.assertEqual(len(reads), 1)
        self.assertIn('渡船倒影', reads[0]['prompt'])

    # ── 中-4：等识别的线程数有上限，超出的排队，识别收尾时拉起 ──────────────────
    def test_only_a_handful_of_drops_wait_for_recognition_at_once(self):
        case = self.case()
        keys = []
        for index in range(6):
            key = case.drop_link(url='https://example.com/pier%d' % index)
            case.intake.resolve(key)
            keys.append(key)
        started = datetime.now(timezone.utc).isoformat()
        for key in keys:
            self.recognize(case, key, status='running', started_at=started)
        for key in keys:
            state = case.cards.request_analysis(key)
            self.assertEqual(state['status'], 'queued')
            self.assertEqual(state['error'], '等识别完成再分析')
        self.assertEqual(len(case.cards.analysis_workers), 4)            # 最多四条在等
        self.assertEqual(len(case.cards.analysis_wait_pending), 2)       # 其余排队，不挂线程
        self.assertEqual(case.calls, [])
        for key in keys:                                                 # 识别全部收尾
            self.recognize(case, key, status='succeeded', text='渡船倒影')
            case.cards._recognition_finished(key)
        case.settle(timeout=30)
        self.assertFalse(case.cards.analysis_wait_pending)
        self.assertEqual(len([call for call in case.calls if call['identity'].endswith('-read')]), 6)

    # ── 中-4：等待期间只开一条只读连接，轮询不再每次新开连接 ────────────────────
    def test_the_wait_loop_reuses_one_database_connection(self):
        case = self.case()
        key = self.installed(case)
        self.recognize(case, key, status='running', started_at=datetime.now(timezone.utc).isoformat())
        opened = []
        polls = []
        real = sqlite3.connect

        def counting(*args, **kwargs):
            database = str(args[0] if args else kwargs.get('database', ''))
            if database == str(case.intake.path):
                opened.append(database)
            return real(*args, **kwargs)

        snapshot = case.cards._waited_recognition
        case.cards._waited_recognition = lambda connection, drop_id: (polls.append(1),
                                                                     snapshot(connection, drop_id))[1]
        with patch.object(intake_cards.sqlite3, 'connect', counting), \
             patch('intake_cards.MEDIA_RECOGNITION_WAIT_SECONDS', .4), \
             patch('intake_cards.MEDIA_RECOGNITION_POLL_SECONDS', .05):
            with sqlite3.connect(':memory:') as unrelated:
                self.assertEqual(unrelated.execute('SELECT 1').fetchone()[0], 1)
            self.assertEqual(opened, [])
            self.assertTrue(case.cards._wait_for_recognition(key))       # 约 8 次兜底轮询
        self.assertGreaterEqual(len(polls), 5)
        # 旧写法每次轮询都经 Intake.db() 新开一条连接（≥9 条）；现在整段只复用一条。
        self.assertLessEqual(len([path for path in opened if path.endswith('intake.db')]), 3)

    # ── 排队中的回收项被删掉：不拉起、不写回、不留孤儿 ──────────────────────────
    def test_a_pending_drop_that_gets_deleted_is_dropped_from_the_queue(self):
        case = self.case()
        keys = []
        for index in range(5):
            key = case.drop_link(url='https://example.com/pier%d' % index)
            case.intake.resolve(key)
            keys.append(key)
        started = datetime.now(timezone.utc).isoformat()
        for key in keys:
            self.recognize(case, key, status='running', started_at=started)
        for key in keys:
            case.cards.request_analysis(key)
        self.assertEqual(len(case.cards.analysis_wait_pending), 1)
        case.intake.delete(keys[4])
        case.cards._promote_pending()
        self.assertNotIn(keys[4], case.cards.analysis_wait_pending)
        for key in keys[:4]:
            self.recognize(case, key, status='succeeded', text='渡船倒影')
            case.cards._recognition_finished(key)
        case.settle(timeout=30)
        self.assertFalse(case.cards.analysis_wait_pending)
        self.assertIsNone(case.cards._analysis_row(keys[4]))
