import importlib.util
import sys
import json
import threading
import types
import unittest
from pathlib import Path
from unittest.mock import patch
from generation import progress
from tests.isolation_patches import WORK_BACKEND_REASON, work_backend_available

class LiveProgressTests(unittest.TestCase):
    # hygiene-4：被测代码是本机工作副本 work/image-reference-fix/backend/live_progress.py，
    # /work/ 被 .gitignore 第 13 行排除，不进公开仓库。
    @unittest.skipUnless(work_backend_available(), WORK_BACKEND_REASON)
    def test_placeholder_foreign_events_and_binary_frames_do_not_invent_steps(self):
        stop=threading.Event();service_stop=threading.Event();seen=[]
        messages=[b'preview',
            {'type':'executing','data':{'prompt_id':'ours','node':'8'}},
            {'type':'progress_state','data':{'prompt_id':'ours','nodes':{'8':{'state':'running','value':0.0,'max':1.0}}}},
            {'type':'progress','data':{'prompt_id':'other','node':'8','value':19,'max':20}},
            {'type':'progress','data':{'value':7,'max':20}},
            {'type':'progress_state','data':{'prompt_id':'ours','nodes':{'8':{'state':'running','value':0.0,'max':1.0}}}},
            {'type':'executing','data':{'prompt_id':'ours','node':'9'}}]
        class Socket:
            def recv(self):
                if not messages:stop.set();return ''
                value=messages.pop(0);return value if isinstance(value,bytes) else json.dumps(value)
            def close(self):pass
        fake=types.SimpleNamespace(create_connection=lambda *a,**k:Socket(),WebSocketTimeoutException=TimeoutError,WebSocketException=OSError)
        source=Path(__file__).resolve().parents[1]/'work/image-reference-fix/backend/live_progress.py'
        spec=importlib.util.spec_from_file_location('live_sampling_under_test',source)
        module=importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules,{'websocket':fake,'progress':progress}):
            spec.loader.exec_module(module)
            module.observe(8192,'client','ours',{'8':{'class_type':'KSampler'},'9':{'class_type':'VAEDecode'}},'image',seen.append,stop,service_stop)
        self.assertNotIn('total',seen[1])
        self.assertEqual([p['current'] for p in seen if 'current' in p],[7,7])
        self.assertEqual(seen[-1]['phase'],'decoding')
        self.assertNotIn('current',seen[-1])

if __name__=='__main__':unittest.main()
