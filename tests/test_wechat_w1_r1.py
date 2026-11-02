import itertools,json,os,sqlite3,unittest
from pathlib import Path
from unittest.mock import Mock,patch
import test_wechat_w1 as fixtures
from wechat_capture import WechatCapture
from wechat_refresh_worker import read_private,write_private

class ReceiptRepairTests(unittest.TestCase):
    setUp=fixtures.W1StorageTests.setUp
    tearDown=fixtures.W1StorageTests.tearDown
    wait_finished=fixtures.W1StorageTests.wait_finished
    receipt_job=fixtures.W1StorageTests.receipt_job

    def unfinished_job(self):
        cap,folder,fd=self.receipt_job()
        previous={'index_status':'ready','finished_at':'2026-10-01T00:00:00Z'}
        write_private(cap.root/'last-success.json',previous)
        with sqlite3.connect(self.collector) as db:db.execute("INSERT INTO messages VALUES('g@chatroom','2','合成作者','合成新消息',1791018010,1)")
        state=read_private(folder/'state.json');state['collector_messages']=2;write_private(folder/'state.json',state)
        return cap,folder,fd,previous

    def test_preindex_write_failure_has_own_stage_and_survives_controller_reload(self):
        cap,folder,fd,previous=self.unfinished_job()
        with patch('wechat_capture.write_private',side_effect=PermissionError('R1_PRIVATE_BODY_SENTINEL R1_CREDENTIAL_SENTINEL')),patch.object(self.k,'sync') as sync,self.assertLogs('wechat_capture') as logs:
            cap._finish(folder,Mock(),fd)
        sync.assert_not_called();state=cap.status()
        self.assertEqual(state['status'],'needs_review');self.assertEqual(state['error_stage'],'receipt_write_failed')
        self.assertEqual(state['exception_type'],'PermissionError');self.assertIn('索引尚未执行',state['error_reason']);self.assertNotIn('超时',state['error_reason'])
        self.assertNotIn('R1_PRIVATE',json.dumps(state)+str(logs.output));self.assertNotIn('R1_CREDENTIAL',json.dumps(state)+str(logs.output))
        self.assertTrue(state['messages_imported']);self.assertFalse(cap.lock.locked());self.assertFalse(self.k.sync_lock.locked())
        self.assertEqual(read_private(cap.root/'last-success.json'),previous)
        fresh=WechatCapture(self.k)
        try:
            after=fresh.status();self.assertEqual(after['status'],'needs_review');self.assertNotEqual(after.get('index_status'),'ready');self.assertNotIn('recovered',after)
        finally:fresh.close()
        with self.k.db() as db:self.assertEqual(db.execute("SELECT COUNT(*) FROM documents WHERE source='wechat'").fetchone()[0],1)

        # A successful read followed by a no-import receipt write failure is still a write failure.
        disk=read_private(folder/'state.json');disk['messages_imported']=False;write_private(folder/'state.json',disk)
        cap.lock.acquire();second_fd=os.open(folder/'fd',os.O_RDWR)
        with patch('wechat_capture.write_private',side_effect=PermissionError('synthetic')),self.assertLogs('wechat_capture'):
            cap._finish(folder,Mock(),second_fd)
        self.assertEqual(cap.status()['error_stage'],'receipt_write_failed')

    def test_chmod_readonly_folder_is_not_unindexed_success_after_reload(self):
        cap,folder,fd,previous=self.unfinished_job();folder.chmod(0o500)
        try:
            with patch.object(self.k,'sync') as sync,self.assertLogs('wechat_capture'):cap._finish(folder,Mock(),fd)
            sync.assert_not_called()
        finally:folder.chmod(0o700)
        self.assertEqual(cap.status()['error_stage'],'receipt_read_failed')
        self.assertIn('索引尚未执行',cap.status()['error_reason'])
        fresh=WechatCapture(self.k)
        try:
            state=fresh.status();self.assertEqual(state['status'],'needs_review');self.assertNotEqual(state.get('index_status'),'ready');self.assertNotIn('recovered',state)
        finally:fresh.close()
        self.assertEqual(read_private(cap.root/'last-success.json'),previous)
        with self.k.db() as db:self.assertEqual(db.execute("SELECT COUNT(*) FROM documents WHERE source='wechat'").fetchone()[0],1)

    def test_done_without_index_receipt_needs_matching_round_ready_evidence(self):
        cap,folder,fd,previous=self.unfinished_job();os.close(fd);cap.lock.release()
        self.assertEqual(cap.status()['status'],'needs_review')
        state=read_private(folder/'state.json');self.k.sync('wechat')
        ready={k:state[k] for k in ('captured_at','finished_at','collector_messages')};ready['index_status']='ready'
        write_private(cap.root/'last-success.json',ready)
        result=cap.status();self.assertEqual(result['status'],'done');self.assertEqual(result['index_status'],'ready');self.assertTrue(result['recovered'])
        self.assertNotIn('index_status',read_private(folder/'state.json'))

    def test_real_index_lock_timeout_still_has_index_wait_reason(self):
        cap,folder,fd,previous=self.unfinished_job();clock=itertools.chain([0,1,121],itertools.repeat(122));owner=Mock();owner.acquire.return_value=False
        with patch.object(self.k,'sync_lock',owner),patch('wechat_capture.time.monotonic',side_effect=lambda:next(clock)),self.assertLogs('wechat_capture'):
            cap._finish(folder,Mock(),fd)
        owner.acquire.assert_called_once_with(timeout=.5)
        state=cap.status();self.assertEqual(state['status'],'error');self.assertEqual(state['error_stage'],'index_wait');self.assertIn('超时',state['error_reason'])
        self.assertEqual(read_private(cap.root/'last-success.json'),previous)

    def test_unrelated_or_malformed_ready_receipt_cannot_confirm_done(self):
        cap,folder,fd,previous=self.unfinished_job();os.close(fd);cap.lock.release();state=read_private(folder/'state.json')
        for success in ([],dict(index_status='ready',finished_at=state['finished_at'],captured_at=state['captured_at'],collector_messages=1),previous):
            with self.subTest(receipt=success):
                write_private(cap.root/'last-success.json',success)
                self.assertEqual(cap.status()['status'],'needs_review')
