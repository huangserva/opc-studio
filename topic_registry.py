"""今日三栏唯一方向展示登记。前九项沿用 K1 既有九方向顺序。

只归并展示与筛选；不改报告、K1 topic_assignments 或用户方向文件。
"""
import re
import unicodedata
from collections import Counter

TOPICS = {
 key: dict(key=key,display_name=name,description=description,aliases=aliases)
 for key,name,description,aliases in (
 ('models','新模型发布','开源权重、量化版、MLX 版',('ai_models','新模型发布（开源权重、量化版、MLX 版）')),
 ('local-inference','本地部署与推理','llama.cpp、vLLM、MLX、4090 实测',('本地部署与推理（llama.cpp、vLLM、MLX、4090 实测）','模型与本地部署')),
 ('visual-models','AI 视频与图像模型','H3、ComfyUI 工作流、LoRA',('AI 视频与图像模型（H3、ComfyUI 工作流、LoRA）','AI视频与图像','AIGC视频图像','视频')),
 ('api-engines','新 API / 推理引擎 / 模型网关','Maya、APXInf、sglang、vLLM、网关',('新 API / 推理引擎 / 模型网关（Maya、APXInf、sglang、vLLM、网关）',)),
 ('agent-coding','Agent 编程工具','Claude Code、Codex、MCP',('Agent 编程工具（Claude Code、Codex、MCP）','Agent与Coding','claude_code','Agent')),
 ('skill-security','Agent Skill 安全扫描','SkillSpector 一类',('Agent Skill 安全扫描（SkillSpector 一类）',)),
 ('local-agent-ecosystem','OpenCode / 本地 Agent 桌面插件生态','',('OpenCode/本地Agent桌面',)),
 ('content-automation','AI 内容生产自动化','选题雷达、剪映、批量出片',('AI 内容生产自动化（选题雷达 / 剪映 / 批量出片）',)),
 ('embodied-3d','3D / 游戏 / 硬件 Agent 落地','',('3D/游戏/硬件Agent',)),
 ('open-source','开源项目与资源','',('github_open','资源荟萃')),
 ('indie-business','创业变现与自媒体','',('money_indie','创业变现自媒体')),
 ('overseas-infra','出海与基础设施','',('overseas_infra',)),
 ('ai-frontier','AI 前沿动态','',('前沿快讯',)),
 ('domestic-ai','国产 AI 与替代方案','',('国产替代',)),
 ('dev-optimization','开发与性能调优','',('开发调优',)),
 ('tools-workflows','工具与工作流','',('工具工作流',)),
 ('unclassified','待归类','未登记方向归入此项，原始 topic 仍在记录中保留',('未分类','其他','')),
 )
}

def normalized(value):
 return re.sub(r'\s+','',unicodedata.normalize('NFKC',value)).casefold() if isinstance(value,str) else ''

ALIASES = {normalized(alias):key for key,row in TOPICS.items() for alias in (key,row['display_name'],*row['aliases'])}

def topic_key(value):
 return ALIASES.get(normalized(value),'unclassified')

def topic_options(items):
 counts=Counter(topic_key(item.get('topic')) for item in items)
 return [dict(key=key,display_name=row['display_name'],description=row['description'],count=counts[key]) for key,row in TOPICS.items() if counts[key]]

def matches_topic(item,value):
 return not value or topic_key(item.get('topic'))==topic_key(value)
