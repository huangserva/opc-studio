"""Acquisition/control tests with synthetic SQLite and isolated child workers."""
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from knowledge_store import Knowledge, identifier
from server import Store
from wechat_capture import WechatCapture
from wechat_refresh_worker import read_private, write_private


class CaptureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.collector = self.root / 'collector.db'
        self.helpers = self.root / 'helpers'
        self.helpers.mkdir()
        with sqlite3.connect(self.collector) as c:
            c.execute('CREATE TABLE messages(chatroom_id,local_id,sender,content,msg_time,msg_type)')
            c.execute('CREATE TABLE watched_chats(chatroom_id,chatroom_name)')
            c.execute("INSERT INTO watched_chats VALUES('g@chatroom','合成项目群')")
            c.execute("INSERT INTO messages VALUES('g@chatroom','1','小明','既有消息完整保留',1791018000,1)")
        (self.helpers / 'refresh_snapshot.py').write_text('''
from pathlib import Path
from types import SimpleNamespace
import json,time
class FinishError(Exception): pass
f=SimpleNamespace(FinishError=FinishError,private_root=lambda:Path(__file__).parent,business_databases=lambda folder:{'synthetic.db'},
load_config=lambda base:json.loads((base/'settings.json').read_text()))
def prepare(capture):
 time.sleep(.15)
 if (capture/'FAIL').exists():raise FinishError('合成校验失败')
 snapshot=capture/'snapshot';snapshot.mkdir(exist_ok=True)
 return {'snapshot_dir':str(snapshot)}
def decrypt(snapshot):return {'expected_databases':['synthetic.db'],'core_checked':['synthetic.db']}
''')
        (self.helpers / 'stage_collector.py').write_text('''
from pathlib import Path
import sqlite3
def stage(snapshot,timeout):return {'stage_dir':str(snapshot),'staged_state':{'messages':2,'max_msg_time':1791018010},'source_state':{'messages':1}}
def commit(stage,execute,timeout):
 if not execute:raise AssertionError('must explicitly commit')
 import refresh_snapshot
 settings=refresh_snapshot.f.load_config(refresh_snapshot.f.private_root())
 if (stage.parent/'UNKNOWN').exists():raise RuntimeError('synthetic uncertain commit')
 with sqlite3.connect(settings['collector_db']) as c:
  if not c.execute("SELECT 1 FROM messages WHERE local_id='2'").fetchone():
   c.execute("INSERT INTO messages VALUES('g@chatroom','2','小明','新增消息有原文依据',1791018010,1)")
 return {'messages_imported':True,'collector_messages':2,'new_messages':1,'max_msg_time':1791018010,'commit_receipt_saved':True}
def database_state(path):return {'messages':999}
''')
        (self.helpers / 'settings.json').write_text(__import__('json').dumps({'collector_db':str(self.collector),'db_dir':str(self.helpers)}))
        self.capture_dir = self.root / 'captured-keys'
        self.capture_dir.mkdir()
        store = Store(self.root / 'studio')
        self.k = Knowledge(store, [{'id': 'wechat', 'name': '合成微信', 'kind': 'wechat', 'backend': 'collector',
            'path': str(self.collector), 'names_path': str(self.collector)}])
        self.k.sync('wechat')
        write_private(self.k.capture.root / 'config.json', {'helper_dir': str(self.helpers),
            'capture_dir': str(self.capture_dir), 'collector_db': str(self.collector),
            'python': sys.executable, 'timeout_seconds': 30})

    def tearDown(self):
        self.k.capture.close()
        self.wait_finished()
        self.temp.cleanup()

    def wait_finished(self):
        until = time.monotonic() + 10
        while self.k.capture.lock.locked() and time.monotonic() < until:
            time.sleep(.02)
        self.assertFalse(self.k.capture.lock.locked(), 'live worker did not finish')

    def test_real_worker_commit_then_index_preserves_old_identity_and_returns_no_private_plan(self):
        self.k.start_wechat_capture()
        self.wait_finished()
        source = self.k.status()['sources'][0]
        self.assertEqual(source['count'], 2)
        self.assertEqual(source['capture']['status'], 'done')
        self.assertEqual(source['capture']['last_success']['index_status'], 'ready')
        self.assertIn('captured_at', source['capture']['last_success'])
        self.assertEqual(self.k.document(identifier('wechat', 'g@chatroom:1'))['body'], '既有消息完整保留')
        self.assertEqual(self.k.document(identifier('wechat', 'g@chatroom:2'))['provenance']['raw_local_id'], '2')
        self.assertNotIn('capture_dir', str(source['capture']))
        self.assertEqual((self.k.capture.root / 'last-success.json').stat().st_mode & 0o777, 0o600)

    def test_collector_capture_does_not_drop_a_concurrent_mail_index_job(self):
        self.k.sync_lock.acquire()
        try:
            self.k.start_wechat_capture()
            until = time.monotonic() + 5
            while time.monotonic() < until:
                _, receipt = self.k.capture._latest()
                if receipt.get('index_status') == 'pending':
                    break
                time.sleep(.02)
            self.assertEqual(receipt.get('index_status'), 'pending')
            self.assertEqual(self.k.status()['total'], 1)
            self.assertTrue(self.k.capture.status()['busy'])
        finally:
            self.k.sync_lock.release()
        self.wait_finished()
        self.assertEqual(self.k.status()['total'], 2)

    def test_shared_inherited_lock_prevents_a_second_controller_after_restart(self):
        self.k.start_wechat_capture()
        other = WechatCapture(self.k)
        with self.assertRaises((BlockingIOError, ValueError)):
            other.start()
        self.wait_finished()
        self.assertEqual(self.k.status()['total'], 2)

    def test_verification_failure_preserves_old_index_and_last_success(self):
        previous = {'captured_at': '2026-10-01T00:00:00+00:00', 'index_status': 'ready'}
        write_private(self.k.capture.root / 'last-success.json', previous)
        before = self.collector.read_bytes()
        (self.capture_dir / 'FAIL').touch()
        self.k.start_wechat_capture()
        self.wait_finished()
        state = self.k.capture.status()
        self.assertEqual(state['status'], 'error')
        self.assertEqual(state['last_success'], previous)
        self.assertEqual(self.collector.read_bytes(), before)
        self.assertEqual(self.k.status()['total'], 1)

    def test_ambiguous_commit_stops_automatic_retry_without_claiming_capture_success(self):
        (self.capture_dir / 'UNKNOWN').touch()
        self.k.start_wechat_capture()
        self.wait_finished()
        state = self.k.capture.status()
        self.assertEqual(state['status'], 'commit_unknown')
        self.assertNotIn('last_success', state)
        self.k.configure_source({'id': 'wechat', 'capture_enabled': True})
        self.k.capture.retry_after = 0
        with patch.object(self.k.capture, 'start') as start:
            self.k.capture.maybe_start()
            start.assert_not_called()

    def test_schedule_opt_in_and_pause_are_independent_of_index_updates(self):
        with patch.object(self.k.capture, 'start') as start:
            self.k.capture.maybe_start()
            start.assert_not_called()
            self.k.configure_source({'id': 'wechat', 'capture_enabled': True})
            self.k.capture.maybe_start()
            start.assert_called_once()
            self.k.configure_source({'id': 'wechat', 'enabled': False})
            self.k.capture.maybe_start()
            start.assert_called_once()
        with self.assertRaisesRegex(ValueError, '暂停'):
            self.k.start_wechat_capture()
        original = self.k.status()['sources'][0]
        with self.assertRaises(ValueError):
            self.k.configure_source({'id': 'wechat', 'capture_enabled': 'yes'})
        self.assertEqual(self.k.status()['sources'][0], original)

    def test_capture_endpoint_rejects_foreign_origin_before_starting_worker(self):
        from http.server import ThreadingHTTPServer
        import json
        import threading
        import urllib.error
        import urllib.request
        from server import Handler
        self.k.store._knowledge = self.k
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        server.store = self.k.store
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = 'http://127.0.0.1:' + str(server.server_port)
        op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            for origin in ['https://example.test', '']:
                req = urllib.request.Request(base + '/api/knowledge/wechat/capture', data=b'{}',
                    headers={'Origin': origin, 'X-Article-Studio': '1', 'Content-Type': 'application/json'})
                with self.assertRaises(urllib.error.HTTPError) as failure:
                    op.open(req)
                self.assertEqual(failure.exception.code, 403)
                failure.exception.close()
                self.assertFalse(self.k.capture.lock.locked())
            req = urllib.request.Request(base + '/api/knowledge/wechat/capture', data=b'{}',
                headers={'Origin': base, 'X-Article-Studio': '1', 'Content-Type': 'application/json'})
            with op.open(req) as response:
                self.assertEqual(response.status, 202)
                self.assertTrue(json.load(response)['sources'][0]['capture']['busy'])
            self.wait_finished()
            self.assertEqual(self.k.status()['total'], 2)
        finally:
            server.shutdown()
            server.server_close()


class DeadlineTests(unittest.TestCase):
    def test_nested_access_check_does_not_extend_the_total_deadline(self):
        from wechat_refresh_worker import deadline, Interrupted
        started = time.monotonic()
        with self.assertRaises(Interrupted):
            with deadline(.2):
                with deadline(2):
                    time.sleep(1)
        self.assertLess(time.monotonic() - started, .8)

    def test_access_prompt_timeout_is_distinct_and_never_reaches_prepare_or_commit(self):
        from types import SimpleNamespace
        import wechat_refresh_worker as worker
        def blocked_access(base):
            time.sleep(1)
        f = SimpleNamespace(FinishError=type('FinishError', (Exception,), {}),
            private_root=lambda: Path('/synthetic'), load_config=blocked_access)
        refresh_module = SimpleNamespace(f=f, prepare=lambda p: self.fail('access has not completed'))
        original = worker.deadline
        with tempfile.TemporaryDirectory() as name:
            folder = Path(name).resolve()
            folder.chmod(0o700)
            with patch.object(worker, 'deadline', side_effect=lambda seconds: original(min(seconds, .15))):
                result = worker.refresh({'timeout_seconds': 30}, folder, refresh_module, None)
            self.assertEqual(result['status'], 'error')
            self.assertEqual(result['error_kind'], 'access_unconfirmed')
            self.assertFalse(result['messages_imported'])
            self.assertNotIn('captured_at', result)

    def test_total_deadline_reaches_existing_child_cleanup_instead_of_orphaning_decrypt(self):
        import importlib
        from wechat_refresh_worker import deadline, Interrupted
        configured = os.environ.get('ARTICLE_STUDIO_WECHAT_HELPERS')
        if not configured or not (Path(configured) / 'finish_capture.py').is_file():
            self.skipTest('External cleanup contract requires ARTICLE_STUDIO_WECHAT_HELPERS')
        helpers = Path(configured)
        sys.path.insert(0, str(helpers))
        f = importlib.import_module('finish_capture')
        with tempfile.TemporaryDirectory() as name:
            folder = Path(name)
            pidfile = folder / 'child.pid'
            command = [sys.executable, '-c', 'import os,time,pathlib;pathlib.Path(' + repr(str(pidfile)) + ').write_text(str(os.getpid()));time.sleep(30)']
            with self.assertRaises(Interrupted):
                with deadline(.5):
                    f.run_private(command, 'synthetic-decrypt', folder, os.environ.copy(), 30)
            pid = int(pidfile.read_text())
            with self.assertRaises(ProcessLookupError):
                os.kill(pid, 0)


if __name__ == '__main__':
    unittest.main()
