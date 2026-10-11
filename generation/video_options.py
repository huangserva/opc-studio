"""Bounded H3 inputs; legacy Director and native multimodal workflows."""
import json
import math
from math import gcd

from .native_h3 import build_native_h3

REFERENCE_MODES = ['r2v', 'ir2v', 'flr2v']
ALL_MODES = ['t2v', 'i2v', 'fl2v', *REFERENCE_MODES]
H3_CHECKPOINTS = [
    {'id': 'singularity', 'name': 'H3 Singularity ref2va · INT8', 'file': 'Minimax-h3_Singularity_ref2va_Pruned_v1.3_int8.safetensors', 'modes': ['t2v', 'i2v', *REFERENCE_MODES]},
    {'id': 'ref2va', 'name': 'H3 ref2va · INT8', 'file': 'minimax_h3_ref2va_pruned_int8_convrot.safetensors', 'modes': ['t2v', 'i2v', *REFERENCE_MODES]},
    {'id': 'fl2va', 'name': 'H3 fl2va · INT8', 'file': 'minimax_h3_fl2va_pruned_int8_convrot.safetensors', 'modes': ['t2v', 'i2v', 'fl2v']},
    {'id': 'hybrid', 'name': 'H3 Hybrid fl2va / ref2va · INT8', 'file': 'minimax_h3_hybrid_fl2va_ref2va_b25-49-int8.safetensors', 'modes': ALL_MODES.copy()},
]
H3_WORKFLOWS = [
    {'id': 't2v', 'name': '文生视频', 'task': 't2v — 文生视频(Text to Video)'},
    {'id': 'i2v', 'name': '图生视频 · 首帧', 'task': 'i2v — 图生视频(Image to Video)'},
    {'id': 'fl2v', 'name': '首尾帧生视频', 'task': 'fl2v — 首尾帧生视频(First-Last Frame)'},
    {'id': 'r2v', 'name': '多参考素材生视频', 'task': 'r2v — 多参考素材生视频(Reference to Video)'},
    {'id': 'ir2v', 'name': '首帧 + 多参考素材', 'task': 'ir2v — 首帧 + 多参考素材'},
    {'id': 'flr2v', 'name': '首尾帧 + 多参考素材', 'task': 'flr2v — 首尾帧 + 多参考素材'},
]
for _workflow in H3_WORKFLOWS:
    if _workflow['id'] in REFERENCE_MODES:
        _workflow['validation'] = {
            'status': 'live_gpu_passed', 'tested_at': '2026-10-05',
            'checkpoint': 'singularity', 'accelerator': 'ref8', 'size': '768x448',
            'frames': 56 if _workflow['id'] == 'r2v' else 124,
            'note': '已验证配置：Singularity、ref2v Turbo 8 步、768×448。其他配置未逐项实测，参考效果以生成结果为准。',
        }
H3_ACCELERATORS = [
    {'id': 'ref8', 'name': 'ref2v Turbo · 8 步', 'file': 'minimax_h3_ref2v_turbo_8step_v1.0_768p_comfyui_bf16.safetensors', 'steps': 8, 'modes': ['t2v', 'i2v', *REFERENCE_MODES]},
    {'id': 'fl8', 'name': 'fl2v Turbo · 8 步', 'file': 'minimax_h3_fl2v_turbo_8step_v1.0_768p_comfyui_bf16.safetensors', 'steps': 8, 'modes': ['t2v', 'i2v', 'fl2v']},
    {'id': 'ref4', 'name': 'ref2v Turbo · 4 步', 'file': 'minimax_h3_ref2v_turbo_4step_v0.1_comfyui_bf16.safetensors', 'steps': 4, 'modes': ['t2v', 'i2v', *REFERENCE_MODES]},
    {'id': 'fl4', 'name': 'fl2v LightX2V · 4 步', 'file': 'minimax_h3_fl2v_lightx2v_turbo_4step_v0.1_comfy.safetensors', 'steps': 4, 'modes': ['t2v', 'i2v', 'fl2v']},
    {'id': 'none', 'name': '不使用加速 LoRA · 25 步', 'file': None, 'steps': 25, 'modes': ALL_MODES.copy()},
]
H3_LORAS = [{'id': key, 'name': name, 'file': filename} for key, name, filename in [
    ('motion', 'Motion Booster', 'H3_Motion_Booster.safetensors'),
    ('realism', '写实人物', 'h3-realism-people-t2v-i2v-r2v.safetensors'),
    ('hm', 'HMNSFW AIO V2', 'HMNSFW_AIO_V2.safetensors'),
    ('naughty', 'NaughtyTimes', 'SexGod-NaughtyTimes-lora-MINIMAXH3.safetensors'),
]]
REFERENCE_LIMITS = {'images': 9, 'videos': 3, 'audios': 3, 'total': 12}

# ---------------- H3 size / duration catalog (mirrored in media-api workflows.py) ----
# Read from the 8191 node contract (/object_info, 2026-10-06): width/height step 32,
# length step 17 with a trained range around 124-362 frames. The node schema allows up to
# 8192/16384 px and 100000 frames, but that is not offered: this single 49140 MiB 4090 also
# hosts qi21/krea2/LTX behind the API's 30 GiB free-VRAM guard. Only H3_SIZES_MEASURED x
# H3_FRAMES_MEASURED are ffprobe-verified end to end; other allowed combinations are
# explicitly labelled as not individually measured.
H3_FPS = 24
H3_SIZE_STEP = 32
H3_CUSTOM_SIZE = {'min': 256, 'max': 1024, 'step': H3_SIZE_STEP, 'max_area': 1024 * 576}
H3_SIZE_GROUPS = {
    '已实测（产物 ffprobe 核对）': ['768x448', '448x768'],
    '16:9 / 9:16': ['768x432', '1024x576', '432x768', '576x1024'],
    '4:3 / 3:4': ['768x576', '576x768'],
    '3:2 / 2:3': ['768x512', '512x768'],
    '1:1': ['512x512', '768x768'],
}
H3_SIZES = [size for group in H3_SIZE_GROUPS.values() for size in group]


def exact_ratio(size):
    """True reduced ratio; an approximate ratio is never labelled as an exact one
    (768x448 is 12:7, not 16:9)."""
    width, height = map(int, size.split('x'))
    divisor = gcd(width, height)
    return f'{width // divisor}:{height // divisor}'


H3_SIZE_LABELS = {size: f'{exact_ratio(size)} · {size}' for size in H3_SIZES}
H3_SIZES_MEASURED = ['768x448', '448x768']
H3_FRAMES_MIN, H3_FRAMES_MAX, H3_FRAMES_STEP = 39, 345, 17
H3_FRAMES = list(range(H3_FRAMES_MIN, H3_FRAMES_MAX + 1, H3_FRAMES_STEP))
H3_FRAMES_MEASURED = [56, 124]
H3_SIZE_HINT = ('H3 自定义宽高须为 256–1024 像素、32 的倍数，且面积不超过 589824 像素'
                '（即 1024×576）')
H3_CONSTRAINT_NOTE = ('尺寸按 32 像素对齐，可选 256–1024 像素且面积≤1024×576；时长 39–345 帧'
                      '（24 FPS，按 17 帧递增）。实测按组合逐项核对：只有列在“已实测组合”里的搭配'
                      '经过产物核对，其余组合允许提交但未经逐项实测；尺寸或时长上的“已实测”标记'
                      '只代表该单项曾出现在实测组合中，不代表任意搭配都已验证。'
                      '768×432 / 432×768 不是 32 的倍数，只用于文生视频；参考素材与首尾帧工作流只列 32 对齐的尺寸。')


# Which graph each mode actually builds, and which combinations were really produced.
H3_ENGINES = {'t2v': 'director', 'i2v': 'director', 'fl2v': 'director',
              'r2v': 'native', 'ir2v': 'native', 'flr2v': 'native'}
H3_MEASURED_COMBOS = [
    {'workflow': 'r2v', 'engine': 'native', 'checkpoint': 'singularity', 'accelerator': 'ref8',
     'size': '768x448', 'frames': 56, 'seconds': 2.33,
     'evidence': 'API 4d27af2cc3834a7eb22e0995efb8aa63 / prompt b82b1a2e-3970-40d6-8fa0-610db4101006 '
                 '/ ffprobe 768x448 h264 24/1 nb_frames 56'},
    {'workflow': 'i2v', 'engine': 'director', 'checkpoint': 'singularity', 'accelerator': 'ref8',
     'size': '768x448', 'frames': 124, 'seconds': 5.17,
     'evidence': 'API 7cc44f7b7f794a678e3b09c0076ad139 / prompt fc160933-e61d-4f25-847a-47c77c2e0e03 '
                 '/ ffprobe 768x448 h264 24/1 nb_frames 124 duration 5.166667'},
    {'workflow': 'i2v', 'engine': 'director', 'checkpoint': 'singularity', 'accelerator': 'ref8',
     'size': '448x768', 'frames': 124, 'seconds': 5.17,
     'evidence': 'API 052f6a7cb4dd4e3f9bbbffacd2d21e42 / 8a349a9f79bb4f1fb161f6ef3cf9fea3 '
                 '/ ffprobe 448x768 h264 24/1 nb_frames 124'},
]


def h3_capability_matrix():
    """One published matrix for both entry points: what each mode really runs, which weights
    and accelerators it accepts, and which combinations were actually produced."""
    rows = []
    for workflow in H3_WORKFLOWS:
        mode = workflow['id']
        accelerators = [a for a in H3_ACCELERATORS if mode in a['modes']]
        rows.append({'workflow': mode, 'name': workflow['name'], 'engine': H3_ENGINES[mode],
                     'checkpoints': [c['id'] for c in H3_CHECKPOINTS if mode in c['modes']],
                     'accelerators': [a['id'] for a in accelerators],
                     'steps': {a['id']: a['steps'] for a in accelerators},
                     'sizes_require_32_alignment': mode != 't2v',
                     'measured_combinations': [row for row in H3_MEASURED_COMBOS if row['workflow'] == mode]})
    return rows


def validate_frames(model, value):
    """Strict H3 duration; the same rule the media API enforces."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError('视频帧数须为整数（不接受小数、字符串或布尔值）')
    if value in (56, 124):
        return value
    if not H3_FRAMES_MIN <= value <= H3_FRAMES_MAX or value % H3_FRAMES_STEP != H3_FRAMES_MIN % H3_FRAMES_STEP:
        raise ValueError(f'视频帧数须为 {H3_FRAMES_MIN}–{H3_FRAMES_MAX} 且满足 17k+5'
                         f'（例如 39/56/124/175/345）')
    return value



def option(items, key, label):
    for item in items:
        if item['id'] == key:
            return item
    raise ValueError('无效' + label)


def _identity(value):
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError('参考素材编号须为字符串')
    identity = value.strip()
    if any(char in identity for char in ('/', '\\', '\x00')) or any(char.isspace() for char in identity):
        raise ValueError('无效参考素材编号')
    return identity or None


def normalize_references(references, workflow=None):
    """Normalize public IDs before file resolution; never discard occupied roles."""
    if not isinstance(references, dict) or not set(references) <= {'first', 'last', 'reference', 'images', 'videos', 'audios'}:
        raise ValueError('无效参考素材参数')
    result = {key: [] for key in ('images', 'videos', 'audios')}
    for role in ('first', 'last'):
        identity = _identity(references.get(role))
        if identity:
            result[role] = identity
    legacy = _identity(references.get('reference'))
    if legacy:
        result['images'].append(legacy)
    for key in ('images', 'videos', 'audios'):
        values = references.get(key, [])
        if not isinstance(values, list):
            raise ValueError('参考图片、视频和音频须使用编号列表')
        for value in values:
            identity = _identity(value)
            if identity:
                result[key].append(identity)
    anchors = [result[key] for key in ('first', 'last') if key in result]
    all_ids = anchors + result['images'] + result['videos'] + result['audios']
    if len(all_ids) != len(set(all_ids)):
        raise ValueError('同一参考素材不可重复使用，包括首尾帧')
    if len(anchors) + len(result['images']) > REFERENCE_LIMITS['images']:
        raise ValueError('参考图片最多 9 张（含首尾帧）')
    if len(result['videos']) > REFERENCE_LIMITS['videos'] or len(result['audios']) > REFERENCE_LIMITS['audios']:
        raise ValueError('参考视频和独立音频各最多 3 个')
    if len(all_ids) > REFERENCE_LIMITS['total']:
        raise ValueError('参考素材合计最多 12 个（含首尾帧）')
    if workflow is not None:
        option(H3_WORKFLOWS, workflow, '视频工作流')
        required = {'t2v': set(), 'i2v': {'first'}, 'fl2v': {'first', 'last'},
                    'r2v': set(), 'ir2v': {'first'}, 'flr2v': {'first', 'last'}}[workflow]
        present = {key for key in ('first', 'last') if key in result}
        if present != required:
            raise ValueError('首尾帧与当前视频工作流不匹配')
        has_material = any(result[key] for key in ('images', 'videos', 'audios'))
        if workflow in REFERENCE_MODES and not has_material:
            raise ValueError('请上传至少一个参考图片、视频或音频')
        if workflow not in REFERENCE_MODES and has_material:
            raise ValueError('当前工作流不接受多参考素材，请选择多参考素材模式')
    return result


def _director_references(raw, normalized):
    if not isinstance(raw, dict) or not set(raw) <= {'first', 'last', 'images', 'videos', 'audios'}:
        raise ValueError('无效首尾帧内容')
    for key in ('images', 'videos', 'audios'):
        if key in raw and (not isinstance(raw[key], list) or raw[key]):
            raise ValueError('当前工作流不接受多参考素材')
    required = {key for key in ('first', 'last') if key in normalized}
    if {key for key in ('first', 'last') if key in raw} != required:
        raise ValueError('请上传当前工作流需要的首尾帧')
    result = {}
    for key in required:
        descriptor = raw[key]
        if not isinstance(descriptor, dict) or not isinstance(descriptor.get('imageB64'), str) or not descriptor['imageB64']:
            raise ValueError('请上传有效首尾帧图片')
        if descriptor.get('kind', 'image') != 'image' or descriptor.get('id', normalized[key]) != normalized[key]:
            raise ValueError('首尾帧素材类型或编号不匹配')
        result[key] = {field: descriptor[field] for field in ('imageB64', 'width', 'height') if field in descriptor}
    return result


def sampling_options(payload):
    mode = payload.get('video_sampling', 'single')
    if mode not in ('single', 'dual'):
        raise ValueError('采样方式须为单采或双采')
    result = {'video_sampling': mode}
    for key, default, lower, upper, integer in (
        ('video_refine_steps', 4, 1, 25, True),
        ('video_refine_denoise', 0.35, 0.05, 0.75, False),
        ('video_upscale', 2.0, 1.0, 2.0, False),
    ):
        value = payload.get(key, default)
        if (isinstance(value, bool) or not isinstance(value, (int, float))
                or not lower <= value <= upper or not math.isfinite(value)
                or (integer and not isinstance(value, int))):
            raise ValueError(f'{key} 须为 {lower}–{upper} 的' + ('整数' if integer else '有限数字'))
        if mode == 'dual':
            result[key] = value
    return result


def apply_video(graph, payload, settings, prompt, frames):
    sampling = sampling_options(payload)
    mode = payload.get('workflow', 't2v')
    wf = option(H3_WORKFLOWS, mode, '视频工作流')
    if sampling['video_sampling'] == 'dual' and any(int(n) % 32 for n in settings['size'].split('x')):
        raise ValueError('双采输出宽高须为 32 的倍数，请选择对齐尺寸')
    if mode != 't2v' and any(int(n) % 32 for n in settings['size'].split('x')):
        raise ValueError('参考素材与首尾帧工作流的宽高须为 32 的倍数（自定义宽高已按 32 对齐，或选 768×448 / 448×768）')
    if isinstance(payload.get('frames', frames), bool) or not isinstance(payload.get('frames', frames), int):
        raise ValueError('视频帧数须为整数')
    if any(key in payload for key in ('steps', 'cfg', 'negative_prompt', 'ref_image_size')):
        raise ValueError('H3 采样参数由加速方案确定，不接受独立步数、CFG、负面提示词或参考图尺寸参数')
    cp = option(H3_CHECKPOINTS, payload.get('video_checkpoint', 'fl2va' if mode == 'fl2v' else 'singularity'), 'H3 权重')
    accel = option(H3_ACCELERATORS, payload.get('video_accelerator', 'fl8' if mode == 'fl2v' else 'ref8'), '加速 LoRA')
    if mode not in cp['modes'] or mode not in accel['modes']:
        raise ValueError('当前权重或加速 LoRA 与工作流不兼容')
    ids = normalize_references(payload.get('references', {}), mode)
    graph['1']['inputs']['unet_name'] = cp['file']
    link = ['1', 0]
    graph.pop('10', None)
    if accel['file']:
        graph['10'] = {'class_type': 'LoraLoaderModelOnly', 'inputs': {'model': link, 'lora_name': accel['file'], 'strength_model': 1.0}}
        link = ['10', 0]
    selected = payload.get('video_loras', [])
    if not isinstance(selected, list) or len(selected) > 3:
        raise ValueError('附加 LoRA 最多 3 个')
    seen, saved = set(), []
    for index, row in enumerate(selected):
        if not isinstance(row, dict) or not set(row) <= {'id', 'strength'}:
            raise ValueError('无效视频 LoRA 参数')
        lo = option(H3_LORAS, row.get('id'), '视频 LoRA')
        strength = row.get('strength', 1.0)
        if lo['id'] in seen or isinstance(strength, bool) or not isinstance(strength, (int, float)) or not 0 <= strength <= 2 or not math.isfinite(strength):
            raise ValueError('LoRA 不可重复，强度须为 0–2')
        seen.add(lo['id'])
        key = 'video_lora_' + str(index)
        graph[key] = {'class_type': 'LoraLoaderModelOnly', 'inputs': {'model': link, 'lora_name': lo['file'], 'strength_model': strength}}
        link = [key, 0]
        saved.append({**lo, 'strength': strength})
    settings.update(workflow=mode, workflow_name=wf['name'], video_checkpoint=cp['id'], checkpoint_file=cp['file'],
                    video_accelerator=accel['id'], accelerator_file=accel['file'], video_loras=saved, references=ids, steps=accel['steps'])
    settings.update(sampling)
    if mode in REFERENCE_MODES or sampling['video_sampling'] == 'dual':
        build_native_h3(graph, payload.get('_references', {}), ids, settings, prompt, frames, link)
        return
    refs = _director_references(payload.get('_references', {}), ids)
    timeline = {'timelineMode': 'gen_image' if mode == 'i2v' else 'gen_blank',
                'global': {'taskType': wf['task'], 'prompt': prompt},
                'output': {'mode': 'fixed', 'width': int(settings['size'].split('x')[0]), 'height': int(settings['size'].split('x')[1])},
                'totalFrames': frames}
    if mode == 'i2v':
        timeline['global']['genImage'] = refs['first']
        timeline['segments'] = [{'frameCount': frames, 'taskType': wf['task'], 'prompt': prompt, 'genImage': refs['first']}]
    if mode == 'fl2v':
        timeline.update(timelineMode='fl2v', shots=[{'startImage': refs['first'], 'endImage': refs['last'], 'durationSec': frames / 24, 'prompt': prompt}])
    graph['5']['inputs'].update(model=link, task_type=wf['task'], steps=accel['steps'], timeline_data=json.dumps(timeline if mode != 't2v' else {}, ensure_ascii=False))
    settings.update(engine='director', reference_labels=[], reference_video_usage=[])
