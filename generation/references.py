"""Bounded local reference assets and deterministic H3 input preparation."""
import base64
import io
import json
import math
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import threading
import uuid

from PIL import Image, ImageOps
from content_store import write

EXTENSIONS = {'image': '.png', 'video': '.mp4', 'audio': '.wav'}
MIMES = {'image': 'image/png', 'video': 'video/mp4', 'audio': 'audio/wav'}
MAX_UPLOAD = 100_000_000


def executable(name):
    path = shutil.which(name)
    if not path and Path('/opt/homebrew/bin', name).is_file():
        path = str(Path('/opt/homebrew/bin', name))
    if not path:
        raise ValueError('本机媒体处理工具暂不可用')
    return path


def probe(path):
    try:
        result = subprocess.run([executable('ffprobe'), '-v', 'error', '-count_frames',
            '-show_entries', 'format=duration:stream=codec_type,width,height,nb_read_frames,duration,avg_frame_rate:stream_disposition=attached_pic',
            '-of', 'json', str(path)], capture_output=True, check=True, timeout=30)
        info = json.loads(result.stdout)
        duration = float(info.get('format', {}).get('duration', 0))
        if not math.isfinite(duration) or duration <= 0:
            raise ValueError()
        info['duration'] = duration
        return info
    except (subprocess.SubprocessError, OSError, ValueError, TypeError):
        raise ValueError('无法读取素材，请上传有效的视频或音频文件') from None


def encode(arguments):
    try:
        subprocess.run([executable('ffmpeg'), '-hide_banner', '-loglevel', 'error', '-nostdin',
                        *arguments], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                       check=True, timeout=180)
    except (subprocess.SubprocessError, OSError):
        raise ValueError('素材处理失败，请检查文件或换一个格式重试') from None


class References:
    def __init__(self, folder):
        self.folder = Path(folder)
        self.folder.mkdir(exist_ok=True)
        self.lock = threading.Lock()

    def identity(self, identity):
        if not isinstance(identity, str) or not re.fullmatch(r'[a-f0-9]{32}', identity):
            raise ValueError('无效参考素材编号')
        return identity

    def info(self, identity):
        identity = self.identity(identity)
        manifest = self.folder / (identity + '.json')
        if manifest.is_file():
            data = json.loads(manifest.read_text())
            if data.get('id') != identity or data.get('kind') not in EXTENSIONS:
                raise ValueError('参考素材记录无效，请重新上传')
        else:
            path = self.folder / (identity + '.png')
            if not path.is_file():
                raise ValueError('参考素材不存在，请重新上传')
            with Image.open(path) as image:
                data = {'id': identity, 'kind': 'image', 'name': '已保存参考图',
                        'width': image.width, 'height': image.height, 'bytes': path.stat().st_size}
        return {**data, 'mime': MIMES[data['kind']], 'url': '/generation-references/' + identity}

    def path(self, identity):
        info = self.info(identity)
        path = self.folder / (identity + EXTENSIONS[info['kind']])
        if not path.is_file():
            raise ValueError('参考素材不存在，请重新上传')
        return path

    def image(self, payload):
        raw = payload.get('data', '')
        if not isinstance(raw, str) or len(raw) > 15_000_000:
            raise ValueError('图片最大 10 MB')
        try:
            data = base64.b64decode(raw.split(',', 1)[-1], validate=True)
            if len(data) > 10_000_000:
                raise ValueError()
            with Image.open(io.BytesIO(data)) as source:
                if source.width * source.height > 32_000_000:
                    raise ValueError()
                image = ImageOps.exif_transpose(source).convert('RGB')
                image.thumbnail((1280, 1280))
        except Exception:
            raise ValueError('请上传有效的 PNG、JPEG 或 WebP 图片（最大 10 MB）') from None
        identity = uuid.uuid4().hex
        path = self.folder / (identity + '.png')
        image.save(path)
        write(self.folder / (identity + '.json'), {
            'id': identity, 'kind': 'image', 'name': str(payload.get('name', '参考图'))[:120],
            'width': image.width, 'height': image.height, 'bytes': path.stat().st_size})
        return self.info(identity)

    def upload(self, kind, name, stream, length):
        if kind not in ('video', 'audio'):
            raise ValueError('请选择视频或音频参考素材')
        if not isinstance(length, int) or not 0 < length <= MAX_UPLOAD:
            raise ValueError('视频和音频每个文件最大 100 MB')
        identity = uuid.uuid4().hex
        with tempfile.TemporaryDirectory(prefix='.upload-', dir=self.folder) as scratch:
            source = Path(scratch) / 'source'
            with source.open('wb') as output:
                remaining = length
                while remaining:
                    chunk = stream.read(min(1024 * 1024, remaining))
                    if not chunk:
                        raise ValueError('素材没有传输完整，请重新上传')
                    output.write(chunk)
                    remaining -= len(chunk)
            meta = probe(source)
            duration = meta['duration']
            streams = meta.get('streams', [])
            has_audio = any(s.get('codec_type') == 'audio' for s in streams)
            normalized = Path(scratch) / ('reference' + EXTENSIONS[kind])
            if kind == 'video':
                videos = [s for s in streams if s.get('codec_type') == 'video'
                          and not s.get('disposition', {}).get('attached_pic')]
                if not videos or videos[0].get('width', 0) * videos[0].get('height', 0) > 16_777_216:
                    raise ValueError('请上传有效的视频，原始画面最多约 1600 万像素')
                video = videos[0]
                try:
                    duration = float(video['duration'])
                except (KeyError, TypeError, ValueError):
                    try:
                        numerator, denominator = map(float, video['avg_frame_rate'].split('/'))
                        duration = int(video['nb_read_frames']) / (numerator / denominator)
                    except (KeyError, TypeError, ValueError, ZeroDivisionError):
                        duration = meta['duration']
                if not math.isfinite(duration) or not 2 <= duration <= 15:
                    raise ValueError('每段参考视频须为 2–15 秒；超出时请先截取需要的片段')
                filters = "scale=w='min(1280,iw)':h='min(1280,ih)':force_original_aspect_ratio=decrease:force_divisible_by=2,pad=ceil(iw/32)*32:ceil(ih/32)*32:(ow-iw)/2:(oh-ih)/2,fps=24"
                encode(['-i', str(source), '-map', '0:v:0', '-map', '0:a:0?', '-vf', filters,
                        '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '20', '-pix_fmt', 'yuv420p',
                        '-c:a', 'aac', '-ar', '32000', '-ac', '2',
                        *(['-af', 'apad'] if has_audio else []), '-t', str(duration), str(normalized)])
            else:
                if not has_audio:
                    raise ValueError('这个文件没有音轨，请上传有效的音频')
                if not 2 <= duration <= 15:
                    raise ValueError('每段参考音频须为 2–15 秒；超出时请先截取需要的片段')
                encode(['-i', str(source), '-map', '0:a:0', '-vn', '-ar', '32000', '-ac', '2',
                        '-c:a', 'pcm_s16le', str(normalized)])
            checked = probe(normalized)
            actual = checked['duration']
            if normalized.stat().st_size > MAX_UPLOAD:
                raise ValueError('处理后的素材超过 100 MB，请缩小后重新上传')
            record = {'id': identity, 'kind': kind, 'name': str(name or ('参考视频' if kind == 'video' else '参考音频'))[:120],
                      'duration': actual, 'source_duration': duration,
                      'bytes': normalized.stat().st_size, 'has_audio': has_audio}
            if kind == 'video':
                video = next(s for s in checked['streams'] if s['codec_type'] == 'video')
                record.update(width=video['width'], height=video['height'], fps=24,
                              frames=int(video['nb_read_frames']))
            normalized.replace(self.folder / (identity + EXTENSIONS[kind]))
            write(self.folder / (identity + '.json'), record)
        return self.info(identity)

    def prepare(self, identity, kind, frames=None, inline=False):
        info = self.info(identity)
        if info['kind'] != kind:
            raise ValueError('参考素材类型与所选输入不匹配，请重新选择')
        source = self.path(identity)
        prepared = self.folder / 'prepared' / identity
        prepared.mkdir(parents=True, exist_ok=True)
        result = dict(info)
        if kind == 'image':
            target = prepared / 'reference.png'
            with self.lock:
                if not target.is_file():
                    with Image.open(source) as image:
                        image = image.convert('RGB')
                        width = (image.width + 31) // 32 * 32
                        height = (image.height + 31) // 32 * 32
                        canvas = Image.new('RGB', (width, height), (127, 127, 127))
                        canvas.paste(image, ((width - image.width) // 2, (height - image.height) // 2))
                        canvas.save(target)
            with Image.open(target) as image:
                result.update(width=image.width, height=image.height)
            if inline:
                result['imageB64'] = base64.b64encode(target.read_bytes()).decode()
        elif kind == 'video':
            available = min(int(info['frames']), frames or int(info['frames']))
            used = (available - 5) // 17 * 17 + 5
            if used < 5:
                raise ValueError('参考视频没有足够的有效帧')
            target = prepared / f'{used}.mp4'
            with self.lock:
                if not target.is_file():
                    temporary = prepared / ('.' + uuid.uuid4().hex + '.mp4')
                    try:
                        encode(['-i', str(source), '-map', '0:v:0', '-map', '0:a:0?',
                                '-frames:v', str(used), '-t', str(used / 24), '-c:v', 'copy',
                                '-c:a', 'aac', '-ar', '32000', '-ac', '2', str(temporary)])
                        if int(next(s for s in probe(temporary)['streams'] if s['codec_type'] == 'video')['nb_read_frames']) != used:
                            raise ValueError('参考视频处理帧数不完整，请重新上传')
                        temporary.replace(target)
                    finally:
                        temporary.unlink(missing_ok=True)
            result.update(frames=used, duration=used / 24)
        else:
            target = source
        result['remote_file'] = f'article_studio_refs/{identity}/reference{EXTENSIONS[kind]}'
        result['_local_path'] = str(target)
        return result
