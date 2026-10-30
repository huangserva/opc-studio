# 合同：fix-p4-4-1（Codex 实施返修 · Orca）

上游：4-1 `task_d4368dc998ad`；复核报告 `coordination/review-p4-4-1-report.md`（0H3M0L，第 18–20 行写明了 M1–M3 的根因和证据位置）。本次只修这 3 个 M，其余项都已通过，不得回退。通用规则沿用 `coordination/impl-finish-p4-contract.md`，其中包括：禁止输出服务环境变量、只用隔离 Playwright、不提交不推送。

## 要修的 3 项
- **M1 推特「最近成功」**：现在直接用了 `state.runs` 的小时槽位键（`strftime('%Y-%m-%dT%H')`）。改成按成功 run_id 找到对应快照，读取其中真实的 `finished_at`，带时区，到秒。找不到就显示「未知」并写明原因，不许补 `:00:00` 冒充。
- **M2 AIsa 数据源**：今日页的 AIsa 数据来自 `public/twitter-radar-bundle.json`（经 `today_view.snapshot → grok.radar_view → grok._bundle`），不是 `aisa.db`。最近成功时间改读 bundle 的 `collected_at` 或 run_id 对应快照。和今日页 `sources.A.updated_at` 用同一条读取路径，最好直接复用同一个函数，并在 reason 里写明数据源。
- **M3 intel 漏报错误**：现在只查 `status IN ('error','failed')`。按 `intel_store.py` 的实际状态语义（约 155、962、1875、2169 行），把 `rate_limited`，以及 detail 里能明确认定为失败或限流的 `partial` 都识别出来，显示时间和原因。正常的 partial 不当作失败。
- 顺带：README 和 product-scope 两处都要明确写出 2026-11-29；复核报告里如果记了 L，也一起修。

## 允许改动
`settings_view.py`、`public/settings.js`（只限显示），`tests/test_settings_view.py`、`tests/e2e/settings_page.cjs`，`README.md`、`docs/product-scope.md`（只限日期一句），报告。其他文件一律不动。如果复用 today_view 的函数需要改它的签名，先问主控。

## 反例（逐条自测，给原始数据）
1. 正式 overview：推特 last_success 等于 run `20261029-140008-f6d22d61` 快照里的 finished_at（或者之后更新的成功 run），精确到秒。
2. 正式 overview 里 AIsa 的时间，和 `/api/today?source=A&range=48h` 的 `sources.A.updated_at` 一致。
3. /tmp 合成库：只有 `rate_limited` 加原因时，last_error 有值；只有正常 partial 时，last_error 为空；有失败类 partial 时，last_error 有值。
4. 每个来源的 reason 都写明了读的是哪个文件或哪张表，页面悬停可以看到。
5. 读取仍然只读：不调用连接器，不调用 collect/intel status，SQLite 用 mode=ro。回归：Python 11 项、UI 33 项、Playwright 23 项全过；全量 Python 新增失败 ID 为 0（`test_aisa_live_chain` 的偶发锁竞争已知，单独重跑证明即可）。
6. 改完后端用 launchctl 重启，确认新 PID。

报告：`coordination/fix-p4-4-1-report.md`，开头放决定要点表（≤10 行）。完成后 `orca orchestration send --type worker_done`。
