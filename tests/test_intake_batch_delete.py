"""回收项批量删除：一次最多 50 条，逐条走单条删除的同一套规则，逐条回执。

覆盖合同点名的场景：批量删除、上限、部分失败不回滚、知识库先收藏的正文不被误删、
共用正文只在最后一条选中项时删、预览与删除口径一致、提示词卡只计数不删。
全部用假抓取与假模型：不联网、不调真实模型、只用临时目录。
"""
import io
import json
import sqlite3
import traceback
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
import intel_recommend
from tests.intake_dependency_patches import guard_intake_dependencies, patch_intake_dependency
from intake_cards import IntakeCards
from intake_store import Intake
from knowledge_store import Knowledge
from server import Handler, Store, ThreadingHTTPServer

SOURCE = ('黄昏的旧码头先把木头泡软，绳索在风里轻轻晃，远处渡船只留一条灯线。'
          '拍照的人蹲下来，让水面把天空再复制一遍，人物只留一个背影，不露脸。')
# 合法 __biz 的公众号链接：collect() 能免费解析出号，不联网。
WECHAT_LINK = 'https://mp.weixin.qq.com/s?__biz=MzA5Mg%3D%3D&mid=264&idx=1&sn=abc123'


def png_bytes(size=(24, 16), colour='red'):
    from PIL import Image
    buffer = io.BytesIO()
    Image.new('RGB', size, colour).save(buffer, format='PNG')
    return buffer.getvalue()


class FakeWeb:
    def __init__(self, markdown=SOURCE):
        self.markdown, self.calls = markdown, []

    def __call__(self):
        return self

    def fetch(self, args, job):
        self.calls.append(dict(args))
        return {'url': args['url'], 'title': '合成页面标题', 'markdown': self.markdown, 'start': 0,
                'total_chars': len(self.markdown), 'truncated': False, 'next_start': None}


class BatchFixture(unittest.TestCase):
    """一套造数据的手：链接、文字、图片、组合各来一条，卡直接写进卡库。"""

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
        self.fake = patch_intake_dependency(self, "WebTools", FakeWeb())
        self.media = patch('intake_media_recognition.MediaRecognizer.recognize_video',
                           return_value={'status': 'no_audio', 'scope': '视频无音轨'})
        self.media.start()
        self.addCleanup(self.media.stop)
        self.counter = iter(range(1, 500))
        self.used = set()

    def tearDown(self):
        for worker in list(self.intake.recognition_workers.values()):
            worker.join(timeout=5)
        self.tmp.cleanup()

    def new_id(self):
        value = format(next(self.counter), '032x')
        self.used.add(value)
        return value

    def link(self, url, reason=''):
        saved = self.intake.receive({'id': self.new_id(), 'kind': 'link', 'url': url, 'reason': reason})
        return self.intake.resolve(saved['id'])

    def note(self, text):
        saved = self.intake.receive({'id': self.new_id(), 'kind': 'text', 'text': text})
        return self.intake.resolve(saved['id'])

    def image(self, name='shot.png'):
        blob = png_bytes()
        staged = self.intake.stage_file('image', name, io.BytesIO(blob), len(blob))
        return self.intake.receive({'id': self.new_id(), 'kind': 'image', 'file_token': staged['token']})

    def picture(self, name='shot.png'):
        return self.intake.resolve(self.image(name)['id'])

    def combo(self, url, name='shot.png'):
        blob = png_bytes()
        staged = self.intake.stage_file('image', name, io.BytesIO(blob), len(blob))
        saved = self.intake.receive({'id': self.new_id(), 'kind': 'link', 'url': url, 'file_token': staged['token']})
        return self.intake.resolve(saved['id'])

    def card(self, drop, prompt='旧码头黄昏，蹲拍视角，水面倒映天空'):
        return self.cards.save_card('run-batch', {'id': drop['id'], 'reason': drop.get('reason') or ''},
                                    {'id': drop['document_id']},
                                    {'prompt': prompt, 'model': 'qi21', 'scene': '公众号头图', 'refs': ['e0']},
                                    {'e0': drop.get('url') or 'local'}, 'synthetic')

    def documents(self):
        with self.store.knowledge.db() as connection:
            return {row['id'] for row in connection.execute('SELECT id FROM documents')}

    def card_rows(self):
        return self._rows('cards')

    def _rows(self, table):
        path = Path(self.tmp.name) / 'inspiration' / 'cards.db'
        if not path.is_file():
            return []
        connection = sqlite3.connect(path)
        connection.row_factory = sqlite3.Row
        try:
            return [dict(row) for row in connection.execute('SELECT * FROM %s' % table)]
        except sqlite3.Error:
            return []
        finally:
            connection.close()


class BatchDeleteTests(BatchFixture):
    # ── 正常批量 ──────────────────────────────────────────────────────────────

    def test_a_batch_deletes_every_kind_and_reports_each_one(self):
        one = self.link('https://example.com/one')
        two = self.note('一段想法，先收着。')
        three = self.picture('dock.png')
        four = self.combo('https://example.com/four')
        self.assertEqual([item['display_kind'] for item in self.intake.listing()['items']],
                         ['combo', 'image', 'text', 'link'])
        result = self.intake.delete_many([four['id'], three['id'], two['id'], one['id']])
        self.assertEqual(result['schema'], 'intake-delete-many/v1')
        self.assertEqual([item['id'] for item in result['deleted']], [four['id'], three['id'], two['id'], one['id']])
        self.assertTrue(all(item['ok'] and item['deleted'] for item in result['deleted']))
        self.assertEqual(result['skipped'], [])
        summary = result['summary']
        self.assertEqual((summary['requested'], summary['deleted'], summary['skipped']), (4, 4, 0))
        self.assertEqual(summary['documents_removed'], 4)
        self.assertEqual(summary['files_removed'], 2)          # 图片 + 组合带的那张
        self.assertEqual(summary['cards_kept'], 0)
        self.assertEqual(summary['cards_kept_drops'], 4)
        self.assertEqual(self.intake.listing()['items'], [])
        self.assertEqual(self.documents(), set())
        self.assertEqual(list(Path(self.tmp.name, 'inspiration', 'media').glob('*')), [])

    def test_the_preview_says_the_same_thing_the_delete_does(self):
        one = self.link('https://example.com/preview-one')
        two = self.picture('preview.png')
        three = self.combo('https://example.com/preview-three')
        self.card(one)
        self.card(three)
        preview = self.intake.delete_preview([one['id'], two['id'], three['id']])
        self.assertEqual(preview['schema'], 'intake-delete-preview/v1')
        self.assertEqual(preview['count'], 3)
        self.assertEqual(preview['kinds'], {'link': 1, 'image': 1, 'combo': 1})
        self.assertEqual(preview['files'], 2)
        self.assertEqual(preview['documents'], {'total': 3, 'would_remove': 3, 'kept': 0, 'kept_shared': 0,
                                                'kept_not_owned': 0, 'kept_unknown': 0})
        self.assertEqual(preview['cards'], {'drops': 2, 'cards': 2})
        self.assertEqual(preview['missing'], [])
        self.assertEqual(self.intake.listing()['items'] and len(self.intake.listing()['items']), 3)  # 预览不改数据
        result = self.intake.delete_many([one['id'], two['id'], three['id']])
        self.assertEqual(result['summary']['documents_removed'], preview['documents']['would_remove'])
        self.assertEqual(result['summary']['files_removed'], preview['files'])

    def test_the_preview_counts_a_body_that_only_the_last_pick_would_remove(self):
        url = 'https://example.com/shared-in-batch'
        first, second = self.link(url), self.link(url)
        outside = self.link('https://example.com/not-picked')
        preview = self.intake.delete_preview([first['id'], second['id']])
        self.assertEqual(preview['documents'], {'total': 1, 'would_remove': 1, 'kept': 0, 'kept_shared': 0,
                                                'kept_not_owned': 0, 'kept_unknown': 0})
        result = self.intake.delete_many([first['id'], second['id']])
        self.assertEqual([(item['document_removed'], item['document_shared_by']) for item in result['deleted']],
                         [(False, 1), (True, 0)])
        self.assertEqual(result['summary']['documents_removed'], 1)
        self.assertEqual(result['summary']['documents_kept'], 0)
        self.assertNotIn(first['document_id'], self.documents())
        self.assertIn(outside['document_id'], self.documents())

    def test_a_body_still_referenced_by_an_unpicked_item_is_promised_kept(self):
        url = 'https://example.com/kept-by-unpicked'
        picked, unpicked = self.link(url), self.link(url)
        preview = self.intake.delete_preview([picked['id']])
        self.assertEqual(preview['documents']['would_remove'], 0)
        self.assertEqual(preview['documents']['kept_shared'], 1)
        result = self.intake.delete_many([picked['id']])
        self.assertFalse(result['deleted'][0]['document_removed'])
        self.assertIn('还有 1 条回收项引用', result['deleted'][0]['document_kept_reason'])
        self.assertIn(picked['document_id'], self.documents())
        self.assertTrue(self.intake.exists(unpicked['id']))

    # ── 知识库先收藏的正文 ────────────────────────────────────────────────────

    def test_a_body_collected_in_the_knowledge_base_first_survives_a_batch(self):
        url = 'https://example.com/collected-first'
        doc_id = self.store.knowledge.add_reference({'title': '黄总先收藏的一篇', 'url': url,
                                                     'reader_note': '这篇要留着'})['id']
        hits = self.store.knowledge.search('黄总先收藏的一篇')['total']
        collected = self.link(url)
        self.assertEqual(collected['outcome'], 'duplicate')
        self.assertFalse(collected['document_owned'])
        other = self.note('另一条回收项，正文是回收站自己存的。')
        preview = self.intake.delete_preview([collected['id'], other['id']])
        self.assertEqual(preview['documents']['would_remove'], 1)
        self.assertEqual(preview['documents']['kept_not_owned'], 1)
        result = self.intake.delete_many([collected['id'], other['id']])
        kept = next(item for item in result['deleted'] if item['id'] == collected['id'])
        self.assertTrue(kept['deleted'])
        self.assertFalse(kept['document_removed'])
        self.assertIn('不是回收站存的', kept['document_kept_reason'])
        self.assertIn(doc_id, self.documents())
        self.assertEqual(self.store.knowledge.document(doc_id)['provenance']['reader_note'], '这篇要留着')
        self.assertEqual(self.store.knowledge.search('黄总先收藏的一篇')['total'], hits)

    # ── 上限、入参与部分失败 ──────────────────────────────────────────────────

    def test_the_batch_refuses_more_than_fifty_ids_and_keeps_every_item(self):
        items = [self.note('第 %d 段想法' % n) for n in range(3)]
        for ids in ([item['id'] for item in items] + ['f' * 32] * 48, [], 'not-a-list', {'id': items[0]['id']}):
            with self.assertRaises(ValueError) as bad:
                self.intake.delete_many(ids)
            self.assertIn('1–50', str(bad.exception))
            with self.assertRaises(ValueError):
                self.intake.delete_preview(ids)
        self.assertEqual(len(self.intake.listing()['items']), 3)

    def test_a_bad_or_repeated_id_is_skipped_with_its_own_reason(self):
        one, two = self.note('要删的一条'), self.note('也要删的一条')
        result = self.intake.delete_many([one['id'], one['id'], 'not-an-id', '', two['id']])
        self.assertEqual([item['id'] for item in result['deleted']], [one['id'], two['id']])
        reasons = {item['id']: item['reason'] for item in result['skipped']}
        self.assertEqual(reasons[one['id']], '同一次里重复提交，未重复删除')
        self.assertEqual(reasons[''], '编号无效，未处理')
        self.assertEqual(result['summary']['deleted'], 2)
        self.assertEqual(len(self.intake.listing()['items']), 0)

    def test_an_item_that_is_already_gone_is_skipped_without_rolling_back_the_rest(self):
        one, two, gone = self.note('一条'), self.note('二条'), self.note('三条')
        self.intake.delete(gone['id'])
        result = self.intake.delete_many([one['id'], gone['id'], two['id']])
        self.assertEqual([item['id'] for item in result['deleted']], [one['id'], two['id']])
        self.assertEqual(len(result['skipped']), 1)
        self.assertEqual(result['skipped'][0]['id'], gone['id'])
        self.assertIn('不存在', result['skipped'][0]['reason'])
        self.assertEqual(result['summary']['deleted'], 2)
        self.assertEqual(self.intake.listing()['items'], [])

    def test_a_body_deletion_that_fails_is_reported_and_the_rest_stay_deleted(self):
        one, two = self.note('正文删得掉的一条'), self.note('正文删不掉的一条')
        real = self.store.knowledge.delete_document

        def flaky(doc_id, **kwargs):
            if doc_id == two['document_id']:
                raise OSError('知识库正忙，删不动')
            return real(doc_id, **kwargs)

        with patch.object(self.store.knowledge, 'delete_document', side_effect=flaky):
            result = self.intake.delete_many([one['id'], two['id']])
        self.assertEqual([item['id'] for item in result['deleted']], [one['id'], two['id']])
        self.assertTrue(all(item['ok'] for item in result['deleted']))
        failed = next(item for item in result['deleted'] if item['id'] == two['id'])
        self.assertFalse(failed['document_removed'])
        self.assertIn('知识库正忙', failed['document_error'])
        self.assertEqual(result['summary']['documents_removed'], 1)
        self.assertEqual(result['summary']['partial'], 1)
        self.assertEqual(result['summary']['errors'][0]['id'], two['id'])
        self.assertFalse(self.intake.exists(one['id']))          # 成功的不回滚
        self.assertFalse(self.intake.exists(two['id']))
        self.assertIn(two['document_id'], self.documents())

    # ── 提示词卡 ──────────────────────────────────────────────────────────────

    def test_cards_are_counted_in_the_preview_but_never_deleted_by_the_batch(self):
        one, two = self.note('会出卡的一条'), self.note('不出卡的一条')
        self.card(one)
        self.card(one, '另一个角度：渡船灯线')
        before = self.card_rows()
        self.assertEqual(len(before), 2)
        preview = self.intake.delete_preview([one['id'], two['id']])
        self.assertEqual(preview['cards'], {'drops': 1, 'cards': 2})
        result = self.intake.delete_many([one['id'], two['id']])
        self.assertTrue(all(item['cards_kept'] for item in result['deleted']))
        self.assertEqual(result['summary']['cards_kept'], 2)
        self.assertEqual({row['id'] for row in self.card_rows()}, {row['id'] for row in before})
        self.assertEqual(self.intake.listing()['items'], [])

    def test_a_card_store_that_does_not_exist_yet_reads_as_zero(self):
        with tempfile.TemporaryDirectory() as other:
            bare = Intake(self.store.knowledge, other)          # 没接上卡库的新工作区
            saved = bare.receive({'id': format(900, '032x'), 'kind': 'text', 'text': '卡库还没建过的一条'})
            self.assertFalse((Path(other) / 'inspiration' / 'cards.db').is_file())
            preview = bare.delete_preview([saved['id']])
            self.assertEqual(preview['cards'], {'drops': 0, 'cards': 0})
            self.assertEqual(bare.delete_many([saved['id']])['summary']['analysis_removed'], 0)

    def test_a_batch_delete_clears_analysis_and_seen_rows_with_the_drop(self):
        one = self.note('要分析的一条')
        self.cards._write_analysis({'id': one['id'], 'document_id': one['document_id']}, 'done',
                                   data={'summary': '摘要'})
        self.cards.mark_seen(one, 'fp-1')
        self.assertEqual(len(self._rows('analysis')), 1)
        result = self.intake.delete_many([one['id']])
        self.assertEqual(result['summary']['analysis_removed'], 1)
        self.assertEqual(result['summary']['seen_removed'], 1)
        self.assertEqual(self._rows('analysis'), [])
        self.assertEqual(self._rows('seen'), [])

    def test_the_preview_lists_what_is_no_longer_in_the_list(self):
        one = self.note('还在的一条')
        preview = self.intake.delete_preview([one['id'], 'a' * 32])
        self.assertEqual(preview['missing'], ['a' * 32])
        self.assertEqual(preview['count'], 1)
        self.assertEqual(preview['requested'], 2)


class BatchDeleteAPITests(unittest.TestCase):
    def setUp(self):
        guard_intake_dependencies(self)
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(self.tmp.name)
        self.store._knowledge = Knowledge(self.store, sources=[])
        self.fake = patch_intake_dependency(self, "WebTools", FakeWeb())
        self.media = patch('intake_media_recognition.MediaRecognizer.recognize_video',
                           return_value={'status': 'no_audio', 'scope': '视频无音轨'})
        self.media.start()
        self.addCleanup(self.media.stop)
        self.srv = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.srv.store = self.store
        self.thread = threading.Thread(target=self.srv.serve_forever, daemon=True)
        self.thread.start()
        self.base = f'http://127.0.0.1:{self.srv.server_port}'
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        self.counter = iter(range(1, 200))

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        self.thread.join()
        intake = self.store._creative_intake
        if intake:
            for worker in list(intake.recognition_workers.values()):
                worker.join(timeout=5)
        self.tmp.cleanup()

    def api(self, path, body=None, headers=None):
        data = json.dumps(body).encode() if body is not None else None
        head = {'Origin': self.base, 'X-Article-Studio': '1', 'Content-Type': 'application/json'}
        head.update(headers or {})
        request = urllib.request.Request(self.base + path, data=data, headers=head, method='POST')
        try:
            with self.opener.open(request) as response:
                return json.loads(response.read().decode())
        except urllib.error.HTTPError as exc:
            raise AssertionError('%s %s' % (exc.code, exc.read().decode()[:200])) from None

    def get(self, path):
        request = urllib.request.Request(self.base + path, headers={'Origin': self.base})
        with self.opener.open(request) as response:
            return json.loads(response.read().decode())

    def fail(self, path, body=None, headers=None):
        data = json.dumps(body).encode() if body is not None else None
        head = {'Origin': self.base, 'X-Article-Studio': '1', 'Content-Type': 'application/json'}
        head.update(headers or {})
        request = urllib.request.Request(self.base + path, data=data, headers=head, method='POST')
        try:
            with self.opener.open(request) as response:
                return response.status, json.loads(response.read().decode())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode())

    def note(self, text):
        value = format(next(self.counter), '032x')
        saved = self.store.creative_intake.receive({'id': value, 'kind': 'text', 'text': text})
        return self.store.creative_intake.resolve(saved['id'])

    def test_the_batch_routes_delete_several_items_in_one_call(self):
        one, two = self.note('接口一条'), self.note('接口二条')
        preview = self.api('/api/intake/delete-preview', {'ids': [one['id'], two['id']]})
        self.assertEqual(preview['count'], 2)
        self.assertEqual(preview['kinds'], {'text': 2})
        self.assertEqual(preview['documents']['would_remove'], 2)
        result = self.api('/api/intake/delete-many', {'ids': [one['id'], two['id']]})
        self.assertEqual(result['summary']['deleted'], 2)
        self.assertEqual([item['id'] for item in result['deleted']], [one['id'], two['id']])
        self.assertEqual(self.get('/api/intake')['items'], [])

    def test_the_batch_routes_validate_their_payload(self):
        one = self.note('参数校验的一条')
        for payload in ({}, {'ids': []}, {'ids': 'x'}, {'ids': ['f' * 32] * 51}, {'ids': [one['id']], 'force': True}):
            self.assertEqual(self.fail('/api/intake/delete-many', payload)[0], 400, payload)
            self.assertEqual(self.fail('/api/intake/delete-preview', payload)[0], 400, payload)
        self.assertTrue(self.store.creative_intake.exists(one['id']))
        self.assertEqual(self.fail('/api/intake/delete-many', {'ids': [one['id']]},
                                   headers={'X-Article-Studio': '0'})[0], 403)
        self.assertTrue(self.store.creative_intake.exists(one['id']))

    def test_the_api_reports_a_partial_batch_honestly(self):
        one, two = self.note('接口一条'), self.note('接口二条')
        self.store.creative_intake.delete(two['id'])
        result = self.api('/api/intake/delete-many', {'ids': [one['id'], two['id']]})
        self.assertEqual(result['summary']['deleted'], 1)
        self.assertEqual(result['summary']['skipped'], 1)
        self.assertIn('不存在', result['skipped'][0]['reason'])


if __name__ == '__main__':
    unittest.main()


class BatchSummaryTests(BatchFixture):
    def test_card_count_has_distinct_units_and_unknown_is_null(self):
        one, two = self.note('甲'), self.note('乙')
        for n in range(3):
            self.card(one, '第%d张：灯塔倒影' % n)
        result = self.intake.delete_many([one['id'], two['id']])
        self.assertEqual(result['summary']['cards_kept'], 3)
        self.assertEqual(result['summary']['cards_kept_drops'], 2)
        original_connect = sqlite3.connect
        target = (self.intake.root / 'cards.db').resolve().as_uri() + '?mode=ro'

        def unreadable_target(database, *args, **kwargs):
            if str(database) == target:
                raise sqlite3.OperationalError('unreadable')
            return original_connect(database, *args, **kwargs)

        with patch('intake_store.sqlite3.connect', side_effect=unreadable_target):
            with sqlite3.connect(':memory:') as unrelated:
                self.assertEqual(unrelated.execute('SELECT 1').fetchone()[0], 1)
            self.assertIsNone(self.intake._batch_card_count([one['id']]))

    def test_missing_document_is_not_counted_as_retained(self):
        one = self.note('正文已被另一入口删除')
        self.store.knowledge.delete_document(one['document_id'])
        result = self.intake.delete_many([one['id']])
        self.assertEqual(result['summary']['documents_kept'], 0)

    def test_retained_shared_document_is_counted_only_once(self):
        rows = [self.link('https://example.com/summary-shared') for _ in range(3)]
        result = self.intake.delete_many([row['id'] for row in rows[:2]])
        self.assertEqual(result['summary']['documents_removed'], 0)
        self.assertEqual(result['summary']['documents_kept'], 1)


    def test_failed_item_still_existing_document_is_included_in_kept_count(self):
        one = self.note('删除前失败仍保留正文')
        with patch.object(self.intake, 'delete', side_effect=OSError('测试拒绝删除')):
            result = self.intake.delete_many([one['id']])
        self.assertEqual(result['summary']['deleted'], 0)
        self.assertEqual(result['summary']['skipped'], 1)
        self.assertEqual(result['summary']['documents_kept'], 1)

    def test_kept_query_failure_returns_unknown_and_keeps_the_receipt(self):
        url = 'https://example.com/summary-query-fails'
        first, second = self.link(url), self.link(url)
        original = self.intake.knowledge.db

        def fail_only_during_summary():
            if any(frame.name == '_batch_summary' for frame in traceback.extract_stack()):
                raise sqlite3.OperationalError('知识库打不开')
            return original()

        with patch.object(self.intake.knowledge, 'db', fail_only_during_summary):
            result = self.intake.delete_many([first['id']])
        self.assertEqual([item['id'] for item in result['deleted']], [first['id']])
        self.assertTrue(result['deleted'][0]['deleted'])
        self.assertIsNone(result['summary']['documents_kept'])
        self.assertEqual(result['summary']['documents_removed'], 0)
        self.assertEqual(result['summary']['deleted'], 1)
        self.assertFalse(self.intake.exists(first['id']))
        self.assertTrue(self.intake.exists(second['id']))
        self.assertIn(first['document_id'], self.documents())


class PerfDeleteTests(BatchFixture):
    """21t：删除收尾不得对 798MB 知识库逐条做 `_document_links` 全表扫描。

    方案：`coordination/plan-perf-knowledge.md` §3.2 改法 1（`live` 提升到 delete_many 批量算一次）。
    假 recommender 的 `_document_links` 只计数 + 注入 sleep，不碰真库、不联网、不调模型。
    """

    def setUp(self):
        super().setUp()
        from server import wire_intake_links
        wire_intake_links(self.store)
        with self.store.intel.db() as connection:      # 让 intel.db 存在，forget 才会真跑
            connection.execute('SELECT 1')
        self.scan_calls = []
        self.scan_delay = 0.0

        def fake_document_links(knowledge_path):
            self.scan_calls.append(str(knowledge_path))
            if self.scan_delay:
                time.sleep(self.scan_delay)
            return []

        self._scan_patcher = patch.object(intel_recommend.Recommender, '_document_links',
                                          staticmethod(fake_document_links))
        self._scan_patcher.start()
        self.addCleanup(self._scan_patcher.stop)

    def _notes(self, count):
        return [self.note('想法 %d，先收着。' % n) for n in range(count)]

    def test_delete_many_scans_knowledge_once_per_batch_not_per_drop(self):
        drops = self._notes(3)
        self.intake.delete_many([drop['id'] for drop in drops])
        self.assertEqual(len(self.scan_calls), 1,
                         '一次批量删除只应对知识库做一次链接全表扫描，实测 %d 次' % len(self.scan_calls))

    def test_delete_many_latency_is_flat_in_batch_size(self):
        self.scan_delay = 0.1
        single = self._notes(1)
        started = time.monotonic()
        self.intake.delete_many([drop['id'] for drop in single])
        one_cost = time.monotonic() - started
        batch = self._notes(3)
        started = time.monotonic()
        self.intake.delete_many([drop['id'] for drop in batch])
        three_cost = time.monotonic() - started
        self.assertLess(three_cost - one_cost, 0.15,
                        '耗时不应随条数线性增长：单条 %.3fs，三条 %.3fs' % (one_cost, three_cost))

    def test_delete_many_response_is_within_page_timeout(self):
        self.scan_delay = 0.5
        drops = self._notes(5)
        started = time.monotonic()
        result = self.intake.delete_many([drop['id'] for drop in drops])
        elapsed = time.monotonic() - started
        self.assertLess(elapsed, 1.0,
                        '5 条批量删除应是「一次扫描」量级，远小于页面 120s 超时；实测 %.2fs' % elapsed)
        self.assertEqual(result['schema'], 'intake-delete-many/v1')
        self.assertEqual((result['summary']['requested'], result['summary']['deleted'],
                          result['summary']['skipped']), (5, 5, 0))
        self.assertTrue(all(item['ok'] and item['deleted'] for item in result['deleted']))

    def test_ledger_cleanup_failure_does_not_block_delete(self):
        self.intake.link_ledger = lambda links, drop_id: (_ for _ in ()).throw(ValueError('情报库读不到'))
        drop = self.note('一条')
        result = self.intake.delete(drop['id'])
        self.assertTrue(result['deleted'])
        self.assertIn('情报库读不到', result['ledger_error'])


class LedgerReconcileTests(BatchFixture):
    """M1：批量删除用删除前 live 快照，本批才删掉的链接会残留 knowledge 来源与推荐计数。

    修法 (a)：批末用新算的 live 对本批涉及的 URL 统一对账清理一次。这里把 Codex 的合成
    场景固化成测试：同一合法 __biz URL 两条回收项，collect 后 intake=2/knowledge=1/count=3，
    delete_many 两条成功后 knowledge/count/link_index 都要清到真实状态。
    """

    def setUp(self):
        super().setUp()
        from server import wire_intake_links
        wire_intake_links(self.store)

    def ledger_counts(self):
        with self.store.intel.db() as connection:
            def count(sql):
                return connection.execute(sql).fetchone()[0]
            return {
                'intake': count("SELECT COUNT(*) FROM recommendation_sources WHERE source_kind='intake'"),
                'knowledge': count("SELECT COUNT(*) FROM recommendation_sources WHERE source_kind='knowledge'"),
                'links': count('SELECT COUNT(*) FROM link_index'),
                'recs': count('SELECT COUNT(*) FROM recommendations'),
                'count': count('SELECT COALESCE(SUM(count),0) FROM recommendations'),
            }

    def test_batch_delete_reconciles_shared_link_after_the_batch(self):
        first = self.link(WECHAT_LINK)
        second = self.link(WECHAT_LINK)
        self.store.intel_recommend.collect()
        before = self.ledger_counts()
        self.assertEqual((before['intake'], before['knowledge'], before['count']), (2, 1, 3),
                         '合成场景前置：两条 intake 来源 + 一条 knowledge 来源，次数 3；实测 %r' % before)
        result = self.intake.delete_many([first['id'], second['id']])
        self.assertEqual(result['summary']['documents_removed'], 1)
        after = self.ledger_counts()
        self.assertEqual(after['knowledge'], 0, '批末对账后知识来源要清掉：%r' % after)
        self.assertEqual(after['count'], 0, '推荐次数要按真实来源归零：%r' % after)
        self.assertEqual(after['links'], 0, '两库都没有的链接 link_index 要清掉：%r' % after)
        self.assertEqual(after['recs'], 0, '没来源且未处理的推荐行要删掉：%r' % after)
        self.assertEqual(result['ledger_error'], '')

    def test_single_item_batch_delete_reconciles_the_same_way(self):
        only = self.link(WECHAT_LINK)
        self.store.intel_recommend.collect()
        before = self.ledger_counts()
        self.assertEqual((before['intake'], before['knowledge'], before['count']), (1, 1, 2),
                         '单条合成场景前置：1 条 intake + 1 条 knowledge，次数 2；实测 %r' % before)
        result = self.intake.delete_many([only['id']])
        self.assertEqual(result['summary']['documents_removed'], 1)
        after = self.ledger_counts()
        self.assertEqual((after['intake'], after['knowledge'], after['count'], after['links'], after['recs']),
                         (0, 0, 0, 0, 0), '单条 delete_many 同样要对账清干净：%r' % after)
