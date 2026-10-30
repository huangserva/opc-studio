"""Read-only frozen replay and independent M3 reviewer probes; no collection or data writes."""
import argparse,importlib.util,json,re,sys
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts import radar_common as common

def load(name,path):
 spec=importlib.util.spec_from_file_location(name,path);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module

def save(path,data):path.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n')

def main():
 p=argparse.ArgumentParser();p.add_argument('--out',type=Path,required=True);a=p.parse_args();root=Path(__file__).resolve().parents[1];prior=root/'coordination/evidence/impl-hub-h3a/m3'
 rows=json.loads((prior/'pool.json').read_text());interests=json.loads((prior/'interest-topics.json').read_text());assert len(rows)==len({r['id'] for r in rows})==2079
 old=load('h3a_m4_m3_common',a.out/'m3-radar-common.py');old_config=load('h3a_m4_m3_config',a.out/'m3-today-filter-config.py')
 with patch.dict(sys.modules,{'today_filter_config':old_config}):before={row['id']:old.today_relevance_gate(row,interests) for row in rows}
 after={row['id']:common.today_relevance_gate(row,interests) for row in rows};days=[];verdicts=[]
 for day in sorted({r['replay_day'] for r in rows}):
  pool=[r for r in rows if r['replay_day']==day];changes=[]
  for row in pool:
   b,c=before[row['id']],after[row['id']];verdicts.append(dict(id=row['id'],day=day,m3=b,m4=c))
   if b!=c:
    words=re.findall(r'[A-Za-z0-9]+',common._today_content_text(row,preserve_case=True));plural=[w for w in words if re.search(r'[a-z].*[A-Z]+s(?=$|[A-Z0-9])',w)]
    assert plural,'非混排缩写复数变化 '+row['id']
    changes.append(dict(id=row['id'],title=row['title'],sources=row['sources'],url=row['url'],m3=b,m4=c,plural_words=plural))
  days.append(dict(day=day,total=len(pool),m3_hidden=sum(not before[r['id']]['visible'] for r in pool),m4_hidden=sum(not after[r['id']]['visible'] for r in pool),changes=changes))
 save(a.out/'seven-days.json',days);save(a.out/'verdicts.json',verdicts)
 probes=json.loads((a.out/'b82-input.json').read_text());results=[]
 for index,probe in enumerate(probes):
  item=dict(title=probe['text'],full_text=probe['text'],sources=['A'],topic='')
  actual=common.today_relevance_gate(item)['visible'];results.append({**probe,'index':index,'m4':actual,'m4_passed':actual==probe['expected']})
 changed=[r for r in results if r['m4']!=r['m3']];assert {r['text'] for r in changed}=={'OpenAPIs','myMCPs','newLLMs','localREPOs','theirAPIs'}
 assert all(r['m4_passed'] for r in changed);assert len(results)==82
 save(a.out/'b82-results.json',dict(total=len(results),passed=sum(r['m4_passed'] for r in results),unchanged=sum(r['m4']==r['m3'] for r in results),changed=changed,known_daily=[r for r in results if not r['m4_passed']],cases=results))
 lines=['# M3→M4 七天逐项差异（冻结2079条，截止2026-10-30T06:31:24.406605Z）']
 for d in days:
  lines+=['','## '+d['day'],'', '| ID | M3可见 | M4可见 | 依据 |','|---|---|---|---|']
  lines += ['| '+c['id']+' | '+str(c['m3']['visible'])+' | '+str(c['m4']['visible'])+' | '+', '.join(c['plural_words'])+' |' for c in d['changes']] or ['无差异。']
 (a.out/'seven-days-deltas.md').write_text('\n'.join(lines)+'\n')
 for d in days:print(d['day'],d['total'],d['m3_hidden'],d['m4_hidden'],'changes',len(d['changes']))
 print('B82',sum(r['m4_passed'] for r in results),'passed;',len(changed),'fixed;',sum(r['m4']==r['m3'] for r in results),'unchanged')
if __name__=='__main__':main()
