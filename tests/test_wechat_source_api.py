import json
from pathlib import Path
import sqlite3
import unittest
import urllib.error
from unittest.mock import patch
import test_clues_api as fixtures


from isolation_patches import WECHAT_SOURCE_REASON, wechat_source_available
class SourceAPITests(unittest.TestCase):
    # hygiene-4：本类的每个用例都要把微信来源插件装起来才能跑（source_module 或
    # patch model_service_status，而后者在模块级就会调 loader）。插件不在公开仓库里，
    # 缺它时整类跳过。本机配置齐全时逐条照跑，行为与从前一致。
    @unittest.skipUnless(wechat_source_available(), WECHAT_SOURCE_REASON)
    def setUp(self):
        self.fixture = fixtures.ClueAPITests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        # Explicit fixture adapter; production Store uses only the Radar API client.
        from wechat_source_loader import source_module
        store=self.fixture.store
        store._wechat_source=source_module('service').WechatSource(store,store.clue_pipeline,source_module('wechat_radar_pipeline').RadarPipeline(store))

    def test_contract_returns_source_only_and_unknown_coverage_is_explicit(self):
        f = self.fixture
        with f.store.knowledge.db() as db:
            db.execute("UPDATE documents SET source='wechat' WHERE id=?", (f.doc['id'],))
        saved = f.new()
        response = f.api('/api/sources/wechat/results?kind=clues')
        self.assertEqual(response['owner'],'wechat-assistant')
        self.assertEqual(response['items'][0]['id'],saved['id'])
        self.assertEqual(response['items'][0]['evidence'][0]['hash'],f.doc['hash'])
        status = f.api('/api/sources/wechat/status')
        self.assertEqual(status['schema'],'wechat-source/v1')
        self.assertEqual(status['stages']['clues']['pending_versions'],1)
        self.assertIsNone(status['stages']['radar']['pending_versions'])
        legacy = f.api('/api/clues/wechat/status')
        self.assertEqual(legacy['owner'],'wechat-assistant')
        self.assertEqual(legacy['pending_versions'],1)

    def test_run_uses_exact_existing_task_no_new_model_or_scope(self):
        scheduler = self.fixture.srv.schedules
        with patch.object(scheduler,'listing',return_value={'tasks':[{'kind':'wechat_clues','id':'a'*32}]}), patch.object(scheduler,'claim',return_value={'id':'b'*32,'status':'running'}) as claim:
            result = self.fixture.api('/api/sources/wechat/run',{'kind':'clues'})
            self.assertEqual(result['run_id'],'b'*32)
            claim.assert_called_once_with('a'*32,'manual')
            with self.assertRaises(urllib.error.HTTPError) as exc:
                self.fixture.api('/api/sources/wechat/run',{'kind':'clues'},headers={'Origin':'https://evil.invalid'})
            self.assertEqual(exc.exception.code,403); exc.exception.close()
            self.assertEqual(claim.call_count,1)

    def test_ambiguous_or_missing_tasks_and_extra_scope_are_rejected(self):
        scheduler = self.fixture.srv.schedules
        for tasks in ([],[{'kind':'wechat_radar','id':'a'*32},{'kind':'wechat_radar','id':'b'*32}]):
            with patch.object(scheduler,'listing',return_value={'tasks':tasks}), patch.object(scheduler,'claim') as claim:
                with self.assertRaises(urllib.error.HTTPError) as exc:
                    self.fixture.api('/api/sources/wechat/run',{'kind':'radar'})
                self.assertEqual(exc.exception.code,400); exc.exception.close(); claim.assert_not_called()
        with self.assertRaises(urllib.error.HTTPError) as exc:
            self.fixture.api('/api/sources/wechat/run',{'kind':'radar','radar_since':'2026-10-05'})
        self.assertEqual(exc.exception.code,400); exc.exception.close()

    def test_filters_fail_before_reading_or_triggering_analyzer(self):
        for query in ('kind=invalid','offset=-1','limit=101','account=other','kind=clues&kind=topics'):
            with self.assertRaises(urllib.error.HTTPError) as exc:
                self.fixture.api('/api/sources/wechat/results?'+query)
            self.assertEqual(exc.exception.code,400); exc.exception.close()

    def ledger(self, quarantined=True):
        from wechat_source_loader import source_module
        root = Path(self.fixture.store.workspace) / 'radar-analysis'; root.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(root / 'assistant.db') as db:
            db.executescript(source_module('wechat_radar_pipeline').SCHEMA)
            if quarantined:
                db.execute('INSERT OR REPLACE INTO quarantined VALUES(?,?,?,?,?,?)',
                           (self.fixture.doc['id'], self.fixture.doc['hash'], 'validation',
                            '2026-10-09T12:00:00+08:00', 'r'*32, '二次修复仍无效，本批已隔离'))

    def test_quarantine_view_and_retry_route_only_requeue_and_delete_nothing(self):
        self.ledger()
        view = self.fixture.api('/api/sources/wechat/radar-quarantine?limit=5')
        self.assertEqual(view['schema'], 'wechat-source/v1'); self.assertEqual(view['owner'], 'wechat-assistant')
        self.assertEqual(view['counts'], {'validation': 1})
        self.assertEqual(view['items'][0]['document_id'], self.fixture.doc['id'])
        self.assertIn('隔离只停止每小时重放', view['scope'])
        result = self.fixture.api('/api/sources/wechat/radar-quarantine/retry', {'document_ids':[self.fixture.doc['id']]})
        self.assertEqual(result['requeued'], 1)
        self.assertEqual(self.fixture.api('/api/sources/wechat/radar-quarantine')['items'], [])
        # Re-queueing is the only effect: the original text and the clue stay readable.
        with self.fixture.store.knowledge.db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM documents').fetchone()[0], 1)
        self.assertEqual(self.fixture.api('/api/clues')['total'], 0)

    def test_quarantine_routes_reject_bad_arguments_and_never_clear_the_ledger(self):
        self.ledger()
        for query in ('limit=0','limit=201','limit=abc','limit=1&limit=2','extra=1'):
            with self.assertRaises(urllib.error.HTTPError) as exc:
                self.fixture.api('/api/sources/wechat/radar-quarantine?' + query)
            self.assertEqual(exc.exception.code, 400); exc.exception.close()
        for body in ({}, {'document_ids':[]}, {'document_ids':'x'}, {'document_ids':['a'*201]}, {'document_ids':['a'],'kind':'radar'}):
            with self.assertRaises(urllib.error.HTTPError) as exc:
                self.fixture.api('/api/sources/wechat/radar-quarantine/retry', body)
            self.assertEqual(exc.exception.code, 400); exc.exception.close()
        self.assertEqual(self.fixture.api('/api/sources/wechat/radar-quarantine')['counts'], {'validation': 1})
        with self.assertRaises(urllib.error.HTTPError) as exc:
            self.fixture.api('/api/sources/wechat/radar-quarantine/retry', {'document_ids':[self.fixture.doc['id']]},
                             headers={'Origin':'https://evil.invalid'})
        self.assertEqual(exc.exception.code, 403); exc.exception.close()
        self.assertEqual(self.fixture.api('/api/sources/wechat/radar-quarantine')['counts'], {'validation': 1})
