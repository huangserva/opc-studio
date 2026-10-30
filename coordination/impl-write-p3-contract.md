# 合同：impl-write-p3（Codex 实施 · Orca）：重新设计 3 期「拿去写 + 发布复盘」

上游：`coordination/redesign-plan.md` 第 3、7 节；依赖 2 期 items 库（`items_store.py`）已收口。冲突时以本合同为准。
派工条件：2 期复核收口后派。
依据调查：explorer 2026-10-29 22:05 只读报告（要点抄在第 0 节）。

## 0. 现状要点
- 作品是文件夹：`store.library/<slug>/`，`POST /api/create` 新建、`POST /api/save/<slug>`（`base_version` 乐观锁，冲突 409）写正文（`server.py:853-879,2147-2152`）。
- 已有不调模型的后端建稿范例：`KnowledgeStore.draft`（`knowledge_store.py:935-961`）直接 `store.create` + `store.save`，并写 `knowledge-sources.json`。
- 助手 `assistant.start()` 有全局单任务锁（`assistant_agent.py:467-468`），会和黄总聊天互相抢占，**本期不得使用**。
- 仓库里没有「说人话」规范的 prompt 文件，起草时没有注入任何风格（`assistant_workflow.py:19-27`）。
- 发布记录在 `<slug>/deliveries.json`；表现数据在 `publication_metrics.snapshots`、`publication_observations`、`x_official/feedback/<account>.json`（`XFeedback.sync`，手动触发，最多 20 条）。曝光数可能拿不到（`public_metrics` 通常不含 impression，未验证）。
- feedback.html 与 x-official.html 都依赖 x-official 账号连接和 `feedback-sync`，大量重叠；x-official 独有的是发布表单、任务记录和 OAuth 配置。

## 0.1 2 期收口后的实际情况（主控 23:35 补充，以此为准）
- 2 期已收口：`items_store.py`、`library_search.py`、`public/library.{html,js,css}`、`public/components/list-reader.js`、`today_view.py`、`public/today.js` 均为 2 期最新版本，报告见 `coordination/impl-library-p2-report.md`、`coordination/fix-library-p2-m1b-report.md`。
- 2 期的「拿去写」现状：复制全文到剪贴板，调 `POST /api/items/<id>/use` 把 status 记为 used，并提示「已复制，粘到创作台」。本期把它替换成第 2 节的建稿流程，`/use` 接口保留不删（兼容），但页面不再走剪贴板。
- 因此允许改动的文件再加上：`public/library.js`、`public/today.js`、`today_view.py`（只限「拿去写」相关的代码）。`items_store.py` 可以给 items 加 `used_work_id` 列（迁移要幂等），也可以加 `mark_used`，其他函数不动。
- 回归基线：Python 60 项、素材库浏览器 31 项、今日页浏览器 56 项，本期不得回退。2 期 e2e 里断言「复制全文」的用例，如果因流程替换必须修改，要在报告里逐条列出改了哪条、为什么，并补上新流程的断言。
- 反例 14（2 期教训）：**只看数字不看功能**。2 期第 1 轮返修速度达标，但常用词搜出来 0 条。本期每个验收项都要检查实际内容，不能只看状态码或耗时：底稿正文里要真有原文全文和数据，v0002 要真有模型写的正文（不是空的，也不是原文的复制），复盘页要真列出已发布记录（报告里写明真实库有多少条、页面显示多少条，两个数要对上）。

## 1. 允许改动的文件（唯一归属）
- 新增 `write_from_item.py`（建稿 + 后台起草）
- 新增 `prompts/draft-template.md`（起草模板，内容见第 3 节）
- 新增 `publish_review.py`（发布复盘聚合，只读现有发布与表现数据）
- 新增 `public/publish.html`、`public/publish.js`、`public/publish.css`
- `public/components/list-reader.js`、`item-card.js`：「拿去写」改为调新接口（保持 1、2 期页面不回退）
- `items_store.py`：只允许新增 `mark_used(item_id, slug)` 一类小函数，写 `used_work_id`
- `server.py`：只新增 `POST /api/items/<id>/draft`、`GET /api/items/<id>/draft-status`、`GET /api/publish/review`、`GET /api/publish/review/<slug>` 的装配行；为 /publish.html 复用 1 期的 CSP 写法（如需外链图）
- `public/workspace-nav.js`：加「发布复盘」（创作台之后）；「发布反馈」「X 官方接口」移入「旧版」分组（不删，路径不变）
- 新增 `tests/test_write_from_item.py`、`tests/test_publish_review.py`、`tests/e2e/write_and_publish.cjs`
- 报告 `coordination/impl-write-p3-report.md` 与截图
- **不改** `knowledge_store.py`、`assistant_agent.py`、`assistant_workflow.py`、`publishing/*`、`publication_*.py`、`content_store.py`、`feedback*.js`、`x-official*.js`。

## 2. 「拿去写」流程
1. 点「拿去写」：同步先 `window.open('about:blank')` 拿到窗口（防弹窗拦截），再 `POST /api/items/<id>/draft`（today 条目先按 2 期逻辑 upsert 进 items）。
2. 后端立即（不调模型）按 `KnowledgeStore.draft` 写法 `store.create(type='post')` + `store.save` 生成 **v0001 底稿**：标题、来源链接、原文全文、数据（浏览/赞/收藏/每小时）、为什么爆、可复制写法、切入角度（my_angle）、提纲（outline；没有就按模板三段留空标题）。写 `<slug>/source-item.json`：`{item_id, sources, url, created_at}`；`items.used_work_id = slug`；返回 `{slug}`，窗口跳到 `/index.html?article=<slug>`（若创作台不支持该参数，用现有能打开指定作品的方式，报告写明）。**点击到底稿可见 ≤ 3 秒。**
3. 后台线程做**一次**模型调用（复用仓库现有的单次模型调用方式，例如 intake 分析所用的客户端与当前激活 profile；不走 `assistant.start`），用第 3 节模板生成正文，成功后 `store.save(base_version=v0001)` 写成 v0002，`summary='AI 初稿'`。
   - 若 409（黄总已手动改过）：不覆盖，把 AI 初稿写到 `<slug>/ai-draft.md`，状态标 `conflict`，创作台/今日页提示「AI 初稿已另存，没有覆盖你的修改」。
   - 模型失败或超时（上限 180 秒）：状态 `failed` + 原因，底稿保留。
   - `GET /api/items/<id>/draft-status` 返回 `pending|done|conflict|failed` + slug + 耗时。今日页/素材库卡片上显示这个状态。
4. 同一 item 重复点「拿去写」：已有 used_work_id 时直接打开已有作品，不重复建（除非按住 Shift 点，另建新稿）。

## 3. 起草模板 `prompts/draft-template.md`
从以下来源整理（逐条注明出处，不自创规则）：`AGENTS.md`「帖子创作与文案规范」段、`coordination/content-profile.md` 第 1、5 节。必须包含：
- 结构：真实问题 → 我亲手跑流程 → 给读者能直接用的工具、参数、链接。
- 第一人称亲测视角；短句；小标题单独成行；手机好读。
- 禁用：1️⃣2️⃣3️⃣ 类 emoji 标号、🔥🚀、炸裂、最狠、说句大实话、兄弟们直接冲。
- 不编造：没有亲测数据的地方写「【待补：我的实测数据】」占位，不许虚构数字、截图、经历。
- 不复制原文：原文只作参考，正文须是黄总视角的重写；引用原作者观点要标 @handle。
- 长度：推文串 3–8 条或单篇 300–800 字，由素材类型决定（报告写明规则）。

## 4. 发布复盘页 `publish.html`
- 列表：按发布时间倒序列出所有 `deliveries.json` 里已发布的记录（kind 属于 `publication_metrics.py:27` 的已发布集合），每条显示：作品标题、平台、发布时间、链接、最新指标（浏览/曝光、赞、转、回复、收藏；拿不到的写「未知」并在悬停里说明原因），**来源素材**（有 `source-item.json` 就显示素材标题与来源图标，点开跳素材库该条）。
- 顶部两个数：近 7 天发布数、其中来自「今日/素材库」的条数（规划第 8 节指标「每周 ≥2 条从今日页出发」）。
- 右侧详情：该条全部快照（时间序列小表）、复盘备注（只读显示现有 review；编辑仍去旧页，本期不搬编辑功能）。
- 「同步 X 数据」按钮：调用现有 `/api/x-official` 的 `feedback-sync`（**不新增定时任务**，X 读取按条计费，定时需黄总另批）；按钮旁写明「手动同步，每次最多 20 条」。
- 「X 发布 / 账号设置」：本期只放入口链接到旧 `x-official.html`。
- `GET /api/publish/review` 5 次 < 1 秒。

## 5. 该工人已知弱点与必测反例（逐条 通过/失败 + 证据；未逐条自测直接退回）
| # | 弱点 | 反例 |
|---|---|---|
| 1 | 抢占助手锁 | 起草进行中，黄总在创作台聊天框发消息仍能正常开始（证明没占 `assistant.active`） |
| 2 | 覆盖人工修改 | 底稿生成后手动 save 一次，再让后台起草完成 → 不覆盖，`ai-draft.md` 生成，状态 conflict，页面有提示 |
| 3 | 模型失败 | 模型不可用 / 超时（用假模型或断开配置的隔离副本）→ 底稿在、状态 failed、原因可读、无未捕获异常 |
| 4 | 重复建稿 | 同一 item 连点 3 次「拿去写」→ 只建 1 个作品；Shift 点才新建 |
| 5 | 原文截断 | 2400 字原文 + emoji 进底稿后逐字一致 |
| 6 | 注入 | 原文含 `<script>`、`<img onerror>`：底稿 HTML 中被转义，创作台打开不执行 |
| 7 | 风格 | 用 3 个真实素材在隔离副本跑起草（如允许调真实模型；不允许则用假模型并说明），输出不含禁用词清单任一项；含亲测占位而非编造数字（列出检查脚本） |
| 8 | 来源链路 | `source-item.json` 与 `items.used_work_id` 双向一致；手动登记一条发布后，发布复盘页能显示来源素材 |
| 9 | 指标缺失 | 无曝光字段的记录显示「未知」不显示 0；指标为 NaN/字符串不炸 |
| 10 | 弹窗拦截 | 隔离 Playwright 默认弹窗策略下点「拿去写」能打开作品 |
| 11 | 来源校验 | 新 POST 接口缺 Origin / 错 Origin / 缺 `X-Article-Studio` → 403，不建作品 |
| 12 | 范围 | 第 1 节「不改」清单文件 SHA 不变；1、2 期 e2e 全过 |
| 13 | 计费 | 测试全程 X 接口调用 0 次（拦截证明）；「同步 X 数据」只在点击时调用 |

## 6. 验收
- 新增单测、隔离 e2e 全过；改完重启 8766，确认新 PID 后用隔离 headless Playwright 真实验收：从今日页点一条「拿去写」→ 截图底稿 → 等后台起草状态（真实模型若可用）→ 截图 v0002；打开发布复盘页截图。
- 真实验收建出来的测试作品：标题前缀「【验收】」，验收后保留并在报告列出 slug（不删黄总库里任何作品）。
- **只用隔离 Playwright（独立 profile、/tmp 目录或随机端口）。禁止 computer use 操作黄总真实桌面或 Chrome。**
- Python 用 `../article-importer/.venv/bin/python`。不提交、不推送。

## 7. 交付
报告 `coordination/impl-write-p3-report.md`，开头「决定要点」表（≤10 行：结论、H/M/L、改动文件与 SHA256、底稿耗时、AI 起草耗时、测试数字、8766 PID）。然后第 5 节 13 条和第 0.1 节反例 14 逐条给结果。发一条 `worker_done` 给主控 `term_b884acc7-d613-41d8-b736-0e3e84a18b37`。
