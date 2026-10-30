#!/usr/bin/env python3
"""只读素材库，将收藏/已使用条目的最小元数据原子导出给 Grok。"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import stat
import sys
import tempfile
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import grok_bot_radar
from platform_registry import derive_platform

BEIJING = timezone(timedelta(hours=8))


def string_list(value):
    """JSON 列表/字符串转为字符串列表；无效 JSON 与非字符串元素不导出。"""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return []
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    return [entry for entry in value if isinstance(entry, str) and entry.strip()]


def saved_time(value):
    # 与 Grok 既有日期口径一致：无时区的历史时间按北京时间处理。
    try:
        moment = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except (ValueError, TypeError, AttributeError):
        raise ValueError('素材存入时间无效，未覆盖已有导出') from None
    return moment.replace(tzinfo=BEIJING) if moment.tzinfo is None else moment


def read_items(db_path):
    uri = Path(db_path).resolve().as_uri() + '?mode=ro'
    with closing(sqlite3.connect(uri, uri=True, timeout=8)) as db:
        db.row_factory = sqlite3.Row
        rows = db.execute("""SELECT id,title,url,source,sources_json,tags_json,created_at
                             FROM items WHERE status IN ('kept','used')
                             AND COALESCE(trashed_at, '') = ''""")
        items = []
        for row in rows:
            sources = [row['source']] if isinstance(row['source'], str) and row['source'].strip() else []
            for source in string_list(row['sources_json']):
                if source not in sources:
                    sources.append(source)
            # 字段白名单：不读取正文、摘要、评论或笔记。
            items.append({
                'id': row['id'], 'title': row['title'], 'url': row['url'],
                'platform': derive_platform(row['id']), 'source': sources,
                'tags': string_list(row['tags_json']),
                'saved_at': saved_time(row['created_at']).isoformat(),
            })
    items.sort(key=lambda item: (datetime.fromisoformat(item['saved_at']), item['id']), reverse=True)
    return items


def atomic_write(path, document):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        mode = stat.S_IMODE(path.stat().st_mode)
    except FileNotFoundError:
        mode = 0o644
    fd, temporary = tempfile.mkstemp(prefix='.library-export-', suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            os.fchmod(stream.fileno(), mode)
            json.dump(document, stream, ensure_ascii=False, allow_nan=False, indent=2)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def export_library(db_path=None, directory=None):
    items = read_items(db_path if db_path is not None else ROOT / 'workspace/items/items.db')
    document = {'generated_at': datetime.now(timezone.utc).isoformat(), 'count': len(items), 'items': items}
    destination = (Path(directory) if directory is not None else grok_bot_radar.reports_dir()) / 'library-export.json'
    atomic_write(destination, document)
    return document


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', type=Path, help='只读 items.db 路径')
    parser.add_argument('--reports-dir', type=Path, help='导出目录（默认 Grok 报告目录）')
    args = parser.parse_args()
    try:
        document = export_library(args.db, args.reports_dir)
    except (OSError, sqlite3.Error, ValueError):
        # 日志只报固定说明，不打印可能携带隐私或凭据的异常内容。
        print('素材库导出失败，请检查数据库、存入时间和输出目录；已有文件保留。', file=sys.stderr)
        return 1
    print(f"素材库导出成功：{document['count']} 条。")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
