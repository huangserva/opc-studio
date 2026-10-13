"""知识库「移到已删除」的 HTTP 接口（第 2 阶段后端）。

隔离：全部 TemporaryDirectory 合成库 + 本机回环端口，不碰正式 knowledge.db、不联网、不调模型。
"""
import hashlib
import json
import sqlite3
import tempfile
import threading
import unittest
import unittest.mock
import urllib.error
import urllib.parse
import urllib.request
from http.client import RemoteDisconnected

import server as server_module
from knowledge_store import Knowledge
from server import Handler, Store, ThreadingHTTPServer

GONE = 'f' * 32
HASH64 = '0' * 64


class TrashAPITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = self.temp.name
        self.store = Store(self.root)
        self.store._knowledge = Knowledge(self.store, sources=[])
        self.srv = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.srv.store = self.store
        self.thread = threading.Thread(target=self.srv.serve_forever, daemon=True)
        self.thread.start()
        self.base = 'http://127.0.0.1:%d' % self.srv.server_port
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        self.knowledge = self.store.knowledge
        self.docs = [self.knowledge.add_note({'title': '资料 %d' % i, 'body': '正文 %d 的内容' % i})
                     for i in range(3)]

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        self.thread.join()
        self.temp.cleanup()

    # ── 小工具 ────────────────────────────────────────────────────────────────

    def call(self, path, body=None, headers=None, raw=None, method=None):
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        head = {'Origin': self.base, 'X-Article-Studio': '1'}
        if raw is None and data is not None:
            head['Content-Type'] = 'application/json'
        head.update(headers or {})
        request = urllib.request.Request(self.base + path, data=data, headers=head,
                                         method=method or ('POST' if data is not None else 'GET'))
        try:
            with self.opener.open(request) as response:
                return response.status, json.loads(response.read() or b'null')
        except urllib.error.HTTPError as error:
            code, payload = error.code, json.loads(error.read() or b'null')
            error.close()
            return code, payload

    def preview(self, ids, **kwargs):
        return self.call('/api/knowledge/trash/preview', {'ids': ids}, **kwargs)

    def commit(self, items, confirm=True, **kwargs):
        """items 传 {'id':..,'hash':..} 或已成形条目；confirm=None 表示不带该字段。"""
        payload = {'items': [{'id': item['id'], 'expected_hash': item.get('hash', HASH64)} for item in items]}
        if confirm is not None:
            payload['confirm'] = confirm
        return self.call('/api/knowledge/trash', payload, **kwargs)

    def restore(self, ids, **kwargs):
        return self.call('/api/knowledge/trash/restore', {'ids': ids}, **kwargs)

    def trashed_ids(self):
        with self.knowledge.db() as c:
            return {row[0] for row in c.execute('SELECT document_id FROM document_trash')}

    # ── 预览与取消：只读 ──────────────────────────────────────────────────────

    def test_preview_is_read_only_and_reports_state_references_and_semantics(self):
        first = self.docs[0]
        before = self.knowledge.status()['total']
        status, receipt = self.preview([first['id'], GONE])
        self.assertEqual(status, 200)
        self.assertEqual(receipt['schema'], 'knowledge-trash-preview/v1')
        self.assertEqual(receipt['summary'], {'requested': 2, 'ready': 1, 'already_trashed': 0, 'missing': 1})
        self.assertIn('原文件与 remio 笔记不动', receipt['semantics'])
        self.assertEqual(receipt['items'][0]['hash'], first['hash'])
        self.assertEqual(receipt['items'][0]['references']['counted']['recycle_drops'], None,
                         '没有回收项库时如实是 unknown，不显示 0')
        self.assertEqual(self.trashed_ids(), set(), '预览零写入')
        self.assertEqual(self.knowledge.status()['total'], before)

    def test_cancel_without_confirm_is_refused_and_writes_nothing(self):
        first = self.docs[0]
        for payload in ({'items': [{'id': first['id'], 'expected_hash': first['hash']}]},
                        {'items': [{'id': first['id'], 'expected_hash': first['hash']}], 'confirm': False},
                        {'items': [{'id': first['id'], 'expected_hash': first['hash']}], 'confirm': 'true'},
                        {'items': [{'id': first['id'], 'expected_hash': first['hash']}], 'confirm': 1}):
            status, body = self.call('/api/knowledge/trash', payload)
            self.assertEqual(status, 400)
            self.assertEqual(body['code'], 'INVALID_REQUEST')
        self.assertEqual(self.trashed_ids(), set(), '取消零写入')
        self.assertEqual(self.knowledge.search('正文')['total'], 3)

    # ── 确认后隐藏 ────────────────────────────────────────────────────────────

    def test_confirmed_delete_hides_from_search_listing_and_counts(self):
        first, second, kept = self.docs
        status, receipt = self.commit([first, second])
        self.assertEqual(status, 200)
        self.assertEqual(receipt['schema'], 'knowledge-trash/v1')
        self.assertEqual(receipt['summary'], {'requested': 2, 'deleted': 2, 'already_trashed': 0,
                                              'missing': 0, 'conflicts': 0, 'errors': 0})
        self.assertEqual(receipt['codes']['deleted'], 'TRASHED')
        self.assertEqual(self.knowledge.search('正文')['total'], 1)
        self.assertEqual(self.knowledge.status()['total'], 1)
        remaining = {row['id'] for row in self.knowledge.search('')['results']}
        self.assertEqual(remaining, {kept['id']}, '列表里只剩没删的那条')
        listing = self.call('/api/knowledge/trash', method='GET')[1]
        self.assertEqual({row['id'] for row in listing['results']}, {first['id'], second['id']})

    def test_resync_and_duplicate_collection_do_not_revive_and_report_trashed(self):
        first = self.docs[0]
        self.commit([first])
        total = self.knowledge.search('正文')['total']
        # 再写一次同标题同正文（新的随机 key）：旧那条不会被复活，新那条照常可检索。
        self.knowledge.add_note({'title': '资料 0', 'body': '正文 0 的内容'})
        self.assertEqual(self.trashed_ids(), {first['id']})
        self.assertEqual(self.knowledge.search('正文')['total'], total + 1)
        self.assertNotIn(first['id'], [row['id'] for row in self.knowledge.search('')['results']])
        # 重复收藏命中隐藏资料：返回 trashed 事实与统一提示码，不自动恢复。
        collected = self.call('/api/knowledge/reference', {
            'title': '重复收藏', 'url': 'https://example.com/dup', 'reader_note': '备注'})[1]
        self.assertEqual(collected['code'], 'COLLECTED')
        self.assertFalse(collected['trashed'])
        self.commit([collected])
        again = self.call('/api/knowledge/reference', {
            'title': '重复收藏', 'url': 'https://example.com/dup', 'reader_note': '备注'})[1]
        self.assertTrue(again['duplicate'])
        self.assertTrue(again['trashed'])
        self.assertEqual(again['code'], 'ALREADY_TRASHED')
        self.assertEqual(self.trashed_ids(), {first['id'], collected['id']}, '重复收藏不是复活通道')

    def test_duplicate_reference_on_trashed_body_reports_already_trashed(self):
        collected = self.call('/api/knowledge/reference', {
            'title': '先收藏', 'url': 'https://example.com/one', 'reader_note': '备注'})[1]
        self.assertEqual(collected['code'], 'COLLECTED')
        self.assertFalse(collected['trashed'])
        self.commit([{'id': collected['id'], 'hash': collected['hash']}])
        duplicate = self.call('/api/knowledge/reference', {
            'title': '再收藏', 'url': 'https://example.com/one', 'reader_note': '备注'})[1]
        self.assertTrue(duplicate['duplicate'])
        self.assertTrue(duplicate['trashed'], '重复收藏必须带回隐藏事实')
        self.assertEqual(duplicate['code'], 'ALREADY_TRASHED')
        self.assertEqual(duplicate['deleted_at'] != '', True)
        self.assertEqual(self.trashed_ids(), {collected['id']}, '收藏不是复活通道')

    def test_legacy_duplicate_branch_reports_trashed_too(self):
        # 造一条按旧键（host+path+query）登记的历史记录，走 knowledge/reference 的旧键分支。
        url = 'https://example.com/legacy'
        with self.knowledge.db() as c:
            c.execute('INSERT OR IGNORE INTO sources(id,config,status) VALUES(?,?,?)',
                      ('external-references', json.dumps({'id': 'external-references', 'name': '外部文章收藏',
                                                          'kind': 'manual'}), 'ready'))
        ident = hashlib.sha256(('external-references:' + url).encode()).hexdigest()[:32]
        with self.knowledge.db() as c:
            c.execute('INSERT INTO documents(id,source,kind,title,body,url,modified,tags,hash,provenance)'
                      ' VALUES(?,?,?,?,?,?,?,?,?,?)',
                      (ident, 'external-references', 'external', '旧记录', '旧正文', url, '2026-10-01T00:00:00+00:00',
                       '[]', HASH64, json.dumps({'canonical_url': url})))
        stored = self.knowledge.document(ident)
        self.commit([{'id': ident, 'hash': stored['hash']}])
        legacy = self.call('/api/knowledge/reference', {'title': '旧链接再收藏', 'url': url, 'reader_note': '备注'})[1]
        self.assertEqual(legacy['id'], ident)
        self.assertTrue(legacy['duplicate'])
        self.assertTrue(legacy['trashed'], '旧键分支也必须带回隐藏事实')
        self.assertEqual(legacy['code'], 'ALREADY_TRASHED')

    # ── 恢复 ──────────────────────────────────────────────────────────────────

    def test_restore_brings_back_count_and_listing(self):
        first = self.docs[0]
        self.commit([first])
        status, receipt = self.restore([first['id']])
        self.assertEqual(status, 200)
        self.assertEqual(receipt['schema'], 'knowledge-restore/v1')
        self.assertEqual(receipt['summary'], {'requested': 1, 'restored': 1, 'unhidden': 0, 'not_found': 0})
        self.assertEqual(self.trashed_ids(), set())
        self.assertEqual(self.knowledge.search('正文')['total'], 3)
        self.assertEqual(self.knowledge.status()['total'], 3)
        self.assertEqual(self.call('/api/knowledge/trash', method='GET')[1]['total'], 0)
        again = self.restore([first['id']])
        self.assertEqual(again[1]['summary']['not_found'], 1, '重复恢复幂等')

    def test_restore_reports_body_missing_instead_of_claiming_recovered(self):
        first = self.docs[0]
        self.commit([first])
        with self.knowledge.db() as c:
            c.execute('DELETE FROM documents WHERE id=?', (first['id'],))
        status, receipt = self.restore([first['id']])
        self.assertEqual(status, 200)
        self.assertEqual(receipt['summary'], {'requested': 1, 'restored': 0, 'unhidden': 1, 'not_found': 0})
        self.assertIn('来源已不在索引', receipt['unhidden'][0]['note'])
        self.assertEqual(self.trashed_ids(), set())

    # ── 历史引用仍可读 ────────────────────────────────────────────────────────

    def test_historical_document_read_still_works_with_trashed_facts(self):
        first = self.docs[0]
        self.commit([first])
        status, document = self.call('/api/knowledge/document/' + first['id'], method='GET')
        self.assertEqual(status, 200)
        self.assertEqual(document['id'], first['id'])
        self.assertTrue(document['trashed'])
        self.assertTrue(document['deleted_at'])
        self.assertEqual(document['hash'], first['hash'])
        self.assertIn('正文 0', document['body'])

    def test_missing_document_returns_safe_404(self):
        status, body = self.call('/api/knowledge/document/' + GONE, method='GET')
        self.assertEqual(status, 404)
        self.assertEqual(body['code'], 'DOCUMENT_NOT_FOUND')
        self.assertEqual(body['error'], '资料不存在，请重新检索')

    # ── 冲突与部分成功 ────────────────────────────────────────────────────────

    def test_hash_conflict_is_one_item_version_conflict_while_rest_succeed(self):
        first, second, third = self.docs
        status, receipt = self.commit([{'id': first['id'], 'hash': HASH64}, second, third])
        self.assertEqual(status, 200, '冲突不遮住整批：仍然 200 分组')
        self.assertEqual(receipt['summary'], {'requested': 3, 'deleted': 2, 'already_trashed': 0,
                                              'missing': 0, 'conflicts': 1, 'errors': 0})
        self.assertEqual(receipt['conflicts'][0]['id'], first['id'])
        self.assertEqual(receipt['conflicts'][0]['code'], 'VERSION_CONFLICT')
        self.assertEqual(self.trashed_ids(), {second['id'], third['id']})
        self.assertEqual(self.knowledge.search('正文')['total'], 1)

    def test_missing_and_already_trashed_are_grouped_not_faked(self):
        first = self.docs[0]
        self.commit([first])
        status, receipt = self.commit([{'id': first['id'], 'hash': first['hash']}, {'id': GONE, 'hash': HASH64}])
        self.assertEqual(status, 200)
        self.assertEqual(receipt['summary'], {'requested': 2, 'deleted': 0, 'already_trashed': 1,
                                              'missing': 1, 'conflicts': 0, 'errors': 0})
        self.assertEqual(receipt['codes']['missing'], 'DOCUMENT_NOT_FOUND')
        self.assertEqual(receipt['codes']['already_trashed'], 'ALREADY_TRASHED')
        self.assertEqual(self.trashed_ids(), {first['id']}, '不存在的条目不造已删除记录')

    def test_commit_failure_reports_nothing_and_leaves_state_untouched(self):
        first, second = self.docs[0], self.docs[1]
        # 存储层在提交阶段抛错（事务已回滚）：HTTP 层不能因此报 deleted，
        # 也不能把 sqlite 原文、库路径带回来。
        with unittest.mock.patch.object(self.knowledge, 'trash_many',
                                        side_effect=sqlite3.OperationalError('database is locked')):
            status, body = self.commit([first, second])
        self.assertEqual(status, 503)
        self.assertEqual(body['code'], 'STORE_BUSY')
        self.assertEqual(set(body), {'error', 'code'}, '失败回执里没有分组、没有 summary')
        self.assertNotIn('deleted', body)
        self.assertNotIn('database is locked', json.dumps(body))
        self.assertEqual(self.trashed_ids(), set())
        self.assertEqual(self.knowledge.search('正文')['total'], 3)

    # ── 非法输入 ──────────────────────────────────────────────────────────────

    def test_invalid_ids_items_and_confirm_are_all_refused(self):
        first = self.docs[0]
        cases = [
            self.preview([]),
            self.preview([first['id']] * 51),
            self.preview(['ZZ' * 16]),
            self.preview([first['id'].upper()]),
            self.preview(['nope']),
            self.preview({'ids': first['id']}),
            self.preview({'id': first['id']}),
            self.preview({'ids': [first['id']], 'confirm': True}),
            self.restore([]),
            self.restore([first['id']] * 51),
            self.restore(['ZZ' * 16]),
            self.restore({'ids': [first['id']]}),
            self.commit([]),
            self.commit([{'id': first['id']}] * 51),
            self.call('/api/knowledge/trash', {'items': 'nope', 'confirm': True}),
            # 这些形状用 call 直发：commit() 只接受成形条目。
            self.call('/api/knowledge/trash', {'items': [{'expected_hash': HASH64}], 'confirm': True}),
            self.call('/api/knowledge/trash', {'items': [{'id': first['id'], 'expected_hash': HASH64, 'force': True}], 'confirm': True}),
            self.call('/api/knowledge/trash', {'items': [{'id': first['id'], 'expected_hash': 'short'}], 'confirm': True}),
            self.call('/api/knowledge/trash', {'items': [{'id': first['id'], 'expected_hash': HASH64}]}),
            self.call('/api/knowledge/trash', {'items': [{'id': first['id'], 'expected_hash': HASH64}], 'confirm': False}),
            self.call('/api/knowledge/trash', {'items': [{'id': first['id'], 'expected_hash': HASH64}], 'confirm': 'true'}),
            self.call('/api/knowledge/trash', {'items': [{'id': first['id'], 'expected_hash': HASH64}], 'confirm': 1}),
            self.call('/api/knowledge/trash', {'confirm': True}),
        ]
        for label, (status, body) in zip(('preview 空', 'preview 51', 'preview 大写 id', 'preview 短 id', 'preview 字符串',
                                   'preview 少复数', 'preview 多字段', 'preview 带 confirm',
                                   'restore 空', 'restore 51', 'restore 短 id', 'restore 少复数',
                                   'commit 空', 'commit 51', 'commit items 非列表', 'commit item 缺 id',
                                   'commit 多字段', 'commit hash 短', 'commit 缺 confirm',
                                   'commit confirm false', 'commit confirm 字符串', 'commit confirm 数字',
                                   'commit 缺 items'), cases):
            self.assertEqual(status, 400, label)
            self.assertEqual(body['code'], 'INVALID_REQUEST', label)
        self.assertEqual(self.trashed_ids(), set())

    def test_quantity_and_shape_are_checked_at_the_http_layer_not_only_by_the_store(self):
        """HTTP 层自己挡住数量与形状，不靠存储层兜底。

        存储层的兜底文案是「请选择 1–50 条知识」，HTTP 层自己的是「…再预览/再恢复」；
        这里断言拿到的是后者 —— 放开 HTTP 层数量上限时，这条会立刻变红。
        """
        first = self.docs[0]
        status, body = self.preview([first['id']] * 51)
        self.assertEqual((status, body['error']), (400, '请选择 1–50 条知识再预览'))
        status, body = self.restore([first['id']] * 51)
        self.assertEqual((status, body['error']), (400, '请选择 1–50 条知识再恢复'))
        status, body = self.commit([{'id': first['id']}] * 51)
        self.assertEqual((status, body['error']), (400, '删除请求包含无效字段'))
        # 50 条必须真的放行。
        notes = [self.knowledge.add_note({'title': '边界 %d' % i, 'body': '边界正文 %d' % i}) for i in range(50)]
        self.assertEqual(self.commit(notes)[0], 200)
        self.assertEqual(self.preview([note['id'] for note in notes])[1]['summary']['requested'], 50)

    def test_fifty_items_ok_and_fifty_one_refused(self):
        notes = [self.knowledge.add_note({'title': '批量 %d' % i, 'body': '批量正文 %d' % i}) for i in range(50)]
        status, receipt = self.commit(notes)
        self.assertEqual(status, 200)
        self.assertEqual(receipt['summary']['deleted'], 50)
        self.assertEqual(len(self.trashed_ids()), 50)
        # 51 条整体非法：零变更，不是删掉 50 条再说。
        more = [self.knowledge.add_note({'title': '再来 %d' % i, 'body': '再来正文 %d' % i}) for i in range(51)]
        status, body = self.commit(more)
        self.assertEqual(status, 400)
        self.assertEqual(body['code'], 'INVALID_REQUEST')
        self.assertEqual(len(self.trashed_ids()), 50, '51 条被拒时不追加也不回退')

    def test_paging_parameters_are_bounded_integers(self):
        first = self.docs[0]
        self.commit([first])
        status, listing = self.call('/api/knowledge/trash?offset=0&limit=1', method='GET')
        self.assertEqual(status, 200)
        self.assertEqual(listing['limit'], 1)
        self.assertEqual(len(listing['results']), 1)
        for query in ('offset=-1', 'limit=0', 'limit=51', 'limit=100000', 'offset=abc', 'limit=1.5',
                      'offset=1&limit=', 'sort=name', 'offset=1&offset=2'):
            status, body = self.call('/api/knowledge/trash?' + query, method='GET')
            self.assertEqual(status, 400, query)
            self.assertEqual(body['code'], 'INVALID_REQUEST', query)
        # 边界值仍然合法。
        self.assertEqual(self.call('/api/knowledge/trash?offset=0&limit=50', method='GET')[0], 200)

    # ── 保护与脱敏 ────────────────────────────────────────────────────────────

    def test_missing_origin_and_header_are_refused_on_every_new_route(self):
        first = self.docs[0]
        items = [{'id': first['id'], 'expected_hash': first['hash']}]
        routes = [('/api/knowledge/trash/preview', {'ids': [first['id']]}),
                  ('/api/knowledge/trash', {'items': items, 'confirm': True}),
                  ('/api/knowledge/trash/restore', {'ids': [first['id']]})]
        for path, body in routes:
            self.assertEqual(self.call(path, body, headers={'Origin': 'https://example.com'})[0], 403, path)
            self.assertEqual(self.call(path, body, headers={'X-Article-Studio': '0'})[0], 403, path)
            headers = {'Origin': self.base}
            request = urllib.request.Request(self.base + path, data=json.dumps(body).encode(),
                                             headers={**headers, 'Content-Type': 'application/json'}, method='POST')
            with self.assertRaises(urllib.error.HTTPError) as caught:
                self.opener.open(request)
            caught.exception.close()
            self.assertEqual(caught.exception.code, 403, path)
        self.assertEqual(self.call('/api/knowledge/trash', method='GET', headers={'Origin': 'https://example.com'})[0], 200,
                         'GET 只要求可信 Host')
        self.assertEqual(self.trashed_ids(), set(), '被拒请求零写入')

    def test_store_failures_never_leak_paths_sql_or_body(self):
        first = self.docs[0]
        secret = 'SELECT * FROM documents -- /Users/huang/workspace/knowledge/knowledge.db ' + first['body']
        cases = [('trash_preview', (first['id'],)), ('trash_many', ([{'id': first['id'], 'expected_hash': first['hash']}], True)),
                 ('restore_many', ([first['id']],)), ('trash_list', (0, 30))]
        for name, args in cases:
            with unittest.mock.patch.object(Knowledge, name, side_effect=RuntimeError(secret)):
                if name == 'trash_many':
                    status, body = self.commit([first])
                elif name == 'trash_preview':
                    status, body = self.preview([first['id']])
                elif name == 'restore_many':
                    status, body = self.restore([first['id']])
                else:
                    status, body = self.call('/api/knowledge/trash', method='GET')
            self.assertEqual(status, 500, name)
            self.assertEqual(body['code'], 'STORE_ERROR', name)
            self.assertEqual(body['error'], '这次操作结果未确认，请刷新已删除列表核对', name)
            self.assertNotIn('SELECT', json.dumps(body))
            self.assertNotIn('/Users/', json.dumps(body))
            self.assertNotIn('正文 0', json.dumps(body))
        self.assertEqual(self.trashed_ids(), set())

    def test_busy_store_returns_503_without_leaking(self):
        first = self.docs[0]
        with unittest.mock.patch.object(Knowledge, 'trash_many', side_effect=sqlite3.OperationalError('database is locked')):
            status, body = self.commit([first])
        self.assertEqual(status, 503)
        self.assertEqual(body['code'], 'STORE_BUSY')
        self.assertEqual(body['error'], '知识库正在更新，请稍后刷新核对')

    def test_other_routes_keep_their_existing_semantics(self):
        first = self.docs[0]
        self.commit([first])
        self.assertEqual(self.call('/api/knowledge', method='GET')[0], 200)
        query = urllib.parse.urlencode({'q': '正文'})
        self.assertEqual(self.call('/api/knowledge/search?' + query, method='GET')[1]['total'], 2)
        self.assertEqual(self.call('/api/nope', method='GET')[0], 404)
        self.assertEqual(self.call('/api/knowledge/nope', {'a': 1})[0], 404)


if __name__ == '__main__':
    unittest.main()