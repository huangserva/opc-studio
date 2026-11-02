"""Exact default-row equivalence, including permissive legacy SQLite edge cases."""
import json
from pathlib import Path
import random
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch
from knowledge_store import Knowledge,NOT_TRASHED
import raw_materials_meta as M

LEGACY_ORDER = "CASE d.kind WHEN 'articles' THEN 0 WHEN 'projects' THEN 1 WHEN 'notes' THEN 2 WHEN 'mail' THEN 3 ELSE 4 END,d.modified DESC,d.id"


class DefaultRowsTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='p0-default-',dir='/tmp');self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/'knowledge.db';db=sqlite3.connect(self.path)
        db.execute('PRAGMA journal_mode=WAL')
        db.executescript('CREATE TABLE documents(id TEXT PRIMARY KEY,source TEXT,kind TEXT,title TEXT,body TEXT,url TEXT,modified TEXT,tags TEXT,hash TEXT,provenance TEXT);CREATE INDEX documents_kind ON documents(kind);CREATE TABLE document_trash(document_id TEXT PRIMARY KEY);')
        kinds=['articles']*7+['projects']*11+['notes']*3+['mail']*4+['custom','wechat','github']*4020
        rng=random.Random(310);rows=[]
        for number,kind in enumerate(kinds):
            identifier=f'{number:032x}';date=None if number%17==0 else f'2026-10-{1+number%3:02d}';rows.append((identifier,'synthetic',kind,'标题'+str(number),'正文😀'+str(number),'',date,'[]','h'+str(number),'{}'))
        rows.append(('f'*32,'synthetic',None,'NULL kind','合成NULL类型','','2026-10-30','[]','null-kind','{}'))
        rng.shuffle(rows);db.executemany('INSERT INTO documents VALUES(?,?,?,?,?,?,?,?,?,?)',rows)
        db.executemany('INSERT INTO document_trash VALUES(?)',[(f'{n:032x}',) for n in range(0,len(kinds),19)])
        db.commit();db.close()
        self.k=Knowledge.__new__(Knowledge);self.k.path=self.path;self.k._raw_meta=None;self.k.sync_lock=threading.Lock()

    def compare(self,offset,limit):
        with self.k.db() as db:
            original=[dict(r) for r in db.execute('SELECT d.* FROM documents d WHERE '+NOT_TRASHED+' ORDER BY '+LEGACY_ORDER+' LIMIT ? OFFSET ?',(limit,offset))]
            actual=[dict(r) for r in M.default_material_rows(db,offset,limit)]
            self.assertEqual(actual,original)

    def test_two_hundred_random_limits_offsets_all_raw_fields_equal(self):
        rng=random.Random(712);cases=[(0,1),(5,30),(7,30),(15,30),(22,30),(10000,30),(12060,100)]
        cases.extend((rng.randrange(12080),rng.randrange(1,101)) for _ in range(200))
        for offset,limit in cases:
            with self.subTest(offset=offset,limit=limit):self.compare(offset,limit)

    def test_equal_modified_ids_null_modified_unknown_kind_trash_empty_segment(self):
        self.compare(0,100);self.compare(10000,100)
        with self.k.db() as db:db.execute("DELETE FROM documents WHERE kind='projects'")
        self.compare(0,100)

    def test_normalized_empty_forms_use_fast_path_but_filters_do_not(self):
        for query in ('','   '):
            with patch.object(M,'default_material_rows',wraps=M.default_material_rows) as fast:
                value=self.k.search(query);fast.assert_called_once();self.assertEqual(len(value['results']),30)
        for field,value in (('kind','projects'),('source','synthetic'),('tag','x'),('query','abc'),('mode','nondefault')):
            with self.subTest(field=field),patch.object(M,'default_material_rows',side_effect=AssertionError('filtered fast path')) as fast:
                try:self.k.search(**{field:value})
                except sqlite3.OperationalError:pass  # This minimal fixture deliberately has no full-text table.
                fast.assert_not_called()
        with patch.object(M,'default_material_rows',wraps=M.default_material_rows) as fast:
            self.k.search(kind=None,source=None,tag=None,mode=None);fast.assert_called_once()

    def test_multi_segment_rows_share_one_snapshot_while_writer_moves_kind(self):
        with self.k.db() as db:
            expected=[dict(r) for r in db.execute('SELECT d.* FROM documents d WHERE '+NOT_TRASHED+' ORDER BY '+LEGACY_ORDER+' LIMIT 100')]
            moved=False
            class Connection:
                @property
                def in_transaction(proxy):return db.in_transaction
                def execute(proxy,sql,args=()):
                    cursor=db.execute(sql,args)
                    if sql.startswith('SELECT d.*') and args and args[0]=='articles':
                        class Cursor:
                            def fetchall(inner):
                                nonlocal moved
                                rows=cursor.fetchall()
                                if not moved:
                                    with sqlite3.connect(self.path) as writer:writer.execute('UPDATE documents SET kind=? WHERE id=?',('projects',f'{1:032x}'))
                                    moved=True
                                return rows
                        return Cursor()
                    return cursor
            actual=[dict(r) for r in M.default_material_rows(Connection(),0,100)]
        self.assertTrue(moved);self.assertEqual(actual,expected)

    def test_deep_offset_uses_exact_legacy_sql_and_records_reason(self):
        with self.k.db() as db:
            statements=[];db.set_trace_callback(statements.append)
            with self.assertLogs(M.__name__,level='WARNING'):
                M.default_material_rows(db,10000,30)
        self.assertEqual(len(statements),1)
        self.assertTrue(statements[0].startswith('SELECT d.* FROM documents d WHERE '+NOT_TRASHED))
        self.assertIn(LEGACY_ORDER,statements[0])
        self.assertGreater(M.default_list_metrics().get('deep_offset_original',0),0)

    def test_fallback_records_controlled_reason_and_full_wire_result_equal(self):
        expected=self.k.search(offset=5)
        before=M.default_list_metrics().get('exception',0)
        with patch.object(M,'default_material_rows',side_effect=RuntimeError('SYNTHETIC_PRIVATE_ERROR')),self.assertLogs(M.__name__,level='WARNING') as logs:
            actual=self.k.search(offset=5)
        self.assertEqual(actual,expected);self.assertEqual(M.default_list_metrics()['exception'],before+1)
        self.assertIn('reason=exception',logs.output[0]);self.assertNotIn('SYNTHETIC_PRIVATE_ERROR',logs.output[0])
        with self.k.db() as db:total=db.execute('SELECT COUNT(*) FROM documents d WHERE '+NOT_TRASHED).fetchone()[0]
        self.assertEqual(actual['total'],total)


if __name__=='__main__':unittest.main()
