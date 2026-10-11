"""Native H3 reference conditioning, keyframes, and joint AV sampling.

ComfyUI Autogrow API inputs use dotted names. The reference node presents
images, videos and their paired soundtracks, then standalone audio.
"""
import math
import copy


def _node(class_type, **inputs):
    return {'class_type': class_type, 'inputs': inputs}


def _finite_number(value, label):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(label + '须为有限数字')
    try:
        number = float(value)
    except (ValueError, OverflowError):
        raise ValueError(label + '须为有限数字') from None
    if not math.isfinite(number):
        raise ValueError(label + '须为有限数字')
    return number


def _descriptor(value, identity, kind, output_frames):
    allowed = {'remote_file', 'width', 'height', 'duration', 'source_duration', 'frames', 'has_audio', 'name', 'id', 'kind'}
    if kind == 'image':
        allowed.add('imageB64')  # Shared anchor descriptors carry legacy Director content.
    if not isinstance(value, dict) or not set(value) <= allowed:
        raise ValueError('无效参考素材内容参数')
    if value.get('id') != identity or value.get('kind') != kind:
        raise ValueError('参考素材类型或编号不匹配')
    extension = {'image': 'png', 'video': 'mp4', 'audio': 'wav'}[kind]
    if value.get('remote_file') != f'article_studio_refs/{identity}/reference.{extension}':
        raise ValueError('参考素材远程文件路径不匹配')
    if 'name' in value and not isinstance(value['name'], str):
        raise ValueError('参考素材名称须为字符串')
    result = {key: item for key, item in value.items() if key != 'imageB64'}
    if kind in ('image', 'video'):
        for key in ('width', 'height'):
            size = value.get(key)
            if isinstance(size, bool) or not isinstance(size, int) or size < 32 or size % 32:
                raise ValueError('参考素材宽高须为 32 的倍数')
    if kind == 'video':
        frames = value.get('frames')
        if isinstance(frames, bool) or not isinstance(frames, int) or frames < 39 or frames > min(345, output_frames) or frames % 17 != 5:
            raise ValueError('参考视频须为裁齐输出长度的 24 fps、17k+5 帧素材')
        duration = _finite_number(value.get('duration'), '参考视频时长')
        if abs(duration - frames / 24) > 1 / 24000:
            raise ValueError('参考视频时长与 24 fps 帧数不匹配')
        if not isinstance(value.get('has_audio'), bool):
            raise ValueError('参考视频须明确标注是否包含音轨')
        result['duration'] = duration
    if kind == 'audio':
        duration = _finite_number(value.get('duration'), '参考音频时长')
        if not 2 <= duration <= 15:
            raise ValueError('每个参考音频须为 2–15 秒')
        result['duration'] = duration
    if kind in ('video', 'audio') and 'source_duration' in value:
        source_duration = _finite_number(value['source_duration'], '参考素材原始时长')
        if not 2 <= source_duration <= 15 or source_duration + 1 / 24 < result['duration']:
            raise ValueError('参考素材原始时长须为 2–15 秒且不短于实际取用时长')
        result['source_duration'] = source_duration
    return result


def _resolve(raw, references, frames):
    if not isinstance(raw, dict) or not set(raw) <= {'first', 'last', 'reference', 'images', 'videos', 'audios'}:
        raise ValueError('无效参考素材内容')
    result = {key: [] for key in ('images', 'videos', 'audios')}
    if 'reference' in raw:
        result['images'].append(raw['reference'])
    for key in result:
        values = raw.get(key, [])
        if not isinstance(values, list):
            raise ValueError('参考素材内容须使用列表')
        result[key].extend(values)
    for key in ('first', 'last'):
        if (key in raw) != (key in references):
            raise ValueError('首尾帧内容与编号不匹配')
        if key in references:
            result[key] = _descriptor(raw[key], references[key], 'image', frames)
    for key, kind in (('images', 'image'), ('videos', 'video'), ('audios', 'audio')):
        if len(result[key]) != len(references[key]):
            raise ValueError('参考素材内容与编号列表不匹配')
        result[key] = [_descriptor(value, identity, kind, frames) for value, identity in zip(result[key], references[key])]
    for key in ('videos', 'audios'):
        if sum(value['duration'] for value in result[key]) > 15 + 1 / 24000:
            raise ValueError('参考视频和独立音频的取用总时长各不得超过 15 秒')
    return result


def build_native_h3(graph, raw, references, settings, prompt, frames, model_link):
    """Replace Director with stock nodes while preserving loaders and LoRAs."""
    resolved = _resolve(raw, references, frames)
    width, height = map(int, settings['size'].split('x'))
    prefix = graph['7']['inputs']['filename_prefix']
    native = {key: value for key, value in graph.items() if key in ('1', '2', '3', '4', '10') or key.startswith('video_lora_')}
    dual = settings.get('video_sampling') == 'dual'
    output_width, output_height = width, height
    if dual:
        scale = settings['video_upscale']
        width, height = [max(32, round(n / scale / 32) * 32) for n in (width, height)]
        settings['video_first_size'] = f'{width}x{height}'
    inputs = {'clip': ['2', 0], 'vae': ['3', 0], 'audio_vae': ['4', 0], 'prompt': prompt,
              'width': width, 'height': height, 'length': frames, 'ref_image_size': 'match'}
    labels, video_usage, anchor_links = [], [], {}
    pictures = [(key, resolved[key]) for key in ('first', 'last') if key in resolved]
    pictures += [('image', item) for item in resolved['images']]
    for index, (role, value) in enumerate(pictures):
        key = 'ref_image_' + str(index)
        native[key] = _node('LoadImage', image=value['remote_file'])
        inputs[f'ref_images.ref_image_{index}'] = [key, 0]
        if role in ('first', 'last'):
            anchor_links[role] = [key, 0]
        labels.append({'id': value['id'], 'kind': 'image', 'role': role, 'name': value.get('name', ''), 'labels': [f'<Picture {index + 1}>']})
    audio_index = 0
    for index, value in enumerate(resolved['videos']):
        key, components = 'ref_video_' + str(index), 'ref_video_components_' + str(index)
        native[key] = _node('LoadVideo', file=value['remote_file'])
        native[components] = _node('GetVideoComponents', video=[key, 0])
        inputs[f'ref_videos.ref_video_{index}'] = [components, 0]
        tags = []
        if value['has_audio']:
            inputs[f'ref_video_audios.ref_video_audio_{index}'] = [components, 1]
            audio_index += 1
            tags.append(f'<Audio {audio_index}>')
        tags.append(f'<Video {index + 1}>')
        usage = {'id': value['id'], 'frames': value['frames'], 'fps': 24, 'duration': value['duration'], 'has_audio': value['has_audio']}
        if 'source_duration' in value:
            usage['source_duration'] = value['source_duration']
        video_usage.append(usage)
        labels.append({'id': value['id'], 'kind': 'video', 'role': 'video', 'name': value.get('name', ''), 'labels': tags, **usage})
    for index, value in enumerate(resolved['audios']):
        key = 'ref_audio_' + str(index)
        native[key] = _node('LoadAudio', audio=value['remote_file'])
        inputs[f'ref_audios.ref_audio_{index}'] = [key, 0]
        audio_index += 1
        labels.append({'id': value['id'], 'kind': 'audio', 'role': 'audio', 'name': value.get('name', ''), 'labels': [f'<Audio {audio_index}>'], 'duration': value['duration']})
    reference_mode = settings['workflow'] in ('r2v', 'ir2v', 'flr2v')
    if reference_mode:
        native['native_reference'] = _node('MiniMaxH3ReferenceToVideo', **inputs)
    else:
        image_inputs = {key: inputs[key] for key in ('clip', 'vae', 'prompt', 'width', 'height', 'length')}
        for role, link in anchor_links.items():
            image_inputs[role + '_frame'] = link
        native['native_reference'] = _node('MiniMaxH3ImageToVideo', **image_inputs)
    positive = ['native_reference', 0]
    latent = ['native_reference', 1]
    for role, frame_index in (('first', 0), ('last', -1)):
        if role in anchor_links and reference_mode:
            key = 'guide_' + role
            native[key] = _node('MiniMaxH3AddGuide', positive=positive, latent=latent, frame_idx=frame_index, vae=['3', 0], image=anchor_links[role])
            positive = [key, 0]
    native['sigma_shift'] = _node('MiniMaxH3SigmaShift', model=model_link, shift_video=12.0, shift_audio=3.0)
    shifted = ['sigma_shift', 0]
    native['scheduler'] = _node('BasicScheduler', model=shifted, scheduler='simple', steps=settings['steps'], denoise=1.0)
    native['guider'] = _node('BasicGuider', model=shifted, conditioning=positive)
    native['noise'] = _node('RandomNoise', noise_seed=settings['seed'])
    native['sampler_select'] = _node('KSamplerSelect', sampler_name='res_multistep')
    native['native_sampler'] = _node('SamplerCustomAdvanced', noise=['noise', 0], guider=['guider', 0], sampler=['sampler_select', 0], sigmas=['scheduler', 0], latent_image=latent)
    output_sampler = 'native_sampler'
    if dual:
        native['first_separate_av'] = _node('LTXVSeparateAVLatent', av_latent=['native_sampler', 0])
        native['video_upscale'] = _node('MinimaxH3LatentUpscaler3D', latent=['first_separate_av', 0],
            model_name='minimax_h3_latent_upscaler_3d_fp16.safetensors',
            mode='target dimensions', **{'mode.width': output_width, 'mode.height': output_height},
            align=32, enable_temporal_chunking=True, force_unload=True, device='cuda', precision='fp16')
        native['refine_av'] = _node('LTXVConcatAVLatent', video_latent=['video_upscale', 0], audio_latent=['first_separate_av', 1])
        native['refine_reference'] = copy.deepcopy(native['native_reference'])
        native['refine_reference']['inputs'].update(width=output_width, height=output_height)
        refine_positive = ['refine_reference', 0]
        for role, frame_index in (('first', 0), ('last', -1)):
            if role in anchor_links and reference_mode:
                key = 'refine_guide_' + role
                native[key] = _node('MiniMaxH3AddGuide', positive=refine_positive, latent=['refine_av', 0],
                    frame_idx=frame_index, vae=['3', 0], image=anchor_links[role])
                refine_positive = [key, 0]
        native['refine_guider'] = _node('BasicGuider', model=shifted, conditioning=refine_positive)
        native['refine_scheduler'] = _node('BasicScheduler', model=shifted, scheduler='simple',
            steps=settings['video_refine_steps'], denoise=settings['video_refine_denoise'])
        native['refine_sampler'] = _node('SamplerCustomAdvanced', noise=['noise', 0], guider=['refine_guider', 0],
            sampler=['sampler_select', 0], sigmas=['refine_scheduler', 0], latent_image=['refine_av', 0])
        output_sampler = 'refine_sampler'
    native['separate_av'] = _node('LTXVSeparateAVLatent', av_latent=[output_sampler, 0])
    native['video_decode'] = _node('VAEDecode', samples=['separate_av', 0], vae=['3', 0])
    native['audio_decode'] = _node('VAEDecodeAudio', samples=['separate_av', 1], vae=['4', 0])
    native['6'] = _node('CreateVideo', images=['video_decode', 0], audio=['audio_decode', 0], fps=24.0, bit_depth=8)
    native['7'] = _node('SaveVideo', video=['6', 0], filename_prefix=prefix, format='mp4', **{'format.codec': 'h264'})
    graph.clear()
    graph.update(native)
    settings.update(engine='native', reference_labels=labels, reference_video_usage=video_usage)
