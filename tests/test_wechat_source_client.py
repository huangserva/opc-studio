import json
import tempfile
from pathlib import Path
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from isolation_patches import clock_sequence, module_time, skip_sleep
from wechat_source_client import WechatSourceClient,RemoteCapture

class RemoteTests(unittest.TestCase):
    def test_workbench_never_instantiates_wechat_processors(self):
        from server import Store
        with tempfile.TemporaryDirectory() as root:
            store=Store(root);client=store.wechat_source
            self.assertIsInstance(client,WechatSourceClient)
            self.assertFalse(hasattr(store,'_clue_pipeline'));self.assertFalse(hasattr(store,'radar_pipeline'))
            self.assertEqual(client.base,'http://127.0.0.1:3000')
    def test_job_submission_never_transmits_model_key_or_raw_source(self):
        client=WechatSourceClient(SimpleNamespace(workspace=Path('/tmp')))
        rid='a'*32;calls=[]
        def response(path,payload=None):
            calls.append((path,payload))
            return {'executor':'wechat-source-daemon','run_id':rid,'status':'succeeded','exit_code':0,'output':'完成'}
        with patch.object(client,'request',side_effect=response):
            result=client.analyze_radar(rid,{'id':'b'*32,'body':'私密原文'},threading.Event(),{'api_key':'synthetic-secret'},time.monotonic()+10)
        self.assertEqual(result,('succeeded',0,'完成'))
        self.assertEqual(calls,[('run',{'kind':'radar','run_id':rid,'task_id':'b'*32})])
        self.assertNotIn('synthetic-secret',json.dumps(calls));self.assertNotIn('私密原文',json.dumps(calls))
    def test_cancellation_is_propagated_to_source_job(self):
        client=WechatSourceClient(SimpleNamespace(workspace=Path('/tmp')));cancel=threading.Event();cancel.set();rid='a'*32
        with patch.object(client,'request',side_effect=[{'executor':'wechat-source-daemon','run_id':rid,'status':'running'}, {'run_id':rid,'status':'cancelled','output':'已停止'}]) as request:
            result=client.analyze_clues(rid,{'id':'b'*32},cancel,{},time.monotonic()+10)
        self.assertEqual(result[0],'cancelled');self.assertEqual(request.call_args.args,('jobs/'+rid+'/cancel',{}))
    def test_deadline_preserves_final_source_statistics_after_async_cancel(self):
        client=WechatSourceClient(SimpleNamespace(workspace=Path('/tmp')));rid='a'*32
        output=json.dumps({'processed':274,'skipped':65,'clues':['b'*32],'has_more':True})
        replies=[{'executor':'wechat-source-daemon','run_id':rid,'status':'running'},
                 {'run_id':rid,'status':'running'}, {'run_id':rid,'status':'cancelled','output':output}]
        with patch.object(client,'request',side_effect=replies),module_time('wechat_source_client',sleep=skip_sleep):
            result=client.analyze_clues(rid,{'id':'c'*32},threading.Event(),{},time.monotonic()-1)
        self.assertEqual(result,('timeout',None,output))
    def test_cancellation_does_not_copy_another_job_receipt(self):
        client=WechatSourceClient(SimpleNamespace(workspace=Path('/tmp')));rid='a'*32;cancel=threading.Event();cancel.set()
        replies=[{'executor':'wechat-source-daemon','run_id':rid,'status':'running'},
                 {'run_id':'b'*32,'status':'cancelled','output':'wrong job'}]
        with patch.object(client,'request',side_effect=replies):
            result=client.analyze_clues(rid,{'id':'c'*32},cancel,{},time.monotonic()+1)
        self.assertNotIn('wrong job',result[2]);self.assertEqual(json.loads(result[2])['source_run_id'],rid)
    def test_slow_shutdown_keeps_only_this_jobs_durable_progress(self):
        for progress_id in ('a'*32,'d'*32):
            client=WechatSourceClient(SimpleNamespace(workspace=Path('/tmp')));rid='a'*32;cancel=threading.Event();cancel.set()
            progress={'run_id':progress_id,'processed':274,'skipped':65,'has_more':True,'clue_ids':['b'*32]}
            replies=[{'executor':'wechat-source-daemon','run_id':rid,'status':'running'},
                     {'run_id':rid,'status':'running'}, {'progress':progress}]
            with patch.object(client,'request',side_effect=replies),module_time('wechat_source_client',monotonic=clock_sequence([10,15])):
                result=client.analyze_clues(rid,{'id':'c'*32},cancel,{},99)
            data=json.loads(result[2])
            if progress_id==rid:
                self.assertEqual(data['processed'],274);self.assertTrue(data['receipt_pending']);self.assertEqual(data['clues'],['b'*32])
            else:self.assertNotIn('processed',data)
    def test_bad_filters_and_nonlocal_url_fail_before_request(self):
        for base in ('https://example.org','http://user:key@127.0.0.1:3000','http://127.0.0.1:3000/path'):
            with self.assertRaises(ValueError):WechatSourceClient(SimpleNamespace(workspace=Path('/tmp')),base)
        client=WechatSourceClient(SimpleNamespace(workspace=Path('/tmp')))
        with patch.object(client,'request') as request:
            for kwargs in ({'kind':'wrong'},{'offset':-1},{'limit':101}):
                with self.assertRaises(ValueError):client.results(**kwargs)
            request.assert_not_called()
    def test_closing_workbench_does_not_stop_source_capture(self):
        client=WechatSourceClient(SimpleNamespace(workspace=Path('/tmp')))
        with patch.object(client,'request') as request:RemoteCapture(client).close();request.assert_not_called()
