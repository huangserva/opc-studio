【H3a 返修 M2：闸门词边界跨日误杀】（同线返修，接 task_47efe67580c5；复核报告 coordination/review-hub-h3a-report.md，0H/1M/2L 不通过）
问题：M1 给原 AI 词加了严格 ASCII 词边界，跨日回放误杀：GenAI、GrokBot/Grokbot、xAI、agentic、超级智能、具身智能等被拒；10-26 新增隐藏 22 条中至少 6 条明确 AI（保守 27.3%）。
主控定的修法（不要再对着单日样本补词）：
1. 闸门正向判断 = 旧 is_ai_related 原样命中（保持原子串行为，不加边界） ∪ 扩展词表（today_filter_config.py）。即新闸门对 AI 的判断必须是旧行为的超集，只有下面第 2 条列出的定点排除例外。
2. 已知子串误命中只做定点排除：URL/短链随机串先剥离再判断；英文单词内部的误命中（如 Capital 中的 api）用一份可维护的「误命中词」排除表处理，每加一条都要有真实样本依据。DS+版本号等新增短词才用词边界。
3. 跨日验证：用 tests/h3a_m1_replay.py 跑 10-24～10-30 七天，交付：每天「M1 前保留、M2 后隐藏」的全部条目逐条列出（应接近 0，若有逐条给理由）；复核报告里的 6 条 10-26 误杀全部写成正例测试；L1（模糊产品语境漏杀）在报告里给处理或说明。
4. 原 13 正例 + 6 反例 + 复核自造 5 反例继续全过；/tmp 旧副本证明新增正例会 F。
5. 改动只限 scripts/radar_common.py（today_relevance_gate 及辅助）、today_filter_config.py、tests；改后重启 8766（先登记）、真实 8766 截图三条硬性样例仍在已过滤；七天比例表重算。L2（接口耗时声明未复现）：报告里删掉或用可复现的方式重测。
6. 交付：impl-hub-h3a-report.md 末尾追加「返修 M2」节并更新决定要点，worker_done。不提交不推送。

【该工人已知弱点与自测（主控 10-30 14:1x 补，按 skill worker-dispatch-profiles）】
- 同线上一轮（M1）：只对着当天样本调词，跨日误杀 GenAI、GrokBot/Grokbot、xAI、agentic、超级智能、具身智能。自测：七天回放每天「M1 前保留、M2 后隐藏」逐条列出，不许只给比例。
- Codex 历史弱点 1，输入边界：大小写、全角/半角、形近字（CIaude）、URL 与短链随机串、英文词内子串（Capital→api）。自测：这五类各造 1 个正例和 1 个反例，列结果。
- Codex 历史弱点 2，整条链路遗漏：闸门改了，但已过滤面板按来源计数、AIsa 51 人原始采样分组、七天比例表没跟着重算。自测：真实 8766 已过滤面板计数与回放脚本数字一致，给截图。
- 改测试断言掩盖回归一律不收：报告里列出所有改动过的旧断言（应为 0）。
- 报告须逐条给以上自测结果；没有逐条结果的交付直接退回，不派复核。

【主控裁定 10-30 14:4x（msg_8033f001f426 → 回复见 Orca）】旧 AI_KEYWORDS 的 "ai"/"api"/"repo" 小写子串会命中 daily、campaign、capital、report，字面超集会让英文噪音整体漏进。改为：关键词分「子串安全类」（中文词 + 英文长词，含 gpt、claude、agent、grok 等，保持旧子串超集）与「需边界类」（至少 ai、api、mcp、llm、repo；命中需独立词，或词内大写形式 GenAI/xAI/OpenAI/AIGC，或在 AI 复合词白名单内），分类与理由写进 today_filter_config.py；b 类每个词用七天真实回放说明「仅因词内小写命中而被旧行为保留」的条数，并随机抽 30 条逐条判断。旧合成断言只授权翻转 chatgptfan 日常、Claudette 的烹饪日记两条（改为保留，报告逐条列出）；daily ads campaign 继续拒绝；其余旧断言改动为 0。
