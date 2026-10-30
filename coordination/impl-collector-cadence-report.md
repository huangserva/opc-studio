# 交付报告：推特雷达改每 2 小时、视频榜改每天 21:20（降 AIsa 费用）

- 合同：`coordination/impl-collector-cadence-contract.md`
- 执行：OpenCode `term_1e842060`（task `task_4e7cd56c7de6`）
- 开发副本：`/tmp/collector-cadence/`；改前基线：`/tmp/collector-cadence-orig/`（与正式目录逐字节相同）
- 交付目录：`/tmp/collector-cadence-deliver/`

## 决定要点

| 项 | 结论 |
|---|---|
| 结论 | **通过（H）**，14 个允许改动文件全部按合同改完，无越界 |
| 补丁 | `/tmp/collector-cadence-deliver/collector-cadence.patch`，SHA256 `2c5edf8ce13bbda7e87b181e8c613135ed6a5b661d6ba1e6784c6ebc09f16e17`；正式目录 `git apply --check` 通过；打到干净基线副本后 14 文件与开发副本 `cmp` 全 IDENTICAL |
| 排期 | 推特 plist `StartCalendarInterval` 18 项 → 9 项（6、8、10、12、14、16、18、20、22 时，Minute 0）；视频 plist 18 项（6:20–23:20）→ 1 项（21:20）；`radar_monitor_config.json` `end_hour` 23 → 22 |
| 阈值 | `radar_page_api.py` `STALE_AFTER` 2 小时 → 150 分钟；`video_radar_page_api.py` 小红书/抖音实时 70 分钟 → 25 小时（1500 分钟），抖音日榜 30 小时不变 |
| 文案 | `public/video-radar.html` + `.js`：页签「小红书」「抖音 1 小时热榜」；页脚写明「每天 **21:20** 更新一次」「超过 **25 小时**标记过期」 |
| 文件 SHA256 | 14 个文件改前→改后全部列在 §2；未改动文件抽检 6 个（`radar_monitor.py`、`video_radar.py`、`server.py`、`style.css`、两个 wrapper 脚本）全部 SAME |
| 反例 1–6 | 全部通过：①旧值逐处说明 ②12 条断言旧→新、无删除无放宽、另加 4 例 ③`plutil -lint` 双 OK + 除排期外逐键相同 + `RunAtLoad=false` ④22 点 1 条 `daily_summary` 覆盖 9 轮、23 点 `outside_hours`、06 点仍基线零事件 ⑤2h10m ok / 2h31m stale、24h30m 不过期 / 25h1m 过期、日榜 30h 不变 ⑥视频 60/60、推特 43/43，pageerror 0、外网请求 0 |
| 测试 | 视频榜 Python `Ran 132 tests OK`（基线 131，+1 新增）；推特雷达 Python 逐模块 259 个全绿；5 个失败模块与基线失败画像逐条一致（缺既有 B1/B2 交付物，非本单引入）；隔离 Playwright 60/60 + 43/43 |
| 边界 | 真实 AIsa 调用 **0 次**；未改正式目录（只做 `git apply --check` 只读校验）；未写正式 `workspace/`；未重启 8766；未碰 launchd；无任何 git 写操作；浏览器仅隔离 Playwright |

## 1. 改动清单（14 文件）

| 文件 | 改动 |
|---|---|
| `scripts/launchd/com.servasyy.radar-monitor.plist` | `StartCalendarInterval` 18 项（6–23 点整点）→ 9 项（6、8、…、22 点，Minute 0） |
| `scripts/launchd/com.servasyy.video-radar.plist` | `StartCalendarInterval` 18 项（6:20–23:20）→ 1 项（Hour 21、Minute 20） |
| `scripts/radar_monitor_config.json` | `end_hour` 23 → 22（日报改在 22 点那轮发，其余字段不变） |
| `radar_page_api.py` | `STALE_AFTER = timedelta(minutes=150)`；模块文档字符串同步（2 小时间隔 + 最长 10 分钟采集 + 余量） |
| `video_radar_page_api.py` | `STALE_AFTER` 小红书/抖音实时 `timedelta(hours=25)`；日榜 `hours=30` 不变；文档字符串同步 |
| `public/video-radar.html` | 页签「小红书（每小时）」→「小红书」、「抖音每小时热榜」→「抖音 1 小时热榜」；页脚 70 分钟 → 25 小时并注明每天 21:20 更新 |
| `public/video-radar.js` | `LIST_LABELS` 同上两处文案 |
| `tests/test_radar_monitor.py` | 排期断言 18 项 → 9 项；日报用例 22/23 点 → 21/22 点；用例改名 `test_16_twenty_two_summary_once_calls_sum`；新增「23 点 `outside_hours` 且日报仍 1 条」断言 |
| `tests/test_video_radar.py` | 排期断言 18 项（6–23 点 20 分）→ 1 项（21:20） |
| `tests/test_video_radar_page_api.py` | 过期边界 70 分钟 → 25 小时；新增 `stale_after_minutes` = 1500/1500/1800 断言 |
| `tests/test_radar_page_api.py` | 新增 150 分钟边界用例（2h10m ok / 150m ok / 150m+1s stale / 2h31m stale） |
| `tests/test_radar_monitor_b1b.py` | 两处依赖「23 点是运行时段」的用例改到 22 点，期望值不变（`b1.when(23, day=8)` 是事件时间戳，与排期无关，刻意未改） |
| `tests/e2e/video_radar_page.cjs` | 自测 6 边界改 24h30m/25h1m；页签断言改新文案；新增页脚文案断言（含 21:20 / 25 小时、不含 70 分钟） |
| `tests/e2e/twitter_radar_monitor_page.cjs` | 新增「反例5-推特阈值」：2h10m 显示增量、2h31m 显示数据已过期 |

## 2. 文件 SHA256（改前 = 正式目录当前内容，改后 = 交付副本）

| 文件 | 改前 | 改后 |
|---|---|---|
| `scripts/launchd/com.servasyy.radar-monitor.plist` | `4e13ed8f44e681a11cceddd6fe23cc4dc4bd724300d9bf3491db18323d007f69` | `fc1210ac0b33cae438259f8e21a4a1a4d9e5ff7ab080c14c2a2874fa4f57f7d0` |
| `scripts/launchd/com.servasyy.video-radar.plist` | `71770662b1dc9f8f60e851903cd2c53f1f3d9029366580498343cb67e75b5d6c` | `4622f83c277861560d9fed37205daa036f788757fc54f5179554e6b9502d86d6` |
| `scripts/radar_monitor_config.json` | `74e978e738da6b47da53d03a021f00374d24a47ddb573c14cf26b7f7f80ce6e0` | `5a3b2f79559aca751c7456c3233c99c61cb6533799ee31269834bfa03848af70` |
| `radar_page_api.py` | `4c2b1e52baf5c6f1040f66913bff1da9dc39f3c4a46a05d6cbf7b7817b0ff9ca` | `bf99dae623c59ccec9608924ffa3c819da83c71c5a5e291a79114f1ddead3eab` |
| `video_radar_page_api.py` | `9dd5ffd2757b3cd33123543e81587d35eced3d2fc80fafb3d7df409b6d29dc6b` | `8eb63bc1a53d4460e524a0e9c94a8cab459494ba6866bddb1eaadf7c809ae9c8` |
| `public/video-radar.html` | `448633d7cb66a7afbfb7c9977812901beee552a41d8135a426a2ab1d515f5e41` | `25b1e0d4ed6aa693da1f8df43d1e6592ca2e2d23f7dd0d509c07a86ffae74b1a` |
| `public/video-radar.js` | `a789b6a9446a2611fdf1e5739d25bab3ca3a50d7a3d8f42f34f2e4ecf8cdc00f` | `0a25bedbd898b89e5913d13b0514e96195882f3dc8551d1d872ba2a6e405a0fb` |
| `tests/test_radar_monitor.py` | `d722118269561ff827d56c7c4d7aaa7332ae5708217d99d2db3271c0bb998772` | `5f697fccbadabc45d28ed3f230384bf365080216a3064ef3c5228dd6ce10bafe` |
| `tests/test_video_radar.py` | `e193c408195ea9fd20a2090a1caa3acb1d4d5d9699268ebf32165cb5acc189cb` | `8659b864649025506a77a8ccd5e1136f25fb1c1a48cf58e94d3aa6e4e3319d97` |
| `tests/test_video_radar_page_api.py` | `87c63f9c946ce5542acad4702f6164d6d51d6911bd8c9ff98a3d462fe13b5e09` | `55c2ca4495b1403359f2a3a0c881a73a633708bf34ad6eadc4bcd9713fdca014` |
| `tests/test_radar_page_api.py` | `dc0ad4972069f620a74873f2511e0a112816042008660f969eb37da33ffc50c4` | `bf4661c6400020823724358418e8af15f48e7ad9a8913a49020c9fec8096139f` |
| `tests/test_radar_monitor_b1b.py` | `1a96ad961ed63e2d076ee5bbeae2df3691b1f1164bf7bb0ca7a2331b0f5bc82a` | `fc4f773dc60951983864c41ddfb903236f013104888e9c641391c6edcc4c7507` |
| `tests/e2e/video_radar_page.cjs` | `0cebb5d192be4794bc8514927dc5d51ccbab37bcc8cd48ff7c71160088fa6c7b` | `44dc6f9811f2476f37b602eae911ee5b1746e1d4fa6d5653e43757b755115d7d` |
| `tests/e2e/twitter_radar_monitor_page.cjs` | `7b682f1cd9b239ab824426955923f107328191c124e5f02e850747cfd4b791af` | `9f0c8b2ef814d5438791d1b395b3176cc526cadc96181250fb268563a9a825b7` |

未改动文件抽检（与正式目录逐字节相同）：`scripts/radar_monitor.py`、`scripts/video_radar.py`、`server.py`、`public/style.css`、`scripts/launchd/run-radar-monitor.sh`、`scripts/launchd/run-video-radar.sh` 全部 SAME。完整清单见 `/tmp/collector-cadence-deliver/file-sha256.txt`。

补丁 SHA256：`2c5edf8ce13bbda7e87b181e8c613135ed6a5b661d6ba1e6784c6ebc09f16e17`

## 3. 反例自测（合同第 19–26 行，逐条）

### 反例 1：阈值/文案前后一致，全仓旧值逐处说明 —— 通过

日志：`logs/counterexample-1-old-values.log`（`rg` 全仓扫描，排除 fixtures/数据目录/node_modules）

| 旧值 | 结果与说明 |
|---|---|
| `70 分钟` | 业务代码与文案已清零；仅剩 `tests/e2e/video_radar_page.cjs:451-452` 的**反向断言**（要求页脚含 21:20、25 小时且**不含** 70 分钟） |
| `70分钟` | 0 处 |
| `end_hour.*23` | 0 处（配置已 22） |
| `range(6, 24)` | 0 处（推特排期断言已改 `range(6, 23, 2)`，视频已改单项 21:20） |
| `抖音每小时热榜` / `小红书（每小时）` | 0 处（HTML/JS/测试均改新文案） |
| `timedelta(hours=2)` | 仅测试 fixture 的时间戳（`test_video_radar_desc_excerpt.py:45`、`test_twitter_radar_foundation.py` 多处 `BASE - timedelta(hours=2)`），与过期阈值无关，未改 |
| 剩余「每小时」字样 | ①`test_schedules_ui.cjs:42`、`test_wechat_source_api.py:86`、`test_wechat_source.py:142` 是微信隔离「每小时重放」，另一功能；②`test_video_radar_page_api.py:396` 是抖音热点宝「近 1 小时榜」来源说明；③`e2e/video_radar_page.cjs:424/426/630` 是指标列「赞每小时」「近一小时涨速」，合同要求不动列名；④`e2e/twitter_radar_monitor_page.cjs:3/330/605` 是推特页指标列「每小时新增」。均不暗示采集频率，按合同不改 |

### 反例 2：测试没被放宽 —— 通过

12 条改动断言逐条「旧期望 → 新期望」：

| # | 用例 | 旧期望 | 新期望 |
|---|---|---|---|
| 1 | `test_radar_monitor.AdapterTests.test_launchd_template_schedule_and_wrapper_syntax` | `range(6,24)` = 18 项 | `range(6,23,2)` = 9 项（6,8,…,22） |
| 2 | `test_radar_monitor.MonitorTests.test_16_*`（原名 twenty_three） | 22 点 calls=52、23 点 calls=53、23:10 复跑，1 条日报 | 21 点 calls=52、22 点 calls=53、22:10 复跑，1 条日报在 22 点；另加 23 点 `outside_hours` 且日报仍 1 条 |
| 3 | 同上累计数字 | `51+52+53=156`、`rounds=3`、`daily.api_calls=156` | 不变（未放宽） |
| 4 | `test_video_radar.RadarTests.test_launchd_template` | `[Hour=h,Minute=20 for h in range(6,24)]` | `[Hour=21,Minute=20]` |
| 5 | `test_video_radar_page_api.PageViewTests.test_stale_boundaries`（小红书） | 69m 不过期 / 70m 不过期 / 71m 过期 | 24h29m 不过期 / 24h30m 不过期 / 25h 不过期 / 25h1m 过期 |
| 6 | 同上（抖音实时） | 70m 不过期 / 70m1s 过期 | 24h30m 不过期 / 25h 不过期 / 25h1s 过期 |
| 7 | 同上（抖音日榜） | 30h 不过期 / 30h1m 过期 | 不变 |
| 8 | `test_radar_monitor_b1b.B1bTests.test_09_*` | 23 点轮次 + 23:10 复跑 | 22 点轮次 + 22:10 复跑；`pushed=3` / `worth=2` / `unreviewed=3` 等期望值全部不变 |
| 9 | `test_radar_monitor_b1b.B1bTests.test_feedback_read_once_per_active_round*` | 23 点轮次 | 22 点轮次；`read.call_count == 1` 不变 |
| 10 | `e2e/video_radar_page.cjs` 自测 6 | 69/71 分钟、70 分钟 + 60s | 24h30m 不过期、25h1m 过期（小红书与抖音实时各一组） |
| 11 | `e2e/video_radar_page.cjs` 修订 1 | `hotTab === '抖音每小时热榜'` | `tabs[0] === '小红书' && hotTab === '抖音 1 小时热榜'` 且不含「（每小时）」 |
| 12 | `e2e/video_radar_page.cjs` 布局 TABS | 「小红书（每小时）」「抖音每小时热榜」 | 「小红书」「抖音 1 小时热榜」 |

无删除用例、无「≥1 项」类宽松写法。新增（只加不减）4 例：
- `test_radar_page_api.MonitorViewTests.test_07_stale_boundary_150_minutes`
- `test_video_radar_page_api.PageViewTests.test_stale_after_minutes_matches_daily_cadence`
- `e2e/video_radar_page.cjs` `附-说明文字`
- `e2e/twitter_radar_monitor_page.cjs` `反例5-推特阈值`

### 反例 3：plist 没改坏 —— 通过

日志：`logs/counterexample-3-plist.log`

- `plutil -lint`：两个 plist 均 OK。
- 推特 plist：旧 18 项 `(6,0)…(23,0)` → 新 9 项 `(6,0)(8,0)…(22,0)`。
- 视频 plist：旧 18 项 `(6,20)…(23,20)` → 新 1 项 `(21,20)`。
- 除 `StartCalendarInterval` 外逐键对比完全相同：`True`；两个 plist 的 `RunAtLoad` 仍为 `False`。

### 反例 4：日报仍会发、06 点仍是基线 —— 通过

日志：`logs/counterexample-4-daily-summary.log`；可重跑脚本 `/tmp/collector-cadence-deliver/scripts/counterexample-4-daily-summary.py`

离线模拟一天 9 轮（06、08、…、22）：

- 06:00 状态 `baseline`，事件 0 条、投递 0 条（零外部效果）；
- 08:00–22:00 每轮 `success`，共 9 轮；
- `daily_summary` 恰好 **1 条**，触发在 22:00，覆盖 9 轮，`api_calls=571`；
- 23:00 状态 `outside_hours`，之后 `daily_summary` 仍为 1 条（不再产生）。

### 反例 5：过期边界 —— 通过

日志：`logs/counterexample-5-and-schedule.log`（7 用例 OK）+ `logs/python-per-module.log` + `logs/e2e-twitter-radar.log`

- 推特监控页：`generated_at` 距今 2h10m → `ok`（显示增量）；2h31m → `stale`（数据已过期）；150m 整点仍 `ok`，150m+1s → `stale`。
- 视频榜小红书/抖音实时：24h30m 不过期，25h 不过期，25h1m 过期。
- 抖音日榜：30h 不过期 / 30h1m 过期（不变）。

### 反例 6：真实页面（隔离 Playwright） —— 通过

日志：`logs/e2e-video-radar.log`、`logs/e2e-twitter-radar.log`；截图 42 张在 `/tmp/collector-cadence-deliver/screenshots/`

- 视频榜页：**通过 60/60**，pageerror 累计 0，外网请求 0，随机端口 52140；新页签文案、页脚「每天 21:20 更新 / 25 小时」、过期标记按新阈值出现/不出现均已断言。
- 推特雷达页：**通过 43/43**，pageerror 累计 0，外网请求 0，随机端口 51203；2h10m 显示增量、2h31m 显示「数据已过期」。
- 全程独立 profile + `/tmp` 副本 + 随机端口，未用 computer use，未触碰真实桌面或 Chrome。

## 4. 测试数字

| 组 | 命令范围 | 结果 |
|---|---|---|
| 视频榜 Python | `test_video_radar test_video_radar_page_api test_video_radar_hourly_boards test_video_radar_desc_excerpt test_video_radar_fix1 fix3 fix4 negation negation_boundary recovery` | **Ran 132 tests，OK**（基线 131，+1 新增） |
| 推特雷达 Python（逐模块独立进程） | `test_radar_monitor` 39、`b1b` 18、`b1b2` 11、`b1c` 18、`b1c2` 14、`b1d` 11、`e2e` 1、`radar_page_api` 34、`b2b3` 6、`b2b4` 7、`a1b` 16、`a1c` 18、`foundation` 29、`feishu` 32、`b2a3` 16、`b2a4` 7、`xradar_source` 90 | 259 个通过；5 个模块失败画像与基线逐条一致 |
| e2e | `video_radar_page.cjs` / `twitter_radar_monitor_page.cjs` | 60/60、43/43 |

失败画像对比（`logs/python-baseline-comparison.log`，改前基线 vs 改后）：

| 模块 | 基线 | 改后 | 原因 |
|---|---|---|---|
| `test_radar_monitor_b1b` | failures=1 | failures=1 | 缺 `/tmp/viral-b2b-deliver` 等既有交付物 |
| `test_radar_monitor_b1b2` | errors=66 | errors=66 | 同上 |
| `test_radar_monitor_b1c` | errors=18 | errors=18 | 缺 `/tmp/viral-b1c-deliver` |
| `test_radar_feishu` | failures=1 | failures=1 | 同上 |
| `test_twitter_radar_foundation` | errors=1 | errors=1 | `C02TimeoutKeepsOldTimestamps` 依赖既有产物；单独复跑两副本结果一致 |

说明：把 14 个推特雷达模块塞进同一进程会互相干扰（基线同样 `Ran 311, failures=1, errors=85`；改后 `Ran 312, failures=1, errors=85`），故按「每模块独立进程」报数。

## 5. 复跑方式

```sh
# 视频榜 Python（应 Ran 132 OK）
cd /tmp/collector-cadence
PYTHONPATH=/tmp/collector-cadence/tests \
  /Users/huangzongning/development/article-importer/.venv/bin/python -m unittest \
  test_video_radar test_video_radar_page_api test_video_radar_hourly_boards \
  test_video_radar_desc_excerpt test_video_radar_fix1 test_video_radar_fix3 \
  test_video_radar_fix4 test_video_radar_negation test_video_radar_negation_boundary \
  test_video_radar_recovery

# 隔离 Playwright
RADAR_C2_REPO=/tmp/collector-cadence RADAR_C2_EVIDENCE=/tmp/collector-cadence-deliver/screenshots \
RADAR_C2_PYTHON=/Users/huangzongning/development/article-importer/.venv/bin/python \
  node tests/e2e/video_radar_page.cjs

RADAR_B2B_REPO=/tmp/collector-cadence RADAR_B2B_EVIDENCE=/tmp/collector-cadence-deliver/screenshots \
RADAR_B2B_PYTHON=/Users/huangzongning/development/article-importer/.venv/bin/python \
  node tests/e2e/twitter_radar_monitor_page.cjs

# 反例 4 离线日报模拟
/Users/huangzongning/development/article-importer/.venv/bin/python \
  /tmp/collector-cadence-deliver/scripts/counterexample-4-daily-summary.py /tmp/collector-cadence
```

## 6. 边界声明

- 真实 AIsa 调用 **0 次**（所有采集调用均为 mock/fixture）。
- 未改正式目录内容；仅在正式目录执行 `git apply --check`（只读）。
- 未写正式 `workspace/`；未重启 8766；未 enable/bootstrap/修改 `~/Library/LaunchAgents` 或任何 launchd 状态。
- 无任何 git 写操作（无 add/commit/push/stash/checkout）。
- 浏览器只用隔离 Playwright（独立 context/profile、`/tmp` 副本、随机端口），未使用 computer use。
- 正式目录仍有他人未提交改动（`server.py`、`generation/*` 等），与本单无关，未触碰。
