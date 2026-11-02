from contextlib import contextmanager
import http.client
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
from types import SimpleNamespace
import unittest
from clues_store import Clues
from server import Handler,ThreadingHTTPServer
import raw_materials_meta as M

class CluePhasesTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='p0b-clues-',dir='/tmp');self.addCleanup(self.tmp.cleanup)
        self.clues=Clues(SimpleNamespace(workspace=Path(self.tmp.name)))
        with self.clues.db() as c:
            data=dict(id='a'*32,title='合成模型',summary='合成摘要',categories=['technology'],status='candidate',priority='normal',kind='information',tracking=False,revision=1)
            c.execute('INSERT INTO threads VALUES(?,?,1,0,?,?)',('a'*32,json.dumps(data),'2026-10-31','2026-10-31'))
    def test_rows_and_facets_together_equal_default_all_fields(self):
        all_fields=self.clues.listing();rows=self.clues.listing(phase='rows');facets=self.clues.listing(phase='facets')
        for key in ('items','timeline','total','next_offset','sort'):self.assertEqual(rows[key],all_fields[key])
        for key in ('counts','tag_counts','heat_counts','years','sources'):self.assertEqual(facets[key],all_fields[key])
        self.assertNotIn('facets_pending',all_fields);self.assertTrue(rows['facets_pending'])
    def test_rows_avoid_aggregate_udf_but_explicit_tag_filter_still_applies(self):
        original=self.clues.db;queries=[]
        @contextmanager
        def db():
            with original() as c:c.set_trace_callback(queries.append);yield c
        self.clues.db=db
        self.clues.listing(phase='rows')
        self.assertFalse(any('json_each(tech_tags' in q or 'SELECT DISTINCT year' in q for q in queries))
        self.assertEqual(self.clues.listing(phase='rows',tag='hardware')['total'],0)
    def test_batched_reads_preserve_rowid_latest_and_source_encounter_order(self):
        key='a'*32
        with self.clues.db() as c:
            for n,created,source in [(1,'2030','second-in-index'),(2,'2020','first-in-index')]:
                data=json.dumps(dict(note='note'+str(n),clock_label='合成时间',evidence=[dict(kind='aisa',source=source,provenance=dict(platform='twitter'))]))
                c.execute('INSERT INTO entries VALUES(?,?,?,?,?,?,?,?,?)',(str(n)*32,key,data,created,'2026-10-31','2026','10','evidence',None))
        expected=self.clues.listing()['items']
        self.assertEqual(expected[0]['latest_note'],'note2')
        self.assertEqual(expected[0]['sources'],['first-in-index','second-in-index'])
        actual=self.clues.listing(phase='rows')['items'];self.assertEqual(actual,expected)

    def test_page_reads_use_bounded_sql_batches_instead_of_per_card_queries(self):
        with self.clues.db() as c:
            template=json.loads(c.execute('SELECT data FROM threads').fetchone()[0])
            for n in range(1,31):
                key=f'{n:032x}';data={**template,'id':key}
                c.execute('INSERT INTO threads VALUES(?,?,1,0,?,?)',(key,json.dumps(data),'2026-10-31','2026-10-31'))
        original=self.clues.db;queries=[]
        @contextmanager
        def db():
            with original() as c:c.set_trace_callback(queries.append);yield c
        self.clues.db=db
        self.assertEqual(len(self.clues.listing(phase='rows')['items']),30)
        selects=[q for q in queries if q.lstrip().upper().startswith('SELECT')]
        self.assertLessEqual(len(selects),6,'30张卡片读取不得膨胀为90次SQL/GIL切换')

    def test_illegal_phase_values_fail_closed_at_http_boundary(self):
        class Quiet(Handler):
            def log_message(self,*args):pass
        server=ThreadingHTTPServer(('127.0.0.1',0),Quiet);server.store=SimpleNamespace(clues=self.clues)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        def stop():server.shutdown();server.server_close();thread.join(2)
        self.addCleanup(stop)
        for query in ('phase=','phase=none','phase=rows&phase=facets','phase=%27OR1%3D1','offset=-1','offset=','offset=1&offset=2','offset=999999999999999999999999','limit=0','limit=101','limit=abc'):
            cn=http.client.HTTPConnection('127.0.0.1',server.server_port)
            try:
                cn.request('GET','/api/clues?'+query);r=cn.getresponse();r.read();self.assertEqual(r.status,400)
            finally:cn.close()
        for value in ('rows','facets','all'):
            self.assertEqual(M.clue_read_phase('phase='+value),value)
        self.assertEqual(M.clue_read_phase(''),'all')
        self.assertEqual(M.clue_read_options('limit=1'),{})
        self.assertEqual(M.clue_read_options('offset=2&phase=rows'),{'phase':'rows'})
