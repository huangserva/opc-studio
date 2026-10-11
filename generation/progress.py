"""Translate actual ComfyUI events into user-facing stages, never elapsed-time percentages."""
import math


def stage(node_type, kind):
    if node_type in ('UNETLoader', 'CLIPLoader', 'VAELoader', 'LoraLoaderModelOnly'):
        return 'loading', '正在加载模型'
    if node_type in ('TextEncodeQwenImage21', 'CLIPTextEncode'):
        return 'encoding', '正在理解画面描述'
    if node_type in ('MiniMaxH3ReferenceToVideo', 'MiniMaxH3AddGuide'):
        return 'encoding', '正在处理参考素材'
    if node_type in ('KSampler', 'SamplerCustomAdvanced'):
        return 'sampling', '正在生成视频' if kind == 'video' else '正在生成画面'
    if node_type == 'MiniMaxH3Director':
        return 'video', '正在生成视频'
    if node_type == 'VAEDecode':
        return 'decoding', '正在解码视频' if kind == 'video' else '正在解码图片'
    if node_type == 'VAEDecodeAudio':
        return 'decoding', '正在解码声音'
    if node_type in ('SaveImage', 'CreateVideo', 'SaveVideo'):
        return 'saving', '正在导出结果'
    return 'preparing', '正在准备画面' if kind == 'image' else '正在准备视频'


def event_progress(event, prompt_id, graph, kind, previous=None):
    data = event.get('data', {})
    if data.get('prompt_id') and data['prompt_id'] != prompt_id:
        return None
    event_type = event.get('type')
    if event_type == 'progress_state':
        nodes = [(key, value) for key, value in data.get('nodes', {}).items() if value.get('state') == 'running']
        if not nodes:
            return None
        node, values = nodes[-1]
        data = {**values, 'node': node}
    elif event_type not in ('executing', 'progress'):
        return None
    node = data.get('node')
    if node is None:
        return None
    node = str(node)
    node_type = graph.get(node, {}).get('class_type', '')
    phase, label = stage(node_type, kind)
    progress = {'phase': phase, 'label': label, 'node': node}
    # Only a sampler's reported value/max is a sampling percentage.
    value, total = data.get('value'), data.get('max')
    if phase == 'sampling' and isinstance(value, (int, float)) and isinstance(total, (int, float)):
        if math.isfinite(value) and math.isfinite(total) and total > 0 and 0 <= value <= total:
            progress.update(current=value, total=total, percent=round(100 * value / total))
    elif previous and previous.get('node') == node:
        progress.update({k: previous[k] for k in ('current', 'total', 'percent') if k in previous})
    if 'refine_sampler' in graph:
        first = graph['scheduler']['inputs']['steps']
        second = graph['refine_scheduler']['inputs']['steps']
        combined = first + second
        if node in ('native_sampler', 'refine_sampler'):
            fraction = value / total if event_type == 'progress' and 'percent' in progress else 0
            completed = fraction * (first if node == 'native_sampler' else second)
            if node == 'refine_sampler':
                completed += first
            completed = max(completed, (previous or {}).get('current', 0))
            percent = round(100 * completed / combined)
            percent = max(percent, (previous or {}).get('percent', 0))
            progress.update(current=completed, total=combined, percent=percent,
                            label='第一遍采样' if node == 'native_sampler' else '第二遍精修')
        elif previous and previous.get('phase') == 'sampling' and phase not in ('decoding', 'saving'):
            progress.update(phase='sampling', label='正在准备第二遍精修',
                            **{k: previous[k] for k in ('current', 'total', 'percent') if k in previous})
    return progress
