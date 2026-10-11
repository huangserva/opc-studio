"""Official Account rich-text preparation following wechat-article-formatter."""
import html
import os
import mimetypes
import base64
from pathlib import Path
from html.parser import HTMLParser
from urllib.parse import urlsplit, quote
from ...core.common import under

# Portable skill resolution: an explicit override wins, otherwise the first
# candidate that actually exists on this host, otherwise the documented default.
SKILL_CANDIDATES = ('.codex/skills/wechat-article-formatter/SKILL.md',
                    'Downloads/skills/wechat-article-formatter/SKILL.md')


def resolve_skill(env=None, home=None):
    env = os.environ if env is None else env
    override = env.get('ARTICLE_STUDIO_WECHAT_SKILL')
    if override:
        return str(override)
    base = Path.home() if home is None else Path(home)
    for candidate in SKILL_CANDIDATES:
        path = base / candidate
        if path.is_file():
            return str(path)
    return str(base / SKILL_CANDIDATES[0])


SKILL = resolve_skill()
STYLES = {
    'p': 'font-size:16px;line-height:1.8;color:#333;margin:15px 0;text-align:justify;',
    'h1': 'font-size:22px;font-weight:bold;margin:20px 0;line-height:1.5;',
    'h2': 'font-size:18px;font-weight:bold;margin:15px 0;border-left:4px solid #d4237a;padding-left:10px;',
    'h3': 'font-size:17px;font-weight:bold;margin:15px 0;',
    'strong': 'color:#d4237a;',
    'blockquote': 'background:#f7f7f7;border-left:4px solid #d4237a;padding:15px;margin:15px 0;',
    'pre': 'white-space:pre-wrap;word-break:break-word;background:#f7f7f7;padding:12px;',
    'table': 'width:100%;border-collapse:collapse;font-size:14px;',
    'th': 'border:1px solid #ddd;padding:8px;background:#f7f7f7;',
    'td': 'border:1px solid #ddd;padding:8px;',
    'img': 'max-width:100%;height:auto;display:block;margin:20px auto;',
}
ALLOWED = set(STYLES) | {'h4','h5','h6','em','s','u','sup','sub','br','hr','ul','ol','li','a','code','thead','tbody','tr'}

class InlineHTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.output, self.skip = [], 0
    def handle_starttag(self, tag, attrs):
        if tag in ('script','style','iframe','object'):
            self.skip += 1
            return
        if self.skip or tag not in ALLOWED or tag == 'img':
            return
        attributes = []
        if tag in STYLES:
            attributes.append(('style', STYLES[tag]))
        for key, value in attrs:
            if tag == 'a' and key == 'href' and value and urlsplit(value).scheme.lower() in ('https','http','mailto'):
                attributes.append((key,value))
            if tag == 'ol' and key == 'start' and value and value.isdigit():
                attributes.append((key,value))
        self.output.append('<' + tag + ''.join(' '+k+'="'+html.escape(v,quote=True)+'"' for k,v in attributes) + '>')
    def handle_endtag(self, tag):
        if tag in ('script','style','iframe','object'):
            self.skip = max(0,self.skip-1)
        elif not self.skip and tag in ALLOWED and tag not in ('img','br','hr'):
            self.output.append('</'+tag+'>')
    def handle_data(self, data):
        if not self.skip:
            self.output.append(html.escape(data))


def capability(kind):
    supported = kind != 'video'
    return {'adapter':'wechat.'+kind,'transport':'wechat_html_export',
            'stage':'formatter_ready' if supported else 'unsupported',
            'label':'微信公众号 · 图文排版' if supported else '微信公众号 · 请使用图文作品'}


def prepare(snapshot, options):
    supported = snapshot['studio']['type'] != 'video'
    return {**capability(snapshot['studio']['type']), 'status':'export_ready' if supported else 'blocked',
            'blocking':[] if supported else ['公众号图文排版暂不支持视频作品；视频号是独立平台入口'],
            'notes':['按保存版本保留正文、配图顺序与封面，生成内联样式 HTML。',
                     '公众号编辑器的图片需上传并核验；导出或复制不代表已创建在线草稿。',
                     '可配置 ARTICLE_STUDIO_WECHAT_SKILL 指定本机排版 skill；尚未接通公众号在线发布。'],
            'skill':SKILL,
            'preview_url':'/api/wechat-preview/'+quote(snapshot['slug'],safe='')+'?version='+quote(snapshot['version'],safe='')}


def render(snapshot, embedded=False):
    if snapshot['studio']['type'] == 'video':
        raise ValueError('公众号图文排版暂不支持视频作品，请选择图文作品或视频号')
    title = snapshot['target'].get('title') or snapshot['manifest']['title']
    parts = ['<h1 style="'+STYLES['h1']+'">'+html.escape(title)+'</h1>']
    for block in snapshot['manifest']['blocks']:
        if block['kind'] == 'text':
            parser = InlineHTML()
            parser.feed(block['html'])
            parser.close()
            parts.append(''.join(parser.output))
        else:
            name = block['file']
            source = under(snapshot['package'],name)
            if embedded:
                mime = mimetypes.guess_type(name)[0] or 'image/png'
                url = 'data:'+mime+';base64,'+base64.b64encode(source.read_bytes()).decode('ascii')
            else:
                url = '/assets/'+quote(snapshot['slug'],safe='')+'/'+quote(snapshot['version'],safe='')+'/'+quote(name,safe='/')
            parts.append('<img style="'+STYLES['img']+'" src="'+html.escape(url,quote=True)+'" alt="'+html.escape(block.get('caption') or name,quote=True)+'">')
            if block.get('caption'):
                parts.append('<p style="font-size:13px;color:#888;text-align:center;">'+html.escape(block['caption'])+'</p>')
    body = '<section style="font-family:-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif;font-size:16px;line-height:1.8;color:#333;">'+''.join(parts)+'</section>'
    return '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>'+html.escape(title)+'</title></head><body style="max-width:600px;margin:auto;padding:20px;">'+body+'</body></html>'
