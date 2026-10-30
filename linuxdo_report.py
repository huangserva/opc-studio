"""linux.do 确定性热帖报告 CLI；由工作台 script 任务每六小时调用。"""
from datetime import datetime, timezone
from pathlib import Path
import argparse
import shlex
import sys

import linuxdo_source as source


def schedule_payload(root):
    root = Path(root).resolve()
    interpreter = root.parent / 'article-importer/.venv/bin/python'
    command = [str(interpreter), str(root / 'linuxdo_report.py'), '--root', str(root)]
    return dict(name='linuxdo 热帖报告', kind='script', body=shlex.join(command), cwd=str(root),
                frequency='interval', minutes=360, timeout=180, enabled=True, allow_commands=False,
                result_destination='runs', result_category='technology')


def generate(root, now=None):
    from today_view import apply_gate
    now = now or datetime.now(timezone.utc)
    source.refresh(root, now)
    items, status = source.snapshot(root)
    if not items:
        raise ValueError('无有效 linux.do 快照，未生成报告')
    kept, audit = apply_gate(items)
    selected = sorted(kept, key=lambda p: (p['content_insufficient'], -(p['metrics'].get('replies') or 0), p['topic_id']))[:8]
    lines = ['# linux.do AI 热帖', '', '生成时间：' + now.astimezone().isoformat(),
             '数据时间：' + str(status['updated_at']), 'RSS 不提供浏览数；涨跌仅比较回复数。',
             f'合并 {len(items)} 条，相关性过滤 {audit["total"]} 条，保留 {len(kept)} 条。', '']
    if status['error']:
        lines += ['来源状态：' + status['error'], '']
    if not selected:
        lines += ['本轮没有符合 AI / 兴趣方向的条目。']
    for i, row in enumerate(selected, 1):
        lines += [f'## {i}. ' + row['title'], '', 'https://linux.do/t/' + row['topic_id'],
                  '回复：' + str(row['metrics'].get('replies')) + ' · ' + row.get('reply_change', '新'),
                  '来源：' + ' / '.join(row.get('rss_boards', [])), '', row['full_text'][:500], '']
    folder = source.directory(root) / 'reports'
    folder.mkdir(parents=True, exist_ok=True)
    # 使用北京时间，与工作台日期口径一致。
    from zoneinfo import ZoneInfo
    path = folder / ('ai_hot-' + now.astimezone(ZoneInfo('Asia/Shanghai')).strftime('%Y%m%d-%H%M') + '.md')
    temp = path.with_suffix('.md.tmp')
    temp.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    temp.replace(path)
    return path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, default=Path(__file__).parent)
    args = parser.parse_args()
    try:
        print(generate(args.root))
    except Exception:
        print('linux.do 报告生成失败，请查看来源状态。', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
