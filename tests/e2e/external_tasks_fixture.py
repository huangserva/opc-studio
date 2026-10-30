"""H1 隔离 HTTP：真实 Handler/Store/ScheduledTasks，所有数据与时钟在 /tmp。"""
from datetime import datetime
import os
from pathlib import Path
import sys
from types import SimpleNamespace
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import grok_bot_radar
import server
from external_tasks import ExternalTasks, SERVICES
from scheduled_tasks import ScheduledTasks

root = Path(sys.argv[1]); port = int(sys.argv[2])
workspace = root / 'workspace'; workspace.mkdir()
reports = root / 'reports'; (reports / 'interest').mkdir(parents=True)
grok_bot_radar.reports_dir = lambda: reports
(reports / 'interest-topics.md').write_text('# 兴趣清单\n\n## 当前方向\n- H1 隔离测试\n\n## 说明\n只用于测试\n')
clock = root / 'clock'; clock.write_text('2026-10-30T11:31:00+08:00')
prior = reports / 'interest/2026-10-29-2240.json'; prior.write_text('{}')
t = datetime.fromisoformat('2026-10-29T22:40:00+08:00').timestamp(); os.utime(prior, (t, t))
assistant = SimpleNamespace(settings=lambda: {'profiles': [], 'active_profile_id': None})
s = server.ThreadingHTTPServer(('127.0.0.1', port), server.Handler)
s.store = server.Store(workspace); s.assistant = assistant
s.schedules = ScheduledTasks(workspace, assistant, autostart=False)
s.store.schedules = s.schedules
s.schedules.external_tasks = ExternalTasks(workspace, reports,
    clock=lambda: datetime.fromisoformat(clock.read_text()).timestamp(),
    service_reader=lambda: {SERVICES[0]: {'pid': 123, 'exit_code': 0}, SERVICES[1]: {'pid': None, 'exit_code': 9}})
s.schedules.external_tasks.refresh(force=True)
refresh = s.schedules.external_tasks.refresh
def controlled_refresh():
    if (root / 'refresh-failure').exists():
        raise RuntimeError('synthetic-private-sentinel')
    refresh()
s.schedules.external_tasks.refresh = controlled_refresh
s.schedules.save(dict(name='H1 隔离内置任务', kind='script', body="printf 'H1 synthetic run'", cwd=str(root),
    frequency='interval', minutes=60, at='08:00', weekdays=list(range(7)), timeout=5, enabled=False, allow_commands=False))
s.schedules.start()
s.serve_forever()
