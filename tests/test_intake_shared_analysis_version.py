"""R12：共享分析缓存必须校验正文版本（knowledge documents.hash）。

同一条资料（document_id 相同）的正文被更新后，旧正文下产出的分析不得再被复用；
旧分析行没有版本信息时安全回退（不复用、不报错、不静默花模型调用）。

返修 b（M1）补上分析/继承**过程中**正文 A→B 的时序：done 记的必须是实际送给模型或被继承
那份分析对应的版本，不得在落盘时改取当前最新正文；继承匹配后正文变了就作废这次继承，
走现有正常分析路径。两条时序用假阅读器/确定性钩子造出来，不靠 sleep 碰概率。

返修 c（M1）补上继承作废**之后**的记账：继承作废时不得把未成功处理的当前正文留成已处理。
`mark_seen` 在继承匹配成功时就写了 seen 并放行重试记录，复查发现正文换版后只作废继承、
不撤销这次标记，于是「阅读失败」或「卡片失败」的项被 `pending()` 当已处理跳过，页面却写着
「定时提炼会重试」。另补 L2：末次复查返回 true 之后、写盘实参求值之前正文又换版的常驻用例。

依据：`coordination/queue-triage-3-report.md` 的 R12 行（来源
`coordination/fixes-intake-seen-accounting-report.md:243`），合同
`coordination/smallfix-intake-contract.md`，返修合同
`coordination/smallfix-intake-b-contract.md`（复核 M1：
`coordination/review-smallfix-intake-report.md`）。

全程假模型、假抓取：零外网、零真实模型调用、不读任何密钥，只用临时目录。
运行：
    ../article-importer/.venv/bin/python -m unittest tests.test_intake_shared_analysis_version
"""
import hashlib
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
UPDATED = ('清晨的新码头先把甲板冲净，缆绳绷得很直，远处渡船拖着一条白线。'
           '拍照的人站到高处，让栈桥把海面切成两半，人物只留一个侧影，不露脸。')
READING = {'summary': '旧码头黄昏的蹲拍方法：用水面倒影复制天空，人物只留背影。',
           'points': ['水面倒影是主手法', '人物不露脸'],
           'why': '开头就能直接借的取景方式。',
           'angles': [{'title': '写一条蹲拍笔记', 'how': '从水面倒影切入，交代蹲位与光线'}]}
READING_UPDATED = {'summary': '新码头清晨的高位拍法：用栈桥切分海面，人物只留侧影。',
                   'points': ['高位切分海面', '侧影不露脸'],
                   'why': '换一种取景就能重写同一条笔记。',
                   'angles': [{'title': '写一条高位拍法笔记', 'how': '从栈桥切分切入，交代机位与光线'}]}
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


class SharedAnalysisVersionTests(unittest.TestCase):
    """共享分析（按 document_id 复用）必须带正文版本；版本不符即视为不可复用。"""

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
        self.reads = 0

        def fake(identity, prompt, system, cancel, config, deadline):
            self.calls.append(identity)
            if not identity.endswith('-read'):
                return payload({'cards': [CARD]})
            self.reads += 1
            return payload(READING if self.reads == 1 else READING_UPDATED)

        self.cards.call_model = fake

    # ── 造数与观测 ────────────────────────────────────────────────────────────
    def link_drop(self, url=LINK, reason='这个开头方式可以借'):
        saved = self.intake.receive({'kind': 'link', 'url': url, 'reason': reason})
        return self.intake.resolve(saved['id'])

    def update_body(self, document_id, body):
        """模拟「同一 ID 的资料被更新」：正文与 hash 一起改（hash 用 knowledge 的同一公式）。"""
        with self.store.knowledge.db() as connection:
            row = dict(connection.execute('SELECT * FROM documents WHERE id=?', (document_id,)).fetchone())
            item = {'id': row['id'], 'source': row['source'], 'kind': row['kind'], 'title': row['title'],
                    'body': body, 'url': row['url'], 'modified': row['modified'], 'tags': row['tags']}
            digest = hashlib.sha256(json.dumps(item, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
            connection.execute('UPDATE documents SET body=?, hash=? WHERE id=?', (body, digest, document_id))
        return digest

    def stored_version(self, drop_id):
        """分析行里记下的正文版本；没有就是 ''。"""
        with self.cards.db() as connection:
            row = connection.execute('SELECT data FROM analysis WHERE drop_id=?', (drop_id,)).fetchone()
        return (json.loads(row['data']) if row else {}).get('document_hash', '')

    def drop_version(self, drop_id):
        drop = self.intake.detail(drop_id)
        return self.store.knowledge.document_hashes([drop['document_id']])[drop['document_id']]

    def projection(self, drop_id):
        return self.cards.analysis_states([self.intake.detail(drop_id)])[drop_id]

    def seen_row(self, drop_id):
        """这条回收项在 seen 里的已读标记；没有就是 {}。"""
        with self.cards.db() as connection:
            row = connection.execute('SELECT * FROM seen WHERE drop_id=?', (drop_id,)).fetchone()
        return dict(row) if row else {}

    def retry_rows(self, drop_id):
        with self.cards.db() as connection:
            return [dict(row) for row in
                    connection.execute('SELECT * FROM extraction_retry WHERE drop_id=?', (drop_id,))]

    def ready_ids(self, model=None):
        """定时提炼这一轮真的会取哪些回收项（与定时任务同一入口）。"""
        ready, _ = self.cards.pending(200, model=model)
        return [row['id'] for row in ready]

    def break_inherit_with_new_body(self):
        """把「继承匹配成功 → mark_seen 落库」之间插入一次正文换版（确定性钩子）。

        返回命中的 drop_id 列表；mark_seen 在里面读到的是新正文，所以它写下的 seen 是**当前版**，
        而这次继承随后会被作废。
        """
        original = self.cards.mark_seen
        hits = []

        def change_body_then_mark_seen(drop, fingerprint, *args, **kwargs):
            hits.append(drop['id'])
            self.update_body(drop['document_id'], UPDATED)
            return original(drop, fingerprint, *args, **kwargs)

        self.cards.mark_seen = change_body_then_mark_seen
        self.addCleanup(setattr, self.cards, 'mark_seen', original)
        return hits

    # ── 返修 c M1：继承作废后不得留下「当前正文已处理」的标记 ──────────────────
    def test_refused_inherit_with_failed_reading_returns_to_pending(self):
        """继承作废 → 正常分析阅读失败：这条必须回到定时提炼的 pending()。

        阅读失败记的是 retryable 失败，页面据此提示可重试；可 `mark_seen` 提前写下的当前正文
        seen 会让 `pending()` 按「同指纹 + 同版本已处理」跳过它，正文与指纹都不再变时永远不会重试。
        """
        first = self.link_drop()
        self.cards.analyze_drop(first['id'])
        second = self.link_drop()
        hits = self.break_inherit_with_new_body()
        calls_before = len(self.calls)

        def failing_read(*args, **kwargs):
            raise OSError('合成阅读器暂时失败，可重试')

        self.cards.call_model = failing_read
        state = self.cards.analyze_drop(second['id'])

        self.assertEqual(hits, [second['id']], '前置：正文换版发生在继承匹配之后、落盘之前')
        self.assertEqual(state['status'], 'failed', '阅读失败要如实记失败：%r' % state)
        self.assertTrue(state['retryable'], '阅读失败必须可重试')
        self.assertEqual(self.seen_row(second['id']), {},
                         '继承作废后不得把没成功处理的当前正文留成已处理')
        self.assertIn(second['id'], self.ready_ids(model='synthetic'),
                      '失败项要回到定时提炼队列，页面的「可重试」才成立')
        self.assertEqual(len(self.calls), calls_before, '阅读失败不得留下额外的模型调用')

        # 单变量对照：只把这条 seen 写回去（正文、分析、重试配置都不动），pending 就跳过它。
        with self.cards.db() as connection:
            connection.execute('INSERT INTO seen(drop_id,document_id,fingerprint,processed_at,document_hash)'
                               ' VALUES(?,?,?,?,?)',
                               (second['id'], second['document_id'], self.cards.fingerprint(second),
                                '2026-01-01T00:00:00', self.drop_version(second['id'])))
        self.assertNotIn(second['id'], self.ready_ids(model='synthetic'),
                         '对照：残留的当前版 seen 正是把失败项挤出定时队列的原因')

    def test_refused_inherit_with_failed_cards_returns_to_pending(self):
        """继承作废 → 阅读成功但卡片提炼失败：页面写「定时提炼会重试」，队列必须真收它。

        四块阅读已经拿到，卡片这一段留给兜底（`cards_done=False`，本轮不写 seen）。可继承匹配时
        `mark_seen` 已经写过一次当前版 seen，于是页面与队列互相矛盾。
        """
        first = self.link_drop()
        self.cards.analyze_drop(first['id'])
        second = self.link_drop()
        hits = self.break_inherit_with_new_body()

        def cards_fail(identity, prompt, system, cancel, config, deadline):
            if identity.endswith('-read'):
                return payload(READING_UPDATED)
            raise OSError('合成卡片提炼暂时失败')

        self.cards.call_model = cards_fail
        state = self.cards.analyze_drop(second['id'])

        self.assertEqual(hits, [second['id']], '前置：正文换版发生在继承匹配之后、落盘之前')
        self.assertEqual(state['status'], 'done', '阅读成功、卡片失败仍算这条分析完成：%r' % state)
        self.assertEqual(state['summary'], READING_UPDATED['summary'], '摘要来自当前正文')
        self.assertEqual(state['cards'], 0, '卡片这一段没成')
        self.assertEqual(state['cards_error'], '合成卡片提炼暂时失败')
        self.assertEqual(self.projection(second['id']).get('cards_error'), '合成卡片提炼暂时失败',
                         '列表页据此显示「提示词卡这一段没完成……定时提炼会重试」')
        self.assertEqual(self.seen_row(second['id']), {},
                         '卡片没提炼成就不能把当前正文算成已处理')
        self.assertIn(second['id'], self.ready_ids(model='synthetic'),
                      '页面承诺定时重试，pending() 就必须真的选中它')

    def test_refused_inherit_restores_the_retry_row_it_resolved(self):
        """继承作废还要撤销它对重试记录的放行：这次没处理完，就不能把重试记录标成 resolved。

        `mark_seen` 除了写 seen 还会把该回收项的 extraction_retry 改成 resolved、next_retry_at 清空。
        继承作废后这次处理并没有完成，重试资格得按原样留着。
        """
        first = self.link_drop()
        self.cards.analyze_drop(first['id'])
        second = self.link_drop()
        self.cards._defer_extraction(self.intake.detail(second['id']), 'synthetic', 'run-previous',
                                     OSError('合成提炼失败'))
        with self.cards.db() as connection:
            connection.execute('UPDATE extraction_retry SET next_retry_at=? WHERE drop_id=?',
                               (time.time() - 1, second['id']))          # 退避已到期，本轮该重取
        before = [(row['state'], row['next_retry_at'], row['attempts']) for row in self.retry_rows(second['id'])]
        self.assertEqual([row[0] for row in before], ['waiting'], '前置：这条正处在等待重试')
        self.assertIn(second['id'], self.ready_ids(model='synthetic'), '前置：退避已到期，定时该取它')

        self.break_inherit_with_new_body()

        def failing_read(*args, **kwargs):
            raise OSError('合成阅读器暂时失败，可重试')

        self.cards.call_model = failing_read
        state = self.cards.analyze_drop(second['id'])
        self.assertEqual(state['status'], 'failed', '%r' % state)

        after = [(row['state'], row['next_retry_at'], row['attempts']) for row in self.retry_rows(second['id'])]
        self.assertEqual(after, before,
                         '继承作废不得把这次没做成的重试记录放行成 resolved')
        self.assertIn(second['id'], self.ready_ids(model='synthetic'), '重试资格要保留')

    def test_refused_inherit_restores_an_earlier_seen_row_instead_of_deleting_it(self):
        """撤销只针对本轮这次写入：之前已有的已读记录要原样写回，不能盲删。

        这条回收项之前已按旧版正文处理过（seen 记着 A）。本轮继承作废，mark_seen 已把这一行
        改写成当前版 B：撤销必须还原成原来那条 A，而不是把整行删掉 —— 否则连之前的处理记录也没了。
        """
        first = self.link_drop()
        self.cards.analyze_drop(first['id'])
        second = self.link_drop()
        old_version = self.drop_version(second['id'])
        with self.cards.db() as connection:
            connection.execute('INSERT INTO seen(drop_id,document_id,fingerprint,processed_at,document_hash)'
                               ' VALUES(?,?,?,?,?)',
                               (second['id'], second['document_id'], self.cards.fingerprint(second),
                                '2026-01-01T00:00:00', old_version))
        self.assertEqual(self.seen_row(second['id']).get('document_hash'), old_version,
                         '前置：这条已按旧版正文处理过')

        self.break_inherit_with_new_body()

        def failing_read(*args, **kwargs):
            raise OSError('合成阅读器暂时失败，可重试')

        self.cards.call_model = failing_read
        state = self.cards.analyze_drop(second['id'])
        self.assertEqual(state['status'], 'failed', '%r' % state)

        restored = self.seen_row(second['id'])
        self.assertEqual(restored.get('document_hash'), old_version,
                         '之前的已读记录要原样还原，不能盲删既有有效记录')
        self.assertEqual(restored.get('processed_at'), '2026-01-01T00:00:00', '还原的是原来那条记录')
        self.assertNotEqual(restored.get('document_hash'), self.drop_version(second['id']),
                            '不得把本轮没处理完的当前正文算成已处理')
        self.assertIn(second['id'], self.ready_ids(model='synthetic'), '正文换版后仍要回到定时队列')

    def test_successful_inherit_still_marks_seen_and_spends_no_extra_model_calls(self):
        """对照：同版本成功继承仍算处理完 —— 记 seen、不再进定时队列，也不多花模型调用。

        撤销只针对「作废的那次继承」；成功继承的记账不能因为这次修复被削弱。
        """
        first = self.link_drop()
        self.cards.analyze_drop(first['id'])
        second = self.link_drop()
        reads_before, calls_before = self.reads, len(self.calls)

        state = self.cards.analyze_drop(second['id'])

        self.assertEqual(state['inherited_from'], first['id'], '前置：同版本走继承')
        self.assertEqual(self.reads, reads_before, '成功继承不得多花模型调用')
        self.assertEqual(len(self.calls), calls_before, '成功继承不产生任何模型调用')
        seen = self.seen_row(second['id'])
        self.assertEqual(seen.get('fingerprint'), self.cards.fingerprint(second), '成功继承仍要记已处理')
        self.assertEqual(seen.get('document_hash'), self.drop_version(second['id']),
                         '已处理标记记的就是当前正文版本')
        self.assertNotIn(second['id'], self.ready_ids(model='synthetic'), '已处理完的项不该再进定时队列')

    def test_requeued_failure_backs_off_under_the_existing_retry_budget(self):
        """失败项重新入队后仍受现有退避与预算约束：定时那一轮取它一次，失败后退避不再重取。

        撤销 seen 只是把该拿回来的重试资格还回去，不是让它每轮都重跑：定时提炼失败一次后
        照旧写 extraction_retry（waiting + 半小时退避），下一轮不再取它，也不再调模型。
        """
        first = self.link_drop()
        self.cards.analyze_drop(first['id'])
        second = self.link_drop()
        self.break_inherit_with_new_body()

        def cards_fail(identity, prompt, system, cancel, config, deadline):
            if identity.endswith('-read'):
                return payload(READING_UPDATED)
            raise OSError('合成卡片提炼暂时失败')

        self.cards.call_model = cards_fail
        state = self.cards.analyze_drop(second['id'])
        self.assertEqual(state['cards_error'], '合成卡片提炼暂时失败', '前置：卡片这一段失败')
        self.assertIn(second['id'], self.ready_ids(model='synthetic'), '前置：已回到定时队列')

        status, _, payload_text = self.cards.analyze('run-synthetic', {'title': '合成兜底'}, threading.Event(),
                                                    {'model': 'synthetic', 'timeout_seconds': 100},
                                                    time.monotonic() + 120)
        data = json.loads(payload_text)
        self.assertEqual(data['checked'], 0, '这一轮没提炼成卡片')
        self.assertTrue(any(second['id'][:8] in error for error in data['errors']),
                        '这一轮确实取到了这条失败项：%s' % data['errors'])

        rows = self.retry_rows(second['id'])
        self.assertEqual(len(rows), 1, '失败按现有机制记一条重试')
        self.assertEqual(rows[0]['state'], 'waiting')
        self.assertEqual(rows[0]['attempts'], 1)
        self.assertGreater(rows[0]['next_retry_at'], time.time(), '退避窗口内不再重取')
        self.assertNotIn(second['id'], self.ready_ids(model='synthetic'), '退避期间定时不再取它')

        calls_before = len(self.calls)
        self.cards.analyze('run-synthetic-2', {'title': '合成兜底'}, threading.Event(),
                           {'model': 'synthetic', 'timeout_seconds': 100}, time.monotonic() + 120)
        self.assertEqual(len(self.calls), calls_before, '退避期间这一轮不调模型')
        self.assertEqual(status in ('partial', 'succeeded'), True, '定时任务本身正常收尾')

    # ── 返修 c L2：末次复查之后、写盘实参求值之前的换版 ──────────────────────
    def test_inherit_write_after_final_recheck_still_binds_the_inherited_version(self):
        """末次复查返回 true 之后、`_write_analysis` 实参求值之前正文又换版：行里仍只能记被继承那一版。

        上一轮报告把「继承 done 实参改成当前版本」当成等价变异，理由是它与末次复查取的是同一时刻的
        值。那个表达式本身要再读一次知识库，与复查不在同一原子时刻：把换版塞在复查返回之后、实参
        求值之前，两种写法结果不同（旧摘要会被冒充成新正文的分析并传播给第三项）。
        这条就是复核独立探针的常驻版，普通方法包装钩子，不靠 sleep、不靠行追踪。
        """
        first = self.link_drop()
        self.cards.analyze_drop(first['id'])
        inherited_version = self.stored_version(first['id'])
        second = self.link_drop()
        check = self.cards._body_unchanged
        hits = []

        def check_then_update(drop, version):
            result = check(drop, version)
            hits.append(result)
            if len(hits) == 2 and result:
                self.update_body(drop['document_id'], UPDATED)   # 末次复查之后、写盘实参求值之前
            return result

        self.cards._body_unchanged = check_then_update
        self.addCleanup(setattr, self.cards, '_body_unchanged', check)
        calls_before = len(self.calls)
        state = self.cards.analyze_drop(second['id'])
        current = self.drop_version(second['id'])

        self.assertEqual(hits, [True, True], '前置：换版发生在继承的末次复查返回 true 之后')
        self.assertNotEqual(current, inherited_version, '前置：此刻正文已是另一版')
        self.assertEqual(state['status'], 'done', '%r' % state)
        self.assertEqual(state['summary'], READING['summary'], '沿用的是旧正文的结果')
        self.assertEqual(self.stored_version(second['id']), inherited_version,
                         '继承行只能记被继承那条分析对应的正文版本，不能记实参求值那一刻的最新版')
        self.assertEqual(len(self.calls), calls_before, '继承路径本身不多花模型调用')

        third = self.link_drop()
        self.assertEqual(self.projection(third['id']), {},
                         '第三项不得把这份旧正文的摘要当成当前正文的分析沿用')
        reads_before = self.reads
        third_state = self.cards.analyze_drop(third['id'])
        self.assertGreater(self.reads, reads_before, '第三项要重新读当前正文')
        self.assertEqual(third_state['summary'], READING_UPDATED['summary'])

    # ── 红：正文更新后不得复用旧分析 ─────────────────────────────────────────
    def test_updated_body_is_not_reused_from_shared_analysis(self):
        first = self.link_drop()
        self.cards.analyze_drop(first['id'])
        self.assertEqual(self.reads, 1, '前置：父回收项走了一次模型阅读')
        document_id = first['document_id']

        second = self.link_drop()
        self.assertEqual(second['document_id'], document_id, '两条回收项共用同一篇正文')
        self.update_body(document_id, UPDATED)
        reads_before = self.reads

        state = self.cards.analyze_drop(second['id'])

        self.assertEqual(state['status'], 'done', '更新后的正文应重新分析成功：%r' % state)
        self.assertGreater(self.reads, reads_before,
                           '正文版本已变，不得复用旧分析（必须重新走模型）')
        self.assertEqual(state['summary'], READING_UPDATED['summary'], '结果必须来自更新后的正文')
        self.assertNotEqual(state.get('inherited_from'), first['id'], '不得标成沿用旧分析')

    def test_legacy_row_without_version_is_not_reused_and_does_not_raise(self):
        first = self.link_drop()
        self.cards.analyze_drop(first['id'])
        # 模拟旧记录：分析行里没有正文版本字段。
        with self.cards.db() as connection:
            row = connection.execute('SELECT data FROM analysis WHERE drop_id=?', (first['id'],)).fetchone()
            data = json.loads(row['data'])
            data.pop('document_hash', None)
            connection.execute('UPDATE analysis SET data=? WHERE drop_id=?',
                               (json.dumps(data, ensure_ascii=False), first['id']))
        self.assertEqual(self.stored_version(first['id']), '', '前置：旧记录没有版本字段')
        reads_before = self.reads

        second = self.link_drop()
        state = self.cards.analyze_drop(second['id'])

        self.assertEqual(state['status'], 'done', '无版本记录不得让分析报错：%r' % state)
        self.assertGreater(self.reads, reads_before, '没有版本信息就不能复用，要重新分析')

    # ── 绿：版本一致时正常复用 ───────────────────────────────────────────────
    def test_same_version_reuse_unchanged(self):
        """对照：正文没动时照旧复用，且不多花模型调用（基座与修复后都必须绿）。"""
        first = self.link_drop()
        self.cards.analyze_drop(first['id'])
        reads_before = self.reads

        second = self.link_drop()          # 正文没动
        state = self.cards.analyze_drop(second['id'])

        self.assertEqual(state['status'], 'done')
        self.assertEqual(state['summary'], READING['summary'])
        self.assertEqual(state['inherited_from'], first['id'])
        self.assertEqual(self.reads, reads_before, '版本一致时复用不该再花模型调用')

    def test_same_version_reuse_records_the_version(self):
        """复用时也要记下当时复用的正文版本，否则继承出来的行永远无法再被复用。"""
        first = self.link_drop()
        self.cards.analyze_drop(first['id'])

        second = self.link_drop()
        self.cards.analyze_drop(second['id'])

        self.assertEqual(self.stored_version(second['id']), self.drop_version(second['id']),
                         '复用时也要记下当时复用的正文版本')
        self.assertEqual(self.stored_version(first['id']), self.drop_version(first['id']),
                         '正常分析完成的行要记下当时正文的版本')

    # ── 列表页投影：只投影同一版正文的分析 ───────────────────────────────────
    def test_projection_shows_shared_analysis_for_the_same_body(self):
        """对照：正文一致时，列表页照旧把已有分析投影给还没有自己分析行的回收项。"""
        first = self.link_drop()
        self.cards.analyze_drop(first['id'])

        second = self.link_drop()          # 自己还没有分析行
        state = self.projection(second['id'])

        self.assertEqual(state.get('inherited_from'), first['id'],
                         '正文一致时列表页照旧投影已有分析')
        self.assertEqual(state.get('summary'), READING['summary'])

    def test_projection_hides_analysis_from_an_older_body(self):
        """正文更新后，列表页不得把旧正文的分析投影过来。

        这条与上面那条成对：上面那条证明投影分支是活的（基座红），这条才是 R12 的要求
        （版本不一致 → 不投影）；两条一起看才不是「分支恒空所以碰巧通过」。
        """
        first = self.link_drop()
        self.cards.analyze_drop(first['id'])
        self.update_body(first['document_id'], UPDATED)

        second = self.link_drop()          # 自己还没有分析行
        self.assertEqual(self.projection(second['id']), {},
                         '正文已更新，列表页不得把旧正文的分析投影过来')

    def test_list_payload_shows_the_inherit_note_only_for_the_same_body(self):
        """用户路径：回收站列表返回的 analysis 字段（public/intake.js 据此显示「沿用已有分析」）。

        同一版正文 → 列表带 inherited_from（前端才显示沿用说明）；正文更新过 → 列表不带，
        不会出现「文章更新了还写着沿用旧分析」。
        """
        first = self.link_drop()
        self.cards.analyze_drop(first['id'])

        same_body = self.link_drop()
        items = {item['id']: item for item in self.intake.listing(50)['items']}
        self.assertEqual(items[same_body['id']]['analysis'].get('inherited_from'), first['id'])

        self.update_body(first['document_id'], UPDATED)
        updated_body = self.link_drop()
        items = {item['id']: item for item in self.intake.listing(50)['items']}
        self.assertNotIn('inherited_from', items[updated_body['id']]['analysis'],
                         '正文更新后列表不得再标沿用旧分析')

    # ── 分12 返修 M1：分析/继承**过程中**正文 A→B 的两条时序 ─────────────────
    def test_body_changed_during_model_read_is_recorded_as_the_version_actually_read(self):
        """正常分析进行中正文 A→B：done 只能记**实际送给模型那一版**，不能记落盘时的新版。

        分析是后台任务，读证据与写盘之间正文可能已被更新。在写盘点重新取当前 hash，等于把
        旧正文的分析盖成新正文的分析：列表投影会把旧摘要当新正文的沿用说明显示，后续回收项
        也会直接继承这份旧结果、不再读当前正文。
        """
        first = self.link_drop()
        read_version = self.drop_version(first['id'])
        document_id = first['document_id']
        prompts = []
        original = self.cards.call_model

        def read_then_update(identity, prompt, system, cancel, config, deadline):
            if identity.endswith('-read'):
                prompts.append(prompt)
                self.update_body(document_id, UPDATED)      # 模型读旧正文期间，正文换成 B
            return original(identity, prompt, system, cancel, config, deadline)

        self.cards.call_model = read_then_update
        state = self.cards.analyze_drop(first['id'])

        self.assertEqual(state['status'], 'done', '分析本身要正常完成：%r' % state)
        self.assertEqual(len(prompts), 1, '前置：这次分析读了一次正文')
        self.assertIn(SOURCE, prompts[0], '前置：送给模型的是更新前的正文 A')
        self.assertEqual(state['summary'], READING['summary'], '结果来自实际读到的那份正文')
        self.assertNotEqual(self.drop_version(first['id']), read_version, '前置：此刻正文已是另一版')
        self.assertEqual(self.stored_version(first['id']), read_version,
                         'done 要记实际送给模型的那版正文，不得在落盘时改取最新版本')

        second = self.link_drop()
        self.assertEqual(self.projection(second['id']), {},
                         '正文已更新，列表页不得把分析进行中那份旧正文的结果投影过来')
        reads_before = self.reads
        second_state = self.cards.analyze_drop(second['id'])

        self.assertGreater(self.reads, reads_before, '新回收项不得复用旧正文的分析，要重新读当前正文')
        self.assertEqual(second_state['status'], 'done', '重新分析要正常完成：%r' % second_state)
        self.assertEqual(second_state['summary'], READING_UPDATED['summary'], '结果必须来自更新后的正文')
        self.assertNotEqual(second_state.get('inherited_from'), first['id'], '不得标成沿用旧分析')

    def test_body_changed_after_inherit_match_refuses_the_inherit(self):
        """继承匹配成功之后、写 done 之前正文 A→B：这次继承作废，不得把旧结果标成新版本。

        旧做法是继承照写 done、版本取落盘那一刻的正文：这条继承行就冒充当前正文的分析，
        列表投影与后续继承会把旧摘要一路传播下去。作废后走现有正常分析路径，按当前正文重读。
        """
        first = self.link_drop()
        self.cards.analyze_drop(first['id'])
        self.assertEqual(self.stored_version(first['id']), self.drop_version(first['id']),
                         '前置：父行记着正文 A 的版本')

        second = self.link_drop()
        original_mark_seen = self.cards.mark_seen
        hook_hits = []

        def mark_seen_after_update(drop, fingerprint, *args, **kwargs):
            hook_hits.append(drop['id'])
            self.update_body(drop['document_id'], UPDATED)   # 匹配之后、落盘之前正文换成 B
            return original_mark_seen(drop, fingerprint, *args, **kwargs)

        self.cards.mark_seen = mark_seen_after_update
        reads_before = self.reads
        state = self.cards.analyze_drop(second['id'])
        self.cards.mark_seen = original_mark_seen

        self.assertTrue(hook_hits, '前置：正文更新发生在继承匹配之后、落盘之前')
        self.assertEqual(state['status'], 'done', '继承作废后要按当前正文重新分析成功：%r' % state)
        self.assertNotEqual(state.get('inherited_from'), first['id'], '不得写成沿用旧分析')
        self.assertEqual(state['summary'], READING_UPDATED['summary'], '结果必须来自更新后的正文')
        self.assertGreater(self.reads, reads_before, '继承作废要重新走一次模型读当前正文')
        self.assertEqual(self.stored_version(second['id']), self.drop_version(second['id']),
                         '重新分析记的就是当前正文的版本')

        third = self.link_drop()
        projected = self.projection(third['id'])
        self.assertNotEqual(projected.get('summary'), READING['summary'],
                            '列表不得把旧正文的摘要当成当前正文的沿用分析传播下去')

    def test_inherit_write_never_stamps_the_version_current_at_write_time(self):
        """继承复查都过了、真正写盘那一刻正文又换了：行里也只能记被继承那条的版本。

        这条把「版本来源」钉在写盘点本身，不依赖复查时机：复查与写盘之间再窄的窗口里
        正文变了，也只能是旧版分析标旧版（不匹配 → 不复用），不能反过来把旧结果标成新版本。
        """
        first = self.link_drop()
        self.cards.analyze_drop(first['id'])
        parent_version = self.stored_version(first['id'])

        second = self.link_drop()
        original_write = self.cards._write_analysis
        fired = []

        def write_after_update(drop, status, **fields):
            if status == 'done' and not fired:
                fired.append(drop['id'])
                self.update_body(drop['document_id'], UPDATED)   # 复查之后、落盘之前正文又换一版
            return original_write(drop, status, **fields)

        self.cards._write_analysis = write_after_update
        state = self.cards.analyze_drop(second['id'])
        self.cards._write_analysis = original_write

        self.assertEqual(fired, [second['id']], '前置：正文更新就发生在继承落盘那一刻')
        self.assertEqual(state['status'], 'done', '%r' % state)
        self.assertEqual(self.stored_version(second['id']), parent_version,
                         '继承行只能记被继承那条分析对应的正文版本，不能记落盘时的最新版')

        third = self.link_drop()
        self.assertEqual(self.projection(third['id']), {},
                         '正文已是另一版，列表不得把这份旧正文的继承结果投影过来')
        reads_before = self.reads
        third_state = self.cards.analyze_drop(third['id'])
        self.assertGreater(self.reads, reads_before, '第三项不得复用这份旧正文的继承结果')
        self.assertEqual(third_state['summary'], READING_UPDATED['summary'], '结果必须来自当前正文')

    def test_list_projection_reads_body_versions_once_per_page(self):
        """L1：列表页按去重 document_id 一次批量读正文版本，投影结果与逐行读一致。

        同一 document_id 的重复行不得每行进一次知识库（上一版 157 行列表要进 154 次）；
        读不到版本的那条仍按「无版本」处理，不影响同页其他条目。
        """
        first = self.link_drop()
        self.cards.analyze_drop(first['id'])
        same = [self.link_drop() for _ in range(3)]      # 同一版正文，应沿用

        reads = []
        original = self.store.knowledge.document_hashes

        def counted(ids):
            reads.append(set(ids))
            return original(ids)

        rows = [self.intake.detail(d['id']) for d in [first] + same]
        self.store.knowledge.document_hashes = counted
        try:
            states = self.cards.analysis_states(rows)
        finally:
            self.store.knowledge.document_hashes = original
        self.update_body(first['document_id'], UPDATED)
        stale = self.link_drop()                          # 正文已换版，不得沿用
        stale_rows = [self.intake.detail(stale['id'])]
        self.store.knowledge.document_hashes = counted
        try:
            stale_states = self.cards.analysis_states(stale_rows)
        finally:
            self.store.knowledge.document_hashes = original

        self.assertEqual(len(reads), 2, '两次列表各读一次正文版本（按去重 document_id 批量，不按行）')
        self.assertEqual(reads[0], {first['document_id']}, '只查这一页涉及的 document_id，不重复、不多查')
        for drop in same:
            self.assertEqual(states[drop['id']].get('inherited_from'), first['id'],
                             '同版正文的重复行照旧沿用同一条分析')
            self.assertEqual(states[drop['id']].get('summary'), READING['summary'])
        self.assertEqual(stale_states[stale['id']], {}, '正文换版后不得沿用')
        self.assertEqual(states[first['id']].get('summary'), READING['summary'], '自己的分析照常返回')

    def test_note_on_a_done_analysis_keeps_the_version_it_was_made_for(self):
        """给已完成的分析补注记：不得顺手把版本重盖成此刻的最新正文。

        补注记写的还是同一份分析结果（只是多一条 parse_notes），版本必须跟着原样保留；
        否则正文更新过一次之后，旧分析又会重新看起来像当前正文的分析。
        """
        first = self.link_drop()
        self.cards.analyze_drop(first['id'])
        read_version = self.stored_version(first['id'])
        self.assertEqual(read_version, self.drop_version(first['id']), '前置：记的是当时那版正文')

        self.update_body(first['document_id'], UPDATED)
        self.cards._note_analysis(first['id'], '识别超时，已按理由/链接/文件名分析')

        self.assertEqual(self.stored_version(first['id']), read_version,
                         '补注记不得把旧分析重新标成最新正文版本')
        self.assertIn('识别超时，已按理由/链接/文件名分析',
                      self.projection(first['id']).get('parse_notes') or [])


if __name__ == '__main__':
    unittest.main(verbosity=2)
