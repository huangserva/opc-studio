"""Persistent media jobs over the 4090 media API (HTTPS + FRP STCP).

No SSH in the media path: submit, reference upload, progress, result download,
cancel and the read-only health/inventory probes all use generation.media_http.
A transport failure is an *unknown* state, never a licence to resubmit.
"""
import base64
import copy
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import threading
import time
import uuid
from datetime import datetime, timezone
from content_store import write, check_asset
from .models import MODELS, build
from .references import References
from .image_references import normalize_image_references
from .video_options import normalize_references
from .inventory import model_selection, enrich_inventory
from .media_http import MediaApi, MediaApiError, MediaApiUnknown, MODEL_IDS, safe_upstream_text

ROOT = Path(__file__).parent
ACTIVE = {'connecting', 'queued', 'running', 'downloading', 'reconnecting'}
# 'uncertain' is deliberately neither ACTIVE nor a failure: the server job may
# still be running, so it stays verifiable instead of being polled as active or
# letting the user resubmit blindly.
API_STATE = {'queued': 'queued', 'submitting': 'queued', 'running': 'running',
             'reconciling': 'reconnecting', 'cancel_requested': 'running', 'cancelling': 'running',
             'succeeded': 'downloading', 'failed': 'error', 'cancelled': 'cancelled',
             'uncertain': 'uncertain'}
CONTENT_EXT = {'image/png': '.png', 'image/jpeg': '.jpg', 'image/webp': '.webp',
               'video/mp4': '.mp4', 'video/webm': '.webm'}
EVENT_PROGRESS = {
    'created': ('queued', '任务已登记，等待服务器处理'),
    'blocked': ('blocked', '等待 4090 空闲后自动提交'),
    'submitting': ('submitting', '正在提交到模型服务'),
    'running': ('running', '服务器正在生成'),
    'progress': ('running', '服务器正在生成'),
    'recovered': ('running', '服务器任务已恢复跟踪'),
    'cancel_requested': ('cancelling', '正在停止服务器任务'),
    'succeeded': ('saving', '正在导出结果'),
}

def now():
    return datetime.now(timezone.utc).isoformat()


class CancelledBeforeSubmit(Exception):
    """The user stopped the job before the API ever received it."""

class Generation:
    def __init__(self, store):
        self.store = store
        self.folder = store.workspace / 'generation'
        self.folder.mkdir(exist_ok=True)
        self.references = References(self.folder / 'references')
        self.lock = threading.RLock()
        self.thumbnail_lock = threading.Lock()
        self.jobs = {}
        self.observers = {}
        for path in self.folder.glob('*/job.json'):
            try:
                job = json.loads(path.read_text())
                self.jobs[job['id']] = job
            except (ValueError, KeyError):
                continue
        for job in self.jobs.values():
            if job['status'] in ACTIVE:
                if job.get('api_job_id'):
                    # We own the media API job: keep polling that same job, never resubmit.
                    self._spawn(self.run, job['id'], None)
                elif job.get('transport') == 'https':
                    # Submitted over HTTP but the response was lost before we recorded the
                    # API id. run() looks the job up by its idempotency key first; it only
                    # re-POSTs when nothing was ever submitted (the key makes even that a
                    # replay, not a second generation).
                    self._spawn(self.run, job['id'], None)
                elif job.get('prompt_id') or job.get('transport') != 'https':
                    # Legacy job created through the old SSH path: it has no API job id, so
                    # adopting it would mean generating the same media a second time.
                    self.update(job['id'], status='uncertain', transport='legacy-ssh',
                                connection_warning='该任务由旧的 SSH 通路提交，HTTP 迁移后不会自动接管或重复生成；'
                                                  '请在工作台核实服务器结果后再处理。')
                else:
                    self.update(job['id'], status='uncertain',
                                connection_warning='工作台重启时提交尚未确认；未新建任务，请稍后对账。')
            elif job['status'] == 'uncertain' and job.get('transport') == 'https':
                # An uncertain job may still be running on the server. Reconcile it under
                # the same idempotency key instead of leaving it dead in the UI.
                self._spawn(self.run, job['id'], None)

    def _spawn(self, target, *args):
        """Single seam for background jobs, so tests can drive them without mocking the
        shared threading module (which would also break local HTTP test servers)."""
        thread = threading.Thread(target=target, args=args, daemon=True)
        thread.start()
        return thread

    def _spawn_observer(self, job_id, api_job_id, stop):
        thread = threading.Thread(target=self.observe, args=(job_id, api_job_id, stop), daemon=True)
        with self.lock:
            self.observers[job_id] = (stop, thread)
        thread.start()
        return thread

    def media(self):
        """Media API client. Missing configuration is an explicit error, never SSH."""
        api = getattr(self, '_media', None)
        if api is None:
            api = MediaApi()
            self._media = api
        return api

    def media_state(self):
        try:
            api = self.media()
            return {'configured': True, 'base': api.base, 'transport': 'https'}
        except MediaApiError as exc:
            return {'configured': False, 'base': None, 'transport': 'https', 'error': str(exc)}

    def update(self, job_id, **changes):
        with self.lock:
            if changes.get('status') in ('done', 'error') and self.jobs[job_id]['status'] not in ('done', 'error'):
                changes.setdefault('finished_at', now())
            self.jobs[job_id].update(changes, updated_at=now())
            write(self.folder / job_id / 'job.json', self.jobs[job_id])

    def public(self, job_id):
        with self.lock:
            if job_id not in self.jobs:
                raise ValueError('生成任务不存在')
            job = copy.deepcopy(self.jobs[job_id])
        if job.get('asset_file'):
            job['asset_url'] = '/generation-assets/' + job_id
            if job['kind'] == 'video' and job['status'] == 'done':
                job['thumbnail_url'] = '/generation-thumbnails/' + job_id
        return job

    def listing(self, *, limit=30, kind=None, origin=None, trash=False):
        with self.lock:
            ids = sorted((key for key,job in self.jobs.items() if bool(job.get('deleted_at'))==trash and (not kind or job['kind']==kind) and (not origin or job.get('origin')==origin)),key=lambda key:self.jobs[key]['created_at'],reverse=True)[:limit]
            busy=any(j['status'] in ACTIVE for j in self.jobs.values())
        return {'models': list(MODELS.values()), 'jobs': [self.public(key) for key in ids], 'busy':busy,
                'transport': '4090 媒体 API · HTTPS/STCP', 'media_api': self.media_state()}

    def manage(self, identities, action):
        if action not in ('delete','restore'):raise ValueError('未知记录操作')
        if not isinstance(identities,list) or not 1<=len(identities)<=100 or any(not isinstance(x,str) for x in identities):raise ValueError('请选择 1–100 条生成记录')
        identities=list(dict.fromkeys(identities))
        with self.lock:
            for identity in identities:
                if identity not in self.jobs:raise ValueError('生成记录不存在')
                if self.jobs[identity]['status'] in ACTIVE:raise ValueError('生成中的任务不能删除，请等待完成')
            for identity in identities:
                self.update(identity,deleted_at=now() if action=='delete' else None)
        return {'count':len(identities),'action':action,'ids':identities}

    def reference_path(self, identity):
        return self.references.path(identity)

    def reference_info(self, identity):
        return self.references.info(identity)

    def upload_media_reference(self, kind, name, stream, length):
        return self.references.upload(kind, name, stream, length)

    def upload_reference(self,payload):
        return self.references.image(payload)

    def reference_from_job(self, job_id):
        """Reuse a finished image result as a reference asset, entirely server side.

        The page only names the generation record. The file never travels back
        through the browser, and the resolved path must stay inside that record's
        own folder, so a forged id or a moved file is an explicit error.
        """
        if not isinstance(job_id, str) or not re.fullmatch(r'[a-f0-9]{32}', job_id):
            raise ValueError('生成记录编号无效，请重新选择图片')
        with self.lock:
            job = copy.deepcopy(self.jobs.get(job_id))
        if not job:
            raise ValueError('生成记录不存在，请重新选择图片')
        if job.get('deleted_at'):
            raise ValueError('这张图片已在回收站，请先恢复后再使用')
        if job.get('kind') != 'image' or job.get('status') != 'done':
            raise ValueError('只能选用已完成的图片生成结果')
        source = self.asset(job_id)
        folder = (self.folder / job_id).resolve()
        resolved = Path(source).resolve()
        if resolved.parent != folder or not resolved.is_file():
            raise ValueError('图片文件不在该生成记录的目录内，请重新选择')
        skill = job.get('image_skill') or {}
        label = str(skill.get('user_prompt') or job.get('input_prompt') or job.get('prompt') or '生成图片').strip()[:120]
        # Picking the same record again reuses the copy already on disk, so a repeated
        # choice cannot grow the reference folder or hand the page a new id each time.
        expected = job.get('sha256')
        if isinstance(expected, str):
            for manifest in self.references.folder.glob('*.json'):
                try:
                    record = json.loads(manifest.read_text())
                    if record.get('source_job') == job_id and record.get('source_sha256') == expected:
                        return self.references.info(record['id'])
                except (ValueError, OSError, KeyError):
                    continue
        return self.references.from_local_file(resolved, name=label or '生成图片', source_job=job_id, source_sha256=expected)

    def resolve_references(self, refs, frames=None):
        normalized = normalize_references(refs)
        for key in ('videos', 'audios'):
            if sum(self.references.info(identity).get('source_duration', self.references.info(identity).get('duration', 0)) for identity in normalized[key]) > 15 + 1e-6:
                raise ValueError('参考视频和独立音频的总时长各不得超过 15 秒；请先缩短片段')
        result = {key: [] for key in ('images', 'videos', 'audios')}
        for key in ('first', 'last'):
            if key in normalized:
                result[key] = self.references.prepare(normalized[key], 'image', inline=True)
        for key, kind in (('images', 'image'), ('videos', 'video'), ('audios', 'audio')):
            result[key] = [self.references.prepare(identity, kind, frames) for identity in normalized[key]]
        # Preserve this internal legacy accessor for older integrations; new requests normalize first.
        if refs.get('reference'):
            result['reference'] = result['images'][0]
        return result

    def health(self, model):
        """Live status of one fixed backend, taken from the media API's read-only probes."""
        if model not in MODELS:
            raise ValueError('模型不存在')
        checked_at = now()
        try:
            document = self.media().backends('health', model)
        except MediaApiUnknown as exc:
            return {'connected': None, 'status': 'unknown', 'busy': None, 'system': '', 'model': model,
                    'checked_at': checked_at, 'error': str(exc)}
        service = (document.get('services') or {}).get(model) or {}
        queue = service.get('queue')
        connected = service.get('connected')
        return {'connected': connected, 'status': service.get('status', 'unknown'),
                'busy': bool(queue.get('busy')) if isinstance(queue, dict) else None,
                'queue': queue, 'system': service.get('system', {}).get('comfyui_version', ''),
                'devices': service.get('devices', []), 'errors': service.get('errors', {}),
                'model': model, 'checked_at': service.get('checked_at', checked_at)}

    def backend_query(self, action, model):
        if action not in ('backend_health', 'backend_inventory'):
            raise ValueError('仅支持固定只读部署查询')
        selected = model_selection(model)
        try:
            result = self.media().backends('inventory' if action == 'backend_inventory' else 'health', model)
            result['source'] = 'media-api read-only probes over HTTPS'
            result['transport'] = 'https'
            return result
        except MediaApiUnknown as exc:
            checked_at = now()
            result = {'checked_at': checked_at, 'source': 'media-api over HTTPS', 'transport': 'https',
                    'read_only': True, 'scope': '固定接入的 ComfyUI 服务',
                    'limitations': ['无法读取媒体服务响应，当前服务器库存与状态未知；配置不代表部署'],
                    'services': {key: {'model': key, 'checked_at': checked_at, 'status': 'unknown',
                                      'connected': None, 'system': {}, 'devices': [], 'queue': None,
                                      'errors': {'transport': '媒体服务查询失败（' + type(exc).__name__
                                                     + '）；未使用静态配置冒充部署状态'}} for key in selected}}
            if model is None:
                result['inference_status'] = {'status': 'unknown', 'checked_at': checked_at,
                    'source': 'not covered by the media API (18088 inference service)',
                    'state': {'loaded': None, 'in_flight': None},
                    'error': '媒体 API 未探测 18088 推理服务，本字段保持未知'}
            return result
        except MediaApiError:
            # The API answered and rejected the request (for example an unregistered
            # service label). That is a configuration fact, not an unknown state.
            raise

    def backend_health(self, model=None):
        """Fresh, fixed-service status; no model execution or queue contents."""
        return self.backend_query('backend_health', model)

    def backend_inventory(self, model=None):
        """Fresh loader enums and node registrations, separate from configured recipes."""
        return enrich_inventory(self.backend_query('backend_inventory', model))

    def close(self):
        # Disconnect progress observers only. ComfyUI owns the durable GPU job.
        with self.lock:
            observers = list(self.observers.values())
        for stop, thread in observers:
            stop.set()
        for stop, thread in observers:
            thread.join(timeout=4)

    def start(self, payload, *, origin='assistant', conversation_id=None):
        request_id=payload.get('request_id')
        if request_id is not None and (not isinstance(request_id,str) or not re.fullmatch(r'[a-f0-9-]{20,40}',request_id)):
            raise ValueError('无效生成请求编号')
        fingerprint=hashlib.sha256(json.dumps({k:payload.get(k) for k in ('model','prompt','size','seed','frames','steps','cfg','slug','version','image_style','lora','lora_strength','lora_trigger','workflow','video_checkpoint','video_accelerator','video_loras','references','denoise','video_sampling','video_refine_steps','video_refine_denoise','video_upscale')},sort_keys=True,ensure_ascii=False).encode()).hexdigest()
        job_id = uuid.uuid4().hex
        payload=dict(payload)
        if MODELS.get(payload.get('model'), {}).get('kind') == 'video':
            payload['references'] = normalize_references(payload.get('references', {}), payload.get('workflow', 't2v'))
        else:
            payload['references'] = normalize_image_references(payload.get('references', {}), payload.get('model'))
        resolved = self.resolve_references(payload.get('references', {}), int(payload.get('frames', 56)))
        allowed_fields = {'remote_file', 'width', 'height', 'duration', 'source_duration', 'frames', 'has_audio', 'name', 'id', 'kind', 'imageB64'}
        slim = lambda item: {k: v for k, v in item.items() if k in allowed_fields}
        payload['_references'] = {key: [slim(item) for item in value] if isinstance(value, list) else slim(value) for key, value in resolved.items()}
        model, prompt, settings, graph = build(payload, job_id)
        descriptors = [resolved[key] for key in ('first', 'last') if key in resolved]
        descriptors += [item for key in ('images', 'videos', 'audios') for item in resolved[key]]
        safe_descriptors = [{k: v for k, v in item.items() if not k.startswith('_') and k != 'imageB64'} for item in descriptors]
        if descriptors:
            settings['reference_media'] = {item['id']: item for item in safe_descriptors}
        # HTTP contract: the API owns graph building, so the workbench sends semantic
        # parameters plus reference file ids. Only ALLOWED_PARAMS fields are sent; the
        # local graph above stays as an audit/validation artifact and is never shipped.
        api_model = MODEL_IDS.get(model['id'])
        if not api_model:
            raise ValueError(f'{model["name"]} 未接入 4090 媒体 API，HTTP 迁移后不再通过 SSH 生成')
        reference_ids = {}
        for key in ('first', 'last'):
            if resolved.get(key):
                reference_ids[key] = resolved[key]['id']
        for key in ('images', 'videos', 'audios'):
            if resolved[key]:
                reference_ids[key] = [item['id'] for item in resolved[key]]
        api_params = {'model': api_model, 'prompt': prompt, 'size': settings['size'], 'seed': settings['seed']}
        if model['kind'] == 'image':
            api_params.update(steps=settings['steps'], cfg=settings['cfg'])
            if reference_ids:
                api_params['references'] = reference_ids
            if 'denoise' in settings:
                api_params['denoise'] = settings['denoise']
            # Krea2-only LoRA: send the allowlisted id plus numeric strength; the media API
            # resolves the LoRA file name server-side. build() already prepended the trigger
            # to the prompt, so the API's own trigger check cannot duplicate it.
            if model['id'] == 'krea2' and settings.get('lora'):
                api_params.update(lora=settings['lora'], lora_strength=settings['lora_strength'],
                                  lora_trigger=settings.get('lora_trigger') or '')
        else:
            # the media API validates these options strictly: send the ids the workbench
            # actually resolved (never None), and omit what the caller left unspecified so
            # the server applies its own documented default instead of rejecting the job
            api_params.update(workflow=settings.get('workflow') or payload.get('workflow') or 't2v',
                              frames=settings['frames'])
            for key in ('video_checkpoint', 'video_accelerator'):
                if settings.get(key) is not None:
                    api_params[key] = settings[key]
            if settings.get('video_loras') is not None:
                # the API re-validates each row strictly as {id, strength}; the workbench
                # keeps name/file/modes locally and must not leak them over the wire
                api_params['video_loras'] = [{'id': row['id'], 'strength': row.get('strength', 1.0)}
                                             for row in settings['video_loras']]
            if settings.get('video_sampling') == 'dual':
                api_params.update({key: settings[key] for key in ('video_sampling', 'video_refine_steps', 'video_refine_denoise', 'video_upscale')})
            api_params['references'] = reference_ids
        upload_plan = []
        for item in descriptors:
            declared = {key: item[key] for key in ('width', 'height', 'fps', 'frames', 'has_audio',
                                                   'source_duration', 'source_frames')
                        if item.get(key) is not None}
            if item['kind'] == 'audio' and item.get('duration') is not None:
                declared['duration'] = item['duration']
            # video duration is intentionally not declared: the server measures it, and
            # container duration can differ from frames/24 beyond the tolerance.
            upload_plan.append({'id': item['id'], 'kind': item['kind'],
                                'name': str(item.get('name') or '参考素材')[:120],
                                '_local_path': item['_local_path'], 'meta': declared})
        api_key = request_id or job_id
        slug = payload.get('slug')
        version = None
        if slug:
            detail = self.store.detail(slug, payload.get('version'))
            version = detail['version']['id']
        with self.lock:
            if request_id:
                existing=next((j for j in self.jobs.values() if j.get('request_id')==request_id and j.get('origin')==origin and j.get('conversation_id')==conversation_id),None)
                if existing:
                    if existing.get('request_fingerprint')!=fingerprint:
                        raise ValueError('此请求编号已用于不同的生成描述，请重新提交')
                    return self.public(existing['id'])
            if any(job['status'] in ACTIVE for job in self.jobs.values()):
                raise ValueError('当前已有媒体任务，请等待它完成后再生成')
            self.jobs[job_id] = {'id': job_id, 'model': model['id'], 'model_name': model['name'],
                'checkpoint': settings.get('checkpoint_file',model['checkpoint']), 'kind': model['kind'], 'prompt': prompt, 'settings': settings,
                'input_prompt': (payload.get('image_skill') or {}).get('user_prompt') or str(payload.get('prompt', '')).strip(),
                'slug': slug, 'base_version': version, 'created_at': now(), 'status': 'connecting',
                'origin':origin,'conversation_id':conversation_id,'request_id':request_id,'request_fingerprint':fingerprint,
                'chat_request':payload.get('chat_request'),'reference_job':payload.get('reference_job'),
                'transport': 'https', 'idempotency_key': api_key, 'api_params': api_params,
                'reference_roles': reference_ids, 'submit_state': 'pending'}
            if payload.get('image_skill'):self.jobs[job_id]['image_skill']=copy.deepcopy(payload['image_skill'])
            self.update(job_id)
            write(self.folder / job_id / 'workflow.json', graph)
            if upload_plan:
                write(self.folder / job_id / 'reference-uploads.json', upload_plan)
        self._spawn(self.run, job_id, graph)
        return self.public(job_id)
    def submit_http(self, job_id, api):
        """Upload normalised references, then create exactly one API job per key."""
        job = self.public(job_id)
        recorded = self.folder / job_id / 'reference-files.json'
        files = json.loads(recorded.read_text()) if recorded.is_file() else {}
        plan_path = self.folder / job_id / 'reference-uploads.json'
        if plan_path.is_file():
            for item in json.loads(plan_path.read_text()):
                if item['id'] in files:
                    continue
                if self.public(job_id).get('cancel_requested'):
                    raise CancelledBeforeSubmit()
                source = Path(item['_local_path'])
                if not source.is_file():
                    raise ValueError('参考素材文件已丢失，请重新上传后再生成')
                record = api.upload(source, item['kind'], item['name'], item.get('meta'))
                files[item['id']] = record['file_id']
            # Persist the reference ids before submitting: a restart or a lost submit
            # response reuses the same file ids, so the idempotency fingerprint matches.
            write(recorded, files)
            with self.lock:
                self.update(job_id, reference_files=files)
        params = copy.deepcopy(job['api_params'])
        roles = params.get('references') or {}
        for key, value in list(roles.items()):
            if isinstance(value, list):
                roles[key] = [files.get(item) or self._reference_file(job_id, item) for item in value]
            elif value:
                roles[key] = files.get(value) or self._reference_file(job_id, value)
        if any(not value for value in [x for v in roles.values() for x in (v if isinstance(v, list) else [v])]):
            raise ValueError('参考素材未能解析为服务器素材编号，未提交生成')
        with self.lock:
            if self.jobs[job_id].get('cancel_requested'):
                raise CancelledBeforeSubmit()
            self.update(job_id, submit_state='submitting')
        try:
            created = api.submit(params, job['idempotency_key'])
        except MediaApiUnknown:
            # The job may already exist server side. Keep the key; reconcile adopts it.
            self.update(job_id, submit_state='unknown')
            raise
        self.update(job_id, api_job_id=created['id'], api_status=created.get('status'),
                    submit_state='confirmed', transport='https', status='queued', error=None)
        if self.public(job_id).get('cancel_requested'):
            # A cancel landed while the POST was in flight: stop the job we just created.
            try:
                result = api.cancel(created['id'])
                self.update(job_id, api_status=result.get('status'),
                            status=API_STATE.get(result.get('status'), 'running'))
            except MediaApiError as exc:
                self.update(job_id, connection_warning='任务已提交，但停止请求未被服务器接受：' + str(exc)[:200])
        return created['id']

    def _reference_file(self, job_id, reference_id):
        """Reuse an already uploaded asset instead of uploading it a second time."""
        job = self.public(job_id)
        return (job.get('reference_files') or {}).get(reference_id)

    def apply_event(self, job_id, event):
        """Map media API events to user-facing stages; no invented node progress."""
        with self.lock:
            job = self.jobs[job_id]
            if job['status'] not in ACTIVE:
                return
            cursor = event.get('seq')
            if isinstance(cursor, int) and cursor > (job.get('event_cursor') or 0):
                # the cursor is persisted as each event is consumed, so a dropped SSE
                # stream or a restart resumes after the last event actually received
                self.update(job_id, event_cursor=cursor)
        if event.get('type') == 'execution_progress':
            data = event.get('data') or {}
            if data.get('phase') not in ('loading','encoding','sampling','decoding','saving','preparing','video'):
                return
            progress = {k: data[k] for k in ('phase','label','node') if k in data}
            previous = self.jobs[job_id].get('progress') or {}
            progress.update(observed_at=now(), source='ComfyUI via media-api',
                            started_at=previous.get('started_at', now()) if previous.get('phase') == data['phase'] else now())
            current, total = data.get('current'), data.get('total')
            if data['phase'] == 'sampling' and all(isinstance(x, (int,float)) and not isinstance(x,bool) and math.isfinite(x) for x in (current,total)) and total > 0 and 0 <= current <= total:
                progress.update(current=current,total=total,percent=round(100*current/total))
            self.update(job_id, progress=progress, progress_connected=True)
            return
        if event.get('type') == 'progress' and (self.jobs[job_id].get('progress') or {}).get('source') == 'ComfyUI via media-api':
            return  # queue heartbeat must not erase real sampling values
        phase, label = EVENT_PROGRESS.get(event.get('type'), (None, None))
        if not phase:
            if event.get('type') == 'poll_error':
                raw_error = str((event.get('data') or {}).get('error', ''))
                safe_error = safe_upstream_text(raw_error, cap=200) or '详情未返回可显示说明'
                self.update(job_id, connection_warning='服务器进度查询暂时异常：' + safe_error)
            return
        previous = self.jobs[job_id].get('progress') or {}
        progress = {'phase': phase, 'label': label, 'observed_at': now(),
                    'source': 'media-api events'}
        if previous.get('phase') == phase:
            progress['started_at'] = previous.get('started_at', now())
        else:
            progress['started_at'] = now()
        self.update(job_id, progress=progress, progress_connected=True)

    def observe(self, job_id, api_job_id, stop):
        """Follow the API's bounded SSE stream; polling stays authoritative."""
        while not stop.is_set():
            # always resume from the persisted cursor: apply_event advances it per event,
            # so a mid-stream drop never replays events the workbench already consumed
            since = self.public(job_id).get('event_cursor') or 0
            try:
                self.media().events(api_job_id, since,
                                    lambda event: self.apply_event(job_id, event), stop,
                                    max_seconds=600)
            except (MediaApiError, OSError, ValueError):
                pass
            with self.lock:
                if self.jobs[job_id]['status'] in ACTIVE:
                    self.update(job_id, progress_connected=False)
            if stop.wait(5):
                break

    def run(self, job_id, graph=None):
        job = self.jobs[job_id]
        stop = threading.Event()
        try:
            api = self.media()
            api_job_id = job.get('api_job_id')
            if not api_job_id:
                if job.get('submit_state') in ('unknown', 'submitting'):
                    # A lost submit response proves nothing: look the job up by its key
                    # and adopt it. Absence is not evidence that nothing was submitted.
                    found = api.find_by_request(job['idempotency_key'])
                    if not found:
                        self.update(job_id, status='uncertain',
                                    connection_warning='提交回执丢失，且未能按幂等键定位服务器任务；'
                                                      '未重复提交，请稍后在卡片上重新核实')
                        return
                    self.update(job_id, api_job_id=found['id'], api_status=found.get('status'),
                                submit_state='confirmed', transport='https')
                    api_job_id = found['id']
                    self._apply_pending_cancel(job_id, api, api_job_id)
                else:
                    api_job_id = self.submit_http(job_id, api)
                    if api_job_id is None:
                        return
            else:
                # restart with a known API id: a persisted stop intent must be replayed
                self._apply_pending_cancel(job_id, api, api_job_id)
            observer = self._spawn_observer(job_id, api_job_id, stop)
            deadline = time.monotonic() + 3600
            failures = 0
            while time.monotonic() < deadline:
                try:
                    remote_job = api.job(api_job_id)
                    failures = 0
                except MediaApiUnknown as exc:
                    failures += 1
                    if failures >= 12:
                        raise
                    self.update(job_id, status='reconnecting',
                                connection_warning='暂时无法确认媒体服务状态，正在重连；请勿重复提交')
                    time.sleep(5)
                    continue
                # a successful poll clears transient warnings, but a stop that the server
                # refused must stay visible: otherwise the user reads silence as success
                self.update(job_id, backend_checked_at=now(),
                            connection_warning=self.public(job_id).get('cancel_note'),
                            api_status=remote_job.get('status'))
                state = API_STATE.get(remote_job.get('status'))
                if state == 'downloading':
                    return self.finish(job_id, remote_job, api)
                if state == 'error':
                    raw = str(remote_job.get('error') or remote_job.get('uncertain_reason')
                              or '生成失败')
                    reason = safe_upstream_text(raw, cap=200) or '生成失败'
                    raise ValueError(reason)
                if state == 'cancelled':
                    self.update(job_id, status='cancelled', error=None)
                    return
                if state == 'uncertain':
                    raw_reason = str(remote_job.get('uncertain_reason') or '')
                    safe_reason = safe_upstream_text(raw_reason, cap=200) or '服务器未返回可显示的状态说明'
                    self.update(job_id, status='uncertain',
                                connection_warning='服务器任务状态无法确定：'
                                + safe_reason
                                + '；请勿重复提交，可稍后核实同一任务')
                    return
                if state:
                    self.update(job_id, status=state)
                time.sleep(4)
            # The wait budget expired while the server job may still be running: that is
            # an unknown state, not a failure, and never a licence to resubmit.
            self.update(job_id, status='uncertain',
                        connection_warning='等待媒体服务超过 60 分钟，服务器任务可能仍在运行；'
                                          '未标记失败，也未重复提交，可稍后重新核实')
        except CancelledBeforeSubmit:
            self.update(job_id, status='cancelled', error=None,
                        connection_warning='任务尚未提交到媒体服务，已在工作台停止；服务器未收到生成请求')
        except MediaApiUnknown as exc:
            # The anti-resubmit sentence is the part carrying business meaning: truncate the
            # transport detail, never the tail. A word-boundary cut can still land inside a
            # tag (no space before it), so any dangling "<…" is removed before we append the
            # tail — no half tag ever reaches the card.
            tail = '；服务器任务可能仍在运行，请勿重复提交'
            detail = ' '.join(str(exc).split())
            if len(detail) > 600:
                cut = detail[:600]
                space = cut.rfind(' ')
                if space > 0:
                    cut = cut[:space]            # never cut mid-token: no half words, no half tags
                if cut.rfind('<') > cut.rfind('>'):
                    cut = cut[:cut.rfind('<')]   # drop a dangling "<…" left by the word cut
                detail = cut.rstrip() + '…'
            self.update(job_id, status='uncertain',
                        connection_warning=(detail or '媒体服务未返回可显示的状态说明') + tail)
        except Exception as exc:
            self.update(job_id, status='error', error=str(exc)[:2000])
        finally:
            stop.set()
            with self.lock:
                self.observers.pop(job_id, None)

    def reconcile(self, job_id):
        """Re-check an uncertain job under the same idempotency key; never resubmit."""
        job = self.public(job_id)
        if job['status'] != 'uncertain':
            raise ValueError('仅状态待核实的任务需要对账')
        if job.get('transport') == 'legacy-ssh':
            raise ValueError('旧 SSH 通路任务不能通过媒体 API 对账，请在服务器侧核实后处理')
        api = self.media()
        api_job_id = job.get('api_job_id') or (api.find_by_request(job.get('idempotency_key')) or {}).get('id')
        if not api_job_id:
            raise ValueError('仍未能定位服务器任务；未重复提交，请稍后再试')
        self.update(job_id, api_job_id=api_job_id, submit_state='confirmed', transport='https',
                    status='reconnecting', connection_warning='正在按原任务编号重新核实服务器状态')
        self._spawn(self.run, job_id, None)
        return self.public(job_id)

    def _apply_pending_cancel(self, job_id, api, api_job_id):
        """Replay a durably recorded stop intent onto the API job we just confirmed.

        Without this, a cancel that arrived while the POST was in flight would be lost
        after a restart adopted the job, and generation would continue.
        """
        if not self.public(job_id).get('cancel_requested'):
            return
        try:
            result = api.cancel(api_job_id)
        except MediaApiUnknown as exc:
            note = '停止请求结果未知：' + str(exc)[:200] + '；服务器任务可能仍在运行'
            self.update(job_id, cancel_note=note, connection_warning=note)
            return
        except MediaApiError as exc:
            note = '停止请求被服务器拒绝：' + str(exc)[:200] + '；任务继续，未伪称已取消'
            self.update(job_id, cancel_note=note, connection_warning=note)
            return
        status = API_STATE.get(result.get('status'))
        # a job that already finished successfully must never be rewritten as cancelled
        if result.get('status') in ('cancelled', 'cancel_requested', 'cancelling'):
            self.update(job_id, api_status=result.get('status'), cancel_note=None,
                        status='cancelled' if status == 'cancelled' else 'running')
        else:
            self.update(job_id, api_status=result.get('status'),
                        connection_warning='服务器已接受停止请求，正在等待任务退出')

    def cancel(self, job_id):
        """Cancel only a job the media API actually owns; record intent durably."""
        with self.lock:
            job = copy.deepcopy(self.jobs[job_id])
            if job['status'] in ('done', 'cancelled'):
                raise ValueError('该任务已结束，无需停止')
            if job.get('transport') == 'legacy-ssh':
                raise ValueError('旧 SSH 通路任务无法通过媒体 API 停止，请在服务器侧核实；未伪称已取消')
            # the stop intent is persisted under the same lock the submit path gates on,
            # so a concurrent submit cannot slip past it
            self.update(job_id, cancel_requested=True, cancel_requested_at=now())
            submit_state = self.jobs[job_id].get('submit_state')
            api_job_id = self.jobs[job_id].get('api_job_id')
        if not api_job_id and submit_state in ('unknown', 'submitting'):
            api_job_id = (self.media().find_by_request(job['idempotency_key']) or {}).get('id')
            if api_job_id:
                self.update(job_id, api_job_id=api_job_id, submit_state='confirmed')
        if not api_job_id:
            if submit_state == 'pending':
                # Nothing has been sent yet; run() re-checks the flag before submitting.
                self.update(job_id, status='cancelled', error=None,
                            connection_warning='任务尚未提交到媒体服务，已在工作台停止；服务器未收到生成请求')
                return self.public(job_id)
            self.update(job_id, connection_warning='提交尚未确认，已登记停止意图；'
                                                  '一旦确认服务器任务将立即停止，请勿重复提交')
            return self.public(job_id)
        try:
            result = self.media().cancel(api_job_id)
        except MediaApiUnknown as exc:
            note = '停止请求结果未知：' + str(exc)[:200] + '；服务器任务可能仍在运行，未伪称已取消'
            self.update(job_id, cancel_note=note, connection_warning=note)
            raise
        except MediaApiError as exc:
            note = '停止请求被服务器拒绝：' + str(exc)[:200] + '；任务继续，未伪称已取消'
            self.update(job_id, cancel_note=note, connection_warning=note)
            raise
        status = API_STATE.get(result.get('status'), 'running')
        self.update(job_id, api_status=result.get('status'), cancel_note=None,
                    status='cancelled' if status == 'cancelled' else status,
                    connection_warning=None if status == 'cancelled' else '服务器正在停止任务，请稍候')
        return self.public(job_id)

    def finish(self, job_id, remote_job, api):
        """Download the API result atomically and verify its sha256 before success."""
        job = self.jobs[job_id]
        accepted = {'.png', '.jpg', '.webp'} if job['kind'] == 'image' else {'.mp4', '.webm'}
        meta = api.result_meta(remote_job['id'])
        extension = CONTENT_EXT.get((meta.get('content_type') or '').split(';')[0].strip())
        if extension not in accepted:
            raise ValueError('模型返回了不支持的媒体类型：' + str(meta.get('content_type') or '未知'))
        path = self.folder / job_id / ('result' + extension)
        downloaded = api.download_result(remote_job['id'], path,
                                         expect_sha256=remote_job.get('output_sha256'),
                                         expect_bytes=remote_job.get('output_bytes'))
        checked = check_asset(path, 'poster' if job['kind'] == 'image' else 'video')
        if checked['sha256'] != downloaded['sha256'] or checked['bytes'] != downloaded['bytes']:
            path.unlink(missing_ok=True)
            raise ValueError('结果文件本地校验与服务器记录不一致，未标记成功')
        note = job.get('cancel_note')
        if job.get('cancel_requested') and not note:
            # the stop intent lost the race to a finished render: say so plainly instead
            # of leaving "正在停止" on a card that actually produced a result
            note = '停止请求已登记，但服务器在此之前已完成生成；结果已保存'
        self.update(job_id, status='done', asset_file='result' + extension, bytes=checked['bytes'],
                    sha256=checked['sha256'], error=None, api_output_sha256=remote_job.get('output_sha256'),
                    api_job_id=remote_job['id'], connection_warning=note)
    def asset(self, job_id):
        job = self.public(job_id)
        if job['status'] != 'done' or not re.fullmatch(r'result\.(png|jpg|webp|mp4|webm)', job.get('asset_file', '')):
            raise ValueError('媒体结果尚未就绪')
        return self.folder / job_id / job['asset_file']

    def thumbnail(self, job_id):
        source = self.asset(job_id)
        if source.suffix not in {'.mp4', '.webm'}:
            raise ValueError('仅视频结果支持视频缩略图')
        poster = source.parent / 'thumbnail.jpg'
        # Legacy results are included. Extract only when the thumbnail is requested,
        # outside the job lock, so listing and generation progress remain responsive.
        with self.thumbnail_lock:
            if poster.is_file():
                return poster
            ffmpeg = shutil.which('ffmpeg')
            if not ffmpeg and Path('/opt/homebrew/bin/ffmpeg').is_file():
                ffmpeg = '/opt/homebrew/bin/ffmpeg'
            if not ffmpeg:
                raise ValueError('视频缩略图暂不可用，仍可播放原视频')
            temporary = poster.with_name('.thumbnail-' + uuid.uuid4().hex + '.jpg')
            try:
                subprocess.run([ffmpeg, '-hide_banner', '-loglevel', 'error', '-nostdin',
                                '-i', str(source), '-frames:v', '1', '-an',
                                '-vf', 'scale=640:640:force_original_aspect_ratio=decrease',
                                '-q:v', '3', str(temporary)],
                               stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                               check=True, timeout=20)
                check_asset(temporary, 'poster')
                temporary.replace(poster)
            except (subprocess.SubprocessError, OSError, ValueError):
                raise ValueError('视频缩略图暂不可用，仍可播放原视频') from None
            finally:
                temporary.unlink(missing_ok=True)
        return poster

    def save(self, job_id, payload):
        with self.lock, self.store.lock:
            job = self.public(job_id)
            mode = payload.get('mode', 'new')
            if mode not in ('new', 'insert'):
                raise ValueError('不支持的保存方式')
            saves = job.get('saved', {})
            if mode in saves:
                return saves[mode]
            path = self.asset(job_id)
            if mode == 'insert':
                if job['kind'] != 'image' or not job.get('slug'):
                    raise ValueError('此结果无法插入原作品，请另存为新作品')
                article = self.store.detail(job['slug'])
                if article['head'] != job['base_version']:
                    raise ValueError('原作品已有新版本，请先下载图片后在最新版插入，或另存为新作品')
            else:
                article = self.store.create({'type': 'gallery' if job['kind'] == 'image' else 'video',
                                             'title': job['prompt'][:60]})
            blocks = [{'index': i} for i in range(len(article['blocks']))]
            data = {'title': article['title'], 'base_version': article['head'], 'blocks': blocks,
                    'summary': 'AI 生成：' + job['model_name'] + ' · 种子 ' + str(job['settings']['seed'])}
            if job['kind'] == 'image':
                blocks.append({'kind': 'image', 'data': base64.b64encode(path.read_bytes()).decode()})
            else:
                with path.open('rb') as stream:
                    upload = self.store.accept_upload(article['slug'], path.name, stream, path.stat().st_size)
                studio = copy.deepcopy(article['studio'])
                studio['video'] = {'upload': upload['upload']}
                studio['script'] = job['prompt']
                data['studio'] = studio
            result = self.store.save(article['slug'], data)
            saved = {'slug': article['slug'], 'version': result['version']}
            saves[mode] = saved
            self.update(job_id, saved=saves)
            return saved
