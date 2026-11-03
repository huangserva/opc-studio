"""只匹配用户当前兴趣方向；不联网、不读写库、不调用模型。"""
import copy
import re
import unicodedata
from datetime import datetime
import hashlib
import json
from functools import lru_cache
from topic_registry import TOPICS, topic_key, normalized

VERSION = 'interest-rules-7'
TOOL_FIELDS=('in_tools','direction_state','share_count','first_shared_at','classification','tool_version','verification','filtered')
# 方向的词典，只有对应方向在用户设置中启用才生效，不是第二份兴趣偏好。
TERMS = {
 'models': ('开源权重','量化版','MLX 版','GGUF','开源模型','模型发布','open weight'),
 'local-inference': ('llama.cpp','vLLM','MLX','本地部署','本地推理'),
 'visual-models': ('ComfyUI','LoRA','视频模型','图像模型','AI视频','AI 视频','AI图像'),
 'api-engines': ('APXInf','sglang','模型网关','推理引擎'),
 'agent-coding': ('Claude Code','Codex','MCP','Agent 编程','Agent编程','Cursor','Windsurf'),
 'skill-security': ('SkillSpector','Skill 安全','Skill安全','Skill 扫描','Skill扫描'),
 'local-agent-ecosystem': ('OpenCode','本地 Agent 桌面','本地Agent桌面','Agent 桌面插件'),
 'content-automation': ('选题雷达','剪映','批量出片','内容生产自动化','内容自动化'),
 'embodied-3d': ('硬件 Agent','硬件Agent','embodied','具身智能','游戏 Agent','游戏Agent','3D Agent','3D生成','3D 生成'),
}
GENERIC = {'agent','模型','ai','视频','api','工具','工作流','3d','游戏'}
WEAK = {'ai视频','ai图像','剪映'}
TECH = re.compile(r'教程|工作流|部署|推理|开源权重|训练|参数|插件|节点|workflow|tutorial|weights|comfyui|lora',re.I)
PROMO = re.compile(r'注册.{0,5}送|留言.{0,5}送|回帖.{0,5}送|领福利|来领钱|抽奖.{0,8}美?金|新用户.{0,6}(?:刀|美元|美金)|限时福利',re.I)
SUMMARY_TECH = re.compile(r'github\.com|教程|插件|工作流|脚本|webhook|ssh|\bapi\b|\bcli\b|推理引擎|部署|安装命令|开源项目|模型权重',re.I)
RSS_AD = re.compile(r'进群.{0,5}送|评论.{0,5}抽|充值|欢迎.{0,5}合作|分组.{0,8}价格|[0-9.]+[/／][0-9.]+.{0,12}(?:ccmax|codex)',re.I)
LIFE_TITLE = re.compile(r'女朋友|男朋友|谈恋爱|发红包|给小辈|帮我评评理')

def text(value):
 return unicodedata.normalize('NFKC',value).casefold() if isinstance(value,str) else ''

def profile_key(topics):
 return VERSION+':'+hashlib.sha256(json.dumps(topics,ensure_ascii=False).encode()).hexdigest()

def compile_topics(topics):return _compile_topics(tuple(topics))

@lru_cache(maxsize=8)
def _compile_topics(topics):
 result=[];seen=set()
 for index,name in enumerate(topics):
  key=topic_key(name)
  if key=='unclassified':key='custom:'+normalized(name)
  if key in seen:continue
  seen.add(key)
  row=TOPICS.get(key,{})
  words=[name,row.get('display_name',''),*row.get('aliases',()),*TERMS.get(key,())]
  # 新增自定义方向依然可用；只拆用户明确给出的括号专名，不扩大到泛词。
  words.extend(re.split(r'[、,;/｜|]',row.get('description','')))
  if key.startswith('custom:'):
   words.extend(re.split(r'[、,;/｜|]',re.sub(r'.*?[（(](.*)[）)].*',r'\1',name)))
  patterns=[]
  for word in dict.fromkeys(w.strip() for w in words if isinstance(w,str)):
   if len(normalized(word))<3 and normalized(word) not in {'剪映','h3'} or normalized(word) in GENERIC:continue
   escaped=re.escape(text(word)).replace(r'\ ',r'\s*')
   if re.fullmatch(r'[\w. -]+',word,re.ASCII):escaped=r'(?<![a-z0-9_])'+escaped+r'(?![a-z0-9_])'
   patterns.append((word,normalized(word),re.compile(escaped)))
  result.append(dict(key=key,name=row.get('display_name') or name,configured_name=name,weight=len(topics)-index,patterns=patterns))
 return result

def classify(item,directions):
 fields={key:text(item.get(key,'')) for key in ('title','full_text','expanded_text')}
 compact={key:re.sub(r'\s+','',value) for key,value in fields.items()}
 if 'linux.do' in item.get('input_sources',[]) and RSS_AD.search(fields['title']) and not TECH.search(fields['title']):return []
 if 'linux.do' in item.get('input_sources',[]) and LIFE_TITLE.search(fields['title']):
  # 生活帖正文顺带提到 OpenCode/Codex 不算兴趣；确实用工具做事的技术标题仍保留。
  if not any(needle in compact['title'] and pattern.search(fields['title']) for d in directions for _,needle,pattern in d['patterns']):return []
 # AIsa 已整理线索中的产品八卦/单纯额度消息误命中较多，需有具体技术使用线索。
 source_topics=item.get('source_topics') or [item.get('topic','')]
 explicit_direction=any(topic_key(v)==d['key'] or normalized(v)==normalized(d['configured_name']) for v in source_topics for d in directions)
 if 'AIsa 线索摘要' in item.get('input_sources',[]) and not explicit_direction and not SUMMARY_TECH.search(fields['title']+'\n'+fields['full_text']):return []
 matches=[];promotion=bool(PROMO.search(fields['title']))
 for direction in directions:
  explicit=next((v for v in source_topics if topic_key(v)==direction['key'] or normalized(v)==normalized(direction['configured_name'])),None)
  # 规范化来源 topic 优先；“待归类”绝不映射成任意自定义方向。
  if explicit and not promotion:
   matches.append(dict(direction=direction['key'],name=direction['name'],word=explicit,field='topic',weight=direction['weight'],basis='source_topic'));continue
  if promotion:continue
  found=None
  for field,value in compact.items():
   for word,needle,pattern in direction['patterns']:
    if needle not in value:continue
    if not pattern.search(fields[field]):continue
    if needle=='h3' and not re.search(r'视频|图像|模型|video|image|model|comfyui|lora',fields['title']+'\n'+fields['full_text']):continue
    if needle=='maya' and not re.search(r'推理|引擎|deepseek|inference|gguf|量化|apxinf|tps|ngram',fields['title']+'\n'+fields['full_text']):continue
    if item.get('kind')=='video' and normalized(word) in WEAK and not TECH.search(fields['title']+'\n'+fields['full_text']):continue
    found=(word,field);break
   if found:break
  if found:matches.append(dict(direction=direction['key'],name=direction['name'],word=found[0],field=found[1],weight=direction['weight'],basis='text'))
 if not matches and item.get('interest_original'):
  matches=[dict(direction='unclassified',name='待归类',word=item.get('topic') or '来源兴趣候选',field='topic',weight=0,basis='original_interest')]
 return sorted(matches,key=lambda m:(m['basis']!='source_topic',-m['weight']))

def balanced_sort(items,beijing,stamp):
 """保留全部结果，按天、完整性分组；方向加权轮转，避免单一方向占满首屏。"""
 from collections import defaultdict,deque
 days=defaultdict(list)
 for value in items:days[(bool(value['content_insufficient']),stamp(value.get('first_seen_at') or value['fetched_at']).astimezone(beijing).date().toordinal())].append(value)
 output=[]
 for day in sorted(days,key=lambda k:(k[0],-k[1])):
  buckets=defaultdict(list)
  for value in days[day]:buckets[value['interest_matches'][0]['direction']].append(value)
  buckets={key:deque(sorted(values,key=lambda p:(-p['interest_rank']['trust'],-stamp(p.get('first_seen_at') or p['fetched_at']).timestamp(),p['canonical_key']))) for key,values in buckets.items()}
  weights={key:max(p['interest_rank']['direction'] for p in values) for key,values in buckets.items()}
  ordered=sorted(buckets,key=lambda key:(-weights[key],key))
  while any(buckets.values()):
   for key in ordered:
    for _ in range(max(1,min(3,(weights[key]+2)//3))):
     if buckets[key]:output.append(buckets[key].popleft())
 return output

def deduplicate(items):
 """只按 K1 规范身份合并；不以标题相似度猜同文。"""
 analysis_fields=('what_new','why','pattern','outline','my_angle','doable_resource','usable','relevance','author_type','author_verification_type')
 present=lambda v:v is not None and (not isinstance(v,(str,list,dict)) or bool(v.strip() if isinstance(v,str) else v))
 moment=lambda v:datetime.fromisoformat(v.replace('Z','+00:00')).timestamp() if v else float('-inf')
 def order(value):return (2 if value.get('interest_original') else 1 if any(s.startswith('Grok ') for s in value.get('input_sources',[])) else 0,moment(value.get('fetched_at')))
 grouped={}
 for candidate in items:
  key=candidate['canonical_key']
  if key not in grouped:
   grouped[key]=copy.deepcopy(candidate)
   grouped[key]['_analysis_orders']={name:order(candidate) for name in analysis_fields if present(candidate.get(name))}
   grouped[key]['_metrics_time']=moment(candidate.get('fetched_at')) if any(v is not None for v in candidate.get('metrics',{}).values()) else float('-inf')
   continue
  prior=grouped[key]
  for name in ('sources','channels','source_topics','input_sources','input_ids','interest_matches','rich_sources'):
   values=prior.get(name,[])+candidate.get(name,[])
   prior[name]=[v for i,v in enumerate(values) if v not in values[:i]]
  # 完整公开正文优先，然后长正文；不复制私密证据来补全公开帖子。
  if not candidate.get('filtered') and (bool(candidate.get('content_complete')),len(candidate.get('full_text','')))>(bool(prior.get('content_complete')),len(prior.get('full_text',''))):
   for name in ('title','full_text','content_complete','content_insufficient','incomplete_reason','incomplete_reasons','expanded_text','url','kind'):
    if name in candidate:prior[name]=copy.deepcopy(candidate[name])
  if not candidate.get('filtered'):
   author=prior.setdefault('author',{})
   for name in ('name','handle'):
    if not author.get(name) and candidate.get('author',{}).get(name):author[name]=candidate['author'][name]
   for name in analysis_fields:
    if present(candidate.get(name)) and (not present(prior.get(name)) or order(candidate)>prior['_analysis_orders'].get(name,(-1,float('-inf')))):
     prior[name]=copy.deepcopy(candidate[name]);prior['_analysis_orders'][name]=order(candidate)
   # 正文选择与指标观察独立；完整旧正文不能覆盖较新的浏览/粉丝数。
   if any(v is not None for v in candidate.get('metrics',{}).values()) and moment(candidate.get('fetched_at'))>prior['_metrics_time']:
    prior['metrics']=copy.deepcopy(candidate['metrics']);prior['_metrics_time']=moment(candidate.get('fetched_at'))
    prior['author']={**prior.get('author',{}),**{k:v for k,v in candidate.get('author',{}).items() if k=='followers' or v}}
  if candidate.get('interest_original') and not candidate.get('filtered'):
   prior['interest_original']=True
   if candidate.get('tier'):prior['tier']=candidate['tier']
   if candidate.get('kind')=='tool':
    for name in ('id','kind',*TOOL_FIELDS):
     if name in candidate:prior[name]=copy.deepcopy(candidate[name])
  # +08:00 与 +00:00 不能按字面比较；两者都来自已验证的 aware 时间。
  first=min((v for v in (prior.get('first_seen_at') or prior.get('fetched_at'),candidate.get('first_seen_at') or candidate.get('fetched_at')) if v),key=moment)
  if first==(candidate.get('first_seen_at') or candidate.get('fetched_at')) and candidate.get('first_seen_basis'):prior['first_seen_basis']=candidate['first_seen_basis']
  prior['first_seen_at']=first
  if candidate.get('last_seen_at') and moment(candidate['last_seen_at'])>moment(prior.get('last_seen_at')):prior['last_seen_at']=candidate['last_seen_at']
  if moment(candidate.get('fetched_at'))>moment(prior.get('fetched_at')):prior['fetched_at']=candidate['fetched_at']
  if candidate.get('clue_id'):
   prior.update(clue_id=candidate['clue_id'],identity_state='k1')
 for value in grouped.values():
  value['interest_matches']=sorted(value.get('interest_matches',[]),key=lambda m:(m['basis']!='source_topic',-m['weight']))
  value.pop('_analysis_orders',None);value.pop('_metrics_time',None)
 return list(grouped.values())

def options(items,directions):
 rows=[dict(key=d['key'],display_name=d['name'],description=d['configured_name'],count=sum(any(m['direction']==d['key'] for m in p['interest_matches']) for p in items)) for d in directions]
 n=sum(any(m['direction']=='unclassified' for m in p['interest_matches']) for p in items)
 return [r for r in rows if r['count']]+([dict(key='unclassified',display_name='待归类',description='来源兴趣候选尚无当前方向命中',count=n)] if n else [])
