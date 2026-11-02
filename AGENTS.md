# AGENTS.md

## 语言
- 和黄总的所有交流一律用中文：进度汇报、结论、提问、选项、按钮文字。
- 用提问工具（AskUser 等）时，问题、选项、标签全部写中文，不得用英文。
- 代码、命令、文件名、接口名保持原样，不翻译。

## 验收
- 验收必须覆盖真实用户路径：在真实浏览器里端到端操作，不能只靠单元测试。
- 工人复核/验收只用隔离 Playwright（独立 profile、/tmp 副本或随机端口），不得用 computer use 操作黄总的真实桌面或 Chrome（黄总 2026-10-13：曾因此抢占用户界面并误导航到 Google）。派工合同须写明这一条。
- 后端代码改动后必须确认服务已重启并加载新代码，再做浏览器验收。
- Python 测试用 `../article-importer/.venv/bin/python`。

## 主控只协调（黄总 2026-10-11）
主控（协调者/Joy）只做协调和调度，不得大量参与具体工作，保住限额额度：
- 不亲自改代码：哪怕 2 个字符的静态改动也派给工人（优先 Pi，token 不限）。
- 不亲自复跑测试、不做全面验收。所有复核由 Codex 做；主控只对最重要的部分抽检：抽检发现问题，主控再做全面复核；抽检无问题，不做全检。
- 工人报告必须自带「决定要点」表（≤10 行：结论【通过/不通过】、H/M/L、改动文件与 SHA、测试数字）；主控只读要点，报告全文留档不进主控上下文。
- 主控会话上下文过半（或每半天）即 `/new` 开新会话；状态靠 `coordination/work-queue.md` 和各 contract/decision 文件延续，不靠会话记忆。
- `coordination/work-queue.md` 已完成项及时移入归档文件（如 `work-queue-archive.md`），活跃队列保持 200 行以内。
- 收消息只走 `orca orchestration check` 拿摘要，不在主控上下文里长篇复述报告全文。
- 需要黄总人工复核的事项不因主控复核减少而省略：该给黄总看的验收结论照常给。
- **关键结论主控亲自核实（黄总 2026-10-16，本段的例外）**：工人报「接口不可用 / 做不到 / 受阻」等会改变方向的结论时，主控必须亲自用最少调用复现核实（看错误原文、对照官方文档、试文档里的替代路线），核实后才向黄总汇报，不得直接转述工人结论。起因：2026-10-16 工人报抖音按关键词搜索受阻，主控未核实即转述；黄总质疑后主控亲测，`douyin/search/fetch_multi_search` 与 `douyin/index/fetch_item_query` 可用（`coordination/check-douyin-search-coordinator.md`）。

## 协调者收消息（防漏看）
- 以 Orca 队列为准：`orca orchestration check --terminal <协调者> --run <run> --all --types worker_done,question,escalation --json`，消息在 `result.messages`（最新优先，最多 100 条），按消息 id 去重。不要用 `--peek`：它按最早优先只返回 100 条，旧未读积压会把新消息挤掉（2026-10-10 因此漏看 3 份交付一个多小时）。
- 常驻监视：launchd 服务 `com.$USER.orca-article-studio-watch`（脚本 ~/.agents/orca-collaboration/monitors/article-studio-buffer/watch-inbox.sh），每 45 秒检查一次，新消息会：写 pending.log、macOS 通知、推送一条「【Orca 新消息·必须处理】」到协调者终端。收到这条推送必须立即读报告、审查、决定并用中文告诉黄总。
- 停用：`launchctl bootout gui/$(id -u)/com.$USER.orca-article-studio-watch`。

## 派工
- **Codex 恢复接实施（黄总 2026-10-15，取代下一条中「Codex 只做复核」）**：Codex 既写代码也做复核；同一条线的实施与复核必须用不同窗口。Codex 实施窗口：term_2d118173（属另一 worktree 登记，用 `orchestration dispatch --inject`，派前先 `/new` 并选「Use current Git worktree」）；Codex 检查窗口：term_c75fec01、term_2619a6ec（2026-10-15 经黄总同意新开）。term_503c695f 内 Codex 已退出，不派。
- **（Codex 部分已被上一条取代）Codex 只做测试/检查，实施交给 Pi、CodeBuddy、OpenCode（黄总 2026-10-13 下午，取代下一条「全部交给 Codex」）**：实施派 Pi Agent、CodeBuddy（模型 DeepSeek v4.1 Flash）、OpenCode（模型 Grace Bunny）；Codex 只做复核、测试、验收，不再接实施单。Antigravity 不在名单内，不派。Codex 检查窗口：term_c75fec01（2026-10-15 经黄总同意新开，替换已退出的 term_b0904322；term_cc4ccc16 于 2026-10-15 报 agent_unconfigured 失效；term_6f63337a 已被另一项目（剪映逆向）占用，不再派）。当前实施窗口：Pi term_3b90b5c2（用 `orchestration dispatch --inject`）、OpenCode term_9057d77a（2026-10-15 经黄总同意新开，Space Bunny Free，替换失效的 term_d7f75f2b）。CodeBuddy term_6710141e（2026-10-15 按黄总指示新开，glm-5.3-flash；旧 term_02363911 已失效）。爆款监控线（A2 页面修复、B 监控本体）黄总 2026-10-15 指定交给 OpenCode。在途的 Codex 实施单（task_8a91b45d2903 AIsa 链路）已交付，按原流程复核收口。
- （已被上一条取代）**全部交给 Codex（黄总 2026-10-13 起）**：后续实施和检查一律派 Codex，不再派 Pi、CodeBuddy、Antigravity、OpenCode（Pi 只做完在途的第 13 轮返修合并单）。固定 4 个 Codex 窗口：实施 term_2d118173、term_503c695f；检查 term_6f63337a、term_b0904322（后两个经黄总同意 2026-10-13 新开）。实施窗口与检查窗口不混用，同一条线的实施与检查必须用不同窗口。注意：term_2d118173 属于另一个 worktree 登记，`worker-start --terminal` 会被拒（"does not belong to worktree"），派工前先确认；经黄总同意 2026-10-13 再新开实施窗口 term_50d22cfc；目前可派实施窗口：term_503c695f、term_50d22cfc。
- **合同写明工人弱点并先自测（黄总 2026-10-15）**：写实施合同前先读 skill `worker-dispatch-profiles`，在合同里单列「该工人已知弱点」与对应反例（同线上一轮复核抓到的反例也要列），要求工人交付前逐条自测并在报告里给出结果；未逐条自测的交付直接退回，不派复核。
- **所有改动必须走 Orca 派工（黄总 2026-10-15）**：正式目录、插件目录和 `~/.agents/skills` 的任何改动，都必须有对应的 Orca 任务（task-create + worker-start/dispatch）和 worker_done 记录，复核同样要走 Orca 派给检查窗口；不得在派工之外直接改文件。发现无记录的改动，先查清来源并在 `coordination/work-queue.md` 记录，再决定补流程或回退。
- **做完一轮就停（黄总 2026-10-13，取代原「工人不要停／队列空了要补充」）**：当前轮次收口（合并→重启→复验→提交推送）后暂停派工，不自行发掘或补充新任务。下一步做什么，由黄总先定目标、策略和衡量指标，再按它立项派工；一次只把一件事做好。
- **Pi 停派（黄总 2026-10-15）**：Pi 做完在途的 A1（task_f8ea2fa19f95）后不再派新单，A1 的返修也不派回 Pi；后续实施改派 CodeBuddy（模型 glm-5.3-flash，窗口 term_6710141e，经 `workbuddy-cbc --model glm-5.3-flash` 启动）和 OpenCode。（原「Pi token 不限，优先多派给 Pi」作废。）
- Pi 每派一份新合同前，先在它空闲时发 `/new` 开新会话（合同自带全部上下文）。Pi 压缩失败根因（2026-10-15 查明）：默认压缩把新内容追加到旧摘要上，摘要只增不减（实测 7 次压缩输出 6k→31k tokens），超过 `min(0.8×reserveTokens, models.json 的 maxTokens=32768)` 就报 `generation hit the token cap`；调大 `reserveTokens` 无效。已装扩展 `~/.pi/agent/extensions/bounded-compaction.ts` 接管压缩：每次重写成限长摘要（约 6k tokens），截断就收紧重试，再失败落保底摘要，不会再卡死。新开或 `/reload` 的 Pi 才加载该扩展。
- **Pi 已接入 REA（黄总 2026-10-16 指示「注册，工具链配好」）**：`~/.pi/agent/mcp.json` 注册了 `rea`（187 工具，codemode，timeout 1800s），环境变量带 Ghidra 12.1.4（`/opt/homebrew/opt/ghidra/libexec`）、openjdk@21、JADX MCP jar（`~/.rea/tools/`，sha256 与 REA 内置 pin 一致）。Hopper 未装（会在真实桌面弹 GUI，且是试用版）。注意：REA 的 Ghidra 不支持 universal Mach-O，需先 `lipo -thin arm64` 切片；剪映两个库的切片在 `~/.rea/work/jianying-11.6.0/`。Pi 窗口要 `/reload` 才加载新 MCP 注册。Codex 侧的 `rea` 注册没带这些引擎环境变量，目前只有 Pi 能用 Ghidra。
- **REA 是常规手段，证据纪律写在 Pi 的全局指令里（黄总 2026-10-16）**：`~/.pi/agent/AGENTS.md`（`pi-rea` 块，Orca 块之外）规定逆向/对照/验证类任务先用 REA，并要求每条结论标「观察/推断/未知」（观察带工具名+Evidence ID）、验收逐条「通过/失败/未知」不给总分、测试先用故意改错的输入证明测得出错、解出的字段带来源进 git 笔记。Pi 不接收 MCP prompts（只支持 tools/resources），所以 REA 六个引导流程做成了提示词模板 `~/.pi/agent/prompts/rea-{feature,compare,verify,crash,unknowns,capture}.md`（Pi 里输入 `/rea-` 补全）。写给 Pi 的合同里，凡涉及逆向或「是否等价」的验收项，沿用这套写法；未经 Orca 流程不改 `~/.agents/orca-collaboration/TASK-TEMPLATE.md` 等共用模板。Ghidra 首次分析大库要十几分钟以上（libvideoeditor 93MB 已跑 20+ 分钟仍未完），派 REA 任务时合同里留足时间。
- **REA/剪映逆向只交给黄总一直用的 Pi 窗口（黄总 2026-10-16）**：剪映逆向线（minive）的实施和 REA 分析，一律由 Pi 窗口 term_13800efb 做。term_3b90b5c2 是 2026-10-16 我擅自拿来做 REA 试用的，只做完那一次（libVECreator 分析），之后不再派 REA 或剪映任务。主控每 15 分钟只读检查一次进度并向黄总汇报，不打断、不擅自中断分析；Ghidra 一次只开一个库，大库首次分析要数十分钟到数小时且不保留。
- **目标优先，不矫枉过正（黄总 2026-10-16）**：剪映拆解的目标是让 minive 的行为等价于剪映（整个剪映覆盖度往上提，以剪映真实行为为准，不是自评分数）。约束只留真实底线（不改黄总真实草稿原文件、不动账号/云端、不强杀黄总正在用的进程、REA 一次只开一个库）；取真值的手段（启动剪映、lldb 挂接、AppleScript/AX、预览画面测量、REA 静态与运行时观察）一律可用。给工人加任何限制前先问：它会不会挡住目标？会挡就别加（2026-10-16 我把"不碰剪映"错套给 minive，被黄总纠正）。
- 固定班底，不乱开窗口（黄总 2026-10-12）：同一条线的连续返修/复验派回原窗口（`worker-start --task <id> --terminal <handle>`），保留经验；原窗口失效才新开，并在 `coordination/work-queue.md` 的「班底」段记下替换。实施与复核始终用不同窗口。闲置旧窗口不擅自关闭。
- 禁止新开窗口（黄总 2026-10-12 再次强调）：派工一律复用现有窗口。顺序：同线原窗口 → 同类型的其他空闲窗口（跨线也行，合同自带全部上下文）→ 排队等现有窗口空出。原窗口忙或慢不算失效；只有现有窗口全部失效且黄总同意后才可新开。`worker-start` 一律带 `--terminal <现有句柄>`，不用 `--agent` 新建。现有 Antigravity 窗口：term_cd6b7593（2026-10-13 黄总误关 term_523bdf6d 后经同意新开，Gemini 3.8 Flash）；term_17a543de、term_523bdf6d 已失效。
- 同一批文件同一时间只派给一个工人，避免互相覆盖。派工前逐份比对在途合同的「允许改动」文件，有交集就指定唯一归属（2026-10-10 两份合同都放开 server.py，导致一次覆盖）。
- 跨版本对照（把文件换回备份版跑测试）只能在 /tmp 的独立副本里做，禁止就地覆盖共享工作区的文件。
- Antigravity 的 Opus 4.6 额度已用完，不再用（黄总 2026-10-12）；现有 Antigravity 窗口改用 Gemini 3.8 Flash（窗口内 `/model` 切换，不新开窗口）。
- CodeBuddy 重新启用（黄总 2026-10-12 晚，撤销此前"不再派 CodeBuddy"）；现有窗口 term_02363911（term_8f72d5f8 已不用）。Antigravity 只派到现有窗口（`--terminal`），不再用 `--agent antigravity` 新建。
- OpenCode 恢复派活（黄总 2026-10-13 新开窗口 term_5dbf0c42，免费模型 Space Bunny）；旧窗口 term_d44df52a 已失效。免费模型常每步即停，派后要盯屏推送。
- CodeBuddy worker 一律用 `~/.agents/orca-collaboration/bin/workbuddy-cbc` 启动，禁止直接跑 `codebuddy`：本机 Clash（127.0.0.1:7897）对 codebuddy.ai 会断 TLS（表现为反复 502），直连才通；裸启动的 worker 不带绕行环境，会一直 502（2026-10-11 查明，重启该终端即恢复）。
- 全量 `discover` 多人并发会抢假模型资源互相干扰：跑全量前先向协调者登记排队，一次只跑一份；不得终止他人的测试进程。并发期间工人交付门槛 = 定向模块全绿 + 全量失败只落在他人在途模块；最终门槛全量由协调者统一跑。

## 帖子创作与文案规范（renhua skill / 说人话润色规范·黄总 2026-10-12）
凡是为黄总（@servasyy_ai）撰写、起草或改写推特帖子/文案，必须强制执行 renhua skill（说人话润色规范），坚决消除 AI 腔与营销号套路：
- 严禁滥用 Emoji 标号与浮夸修辞：禁止 1️⃣2️⃣3️⃣、🔥、🚀、炸裂、最狠、说句大实话、兄弟们直接冲等廉价营销号句式。
- 采用极客留白排版：小标题单独成行（如“比 Google Trends 早 1~14 天截流”），正文空行展开，多用短句，保证手机端呼吸感与视觉清爽。
- 词汇降虚向实，紧扣创业者/实战视角：使用真实具象的行业行话（如“肉身刷推”、“直接立项”、“抗噪提炼”、“前端嗅探/后端无缝喂给”），杜绝空洞口号。
- 真实老兵人设：始终立足于“自己亲自跑过、踩过坑、搭过流水线”的第一人称硬核实操视角，结尾行动号召克制自信，不搞廉价叫卖。

