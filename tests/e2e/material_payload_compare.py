"""Byte comparison of runtime dispatch with the unmodified checkout (no materials)."""
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import threading
from types import SimpleNamespace
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))


def capture(source):
    spec=importlib.util.spec_from_file_location('compare_runtime',source)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    request=lambda *a,**kw:None
    owner=SimpleNamespace(lock=threading.RLock(),turns=[{'id':'old','prompt':' \n旧要求  \n','answer':'旧回答','status':'done','time':'fixed'}],
                          client=request,request_model=request,folder=Path('/tmp/material-payload-fixed'),
                          image_skill=SimpleNamespace(entry_paths=lambda:[]))
    runtime=module.PiRuntime.__new__(module.PiRuntime);runtime.owner=owner;runtime.prepared=True;runtime.node='node';runtime.worker=source.parent/'pi-runtime/worker.mjs'
    process=SimpleNamespace(stdin=io.StringIO(),stdout=io.StringIO(json.dumps({'type':'done','status':'done','answer':'OK'})+'\n'),poll=lambda:0)
    job={'id':'fixed-job','prompt':' \n读一下这份素材\n','context':{'ui':{}},'policy':{'allowed_tools':[]},'conversation_id':'fixed-conversation',
         'cancel':False,'model_config':{'model':'fake','endpoint':'http://127.0.0.1:1/unused'},'_schedule_cwd':'/tmp/fixed-cwd'}
    with patch.object(module.subprocess,'Popen',return_value=process):
        # PiRuntime closes stdin; retain its data first without changing production code.
        saved=[]
        original=process.stdin.close
        process.stdin.close=lambda:(saved.append(process.stdin.getvalue()),original())
        runtime.run(job,'固定系统提示',[])
    return saved[0].encode()

if __name__=='__main__':
    baseline=Path(os.environ['MATERIAL_BASELINE_ROOT'])/'pi_runtime.py'
    current=capture(ROOT/'pi_runtime.py');previous=capture(baseline)
    assert current==previous,'无素材请求与基线出现字节差异'
    import hashlib
    print('无素材完整运行时请求字节一致：'+hashlib.sha256(current).hexdigest())
