"""Verified deployments and bounded workflows; chat models are configured separately."""
import json
import secrets
import re
import math
from pathlib import Path
from .video_options import (H3_CHECKPOINTS,H3_WORKFLOWS,H3_ACCELERATORS,H3_LORAS,apply_video,
                            H3_SIZES,H3_SIZE_LABELS,H3_SIZE_GROUPS,H3_CUSTOM_SIZE,H3_SIZE_HINT,
                            H3_SIZES_MEASURED,H3_FRAMES,H3_FRAMES_MIN,H3_FRAMES_MAX,H3_FRAMES_STEP,
                            H3_FRAMES_MEASURED,H3_FPS,H3_CONSTRAINT_NOTE,H3_ENGINES,H3_MEASURED_COMBOS,
                            exact_ratio,h3_capability_matrix,validate_frames)
from .image_references import apply_image_references
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
             'reference_images':True,'reference_limit':4,'reference_mode':'native_edit',
             'steps': 20, 'cfg':2.5},
    'krea2': {'id': 'krea2', 'name': 'Krea2 Turbo · 4090', 'kind': 'image',
              'checkpoint': 'krea2_turbo_fp8.safetensors',
              'sizes': [size for group in KREA_PRESETS.values() for size in group],
              'size_labels': {size:f'{ratio} · {size}' for group in KREA_PRESETS.values() for size,ratio in group.items()},
              'size_groups': [{'name':name,'sizes':list(group)} for name,group in KREA_PRESETS.items()],
              'custom_size': {'min':512,'max':2048,'step':16},
              'size_hint': 'Krea2 自定义宽高须为 512–2048 像素，且为 16 的倍数',
              'reference_images':True,'reference_limit':1,'reference_mode':'img2img',
              'steps': 8, 'cfg':1.0,
              'loras': KREA_LORAS},
    'h3': {'id': 'h3', 'name': 'MiniMax H3 · 4090', 'kind': 'video',
           'checkpoint': 'Minimax-h3_Singularity_ref2va_Pruned_v1.3_int8.safetensors',
           'sizes': H3_SIZES,
           'size_labels': H3_SIZE_LABELS,
           'size_groups': [{'name':name,'sizes':list(group)} for name,group in H3_SIZE_GROUPS.items()],
           'custom_size': H3_CUSTOM_SIZE, 'size_hint': H3_SIZE_HINT,
           'sizes_measured': H3_SIZES_MEASURED,
           'capability_matrix': h3_capability_matrix(),
           'frames': H3_FRAMES, 'frames_min': H3_FRAMES_MIN, 'frames_max': H3_FRAMES_MAX,
           'frames_step': H3_FRAMES_STEP, 'frames_measured': H3_FRAMES_MEASURED, 'fps': H3_FPS,
           'constraint_note': H3_CONSTRAINT_NOTE,
           'video_sampling_modes': [{'id': 'single', 'name': '单采'}, {'id': 'dual', 'name': '双采'}],
           'video_sampling_defaults': {'video_refine_steps': 4, 'video_refine_denoise': 0.35, 'video_upscale': 2.0},
           'steps': 8, 'workflows':H3_WORKFLOWS,'checkpoints':H3_CHECKPOINTS,'accelerators':H3_ACCELERATORS,'video_loras':H3_LORAS},
}

def validate_size(model, size):
    if isinstance(size,str) and size in model['sizes']:
        return size
    limits=model.get('custom_size')
    if limits and isinstance(size,str) and re.fullmatch(r'[0-9]{3,4}x[0-9]{3,4}',size):
        width,height=map(int,size.split('x'))
        step=limits['step']
        axes_ok=all(limits['min']<=n<=limits['max'] and n%step==0 for n in (width,height))
        area_ok=not limits.get('max_area') or width*height<=limits['max_area']
        if axes_ok and area_ok:
            return f'{width}x{height}'
    if limits:
        raise ValueError(model.get('size_hint') or
                         f"自定义宽高须为 {limits['min']}–{limits['max']} 像素，且为 {limits['step']} 的倍数")
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
        frames = validate_frames(model, payload.get('frames', 56))
        graph['5']['inputs'].update(task_type='t2v — 文生视频(Text to Video)', global_prompt=prompt,
                                  width=width, height=height, total_frames=frames, seed=seed, timeline_data='{}')
        graph['7']['inputs']['filename_prefix'] = prefix
        settings.update(frames=frames, fps=H3_FPS, seconds=round(frames / H3_FPS, 2))
        apply_video(graph,payload,settings,prompt,frames)
    if kind == 'image':
        apply_image_references(graph, model['id'], payload, settings)
    return model, prompt, settings, graph
