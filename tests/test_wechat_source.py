import json
import os
from pathlib import Path
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
