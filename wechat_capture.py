"""Workbench control for bounded, local WeChat acquisition and indexing."""
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import signal
import stat
import subprocess
import threading
import time
import uuid

from wechat_refresh_worker import clock, private_directory, read_private, write_private


class WechatCapture:
    def __init__(self, knowledge):
        self.knowledge = knowledge
        self.root = knowledge.root.resolve() / 'wechat-capture'
        self.root.mkdir(mode=0o700, exist_ok=True)
        private_directory(self.root)
        self.lock = threading.Lock()
        self.process = None
        self.closed = threading.Event()
        self.retry_after = 0

    def _plan(self):
        plan = read_private(self.root / 'config.json')
        required = ('helper_dir', 'capture_dir', 'collector_db', 'python')
        if not isinstance(plan, dict) or any(not isinstance(plan.get(k), str) or not Path(plan[k]).is_absolute() for k in required):
            raise ValueError('本机微信采集配置无效')
        if type(plan.get('timeout_seconds')) is not int or not 30 <= plan['timeout_seconds'] <= 7200:
            raise ValueError('本机采集总时限无效')
        if not Path(plan['python']).is_file() or any(not (Path(plan['helper_dir']) / name).is_file() for name in ('refresh_snapshot.py', 'stage_collector.py')):
            raise ValueError('本机采集依赖缺失')
        return plan

    def _source(self):
        with self.knowledge.db() as c:
            row = c.execute("SELECT config FROM sources WHERE id='wechat'").fetchone()
        return json.loads(row[0]) if row else {}

    def _latest(self):
        path = self.root / 'latest.json'
        if not path.exists():
            return None, {}
        pointer = read_private(path)
        identifier = pointer.get('id') if isinstance(pointer, dict) else None
        if not isinstance(identifier, str) or len(identifier) != 32 or any(ch not in '0123456789abcdef' for ch in identifier):
            raise ValueError('采集任务编号无效')
        folder = self.root / identifier
        return folder, read_private(folder / 'state.json')

    def status(self):
        try:
            self._plan()
        except (OSError, ValueError):
            return {'available': False, 'status': 'unavailable', 'detail': '尚未配置本机新消息采集；仍可更新已有快照索引'}
        try:
            folder, state = self._latest()
            if state.get('status') == 'running' and self.process is None:
                # Do not infer success/termination from an abandoned state file.
                state = dict(state, status='needs_review', detail='上一采集尚未确认结束；请核对任务，旧索引保留')
            result = {k: state.get(k) for k in ('status', 'phase', 'started_at', 'finished_at', 'detail',
                'messages_imported', 'new_messages', 'collector_messages', 'index_status', 'error_kind') if k in state}
            if folder and state.get('status') == 'error' and (folder / 'diagnostic.json').exists():
                diagnostic = read_private(folder / 'diagnostic.json')
                if diagnostic.get('source') == 'macos_access_audit' and isinstance(diagnostic.get('message'), str):
                    result['detail'] = diagnostic['message'][:300]
            success = self.root / 'last-success.json'
            if success.exists():
                result['last_success'] = read_private(success)
            return {'available': True, 'status': 'idle', **result, 'busy': self.lock.locked()}
        except (OSError, ValueError):
            return {'available': True, 'status': 'needs_review', 'busy': self.lock.locked(), 'detail': '采集回执需要核对；旧索引保留'}

    def _acquire_file_lock(self):
        path = self.root / '.capture.lock'
        fd = os.open(path, os.O_RDWR | os.O_CREAT | getattr(os, 'O_NOFOLLOW', 0), 0o600)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600:
                raise ValueError('采集协作锁无效')
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return fd
        except BaseException:
            os.close(fd)
            raise

    def start(self):
        if self.closed.is_set():
            raise ValueError('工作台正在关闭')
        plan, source = self._plan(), self._source()
        if source.get('backend') != 'collector' or source.get('path') != plan['collector_db']:
            raise ValueError('新消息采集只适用于配置一致的 wechat-assistant 来源')
        if source.get('enabled') is False:
            raise ValueError('微信来源已暂停')
        if not self.lock.acquire(blocking=False):
            raise ValueError('本机微信采集正在进行，请稍后')
        fd = None
        try:
            fd = self._acquire_file_lock()
            identifier = uuid.uuid4().hex
            folder = self.root / identifier
            folder.mkdir(mode=0o700)
            write_private(folder / 'plan.json', plan)
            write_private(folder / 'state.json', {'status': 'running', 'phase': 'starting', 'started_at': clock()})
            write_private(self.root / 'latest.json', {'id': identifier})
            environment = os.environ.copy()
            environment.update(PYTHONDONTWRITEBYTECODE='1', COREPACK_ENABLE_NETWORK='0')
            log_fd = os.open(folder / 'worker.log', os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(log_fd, 'wb') as handle:
                self.process = subprocess.Popen([plan['python'], '-B', str(Path(__file__).with_name('wechat_refresh_worker.py')),
                    '--job-dir', str(folder), '--lock-fd', str(fd)], stdout=handle, stderr=handle,
                    env=environment, start_new_session=True, pass_fds=(fd,))
            threading.Thread(target=self._finish, args=(folder, self.process, fd), daemon=True).start()
            fd = None
            return self.status()
        except BaseException:
            if fd is not None:
                os.close(fd)
            self.process = None
            self.lock.release()
            raise

    def _finish(self, folder, child, fd):
        try:
            child.wait()  # Worker deadline covers copying, decrypting and staging.
            state = read_private(folder / 'state.json')
            if state.get('status') == 'running':
                state.update(status='needs_review', detail='采集进程已退出，但完成回执缺失；旧索引保留')
            if state.get('messages_imported') is True:
                # The collector was acquired independently of the index. Wait
                # for an in-flight mail/index operation instead of losing it.
                state['index_status'] = 'pending'
                write_private(folder / 'state.json', state)
                until = time.monotonic() + 120
                acquired = False
                while not self.closed.is_set() and time.monotonic() < until:
                    if self.knowledge.sync_lock.acquire(timeout=.5):
                        acquired = True
                        break
                if acquired:
                    self.knowledge.sync('wechat', reserved=True)
                    with self.knowledge.db() as c:
                        row = c.execute("SELECT status,synced_at FROM sources WHERE id='wechat'").fetchone()
                    state['index_status'] = row['status']
                    state['indexed_at'] = row['synced_at']
                success = {k: state[k] for k in ('captured_at', 'finished_at', 'new_messages', 'collector_messages',
                    'max_msg_time', 'index_status', 'indexed_at') if k in state}
                write_private(self.root / 'last-success.json', success)
            write_private(folder / 'state.json', state)
        except BaseException:
            # No private traceback or key material is returned to HTTP callers.
            try:
                state = read_private(folder / 'state.json')
                state.update(status='needs_review', detail='采集回执或索引需要核对；旧资料保留')
                write_private(folder / 'state.json', state)
            except (OSError, ValueError):
                pass
        finally:
            os.close(fd)
            self.process = None
            self.retry_after = time.monotonic() + 3600
            self.lock.release()

    def maybe_start(self):
        source, state = self._source(), self.status()
        if source.get('enabled') is False or source.get('capture_enabled') is not True or not state.get('available') or state.get('busy'):
            return
        if state['status'] in ('commit_unknown', 'needs_review') or time.monotonic() < self.retry_after:
            return
        previous = state.get('finished_at')
        if previous:
            try:
                if (datetime.now(timezone.utc) - datetime.fromisoformat(previous)).total_seconds() < 3600:
                    return
            except (ValueError, TypeError):
                return
        try:
            self.start()
        except (OSError, ValueError):
            self.retry_after = time.monotonic() + 3600

    def close(self):
        self.closed.set()
        child = self.process
        if child is not None and child.poll() is None:
            child.send_signal(signal.SIGTERM)
            try:
                child.wait(timeout=20)
            except subprocess.TimeoutExpired:
                # Never kill only the coordinator while its separate child
                # group might still be cleaning up or committing.
                pass
