"""Real socket deadlines: slow bodies must not renew the six-second budget."""
import json,threading,time,unittest,subprocess,os
from pathlib import Path
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from types import SimpleNamespace
from wechat_source_client import WechatSourceClient,SourceUnavailable

class DeadlineTests(unittest.TestCase):
    def setUp(self):
        self.mode='normal';self.closed=threading.Event()
        owner=self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_GET(self):
                mode=owner.mode
                data={'schema':'wechat-source/v1','owner':'wechat-assistant','available':True,
                  'stages':{'clues':{'available':True,'processed_versions':387},'radar':{'available':True,'processed_versions':253}},'coverage':{'as_of':'saved','stale':False}}
                raw=json.dumps(data).encode()
                try:
                    if mode=='headers':time.sleep(9)
                    self.send_response(503 if mode=='error_body' else 200)
                    self.send_header('Content-Length',str(4+len(raw)) if mode in ('body','error_body') else str(len(raw) if mode!='invalid' else 3));self.end_headers()
                    if mode in ('body','error_body'):
                        def observe_close():
                            try:
                                if not self.connection.recv(1):owner.closed.set()
                            except OSError:owner.closed.set()
                        threading.Thread(target=observe_close,daemon=True).start()
                        for _ in range(4):self.wfile.write(b' ');self.wfile.flush();time.sleep(2.2)
                    self.wfile.write(b'bad' if mode=='invalid' else raw);self.wfile.flush()
                except (BrokenPipeError,ConnectionResetError):owner.closed.set()
        self.server=ThreadingHTTPServer(('127.0.0.1',0),Handler);self.server.daemon_threads=True
        threading.Thread(target=self.server.serve_forever,daemon=True).start()
        self.client=WechatSourceClient(SimpleNamespace(workspace='/tmp'),base='http://127.0.0.1:'+str(self.server.server_port))
    def tearDown(self):self.server.shutdown();self.server.server_close()
    def check_timeout(self,mode):
        self.mode=mode;start=time.perf_counter();data=self.client.status();elapsed=time.perf_counter()-start
        self.assertLess(elapsed,8);self.assertGreater(elapsed,5)
        self.assertFalse(data['available']);self.assertEqual(data['error_kind'],'timeout');self.assertEqual(data['error_stage'],'workbench_to_radar');self.assertTrue(data['coverage']['stale'])
        if mode in ('body','error_body'):self.assertTrue(self.closed.wait(.5),'deadline must shut down the socket')
        print(json.dumps({'platform':'python','mode':mode,'seconds':elapsed,'stage':data['error_stage'],'kind':data['error_kind']}),flush=True)
        return data
    def test_slow_body_22_seconds_per_byte(self):self.check_timeout('body')
    def test_late_headers_9_seconds(self):self.check_timeout('headers')
    def test_timeout_keeps_previous_nonzero_values_and_marks_stale(self):
        warm=self.client.status();data=self.check_timeout('body')
        self.assertEqual(data['stages']['clues']['processed_versions'],387);self.assertEqual(data['stages']['radar']['processed_versions'],253)
        self.assertEqual(data['coverage']['as_of'],warm['coverage']['as_of'])
    def test_error_body_is_also_bounded(self):self.check_timeout('error_body')
    def test_actual_ts_relay_timeout_keeps_workbench_old_values(self):
        radar=Path(__file__).resolve().parents[2]/'radar'
        child=subprocess.Popen(['node','--import','tsx','lib/source-w2-relay.fixture.ts'],cwd=radar,
            env={**os.environ,'WECHAT_RADAR_SOURCE_URL':self.client.base},stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True)
        try:
            relay='http://127.0.0.1:'+str(json.loads(child.stdout.readline())['port'])
            client=WechatSourceClient(SimpleNamespace(workspace='/tmp'),base=relay)
            warm=client.status();self.assertEqual(warm['stages']['clues']['processed_versions'],387)
            self.mode='body';start=time.perf_counter();data=client.status();elapsed=time.perf_counter()-start
            self.assertLess(elapsed,8);self.assertEqual(data['error_kind'],'timeout')
            self.assertIn(data['error_stage'],('workbench_to_radar','radar_to_source'))
            self.assertEqual(data['stages']['clues']['processed_versions'],387);self.assertEqual(data['coverage']['as_of'],'saved');self.assertTrue(data['coverage']['stale']);self.assertFalse(data['available'])
            print(json.dumps({'platform':'python-through-real-ts','mode':'body','seconds':elapsed,'stage':data['error_stage'],'kind':data['error_kind'],'old_count':387,'stale':True}),flush=True)
        finally:
            child.terminate();child.wait(timeout=5);child.stdout.close()
    def test_invalid_json_is_not_timeout(self):
        self.mode='invalid';start=time.perf_counter();data=self.client.status()
        self.assertLess(time.perf_counter()-start,1);self.assertEqual(data['error_kind'],'invalid_response')

if __name__=='__main__':unittest.main(verbosity=2)
