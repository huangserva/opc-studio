"""知识库「移到已删除」（可恢复软删除）的存储层测试。

全部用 TemporaryDirectory 里的合成库：不碰正式 knowledge.db、不依赖 remio、不联网、不调模型。
覆盖 design-knowledge-delete-report.md §5 里属于存储层的必测项。
"""
import json
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import knowledge_store
from knowledge_store import Knowledge
from server import Store

GONE = 'f' * 32


class TrashStorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = Store(self.root / 'studio')
        self.folder = self.root / 'project'
        self.folder.mkdir()
        self.write('甲.md', '项目决策：本地优先。这是一段可检索的正文。')
        self.write('乙.md', '项目复盘：删掉也要留着原文引用。')
        self.k = Knowledge(self.store, [{'id': 'project', 'name': '项目', 'kind': 'projects', 'paths': [str(self.folder)]}])
        self.k.sync('project')
        self.docs = {item['title'].rsplit(' / ', 1)[-1]: item for item in self.all_docs()}
        self.hooks = []

    def tearDown(self):
        self.temp.cleanup()

    def write(self, name, text):
        path = self.folder / name
        path.write_text(text, encoding='utf-8')
        return path

    def all_docs(self, **filters):
        """翻完所有分页：每页只有 30 条，计数类断言不能只看第一页。"""
        rows, offset = [], 0
        while True:
            page = self.k.search('', offset=offset, **filters)
            rows += page['results']
            offset = page['next_offset']
            if offset is None:
                return rows

    def doc(self, name):
        return self.docs[name]

    def intake_db(self, rows=(), path='intake.db'):
        """造一个最小回收项库，只提供 drops 表这一处批量引用接口。"""
        target = self.root / 'studio' / 'inspiration' / path
        target.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(target) as c:
            c.execute('CREATE TABLE IF NOT EXISTS drops(id TEXT PRIMARY KEY, document_id TEXT NOT NULL DEFAULT \'\')')
            for drop_id, doc_id in rows:
                c.execute('INSERT INTO drops VALUES(?,?)', (drop_id, doc_id))
        return target

    def cards_db(self, rows=(), path='cards.db'):
        """造一个最小提示词卡库：主资料列 document_id 与 refs JSON 两处引用接口都在。"""
        target = self.root / 'studio' / 'inspiration' / path
        target.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(target) as c:
            c.execute('CREATE TABLE IF NOT EXISTS cards(id TEXT PRIMARY KEY, document_id TEXT NOT NULL DEFAULT \'\', '
                      'refs TEXT NOT NULL DEFAULT \'[]\')')
            for card_id, doc_id, refs in rows:
                c.execute('INSERT INTO cards VALUES(?,?,?)', (card_id, doc_id, refs))
        return target

    def trashed(self):
        with self.k.db() as c:
            return {row[0] for row in c.execute('SELECT document_id FROM document_trash')}

    # ── 迁移 ──────────────────────────────────────────────────────────────────
    def test_migration_is_idempotent_across_repeated_opens(self):
        self.assertEqual(self.trashed(), set())
        with self.k.db() as c:
            columns = [row['name'] for row in c.execute('PRAGMA table_info(document_trash)')]
            tables = c.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='document_trash'").fetchone()[0]
            schema = ''.join(row['sql'] or '' for row in c.execute('SELECT sql FROM sqlite_master'))
        self.assertEqual(tables, 1)
        self.assertEqual(columns, ['document_id', 'deleted_at', 'deleted_hash', 'title', 'source'])
        self.assertNotIn('FOREIGN KEY', schema, '独立标记表不能带外键级联')
        target = self.doc('甲.md')
        self.k.trash_many([{'id': target['id'], 'expected_hash': target['hash']}], confirm=True)
        for _ in range(3):
            reopened = Knowledge(self.store, [{'id': 'project', 'name': '项目', 'kind': 'projects', 'paths': [str(self.folder)]}])
            self.assertEqual(reopened.status()['total'], 1)
            self.assertEqual({item['id'] for item in reopened.trash_list()['results']}, {target['id']})

    # ── 参数校验：整体非法 → 抛错且零变更 ───────────────────────────────────
    def test_invalid_request_shape_is_rejected_with_zero_change(self):
        target = self.doc('甲.md')
        item = {'id': target['id'], 'expected_hash': target['hash']}
        bad_calls = [
            lambda: self.k.trash_many([item]),
            lambda: self.k.trash_many([item], confirm='yes'),
            lambda: self.k.trash_many([item], confirm=1),
            lambda: self.k.trash_many([{'id': target['id']}], confirm=True),
            lambda: self.k.trash_many([{'expected_hash': target['hash']}], confirm=True),
            lambda: self.k.trash_many([{**item, 'force': True}], confirm=True),
            lambda: self.k.trash_many([{**item, 'expected_hash': None}], confirm=True),
            lambda: self.k.trash_many([{**item, 'expected_hash': 'not-a-hash'}], confirm=True),
            lambda: self.k.trash_many([{'id': 'not-an-id', 'expected_hash': target['hash']}], confirm=True),
            lambda: self.k.trash_many([{'id': target['id'].upper(), 'expected_hash': target['hash']}], confirm=True),
            lambda: self.k.trash_many([], confirm=True),
            lambda: self.k.trash_many('nope', confirm=True),
            lambda: self.k.trash_many([item] * 51, confirm=True),
        ]
        for call in bad_calls:
            with self.assertRaises(ValueError):
                call()
        self.assertEqual(self.trashed(), set())
        self.assertEqual(self.k.status()['total'], 2)

    def test_fifty_items_ok_and_fifty_one_rejected_with_dedupe(self):
        for i in range(52):
            self.write('批量%d.md' % i, '批量正文 %d 的可检索内容' % i)
        self.k.sync('project')
        many = self.all_docs()
        self.assertEqual(len(many), 54)
        picked = many[:50]
        receipt = self.k.trash_many([{'id': d['id'], 'expected_hash': d['hash']} for d in picked], confirm=True)
        self.assertEqual(receipt['summary'], {'requested': 50, 'deleted': 50, 'already_trashed': 0,
                                              'missing': 0, 'conflicts': 0, 'errors': 0})
        self.assertEqual(self.k.status()['total'], 4)
        with self.assertRaises(ValueError):
            self.k.trash_many([{'id': d['id'], 'expected_hash': d['hash']} for d in many], confirm=True)
        with self.assertRaises(ValueError):
            self.k.trash_many([{'id': many[0]['id'], 'expected_hash': picked[0]['hash']}] * 51, confirm=True)
        self.assertEqual(self.k.status()['total'], 4)
        # 同一批里重复编号只算一条，不报错也不重复写。
        again = self.k.trash_many([{'id': many[50]['id'], 'expected_hash': many[50]['hash']},
                                   {'id': many[51]['id'], 'expected_hash': many[51]['hash']},
                                   {'id': many[51]['id'], 'expected_hash': many[51]['hash']}], confirm=True)
        self.assertEqual(again['summary'], {'requested': 3, 'deleted': 2, 'already_trashed': 1,
                                            'missing': 0, 'conflicts': 0, 'errors': 0}, '同一请求里的重复编号幂等')
        self.assertEqual(again['already_trashed'][0]['reason'], '同一请求里重复提交')

    def test_preview_rejects_zero_and_over_fifty_and_bad_ids(self):
        for bad in ([], ['x'] * 51, [42], ['ZZ' * 16], '甲.md'):
            with self.assertRaises(ValueError):
                self.k.trash_preview(bad)
            with self.assertRaises(ValueError):
                self.k.restore_many(bad)
        self.assertEqual(self.trashed(), set())

    # ── 预览 ──────────────────────────────────────────────────────────────────
    def test_preview_reports_state_references_and_semantics_without_writing(self):
        target = self.doc('甲.md')
        other = self.doc('乙.md')
        self.intake_db([('d1', target['id']), ('d2', target['id']), ('d3', other['id'])])
        self.k.save_dossier({'title': '依据', 'items': [{'id': target['id'], 'start': 0, 'end': 6, 'hash': target['hash']}]})
        before = self.trashed()
        preview = self.k.trash_preview([target['id'], other['id'], GONE, target['id']])
        self.assertEqual(preview['summary'], {'requested': 3, 'ready': 2, 'already_trashed': 0, 'missing': 1})
        self.assertEqual(self.trashed(), before, '预览是只读的')
        first, second, missing = preview['items']
        self.assertEqual((first['id'], first['title'], first['source'], first['status']),
                         (target['id'], target['title'], 'project', 'ready'))
        self.assertEqual(first['hash'], target['hash'])
        self.assertEqual(first['references']['counted'], {'recycle_drops': 2, 'prompt_cards': None, 'dossier_snapshots': 1})
        self.assertEqual(second['references']['counted']['recycle_drops'], 1)
        self.assertEqual(second['references']['counted']['dossier_snapshots'], 0)
        self.assertEqual(missing['status'], 'missing')
        self.assertIn('原文件与 remio 笔记不动', preview['semantics'])
        self.assertIn('不是彻底抹除', preview['semantics'])

    def test_unknown_reference_is_never_reported_as_zero(self):
        target = self.doc('甲.md')
        unknown = self.k.trash_preview([target['id']])['items'][0]['references']
        self.assertEqual(unknown['counted'], {'recycle_drops': None, 'prompt_cards': None, 'dossier_snapshots': 0})
        self.assertIn('作品与底稿引用', unknown['unknown'])
        self.assertIn('线索与事项证据', unknown['unknown'])
        # 库存在、确实没有引用时才可以显示 0。
        self.intake_db([])
        readable = self.k.trash_preview([target['id']])['items'][0]['references']
        self.assertEqual(readable['counted']['recycle_drops'], 0)
        self.assertIn('作品与底稿引用', readable['unknown'], '没有批量接口的引用类别仍然如实标未统计')

    def test_broken_reference_database_stays_unknown(self):
        target = self.doc('甲.md')
        broken = self.intake_db()
        broken.write_text('这不是一个数据库文件', encoding='utf-8')
        counted = self.k.trash_preview([target['id']])['items'][0]['references']['counted']
        self.assertIsNone(counted['recycle_drops'])
        self.assertIsNone(counted['prompt_cards'])
        self.assertEqual(counted['dossier_snapshots'], 0)

    def test_unreadable_cross_reference_categories_join_unknown(self):
        """缺库、坏库、缺表、真实 0 四种对照：counted 与 unknown 必须一致。"""
        target = self.doc('甲.md')['id']
        inspiration = self.root / 'studio' / 'inspiration'

        def report():
            return self.k.trash_preview([target])['items'][0]['references']

        def assert_consistent(entry, key, label, expected):
            counted = entry['counted'][key]
            if expected is None:
                self.assertIsNone(counted, '%s 读不到时 counted 必须是 None' % key)
                self.assertIn(label, entry['unknown'], '%s 读不到时必须进 unknown' % key)
            else:
                self.assertEqual(counted, expected, '%s 读得到时按真实计数' % key)
                self.assertNotIn(label, entry['unknown'], '%s 真实计数（含 0）不能进 unknown' % key)

        # 缺库：intake.db 与 cards.db 都不存在。
        entry = report()
        assert_consistent(entry, 'recycle_drops', '回收项', None)
        assert_consistent(entry, 'prompt_cards', '提示词卡', None)

        # 坏库：两个文件都在，但不是数据库。
        self.intake_db().write_text('这不是一个数据库文件', encoding='utf-8')
        self.cards_db().write_text('这也不是一个数据库文件', encoding='utf-8')
        entry = report()
        assert_consistent(entry, 'recycle_drops', '回收项', None)
        assert_consistent(entry, 'prompt_cards', '提示词卡', None)

        # 缺表：库能打开，但没有 drops / cards 表。
        for name in ('intake.db', 'cards.db'):
            (inspiration / name).unlink()
        with sqlite3.connect(inspiration / 'intake.db') as c:
            c.execute('CREATE TABLE other(x)')
        with sqlite3.connect(inspiration / 'cards.db') as c:
            c.execute('CREATE TABLE other(x)')
        entry = report()
        assert_consistent(entry, 'recycle_drops', '回收项', None)
        assert_consistent(entry, 'prompt_cards', '提示词卡', None)

        # 真实 0：库和表都在，只是没有引用。
        for name in ('intake.db', 'cards.db'):
            (inspiration / name).unlink()
        self.intake_db([])
        self.cards_db([])
        entry = report()
        assert_consistent(entry, 'recycle_drops', '回收项', 0)
        assert_consistent(entry, 'prompt_cards', '提示词卡', 0)
        self.assertIn('作品与底稿引用', entry['unknown'])
        self.assertIn('线索与事项证据', entry['unknown'])

    def test_prompt_card_refs_counted_as_cards_and_bad_json_is_unknown(self):
        """提示词卡引用：主资料列与 refs JSON 都算，按卡片数去重；坏 JSON / 非数组退回 unknown。"""
        self.write('丙.md', '第三篇用于卡片去重与引用形状的正文。')
        self.k.sync('project')
        docs = {item['title'].rsplit(' / ', 1)[-1]: item for item in self.all_docs()}
        a, b, c = docs['甲.md']['id'], docs['乙.md']['id'], docs['丙.md']['id']
        self.cards_db([
            ('k1', a, '[]'),                                                                   # 仅主引用
            ('k2', '', json.dumps([{'id': 'r1', 'document_id': b}])),                          # 仅 refs
            ('k3', c, json.dumps([{'id': 'r1', 'document_id': c}])),                           # 同卡主 + refs 重复
            ('k4', '', json.dumps([{'id': 'r1', 'document_id': a}, {'id': 'r2', 'document_id': b}])),  # 一卡引用两篇
        ])
        counted = {item['id']: item['references']['counted']['prompt_cards']
                   for item in self.k.trash_preview([a, b, c])['items']}
        self.assertEqual(counted, {a: 2, b: 2, c: 1}, '主引用与 refs 都算，同一卡重复只算一张')
        self.assertEqual(self.k.trash_references([c])[c]['units']['prompt_cards'], '引用该文档的卡片数')

        # 坏 JSON：整类退回 unknown，不能静默当 0。
        self.cards_db([('k5', '', '{坏 JSON')])
        entry = self.k.trash_preview([a, b, c])['items'][0]['references']
        self.assertIsNone(entry['counted']['prompt_cards'])
        self.assertIn('提示词卡', entry['unknown'])

        # 非数组形状同样算读不懂。
        (self.root / 'studio' / 'inspiration' / 'cards.db').unlink()
        self.cards_db([('k6', '', '{}')])
        entry = self.k.trash_preview([a])['items'][0]['references']
        self.assertIsNone(entry['counted']['prompt_cards'])
        self.assertIn('提示词卡', entry['unknown'])

    def test_preview_shows_already_trashed_and_missing_states(self):
        target = self.doc('甲.md')
        self.k.trash_many([{'id': target['id'], 'expected_hash': target['hash']}], confirm=True)
        items = {item['id']: item for item in self.k.trash_preview([target['id'], GONE])['items']}
        self.assertEqual(items[target['id']]['status'], 'trashed')
        self.assertEqual(items[target['id']]['hash'], target['hash'])
        self.assertTrue(items[target['id']]['deleted_at'])
        self.assertEqual(items[GONE]['status'], 'missing')

    # ── 删除语义 ──────────────────────────────────────────────────────────────
    def test_search_total_and_source_counts_all_filter_trashed(self):
        first, second = self.doc('甲.md'), self.doc('乙.md')
        self.assertEqual(self.k.search('项目')['total'], 2)
        self.k.trash_many([{'id': first['id'], 'expected_hash': first['hash']}], confirm=True)
        self.assertEqual(self.k.search('项目')['total'], 1, 'LIKE 检索要过滤')
        self.assertEqual(self.k.search('项目复盘', mode='fts')['total'], 1, 'FTS 检索要过滤')
        self.assertEqual(self.k.search('本地优先', mode='fts')['total'], 0, 'FTS 检索要过滤')
        self.assertNotIn(first['id'], [row['id'] for row in self.k.search('')['results']])
        self.assertEqual(self.k.status()['total'], 1)
        self.assertEqual([s['count'] for s in self.k.status()['sources']], [1])
        for row in self.k.search('')['results']:
            self.assertNotEqual(row['id'], first['id'])
        # 分页计数与结果集一致：翻页不会把已删除的又数进来。
        for i in range(32):
            self.write('页%d.md' % i, '分页正文 %d 的内容' % i)
        self.k.sync('project')
        total = self.k.search('分页')['total']
        seen, offset = [], 0
        while offset is not None:
            page = self.k.search('分页', offset=offset)
            seen += [row['id'] for row in page['results']]
            offset = page['next_offset']
        self.assertEqual(len(seen), total)
        self.assertNotIn(first['id'], seen)
        self.k.trash_many([{'id': second['id'], 'expected_hash': second['hash']}], confirm=True)
        self.assertEqual(self.k.status()['total'], total)

    def test_assistant_search_path_uses_the_same_filtered_index(self):
        target = self.doc('甲.md')
        self.k.trash_many([{'id': target['id'], 'expected_hash': target['hash']}], confirm=True)
        self.assertEqual(self.k.search('本地优先')['total'], 0)
        self.assertEqual(self.k.search('本地优先', mode='fts')['total'], 0)
        # 助手 search_knowledge 走的就是 knowledge.search()，过滤口径天然一致。
        import assistant_knowledge
        tools = assistant_knowledge.KnowledgeTools(self.store)
        hidden = tools.search_knowledge({'query': '本地优先'}, {})
        self.assertEqual(hidden['results'], [], '助手检索看不到已移到已删除的正文')
        self.assertEqual(hidden['query_mode'], 'keyword_candidates')
        kept = tools.search_knowledge({'query': '项目复盘'}, {})
        self.assertEqual([entry['id'] for entry in kept['results']], [self.doc('乙.md')['id']])

    def test_single_document_stays_readable_with_trashed_facts(self):
        target = self.doc('甲.md')
        before = self.k.document(target['id'])
        self.k.trash_many([{'id': target['id'], 'expected_hash': target['hash']}], confirm=True)
        after = self.k.document(target['id'])
        self.assertFalse(before['trashed'])
        self.assertEqual(before['deleted_at'], '')
        self.assertTrue(after['trashed'])
        self.assertTrue(after['deleted_at'])
        self.assertEqual({k: v for k, v in after.items() if k not in ('trashed', 'deleted_at')},
                         {k: v for k, v in before.items() if k not in ('trashed', 'deleted_at')})
        # 资料夹、卡片、回收守卫依赖的批量读接口语义不变。
        self.assertEqual(self.k.document_hashes([target['id']]), {target['id']: target['hash']})
        self.assertEqual(self.k.document_provenance_facts([target['id']])[target['id']]['intake_channel'], '')

    def test_hard_delete_hook_still_runs_and_soft_delete_never_calls_it(self):
        self.k.document_removed_hook = self.hooks.append
        first, second = self.doc('甲.md'), self.doc('乙.md')
        receipt = self.k.trash_many([{'id': first['id'], 'expected_hash': first['hash']}], confirm=True)
        self.assertEqual(receipt['summary']['deleted'], 1)
        self.assertEqual(self.hooks, [], '软删除不触发正文删除钩子')
        removed = self.k.delete_document(second['id'])
        self.assertTrue(removed['removed'])
        self.assertEqual(self.hooks, [second['id']])
        self.assertEqual(self.k.status()['total'], 0, '硬删除照旧真的删掉正文')
        with self.assertRaisesRegex(ValueError, '资料不存在'):
            self.k.document(second['id'])

    def test_missing_and_already_trashed_are_idempotent_and_never_invent_records(self):
        target = self.doc('甲.md')
        first = self.k.trash_many([{'id': target['id'], 'expected_hash': target['hash']}, {'id': GONE, 'expected_hash': target['hash']}], confirm=True)
        self.assertEqual(first['summary'], {'requested': 2, 'deleted': 1, 'already_trashed': 0,
                                            'missing': 1, 'conflicts': 0, 'errors': 0})
        self.assertEqual(self.trashed(), {target['id']}, '正文不在索引里就不造无意义的已删除记录')
        repeat = self.k.trash_many([{'id': target['id'], 'expected_hash': target['hash']},
                                    {'id': GONE, 'expected_hash': target['hash']}], confirm=True)
        self.assertEqual(repeat['summary'], {'requested': 2, 'deleted': 0, 'already_trashed': 1,
                                             'missing': 1, 'conflicts': 0, 'errors': 0})
        self.assertEqual(self.trashed(), {target['id']})

    def test_body_changed_after_preview_is_a_conflict_not_a_deletion(self):
        target = self.doc('甲.md')
        preview = self.k.trash_preview([target['id']])
        self.assertEqual(preview['items'][0]['status'], 'ready')
        self.write('甲.md', '项目决策：本地优先。这是一段可检索的正文。补充了新段落。')
        self.k.sync('project')
        receipt = self.k.trash_many([{'id': target['id'], 'expected_hash': preview['items'][0]['hash']}], confirm=True)
        self.assertEqual(receipt['summary'], {'requested': 1, 'deleted': 0, 'already_trashed': 0,
                                              'missing': 0, 'conflicts': 1, 'errors': 0})
        self.assertEqual(receipt['conflicts'][0]['expected_hash'], preview['items'][0]['hash'])
        self.assertNotEqual(receipt['conflicts'][0]['current_hash'], preview['items'][0]['hash'])
        self.assertEqual(self.trashed(), set())
        self.assertEqual(self.k.status()['total'], 2)
        # 重新预览后可以正常删除。
        fresh = self.k.trash_preview([target['id']])['items'][0]
        self.assertEqual(self.k.trash_many([{'id': target['id'], 'expected_hash': fresh['hash']}], confirm=True)['summary']['deleted'], 1)

    def test_failed_statement_reports_errors_and_keeps_the_rest_honest(self):
        first, second = self.doc('甲.md'), self.doc('乙.md')
        with self.k.db() as c:
            c.execute("CREATE TRIGGER guard BEFORE INSERT ON document_trash WHEN new.document_id='%s'"
                      " BEGIN SELECT RAISE(ABORT,'这一条不许写'); END" % second['id'])
        receipt = self.k.trash_many([{'id': first['id'], 'expected_hash': first['hash']},
                                     {'id': second['id'], 'expected_hash': second['hash']}], confirm=True)
        self.assertEqual(receipt['summary'], {'requested': 2, 'deleted': 1, 'already_trashed': 0,
                                              'missing': 0, 'conflicts': 0, 'errors': 1})
        self.assertEqual(self.trashed(), {first['id']}, '报 deleted 的必须真的在已删除表里')
        self.assertEqual(self.k.status()['total'], 1)

    def test_failed_commit_reports_nothing_and_rolls_everything_back(self):
        first, second = self.doc('甲.md'), self.doc('乙.md')
        real_connect = sqlite3.connect

        class CommitFails:
            """只在退出事务（提交）那一刻炸掉：整批都不该被当成删掉了。"""

            def __init__(self, inner):
                self._inner = inner

            def __getattr__(self, name):
                return getattr(self._inner, name)

            def __setattr__(self, name, value):
                if name == '_inner':
                    object.__setattr__(self, name, value)
                else:
                    setattr(self._inner, name, value)

            def __enter__(self):
                self._inner.__enter__()
                return self

            def __exit__(self, exc_type, exc, tb):
                if exc_type is None:
                    raise sqlite3.OperationalError('模拟提交失败')
                return self._inner.__exit__(exc_type, exc, tb)

        def connect(*args, **kwargs):
            return CommitFails(real_connect(*args, **kwargs))

        with patch.object(knowledge_store.sqlite3, 'connect', connect):
            with self.assertRaises(sqlite3.Error):
                self.k.trash_many([{'id': first['id'], 'expected_hash': first['hash']},
                                   {'id': second['id'], 'expected_hash': second['hash']}], confirm=True)
        self.assertEqual(self.trashed(), set())
        self.assertEqual(self.k.status()['total'], 2)
        self.assertEqual(self.k.search('项目')['total'], 2)

    # ── 同步不复活 ────────────────────────────────────────────────────────────
    def test_sync_never_resurrects_trashed_document(self):
        target = self.doc('甲.md')
        self.k.trash_many([{'id': target['id'], 'expected_hash': target['hash']}], confirm=True)
        # 同内容重新同步
        self.k.sync('project')
        self.assertEqual(self.trashed(), {target['id']})
        self.assertEqual(self.k.search('项目')['total'], 1)
        # 内容变化后重新同步：编号不变，正文更新，但仍然保持隐藏
        self.write('甲.md', '项目决策：本地优先。改写后的正文，全新的内容。')
        self.k.sync('project')
        self.assertEqual(self.trashed(), {target['id']})
        self.assertEqual(self.k.search('项目')['total'], 1)
        self.assertTrue(self.k.document(target['id'])['trashed'])
        self.assertIn('改写后的正文', self.k.document(target['id'])['body'])
        # 来源移除后完整导入把正文行清掉，标记仍在；来源回来后重新出现仍是隐藏状态
        (self.folder / '甲.md').unlink()
        self.k.sync('project')
        self.assertEqual(self.trashed(), {target['id']})
        self.assertNotIn(target['id'], [row['id'] for row in self.all_docs()])
        with self.assertRaises(ValueError):
            self.k.document(target['id'])
        listed = self.k.trash_list()['results'][0]
        self.assertEqual(listed['id'], target['id'])
        self.assertEqual(listed['status'], 'source_gone')
        self.assertFalse(listed['body_available'])
        self.write('甲.md', '项目决策：回来之后又是新的一份。')
        self.k.sync('project')
        self.assertEqual(self.trashed(), {target['id']})
        self.assertEqual(self.k.search('项目')['total'], 1, '同编号重新导入仍应保持隐藏')
        self.assertTrue(self.k.document(target['id'])['trashed'])

    def test_restore_reports_body_missing_when_source_is_gone(self):
        target = self.doc('甲.md')
        original = self.k.document(target['id'])
        self.k.trash_many([{'id': target['id'], 'expected_hash': target['hash']}], confirm=True)
        (self.folder / '甲.md').unlink()
        self.k.sync('project')
        receipt = self.k.restore_many([target['id']])
        self.assertEqual(receipt['summary'], {'requested': 1, 'restored': 0, 'unhidden': 1, 'not_found': 0})
        self.assertIn('来源已不在索引', receipt['unhidden'][0]['note'])
        self.assertEqual(self.trashed(), set())
        self.write('甲.md', '项目决策：同步回来的正文。')
        self.k.sync('project')
        restored = self.k.document(target['id'])
        self.assertFalse(restored['trashed'])
        self.assertEqual(restored['deleted_at'], '')
        self.assertEqual(restored['body'], '项目决策：同步回来的正文。')
        # 恢复不改正文，也不改 hash：删除时的指纹与重新导入后的新指纹都如实带出来。
        self.assertNotEqual(restored['hash'], original['hash'])

    def test_restore_brings_back_count_and_content_unchanged(self):
        first, second = self.doc('甲.md'), self.doc('乙.md')
        dossier = self.k.save_dossier({'title': '依据', 'items': [{'id': first['id'], 'start': 0, 'end': 6, 'hash': first['hash']}]})
        self.intake_db([('d1', first['id']), ('d2', first['id'])])
        before = self.k.document(first['id'])
        self.k.trash_many([{'id': first['id'], 'expected_hash': first['hash']}], confirm=True)
        self.assertEqual(self.k.status()['total'], 1)
        receipt = self.k.restore_many([first['id'], first['id']])
        self.assertEqual(receipt['summary'], {'requested': 1, 'restored': 1, 'unhidden': 0, 'not_found': 0},
                         '同一请求里的重复编号去重后只恢复一次')
        self.assertFalse(receipt['restored'][0]['content_changed'])
        self.assertEqual(self.k.status()['total'], 2)
        self.assertEqual(self.k.search('项目')['total'], 2)
        self.assertEqual(self.k.search('本地优先', mode='fts')['total'], 1)
        after = self.k.document(first['id'])
        self.assertEqual(after['hash'], before['hash'], '恢复不改正文与指纹')
        self.assertEqual(self.k.dossier(dossier['id'])['items'], dossier['items'], '资料夹快照不动')
        self.assertEqual(self.k.trash_preview([first['id']])['items'][0]['references']['counted']['recycle_drops'], 2)
        again = self.k.restore_many([first['id']])
        self.assertEqual(again['summary']['not_found'], 1)
        self.assertEqual(self.k.status()['total'], 2)

    def test_trash_list_is_bounded_and_does_not_mix_into_source_counts(self):
        self.assertEqual(self.k.trash_list()['total'], 0)
        for i in range(5):
            self.write('已删%d.md' % i, '待删除的正文 %d' % i)
        self.k.sync('project')
        page = self.k.search('', source='project', offset=3)
        self.assertEqual(page['total'], 7)
        for item in page['results'][:3]:
            self.k.trash_many([{'id': item['id'], 'expected_hash': item['hash']}], confirm=True)
        listing = self.k.trash_list()
        self.assertEqual(listing['total'], 3)
        self.assertEqual([entry['status'] for entry in listing['results']], ['trashed'] * 3)
        self.assertEqual([entry['source'] for entry in listing['results']], ['project'] * 3)
        self.assertEqual(self.k.status()['total'], 4)
        self.assertEqual([s['count'] for s in self.k.status()['sources']], [4], '已删除不混进来源计数')
        self.assertEqual(len(self.k.trash_list(limit=2)['results']), 2)
        self.assertEqual(self.k.trash_list(limit=2)['next_offset'], 2)
        self.assertIsNone(self.k.trash_list(offset=2, limit=2)['next_offset'])
        self.assertEqual(self.k.trash_list(offset=-5, limit=0)['limit'], 1)

    # ── 回收出处与 ownership ──────────────────────────────────────────────────
    def test_recycle_ownership_and_sharing_are_untouched_by_soft_delete(self):
        folder = self.root / 'recycle'
        folder.mkdir()
        (folder / '回收正文.md').write_text('创意回收工厂收进来的正文', encoding='utf-8')
        k = Knowledge(self.store, [{'id': 'recycle-src', 'name': '回收来源', 'kind': 'projects', 'paths': [str(folder)]}])
        k.sync('recycle-src')
        owned = k.search('')[ 'results'][0]
        borrowed = self.doc('乙.md')
        with k.db() as c:
            c.execute('UPDATE documents SET provenance=? WHERE id=?',
                      (json.dumps({'intake_channel': '创意回收工厂', 'drop_id': 'd1'}, ensure_ascii=False), owned['id']))
        before_facts = k.document_provenance_facts([owned['id'], borrowed['id']])
        self.assertEqual(before_facts[owned['id']]['intake_channel'], '创意回收工厂')
        self.assertEqual(before_facts[borrowed['id']]['intake_channel'], '')
        self.intake_db([('d1', owned['id']), ('d2', owned['id']), ('d3', borrowed['id'])])
        counted = k.trash_preview([owned['id'], borrowed['id']])['items']
        self.assertEqual([item['references']['counted']['recycle_drops'] for item in counted], [2, 1])
        receipt = k.trash_many([{'id': owned['id'], 'expected_hash': owned['hash']},
                                {'id': borrowed['id'], 'expected_hash': borrowed['hash']}], confirm=True)
        self.assertEqual(receipt['summary']['deleted'], 2)
        # 软删除不动 provenance：归属判断与共享计数继续按回收站老规矩读。
        self.assertEqual(k.document_provenance_facts([owned['id'], borrowed['id']]), before_facts)
        self.assertEqual(k.document_hashes([owned['id'], borrowed['id']]),
                         {owned['id']: owned['hash'], borrowed['id']: borrowed['hash']})
        guard = self.intake_db([('d1', owned['id']), ('d2', owned['id']), ('d3', borrowed['id'])], path='guard.db')
        blocked = k.delete_document(owned['id'], keep_if_referenced_by=(str(guard), 'drops'), require_intake_channel='创意回收工厂')
        self.assertFalse(blocked['removed'], '还有回收项引用时，硬删除守卫依旧拦住')
        self.assertEqual(blocked['referenced'], 2)

    # ── 并发 ──────────────────────────────────────────────────────────────────
    def test_sync_and_trash_are_serialised_by_the_same_write_lock(self):
        target = self.doc('甲.md')
        opened, release, attempted = threading.Event(), threading.Event(), threading.Event()
        real_import = Knowledge._import
        real_connect = sqlite3.connect

        def slow_import(c, source):
            # 真导入一次（正文已写、事务仍开着），然后把写锁攥在手里不放。
            detail, complete = real_import(self.k, c, source)
            opened.set()
            release.wait(5)
            return detail, complete

        class Barrier:
            """转发给真连接；删除线程走到 BEGIN IMMEDIATE 那一刻先点亮屏障，再去抢锁。"""

            def __init__(self, inner):
                object.__setattr__(self, '_inner', inner)

            def __getattr__(self, name):
                return getattr(self._inner, name)

            def __setattr__(self, name, value):
                setattr(self._inner, name, value)

            def __enter__(self):
                self._inner.__enter__()
                return self

            def __exit__(self, *exc):
                return self._inner.__exit__(*exc)

            def execute(self, sql, *args):
                if 'BEGIN IMMEDIATE' in sql.upper():
                    attempted.set()
                return self._inner.execute(sql, *args)

        def connect(*args, **kwargs):
            return Barrier(real_connect(*args, **kwargs))

        self.k.sync_lock.acquire()
        outcome, worker = [], None
        try:
            with patch.object(self.k, '_import', side_effect=slow_import), \
                 patch.object(knowledge_store.sqlite3, 'connect', connect):
                worker = threading.Thread(target=lambda: self.k.sync('project', reserved=True))
                worker.start()
                self.assertTrue(opened.wait(2), '同步应先拿到写锁')
                remover = threading.Thread(target=lambda: outcome.append(
                    self.k.trash_many([{'id': target['id'], 'expected_hash': target['hash']}], confirm=True)))
                remover.start()
                # 屏障保证删除线程确实已走到 BEGIN IMMEDIATE 门口，而不是还没被调度。
                self.assertTrue(attempted.wait(2), '删除线程必须已尝试进入 BEGIN IMMEDIATE')
                self.assertEqual(outcome, [], '删除线程已到写锁门口但同步持锁，不能插进去')
                release.set()
                worker.join(5)
                remover.join(5)
        finally:
            if worker is None or worker.is_alive():
                self.k.sync_lock.release()
        self.assertEqual(outcome[0]['summary']['deleted'], 1)
        self.assertEqual(self.trashed(), {target['id']})
        self.assertEqual(self.k.search('原文引用')['total'], 1, '同步照常完成')
        self.assertEqual(self.k.search('本地优先')['total'], 0)
        self.assertEqual(self.k.status()['total'], 1)

    def test_concurrent_delete_restore_and_sync_end_in_one_consistent_state(self):
        target = self.doc('甲.md')
        outcomes, errors = [], []

        def attempt(kind):
            try:
                if kind == 'trash':
                    outcomes.append(self.k.trash_many([{'id': target['id'], 'expected_hash': target['hash']}], confirm=True))
                elif kind == 'restore':
                    outcomes.append(self.k.restore_many([target['id']]))
                else:
                    outcomes.append(self.k.sync('project'))
            except Exception as exc:  # noqa: BLE001 - 并发下只记录，不掩盖
                errors.append((kind, type(exc).__name__))

        threads = [threading.Thread(target=attempt, args=(kind,)) for kind in ('trash', 'restore', 'sync', 'trash')]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(10)
        self.assertEqual(errors, [])
        # 无论谁先谁后，最终状态必须自洽：标记与检索结果不会互相打架。
        trashed = target['id'] in self.trashed()
        self.assertEqual(trashed, self.k.document(target['id'])['trashed'])
        self.assertEqual(trashed, target['id'] not in [row['id'] for row in self.all_docs()])
        self.assertEqual(self.k.status()['total'], 1 if trashed else 2)
        self.assertEqual(len(outcomes), 4)

    def test_errors_never_leak_database_path_sql_or_body(self):
        broken = self.intake_db()
        broken.write_text('坏掉的库', encoding='utf-8')
        target = self.doc('甲.md')
        preview = self.k.trash_preview([target['id']])['items'][0]
        self.assertIsNone(preview['references']['counted']['recycle_drops'])
        messages = []
        for call in (lambda: self.k.trash_many([{'id': target['id'], 'expected_hash': 'x' * 200}], confirm=True),
                     lambda: self.k.trash_many([{'id': target['id'], 'expected_hash': target['hash']}]),
                     lambda: self.k.trash_many([{'id': 'x' * 40, 'expected_hash': target['hash']}], confirm=True),
                     lambda: self.k.trash_many([{'id': target['id'], 'expected_hash': target['hash'], 'force': True}], confirm=True),
                     lambda: self.k.trash_many([{'id': target['id'], 'expected_hash': target['hash']}] * 51, confirm=True),
                     lambda: self.k.trash_preview([]),
                     lambda: self.k.restore_many(['bad'])):
            with self.assertRaises(ValueError) as error:
                call()
            messages.append(str(error.exception))
        for message in messages:
            self.assertNotIn(str(self.root), message)
            self.assertNotIn(str(self.k.path), message)
            self.assertNotIn('inspiration', message)
            self.assertNotIn('SELECT', message)
            self.assertNotIn('document_trash', message)
            self.assertNotIn('项目决策', message)
            self.assertNotIn('这是一段可检索的正文', message)
        self.assertEqual(self.trashed(), set())
        # 已删除列表给的是标题和定位，不是正文。
        self.k.trash_many([{'id': target['id'], 'expected_hash': target['hash']}], confirm=True)
        entry = self.k.trash_list()['results'][0]
        self.assertEqual(entry['title'], target['title'])
        self.assertNotIn('这是一段可检索的正文', json.dumps(entry, ensure_ascii=False))


if __name__ == '__main__':
    unittest.main()