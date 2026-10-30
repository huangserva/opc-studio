"""真实跨进程 flock：终态回执不能使手动采集请求无界等待。"""
import contextlib
import json
import os
from pathlib import Path
import select
import subprocess
import sys
import threading
import time
import unittest
from unittest.mock import patch

import collect_adapters as adapters
import test_aisa_live_chain as chain

# 固定行为上限，不能随实现常量一起调大而掩盖回退。
MAX_START_SECONDS = 0.8


@contextlib.contextmanager
def external_lock(adapter):
    lockfile = Path(adapter.source.store.workspace) / 'aisa' / 'manual.lock'
    child = subprocess.Popen([
        sys.executable, '-u', '-c',
        "import os,fcntl,sys;fd=os.open(sys.argv[1],os.O_RDWR|os.O_CREAT,0o600);"
        "fcntl.flock(fd,fcntl.LOCK_EX);print('READY',flush=True);"
        "sys.stdin.readline();os.close(fd)", str(lockfile),
    ], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        if not select.select([child.stdout], [], [], 3)[0] or child.stdout.readline().strip() != 'READY':
            raise AssertionError('external lock holder did not become ready')
        yield child
    finally:
        child.communicate('release\n', timeout=3)
        if child.returncode != 0:
            raise AssertionError('external lock holder failed')


def seed(adapter, status='done'):
    identity, config = adapter._config(chain.AIsaFixture.config(None, 'xiaohongshu'))
    job = {'job_id': identity, **config, 'status': status, 'errors': [],
           'created_at': '2026-10-30T00:00:00Z', 'finished_at': '2026-10-30T00:00:01Z'}
    with contextlib.closing(adapter._job_db()) as db:
        db.execute('INSERT INTO jobs VALUES(?,?,?)',
                   (identity, json.dumps(config, sort_keys=True), json.dumps(job)))
        db.commit()
    return job


class LockWaitTests(unittest.TestCase):
    def setUp(self):
        self.fixture = chain.AIsaFixture()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.adapter = self.fixture.adapter

    def bounded_start(self):
        result = []
        def start():
            try:
                result.append(self.adapter.start(self.fixture.config('xiaohongshu', 'b' * 32)))
            except Exception as exc:
                result.append(exc)
        thread = threading.Thread(target=start, daemon=True)
        tick = time.monotonic()
        thread.start()
        thread.join(MAX_START_SECONDS)
        elapsed = time.monotonic() - tick
        return thread, result, elapsed

    def test_terminal_receipt_with_long_external_lock_has_bounded_error_and_closes_fd(self):
        seed(self.adapter)
        real_os = adapters.os
        opened, closed = [], []
        class TrackingOS:
            def __getattr__(self, name): return getattr(real_os, name)
            def open(self, *args, **kwargs):
                fd = real_os.open(*args, **kwargs)
                opened.append(fd)
                return fd
            def close(self, fd):
                closed.append(fd)
                return real_os.close(fd)
        thread = None
        try:
            with external_lock(self.adapter) as holder, patch.object(adapters, 'os', TrackingOS()):
                thread, result, elapsed = self.bounded_start()
                self.assertFalse(thread.is_alive(), f'start still blocked after {elapsed:.3f}s')
                self.assertIsNone(holder.poll(), 'lock holder must stay alive during the assertion')
                self.assertEqual(len(result), 1)
                self.assertIsInstance(result[0], ValueError)
                self.assertEqual(str(result[0]), '已有 AIsa 采集正在启动，请稍后查看')
                self.assertLess(elapsed, MAX_START_SECONDS)
                self.assertEqual(len(opened), 1)
                self.assertEqual(closed, opened, 'exactly one close for the waiting request fd')
                self.assertEqual(self.fixture.transport.calls, [])
        finally:
            if thread is not None: thread.join(3)

    def test_queued_published_while_waiting_reuses_without_transport(self):
        job = seed(self.adapter)
        publish_errors = []
        def publish():
            try:
                job['status'] = 'queued'
                self.adapter._write_job(job)
            except Exception as exc: publish_errors.append(exc)
        with external_lock(self.adapter) as holder:
            timer = threading.Timer(.05, publish)
            timer.start()
            thread, result, elapsed = self.bounded_start()
            timer.join(3)
            self.assertFalse(thread.is_alive())
            self.assertEqual(publish_errors, [])
            self.assertIsNone(holder.poll())
            self.assertLess(elapsed, MAX_START_SECONDS)
            self.assertEqual(result[0]['job_id'], 'a' * 32)
            self.assertEqual(result[0]['status'], 'queued')
            self.assertTrue(result[0]['reused'])
            self.assertEqual(self.fixture.transport.calls, [])

    def test_lock_taken_before_any_queued_receipt_returns_busy(self):
        with external_lock(self.adapter) as holder:
            tick = time.monotonic()
            with self.assertRaisesRegex(ValueError, '^已有 AIsa 采集正在启动，请稍后查看$'):
                self.adapter.start(self.fixture.config('xiaohongshu', 'b' * 32))
            self.assertLess(time.monotonic() - tick, MAX_START_SECONDS)
            self.assertIsNone(holder.poll())
            self.assertEqual(self.adapter.job(), {'status': 'idle', 'available': True})
            self.assertEqual(self.fixture.transport.calls, [])


class HttpLockWaitTests(unittest.TestCase):
    def test_real_http_done_receipt_with_external_lock_returns_before_release(self):
        fixture = chain.HttpChainTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        seed(fixture.adapter)
        with external_lock(fixture.adapter) as holder:
            tick = time.monotonic()
            status, body = fixture.request('/api/collect/aisa/run',
                                          chain.AIsaFixture.config(None, 'xiaohongshu', 'b' * 32))
            elapsed = time.monotonic() - tick
            self.assertEqual(status, 400)
            self.assertEqual(body['error'], '已有 AIsa 采集正在启动，请稍后查看')
            self.assertLess(elapsed, MAX_START_SECONDS)
            self.assertIsNone(holder.poll())
            self.assertEqual(fixture.transport.calls, [])
            print(json.dumps({'scenario': 'done+external_lock', 'port': fixture.http.server_port,
                              'elapsed_seconds': elapsed, 'http_status': status,
                              'error': body['error'], 'transport_calls': 0}, ensure_ascii=False), flush=True)
