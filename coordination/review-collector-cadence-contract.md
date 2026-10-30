# 复核合同：推特雷达改每 2 小时、视频榜改每天 21:20

- 派给：Codex term_02017284（检查窗口；实施是 OpenCode term_1e842060，不同窗口）。
- 被复核交付：`/tmp/collector-cadence-deliver/`（补丁 `collector-cadence.patch`，14 个文件，SHA256 `2c5edf8ce13bbda7e87b181e8c613135ed6a5b661d6ba1e6784c6ebc09f16e17`）。实施合同：`coordination/impl-collector-cadence-contract.md`；实施报告：`coordination/impl-collector-cadence-report.md`（实施方自评 6 条反例全过，视频 Python Ran 132、e2e 视频 60/60、推特 43/43）。
- 背景：黄总 2026-10-27 为降 AIsa 费用定新节奏：推特雷达 06–22 点每 2 小时（9 轮，06 点基线不推送），视频榜每天 21:20 一次。两个任务现已暂停。复核通过后主控另派上线（替换 LaunchAgents 并重新加载）。
- 主控抽检已看过：补丁 SHA 与 README 一致、正式目录 `git apply --check` 通过、两个 plist 时点正确且其他键与 `~/Library/LaunchAgents` 现版一致、`end_hour=22`、两处 `STALE_AFTER` 新值正确。另发现：`collector-cadence.patch.sha256` 文件里写的是旧值 `8e6981a8…`，与补丁实际 SHA 不符，请核实并定级。

## 复核要点（逐条给 通过/失败/未知）
1. **交付一致**：补丁实际 SHA256 = `2c5edf8c…`；在 `/tmp/collector-cadence-review/` 新副本（从正式目录复制代码，不复制正式 `workspace/`）`git apply --check` 与 apply 都通过；改动文件只有实施合同「允许改动」表里的文件，没有越界。
2. **plist**：两个 plist `plutil -lint` OK；推特 9 项（6、8、…、22 时，Minute 0），视频 1 项（21:20）；除 `StartCalendarInterval` 外与 `~/Library/LaunchAgents/com.servasyy.radar-monitor.plist`、`com.servasyy.video-radar.plist` 逐键相同（`RunAtLoad` 仍为 false，路径、环境变量不变）。
3. **日报与基线**：独立验证 22 点那轮（非基线、未损坏）产生 1 条 `daily_summary`，汇总 9 轮；23 点 `outside_hours`；06 点仍是基线、零外部效果（不推飞书）。顺带评估 `fault_after_rounds: 2` 在 2 小时间隔下意味着故障 4 小时才报警，是否需要提醒黄总（只评估，不要求改）。
4. **增量语义**：确认推特增量按两次 `fetched_at` 实际间隔折算每小时（`radar_monitor.py` 约 676–679 行），2 小时间隔下 `hourly_delta ≥ 5000/h` 等阈值语义不变，不会因间隔变长而翻倍误报或漏报。
5. **过期边界**：推特监控页 `generated_at` 距今 2h10m → ok、150m → ok、150m+1s → stale；视频榜小红书/抖音实时 24h30m 不过期、25h1m 过期；抖音日榜 30 小时边界不变。页面文案与后端阈值一致，全仓 `rg` 不再有暗示每小时更新或 70 分钟的残留（确有保留的，说明理由是否成立）。
6. **断言没被削弱**：逐条核对测试 diff「旧期望 → 新期望」，无删除、无放宽。独立做三次故意改错，对应测试都必须红再改回：①`STALE_AFTER` 小红书改回 70 分钟；②`end_hour` 改回 23；③推特 plist 加回一个 23 点时点。
7. **测试**：`tests/test_video_radar*.py` 全绿（报 Ran N，应 ≥132）；`tests/test_radar_monitor*.py`、`tests/test_radar_page_api.py` 全绿（报 Ran N）；隔离 Playwright 跑 `tests/e2e/video_radar_page.cjs`、`tests/e2e/twitter_radar_monitor_page.cjs` 全过（报数字，pageerror 0）。
8. **采集代码不变**：`scripts/radar_monitor.py`、`scripts/video_radar.py`、两个 wrapper 脚本、`server.py`、`style.css` 与正式目录逐字节相同。

## 约束
- 只读正式目录；在 `/tmp/collector-cadence-review/` 工作；不改正式目录、不写正式 `workspace/`、不重启 8766、**不碰 launchd（不 enable、不 bootstrap、不改 ~/Library/LaunchAgents）**、不做任何 git 写操作。
- **真实 AIsa 调用 0 次。** 浏览器只用隔离 Playwright（独立 profile、/tmp 副本、随机端口）；**不得用 computer use 操作黄总的真实桌面或 Chrome**。e2e 需要兄弟目录时在 /tmp 父目录 `cp -R` 复制 `article-importer/scripts`，不软链。
- Python 用 `/Users/huangzongning/development/article-importer/.venv/bin/python`。

## 交付
- 报告写到 `coordination/review-collector-cadence-report.md`，开头「决定要点」表（≤10 行：结论【通过/不通过】、H/M/L、补丁 SHA256、8 条结果、各组 Ran N 与 e2e 数）。完成后用 Orca 发 worker_done。
