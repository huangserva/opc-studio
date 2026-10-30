# 素材库测试数据清理 + 测试写真实库源头修复（第二阶段，实施窗口 A term_02017284）

依据：黄总 2026-10-30 15:1x 决定（覆盖之前「暂不清理」）；第一阶段只读核查 `coordination/check-items-test-data-report.md`（第 4 节方案、第 3 节 5 个入口）和清单 `coordination/items-test-data-candidates.json` / `.md`（task_3ccd76ebf5e3，B 交付）。复核派 B（term_57dd3162），排在 K0 之后。

## 黄总决定
1. 39 条当前活跃的「疑似测试」按第二阶段方案处理：先备份，再写 trashed_at 放进回收站（可恢复，**不硬删、不写 dismissed、不用 SQL DELETE**）。已在回收站的 `kb:5d9fc0775cb250b8506f0eaebc56caae` 保持原状。5 条「不确定」和 62 条「真实」一律不动。
2. 修 5 个带 `--real` 会写真实 items 的 E2E 脚本：`tests/e2e/library_page.cjs`、`today_page.cjs`、`today_t5.cjs`、`write_and_publish.cjs`、`interest_channel.cjs`。**任何情况下都不能写真实素材库，只能写隔离副本**；补反例测试证明。
3. **不重跑导出**：不手动触发 H5 导出任务、不改 `library-export.json`，明早 08:30 定时导出自然生成干净结果。可以把导出脚本输出到 /tmp 做一次预演计数。

## 一、数据清理（先做）
1. 开工前：GET 调度状态，确认没有正在运行、会写 items 的任务；有就等它自然结束，不取消。
2. 备份：用 SQLite backup API（只读连接）把一致快照存到 `/tmp/items-cleanup-<UTC时间>/items.before.db`，不要直接复制裸 db 而漏掉 WAL；另存 39 条的逐行原状态（status、trashed_at、parent_id、完整行 SHA256）、当前 `library-export.json` 副本及其 SHA。备份含私人素材，只留本机 /tmp，报告里不抄正文。
3. 执行前逐行比对：39 条的完整行 SHA 必须与 candidates.json 一致；不一致的跳过并在报告列出，不按日期或关键词批量更新。
4. 级联预演：`items_store.py:282` 父素材进回收站会带走同父 card。执行前重查这 39 条的子项，若有不在 39 条里的子 card 被连带，停下问主控。
5. 执行：走产品现有的「回收」动作（素材库页面用的同一个 API / `items_store.action('trash')` 语义），逐条执行并记录返回；不直接 UPDATE。
6. 回滚清单：`/tmp/items-cleanup-<时间>/rollback.json`（39 个 ID + 原 trashed_at）；回滚只对这批 ID 做 restore，不覆盖之后用户新增或修改的内容。报告里写清回滚命令。
7. 验证：总行数仍 107（或 107 + 期间正常采集新增，新增行要能归因）；回收站 40 条（39 + 原有 1）；5 条不确定和 62 条真实逐行 SHA 不变；「我的素材」列表和搜索不再出现这 39 条；H5 导出条件下的候选数 106→67（导出脚本输出到 /tmp 预演，不覆盖正式导出文件）；在 /tmp 副本上重跑迁移，确认不会把软删的行恢复。
8. 真实用户路径：隔离 Playwright（独立 profile）只读打开真实 8766 的素材库页和回收站，截图确认；**不在真实页面上点 restore 或任何写操作**（restore 可恢复性在 /tmp 副本上验证）。

## 二、5 个 E2E 脚本源头修复
1. 目标：这 5 个脚本无论带不带 `--real`、无论传什么 base/端口/workspace，都不能向真实 8766 或正式 `article-studio/workspace` 做任何写操作（items、作品、feedback、兴趣方向文件都算写）。写流程（存为素材、拿去写、建底稿、点存、回收/恢复等）一律在 /tmp 副本 + 随机端口上跑。`--real` 若保留，只能是只读（GET）模式，并在输出里明确标注；也可以直接去掉写分支，二选一在报告里说明理由。
2. 拦截要覆盖所有通道：浏览器 `page.route` / `context.route`、`context.request` / `APIRequestContext`、Node 端 `fetch` / `http`，以及页面内重定向和跨端口跳转。生产判定不能只认字面 `127.0.0.1:8766`：`localhost`、`[::1]`、`0.0.0.0`、带尾斜杠或路径的 base、3000 等其他生产端口都要算；workspace 路径要先 `realpath` 归一（防符号链接指回正式目录）。
3. 写一个共用防护（例如 `tests/e2e/real_write_guard.cjs`），5 个脚本都用它；`knowledge_first.cjs`、`batch_delete.cjs` 两个 helper 被单独接到生产 base 时也会经由镜像写 items，一并加上 base 断言。
4. **只改测试代码**，不改 server.py、items_store.py 等产品代码，不让产品正常写入受影响，因此**不需要也不得重启 8766**。
5. 反例测试（必须真跑）：
   - 在 /tmp 起一个「伪生产」副本（随机端口，用测试可配置的生产判定把它当成生产），每个脚本带 `--real` 跑一遍：副本 items.db 全行摘要、作品目录、feedback 和兴趣文件前后完全一致，防护日志记录被拦下的写请求。
   - 证明测得出错：在 /tmp 副本里把防护故意去掉或改坏，同一组反例必须报失败（逐个脚本给 F 的数量）。
   - 别名和绕过反例：`localhost`、`[::1]`、`0.0.0.0`、符号链接 workspace、通过 `context.request` 直接 POST、页面重定向到生产端口，各至少 1 条，必须全部被拦。
   - 正常（不带 `--real`）模式原有用例全部照常通过，给出修改前后的通过数对比。
6. 真实路径收尾：隔离反例全部通过后，可以对真实 8766 用修好的 `--real` 各跑 1 次（只读模式），前后对比真实 items.db 全行摘要、feedback 与兴趣文件 SHA；期间正常采集新增的行要能归因，否则停下报告。

## 允许改动
- 数据：仅对上述 39 条执行回收动作（workspace/items/items.db 由产品 API 写入）。
- 文件：上述 5 个脚本、`tests/e2e/knowledge_first.cjs`、`tests/e2e/batch_delete.cjs`、新增 `tests/e2e/real_write_guard.cjs` 及其测试、`tests/e2e/run.cjs` / `tests/e2e/server_fixture.py`（仅限接入防护所必需的改动，报告里单列 diff）、报告和证据。这几个文件在本单收口前归本单独占；R4 的 today e2e 时间边界修复以本单结果为基础。
- 不碰：H3a M2 的 `scripts/radar_common.py`、`today_filter_config.py`、`tests/e2e/today_h3a_m2.cjs` 等文件（M2 仍在复核）；任何产品代码、调度、plist。

## 约束
- 浏览器只用隔离 Playwright（独立 profile、/tmp 副本或随机端口），禁止 computer use，不碰黄总的真实桌面或 Chrome。
- 不重启 8766，不触发 H5 导出任务，不改 `library-export.json`。
- 禁止 launchctl print / ps eww / env / 打印 plist；报告不出现 Key、聊天原文、商单正文。
- 跨版本对照只在 /tmp 副本做，不就地覆盖共享工作区文件。
- Python 用 `../article-importer/.venv/bin/python`。不提交不推送。

## 该工人已知弱点与自测（skill worker-dispatch-profiles：Codex 历史实施）
- **输入/授权边界**：只动 39 个 ID。自测：执行前后分别列出被改动行的 ID 集合，与 candidates.json 中「疑似测试且 trashed_at 为空」的集合逐个相等；5 条不确定、62 条真实、已回收 1 条的行 SHA 前后不变。
- **整条链路与并发状态遗漏**：不能只验数据库。自测：数据库、「我的素材」列表与搜索、回收站、H5 预演计数、迁移重跑、级联子项，每一环给证据；执行期间如有新写入，说明怎么区分。
- **只读保护与继承风险**（上一轮 X 重定向漏拦、AIsa 继承旧鉴权头）：防护不能只拦一个通道。自测：上面第二部分第 5 条的全部别名、重定向和 `context.request` 反例逐条给「通过/失败」，并给出「去掉防护后报 F」的证据。

## 交付
`coordination/impl-items-cleanup-report.md`：开头「决定要点」表（≤10 行：结论、H/M/L、改动文件与 SHA、执行的 39 个 ID 数、跳过数、测试数字、备份与回滚路径），然后是逐条自测结果。完成后 worker_done。
