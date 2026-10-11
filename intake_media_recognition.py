"""Installed, private-host media recognition only; no model downloads or API keys.

图片描述走工作台当前模型配置（ProfileVision → PiRuntime chat/completions），视频转写走 4090 已有 FunASR。
两条都不装东西、不下模型、不读密钥、不碰外部 API。
"""
import json
import math
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import tempfile
import threading
import time
import uuid

MAX_SECONDS = 600
REMOTE_PYTHON = '/home/admin01/.cache/hive/funasr-venv/bin/python'
REMOTE_ASR = '/home/admin01/funasr_asr.py'
MODEL = 'FunASR Paraformer zh-cn + FSMN VAD + CT punctuation'
# 图片识别：长边缩到 2048 以内再送模型，和聊天附件同一套缩放；一张图最多一次模型调用。
MAX_IMAGE_EDGE = 2048
VISION_RUN_SECONDS = 240
VISION_PROMPT = (
    '请看这张图，用中文写下你实际看到的内容：主体、场景、动作、构图、光线、文字（图上有的就逐字抄出）、'
    '能看出的风格。只写图上真看得到的，看不出不猜，不补充品牌、人物身份或数据；'
    '图上信息有限就直说有限。不要开场白，不要总结建议。')

def model_busy_class():
    """ModelBusy 住在调度器里；延迟导入，不让媒体识别模块去拉整份调度依赖。"""
    from scheduled_tasks import ModelBusy
    return ModelBusy


class CapabilityUnavailable(ValueError):
    pass


class ProfileVision:
    """用工作台当前模型配置读一张图：一次 chat/completions，图片按 image_url data URI 送出。

    模型配置、繁忙探测和调用通道全部复用即时分析那一条路（PiRuntime → pi-ai openai-completions）：
    不新建服务、不新建模型配置、不读密钥、不碰外部视觉 API。一张图最多一次模型调用，
    图片先缩到长边 ≤2048 的 PNG。没配模型或模型正忙都如实拒调，不伪描述。
    """

    def __init__(self, model_provider, busy_probe=None, folder=None):
        self.model_provider = model_provider
        self.busy_probe = busy_probe
        self.folder = Path(folder) if folder else None

    def prepare(self, path):
        """缩放后的 PNG 字节与缩放后尺寸；原文件不动。"""
        import io
        import warnings
        from PIL import Image, ImageOps, UnidentifiedImageError
        try:
            with warnings.catch_warnings():
                warnings.simplefilter('error', Image.DecompressionBombWarning)
                with Image.open(path) as source:
                    source.load()
                    image = ImageOps.exif_transpose(source).convert('RGBA' if 'A' in source.getbands() else 'RGB')
        except (UnidentifiedImageError, OSError, Image.DecompressionBombError, Image.DecompressionBombWarning):
            raise ValueError('图片本机读不到，未送模型识别') from None
        image.thumbnail((MAX_IMAGE_EDGE, MAX_IMAGE_EDGE), Image.Resampling.LANCZOS)
        image.info.clear()
        buffer = io.BytesIO()
        image.save(buffer, format='PNG')
        return buffer.getvalue(), image.width, image.height

    def _call(self, prompt, config, cancel, deadline):
        """一次模型调用，图以附件身份进去（pi_runtime 编成 image_url data URI）。"""
        from types import SimpleNamespace
        from pi_runtime import PiRuntime
        folder = self.folder or Path('.')
        folder.mkdir(parents=True, mode=0o700, exist_ok=True)
        token = uuid.uuid4().hex
        image = folder / ('vision-' + token + '.png')
        # 每张图一个新 session，不续接 intake-vision 的历史。
        conversation_id = 'intake-vision-' + token
        session_dir = folder / 'pi-sessions' / conversation_id
        finished = threading.Event()
        try:
            image.write_bytes(self.blob)
            image.chmod(0o600)

            def no_transport(*args, **kwargs):
                raise ValueError('图片识别仅使用 Pi 原生模型接口')

            owner = SimpleNamespace(folder=folder, lock=threading.RLock(), turns=[], tool_labels={},
                                    request_model=no_transport, client=no_transport,
                                    persist_pending=lambda job: None, attachment_path=lambda identity: image,
                                    _schedule_deadline=deadline, _schedule_cancel=cancel)
            job = {'id': 'intake-vision-' + token[:12], 'conversation_id': conversation_id,
                   'model_config': config, 'prompt': prompt, 'context': {},
                   'attachments': [{'id': image.stem, 'name': '回收站图片'}], 'answer': '',
                   'policy': {'allowed_tools': []}, 'actions': [], 'cancel': False,
                   'max_provider_requests': 1, 'compaction_enabled': False,
                   '_workflow_stage': 'scheduled', '_schedule_cwd': str(folder.parent)}

            def watch():
                while not finished.wait(.2):
                    if cancel.is_set() or time.monotonic() >= deadline:
                        job['cancel'] = True
                        return

            threading.Thread(target=watch, daemon=True).start()
            result = PiRuntime(owner).run(job, '你是看图描述助手，中文简洁回答，不调用工具。', [])
            if cancel.is_set() or time.monotonic() >= deadline:
                raise ValueError('图片识别本轮停止或达到运行上限，未生成描述')
            if result.get('status') != 'done':
                raise ValueError('模型未返回有效描述，未生成图片描述')
            return result.get('answer', '')
        finally:
            finished.set()
            image.unlink(missing_ok=True)
            shutil.rmtree(session_dir, ignore_errors=True)

    def __call__(self, path):
        config = (self.model_provider() or {}) if self.model_provider else {}
        if not isinstance(config, dict) or not config.get('model') or not config.get('endpoint'):
            raise CapabilityUnavailable('未接上图片识别模型：请先在模型设置里配好当前模型，再重新解析')
        if self.busy_probe and self.busy_probe(config):
            raise model_busy_class()('同一模型已有后台任务在运行，图片识别暂缓；可稍后重新解析')
        self.blob, width, height = self.prepare(path)
        started = time.monotonic()
        answer = self._call(VISION_PROMPT, dict(config), threading.Event(), started + VISION_RUN_SECONDS)
        text = str(answer or '').strip()
        if not text:
            raise ValueError('模型未返回图片描述')
        return {'text': text, 'model': str(config.get('model') or '')[:200],
                'scope': f'本张图片的视觉描述，未核实（已缩放至 {width}×{height} 送模型）'}


class MediaRecognizer:
    def __init__(self, image_describer=None):
        self.image_describer = image_describer
        self.host = os.environ.get('ARTICLE_STUDIO_GPU_HOST', 'newgpu')
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}', self.host):
            raise ValueError('GPU 主机别名无效')

    def _ssh(self, command, timeout=15):
        return subprocess.run(['ssh','-T','-o','BatchMode=yes','-o','ConnectTimeout=8',
                               '-o','StrictHostKeyChecking=yes','--',self.host,command],
                              capture_output=True,text=True,timeout=timeout,check=True)

    def _require_idle(self):
        usage=self._ssh('nvidia-smi --query-gpu=utilization.gpu --format=csv,noheader,nounits').stdout.strip().splitlines()
        if not usage or any(not x.strip().isdigit() or int(x)>0 for x in usage):
            raise CapabilityUnavailable('4090 正在使用，语音转写暂缓；可稍后重新解析')

    def recognize_image(self, path, metadata):
        if self.image_describer is None:
            raise CapabilityUnavailable('未接上图片识别模型：请先在模型设置里配好当前模型，再重新解析')
        result = self.image_describer(path)
        if not isinstance(result,dict) or not isinstance(result.get('text'),str) or not result['text'].strip() or not result.get('model'):
            raise ValueError('视觉模型未返回有效描述与模型标识')
        return {**result,'text':result['text'][:20000],'label':result.get('label') or 'AI 描述，未核实',
                'scope':result.get('scope') or '本张图片的视觉描述，未核实'}

    def recognize_video(self, path, metadata):
        if metadata.get('has_audio') is False:
            return {'status':'no_audio','text':'','label':'语音转写，未核实','scope':'视频无音轨；未生成语音转写','model':MODEL}
        ffmpeg,ffprobe=shutil.which('ffmpeg'),shutil.which('ffprobe')
        if not ffmpeg or not ffprobe:
            raise CapabilityUnavailable('本机 ffmpeg/ffprobe 不可用，未读取视频语音')
        info=json.loads(subprocess.run([ffprobe,'-v','error','-show_entries','format=duration:stream=codec_type','-of','json',str(path)],capture_output=True,text=True,check=True,timeout=30).stdout)
        if not any(s.get('codec_type')=='audio' for s in info.get('streams',[])):
            return {'status':'no_audio','text':'','label':'语音转写，未核实','scope':'视频无音轨；未生成语音转写','model':MODEL}
        # Only installed files are accepted; no package/model acquisition path.
        self._ssh('test -x '+REMOTE_PYTHON+' && test -f '+REMOTE_ASR)
        self._require_idle()
        identity=uuid.uuid4().hex
        remote='/tmp/article-studio-intake-'+identity
        try:
            with tempfile.TemporaryDirectory(prefix='intake-asr-') as directory:
                audio=Path(directory)/'audio.wav'
                subprocess.run([ffmpeg,'-nostdin','-v','error','-y','-i',str(path),'-map','0:a:0','-t',str(MAX_SECONDS),'-ar','16000','-ac','1',str(audio)],capture_output=True,check=True,timeout=120)
                subprocess.run(['scp','-q','-o','BatchMode=yes','-o','ConnectTimeout=8','-o','StrictHostKeyChecking=yes',str(audio),self.host+':'+remote+'.wav'],capture_output=True,check=True,timeout=120)
                self._require_idle()
                command='timeout 300 env HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 '+REMOTE_PYTHON+' '+REMOTE_ASR+' '+remote+'.wav '+remote+'.json'
                self._ssh(command,timeout=320)
                output=json.loads(self._ssh('cat '+shlex.quote(remote+'.json')).stdout)
            item=output[0] if isinstance(output,list) and output else None
            if not isinstance(item,dict) or not isinstance(item.get('text'),str) or not item['text'].strip():
                raise ValueError('语音模型未返回有效文本；无可识别语音或模型输出为空')
            try:duration=float(info.get('format',{}).get('duration'))
            except (ValueError,TypeError):duration=None
            if duration is not None and (not math.isfinite(duration) or duration<=0):duration=None
            covered=min(duration,MAX_SECONDS) if duration and duration>0 else MAX_SECONDS
            return {'text':item['text'][:100000],'timestamps_ms':item.get('timestamp',[]),
                    'label':'AI 语音转写，未核实','model':MODEL,'scope':'第一条音轨，0–'+str(round(covered,2))+' 秒；'+('音轨范围已完整处理' if duration and duration<=MAX_SECONDS else '仅覆盖前600秒，剩余未读取'),
                    'coverage_seconds':covered,'complete':bool(duration and duration<=MAX_SECONDS)}
        finally:
            try:self._ssh('rm -f '+remote+'.wav '+remote+'.json')
            except (OSError,subprocess.SubprocessError):pass
