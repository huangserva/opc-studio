"""One-shot local health reporter. Writes only its own log, state and lock."""
import argparse
from datetime import datetime, timezone
import fcntl
from http.client import HTTPException
import json
import math
from pathlib import Path
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request

MAX_LOG_BYTES = 1024 * 1024
COOLDOWN_SECONDS = 3600
MESSAGES = {
    'port_unavailable': '工作台端口不可用',
    'http_timeout': '工作台心跳接口超时',
    'http_status': '工作台心跳接口未返回 200',
    'http_unavailable': '工作台心跳接口不可用',
    'invalid_json': '工作台心跳接口返回格式无效',
    'invalid_health': '工作台心跳字段无效',
    'tick_stale': '调度器心跳超过 5 分钟',
    'scheduler_error': '调度器报告检查失败',
    'scheduler_unhealthy': '调度器状态异常',
}


def _valid_timestamp(value):
    if not isinstance(value, str):
        return False
    try:
        return datetime.fromisoformat(value).utcoffset() is not None
    except (ValueError, OverflowError):
        return False


def probe(port=8766, timeout=5):
    """No proxies or redirects. TCP and HTTP share a five-second deadline."""
    timeout = min(timeout, 5)
    deadline = time.monotonic() + timeout
    result = {'port_listening': False, 'http_status': None,
              'tick_age_seconds': None, 'last_error_empty': None, 'errors': []}
    # A hard wall-clock limit also covers slow-drip HTTP headers. The daemon
    # performs only network reads, never writes state or sends notifications.
    def run_probe():
        try:
            _probe(port, deadline, result)
        except Exception:
            # A malformed response must fail closed, without dumping its contents.
            result['errors'] = ['http_unavailable']
    worker = threading.Thread(target=run_probe, daemon=True)
    worker.start()
    worker.join(max(0, deadline-time.monotonic()))
    if worker.is_alive():
        return {**result, 'errors': ['http_timeout']}
    return result


def _probe(port, deadline, result):
    try:
        with socket.create_connection(('127.0.0.1', port), timeout=max(.001, deadline-time.monotonic())):
            result['port_listening'] = True
    except OSError:
        result['errors'].append('port_unavailable')
        return result

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None

    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    try:
        with opener.open(f'http://127.0.0.1:{port}/api/schedules/health',
                         timeout=max(.001, deadline-time.monotonic())) as response:
            result['http_status'] = response.status
            if response.status != 200:
                result['errors'].append('http_status')
                return result
            # Bound both payload and total read time, including trickle responses.
            data = bytearray()
            while len(data) <= 16384:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError
                chunk = response.read1(min(4096, 16385-len(data)))
                if not chunk:
                    break
                data.extend(chunk)
            if len(data) > 16384:
                result['errors'].append('invalid_json')
                return result
    except urllib.error.HTTPError as exc:
        result['http_status'] = exc.code
        exc.close()
        result['errors'].append('http_status')
        return result
    except (TimeoutError, socket.timeout):
        result['errors'].append('http_timeout')
        return result
    except urllib.error.URLError as exc:
        result['errors'].append('http_timeout' if isinstance(exc.reason, TimeoutError) else 'http_unavailable')
        return result
    except (OSError, ValueError, HTTPException):
        result['errors'].append('http_unavailable')
        return result
    try:
        health = json.loads(data)
    except (ValueError, UnicodeError):
        result['errors'].append('invalid_json')
        return result
    if not isinstance(health, dict):
        result['errors'].append('invalid_health')
        return result
    age = health.get('tick_age_seconds')
    valid_age = (type(age) is int and age >= 0 or
                 type(age) is float and math.isfinite(age) and age >= 0)
    error = health.get('last_error', True)
    valid_error = error is None or isinstance(error, str)
    count = health.get('running_count')
    valid_count = type(count) is int and count >= 0
    if (not valid_age or not valid_error or type(health.get('ok')) is not bool or
            not _valid_timestamp(health.get('last_tick')) or
            not _valid_timestamp(health.get('now')) or not valid_count):
        result['errors'].append('invalid_health')
    if valid_age:
        result['tick_age_seconds'] = age
        if age > 300:
            result['errors'].append('tick_stale')
    if valid_error:
        result['last_error_empty'] = not error
        if error:
            result['errors'].append('scheduler_error')
    if health.get('ok') is False:
        result['errors'].append('scheduler_unhealthy')
    return result


def notify(message):
    # Fixed script, message passed as argv: never evaluate HTTP text as AppleScript.
    script = ('on run argv\n display notification (item 1 of argv) '
              'with title "内容工作台看门狗"\nend run')
    try:
        completed = subprocess.run(['/usr/bin/osascript', '-e', script, message],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)
        return completed.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def check_once(log_dir, port=8766, timeout=5, notifier=notify, clock=time.time):
    log_dir = Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (log_dir / 'studio-watchdog.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return None
        now = clock()
        result = probe(port, timeout)
        state_path = log_dir / 'studio-watchdog.state.json'
        try:
            state = json.loads(state_path.read_text())
            if not isinstance(state, dict):
                state = {}
        except (OSError, ValueError):
            state = {}
        previous = state.get('errors', [])
        old_times = state.get('last_notifications', {})
        # Whitelist state too, so corrupt content never reaches output/notification.
        times = {code: value for code, value in old_times.items()
                 if code in MESSAGES and type(value) in (int, float) and math.isfinite(value)} if isinstance(old_times, dict) else {}
        due = [code for code in result['errors']
               if code not in times or now-times[code] >= COOLDOWN_SECONDS or now < times[code]]
        notification = 'suppressed' if result['errors'] else 'none'
        if due:
            sent = notifier('；'.join(MESSAGES[code] for code in due))
            notification = 'sent' if sent else 'failed'
            if sent:
                times.update({code: now for code in due})
        elif not result['errors'] and (previous or state.get('recovery_pending')):
            sent = notifier('已恢复：工作台服务与调度器心跳正常')
            notification = 'recovery_sent' if sent else 'failed'
        next_state = {'errors': result['errors'], 'last_notifications': times,
                      'recovery_pending': not result['errors'] and notification == 'failed'}
        temp_path = state_path.with_suffix('.tmp')
        temp_path.write_text(json.dumps(next_state, ensure_ascii=False) + '\n')
        temp_path.chmod(0o600)
        temp_path.replace(state_path)
        record = {'time': datetime.fromtimestamp(now, timezone.utc).isoformat(), **result,
                  'conclusion': '异常' if result['errors'] else 'ok', 'notification': notification}
        line = json.dumps(record, ensure_ascii=False, allow_nan=False) + '\n'
        log_path = log_dir / 'studio-watchdog.log'
        if log_path.exists() and log_path.stat().st_size + len(line.encode()) > MAX_LOG_BYTES:
            log_path.replace(log_path.with_suffix('.log.1'))
        with log_path.open('a') as log:
            log.write(line)
        log_path.chmod(0o600)
        return record


def main():
    parser = argparse.ArgumentParser(description='工作台只读健康检查')
    parser.add_argument('--log-dir', type=Path, default=Path(__file__).resolve().parents[1] / 'workspace/logs')
    parser.add_argument('--port', type=int, default=8766)
    parser.add_argument('--timeout', type=float, default=5)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535 or not math.isfinite(args.timeout) or not 0 < args.timeout <= 5:
        parser.error('端口须为 1–65535，超时须大于 0 且不超过 5 秒')
    record = check_once(args.log_dir, args.port, args.timeout)
    return 1 if record and record['errors'] else 0


if __name__ == '__main__':
    raise SystemExit(main())
