import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
import test_clue_pipeline as fixtures
from wechat_source_loader import source_module

Reader = source_module('results').ResultReader


class SourceContractTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.PipelineTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.store = self.fixture.store
        self.reader = Reader(self.store.workspace)

    def test_source_module_owns_rules_and_reuses_existing_ids_and_citations(self):
        result = self.fixture.run_analysis()
        clue_id = json.loads(result[2])['clues'][0]
        data = self.reader.results(category='technology')
        self.assertTrue(data['available'])
        self.assertEqual(data['owner'], 'wechat-assistant')
        self.assertEqual(data['schema'], 'wechat-source/v1')
        self.assertEqual(data['items'][0]['id'], clue_id)
        quote = data['items'][0]['evidence'][0]
        self.assertEqual(quote['hash'], self.fixture.doc['hash'])
        self.assertEqual(quote['excerpt'], self.fixture.doc['body'][quote['start']:quote['end']])
        self.assertEqual(quote['analysis']['run_id'], 'a'*32)
        self.assertIn('wechat_source', type(self.fixture.pipeline).__mro__[1].__module__)

    def test_filter_and_pagination_have_same_ids_as_workbench_without_gmail_leak(self):
        self.fixture.run_analysis()
        doc = self.fixture.add('email', '合成邮件引用', source='gmail')
        self.store.clues.save({'title':'仅邮件', 'categories':['business'], 'picks':[{'id':doc['id'],'hash':doc['hash'],'start':0,'end':len(doc['body'])}]})
        source = self.reader.results(query='合成', limit=1)
        central = self.store.clues.listing(source='wechat', query='合成', limit=1)
        self.assertEqual([r['id'] for r in source['items']], [r['id'] for r in central['items']])
        self.assertEqual(source['counts'], central['counts'])
        self.assertEqual(source['total'], 1)
        self.assertEqual(source['counts']['business'], 0)

    def test_live_pending_does_not_reuse_old_projection_count(self):
        self.fixture.run_analysis()
        before = self.reader.status()
        self.assertEqual(before['stages']['clues']['pending_versions'], 0)
        self.fixture.add('new', '尚未处理的合成消息')
        after = self.reader.status()
        self.assertEqual(after['stages']['clues']['processed_versions'], 1)
        self.assertEqual(after['stages']['clues']['pending_versions'], 1)
        self.assertFalse(after['stages']['radar']['available'])
        self.assertIsNone(after['stages']['radar']['has_pending'])

    def test_absent_source_is_not_successful_empty_and_does_not_create_files(self):
        with tempfile.TemporaryDirectory() as directory:
            data = Reader(directory).results()
            self.assertFalse(data['available'])
            self.assertIsNone(data['total'])
            self.assertEqual(list(Path(directory).iterdir()), [])
        for params in ({'kind':'invalid'}, {'category':'invalid'}, {'offset':-1}, {'limit':101}):
            with self.assertRaises(ValueError): self.reader.results(**params)

    def test_cli_without_host_import_is_readonly_and_retains_the_same_result(self):
        self.fixture.run_analysis()
        package = Path(source_module('results').__file__).parent
        paths = list(self.store.workspace.rglob('*.db'))
        snapshots = {p: p.read_bytes() for p in paths}
        env = {**os.environ}; env.pop('PYTHONPATH', None)
        completed = subprocess.run([sys.executable,'-m','wechat_source','--workspace',str(self.store.workspace),'results','--kind','clues'],cwd=package.parent,env=env,capture_output=True,text=True,check=True)
        data = json.loads(completed.stdout)
        self.assertEqual(data['items'][0]['id'], self.reader.results()['items'][0]['id'])
        for p, before in snapshots.items(): self.assertEqual(p.read_bytes(), before)
        imports = subprocess.run([sys.executable,'-c',"import sys; from wechat_source.results import ResultReader; assert 'server' not in sys.modules; assert 'clues_store' not in sys.modules"],cwd=package.parent,env=env,capture_output=True,text=True)
        self.assertEqual(imports.returncode, 0, imports.stderr)

    def test_source_service_uses_injected_storage_and_existing_checkpoint(self):
        self.store._clue_pipeline = self.fixture.pipeline
        radar = source_module('wechat_radar_pipeline').RadarPipeline(self.store)
        service = source_module('service').WechatSource(self.store,self.fixture.pipeline,radar)
        self.assertIs(service.clues, self.fixture.pipeline)
        self.assertIs(service.radar, radar)
        self.fixture.pipeline.call_pi = lambda *args: self.fixture.answer()
        import threading, time
        result = service.analyze_clues('b'*32, {'body':'合成测试'}, threading.Event(), self.fixture.config, time.monotonic()+30)
        self.assertEqual(result[0], 'succeeded')
        self.assertEqual(service.clue_status()['processed_versions'], 1)
        self.assertEqual(service.status()['stages']['clues']['processed_versions'], 1)
        self.assertEqual(service.results()['total'], 1)

    def test_cli_run_rejects_remote_host_without_attempting_network(self):
        package = Path(source_module('results').__file__).parent
        for base in ('https://example.com','http://127.0.0.1.evil.test','http://user:secret@localhost:8766','http://localhost:8766/private'):
            result = subprocess.run([sys.executable,'-m','wechat_source','--workspace',str(self.store.workspace),'--base',base,'run','--kind','clues'],cwd=package.parent,capture_output=True,text=True)
            self.assertEqual(result.returncode,2)
            self.assertIn('任务触发地址须为本机',result.stderr)


class RadarQuarantineReaderTests(unittest.TestCase):
    """radar-batch-hardening: the read-only view of batches held out of the hourly replay."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.workspace = Path(self.temp.name)
        (self.workspace / 'knowledge').mkdir(mode=0o700)
        (self.workspace / 'radar-analysis').mkdir(mode=0o700)
        with sqlite3.connect(self.workspace / 'knowledge' / 'knowledge.db') as db:
            db.execute('CREATE TABLE documents(id TEXT PRIMARY KEY,hash TEXT,source TEXT,kind TEXT,title TEXT,body TEXT,modified TEXT,provenance TEXT,url TEXT)')
            db.execute('INSERT INTO documents VALUES(?,?,?,?,?,?,?,?,?)',
                       ('a'*32,'b'*64,'wechat','wechat','群 · 作者 · 日期','合成技术资料',
                        '2026-10-09T02:00:00+00:00',json.dumps({'chatroom_id':'fixture@chatroom'}),''))
        self.reader = Reader(self.workspace)
        self.package = Path(source_module('results').__file__).parent

    def ledger(self, quarantined=True):
        schema = source_module('wechat_radar_pipeline').SCHEMA
        with sqlite3.connect(self.workspace / 'radar-analysis' / 'assistant.db') as db:
            db.executescript(schema)
            db.execute("INSERT OR IGNORE INTO analyzed VALUES('z'*32,'y'*64,'fixture','radar-evidence-v1','r'*32,'2026-10-09T02:00:00+00:00')")
            if quarantined:
                db.execute('INSERT INTO quarantined VALUES(?,?,?,?,?,?)',
                           ('a'*32,'b'*64,'validation','2026-10-09T12:00:00+08:00','r'*32,'二次修复仍无效，本批已隔离'))
        (self.workspace / 'radar-analysis' / 'analysis-status.json').write_text(
            json.dumps({'policy':'radar-evidence-v1','model':'fixture','since':'2026-10-06T00:00:00+00:00'}, ensure_ascii=False))

    def test_view_names_the_reason_and_links_the_original_without_touching_any_file(self):
        self.ledger()
        before = {p: p.read_bytes() for p in self.workspace.rglob('*.db')}
        data = self.reader.radar_quarantine()
        self.assertTrue(data['available']); self.assertEqual(data['schema'], 'wechat-source/v1')
        self.assertEqual(data['counts'], {'validation': 1})
        item = data['items'][0]
        self.assertEqual((item['document_id'], item['hash'], item['error_kind'], item['run_id']),
                         ('a'*32, 'b'*64, 'validation', 'r'*32))
        self.assertEqual(item['title'], '群 · 作者 · 日期')
        self.assertIn('二次修复仍无效', item['detail'])
        self.assertIn('document=' + 'a'*32, item['url'])
        self.assertIn('隔离只停止每小时重放', data['scope'])
        for path, content in before.items(): self.assertEqual(path.read_bytes(), content)

    def test_a_held_batch_is_reported_separately_instead_of_pending_replay(self):
        self.ledger(quarantined=False)
        open_stage = self.reader.status()['stages']['radar']
        self.assertTrue(open_stage['available'])
        self.assertEqual((open_stage['pending_versions'], open_stage['quarantined_versions']), (1, 0))
        self.assertTrue(open_stage['has_pending'])
        self.ledger()
        held = self.reader.status()['stages']['radar']
        self.assertEqual((held['pending_versions'], held['quarantined_versions']), (0, 1))
        self.assertFalse(held['has_pending'])
        # Only the radar stage can hold batches; the clues stage never invents a count.
        self.assertIsNone(self.reader.status()['stages']['clues'].get('quarantined_versions'))

    def test_arguments_are_validated_and_a_missing_ledger_is_not_reported_as_empty(self):
        for limit in (0, 201, True, '5', None):
            with self.subTest(limit=limit), self.assertRaises(ValueError): self.reader.radar_quarantine(limit)
        with self.assertRaises(ValueError): self.reader.radar_quarantine()

    def test_cli_quarantine_reads_without_the_workbench_and_writes_nothing(self):
        self.ledger()
        paths = list(self.workspace.rglob('*.db'))
        before = {p: p.read_bytes() for p in paths}
        env = {**os.environ}; env.pop('PYTHONPATH', None)
        completed = subprocess.run([sys.executable,'-m','wechat_source','--workspace',str(self.workspace),'quarantine','--limit','5'],
                                   cwd=self.package.parent, env=env, capture_output=True, text=True)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        data = json.loads(completed.stdout)
        self.assertEqual(data['counts'], {'validation': 1})
        self.assertEqual(data['items'][0]['document_id'], 'a'*32)
        for path, content in before.items(): self.assertEqual(path.read_bytes(), content)
        # A retry needs the source service; the CLI must not write the ledger itself.
        refused = subprocess.run([sys.executable,'-m','wechat_source','--workspace',str(self.workspace),'--base','https://example.invalid','retry','--document','a'*32],
                                 cwd=self.package.parent, env=env, capture_output=True, text=True)
        self.assertEqual(refused.returncode, 2)
        self.assertIn('任务触发地址须为本机', refused.stderr)
        for path, content in before.items(): self.assertEqual(path.read_bytes(), content)

