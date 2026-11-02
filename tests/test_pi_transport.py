"""Connection diagnostics use only synthetic providers and temporary workspaces."""
import fcntl
import importlib
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from isolation_patches import wechat_source_available, WECHAT_SOURCE_REASON
if not wechat_source_available():raise unittest.SkipTest(WECHAT_SOURCE_REASON)
from wechat_source_loader import source_module
pe=source_module('pi_executor')
WORKER=Path(os.environ.get('PI_CONNECTION_WORKER',Path(__file__).resolve().parents[1]/'pi-runtime/worker.mjs'))
FIELDS={'category','error_name','cause_code','http_status','elapsed_ms','attempt','retried','retry_skipped_reason'}
POISON='private-sentinel-not-a-key'


class WorkerProcessTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);self.root=Path(temp.name)
        self.actions=[];self.calls=0;self.release=threading.Event();test=self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_POST(self):
                self.rfile.read(int(self.headers.get('Content-Length',0)));test.calls+=1
                action=test.actions.pop(0) if test.actions else 'success'
                if action=='disconnect':self.connection.shutdown(socket.SHUT_RDWR);self.connection.close();return
                if action=='auth-disconnect':
                    self.send_response(401);self.send_header('Content-Length','100000');self.end_headers();self.wfile.write(b'{"error":');self.wfile.flush();self.connection.shutdown(socket.SHUT_RDWR);self.connection.close();return
                if action=='timeout':test.release.wait(3);return
                if action=='stream-timeout':
                    self.send_response(200);self.send_header('Content-Type','text/event-stream');self.end_headers();self.wfile.flush();test.release.wait(3);return
                if action=='partial-timeout':
                    self.send_response(200);self.send_header('Content-Type','text/event-stream');self.end_headers()
                    packet={'id':'fixture','object':'chat.completion.chunk','model':'fixture','choices':[{'index':0,'delta':{'content':'synthetic partial'},'finish_reason':None}]}
                    self.wfile.write(('data: '+json.dumps(packet)+'\n\n').encode());self.wfile.flush();test.release.wait(3);return
                if isinstance(action,tuple):
                    status,message=action;self.send_response(status);self.end_headers()
                    try:self.wfile.write(json.dumps({'error':{'message':message+' '+POISON+' https://private.invalid/?key='+POISON}}).encode())
                    except OSError:pass
                    return
                if isinstance(action,int):
                    self.send_response(action);self.end_headers()
                    try:self.wfile.write(json.dumps({'error':{'message':('model not found ' if action==404 else 'context length exceeded ')+POISON+' https://private.invalid/?key='+POISON}}).encode())
                    except OSError:pass
                    return
                self.send_response(200);self.send_header('Content-Type','text/event-stream');self.end_headers()
                packet={'id':'fixture','object':'chat.completion.chunk','model':'fixture','choices':[{'index':0,'delta':{'content':'OK'},'finish_reason':None}]}
                self.wfile.write(('data: '+json.dumps(packet)+'\n\n').encode());packet['choices'][0].update(delta={},finish_reason='stop')
                self.wfile.write(('data: '+json.dumps(packet)+'\n\ndata: [DONE]\n\n').encode());self.wfile.flush()
        self.server=ThreadingHTTPServer(('127.0.0.1',0),Handler);self.server.daemon_threads=True
        thread=threading.Thread(target=self.server.serve_forever,daemon=True);thread.start()
        def cleanup():self.release.set();self.server.shutdown();self.server.server_close();thread.join(2)
        self.addCleanup(cleanup)
        self.config={'model':'fixture','api_key':POISON,'endpoint':f'http://127.0.0.1:{self.server.server_port}/v1',
            'timeout_seconds':1,'context_tokens':8192,'max_output_tokens':128,'temperature':0,'no_api_key':False}
    def worker(self,config=None,node_args=None):
        cfg=config or self.config
        request=dict(type='run',job_id='synthetic',cwd=str(self.root),session_dir=str(self.root/'sessions'),config=cfg,system='Reply OK',tools=[],skill_paths=[],image_catalog=[],history=[],images=[],text='Reply OK',workflow_stage='scheduled')
        process=subprocess.Popen(['node',*(node_args or []),str(WORKER)],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,cwd=WORKER.parent)
        def cleanup_process():
            if process.poll() is None:process.kill()
            process.wait(timeout=5)
            for stream in (process.stdin,process.stdout,process.stderr):stream.close()
        self.addCleanup(cleanup_process)
        process.stdin.write(json.dumps(request)+'\n');process.stdin.flush()
        timer=threading.Timer(25,process.kill);timer.start();self.addCleanup(timer.cancel)
        events=[];output=''
        while True:
            line=process.stdout.readline()
            if not line:break
            output+=line;event=json.loads(line);events.append(event)
            if event.get('type')=='done':break
        process.wait(timeout=5);timer.cancel()
        stderr=process.stderr.read()
        self.assertEqual(process.returncode,0)
        self.assertNotIn(POISON,output+stderr)
        for path in self.root.rglob('*.jsonl'):self.assertNotIn(POISON,path.read_text())
        self.last_events=events
        return next(e for e in events if e.get('type')=='done')
    def test_success_real_sdk(self):
        done=self.worker();self.assertEqual(done['status'],'done');self.assertEqual(done['answer'],'OK');self.assertNotIn('transport_diagnostic',done)
    def test_http_categories_and_provider_message_never_persist(self):
        for status,category in [(401,'authentication'),(429,'rate_limit'),(413,'context_limit'),(400,'context_limit'),(404,'local_config')]:
            with self.subTest(status=status):
                self.actions=[status];done=self.worker();d=done['transport_diagnostic']
                self.assertEqual(d['category'],category);self.assertEqual(d['http_status'],status);self.assertLessEqual(set(d),FIELDS)
                self.assertEqual(done['status'],'error')
    def test_disconnect_is_network_connection(self):
        self.actions=['disconnect'];done=self.worker();d=done['transport_diagnostic']
        self.assertEqual(d['category'],'network_connection');self.assertIn(d['cause_code'],pe.CAUSE_CODES)
    def test_dns_failure_is_local_simulation(self):
        hook=self.root/'dns.cjs'
        hook.write_text("const dns=require('node:dns');dns.lookup=(name,opts,cb)=>{if(typeof opts==='function')cb=opts;const e=new Error('private-sentinel-not-a-key');e.code='ENOTFOUND';queueMicrotask(()=>cb(e));};")
        cfg={**self.config,'endpoint':'http://synthetic.invalid/v1'}
        d=self.worker(cfg,['--require',str(hook)])['transport_diagnostic'];self.assertEqual(d['category'],'network_connection');self.assertEqual(d['cause_code'],'ENOTFOUND');self.assertEqual(self.calls,0)
    def test_timeout_is_classified(self):
        self.actions=['timeout'];done=self.worker();self.assertEqual(done['transport_diagnostic']['category'],'timeout')
    def test_executor_disconnect_then_success_real_processes(self):
        self.actions=['disconnect','success'];owner=SimpleNamespace(root=self.root,store=SimpleNamespace(workspace=self.root))
        engine=pe.PiExecutor(WORKER)
        with patch.object(pe,'backend_slots',return_value='ready'):
            answer=engine.execute(owner,'batch','Reply OK',threading.Event(),self.config,time.monotonic()+60)
        self.assertEqual(answer,'OK');self.assertEqual(self.calls,2)
        r=json.loads((self.root/'analysis-pi/batch/execution-receipt.json').read_text())
        self.assertEqual(r['model_connection_retries'],1)
    def test_http_nonretryable_categories_use_one_real_provider_attempt(self):
        owner=SimpleNamespace(root=self.root,store=SimpleNamespace(workspace=self.root));engine=pe.PiExecutor(WORKER)
        for status,category in [(401,'authentication'),(429,'rate_limit'),(400,'context_limit'),(404,'local_config')]:
            self.actions=[status];before=self.calls
            with self.subTest(status=status),patch.object(pe,'backend_slots',return_value='ready'):
                with self.assertRaises(pe.ModelDiagnosticError) as caught:
                    engine.execute(owner,'http-'+str(status),'Reply OK',threading.Event(),self.config,time.monotonic()+60)
            self.assertEqual(self.calls-before,1);self.assertEqual(caught.exception.model_diagnostic['category'],category)
    def test_redacted_timeout_compatibility_preserves_six_legacy_cases(self):
        owner=SimpleNamespace(root=self.root,store=SimpleNamespace(workspace=self.root));engine=pe.PiExecutor(WORKER)
        key='private-timeout-credential-sentinel';self.config.update(api_key=key,no_api_key=True)
        for status in (408,504):
            for case in ('url-only','credential-only','long-url-real-timeout'):
                message=('unavailable https://synthetic.invalid/?reason=timeout' if case=='url-only'
                    else 'unavailable '+key if case=='credential-only'
                    else 'unavailable https://synthetic.invalid/'+('a'*800)+' Request timed out.')
                expected=2 if case=='long-url-real-timeout' else 1
                self.actions=[(status,message)]*expected;before=self.calls;identity=str(status)+'-'+case
                with self.subTest(status=status,case=case),patch.object(pe,'backend_slots',return_value='ready'):
                    with self.assertRaises(ValueError) as caught:
                        engine.execute(owner,identity,'Reply OK',threading.Event(),self.config,time.monotonic()+60)
                self.assertEqual(self.calls-before,expected)
                self.assertEqual(getattr(caught.exception,'failure_kind',None),'model_timeout' if expected==2 else 'model_failed')
                self.assertEqual(caught.exception.model_diagnostic['category'],'timeout' if expected==2 else 'unknown')
                receipt=json.loads((self.root/'analysis-pi'/identity/'execution-receipt.json').read_text())
                self.assertEqual(receipt['model_timeout_retries'],expected-1)
                for path in self.root.rglob('*.jsonl'):self.assertNotIn(key,path.read_text())
    def test_executor_watchdog_preserves_unresponsive_single_physical_call(self):
        owner=SimpleNamespace(root=self.root,store=SimpleNamespace(workspace=self.root));engine=pe.PiExecutor(WORKER)
        for invalid in (POISON,True,['watchdog']):
            self.assertNotIn('timeout_source',pe.clean_model_diagnostic({'category':'timeout','timeout_source':invalid}))
        # Prove that the partial-SSE fixture really reaches the SDK text stream.
        self.actions=['partial-timeout'];self.worker()
        self.assertTrue(any(e.get('type')=='text' and 'synthetic partial' in e.get('delta','') for e in self.last_events))
        for mode in ('timeout','stream-timeout','partial-timeout'):
            self.actions=[mode,mode];before=self.calls;identity='watchdog-'+mode
            with self.subTest(mode=mode),patch.object(pe,'backend_slots',return_value='ready'):
                with self.assertRaises(ValueError) as caught:
                    engine.execute(owner,identity,'Reply OK',threading.Event(),self.config,time.monotonic()+60)
            self.assertRegex(str(caught.exception),'模型无响应');self.assertEqual(self.calls-before,1)
            d=caught.exception.model_diagnostic;self.assertEqual(d['category'],'timeout');self.assertEqual(d['timeout_source'],'watchdog')
            receipt=json.loads((self.root/'analysis-pi'/identity/'execution-receipt.json').read_text())
            self.assertEqual(receipt['model_timeout_retries'],0);self.assertEqual(len(receipt['attempts']),1)
    def test_http_timeout_statuses_preserve_legacy_retry_decision(self):
        owner=SimpleNamespace(root=self.root,store=SimpleNamespace(workspace=self.root));engine=pe.PiExecutor(WORKER)
        for status in (408,504):
            for timed_out in (True,False):
                message='Request timed out.' if timed_out else 'unavailable'
                self.actions=[(status,message)]*(2 if timed_out else 1);before=self.calls
                expected=pe.ModelTimeoutError if timed_out else pe.ModelDiagnosticError
                identity='http-'+str(status)+'-'+str(timed_out)
                with self.subTest(status=status,timed_out=timed_out),patch.object(pe,'backend_slots',return_value='ready'):
                    with self.assertRaises(ValueError) as caught:
                        engine.execute(owner,identity,'Reply OK',threading.Event(),self.config,time.monotonic()+60)
                self.assertIsInstance(caught.exception,expected)
                self.assertEqual(self.calls-before,2 if timed_out else 1)
                self.assertEqual(caught.exception.model_diagnostic['category'],'timeout' if timed_out else 'unknown')
                self.assertEqual(caught.exception.model_diagnostic['http_status'],status)
                self.assertEqual(caught.exception.model_diagnostic['retry_skipped_reason'],'attempt_limit' if timed_out else 'non_retryable')
        # A timeout word in a non-retryable HTTP error must not override its category.
        for status,category in ((401,'authentication'),(429,'rate_limit'),(400,'local_config'),(404,'local_config')):
            self.actions=[(status,'Request timed out.')];before=self.calls
            with self.subTest(nonretryable=status),patch.object(pe,'backend_slots',return_value='ready'):
                with self.assertRaises(pe.ModelDiagnosticError) as caught:
                    engine.execute(owner,'nonretry-'+str(status),'Reply OK',threading.Event(),self.config,time.monotonic()+60)
            self.assertEqual(self.calls-before,1);self.assertEqual(caught.exception.model_diagnostic['category'],category)
    def test_truncated_401_body_still_never_retries(self):
        self.actions=['auth-disconnect'];owner=SimpleNamespace(root=self.root,store=SimpleNamespace(workspace=self.root));engine=pe.PiExecutor(WORKER)
        with patch.object(pe,'backend_slots',return_value='ready'):
            with self.assertRaises(pe.ModelDiagnosticError) as caught:
                engine.execute(owner,'truncated-auth','Reply OK',threading.Event(),self.config,time.monotonic()+60)
        self.assertEqual(caught.exception.model_diagnostic['category'],'authentication')
        self.assertEqual(caught.exception.model_diagnostic['http_status'],401);self.assertEqual(self.calls,1)
    def test_timeout_after_headers_is_classified(self):
        # A second endpoint closes after headers: fetch succeeded but body consumption failed.
        self.actions=['stream-timeout'];done=self.worker();self.assertEqual(done['transport_diagnostic']['category'],'timeout')
    def test_js_classifier_nested_aggregate_and_bad_inputs(self):
        script='''import assert from 'node:assert/strict';import {transportDiagnostic as d} from WORKER;
const poison='private-sentinel-not-a-key';
assert.equal(d(new Error(poison,{cause:new Error(poison,{cause:{code:'ECONNRESET'}})})).cause_code,'ECONNRESET');
assert.equal(d(new AggregateError([{code:'ENOTFOUND'},{code:'ECONNRESET'}],poison)).cause_code,'ENOTFOUND');
assert.equal(d(new Error(poison,{cause:{code:'CERT_HAS_EXPIRED'}})).category,'network_connection');
const cycle={};cycle.cause=cycle;assert.equal(d(cycle).category,'unknown');
for(const e of [null,'bad',42,{},new Error('x'.repeat(100000)),{code:'ECONNRESET'},{status:401},{code:'SECRET_UNKNOWN'},Object.defineProperty({},'name',{get(){throw Error(poison)}})])assert.equal(d(e).category,'unknown');
for(const [status,category] of [[401,'authentication'],[429,'rate_limit'],[413,'context_limit'],[404,'local_config']])assert.equal(d(Object.assign(new Error(poison),{status})).category,category);
assert.equal(d(Object.assign(new Error('context length exceeded '+poison),{status:400})).category,'context_limit');
assert(!JSON.stringify(d(new Error(poison))).includes(poison));process.stdout.write('classifier checks passed');process.exit(0);'''.replace('from WORKER','from '+json.dumps(WORKER.as_uri()))
        cp=subprocess.run(['node','--input-type=module','-e',script],capture_output=True,text=True,timeout=20,cwd=WORKER.parent)
        self.assertEqual(cp.returncode,0,cp.stderr);self.assertNotIn(POISON,cp.stdout)


import test_wechat_radar_pipeline as fixtures
class PipelineDiagnosticTests(unittest.TestCase):
    setUp=fixtures.RadarTest.setUp
    add=fixtures.RadarTest.add
    def analyze(self,error):
        self.cp.call_pi=lambda *a:(_ for _ in ()).throw(error)
        with patch('wechat_radar_pipeline.backend_slots',return_value='ready'):
            state,_,out=self.pipeline.analyze('diag',{'radar_since':self.pipeline.since,'body':'synthetic'},threading.Event(),{'model':'synthetic','context_tokens':10000,'max_output_tokens':1000},time.monotonic()+60)
        return state,json.loads(out)
    def test_safe_child_propagates_parent_kind_and_failure_storage(self):
        exc=pe.ModelDiagnosticError({'category':'authentication','http_status':401,'message':'poison-not-a-key','error_name':'poison-not-a-key','url':'https://private.invalid/?key=poison-not-a-key','retried':False,'retry_skipped_reason':'non_retryable'})
        state,data=self.analyze(exc)
        self.assertEqual(state,'failed');self.assertEqual(data['error_kind'],'model_failed')
        self.assertEqual(data['model_diagnostic']['category'],'authentication');self.assertNotIn('poison-not-a-key',json.dumps(data))
        with self.pipeline.db() as db:
            row=db.execute('SELECT error,error_kind FROM failures WHERE run_id=?',('diag',)).fetchone()
        self.assertEqual(row['error_kind'],'model_failed');self.assertEqual(json.loads(row['error'])['model_diagnostic'],data['model_diagnostic'])
    def test_legacy_missing_child_keeps_old_shape(self):
        _,data=self.analyze(ValueError('模型执行失败'))
        self.assertEqual(data['error_kind'],'model_failed');self.assertNotIn('model_diagnostic',data)
    def test_poisoned_exception_child_is_sanitized_again(self):
        exc=ValueError('模型执行失败');exc.model_diagnostic={'category':'poison-not-a-key','cause_code':'poison-not-a-key','http_status':'https://private.invalid','message':'poison-not-a-key'}
        _,data=self.analyze(exc)
        self.assertEqual(data['model_diagnostic']['category'],'unknown');self.assertNotIn('poison-not-a-key',json.dumps(data))

if __name__=='__main__':unittest.main()
