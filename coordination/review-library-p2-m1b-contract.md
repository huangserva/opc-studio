# 合同：review-library-p2-m1b（Codex 定向复验 · Orca · 只读）

对象：2 期素材库 M1 第 2 轮返修 `task_5a0741452378`，报告 `coordination/fix-library-p2-m1b-report.md`，合同 `coordination/fix-library-p2-m1b-contract.md`。上一轮复核报告 `coordination/review-library-p2-report.md`（其余 10 项已通过，本次只验 M1 及其回归）。
产出：只写 `coordination/review-library-p2-m1b-report.md` 与 `/tmp/review-library-p2-m1b/` 证据；开头放决定要点表（≤10 行）。

## 规则
只用隔离 Playwright，禁 computer use；正式 8766（PID 71674）只做 GET；写入、删目录重建、增量同步只在 /tmp 副本 + 随机端口做；不改业务代码，不提交不推送。

## 复验项
1. SHA 与范围：报告所列文件 SHA 与磁盘一致；改动只落在合同允许文件；server.py 只增加装配/启动一行。
2. knowledge.db 只读：代码审查（mode=ro，无 DML/DDL/ATTACH 写），并在 /tmp 副本上比对查询、构建、增量同步前后的 SHA、大小和 mtime。
3. 完整性：在正式库只读副本上，对 微信、模型、视频、发布、抖音、鲸 6 个词，用全表 `title LIKE`（排除 trash）独立数出命中数，和接口结果比对（≥50 时接口返回 50 条且标为 ≥50；<50 时条数一致）；抽 3 条核对排序是不是新到旧。
4. 首查性能：等正式服务空闲 ≥5 分钟，不预热，选 3 个本轮从没查过的短词（自选，例如「推特」「爆款」「剪映」），加上「微信」，每个查 3 次，每次都要 <1 秒，报原始数字。
5. 构建与降级：在 /tmp 副本上删掉 research_catalog.db 后启动服务，构建期间搜索有提示、不报错；构建完成后结果完整；库文件损坏时能重建。
6. 增量与 trash：在 /tmp 副本上新增 1 条文档，超过 60 秒能搜到；放进 trash 后从结果里消失。
7. 页面：隔离浏览器里在资料检索搜短词，能看到结果；部分结果或构建中的提示文案与 `partial`/`catalog_state` 一致。另外留意：未分页且结果 <50 条时总数显示「≥9」这类下界，看是否应改成精确数，记为 L 或不记，写明理由。
8. 回归：Python 60 项、素材库 31 项、今日页 56 项独立跑。

H = 写了 knowledge.db / 数据错误；M = 完整性或性能不达标；L = 文案瑕疵。完成后 `orca orchestration send --type worker_done`，payload 带 reportPath。
