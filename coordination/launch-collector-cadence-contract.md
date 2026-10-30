# 上线合同：采集降频补丁合入并重新启用两个定时任务

- 派给：Codex term_02017284（本线复核窗口；实施是 OpenCode term_1e842060，已关闭）。
- 被上线交付：`/tmp/collector-cadence-deliver/collector-cadence.patch`（14 个文件，SHA256 `2c5edf8ce13bbda7e87b181e8c613135ed6a5b661d6ba1e6784c6ebc09f16e17`；改后各文件 SHA256 见同目录 `file-sha256.txt`）。实施合同 `coordination/impl-collector-cadence-contract.md`，复核报告 `coordination/review-collector-cadence-report.md`。
- 复核结论 0H1M1L，主控决定（2026-10-28）：
  - **M1 接受为既有基线例外**：`test_radar_monitor_b1b`、`b1b2`、`b1c` 依赖 `/tmp/viral-b2b-deliver/`、`/tmp/viral-b1c-deliver/` 的历史交付物，10-23 电脑重启清空 /tmp 后缺失；改前改后失败同名同因，与本补丁无关。上线门槛改为：这 3 个模块的失败集合与复核报告记录的基线完全一致，其余推特模块全绿。
  - **L1 不阻塞**：sidecar `.sha256` 文件过期，以实际 SHA `2c5edf8c…` 为准。
- 新节奏：推特雷达 06、08、…、22 点（9 轮，06 点基线不推送）；视频榜每天 21:20。两个任务 10-27 14:16 起处于 `disable` + `bootout` 暂停状态。

## 步骤
1. **备份**：把正式目录 14 个目标文件、`~/Library/LaunchAgents/com.servasyy.radar-monitor.plist`、`~/Library/LaunchAgents/com.servasyy.video-radar.plist` 复制到 `/tmp/collector-cadence-launch-backup/`，记录 SHA256。
2. **合入**：在正式目录 `git apply --check` 后 `git apply`；核对 14 个文件改后 SHA256 与 `file-sha256.txt` 一致；`git status --short` 除这 14 个文件外与合入前相同（正式目录里有其他线未提交的改动，一个都不能动）。
3. **定向测试（正式目录）**：`tests/test_video_radar*.py` Ran 132 OK；推特相关模块（`tests/test_radar_monitor*.py`、`tests/test_radar_page_api.py`）除上述 3 个模块外全绿，3 个模块的失败集合与复核报告记录的基线一致。
4. **重启 8766**：`launchctl kickstart -k gui/$(id -u)/com.servasyy.article-studio`，确认新进程 PID、端口 8766 可访问，并确认加载了新代码（例如视频榜接口返回的 `stale_after_minutes` 为 1500/1500/1800）。
5. **替换启动配置**：把 `scripts/launchd/com.servasyy.radar-monitor.plist`、`scripts/launchd/com.servasyy.video-radar.plist` 复制到 `~/Library/LaunchAgents/`；`plutil -lint` 都 OK；与备份逐键对比，只有 `StartCalendarInterval` 不同。
6. **重新启用**：两个 label 各执行 `launchctl enable gui/$(id -u)/<label>` 和 `launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/<label>.plist`。用 `launchctl print gui/$(id -u)/<label>` 和 `launchctl print-disabled gui/$(id -u)` 确认：已加载、未禁用、时间表为新节奏（推特 9 个时点，视频榜 1 个）。写明两个任务各自的下一次运行时间。
7. **不手动触发**：不 `kickstart` 两个采集任务，不手动跑采集脚本（`RunAtLoad` 为 false，加载后不会立即运行）。首轮自动运行由主控事后检查。
8. **页面验收**：隔离 Playwright（全新独立 profile，随机调试端口）打开正式 `http://127.0.0.1:8766/video-radar.html` 和推特雷达监控页，只看不点任何反馈按钮：新文案显示（「小红书」「抖音 1 小时热榜」「每天 21:20 更新」「25 小时」），pageerror 0，截图存 `/tmp/collector-cadence-launch/`。

## 回退
任何一步失败：停下，从 `/tmp/collector-cadence-launch-backup/` 还原 14 个文件和两个 plist，两个任务恢复到 `disable` + `bootout` 暂停状态，如已重启 8766 则再重启一次加载旧代码；然后发 worker_done（结论不通过）说明卡在哪一步。

## 约束
- **真实 AIsa 调用 0 次**（不手动触发采集）。不写正式 `workspace/` 数据，不做 git 写操作（提交由主控另行处理）。
- 只动本合同列出的 14 个文件和两个 LaunchAgents plist，其他 LaunchAgents（含 `com.servasyy.wechat-*`、`com.huangzongning.orca-article-studio-watch`）不碰。
- **不得用 computer use 操作黄总的真实桌面或 Chrome**；浏览器只用隔离 Playwright。
- Python 用 `/Users/huangzongning/development/article-importer/.venv/bin/python`。

## 交付
- 报告 `coordination/launch-collector-cadence-report.md`，开头「决定要点」表（≤10 行：结论【通过/不通过】、14 文件 SHA 核对、测试数字、8766 新 PID、两个任务加载与禁用状态、下次运行时间、页面验收结果）。完成后用 Orca 发 worker_done。
