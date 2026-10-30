# 合同：fix-library-p2-m1b（Codex 实施返修第 2 轮 · Orca）

上游：`coordination/fix-library-p2-m1-contract.md`、`coordination/fix-library-p2-m1-report.md`（第 1 轮采用方案②，速度达标）。

## 主控抽检结论：第 1 轮不收
正式 8766 实测：`q=微信`、`q=模型` 都只要 0.07 秒，但都是 **0 条**（partial=true，scanned_count=8192，total_label ≥0）。29 万份资料里只扫了最近 8192 条，常用短词搜不到任何东西。速度达标了，但功能等于没有。这是主控合同给的方案②本身的问题，不算工人违约。本轮改用方案①。

## 要求
- 在 `workspace/items/` 下建素材库专用的**标题目录旁路库**（例如 `workspace/items/research_catalog.db`）：只存 `rowid`、`source`、`title`、`updated/created` 和是否在 document_trash 里，不存 body。从 knowledge.db 用 `mode=ro` 只读、按 rowid 水位增量同步。首次全量构建放后台线程，或用独立脚本，不阻塞页面，构建期间沿用第 1 轮的限时扫描并给出提示。之后每次查询前，只要距上次同步超过 60 秒，就做一次轻量增量同步（只读 rowid > 水位的行，限时）。trash 变化也要能反映出来，比如同步时顺带比对 trash 表最大 rowid 或行数。
- 短词（1–2 字）在旁路库按标题 LIKE 搜**全库**，最多 50 条，按新到旧排序，总数显示下界。长词照旧走 knowledge.db 的 FTS + snippet。
- 第 1 轮的时间预算保留，只作兜底：旁路库不存在或损坏时才启用，并如实提示。
- knowledge.db 仍只读：不写、不加表、不加索引；`knowledge_store.py` 不改。旁路库坏了就删掉重建，不影响主功能。
- 允许改动：`items_store.py`（或新增 `library_search.py`）、`server.py`（仅限 `/api/library/search` 装配行，以及在启动时触发后台构建的一行）、`public/library.js`（提示文案）、`tests/test_items_store.py`、`tests/e2e/library_page.cjs`，可新增 `scripts/build_research_catalog.py`、报告。

## 反例（逐条自测，报告给原始数字）
1. 正式 8766：`微信`、`模型`、`视频`、`发布`、`抖音` 各自的命中数要与 knowledge.db 只读全表 `title LIKE` 的命中数一致（或在 ≥50 时都是 ≥50），不得为 0 条的假空页。
2. 重启后静置 ≥5 分钟、不预热，上述 5 个词加 `鲸` 各首查 3 次，每次都 <1 秒，报原始数字。
3. 全新构建：删掉旁路库后重启，构建期间搜索有提示且不报错；构建完成后结果完整。报告写明全量构建用了多少时间、旁路库多大。
4. 增量同步：在 /tmp 副本上往 knowledge 写一条新文档，超过 60 秒后能搜到；把一条文档放进 trash 后，它从结果里消失。
5. knowledge.db 的 SHA、大小、mtime 在只读路径前后不变，用 /tmp 副本证明。正式库因为后台采集会自己变化，不能只靠整库 SHA 下结论，要配合代码审查。
6. 回归：Python 54 项、素材库 30 项、今日页 56 项不回退。后端改完用 launchctl 重启，确认新 PID 加载了新代码。只用隔离 Playwright，禁 computer use；不提交不推送。

报告：`coordination/fix-library-p2-m1b-report.md`，开头放决定要点表（≤10 行）。完成后 `orca orchestration send --type worker_done`。
