"""Local catch-up receipts. No HTTP action, credentials or generic command executor.

Owner-managed schedules are observed only. Grok adapters are deliberately absent
until the coordinator supplies an authorized trigger; an unavailable receipt is
never a successful run. All times are UTC seconds, source schedules use Shanghai.
"""
from __future__ import annotations
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
import fcntl
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import stat
import subprocess
import tempfile

GROK = frozenset(('grok-daily', 'grok-alerts', 'grok-interest'))
SOURCES = GROK | {'twitter-radar', 'video-radar', 'wechat-radar', 'linuxdo'}
LABELS = dict(idle='无需补跑', waiting='等待宽限期', running='补跑中', completed='已补',
              unavailable='不可补', failed='补跑失败', unknown='状态待确认', fault='来源故障')
REASONS = dict(report_invalid='本时段报告损坏或格式无效，需排查来源', report_unreadable='产出不可读，未触发补跑', no_authorized_trigger='没有授权的触发入口', owner_managed='由原调度器补跑，不另起任务',
    owner_failed='原任务运行失败，需排查故障', owner_disabled='原任务已停用，不自动触发',
    state_invalid='补跑账本不可用，已停止触发', prior_attempt_uncertain='上次触发结果待确认，不重复触发',
    trigger_failed='补跑触发失败，不重复触发', trigger_accepted='补跑已受理，等待产出',
    output_received='产出已到达', output_present='已有本时段产出，不重复补跑',
    grace_period='计划后的宽限期尚未结束', clock_reversed='系统时间回退，已停止触发',
    owner_regular_running='原任务按计划运行，没有补跑',
    owner_pending='等待原调度器处理逾期任务', no_sleep_evidence='缺少休眠证据，不把故障当成休眠补跑')
MAX_BYTES = 512 * 1024


def finite(value):
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def iso(value):
    return datetime.fromtimestamp(value, timezone.utc).isoformat() if finite(value) else None


def epoch(value):
    try:
        moment = datetime.fromisoformat(value.replace('Z', '+00:00'))
        result = moment.timestamp() if moment.tzinfo is not None else None
        return result if finite(result) else None
    except (ValueError, TypeError, AttributeError, OverflowError):
        return None


def sleep_snapshot():
    """Fixed, read-only Darwin keys; do not print or retain sysctl output."""
    values = []
    try:
        for key in ('kern.sleeptime', 'kern.waketime'):
            p = subprocess.run(['/usr/sbin/sysctl', '-n', key], capture_output=True, text=True, timeout=1, check=True)
            match = re.match(r'^\{ sec = ([0-9]{1,12}), usec = [0-9]{1,6} \}', p.stdout)
            if match is None:
                return None
            values.append(int(match[1]))
    except (OSError, subprocess.SubprocessError):
        return None
    return {'slept_at': values[0], 'woke_at': values[1]} if 0 < values[0] < values[1] else None


def _json_file(path, maximum=MAX_BYTES):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as f:
        info = os.fstat(f.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > maximum:
            raise ValueError('invalid')
        return json.loads(f.read(maximum + 1))


def report_time(path, now):
    """Only schema + declared generated time; never infer content success from mtime."""
    try:
        d = _json_file(path, 5 * 1024 * 1024)
        if (not isinstance(d, dict) or not any(isinstance(d.get(k), list) for k in ('items', 'posts', 'entries'))
                or any(k in d and not isinstance(d[k], list) for k in ('items', 'posts', 'entries'))):
            return None
        from grok_bot_radar import stamp as grok_stamp
        moment = grok_stamp(d.get('generated_at')) or grok_stamp(d.get('collected_at'))
        stamp = moment.timestamp() if moment is not None else None
        return stamp if stamp is not None and stamp <= now and path.stat().st_mtime <= now else None
    except (OSError, ValueError, TypeError, RecursionError):
        return None


def grok_output(reports, task, due, upcoming, now, evidence=None):
    evidence = evidence if evidence is not None else {}
    folder = Path(reports) / task['folder']
    if folder.is_symlink():
        evidence['input_error'] = 'report_unreadable'
        return None
    latest = None
    try:
        for i, p in enumerate(folder.iterdir()):
            if i >= 20000:
                evidence['input_error'] = 'report_unreadable'
                return None
            if not re.fullmatch(task['pattern'], p.name) or p.suffix != '.json':
                continue
            if p.is_symlink():
                evidence['input_error'] = 'report_unreadable'
                continue
            value = report_time(p, now)
            # A produced but malformed report is a source fault, not a missed
            # invocation. Mtime is used only to reject unsafe retriggering.
            try:
                modified = p.stat().st_mtime
                if value is None and (modified > now or due <= modified < min(upcoming, now + .001)):
                    evidence['input_error'] = 'report_invalid'
            except OSError:
                evidence['input_error'] = 'report_unreadable'
            # A later slot must not be credited to an earlier one. The current
            # slot includes late output after midnight, using declared time.
            if value is not None and due <= value < upcoming:
                if task['folder'] in ('daily', 'alerts', 'interest'):
                    local = datetime.fromtimestamp(value, ZoneInfo('Asia/Shanghai'))
                    output_day = local.date() if local.strftime('%H:%M') >= min(task['times']) else (local - timedelta(days=1)).date()
                    if p.name[:10] != output_day.isoformat():
                        continue
                latest = max(latest or value, value)
    except FileNotFoundError:
        return None
    except OSError:
        evidence['input_error'] = 'report_unreadable'
        return None
    return latest


class CatchupLedger:
    def __init__(self, workspace, adapters=None):
        self.folder = Path(workspace) / 'catchup'
        self.adapters = dict(adapters or {})
        if set(self.adapters) - GROK:
            raise ValueError('补跑适配器来源无效')

    @contextmanager
    def _locked(self):
        # Never follow a workspace/catchup/lock symlink into another directory.
        if self.folder.parent.is_symlink() or self.folder.is_symlink():
            raise ValueError('invalid')
        self.folder.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd = os.open(self.folder / '.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise ValueError('invalid')
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                yield None
                return
            yield fd
        finally:
            os.close(fd)

    def _load(self):
        try:
            d = _json_file(self.folder / 'state.json')
        except FileNotFoundError:
            return {'version': 1, 'checked_at': None, 'slots': {}, 'owner_due': {}}
        if (not isinstance(d, dict) or d.get('version') != 1 or not isinstance(d.get('slots'), dict)
                or not isinstance(d.get('owner_due'), dict) or len(d['slots']) > 2048):
            raise ValueError('invalid')
        for key, row in d['slots'].items():
            if (not isinstance(key, str) or not isinstance(row, dict) or row.get('source_id') not in SOURCES
                    or not finite(row.get('due')) or row.get('status') not in LABELS
                    or type(row.get('attempts')) is not int or row['attempts'] not in (0, 1)
                    or row.get('reason') not in REASONS or row.get('cause') not in ('sleep', 'offline', 'overdue', 'missing')
                    or type(row.get('owner_managed')) is not bool
                    or key != row['source_id'] + ':' + str(int(row['due'])) or row.get('slot') != key
                    or row.get('run_id') is not None and (not isinstance(row['run_id'], str) or re.fullmatch(r'[A-Za-z0-9_-]{1,64}', row['run_id']) is None)):
                raise ValueError('invalid')
        if d.get('checked_at') is not None and not finite(d['checked_at']):
            raise ValueError('invalid')
        # Reject unknown persisted fields instead of forwarding their text to an API.
        keep = ('source_id', 'due', 'slot', 'status', 'reason', 'cause', 'attempts', 'owner_managed', 'run_id')
        d['slots'] = {k: {name: row.get(name) for name in keep} for k, row in d['slots'].items()}
        d['owner_due'] = {k: v for k, v in d['owner_due'].items() if isinstance(k, str) and re.fullmatch(r'[A-Za-z0-9_-]{1,64}', k) and finite(v)}
        return d

    def _save(self, d):
        destination = self.folder / 'state.json'
        if destination.is_symlink():
            raise ValueError('invalid')
        fd, name = tempfile.mkstemp(prefix='.state-', dir=self.folder)
        try:
            with os.fdopen(fd, 'w') as f:
                json.dump(d, f, ensure_ascii=False, allow_nan=False)
                f.flush(); os.fsync(f.fileno())
            os.replace(name, destination)
        finally:
            if os.path.exists(name):
                os.unlink(name)

    @staticmethod
    def view(row):
        return {k: row.get(k) for k in ('source_id', 'slot', 'status', 'reason', 'cause', 'attempts', 'owner_managed', 'run_id')} | {
            'status_label': LABELS[row['status']], 'detail': REASONS[row['reason']], 'expected_at': iso(row['due'])}

    def refresh(self, observations, now, sleep=None, owner_due=None):
        try:
            with self._locked() as locked:
                if locked is None:
                    return {'status': 'busy', 'sources': [], 'detail': '其他进程正在检查，不重复触发', 'action_endpoint': False}
                state = self._load()
                prior = state.get('checked_at')
                reversed_clock = finite(prior) and now < prior
                rows = []
                for observation in observations:
                    source, due = observation['source_id'], observation['due']
                    if source not in SOURCES or not finite(due) or due > now:
                        continue
                    key = source + ':' + str(int(due))
                    old = state['slots'].get(key)
                    wake = (isinstance(sleep, dict) and finite(sleep.get('slept_at')) and finite(sleep.get('woke_at'))
                        and sleep['slept_at'] <= due < sleep['woke_at'] <= now)
                    gap = finite(prior) and prior < due <= now and now - prior > 600
                    started = observation.get('started_at')
                    late_owner = observation.get('owner_managed') and finite(started) and started > due + 60
                    row = dict(old or {}, source_id=source, due=due, slot=key,
                        cause=('sleep' if wake else (old or {}).get('cause') if (old or {}).get('cause') in ('sleep', 'offline', 'overdue') else 'offline' if gap else 'overdue' if late_owner else 'missing'),
                        attempts=(old or {}).get('attempts', 0), owner_managed=observation.get('owner_managed', False),
                        run_id=observation.get('run_id'))
                    received = finite(observation.get('output_at')) and due <= observation['output_at'] <= now
                    if reversed_clock:
                        row.update(status='unavailable', reason='clock_reversed')
                    elif observation.get('input_error') in ('report_invalid', 'report_unreadable'):
                        row.update(status='fault', reason=observation['input_error'])
                    elif received:
                        # A snapshot file proves output, not when launchd started.
                        is_late_owner = row['owner_managed'] and late_owner
                        row.update(status='completed' if row['attempts'] or is_late_owner else 'idle',
                                   reason='output_received' if row['attempts'] or is_late_owner else 'output_present')
                    elif not observation.get('enabled', True):
                        row.update(status='unavailable', reason='owner_disabled')
                    elif now <= due + observation['grace_seconds']:
                        row.update(status='waiting', reason='grace_period')
                    elif row['owner_managed']:
                        status = observation.get('run_status')
                        if status == 'running':
                            row.update(status='running' if late_owner else 'idle', reason='owner_managed' if late_owner else 'owner_regular_running')
                        elif status in ('failed', 'timeout', 'cancelled', 'interrupted'):
                            row.update(status='failed' if late_owner else 'fault', reason='owner_failed')
                        else:
                            row.update(status='unknown', reason='owner_pending')
                    elif row['attempts']:
                        # Accepted does not mean completed. A crash between the
                        # durable claim and response is uncertain, never resent.
                        row.update(status='running' if old and old['reason'] == 'trigger_accepted' else 'unknown',
                                   reason='trigger_accepted' if old and old['reason'] == 'trigger_accepted' else 'prior_attempt_uncertain')
                    elif source not in self.adapters:
                        row.update(status='unavailable', reason='no_authorized_trigger')
                    elif row['cause'] not in ('sleep', 'offline'):
                        row.update(status='unavailable', reason='no_sleep_evidence')
                    else:
                        # At most one physical trigger per source+slot, even
                        # across processes, restart, repeated polls or crashes.
                        row.update(status='unknown', reason='prior_attempt_uncertain', attempts=1)
                        state['slots'][key] = row
                        self._save(state)  # If persistence fails, never trigger.
                        try:
                            accepted = self.adapters[source](source, iso(due)) is True
                        except Exception:
                            accepted = False
                        row.update(status='running' if accepted else 'failed', reason='trigger_accepted' if accepted else 'trigger_failed')
                    state['slots'][key] = row
                    rows.append(self.view(row))
                if not reversed_clock:
                    state['checked_at'] = now
                    state['owner_due'] = owner_due or state['owner_due']
                    # Old slots cannot be selected again: retain 31 days and
                    # refuse dispatch on clock rollback rather than forgetting it.
                    state['slots'] = {k: v for k, v in state['slots'].items() if v['due'] >= now - 31 * 86400}
                self._save(state)
                return {'status': 'ok', 'sources': rows, 'checked_at': iso(now), 'action_endpoint': False}
        except (OSError, ValueError, TypeError, RecursionError, KeyError, OverflowError):
            return {'status': 'unavailable', 'sources': [], 'detail': REASONS['state_invalid'], 'action_endpoint': False}

    def owner_due(self):
        try:
            with self._locked() as locked:
                return dict(self._load()['owner_due']) if locked is not None else {}
        except (OSError, ValueError, TypeError, KeyError):
            return {}


def native_observations(workspace, now, prior_due=None):
    """Read existing task claims; never save/control/claim a task or execute its body."""
    path = Path(workspace) / 'schedules/tasks.db'
    if not path.is_file() or path.is_symlink():
        return [], {}
    prior_due = prior_due or {}
    observations, following = [], {}
    try:
        with sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=.2) as db:
            db.row_factory = sqlite3.Row
            for taskrow in db.execute('SELECT id,data,next_run FROM tasks'):
                try:
                    task = json.loads(taskrow['data'])
                    body = task.get('body', '')
                    source = ('wechat-radar' if task.get('kind') == 'wechat_radar' else
                              'video-radar' if task.get('kind') == 'script' and 'scripts/launchd/run-video-radar.sh' in body else
                              'linuxdo' if task.get('kind') == 'script' and 'linuxdo_report.py' in body else None)
                    if source is None:
                        continue
                    identity = taskrow['id']
                    runs = list(db.execute("SELECT id,status,trigger,started_at,finished_at,output FROM runs WHERE task_id=? AND trigger='scheduled' ORDER BY started_at DESC LIMIT 2", (identity,)))
                    latest = runs[0] if runs else None
                    next_run = taskrow['next_run']
                    if finite(next_run):
                        following[identity] = next_run
                    if task.get('frequency') == 'daily':
                        from external_tasks import occurrences
                        _, due, _ = occurrences({'times': (task['at'],), 'weekdays': task.get('weekdays', range(7))}, now)
                    elif finite(prior_due.get(identity)) and prior_due[identity] <= now:
                        due = prior_due[identity]
                    else:
                        due = next_run
                    if not finite(due) or due > now:
                        continue
                    started = epoch(latest['started_at']) if latest else None
                    matching = started is not None and started >= due - 2
                    status = latest['status'] if matching else None
                    ended = epoch(latest['finished_at']) if matching else None
                    if status == 'partial':
                        try:
                            output = json.loads(latest['output'])
                            if not isinstance(output, dict) or output.get('error_kind') or output.get('time_budget') is not True:
                                status = 'failed'
                        except (ValueError, TypeError):
                            status = 'failed'
                    output_at = ended if status in ('succeeded', 'partial') else None
                    observations.append(dict(source_id=source, due=due, grace_seconds=60,
                        output_at=output_at, owner_managed=True, enabled=task.get('enabled') is True,
                        run_id=latest['id'] if matching else None, run_status=status, started_at=started))
                except (ValueError, TypeError, KeyError, OverflowError):
                    continue
    except sqlite3.Error:
        return [], {}
    return observations, following


def status_snapshot(workspace):
    """Bounded read-only projection for page/API consumers; never initialize state."""
    ledger = CatchupLedger(workspace)
    try:
        if ledger.folder.parent.is_symlink() or ledger.folder.is_symlink():
            raise ValueError('invalid')
        state = ledger._load()
        latest = {}
        for row in state['slots'].values():
            source = row['source_id']
            if source not in latest or row['due'] > latest[source]['due']:
                latest[source] = row
        return {'status': 'ok' if state['checked_at'] is not None else 'waiting', 'checked_at': iso(state['checked_at']),
                'sources': [ledger.view(latest[k]) for k in sorted(latest)], 'action_endpoint': False}
    except (OSError, ValueError, TypeError, KeyError, RecursionError, OverflowError):
        return {'status': 'unavailable', 'sources': [], 'detail': REASONS['state_invalid'], 'action_endpoint': False}
