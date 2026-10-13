# AGENTS.md

## 语言
- 和黄总的所有交流一律用中文：进度汇报、结论、提问、选项、按钮文字。
- 用提问工具（AskUser 等）时，问题、选项、标签全部写中文，不得用英文。
- 代码、命令、文件名、接口名保持原样，不翻译。

## 验收
- 验收必须覆盖真实用户路径：在真实浏览器里端到端操作（computer use 或 Playwright），不能只靠单元测试。
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

## 协调者收消息（防漏看）
- 以 Orca 队列为准：`orca orchestration check --terminal <协调者> --run <run> --all --types worker_done,question,escalation --json`，消息在 `result.messages`（最新优先，最多 100 条），按消息 id 去重。不要用 `--peek`：它按最早优先只返回 100 条，旧未读积压会把新消息挤掉（2026-10-10 因此漏看 3 份交付一个多小时）。
- 常驻监视：launchd 服务 `com.$USER.orca-article-studio-watch`（脚本 ~/.agents/orca-collaboration/monitors/article-studio-buffer/watch-inbox.sh），每 45 秒检查一次，新消息会：写 pending.log、macOS 通知、推送一条「【Orca 新消息·必须处理】」到协调者终端。收到这条推送必须立即读报告、审查、决定并用中文告诉黄总。
- 停用：`launchctl bootout gui/$(id -u)/com.$USER.orca-article-studio-watch`。

## 派工
- **全部交给 Codex（黄总 2026-10-13 起，优先于下文各工人条款）**：后续实施和检查一律派 Codex，不再派 Pi、CodeBuddy、Antigravity、OpenCode（Pi 只做完在途的第 13 轮返修合并单）。固定 4 个 Codex 窗口：实施 term_2d118173、term_503c695f；检查 term_6f63337a、term_b0904322（后两个经黄总同意 2026-10-13 新开）。实施窗口与检查窗口不混用，同一条线的实施与检查必须用不同窗口。注意：term_2d118173 属于另一个 worktree 登记，`worker-start --terminal` 会被拒（"does not belong to worktree"），派工前先确认；目前实际可派的实施窗口只有 term_503c695f。
- 工人不要停：任何工人交付并审查完，立即从 `coordination/work-queue.md` 派下一项；队列空了要补充。
- Pi 用本地显卡部署的模型，token 不限，优先多派给 Pi。
- Pi 每派一份新合同前，先在它空闲时发 `/new` 开新会话（合同自带全部上下文）。同一会话连续压缩时摘要越滚越长，超过摘要输出上限就会「压缩失败」（2026-10-10 查明）；`~/.pi/agent/settings.json` 已设 `compaction.reserveTokens=65536`，改设置后需 `/reload` 或重开 Pi 才生效。
- 固定班底，不乱开窗口（黄总 2026-10-12）：同一条线的连续返修/复验派回原窗口（`worker-start --task <id> --terminal <handle>`），保留经验；原窗口失效才新开，并在 `coordination/work-queue.md` 的「班底」段记下替换。实施与复核始终用不同窗口。闲置旧窗口不擅自关闭。
- 禁止新开窗口（黄总 2026-10-12 再次强调）：派工一律复用现有窗口。顺序：同线原窗口 → 同类型的其他空闲窗口（跨线也行，合同自带全部上下文）→ 排队等现有窗口空出。原窗口忙或慢不算失效；只有现有窗口全部失效且黄总同意后才可新开。`worker-start` 一律带 `--terminal <现有句柄>`，不用 `--agent` 新建。现有 Antigravity 窗口：term_cd6b7593（2026-10-13 黄总误关 term_523bdf6d 后经同意新开，Gemini 3.8 Flash）；term_17a543de、term_523bdf6d 已失效。
- 同一批文件同一时间只派给一个工人，避免互相覆盖。派工前逐份比对在途合同的「允许改动」文件，有交集就指定唯一归属（2026-10-10 两份合同都放开 server.py，导致一次覆盖）。
- 跨版本对照（把文件换回备份版跑测试）只能在 /tmp 的独立副本里做，禁止就地覆盖共享工作区的文件。
- Antigravity 的 Opus 4.6 额度已用完，不再用（黄总 2026-10-12）；现有 Antigravity 窗口改用 Gemini 3.8 Flash（窗口内 `/model` 切换，不新开窗口）。
- CodeBuddy 重新启用（黄总 2026-10-12 晚，撤销此前"不再派 CodeBuddy"）；现有窗口 term_8f72d5f8。Antigravity 只派到现有窗口（`--terminal`），不再用 `--agent antigravity` 新建。
- OpenCode 恢复派活（黄总 2026-10-13 新开窗口 term_5dbf0c42，免费模型 Space Bunny）；旧窗口 term_d44df52a 已失效。免费模型常每步即停，派后要盯屏推送。
- CodeBuddy worker 一律用 `~/.agents/orca-collaboration/bin/workbuddy-cbc` 启动，禁止直接跑 `codebuddy`：本机 Clash（127.0.0.1:7897）对 codebuddy.ai 会断 TLS（表现为反复 502），直连才通；裸启动的 worker 不带绕行环境，会一直 502（2026-10-11 查明，重启该终端即恢复）。
- 全量 `discover` 多人并发会抢假模型资源互相干扰：跑全量前先向协调者登记排队，一次只跑一份；不得终止他人的测试进程。并发期间工人交付门槛 = 定向模块全绿 + 全量失败只落在他人在途模块；最终门槛全量由协调者统一跑。

## 帖子创作与文案规范（renhua skill / 说人话润色规范·黄总 2026-10-12）
凡是为黄总（@servasyy_ai）撰写、起草或改写推特帖子/文案，必须强制执行 renhua skill（说人话润色规范），坚决消除 AI 腔与营销号套路：
- 严禁滥用 Emoji 标号与浮夸修辞：禁止 1️⃣2️⃣3️⃣、🔥、🚀、炸裂、最狠、说句大实话、兄弟们直接冲等廉价营销号句式。
- 采用极客留白排版：小标题单独成行（如“比 Google Trends 早 1~14 天截流”），正文空行展开，多用短句，保证手机端呼吸感与视觉清爽。
- 词汇降虚向实，紧扣创业者/实战视角：使用真实具象的行业行话（如“肉身刷推”、“直接立项”、“抗噪提炼”、“前端嗅探/后端无缝喂给”），杜绝空洞口号。
- 真实老兵人设：始终立足于“自己亲自跑过、踩过坑、搭过流水线”的第一人称硬核实操视角，结尾行动号召克制自信，不搞廉价叫卖。

