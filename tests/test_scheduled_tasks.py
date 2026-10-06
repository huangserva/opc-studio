import concurrent.futures
from datetime import datetime
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scheduled_tasks import ScheduledTasks,next_due,MAX_OUTPUT

class SchedulerTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.cwd=self.tmp.name
        self.scheduler=ScheduledTasks(self.cwd,autostart=False)
    def tearDown(self):
        self.scheduler.close();self.tmp.cleanup()
    def payload(self,**kw):
        return dict(name='合成测试',kind='script',body="printf '合成资料验证'",cwd=self.cwd,frequency='interval',minutes=60,at='08:00',weekdays=list(range(7)),ends_at=None,timeout=5,enabled=False,profile_id=None,allow_commands=False,**kw)
    def save(self,**kw):return self.scheduler.save(self.payload(**kw))['id']
    def wait(self,identity):
        deadline=time.monotonic()+10
        while time.monotonic()<deadline:
            row=next(r for r in self.scheduler.runs()['runs'] if r['id']==identity)
            if row['status']!='running':return row
            time.sleep(.05)
        self.fail('run did not finish')
    def test_script_records_actual_output_exit_and_immutable_revision(self):
        identity=self.save();run=self.scheduler.control(identity,'run')['id'];result=self.wait(run)
        self.assertEqual(result['status'],'succeeded');self.assertEqual(result['exit_code'],0);self.assertEqual(result['output'],'合成资料验证')
        task=self.scheduler.listing()['tasks'][0];task['body']='exit 9';self.scheduler.save(task)
        self.assertEqual(self.scheduler.runs()['runs'][0]['snapshot']['body'],"printf '合成资料验证'")
        self.assertEqual(self.wait(self.scheduler.control(identity,'run')['id'])['exit_code'],9)
    def test_default_pause_and_revision_conflict(self):
        identity=self.save();task=self.scheduler.listing()['tasks'][0];self.assertIsNone(task['next_run'])
        self.scheduler.control(identity,'resume');self.assertIsNotNone(self.scheduler.listing()['tasks'][0]['next_run'])
        with self.assertRaisesRegex(ValueError,'已被更新'):self.scheduler.save(task)
    def test_due_claim_is_atomic_and_overlaps_do_not_launch(self):
        identity=self.save();
        def claim():
            try:return self.scheduler.claim(identity,'manual',launch=False)
            except ValueError:return None
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool: results=list(pool.map(lambda _:claim(),range(8)))
        self.assertEqual(sum(r is not None for r in results),1);self.assertEqual(len(self.scheduler.runs()['runs']),1)

    def test_same_model_waits_without_failure_or_advancing_due_time(self):
        from types import SimpleNamespace
        from scheduled_tasks import ModelBusy
        config={'model':'synthetic','endpoint':'http://127.0.0.1:1/v1/chat/completions','api_key':'synthetic-only'}
        self.scheduler.assistant=SimpleNamespace(model_document=lambda:{'profiles':[{'id':'fake','config':config}]},settings=lambda:{'profiles':[]})
        p=self.payload();p.update(kind='pi',profile_id='fake')
        first=self.scheduler.save(p)['id'];second=self.scheduler.save(p)['id']
        self.scheduler.claim(first,'manual',launch=False)
        with self.assertRaises(ModelBusy):self.scheduler.claim(second,'manual',launch=False)
        self.scheduler.control(second,'resume')
        due=time.time()-30
        with self.scheduler.db() as db:db.execute('UPDATE tasks SET next_run=? WHERE id=?',(due,second))
        self.scheduler.tick()
        self.assertEqual(len(self.scheduler.runs()['runs']),1)
        with self.scheduler.db() as db:self.assertEqual(db.execute('SELECT next_run FROM tasks WHERE id=?',(second,)).fetchone()[0],due)
    def test_missed_intervals_catchup_once_and_move_forward(self):
        identity=self.save();self.scheduler.control(identity,'resume')
        with self.scheduler.db() as db:db.execute('UPDATE tasks SET next_run=? WHERE id=?',(time.time()-86400,identity))
        self.scheduler.tick();self.scheduler.tick()
        result=self.wait(self.scheduler.runs()['runs'][0]['id']);self.assertEqual(result['status'],'succeeded')
        self.assertEqual(len(self.scheduler.runs()['runs']),1);self.assertGreater(self.scheduler.listing()['tasks'][0]['next_run'],time.time())
    def test_stale_due_claim_does_not_override_pause(self):
        identity=self.save();self.scheduler.control(identity,'resume');due=self.scheduler.listing()['tasks'][0]['next_run']
        self.scheduler.control(identity,'pause');self.assertIsNone(self.scheduler.claim(identity,'scheduled',due,launch=False))
    def test_timeout_and_cancel_kill_processes(self):
        status,code,output=self.scheduler.shell('sleep 30',self.cwd,threading.Event(),time.monotonic()+.15)
        self.assertEqual(status,'timeout');self.assertNotEqual(code,0)
        cancel=threading.Event();cancel.set();status,_,_=self.scheduler.shell('sleep 30',self.cwd,cancel,time.monotonic()+10)
        self.assertEqual(status,'cancelled')
    def test_output_is_bounded_and_background_children_terminated(self):
        status,_,output=self.scheduler.shell("/usr/bin/yes x | /usr/bin/head -c 100000; sleep 30 &",self.cwd,threading.Event(),time.monotonic()+3)
        self.assertEqual(status,'succeeded');self.assertIn('仅保留末尾',output);self.assertLess(len(output.encode()),MAX_OUTPUT+100)
    def test_restart_keeps_history_and_marks_abandoned_runs(self):
        identity=self.save();run=self.scheduler.claim(identity,'manual',launch=False)['id'];self.scheduler.close()
        self.scheduler=ScheduledTasks(self.cwd,autostart=False)
        self.assertEqual(self.scheduler.runs()['runs'][0]['status'],'interrupted');self.assertFalse(self.scheduler.listing()['tasks'][0]['running'])
    def test_daily_weekdays_and_expiry_use_beijing(self):
        task=self.payload();task.update(frequency='daily',weekdays=[0],at='08:00')
        now=datetime.fromisoformat('2026-10-05T08:00:00+08:00').timestamp()
        self.assertEqual(datetime.fromtimestamp(next_due(task,now)).timestamp(),datetime.fromisoformat('2026-10-12T08:00:00+08:00').timestamp())
        task['ends_at']='2026-10-06T00:00:00+08:00';self.assertIsNone(next_due(task,now))
    def test_validation_and_private_files(self):
        p=self.payload();p['timeout']=True
        with self.assertRaises(ValueError):self.scheduler.save(p)
        p=self.payload();p['cwd']='relative'
        with self.assertRaises(ValueError):self.scheduler.save(p)
        self.assertEqual(self.scheduler.db_path.stat().st_mode&0o777,0o600)
        self.assertEqual(self.scheduler.folder.stat().st_mode&0o777,0o700)
    def test_delete_preserves_run_and_expired_tasks_do_not_execute(self):
        identity=self.save();run=self.scheduler.control(identity,'run')['id'];self.wait(run);self.scheduler.control(identity,'delete')
        self.assertEqual(len(self.scheduler.runs()['runs']),1);self.assertEqual(self.scheduler.listing()['tasks'],[])
        p=self.payload();p['ends_at']='2020-01-01T00:00:00+08:00';identity=self.scheduler.save(p)['id']
        self.assertIsNone(self.scheduler.control(identity,'run'));self.assertEqual(len(self.scheduler.runs()['runs']),1)
    def test_pi_is_fresh_scoped_has_no_tools_by_default_and_no_secret_in_history(self):
        from types import SimpleNamespace
        config={'model':'synthetic','endpoint':'http://127.0.0.1:1/v1/chat/completions','api_key':'not-a-real-key'}
        self.scheduler.assistant=SimpleNamespace(model_document=lambda:{'profiles':[{'id':'fake','config':config}]},settings=lambda:{'profiles':[]})
        p=self.payload();p.update(kind='pi',profile_id='fake',body='仅处理合成文字')
        identity=self.scheduler.save(p)['id']; seen=[]
        def fake(runtime,job,system,tools):
            seen.append((runtime.owner,dict(job),tools));return {'status':'done','answer':'合成输出'}
        with patch('pi_runtime.PiRuntime.run',fake):
            first=self.wait(self.scheduler.control(identity,'run')['id']);second=self.wait(self.scheduler.control(identity,'run')['id'])
        self.assertEqual(first['status'],'succeeded');self.assertEqual(seen[0][0].turns,[]);self.assertEqual(seen[0][2],[])
        self.assertEqual(seen[0][1]['_schedule_cwd'],str(Path(self.cwd).resolve()));self.assertEqual(seen[0][1]['context'],{})
        self.assertNotEqual(seen[0][1]['conversation_id'],seen[1][1]['conversation_id'])
        self.assertNotIn('not-a-real-key',json.dumps(self.scheduler.runs()))
    def test_pi_command_permission_enforced(self):
        task=self.payload();config={}
        def fake(runtime,job,system,tools):
            with self.assertRaisesRegex(ValueError,'未授权'):runtime.owner.execute('run_command',{'command':'touch forbidden'},job)
            return {'status':'done','answer':'合成输出'}
        with patch('pi_runtime.PiRuntime.run',fake):self.scheduler.pi('a'*32,task,threading.Event(),config,time.monotonic()+10)
        self.assertFalse((Path(self.cwd)/'forbidden').exists())

    def test_real_pi_protocol_uses_fake_transport_and_runs_only_opted_in_tool(self):
        from types import SimpleNamespace
        from pi_runtime import PiRuntime
        original=PiRuntime.__init__
        def inject(runtime,owner):
            original(runtime,owner)
            responses=[{'role':'assistant','content':None,'tool_calls':[{'id':'synthetic1','type':'function','function':{'name':'run_command','arguments':json.dumps({'command':"printf 'synthetic-pi-tool'"})}}]}, {'role':'assistant','content':'synthetic-pi-finished'}]
            owner.client=lambda *args,**kw:responses.pop(0)
        config={'model':'synthetic','endpoint':'http://127.0.0.1:1/v1/chat/completions','api_key':'synthetic-only','timeout_seconds':10}
        self.scheduler.assistant=SimpleNamespace(model_document=lambda:{'profiles':[{'id':'fake','config':config}]},settings=lambda:{'profiles':[]})
        p=self.payload();p.update(kind='pi',profile_id='fake',allow_commands=True,timeout=15)
        identity=self.scheduler.save(p)['id']
        with patch('pi_runtime.PiRuntime.__init__',inject):result=self.wait(self.scheduler.control(identity,'run')['id'])
        self.assertEqual(result['status'],'succeeded',result['output'])
        self.assertIn('synthetic-pi-finished',result['output']);self.assertIn('synthetic-pi-tool',result['output'])

if __name__=='__main__':unittest.main()
