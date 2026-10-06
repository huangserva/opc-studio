"""Only this registry decides platform/content routing. No network side effects."""
from .platforms.x import article, post, gallery, video
from .platforms import xiaohongshu, douyin, channels, wechat

PLATFORMS = {'x': 'X', 'xiaohongshu': '小红书', 'douyin': '抖音', 'channels': '视频号', 'wechat': '微信公众号'}
TYPES = ('article', 'post', 'gallery', 'video')
ADAPTERS = {('x', 'article'): article, ('x', 'post'): post,
            ('x', 'gallery'): gallery, ('x', 'video'): video}
for platform, adapter in [('xiaohongshu', xiaohongshu), ('douyin', douyin), ('channels', channels), ('wechat', wechat)]:
    for kind in TYPES:
        ADAPTERS[(platform, kind)] = adapter

def capabilities():
    return [{'platform':p, 'platform_name':PLATFORMS[p], 'content_type':k,
             **a.capability(k)} for (p,k),a in ADAPTERS.items()]

def build_plan(snapshot, options=None):
    key=(snapshot['platform'],snapshot['studio']['type'])
    if key not in ADAPTERS:
        raise ValueError('不支持的平台或作品类型')
    result=ADAPTERS[key].prepare(snapshot,options or {})
    return {'schema_version':1,'platform':key[0],'content_type':key[1],
            'slug':snapshot['slug'],'source_version':snapshot['version'],
            'package_id':snapshot['manifest']['package_id'], **result,
            'published':False}
