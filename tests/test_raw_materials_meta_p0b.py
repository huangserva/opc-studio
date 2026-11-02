"""P0-b exact snapshot, query-work budget, and HTTP boundary regressions."""
from contextlib import contextmanager
import json
import sqlite3
import threading
import unittest
from unittest.mock import patch
import raw_materials_meta as M
import test_raw_materials_meta_default as fixtures
from knowledge_store import NOT_TRASHED


class P0bTests(unittest.TestCase):
    setUp = fixtures.DefaultRowsTests.setUp

    def test_exact_total_handles_null_ids_null_orphan_tombstones(self):
        with self.k.db() as c:
            c.execute("INSERT INTO documents VALUES(NULL,'synthetic','articles','NULL','正文','','2026-10-31','[]','h','{}')")
            c.execute('INSERT INTO document_trash VALUES(NULL)')
            c.execute("INSERT INTO document_trash VALUES('orphan')")
            expected=c.execute('SELECT COUNT(*) FROM documents d WHERE '+NOT_TRASHED).fetchone()[0]
            self.assertEqual(M.default_material_total(c),expected)
        self.assertEqual(self.k.search()['total'],expected)

    def test_default_total_does_not_scan_each_document_for_tombstones(self):
        with self.k.db() as c:
            n=c.execute('SELECT COUNT(*) FROM documents').fetchone()[0]
            steps=[]
            c.set_progress_handler(lambda:steps.append(1) or 0,100)
            total=M.default_material_total(c)
            c.set_progress_handler(None,0)
            self.assertGreater(total,0)
            self.assertLess(len(steps)*100,n, '精确总数不得逐文档执行回收站反查')

    def test_writer_commits_between_total_and_rows_reader_stays_consistent(self):
        original=self.k.db
        with original() as c:
            expected=self.k.search()
        committed=threading.Event();path=self.path
        @contextmanager
        def db():
            with original() as c:
                class Proxy:
                    def __getattr__(self,name):return getattr(c,name)
                    def execute(self,sql,args=()):
                        cursor=c.execute(sql,args)
                        if sql.startswith('SELECT (SELECT COUNT(*)'):
                            class Cursor:
                                def fetchone(inner):
                                    value=cursor.fetchone()
                                    with sqlite3.connect(path) as writer:
                                        writer.execute("UPDATE documents SET kind='projects' WHERE id=?",(f'{1:032x}',))
                                        writer.execute("INSERT INTO documents VALUES('new','synthetic','articles','新增','正文','','2026-10-31','[]','h','{}')")
                                    committed.set()
                                    return value
                            return Cursor()
                        return cursor
                yield Proxy()
        with patch.object(self.k,'db',db):actual=self.k.search()
        self.assertTrue(committed.is_set());self.assertEqual(actual,expected)
        after=self.k.search();self.assertEqual(after['total'],expected['total']+1)
        self.assertEqual(after['results'][0]['id'],'new')

    def test_open_write_transaction_does_not_expose_partial_kind_or_count(self):
        expected=self.k.search()
        writer=sqlite3.connect(self.path)
        self.addCleanup(writer.close)
        writer.execute('BEGIN IMMEDIATE')
        writer.execute("UPDATE documents SET kind='articles',modified='2099' WHERE id=?",(f'{100:032x}',))
        writer.execute("DELETE FROM documents WHERE id=?",(f'{101:032x}',))
        self.assertEqual(self.k.search(),expected)
        writer.rollback()

    def test_http_offset_limit_validation(self):
        for query in ('offset=-1','offset=100001','offset=abc','offset=1.5','offset=','offset=1&offset=2','limit=0','limit=-1','limit=100000','limit=abc','limit=','limit=30&limit=30'):
            with self.subTest(query=query),self.assertRaises(ValueError):M.material_search_offset(query)
        self.assertEqual(M.material_search_offset('offset=100000&limit=30'),100000)
        self.assertEqual(M.material_search_offset('limit=1'),0)
        self.assertEqual(M.material_search_offset('q=%E4%B8%AD%E6%96%87'),0)

    def test_real_handler_bad_pagination_returns_400_and_valid_shape_is_unchanged(self):
        import http.client
        from types import SimpleNamespace
        from server import Handler, ThreadingHTTPServer
        class QuietHandler(Handler):
            def log_message(self,*args): pass
        server=ThreadingHTTPServer(('127.0.0.1',0),QuietHandler)
        server.store=SimpleNamespace(knowledge=self.k)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        def cleanup():
            server.shutdown();server.server_close();thread.join(2)
        self.addCleanup(cleanup)
        for query in ('offset=-1','offset=abc','offset=1&offset=2','offset=','limit=0','limit=abc','limit=100000'):
            with self.subTest(query=query):
                connection=http.client.HTTPConnection('127.0.0.1',server.server_port)
                try:
                    connection.request('GET','/api/knowledge/search?'+query)
                    response=connection.getresponse();body=json.loads(response.read())
                    self.assertEqual(response.status,400);self.assertEqual(set(body),{'error'})
                finally:connection.close()
        for limit in (30,1):
            connection=http.client.HTTPConnection('127.0.0.1',server.server_port)
            try:
                connection.request('GET','/api/knowledge/search?offset=0&limit='+str(limit))
                response=connection.getresponse();value=json.loads(response.read())
                self.assertEqual(response.status,200);self.assertEqual(value,self.k.search())
            finally:connection.close()

    def test_nondefault_query_uses_existing_count_and_sort(self):
        with patch.object(M,'default_material_total',side_effect=AssertionError('非默认请求不能进入优化')):
            value=self.k.search(kind='projects')
        with self.k.db() as c:
            expected=c.execute('SELECT COUNT(*) FROM documents d WHERE kind=? AND '+NOT_TRASHED,('projects',)).fetchone()[0]
        self.assertEqual(value['total'],expected)


if __name__=='__main__':unittest.main()
