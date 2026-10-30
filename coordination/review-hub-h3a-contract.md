# H3a 复核合同（复核窗口 B term_57dd3162）
对象：实施 task_1ee293aab977 + 返修 M1 task_47efe67580c5（实施 A term_02017284），报告 coordination/impl-hub-h3a-report.md（含末尾「返修 M1」节），证据 coordination/evidence/impl-hub-h3a/。原合同 coordination/impl-hub-h3-contract.md 顶部 §3.12 1–8 条 + A 段。
改动文件：以报告 changed-sha256.json 与返修节为准（linuxdo_source.py、linuxdo_report.py、today_filter_config.py、scripts/radar_common.py、today_view.py、interest_channel.py、public/today.{js,html,css}、public/components/item-card.js、tests/test_linuxdo_source.py、tests/test_today_h3a.py、tests/e2e/today_t5.cjs、tests/e2e/today_h3a.cjs 等）。

## 必查
1. SHA 与报告一致；改动不越出合同允许范围及主控批准的扩围（interest_channel.py 三项接线 msg_ee1d5b3d7321；today_t5 mock 改接 fetch_feeds msg_05daead77560；public/today.js renderFiltering 分组 msg_70840aadf533）。
2. 定向测试全过（报告给出的命令）。不跑 Python 全量；UI 全量实施已跑，如需复跑先向主控登记。
3. W1 解析：用 evidence 里的真实 RSS（rss-tag/daily/weekly/develop.json）核对分类取自每个 item 的 category、置顶取 Discourse 命名空间 topicPinned，不是取第一处匹配；黑名单（搞七捻三/福利羊毛）在配置常量里可维护。
4. 闸门：只有一个共用函数 today_relevance_gate；is_ai_related 现有行为未改（对比 git diff 或旧副本，跑旧测试）；兴趣栏只有纯 Grok 条目只标记不过滤，混合来源与其他来源都过闸门。
5. 返修 M1 正例/反例：主控列的 13 条正例（Codex 用量、Pro 额度、50X 用量、DS 4.1pro、CIaude、WorkBuddy、Notebook 知识库、Seedance 2.5、提示词换装、Muse、linux.do「tibo 砍 plus 额度」）应保留；6 条反例（intc 爆仓、张雪直播、万磁王、书法、黄仁勋八卦、德鲁肯米勒/OKX）应仍过滤。自己再造 5 个边界反例（如 ads/DSL 不应命中 DS，普通 "Pro" 购物帖不应命中，Claude 形近字以外的常见词不误判），报告结果。在 /tmp 副本把扩展词表清空，正例测试应报 F。
6. 已过滤面板：按来源计数；AIsa 51 人原始采样单独分组并标「原始采样统计，不属于主列表候选」；被滤条目有标题/来源/原因/原帖链接。
7. 硬性验收：今天三条（亲哥欠钱 10w、被骗的钱追回来了、不限次数的 seedance2.0）不在主列表、在已过滤并显示原因。隔离 Playwright 对真实 8766 只读访问，1440/390 截图存 /tmp/review-hub-h3a/。
8. 内容不足：只用已有数据展开，不新增外网抓取；标记并后排。「不准」累计文件 workspace/today/inaccurate-counts.json 幂等、按来源+分类计数；复核时只在隔离副本或 /tmp 数据上点「不准」，不得往真实累计文件写入。
9. linuxdo 热帖报告：内置 script 任务每 6 小时已登记；CLI 在 /tmp 根目录跑一次生成报告，含链接与回复涨跌，不比较浏览数；节流与 2MiB 限制保留。
9b. 主控补充（看了 M1 结果）：七天比例里只有今天明显下降（31.8%→21.9%），其他日基本不变、10-26 反而 23.7%→27.7%。担心词表只对今天样本调过。请用 tests/h3a_m1_replay.py 取 10-26 与 10-28 两天「M1 后隐藏」各随机 20 条逐条判断是否 AI 相关，列出误杀条目与原因；另列 10-26「M1 前保留、M1 后隐藏」的全部条目（词边界收紧带来的新增隐藏）逐条判断。误杀率 >10% 记 M。
10. W2/W3：测试不写真实 workspace、~/grok-bot-reports、tasks.db；grep 报告、证据、运行记录无 Key（sk-/Bearer/AISA_API_KEY 值）。

## 约束
只读复核，不改正式文件；不重启 8766；隔离 Playwright，禁止 computer use；禁止 launchctl print / ps eww / env / 打印 plist；视频实验室保护边界不动；不提交不推送。
## 交付
coordination/review-hub-h3a-report.md，开头「决定要点」表（≤10 行：结论 通过/不通过、H/M/L 列表、SHA 核对、测试数字、证据路径），并单列「主控需判断的误杀/漏杀条目」，完成后 worker_done。

## 返修 M2 复验节（主控 10-30 14:1x 预写，M2 交付后随复核单一起派 B）
对象：返修 M2（合同 coordination/impl-hub-h3a-m2-spec.md），报告 impl-hub-h3a-report.md 末尾「返修 M2」节。
11. SHA 与 M2 节一致；改动只在 scripts/radar_common.py（today_relevance_gate 及辅助）、today_filter_config.py、tests。
12. 超集：在 /tmp 副本对 10-24～10-30 七天全部条目跑旧 is_ai_related 与新闸门，旧命中而新不命中的条目逐条列出，每条须对应「误命中词」排除表里有样本依据的一项或 URL 剥离；无依据的记 M。
13. 你上一轮列的 10-26 六条误杀全部保留，并已写成正例测试；原 13 正例 + 6 反例 + 你自造的 5 反例继续成立；/tmp 旧副本跑新正例应报 F。
14. 重抽：10-26、10-28 各随机 20 条「M2 后隐藏」逐条判断，误杀率 >10% 记 M；同时抽 20 条「M2 后保留」看有无明显非 AI 漏网（L1 口径）。
15. 真实 8766：三条硬性样例仍在已过滤；已过滤面板计数与实施报告七天比例表一致。
12b. 按主控裁定（impl-hub-h3a-m2-spec.md 末节）核对：a 类（子串安全）对旧行为是超集；b 类（需边界）名单与理由都有七天真实回放依据，抽 30 条的判断可复现；被翻转的旧合成断言只有 chatgptfan、Claudette 两条，daily ads campaign 仍拒绝。另自造 3 个反例：普通英文长句（含 daily/said/email/report/capital）应拒绝；小写 #genai 话题应保留；"OpenAI's new model" 应保留。
9c. 主控已看 M2 的 28 条「M1 前保留、M2 后隐藏」（evidence/impl-hub-h3a/m2/seven-days-newly-hidden.md）：大多是非 AI 内容，以前只是碰巧被 trails、tailscale、URL 随机串里的「ai」命中。疑似 AI 的有 x:2114346487882662303（Atlas Temporal Echo 舞者特效，像生成提示词）、x:2115759808968266130（GEO 出单，可能指生成式引擎优化）；未知的有春日续卷 x:2115896335681323499、#Malfoid 两条。这类关键词闸门无法识别的语义漏判记 L，不记 M，**不要为单个样本补词**。但如果其中有明确的 AI 词被规则漏掉（不是语义推断），记 M。另核对 A 改过的新 M2 验收脚本（intc 金融帖两组都出现）没有放宽断言：原始失败日志 real-first-failure.log 已保留，改后逐组精确核对。

## 返修 M3 复验节（主控 10-30 16:4x 写，派 B）
对象：返修 M3 task_d4221c6ad7c8（实施 A），合同 coordination/impl-hub-h3a-m3-spec.md，报告 impl-hub-h3a-report.md 末尾「返修 M3」节（第 289 行起）及 evidence/impl-hub-h3a/m3/（含 acceptance-amendment.md 补充裁定）。实施自评 0H/0M/2L。
主控裁定（复核以此为准）：驼峰切分——只对大小写混排的英文词切段，需边界类缩写须整段大写命中；全大写词不切分，只按独立词匹配；白名单复合词不分大小写。无日常含义的产品名（通义、千问）七天 0 命中也可加，须注明无噪音样本并有合成正例；有日常含义的词（海螺等）只能带 AI 语境条件加，并要有日常反例。Opus 只在「Opus + 小数版本号」或 AI 语境下保留，裸 Opus 拒绝。

16. SHA 与 M3 节一致；改动只在报告列的 7 个允许源码/测试文件，旧断言 0 改动（对照 M2 副本逐条 diff 测试文件）；清理单文件（tests/e2e/real_write_guard.cjs 等）SHA 未被本单改动。
17. 驼峰与全大写：MCPServer、APIGateway、OpenAIClient、GenAI、xAI、LLMs 保留；RAPIDServer、CAPITALOne、MAILBox、CAPITAL、REPORT、CHAIR 及你上轮 14 项探针全部拒绝。再自造至少 5 个反例（如 RAIL、SAID、DETAIL、PAIRS 全大写；MAILServer、OpenAPIs 这类混排边界），报告结果。/tmp 的 M2 副本跑新正反例应能报 F。
18. 产品名：逐个核对 15 个新增名的七天依据（或 0 样本注明 + 合成正例）；Kimi `x:2115350337179807903`、豆包 `x:2114559061371064705` 现应保留；有日常含义的词各造日常反例（如海螺吃法、元宝/金元宝、豆包点心、即梦/可灵作普通词）应拒绝，带 AI 语境的同词应保留。
19. Opus：Opus 4.x / Opus5.5 等保留；Opus Dei、音乐 opus no.、Opus 编码器等无 AI 语境的拒绝；抽查 A 说的七天 113 条依据是否可复现。
20. 七天回放：在 /tmp 独立重跑固定 2,079 条（截止 2026-10-30T06:31:24Z），核对「M2→M3 新恢复 19、新隐藏 14」两份清单逐条一致；14 条新隐藏逐条判断——明确 AI 内容因规则（非语义推断）被隐藏记 M；AInotes 用户名误命中要说明是否已排除；19 条新恢复里明显非 AI 的记 L。10-26、10-28 各随机 20 条「M3 后隐藏」重抽，误杀率 >10% 记 M。
21. 真实 8766（只读，隔离 Playwright）：三条硬性样例仍在已过滤并显示原因；输入里找不到就记「未知」并引用历史证据，不算失败。已过滤面板计数与实施报告一致。
22. 重启：核对 PID 33302、重启 UTC 2026-10-30T08:36:02Z、重启前全部任务 running=false 且 next_run 剩 565 秒的证据；查调度历史确认 08:30–08:40Z 没有任务被打断。
23. 实施自报 L（检索时输出了仓库模板 plist 的 Label 一行）：确认只是模板标签、不含 Key/路径以外的敏感信息；grep M3 报告与证据无 Key（sk-/Bearer/AISA_API_KEY 值）。
约束补充：跑任何会落证据的脚本前，先把输出目录指到 /tmp/review-hub-h3a-m3/，不得覆盖 coordination 下既有证据文件（上一单曾覆盖 impl-write-p3-screenshots/isolated 6 个文件）；不在真实页面点「不准」或任何写操作；不重启 8766。
交付：在 review-hub-h3a-report.md 末尾追加「返修 M3 复验」节，开头决定要点表（≤10 行），单列「主控需判断的误杀/漏杀条目」，完成后 worker_done。

## 返修 M4 复验节（主控 10-30 17:4x 写，清理返修 1 复核后派 B）
对象：返修 M4 task_96fce4e8b40c（实施 A），合同 coordination/impl-hub-h3a-m4-spec.md，报告 impl-hub-h3a-report.md 末尾「返修 M4」节。实施自评：Python 289、旧隔离浏览器 100、真实浏览器 29 全过；M3 旧副本 5F/0E；你的 82 探针只有 5 例回归恢复、其余 77 不变；冻结 2,079 条 M3→M4 零差异；重启 UTC 09:38:31Z，PID 33302→35891。
黄总已定：即梦/可灵保持无语境产品组（你上轮 L1 不再算问题，照记已知 L 即可）。
24. SHA 与 M4 节一致；改动只在 scripts/radar_common.py、today_filter_config.py 和测试；旧断言 0 改动；没有为五个样本逐个加白名单（读实现确认是统一规则）。
25. 用你上轮的 82 探针独立重跑：OpenAPIs、myMCPs、newLLMs、localREPOs、theirAPIs 转为保留，其余 77 例结果与 M3 一致。再自造至少 5 个：GPUs、myGPTs、HTTPs、newMAILs、CAPITALs、OpenAPIsLab 之类，判断期望是否合理（API/LLM/MCP/GPT 等目标缩写整段 + 复数保留；普通全大写词复数拒绝）。/tmp 的 M3 副本跑新正例应报 F。
26. 七天 2,079 条独立重放 M3→M4 差异（A 报零差异），逐条核对；10-26、10-28 各重抽 20 条「M4 后隐藏」，误杀率 >10% 记 M。
27. 真实 8766（隔离 Playwright 只读）：三条硬性样例仍在已过滤；核对重启 09:38:31Z 前后调度历史无任务被打断。
28. 流程：报告与证据无 plist 内容输出、无 Key；证据没覆盖既有文件。
约束同前（证据输出 /tmp/review-hub-h3a-m4/，不重启，不点写操作）。交付：review-hub-h3a-report.md 末尾追加「返修 M4 复验」节，决定要点表，完成后 worker_done。
