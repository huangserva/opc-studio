"""只读回放 H3a M1 前后过滤结果；不启动采集、不写真实来源或反馈。"""
import argparse
from datetime import datetime, timedelta, timezone
import importlib.util
import json
from pathlib import Path
import sys
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import today_view as today
import scripts.radar_common as common
from today_filter_config import SOURCE_NAMES


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def markdown_rows(rows):
    return '\n'.join('| ' + str(r.get('id', '')).replace('|', '\\|') + ' | ' + r['title'].replace('|', '\\|').replace('\n', ' ') +
                     ' | ' + ' / '.join(r['sources']) + ' | ' + r.get('reason', '').replace('|', '\\|') + ' | ' + r.get('url', '') + ' |' for r in rows)


def exclusion_reason(item):
    """逐条解释旧闸门保留、新闸门隐藏的差异，不把未知误当成正确排除。"""
    import re
    import today_filter_config as config
    text = common._today_content_text(item)
    tokens = re.findall(r'[a-z0-9]+', text)
    embedded = {term: sorted({word for word in tokens if term in word and word != term})
                for term in config.TODAY_AI_BOUNDARY_TERMS if term in text}
    embedded = {term: words for term, words in embedded.items() if words}
    if embedded:
        return '短词仅词内小写命中：' + '；'.join(term + '→' + '、'.join(words) for term, words in embedded.items()) + '；无独立词/原文大写/已证实复合词命中'
    raw = '\n'.join(str(item.get(k) or '') for k in ('title', 'full_text', 'expanded_text'))
    if re.search(r'https?://', common._today_normalize(raw)):
        return 'URL/短链剥离后正文未命中；旧判断由URL/链接字符命中'
    return '待人工核查：未归入已授权的URL/定点排除'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--old-gate', type=Path, required=True)
    parser.add_argument('--as-of', help='固定回放时刻（带时区ISO时间），用于跨版本复现')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    args.out.mkdir(parents=True, exist_ok=True)
    spec = importlib.util.spec_from_file_location('h3a_m1_old', args.old_gate)
    old = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(old)
    now = datetime.fromisoformat(args.as_of) if args.as_of else datetime.now(timezone.utc)
    if now.tzinfo is None:
        parser.error('--as-of 必须包含时区')
    with patch.object(today.linuxdo, 'refresh_background'):
        with patch.object(common, 'today_relevance_gate', old.today_relevance_gate):
            before = today.page_view(root, now=now)
        after = today.page_view(root, now=now)
    before_hidden = {r['id']: r for r in before['filtering']['items'] if r['hidden']}
    after_hidden = {r['id']: r for r in after['filtering']['items'] if r['hidden']}
    restored = [dict(r, reason='M1：原相关性判定隐藏 → 保留；原始采样统计项不等同于主列表补入')
                for key, r in before_hidden.items() if key not in after_hidden]
    newly_hidden = [r for key, r in after_hidden.items() if key not in before_hidden]
    excluded_non_linux = [r for r in after_hidden.values() if 'linux.do' not in r['sources']]
    history = []
    day = now.astimezone(today.grok.BEIJING).replace(hour=0, minute=0, second=0, microsecond=0)
    for i in range(7):
        start = day - timedelta(days=i)
        end = now if i == 0 else start + timedelta(days=1) - timedelta(microseconds=1)
        captured = []
        real_apply = today.apply_gate
        def capture(items, *a, **kw):
            captured.extend(items)
            return real_apply(items, *a, **kw)
        with patch.object(common, 'today_relevance_gate', old.today_relevance_gate), patch.object(today, 'apply_gate', capture):
            prev, previous_audit = today.aisa_full_stats(root, start, end)
        new, current_audit = today.aisa_full_stats(root, start, end)
        old_hidden = {r['id'] for r in previous_audit if r['hidden']}
        new_hidden = {r['id'] for r in current_audit if r['hidden']}
        changes = [dict(r, sources=['AIsa 51 人全量推文'], reason=exclusion_reason(r))
                   for r in captured if r['id'] in new_hidden - old_hidden]
        recovered = [r['id'] for r in captured if r['id'] in old_hidden - new_hidden]
        history.append({'day': start.date().isoformat(), 'before': prev, 'after': new,
                        'window_start': start.isoformat(), 'window_end': end.isoformat(),
                        'newly_hidden': changes, 'restored_ids': recovered})
    # 列表输入冻结后对比主列表真正恢复/隐藏的项目，避免混淆原始采样统计。
    items, _ = today.snapshot(root, now)
    items = [p for p in items if today.grok.stamp(p.get('published_at')) and day <= today.grok.stamp(p['published_at']) <= now]
    with patch.object(common, 'today_relevance_gate', old.today_relevance_gate):
        old_main, _ = today.apply_gate(items)
    new_main, _ = today.apply_gate(items)
    old_ids, new_ids = {r['id'] for r in old_main}, {r['id'] for r in new_main}
    actual_restored = [{'id': r['id'], 'title': r['title'], 'sources': [SOURCE_NAMES.get(s, s) for s in r['sources']], 'url': r['url'], 'reason': 'M1：主列表实际新增展示'}
                       for r in new_main if r['id'] not in old_ids]
    actual_hidden = [{'id': r['id'], 'title': r['title'], 'sources': [SOURCE_NAMES.get(s, s) for s in r['sources']], 'url': r['url'], 'reason': 'M1：主列表实际改为隐藏'}
                     for r in old_main if r['id'] not in new_ids]
    # H3b晨报是独立产品内容，本单回放证据不复制微信正文。
    write_json(args.out / 'today-before.json', {k: v for k, v in before.items() if k != 'clue_morning'})
    write_json(args.out / 'today-after.json', {k: v for k, v in after.items() if k != 'clue_morning'})
    write_json(args.out / 'seven-days.json', history)
    head = '| id | 标题 | 来源 | 原因 | 原帖 |\n|---|---|---|---|---|\n'
    (args.out / 'seven-days-newly-hidden.md').write_text(
        '# 七天：M1前保留、当前闸门隐藏（逐条，不用净差代替）\n\n' +
        '\n\n'.join('## ' + h['day'] + '（' + str(len(h['newly_hidden'])) + '条）\n\n' +
                    (head + markdown_rows(h['newly_hidden']) if h['newly_hidden'] else '无。') for h in history) + '\n')
    write_json(args.out / 'restored.json', {'evaluated_at': now.isoformat(), 'restored_relevance': restored, 'newly_hidden_relevance': newly_hidden,
                                         'actual_main_restored': actual_restored, 'actual_main_hidden': actual_hidden})
    head = '| id | 标题 | 来源 | 原因 | 原帖 |\n|---|---|---|---|---|\n'
    (args.out / 'non-linuxdo-filtered.md').write_text('# M1 后非 linux.do 隐藏清单\n\n' + head + markdown_rows(excluded_non_linux) + '\n')
    raw_restored = [r for r in restored if 'AIsa 51 人全量推文' in r['sources']]
    (args.out / 'restored.md').write_text('# M1 由隐藏改为相关性保留\n\n原始采样统计与今日主列表分开列出，避免把统计保留冒充列表展示。\n\n## 原始采样统计：由隐藏改为保留\n\n' + head + markdown_rows(raw_restored) +
        '\n\n## 主列表实际新增展示\n\n' + head + markdown_rows(actual_restored) + '\n\n## 词边界修正后新增隐藏\n\n' + head + markdown_rows(newly_hidden) + '\n')
    print(json.dumps({'evaluated_at': now.isoformat(), 'sources_before': before['filtering']['by_source'], 'sources_after': after['filtering']['by_source'],
                      'aisa_before': before['filtering']['aisa_all'], 'aisa_after': after['filtering']['aisa_all'],
                      'restored': len(restored), 'non_linux_hidden': len(excluded_non_linux), 'newly_hidden': len(newly_hidden)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
