"""Official Account rich-text preparation following wechat-article-formatter."""
import html
import os
import re
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
    'hr': 'border:none;border-top:1px solid #ddd;margin:20px 0;',
}
ALLOWED = set(STYLES) | {'h4','h5','h6','em','s','u','sup','sub','br','hr','ul','ol','li','a','code','thead','tbody','tr','span'}

# 样式积木 → 公众号内联 style。色板/字号与 rich_text.py、public/style.css 保持一致；
# 这里复制常量而不是 import，因为 publishing 包不能反向依赖工作区根目录模块。
RICH_COLORS = {'red':'#d63031','blue':'#2f6fe4','green':'#1e8e4f','orange':'#d97a1a','gray':'#7c8396','accent':'#4263eb'}
RICH_HIGHLIGHTS = {'yellow':'#fff3ad','green':'#d3f2d9','pink':'#ffd9e2'}
RICH_SIZES = {'s':'14px','m':'16px','l':'20px','xl':'26px'}
RICH_SPACES = {'tight':'margin:6px 0;','normal':'','loose':'margin:30px 0;'}
# 块级积木 → 基础主题里要被替换掉的声明（换成积木值），其余直接追加。
RICH_OVERRIDES = {
    'data-align': ('text-align', lambda v: 'text-align:'+v+';'),
    'data-indent': ('text-indent', lambda v: 'text-indent:2em;' if v=='2' else ''),
    'data-space': ('margin', lambda v: RICH_SPACES.get(v,'')),
    'data-size': ('font-size', lambda v: 'font-size:'+RICH_SIZES[v]+';' if v in RICH_SIZES else ''),
}
# 承载块级积木的全部标签（含不在主题 STYLES 里的 li）；这些标签的积木都要换算成内联 style。
RICH_BLOCK_TAGS = frozenset({'p','h1','h2','h3','li','blockquote'})

def rich_inline_style(tag, attrs):
    """把 data-* 积木换算成公众号可用的内联 style 声明（覆盖主题默认值）。

    每条声明都以分号结尾：块级标签的积木与行内颜色/高亮拼接时，
    缺分号会把下一条声明吞进前一条的值里（M1）。
    """
    values = dict(attrs)
    base = STYLES.get(tag, '')
    extra = []
    for name, (prop, render) in RICH_OVERRIDES.items():
        value = values.get(name)
        if value is None:
            continue
        rendered = render(value)
        if rendered:
            base = re.sub(prop + r':[^;]*;?', '', base)
            extra.append(rendered)
    if tag == 'span':
        if values.get('data-color') in RICH_COLORS:
            extra.append('color:' + RICH_COLORS[values['data-color']] + ';')
        if values.get('data-hl') in RICH_HIGHLIGHTS:
            extra.append('background-color:' + RICH_HIGHLIGHTS[values['data-hl']] + ';padding:0 4px;')
    return base + ''.join(extra)


class InlineHTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.output, self.skip = [], 0
        # span/mark 开闭配对栈 + 用户显式颜色深度：strong 主题色不得盖掉范围内的用户颜色（M4）。
        self.span_stack, self.user_color = [], 0
    def handle_starttag(self, tag, attrs):
        if tag in ('script','style','iframe','object'):
            self.skip += 1
            return
        if self.skip or (tag not in ALLOWED and tag != 'mark') or tag == 'img':
            return
        values = dict(attrs)
        attributes = []
        if tag == 'mark':
            # mark 在公众号编辑器不可靠，统一降为带底色的 span。
            tag = 'span'
            attributes.append(('style', 'background-color:'+RICH_HIGHLIGHTS['yellow']+';padding:0 4px;'))
        elif tag in STYLES or tag == 'span' or tag in RICH_BLOCK_TAGS:
            attributes.append(('style', rich_inline_style(tag, attrs)))
        for key, value in attrs:
            if tag == 'a' and key == 'href' and value and urlsplit(value).scheme.lower() in ('https','http','mailto'):
                attributes.append((key,value))
            if tag == 'ol' and key == 'start' and value and value.isdigit():
                attributes.append((key,value))
        if tag == 'strong' and self.user_color > 0:
            # 用户显式颜色作用范围内，主题强调色退位，只保留加粗。
            attributes = [('style','font-weight:bold;')] + [a for a in attributes if a[0] != 'style']
        self.output.append('<' + tag + ''.join(' '+k+'="'+html.escape(v,quote=True)+'"' for k,v in attributes) + '>')
        if tag in ('span','mark'):
            # mark 已改写为 span，但结束标签仍是 </mark>，栈按是否带用户颜色配对。
            colored = tag == 'span' and values.get('data-color') in RICH_COLORS
            self.span_stack.append('color' if colored else 'plain')
            if colored:
                self.user_color += 1
    def handle_endtag(self, tag):
        if tag in ('script','style','iframe','object'):
            self.skip = max(0,self.skip-1)
        elif not self.skip and (tag in ALLOWED or tag == 'mark') and tag not in ('img','br','hr'):
            # mark 开始时被改写为 span，结束标签也要跟着改写（M2），否则高亮外溢。
            self.output.append('</'+('span' if tag=='mark' else tag)+'>')
            if tag in ('span','mark') and self.span_stack:
                if self.span_stack.pop() == 'color':
                    self.user_color = max(0, self.user_color - 1)
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
