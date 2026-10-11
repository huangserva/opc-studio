"""20s-L2：提炼计数如实 + mark_seen 只把「正文已删」当失效。

依据：`coordination/review-intake-inherit-seen-report.md` §3.1、§3.2、§3.4
（20s-L1 复核发现的 L1/L2/L4）与定案 `coordination/review-intake-inherit-seen-decision.md`。

20s-L1 让 `mark_seen` 在正文被单独删除时返回 False 而不是抛，四个调用点里三个
直接用了这个返回值，但两处**没有把返回值算进本轮摘要**：

1. `:649` 未识别正文分支 —— 返回值被丢弃，仍 `checked += 1`，
   摘要说「已处理 N 条」而库里其实没写 seen。
2. `:684` 提炼收尾 —— `if gone or not self.mark_seen(...): continue`，
   把「正文没了」与「回收项被删」同等静默跳过，窄竞态下本轮 errors 为空、
   用户看不到任何提示。
3. `mark_seen` 的 `except ValueError` 过宽 —— `knowledge.document()` 还有
   `json.loads` 坏数据那条 ValueError 路径（JSONDecodeError 是 ValueError 子类），
   数据损坏会被当成「用户删了正文」而静默返回 False。

契约要求（`coordination/fixes-intake-seen-accounting-contract.md`）：

1. `:649` 返回 False 时不计 `checked`，并如实写一条；
2. `:684` 区分「回收项已删」（静默跳过）与「正文已删」（写 errors）；
3. `mark_seen` 只把「资料不存在」当正文已删，JSON 损坏等其他 ValueError 照常抛出，
   且 `_analyze` 与继承分支在抛出时仍落到既有可诊断状态、不炸整轮；
4. 不改 `:1339`、不改继承语义；`pending()` 不无限重试的保证补断言。

全程假模型、假抓取：不联网、不调真实模型、不读任何密钥，只用临时目录。
"""
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tests.intake_dependency_patches import guard_intake_dependencies, patch_intake_dependency
from intake_cards import IntakeCards
from intake_store import Intake
from knowledge_store import Knowledge
from server import Store

SOURCE = ('黄昏的旧码头先把木头泡软，绳索在风里轻轻晃，远处渡船只留一条灯线。'
          '拍照的人蹲下来，让水面把天空再复制一遍，人物只留一个背影，不露脸。')
READING = {'summary': '旧码头黄昏的蹲拍方法：用水面倒影复制天空，人物只留背影。',
           'points': ['水面倒影是主手法', '人物不露脸'],
           'why': '开头就能直接借的取景方式。',
           'angles': [{'title': '写一条蹲拍笔记', 'how': '从水面倒影切入，交代蹲位与光线'}]}
CARD = {'prompt': '旧码头黄昏，蹲拍视角，水面倒映天空，人物不露背影，暖色低对比',
        'model': 'qi21', 'scene': '公众号头图', 'refs': ['e0']}
LINK = 'https://example.com/pier'
# 空正文：evidence() 建不出任何 ref，走 `:649` 的「资料正文未识别」分支
EMPTY_BODY = '   \n  '


def payload(value):
    return json.dumps(value, ensure_ascii=False)


class FakeWeb:
    """markdown 由每个用例自己指定，默认是正常正文。"""

    markdown = SOURCE

    def __call__(self):
        return self

    def fetch(self, args, job):
        return {'url': args['url'], 'title': '旧码头的蹲拍', 'markdown': self.markdown,
                'start': 0, 'total_chars': len(self.markdown), 'truncated': False, 'next_start': None}


class SeenAccountingTests(unittest.TestCase):
    def setUp(self):
        guard_intake_dependencies(self)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(self.tmp.name)
        self.store._knowledge = Knowledge(self.store, sources=[])
        self.intake = Intake(self.store.knowledge, self.tmp.name)
        self.store._creative_intake = self.intake
        self.cards = IntakeCards(self.intake, self.store.knowledge, self.tmp.name)
        self.store._intake_cards = self.cards
        self.intake.analysis_provider = self.cards.analysis_states
        self.cards.model_provider = lambda: {'model': 'synthetic', 'timeout_seconds': 100}
        self.cards.analysis_retry_wait = 0
        patch_intake_dependency(self, "WebTools", FakeWeb())
        self.calls = []
        self.answers = {'read': payload(READING), 'cards': payload({'cards': [CARD]})}

        def fake(identity, prompt, system, cancel, config, deadline):
            self.calls.append(identity)
            answer = self.answers.get('read' if identity.endswith('-read') else 'cards')
            if isinstance(answer, Exception):
                raise answer
            return answer
        self.cards.call_model = fake

    # ── 造数与观测 ────────────────────────────────────────────────────────────
    def link_drop(self, url=LINK, reason='这个开头方式可以借'):
        saved = self.intake.receive({'kind': 'link', 'url': url, 'reason': reason})
        return self.intake.resolve(saved['id'])

    def seen_rows(self):
        with self.cards.db() as connection:
            return {row['drop_id'] for row in connection.execute('SELECT drop_id FROM seen')}

    def corrupt_tags(self, document_id):
        """把 tags 写成坏 JSON —— 触发 knowledge.document() 的 JSONDecodeError 路径。"""
        with self.store.knowledge.db() as connection:
            connection.execute('UPDATE documents SET tags=? WHERE id=?', ('{坏掉的 json', document_id))

    def kill_document(self, drop):
        """只删知识库正文，父回收项留着 —— 20s-L1 的场景。"""
        return self.store.knowledge.delete_document(drop['document_id'])

    def analyze(self, timeout=10000):
        """跑一轮定时提炼，返回 (status, data)。deadline 必须远大于 timeout_seconds。"""
        result = self.cards._analyze('r' * 32, {'body': '提炼候选卡'}, threading.Event(),
                                     {'model': 'synthetic', 'timeout_seconds': 100},
                                     time.monotonic() + timeout)
        status, raw = result[0], result[2]
        return status, (json.loads(raw) if isinstance(raw, str) else raw)

    def drop_seen(self, drop_id):
        with self.cards.db() as connection:
            connection.execute('DELETE FROM seen WHERE drop_id=?', (drop_id,))

    def empty_evidence(self, drop):
        """替身：正文为空 → evidence() 建不出 ref → 走 `:649` 未识别正文分支。

        不用占位句是因为 link_drop 一定带 reason/url，extractable() 会判 True。
        """
        return {'body': EMPTY_BODY, 'id': drop['document_id']}, {}

    # ── 1. :649 未识别正文分支：返回 False 时不计 checked，并如实写一条 ──────────
    def test_unrecognised_branch_does_not_count_when_mark_seen_false(self):
        """空正文 → 走 :649；mark_seen 因正文被删返回 False → 不得计入 checked。"""
        drop = self.link_drop()
        self.drop_seen(drop['id'])
        with patch_object(self.cards, 'evidence', self.empty_evidence):
            real = self.cards._mark_seen_result
            self.cards._mark_seen_result = lambda d, f: (
                self.kill_document(d), real(d, f))[1] if d['id'] == drop['id'] else real(d, f)
            status, data = self.analyze()
        self.cards._mark_seen_result = real

        self.assertEqual(data['checked'], 0, '正文没了就不能计入已处理：%r' % data)
        self.assertNotIn(drop['id'], self.seen_rows(), '不得写已读指纹')
        self.assertTrue(data['errors'], '本轮必须有一条可见提示，用户才知道这条没被处理：%r' % data)
        self.assertIn(drop['id'][:8], ' '.join(data['errors']), '错误里要指明是哪一条：%r' % data['errors'])
        self.assertIn('正文', ' '.join(data['errors']), '错误原因要说清是正文读不到：%r' % data['errors'])

    # ── 2. :684 区分「回收项已删」与「正文已删」 ────────────────────────────────
    def test_extract_tail_reports_document_gone_but_stays_quiet_when_drop_deleted(self):
        """`:684` 的两个 False 原因必须区别对待。"""
        # (a) 正文被删（父行仍在）→ 写 errors
        drop = self.link_drop()
        self.cards.analyze_drop(drop['id'])
        self.drop_seen(drop['id'])
        real = self.cards._mark_seen_result
        self.cards._mark_seen_result = lambda d, f: (
            self.kill_document(d), real(d, f))[1] if d['id'] == drop['id'] else real(d, f)
        status, data = self.analyze()
        self.assertTrue(self.intake.exists(drop['id']), '前置：父回收项必须还在')
        self.assertEqual(data['checked'], 0, '正文没了不计已处理：%r' % data)
        self.assertTrue(data['errors'], '「正文已删」必须在本轮可见：%r' % data)
        self.assertIn(drop['id'][:8], ' '.join(data['errors']), '要指明是哪一条：%r' % data['errors'])

    def test_extract_tail_stays_silent_when_drop_was_deleted(self):
        """父回收项被删 → 静默跳过，不报错（黄总主动删的，不该打扰）。"""
        drop = self.link_drop()
        self.cards.analyze_drop(drop['id'])
        self.drop_seen(drop['id'])
        real = self.cards._mark_seen_result
        self.cards._mark_seen_result = lambda d, f: (
            self.intake.delete(d['id']), real(d, f))[1] if d['id'] == drop['id'] else real(d, f)
        status, data = self.analyze()
        self.assertFalse(self.intake.exists(drop['id']), '前置：父回收项应已被删')
        self.assertFalse([e for e in data['errors'] if drop['id'][:8] in e],
                         '回收项被删是用户主动行为，不该报错：%r' % data['errors'])

    # ── 3. mark_seen 捕获收窄：JSON 损坏照常抛出 ────────────────────────────────
    def test_mark_seen_lets_json_corruption_raise(self):
        """数据损坏不是「正文被删」，必须照常抛出，不能静默返回 False。"""
        drop = self.link_drop()
        self.cards.analyze_drop(drop['id'])
        self.drop_seen(drop['id'])
        self.corrupt_tags(drop['document_id'])
        with self.assertRaises(ValueError) as caught:
            self.cards.mark_seen(drop, self.cards.fingerprint(drop))
        self.assertNotIsInstance(caught.exception, type(None))
        self.assertNotIn(drop['id'], self.seen_rows(), '抛异常时不得写指纹')

    def test_mark_seen_still_returns_false_when_document_gone(self):
        """「资料不存在」仍然返回 False —— 这是 20s-L1 修好的行为，不能回退。"""
        drop = self.link_drop()
        self.cards.analyze_drop(drop['id'])
        self.drop_seen(drop['id'])
        self.kill_document(drop)
        self.assertFalse(self.cards.mark_seen(drop, self.cards.fingerprint(drop)),
                         '正文被删必须返回 False（20s-L1 的修复）')
        self.assertNotIn(drop['id'], self.seen_rows())

    def test_json_corruption_in_analyze_reaches_errors_not_crash(self):
        """JSON 损坏在 `_analyze` 里必须落到 errors，不能炸整轮。

        `partial` 是既有设计：只要 errors 非空就返回 partial（见 `_analyze` 收尾），
        所以这里断言的是「没抛异常 + 记进 errors」，不是 succeeded。
        """
        drop = self.link_drop()
        self.drop_seen(drop['id'])
        self.corrupt_tags(drop['document_id'])
        status, data = self.analyze()          # 不抛异常即为「没炸整轮」
        self.assertEqual(status, 'partial', '有 errors 时按既有设计返回 partial：%r' % data)
        self.assertTrue(data['errors'], '损坏必须被记进 errors：%r' % data)
        self.assertIn(drop['id'][:8], ' '.join(data['errors']), '要指明是哪一条：%r' % data['errors'])
        self.assertNotIn(drop['id'], self.seen_rows(), '不得写指纹')

    def test_json_corruption_in_inherit_lands_failed(self):
        """JSON 损坏在继承分支必须落到既有可诊断状态（failed），不能抛给调用方。"""
        first = self.link_drop()
        self.cards.analyze_drop(first['id'])
        second = self.link_drop()          # 正文还在 → 正常继承
        self.corrupt_tags(first['document_id'])
        state = self.cards.analyze_drop(second['id'])
        self.assertEqual(state['status'], 'failed', '数据损坏不得盲标完成：%r' % state)
        self.assertTrue(state['retryable'], '应为可重试：%r' % state)
        self.assertNotIn(second['id'], self.seen_rows(), '不得写指纹')

    def test_mark_seen_raising_inside_analyze_does_not_crash_the_round(self):
        """窄竞态：evidence() 读到了，mark_seen 那一刻才坏 —— 整轮不能炸。

        `test_json_corruption_in_analyze_reaches_errors_not_crash` 走的是 evidence()
        先抛的常见路径；这条专门盯 mark_seen 自己抛的那一步。
        """
        drop = self.link_drop()
        self.drop_seen(drop['id'])

        def boom(*args, **kwargs):
            raise ValueError('Expecting property name enclosed in double quotes')

        with patch_object(self.cards, '_mark_seen_result', boom):
            status, data = self.analyze()          # 不抛异常即为「没炸整轮」
        self.assertIn(status, ('partial', 'succeeded'), '整轮必须正常收尾：%r' % data)
        self.assertTrue(data['errors'], 'mark_seen 抛错必须被记进 errors：%r' % data)
        self.assertIn(drop['id'][:8], ' '.join(data['errors']), '要指明是哪一条：%r' % data['errors'])
        self.assertEqual(data['checked'], 0, '没写成指纹就不能计入已处理：%r' % data)
        self.assertNotIn(drop['id'], self.seen_rows())

    def test_mark_seen_raising_in_unrecognised_branch_does_not_crash(self):
        """`:649` 分支同样要收口，不能因 mark_seen 抛错炸整轮。"""
        drop = self.link_drop()
        self.drop_seen(drop['id'])

        def boom(*args, **kwargs):
            raise ValueError('Expecting property name enclosed in double quotes')

        with patch_object(self.cards, 'evidence', self.empty_evidence), \
                patch_object(self.cards, '_mark_seen_result', boom):
            status, data = self.analyze()
        self.assertIn(status, ('partial', 'succeeded'), '整轮必须正常收尾：%r' % data)
        self.assertTrue(data['errors'], '必须记进 errors：%r' % data)
        self.assertEqual(data['checked'], 0, '不得计入已处理：%r' % data)

    # ── 4. 不回退：pending() 不无限重试 + :1339 / 继承语义不变 ──────────────────
    def test_pending_does_not_reselect_drop_whose_document_is_gone(self):
        """20s-L1 的保证不能因本次改动丢掉。"""
        drop = self.link_drop()
        self.cards.analyze_drop(drop['id'])
        self.drop_seen(drop['id'])
        self.kill_document(drop)
        batch, _ = self.cards.pending(model='synthetic')
        self.assertNotIn(drop['id'], [row['id'] for row in batch],
                         '正文没了不该被反复选中（否则每轮都白烧一次）')

    def test_successful_run_still_counts_and_marks(self):
        """正常路径不得被误伤：checked 计数、seen 写入都要照旧。"""
        drop = self.link_drop()
        status, data = self.analyze()
        self.assertEqual(data['checked'], 1, '正常处理一条应计入 checked：%r' % data)
        self.assertIn(drop['id'], self.seen_rows(), '正常路径必须写指纹')
        self.assertFalse(data['errors'], '正常路径不该有 errors：%r' % data['errors'])

    def test_rejected_branch_still_counts_when_mark_seen_succeeds(self):
        """`:649` 的原行为：mark_seen 成功时仍要计 checked、仍算丢弃。"""
        drop = self.link_drop()
        self.drop_seen(drop['id'])
        with patch_object(self.cards, 'evidence', self.empty_evidence):
            status, data = self.analyze()
        self.assertEqual(data['checked'], 1, 'mark_seen 成功时照旧计入：%r' % data)
        self.assertIn(drop['id'], self.seen_rows(), '必须写指纹')
        self.assertIn('资料正文未识别，暂不提炼', data['drop_reasons'], '仍是丢弃原因：%r' % data)
        self.assertFalse(data['errors'], '正常路径不该有 errors：%r' % data['errors'])

    # ── 5. 20s-L3：原因在锁内判定，锁外不得再用 exists() 复判 ──────────────────
    def test_document_gone_still_reported_when_exists_would_say_gone(self):
        """正文被单删、而锁外 exists() 会返回 False（模拟父行在判定后被删）时，
        仍要写出「知识库正文读不到」那条 errors 并返回 False。

        旧实现先 mark_seen 返回 False、再在锁外 exists() 复判：两次读之间父行若被删，
        诊断会被静默吞掉。20s-L3 把原因挪进锁内，本用例即盯这个交错。
        """
        drop = self.link_drop()
        self.cards.analyze_drop(drop['id'])
        self.drop_seen(drop['id'])
        self.kill_document(drop)                     # 正文被单删 → 原因应为 document_gone
        errors = []
        with patch_object(self.intake, 'exists', lambda _id: False):   # 锁外复判会误判成「父行没了」
            counted = self.cards.mark_seen_quietly(drop, self.cards.fingerprint(drop), errors)
        self.assertFalse(counted, '正文没了不能算已处理')
        self.assertTrue(errors, '原因在锁内定，不该被锁外的 exists() 吞掉：%r' % errors)
        self.assertIn(drop['id'][:8], ' '.join(errors), '要指明是哪一条：%r' % errors)
        self.assertIn('正文', ' '.join(errors), '原因要说清是正文读不到：%r' % errors)

    def test_mark_seen_quietly_never_rechecks_exists_outside_lock(self):
        """更强的守卫：mark_seen_quietly 根本不得再调用 intake.exists。

        把 exists 换成抛错替身：若新实现仍在锁外复判，本用例会直接 ERROR。
        """
        drop = self.link_drop()
        self.cards.analyze_drop(drop['id'])
        self.drop_seen(drop['id'])
        self.kill_document(drop)
        errors = []

        def forbidden(_id):
            raise AssertionError('锁外复判 intake.exists 应已被移除（20s-L3）')

        with patch_object(self.intake, 'exists', forbidden):
            counted = self.cards.mark_seen_quietly(drop, self.cards.fingerprint(drop), errors)
        self.assertFalse(counted)
        self.assertTrue(errors, '正文读不到必须可见：%r' % errors)
        self.assertIn('正文', ' '.join(errors))

    def test_parent_gone_stays_silent_without_exists_recheck(self):
        """父回收项被删 → 原因在锁内即为 parent_gone，静默跳过、不写 errors。"""
        drop = self.link_drop()
        self.cards.analyze_drop(drop['id'])
        self.drop_seen(drop['id'])
        self.intake.delete(drop['id'])               # 父行被删 → parent_gone
        errors = []
        with patch_object(self.intake, 'exists', lambda _id: True):   # 锁外复判即使说「还在」也不该写
            counted = self.cards.mark_seen_quietly(drop, self.cards.fingerprint(drop), errors)
        self.assertFalse(counted)
        self.assertFalse(errors, '父行被删是用户主动行为，静默：%r' % errors)
        self.assertFalse(self.cards.mark_seen(drop, self.cards.fingerprint(drop)),
                         'mark_seen 公开语义：parent_gone → False')

    def test_mark_seen_public_bool_semantics_unchanged(self):
        """20s-L3：mark_seen 仍是 bool（:1250 继承分支与 :1375 收尾、多个测试在用）。"""
        ok = self.link_drop()
        self.cards.analyze_drop(ok['id'])
        self.drop_seen(ok['id'])
        self.assertTrue(self.cards.mark_seen(ok, self.cards.fingerprint(ok)), 'ok → True')
        self.assertIn(ok['id'], self.seen_rows(), 'ok 必须写指纹')
        gone = self.link_drop()
        self.cards.analyze_drop(gone['id'])
        self.drop_seen(gone['id'])
        self.kill_document(gone)
        self.assertFalse(self.cards.mark_seen(gone, self.cards.fingerprint(gone)), 'document_gone → False')
        self.assertNotIn(gone['id'], self.seen_rows())
        parentless = self.link_drop()
        self.cards.analyze_drop(parentless['id'])
        self.drop_seen(parentless['id'])
        self.intake.delete(parentless['id'])
        self.assertFalse(self.cards.mark_seen(parentless, self.cards.fingerprint(parentless)),
                         'parent_gone → False')
        self.assertNotIn(parentless['id'], self.seen_rows())


class patch_object:
    """极简替身：只在 with 期间把实例属性换掉，退出后还原。"""

    def __init__(self, obj, name, value):
        self.obj, self.name, self.value = obj, name, value
        self.had = hasattr(obj, name)
        self.old = getattr(obj, name, None)

    def __enter__(self):
        setattr(self.obj, self.name, self.value)
        return self.value

    def __exit__(self, *exc):
        if self.had:
            setattr(self.obj, self.name, self.old)
        else:
            try:
                delattr(self.obj, self.name)
            except AttributeError:
                pass
        return False


if __name__ == '__main__':
    unittest.main(verbosity=2)