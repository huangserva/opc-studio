"""Read-only live/offline parity after an authorized restart; disables background refresh offline."""
import argparse,json,re,sys,time,urllib.request
from datetime import datetime,timezone
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import today_view as today

def main():
 p=argparse.ArgumentParser();p.add_argument('--out',type=Path,required=True);a=p.parse_args();root=Path(__file__).resolve().parents[1]
 # Wait only for a GET-triggered source refresh; no collector or scheduler action.
 for _ in range(40):
  ready=json.load(urllib.request.urlopen('http://127.0.0.1:8766/api/today',timeout=30))
  if not ready.get('sources',{}).get('L',{}).get('refreshing'):break
  time.sleep(.5)
 now=datetime.now(timezone.utc)
 with patch.object(today.linuxdo,'refresh_background'),patch.object(today,'morning_snapshot',return_value={}):
  offline=today.page_view(root,now=now)
  candidates,_=today.snapshot(root,now)
 actual=json.load(urllib.request.urlopen('http://127.0.0.1:8766/api/today',timeout=30));safe={k:v for k,v in actual.items() if k!='clue_morning'}
 assert offline['filtering']['by_source']==safe['filtering']['by_source'], '来源计数实时输入变化或旧代码未重启'
 assert all(offline['filtering']['aisa_all'][k]==safe['filtering']['aisa_all'][k] for k in ['total','visible','filtered','ratio']), '原始51人计数不符'
 a.out.mkdir(parents=True,exist_ok=True)
 patterns=[r'亲哥欠钱\s*10w',r'被.*骗.*钱.*追回',r'不限次数的\s*seedance2\.0'];cutoff=now.astimezone(today.grok.BEIJING).replace(hour=0,minute=0,second=0,microsecond=0)
 hard=[dict(pattern=pattern,rows=[{k:row.get(k) for k in ['id','title','sources','category','topic','published_at']} for row in candidates if 'L' in row.get('sources',[]) and today.grok.stamp(row.get('published_at')) and cutoff<=today.grok.stamp(row['published_at'])<=now and re.search(pattern,row['title'],re.I)]) for pattern in patterns]
 (a.out/'hard-samples-input.json').write_text(json.dumps(hard,ensure_ascii=False,indent=2)+'\n')
 (a.out/'today-after.json').write_text(json.dumps({k:v for k,v in offline.items() if k!='clue_morning'},ensure_ascii=False,indent=2)+'\n');(a.out/'real-today.json').write_text(json.dumps(safe,ensure_ascii=False,indent=2)+'\n')
 result=dict(at=now.isoformat(),by_source=safe['filtering']['by_source'],aisa=safe['filtering']['aisa_all'],equal=True)
 (a.out/'live-parity.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n');print(json.dumps(result,ensure_ascii=False))
if __name__=='__main__':main()
