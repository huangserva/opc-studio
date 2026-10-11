"""21o 相关条目：总时间预算 / 部分返回 / 调用次数上限 / 混合检索路径 / 读模块时钟。

方案：`coordination/plan-perf-knowledge.md` §3.1（A 三字组走 document_fts + B 约 1500ms 总预算）
与 `coordination/check-recall-trigram-report.md` §5（协调者采纳的混合做法）。
本文件只写测试，不改产品代码；用假 knowledge 计数与 sleep 注入，不碰 798MB 库、不联网、不调模型。

关于「混合检索路径」：假 knowledge 记录每次调用是否走了 FTS 分支。约定产品按下面任一形状
把 ≥3 字词交给 FTS（其余视为 LIKE-only）：
  - `knowledge.search(query=term, mode='fts')`
  - `knowledge.search(query=term, fts=True)`
  - `knowledge.search_fts(query=term)`
2 字词允许走 LIKE-only（预算内、限次数）。
"""
import importlib.util
import sys
import sqlite3
import tempfile
import time
import unittest
from isolation_patches import MIGRATOR_REASON
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tests.intake_dependency_patches import guard_intake_dependencies
from tests.isolation_patches import clock_sequence, module_time
from intake_cards import MAX_RELATED_TERMS, IntakeCards
from intake_store import Intake
from intel_recommend import Recommender
from knowledge_store import Knowledge
from server import Store


def load_migrator():
    """迁移脚本文件名带连字符，不能直接 import；按路径加载成模块。

    hygiene-4：脚本在 coordination/ 下，是私有协调证据，不进公开仓库。缺它时
    抛 SkipTest 而不是 FileNotFoundError，只跳过依赖它的 MigrationScriptTests，
    本机有脚本时行为与从前逐字一致。
    """
    path = Path(__file__).resolve().parents[1] / 'coordination' / 'migrate-knowledge-link-host.py'
    if not path.is_file():
        raise unittest.SkipTest(MIGRATOR_REASON)
    spec = importlib.util.spec_from_file_location('migrate_knowledge_link_host', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

DROP = {'id': 'a' * 32, 'reason': '这个开头方式可以借'}
DOCUMENT = {'id': 'b' * 32, 'title': '旧码头黄昏人像'}
# 摘要 + 理由已足够造出 >= 24 个候选词，稳稳触到 MAX_RELATED_TERMS 上限。
# 摘要里放一个英文词 OpenAI，确保词表里同时有 >=3 字词（混合做法必须走 FTS）。
READING = {
    'summary': '用 OpenAI 搭内容工作流：黄昏的旧码头，水面倒影与背影的拍法。',
    'why': '开头方式可以借，构图值得学。',
    'points': ['蹲拍视角压低地平线', '让水面复制天空', '人物只留背影不露脸'],
    'angles': [{'title': '写成取景清单', 'how': '把要点排成清单'}],
}


class FakeKnowledge:
    """计数 + 可注入耗时；记录每次调用走的是 FTS 还是 LIKE-only。"""

    def __init__(self, delay=0.0, rows=None):
        self.delay = delay
        self.rows = rows or []
        self.calls = []          # [(term, 'fts' | 'like')]

    @staticmethod
    def _path(kwargs):
        if (kwargs.get('fts') is True or kwargs.get('only_fts') is True
                or kwargs.get('mode') in ('fts', 'fts_only')
                or kwargs.get('path') in ('fts', 'fts_only')):
            return 'fts'
        return 'like'

    def _record(self, term, kwargs):
        self.calls.append((term, self._path(kwargs)))
        if self.delay:
            time.sleep(self.delay)
        return {'results': list(self.rows)}

    def search(self, query='', **kwargs):
        return self._record(query, kwargs)

    def search_fts(self, query='', **kwargs):
        return self._record(query, dict(kwargs, fts=True))


class RelatedEntriesPerfTests(unittest.TestCase):
    def setUp(self):
        guard_intake_dependencies(self)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(self.tmp.name)
        self.store._knowledge = Knowledge(self.store, sources=[])
        self.intake = Intake(self.store.knowledge, self.tmp.name)
        self.cards = IntakeCards(self.intake, self.store.knowledge, self.tmp.name)
        self.cards.knowledge = FakeKnowledge()

    def run_related(self, fake):
        self.cards.knowledge = fake
        return self.cards.related_entries(DROP, DOCUMENT, READING)

    # ── 总时间预算 ────────────────────────────────────────────────────────────

    def test_related_entries_has_a_total_time_budget(self):
        fake = FakeKnowledge(delay=0.2)
        started = time.monotonic()
        self.run_related(fake)
        elapsed = time.monotonic() - started
        self.assertLess(elapsed, 2.0,
                        '相关条目检索要有总时间预算（约 1500ms），不能逐词跑满 24 次；实测 %.2fs' % elapsed)

    def test_related_entries_returns_partial_on_budget_exhaustion(self):
        rows = [{'id': 'c' * 32, 'title': '相关笔记', 'kind': 'notes',
                 'modified': '2026-01-01T00:00:00+00:00', 'excerpt': '摘录'}]
        fake = FakeKnowledge(delay=0.2, rows=rows)
        items = self.run_related(fake)
        self.assertTrue(items, '预算用尽时应返回已拿到的结果，而不是空')
        self.assertLess(len(fake.calls), MAX_RELATED_TERMS,
                        '预算用尽后不应继续把 %d 个词全部检索完，实测 %d 次'
                        % (MAX_RELATED_TERMS, len(fake.calls)))

    def test_related_entries_search_calls_are_capped(self):
        fake = FakeKnowledge(delay=0.2)
        self.run_related(fake)
        self.assertLessEqual(len(fake.calls), 8,
                             '单次分析的相关条目检索次数应受总预算约束（约 1500ms）；实测 %d 次'
                             % len(fake.calls))

    # ── 混合检索路径 ──────────────────────────────────────────────────────────

    def test_related_entries_uses_fts_for_long_terms_and_limits_short_like(self):
        fake = FakeKnowledge()
        self.run_related(fake)
        long_like = [term for term, path in fake.calls if len(term) >= 3 and path != 'fts']
        self.assertEqual(long_like, [],
                         '≥3 字词必须走 document_fts，不得落到 LIKE-only 全表扫描：%r' % long_like)
        short_like = [term for term, path in fake.calls if len(term) == 2 and path == 'like']
        self.assertLessEqual(len(short_like), MAX_RELATED_TERMS,
                             '2 字词可走 LIKE 兜底，但要限次数并计入总预算；实测 %d 个' % len(short_like))

    # ── 读模块时钟 ────────────────────────────────────────────────────────────

    def test_related_entries_reads_the_module_clock(self):
        fake = FakeKnowledge()
        self.cards.knowledge = fake
        # 第一次 0.0 设基准，之后每次都 10.0（远超 1500ms 预算）：必须早停。
        clock = clock_sequence([0.0] + [10.0] * 80)
        with module_time('intake_cards', monotonic=clock):
            self.cards.related_entries(DROP, DOCUMENT, READING)
        self.assertLessEqual(len(fake.calls), 3,
                             '预算必须用 intake_cards.time.monotonic 判定：时钟一跳过预算就该停；'
                             '实测 %d 次调用' % len(fake.calls))


class PendingBatchFetchTests(unittest.TestCase):
    """M1：pending() 一次批量取 id/hash，不再逐条 knowledge.document()（会把整篇 body 拉进来）。

    来源：review-clues-retry-2 的 M1；产品改动只在 intake_cards.pending()，行为不变。
    """

    def setUp(self):
        guard_intake_dependencies(self)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(self.tmp.name)
        self.store._knowledge = Knowledge(self.store, sources=[])
        self.intake = Intake(self.store.knowledge, self.tmp.name)
        self.cards = IntakeCards(self.intake, self.store.knowledge, self.tmp.name)
        self.cards.model_provider = lambda: None

    def test_pending_fetches_hashes_in_one_batch_not_one_document_per_row(self):
        for n in range(3):
            drop = self.intake.receive({'id': format(n + 1, '032x'), 'kind': 'text',
                                        'text': '一段想法 %d，先收着。' % n, 'reason': ''})
            self.intake.resolve(drop['id'])
        calls = {'document': [], 'document_hashes': []}
        real = self.store.knowledge

        class Counting:
            def document(self, doc_id):
                calls['document'].append(doc_id)
                return real.document(doc_id)

            def document_hashes(self, ids):
                calls['document_hashes'].append(list(ids))
                return real.document_hashes(ids)

            def __getattr__(self, name):
                return getattr(real, name)

        self.cards.knowledge = Counting()
        ready, waiting = self.cards.pending(5)
        self.assertEqual((len(ready), waiting), (3, 3))
        self.assertEqual(calls['document'], [],
                         'pending() 不应逐条 knowledge.document() 拉整篇正文：%r' % calls['document'])
        self.assertTrue(calls['document_hashes'], 'pending() 应一次批量取 id/hash')


class DocumentLinksMigrationTests(unittest.TestCase):
    """21t 改法 2：documents.link_host 生成列在时走等值索引；不在时退回 LIKE（前后兼容）。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def _old_schema_db(self, with_column):
        path = Path(self.tmp.name) / ('with.db' if with_column else 'without.db')
        column = (", link_host TEXT GENERATED ALWAYS AS (CASE WHEN url LIKE '%mp.weixin.qq.com%' "
                  "THEN 'mp.weixin.qq.com' ELSE NULL END) VIRTUAL") if with_column else ''
        connection = sqlite3.connect(path)
        connection.execute('''CREATE TABLE documents(id TEXT PRIMARY KEY, source TEXT NOT NULL,
            kind TEXT NOT NULL, title TEXT NOT NULL, body TEXT NOT NULL, url TEXT NOT NULL,
            modified TEXT NOT NULL, tags TEXT NOT NULL, hash TEXT NOT NULL,
            provenance TEXT NOT NULL DEFAULT '{}'%s)''' % column)
        connection.execute('''INSERT INTO documents(id,source,kind,title,body,url,modified,tags,hash,provenance)
            VALUES(?,?,?,?,?,?,?,?,?,?)''',
                           ('a' * 32, 'wechat-links', 'wechat_links', '公众号的一篇', '正文',
                            'https://mp.weixin.qq.com/s/abc', '2026', '[]', 'h', '{}'))
        connection.execute('''INSERT INTO documents(id,source,kind,title,body,url,modified,tags,hash,provenance)
            VALUES(?,?,?,?,?,?,?,?,?,?)''',
                           ('b' * 32, 'articles', 'articles', '普通文章', '正文',
                            'https://example.com/x', '2026', '[]', 'h', '{}'))
        connection.commit()
        connection.close()
        return path

    def test_document_links_returns_wechat_rows_with_the_generated_column(self):
        path = self._old_schema_db(with_column=True)
        connection = sqlite3.connect(path)
        connection.execute('CREATE INDEX documents_link_host ON documents(link_host)')
        plan = ' '.join(row[3] for row in connection.execute(
            "EXPLAIN QUERY PLAN SELECT id FROM documents WHERE link_host='mp.weixin.qq.com'"))
        connection.close()
        rows = Recommender._document_links(str(path))
        self.assertEqual([row[0] for row in rows], ['a' * 32])
        self.assertIn('documents_link_host', plan, '等值查询必须用上 link_host 索引：%r' % plan)

    def test_document_links_falls_back_to_like_when_the_column_is_absent(self):
        path = self._old_schema_db(with_column=False)
        rows = Recommender._document_links(str(path))
        self.assertEqual([row[0] for row in rows], ['a' * 32],
                         '没有 link_host 列的旧库必须退回 LIKE，仍要返回公众号链接')


class OldSchemaStartupTests(unittest.TestCase):
    """H1：未迁移旧库（没有 link_host 列）也必须能构造 Knowledge，并走 LIKE 回退。

    修前 `Knowledge.__init__` 无条件 `CREATE INDEX ... ON documents(link_host)`，
    旧库构造直接 `OperationalError: no such column: link_host`，到不了回退逻辑。
    """

    def setUp(self):
        guard_intake_dependencies(self)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def _unmigrated_workspace(self):
        workspace = Path(self.tmp.name) / 'ws'
        (workspace / 'knowledge').mkdir(parents=True)
        db = workspace / 'knowledge' / 'knowledge.db'
        connection = sqlite3.connect(db)
        connection.executescript('''CREATE TABLE sources(id TEXT PRIMARY KEY, config TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending', detail TEXT NOT NULL DEFAULT '', synced_at TEXT);
            CREATE TABLE documents(id TEXT PRIMARY KEY, source TEXT NOT NULL, kind TEXT NOT NULL,
            title TEXT NOT NULL, body TEXT NOT NULL, url TEXT NOT NULL, modified TEXT NOT NULL,
            tags TEXT NOT NULL, hash TEXT NOT NULL, provenance TEXT NOT NULL DEFAULT '{}');
            CREATE TABLE dossiers(id TEXT PRIMARY KEY, title TEXT NOT NULL, content TEXT NOT NULL,
            modified TEXT NOT NULL, purpose TEXT NOT NULL DEFAULT '');''')
        connection.execute('''INSERT INTO documents(id,source,kind,title,body,url,modified,tags,hash,provenance)
            VALUES(?,?,?,?,?,?,?,?,?,?)''',
                           ('a' * 32, 'wechat-links', 'wechat_links', '公众号的一篇', '正文',
                            'https://mp.weixin.qq.com/s/abc', '2026', '[]', 'h', '{}'))
        connection.commit()
        connection.close()
        return workspace

    def test_knowledge_boots_and_falls_back_on_an_unmigrated_database(self):
        workspace = self._unmigrated_workspace()
        store = Store(str(workspace))
        knowledge = Knowledge(store, sources=[])           # 修前这里 OperationalError
        with knowledge.db() as connection:
            columns = {row[1] for row in connection.execute('PRAGMA table_xinfo(documents)')}
        self.assertNotIn('link_host', columns, '构造旧库不应偷偷补列（迁移要人工显式做）')
        rows = Recommender._document_links(str(knowledge.path))
        self.assertEqual([row[0] for row in rows], ['a' * 32], '缺列时必须退回 LIKE 并命中公众号链接')


class MigrationScriptTests(unittest.TestCase):
    """M2/L1/列名校验：迁移脚本的备份唯一、事务自检回滚、既有列定义核对。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.mig = load_migrator()

    def _old_schema_db(self, name='knowledge.db'):
        path = Path(self.tmp.name) / name
        connection = sqlite3.connect(path)
        connection.executescript('''CREATE TABLE documents(id TEXT PRIMARY KEY, source TEXT NOT NULL,
            kind TEXT NOT NULL, title TEXT NOT NULL, body TEXT NOT NULL, url TEXT NOT NULL,
            modified TEXT NOT NULL, tags TEXT NOT NULL, hash TEXT NOT NULL,
            provenance TEXT NOT NULL DEFAULT '{}');''')
        insert = ('INSERT INTO documents(id,source,kind,title,body,url,modified,tags,hash,provenance) '
                  'VALUES(?,?,?,?,?,?,?,?,?,?)')
        connection.execute(insert, ('a' * 32, 'wechat-links', 'wechat_links', '公众号的一篇', '正文',
                                    'https://mp.weixin.qq.com/s/abc', '2026', '[]', 'h', '{}'))
        connection.execute(insert, ('b' * 32, 'articles', 'articles', '普通文章', '正文',
                                    'https://example.com/x', '2026', '[]', 'h', '{}'))
        connection.commit()
        connection.close()
        return path

    def _columns(self, path):
        connection = sqlite3.connect(path)
        try:
            return {row[1] for row in connection.execute('PRAGMA table_xinfo(documents)')}
        finally:
            connection.close()

    def test_migrate_adds_column_and_index(self):
        path = self._old_schema_db()
        self.mig.migrate(path)
        self.assertIn('link_host', self._columns(path))

    def test_two_migrations_keep_two_backups_and_the_first_stays_old_schema(self):
        # 固定时钟：若备份名只有秒级时间戳就会重名覆盖；排他创建 + 随机后缀必须区分开。
        # 两次备份时间戳相同，只能按创建先后区分，不能用名字排序。
        fixed = datetime(2026, 10, 10, 8, 42, 24, tzinfo=timezone.utc)
        self.mig.datetime = type('FixedDateTime', (datetime,),
                                 {'now': classmethod(lambda cls, tz=None: fixed)})
        path = self._old_schema_db()

        def backups():
            return set(Path(self.tmp.name).glob(path.name + '.bak-*'))

        before = backups()
        self.mig.migrate(path)
        first = backups() - before
        self.mig.migrate(path)
        second = backups() - before - first
        self.assertEqual(len(first), 1, '第一次迁移要留一份备份：%r' % [b.name for b in first])
        self.assertEqual(len(second), 1, '第二次迁移要另留一份，不得覆盖：%r' % [b.name for b in second])
        self.assertNotIn('link_host', self._columns(first.pop()),
                         '第一份（最早）备份应仍是迁移前的原 schema')

    def test_existing_link_host_with_wrong_definition_is_rejected(self):
        path = Path(self.tmp.name) / 'wrong.db'
        connection = sqlite3.connect(path)
        connection.execute('''CREATE TABLE documents(id TEXT PRIMARY KEY, url TEXT NOT NULL,
            link_host TEXT GENERATED ALWAYS AS (CASE WHEN url LIKE '%weixin%' THEN 'mp.weixin.qq.com'
            ELSE NULL END) VIRTUAL)''')
        connection.commit()
        connection.close()
        with self.assertRaises(SystemExit) as bad:
            self.mig.migrate(path)
        self.assertIn('定义不符', str(bad.exception))

    def test_count_mismatch_rolls_back_and_leaves_schema_unchanged(self):
        path = Path(self.tmp.name) / 'bad.db'
        connection = sqlite3.connect(path)
        connection.execute('''CREATE TABLE documents(id TEXT PRIMARY KEY, source TEXT NOT NULL,
            kind TEXT NOT NULL, title TEXT NOT NULL, body TEXT NOT NULL, url TEXT NOT NULL,
            modified TEXT NOT NULL, tags TEXT NOT NULL, hash TEXT NOT NULL,
            provenance TEXT NOT NULL DEFAULT '{}',
            link_host TEXT GENERATED ALWAYS AS (CASE WHEN url LIKE '%weixin%' THEN 'mp.weixin.qq.com'
            ELSE NULL END) VIRTUAL)''')
        insert = ('INSERT INTO documents(id,source,kind,title,body,url,modified,tags,hash,provenance) '
                  'VALUES(?,?,?,?,?,?,?,?,?,?)')
        connection.execute(insert, ('a' * 32, 's', 'wechat_links', 't', 'b',
                                    'https://mp.weixin.qq.com/s/abc', '2026', '[]', 'h', '{}'))
        # 这一条只命中错误的 link_host 谓词、不命中正确的 LIKE，让自检计数不一致。
        connection.execute(insert, ('b' * 32, 's', 'articles', 't', 'b',
                                    'https://weixin.example.com/y', '2026', '[]', 'h', '{}'))
        connection.commit()
        connection.close()

        def schema():
            connection = sqlite3.connect(path)
            try:
                columns = [row[1] for row in connection.execute('PRAGMA table_xinfo(documents)')]
                indexes = [row[0] for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='index' AND name='documents_link_host'")]
                sql = connection.execute(
                    "SELECT sql FROM sqlite_master WHERE type='table' AND name='documents'").fetchone()[0]
                return columns, indexes, sql
            finally:
                connection.close()

        before = schema()
        # 模拟旧脚本「只按名字接受」：绕过列定义核对，直达计数自检，验证失败时确实回滚。
        self.mig.check_existing_column = lambda connection: True
        with self.assertRaises(SystemExit) as bad:
            self.mig.migrate(path)
        self.assertIn('计数不一致', str(bad.exception))
        self.assertEqual(schema(), before, '自检失败必须回滚，原库 schema 不变')


if __name__ == '__main__':
    unittest.main()