"""Frozen seven-day input: M2/M3 full delta, product evidence and deterministic noise sample."""
import argparse,importlib.util,json,random,re,sys
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts import radar_common as common
import today_filter_config as config

def save(path,data):path.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n')
def main():
 p=argparse.ArgumentParser();p.add_argument('--out',type=Path,required=True);a=p.parse_args();rows=json.loads((a.out/'pool.json').read_text());interests=json.loads((a.out/'interest-topics.json').read_text());days=[]
 assert len(rows)==len({r['id'] for r in rows}), '跨日重复tweet_id'
 assert sorted({r['replay_day'] for r in rows})==['2026-10-'+str(d) for d in range(24,31)]
 def load(name,path):
  spec=importlib.util.spec_from_file_location(name,path);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module
 old=load('h3a_m3_m2_common',a.out/'m2-radar-common.py');old_config=load('h3a_m3_m2_config',a.out/'m2-today-filter-config.py')
 with patch.dict(sys.modules,{'today_filter_config':old_config}):
  for row in rows:assert old.today_relevance_gate(row,interests)==row['m2_gate'],row['id']+' M2基线不一致'
 for day in sorted({r['replay_day'] for r in rows}):
  pool=[r for r in rows if r['replay_day']==day];hidden=[];restored=[]
  for row in pool:
   before=row['m2_gate']['visible'];after=common.today_relevance_gate(row,interests)['visible']
   if before and not after:hidden.append(row)
   if not before and after:restored.append(row)
  before=sum(not r['m2_gate']['visible'] for r in pool);after=sum(not common.today_relevance_gate(r,interests)['visible'] for r in pool)
  days.append(dict(day=day,total=len(pool),m2_hidden=before,m3_hidden=after,m3_ratio=after/len(pool),newly_hidden=hidden,restored=restored))
 products={}
 for index,term in enumerate([*config.TODAY_AI_PRODUCTS,*config.TODAY_AI_CONTEXT_PRODUCTS,'deepseek']):
  hits=[r for r in rows if term in common._today_content_text(r)]
  seed=2026103000+index;sample=random.Random(seed).sample(hits,min(5,len(hits)))
  matched=[]
  for row in hits:
   if term=='deepseek':ok=True
   else:
    direct={term:config.TODAY_AI_PRODUCTS[term]} if term in config.TODAY_AI_PRODUCTS else {}
    contextual={term:config.TODAY_AI_CONTEXT_PRODUCTS[term]} if term in config.TODAY_AI_CONTEXT_PRODUCTS else {}
    with patch.object(config,'TODAY_AI_PRODUCTS',direct),patch.object(config,'TODAY_AI_CONTEXT_PRODUCTS',contextual):ok=common._today_product_related(common._today_content_text(row))
   if ok:matched.append(row['id'])
  products[term]=dict(basis='去URL、NFKC后的正文/已有引用内容，按tweet_id去重；不使用作者身份',total=len(hits),rule_matched_count=len(matched),seed=seed,sample=sample,newly_restored=[r for h in days for r in h['restored'] if r['id'] in matched],already_existing=term=='deepseek',rule=config.TODAY_AI_PRODUCTS.get(term) or config.TODAY_AI_CONTEXT_PRODUCTS.get(term) or '旧安全子串保持')
 save(a.out/'seven-days.json',days);save(a.out/'product-audit.json',products)
 head='| ID | 标题 | 来源 | 原帖 |\n|---|---|---|---|\n'
 def table(pool):return head+'\n'.join('| '+r['id']+' | '+r['title'].replace('|','\\|').replace('\n',' ')+' | AIsa 51人原始采样 | '+r['url']+' |' for r in pool) if pool else '无。'
 (a.out/'seven-days-deltas.md').write_text('# 七天逐项变化：M2→M3\n\n'+'\n\n'.join('## '+h['day']+'\n\n### M2保留→M3隐藏（'+str(len(h['newly_hidden']))+'条）\n\n'+table(h['newly_hidden'])+'\n\n### M2隐藏→M3保留（'+str(len(h['restored']))+'条）\n\n'+table(h['restored']) for h in days)+'\n')
 for h in days:print(h['day'],h['total'],h['m2_hidden'],h['m3_hidden'],'new_hidden',len(h['newly_hidden']),'restored',len(h['restored']))
 print('PRODUCTS',json.dumps({t:dict(total=v['total'],restored=len(v['newly_restored']),sample=len(v['sample'])) for t,v in products.items()},ensure_ascii=False))
if __name__=='__main__':main()
