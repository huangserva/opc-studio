# H1-L1：外部心跳刷新异常隔离

## 决定要点

| 项目 | 结果 |
|---|---|
| 结论 | 通过：实现、隔离故障反例与正式只读验收均通过 |
| H/M/L 自查 | 0H / 0M / 0L |
| 业务改动 | scheduled_tasks.py（仅循环异常边界）、external_tasks.py、public/settings.js（1 行） |
| 测试改动 | 新增 tests/test_external_refresh_isolation.py；扩展 tests/e2e/external_tasks.cjs 与 fixture |
| SHA256 | 完整值见下表及 /tmp/impl-small-h1-l1/final-sha.json |
| 测试数字 | 定向 Python 82/82；隔离浏览器 21/21；旧副本 3 项中 2F/1P（预期） |
| 全量 | 主控明确同意不重复 Python 全量，避免与 A 窗口 R3-0 争用 |
| 正式加载 | A 按主控批准统一重启；本窗口亲自核对 PID 23580→77037、新 external 元数据与 health.ok=true |
| 证据 | /tmp/impl-small-h1-l1/ |

## 实现行为

- tick 保留原 try/except 与固定 last_error 文案；tick 失败时仍不执行外部刷新，health.ok=false 的原行为保留。
- tick 成功后单独调用 external_tasks.refresh；未知异常仅标记 external.last_error="外部心跳刷新失败" 与 external.last_error_at（UTC ISO 时间），不写调度器 last_error。
- 外部快照 rows 与 checked_at 仅在整轮刷新成功后原子替换；失败时保持旧值，错误原文不进入接口。存在错误标识时跳过 5 分钟缓存限流，下一轮可立即重试；成功刷新同时清除两个错误字段。
- 设置页通过 textContent 在原检查时间旁提示失败时间与“保留上次快照”；不增加接口与操作入口。

## 验证与反例

Python 定向命令：

```sh
../article-importer/.venv/bin/python -m unittest tests.test_external_refresh_isolation tests.test_external_tasks tests.test_scheduled_tasks tests.test_schedules_http tests.test_schedules_management tests.test_settings_view tests.test_video_radar_schedule_h2
```

82 项全部通过（directed.log）。新增 3 项使用真实 loop、health、只读 schedules API 与 /tmp 数据库，覆盖：

1. refresh 连续两次 RuntimeError，三轮 tick 都执行；前两轮 health.ok=true、调度 last_error 为空，外部旧 rows/checked_at 保留，独立错误与时间存在；第三轮恢复后清空标识。异常哨兵不出现在 API 返回。
2. 局部 rows 已计算出不同值后，service_reader 抛 RuntimeError；不得发布半份结果，旧快照与正常调度健康保留；恢复刷新清空错误。
3. tick 连续抛异常，仍保留原固定 last_error、health.ok=false、last_tick 不更新，外部刷新不执行。

旧版负向证明：把实施前 scheduled_tasks.py、external_tasks.py 放入 /tmp/impl-small-h1-l1/old，与同一份新测试运行；2 项在 health.ok 断言处失败、tick 原行为测试通过，0 errors（old-tests.log）。未就地替换共享工作区。

浏览器命令：

```sh
H1_EVIDENCE_DIR=/tmp/impl-small-h1-l1/browser node tests/e2e/external_tasks.cjs
```

隔离随机端口、/tmp 工作区与独立 Chromium profile，21 项全部通过（browser.log、browser/browser-results.json）。故障由隔离 fixture 控制文件触发真实调度循环，未对正式服务注入；覆盖 API 旧快照/独立错误、health.ok=true、1440/390 提示与无页面溢出、恢复后提示消失，并复用原 H1 内置暂停/恢复/立即运行与运行记录路径。故障截图 browser/refresh-failed-1440.png 与 browser/refresh-failed-390.png 已目视检查。

## 已知弱点逐条自测

- W1：分别断言顶层 last_error、scheduler_health.last_error、health.last_error 为空，并单独读取 external.last_error；快照比较包含全部任务与 checked_at，避免只核对一行或取错字段。通过。
- W2：全部 patch 使用上下文管理器；TemporaryDirectory 通过 addCleanup 清理，ScheduledTasks.close 同样注册清理；模拟存活线程的 patch 在 close 前恢复，不遗留 mock。新增测试与浏览器 fixture 只使用 /tmp/workspace、/tmp/reports、独立 profile；未写真实 tasks.db 或 ~/grok-bot-reports。82 项连续组合通过。
- W3：固定错误文案，不保存 RuntimeError 原文；哨兵不出现在 schedules API。改动文件、日志与浏览器 JSON 共 10 文件凭据模式扫描 0 命中（credential-check.json）；未执行受禁命令或打印 plist。

## 范围与部署

不提交、不推送；不改 server.py、H4 脚本/测试和视频实验室。开工已有的共享工作区改动保留。server.py、scripts/studio_watchdog.py、tests/test_studio_watchdog.py 与本次开工 SHA 相同（before.sha）。

主控答复先不重启：A 窗口真实雷达任务正在经 8766 调度，结束后由 A 统一重启并通知本窗口；本窗口收到通知后只读验收 PID、新后端 external 元数据、JS SHA 与 1440/390 设置总览。批准答复原文保存在 restart-approval.json。

正式只读验收通过：收到 A 通知 msg_6ef3bf4bbd49 后已确认并 ack；本窗口用 launchctl list 核对 PID 为 77037。独立 profile 仅允许 8766 同源 GET，1440/390 均显示 12 项内置与 9 项外部任务、无页面横向溢出，目视检查截图正常；零 pageerror、零写请求。重启前接口没有 external.last_error/last_error_at，重启后两字段存在且为空，health.ok=true，证明新后端已加载；HTTP settings.js SHA 与交付文件一致。证据 live-before.json、live-results.json、live.log、live-1440.png、live-390.png；可复跑脚本 live.cjs 位于同证据目录。本窗口未自行重启，未对正式服务注入故障。

## 交付 SHA256

| 文件 | SHA256 |
|---|---|
| scheduled_tasks.py | 7c18b2febf4fa2fbabfb4e35d795d541a02f0adc573c28d566ac15e46e7ae502 |
| external_tasks.py | 2d0ee0ee02f8271bba870409ef46b7171b810e78572de85a11ab1aea05c17fa7 |
| public/settings.js | c8f25ca700cf40583b99066b3aa851b51a8110939b0b400395d727c05543b612 |
| tests/test_external_refresh_isolation.py | a548d35b7fdbd4a8d20ab4ec1b0f3c52e75a58b26f276ac7855339951a514638 |
| tests/e2e/external_tasks.cjs | 1b752dafa19ded3508780baecd0a3f1a0f1a2d1d06ab3a6e678c0227541556b7 |
| tests/e2e/external_tasks_fixture.py | 6d08d6db3884b4c16e7b0866919d358b3625aba5ee6d9f03aff3b093207bb5c1 |
