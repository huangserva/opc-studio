"""素材平台登记表；所有从素材 ID 推导平台的调用统一从这里进入。"""
from __future__ import annotations

# 唯一登记源。图标为本地静态文件；API 将登记表交给前端，不维护 JS 副本。
PLATFORMS = {
    key: dict(key=key, display_name=name, icon='/icons/platforms/' + icon + '.svg', color=color)
    for key, name, icon, color in (
        ('x', 'X', 'x', '#14171A'), ('douyin', '抖音', 'tiktok', '#161823'),
        ('xhs', '小红书', 'xiaohongshu', '#FF2442'), ('wechat', '微信', 'wechat', '#07C160'),
        ('github', 'GitHub', 'github', '#181717'), ('linuxdo', 'linux.do', 'linuxdo', '#B78024'),
        ('web', '网页', 'web', '#697586'),
    )
}
CHANNELS = {
    key: dict(key=key, display_name=name, short_name=short, icon='/icons/platforms/' + icon + '.svg', color=color, aliases=aliases)
    for key, name, short, icon, color, aliases in (
        ('grok', 'Grok Bot', 'Grok', 'grok', '#526174', ('G', 'Grok', 'Grok Bot', 'grok:daily', 'grok:alerts', 'grok:interest', 'grok:radar')),
        ('aisa', 'AIsa', 'AIsa', 'aisa', '#526174', ('A', 'V', 'AIsa', 'video', 'aisa:twitter', 'aisa:video', 'video:*', 'aisa:*')),
        ('wechat', '微信雷达', '微信雷达', 'wechat', '#07C160', ('W', 'wechat', 'wechat_tools', 'wechat:*', 'wechat_tools:*')),
        ('rss', 'RSS', 'RSS', 'rss', '#FFA500', ('L', 'RSS', 'linuxdo', 'rss:linuxdo', 'linuxdo:rss', 'rss:*')),
        ('github', 'GitHub API', 'GitHub API', 'github', '#181717', ('GitHub API', 'github:api')),
        ('manual', '手动', '手动', 'manual', '#697586', ('手动', 'manual', 'x:bookmarks', 'reader:questions')),
        ('unknown', '渠道未知', '渠道未知', 'web', '#697586', ()),
    )
}
CHANNEL_METADATA = {key: {field:value for field,value in row.items() if field != 'aliases'} for key,row in CHANNELS.items()}
LEGACY_SOURCE_FILTERS = {'G': 'grok', 'A': 'aisa', 'V': 'aisa', 'L': 'rss', 'W': 'wechat'}


def derive_platform(item_id):
    """识别带内容的已登记 ID 前缀；未知或不完整的 ID 归入 web。"""
    if not isinstance(item_id, str):
        return 'web'
    prefix, colon, suffix = item_id.partition(':')
    prefix = prefix.lower()
    return prefix if colon and suffix.strip() and prefix in PLATFORMS else 'web'


def channel_key(value):
    if not isinstance(value, str):
        return 'unknown'
    value = value.strip().casefold()
    for key, row in CHANNELS.items():
        if value == key or any(value.startswith(a[:-1].casefold()) if a.endswith('*') else value == a.casefold() for a in row['aliases']):
            return key
    return 'unknown'


def normalize_channels(values):
    return list(dict.fromkeys(channel_key(v) for v in values)) or ['unknown']


def registries():
    return dict(platforms=PLATFORMS, channels=CHANNEL_METADATA)


def identity(item, record=None):
    """K1 线索平台/渠道优先，尚未入库的旧记录只在此兜底。原来源原样保留。"""
    record = record or {}
    platform = record.get('platform') or item.get('platform') or derive_platform(item.get('id'))
    platform = platform if isinstance(platform,str) and platform in PLATFORMS else 'web'
    values = [v for entries in (record.get('channels'), item.get('channels'), item.get('sources')) if isinstance(entries,(list,tuple)) for v in entries]
    if not values and item.get('source'):
        values = [item['source']]
    channels = normalize_channels(values)
    if len(channels) > 1 and 'unknown' in channels:
        channels.remove('unknown')
    notes = ['视频榜'] if 'V' in item.get('sources', []) or any(isinstance(v,str) and v.startswith(('video:', 'aisa:video')) for v in values) else []
    return dict(platform=platform, channels=channels, platform_info=PLATFORMS[platform],
                channel_details=[dict(CHANNEL_METADATA[key], note=' · '.join(notes) if key == 'aisa' else '') for key in channels])


def annotate_items(root, items):
    """只读 K1 身份，批量查询当前对象的 canonical_key / 旧别名 / URL。"""
    import sqlite3
    import logging
    from contextlib import closing
    from pathlib import Path
    root = Path(root)
    base = root / 'workspace' if (root / 'workspace').is_dir() else root
    path = base / 'clues/clues.db'
    records, candidates = {}, []
    for item in items:
        key = item.get('id', '')
        # 旧 RSS 的 ID 是 url:hash；复用 K1 的规范化器，绝不另写 URL/平台推导。
        if derive_platform(key) == 'web' and 'L' in item.get('sources', []):
            try:
                from clue_store_v2 import canonical
                key = canonical(item, 'linuxdo')[0]
            except (ValueError, TypeError):
                pass
        aliases = list(dict.fromkeys(v for v in (key, item.get('id'), item.get('url') if item.get('kind')!='question_cluster' else None) if isinstance(v,str) and v))
        candidates.append((item, key, aliases))
    keys = list(dict.fromkeys(v for _, _, aliases in candidates for v in aliases))
    if path.is_file() and keys:
        try:
            with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=.05)) as db:
                db.row_factory = sqlite3.Row
                db.execute('BEGIN')
                has_aliases = bool(db.execute("SELECT 1 FROM sqlite_master WHERE name='clue_aliases'").fetchone())
                for start in range(0, len(keys), 400):
                    batch = keys[start:start + 400]
                    marks = ','.join('?' for _ in batch)
                    sql = 'SELECT o.canonical_key AS lookup_key,o.platform,c.channel FROM clue_objects o LEFT JOIN clue_channels c ON c.clue_id=o.id WHERE o.canonical_key IN (' + marks + ')'
                    args = batch
                    if has_aliases:
                        sql += ' UNION ALL SELECT a.alias AS lookup_key,o.platform,c.channel FROM clue_aliases a JOIN clue_objects o ON o.id=a.clue_id LEFT JOIN clue_channels c ON c.clue_id=o.id WHERE a.alias IN (' + marks + ')'
                        args = batch + batch
                    for row in db.execute(sql, args):
                        record = records.setdefault(row['lookup_key'], dict(platform=row['platform'], channels=[]))
                        if row['channel']:
                            record['channels'].append(row['channel'])
                assert db.total_changes == 0
        except (sqlite3.DatabaseError, OSError) as exc:
            logging.getLogger(__name__).warning('K1身份读取降级：%s', type(exc).__name__)
            records = {}
    for item, key, aliases in candidates:
        record = next((records[v] for v in aliases if v in records), None)
        if record is None and key != item.get('id'):
            record = dict(platform=derive_platform(key), channels=[])
        item.update(identity(item, record))
    return items


def parse_filters(query):
    """有界、去重、大小写不敏感；无效输入拒绝而不是静默放宽。"""
    selected = []
    for value in query.get('platform', []):
        if not isinstance(value, str) or len(value) > 200:
            raise ValueError('平台参数无效')
        for key in value.lower().split(','):
            if key not in PLATFORMS:
                raise ValueError('平台参数无效')
            if key not in selected:
                selected.append(key)
    channel = query.get('collection_channel', [''])[0]
    if not isinstance(channel, str) or len(channel) > 50 or channel and channel.lower() not in CHANNELS:
        raise ValueError('采集渠道参数无效')
    legacy = query.get('source', [''])[0]
    if legacy and (not isinstance(legacy, str) or legacy.upper() not in LEGACY_SOURCE_FILTERS):
        raise ValueError('来源参数无效')
    return selected, channel.lower() or LEGACY_SOURCE_FILTERS.get(legacy.upper(), '')


def matches(item, filters, legacy=''):
    platforms, channel = filters
    return (not platforms or item['platform'] in platforms) and (not channel or channel in item['channels']) and (not legacy or legacy.upper() in item.get('sources', []) or legacy.upper() == 'G' and 'grok:radar' in item.get('sources', []))
