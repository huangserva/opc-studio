# 合同：review-today-p1（Codex 复核 · Orca）：重新设计 1 期「今日」页

## 对象
- 实施合同 `coordination/impl-today-p1-contract.md`（含第 6 节排序追加、主控批准的 today.html CSP 一行例外）。
- 实施报告 `coordination/impl-today-p1-report.md`（task_61609dae2ac7，Codex term_02017284 交付，自报 0H0M0L + 既有 1L）。SHA 以报告文末清单为准。
- 8766 当前 PID 52356（21:45:20 启动）。
- 主控抽检：`/api/today` 3 次 0.53/0.03/0.05 秒；同档位「爆款」里 eternityspring（收藏 412）排在 criscxuan（收藏 111）前；CSP 只有 /today.html 带 `https:`，twitter-radar.html 与 index.html 不变。抽检疑点：AIsa 的 dotey（每小时 9,330）排在 Grok 的 eternityspring（每小时 2,594、收藏 412）之后，需核对热度公式是否与合同第 6 节一致、是否让「AIsa 无收藏」被系统性压低。

## 实施方已知弱点（skill `worker-dispatch-profiles`，Codex 历史实施）
原文截断与贴文歧义；整条链路与并发状态遗漏；只读脚本保护与继承风险。上一单复核抓到的 M1（组件读页面全局状态）、M2（双来源详情漏 Grok 独有内容）本单必须已修。

## 复核项（逐条 通过/失败/未知 + 证据）
1. **SHA 与范围**：报告 SHA 与磁盘一致；合同 1.2 节以外 tracked 文件 0 增量（对照实施方开工基线）；`twitter-radar.js` SHA 仍为 `e769f6c7…`；server.py 本单增量只有 3 个路由装配 + 1 行 CSP 例外。
2. **CSP 例外**：只对 /today.html 生效、只加 `https:`；任取 5 个其他页面响应头与改前一致；http 图片被拦并显示占位。
3. **排序**：读 `today_view.py` 写出实际公式；用夹具验证第 6 节反例 15 与「热度相同按时间」；评估 AIsa 无收藏条目是否被不合理压低（给 2 个真实例子），若是记 M 并给修正建议（例如收藏未知时不扣分、只对已知高收藏加分）。
4. **「该做的 3 条」不凑数**：无 fit_score/doable 时为空并显示等待文案；夹具里给 4 条合格条目只出 3 条且按 fit+doable 排序。
5. **内容完整与安全**：独立构造反例 1–3（长文逐字、注入、链接白名单）在阅读器复测；不完整提示正确。
6. **M1/M2 已修**：空白隔离页只引入 `components/item-card.js` + `list-reader.js`，直接传 `/api/today` 与 `/api/today/item` 的真实双来源对象：按钮组正确、Grok 独有长文/串推/引用/媒体与 AIsa 独有内容都在；组件源码无页面级可变状态读取（列出 grep 证据）。
7. **反馈写入**：403 三种；并发；撤销；G 条目写 Bot 镜像、V 条目写视频雷达反馈与原有格式兼容（旧视频雷达页刷新后能看到同一反馈）。写入测试只在 /tmp 或随机端口实例。
8. **首屏与性能**：隔离 headless 1440×900 首屏可见条目 ≥5、首卡时间 3 次；三接口 5 次 <1 秒；窄屏 <900px 列表→全屏阅读器→返回可用。
9. **键盘与作者视图**：J/K、O、Enter；点作者名显示该作者近 30 天条目。
10. **测试未改弱**：新增断言检验真实语义；既有测试 diff 为空。
11. **既有 1L**：A2 旧页 30/34 是否确为基线既有（独立复现一次）。

## 约束
- 只读，不改仓库文件（产出只有报告与 /tmp 证据）。不调 `/api/collect/*`，不调 AIsa，不触发采集。真实 8766 只做 GET；写入类只在隔离实例。
- 只用隔离 Playwright（独立 profile、/tmp 副本或随机端口）。**禁止 computer use 操作黄总真实桌面或 Chrome。**
- Python 用 `../article-importer/.venv/bin/python`。

## 交付
报告 `coordination/review-today-p1-report.md`，开头「决定要点」表（≤10 行：结论通过/不通过、H/M/L、SHA 核对、排序结论、M1/M2 结论、首屏与性能数字）。发一条 `worker_done` 给主控 `term_b884acc7-d613-41d8-b736-0e3e84a18b37`。
