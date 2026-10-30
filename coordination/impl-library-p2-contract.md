# 合同：impl-library-p2（Codex 实施 · Orca）：重新设计 2 期「素材库」

上游：`coordination/redesign-plan.md`（第 3、5、6、7 节）+ 本合同第 0 节的范围修正。冲突时以本合同为准。
派工条件：1 期 task_61609dae2ac7 复核收口后派。
依据调查：explorer 子代理 2026-10-29 21:55 只读报告（要点抄在第 0 节）。

## 0. 范围修正（主控决定，黄总已授权按规划连续推进）
调查结果：knowledge.db 有 29.2 万份文档，其中微信消息 26.5 万、projects 1,130、github 846、gmail 95；真正「人存下来的东西」只有 external-references 19、articles 19、manual 10、remio 11，加上创意回收 18 条、提示词卡 19 张。clues 4,862 条是机器产出的候选线索。
所以 **items 只装「素材」（人决定存下的东西），不把 29 万份语料搬进来**：
- 搬进 items：external-references、articles、manual、remio 四个来源的文档；intake drops；cards（作为 kind=card 子条目）；今日页点过「存」「拿去写」的条目。
- 不搬：微信、projects、github、gmail 等语料留在 knowledge.db，作为「资料检索」的数据源，素材库页提供快速检索。
- clues 本期不动（4 期再定去留）。
- knowledge.db 本期**只读**：不改表结构、不写、不删；`knowledge_store.py` 不改。

## 0.1 1 期收口遗留（主控决定，并入本期）
1 期复核（`coordination/review-today-p1-report.md` 第 3 节）确认高收藏加权 `W=1+B/100` 无上限：dotey（AIsa，每小时 9,330 浏览、收藏未知）排在 eternityspring（每小时 2,594、收藏 412，W=5.12）之后。主控决定浏览速率为主、收藏只作加分：
- `today_view.py` 中 `W` 封顶 3：`W=min(3, 1+B/100)`；其余规则（高收藏判定、未知收藏 W=1、同热度按时间倒序）不变。
- 反例 16：同档位 R=9330/B 未知 与 R=2594/B=412，前者必须排前；R=1000/B=412 与 R=1000/B=111，前者仍排前（加权仍生效）。断言具体 id 先后。
- 原 1 期 22 项 Python 测试中若有断言依赖无上限倍率，改为新规则并在报告里逐条列出改了哪条、为什么。

## 1. 允许改动的文件（唯一归属）
- 新增 `items_store.py`（items 库：`workspace/items/items.db`）
- 新增 `scripts/migrate_to_items.py`（只读旧库 → 写 items 库，幂等）
- 新增 `public/library.html`、`public/library.js`、`public/library.css`
- 复用 `public/components/item-card.js`、`list-reader.js`（可改，保持 1 期今日页不回退）
- `today_view.py`、`public/today.js`：「存」「拿去写」改为写 items（保留 1 期的反馈文件写入）
- `server.py`：只新增 `/api/items*`、`/api/library/search` 路由装配行；`POST /api/knowledge/reference` 成功后**追加一行**调用 `items_store.upsert_from_reference(...)` 镜像写 items（失败只记日志，不影响原接口返回）。其他行不动。1 期已为 /today.html 加了 CSP 例外（img-src 加 `https:`），本期允许把同一例外扩到 /library.html，写法与 1 期一致，不放 http:。
- `public/workspace-nav.js`：加「素材库」（今日之后）；「知识库」「创意回收」「提示词卡」三项移入导航底部「旧版」分组（不删，路径不变）
- 新增 `tests/test_items_store.py`、`tests/test_migrate_to_items.py`、`tests/e2e/library_page.cjs`
- 报告 `coordination/impl-library-p2-report.md` 与截图

## 2. 数据模型（items.db）
- `items`：规划第 5 节字段，另加 `legacy_refs`（JSON：`[{"store":"knowledge","id":"<32位旧id>"},{"store":"intake","id":...},{"store":"cards","id":...}]`）、`parent_id`（card 指向其素材）、`trashed_at`、`note`（收藏理由）、`attachments_json`（intake 附件路径与 meta，文件不复制）。
- 规范 id：x.com → `x:<tweet_id>`；mp.weixin → `wechat:<sha256(url)[:16]>`；github → `github:<owner>/<repo>`；抖音/小红书沿用 today_view 的 `douyin:`/`xhs:`；其他有 url → `url:<sha256(规范化url)[:16]>`；无 url → `kb:<旧id>`；卡片 → `card:<prompt_sha>`。
- `items_fts`：FTS5 `tokenize='trigram'`，覆盖 title、full_text、article_text、author_handle、note、tags；触发器维护。
- 写入：临时事务 + `BEGIN IMMEDIATE`；upsert 按 id 合并 sources 与 legacy_refs，不覆盖人写的 note/status/feedback。

## 3. 接口
- `POST /api/items`（upsert，同 Origin + `X-Article-Studio` 校验）、`GET /api/items?q=&source=&kind=&tag=&status=&trashed=0|1&page=`（只回摘要，每页 50，`total` 用 FTS 或索引计数，不做全表 LIKE）、`GET /api/items/<id>`（全文）、`POST /api/items/<id>/trash`、`POST /api/items/<id>/restore`、`DELETE /api/items/<id>`（只允许删已在回收站的，且 cards 子条目一起处理；被其他 item 的 parent_id 引用时拒绝并说明）。
- `GET /api/library/search?q=&source=&page=`：只读 knowledge.db（`file:...?mode=ro`），**只走 `document_fts MATCH`**，用 `snippet()` 出摘要，`LIMIT 50`，不 `SELECT body`，不做全表 `COUNT(*)`（总数给「≥50」或用 `LIMIT 501` 判断是否「500+」）；查询词 < 3 字时只对 `title LIKE` 且 `LIMIT 50`，并在返回里标 `short_query: true`。排除 `document_trash` 里的条目。
- 性能门槛：两个列表接口在真实数据上 5 次均 < 1 秒；`/api/library/search` 用「agent」「微信」「MiniMax H3」「4090」4 个词各测 3 次均 < 1 秒。

## 4. 页面（library.html）
- 两个标签：「我的素材」（items）与「资料检索」（knowledge 语料）。
- 复用 list-reader：左列表右全文；J/K；搜索框 300ms 防抖。
- 「我的素材」：按来源/类型/标签/状态筛；动作：拿去写（同 1 期逻辑）、编辑收藏理由、移到回收站、恢复、彻底删除（二次确认）。底部「回收站」入口。
- 「资料检索」：只读，结果显示来源、标题、snippet 高亮、时间；点开右侧看全文（`/api/knowledge/document/<id>` 现有接口）；动作只有「存为素材」（POST /api/items）。
- 首屏 < 1 秒出列表（给 3 次数字）。

## 5. 迁移脚本
- `migrate_to_items.py --workspace <dir> [--dry-run]`：只读打开 knowledge.db / intake.db / cards.db（`mode=ro`；knowledge.db 若 ro 打不开，按调查用 `immutable=1`），写 items.db。
- 幂等：重复跑结果一致（条数、内容 hash）。
- 输出对账表：每个旧来源的旧条数 / 新条数 / 合并数 / 跳过数及原因。
- 先在 `/tmp` 副本（复制真实 workspace 的三个库到 /tmp）跑通并对账，再对真实 workspace 跑一次（真实跑前备份 items.db 不存在也要记录）。

## 6. 该工人已知弱点与必测反例（逐条给 通过/失败 + 证据；未逐条自测直接退回）
| # | 弱点 | 反例 |
|---|---|---|
| 1 | 旧库被写 | 迁移与所有新接口运行前后，knowledge.db / intake.db / cards.db 的 SHA256 不变（真实库与 /tmp 副本都测） |
| 2 | 迁移不幂等 | 同一副本连跑 2 次，items 条数、每条内容 hash 完全一致 |
| 3 | 旧 id 映射丢失 | 每条迁移素材的 legacy_refs 能反查回原库原行；cards 的 refs 指向的 document 都能在 items 里找到对应 parent |
| 4 | 规范 id 冲突 | 同一 x.com 链接分别来自 external-references 和 intake，只生成一条 `x:` item，两个 legacy_refs 都在 |
| 5 | 原文截断 | 2400 字中文 + emoji 的文档迁移后 full_text 逐字一致；阅读器逐字一致 |
| 6 | 注入与链接 | 同 1 期反例 2、3（标题、正文、note、snippet 高亮都要测；snippet 的高亮标记不能变成可注入 HTML） |
| 7 | 搜索慢 | 第 3 节性能门槛；`EXPLAIN QUERY PLAN` 证明 `/api/library/search` 不做全表扫描 |
| 8 | 短词 | 查「AI」返回 `short_query: true` 且 < 1 秒 |
| 9 | 删除边界 | 未进回收站直接 DELETE → 拒绝；被 parent_id 引用 → 拒绝；回收站恢复后内容一致；彻底删除后 FTS 也查不到 |
| 10 | 来源校验 | 所有写接口缺 Origin / 错 Origin / 缺 `X-Article-Studio` → 403 不落盘 |
| 11 | 并发 | 两个并发 POST /api/items 同 id（不同 sources）→ 最终一条，sources 合并；并发不同 id → 两条都在 |
| 12 | 镜像写不影响原接口 | items.db 不可写（chmod 只读的 /tmp 副本）时 `POST /api/knowledge/reference` 仍按原逻辑成功返回 |
| 13 | 今日页回退 | 1 期 e2e 全部仍通过；「存」之后素材库能看到该条 |
| 14 | 范围 | 第 1 节以外文件 0 改动（开工前存 diff 与 SHA 对照）；`knowledge_store.py`、`intake_store.py`、`intake_cards.py`、`clues_store.py` SHA 不变 |
| 15 | 旧页可用 | knowledge、intake、cards 三个旧页仍能打开，各自既有 e2e 不比基线差（基线既有失败需在 /tmp 副本复现后列明） |

## 7. 验收
- 新增单测、隔离 e2e 全过；改完重启 8766，确认新 PID，再用隔离 headless Playwright 在真实页面验收：素材库首屏截图、搜一个词、存一条资料为素材、移到回收站再恢复。
- **只用隔离 Playwright（独立 profile、/tmp 目录或随机端口）。禁止 computer use 操作黄总真实桌面或 Chrome。**
- Python 用 `../article-importer/.venv/bin/python`。不提交、不推送。

## 8. 交付
报告 `coordination/impl-library-p2-report.md`，开头「决定要点」表（≤10 行：结论、H/M/L、改动文件与 SHA256、迁移对账数字、接口耗时、测试数字、8766 PID）。然后第 6 节 15 条逐条结果。发一条 `worker_done` 给主控 `term_b884acc7-d613-41d8-b736-0e3e84a18b37`。
