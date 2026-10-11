"""Image conditioning shared with the media API; accepts resolved files only."""
import math
import re


def normalize_image_references(refs, model):
    if not isinstance(refs, dict) or not set(refs) <= {'images', 'reference'}:
        raise ValueError('图片生成仅接受参考图片')
    images = refs.get('images', [])
    if not isinstance(images, list):
        raise ValueError('参考图片须使用编号列表')
    images = list(images)
    if refs.get('reference'):
        images.insert(0, refs['reference'])
    limit = 4 if model == 'qi21' else 1
    if len(images) > limit:
        raise ValueError(f'当前模型最多使用 {limit} 张参考图片')
    if any(not isinstance(value, str) or not re.fullmatch(r'[a-f0-9]{32}', value) for value in images):
        raise ValueError('无效参考图片编号')
    if len(set(images)) != len(images):
        raise ValueError('参考图片不可重复使用')
    return {'images': images} if images else {}


def apply_image_references(graph, model, payload, settings):
    refs = normalize_image_references(payload.get('references', {}), model)
    images = refs.get('images', [])
    denoise = payload.get('denoise', 0.7)
    if isinstance(denoise, bool) or not isinstance(denoise, (float, int)) or not math.isfinite(denoise) or not 0.05 <= denoise <= 1:
        raise ValueError('重绘强度须为 0.05–1 的有限数字')
    if not images:
        return
    raw = payload.get('_references', {}).get('images', [])
    if len(raw) != len(images):
        raise ValueError('参考图片尚未解析')
    width, height = map(int, settings['size'].split('x'))
    for index, (identity, item) in enumerate(zip(images, raw), 1):
        if item.get('id') != identity or item.get('kind') != 'image' or item.get('remote_file') != f'article_studio_refs/{identity}/reference.png':
            raise ValueError('参考图片与已解析文件不一致')
        key = f'image_reference_{index}'
        graph[key] = {'class_type': 'LoadImage', 'inputs': {'image': item['remote_file']}}
        if index == 1:
            graph['image_reference_size'] = {'class_type': 'ImageScale', 'inputs': {
                'image': [key, 0], 'upscale_method': 'lanczos', 'width': width, 'height': height, 'crop': 'center'}}
            link = ['image_reference_size', 0]
        else:
            link = [key, 0]
        if model == 'qi21':
            graph['4']['inputs'][f'images.image_{index}'] = link
    settings.update(references=refs, workflow='reference' if model == 'qi21' else 'img2img')
    if model == 'qi21':
        graph['4']['inputs']['resolution'] = 0
        graph['8']['inputs']['latent_image'] = ['4', 2]
    else:
        graph['image_reference_encode'] = {'class_type': 'VAEEncode', 'inputs': {
            'pixels': ['image_reference_size', 0], 'vae': ['vae', 0]}}
        graph['sampler']['inputs'].update(latent_image=['image_reference_encode', 0], denoise=float(denoise))
        settings['denoise'] = float(denoise)
