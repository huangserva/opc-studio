"""外部来源只读心跳与本机补跑账本；不改变外部任务计划。"""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import re
import stat
import subprocess
import threading
import time
from zoneinfo import ZoneInfo

TZ = ZoneInfo('Asia/Shanghai')
REFRESH_SECONDS = 300
# 归属与产出规则入仓；收藏 digest 的路径尚未确认，绝不猜测。
TASKS = (
    dict(id='wechat-clue-morning', name='微信线索晨报', owner='workbench', times=('08:30',), grace_minutes=60,
         schedule='每天 08:30', folder='clue-morning', rule='workspace/clue-morning/<日期>.json + .md', pattern=r'\d{4}-\d{2}-\d{2}\.json', morning=True),
    dict(id='grok-daily', name='Grok 日报', owner='Grok', times=('09:00',), grace_minutes=60,
         schedule='每天 09:00', folder='daily', rule='daily/<日期>.json', pattern=r'\d{4}-\d{2}-\d{2}\.json'),
    dict(id='grok-alerts', name='Grok 快报', owner='Grok', times=('08:00','12:00','16:00','20:00'), grace_minutes=60,
         schedule='每天 08 / 12 / 16 / 20 点', folder='alerts', rule='alerts/<日期>-*.json', pattern=r'\d{4}-\d{2}-\d{2}-.+\.json'),
    dict(id='grok-interest', name='Grok 兴趣通道', owner='Grok', times=('10:30','22:30'), grace_minutes=60,
         schedule='每天 10:30 / 22:30', folder='interest', rule='interest/<日期>-*.json', pattern=r'\d{4}-\d{2}-\d{2}-.+\.json'),
    dict(id='grok-weekly', name='Grok 周报', owner='Grok', times=('20:00',), weekdays=(6,), grace_minutes=120,
         schedule='周日 20:00', folder='weekly', rule='weekly/ 下计划时点后的新文件', pattern=r'.+'),
    dict(id='grok-digest', name='Grok 收藏 digest', owner='Grok', times=('08:54',), weekdays=(0,), grace_minutes=None,
         schedule='周一 08:54', folder=None, rule='待确认'),
    dict(id='twitter-radar', name='推特雷达', owner='launchd', times=tuple(f'{h:02}:00' for h in range(6,23,2)), grace_minutes=30,
         schedule='每天 06–22 点，每 2 小时', folder='twitter-radar/snapshots', rule='workspace/twitter-radar/snapshots/ 新 JSON 文件', pattern=r'.+\.json', recursive=True),
    dict(id='studio-watchdog', name='工作台看门狗', owner='launchd', times=tuple(f'{h:02}:{m:02}' for h in range(24) for m in (0,15,30,45)), grace_minutes=30,
         schedule='每 15 分钟', folder='logs', rule='workspace/logs/studio-watchdog.log 新行', pattern=r'studio-watchdog\.log', log=True),
)
SERVICES = ('com.servasyy.wechat-radar', 'com.servasyy.wechat-source')
LABELS = dict(ok='正常', waiting='等待中', overdue='逾期', missing='缺失', unconfirmed='待确认', running='运行中', exited='已退出', unknown='未知')


def occurrences(task, now):
    """含当前时点；至少覆盖周任务的前两次、下一次，跨日用当地日历。"""
    local = datetime.fromtimestamp(now, TZ)
    values = []
    for offset in range(-15, 9):
        day = local + timedelta(days=offset)
        if day.weekday() not in task.get('weekdays', range(7)):
            continue
        for at in task['times']:
            hour, minute = map(int, at.split(':'))
            values.append(day.replace(hour=hour, minute=minute, second=0, microsecond=0).timestamp())
    before = sorted(t for t in values if t <= now)
    return before[-2], before[-1], min(t for t in values if t > now)


def service_snapshot():
    """仅 list 的三列，不读 plist、不返回命令输出或错误原文。"""
    try:
        result = subprocess.run(['/bin/launchctl', 'list'], capture_output=True, text=True, timeout=1, check=True)
        found = {}
        for line in result.stdout.splitlines():
            columns = line.split()
            if len(columns) == 3 and columns[2] in SERVICES:
                pid, code, label = columns
                found[label] = dict(pid=int(pid) if pid.isdigit() else None,
                                    exit_code=int(code) if re.fullmatch(r'-?\d+', code) else None)
        return found
    except (OSError, subprocess.SubprocessError):
        return None


def iso(value):
    return datetime.fromtimestamp(value, timezone.utc).isoformat() if value is not None else None


class ExternalTasks:
    def __init__(self, workspace, reports=None, clock=time.time, service_reader=service_snapshot, sleep_reader=None):
        self.workspace = Path(workspace)
        self.reports = Path(reports) if reports is not None else None
        self.clock, self.service_reader = clock, service_reader
        self.lock = threading.Lock()
        self.refresh_lock = threading.Lock()
        self.refreshed = None
        self.last_error = None
        self.last_error_at = None
        self.cache = self._empty()
        self._reports_root = self.reports
        from source_catchup import CatchupLedger
        self.catchup = CatchupLedger(self.workspace)
        if sleep_reader is None:
            from source_catchup import sleep_snapshot
            sleep_reader = sleep_snapshot
        self.sleep_reader = sleep_reader

    @staticmethod
    def row(task):
        return {k: task[k] for k in ('id','name','owner','schedule','rule','grace_minutes')} | {
            'external': True, 'readonly': True, 'timezone': 'Asia/Shanghai', 'last_output_at': None,
            'status': 'unconfirmed' if task['folder'] is None else 'waiting',
            'detail': '产出路径待确认' if task['folder'] is None else '等待首次后台检查'}

    def _empty(self):
        rows = [self.row(t) for t in TASKS]
        rows += [dict(id=label, name=label, owner='launchd', external=True, readonly=True,
                      timezone='Asia/Shanghai', schedule='常驻服务', rule='launchctl list 的 PID / 退出码',
                      grace_minutes=None, last_output_at=None, status='unknown', detail='等待首次后台检查', pid=None, exit_code=None)
                 for label in SERVICES]
        return self._snapshot(rows, None) | {'catchup': {'status': 'waiting', 'sources': [], 'checked_at': None, 'action_endpoint': False}}

    @staticmethod
    def _snapshot(rows, checked):
        for row in rows:
            row['status_label'] = LABELS[row['status']]
        return dict(tasks=rows, checked_at=iso(checked), timezone='Asia/Shanghai', refresh_seconds=REFRESH_SECONDS)

    def snapshot(self):
        # HTTP 只拷贝内存，不扫描目录、不调用 launchctl。
        with self.lock:
            return deepcopy(self.cache) | {'last_error': self.last_error, 'last_error_at': self.last_error_at}

    def mark_refresh_failed(self):
        # 保留上次完整快照；不暴露异常原文，也不污染调度器健康状态。
        with self.lock:
            self.last_error = '外部心跳刷新失败'
            self.last_error_at = datetime.now(timezone.utc).isoformat()

    def _latest(self, task, now):
        if task['owner'] == 'Grok':
            from grok_bot_radar import reports_dir
            self._reports_root = self.reports if self.reports is not None else reports_dir()
            base = self._reports_root / task['folder']
        else:
            base = self.workspace / task['folder']
        latest = None
        scanned = 0
        def scan(folder, depth=0):
            nonlocal latest, scanned
            # 不沿符号链接读取真实目录以外的内容；普通文件读取只用于确认日志新行。
            if folder.is_symlink():
                raise OSError('symlink')
            with os.scandir(folder) as entries:
                for entry in entries:
                    scanned += 1
                    if scanned > 20000:
                        raise OSError('scan limit')
                    if entry.is_symlink():
                        continue
                    if task.get('recursive') and depth == 0 and entry.is_dir(follow_symlinks=False):
                        scan(Path(entry.path), 1)
                        continue
                    if not re.fullmatch(task['pattern'], entry.name) or not entry.is_file(follow_symlinks=False):
                        continue
                    info = entry.stat(follow_symlinks=False)
                    if not stat.S_ISREG(info.st_mode) or info.st_mtime > now:
                        continue
                    if task.get('morning'):
                        from clue_morning import snapshot
                        try:
                            day = datetime.strptime(entry.name[:10], '%Y-%m-%d').replace(tzinfo=TZ)
                        except ValueError:
                            continue
                        report = snapshot(self.workspace, day)
                        if report['status'] != 'ok' or entry.name != report['date'] + '.json':
                            continue
                        try:
                            generated = datetime.fromisoformat(report['generated_at']).timestamp()
                        except (ValueError, TypeError):
                            continue
                        if generated > now or datetime.fromtimestamp(generated, TZ).date() != day.date():
                            continue
                        latest = max(latest or generated, generated)
                        continue
                    if task['owner'] == 'Grok' and task['folder'] in ('daily', 'alerts', 'interest'):
                        # 不取第一段数字冒充日期；完整文件名先验证，日期也须有效且与落盘日期一致。
                        try:
                            date = datetime.strptime(entry.name[:10], '%Y-%m-%d').date()
                        except ValueError:
                            continue
                        local = datetime.fromtimestamp(info.st_mtime, TZ)
                        first = min(task['times'])
                        output_day = local.date() if local.strftime('%H:%M') >= first else (local - timedelta(days=1)).date()
                        if date != output_day:
                            continue
                    output_time = info.st_mtime
                    if task.get('log'):
                        output_time = self._log_output(entry.path)
                        if output_time is None:
                            continue
                    latest = max(latest or output_time, output_time)
        try:
            scan(base)
            return latest, None
        except FileNotFoundError:
            return latest, '产出目录或文件不存在'
        except OSError:
            # 不把路径、异常原文或日志正文放入缓存。
            return None, '产出不可读或扫描范围超限'

    @staticmethod
    def _log_output(path):
        # 无状态：只确认普通文件中存在完整新行，时间沿用文件 mtime；不解析或返回日志正文。
        fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0))
        with os.fdopen(fd, 'rb') as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or not info.st_size:
                return None
            stream.seek(-1, 2)
            return info.st_mtime if stream.read(1) == b'\n' else None

    def refresh(self, force=False):
        # 同一轮只执行一次扫描；请求线程永远不调用此方法。
        with self.refresh_lock:
            now = self.clock()
            if not force and self.last_error is None and self.refreshed is not None and 0 <= now - self.refreshed < REFRESH_SECONDS:
                return
            rows = []
            for task in TASKS:
                row = self.row(task)
                previous, due, upcoming = occurrences(task, now)
                row.update(expected_at=iso(due), next_expected_at=iso(upcoming))
                if task['folder'] is not None:
                    latest, issue = self._latest(task, now)
                    row['last_output_at'] = iso(latest)
                    grace = task['grace_minutes'] * 60
                    if latest is not None and latest >= due:
                        state = 'ok'
                    elif latest is None or latest < previous:
                        state = 'missing'
                    elif now <= due + grace:
                        state = 'waiting'
                    else:
                        state = 'overdue'
                    if latest is None and (issue is None or '不存在' in issue):
                        detail = '从未产出' + ('；' + issue if issue else '')
                    else:
                        detail = issue or ('上一周期也没产出' if state == 'missing' else '按产出文件修改时间判定；不代表内容质量')
                    row.update(status=state, detail=detail)
                rows.append(row)
            # Read-only API consumes this cached envelope; no action route is exposed.
            from source_catchup import (GROK, grok_output, native_observations)
            observations = []
            for task, row in zip(TASKS, rows):
                if task['id'] in GROK and self._reports_root is not None:
                    _, due, upcoming = occurrences(task, now)
                    evidence = {}
                    output = grok_output(self._reports_root, task, due, upcoming, now, evidence)
                    observations.append(dict(source_id=task['id'], due=due,
                        grace_seconds=task['grace_minutes'] * 60, owner_managed=False,
                        output_at=output, input_error=evidence.get('input_error')))
                elif task['id'] == 'twitter-radar':
                    _, due, _ = occurrences(task, now)
                    from source_catchup import epoch
                    observations.append(dict(source_id=task['id'], due=due,
                        grace_seconds=task['grace_minutes'] * 60, owner_managed=True,
                        output_at=epoch(row['last_output_at'])))
            native, following = native_observations(self.workspace, now, self.catchup.owner_due())
            catchup = self.catchup.refresh(observations + native, now, self.sleep_reader(), following)
            by_source = {r['source_id']: r for r in catchup.get('sources', [])}
            for row in rows:
                if row['id'] in by_source:
                    row['catchup'] = by_source[row['id']]
            services = self.service_reader()
            for label in SERVICES:
                found = (services or {}).get(label, {})
                state = 'unknown' if services is None else 'running' if found.get('pid') else 'exited'
                rows.append(dict(id=label, name=label, owner='launchd', external=True, readonly=True,
                                 timezone='Asia/Shanghai', schedule='常驻服务', rule='launchctl list 的 PID / 退出码',
                                 grace_minutes=None, last_output_at=None, status=state,
                                 detail='launchctl list 不可用' if services is None else 'PID / 退出码只读快照' if found else '未在 launchctl list 中发现服务',
                                 pid=found.get('pid'), exit_code=found.get('exit_code')))
            with self.lock:
                self.cache = self._snapshot(rows, now)
                self.cache['catchup'] = catchup
                self.refreshed = now
                self.last_error = None
                self.last_error_at = None
