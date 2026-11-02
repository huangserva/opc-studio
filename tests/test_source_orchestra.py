from datetime import datetime
import json
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from scheduled_tasks import ScheduledTasks,ModelBusy,next_due
from source_orchestra import presets,preset_ids


class SourceSchedulesTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        config={'model':'fixture','endpoint':'http://127.0.0.1:1/v1/chat/completions','no_api_key':True}
        self.assistant=SimpleNamespace(model_document=lambda:{'profiles':[{'id':'fixture','config':config}]},
            settings=lambda:{'active_profile_id':'fixture','profiles':[{'profile_id':'fixture','configured':True,'display_name':'合成模型'}]})
        self.scheduler=ScheduledTasks(self.root,self.assistant,autostart=False);self.addCleanup(self.scheduler.close)

    def payload(self,**changes):
        return dict(name='合成定时器',kind='source_events',source='wechat',events=[3],window=None,
            body='按所选事件处理本机资料。',cwd=str(self.root),frequency='interval',minutes=60,at='08:00',
            weekdays=list(range(7)),ends_at=None,timeout=300,enabled=False,profile_id='fixture',allow_commands=False,
            result_destination='runs',**changes)

    def legacy(self):
        ids=[]
        for kind,enabled in [('wechat_clues',True),('wechat_radar',False),('script',True)]:
            p=self.payload();p.update(kind=kind,enabled=enabled)
            if kind=='wechat_clues':p.update(wechat_mode='incremental')
            elif kind=='wechat_radar':p.update(radar_since='2026-10-01T00:00:00+08:00')
            else:p.update(body='python /tmp/wechat_tools.py --workspace /tmp',profile_id=None)
            ids.append(self.scheduler.save(p)['id'])
        return ids

    def enabled(self):return {t['id']:t['enabled'] for t in self.scheduler.listing()['tasks']}

    def test_validation_is_server_side_and_preserves_closed_execution_scope(self):
        task=self.scheduler.validate(self.payload());self.assertEqual(task['events'],[1,2,3])
        for updates in ({'events':[]},{'events':[3,3]},{'events':[11]},{'events':[999]},{'source':'mail'},
                        {'window':{'start':'08:00','end':'08:00'}},{'allow_commands':True},{'result_destination':'clues'},
                        {'name':'x'*101},{'assistant_id':'workbench'},{'tool_groups':['workspace']}):
            with self.subTest(updates=updates),self.assertRaises(ValueError):self.scheduler.save({**self.payload(),**updates})
        for minutes in (1,1440):self.assertEqual(self.scheduler.validate({**self.payload(),'minutes':minutes})['minutes'],minutes)

    def test_registered_nonloopback_wechat_profile_is_reused_but_other_routes_are_rejected(self):
        remote={'model':'fixture','endpoint':'https://model.example.invalid/v1/chat/completions','no_api_key':True}
        with patch.object(self.scheduler,'model_config',return_value=remote):
            with self.assertRaises(ValueError):self.scheduler.save(self.payload())
            self.legacy()
            saved=self.scheduler.save(self.payload());self.assertTrue(saved['id'])
            with self.assertRaises(ValueError):self.scheduler.save({**self.payload(),'profile_id':'other','_wechat_registered_route':True})
        with self.scheduler.db() as db:
            stored=json.loads(db.execute('SELECT data FROM tasks WHERE id=?',(saved['id'],)).fetchone()[0])
        self.assertNotIn('_wechat_registered_route',stored)

    def test_install_is_disabled_idempotent_and_switch_back_restores_exact_states(self):
        self.legacy();before=self.enabled()
        result=presets(self.scheduler,{'action':'install','profile_id':'fixture'})
        self.assertFalse(result['enabled']);self.assertEqual(len(self.enabled()),7)
        installed=self.enabled();self.assertTrue(all(not installed[i] for i in preset_ids(self.scheduler)))
        presets(self.scheduler,{'action':'install','profile_id':'fixture'});self.assertEqual(self.enabled(),installed)
        presets(self.scheduler,{'action':'new'})
        self.assertTrue(all(self.enabled()[i] for i in preset_ids(self.scheduler)))
        self.assertTrue(all(not self.enabled()[i] for i in before))
        presets(self.scheduler,{'action':'new'});presets(self.scheduler,{'action':'old'})
        self.assertEqual(self.enabled(),installed)

    def test_switch_midway_failure_rolls_back_all_rows_and_saved_state(self):
        self.legacy();presets(self.scheduler,{'action':'install','profile_id':'fixture'});before=self.enabled()
        from source_orchestra import _write
        count=0
        def fail(*args):
            nonlocal count
            count+=1;_write(*args)
            if count==2:raise RuntimeError('合成故障')
        with patch('source_orchestra._write',side_effect=fail),self.assertRaises(RuntimeError):presets(self.scheduler,{'action':'new'})
        self.assertEqual(self.enabled(),before)
        presets(self.scheduler,{'action':'new'});presets(self.scheduler,{'action':'old'})
        self.assertEqual(self.enabled(),before)

    def test_new_and_old_tasks_share_execution_gate(self):
        old=self.legacy()[0];new=self.scheduler.save(self.payload())['id']
        self.scheduler.claim(new,'manual',launch=False)
        with self.assertRaises(ModelBusy):self.scheduler.claim(old,'manual',launch=False)

    def test_pending_source_stop_keeps_shared_gate_until_confirmed_final(self):
        old=self.legacy()[0];new=self.scheduler.save(self.payload())['id']
        rid=self.scheduler.claim(new,'manual',launch=False)['id']
        with self.scheduler.db() as db:db.execute("UPDATE runs SET status='timeout',output=? WHERE id=?",(json.dumps({'receipt_pending':True,'events':[]}),rid))
        with self.assertRaises(ModelBusy):self.scheduler.claim(old,'manual',launch=False)
        with self.scheduler.db() as db:db.execute('UPDATE runs SET output=? WHERE id=?',(json.dumps({'receipt_pending':False,'events':[]}),rid))
        self.assertEqual(self.scheduler.claim(old,'manual',launch=False)['status'],'running')

    def test_restart_reattaches_same_parent_run_and_keeps_original_deadline(self):
        identity=self.scheduler.save(self.payload())['id'];rid=self.scheduler.claim(identity,'manual',launch=False)['id']
        self.scheduler.close();self.scheduler=ScheduledTasks(self.root,self.assistant,autostart=False)
        self.addCleanup(self.scheduler.close)
        self.assertEqual(self.scheduler.get_run(rid)['status'],'running')
        calls=[]
        def finish(run_id,task,cancel,config,deadline):
            calls.append((run_id,task['id'],task['execution_deadline_at']))
            return 'succeeded',0,json.dumps({'source':'wechat','events':[{'event':3,'status':'succeeded'}]})
        self.scheduler.source_analyzer=finish;self.scheduler.recover_sources();self.scheduler.recover_sources()
        until=time.monotonic()+3
        while self.scheduler.get_run(rid)['status']=='running':
            if time.monotonic()>until:self.fail('recovery did not finish')
            time.sleep(.01)
        self.assertEqual(len(calls),1);self.assertEqual(calls[0][:2],(rid,identity))
        self.assertLessEqual(calls[0][2],time.time()+300)

    def test_coverage_only_counts_enabled_timers_and_receipt_cannot_be_published(self):
        identity=self.scheduler.save(self.payload())['id']
        before=self.scheduler.listing()['event_coverage'];self.assertTrue(any(e['id']==3 for e in before['missing']))
        self.scheduler.control(identity,'resume')
        after=self.scheduler.listing()['event_coverage'];self.assertFalse(any(e['id']==3 for e in after['missing']))
        rid=self.scheduler.claim(identity,'manual',launch=False)['id']
        with self.assertRaises(ValueError):self.scheduler.deliver_result(rid)

    def test_snapshot_executes_only_stored_events_and_survives_pause(self):
        identity=self.scheduler.save(self.payload())['id'];calls=[]
        def run(rid,task,cancel,config,deadline):
            calls.append(task['events']);return 'succeeded',0,json.dumps({'source':'wechat','events':[{'event':n,'status':'succeeded'} for n in task['events']]})
        self.scheduler.source_analyzer=run
        rid=self.scheduler.control(identity,'run')['id'];self.scheduler.control(identity,'pause')
        until=time.monotonic()+3
        while self.scheduler.get_run(rid)['status']=='running':
            if time.monotonic()>until:self.fail('run did not finish')
            time.sleep(.01)
        self.assertEqual(calls,[[1,2,3]])

    def test_sunday_overnight_schedule_belongs_to_window_start_day(self):
        task={**self.payload(),'frequency':'daily','at':'01:00','weekdays':[6], 'window':{'start':'22:00','end':'02:00'}}
        now=datetime.fromisoformat('2026-11-01T12:00:00+08:00').timestamp()
        self.assertEqual(next_due(task,now),datetime.fromisoformat('2026-11-02T01:00:00+08:00').timestamp())

    def test_new_source_timer_wakeup_catches_up_at_most_once(self):
        identity=self.scheduler.save({**self.payload(),'enabled':True,'minutes':1})['id'];calls=[]
        self.scheduler.source_analyzer=lambda *args:(calls.append(args[0]) or ('succeeded',0,'{"events":[]}'))
        with self.scheduler.db() as db:db.execute('UPDATE tasks SET next_run=? WHERE id=?',(time.time()-86400,identity))
        self.scheduler.tick();self.scheduler.tick()
        until=time.monotonic()+3
        while self.scheduler.runs()['runs'][0]['status']=='running':
            if time.monotonic()>until:self.fail('catchup did not finish')
            time.sleep(.01)
        self.assertEqual(len(calls),1);self.assertEqual(len(self.scheduler.runs()['runs']),1)
        self.assertGreater(self.scheduler.listing()['tasks'][0]['next_run'],time.time())


class SourceReceiptTests(unittest.TestCase):
    def test_pending_cancel_keeps_committed_events_and_no_unbound_receipt(self):
        from wechat_source_client import WechatSourceClient
        from isolation_patches import module_time,clock_sequence
        client=WechatSourceClient(SimpleNamespace(workspace=Path('/tmp')))
        rid='a'*32;tid='b'*32;cancel=threading.Event();cancel.set()
        pending={'source':'wechat','events':[{'event':1,'status':'succeeded','processed':7},{'event':3,'status':'running'}]}
        result={'executor':'wechat-source-daemon','run_id':rid,'task_id':tid,'kind':'source_events','status':'running','output':json.dumps(pending)}
        with patch.object(client,'request',return_value=result),module_time('wechat_source_client',monotonic=clock_sequence([10,15])):
            status,_,output=client.analyze_events(rid,{'id':tid},cancel,{},99)
        self.assertEqual(status,'cancelled');data=json.loads(output)
        self.assertTrue(data['receipt_pending']);self.assertEqual(data['events'][0]['processed'],7)
        with patch.object(client,'request',return_value={**result,'task_id':'c'*32}),self.assertRaises(ValueError):client.event_receipt(rid,tid)
        with patch.object(client,'request',return_value={**result,'run_id':'d'*32}),self.assertRaises(ValueError):client.analyze_events(rid,{'id':tid},cancel,{},99)

    def test_final_receipt_reconciles_without_reexecution_or_rewriting_timeout(self):
        with tempfile.TemporaryDirectory() as root:
            scheduler=ScheduledTasks(root,autostart=False)
            self.addCleanup(scheduler.close)
            rid='a'*32;tid='b'*32
            with scheduler.db() as db:
                db.execute('INSERT INTO runs(id,task_id,snapshot,trigger,status,started_at,finished_at,output) VALUES(?,?,?,?,?,?,?,?)',
                    (rid,tid,json.dumps({'kind':'source_events'}),'manual','timeout','2026-01-01T00:00:00+00:00','2026-01-01T00:01:00+00:00',json.dumps({'receipt_pending':True,'events':[]})))
            scheduler.source_analyzer=unittest.mock.Mock()
            reply={'run_id':rid,'task_id':tid,'kind':'source_events','status':'cancelled','output':json.dumps({'source':'wechat','events':[{'event':3,'status':'partial','processed':9}]})}
            scheduler.source_receipt_reader=unittest.mock.Mock(return_value={**reply,'task_id':'c'*32})
            scheduler.reconcile_source_receipt();self.assertEqual(json.loads(scheduler.get_run(rid)['output'])['events'],[])
            scheduler.source_receipt_cursor=0;scheduler.source_receipt_reader.return_value=reply
            scheduler.reconcile_source_receipt();after=scheduler.get_run(rid);data=json.loads(after['output'])
            self.assertEqual(after['status'],'timeout');self.assertEqual(after['finished_at'],'2026-01-01T00:01:00+00:00')
            self.assertEqual(data['events'][0]['processed'],9);self.assertFalse(data['receipt_pending']);self.assertTrue(data['source_receipt_final'])
            scheduler.reconcile_source_receipt();scheduler.reconcile_source_receipt()
            self.assertEqual(scheduler.source_receipt_reader.call_count,2);scheduler.source_analyzer.assert_not_called()
