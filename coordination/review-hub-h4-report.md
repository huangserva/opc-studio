# H4 独立复核报告

| 决定要点 | 结果 |
|---|---|
| 结论 | **不通过**：需修正看门狗健康响应字段漏校验；复核任务已完成 |
| H / M / L | **0 / 1 / 0**；M1 为单一根因，9 个独立反例复现 |
| 真实接口与浏览器 | 真实 8766、隔离 headless Chrome 独立 profile，**7/7**；仅 GET，无自定义鉴权头 |
| 独立反例 | **26 检查：17 通过 / 9 失败**；9 失败均为 M1；6 秒响应于 **5.005 秒**判超时 |
| 测试检错能力 | 故意把阈值 300 改为 3000 的 `/tmp` 副本，被现有测试检出 **1F/0E（预期）**；正式代码未动 |
| Python 全量 | **3633 项，1F/1E/7S，411.970 秒**；F 属在途 H2 时钟补丁，E 属既有受保护 video_lab；H4 未出现对应失败 |
| UI 全量 | **723 项，719P/3F/1S，285.859 秒**；3F 与基线相同；清理阶段有人工结束自有子进程，完整限制见下文；该测试单跑 **1/1** |
| SHA 与路由 | 7 个代码/测试文件及已安装 plist SHA 与实施报告一致；server 相对 before 副本仅新增授权 1 行 |
| 改动与证据 | 仅新增本报告；源码 SHA 见下表；证据 `/tmp/article-studio-h4-review/`；未重启/停止真实 8766，未提交推送 |

## 阻断项 M1：健康响应缺字段或类型错仍判正常

位置：`scripts/studio_watchdog.py:115–134`。目前仅验证 `ok`、`tick_age_seconds`、`last_error`，完全不验证 `last_tick`、`now`、`running_count`。独立随机端口 HTTP 服务返回下列精简响应，结果是 `errors=[]`、`conclusion="ok"`，没有异常通知：

```json
{"ok":true,"tick_age_seconds":1,"last_error":null}
```

在完整正常响应里，分别删除 `last_tick` / `now` / `running_count`，或分别将这些字段改为 `[]`、`false`，共 **9/9** 被误判正常。该项违反复核合同第 3 项“JSON 缺字段/类型错均判异常”；接口回退、响应结构损坏时会漏报。证据：`independent.py`、`independent-results.json`（字段校验开头的 9 条）。

建议修复：明确要求六字段齐全；`last_tick`、`now` 必须为带时区的有效 ISO 时间，`running_count` 必须为非负整数并拒绝 bool；损坏字段返回固定 `invalid_health`，不把原文写日志。按真实接口的异常状态设计允许的 null 值，同时必须保持异常结论。追加缺字段、类型错、无时区/无效日期和负数/bool 计数反例。此处属于原合同校验要求，不扩展功能范围；只读复核未修改实现。

## 合同逐项结论

| 项 | 结论 | 独立证据与说明 |
|---|---|---|
| 1 接口、数据边界与分发 | 通过 | `browser.json`：真实无鉴权头 GET 200，键恰为六项、时间带时区，刷新心跳前进；`routes.json`：隔离真实 Handler 注入私有哨兵，last_error 返回固定安全文字。源码 `ScheduledTasks.health()` 使用只读 SQLite `mode=ro`，不调用 listing、不读取任务正文。无路径/正文泄露，未发现需另计级别的 last_error 风险。 |
| 1 其他方法/相近路径 | 通过 | 真实浏览器 GET `/healthx`、`/health/1`、`/health/` 均 404，`/health?probe=1` 为 200；随机端口真实 Handler：POST 404、PUT 501、DELETE 404、HEAD 200、OPTIONS 501。HEAD 沿用 do_GET 并省略响应体，行为合理；真实服务仅做 GET。 |
| 2 server 最小改动 | 通过 | `server.diff` 相对 `/tmp/article-studio-h4-evidence/server.before.py` 仅新增 `(parts.path == '/api/schedules/health') or` 一行；视频/generation 路由未动。 |
| 3 异常探测 | **失败（M1）** | 拒连、6 秒超时、200+age400、last_error 非空、非 JSON、age 字符串、缺 last_error 均判异常且不崩；正常判 ok。但其余三个字段缺失/类型错漏报。 |
| 4 去重、恢复与坏状态 | 通过 | `independent-results.json`：同错误 3599 秒抑制、3600 秒重发；不同错误码立即通知，切回已通知码仍按其各自一小时窗口抑制；正常恢复一次、持续正常不重发。状态为空、坏 JSON、数组、null、last_notifications 类型错均不崩。 |
| 5 JSON 日志、轮转与凭据 | 通过 | 正常每轮一行 JSON；独立构造 1 MiB+1 日志和旧备份，运行后 `.log.1` 正确覆盖且只保留一份，当前日志一行可解析。私有哨兵未进入日志/状态/通知；凭据特征扫描零命中，见 `credential-scan.json`。真实日志已有正常运行 JSON 行。 |
| 6 只报告、不改动 | 通过 | `protected-snapshot.json`：真实 tasks.db、所有现有 LaunchAgents plist、Grok 目录/文件共 **41** 对象，在独立看门狗调用前后 mtime/size 一致。源码无服务管理、杀进程、写库语句；自身写入仅日志/状态/锁和轮转备份。 |
| 7 launchd | 通过 | `launchctl list` 包含 watchdog（退出码 0）；安装及模板 plutil lint OK，非敏感键 StartInterval=900、RunAtLoad=true；提取 EnvironmentVariables 返回不存在。已安装 plist SHA 匹配实施报告；`real-watchdog-log-summary.json` 记录真实日志正常行。未打印任何 plist 内容。 |
| 8 新测试质量 | 通过（覆盖不足由 M1 单列） | 新测试均使用临时目录/随机端口；mock patch 使用上下文管理器，资源有 addCleanup/finally，无真实库/Grok/LaunchAgents 写入。故意改错 `/tmp` 副本后，既有过期心跳测试明确失败，见 `mutation.log`；未覆盖 M1 的三个字段，须补测。 |
| 9 全量回归 | **有新增非 H4 失败；UI 退出需干预** | Python 新增 1F 精确命中在途 H2 新测试，详见下节；UI 计数与基线一致，但不能当作无干预全量退出证据。 |
| 10 发现时延 | 通过（事实说明） | 900 秒间隔意味着正常唤醒调度下，下一轮探测最坏等待约 15 分钟，再加最多 5 秒探测和最多 5 秒通知后才落日志；不能承诺生产 5 分钟发现。300 秒的影响见下节。 |

## 全量回归、归因与限制

本单已登记全量资源，Python 与 UI 各启动一次，完整输出分别为 `python-full.log`、`ui-full.log`，退出码文件均为 1。

- Python 命令：`../article-importer/.venv/bin/python -m unittest discover -s tests`。3633 项，1F/1E/7S。E 为既有 `test_video_lab.VideoLabTests.test_multi_segment_sequence_video_builds_director_timeline`，栈进入受保护 generation/native_h3 路径，与合同基线对应。新增 F 是 `test_no_global_time_patch.NoGlobalTimePatchTests.test_no_global_time_patch_in_tests`，仅列出 `tests/test_video_radar_schedule_h2.py:56,70` 的 `patch('scheduled_tasks.time.time', ...)`；该文件不在 H4 改动清单、属于并行在途 H2，已向主控发送具体位置。**不得称当前共享工作区 Python 回归无新增失败**；归因依据是失败原文列出的文件与 H4 SHA 清单，未修改或重跑该 H2 测试。
- H4 新增 20 项已随 Python discover 执行，没有相应测试失败或错误；与独立反例发现 M1 并不矛盾，原因是原测试没有覆盖那三个字段。
- UI 命令：`npm run test:ui`。完整 TAP 最终计数 723、719P/3F/1S，3F 全在受保护 `tests/test_lab_test_drama_ui.cjs`，与基线相同。清理阶段 `test_radar_links_e2e.cjs` 超过 4 分钟未退出，输出缓冲在第 663 项；源码先注册 `server.close(callback)` 再注册 `browser.close()`，当时测试子进程仅余一个随机端口已建立连接。**观察为清理等待，具体连接为何未及时释放仍不确定**，不能只凭时长断言根因。
- 根据主控队列回复 `msg_11bcbb0dbd2e`，仅对本轮已记录的自有测试子进程 PID 43527 发 SIGTERM，未用 pkill，未碰服务或其他窗口。随后该项输出通过（277.086 秒），全量父进程完整输出上述计数并退出。因此 **有完整计数，但进程退出有干预**，不能宣称无干预全量完成。
- 主控要求单独重跑的 `node --test tests/test_radar_links_e2e.cjs`：**1/1 通过，1.930 秒正常退出，无干预**，见 `ui-radar-single.log`、`ui-radar-single-result.json`。未稳定复现挂起。该测试只读 twitter-radar 静态快照并开随机端口，不导入/调用 H4 心跳接口或看门狗；未发现其与 H4 直接相关的证据，不在本单扩展修复。

## 900 秒改为 300 秒的实际影响

正常运行每天探测次数由 **96 次增加到 288 次**，日志写入量约三倍，1 MiB 轮转更频繁；日志仍只保留当前文件和一份备份。按错误码的一小时去重时间不变，同一持续异常不会变成每五分钟通知；新错误和恢复可以更早通知，异常抖动时恢复提示也可能更早出现。

300 秒能把等待下一轮的最坏时间从约 15 分钟降到约 5 分钟；考虑最多 5 秒探测、5 秒通知及系统调度/休眠，仍不能保证严格 300 秒内落日志。本轮未修改间隔，留主控/黄总决策。

## 核对的版本、产物与保护边界

| 文件 | SHA256 |
|---|---|
| `scheduled_tasks.py` | `a80ae8333fbf05a39d31d42b0679ec3a5b2b54b02aed7e857d92af4406f1679e` |
| `schedules_assistant_api.py` | `40ada06e080c9c7f09b5738794e7d671fedbae5b205df726062d97d0ca61bc1a` |
| `server.py` | `728aa7b491bec880ffb3794a7149df695024afab5a43e4212cc6f28d4db7ea28` |
| `scripts/studio_watchdog.py` | `90174bab84db0986110f0870fac1c8e3fb5962493d15514554501528fc39995a` |
| `scripts/launchd/run-studio-watchdog.sh` | `17c1e442fc29a45a46931aa95dc5b4e2d13212d3f139c7dcccf3e5b27c993e15` |
| `scripts/launchd/com.servasyy.article-studio-watchdog.plist` | `fb1c0e9e177de6985eec7283d93fd5ea35f43b8141d915ab83ea6e308846679c` |
| `tests/test_studio_watchdog.py` | `cb92c748b01e877ef072c4ca7236131f123196eb873130079f241d22309fbbcc` |

已安装看门狗 plist SHA256：`cf95d559b51a465e349813c42b6919507b4c4ff5f2214fe0d8e56fc218b10a9e`。复核开始及结束前读取的 H4 文件版本与实施报告一致；报告以该版本为准，不覆盖其他工人的在途文件。

主要证据：`independent.py` / `independent-results.json`（独立反例）、`browser.cjs` / `browser.json` / `health-browser.png`（真实浏览器）、`routes.py` / `routes.json`（随机端口真实 Handler）、`server.diff`、`protected-snapshot.json`、`mutation.log`、两份全量日志、单测重跑日志、`credential-scan.json`、`reviewed-sha256.json`。所有临时构造均在 `/tmp/article-studio-h4-review/`，报告本身为唯一正式目录改动。

未执行真实 8766 的停止/重启/卸载，未动 Grok Bot 设置、飞书、雷达采集或真实用户桌面，未用 computer use，未提交/推送。浏览器验收阶段真实服务 PID 为 29588；收尾只读核对时为 54621，本窗口未发送任何服务重启指令，PID 变化来源本单未核实。收尾复算全部 7 个 H4 文件 SHA 无漂移，见 `end-version-drift.json`。后续由实施窗口修复 M1，检查窗口按原班底复验；H2 时钟隔离失败由其所属线处理，本单不擅自修复。
