# 中枢期 H4 心跳接口 + 看门狗 复核合同

派单：主控 term_b884acc7（run_17dc13f37a10）；复核 Codex term_57dd3162（实施是 term_02017284，task_7897be008b4a）。
仓库 /Users/huangzongning/development/article-studio；Python ../article-importer/.venv/bin/python。查 PID 只用 `launchctl list | awk '$3=="com.servasyy.article-studio"{print $1}'`。**禁止** launchctl print / ps eww / env / cat 或 plutil 打印任何 plist 内容（会把 AISA_API_KEY 打进输出）；确认 plist 结构只用 `plutil -extract <非密钥键> raw` 或 `plutil -lint`。

## 要读
实施合同 coordination/impl-hub-h4-contract.md；实施报告 coordination/impl-hub-h4-report.md（先核对报告里的文件 SHA256 与工作区一致）。

## 复核重点（逐条 通过/失败/未知 + 证据；独立构造反例，不只跑工人的测试）
1. 接口：真实 8766 `GET /api/schedules/health` 字段恰为 ok/last_tick/last_error/now/tick_age_seconds/running_count，last_tick 带时区；不需要 X-Article-Studio 头；不返回任务正文、提示词、路径或任何用户数据（看源码确认 last_error 是否可能带出路径/正文，若会，给建议级别）。别的方法（POST 等）和相近路径（/api/schedules/healthx、/api/schedules/health/1）行为合理、不误转发。
2. server.py：相对 /tmp/article-studio-h4-evidence/server.before.py 只多 1 行（授权的 `/api/schedules/health` 条件），视频/generation 路由未动。
3. 看门狗判定：独立反例（随机端口副本或无人监听端口，不得停/杀/重启真实 8766）——拒连、超时 6 秒、200 但 tick_age=400、last_error 非空、非 JSON、JSON 缺字段/类型错（如 tick_age 为字符串）→ 均判异常且脚本不崩；正常 → ok。
4. 通知去重：1 小时内同一异常只通知一次；恢复只通知一次；不同异常类型切换时的行为说明清楚；状态文件损坏/为空时不崩。
5. 日志：每次一行 JSON；超过 1 MiB 轮转一份（独立构造验证）；日志不含 Key（W3：grep 日志、状态文件、报告、plist 模板）。
6. 只报告不改动：看门狗运行前后 tasks.db、~/Library/LaunchAgents 下其他 plist、~/grok-bot-reports 的 mtime 不变；脚本里没有 kickstart/kill/写库语句。
7. launchd：`launchctl list` 有 com.servasyy.article-studio-watchdog；`plutil -extract StartInterval raw` = 900、RunAtLoad = true；plist 不含 EnvironmentVariables（用 `plutil -extract EnvironmentVariables raw` 看是否报不存在即可，不要打印内容）；workspace/logs/studio-watchdog.log 已有真实运行行。
8. 测试质量（W2）：新测试的 patch 都有清理；测试不写真实 ~/grok-bot-reports、~/Library/LaunchAgents、真实 tasks.db；用一个故意改错的看门狗副本证明测试测得出错。
9. 全量回归：Python 全量与 UI 全量各跑一次（本单已登记，实施窗口此时可能在做 H2，若它也要跑全量会先向主控登记，主控会错开）。基线 Python 约 3613 项 0F/1E/7S（E=受保护 video_lab），UI 723 项 719P/3F/1S（3F=受保护 lab-test），不得新增失败；若有新增失败，判断是否 H4 引起。
10. 5 分钟发现：给出最坏发现时间（StartInterval 900 → 最坏约 15 分钟）与改成 300 秒的影响（日志量、通知频率），供主控决定。

## 规则
只读复核，不改正式代码；验证改动在 /tmp 副本做。禁止 computer use，浏览器（如需）只用隔离 Playwright。不停/不重启真实 8766，不卸载看门狗。不动 Grok Bot 设置、不碰飞书、不停推特 AIsa 采集（com.servasyy.radar-monitor）。不提交不推送。

## 报告
coordination/review-hub-h4-report.md，开头「决定要点」表（≤10 行：结论 通过/不通过、H/M/L、关键证据、测试数字、全量结果）。完成 worker_done。
