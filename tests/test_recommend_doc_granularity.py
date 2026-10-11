"""推荐来源的文档粒度：同一 URL 在知识库有两份文档，删掉一份要只清那一行。

背景（coordination/reverify-perf-knowledge-report.md §6，23d 复验时发现的既有残留）：

`recommendation_sources` 的主键是 `(source_kind, source_id, link)`，其中 knowledge 来源的
`source_id` 是**文档 id**。但清理只按 URL 判 dead（`forget_intake_source` /
`reconcile_links`），于是：

- 同一 URL 有两份 knowledge 文档时，删掉其中一份 → 那个 URL 还被另一份文档引用着，
  不算 dead → 一行都不清 → 被删文档的那条来源行残留，推荐次数多算 1。

修法：删除知识库文档时按 `(source_kind='knowledge', source_id=<文档 id>)` 精确删那一行，
再走 URL 级对账决定 link_index 与推荐行 —— 这样既清了残留，又保住了「另一份文档仍
引用同 URL 时不删 link_index/推荐」的语义。

全程离线：假抓取、假模型、临时 workspace，不联网、不碰正式库。
"""
import hashlib
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import intel_recommend
from intake_store import Intake
from knowledge_store import Knowledge
from server import Store, wire_intake_links
from tests.intake_dependency_patches import guard_intake_dependencies, patch_intake_dependency

# 合法 __biz 的公众号链接：collect() 能免费解析出号，不联网。
WECHAT_LINK = 'https://mp.weixin.qq.com/s?__biz=MzA5Mg%3D%3D&mid=264&idx=1&sn=abc123'
# 另一个号（__biz 不同 → fakeid 不同），用来验「别牵连别的链接的痕迹」
OTHER_LINK = 'https://mp.weixin.qq.com/s?__biz=MzI3NjQ4OTk0MA%3D%3D&mid=999&idx=1&sn=zzz999'

FAKE_BODY = '黄昏的旧码头先把木头泡软，绳索在风里轻轻晃，远处渡船只留一条灯线。'


class FakeWeb:
    markdown = FAKE_BODY

    def __call__(self):
        return self

    def fetch(self, args, job):
        return {'url': args['url'], 'title': '合成页面标题', 'markdown': self.markdown,
                'start': 0, 'total_chars': len(self.markdown), 'truncated': False,
                'next_start': None}


def doc_id(seed):
    return hashlib.sha256(seed.encode()).hexdigest()[:32]


class DocGranularityFixture(unittest.TestCase):
    def setUp(self):
        guard_intake_dependencies(self)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(self.tmp.name)
        self.store._knowledge = Knowledge(self.store, sources=[])
        self.intake = Intake(self.store.knowledge, self.tmp.name)
        self.store._creative_intake = self.intake
        wire_intake_links(self.store)
        patch_intake_dependency(self, "WebTools", FakeWeb())
        # 让 intel.db 真的建出来：_IntakeLedger._ready() 看的就是这个文件在不在
        with self.store.intel.db() as connection:
            connection.execute('SELECT 1')
        self.counter = iter(range(1, 500))
        self.scans = []
        real = intel_recommend.Recommender._document_links

        def counting(path):
            self.scans.append(str(path))
            return real(path)
        patcher = patch.object(intel_recommend.Recommender, '_document_links', staticmethod(counting))
        patcher.start()
        self.addCleanup(patcher.stop)

    # ── 造数据 ────────────────────────────────────────────────────────────
    def knowledge_doc(self, url, title='独立收藏的一篇'):
        """直接往知识库写一份文档（不是回收站收集时顺带产生的那份）。"""
        item_id = doc_id(title + url)
        with self.store.knowledge.db() as connection:
            connection.execute(
                'INSERT INTO documents(id,source,kind,title,body,url,modified,tags,hash,provenance)'
                ' VALUES(?,?,?,?,?,?,?,?,?,?)',
                (item_id, 'wechat-links', 'wechat_links', title, FAKE_BODY, url,
                 '2026-10-10', '[]', 'h-' + item_id[:8], '{}'))
        return item_id

    def intake_link(self, url=WECHAT_LINK):
        saved = self.intake.receive({'id': format(next(self.counter), '032x'),
                                     'kind': 'link', 'url': url})
        return self.intake.resolve(saved['id'])

    def intake_note(self, text='一个纯文字想法。'):
        saved = self.intake.receive({'id': format(next(self.counter), '032x'),
                                     'kind': 'text', 'text': text})
        return self.intake.resolve(saved['id'])

    # ── 观测 ──────────────────────────────────────────────────────────────
    def ledger(self):
        with self.store.intel.db() as connection:
            def count(sql, args=()):
                return connection.execute(sql, args).fetchone()[0]
            return {
                'intake': count("SELECT COUNT(*) FROM recommendation_sources"
                                " WHERE source_kind='intake'"),
                'knowledge': count("SELECT COUNT(*) FROM recommendation_sources"
                                   " WHERE source_kind='knowledge'"),
                'links': count('SELECT COUNT(*) FROM link_index'),
                'recs': count('SELECT COUNT(*) FROM recommendations'),
                'count': count('SELECT COALESCE(SUM(count),0) FROM recommendations'),
            }

    def intake_doc_id(self, url=WECHAT_LINK):
        """回收站收集时建的那份知识文档 id（intake 那条回收项的 document_id）。"""
        with self.intake.db() as connection:
            row = connection.execute('SELECT document_id FROM drops'
                                     ' WHERE url=? ORDER BY rowid LIMIT 1',
                                     (url,)).fetchone()
        return row['document_id']

    def knowledge_source_ids(self, url=WECHAT_LINK):
        with self.store.intel.db() as connection:
            return sorted(row[0] for row in connection.execute(
                "SELECT source_id FROM recommendation_sources"
                " WHERE source_kind='knowledge' AND link=?", (url,)))


class DeletedDocumentResidueTest(DocGranularityFixture):
    """合同 1(a)：同 URL 两份 knowledge 文档删一份 → 只清那一行。"""

    def test_removing_one_of_two_knowledge_documents_drops_only_its_source_row(self):
        first = self.knowledge_doc(WECHAT_LINK, '知识库收藏 A')
        second = self.knowledge_doc(WECHAT_LINK, '知识库收藏 B')
        drop = self.intake_link()               # 这条收集时也会在知识库建一份正文 C
        self.store.intel_recommend.collect()

        # 前置：A、B 两份独立收藏 + 回收站收集产生的 C，共三条知识来源
        collected = self.intake_doc_id()
        before = self.ledger()
        self.assertEqual(before['knowledge'], 3, '三份知识文档应有三条来源：%r' % before)
        self.assertEqual(before['intake'], 1)
        self.assertEqual(before['count'], 4)
        self.assertEqual(self.knowledge_source_ids(), sorted([first, second, collected]))

        result = self.intake.delete(drop['id'])
        self.assertTrue(result['document_removed'], '回收站那份正文应被删')

        after = self.ledger()
        # 被删文档 C 的来源行必须消失，只剩没被删的 A、B
        self.assertEqual(self.knowledge_source_ids(), sorted([first, second]),
                         '被删文档的来源行要清掉')
        # 但 URL 还被 A、B 引用着 → link_index 与推荐行必须保留
        self.assertEqual(after['knowledge'], 2, '知识来源应只剩两条：%r' % after)
        self.assertEqual(after['links'], 1, 'URL 仍存在，link_index 不该被删：%r' % after)
        self.assertEqual(after['recs'], 1, '还有来源，推荐行不该被删：%r' % after)
        # 剩下的来源就是 A、B 两条知识来源 → 次数 2
        self.assertEqual(after['count'], 2, '次数应按剩下的来源重算：%r' % after)
        self.assertEqual(after['intake'], 0)


class UrlFullyGoneTest(DocGranularityFixture):
    """合同 1(b)：删到 URL 彻底消失 → 全部清到真实状态（与 23d M1 一致）。"""

    def test_removing_the_last_document_clears_everything(self):
        keep = self.knowledge_doc(WECHAT_LINK, '知识库收藏 A')
        drop = self.intake_link()          # 收集时也会建一份 B
        self.store.intel_recommend.collect()
        collected = self.intake_doc_id()
        before = self.ledger()
        self.assertEqual((before['knowledge'], before['intake'], before['count']), (2, 1, 3), before)

        self.intake.delete(drop['id'])

        after = self.ledger()
        # 回收站那份（B）删了，但 A 还在 → URL 仍存在
        self.assertEqual(after['knowledge'], 1, 'A 还在，来源必须保留：%r' % after)
        self.assertEqual(self.knowledge_source_ids(), [keep])
        self.assertEqual(after['links'], 1)
        self.assertEqual(after['count'], 1)
        self.assertEqual(collected not in self.knowledge_source_ids(), True,
                         '被删的 B 不该留下来源行')

        # 再把 A 删掉。这一步走知识库自身的删除入口（不经回收站），
        # 只负责按文档精确清来源行；「URL 是否彻底消失」是 URL 级的事，
        # 由对账入口决定 —— 所以这里显式调一次 reconcile_links（批末 finalize 调的就是它）。
        removed = self.store.knowledge.delete_document(keep)
        self.assertTrue(removed['removed'])
        self.assertEqual(self.ledger()['knowledge'], 0,
                         '删掉 A 它的来源行就该清掉：%r' % self.ledger())
        self.store.intel_recommend.reconcile_links([WECHAT_LINK])

        final = self.ledger()
        self.assertEqual(final['knowledge'], 0, 'URL 彻底消失后知识来源要清掉：%r' % final)
        self.assertEqual(final['links'], 0)
        self.assertEqual(final['recs'], 0)
        self.assertEqual(final['count'], 0)


class MixedIntakeAndKnowledgeTest(DocGranularityFixture):
    """合同 1(c)：intake + knowledge 混合。"""

    def test_mixed_sources_shrink_by_exactly_one(self):
        doc = self.knowledge_doc(WECHAT_LINK, '知识库收藏 A')
        drop = self.intake_link()          # 收集时也会建一份 B
        self.store.intel_recommend.collect()
        before = self.ledger()
        self.assertEqual((before['intake'], before['knowledge'], before['count']), (1, 2, 3), before)

        self.intake.delete(drop['id'])

        after = self.ledger()
        self.assertEqual(after['intake'], 0, 'intake 来源清掉：%r' % after)
        self.assertEqual(after['knowledge'], 1, '只剩 A：%r' % after)
        self.assertEqual(after['links'], 1, '知识库还引用着，链接保留：%r' % after)
        self.assertEqual(after['count'], 1, '次数按真实来源归到 1：%r' % after)
        self.assertEqual(self.knowledge_source_ids(), [doc])

    def test_two_known_ids_sharing_a_url_each_lose_only_their_own_row(self):
        a = self.knowledge_doc(WECHAT_LINK, '知识库收藏 A')
        b = self.knowledge_doc(WECHAT_LINK, '知识库收藏 B')
        self.store.intel_recommend.collect()
        self.assertEqual(self.ledger()['knowledge'], 2)
        self.assertEqual(self.knowledge_source_ids(), sorted([a, b]))

        # 逐份删知识库文档，每次只能少一行，且 URL 还在时 link_index 不能动
        self.store.knowledge.delete_document(a)
        self.store.intel_recommend.collect()
        mid = self.ledger()
        self.assertEqual(mid['knowledge'], 1, '只该少一行：%r' % mid)
        self.assertEqual(self.knowledge_source_ids(), [b])
        self.assertEqual(mid['links'], 1, 'B 还在引用，link_index 保留：%r' % mid)
        self.assertEqual(mid['count'], 1)

        self.store.knowledge.delete_document(b)
        self.assertEqual(self.ledger()['knowledge'], 0, 'B 的来源行也要清掉')
        self.store.intel_recommend.reconcile_links([WECHAT_LINK])
        final = self.ledger()
        self.assertEqual(final['knowledge'], 0, '%r' % final)
        self.assertEqual(final['links'], 0, 'URL 彻底消失，link_index 才清：%r' % final)
        self.assertEqual(final['recs'], 0)
        self.assertEqual(final['count'], 0)


class ScanBudgetTest(DocGranularityFixture):
    """合同 2：文档粒度的清理不得增加知识库扫描次数。"""

    def test_batch_delete_keeps_the_two_scan_ceiling(self):
        drops = [self.intake_link() for _ in range(2)]
        self.store.intel_recommend.collect()
        self.scans.clear()
        self.intake.delete_many([d['id'] for d in drops])
        self.assertLessEqual(len(self.scans), 2,
                             '每批知识库扫描不得超过 2 次，实测 %d 次' % len(self.scans))

    def test_text_only_batch_still_scans_once(self):
        notes = [self.intake_note('纯文字 %d。' % i) for i in range(3)]
        self.scans.clear()
        self.intake.delete_many([n['id'] for n in notes])
        self.assertEqual(len(self.scans), 1,
                         '纯文字批仍应只有 1 次扫描，实测 %d 次' % len(self.scans))

    def test_single_delete_scans_once(self):
        drop = self.intake_link()
        self.store.intel_recommend.collect()
        self.scans.clear()
        self.intake.delete(drop['id'])
        self.assertLessEqual(len(self.scans), 1,
                             '单条删除最多 1 次扫描，实测 %d 次' % len(self.scans))


class NoIntelDatabaseTest(DocGranularityFixture):
    """没跑过情报侧的 workspace：删文档不该凭空把 intel.db 建出来。

    `_IntakeLedger._ready` 早就守这条口径（`tests/test_intake_delete.py` 有对应用例），
    新加的文档钩子必须守同一条，否则「删个回收项」会凭空多一个情报库。
    """

    def test_deleting_a_document_does_not_create_the_intel_database(self):
        # 把 intel.db 删掉，模拟从没跑过情报采集的 workspace
        for suffix in ('', '-wal', '-shm'):
            path = Path(self.store.workspace) / 'intel' / ('intel.db' + suffix)
            if path.exists():
                path.unlink()
        doc = self.knowledge_doc(WECHAT_LINK, '知识库收藏 A')
        removed = self.store.knowledge.delete_document(doc)
        self.assertTrue(removed['removed'])
        self.assertFalse((Path(self.store.workspace) / 'intel' / 'intel.db').exists(),
                         '钩子不该在情报侧缺席时建库')


class HookFailureIsReportedTest(DocGranularityFixture):
    """合同 M1：情报侧清理钩子抛错时，删除仍成功，但错误必须看得见。

    `knowledge_store.delete_document` 把钩子异常收进 `hook_error`（正文已删，不推翻
    删除结果）。回收项路径 `Intake.delete` / `delete_many` 以前没读这个字段，异常在
    这一层被静默吞掉：上层拿到一份「一切正常」的回执，而情报侧实际留着一条孤儿来源行。

    这类错误不能靠 URL 级对账兜底 —— 共享 URL 还活着时对账不会动那些行。
    """

    def broken_hook(self, message='synthetic-hook-failure'):
        """把文档钩子换成必抛异常的版本，返回装好的异常类型。"""
        def boom(document_id):
            raise RuntimeError(message)
        self.store.knowledge.document_removed_hook = boom

    def assert_does_not_claim_clean(self, receipt):
        """回执不许谎报成「推荐来源已清干净」。

        判据是「有没有说自己清干净了」，不是「有没有出现某个词」——
        错误文案本身就该说清楚是情报侧出的问题，所以只查断言式措辞。
        """
        claims = ('已清', '已清干净', '已清理', '清理完成', '痕迹已清', '无残留')
        # 批删回执的错误在 deleted[] 的逐条回执里，单删回执就是它自己。
        receipts = receipt['deleted'] if isinstance(receipt.get('deleted'), list) else [receipt]
        for one in receipts:
            blob = ' '.join(str(v) for v in one.values() if isinstance(v, str))
            for word in claims:
                self.assertNotIn(word, blob,
                                 '回执出现断言式措辞 %r，可能在谎报已清干净：%r' % (word, one))

    def test_single_delete_surfaces_hook_error(self):
        keep = self.knowledge_doc(WECHAT_LINK, '知识库收藏 A')
        drop = self.intake_link()             # 收集时另建一份 B
        self.store.intel_recommend.collect()
        self.assertEqual(self.ledger()['knowledge'], 2, '前置：A+B 两条来源：%r' % self.ledger())

        self.broken_hook()
        receipt = self.intake.delete(drop['id'])

        # 1) 删除本身照常成功，不因为附带清理失败就回滚
        self.assertTrue(receipt['deleted'], '钩子失败不该把删除判成失败：%r' % receipt)
        self.assertTrue(receipt['document_removed'], '正文已经删了，如实报删成功：%r' % receipt)

        # 2) 错误必须看得见，且能定位到是钩子
        visible = ' '.join(str(receipt.get(k, '')) for k in
                           ('document_error', 'ledger_error', 'records_error', 'file_error'))
        self.assertIn('synthetic-hook-failure', visible,
                      '钩子异常必须在回执里看得见，实际回执：%r' % receipt)

        # 3) 不得声称情报侧痕迹已清干净
        self.assert_does_not_claim_clean(receipt)

        # 4) 实际残留如实可见：A 的来源行还在，B 的孤儿行也还在（这正是要说清的事）
        self.assertEqual(self.knowledge_source_ids(), sorted([keep, receipt['document_id']]),
                         '钩子失败 → B 的来源行会残留，A 不该受影响：%r' % self.ledger())

    def test_batch_delete_summary_surfaces_hook_error(self):
        keep = self.knowledge_doc(WECHAT_LINK, '知识库收藏 A')
        drops = [self.intake_link() for _ in range(2)]
        self.store.intel_recommend.collect()

        self.broken_hook()
        summary = self.intake.delete_many([d['id'] for d in drops])

        # 1) 逐条删除照常成功。
        #    两条回收项共享同一篇正文（引用计数），所以只有最后一条真删了正文 ——
        #    这是既有的正确行为，不是本次要改的。关键是**每条都 deleted**。
        self.assertEqual(summary['summary']['deleted'], 2, '两条都该删成功：%r' % summary['summary'])
        self.assertEqual(summary['summary']['skipped'], 0)
        self.assertTrue(all(r['deleted'] for r in summary['deleted']),
                        '两条回收项都该删成功：%r' % summary['deleted'])
        self.assertTrue(any(r['document_removed'] for r in summary['deleted']),
                        '最后一条应真删掉共享正文：%r' % summary['deleted'])

        # 2) 批末汇总必须看得见，且不能被批末 finalize 的错误覆盖掉
        blob = json.dumps(summary, ensure_ascii=False)
        self.assertIn('synthetic-hook-failure', blob,
                      '钩子异常必须在批删汇总里看得见：%r' % summary['summary'])
        self.assertGreater(summary['summary']['partial'], 0, '应计入部分失败：%r' % summary['summary'])
        self.assertTrue(summary['summary']['errors'], 'errors 不能是空列表：%r' % summary['summary'])

        # 3) 每条错误都要指得出是哪一条
        ids = {e['id'] for e in summary['summary']['errors']}
        self.assertTrue(ids, '错误条目要带回收项 id：%r' % summary['summary']['errors'])

        # 4) 不得声称情报侧痕迹已清干净
        self.assert_does_not_claim_clean(summary)

        # 5) 实际残留如实可见
        self.assertIn(keep, self.knowledge_source_ids(),
                      'A 的来源行不该被动：%r' % self.ledger())

    def test_hook_error_is_not_overwritten_by_later_ledger_error(self):
        """两个错误同时发生时都要在，且各自可辨（不得后者覆盖前者）。"""
        keep = self.knowledge_doc(WECHAT_LINK, '知识库收藏 A')
        drop = self.intake_link()
        self.store.intel_recommend.collect()

        self.broken_hook('HOOK-MARKER')
        # 让批末 finalize 抛另一个错
        original = self.store.intel_recommend.reconcile_links

        def ledger_boom(links):
            raise RuntimeError('LEDGER-MARKER')
        self.store.intel_recommend.reconcile_links = ledger_boom

        summary = self.intake.delete_many([drop['id']])
        self.store.intel_recommend.reconcile_links = original

        blob = json.dumps(summary, ensure_ascii=False)
        self.assertIn('HOOK-MARKER', blob, '钩子错不能被批末对账错覆盖掉：%r' % blob[:600])
        self.assertIn('LEDGER-MARKER', blob, '批末对账错也要在：%r' % blob[:600])
        self.assertTrue(summary['deleted'][0]['document_removed'])

    def test_hook_error_does_not_break_normal_path(self):
        """钩子正常时不产生任何多余错误字段（回归护栏）。"""
        drop = self.intake_link()
        self.store.intel_recommend.collect()
        receipt = self.intake.delete(drop['id'])
        for key in ('document_error', 'ledger_error', 'records_error', 'file_error'):
            self.assertEqual(receipt.get(key, ''), '', '%s 应为空：%r' % (key, receipt))

class UnrelatedLinkUntouchedTest(DocGranularityFixture):
    """回归护栏：只碰被删文档涉及的那条链接，别把别的号的痕迹清了。"""

    def test_other_links_are_not_touched(self):
        keep_a = self.knowledge_doc(WECHAT_LINK, '知识库收藏 A')
        keep_b = self.knowledge_doc(OTHER_LINK, '知识库收藏 B')
        drop = self.intake_link(WECHAT_LINK)
        self.store.intel_recommend.collect()
        with self.store.intel.db() as connection:
            accounts = connection.execute(
                'SELECT DISTINCT fakeid FROM recommendation_sources').fetchall()
        self.assertEqual(len(accounts), 2, '两个链接应属两个不同的号')

        self.intake.delete(drop['id'])

        with self.store.intel.db() as connection:
            other = connection.execute(
                'SELECT COUNT(*) FROM recommendation_sources WHERE link=?',
                (OTHER_LINK,)).fetchone()[0]
            other_links = connection.execute(
                'SELECT COUNT(*) FROM link_index WHERE link=?', (OTHER_LINK,)).fetchone()[0]
        self.assertEqual(other, 1, '另一条链接的来源不能被牵连：%r' % self.ledger())
        self.assertEqual(other_links, 1, '另一条链接的 link_index 不能被牵连')
        self.assertEqual(self.knowledge_source_ids(WECHAT_LINK), [keep_a])
        self.assertTrue(keep_b)


if __name__ == '__main__':
    unittest.main(verbosity=2)