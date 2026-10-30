# H4 返修 M1：看门狗健康响应字段校验（实施合同）

派单：主控 term_b884acc7（run_17dc13f37a10）；实施 Codex term_02017284（原 H4 实施窗口）；复验 Codex term_57dd3162。
依据：coordination/review-hub-h4-report.md「阻断项 M1」；原合同 coordination/impl-hub-h4-contract.md（通用段全部沿用）。

## 问题
`scripts/studio_watchdog.py` 约 105–124 行只校验 ok、tick_age_seconds、last_error；last_tick、now、running_count 缺失或类型错时仍判 ok（复核 9 个反例复现）。

## 要做
- 六个字段全部校验：ok 为 bool；last_tick 为可解析的带时区 ISO 字符串（null 视为异常，调度器从未跑过也该报）；now 为可解析的带时区 ISO 字符串；tick_age_seconds 为有限非负数（排除 bool、NaN、inf、负数）；last_error 为 null 或字符串；running_count 为非负整数（排除 bool）。任一不符 → errors 含 invalid_health，结论异常，不崩。
- 健康响应顶层不是 dict（数组、null、数字）→ invalid_health，不崩。
- 只改 scripts/studio_watchdog.py 与 tests/test_studio_watchdog.py；不改接口、server.py、plist、launchd 任务；不重启 8766（本单不需要）。

## 必测反例（逐条自测，报告给结果）
复核报告里的 9 个失败反例全部转成测试并通过；另加：tick_age 为 true / NaN / -1；running_count 为 1.5 / "0" / true；last_tick 为无时区字符串；顶层为 []。正常完整响应仍判 ok。用故意去掉某一字段校验的 /tmp 副本证明新测试测得出（W2 同时满足：patch 有清理、不写真实目录）。

## 已知弱点（逐条自测）
W1 只修一处漏另一处（逐字段列表核对）；W2 mock 泄漏；W3 凭证外露（日志/报告 grep 自证）。

## 交付
定向测试数字；Python 全量跑前向主控登记（基线 3633 项 0F/1E/7S，H2 修正后）。报告 coordination/impl-hub-h4-m1-report.md，开头决定要点表，完成 worker_done。不提交不推送。
