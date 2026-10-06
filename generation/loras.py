"""Reviewed text-to-image adapters. Editing adapters use different workflows."""
import math

KREA_LORAS = [
    {'id': 'xhs', 'name': '小红书', 'file': 'krea2_lora_xhs.safetensors'},
    {'id': 'social', 'name': '社交媒体大片', 'file': 'krea2_lora_社交媒体大片.safetensors'},
    {'id': 'daofushou', 'name': '通行证刀斧手版', 'file': 'krea2_lora 通行证刀斧手版.safetensors'},
    {'id': 'wumao', 'name': '五毛', 'file': 'krea2_lora 五毛.safetensors'},
    {'id': 'buding', 'name': '布丁', 'file': 'krea2_lora_ 布丁.safetensors'},
    {'id': 'ccd', 'name': 'CCD', 'file': 'krea2_lora_ccd.safetensors'},
    {'id': 'ahei', 'name': '阿黑（元数据标注汉服）', 'file': 'krea2_lora_阿黑.safetensors'},
]

def lora_parameters(payload):
    identity = payload.get('lora') or ''
    if not isinstance(identity, str):
        raise ValueError('无效 LoRA 选择')
    if not identity:
        return {'lora': '', 'lora_strength': 0.8, 'lora_trigger': ''}
    if payload.get('model') != 'krea2':
        raise ValueError('当前 LoRA 仅适用于 Krea2')
    if identity not in {item['id'] for item in KREA_LORAS}:
        raise ValueError('请选择已接入的 Krea2 LoRA')
    raw = payload.get('lora_strength', 0.8)
    try:
        if isinstance(raw, bool):
            raise ValueError()
        strength = float(raw)
    except (ValueError, TypeError):
        raise ValueError('LoRA 强度须为 0–2 的数字') from None
    if not math.isfinite(strength) or not 0 <= strength <= 2:
        raise ValueError('LoRA 强度须为 0–2 的数字')
    trigger = payload.get('lora_trigger') or ''
    if not isinstance(trigger, str) or len(trigger) > 200:
        raise ValueError('LoRA 触发词最长 200 字')
    return {'lora': identity, 'lora_strength': strength, 'lora_trigger': trigger.strip()}

def apply_lora(graph, prompt, payload, settings):
    parameters = lora_parameters(payload)
    if not parameters['lora']:
        return prompt
    item = next(item for item in KREA_LORAS if item['id'] == parameters['lora'])
    graph['selected_lora'] = {'class_type': 'LoraLoaderModelOnly', 'inputs': {
        'model': ['unet', 0], 'lora_name': item['file'], 'strength_model': parameters['lora_strength']}}
    graph['sampler']['inputs']['model'] = ['selected_lora', 0]
    trigger = parameters['lora_trigger']
    if trigger and trigger not in prompt:
        prompt = trigger + '\n\n' + prompt
    if len(prompt) > 8000:
        raise ValueError('加入 LoRA 触发词后描述超过 8000 字')
    settings.update(parameters, lora_file=item['file'], lora_name=item['name'])
    return prompt
