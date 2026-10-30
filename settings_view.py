"""设置总览：只读本地快照；不初始化连接器、不恢复任务、不联网。"""
from contextlib import closing
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import tempfile
from urllib.parse import parse_qs

LOGS = {'service': 'logs/service.log', 'service-error': 'logs/service-error.log',
        'video-radar': 'video-radar-launchd.log'}
SERVICES = {
    'com.servasyy.article-studio': ('logs/service.log', 'logs/service-error.log'),
    'com.servasyy.radar-monitor': ('twitter-radar/monitor/monitor.log',),
    'com.servasyy.video-radar': ('video-radar-launchd.log',),
    'com.servasyy.wechat-radar': ('wechat-source/radar-service.log', 'wechat-source/radar-service-error.log'),
    'com.servasyy.wechat-source': ('wechat-source/service.log', 'wechat-source/service-error.log'),
}
SOURCES = [('twitter', '推特雷达', ('twitter_radar',), '/twitter-radar.html'),
           ('grok', 'Grok Bot', ('grok_bot',), '/twitter-radar.html'),
           ('aisa', 'AIsa', ('aisa',), '/clues.html'),
           ('video', '视频榜', ('video_radar',), '/video-radar.html'),
           ('wechat', '微信雷达', ('wechat_radar', 'wechat_clues'), '/clues.html'),
           ('github', 'GitHub', ('github_clues',), '/github.html'),
           ('intel', 'intel', ('intel_wechat_mp',), '/intel.html')]
SENSITIVE = re.compile(r'key|token|secret|authorization|cookie', re.I)
MAX_TAIL_BYTES = 512 * 1024


def _live_workspace(workspace):
    root = Path(workspace).resolve()
    return (root == Path(__file__).resolve().parent / 'workspace'
            and not any(root.is_relative_to(p.resolve()) for p in (Path('/tmp'), Path(tempfile.gettempdir()))))


def service_log_paths(workspace, label):
    root = Path(workspace)
    if _live_workspace(root):
        if label == 'com.servasyy.article-studio':
            folder = Path.home() / 'Library/Logs/ArticleStudio'
            return [folder / 'service.log', folder / 'service-error.log']
        if label == 'com.servasyy.radar-monitor':
            return [Path('/tmp/com.servasyy.radar-monitor.stdout.log'), Path('/tmp/com.servasyy.radar-monitor.stderr.log')]
    return [root / p for p in SERVICES[label]]


def redact(text):
    # 敏感行保留时间等前缀，到字段名即截断；同时遮住 Bearer、多 cookie、嵌套 JSON。
    # 无法可靠解析的多行日志也不回显孤立的 Bearer/常见密钥。
    lines = []
    for line in str(text).splitlines():
        match = SENSITIVE.search(line)
        if match:
            line = line[:match.start()] + '***'
        line = re.sub(r'(?i)\bBearer\s+\S+|\bsk-[\w-]+', '***', line)
        lines.append(line)
    return '\n'.join(lines)


def _json(path):
    try:
        if path.is_symlink() or path.stat().st_size > 8 * 1024 * 1024:
            return {}
        value = json.loads(path.read_text())
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _rows(path, sql, args=()):
    if not path.is_file():
        return []
    try:
        with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=.1)) as db:
            db.row_factory = sqlite3.Row
            return [dict(row) for row in db.execute(sql, args)]
    except sqlite3.Error:
        return []


def _latest(values):
    return max((v for v in values if isinstance(v, str) and v), default=None)


def _twitter_success(workspace, state):
    # runs 的键只是调度小时槽位；完成时间必须取该成功轮次的原始快照。
    runs = state.get('runs', {})
    if not isinstance(runs, dict):
        return None, 'state.json 的 runs 格式异常'
    latest = next((run for _, run in sorted(runs.items(), reverse=True)
                   if isinstance(run, dict) and run.get('success') is True), None)
    if not latest:
        return None, 'state.json 未记录成功轮次'
    run_id = latest.get('run_id')
    if not isinstance(run_id, str) or not re.fullmatch(r'[0-9]{8}-[0-9]{6}-[a-zA-Z0-9]+', run_id):
        return None, '成功轮次的 run_id 缺失或格式异常'
    day = f'{run_id[:4]}-{run_id[4:6]}-{run_id[6:8]}'
    path = Path(workspace) / 'twitter-radar/snapshots' / day / (run_id + '.json')
    data = _json(path)
    meta = data.get('run', data)
    value = meta.get('finished_at') if isinstance(meta, dict) else None
    try:
        if meta.get('run_id') != run_id or not isinstance(value, str):
            raise ValueError
        # 不接受小时槽位、无时区时间，也不为缺失字段补零。
        if not re.search(r'T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$', value):
            raise ValueError
        datetime.fromisoformat(value.replace('Z', '+00:00'))
    except (ValueError, TypeError, AttributeError):
        return None, f'{path.relative_to(workspace)} 缺失或 finished_at/run_id 无效'
    return value, f'{path.relative_to(workspace)} 的 run.finished_at（run_id={run_id}）'


def _aisa_success(workspace):
    import grok_bot_radar as grok
    # 与 today_view.snapshot 的 A 来源共用读取器、路径；成功时间保留 ISO 时区。
    path = grok.BUNDLE_PATH if _live_workspace(workspace) else Path(workspace).parent / 'public/twitter-radar-bundle.json'
    try:
        bundle, _ = grok._read_json(path)
        if not isinstance(bundle, dict) or not any(isinstance(bundle.get(k), list) for k in ('tweets', 'vip_tweets')):
            raise ValueError
        value = bundle.get('collected_at')
        if not isinstance(value, str) or not value:
            raise ValueError
        return value, 'public/twitter-radar-bundle.json 的 collected_at（与今日页共用读取器；sources.A.updated_at 为同一采集时刻）'
    except (OSError, ValueError, TypeError):
        return None, 'public/twitter-radar-bundle.json 缺失、损坏或未记录更新时间'


def _intel_error(row):
    detail = str(row.get('detail') or row.get('reason') or row['status'])
    # intel 的正常 partial 也可能写“保存 N 篇，失败 0 篇，未处理 M 篇”。
    # 零失败计数或明确否定不能作为失败证据；原文仍完整保留用于显示。
    signals = re.sub(r'失败\s*[:：]?\s*0(?!\d)|(?:没有|并无|无|未发现)(?:失败|异常)', '', detail)
    if row['status'] == 'partial' and not re.search(
            r'200013|限流|失败|异常|超时|验证码|\b(?:rate_limited|failed|error|timeout)\b', signals, re.I):
        return None
    return redact(f"{row.get('finished_at') or row.get('started_at') or '时间未知'} · {row['status']} · {detail}")


def schedule_snapshot(workspace):
    path = Path(workspace) / 'schedules/tasks.db'
    tasks = []
    for row in _rows(path, 'SELECT id,data FROM tasks ORDER BY rowid DESC'):
        try:
            data = json.loads(row['data'])
            task = {'id': row['id'], 'name': redact(data.get('name', '')), 'kind': data.get('kind'),
                    'enabled': data.get('enabled') is True}
            task['_body'] = data.get('body', '')  # 仅分类；绝不发给前端。
            tasks.append(task)
        except (ValueError, TypeError):
            continue
    return tasks, path


def overview(server):
    workspace = Path(server.store.workspace)
    root = workspace.parent
    tasks, schedule_db = schedule_snapshot(workspace)
    sources = []
    # 只复用已经存在的缓存，不访问 store.collect / store.intel 属性。
    collect = getattr(server.store, '_collect', None)
    cache = dict(getattr(collect, '_status_cache', {})) if collect is not None else {}
    for identity, label, kinds, link in SOURCES:
        linked = [t for t in tasks if t['kind'] in kinds or
                  t['kind'] == 'script' and any(word in t['_body'] for word in {
                      'twitter': ('radar_monitor.py',), 'grok': ('grok-bot', 'grok_bot'),
                      'aisa': ('aisa',), 'video': ('video_radar.py',),
                      'wechat': ('wechat_radar.py',), 'github': ('github_source.py',),
                      'intel': ('intel_wechat',)}[identity])]
        successes, errors = [], []
        for task in linked:
            successes += [r['finished_at'] for r in _rows(schedule_db,
                "SELECT finished_at FROM runs WHERE task_id=? AND status='succeeded' ORDER BY rowid DESC LIMIT 1", (task['id'],))]
            errors += [r['status'] + ' · ' + str(r['finished_at'] or r['started_at']) for r in _rows(schedule_db,
                "SELECT status,started_at,finished_at FROM runs WHERE task_id=? AND status IN ('failed','timeout','interrupted') ORDER BY rowid DESC LIMIT 1", (task['id'],))]
        enabled = any(t['enabled'] for t in linked) if linked else None
        evidence = ['schedules/tasks.db 的 tasks/runs（任务启用与成功/失败记录；不等同于采集成功）' if linked
                    else 'schedules/tasks.db 的 tasks（无匹配任务）']
        if identity == 'twitter':
            config = _json(root / 'scripts/radar_monitor_config.json')
            if type(config.get('enabled')) is bool and not linked:
                enabled = config['enabled']
            state = _json(workspace / 'twitter-radar/monitor/state.json')
            success, detail = _twitter_success(workspace, state)
            successes = [success]
            if state.get('fault_active'):
                errors.append('雷达连续失败，等待核对')
            evidence += ['scripts/radar_monitor_config.json 与 twitter-radar/monitor/state.json', detail]
        elif identity == 'grok':
            import grok_bot_radar
            for kind in grok_bot_radar.KINDS:
                folder = grok_bot_radar.reports_dir() / kind
                if folder.is_dir() and not folder.is_symlink():
                    # 只读最近报告；不调用聚合器扫描帖子全文。
                    paths = sorted(folder.glob('*.json'), key=lambda p: p.stat().st_mtime, reverse=True)[:1]
                    for path in paths:
                        data = _json(path)
                        if isinstance(data.get('posts', data.get('entries')), list):
                            successes.append(data.get('collected_at') or data.get('generated_at'))
            evidence += [f'{grok_bot_radar.reports_dir()} 各类目录中最近 *.json 报告的 collected_at/generated_at']
        elif identity == 'aisa':
            success, detail = _aisa_success(workspace)
            successes = [success]
            evidence += [detail]
        elif identity == 'video':
            page = _json(workspace / 'video-radar/page.json')
            for row in page.get('sources', {}).values():
                if isinstance(row, dict):
                    successes.append(row.get('last_success_at'))
                    if row.get('status') in ('failed', 'partial'):
                        errors.append(redact(row.get('note') or row['status']))
            evidence += ['video-radar/page.json 的 sources']
        elif identity == 'github':
            for row in _rows(workspace / 'github/github.db', 'SELECT data FROM state'):
                try:
                    state = json.loads(row['data'])
                    successes.append(state.get('last_capture'))
                    if state.get('error'):
                        errors.append(redact(state['error']))
                except (ValueError, TypeError):
                    pass
            evidence += ['github/github.db 的 state']
        elif identity == 'intel':
            path = workspace / 'intel/intel.db'
            successes += [r['finished_at'] for r in _rows(path, "SELECT finished_at FROM jobs WHERE status IN ('done','completed','succeeded') ORDER BY rowid DESC LIMIT 1")]
            for row in _rows(path, "SELECT detail,reason,status,started_at,finished_at FROM jobs WHERE status IN ('error','failed','rate_limited','partial') ORDER BY rowid DESC"):
                error = _intel_error(row)
                if error:
                    errors.append(error)
                    break
            evidence += ['intel/intel.db 的 jobs；不调用 status 或任务恢复']
        for key, value in cache.items():
            platform = key[0] if isinstance(key, tuple) else key
            if platform not in {'wechat': ('wechat_msg',), 'github': ('github',), 'intel': ('wechat_mp',)}.get(identity, ()):
                continue
            record = value[2] if len(value) > 2 and isinstance(value[2], dict) else {}
            successes.append((record.get('extra') or {}).get('last_synced'))
            if record.get('available') is False:
                errors.append(redact(record.get('detail', '状态缓存不可用')))
            evidence.append('既有 collect 状态缓存（不刷新）')
        reason = '；'.join(evidence) + '。未记录的时间/错误显示未知；未有 schedules 或显式 enabled 字段，无法确认启用状态。'
        sources.append({'id': identity, 'name': label, 'enabled': enabled,
                        'last_success': _latest(successes), 'last_error': '；'.join(errors) or None,
                        'reason': reason, 'href': link,
                        'tasks': [{k: v for k, v in t.items() if k != '_body'} for t in linked]})
    services = []
    for label in SERVICES:
        times = []
        for path in service_log_paths(workspace, label):
            try:
                times.append(path.stat().st_mtime)
            except OSError:
                pass
        services.append({'label': label, 'last_log_at': datetime.fromtimestamp(max(times), timezone.utc).isoformat() if times else None})
    return {'sources': sources, 'schedule_task_count': len(tasks), 'services': services,
            'mode': '只读快照；实时状态请打开来源页面'}


def logs(workspace, query):
    if set(query) - {'name', 'lines'} or any(len(v) != 1 for v in query.values()):
        raise ValueError('只允许 name 和 lines 各一个')
    name = query.get('name', ['service'])[0]
    number = query.get('lines', ['100'])[0]
    if name not in LOGS or not re.fullmatch(r'[0-9]{1,3}', number) or not 1 <= int(number) <= 500:
        raise ValueError('日志名称无效，行数须为 1 至 500')
    root = Path(workspace).resolve()
    path = root / LOGS[name]
    resolved = path.resolve()
    allowed = {root / LOGS[name]}
    if resolved not in allowed or path.is_symlink():
        raise ValueError('日志路径无效')
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0))
    except FileNotFoundError:
        return {'name': name, 'lines': [], 'missing': True, 'truncated': False}
    with os.fdopen(fd, 'rb') as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError('日志须为普通文件')
        end = stream.seek(0, 2)
        start = max(0, end - MAX_TAIL_BYTES)
        stream.seek(start)
        data = stream.read(MAX_TAIL_BYTES)
    # 截断开头的不完整行可能缺少敏感字段名，整行丢弃。
    if start:
        data = data.partition(b'\n')[2]
    result = redact(data.decode('utf-8', errors='replace')).splitlines()[-int(number):]
    return {'name': name, 'lines': result, 'missing': False, 'truncated': start > 0}


def get_route(handler, parts):
    query = parse_qs(parts.query, keep_blank_values=True)
    if parts.path.endswith('/overview'):
        if query:
            raise ValueError('总览不接受参数')
        return handler.reply(200, overview(handler.server))
    return handler.reply(200, logs(handler.server.store.workspace, query))
