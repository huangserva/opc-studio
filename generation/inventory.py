"""Compare fixed workbench recipes with live loader enums without loading weights."""
import copy
import json
from pathlib import Path

from .models import MODELS


def model_selection(model):
    if model is not None and (not isinstance(model, str) or model not in MODELS):
        raise ValueError('请选择已接入的模型服务')
    return [model] if model is not None else list(MODELS)


def file_availability(inventory, category, filename):
    if filename is None:
        return True
    entry = inventory.get(category, {})
    if entry.get('status') not in ('ok', 'missing'):
        return None
    return filename.replace('\\', '/') in entry.get('files', [])


def node_availability(nodes, required):
    values = [nodes.get(name, {}).get('available') for name in required]
    if any(value is False for value in values):
        return False
    return True if all(value is True for value in values) else None


def configured_inventory(model, live):
    config = MODELS[model]
    inventory, nodes = live.get('inventory', {}), live.get('nodes', {})
    template = 'video' if model == 'h3' else ('krea2' if model == 'krea2' else 'image')
    graph = json.loads((Path(__file__).parent / 'workflows' / (template + '.json')).read_text())

    def mapped(rows, category):
        return [{**copy.deepcopy(row), 'available': file_availability(inventory, category, row.get('file')),
                 'availability_source': 'live ComfyUI loader enum' if row.get('file') else 'no weight file required'} for row in rows]

    checkpoints = config.get('checkpoints', [{'id': model, 'name': config['name'], 'file': config['checkpoint']}])
    encoders = [{'file': item['inputs']['clip_name'], 'type': item['inputs'].get('type')}
                for item in graph.values() if item['class_type'] == 'CLIPLoader']
    vaes = [{'file': item['inputs']['vae_name']} for item in graph.values() if item['class_type'] == 'VAELoader']
    result = {'id': model, 'name': config['name'], 'kind': config['kind'],
              'source': 'workbench static configuration; availability compared with live enums',
              'checkpoints': mapped(checkpoints, 'diffusion_models'),
              'text_encoders': mapped(encoders, 'text_encoders'), 'vaes': mapped(vaes, 'vaes'),
              'loras': mapped(config.get('video_loras', config.get('loras', [])), 'loras'),
              'accelerators': mapped(config.get('accelerators', []), 'loras')}
    types = inventory.get('text_encoders', {}).get('types')
    for encoder in result['text_encoders']:
        encoder['type_available'] = None if types is None else encoder['type'] in types
    if model == 'h3':
        common = {'UNETLoader', 'CLIPLoader', 'VAELoader', 'CreateVideo', 'SaveVideo'}
        native = common | {'MiniMaxH3ReferenceToVideo', 'MiniMaxH3SigmaShift', 'BasicScheduler',
                           'BasicGuider', 'RandomNoise', 'KSamplerSelect', 'SamplerCustomAdvanced',
                           'LTXVSeparateAVLatent', 'VAEDecode', 'VAEDecodeAudio'}
        workflows = []
        for row in config['workflows']:
            mode = row['id']
            required = native if mode in ('r2v', 'ir2v', 'flr2v') else common | {'MiniMaxH3Director'}
            if mode in ('ir2v', 'flr2v'):
                required = required | {'MiniMaxH3AddGuide', 'LoadImage'}
            workflows.append({**copy.deepcopy(row), 'required_nodes': sorted(required),
                              'required_nodes_present': node_availability(nodes, required),
                              'conditional_input_nodes': {'accelerator_or_lora': {
                                  'nodes': ['LoraLoaderModelOnly'], 'present': node_availability(nodes, ['LoraLoaderModelOnly'])}}})
            if mode in ('r2v', 'ir2v', 'flr2v'):
                workflows[-1]['conditional_input_nodes'].update({
                    role: {'nodes': names, 'present': node_availability(nodes, names)}
                    for role, names in {'images': ['LoadImage'], 'videos': ['LoadVideo', 'GetVideoComponents'],
                                        'audios': ['LoadAudio']}.items()})
        result['workflows'] = workflows
    else:
        required = {item['class_type'] for item in graph.values()}
        result['workflows'] = [{'id': 'text_to_image', 'required_nodes': sorted(required),
                                'required_nodes_present': node_availability(nodes, required)}]
        if config.get('loras'):
            result['workflows'][0]['conditional_input_nodes'] = {
                'lora': {'nodes': ['LoraLoaderModelOnly'], 'present': node_availability(nodes, ['LoraLoaderModelOnly'])}}
    result['workflow_check_scope'] = '仅核对所需节点是否注册；不加载权重，不代表可成功推理'
    return result


def enrich_inventory(result):
    result = copy.deepcopy(result)
    for model, live in result['services'].items():
        if model in MODELS:
            live['configured'] = configured_inventory(model, live)
    return result
