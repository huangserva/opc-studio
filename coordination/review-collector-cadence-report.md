# 采集降频补丁独立复核

## 决定要点

| 项 | 结果 |
|---|---|
| 结论 | **不通过（严格按合同：第 7 条推特 Python 全绿门槛未满足）**；未发现本补丁引入的运行时缺陷 |
| H/M/L | **0H / 1M / 1L**：M 为历史测试依赖缺失导致验收门槛未满足；L 为补丁校验文件写旧 SHA |
| 补丁 SHA256 | `2c5edf8ce13bbda7e87b181e8c613135ed6a5b661d6ba1e6784c6ebc09f16e17`，符合合同 |
| 8 条结果 | **1 通过、2 通过、3 通过、4 通过、5 通过、6 通过、7 失败、8 通过**；无未知 |
| 文件与 SHA | 14 个允许文件；改前/改后 SHA 均与交付清单一致；完整证据 `evidence/logs/static.log`；6 个禁止改动文件逐字节相同 |
| 视频 Python | **Ran 132，OK** |
| 推特 Python | 8 个模块合计 **Ran 146**；3 个模块合计 **1 failure / 84 errors**，全部在未打补丁基线中同名、同因复现 |
| 浏览器/反例 | 视频 **60/60**、推特 **43/43**，各自 pageerror=0、外网请求=0；3 次故意改错均红，恢复后均绿 |

复核任务 `task_1e174e0c53ff` / Dispatch `ctx_8990ad72296b`。完整读过复核合同、实施合同和实施报告；本报告为独立复核结果。全部证据路径以 `/tmp/collector-cadence-review/` 为根；下文 `logs/` 简写均指 `evidence/logs/`。**复核工作已完成；本补丁尚未部署。**

## 发现与处置建议

**M1：合同第 7 条未达到全绿；属于已验证的基线依赖问题。** `test_radar_monitor_b1b` 缺 B2 历史交付 API，`b1b2` 缺 `/tmp/viral-b2b-deliver/radar_page_api.py`，`b1c` 缺 `/tmp/viral-b1c-deliver/evidence/live/round-1-bundle.json`。独立复制正式代码到审查目录的 `baseline/`，逐模块比较失败用例名、子测试标签、错误原因及数量，改前与改后完全一致。本次没有伪造旧交付物或删改测试以满足门槛。建议协调者明确决定是否接受这项既有基线例外，或补齐真实历史依赖后仅复验这 3 个模块；本报告不代为豁免合同。

**L1：交付校验文件过期。** `collector-cadence.patch.sha256` 声称 `8e6981a8891c3ffeebf7cbae5800800395525c7fe80f7a69d95930af1c9be3e7`，实际补丁为合同指定 `2c5edf8c…`，README、实施报告、14 个文件 SHA 清单与实际一致。按 sidecar 验证会失败，但补丁本体身份已独立确认，未发现内容错包，因此定 L。建议上线前由实施者重新生成 sidecar；本次只读交付包，未替其修正。

**需提醒黄总的行为变化：故障报警变慢。** `fault_after_rounds=2` 没变；健康 06 点 → 08 点首次失败无报警 → 10 点第二次失败才发 1 条 `monitor_fault`，已离线实测。即距最后健康轮约 4 小时、距首次失败轮约 2 小时；实际故障发生到告警的时间取决于发生时刻及采集耗时，并非固定 4 小时。夜间停采以及 06 点静默基线可能使等待更长。此为新节奏下现有逻辑的结果，合同只要求评估，本次不要求改。

## 八条逐项结论

| # | 判定 | 独立证据与理由 |
|---|---|---|
| 1 交付一致 | **通过** | 正式代码复制到 `/tmp/collector-cadence-review/`，排除正式 `workspace/`、`.git`、依赖与缓存；补丁 check/apply 成功。补充可重放的 `evidence/apply-proof/` 只复制正式 14 个目标文件，check/apply 均 exit 0，结果与审查树 14 文件逐字节相同。14 文件全在合同允许表内；改前/改后 SHA 对照交付清单一致。sidecar 另列 L1。证据 `logs/apply-proof.log`、`logs/static.log`。 |
| 2 plist | **通过** | 两个 `plutil -lint` OK；推特精确 9 项 06、08、10、12、14、16、18、20、22，Minute=0；视频精确 1 项 21:20。用 `plistlib` 与 `~/Library/LaunchAgents/` 现版逐键比较，除 `StartCalendarInterval` 外全部相同，`RunAtLoad=False`；路径与环境字段未输出敏感值。`logs/static.log`。 |
| 3 日报与基线 | **通过** | 独立脚本注入离线 collector、sender、AI filter、clue writer；06 点放入高热帖子仍 baseline、0 事件/0 投递/0 线索写入；随后 08–22 每两小时一轮，22 点恰好 1 条 `daily_summary`、`rounds=9`、触发时间精确相等；23 点 `outside_hours`、日报仍 1 条。`logs/independent-probes.log`。故障报警评估见上。 |
| 4 增量语义 | **通过** | 阅读 `scripts/radar_monitor.py:676–679`，分母为当前与上次成功样本的 `fetched_at` 差（小时），不是写死的一小时。独立实测 2h 间隔，浏览增量 9998/10000/10002 → 4999/5000/5001 每小时，阈值触发 False/True/True；页导出 `interval_hours=2`，无翻倍。`logs/independent-probes.log`；原 `test_03_failed_round_preserves_actual_two_hour_interval` 同时通过。 |
| 5 过期与文案 | **通过** | 推特确定性测试 2h10m/150m 为 ok、150m+1s/2h31m 为 stale；视频确定性测试小红书/实时 24h30m 与 25h 不过期、25h+1m/1s 过期，日榜 30h 与超过 30h 边界不变。真实浏览器新标签、21:20/25h 页脚和前后边界均通过。全仓残留逐类说明见下。 |
| 6 断言强度 | **通过** | 人工逐段审阅 7 个测试文件 diff，无删用例、无放宽；旧→新清单见下。3 次故意改错均由原交付测试检出，恢复原字节后均绿；`logs/mutations.log`，每次红/恢复日志单独留存。 |
| 7 测试 | **失败** | 视频 Ran 132 OK；推特指定模式逐模块合计 Ran 146，存在 3 个失败模块，不能称全绿。失败与正式未补丁基线同名同因同数量。浏览器视频 60/60（55123）、推特 43/43（55742），均 pageerror=0、外网请求=0。详见数字表。 |
| 8 采集代码不变 | **通过** | `scripts/radar_monitor.py`、`scripts/video_radar.py`、两个 `scripts/launchd/run-*.sh`、`server.py`、`public/style.css` 与正式目录逐字节相同，SHA 见 `logs/static.log`；补丁不涉及这些文件。 |

## 测试数字与失败归因

全部 Python 使用 `/Users/huangzongning/development/article-importer/.venv/bin/python`，去掉继承的 AIsa/飞书凭据。推特模块采用各自独立进程，避免测试间全局状态干扰；不把子测试 errors 数误写成独立用例数。

| 组/模块 | Ran | 结果 |
|---|---:|---|
| `discover -s tests -p 'test_video_radar*.py'` | 132 | OK |
| `test_radar_monitor` | 39 | OK |
| `test_radar_monitor_b1b` | 18 | failures=1；`test_10_read_only_b2_monitor_view_matches_projection` 的历史 B2 API 存在断言失败；本轮修改的 `test_09` 日报反馈用例本身通过 |
| `test_radar_monitor_b1b2` | 11 | errors=66（含子测试）；缺 `/tmp/viral-b2b-deliver/radar_page_api.py` |
| `test_radar_monitor_b1c` | 18 | errors=18；setUp 缺历史 live fixture |
| `test_radar_monitor_b1c2` | 14 | OK |
| `test_radar_monitor_b1d` | 11 | OK |
| `test_radar_monitor_e2e` | 1 | OK，另一个真实隔离浏览器线索路径 |
| `test_radar_page_api` | 34 | OK |
| 推特指定范围合计 | 146 | 1 failure / 84 errors，3 个失败模块；其余 5 模块全绿 |
| Playwright `video_radar_page.cjs` | 60/60 | pageerror=0、外网请求=0，随机端口 55123 |
| Playwright `twitter_radar_monitor_page.cjs` | 43/43 | pageerror=0、外网请求=0，随机端口 55742 |

推特未打补丁基线对照：`evidence/baseline-compare.py`、`logs/baseline-comparison.log`、`logs/baseline-test_radar_monitor_b1*.log`。独立脚本 `evidence/run-twitter.py` 对应逐模块日志与 `evidence/twitter-python-results.json`。浏览器 `evidence/video/results.json`、`evidence/twitter/results.json` 保存全部断言及错误列表，截图各在同目录；另目视检查了视频过期页及推特阈值截图。

## 故意改错：先绿 → 红 → 原字节恢复 → 绿

| 改错项 | 对应交付测试 | 红结果 | 恢复结果 |
|---|---|---|---|
| 小红书 `STALE_AFTER` 25h 改回 70m | `PageViewTests.test_stale_boundaries` | failures=3 | OK，SHA 恢复 `8eb63bc1…` |
| `end_hour` 22 改回 23 | `MonitorTests.test_16_twenty_two_summary_once_calls_sum` | failures=1 | OK，SHA 恢复 `5a3b2f79…` |
| 推特 plist 加回 23:00 | `AdapterTests.test_launchd_template_schedule_and_wrapper_syntax` | failures=1 | OK，SHA 恢复 `fc1210ac…` |

只在审查副本改错，`finally` 恢复；`evidence/mutations.py` 和 `logs/mutation-*.log` 可重放。恢复后再次用新鲜正式 14 文件打同一补丁，所得全部字节与审查树一致，确认无残留。

## 测试 diff 旧期望 → 新期望

| 测试改动 | 旧 → 新；保留的约束 |
|---|---|
| 推特 plist | 18 个整点 → 精确 9 个偶数整点；仍完整列表相等、RunAtLoad false、TZ 相等、wrapper 语法检查 |
| 22 点日报用例 | 22/23/23:10 → 21/22/22:10；仍 1 条日报、156 次调用、3 轮、unknown=0；新增 23 点 outside_hours 和日报仍 1 条 |
| 视频 plist | 18 个 `h:20` → 精确 `[21:20]`；wrapper 和 RunAtLoad 断言未动 |
| 小红书过期 | 69m/70m 不过期、71m 过期 → 24h29m/24h30m/25h 不过期、25h1m 过期；边界相等约束保留并增加样本 |
| 抖音实时过期 | 70m 不过期、70m1s 过期 → 24h30m/25h 不过期、25h1s 过期；日榜 30h 边界未动 |
| 视频阈值元数据 | 新增精确 1500/1500/1800 分钟断言 |
| 推特阈值 | 新增 130m、150m、150m1s、151m 精确状态断言 |
| B1b 日报反馈 | 23 点及 23:10 → 22 点及 22:10；pushed/worth/unreviewed/useful_rate、日报最后发送、快照不可变等所有期望值保留 |
| B1b 单轮读取 | 23 点 → 22 点；`read.call_count == 1` 不变 |
| 视频 e2e 过期 | 69m/71m → 24h30m/25h1m；两来源都测，日榜/空时间戳仍测 |
| 视频 e2e 标签/布局 | 标签值改新文字；旧 hotTab 精确相等和排除“实时候选”仍保留，增加小红书精确值/排除“（每小时）”；三视口三标签布局检查保留 |
| 新增 e2e 文案/推特阈值 | 页脚必须含 21:20/25 小时且不含 70 分钟；推特 130m 必须仍显示 `+1.2万/h`、151m 必须数据已过期 |

## 全仓旧值扫描与保留理由

`logs/residual-all.log` 为包含 coordination 历史文档的扫描，`logs/residual-repository.log` 排除历史协调目录后扫描，`logs/residual-active.log`/`logs/residual-targeted.log` 覆盖运行代码和测试。未复制生产 workspace；无任何运行页面仍把本次两个任务写为每小时采集或使用 70 分钟阈值。

- `70 分钟`：运行路径仅存在于 e2e 的反向断言名称和 `!foot.includes(...)`；其余在旧合同、旧验收报告及历史决策中，记录旧版本，不是当前执行值。`coordination/video-radar-schema.md` 的旧标签也是既有来源设计文档，本次新合同/补丁已覆盖其旧排期；实施允许范围不含这些历史文件，不改历史证据。
- `每小时新增`、`赞每小时`、`近一小时涨速`、`hourly_delta`、`mode=hourly`、`page-hourly.json`：分别为归一化指标、来源窗口或稳定接口名，不能据其字面当作当前执行频率。网页已明确每日 21:20 更新。
- `test_video_radar_page_api.py` 内“抖音每小时主来源”是带 2026-10-16 日期的修订说明，描述热点宝 `date_window=1` 数据来源，不是调度断言。
- `timedelta(hours=2)` 剩余用于测试帖子年龄、样本间隔、日榜 fixture 等；非过期常量。23 点时间戳仍可用作事件/发布时间 fixture；新增 outside_hours 用例也必须保留 23 点。
- `tests/fixtures/xradar/words-*.html`、`coordination/research/xradar/snapshots/*.html` 的“页面每小时重新生成”是对照站点冻结快照，不能改成我们的新频率。
- `public/knowledge.js`、微信/定时任务相关文字和 `知识库说明.md` 的每小时采集属于微信、Gmail 或其历史状态；不属于本次两个采集任务。`workbench-structure-v4.html:127` 的“每日/每小时自动化雷达采集”为整套工作台的通用结构图，未指称本次推特/视频采集，保留成立。

## 范围与证据边界

本次仅写 `/tmp/collector-cadence-review/`、为 e2e 按合同 `cp -R` 复制的 `/tmp/article-importer/scripts/`，以及合同指定的正式报告 `coordination/review-collector-cadence-report.md`。正式应用代码、正式 workspace、8766 服务、LaunchAgents 文件和 launchd 状态均未改；没有 add/commit/push/stash/checkout 等 Git 状态写操作，仅在 `/tmp` 普通副本按合同执行 `git apply --check`/`git apply`。

AIsa 实际调用 **0 次**：测试使用离线 fixture/mock collector，HTTP 测试只访问各自本地假服务；wrapper 测试使用自建临时 plist 与探针子进程。两份 e2e 从新启动的补丁副本 server.py 加载代码，使用新的临时 workspace、随机端口、headless Playwright 独立 context，外域请求拦截且计数为 0；未用 computer use、未接管真实 Chrome/profile/桌面。没有对正式 launchd 的实际触发进行验收；该部分按合同留给复核决策后的另派上线。
