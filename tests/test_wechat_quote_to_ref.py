"""Batch-local exact matching; synthetic inputs only."""
import json
import unittest
from wechat_source_loader import source_module
import test_clue_retry as clues
import test_wechat_radar_pipeline as radar

mapping=source_module('quote_refs')

class MappingTests(unittest.TestCase):
    def refs(self,text='你好， 模型！'):
        return {'e0':{'id':'a','text':text,'start':0,'end':len(text)}}
    def test_exact_and_allowed_normalization(self):
        for quote in ['你好， 模型！',' 你好,   模型!\n',{'text':'你好,\t模型!'},{'id':'a','text':'你好, 模型!'},{'ref':'你好, 模型!'}]:
            stats={};self.assertEqual(mapping.quote_ref(quote,self.refs(),stats),'e0');self.assertEqual(stats,{'quote_mapped':1})
        stats={};self.assertEqual(mapping.quote_ref('e0',self.refs(),stats),'e0');self.assertEqual(stats,{})
        self.assertEqual(mapping.quote_ref('测试｡引用､结束',self.refs('测试。引用、结束')),'e0')
    def test_ambiguous_full_span_and_substrings_and_cross_batch_reject(self):
        refs={**self.refs(),'e1':{'id':'b','text':'你好, 模型!'}}
        with self.assertRaisesRegex(ValueError,'多处命中'):mapping.quote_ref({'id':'a','text':'你好， 模型！'},refs)
        for quote in ['模型','你好，','另一批原文','e99',{'id':'old','text':'你好， 模型！'}]:
            with self.assertRaises(ValueError):mapping.quote_ref(quote,self.refs())
    def test_no_compatibility_letters_digits_semantics_or_hidden_chars(self):
        for original,quote in [('Ａ模型','A模型'),('①项','1项'),('模型…','模型...'),('模型技术','模型 技术'),('模型\u200b技术','模型技术'),('你好，模型','你好模型')]:
            with self.assertRaises(ValueError):mapping.quote_ref({'text':quote},self.refs(original))
    def test_text_field_is_never_interpreted_as_ref(self):
        with self.assertRaises(ValueError):mapping.quote_ref({'text':'e0'},self.refs('另一段原文'))
        self.assertEqual(mapping.quote_ref({'text':'e0'},self.refs('e0')),'e0')

class ClueMappingTests(unittest.TestCase):
    def setUp(self):
        clues.ClueRetryTests.setUp(self)
        self.doc=self.add("sample","合成技术原文，必须保留真实出处！")
    add=clues.ClueRetryTests.add
    model=clues.ClueRetryTests.model
    run_analysis=clues.ClueRetryTests.run_analysis
    seen=clues.ClueRetryTests.seen
    candidate=staticmethod(clues.ClueRetryTests.candidate)
    def test_saved_receipt_maps_unique_text_and_preserves_original_span(self):
        quote=self.doc['body']
        self.model(json.dumps({'clues':[{**self.candidate('e0'),'quotes':[{'id':self.doc['id'],'text':'  '+quote.replace('，',',').replace('！','!')+'  '}]}]},ensure_ascii=False))
        status,data=self.run_analysis('a',incremental=True)
        self.assertEqual(status,'succeeded');self.assertEqual(data['format']['quote_mapped'],1)
        self.assertTrue(self.seen(self.doc));self.assertEqual(len(data['clues']),1)
        self.assertEqual(self.pipeline.status()['progress']['format']['quote_mapped'],1)
        with self.store.clues.db() as db:
            evidence=json.loads(db.execute('select data from entries').fetchone()[0])['evidence'][0]
        self.assertEqual(evidence['excerpt'],quote);self.assertEqual(evidence['hash'],self.doc['hash'])
    def test_ambiguous_mapping_drops_and_requeues_original_documents(self):
        other=self.add('same',self.doc['body'])
        self.model(json.dumps({'clues':[{**self.candidate('e0'),'quotes':[{'text':self.doc['body']}]}]},ensure_ascii=False))
        status,data=self.run_analysis('b',incremental=True)
        self.assertEqual(status,'failed');self.assertEqual(data['clues'],[])
        self.assertTrue(any('多处命中' in d['reason'] for d in data['dropped']))
        self.assertFalse(self.seen(self.doc));self.assertFalse(self.seen(other))

class RadarMappingTests(unittest.TestCase):
    setUp=radar.RadarTest.setUp
    add=radar.RadarTest.add
    result=radar.RadarTest.result
    def test_unique_maps_all_lanes_and_keeps_owner_check(self):
        _,refs,catalog=self.pipeline.prepare([self.doc])
        findings=self.pipeline.validate(self.result([self.doc['body']]),refs,catalog,{self.doc['id']})
        self.assertEqual(len(findings),3);self.assertEqual(self.pipeline.format_stats['quote_mapped'],3)
        self.assertEqual(findings[0]['evidence'][0]['text'],self.doc['body'])
        other=self.add('other','他人的技术原文',False);_,refs,catalog=self.pipeline.prepare([other])
        findings=self.pipeline.validate(self.result([other['body']]),refs,catalog,{'other'})
        self.assertEqual([f['kind'] for f in findings],['topics','knowledge']);self.assertEqual(self.pipeline.rejected_profiles,1)
    def test_ambiguous_partial_and_cross_batch_do_not_map(self):
        other=self.add('same',self.doc['body'],True);_,refs,catalog=self.pipeline.prepare([self.doc,other])
        self.assertEqual(self.pipeline.validate(self.result([self.doc['body']]),refs,catalog,{'a','same'}),[])
        self.assertTrue(any('多处命中' in reason for reason in self.pipeline.dropped_items))
        _,refs,catalog=self.pipeline.prepare([self.doc])
        for quote in [self.doc['body'][:5],'跨批原文','e99']:
            self.assertEqual(self.pipeline.validate(self.result([quote]),refs,catalog,{'a'}),[])

class ReceiptMappingTests(unittest.TestCase):
    def test_workbench_failure_recovers_only_valid_format_counts(self):
        import tempfile
        from scheduled_tasks import ScheduledTasks
        run_id='a'*32
        class Owner:
            def status(self):return {'progress':{'run_id':run_id,'processed':1,'skipped':0,'clue_ids':[],'format':{'quote_mapped':2,'field_names':-1,'ignored_fields':'wrong','unknown':10}}}
            def analyze(self):pass
        with tempfile.TemporaryDirectory() as directory:
            scheduler=ScheduledTasks(directory,autostart=False)
            try:
                scheduler.wechat_analyzer=Owner().analyze
                partial,out=scheduler._wechat_failure(run_id,{},ValueError('synthetic failure'))
                self.assertTrue(partial);self.assertEqual(json.loads(out)['format'],{'quote_mapped':2})
            finally:scheduler.close()
    def test_pending_source_receipt_keeps_quote_mapping_count(self):
        import threading
        from unittest.mock import patch
        from isolation_patches import clock_sequence, module_time
        from wechat_source_client import WechatSourceClient
        client=WechatSourceClient.__new__(WechatSourceClient);rid='a'*32;cancel=threading.Event();cancel.set()
        replies=[{'executor':'wechat-source-daemon','run_id':rid,'status':'running'}, {'run_id':rid,'status':'running'}, {'progress':{'run_id':rid,'processed':1,'skipped':0,'clue_ids':[],'has_more':True,'format':{'quote_mapped':3}}}]
        with patch.object(client,'request',side_effect=replies),module_time('wechat_source_client',monotonic=clock_sequence([10,15])):
            status,_,out=client.analyze_clues(rid,{'id':'b'*32},cancel,{},99)
        self.assertEqual(status,'cancelled');self.assertEqual(json.loads(out)['format']['quote_mapped'],3)
