# 实施合同：推特雷达改每 2 小时、视频榜改每天 21:20（降 AIsa 费用）

- 派给：OpenCode term_1e842060（实施）。复核派 Codex term_02017284；复核通过后另派上线（替换 LaunchAgents 并重新加载）。
- 背景（黄总 2026-10-27）：AIsa 本月 21,410 次、$106.74，两个每小时任务约 $10/天（推特雷达约 1,000 次/天 × $0.003；视频榜约 725 次/天）。黄总定：**推特雷达 06–22 点每 2 小时（06、08、…、22，每天 9 轮，06 点那轮仍是基线不推送）；视频榜每天只跑一次，21:20**。两个任务 14:16 起已由主控 `launchctl disable` + `bootout` 暂停，本单**不碰 launchd**。

## 允许改动（只这些，别的文件一律不动）
| 文件 | 要改什么 |
|---|---|
| `scripts/launchd/com.servasyy.radar-monitor.plist` | `StartCalendarInterval` 改为 9 项：Hour 6、8、10、12、14、16、18、20、22，Minute 0。其他键（含 `RunAtLoad`、环境变量、路径）逐字不变 |
| `scripts/launchd/com.servasyy.video-radar.plist` | `StartCalendarInterval` 改为只有 1 项：Hour 21，Minute 20。其他键逐字不变 |
| `scripts/radar_monitor_config.json` | `end_hour` 23 → 22（`radar_monitor.py` 第 832 行在 `end_hour` 那轮发 `daily_summary`，不改的话再也不会发日报）。其余字段不变 |
| `radar_page_api.py` | `STALE_AFTER` 2 小时 → 150 分钟（2 小时间隔 + 最长 10 分钟采集 + 余量），同步改模块文档字符串 |
| `video_radar_page_api.py` | `STALE_AFTER` 中 `xiaohongshu`、`douyin_realtime` 70 分钟 → 25 小时；`douyin_daily` 30 小时不变；同步改文档字符串 |
| `public/video-radar.html`、`public/video-radar.js` | 只改说明文字：「小红书（每小时）」「抖音每小时热榜」等字样改成不暗示每小时更新的写法（例如「小红书」「抖音 1 小时热榜」，并注明「每天 21:20 更新」）；过期说明里的「70 分钟」改成与新阈值一致 |
| 测试 | 只改断言旧节奏/旧阈值的地方（例如 `tests/test_radar_monitor.py`、`tests/test_video_radar*.py`、`tests/e2e/video_radar_page.cjs`、`tests/e2e/twitter_radar_monitor_page.cjs` 里断言 18 个时点、70 分钟、2 小时、23 点日报的用例），**把期望值改成新值，不删用例、不放宽**；新增边界用例见下 |

不改：`scripts/radar_monitor.py`、`scripts/video_radar.py` 等采集逻辑（推特增量已按 `interval_hours` 折算成每小时，见 `radar_monitor.py` 第 676–679 行，2 小时间隔下阈值语义不变）、飞书卡片文字、`server.py`、`style.css`、推特雷达页其他内容。

## 该工人已知弱点与必测反例（逐条自测并在报告给结果）
画像：OpenCode 常见失误是改一处漏一处（文案/阈值前后端不一致）、为让测试过而改松断言、真实交互漏检。本单对应：
1. **阈值前后不一致**：后端改成 25 小时，页面说明或测试仍写 70 分钟；或推特页 150 分钟只改了常量没改文档。全仓 `rg` 旧值（`70`、`hours=2`、`每小时`、`23` 点日报）逐处说明改或不改的理由。
2. **测试被放宽**：旧节奏断言被删掉或改成「≥1 项」之类宽松写法。报告列出每个改动的断言「旧期望 → 新期望」。
3. **plist 改坏**：`plutil -lint` 两个 plist 都 OK；与改前逐键对比，除 `StartCalendarInterval` 外完全相同（`RunAtLoad` 仍为 false）。
4. **日报不发**：用测试或离线跑证明 22 点那轮（非基线、未损坏）会产生 `daily_summary` 事件，23 点不会再有运行；06 点那轮仍为基线、零外部效果。
5. **过期边界**：推特监控页 `generated_at` 距今 2 小时 10 分 → `ok`，2 小时 31 分 → `stale`；视频榜小红书/抖音实时 `last_success_at` 距今 24 小时 30 分 → 不过期，25 小时 1 分 → 过期；抖音日榜 30 小时边界不变。
6. **真实页面**：隔离 Playwright 打开视频榜页与推特雷达页，确认新文案显示、过期标记按新阈值出现/不出现，pageerror 0。

## 自测门槛
- `tests/test_video_radar*.py` 全绿（报 Ran N，应 ≥131）；`tests/test_radar_monitor.py` 及推特雷达相关 Python 测试全绿（报 Ran N）；`tests/e2e/video_radar_page.cjs`、`tests/e2e/twitter_radar_monitor_page.cjs` 隔离 Playwright 全过（报数字）。
- 未改动文件 SHA 不变（报告列出允许改动文件的改前/改后 SHA256）。

## 约束
- 在 `/tmp/collector-cadence/` 副本开发（从正式目录复制代码，不复制正式 `workspace/` 的生产数据；e2e 需要兄弟目录时在 /tmp 父目录 `cp -R` 复制 `article-importer/scripts`，不软链、不改真实目录）。交付到 `/tmp/collector-cadence-deliver/`：相对正式目录的补丁（正式目录 `git apply --check` 必须通过）+ 补丁 SHA256 + 每个改动文件改后 SHA256 + 测试日志。
- **真实 AIsa 调用 0 次。** 不改正式目录，不写正式 `workspace/`，不重启 8766，**不碰 launchd（不 enable、不 bootstrap、不改 ~/Library/LaunchAgents）**，不做 git 写操作。
- 浏览器只用隔离 Playwright（独立 profile、/tmp 副本、随机端口）；**不得用 computer use 操作黄总的真实桌面或 Chrome**。
- Python 用 `/Users/huangzongning/development/article-importer/.venv/bin/python`。

## 交付
- 报告 `coordination/impl-collector-cadence-report.md`，开头「决定要点」表（≤10 行：结论【通过/不通过】、H/M/L、补丁 SHA256、改动文件与 SHA256、6 条自测结果、各组测试数字）。完成后用 Orca 发 worker_done。
