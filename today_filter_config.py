"""今日页可维护的显示过滤配置；不改变采集与原始快照。"""
LINUXDO_CATEGORY_BLACKLIST = frozenset({'搞七捻三', '福利羊毛'})
SOURCE_NAMES = {'grok:radar': 'Grok 推特雷达', 'G': 'Grok', 'Grok': 'Grok', 'A': 'AIsa 推特', 'AIsa': 'AIsa 推特',
                'V': '视频榜', 'L': 'linux.do', 'linuxdo': 'linux.do'}

# 旧词逐项分组，依据主控M2裁定：不改采集侧 is_ai_related。
# 值为分组理由；长词延续子串语义，避免再次误杀GrokBot/agentic等派生写法。
TODAY_AI_SUBSTRING_TERMS = {
    'gpt': '沿用产品词根，ChatGPT/GPT版本名',
    'claude': '沿用产品词根，不按词边界收紧',
    'agent': '保留agentic及智能体派生写法',
    'prompt': '提示词术语及派生写法',
    'cursor': '编辑器产品词根',
    'codex': '编程产品词根',
    'skill': '技能术语及复数/派生写法',
    'deepseek': '模型产品长词',
    'qwen': '模型产品词根',
    'gemini': '模型产品长词',
    'openai': '机构产品长词',
    'anthropic': '机构长词',
    'grok': '保留GrokBot/Grokbot复合名',
    'github': '开发平台长词',
    'token': '模型用量术语',
    'lora': '模型微调术语',
    '4090': '沿用显卡型号匹配',
    **{word: '中文旧词，保持原子串语义' for word in (
        '大模型', '模型', '写代码', '编程', '生图', '视频生成', '评测', '推理', '蒸馏', '训练',
        '算力', '显卡', '本地部署', '开源', '独立开发', '出海', '变现', '赚钱', '订阅', '支付',
        '收款', '工作流', '自动化', '部署', '智能体', '多模态')},
}
TODAY_AI_BOUNDARY_TERMS = {
    'ai': '两字母短词会误命中daily/said/email等普通词',
    'api': '短缩写会误命中Capital等普通词',
    'mcp': '三字母缩写，需独立词、混排驼峰整段或已证实复合名',
    'llm': '三字母缩写，需独立词、混排驼峰整段或已证实复合名',
    'repo': '缩写会误命中report等普通词',
}
TODAY_AI_COMPOUNDS = {
    'genai': '10-26 x:2114535980132950057 明确GenAI',
    'xai': '10-26 x:2114691110727553094 明确xAI超算',
    'openai': '已有机构产品名，也由子串安全组保留',
    'aigc': '10-29 x:2115758761919017347 小写aigc创意；短词审计AI正例',
}

# 显示闸门的增量词表，新增短词/模糊产品仍按ASCII边界与语境匹配。
# 来源：H3a M1/M2真实误杀样本；10-30 x:2116032103100088509明确Vibe Coder。
# Muse 有重名，需下面的产品语境规则。
TODAY_AI_TERMS = ('Codex', 'Claude', 'DeepSeek', 'ChatGPT', 'OAI', 'Seedance', 'Higgsfield', 'WorkBuddy',
                  'Buddy One', 'NotebookLM', 'OrcaRouter', 'Tibo', 'thsottiaux', '提示词', 'GPU', 'AIGC',
                  '超级智能', '具身智能', 'Vibe Coder', 'VibeCoder', 'Vibe Coding')
# 边界收紧时保留合法的产品版本名和原有关键词复数，不退回任意子串匹配。
TODAY_AI_VERSIONED_TERMS = frozenset({'codex', 'claude', 'deepseek', 'chatgpt', 'seedance',
                                     'higgsfield', 'workbuddy', 'notebooklm', 'gpt', 'qwen', 'gemini', 'grok', 'lora',
                                     'glm', 'kling', 'minimax'})
TODAY_AI_PLURAL_TERMS = frozenset({'agent', 'skill', 'api', 'token', 'prompt', 'repo'})
TODAY_AI_CONTEXT_PATTERNS = (
    # DS 必须带版本；不把 DSL、ads 或 DS4.1protocol 当作 DeepSeek。
    r'(?<![a-z0-9_])ds[\s-]*v?\d+(?:\.\d+)*(?:\s*(?:pro|flash|lite|air))?(?![a-z0-9_.])',
    # Pro/Plus 是泛词，必须与套餐用量/额度语境同时出现。
    r'(?<![a-z0-9_])(?:pro|plus)(?![a-z0-9_])(?!.{0,48}流量套餐).{0,48}(?:额度|用量|\busage\b|\blimits?\b|\bquota\b|\btiers?\b)',
    r'(?:额度|用量|\busage\b|\blimits?\b|\bquota\b)(?!.{0,48}流量套餐).{0,48}(?<![a-z0-9_])(?:pro|plus)(?![a-z0-9_])',
    # 本轮 50X→100X 的账户讨论：倍数 + 账号 + 用量同时出现。
    r'(?<![a-z0-9_])\d+x(?![a-z0-9_]).{0,64}账号.{0,128}(?:用量|额度)',
    r'(?<![a-z0-9_])notebook(?![a-z0-9_]).{0,64}(?:知识库|教科书|提问)',
    r'(?:知识库|教科书).{0,64}(?<![a-z0-9_])notebook(?![a-z0-9_])',
    # 官方定义：Meta Muse personal AI agent；避免误收同名乐队/普通 muse。
    r'(?<![a-z0-9_])muse(?![a-z0-9_]).{0,64}(?:更新|升级|解决问题|智能体|\bagent\b|\bai\b)',
    # 10-24 x:2113801701132013849：Muse在个人homelab中自主创建播放列表。
    r'(?<![a-z0-9_])muse(?![a-z0-9_]).{0,48}created.{0,32}playlist.{0,128}homelab',
    # 10-26 x:2114670876050460994：Bot自主登录后台、处理验证码。
    r'(?<![a-z0-9_])bot(?![a-z0-9_]).{0,128}(?:登录|验证码).{0,128}(?:不用自己|直接登录|自动|验证码)',
    # 10-24 x:2113829196065845274：与Bot对话提出要求并生成/修改成果页。
    r'(?<![a-z0-9_])bot(?![a-z0-9_]).{0,80}(?:聊天|对话|提要求).{0,80}(?:成果|页面|生成)',
)

# M3：10-24～10-30 51人原始采样证据；新名只用于显示闸门。
# 低歧义类：中文产品名保持子串，英文产品名按边界/版本识别；不按作者身份放行。
TODAY_AI_PRODUCTS = {
    '通义': '主控M3补充裁定：无日常歧义；七天0命中，无噪音样本，允许合成验证',
    '千问': '5条；x:2113842948945412240 明确短剧Skill/智能体产品',
    '智谱': '7条；x:2113958347875708967 与Kimi/MiniMax产品比较',
    'glm': '23条；x:2113828576722096633 DeepSeek/GLM模型额度；短缩写按边界/版本',
    '可灵': '1条；x:2113842948945412240 视频提示词产品',
    'kling': '8条；x:2115668310629200379 Kling4.0平台用量；英文按边界/版本',
    '即梦': '2条；x:2114934754000904273 Dreamina画布生成短片',
    'minimax': '26条；x:2113687016546463957 MiniMax3.1-pro国模跑分；按边界/版本',
}
# 有日常/人名/文学或武术含义，禁止裸名字放行；须同句AI产品语境。
TODAY_AI_CONTEXT_PRODUCTS = {
    'opus': '113条；x:2115434479661560296 Opus5.5视频制作；普通作品含义不裸放，限模型语境或小数版本',
    '海螺': '主控M3补充裁定：七天0命中，无噪音样本；只允许同句AI语境，拒绝海边贝壳',
    'kimi': '86条；x:2115350337179807903 正文@Kimi_Moonshot；模型版本亦可作语境，拒绝F1人名',
    '豆包': '30条；x:2114559061371064705 豆包工作（具体产品名）；拒绝早餐面食',
    '元宝': '1条；x:2113950089412792594 同句DeepSeek/千问问答；拒绝钱币/压岁钱',
    '文心': '2条；x:2114927673894199611 文心Unlimited OCR模型；拒绝文心雕龙文学',
    '混元': '3条；x:2114276991973412872 腾讯混元3D资产；拒绝混元太极武术',
}
TODAY_AI_PRODUCT_CONTEXT = (
    r'(?<![a-z0-9_])(?:ai|llms?|gpt(?:[- ]?\d+(?:\.\d+)*)?|chatgpt|claude|codex|deepseek|qwen|gemini|seedance|glm(?:[- ]?\d+(?:\.\d+)*)?)(?![a-z0-9_])',
    r'人工智能|大模型|模型|智能体|生图|视频生成|生成视频|提示词|推理|算力|上下文|多模态|编程|代码|前端|国模|智谱|千问|字节|腾讯',
)
TODAY_AI_PRODUCT_SPECIFIC_CONTEXT = {
    'opus': (r'(?<![a-z0-9_])opus[\s-]*v?\d+\.\d+(?![a-z0-9_.])',),
    # 版本必须是产品格式，不把F1车号或普通数字当作AI语境。
    'kimi': (r'(?<![a-z0-9_])@kimi_moonshot(?![a-z0-9_])',
             r'(?<![a-z0-9_])kimi[\s-]*(?:k\d+(?:\.\d+)+|\d+(?:\.\d+)+\s+(?:pro|flash|high))(?![a-z0-9_])'),
    # “豆包工作”是本轮真实帖明确出现的产品复合名；不接受“豆包工作餐”。
    '豆包': (r'豆包工作(?=$|[\s，,。！？!?：:；;的、]|(?:台|版|流))',),
    '文心': (r'unlimited\s+ocr',),
    '混元': (r'混元\s*3d(?![a-z0-9_])',),
}
# 通义/海螺为主控M3补充授权的0样本项；前者低歧义，后者仍须AI语境。
# DeepSeek 已在旧安全子串组，96条真实命中，不重复添加。
