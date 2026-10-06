"""Validated local image attachments for multimodal chat; files persist with history."""
import base64
import io
import re
import uuid
import warnings
from PIL import Image, ImageOps, UnidentifiedImageError

MAX_BYTES=10*1024*1024
MAX_IMAGES=4

class ChatAttachmentsMixin:
    def attachment_folder(self):
        path=self.folder/'attachments';path.mkdir(exist_ok=True)
        return path

    def attachment_path(self, identity):
        if not isinstance(identity,str) or not re.fullmatch(r'[a-f0-9]{32}',identity):
            raise ValueError('无效图片编号')
        return self.attachment_folder()/(identity+'.png')

    def upload_chat_image(self, payload):
        encoded=payload.get('data','')
        if not isinstance(encoded,str) or len(encoded)>MAX_BYTES*4//3+8:
            raise ValueError('每张图片最多 10 MB')
        try:raw=base64.b64decode(encoded,validate=True)
        except (ValueError,TypeError):raise ValueError('图片数据无效') from None
        if not raw or len(raw)>MAX_BYTES:raise ValueError('每张图片最多 10 MB')
        try:
            with warnings.catch_warnings():
                warnings.simplefilter('error',Image.DecompressionBombWarning)
                with Image.open(io.BytesIO(raw)) as source:
                    if source.format not in ('PNG','JPEG','WEBP'):raise ValueError('支持 PNG、JPEG 和 WebP 图片')
                    if source.width*source.height>25_000_000:raise ValueError('图片像素过大，请缩小后重试')
                    source.load();image=ImageOps.exif_transpose(source).convert('RGBA' if 'A' in source.getbands() else 'RGB')
            image.thumbnail((2048,2048),Image.Resampling.LANCZOS)
            image.info.clear()
            output=io.BytesIO();image.save(output,format='PNG')
        except (UnidentifiedImageError,OSError,Image.DecompressionBombError,Image.DecompressionBombWarning):
            raise ValueError('无法读取图片，请选择有效的 PNG、JPEG 或 WebP 文件') from None
        identity=uuid.uuid4().hex;path=self.attachment_path(identity);path.write_bytes(output.getvalue())
        name=str(payload.get('name') or '粘贴的图片')[:150]
        meta={'id':identity,'name':name,'url':'/chat-attachments/'+identity+'.png','width':image.width,'height':image.height,'bytes':path.stat().st_size}
        self.content.write(path.with_suffix('.json'),meta)
        return meta

    def resolve_attachments(self, identities):
        if not isinstance(identities,list) or len(identities)>MAX_IMAGES:
            raise ValueError('每条消息最多附加 4 张图片')
        result=[]
        if any(not isinstance(identity,str) for identity in identities):raise ValueError('无效图片编号')
        for identity in dict.fromkeys(identities):
            path=self.attachment_path(identity)
            if not path.is_file() or not path.with_suffix('.json').is_file():raise ValueError('图片不存在，请重新上传')
            result.append(self.content.read(path.with_suffix('.json')))
        return result

    def image_parts(self, attachments, *, inline=True):
        result=[]
        for attachment in attachments:
            path=self.attachment_path(attachment['id'])
            if not path.is_file():
                result.append({'type':'text','text':'[图片文件缺失，无法查看此图]'});continue
            url='data:image/png;base64,'+base64.b64encode(path.read_bytes()).decode() if inline else attachment['url']
            result.append({'type':'image_url','image_url':{'url':url}})
        return result

    @staticmethod
    def message_budget(messages):
        # Base64 is transport encoding, not text tokens. Reserve 8192 units per image.
        import json
        def bounded(value):
            if isinstance(value,dict):
                if value.get('type')=='image_url':return {'image_budget':'x'*8192}
                return {k:bounded(v) for k,v in value.items()}
            if isinstance(value,list):return [bounded(v) for v in value]
            return value
        return len(json.dumps(bounded(messages),ensure_ascii=False).encode())
