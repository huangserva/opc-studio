# 中枢期 H5：素材库每日导出 library-export.json（实施合同，H1 之后、R3-0 之前）

派单：主控 term_b884acc7（run_17dc13f37a10）；实施 Codex term_02017284；复核 Codex term_57dd3162。
依据：coordination/twitter-intel-plan.md v0.7 §3.11（兴趣画像材料第 4 条）；coordination/twitter-intel-handoff-r5.md B 段。用途：Grok 读它归纳黄总兴趣画像（不存在时 Grok 会跳过），所以要尽快有第一份真实文件。

## 要做
1. 新脚本 `scripts/library_export.py`（只读 items.db，用 sqlite 只读连接 `mode=ro`）：导出素材库里 status 为 kept 或 used 的条目到 `~/grok-bot-reports/library-export.json`（目录用 grok_bot_radar.reports_dir()，测试可注入）。
2. 格式：`{"generated_at": 带时区 ISO, "count": N, "items": [...]}`，按存入时间倒序。每条**只有**：`id`、`title`、`url`、`platform`（从 id 前缀推：x: / douyin: / xhs: / wechat: / github: / linuxdo:，拿不准 web；**平台推导全仓只留一份（黄总 10-30 补充第 3 点）**：本单新建 `platform_registry.py`，内含平台登记表（键、显示名；图标/颜色字段先留空，R4 补）和唯一的 `derive_platform(item_id)`；导出脚本只调用它。R4 必须复用并扩展这同一个模块，不得另写一份）、`source`（items 现有 source / sources_json 原样整理成字符串列表）、`tags`（列表）、`saved_at`（存入时间，带时区 ISO）。**不含正文、摘要、全文、评论、笔记**；字段名白名单输出，不能整行 dump 再删字段。
3. 原子写：同目录临时文件 → fsync → os.replace；目标已存在时保留原文件权限（沿用 L1 的 fchmod 做法），新建用 0644；失败不留临时文件、不破坏旧文件。内容无变化也照常重写 generated_at（Grok 用它判断新鲜度）。
4. 挂内置调度：通过现有 `/api/schedules` 接口新建 kind=script 任务「素材库导出（给 Grok）」，每天 08:30（北京时间，早于 Grok 日报 9:00），任务正文只调用该脚本，超时 120 秒。**不改 scheduled_tasks.py**（H1 刚收口，归属其复核）。建好后「立即运行」一次，得到第一份真实文件。
5. 交付样例：报告里给真实文件的 count、按 platform/source 的分布、前 3 条样例（标题、链接可以给，素材库是黄总自己存的公开链接）。

## 允许改动
新建 scripts/library_export.py、platform_registry.py、tests/test_library_export.py、tests/test_platform_registry.py；通过 API 新建调度任务；写 ~/grok-bot-reports/library-export.json（真实写入仅此一个文件）。不改 items 存储、today_view、item-card.js、settings、scheduled_tasks.py、server.py。

## 已知弱点与必测反例（逐条自测）
- W1：tags / sources_json 是 JSON 字符串、null、空串、坏 JSON 时都能正确整理；platform 前缀大小写、缺冒号、空 id。
- W2：测试只写临时目录，不写真实 ~/grok-bot-reports 和真实 items.db；patch 有清理。
- W3：导出文件和日志里没有 Key。
- 隐私/字段：构造含正文、摘要、笔记的夹具条目，断言输出 JSON 的键集合恰好是 8 个字段（含 id）；用故意多输出一个字段的 /tmp 副本证明测试抓得到。
- 只导出 kept/used：构造 new / dismissed / 其他状态条目，断言不出现。
- 原子写：模拟 replace 失败 → 旧文件不变、无临时文件残留；0644 / 0600 原文件权限保持。
- 0 条时写 `count: 0, items: []`，不报错。
- 性能：1000 条夹具 < 1 秒。

## 通用
- 仓库 /Users/huangzongning/development/article-studio；Python ../article-importer/.venv/bin/python。本单不需要重启 8766。查 PID 只用 `launchctl list | awk '$3=="com.servasyy.article-studio"{print $1}'`。**禁止** launchctl print / ps eww / env / cat 或 plutil 打印任何 plist 内容。
- 约束：不动 Grok Bot 设置；不碰飞书；不停推特 AIsa 采集；视频实验室保护边界不动。
- 本单只需定向测试；全量 Python 如要跑先向主控登记。不提交不推送。禁止 computer use。
- 报告 coordination/impl-hub-h5-report.md，开头「决定要点」表（≤10 行：结论、H/M/L 自查、文件 SHA256、测试数字、调度任务 id、立即运行记录 id、真实导出条数），完成 worker_done。
