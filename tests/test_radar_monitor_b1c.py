"""A1-b adapter boundary probes, seeded by a real published B1-c run."""
import copy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT));sys.path.insert(0,str(ROOT/'scripts'))
from scripts import radar_monitor as code
from test_radar_monitor import when

LIVE = Path(os.environ.get('B1C_LIVE_FIXTURE', '/tmp/viral-b1c-deliver/evidence/live'))


class A1bAdapterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='b1c-probe-',dir='/tmp');self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root/'scripts').mkdir();(self.root/'public').mkdir()
        for name in ['radar_common.py','radar_monitor_config.json','radar_targets.json']:
            shutil.copy2(ROOT/'scripts'/name,self.root/'scripts'/name)
        self.bundle = json.loads((LIVE/'round-1-bundle.json').read_text())
        self.raw = json.loads((LIVE/'round-1-snapshot.json').read_text())
        self.started = code.stamp(self.raw['started_at'])
        self.save()

    def save(self):
        payload = json.dumps(self.raw,ensure_ascii=False).encode()
        self.path = self.root/'workspace/twitter-radar'/self.bundle['manifest']['snapshot_file']
        self.path.parent.mkdir(parents=True,exist_ok=True);self.path.write_bytes(payload)
        self.bundle['manifest']['snapshot_sha256'] = hashlib.sha256(payload).hexdigest()
        self.public = self.root/'public/twitter-radar-bundle.json'
        self.public.write_text(json.dumps(self.bundle,ensure_ascii=False))

    def read(self):
        return code.read_snapshot(self.root,started_at=self.started)

    def test_01_real_generated_bundle_raw_fields(self):
        result=self.read()
        self.assertEqual(result['run_id'],self.raw['run_id']);self.assertEqual(result['api_calls'],54)
        self.assertEqual(result['coverage'],1);self.assertEqual(result['ended_at'],code.iso(self.raw['finished_at']))
        byid={x['tweet_id']:x for x in self.raw['tweets']}
        for row in result['tweets']:
            raw=byid[row['tweet_id']]
            self.assertEqual(row['author'],raw['author_handle'].lower())
            self.assertEqual(row['user_id'],raw['author_user_id']);self.assertEqual(row['type'],raw['kind'])
            self.assertEqual(row['type_evidence'],raw['type_evidence'])
            self.assertFalse(row['is_stale'])

    def test_02_snapshot_sha_tamper(self):
        self.path.write_bytes(self.path.read_bytes()+b' ')
        with self.assertRaisesRegex(ValueError,'SHA'):self.read()

    def test_03_snapshot_missing(self):
        self.path.unlink()
        with self.assertRaises(ValueError):self.read()

    def test_04_embedded_manifest_missing_even_index_valid(self):
        (self.root/'workspace/twitter-radar/manifest.json').write_text(json.dumps(self.bundle['manifest']))
        self.bundle.pop('manifest');self.public.write_text(json.dumps(self.bundle))
        with self.assertRaises(ValueError):self.read()

    def test_05_newer_directory_snapshot_and_wrong_index_ignored(self):
        decoy=copy.deepcopy(self.raw);decoy['run_id']='newer-decoy';decoy['finished_at']='2099-01-01T00:00:00Z'
        (self.path.parent/'zz-newer.json').write_text(json.dumps(decoy))
        (self.root/'workspace/twitter-radar/manifest.json').write_text(json.dumps({'run_id':'wrong-index'}))
        self.assertEqual(self.read()['run_id'],self.raw['run_id'])

    def test_06_run_id_and_manifest_start_mismatch(self):
        for key,value in [('run_id','wrong-run'),('started_at','2000-01-01T00:00:00Z')]:
            with self.subTest(key=key):
                old=self.bundle['manifest'][key];self.bundle['manifest'][key]=value;self.save()
                with self.assertRaises(ValueError):self.read()
                self.bundle['manifest'][key]=old

    def test_07_path_escape_and_symlink(self):
        old=self.bundle['manifest']['snapshot_file']
        for relative in ['/tmp/outside.json','../../outside.json']:
            self.bundle['manifest']['snapshot_file']=relative;self.public.write_text(json.dumps(self.bundle))
            with self.assertRaises(ValueError):self.read()
        self.bundle['manifest']['snapshot_file']=old;self.save()
        other=self.root/'other.json';self.path.rename(other);self.path.symlink_to(other)
        with self.assertRaises(ValueError):self.read()

    def test_08_mixed_empty_partial_failure_not_started(self):
        handles=list(self.raw['accounts']);empty,partial,failed,not_started=handles[:4]
        self.raw['accounts'][empty].update(status='empty',ok=True)
        self.raw['accounts'][partial].update(status='partial',ok=True,pages_complete=False)
        self.raw['accounts'][failed].update(status='timeout',ok=False)
        calls=self.raw['accounts'][not_started]['calls']
        self.raw['accounts'][not_started].update(status='not_started',ok=False,calls=0)
        self.raw.update(success=49,failed=2,empty=1,partial=1,coverage=round(49/51,4),attempted=50,api_calls=self.raw['api_calls']-calls)
        self.save();result=self.read();self.assertAlmostEqual(result['coverage'],49/51)
        self.assertEqual(result['api_calls'],self.raw['api_calls']);self.assertEqual(set(result['failed_handles']),{failed,not_started})
        rows=[t for t in result['tweets'] if t['author']==partial];self.assertTrue(rows);self.assertTrue(all(not t['is_stale'] for t in rows))
        self.assertTrue(all(t['is_stale'] for t in result['tweets'] if t['author']==failed))

    def test_09_every_run_count_checked(self):
        for key in ['target_count','attempted','success','failed','api_calls','partial','empty','coverage']:
            with self.subTest(key=key):
                old=self.raw[key];self.raw[key]=old+1;self.save()
                with self.assertRaises(ValueError):self.read()
                self.raw[key]=old
        self.save()

    def test_10_account_ok_calls_and_duplicates_checked(self):
        account=next(iter(self.raw['accounts'].values()))
        for key,value in [('ok',False),('calls',True),('calls',-1),('calls',0),('status','not_started')]:
            with self.subTest(key=key,value=value):
                old=account[key];account[key]=value;self.save()
                with self.assertRaises(ValueError):self.read()
                account[key]=old
        old_handle=account['handle'];account['handle']='wrong_identity';self.save()
        with self.assertRaises(ValueError):self.read()
        account['handle']=old_handle
        original=self.raw['accounts'];self.raw['accounts']=list(original.values())+[copy.deepcopy(account)];self.save()
        with self.assertRaises(ValueError):self.read()

    def test_11_author_never_replaced_by_queried_handle(self):
        row=self.raw['tweets'][0];self.raw['tweets']=[row]
        for author in ['external_other',next(h for h in self.raw['accounts'] if h!=row['queried_handle'])]:
            for kind in ['quote','retweet','original']:
                row.update(author_handle=author,author_handle_normalized=author,kind=kind)
                self.save();self.assertEqual(self.read()['tweets'],[])
        row['author_handle']=row['queried_handle'];row['author_handle_observed']=False;self.save()
        self.assertEqual(self.read()['tweets'],[])
        row['author_handle_observed']=True
        row['author_handle']=None;self.save()
        with self.assertRaises(ValueError):self.read()

    def test_12_shared_content_filter_and_snapshot_kind(self):
        from radar_common import content_filter
        accepts=code.radar_filter(self.root)
        for text in ['Agent MCP coding','weekend walking and tea','普通中文作者内容','AI 抽奖 送福利']:
            with self.subTest(text=text):
                self.assertEqual(accepts({'type':'quote','text':text}),content_filter(text,is_target_vip=True)[0])
        self.assertTrue(accepts({'type':'original','text':'RT @x: Agent'}))
        for kind in ['retweet','reply']:
            self.assertFalse(accepts({'type':kind,'text':'Agent MCP'}))
        (self.root/'scripts/build_fresh_radar_bundle.py').write_text('raise RuntimeError("retired must not load")')
        self.assertTrue(code.radar_filter(self.root)({'type':'original','text':'Agent'}))

    def seed_monitor(self):
        monitor=code.Monitor(self.root,collector=lambda *args:self.read(),sender=lambda es:[],clue_writer=lambda e:{'id':'probe'})
        self.assertEqual(monitor.run_once(self.started)['status'],'baseline')
        return monitor

    def test_13_corrupt_authority_fails_preserves_page_and_baseline(self):
        for mode in ['sha','missing','manifest','counts']:
            with self.subTest(mode=mode):
                self.save();monitor=self.seed_monitor() if not hasattr(self,'monitor') else self.monitor
                self.monitor=monitor
                before=(monitor.directory/'page-hourly.json').read_bytes();old=monitor.load()[0]['samples']
                if mode=='sha':self.path.write_bytes(self.path.read_bytes()+b' ')
                elif mode=='missing':self.path.unlink()
                elif mode=='manifest':self.public.write_text('{}')
                else:self.raw['api_calls']+=1;self.save()
                hour=self.started+timedelta(hours=['sha','missing','manifest','counts'].index(mode)+1)
                monitor.collector=lambda *args:code.read_snapshot(self.root)
                self.assertEqual(monitor.run_once(hour)['status'],'failed')
                self.assertEqual((monitor.directory/'page-hourly.json').read_bytes(),before)
                self.assertEqual(monitor.load()[0]['samples'],old)
                if mode=='counts':self.raw['api_calls']-=1

    def test_14_real_a1_low_coverage_does_not_publish_old_bundle(self):
        monitor=self.seed_monitor();before=self.public.read_bytes();page=(monitor.directory/'page-hourly.json').read_bytes();samples=monitor.load()[0]['samples']
        wrapper=f'''import sys
from pathlib import Path
sys.path.insert(0,{str(ROOT/'scripts')!r})
import refresh_twitter_radar as a1
from radar_collect import collect
from datetime import datetime, timezone
# Real collect and real refresh publication policy, only remote fetch is offline.
def fetch(handle,key,**kw):
 return dict(handle=handle,status='timeout',tweets=[],calls=1,pages=0,pages_complete=False)
a1.collect=lambda **kw: collect(**kw,fetch=fetch,log=lambda *a:None)
raise SystemExit(a1.main(['--repo-root',{str(self.root)!r},'--targets',{str(self.root/'scripts/radar_targets.json')!r},'--summary-json',sys.argv[2]]))
'''
        (self.root/'scripts/refresh_twitter_radar.py').write_text(wrapper)
        with unittest.mock.patch.dict(os.environ,{'AISA_API_KEY':'offline-fixture'}):
            failed=code.collect_snapshot(self.root,5,(),self.started)
        self.assertTrue(failed['collector_failed']);self.assertEqual(failed['api_calls'],51);self.assertEqual(failed['coverage'],0)
        self.assertEqual(self.public.read_bytes(),before)
        monitor.collector=lambda *args:failed
        self.assertEqual(monitor.run_once(self.started+timedelta(hours=1))['status'],'failed')
        self.assertEqual((monitor.directory/'page-hourly.json').read_bytes(),page);self.assertEqual(monitor.load()[0]['samples'],samples)

    def test_15_zero_exit_old_bundle_no_summary_rejected(self):
        (self.root/'scripts/refresh_twitter_radar.py').write_text('raise SystemExit(0)\n')
        with self.assertRaisesRegex(ValueError,'未提交'):code.collect_snapshot(self.root,2,(),self.started)

    def test_16_zero_exit_summary_unpublished_not_success(self):
        summary={'run_id':'no-publish-run','started_at':self.raw['started_at'],'api_calls':54,'coverage':1,'published':False}
        (self.root/'scripts/refresh_twitter_radar.py').write_text('import json,sys\nfrom pathlib import Path\nPath(sys.argv[2]).write_text('+repr(json.dumps(summary))+')\n')
        result=code.collect_snapshot(self.root,2,(),self.started)
        self.assertTrue(result['collector_failed']);self.assertEqual(result['tweets'],[])

    def test_17_real_timeout_kills_child_group(self):
        marker=self.root/'late-child'
        child='import time;from pathlib import Path;time.sleep(.7);Path('+repr(str(marker))+').touch()'
        script='import subprocess,sys,time\nsubprocess.Popen([sys.executable,"-c",'+repr(child)+'])\ntime.sleep(30)\n'
        (self.root/'scripts/refresh_twitter_radar.py').write_text(script)
        tick=time.monotonic()
        with self.assertRaisesRegex(ValueError,'超时'):code.collect_snapshot(self.root,.15,(),self.started)
        self.assertLess(time.monotonic()-tick,3);time.sleep(.8);self.assertFalse(marker.exists())

    def test_18_excluded_old_and_future_finished_cannot_refresh_old_start(self):
        with self.assertRaises(ValueError):code.read_snapshot(self.root,{self.raw['run_id']})
        self.raw['finished_at']='2099-01-01T00:00:00Z';self.save()
        with self.assertRaises(ValueError):code.read_snapshot(self.root,started_at=self.started+timedelta(seconds=1))


if __name__=='__main__':unittest.main(verbosity=2)
