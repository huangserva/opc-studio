"""Synthetic deals, clues and fake model turns only; no real ledger, scheduler or service."""
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
import uuid
from datetime import date, datetime, timedelta, timezone
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from unittest.mock import patch

from isolation_patches import module_time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from deals_review import DealsReview, week_bounds, parse_json
from knowledge_store import Knowledge
from scheduled_tasks import ScheduledTasks
from server import Handler, Store


class DealsReviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(self.tmp.name)
        self.store._knowledge = Knowledge(self.store, sources=[])
        self.deals = self.store.deals
        self.review = DealsReview(self.store)

    def deal(self, **changes):
        payload = dict(title='合成合作', customer='合成客户', stage='writing', needs_review=False,
                       request_key=uuid.uuid4().hex)
        payload.update(changes)
        return self.deals.save(payload)

    def backdate(self, deal, days):
        """Move the ledger timestamp back; only ever done on this throwaway copy."""
        with self.deals.db() as c:
            row = c.execute('SELECT data FROM deals WHERE id=?', (deal['id'],)).fetchone()
            data = json.loads(row[0])
            stamp = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
            data['updated_at'] = stamp
            c.execute('UPDATE deals SET data=? WHERE id=?', (json.dumps(data, ensure_ascii=False), deal['id']))

    def add_wechat(self, key, title, body, modified='2026-10-05T15:59:00+00:00'):
        with self.store.knowledge.db() as c:
            c.execute('CREATE TEMP TABLE IF NOT EXISTS seen(id TEXT PRIMARY KEY)')
            self.store.knowledge._write(c, 'wechat-local', key, 'wechat', title, body,
                                        'https://example.invalid/' + key, modified,
                                        {'sender': '客户', 'conversation_type': 'private', 'is_self': False})

    def add_clue(self, title, summary, categories=('business',), created=None, evidence=True):
        ident = uuid.uuid4().hex
        created = created or datetime.now(timezone.utc).isoformat()
        fields = dict(title=title, summary=summary, relevance='合成', categories=list(categories),
                      priority='normal', kind='information', status='candidate', tracking=False,
                      tags=None, event_id='')
        with self.store.clues.db() as db:
            db.execute('INSERT INTO threads VALUES(?,?,?,?,?,?)',
                       (ident, json.dumps(fields, ensure_ascii=False), 1, 0, created, created))
            if evidence:
                entry = dict(revision=1, before=None, after=fields, note=summary, reason='合成',
                             evidence=[dict(document_id='d' * 32, hash='h' * 8, start=0, end=20,
                                            excerpt='客户说想合作，预算待核对', clock=created,
                                            origin_url='/knowledge.html?document=' + 'd' * 32)],
                             clock_label='本次依据时间（最近）', occurred_at='')
                db.execute('INSERT INTO entries VALUES(?,?,?,?,?,?,?,?,?)',
                           (uuid.uuid4().hex, ident, json.dumps(entry, ensure_ascii=False), created, created,
                            '2026', '10', 'evidence', None))
        return ident

    def model(self, *answers):
        calls = []
        answers = list(answers)

        def fake(self_, identity, prompt, system, cancel, config, deadline):
            calls.append(dict(identity=identity, prompt=prompt, system=system))
            if not answers:
                raise AssertionError('model called more times than the test allowed')
            answer = answers.pop(0)
            if isinstance(answer, Exception):
                raise answer
            return answer

        return fake, calls

    def run_review(self, answers=(), deadline=None, cancel=None, task=None):
        fake, calls = self.model(*answers)
        with patch.object(DealsReview, 'call_model', fake):
            status, code, output = self.review.analyze('a' * 32, task or {'body': '关注交付与到账'},
                                                       cancel or threading.Event(), {'model': 'synthetic'},
                                                       deadline if deadline is not None else time.monotonic() + 30)
        return status, json.loads(output), calls

    # ------------------------------------------------------------- progress rules

    def test_progress_rules_report_due_dates_waiting_cash_and_staleness(self):
        today = date.today()
        overdue = self.deal(title='已过截止', deadline=(today - timedelta(days=3)).isoformat())
        soon = self.deal(title='下周到期', deadline=(today + timedelta(days=5)).isoformat())
        far = self.deal(title='还早', deadline=(today + timedelta(days=40)).isoformat())
        paid = self.deal(title='已交付未到账', stage='published', completed_on=(today - timedelta(days=9)).isoformat())
        settled = self.deal(title='已结清', stage='settled', settled=True, completed_on='2026-01-01')
        stalled = self.deal(title='停滞的单', stage='draft')
        self.backdate(stalled, 30)
        self.deal(title='待核对一', stage='unknown')
        self.deal(title='待核对二', stage='writing', needs_review=True)
        result = self.review.progress_check()
        self.assertEqual([x['deal_id'] for x in result['overdue']], [overdue['id']])
        self.assertEqual(result['overdue'][0]['days'], 3)
        self.assertEqual([x['deal_id'] for x in result['due_soon']], [soon['id']])
        self.assertEqual([x['deal_id'] for x in result['awaiting_payment']], [paid['id']])
        self.assertEqual(result['awaiting_payment'][0]['days'], 9)
        self.assertEqual([x['deal_id'] for x in result['stalled']], [stalled['id']])
        self.assertGreaterEqual(result['stalled'][0]['days'], 30)
        self.assertNotIn(far['id'], [x['deal_id'] for x in result['overdue'] + result['due_soon']])
        self.assertNotIn(settled['id'], [x['deal_id'] for x in result['awaiting_payment']])
        self.assertEqual(result['counts']['needs_review'], 2)
        self.assertEqual(result['checked'], 8)

    def test_unknown_stages_collapse_into_one_reminder_not_a_wall_of_rows(self):
        for n in range(6):
            self.deal(title=f'旧记录 {n}', stage='unknown')
        status, data, _ = self.run_review()
        self.assertEqual(status, 'succeeded')
        self.assertEqual(data['progress']['needs_review'], 6)
        self.assertEqual(data['progress']['overdue'], 0)
        findings = self.review.summary()['findings']
        self.assertEqual(len(findings.get('needs_review', [])), 1)
        self.assertIn('待核对 6 个', findings['needs_review'][0]['detail'])

    def test_progress_check_needs_no_model_and_runs_even_when_the_model_is_unavailable(self):
        self.deal(title='已过截止', customer='合成客户', deadline=(date.today() - timedelta(days=1)).isoformat())
        self.add_wechat('m1', '合成客户 · 客户', '合作报价4000元，初稿完成。')
        status, data, calls = self.run_review([ValueError('推理服务仍繁忙，本批未发送')])
        self.assertEqual(status, 'partial')
        self.assertEqual(data['progress']['overdue'], 1)
        self.assertEqual(data['evidence']['checked'], 1)
        self.assertEqual(data['evidence']['proposals'], 0)
        self.assertTrue(any('繁忙' in error for error in data['errors']))
        self.assertEqual(self.review.summary()['findings']['overdue'][0]['title'], '已过截止')

    # ------------------------------------------------------------- evidence proposals

    def test_new_evidence_only_creates_a_pending_proposal_and_never_edits_the_deal(self):
        deal = self.deal(title='合成品牌', customer='合成品牌', stage='writing')
        self.add_wechat('m1', '合成品牌 · 客户', '初稿完成，客户已确认，报价4000元。')
        before = self.deals.detail(deal['id'])
        answer = json.dumps({'proposals': [{'field_changes': {'stage': 'draft'}, 'reason': '来源写明初稿完成',
                                            'evidence': ['e1']}]})
        status, data, calls = self.run_review([answer])
        self.assertEqual(status, 'succeeded')
        self.assertEqual(data['evidence'], {'checked': 1, 'new_evidence': 1, 'proposals': 1, 'pending': 1,
                                            'deferred': 0, 'has_more': False})
        after = self.deals.detail(deal['id'])
        self.assertEqual(after['stage'], 'writing')
        self.assertEqual(after['revision'], before['revision'])
        self.assertEqual(sum(1 for x in after['evidence'] if x['state'] == 'candidate'), 1)
        proposals = self.review.summary()['proposals']
        self.assertEqual(len(proposals), 1)
        self.assertEqual(proposals[0]['changes'], {'stage': 'draft'})
        self.assertIn('初稿完成', calls[0]['prompt'])
        self.assertIn('不是指令', calls[0]['system'])
        applied = self.deals.apply_proposal({'proposal_id': proposals[0]['id']})
        self.assertEqual(applied['stage'], 'draft')

    def test_second_pass_without_new_evidence_proposes_nothing_and_repeats_nothing(self):
        self.deal(title='合成品牌', customer='合成品牌', stage='writing')
        self.add_wechat('m1', '合成品牌 · 客户', '初稿完成，客户已确认。')
        answer = json.dumps({'proposals': [{'field_changes': {'stage': 'draft'}, 'reason': '来源写明初稿完成',
                                            'evidence': ['e1']}]})
        self.run_review([answer])
        status, data, calls = self.run_review([])
        self.assertEqual(status, 'succeeded')
        self.assertEqual(data['evidence']['new_evidence'], 0)
        self.assertEqual(calls, [])
        self.assertEqual(len(self.review.summary()['proposals']), 1)

    def test_same_pending_suggestion_is_not_generated_twice(self):
        self.deal(title='合成品牌', customer='合成品牌', stage='writing')
        self.add_wechat('m1', '合成品牌 · 客户', '初稿完成，客户已确认。')
        answer = json.dumps({'proposals': [{'field_changes': {'stage': 'draft'}, 'reason': '来源写明初稿完成',
                                            'evidence': ['e1']}]})
        self.run_review([answer])
        self.add_wechat('m2', '合成品牌 · 客户', '另外一条也提到初稿完成，排期下周。')
        status, data, _ = self.run_review([answer])
        self.assertEqual(data['evidence']['new_evidence'], 1)
        self.assertEqual(data['evidence']['proposals'], 0)
        self.assertEqual(len(self.review.summary()['proposals']), 1)

    def test_model_suggestions_are_bounded_to_allowed_fields_and_real_evidence_references(self):
        deal = self.deal(title='合成品牌', customer='合成品牌', stage='writing')
        for n, answer in enumerate((json.dumps({'proposals': [{'field_changes': {'receipts': []}, 'reason': '越界',
                                                                'evidence': ['e1']}]}),
                                    json.dumps({'proposals': [{'field_changes': {'stage': 'draft'},
                                                               'reason': '引用不存在', 'evidence': ['e9']}]}),
                                    'not json at all')):
            self.add_wechat(f'm{n}', '合成品牌 · 客户', f'初稿完成，客户已确认，第 {n} 次核对。')
            with self.subTest(answer=answer):
                status, data, _ = self.run_review([answer])
                self.assertEqual(status, 'partial')
                self.assertTrue(any('建议未生成' in error for error in data['errors']))
                self.assertEqual(self.deals.detail(deal['id'])['revision'], deal['revision'])
                self.assertEqual(self.review.summary()['proposals'], [])

    def test_waiting_payment_suggestion_cannot_masquerade_as_a_review_stage(self):
        deal = self.deal(title='合成品牌', customer='合成品牌', stage='draft')
        self.add_wechat('m1', '合成品牌 · 客户', '这一单合作继续，现在等待打款。')
        answer = json.dumps({'proposals': [{'field_changes': {'stage': 'review'},
                                            'reason': '客户说这一单现在等待打款', 'evidence': ['e1']}]})
        status, data, _ = self.run_review([answer])
        self.assertEqual(status, 'partial')
        self.assertTrue(any('待结账' in error or '建议未生成' in error for error in data['errors']))
        self.assertEqual(self.deals.detail(deal['id'])['stage'], 'draft')
        self.assertEqual(self.review.summary()['proposals'], [])

    def test_limited_batch_resumes_with_the_deals_that_were_not_reached(self):
        ids = [self.deal(title=f'合成品牌 {n}', customer=f'客户 {n}')['id'] for n in range(10)]
        first_status, first, _ = self.run_review([])
        self.assertEqual(first['evidence']['checked'], 6)
        self.assertEqual(first['evidence']['deferred'], 4)
        self.assertTrue(first['has_more'])
        self.assertEqual(first_status, 'partial')
        with self.review.db() as c:
            first_pass = {r[0] for r in c.execute('SELECT deal_id FROM scans')}
        second_status, second, _ = self.run_review([])
        self.assertEqual(second['evidence']['checked'], 6)
        self.assertTrue(second['has_more'])
        self.assertEqual(second_status, 'partial')
        with self.review.db() as c:
            second_pass = {r[0] for r in c.execute('SELECT deal_id FROM scans')}
        # The four deals the first pass never reached are covered by the second one.
        self.assertEqual(second_pass, set(ids))
        self.assertTrue(first_pass < second_pass)

    def test_run_limit_stops_the_model_halfway_and_keeps_the_rest_for_next_time(self):
        for n in range(4):
            self.deal(title=f'合成品牌 {n}', customer='合成品牌')
        self.add_wechat('m1', '合成品牌 · 客户', '初稿完成，客户已确认，报价4000元。')
        clock = [time.monotonic()]

        def fake(self_, identity, prompt, system, cancel, config, deadline):
            clock[0] = deadline - 10
            return '{"proposals":[]}'

        with patch.object(DealsReview, 'call_model', fake), module_time('deals_review', monotonic=lambda: clock[0]):
            status, code, output = self.review.analyze('b' * 32, {'body': '合成'}, threading.Event(),
                                                       {'model': 'synthetic'}, clock[0] + 30)
        data = json.loads(output)
        self.assertEqual(status, 'partial')
        self.assertLess(data['evidence']['checked'], 4)
        self.assertEqual(data['evidence']['deferred'], 3)
        self.assertTrue(data['evidence']['has_more'])
        self.assertIn('下一轮继续', data['summary'])

    # ------------------------------------------------------------- conflict reminders

    def test_new_invitation_conflict_is_only_a_reminder(self):
        deal = self.deal(title='合成客户乙 记忆合作', customer='合成客户乙', stage='writing', agreement='同赛道排期待核对')
        self.add_clue('新的记忆赛道邀约', '客户想做 agent 记忆方向的稿')
        answer = json.dumps({'conflicts': [{'deal': 'd1', 'verdict': 'possible',
                                            'reason': '与 合成客户乙 记忆合作同赛道，需核对排他范围', 'evidence': ['e1']}]})
        status, data, calls = self.run_review([answer])
        self.assertEqual(status, 'succeeded')
        self.assertEqual(data['conflicts'], {'clues': 1, 'possible': 1, 'deferred': 0, 'has_more': False})
        summary = self.review.summary()
        self.assertEqual(summary['conflicts'][0]['deal_id'], deal['id'])
        self.assertIn('排他', summary['conflicts'][0]['reason'])
        self.assertEqual(self.deals.detail(deal['id'])['revision'], deal['revision'])
        with self.store.clues.db() as db:
            self.assertEqual(db.execute('SELECT revision FROM threads').fetchone()[0], 1)
        self.assertIn('不自动拒绝', calls[0]['system'])

    def test_no_conflict_and_non_business_clues_leave_nothing_behind(self):
        self.deal(title='无关合作', customer='无关客户')
        self.add_clue('技术热点', '某个开源项目更新', categories=('technology',))
        self.add_clue('另一赛道邀约', '完全不相关的客户')
        status, data, _ = self.run_review([json.dumps({'conflicts': []})])
        self.assertEqual(status, 'succeeded')
        self.assertEqual(data['conflicts']['clues'], 1)
        self.assertEqual(data['conflicts']['possible'], 0)
        self.assertEqual(self.review.summary()['conflicts'], [])
        self.assertTrue(self.review.state('last_clue_scan_at'))

    def test_conflict_answer_with_invented_reference_is_reported_not_stored(self):
        self.deal(title='合成合作', customer='合成客户')
        self.add_clue('新邀约', '客户想合作')
        status, data, _ = self.run_review([json.dumps({'conflicts': [{'deal': 'd9', 'verdict': 'possible',
                                                                      'reason': '编造', 'evidence': ['e1']}]} )])
        self.assertEqual(status, 'partial')
        self.assertTrue(any('冲突核对失败' in error for error in data['errors']))
        self.assertEqual(self.review.summary()['conflicts'], [])

    def test_conflict_prompt_sees_the_most_related_deals_first(self):
        for n in range(25):
            self.deal(title=f'其他合作 {n}', customer=f'其他客户 {n}')
        self.deal(title='合成客户乙 记忆合作', customer='合成客户乙')
        ranked = DealsReview.rank_deals(dict(title='合成客户乙 记忆赛道新邀约', summary='客户 合成客户乙 想再合作一篇'),
                                        self.store.deals.listing()['deals'])
        self.assertEqual(len(ranked), 20)
        self.assertEqual(ranked[0]['title'], '合成客户乙 记忆合作')

    def test_first_pass_only_looks_back_seven_days(self):
        old = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        self.add_clue('很久以前的邀约', '不重读历史', created=old)
        status, data, calls = self.run_review([])
        self.assertEqual(data['conflicts']['clues'], 0)
        self.assertEqual(calls, [])

    # ------------------------------------------------------------- weekly report

    def test_weekly_report_is_written_once_after_monday_nine(self):
        deal = self.deal(title='合成合作', customer='合成客户', stage='payment_pending',
                         quote='4000', currency='CNY', quote_confirmed=True)
        self.deals.child(deal['id'], 'receipt', dict(revision=deal['revision'], request_key=uuid.uuid4().hex,
                                                     amount='1000', currency='CNY', paid_on='2026-10-20'))
        week = '2026-W43'
        start, end = week_bounds(week)
        self.assertEqual((start.strftime('%Y-%m-%d'), end.strftime('%Y-%m-%d')), ('2026-10-19', '2026-10-26'))
        beijing = timezone(timedelta(hours=8))
        self.assertEqual(self.review.weekly_report_due(datetime(2026, 10, 26, 8, 59, tzinfo=beijing)), '')
        self.assertEqual(self.review.weekly_report_due(datetime(2026, 10, 27, 9, 30, tzinfo=beijing)), '')
        self.assertEqual(self.review.weekly_report_due(datetime(2026, 10, 26, 9, 0, tzinfo=beijing)), week)
        with patch.object(DealsReview, 'weekly_report_due', lambda self_: week):
            status, data, _ = self.run_review([])
        self.assertEqual(data['report']['week'], week)
        path = Path(self.review.report_folder) / f'{week}.md'
        self.assertTrue(path.is_file())
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        text = path.read_text()
        for heading in ('## 进度变化', '## 到账', '## 待到账', '## 新邀约', '## 冲突提醒', '## 待确认建议'):
            self.assertIn(heading, text)
        self.assertIn('¥1000.0 CNY', text)
        self.assertIn('未推送到任何外部渠道', text)
        self.assertIn('合成合作', text)
        self.assertEqual(self.review.weekly_report_due(datetime(2026, 10, 26, 12, 0, tzinfo=beijing)), '')
        again = self.review.report(week)
        self.assertEqual(again['markdown'], text)
        self.assertEqual(status, 'succeeded')

    def test_report_week_validation_and_missing_file_are_honest(self):
        for week in ('2026-44', 'nope', '2026-W54'):
            with self.subTest(week=week), self.assertRaises(ValueError):
                self.review.report(week)
        with self.assertRaises(ValueError):
            self.review.report('2026-W01')

    # ------------------------------------------------------------- plumbing

    def test_model_turn_is_fresh_tool_less_and_writes_nothing_outside_its_folder(self):
        seen = []

        def fake(runtime, job, system, tools):
            seen.append((dict(job), tools, runtime.owner.folder))
            return {'status': 'done', 'answer': '{"proposals":[]}'}

        with patch('pi_runtime.PiRuntime.run', fake):
            answer = self.review.call_model('c' * 32, '合成资料', '系统说明', threading.Event(),
                                            {'model': 'synthetic'}, time.monotonic() + 10)
        self.assertEqual(answer, '{"proposals":[]}')
        self.assertEqual(seen[0][1], [])
        self.assertEqual(seen[0][0]['policy']['allowed_tools'], [])
        self.assertEqual(seen[0][0]['context'], {})
        self.assertTrue(str(seen[0][2]).startswith(str(self.review.root)))

    def test_model_that_does_not_finish_leaves_the_deal_untouched(self):
        with patch('pi_runtime.PiRuntime.run', lambda runtime, job, system, tools: {'status': 'error'}):
            with self.assertRaisesRegex(ValueError, '未返回有效正文'):
                self.review.call_model('d' * 32, 'x', 'y', threading.Event(), {'model': 'synthetic'},
                                       time.monotonic() + 10)

    def test_two_reviews_cannot_run_at_once(self):
        holder_started = threading.Event()
        release = threading.Event()

        def hold():
            self.review.lock.acquire()
            holder_started.set()
            release.wait(5)
            self.review.lock.release()

        holder = threading.Thread(target=hold, daemon=True)
        holder.start()
        self.assertTrue(holder_started.wait(5))
        try:
            with self.assertRaisesRegex(ValueError, '不重复启动'):
                self.review.analyze('e' * 32, {'body': '合成'}, threading.Event(), {'model': 'synthetic'},
                                    time.monotonic() + 5)
        finally:
            release.set()
            holder.join(5)

    def test_private_files_and_no_outward_call(self):
        self.assertEqual(self.review.path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.review.report_folder.stat().st_mode & 0o777, 0o700)
        self.assertEqual(parse_json('```json\n{"proposals":[]}\n```'), {'proposals': []})


class SchedulerKindTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.scheduler = ScheduledTasks(self.tmp.name, autostart=False)
        self.addCleanup(self.scheduler.close)
        from types import SimpleNamespace
        config = {'model': 'synthetic', 'endpoint': 'http://127.0.0.1:1/v1/chat/completions', 'api_key': 'x'}
        self.scheduler.assistant = SimpleNamespace(
            model_document=lambda: {'profiles': [{'id': 'fake', 'config': config}]},
            settings=lambda: {'profiles': [{'profile_id': 'fake', 'display_name': '合成模型',
                                            'model': 'synthetic', 'configured': True}]})

    def payload(self, **changes):
        payload = dict(name='商单定时分析', kind='deals_review', body='关注交付与到账', cwd=self.tmp.name,
                       frequency='interval', minutes=240, at='09:00', weekdays=[0], ends_at=None, timeout=900,
                       enabled=True, profile_id='fake', allow_commands=False)
        payload.update(changes)
        return payload

    def test_deals_review_is_a_selectable_kind_with_its_own_executor_and_default_interval(self):
        saved = self.scheduler.save(self.payload())
        task = self.scheduler.listing()['tasks'][0]
        self.assertEqual(task['kind'], 'deals_review')
        self.assertEqual(task['executor_name'], '商单分析（Pi）')
        self.assertEqual(task['assistant_id'], 'isolated')
        self.assertEqual(task['tool_groups'], [])
        self.assertIsNotNone(task['next_run'])
        self.assertAlmostEqual(task['next_run'] - time.time(), 240 * 60, delta=5)

    def test_deals_review_rejects_commands_clue_delivery_and_missing_model(self):
        for changes in ({'allow_commands': True}, {'result_destination': 'clues'}, {'clue_id': 'a' * 32},
                        {'profile_id': 'missing'}, {'assistant_id': 'workbench'}, {'tool_groups': ['memory']}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.scheduler.save(self.payload(**changes))

    def test_missing_analyzer_entry_is_a_honest_failure_and_never_touches_a_ledger(self):
        saved = self.scheduler.save(self.payload())
        run = self.scheduler.control(saved['id'], 'run')['id']
        deadline = time.monotonic() + 5
        for _ in range(100):
            row = self.scheduler.get_run(run)
            if row['status'] != 'running':
                break
            time.sleep(.05)
        self.assertEqual(row['status'], 'failed')
        self.assertIn('商单分析入口未连接', row['output'])

    def test_analyzer_result_is_recorded_verbatim_and_partial_keeps_no_exit_code(self):
        saved = self.scheduler.save(self.payload())
        self.scheduler.deals_analyzer = lambda run_id, task, cancel, config, deadline: (
            'partial', 0, json.dumps({'progress': {'checked': 3}}))
        run = self.scheduler.control(saved['id'], 'run')['id']
        for _ in range(100):
            row = self.scheduler.get_run(run)
            if row['status'] != 'running':
                break
            time.sleep(.05)
        self.assertEqual(row['status'], 'partial')
        self.assertIsNone(row['exit_code'])
        self.assertEqual(json.loads(row['output'])['progress']['checked'], 3)


class DealsReviewHTTPTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.http = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.http.store = Store(self.tmp.name)
        self.thread = threading.Thread(target=self.http.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.thread.join)
        self.base = 'http://127.0.0.1:' + str(self.http.server_port)
        self.addCleanup(self.http.shutdown)
        self.addCleanup(self.http.server_close)

    def get(self, path):
        try:
            with urlopen(Request(self.base + path, headers={'Origin': self.base, 'X-Article-Studio': '1'})) as r:
                return r.status, json.load(r)
        except HTTPError as exc:
            with exc:
                return exc.code, json.load(exc)
        except OSError as exc:
            return 0, exc

    def test_review_summary_and_report_routes_are_read_only_and_validated(self):
        code, body = self.get('/api/deals/review')
        self.assertEqual(code, 200)
        self.assertEqual(body['run_id'], '')
        self.assertEqual(body['findings'], {})
        self.assertEqual(body['proposals'], [])
        for path in ('/api/deals/review/report', '/api/deals/review/report?week=nope',
                     '/api/deals/review/report?week=2026-W01&week=2026-W02'):
            code, _ = self.get(path)
            self.assertEqual(code, 400, path)
        code, _ = self.get('/api/deals/review/report?week=2026-W01')
        self.assertEqual(code, 400)

    def test_review_routes_do_not_shadow_a_deal_id(self):
        deals = self.http.store.deals
        ident = deals.save(dict(title='合成', customer='客户', request_key=uuid.uuid4().hex))['id']
        code, body = self.get('/api/deals/' + ident)
        self.assertEqual(code, 200)
        self.assertEqual(body['title'], '合成')


if __name__ == '__main__':
    unittest.main()
