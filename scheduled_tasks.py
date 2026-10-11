"""Workbench-owned local schedules. SQLite claims, immutable runs, isolated Pi turns."""
import copy
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import signal
import sqlite3
import subprocess
import threading
import time
from types import SimpleNamespace
import uuid
from zoneinfo import ZoneInfo
from events_store import identity

TZ = ZoneInfo('Asia/Shanghai')
MAX_OUTPUT = 65536
RUN_STATUSES = frozenset({'running', 'succeeded', 'partial', 'failed', 'timeout', 'cancelled', 'interrupted'})
# Kinds that must resolve a real model profile before a run is claimed. `script` and
# `intel_wechat_mp` run locally with no model; keeping one list stops claim() from
# handing a model task a None config (intake_cards used to fall out of this list).
MODEL_KINDS = ('pi','wechat_clues','github_clues','wechat_radar','deals_review','intake_cards','outsourced')

class ModelBusy(ValueError):
    """A runnable task waits; no failure receipt or checkpoint is created."""

def stamp():
    return datetime.now(timezone.utc).isoformat()

def next_due(task, now):
    if task['frequency'] == 'interval':
        result = now + task['minutes'] * 60
    else:
        local = datetime.fromtimestamp(now, TZ)
        hour, minute = map(int, task['at'].split(':'))
        result = None
        for offset in range(8):
            date = local + timedelta(days=offset)
            candidate = date.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if candidate.weekday() in task['weekdays'] and candidate.timestamp() > now:
                result = candidate.timestamp(); break
    if task.get('ends_at') and result > datetime.fromisoformat(task['ends_at']).timestamp():
        return None
    return result

class ScheduledTasks:
    def __init__(self, workspace, assistant=None, autostart=True):
        self.folder = Path(workspace) / 'schedules'
        self.folder.mkdir(mode=0o700, parents=True, exist_ok=True); self.folder.chmod(0o700)
        self.db_path = self.folder / 'tasks.db'
        self.lock = threading.RLock(); self.stop_event = threading.Event()
        self.active = {}; self.workers = {}; self.assistant = assistant; self.last_error = None; self.last_tick = None; self.heat_recomputer = None; self.publisher = None; self.wechat_analyzer = None; self.github_analyzer = None; self.radar_analyzer = None; self.deals_analyzer = None; self.intake_analyzer = None; self.intel_analyzer = None; self.outsourced_clues = None
        with self.db() as db:
            db.executescript('''CREATE TABLE IF NOT EXISTS tasks(id TEXT PRIMARY KEY, revision INTEGER, data TEXT, next_run REAL);
            CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY, task_id TEXT, snapshot TEXT, trigger TEXT,
            status TEXT, started_at TEXT, finished_at TEXT, exit_code INTEGER, output TEXT);
            CREATE UNIQUE INDEX IF NOT EXISTS one_active ON runs(task_id) WHERE status='running';''')
            columns={r['name'] for r in db.execute('PRAGMA table_info(runs)')}
            for name in ('clue_id','publication_error'):
                if name not in columns: db.execute('ALTER TABLE runs ADD COLUMN '+name+' TEXT')
            db.execute("UPDATE runs SET status='interrupted',finished_at=?,output=output||? WHERE status='running'",
                       (stamp(), '\n工作台重启中断；未自动重做该次运行。'))
        self.db_path.chmod(0o600)
        self.thread = None
        if autostart:self.start()

    def start(self):
        if self.thread is None:
            self.thread=threading.Thread(target=self.run_loop,daemon=True);self.thread.start()

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.db_path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db: yield db
        finally: db.close()

    def validate(self, payload):
        if not isinstance(payload, dict): raise ValueError('任务格式错误')
        task = {k: copy.deepcopy(payload.get(k)) for k in ('name','kind','body','cwd','frequency','minutes','at','weekdays','ends_at','timeout','enabled','profile_id','allow_commands')}
        task['result_destination']=payload.get('result_destination','runs')
        task['result_category']=payload.get('result_category','other')
        task['clue_id']=payload.get('clue_id','')
        if task['result_destination'] not in ('runs','clues'):raise ValueError('结果位置无效')
        if task['result_category'] not in ('business','technology','topics','projects','other'):raise ValueError('结果分类无效')
        if task['clue_id']:identity(task['clue_id'],'持续更新线索编号')
        for key, maximum in [('name',100),('body',24000),('cwd',4096)]:
            if not isinstance(task[key],str) or not task[key].strip() or len(task[key]) > maximum or '\x00' in task[key]:
                raise ValueError('请填写任务名称、执行内容和有效工作目录')
        task['name'] = task['name'].strip()
        if task['kind']=='outsourced':
            # 外包任务的工作目录就是它的交接子目录，由 slug 推出来，不让调用方自己填。
            from outsourced_tasks import _check_slug, outbox_root, resolved_inside
            task['slug']=_check_slug(payload.get('slug',''))
            task['fallback']=payload.get('fallback','pi')
            if task['fallback'] not in ('pi','none'): raise ValueError('兜底方式只能是 pi 或 none')
            root = outbox_root()
            folder = root / task['slug']
            # 顺序不能反：先判软链逃逸再落盘。实测 mkdir 不会真的在根外建出目录
            # （指向已存在目录时静默通过，指向不存在目录时抛 FileExistsError），
            # 但那样报错会变成「无法创建目录」，把「slug 指到交接根之外」说成了
            # 权限问题。先判能给出真正的原因，也完全不碰文件系统。
            if not resolved_inside(root, folder): raise ValueError('外包任务目录必须位于交接目录之内')
            try:
                folder.mkdir(parents=True, exist_ok=True, mode=0o700)
            except OSError as exc:
                raise ValueError('无法创建外包任务交接目录 %s：%s' % (folder, exc)) from None
            task['cwd']=str(folder.resolve())
        path = Path(task['cwd']).expanduser()
        if not path.is_absolute() or not path.is_dir(): raise ValueError('工作目录必须是本机存在的绝对目录')
        task['cwd'] = str(path.resolve())
        if task['kind'] not in ('script','pi','wechat_clues','github_clues','wechat_radar','deals_review','intake_cards','intel_wechat_mp','recompute_heat','outsourced'): raise ValueError('请选择脚本、Pi、外包、商单分析、创意提炼、公众号订阅或线索分析任务')
        from scheduled_pi import validate_scope
        task.update(validate_scope(payload, task['kind']))
        if task['kind'] in ('wechat_clues','github_clues','wechat_radar'):
            if task['allow_commands'] or task['result_destination']!='runs' or task['clue_id']:raise ValueError('微信分析仅使用受限缓存与原文引用入口，不允许命令或通用结果投递')
            task['wechat_mode']=payload.get('wechat_mode','once')
            if task['wechat_mode'] not in ('once','incremental'):raise ValueError('微信分析范围无效')
            if task['kind']=='wechat_clues':
                task['wechat_review']=payload.get('wechat_review',False)
                if type(task['wechat_review']) is not bool:raise ValueError('会话日复查开关格式错误')
                if task['wechat_review'] and task['wechat_mode']!='incremental':raise ValueError('会话日复查需要持续增量任务')
        if task['kind']=='recompute_heat':
            if task['allow_commands'] or task['result_destination']!='runs' or task['clue_id'] or task['frequency']!='daily':
                raise ValueError('heat 重算仅允许每日本地计算，不允许命令或结果投递')
        if task['kind']=='deals_review':
            if task['allow_commands'] or task['result_destination']!='runs' or task['clue_id']:
                raise ValueError('商单分析只在本机生成提醒与待确认建议，不允许命令或通用结果投递')
        if task['kind']=='intake_cards':
            if task['allow_commands'] or task['result_destination']!='runs' or task['clue_id']:
                raise ValueError('创意提炼只生成候选提示词卡，需黄总确认，不允许命令或通用结果投递')
        if task['kind']=='intel_wechat_mp':
            # 公众号订阅：只按既定限流纪律抓已订阅号的最新一篇页，结果只进 intel.db。
            if task['allow_commands'] or task['result_destination']!='runs' or task['clue_id']:
                raise ValueError('公众号订阅只抓已订阅号的最新文章，不允许命令或通用结果投递')
            if task['frequency']!='daily':
                raise ValueError('公众号订阅按每天一次执行，不支持间隔触发')
        if task['kind']=='outsourced':
            # 外包任务：WorkBuddy 按时把结果写成约定文件，工作台到点只验收并导入；
            # 没有或不合格时才用本机 Pi 兜底。
            if task['allow_commands'] or task['clue_id']:
                raise ValueError('外包任务只按交接文件导入线索，不允许命令或指定线索编号')
        if task['kind']=='wechat_radar':
            value=payload.get('radar_since')
            try:
                date=datetime.fromisoformat(value)
                if date.tzinfo is None or date.timestamp()>time.time():raise ValueError()
            except (TypeError,ValueError):raise ValueError('雷达起点必须是已发生、含时区的时间') from None
            task['radar_since']=date.astimezone(timezone.utc).isoformat()
        if task['frequency'] not in ('interval','daily'): raise ValueError('执行频率无效')
        if type(task['timeout']) is not int or not 5 <= task['timeout'] <= 1800: raise ValueError('运行上限为 5 至 1800 秒')
        if type(task['enabled']) is not bool or type(task['allow_commands']) is not bool: raise ValueError('任务开关格式错误')
        if task['frequency'] == 'interval':
            if type(task['minutes']) is not int or not 1 <= task['minutes'] <= 43200: raise ValueError('间隔为 1 至 43200 分钟')
        else:
            import re
            if not isinstance(task['at'],str) or not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d', task['at']): raise ValueError('请填写有效的北京时间')
            if not isinstance(task['weekdays'],list) or not task['weekdays'] or any(type(n) is not int or n not in range(7) for n in task['weekdays']): raise ValueError('至少选择一个执行日')
            task['weekdays'] = sorted(set(task['weekdays']))
        if task['ends_at']:
            try:
                end = datetime.fromisoformat(task['ends_at'])
                if end.tzinfo is None: raise ValueError()
            except (ValueError, TypeError): raise ValueError('截止时间必须包含时区') from None
            task['ends_at'] = end.isoformat()
        # outsourced 只有开本机兜底时才需要模型配置；fallback=none 纯等 WorkBuddy 交付。
        if task['kind']=='outsourced' and task['fallback']=='none':
            return task
        if task['kind'] in MODEL_KINDS:
            self.model_config(task['profile_id'])
        return task

    def seed_heat_schedule(self,workspace):
        # Preserve existing user controls; registration performs no computation.
        existing=next((t for t in self.listing()['tasks'] if t['kind']=='recompute_heat'),None)
        if existing:return existing
        return self.save({'name':'线索 heat 每日重算','kind':'recompute_heat','body':'只读本地观测并记录 heat 编辑条目，不联网、不调用模型。',
                          'cwd':str(workspace),'frequency':'daily','at':'02:10','weekdays':list(range(7)),
                          'timeout':1800,'enabled':False,'allow_commands':False,'result_destination':'runs'})

    def model_config(self, identity):
        from model_settings import find_profile, public_config
        if not self.assistant: raise ValueError('Pi 运行环境不可用')
        profile = find_profile(self.assistant.model_document(), identity)
        if not public_config(profile['config'])['configured']: raise ValueError('所选模型未配置完成')
        return copy.deepcopy(profile['config'])

    def listing(self):
        with self.lock, self.db() as db:
            settings = self.assistant.settings() if self.assistant else {}
            profiles = {profile['profile_id']: profile for profile in settings.get('profiles', [])
                        if isinstance(profile, dict) and isinstance(profile.get('profile_id'), str)}
            tasks=[]
            active_profiles={json.loads(r['snapshot']).get('profile_id') for r in db.execute("SELECT snapshot FROM runs WHERE status='running'")}
            for row in db.execute('SELECT * FROM tasks ORDER BY rowid DESC'):
                task=json.loads(row['data']);task.update(id=row['id'],revision=row['revision'],next_run=row['next_run'])
                task.setdefault('assistant_id', 'isolated')
                task.setdefault('tool_groups', [])
                if task['kind'] == 'script':
                    task['executor_name'] = 'Bash 脚本'
                    task['model_name'] = ''
                elif task['kind'] in ('intel_wechat_mp','recompute_heat'):
                    # 本机抓取，不调用任何模型。
                    task['executor_name'] = '本机 heat 重算' if task['kind']=='recompute_heat' else '公众号订阅（本机抓取）'
                    task['model_name'] = ''
                else:
                    task['executor_name'] = ('微信雷达分析（Pi）' if task['kind'] == 'wechat_radar' else
                                             '微信线索分析（Pi）' if task['kind'] == 'wechat_clues' else
                                             'GitHub 线索分析（Pi）' if task['kind'] == 'github_clues' else
                                             '商单分析（Pi）' if task['kind'] == 'deals_review' else
                                             '创意提炼（Pi）' if task['kind'] == 'intake_cards' else
                                             '工作台助手（Pi）' if task['assistant_id'] == 'workbench' else '独立 Pi')
                    profile = profiles.get(task.get('profile_id'))
                    task['model_name'] = ((profile.get('display_name') or profile.get('model') or '')
                                          if profile else '模型配置已移除')
                last=db.execute('SELECT id,status,started_at,finished_at,exit_code FROM runs WHERE task_id=? ORDER BY rowid DESC LIMIT 1',(row['id'],)).fetchone()
                task['last_run']=dict(last) if last else None
                task['running']=db.execute("SELECT id FROM runs WHERE task_id=? AND status='running'",(row['id'],)).fetchone() is not None
                task['waiting_for_model']=bool(task['kind']!='script' and not task['running'] and task['enabled'] and row['next_run'] and row['next_run']<=time.time() and task.get('profile_id') in active_profiles)
                tasks.append(task)
            active = db.execute("SELECT COUNT(*) FROM runs WHERE status='running'").fetchone()[0]
            return {'tasks':tasks,'timezone':'Asia/Shanghai','engine':'workbench','default_cwd':str(self.folder.parent),
                    'last_error':self.last_error,'last_tick':self.last_tick,
                    'scheduler_health': {'running': bool(self.thread and self.thread.is_alive() and not self.stop_event.is_set()),
                                         'last_tick': self.last_tick, 'last_error': self.last_error},
                    'concurrency': {'max': 2, 'active': active, 'available': max(0, 2 - active)},
                    'models':settings.get('profiles',[]),'default_profile_id':settings.get('active_profile_id')}

    def save(self, payload):
        task=self.validate(payload); identity=payload.get('id') or uuid.uuid4().hex
        with self.lock, self.db() as db:
            prior=db.execute('SELECT * FROM tasks WHERE id=?',(identity,)).fetchone()
            if payload.get('id') and not prior: raise ValueError('任务不存在')
            if prior and payload.get('revision') != prior['revision']: raise ValueError('任务已被更新，请刷新后再保存')
            revision=prior['revision']+1 if prior else 1
            task['updated_at']=stamp()
            due=next_due(task,time.time()) if task['enabled'] else None
            db.execute('INSERT OR REPLACE INTO tasks VALUES(?,?,?,?)',(identity,revision,json.dumps(task,ensure_ascii=False),due))
        return {'id':identity,'revision':revision}

    def control(self, identity, action):
        if action=='run': return self.claim(identity,'manual')
        with self.lock, self.db() as db:
            row=db.execute('SELECT * FROM tasks WHERE id=?',(identity,)).fetchone()
            if not row: raise ValueError('任务不存在')
            if action=='delete':
                if db.execute("SELECT 1 FROM runs WHERE task_id=? AND status='running'",(identity,)).fetchone(): raise ValueError('请先停止运行')
                db.execute('DELETE FROM tasks WHERE id=?',(identity,));return {'ok':True}
            if action not in ('pause','resume'): raise ValueError('未知操作')
            task=json.loads(row['data']);task['enabled']=action=='resume';task['updated_at']=stamp()
            due=next_due(task,time.time()) if task['enabled'] else None
            if task['enabled'] and due is None: raise ValueError('该任务已到期，请调整截止时间')
            db.execute('UPDATE tasks SET revision=revision+1,data=?,next_run=? WHERE id=?',(json.dumps(task,ensure_ascii=False),due,identity))
            return {'ok':True, 'id': identity, 'revision': row['revision'] + 1,
                    'enabled': task['enabled'], 'next_run': due}

    def batch(self, payload):
        """Validate every selected revision before changing any task in one transaction."""
        if not isinstance(payload, dict) or set(payload) != {'action', 'tasks'}:
            raise ValueError('批量操作格式错误')
        action = payload['action']
        if action not in ('pause', 'resume'):
            raise ValueError('批量操作仅支持暂停或启用')
        entries = payload['tasks']
        if not isinstance(entries, list) or not 1 <= len(entries) <= 100:
            raise ValueError('每次批量操作请选择 1 至 100 个任务')
        selected = []
        seen = set()
        for entry in entries:
            if not isinstance(entry, dict) or set(entry) != {'id', 'revision'}:
                raise ValueError('批量任务必须包含编号和版本')
            task_id = identity(entry['id'], '任务编号')
            revision = entry['revision']
            if type(revision) is not int or revision < 1:
                raise ValueError('任务版本无效')
            if task_id in seen:
                raise ValueError('批量任务编号不能重复')
            seen.add(task_id)
            selected.append((task_id, revision))
        now = time.time()
        updated_at = stamp()
        with self.lock, self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            changes = []
            for task_id, revision in selected:
                row = db.execute('SELECT * FROM tasks WHERE id=?', (task_id,)).fetchone()
                if not row:
                    raise ValueError('所选任务不存在，请刷新后重试')
                if row['revision'] != revision:
                    raise ValueError('所选任务已被更新，请刷新后重试')
                task = json.loads(row['data'])
                task['enabled'] = action == 'resume'
                task['updated_at'] = updated_at
                due = next_due(task, now) if task['enabled'] else None
                if task['enabled'] and due is None:
                    raise ValueError('所选任务已到期，请调整截止时间后重试')
                changes.append((task_id, revision + 1, task, due))
            for task_id, revision, task, due in changes:
                db.execute('UPDATE tasks SET revision=?,data=?,next_run=? WHERE id=?',
                           (revision, json.dumps(task, ensure_ascii=False), due, task_id))
        return {'ok': True, 'action': action, 'updated': len(changes),
                'tasks': [{'id': task_id, 'revision': revision, 'enabled': task['enabled'], 'next_run': due}
                          for task_id, revision, task, due in changes]}

    def get_run(self,run_id):
        identity(run_id,'运行编号')
        with self.db() as db:
            row=db.execute('SELECT * FROM runs WHERE id=?',(run_id,)).fetchone()
            if not row:raise ValueError('运行记录不存在')
            return {**dict(row),'snapshot':json.loads(row['snapshot'])}

    def runs(self, task_id=None, q='', status=None, page=1, page_size=50):
        if task_id not in (None, ''):
            identity(task_id, '任务编号')
        if not isinstance(q, str) or len(q) > 500 or any(ord(char) < 32 or ord(char) == 127 for char in q):
            raise ValueError('运行记录搜索词最多 500 字，不能包含控制字符')
        q = q.strip()
        if status not in (None, '') and (not isinstance(status, str) or status not in RUN_STATUSES):
            raise ValueError('运行记录状态无效')
        if type(page_size) is not int or not 1 <= page_size <= 100:
            raise ValueError('每页运行记录数须为 1 至 100')
        if type(page) is not int or page < 1 or page > 9223372036854775807 // page_size:
            raise ValueError('运行记录页码须为有效正整数')
        clauses = []
        parameters = []
        if task_id:
            clauses.append('task_id=?')
            parameters.append(task_id)
        if status:
            clauses.append('status=?')
            parameters.append(status)
        if q:
            pattern = '%' + q.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%'
            clauses.append("(COALESCE(json_extract(snapshot,'$.name'),'') LIKE ? ESCAPE '\\' OR "
                           "COALESCE(json_extract(snapshot,'$.body'),'') LIKE ? ESCAPE '\\' OR "
                           "COALESCE(output,'') LIKE ? ESCAPE '\\')")
            parameters.extend([pattern] * 3)
        where = ' WHERE ' + ' AND '.join(clauses) if clauses else ''
        with self.db() as db:
            # Keep the count and this page on the same SQLite read snapshot.
            db.execute('BEGIN')
            total = db.execute('SELECT COUNT(*) FROM runs' + where, parameters).fetchone()[0]
            rows = db.execute('SELECT * FROM runs' + where + ' ORDER BY rowid DESC LIMIT ? OFFSET ?',
                              [*parameters, page_size, (page - 1) * page_size]).fetchall()
            return {'runs': [{**dict(row), 'snapshot': json.loads(row['snapshot'])} for row in rows],
                    'total': total, 'page': page, 'page_size': page_size,
                    'has_more': page * page_size < total}

    def model_in_use(self, profile_id):
        """Is a scheduled run already holding this model profile? 即时分析用它避免撞同一个模型。"""
        if not profile_id:
            return False
        with self.db() as db:
            return db.execute("SELECT 1 FROM runs WHERE status='running' AND json_extract(snapshot,'$.profile_id')=?",
                              (profile_id,)).fetchone() is not None

    def claim(self, identity, trigger, expected_due=None, launch=True):
        with self.lock, self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT * FROM tasks WHERE id=?',(identity,)).fetchone()
            if not row: raise ValueError('任务不存在')
            task=json.loads(row['data']); now=time.time()
            from scheduled_pi import validate_scope, allowed_tools
            task.update(validate_scope(task, task['kind']))
            if self.stop_event.is_set(): raise ValueError('调度器正在关闭')
            if expected_due is not None and (row['next_run']!=expected_due or not task['enabled'] or expected_due>now): return None
            if db.execute("SELECT 1 FROM runs WHERE task_id=? AND status='running'",(identity,)).fetchone(): raise ValueError('此任务正在运行，不重复启动')
            if db.execute("SELECT COUNT(*) FROM runs WHERE status='running'").fetchone()[0]>=2: raise ValueError('同时最多运行两个任务，请稍后再试')
            if task.get('ends_at') and now>datetime.fromisoformat(task['ends_at']).timestamp():
                db.execute('UPDATE tasks SET next_run=NULL WHERE id=?',(identity,));return None
            config=self.model_config(task['profile_id']) if task['kind'] in MODEL_KINDS else None
            if config and db.execute("SELECT 1 FROM runs WHERE status='running' AND json_extract(snapshot,'$.profile_id')=?",(task['profile_id'],)).fetchone():
                raise ModelBusy('同一模型已有后台任务，排队等待，不重复占用推理服务')
            run_id=uuid.uuid4().hex; snapshot={**task,'id':identity,'revision':row['revision'],'execution_trigger':trigger}
            if task['kind']=='pi': snapshot['authorized_tools']=allowed_tools(task)
            if config: snapshot['model']=config['model']
            db.execute('INSERT INTO runs(id,task_id,snapshot,trigger,status,started_at,finished_at,exit_code,output) VALUES(?,?,?,?,?,?,?,?,?)',(run_id,identity,json.dumps(snapshot,ensure_ascii=False),trigger,'running',stamp(),None,None,''))
            if trigger=='scheduled': db.execute('UPDATE tasks SET next_run=? WHERE id=?',(next_due(task,now),identity))
            cancel=threading.Event(); self.active[run_id]=cancel
            if launch:
                worker=threading.Thread(target=self.execute,args=(run_id,snapshot,cancel,config),daemon=True)
                self.workers[run_id]=worker
            db.commit()
            if launch: worker.start()
            return {'id':run_id,'status':'running'}

    def cancel(self, run_id):
        with self.lock:
            cancel=self.active.get(run_id)
            if not cancel: raise ValueError('该次运行已结束')
            cancel.set();return {'ok':True}

    def tick(self):
        with self.db() as db:
            due=db.execute('SELECT id,next_run FROM tasks WHERE next_run<=? ORDER BY next_run',(time.time(),)).fetchall()
        for row in due:
            try: self.claim(row['id'],'scheduled',row['next_run'])
            except ModelBusy:
                continue  # Keep due time: next tick retries after the owner finishes.
            except ValueError as exc:
                if '配置' in str(exc) or '模型' in str(exc) or '不存在' in str(exc):
                    # Missing configuration is a persisted failed attempt, not a tight retry loop.
                    with self.lock,self.db() as db:
                        current=db.execute('SELECT * FROM tasks WHERE id=? AND next_run=?',(row['id'],row['next_run'])).fetchone()
                        if current:
                            task=json.loads(current['data']);snapshot={**task,'id':row['id'],'revision':current['revision']}
                            db.execute('INSERT INTO runs(id,task_id,snapshot,trigger,status,started_at,finished_at,exit_code,output) VALUES(?,?,?,?,?,?,?,?,?)',(uuid.uuid4().hex,row['id'],json.dumps(snapshot,ensure_ascii=False),'scheduled','failed',stamp(),stamp(),None,str(exc)))
                            db.execute('UPDATE tasks SET next_run=? WHERE id=?',(next_due(task,time.time()),row['id']))

    def deliver_result(self,run_id):
        try:
            if not self.publisher:raise ValueError('线索入口未连接')
            result=self.publisher(run_id)
            with self.db() as db:db.execute('UPDATE runs SET clue_id=?,publication_error=NULL WHERE id=?',(result.get('id'),run_id))
            return result
        except Exception as exc:
            with self.db() as db:db.execute('UPDATE runs SET publication_error=? WHERE id=?',(str(exc)[:2000],run_id))
            raise

    def run_loop(self):
        # Resume only unsent successful results. An execution is never repeated here.
        with self.db() as db:
            pending=db.execute("SELECT id FROM runs WHERE status='succeeded' AND clue_id IS NULL AND publication_error IS NULL AND output!='' AND json_extract(snapshot,'$.result_destination')='clues' ORDER BY rowid LIMIT 100").fetchall()
        for row in pending:
            if self.stop_event.is_set():break
            try:self.deliver_result(row['id'])
            except Exception:pass
        self.loop()

    def loop(self):
        while not self.stop_event.is_set():
            try:
                self.tick();self.last_tick=stamp();self.last_error=None
            except Exception:
                self.last_error='调度检查失败，请检查工作台日志与任务数据库；任务没有被标记成功'
            self.stop_event.wait(2)

    @staticmethod
    def shell(command,cwd,cancel,deadline):
        process=subprocess.Popen(['/bin/bash','-lc',command],cwd=cwd,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,start_new_session=True)
        buffer=bytearray(); output_lock=threading.Lock(); total=[0]
        def drain():
            while True:
                chunk=process.stdout.read1(4096)
                if not chunk: break
                with output_lock:
                    total[0]+=len(chunk);buffer.extend(chunk)
                    if len(buffer)>MAX_OUTPUT: del buffer[:-MAX_OUTPUT]
        reader=threading.Thread(target=drain,daemon=True);reader.start()
        status=None
        try:
            while process.poll() is None:
                if cancel.is_set() or time.monotonic()>=deadline:
                    status='cancelled' if cancel.is_set() else 'timeout';break
                time.sleep(.05)
        finally:
            # Kill background descendants too, even after the command parent exits.
            try: os.killpg(process.pid,signal.SIGTERM)
            except ProcessLookupError: pass
            try: process.wait(timeout=1)
            except subprocess.TimeoutExpired: pass
            # macOS can briefly report EPERM for a group being reaped after TERM.
            reader.join(timeout=.2)
            try:
                for attempt in range(6):
                    try: os.killpg(process.pid,signal.SIGKILL); break
                    except ProcessLookupError: break
                    except PermissionError:
                        if attempt == 5: raise
                        time.sleep(.05)
                process.wait();reader.join(timeout=2)
            finally: process.stdout.close()
        with output_lock:
            output=('［输出超过 64 KiB，仅保留末尾］\n' if total[0]>MAX_OUTPUT else '')+bytes(buffer).decode('utf-8','replace')
        return status or ('succeeded' if process.returncode==0 else 'failed'),process.returncode,output

    def pi(self, run_id, task, cancel, config, deadline):
        from pi_runtime import PiRuntime
        from scheduled_pi import ScheduledPiTools, schemas, redact
        from assistant_agent import LABELS
        bridge=ScheduledPiTools(self,task,cancel,deadline)
        tools=schemas(task)
        folder=self.folder/'pi'/run_id;folder.mkdir(parents=True,mode=0o700)
        class Owner:
            turns=[];tool_labels={**LABELS,'run_command':'执行本机命令'}
            def request_model(self,*args,**kwargs): raise ValueError('调度任务使用 Pi 原生模型接口')
            def persist_pending(self,job): pass
            def execute(owner,name,args,job):
                return bridge.execute(name,args,job)
        owner=Owner();owner.folder=folder;owner.lock=threading.RLock();owner.client=owner.request_model
        owner._schedule_deadline=deadline;owner._schedule_cancel=cancel
        bridge.check()
        runtime=PiRuntime(owner)
        bridge.check()
        context=({'schedule':{'id':task.get('id'),'name':task['name'],'cwd':task['cwd'],
                             'assistant_id':task['assistant_id'],'tool_groups':task.get('tool_groups',[]),
                             'result_destination':task.get('result_destination','runs')}}
                 if task.get('assistant_id')=='workbench' else {})
        job={'id':run_id,'conversation_id':run_id,'model_config':config,'prompt':task['body'],'context':context,'attachments':[],
             'answer':'','policy':{'allowed_tools':sorted(bridge.allowed)},'actions':[],'cancel':False,
             'read_versions':{},'read_blocks':{},
             '_workflow_stage':'scheduled','_schedule_cwd':task['cwd']}
        finished=threading.Event()
        def watch():
            while not finished.wait(.1):
                if cancel.is_set() or time.monotonic()>=deadline: job['cancel']=True;return
        watcher=threading.Thread(target=watch,daemon=True);watcher.start()
        try:
            result=runtime.run(job,bridge.system(),tools)
            status='cancelled' if cancel.is_set() else 'timeout' if time.monotonic()>=deadline else 'succeeded' if result['status']=='done' else 'failed'
            output=result.get('answer','')
            if job['actions']: output+='\n\n执行记录：\n'+json.dumps(job['actions'],ensure_ascii=False)
            return status,None,redact(output,config)[-MAX_OUTPUT:]
        finally:
            finished.set()
            for path in folder.rglob('*'):
                if path.is_file(): path.chmod(0o600)
                elif path.is_dir(): path.chmod(0o700)

    def _run_outsourced(self, run_id, task, cancel, config, pi_runner=None, date=None):
        """外包任务的一次执行：先验收 WorkBuddy 的交接文件，没有或不合法才本机 Pi 兜底。

        顺序不能反：有合法文件时**绝不**兜底 —— 那是黄总的免费额度。
        本机兜底只该在「没交付」或「交付不合法」时发生。
        """
        import outsourced_tasks as ot
        slug = task['slug']
        day = date or ot.today()
        folder = Path(task['cwd'])
        outbox = folder.parent
        with ot.outbox_root_override(outbox):
            target = ot.file_for(slug, day)
            warnings, data = [], None
            if target.is_file():
                try:
                    data = ot.validate(target, slug, day)
                except (ValueError, FileNotFoundError) as exc:
                    warnings.append('交接文件不合格，改用本机兜底：%s' % str(exc)[:200])
            else:
                warnings.append('外包任务未交付：未见 %s' % target)
            # 交付方自己报 failed 时同样不导入，但**按兜底设置决定补不补做**：
            # 这个功能的目的就是保证当天有产出，「失败了」和「没交付」同等对待。
            # 是否补做由 fallback 开关决定，不是自动的。
            if data is None or data['status'] == 'failed':
                return self._outsourced_fallback(run_id, task, cancel, config, day, target,
                                                  outbox, warnings, pi_runner, data)
            return self._outsourced_collect(slug, day, target, data, warnings)

    def _outsourced_collect(self, slug, day, target, data, warnings):
        """验收通过：导入线索中心。不调用模型。"""
        import outsourced_tasks as ot
        if not self.outsourced_clues:
            # 没接上就是没干活 —— 记成功会让黄总以为线索到了。
            # 交接文件保留在原处，下次到点还能再收。
            reason = '线索存储入口未连接，本次未导入；交接文件已保留，下次可再收'
            warnings.append(reason)
            return {'status': 'failed', 'producer': data['producer'], 'file': str(target),
                    'error': reason,
                    'imported': 0, 'skipped_duplicates': 0, 'warnings': warnings, 'clue_ids': []}
        result = ot.ingest(self.outsourced_clues, slug, data)
        result['warnings'] = warnings + list(result.get('warnings', []))
        result['file'] = str(target)
        return result

    def _outsourced_fallback(self, run_id, task, cancel, config, day, target, outbox, warnings,
                             pi_runner=None, failed_delivery=None):
        """没交付、不合格或交付方报失败：按开关决定是否用本机 Pi 兜底，用同一份提示词补做。"""
        import outsourced_tasks as ot
        if task.get('fallback', 'pi') != 'pi':
            if failed_delivery is not None:
                reason = failed_delivery.get('error') or '交付方未说明原因'
                return {'status': 'failed', 'producer': failed_delivery['producer'],
                        'file': str(target),
                        'error': '交付方报告失败：%s（未开启本机兜底，本次未补做）' % reason,
                        'imported': 0, 'skipped_duplicates': 0,
                        'warnings': warnings, 'clue_ids': []}
            return {'status': 'failed', 'producer': None, 'file': str(target),
                    'error': '外包任务未交付（%s 缺失或不合法），且该任务未开启本机兜底' % target.name,
                    'imported': 0, 'skipped_duplicates': 0, 'warnings': warnings, 'clue_ids': []}
        prompt = ot.prompt_for(task, producer='local-pi', outbox=outbox, date=day)
        deadline = time.monotonic() + task['timeout']
        status, code, output = (pi_runner or self.pi)(run_id, {**task, 'body': prompt, 'cwd': str(Path(task['cwd']))},
                                                        cancel, config, deadline)
        if cancel.is_set():
            status = 'cancelled'
        elif time.monotonic() >= deadline and status == 'succeeded':
            status = 'timeout'
        if status != 'succeeded':
            return {'status': status, 'producer': 'local-pi', 'file': str(target), 'error': output,
                    'imported': 0, 'skipped_duplicates': 0, 'warnings': warnings, 'clue_ids': []}
        if not target.is_file():
            return {'status': 'failed', 'producer': 'local-pi', 'file': str(target),
                    'error': '本机兜底已执行，但没有留下约定的交接文件 %s' % target.name,
                    'imported': 0, 'skipped_duplicates': 0, 'warnings': warnings, 'clue_ids': []}
        try:
            data = ot.validate(target, task['slug'], day)
        except (ValueError, FileNotFoundError) as exc:
            return {'status': 'failed', 'producer': 'local-pi', 'file': str(target),
                    'error': '本机兜底写出的交接文件仍不合格：%s' % str(exc)[:200],
                    'imported': 0, 'skipped_duplicates': 0, 'warnings': warnings, 'clue_ids': []}
        if data['producer'] != 'local-pi':
            # 这条路径上我们已经确定是本机 Pi 在跑。产物自称什么都不改变事实，
            # 所以在**导入前**改掉数据，而不是只在警告里说说 —— 否则回执、证据
            # provenance、页面徽章三处仍会各说各的。
            warnings.append('兜底产物自称 producer=%s，已按实际交付方记为 local-pi' % data['producer'])
            # 必须走 ot 的 helper：`{**data, ...}` 会把已规范化数据降级成普通 dict，
            # adopt 就不认了，重新规范化会把 validate 记下的截断警告弄丢（M3）。
            data = ot.relabel_producer(data, 'local-pi')
        return self._outsourced_collect(task['slug'], day, target, data, warnings)

    def outsourced_prompt(self, task_id):
        """只读：返回可直接粘给 WorkBuddy 的完整提示词（页面「复制」按钮用）。"""
        import outsourced_tasks as ot
        task = self._task(task_id)
        if task['kind'] != 'outsourced':
            raise ValueError('只有外包任务才有 WorkBuddy 提示词')
        return {'prompt': ot.prompt_for(task, producer='workbuddy', outbox=Path(task['cwd']).parent),
                'slug': task['slug'], 'folder': task['cwd']}

    def _task(self, task_id):
        with self.db() as db:
            row = db.execute('SELECT data FROM tasks WHERE id=?', (identity(task_id, '任务编号'),)).fetchone()
        if not row:
            raise ValueError('任务不存在')
        return json.loads(row['data'])

    def _outsourced_execute(self, run_id, task, cancel, config, deadline):
        """把 _run_outsourced 的结果压成 execute() 认得的 (status, code, output)。"""
        remaining = max(1, int(deadline - time.monotonic()))
        result = self._run_outsourced(run_id, task, cancel, config,
                                      pi_runner=lambda *a: self.pi(*a[:4], min(deadline, time.monotonic() + remaining)))
        payload = {k: result.get(k) for k in ('producer', 'file', 'imported', 'skipped_duplicates', 'warnings')}
        if result.get('error'):
            payload['error'] = result['error']
        status = result.get('status', 'succeeded')
        return ('succeeded' if status == 'succeeded' else status), (0 if status == 'succeeded' else None), json.dumps(payload, ensure_ascii=False)

    def _wechat_failure(self,run_id,task,exc):
        """Recover only this analyzer owner's validated, durable run progress."""
        progress = None
        owner = getattr(self.wechat_analyzer, '__self__', None)
        try:
            state = owner.status() if owner is not None else None
            candidate = state.get('progress') if isinstance(state, dict) else None
            if isinstance(candidate, dict) and candidate.get('run_id') == run_id:
                counts = [candidate.get('processed'), candidate.get('skipped')]
                clue_ids = candidate.get('clue_ids', [])
                if (all(type(count) is int and count >= 0 for count in counts)
                        and isinstance(clue_ids, list)
                        and type(candidate.get('has_more', True)) is bool):
                    for clue_id in clue_ids: identity(clue_id, '线索编号')
                    progress = candidate
        except Exception:
            # An unrelated or unavailable progress report cannot replace this failure.
            pass
        progress = progress or {}
        errors = []
        prior_errors = progress.get('errors', [])
        if isinstance(prior_errors, list):
            for error in prior_errors:
                if not isinstance(error, dict) or not isinstance(error.get('message'), str): continue
                entry = {'message': error['message'][:2000]}
                if 'state' in error and error['state'] in ('waiting', 'needs_review', None):
                    entry['state'] = error['state']
                for field in ('documents', 'attempts'):
                    count = error.get(field)
                    if type(count) is int and count >= 0: entry[field] = count
                batch_id = error.get('batch_id')
                if isinstance(batch_id, str) and len(batch_id) == 64 and all(char in '0123456789abcdef' for char in batch_id):
                    entry['batch_id'] = batch_id
                retry_at = error.get('next_retry_at')
                if isinstance(retry_at, str) and len(retry_at) <= 80:
                    try:
                        if datetime.fromisoformat(retry_at.replace('Z', '+00:00')).tzinfo is not None:
                            entry['next_retry_at'] = retry_at
                    except ValueError: pass
                errors.append(entry)
        message = str(exc)[:2000] if isinstance(exc, (ValueError, OSError)) else '执行异常，请查看工作台服务日志'
        errors.append({'message': message})
        scope = ('本机微信最近七天；持续增量，无每小时条数配额' if task.get('wechat_mode') == 'incremental'
                 else '本机微信缓存单次试跑，最多30条')
        result = {'scope': scope, 'processed': progress.get('processed', 0), 'skipped': progress.get('skipped', 0),
                  'clues': list(dict.fromkeys(progress.get('clue_ids', []))),
                  'has_more': progress.get('has_more', True), 'errors': errors}
        format_counts=progress.get('format')
        if isinstance(format_counts,dict):
            result['format']={key:value for key,value in format_counts.items() if key in ('field_names','ignored_fields','dropped_clues','requeued_documents','quote_mapped') and type(value) is int and value>=0}
        reviewed=progress.get('reviewed')
        if type(reviewed) is int and reviewed>=0:result['reviewed']=reviewed
        deferred = progress.get('deferred')
        if type(deferred) is int and deferred >= 0: result['deferred'] = deferred
        capture = progress.get('capture')
        if isinstance(capture, dict):
            validated = {}
            if type(capture.get('fresh')) is bool: validated['fresh'] = capture['fresh']
            for field in ('warning', 'status', 'index_status'):
                value = capture.get(field)
                if isinstance(value, str): validated[field] = value[:2000]
            if validated: result['capture'] = validated
        partial = bool(result['processed'] or result['skipped'] or result['clues'] or result.get('reviewed'))
        return partial, json.dumps(result, ensure_ascii=False)

    def execute(self,run_id,task,cancel,config):
        deadline=time.monotonic()+task['timeout']
        try:
            if task['kind']=='recompute_heat':
                if not self.heat_recomputer:raise ValueError('heat 重算入口未连接')
                result=self.heat_recomputer(cancel,deadline)
                status=('cancelled' if cancel.is_set() else 'timeout' if time.monotonic()>=deadline else 'succeeded')
                code=0 if status=='succeeded' else None
                output=json.dumps(result,ensure_ascii=False)
            elif task['kind']=='github_clues':
                if not self.github_analyzer:raise ValueError('GitHub 分析入口未连接')
                status,code,output=self.github_analyzer(run_id,task,cancel,config,deadline)
            elif task['kind']=='wechat_radar':
                if not self.radar_analyzer:raise ValueError('微信雷达分析入口未连接')
                status,code,output=self.radar_analyzer(run_id,task,cancel,config,deadline)
            elif task['kind']=='deals_review':
                if not self.deals_analyzer:raise ValueError('商单分析入口未连接')
                status,code,output=self.deals_analyzer(run_id,task,cancel,config,deadline)
            elif task['kind']=='intake_cards':
                if not self.intake_analyzer:raise ValueError('创意提炼入口未连接')
                status,code,output=self.intake_analyzer(run_id,task,cancel,config,deadline)
            elif task['kind']=='intel_wechat_mp':
                if not self.intel_analyzer:raise ValueError('公众号订阅入口未连接')
                status,code,output=self.intel_analyzer(run_id,task,cancel,config,deadline)
            elif task['kind']=='outsourced':
                status,code,output=self._outsourced_execute(run_id,task,cancel,config,deadline)
            elif task['kind']=='wechat_clues':
                if not self.wechat_analyzer:raise ValueError('微信线索分析入口未连接')
                status,code,output=self.wechat_analyzer(run_id,task,cancel,config,deadline)
                if status in ('succeeded', 'partial'):
                    if cancel.is_set(): status,code='cancelled',None
                    elif time.monotonic()>=deadline: status,code='timeout',None
            else:status,code,output=self.shell(task['body'],task['cwd'],cancel,deadline) if task['kind']=='script' else self.pi(run_id,task,cancel,config,deadline)
        except Exception as exc:
            status='cancelled' if cancel.is_set() else 'timeout' if time.monotonic()>=deadline else 'failed'
            code=None
            output=str(exc) if isinstance(exc,(ValueError,OSError)) else '执行异常，请查看工作台服务日志'
            if task['kind']=='wechat_clues':
                partial,output=self._wechat_failure(run_id,task,exc)
                status=('cancelled' if cancel.is_set() else 'timeout' if time.monotonic()>=deadline
                        else 'partial' if partial else 'failed')
        if status=='partial': code=None
        with self.lock,self.db() as db:
            db.execute('UPDATE runs SET status=?,finished_at=?,exit_code=?,output=? WHERE id=?',(status,stamp(),code,output,run_id))
        try:
            if status=='succeeded' and task.get('result_destination')=='clues':
                self.deliver_result(run_id)
        except Exception as exc:
            with self.db() as db:db.execute('UPDATE runs SET publication_error=? WHERE id=?',(str(exc)[:2000],run_id))
        finally:
            with self.lock:
                self.active.pop(run_id,None);self.workers.pop(run_id,None)

    def close(self):
        self.stop_event.set()
        with self.lock:
            for cancel in self.active.values(): cancel.set()
            workers=list(self.workers.values())
        if self.thread: self.thread.join(timeout=3)
        deadline=time.monotonic()+8
        for worker in workers: worker.join(timeout=max(0,deadline-time.monotonic()))
