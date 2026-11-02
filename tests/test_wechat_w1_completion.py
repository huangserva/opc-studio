import json,threading,time,unittest
from unittest.mock import patch
import test_clue_pipeline as fixture

class W1CompletionTests(unittest.TestCase):
    setUp=fixture.PipelineTests.setUp
    tearDown=fixture.PipelineTests.tearDown
    add=fixture.PipelineTests.add

    def replay(self,processed=352,capture_error=None):
        p=self.pipeline;historical=[{'batch_id':'f'*64,'message':'历史失败','state':'needs_review','documents':9198,'attempts':3}]
        page=('partial' if processed else 'succeeded',None,json.dumps({'processed':processed,'skipped':0,'clues':[],
             'errors':[],'has_more':True,'has_runnable':False,'time_budget':bool(processed)}))
        with patch.object(p,'prepare_cache',side_effect=capture_error,return_value={'status':'done','fresh':True}),\
             patch.object(p,'requeue_recoverable_batches',return_value={}),patch.object(p,'analyze_page',return_value=page),\
             patch.object(p,'retry_queue',return_value=historical),patch.object(p,'pending',return_value=[self.doc]):
            status,_,output=p.analyze('b'*32,{'wechat_mode':'incremental','body':'fixture'},threading.Event(),self.config,time.monotonic()+1800)
        return status,json.loads(output)

    def test_C1_budget_and_historical_queue_remain_partial_continuation(self):
        status,d=self.replay();self.assertEqual(status,'partial');self.assertEqual(d['completion']['type'],'continuation')
        self.assertEqual(d['completion']['processed'],352);self.assertEqual(d['completion']['review_documents'],9198)
        self.assertEqual(d['completion']['current_error_count'],0)

    def test_C2_capture_failure_remains_failed_with_phase(self):
        status,d=self.replay(0,ValueError('微信采集或索引未成功：合成失败；未开始模型分析'))
        self.assertEqual(status,'failed');self.assertEqual(d['completion']['type'],'failed');self.assertEqual(d['completion']['phase'],'capture')

    def test_C3_no_new_work_legacy_failed_status_is_preserved_but_queue_is_distinguished(self):
        status,d=self.replay(0);self.assertEqual(status,'failed');self.assertEqual(d['completion']['type'],'continuation')
        self.assertEqual(d['completion']['reason_code'],'review_queue');self.assertEqual(d['completion']['current_error_count'],0)

import test_deals_review as deals_fixture
class W1DealReceiptTests(unittest.TestCase):
    setUp=deals_fixture.DealsReviewTests.setUp
    def test_D1_rotation_receipt_is_additive_and_original_partial_is_preserved(self):
        self.addCleanup(self.tmp.cleanup)
        evidence={'checked':6,'new_evidence':0,'proposals':0,'pending':98,'deferred':29,'has_more':True,'errors':[]}
        conflicts={'clues':0,'possible':0,'deferred':0,'has_more':False,'errors':[]}
        with patch.object(self.review,'evidence_phase',return_value=evidence),patch.object(self.review,'conflict_phase',return_value=conflicts),patch.object(self.review,'weekly_report_due',return_value=None):
            status,_,output=self.review.analyze('c'*32,{},threading.Event(),{},time.monotonic()+900)
        d=json.loads(output);self.assertEqual(status,'partial');self.assertEqual(d['completion']['type'],'continuation')
        self.assertEqual(d['completion']['rotation_limit'],6);self.assertEqual(d['completion']['remaining_count'],29)
