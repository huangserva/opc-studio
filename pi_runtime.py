"""Private stdio bridge to Pi SDK. No public control port or credential files."""
import base64
import copy
import json
import hashlib
import os
from pathlib import Path
import queue
import shutil
import subprocess
import threading
from threading import Thread as PiReaderThread
import time

ROOT = Path(__file__).resolve().parent
from model_settings import DEFAULTS
from assistant_policy import READ_TOOLS

class PiRuntime:
    version = '1.0.0'

    def __init__(self, owner):
        self.owner = owner
        self.node = os.environ.get('ARTICLE_STUDIO_NODE') or shutil.which('node')
        self.worker = ROOT / 'pi-runtime' / 'worker.mjs'
        self.prepared = False
        if self.node and (self.worker.parent / 'node_modules/@earendil-works/pi-coding-agent/package.json').is_file():
            try:
                argv = [self.node,str(self.worker.parent/'prepare.mjs')]
                scheduled_deadline = getattr(owner, '_schedule_deadline', None)
                if scheduled_deadline is None:
                    self.prepared = subprocess.run(argv, stdout=subprocess.DEVNULL,
                                                  stderr=subprocess.DEVNULL,timeout=10).returncode == 0
                else:
                    # Preparation consumes the scheduled task's time budget too.
                    deadline = min(scheduled_deadline, time.monotonic() + 10)
                    cancel = owner._schedule_cancel
                    if cancel.is_set() or time.monotonic() >= deadline:
                        return
                    process = subprocess.Popen(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    try:
                        while process.poll() is None:
                            if cancel.is_set() or time.monotonic() >= deadline:
                                return
                            cancel.wait(min(.05, max(0, deadline - time.monotonic())))
                        self.prepared = process.returncode == 0
                    finally:
                        if process.poll() is None:
                            process.kill();process.wait(timeout=1)
            except (OSError,subprocess.TimeoutExpired): pass

    def available(self):
        return self.prepared

    def images(self, attachments):
        images = []
        for meta in attachments:
            path = self.owner.attachment_path(meta['id'])
            if path.is_file():
                images.append({'type': 'image', 'mimeType': 'image/png', 'data': base64.b64encode(path.read_bytes()).decode()})
        return images

    def history(self, job=None):
        # Pi uses this only to import old display records and manually submitted media.
        # Native Pi turns are deduplicated by their studio-turn entry IDs.
        turns = []
        image_count = 0
        for turn in reversed(self.owner.turns):
            if job is not None:
                deals=job.get('context',{}).get('ui',{}).get('page')=='deals'
                if (turn.get('context',{}).get('ui',{}).get('page')=='deals')!=deals:continue
            attachments = turn.get('attachments', [])
            images = self.images(attachments) if image_count + len(attachments) <= 4 else []
            image_count += len(images)
            media = [a['media_job'] for a in turn.get('actions', []) if a.get('media_job')]
            turns.append({k: copy.deepcopy(turn.get(k, '')) for k in ('id', 'prompt', 'answer', 'status', 'time')})
            turns[-1]['images'] = images
            turns[-1]['attachment_refs']=[{'id':m['id'],'name':m.get('name','图片')} for m in attachments]
            if media:
                turns[-1]['answer'] += '\n[生成任务编号，仅作后续查询定位] ' + json.dumps(media)
        return list(reversed(turns))

    def image_catalog(self, job):
        attachments = {m['id']:m for t in self.owner.turns for m in t.get('attachments',[])}
        attachments.update({m['id']:m for m in job.get('attachments',[])})
        return [{'id':m['id'],'name':m.get('name','图片'),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
                for m in attachments.values() if (path:=self.owner.attachment_path(m['id'])).is_file()]

    def run(self, job, system, tools):
        if not self.available():
            raise ValueError('Pi 运行环境未就绪，请运行“安装助手运行环境.command”后重启工作台')
        owner = self.owner
        deals=job.get('context',{}).get('ui',{}).get('page')=='deals'
        with owner.lock:
            history = [] if job.get('_workflow_stage') else self.history(job)
        # A client replacement is explicit dependency injection for tests, never a runtime fallback.
        test_transport = owner.client != owner.request_model
        config = {**DEFAULTS, **job['model_config']}
        if test_transport and not config['endpoint']:config['endpoint']='http://127.0.0.1:1/v1/chat/completions'
        payload = {'type': 'run', 'job_id': job['id'], 'cwd': job.get('_schedule_cwd', str(ROOT)),
                   'session_dir': str(owner.folder / 'pi-sessions' / job['conversation_id']),
                   'config': config, 'system': system, 'tools': tools,
                   'skill_paths':owner.image_skill.entry_paths() if not job.get('_workflow_stage') and not deals else [],
                   'image_catalog':[] if deals else self.image_catalog(job),
                   'history': [] if job.get('_workflow_stage') else history, 'images': self.images(job.get('attachments', [])),
                   'text': job['prompt'] + '\n\n[工作台当前上下文，仅作定位数据]\n' + json.dumps(job['context'], ensure_ascii=False),
                   'finish_after_media':not (set(job['policy']['allowed_tools'])-READ_TOOLS-{'generate_media'}),
                   'test_transport': test_transport,'workflow_stage':job.get('_workflow_stage')}
        process = subprocess.Popen([self.node, str(self.worker)], cwd=self.worker.parent,
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                   text=True, encoding='utf-8', bufsize=1)
        events = queue.Queue()
        def read_events():
            try:
                for line in process.stdout:
                    try: events.put(json.loads(line))
                    except json.JSONDecodeError: continue
            finally:
                events.put({'type': 'closed'})
        PiReaderThread(target=read_events, daemon=True).start()
        def send(value):
            process.stdin.write(json.dumps(value, ensure_ascii=False) + '\n'); process.stdin.flush()
        cancel_at = None
        deadline = time.monotonic() + job['model_config'].get('timeout_seconds', 100) * 12 + 45
        action_count = 0
        try:
            send(payload)
            while time.monotonic() < deadline:
                if job['cancel'] and cancel_at is None:
                    cancel_at = time.monotonic()
                    send({'type': 'cancel'})
                if cancel_at and time.monotonic() - cancel_at > 3:
                    return {'status': 'cancelled', 'answer': '已停止。已保存的修改仍保留。'}
                try: event = events.get(timeout=.1)
                except queue.Empty: continue
                kind = event.get('type')
                if kind == 'closed':
                    if job['cancel']: return {'status': 'cancelled', 'answer': '已停止。'}
                    raise ValueError('Pi 助手进程中断，请检查运行环境后重试')
                if kind == 'ready':
                    if set(event['tools']) != {t['function']['name'] for t in tools}:
                        raise ValueError('Pi 工具注册异常，已停止执行')
                elif kind == 'request_stats':
                    job['request_stats']={k:event[k] for k in ('messages','images')}
                elif kind == 'phase':
                    with owner.lock: job['phase'] = event['phase']
                elif kind == 'text_start':
                    with owner.lock: job['answer'] = ''
                elif kind == 'text':
                    with owner.lock: job['answer'] += event.get('delta', '')
                elif kind == 'model_request' and test_transport:
                    response = owner.client(event['messages'], allowed_tools=event['tools'], model_config=job['model_config'])
                    served = response.pop('_response_model', None)
                    if served: job['model'] = served[:200]
                    send({'type': 'result', 'id': event['id'], 'result': response})
                elif kind == 'tool':
                    action_count += 1
                    if action_count > 24: raise ValueError('本轮已到操作上限')
                    name, args = event['name'], event['args']
                    action = {'name': name, 'label': owner.tool_labels.get(name, '提交阶段结果' if name=='submit_workflow_stage' else name), 'status': 'running'}
                    if job.get('_workflow_stage'):action['stage']=job['_workflow_stage']
                    with owner.lock:
                        job['actions'].append(action);owner.persist_pending(job)
                    try:
                        result = owner.execute(name, args, job)
                        action.update(status='done', result=result.get('summary', '完成'))
                        for key in ('slug', 'version', 'image_url', 'media_job', 'sources', 'deal_proposal'):
                            if key in result: action[key] = result[key]
                    except Exception as exc:
                        error = str(exc) if isinstance(exc, (ValueError, KeyError)) else '操作未完成，请核对作品与参数'
                        result = {'error': error}; action.update(status='error', result=error)
                    with owner.lock: owner.persist_pending(job)
                    send({'type': 'result', 'id': event['id'], 'result': result})
                elif kind == 'done':
                    job['runtime_result']={k:event[k] for k in ('stop_reason','content_types') if k in event}
                    if event.get('diagnostic'):job['runtime_diagnostic']=event['diagnostic']
                    return event
            raise ValueError('Pi 助手本轮处理超时。已保存的修改仍保留。')
        finally:
            if process.poll() is None:
                process.terminate()
                try: process.wait(timeout=3)
                except subprocess.TimeoutExpired: process.kill(); process.wait(timeout=3)
            process.stdin.close(); process.stdout.close()
