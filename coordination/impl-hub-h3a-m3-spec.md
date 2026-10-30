# H3a 返修 M3（实施窗口 A term_02017284，复核 B term_57dd3162）

依据：B 的 M2 复验 `coordination/review-hub-h3a-report.md`「返修 M2 复验」节（第 151 行起，0H/1M/2L，msg_e2d2bb18230f），以及主控裁定 msg_7b122f2c9d98、msg_bc53b8b450c0。M2 原合同 `coordination/impl-hub-h3a-m2-spec.md` 和末节裁定继续有效，本单只改下面两项。
派工时机：A 交付素材库清理单（task_1dc1233a6d12）之后。

## 1. 修 M：全大写普通词误放
- 现状：需边界类（ai、api、mcp、llm、repo）的「词内大写」实现成 `term.upper() in original`，CAPITAL→API、REPORT→REPO、CHAIR/MAIL/RAIN/PAID→AI 被放行（B 实跑 14 项 8F，证据 `/tmp/review-hub-h3a-m2/uppercase-test.log`、`uppercase-extra-test.log`）。
- 口径（主控裁定）：词内大写只指**大小写混排**，目标词大写且紧挨小写字母（GenAI、xAI、OpenAI's 这类）；全大写单词里碰巧含 AI/API/REPO 不算。全大写文本只在 AI、API 等作为独立词时命中（AI AGENT、NEW API 应保留）。白名单复合词（genai/xai/openai/aigc）不区分大小写，OPENAI 应保留。
- 测试：B 的 14 项全部写进测试并通过；再补整句大写的普通新闻标题反例、混排正例各 3 条。

## 2. 补常见 AI 产品名（黄总 10-30 决定放进 M3）
- 起因：Kimi 帖 `x:2115350337179807903`、豆包帖 `x:2114559061371064705` 在旧版、M1、M2 都被隐藏。
- 候选（不限于）：Kimi、豆包、通义/千问、文心、元宝、智谱/GLM、可灵/Kling、即梦、海螺、混元、MiniMax、DeepSeek（已有的不重复加）。
- 每个词必须有 10-24～10-30 七天真实回放依据：命中条数、新恢复的条目逐条列出、抽 5 条看噪音。有日常含义、容易误命中的词（例如 豆包=面食、元宝=钱、Kimi=人名如 Kimi Räikkönen、海螺=贝壳）不能裸加：要么放进需边界类并加 AI 语境条件（同句出现 AI/模型/大模型/字节/腾讯/@官方号 等），要么不加并写明理由。每个词归哪一类、为什么，写进 today_filter_config.py 注释和报告。
- 测试：两条真实样例恢复（正例）；每个有歧义的词至少 1 个日常反例（豆沙包早餐、压岁钱元宝、F1 车手 Kimi、海边捡海螺）必须拒绝；/tmp 的 M2 副本跑新正例应报 F。

## 允许改动
`scripts/radar_common.py`（仅 today_relevance_gate 及辅助）、`today_filter_config.py`、`tests/test_today_h3a_m*.py`、`tests/fixtures/today_h3a_m*.json`、`tests/h3a_m*_*.py`、`tests/e2e/today_h3a_m2.cjs`（或新建 m3 版）、实施报告和证据。不碰素材库清理单的 E2E 文件（5 个脚本、real_write_guard 等）。旧断言改动应为 0；若必须改，逐条列出并说明。

## 重启
需要重启 8766 时先发 question 登记：所有启用任务 running=false，离最近一次 next_run ≥5 分钟才重启；错过就等下一个空档，不中断任何任务。

## 验收
- Python 定向全绿（M2 的 126 项 + 新增）；旧隔离浏览器 56+22+22 照常通过。
- 七天回放：与 M2 相比新恢复、新隐藏逐条列出，更新七天隐藏比例表。
- 真实 8766：已过滤面板计数与回放一致；三条硬性样例若仍在当前输入里，确认仍被过滤，不在就写明并保留证据。
- 浏览器只用隔离 Playwright，禁止 computer use；不提交不推送。

## 该工人已知弱点与自测（skill worker-dispatch-profiles + 同线前两轮反例）
- 同线 M1：只对着当天样本调词，造成跨日误杀。自测：七天每天「M2 后保留、M3 后隐藏」和「M2 后隐藏、M3 后保留」逐条列出，不许只给比例。
- 同线 M2：按口径字面实现，没想反例（`term.upper() in original`）。自测：需边界类每个词各给 3 个全大写反例、2 个混排正例，列结果。
- Codex 输入边界：大小写、全角/半角、形近字、URL/短链随机串、词内子串、中文日常词。自测：每类 1 正 1 反，列结果。
- Codex 整条链路遗漏：已过滤面板按来源计数、AIsa 51 人原始组、七天比例表都要跟着重算。自测：真实面板计数与回放脚本数字一致，给截图。
- 报告须逐条给以上自测结果；没有逐条结果的交付直接退回，不派复核。

## 交付
在 `coordination/impl-hub-h3a-report.md` 末尾追加「返修 M3」节，节首「决定要点」表（≤10 行）。完成后 worker_done。
