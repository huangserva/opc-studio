"""Verified deployments and bounded workflows; chat models are configured separately."""
import json
import secrets
import re
import math
from pathlib import Path
from .video_options import H3_CHECKPOINTS,H3_WORKFLOWS,H3_ACCELERATORS,H3_LORAS,apply_video
from .loras import KREA_LORAS, apply_lora, lora_parameters

QWEN_2K_PRESETS = {
    '2048x2048': '1:1',
    '2400x1792': '4:3',
    '1792x2400': '3:4',
    '2528x1696': '3:2',
    '1696x2528': '2:3',
    '2752x1536': '16:9',
    '1536x2752': '9:16',
}

KREA_PRESETS = {
    '1K 档': {'1024x1024':'1:1','1024x768':'4:3','768x1024':'3:4',
              '1152x768':'3:2','768x1152':'2:3','1024x576':'16:9','576x1024':'9:16',
              '1024x1280':'4:5','1280x1024':'5:4'},
    '2K 档': {'2048x2048':'1:1','2048x1536':'4:3','1536x2048':'3:4',
              '2016x1344':'3:2','1344x2016':'2:3','2048x1152':'16:9','1152x2048':'9:16',
              '1536x1920':'4:5','1920x1536':'5:4'},
    '原有尺寸': {'1344x768':'7:4','768x1344':'4:7','768x512':'3:2'},
}

MODELS = {
    'qi21': {'id': 'qi21', 'name': 'Qwen Image 2.1 · 4090', 'kind': 'image',
             'checkpoint': 'qwen_image_2.1_bf16.safetensors',
             'sizes': ['1024x1024', '1344x768', '768x1344', '768x512', *QWEN_2K_PRESETS],
             'size_labels': {size: f'{ratio} · {size}（2K）' for size, ratio in QWEN_2K_PRESETS.items()},
             'steps': 20, 'cfg':2.5},
    'krea2': {'id': 'krea2', 'name': 'Krea2 Turbo · 4090', 'kind': 'image',
              'checkpoint': 'krea2_turbo_fp8.safetensors',
              'sizes': [size for group in KREA_PRESETS.values() for size in group],
              'size_labels': {size:f'{ratio} · {size}' for group in KREA_PRESETS.values() for size,ratio in group.items()},
              'size_groups': [{'name':name,'sizes':list(group)} for name,group in KREA_PRESETS.items()],
              'custom_size': {'min':512,'max':2048,'step':16}, 'steps': 8, 'cfg':1.0,
              'loras': KREA_LORAS},
    'h3': {'id': 'h3', 'name': 'MiniMax H3 · 4090', 'kind': 'video',
           'checkpoint': 'Minimax-h3_Singularity_ref2va_Pruned_v1.3_int8.safetensors', 'sizes': ['768x432', '432x768','768x448','448x768'], 'steps': 8, 'workflows':H3_WORKFLOWS,'checkpoints':H3_CHECKPOINTS,'accelerators':H3_ACCELERATORS,'video_loras':H3_LORAS},
}

def validate_size(model, size):
    if isinstance(size,str) and size in model['sizes']:
        return size
    limits=model.get('custom_size')
    if limits and isinstance(size,str) and re.fullmatch(r'[0-9]{3,4}x[0-9]{3,4}',size):
        width,height=map(int,size.split('x'))
        if all(limits['min']<=n<=limits['max'] and n%limits['step']==0 for n in (width,height)):
            return f'{width}x{height}'
    if limits:raise ValueError('Krea2 自定义宽高须为 512–2048 像素，且为 16 的倍数')
    raise ValueError('不支持该尺寸')

def build(payload, job_id):
    model = MODELS.get(payload.get('model'))
    if not model:
        raise ValueError('请选择已接入的生图或视频模型')
    lora_parameters(payload)
    prompt = str(payload.get('prompt', '')).strip()
    if not prompt or len(prompt) > 8000:
        raise ValueError('生成描述须为 1–8000 字')
    size = validate_size(model,payload.get('size', model['sizes'][0]))
    width, height = map(int, size.split('x'))
    seed = payload.get('seed')
    seed = secrets.randbelow(2**32) if seed in ('', None) else int(seed)
    if not 0 <= seed < 2**32:
        raise ValueError('种子范围为 0–4294967295')
    kind = model['kind']
    graph = json.loads((Path(__file__).parent / 'workflows' / (('krea2' if model['id'] == 'krea2' else kind) + '.json')).read_text())
    settings = {'size': size, 'seed': seed, 'steps': model['steps']}
    if kind=='image':
        if payload.get('references'):raise ValueError('当前图片模型尚未接入参考图编辑工作流')
        steps=payload.get('steps',model['steps']);cfg=payload.get('cfg',model['cfg'])
        if isinstance(steps,bool) or not isinstance(steps,int) or not 1<=steps<=60:
            raise ValueError('图片采样步数须为 1–60 的整数')
        if isinstance(cfg,bool) or not isinstance(cfg,(int,float)) or not math.isfinite(cfg) or not 0<=cfg<=20:
            raise ValueError('CFG 须为 0–20 的有限数字')
        settings.update(steps=steps,cfg=float(cfg))
    prefix = 'article_studio/' + job_id
    if model['id'] == 'krea2':
        prompt = apply_lora(graph, prompt, payload, settings)
        graph['positive']['inputs']['text'] = prompt
        graph['latent']['inputs'].update(width=width, height=height)
        graph['sampler']['inputs']['seed'] = seed
        graph['sampler']['inputs'].update(steps=settings['steps'],cfg=settings['cfg'])
        graph['save']['inputs']['filename_prefix'] = prefix
    elif kind == 'image':
        graph['4']['inputs']['prompt'] = prompt
        graph['6']['inputs'].update(width=width, height=height)
        graph['8']['inputs']['seed'] = seed
        graph['8']['inputs'].update(steps=settings['steps'],cfg=settings['cfg'])
        graph['10']['inputs']['filename_prefix'] = prefix
    else:
        frames = int(payload.get('frames', 56))
        if frames not in (56, 124):
            raise ValueError('请选择 2.3 秒或 5.2 秒视频')
        graph['5']['inputs'].update(task_type='t2v — 文生视频(Text to Video)', global_prompt=prompt,
                                  width=width, height=height, total_frames=frames, seed=seed, timeline_data='{}')
        graph['7']['inputs']['filename_prefix'] = prefix
        settings.update(frames=frames, fps=24, seconds=round(frames / 24, 2))
        apply_video(graph,payload,settings,prompt,frames)
    return model, prompt, settings, graph
