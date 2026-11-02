from datetime import datetime
import fcntl
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from source_catchup import CatchupLedger, grok_output, native_observations, report_time, sleep_snapshot
from external_tasks import ExternalTasks, TASKS
import grok_bot_radar as grok


def ts(value):
    return datetime.fromisoformat(value+'+08:00').timestamp()


class CatchupTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir='/tmp',prefix='grok-catchup-test-');self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.ws=self.root/'workspace';self.ws.mkdir();self.calls=[]
        self.adapter=lambda *args:self.calls.append(args) or True
        self.ledger=CatchupLedger(self.ws,{'grok-interest':self.adapter})
        self.due=ts('2026-10-31T10:30:00');self.now=self.due+3601
        self.sleep={'slept_at':self.due-1800,'woke_at':self.due+600}

    def obs(self,**extra):
        return {'source_id':'grok-interest','due':self.due,'grace_seconds':3600,'output_at':None,**extra}

    def row(self,**extra):
        return self.ledger.refresh([self.obs(**extra)],self.now,self.sleep)['sources'][0]

    def test_same_slot_repeated_poll_and_restart_only_one_physical_trigger(self):
        self.assertEqual(self.row()['status'],'running')
        self.row();self.ledger=CatchupLedger(self.ws,{'grok-interest':self.adapter});self.row()
        self.assertEqual(len(self.calls),1)
        self.assertEqual(self.row()['attempts'],1)

    def test_not_before_due_or_in_grace(self):
        self.now=self.due-1;self.assertEqual(self.ledger.refresh([self.obs()],self.now,self.sleep)['sources'],[])
        self.now=self.due+3600;self.assertEqual(self.row()['status'],'waiting');self.assertEqual(self.calls,[])
        self.now+=1;self.assertEqual(self.row()['status'],'running');self.assertEqual(len(self.calls),1)

    def test_fake_sleep_across_morning_evening_midnight(self):
        for day,clock,wake in [('2026-10-31','10:30:00','2026-10-31T11:45:00'),('2026-10-31','22:30:00','2026-10-31T23:45:00'),('2026-11-01','22:30:00','2026-11-02T00:15:00')]:
            with self.subTest(day=day,clock=clock):
                self.due=ts(day+'T'+clock);self.now=ts(wake);self.sleep={'slept_at':self.due-1200,'woke_at':self.now-60}
                self.assertEqual(self.row()['status'],'running');self.row()
        self.assertEqual(len(self.calls),3)

    def test_normal_output_and_late_original_output_never_trigger(self):
        self.assertEqual(self.row(output_at=self.due+600)['status'],'idle')
        self.assertEqual(self.calls,[])
        self.row(output_at=self.now);self.assertEqual(self.calls,[])

    def test_absent_authorized_adapter_explicit_unavailable_zero_attempts(self):
        self.ledger=CatchupLedger(self.ws)
        row=self.row();self.assertEqual(row['status'],'unavailable');self.assertEqual(row['reason'],'no_authorized_trigger');self.assertEqual(row['attempts'],0)
        self.assertIn('没有授权',row['detail']);self.assertEqual(self.calls,[])

    def test_callback_success_does_not_fake_file_success(self):
        self.assertEqual(self.row()['status'],'running')
        self.assertEqual(self.row(output_at=self.now)['status'],'completed')
        self.assertEqual(len(self.calls),1)

    def test_no_sleep_or_downtime_evidence_does_not_retry_real_fault(self):
        self.sleep=None;row=self.row();self.assertEqual(row['reason'],'no_sleep_evidence');self.assertEqual(self.calls,[])

    def test_offline_gap_can_dispatch_once_without_claiming_sleep(self):
        self.ledger.refresh([],self.due-100)
        self.sleep=None;row=self.row();self.assertEqual(row['cause'],'offline');self.assertEqual(row['status'],'running')

    def test_native_sources_observed_not_dispatched_and_regular_failure_is_fault(self):
        normal=dict(source_id='wechat-radar',due=self.due,grace_seconds=60,owner_managed=True,run_status='running',started_at=self.due+1)
        row=self.ledger.refresh([normal],self.now,self.sleep)['sources'][0];self.assertEqual(row['status'],'idle')
        normal['run_status']='failed';self.assertEqual(self.ledger.refresh([normal],self.now,self.sleep)['sources'][0]['status'],'fault')
        normal.update(started_at=self.due+100,run_status='running');self.assertEqual(self.ledger.refresh([normal],self.now,self.sleep)['sources'][0]['status'],'running')
        normal.update(output_at=self.now,run_status='partial');self.assertEqual(self.ledger.refresh([normal],self.now,self.sleep)['sources'][0]['status'],'completed');self.assertEqual(self.calls,[])
        with self.assertRaises(ValueError):CatchupLedger(self.ws,{'wechat-radar':self.adapter})

    def test_disabled_owner_not_overridden(self):
        row=self.row(enabled=False);self.assertEqual(row['reason'],'owner_disabled');self.assertEqual(self.calls,[])

    def test_crash_after_durable_claim_never_resends(self):
        def crash(*args):raise KeyboardInterrupt()
        self.ledger=CatchupLedger(self.ws,{'grok-interest':crash})
        with self.assertRaises(KeyboardInterrupt):self.row()
        self.ledger=CatchupLedger(self.ws,{'grok-interest':self.adapter});self.assertEqual(self.row()['status'],'unknown');self.assertEqual(self.calls,[])

    def test_error_message_secret_never_persisted_or_returned(self):
        def bad(*args):raise RuntimeError('fake-private-secret-token-and-url')
        self.ledger=CatchupLedger(self.ws,{'grok-interest':bad});row=self.row();self.assertEqual(row['status'],'failed')
        all_text=json.dumps(row)+(self.ws/'catchup/state.json').read_text();self.assertNotIn('fake-private',all_text)

    def test_corrupt_state_and_failed_persistence_prevent_trigger(self):
        (self.ws/'catchup').mkdir();(self.ws/'catchup/state.json').write_text('invalid')
        self.assertEqual(self.ledger.refresh([self.obs()],self.now,self.sleep)['status'],'unavailable');self.assertEqual(self.calls,[])
        (self.ws/'catchup/state.json').unlink()
        with patch.object(self.ledger,'_save',side_effect=OSError('private')):
            self.assertEqual(self.ledger.refresh([self.obs()],self.now,self.sleep)['status'],'unavailable')
        self.assertEqual(self.calls,[])

    def test_nonblocking_lock_no_second_trigger(self):
        self.row();lock=(self.ws/'catchup/.lock').open('r+')
        self.addCleanup(lock.close);fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        result=self.ledger.refresh([self.obs()],self.now,self.sleep);self.assertEqual(result['status'],'busy');self.assertEqual(len(self.calls),1)

    def test_symlink_workspace_and_state_refused_permissions_private(self):
        alias=self.root/'alias';alias.symlink_to(self.ws,target_is_directory=True)
        self.assertEqual(CatchupLedger(alias).refresh([self.obs()],self.now,self.sleep)['status'],'unavailable')
        self.row();self.assertEqual((self.ws/'catchup/state.json').stat().st_mode&0o777,0o600)
        (self.ws/'catchup/state.json').unlink();outside=self.root/'outside';outside.write_text('untouched');(self.ws/'catchup/state.json').symlink_to(outside)
        self.assertEqual(self.ledger.refresh([self.obs()],self.now,self.sleep)['status'],'unavailable');self.assertEqual(outside.read_text(),'untouched')

    def test_clock_rollback_does_not_create_new_attempt(self):
        self.row();self.now-=1;row=self.row();self.assertEqual(row['reason'],'clock_reversed');self.assertEqual(len(self.calls),1)

    def test_declared_items_timestamp_and_auxiliary_files(self):
        folder=self.root/'reports/interest';folder.mkdir(parents=True)
        p=folder/'2026-10-31-2045.json';p.write_text(json.dumps({'generated_at':'2026-10-31T20:45:00+08:00','items':[]}));os.utime(p,(ts('2026-10-31T20:46:00'),)*2)
        task=next(x for x in TASKS if x['id']=='grok-interest')
        out=grok_output(folder.parent,task,self.due,ts('2026-10-31T22:30:00'),ts('2026-10-31T21:00:00'))
        self.assertEqual(out,ts('2026-10-31T20:45:00'))
        self.assertIsNone(grok_output(folder.parent,task,ts('2026-10-31T22:30:00'),ts('2026-11-01T10:30:00'),ts('2026-10-31T23:50:00')))
        p.write_text(json.dumps({'generated_at':'2026-10-31T20:45:00+08:00','other':[]}));self.assertIsNone(report_time(p,ts('2026-10-31T21:00:00')))
        alerts=self.root/'reports/alerts';alerts.mkdir()
        for i,data in enumerate([[],{},'private']):
            (alerts/f'_post_results_2026-10-31-{1200+i}.json').write_text(json.dumps(data))
        (alerts/'2026-10-31-1400.json').write_text('{"posts":"bad"}')
        result=grok.load_reports(folder.parent);self.assertEqual(result['ignored_files'],3);self.assertEqual(result['bad_files'],1)

    def test_sysctl_reader_fixed_keys_and_sanitized_failure(self):
        class Reply:stdout='{ sec = 123, usec = 1 } text'
        class Wake:stdout='{ sec = 234, usec = 1 } text'
        with patch('source_catchup.subprocess.run',side_effect=[Reply(),Wake()]) as call:
            self.assertEqual(sleep_snapshot(),{'slept_at':123,'woke_at':234});self.assertEqual(call.call_args_list[0].args[0],['/usr/sbin/sysctl','-n','kern.sleeptime'])
        with patch('source_catchup.subprocess.run',side_effect=OSError('private')):self.assertIsNone(sleep_snapshot())

    def test_two_processes_share_slot_claim(self):
        import subprocess,sys
        stage=Path(__file__).resolve().parents[1];barrier=self.root/'go';count=self.root/'count'
        code='''import sys,time,os
from pathlib import Path
sys.path.insert(0,sys.argv[1]);from source_catchup import CatchupLedger
ws=Path(sys.argv[2]);barrier=Path(sys.argv[3]);count=Path(sys.argv[4]);due=float(sys.argv[5])
def adapter(*args):
 fd=os.open(count,os.O_WRONLY|os.O_CREAT|os.O_APPEND,0o600);os.write(fd,b'1\\n');os.close(fd);time.sleep(.1);return True
end=time.monotonic()+5
while not barrier.exists() and time.monotonic()<end:time.sleep(.01)
CatchupLedger(ws,{'grok-interest':adapter}).refresh([{'source_id':'grok-interest','due':due,'grace_seconds':3600}],due+4000,{'slept_at':due-10,'woke_at':due+100})
'''
        children=[]
        try:
            for _ in range(2):children.append(subprocess.Popen([sys.executable,'-c',code,str(stage),str(self.ws),str(barrier),str(count),str(self.due)],stdout=subprocess.DEVNULL,stderr=subprocess.PIPE))
            barrier.touch()
            for p in children:self.assertEqual(p.wait(timeout=10),0)
        finally:
            for p in children:
                if p.poll() is None:p.terminate();p.wait(timeout=5)
                if p.stderr:p.stderr.close()
        self.assertEqual(count.read_text().splitlines(),['1'])

    def test_native_db_observer_readonly_runs_match_source_and_keep_error_private(self):
        import hashlib
        folder=self.ws/'schedules';folder.mkdir();path=folder/'tasks.db'
        with sqlite3.connect(path) as db:
            db.executescript('CREATE TABLE tasks(id TEXT,data TEXT,next_run REAL);CREATE TABLE runs(id TEXT,task_id TEXT,status TEXT,trigger TEXT,started_at TEXT,finished_at TEXT,output TEXT);')
            task={'kind':'wechat_radar','frequency':'interval','minutes':60,'enabled':True,'body':'fake-private-prompt'}
            db.execute('insert into tasks values(?,?,?)',('r',json.dumps(task),self.now+3600))
            for id,started,output in [('prior',self.due-3600,{}),('new',self.due+100,{'time_budget':True,'processed':3})]:
                db.execute('insert into runs values(?,?,?,?,?,?,?)',(id,'r','partial','scheduled',datetime.fromtimestamp(started).astimezone().isoformat(),datetime.fromtimestamp(started+10).astimezone().isoformat(),json.dumps(output)))
        cold,following=native_observations(self.ws,self.now);self.assertEqual(cold,[])  # No guessed interval due on cold boot.
        before=hashlib.sha256(path.read_bytes()).hexdigest();rows,following=native_observations(self.ws,self.now,{'r':self.due})
        self.assertEqual(rows[0]['run_id'],'new');self.assertEqual(rows[0]['run_status'],'partial');self.assertIsNotNone(rows[0]['output_at']);self.assertEqual(following,{'r':self.now+3600})
        self.assertNotIn('fake-private',json.dumps(rows));self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),before)
        with sqlite3.connect(path) as db:db.execute("update runs set output=? where id='new'",(json.dumps({'error_kind':'model_failed','private':'fake-private-detail'}),))
        rows,_=native_observations(self.ws,self.now,{'r':self.due});self.assertEqual(rows[0]['run_status'],'failed');self.assertIsNone(rows[0]['output_at']);self.assertNotIn('fake-private',json.dumps(rows))

    def test_cached_api_fields_and_real_reports_untouched(self):
        from types import SimpleNamespace
        from urllib.parse import urlsplit
        from schedules_assistant_api import get_route
        from scheduled_tasks import ScheduledTasks
        reports=self.root/'reports';(reports/'interest').mkdir(parents=True)
        topics=reports/'interest-topics.md';topics.write_text('not changed')
        cache=ExternalTasks(self.ws,reports,clock=lambda:self.now,service_reader=lambda:{},sleep_reader=lambda:self.sleep)
        cache.refresh(force=True)
        assistant=SimpleNamespace(settings=lambda:{'profiles':[],'active_profile_id':None})
        scheduler=ScheduledTasks(self.ws,assistant,autostart=False);self.addCleanup(scheduler.close);scheduler.external_tasks=cache
        handler=SimpleNamespace(server=SimpleNamespace(store=None,schedules=scheduler),reply=lambda code,body:body)
        with patch.object(cache,'refresh',side_effect=AssertionError('GET must not dispatch or scan')):
            result=get_route(handler,urlsplit('/api/schedules'))
        row=next(x for x in result['external']['tasks'] if x['id']=='grok-interest')
        self.assertEqual(row['catchup']['status'],'unavailable');self.assertFalse(result['external']['catchup']['action_endpoint']);self.assertEqual(topics.read_text(),'not changed')
        self.assertEqual(scheduler.runs()['runs'],[])

    def test_report_future_symlink_nested_timestamp_not_success(self):
        p=self.root/'report.json';p.write_text(json.dumps({'items':[],'nested':{'generated_at':'2026-10-31T10:40:00+08:00'}}));self.assertIsNone(report_time(p,self.now))
        p.write_text(json.dumps({'items':[],'generated_at':'2099-01-01T00:00:00+08:00'}));self.assertIsNone(report_time(p,self.now))
        alias=self.root/'alias.json';alias.symlink_to(p);self.assertIsNone(report_time(alias,self.now))

    def test_real_grok_legacy_shanghai_time_and_wrong_filename(self):
        p=self.root/'reports/interest/2026-10-31-1205.json';p.parent.mkdir(parents=True)
        p.write_text(json.dumps({'items':[],'collected_at':'2026-10-31 12:05 Asia/Shanghai'}))
        now=ts('2026-10-31T13:00:00');os.utime(p,(now-10,)*2)
        task=next(x for x in TASKS if x['id']=='grok-interest')
        self.assertEqual(grok_output(p.parent.parent,task,self.due,ts('2026-10-31T22:30:00'),now),ts('2026-10-31T12:05:00'))
        p.rename(p.with_name('2026-10-29-1205.json'))
        self.assertIsNone(grok_output(p.parent.parent,task,self.due,ts('2026-10-31T22:30:00'),now))

    def test_later_sleep_evidence_upgrades_unknown_cause(self):
        self.ledger=CatchupLedger(self.ws)
        self.sleep=None;self.now=self.due+1;self.assertEqual(self.row()['cause'],'missing')
        self.sleep={'slept_at':self.due-10,'woke_at':self.due+10};self.now=self.due+3601
        self.assertEqual(self.row()['cause'],'sleep')

    def test_readonly_snapshot_filters_private_unknown_fields_and_rejects_bad_enum(self):
        from source_catchup import status_snapshot
        before=status_snapshot(self.ws);self.assertEqual(before['sources'],[]);self.assertFalse((self.ws/'catchup').exists())
        self.row();p=self.ws/'catchup/state.json';state=json.loads(p.read_text());key=next(iter(state['slots']))
        state['slots'][key]['private']='fake-private-key-value';p.write_text(json.dumps(state))
        self.assertNotIn('fake-private',json.dumps(status_snapshot(self.ws)))
        state['slots'][key]['reason']='fake-private-key-value';p.write_text(json.dumps(state))
        out=status_snapshot(self.ws);self.assertEqual(out['status'],'unavailable');self.assertNotIn('fake-private',json.dumps(out))

    def test_wake_trigger_file_to_cached_api_chain_with_authorized_fake_adapter(self):
        reports=self.root/'reports';(reports/'interest').mkdir(parents=True)
        cache=ExternalTasks(self.ws,reports,clock=lambda:self.now,service_reader=lambda:{},sleep_reader=lambda:self.sleep)
        count=[]
        def produce(source,slot):
            count.append(slot);p=reports/'interest/2026-10-31-1145.json'
            p.write_text(json.dumps({'generated_at':'2026-10-31T11:45:00+08:00','items':[]}));os.utime(p,(self.now,self.now));return True
        cache.catchup.adapters={'grok-interest':produce}
        self.now=ts('2026-10-31T10:00:00');cache.refresh(force=True)
        self.now=ts('2026-10-31T11:45:00');cache.refresh(force=True)
        row=lambda:next(t['catchup'] for t in cache.snapshot()['tasks'] if t['id']=='grok-interest')
        self.assertEqual(row()['status'],'running');self.assertEqual(len(count),1)
        cache.refresh(force=True);self.assertEqual(row()['status'],'completed')
        restarted=ExternalTasks(self.ws,reports,clock=lambda:self.now,service_reader=lambda:{},sleep_reader=lambda:self.sleep)
        restarted.catchup.adapters={'grok-interest':produce};restarted.refresh(force=True)
        self.assertEqual(next(t['catchup'] for t in restarted.snapshot()['tasks'] if t['id']=='grok-interest')['status'],'completed');self.assertEqual(len(count),1)

    def test_bad_output_is_fault_not_missing_and_never_retriggered(self):
        reports=self.root/'reports';p=reports/'interest/2026-10-31-1131.json';p.parent.mkdir(parents=True);p.write_text('bad')
        os.utime(p,(self.now,self.now));task=next(t for t in TASKS if t['id']=='grok-interest');meta={}
        self.assertIsNone(grok_output(reports,task,self.due,ts('2026-10-31T22:30:00'),self.now,meta));self.assertEqual(meta['input_error'],'report_invalid')
        row=self.row(input_error=meta['input_error']);self.assertEqual(row['status'],'fault');self.assertEqual(self.calls,[])
