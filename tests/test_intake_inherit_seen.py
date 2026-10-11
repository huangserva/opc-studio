"""继承他人分析时，源知识库正文已被单独删除 —— 不得抛 ValueError，也不得写 done/seen。

依据：`coordination/review-clues-retry-2-report.md` §L1 与
`coordination/fixes-clue-pipeline-locks-report.md` 第 38 行核对结论。

场景：父回收项仍在、分析已完成；它对应的知识库正文被**单独删除**（父回收项没删）。
此时又收进一条指向同一篇资料的回收项，`analyze_drop` 会走「继承他人分析」分支。
旧代码在该分支直接调 `mark_seen`，而 `mark_seen` 读 `knowledge.document(...)` ——
正文没了就抛 `ValueError('资料不存在，请重新检索')`，整条分析随之炸掉。

契约要求（`coordination/fixes-intake-inherit-seen-contract.md`）：

1. 继承前验证源文档仍存在且版本可读；
2. 失效时**不得**写 done/seen、不得静默吞错、不得盲标完成，要给出可诊断状态；
3. 不改正常继承路径的行为（下面 `test_a_successful_inherit_still_works` 盯这条）。

全程假模型、假抓取：不联网、不调真实模型、不读任何密钥，只用临时目录。
"""
import json
from pathlib import Path
import sys
import tempfile
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
CARD = {'prompt': '旧码头黄昏，蹲拍视角，水面倒映天空，人物只留背影，暖色低对比',
        'model': 'qi21', 'scene': '公众号头图', 'refs': ['e0']}
LINK = 'https://example.com/pier'


def payload(value):
    return json.dumps(value, ensure_ascii=False)


class FakeWeb:
    def __call__(self):
        return self

    def fetch(self, args, job):
        return {'url': args['url'], 'title': '旧码头的蹲拍', 'markdown': SOURCE, 'start': 0,
                'total_chars': len(SOURCE), 'truncated': False, 'next_start': None}


class InheritSeenTests(unittest.TestCase):
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

    def analysis_status(self, drop_id):
        with self.cards.db() as connection:
            row = connection.execute('SELECT status FROM analysis WHERE drop_id=?', (drop_id,)).fetchone()
        return row['status'] if row else None

    def documents(self):
        with self.store.knowledge.db() as connection:
            return {row['id'] for row in connection.execute('SELECT id FROM documents')}

    # ── 红：源正文被单独删除时，继承分支不得抛 ValueError ──────────────────────
    def test_inheriting_from_a_deleted_source_document_does_not_raise(self):
        first = self.link_drop()
        self.cards.analyze_drop(first['id'])
        self.assertEqual(self.analysis_status(first['id']), 'done', '前置：父回收项分析完成')
        document_id = first['document_id']
        self.assertIn(document_id, self.documents())

        # 再收进一条指向同一篇资料的回收项（正文还在 → 解析成 duplicate，共用同一篇正文）。
        second = self.link_drop()
        self.assertEqual(second['document_id'], document_id, '两条回收项共用同一篇正文')

        # 现在单独删掉知识库正文 —— 父回收项没删，第二条也还指着它。
        removed = self.store.knowledge.delete_document(document_id)
        self.assertTrue(removed['removed'], '前置：正文应被删掉')
        self.assertNotIn(document_id, self.documents())
        self.assertTrue(self.intake.exists(first['id']), '父回收项必须还在')

        # 触发分析 → 走「继承他人分析」分支。旧代码在这里抛 ValueError。
        state = self.cards.analyze_drop(second['id'])

        self.assertEqual(state['status'], 'failed', '源正文没了，不得标 done：%r' % state)
        self.assertTrue(state['retryable'], '应为可重试失败，交回重新分析：%r' % state)
        self.assertIn('正文', state['error'] + state['summary'], '失败原因要能诊断：%r' % state)
        self.assertNotIn(second['id'], self.seen_rows(), '不得写已读指纹')
        self.assertNotEqual(self.analysis_status(second['id']), 'done', '不得盲标完成')

    # ── 绿：正常继承路径行为不变 ──────────────────────────────────────────────
    def test_a_successful_inherit_still_works(self):
        first = self.link_drop()
        self.cards.analyze_drop(first['id'])
        calls_after_first = len(self.calls)

        second = self.link_drop()          # 正文还在 → 正常继承
        state = self.cards.analyze_drop(second['id'])

        self.assertEqual(state['status'], 'done')
        self.assertEqual(state['summary'], READING['summary'])
        self.assertEqual(state['inherited_from'], first['id'])
        self.assertEqual(len(self.calls), calls_after_first, '继承不该再花模型调用')
        self.assertIn(second['id'], self.seen_rows(), '正常继承应落已读指纹')


if __name__ == '__main__':
    unittest.main(verbosity=2)
