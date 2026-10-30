# 实施合同：推特雷达页接入 Grok Bot 精选爆款（impl-grok-bot-radar）

派给：Codex 实施窗口 term_02017284（复核另派 term_57dd3162，本窗口不做复核）
背景（黄总 2026-10-29 晚）：Grok Bot 每天 9:00 出日报、8/12/16/20 点出快报，挑推特爆款，结果写在本机 `~/grok-bot-reports/`。目前只以「外部文章收藏」进了知识库：点不开、看不到原文、看不出为什么值得学。黄总要求：**先保证推特雷达页能看到 Grok Bot 挑出来的精品爆款**，看得到原文、数据、值得学的理由，并且能反馈准不准，让 Grok Bot 每天迭代、越挑越准。

## 0. v2 变更（黄总 2026-10-29 21:15，优先于下文所有冲突条款）
黄总原话：「为什么要单独做一个 Grok Bot 呢？从哪里来的，放一个小图标就可以了！我主要的还是需要内容是完整的！」
1. **不做单独的 Grok Bot 标签**（作废第 1 节第 1 条、第 4 条的独立标签与独立筛选）。Grok Bot 的帖子并入现有视图，和 AIsa 的帖子混排：
   - `label=低粉爆款` 或粉丝 ≤10,000 的爆款 → 「⚡ 低粉逆袭爆款单篇榜」；
   - 其余（爆款/起势/推流/观察）→ 「⭐ 特别关注博主爆款」（Grok Bot 的 X List 就是扩大后的关注池），推流/观察排在爆款、起势之后；
   - 赛道按钮（5 条 AI 赛道）要能筛到 Grok Bot 帖子：按 topic/list/正文给出映射表，映射不上的只在「全部 AI 赛道」下出现，不能丢；
   - 遵守现有 48 小时窗口与内容过滤规则，规则不同时在报告里列出差异，不要悄悄放宽。
2. **来源只用小图标表示**：每张卡片角上标来源小图标/徽标（如「Grok」「AIsa」）；同一推文 id 两边都抓到 → 一张卡，两个图标都显示，数字取最新。Grok Bot 独有的「为什么爆」「可复制写法」附在卡片里（可折叠），AIsa 卡片没有就不显示。
3. **内容完整是第一要求**：
   - 卡片正文逐字完整（第 3 节反例 1 照旧）；
   - 报告里如果有 `full_text`、`article`（X 长文标题与正文）、`thread`（串推）、`quoted`（被引用帖作者/正文/链接）、`media`（图片/视频地址）、`card`（外链卡片）字段，全部显示：图片显示缩略图，视频显示封面与「播放原帖」，被引用帖显示为嵌套小卡；
   - 判断「可能不完整」时，卡片上必须明确标出「原文不完整，点开原帖看全文」，不得悄悄显示半截。判断条件至少包括：报告给了 `content_complete=false`；正文不到 80 字但收藏 ≥100；正文结尾只有 t.co 链接；`why`/`pattern` 提到长文、引用、串推但缺对应字段；
   - 顺带检查现有 AIsa 卡片是否有截断（前端和 bundle 两层），只报告不改 AIsa 采集代码。
4. 反馈按钮放在每张卡片上（现有卡片已有反馈按钮就复用，不要做两套）；Grok 来源的反馈照第 1 节第 5 条导出到 `~/grok-bot-reports/feedback/studio-feedback.json`。
5. Grok Bot 报告的新鲜度并入现有「数据回执」行，一句话即可，不另开横幅。
6. 允许改动文件不变；第 3 节反例照旧逐条自测，另加反例 11：同一 id 同时来自 AIsa 与 Grok，一张卡、两个图标、数字取最新；反例 12：无 `full_text` 的 40 字短文、收藏 111 的样本（真实数据 @criscxuan 2115715632532713672）必须标「原文不完整」。

## 1. 目标与验收指标
1. 打开 `http://127.0.0.1:8766/twitter-radar.html`，**默认第一个标签**是「🛰 Grok Bot 精选爆款」；原有 4 个标签行为不变。
2. 卡片数 = `~/grok-bot-reports/` 下 daily/、alerts/、weekly/ 的 JSON 里不重复推文 id 的个数（在所选时间范围内）。同一推文出现在多份报告里只显示一张卡：数字取最新一份，卡上标「首次发现时间 / 最近更新时间 / 出现于哪几份报告（日报/快报/周报）」。
3. 每张卡显示：作者名、@handle、粉丝数、标签（爆款/起势/低粉爆款/推流/观察）、所属 List 与 topic、中推/英推、**完整原文**（保留换行，不截断；超长可折叠但「展开」后必须是全文）、`title_zh`（有则显示）、浏览/点赞/收藏/转发/回复/每小时浏览/发帖距采集小时数、发帖时间（北京时间）、「为什么爆」（why）、「可复制写法」（pattern）、「打开原帖」按钮（新标签页打开 x.com 原链接）。
4. 筛选：标签、topic、时间范围（24 小时 / 48 小时 / 7 天，默认 48 小时）；排序：最近发现 / 收藏数 / 每小时浏览。
5. 反馈按钮：「准·值得学」「不准」「已用来写」，刷新后保持；同一推文可改选。反馈落盘到工作台 workspace（仿照 `radar_page_api.py` 的 `_monitor_dir` 约定放在 `twitter-radar/grok-bot/feedback.json`），并**额外原子写一份**到 `~/grok-bot-reports/feedback/studio-feedback.json`（只允许写这一个文件，目录不存在就建），给 Grok Bot 每天复盘读取。
6. 顶部回执条：最近一份报告的时间与类型、本范围卡片数、跳过的坏文件数；超过 6 小时没有新快报时显示黄色提示「Grok Bot 已 N 小时没有新报告」。
7. Grok Bot 新写入报告后，**不重启服务**，刷新页面即可看到（按文件 mtime 缓存可以，不能只在启动时读一次）。
8. 接口 `GET /api/radar/grok-bot` 在当前真实数据上响应 < 1 秒（知识库那种 40 秒不可接受）。

## 2. 允许改动的文件（唯一归属，其他文件不碰）
- 新增 `grok_bot_radar.py`（读取、去重、合并、反馈读写）
- `server.py`：只加 `GET /api/radar/grok-bot`、`GET/POST /api/radar/grok-bot/feedback` 路由注册，写接口沿用现有 `/api/radar/feedback` 的 Origin 与 `X-Article-Studio` 校验
- `public/twitter-radar.html`、`public/twitter-radar.js`：加标签、卡片、筛选、反馈
- 新增 `tests/test_grok_bot_radar.py`、`tests/e2e/twitter_radar_grok_bot.cjs`
- 报告目录可用环境变量 `GROK_BOT_REPORTS_DIR` 覆盖，默认 `~/grok-bot-reports`；测试一律用 /tmp 夹具，不依赖真实目录

**工作区里 `server.py`、`radar_page_api.py` 等文件有黄总未提交的改动（采集降频等），那是用户工作，不得回退、覆盖或重排；只做增量修改，交付时给出 `git diff --stat` 证明只动了上面列的文件。** `~/grok-bot-reports/` 除第 5 条那一个反馈文件外只读。

## 3. 该工人已知弱点与必测反例（交付前逐条自测，报告逐条给结果；未逐条自测直接退回）
Codex 历史弱点：原文截断与贴文歧义、整条链路与并发状态遗漏、链接重定向漏拦。对应反例：
1. 原文保真：多行推文、2000 字以上长文、含 emoji、含 `<script>alert(1)</script>`、`&amp;`、`<b>` 的推文，页面显示与 JSON 原文逐字一致（用 textContent，不得 innerHTML 拼接不可信文本），换行保留，展开后无截断。
2. 链接安全：`url` 为 `javascript:alert(1)`、`http://evil.com/x.com/status/1`、`https://x.com.evil.com/...`、空串时，不生成可点链接；只放行 `https://x.com/<handle>/status/<数字>` 与 `https://twitter.com/...` 同形；正文里的 http(s) 链接如做成可点，必须带 `rel="noopener noreferrer"` 且只放行 http/https。
3. 坏文件：空文件、半写入的截断 JSON、`posts`/`entries` 不是数组、字段缺失、数字是字符串（如 `"1.2万"`、`"8202"`）、单文件超过 5MB、非 .json 文件、符号链接指向目录外 → 跳过或按缺失显示「—」，回执计数，整页不报错、接口不 500。
4. 目录不存在或为空 → 显示空态「Grok Bot 还没有产出报告」，接口 200。
5. 去重合并：同一 id 同时在 daily 和两份 alerts 中、数字不同 → 一张卡，取最新报告的数字，来源列出 3 份；id 缺失时用 url 里的 status 数字兜底，再缺失则跳过并计数。
6. 时区：`created_at` 为 UTC ISO，页面显示北京时间；`collected_at` 为「2026-10-29 20:13 Asia/Shanghai」格式也要能解析；时间范围筛选在 24h 边界上正确。
7. 并发：Grok Bot 写文件到一半时读取不报错；两个并发反馈 POST 都落盘不丢（临时文件 + rename，加锁）；反馈 payload 校验类型、长度、未知 id。
8. 写接口无 Origin / 错误 Origin / 缺 `X-Article-Studio` → 拒绝，不写盘。
9. 新报告热加载：服务运行中往夹具目录新增一份快报，再请求接口能看到新卡片。
10. 原有 4 个标签、爆款监控、旧反馈接口行为不变：跑现有 `tests/test_twitter_radar_*.py`、`tests/test_radar_page_api*.py`、`tests/e2e/twitter_radar_monitor_page.cjs` 全绿。

## 4. 验收方式（硬性）
- Python 测试用 `../article-importer/.venv/bin/python`。
- 改完后端必须重启 8766（`launchctl kickstart -k gui/$(id -u)/com.servasyy.article-studio`），报告里给出新 PID，并确认 `GET /api/radar/grok-bot` 200 且带真实数据。
- 浏览器验收只用隔离 Playwright（独立 /tmp profile），**不得用 computer use、不得操作黄总的真实桌面或 Chrome**。在真实服务上：打开雷达页 → 默认落在 Grok Bot 标签 → 核对卡片数与真实文件去重数一致 → 抽 3 张卡逐字核对原文、why、pattern、数字 → 点「打开原帖」确认新标签页 URL 正确 → 点一次「准·值得学」刷新后仍在（验收完把这条测试反馈撤掉，恢复原状）→ 截图保存到 `coordination/impl-grok-bot-radar-*.png`。
- 不提交、不推送。

## 5. 交付
报告写到 `coordination/impl-grok-bot-radar-report.md`，开头必须是「决定要点」表（≤10 行：结论通过/不通过、H/M/L、改动文件与 SHA256、测试数字、新 PID、真实卡片数），然后逐条列第 3 节 10 条反例的自测结果，最后 `orca orchestration worker-done` 回报。
