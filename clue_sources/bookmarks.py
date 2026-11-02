"""X bookmarks: folders are multi-valued edges; topic suggestions never confirm themselves."""
from clue_sources import grok
from clue_store_v2 import safe_text
records = grok.records


def parse(row, meta, source='bookmarks'):
    item = grok.parse(row, meta, source)
    folders = row.get('folders') or []
    if not isinstance(folders, list) or any(not isinstance(v, str) for v in folders):
        raise ValueError('invalid_folders')
    item['folders'] = list(dict.fromkeys(safe_text(x) for x in folders))
    item['suggested_topic'] = safe_text(row.get('suggested_topic'))
    return item
