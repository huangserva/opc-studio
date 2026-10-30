# 中枢期 H3 拆两单（主控 10-30 12:0x，依据 twitter-intel-plan.md v0.8 §3.12，黄总补充指示）
- **H3a = 下文 A 段 + 本节 §3.12 全部要求**，先做（排在 H5 之后、R3-0 之前）。
- **H3b = 下文 B 段（微信线索晨报）**，排在 R3-0 之后。
- 两单都改 today_view.py / public/today.*，串行在同一实施窗口，不并发。

## H3a 追加要求（§3.12，与 A 段冲突处以本节为准）
1. **linux.do 换主源**：主源改为「人工智能」标签 RSS（`/tag/%E4%BA%BA%E5%B7%A5%E6%99%BA%E8%83%BD.rss`）及相关 AI 版块 RSS（Discourse `/c/<版块>/<id>.rss`，先只读实测哪些版块可用并写进报告）；全站日榜/周榜（top.rss?period=daily|weekly）只作补充，必须过两道过滤：分类黑名单（初版：搞七捻三、福利羊毛，做成可维护的配置常量/文件）+ AI 关键词。RSS 条目的分类字段从哪取（`<category>` 等）要实测，W1：取对字段，不要取第一处匹配。
2. **全来源相关性闸门**：今日页所有来源（Grok、AIsa、视频榜、linux.do；兴趣栏里**只有 Grok 条目**只做标记不过滤，理由：Grok 已按兴趣清单挑过；其他来源进兴趣栏一律过闸门）在进列表前统一判断：`radar_common.is_ai_related` + `~/grok-bot-reports/interest-topics.md` 相关度；不相关的不显示。闸门是一个函数，所有来源共用，便于以后调。
3. **「已过滤 N 条」**：**按来源分别给出条数**（Grok / AIsa 推特 / 视频榜 / linux.do …），尤其单列「AIsa 51 人全量推文」被滤掉多少条、占其总数比例（黄总 10-30 补充第 5 点，作为 4 期是否停 AIsa 推特采集的参考；报告里给今天及能回放的最近几天数字）。今日页顶部显示，点开看到被滤掉的条目（标题、来源、被滤原因：分类黑名单 / 非 AI / 内容不足…），方便发现误杀；可从这里点开原帖。
4. **信息量太低**：正文基本只有链接的条目（例如 Grok 那条「无语凝噎」+ 两个短链接），尝试展开引用帖或链接卡片（已有数据里有就用，不新增外网抓取）；展不开的标「内容不足」并排到后面。
5. **「不准」反哺**：今日页对某条点「不准」时，按 来源 + 分类 累计计数，落到工作台本地（写明位置），并提供只读接口或文件给将来的周复盘读；不自动改黑名单。
6. **验收（硬性）**：今天这 3 条——「亲哥欠钱 10w 怎么办」「被骗的钱追回来了」（搞七捻三）、「不限次数的 seedance2.0」（福利羊毛）——不再出现在今日页列表，且出现在「已过滤」里并显示原因；用隔离 Playwright 对真实 8766 截图（1440/390）。另给：过滤前后各来源条数对照；被闸门滤掉的非 linux.do 条目逐条列出（标题+原因），由主控判断有无误杀。
7. **允许改动（H3a）**：linuxdo_source.py、radar_common.py（只可新增闸门所需函数，不改 is_ai_related 现有行为，若必须改先问主控）、today_view.py、public/today.*、public/components/item-card.js（仅「内容不足」标记，如需要）、新增过滤配置文件、tests 与 e2e。A 段原「每 6 小时报告 + 调度任务」保留。今日页现有 e2e 断言如因条数变化需改，先问主控并给 diff（W4 不放宽）。
8. 基线更新：以主控派单时通知的 H1 收口后全量数字为准。
---

# 中枢期 H3：linuxdo 热帖监控与微信线索晨报收编（实施合同，H1 收口后做）

## 提示词来源
主控已按黄总授权只读导出：coordination/workbuddy-prompts/export-linuxdo-ai-hot.md、export-wechat-clue-morning.md（不要再读 ~/.workbuddy-ai/）。注意原提示词里的仓库路径是旧位置 ~/Documents/Codex/2026-10-01/...，收编时一律改用当前仓库。

## A. linuxdo：与现有 linuxdo_source.py 合并成一个来源
- 原 WorkBuddy 版用 top.json，已被 Cloudflare 403；主控实测 `https://linux.do/top.rss?period=weekly` 与 `https://linux.do/tag/%E4%BA%BA%E5%B7%A5%E6%99%BA%E8%83%BD.rss` 直连 200、各 30 条。
- 扩展 linuxdo_source.py：在现有每日 RSS 之外加每周热门与「人工智能」标签 RSS，按话题 id 合并去重、跳过置顶；条目标注来自哪个榜（日榜/周榜/AI 标签）。沿用 T5 的安全与节流（curl 参数、2 MiB、失败 10 分钟退避、成功 1 小时）。
- 涨跌对比：保存上一轮快照，对每个话题给出回复数变化（↑+N / 新 / —），RSS 无浏览数，报告写明不比浏览。
- 每 6 小时生成一份报告 `workspace/linuxdo/reports/ai_hot-<YYYYMMDD-HHMM>.md`（最热 5–8 条 AI 相关，每条附 https://linux.do/t/<id> 链接与涨跌），注册为内置调度任务「linuxdo 热帖报告」（kind=script，每 6 小时，调用一个 CLI 入口），确定性代码，不需要大模型。今日页 linux.do 来源仍只有一个。
- 对照：只读 `~/Documents/linuxdo-ai-hot/data/topics.json`（10-30 09:47 最近一次）与 `reports/ai_hot.md`，比较话题重合率与报告条目。

## B. 微信线索晨报：内置 Pi 任务 + 落文件
- 新建内置调度任务「微信线索晨报」（kind=pi，每天 08:30），提示词按导出版改写：数据源为当前仓库 workspace/schedules/tasks.db 的 runs（最近 24 小时 succeeded/partial 的 clues）与 workspace/clues/clues.db；按类别分组（商单最前，标发言人与合作需求），最后一句当日判断；无新线索写「今日无新增」不编造。
- **必须落文件**：`workspace/clue-morning/<YYYY-MM-DD>.md`（与同名 .json 元数据：线索条数、各类别计数、生成时间），供 H1 心跳登记（把它加入外部/内置总览）。
- 今日页显示当天晨报入口（顶部折叠块或来源状态里一项，点开看全文），不和爆款、兴趣混排。
- 对照：用 clues.db 直接 SQL 核对晨报里的线索条数与各类别计数一致。

## 允许改动
linuxdo_source.py、新建 linuxdo 报告 CLI 与晨报相关模块、today_view.py 与 public/today.*（晨报入口；以 1 期收口版本为基线）、external_tasks 登记（H1 产物）、tests。不改 server.py（除非必须加一行接线，先问主控）。

## 验收与连续 3 天
- 两个任务各在工作台「立即运行」一次成功、运行记录可查；隔离 Playwright 看今日页晨报入口与 linux.do 周榜/AI 标签条目。
- 交付：coordination/impl-hub-h3-report.md。之后连续 3 天（自然日）的运行记录由主控巡检收集，不需要工人等待。
## 通用
- 派单：主控 term_b884acc7（run_17dc13f37a10）；实施 Codex term_02017284；复核 Codex term_57dd3162（不同窗口）。
- 仓库 /Users/huangzongning/development/article-studio；Python ../article-importer/.venv/bin/python。8766 重启 `launchctl kickstart -k gui/$(id -u)/com.servasyy.article-studio`；查 PID 只用 `launchctl list | awk '$3=="com.servasyy.article-studio"{print $1}'`。**禁止** launchctl print / ps eww / env / cat 或 plutil 打印任何 plist 内容（会把 AISA_API_KEY 打进输出）；需要确认 plist 结构时只用 `plutil -extract <键> raw` 取**非密钥**键，或 `plutil -lint`。
- 依据：coordination/twitter-intel-plan.md v0.4 第 3.7 节与第 4 节「中枢期」；coordination/twitter-intel-handoff-r2.md B 段。
- 约束：不动 Grok Bot 设置；不碰飞书；不停推特 AIsa 采集（com.servasyy.radar-monitor 不动）；WorkBuddy（~/.workbuddy-ai/）只读不写，本单不需要读；视频实验室保护边界（public/lab-test.*、generation/*、server.py 视频/generation 路由、tests/test_*lab*）不动。
- Codex 已知弱点（逐条自测并在报告给结果）：W1 解析取错位置（T5 教训：正则取第一处）；W2 测试 mock 泄漏（patch 一律 addCleanup/上下文管理器，测试不写真实 ~/grok-bot-reports、~/Library/LaunchAgents、真实 tasks.db）；W3 凭证外露（日志、报告、任务库、运行记录里不得出现 Key；用 grep 自证）。
- 全量 Python 与 UI 跑前向主控登记。基线：Python 以 1 期交付时数字为准（约 3612 项 0F/1E/7S，E=受保护 video_lab），UI 723 项 719P/3F/1S（3F=受保护 lab-test），不得新增失败。
- 浏览器只用隔离 Playwright（独立 profile；随机端口或对 8766 只读访问），禁止 computer use，不操作黄总真实桌面或 Chrome。不提交、不推送。
- 报告开头「决定要点」表（≤10 行：结论、H/M/L 自查、改动文件与 SHA256、测试数字、新 PID、证据路径），完成后 worker_done。
