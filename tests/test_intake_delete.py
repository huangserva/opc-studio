"""回收项手动删除：drop、即时分析、知识库正文与本机媒体文件一起删。

覆盖合同里点名的场景：正常删、共用正文的引用计数、进行中删除、删后可重新收下、
卡片来源已删除、情报侧 link_index 清理。全部用假模型与假抓取：
不联网、不调真实模型、不读任何密钥，测试只用临时目录。
"""
import io
import json
import sqlite3
import contextlib
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import intake_store
from tests.intake_dependency_patches import guard_intake_dependencies, patch_intake_dependency
from intake_cards import IntakeCards
from intake_store import Intake
from knowledge_store import Knowledge
from server import Handler, Store, ThreadingHTTPServer, wire_intake_links

SOURCE = ('黄昏的旧码头先把木头泡软，绳索在风里轻轻晃，远处渡船只留一条灯线。'
          '拍照的人蹲下来，让水面把天空再复制一遍，人物只留一个背影，不露脸。')
READING = {'summary': '旧码头黄昏的蹲拍方法：用水面倒影复制天空，人物只留背影。',
           'points': ['水面倒影是主手法', '人物不露脸'],
           'why': '开头就能直接借的取景方式。',
           'angles': [{'title': '写一条蹲拍笔记', 'how': '从水面倒影切入，交代蹲位与光线'}]}
CARD = {'prompt': '旧码头黄昏，蹲拍视角，水面倒映天空，人物只留背影，暖色低对比',
        'model': 'qi21', 'scene': '公众号头图', 'refs': ['e0']}
WECHAT_LINK = 'https://mp.weixin.qq.com/s?__biz=MzA5Mg%3D%3D&mid=264&idx=1&sn=abc123'


def payload(value):
    return json.dumps(value, ensure_ascii=False)


class FakeWeb:
    def __call__(self):
        return self

    def fetch(self, args, job):
        return {'url': args['url'], 'title': '旧码头的蹲拍', 'markdown': SOURCE, 'start': 0,
                'total_chars': len(SOURCE), 'truncated': False, 'next_start': None}


class IntakeDeleteTests(unittest.TestCase):
    def setUp(self):
        guard_intake_dependencies(self)
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(self.tmp.name)
        self.store._knowledge = Knowledge(self.store, sources=[])
        self.intake = Intake(self.store.knowledge, self.tmp.name)
        self.store._creative_intake = self.intake
        self.cards = IntakeCards(self.intake, self.store.knowledge, self.tmp.name)
        self.store._intake_cards = self.cards
        self.intake.analysis_provider = self.cards.analysis_states
        self.cards.model_provider = lambda: {'model': 'synthetic', 'timeout_seconds': 100}
        self.cards.analysis_retry_wait = 0
        self.calls = []
        self.answers = {'read': payload(READING), 'cards': payload({'cards': [CARD]})}
        patch_intake_dependency(self, "WebTools", FakeWeb)
        self.seed_model()
        self.srv = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.srv.store = self.store
        self.thread = threading.Thread(target=self.srv.serve_forever, daemon=True)
        self.thread.start()
        self.base = f'http://127.0.0.1:{self.srv.server_port}'
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def tearDown(self):
        self.settle()
        self.srv.shutdown()
        self.srv.server_close()
        self.thread.join()
        for worker in list(self.cards.analysis_workers.values()):
            worker.join(timeout=5)
        for worker in list(self.intake.recognition_workers.values()):
            worker.join(timeout=5)
        self.tmp.cleanup()

    def seed_model(self):
        def fake(identity, prompt, system, cancel, config, deadline):
            self.calls.append({'identity': identity})
            answer = self.answers.get('read' if identity.endswith('-read') else 'cards')
            if isinstance(answer, Exception):
                raise answer
            return answer
        self.cards.call_model = fake

    def settle(self, timeout=8):
        deadline = time.time() + timeout
        while time.time() < deadline:
            workers = list(self.cards.analysis_workers.values()) + list(self.intake.recognition_workers.values())
            if not workers:
                break
            for worker in workers:
                worker.join(timeout=1)
        time.sleep(0.05)

    def api(self, path, body=None, headers=None):
        data = json.dumps(body).encode() if body is not None else None
        head = {'Origin': self.base, 'X-Article-Studio': '1', 'Content-Type': 'application/json'}
        head.update(headers or {})
        request = urllib.request.Request(self.base + path, data=data,
                                         method='POST' if data is not None else 'GET', headers=head)
        try:
            with self.opener.open(request, timeout=10) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read())

    def note(self, text=SOURCE, reason='这个取景方式可以借'):
        saved = self.intake.receive({'kind': 'text', 'text': text, 'reason': reason})
        return self.intake.resolve(saved['id'])

    def link(self, url='https://example.com/pier', reason='这个开头方式可以借'):
        saved = self.intake.receive({'kind': 'link', 'url': url, 'reason': reason})
        return self.intake.resolve(saved['id'])

    def png(self, size=(12, 8)):
        from PIL import Image
        buffer = io.BytesIO()
        Image.new('RGB', size, 'blue').save(buffer, format='PNG')
        return buffer.getvalue()

    def documents(self):
        with self.store.knowledge.db() as connection:
            return {row['id'] for row in connection.execute('SELECT id FROM documents')}

    def analysis_rows(self):
        with self.cards.db() as connection:
            return {row['drop_id'] for row in connection.execute('SELECT drop_id FROM analysis')}

    def seen_rows(self):
        with self.cards.db() as connection:
            return {row['drop_id'] for row in connection.execute('SELECT drop_id FROM seen')}

    def card_rows(self):
        with self.cards.db() as connection:
            return {row['id'] for row in connection.execute('SELECT id FROM cards')}

    def rejected_rows(self):
        with self.cards.db() as connection:
            return {row['drop_id'] for row in connection.execute('SELECT drop_id FROM drops_rejected')}

    # ── 正常删 ────────────────────────────────────────────────────────────────

    def test_deleting_a_drop_removes_the_analysis_and_the_knowledge_body(self):
        drop = self.note()
        self.cards.analyze_drop(drop['id'])
        self.assertIn(drop['id'], self.analysis_rows())
        self.assertIn(drop['document_id'], self.documents())
        result = self.intake.delete(drop['id'])
        self.assertEqual((result['deleted'], result['document_removed']), (True, True))
        self.assertEqual((result['analysis_removed'], result['seen_removed']), (1, 1))
        self.assertEqual(result['document_shared_by'], 0)
        self.assertNotIn(drop['id'], self.analysis_rows())
        self.assertNotIn(drop['document_id'], self.documents())
        with self.assertRaises(ValueError):
            self.intake.detail(drop['id'])
        self.assertEqual(self.intake.listing()['items'], [])
        # 检索索引里不留悬空条目：FTS 触发器跟着 documents 一起删。
        self.assertNotIn(drop['document_id'], self.documents())
        self.assertEqual(self.cards.analysis_states([{'id': drop['id'], 'document_id': drop['document_id']}]),
                         {drop['id']: {}})

    def test_the_delete_reports_what_it_kept_and_what_it_removed(self):
        drop = self.note()
        self.cards.analyze_drop(drop['id'])
        result = self.intake.delete(drop['id'])
        self.assertTrue(result['cards_kept'])
        self.assertEqual(result['file_removed'], False)
        self.assertEqual(result['document_error'], '')
        self.assertEqual(result['ledger_error'], '')

    def test_a_pending_or_failed_drop_deletes_without_touching_any_document(self):
        saved = self.intake.receive({'kind': 'text', 'text': '还没解析的想法'})
        result = self.intake.delete(saved['id'])
        self.assertTrue(result['deleted'])
        self.assertEqual(result['document_id'], '')
        self.assertEqual(result['document_removed'], False)
        self.assertEqual(self.intake.listing()['items'], [])

    def test_an_unknown_or_malformed_id_is_refused_without_deleting_anything(self):
        drop = self.note()
        for value in ('zzzz', 'x' * 32, '', None):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    self.intake.delete(value)
        with self.assertRaises(ValueError):
            self.intake.delete('f' * 32)
        self.assertEqual(len(self.intake.listing()['items']), 1)
        self.assertEqual(self.intake.delete(drop['id'])['deleted'], True)

    # ── 共用正文的引用计数 ────────────────────────────────────────────────────

    def test_a_shared_document_is_only_deleted_with_the_last_reference(self):
        first = self.link()
        second = self.link()          # 同一链接：已存在，未重复导入
        self.assertEqual(second['outcome'], 'duplicate')
        self.assertEqual(second['document_id'], first['document_id'])
        self.assertEqual(second['document_shares'], 2)
        result = self.intake.delete(first['id'])
        self.assertEqual((result['document_shared_by'], result['document_removed']), (1, False))
        self.assertIn(first['document_id'], self.documents())
        self.assertEqual(self.intake.detail(second['id'])['document_shares'], 1)
        self.assertEqual(self.intake.detail(second['id'])['outcome'], 'duplicate')
        result = self.intake.delete(second['id'])
        self.assertTrue(result['document_removed'])
        self.assertNotIn(first['document_id'], self.documents())

    def test_the_listing_says_how_many_drops_share_each_document(self):
        self.link()
        self.link()
        solo = self.note()
        shares = {item['id']: item['document_shares'] for item in self.intake.listing()['items']}
        self.assertEqual(shares[solo['id']], 1)
        self.assertEqual(sorted(value for key, value in shares.items() if key != solo['id']), [2, 2])

    def test_a_deleted_link_can_be_collected_again_as_a_new_copy(self):
        first = self.link()
        self.intake.delete(first['id'])
        again = self.link()
        # 知识库正文已删，所以同一链接不再是「已存在」；编号仍由链接决定，这是知识库原有约定。
        self.assertEqual(again['outcome'], 'stored')
        self.assertEqual(again['document_id'], first['document_id'])
        self.assertIn(again['document_id'], self.documents())

    def test_a_shared_link_still_reports_duplicate_after_one_copy_is_deleted(self):
        first, second = self.link(), self.link()
        self.intake.delete(first['id'])
        self.assertEqual(self.link()['outcome'], 'duplicate')
        self.assertEqual(second['document_id'], self.intake.detail(second['id'])['document_id'])

    # ── 正文归谁：知识库先收藏的不会被回收站删 ──────────────────────────────

    def test_a_knowledge_first_collection_survives_deleting_the_recycle_item(self):
        url = 'https://example.com/kept-note'
        saved = self.store.knowledge.add_reference({'title': '黄总先收藏的一篇', 'url': url,
                                                   'reader_note': '这篇要留着，回头写系列'})
        doc_id = saved['id']
        hits = self.store.knowledge.search('黄总先收藏的一篇')['total']
        self.assertGreaterEqual(hits, 1)
        drop = self.link(url)
        self.assertEqual(drop['outcome'], 'duplicate')
        self.assertEqual(drop['document_id'], doc_id)
        # 呚归谁看知识库 provenance，不看本条 drop 的 duplicate 标记。
        self.assertFalse(self.intake.detail(drop['id'])['document_owned'])
        self.assertFalse([item['document_owned'] for item in self.intake.listing()['items']
                          if item['id'] == drop['id']][0])
        result = self.intake.delete(drop['id'])
        self.assertTrue(result['deleted'])
        self.assertFalse(result['document_removed'])
        self.assertFalse(result['document_owned'])
        self.assertIn('不是回收站存的', result['document_kept_reason'])
        self.assertIn(doc_id, self.documents())
        document = self.store.knowledge.document(doc_id)
        self.assertEqual(document['title'], '黄总先收藏的一篇')
        self.assertEqual(document['provenance']['reader_note'], '这篇要留着，回头写系列')
        self.assertEqual(self.store.knowledge.search('黄总先收藏的一篇')['total'], hits)

    def test_the_body_created_by_the_recycle_bin_goes_only_with_the_last_copy(self):
        url = 'https://example.com/two-copies'
        first, second = self.link(url), self.link(url)
        self.assertEqual((first['outcome'], second['outcome']), ('stored', 'duplicate'))
        self.assertEqual(second['document_id'], first['document_id'])
        self.assertTrue(self.intake.detail(first['id'])['document_owned'])
        # 第一条建、第二条复用、第一条先删：当时还有引用，正文不删。
        removed = self.intake.delete(first['id'])
        self.assertEqual((removed['document_removed'], removed['document_shared_by']), (False, 1))
        self.assertIn(first['document_id'], self.documents())
        # 建正文的那条负已不在，但 provenance 仍证明是回收站建的 → 最后一条删时不会漏下孤儿正文。
        last = self.intake.delete(second['id'])
        self.assertTrue(last['document_removed'])
        self.assertNotIn(first['document_id'], self.documents())

    def test_a_knowledge_first_body_survives_even_when_it_is_the_only_reference(self):
        url = 'https://example.com/solo-collection'
        doc_id = self.store.knowledge.add_reference({'title': '单独收藏', 'url': url})['id']
        drop = self.link(url)
        self.assertEqual(self.intake.detail(drop['id'])['document_shares'], 1)
        result = self.intake.delete(drop['id'])
        self.assertFalse(result['document_removed'])
        self.assertIn(doc_id, self.documents())

    def test_an_unreadable_provenance_keeps_the_body_and_says_so(self):
        first = self.link('https://example.com/unknown-owner')
        doc_id = first['document_id']
        with patch.object(Knowledge, 'document_provenance_facts', side_effect=RuntimeError('库读不了')):
            self.assertIsNone(self.intake.detail(first['id'])['document_owned'])
            result = self.intake.delete(first['id'])
        self.assertTrue(result['deleted'])
        self.assertFalse(result['document_removed'])
        self.assertIsNone(result['document_owned'])
        self.assertIn('出处读不到', result['document_kept_reason'])
        self.assertIn(doc_id, self.documents())

    # ── 删除与写入的竞态 ──────────────────────────────────────────────────

    def test_a_reference_committed_during_the_delete_keeps_the_body(self):
        first = self.link('https://example.com/race-doc')
        doc_id = first['document_id']
        real = self.store.knowledge.delete_document
        held, calls = [], []

        def racing(document_id, **kwargs):
            calls.append(document_id)
            # 模拟另一条同链接在计数之后、删正文之前提交了对同一篇正文的引用。
            with self.intake.db() as connection:
                connection.execute('INSERT INTO drops(id,kind,received_at,url,status,document_id)'
                                   ' VALUES(?,?,?,?,?,?)',
                                   ('f' * 32, 'link', '2026-01-01T00:00:00+08:00',
                                    'https://example.com/race-doc', 'resolved', doc_id))
            # 回收站那把锁必须还拿着（RLock 同线程可重入，所以要另开线程探）。
            probe = []

            def try_lock():
                probe.append(self.intake.lock.acquire(blocking=False))
                if probe[-1]:
                    self.intake.lock.release()

            watcher = threading.Thread(target=try_lock)
            watcher.start()
            watcher.join(2)
            held.append(probe[0] if probe else None)
            return real(document_id, **kwargs)
        with patch.object(self.store.knowledge, 'delete_document', side_effect=racing):
            result = self.intake.delete(first['id'])
        self.assertEqual(calls, [doc_id])
        self.assertEqual(held, [False])          # 删正文时回收站那把锁还拿着
        self.assertFalse(result['document_removed'])
        self.assertIn('引用', result['document_kept_reason'])
        self.assertIn(doc_id, self.documents())

    def test_analysis_never_writes_a_row_for_a_drop_whose_parent_row_is_gone(self):
        drop = self.note()
        self.cards.analyze_drop(drop['id'])
        self.assertIn(drop['id'], self.analysis_rows())
        self.intake.delete(drop['id'])
        self.assertNotIn(drop['id'], self.analysis_rows())
        stale = dict(drop)
        # 就算进程内的存在性检查诤了，事务里的父行检查也不会把已删回收项的分析写回来。
        with patch.object(intake_store.Intake, 'exists', return_value=True):
            with self.assertRaisesRegex(ValueError, '已删除'):
                self.cards._write_analysis(stale, 'done', data=READING)
        self.assertNotIn(drop['id'], self.analysis_rows())

    def test_group_card_cleanup_refuses_when_only_the_python_check_thinks_the_source_is_gone(self):
        drop = self.note()
        self.cards.analyze_drop(drop['id'])
        kept = [item['id'] for item in self.cards.listing()['items'] if item['drop_id'] == drop['id']]
        self.assertTrue(kept)
        with patch.object(intake_store.Intake, 'detail', side_effect=ValueError('回收项不存在')), \
                patch.object(intake_store.Intake, 'exists', return_value=False):
            with self.assertRaisesRegex(ValueError, '还在'):
                self.cards.delete_deleted_source(drop['id'])
        self.assertEqual([item['id'] for item in self.cards.listing()['items'] if item['drop_id'] == drop['id']],
                         kept)

    def test_group_card_cleanup_still_works_once_the_drop_is_really_gone(self):
        drop = self.note()
        self.cards.analyze_drop(drop['id'])
        self.intake.delete(drop['id'])
        cleaned = self.cards.delete_deleted_source(drop['id'])
        self.assertGreater(cleaned['deleted_cards'], 0)
        self.assertFalse([item for item in self.cards.listing()['items'] if item['drop_id'] == drop['id']])

    # ── 进行中的条目也能删，收尾不得写回 ──────────────────────────────────────

    def test_the_knowledge_side_rechecks_ownership_before_the_body_is_deleted(self):
        first = self.link('https://example.com/owner-flip')
        doc_id = first['document_id']
        real = self.store.knowledge.delete_document

        def flip(document_id, **kwargs):
            # 知识库侧的独立提交：回收站读出 owned=True 之后、删正文之前，归属被收回。
            with self.store.knowledge.db() as connection:
                connection.execute("UPDATE documents SET provenance=json_set(provenance,'$.intake_channel','工作台收藏登记')"
                                   ' WHERE id=?', (document_id,))
            return real(document_id, **kwargs)

        with patch.object(self.store.knowledge, 'delete_document', side_effect=flip):
            result = self.intake.delete(first['id'])
        self.assertTrue(result['deleted'])
        self.assertFalse(result['document_removed'])
        self.assertIn('出处已不是回收站渠道', result['document_kept_reason'])
        self.assertEqual(result['document_channel_now'], '工作台收藏登记')
        self.assertIn(doc_id, self.documents())
        self.assertEqual(self.store.knowledge.document(doc_id)['provenance']['intake_channel'], '工作台收藏登记')

    def test_a_reference_committed_by_another_database_connection_still_keeps_the_body(self):
        first = self.link('https://example.com/cross-connection')
        doc_id = first['document_id']
        real = self.store.knowledge.delete_document

        def racing(document_id, **kwargs):
            # 不经过回收站锁的第二个真连接，在删正文前提交一条同文档引用。
            connection = sqlite3.connect(str(self.intake.path))
            try:
                connection.execute('INSERT INTO drops(id,kind,received_at,url,status,document_id) VALUES(?,?,?,?,?,?)',
                                   ('e' * 32, 'link', '2026-01-01T00:00:00+08:00',
                                    'https://example.com/cross-connection', 'resolved', doc_id))
                connection.commit()
            finally:
                connection.close()
            return real(document_id, **kwargs)

        with patch.object(self.store.knowledge, 'delete_document', side_effect=racing):
            result = self.intake.delete(first['id'])
        self.assertFalse(result['document_removed'])
        self.assertIn('引用', result['document_kept_reason'])
        self.assertIn(doc_id, self.documents())

    def test_the_reference_and_ownership_guard_run_in_one_explicit_transaction(self):
        first = self.link('https://example.com/one-shot-guard')
        seen = []
        original = Knowledge.db

        class Spy:
            def __init__(self, connection):
                self.connection = connection

            def execute(self, statement, parameters=()):
                cursor = self.connection.execute(statement, parameters)
                seen.append((' '.join(statement.split()), self.connection.in_transaction))
                return cursor

            def __getattr__(self, name):
                return getattr(self.connection, name)

        @contextlib.contextmanager
        def spying(self):
            with original(self) as connection:
                yield Spy(connection)

        with patch.object(Knowledge, 'db', spying):
            result = self.intake.delete(first['id'])
        self.assertTrue(result['document_removed'])
        deletes = [entry for entry in seen if entry[0].startswith('DELETE FROM documents')]
        self.assertEqual(len(deletes), 1)
        statement, in_transaction = deletes[0]
        self.assertTrue(in_transaction)                       # 删时已在显式事务里
        self.assertIn(("BEGIN IMMEDIATE", True), seen)
        # 引用与归属都在同一条条件 DML 的 WHERE 里，不是先 COUNT 再 DELETE。
        self.assertIn("json_extract(provenance,'$.intake_channel')=?", statement)
        self.assertIn('NOT EXISTS (SELECT 1 FROM ref."drops" WHERE document_id=?)', statement)
        before = seen[:seen.index(deletes[0])]
        self.assertFalse([entry for entry in before if entry[0].startswith('SELECT COUNT(*) FROM ref')])

    def test_a_delete_between_the_done_write_and_mark_seen_leaves_no_seen_row(self):
        drop = self.note()
        real = self.cards._write_analysis

        def write_then_delete(record, status, **fields):
            state = real(record, status, **fields)
            if status == 'done':
                self.intake.delete(record['id'])
            return state

        with patch.object(self.cards, '_write_analysis', side_effect=write_then_delete):
            self.cards.analyze_drop(drop['id'])
        self.assertNotIn(drop['id'], self.analysis_rows())
        self.assertNotIn(drop['id'], self.seen_rows())

    def test_a_deleted_drop_blocks_late_card_rejection_and_seen_writes(self):
        drop = self.note()
        document, refs = self.cards.evidence(drop)
        self.intake.delete(drop['id'])
        card = {'prompt': '旧码头黄昏，蹲拍视角，水面倒映天空', 'model': 'qi21', 'scene': '公众号头图', 'refs': ['e0']}
        self.assertIsNone(self.cards.save_card('r' * 32, drop, document, card, refs, 'synthetic'))
        self.assertFalse(self.cards.record_rejected('r' * 32, drop, '测试理由', {}))
        self.assertFalse(self.cards.mark_seen(drop, 'fingerprint'))
        self.assertEqual(self.card_rows(), set())
        self.assertEqual(self.rejected_rows(), set())
        self.assertEqual(self.seen_rows(), set())

    def test_the_scheduled_extraction_stops_writing_when_the_drop_goes_while_the_model_runs(self):
        drop = self.note()

        def model(identity, prompt, system, cancel, config, deadline):
            self.calls.append(identity)
            self.intake.delete(drop['id'])        # 模型返回前后黄总把它删了
            return json.dumps({'cards': [CARD]}, ensure_ascii=False)

        self.cards.call_model = model
        receipt = json.loads(self.cards.analyze('r' * 32, {'body': ''}, threading.Event(), {'model': 'mock'},
                                                time.monotonic() + 300)[2])
        self.assertEqual(receipt['checked'], 0)
        self.assertEqual(receipt['cards'], 0)
        self.assertEqual(self.card_rows(), set())
        self.assertEqual(self.seen_rows(), set())
        self.assertEqual(self.rejected_rows(), set())

    def test_deleting_while_an_analysis_is_running_does_not_write_it_back(self):
        gate = threading.Event()
        started = threading.Event()

        def blocked(identity, prompt, system, cancel, config, deadline):
            started.set()
            gate.wait(timeout=8)
            return self.answers['read']

        self.cards.call_model = blocked
        drop = self.note()
        self.cards.request_analysis(drop['id'])
        self.assertTrue(started.wait(timeout=5))
        self.assertEqual(self.cards._analysis_row(drop['id'])['status'], 'running')
        result = self.intake.delete(drop['id'])
        self.assertTrue(result['deleted'])
        gate.set()
        self.settle()
        self.assertEqual(self.analysis_rows(), set())
        self.assertEqual(self.intake.listing()['items'], [])
        self.assertEqual(self.cards.analysis_workers, {})

    def test_deleting_while_media_recognition_is_running_leaves_nothing_behind(self):
        gate, started = threading.Event(), threading.Event()

        def blocked(path, record):
            started.set()
            gate.wait(timeout=8)
            return {'status': 'succeeded', 'label': 'AI 描述，未核实', 'text': '一张合成图', 'model': 'fake'}

        drop = self.intake.receive_file('image', 'shot.png', '合成图', io.BytesIO(self.png()), len(self.png()))
        with patch('intake_media_recognition.MediaRecognizer.recognize_image', side_effect=blocked):
            self.intake.resolve(drop['id'])
            self.assertTrue(started.wait(timeout=5))
            result = self.intake.delete(drop['id'])
            gate.set()
            self.settle()
        self.assertTrue(result['deleted'])
        self.assertTrue(result['file_removed'])
        self.assertEqual(self.intake.listing()['items'], [])
        self.assertEqual(self.analysis_rows(), set())
        self.assertEqual(self.intake.recognition_workers, {})

    # ── 本机媒体文件 ──────────────────────────────────────────────────────────

    def test_a_local_media_file_is_deleted_with_the_drop(self):
        drop = self.intake.receive_file('image', 'shot.png', '合成图', io.BytesIO(self.png()), len(self.png()))
        self.intake.resolve(drop['id'])
        path = self.intake.file_path(drop['id'])
        self.assertTrue(path.is_file())
        result = self.intake.delete(drop['id'])
        self.assertTrue(result['file_removed'])
        self.assertFalse(path.exists())
        self.assertEqual(list((Path(self.tmp.name) / 'inspiration' / 'media').glob(drop['id'] + '*')), [])

    def test_a_missing_media_file_is_reported_not_invented(self):
        drop = self.intake.receive_file('image', 'shot.png', '合成图', io.BytesIO(self.png()), len(self.png()))
        self.intake.resolve(drop['id'])
        self.intake.file_path(drop['id']).unlink()
        result = self.intake.delete(drop['id'])
        self.assertTrue(result['deleted'])
        self.assertEqual(result['file_removed'], False)
        self.assertIn('已不在', result['file_error'])

    # ── 卡片只标记来源已删除 ──────────────────────────────────────────────────

    def test_cards_survive_and_say_their_source_is_deleted(self):
        drop = self.note()
        self.cards.analyze_drop(drop['id'])
        card_id = self.cards.listing()['items'][0]['id']
        self.intake.delete(drop['id'])
        card = self.cards.detail(card_id)
        self.assertEqual(card['source_state'], 'missing')
        self.assertEqual(card['source']['deleted'], True)
        self.assertEqual(card['source']['kind_label'], '回收项已删除')
        self.assertEqual(card['source']['knowledge_url'], '')
        self.assertEqual(card['source']['title'], '')
        # 卡片页不能出现指向已删正文的链接，也不能让整页读不出来。
        self.assertEqual(self.cards.listing()['total'], 1)
        with self.assertRaises(ValueError):
            self.cards.prompt_card(card_id)
        with self.assertRaises(ValueError):
            self.cards.decide(card_id, 'confirm')

    def test_a_card_keeps_working_while_a_shared_document_still_exists(self):
        first, second = self.link(), self.link()
        self.cards.analyze_drop(first['id'])
        card_id = self.cards.listing()['items'][0]['id']
        self.cards.decide(card_id, 'confirm')
        self.intake.delete(first['id'])
        card = self.cards.detail(card_id)
        self.assertEqual(card['source']['deleted'], True)
        # 正文还在（第二条还引用着），所以来源核对仍然成立。
        self.assertEqual(card['source_state'], 'verified')
        self.assertEqual(self.cards.prompt_card(card_id)['prompt'], CARD['prompt'])
        self.intake.delete(second['id'])
        self.assertEqual(self.cards.detail(card_id)['source_state'], 'missing')
        with self.assertRaises(ValueError):
            self.cards.prompt_card(card_id)

    def test_the_scheduled_extraction_skips_a_drop_deleted_after_it_was_queued(self):
        first = self.note()
        second = self.note('另一条资料，排队后被删。')
        self.assertEqual(self.cards.pending()[1], 2)
        self.intake.delete(second['id'])
        outcome, _, data = self.cards.analyze('r' * 32, {'body': ''}, threading.Event(),
                                              {'model': 'synthetic'}, time.monotonic() + 300)
        self.assertEqual(json.loads(data)['checked'], 1)
        self.assertNotEqual(outcome, 'timeout')
        with self.cards.db() as connection:
            # 已删的那条不写提炼指纹，也不留卡片。
            self.assertEqual({row['drop_id'] for row in connection.execute('SELECT drop_id FROM seen')},
                             {first['id']})
            self.assertEqual({row['drop_id'] for row in connection.execute('SELECT drop_id FROM cards')},
                             {first['id']})

    def test_a_locked_card_database_still_loses_the_analysis_records(self):
        # ATTACH 卡不上（卡库正被后台写锁住）时不能弄挂删除，也不能留分析记录。
        drop = self.note()
        self.cards.analyze_drop(drop['id'])
        self.intake._delete_records = lambda connection, drop_id: {
            'attached': False, 'analysis': 0, 'seen': 0, 'error': 'database is locked'}
        result = self.intake.delete(drop['id'])
        self.assertTrue(result['deleted'])
        self.assertIn('locked', result['records_error'])
        self.assertEqual(result['analysis_removed'], 1)
        self.assertEqual(self.analysis_rows(), set())
        self.assertEqual(self.intake.listing()['items'], [])

    # ── 对外接口 ──────────────────────────────────────────────────────────────

    def test_the_api_deletes_a_drop_and_the_list_loses_it(self):
        drop = self.note()
        code, result = self.api('/api/intake/%s/delete' % drop['id'], {})
        self.assertEqual(code, 200)
        self.assertTrue(result['deleted'])
        code, listing = self.api('/api/intake')
        self.assertEqual(listing['items'], [])

    def test_the_delete_route_refuses_a_missing_header_and_extra_fields(self):
        drop = self.note()
        code, body = self.api('/api/intake/%s/delete' % drop['id'], {},
                              headers={'X-Article-Studio': '0'})
        self.assertEqual(code, 403)
        code, body = self.api('/api/intake/%s/delete' % drop['id'], {'force': True})
        self.assertEqual(code, 400)
        self.assertIn('不接受额外字段', body['error'])
        code, body = self.api('/api/intake/%s/delete' % ('e' * 32), {})
        self.assertEqual(code, 400)
        self.assertIn('不存在', body['error'])
        self.assertEqual(len(self.intake.listing()['items']), 1)

    def test_a_deleted_drop_answers_gone_for_detail_and_analysis(self):
        drop = self.note()
        self.api('/api/intake/%s/delete' % drop['id'], {})
        self.assertEqual(self.api('/api/intake/' + drop['id'])[0], 400)
        self.assertEqual(self.api('/api/intake/%s/analyze' % drop['id'], {})[0], 400)
        self.assertEqual(self.api('/api/intake/%s/resolve' % drop['id'], {})[0], 400)

    # ── 情报侧的链接痕迹 ──────────────────────────────────────────────────────

    def test_the_link_ledger_is_cleaned_only_when_nothing_else_uses_the_link(self):
        from server import wire_intake_links
        wire_intake_links(self.store)
        first = self.link(WECHAT_LINK)
        second = self.link(WECHAT_LINK)
        self.store.intel_recommend.collect()
        with self.store.intel.db() as connection:
            # 两条回收项 + 知识库正文：同一链接三个来源，链接只算一条。
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM link_index').fetchone()[0], 1)
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM recommendation_sources').fetchone()[0], 3)
            self.assertEqual(connection.execute('SELECT count FROM recommendations').fetchone()[0], 3)
        self.intake.delete(first['id'])
        with self.store.intel.db() as connection:
            # 第二条回收项还在用这条链接：link_index 行保留，次数按剩下的来源重算。
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM link_index').fetchone()[0], 1)
            self.assertEqual(connection.execute('SELECT count FROM recommendations').fetchone()[0], 2)
        result = self.intake.delete(second['id'])
        self.assertEqual(result['ledger_error'], '')
        with self.store.intel.db() as connection:
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM link_index').fetchone()[0], 0)
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM recommendation_sources').fetchone()[0], 0)
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM recommendations').fetchone()[0], 0)

    def test_an_intel_article_stops_pointing_at_a_deleted_drop(self):
        from server import wire_intake_links
        wire_intake_links(self.store)
        with self.store.intel.db() as connection:
            connection.execute('''INSERT INTO articles(id,fakeid,aid,link,title,body_status,body,added_at)
                VALUES(?,?,?,?,?,?,?,?)''',
                               ('a' * 32, 'MzA5Mg==', 'aid1', WECHAT_LINK, '公众号的一篇', 'awaiting_confirm',
                                '公众号正文', '2026-10-09T00:00:00+00:00'))
        pushed = self.store.intel.push_to_intake('a' * 32, self.intake)
        drop_id = pushed['drop_id']
        with self.store.intel.db() as connection:
            self.assertEqual(connection.execute('SELECT drop_id FROM articles WHERE id=?',
                                                ('a' * 32,)).fetchone()[0], drop_id)
        self.intake.delete(drop_id)
        with self.store.intel.db() as connection:
            self.assertEqual(connection.execute('SELECT drop_id FROM articles WHERE id=?',
                                                ('a' * 32,)).fetchone()[0], '')
        # 清掉标记后同一篇可以重新送入回收站，不会卡在「已进回收站」。
        self.assertEqual(self.store.intel.push_to_intake('a' * 32, self.intake)['drop_id'], drop_id)
        self.intake.delete(drop_id)

    def test_a_workspace_without_the_intel_database_deletes_without_creating_one(self):
        from server import wire_intake_links
        wire_intake_links(self.store)
        drop = self.link(WECHAT_LINK)
        result = self.intake.delete(drop['id'])
        self.assertTrue(result['deleted'])
        self.assertEqual(result['ledger_error'], '')
        self.assertFalse((Path(self.tmp.name) / 'intel' / 'intel.db').exists())

    def test_a_broken_ledger_hook_is_reported_instead_of_hidden(self):
        self.intake.link_ledger = lambda links, drop_id: (_ for _ in ()).throw(ValueError('情报库读不到'))
        drop = self.link(WECHAT_LINK)
        result = self.intake.delete(drop['id'])
        self.assertTrue(result['deleted'])
        self.assertIn('情报库读不到', result['ledger_error'])


if __name__ == '__main__':
    unittest.main()
