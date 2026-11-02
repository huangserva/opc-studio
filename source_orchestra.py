"""Generic schedule/source boundary. Only registered source capabilities execute."""
import json
from pathlib import Path
import shlex
import time
import uuid
from wechat_source_loader import source_module


def rules():return source_module('event_catalog')


def catalog():return rules().catalog()


def normalize(task,payload):
    task.update(source=payload.get('source'),events=payload.get('events'),window=payload.get('window'))
    task=rules().validate_task(task)
    if task.get('result_destination')!='runs' or task.get('clue_id'):raise ValueError('来源事件结果只保存到运行记录')
    return task


def is_wechat(task):
    if task.get('kind') in ('wechat_clues','wechat_radar','deals_review'):return True
    if task.get('kind')=='source_events':return task.get('source')=='wechat'
    if task.get('kind')!='script':return False
    try:return any(Path(part).name=='wechat_tools.py' for part in shlex.split(task.get('body','')))
    except ValueError:return False


def coverage(tasks):
    active=[t for t in tasks if t.get('kind')=='source_events' and t.get('source')=='wechat' and t.get('enabled')]
    covered={n for t in active for n in t.get('events',[])}
    return {'source':'wechat','enabled_timers':len(active),
            'missing':[{'id':n,'name':rules().NAMES[n]} for n in range(3,11) if n not in covered],
            'unavailable':[11,12,13],
            'morning_notice':'晨报汇总已完成的微信线索，不会替代上游识别；请检查各事件最近运行。'}


PRESETS=(('day','白天每小时',[1,2,3,5,6],'interval',60,'08:00',{'start':'08:00','end':'24:00'}),
         ('deal','每4小时跟进商单',[1,2,4],'interval',240,'08:00',None),
         ('night','深夜整理',[1,2,7,8,9],'daily',60,'02:30',None),
         ('morning','早上晨报',[1,2,10],'daily',60,'08:40',None))


def preset_ids(scheduler):
    return [uuid.uuid5(uuid.NAMESPACE_URL,str(scheduler.db_path.resolve())+':wechat:'+p[0]).hex for p in PRESETS]


def _write(db,tid,task,revision,now):
    from scheduled_tasks import next_due
    db.execute('UPDATE tasks SET data=?,revision=?,next_run=? WHERE id=?',
               (json.dumps(task,ensure_ascii=False),revision+1,next_due(task,now) if task['enabled'] else None,tid))


def presets(scheduler,payload):
    if not isinstance(payload,dict) or set(payload)-{'action','profile_id'} or payload.get('action') not in ('install','new','old'):
        raise ValueError('预设操作无效')
    action=payload['action'];ids=preset_ids(scheduler);now=time.time()
    with scheduler.lock,scheduler.db() as db:
        db.execute('BEGIN IMMEDIATE')
        db.execute('CREATE TABLE IF NOT EXISTS source_switches(source TEXT PRIMARY KEY,before TEXT,active INTEGER)')
        rows={r['id']:(json.loads(r['data']),r['revision']) for r in db.execute('SELECT * FROM tasks')}
        if action=='install':
            profile=payload.get('profile_id')
            for tid,p in zip(ids,PRESETS):
                if tid in rows:
                    if rows[tid][0].get('kind')!='source_events' or rows[tid][0].get('source')!='wechat':raise ValueError('预设编号冲突')
                    continue
                key,name,events,frequency,minutes,at,window=p
                task=scheduler.validate(dict(name='微信 · '+name,kind='source_events',source='wechat',events=events,window=window,
                    body='按所选事件处理微信资料，结果只留本机。',cwd=str(scheduler.folder.parent.resolve()),
                    frequency=frequency,minutes=minutes,at=at,weekdays=list(range(7)),ends_at=None,
                    timeout=1800,enabled=False,profile_id=profile,allow_commands=False,result_destination='runs'))
                db.execute('INSERT INTO tasks VALUES(?,?,?,NULL)',(tid,1,json.dumps(task,ensure_ascii=False)))
            return {'installed':4,'enabled':False,'unavailable_notice':'待办、人脉、周日群价值评估第三期提供，未加入可执行事件'}
        if any(tid not in rows for tid in ids):raise ValueError('请先安装四个停用预设')
        old_groups=([tid for tid,(t,_) in rows.items() if t.get('kind')==kind] for kind in ('wechat_clues','wechat_radar'))
        clues,radar=old_groups
        tools=[tid for tid,(t,_) in rows.items() if t.get('kind')=='script' and is_wechat(t)]
        if any(len(group)!=1 for group in (clues,radar,tools)):raise ValueError('旧三项配置缺失或不唯一，未切换任何任务')
        old=clues+radar+tools;targets=old+ids
        previous=db.execute("SELECT * FROM source_switches WHERE source='wechat'").fetchone()
        if action=='new':
            if previous and previous['active']:return {'active':'new','unchanged':True}
            before={tid:rows[tid][0]['enabled'] for tid in targets}
            for tid in targets:
                task,revision=rows[tid];_write(db,tid,{**task,'enabled':tid in ids},revision,now)
            db.execute("INSERT OR REPLACE INTO source_switches VALUES('wechat',?,1)",(json.dumps(before),))
        else:
            if not previous or not previous['active']:return {'active':'old','unchanged':True}
            before=json.loads(previous['before'])
            if set(before)!=set(targets):raise ValueError('切换前任务已变化，未恢复任何状态')
            for tid in targets:
                task,revision=rows[tid];_write(db,tid,{**task,'enabled':before[tid]},revision,now)
            db.execute("UPDATE source_switches SET active=0 WHERE source='wechat'")
    return {'active':action,'changed':len(targets),'message':'仅更改后续计划；当前运行继续，源侧共用进度'}


def private_model_allowed(scheduler,profile_id,config):
    with scheduler.db() as db:tasks=[json.loads(row['data']) for row in db.execute('SELECT data FROM tasks')]
    return rules().private_route_allowed(config,profile_id,tasks)
