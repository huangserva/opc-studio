"""补充真实子进程恢复、HTTP跳转和请求回执验证。全部只访问本地假服务。"""
import contextlib
import copy
import datetime as dt
import http.server
import io
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

from test_video_radar import ROOT, FIX, CONFIG, AT, FixtureClient, load, item
import video_radar as v


class RecoveryTests(unittest.TestCase):
    def test_run_sigkill_counts_reserved_call_and_next_run_recovers(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            code='''
import sys,time,json
from pathlib import Path
sys.path.insert(0,sys.argv[1])
import video_radar as v
class Hold:
 def __init__(self,config,reserve,finish,deadline):self.reserve=reserve
 def request(self,method,path,params,query=False):
  self.reserve(dict(method=method,path=path,started_at=v.stamp(),status='in_progress'))
  print('reserved',flush=True)
  time.sleep(60)
v.run(Path(sys.argv[2]),'hourly',Hold)
'''
            proc=subprocess.Popen([sys.executable,'-c',code,str(ROOT/'scripts'),temp],stdout=subprocess.PIPE,text=True)
            try:
                self.assertEqual(proc.stdout.readline().strip(),'reserved')
                competitor=subprocess.run([sys.executable,str(ROOT/'scripts/video_radar.py'),'--root',temp,'--mode','hourly'],capture_output=True,text=True,timeout=3)
                self.assertEqual(competitor.returncode,0)
                self.assertIn('已有轮次持锁',competitor.stdout)
                os.kill(proc.pid,signal.SIGKILL);proc.wait(timeout=3)
                storage=root/'workspace/video-radar'
                stats=json.loads(next(storage.glob('daily-*.json')).read_text())
                self.assertEqual(stats['api_calls'],1)
                FixtureClient.fail, FixtureClient.requests, FixtureClient.at = set(), [], v.now()
                with contextlib.redirect_stdout(io.StringIO()):page=v.run(root,'hourly',FixtureClient)
                stats=json.loads(next(storage.glob('daily-*.json')).read_text())
                self.assertEqual(stats['api_calls'],11)
                self.assertEqual(stats['runs'][0]['status'],'interrupted')
                self.assertEqual(stats['runs'][0]['calls'][0]['status'],'interrupted_unknown')
                self.assertEqual(stats['runs'][0]['sources']['xiaohongshu']['status'],'interrupted')
                self.assertEqual(page['sources']['xiaohongshu']['status'],'ok')
            finally:
                if proc.poll() is None:proc.kill();proc.wait()
                proc.stdout.close()

    def test_sigkill_mid_json_write_preserves_old_page(self):
        with tempfile.TemporaryDirectory() as temp:
            page=Path(temp)/'page.json';v.atomic_json(page,{'old':'完整'})
            code = """
import sys,time,os
from pathlib import Path
sys.path.insert(0,sys.argv[1]);import video_radar as v
def slow_dump(value,handle,**kwargs):
 handle.write('{"new": [');handle.flush();os.fsync(handle.fileno())
 print('mid_write',flush=True);time.sleep(60)
v.json.dump=slow_dump
v.atomic_json(Path(sys.argv[2]),{'new':[1,2,3]})
"""
            proc=subprocess.Popen([sys.executable,'-c',code,str(ROOT/'scripts'),str(page)],stdout=subprocess.PIPE,text=True)
            try:
                self.assertEqual(proc.stdout.readline().strip(),'mid_write')
                os.kill(proc.pid,signal.SIGKILL);proc.wait(timeout=3)
                self.assertEqual(json.loads(page.read_text()),{'old':'完整'})
                leftover=list(page.parent.glob('.video-radar-*.tmp'))
                self.assertEqual(len(leftover),1)
                with self.assertRaises(ValueError):json.loads(leftover[0].read_text())
                v.cleanup(page.parent,AT,30)
                self.assertFalse(list(page.parent.glob('.video-radar-*.tmp')))
            finally:
                if proc.poll() is None:proc.kill();proc.wait()
                proc.stdout.close()

    def test_snapshot_retention_30_calendar_days_boundary(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);snapshot=root/'snapshots';snapshot.mkdir()
            expired=snapshot/(AT.date()-dt.timedelta(days=30)).isoformat();expired.mkdir()
            retained=snapshot/(AT.date()-dt.timedelta(days=29)).isoformat();retained.mkdir()
            v.cleanup(root,AT,30)
            self.assertFalse(expired.exists());self.assertTrue(retained.exists())

    def test_actual_http_redirect_does_not_hit_destination(self):
        visits=[]
        class Destination(http.server.BaseHTTPRequestHandler):
            def log_message(self,*a):pass
            def do_GET(self):
                visits.append(self.path);self.send_response(200);self.end_headers();self.wfile.write(b'{"code":200}')
        destination=http.server.ThreadingHTTPServer(('127.0.0.1',0),Destination)
        class Redirect(http.server.BaseHTTPRequestHandler):
            def log_message(self,*a):pass
            def do_GET(self):
                self.send_response(302);self.send_header('Location',f'http://127.0.0.1:{destination.server_port}/tripwire');self.end_headers()
        origin=http.server.ThreadingHTTPServer(('127.0.0.1',0),Redirect)
        servers=[origin,destination]
        threads=[threading.Thread(target=s.serve_forever,daemon=True) for s in servers]
        for t in threads:t.start()
        run=v.subprocess.run
        def local(args,**kw):
            args=list(args);args[args.index('=https')]='=http'
            return run(args,**kw)
        try:
            with mock.patch.object(v,'validate_gateway'),mock.patch.object(v.subprocess,'run',side_effect=local):
                with self.assertRaisesRegex(v.RadarError,'http_302'):
                    v.curl_json(f'http://127.0.0.1:{origin.server_port}/start','GET',None,'fake-test-secret',1)
            self.assertEqual(visits,[])
        finally:
            for s in servers:s.shutdown();s.server_close()
            for t in threads:t.join(timeout=2)

    def test_null_and_malformed_records_do_not_crash_source_parser(self):
        data={'code':200,'data':[{'status_code':0,'business_data':[{'data':None},{'data':{'aweme_info':{'aweme_id':'123','aweme_type':0,'media_type':4,'author':42}}}]}]}
        rows=v.envelope(data,'multi')
        self.assertEqual(len(rows),1)
        self.assertIsNone(v.normalize(rows[0],'dy_multi',AT,CONFIG))
        raw=load('xhs_search')['data']['data']['items'][0]['note']
        raw['user']=False
        self.assertIsNotNone(v.normalize(raw,'xhs_latest',AT,CONFIG))
        raw['images_list']=[None]
        self.assertIsNone(v.normalize(raw,'xhs_latest',AT,CONFIG))

    def test_snapshot_date_symlink_cannot_receive_writes(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);storage=root/'workspace/video-radar'
            target=root/'sentinel';target.mkdir();(target/'keep').write_text('alive')
            (storage/'snapshots').mkdir(parents=True)
            (storage/'snapshots'/AT.date().isoformat()).symlink_to(target,target_is_directory=True)
            FixtureClient.fail,FixtureClient.requests,FixtureClient.at=set(),[],AT
            with mock.patch.object(v,'now',return_value=AT),contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaisesRegex(v.RadarError,'snapshot_symlink_refused'):v.run(root,'hourly',FixtureClient)
            self.assertEqual([p.name for p in target.iterdir()],['keep'])

    def test_actual_response_time_not_generation_time(self):
        with tempfile.TemporaryDirectory() as temp:
            FixtureClient.fail,FixtureClient.requests=set(),[]
            FixtureClient.at=AT-dt.timedelta(minutes=4)
            with mock.patch.object(v,'now',return_value=AT),contextlib.redirect_stdout(io.StringIO()):
                page=v.run(Path(temp),'hourly',FixtureClient)
            self.assertEqual(page['generated_at'],v.stamp(AT))
            for rows in page['lists'].values():
                for row in rows:self.assertEqual(row['last_seen_at'],v.stamp(FixtureClient.at))

    def test_unknown_business_code_is_failure_and_empty_is_unverified(self):
        for payload,kind in [({'code':500,'data':{'data':[{}]}},'index'),({'code':200,'data':{'code':0,'success':True,'data':[]}},'xhs_detail'),({'code':200,'data':[{'status_code':0,'business_data':[]}]},'multi')]:
            with self.assertRaises(v.RadarError):v.envelope(payload,kind)

    def test_first_seen_persists_for_daily_items_outside_page_top50(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            FixtureClient.fail,FixtureClient.requests,FixtureClient.at=set(),[],AT
            with mock.patch.object(v,'now',return_value=AT),contextlib.redirect_stdout(io.StringIO()):first=v.run(root,'daily',FixtureClient)
            state=json.loads((root/'workspace/video-radar/state.json').read_text())
            hidden=next(i for i in state['daily']['dy_index'] if i['key'] not in {r['key'] for r in first['lists']['douyin_daily']})
            FixtureClient.at=AT+dt.timedelta(days=1)
            with mock.patch.object(v,'now',return_value=FixtureClient.at),contextlib.redirect_stdout(io.StringIO()):v.run(root,'daily',FixtureClient)
            state=json.loads((root/'workspace/video-radar/state.json').read_text())
            after=next(i for i in state['daily']['dy_index'] if i['key']==hidden['key'])
            self.assertEqual(after['first_seen_at'],hidden['first_seen_at'])


if __name__=='__main__':unittest.main()
