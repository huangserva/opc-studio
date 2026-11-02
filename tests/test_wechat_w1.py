import json,logging,os,sqlite3,tempfile,threading,time,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch,Mock
from knowledge_store import Knowledge
from wechat_capture import WechatCapture
from wechat_refresh_worker import write_private,read_private
import test_wechat_capture as capture_fixture

class W1StorageTests(unittest.TestCase):
    setUp=capture_fixture.CaptureTests.setUp
    tearDown=capture_fixture.CaptureTests.tearDown
    wait_finished=capture_fixture.CaptureTests.wait_finished

    def receipt_job(self):
        cap=self.k.capture;folder=cap.root/('f'*32);folder.mkdir(mode=0o700,exist_ok=True)
        write_private(folder/'state.json',{'status':'done','phase':'captured','messages_imported':True,'new_messages':0,
            'collector_messages':1,'captured_at':'2026-11-02T00:00:00Z','finished_at':'2026-11-02T00:00:01Z'})
        write_private(cap.root/'latest.json',{'id':folder.name});cap.lock.acquire()
        return cap,folder,os.open(folder/'fd',os.O_CREAT|os.O_RDWR,0o600)

    def test_attach_existing_no_schema_or_other_source_reset(self):
        with self.k.db() as d:d.execute("INSERT INTO sources VALUES(?,?,?,?,?)",("mail",json.dumps({"id":"mail","kind":"manual","name":"合成邮件"}),"syncing","other job",None))
        with self.k.db() as d:before=d.execute("SELECT type,name,sql FROM sqlite_master ORDER BY type,name").fetchall()
        a=Knowledge.attach_existing(SimpleNamespace(workspace=self.k.store.workspace))
        b=Knowledge.attach_existing(SimpleNamespace(workspace=self.k.store.workspace))
        self.assertIsNot(a.sync_lock,b.sync_lock);self.assertIsNot(a._raw_meta_guard,b._raw_meta_guard)
        a.last_sync_error={'stage':'index'};self.assertIsNone(b.last_sync_error)
        with a.db() as d:d.execute("UPDATE sources SET detail='committed' WHERE id='wechat'")
        try:
            with a.db() as d:d.execute("UPDATE sources SET detail='rolled back' WHERE id='wechat'");raise ValueError('rollback')
        except ValueError:pass
        with a.db() as d:
            self.assertEqual(d.execute("SELECT detail FROM sources WHERE id='wechat'").fetchone()[0],'committed')
            self.assertEqual(d.execute("SELECT status FROM sources WHERE id='mail'").fetchone()[0],'syncing')
            self.assertEqual([tuple(x) for x in before],[tuple(x) for x in d.execute("SELECT type,name,sql FROM sqlite_master ORDER BY type,name")])

    def test_commit_then_index_failure_explicit_safe_receipt_and_source_error(self):
        cap,folder,fd=self.receipt_job();previous={'index_status':'ready','finished_at':'2026-10-01T00:00:00Z'}
        write_private(cap.root/'last-success.json',previous)
        secret='fixture-private-credential-do-not-echo'
        with patch.object(self.k,'_import',side_effect=ValueError(secret)),self.assertLogs('wechat_capture',logging.WARNING) as logs:
            cap._finish(folder,Mock(),fd)
        state=cap.status();self.assertEqual(state['status'],'error');self.assertEqual(state['error_stage'],'index')
        self.assertEqual(state['exception_type'],'ValueError');self.assertTrue(state['messages_imported'])
        self.assertNotIn(secret,json.dumps(state)+str(logs.output));self.assertEqual(read_private(cap.root/'last-success.json'),previous)
        with self.k.db() as d:self.assertEqual(d.execute("SELECT status FROM sources WHERE id='wechat'").fetchone()[0],'error')

    def test_index_success_then_receipt_write_failure_preserves_ready_not_false_success(self):
        cap,folder,fd=self.receipt_job();original=write_private
        def fail_last(path,value):
            if Path(path).name=='last-success.json':raise PermissionError('fixture-private-credential-do-not-echo')
            return original(path,value)
        with patch('wechat_capture.write_private',side_effect=fail_last),self.assertLogs('wechat_capture'):
            cap._finish(folder,Mock(),fd)
        state=cap.status();self.assertEqual(state['status'],'needs_review');self.assertEqual(state['index_status'],'ready')
        self.assertEqual(state['error_stage'],'receipt');self.assertEqual(state['exception_type'],'PermissionError')
        with self.k.db() as d:self.assertEqual(d.execute("SELECT status FROM sources WHERE id='wechat'").fetchone()[0],'ready')

    def test_persistent_receipt_failure_still_visible_in_memory(self):
        cap,folder,fd=self.receipt_job()
        def fail_receipts(path,value):
            if Path(path).name == 'last-success.json' or value.get('error_stage') == 'receipt':raise PermissionError('private body')
            return write_private(path,value)
        with patch('wechat_capture.write_private',side_effect=fail_receipts),self.assertLogs('wechat_capture'):
            cap._finish(folder,Mock(),fd)
        self.assertEqual(cap.status()['status'],'needs_review');self.assertEqual(cap.status()['error_stage'],'receipt')
        self.assertNotIn('private body',str(cap.status()))

    def test_sync_busy_second_round_does_not_overwrite_owner_status(self):
        with self.k.db() as d:d.execute("UPDATE sources SET status='syncing',detail='owner' WHERE id='wechat'")
        self.k.sync_lock.acquire()
        try:
            with self.assertRaisesRegex(ValueError,'资料正在同步'):self.k.sync('wechat')
            with self.k.db() as d:self.assertEqual(tuple(d.execute("SELECT status,detail FROM sources WHERE id='wechat'").fetchone()),('syncing','owner'))
        finally:self.k.sync_lock.release()

    def test_successful_capture_still_updates_last_success_with_ready(self):
        cap,folder,fd=self.receipt_job();cap._finish(folder,Mock(),fd)
        self.assertEqual(read_private(cap.root/'last-success.json')['index_status'],'ready')
        self.assertEqual(cap.status()['status'],'done')
