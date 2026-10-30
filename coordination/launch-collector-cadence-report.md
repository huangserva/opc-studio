# 采集降频补丁上线报告

## 决定要点

| 项 | 结果 |
|---|---|
| 结论 | **通过**；8 步上线门槛满足，新增 0H/0M/0L；沿用主控已接受 M1 基线例外，L1 不阻塞 |
| 14 文件 SHA | 补丁 `2c5edf8ce13bbda7e87b181e8c613135ed6a5b661d6ba1e6784c6ebc09f16e17`；改前、改后各 14/14 一致 |
| 定向测试 | 视频 Ran 132 OK；推特 Ran 146，3 个例外模块 1 failure/84 errors 与复核基线逐项一致，其余 5 模块 99 项全绿 |
| 8766 | PID 4049 → **99761**，HTTP 200，新接口阈值 **1500/1500/1800 分钟** |
| 两任务状态 | 两者已 enable + bootstrap，loaded=true、disabled=false、state=not running、runs=0 |
| 下次运行 | 推特 **2026-10-28 08:00**；视频榜 **2026-10-28 21:20**；均北京时间 UTC+08:00 |
| 页面验收 | 正式 8766 两页均 HTTP 200/API 200、pageerror=0；4 条新文案命中，推特 48 小时页 5 条监控行 |
| 保护范围 | 其他线 43 个改动文件内容和 git status 原样；采集 workspace 818 个文件原样；AIsa 真实调用 **0**，未手动触发采集，未进行 git 状态写操作 |

任务 `task_ac63c70e1737` / Dispatch `ctx_a74997a0057c`。执行起点 2026-10-28T07:07:17.269651+08:00，最终核验 2026-10-28T07:11:26.749619+08:00。证据目录 `/tmp/collector-cadence-launch/`；备份 `/tmp/collector-cadence-launch-backup/`（权限 0700，含 14 个正式文件、两个 LaunchAgents plist 及 SHA 清单）。

## 八步执行结果

1. **备份通过**：14 文件和两个已安装 plist 均先复制再合入，`manifest.json` 记录原 SHA，备份 SHA 已复验；未覆盖历史备份。
2. **合入通过**：正式目录 `git apply --check` 后 `git apply` 成功，改后 14 SHA 与交付清单精确一致。合入前后以及最终 `git status --short -z` 排除目标 14 文件后完全一致；其他线 43 个改动文件 SHA 未变。
3. **定向测试通过（含授权基线例外）**：视频 132 OK。推特逐模块独立进程执行，共 Ran 146，失败集合、子测试标签、缺失原因、数量与复核留存的未补丁基线完全一致；未补造 `/tmp/viral-*` 历史物料，也未修改其他测试。使用指定 Python，测试环境移除 AIsa/飞书凭据；测试采集器和 HTTP 均为离线 fixture/mock 或本机假服务。
4. **重启与加载通过**：仅对 `com.servasyy.article-studio` 执行一次 `launchctl kickstart -k`；新 PID 99761，确认时间 2026-10-28T07:08:10.739997+08:00。正式 `/api/video-radar/page` 返回小红书 1500、抖音 1 小时热榜 1500、抖音日榜 1800，证明新代码已加载。
5. **启动配置替换通过**：两个模板复制到对应 `~/Library/LaunchAgents/` 路径，`plutil -lint` 均 OK，与备份逐键比较仅 `StartCalendarInterval` 不同，`RunAtLoad=false`。
6. **启用通过**：两个 label 各 enable + bootstrap 一次。`launchctl print` 实际日历 descriptor 精确等于新模板：推特 06/08/10/12/14/16/18/20/22 点整，共 9 项；视频 21:20，共 1 项。`print-disabled` 两者均 enabled；最终核验依然 runs=0、not running。按本机 UTC+08:00 日历，下一次为上述时间。
7. **未手动触发**：没有 kickstart 两个采集 label，没有手动执行正式采集脚本；仅启用并加载定时任务，没有发生 RunAtLoad 运行。首轮自动采集实绩按合同留给主控事后检查。
8. **页面通过**：全新 headless Playwright 持久 profile `/tmp/collector-cadence-launch-profile-w0UrS1`，随机调试端口 `58515`，未接管现有浏览器或真实桌面。仅本机 GET，写请求 0、外域请求 0；正式 `video-radar.html` 和 `twitter-radar.html` 的 pageerror 各 0。小红书、抖音 1 小时热榜、每天 21:20 更新、25 小时均显示，截图及页脚已目视核验。推特进入特别关注、48 小时筛选，显示 5 条监控行，现有监控状态为 stale；没有点反馈按钮。

## 测试数字

| 模块/组 | Ran | 结果 |
|---|---|---|
| `video` | Ran 132 tests | OK |
| `test_radar_monitor` | Ran 39 tests | OK |
| `test_radar_monitor_b1b` | Ran 18 tests | FAILED (failures=1) |
| `test_radar_monitor_b1b2` | Ran 11 tests | FAILED (errors=66) |
| `test_radar_monitor_b1c` | Ran 18 tests | FAILED (errors=18) |
| `test_radar_monitor_b1c2` | Ran 14 tests | OK |
| `test_radar_monitor_b1d` | Ran 11 tests | OK |
| `test_radar_monitor_e2e` | Ran 1 test | OK |
| `test_radar_page_api` | Ran 34 tests | OK |

基线比较证据 `baseline-comparison.json`：直接比较复核目录 `evidence/logs/baseline-test_radar_monitor_b1*.log` 与此次正式目录日志的全部 FAIL/ERROR 标题、缺失路径/断言信息、Ran 与 FAILED 数字，3 模块 `same=true`。测试日志在 `logs/`，运行脚本 `run-tests.py`。

## 探针修正说明

预检第一次把 `launchctl print-disabled` 的输出格式假定为 `true/false`，实际本机输出为 `disabled/enabled`；核对原文后修正解析，实际状态一直符合合同，未造成部署步骤失败。

页面探针最初额外假定默认今日页必有监控卡，等待超时；正式数据今日 0 条、48 小时 5 条，属于筛选结果。改为确认默认页正常后选择已有的 48 小时筛选，5 条监控行可见。补截图时 `.vr-foot` 匹配两个元素，已限定最终页脚。上述均是 `/tmp` 探针预设/选择器问题，正式产品没有因此修改；合同规定的 HTTP、文案、pageerror 与只读约束全部通过，无合同步骤失败，未触发回退。

## 文件 SHA256（正式目录最终值）

| 文件 | SHA256 |
|---|---|
| `scripts/launchd/com.servasyy.radar-monitor.plist` | `fc1210ac0b33cae438259f8e21a4a1a4d9e5ff7ab080c14c2a2874fa4f57f7d0` |
| `scripts/launchd/com.servasyy.video-radar.plist` | `4622f83c277861560d9fed37205daa036f788757fc54f5179554e6b9502d86d6` |
| `scripts/radar_monitor_config.json` | `5a3b2f79559aca751c7456c3233c99c61cb6533799ee31269834bfa03848af70` |
| `radar_page_api.py` | `bf99dae623c59ccec9608924ffa3c819da83c71c5a5e291a79114f1ddead3eab` |
| `video_radar_page_api.py` | `8eb63bc1a53d4460e524a0e9c94a8cab459494ba6866bddb1eaadf7c809ae9c8` |
| `public/video-radar.html` | `25b1e0d4ed6aa693da1f8df43d1e6592ca2e2d23f7dd0d509c07a86ffae74b1a` |
| `public/video-radar.js` | `0a25bedbd898b89e5913d13b0514e96195882f3dc8551d1d872ba2a6e405a0fb` |
| `tests/test_radar_monitor.py` | `5f697fccbadabc45d28ed3f230384bf365080216a3064ef3c5228dd6ce10bafe` |
| `tests/test_video_radar.py` | `8659b864649025506a77a8ccd5e1136f25fb1c1a48cf58e94d3aa6e4e3319d97` |
| `tests/test_video_radar_page_api.py` | `55c2ca4495b1403359f2a3a0c881a73a633708bf34ad6eadc4bcd9713fdca014` |
| `tests/test_radar_page_api.py` | `bf4661c6400020823724358418e8af15f48e7ad9a8913a49020c9fec8096139f` |
| `tests/test_radar_monitor_b1b.py` | `fc4f773dc60951983864c41ddfb903236f013104888e9c641391c6edcc4c7507` |
| `tests/e2e/video_radar_page.cjs` | `44dc6f9811f2476f37b602eae911ee5b1746e1d4fa6d5653e43757b755115d7d` |
| `tests/e2e/twitter_radar_monitor_page.cjs` | `9f0c8b2ef814d5438791d1b395b3176cc526cadc96181250fb268563a9a825b7` |

## 证据与后续

- `apply-result.json`、`manifest.json`、`git-status-before.bin`、`git-status-after-apply.bin`、`final-integrity.json`：范围和 SHA。
- `test-results.json`、`baseline-comparison.json`、`logs/`：测试与基线。
- `restart-result.json`：PID 与正式 API 新阈值。
- `plist-install-result.json`、`launchd-result.json`：plist 差异、真实日历核验、启用状态与下次时间；含环境字段的完整 launchctl 原文仅留在受限备份目录，不写入本报告。
- `browser-result.json`、`browser-check.log`、`video-radar.png`、`video-radar-footer.png`、`twitter-radar.png`、两页 `*-viewport.png`：真实隔离浏览器、请求清单和截图。

没有改正式 workspace 数据，没有 add/commit/push/stash/checkout 等 Git 状态写操作，没有触碰其他 LaunchAgents。已有历史视频页上显示的 AIsa 调用数字属于旧快照，不是此次调用。备份和回退依据齐全；本次未执行回退。**剩余仅首轮自动运行后的实绩检查，由主控按合同后查，不在本次手动触发。**
