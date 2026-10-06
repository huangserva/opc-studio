import json
import unittest
import urllib.error
from unittest.mock import patch
import test_clues_api as fixtures


class SourceAPITests(unittest.TestCase):
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
