# 合同：review-p4-4-1-fix（Codex 定向复验 · Orca · 只读）

对象：4-1 返修 `task_1dc48ac12cb1`，报告 `coordination/fix-p4-4-1-report.md`，合同 `coordination/fix-p4-4-1-contract.md`。上一轮复核报告 `coordination/review-p4-4-1-report.md` 的其余项都已通过，本次只验 M1–M3、文档日期和回归。主控在返修中的答复：AIsa 返回 bundle.collected_at（ISO 带时区），与今日页 `sources.A.updated_at` 按时间等价验收；正常的 partial（包括「失败 0 篇」）不算失败。
产出：只写 `coordination/review-p4-4-1-fix-report.md` 与 `/tmp/review-p4-4-1-fix/` 证据；开头放决定要点表（≤10 行）。

## 规则
只用隔离 Playwright，禁止 computer use。正式 8766（PID 72793）只做 GET。不得输出服务环境变量。不改业务代码，不提交不推送。

## 复验项
1. SHA 与范围：5 个文件的 SHA 一致，改动只落在允许文件里。
2. M1：正式推特 last_success 必须等于最近一次成功 run 快照里的 finished_at，自己找到对应快照去核对；/tmp 合成状态下，没有快照时显示「未知」，不补零。
3. M2：正式 AIsa 时间与 `/api/today?source=A&range=48h` 的 `updated_at` 指向同一秒；读代码确认复用的是今日页的读取路径，不再读 aisa.db。
4. M3：在 /tmp 合成 intel 库上分别放 rate_limited、失败类 partial、正常 partial（含「失败 0 篇」）、成功，检查 last_error 的有无和内容是否正确；正式库的 last_error 要指向 10-11 那次后台限流 200013。
5. reason：每个来源都写明数据源，页面悬停能看到。
6. 文档：README 和 product-scope 两处都写明 2026-11-29。
7. 只读：overview 前后连接器调用计数为 0，SQLite 用 mode=ro。
8. 回归：专项 Python 15、UI 33、Playwright 23；Python 全量按 ID 子集判定，新增为 0；UI 全量只有 4 项既有失败。

完成后 `orca orchestration send --type worker_done`，payload 带 reportPath。
