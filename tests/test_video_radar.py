import contextlib
import copy
import datetime as dt
import functools
import http.server
import io
import json
import os
from pathlib import Path
import plistlib
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'scripts'))
import video_radar as v

FIX = ROOT/'tests/fixtures/video_radar'
CONFIG = json.loads((ROOT/'scripts/video_radar_config.json').read_text())
AT = dt.datetime(2026, 10, 16, 12, 30, tzinfo=v.TZ)


def load(name):
    return json.loads((FIX/(name+'.json')).read_text())


def item(likes=40, at=AT):
    raw = copy.deepcopy(load('xhs_search')['data']['data']['items'][0]['note'])
    raw['liked_count'] = likes
    raw['timestamp'] = (AT-dt.timedelta(hours=3)).timestamp()
    return v.normalize(raw, 'xhs_latest', at, CONFIG)


class FixtureClient:
    fail = set()
    at = AT
    requests = []

    def __init__(self, config, reserve, finish, deadline):
        self.reserve, self.finish, self.deadline = reserve, finish, deadline

    def request(self, method, path, params, query=False):
        self.requests.append((method, path, copy.deepcopy(params), query))
        call = dict(method=method, path=path, started_at=v.stamp(self.at), status='in_progress')
        self.reserve(call)
        try:
            if any(x in path for x in self.fail):
                raise v.RadarError('fixture_failure')
            name = 'xhs_search' if 'search_notes' in path else 'xhs_detail' if 'get_video' in path else 'dy_multi' if 'multi_search' in path else 'dy_index' if 'item_query' in path else 'dy_board'
            if 'fetch_hot_total' in path and params.get('date_window') == 1:
                name = ('dy_board_low_fan_1h_p1' if 'low_fan' in path else 'dy_board_video_1h_p'+str(params['page']))
            call.update(status='http_ok', finished_at=v.stamp(self.at))
            return load(name), self.at
        finally:
            self.finish()


class RadarTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        FixtureClient.fail, FixtureClient.requests, FixtureClient.at = set(), [], AT

    def run_round(self, mode='auto', factory=FixtureClient):
        with mock.patch.object(v, 'now', return_value=FixtureClient.at), contextlib.redirect_stdout(io.StringIO()):
            return v.run(self.root, mode, factory)

    def test_schema_real_fixture_pipeline(self):
        page = self.run_round()
        self.assertEqual(set(page), {'schema','generated_at','keyword','sources','today','lists'})
        self.assertEqual(page['schema'], 'video-radar-page/v1')
        self.assertEqual(page['today']['api_calls'], 15)
        required = set(item())
        for source, rows in page['lists'].items():
            self.assertLessEqual(len(rows), v.LIMITS[source])
            self.assertEqual(rows, sorted(rows, key=lambda i:(-i['score'], i['key'])))
            for row in rows:
                self.assertEqual(set(row), required)
                self.assertEqual(row['url'], v.safe_url(row['url'], row['platform']))
                self.assertIn(row['relevance'], ('relevant','weak'))
                self.assertTrue(row['last_seen_at'].endswith('+08:00'))
        self.assertTrue(any('low_fan_hot' in i['flags'] for i in page['lists']['douyin_daily']))

    def test_pagination_parameters_and_daily_query(self):
        self.run_round()
        search = [q for q in FixtureClient.requests if 'search_notes' in q[1]]
        self.assertEqual(len(search), 6)
        self.assertEqual([q[2]['page'] for q in search], [1,2,3,1,2,3])
        self.assertNotIn('search_id', search[0][2])
        self.assertEqual(search[1][2]['search_id'], load('xhs_search')['data']['search_id'])
        self.assertEqual(search[2][2]['search_session_id'], load('xhs_search')['data']['search_session_id'])
        index = next(q for q in FixtureClient.requests if 'item_query' in q[1])
        self.assertEqual((index[0],index[2]['category_id'],index[3]), ('POST',0,True))
        multi = [q for q in FixtureClient.requests if 'multi_search' in q[1]]
        self.assertEqual(len(multi),1)
        self.assertEqual(multi[0][2]['cursor'],0)

    def test_source_failure_preserves_previous_list(self):
        previous = self.run_round()
        FixtureClient.fail = {'search_notes','get_video_note_detail','multi_search','item_query','fetch_hot_total'}
        FixtureClient.at += dt.timedelta(hours=2)
        failed = self.run_round('hourly')
        for source in ('xiaohongshu','douyin_realtime'):
            self.assertEqual(failed['sources'][source]['status'],'failed')
            self.assertEqual(failed['sources'][source]['last_success_at'],previous['sources'][source]['last_success_at'])
            self.assertEqual(failed['lists'][source],previous['lists'][source])
        self.assertEqual(failed['lists']['douyin_daily'],previous['lists']['douyin_daily'])
        self.assertGreater(failed['today']['api_calls'], previous['today']['api_calls'])

    def test_one_source_failure_does_not_block_other(self):
        FixtureClient.fail = {'search_notes'}
        page = self.run_round()
        self.assertEqual(page['sources']['xiaohongshu']['status'],'failed')
        self.assertEqual(page['sources']['douyin_realtime']['status'],'ok')
        self.assertEqual(page['sources']['douyin_daily']['status'],'ok')
        self.assertGreater(len(page['lists']['douyin_daily']),0)

    def test_daily_success_once_retry_max_three_and_rollover(self):
        FixtureClient.fail = {'item_query'}
        for _ in range(4):
            page = self.run_round('daily')
        self.assertEqual(len(FixtureClient.requests),15)
        self.assertEqual(page['sources']['douyin_daily']['status'],'partial')
        stats = json.loads((self.root/'workspace/video-radar/daily-2026-10-16.json').read_text())
        self.assertEqual(sum(r['daily_attempt'] for r in stats['runs']),3)
        FixtureClient.fail = set()
        FixtureClient.at += dt.timedelta(days=1)
        self.run_round('daily')
        self.run_round('daily')
        self.assertEqual(len(FixtureClient.requests),20)

    def test_daily_partial_retains_failed_subsource(self):
        page = self.run_round('daily')
        old = [r for r in page['lists']['douyin_daily'] if 'dy_index' in r['sources']]
        # 新的一天指数失败时，上一成功指数列表仍在，不会被四榜覆盖。
        FixtureClient.at += dt.timedelta(days=1)
        FixtureClient.fail = {'item_query'}
        page = self.run_round('daily')
        self.assertEqual(page['sources']['douyin_daily']['status'],'partial')
        self.assertTrue(set(r['key'] for r in old).issubset(set(r['key'] for r in page['lists']['douyin_daily'])))

    def test_request_observation_time_delta_and_negative_growth(self):
        earlier = item(40)
        later = item(100, AT+dt.timedelta(minutes=75))
        measured = v.observe(later, earlier)
        self.assertEqual(measured['likes_delta_per_hour'],48)
        self.assertEqual(measured['score'],48)
        self.assertEqual(measured['last_seen_at'],v.stamp(AT+dt.timedelta(minutes=75)))
        negative = v.observe(item(10,AT+dt.timedelta(hours=1)),earlier)
        self.assertEqual(negative['likes_delta_per_hour'],-30)
        self.assertEqual(v.observe(item(),None)['likes_delta_per_hour'],None)
        daily = v.observe(item(100,AT+dt.timedelta(hours=1)), earlier, True)
        self.assertEqual(daily['score'],daily['likes_per_hour'])

    def test_expiry_and_type_real_counterexamples(self):
        search = load('xhs_search')['data']['data']['items'] + [{'note': r['value']} for r in load('xhs_normal_counterexample')['records']]
        self.assertTrue(any(r['note']['type']!='video' for r in search))
        for row in search:
            raw = row['note']
            if raw['type'] != 'video':
                self.assertIsNone(v.normalize(raw,'xhs_latest',AT,CONFIG))
        multi = v.envelope(load('dy_multi'),'multi')
        self.assertTrue(any(r['aweme_type']==68 for r in multi))
        for raw in multi:
            if raw['aweme_type']!=0 or raw['media_type']!=4:
                self.assertIsNone(v.normalize(raw,'dy_multi',AT,CONFIG))
        raw = search[0]['note'].copy()
        raw['timestamp']=(AT-dt.timedelta(hours=25)).timestamp()
        self.assertIsNone(v.normalize(raw,'xhs_hot',AT,CONFIG))
        raw['timestamp']=(AT+dt.timedelta(seconds=1)).timestamp()
        self.assertIsNone(v.normalize(raw,'xhs_hot',AT,CONFIG))

    def test_seconds_milliseconds_timezone_and_unknown(self):
        self.assertEqual(v.published(1792112960),v.published(1792112960000))
        self.assertEqual(v.published(1792112960).isoformat(),'2026-10-16T09:09:20+08:00')
        self.assertEqual(v.published('2026-10-14 22:46:36').utcoffset(),dt.timedelta(hours=8))
        for invalid in (None,0,-1,'invalid',True):self.assertIsNone(v.published(invalid))
        for invalid in (None,'1万',-1,True,1.5):self.assertIsNone(v.integer(invalid))
        self.assertIsNone(v.integer(0,True));self.assertEqual(v.integer('0'),0)
        for row in v.envelope(load('dy_multi'),'multi'):
            candidate = v.normalize(row,'dy_multi',AT,CONFIG)
            if candidate:
                self.assertIsNone(candidate['author_fans']);self.assertIsNone(candidate['plays'])
        self.assertIsNone(item()['author_fans']);self.assertIsNone(item()['plays'])

    def test_http200_business_failures_and_missing_arrays(self):
        cases=[({'code':200,'data':{'code':1,'success':True,'data':{'items':[]}}},'xhs_search'),
               ({'code':200,'data':{'code':0,'success':True,'data':{}}},'xhs_search'),
               ({'code':200,'data':{'code':0,'data':{}}},'board'),
               ({'code':200,'data':{}},'index'),
               ({'code':200,'data':[{'status_code':4,'business_data':[]}]},'multi')]
        for payload,kind in cases:
            with self.subTest(kind=kind),self.assertRaises(v.RadarError):v.envelope(payload,kind)
        with self.assertRaisesRegex(v.RadarError,'empty_result_unverified'):
            v.envelope({'code':200,'data':{'data':[]}},'index')

    def test_url_whitelist(self):
        for platform,path in [('xiaohongshu','explore/abc123'),('douyin','video/123')]:
            host='www.xiaohongshu.com' if platform=='xiaohongshu' else 'www.douyin.com'
            good='https://'+host+'/'+path
            self.assertEqual(v.safe_url(good,platform),good)
            for bad in ['javascript:alert(1)','http://'+host+'/'+path,'https://evil.test/'+path,good+'%2f..',good+'/../other','https://'+host+'.evil/'+path,'https://'+host+'@evil/'+path,'https://'+host+':443/'+path,good+'\n']:
                self.assertEqual(v.safe_url(bad,platform),'')

    def test_relevance_substrings_activity_and_tools(self):
        for word in ('Aijing','dazai','Thai','said','AIR','Mai','chair','email'):
            self.assertIsNone(v.classify(word,CONFIG)[0])
        for word in ('AI电影','AI 短剧','ai动画','人工智能','Claude 写代码','DeepSeek','Cursor'):
            self.assertEqual(v.classify(word,CONFIG)[0],'relevant')
        for word in ('#我AI玩和平','ai作者团','#即梦AI创作者成长计划'):
            self.assertEqual(v.classify(word,CONFIG)[0],'relevant')
        self.assertEqual(v.classify('用AI生成了电影 #我AI玩和平',CONFIG)[0],'relevant')

    def test_relevance_labeled_precision(self):
        selected=[r for r in load('relevance_labels') if v.classify(r['text'],CONFIG)[0]=='relevant']
        self.assertGreaterEqual(sum(r['label']=='相关' for r in selected)/len(selected),.85)
        self.assertEqual(len(load('relevance_labels')),211)

    def test_detail_target_mismatch_is_partial(self):
        self.run_round('hourly')
        state=self.root/'workspace/video-radar/state.json'
        payload=json.loads(state.read_text())
        fake=item();fake['id']='notfound';fake['key']='xiaohongshu:notfound'
        payload['items'][fake['key']]=fake;v.atomic_json(state,payload)
        page=self.run_round('hourly')
        self.assertEqual(page['sources']['xiaohongshu']['status'],'partial')
        self.assertIn('detail_target_missing',page['sources']['xiaohongshu']['note'])

    def test_recollection_cap_min_likes_and_feedback_untouched(self):
        storage=self.root/'workspace/video-radar';storage.mkdir(parents=True)
        state={'items':{},'daily':{}}
        for i in range(40):
            candidate=item(9);candidate.update(key='xiaohongshu:fake'+str(i), id='fake'+str(i))
            state['items'][candidate['key']]=candidate
        v.atomic_json(storage/'state.json',state)
        (storage/'feedback.json').write_text('{"sentinel":true}')
        page=self.run_round('hourly')
        self.assertEqual(sum('get_video_note_detail' in q[1] for q in FixtureClient.requests),30)
        self.assertTrue(all(i['likes']>=10 for i in page['lists']['xiaohongshu']))
        self.assertEqual((storage/'feedback.json').read_text(),'{"sentinel":true}')

    def test_snapshot_cleanup_symlink_sentinel(self):
        storage=self.root/'store';storage.mkdir()
        sentinel=self.root/'sentinel';sentinel.mkdir();(sentinel/'keep').write_text('alive')
        snapshots=storage/'snapshots';snapshots.mkdir()
        (snapshots/'2020-01-01').symlink_to(sentinel, target_is_directory=True)
        old=snapshots/'2020-01-02';old.mkdir();(old/'old.json').write_text('{}')
        current=snapshots/'2026-10-16';current.mkdir();(current/'keep.json').write_text('{}')
        (storage/'.video-radar-crash.tmp').write_text('incomplete')
        v.cleanup(storage,AT,30)
        self.assertTrue((sentinel/'keep').exists());self.assertTrue((snapshots/'2020-01-01').is_symlink())
        self.assertFalse(old.exists());self.assertTrue((current/'keep.json').exists())
        self.assertFalse((storage/'.video-radar-crash.tmp').exists())

    def test_lock_two_processes_and_sigkill_recovery(self):
        store=self.root/'store';store.mkdir()
        code="import sys,time;from pathlib import Path;sys.path.insert(0,sys.argv[1]);import video_radar as v;h=v.acquire_lock(Path(sys.argv[2]));print('locked' if h else 'busy',flush=True);time.sleep(60) if h else None"
        first=subprocess.Popen([sys.executable,'-c',code,str(ROOT/'scripts'),str(store)],stdout=subprocess.PIPE,text=True)
        try:
            self.assertEqual(first.stdout.readline().strip(),'locked')
            second=subprocess.run([sys.executable,'-c',code,str(ROOT/'scripts'),str(store)],capture_output=True,text=True,timeout=3)
            self.assertEqual(second.stdout.strip(),'busy')
            os.kill(first.pid,signal.SIGKILL);first.wait(timeout=3)
            lock=v.acquire_lock(store);self.assertIsNotNone(lock);lock.close()
        finally:
            if first.poll() is None:first.kill();first.wait()
            first.stdout.close()

    def test_cli_lock_exits_immediately_with_log(self):
        storage=self.root/'workspace/video-radar';storage.mkdir(parents=True)
        lock=v.acquire_lock(storage)
        try:
            proc=subprocess.run([sys.executable,str(ROOT/'scripts/video_radar.py'),'--root',str(self.root),'--mode','hourly'],capture_output=True,text=True,timeout=3)
            self.assertEqual(proc.returncode,0)
            self.assertIn('已有轮次持锁',proc.stdout)
        finally:lock.close()

    def test_atomic_write_sigkill_preserves_old_then_cleanup(self):
        store=self.root/'store';store.mkdir();page=store/'page.json';v.atomic_json(page,{'old':[1,2,3]})
        code="import sys,time,os;from pathlib import Path;sys.path.insert(0,sys.argv[1]);import video_radar as v;v.os.replace=lambda *a:(print('before_rename',flush=True),time.sleep(60));v.atomic_json(Path(sys.argv[2]),{'new':list(range(10000))})"
        proc=subprocess.Popen([sys.executable,'-c',code,str(ROOT/'scripts'),str(page)],stdout=subprocess.PIPE,text=True)
        try:
            self.assertEqual(proc.stdout.readline().strip(),'before_rename')
            os.kill(proc.pid,signal.SIGKILL);proc.wait(timeout=3)
            self.assertEqual(json.loads(page.read_text()),{'old':[1,2,3]})
            self.assertTrue(list(store.glob('.video-radar-*.tmp')))
            v.cleanup(store,AT,30)
            self.assertFalse(list(store.glob('.video-radar-*.tmp')))
        finally:
            if proc.poll() is None:proc.kill();proc.wait()
            proc.stdout.close()

    def test_gateway_rejection_and_no_network(self):
        for url in ['http://api.aisa.one'+v.BASE+'x','https://api.tikhub.io'+v.BASE+'x','https://api.aisa.one.evil'+v.BASE+'x','https://api.aisa.one@evil'+v.BASE+'x','https://api.aisa.one:443'+v.BASE+'x']:
            with self.subTest(url=url),mock.patch.object(v.subprocess,'run') as transport:
                with self.assertRaises(v.RadarError):v.curl_json(url,'GET',None,'fake-credential',1)
                transport.assert_not_called()

    def test_key_stdin_redaction_and_redirect_refusal(self):
        key='FAKEcredential1234567890secrettail'
        response={'code':200,'title':key,'nested':[key[:12],key[-8:]],'authorization':key}
        def fake(args,**kw):
            self.assertNotIn(key,' '.join(args));self.assertNotIn('--location',args);self.assertNotIn('-L',args)
            self.assertIn(key,kw['input'].decode());self.assertEqual(args[1],'-q')
            return subprocess.CompletedProcess(args,0,json.dumps(response).encode()+b'\n200')
        with mock.patch.object(v.subprocess,'run',side_effect=fake):
            safe=v.curl_json(v.ORIGIN+v.BASE+'probe','POST',{'keyword':'AI'},key,1)
        self.assertNotIn(key,json.dumps(safe));self.assertNotIn(key[:12],json.dumps(safe));self.assertNotIn(key[-8:],json.dumps(safe))
        with mock.patch.object(v.subprocess,'run',return_value=subprocess.CompletedProcess([],0,b'{}\n302')):
            with self.assertRaisesRegex(v.RadarError,'http_302'):v.curl_json(v.ORIGIN+v.BASE+'probe','GET',None,key,1)

    def test_request_failure_accounting_and_actual_time(self):
        calls=[];finished=[]
        with mock.patch.dict(os.environ,{'AISA_API_KEY':'fake-credential'}):
            client=v.Client(CONFIG,calls.append,lambda:finished.append(True),time.monotonic()+30)
            with mock.patch.object(v,'curl_json',side_effect=v.RadarError('http_429')):
                with self.assertRaises(v.RadarError):client.request('GET',v.BASE+'probe',{})
        self.assertEqual(len(calls),1);self.assertEqual(len(finished),1)
        self.assertEqual(calls[0]['status'],'failed');self.assertIn('finished_at',calls[0])

    def test_launchd_template(self):
        config=plistlib.loads((ROOT/'scripts/launchd/com.servasyy.video-radar.plist').read_bytes())
        self.assertFalse(config['RunAtLoad'])
        self.assertEqual(config['StartCalendarInterval'],[dict(Hour=21,Minute=20)])
        shell=(ROOT/'scripts/launchd/run-video-radar.sh').read_text()
        self.assertNotIn('FEISHU',shell);self.assertIn('EnvironmentVariables.AISA_API_KEY',shell)


class DeadlineTests(unittest.TestCase):
    def test_real_slow_drip_request_and_round_deadline(self):
        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self,*a):pass
            def do_GET(self):
                self.send_response(200);self.end_headers()
                try:
                    for _ in range(80):
                        self.wfile.write(b' ');self.wfile.flush();time.sleep(.1)
                except (BrokenPipeError,ConnectionResetError):pass
        Handler.do_POST=Handler.do_GET
        server=http.server.ThreadingHTTPServer(('127.0.0.1',0),Handler)
        server.daemon_threads=True
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        url='http://127.0.0.1:'+str(server.server_port)+'/drip'
        original_run=v.subprocess.run
        def local_curl(args,**kw):
            args=list(args);args[args.index('=https')]='=http'
            return original_run(args,**kw)
        try:
            with mock.patch.object(v,'validate_gateway'),mock.patch.object(v.subprocess,'run',side_effect=local_curl):
                start=time.monotonic()
                with self.assertRaises(v.RadarError):v.curl_json(url,'GET',None,'fixture-key',.6)
                request_elapsed=time.monotonic()-start
            self.assertLess(request_elapsed,1.2)
            self.assertGreater(request_elapsed,.45)
            with tempfile.TemporaryDirectory() as temp:
                cfg=copy.deepcopy(CONFIG);cfg['request_timeout_seconds']=.6;cfg['round_timeout_seconds']=1.5
                with mock.patch.object(v,'ORIGIN',f'http://127.0.0.1:{server.server_port}'),mock.patch.object(v,'validate_gateway'),mock.patch.object(v.subprocess,'run',side_effect=local_curl),mock.patch.dict(os.environ,{'AISA_API_KEY':'fixture-key'}),contextlib.redirect_stdout(io.StringIO()):
                    start=time.monotonic();page=v.run(Path(temp),'auto',config=cfg);elapsed=time.monotonic()-start
                self.assertLess(elapsed,2.0);self.assertGreater(elapsed,1.3)
                self.assertTrue(all(s['status']=='failed' for s in page['sources'].values()))
                self.assertEqual(page['today']['api_calls'],3)
        finally:
            server.shutdown();server.server_close();thread.join(timeout=2)


if __name__=='__main__':unittest.main()
