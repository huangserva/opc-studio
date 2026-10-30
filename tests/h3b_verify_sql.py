"""真实晨报独立 SQL 对账，仅输出计数与布尔结果，不输出任何线索正文或身份。"""
from contextlib import closing
import json
from pathlib import Path
import re
import sqlite3
import sys

root = Path(__file__).resolve().parents[1]
day = sys.argv[1]
path = root / 'workspace/clue-morning' / (day + '.json')
meta = json.loads(path.read_text())
categories = {'business': '商单', 'technology': '新技术', 'topics': '热点', 'projects': '项目进展', 'other': '其他'}
with closing(sqlite3.connect((root/'workspace/clues/clues.db').as_uri()+'?mode=ro',uri=True)) as db:
    db.execute('ATTACH DATABASE ? AS schedules',((root/'workspace/schedules/tasks.db').as_uri()+'?mode=ro',))
    db.execute('BEGIN')
    prefix = """WITH eligible_ids AS (
      SELECT DISTINCT j.value id FROM schedules.runs r, json_each(r.output,'$.clues') j
      WHERE r.status IN ('succeeded','partial')
      AND json_extract(r.snapshot,'$.kind') IN ('wechat_clues','wechat_radar')
      AND julianday(r.started_at) BETWEEN julianday(:start) AND julianday(:end)
      AND j.type='text'
    ), selected AS (
      SELECT t.id,t.data FROM threads t JOIN eligible_ids i ON t.id=i.id
      WHERE julianday(t.created_at) BETWEEN julianday(:start) AND julianday(:end)
    ) """
    params = dict(start=meta['window_start'],end=meta['window_end'])
    ids = {r[0] for r in db.execute(prefix+'SELECT id FROM selected',params)}
    counts = {}
    for category in categories:
        condition = "EXISTS(SELECT 1 FROM json_each(s.data,'$.categories') c WHERE c.value=:category)"
        if category=='other':
            condition += " OR NOT EXISTS(SELECT 1 FROM json_each(s.data,'$.categories') c WHERE c.value IN ('business','technology','topics','projects','other'))"
        counts[category] = db.execute(prefix+'SELECT count(*) FROM selected s WHERE '+condition,{**params,'category':category}).fetchone()[0]
    assert ids==set(meta['clue_ids']), 'SQL线索集合与元数据不一致'
    assert len(ids)==meta['clue_count'], 'SQL线索总数与元数据不一致'
    assert counts==meta['category_counts'], 'SQL类别计数与元数据不一致'
content = path.with_suffix('.md').read_text()
assert int(re.search(r'^线索共 (\d+) 条',content,re.M)[1])==len(ids), '正文总数不一致'
line = re.search(r'^分类计数：(.*)$',content,re.M)[1]
for category,label in categories.items():
    assert re.search(re.escape(label)+r' '+str(counts[category])+r'(?:、|$)',line), '正文分类计数不一致'
print(json.dumps(dict(sql_unique_clues=len(ids),sql_category_counts=counts,
    metadata_matches=True,markdown_counts_match=True,window_start=meta['window_start'],window_end=meta['window_end']),ensure_ascii=False,indent=2))
