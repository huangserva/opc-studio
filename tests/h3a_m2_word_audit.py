"""M2短词分组依据：七天真实采样，固定种子抽样，不补抓、不写来源。"""
import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import random
import re
import sys
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import today_view as today
from scripts import radar_common as common
from today_filter_config import TODAY_AI_BOUNDARY_TERMS


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--as-of', required=True)
    args = parser.parse_args()
    now = datetime.fromisoformat(args.as_of)
    if now.tzinfo is None:
        parser.error('--as-of 必须带时区')
    day = now.astimezone(today.grok.BEIJING).replace(hour=0, minute=0, second=0, microsecond=0)
    root = Path(__file__).resolve().parents[1]
    pool = {term: {} for term in TODAY_AI_BOUNDARY_TERMS}
    daily = []
    for offset in range(7):
        start = day - timedelta(days=offset)
        end = now if offset == 0 else start + timedelta(days=1) - timedelta(microseconds=1)
        captured = []
        real = today.apply_gate
        def capture(items, *a, **kw):
            captured.extend(items)
            return real(items, *a, **kw)
        with patch.object(today, 'apply_gate', capture):
            today.aisa_full_stats(root, start, end)
        counts = {term: 0 for term in pool}
        for item in captured:
            original = common._today_content_text(item, preserve_case=True)
            low = common._today_normalize(original)
            old_hits = {term for term in common.AI_KEYWORDS if term in low}
            tokens = re.findall(r'[A-Za-z0-9]+', original)
            for term in pool:
                embedded = sorted({t for t in tokens if term in t and len(t) > len(term)})
                independent = re.search(r'(?<![a-z0-9])' + re.escape(term) + r'(?![a-z0-9])', low)
                if old_hits != {term} or not embedded or independent or term.upper() in original:
                    continue
                counts[term] += 1
                pool[term][item['id']] = dict(item, day=start.date().isoformat(),
                    embedded_words=embedded, m2_visible=common.today_relevance_gate(item)['visible'])
        daily.append(dict(day=start.date().isoformat(), count=counts))
    result = dict(as_of=now.isoformat(), definition='去URL正文旧词命中集合恰为该词，且无独立词/词内原文全大写，仅有词内小写命中；包括白名单已恢复项，不以M2隐藏结果筛样', daily=daily, terms={})
    for index, (term, rows) in enumerate(pool.items()):
        ordered = sorted(rows.values(), key=lambda r: r['id'])
        seed = 20261030 + index
        sample = random.Random(seed).sample(ordered, min(30, len(ordered)))
        result['terms'][term] = dict(total=len(ordered), seed=seed, sample_size=len(sample), sample=sample)
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / 'short-word-audit.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({term: {k: row[k] for k in ('total', 'seed', 'sample_size')} for term, row in result['terms'].items()}, ensure_ascii=False))


if __name__ == '__main__':
    main()
