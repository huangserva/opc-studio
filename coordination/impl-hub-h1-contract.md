# 中枢期 H1：外部任务心跳 + 设置页定时任务总览（实施合同，1 期兴趣通道收口后做）

## 要做
1. **外部任务登记表**（入仓，建议 `external_tasks.json` 或 external_tasks.py 常量）：每项 名称、归属（Grok / launchd）、计划（cron 或时刻列表，Asia/Shanghai）、产出路径规则、宽限分钟。初版：
   | 任务 | 计划 | 产出 | 宽限 |
   |---|---|---|---|
   | Grok 日报 | 每天 9:00 | `~/grok-bot-reports/daily/<日期>.json` | 60 |
   | Grok 快报 | 8/12/16/20 点 | 该时段后出现的 `alerts/<日期>-*.json` | 60 |
   | Grok 兴趣通道 | 10:30 / 22:30 | 该时段后出现的 `interest/<日期>-*.json` | 60 |
   | Grok 周报 | 周日 20:00 | `weekly/` 下该时点后新文件 | 120 |
   | Grok 收藏 digest | 周一 8:54 | **待确认**（不编路径；状态显示「待确认」） | — |
   | 推特雷达（4 期停之前） | 6–22 点每 2 小时 | `workspace/twitter-radar/snapshots/` 新文件 | 30 |
   | 看门狗（H4） | 每 15 分钟 | `workspace/logs/studio-watchdog.log` 新行 | 30 |
   另在总览里只读列出常驻 launchd 服务 com.servasyy.wechat-radar、com.servasyy.wechat-source（用 `launchctl list` 的 PID/退出码显示运行中/已退出，不读 plist 内容）。Grok 目录沿用 grok_bot_radar.reports_dir()。
2. **状态判定**（只读，不触发外部任务）：正常（宽限内出现）/ 等待中（计划时点已到但仍在宽限内）/ 逾期（过了宽限仍未出现）/ 缺失（到了下一周期仍没有）。由调度器定时（如每 5 分钟）计算并缓存，接口读缓存，< 1 秒。Grok 兴趣通道「本次无」也会落空 json（handoff A 段第 1 条），空文件算正常。
3. **设置页「定时任务」区改成总览**：内置任务与外部任务同一张表；外部任务标「外部 · 只读」，显示计划、上次产出时间、状态（颜色 + 文字）；内置任务保留现有开关、立即运行、运行记录。schedules.html 已在旧版分组，不改。

## 允许改动
新建 external_tasks.py/json、scheduled_tasks.py（挂定时计算）、settings_view.py、schedules_assistant_api.py（如需新接口）、public/settings.html|js|css、新增测试与 e2e。开工前与 1 期兴趣通道最终 SHA 比对，settings 相关文件以 1 期收口版本为基线。

## 必测反例与验收
- 在测试副本里让某外部任务产出缺失：先「等待中」→ 过宽限「逾期」→ 到下一周期「缺失」；补上文件后「正常」。用可注入的时钟测，不 sleep。
- 产出目录不存在、无权限、文件名不符合规则、时区跨零点（北京时间 00:30 判前一天 22:30 兴趣通道）、周报跨周。
- 真实浏览器（隔离 Playwright）：设置页总览能看到全部内置与外部任务；在隔离端口副本里演示一个「逾期」并截图（1440 与 390）。
- 交付：coordination/impl-hub-h1-report.md。
## 通用
- 派单：主控 term_b884acc7（run_17dc13f37a10）；实施 Codex term_02017284；复核 Codex term_57dd3162（不同窗口）。
- 仓库 /Users/huangzongning/development/article-studio；Python ../article-importer/.venv/bin/python。8766 重启 `launchctl kickstart -k gui/$(id -u)/com.servasyy.article-studio`；查 PID 只用 `launchctl list | awk '$3=="com.servasyy.article-studio"{print $1}'`。**禁止** launchctl print / ps eww / env / cat 或 plutil 打印任何 plist 内容（会把 AISA_API_KEY 打进输出）；需要确认 plist 结构时只用 `plutil -extract <键> raw` 取**非密钥**键，或 `plutil -lint`。
- 依据：coordination/twitter-intel-plan.md v0.4 第 3.7 节与第 4 节「中枢期」；coordination/twitter-intel-handoff-r2.md B 段。
- 约束：不动 Grok Bot 设置；不碰飞书；不停推特 AIsa 采集（com.servasyy.radar-monitor 不动）；WorkBuddy（~/.workbuddy-ai/）只读不写，本单不需要读；视频实验室保护边界（public/lab-test.*、generation/*、server.py 视频/generation 路由、tests/test_*lab*）不动。
- Codex 已知弱点（逐条自测并在报告给结果）：W1 解析取错位置（T5 教训：正则取第一处）；W2 测试 mock 泄漏（patch 一律 addCleanup/上下文管理器，测试不写真实 ~/grok-bot-reports、~/Library/LaunchAgents、真实 tasks.db）；W3 凭证外露（日志、报告、任务库、运行记录里不得出现 Key；用 grep 自证）。
- 全量 Python 与 UI 跑前向主控登记。基线：Python 以 1 期交付时数字为准（约 3612 项 0F/1E/7S，E=受保护 video_lab），UI 723 项 719P/3F/1S（3F=受保护 lab-test），不得新增失败。
- 浏览器只用隔离 Playwright（独立 profile；随机端口或对 8766 只读访问），禁止 computer use，不操作黄总真实桌面或 Chrome。不提交、不推送。
- 报告开头「决定要点」表（≤10 行：结论、H/M/L 自查、改动文件与 SHA256、测试数字、新 PID、证据路径），完成后 worker_done。
