"""fixes-intel-lease-b · 阶段 L：任务所有权（持续持有的 flock + owner CAS）。

只跑临时库、合成进程与 /tmp 锁目录；零外网、不读凭证、不碰正式库、不重启服务。
多进程用子进程 ready 握手，所有 wait 都有超时并在 finally 回收；只对测试自己拉起的
子进程发信号，绝不碰现有服务或他人进程。

覆盖合同 §L必测：同 FD 到 finish、同进程/跨实例 claim 一次、持锁超龄/SIGSTOP 不回收、
SIGKILL 下次检查恢复、inode 替换/缺失/symlink unknown、两恢复者一 CAS、迟到 owner 零覆盖、
DB busy 有界并释放 FD、启动失败/定时 finish 异常释放、exec 不继承、fork 只 close 不解父锁、
legacy 兼容、重复迁移/未知版本、API 不暴露 owner 原信息。另有 5 项变异行为红例。

RecoveryOrderTests：情报任务恢复必须排在 schema 迁移之后（旧库上先跑恢复不告警、列会被
补齐；v1 行恢复行为不变；库读不了仍要告警），并带 1 组「恢复挪回迁移前」的变异例。
"""
import json
import importlib.util
import os
import signal
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import intel_job_ownership as ownership
import intel_store
from intel_store import (ABANDONED_JOB_SECONDS, Intel, LIVE_STALE_REASON, STALE_JOB_SECONDS)
from server import Store

REPO = str(Path(__file__).resolve().parent.parent)
BIZ = 'MzB5NTkwMDAwMDE='
NAME = '占位公众号'
PYTHON = sys.executable


class _UnavailableDiscovery:
    """测试用发现来源：明确不可用，零真实请求。"""

    name = 'unavailable'

    def capabilities(self):
        return {'provider': self.name, 'installed': False, 'available': False}

    def search(self, query, count, freshness, cancel=None, deadline=None):
        from intel_link_discovery import ProviderUnavailable
        raise ProviderUnavailable('发现来源不可用（测试）', provider=self.name)


def _no_reader(link):
    raise ValueError('测试不读正文')


def fake_discovery(store):
    return {'provider': _UnavailableDiscovery(), 'reader': _no_reader}


# 子进程：取锁并在库里写一条 v1 running 行，打印 ready 后长睡，供 SIGSTOP/SIGKILL 使用。
HOLDER = r'''
import json, sqlite3, sys, time
import intel_job_ownership as ow
root, token, dbpath = sys.argv[1], sys.argv[2], sys.argv[3]
h = ow.acquire(root, token)
c = sqlite3.connect(dbpath)
c.execute("INSERT INTO jobs(id,fakeid,name,mode,status,started_at,ownership_version,"
          "owner_token,owner_dev,owner_ino) VALUES('job',?,?,?,?,?,?,?,?,?)",
          ('MzB5NTkwMDAwMDE=', 'x', 'latest', 'running', '2020-01-01T00:00:00+00:00',
           1, token, h.dev, h.ino))
c.commit(); c.close()
print(json.dumps({'token': token, 'dev': h.dev, 'ino': h.ino}), flush=True)
time.sleep(120)
'''

# 子进程：尝试对给定锁文件取 flock；父进程持锁时应打印 busy。
ATTEMPT = r'''
import fcntl, os, sys
fd = os.open(sys.argv[1], os.O_RDWR | os.O_CLOEXEC)
try:
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    print('locked')
except OSError:
    print('busy')
os.close(fd)
'''

# 子进程：ready/go 文件握手后尝试一次定时认领，结果写 result 文件。
# argv: db, biz, ready, go, result, release（release 非空时认领成功后要等该文件才关闭）。
CLAIMER = r'''
import json, os, sys, time
from server import Store
from intel_store import Intel
db, biz, ready, go, result, release = sys.argv[1:7]
intel = Intel(Store(db))
with open(ready, 'w') as fh:
    fh.write('1')
deadline = time.monotonic() + 15
while not os.path.exists(go) and time.monotonic() < deadline:
    time.sleep(0.01)
handle = intel._claim_scheduled_job({'fakeid': biz, 'name': 'probe'})
with open(result, 'w') as fh:
    json.dump({'claimed': handle is not None}, fh)
if handle is not None:
    if release:
        wait_deadline = time.monotonic() + 15
        while not os.path.exists(release) and time.monotonic() < wait_deadline:
            time.sleep(0.01)
    handle.close()
'''

# 子进程：ready/go 握手后对库里的 'job' 做一次恢复探测 + CAS，报告是否抢到（won）。
RECOVERER = r'''
import json, os, sys, time
import intel_job_ownership as ow
from server import Store
from intel_store import Intel
db, ready, go, result = sys.argv[1:5]
intel = Intel(Store(db))
root = ow.normalize_root(intel.workspace)
with open(ready, 'w') as fh:
    fh.write('1')
deadline = time.monotonic() + 15
while not os.path.exists(go) and time.monotonic() < deadline:
    time.sleep(0.01)
# 用 _connect 直接读快照：不触发 _ensure 的自动恢复，保证两进程在同一 go 后才竞争 CAS。
conn = intel._connect(timeout=1.0, busy_timeout=1000)
try:
    row = dict(conn.execute("SELECT id,fakeid,owner_token,owner_dev,owner_ino FROM jobs "
                            "WHERE id='job'").fetchone())
finally:
    conn.close()
state, handle = ow.probe(root, row['owner_token'], row['owner_dev'], row['owner_ino'])
won = 0
if state == ow.ACQUIRED:
    won = int(intel._cas_recover(row) or 0)
    handle.close()
with open(result, 'w') as fh:
    json.dump({'won': won}, fh)
'''

OLD_INTEL_PATH = str(Path(REPO) / 'coordination' / 'backups' / 'fixes-intel-lease-b-L' / 'before' / 'intel_store.py')
ROLLBACK_SCRIPT = str(Path(REPO) / 'coordination' / 'rollback' / 'intel-lease-b-L2-rollback.py')


def load_old_intel():
    """加载未改动的 ce87 旧代码（回滚目标），证明回滚后旧代码安全。"""
    import importlib.util
    spec = importlib.util.spec_from_file_location('old_intel_rollback', OLD_INTEL_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module



def run_child(code, *args, timeout=15):
    """跑一段子进程脚本，返回 (stdout, returncode)。环境注入仓库路径。"""
    env = dict(os.environ, PYTHONPATH=REPO, PYTHONDONTWRITEBYTECODE='1')
    proc = subprocess.run([PYTHON, '-c', code, *args], cwd=REPO, env=env,
                          capture_output=True, text=True, timeout=timeout)
    return proc.stdout, proc.returncode


def read_line_bounded(proc, timeout):
    """有界读一行：超时就 kill 子进程并抛错，绝不让 readline 无限阻塞。"""
    box = {}

    def reader():
        try:
            box['line'] = proc.stdout.readline()
        except Exception:  # noqa: BLE001
            box['line'] = ''

    thread = threading.Thread(target=reader, daemon=True)
    thread.start()
    thread.join(timeout)
    if thread.is_alive():
        proc.kill()
        try:
            proc.wait(timeout=5)
        except Exception:  # noqa: BLE001
            pass
        thread.join(1)
        raise AssertionError('子进程 ready 超时')
    return box.get('line', '')


def wait_pid_bounded(pid, timeout):
    """有界回收子进程：超时 kill 再 wait，绝不用 waitpid(pid, 0) 死等。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        got, status = os.waitpid(pid, os.WNOHANG)
        if got:
            return status
        time.sleep(0.02)
    os.kill(pid, signal.SIGKILL)
    os.waitpid(pid, 0)
    raise AssertionError('子进程未在有界时间内退出')


def wait_for_file(path, timeout, what='文件'):
    """有界等待一个文件出现；超时抛错。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if os.path.exists(path):
            return
        time.sleep(0.01)
    raise AssertionError(f'{what} 未在有界时间内出现：{path}')


def wait_for_processes(procs, timeout):
    """有界等待一组子进程退出；超时 kill 后回收。"""
    deadline = time.monotonic() + timeout
    for proc in procs:
        remaining = max(0.1, deadline - time.monotonic())
        try:
            proc.wait(timeout=remaining)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
            raise AssertionError('子进程未在有界时间内退出')


def attempt_lock(path):
    out, code = run_child(ATTEMPT, str(path), timeout=10)
    if code != 0:
        raise AssertionError(f'子进程取锁失败：{out}')
    return out.strip() == 'locked'


class BlockingPublic:
    """在三期主路（合集）上阻塞，用来稳定观察“任务进行中”。"""

    def __init__(self):
        self.entered = threading.Event()
        self.release = threading.Event()

    def begin_round(self):
        return None

    def album_page(self, identifier, album_id, begin_msgid=None, begin_itemidx=None):
        self.entered.set()
        self.release.wait(10)
        return {'title': '占位合集', 'article_count': 0, 'nickname': NAME, 'items': [],
                'continue_flag': False}

    def sogou_search(self, *a, **k):
        return []

    def sogou_resolve(self, *a, **k):
        return ''


class _NullClient:
    def __init__(self, intel):
        from intel_wechat_mp import BackendQuota
        from intel_store import Settings
        self.quota = BackendQuota(Settings(intel))

    def credentials_ready(self):
        return ('stub', '9999999999')

    def search_biz(self, query):
        return []

    def appmsg_list(self, identifier, begin, count):
        return []

    def fetch_article(self, url):
        raise AssertionError('这些测试不该抓正文')


class OwnershipTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(self.tmp.name)
        self.public = BlockingPublic()
        self.intel = Intel(self.store, public_factory=lambda s: self.public,
                           discovery_factory=fake_discovery)
        self.intel._client = _NullClient(self.intel)
        self.intel.subscribe({'fakeid': BIZ, 'name': NAME})
        self.intel.remember_albums(BIZ, ['1234567890123456'])
        self.root = ownership.normalize_root(self.intel.workspace)
        self.children = []

    def tearDown(self):
        self.public.release.set()
        for worker in list(self.intel.workers.values()):
            worker.join(5)
        for proc in self.children:
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=5)
        self.tmp.cleanup()

    def lock_path(self, token):
        return ownership.locks_dir(self.root) / f'{token}.lock'

    def handshake_paths(self, tag):
        base = Path(self.tmp.name) / f'hs-{tag}'
        return str(base) + '.ready', str(base) + '.go', str(base) + '.result'

    def insert_v1(self, job_id, token, dev, ino, status='running', started='2020-01-01T00:00:00+00:00'):
        self.insert_identity(job_id, 1, token, dev, ino, started=started, status=status)

    def insert_identity(self, job_id, version, token, dev, ino, status='running',
                        started='2020-01-01T00:00:00+00:00'):
        """直接写一行任意身份（包括 SQLite 能存的畸形类型），不走产品认领路径。"""
        with self.intel.db() as connection:
            connection.execute(
                "INSERT INTO jobs(id,fakeid,name,mode,status,started_at,ownership_version,"
                "owner_token,owner_dev,owner_ino) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (job_id, BIZ, NAME, 'latest', status, started, version, token, dev, ino))

    def subscription_receipt(self):
        with self.intel.db() as connection:
            row = connection.execute('SELECT last_run_at,last_status FROM subscriptions WHERE fakeid=?',
                                     (BIZ,)).fetchone()
        return row['last_run_at'], row['last_status']

    def freeze_subscription(self):
        """把订阅回执改成哨兵值，便于断言「零变化」。"""
        with self.intel.db() as connection:
            connection.execute("UPDATE subscriptions SET last_run_at='SENTINEL',"
                               "last_status='sentinel' WHERE fakeid=?", (BIZ,))

    def acquire_free(self, token):
        """取锁后立刻释放：文件与身份都留在库里，但当前无人持有。"""
        handle = ownership.acquire(self.root, token)
        dev, ino = handle.dev, handle.ino
        handle.close()
        return dev, ino

    def spawn_holder(self, token):
        proc = subprocess.Popen(
            [PYTHON, '-c', HOLDER, str(self.root), token, str(self.intel.path)],
            cwd=REPO, env=dict(os.environ, PYTHONPATH=REPO, PYTHONDONTWRITEBYTECODE='1'),
            stdout=subprocess.PIPE, text=True)
        self.children.append(proc)
        line = read_line_bounded(proc, 10)
        if not line.strip():
            raise AssertionError('持锁子进程没有 ready')
        return proc, json.loads(line)

    def spawn_child(self, code, *args):
        """拉起一个带 ready/go/result 文件握手的子进程，登记到 children 供 finally 回收。"""
        proc = subprocess.Popen(
            [PYTHON, '-c', code, *args],
            cwd=REPO, env=dict(os.environ, PYTHONPATH=REPO, PYTHONDONTWRITEBYTECODE='1'),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.children.append(proc)
        return proc

    def run_blocked(self):
        """起一次手动抓取并等它进入合集请求（持锁运行中）。"""
        job = self.intel.run({'fakeid': BIZ, 'mode': 'latest', 'sogou': False})
        if not self.public.entered.wait(5):
            self.fail('任务没有进入合集请求')
        return job


class ClaimLifecycleTests(OwnershipTestCase):
    def test_claim_holds_same_fd_until_finish(self):
        captured = {}
        real = ownership.acquire

        def spy(root, token):
            handle = real(root, token)
            captured['handle'] = handle
            return handle

        with patch.object(ownership, 'acquire', spy):
            job = self.run_blocked()
        handle = captured['handle']
        # 运行中：同一 FD 持续持有，锁文件被占用；DB 里的身份就是这把锁的 dev/ino。
        with self.intel.db() as connection:
            row = connection.execute('SELECT owner_dev,owner_ino,owner_token FROM jobs WHERE id=?',
                                     (job['id'],)).fetchone()
        self.assertEqual((row['owner_dev'], row['owner_ino']), handle.identity)
        self.assertEqual(row['owner_token'], handle.token)
        state, _ = ownership.probe(self.root, handle.token, handle.dev, handle.ino)
        self.assertEqual(state, ownership.HELD)
        self.assertFalse(handle.closed)
        # 放行到 finish：worker 的 finally 关闭同一个 FD，锁释放。
        self.public.release.set()
        self.intel.wait_for(job['id'], timeout=5)
        self.assertTrue(handle.closed)
        state, free = ownership.probe(self.root, handle.token, handle.dev, handle.ino)
        self.assertEqual(state, ownership.ACQUIRED)
        free.close()
        self.assertEqual(self.intel.job(job['id'])['status'], 'done')

    def test_same_process_two_instances_claim_once(self):
        other = Intel(self.store, public_factory=lambda s: self.public,
                      discovery_factory=fake_discovery)
        other._client = _NullClient(other)
        first = self.run_blocked()
        try:
            answer = other.run({'fakeid': BIZ, 'mode': 'latest', 'sogou': False})
            self.assertTrue(answer.get('reused'))
            self.assertEqual(answer['id'], first['id'])
            self.assertEqual(len(self.intel.jobs(20)), 1)
        finally:
            self.public.release.set()
            self.intel.wait_for(first['id'], timeout=5)

    def test_thread_start_failure_releases_owner(self):
        captured = {}
        real = ownership.acquire

        def spy(root, token):
            handle = real(root, token)
            captured['handle'] = handle
            return handle

        original_start = threading.Thread.start

        def broken_start(worker):
            if str(getattr(worker, 'name', '')).startswith('intel-mp-'):
                raise RuntimeError('合成启动失败')
            return original_start(worker)

        with patch.object(ownership, 'acquire', spy), \
                patch('intel_store.threading.Thread.start', broken_start):
            with self.assertRaises(ValueError):
                self.intel.run({'fakeid': BIZ, 'mode': 'latest', 'sogou': False})
        handle = captured['handle']
        self.assertTrue(handle.closed, '启动失败必须释放 owner FD')
        # 行已 interrupted（收口成功）；即使收口失败，FD 也已释放、可恢复。
        self.assertEqual(self.intel.jobs(1)[0]['status'], 'interrupted')

    def test_thread_construction_failure_releases_owner(self):
        """L5：取锁后、交接前的任何异常（这里让 Thread 构造本身报错）都得关掉 handle。

        否则闸门 FD 泄漏到进程重启才恢复，之后本进程所有认领永远 OwnershipBusy。
        """
        def boom_init(*args, **kwargs):
            raise MemoryError('合成：线程构造失败')

        with patch('intel_store.threading.Thread.__init__', boom_init):
            with self.assertRaises(MemoryError):
                self.intel.run({'fakeid': BIZ, 'mode': 'latest', 'sogou': False})
        self.assertEqual(list(ownership._OPEN_HANDLES), [], '不得泄漏持锁 handle')
        # 闸门已释放：下一次认领能成功（不靠重启）。
        token = ownership.new_token()
        handle = ownership.acquire(self.root, token)
        handle.close()
        self.assertEqual(self.intel.jobs(1)[0]['ownership_state'], 'released')

    def test_scheduled_handoff_failure_releases_owner(self):
        """L5（定时路）：认领后交接前的辅助步骤抛异常，同样必须释放 handle。"""
        def boom():
            raise MemoryError('合成：交接失败')

        record = self.intel.subscriptions()[0]
        with patch.object(self.intel, '_release_settled_abandoned', boom):
            with self.assertRaises(MemoryError):
                self.intel._claim_scheduled_job(record)
        self.assertEqual(list(ownership._OPEN_HANDLES), [], '不得泄漏持锁 handle')
        token = ownership.new_token()
        handle = ownership.acquire(self.root, token)
        handle.close()

    def test_scheduled_finish_exception_releases_owner(self):
        captured = {}
        real = ownership.acquire

        def spy(root, token):
            handle = real(root, token)
            captured['handle'] = handle
            return handle

        def boom(*a, **k):
            raise sqlite3.OperationalError('数据库不可写')

        with patch.object(ownership, 'acquire', spy), patch.object(self.intel, '_finish_job', boom):
            with self.assertLogs('intel_store', level='ERROR'):
                receipt = self.intel.daily_latest(run_id='r' * 32)
        self.assertEqual(receipt['state'], 'error')
        self.assertTrue(captured['handle'].closed, '定时 finish 异常也必须释放 owner FD')
        self.assertEqual(self.intel.jobs(1)[0]['status'], 'running')
        # 已释放所有权：下次检查立即恢复，不必等 3600 秒。
        self.assertIsNone(self.intel.active_job())
        self.assertEqual(self.intel.jobs(1)[0]['status'], 'interrupted')


class RecoveryTests(OwnershipTestCase):
    def test_new_protocol_survives_age_when_locked(self):
        token = ownership.new_token()
        proc, info = self.spawn_holder(token)
        # 超龄（started_at 是 2020 年）但锁仍被持有：绝不按年龄回收。
        busy = self.intel.active_job()
        self.assertIsNotNone(busy)
        self.assertEqual(busy['id'], 'job')
        with self.intel.db() as connection:
            status = connection.execute("SELECT status FROM jobs WHERE id='job'").fetchone()[0]
        self.assertEqual(status, 'running', 'v1 持锁超龄不得被回收')
        proc.kill()
        proc.wait(timeout=5)

    def test_sigstop_owner_is_not_recovered(self):
        import signal
        token = ownership.new_token()
        proc, info = self.spawn_holder(token)
        try:
            os.kill(proc.pid, signal.SIGSTOP)
            self.assertIsNotNone(self.intel.active_job(), 'SIGSTOP 后仍持锁，不能恢复')
            with self.intel.db() as connection:
                status = connection.execute("SELECT status FROM jobs WHERE id='job'").fetchone()[0]
            self.assertEqual(status, 'running')
        finally:
            os.kill(proc.pid, signal.SIGCONT)
            proc.kill()
            proc.wait(timeout=5)

    def test_sigkill_owner_is_recovered_on_next_check(self):
        token = ownership.new_token()
        proc, info = self.spawn_holder(token)
        proc.kill()
        proc.wait(timeout=5)
        # 进程已死：身份匹配且锁可取 → 下次检查 CAS 标中断。
        self.assertIsNone(self.intel.active_job())
        row = self.intel.job('job')
        self.assertEqual(row['status'], 'interrupted')
        self.assertIn('所有权', row['reason'])
        self.assertTrue(row['outcome_available'])
        self.assertEqual(row['outcome']['list']['state'], 'unknown')

    def test_missing_lock_is_unknown(self):
        token = ownership.new_token()
        dev, ino = self.acquire_free(token)
        self.insert_v1('job', token, dev, ino)
        self.lock_path(token).unlink()
        self.assertIsNotNone(self.intel.active_job(), '锁文件缺失必须保持闸门（unknown）')
        self.assertEqual(self.intel.job('job')['status'], 'running')
        # 公共回执不得把「数据库 running」等同「已确认持锁」。
        self.assertEqual(self.intel.job('job')['ownership_state'], 'unknown')

    def test_symlink_lock_is_unknown(self):
        token = ownership.new_token()
        dev, ino = self.acquire_free(token)
        self.insert_v1('job', token, dev, ino)
        path = self.lock_path(token)
        path.unlink()
        target = self.root / 'elsewhere.lock'
        target.write_text('')
        path.symlink_to(target)
        self.assertIsNotNone(self.intel.active_job(), '符号链接必须判 unknown')
        self.assertEqual(self.intel.job('job')['status'], 'running')
        self.assertEqual(self.intel.job('job')['ownership_state'], 'unknown')

    def test_inode_replacement_is_unknown(self):
        token = ownership.new_token()
        dev, ino = self.acquire_free(token)
        self.insert_v1('job', token, dev, ino)
        path = self.lock_path(token)
        path.unlink()
        path.write_text('')  # 重建：新 inode，与库里原身份不符
        self.assertIsNotNone(self.intel.active_job(), 'inode 被替换必须判 unknown')
        self.assertEqual(self.intel.job('job')['status'], 'running')
        self.assertEqual(self.intel.job('job')['ownership_state'], 'unknown')

    def test_two_recoverers_one_cas_winner(self):
        token = ownership.new_token()
        dev, ino = self.acquire_free(token)
        self.insert_v1('job', token, dev, ino)
        # 恢复者 1：取锁成功 → CAS 成功。
        state, handle = ownership.probe(self.root, token, dev, ino)
        self.assertEqual(state, ownership.ACQUIRED)
        self.intel._cas_recover({'id': 'job', 'fakeid': BIZ, 'owner_token': token,
                                 'owner_dev': dev, 'owner_ino': ino})
        handle.close()
        self.assertEqual(self.intel.job('job')['status'], 'interrupted')
        # 恢复者 2：晚取到锁，CAS 应为 0，不重复收口。
        state2, handle2 = ownership.probe(self.root, token, dev, ino)
        self.assertEqual(state2, ownership.ACQUIRED)
        self.intel._cas_recover({'id': 'job', 'fakeid': BIZ, 'owner_token': token,
                                 'owner_dev': dev, 'owner_ino': ino})
        handle2.close()
        self.assertEqual(self.intel.job('job')['status'], 'interrupted')

    def test_database_busy_is_bounded_and_releases_recovery_fd(self):
        token = ownership.new_token()
        dev, ino = self.acquire_free(token)
        self.insert_v1('job', token, dev, ino)
        blocker = sqlite3.connect(self.intel.path, timeout=1)
        try:
            blocker.execute('BEGIN IMMEDIATE')
            started = time.monotonic()
            self.intel._recover_owned_jobs()  # 不得抛异常
            elapsed = time.monotonic() - started
        finally:
            blocker.rollback()
            blocker.close()
        self.assertLess(elapsed, 5, '恢复必须有界，不无限等 DB')
        # 锁 FD 必须已在 finally 释放：现在能取到锁。
        state, handle = ownership.probe(self.root, token, dev, ino)
        self.assertEqual(state, ownership.ACQUIRED)
        handle.close()
        # 未写回：行仍是 running，等下次可写时再恢复。
        self.assertEqual(self.intel.job('job')['status'], 'running')


class LateOwnerTests(OwnershipTestCase):
    def test_wrong_token_cannot_finish_v1_row(self):
        token = ownership.new_token()
        dev, ino = self.acquire_free(token)
        self.insert_v1('job', token, dev, ino)
        before = self.intel.subscriptions()[0]['last_run_at']
        self.intel._finish_job('job', 'done', '迟到的 owner', owner='f' * 32,
                               outcome=intel_store.recovered_outcome())
        row = self.intel.job('job')
        self.assertEqual(row['status'], 'running', '错误 token 不得收口')
        self.assertIsNone(row['outcome'])
        self.assertEqual(self.intel.subscriptions()[0]['last_run_at'], before,
                         'CAS=0 不得同步旧 owner 统计/订阅时间')

    def test_interrupted_row_rejects_late_owner_outcome(self):
        token = ownership.new_token()
        dev, ino = self.acquire_free(token)
        self.insert_v1('job', token, dev, ino)
        self.intel._finish_job('job', 'interrupted', '恢复收口', owner=token)
        self.intel._finish_job('job', 'done', '迟到 owner', owner=token,
                               outcome=intel_store.recovered_outcome())
        row = self.intel.job('job')
        self.assertEqual(row['status'], 'interrupted')
        self.assertIsNone(row['outcome'], '已中断的行不得被迟到 owner 补上 outcome')

    def test_detail_write_is_owner_guarded(self):
        token = ownership.new_token()
        dev, ino = self.acquire_free(token)
        self.insert_v1('job', token, dev, ino)
        self.intel._write_job_detail('job', '错 token 的详情', owner='f' * 32)
        self.assertEqual(self.intel.job('job')['detail'], '')
        self.intel._write_job_detail('job', '对 token 的详情', owner=token)
        self.assertEqual(self.intel.job('job')['detail'], '对 token 的详情')

    def test_late_owner_cannot_write_articles_or_albums(self):
        token = ownership.new_token()
        dev, ino = self.acquire_free(token)
        self.insert_v1('job', token, dev, ino)
        self.intel._cas_recover({'id': 'job', 'fakeid': BIZ, 'owner_token': token,
                                 'owner_dev': dev, 'owner_ino': ino})
        self.assertEqual(self.intel.job('job')['status'], 'interrupted')
        with self.assertRaises(intel_store.StaleOwnershipError):
            self.intel._save_article(BIZ, {'title': '迟到文章',
                                           'link': 'https://mp.weixin.qq.com/s/LATE00001',
                                           'published_at': 1, 'aid': 'L1'}, 'job', token)
        self.assertEqual(self.intel.articles(BIZ)['total'], 0, '迟到 owner 不得写文章')
        with self.assertRaises(intel_store.StaleOwnershipError):
            self.intel.remember_albums(BIZ, ['9999999999999999'],
                                       guard=self.intel._owner_guard('job', token))
        self.assertEqual([album['album_id'] for album in self.intel.albums(BIZ)],
                         ['1234567890123456'], '迟到 owner 不得登记合集')

    def test_late_owner_cannot_write_body(self):
        token = ownership.new_token()
        dev, ino = self.acquire_free(token)
        self.insert_v1('job', token, dev, ino)
        self.intel._cas_recover({'id': 'job', 'fakeid': BIZ, 'owner_token': token,
                                 'owner_dev': dev, 'owner_ino': ino})
        with self.intel.db() as connection:
            connection.execute(
                "INSERT INTO articles(id,fakeid,aid,link,title,published_at,body_status,added_at,job_id) "
                "VALUES('a1',?,?,?,?,?,?,?,?)",
                (BIZ, 'A1', 'https://mp.weixin.qq.com/s/BODY00001', '标题', 1, 'pending',
                 intel_store.now(), 'job'))
        with self.assertRaises(intel_store.StaleOwnershipError):
            self.intel.save_body('a1', {'text': '正文', 'chars': 2, 'images': [], 'nickname': ''},
                                 guard=self.intel._owner_guard('job', token))
        self.assertEqual(self.intel.article('a1')['body_status'], 'pending')

    def test_active_owner_can_still_write(self):
        token = ownership.new_token()
        handle = ownership.acquire(self.root, token)
        try:
            self.insert_v1('job', token, handle.dev, handle.ino)
            _, created = self.intel._save_article(
                BIZ, {'title': '正常文章', 'link': 'https://mp.weixin.qq.com/s/OK0000001',
                      'published_at': 2, 'aid': 'OK1'}, 'job', token)
            self.assertTrue(created)
            self.assertEqual(self.intel.articles(BIZ)['total'], 1)
        finally:
            handle.close()

    # ── L3-M3：订阅回读必须跟确实匹配的 owner CAS 走 ──

    def test_missing_owner_cannot_touch_v1_subscription(self):
        """漏传 owner（CAS=0）：v1 行不收口，订阅回执与时间也不得改写。"""
        token = ownership.new_token()
        handle = ownership.acquire(self.root, token)
        try:
            self.insert_v1('held', token, handle.dev, handle.ino)
            self.freeze_subscription()
            self.intel._finish_job('held', 'done', '漏传 owner 的迟到收口',
                                   outcome=intel_store.recovered_outcome(), added=7)
            row = self.intel.job('held')
            self.assertEqual(row['status'], 'running', '漏 token 不得收口 v1 行')
            self.assertEqual(row['added'], 0, 'CAS=0 不得写计数')
            self.assertIsNone(row['outcome'], 'CAS=0 不得写 outcome')
            self.assertEqual(self.subscription_receipt(), ('SENTINEL', 'sentinel'),
                             'CAS=0 不得同步订阅时间/状态')
        finally:
            handle.close()

    def test_wrong_token_cannot_touch_subscription_counts_or_outcome(self):
        """错 token：job/outcome/counts/订阅四项零变化。"""
        token = ownership.new_token()
        dev, ino = self.acquire_free(token)
        self.insert_v1('job', token, dev, ino)
        self.freeze_subscription()
        self.intel._finish_job('job', 'done', '错 token', owner='f' * 32,
                               outcome=intel_store.recovered_outcome(), added=9, found=3)
        row = self.intel.job('job')
        self.assertEqual((row['status'], row['added'], row['found'], row['outcome']),
                         ('running', 0, 0, None))
        self.assertEqual(self.subscription_receipt(), ('SENTINEL', 'sentinel'))

    def test_interrupted_row_keeps_subscription_untouched_by_late_owner(self):
        """已 interrupted 的行被迟到 owner 再收口：订阅不得被刷成当前时刻。"""
        token = ownership.new_token()
        dev, ino = self.acquire_free(token)
        self.insert_v1('job', token, dev, ino)
        self.intel._finish_job('job', 'interrupted', '恢复收口', owner=token)
        self.freeze_subscription()
        self.intel._finish_job('job', 'done', '迟到 owner', owner=token,
                               outcome=intel_store.recovered_outcome())
        self.assertEqual(self.intel.job('job')['status'], 'interrupted')
        self.assertEqual(self.subscription_receipt(), ('SENTINEL', 'sentinel'),
                         '未命中的收口不得刷新订阅回执')

    def test_legacy_finish_cannot_touch_unknown_identity_subscription(self):
        """v0 混入 owner（unknown）：缺 token 的 legacy 收口不得写行、也不得同步订阅。"""
        self.insert_identity('v0mix', 0, 'deadbeef', 7, 8)
        self.freeze_subscription()
        self.intel._finish_job('v0mix', 'done', 'legacy 收口')
        self.assertEqual(self.intel.job('v0mix')['status'], 'running',
                         '缺 token 的 legacy 路径不得改写 unknown 身份行')
        self.assertEqual(self.subscription_receipt(), ('SENTINEL', 'sentinel'))

    def test_genuine_legacy_finish_still_syncs_subscription(self):
        """对照组：严格 legacy 行漏传 owner 仍按旧语义收口并同步订阅。"""
        started = intel_store.now()
        self.insert_identity('leg', 0, '', 0, 0, started=started)
        self.freeze_subscription()
        self.intel._finish_job('leg', 'done', 'legacy 收口')
        self.assertEqual(self.intel.job('leg')['status'], 'done')
        run_at, status = self.subscription_receipt()
        self.assertEqual(status, 'done', '严格 legacy 的回读兼容必须保留')
        self.assertNotEqual(run_at, 'SENTINEL')


class InheritanceTests(OwnershipTestCase):
    def test_exec_cannot_inherit_owner_lock(self):
        handle = ownership.acquire(self.root, ownership.new_token())
        try:
            self.assertFalse(os.get_inheritable(handle.fd), 'CLOEXEC/set_inheritable(False) 必须生效')
            self.assertFalse(attempt_lock(handle.path), '父进程持锁时子进程取锁应 busy')
        finally:
            handle.close()
        self.assertTrue(attempt_lock(handle.path), '父进程释放后子进程应能取锁')

    def test_fork_child_closes_without_unlocking_parent(self):
        handle = ownership.acquire(self.root, ownership.new_token())
        try:
            pid = os.fork()
            if pid == 0:  # 子进程：at_fork 处理器只 close 继承 FD，不 LOCK_UN。
                os._exit(0)
            try:
                wait_pid_bounded(pid, 5)
            except BaseException:
                try:
                    os.kill(pid, signal.SIGKILL)
                    os.waitpid(pid, 0)
                except OSError:
                    pass
                raise
            self.assertFalse(attempt_lock(handle.path),
                             '子进程 close 继承 FD 后父进程锁仍在（若 LOCK_UN 会误放行）')
        finally:
            handle.close()
        self.assertTrue(attempt_lock(handle.path))


class CleanupGateTests(OwnershipTestCase):
    """M1：终态已写、旧 FD 仍持锁的清理期，跨进程不得认领（闸门保持到同 FD 关闭）。"""

    def test_cleanup_window_blocks_cross_process_claim(self):
        entered = threading.Event()
        release = threading.Event()
        real = self.intel._write_job_detail

        def blocked(job_id, detail, owner=None):
            entered.set()
            release.wait(10)
            return real(job_id, detail, owner=owner)

        with patch.object(self.intel, '_write_job_detail', blocked):
            first = self.intel.run({'fakeid': BIZ, 'mode': 'latest', 'sogou': False})
            self.public.entered.wait(5)
            self.public.release.set()
            self.assertTrue(entered.wait(5), '任务应停在终态提交后的辅助详情补写')
            # 终态已提交、worker 仍持同一 FD：数据库不再是闸门，跨进程闸门必须仍然拦住。
            self.assertEqual(self.intel.job(first['id'])['status'], 'done')
            ready, go, result = self.handshake_paths('cleanup')
            proc = self.spawn_child(CLAIMER, self.tmp.name, BIZ, ready, go, result, '')
            try:
                wait_for_file(ready, 10)
                Path(go).write_text('1')
                wait_for_file(result, 15)
                wait_for_processes([proc], 15)
                self.assertFalse(json.loads(Path(result).read_text())['claimed'],
                                 '清理期第二个进程必须被闸门拒绝')
            finally:
                release.set()
                if proc.poll() is None:
                    proc.kill()
                    proc.wait(timeout=5)
            self.intel.wait_for(first['id'], 5)

        # 清理完成、FD 关闭：闸门释放，新的认领可以拿到。
        ready2, go2, result2 = self.handshake_paths('cleanup2')
        proc2 = self.spawn_child(CLAIMER, self.tmp.name, BIZ, ready2, go2, result2, '')
        try:
            wait_for_file(ready2, 10)
            Path(go2).write_text('1')
            wait_for_file(result2, 15)
            wait_for_processes([proc2], 15)
            self.assertTrue(json.loads(Path(result2).read_text())['claimed'],
                            '释放后第二个进程应能认领')
        finally:
            if proc2.poll() is None:
                proc2.kill()
                proc2.wait(timeout=5)


class IdentityClassificationTests(OwnershipTestCase):
    """M2：残缺/未知身份安全归类 unknown，恢复不抛异常，公共回执真实。"""

    def test_malformed_dev_does_not_abort_recovery(self):
        token = ownership.new_token()
        dev, ino = self.acquire_free(token)
        self.insert_v1('bad', token, 'bad-dev', ino)
        token2 = ownership.new_token()
        dev2, ino2 = self.acquire_free(token2)
        self.insert_v1('good', token2, dev2, ino2, started='2000-01-01T00:00:00+00:00')
        self.intel._recover_owned_jobs()  # 绝不抛异常
        self.assertEqual(self.intel.job('bad')['status'], 'running', '残缺身份保持闸门')
        self.assertEqual(self.intel.job('bad')['ownership_state'], 'unknown')
        self.assertEqual(self.intel.job('good')['status'], 'interrupted',
                         '单条坏数据不得中断后续恢复')

    def test_missing_token_is_unknown(self):
        token = ownership.new_token()
        dev, ino = self.acquire_free(token)
        self.insert_v1('job', '', dev, ino)
        self.assertEqual(self.intel.job('job')['ownership_state'], 'unknown')
        self.assertIsNotNone(self.intel.active_job(), '缺 token 保持闸门')

    def test_malformed_ino_is_unknown(self):
        token = ownership.new_token()
        dev, ino = self.acquire_free(token)
        self.insert_v1('job', token, dev, 'not-a-number')
        self.assertEqual(self.intel.job('job')['ownership_state'], 'unknown')

    def test_v0_with_owner_is_unknown_not_legacy(self):
        with self.intel.db() as connection:
            connection.execute(
                "INSERT INTO jobs(id,fakeid,name,mode,status,started_at,ownership_version,owner_token) "
                "VALUES('v0x',?,?,'latest','running',?,0,'deadbeef')", (BIZ, NAME, intel_store.now()))
        row = self.intel.job('v0x')
        self.assertEqual(row['ownership_state'], 'unknown', 'v0 混入 owner 不得走 legacy')
        self.assertIsNotNone(self.intel.active_job(), 'v0 混入 owner 保持闸门')

    def test_public_state_reflects_live_lock(self):
        # 真正持锁的 v1 running 行投影 held；释放后投影 released。
        token = ownership.new_token()
        handle = ownership.acquire(self.root, token)
        try:
            self.insert_v1('job', token, handle.dev, handle.ino)
            self.assertEqual(self.intel.job('job')['ownership_state'], 'held')
        finally:
            handle.close()
        self.assertEqual(self.intel.job('job')['ownership_state'], 'released')

    # ── L3-M2：坥形类型不得让公开路径抛异常，非法 version 不得伪装 legacy ──

    MALFORMED = (
        (1, b'BLOB', 1, 1),            # owner_token 存成 BLOB
        (1, 'a' * 32, float('inf'), 1),  # owner_dev 存成 REAL 正无穷
        (1, 'a' * 32, b'x', 1),        # owner_dev 存成 BLOB
        (1, 'a' * 32, -1, 1),          # owner_dev 为负
        ('bad-version', '', 0, 0),     # 非法 version 文本，owner 三列全空
        (b'v1', '', 0, 0),             # 非法 version BLOB
        (1.5, '', 0, 0),               # 非整数 version
    )

    def test_malformed_identity_types_keep_public_paths_safe(self):
        """公开 job / 订阅列表 / 恢复三条路径对坥形身份一律 unknown，绝不抛异常。"""
        for index, case in enumerate(self.MALFORMED):
            self.insert_identity(f'bad{index}', *case)
        rows = self.intel.jobs(50)  # 一份坏数据不得让整表投影抛
        self.assertEqual(len(rows), len(self.MALFORMED))
        for index in range(len(self.MALFORMED)):
            row = self.intel.job(f'bad{index}')
            self.assertEqual(row['ownership_state'], 'unknown', f'bad{index} 应为 unknown')
            self.assertNotIn('owner_token', row)
            self.assertNotIn('owner_dev', row)
            self.assertNotIn('owner_ino', row)
        subs = self.intel.subscriptions()  # 订阅列表复用同一投影
        self.assertEqual([s['last_result']['ownership_state'] for s in subs], ['unknown'],
                         '订阅列表的最近结果也得是 unknown')
        self.intel._recover_owned_jobs()  # 不抛

    def test_illegal_version_is_not_legacy_and_keeps_gate(self):
        """非法 version 不得先被降级成 0 再当 legacy：既不能显示 legacy，也不能被年龄清理。"""
        self.insert_identity('badver', 'bad-version', '', 0, 0)
        self.insert_identity('blobver', b'v1', '', 0, 0)
        for job_id in ('badver', 'blobver'):
            row = self.intel.job(job_id)
            self.assertEqual(row['ownership_state'], 'unknown', f'{job_id} 不得伪装 legacy')
        self.assertIsNotNone(self.intel.active_job(), '非法 version 保持闸门')
        for job_id in ('badver', 'blobver'):
            self.assertEqual(self.intel.job(job_id)['status'], 'running', f'{job_id} 不得按年龄回收')

    def test_classify_and_probe_never_raise_on_any_type(self):
        """classify/probe 对任意类型都只归类，不抛异常。"""
        for value in (None, b'x', float('inf'), float('nan'), 1.5, 'x', [], object(), 0, ''):
            kind = ownership.classify(value, value, value, value)
            self.assertIn(kind, (ownership.LEGACY, ownership.V1, ownership.UNKNOWN))
            self.assertIsInstance(ownership.safe_version(value), int)
        for token, dev, ino in ((b'BLOB', 1, 1), ('a' * 32, float('inf'), 1), ('a' * 32, b'x', 1),
                                ('', 0, 0), (None, None, None), ('a' * 32, 1.5, 1)):
            state, handle = ownership.probe(self.root, token, dev, ino)
            self.assertEqual(state, ownership.UNKNOWN)
            self.assertIsNone(handle)

    def test_strict_int_parsing_rejects_non_integers(self):
        """_as_int 不能把无限/非整数/BLOB 当成整数（否则 int() 会抛 OverflowError）。"""
        for bad in (float('inf'), float('-inf'), float('nan'), b'7', '7.5', 'bad', None, object()):
            self.assertIsNone(ownership._as_int(bad), f'{bad!r} 不是完整整数')
        self.assertEqual(ownership._as_int('7'), 7)
        self.assertEqual(ownership._as_int(7.0), 7)


class StrictLegacyIdentityTests(OwnershipTestCase):
    """L3-M1：投影说是 unknown 的行，年龄清理/短宽限/闸门必须用同一分类。"""

    def test_v0_with_owner_survives_long_grace(self):
        """v0 混入 owner：投影 unknown，长宽限（3600s）也不能把它清掉。"""
        token = ownership.new_token()
        dev, ino = self.acquire_free(token)
        self.insert_identity('v0mix', 0, token, dev, ino)  # started_at 是 2020 年
        self.assertEqual(self.intel.job('v0mix')['ownership_state'], 'unknown')
        self.assertIsNotNone(self.intel.active_job(), '身份未知行不得按年龄清理')
        self.assertEqual(self.intel.job('v0mix')['status'], 'running')

    def test_v0_with_owner_survives_short_grace(self):
        """v0 混入 owner：即使被登记为 abandoned，60 秒短宽限也不得放行。"""
        token = ownership.new_token()
        dev, ino = self.acquire_free(token)
        started = (datetime.now(timezone.utc) - timedelta(seconds=ABANDONED_JOB_SECONDS + 120)).isoformat()
        self.insert_identity('v0mix', 0, token, dev, ino, started=started)
        key = (str(self.intel.path.resolve()), 'v0mix')
        intel_store._PROCESS_ABANDONED.add(key)
        try:
            self.assertIsNotNone(self.intel.active_job(), '短宽限不得放行身份未知行')
            self.assertEqual(self.intel.job('v0mix')['status'], 'running')
        finally:
            intel_store._PROCESS_ABANDONED.discard(key)

    def test_v0_with_owner_blocks_new_claim(self):
        """闸门真实生效：未知行不会被新手动认领绕过，返回的是现有任务 reused。"""
        token = ownership.new_token()
        dev, ino = self.acquire_free(token)
        self.insert_identity('v0mix', 0, token, dev, ino)
        job = self.intel.run({'fakeid': BIZ, 'mode': 'latest', 'sogou': False})
        self.assertTrue(job.get('reused'), '未知身份行必须保持闸门')
        self.assertEqual(job['id'], 'v0mix')
        with self.intel.db() as connection:
            active = connection.execute(
                "SELECT count(*) FROM jobs WHERE status IN ('queued','running')").fetchone()[0]
        self.assertEqual(active, 1, '不得新建第二个任务')

    def test_genuine_legacy_still_uses_long_and_short_grace(self):
        """对照组：严格 legacy 行的 3600/60 旧语义不变。"""
        self.insert_identity('old', 0, '', 0, 0)  # 2020 年 → 超 3600s
        self.assertIsNone(self.intel.active_job())
        self.assertEqual(self.intel.job('old')['status'], 'interrupted')
        short = (datetime.now(timezone.utc) - timedelta(seconds=ABANDONED_JOB_SECONDS + 120)).isoformat()
        self.insert_identity('recent', 0, '', 0, 0, started=short)
        key = (str(self.intel.path.resolve()), 'recent')
        intel_store._PROCESS_ABANDONED.add(key)
        try:
            self.intel.active_job()
            self.assertEqual(self.intel.job('recent')['status'], 'interrupted',
                             '严格 legacy 的 abandoned 短宽限仍生效')
        finally:
            intel_store._PROCESS_ABANDONED.discard(key)

    def test_sql_legacy_predicate_matches_classify(self):
        """SQL 谓词与 classify 必须是同一分类：逐元组对照真实表列（带列亲和性）。"""
        token = ownership.new_token()
        cases = (
            (0, '', 0, 0), (0, '', 0, 5), (0, '', 7, 0), (0, 'deadbeef', 0, 0),
            (0, 'deadbeef', 7, 8), (0, token, 1, 2), (1, token, 7, 8), (1, '', 0, 0),
            (2, token, 7, 8), ('bad-version', '', 0, 0), (b'v1', '', 0, 0), (1.0, '', 0, 0),
            (0, b'', 0, 0), (0, '', float('inf'), 0), (0, '', 1.5, 0), (0, '', -1, 0),
            (0, '0', 0, 0), (0, '', '0', '0'), (0, None, 0, 0), (None, '', 0, 0),
        )
        for index, (version, owner_token, dev, ino) in enumerate(cases):
            job_id = f'x{index}'
            try:
                self.insert_identity(job_id, version, owner_token, dev, ino)
            except sqlite3.IntegrityError:
                continue  # NOT NULL 列存不下的写法（如 version=None）跳过
            with self.intel.db() as connection:
                hit = connection.execute(
                    'SELECT 1 FROM jobs WHERE id=? AND ' + ownership.LEGACY_WHERE,
                    (job_id,)).fetchone() is not None
            self.assertEqual(
                hit, ownership.classify(version, owner_token, dev, ino) == ownership.LEGACY,
                f'{job_id} 的 SQL 谓词与 classify 对 {version!r},{owner_token!r},{dev!r},{ino!r} 不一致')


class ForkLockTests(OwnershipTestCase):
    """M3：fork 子进程必须重建继承的 registry 锁，之后仍能正常 acquire/close。"""

    def _hold_handles_lock_in_thread(self):
        locked = threading.Event()
        release = threading.Event()

        def holder():
            ownership._HANDLES_LOCK.acquire()
            locked.set()
            release.wait(10)
            ownership._HANDLES_LOCK.release()

        thread = threading.Thread(target=holder, daemon=True)
        thread.start()
        self.assertTrue(locked.wait(5))
        return thread, release

    def test_fork_child_can_still_use_lock_module(self):
        thread, release = self._hold_handles_lock_in_thread()
        try:
            pid = os.fork()
            if pid == 0:
                signal.alarm(5)
                try:
                    handle = ownership.acquire(self.root, ownership.new_token())
                    handle.close()
                    os._exit(0)
                except BaseException:  # noqa: BLE001
                    os._exit(3)
            try:
                status = wait_pid_bounded(pid, 8)
            except BaseException:
                try:
                    os.kill(pid, signal.SIGKILL)
                    os.waitpid(pid, 0)
                except OSError:
                    pass
                raise
            self.assertEqual(os.waitstatus_to_exitcode(status), 0,
                             'fork 子进程必须能继续用锁模块，不得死等继承的锁')
        finally:
            release.set()
            thread.join(5)


class CrossProcessTests(OwnershipTestCase):
    """M5：真两进程 ready + 共同放行，验证一次认领、一次 CAS。"""

    def test_two_process_claim_is_serialized(self):
        ready_a, go_a, res_a = self.handshake_paths('a')
        release_a = str(Path(self.tmp.name) / 'hs-a.release')
        proc_a = self.spawn_child(CLAIMER, self.tmp.name, BIZ, ready_a, go_a, res_a, release_a)
        try:
            wait_for_file(ready_a, 10)
            Path(go_a).write_text('1')
            wait_for_file(res_a, 15)
            self.assertTrue(json.loads(Path(res_a).read_text())['claimed'], 'A 应拿到闸门')
            # A 仍持有（等 release 文件）：B 必须拿不到。
            ready_b, go_b, res_b = self.handshake_paths('b')
            proc_b = self.spawn_child(CLAIMER, self.tmp.name, BIZ, ready_b, go_b, res_b, '')
            try:
                wait_for_file(ready_b, 10)
                Path(go_b).write_text('1')
                wait_for_file(res_b, 15)
                wait_for_processes([proc_b], 15)
                self.assertFalse(json.loads(Path(res_b).read_text())['claimed'],
                                 'A 持闸门时 B 不得认领')
            finally:
                if proc_b.poll() is None:
                    proc_b.kill()
                    proc_b.wait(timeout=5)
        finally:
            Path(release_a).write_text('1')
            wait_for_processes([proc_a], 15)
        # A 释放后再认领应成功。
        ready_c, go_c, res_c = self.handshake_paths('c')
        proc_c = self.spawn_child(CLAIMER, self.tmp.name, BIZ, ready_c, go_c, res_c, '')
        try:
            wait_for_file(ready_c, 10)
            Path(go_c).write_text('1')
            wait_for_file(res_c, 15)
            wait_for_processes([proc_c], 15)
            self.assertTrue(json.loads(Path(res_c).read_text())['claimed'], '释放后应能认领')
        finally:
            if proc_c.poll() is None:
                proc_c.kill()
                proc_c.wait(timeout=5)

    def test_two_process_recoverers_one_cas(self):
        token = ownership.new_token()
        dev, ino = self.acquire_free(token)
        self.insert_v1('job', token, dev, ino)
        paths = [self.handshake_paths(f'r{i}') for i in range(2)]
        procs = [self.spawn_child(RECOVERER, self.tmp.name, r, g, res)
                 for r, g, res in paths]
        try:
            for ready, go, res in paths:
                wait_for_file(ready, 10)
            for ready, go, res in paths:
                Path(go).write_text('1')
            for ready, go, res in paths:
                wait_for_file(res, 15)
            wait_for_processes(procs, 15)
        finally:
            for proc in procs:
                if proc.poll() is None:
                    proc.kill()
                    proc.wait(timeout=5)
        wins = [json.loads(Path(res).read_text())['won'] for ready, go, res in paths]
        self.assertEqual(sum(wins), 1, '两个恢复者只能有一个 CAS 抢到')
        self.assertEqual(self.intel.job('job')['status'], 'interrupted')
        self.assertEqual(self.intel.subscriptions()[0]['last_status'], 'interrupted')


class RollbackTests(unittest.TestCase):
    """M4：可执行回滚包——迁移后回到 ce87 旧代码的安全路径。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(self.tmp.name)
        self.path = Path(self.tmp.name) / 'intel' / 'intel.db'
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        self.tmp.cleanup()

    def columns(self):
        with sqlite3.connect(self.path) as connection:
            return {row[1] for row in connection.execute('PRAGMA table_info(jobs)')}

    def seed_migrated_v1(self, status='done'):
        intel = Intel(self.store)
        intel._ensure()
        intel.subscribe({'fakeid': BIZ, 'name': NAME})
        token = ownership.new_token()
        handle = ownership.acquire(ownership.normalize_root(intel.workspace), token)
        dev, ino = handle.dev, handle.ino
        handle.close()
        with intel.db() as connection:
            connection.execute(
                "INSERT INTO jobs(id,fakeid,name,mode,status,reason,detail,started_at,finished_at,"
                "found,added,ownership_version,owner_token,owner_dev,owner_ino) "
                "VALUES('j1',?,?,'latest',?,?,?,?,?,5,2,1,?,?,?)",
                (BIZ, NAME, status, '正常收口', 'keep detail', '2026-10-11T00:00:00+00:00',
                 '2026-10-11T00:01:00+00:00', token, dev, ino))
            connection.execute(
                "INSERT INTO articles(id,fakeid,aid,link,title,published_at,added_at) "
                "VALUES('a1',?,?,?,?,?,?)",
                (BIZ, 'A1', 'https://mp.weixin.qq.com/s/KEEP00001', '保留文章', 1,
                 '2026-10-11T00:00:30+00:00'))
        return intel

    def run_rollback(self, *extra):
        proc = subprocess.run([PYTHON, ROLLBACK_SCRIPT, str(self.path), *extra],
                              capture_output=True, text=True, timeout=30)
        return proc

    def load_rollback_module(self):
        """把回滚脚本当模块加载，便于强制走 DROP COLUMN 不可用的重建分支。"""
        spec = importlib.util.spec_from_file_location('intel_lease_b_rollback', ROLLBACK_SCRIPT)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_rollback_drops_owner_columns_and_keeps_business_data(self):
        self.seed_migrated_v1(status='done')
        proc = self.run_rollback()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        for column in ('ownership_version', 'owner_token', 'owner_dev', 'owner_ino'):
            self.assertNotIn(column, self.columns())
        with sqlite3.connect(self.path) as connection:
            connection.row_factory = sqlite3.Row
            job = dict(connection.execute("SELECT * FROM jobs WHERE id='j1'").fetchone())
            articles = connection.execute('SELECT COUNT(*) FROM articles').fetchone()[0]
        self.assertEqual(job['status'], 'done')
        self.assertEqual(job['detail'], 'keep detail')
        self.assertEqual(job['found'], 5)
        self.assertEqual(job['added'], 2)
        self.assertEqual(articles, 1)

    def test_rollback_makes_ce87_safe(self):
        self.seed_migrated_v1(status='done')
        proc = self.run_rollback()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        old = load_old_intel()
        with sqlite3.connect(self.path) as connection:
            connection.row_factory = sqlite3.Row
            row = dict(connection.execute("SELECT * FROM jobs WHERE id='j1'").fetchone())
        # ce87 的 _public_job 用 SELECT * 组 dict：回滚后库结构无 owner 列，不得泄露。
        public = old.Intel._public_job(row)
        self.assertNotIn('owner_token', public)
        self.assertNotIn('owner_dev', public)
        self.assertNotIn('owner_ino', public)
        # ce87 的 interrupt_stale_jobs 不看 version：回滚后没有 v1 行，且没有活跃行可被年龄误回收。
        with sqlite3.connect(self.path) as connection:
            connection.row_factory = sqlite3.Row
            old.interrupt_stale_jobs(connection, 'rollback age')
            active = connection.execute(
                "SELECT COUNT(*) FROM jobs WHERE status IN ('queued','running')").fetchone()[0]
            status = connection.execute("SELECT status FROM jobs WHERE id='j1'").fetchone()[0]
        self.assertEqual(active, 0)
        self.assertEqual(status, 'done', '回滚后旧代码不得按年龄误回收')

    def test_rollback_refuses_while_v1_task_active(self):
        self.seed_migrated_v1(status='running')
        proc = self.run_rollback()
        self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
        self.assertIn('ownership_version', self.columns(), '拒绝执行时不得改动 schema')
        with sqlite3.connect(self.path) as connection:
            version = connection.execute("SELECT ownership_version FROM jobs WHERE id='j1'").fetchone()[0]
        self.assertEqual(version, 1, '拒绝执行时不得降级')

    def test_rollback_is_idempotent(self):
        self.seed_migrated_v1(status='done')
        first = self.run_rollback()
        self.assertEqual(first.returncode, 0, first.stderr)
        second = self.run_rollback()
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertIn('无需处理', second.stdout)

    def test_rollback_backup_is_consistent_snapshot(self):
        """L3：备份走 sqlite3 在线备份 API，不是对活库拍文件（copy2）。"""
        self.seed_migrated_v1(status='done')
        proc = self.run_rollback()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        backups = sorted(self.path.parent.glob('intel.db.rollback-*.bak'))
        self.assertEqual(len(backups), 1, f'应有一份备份：{proc.stdout}')
        with sqlite3.connect(backups[0]) as connection:
            # 备份必须是回滚前的完整库：所有权列仍在、业务行可读。
            self.assertIn('owner_token', {row[1] for row in connection.execute('PRAGMA table_info(jobs)')})
            self.assertEqual(connection.execute("SELECT detail FROM jobs WHERE id='j1'").fetchone()[0],
                             'keep detail')
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM articles').fetchone()[0], 1)
            self.assertEqual(connection.execute('PRAGMA integrity_check').fetchone()[0], 'ok')

    def test_rollback_rebuild_refuses_columns_added_after_ce87(self):
        """L1：旧 SQLite 重建分支遇到 ce87 之后新增的列时明确拒绝，不半改 schema。

        B 适配：B 自己就在 jobs 上新增了 discovery 列（ce87 之后），所以这个库**本来就带**
        一个回滚脚本不认识的列，无需再 ALTER 模拟——拒绝路径由 B 的 discovery 直接触发。
        """
        self.seed_migrated_v1(status='done')
        module = self.load_rollback_module()
        with patch.object(module, '_has_drop_column', lambda: False):
            code = module.rollback(str(self.path), backup=False)
        self.assertEqual(code, 1, '遇到新增列必须报错退出，不能猜着丢列')
        self.assertIn('ownership_version', self.columns(), '拒绝时不得改动 schema')
        self.assertIn('discovery', self.columns())
        with sqlite3.connect(self.path) as connection:
            self.assertEqual(
                connection.execute("SELECT detail,found,added,status FROM jobs WHERE id='j1'").fetchone(),
                ('keep detail', 5, 2, 'done'), '拒绝时不得丢业务数据')

    def test_rollback_rebuild_branch_still_works_on_ce87_schema(self):
        """L1 对照组：无新增列时，强制走重建分支仍能回到 ce87 的 15 列。

        B 适配：B 给 jobs 加了 discovery 列，先把它去掉（测试环境的 SQLite 支持 DROP COLUMN），
        还原「无 ce87 之后新增列」的前提，再强制走重建分支。
        """
        self.seed_migrated_v1(status='done')
        with sqlite3.connect(self.path) as connection:
            connection.execute('ALTER TABLE jobs DROP COLUMN discovery')
        self.assertNotIn('discovery', self.columns(), '前置：应已还原成无 discovery 的 ce87 形态')
        module = self.load_rollback_module()
        with patch.object(module, '_has_drop_column', lambda: False):
            self.assertEqual(module.rollback(str(self.path), backup=False), 0)
        columns = self.columns()
        self.assertEqual(columns, set(module._CE87_JOBS_COLUMNS), '列应精确回到 ce87 原样')
        with sqlite3.connect(self.path) as connection:
            self.assertEqual(
                connection.execute("SELECT detail,found,added,status FROM jobs WHERE id='j1'").fetchone(),
                ('keep detail', 5, 2, 'done'))
            indexes = {row[1] for row in connection.execute('PRAGMA index_list(jobs)')}
        self.assertIn('jobs_fakeid', indexes, '重建后索引要保留')


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(self.tmp.name)
        self.path = Path(self.tmp.name) / 'intel' / 'intel.db'
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        self.tmp.cleanup()

    def columns(self):
        with sqlite3.connect(self.path) as connection:
            return {row[1] for row in connection.execute('PRAGMA table_info(jobs)')}

    def test_owner_columns_migrate_idempotently_without_outcome(self):
        # 旧库：既没有 outcome 也没有 owner 四列。
        with sqlite3.connect(self.path) as connection:
            connection.execute('''CREATE TABLE jobs(
                id TEXT PRIMARY KEY, fakeid TEXT NOT NULL, name TEXT NOT NULL DEFAULT '',
                mode TEXT NOT NULL, status TEXT NOT NULL, reason TEXT NOT NULL DEFAULT '',
                detail TEXT NOT NULL DEFAULT '', routes TEXT NOT NULL DEFAULT '',
                found INTEGER NOT NULL DEFAULT 0, added INTEGER NOT NULL DEFAULT 0,
                bodies INTEGER NOT NULL DEFAULT 0, pages INTEGER NOT NULL DEFAULT 0,
                started_at TEXT NOT NULL, finished_at TEXT NOT NULL DEFAULT '', UNIQUE(id))''')
            connection.execute("INSERT INTO jobs(id,fakeid,name,mode,status,started_at) "
                               "VALUES('old',?,'x','latest','running','2020-01-01T00:00:00+00:00')", (BIZ,))
        intel = Intel(self.store)
        intel._ensure()
        first = self.columns()
        for column in ('ownership_version', 'owner_token', 'owner_dev', 'owner_ino'):
            self.assertIn(column, first)
        Intel(self.store)._ensure()
        self.assertEqual(self.columns(), first)
        row = intel.job('old')
        self.assertEqual(row['ownership_version'], 0)
        self.assertEqual(row['ownership_state'], 'legacy')
        self.assertNotIn('owner_token', row)

    def test_unknown_ownership_version_keeps_gate(self):
        intel = Intel(self.store)
        intel._ensure()
        with intel.db() as connection:
            connection.execute(
                "INSERT INTO jobs(id,fakeid,name,mode,status,started_at,ownership_version,owner_token) "
                "VALUES('v9',?,?,'latest','running','2020-01-01T00:00:00+00:00',9,'deadbeef')",
                (BIZ, NAME))
        self.assertIsNotNone(intel.active_job(), '未知版本必须保持闸门')
        intel._recover_owned_jobs()  # 未知版本不恢复
        self.assertEqual(intel.job('v9')['status'], 'running')
        self.assertEqual(intel.job('v9')['ownership_state'], 'unknown')

    def test_legacy_rows_keep_old_grace(self):
        intel = Intel(self.store)
        intel._ensure()
        stale = '2000-01-01T00:00:00+00:00'
        with intel.db() as connection:
            connection.execute("INSERT INTO jobs(id,fakeid,mode,status,started_at) "
                               "VALUES('old',?,'latest','running',?)", (BIZ, stale))
        self.assertIsNone(intel.active_job())
        self.assertEqual(intel.job('old')['status'], 'interrupted')

    def test_owner_metadata_not_exposed_in_api(self):
        intel = Intel(self.store)
        intel._ensure()
        token = ownership.new_token()
        handle = ownership.acquire(ownership.normalize_root(intel.workspace), token)
        try:
            with intel.db() as connection:
                connection.execute(
                    "INSERT INTO jobs(id,fakeid,name,mode,status,started_at,ownership_version,"
                    "owner_token,owner_dev,owner_ino) VALUES('v1',?,?,'latest','running',?,1,?,?,?)",
                    (BIZ, NAME, '2020-01-01T00:00:00+00:00', token, handle.dev, handle.ino))
            row = intel.job('v1')
        finally:
            handle.close()
        self.assertNotIn('owner_token', row)
        self.assertNotIn('owner_dev', row)
        self.assertNotIn('owner_ino', row)
        self.assertEqual(row['ownership_version'], 1)
        self.assertIn(row['ownership_state'], ('held', 'released'))


class RecoveryOrderTests(unittest.TestCase):
    """第 13 轮后续小修：情报任务恢复必须排在 schema 迁移之后。

    8766 重启日志里那条「情报任务恢复：读取 v1 快照失败（no such column: owner_token），
    本轮不恢复」是恢复跑在 `_ensure` 的逐列 ALTER 之前：旧库没有所有权列，SELECT 直接
    失败，既刷一条假告警，又把这一轮的恢复整个跳过。下面三条常驻用例钉住：
    旧库上先跑恢复 → 不告警、列会被迁移补齐；已有 v1 行的库恢复行为不变；
    真读不了库时仍然要告警（fail-closed 不变）。
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(self.tmp.name)
        self.path = Path(self.tmp.name) / 'intel' / 'intel.db'
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        self.tmp.cleanup()

    def columns(self):
        with sqlite3.connect(self.path) as connection:
            return {row[1] for row in connection.execute('PRAGMA table_info(jobs)')}

    def create_old_schema_db(self):
        """第 13 轮之前的旧库：jobs 没有 outcome 与所有权四列，只有一条 legacy running 行。"""
        with sqlite3.connect(self.path) as connection:
            connection.execute('''CREATE TABLE jobs(
                id TEXT PRIMARY KEY, fakeid TEXT NOT NULL, name TEXT NOT NULL DEFAULT '',
                mode TEXT NOT NULL, status TEXT NOT NULL, reason TEXT NOT NULL DEFAULT '',
                detail TEXT NOT NULL DEFAULT '', routes TEXT NOT NULL DEFAULT '',
                found INTEGER NOT NULL DEFAULT 0, added INTEGER NOT NULL DEFAULT 0,
                bodies INTEGER NOT NULL DEFAULT 0, pages INTEGER NOT NULL DEFAULT 0,
                started_at TEXT NOT NULL, finished_at TEXT NOT NULL DEFAULT '', UNIQUE(id))''')
            connection.execute("INSERT INTO jobs(id,fakeid,name,mode,status,started_at) "
                               "VALUES('old',?,'x','latest','running','2020-01-01T00:00:00+00:00')", (BIZ,))

    def insert_dead_v1(self, intel, job_id='v1'):
        """写一行 v1 running，原锁取到就放掉（= 原所有权已结束，下次检查该被恢复）。"""
        token = ownership.new_token()
        handle = ownership.acquire(ownership.normalize_root(intel.workspace), token)
        try:
            dev, ino = handle.dev, handle.ino
        finally:
            handle.close()
        with intel.db() as connection:
            connection.execute(
                "INSERT INTO jobs(id,fakeid,name,mode,status,started_at,ownership_version,"
                "owner_token,owner_dev,owner_ino) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (job_id, BIZ, NAME, 'latest', 'running', '2020-01-01T00:00:00+00:00', 1, token, dev, ino))
        return token, dev, ino

    def test_recovery_on_old_schema_migrates_first_and_stays_silent(self):
        """旧库上直接调恢复：不打告警，并且先把所有权四列迁移出来。"""
        self.create_old_schema_db()
        intel = Intel(self.store)
        with self.assertNoLogs('intel_store', level='WARNING'):
            intel._recover_owned_jobs()
        for column in ('ownership_version', 'owner_token', 'owner_dev', 'owner_ino'):
            self.assertIn(column, self.columns(), '恢复必须先让迁移把所有权列补齐')
        # 旧行仍是 legacy：不得被所有权恢复路径误收（它只能被启动期的年龄规则收）。
        row = intel.job('old')
        self.assertEqual(row['ownership_version'], 0)
        self.assertEqual(row['ownership_state'], 'legacy')
        self.assertNotIn('所有权', row['reason'], 'legacy 行不得走 v1 所有权恢复')

    def test_v1_row_recovery_behaviour_unchanged_after_gate(self):
        """已有 v1 行的库：恢复行为不变（身份匹配且锁可取 → CAS 标中断）。"""
        intel = Intel(self.store)
        intel._ensure()
        self.insert_dead_v1(intel)
        with self.assertNoLogs('intel_store', level='WARNING'):
            intel._recover_owned_jobs()
        row = intel.job('v1')
        self.assertEqual(row['status'], 'interrupted')
        self.assertIn('所有权', row['reason'])
        self.assertIsNone(intel.active_job(), '恢复后闸门必须放开')

    def test_recovery_before_first_ensure_still_recovers_v1(self):
        """全新实例（还没 _ensure）也要能恢复：先补迁移，再走同一条恢复路径。"""
        seeded = Intel(self.store)
        seeded._ensure()
        self.insert_dead_v1(seeded)
        fresh = Intel(self.store)          # 另一个实例/另一个进程刚启动
        fresh._recover_owned_jobs()        # 走「先确保迁移」，恢复在 _ensure 尾部完成
        self.assertEqual(fresh.job('v1')['status'], 'interrupted')
        self.assertIn('owner_token', self.columns())

    def test_unreadable_database_still_warns(self):
        """真读不了库时仍然要响：不能把失败当成「没有 v1 行」静默吞掉。"""
        self.path.write_bytes(b'not a database at all')
        intel = Intel(self.store)
        with self.assertLogs('intel_store', level='WARNING') as logs:
            intel._recover_owned_jobs()
        self.assertTrue(any('情报任务恢复' in line for line in logs.output), logs.output)
        self.assertFalse(getattr(intel, '_ready', False), '迁移没完成就不能假装就绪')

    def test_mutation_recovery_before_migration_is_caught(self):
        """变异 1 组：把恢复挪回迁移之前（去掉恢复前的迁移闸门）→ 上面的用例变红。

        这里对同一份旧库显式施加变异：直接把 `_ready` 当已就绪，跳过「先确保迁移」。
        变异下所有权列仍然缺失（上面第一条用例的 assertIn 会红），并且旧代码那条
        SELECT 就是 8766 日志里的 no such column。对照：同库走正常路径会被迁移补齐。
        """
        self.create_old_schema_db()
        mutant = Intel(self.store)
        mutant._ready = True                       # 变异：恢复不再先跑 _ensure
        mutant._recover_owned_jobs()
        self.assertNotIn('owner_token', self.columns(), '变异下恢复跑在迁移前，列仍缺失')
        with sqlite3.connect(self.path) as connection:
            with self.assertRaises(sqlite3.OperationalError) as ctx:
                connection.execute("SELECT id,fakeid,owner_token,owner_dev,owner_ino FROM jobs "
                                   "WHERE ownership_version=1 AND status IN ('queued','running')")
        self.assertIn('no such column: owner_token', str(ctx.exception),
                      '变异复现的就是 8766 日志那条失败')
        Intel(self.store)._recover_owned_jobs()    # 对照：正常路径会先迁移
        self.assertIn('owner_token', self.columns())


class RecoveryConcurrencyTests(unittest.TestCase):
    """第 13 轮后续 M1：schema 迁移必须跨实例/跨连接串行化。

    复核复现：``Intel._ensure()`` 只持本实例的 ``self.lock``（一个 RLock）。同一工作区上另一个
    Intel 实例、另一个进程各有各的锁、彼此看不见，两边同时首次进入迁移时会读到**同一份**旧列
    集合、都去 ALTER 同名列，后动手的那个拿到「duplicate column name: outcome」：迁移被判失败、
    ``_ready`` 留假、这一轮 v1 恢复整个跳过（数据没丢，但白刷一条告警 + 少一次恢复）。

    下面钉住四件事：
    1. 自然并发（Barrier 起跑，不挂钩）10 轮，两个实例都本轮就绪、都不告警；
    2. 确定性时序——两个独立 Store 的连接都被同步到同一份旧列集合（都在写事务之前），仍不重复
       ALTER，且 v1 行照常被恢复（没被跳过）；
    3. 迁移抢不到锁（另一连接 BEGIN EXCLUSIVE）仍然告警并 fail-closed；
    4. 变异 1 组：去掉「拿到写锁后重读列集合」这一半，同一场景必须红。
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(self.tmp.name)
        self.path = Path(self.tmp.name) / 'intel' / 'intel.db'
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        self.tmp.cleanup()

    def columns(self):
        with sqlite3.connect(self.path) as connection:
            return {row[1] for row in connection.execute('PRAGMA table_info(jobs)')}

    def create_legacy_db(self, with_outcome=False):
        """第 13 轮之前的旧库：jobs 只有原始列，缺 routes/outcome/所有权四列/discovery。

        legacy 行取当下 started_at：迁移顺带会跑 interrupt_stale_jobs，2020 年的行会被它按年龄
        收掉，那样就分不清「所有权恢复有没有误碰 legacy 行」了。
        """
        with sqlite3.connect(self.path) as connection:
            connection.execute('''CREATE TABLE jobs(
                id TEXT PRIMARY KEY, fakeid TEXT NOT NULL, name TEXT NOT NULL DEFAULT '',
                mode TEXT NOT NULL, status TEXT NOT NULL, reason TEXT NOT NULL DEFAULT '',
                detail TEXT NOT NULL DEFAULT '', routes TEXT NOT NULL DEFAULT '',
                found INTEGER NOT NULL DEFAULT 0, added INTEGER NOT NULL DEFAULT 0,
                bodies INTEGER NOT NULL DEFAULT 0, pages INTEGER NOT NULL DEFAULT 0,
                started_at TEXT NOT NULL, finished_at TEXT NOT NULL DEFAULT '', UNIQUE(id))''')
            connection.execute("INSERT INTO jobs(id,fakeid,name,mode,status,started_at) "
                               "VALUES('old',?,'x','latest','running',?)", (BIZ, intel_store.now()))
        with sqlite3.connect(self.path) as connection:
            # 先建旧 jobs 再补其余表，才是真实旧库的样子（复核就是照这个顺序构造的）
            connection.executescript(intel_store.SCHEMA)
            connection.execute("INSERT INTO subscriptions(fakeid,name,added_at) VALUES(?,?,?)",
                               (BIZ, NAME, intel_store.now()))
            if with_outcome:
                connection.execute("ALTER TABLE jobs ADD COLUMN outcome TEXT NOT NULL DEFAULT ''")

    def create_partial_db_with_dead_v1(self):
        """半迁移旧库：所有权四列+discovery+routes 已在、outcome 仍缺，另有一行锁已释放的 v1。

        对应复核里的 scheduled.py 场景（两个 Store 复现 duplicate ownership_version）：
        迁移仍有活要干（补 outcome）才撞得上并发，而 v1 行让「恢复没被跳过」可断言。
        """
        self.create_legacy_db()
        with sqlite3.connect(self.path) as connection:
            for column, declaration in (('ownership_version', 'INTEGER NOT NULL DEFAULT 0'),
                                        ('owner_token', "TEXT NOT NULL DEFAULT ''"),
                                        ('owner_dev', 'INTEGER NOT NULL DEFAULT 0'),
                                        ('owner_ino', 'INTEGER NOT NULL DEFAULT 0'),
                                        ('discovery', "TEXT NOT NULL DEFAULT ''")):
                connection.execute(f'ALTER TABLE jobs ADD COLUMN {column} {declaration}')
            root = ownership.normalize_root(self.workspace())
            token = ownership.new_token()
            handle = ownership.acquire(root, token)
            try:
                dev, ino = handle.dev, handle.ino
            finally:
                handle.close()          # 原所有权已结束：下次检查该被恢复
            connection.execute(
                "INSERT INTO jobs(id,fakeid,name,mode,status,started_at,ownership_version,"
                "owner_token,owner_dev,owner_ino) VALUES(?,?,?,?,?,?,?,?,?,?)",
                ('v1', BIZ, NAME, 'latest', 'running', intel_store.now(), 1, token, dev, ino))

    def workspace(self):
        return Path(self.tmp.name) / 'intel'

    def make_intel(self):
        """每个实例配自己的 Store：两个 Store 对象 = 两个互不相识的进程内锁。"""
        return Intel(Store(self.tmp.name), discovery_factory=fake_discovery)

    def access_together(self, intels, timeout=30):
        """两个实例同时首次访问（Barrier 起跑就是「自然并发」，不挂钩 SQL）。"""
        errors = []
        barrier = threading.Barrier(len(intels))

        def access(intel):
            try:
                barrier.wait(timeout=10)
                intel._recover_owned_jobs()
            except Exception as exc:  # noqa: BLE001
                errors.append(repr(exc))

        threads = [threading.Thread(target=access, args=(intel,)) for intel in intels]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout)
        self.assertFalse(any(thread.is_alive() for thread in threads), '并发访问必须自己收尾，不留挂死线程')
        return errors

    def stale_column_gate(self, barrier):
        """把每个连接第一次读「PRAGMA table_info(jobs)」（锁外快路径）同步到同一刻。

        真实执行照旧：不伪造 SQL 结果、不注入异常、不替换连接；只是让两条连接先各自读到
        同一份旧列集合，之后走产品代码里真正的 BEGIN IMMEDIATE 与真正的 ALTER。
        """
        real_connect = sqlite3.connect

        class _Gated:
            def __init__(self, connection):
                self._connection = connection
                self._paused = False

            def __getattr__(self, name):
                return getattr(self._connection, name)

            def execute(self, sql, *args):
                cursor = self._connection.execute(sql, *args)
                if self._paused or sql != 'PRAGMA table_info(jobs)':
                    return cursor
                self._paused = True
                rows = cursor.fetchall()
                barrier.wait(timeout=10)
                return rows

        def connect(*args, **kwargs):
            # 只包 _ensure 的迁移连接（位置参数）；_connect(timeout=…) 那些恢复连接保持原样。
            return _Gated(real_connect(*args, **kwargs)) if not kwargs else real_connect(*args, **kwargs)

        return patch.object(sqlite3, 'connect', side_effect=connect)

    def assert_both_ready(self, intels, errors, note):
        self.assertEqual(errors, [], f'{note}：并发访问抛出了未收口的异常')
        self.assertEqual([getattr(intel, '_ready', False) for intel in intels], [True, True],
                         f'{note}：两个实例必须都本轮就绪，不能有一个 _ready=False')

    def test_two_instances_first_access_both_ready_under_natural_barrier(self):
        """自然并发 10 轮：同时首次访问旧库，两个实例都就绪、都不告警、恢复不被跳过。"""
        for attempt in range(10):
            with self.subTest(round=attempt):
                self.path.parent.mkdir(parents=True, exist_ok=True)
                for leftover in self.path.parent.glob('intel.db*'):   # 每轮一份全新旧库
                    leftover.unlink()
                self.create_legacy_db()
                intels = [self.make_intel(), self.make_intel()]
                with self.assertNoLogs('intel_store', level='WARNING'):
                    errors = self.access_together(intels)
                self.assert_both_ready(intels, errors, f'第 {attempt} 轮')
                self.assertLessEqual(set(intel_store.Intel.OWNERSHIP_COLUMNS), self.columns(),
                                     '本轮迁移必须把所有权四列补齐')
                row = intels[0].job('old')
                self.assertEqual(row['ownership_version'], 0)
                self.assertEqual(row['status'], 'running', 'legacy 行不得被所有权恢复路径误碰')

    def test_two_stores_forced_onto_same_stale_columns_do_not_alter_twice(self):
        """确定性时序：两个 Store 的连接被同步到同一份旧列集合后，仍不重复 ALTER、恢复照跑。"""
        self.create_partial_db_with_dead_v1()
        intels = [self.make_intel(), self.make_intel()]
        self.assertIsNot(intels[0].store, intels[1].store, '必须是两个独立 Store 对象（各自的 RLock）')
        gate = self.stale_column_gate(threading.Barrier(2))
        with gate:
            with self.assertNoLogs('intel_store', level='WARNING'):
                errors = self.access_together(intels)
        self.assert_both_ready(intels, errors, '同步到同一份旧列集合')
        self.assertIn('outcome', self.columns())
        row = intels[0].job('v1')
        self.assertEqual(row['status'], 'interrupted', 'v1 行必须被恢复，不能因为迁移串行化被跳过')
        self.assertEqual(intels[0].active_job()['id'], 'old',
                         '恢复后占着「已有任务」的必须只剩 legacy 行：v1 行不能再挡着闸门')
        self.assertEqual(intels[1].job('old')['status'], 'running')

    def test_lock_timeout_during_migration_still_warns_and_stays_not_ready(self):
        """迁移抢不到写锁（另一连接 BEGIN EXCLUSIVE）仍然告警并 fail-closed，不能当成「已迁移」。"""
        self.create_legacy_db()
        holder = sqlite3.connect(self.path)
        holder.execute('BEGIN EXCLUSIVE')
        try:
            intel = self.make_intel()
            with self.assertLogs('intel_store', level='WARNING') as logs:
                intel._recover_owned_jobs()
        finally:
            holder.rollback()
            holder.close()
        self.assertTrue(any('情报任务恢复' in line for line in logs.output), logs.output)
        self.assertFalse(getattr(intel, '_ready', False), '迁移没落地就不能假装就绪')

    def test_mutation_migrating_without_lock_inner_reread_is_caught(self):
        """变异 1 组：拿到写锁后不重读列集合 → 同一个并发场景必须红（duplicate column）。

        这里真的把 ``_migrate_columns`` 换成「锁外算一次列集合、拿到锁后照旧 ALTER 那一版」，
        产品代码其余部分不动：两实例都被同步到同一份旧列集合后，后一个必然 ALTER 到别人刚加的列。
        """
        def mutant(self, connection):
            missing = self._missing_columns(connection)      # 变异：锁外算一次，之后不再重读
            if not missing:
                return
            connection.execute('BEGIN IMMEDIATE')
            try:
                for table, gap in missing.items():
                    for column, declaration in gap:
                        connection.execute(f'ALTER TABLE {table} ADD COLUMN {column} {declaration}')
                connection.commit()
            finally:
                connection.rollback()                        # 别把半截事务留在连接上

        self.create_legacy_db(with_outcome=True)              # 只缺所有权四列 → 撞 duplicate owner_*
        intels = [self.make_intel(), self.make_intel()]
        gate = self.stale_column_gate(threading.Barrier(2))
        with patch.object(intel_store.Intel, '_migrate_columns', mutant), gate:
            with self.assertLogs('intel_store', level='WARNING') as logs:
                errors = self.access_together(intels)
        self.assertEqual(errors, [], '变异下也不该有未收口的异常，只有告警')
        self.assertTrue(any('duplicate column name' in line for line in logs.output), logs.output)
        self.assertFalse(all(getattr(intel, '_ready', False) for intel in intels),
                         '变异确实让一个实例迁移失败、_ready 留假（红例）')
        # 对照：拿掉变异后同一份库能被正常迁移补齐。
        control = self.make_intel()
        with self.assertNoLogs('intel_store', level='WARNING'):
            control._recover_owned_jobs()
        self.assertTrue(getattr(control, '_ready', False))
        self.assertLessEqual(set(intel_store.Intel.OWNERSHIP_COLUMNS), self.columns())


class MutationTests(OwnershipTestCase):
    """变异各有行为红例：施加变异后，正常不变式确实被破坏。

    原 5 项（v1 年龄回收 / 忽略 inode / 漏 token CAS / 提前 close / DB busy 漏 finally）+
    L2 为 M1–M4 各加 1 项（不取清理期闸门 / 公共状态不核对锁 / 继承 registry 锁不重建 /
    回滚只降级不删列）。
    """

    def test_mutation_v1_recycled_by_age(self):
        self.insert_v1('job', ownership.new_token(), 1, 2)  # started_at 是 2020 年

        def mutant(connection, reason, protected=(), abandoned=()):
            # 变异：去掉 ownership_version=0 过滤，v1 也按年龄回收。
            from datetime import datetime, timedelta, timezone
            cutoff = (datetime.now(timezone.utc) - timedelta(seconds=STALE_JOB_SECONDS)).isoformat()
            connection.execute("UPDATE jobs SET status='interrupted' WHERE status IN ('queued','running') "
                               "AND started_at < ?", (cutoff,))

        with self.intel.db() as connection:
            intel_store.interrupt_stale_jobs(connection, LIVE_STALE_REASON)  # 正确：不动 v1
            self.assertEqual(connection.execute("SELECT status FROM jobs WHERE id='job'").fetchone()[0],
                             'running')
        with self.intel.db() as connection:
            mutant(connection, LIVE_STALE_REASON)  # 变异：误回收 v1
            self.assertEqual(connection.execute("SELECT status FROM jobs WHERE id='job'").fetchone()[0],
                             'interrupted', '变异确实产生错误行为（红例）')

    def test_mutation_ignore_inode(self):
        token = ownership.new_token()
        dev, ino = self.acquire_free(token)
        path = self.lock_path(token)
        path.unlink()
        path.write_text('')  # 重建，inode 变了
        real_state, _ = ownership.probe(self.root, token, dev, ino)
        self.assertEqual(real_state, ownership.UNKNOWN)  # 正确：身份不符判 unknown

        def mutant_probe(root, token_, dev_, ino_):
            # 变异：只看文件存在，不核 inode。
            try:
                fd = os.open(ownership.locks_dir(root) / f'{token_}.lock', os.O_RDWR | os.O_CLOEXEC)
            except OSError:
                return ownership.UNKNOWN, None
            os.close(fd)
            return ownership.ACQUIRED, None

        state, _ = mutant_probe(self.root, token, dev, ino)
        self.assertEqual(state, ownership.ACQUIRED, '变异确实忽略 inode（红例）')

    def test_mutation_missing_token_cas(self):
        token = ownership.new_token()
        dev, ino = self.acquire_free(token)
        self.insert_v1('job', token, dev, ino)
        self.intel._finish_job('job', 'done', '错 token', owner='f' * 32,
                               outcome=intel_store.recovered_outcome())
        self.assertEqual(self.intel.job('job')['status'], 'running')  # 正确：CAS 拦住

        def mutant_finish(job_id, status, reason='', outcome=None, **kwargs):
            # 变异：无条件写（漏 token CAS）。
            with self.intel.db() as connection:
                connection.execute("UPDATE jobs SET status=?,reason=? WHERE id=?",
                                   (status, reason, job_id))

        mutant_finish('job', 'done', '错 token')
        self.assertEqual(self.intel.job('job')['status'], 'done', '变异确实漏了 token CAS（红例）')

    def test_mutation_early_close_owner(self):
        token = ownership.new_token()
        handle = ownership.acquire(self.root, token)
        self.insert_v1('job', token, handle.dev, handle.ino)
        state, _ = ownership.probe(self.root, token, handle.dev, handle.ino)
        self.assertEqual(state, ownership.HELD)  # 正确：运行中仍持锁
        handle.close()  # 变异：提前关闭 owner
        state2, free = ownership.probe(self.root, token, handle.dev, handle.ino)
        if free is not None:
            free.close()
        self.assertEqual(state2, ownership.ACQUIRED, '变异确实提前放锁（红例）')

    def test_mutation_db_busy_missed_finally(self):
        token = ownership.new_token()
        dev, ino = self.acquire_free(token)
        self.insert_v1('job', token, dev, ino)
        blocker = sqlite3.connect(self.intel.path, timeout=1)
        try:
            blocker.execute('BEGIN IMMEDIATE')
            # 变异：取到锁后 DB busy 时不 close（漏 finally）。
            state, leaked = ownership.probe(self.root, token, dev, ino)
            self.assertEqual(state, ownership.ACQUIRED)
            try:
                connection = self.intel._connect(timeout=0.2, busy_timeout=200)
                try:
                    connection.execute('BEGIN IMMEDIATE')
                finally:
                    connection.close()
            except sqlite3.Error:
                pass  # 变异路径：忘记 leaked.close()
            self.assertEqual(ownership.probe(self.root, token, dev, ino)[0], ownership.HELD,
                             '变异确实泄漏了恢复 FD（红例）')
            leaked.close()
        finally:
            blocker.rollback()
            blocker.close()
        # 正确行为：finally 关闭后锁可取。
        state, free = ownership.probe(self.root, token, dev, ino)
        self.assertEqual(state, ownership.ACQUIRED)
        free.close()

    def test_mutation_without_cleanup_gate_cross_process_claims(self):
        """M1 变异红例：不取清理期闸门时，清理期的第二个进程确实能认领。"""
        entered = threading.Event()
        release = threading.Event()
        real = self.intel._write_job_detail

        def blocked(job_id, detail, owner=None):
            entered.set()
            release.wait(10)
            return real(job_id, detail, owner=owner)

        with patch.object(ownership, '_open_gate', lambda dir_fd: None), \
                patch.object(self.intel, '_write_job_detail', blocked):
            first = self.intel.run({'fakeid': BIZ, 'mode': 'latest', 'sogou': False})
            self.public.entered.wait(5)
            self.public.release.set()
            self.assertTrue(entered.wait(5))
            self.assertEqual(self.intel.job(first['id'])['status'], 'done')
            ready, go, result = self.handshake_paths('mutgate')
            proc = self.spawn_child(CLAIMER, self.tmp.name, BIZ, ready, go, result, '')
            try:
                wait_for_file(ready, 10)
                Path(go).write_text('1')
                wait_for_file(result, 15)
                wait_for_processes([proc], 15)
                self.assertTrue(json.loads(Path(result).read_text())['claimed'],
                                '变异（不取清理期闸门）确实放行了清理期的第二个进程（红例）')
            finally:
                release.set()
                if proc.poll() is None:
                    proc.kill()
                    proc.wait(timeout=5)
            self.intel.wait_for(first['id'], 5)

    def test_mutation_public_state_ignores_lock_identity(self):
        """M2 变异红例：只按数据库 running 判 held、不核对锁文件时，缺锁也报 held。"""
        token = ownership.new_token()
        dev, ino = self.acquire_free(token)
        self.insert_v1('job', token, dev, ino)
        self.lock_path(token).unlink()  # 锁缺失
        self.assertEqual(self.intel.job('job')['ownership_state'], 'unknown')  # 正确
        with self.intel.db() as connection:
            row = dict(connection.execute("SELECT * FROM jobs WHERE id='job'").fetchone())
        # 变异：跳过锁核对，直接由 version+status 判 held。
        mutant = Intel._ownership_state(row['ownership_version'], row['owner_token'],
                                        row['owner_dev'], row['owner_ino'], 'running')
        self.assertEqual(mutant, 'held', '变异确实忽略了锁身份（红例）')

    def test_mutation_inherited_lock_deadwaits(self):
        """M3 变异红例：不重建继承的 registry 锁时，子进程沿用它就会死等（取不到）。"""
        inherited = ownership._HANDLES_LOCK
        inherited.acquire()  # 父线程持有；等价于 fork 时另一线程正持 registry 锁
        try:
            pid = os.fork()
            if pid == 0:
                signal.alarm(3)
                ok = inherited.acquire(timeout=1.0)  # 沿用继承来的旧锁（变异路径）
                if ok:
                    inherited.release()
                    os._exit(0)
                os._exit(7)
            try:
                status = wait_pid_bounded(pid, 5)
            except BaseException:
                try:
                    os.kill(pid, signal.SIGKILL)
                    os.waitpid(pid, 0)
                except OSError:
                    pass
                raise
            self.assertEqual(os.waitstatus_to_exitcode(status), 7,
                             '继承的锁在子进程里取不到（变异红例）')
        finally:
            inherited.release()

    def test_mutation_rollback_skips_column_drop(self):
        """M4 变异红例：回滚只降级、不删列时，ce87 旧代码仍会 SELECT * 暴露 owner 字段。"""
        tmp = tempfile.TemporaryDirectory()
        try:
            store = Store(tmp.name)
            path = Path(tmp.name) / 'intel' / 'intel.db'
            path.parent.mkdir(parents=True, exist_ok=True)
            intel = Intel(store)
            intel._ensure()
            with intel.db() as connection:
                connection.execute(
                    "INSERT INTO jobs(id,fakeid,mode,status,started_at,ownership_version,"
                    "owner_token,owner_dev,owner_ino) "
                    "VALUES('j1','biz','latest','done','2026-01-01T00:00:00+00:00',1,"
                    "'ROLLBACK_TOKEN',1,2)")
            # 变异：只降 version，不删列、不清 owner。
            with sqlite3.connect(path) as connection:
                connection.execute("UPDATE jobs SET ownership_version=0")
                connection.row_factory = sqlite3.Row
                row = dict(connection.execute("SELECT * FROM jobs WHERE id='j1'").fetchone())
            old = load_old_intel()
            public = old.Intel._public_job(row)
            self.assertEqual(public.get('owner_token'), 'ROLLBACK_TOKEN',
                             '变异确实保留了 owner 列，旧代码仍会暴露原字段（红例）')
        finally:
            tmp.cleanup()

    # ── L3：为 M1/M2/M3 各补变异，去掉修复必须至少一个红例 ──

    def test_mutation_legacy_predicate_by_version_only(self):
        """M1 变异红例：年龄清理只看 ownership_version=0 时，v0 混入 owner 的行确实被清掉。"""
        token = ownership.new_token()
        dev, ino = self.acquire_free(token)
        self.insert_identity('v0mix', 0, token, dev, ino)  # 2020 年，身份未知
        with self.intel.db() as connection:
            intel_store.interrupt_stale_jobs(connection, LIVE_STALE_REASON)
        self.assertEqual(self.intel.job('v0mix')['status'], 'running', '正确：不动未知身份行')
        with patch.object(ownership, 'LEGACY_WHERE', 'ownership_version=0'):
            with self.intel.db() as connection:
                intel_store.interrupt_stale_jobs(connection, LIVE_STALE_REASON)
        self.assertEqual(self.intel.job('v0mix')['status'], 'interrupted',
                         '变异（只按 version）确实误清了 unknown 行（红例）')

    def test_mutation_short_grace_releases_unknown_row(self):
        """M1 变异红例：abandoned 排除只看 version!=0 时，未知行确实被短宽限放行。"""
        token = ownership.new_token()
        dev, ino = self.acquire_free(token)
        started = (datetime.now(timezone.utc) - timedelta(seconds=ABANDONED_JOB_SECONDS + 120)).isoformat()
        self.insert_identity('v0mix', 0, token, dev, ino, started=started)
        key = (str(self.intel.path.resolve()), 'v0mix')
        intel_store._PROCESS_ABANDONED.add(key)
        try:
            self.assertIsNotNone(self.intel.active_job(), '正确：未知行仍占闸门')
            with patch.object(ownership, 'NOT_LEGACY_WHERE', 'ownership_version != 0'):
                self.assertIsNone(self.intel.active_job(),
                                  '变异确实让短宽限绕过了未知身份（红例）')
        finally:
            intel_store._PROCESS_ABANDONED.discard(key)

    def test_mutation_lenient_classify_raises_on_blob_token(self):
        """M2 变异红例：旧宽松归类碰上 BLOB token 确实抛 TypeError（公开路径会崩）。"""
        self.assertEqual(ownership.classify(1, b'BLOB', 1, 1), ownership.UNKNOWN)  # 正确

        def lenient_classify(version, token, dev, ino):
            # 变异：不先校类型，直接 TOKEN_RE.match(token or '')，并用宽松 int() 解析。
            def lenient_int(value):
                try:
                    return int(value)
                except (TypeError, ValueError):
                    return None

            v = lenient_int(version)
            if v == 0 and (token or '') == '' and lenient_int(dev) == 0 and lenient_int(ino) == 0:
                return ownership.LEGACY
            if v != ownership.OWNERSHIP_VERSION:
                return ownership.UNKNOWN
            if not ownership.TOKEN_RE.match(token or ''):
                return ownership.UNKNOWN
            d, i = lenient_int(dev), lenient_int(ino)
            if d is None or i is None or d <= 0 or i <= 0:
                return ownership.UNKNOWN
            return ownership.V1

        with self.assertRaises(TypeError):
            lenient_classify(1, b'BLOB', 1, 1)
        with self.assertRaises(OverflowError):
            lenient_classify(1, 'a' * 32, float('inf'), 1)

    def test_mutation_projection_downgrades_illegal_version(self):
        """M2 变异红例：投影先把非法 version 降成 0，该行确实伪装成 legacy。"""
        self.insert_identity('badver', 'bad-version', '', 0, 0)
        self.assertEqual(self.intel.job('badver')['ownership_state'], 'unknown')  # 正确
        with self.intel.db() as connection:
            row = dict(connection.execute("SELECT * FROM jobs WHERE id='badver'").fetchone())
        lenient = int(row['ownership_version'] or 0) if str(row['ownership_version']).isdigit() else 0
        # 变异：先降级再分类（等价于旧的 _int_or(...,0) 写法）。
        self.assertEqual(Intel._ownership_state(lenient, row['owner_token'], row['owner_dev'],
                                                row['owner_ino'], row['status']),
                         'legacy', '变异确实把非法 version 伪装成 legacy（红例）')

    def test_mutation_missing_owner_syncs_subscription(self):
        """M3 变异红例：订阅回读仍满于 `owner is None or rowcount>0` 时，CAS=0 也刷了订阅。"""
        token = ownership.new_token()
        handle = ownership.acquire(self.root, token)
        try:
            self.insert_v1('held', token, handle.dev, handle.ino)
            self.freeze_subscription()
            self.intel._finish_job('held', 'done', '漏传 owner')
            self.assertEqual(self.subscription_receipt(), ('SENTINEL', 'sentinel'),
                             '正确：CAS=0 不动订阅')
            with self.intel.db() as connection:
                cursor = connection.execute(
                    "UPDATE jobs SET status='done',finished_at=? WHERE id=? AND status != 'interrupted' "
                    "AND ownership_version=0", (intel_store.now(), 'held'))
                if True:  # 变异：owner is None 就无条件回读（不看 rowcount）
                    row = connection.execute('SELECT fakeid,status FROM jobs WHERE id=?',
                                             ('held',)).fetchone()
                    self.assertEqual(cursor.rowcount, 0, '变异下 CAS 仍为 0')
                    connection.execute('UPDATE subscriptions SET last_run_at=?,last_status=? WHERE fakeid=?',
                                       (intel_store.now(), row['status'], row['fakeid']))
            self.assertNotEqual(self.subscription_receipt(), ('SENTINEL', 'sentinel'),
                                '变异确实让漏 token 的收口刷了订阅（红例）')
        finally:
            handle.close()


if __name__ == '__main__':
    unittest.main()
