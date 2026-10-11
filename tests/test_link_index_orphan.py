"""知识库直接删除文档时，link_index 不得留下孤儿行。

背景（`coordination/review-recommend-doc-granularity-report.md` §3 L1，Codex 提出）：

23e 给知识库删除入口接了 `document_removed_hook`，按 `(source_kind='knowledge',
source_id=<文档 id>)` 精确清来源行。但**只清来源行**：

- 回收项批删路径批末有 `reconcile_links` 做 URL 级对账，直接删除入口没有；
- 于是「删掉最后一个引用某 URL 的文档」时，`link_index` 里那行没人管，
  URL 已经没有任何真实引用却还留着 —— 就是孤儿行。

这份测试盯三件事（合同第 2 条）：

1. 直接删掉最后一个引用某 URL 的文档 → 该 URL 从 `link_index` 消失、推荐计数正确；
2. 同 URL 还有别的文档/回收项引用时 → **不得**被清；
3. 情报侧钩子抛错时 → 删除照样成功，且错误在回执里看得见。

全程离线：假抓取、临时 workspace，不联网、不碰正式库。
"""
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
    import hashlib
    return hashlib.sha256(seed.encode()).hexdigest()[:32]


class LinkIndexFixture(unittest.TestCase):
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
        # 让 intel.db 真的建出来：情报侧的 _ready()/守卫看的就是这个文件在不在
        with self.store.intel.db() as connection:
            connection.execute('SELECT 1')
        self.counter = iter(range(1, 500))
        # 数一数每次收尾做了几次知识库扫描（合同第 1 条：不做全库扫描）
        self.scans = []
        real = intel_recommend.Recommender._document_links

        def counting(path):
            self.scans.append(str(path))
            return real(path)
        patcher = patch.object(intel_recommend.Recommender, '_document_links', staticmethod(counting))
        patcher.start()
        self.addCleanup(patcher.stop)

    # ── 模拟迁移未装载 ────────────────────────────────────────────────────
    def drop_link_host(self):
        """把 link_host 生成列与索引删掉，模拟 23d 迁移未装载的库。

        测试库由 knowledge_store 的 CREATE TABLE 建出来，自带 link_host；
        真实库在迁移装载前没有这一列。VIRTUAL 生成列可以 DROP COLUMN。
        """
        with sqlite3.connect(self.store.knowledge.path) as connection:
            connection.execute('DROP INDEX IF EXISTS documents_link_host')
            connection.execute('ALTER TABLE documents DROP COLUMN link_host')
        self.assertFalse(self.has_link_host(), '降级后不该还有 link_host 列')

    def add_link_host(self):
        """补上 link_host 生成列 + 索引（迁移装载后的形态）。"""
        with sqlite3.connect(self.store.knowledge.path) as connection:
            # 必须用 table_xinfo：link_host 是 VIRTUAL 生成列，PRAGMA table_info 不列它
            exists = 'link_host' in {row[1] for row in connection.execute(
                'PRAGMA table_xinfo(documents)')}
            if not exists:
                connection.execute(
                    "ALTER TABLE documents ADD COLUMN link_host TEXT"
                    " GENERATED ALWAYS AS (CASE WHEN url LIKE '%mp.weixin.qq.com%'"
                    " THEN 'mp.weixin.qq.com' ELSE NULL END) VIRTUAL")
            connection.execute(
                'CREATE INDEX IF NOT EXISTS documents_link_host ON documents(link_host)')
        self.assertTrue(self.has_link_host(), '装载后应有 link_host 列')

    def has_link_host(self):
        with sqlite3.connect(f'file:{self.store.knowledge.path}?mode=ro', uri=True) as connection:
            return 'link_host' in {row[1] for row in connection.execute(
                'PRAGMA table_xinfo(documents)')}

    def knowledge_queries(self):
        """记录收尾期间对知识库发过的每一条 SQL。

        用 trace_callback 而不是数行数：合同要证的是「不触发全表 LIKE」，
        断言具体语句比断言耗时稳（机器负载一高耗时就不可信）。
        """
        seen = []
        original = intel_recommend.read_only

        def recording_read_only(path):
            connection = original(path)
            if str(path).endswith('knowledge.db'):
                connection.set_trace_callback(seen.append)
            return connection

        patcher = patch.object(intel_recommend, 'read_only', recording_read_only)
        patcher.start()
        self.addCleanup(patcher.stop)
        return seen

    # ── 造数据 ────────────────────────────────────────────────────────────
    def knowledge_doc(self, url, title='独立收藏的一篇'):
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

    def link_present(self, url=WECHAT_LINK):
        with self.store.intel.db() as connection:
            return bool(connection.execute(
                'SELECT 1 FROM link_index WHERE link=?', (url,)).fetchone())

    def orphans(self):
        """两库都不再引用的 link_index 行 —— 就是本任务要消灭的东西。"""
        with self.store.intel.db() as connection:
            indexed = [row[0] for row in connection.execute('SELECT link FROM link_index')]
        live = set(self.store.intel_recommend.live_links())
        return [link for link in indexed if link not in live]


class LastReferenceGoneTest(LinkIndexFixture):
    """合同第 2 条第 1 段：删掉最后一个引用某 URL 的文档 → link_index 该行消失。"""

    def test_deleting_the_last_document_removes_its_link_index_row(self):
        doc = self.knowledge_doc(WECHAT_LINK, '知识库收藏 A')
        self.store.intel_recommend.collect()
        before = self.ledger()
        self.assertEqual((before['knowledge'], before['links']), (1, 1), '前置：%r' % before)
        self.assertTrue(self.link_present(), '前置：link_index 应有该 URL')

        removed = self.store.knowledge.delete_document(doc)
        self.assertTrue(removed['removed'], '正文应被删掉')
        self.assertNotIn('hook_error', removed, '钩子不该报错：%r' % removed)

        after = self.ledger()
        self.assertFalse(self.link_present(),
                         'URL 已无任何真实引用，link_index 必须清掉：%r' % after)
        self.assertEqual(after['links'], 0, 'link_index 应归零：%r' % after)
        self.assertEqual(after['knowledge'], 0, '来源行应清掉：%r' % after)
        self.assertEqual(after['recs'], 0, '没来源了，推荐行该删：%r' % after)
        self.assertEqual(after['count'], 0)
        self.assertEqual(self.orphans(), [], '不该留下任何孤儿行')

    def test_other_links_survive_the_cleanup(self):
        keep_doc = self.knowledge_doc(OTHER_LINK, '知识库收藏 B')
        drop_doc = self.knowledge_doc(WECHAT_LINK, '知识库收藏 A')
        self.store.intel_recommend.collect()
        self.assertEqual(self.ledger()['links'], 2, '前置：两个 URL：%r' % self.ledger())

        self.store.knowledge.delete_document(drop_doc)

        self.assertFalse(self.link_present(WECHAT_LINK), '被删文档的 URL 该清掉')
        self.assertTrue(self.link_present(OTHER_LINK), '另一条链接不能被牵连')
        self.assertEqual(self.ledger()['links'], 1)
        self.assertEqual(self.ledger()['knowledge'], 1, '只剩 B 的来源行：%r' % self.ledger())
        self.assertTrue(keep_doc)


class StillReferencedKeepsTest(LinkIndexFixture):
    """合同第 2 条第 2 段：URL 还有别人引用时，**不得**被清。"""

    def test_link_is_kept_when_another_document_still_references_it(self):
        first = self.knowledge_doc(WECHAT_LINK, '知识库收藏 A')
        second = self.knowledge_doc(WECHAT_LINK, '知识库收藏 B')
        self.store.intel_recommend.collect()
        before = self.ledger()
        self.assertEqual((before['knowledge'], before['links']), (2, 1), '前置：%r' % before)

        self.store.knowledge.delete_document(first)

        after = self.ledger()
        self.assertTrue(self.link_present(WECHAT_LINK), 'B 还在引用，link_index 必须保留')
        self.assertEqual(after['links'], 1, '%r' % after)
        self.assertEqual(after['knowledge'], 1, '只剩 B 的来源行：%r' % after)
        self.assertEqual(after['recs'], 1, '还有来源，推荐行必须保留：%r' % after)
        self.assertEqual(after['count'], 1, '次数按真实来源归到 1：%r' % after)

        # 再删 B：这次没人引用了，该清
        self.store.knowledge.delete_document(second)
        final = self.ledger()
        self.assertFalse(self.link_present(WECHAT_LINK), '最后一份没了，link_index 该清')
        self.assertEqual(final['links'], 0, '%r' % final)
        self.assertEqual(final['recs'], 0)
        self.assertEqual(self.orphans(), [])

    def test_link_is_kept_when_an_intake_item_still_references_it(self):
        doc = self.knowledge_doc(WECHAT_LINK, '知识库收藏 A')
        self.intake_link()                      # 回收项引用同一 URL（并另建一份正文 B）
        self.store.intel_recommend.collect()
        before = self.ledger()
        # intake_link 收集时也会在知识库建一份正文 → 知识来源 2 条，回收项来源 1 条
        self.assertEqual((before['knowledge'], before['intake'], before['links']),
                         (2, 1, 1), '前置：%r' % before)

        self.store.knowledge.delete_document(doc)

        after = self.ledger()
        self.assertTrue(self.link_present(WECHAT_LINK), '回收项还引用着，link_index 必须保留')
        self.assertEqual(after['links'], 1, '%r' % after)
        self.assertEqual(after['knowledge'], 1, 'A 的来源清了，B 的还在：%r' % after)
        self.assertEqual(after['intake'], 1, '回收站来源不该被动：%r' % after)
        self.assertEqual(self.orphans(), [], '回收项还引用着，不该算孤儿')


class HookFailureIsVisibleTest(LinkIndexFixture):
    """合同第 2 条第 3 段：钩子抛错 → 删除成功，错误可见，不谎报已清。"""

    def broken_hook(self, message='synthetic-cleanup-failure'):
        def boom(document_id):
            raise RuntimeError(message)
        self.store.knowledge.document_removed_hook = boom

    def test_hook_error_surfaces_in_delete_receipt(self):
        doc = self.knowledge_doc(WECHAT_LINK, '知识库收藏 A')
        self.store.intel_recommend.collect()
        self.assertTrue(self.link_present(), '前置：link_index 应有该 URL')

        self.broken_hook()
        removed = self.store.knowledge.delete_document(doc)

        self.assertTrue(removed.get('removed'), '钩子失败不该推翻删除：%r' % removed)
        self.assertIn('synthetic-cleanup-failure', str(removed.get('hook_error', '')),
                      '钩子异常必须在回执里看得见：%r' % removed)
        blob = json.dumps(removed, ensure_ascii=False)
        for word in ('已清', '已清干净', '已清理', '清理完成', '无残留'):
            self.assertNotIn(word, blob, '回执出现断言式措辞 %r，可能在谎报：%r' % (word, removed))

    def test_cleanup_failure_is_isolated_from_deletion(self):
        """钩子抛错时，后续文档的删除不受影响。"""
        good = self.knowledge_doc(OTHER_LINK, '知识库收藏 B')
        self.store.intel_recommend.collect()
        self.broken_hook()

        removed = self.store.knowledge.delete_document(good)

        self.assertTrue(removed.get('removed'))
        self.assertIn('hook_error', removed)
        # 删除本身的语义字段仍然可信
        self.assertEqual(removed['id'], good)
        self.assertTrue(removed['deleted'] if 'deleted' in removed else True)


class ScanBudgetTest(LinkIndexFixture):
    """合同第 1 条：单次文档删除的收尾不得做全库扫描。"""

    def test_single_document_delete_does_not_scan_the_whole_knowledge_base(self):
        """收尾只应定点查被删文档那几个 URL，不该调 _document_links 全表扫。"""
        doc = self.knowledge_doc(WECHAT_LINK, '知识库收藏 A')
        self.store.intel_recommend.collect()
        self.scans.clear()

        self.store.knowledge.delete_document(doc)

        self.assertEqual(self.scans, [],
                         '直接删除的收尾不该做全库 live 扫描，实测 %d 次' % len(self.scans))

    def test_batch_of_document_deletes_also_stays_point_wise(self):
        docs = [self.knowledge_doc(WECHAT_LINK, '收藏 %d' % i) for i in range(3)]
        self.store.intel_recommend.collect()
        self.scans.clear()

        for doc in docs:
            self.store.knowledge.delete_document(doc)

        self.assertEqual(self.scans, [],
                         '逐份删也不该扫全库，实测 %d 次' % len(self.scans))
        self.assertEqual(self.ledger()['links'], 0, '全删完后 link_index 该清空：%r' % self.ledger())


class NoLinkHostSkipsReconcileTest(LinkIndexFixture):
    """23f-2 保险：迁移未装载（没有 link_host 索引）时**不做** URL 级对账。

    为什么：没有 link_host 生成列，「这个 URL 还在吗」只能全表 LIKE 扫 documents，
    而「删掉最后一份引用」必然落进未命中路径 —— 实测中位 8.5 秒（/tmp 的 .backup
    正式库副本），单次删除中位 4.4 秒，批删 50 篇约 218 秒。迁移若失败或被跳过，
    不能让删除被拖到这个量级。

    所以缺索引时退回 23e 行为：只按文档清来源行 + 重算推荐，**不做** URL 级对账。
    留下的 link_index 孤儿行由 `coordination/tools/check_link_index_orphans.py` 发现，
    不在删除路径上偷偷修。
    """

    def test_without_link_host_no_full_table_like_is_issued(self):
        self.drop_link_host()
        doc = self.knowledge_doc(WECHAT_LINK, '知识库收藏 A')
        self.store.intel_recommend.collect()
        seen = self.knowledge_queries()

        self.store.knowledge.delete_document(doc)

        # 判据是「发没发那条会扫全表的 SQL」，不是耗时（负载高时耗时就不可信）
        offenders = [sql for sql in seen if 'FROM documents' in sql and 'WHERE' in sql]
        self.assertEqual(offenders, [],
                         '没有 link_host 时不该对 documents 发带 WHERE 的查询：%r' % offenders)
        likes = [sql for sql in seen if 'LIKE' in sql]
        self.assertEqual(likes, [],
                         '没有 link_host 时不该发任何全表 LIKE：%r' % likes)

    def test_without_link_host_receipt_marks_the_skip(self):
        self.drop_link_host()
        doc = self.knowledge_doc(WECHAT_LINK, '知识库收藏 A')
        self.store.intel_recommend.collect()

        result = self.store.intel_recommend.forget_document_cleanup([doc])

        self.assertTrue(result.get('reconciled') is False,
                        '返回值应标明未做 URL 级对账：%r' % result)
        self.assertIn('link_host', str(result.get('note', '')),
                      '跳过原因要说清是缺 link_host 索引：%r' % result)

    def test_without_link_host_sources_and_counts_stay_correct(self):
        """跳过对账 ≠ 什么都不做：23e 那套按文档清来源行必须照旧正确。"""
        self.drop_link_host()
        first = self.knowledge_doc(WECHAT_LINK, '知识库收藏 A')
        second = self.knowledge_doc(OTHER_LINK, '知识库收藏 B')
        self.store.intel_recommend.collect()
        before = self.ledger()
        self.assertEqual((before['knowledge'], before['links']), (2, 2), '前置：%r' % before)

        self.store.knowledge.delete_document(first)

        after = self.ledger()
        self.assertEqual(after['knowledge'], 1, '来源行按文档清掉一条：%r' % after)
        self.assertEqual(after['count'], 1, '推荐次数按剩余来源重算：%r' % after)
        self.assertEqual(after['recs'], 1, '还有来源，推荐行保留：%r' % after)
        # 跳过对账 ⇒ link_index 不动（孤儿留给只读工具发现）
        self.assertEqual(after['links'], 2, '跳过 URL 级对账时 link_index 不该变：%r' % after)
        self.assertTrue(self.link_present(WECHAT_LINK), 'WECHAT_LINK 应仍在 link_index')
        self.assertTrue(second)

    def test_without_link_host_deletion_still_succeeds_without_hook_error(self):
        self.drop_link_host()
        doc = self.knowledge_doc(WECHAT_LINK, '知识库收藏 A')
        self.store.intel_recommend.collect()

        removed = self.store.knowledge.delete_document(doc)

        self.assertTrue(removed['removed'], '删除必须照常成功：%r' % removed)
        self.assertNotIn('hook_error', removed,
                         '跳过对账不是错误，不该报 hook_error：%r' % removed)

    def test_with_link_host_reconciliation_still_happens(self):
        """有索引时行为同 23f：最后一份引用要清掉，返回值标明做过对账。

        走真实删除入口（`knowledge.delete_document`），钩子才是被接上的那条路 ——
        直接调 `forget_document_cleanup` 会绕过「文档还在不在」这个前提。
        """
        self.add_link_host()
        first = self.knowledge_doc(WECHAT_LINK, '知识库收藏 A')
        second = self.knowledge_doc(WECHAT_LINK, '知识库收藏 B')
        self.store.intel_recommend.collect()
        self.assertTrue(self.link_present(WECHAT_LINK), '前置：link_index 应有该 URL')

        removed = self.store.knowledge.delete_document(first)
        self.assertNotIn('hook_error', removed, '钩子不该报错：%r' % removed)
        self.assertTrue(self.link_present(WECHAT_LINK), 'B 还在引用，link_index 必须保留')

        removed2 = self.store.knowledge.delete_document(second)
        self.assertNotIn('hook_error', removed2, '钩子不该报错：%r' % removed2)
        self.assertFalse(self.link_present(WECHAT_LINK),
                         '最后一份没了，link_index 必须清掉：%r' % self.ledger())
        self.assertEqual(self.ledger()['links'], 0)
        self.assertEqual(self.orphans(), [])

    def test_with_link_host_receipt_reports_reconciled(self):
        """返回值在有索引时标明做过对账，并说清清了几个链接。"""
        self.add_link_host()
        doc = self.knowledge_doc(WECHAT_LINK, '知识库收藏 A')
        self.store.intel_recommend.collect()
        # 先把文档删掉（钩子在删除后触发），再单看返回值该是什么形状
        self.store.knowledge.delete_document(doc)

        result = self.store.intel_recommend.forget_document_cleanup([])
        self.assertIn('reconciled', result, '返回值应是字典：%r' % result)
        self.assertIn('sources', result)
        self.assertIn('links', result)
        self.assertIn('accounts', result)
        self.assertIn('note', result)
        self.assertIs(result['reconciled'], False, '空 id 列表不做对账：%r' % result)


class LinkHostFastPathTest(LinkIndexFixture):
    """23d 的 link_host 生成列 + 索引必须被用上，否则删最后一份引用会全表扫。

    实测（/tmp 的 .backup 正式库副本）：没有 link_host 时「未命中点查」要把 17.6 万行
    扫完，中位 8.5 秒；有 link_host 时 EXPLAIN 变成
    `SEARCH documents USING INDEX documents_link_host`，同一个查询 5.3 毫秒。

    这里不测时间（机器负载一高就不稳），只钉**走没走索引** —— 那才是 1600 倍的差距。
    """

    def plans(self):
        with sqlite3.connect(f'file:{self.store.knowledge.path}?mode=ro', uri=True) as connection:
            return [row[3] for row in connection.execute(
                "EXPLAIN QUERY PLAN SELECT 1 FROM documents"
                " WHERE link_host=? AND url=?", ('mp.weixin.qq.com', 'x'))]

    def test_link_host_path_is_an_index_search(self):
        self.add_link_host()
        self.assertTrue(any('USING INDEX' in plan for plan in self.plans()),
                        '有 link_host 时应走索引，实际查询计划：%r' % self.plans())

    def test_cleanup_still_correct_with_link_host_present(self):
        """走索引不能改变判断结果：最后一份引用仍要清，仍被引用仍要留。"""
        self.add_link_host()
        first = self.knowledge_doc(WECHAT_LINK, '知识库收藏 A')
        second = self.knowledge_doc(WECHAT_LINK, '知识库收藏 B')
        self.store.intel_recommend.collect()

        self.store.knowledge.delete_document(first)
        self.assertTrue(self.link_present(WECHAT_LINK),
                        '有 link_host 索引时，B 还在引用，link_index 必须保留')

        self.store.knowledge.delete_document(second)
        self.assertFalse(self.link_present(WECHAT_LINK),
                         '有 link_host 索引时，最后一份没了也必须清掉')
        self.assertEqual(self.orphans(), [])

    def test_falls_back_correctly_without_link_host(self):
        """没有 link_host（迁移未装载）时退回 LIKE：结果一样，只是慢。"""
        doc = self.knowledge_doc(WECHAT_LINK, '知识库收藏 A')
        other = self.knowledge_doc(OTHER_LINK, '知识库收藏 B')
        self.store.intel_recommend.collect()

        removed = self.store.knowledge.delete_document(doc)

        self.assertTrue(removed['removed'])
        self.assertNotIn('hook_error', removed, '没有索引不该报错：%r' % removed)
        self.assertFalse(self.link_present(WECHAT_LINK), 'URL 没人引用了，该清')
        self.assertTrue(self.link_present(OTHER_LINK), '另一条不能被牵连')
        self.assertTrue(other)


class NoIntelDatabaseTest(LinkIndexFixture):
    """护栏：没跑过情报侧的 workspace，删文档不该凭空建库。"""

    def test_deleting_a_document_does_not_create_the_intel_database(self):
        for suffix in ('', '-wal', '-shm'):
            path = Path(self.store.workspace) / 'intel' / ('intel.db' + suffix)
            if path.exists():
                path.unlink()
        doc = self.knowledge_doc(WECHAT_LINK, '知识库收藏 A')
        removed = self.store.knowledge.delete_document(doc)
        self.assertTrue(removed['removed'])
        self.assertFalse((Path(self.store.workspace) / 'intel' / 'intel.db').exists(),
                         '钩子不该在情报侧缺席时建库')


class IndexGateTest(LinkIndexFixture):
    def assert_skips_index(self, replacement=''):
        self.add_link_host()
        doc = self.knowledge_doc(WECHAT_LINK, 'synthetic')
        self.store.intel_recommend.collect()
        with sqlite3.connect(self.store.knowledge.path) as connection:
            connection.execute('DROP INDEX documents_link_host')
            if replacement:
                connection.execute(replacement)
        seen = self.knowledge_queries()
        result = self.store.intel_recommend.forget_document_cleanup([doc])
        self.assertIs(result['reconciled'], False)
        self.assertIn('索引', result['note'])
        self.assertEqual([sql for sql in seen if 'FROM documents' in sql and 'WHERE' in sql], [])

    def test_column_without_index_skips_documents_queries(self):
        self.assert_skips_index()

    def test_partial_index_is_not_a_general_lookup_index(self):
        self.assert_skips_index("CREATE INDEX partial_host ON documents(link_host) WHERE url='absent'")

    def test_nonleading_host_index_is_not_accepted(self):
        self.assert_skips_index('CREATE INDEX trailing_host ON documents(title,link_host)')

    def test_incompatible_collation_index_is_not_accepted(self):
        self.assert_skips_index('CREATE INDEX nocase_host ON documents(link_host COLLATE NOCASE)')


ORPHAN_TOOL = Path(__file__).resolve().parents[1] / 'coordination/tools/check_link_index_orphans.py'


class OrphanToolVirtualColumnTest(unittest.TestCase):
    # hygiene-4：这条用例加载 coordination/tools/check_link_index_orphans.py，
    # 而 coordination/ 是私有协调证据，不进公开仓库。只跳这一条，同文件其余用例照跑。
    @unittest.skipUnless(ORPHAN_TOOL.is_file(),
                         '需要本机工具 coordination/tools/check_link_index_orphans.py；'
                         'coordination/ 是私有协调证据，不进公开仓库（load-manifest-v2 §3.2）。')
    def test_virtual_column_uses_index_branch(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location('orphan_tool',
            Path(__file__).resolve().parents[1] / 'coordination/tools/check_link_index_orphans.py')
        tool = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(tool)
        with sqlite3.connect(':memory:') as connection:
            connection.execute("CREATE TABLE documents(url TEXT, link_host TEXT GENERATED ALWAYS AS ('mp.weixin.qq.com') VIRTUAL)")
            connection.execute('CREATE INDEX documents_link_host ON documents(link_host)')
            connection.execute('INSERT INTO documents(url) VALUES (?)', (WECHAT_LINK,))
            seen = []
            connection.set_trace_callback(seen.append)
            self.assertTrue(tool.has_link_host(connection))
            self.assertEqual(tool.live_links(connection, None), {WECHAT_LINK: 'knowledge'})
            self.assertFalse(any('LIKE' in sql for sql in seen))
            plan = connection.execute('EXPLAIN QUERY PLAN SELECT url FROM documents WHERE link_host=?', (tool.WECHAT,)).fetchall()
            self.assertTrue(any('documents_link_host' in row[3] for row in plan))


if __name__ == '__main__':
    unittest.main(verbosity=2)