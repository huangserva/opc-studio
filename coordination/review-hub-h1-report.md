# H1 外部任务心跳与设置页总览独立复核

## 决定要点

| 项目 | 结果 |
|---|---|
| 结论 | **通过；0H / 0M / 1L 非阻断建议**，无须为本单返修或重启 |
| 范围 | task_0d9796854c6f / ctx_980c4aef23a9；只读审查 H1，正式代码零改动 |
| 文件与 SHA | 实施报告 10 份源码/测试 SHA256 全部一致，复核前后也一致；完整值 `/tmp/review-hub-h1/sha.json` |
| 定向 Python | **79/79**；另写独立反例 **7/7**，实际无权限目录检查通过 |
| 故障测试质量 | 两种 /tmp 故意改错均被原测试准确抓住，**2/2** |
| 隔离浏览器 | 新 H1 **14/14**，旧设置页 **23/23**，1440/390 逾期截图已目视检查 |
| 正式只读 | 浏览器 **14/14**；内置 11 + 外部 9；缓存接口 5 次 **22–158ms**；health.ok=true |
| 服务与保护 | PID **93453**；未停/重启 8766；保护文件与实施前备份逐字一致；未提交、未推送 |
| 证据 | `/tmp/review-hub-h1/`；本次唯一正式写入文件为本报告 |

## 验收与独立反例

| 合同项 | 结论 | 证据与实际观察 |
|---|---|---|
| 7 个外部任务登记、宽限及产出规则 | 通过 | 独立 `test_registry_exact` 精确比对 7 组时刻/宽限；源码核对 daily/alerts/interest/weekly、快报 8/12/16/20、雷达 6–22 每 2 小时、看门狗每 15 分钟。Grok 使用已有 reports_dir()；digest 保持待确认，符合本单豁免口径。 |
| 2 个微信常驻服务 | 通过 | 仅固定 `/bin/launchctl list`，不读 plist；缓存/界面只展示固定两标签 PID 与退出码。真实源服务 PID 4070、雷达服务 PID 4042，退出码均为 0。 |
| 等待→逾期→缺失→补产出恢复 | 通过 | 原测试含精确宽限边界；独立反例对快报四个时点分别走完整状态链，分别跨到下一时点（20 点场景跨日）。空 `{}` 恢复正常，原测试另覆盖 0 字节。所有时钟注入，无 sleep 推动状态。 |
| 目录不存在、无权限、文件名不符 | 通过 | 缺目录缺失且明示从未产出；原测试和独立坏文件名反例通过。除 mock PermissionError 外，额外用 /tmp 目录 chmod(0) 实测：missing +「产出不可读或扫描范围超限」，随后恢复权限；不泄漏异常原文。 |
| 北京时间跨零点与周报跨周 | 通过 | 北京时间 10/31 00:30 仍判 10/30 22:30 兴趣通道；11/2 周一 00:30 仍判 11/1 周日 20:00，下一次为 11/8 20:00。原测试另覆盖周报逾期、缺失、任意新文件恢复。 |
| 重建后的无状态一致性 | 通过 | 独立新建 ExternalTasks，比较完整快照完全一致；原测试另覆盖看门狗日志重建、空行/半行/轮换。 |
| 后台缓存及 HTTP 性能 | 通过 | loop 调用 refresh，缓存节流 300 秒；snapshot 深拷贝，独立禁止 scandir/subprocess 后读缓存仍通过；HTTP 读缓存不扫描。正式 5 次 157.97 / 28.82 / 22.24 / 27.81 / 42.07ms。 |
| 常见扫描异常不污染调度器 | 通过 | 原实现捕获 FileNotFoundError/OSError，独立将 scandir 全部注入 PermissionError 后真实调度循环仍 tick 1 次、last_error=None，9 项缓存完整。未知类型异常另见 L1。 |
| 设置页内置/外部同表、只读、旧行为 | 通过 | 新隔离浏览器 14/14：精确行数、外部零按钮、中文状态与颜色、digest、服务信息；真实点击内置恢复/暂停/立即运行合成脚本，成功记录可见；兴趣清单仍能读。旧设置页 E2E 23/23，旧 `[data-task]` 选择器保持兼容，立即运行单独使用 data-run-task。 |
| 1440 / 390 展示 | 通过 | 本次生成 `overdue-1440.png`、`overdue-390.png` 并打开目视检查，兴趣通道「逾期」清晰；窄屏纵向行、无横向溢出。 |
| 测试断言与 patch 清理 | 通过 | 对 `/tmp/impl-hub-h1/before/settings_page.cjs` 做 diff，只有 G1 一处断言变更，仍精确检查旧内置数，并增加外部精确 9 行、零按钮和合计数；无放宽。新增单测 TemporaryDirectory + addCleanup，patch 上下文清理；新 E2E 全部测试写入 /tmp，未写真实报告目录或任务库。 |
| 故意改错测试有效性 | 通过 | /tmp 副本①将 latest 改成 now，状态链测试以 ok≠waiting 失败；②将文件正则改为任意名称，日期文件名测试以 ok≠missing 失败。均为预期断言失败，非导入/环境错误；正式源未改。 |
| 正式缓存与实际文件 | 通过 | 以缓存 checked_at 的时钟重算，9 行状态、产出时间、计划时点、PID/退出码零差异；并独立读取目录文件名与 mtime 交叉验证，详见下表。正式独立 profile 浏览器 14/14，写请求尝试 0，JS 错误 0。 |

## 正式只读事实

缓存检查时间 **2026-10-30 12:10:52 北京时间**（`live.json`）。真实浏览器验收时内置 11 项、外部 9 项；实施报告原为 10 项内置，本次并行 H5 可新增调度任务，已由派单预先告知，未把它当成 H1 回归。

| 外部项 | 缓存状态 | 独立文件/运行事实 |
|---|---|---|
| 日报 | 正常 | daily/2026-10-30.json，10/30 09:10:32 |
| 快报 | 缺失 | alerts 最新 JSON 为 2026-10-29-2013.json，10/29 20:13:27；当天 08:00、12:00 两周期都无产出，故已从实施验收时的逾期自然进入缺失 |
| 兴趣通道 | 正常 | interest/2026-10-30-1038.json，10/30 10:41:10 |
| 周报 | 缺失 | weekly 目录存在且为空，last_output_at=null |
| 收藏 digest | 待确认 | 本单明文允许不猜路径；handoff-r5 后已明确的新 digest 路径可后续更新，不列 H1 缺陷 |
| 推特雷达 | 正常 | snapshots/2026-10-30/20261030-040007-9c5bb9a5.json，10/30 12:00:40 |
| 看门狗 | 等待中 | 缓存时最后完整日志为 11:57:11，当前周期 12:00；其后另一次只读 stat 看到日志推进至 12:12:11，属于外部任务继续运行 |
| 微信两个服务 | 运行中 | com.servasyy.wechat-source：4070 / 0；com.servasyy.wechat-radar：4042 / 0 |

这只证明文件产出与服务 PID 心跳，不证明报告内容质量或采集业务成功。

## 非阻断建议 L1：未知内部异常与 H4 健康状态共用错误字段

**观察：** `scheduled_tasks.py:463–466` 将 tick 和 external_tasks.refresh 放在同一 try 内。/tmp 合成故障注入令 refresh 持续抛 RuntimeError 时，两轮 tick 均执行（没有阻止其他任务），但两轮 last_error 均被写成「调度检查失败」。由 health() 的 `not error` 条件及 H4 对 health.ok=false 的处理，可推得此类未知异常会被 H4 视作调度器异常。

**等级 L，非阻断：** 正常文件系统错误 FileNotFoundError/OSError（包括真实无权限）已被扫描器捕获，不会走这条路径；正式服务 health.ok=true。本次没有发现真实目录触发 RuntimeError，仅通过显式故障注入证明通用异常边界，因此不把它当成本单实际失效或新增返修门槛。可在后续健壮性工作中单独隔离外部缓存异常，保留原快照与单独错误标识。证据 `exception-isolation.json`。

## 验证范围、完整性与证据

本次没有复跑全量。79 项定向命令为：

```sh
../article-importer/.venv/bin/python -m unittest tests.test_external_tasks tests.test_scheduled_tasks tests.test_schedules_http tests.test_schedules_management tests.test_settings_view tests.test_video_radar_schedule_h2
```

实施全量 3665 项 0F/1E/7S、UI 719P/3F/1S 仅复用其证据，不冒充本次重跑结果。`test_radar_links_e2e.cjs` 收尾挂起在合同与实施报告已有两次复现记录；本单未复现、未修复、未处理其进程，不纳入 H1 缺陷。

证据目录 `/tmp/review-hub-h1/`：

- `sha.json`、`final-integrity.json`：10 份交付文件 SHA256 与复核前后完整性；server.py、interest_channel.py、settings_view.py、看门狗脚本/测试与实施前备份完全相同。
- `directed.log`：79/79；`independent.py`、`independent.log`：7/7；`exception-isolation.json`：真实无权限和合成未知异常两类边界。
- `mutation-results.json`、`mutation-false-normal.log`、`mutation-loose-name.log`：两份故意改错副本与预期断言失败。
- `browser.log`、`browser-results.json`、`overdue-1440.png`、`overdue-390.png`：14 项隔离 H1 E2E 与截图；加载阶段零写请求，随后仅向隔离端口执行合成内置任务操作。
- `settings-existing.log`：旧设置页 23 项；原 E2E 完整临时证据 `/tmp/settings-p4-e2e-iBubez/`。
- `live.json`、`file-metadata.json`、`runtime-metadata.json`：正式只读 API、文件名/mtime、固定服务 PID/退出码。
- `live-browser.cjs`、`live-browser.log`、`live-browser.json`、`live-1440.png`、`live-390.png`：正式独立 profile 只读浏览器，拦截任何非 GET/HEAD 请求，14 项通过、零写尝试、零页面异常。

未使用 computer use、未操作真实桌面或用户 Chrome profile；未改正式代码、未重启 8766、未提交、未推送。审查结束交付文件 SHA 无漂移。本报告为约定的唯一正式写入文件。
