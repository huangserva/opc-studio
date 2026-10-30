# H4 M1 返修实施报告

| 决定要点 | 结果 |
|---|---|
| 结论 | 实施自检通过，M1 已修复；待原复核窗口独立复验 |
| H / M / L 自查 | 0 / 0 / 0；不代替独立复核结论 |
| 生产代码与 SHA256 | `scripts/studio_watchdog.py`：`098f0322cec6c25a29cdf447e6eddc2ff75dc96010f4869d2e6c5ae36f8c1f7c` |
| 测试与 SHA256 | `tests/test_studio_watchdog.py`：`9ea9871c1bfef5efaf33d70eecba3919dd8f570e3b49c080dfd8856023268e65` |
| 定向测试 | 26/26 通过，0F/0E/0S，7.679 秒；原 20 项 + 新增 6 项 |
| 检错能力 | 新测试在修复前副本检出 9/9 漏报；删除计数校验的副本检出预期 3F/0E |
| 浏览器路径 | 独立 profile、随机端口、headless Chrome + 真实看门狗 HTTP 探测，5/5 通过 |
| 全量与部署 | 本轮未跑全量，避开 H2 在途全量；未重启 8766，新 PID 不适用；不提交不推送 |
| 证据 | `/tmp/article-studio-h4-m1-evidence/`；除上述两文件，仅新增本报告 |

## 修正内容与六字段核对（W1）

`_probe()` 在 JSON 解码后统一校验六个根字段。任一字段缺失或结构无效，都加入固定错误码 `invalid_health`，`check_once()` 记录“异常”；不输出错误响应原文。顶层不是对象时同样按 `invalid_health` 返回。

| 字段 | 接受条件 | 自测结果 |
|---|---|---|
| `ok` | 严格 bool；false 继续按原行为报告 `scheduler_unhealthy` | 缺失、null、数字、字符串、数组均拒绝；通过 |
| `last_tick` | 可解析且带时区的 ISO 字符串 | 缺失、数组、false、null、空串、无时区、仅日期、无效日期均拒绝；通过 |
| `now` | 可解析且带时区的 ISO 字符串 | 同上；通过 |
| `tick_age_seconds` | 有限非负 int/float，排除 bool | null、字符串、true、NaN、inf、-1 拒绝；0、0.5、300 接受；通过 |
| `last_error` | null 或字符串；非空字符串继续报告 `scheduler_error` | 缺失、bool、数字、数组、对象拒绝；null/空串正常，非空串异常；通过 |
| `running_count` | 严格非负 int，排除 bool | 缺失、数组、false、null、-1、1.5、`"0"`、true 拒绝；0/1/2 接受；通过 |

数值判断将 int 与 float 分开，避免大整数进入 `math.isfinite()` 时转换溢出；既有 300 秒阈值、错误去重、恢复通知、日志轮转均保持原行为。

## 必测反例与命令

定向命令：`../article-importer/.venv/bin/python -m unittest discover -s tests -p test_studio_watchdog.py -v`。完整输出 `targeted.log`，26 项全部通过。

- 复核 9 个反例逐条通过：`last_tick` / `now` / `running_count` 分别缺失、为 `[]`、为 false。对应测试 `test_review_m1_nine_missing_or_mistyped_field_counterexamples`。
- 六字段逐一删除全部拒绝；时间字段 null、无时区、无效日期均拒绝；tick_age 为 true / NaN / -1 与计数为 1.5 / `"0"` / true 均覆盖通过。
- 顶层 `[]` / null / 数字 / 字符串均产生 `invalid_health`，日志正常落盘，没有崩溃。
- 完整健康响应，以及 `+08:00` / `Z` 时区、空错误、计数和心跳边界仍正常。

检错实验只在 `/tmp` 副本运行同一新增测试：`pre_fix.log` 为修复前代码，预期 9 个子测试失败全部出现；`mutation.log` 为修复后副本故意删去 `or not valid_count`，预期 3 个计数字段子测试失败全部出现。两次均退出码 1、0E，摘要见 `mutation-results.json`。正式文件没有为实验回退或覆盖，最后 SHA 与上表一致。

## 浏览器与隔离（W2）

`browser.cjs` 用 Playwright 启动独立 profile 的 headless Chrome，访问随机端口的 `/api/schedules/health`，确认 HTTP 200 及 JSON 内容；每轮再以独立 Python 进程执行真实 `check_once()`，经真实 TCP/HTTP 请求完成判断、状态和日志写入。通知器替换为无副作用回调，不发系统通知。最终 5 个场景：正常 → 缺 `last_tick` → bool 计数 → 顶层 null → 恢复；均通过，首次正常不通知、异常去重、恢复通知一次。证据 `browser-results.json`、`browser.log` 及结果中记录的独立 `browser-run-*` 日志目录。

临时浏览器夹具最初未处理自动 favicon 请求，修正为 404；最终成功运行使用全新日志目录，首轮断言 `notification=none`，避免先前夹具状态影响结论。浏览器与服务器均有 finally 关闭；没有 computer use，没有操作真实桌面或用户 Chrome，没有访问/重启真实 8766。

新增单元测试不新增 patch，沿用 `fake_service()` 的 finally 清理 HTTP 服务器与线程、`TemporaryDirectory` 的 addCleanup，所有看门狗日志/状态/锁仅写临时目录。原有 patch 使用上下文管理器；本轮没有修改真实 tasks.db、LaunchAgents、Grok 目录或共享配置。全量资源状态已通过 Orca status 告知主控，本轮不申请或运行全量，不声称共享工作区全量回归已通过。

## 凭据检查（W3）与交付边界

对本报告、两份代码及本轮日志/状态/证据执行凭据特征扫描，仅记录命中文件名与数量，不输出匹配内容；结果零命中，见 `credential-scan.json`。无 API Key 读取、打印或写入。时间字段中的私有哨兵通过单测确认未进入日志、状态或通知。

改动范围为合同允许的两份代码及指定报告；未改接口、server.py、plist、launchd 任务或其他在途文件。差异备份 `studio_watchdog.diff` / `test_studio_watchdog.diff`、修复前副本及 `sha256.json` 均在证据目录。后续由原复核窗口按合同独立复验；本单不承担重启、部署、全量/UI 全量或提交推送。
