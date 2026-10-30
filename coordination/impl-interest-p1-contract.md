# 推特情报 1 期：兴趣通道（实施合同）

派单：主控 term_b884acc7（run_17dc13f37a10）；实施 Codex term_02017284；复核 Codex term_57dd3162（不同窗口）。
仓库 /Users/huangzongning/development/article-studio；Python ../article-importer/.venv/bin/python。8766 重启 `launchctl kickstart -k gui/$(id -u)/com.servasyy.article-studio`；查 PID 只用 `launchctl list | awk '$3=="com.servasyy.article-studio"{print $1}'`，禁止 launchctl print / ps eww / env / cat plist。

## 依据（先读）
- coordination/twitter-intel-plan.md v0.2：第 1 节「已定决定」、3.4「兴趣通道」、4「分期」。
- coordination/twitter-intel-handoff-r1.md：「补充」节（10-30 09:40，必读）；A 段「一、新开兴趣通道」第 4 条是 Grok 写出的 json 字段；B 段第 1 条是本单范围。
- 4-2（server.py 拆分）已于 10-30 收口，今日页 T5 也已收口，当前无在途单与本单文件重叠。

## 要做
1. **兴趣清单（工作台维护）**
   - `<GROK_BOT_REPORTS_DIR>/interest-topics.md`（目录沿用 grok_bot_radar.reports_dir()，默认 ~/grok-bot-reports）已由 Grok Bot 于 10-30 09:38 建好，格式：标题与引言 + `## 当前方向`（`- ` 列表）+ `## 说明`（筛选规则）。**沿用这个格式**：设置页 /settings.html 新增「兴趣清单」区，只增删「当前方向」的列表项；标题、引言、`## 说明` 段及其他任何段落逐字保留（含顺序和空行）。以该文件为准读取当前清单；文件不存在时用计划 3.4 的 5 项并按上述格式新建。如需工作台侧存档（如 workspace/interest/topics.json）只作备份，不得与文件不一致。
   - 写入先写同目录临时文件再原子替换；写之前比对 mtime，若文件在页面打开后被别人（如 Grok Bot）改过，提示「文件已被修改，请刷新」而不是覆盖。
   - **该目录里工作台只写 interest-topics.md 和已有的 feedback/studio-feedback.json，别的一律不写**（包括不建 interest/ 下任何文件；临时文件写完即改名，失败要清理）。
   - 校验：每项去首尾空白、非空、长度上限（如 80 字）、总数上限（如 30）、去重、不能含换行或以 `#` 开头（防止破坏 Markdown 结构）；POST 走现有 X-Article-Studio 头校验风格。2. **读取 Grok 兴趣报告**：`<reports_dir>/interest/*.json`（字段见交接 A 段第 4 条）。
   - 只读最近 N 天（如 7 天）文件，单文件大小上限（如 2 MiB），按文件名时间排序。
   - 健壮性：字段缺失、坏链接、非 x.com 链接、id 与 url 不符、重复 id（跨文件按 id 合并，保留最新 collected_at 的版本，合并 also_posted_by）、空文件 / `[]` / 「本次无」、文件写到一半（非法 JSON 或 .tmp）都不能让页面崩：坏文件跳过并在来源状态里标异常，坏条目丢弃或标不完整，并计数显示。
   - 帖子 url 必须规范为 `https://x.com/<handle>/status/<数字id>`，不规范的不当作可点原帖链接。`usable[].url` 只允许 http/https，javascript:/data: 等一律不渲染成链接；外链 rel=noopener noreferrer。
   - content_complete=false 或缺 full_text 的标「内容不完整」并显示 incomplete_reason。
3. **今日页「兴趣」标签**：与现有爆款列表分开（不混排），可切换；首屏接口 < 1 秒。卡片显示：一句话新在哪（what_new）、可用链接（按 type 标中文：权重/API/仓库/教程/论文/其他）、相关度（relevance 0–3）、作者与发布时间、右上角来源图标沿用 T5 组件；阅读区显示全文（article/thread/quoted/media 有就展示，沿用现有 rich 渲染与 html_sanitize，防 XSS）。按钮三个：存、准、不准（不要「拿去写」）。
4. **入库**：只有点「存」才写 items（status=kept），进素材库，按帖子 id 幂等（重复点不重复入库，与今日页现有 canonical ID 规则一致）。准/不准沿用今日页现有反馈落盘方式，标明 channel=interest。爆款通道现有自动入库行为不改；兴趣通道无 10K 门槛。

## 允许改动
新建 interest_channel.py（名字可调）、today_view.py、settings_view.py、public/today.html|js|css、public/settings.html|js|css、public/components/item-card.js、list-reader.js（确有需要）、server.py 仅限加路由接线的少量行（报告列出行号）、新增测试 tests/test_interest_*.py 与 e2e。其他不动；视频实验室保护边界（public/lab-test.*、generation/*、server.py 视频/generation 路由、tests/test_*lab*）绝对不动。不动 Grok Bot 设置；不碰飞书；不停 AIsa 推特采集。

## 该工人已知弱点与必测反例（逐条自测，报告逐条给结果）
- G1 解析取第一处而非正确位置（T5 M1 教训：正则匹配正文首个数字）：用夹具证明 id/url 校验取的是规范字段，url 中 status 后的 id 与 id 字段不一致时判坏链接；`https://x.com.evil.com/...`、`https://twitter.com/...`、`http://x.com/...`、带 query 的 url 分别给出预期并测试。
- G2 测试 mock 泄漏（T5 全量 1F 教训）：所有 patch 用 addCleanup 或上下文管理器；测试不得写真实 ~/grok-bot-reports，一律用临时目录 + GROK_BOT_REPORTS_DIR；加一个测试证明运行后真实目录无变化（对比前后文件清单与 mtime）。
- G3 写边界：除 interest-topics.md 与 feedback/studio-feedback.json 外不写 Grok 目录，用测试列目录证明；interest-topics.md 原子写（写一半中断不留半个文件）。
- G4 反例六项必须逐项实测：缺字段、坏链接、重复 id、非 x.com 链接、空文件、文件写到一半；另加 usable 含 javascript: 链接、full_text 含 `<script>`/onerror。
- G5 凭证：日志、页面、报告不得出现任何 Key/Cookie。
- G6 性能：放 7 天 × 2 份 × 15 条的夹具，再加一份 2 MiB 以上的坏文件，接口 < 1 秒。
- G7 兼容新字段：Grok 日报和快报自 10-30 起新增 `is_retweet`、`original_author`、`original_author_followers`、`bookmarks`、`collected_at`、`crowding`（crowding 可能是对象：条数 + 2–3 条代表链接）。用夹具证明 grok_bot_radar.py 与 today_view.py 读到这些字段（含类型异常：字符串数字、null、crowding 为字符串或列表）不报错、今日页正常；本单不要求展示这些字段，但 bookmarks 若现有逻辑已读则保持一致。
- G8 interest-topics.md 格式保真：用 Grok 现有文件的副本做夹具，增一项、删一项、再保存，证明「说明」段与其他内容逐字不变；并发改动（mtime 变化）时不覆盖。

## 验收
- 定向测试全绿；全量 Python 与 UI 跑前向主控登记。基线：Python 3587 项 3579P/0F/1E/7S（E=受保护 video_lab），UI 723 项 719P/3F/1S（3F=受保护 lab-test）；不得新增失败。
- 重启 8766 确认新 PID。隔离 Playwright（独立 profile；对 8766 或随机端口）走：今日页 → 兴趣标签 → 打开一条看全文 → 存 → 素材库搜到；设置页增删兴趣清单并保存 → 确认 interest-topics.md 同步且「说明」段不变（真实 8766 上的增删验收要在操作前备份文件，结束时恢复为操作前的「当前方向」，并比对整份文件与操作前逐字一致；不擅自改黄总的清单）。截图 1440 与 390 存 /tmp/impl-interest-p1/。禁止 computer use，不操作黄总真实桌面或 Chrome。
- **真实文件**：Grok 兴趣通道每天 10:30、22:30 跑，10-30 上午就会有第一份真实 `~/grok-bot-reports/interest/*.json`。先用夹具开发，**最终验收必须用这份真实文件**（只读，不改它）。若交付时仍未出现，报告写明「真实文件验收待补」照发 worker_done，主控再安排。
- 不提交、不推送。

## 报告
coordination/impl-interest-p1-report.md，开头「决定要点」表（≤10 行：结论、H/M/L 自查、改动文件与 SHA256、测试数字、新 PID、截图路径、真实文件验收状态），然后 G1–G6 逐条结果、server.py 改动行号。完成后 worker_done。
