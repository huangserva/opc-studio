# 中枢期 H1 外部任务心跳 + 设置页总览 复核合同

派单：主控 term_b884acc7（run_17dc13f37a10）；复核 Codex term_57dd3162（实施 term_02017284，task_c94d499ce5a7）。
仓库 /Users/huangzongning/development/article-studio；Python ../article-importer/.venv/bin/python。查 PID 只用 `launchctl list | awk '$3=="com.servasyy.article-studio"{print $1}'`（现应 93453）。**禁止** launchctl print / ps eww / env / cat 或 plutil 打印任何 plist 内容。

## 要读
实施合同 coordination/impl-hub-h1-contract.md + 派单补充（task spec：不碰看门狗与 interest_channel；server.py 未改）；主控答复的口径：状态完全无状态推导，目录不存在/从未产出 = 「缺失」且说明写「从未产出」；设置 E2E 只改数量断言为精确的 内置数 + 9。实施报告 coordination/impl-hub-h1-report.md（先核 SHA）。

## 复核重点（逐条 通过/失败/未知 + 证据；独立反例）
1. 登记表：7 个外部任务的计划、产出路径规则、宽限与合同表一致；Grok 收藏 digest 显示「待确认」（注意：handoff-r5 已让 Grok 固定写 ~/grok-bot-reports/digest/YYYY-MM-DD.{md,json}，记录为后续可更新项，不算本单缺陷）；两个微信常驻服务只读 PID/退出码。
2. 状态判定（注入时钟，不 sleep）：等待中 → 逾期 → 缺失 → 补文件后正常；目录不存在、无权限、文件名不符、北京时间 00:30 判前一天 22:30 兴趣通道、周报跨周、快报 4 个时点各自判定、空 json（本次无）算正常、重启后结果一致。
3. 缓存与性能：调度器定时计算、接口读缓存 < 1 秒；扫描异常不影响调度器其他任务（last_error 不被污染？如会，说明影响 H4 看门狗）。
4. 设置页：内置与外部同表，外部标「外部 · 只读」；内置开关/立即运行/运行记录行为不回退（旧选择器兼容）；1440/390 截图；隔离副本演示一个逾期。
5. 测试质量（W2/W4）：settings_page.cjs 断言改动只此一处且未放宽（看 diff）；新测试 patch 有清理、不写真实目录；故意改错副本证明测得出错。
6. 真实只读：对真实 8766 读总览，核对当前各外部任务状态与文件系统实际一致（例如快报：alerts/ 最新文件时间）。
7. 全量不必重跑（实施已跑 3665 项 0F/1E/7S、UI 719P/3F/1S）；如需核对，只定向跑本单测试。UI 全量里 tests/test_radar_links_e2e.cjs 清理挂起已两次复现，单列观察（不是本单范围，记录即可）。

## 规则
只读复核，不改正式代码；验证改动在 /tmp 副本做。不停/不重启真实 8766。禁止 computer use。不提交不推送。

## 报告
coordination/review-hub-h1-report.md，开头「决定要点」表（≤10 行）。完成 worker_done。
