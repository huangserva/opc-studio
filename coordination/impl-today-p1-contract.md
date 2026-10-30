# 合同：impl-today-p1（Codex 实施 · Orca）：重新设计 1 期「今日」页

上游：`coordination/redesign-plan.md`（黄总 2026-10-29 认可）第 3、4、6、7 节。本合同与它冲突时以本合同为准，本合同没写的以规划为准。
派工条件：已满足。推特雷达单 task_5fb69819e395 复核（task_87a9c4663438）结论：雷达主路径通过，2 项 M 都是组件复用缺陷，主控决定并入本单第 3 节反例 13、14 修，不回头改 `twitter-radar.js`。

## 0. 目标（一句话）
黄总打开 `http://127.0.0.1:8766/today.html`，第一屏就看到今天值得学的帖子和视频，点 1 下看全文，点 1 下处理完。

## 1. 范围

### 1.1 本期接的来源（3 个）
| 来源 | 读法 | 图标 |
|---|---|---|
| Grok Bot | `grok_bot_radar.radar_view()`（已含与 AIsa 按 id 合并的 `merged_posts`） | G |
| AIsa 推特雷达 | 同上 `merged_posts` 里 sources 含 aisa 的条目；AIsa 独有条目也要出现 | A |
| 视频爆款雷达（抖音 / 小红书） | `video_radar_page_api.page_view()` | V |

微信雷达放 2 期，本期不接。不新增任何采集、不调 AIsa、不调 `/api/collect/*`。

### 1.2 允许改动的文件（唯一归属，其他文件一律不改）
- 新增 `today_view.py`（后端聚合：读上面 3 个来源，统一成规划第 5 节 items 字段的子集，内存去重排序；不建表）
- 新增 `public/today.html`、`public/today.js`、`public/today.css`
- 新增 `public/components/item-card.js`、`public/components/list-reader.js`（从 `public/twitter-radar.js` 的 `renderPostCard` / `renderPostDetail` / `renderRichContent` **复制并改成不依赖雷达页全局状态**的模块；**不改 twitter-radar.js 本身**）
- `server.py`：只允许新增 `GET /api/today`、`GET /api/today/item`、`POST /api/today/feedback` 三个路由的装配行（每个路由 ≤ 6 行，调用 `today_view.py`），不动其他行
- `public/workspace-nav.js`：导航最前面加「今日」一项；其余项不删不改顺序
- 新增 `tests/test_today_view.py`、`tests/e2e/today_page.cjs`
- 报告 `coordination/impl-today-p1-report.md` 与截图

### 1.3 不做
items 表、迁移、素材库页、删除功能、拿去写自动起草、改旧页面、改视频实验室（lab-test 保护边界）、改采集脚本。

## 2. 功能要求

### 2.1 接口
- `GET /api/today?range=today|48h|7d&tier=&source=&topic=&q=&page=`：只返回摘要字段（id、kind、sources、author、published_at、title/一句话摘要（≤120 字，只用于列表）、metrics 摘要、tier、topic、fit_score、doable、content_complete、status、feedback），每页 50 条。**不返回全文**。
- `GET /api/today/item?id=`：返回该条全文、长文、串推、引用、媒体、why、pattern、my_angle、outline、doable_resource、全部指标。
- `POST /api/today/feedback`：body `{id, action}`，action ∈ `accurate | inaccurate | keep | use | clear`。沿用 server 现有 Origin + `X-Article-Studio` 校验。写 `workspace/today/feedback.json`（临时文件 + fsync + rename + 锁）；带 G 来源的条目同时按现有 `grok_bot_radar.save_feedback` 写 Bot 镜像（复用，不另写一套）；带 V 来源的条目同时按 `video_radar_page_api.save_feedback` 写视频雷达反馈。
- 三个接口 5 次连续调用都 < 1 秒（真实数据）。

### 2.2 排序与「该做的 3 条」
- 置顶区「今天该做的 3 条」：同时满足 tier ∈ {爆款, 起势, 低粉爆款}、`fit_score ≥ 2`、`doable ≥ 2`，按 fit_score+doable 降序取前 3。不足 3 条就少给；0 条显示「今天没有值得做的」。Grok 报告还没有 fit_score / doable 字段时，置顶区显示「等 Grok Bot 按画像打分后出现」，**不许用别的规则凑数**。
- 其余：爆款 → 起势 → 低粉爆款 → 其他，同类按发布时间倒序。
- 去重：同一原帖 id 只出一行，来源图标并列。

### 2.3 页面
- 版面：规划第 4.1 节。左列表右阅读器，左右分栏可拖；窄屏（< 900px）改为列表 → 点开全屏阅读器 → 返回。
- 第一屏（1440×900）至少 5 条列表项可见；顶部只有一行状态（各来源最近更新时间）+ 一排筛选；run_id、覆盖率等技术信息只在某来源出错时以红字出现在状态行，点开看详情。
- 阅读器：完整正文、长文、串推、引用帖（嵌套卡）、图片缩略图、视频封面 +「播放原帖」；`content_complete=false` 或启发式判断不完整时显著提示「原文不完整，点开原帖看全文」+ 原因。
- 动作栏：打开原帖 / 准 / 不准 / 存 / 拿去写。「拿去写」本期 = 记 use + 新标签打开创作台 `index.html`，并把全文、链接、why、my_angle、outline 拼成一段 Markdown 复制到剪贴板，按钮旁提示「已复制，粘到创作台」。再次点当前选中的准/不准可撤销。
- 键盘：J/K 上下，O 打开原帖，Enter 聚焦阅读器。
- 点作者名：阅读器上方切换为该作者近 30 天在 3 个来源中的条目列表（读已有数据，不新采集）。
- 打开页面先显示骨架屏，列表 1 秒内到；阅读器详情点开后另取。

## 3. 该工人已知弱点（Codex 历史实施，见 skill `worker-dispatch-profiles`）与必测反例
交付前逐条自测，报告逐条给「通过 / 失败」+ 证据。未逐条自测直接退回。

| # | 弱点 | 反例 |
|---|---|---|
| 1 | 原文截断 | 2400 字中文长文 + emoji + 多行，阅读器 textContent 与源逐字一致；列表摘要可截，阅读器不能截 |
| 2 | 注入 | 正文含 `<script>alert(1)</script>`、`<img src=x onerror=alert(1)>`、`&amp;`，阅读器不生成任何对应节点 |
| 3 | 链接白名单 | `javascript:`、`https://x.com.evil.com/a/status/1`、空串、`//evil.com` 均不生成原帖链接；视频封面链接只放行 http(s) |
| 4 | 跨来源合并取数 | 同 id 在 G 和 A 都有、采样时间不同，指标取最新采样；正文取更完整的 |
| 5 | 并发写 | 两个并发 POST 不同 id，两条都在；同 id 先 accurate 再 clear，最终无记录；Bot 镜像与工作台文件一致 |
| 6 | 来源校验 | 缺 Origin / 错 Origin / 缺 `X-Article-Studio` 均 403 且不落盘 |
| 7 | 坏数据 | 某来源目录不存在、文件半写、数字是 `1.2万` / NaN / 布尔：接口 200，状态行标该来源异常，其他来源照常显示 |
| 8 | 凑数 | 所有条目都没有 fit_score：置顶区显示等待文案，不出现任何条目 |
| 9 | 首屏 | 1440×900 下列表可见条目 ≥ 5，首个列表项出现时间 < 1 秒（给 3 次数字） |
| 10 | 隔离 | 测试不写真实 `workspace/` 和真实 `~/grok-bot-reports/`；如在真实 8766 点按钮验收，测完恢复并给前后 SHA |
| 11 | 范围 | 1.2 节以外文件 0 改动（开工前存 `git diff` 与关键文件 SHA，交付时对照）；`twitter-radar.js` SHA 不变 |
| 12 | 旧页回归 | 旧推特雷达、视频雷达页面照常打开，各自既有 e2e 通过 |
| 13 | 组件依赖页面全局状态（上一单复核 M1，`coordination/review-grok-bot-radar-report.md`） | 在空白隔离页只引入 `components/item-card.js`，传入 `GET /api/today` 返回的双来源条目（sources 含 G 和 A），卡片按条目自身的 sources 渲染正确的按钮组；反馈状态与点击动作通过参数/回调传入，组件内不读任何页面级可变变量。给 `reuse` 测试：直接传、不注入任何全局状态 |
| 14 | 统一双来源详情漏显 Grok 独有内容（上一单复核 M2，原因见 `public/twitter-radar.js:2160-2162` 只认 `_grok`/`_sources` 下划线字段） | 空白页直接用 `/api/today/item` 返回的统一对象渲染阅读器：Grok 独有的长文、串推、引用、媒体，AIsa 独有的内容，均不丢；断言逐项存在 |

## 4. 验收
- 新增 Python 单测、隔离 Playwright e2e 全部通过；既有 `tests.test_grok_bot_radar`、视频雷达测试不回退（已知既有失败 `C02TimeoutKeepsOldTimestamps` 可豁免，需注明）。
- 改完重启 8766（`launchctl kickstart -k gui/$(id -u)/com.servasyy.article-studio`），确认新 PID 加载新代码，再用隔离 headless Playwright 打开真实 `today.html` 验收：首屏截图、点开 3 条（含 1 条视频、1 条不完整）、点准再撤销、拿去写剪贴板内容截图。
- **只用隔离 Playwright（独立 profile、/tmp 目录或随机端口）。禁止 computer use 操作黄总真实桌面或 Chrome。**
- Python 用 `../article-importer/.venv/bin/python`。
- 不提交、不推送。

## 5. 交付
报告 `coordination/impl-today-p1-report.md`，开头「决定要点」表（≤10 行：结论通过/不通过、H/M/L、改动文件与 SHA256、测试数字、首屏数字、接口耗时、8766 PID）。然后第 3 节 12 条反例逐条结果。发一条 `worker_done` 给主控 `term_b884acc7-d613-41d8-b736-0e3e84a18b37`。

## 6. 主控追加（2026-10-29 21:40，黄总看页面后问排序；黄总 21:45 指示「按你的想法先改」，**本单内完成**，计入第 3 节反例 15）
- 现状：today_view.py:253 同档位只按发布时间倒序，导致收藏 412 的帖排在收藏 111 的帖后面。
- 要求：同档位内改为按热度降序，热度相同再按时间倒序。热度 = views_per_hour（两个来源都有）；bookmarks ≥ 100 或 bookmarks/likes ≥ 1 的条目额外加权（画像「高收藏直接算爆款」）。视频条目用播放量/小时（无则用播放量）。
- 列表卡片上 AIsa 没有收藏时显示「收藏 未知」，不显示「—」。
- 反例：同档位 A（收藏 412、发得早）与 B（收藏 111、发得晚），A 必须在 B 前。
