# 中枢期 H4：调度器心跳接口 + launchd 看门狗（实施合同，最先做）

## 背景
WorkBuddy 不再打开，原「内容工作台健康巡检」停了，现在没有任何东西在检查工作台是否健康。调度器跑在服务进程里，服务挂了它自己发现不了。

## 要做
1. **心跳接口**：`ScheduledTasks.last_tick`、`last_error` 已存在（scheduled_tasks.py 约 54、226、436 行），确认是否已随 /api/schedules 对外返回；新增一个轻量只读接口（建议 `GET /api/schedules/health`，放 schedules_assistant_api.py，不改 server.py），返回 `{ok, last_tick, last_error, now, tick_age_seconds, running_count}`，last_tick 用带时区的 ISO 时间。不需要 X-Article-Studio 头（只读、无敏感信息），但不得返回任务正文、提示词、路径中的用户数据。
2. **看门狗脚本**：`scripts/launchd/run-studio-watchdog.sh`（可调用一个小 Python 文件），检查：① 127.0.0.1:8766 在监听且 `/api/schedules/health` 5 秒内返回 200；② tick_age ≤ 300 秒；③ last_error 为空。
   - 结果写 `workspace/logs/studio-watchdog.log`（每次一行 JSON：时间、各项结果、结论 ok/异常）；日志超过 1 MiB 轮转一份。
   - 异常时发 macOS 通知（`osascript -e 'display notification ...'`）；同一异常 1 小时内只通知一次，恢复正常时通知一次「已恢复」（状态存 workspace/logs/studio-watchdog.state.json）。
   - **只报告，不改任何配置，不重启服务，不写 tasks.db**。
3. **launchd 任务**：`com.servasyy.article-studio-watchdog`，StartInterval=900，RunAtLoad=true；plist 模板入仓 `scripts/launchd/com.servasyy.article-studio-watchdog.plist`（不含任何密钥、不用 EnvironmentVariables 放 Key），安装到 ~/Library/LaunchAgents 并 `launchctl bootstrap gui/$(id -u)`（黄总已授权本单安装此任务）。安装后 `launchctl list | grep watchdog` 证明已加载，并等它跑出第一行日志。

## 允许改动
schedules_assistant_api.py、scheduled_tasks.py（仅心跳相关最小改动）、新建 scripts/launchd/run-studio-watchdog.sh 与可选 scripts/studio_watchdog.py、新建 plist 模板、新增 tests/test_studio_watchdog*.py；~/Library/LaunchAgents/com.servasyy.article-studio-watchdog.plist（安装）。**不得改** server.py、settings_view.py、public/settings.*、today_view.py、public/today.*（属于 1 期兴趣通道与后续 H1）。

## 必测反例（逐条实测，报告给结果）
- 服务停掉（隔离环境：随机端口起一个副本再停掉，或指向一个没人监听的端口）→ 一次运行内写出异常日志并发通知（测试里把通知命令替换成记录，真实环境演示一次真实通知即可）。
- 接口返回 200 但 tick_age=400 秒 → 异常；last_error 非空 → 异常；接口超时 6 秒 → 异常；返回非 JSON → 异常，不崩。
- 1 小时内重复异常只通知一次；恢复时通知一次。
- 看门狗运行期间不修改任何文件以外的状态（证明：运行前后 tasks.db、plist、~/grok-bot-reports 的 mtime 不变）。
- 「5 分钟内写出异常日志」：用缩短间隔的测试配置证明从故障发生到日志写出 ≤ 5 分钟（真实任务是 15 分钟一跑，报告说明两者关系：最坏 15 分钟发现，若黄总要求 5 分钟可把 StartInterval 调到 300，写在报告里供主控决定）。
- 不能误伤真实 8766：任何测试都不得停、杀、重启真实服务（真实重启只按通用段的 kickstart 做一次，用于加载新接口）。

## 交付
coordination/impl-hub-h4-report.md；附 `/api/schedules/health` 真实返回样例（脱敏不需要，因为无敏感字段）、看门狗日志前几行、launchctl list 结果。
## 通用
- 派单：主控 term_b884acc7（run_17dc13f37a10）；实施 Codex term_02017284；复核 Codex term_57dd3162（不同窗口）。
- 仓库 /Users/huangzongning/development/article-studio；Python ../article-importer/.venv/bin/python。8766 重启 `launchctl kickstart -k gui/$(id -u)/com.servasyy.article-studio`；查 PID 只用 `launchctl list | awk '$3=="com.servasyy.article-studio"{print $1}'`。**禁止** launchctl print / ps eww / env / cat 或 plutil 打印任何 plist 内容（会把 AISA_API_KEY 打进输出）；需要确认 plist 结构时只用 `plutil -extract <键> raw` 取**非密钥**键，或 `plutil -lint`。
- 依据：coordination/twitter-intel-plan.md v0.4 第 3.7 节与第 4 节「中枢期」；coordination/twitter-intel-handoff-r2.md B 段。
- 约束：不动 Grok Bot 设置；不碰飞书；不停推特 AIsa 采集（com.servasyy.radar-monitor 不动）；WorkBuddy（~/.workbuddy-ai/）只读不写，本单不需要读；视频实验室保护边界（public/lab-test.*、generation/*、server.py 视频/generation 路由、tests/test_*lab*）不动。
- Codex 已知弱点（逐条自测并在报告给结果）：W1 解析取错位置（T5 教训：正则取第一处）；W2 测试 mock 泄漏（patch 一律 addCleanup/上下文管理器，测试不写真实 ~/grok-bot-reports、~/Library/LaunchAgents、真实 tasks.db）；W3 凭证外露（日志、报告、任务库、运行记录里不得出现 Key；用 grep 自证）。
- 全量 Python 与 UI 跑前向主控登记。基线：Python 以 1 期交付时数字为准（约 3612 项 0F/1E/7S，E=受保护 video_lab），UI 723 项 719P/3F/1S（3F=受保护 lab-test），不得新增失败。
- 浏览器只用隔离 Playwright（独立 profile；随机端口或对 8766 只读访问），禁止 computer use，不操作黄总真实桌面或 Chrome。不提交、不推送。
- 报告开头「决定要点」表（≤10 行：结论、H/M/L 自查、改动文件与 SHA256、测试数字、新 PID、证据路径），完成后 worker_done。
