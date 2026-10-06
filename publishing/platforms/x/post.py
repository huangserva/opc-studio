"""Ordinary X posts and threads → Buffer's official CLI input."""
from urllib.parse import urlsplit
import ipaddress

def capability(kind):
    return {'adapter':'x.'+kind,'transport':'buffer_cli',
            'stage':'payload_ready_only','label':'X 普通推文 / 串文 · Buffer CLI'}

def public_url(value):
    p=urlsplit(str(value))
    if p.scheme!='https' or not p.hostname or p.username or p.password:
        return False
    if p.hostname=='localhost' or '.' not in p.hostname or p.hostname.endswith(('.localhost','.local','.internal','.test','.invalid')):
        return False
    try:
        return ipaddress.ip_address(p.hostname).is_global
    except ValueError:
        return True

def prepare(snapshot, options):
    kind=snapshot['studio']['type']; manifest=snapshot['manifest']; target=snapshot['target']
    channel=str(options.get('channel_id') or '').strip()
    urls=options.get('media_urls') or {}
    if not isinstance(urls,dict):raise ValueError('media_urls 必须为素材文件名到公开 HTTPS 链接的映射')
    missing=[];blocking=[];notes=[];posts=[]
    media={m['id']:m for m in manifest['body_media']}
    def asset(item,media_kind='image'):
        url=urls.get(item['file'])
        if not url or not public_url(url):
            missing.append(item['file']);return None
        return {media_kind:{'url':url}}
    if kind=='post':
        for block in manifest['blocks']:
            if block['kind']=='text':
                posts.append({'text':block.get('display_text',block.get('text','')),'assets':[]})
            elif block['kind']=='image':
                if not posts:posts.append({'text':'','assets':[]})
                item=media[block['id']];a=asset(item)
                if a:posts[-1]['assets'].append(a)
        if target.get('caption'):
            if len(posts)>1:
                unchanged={snapshot['text'].strip(),'\n\n'.join(p['text'] for p in posts).strip()}
                if target['caption'].strip() not in unchanged:
                    blocking.append('串文包含多条正文：请清空单条平台文案，避免替换或合并串文内容')
            elif posts:posts[0]['text']=target['caption']
    else:
        posts=[{'text':target.get('caption') or snapshot['text'],'assets':[]}]
        if kind=='video':
            item=target.get('video') or snapshot['studio'].get('video')
            if not item:blocking.append('请先上传视频成片并保存版本')
            else:
                a=asset(item,'video')
                if a:posts[0]['assets'].append(a)
            if manifest['body_media']:
                blocking.append('视频作品还有正文插图，请明确整理为独立视频帖后再提交，避免遗漏图片')
        else:
            for item in manifest['body_media']:
                a=asset(item)
                if a:posts[0]['assets'].append(a)
    if manifest.get('cover'):
        blocking.append('作品含独立文章封面；请明确将其放入推文正文图片序列，避免静默遗漏或重复')
    if posts and target.get('topics'):
        posts[0]['text']+='\n\n'+target['topics']
    if not posts or not any(p['text'].strip() or p['assets'] for p in posts):
        blocking.append('推文内容为空')
    if not channel:blocking.append('尚未指定 Buffer 的 X channel_id；账号名称不能代替渠道 ID')
    if missing:blocking.append('本机素材需要先取得可公开访问的 HTTPS 链接')
    notes.append('仅生成草稿请求；提交前须核对 Buffer 渠道确为目标 X 账号。')
    notes.append('素材 URL 仅做格式检查，实际可访问性和平台限制由 Buffer 校验。')
    payload=None
    if not blocking:
        payload={'channelId':channel,'schedulingType':'automatic','mode':'addToQueue',
                 'saveToDraft':True,'text':posts[0]['text']}
        if posts[0]['assets']:payload['assets']=posts[0]['assets']
        if len(posts)>1:payload['metadata']={'twitter':{'thread':posts}}
    return {**capability(kind),'status':'blocked' if blocking else 'payload_ready',
            'blocking':blocking,'notes':notes,'missing_media':list(dict.fromkeys(missing)),
            'posts':posts,'buffer_input':payload,
            'next_step':'buffer posts create --input <请求文件> --dry-run'}
