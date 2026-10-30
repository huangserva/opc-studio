"""素材平台登记表；所有从素材 ID 推导平台的调用统一从这里进入。"""
from __future__ import annotations

PLATFORMS = {
    key: {'key': key, 'display_name': name, 'icon': None, 'color': None}
    for key, name in (
        ('x', 'X'), ('douyin', '抖音'), ('xhs', '小红书'),
        ('wechat', '微信'), ('github', 'GitHub'), ('linuxdo', 'LINUX DO'),
        ('web', '网页'),
    )
}


def derive_platform(item_id):
    """识别带内容的已登记 ID 前缀；未知或不完整的 ID 归入 web。"""
    if not isinstance(item_id, str):
        return 'web'
    prefix, colon, suffix = item_id.partition(':')
    prefix = prefix.lower()
    return prefix if colon and suffix.strip() and prefix in PLATFORMS else 'web'
