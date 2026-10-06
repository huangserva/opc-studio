import json
import tempfile
from pathlib import Path
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
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
        with patch.object(client,'request',return_value={'executor':'wechat-source-daemon','run_id':rid,'status':'running'}) as request:
            result=client.analyze_clues(rid,{'id':'b'*32},cancel,{},time.monotonic()+10)
        self.assertEqual(result[0],'cancelled');self.assertEqual(request.call_args.args,('jobs/'+rid+'/cancel',{}))
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
