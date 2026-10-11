"""Existing SSH bridge with fixed ComfyUI services and read-only inference status."""
import base64
import asyncio
import datetime
import errno
import fcntl
import http.client
import json
import re
import sqlite3
import sys
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

PORTS = {'qi21': 8192, 'krea2': 8188, 'h3': 8191}
INVENTORY_LOADERS = {
    'UNETLoader': ('diffusion_models', 'unet_name'),
    'CLIPLoader': ('text_encoders', 'clip_name'),
    'VAELoader': ('vaes', 'vae_name'),
    'LoraLoaderModelOnly': ('loras', 'lora_name'),
}
COMMON_INVENTORY_NODES = tuple(INVENTORY_LOADERS) + ('KSampler', 'VAEDecode', 'SaveImage')
INVENTORY_NODES = {
    'qi21': COMMON_INVENTORY_NODES + ('TextEncodeQwenImage21', 'EmptySD3LatentImage', 'ModelSamplingAuraFlow'),
    'krea2': COMMON_INVENTORY_NODES + ('CLIPTextEncode', 'ConditioningZeroOut', 'EmptyLatentImage'),
    'h3': tuple(INVENTORY_LOADERS) + (
        'MiniMaxH3Director', 'MiniMaxH3ImageToVideo', 'MiniMaxH3ReferenceToVideo',
        'MiniMaxH3AddGuide', 'MiniMaxH3SigmaShift', 'BasicScheduler', 'BasicGuider',
        'RandomNoise', 'KSamplerSelect', 'SamplerCustomAdvanced', 'LTXVSeparateAVLatent',
        'VAEDecode', 'VAEDecodeAudio', 'CreateVideo', 'SaveVideo', 'LoadImage', 'LoadVideo',
        'GetVideoComponents', 'LoadAudio'),
}
READ_ONLY_ROUTES = {'/system_stats', '/queue'} | {
    '/object_info/' + name for names in INVENTORY_NODES.values() for name in names
}


class NoReadOnlyRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def readonly_request(port, route):
    """Only fixed Comfy GETs; no proxy, redirect, user route, or HTTP request body."""
    allowed = (port in PORTS.values() and route in READ_ONLY_ROUTES) or (port == 18088 and route == '/status')
    if not allowed:
        raise ValueError('部署查询仅支持固定的只读接口')
    req = urllib.request.Request(f'http://127.0.0.1:{port}' + route, method='GET')
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoReadOnlyRedirect())
    with opener.open(req, timeout=3) as response:
        body = response.read(8 * 1024 * 1024 + 1)
    if len(body) > 8 * 1024 * 1024:
        raise ValueError('部署查询响应过大')
    result = json.loads(body)
    if not isinstance(result, dict):
        raise ValueError('部署查询响应格式无效')
    return result


def query_readonly(port, route):
    try:
        data = readonly_request(port, route)
        if route == '/system_stats' and (not isinstance(data.get('system'), dict) or not isinstance(data.get('devices'), list)):
            raise ValueError('无效系统状态')
        if route == '/queue' and any(not isinstance(data.get(key), list) for key in ('queue_running', 'queue_pending')):
            raise ValueError('无效队列状态')
        return data, None
    except urllib.error.HTTPError as exc:
        code = exc.code
        exc.close()
        return None, f'服务 HTTP {code}'
    except (OSError, ValueError, http.client.HTTPException):
        # HTTP bodies and exception strings may contain local paths or prompts.
        return None, '只读接口连接失败或响应无效'


def enum_files(schema, field):
    inputs = schema.get('input', {})
    spec = inputs.get('required', {}).get(field, inputs.get('optional', {}).get(field))
    if not isinstance(spec, list) or not spec or not isinstance(spec[0], list):
        raise ValueError('模型加载器没有返回文件枚举')
    files = []
    for value in spec[0]:
        if not isinstance(value, str) or not value or len(value) > 512:
            continue
        value = value.replace('\\', '/')
        if value.startswith('/') or re.match(r'^[a-zA-Z]:', value) or '..' in value.split('/') or any(ord(c) < 32 for c in value):
            continue
        files.append(value)
    return sorted(set(files))


def inference_status(data, error, checked_at):
    state = {'loaded': None, 'in_flight': None}
    if isinstance(data, dict):
        for key in ('loaded', 'loading'):
            if isinstance(data.get(key), bool):
                state[key] = data[key]
        if type(data.get('in_flight')) is int and data['in_flight'] >= 0:
            state['in_flight'] = data['in_flight']
        for key in ('model', 'model_name', 'status'):
            value = data.get(key)
            if isinstance(value, str) and len(value) <= 200 and not value.startswith('/') and '\\' not in value and not any(ord(c) < 32 for c in value):
                state[key] = value
    result = {'status': 'unavailable' if error else 'ok', 'checked_at': checked_at,
              'source': 'Strata inference service on GPU host / loopback:18088/status',
              'state': state, 'scope': '仅顶层白名单字段；未返回的字段为 unknown，不推断模型或整张 GPU 空闲'}
    if error:
        result['error'] = error
    return result


def backend_query(payload):
    action = payload.get('action')
    model = payload.get('model')
    if set(payload) - {'action', 'model'} or action not in ('backend_health', 'backend_inventory'):
        raise ValueError('无效部署查询参数')
    if model is not None and (not isinstance(model, str) or model not in PORTS):
        raise ValueError('请选择已接入的模型服务')
    selected = [model] if model else list(PORTS)
    routes = {key: ['/system_stats', '/queue'] + (
        ['/object_info/' + name for name in INVENTORY_NODES[key]] if action == 'backend_inventory' else []) for key in selected}
    with ThreadPoolExecutor(max_workers=12) as executor:
        pending = {(key, route): executor.submit(query_readonly, PORTS[key], route)
                   for key in selected for route in routes[key]}
        inference = executor.submit(query_readonly, 18088, '/status') if model is None else None
        responses = {key: future.result() for key, future in pending.items()}
        inference_result = inference.result() if inference else None
    checked_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
    services = {}
    for key in selected:
        errors = {route: responses[key, route][1] for route in routes[key] if responses[key, route][1]}
        connected = any(responses[key, route][0] is not None for route in routes[key])
        stats = responses[key, '/system_stats'][0] or {}
        system = {k: v for k, v in stats.get('system', {}).items()
                  if k in ('comfyui_version', 'python_version', 'pytorch_version', 'embedded_python', 'ram_total', 'ram_free') and isinstance(v, (str, int, float, bool))}
        device_fields = ('name', 'type', 'index', 'vram_total', 'vram_free', 'torch_vram_total', 'torch_vram_free')
        devices = [{k: v for k, v in device.items() if k in device_fields and isinstance(v, (str, int, float))}
                   for device in stats.get('devices', []) if isinstance(device, dict)]
        queue = responses[key, '/queue'][0]
        queue_summary = None if queue is None else {
            'running': len(queue.get('queue_running', [])), 'pending': len(queue.get('queue_pending', [])),
            'busy': bool(queue.get('queue_running') or queue.get('queue_pending'))}
        item = {'model': key, 'port': PORTS[key], 'checked_at': checked_at,
                'source': f'ComfyUI service on GPU host / loopback:{PORTS[key]}',
                'connected': connected, 'system': system, 'devices': devices,
                'queue': queue_summary, 'errors': errors,
                'sources': {'system': '/system_stats', 'devices': '/system_stats', 'queue': '/queue'}}
        if action == 'backend_inventory':
            nodes, inventory = {}, {}
            for name in INVENTORY_NODES[key]:
                route = '/object_info/' + name
                data, error = responses[key, route]
                available = None if error else isinstance(data.get(name), dict)
                nodes[name] = {'available': available, 'source': route}
                if error:
                    nodes[name]['error'] = error
                if name in INVENTORY_LOADERS:
                    group, field = INVENTORY_LOADERS[name]
                    entry = {'files': [], 'source': route + ' input.' + field,
                             'status': 'unavailable' if error else ('ok' if available else 'missing')}
                    if available:
                        try:
                            entry['files'] = enum_files(data[name], field)
                        except (TypeError, AttributeError, ValueError):
                            entry['status'] = 'unavailable'
                            errors[route] = '模型加载器没有返回有效文件枚举'
                        if name == 'CLIPLoader':
                            try:
                                entry['types'] = enum_files(data[name], 'type')
                            except (TypeError, AttributeError, ValueError):
                                entry['types'] = None
                    inventory[group] = entry
            item.update(nodes=nodes, inventory=inventory)
        item['status'] = 'unavailable' if not connected else ('partial' if errors else 'ok')
        services[key] = item
    result = {'checked_at': checked_at, 'source': 'live_comfyui_over_existing_ssh',
            'read_only': True, 'services': services,
            'scope': '固定接入的三个 ComfyUI 服务；模型库存来自加载器当前枚举的可选文件',
            'limitations': ['不覆盖 4090 上全部磁盘文件或其他未接入服务',
                            '枚举可选文件不表示模型已加载，也不保证权重兼容或可成功推理',
                            '设备显存来自 ComfyUI system_stats，不包含 GPU 进程清单或实时利用率',
                            '服务队列为空不代表整张 GPU 空闲；Strata 仅按需读取固定 /status']}
    if inference_result is not None:
        result['inference_status'] = inference_status(*inference_result, checked_at)
    return result

async def observe(payload):
    # ComfyUI sends execution events only to the client that submitted the job.
    # This connection observes that client's events; it never submits or interrupts.
    import aiohttp
    port = PORTS[payload['model']]
    url = f"http://127.0.0.1:{port}/ws?" + urllib.parse.urlencode({'clientId': payload['client_id']})
    async with aiohttp.ClientSession() as session:
        async with session.ws_connect(url, heartbeat=15, max_msg_size=32*1024*1024) as ws:
            print(json.dumps({'type': 'observer_connected', 'data': {}}), flush=True)
            async for message in ws:
                if message.type == aiohttp.WSMsgType.TEXT:
                    event = json.loads(message.data)
                    if event.get('type') in ('executing', 'progress', 'progress_state', 'execution_start', 'execution_success', 'execution_error', 'execution_interrupted'):
                        # Never forward previews, stack traces or user prompts.
                        data = event.get('data', {})
                        safe = {k: data[k] for k in ('node', 'prompt_id', 'value', 'max') if k in data}
                        if event['type'] == 'progress_state':
                            safe['nodes'] = {key: {k: value[k] for k in ('node_id', 'value', 'max', 'state') if k in value}
                                             for key, value in data.get('nodes', {}).items()}
                        print(json.dumps({'type': event['type'], 'data': safe}), flush=True)
                elif message.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                    break

def request(port, route, payload=None):
    data = None if payload is None else json.dumps(payload).encode()
    req = urllib.request.Request(f'http://127.0.0.1:{port}' + route, data=data,
                                 headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=25) as response:
            body = response.read()
            return json.loads(body) if body.strip() else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read(16000).decode(errors='replace')
        raise ValueError(f'模型服务 HTTP {exc.code}: {detail}') from None

def optional_service_request(port, route, payload=None):
    try:
        return request(port, route, payload)
    except urllib.error.URLError as exc:
        # A stopped loopback service cannot hold GPU models or have a queue.
        # Timeouts and other errors remain fatal: its activity is unknown.
        if isinstance(exc.reason, OSError) and exc.reason.errno == errno.ECONNREFUSED:
            return None
        raise

def ensure_idle():
    db = '/home/admin01/.h3storyboard/h3storyboard.db'
    with sqlite3.connect('file:' + db + '?mode=ro', uri=True) as connection:
        leases = connection.execute('SELECT lease_expires_at FROM gpu_leases').fetchall()
    now = datetime.datetime.now(datetime.timezone.utc)
    if any(datetime.datetime.fromisoformat(row[0].replace('Z', '+00:00')) > now for row in leases):
        raise ValueError('4090 正被其他工作台使用，请稍后生成')
    available = []
    # The separate H3Storyboard service currently uses 8190 on this host.
    for port in (8188, 8190, 8191, 8192):
        queue = optional_service_request(port, '/queue')
        if queue is None:
            continue
        available.append(port)
        if queue.get('queue_running') or queue.get('queue_pending'):
            raise ValueError('4090 已有生成任务，请等待完成再提交')
    return available

def main(payload):
    if payload.get('action') in ('backend_health', 'backend_inventory'):
        return backend_query(payload)
    port = PORTS[payload['model']]
    action = payload['action']
    if action == 'upload_reference':
        relative = payload.get('remote_file', '')
        if payload['model'] != 'h3' or not re.fullmatch(r'article_studio_refs/[a-f0-9]{32}/reference\.(png|mp4|wav)', relative):
            raise ValueError('无效模型参考素材路径')
        length = payload.get('bytes', 0)
        if not isinstance(length, int) or not 0 < length <= 100_000_000:
            raise ValueError('参考素材超过 100 MB')
        binary = sys.stdin.buffer.read(length + 1)
        if len(binary) != length:
            raise ValueError('参考素材传输不完整')
        folder, name = relative.rsplit('/', 1)
        boundary = 'ArticleStudioReferenceUpload'
        fields = [('type', 'input'), ('subfolder', folder), ('overwrite', 'true')]
        body = bytearray()
        for key, value in fields:
            body.extend(f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'.encode())
        body.extend(f'--{boundary}\r\nContent-Disposition: form-data; name="image"; filename="{name}"\r\nContent-Type: application/octet-stream\r\n\r\n'.encode())
        body.extend(binary)
        body.extend(f'\r\n--{boundary}--\r\n'.encode())
        req = urllib.request.Request(f'http://127.0.0.1:{port}/upload/image', data=body,
                                     headers={'Content-Type': 'multipart/form-data; boundary=' + boundary})
        with urllib.request.urlopen(req, timeout=90) as response:
            uploaded = json.loads(response.read())
        if uploaded.get('type') != 'input' or uploaded.get('subfolder') != folder or uploaded.get('name') != name:
            raise ValueError('模型服务参考素材返回不一致')
        return {'remote_file': relative}
    if action == 'health':
        return {'connected': True, 'queue': request(port, '/queue'), 'system': request(port, '/system_stats')['system'].get('comfyui_version', '')}
    if action == 'submit':
        # Serializes this workbench's clients. Existing H3Storyboard leases are observed,
        # but that application does not participate in this advisory lock.
        with open('/tmp/article-studio-media-submit.lock', 'w') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            available = ensure_idle()
            for other in available:
                if other != port:
                    optional_service_request(other, '/free', {'unload_models': True, 'free_memory': True})
            return request(port, '/prompt', {'prompt': payload['workflow'], 'client_id': payload['client_id']})
    if action == 'poll':
        prompt_id = payload['prompt_id']
        history = request(port, '/history/' + urllib.parse.quote(prompt_id, safe=''))
        item = history.get(prompt_id)
        if item:
            return {'history': item}
        queue = request(port, '/queue')
        pending = queue.get('queue_pending', [])
        return {'running': any(x[1] == prompt_id for x in queue.get('queue_running', [])),
                'queued': any(x[1] == prompt_id for x in pending),
                'queue_position': next((i + 1 for i, x in enumerate(pending) if x[1] == prompt_id), None)}
    if action == 'fetch':
        asset = payload['asset']
        if asset.get('type') != 'output':
            raise ValueError('仅能读取模型输出文件')
        url = f'http://127.0.0.1:{port}/view?' + urllib.parse.urlencode({k: asset.get(k, '') for k in ('filename', 'subfolder', 'type')})
        with urllib.request.urlopen(url, timeout=60) as response:
            total = 0
            while chunk := response.read(1024 * 1024):
                total += len(chunk)
                if total > 1024**3:
                    raise ValueError('模型输出超过 1 GB')
                sys.stdout.buffer.write(chunk)
        return None
    raise ValueError('不支持的媒体操作')

if __name__ == '__main__':
    try:
        payload = json.loads(base64.b64decode(sys.argv[1]))
        if payload.get('action') == 'observe':
            asyncio.run(observe(payload))
            sys.exit(0)
        result = main(payload)
        if result is not None:
            print(json.dumps(result, ensure_ascii=False))
    except Exception as exc:
        print(str(exc)[:2000], file=sys.stderr)
        sys.exit(1)
