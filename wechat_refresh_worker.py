"""Local-only refresh coordinator, using the previously verified snapshot tools.

No login, debugger, key extraction, global key installation or Radar mutation.
The worker owns a deadline; its signal exceptions also reach run_private(),
which stops the separate process group used by each decrypt/collector child.
"""
import argparse
import fcntl
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import signal
import stat
import sys
import tempfile
import time


def clock():
    return datetime.now(timezone.utc).isoformat()


def private_directory(path):
    path = Path(path).absolute()
    current = path
    while current != current.parent:
        if current.is_symlink():
            raise ValueError('采集任务路径包含符号链接')
        current = current.parent
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
        raise ValueError('采集任务目录须属于本机用户且为私有目录')
    return path


def read_private(path):
    path = Path(path)
    private_directory(path.parent)
    fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
    with os.fdopen(fd) as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600 or info.st_size > 65536:
            raise ValueError('采集配置或回执不是有效的私有文件')
        return json.load(handle)


def write_private(path, value):
    path = Path(path)
    private_directory(path.parent)
    if path.is_symlink():
        raise ValueError('采集回执不能写入符号链接')
    fd, temporary = tempfile.mkstemp(prefix='.receipt-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as handle:
            json.dump(value, handle, ensure_ascii=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class Interrupted(Exception):
    pass


@contextmanager
def deadline(seconds):
    def stop(signum, frame):
        raise Interrupted('本机采集已停止或超过总时限')
    previous = {s: signal.signal(s, stop) for s in (signal.SIGTERM, signal.SIGINT, signal.SIGALRM)}
    old_timer = signal.getitimer(signal.ITIMER_REAL)
    started = time.monotonic()
    signal.setitimer(signal.ITIMER_REAL, min(seconds, old_timer[0]) if old_timer[0] > 0 else seconds)
    try:
        yield
    finally:
        elapsed = time.monotonic() - started
        remaining = max(.001, old_timer[0] - elapsed) if old_timer[0] > 0 else 0
        signal.setitimer(signal.ITIMER_REAL, remaining, old_timer[1])
        for signum, handler in previous.items():
            signal.signal(signum, handler)


def refresh(plan, job_dir, refresh_module, collector_module):
    """No file fallback on failure. A commit ambiguity stops automatic refresh."""
    folder = private_directory(job_dir)
    state = {'status': 'running', 'phase': 'checking_access', 'started_at': clock(), 'messages_imported': False}
    stage = None

    def publish(**changes):
        state.update(changes)
        write_private(folder / 'state.json', state)

    publish()
    try:
        with deadline(plan['timeout_seconds']):
            # A hidden macOS App Data prompt can suspend directory access. Bound
            # that check independently instead of showing "copying" for minutes.
            with deadline(15):
                settings = refresh_module.f.load_config(refresh_module.f.private_root())
                refresh_module.f.business_databases(settings['db_dir'])
            if str(settings['collector_db']) != plan['collector_db']:
                raise ValueError('采集器路径与工作台来源不一致，停止刷新')
            publish(phase='preparing')
            prepared = refresh_module.prepare(Path(plan['capture_dir']))
            snapshot = Path(prepared['snapshot_dir'])
            # Time at which the stable source copy passed HMAC validation.
            publish(phase='decrypting', captured_at=clock(), snapshot_dir=str(snapshot))
            receipt = refresh_module.decrypt(snapshot)
            publish(phase='staging', verified_databases=len(receipt['expected_databases']),
                    core_integrity_passed=len(receipt['core_checked']))
            stage = collector_module.stage(snapshot, timeout=plan['timeout_seconds'])
            publish(phase='committing', stage_dir=stage['stage_dir'])
            result = collector_module.commit(Path(stage['stage_dir']), execute=True, timeout=60)
            if result.get('messages_imported') is not True:
                raise ValueError('正式采集库提交尚未确认')
            publish(status='done', phase='captured', finished_at=clock(), **result)
            return state
    except BaseException as error:
        # A signal after SQLITE_DONE must not masquerade as a rolled-back import.
        committed = False
        unknown = state.get('phase') == 'committing'
        if unknown and stage is not None:
            try:
                actual = collector_module.database_state(Path(plan['collector_db']))
                logical = {k: v for k, v in actual.items() if k != 'files'}
                if logical == stage['staged_state']:
                    committed, unknown = True, False
                elif actual == stage['source_state'] or logical == {k: v for k, v in stage['source_state'].items() if k != 'files'}:
                    unknown = False
            except BaseException:
                pass
        access_unconfirmed = state.get('phase') == 'checking_access' and isinstance(error, Interrupted)
        detail = ('访问微信文件尚未完成，请检查 macOS 授权或文件状态；旧资料保留' if access_unconfirmed else
                  '正式采集库提交结果需要核对，已停止自动重试' if unknown else
                  '采集已提交；进程中断，等待更新工作台索引' if committed else
                  '没有访问必要文件的权限；旧资料保留' if isinstance(error, PermissionError) else
                  str(error) if isinstance(error, (Interrupted, refresh_module.f.FinishError, ValueError)) else
                  '本机采集未完成；旧资料保留，诊断仅存本机')
        changes = {'status': 'commit_unknown' if unknown else 'done' if committed else 'error',
                   'phase': 'captured' if committed else 'stopped', 'detail': detail,
                   'finished_at': clock(), 'messages_imported': committed}
        if access_unconfirmed:
            changes['error_kind'] = 'access_unconfirmed'
        if committed:
            changes.update(collector_messages=stage['staged_state']['messages'],
                           new_messages=stage['staged_state']['messages'] - stage['source_state']['messages'],
                           max_msg_time=stage['staged_state']['max_msg_time'])
        publish(**changes)
        return state


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--job-dir', type=Path, required=True)
    parser.add_argument('--lock-fd', type=int, required=True)
    args = parser.parse_args()
    folder = private_directory(args.job_dir)
    lock_info = os.fstat(args.lock_fd)
    lock_path = folder.parent / '.capture.lock'
    actual = lock_path.lstat()
    if (not stat.S_ISREG(lock_info.st_mode) or lock_info.st_uid != os.getuid()
            or stat.S_IMODE(lock_info.st_mode) != 0o600 or stat.S_ISLNK(actual.st_mode)
            or (actual.st_dev, actual.st_ino) != (lock_info.st_dev, lock_info.st_ino)):
        raise ValueError('采集协作锁未绑定本次任务')
    fcntl.flock(args.lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    plan = read_private(folder / 'plan.json')
    if os.geteuid() == 0 or not isinstance(plan, dict) or type(plan.get('timeout_seconds')) is not int or not 30 <= plan['timeout_seconds'] <= 7200:
        raise ValueError('采集须由本机用户运行且总时限有效')
    sys.dont_write_bytecode = True
    sys.path.insert(0, plan['helper_dir'])
    import refresh_snapshot
    import stage_collector
    result = refresh(plan, folder, refresh_snapshot, stage_collector)
    # Only summary counts/status leave the worker. Private receipts retain paths.
    print(json.dumps({k: result.get(k) for k in ('status', 'messages_imported', 'new_messages')}, ensure_ascii=False))
    return 0 if result['status'] == 'done' else 3


if __name__ == '__main__':
    raise SystemExit(main())
