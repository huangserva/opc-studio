# H2 视频雷达调度收编 + L1 兴趣清单权限 独立复核

| 决定要点 | 结果 |
|---|---|
| 结论 | **通过（本单白天复核范围）**；今晚 21:20 真实触发由主控见证 |
| H / M / L | 0 / 0 / 0；晚间尚未发生的行为单列为未知，不冒充完成 |
| 生产任务 | `e49a042aa2ff48379c7aa6bba6af1f6a` 唯一、启用、每日北京时间 21:20、900 秒 |
| 真实运行 | `9e0d79dabfe1468788b50fcda4148343` manual succeeded / 0；原巡检日志末行与其 output 完全相等 |
| 源码 SHA256 前 12 位 | interest_channel.py `d24a59d1524e`；tests/test_interest_channel.py `d79e6f3d0b39`；tests/test_video_radar_schedule_h2.py `81cc91f71500`；与实施报告一致 |
| 测试 | Python 全量 3638 项，0F/1E/7S，416.923 秒，唯一 E 与既有 video_lab 一致；定向 55/55；独立反例 5/5；浏览器 12/12；4 类故意改错均被检出 |
| 正式保护对象 | 不重启 8766、不运行真实雷达、不改正式任务、不提交不推送；本单仅新增本报告 |
| 证据 | `/tmp/review-hub-h2/`；真实晚间运行留待主控自然时点验收 |

复核任务 `task_49d6acd44208`，Dispatch `ctx_59fe99d99364`；复核窗口 `term_57dd3162`，实施窗口 `term_02017284`。遵循 review-hub-h2-contract.md 与 impl-hub-h2-contract.md。正式目录除本报告外只读，所有行为验证写入 `/tmp`，浏览器使用独立临时 profile，未使用 computer use。

## 逐项验收

| 验收项 | 结论 | 关键证据 |
|---|---|---|
| 1. 任务配置、超时、凭据 | 通过 | 只读 SQLite 备份核对精确任务 id；kind=script、enabled=true、daily、21:20、weekdays=0..6、timeout=900、next_run=1793366400（2026-10-30 21:20 +08:00）。正式任务中调用 run-video-radar.sh 的任务仅 1 条。历史 192 条 summary（含本次 manual）最长 600.211 秒，旧 launchd 191 条最长相同，900 秒有约 300 秒余量；未来接口耗时不作保证。凭据只在内存取值并扫描，仅输出计数，结果见 credential-scan.json。 |
| 2. 新旧产出与日志 | 通过 | 自行读取 page-before/page-after，与正式当前 page.json 对照；顶层字段、schema、各来源条目字段集合相同，当前 page 等于实施产物。独立读取真实日志倒数两条 summary，确认新旧数值，末行与精确 manual 运行 output 相等。快照 10-29/2120.json、10-30/1019.json，state.json、daily-2026-10-30.json 均存在。 |
| 3. 旧 launchd 退出、新调度防重 | 通过；晚间真实触发未知 | 当前 launchctl list 无旧 label，disable 状态为 disabled；原 `.plist` 不存在，`.plist.disabled-2026-10-30` 存在，其 SHA 与实施证据相同。回滚路径、enable/bootstrap 顺序可行；未执行。生产库副本 + 模块隔离时钟实测错过补跑一次、同日反复 tick 不重复、同分钟重启不重复、manual 不挤掉当晚计划；详见下文。 |
| 4. 脚本失败、其他任务 | 通过 | 在 `/tmp` 生产库副本把任务正文改为不存在脚本路径，得到 failed / 127，原文 `can't open input file` 与脚本路径保留、tee 日志与输出一致；随后另一隔离任务成功。实施新增测试亦以无效 Python 路径验证 failed / 127 与错误原文。 |
| 5. L1 权限与真实文件保护 | 通过 | 独立测试与真实浏览器设置页在 `/tmp` 各验证原 0644、0640 保存后保留；原文件不存在时保存成功且为 0600。真实兴趣清单 SHA256、mode、mtime_ns 前后对照见 protected-before/after.json。 |
| 6. 测试质量与负对照 | 通过 | 新 H2 测试用 tests.isolation_patches.module_time，仅替换 scheduled_tasks 的 time 绑定；测试写入 TemporaryDirectory(dir=/tmp)，patch 用上下文或 addCleanup 清理。两个守卫文件相对 Git HEAD 均无修改；故意改错的 `/tmp` 副本分别检出不推进 next_run、移除权限保留、移除 pipefail、共享 time 全局补丁。 |
| 7. 回归与浏览器 | 通过（沿用既有基线错误） | Python 全量仅跑一次：3638 项，0F/1E/7S，416.923 秒；唯一 E 的测试名和异常原文与实施全量日志逐项相同，无新增失败。定向 55/55、独立测试 5/5、浏览器 12/12。正式 8766 浏览器仅 GET/HEAD 抽查精确任务行，显示视频雷达、每天 21:20、启用、最近成功；0 写请求、0 页面异常。另随机端口真实设置页端到端保存验证权限。 |
| 8. 不重复采集与保护边界 | 通过 | 未点正式立即运行，未执行真实 run-video-radar.sh，未改任务、未重启 8766、未动其他 launchd/Grok/飞书配置；服务观察 PID=54621，与实施重启后的 PID 一致。 |

## 新旧数据独立对照

| 项目 | 10-29 launchd | 10-30 工作台 manual |
|---|---|---|
| 时间 | 21:20:06 至 21:22:19 +08:00 | 10:19:49 至 10:22:23 +08:00 |
| 页面 | workspace/video-radar/page.json | 相同 |
| schema / 字段 | video-radar-page/v1；generated_at、keyword、lists、schema、sources、today | 完全相同；三来源条目字段集合亦相同 |
| 小红书 / 实时 / 日榜条数 | 16 / 9 / 50 | 14 / 30 / 50 |
| api_calls | 15（6 / 4 / 5） | 45（36 / 4 / 5） |
| 各来源 failures | 0 / 3 / 1 | 0 / 0 / 0 |
| 来源 status | ok / partial / partial | ok / ok / ok |
| elapsed_s | 133.733 | 154.377 |

数量随榜单变化，仍处相同数量级。小红书调用增量为 30 次详情复采，实施报告说明与来源计数相符；本单没有额外调用外部 API 来重放采集。独立结果保存于 independent-comparison.json，非仅复述实施 comparison.json。

任务正文核对为：

```bash
set -o pipefail
/bin/zsh scripts/launchd/run-video-radar.sh 2>&1 | /usr/bin/tee -a workspace/video-radar-launchd.log
```

仅调用既有采集脚本，tee 追加原稳定日志，pipefail 保留失败退出码。报告不含凭据值。

## 防重语义与反例

源码 `claim()` 以 BEGIN IMMEDIATE 把运行插入与 next_run 推进放在同一事务；expected_due 防止旧到期值再次认领，one_active 防止同任务并发。仅 trigger=scheduled 推进 next_run，因此上午 manual 不影响今日晚间 due。构造生产 tasks.db 的只读 SQLite 备份，在独立临时副本删除其他任务并将目标正文换成 printf，避免误触采集；未向正式库写 SQL。

独立 5 项全部通过（review_cases.py / independent-tests.log）：

1. 上午 manual 成功后 due 仍为当日 21:20；推进到 21:20 跑一次 scheduled；完成后同分钟重建调度器并重复 tick，scheduled 仍只有 1 条，总计 1 manual + 1 scheduled，due 为次日 21:20。
2. 保留昨日 21:20 due 后重建，今日 21:25 连续 tick 并再次重建，只补 1 次，due 为次日 21:20。
3. 当分钟已持久化认领但未启动执行，重建后该运行标 interrupted，tick 不产生第二次运行。这里证明的是防止重复认领；中断运行不会自动重做，不能声称崩溃后仍保证成功产出。
4. 不存在脚本路径 failed / 127、错误原文和路径保留、日志一致，其他任务仍可 succeeded。
5. 0644、0640、新建文件 0600、reports_dir patch 退出恢复。

第一次独立失败用例对 zsh 错误文案误写为 `no such file or directory`；实际是 `can't open input file`。校正本单 `/tmp` 断言后通过，未改产品代码。

负对照 4 类均实际失败并指向预期断言：

| 故意改错 | 检出结果 |
|---|---|
| 删除 scheduled 后的 next_run 推进 | 原 H2 补跑测试报运行数 2 != 1 |
| 删除 fchmod 两行 | 原 L1 新测试两个 subTest 均报 mode 0600 != 0644/0640 |
| 移除任务正文 pipefail | 原 H2 故障测试报 succeeded != failed |
| `/tmp` 文件写共享 time.time 补丁 | 原静态守卫精确报 test_bad_clock.py:1 为违规；未扩大白名单 |

源码和守卫 SHA 见 source-audit.json，三份实施文件完整 SHA 与实施报告逐字一致。新增测试无真实 tasks.db、LaunchAgents 或真实 reports 写入路径；合成 plist 只位于 `/tmp`。

## 回归、保护与待主控见证

命令 `../article-importer/.venv/bin/python -m unittest discover -s tests`，本单只执行一次，结果 **3638 项、0 failures、1 error、7 skipped、416.923 秒**。进程退出码 1 来自唯一既有错误：`test_multi_segment_sequence_video_builds_director_timeline (test_video_lab.VideoLabTests.test_multi_segment_sequence_video_builds_director_timeline)`，`generation/native_h3.py:45` 抛出 `ValueError: 参考素材宽高须为 32 的倍数`。已对比 `/tmp/impl-hub-h2/full-python.log` 与更早的 `/tmp/article-studio-h4-review/python-full.log`，测试名与错误原文均完全相同；未修改受保护视频实验室代码。摘要见 python-full-summary.json。

主控 03:17:00Z 来信告知 H1 正在开展，可能修改 scheduled_tasks.py 等文件，要求以本单记录的 SHA 为复核基准。本单已阅读并 ack；收尾比对 source-audit.json 的 7 份源码（含 scheduled_tasks.py）全部未漂移，记录于 source-drift-at-finish.json。此结论只覆盖本报告所记录 SHA，不替后续 H1 交付背书。

正式兴趣文件验收前后均为 mode=0600，SHA256=`54554667a5b25a88e7c4042dad99e8ab160120f7d6452c2820a9054b07b95a99`，mtime_ns=`1793325130080727025`，三者完全不变。正式任务配置与该任务运行记录前后也完全不变（production-unchanged.json）；未新增真实执行。凭据最终扫描 82 个文件，匹配总数 0；覆盖任务库副本、运行记录、正式日志、实施与复核报告及两份证据目录。浏览器 profile、随机端口、副本报告目录、12 项断言均写在 browser-evidence.json；正式页截图 schedules-readonly.png、隔离保存截图 interest-fixture-save.png。

截至本单白天复核，生产仅有上午 manual 一条视频雷达记录。今晚 2026-10-30 21:20 还未发生，由主控按既有安排检查：该 task_id 当晚 scheduled 恰好 1 条、最终状态及 output 正常、旧 label 仍不在、原巡检日志新增对应 summary。该自然时间点未见证不算新增代码缺陷，也不能写成整轮夜间验收完成。

## 证据索引

- production-state.json、tasks-production-copy.db、log-history-summary.json、launchd-state.json：正式状态只读证据。
- independent-comparison.json：独立新旧产出结构、计数、summary 及路径核查。
- review_cases.py、independent-tests.log：5 项独立反例。
- targeted-tests.log：55 项定向结果；python-full.log / python-full-summary.json：单次全量结果及既有错误对照。
- negative-controls.json、no-due-advance.log、no-permission-preservation.log、no-pipefail.log、guard-negative.log：故意改错检出证据。
- source-audit.json、source-drift-at-finish.json：SHA256、守卫未改、收尾源码未漂移核对。
- browser-check.cjs、browser.log、browser-evidence.json、两张 PNG：独立 Playwright 用户路径。
- protected-before.json、protected-after.json、production-unchanged.json、credential-scan.json：最终保护对象、任务/运行未变与凭据计数。
