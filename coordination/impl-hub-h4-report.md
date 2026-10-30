# H4 实施交付报告

| 决定要点 | 结果 |
|---|---|
| 结论 | **通过（实施自检）**；已上线，待独立复核，不提交不推送 |
| H / M / L | 0 / 0 / 0（本单自查未发现遗留缺陷；未替代独立复核） |
| 核心源文件与 SHA256 前 12 位 | `scheduled_tasks.py` a80ae8333fbf；`schedules_assistant_api.py` 40ada06e080c；`server.py` 728aa7b491be |
| 看门狗文件与 SHA256 前 12 位 | `scripts/studio_watchdog.py` 90174bab84db；`run-studio-watchdog.sh` 17c1e442fc29；模板 plist fb1c0e9e177d |
| 测试与 SHA256 前 12 位 | `tests/test_studio_watchdog.py` cb92c748b01e；新增 20/20、调度回归 80/80、隔离浏览器 5/5 检查 |
| 部署 | 看门狗已 bootstrap；安装 plist SHA256 cf95d559b51a；900 秒一次、RunAtLoad=true；首轮正常 |
| 服务 PID | 99710 → **29588**；按合同 kickstart 一次，真实健康接口确认新代码已加载 |
| 保护边界 | settings/today 等 8 文件 SHA 未变；server.py 仅经主控授权增加 1 行；实际受保护对象 28 项 mtime 未变 |
| 证据 | `/tmp/article-studio-h4-evidence/`；实时日志 `workspace/logs/studio-watchdog.log`；完整 SHA256 见下表 |

## 范围与授权补充

已读实施合同、计划 v0.4 §3.7、handoff-r2 B 段与项目约定。本轮仅交付 H4，不涉及 H1/H2/H3。

真实 Handler 测试发现：server.py 固定条件未转发新路径，仅在 schedules_assistant_api.py 加分支会得到 404。通过本 Dispatch 的 `orca orchestration ask` 获得主控答复：**“授权单行路由条件：仅在 server.py 第 899-909 行的 schedules_assistant_api 分发条件里增加 (parts.path == '/api/schedules/health') 一项，其他行（含视频/generation 路由）一律不动”**。按此实施；改后 server.py **1336 行**，相对本单改动前副本的完整 diff 如下（仓库原来已有其他改动，未覆盖）：

```diff
--- /tmp/article-studio-h4-evidence/server.before.py	2026-10-30 10:11:04
+++ server.py	2026-10-30 10:11:04
@@ -897,6 +897,7 @@
                 from publishing.registry import capabilities
                 return self.reply(200, capabilities())
             if ((parts.path == '/api/schedules') or
+                    (parts.path == '/api/schedules/health') or
                     (len(segments)==4 and segments[:3]==['api','schedules','run']) or
                     (len(segments)==4 and segments[:2]==['api','schedules'] and segments[3]=='outsourced_prompt') or
                     (parts.path == '/api/schedules/runs') or
```

## 实现

- `GET /api/schedules/health` 无需 X-Article-Studio 头。仅返回六个健康字段；UTC ISO 带时区；不调用 listing、不返回任务正文/提示词/路径，错误返回固定安全文字。计数通过独立 SQLite `mode=ro` 连接读取，数据库缺失不会重建。
- 看门狗仅探测 `127.0.0.1`，默认 8766；TCP 与 HTTP 共用总计 5 秒的硬期限，忽略代理、不跟随重定向，响应上限 16 KiB。HTTP 非 200、无效 JSON、坏字段、过期心跳、last_error 非空和调度器 ok=false 均报告异常。
- 每轮一行 JSON；追加后将超过 1 MiB 时覆盖轮转一份 `.log.1`。仅写自身日志、状态、锁；非阻塞锁避免重复运行；状态原子替换。同一错误码 3600 秒内抑制重复通知，正常恢复通知一次；通知失败下轮重试。macOS 通知用固定 AppleScript + argv，接口原文不会进入通知或日志。
- 模板不含 EnvironmentVariables 或凭据，安装时仅替换 `__PROJECT_ROOT__`；shell 直接使用规定的项目 venv。未读取服务 plist 内容、未打印任何 plist；安装验证只用了 lint 和提取 StartInterval/RunAtLoad。

## 必测反例逐条结果

| 场景 | 实测结果 | 证据 |
|---|---|---|
| 服务不监听 | 通过：随机空闲端口一轮内异常、记录通知；真实通知演示耗时 0.202 秒，osascript 返回 0 | `real-notification.json`、`notification-demo/studio-watchdog.log` |
| HTTP 200，tick_age=400 | 通过：tick_stale，与 HTTP 状态独立判断 | `test_200_stale_tick_and_nonempty_error_are_independent_failures` |
| last_error 非空 | 通过：scheduler_error；原始敏感哨兵不进入日志/状态/通知 | 同上 |
| HTTP 延迟 6 秒 | 通过：总期限约 5 秒退出探测并通知，测试断言 4.8–5.5 秒 | `test_six_second_response_times_out_at_five_seconds` |
| 非 JSON / HTTP 非 200 | 通过：HTML、201、302、503、超长响应均异常不崩；302 不跟随 | `test_non_json_http_status_and_redirect_do_not_crash_or_follow` |
| 小时内去重、恢复一次 | 通过：3599 秒抑制、3600 秒重发；不同错误立即报；连续正常只恢复一次；重启复用状态 | `test_hourly_dedup_different_errors_and_one_recovery`、`test_dedup_survives_recovery_and_restart` |
| 不修改任务库/plist/Grok 数据 | 通过：临时反例文件字节+mtime 不变；真实 tasks.db、服务 plist、看门狗 plist、Grok 目录与文件合计 28 项 mtime/size 不变 | `readonly-snapshot.json`；对应测试 |
| 5 分钟内写异常日志 | 通过：缩短测试间隔 0.05 秒后制造 stale 并检出，断言小于 300 秒；真实空端口演示 0.202 秒 | `test_short_interval_detects_failure_within_five_minutes`、`real-notification.json` |
| 不误伤真实 8766 | 通过：故障注入均为随机隔离端口；真实服务仅按合同 kickstart 一次；浏览器仅两次 GET 健康接口 | `browser.json`、重启 PID 与真实样例 |
| 轮转/并发/坏状态/通知失败 | 通过：1 MiB 备份仅留一份；并发退出无重复通知；坏状态恢复；通知失败重试 | `targeted-tests.log` |
| 慢速响应/格式边界 | 通过：慢滴 HTTP 头受总期限限制；负数、字符串、bool、NaN/Infinity、缺字段拒绝；300 秒边界正常 | `targeted-tests.log` |

**15 分钟与 5 分钟的关系**：生产 StartInterval=900，正常调度、机器唤醒时，故障到下一次检查最长约 15 分钟，另加最多 5 秒探测及通知时间；休眠/系统调度可能延后。测试缩短调度间隔仅证明单轮能在 5 分钟内记日志，不能证明生产每 5 分钟检查。如黄总需要约 5 分钟发现，可由主控决定把 StartInterval 改为 300；本单保持合同的 900。

真实通知证据说明：已实际执行 osascript 并获得成功退出码；未使用 computer use，也未断言黄总亲眼看到通知。

## 已知弱点逐条自测

| 弱点 | 反例与结果 |
|---|---|
| W1 解析取错位置 | 通过：嵌套 history 先放相反 tick_age，分别验证根字段 2/嵌套 400 为正常、根 400/嵌套 2 为异常；HTTP 路由用真实 Handler 证明，未用字符串存在当作路由已接通 |
| W2 mock 泄漏 | 通过：全部 patch 都是 with 上下文；所有 Python 测试用 TemporaryDirectory/随机端口，Scheduler 的数据库均在 /tmp，未写真实 LaunchAgents/Grok/tasks.db；真实安装是合同授权部署步骤，另行记载 |
| W3 凭证外露 | 通过：日志/状态/通知丢弃接口原文，敏感哨兵反例无泄露；文件与运行产物凭据特征扫描 0 命中，见 `credential-scan.json`；未打印 plist |

## 测试、浏览器与线上证据

- 新增：`../article-importer/.venv/bin/python -m unittest discover -s tests -p 'test_studio_watchdog*.py' -v`：**20 项，0F/0E**，6.329 秒。
- 定向回归：`../article-importer/.venv/bin/python -m unittest discover -s tests -p 'test_schedule*.py'`：**80 项，0F/0E**，8.694 秒。
- 隔离 Playwright：`node /tmp/article-studio-h4-evidence/browser.cjs`，独立临时 profile、headless Chrome：**5/5 检查通过**，访问真实服务 GET，无自定义头、六字段/时区正确，刷新后心跳前进。截图 `health-browser.png`。未进行全量 Python/UI，未占用全量测试资源。
- `/bin/sh -n`、模板和已安装 plist 的 `plutil -lint` 均通过。提取非敏感键：StartInterval=900，RunAtLoad=true。

真实健康返回样例：

```json
{
  "ok": true,
  "last_tick": "2026-10-30T02:11:40.339867+00:00",
  "last_error": null,
  "now": "2026-10-30T02:11:40.547963+00:00",
  "tick_age_seconds": 0.208096,
  "running_count": 0
}
```

`launchctl list` 记录（短任务已正常结束，因此 PID 为 `-`，退出状态为 0）：

```text
-	0	com.servasyy.article-studio-watchdog
```

真实看门狗日志前几行：

```jsonl
{"time": "2026-10-30T02:12:09.935638+00:00", "port_listening": true, "http_status": 200, "tick_age_seconds": 2.019487, "last_error_empty": true, "errors": [], "conclusion": "ok", "notification": "none"}
{"time": "2026-10-30T02:12:25.950595+00:00", "port_listening": true, "http_status": 200, "tick_age_seconds": 0.461779, "last_error_empty": true, "errors": [], "conclusion": "ok", "notification": "none"}
```

## 改动文件与完整 SHA256

| 文件 | SHA256 |
|---|---|
| `scheduled_tasks.py` | `a80ae8333fbf05a39d31d42b0679ec3a5b2b54b02aed7e857d92af4406f1679e` |
| `schedules_assistant_api.py` | `40ada06e080c9c7f09b5738794e7d671fedbae5b205df726062d97d0ca61bc1a` |
| `server.py` | `728aa7b491bec880ffb3794a7149df695024afab5a43e4212cc6f28d4db7ea28` |
| `scripts/studio_watchdog.py` | `90174bab84db0986110f0870fac1c8e3fb5962493d15514554501528fc39995a` |
| `scripts/launchd/run-studio-watchdog.sh` | `17c1e442fc29a45a46931aa95dc5b4e2d13212d3f139c7dcccf3e5b27c993e15` |
| `scripts/launchd/com.servasyy.article-studio-watchdog.plist` | `fb1c0e9e177de6985eec7283d93fd5ea35f43b8141d915ab83ea6e308846679c` |
| `tests/test_studio_watchdog.py` | `cb92c748b01e877ef072c4ca7236131f123196eb873130079f241d22309fbbcc` |
| `/Users/huangzongning/Library/LaunchAgents/com.servasyy.article-studio-watchdog.plist` | `cf95d559b51a465e349813c42b6919507b4c4ff5f2214fe0d8e56fc218b10a9e` |

另新增本报告 `coordination/impl-hub-h4-report.md`。看门狗实际日志/状态/锁为运行产物。全量证据位于 `/tmp/article-studio-h4-evidence/`，报告已内嵌关键结果；临时证据目录由本单创建。

## 回滚与待办

- 停用新增看门狗可执行 `launchctl bootout gui/$(id -u)/com.servasyy.article-studio-watchdog`；再按需要移除本单安装 plist。此处仅记载方法，未执行回滚。
- 代码回滚只撤销本单新增 health 方法、路由两处、看门狗文件和测试；server.py 只移除上述新增一行，**不得 git checkout 覆盖共享工作区既有改动**。旧 server.py 本单前副本在证据目录。
- 剩余：由独立检查窗口复核；是否把 900 秒改 300 秒由主控/黄总决定。本单无其他待实施项，不提交、不推送，不启动下一轮任务。
